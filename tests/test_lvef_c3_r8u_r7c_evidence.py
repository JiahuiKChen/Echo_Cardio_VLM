#!/usr/bin/env python3
"""Focused synthetic tests for body-free R8U-R7C evidence loading."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_r8u_r7c_accounting as accounting
import lvef_c3_r8u_r7c_evidence as evidence
import lvef_c3_r8u_r7c_metadata as metadata


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _batch_metadata() -> list[dict]:
    values: list[dict] = []
    for ordinal, batch_id in enumerate(metadata.EXPECTED_BATCH_IDS):
        studies = 30 if ordinal == 18 else 250
        no_cine = 5 if ordinal == 0 else 0
        hashes = {
            key: _hash(f"{key}-{ordinal}") for key in metadata.BATCH_HASH_KEYS
        }
        hashes["batch_finalization_receipt_sha256"] = (
            evidence.PREFIX_FINAL_RECEIPT_SHA256[ordinal]
            if ordinal < 16
            else _hash(f"tail-final-{ordinal}")
        )
        values.append(
            {
                "batch_id": batch_id,
                "ordinal": ordinal,
                "attempt_id": accounting.ATTEMPT_ID,
                "batch_plan_sha256": accounting.PLAN_SHA256,
                "scientific_commit": accounting.SCIENTIFIC_COMMIT,
                **hashes,
                "n_selected_studies": studies,
                "n_selected_subjects": studies,
                "n_source_objects": 1,
                "source_bytes": 10,
                "n_downloaded_objects": 1,
                "downloaded_bytes": 10,
                "n_readable_objects": 1,
                "readable_bytes": 10,
                "n_multiframe_candidates": 1,
                "n_successful_extractions": 1,
                "n_clip_embeddings": 1,
                "n_technical_dispositions": 0,
                "n_ordinary_preprocessing_path": 1,
                "n_spatial_fallback_preprocessing_path": 0,
                "n_temporal_fallback_preprocessing_path": 0,
                "n_spatial_temporal_fallback_preprocessing_path": 0,
                "n_study_embeddings": studies - no_cine,
                "n_prespecified_no_cine_studies": no_cine,
                "n_new_no_cine_studies": 0,
                "retired_extracted_cache_bytes": 10,
                "n_missing_selected_studies": 0,
                "n_duplicate_selected_studies": 0,
                "n_source_substitutions": 0,
                "n_unaccounted_multiframe_candidates": 0,
                "n_outcome_informed_decisions": 0,
                "final_ledger_status": "FINALIZED",
                "preservation_status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
                "cache_retirement_status": "PASS_RETIRED",
                "batch_finalization_status": "PASS_BATCH_FINALIZED",
                "raw_source_authority_retained": True,
                "extracted_cache_absent": True,
            }
        )
    return values


def _original_summary(batch_metadata: list[dict]) -> dict:
    totals = {
        key: sum(item[key] for item in batch_metadata)
        for key in metadata.BATCH_COUNT_KEYS
    }
    receipt_hashes = [
        item["batch_finalization_receipt_sha256"] for item in batch_metadata
    ]
    receipt_set_sha256 = hashlib.sha256(
        ("\n".join(sorted(receipt_hashes)) + "\n").encode("ascii")
    ).hexdigest()
    value = {key: 0 for key in finalizer.FINAL_KEYS}
    value.update(
        {
            "schema_version": 2,
            "artifact_type": "lvef_c3_production_finalization_summary_v2",
            "status": "PASS_PRODUCTION_C3_FINALIZED",
            "production_batches": 19,
            "selected_studies": totals["n_selected_studies"],
            "selected_subjects": totals["n_selected_subjects"],
            "verified_source_objects": totals["n_downloaded_objects"],
            "selected_source_bytes": totals["source_bytes"],
            "dicom_readable_objects": totals["n_readable_objects"],
            "dicom_unreadable_objects": 0,
            "multiframe_cines": totals["n_multiframe_candidates"],
            "single_frame_objects": 0,
            "extracted_clips": totals["n_successful_extractions"],
            "successfully_extracted_cines": totals["n_successful_extractions"],
            "object_technical_dispositions": totals[
                "n_technical_dispositions"
            ],
            "blocking_failures": 0,
            "studies_affected_by_technical_disposition": 0,
            "new_no_cine_studies": totals["n_new_no_cine_studies"],
            "technical_disposition_counts_by_class": {
                "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": 0
            },
            "technical_disposition_policy_version": (
                finalizer.production_stages.
                OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_set_sha256": _hash("dispositions"),
            "unique_clip_keys": totals["n_clip_embeddings"],
            "clip_embeddings": totals["n_clip_embeddings"],
            "pooled_imaging_eligible_studies": totals["n_study_embeddings"],
            "no_cine_studies": totals["n_prespecified_no_cine_studies"],
            "no_cine_disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE",
            "batch_receipt_set_sha256": receipt_set_sha256,
            "all_authority_bindings_identical": False,
            "canonical_clip_index_sha256": _hash("clip-index"),
            "canonical_clip_index_size_bytes": 1,
            "canonical_clip_index_rows": totals["n_clip_embeddings"],
            "canonical_study_embeddings_sha256": _hash("study-embeddings"),
            "canonical_study_embeddings_size_bytes": 1,
            "canonical_study_manifest_sha256": _hash("study-manifest"),
            "canonical_study_manifest_size_bytes": 1,
            "canonical_study_store_receipt_sha256": _hash("study-receipt"),
            "canonical_study_store_receipt_size_bytes": 1,
            "cohort_preservation_receipt_sha256": _hash("cohort-receipt"),
            "cohort_preservation_receipt_size_bytes": 1,
            "cohort_preserved_artifacts": 42,
            "cohort_preservation_second_pass_replay_passed": True,
            "cohort_preservation_passed": True,
        }
    )
    for key in (
        "all_batches_finalized",
        "all_source_receipts_passed",
        "all_dicom_audits_passed",
        "all_extraction_rows_resolved",
        "all_successful_extractions_embedded",
        "all_technical_dispositions_retained",
        "all_no_cine_studies_prespecified",
        "all_embeddings_passed",
        "all_pooling_passed",
        "all_preservation_manifests_passed",
        "all_aggregate_safety_gates_passed",
        "raw_dicoms_retained",
        "extracted_cache_retired",
    ):
        value[key] = True
    for key in (
        "outside_selected_studies_permitted",
        "scientific_inconsistency_repair_performed",
        "identifiers_emitted",
        "restricted_paths_emitted",
    ):
        value[key] = False
    value.update(
        {
            "all_scientific_authority_bindings_identical": True,
            "implementation_authority_epoch_count": 3,
            "r8u_implementation_commit": accounting.RUNTIME_IMPLEMENTATION_COMMIT,
            "r8u_recovery_continuation_authority_sha256": _hash("r7-authority"),
        }
    )
    finalizer.validate_closed_final_summary(value)
    return value


def _write_private_json(path: Path, value: dict) -> bytes:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return payload


def test_original_cohort_receipt_absence_is_distinct() -> None:
    batches = _batch_metadata()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "absent.aggregate_safe.json"
        with mock.patch.object(evidence, "ORIGINAL_COHORT_RECEIPT_PATH", path):
            assert evidence.load_original_cohort_finalization_receipt(batches) is None


def test_original_cohort_receipt_binds_fixed_batch_set_and_aggregates() -> None:
    batches = _batch_metadata()
    value = _original_summary(batches)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "full_c3_finalization.aggregate_safe.json"
        payload = _write_private_json(path, value)
        with mock.patch.object(evidence, "ORIGINAL_COHORT_RECEIPT_PATH", path):
            assert evidence.load_original_cohort_finalization_receipt(batches) == (
                value,
                hashlib.sha256(payload).hexdigest(),
            )
            contradictory = dict(
                value, selected_source_bytes=value["selected_source_bytes"] + 1
            )
            _write_private_json(path, contradictory)
            try:
                evidence.load_original_cohort_finalization_receipt(batches)
            except evidence.R7CEvidenceError as exc:
                assert exc.code == (
                    "R8U_R7C_ORIGINAL_COHORT_RECEIPT_CONTRADICTION"
                )
            else:
                raise AssertionError("contradictory original cohort receipt accepted")


def test_original_cohort_receipt_rejects_occupied_nonregular_path() -> None:
    batches = _batch_metadata()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "full_c3_finalization.aggregate_safe.json"
        path.mkdir()
        with mock.patch.object(evidence, "ORIGINAL_COHORT_RECEIPT_PATH", path):
            try:
                evidence.load_original_cohort_finalization_receipt(batches)
            except evidence.R7CEvidenceError as exc:
                assert exc.code == "R8U_R7C_ORIGINAL_COHORT_RECEIPT_INVALID"
            else:
                raise AssertionError("occupied nonregular cohort path accepted")


def test_valid_stage_schema_with_nonpass_status_is_proven_failure() -> None:
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_stage_completion_receipt_v1",
        "status": "FAILED_STAGE_OUTPUT",
        "stage": "DICOM_EXTRACTION",
        "batch_id": "c3_batch_016",
        "attempt_id": accounting.ATTEMPT_ID,
        "runtime_authority": {},
        "input_manifest_sha256": _hash("input"),
        "artifacts": {},
    }
    try:
        evidence._validate_stage_receipt(
            receipt,
            batch_id="c3_batch_016",
            stage="DICOM_EXTRACTION",
            required_artifacts={},
        )
    except evidence.R7CEvidenceError as exc:
        assert exc.code == "R8U_R7C_STAGE_RECEIPT_PROVEN_FAILURE"
    else:
        raise AssertionError("nonpass canonical stage was not a proven failure")


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
