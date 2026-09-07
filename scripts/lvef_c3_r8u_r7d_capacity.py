"""Fixed Tasks-17--19-only R8U-R7D capacity authority.

The historic post-reallocation capacity entrypoint is a frozen canary.  This
module reuses its read-only observation and immutable-plan helpers while
keeping the R7D schema and arithmetic in a separate implementation epoch.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Callable, Mapping, Sequence

import capture_lvef_c3_post_reallocation_capacity as _frozen_capacity

from capture_lvef_c3_post_reallocation_capacity import (
    COMMIT_RE,
    DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
    DISPLAY_CROSSCHECK_PASS,
    DISPLAY_CROSSCHECK_UNAVAILABLE,
    EXPECTED_RESEARCH_FILE_QUOTA,
    EXPECTED_RESEARCH_QUOTA_KIB,
    PostReallocationCapacityError,
    R8U_BASE_IMPLEMENTATION_COMMIT,
    R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT,
    R8U_CONTINUATION_FIRST_TASK,
    R8U_CONTINUATION_LAST_TASK,
    R8U_CONTINUATION_TASK_COUNT,
    R8U_EXTRACTED_BYTES_PER_OBJECT,
    R8U_FIXED_CONTROL_FILE_DEMAND,
    R8U_LOG_BYTES,
    R8U_MANIFEST_AND_METADATA_BYTES,
    R8U_ORIGINAL_ATTEMPT_ID,
    R8U_ORIGINAL_PLAN_SHA256,
    R8U_ORIGINAL_SCIENTIFIC_COMMIT,
    R8U_PRESERVATION_AND_FINALIZATION_BYTES,
    R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
    R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
    R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
    R8U_R8R_IMPLEMENTATION_COMMIT,
    R8U_RETAINED_EXTRACTED_AUDIT_BYTES,
    R8U_SAFETY_BYTES,
    R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
    R8U_STUDY_EMBEDDING_BYTES_PER_STUDY,
    _capture_current_capacity_snapshot,
    _fixed_r8u_recovery_batches,
)


# Phase 1I-R8U-R7D is a final, narrowly scoped admission check for original
# Tasks 17--19 after Batches 1--16 have been finalized.  The current native
# quota/file observations already include the finalized prefix, Batch 16, old
# control-plane evidence, and any preserved partial artifacts.  Those objects
# are therefore recorded as baseline evidence and are never charged again.
# Fresh demand is exactly the three remaining batches, the largest transfer
# retry, one (and only one) rolling extraction cache, their embeddings, final
# cohort aggregation/preservation, and the established control/safety bounds.
R8U_R7D_CAPACITY_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r7d_tasks17_19_capacity_v1"
)
R8U_R7D_CAPACITY_STATUS_PASS = (
    "PASS_R7D_TASKS_17_19_WITH_200GB_RESERVE"
)
R8U_R7D_CAPACITY_STATUS_BLOCKED = (
    "BLOCKED_R7D_TASKS_17_19_CAPACITY"
)
R8U_R7D_INCREMENT_BYTES = 353_307_877_898
R8U_R7D_REQUIRED_FILE_SLOTS = 154_607
R8U_R7D_REQUIRED_QUOTA_RESERVE_BYTES = 200_000_000_000
R8U_R7D_REQUIRED_PHYSICAL_RESERVE_BYTES = 200_000_000_000
R8U_R7D_FINALIZED_PREFIX_BATCHES = 16
R8U_R7D_FINALIZED_PREFIX_STUDIES = 4_000
R8U_R7D_REMAINING_STUDIES = 530
R8U_R7D_REMAINING_OBJECTS = 39_607
R8U_R7D_REMAINING_SOURCE_BYTES = 143_890_036_746
R8U_R7D_ZERO_EFFECT_KEYS = frozenset(
    {
        "cloud_requests",
        "qsub_submissions",
        "scheduler_jobs_submitted",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accesses",
        "files_moved",
        "files_copied",
        "files_deleted",
        "writes_performed",
    }
)
R8U_R7D_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "blocking_reason_codes",
        "capacity_observation_scope",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "r7d_runtime_commit",
        "continuation_first_task",
        "continuation_last_task",
        "continuation_task_count",
        "remaining_batch_count",
        "finalized_prefix_batches",
        "finalized_prefix_studies",
        "remaining_studies",
        "remaining_objects",
        "remaining_source_bytes",
        "largest_remaining_batch_objects",
        "largest_remaining_batch_source_bytes",
        "maximum_simultaneous_active_extraction_caches",
        "continuation_raw_source_demand_bytes",
        "largest_transfer_retry_demand_bytes",
        "active_extraction_cache_object_demand",
        "active_extraction_cache_demand_bytes",
        "continuation_clip_embedding_upper_bound_bytes",
        "continuation_study_embedding_upper_bound_bytes",
        "retained_extracted_audit_demand_bytes",
        "manifest_and_metadata_demand_bytes",
        "log_demand_bytes",
        "final_cohort_aggregation_and_preservation_demand_bytes",
        "safety_demand_bytes",
        "r7d_increment_bytes",
        "continuation_raw_object_file_demand",
        "active_extraction_cache_file_demand",
        "fixed_control_file_demand",
        "required_file_slots",
        "batches_1_16_bytes_added_to_increment",
        "batches_1_16_files_added_to_demand",
        "batch16_extraction_bytes_added_to_increment",
        "batch16_embedding_bytes_added_to_increment",
        "batch16_files_added_to_demand",
        "preserved_old_control_evidence_bytes_baseline",
        "preserved_old_control_evidence_files_baseline",
        "confirmed_partial_artifact_bytes_baseline",
        "confirmed_partial_artifact_files_baseline",
        "baseline_evidence_bytes_added_to_increment",
        "baseline_evidence_files_added_to_demand",
        "finalized_prefix_already_in_observed_usage",
        "batch16_already_in_observed_usage",
        "preserved_old_evidence_already_in_observed_usage",
        "confirmed_partials_already_in_observed_usage",
        "research_quota_bytes",
        "research_usage_bytes",
        "research_quota_remaining_bytes",
        "research_file_quota",
        "research_files_used",
        "research_file_slots_remaining",
        "research_filesystem_total_bytes",
        "research_filesystem_used_bytes",
        "research_filesystem_available_bytes",
        "projected_research_usage_bytes",
        "required_quota_reserve_bytes",
        "required_physical_reserve_bytes",
        "quota_required_available_bytes",
        "physical_required_available_bytes",
        "quota_slack_after_r7d_bytes",
        "physical_slack_after_r7d_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
        "quota_reserve_deficit_bytes",
        "physical_reserve_deficit_bytes",
        "file_slot_deficit",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "native_capacity_snapshot_captures",
        "native_quota_file_captures",
        "capacity_command_captures",
        "pquota_command_captures",
        "findmnt_command_captures",
        "df_command_captures",
        "native_quota_authority_read_only",
        "pquota_display_crosscheck",
        *R8U_R7D_ZERO_EFFECT_KEYS,
    }
)

def _fixed_r8u_r7d_runtime_commit(r7d_runtime_commit: str) -> str:
    """Validate the one caller-supplied R7D implementation epoch."""

    if (
        type(r7d_runtime_commit) is not str
        or COMMIT_RE.fullmatch(r7d_runtime_commit) is None
        or r7d_runtime_commit
        in {
            R8U_ORIGINAL_SCIENTIFIC_COMMIT,
            R8U_R8R_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        }
    ):
        raise PostReallocationCapacityError(
            "R8U_R7D_RUNTIME_COMMIT_INVALID"
        )
    return r7d_runtime_commit


def _fixed_r8u_r7d_baselines(
    *,
    preserved_old_evidence_bytes: int,
    preserved_old_evidence_files: int,
    confirmed_partial_artifact_bytes: int,
    confirmed_partial_artifact_files: int,
) -> dict[str, int]:
    """Close the already-observed evidence/partial baseline declaration."""

    values = {
        "preserved_old_control_evidence_bytes_baseline": (
            preserved_old_evidence_bytes
        ),
        "preserved_old_control_evidence_files_baseline": (
            preserved_old_evidence_files
        ),
        "confirmed_partial_artifact_bytes_baseline": (
            confirmed_partial_artifact_bytes
        ),
        "confirmed_partial_artifact_files_baseline": (
            confirmed_partial_artifact_files
        ),
    }
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise PostReallocationCapacityError(
            "R8U_R7D_BASELINE_AUTHORITY_INVALID"
        )
    return values


def _derive_fixed_r8u_r7d_tasks17_19_demands(
    plan: Mapping[str, Any],
) -> dict[str, int]:
    """Derive only original Tasks 17--19 from the immutable C3 plan."""

    _, continuation = _fixed_r8u_recovery_batches(plan)
    batches = plan.get("batches")
    if not isinstance(batches, list):
        raise PostReallocationCapacityError(
            "R8U_R7D_FIXED_PLAN_SCOPE_INVALID"
        )
    prefix_studies = sum(
        int(batch["n_studies"])
        for batch in batches[:R8U_R7D_FINALIZED_PREFIX_BATCHES]
    )
    remaining_studies = sum(
        int(batch["n_studies"]) for batch in continuation
    )
    remaining_objects = sum(
        int(batch["n_objects"]) for batch in continuation
    )
    remaining_source_bytes = sum(
        int(batch["source_bytes"]) for batch in continuation
    )
    largest_objects = max(
        int(batch["n_objects"]) for batch in continuation
    )
    largest_source_bytes = max(
        int(batch["source_bytes"]) for batch in continuation
    )
    active_cache_bytes = largest_objects * R8U_EXTRACTED_BYTES_PER_OBJECT
    clip_embedding_bytes = (
        remaining_objects * R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_embedding_bytes = (
        remaining_studies * R8U_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    increment = sum(
        (
            remaining_source_bytes,
            largest_source_bytes,
            active_cache_bytes,
            clip_embedding_bytes,
            study_embedding_bytes,
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES,
            R8U_MANIFEST_AND_METADATA_BYTES,
            R8U_LOG_BYTES,
            R8U_PRESERVATION_AND_FINALIZATION_BYTES,
            R8U_SAFETY_BYTES,
        )
    )
    required_file_slots = (
        remaining_objects + largest_objects + R8U_FIXED_CONTROL_FILE_DEMAND
    )
    if (
        len(continuation) != R8U_CONTINUATION_TASK_COUNT
        or prefix_studies != R8U_R7D_FINALIZED_PREFIX_STUDIES
        or remaining_studies != R8U_R7D_REMAINING_STUDIES
        or remaining_objects != R8U_R7D_REMAINING_OBJECTS
        or remaining_source_bytes != R8U_R7D_REMAINING_SOURCE_BYTES
        or increment != R8U_R7D_INCREMENT_BYTES
        or required_file_slots != R8U_R7D_REQUIRED_FILE_SLOTS
    ):
        raise PostReallocationCapacityError(
            "R8U_R7D_FIXED_PLAN_SCOPE_INVALID"
        )
    return {
        "continuation_first_task": R8U_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8U_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8U_CONTINUATION_TASK_COUNT,
        "remaining_batch_count": len(continuation),
        "finalized_prefix_batches": R8U_R7D_FINALIZED_PREFIX_BATCHES,
        "finalized_prefix_studies": prefix_studies,
        "remaining_studies": remaining_studies,
        "remaining_objects": remaining_objects,
        "remaining_source_bytes": remaining_source_bytes,
        "largest_remaining_batch_objects": largest_objects,
        "largest_remaining_batch_source_bytes": largest_source_bytes,
        "maximum_simultaneous_active_extraction_caches": 1,
        "continuation_raw_source_demand_bytes": remaining_source_bytes,
        "largest_transfer_retry_demand_bytes": largest_source_bytes,
        "active_extraction_cache_object_demand": largest_objects,
        "active_extraction_cache_demand_bytes": active_cache_bytes,
        "continuation_clip_embedding_upper_bound_bytes": (
            clip_embedding_bytes
        ),
        "continuation_study_embedding_upper_bound_bytes": (
            study_embedding_bytes
        ),
        "retained_extracted_audit_demand_bytes": (
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            R8U_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": R8U_LOG_BYTES,
        "final_cohort_aggregation_and_preservation_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "r7d_increment_bytes": increment,
        "continuation_raw_object_file_demand": remaining_objects,
        "active_extraction_cache_file_demand": largest_objects,
        "fixed_control_file_demand": R8U_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": required_file_slots,
    }


# R7F repairs only the static Tasks-17--19 projection.  These aggregate-safe
# rows were regenerated from the canonical immutable plan identified by
# R8U_ORIGINAL_PLAN_SHA256; they contain no study identifiers or source paths.
# Production arithmetic below is always derived from the hash-validated plan,
# while this frozen projection provides an independent, field-specific
# comparator rather than an aggregate-equivalent fabricated distribution.
R8U_R7F_TASKS17_19_SCALAR_PROJECTION = (
    {
        "task_id": 17,
        "batch_ordinal": 16,
        "batch_id": "c3_batch_016",
        "n_studies": 250,
        "n_objects": 18_606,
        "source_bytes": 66_807_894_336,
    },
    {
        "task_id": 18,
        "batch_ordinal": 17,
        "batch_id": "c3_batch_017",
        "n_studies": 250,
        "n_objects": 18_658,
        "source_bytes": 68_754_613_138,
    },
    {
        "task_id": 19,
        "batch_ordinal": 18,
        "batch_id": "c3_batch_018",
        "n_studies": 30,
        "n_objects": 2_343,
        "source_bytes": 9_640_479_152,
    },
)
R8U_R7E_SYNTHETIC_TASKS17_19_SCALAR_PROJECTION = (
    {
        "task_id": 17,
        "batch_ordinal": 16,
        "batch_id": "c3_batch_016",
        "n_studies": 250,
        "n_objects": 15_000,
        "source_bytes": 60_000_000_000,
    },
    {
        "task_id": 18,
        "batch_ordinal": 17,
        "batch_id": "c3_batch_017",
        "n_studies": 250,
        "n_objects": 15_000,
        "source_bytes": 55_000_000_000,
    },
    {
        "task_id": 19,
        "batch_ordinal": 18,
        "batch_id": "c3_batch_018",
        "n_studies": 30,
        "n_objects": 9_607,
        "source_bytes": 28_890_036_746,
    },
)
R8U_R7F_REMAINING_SOURCE_BYTES = 145_202_986_626
R8U_R7F_LARGEST_REMAINING_BATCH_OBJECTS = 18_658
R8U_R7F_LARGEST_REMAINING_BATCH_SOURCE_BYTES = 68_754_613_138
R8U_R7F_ACTIVE_EXTRACTION_CACHE_DEMAND_BYTES = 89_873_645_568
R8U_R7F_CLIP_EMBEDDING_UPPER_BOUND_BYTES = 162_230_272
R8U_R7F_STUDY_EMBEDDING_UPPER_BOUND_BYTES = 2_170_880
R8U_R7F_INCREMENT_BYTES = 380_995_646_484
R8U_R7F_REQUIRED_FILE_SLOTS = 158_265
R8U_R7F_STATIC_PLAN_PROJECTION_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r7f_tasks17_19_static_plan_projection_v1"
)
R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_PASS = (
    "PASS_R8U_R7F_STATIC_PLAN_PROJECTION"
)
R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_MISMATCH = (
    "BLOCKED_R8U_R7F_STATIC_PLAN_PROJECTION"
)
R8U_R7F_STATIC_COMPARATOR_KEYS = frozenset(
    {
        "field_name",
        "code_frozen_expected_value",
        "immutable_plan_derived_observed_value",
        "comparison",
        "mismatch_code",
    }
)
R8U_R7F_STATIC_PLAN_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "task_projection",
        "comparators",
        "mismatching_fields",
        "mismatch_codes",
        "historical_r7e_comparators",
        "historical_r7e_mismatching_fields",
        "historical_r7e_mismatch_codes",
    }
)


class R8UR7FPlanProjectionError(PostReallocationCapacityError):
    """One exact R7F static scalar mismatch with the full comparator table."""

    def __init__(self, code: str, *, projection: Mapping[str, Any]) -> None:
        super().__init__(code)
        self.projection = dict(projection)


def _r8u_r7f_expected_scalar_projection() -> dict[str, int]:
    return {
        "continuation_batch_count": R8U_CONTINUATION_TASK_COUNT,
        "finalized_prefix_batch_count": R8U_R7D_FINALIZED_PREFIX_BATCHES,
        "finalized_prefix_study_count": R8U_R7D_FINALIZED_PREFIX_STUDIES,
        "remaining_study_count": R8U_R7D_REMAINING_STUDIES,
        "remaining_object_count": R8U_R7D_REMAINING_OBJECTS,
        "remaining_source_byte_count": R8U_R7F_REMAINING_SOURCE_BYTES,
        "largest_remaining_batch_object_count": (
            R8U_R7F_LARGEST_REMAINING_BATCH_OBJECTS
        ),
        "largest_remaining_batch_source_byte_count": (
            R8U_R7F_LARGEST_REMAINING_BATCH_SOURCE_BYTES
        ),
        "maximum_simultaneous_active_extraction_caches": 1,
        "active_extraction_cache_demand_bytes": (
            R8U_R7F_ACTIVE_EXTRACTION_CACHE_DEMAND_BYTES
        ),
        "continuation_clip_embedding_upper_bound_bytes": (
            R8U_R7F_CLIP_EMBEDDING_UPPER_BOUND_BYTES
        ),
        "continuation_study_embedding_upper_bound_bytes": (
            R8U_R7F_STUDY_EMBEDDING_UPPER_BOUND_BYTES
        ),
        "retained_extracted_audit_demand_bytes": (
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": R8U_MANIFEST_AND_METADATA_BYTES,
        "log_demand_bytes": R8U_LOG_BYTES,
        "final_cohort_aggregation_and_preservation_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "incremental_demand_bytes": R8U_R7F_INCREMENT_BYTES,
        "required_file_slots": R8U_R7F_REQUIRED_FILE_SLOTS,
    }


def _r8u_r7e_synthetic_scalar_projection() -> dict[str, int]:
    """Return the consumed R7E fixture-derived scalars for diagnosis only."""

    return {
        **_r8u_r7f_expected_scalar_projection(),
        "remaining_source_byte_count": R8U_R7D_REMAINING_SOURCE_BYTES,
        "largest_remaining_batch_object_count": 15_000,
        "largest_remaining_batch_source_byte_count": 60_000_000_000,
        "active_extraction_cache_demand_bytes": 15_000 * 4_816_896,
        "incremental_demand_bytes": R8U_R7D_INCREMENT_BYTES,
        "required_file_slots": R8U_R7D_REQUIRED_FILE_SLOTS,
    }


def _r8u_r7f_scalar_mismatch_code(field_name: str) -> str:
    return {
        "continuation_batch_count": "CONTINUATION_BATCH_COUNT_MISMATCH",
        "finalized_prefix_batch_count": "FINALIZED_PREFIX_BATCHES_MISMATCH",
        "finalized_prefix_study_count": "FINALIZED_PREFIX_STUDIES_MISMATCH",
        "remaining_study_count": "REMAINING_STUDIES_MISMATCH",
        "remaining_object_count": "REMAINING_OBJECTS_MISMATCH",
        "remaining_source_byte_count": "REMAINING_SOURCE_BYTES_MISMATCH",
        "largest_remaining_batch_object_count": (
            "LARGEST_BATCH_OBJECTS_MISMATCH"
        ),
        "largest_remaining_batch_source_byte_count": (
            "LARGEST_BATCH_SOURCE_BYTES_MISMATCH"
        ),
        "maximum_simultaneous_active_extraction_caches": (
            "ACTIVE_EXTRACTION_CACHE_COUNT_MISMATCH"
        ),
        "active_extraction_cache_demand_bytes": (
            "ACTIVE_EXTRACTION_CACHE_DEMAND_MISMATCH"
        ),
        "continuation_clip_embedding_upper_bound_bytes": (
            "CLIP_EMBEDDING_DEMAND_MISMATCH"
        ),
        "continuation_study_embedding_upper_bound_bytes": (
            "STUDY_EMBEDDING_DEMAND_MISMATCH"
        ),
        "retained_extracted_audit_demand_bytes": (
            "RETAINED_EXTRACTED_AUDIT_DEMAND_MISMATCH"
        ),
        "manifest_and_metadata_demand_bytes": "METADATA_DEMAND_MISMATCH",
        "log_demand_bytes": "LOG_DEMAND_MISMATCH",
        "final_cohort_aggregation_and_preservation_demand_bytes": (
            "PRESERVATION_DEMAND_MISMATCH"
        ),
        "safety_demand_bytes": "SAFETY_DEMAND_MISMATCH",
        "incremental_demand_bytes": "INCREMENTAL_DEMAND_MISMATCH",
        "required_file_slots": "REQUIRED_FILE_SLOTS_MISMATCH",
    }[field_name]


def build_fixed_r8u_r7f_tasks17_19_plan_projection(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare the exact immutable-plan tail with its production scalar seal."""

    _, continuation = _fixed_r8u_recovery_batches(plan)
    batches = plan.get("batches")
    if not isinstance(batches, list):
        raise PostReallocationCapacityError("R8U_R7F_FIXED_PLAN_SCHEMA_INVALID")
    task_projection = [
        {
            "task_id": ordinal + 1,
            "batch_ordinal": int(batch["ordinal"]),
            "batch_id": str(batch["batch_id"]),
            "n_studies": int(batch["n_studies"]),
            "n_objects": int(batch["n_objects"]),
            "source_bytes": int(batch["source_bytes"]),
        }
        for ordinal, batch in enumerate(
            continuation,
            start=R8U_CONTINUATION_FIRST_TASK - 1,
        )
    ]
    prefix = batches[:R8U_R7D_FINALIZED_PREFIX_BATCHES]
    remaining_studies = sum(item["n_studies"] for item in task_projection)
    remaining_objects = sum(item["n_objects"] for item in task_projection)
    remaining_source_bytes = sum(item["source_bytes"] for item in task_projection)
    largest_objects = max(item["n_objects"] for item in task_projection)
    largest_source_bytes = max(item["source_bytes"] for item in task_projection)
    active_cache_bytes = largest_objects * R8U_EXTRACTED_BYTES_PER_OBJECT
    clip_embedding_bytes = remaining_objects * R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT
    study_embedding_bytes = remaining_studies * R8U_STUDY_EMBEDDING_BYTES_PER_STUDY
    increment = sum(
        (
            remaining_source_bytes,
            largest_source_bytes,
            active_cache_bytes,
            clip_embedding_bytes,
            study_embedding_bytes,
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES,
            R8U_MANIFEST_AND_METADATA_BYTES,
            R8U_LOG_BYTES,
            R8U_PRESERVATION_AND_FINALIZATION_BYTES,
            R8U_SAFETY_BYTES,
        )
    )
    observed = {
        "continuation_batch_count": len(task_projection),
        "finalized_prefix_batch_count": len(prefix),
        "finalized_prefix_study_count": sum(
            int(batch["n_studies"]) for batch in prefix
        ),
        "remaining_study_count": remaining_studies,
        "remaining_object_count": remaining_objects,
        "remaining_source_byte_count": remaining_source_bytes,
        "largest_remaining_batch_object_count": largest_objects,
        "largest_remaining_batch_source_byte_count": largest_source_bytes,
        "maximum_simultaneous_active_extraction_caches": 1,
        "active_extraction_cache_demand_bytes": active_cache_bytes,
        "continuation_clip_embedding_upper_bound_bytes": clip_embedding_bytes,
        "continuation_study_embedding_upper_bound_bytes": study_embedding_bytes,
        "retained_extracted_audit_demand_bytes": R8U_RETAINED_EXTRACTED_AUDIT_BYTES,
        "manifest_and_metadata_demand_bytes": R8U_MANIFEST_AND_METADATA_BYTES,
        "log_demand_bytes": R8U_LOG_BYTES,
        "final_cohort_aggregation_and_preservation_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "incremental_demand_bytes": increment,
        "required_file_slots": (
            remaining_objects + largest_objects + R8U_FIXED_CONTROL_FILE_DEMAND
        ),
    }
    expected = _r8u_r7f_expected_scalar_projection()
    comparator_values: list[tuple[str, Any, Any, str]] = [
        (
            field,
            expected_value,
            observed[field],
            _r8u_r7f_scalar_mismatch_code(field),
        )
        for field, expected_value in expected.items()
    ]
    for expected_task, observed_task in zip(
        R8U_R7F_TASKS17_19_SCALAR_PROJECTION,
        task_projection,
        strict=True,
    ):
        task_id = int(expected_task["task_id"])
        for field in (
            "task_id",
            "batch_ordinal",
            "batch_id",
            "n_studies",
            "n_objects",
            "source_bytes",
        ):
            comparator_values.append(
                (
                    f"task_{task_id}.{field}",
                    expected_task[field],
                    observed_task[field],
                    f"TASK_{task_id}_{field.upper()}_MISMATCH",
                )
            )
    comparators = [
        {
            "field_name": field,
            "code_frozen_expected_value": expected_value,
            "immutable_plan_derived_observed_value": observed_value,
            "comparison": "MATCH" if expected_value == observed_value else "MISMATCH",
            "mismatch_code": mismatch_code,
        }
        for field, expected_value, observed_value, mismatch_code in comparator_values
    ]
    mismatches = [
        item for item in comparators if item["comparison"] == "MISMATCH"
    ]
    historical_values: list[tuple[str, Any, Any, str]] = [
        (
            field,
            expected_value,
            observed[field],
            _r8u_r7f_scalar_mismatch_code(field),
        )
        for field, expected_value in _r8u_r7e_synthetic_scalar_projection().items()
    ]
    for expected_task, observed_task in zip(
        R8U_R7E_SYNTHETIC_TASKS17_19_SCALAR_PROJECTION,
        task_projection,
        strict=True,
    ):
        task_id = int(expected_task["task_id"])
        for field in (
            "task_id",
            "batch_ordinal",
            "batch_id",
            "n_studies",
            "n_objects",
            "source_bytes",
        ):
            historical_values.append(
                (
                    f"task_{task_id}.{field}",
                    expected_task[field],
                    observed_task[field],
                    f"TASK_{task_id}_{field.upper()}_MISMATCH",
                )
            )
    historical_comparators = [
        {
            "field_name": field,
            "code_frozen_expected_value": expected_value,
            "immutable_plan_derived_observed_value": observed_value,
            "comparison": "MATCH" if expected_value == observed_value else "MISMATCH",
            "mismatch_code": mismatch_code,
        }
        for field, expected_value, observed_value, mismatch_code in historical_values
    ]
    historical_mismatches = [
        item
        for item in historical_comparators
        if item["comparison"] == "MISMATCH"
    ]
    return {
        "schema_version": 1,
        "artifact_type": R8U_R7F_STATIC_PLAN_PROJECTION_ARTIFACT_TYPE,
        "status": (
            R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_PASS
            if not mismatches
            else R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_MISMATCH
        ),
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": _frozen_capacity.R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        "task_projection": task_projection,
        "comparators": comparators,
        "mismatching_fields": [item["field_name"] for item in mismatches],
        "mismatch_codes": [item["mismatch_code"] for item in mismatches],
        "historical_r7e_comparators": historical_comparators,
        "historical_r7e_mismatching_fields": [
            item["field_name"] for item in historical_mismatches
        ],
        "historical_r7e_mismatch_codes": [
            item["mismatch_code"] for item in historical_mismatches
        ],
    }


