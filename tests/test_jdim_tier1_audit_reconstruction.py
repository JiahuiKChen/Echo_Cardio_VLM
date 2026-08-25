from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import pydicom
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    from extract_mimic_echo_cines import (  # noqa: E402
        crop_and_scale,
        mask_outside_ultrasound,
        temporal_sample,
    )
    from jdim_tier1.audit import canonical_clip_source_row_sha256  # noqa: E402
    from jdim_tier1.audit_reconstruction import (  # noqa: E402
        BLOCKED_AUDIT_RECONSTRUCTION,
        build_contact_sheet,
        build_reconstruction_pilot,
        model_seen_frames,
    )
    from jdim_tier1.safety import Tier1BlockedError  # noqa: E402
    DEPENDENCY_ERROR = ""
except ModuleNotFoundError as exc:  # pragma: no cover - depends on local test environment
    DEPENDENCY_ERROR = str(exc)


@unittest.skipIf(bool(DEPENDENCY_ERROR), f"optional reconstruction dependencies unavailable: {DEPENDENCY_ERROR}")
class AuditReconstructionTests(unittest.TestCase):
    @staticmethod
    def _write_multiframe_rgb(path: Path, frames: np.ndarray) -> None:
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = generate_uid()
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        dataset = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
        dataset.SOPClassUID = meta.MediaStorageSOPClassUID
        dataset.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        dataset.Rows = int(frames.shape[1])
        dataset.Columns = int(frames.shape[2])
        dataset.NumberOfFrames = str(frames.shape[0])
        dataset.SamplesPerPixel = 3
        dataset.PhotometricInterpretation = "RGB"
        dataset.PlanarConfiguration = 0
        dataset.BitsAllocated = 8
        dataset.BitsStored = 8
        dataset.HighBit = 7
        dataset.PixelRepresentation = 0
        dataset.PixelData = frames.tobytes()
        dataset.save_as(path, enforce_file_format=True)

    @staticmethod
    def _write_roster(root: Path, manifest_path: Path) -> Path:
        manifest = pd.read_csv(manifest_path)
        if "write_ok" in manifest.columns:
            manifest = manifest[
                manifest["write_ok"].astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
            ].copy()
        manifest = manifest.reset_index(drop=True)
        manifest["study_id"] = manifest["study_id"].astype(str)
        manifest["subject_id"] = manifest["subject_id"].astype(str)
        manifest["source_manifest_row"] = np.arange(len(manifest), dtype=int)
        roster = root / "canonical_clip_roster_restricted.csv"
        pd.DataFrame(
            [
                {
                    "audit_id": "audit-opaque",
                    "clip_audit_id": "clip-opaque",
                    "study_id": 100,
                    "subject_id": 10,
                    "source_manifest_row": 0,
                    "source_manifest_row_sha256": canonical_clip_source_row_sha256(manifest.iloc[0]),
                }
            ]
        ).to_csv(roster, index=False)
        return roster

    def test_model_seen_frames_uses_exact_stride(self) -> None:
        frames = np.arange(40 * 2 * 2 * 3, dtype=np.uint16).reshape(40, 2, 2, 3)
        selected = model_seen_frames(frames)
        np.testing.assert_array_equal(selected, frames[:32:2])
        sheet = build_contact_sheet(selected.astype(np.uint8), selected.astype(np.uint8))
        self.assertEqual(sheet.shape, (1016, 1792, 3))

    def test_reconstruction_pilot_replays_source_and_writes_only_safe_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data_root = root / "dicoms"
            data_root.mkdir()
            raw = np.zeros((40, 32, 32, 3), dtype=np.uint8)
            for index in range(len(raw)):
                raw[index, 5:27, 5:27, :] = index + 1
            dicom = data_root / "sample.dcm"
            self._write_multiframe_rgb(dicom, raw)

            masked = mask_outside_ultrasound(raw)
            resized = np.stack([crop_and_scale(frame, 224) for frame in masked], axis=0).astype(np.uint8)
            processed, sampled = temporal_sample(resized, 32)
            npz = root / "processed.npz"
            np.savez_compressed(
                npz,
                frames=processed,
                sampled_indices=sampled,
                source_num_frames=np.array([len(raw)], dtype=np.int32),
            )

            linkage = root / "audit_linkage.csv"
            pd.DataFrame(
                [{"audit_id": "audit-opaque", "study_id": 100, "subject_id": 10, "review_order": 1}]
            ).to_csv(linkage, index=False)
            manifest = root / "canonical_clips.csv"
            pd.DataFrame(
                [
                    {
                        "study_id": 100,
                        "subject_id": 10,
                        "embedding_idx": 0,
                        "canonical_clip_id": "a" * 64,
                        "dicom_filepath": "sample.dcm",
                        "output_path": str(npz),
                        "write_ok": True,
                    }
                ]
            ).to_csv(manifest, index=False)
            roster = self._write_roster(root, manifest)

            output = root / "restricted_pilot"
            safe = root / "aggregate_safe"
            result = build_reconstruction_pilot(
                linkage,
                roster,
                manifest,
                data_root,
                output,
                safe,
                max_studies=1,
                max_clips_per_study=1,
            )
            self.assertEqual(result.safe_summary["status"], "ok")
            self.assertEqual(result.safe_summary["clips_with_exact_source_replay"], 1)
            self.assertTrue((output / "index.html").exists())
            self.assertEqual(len(list(output.glob("*/*.png"))), 1)
            restricted_rows = pd.read_csv(output / "reconstruction_pilot_rows.csv")
            for column in (
                "source_dicom_sha256",
                "processed_npz_sha256",
                "sampled_indices_sha256",
                "model_seen_source_frames_sha256",
                "model_seen_processed_frames_sha256",
            ):
                self.assertRegex(str(restricted_rows.iloc[0][column]), r"^[0-9a-f]{64}$")
            safe_frame = pd.read_csv(safe / "reconstruction_pilot_summary.csv")
            self.assertNotIn("study_id", safe_frame.columns)
            self.assertNotIn("subject_id", safe_frame.columns)
            self.assertNotIn("path", " ".join(safe_frame.columns).lower())

    def test_reconstruction_rejects_changed_manifest_row_at_locked_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data_root = root / "dicoms"
            data_root.mkdir()
            linkage = root / "audit_linkage.csv"
            pd.DataFrame(
                [{"audit_id": "audit-opaque", "study_id": 100, "subject_id": 10, "review_order": 1}]
            ).to_csv(linkage, index=False)
            manifest = root / "canonical_clips.csv"
            frame = pd.DataFrame(
                [
                    {
                        "study_id": 100,
                        "subject_id": 10,
                        "embedding_idx": 0,
                        "dicom_filepath": "original.dcm",
                        "output_path": "original.npz",
                    }
                ]
            )
            frame.to_csv(manifest, index=False)
            roster = self._write_roster(root, manifest)
            frame.loc[0, "output_path"] = "changed-at-same-ordinal.npz"
            frame.to_csv(manifest, index=False)
            with self.assertRaisesRegex(Tier1BlockedError, "source-row fingerprint"):
                build_reconstruction_pilot(
                    linkage,
                    roster,
                    manifest,
                    data_root,
                    root / "restricted_pilot",
                    root / "aggregate_safe",
                    max_studies=1,
                    max_clips_per_study=1,
                )

    def test_reconstruction_mismatch_stops_pilot(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data_root = root / "dicoms"
            data_root.mkdir()
            raw = np.full((32, 32, 32, 3), 12, dtype=np.uint8)
            self._write_multiframe_rgb(data_root / "sample.dcm", raw)
            npz = root / "wrong.npz"
            np.savez_compressed(
                npz,
                frames=np.zeros((32, 224, 224, 3), dtype=np.uint8),
                sampled_indices=np.arange(32, dtype=np.int32),
                source_num_frames=np.array([len(raw)], dtype=np.int32),
            )
            linkage = root / "audit_linkage.csv"
            pd.DataFrame(
                [{"audit_id": "audit-opaque", "study_id": 100, "subject_id": 10, "review_order": 1}]
            ).to_csv(linkage, index=False)
            manifest = root / "canonical_clips.csv"
            pd.DataFrame(
                [
                    {
                        "study_id": 100,
                        "subject_id": 10,
                        "embedding_idx": 0,
                        "dicom_filepath": "sample.dcm",
                        "output_path": str(npz),
                    }
                ]
            ).to_csv(manifest, index=False)
            roster = self._write_roster(root, manifest)
            result = build_reconstruction_pilot(
                linkage,
                roster,
                manifest,
                data_root,
                root / "restricted_pilot",
                root / "aggregate_safe",
                max_studies=1,
                max_clips_per_study=1,
            )
            self.assertEqual(result.safe_summary["status"], BLOCKED_AUDIT_RECONSTRUCTION)
            self.assertEqual(result.safe_summary["reconstruction_failure_rate"], 1.0)

    def test_reconstruction_requires_exact_stored_frame_order(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data_root = root / "dicoms"
            data_root.mkdir()
            raw = np.zeros((40, 32, 32, 3), dtype=np.uint8)
            for index in range(len(raw)):
                raw[index, 5:27, 5:27, :] = index + 1
            self._write_multiframe_rgb(data_root / "sample.dcm", raw)

            masked = mask_outside_ultrasound(raw)
            resized = np.stack([crop_and_scale(frame, 224) for frame in masked], axis=0).astype(np.uint8)
            processed, sampled = temporal_sample(resized, 32)
            npz = root / "processed.npz"
            np.savez_compressed(
                npz,
                frames=processed,
                sampled_indices=sampled[::-1],
                source_num_frames=np.array([len(raw)], dtype=np.int32),
            )
            linkage = root / "audit_linkage.csv"
            pd.DataFrame(
                [{"audit_id": "audit-opaque", "study_id": 100, "subject_id": 10, "review_order": 1}]
            ).to_csv(linkage, index=False)
            manifest = root / "canonical_clips.csv"
            pd.DataFrame(
                [
                    {
                        "study_id": 100,
                        "subject_id": 10,
                        "embedding_idx": 0,
                        "dicom_filepath": "sample.dcm",
                        "output_path": str(npz),
                    }
                ]
            ).to_csv(manifest, index=False)
            roster = self._write_roster(root, manifest)
            result = build_reconstruction_pilot(
                linkage,
                roster,
                manifest,
                data_root,
                root / "restricted_pilot",
                root / "aggregate_safe",
                max_studies=1,
                max_clips_per_study=1,
            )
            self.assertEqual(result.safe_summary["status"], BLOCKED_AUDIT_RECONSTRUCTION)
            self.assertEqual(result.safe_summary["clips_with_exact_source_replay"], 1)
            self.assertEqual(result.safe_summary["clips_with_verified_frame_order"], 0)


if __name__ == "__main__":
    unittest.main()
