#!/usr/bin/env python3
"""Focused synthetic tests for body-free R8U-R7G evidence loading."""
from __future__ import annotations

import copy
import hashlib
import inspect
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
import lvef_c3_r8u_r7g_evidence as evidence
import lvef_c3_r8u_r7g_metadata as metadata


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _write_private_json(path: Path, value: dict) -> bytes:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return payload


def _summary_and_receipt(*, technical: int) -> tuple[dict, dict, dict]:
    successful = 9
    multiframe = successful + technical
    class_counts = {
        finalizer.production_stages.OBJECT_TECHNICAL_DISPOSITION: technical
    }
    receipt = {
        "n_expected_objects": 20,
        "n_dicom_readable": 20,
        "n_dicom_unreadable": 0,
        "n_multiframe_cines": multiframe,
        "n_single_frame_objects": 20 - multiframe,
        "n_extracted_clips": successful,
        "n_successfully_extracted_cines": successful,
        "n_object_technical_dispositions": technical,
        "n_blocking_failures": 0,
        "n_studies_affected_by_technical_disposition": technical,
        "n_new_no_cine_studies": 0,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "technical_disposition_counts_by_class": class_counts,
        "technical_disposition_policy_version": (
            finalizer.production_stages.
            OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "technical_disposition_manifest_sha256": _hash("disposition"),
    }
    value = {
        key: None
        for key in finalizer.production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
    }
    value.update(
        {
            "schema_version": 2,
            "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v2",
            "status": (
                "PASS_EXTRACTION_WITH_OBJECT_TECHNICAL_DISPOSITIONS"
                if technical
                else "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
            ),
            "n_objects": 20,
            "n_studies": 10,
            "n_readable": 20,
            "n_unreadable": 0,
            "n_multiframe_candidates": multiframe,
            "n_single_frame": 20 - multiframe,
            "n_pixel_decode_failures": 0,
            "physical_source_keys_unique": True,
            "n_extracted_clips": successful,
            "n_studies_with_extracted_clips": 10,
            "clip_keys_unique": True,
            "all_shapes_and_dtypes_valid": True,
            "all_pixel_decodes_passed": True,
            "n_ordinary_preprocessing_path": successful,
            "n_spatial_fallback_preprocessing_path": 0,
            "n_temporal_fallback_preprocessing_path": 0,
            "n_spatial_temporal_fallback_preprocessing_path": 0,
            "n_fallback_path_pass": 0,
            "n_fallback_path_failed": 0,
            "all_fallback_encoder_visible_signal_gates_passed": True,
            "n_requested_cines": multiframe,
            "n_successfully_extracted_cines": successful,
            "n_successfully_extracted_clips": successful,
            "n_object_technical_dispositions": technical,
            "n_blocking_failures": 0,
            "n_studies_affected_by_technical_disposition": technical,
            "n_new_no_cine_studies": 0,
            "technical_disposition_counts_by_class": class_counts,
            "all_extraction_rows_resolved": True,
            "all_successful_extractions_embeddable": True,
            "all_technical_dispositions_retained": True,
            "object_substitution_count": 0,
            "unaccounted_multiframe_objects": 0,
            "all_failure_substages_none": not bool(technical),
            "all_source_signal_gates_passed": not bool(technical),
            "all_post_crop_signal_gates_passed": not bool(technical),
            "all_sampled_signal_gates_passed": not bool(technical),
            "successful_extraction_gate_scope": "SUCCESSFUL_ROWS_ONLY",
            "requested_cine_resolution_scope": (
                "SUCCESS_OR_APPROVED_TECHNICAL_DISPOSITION"
            ),
            "technical_disposition_policy_version": receipt[
                "technical_disposition_policy_version"
            ],
            "technical_disposition_manifest_sha256": receipt[
                "technical_disposition_manifest_sha256"
            ],
            "identifiers_emitted": False,
            "paths_emitted": False,
        }
    )
    return value, receipt, {"n_studies": 10}


def test_approved_disposition_allows_historical_false_signal_flags() -> None:
    summary, receipt, planned = _summary_and_receipt(technical=1)
    evidence._validate_extraction_summary(
        summary, receipt=receipt, planned=planned
    )
    assert summary["all_failure_substages_none"] is False
    assert summary["all_source_signal_gates_passed"] is False
    assert summary["all_post_crop_signal_gates_passed"] is False
    assert summary["all_sampled_signal_gates_passed"] is False


def test_false_signal_flag_without_disposition_is_rejected() -> None:
    summary, receipt, planned = _summary_and_receipt(technical=0)
    summary["all_source_signal_gates_passed"] = False
    try:
        evidence._validate_extraction_summary(
            summary, receipt=receipt, planned=planned
        )
    except evidence.R7GEvidenceError as exc:
        assert exc.code == "R8U_R7G_EXTRACTION_SUMMARY_RECONCILIATION_INVALID"
    else:
        raise AssertionError("unexplained false signal gate accepted")


def test_disposition_accounting_and_blocking_failure_are_closed() -> None:
    summary, receipt, planned = _summary_and_receipt(technical=1)
    summary["n_blocking_failures"] = 1
    receipt["n_blocking_failures"] = 1
    try:
        evidence._validate_extraction_summary(
            summary, receipt=receipt, planned=planned
        )
    except evidence.R7GEvidenceError as exc:
        assert exc.code == "R8U_R7G_EXTRACTION_SUMMARY_RECONCILIATION_INVALID"
    else:
        raise AssertionError("blocking extraction failure accepted")


def _batch_metadata() -> list[dict]:
    object_counts = [18_500] * 15 + [18_877, 18_606, 18_658, 2_343]
    source_bytes = (
        [66_000_000_000] * 15
        + [81_366_146_696, 66_807_894_336, 68_754_613_138, 9_640_479_152]
    )
    values: list[dict] = []
    for ordinal, batch_id in enumerate(metadata.EXPECTED_BATCH_IDS):
        studies = 30 if ordinal == 18 else 250
        no_cine = 5 if ordinal == 0 else 0
        multiframe = 100 if ordinal == 18 else 1_000
        technical = 1 if ordinal == 2 else 0
        successful = multiframe - technical
        hashes = {
            key: _hash(f"{key}-{ordinal}")
            for key in metadata.BATCH_HASH_KEYS
        }
        if ordinal < 16:
            hashes["batch_finalization_receipt_sha256"] = (
                evidence.PREFIX_FINAL_RECEIPT_SHA256[ordinal]
            )
        values.append(
            {
                "batch_id": batch_id,
                "ordinal": ordinal,
                "attempt_id": metadata.ATTEMPT_ID,
                "batch_plan_sha256": metadata.PLAN_SHA256,
                "scientific_commit": metadata.SCIENTIFIC_COMMIT,
                **hashes,
                "n_selected_studies": studies,
                "n_selected_subjects": studies,
                "n_source_objects": object_counts[ordinal],
                "source_bytes": source_bytes[ordinal],
                "n_downloaded_objects": object_counts[ordinal],
                "downloaded_bytes": source_bytes[ordinal],
                "n_readable_objects": object_counts[ordinal],
                "n_unreadable_objects": 0,
                "readable_bytes": source_bytes[ordinal],
                "n_multiframe_candidates": multiframe,
                "n_single_frame_objects": object_counts[ordinal] - multiframe,
                "n_successful_extractions": successful,
                "n_clip_embeddings": successful,
                "n_technical_dispositions": technical,
                "n_blocking_failures": 0,
                "n_ordinary_preprocessing_path": successful,
                "n_spatial_fallback_preprocessing_path": 0,
                "n_temporal_fallback_preprocessing_path": 0,
                "n_spatial_temporal_fallback_preprocessing_path": 0,
                "n_study_embeddings": studies - no_cine,
                "n_prespecified_no_cine_studies": no_cine,
                "n_new_no_cine_studies": 0,
                "retired_extracted_cache_bytes": successful * 4_096,
                "n_missing_selected_studies": 0,
                "n_duplicate_selected_studies": 0,
                "n_source_substitutions": 0,
                "n_unaccounted_multiframe_candidates": 0,
                "n_outcome_informed_decisions": 0,
                "final_ledger_status": "FINALIZED",
                "preservation_status": (
                    "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
                ),
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
    receipt_set_sha = _hash("placeholder")
    hashes = [
        item["batch_finalization_receipt_sha256"] for item in batch_metadata
    ]
    receipt_set_sha = hashlib.sha256(
        ("\n".join(sorted(hashes)) + "\n").encode("ascii")
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
            "dicom_unreadable_objects": totals["n_unreadable_objects"],
            "multiframe_cines": totals["n_multiframe_candidates"],
            "single_frame_objects": totals["n_single_frame_objects"],
            "extracted_clips": totals["n_successful_extractions"],
            "successfully_extracted_cines": totals[
                "n_successful_extractions"
            ],
            "object_technical_dispositions": totals[
                "n_technical_dispositions"
            ],
            "blocking_failures": 0,
            "studies_affected_by_technical_disposition": 1,
            "new_no_cine_studies": 0,
            "technical_disposition_counts_by_class": {
                finalizer.production_stages.OBJECT_TECHNICAL_DISPOSITION: 1
            },
            "technical_disposition_policy_version": (
                finalizer.production_stages.
                OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_set_sha256": _hash(
                "disposition-set"
            ),
            "unique_clip_keys": totals["n_clip_embeddings"],
            "clip_embeddings": totals["n_clip_embeddings"],
            "pooled_imaging_eligible_studies": totals["n_study_embeddings"],
            "no_cine_studies": totals["n_prespecified_no_cine_studies"],
            "no_cine_disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE",
            "batch_receipt_set_sha256": receipt_set_sha,
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
            "implementation_authority_epoch_count": 4,
            "r8u_r7d_implementation_commit": (
                metadata.R7F_RUNTIME_IMPLEMENTATION_COMMIT
            ),
            "r8u_r7d_continuation_authority_sha256": _hash(
                "r7f-authority"
            ),
        }
    )
    finalizer.validate_closed_final_summary(value)
    return value


def test_original_r7f_cohort_receipt_absence_is_distinct() -> None:
    batches = _batch_metadata()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "absent.aggregate_safe.json"
        with mock.patch.object(evidence, "ORIGINAL_COHORT_RECEIPT_PATH", path):
            assert evidence.load_original_cohort_finalization_receipt(batches) is None


def test_original_r7f_cohort_receipt_validates_r7d_epoch_and_totals() -> None:
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
            wrong = copy.deepcopy(value)
            wrong["r8u_r7d_implementation_commit"] = "8" * 40
            _write_private_json(path, wrong)
            try:
                evidence.load_original_cohort_finalization_receipt(batches)
            except evidence.R7GEvidenceError as exc:
                assert exc.code == (
                    "R8U_R7G_ORIGINAL_COHORT_RECEIPT_CONTRADICTION"
                )
            else:
                raise AssertionError("wrong R7F runtime epoch accepted")


def test_fixed_plan_requires_exact_canonical_and_byte_hash() -> None:
    plan = {
        "schema_version": 3,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v3",
        "cohort": {},
        "batches": [{} for _ in range(19)],
    }
    plan_sha = metadata.canonical_json_sha256(plan)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "full_batch_plan.restricted.json"
        _write_private_json(path, plan)
        with (
            mock.patch.object(evidence, "PLAN_PATH", path),
            mock.patch.object(metadata, "PLAN_SHA256", plan_sha),
        ):
            assert evidence.load_fixed_plan() == plan


def test_failed_batch16_partial_remains_delegated_to_r7a_binding() -> None:
    topology = {
        "active_finalized_extraction_caches": 0,
        "batch16_failed_partial_cache_retained": True,
        "batch16_failed_partial_cache_outside_active_topology": True,
        "batch16_failed_partial_cache_adopted": False,
        "batch16_failed_partial_cache_deleted": False,
        "batch16_failed_partial_cache_overwritten": False,
        "batch16_failed_partial_seal_sha256": _hash("seal"),
        "batch16_failed_partial_metadata_projection_sha256": _hash("tree"),
    }
    historical = mock.Mock(return_value=topology)
    with mock.patch.object(evidence.r7c, "fixed_cache_topology", historical):
        assert evidence.fixed_cache_topology() == topology
    historical.assert_called_once_with()
    assert "historical validator" in inspect.getsource(
        evidence.fixed_cache_topology
    )


def test_evidence_surface_has_no_scientific_body_reader_or_execution() -> None:
    source = inspect.getsource(evidence)
    assert "numpy" not in source
    assert "np.load" not in source
    assert "finalize_receipts" not in source
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "read_bytes" not in source
    assert "write_bytes" not in source
    assert "run_production_dicom_extraction" not in source
    assert "run_production_echoprime" not in source


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
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