def validate_fixed_r8u_r7f_tasks17_19_plan_projection(
    plan: Mapping[str, Any], value: Mapping[str, Any],
) -> dict[str, Any]:
    """Purely replay the exact R7F scalar projection and comparator table."""

    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R7F_STATIC_PLAN_PROJECTION_KEYS
        or any(
            not isinstance(item, Mapping)
            or set(item) != R8U_R7F_STATIC_COMPARATOR_KEYS
            for item in value.get("comparators", [])
        )
    ):
        raise PostReallocationCapacityError(
            "R8U_R7F_STATIC_PLAN_PROJECTION_SCHEMA_INVALID"
        )
    expected = build_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
    if _r8u_r7e_canonical(dict(value)) != _r8u_r7e_canonical(expected):
        raise PostReallocationCapacityError(
            "R8U_R7F_STATIC_PLAN_PROJECTION_REPLAY_INVALID"
        )
    return dict(value)


def require_fixed_r8u_r7f_tasks17_19_plan_projection(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a passing production projection or raise its first exact field."""

    projection = build_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
    validate_fixed_r8u_r7f_tasks17_19_plan_projection(plan, projection)
    if projection["status"] != R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_PASS:
        raise R8UR7FPlanProjectionError(
            str(projection["mismatch_codes"][0]),
            projection=projection,
        )
    return projection


def _derive_fixed_r8u_r7f_tasks17_19_demands(
    plan: Mapping[str, Any],
) -> dict[str, int]:
    """Derive R7F demand solely from the passing hash-bound production plan."""

    projection = require_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
    scalars = {
        item["field_name"]: int(item["immutable_plan_derived_observed_value"])
        for item in projection["comparators"]
        if not str(item["field_name"]).startswith("task_")
    }
    return {
        "continuation_first_task": R8U_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8U_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8U_CONTINUATION_TASK_COUNT,
        "remaining_batch_count": scalars["continuation_batch_count"],
        "finalized_prefix_batches": scalars["finalized_prefix_batch_count"],
        "finalized_prefix_studies": scalars["finalized_prefix_study_count"],
        "remaining_studies": scalars["remaining_study_count"],
        "remaining_objects": scalars["remaining_object_count"],
        "remaining_source_bytes": scalars["remaining_source_byte_count"],
        "largest_remaining_batch_objects": scalars[
            "largest_remaining_batch_object_count"
        ],
        "largest_remaining_batch_source_bytes": scalars[
            "largest_remaining_batch_source_byte_count"
        ],
        "maximum_simultaneous_active_extraction_caches": 1,
        "continuation_raw_source_demand_bytes": scalars[
            "remaining_source_byte_count"
        ],
        "largest_transfer_retry_demand_bytes": scalars[
            "largest_remaining_batch_source_byte_count"
        ],
        "active_extraction_cache_object_demand": scalars[
            "largest_remaining_batch_object_count"
        ],
        "active_extraction_cache_demand_bytes": scalars[
            "active_extraction_cache_demand_bytes"
        ],
        "continuation_clip_embedding_upper_bound_bytes": scalars[
            "continuation_clip_embedding_upper_bound_bytes"
        ],
        "continuation_study_embedding_upper_bound_bytes": scalars[
            "continuation_study_embedding_upper_bound_bytes"
        ],
        "retained_extracted_audit_demand_bytes": R8U_RETAINED_EXTRACTED_AUDIT_BYTES,
        "manifest_and_metadata_demand_bytes": R8U_MANIFEST_AND_METADATA_BYTES,
        "log_demand_bytes": R8U_LOG_BYTES,
        "final_cohort_aggregation_and_preservation_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "r7d_increment_bytes": scalars["incremental_demand_bytes"],
        "continuation_raw_object_file_demand": scalars["remaining_object_count"],
        "active_extraction_cache_file_demand": scalars[
            "largest_remaining_batch_object_count"
        ],
        "fixed_control_file_demand": R8U_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": scalars["required_file_slots"],
    }


def _validated_r8u_r7d_capacity_observation(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract one exact, metadata-only research-capacity observation."""

    try:
        native = snapshot["native"]
        dfs = snapshot["dfs"]
        research = native["research"]
        research_df = dfs["research"]
        values: dict[str, Any] = {
            "quota_kib": research["quota_kib"],
            "usage_kib": research["usage_kib"],
            "file_quota": research["file_quota"],
            "files_used": research["files_used"],
            "filesystem_total": research_df["total"],
            "filesystem_used": research_df["used"],
            "filesystem_available": research_df["available"],
            "native_capacity_snapshot_captures": snapshot[
                "native_capacity_snapshot_captures"
            ],
            "native_quota_file_captures": snapshot[
                "native_quota_file_captures"
            ],
            "capacity_command_captures": snapshot[
                "capacity_command_captures"
            ],
            "pquota_command_captures": snapshot[
                "pquota_command_captures"
            ],
            "findmnt_command_captures": snapshot[
                "findmnt_command_captures"
            ],
            "df_command_captures": snapshot["df_command_captures"],
            "pquota_display_crosscheck": snapshot[
                "pquota_display_crosscheck"
            ],
        }
    except (KeyError, TypeError) as exc:
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_OBSERVATION_INVALID"
        ) from exc
    integer_fields = set(values) - {"pquota_display_crosscheck"}
    if (
        not isinstance(snapshot, Mapping)
        or not isinstance(native, Mapping)
        or not isinstance(dfs, Mapping)
        or not isinstance(research, Mapping)
        or not isinstance(research_df, Mapping)
        or any(type(values[field]) is not int for field in integer_fields)
        or type(values["pquota_display_crosscheck"]) is not str
        or values["pquota_display_crosscheck"]
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or values["native_capacity_snapshot_captures"] != 1
        or values["native_quota_file_captures"] != 1
        or values["capacity_command_captures"] != 5
        or values["pquota_command_captures"] != 1
        or values["findmnt_command_captures"] != 2
        or values["df_command_captures"] != 2
    ):
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_OBSERVATION_INVALID"
        )
    quota_kib = int(values["quota_kib"])
    usage_kib = int(values["usage_kib"])
    file_quota = int(values["file_quota"])
    files_used = int(values["files_used"])
    filesystem_total = int(values["filesystem_total"])
    filesystem_used = int(values["filesystem_used"])
    filesystem_available = int(values["filesystem_available"])
    if (
        min(
            quota_kib,
            usage_kib,
            file_quota,
            files_used,
            filesystem_total,
            filesystem_used,
            filesystem_available,
        )
        < 0
        or quota_kib == 0
        or file_quota == 0
        or filesystem_total == 0
        or quota_kib < EXPECTED_RESEARCH_QUOTA_KIB
        or file_quota < EXPECTED_RESEARCH_FILE_QUOTA
        or usage_kib > quota_kib
        or files_used > file_quota
        or filesystem_used + filesystem_available > filesystem_total
    ):
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_OBSERVATION_INVALID"
        )
    return values


