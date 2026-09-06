#!/usr/bin/env python3
"""Metadata-only R8U-R7C cohort reconciliation and lock receipts.

This module deliberately accepts already-adjudicated metadata projections.  It
does not import the scientific worker, open DICOM/NPZ/embedding bodies, submit
jobs, or construct model-ready cohort artifacts.  The caller is responsible
for deriving each :data:`BATCH_METADATA_KEYS` projection from the immutable
plan and the sealed batch receipt/summary chain.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
TIMESTAMP_RE = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]+)?Z$"
)
ID_RE = re.compile(r"^[1-9][0-9]*$")

EXPECTED_BATCH_IDS = tuple(f"c3_batch_{ordinal:03d}" for ordinal in range(19))
EXPECTED_FULL_BATCH_STUDIES = 250
EXPECTED_FINAL_BATCH_STUDIES = 30
EXPECTED_SELECTED_STUDIES = 4_530

ACCOUNTING_ROLES = ("task_17", "task_18", "task_19", "finalizer")

BATCH_COUNT_KEYS = frozenset(
    {
        "n_selected_studies",
        "n_selected_subjects",
        "n_source_objects",
        "source_bytes",
        "n_downloaded_objects",
        "downloaded_bytes",
        "n_readable_objects",
        "readable_bytes",
        "n_multiframe_candidates",
        "n_successful_extractions",
        "n_clip_embeddings",
        "n_technical_dispositions",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "n_study_embeddings",
        "n_prespecified_no_cine_studies",
        "n_new_no_cine_studies",
        "retired_extracted_cache_bytes",
        "n_missing_selected_studies",
        "n_duplicate_selected_studies",
        "n_source_substitutions",
        "n_unaccounted_multiframe_candidates",
        "n_outcome_informed_decisions",
    }
)

BATCH_HASH_KEYS = frozenset(
    {
        "batch_finalization_receipt_sha256",
        "preservation_receipt_sha256",
        "preservation_manifest_sha256",
        "extraction_stage_completion_receipt_sha256",
        "extraction_summary_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "retired_cache_tree_sha256",
        "study_membership_sha256",
        "prespecified_no_cine_study_set_sha256",
    }
)

BATCH_METADATA_KEYS = frozenset(
    {
        "batch_id",
        "ordinal",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        *BATCH_HASH_KEYS,
        *BATCH_COUNT_KEYS,
        "final_ledger_status",
        "preservation_status",
        "cache_retirement_status",
        "batch_finalization_status",
        "raw_source_authority_retained",
        "extracted_cache_absent",
    }
)

AGGREGATE_KEYS = frozenset({"finalized_batches", *BATCH_COUNT_KEYS})

ZERO_SCIENTIFIC_ACTION_KEYS = frozenset(
    {
        "scientific_qsub_submissions",
        "job_mutations",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "embedding_body_reads",
        "dicom_extraction_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accesses",
    }
)

CACHE_TOPOLOGY_KEYS = frozenset(
    {
        "active_finalized_extraction_caches",
        "batch16_failed_partial_cache_retained",
        "batch16_failed_partial_cache_outside_active_topology",
        "batch16_failed_partial_cache_adopted",
        "batch16_failed_partial_cache_deleted",
        "batch16_failed_partial_cache_overwritten",
        "batch16_failed_partial_seal_sha256",
        "batch16_failed_partial_metadata_projection_sha256",
    }
)

STUDY_PARTITION_KEYS = frozenset(
    {
        "batch_count",
        "selected_studies",
        "selected_subjects",
        "missing_batches",
        "duplicate_batches",
        "missing_studies",
        "duplicate_studies",
        "duplicate_subjects",
        "exact_plan_equality",
        "ordered_study_partition_sha256",
        "selected_study_set_sha256",
    }
)

ORDERED_RECEIPT_KEYS = frozenset({"batch_id", "sha256"})

COHORT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "finalization_mode",
        "created_at_utc",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "adjudication_implementation_commit",
        "batch16_final_receipt_sha256",
        "continuation_receipt_sha256",
        "continuation_array_job_id",
        "continuation_array_task_range",
        "continuation_array_max_concurrency",
        "cohort_finalizer_job_id",
        "accounting_receipt_sha256",
        "ordered_batch_finalization_receipts",
        "batch_metadata_projection_sha256",
        "study_partition",
        "aggregate_totals",
        "finalized_ledgers",
        "preservation_transitions_passed",
        "cache_retirement_transitions_passed",
        "batch_finalizations_passed",
        "raw_source_authority_retained",
        "cache_topology",
        "prohibited_actions",
    }
)

REPOSITORY_STATE_KEYS = frozenset(
    {
        "branch",
        "local_head",
        "origin_head",
        "scc_head",
        "local_tracked_clean",
        "scc_tracked_clean",
    }
)

SCHEDULER_STATE_KEYS = frozenset(
    {
        "matching_active_jobs",
        "matching_active_processes",
        "qstat_projection_sha256",
        "process_projection_sha256",
    }
)

LOCK_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "created_at_utc",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "adjudication_implementation_commit",
        "batch16_final_receipt_sha256",
        "continuation_receipt_sha256",
        "accounting_receipt_sha256",
        "ordered_batch_finalization_receipts",
        "cohort_finalization_receipt_sha256",
        "cohort_finalization_mode",
        "study_partition",
        "aggregate_totals",
        "cache_topology",
        "repository_state",
        "scheduler_state",
        "prohibited_actions",
        "lock_review_count",
        "all_batch_ledgers_finalized",
        "all_preservation_transitions_valid",
        "all_cache_retirement_transitions_valid",
        "all_batch_finalization_receipts_valid",
        "exact_4530_study_partition",
        "exact_aggregate_reconciliation",
        "raw_source_authority_retained",
        "no_source_substitution",
        "no_outcome_informed_decision",
    }
)


class R7CMetadataError(RuntimeError):
    """One sanitized fail-closed metadata-validation error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise R7CMetadataError(code)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise R7CMetadataError("CANONICAL_JSON_INVALID") from exc


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_typed_equal(observed: object, expected: object) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _exact_typed_equal(observed[key], expected[key])
            for key in expected
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _exact_typed_equal(left, right)
            for left, right in zip(observed, expected, strict=True)
        )
    return observed == expected


