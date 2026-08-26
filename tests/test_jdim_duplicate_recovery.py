from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.duplicate_recovery import (  # noqa: E402
    BLOCKED_FRAME_SELECTION_REPRODUCTION,
    BLOCKED_PREPROCESSING_PROVENANCE,
    BLOCKED_SOURCE_LINKAGE,
    DUPLICATE_SEMANTICS_RESOLVED,
    MODE_DICOM_REHYDRATION,
    MODE_HISTORICAL_NPZ,
    RECONSTRUCTED_VECTOR_MISMATCH,
    RecoveryPrerequisites,
    WindowSpec,
    compare_reconstructed_vector,
    prerequisite_status,
    prepare_encoder_input,
    select_processed_array,
    verify_processed_input,
    write_recovery_outputs,
)
from jdim_tier1.safety import Tier1BlockedError  # noqa: E402


class DuplicateRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.frames = np.zeros((32, 224, 224, 3), dtype=np.uint8)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _encoder(value: np.ndarray) -> np.ndarray:
        return np.asarray(
            [value.mean(), value.std(), value.min(), value.max()], dtype=np.float32
        )

    def _vector(self, frames: np.ndarray) -> np.ndarray:
        return self._encoder(prepare_encoder_input(frames))

    def test_one_npz_semantic_array_produces_one_embedding(self) -> None:
        vector = self._vector(self.frames)
        evidence = verify_processed_input(
            {"frames": self.frames}, [vector], self._encoder
        )
        self.assertEqual(evidence.status, DUPLICATE_SEMANTICS_RESOLVED)
        self.assertEqual(evidence.historical_vector_count, 1)
        self.assertEqual(evidence.exact_historical_vector_matches, 1)
        self.assertEqual(evidence.processed_array_selector, "frames")

    def test_one_npz_can_represent_multiple_explicit_windows(self) -> None:
        windows = np.stack(
            [self.frames, np.full_like(self.frames, 7)],
            axis=0,
        )
        first, first_selector = select_processed_array(
            {"frames": windows}, WindowSpec(window_index=0)
        )
        second, second_selector = select_processed_array(
            {"frames": windows}, WindowSpec(window_index=1)
        )
        self.assertFalse(np.array_equal(first, second))
        self.assertEqual(first_selector, "frames[0]")
        self.assertEqual(second_selector, "frames[1]")

    def test_multiwindow_npz_without_window_provenance_fails_closed(self) -> None:
        windows = np.stack([self.frames, self.frames], axis=0)
        with self.assertRaises(Tier1BlockedError) as context:
            select_processed_array({"frames": windows})
        self.assertEqual(context.exception.status, BLOCKED_FRAME_SELECTION_REPRODUCTION)

    def test_same_semantic_clip_duplicated_at_merge_matches_both_vectors(self) -> None:
        vector = self._vector(self.frames)
        evidence = verify_processed_input(
            {"frames": self.frames}, [vector.copy(), vector.copy()], self._encoder
        )
        self.assertEqual(evidence.status, DUPLICATE_SEMANTICS_RESOLVED)
        self.assertEqual(evidence.exact_historical_vector_matches, 2)

    def test_same_dicom_distinct_temporal_windows_are_distinguishable(self) -> None:
        frames = np.concatenate(
            [np.zeros((16, 224, 224, 3), dtype=np.uint8), np.ones((16, 224, 224, 3), dtype=np.uint8)],
            axis=0,
        )
        first, _ = select_processed_array(
            {"frames": frames}, WindowSpec(frame_start=0, frame_end=16)
        )
        second, _ = select_processed_array(
            {"frames": frames}, WindowSpec(frame_start=16, frame_end=32)
        )
        self.assertFalse(np.array_equal(first, second))

    def test_missing_npz_with_unique_source_linkage_is_ready(self) -> None:
        source = self.root / "source.dcm"
        source.write_bytes(b"synthetic")
        status, _ = prerequisite_status(
            RecoveryPrerequisites(
                mode=MODE_DICOM_REHYDRATION,
                source_candidates=(source,),
                preprocessing_expected_sha256="a",
                preprocessing_observed_sha256="a",
                frame_selection_pinned=True,
                checkpoint_expected_sha256="b",
                checkpoint_observed_sha256="b",
            )
        )
        self.assertEqual(status, "READY")

    def test_ambiguous_source_linkage_fails_closed(self) -> None:
        first = self.root / "first.dcm"
        second = self.root / "second.dcm"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        status, _ = prerequisite_status(
            RecoveryPrerequisites(
                mode=MODE_DICOM_REHYDRATION,
                source_candidates=(first, second),
                preprocessing_expected_sha256="a",
                preprocessing_observed_sha256="a",
                frame_selection_pinned=True,
                checkpoint_expected_sha256="b",
                checkpoint_observed_sha256="b",
            )
        )
        self.assertEqual(status, BLOCKED_SOURCE_LINKAGE)

    def test_preprocessing_version_mismatch_fails_closed(self) -> None:
        npz = self.root / "recovered.npz"
        np.savez_compressed(npz, frames=self.frames)
        status, _ = prerequisite_status(
            RecoveryPrerequisites(
                mode=MODE_HISTORICAL_NPZ,
                recovered_npz=npz,
                preprocessing_expected_sha256="expected",
                preprocessing_observed_sha256="observed",
                frame_selection_pinned=True,
                checkpoint_expected_sha256="b",
                checkpoint_observed_sha256="b",
            )
        )
        self.assertEqual(status, BLOCKED_PREPROCESSING_PROVENANCE)

    def test_mismatch_against_one_or_both_historical_vectors(self) -> None:
        reconstructed = np.asarray([1.0, 2.0], dtype=np.float32)
        status_one, matches_one, _ = compare_reconstructed_vector(
            reconstructed,
            [reconstructed.copy(), np.asarray([1.0, 3.0], dtype=np.float32)],
        )
        status_both, matches_both, _ = compare_reconstructed_vector(
            reconstructed,
            [np.asarray([0.0, 2.0], dtype=np.float32), np.asarray([1.0, 3.0], dtype=np.float32)],
        )
        self.assertEqual(status_one, RECONSTRUCTED_VECTOR_MISMATCH)
        self.assertEqual(matches_one, 1)
        self.assertEqual(status_both, RECONSTRUCTED_VECTOR_MISMATCH)
        self.assertEqual(matches_both, 0)

    def test_output_is_nonoverwriting_and_safe_summary_is_row_free(self) -> None:
        output = self.root / "outputs"
        rows = pd.DataFrame(
            [
                {
                    "group_token": "secret-group-token",
                    "study_id": 12345,
                    "source_dicom_path": "/restricted/secret/source.dcm",
                    "status": DUPLICATE_SEMANTICS_RESOLVED,
                }
            ]
        )
        write_recovery_outputs(rows, output, worktree=ROOT)
        safe = json.loads(
            (output / "aggregate_safe" / "duplicate_recovery_summary.json").read_text()
        )
        serialized = json.dumps(safe)
        self.assertNotIn("12345", serialized)
        self.assertNotIn("secret-group-token", serialized)
        self.assertNotIn("/restricted/secret", serialized)
        with self.assertRaises(FileExistsError):
            write_recovery_outputs(rows, output, worktree=ROOT)


if __name__ == "__main__":
    unittest.main()