def _build_fixed_r8u_r7d_tasks17_19_capacity(
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    r7d_runtime_commit: str,
    preserved_old_evidence_bytes: int,
    preserved_old_evidence_files: int,
    confirmed_partial_artifact_bytes: int,
    confirmed_partial_artifact_files: int,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Internal common constructor for pure replay and one live capture."""

    runtime_commit = _fixed_r8u_r7d_runtime_commit(r7d_runtime_commit)
    demands = _demand_builder(plan)
    baselines = _fixed_r8u_r7d_baselines(
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
    )
    observed = _validated_r8u_r7d_capacity_observation(snapshot)

    quota = int(observed["quota_kib"]) * 1024
    usage = int(observed["usage_kib"]) * 1024
    file_quota = int(observed["file_quota"])
    files_used = int(observed["files_used"])
    physical_available = int(observed["filesystem_available"])
    increment = int(demands["r7d_increment_bytes"])
    required_file_slots = int(demands["required_file_slots"])
    quota_required = increment + R8U_R7D_REQUIRED_QUOTA_RESERVE_BYTES
    physical_required = (
        increment + R8U_R7D_REQUIRED_PHYSICAL_RESERVE_BYTES
    )
    quota_remaining = quota - usage
    file_slots_remaining = file_quota - files_used
    quota_slack = quota_remaining - increment
    physical_slack = physical_available - increment
    quota_margin = quota_remaining - quota_required
    physical_margin = physical_available - physical_required
    file_margin = file_slots_remaining - required_file_slots
    deficits = {
        "quota_reserve_deficit_bytes": max(-quota_margin, 0),
        "physical_reserve_deficit_bytes": max(-physical_margin, 0),
        "file_slot_deficit": max(-file_margin, 0),
    }
    gates = {
        "quota_reserve_gate_passed": deficits[
            "quota_reserve_deficit_bytes"
        ]
        == 0,
        "physical_reserve_gate_passed": deficits[
            "physical_reserve_deficit_bytes"
        ]
        == 0,
        "file_slot_gate_passed": deficits["file_slot_deficit"] == 0,
    }
    blocking_reason_codes = [
        code
        for field, code in (
            (
                "quota_reserve_gate_passed",
                "R8U_R7D_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8U_R7D_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8U_R7D_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[field] is not True
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": R8U_R7D_CAPACITY_ARTIFACT_TYPE,
        "status": (
            R8U_R7D_CAPACITY_STATUS_PASS
            if not blocking_reason_codes
            else R8U_R7D_CAPACITY_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "capacity_observation_scope": (
            "TASKS_17_19_ONLY_METADATA_NO_SCIENTIFIC_BODY_ACCESS"
        ),
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "r7d_runtime_commit": runtime_commit,
        **demands,
        "batches_1_16_bytes_added_to_increment": 0,
        "batches_1_16_files_added_to_demand": 0,
        "batch16_extraction_bytes_added_to_increment": 0,
        "batch16_embedding_bytes_added_to_increment": 0,
        "batch16_files_added_to_demand": 0,
        **baselines,
        "baseline_evidence_bytes_added_to_increment": 0,
        "baseline_evidence_files_added_to_demand": 0,
        "finalized_prefix_already_in_observed_usage": True,
        "batch16_already_in_observed_usage": True,
        "preserved_old_evidence_already_in_observed_usage": True,
        "confirmed_partials_already_in_observed_usage": True,
        "research_quota_bytes": quota,
        "research_usage_bytes": usage,
        "research_quota_remaining_bytes": quota_remaining,
        "research_file_quota": file_quota,
        "research_files_used": files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": int(
            observed["filesystem_total"]
        ),
        "research_filesystem_used_bytes": int(
            observed["filesystem_used"]
        ),
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": usage + increment,
        "required_quota_reserve_bytes": (
            R8U_R7D_REQUIRED_QUOTA_RESERVE_BYTES
        ),
        "required_physical_reserve_bytes": (
            R8U_R7D_REQUIRED_PHYSICAL_RESERVE_BYTES
        ),
        "quota_required_available_bytes": quota_required,
        "physical_required_available_bytes": physical_required,
        "quota_slack_after_r7d_bytes": quota_slack,
        "physical_slack_after_r7d_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **deficits,
        **gates,
        "native_capacity_snapshot_captures": int(
            observed["native_capacity_snapshot_captures"]
        ),
        "native_quota_file_captures": int(
            observed["native_quota_file_captures"]
        ),
        "capacity_command_captures": int(
            observed["capacity_command_captures"]
        ),
        "pquota_command_captures": int(
            observed["pquota_command_captures"]
        ),
        "findmnt_command_captures": int(
            observed["findmnt_command_captures"]
        ),
        "df_command_captures": int(observed["df_command_captures"]),
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": str(
            observed["pquota_display_crosscheck"]
        ),
        **{key: 0 for key in R8U_R7D_ZERO_EFFECT_KEYS},
    }
    if set(result) != R8U_R7D_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_SCHEMA_INVALID"
        )
    return result


def build_fixed_r8u_r7d_tasks17_19_capacity(
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    r7d_runtime_commit: str,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Purely build one closed R7D Tasks-17--19 capacity authority."""

    return _build_fixed_r8u_r7d_tasks17_19_capacity(
        plan,
        snapshot,
        r7d_runtime_commit=r7d_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        _demand_builder=_demand_builder,
    )
def validate_fixed_r8u_r7d_tasks17_19_capacity(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    r7d_runtime_commit: str,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Purely replay the R7D demand, exact deficits, gates, and status."""

    if not isinstance(value, Mapping) or set(value) != R8U_R7D_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_SCHEMA_INVALID"
        )
    text_fields = {
        "artifact_type",
        "status",
        "capacity_observation_scope",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "r7d_runtime_commit",
        "pquota_display_crosscheck",
    }
    boolean_fields = {
        "finalized_prefix_already_in_observed_usage",
        "batch16_already_in_observed_usage",
        "preserved_old_evidence_already_in_observed_usage",
        "confirmed_partials_already_in_observed_usage",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "native_quota_authority_read_only",
    }
    special_fields = text_fields | boolean_fields | {"blocking_reason_codes"}
    integer_fields = R8U_R7D_CAPACITY_KEYS - special_fields
    if (
        any(type(value.get(field)) is not str for field in text_fields)
        or any(type(value.get(field)) is not bool for field in boolean_fields)
        or any(type(value.get(field)) is not int for field in integer_fields)
        or not isinstance(value.get("blocking_reason_codes"), list)
        or any(
            type(code) is not str for code in value["blocking_reason_codes"]
        )
    ):
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_SCHEMA_INVALID"
        )
    quota = int(value["research_quota_bytes"])
    usage = int(value["research_usage_bytes"])
    if quota % 1024 != 0 or usage % 1024 != 0:
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_SCHEMA_INVALID"
        )
    replay_snapshot = {
        "native": {
            "research": {
                "quota_kib": quota // 1024,
                "usage_kib": usage // 1024,
                "file_quota": value["research_file_quota"],
                "files_used": value["research_files_used"],
            }
        },
        "dfs": {
            "research": {
                "total": value["research_filesystem_total_bytes"],
                "used": value["research_filesystem_used_bytes"],
                "available": value[
                    "research_filesystem_available_bytes"
                ],
            }
        },
        "native_capacity_snapshot_captures": value[
            "native_capacity_snapshot_captures"
        ],
        "native_quota_file_captures": value[
            "native_quota_file_captures"
        ],
        "capacity_command_captures": value["capacity_command_captures"],
        "pquota_command_captures": value["pquota_command_captures"],
        "findmnt_command_captures": value["findmnt_command_captures"],
        "df_command_captures": value["df_command_captures"],
        "pquota_display_crosscheck": value["pquota_display_crosscheck"],
    }
    expected = _build_fixed_r8u_r7d_tasks17_19_capacity(
        plan,
        replay_snapshot,
        r7d_runtime_commit=r7d_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        _demand_builder=_demand_builder,
    )
    if dict(value) != expected:
        raise PostReallocationCapacityError(
            "R8U_R7D_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def capture_and_validate_fixed_r8u_r7d_tasks17_19_capacity(
    plan: Mapping[str, Any],
    *,
    r7d_runtime_commit: str,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Capture exactly one live metadata snapshot and validate R7D capacity."""

    snapshot = _capture_current_capacity_snapshot(
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=process_runner,
    )
    value = build_fixed_r8u_r7d_tasks17_19_capacity(
        plan,
        snapshot,
        r7d_runtime_commit=r7d_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
    )
    return validate_fixed_r8u_r7d_tasks17_19_capacity(
        plan,
        value,
        r7d_runtime_commit=r7d_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
    )


# R7E is an additive observation/persistence epoch.  In particular, none of
# the R7D functions above call this code, and their historical behavior stays
# unchanged.  The R7E receipt records enough bounded metadata to distinguish
# command, parser, authority, and arithmetic failures without putting any raw
# quota or filesystem output in the aggregate artifact.
R8U_R7E_CAPACITY_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r7e_tasks17_19_capacity_observation_v1"
)
R8U_R7E_CAPACITY_STATUS_PASS = (
    "PASS_R8U_R7E_TASKS_17_19_REMAINING_CAPACITY"
)
R8U_R7E_CAPACITY_STATUS_DEFICIT = (
    "BLOCKED_R8U_R7E_QUANTIFIED_CAPACITY_DEFICIT"
)
R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX = (
    "BLOCKED_R8U_R7E_CAPACITY_OBSERVATION_"
)
R8U_R7E_COMMAND_DIAGNOSTIC_KEYS = frozenset(
    {
        "logical_role",
        "command_type",
        "command_ordinal",
        "registry_ordinal",
        "invocation_attempted",
        "exit_status",
        "exit_class",
        "capture_present",
        "capture_regular_file",
        "stdout_size_bytes",
        "stdout_sha256",
        "stdout_capture_regular_file",
        "stderr_size_bytes",
        "stderr_sha256",
        "stderr_capture_regular_file",
        "raw_capture_sha256",
        "parser_result",
        "failure_predicate",
        "source_error_code",
    }
)
R8U_R7E_FAILURE_DIAGNOSTIC_KEYS = frozenset(
    {
        "failure_code",
        "failure_stage",
        "failure_field",
        "failure_predicate",
        "command_type",
        "command_ordinal",
        "registry_ordinal",
        "command_exit_status",
        "command_exit_class",
        "capture_present",
        "capture_regular_file",
        "parser_result",
        "expected_value_category",
        "observed_value_category",
        "validation_class",
        "source_error_code",
        "raw_capture_sha256",
        "failure_before_capacity_arithmetic",
    }
)
R8U_R7E_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "r7e_runtime_commit",
        "preserved_old_control_evidence_bytes_baseline",
        "preserved_old_control_evidence_files_baseline",
        "confirmed_partial_artifact_bytes_baseline",
        "confirmed_partial_artifact_files_baseline",
        "capacity_observation_count",
        "native_quota_file_captures",
        "pquota_command_captures",
        "findmnt_command_captures",
        "df_command_captures",
        "du_command_captures",
        "pquota_command_invocation_attempts",
        "findmnt_command_invocation_attempts",
        "df_command_invocation_attempts",
        "du_command_invocation_attempts",
        "raw_capture_file_count",
        "raw_capture_root_owner_private",
        "command_diagnostics",
        "native_quota_size_bytes",
        "native_quota_sha256",
        "failure_diagnostic",
        "arithmetic_evaluated",
        "valid_numerical_deficit_calculated",
        "capacity_projection",
    }
)
_R8U_R7E_COMMAND_ROLES = (
    ("pquota", "PQUOTA", 1, 1),
    ("research_findmnt", "FINDMNT", 1, 2),
    ("backed_findmnt", "FINDMNT", 2, 3),
    ("research_df", "DF", 1, 4),
    ("backed_df", "DF", 2, 5),
)
_R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES = 2_000_000


class R8UR7ECapacityObservationError(PostReallocationCapacityError):
    """One field-specific R7E failure, optionally carrying a sealed receipt."""

    def __init__(
        self,
        code: str,
        *,
        diagnostic: Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
        receipt_sha256: str | None = None,
    ) -> None:
        super().__init__(code)
        self.diagnostic = dict(diagnostic)
        self.receipt = None if receipt is None else dict(receipt)
        self.receipt_sha256 = receipt_sha256


def _r8u_r7e_canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _r8u_r7e_sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _r8u_r7e_is_sha(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _r8u_r7e_private_directory(path: Path) -> os.stat_result:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or Path(os.path.abspath(path)) != path
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_DIRECTORY_INVALID"
        )
    _frozen_capacity._no_symlink_ancestors(
        path / ".r8u_r7e_nofollow_probe",
        "R8U_R7E_PRIVATE_DIRECTORY",
    )
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_DIRECTORY_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) not in {0o700, 0o2700}
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_DIRECTORY_INVALID"
        )
    return metadata


