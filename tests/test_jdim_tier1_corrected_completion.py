from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.corrected_completion import (  # noqa: E402
    BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
    build_corrected_completion_manifest,
    validate_corrected_completion_manifest,
)
from jdim_tier1.safety import (  # noqa: E402
    Tier1BlockedError,
    restricted_file_record,
    safe_file_record,
    sha256_file,
)
from tests.jdim_tier1_corrected_fixture import build_valid_corrected_fixture  # noqa: E402


class CorrectedCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "corrected"
        self.output = self.root / "aggregate_safe" / "corrected_analysis_completion_v1.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _build_artifacts(self) -> dict[str, Path]:
        return build_valid_corrected_fixture(
            self.root, Path(self.tempdir.name) / "parents"
        )

    def _rebind_main_comparison_role(self, role: str, path: Path) -> None:
        restricted_path = (
            self.root
            / "restricted/comparisons/original_vs_corrected_main/input_provenance_restricted.json"
        )
        restricted = json.loads(restricted_path.read_text(encoding="utf-8"))
        restricted["input_files"] = [
            restricted_file_record(role, path)
            if record["logical_role"] == role
            else record
            for record in restricted["input_files"]
        ]
        restricted_path.write_text(json.dumps(restricted), encoding="utf-8")

        safe_path = (
            self.root
            / "aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json"
        )
        safe = json.loads(safe_path.read_text(encoding="utf-8"))
        safe["input_files"] = [
            safe_file_record(role, path)
            if record["logical_role"] == role
            else record
            for record in safe["input_files"]
        ]
        safe["restricted_input_manifest_sha256"] = sha256_file(restricted_path)
        safe_path.write_text(json.dumps(safe), encoding="utf-8")

    def test_complete_artifact_matrix_is_certified_once(self) -> None:
        artifacts = self._build_artifacts()
        payload = build_corrected_completion_manifest(self.root, self.output)
        self.assertEqual(payload["status"], "CORRECTED_ANALYSIS_COMPLETE")
        self.assertEqual(payload["required_artifact_count"], len(artifacts))
        self.assertTrue(self.output.exists())
        validated = validate_corrected_completion_manifest(self.root, self.output)
        self.assertEqual(validated["status"], "CORRECTED_ANALYSIS_COMPLETE")
        with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_artifact_mutation_after_certificate_fails_closed(self) -> None:
        artifacts = self._build_artifacts()
        build_corrected_completion_manifest(self.root, self.output)
        next(iter(artifacts.values())).write_bytes(b"changed after certification\n")
        with self.assertRaisesRegex(Tier1BlockedError, "changed after certification"):
            validate_corrected_completion_manifest(self.root, self.output)

    def test_missing_hard_extreme_comparison_fails_closed(self) -> None:
        artifacts = self._build_artifacts()
        missing = next(
            path
            for role, path in artifacts.items()
            if role.startswith("original_vs_corrected_hard_extremes_")
        )
        missing.unlink()
        with self.assertRaisesRegex(Tier1BlockedError, "workflow is incomplete") as context:
            build_corrected_completion_manifest(self.root, self.output)
        self.assertEqual(
            context.exception.status,
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
        )

    def test_failed_comparison_status_fails_closed(self) -> None:
        self._build_artifacts()
        provenance = (
            self.root
            / "aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json"
        )
        provenance.write_text(json.dumps({"status": "partial"}), encoding="utf-8")
        with self.assertRaisesRegex(Tier1BlockedError, "fixed comparison protocol"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_comparison_input_mutation_before_certificate_fails_closed(self) -> None:
        self._build_artifacts()
        prediction = (
            self.root
            / "restricted/analyses/lvot_vti/all_clips/imaging_baseline_predictions.csv"
        )
        prediction.write_bytes(b"changed after comparison\n")
        with self.assertRaisesRegex(Tier1BlockedError, "input provenance.*mismatch"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_aggregation_output_mutation_before_certificate_fails_closed(self) -> None:
        self._build_artifacts()
        corrected_embeddings = (
            self.root / "aggregation/restricted/corrected_study_embeddings.npz"
        )
        corrected_embeddings.write_bytes(b"changed after aggregation\n")
        with self.assertRaisesRegex(Tier1BlockedError, "output provenance.*mismatch"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_original_source_mutation_before_certificate_fails_closed(self) -> None:
        self._build_artifacts()
        restricted_path = (
            self.root
            / "restricted/comparisons/original_vs_corrected_main/input_provenance_restricted.json"
        )
        restricted = json.loads(restricted_path.read_text(encoding="utf-8"))
        record = next(
            item
            for item in restricted["input_files"]
            if item["logical_role"] == "original_predictions_lvot_vti"
        )
        Path(record["path"]).write_bytes(b"changed frozen original predictions\n")
        with self.assertRaisesRegex(Tier1BlockedError, "restricted input provenance.*mismatch"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_rebound_invalid_reviewer_metric_protocol_fails_closed(self) -> None:
        self._build_artifacts()
        provenance = (
            self.root
            / "aggregate_safe/reviewer_metrics/fixed_prediction_metrics_provenance.json"
        )
        payload = json.loads(provenance.read_text(encoding="utf-8"))
        payload["bootstrap_unit"] = "study"
        provenance.write_text(json.dumps(payload), encoding="utf-8")
        self._rebind_main_comparison_role(
            "corrected_reviewer_metrics_fixed_prediction_metrics_provenance.json",
            provenance,
        )
        with self.assertRaisesRegex(Tier1BlockedError, "fixed saved-prediction metrics"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_self_asserted_original_hash_cannot_replace_file_hash(self) -> None:
        self._build_artifacts()
        restricted_path = (
            self.root
            / "restricted/comparisons/original_vs_corrected_main/input_provenance_restricted.json"
        )
        restricted = json.loads(restricted_path.read_text(encoding="utf-8"))
        role = "original_predictions_lvot_vti"
        record = next(
            item for item in restricted["input_files"] if item["logical_role"] == role
        )
        record["sha256"] = "0" * 64
        restricted_path.write_text(json.dumps(restricted), encoding="utf-8")
        safe_path = (
            self.root
            / "aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json"
        )
        safe = json.loads(safe_path.read_text(encoding="utf-8"))
        next(
            item for item in safe["input_files"] if item["logical_role"] == role
        )["sha256"] = "0" * 64
        safe["restricted_input_manifest_sha256"] = sha256_file(restricted_path)
        safe_path.write_text(json.dumps(safe), encoding="utf-8")
        with self.assertRaisesRegex(Tier1BlockedError, "restricted input provenance.*mismatch"):
            build_corrected_completion_manifest(self.root, self.output)

    def test_rebound_reviewer_csv_cannot_leave_validated_metric_package(self) -> None:
        self._build_artifacts()
        alternate = Path(self.tempdir.name) / "alternate_calibration.csv"
        alternate.write_text(
            "target,bootstrap_n,bootstrap_seed\nlvot_vti,2000,20260824\n",
            encoding="utf-8",
        )
        self._rebind_main_comparison_role(
            "original_reviewer_metrics_continuous_calibration_metrics.csv",
            alternate,
        )
        with self.assertRaisesRegex(Tier1BlockedError, "outside its validated package"):
            build_corrected_completion_manifest(self.root, self.output)


if __name__ == "__main__":
    unittest.main()
