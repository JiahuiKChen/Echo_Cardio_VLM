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

from jdim_tier1.audit_interface import (  # noqa: E402
    CheckpointStore,
    PROHIBITED_READER_FIELDS,
    build_blinded_interface_package,
    reader_visible_record,
)
from jdim_tier1.phase2i import (  # noqa: E402
    BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION,
    BLOCKED_REPLAY_CONTENT_DIFFERENCE,
    BLOCKED_REPLAY_PROVENANCE,
    EXACT_MODEL_INPUT,
    NOT_ASSESSABLE,
    REPLAY_PATH_VALIDATED,
    REPLAY_USABLE_WITH_TIERED_REPORTING,
    SOURCE_ACQUISITION_ONLY,
    VERIFIED_EQUIVALENT_REPLAY,
    _difference_metrics,
    _validate_relative_dicom_path,
    classify_evidence_tier,
    replay_readiness_status,
    restore_locked_sources,
)


def _replay_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_identity_verified": True,
        "frame_count_verified": True,
        "recorded_indices_valid": True,
        "frame_indices_match": True,
        "frame_order_verified": True,
        "geometry_verified": True,
        "content_difference_status": "none",
        "retained_historical_processed": True,
        "exact_replay": True,
    }
    row.update(overrides)
    return row


class Phase2ITierTests(unittest.TestCase):
    def test_job_a_retry_command_uses_real_lines_and_minimal_qsub_environment(self) -> None:
        wrapper = (ROOT / "scripts" / "scc_run_jdim_phase2i_job_a.sh").read_text()
        self.assertIn("printf '%s\\n'", wrapper)
        self.assertNotIn("qsub -cwd -V", wrapper)

    def test_retained_or_bitwise_replay_is_tier_a(self) -> None:
        common = {
            "equivalent_replay_verified": False,
            "unique_source_linkage": True,
            "source_viewable": True,
            "provenance_pinned": True,
            "frame_selection_established": True,
            "content_affecting_difference": False,
        }
        self.assertEqual(
            classify_evidence_tier(
                retained_historical_processed=True,
                exact_replay=False,
                **common,
            ),
            EXACT_MODEL_INPUT,
        )
        self.assertEqual(
            classify_evidence_tier(
                retained_historical_processed=False,
                exact_replay=True,
                **common,
            ),
            EXACT_MODEL_INPUT,
        )

    def test_verified_equivalent_replay_requires_every_guard(self) -> None:
        values = {
            "retained_historical_processed": False,
            "exact_replay": False,
            "equivalent_replay_verified": True,
            "unique_source_linkage": True,
            "source_viewable": True,
            "provenance_pinned": True,
            "frame_selection_established": True,
            "content_affecting_difference": False,
        }
        self.assertEqual(classify_evidence_tier(**values), VERIFIED_EQUIVALENT_REPLAY)
        values["content_affecting_difference"] = True
        self.assertEqual(classify_evidence_tier(**values), SOURCE_ACQUISITION_ONLY)

    def test_source_only_and_missing_source_tiers(self) -> None:
        values = {
            "retained_historical_processed": False,
            "exact_replay": False,
            "equivalent_replay_verified": False,
            "unique_source_linkage": True,
            "source_viewable": True,
            "provenance_pinned": True,
            "frame_selection_established": False,
            "content_affecting_difference": False,
        }
        self.assertEqual(classify_evidence_tier(**values), SOURCE_ACQUISITION_ONLY)
        values["source_viewable"] = False
        self.assertEqual(classify_evidence_tier(**values), NOT_ASSESSABLE)

    def test_replay_gate_accepts_exact_and_retained_noncontent_difference(self) -> None:
        self.assertEqual(replay_readiness_status(pd.DataFrame([_replay_row()])), REPLAY_PATH_VALIDATED)
        rows = pd.DataFrame(
            [
                _replay_row(
                    exact_replay=False,
                    content_difference_status="non_content_affecting",
                )
            ]
        )
        self.assertEqual(replay_readiness_status(rows), REPLAY_USABLE_WITH_TIERED_REPORTING)

    def test_replay_gate_blocks_content_and_frame_order_failures(self) -> None:
        content = pd.DataFrame(
            [_replay_row(exact_replay=False, content_difference_status="unresolved")]
        )
        self.assertEqual(replay_readiness_status(content), BLOCKED_REPLAY_CONTENT_DIFFERENCE)
        order = pd.DataFrame([_replay_row(frame_order_verified=False)])
        self.assertEqual(replay_readiness_status(order), BLOCKED_REPLAY_PROVENANCE)

    def test_pixel_difference_policy_is_bounded(self) -> None:
        base = np.zeros((16, 2, 2, 3), dtype=np.uint8)
        self.assertEqual(_difference_metrics(base, base)["content_difference_status"], "none")
        one_level = base.copy()
        one_level[0, 0, 0, 0] = 1
        self.assertEqual(
            _difference_metrics(one_level, base)["content_difference_status"],
            "non_content_affecting",
        )
        large = base.copy()
        large[0, 0, 0, 0] = 2
        self.assertEqual(_difference_metrics(large, base)["content_difference_status"], "unresolved")

    def test_locked_relative_paths_fail_closed(self) -> None:
        self.assertEqual(
            _validate_relative_dicom_path("files/p10/p100/s1/clip.dcm"),
            "files/p10/p100/s1/clip.dcm",
        )
        for value in ("../clip.dcm", "/files/clip.dcm", "files/clip.txt"):
            with self.assertRaises(Exception):
                _validate_relative_dicom_path(value)

    def test_plan_only_restoration_blocks_only_on_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manifest = root / "restoration.csv"
            pd.DataFrame(
                [
                    {
                        "audit_id": "A1",
                        "clip_audit_id": "C1",
                        "declared_source_dicom": "files/p10/p100/s1/clip.dcm",
                        "unique_linkage": True,
                    }
                ]
            ).to_csv(manifest, index=False)
            result = restore_locked_sources(
                restoration_manifest_csv=manifest,
                output_root=root / "restricted",
                safe_output_dir=root / "safe",
                source_destination_root=root / "source",
                netrc_path=root / ".netrc",
                perform_downloads=False,
                job_a_command="qsub phase2i-job-a.sh",
            )
            self.assertEqual(result.safe_summary["status"], BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION)
            self.assertEqual(result.safe_summary["requested_files"], 1)
            action = root / "restricted" / "official_source_authentication_action.md"
            self.assertTrue(action.is_file())
            action_text = action.read_text()
            self.assertNotIn("\\nexport", action_text)
            self.assertNotIn("qsub -cwd -V", action_text)