def _r8u_r7e_prepare_raw_capture_root(path: Path) -> os.stat_result:
    if not isinstance(path, Path) or not path.is_absolute():
        raise PostReallocationCapacityError(
            "R8U_R7E_RAW_CAPTURE_ROOT_INVALID"
        )
    if not os.path.lexists(path):
        _r8u_r7e_private_directory(path.parent)
        try:
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise PostReallocationCapacityError(
                "R8U_R7E_RAW_CAPTURE_ROOT_CREATE_FAILED"
            ) from exc
    try:
        metadata = _r8u_r7e_private_directory(path)
        entries = list(os.scandir(path))
    except PostReallocationCapacityError:
        raise
    except OSError as exc:
        raise PostReallocationCapacityError(
            "R8U_R7E_RAW_CAPTURE_ROOT_INVALID"
        ) from exc
    if entries:
        raise PostReallocationCapacityError(
            "R8U_R7E_RAW_CAPTURE_ROOT_NOT_EMPTY"
        )
    return metadata


def _r8u_r7e_write_new_private_bytes(path: Path, payload: bytes) -> str:
    if not isinstance(path, Path) or not path.is_absolute():
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_OUTPUT_PATH_INVALID"
        )
    parent = _r8u_r7e_private_directory(path.parent)
    if os.path.lexists(path):
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_OUTPUT_COLLISION"
        )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short private output write")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
    except Exception as exc:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_OUTPUT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size != len(payload)
        or (metadata.st_dev, metadata.st_ino, metadata.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or after.st_dev != parent.st_dev
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_OUTPUT_INVARIANT_FAILED"
        )
    verify = _frozen_capacity._read_regular(
        path,
        private=True,
        maximum=max(len(payload), 1),
    ) if payload else b""
    if verify != payload:
        raise PostReallocationCapacityError(
            "R8U_R7E_PRIVATE_OUTPUT_READBACK_MISMATCH"
        )
    return _r8u_r7e_sha(payload)


def write_r8u_r7e_capacity_receipt_no_clobber(
    path: Path,
    value: Mapping[str, Any],
) -> str:
    """Publish one canonical owner-private mode-0600 receipt, no-clobber."""

    if not isinstance(value, Mapping):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return _r8u_r7e_write_new_private_bytes(
        path,
        _r8u_r7e_canonical(dict(value)),
    )


