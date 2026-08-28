from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.cohort_flow import (  # noqa: E402
    CohortFlowInputs,
    InvariantReport,
    MetadataDuplicatePolicy,
    _prepare_dicom_branch,
    _target_branch,
    reconstruct_cohort_flow,
    validate_cohort_input_schemas,
    validate_cohort_output_generation,
    write_cohort_flow_outputs,
)
from jdim_tier1.duplicate_forensics import source_manifest_row_fingerprint  # noqa: E402
from jdim_tier1.safety import (  # noqa: E402
    BLOCKED_COHORT_CANONICAL_MISMATCH,
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    canonical_id_set_sha256,
    forbidden_safe_columns,
    sha256_file,
)


def write_csv(root: Path, name: str, rows: list[dict]) -> Path:
    path = root / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_json(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def refresh_forensic_evidence(inputs: CohortFlowInputs) -> None:
    decisions = pd.read_csv(inputs.duplicate_forensics)
    if not decisions.empty:
        fingerprints: list[str] = []
        for row in decisions.to_dict(orient="records"):
            batch_name = str(row["_batch"])
            manifest = pd.read_csv(inputs.embedding_batches[batch_name])
            if "write_ok" in manifest.columns:
                success = manifest["write_ok"].astype(str).str.lower().isin(
                    {"true", "1", "yes", "y"}
                )
                manifest = manifest.loc[success].reset_index(drop=True)
            source_row = manifest.iloc[int(row["_manifest_row"])].to_dict()
            fingerprints.append(
                source_manifest_row_fingerprint(
                    source_row,
                    batch_name,
                    int(row["_manifest_row"]),
                )
            )
        decisions["source_manifest_row_fingerprint_sha256"] = fingerprints
        decisions.to_csv(inputs.duplicate_forensics, index=False)

    extraction_hashes = [sha256_file(path) for path in inputs.extraction_manifests]

    def extraction_hash_for(batch_name: str) -> str:
        matching = [
            path
            for path in inputs.extraction_manifests
            if batch_name in str(path)
        ]
        if matching:
            return sha256_file(matching[0])
        nonbatch = [path for path in inputs.extraction_manifests if "batch_" not in str(path)]
        return sha256_file(nonbatch[0] if nonbatch else inputs.extraction_manifests[0])
    payload = {
        "status": "ok",
        "restricted_artifact_sha256": {
            "duplicate_forensics_rows.csv": sha256_file(inputs.duplicate_forensics)
        },
        "input_provenance": {
            "split_map": {"sha256": sha256_file(inputs.split_map)},
            "batches": [
                {
                    "batch_name": name,
                    "extraction_manifest_sha256": extraction_hash_for(name),
                    "embedding_manifest_sha256": sha256_file(path),
                    "embedding_npz_sha256": sha256_file(inputs.embedding_batch_npzs[name]),
                }
                for name, path in sorted(inputs.embedding_batches.items())
            ],
        },
    }
    inputs.duplicate_forensics_provenance.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


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
        {
            "study_id": 6,
            "subject_id": 50,
            "dicom_filepath": "legacy_outside.dcm",
            "embedding_idx": 2,
            "write_ok": True,
        },
    ]
    batch_rows = [
        {"study_id": 3, "subject_id": 20, "dicom_filepath": "d3.dcm", "embedding_idx": 0, "write_ok": True},
        {"study_id": 4, "subject_id": 30, "dicom_filepath": "d4.dcm", "embedding_idx": 1, "write_ok": True},
        {"study_id": 5, "subject_id": 40, "dicom_filepath": "d5.dcm", "embedding_idx": 2, "write_ok": True},
    ]
    final_rows = [
        {"study_id": row["study_id"], "subject_id": row["subject_id"], "study_idx": index, "n_clips": 1}
        for index, row in enumerate(source_rows)
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
    paths["legacy_npz"] = root / "legacy_embeddings.npz"
    paths["batch_npz"] = root / "batch_embeddings.npz"
    np.savez_compressed(paths["legacy_npz"], embeddings=np.zeros((10, 3), dtype=np.float32))
    np.savez_compressed(paths["batch_npz"], embeddings=np.ones((10, 3), dtype=np.float32))
    forensic_columns = [
        "group_token",
        "classification",
        "_batch",
        "_manifest_row",
        "study_id",
        "subject_id",
        "dedup_keep_candidate",
        "source_manifest_row_fingerprint_sha256",
    ]
    paths["forensics"] = root / "duplicate_forensics_rows.csv"
    pd.DataFrame(columns=forensic_columns).to_csv(paths["forensics"], index=False)
    paths["lvot_predictions"] = write_csv(
        root,
        "lvot_predictions.csv",
        [
            {"target": "lvot_vti", "study_id": 1, "subject_id": 10, "split": "train", "target_value": 15.0},
            {"target": "lvot_vti", "study_id": 2, "subject_id": 10, "split": "train", "target_value": 20.0},
            {"target": "lvot_vti", "study_id": 3, "subject_id": 20, "split": "val", "target_value": 22.0},
            {"target": "lvot_vti", "study_id": 4, "subject_id": 30, "split": "test", "target_value": 24.0},
        ],
    )
    paths["tapse_predictions"] = write_csv(
        root,
        "tapse_predictions.csv",
        [
            {"target": "tapse", "study_id": 2, "subject_id": 10, "split": "train", "target_value": 16.0},
            {"target": "tapse", "study_id": 4, "subject_id": 30, "split": "test", "target_value": 18.0},
            {"target": "tapse", "study_id": 5, "subject_id": 40, "split": "test", "target_value": 21.0},
        ],
    )
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
            "legacy_stage_d_500": {
                "source_class": "legacy",
                "outside_universe_policy": "declared_legacy_scope",
                "declared_legacy_scope": {
                    "version": "jdim-declared-legacy-scope-v1",
                    "legacy_studies_total": 3,
                    "legacy_study_set_sha256": canonical_id_set_sha256({"1", "2", "6"}),
                    "inside_selected_universe_count": 2,
                    "inside_selected_universe_study_set_sha256": canonical_id_set_sha256({"1", "2"}),
                    "outside_selected_universe_count": 1,
                    "outside_selected_universe_study_set_sha256": canonical_id_set_sha256({"6"}),
                    "later_fullscale_overlap_count": 0,
                    "later_fullscale_overlap_study_set_sha256": canonical_id_set_sha256(set()),
                    "deduplicated_canonical_contribution_count": 2,
                    "deduplicated_canonical_contribution_study_set_sha256": canonical_id_set_sha256({"1", "2"}),
                },
            },
            "batch_000": {
                "source_class": "fullscale",
                "outside_universe_policy": "canonical_only",
            },
        },
        "allowed_batch_overlap_pairs": [],
        "selected_analysis_universe": {
            "study_count": 5,
            "subject_count": 4,
            "study_set_sha256": canonical_id_set_sha256({"1", "2", "3", "4", "5"}),
            "source_sha256": sha256_file(paths["eligible"]),
            "deterministic_selection_rule": "synthetic deterministic selection",
        },
    }
    paths["lineage"] = write_json(root, "lineage.json", metadata)
    paths["forensic_provenance"] = root / "duplicate_forensics_summary.json"
    inputs = CohortFlowInputs(
        source_studies=paths["source"],
        eligible_studies=paths["eligible"],
        expected_records=[paths["records"]],
        dicom_audits=[paths["audits"]],
        extraction_manifests=[paths["extraction"]],
        embedding_batches={"legacy_stage_d_500": paths["legacy"], "batch_000": paths["batch"]},
        embedding_batch_npzs={
            "legacy_stage_d_500": paths["legacy_npz"],
            "batch_000": paths["batch_npz"],
        },
        final_study_embeddings=paths["final"],
        structured_measurements=paths["measures"],
        split_map=paths["split"],
        canonical_summaries=canonical_paths,
        lineage_metadata=paths["lineage"],
        duplicate_forensics=paths["forensics"],
        duplicate_forensics_provenance=paths["forensic_provenance"],
        canonical_predictions={
            "lvot_vti": paths["lvot_predictions"],
            "tapse": paths["tapse_predictions"],
        },
    )
    refresh_forensic_evidence(inputs)
    return inputs, paths