class Phase2IInterfaceTests(unittest.TestCase):
    @staticmethod
    def _checkpoint_payload() -> dict[str, object]:
        return {
            "annotations": {
                "studies": {
                    "A1": {
                        "spectral_doppler_present": "no",
                        "reader_confidence": "high",
                    }
                },
                "clips": {
                    "C1": {
                        "acquisition_content_type": "2d_b_mode",
                        "visible_numeric_value": "no",
                        "candidate_target_value": "",
                        "reader_confidence": "high",
                    }
                },
            }
        }

    def test_autosave_resume_and_annotation_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = CheckpointStore(Path(tempdir) / "checkpoints")
            store.save("primary.reader1", self._checkpoint_payload())
            loaded = store.load("primary.reader1")
            self.assertEqual(
                loaded["annotations"]["clips"]["C1"]["acquisition_content_type"],
                "2d_b_mode",
            )
            store.lock("primary.reader1")
            self.assertTrue(store.load("primary.reader1")["locked"])
            with self.assertRaises(PermissionError):
                store.save("primary.reader1", self._checkpoint_payload())

    def test_checkpoint_rejects_unexpected_or_unblinded_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = CheckpointStore(Path(tempdir) / "checkpoints")
            payload = self._checkpoint_payload()
            payload["annotations"]["clips"]["C1"]["target_value"] = "18"  # type: ignore[index]
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                store.save("reader1", payload)

    def test_second_reader_namespace_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = CheckpointStore(Path(tempdir) / "checkpoints")
            store.save("primary.reader1", self._checkpoint_payload())
            self.assertEqual(store.load("second.reader1")["annotations"], {})

    def test_reader_record_is_whitelisted(self) -> None:
        row = {
            "audit_id": "A1",
            "clip_audit_id": "C1",
            "review_order": 1,
            "evidence_tier": EXACT_MODEL_INPUT,
            "source_media_id": "source1",
            "model_input_media_id": "model1",
            "model_input_verified": True,
            "source_only": False,
            "target": "lvot_vti",
            "target_value": 18.0,
            "study_id": 100,
            "unexpected_secret": "not exported",
        }
        visible = reader_visible_record(row)
        self.assertFalse(PROHIBITED_READER_FIELDS.intersection(visible))
        self.assertNotIn("unexpected_secret", visible)

    def test_interface_has_no_ocr_or_export_and_keeps_second_reader_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            media = root / "media"
            media.mkdir()
            for token in ("source1", "model1", "source2"):
                (media / f"{token}.png").write_bytes(b"png")
            technical = root / "technical.csv"
            pd.DataFrame(
                [
                    {
                        "audit_id": "A1",
                        "clip_audit_id": "C1",
                        "evidence_tier": EXACT_MODEL_INPUT,
                        "source_media_id": "source1",
                        "model_input_media_id": "model1",
                        "model_input_verified": True,
                        "source_only": False,
                        "target": "lvot_vti",
                        "study_id": 100,
                    },
                    {
                        "audit_id": "A2",
                        "clip_audit_id": "C2",
                        "evidence_tier": SOURCE_ACQUISITION_ONLY,
                        "source_media_id": "source2",
                        "model_input_media_id": "",
                        "model_input_verified": False,
                        "source_only": True,
                        "target": "tapse",
                        "study_id": 200,
                    },
                ]
            ).to_csv(technical, index=False)
            primary = root / "primary.csv"
            pd.DataFrame(
                [{"audit_id": "A1", "review_order": 1}, {"audit_id": "A2", "review_order": 2}]
            ).to_csv(primary, index=False)
            second = root / "second.csv"
            pd.DataFrame([{"audit_id": "A2", "review_order": 1}]).to_csv(second, index=False)
            output = root / "interface"
            result = build_blinded_interface_package(
                technical_manifest_csv=technical,
                reader_manifest_csv=primary,
                second_reader_manifest_csv=second,
                media_root=media,
                output_root=output,
            )
            self.assertEqual(result.summary["second_reader_studies"], 1)
            policy = json.loads((output / "interface_policy.json").read_text())
            self.assertFalse(policy["ocr_available"])
            self.assertFalse(policy["image_export_button"])
            self.assertFalse(policy["unrestricted_image_export_route"])
            rendered = "\n".join(path.read_text() for path in output.glob("*.json"))
            self.assertNotIn("lvot_vti", rendered)
            self.assertNotIn("tapse", rendered)
            html = (output / "index.html").read_text()
            self.assertNotIn("download", html.lower())
            self.assertNotIn("screenshot", html.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