def _r8u_r7e_preflight_receipt_no_clobber(path: Path) -> None:
    """Reject a receipt collision before consuming the live observation."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_PATH_INVALID"
        )
    _r8u_r7e_private_directory(path.parent)
    if os.path.lexists(path):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_COLLISION"
        )


def _r8u_r7e_failure_code(reason: str) -> str:
    if (
        type(reason) is not str
        or not reason
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
            for character in reason
        )
    ):
        reason = "INTERNAL_REASON_INVALID"
    return R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX + reason


def _r8u_r7e_failure_diagnostic(
    reason: str,
    *,
    failure_stage: str,
    failure_field: str,
    failure_predicate: str,
    command_type: str = "NONE",
    command_ordinal: int | None = None,
    registry_ordinal: int | None = None,
    command_exit_status: int | None = None,
    capture_present: bool = False,
    capture_regular_file: bool | None = None,
    parser_result: str = "NOT_EVALUATED",
    expected_value_category: str = "TRUSTED_FIXED_AUTHORITY",
    observed_value_category: str = "INVALID_OR_UNAVAILABLE",
    validation_class: str = "R8UR7ECapacityObservationError",
    source_error_code: str | None = None,
    raw_capture_sha256: str | None = None,
    failure_before_capacity_arithmetic: bool = True,
) -> dict[str, Any]:
    code = _r8u_r7e_failure_code(reason)
    return {
        "failure_code": code,
        "failure_stage": failure_stage,
        "failure_field": failure_field,
        "failure_predicate": failure_predicate,
        "command_type": command_type,
        "command_ordinal": command_ordinal,
        "registry_ordinal": registry_ordinal,
        "command_exit_status": command_exit_status,
        "command_exit_class": (
            "NOT_AVAILABLE"
            if command_exit_status is None
            else "ZERO" if command_exit_status == 0 else "NONZERO"
        ),
        "capture_present": capture_present,
        "capture_regular_file": capture_regular_file,
        "parser_result": parser_result,
        "expected_value_category": expected_value_category,
        "observed_value_category": observed_value_category,
        "validation_class": validation_class,
        "source_error_code": source_error_code,
        "raw_capture_sha256": raw_capture_sha256,
        "failure_before_capacity_arithmetic": (
            failure_before_capacity_arithmetic
        ),
    }


def _r8u_r7e_static_authority(
    plan: Mapping[str, Any],
    *,
    r7e_runtime_commit: str,
    preserved_old_evidence_bytes: int,
    preserved_old_evidence_files: int,
    confirmed_partial_artifact_bytes: int,
    confirmed_partial_artifact_files: int,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> tuple[str, dict[str, int]]:
    runtime_commit = _fixed_r8u_r7d_runtime_commit(r7e_runtime_commit)
    _demand_builder(plan)
    baselines = _fixed_r8u_r7d_baselines(
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
    )
    return runtime_commit, baselines


def _r8u_r7e_receipt_base(
    *,
    runtime_commit: str,
    baselines: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": R8U_R7E_CAPACITY_ARTIFACT_TYPE,
        "status": "",
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": (
            _frozen_capacity.R8U_ORIGINAL_PLAN_SHA256
        ),
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "r7e_runtime_commit": runtime_commit,
        **dict(baselines),
        "capacity_observation_count": 0,
        "native_quota_file_captures": 0,
        "pquota_command_captures": 0,
        "findmnt_command_captures": 0,
        "df_command_captures": 0,
        "du_command_captures": 0,
        "pquota_command_invocation_attempts": 0,
        "findmnt_command_invocation_attempts": 0,
        "df_command_invocation_attempts": 0,
        "du_command_invocation_attempts": 0,
        "raw_capture_file_count": 0,
        "raw_capture_root_owner_private": False,
        "command_diagnostics": [],
        "native_quota_size_bytes": None,
        "native_quota_sha256": None,
        "failure_diagnostic": None,
        "arithmetic_evaluated": False,
        "valid_numerical_deficit_calculated": False,
        "capacity_projection": None,
    }


def _r8u_r7e_static_receipt_authority(
    plan: Mapping[str, Any],
    *,
    r7e_runtime_commit: Any,
    preserved_old_evidence_bytes: Any,
    preserved_old_evidence_files: Any,
    confirmed_partial_artifact_bytes: Any,
    confirmed_partial_artifact_files: Any,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> tuple[
    dict[str, Any],
    str | None,
    dict[str, int] | None,
    dict[str, Any] | None,
]:
    """Classify static authority before any live observation is consumed."""

    baseline_inputs = (
        (
            "preserved_old_control_evidence_bytes_baseline",
            preserved_old_evidence_bytes,
        ),
        (
            "preserved_old_control_evidence_files_baseline",
            preserved_old_evidence_files,
        ),
        (
            "confirmed_partial_artifact_bytes_baseline",
            confirmed_partial_artifact_bytes,
        ),
        (
            "confirmed_partial_artifact_files_baseline",
            confirmed_partial_artifact_files,
        ),
    )
    safe_baselines = {
        field: value if type(value) is int and value >= 0 else 0
        for field, value in baseline_inputs
    }
    recorded_runtime = (
        r7e_runtime_commit
        if type(r7e_runtime_commit) is str
        else "INVALID_RUNTIME_COMMIT_TYPE"
    )
    receipt = _r8u_r7e_receipt_base(
        runtime_commit=recorded_runtime,
        baselines=safe_baselines,
    )
    try:
        runtime_commit = _fixed_r8u_r7d_runtime_commit(
            r7e_runtime_commit
        )
    except PostReallocationCapacityError as exc:
        return receipt, None, None, _r8u_r7e_failure_diagnostic(
            "RUNTIME_COMMIT_INVALID",
            failure_stage="STATIC",
            failure_field="r7e_runtime_commit",
            failure_predicate="RUNTIME_COMMIT_INVALID",
            expected_value_category="DISTINCT_FULL_GIT_COMMIT",
            observed_value_category=(
                "NONSTRING" if type(r7e_runtime_commit) is not str
                else "INVALID_OR_FORBIDDEN_COMMIT"
            ),
            validation_class=type(exc).__name__,
            source_error_code=exc.code,
        )
    try:
        _demand_builder(plan)
    except (PostReallocationCapacityError, TypeError, KeyError, ValueError) as exc:
        return receipt, None, None, _r8u_r7e_failure_diagnostic(
            "PLAN_SCOPE_MISMATCH",
            failure_stage="STATIC",
            failure_field="plan.tasks17_19_scope",
            failure_predicate="PLAN_SCOPE_MISMATCH",
            expected_value_category="FIXED_ORIGINAL_TASKS_17_19_PLAN",
            observed_value_category="PLAN_SCOPE_REJECTED",
            validation_class=type(exc).__name__,
            source_error_code=str(
                getattr(exc, "code", type(exc).__name__)
            ),
        )
    for field, value in baseline_inputs:
        if type(value) is not int or value < 0:
            return receipt, None, None, _r8u_r7e_failure_diagnostic(
                "BASELINE_AUTHORITY_INVALID",
                failure_stage="STATIC",
                failure_field=field,
                failure_predicate="NONNEGATIVE_INTEGER_REQUIRED",
                expected_value_category="NONNEGATIVE_INTEGER",
                observed_value_category=(
                    "NONINTEGER" if type(value) is not int else "NEGATIVE_INTEGER"
                ),
                validation_class="PostReallocationCapacityError",
                source_error_code="R8U_R7D_BASELINE_AUTHORITY_INVALID",
            )
    baselines = _fixed_r8u_r7d_baselines(
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
    )
    return receipt, runtime_commit, baselines, None


def _r8u_r7e_apply_failure(
    receipt: dict[str, Any],
    diagnostic: Mapping[str, Any],
) -> R8UR7ECapacityObservationError:
    value = dict(diagnostic)
    receipt["status"] = value["failure_code"]
    receipt["failure_diagnostic"] = value
    receipt["arithmetic_evaluated"] = not bool(
        value["failure_before_capacity_arithmetic"]
    )
    receipt["valid_numerical_deficit_calculated"] = False
    receipt["capacity_projection"] = None
    return R8UR7ECapacityObservationError(
        str(value["failure_code"]),
        diagnostic=value,
        receipt=receipt,
    )


def _r8u_r7e_command_diagnostic(
    *,
    role: str,
    command_type: str,
    command_ordinal: int,
    registry_ordinal: int,
) -> dict[str, Any]:
    return {
        "logical_role": role,
        "command_type": command_type,
        "command_ordinal": command_ordinal,
        "registry_ordinal": registry_ordinal,
        "invocation_attempted": False,
        "exit_status": None,
        "exit_class": "NOT_AVAILABLE",
        "capture_present": False,
        "capture_regular_file": False,
        "stdout_size_bytes": None,
        "stdout_sha256": None,
        "stdout_capture_regular_file": False,
        "stderr_size_bytes": None,
        "stderr_sha256": None,
        "stderr_capture_regular_file": False,
        "raw_capture_sha256": None,
        "parser_result": "NOT_EVALUATED",
        "failure_predicate": None,
        "source_error_code": None,
    }


def _r8u_r7e_capture_commands(
    authority: Any,
    raw_capture_root: Path,
    *,
    process_runner: Callable[..., Any] | None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, bytes],
    dict[str, Any] | None,
]:
    runner = process_runner or subprocess.run
    outputs: dict[str, bytes] = {}
    diagnostics: list[dict[str, Any]] = []
    first_failure: dict[str, Any] | None = None
    specifications = tuple(_frozen_capacity.CAPACITY_COMMAND_SPECS)
    if tuple(item.logical_role for item in specifications) != tuple(
        item[0] for item in _R8U_R7E_COMMAND_ROLES
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_COMMAND_REGISTRY_INVALID"
        )

    for specification, role_authority in zip(
        specifications,
        _R8U_R7E_COMMAND_ROLES,
        strict=True,
    ):
        role, command_type, command_ordinal, registry_ordinal = role_authority
        diagnostic = _r8u_r7e_command_diagnostic(
            role=role,
            command_type=command_type,
            command_ordinal=command_ordinal,
            registry_ordinal=registry_ordinal,
        )
        try:
            argv = _frozen_capacity._current_canary_command_argv(
                specification,
                authority,
            )
            executable = Path(argv[0])
            before = os.stat(executable, follow_symlinks=False)
        except (PostReallocationCapacityError, OSError) as exc:
            reason = f"{role.upper()}_COMMAND_AUTHORITY_INVALID"
            predicate = "COMMAND_AUTHORITY_INVALID"
            diagnostic["failure_predicate"] = predicate
            diagnostic["source_error_code"] = str(
                getattr(exc, "code", type(exc).__name__)
            )
            diagnostics.append(diagnostic)
            if first_failure is None:
                first_failure = _r8u_r7e_failure_diagnostic(
                    reason,
                    failure_stage="COMMAND",
                    failure_field=f"{role}.executable_authority",
                    failure_predicate=predicate,
                    command_type=command_type,
                    command_ordinal=command_ordinal,
                    registry_ordinal=registry_ordinal,
                    capture_regular_file=False,
                    expected_value_category="STABLE_FIXED_EXECUTABLE_AUTHORITY",
                    observed_value_category="AUTHORITY_UNAVAILABLE",
                    validation_class=type(exc).__name__,
                    source_error_code=str(
                        getattr(exc, "code", type(exc).__name__)
                    ),
                )
            continue
        result: Any = None
        runner_failed = False
        diagnostic["invocation_attempted"] = True
        try:
            result = runner(
                list(argv),
                capture_output=True,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                },
                check=False,
            )
        except Exception as exc:
            runner_failed = True
            diagnostic["source_error_code"] = type(exc).__name__
        try:
            after = os.stat(executable, follow_symlinks=False)
            executable_unchanged = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        except OSError:
            executable_unchanged = False

        reason: str | None = None
        predicate: str | None = None
        if runner_failed:
            reason = f"{role.upper()}_COMMAND_CAPTURE_MISSING"
            predicate = "COMMAND_CAPTURE_MISSING"
        else:
            returncode = getattr(result, "returncode", None)
            stdout = getattr(result, "stdout", None)
            stderr = getattr(result, "stderr", None)
            if (
                type(returncode) is not int
                or type(stdout) is not bytes
                or type(stderr) is not bytes
            ):
                reason = f"{role.upper()}_COMMAND_CAPTURE_MISSING"
                predicate = "COMMAND_CAPTURE_MISSING"
                diagnostic["source_error_code"] = (
                    "PROCESS_RESULT_SCHEMA_INVALID"
                )
            else:
                diagnostic["exit_status"] = returncode
                diagnostic["exit_class"] = (
                    "ZERO" if returncode == 0 else "NONZERO"
                )
                diagnostic["capture_present"] = True
                diagnostic["stdout_size_bytes"] = len(stdout)
                diagnostic["stdout_sha256"] = _r8u_r7e_sha(stdout)
                diagnostic["stderr_size_bytes"] = len(stderr)
                diagnostic["stderr_sha256"] = _r8u_r7e_sha(stderr)
                diagnostic["raw_capture_sha256"] = _r8u_r7e_sha(
                    _r8u_r7e_canonical(
                        {
                            "stdout_size_bytes": len(stdout),
                            "stdout_sha256": diagnostic["stdout_sha256"],
                            "stderr_size_bytes": len(stderr),
                            "stderr_sha256": diagnostic["stderr_sha256"],
                        }
                    )
                )
                if (
                    len(stdout) > _R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES
                    or len(stderr) > _R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES
                ):
                    reason = f"{role.upper()}_COMMAND_OUTPUT_OVERSIZED"
                    predicate = "COMMAND_OUTPUT_OVERSIZED"
                    diagnostic["source_error_code"] = (
                        "COMMAND_OUTPUT_OVERSIZED"
                    )
                else:
                    stdout_path = raw_capture_root / (
                        f"{registry_ordinal:02d}_{role}.stdout.bin"
                    )
                    stderr_path = raw_capture_root / (
                        f"{registry_ordinal:02d}_{role}.stderr.bin"
                    )
                    try:
                        stdout_digest = _r8u_r7e_write_new_private_bytes(
                            stdout_path,
                            stdout,
                        )
                        diagnostic["stdout_capture_regular_file"] = (
                            stdout_digest == diagnostic["stdout_sha256"]
                        )
                    except PostReallocationCapacityError as exc:
                        diagnostic["source_error_code"] = exc.code
                    try:
                        stderr_digest = _r8u_r7e_write_new_private_bytes(
                            stderr_path,
                            stderr,
                        )
                        diagnostic["stderr_capture_regular_file"] = (
                            stderr_digest == diagnostic["stderr_sha256"]
                        )
                    except PostReallocationCapacityError as exc:
                        if diagnostic["source_error_code"] is None:
                            diagnostic["source_error_code"] = exc.code
                    diagnostic["capture_regular_file"] = (
                        diagnostic["stdout_capture_regular_file"] is True
                        and diagnostic["stderr_capture_regular_file"] is True
                    )
                    if diagnostic["capture_regular_file"] is not True:
                        reason = (
                            f"{role.upper()}_COMMAND_CAPTURE_NOT_REGULAR"
                        )
                        predicate = "COMMAND_CAPTURE_NOT_REGULAR"
                    elif not executable_unchanged:
                        reason = f"{role.upper()}_COMMAND_AUTHORITY_CHANGED"
                        predicate = "COMMAND_AUTHORITY_CHANGED"
                        diagnostic["source_error_code"] = (
                            "TOOL_CHANGED_DURING_CAPTURE"
                        )
                    elif returncode != 0:
                        reason = f"{role.upper()}_COMMAND_EXIT_NONZERO"
                        predicate = "COMMAND_EXIT_NONZERO"
                        diagnostic["source_error_code"] = (
                            "COMMAND_NONZERO_EXIT"
                        )
                    elif stderr:
                        reason = f"{role.upper()}_COMMAND_STDERR_PRESENT"
                        predicate = "COMMAND_STDERR_PRESENT"
                        diagnostic["source_error_code"] = (
                            "COMMAND_STDERR_PRESENT"
                        )
                    else:
                        try:
                            stdout.decode("utf-8")
                            stderr.decode("utf-8")
                        except UnicodeDecodeError:
                            reason = (
                                f"{role.upper()}_COMMAND_OUTPUT_NOT_UTF8"
                            )
                            predicate = "COMMAND_OUTPUT_NOT_UTF8"
                            diagnostic["source_error_code"] = (
                                "COMMAND_OUTPUT_NOT_UTF8"
                            )
                        else:
                            outputs[role] = stdout
        diagnostic["failure_predicate"] = predicate
        diagnostics.append(diagnostic)
        if first_failure is None and reason is not None:
            first_failure = _r8u_r7e_failure_diagnostic(
                reason,
                failure_stage="COMMAND",
                failure_field=f"{role}.capture",
                failure_predicate=str(predicate),
                command_type=command_type,
                command_ordinal=command_ordinal,
                registry_ordinal=registry_ordinal,
                command_exit_status=diagnostic["exit_status"],
                capture_present=bool(diagnostic["capture_present"]),
                capture_regular_file=bool(
                    diagnostic["capture_regular_file"]
                ),
                expected_value_category=(
                    "ZERO_EXIT_EMPTY_STDERR_BOUNDED_UTF8_REGULAR_CAPTURE"
                ),
                observed_value_category=str(predicate),
                source_error_code=diagnostic["source_error_code"],
                raw_capture_sha256=diagnostic["raw_capture_sha256"],
            )
    return diagnostics, outputs, first_failure


def _r8u_r7e_parser_failure(
    receipt: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    role: str,
    predicate: str,
    source_error: Exception,
) -> R8UR7ECapacityObservationError:
    role_index = {
        item[0]: index for index, item in enumerate(_R8U_R7E_COMMAND_ROLES)
    }[role]
    role_authority = _R8U_R7E_COMMAND_ROLES[role_index]
    diagnostic = diagnostics[role_index]
    diagnostic["parser_result"] = "FAIL"
    diagnostic["failure_predicate"] = predicate
    source_code = getattr(source_error, "code", type(source_error).__name__)
    diagnostic["source_error_code"] = str(source_code)
    if predicate == "PQUOTA_DISPLAY_CONTRADICTION":
        reason = predicate
    elif predicate in {
        "PQUOTA_PARSE_FAILURE",
        "FINDMNT_PARSE_FAILURE",
        "DF_PARSE_FAILURE",
    }:
        reason = f"{role.upper()}_PARSE_FAILURE"
    else:
        reason = f"{role.upper()}_{predicate}"
    return _r8u_r7e_apply_failure(
        receipt,
        _r8u_r7e_failure_diagnostic(
            reason,
            failure_stage="PARSER",
            failure_field=f"{role}.parsed_output",
            failure_predicate=predicate,
            command_type=role_authority[1],
            command_ordinal=role_authority[2],
            registry_ordinal=role_authority[3],
            command_exit_status=diagnostic["exit_status"],
            capture_present=bool(diagnostic["capture_present"]),
            capture_regular_file=bool(
                diagnostic["capture_regular_file"]
            ),
            parser_result="FAIL",
            expected_value_category="STRICT_CANONICAL_PARSED_VALUE",
            observed_value_category="PARSER_REJECTED",
            validation_class=type(source_error).__name__,
            source_error_code=str(source_code),
            raw_capture_sha256=diagnostic["raw_capture_sha256"],
        ),
    )


def capture_fixed_r8u_r7e_tasks17_19_capacity(
    plan: Mapping[str, Any],
    *,
    r7e_runtime_commit: str,
    raw_capture_root: Path,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    authority: Any = DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
    process_runner: Callable[..., Any] | None = None,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Perform one exact pquota1/findmnt2/df2/du0 R7E observation."""

    receipt, runtime_commit, baselines, static_failure = (
        _r8u_r7e_static_receipt_authority(
        plan,
        r7e_runtime_commit=r7e_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        _demand_builder=_demand_builder,
        )
    )
    if static_failure is not None:
        raise _r8u_r7e_apply_failure(receipt, static_failure)
    if runtime_commit is None or baselines is None:
        raise PostReallocationCapacityError(
            "R8U_R7E_STATIC_AUTHORITY_INTERNAL_INVALID"
        )
    try:
        _r8u_r7e_prepare_raw_capture_root(raw_capture_root)
        receipt["raw_capture_root_owner_private"] = True
    except PostReallocationCapacityError as exc:
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                "RAW_CAPTURE_ROOT_AUTHORITY_MISMATCH",
                failure_stage="RAW_CAPTURE_ROOT",
                failure_field="raw_capture_root",
                failure_predicate="PRIVATE_EMPTY_DIRECTORY_REQUIRED",
                source_error_code=exc.code,
            ),
        ) from exc
    try:
        production = (
            _frozen_capacity._validate_current_canary_headroom_authority(
                authority
            )
        )
    except PostReallocationCapacityError as exc:
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                "CAPACITY_AUTHORITY_MISMATCH",
                failure_stage="CAPACITY_AUTHORITY",
                failure_field="capacity_authority",
                failure_predicate="FIXED_PATH_AUTHORITY_REQUIRED",
                source_error_code=exc.code,
            ),
        ) from exc

    diagnostics, outputs, command_failure = _r8u_r7e_capture_commands(
        authority,
        raw_capture_root,
        process_runner=process_runner,
    )
    receipt.update(
        {
            "capacity_observation_count": 1,
            "pquota_command_captures": sum(
                item["command_type"] == "PQUOTA"
                and item["capture_present"] is True
                for item in diagnostics
            ),
            "findmnt_command_captures": sum(
                item["command_type"] == "FINDMNT"
                and item["capture_present"] is True
                for item in diagnostics
            ),
            "df_command_captures": sum(
                item["command_type"] == "DF"
                and item["capture_present"] is True
                for item in diagnostics
            ),
            "du_command_captures": 0,
            "pquota_command_invocation_attempts": sum(
                item["command_type"] == "PQUOTA"
                and item["invocation_attempted"] is True
                for item in diagnostics
            ),
            "findmnt_command_invocation_attempts": sum(
                item["command_type"] == "FINDMNT"
                and item["invocation_attempted"] is True
                for item in diagnostics
            ),
            "df_command_invocation_attempts": sum(
                item["command_type"] == "DF"
                and item["invocation_attempted"] is True
                for item in diagnostics
            ),
            "du_command_invocation_attempts": 0,
            "raw_capture_file_count": sum(
                int(item["stdout_capture_regular_file"])
                + int(item["stderr_capture_regular_file"])
                for item in diagnostics
            ),
            "command_diagnostics": diagnostics,
        }
    )
    if command_failure is not None:
        raise _r8u_r7e_apply_failure(receipt, command_failure)

    try:
        native_payload = _frozen_capacity._read_regular(
            authority.native_quota_path,
            maximum=64_000_000,
        )
        receipt["native_quota_file_captures"] = 1
        receipt["native_quota_size_bytes"] = len(native_payload)
        receipt["native_quota_sha256"] = _r8u_r7e_sha(native_payload)
        native = _frozen_capacity._parse_native_quota_rows(native_payload)
        if (
            int(native["research"]["quota_kib"])
            < EXPECTED_RESEARCH_QUOTA_KIB
            or int(native["research"]["file_quota"])
            < EXPECTED_RESEARCH_FILE_QUOTA
            or int(native["backed"]["quota_kib"])
            != _frozen_capacity.EXPECTED_BACKED_QUOTA_KIB
            or int(native["backed"]["file_quota"])
            != _frozen_capacity.EXPECTED_BACKED_FILE_QUOTA
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_NATIVE_QUOTA_ALLOCATION_AUTHORITY_MISMATCH"
            )
    except PostReallocationCapacityError as exc:
        native_read_error = receipt["native_quota_file_captures"] == 0
        numeric_error = exc.code in {
            "NATIVE_QUOTA_INTEGER_INVALID",
            "NATIVE_QUOTA_USAGE_INVALID",
            "NATIVE_QUOTA_USAGE_EXCEEDS_ALLOCATION",
        }
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                (
                    "NUMERIC_VALUE_INVALID"
                    if numeric_error
                    else "QUOTA_AUTHORITY_MISMATCH"
                ),
                failure_stage=(
                    "NATIVE_QUOTA_READ"
                    if native_read_error
                    else "NATIVE_QUOTA"
                ),
                failure_field=(
                    "native_quota_file.capture"
                    if native_read_error
                    else (
                        "native_quota_file.numeric_values"
                        if numeric_error
                        else "native_quota_file.allocation_authority"
                    )
                ),
                failure_predicate=(
                    "QUOTA_AUTHORITY_READ_FAILED"
                    if native_read_error
                    else (
                        "NUMERIC_VALUE_INVALID"
                        if numeric_error
                        else "QUOTA_AUTHORITY_MISMATCH"
                    )
                ),
                expected_value_category=(
                    "BOUNDED_REGULAR_NATIVE_QUOTA_AUTHORITY"
                    if native_read_error
                    else (
                        "NONNEGATIVE_BOUNDED_INTEGER_QUOTA_VALUES"
                        if numeric_error
                        else "STRICT_NATIVE_QUOTA_ROWS_AND_ALLOCATIONS"
                    )
                ),
                validation_class=type(exc).__name__,
                source_error_code=exc.code,
            ),
        ) from exc

    def decoded(role: str) -> str:
        return outputs[role].decode("utf-8")

    try:
        display = _frozen_capacity._parse_pquota(
            decoded("pquota"),
            native,
            command_available=True,
        )
        if display.get("status") != DISPLAY_CROSSCHECK_PASS:
            raise PostReallocationCapacityError(
                (
                    "PQUOTA_DISPLAY_CONTRADICTION"
                    if display.get("status")
                    == _frozen_capacity.DISPLAY_CROSSCHECK_FAIL
                    else "PQUOTA_DISPLAY_NOT_EXACT_PASS"
                )
            )
        diagnostics[0]["parser_result"] = "PASS"
    except (PostReallocationCapacityError, KeyError, UnicodeError) as exc:
        predicate = (
            "PQUOTA_DISPLAY_CONTRADICTION"
            if getattr(exc, "code", "")
            == "PQUOTA_DISPLAY_CONTRADICTION"
            else "PQUOTA_PARSE_FAILURE"
        )
        raise _r8u_r7e_parser_failure(
            receipt,
            diagnostics,
            role="pquota",
            predicate=predicate,
            source_error=exc,
        ) from exc

    mounts: dict[str, Mapping[str, Any]] = {}
    for role, target_role in (
        ("research_findmnt", "research"),
        ("backed_findmnt", "backed"),
    ):
        try:
            mounts[target_role] = _frozen_capacity._parse_findmnt(
                decoded(role),
                getattr(authority, f"{target_role}_path"),
            )
            diagnostic_index = 1 if role == "research_findmnt" else 2
            diagnostics[diagnostic_index]["parser_result"] = "PASS"
        except (PostReallocationCapacityError, KeyError, UnicodeError) as exc:
            predicate = (
                "MOUNT_RESOLUTION_AMBIGUOUS"
                if getattr(exc, "code", "")
                == "FINDMNT_ROW_COUNT_INVALID"
                else "FINDMNT_PARSE_FAILURE"
            )
            raise _r8u_r7e_parser_failure(
                receipt,
                diagnostics,
                role=role,
                predicate=predicate,
                source_error=exc,
            ) from exc

    try:
        paths = {
            "research": _frozen_capacity._path_identity(
                authority.research_path
            ),
            "backed": _frozen_capacity._path_identity(authority.backed_path),
        }
        if production:
            _frozen_capacity._validate_pquota_restricted_mount_reconciliation(
                native=native,
                paths=paths,
                mounts=mounts,
            )
        elif any(
            paths[role]["is_symlink"] is not False
            or mounts[role]["bind"] is not False
            or mounts[role]["fsroot"] != "/"
            or native[role]["native_name_sha256"]
            != _r8u_r7e_sha(
                _frozen_capacity.EXPECTED_NATIVE_ROWS[role].encode()
            )
            for role in ("research", "backed")
        ):
            raise PostReallocationCapacityError(
                "NONPRODUCTION_MOUNT_RECONCILIATION_FAILED"
            )
    except (PostReallocationCapacityError, KeyError, OSError) as exc:
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                "MOUNT_AUTHORITY_MISMATCH",
                failure_stage="MOUNT",
                failure_field="restricted_mount_reconciliation",
                failure_predicate="MOUNT_AUTHORITY_MISMATCH",
                expected_value_category=(
                    "NATIVE_FILESET_TO_NONBIND_RESTRICTED_MOUNT"
                ),
                validation_class=type(exc).__name__,
                source_error_code=str(
                    getattr(exc, "code", type(exc).__name__)
                ),
            ),
        ) from exc

    dfs: dict[str, Mapping[str, int]] = {}
    for role, target_role, diagnostic_index in (
        ("research_df", "research", 3),
        ("backed_df", "backed", 4),
    ):
        try:
            dfs[target_role] = _frozen_capacity._parse_df(
                decoded(role),
                mounts[target_role],
            )
            diagnostics[diagnostic_index]["parser_result"] = "PASS"
        except (PostReallocationCapacityError, KeyError, UnicodeError) as exc:
            source_code = str(
                getattr(exc, "code", type(exc).__name__)
            )
            if source_code == "DF_BYTES_DO_NOT_RECONCILE":
                predicate = "FILESYSTEM_ARITHMETIC_INVALID"
            elif source_code == "DF_BYTES_INVALID":
                predicate = "NUMERIC_VALUE_INVALID"
            else:
                predicate = "DF_PARSE_FAILURE"
            raise _r8u_r7e_parser_failure(
                receipt,
                diagnostics,
                role=role,
                predicate=predicate,
                source_error=exc,
            ) from exc

    snapshot = {
        "native": native,
        "dfs": dfs,
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "pquota_display_crosscheck": DISPLAY_CROSSCHECK_PASS,
    }
    try:
        projection = build_fixed_r8u_r7d_tasks17_19_capacity(
            plan,
            snapshot,
            r7d_runtime_commit=runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=(
                confirmed_partial_artifact_bytes
            ),
            confirmed_partial_artifact_files=(
                confirmed_partial_artifact_files
            ),
            _demand_builder=_demand_builder,
        )
        projection = validate_fixed_r8u_r7d_tasks17_19_capacity(
            plan,
            projection,
            r7d_runtime_commit=runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=(
                confirmed_partial_artifact_bytes
            ),
            confirmed_partial_artifact_files=(
                confirmed_partial_artifact_files
            ),
            _demand_builder=_demand_builder,
        )
    except PostReallocationCapacityError as exc:
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                failure_stage="ARITHMETIC",
                failure_field="capacity_projection",
                failure_predicate="CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                expected_value_category="EXACT_R7D_INTEGER_REPLAY",
                observed_value_category="ARITHMETIC_REPLAY_REJECTED",
                validation_class=type(exc).__name__,
                source_error_code=exc.code,
                failure_before_capacity_arithmetic=False,
            ),
        ) from exc

    receipt["capacity_projection"] = projection
    receipt["arithmetic_evaluated"] = True
    if projection["status"] == R8U_R7D_CAPACITY_STATUS_PASS:
        receipt["status"] = R8U_R7E_CAPACITY_STATUS_PASS
        receipt["valid_numerical_deficit_calculated"] = False
    elif projection["status"] == R8U_R7D_CAPACITY_STATUS_BLOCKED:
        receipt["status"] = R8U_R7E_CAPACITY_STATUS_DEFICIT
        receipt["valid_numerical_deficit_calculated"] = True
    else:
        raise _r8u_r7e_apply_failure(
            receipt,
            _r8u_r7e_failure_diagnostic(
                "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                failure_stage="ARITHMETIC",
                failure_field="capacity_projection.status",
                failure_predicate="CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                source_error_code="R8U_R7D_CAPACITY_STATUS_INVALID",
                failure_before_capacity_arithmetic=False,
            ),
        )
    if set(receipt) != R8U_R7E_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return receipt