def attach_exact_metadata_duplicate_packet(
    inputs: CohortFlowInputs,
    paths: dict,
) -> None:
    group_rows: list[dict] = []
    stage_rows: list[dict] = []
    decision_rows: list[dict] = []
    duplicate_records: list[dict] = []
    duplicate_audits: list[dict] = []
    duplicate_extractions: list[dict] = []
    batch = pd.read_csv(paths["batch"])
    next_embedding_idx = int(batch["embedding_idx"].max()) + 1
    manifest_row = len(batch)
    for group_index in range(32):
        token = f"approved-group-{group_index:02d}"
        dicom = f"approved_duplicate_{group_index:02d}.dcm"
        group_rows.append(
            {
                "group_token": token,
                "classification": "TRUE_DUPLICATE_EXPECTED_ROWS",
                "first_duplicate_stage": "expected_records",
                "semantic_identity_equal_all_stages": True,
                "n_expected_records_rows": 2,
                "n_dicom_audit_rows": 2,
                "n_cine_candidates_rows": 2,
                "n_extraction_manifest_rows": 2,
                "n_batch_embedding_manifest_rows": 2,
                "n_merged_embedding_manifest_rows": 2,
            }
        )
        record = {"study_id": 3, "subject_id": 20, "dicom_filepath": dicom}
        audit = {**record, "read_ok": True, "is_multiframe": True}
        extraction = {
            **record,
            "write_ok": True,
            "npz_path": f"approved_duplicate_{group_index:02d}.npz",
        }
        for pair_index in range(2):
            duplicate_records.append(dict(record))
            duplicate_audits.append(dict(audit))
            duplicate_extractions.append(dict(extraction))
            batch_row = {
                **record,
                "embedding_idx": next_embedding_idx,
                "write_ok": True,
            }
            batch = pd.concat([batch, pd.DataFrame([batch_row])], ignore_index=True)
            decision_rows.append(
                {
                    "group_token": token,
                    "classification": "TRUE_DUPLICATE_EXPECTED_ROWS",
                    "_batch": "batch_000",
                    "_manifest_row": manifest_row,
                    "study_id": 3,
                    "subject_id": 20,
                    "dicom_filepath": dicom,
                    "semantic_clip_identity_sha256": f"semantic-{group_index:02d}",
                    "dedup_keep_candidate": pair_index == 0,
                }
            )
            manifest_row += 1
            next_embedding_idx += 1
        for stage in (
            "expected_records",
            "dicom_audit",
            "cine_candidates",
            "extraction_manifest",
            "batch_embedding_manifest",
            "merged_embedding_manifest",
        ):
            for pair_index in range(2):
                stage_rows.append(
                    {
                        "group_token": token,
                        "stage": stage,
                        "stage_row_ordinal": pair_index,
                        "study_id": 3,
                        "subject_id": 20,
                        "dicom_filepath": dicom,
                    }
                )

    batch.to_csv(paths["batch"], index=False)
    duplicate_record_path = write_csv(
        paths["records"].parent,
        "batch_000_duplicate_records.csv",
        duplicate_records,
    )
    duplicate_audit_path = write_csv(
        paths["audits"].parent,
        "batch_000_duplicate_audit.csv",
        duplicate_audits,
    )
    duplicate_extraction_path = write_csv(
        paths["extraction"].parent,
        "batch_000_duplicate_extraction.csv",
        duplicate_extractions,
    )
    inputs.expected_records = [paths["records"], duplicate_record_path]
    inputs.dicom_audits = [paths["audits"], duplicate_audit_path]
    inputs.extraction_manifests = [paths["extraction"], duplicate_extraction_path]
    pd.DataFrame(decision_rows).to_csv(paths["forensics"], index=False)
    final = pd.read_csv(paths["final"])
    final.loc[final["study_id"] == 3, "n_clips"] = 33
    final.to_csv(paths["final"], index=False)

    groups_path = write_csv(paths["records"].parent, "metadata_groups_exact.csv", group_rows)
    stage_rows_path = write_csv(
        paths["records"].parent,
        "metadata_stage_rows_exact.csv",
        stage_rows,
    )
    metadata_summary_path = write_json(
        paths["records"].parent,
        "metadata_summary_exact.json",
        {
            "status": "DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE",
            "restricted_evidence_packet_sha256": {
                "metadata_stage_rows.csv": sha256_file(stage_rows_path)
            },
        },
    )
    corrected_path = write_csv(
        paths["records"].parent,
        "corrected_clip_manifest_exact.csv",
        [
            {
                "study_id": 3,
                "subject_id": 20,
                "dicom_filepath": f"approved_duplicate_{index:02d}.dcm",
                "forensic_classification": "TRUE_DUPLICATE_EXPECTED_ROWS",
                "dedup_group_size": 2,
                "write_ok": True,
            }
            for index in range(32)
        ],
    )
    inputs.duplicate_metadata_summary = metadata_summary_path
    inputs.duplicate_metadata_groups = groups_path
    inputs.duplicate_metadata_stage_rows = stage_rows_path
    inputs.corrected_clip_embeddings = corrected_path
    refresh_packet_evidence(inputs)


