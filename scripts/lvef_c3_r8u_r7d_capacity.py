"""Fixed Tasks-17--19-only R8U-R7D capacity authority.

The historic post-reallocation capacity entrypoint is a frozen canary.  This
module reuses its read-only observation and immutable-plan helpers while
keeping the R7D schema and arithmetic in a separate implementation epoch.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

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
) -> dict[str, Any]:
    """Internal common constructor for pure replay and one live capture."""

    runtime_commit = _fixed_r8u_r7d_runtime_commit(r7d_runtime_commit)
    demands = _derive_fixed_r8u_r7d_tasks17_19_demands(plan)
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
