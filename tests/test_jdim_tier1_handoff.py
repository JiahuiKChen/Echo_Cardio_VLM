from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_jdim_cohort_lineage_metadata import (  # noqa: E402
    parse_name_class,
    parse_outside_universe_batches,
    parse_overlap_pairs,
)
from build_jdim_provenance_spec import (  # noqa: E402
    parse_arguments,
    parse_file_specs,
    validate_corrected_completion_roles,
)
from jdim_tier1.corrected_completion import (  # noqa: E402
    build_corrected_completion_manifest,
)
from jdim_tier1.safety import sanitize_for_safe_manifest  # noqa: E402
from tests.jdim_tier1_corrected_fixture import build_valid_corrected_fixture  # noqa: E402


class Tier1HandoffTests(unittest.TestCase):
    def test_lineage_metadata_cli_hashes_the_existing_split(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            split = root / "subject_split_map_v1.csv"
            output = root / "lineage.json"
            pd.DataFrame(
                [
                    {"subject_id": 1, "split": "train"},
                    {"subject_id": 2, "split": "test"},
                ]
            ).to_csv(split, index=False)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_jdim_cohort_lineage_metadata.py"),
                    "--mimic-iv-echo-release",
                    "synthetic-1.0",
                    "--source-denominator-definition",
                    "synthetic source",
                    "--imaging-lineage",
                    "synthetic imaging",
                    "--label-lineage",
                    "synthetic labels",
                    "--split-map-csv",
                    str(split),
                    "--split-version",
                    "subject_split_map_v1",
                    "--split-generator",
                    "synthetic generator",
                    "--batch-source",
                    "legacy=legacy",
                    "--batch-source",
                    "batch_000=fullscale",
                    "--allow-outside-universe-batch",
                    "legacy",
                    "--output-json",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(completed.stdout)["status"], "ok")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["batch_sources"]["legacy"]["source_class"], "legacy")
            self.assertEqual(
                payload["batch_sources"]["legacy"]["outside_universe_policy"],
                "declared_legacy_scope",
            )
            self.assertEqual(
                payload["batch_sources"]["batch_000"]["outside_universe_policy"],
                "canonical_only",
            )
            self.assertEqual(len(payload["split_map"]["expected_sha256"]), 64)

    def test_lineage_batch_and_overlap_syntax_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "path-like"):
            parse_name_class(["/restricted/batch=legacy"])
        batches = set(parse_name_class(["legacy=legacy", "batch_000=fullscale"]))
        with self.assertRaisesRegex(ValueError, "Invalid declared overlap"):
            parse_overlap_pairs(["legacy|legacy"], batches)
        with self.assertRaisesRegex(ValueError, "Invalid declared overlap"):
            parse_overlap_pairs(["legacy|unknown"], batches)
        parsed = parse_name_class(["legacy=legacy", "batch_000=fullscale"])
        self.assertEqual(parse_outside_universe_batches(["legacy"], parsed), {"legacy"})
        with self.assertRaisesRegex(ValueError, "only for legacy"):
            parse_outside_universe_batches(["batch_000"], parsed)
        with self.assertRaisesRegex(ValueError, "Unknown outside-universe batch"):
            parse_outside_universe_batches(["missing"], parsed)

    def test_provenance_spec_parsers_require_safe_roles_and_absolute_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "synthetic.csv"
            path.write_text("value\n1\n", encoding="utf-8")
            parsed = parse_file_specs([f"synthetic_input=restricted={path}"])
            self.assertEqual(parsed["synthetic_input"]["classification"], "restricted")
            with self.assertRaisesRegex(ValueError, "path-free"):
                parse_file_specs([f"/restricted/role=restricted={path}"])
            with self.assertRaisesRegex(ValueError, "must be absolute"):
                parse_file_specs(["relative=restricted=relative.csv"])
        self.assertEqual(parse_arguments(["bootstrap_n=2000", "model_refit=false"]), {
            "bootstrap_n": 2000,
            "model_refit": False,
        })

    def test_safe_manifest_sanitizer_redacts_embedded_absolute_paths(self) -> None:
        payload = sanitize_for_safe_manifest(
            {"command": "tool --input=/restricted/project/example.csv --bootstrap-n=2000"}
        )
        self.assertEqual(payload["command"], "[REDACTED_PATH]")

    def test_corrected_provenance_requires_complete_artifact_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "corrected"
            parents = {
                "frozen_study_embedding_manifest": Path(tempdir) / "frozen_studies.csv",
                "frozen_study_embedding_array": Path(tempdir) / "frozen_studies.npz",
                "historical_clip_embedding_manifest": Path(tempdir) / "historical_clips.csv",
                "historical_clip_embedding_array": Path(tempdir) / "historical_clips.npz",
            }
            for path in parents.values():
                path.write_bytes(b"synthetic parent\n")
            build_valid_corrected_fixture(
                root,
                Path(tempdir) / "comparison_sources",
                aggregation_parents=parents,
            )
            completion = root / "aggregate_safe/corrected_analysis_completion_v1.json"
            build_corrected_completion_manifest(root, completion)
            files = {
                role: {
                    "path": str(path),
                    "classification": "restricted",
                }
                for role, path in parents.items()
            }
            files.update(
                {
                    "corrected_study_embedding_manifest": {
                        "path": str(
                            root
                            / "aggregation/restricted/corrected_study_embedding_manifest.csv"
                        ),
                        "classification": "restricted",
                    },
                    "corrected_study_embedding_array": {
                        "path": str(root / "aggregation/restricted/corrected_study_embeddings.npz"),
                        "classification": "restricted",
                    },
                    "corrected_clip_embedding_manifest": {
                        "path": str(root / "aggregation/restricted/deduplicated_clip_manifest.csv"),
                        "classification": "restricted",
                    },
                    "corrected_clip_embedding_array": {
                        "path": str(root / "aggregation/restricted/deduplicated_clip_embeddings.npz"),
                        "classification": "restricted",
                    },
                    "corrected_aggregation_provenance": {
                        "path": str(
                            root
                            / "aggregation/restricted/corrected_aggregation_provenance_restricted.json"
                        ),
                        "classification": "restricted",
                    },
                    "corrected_reviewer_metrics_input_provenance": {
                        "path": str(
                            root
                            / "restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
                        ),
                        "classification": "restricted",
                    },
                    "corrected_main_comparison_provenance": {
                        "path": str(
                            root
                            / "aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json"
                        ),
                        "classification": "aggregate_safe",
                    },
                    "corrected_main_comparison_input_provenance": {
                        "path": str(
                            root
                            / "restricted/comparisons/original_vs_corrected_main/input_provenance_restricted.json"
                        ),
                        "classification": "restricted",
                    },
                    "corrected_hard_extremes_comparison_provenance": {
                        "path": str(
                            root
                            / "aggregate_safe/original_vs_corrected_hard_extremes/original_vs_corrected_comparison_provenance.json"
                        ),
                        "classification": "aggregate_safe",
                    },
                    "corrected_hard_extremes_comparison_input_provenance": {
                        "path": str(
                            root
                            / "restricted/comparisons/original_vs_corrected_hard_extremes/input_provenance_restricted.json"
                        ),
                        "classification": "restricted",
                    },
                    "corrected_analysis_completion": {
                        "path": str(completion),
                        "classification": "aggregate_safe",
                    },
                }
            )
            arguments = {"model_refit": True, "corrected_analysis_complete": True}
            validate_corrected_completion_roles(files, arguments)
            original_parent = files["historical_clip_embedding_array"]
            files["historical_clip_embedding_array"] = files["historical_clip_embedding_manifest"]
            with self.assertRaisesRegex(ValueError, "does not match aggregation provenance"):
                validate_corrected_completion_roles(files, arguments)
            files["historical_clip_embedding_array"] = original_parent
            files.pop("corrected_hard_extremes_comparison_provenance")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_corrected_completion_roles(files, arguments)

    def test_scc_wrapper_has_valid_shell_syntax_and_help(self) -> None:
        wrapper = ROOT / "scripts" / "scc_run_jdim_tier1.sh"
        subprocess.run(["bash", "-n", str(wrapper)], cwd=ROOT, check=True)
        completed = subprocess.run(
            ["bash", str(wrapper), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("reviewer-metrics", completed.stdout)
        self.assertIn("audit-pilot", completed.stdout)
        runbook = (ROOT / "docs" / "jdim_major_revision_tier1_runbook.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("--manual-audit-completion-json", runbook)
        self.assertIn("manual_audit_completion.json", runbook)

    def test_scc_handoff_resolves_explicit_inputs_from_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fullscale = root / "canonical_fullscale"
            legacy = root / "canonical_legacy"
            phase2 = root / "canonical_phase2"
            output = root / "new_output"
            corrected = root / "corrected_output"
            audit_root = output / "restricted" / "audit"

            required_files = {
                "source": root / "source.csv",
                "lineage": root / "lineage.json",
                "selected": fullscale / "manifests" / "selected.csv",
                "measures": fullscale / "manifests" / "measures.csv",
                "split": fullscale / "manifests" / "split.csv",
                "study_npz": fullscale / "study" / "study.npz",
                "study_manifest": fullscale / "study" / "study.csv",
                "clip_npz": fullscale / "merged" / "clips.npz",
                "clip_manifest": fullscale / "merged" / "clips.csv",
                "checkpoint": root / "weights" / "encoder.pt",
                "lvot_summary": phase2 / "lvot_summary.json",
                "tapse_summary": phase2 / "tapse_summary.json",
                "lvot_predictions": phase2 / "lvot_predictions.csv",
                "tapse_predictions": phase2 / "tapse_predictions.csv",
                "config": root / "audit_config.json",
                "legacy_records": legacy / "manifests" / "selected_records.csv",
                "legacy_audit": legacy / "audit" / "dicom_audit.csv",
                "legacy_extraction": legacy / "extract_allclip" / "extraction_manifest.csv",
                "legacy_embeddings": (
                    legacy / "echoprime_embeddings_512" / "clip_embedding_manifest.csv"
                ),
                "legacy_embedding_npz": (
                    legacy / "echoprime_embeddings_512" / "clip_embeddings_512.npz"
                ),
                "batch_records": fullscale / "batches" / "batch_000_records.csv",
                "batch_audit": fullscale / "batches" / "batch_000_audit" / "dicom_audit.csv",
                "batch_extraction": fullscale / "batches" / "batch_000_extraction_manifest.csv",
                "batch_embeddings": (
                    fullscale
                    / "batches"
                    / "batch_000_embeddings"
                    / "clip_embedding_manifest.csv"
                ),
                "batch_embedding_npz": (
                    fullscale
                    / "batches"
                    / "batch_000_embeddings"
                    / "clip_embeddings_512.npz"
                ),
            }
            for path in required_files.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic\n")
            output.mkdir(parents=True)
            audit_root.mkdir(parents=True)

            env = {
                **os.environ,
                "JDIM_REPO_ROOT": str(ROOT),
                "JDIM_PYTHON_BIN": sys.executable,
                "JDIM_FULLSCALE_ROOT": str(fullscale),
                "JDIM_LEGACY_ROOT": str(legacy),
                "JDIM_OUTPUT_ROOT": str(output),
                "JDIM_PHASE2_ROOT": str(phase2),
                "JDIM_SOURCE_STUDIES_CSV": str(required_files["source"]),
                "JDIM_LINEAGE_JSON": str(required_files["lineage"]),
                "JDIM_SELECTED_STUDIES_CSV": str(required_files["selected"]),
                "JDIM_STRUCTURED_MEASUREMENTS_CSV": str(required_files["measures"]),
                "JDIM_SPLIT_MAP_CSV": str(required_files["split"]),
                "JDIM_STUDY_EMBEDDING_NPZ": str(required_files["study_npz"]),
                "JDIM_STUDY_EMBEDDING_MANIFEST_CSV": str(
                    required_files["study_manifest"]
                ),
                "JDIM_CLIP_EMBEDDING_NPZ": str(required_files["clip_npz"]),
                "JDIM_CLIP_EMBEDDING_MANIFEST_CSV": str(
                    required_files["clip_manifest"]
                ),
                "JDIM_ENCODER_CHECKPOINT": str(required_files["checkpoint"]),
                "JDIM_LVOT_SUMMARY_JSON": str(required_files["lvot_summary"]),
                "JDIM_TAPSE_SUMMARY_JSON": str(required_files["tapse_summary"]),
                "JDIM_LVOT_PREDICTIONS_CSV": str(required_files["lvot_predictions"]),
                "JDIM_TAPSE_PREDICTIONS_CSV": str(required_files["tapse_predictions"]),
                "JDIM_AUDIT_CONFIG": str(required_files["config"]),
                "JDIM_RESTRICTED_AUDIT_ROOT": str(audit_root),
                "JDIM_CORRECTED_ROOT": str(corrected),
            }
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "scc_run_jdim_tier1.sh"), "handoff-check"],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("repository/worktree root resolved explicitly", completed.stdout)
            self.assertIn("selected universe", completed.stdout)
            self.assertIn("output roots are explicit and separate", completed.stdout)


if __name__ == "__main__":
    unittest.main()