def refresh_packet_evidence(inputs: CohortFlowInputs) -> None:
    refresh_forensic_evidence(inputs)
    provenance = json.loads(inputs.duplicate_forensics_provenance.read_text())
    provenance["metadata_resolution"] = {
        "classification": "TRUE_DUPLICATE_EXPECTED_ROWS",
        "first_duplicate_stage": "expected_records",
        "n_groups": 32,
        "metadata_group_classification_sha256": sha256_file(
            inputs.duplicate_metadata_groups
        ),
        "metadata_summary_sha256": sha256_file(inputs.duplicate_metadata_summary),
    }
    inputs.duplicate_forensics_provenance.write_text(json.dumps(provenance, indent=2))


def synthetic_duplicate_stage_inputs(
    *,
    extra_group: bool = False,
    three_row_group: bool = False,
    batch_name: str = "batch_000",
    changed_study: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, MetadataDuplicatePolicy]:
    keys = [("3", f"duplicate_{index:02d}.dcm") for index in range(32)]
    key_to_token = {key: f"group-{index:02d}" for index, key in enumerate(keys)}
    key_to_subject = {key: "20" for key in keys}
    rows: list[dict] = []
    for index, (_, dicom) in enumerate(keys):
        row_count = 3 if three_row_group and index == 0 else 2
        for _ in range(row_count):
            rows.append(
                {
                    "study_id": 999 if changed_study and index == 0 else 3,
                    "subject_id": 20,
                    "dicom_filepath": dicom,
                    "read_ok": True,
                    "is_multiframe": True,
                    "write_ok": True,
                    "_source_batch": batch_name,
                }
            )
    if extra_group:
        rows.extend(
            [
                {
                    "study_id": 3,
                    "subject_id": 20,
                    "dicom_filepath": "duplicate_32.dcm",
                    "read_ok": True,
                    "is_multiframe": True,
                    "write_ok": True,
                    "_source_batch": batch_name,
                }
                for _ in range(2)
            ]
        )
    frame = pd.DataFrame(rows)
    policy = MetadataDuplicatePolicy(
        key_to_token=key_to_token,
        key_to_subject=key_to_subject,
        expected_batch="batch_000",
        n_groups=32,
        n_member_rows=64,
        n_duplicate_rows=32,
        packet_hashes={},
    )
    return frame.copy(), frame.copy(), frame.copy(), policy


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
        self.assertEqual(result.summary["final_study_embeddings"], 6)
        self.assertEqual(result.summary["canonical_universe_study_embeddings"], 5)
        self.assertEqual(result.summary["outside_universe_retained_legacy_embeddings"], 1)
        lvot_mult = result.subject_multiplicity.query("target == 'lvot_vti' and studies_per_subject == 2")
        self.assertEqual(int(lvot_mult.iloc[0]["n_subjects"]), 1)

    def test_exact_approved_32_metadata_duplicate_groups_pass(self) -> None:
        attach_exact_metadata_duplicate_packet(self.inputs, self.paths)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")
        counts = result.summary["metadata_duplicate_adjudication"]
        for stage in ("expected_records", "dicom_audit", "extraction_manifest"):
            self.assertEqual(counts[stage]["provenance_resolved_duplicate_member_rows"], 64)
            self.assertEqual(counts[stage]["provenance_resolved_duplicate_rows"], 32)
            self.assertEqual(
                counts[stage]["raw_manifest_rows"] - 32,
                counts[stage]["adjudicated_unique_clip_rows"],
            )
        preflight = validate_cohort_output_generation(result)
        self.assertTrue(preflight["output_generation_validated"])
        self.assertFalse(preflight["aggregate_output_written"])

    def test_33rd_metadata_duplicate_group_fails(self) -> None:
        expected, audit, extraction, policy = synthetic_duplicate_stage_inputs(extra_group=True)
        with self.assertRaisesRegex(Tier1BlockedError, "approved 32 batch_000 groups"):
            _prepare_dicom_branch(expected, audit, extraction, InvariantReport(), policy)

    def test_three_row_metadata_duplicate_group_fails(self) -> None:
        expected, audit, extraction, policy = synthetic_duplicate_stage_inputs(
            three_row_group=True
        )
        with self.assertRaisesRegex(Tier1BlockedError, "approved 32 batch_000 groups"):
            _prepare_dicom_branch(expected, audit, extraction, InvariantReport(), policy)

    def test_metadata_duplicate_in_other_batch_fails(self) -> None:
        expected, audit, extraction, policy = synthetic_duplicate_stage_inputs(
            batch_name="batch_001"
        )
        with self.assertRaisesRegex(Tier1BlockedError, "approved 32 batch_000 groups"):
            _prepare_dicom_branch(expected, audit, extraction, InvariantReport(), policy)

    def test_metadata_duplicate_affecting_other_study_fails(self) -> None:
        expected, audit, extraction, policy = synthetic_duplicate_stage_inputs(
            changed_study=True
        )
        with self.assertRaisesRegex(Tier1BlockedError, "approved 32 batch_000 groups"):
            _prepare_dicom_branch(expected, audit, extraction, InvariantReport(), policy)

    def test_declared_legacy_scope_wrong_hash_fails(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["batch_sources"]["legacy_stage_d_500"]["declared_legacy_scope"][
            "outside_selected_universe_study_set_sha256"
        ] = "0" * 64
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "exact declared legacy scope"):
            reconstruct_cohort_flow(self.inputs)

    def test_declared_legacy_outside_study_entering_target_fails(self) -> None:
        measures = pd.read_csv(self.paths["measures"])
        measures = pd.concat(
            [
                measures,
                pd.DataFrame(
                    [
                        {
                            "study_id": 6,
                            "subject_id": 50,
                            "measurement": "lvot_vti",
                            "result": 19.0,
                            "unit": "cm",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        measures.to_csv(self.paths["measures"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "entered the lvot_vti label cohort"):
            reconstruct_cohort_flow(self.inputs)

    def test_declared_legacy_outside_study_receiving_split_fails(self) -> None:
        predictions = pd.read_csv(self.paths["lvot_predictions"])
        predictions = pd.concat(
            [
                predictions,
                pd.DataFrame(
                    [
                        {
                            "target": "lvot_vti",
                            "study_id": 6,
                            "subject_id": 50,
                            "split": "train",
                            "target_value": 19.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        predictions.to_csv(self.paths["lvot_predictions"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "entered canonical lvot_vti predictions"):
            reconstruct_cohort_flow(self.inputs)

    def test_target_intersections_use_only_corrected_canonical_universe(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        outside = result.summary["declared_legacy_outside_analysis_universe"]
        self.assertEqual(outside, 1)
        checks = {item["check"]: item for item in result.invariants["checks"]}
        for target in ("lvot_vti", "tapse"):
            self.assertTrue(
                checks[
                    f"{target}_declared_legacy_outside_studies_receive_no_canonical_split"
                ]["passed"]
            )

    def test_cohort_reconstruction_does_not_overwrite_historical_inputs(self) -> None:
        paths = [
            *self.inputs.expected_records,
            *self.inputs.dicom_audits,
            *self.inputs.extraction_manifests,
            *self.inputs.embedding_batches.values(),
        ]
        before = {path: sha256_file(path) for path in paths}
        reconstruct_cohort_flow(self.inputs)
        self.assertEqual(before, {path: sha256_file(path) for path in paths})

    def test_aggregate_safe_outputs_have_no_identifiers_or_restricted_paths(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        output = self.root / "aggregate_safe"
        write_cohort_flow_outputs(result, output)
        for path in output.glob("*.csv"):
            frame = pd.read_csv(path)
            self.assertEqual(forbidden_safe_columns(frame.columns), [])
        for path in output.iterdir():
            if path.suffix in {".json", ".csv", ".txt", ".mmd"}:
                self.assertNotIn(str(self.root), path.read_text(encoding="utf-8"))

    def test_legitimate_repeated_dicom_keys_preserve_both_clip_inputs(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        duplicate = dict(batch.iloc[0])
        duplicate["embedding_idx"] = 3
        batch = pd.concat([batch, pd.DataFrame([duplicate])], ignore_index=True)
        batch.to_csv(self.paths["batch"], index=False)
        final = pd.read_csv(self.paths["final"])
        final.loc[final["study_id"] == 3, "n_clips"] = 2
        final.to_csv(self.paths["final"], index=False)
        pd.DataFrame(
            [
                {
                    "group_token": "g1",
                    "classification": "LEGITIMATE_DISTINCT_CLIPS",
                    "_batch": "batch_000",
                    "_manifest_row": row,
                    "study_id": 3,
                    "subject_id": 20,
                    "dedup_keep_candidate": True,
                }
                for row in (0, 3)
            ]
        ).to_csv(self.paths["forensics"], index=False)
        refresh_forensic_evidence(self.inputs)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")
        stage = result.stages.set_index("stage")
        self.assertEqual(
            int(stage.loc["repeated_clip_rows_legitimate_distinct_clips", "n_rows"]), 2
        )

    def test_forensic_row_identity_uses_successful_embedding_ordinal(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        failed = dict(batch.iloc[0])
        failed.update(
            {
                "dicom_filepath": "failed_before_successes.dcm",
                "embedding_idx": -1,
                "write_ok": False,
            }
        )
        duplicate = dict(batch.iloc[0])
        duplicate["embedding_idx"] = 3
        batch = pd.concat(
            [pd.DataFrame([failed]), batch, pd.DataFrame([duplicate])],
            ignore_index=True,
        )
        batch.to_csv(self.paths["batch"], index=False)
        final = pd.read_csv(self.paths["final"])
        final.loc[final["study_id"] == 3, "n_clips"] = 2
        final.to_csv(self.paths["final"], index=False)
        pd.DataFrame(
            [
                {
                    "group_token": "g1",
                    "classification": "LEGITIMATE_DISTINCT_CLIPS",
                    "_batch": "batch_000",
                    "_manifest_row": row,
                    "study_id": 3,
                    "subject_id": 20,
                    "dedup_keep_candidate": True,
                }
                for row in (0, 3)
            ]
        ).to_csv(self.paths["forensics"], index=False)
        refresh_forensic_evidence(self.inputs)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")

    def test_true_duplicate_rows_require_corrected_final_clip_count(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        duplicate = dict(batch.iloc[0])
        duplicate["embedding_idx"] = 3
        batch = pd.concat([batch, pd.DataFrame([duplicate])], ignore_index=True)
        batch.to_csv(self.paths["batch"], index=False)
        pd.DataFrame(
            [
                {
                    "group_token": "g1",
                    "classification": "TRUE_DUPLICATE_EMBEDDING_ROWS",
                    "_batch": "batch_000",
                    "_manifest_row": row,
                    "study_id": 3,
                    "subject_id": 20,
                    "dedup_keep_candidate": row == 0,
                }
                for row in (0, 3)
            ]
        ).to_csv(self.paths["forensics"], index=False)
        refresh_forensic_evidence(self.inputs)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")

    def test_forensic_decision_identity_mismatch_fails_closed(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        duplicate = dict(batch.iloc[0])
        duplicate["embedding_idx"] = 3
        pd.concat([batch, pd.DataFrame([duplicate])], ignore_index=True).to_csv(
            self.paths["batch"], index=False
        )
        pd.DataFrame(
            [
                {
                    "group_token": "g1",
                    "classification": "TRUE_DUPLICATE_EMBEDDING_ROWS",
                    "_batch": "batch_000",
                    "_manifest_row": row,
                    "study_id": 999 if row == 0 else 3,
                    "subject_id": 20,
                    "dedup_keep_candidate": row == 0,
                }
                for row in (0, 3)
            ]
        ).to_csv(self.paths["forensics"], index=False)
        refresh_forensic_evidence(self.inputs)
        with self.assertRaisesRegex(Tier1BlockedError, "identity does not match"):
            reconstruct_cohort_flow(self.inputs)

    def test_stale_forensic_evidence_fails_when_source_manifest_changes(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        duplicate = dict(batch.iloc[0])
        duplicate["embedding_idx"] = 3
        pd.concat([batch, pd.DataFrame([duplicate])], ignore_index=True).to_csv(
            self.paths["batch"], index=False
        )
        pd.DataFrame(
            [
                {
                    "group_token": "g1",
                    "classification": "LEGITIMATE_DISTINCT_CLIPS",
                    "_batch": "batch_000",
                    "_manifest_row": row,
                    "study_id": 3,
                    "subject_id": 20,
                    "dedup_keep_candidate": True,
                }
                for row in (0, 3)
            ]
        ).to_csv(self.paths["forensics"], index=False)
        refresh_forensic_evidence(self.inputs)

        changed = pd.read_csv(self.paths["batch"])
        changed.loc[0, "embedding_idx"] = 8
        changed.to_csv(self.paths["batch"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "provenance is stale"):
            reconstruct_cohort_flow(self.inputs)

    def test_empty_forensic_decisions_remain_bound_to_source_manifests(self) -> None:
        self.assertTrue(pd.read_csv(self.paths["forensics"]).empty)
        changed = pd.read_csv(self.paths["batch"])
        changed.loc[0, "embedding_idx"] = 8
        changed.to_csv(self.paths["batch"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "provenance is stale"):
            reconstruct_cohort_flow(self.inputs)

    def test_extra_extraction_manifest_fails_forensic_binding(self) -> None:
        extra = write_csv(
            self.root,
            "extra_extraction.csv",
            [
                {
                    "study_id": 999,
                    "subject_id": 9990,
                    "dicom_filepath": "unexpected.dcm",
                    "write_ok": True,
                    "npz_path": "unexpected.npz",
                }
            ],
        )
        self.inputs.extraction_manifests = [*self.inputs.extraction_manifests, extra]
        with self.assertRaisesRegex(Tier1BlockedError, "extraction-manifest set"):
            reconstruct_cohort_flow(self.inputs)

    def test_prediction_rows_must_match_reconstructed_target_cohort(self) -> None:
        predictions = pd.read_csv(self.paths["lvot_predictions"])
        predictions.loc[predictions["study_id"] == 3, "target_value"] = 999.0
        predictions.to_csv(self.paths["lvot_predictions"], index=False)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], BLOCKED_LINEAGE)
        checks = {item["check"]: item for item in result.invariants["checks"]}
        self.assertFalse(checks["lvot_vti_prediction_labels_match_reconstruction"]["passed"])

    def test_schema_only_validation_does_not_compute_counts(self) -> None:
        payload = validate_cohort_input_schemas(self.inputs)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["row_level_analysis_executed"])
        self.assertEqual(payload["canonical_targets_checked"], ["lvot_vti", "tapse"])

    def test_cohort_cli_schema_only_integration(self) -> None:
        metadata_summary = write_json(self.root, "metadata_summary.json", {})
        metadata_groups = write_csv(
            self.root,
            "metadata_groups.csv",
            [
                {
                    "group_token": "synthetic",
                    "classification": "TRUE_DUPLICATE_EXPECTED_ROWS",
                    "first_duplicate_stage": "expected_records",
                }
            ],
        )
        metadata_stage_rows = write_csv(
            self.root,
            "metadata_stage_rows.csv",
            [
                {
                    "group_token": "synthetic",
                    "stage": "expected_records",
                    "study_id": 1,
                    "subject_id": 10,
                    "dicom_filepath": "d1.dcm",
                }
            ],
        )
        corrected_clips = write_csv(
            self.root,
            "corrected_clips.csv",
            [
                {
                    "study_id": 1,
                    "subject_id": 10,
                    "dicom_filepath": "d1.dcm",
                    "forensic_classification": "UNIQUE",
                }
            ],
        )
        command = [
            sys.executable,
            str(ROOT / "scripts" / "reconstruct_jdim_cohort_flow.py"),
            "--source-studies-csv",
            str(self.inputs.source_studies),
            "--eligible-studies-csv",
            str(self.inputs.eligible_studies),
            "--expected-records-csv",
            *map(str, self.inputs.expected_records),
            "--dicom-audit-csv",
            *map(str, self.inputs.dicom_audits),
            "--extraction-manifest-csv",
            *map(str, self.inputs.extraction_manifests),
        ]
        for name, path in self.inputs.embedding_batches.items():
            command.extend(["--embedding-batch", f"{name}={path}"])
        for name, path in self.inputs.embedding_batch_npzs.items():
            command.extend(["--embedding-batch-npz", f"{name}={path}"])
        command.extend(
            [
                "--final-study-embedding-manifest-csv",
                str(self.inputs.final_study_embeddings),
                "--structured-measurements-csv",
                str(self.inputs.structured_measurements),
                "--subject-split-map-csv",
                str(self.inputs.split_map),
            ]
        )
        for target, path in self.inputs.canonical_summaries.items():
            command.extend(["--canonical-summary", f"{target}={path}"])
        for target, path in self.inputs.canonical_predictions.items():
            command.extend(["--canonical-prediction", f"{target}={path}"])
        command.extend(
            [
                "--duplicate-forensics-rows-csv",
                str(self.inputs.duplicate_forensics),
                "--duplicate-forensics-provenance-json",
                str(self.inputs.duplicate_forensics_provenance),
                "--duplicate-metadata-summary-json",
                str(metadata_summary),
                "--duplicate-metadata-groups-csv",
                str(metadata_groups),
                "--duplicate-metadata-stage-rows-csv",
                str(metadata_stage_rows),
                "--corrected-clip-embedding-manifest-csv",
                str(corrected_clips),
                "--lineage-metadata-json",
                str(self.inputs.lineage_metadata),
                "--output-dir",
                str(self.root / "unused"),
                "--schema-only",
            ]
        )
        completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["row_level_analysis_executed"])

    def test_repeated_target_rows_use_median(self) -> None:
        measures = pd.read_csv(self.paths["measures"])
        grouped, summary, _, multiplicity = _target_branch(measures, "lvot_vti")
        study_one = grouped[grouped["_study"] == "1"].iloc[0]
        self.assertEqual(float(study_one["target_value"]), 15.0)
        self.assertEqual(summary["studies_with_multiple_numeric_values"], 1)
        self.assertEqual(int(multiplicity.query("eligible_rows_per_study == 2").iloc[0]["n_studies"]), 1)

    def test_restricted_target_cohort_retains_report_label_value(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        destination = self.root / "restricted" / "reconciliation.csv"
        safe = self.root / "safe"
        write_cohort_flow_outputs(result, safe, destination)
        lvot = pd.read_csv(destination.parent / "jdim_target_cohort_lvot_vti.csv")
        study_one = lvot[lvot["study_id"] == 1].iloc[0]
        self.assertEqual(float(study_one["target_value"]), 15.0)
        self.assertEqual(int(study_one["n_target_rows"]), 2)
        with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
            write_cohort_flow_outputs(result, safe, destination)

    def test_declared_legacy_batch_reconciles_outside_member_separately(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        legacy = result.batch_reconciliation.query("batch_name == 'legacy_stage_d_500'").iloc[0]
        self.assertEqual(int(legacy["n_studies_in_canonical_universe"]), 2)
        self.assertEqual(int(legacy["n_studies_outside_canonical_universe"]), 1)
        self.assertEqual(
            int(legacy["n_declared_legacy_studies_outside_canonical_universe"]),
            1,
        )
        self.assertEqual(result.summary["canonical_universe_study_embeddings"], 5)
        self.assertEqual(result.summary["outside_universe_retained_legacy_embeddings"], 1)

    def test_undeclared_outside_universe_batch_study_fails_closed(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["batch_sources"]["legacy_stage_d_500"]["outside_universe_policy"] = (
            "canonical_only"
        )
        del metadata["batch_sources"]["legacy_stage_d_500"]["declared_legacy_scope"]
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "outside the exact declared legacy scope"):
            reconstruct_cohort_flow(self.inputs)

    def test_unexplained_final_outside_universe_study_fails_closed(self) -> None:
        final = pd.read_csv(self.paths["final"])
        final = pd.concat(
            [
                final,
                pd.DataFrame(
                    [
                        {
                            "study_id": 999,
                            "subject_id": 9990,
                            "study_idx": 99,
                            "n_clips": 1,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        final.to_csv(self.paths["final"], index=False)
        with self.assertRaisesRegex(Tier1BlockedError, "does not reconcile"):
            reconstruct_cohort_flow(self.inputs)

    def test_canonical_intersection_matches_exactly_with_mixed_final_manifest(self) -> None:
        result = reconstruct_cohort_flow(self.inputs)
        checks = {item["check"]: item for item in result.invariants["checks"]}
        self.assertTrue(
            checks["canonical_batch_union_matches_final_manifest_canonical_intersection"]["passed"]
        )
        self.assertTrue(
            checks["declared_legacy_outside_union_matches_final_manifest_outside_scope"]["passed"]
        )

    def test_duplicate_canonical_study_key_blocks_manuscript_outputs(self) -> None:
        eligible = pd.read_csv(self.paths["eligible"])
        eligible = pd.concat([eligible, eligible.iloc[[0]]], ignore_index=True)
        eligible.to_csv(self.paths["eligible"], index=False)
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["selected_analysis_universe"]["source_sha256"] = sha256_file(
            self.paths["eligible"]
        )
        self.paths["lineage"].write_text(json.dumps(metadata))
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
        refresh_forensic_evidence(self.inputs)
        with self.assertRaisesRegex(Tier1BlockedError, "conflicting subject"):
            reconstruct_cohort_flow(self.inputs)

    def test_subject_overlap_across_splits_fails_closed(self) -> None:
        split = pd.read_csv(self.paths["split"])
        split = pd.concat([split, pd.DataFrame([{"subject_id": 10, "split": "test"}])], ignore_index=True)
        split.to_csv(self.paths["split"], index=False)
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["split_map"]["expected_sha256"] = sha256_file(self.paths["split"])
        self.paths["lineage"].write_text(json.dumps(metadata))
        refresh_forensic_evidence(self.inputs)
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

    def test_lineage_batch_set_must_match_supplied_inputs(self) -> None:
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["batch_sources"]["batch_999"] = {"source_class": "fullscale"}
        self.paths["lineage"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(Tier1BlockedError, "not supplied as inputs"):
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
            [
                batch,
                pd.DataFrame(
                    [
                        {
                            "study_id": 2,
                            "subject_id": 10,
                            "dicom_filepath": "duplicate_d2.dcm",
                            "embedding_idx": 9,
                            "write_ok": True,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        batch.to_csv(self.paths["batch"], index=False)
        refresh_forensic_evidence(self.inputs)
        with self.assertRaisesRegex(Tier1BlockedError, "exact declared legacy scope"):
            reconstruct_cohort_flow(self.inputs)

    def test_declared_cross_batch_overlap_is_reconciled(self) -> None:
        batch = pd.read_csv(self.paths["batch"])
        batch = pd.concat(
            [
                batch,
                pd.DataFrame(
                    [
                        {
                            "study_id": 2,
                            "subject_id": 10,
                            "dicom_filepath": "duplicate_d2.dcm",
                            "embedding_idx": 9,
                            "write_ok": True,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        batch.to_csv(self.paths["batch"], index=False)
        refresh_forensic_evidence(self.inputs)
        metadata = json.loads(self.paths["lineage"].read_text())
        metadata["allowed_batch_overlap_pairs"] = ["legacy_stage_d_500|batch_000"]
        scope = metadata["batch_sources"]["legacy_stage_d_500"]["declared_legacy_scope"]
        scope.update(
            {
                "later_fullscale_overlap_count": 1,
                "later_fullscale_overlap_study_set_sha256": canonical_id_set_sha256({"2"}),
                "deduplicated_canonical_contribution_count": 1,
                "deduplicated_canonical_contribution_study_set_sha256": canonical_id_set_sha256({"1"}),
            }
        )
        self.paths["lineage"].write_text(json.dumps(metadata))
        final = pd.read_csv(self.paths["final"])
        final.loc[final["study_id"] == 2, "n_clips"] = 2
        final.to_csv(self.paths["final"], index=False)
        result = reconstruct_cohort_flow(self.inputs)
        self.assertEqual(result.invariants["status"], "ok")
        self.assertEqual(result.summary["final_study_embeddings"], 6)
        self.assertEqual(result.summary["canonical_universe_study_embeddings"], 5)

    def test_canonical_split_mismatch_blocks(self) -> None:
        canonical = json.loads(self.inputs.canonical_summaries["tapse"].read_text())
        canonical["split_counts"]["test"] = 1
        self.inputs.canonical_summaries["tapse"].write_text(json.dumps(canonical))
        with self.assertRaises(Tier1BlockedError) as context:
            reconstruct_cohort_flow(self.inputs)
        self.assertEqual(context.exception.status, BLOCKED_COHORT_CANONICAL_MISMATCH)


if __name__ == "__main__":
    unittest.main()
