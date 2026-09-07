#!/usr/bin/env python3
"""Closed, metadata-only R8U-R7G cohort and lock receipts.

The historical R7C module remains the authority for its own receipts.  This
additive module reuses only its pure partition and cache-topology validators,
then adds the fixed R7F event authority and the full-cohort facts that were
not part of the R7C schema.  It has no scheduler or scientific entrypoint.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Final, Mapping, Sequence

import lvef_c3_r8u_r7c_metadata as r7c


ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
SCIENTIFIC_COMMIT: Final = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
R7F_RUNTIME_IMPLEMENTATION_COMMIT: Final = (
    "2223d9768a1cc23efbe95a3c5474ea747a383a10"
)
R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT: Final = (
    "4dc4b2327f91ffd3912c91a7113f16d41d0562a8"
)
BATCH16_FINAL_RECEIPT_SHA256: Final = (
    "63b002947814e92c616d0eb7f74ca334cba4e77cdc17f7ce2b55cfc51e090439"
)
R7F_CAPACITY_RECEIPT_SHA256: Final = (
    "4163c6faf46073ce79cd5dd6999407ec583d72663904b1bbda5c7bf20d45964d"
)
R7F_CONTINUATION_CLAIM_SHA256: Final = (
    "3eeb09049871ea79a48f7cd7015130909492ddf5e339f9fc9cb1f432204a9f14"
)
R7F_ARRAY_SUBMISSION_RECEIPT_SHA256: Final = (
    "a2346272e02edc2584017361bf5404186a7a3eca1b51e25c704430bea95303a4"
)
R7F_FINALIZER_SUBMISSION_RECEIPT_SHA256: Final = (
    "14c1d3913969aba5893e32de4524e525f891e43523a323b80f47313cb77214a5"
)
R7F_COMBINED_SUBMISSION_RECEIPT_SHA256: Final = (
    "4f6b1156e1580e175ed605c4a5002d6d180747a8bc82f0c1e90ebe3b80cbc306"
)
ARRAY_JOB_ID: Final = "7480830"
FINALIZER_JOB_ID: Final = "7480831"

EXPECTED_BATCH_IDS: Final = r7c.EXPECTED_BATCH_IDS
EXPECTED_SELECTED_STUDIES: Final = 4_530
EXPECTED_SELECTED_SOURCE_OBJECTS: Final = 335_984
EXPECTED_SELECTED_SOURCE_BYTES: Final = 1_216_569_133_322
EXPECTED_TAIL_STUDIES: Final = 530

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP_RE: Final = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]+)?Z$"
)

DERIVED_BATCH_COUNT_KEYS: Final = frozenset(
    {"n_unreadable_objects", "n_single_frame_objects", "n_blocking_failures"}
)
BATCH_COUNT_KEYS: Final = frozenset(
    set(r7c.BATCH_COUNT_KEYS) | set(DERIVED_BATCH_COUNT_KEYS)
)
BATCH_HASH_KEYS: Final = r7c.BATCH_HASH_KEYS
BATCH_METADATA_KEYS: Final = frozenset(
    set(r7c.BATCH_METADATA_KEYS) | set(DERIVED_BATCH_COUNT_KEYS)
)
AGGREGATE_KEYS: Final = frozenset(
    {"finalized_batches", "tail_selected_studies", *BATCH_COUNT_KEYS}
)
ACCOUNTING_ROLES: Final = r7c.ACCOUNTING_ROLES
ZERO_SCIENTIFIC_ACTION_KEYS: Final = r7c.ZERO_SCIENTIFIC_ACTION_KEYS
CACHE_TOPOLOGY_KEYS: Final = r7c.CACHE_TOPOLOGY_KEYS
STUDY_PARTITION_KEYS: Final = r7c.STUDY_PARTITION_KEYS
REPOSITORY_STATE_KEYS: Final = r7c.REPOSITORY_STATE_KEYS
SCHEDULER_STATE_KEYS: Final = r7c.SCHEDULER_STATE_KEYS

R7F_AUTHORITY_RECEIPT_KEYS: Final = frozenset(
    {
        "r7f_capacity_receipt_sha256",
        "r7f_probe_terminal_receipt_sha256",
        "r7f_continuation_claim_sha256",
        "r7f_array_submission_receipt_sha256",
        "r7f_finalizer_submission_receipt_sha256",
        "r7f_combined_submission_receipt_sha256",
    }
)

COHORT_RECEIPT_KEYS: Final = frozenset(
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
        "base_adjudication_implementation_commit",
        "adjudication_implementation_commit",
        "terminal_authority_sha256",
        *R7F_AUTHORITY_RECEIPT_KEYS,
        "batch16_final_receipt_sha256",
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

LOCK_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "created_at_utc",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "base_adjudication_implementation_commit",
        "adjudication_implementation_commit",
        "terminal_authority_sha256",
        *R7F_AUTHORITY_RECEIPT_KEYS,
        "batch16_final_receipt_sha256",
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
        "exact_335984_source_objects",
        "exact_1216569133322_source_bytes",
        "exact_530_study_tail",
        "exact_aggregate_reconciliation",
        "raw_source_authority_retained",
        "no_source_substitution",
        "no_outcome_informed_decision",
        "no_blocking_failure",
    }
)


class R7GMetadataError(RuntimeError):
    """One sanitized, fail-closed R7G metadata error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7GMetadataError(code)