def _r8u_r7e_validate_command_diagnostics(
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) not in {0, 5}:
        raise PostReallocationCapacityError(
            "R8U_R7E_COMMAND_DIAGNOSTIC_SCHEMA_INVALID"
        )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, Mapping)
            or set(item) != R8U_R7E_COMMAND_DIAGNOSTIC_KEYS
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_COMMAND_DIAGNOSTIC_SCHEMA_INVALID"
            )
        role, command_type, command_ordinal, registry_ordinal = (
            _R8U_R7E_COMMAND_ROLES[index]
        )
        exit_status = item["exit_status"]
        integer_or_none = (
            exit_status is None or type(exit_status) is int
        )
        stream_fields_valid = all(
            (
                item[f"{stream}_size_bytes"] is None
                and item[f"{stream}_sha256"] is None
            )
            or (
                type(item[f"{stream}_size_bytes"]) is int
                and item[f"{stream}_size_bytes"] >= 0
                and _r8u_r7e_is_sha(item[f"{stream}_sha256"])
            )
            for stream in ("stdout", "stderr")
        )
        if (
            item["logical_role"] != role
            or item["command_type"] != command_type
            or item["command_ordinal"] != command_ordinal
            or item["registry_ordinal"] != registry_ordinal
            or type(item["invocation_attempted"]) is not bool
            or not integer_or_none
            or item["exit_class"]
            != (
                "NOT_AVAILABLE"
                if exit_status is None
                else "ZERO" if exit_status == 0 else "NONZERO"
            )
            or type(item["capture_present"]) is not bool
            or type(item["capture_regular_file"]) is not bool
            or type(item["stdout_capture_regular_file"]) is not bool
            or type(item["stderr_capture_regular_file"]) is not bool
            or not stream_fields_valid
            or (
                item["raw_capture_sha256"] is not None
                and not _r8u_r7e_is_sha(item["raw_capture_sha256"])
            )
            or item["parser_result"]
            not in {"NOT_EVALUATED", "PASS", "FAIL"}
            or (
                item["failure_predicate"] is not None
                and type(item["failure_predicate"]) is not str
            )
            or (
                item["source_error_code"] is not None
                and type(item["source_error_code"]) is not str
            )
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_COMMAND_DIAGNOSTIC_SCHEMA_INVALID"
            )
        if item["capture_present"]:
            expected_capture_sha = _r8u_r7e_sha(
                _r8u_r7e_canonical(
                    {
                        "stdout_size_bytes": item["stdout_size_bytes"],
                        "stdout_sha256": item["stdout_sha256"],
                        "stderr_size_bytes": item["stderr_size_bytes"],
                        "stderr_sha256": item["stderr_sha256"],
                    }
                )
            )
            if item["raw_capture_sha256"] != expected_capture_sha:
                raise PostReallocationCapacityError(
                    "R8U_R7E_COMMAND_DIAGNOSTIC_DIGEST_INVALID"
                )
        elif any(
            item[field] is not None
            for field in (
                "stdout_size_bytes",
                "stdout_sha256",
                "stderr_size_bytes",
                "stderr_sha256",
                "raw_capture_sha256",
            )
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_COMMAND_DIAGNOSTIC_CAPTURE_INVALID"
            )
        if (
            item["capture_regular_file"]
            is not (
                item["stdout_capture_regular_file"] is True
                and item["stderr_capture_regular_file"] is True
            )
            or (
                not item["invocation_attempted"]
                and (
                    item["exit_status"] is not None
                    or item["capture_present"] is not False
                    or item["stdout_capture_regular_file"] is not False
                    or item["stderr_capture_regular_file"] is not False
                    or item["parser_result"] != "NOT_EVALUATED"
                )
            )
            or (
                item["capture_present"]
                and item["invocation_attempted"] is not True
            )
            or (
                (
                    item["stdout_capture_regular_file"] is True
                    or item["stderr_capture_regular_file"] is True
                )
                and item["capture_present"] is not True
            )
            or (
                item["parser_result"] in {"PASS", "FAIL"}
                and item["capture_regular_file"] is not True
            )
            or (
                item["parser_result"] == "PASS"
                and item["failure_predicate"] is not None
            )
            or (
                item["failure_predicate"] is None
                and item["source_error_code"] is not None
            )
            or (
                item["failure_predicate"] is not None
                and not item["source_error_code"]
            )
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_COMMAND_DIAGNOSTIC_CAPTURE_INVALID"
            )
        result.append(dict(item))
    return result