def _require_hash(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_commit(value: object, code: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(code)
    return value


def _require_timestamp(value: object, code: str) -> str:
    if not isinstance(value, str) or TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise R7CMetadataError(code) from exc
    return value


def _require_attempt(value: object) -> str:
    if not isinstance(value, str) or ATTEMPT_RE.fullmatch(value) is None:
        _fail("ATTEMPT_ID_INVALID")
    return value


def validate_zero_scientific_actions(value: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != ZERO_SCIENTIFIC_ACTION_KEYS:
        _fail("ZERO_SCIENTIFIC_ACTION_SCHEMA_INVALID")
    result: dict[str, int] = {}
    for key in sorted(ZERO_SCIENTIFIC_ACTION_KEYS):
        observed = _require_nonnegative_int(
            value.get(key), "ZERO_SCIENTIFIC_ACTION_VALUE_INVALID"
        )
        if observed != 0:
            _fail("PROHIBITED_SCIENTIFIC_ACTION_OBSERVED")
        result[key] = observed
    return result


def zero_scientific_actions() -> dict[str, int]:
    return {key: 0 for key in sorted(ZERO_SCIENTIFIC_ACTION_KEYS)}


def validate_cache_topology(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != CACHE_TOPOLOGY_KEYS:
        _fail("CACHE_TOPOLOGY_SCHEMA_INVALID")
    expected_booleans = {
        "batch16_failed_partial_cache_retained": True,
        "batch16_failed_partial_cache_outside_active_topology": True,
        "batch16_failed_partial_cache_adopted": False,
        "batch16_failed_partial_cache_deleted": False,
        "batch16_failed_partial_cache_overwritten": False,
    }
    if (
        _require_nonnegative_int(
            value.get("active_finalized_extraction_caches"),
            "CACHE_TOPOLOGY_COUNT_INVALID",
        )
        != 0
        or any(value.get(key) is not expected for key, expected in expected_booleans.items())
    ):
        _fail("CACHE_TOPOLOGY_CONTRADICTION")
    for key in (
        "batch16_failed_partial_seal_sha256",
        "batch16_failed_partial_metadata_projection_sha256",
    ):
        _require_hash(value.get(key), "CACHE_TOPOLOGY_HASH_INVALID")
    return dict(value)


def _validate_plan_partition(
    plan: Mapping[str, Any], *, expected_plan_sha256: str
) -> tuple[dict[str, Any], Mapping[str, Mapping[str, Any]]]:
    _require_hash(expected_plan_sha256, "BATCH_PLAN_SHA256_INVALID")
    if canonical_json_sha256(plan) != expected_plan_sha256:
        _fail("BATCH_PLAN_SHA256_MISMATCH")
    if (
        not isinstance(plan, Mapping)
        or plan.get("schema_version") != 3
        or plan.get("artifact_type")
        != "lvef_c3_restricted_immutable_batch_plan_v3"
        or not isinstance(plan.get("cohort"), Mapping)
        or not isinstance(plan.get("batches"), list)
        or len(plan["batches"]) != 19
    ):
        _fail("BATCH_PLAN_SCHEMA_INVALID")

    cohort = plan["cohort"]
    if (
        cohort.get("selected_studies") != EXPECTED_SELECTED_STUDIES
        or cohort.get("selected_subjects") != EXPECTED_SELECTED_STUDIES
    ):
        _fail("BATCH_PLAN_COHORT_COUNT_INVALID")

    by_batch: dict[str, Mapping[str, Any]] = {}
    ordered_partition: list[dict[str, str]] = []
    selected_set: list[dict[str, str]] = []
    seen_studies: set[str] = set()
    seen_subjects: set[str] = set()
    for ordinal, raw_batch in enumerate(plan["batches"]):
        if not isinstance(raw_batch, Mapping):
            _fail("BATCH_PLAN_BATCH_INVALID")
        batch_id = EXPECTED_BATCH_IDS[ordinal]
        expected_studies = (
            EXPECTED_FINAL_BATCH_STUDIES
            if ordinal == 18
            else EXPECTED_FULL_BATCH_STUDIES
        )
        studies = raw_batch.get("studies")
        if (
            raw_batch.get("batch_id") != batch_id
            or raw_batch.get("ordinal") != ordinal
            or raw_batch.get("n_studies") != expected_studies
            or raw_batch.get("n_subjects") != expected_studies
            or not isinstance(studies, list)
            or len(studies) != expected_studies
            or raw_batch.get("study_membership_sha256")
            != canonical_json_sha256(studies)
        ):
            _fail("BATCH_PLAN_BATCH_COUNT_OR_MEMBERSHIP_INVALID")
        _require_nonnegative_int(raw_batch.get("n_objects"), "BATCH_PLAN_SOURCE_COUNT_INVALID")
        _require_nonnegative_int(raw_batch.get("source_bytes"), "BATCH_PLAN_SOURCE_BYTES_INVALID")
        expected_no_cine = _require_nonnegative_int(
            raw_batch.get("expected_no_cine_studies"),
            "BATCH_PLAN_NO_CINE_COUNT_INVALID",
        )
        if expected_no_cine > expected_studies:
            _fail("BATCH_PLAN_NO_CINE_COUNT_INVALID")
        _require_hash(
            raw_batch.get("prespecified_no_cine_study_set_sha256"),
            "BATCH_PLAN_NO_CINE_HASH_INVALID",
        )
        for row in studies:
            if not isinstance(row, Mapping) or set(row) != {
                "subject_id", "study_id", "split"
            }:
                _fail("BATCH_PLAN_STUDY_SCHEMA_INVALID")
            subject = row.get("subject_id")
            study = row.get("study_id")
            if (
                not isinstance(subject, str)
                or ID_RE.fullmatch(subject) is None
                or not isinstance(study, str)
                or ID_RE.fullmatch(study) is None
                or row.get("split") not in {"train", "val", "test"}
            ):
                _fail("BATCH_PLAN_STUDY_VALUE_INVALID")
            if study in seen_studies:
                _fail("DUPLICATE_STUDY_ACROSS_BATCHES")
            if subject in seen_subjects:
                _fail("DUPLICATE_SUBJECT_ACROSS_BATCHES")
            seen_studies.add(study)
            seen_subjects.add(subject)
            selected = {"subject_id": subject, "study_id": study}
            selected_set.append(selected)
            ordered_partition.append({"batch_id": batch_id, **selected})
        by_batch[batch_id] = raw_batch

    if len(seen_studies) != EXPECTED_SELECTED_STUDIES or len(seen_subjects) != EXPECTED_SELECTED_STUDIES:
        _fail("BATCH_PLAN_STUDY_PARTITION_INCOMPLETE")
    selected_set.sort(key=lambda row: (int(row["subject_id"]), int(row["study_id"])))
    partition = {
        "batch_count": 19,
        "selected_studies": len(seen_studies),
        "selected_subjects": len(seen_subjects),
        "missing_batches": 0,
        "duplicate_batches": 0,
        "missing_studies": 0,
        "duplicate_studies": 0,
        "duplicate_subjects": 0,
        "exact_plan_equality": True,
        "ordered_study_partition_sha256": canonical_json_sha256(ordered_partition),
        "selected_study_set_sha256": canonical_json_sha256(selected_set),
    }
    return partition, by_batch


def validate_batch_metadata_partition(
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    *,
    expected_plan_sha256: str,
    expected_attempt_id: str,
    expected_scientific_commit: str,
) -> dict[str, Any]:
    """Validate and aggregate 19 body-free sealed batch projections."""

    _require_attempt(expected_attempt_id)
    _require_commit(expected_scientific_commit, "SCIENTIFIC_COMMIT_INVALID")
    partition, planned = _validate_plan_partition(
        plan, expected_plan_sha256=expected_plan_sha256
    )
    if not isinstance(batch_metadata, Sequence) or isinstance(batch_metadata, (str, bytes)):
        _fail("BATCH_METADATA_SEQUENCE_INVALID")
    if len(batch_metadata) != 19:
        _fail("MISSING_OR_ADDITIONAL_BATCH_METADATA")

    totals = {key: 0 for key in BATCH_COUNT_KEYS}
    ordered_receipts: list[dict[str, str]] = []
    normalized: list[dict[str, Any]] = []
    seen_final_receipts: set[str] = set()
    for ordinal, item in enumerate(batch_metadata):
        if not isinstance(item, Mapping) or set(item) != BATCH_METADATA_KEYS:
            _fail("BATCH_METADATA_SCHEMA_INVALID")
        batch_id = EXPECTED_BATCH_IDS[ordinal]
        plan_batch = planned[batch_id]
        if (
            item.get("batch_id") != batch_id
            or item.get("ordinal") != ordinal
            or item.get("attempt_id") != expected_attempt_id
            or item.get("batch_plan_sha256") != expected_plan_sha256
            or item.get("scientific_commit") != expected_scientific_commit
        ):
            _fail("BATCH_METADATA_AUTHORITY_MISMATCH")
        for key in BATCH_HASH_KEYS:
            _require_hash(item.get(key), "BATCH_METADATA_HASH_INVALID")
        final_receipt_sha256 = str(item["batch_finalization_receipt_sha256"])
        if final_receipt_sha256 in seen_final_receipts:
            _fail("DUPLICATE_BATCH_FINALIZATION_RECEIPT")
        seen_final_receipts.add(final_receipt_sha256)
        expected_studies = EXPECTED_FINAL_BATCH_STUDIES if ordinal == 18 else EXPECTED_FULL_BATCH_STUDIES
        if (
            item.get("study_membership_sha256")
            != plan_batch.get("study_membership_sha256")
            or item.get("prespecified_no_cine_study_set_sha256")
            != plan_batch.get("prespecified_no_cine_study_set_sha256")
            or item.get("n_selected_studies") != expected_studies
            or item.get("n_selected_studies") != plan_batch.get("n_studies")
            or item.get("n_selected_subjects") != plan_batch.get("n_subjects")
            or item.get("n_source_objects") != plan_batch.get("n_objects")
            or item.get("source_bytes") != plan_batch.get("source_bytes")
            or item.get("n_prespecified_no_cine_studies")
            != plan_batch.get("expected_no_cine_studies")
        ):
            _fail("BATCH_METADATA_PLAN_MISMATCH")
        for key in BATCH_COUNT_KEYS:
            totals[key] += _require_nonnegative_int(
                item.get(key), "BATCH_METADATA_COUNT_INVALID"
            )
        if (
            item.get("final_ledger_status") != "FINALIZED"
            or item.get("preservation_status")
            != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
            or item.get("cache_retirement_status") != "PASS_RETIRED"
            or item.get("batch_finalization_status") != "PASS_BATCH_FINALIZED"
            or item.get("raw_source_authority_retained") is not True
            or item.get("extracted_cache_absent") is not True
        ):
            _fail("BATCH_METADATA_FINALIZATION_INVALID")
        if (
            item["n_downloaded_objects"] != item["n_source_objects"]
            or item["downloaded_bytes"] != item["source_bytes"]
            or item["n_readable_objects"] != item["n_source_objects"]
            or item["readable_bytes"] != item["source_bytes"]
        ):
            _fail("BATCH_SOURCE_DOWNLOAD_READABILITY_MISMATCH")
        if (
            item["n_multiframe_candidates"]
            != item["n_successful_extractions"] + item["n_technical_dispositions"]
            or item["n_successful_extractions"] != item["n_clip_embeddings"]
        ):
            _fail("BATCH_CLIP_ACCOUNTING_MISMATCH")
        if (
            item["n_ordinary_preprocessing_path"]
            + item["n_spatial_fallback_preprocessing_path"]
            + item["n_temporal_fallback_preprocessing_path"]
            + item["n_spatial_temporal_fallback_preprocessing_path"]
            != item["n_successful_extractions"]
        ):
            _fail("BATCH_PREPROCESSING_PATH_ACCOUNTING_MISMATCH")
        if (
            item["n_study_embeddings"] + item["n_prespecified_no_cine_studies"]
            != item["n_selected_studies"]
            or item["n_new_no_cine_studies"] != 0
        ):
            _fail("BATCH_STUDY_ACCOUNTING_MISMATCH")
        for key in (
            "n_missing_selected_studies",
            "n_duplicate_selected_studies",
            "n_source_substitutions",
            "n_unaccounted_multiframe_candidates",
            "n_outcome_informed_decisions",
        ):
            if item[key] != 0:
                _fail("BATCH_SCIENTIFIC_CONTRADICTION")
        normalized_item = dict(item)
        normalized.append(normalized_item)
        ordered_receipts.append(
            {
                "batch_id": batch_id,
                "sha256": final_receipt_sha256,
            }
        )

    totals_with_batches = {"finalized_batches": 19, **totals}
    cohort = plan["cohort"]
    required_totals = {
        "n_selected_studies": EXPECTED_SELECTED_STUDIES,
        "n_selected_subjects": EXPECTED_SELECTED_STUDIES,
        "n_source_objects": cohort.get("normalized_source_objects"),
        "source_bytes": cohort.get("selected_source_bytes"),
        "n_downloaded_objects": cohort.get("normalized_source_objects"),
        "downloaded_bytes": cohort.get("selected_source_bytes"),
        "n_readable_objects": cohort.get("normalized_source_objects"),
        "readable_bytes": cohort.get("selected_source_bytes"),
        "n_prespecified_no_cine_studies": cohort.get("expected_no_cine_studies"),
        "n_new_no_cine_studies": 0,
    }
    if any(totals.get(key) != expected for key, expected in required_totals.items()):
        _fail("FULL_COHORT_AGGREGATE_MISMATCH")
    if (
        totals["n_multiframe_candidates"]
        != totals["n_successful_extractions"] + totals["n_technical_dispositions"]
        or totals["n_successful_extractions"] != totals["n_clip_embeddings"]
        or totals["n_study_embeddings"] + totals["n_prespecified_no_cine_studies"]
        != EXPECTED_SELECTED_STUDIES
    ):
        _fail("FULL_COHORT_AGGREGATE_MISMATCH")
    if set(totals_with_batches) != AGGREGATE_KEYS:
        _fail("FULL_COHORT_AGGREGATE_SCHEMA_INVALID")
    return {
        "ordered_batch_finalization_receipts": ordered_receipts,
        "batch_metadata_projection_sha256": canonical_json_sha256(normalized),
        "study_partition": partition,
        "aggregate_totals": totals_with_batches,
    }


def _validate_accounting_hashes(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(ACCOUNTING_ROLES):
        _fail("ACCOUNTING_RECEIPT_HASH_SCHEMA_INVALID")
    result: dict[str, str] = {}
    for role in ACCOUNTING_ROLES:
        result[role] = _require_hash(
            value.get(role), "ACCOUNTING_RECEIPT_HASH_INVALID"
        )
    return result


def _validate_ordered_batch_receipts(
    value: object,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 19:
        _fail("ORDERED_BATCH_RECEIPT_SEQUENCE_INVALID")
    result: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    for ordinal, row in enumerate(value):
        if not isinstance(row, Mapping) or set(row) != ORDERED_RECEIPT_KEYS:
            _fail("ORDERED_BATCH_RECEIPT_SCHEMA_INVALID")
        digest = _require_hash(
            row.get("sha256"), "ORDERED_BATCH_RECEIPT_HASH_INVALID"
        )
        if row.get("batch_id") != EXPECTED_BATCH_IDS[ordinal]:
            _fail("ORDERED_BATCH_RECEIPT_IDENTITY_INVALID")
        if digest in seen_hashes:
            _fail("DUPLICATE_BATCH_FINALIZATION_RECEIPT")
        seen_hashes.add(digest)
        result.append({"batch_id": EXPECTED_BATCH_IDS[ordinal], "sha256": digest})
    return result


def _validate_study_partition_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != STUDY_PARTITION_KEYS:
        _fail("STUDY_PARTITION_RECEIPT_SCHEMA_INVALID")
    exact_counts = {
        "batch_count": 19,
        "selected_studies": EXPECTED_SELECTED_STUDIES,
        "selected_subjects": EXPECTED_SELECTED_STUDIES,
        "missing_batches": 0,
        "duplicate_batches": 0,
        "missing_studies": 0,
        "duplicate_studies": 0,
        "duplicate_subjects": 0,
    }
    for key, expected in exact_counts.items():
        if _require_nonnegative_int(
            value.get(key), "STUDY_PARTITION_RECEIPT_COUNT_INVALID"
        ) != expected:
            _fail("STUDY_PARTITION_RECEIPT_CONTRADICTION")
    if value.get("exact_plan_equality") is not True:
        _fail("STUDY_PARTITION_RECEIPT_CONTRADICTION")
    for key in (
        "ordered_study_partition_sha256",
        "selected_study_set_sha256",
    ):
        _require_hash(value.get(key), "STUDY_PARTITION_RECEIPT_HASH_INVALID")
    return dict(value)


def _validate_aggregate_totals_receipt(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        _fail("FULL_COHORT_AGGREGATE_SCHEMA_INVALID")
    result = {
        key: _require_nonnegative_int(
            value.get(key), "FULL_COHORT_AGGREGATE_VALUE_INVALID"
        )
        for key in sorted(AGGREGATE_KEYS)
    }
    exact_counts = {
        "finalized_batches": 19,
        "n_selected_studies": EXPECTED_SELECTED_STUDIES,
        "n_selected_subjects": EXPECTED_SELECTED_STUDIES,
        "n_missing_selected_studies": 0,
        "n_duplicate_selected_studies": 0,
        "n_source_substitutions": 0,
        "n_unaccounted_multiframe_candidates": 0,
        "n_outcome_informed_decisions": 0,
        "n_new_no_cine_studies": 0,
    }
    if any(result[key] != expected for key, expected in exact_counts.items()):
        _fail("FULL_COHORT_AGGREGATE_CONTRADICTION")
    if (
        result["n_downloaded_objects"] != result["n_source_objects"]
        or result["downloaded_bytes"] != result["source_bytes"]
        or result["n_readable_objects"] != result["n_source_objects"]
        or result["readable_bytes"] != result["source_bytes"]
        or result["n_multiframe_candidates"]
        != result["n_successful_extractions"]
        + result["n_technical_dispositions"]
        or result["n_successful_extractions"] != result["n_clip_embeddings"]
        or result["n_ordinary_preprocessing_path"]
        + result["n_spatial_fallback_preprocessing_path"]
        + result["n_temporal_fallback_preprocessing_path"]
        + result["n_spatial_temporal_fallback_preprocessing_path"]
        != result["n_successful_extractions"]
        or result["n_study_embeddings"]
        + result["n_prespecified_no_cine_studies"]
        != EXPECTED_SELECTED_STUDIES
    ):
        _fail("FULL_COHORT_AGGREGATE_CONTRADICTION")
    return result


def _validate_cohort_receipt_projection(value: object) -> dict[str, Any]:
    """Validate the self-contained projection needed by the lock review."""

    if not isinstance(value, Mapping) or set(value) != COHORT_RECEIPT_KEYS:
        _fail("COHORT_RECEIPT_SCHEMA_INVALID")
    if (
        value.get("schema_version") != 1
        or isinstance(value.get("schema_version"), bool)
        or value.get("artifact_type")
        != "lvef_c3_r8u_r7c_metadata_only_cohort_finalization_v1"
        or value.get("status")
        != "PASS_R8U_R7C_METADATA_ONLY_FULL_COHORT_FINALIZED"
        or value.get("finalization_mode") != "R7C_METADATA_ONLY_PUBLICATION"
        or value.get("continuation_array_job_id") != "7478863"
        or value.get("continuation_array_task_range") != "17-19"
        or value.get("continuation_array_max_concurrency") != 1
        or isinstance(value.get("continuation_array_max_concurrency"), bool)
        or value.get("cohort_finalizer_job_id") != "7478864"
    ):
        _fail("COHORT_RECEIPT_IDENTITY_INVALID")
    _require_timestamp(value.get("created_at_utc"), "CREATION_TIMESTAMP_INVALID")
    _require_attempt(value.get("attempt_id"))
    for key in (
        "batch_plan_sha256",
        "batch16_final_receipt_sha256",
        "continuation_receipt_sha256",
        "batch_metadata_projection_sha256",
    ):
        _require_hash(value.get(key), "COHORT_RECEIPT_HASH_INVALID")
    for key in (
        "scientific_commit",
        "runtime_implementation_commit",
        "adjudication_implementation_commit",
    ):
        _require_commit(value.get(key), "COHORT_RECEIPT_COMMIT_INVALID")
    if value["runtime_implementation_commit"] == value["adjudication_implementation_commit"]:
        _fail("RUNTIME_ADJUDICATION_COMMIT_CONFLATION")
    accounting = _validate_accounting_hashes(value["accounting_receipt_sha256"])
    ordered = _validate_ordered_batch_receipts(
        value["ordered_batch_finalization_receipts"]
    )
    if ordered[15]["sha256"] != value["batch16_final_receipt_sha256"]:
        _fail("BATCH16_RECEIPT_HASH_MISMATCH")
    partition = _validate_study_partition_receipt(value["study_partition"])
    aggregate = _validate_aggregate_totals_receipt(value["aggregate_totals"])
    for key in (
        "finalized_ledgers",
        "preservation_transitions_passed",
        "cache_retirement_transitions_passed",
        "batch_finalizations_passed",
    ):
        if _require_nonnegative_int(
            value.get(key), "COHORT_RECEIPT_TRANSITION_COUNT_INVALID"
        ) != 19:
            _fail("COHORT_RECEIPT_TRANSITION_COUNT_INVALID")
    if value.get("raw_source_authority_retained") is not True:
        _fail("COHORT_RAW_SOURCE_AUTHORITY_INVALID")
    topology = validate_cache_topology(value["cache_topology"])
    zero = validate_zero_scientific_actions(value["prohibited_actions"])
    return {
        **dict(value),
        "accounting_receipt_sha256": accounting,
        "ordered_batch_finalization_receipts": ordered,
        "study_partition": partition,
        "aggregate_totals": aggregate,
        "cache_topology": topology,
        "prohibited_actions": zero,
    }


def build_cohort_finalization_receipt(
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
    plan_sha256: str,
    scientific_commit: str,
    runtime_implementation_commit: str,
    adjudication_implementation_commit: str,
    batch16_final_receipt_sha256: str,
    continuation_receipt_sha256: str,
    accounting_receipt_sha256: Mapping[str, Any],
    cache_topology: Mapping[str, Any],
    prohibited_actions: Mapping[str, Any],
    created_at_utc: str,
    continuation_array_job_id: str = "7478863",
    cohort_finalizer_job_id: str = "7478864",
) -> dict[str, Any]:
    """Build a deterministic receipt from sealed metadata projections only."""

    _require_attempt(attempt_id)
    _require_hash(plan_sha256, "BATCH_PLAN_SHA256_INVALID")
    _require_commit(scientific_commit, "SCIENTIFIC_COMMIT_INVALID")
    _require_commit(runtime_implementation_commit, "RUNTIME_COMMIT_INVALID")
    _require_commit(adjudication_implementation_commit, "ADJUDICATION_COMMIT_INVALID")
    _require_hash(batch16_final_receipt_sha256, "BATCH16_RECEIPT_HASH_INVALID")
    _require_hash(continuation_receipt_sha256, "CONTINUATION_RECEIPT_HASH_INVALID")
    _require_timestamp(created_at_utc, "CREATION_TIMESTAMP_INVALID")
    if (
        continuation_array_job_id != "7478863"
        or cohort_finalizer_job_id != "7478864"
    ):
        _fail("CONTINUATION_JOB_IDENTITY_INVALID")
    reconciliation = validate_batch_metadata_partition(
        plan,
        batch_metadata,
        expected_plan_sha256=plan_sha256,
        expected_attempt_id=attempt_id,
        expected_scientific_commit=scientific_commit,
    )
    if (
        reconciliation["ordered_batch_finalization_receipts"][15]["sha256"]
        != batch16_final_receipt_sha256
    ):
        _fail("BATCH16_RECEIPT_HASH_MISMATCH")
    accounting = _validate_accounting_hashes(accounting_receipt_sha256)
    topology = validate_cache_topology(cache_topology)
    zero = validate_zero_scientific_actions(prohibited_actions)
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7c_metadata_only_cohort_finalization_v1",
        "status": "PASS_R8U_R7C_METADATA_ONLY_FULL_COHORT_FINALIZED",
        "finalization_mode": "R7C_METADATA_ONLY_PUBLICATION",
        "created_at_utc": created_at_utc,
        "attempt_id": attempt_id,
        "batch_plan_sha256": plan_sha256,
        "scientific_commit": scientific_commit,
        "runtime_implementation_commit": runtime_implementation_commit,
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "batch16_final_receipt_sha256": batch16_final_receipt_sha256,
        "continuation_receipt_sha256": continuation_receipt_sha256,
        "continuation_array_job_id": continuation_array_job_id,
        "continuation_array_task_range": "17-19",
        "continuation_array_max_concurrency": 1,
        "cohort_finalizer_job_id": cohort_finalizer_job_id,
        "accounting_receipt_sha256": accounting,
        "ordered_batch_finalization_receipts": reconciliation[
            "ordered_batch_finalization_receipts"
        ],
        "batch_metadata_projection_sha256": reconciliation[
            "batch_metadata_projection_sha256"
        ],
        "study_partition": reconciliation["study_partition"],
        "aggregate_totals": reconciliation["aggregate_totals"],
        "finalized_ledgers": 19,
        "preservation_transitions_passed": 19,
        "cache_retirement_transitions_passed": 19,
        "batch_finalizations_passed": 19,
        "raw_source_authority_retained": True,
        "cache_topology": topology,
        "prohibited_actions": zero,
    }
    if set(receipt) != COHORT_RECEIPT_KEYS:
        _fail("COHORT_RECEIPT_SCHEMA_INVALID")
    return _validate_cohort_receipt_projection(receipt)


def validate_cohort_finalization_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
    plan_sha256: str,
    scientific_commit: str,
    runtime_implementation_commit: str,
    adjudication_implementation_commit: str,
    batch16_final_receipt_sha256: str,
    continuation_receipt_sha256: str,
    accounting_receipt_sha256: Mapping[str, Any],
    cache_topology: Mapping[str, Any],
    prohibited_actions: Mapping[str, Any],
    created_at_utc: str,
) -> dict[str, Any]:
    expected = build_cohort_finalization_receipt(
        plan,
        batch_metadata,
        attempt_id=attempt_id,
        plan_sha256=plan_sha256,
        scientific_commit=scientific_commit,
        runtime_implementation_commit=runtime_implementation_commit,
        adjudication_implementation_commit=adjudication_implementation_commit,
        batch16_final_receipt_sha256=batch16_final_receipt_sha256,
        continuation_receipt_sha256=continuation_receipt_sha256,
        accounting_receipt_sha256=accounting_receipt_sha256,
        cache_topology=cache_topology,
        prohibited_actions=prohibited_actions,
        created_at_utc=created_at_utc,
    )
    if not isinstance(value, Mapping) or not _exact_typed_equal(dict(value), expected):
        _fail("COHORT_RECEIPT_MISMATCH")
    return dict(value)


def _validate_repository_state(
    value: Mapping[str, Any], *, expected_commit: str, expected_branch: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != REPOSITORY_STATE_KEYS:
        _fail("LOCK_REPOSITORY_STATE_SCHEMA_INVALID")
    if (
        not isinstance(expected_branch, str)
        or not expected_branch
        or value.get("branch") != expected_branch
        or any(value.get(key) != expected_commit for key in ("local_head", "origin_head", "scc_head"))
        or value.get("local_tracked_clean") is not True
        or value.get("scc_tracked_clean") is not True
    ):
        _fail("LOCK_REPOSITORY_STATE_CONTRADICTION")
    return dict(value)


def _validate_scheduler_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != SCHEDULER_STATE_KEYS:
        _fail("LOCK_SCHEDULER_STATE_SCHEMA_INVALID")
    if (
        _require_nonnegative_int(value.get("matching_active_jobs"), "LOCK_ACTIVE_JOB_COUNT_INVALID") != 0
        or _require_nonnegative_int(value.get("matching_active_processes"), "LOCK_ACTIVE_PROCESS_COUNT_INVALID") != 0
    ):
        _fail("LOCK_ACTIVE_EXECUTION_CONTRADICTION")
    for key in ("qstat_projection_sha256", "process_projection_sha256"):
        _require_hash(value.get(key), "LOCK_SCHEDULER_PROJECTION_HASH_INVALID")
    return dict(value)


def build_post_reconstruction_lock_receipt(
    cohort_receipt: Mapping[str, Any],
    *,
    cohort_receipt_sha256: str,
    repository_state: Mapping[str, Any],
    scheduler_state: Mapping[str, Any],
    expected_branch: str,
    created_at_utc: str,
    cohort_finalization_mode: str = "R7C_METADATA_ONLY_PUBLICATION",
) -> dict[str, Any]:
    """Build the single post-reconstruction lock from a validated cohort receipt."""

    _require_hash(cohort_receipt_sha256, "COHORT_RECEIPT_HASH_INVALID")
    _require_timestamp(created_at_utc, "LOCK_TIMESTAMP_INVALID")
    if cohort_finalization_mode not in {
        "R7C_METADATA_ONLY_PUBLICATION",
        "REUSED_EXISTING",
    }:
        _fail("LOCK_COHORT_FINALIZATION_MODE_INVALID")
    if (
        cohort_finalization_mode == "R7C_METADATA_ONLY_PUBLICATION"
        and canonical_json_sha256(cohort_receipt) != cohort_receipt_sha256
    ):
        _fail("LOCK_COHORT_RECEIPT_INVALID")
    cohort_receipt = _validate_cohort_receipt_projection(cohort_receipt)
    adjudication_commit = _require_commit(
        cohort_receipt.get("adjudication_implementation_commit"),
        "ADJUDICATION_COMMIT_INVALID",
    )
    repository = _validate_repository_state(
        repository_state,
        expected_commit=adjudication_commit,
        expected_branch=expected_branch,
    )
    scheduler = _validate_scheduler_state(scheduler_state)
    zero = validate_zero_scientific_actions(cohort_receipt["prohibited_actions"])
    topology = validate_cache_topology(cohort_receipt["cache_topology"])
    aggregate = cohort_receipt["aggregate_totals"]
    partition = cohort_receipt["study_partition"]
    if (
        not isinstance(aggregate, Mapping)
        or set(aggregate) != AGGREGATE_KEYS
        or aggregate.get("finalized_batches") != 19
        or aggregate.get("n_selected_studies") != EXPECTED_SELECTED_STUDIES
        or not isinstance(partition, Mapping)
        or set(partition) != STUDY_PARTITION_KEYS
        or partition.get("selected_studies") != EXPECTED_SELECTED_STUDIES
        or partition.get("exact_plan_equality") is not True
    ):
        _fail("LOCK_COHORT_RECONCILIATION_INVALID")
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7c_post_reconstruction_lock_v1",
        "status": "PASS_R8U_R7C_POST_RECONSTRUCTION_LOCK",
        "created_at_utc": created_at_utc,
        "attempt_id": cohort_receipt["attempt_id"],
        "batch_plan_sha256": cohort_receipt["batch_plan_sha256"],
        "scientific_commit": cohort_receipt["scientific_commit"],
        "runtime_implementation_commit": cohort_receipt["runtime_implementation_commit"],
        "adjudication_implementation_commit": adjudication_commit,
        "batch16_final_receipt_sha256": cohort_receipt[
            "batch16_final_receipt_sha256"
        ],
        "continuation_receipt_sha256": cohort_receipt[
            "continuation_receipt_sha256"
        ],
        "accounting_receipt_sha256": dict(cohort_receipt["accounting_receipt_sha256"]),
        "ordered_batch_finalization_receipts": [
            dict(row)
            for row in cohort_receipt["ordered_batch_finalization_receipts"]
        ],
        "cohort_finalization_receipt_sha256": cohort_receipt_sha256,
        "cohort_finalization_mode": cohort_finalization_mode,
        "study_partition": dict(partition),
        "aggregate_totals": dict(aggregate),
        "cache_topology": topology,
        "repository_state": repository,
        "scheduler_state": scheduler,
        "prohibited_actions": zero,
        "lock_review_count": 1,
        "all_batch_ledgers_finalized": True,
        "all_preservation_transitions_valid": True,
        "all_cache_retirement_transitions_valid": True,
        "all_batch_finalization_receipts_valid": True,
        "exact_4530_study_partition": True,
        "exact_aggregate_reconciliation": True,
        "raw_source_authority_retained": True,
        "no_source_substitution": True,
        "no_outcome_informed_decision": True,
    }
    if set(receipt) != LOCK_RECEIPT_KEYS:
        _fail("LOCK_RECEIPT_SCHEMA_INVALID")
    return receipt


def validate_post_reconstruction_lock_receipt(
    value: Mapping[str, Any],
    cohort_receipt: Mapping[str, Any],
    *,
    cohort_receipt_sha256: str,
    repository_state: Mapping[str, Any],
    scheduler_state: Mapping[str, Any],
    expected_branch: str,
    created_at_utc: str,
    cohort_finalization_mode: str = "R7C_METADATA_ONLY_PUBLICATION",
) -> dict[str, Any]:
    expected = build_post_reconstruction_lock_receipt(
        cohort_receipt,
        cohort_receipt_sha256=cohort_receipt_sha256,
        repository_state=repository_state,
        scheduler_state=scheduler_state,
        expected_branch=expected_branch,
        created_at_utc=created_at_utc,
        cohort_finalization_mode=cohort_finalization_mode,
    )
    if not isinstance(value, Mapping) or not _exact_typed_equal(dict(value), expected):
        _fail("LOCK_RECEIPT_MISMATCH")
    return dict(value)


def _strict_json(payload: bytes) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                _fail("PRIVATE_JSON_DUPLICATE_KEY")
            value[key] = item
        return value

    def reject(_value: str) -> Any:
        _fail("PRIVATE_JSON_CONSTANT_INVALID")

    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R7CMetadataError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7CMetadataError("PRIVATE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("PRIVATE_JSON_NOT_OBJECT")
    return value


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_private_json(path: Path, *, maximum_bytes: int = 8 * 1024 * 1024) -> tuple[dict[str, Any], bytes]:
    """Read one owner-0600 regular file through a stable no-follow fd."""

    descriptor = -1
    try:
        before_visible = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before_visible.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or _file_identity(before) != _file_identity(before_visible)
        ):
            _fail("PRIVATE_RECEIPT_NOT_OWNER_REGULAR_0600")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum_bytes:
                _fail("PRIVATE_RECEIPT_TOO_LARGE")
        after = os.fstat(descriptor)
        after_visible = os.lstat(path)
        if (
            total != before.st_size
            or _file_identity(before) != _file_identity(after)
            or _file_identity(before) != _file_identity(after_visible)
        ):
            _fail("PRIVATE_RECEIPT_CHANGED_DURING_READ")
        payload = b"".join(chunks)
        return _strict_json(payload), payload
    except R7CMetadataError:
        raise
    except OSError as exc:
        raise R7CMetadataError("PRIVATE_RECEIPT_NOT_OWNER_REGULAR_0600") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def publish_private_json_no_clobber(
    path: Path,
    value: Mapping[str, Any],
    *,
    validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[str, bool]:
    """Publish once, or reuse an exactly valid pre-existing receipt."""

    validator(value)
    body = canonical_json_bytes(value)
    digest = hashlib.sha256(body).hexdigest()
    if os.path.lexists(path):
        observed, payload = read_private_json(path)
        try:
            validator(observed)
        except R7CMetadataError as exc:
            raise R7CMetadataError(
                "EXISTING_RECEIPT_CONTRADICTS_EXPECTED"
            ) from exc
        if not _exact_typed_equal(observed, dict(value)) or payload != body:
            _fail("EXISTING_RECEIPT_CONTRADICTS_EXPECTED")
        return digest, False
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise R7CMetadataError("PRIVATE_RECEIPT_PARENT_INVALID") from exc
    if (
        stat.S_ISLNK(parent_info.st_mode)
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) not in {0o700, 0o2700}
    ):
        _fail("PRIVATE_RECEIPT_PARENT_INVALID")
    temporary = parent / f".{path.name}.{os.getpid()}.partial"
    if os.path.lexists(temporary):
        _fail("PRIVATE_RECEIPT_PARTIAL_COLLISION")
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        # ``open(..., 0o600)`` is still filtered through the caller's umask.
        # Establish the exact owner-private mode before the inode can become
        # visible at the canonical no-clobber path.
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as exc:
        raise R7CMetadataError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER") from exc
    except R7CMetadataError:
        raise
    except OSError as exc:
        raise R7CMetadataError("PRIVATE_RECEIPT_PUBLICATION_FAILED") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            try:
                if not temporary.is_symlink() and temporary.is_file():
                    temporary.unlink()
            except OSError:
                pass
    observed, payload = read_private_json(path)
    validator(observed)
    if payload != body or not _exact_typed_equal(observed, dict(value)):
        _fail("PUBLISHED_RECEIPT_REOPEN_MISMATCH")
    return digest, True


def publish_cohort_finalization_receipt(
    path: Path,
    value: Mapping[str, Any],
    *,
    validation_kwargs: Mapping[str, Any],
) -> tuple[str, bool]:
    return publish_private_json_no_clobber(
        path,
        value,
        validator=lambda observed: validate_cohort_finalization_receipt(
            observed, **validation_kwargs
        ),
    )


def publish_post_reconstruction_lock_receipt(
    path: Path,
    value: Mapping[str, Any],
    *,
    validation_kwargs: Mapping[str, Any],
) -> tuple[str, bool]:
    return publish_private_json_no_clobber(
        path,
        value,
        validator=lambda observed: validate_post_reconstruction_lock_receipt(
            observed, **validation_kwargs
        ),
    )


__all__ = [
    "ACCOUNTING_ROLES",
    "AGGREGATE_KEYS",
    "BATCH_METADATA_KEYS",
    "CACHE_TOPOLOGY_KEYS",
    "COHORT_RECEIPT_KEYS",
    "EXPECTED_BATCH_IDS",
    "LOCK_RECEIPT_KEYS",
    "R7CMetadataError",
    "ZERO_SCIENTIFIC_ACTION_KEYS",
    "build_cohort_finalization_receipt",
    "build_post_reconstruction_lock_receipt",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "publish_cohort_finalization_receipt",
    "publish_post_reconstruction_lock_receipt",
    "publish_private_json_no_clobber",
    "read_private_json",
    "validate_batch_metadata_partition",
    "validate_cache_topology",
    "validate_cohort_finalization_receipt",
    "validate_post_reconstruction_lock_receipt",
    "validate_zero_scientific_actions",
    "zero_scientific_actions",
]