def _translate_r7c(exc: r7c.R7CMetadataError) -> R7GMetadataError:
    return R7GMetadataError(exc.code.replace("R8U_R7C", "R8U_R7G"))


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
        raise R7GMetadataError("CANONICAL_JSON_INVALID") from exc


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_typed_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _exact_typed_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _exact_typed_equal(one, two)
            for one, two in zip(left, right, strict=True)
        )
    return left == right


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
        raise R7GMetadataError(code) from exc
    return value


def validate_zero_scientific_actions(
    value: Mapping[str, Any],
) -> dict[str, int]:
    try:
        return r7c.validate_zero_scientific_actions(value)
    except r7c.R7CMetadataError as exc:
        raise _translate_r7c(exc) from exc


def zero_scientific_actions() -> dict[str, int]:
    return {key: 0 for key in sorted(ZERO_SCIENTIFIC_ACTION_KEYS)}


def validate_cache_topology(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return r7c.validate_cache_topology(value)
    except r7c.R7CMetadataError as exc:
        raise _translate_r7c(exc) from exc


def _legacy_batch_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(item, Mapping) or set(item) != BATCH_METADATA_KEYS:
        _fail("BATCH_METADATA_SCHEMA_INVALID")
    projected = dict(item)
    unreadable = _require_nonnegative_int(
        projected.pop("n_unreadable_objects"),
        "BATCH_UNREADABLE_COUNT_INVALID",
    )
    single_frame = _require_nonnegative_int(
        projected.pop("n_single_frame_objects"),
        "BATCH_SINGLE_FRAME_COUNT_INVALID",
    )
    blocking = _require_nonnegative_int(
        projected.pop("n_blocking_failures"),
        "BATCH_BLOCKING_FAILURE_COUNT_INVALID",
    )
    source = _require_nonnegative_int(
        item.get("n_source_objects"), "BATCH_SOURCE_COUNT_INVALID"
    )
    readable = _require_nonnegative_int(
        item.get("n_readable_objects"), "BATCH_READABLE_COUNT_INVALID"
    )
    multiframe = _require_nonnegative_int(
        item.get("n_multiframe_candidates"), "BATCH_MULTIFRAME_COUNT_INVALID"
    )
    if (
        unreadable != source - readable
        or single_frame != readable - multiframe
        or blocking != 0
    ):
        _fail("BATCH_DERIVED_COUNT_CONTRADICTION")
    return projected


def validate_batch_metadata_partition(
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    *,
    expected_plan_sha256: str,
    expected_attempt_id: str,
    expected_scientific_commit: str,
) -> dict[str, Any]:
    """Validate 19 ordered sealed projections and literal full-cohort totals."""

    if not isinstance(batch_metadata, Sequence) or isinstance(
        batch_metadata, (str, bytes)
    ):
        _fail("BATCH_METADATA_SEQUENCE_INVALID")
    try:
        legacy_rows = [_legacy_batch_projection(item) for item in batch_metadata]
        base = r7c.validate_batch_metadata_partition(
            plan,
            legacy_rows,
            expected_plan_sha256=expected_plan_sha256,
            expected_attempt_id=expected_attempt_id,
            expected_scientific_commit=expected_scientific_commit,
        )
    except r7c.R7CMetadataError as exc:
        raise _translate_r7c(exc) from exc
    aggregate = dict(base["aggregate_totals"])
    for key in DERIVED_BATCH_COUNT_KEYS:
        aggregate[key] = sum(int(item[key]) for item in batch_metadata)
    aggregate["tail_selected_studies"] = sum(
        int(item["n_selected_studies"]) for item in batch_metadata[16:19]
    )
    if set(aggregate) != AGGREGATE_KEYS:
        _fail("FULL_COHORT_AGGREGATE_SCHEMA_INVALID")
    if (
        aggregate["finalized_batches"] != 19
        or aggregate["n_selected_studies"] != EXPECTED_SELECTED_STUDIES
        or aggregate["n_selected_subjects"] != EXPECTED_SELECTED_STUDIES
        or aggregate["n_source_objects"] != EXPECTED_SELECTED_SOURCE_OBJECTS
        or aggregate["source_bytes"] != EXPECTED_SELECTED_SOURCE_BYTES
        or aggregate["tail_selected_studies"] != EXPECTED_TAIL_STUDIES
        or [item["n_selected_studies"] for item in batch_metadata[16:19]]
        != [250, 250, 30]
        or aggregate["n_unreadable_objects"]
        != aggregate["n_source_objects"] - aggregate["n_readable_objects"]
        or aggregate["n_single_frame_objects"]
        != aggregate["n_readable_objects"]
        - aggregate["n_multiframe_candidates"]
        or aggregate["n_blocking_failures"] != 0
    ):
        _fail("FULL_COHORT_FIXED_AGGREGATE_MISMATCH")
    normalized = [dict(item) for item in batch_metadata]
    return {
        **dict(base),
        "batch_metadata_projection_sha256": canonical_json_sha256(normalized),
        "aggregate_totals": aggregate,
    }


def _validate_accounting_hashes(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(ACCOUNTING_ROLES):
        _fail("ACCOUNTING_RECEIPT_HASH_SCHEMA_INVALID")
    return {
        role: _require_hash(
            value.get(role), "ACCOUNTING_RECEIPT_HASH_INVALID"
        )
        for role in ACCOUNTING_ROLES
    }


def _validate_r7f_authority_hashes(
    value: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != R7F_AUTHORITY_RECEIPT_KEYS:
        _fail("R7F_AUTHORITY_HASH_SCHEMA_INVALID")
    expected = {
        "r7f_capacity_receipt_sha256": R7F_CAPACITY_RECEIPT_SHA256,
        "r7f_continuation_claim_sha256": R7F_CONTINUATION_CLAIM_SHA256,
        "r7f_array_submission_receipt_sha256": (
            R7F_ARRAY_SUBMISSION_RECEIPT_SHA256
        ),
        "r7f_finalizer_submission_receipt_sha256": (
            R7F_FINALIZER_SUBMISSION_RECEIPT_SHA256
        ),
        "r7f_combined_submission_receipt_sha256": (
            R7F_COMBINED_SUBMISSION_RECEIPT_SHA256
        ),
    }
    result: dict[str, str] = {}
    for key in sorted(R7F_AUTHORITY_RECEIPT_KEYS):
        digest = _require_hash(value.get(key), "R7F_AUTHORITY_HASH_INVALID")
        if key in expected and digest != expected[key]:
            _fail("R7F_AUTHORITY_HASH_MISMATCH")
        result[key] = digest
    return result


def _validate_fixed_authority(
    *,
    attempt_id: str,
    plan_sha256: str,
    scientific_commit: str,
    runtime_implementation_commit: str,
    base_adjudication_implementation_commit: str,
    adjudication_implementation_commit: str,
) -> None:
    base_adjudication = _require_commit(
        base_adjudication_implementation_commit,
        "BASE_ADJUDICATION_COMMIT_INVALID",
    )
    adjudication = _require_commit(
        adjudication_implementation_commit, "ADJUDICATION_COMMIT_INVALID"
    )
    if (
        attempt_id != ATTEMPT_ID
        or plan_sha256 != PLAN_SHA256
        or scientific_commit != SCIENTIFIC_COMMIT
        or runtime_implementation_commit != R7F_RUNTIME_IMPLEMENTATION_COMMIT
        or base_adjudication
        != R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        or adjudication
        in {
            R7F_RUNTIME_IMPLEMENTATION_COMMIT,
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
        }
    ):
        _fail("R7G_FIXED_AUTHORITY_MISMATCH")


def _validate_aggregate_totals(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        _fail("FULL_COHORT_AGGREGATE_SCHEMA_INVALID")
    result = {
        key: _require_nonnegative_int(
            value.get(key), "FULL_COHORT_AGGREGATE_VALUE_INVALID"
        )
        for key in AGGREGATE_KEYS
    }
    if (
        result["finalized_batches"] != 19
        or result["n_selected_studies"] != EXPECTED_SELECTED_STUDIES
        or result["n_selected_subjects"] != EXPECTED_SELECTED_STUDIES
        or result["n_source_objects"] != EXPECTED_SELECTED_SOURCE_OBJECTS
        or result["source_bytes"] != EXPECTED_SELECTED_SOURCE_BYTES
        or result["tail_selected_studies"] != EXPECTED_TAIL_STUDIES
        or result["n_unreadable_objects"]
        != result["n_source_objects"] - result["n_readable_objects"]
        or result["n_single_frame_objects"]
        != result["n_readable_objects"] - result["n_multiframe_candidates"]
        or result["n_blocking_failures"] != 0
        or result["n_downloaded_objects"] != result["n_source_objects"]
        or result["downloaded_bytes"] != result["source_bytes"]
        or result["n_multiframe_candidates"]
        != result["n_successful_extractions"]
        + result["n_technical_dispositions"]
        or result["n_successful_extractions"] != result["n_clip_embeddings"]
        or result["n_study_embeddings"]
        + result["n_prespecified_no_cine_studies"]
        != EXPECTED_SELECTED_STUDIES
        or result["n_new_no_cine_studies"] != 0
        or any(
            result[key] != 0
            for key in (
                "n_missing_selected_studies",
                "n_duplicate_selected_studies",
                "n_source_substitutions",
                "n_unaccounted_multiframe_candidates",
                "n_outcome_informed_decisions",
            )
        )
    ):
        _fail("FULL_COHORT_AGGREGATE_CONTRADICTION")
    return dict(sorted(result.items()))


def _validate_study_partition(value: object) -> dict[str, Any]:
    try:
        return r7c._validate_study_partition_receipt(value)
    except r7c.R7CMetadataError as exc:
        raise _translate_r7c(exc) from exc


def _validate_ordered_receipts(value: object) -> list[dict[str, str]]:
    try:
        rows = r7c._validate_ordered_batch_receipts(value)
    except r7c.R7CMetadataError as exc:
        raise _translate_r7c(exc) from exc
    if rows[15]["sha256"] != BATCH16_FINAL_RECEIPT_SHA256:
        _fail("BATCH16_RECEIPT_HASH_MISMATCH")
    return rows


def build_cohort_finalization_receipt(
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
    plan_sha256: str,
    scientific_commit: str,
    runtime_implementation_commit: str,
    base_adjudication_implementation_commit: str,
    adjudication_implementation_commit: str,
    terminal_authority_sha256: str,
    r7f_authority_receipt_sha256: Mapping[str, Any],
    accounting_receipt_sha256: Mapping[str, Any],
    cache_topology: Mapping[str, Any],
    prohibited_actions: Mapping[str, Any],
    created_at_utc: str,
) -> dict[str, Any]:
    """Build the fixed R7G receipt from sealed metadata projections only."""

    _validate_fixed_authority(
        attempt_id=attempt_id,
        plan_sha256=plan_sha256,
        scientific_commit=scientific_commit,
        runtime_implementation_commit=runtime_implementation_commit,
        base_adjudication_implementation_commit=(
            base_adjudication_implementation_commit
        ),
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    _require_hash(terminal_authority_sha256, "TERMINAL_AUTHORITY_HASH_INVALID")
    _require_timestamp(created_at_utc, "CREATION_TIMESTAMP_INVALID")
    r7f_hashes = _validate_r7f_authority_hashes(
        r7f_authority_receipt_sha256
    )
    accounting = _validate_accounting_hashes(accounting_receipt_sha256)
    topology = validate_cache_topology(cache_topology)
    zero = validate_zero_scientific_actions(prohibited_actions)
    reconciliation = validate_batch_metadata_partition(
        plan,
        batch_metadata,
        expected_plan_sha256=plan_sha256,
        expected_attempt_id=attempt_id,
        expected_scientific_commit=scientific_commit,
    )
    receipt = {
        "schema_version": 1,
        "artifact_type": (
            "lvef_c3_r8u_r7g_metadata_only_cohort_finalization_v1"
        ),
        "status": "PASS_R8U_R7G_METADATA_ONLY_FULL_COHORT_FINALIZED",
        "finalization_mode": "R7G_METADATA_ONLY_PUBLICATION",
        "created_at_utc": created_at_utc,
        "attempt_id": attempt_id,
        "batch_plan_sha256": plan_sha256,
        "scientific_commit": scientific_commit,
        "runtime_implementation_commit": runtime_implementation_commit,
        "base_adjudication_implementation_commit": (
            base_adjudication_implementation_commit
        ),
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "terminal_authority_sha256": terminal_authority_sha256,
        **r7f_hashes,
        "batch16_final_receipt_sha256": BATCH16_FINAL_RECEIPT_SHA256,
        "continuation_array_job_id": ARRAY_JOB_ID,
        "continuation_array_task_range": "17-19",
        "continuation_array_max_concurrency": 1,
        "cohort_finalizer_job_id": FINALIZER_JOB_ID,
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


def _validate_cohort_receipt_projection(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != COHORT_RECEIPT_KEYS:
        _fail("COHORT_RECEIPT_SCHEMA_INVALID")
    if (
        value.get("schema_version") != 1
        or isinstance(value.get("schema_version"), bool)
        or value.get("artifact_type")
        != "lvef_c3_r8u_r7g_metadata_only_cohort_finalization_v1"
        or value.get("status")
        != "PASS_R8U_R7G_METADATA_ONLY_FULL_COHORT_FINALIZED"
        or value.get("finalization_mode") != "R7G_METADATA_ONLY_PUBLICATION"
        or value.get("attempt_id") != ATTEMPT_ID
        or value.get("batch_plan_sha256") != PLAN_SHA256
        or value.get("scientific_commit") != SCIENTIFIC_COMMIT
        or value.get("runtime_implementation_commit")
        != R7F_RUNTIME_IMPLEMENTATION_COMMIT
        or value.get("base_adjudication_implementation_commit")
        != R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        or value.get("continuation_array_job_id") != ARRAY_JOB_ID
        or value.get("continuation_array_task_range") != "17-19"
        or value.get("continuation_array_max_concurrency") != 1
        or isinstance(value.get("continuation_array_max_concurrency"), bool)
        or value.get("cohort_finalizer_job_id") != FINALIZER_JOB_ID
    ):
        _fail("COHORT_RECEIPT_IDENTITY_INVALID")
    _require_timestamp(value.get("created_at_utc"), "CREATION_TIMESTAMP_INVALID")
    adjudication = _require_commit(
        value.get("adjudication_implementation_commit"),
        "ADJUDICATION_COMMIT_INVALID",
    )
    if adjudication in {
        R7F_RUNTIME_IMPLEMENTATION_COMMIT,
        R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
    }:
        _fail("RUNTIME_ADJUDICATION_COMMIT_CONFLATION")
    _require_hash(
        value.get("terminal_authority_sha256"),
        "TERMINAL_AUTHORITY_HASH_INVALID",
    )
    r7f_hashes = _validate_r7f_authority_hashes(
        {key: value[key] for key in R7F_AUTHORITY_RECEIPT_KEYS}
    )
    accounting = _validate_accounting_hashes(value["accounting_receipt_sha256"])
    ordered = _validate_ordered_receipts(
        value["ordered_batch_finalization_receipts"]
    )
    _require_hash(
        value.get("batch_metadata_projection_sha256"),
        "COHORT_RECEIPT_HASH_INVALID",
    )
    partition = _validate_study_partition(value["study_partition"])
    aggregate = _validate_aggregate_totals(value["aggregate_totals"])
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
    return {
        **dict(value),
        **r7f_hashes,
        "accounting_receipt_sha256": accounting,
        "ordered_batch_finalization_receipts": ordered,
        "study_partition": partition,
        "aggregate_totals": aggregate,
        "cache_topology": validate_cache_topology(value["cache_topology"]),
        "prohibited_actions": validate_zero_scientific_actions(
            value["prohibited_actions"]
        ),
    }


def validate_cohort_finalization_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    batch_metadata: Sequence[Mapping[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    expected = build_cohort_finalization_receipt(
        plan, batch_metadata, **kwargs
    )
    if not isinstance(value, Mapping) or not _exact_typed_equal(
        dict(value), expected
    ):
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
        or any(
            value.get(key) != expected_commit
            for key in ("local_head", "origin_head", "scc_head")
        )
        or value.get("local_tracked_clean") is not True
        or value.get("scc_tracked_clean") is not True
    ):
        _fail("LOCK_REPOSITORY_STATE_CONTRADICTION")
    return dict(value)


def _validate_scheduler_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != SCHEDULER_STATE_KEYS:
        _fail("LOCK_SCHEDULER_STATE_SCHEMA_INVALID")
    if (
        _require_nonnegative_int(
            value.get("matching_active_jobs"), "LOCK_ACTIVE_JOB_COUNT_INVALID"
        )
        != 0
        or _require_nonnegative_int(
            value.get("matching_active_processes"),
            "LOCK_ACTIVE_PROCESS_COUNT_INVALID",
        )
        != 0
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
    cohort_finalization_mode: str = "R7G_METADATA_ONLY_PUBLICATION",
) -> dict[str, Any]:
    """Build the one R7G lock from a self-contained cohort projection."""

    _require_hash(cohort_receipt_sha256, "COHORT_RECEIPT_HASH_INVALID")
    _require_timestamp(created_at_utc, "LOCK_TIMESTAMP_INVALID")
    if cohort_finalization_mode not in {
        "R7G_METADATA_ONLY_PUBLICATION",
        "REUSED_EXISTING",
    }:
        _fail("LOCK_COHORT_FINALIZATION_MODE_INVALID")
    if canonical_json_sha256(cohort_receipt) != cohort_receipt_sha256:
        _fail("LOCK_COHORT_RECEIPT_INVALID")
    cohort = _validate_cohort_receipt_projection(cohort_receipt)
    base_adjudication = str(
        cohort["base_adjudication_implementation_commit"]
    )
    adjudication = str(cohort["adjudication_implementation_commit"])
    repository = _validate_repository_state(
        repository_state,
        expected_commit=adjudication,
        expected_branch=expected_branch,
    )
    scheduler = _validate_scheduler_state(scheduler_state)
    aggregate = _validate_aggregate_totals(cohort["aggregate_totals"])
    partition = _validate_study_partition(cohort["study_partition"])
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7g_post_reconstruction_lock_v1",
        "status": "PASS_R8U_R7G_POST_RECONSTRUCTION_LOCK",
        "created_at_utc": created_at_utc,
        "attempt_id": cohort["attempt_id"],
        "batch_plan_sha256": cohort["batch_plan_sha256"],
        "scientific_commit": cohort["scientific_commit"],
        "runtime_implementation_commit": cohort[
            "runtime_implementation_commit"
        ],
        "base_adjudication_implementation_commit": base_adjudication,
        "adjudication_implementation_commit": adjudication,
        "terminal_authority_sha256": cohort["terminal_authority_sha256"],
        **{key: cohort[key] for key in R7F_AUTHORITY_RECEIPT_KEYS},
        "batch16_final_receipt_sha256": cohort[
            "batch16_final_receipt_sha256"
        ],
        "accounting_receipt_sha256": dict(
            cohort["accounting_receipt_sha256"]
        ),
        "ordered_batch_finalization_receipts": [
            dict(row) for row in cohort["ordered_batch_finalization_receipts"]
        ],
        "cohort_finalization_receipt_sha256": cohort_receipt_sha256,
        "cohort_finalization_mode": cohort_finalization_mode,
        "study_partition": dict(partition),
        "aggregate_totals": dict(aggregate),
        "cache_topology": validate_cache_topology(cohort["cache_topology"]),
        "repository_state": repository,
        "scheduler_state": scheduler,
        "prohibited_actions": validate_zero_scientific_actions(
            cohort["prohibited_actions"]
        ),
        "lock_review_count": 1,
        "all_batch_ledgers_finalized": True,
        "all_preservation_transitions_valid": True,
        "all_cache_retirement_transitions_valid": True,
        "all_batch_finalization_receipts_valid": True,
        "exact_4530_study_partition": True,
        "exact_335984_source_objects": True,
        "exact_1216569133322_source_bytes": True,
        "exact_530_study_tail": True,
        "exact_aggregate_reconciliation": True,
        "raw_source_authority_retained": True,
        "no_source_substitution": True,
        "no_outcome_informed_decision": True,
        "no_blocking_failure": True,
    }
    if set(receipt) != LOCK_RECEIPT_KEYS:
        _fail("LOCK_RECEIPT_SCHEMA_INVALID")
    return receipt


def validate_post_reconstruction_lock_receipt(
    value: Mapping[str, Any],
    cohort_receipt: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    expected = build_post_reconstruction_lock_receipt(
        cohort_receipt, **kwargs
    )
    if not isinstance(value, Mapping) or not _exact_typed_equal(
        dict(value), expected
    ):
        _fail("LOCK_RECEIPT_MISMATCH")
    return dict(value)


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


def _strict_json(payload: bytes) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                _fail("PRIVATE_JSON_DUPLICATE_KEY")
            result[key] = item
        return result

    def reject(_value: str) -> Any:
        _fail("PRIVATE_JSON_CONSTANT_INVALID")

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R7GMetadataError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7GMetadataError("PRIVATE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("PRIVATE_JSON_NOT_OBJECT")
    return value


def read_private_json(
    path: Path, *, maximum_bytes: int = 8 * 1024 * 1024
) -> tuple[dict[str, Any], bytes]:
    """Read one stable owner-0600 regular JSON file without following links."""

    descriptor = -1
    try:
        visible = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            stat.S_ISLNK(visible.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or _file_identity(visible) != _file_identity(before)
        ):
            _fail("PRIVATE_RECEIPT_NOT_OWNER_REGULAR_0600")
        remaining = before.st_size
        blocks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("PRIVATE_RECEIPT_CHANGED_DURING_READ")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        after_visible = os.lstat(path)
        if (
            _file_identity(before) != _file_identity(after)
            or _file_identity(after) != _file_identity(after_visible)
        ):
            _fail("PRIVATE_RECEIPT_CHANGED_DURING_READ")
        payload = b"".join(blocks)
        return _strict_json(payload), payload
    except R7GMetadataError:
        raise
    except OSError as exc:
        raise R7GMetadataError(
            "PRIVATE_RECEIPT_NOT_OWNER_REGULAR_0600"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def publish_private_json_no_clobber(
    path: Path,
    value: Mapping[str, Any],
    *,
    validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[str, bool]:
    """Publish canonically once, or reuse only an exact valid receipt."""

    validator(value)
    body = canonical_json_bytes(value)
    digest = hashlib.sha256(body).hexdigest()
    if os.path.lexists(path):
        observed, payload = read_private_json(path)
        try:
            validator(observed)
        except R7GMetadataError as exc:
            raise R7GMetadataError(
                "EXISTING_RECEIPT_CONTRADICTS_EXPECTED"
            ) from exc
        if not _exact_typed_equal(observed, dict(value)) or payload != body:
            _fail("EXISTING_RECEIPT_CONTRADICTS_EXPECTED")
        return digest, False
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise R7GMetadataError("PRIVATE_RECEIPT_PARENT_INVALID") from exc
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
        descriptor = os.open(
            temporary,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        directory_descriptor = os.open(
            parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as exc:
        raise R7GMetadataError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER") from exc
    except R7GMetadataError:
        raise
    except OSError as exc:
        raise R7GMetadataError("PRIVATE_RECEIPT_PUBLICATION_FAILED") from exc
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
    "ARRAY_JOB_ID",
    "ATTEMPT_ID",
    "BATCH_COUNT_KEYS",
    "BATCH_HASH_KEYS",
    "BATCH_METADATA_KEYS",
    "CACHE_TOPOLOGY_KEYS",
    "COHORT_RECEIPT_KEYS",
    "EXPECTED_BATCH_IDS",
    "FINALIZER_JOB_ID",
    "LOCK_RECEIPT_KEYS",
    "PLAN_SHA256",
    "R7F_AUTHORITY_RECEIPT_KEYS",
    "R7F_RUNTIME_IMPLEMENTATION_COMMIT",
    "R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT",
    "R7GMetadataError",
    "SCIENTIFIC_COMMIT",
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