def _r8u_r7e_validate_raw_capture_files(
    raw_capture_root: Path | None,
    diagnostics: Sequence[Mapping[str, Any]],
    *,
    failure_stage: str | None,
) -> None:
    """Optionally bind aggregate stream hashes to private raw files."""

    if raw_capture_root is None:
        return
    if not diagnostics and not os.path.lexists(raw_capture_root):
        return
    if failure_stage == "RAW_CAPTURE_ROOT":
        return
    root_metadata = _r8u_r7e_private_directory(raw_capture_root)
    allowed_names = {
        f"{item['registry_ordinal']:02d}_{item['logical_role']}.{stream}.bin"
        for item in diagnostics
        for stream in ("stdout", "stderr")
    }
    required_names = {
        f"{item['registry_ordinal']:02d}_{item['logical_role']}.{stream}.bin"
        for item in diagnostics
        for stream in ("stdout", "stderr")
        if item[f"{stream}_capture_regular_file"] is True
    }
    try:
        entries = {entry.name: entry for entry in os.scandir(raw_capture_root)}
    except OSError as exc:
        raise PostReallocationCapacityError(
            "R8U_R7E_RAW_CAPTURE_REPLAY_INVALID"
        ) from exc
    if (
        not set(entries).issubset(allowed_names)
        or set(entries) != required_names
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_RAW_CAPTURE_REPLAY_INVALID"
        )
    for item in diagnostics:
        for stream in ("stdout", "stderr"):
            name = (
                f"{item['registry_ordinal']:02d}_"
                f"{item['logical_role']}.{stream}.bin"
            )
            required = item[f"{stream}_capture_regular_file"] is True
            if not required:
                continue
            if name not in entries:
                raise PostReallocationCapacityError(
                    "R8U_R7E_RAW_CAPTURE_REPLAY_MISSING"
                )
            path = raw_capture_root / name
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = -1
            try:
                descriptor = os.open(path, flags)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_nlink != 1
                    or metadata.st_dev != root_metadata.st_dev
                    or metadata.st_size != item[f"{stream}_size_bytes"]
                ):
                    raise PostReallocationCapacityError(
                        "R8U_R7E_RAW_CAPTURE_REPLAY_INVARIANT_INVALID"
                    )
                payload = b""
                maximum = _R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES
                while len(payload) <= maximum:
                    block = os.read(
                        descriptor,
                        min(1_048_576, maximum + 1 - len(payload)),
                    )
                    if not block:
                        break
                    payload += block
                after = os.fstat(descriptor)
            except PostReallocationCapacityError:
                raise
            except OSError as exc:
                raise PostReallocationCapacityError(
                    "R8U_R7E_RAW_CAPTURE_REPLAY_INVALID"
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if (
                len(payload) != metadata.st_size
                or _r8u_r7e_sha(payload) != item[f"{stream}_sha256"]
                or (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                )
            ):
                raise PostReallocationCapacityError(
                    "R8U_R7E_RAW_CAPTURE_REPLAY_DIGEST_INVALID"
                )
            if (
                stream == "stdout"
                and item["failure_predicate"]
                == "COMMAND_OUTPUT_NOT_UTF8"
            ):
                try:
                    payload.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    raise PostReallocationCapacityError(
                        "R8U_R7E_RAW_CAPTURE_REPLAY_UTF8_INVALID"
                    )


def _r8u_r7e_validate_failure_diagnostic(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R7E_FAILURE_DIAGNOSTIC_KEYS
        or type(value["failure_code"]) is not str
        or not value["failure_code"].startswith(
            R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
        )
        or any(
            type(value[field]) is not str
            for field in (
                "failure_stage",
                "failure_field",
                "failure_predicate",
                "command_type",
                "command_exit_class",
                "parser_result",
                "expected_value_category",
                "observed_value_category",
                "validation_class",
            )
        )
        or value["failure_stage"] not in {
            "STATIC",
            "RAW_CAPTURE_ROOT",
            "CAPACITY_AUTHORITY",
        "COMMAND",
            "NATIVE_QUOTA_READ",
            "NATIVE_QUOTA",
            "PARSER",
            "MOUNT",
            "ARITHMETIC",
        }
        or value["command_type"] not in {"PQUOTA", "FINDMNT", "DF", "NONE"}
        or value["command_exit_class"]
        not in {"ZERO", "NONZERO", "NOT_AVAILABLE"}
        or value["parser_result"]
        not in {"NOT_EVALUATED", "PASS", "FAIL"}
        or (
            value["command_ordinal"] is not None
            and type(value["command_ordinal"]) is not int
        )
        or (
            value["registry_ordinal"] is not None
            and type(value["registry_ordinal"]) is not int
        )
        or (
            value["command_exit_status"] is not None
            and type(value["command_exit_status"]) is not int
        )
        or type(value["capture_present"]) is not bool
        or value["capture_regular_file"] is not None
        and type(value["capture_regular_file"]) is not bool
        or value["source_error_code"] is not None
        and type(value["source_error_code"]) is not str
        or value["raw_capture_sha256"] is not None
        and not _r8u_r7e_is_sha(value["raw_capture_sha256"])
        or type(value["failure_before_capacity_arithmetic"]) is not bool
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_FAILURE_DIAGNOSTIC_SCHEMA_INVALID"
        )
    exit_status = value["command_exit_status"]
    if value["command_exit_class"] != (
        "NOT_AVAILABLE"
        if exit_status is None
        else "ZERO" if exit_status == 0 else "NONZERO"
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_FAILURE_DIAGNOSTIC_SCHEMA_INVALID"
        )
    return dict(value)


def validate_fixed_r8u_r7e_tasks17_19_capacity(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    r7e_runtime_commit: Any,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    raw_capture_root: Path | None = None,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Purely validate/replay a closed R7E success, deficit, or failure."""

    if not isinstance(value, Mapping) or set(value) != R8U_R7E_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    static_receipt, runtime_commit, baselines, static_failure = (
        _r8u_r7e_static_receipt_authority(
            plan,
            r7e_runtime_commit=r7e_runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=(
                confirmed_partial_artifact_bytes
            ),
            confirmed_partial_artifact_files=(
                confirmed_partial_artifact_files
            ),
            _demand_builder=_demand_builder,
        )
    )
    if static_failure is not None:
        expected_exception = _r8u_r7e_apply_failure(
            static_receipt,
            static_failure,
        )
        expected = expected_exception.receipt
        if (
            expected is None
            or _r8u_r7e_canonical(dict(value))
            != _r8u_r7e_canonical(expected)
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_STATIC_FAILURE_RECEIPT_INVALID"
            )
        return dict(value)
    if runtime_commit is None or baselines is None:
        raise PostReallocationCapacityError(
            "R8U_R7E_STATIC_AUTHORITY_INTERNAL_INVALID"
        )
    integer_fields = {
        "schema_version",
        "preserved_old_control_evidence_bytes_baseline",
        "preserved_old_control_evidence_files_baseline",
        "confirmed_partial_artifact_bytes_baseline",
        "confirmed_partial_artifact_files_baseline",
        "capacity_observation_count",
        "native_quota_file_captures",
        "pquota_command_captures",
        "findmnt_command_captures",
        "df_command_captures",
        "du_command_captures",
        "pquota_command_invocation_attempts",
        "findmnt_command_invocation_attempts",
        "df_command_invocation_attempts",
        "du_command_invocation_attempts",
        "raw_capture_file_count",
    }
    if (
        any(type(value[field]) is not int for field in integer_fields)
        or any(int(value[field]) < 0 for field in integer_fields)
        or type(value["raw_capture_root_owner_private"]) is not bool
        or type(value["arithmetic_evaluated"]) is not bool
        or type(value["valid_numerical_deficit_calculated"]) is not bool
        or value["native_quota_size_bytes"] is not None
        and type(value["native_quota_size_bytes"]) is not int
        or value["native_quota_sha256"] is not None
        and not _r8u_r7e_is_sha(value["native_quota_sha256"])
        or type(value["status"]) is not str
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    if (
        value["schema_version"] != 1
        or value["artifact_type"] != R8U_R7E_CAPACITY_ARTIFACT_TYPE
        or value["original_attempt_id"] != R8U_ORIGINAL_ATTEMPT_ID
        or value["original_plan_sha256"]
        != _frozen_capacity.R8U_ORIGINAL_PLAN_SHA256
        or value["original_scientific_governing_commit"]
        != R8U_ORIGINAL_SCIENTIFIC_COMMIT
        or value["r7e_runtime_commit"] != runtime_commit
        or any(value[field] != expected for field, expected in baselines.items())
        or value["du_command_captures"] != 0
        or value["du_command_invocation_attempts"] != 0
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_RECEIPT_AUTHORITY_INVALID"
        )
    diagnostics = _r8u_r7e_validate_command_diagnostics(
        value["command_diagnostics"]
    )
    expected_counts = {
        "capacity_observation_count": 1 if diagnostics else 0,
        "pquota_command_captures": sum(
            item["command_type"] == "PQUOTA"
            and item["capture_present"] is True
            for item in diagnostics
        ),
        "findmnt_command_captures": sum(
            item["command_type"] == "FINDMNT"
            and item["capture_present"] is True
            for item in diagnostics
        ),
        "df_command_captures": sum(
            item["command_type"] == "DF"
            and item["capture_present"] is True
            for item in diagnostics
        ),
        "pquota_command_invocation_attempts": sum(
            item["command_type"] == "PQUOTA"
            and item["invocation_attempted"] is True
            for item in diagnostics
        ),
        "findmnt_command_invocation_attempts": sum(
            item["command_type"] == "FINDMNT"
            and item["invocation_attempted"] is True
            for item in diagnostics
        ),
        "df_command_invocation_attempts": sum(
            item["command_type"] == "DF"
            and item["invocation_attempted"] is True
            for item in diagnostics
        ),
        "raw_capture_file_count": sum(
            int(item["stdout_capture_regular_file"])
            + int(item["stderr_capture_regular_file"])
            for item in diagnostics
        ),
    }
    if any(value[field] != expected for field, expected in expected_counts.items()):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPTURE_COUNTER_MISMATCH"
        )
    if (
        value["native_quota_file_captures"] not in {0, 1}
        or (
            value["native_quota_file_captures"] == 0
            and (
                value["native_quota_size_bytes"] is not None
                or value["native_quota_sha256"] is not None
            )
        )
        or (
            value["native_quota_file_captures"] == 1
            and (
                type(value["native_quota_size_bytes"]) is not int
                or value["native_quota_size_bytes"] <= 0
                or not _r8u_r7e_is_sha(value["native_quota_sha256"])
            )
        )
        or (
            bool(diagnostics)
            and value["raw_capture_root_owner_private"] is not True
        )
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPTURE_COUNTER_MISMATCH"
        )
    status = value["status"]
    if status.startswith(R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX):
        failure = _r8u_r7e_validate_failure_diagnostic(
            value["failure_diagnostic"]
        )
        if (
            failure["failure_code"] != status
            or value["capacity_projection"] is not None
            or value["valid_numerical_deficit_calculated"] is not False
            or value["arithmetic_evaluated"]
            is failure["failure_before_capacity_arithmetic"]
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_INVARIANT_INVALID"
            )
        failed_diagnostics = [
            item for item in diagnostics
            if item["failure_predicate"] is not None
        ]
        stage = failure["failure_stage"]
        expected_native_capture = (
            1
            if stage in {"NATIVE_QUOTA", "PARSER", "MOUNT", "ARITHMETIC"}
            else 0
        )
        if value["native_quota_file_captures"] != expected_native_capture:
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
            )
        if stage in {
            "NATIVE_QUOTA_READ",
            "NATIVE_QUOTA",
            "PARSER",
            "MOUNT",
            "ARITHMETIC",
        } and any(
            item["invocation_attempted"] is not True
            or item["capture_present"] is not True
            or item["capture_regular_file"] is not True
            or item["exit_status"] != 0
            for item in diagnostics
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
            )
        parser_states = [item["parser_result"] for item in diagnostics]
        if (
            stage in {"COMMAND", "NATIVE_QUOTA_READ", "NATIVE_QUOTA"}
            and parser_states != ["NOT_EVALUATED"] * 5
        ):
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
            )
        if stage == "MOUNT" and parser_states != [
            "PASS", "PASS", "PASS", "NOT_EVALUATED", "NOT_EVALUATED"
        ]:
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
            )
        if stage == "ARITHMETIC" and parser_states != ["PASS"] * 5:
            raise PostReallocationCapacityError(
                "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
            )
        if failure["command_type"] == "NONE":
            noncommand_reasons = {
                (
                    "raw_capture_root",
                    "PRIVATE_EMPTY_DIRECTORY_REQUIRED",
                ): (
                    "RAW_CAPTURE_ROOT_AUTHORITY_MISMATCH",
                    "RAW_CAPTURE_ROOT",
                    "TRUSTED_FIXED_AUTHORITY",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "capacity_authority",
                    "FIXED_PATH_AUTHORITY_REQUIRED",
                ): (
                    "CAPACITY_AUTHORITY_MISMATCH",
                    "CAPACITY_AUTHORITY",
                    "TRUSTED_FIXED_AUTHORITY",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "native_quota_file.capture",
                    "QUOTA_AUTHORITY_READ_FAILED",
                ): (
                    "QUOTA_AUTHORITY_MISMATCH",
                    "NATIVE_QUOTA_READ",
                    "BOUNDED_REGULAR_NATIVE_QUOTA_AUTHORITY",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "native_quota_file.numeric_values",
                    "NUMERIC_VALUE_INVALID",
                ): (
                    "NUMERIC_VALUE_INVALID",
                    "NATIVE_QUOTA",
                    "NONNEGATIVE_BOUNDED_INTEGER_QUOTA_VALUES",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "native_quota_file.allocation_authority",
                    "QUOTA_AUTHORITY_MISMATCH",
                ): (
                    "QUOTA_AUTHORITY_MISMATCH",
                    "NATIVE_QUOTA",
                    "STRICT_NATIVE_QUOTA_ROWS_AND_ALLOCATIONS",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "restricted_mount_reconciliation",
                    "MOUNT_AUTHORITY_MISMATCH",
                ): (
                    "MOUNT_AUTHORITY_MISMATCH",
                    "MOUNT",
                    "NATIVE_FILESET_TO_NONBIND_RESTRICTED_MOUNT",
                    "INVALID_OR_UNAVAILABLE",
                ),
                (
                    "capacity_projection",
                    "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                ): (
                    "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                    "ARITHMETIC",
                    "EXACT_R7D_INTEGER_REPLAY",
                    "ARITHMETIC_REPLAY_REJECTED",
                ),
                (
                    "capacity_projection.status",
                    "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                ): (
                    "CAPACITY_ARITHMETIC_INVARIANT_FAILURE",
                    "ARITHMETIC",
                    "TRUSTED_FIXED_AUTHORITY",
                    "INVALID_OR_UNAVAILABLE",
                ),
            }
            expected_noncommand = noncommand_reasons.get(
                (
                    failure["failure_field"],
                    failure["failure_predicate"],
                )
            )
            expected_reason = (
                None if expected_noncommand is None else expected_noncommand[0]
            )
            expected_stage = (
                None if expected_noncommand is None else expected_noncommand[1]
            )
            expected_value_category = (
                None if expected_noncommand is None else expected_noncommand[2]
            )
            observed_value_category = (
                None if expected_noncommand is None else expected_noncommand[3]
            )
            numeric_source_codes = {
                "NATIVE_QUOTA_INTEGER_INVALID",
                "NATIVE_QUOTA_USAGE_INVALID",
                "NATIVE_QUOTA_USAGE_EXCEEDS_ALLOCATION",
            }
            if (
                expected_reason is None
                or status != _r8u_r7e_failure_code(expected_reason)
                or stage != expected_stage
                or failure["expected_value_category"]
                != expected_value_category
                or failure["observed_value_category"]
                != observed_value_category
                or failure["command_ordinal"] is not None
                or failure["registry_ordinal"] is not None
                or failure["command_exit_status"] is not None
                or failure["command_exit_class"] != "NOT_AVAILABLE"
                or failure["raw_capture_sha256"] is not None
                or failed_diagnostics
                or not failure["source_error_code"]
                or (
                    expected_reason == "NUMERIC_VALUE_INVALID"
                    and failure["source_error_code"]
                    not in numeric_source_codes
                )
                or (
                    expected_reason == "QUOTA_AUTHORITY_MISMATCH"
                    and failure["source_error_code"] in numeric_source_codes
                )
            ):
                raise PostReallocationCapacityError(
                    "R8U_R7E_FAILURE_RECEIPT_FIRST_PREDICATE_INVALID"
                )
        else:
            if not failed_diagnostics or stage not in {"COMMAND", "PARSER"}:
                raise PostReallocationCapacityError(
                    "R8U_R7E_FAILURE_RECEIPT_FIRST_PREDICATE_INVALID"
                )
            first = failed_diagnostics[0]
            if first["parser_result"] == "FAIL":
                failure_index = diagnostics.index(first)
                if parser_states != (
                    ["PASS"] * failure_index
                    + ["FAIL"]
                    + ["NOT_EVALUATED"] * (4 - failure_index)
                ):
                    raise PostReallocationCapacityError(
                        "R8U_R7E_FAILURE_RECEIPT_STAGE_INVALID"
                    )
                if first["failure_predicate"] == (
                    "PQUOTA_DISPLAY_CONTRADICTION"
                ):
                    expected_reason = "PQUOTA_DISPLAY_CONTRADICTION"
                elif first["failure_predicate"] in {
                        "PQUOTA_PARSE_FAILURE",
                        "FINDMNT_PARSE_FAILURE",
                        "DF_PARSE_FAILURE",
                }:
                    expected_reason = (
                        f"{first['logical_role'].upper()}_PARSE_FAILURE"
                    )
                else:
                    expected_reason = (
                        f"{first['logical_role'].upper()}_"
                        f"{first['failure_predicate']}"
                    )
                expected_field = (
                    f"{first['logical_role']}.parsed_output"
                )
                parser_source_codes = {
                    "PQUOTA_PARSE_FAILURE": {
                        "PQUOTA_DISPLAY_NOT_EXACT_PASS",
                    },
                    "PQUOTA_DISPLAY_CONTRADICTION": {
                        "PQUOTA_DISPLAY_CONTRADICTION",
                    },
                    "FINDMNT_PARSE_FAILURE": {
                        "FINDMNT_JSON_INVALID",
                        "FINDMNT_SCHEMA_INVALID",
                        "FINDMNT_ROW_SCHEMA_INVALID",
                        "FINDMNT_PATH_NOT_ON_TARGET",
                    },
                    "MOUNT_RESOLUTION_AMBIGUOUS": {
                        "FINDMNT_ROW_COUNT_INVALID",
                    },
                    "DF_PARSE_FAILURE": {
                        "DF_ROW_COUNT_INVALID",
                        "DF_MOUNT_IDENTITY_MISMATCH",
                    },
                    "NUMERIC_VALUE_INVALID": {"DF_BYTES_INVALID"},
                    "FILESYSTEM_ARITHMETIC_INVALID": {
                        "DF_BYTES_DO_NOT_RECONCILE",
                    },
                }
                allowed_sources = parser_source_codes.get(
                    str(first["failure_predicate"])
                )
                if (
                    allowed_sources is None
                    or first["source_error_code"] not in allowed_sources
                    or failure["expected_value_category"]
                    != "STRICT_CANONICAL_PARSED_VALUE"
                    or failure["observed_value_category"]
                    != "PARSER_REJECTED"
                ):
                    raise PostReallocationCapacityError(
                        "R8U_R7E_FAILURE_RECEIPT_SEMANTIC_INVALID"
                    )
            else:
                expected_reason = (
                    f"{first['logical_role'].upper()}_"
                    f"{first['failure_predicate']}"
                )
                expected_field = (
                    f"{first['logical_role']}.executable_authority"
                    if first["failure_predicate"]
                    == "COMMAND_AUTHORITY_INVALID"
                    else f"{first['logical_role']}.capture"
                )
                standardized_command_sources = {
                    "COMMAND_CAPTURE_NOT_REGULAR": {
                        "R8U_R7E_PRIVATE_OUTPUT_PATH_INVALID",
                        "R8U_R7E_PRIVATE_DIRECTORY_INVALID",
                        "R8U_R7E_PRIVATE_OUTPUT_COLLISION",
                        "R8U_R7E_PRIVATE_OUTPUT_WRITE_FAILED",
                        "R8U_R7E_PRIVATE_OUTPUT_INVARIANT_FAILED",
                        "R8U_R7E_PRIVATE_OUTPUT_READBACK_MISMATCH",
                    },
                    "COMMAND_AUTHORITY_CHANGED": {
                        "TOOL_CHANGED_DURING_CAPTURE"
                    },
                    "COMMAND_EXIT_NONZERO": {"COMMAND_NONZERO_EXIT"},
                    "COMMAND_STDERR_PRESENT": {"COMMAND_STDERR_PRESENT"},
                    "COMMAND_OUTPUT_OVERSIZED": {
                        "COMMAND_OUTPUT_OVERSIZED"
                    },
                    "COMMAND_OUTPUT_NOT_UTF8": {
                        "COMMAND_OUTPUT_NOT_UTF8"
                    },
                }
                predicate_sources = standardized_command_sources.get(
                    str(first["failure_predicate"])
                )
                command_authority_failure = (
                    first["failure_predicate"]
                    == "COMMAND_AUTHORITY_INVALID"
                )
                if (
                    not command_authority_failure
                    and first["failure_predicate"]
                    != "COMMAND_CAPTURE_MISSING"
                    and predicate_sources is None
                ) or (
                    predicate_sources is not None
                    and first["source_error_code"] not in predicate_sources
                ) or (
                    failure["expected_value_category"]
                    != (
                        "STABLE_FIXED_EXECUTABLE_AUTHORITY"
                        if command_authority_failure
                        else (
                            "ZERO_EXIT_EMPTY_STDERR_BOUNDED_UTF8_"
                            "REGULAR_CAPTURE"
                        )
                    )
                ) or failure["observed_value_category"] != (
                    "AUTHORITY_UNAVAILABLE"
                    if command_authority_failure
                    else str(first["failure_predicate"])
                ):
                    raise PostReallocationCapacityError(
                        "R8U_R7E_FAILURE_RECEIPT_SEMANTIC_INVALID"
                    )
                predicate = first["failure_predicate"]
                predicate_fields_valid = {
                    "COMMAND_AUTHORITY_INVALID": (
                        first["invocation_attempted"] is False
                        and first["capture_present"] is False
                    ),
                    "COMMAND_CAPTURE_MISSING": (
                        first["invocation_attempted"] is True
                        and first["capture_present"] is False
                    ),
                    "COMMAND_CAPTURE_NOT_REGULAR": (
                        first["capture_present"] is True
                        and first["capture_regular_file"] is False
                    ),
                    "COMMAND_AUTHORITY_CHANGED": (
                        first["capture_regular_file"] is True
                    ),
                    "COMMAND_EXIT_NONZERO": (
                        first["capture_regular_file"] is True
                        and type(first["exit_status"]) is int
                        and first["exit_status"] != 0
                    ),
                    "COMMAND_STDERR_PRESENT": (
                        first["capture_regular_file"] is True
                        and first["exit_status"] == 0
                        and type(first["stderr_size_bytes"]) is int
                        and first["stderr_size_bytes"] > 0
                    ),
                    "COMMAND_OUTPUT_OVERSIZED": (
                        first["capture_present"] is True
                        and (
                            int(first["stdout_size_bytes"] or 0)
                            > _R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES
                            or int(first["stderr_size_bytes"] or 0)
                            > _R8U_R7E_MAXIMUM_COMMAND_STREAM_BYTES
                        )
                    ),
                    "COMMAND_OUTPUT_NOT_UTF8": (
                        first["capture_regular_file"] is True
                        and first["exit_status"] == 0
                        and first["stderr_size_bytes"] == 0
                    ),
                }.get(str(predicate), False)
                if not predicate_fields_valid:
                    raise PostReallocationCapacityError(
                        "R8U_R7E_FAILURE_RECEIPT_PREDICATE_INVALID"
                    )
            expected_code = _r8u_r7e_failure_code(expected_reason)
            if (
                status != expected_code
                or stage
                != ("PARSER" if first["parser_result"] == "FAIL" else "COMMAND")
                or failure["failure_field"] != expected_field
                or failure["failure_predicate"]
                != first["failure_predicate"]
                or failure["command_type"] != first["command_type"]
                or failure["command_ordinal"]
                != first["command_ordinal"]
                or failure["registry_ordinal"]
                != first["registry_ordinal"]
                or failure["command_exit_status"]
                != first["exit_status"]
                or failure["command_exit_class"] != first["exit_class"]
                or failure["capture_present"]
                is not first["capture_present"]
                or failure["capture_regular_file"]
                is not first["capture_regular_file"]
                or failure["parser_result"]
                != first["parser_result"]
                or failure["raw_capture_sha256"]
                != first["raw_capture_sha256"]
                or failure["source_error_code"]
                != first["source_error_code"]
            ):
                raise PostReallocationCapacityError(
                    "R8U_R7E_FAILURE_RECEIPT_FIRST_PREDICATE_INVALID"
                )
        _r8u_r7e_validate_raw_capture_files(
            raw_capture_root,
            diagnostics,
            failure_stage=failure["failure_stage"],
        )
        return dict(value)
    if status not in {
        R8U_R7E_CAPACITY_STATUS_PASS,
        R8U_R7E_CAPACITY_STATUS_DEFICIT,
    }:
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_STATUS_INVALID"
        )
    if (
        value["failure_diagnostic"] is not None
        or value["arithmetic_evaluated"] is not True
        or value["capacity_observation_count"] != 1
        or value["native_quota_file_captures"] != 1
        or value["pquota_command_captures"] != 1
        or value["findmnt_command_captures"] != 2
        or value["df_command_captures"] != 2
        or value["du_command_captures"] != 0
        or value["pquota_command_invocation_attempts"] != 1
        or value["findmnt_command_invocation_attempts"] != 2
        or value["df_command_invocation_attempts"] != 2
        or value["du_command_invocation_attempts"] != 0
        or value["raw_capture_root_owner_private"] is not True
        or value["raw_capture_file_count"] != 10
        or type(value["native_quota_size_bytes"]) is not int
        or value["native_quota_size_bytes"] <= 0
        or not _r8u_r7e_is_sha(value["native_quota_sha256"])
        or any(
            item["exit_status"] != 0
            or item["capture_present"] is not True
            or item["capture_regular_file"] is not True
            or item["parser_result"] != "PASS"
            or item["failure_predicate"] is not None
            for item in diagnostics
        )
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_OBSERVATION_INVARIANT_INVALID"
        )
    _r8u_r7e_validate_raw_capture_files(
        raw_capture_root,
        diagnostics,
        failure_stage=None,
    )
    projection = validate_fixed_r8u_r7d_tasks17_19_capacity(
        plan,
        value["capacity_projection"],
        r7d_runtime_commit=runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        _demand_builder=_demand_builder,
    )
    expected_status = (
        R8U_R7E_CAPACITY_STATUS_PASS
        if projection["status"] == R8U_R7D_CAPACITY_STATUS_PASS
        else R8U_R7E_CAPACITY_STATUS_DEFICIT
    )
    expected_valid_deficit = expected_status == R8U_R7E_CAPACITY_STATUS_DEFICIT
    if (
        status != expected_status
        or value["valid_numerical_deficit_calculated"]
        is not expected_valid_deficit
    ):
        raise PostReallocationCapacityError(
            "R8U_R7E_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity(
    plan: Mapping[str, Any],
    *,
    r7e_runtime_commit: str,
    receipt_path: Path,
    raw_capture_root: Path,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    authority: Any = DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
    process_runner: Callable[..., Any] | None = None,
    _demand_builder: Callable[
        [Mapping[str, Any]], dict[str, int]
    ] = _derive_fixed_r8u_r7d_tasks17_19_demands,
) -> dict[str, Any]:
    """Capture, pure-replay, and seal R7E; seal known failures before raise."""

    _r8u_r7e_preflight_receipt_no_clobber(receipt_path)
    try:
        value = capture_fixed_r8u_r7e_tasks17_19_capacity(
            plan,
            r7e_runtime_commit=r7e_runtime_commit,
            raw_capture_root=raw_capture_root,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=(
                confirmed_partial_artifact_bytes
            ),
            confirmed_partial_artifact_files=(
                confirmed_partial_artifact_files
            ),
            authority=authority,
            process_runner=process_runner,
            _demand_builder=_demand_builder,
        )
    except R8UR7ECapacityObservationError as exc:
        if exc.receipt is None:
            raise
        validated = validate_fixed_r8u_r7e_tasks17_19_capacity(
            plan,
            exc.receipt,
            r7e_runtime_commit=r7e_runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=(
                confirmed_partial_artifact_bytes
            ),
            confirmed_partial_artifact_files=(
                confirmed_partial_artifact_files
            ),
            raw_capture_root=raw_capture_root,
            _demand_builder=_demand_builder,
        )
        receipt_sha256 = write_r8u_r7e_capacity_receipt_no_clobber(
            receipt_path,
            validated,
        )
        exc.receipt = validated
        exc.receipt_sha256 = receipt_sha256
        raise
    validated = validate_fixed_r8u_r7e_tasks17_19_capacity(
        plan,
        value,
        r7e_runtime_commit=r7e_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        raw_capture_root=raw_capture_root,
        _demand_builder=_demand_builder,
    )
    write_r8u_r7e_capacity_receipt_no_clobber(receipt_path, validated)
    return validated


# R7F is an additive plan-projection/capacity epoch.  The mature R7E capture
# engine remains the single implementation of the fixed five-command registry;
# a private demand-builder seam gives R7F the corrected plan-derived arithmetic
# without changing any default R7D/R7E call or receipt.
R8U_R7F_CAPACITY_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r7f_tasks17_19_capacity_observation_v1"
)
R8U_R7F_CAPACITY_STATUS_PASS = (
    "PASS_R8U_R7F_TASKS_17_19_REMAINING_CAPACITY"
)
R8U_R7F_CAPACITY_STATUS_DEFICIT = (
    "BLOCKED_R8U_R7F_QUANTIFIED_CAPACITY_DEFICIT"
)
R8U_R7F_CAPACITY_STATUS_OBSERVATION_PREFIX = (
    "BLOCKED_R8U_R7F_CAPACITY_OBSERVATION_"
)
R8U_R7F_CAPACITY_PROJECTION_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r7f_tasks17_19_capacity_v1"
)
R8U_R7F_CAPACITY_PROJECTION_STATUS_PASS = (
    "PASS_R7F_TASKS_17_19_WITH_200GB_RESERVE"
)
R8U_R7F_CAPACITY_PROJECTION_STATUS_BLOCKED = (
    "BLOCKED_R7F_TASKS_17_19_CAPACITY"
)
R8U_R7F_CAPACITY_KEYS = frozenset(
    (R8U_R7E_CAPACITY_KEYS - {"r7e_runtime_commit"})
    | {"r7f_runtime_commit"}
)
R8U_R7F_CAPACITY_PROJECTION_KEYS = frozenset(
    (
        R8U_R7D_CAPACITY_KEYS
        - {
            "r7d_runtime_commit",
            "r7d_increment_bytes",
            "quota_slack_after_r7d_bytes",
            "physical_slack_after_r7d_bytes",
        }
    )
    | {
        "r7f_runtime_commit",
        "r7f_increment_bytes",
        "quota_slack_after_r7f_bytes",
        "physical_slack_after_r7f_bytes",
    }
)


class R8UR7FCapacityObservationError(PostReallocationCapacityError):
    """One field-specific R7F observation failure with its bounded receipt."""

    def __init__(
        self,
        code: str,
        *,
        diagnostic: Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
        receipt_sha256: str | None = None,
    ) -> None:
        super().__init__(code)
        self.diagnostic = dict(diagnostic)
        self.receipt = None if receipt is None else dict(receipt)
        self.receipt_sha256 = receipt_sha256


def _r8u_relabel_epoch(value: Any, *, old: str, new: str) -> Any:
    """Relabel one bounded receipt epoch without touching numeric evidence."""

    old_lower = old.lower()
    new_lower = new.lower()
    if isinstance(value, Mapping):
        return {
            str(key).replace(old, new).replace(old_lower, new_lower): (
                _r8u_relabel_epoch(item, old=old, new=new)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_r8u_relabel_epoch(item, old=old, new=new) for item in value]
    if type(value) is str:
        return value.replace(old, new).replace(old_lower, new_lower)
    return value


def _r8u_r7e_to_r7f_capacity_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(value)
    projection = source.pop("capacity_projection", None)
    converted = _r8u_relabel_epoch(source, old="R7E", new="R7F")
    converted["capacity_projection"] = (
        None
        if projection is None
        else _r8u_relabel_epoch(projection, old="R7D", new="R7F")
    )
    return converted


def _r8u_r7f_to_r7e_capacity_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(value)
    projection = source.pop("capacity_projection", None)
    converted = _r8u_relabel_epoch(source, old="R7F", new="R7E")
    converted["capacity_projection"] = (
        None
        if projection is None
        else _r8u_relabel_epoch(projection, old="R7F", new="R7D")
    )
    return converted


def _r8u_r7f_translate_write_error(exc: PostReallocationCapacityError) -> None:
    code = str(exc.code).replace("R8U_R7E", "R8U_R7F")
    raise PostReallocationCapacityError(code) from exc


def write_r8u_r7f_static_plan_projection_no_clobber(
    path: Path, value: Mapping[str, Any],
) -> str:
    """Write one canonical mode-0600 R7F static projection without clobber."""

    if not isinstance(value, Mapping):
        raise PostReallocationCapacityError(
            "R8U_R7F_STATIC_PLAN_PROJECTION_SCHEMA_INVALID"
        )
    try:
        return _r8u_r7e_write_new_private_bytes(
            path, _r8u_r7e_canonical(dict(value))
        )
    except PostReallocationCapacityError as exc:
        _r8u_r7f_translate_write_error(exc)


def write_r8u_r7f_capacity_receipt_no_clobber(
    path: Path, value: Mapping[str, Any],
) -> str:
    """Write one canonical mode-0600 R7F capacity receipt without clobber."""

    if not isinstance(value, Mapping) or set(value) != R8U_R7F_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7F_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    try:
        return _r8u_r7e_write_new_private_bytes(
            path, _r8u_r7e_canonical(dict(value))
        )
    except PostReallocationCapacityError as exc:
        _r8u_r7f_translate_write_error(exc)


def capture_fixed_r8u_r7f_tasks17_19_capacity(
    plan: Mapping[str, Any],
    *,
    r7f_runtime_commit: str,
    raw_capture_root: Path,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    authority: Any = DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Perform one exact pquota1/findmnt2/df2/du0 R7F observation."""

    require_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
    try:
        legacy = capture_fixed_r8u_r7e_tasks17_19_capacity(
            plan,
            r7e_runtime_commit=r7f_runtime_commit,
            raw_capture_root=raw_capture_root,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
            confirmed_partial_artifact_files=confirmed_partial_artifact_files,
            authority=authority,
            process_runner=process_runner,
            _demand_builder=_derive_fixed_r8u_r7f_tasks17_19_demands,
        )
    except R8UR7ECapacityObservationError as exc:
        receipt = (
            None
            if exc.receipt is None
            else _r8u_r7e_to_r7f_capacity_receipt(exc.receipt)
        )
        diagnostic = _r8u_relabel_epoch(
            exc.diagnostic, old="R7E", new="R7F"
        )
        raise R8UR7FCapacityObservationError(
            str(exc.code).replace("R8U_R7E", "R8U_R7F"),
            diagnostic=diagnostic,
            receipt=receipt,
        ) from exc
    value = _r8u_r7e_to_r7f_capacity_receipt(legacy)
    if (
        set(value) != R8U_R7F_CAPACITY_KEYS
        or not isinstance(value["capacity_projection"], Mapping)
        or set(value["capacity_projection"]) != R8U_R7F_CAPACITY_PROJECTION_KEYS
    ):
        raise PostReallocationCapacityError(
            "R8U_R7F_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return value


def validate_fixed_r8u_r7f_tasks17_19_capacity(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    r7f_runtime_commit: Any,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    raw_capture_root: Path | None = None,
) -> dict[str, Any]:
    """Purely replay a closed R7F success, deficit, or observation failure."""

    require_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
    if not isinstance(value, Mapping) or set(value) != R8U_R7F_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R7F_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    legacy = _r8u_r7f_to_r7e_capacity_receipt(value)
    try:
        validated = validate_fixed_r8u_r7e_tasks17_19_capacity(
            plan,
            legacy,
            r7e_runtime_commit=r7f_runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
            confirmed_partial_artifact_files=confirmed_partial_artifact_files,
            raw_capture_root=raw_capture_root,
            _demand_builder=_derive_fixed_r8u_r7f_tasks17_19_demands,
        )
    except PostReallocationCapacityError as exc:
        raise PostReallocationCapacityError(
            str(exc.code).replace("R8U_R7E", "R8U_R7F")
        ) from exc
    expected = _r8u_r7e_to_r7f_capacity_receipt(validated)
    if _r8u_r7e_canonical(dict(value)) != _r8u_r7e_canonical(expected):
        raise PostReallocationCapacityError(
            "R8U_R7F_CAPACITY_RECEIPT_REPLAY_INVALID"
        )
    return dict(value)


def capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
    plan: Mapping[str, Any],
    *,
    r7f_runtime_commit: str,
    receipt_path: Path,
    raw_capture_root: Path,
    preserved_old_evidence_bytes: int = 0,
    preserved_old_evidence_files: int = 0,
    confirmed_partial_artifact_bytes: int = 0,
    confirmed_partial_artifact_files: int = 0,
    authority: Any = DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Capture, replay, and no-clobber seal one R7F observation."""

    try:
        _r8u_r7e_preflight_receipt_no_clobber(receipt_path)
    except PostReallocationCapacityError as exc:
        _r8u_r7f_translate_write_error(exc)
    try:
        value = capture_fixed_r8u_r7f_tasks17_19_capacity(
            plan,
            r7f_runtime_commit=r7f_runtime_commit,
            raw_capture_root=raw_capture_root,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
            confirmed_partial_artifact_files=confirmed_partial_artifact_files,
            authority=authority,
            process_runner=process_runner,
        )
    except R8UR7FCapacityObservationError as exc:
        if exc.receipt is None:
            raise
        validated = validate_fixed_r8u_r7f_tasks17_19_capacity(
            plan,
            exc.receipt,
            r7f_runtime_commit=r7f_runtime_commit,
            preserved_old_evidence_bytes=preserved_old_evidence_bytes,
            preserved_old_evidence_files=preserved_old_evidence_files,
            confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
            confirmed_partial_artifact_files=confirmed_partial_artifact_files,
            raw_capture_root=raw_capture_root,
        )
        exc.receipt = validated
        exc.receipt_sha256 = write_r8u_r7f_capacity_receipt_no_clobber(
            receipt_path, validated
        )
        raise
    validated = validate_fixed_r8u_r7f_tasks17_19_capacity(
        plan,
        value,
        r7f_runtime_commit=r7f_runtime_commit,
        preserved_old_evidence_bytes=preserved_old_evidence_bytes,
        preserved_old_evidence_files=preserved_old_evidence_files,
        confirmed_partial_artifact_bytes=confirmed_partial_artifact_bytes,
        confirmed_partial_artifact_files=confirmed_partial_artifact_files,
        raw_capture_root=raw_capture_root,
    )
    write_r8u_r7f_capacity_receipt_no_clobber(receipt_path, validated)
    return validated
