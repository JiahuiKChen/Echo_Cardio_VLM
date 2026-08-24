from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.cohort_flow import (  # noqa: E402
    CohortFlowInputs,
    _target_branch,
    reconstruct_cohort_flow,
    write_cohort_flow_outputs,
)
from jdim_tier1.safety import BLOCKED_LINEAGE, Tier1BlockedError, sha256_file  # noqa: E402


def write_csv(root: Path, name: str, rows: list[dict]) -> Path:
    path = root / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_json(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def make_fixture(root: Path) -> tuple[CohortFlowInputs, dict]:
    source_rows = [
        {"study_id": 1, "subject_id": 10},
        {"study_id": 2, "subject_id": 10},
        {"study_id": 3, "subject_id": 20},
        {"study_id": 4, "subject_id": 30},
        {"study_id": 5, "subject_id": 40},
        {"study_id": 6, "subject_id": 50},
    ]
    eligible_rows = source_rows[:5]
    records = [
        {"study_id": row["study_id"], "subject_id": row["subject_id"], "dicom_filepath": f"d{row['study_id']}.dcm"}
        for row in eligible_rows
    ]
    audits = [
        {**row, "read_ok": True, "is_multiframe": True}
        for row in records
    ]
    extraction = [
        {**row, "write_ok": True, "npz_path": f"clip{row['study_id']}.npz"}
        for row in records
    ]
    legacy_rows = [
        {"study_id": 1, "subject_id": 10, "dicom_filepath": "d1.dcm", "embedding_idx": 0, "write_ok": True},
        {"study_id": 2, "subject_id": 10, "dicom_filepath": "d2.dcm", "embedding_idx": 1, "write_ok": True},
        {"study_id": 6, "subject_id": 50, "dicom_filepath": "legacy_outside.dcm", "embedding_idx": 2, "write_ok": True},
    ]
    batch_rows = [
        {"study_id": 3, "subject_id": 20, "dicom_filepath": "d3.dcm", "embedding_idx": 0, "write_ok": True},
        {"study_id": 4, "subject_id": 30, "dicom_filepath": "d4.dcm", "embedding_idx": 1, "write_ok": True},
        {"study_id": 5, "subject_id": 40, "dicom_filepath": "d5.dcm", "embedding_idx": 2, "write_ok": True},
    ]
    final_rows = [
        {"study_id": row["study_id"], "subject_id": row["subject_id"], "study_idx": index, "n_clips": 1}
        for index, row in enumerate(eligible_rows)
    ]
    measures = [
        {"study_id": 1, "subject_id": 10, "measurement": "lvot_vti", "result": 12.0, "unit": "cm"},
        {"study_id": 1, "subject_id": 10, "measurement": "lvot_vti", "result": 18.0, "unit": "cm"},
        {"study_id": 2, "subject_id": 10, "measurement": "lvot_vti", "result": 20.0, "unit": "cm"},
        {"study_id": 3, "subject_id": 20, "measurement": "lvot_vti", "result": 22.0, "unit": "cm"},
        {"study_id": 4, "subject_id": 30, "measurement": "lvot_vti", "result": 24.0, "unit": "cm"},
        {"study_id": 2, "subject_id": 10, "measurement": "tapse", "result": 1.6, "unit": "cm"},
        {"study_id": 4, "subject_id": 30, "measurement": "tapse", "result": 18.0, "unit": "mm"},
        {"study_id": 5, "subject_id": 40, "measurement": "tapse", "result": 21.0, "unit": "mm"},
    ]
    split_rows = [
        {"subject_id": 10, "split": "train", "split_version": "subject_split_v1"},
        {"subject_id": 20, "split": "val", "split_version": "subject_split_v1"},
        {"subject_id": 30, "split": "test", "split_version": "subject_split_v1"},
        {"subject_id": 40, "split": "test", "split_version": "subject_split_v1"},
        {"subject_id": 50, "split": "train", "split_version": "subject_split_v1"},
    ]

    paths = {
        "source": write_csv(root, "source.csv", source_rows),
        "eligible": write_csv(root, "eligible.csv", eligible_rows),
        "records": write_csv(root, "records.csv", records),
        "audits": write_csv(root, "audits.csv", audits),
        "extraction": write_csv(root, "extraction.csv", extraction),
        "legacy": write_csv(root, "legacy.csv", legacy_rows),
        "batch": write_csv(root, "batch.csv", batch_rows),
        "final": write_csv(root, "final.csv", final_rows),
        "measures": write_csv(root, "measures.csv", measures),
        "split": write_csv(root, "split.csv", split_rows),
    }
    canonical = {
        "lvot_vti": {
            "target": "lvot_vti",
            "joined_target_embedding_studies": 4,
            "joined_target_embedding_subjects": 3,
            "split_counts": {"train": 2, "val": 1, "test": 1},
        },
        "tapse": {
            "target": "tapse",
            "joined_target_embedding_studies": 3,
            "joined_target_embedding_subjects": 3,
            "split_counts": {"train": 1, "val": 0, "test": 2},
        },
    }
    canonical_paths = {
        target: write_json(root, f"canonical_{target}.json", summary)
        for target, summary in canonical.items()
    }
    metadata = {
        "protocol_version": "jdim-tier1-v1",
        "flow_structure": "parallel_branches",
        "mimic_iv_echo_release": "synthetic-1.0",
        "source_denominator_definition": "synthetic official DICOM study universe",
        "imaging_lineage": "synthetic legacy plus batch union",
        "label_lineage": "synthetic structured measurements",
        "split_map": {
            "version": "subject_split_v1",
            "generator": "synthetic fixture",
            "expected_sha256": sha256_file(paths["split"]),
        },
        "batch_sources": {
            "legacy_stage_d_500": {"source_class": "legacy"},
            "batch_000": {"source_class": "fullscale"},
        },
        "allowed_batch_overlap_pairs": [],
    }
    paths["lineage"] = write_json(root, "lineage.json", metadata)
    inputs = CohortFlowInputs(
        source_studies=paths["source"],
        eligible_studies=paths["eligible"],
        expected_records=[paths["records"]],
        dicom_audits=[paths["audits"]],
        extraction_manifests=[paths["extraction"]],
        embedding_batches={"legacy_stage_d_500": paths["legacy"], "batch_000": paths["batch"]},
        final_study_embeddings=paths["final"],
        structured_measurements=paths["measures"],
        split_map=paths["split"],
        canonical_summaries=canonical_paths,
        lineage_metadata=paths["lineage"],
    )
    return inputs, paths


class CohortFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.inputs, self.paths = make_fixture(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_valid_parallel_flow_and_multiple_studies_per_subject(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")
        self.assertEqual(result.summary["final_study_embeddings"], 5)
        lvot_mult = result.subject_multiplicity.query("target == 'lvot_vti' and studies_per_subject == 2")
        self.assertEqual(int(lvot_mult.iloc[0]["n_subjects"]), 1)

    def test_repeated_target_rows_use_median(self) -> None:
        measures = pd.read_csv(self.paths["measures"])
        grouped, summary, _, multiplicity = _target_branch(measures, "lvot_vti")
        study_one = grouped[grouped["_study"] == "1"].iloc[0]
        self.assertEqual(float(study_one["target_value"]), 15.0)
        self.assertEqual(summary["studies_with_multiple_numeric_values"], 1)
        self.assertEqual(int(multiplicity.query("eligible_rows_per_study == 2").iloc[0]["n_studies"]), 1)

    def test_legacy_batch_retains_canonical_and_excludes_outside_member(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        legacy = result.batch_reconciliation.query("batch_name == 'legacy_stage_d_500'").iloc[0]
        self.assertEqual(int(legacy["n_studies_in_canonical_universe"]), 2)
        self.assertEqual(int(legacy["n_studies_outside_canonical_universe"]), 1)
        self.assertEqual(result.summary["final_study_embeddings"], 5)

    def test_duplicate_canonical_study_key_blocks_manuscript_outputs(self) -> None:
        eligible = pd.read_csv(self.paths["eligible"])
        eligible = pd.concat([eligible, eligible.iloc[[0]]], ignore_index=True)
        eligible.to_csv(self.paths["eligible"], index=False)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], BLOCKED_LINEAGE)
        output = self.root / "safe"
        with self.assertRaises(Tier1BlockedError):
            write_cohort_flow_outputs(result, output)
        self.assertTrue((output / "cohort_flow_invariants.json").exists())
        self.assertFalse((output / "cohort_flow_summary.json").exists())

    def test_conflicting_subject_assignment_fails_closed(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        batch.loc[batch["study_id"] == 3, "subject_id"] = 999
        batch.to_csv(self.paths["batch"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "conflicting subject"):
            reconstruct_cohort_flow(self.inputs)

    def test_subject_overlap_across_splits_fails_closed(self) -> None:
        split = pd.read_csv(self.paths["split"])
        split = pd.concat([split, pd.DataFrame([{"subject_id": 10, "split": "test"}])], ignore_index=True)
        split.to_csv(self.paths["split"], index=False)
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["split_map"]["expected_sha256"] = sha256_file(self.paths["split"])
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "invalid frozen split map"):
            reconstruct_cohort_flow(self.inputs)

    def test_missing_release_metadata_fails_closed(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["mimic_iv_echo_release"] = ""
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "missing lineage metadata"):
            reconstruct_cohort_flow(self.inputs)

    def test_missing_split_provenance_fails_closed(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        del metadata["split_map"]["expected_sha256"]
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "split_map.expected_sha256"):
            reconstruct_cohort_flow(self.inputs)

    def test_invalid_forced_linear_flow_is_rejected(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["flow_structure"] = "linear"
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "lineage metadata"):
            reconstruct_cohort_flow(self.inputs)

    def test_unexplained_cross_batch_overlap_fails_invariant(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        batch = pd.concat(
            [batch, pd.DataFrame([{"study_id": 2, "subject_id": 10, "dicom_filepath": "duplicate_d2.dcm", "embedding_idx": 9, "write_ok": True}])],
            ignore_index=True,
        )
        batch.to_csv(self.paths["batch"], index=False)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], BLOCKED_LINEAGE)
        overlap = [check for check in result.invariants["checks"] if check["check"].startswith("batch_overlap_explained")]
        self.assertFalse(overlap[0]["passed"])

    def test_declared_cross_batch_overlap_is_reconciled(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        batch = pd.concat(
            [batch, pd.DataFrame([{"study_id": 2, "subject_id": 10, "dicom_filepath": "duplicate_d2.dcm", "embedding_idx": 9, "write_ok": True}])],
            ignore_index=True,
        )
        batch.to_csv(self.paths["batch"], index=False)
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["allowed_batch_overlap_pairs"] = ["legacy_stage_d_500|batch_000"]
        self.paths["lineage"].write_text(json.dumps(metadata))
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")
        self.assertEqual(result.summary["final_study_embeddings"], 5)

    def test_canonical_split_mismatch_blocks(self) -> None:
        canonical = json.loads(self.inputs.canonical_summaries["tapse"].read_text())
        canonical["split_counts"]["test"] = 1
        self.inputs.canonical_summaries["tapse"].write_text(json.dumps(canonical))
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], BLOCKED_LINEAGE)


if __name__ == "__main__":
    unittest.main()
