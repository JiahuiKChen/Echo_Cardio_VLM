#!/usr/bin/env python3
"""Capture and adjudicate exact SCC post-reallocation capacity offline.

The only subprocesses implemented here are the fixed read-only commands
``pquota``, ``findmnt`` and ``df``.  The native quota file is read without
following symlinks so its integer-KiB FILESET rows, rather than the rounded
human display, govern byte and file-count arithmetic.  No directory walk,
``du``, cloud request, scheduler operation, or scientific-data read exists.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence


SCHEMA_VERSION = 2
RECEIPT_TYPE = "lvef_c3_post_reallocation_capacity_receipt_v2"
RECEIPT_STATUS = "PASS_READ_ONLY_POST_REALLOCATION_CAPACITY_CAPTURE"
AGGREGATE_TYPE = "lvef_c3_post_reallocation_capacity_summary_v2"
AGGREGATE_STATUS = "PASS_POST_REALLOCATION_CAPACITY"
ATTEMPT_RE = re.compile(
    r"^lvef_multitask_phase1ef_post_reallocation_lock_attempt_[0-9]{3}$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SELECTED_SOURCE_BYTES = 1_216_569_133_322
PROJECTED_PEAK_BYTES = 1_611_642_076_332
REQUIRED_FREE_HEADROOM_BYTES = 200_000_000_000
MINIMUM_EFFECTIVE_QUOTA_BYTES = 1_811_642_076_332
PREFERRED_RESEARCH_QUOTA_BYTES = 1_950_000_000_000
PRESPECIFIED_CONTROL_BURDEN_BYTES = 10_000_000_000
PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES = 10_000_000_000
RESEARCH_ADDITIONAL_FILE_DEMAND = 3_500_000
CONTROL_ADDITIONAL_FILE_DEMAND = 100_000
EXPECTED_RESEARCH_QUOTA_KIB = 2_044_723_200
EXPECTED_BACKED_QUOTA_KIB = 52_428_800
EXPECTED_RESEARCH_FILE_QUOTA = 33_554_432
EXPECTED_BACKED_FILE_QUOTA = 1_638_400
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
EXPECTED_QUOTA_PRINCIPAL = "mimicecho"
EXPECTED_NATIVE_ROWS = {
    "backed": "rproject_mimicecho",
    "research": "rprojectnb_mimicecho",
}
EXPECTED_NATIVE_FILESET_FIELD = "root"
EXPECTED_NATIVE_PRINCIPAL_SUFFIX = "_mimicecho"
EXPECTED_DISPLAY_ROW_ALIASES = {
    "backed": frozenset({"/rproject/mimicecho", "/project/mimicecho"}),
    "research": frozenset({"/rprojectnb/mimicecho", "/projectnb/mimicecho"}),
}
EXPECTED_RESTRICTED_PATHS = {
    "backed": Path("/restricted/project/mimicecho"),
    "research": Path("/restricted/projectnb/mimicecho"),
}
EXPECTED_NATIVE_QUOTA_FILE = Path("/usr/local/etc/quota/project.quota")
EXPECTED_PQUOTA_EXECUTABLE = Path("/usr/local/etc/quota/pquota")
EXPECTED_FINDMNT_EXECUTABLE = Path("/usr/bin/findmnt")
EXPECTED_DF_EXECUTABLE = Path("/usr/bin/df")
EXPECTED_PQUOTA_SIZE_BYTES = 5_840
EXPECTED_PQUOTA_SHA256 = (
    "d0aacf79af95e8a558e2b27f81b685210427fe773a3aff93b4f1b1ba5ba339ab"
)
# Exact-five canary current-headroom policy.  The overhead is the tracked
# ``storage.manifest_and_metadata_reserve_bytes`` value in
# configs/lvef_c3_resource_policy.yaml; it is frozen here so a current probe
# cannot silently reinterpret that tracked contract.
CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES = 5_000_000_000
CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES = 5_000_000_000
CANARY_REQUIRED_REMAINING_PROJECT_BYTES = (
    CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES
    + CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
)
CANARY_REQUIRED_REMAINING_FILE_SLOTS = 2_048
CANARY_HEADROOM_STATUS = "PASS_READ_ONLY_CURRENT_CANARY_HEADROOM"
CANARY_HEADROOM_KEYS = frozenset(
    {
        "status",
        "project_quota_remaining_bytes",
        "required_object_bytes",
        "frozen_overhead_bytes",
        "required_remaining_project_bytes",
        "project_file_slots_remaining",
        "required_remaining_file_slots",
        "physical_filesystem_available_bytes",
        "required_physical_available_bytes",
        "project_byte_headroom_passed",
        "project_file_slot_headroom_passed",
        "physical_byte_headroom_passed",
        "native_quota_authority_read_only",
        "pquota_display_crosscheck",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "writes_performed",
    }
)
FULL_HEADROOM_STATUS = "PASS_READ_ONLY_CURRENT_FULL_C3_HEADROOM"
FULL_HEADROOM_KEYS = frozenset(
    {
        "status",
        "research_quota_bytes",
        "research_usage_bytes",
        "research_quota_remaining_bytes",
        "research_file_quota",
        "research_files_used",
        "research_file_slots_remaining",
        "research_filesystem_available_bytes",
        "backed_quota_bytes",
        "backed_usage_bytes",
        "backed_quota_remaining_bytes",
        "backed_file_quota",
        "backed_files_used",
        "backed_file_slots_remaining",
        "selected_source_bytes",
        "projected_peak_bytes",
        "required_free_headroom_bytes",
        "research_physical_required_available_bytes",
        "research_quota_slack_after_projected_peak_bytes",
        "research_margin_beyond_200gb_reserve_bytes",
        "research_physical_slack_bytes",
        "research_additional_file_demand",
        "backed_control_burden_bytes",
        "backed_additional_file_demand",
        "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate_passed",
        "native_quota_authority_read_only",
        "pquota_display_crosscheck",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "writes_performed",
    }
)

# Phase 1I-R8R continuation capacity is intentionally not a general successor
# estimate.  It is one fixed read-only adjudication for the still-unrun array
# tasks 4--19 of the original, already materialized scientific attempt.  The
# plan is supplied as an already-loaded mapping so this module never reopens or
# reinterprets a caller-selected path, range, attempt, commit, or demand.
R8R_ORIGINAL_PLAN_SHA256 = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
R8R_ORIGINAL_ATTEMPT_ID = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
R8R_ORIGINAL_SCIENTIFIC_COMMIT = (
    "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
)
R8R_CONTINUATION_FIRST_TASK = 4
R8R_CONTINUATION_LAST_TASK = 19
R8R_CONTINUATION_TASK_COUNT = 16
R8R_CONTINUATION_FIRST_BATCH_INDEX = 3
R8R_CONTINUATION_EXCLUSIVE_LAST_BATCH_INDEX = 19
R8R_CONTINUATION_REMAINING_STUDIES = 3_780
R8R_CONTINUATION_REMAINING_OBJECTS = 280_263
R8R_CONTINUATION_REMAINING_SOURCE_BYTES = 1_014_021_066_806
R8R_ORIGINAL_STUDIES = 4_530
R8R_ORIGINAL_OBJECTS = 335_984
R8R_ORIGINAL_SOURCE_BYTES = 1_216_569_133_322
R8R_EXTRACTED_BYTES_PER_OBJECT = 4_816_896
R8R_CLIP_EMBEDDING_BYTES_PER_OBJECT = 4_096
R8R_STUDY_EMBEDDING_BYTES_PER_STUDY = 4_096
R8R_RETAINED_EXTRACTED_AUDIT_BYTES = 2_000_000_000
R8R_MANIFEST_AND_METADATA_BYTES = 5_000_000_000
R8R_LOG_BYTES = 10_000_000_000
R8R_PRESERVATION_AND_FINALIZATION_BYTES = 10_000_000_000
R8R_SAFETY_BYTES = 50_000_000_000
R8R_QUOTA_RESERVE_BYTES = 200_000_000_000
R8R_PHYSICAL_RESERVE_BYTES = 200_000_000_000
R8R_FIXED_CONTROL_FILE_DEMAND = 100_000
R8R_CONTINUATION_STATUS_PASS = (
    "PASS_FIXED_CONTINUATION_4_19_WITH_200GB_RESERVE"
)
R8R_CONTINUATION_STATUS_BLOCKED = "BLOCKED"
R8R_CONTINUATION_ARTIFACT_TYPE = (
    "lvef_c3_r8r_fixed_continuation_capacity_authority_v1"
)
R8R_CONTINUATION_ZERO_EFFECT_KEYS = frozenset(
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
        "files_deleted",
        "writes_performed",
    }
)
R8R_CONTINUATION_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "blocking_reason_codes",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "continuation_first_task",
        "continuation_last_task",
        "continuation_task_count",
        "remaining_batch_count",
        "remaining_studies",
        "remaining_objects",
        "remaining_source_bytes",
        "largest_remaining_batch_objects",
        "largest_remaining_batch_source_bytes",
        "remaining_raw_source_demand_bytes",
        "largest_remaining_transfer_retry_demand_bytes",
        "largest_rolling_extracted_cache_demand_bytes",
        "remaining_clip_embedding_upper_bound_bytes",
        "remaining_study_embedding_upper_bound_bytes",
        "retained_extracted_audit_demand_bytes",
        "manifest_and_metadata_demand_bytes",
        "log_demand_bytes",
        "preservation_and_finalization_demand_bytes",
        "safety_demand_bytes",
        "continuation_increment_bytes",
        "remaining_raw_object_file_demand",
        "largest_rolling_object_file_demand",
        "fixed_control_file_demand",
        "required_file_slots",
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
        "quota_slack_after_continuation_bytes",
        "physical_slack_after_continuation_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
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
        *R8R_CONTINUATION_ZERO_EFFECT_KEYS,
    }
)

# Phase 1I-R8U capacity is one fixed recovery of original Task 16 followed by
# the still-unrun original Tasks 17--19.  The retained Task-16 raw files and
# failed partial extraction cache are already represented in the observed
# usage/file counts, so they are bound as baseline evidence and contribute
# exactly zero incremental bytes and file slots.  Only fresh work is charged:
# Tasks 17--19 raw files and retry space, one rolling largest fresh extraction
# envelope across Tasks 16--19, fresh embeddings across Tasks 16--19, and the
# established control/finalization burdens.
R8U_ORIGINAL_PLAN_SHA256 = R8R_ORIGINAL_PLAN_SHA256
R8U_ORIGINAL_ATTEMPT_ID = R8R_ORIGINAL_ATTEMPT_ID
R8U_ORIGINAL_SCIENTIFIC_COMMIT = R8R_ORIGINAL_SCIENTIFIC_COMMIT
R8U_R8R_IMPLEMENTATION_COMMIT = (
    "fe3b6c40162d16d5021558bc686ba93c05ab03f5"
)
R8U_BASE_IMPLEMENTATION_COMMIT = (
    "cbd54ec67a24bc26e538be0423df38cee8a9eb6f"
)
R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT = (
    "f3df5cd969ff70c87378657767c5bf2b92d4e074"
)
R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = frozenset(
    {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
    }
)
R8U_RECOVERY_TASK = 16
R8U_RECOVERY_BATCH_INDEX = 15
R8U_RECOVERY_BATCH_ID = "c3_batch_015"
R8U_CONTINUATION_FIRST_TASK = 17
R8U_CONTINUATION_LAST_TASK = 19
R8U_CONTINUATION_TASK_COUNT = 3
R8U_CONTINUATION_FIRST_BATCH_INDEX = 16
R8U_EXCLUSIVE_LAST_BATCH_INDEX = 19
R8U_FRESH_PIPELINE_BATCH_COUNT = 4
R8U_BATCH16_RAW_OBJECT_FILES_BASELINE = 18_677
R8U_FAILED_PARTIAL_FILES_BASELINE = 4_757
R8U_FAILED_PARTIAL_BYTES_BASELINE = 8_583_119_701
R8U_EXTRACTED_BYTES_PER_OBJECT = R8R_EXTRACTED_BYTES_PER_OBJECT
R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT = R8R_CLIP_EMBEDDING_BYTES_PER_OBJECT
R8U_STUDY_EMBEDDING_BYTES_PER_STUDY = R8R_STUDY_EMBEDDING_BYTES_PER_STUDY
R8U_RETAINED_EXTRACTED_AUDIT_BYTES = R8R_RETAINED_EXTRACTED_AUDIT_BYTES
R8U_MANIFEST_AND_METADATA_BYTES = R8R_MANIFEST_AND_METADATA_BYTES
R8U_LOG_BYTES = R8R_LOG_BYTES
R8U_PRESERVATION_AND_FINALIZATION_BYTES = (
    R8R_PRESERVATION_AND_FINALIZATION_BYTES
)
R8U_SAFETY_BYTES = R8R_SAFETY_BYTES
R8U_QUOTA_RESERVE_BYTES = 200_000_000_000
R8U_PHYSICAL_RESERVE_BYTES = 200_000_000_000
R8U_FIXED_CONTROL_FILE_DEMAND = R8R_FIXED_CONTROL_FILE_DEMAND
R8U_CAPACITY_STATUS_PASS = (
    "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE"
)
R8U_CAPACITY_STATUS_BLOCKED = "BLOCKED"
R8U_CAPACITY_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r2_batch16_recovery_capacity_v1"
)
R8U_CAPACITY_ZERO_EFFECT_KEYS = frozenset(
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
        "files_deleted",
        "writes_performed",
    }
)
R8U_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "blocking_reason_codes",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "implementation_authority_epochs",
        "recovery_task",
        "recovery_batch_id",
        "continuation_first_task",
        "continuation_last_task",
        "continuation_task_count",
        "fresh_pipeline_batch_count",
        "fresh_pipeline_studies",
        "fresh_pipeline_objects",
        "fresh_pipeline_source_bytes",
        "continuation_studies",
        "continuation_objects",
        "continuation_source_bytes",
        "largest_fresh_pipeline_batch_objects",
        "largest_continuation_batch_source_bytes",
        "batch16_raw_reused",
        "batch16_raw_object_files_baseline",
        "batch16_raw_source_bytes_baseline",
        "failed_partial_files_baseline",
        "failed_partial_bytes_baseline",
        "batch16_redownload_demand_bytes",
        "baseline_batch16_raw_bytes_added_to_increment",
        "baseline_failed_partial_bytes_added_to_increment",
        "baseline_batch16_raw_files_added_to_demand",
        "baseline_failed_partial_files_added_to_demand",
        "continuation_raw_source_demand_bytes",
        "largest_continuation_transfer_retry_demand_bytes",
        "largest_rolling_fresh_extracted_cache_demand_bytes",
        "fresh_clip_embedding_upper_bound_bytes",
        "fresh_study_embedding_upper_bound_bytes",
        "retained_extracted_audit_demand_bytes",
        "manifest_and_metadata_demand_bytes",
        "log_demand_bytes",
        "preservation_and_finalization_demand_bytes",
        "safety_demand_bytes",
        "r8u_increment_bytes",
        "continuation_raw_object_file_demand",
        "largest_rolling_fresh_object_file_demand",
        "fixed_control_file_demand",
        "required_file_slots",
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
        "quota_slack_after_r8u_bytes",
        "physical_slack_after_r8u_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
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
        *R8U_CAPACITY_ZERO_EFFECT_KEYS,
    }
)

# Phase 1I-R8U-R3 resumes from the complete Batch-16 extraction produced by
# the consumed R8U-R2 scheduler epoch.  The completed candidate, retained raw
# data, failed partial cache, and finalized Batches 1--15 are already present
# in the live usage/file observations and therefore contribute no incremental
# storage or file demand.  Remaining demand is limited to Batch-16 EchoPrime
# outputs and closure, the ordinary Tasks 17--19 continuation, and cohort
# finalization.  Keep this schema additive so no R8U-R2 receipt is ever
# reinterpreted under the R3 arithmetic.
R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT = (
    "4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff"
)
R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT = (
    "ce3326a23f149dd864c5aa534225b959d7b5abbe"
)
R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = frozenset(
    {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
        "r8u_publication_resume_repair_commit",
        "r8u_candidate_authority_repair_commit",
    }
)
R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_FILES = 10_187
R8U_R3_CAPACITY_STATUS_PASS = (
    "PASS_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE"
)
R8U_R3_CAPACITY_STATUS_BLOCKED = "BLOCKED"
R8U_R3_CAPACITY_ARTIFACT_TYPE = (
    "lvef_c3_r8u_r3_batch16_publication_resume_capacity_v1"
)
R8U_R3_CAPACITY_ZERO_EFFECT_KEYS = frozenset(
    {
        "cloud_requests",
        "downloads",
        "download_reruns",
        "qsub_submissions",
        "scheduler_jobs_submitted",
        "dicom_body_reads",
        "dicom_extraction_executions",
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
R8U_R3_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "blocking_reason_codes",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "implementation_authority_epochs",
        "resume_task",
        "resume_batch_id",
        "continuation_first_task",
        "continuation_last_task",
        "continuation_task_count",
        "remaining_scope_batch_count",
        "remaining_scope_studies",
        "remaining_scope_source_objects",
        "remaining_scope_source_bytes",
        "continuation_studies",
        "continuation_objects",
        "continuation_source_bytes",
        "largest_continuation_batch_objects",
        "largest_continuation_batch_source_bytes",
        "batch16_raw_reused",
        "batch16_raw_object_files_baseline",
        "batch16_raw_source_bytes_baseline",
        "failed_partial_files_baseline",
        "failed_partial_bytes_baseline",
        "completed_extraction_candidate_reused",
        "completed_extraction_candidate_files_baseline",
        "completed_extraction_candidate_bytes_baseline",
        "completed_extraction_candidate_seal_sha256",
        "batch16_redownload_demand_bytes",
        "batch16_dicom_extraction_demand_bytes",
        "batch16_dicom_extraction_file_demand",
        "baseline_batch16_raw_bytes_added_to_increment",
        "baseline_batch16_raw_files_added_to_demand",
        "baseline_failed_partial_bytes_added_to_increment",
        "baseline_failed_partial_files_added_to_demand",
        "baseline_completed_candidate_bytes_added_to_increment",
        "baseline_completed_candidate_files_added_to_demand",
        "continuation_raw_source_demand_bytes",
        "largest_continuation_transfer_retry_demand_bytes",
        "largest_rolling_continuation_extracted_cache_demand_bytes",
        "batch16_clip_embedding_file_upper_bound",
        "continuation_clip_embedding_file_upper_bound",
        "remaining_clip_embedding_file_upper_bound",
        "remaining_clip_embedding_upper_bound_bytes",
        "remaining_study_embedding_upper_bound_bytes",
        "retained_extracted_audit_demand_bytes",
        "manifest_and_metadata_demand_bytes",
        "log_demand_bytes",
        "preservation_and_finalization_demand_bytes",
        "safety_demand_bytes",
        "r8u_r3_increment_bytes",
        "continuation_raw_object_file_demand",
        "largest_rolling_continuation_object_file_demand",
        "fixed_control_file_demand",
        "required_file_slots",
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
        "quota_slack_after_r8u_r3_bytes",
        "physical_slack_after_r8u_r3_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
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
        *R8U_R3_CAPACITY_ZERO_EFFECT_KEYS,
    }
)

# The historical Phase 1E-F receipt above deliberately remains bound to the
# exact allocation that existed when it was captured.  Fresh-successor
# admission is a different authority: it is expected to observe a changed
# research allocation and therefore has its own closed schema and validator.
# Never use these dynamic constants to reinterpret a historical receipt.
DYNAMIC_SUCCESSOR_OBSERVATION_TYPE = (
    "lvef_c3_dynamic_successor_capacity_observation_v1"
)
DYNAMIC_SUCCESSOR_RECEIPT_TYPE = (
    "lvef_c3_dynamic_successor_capacity_receipt_v1"
)
DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING = "ALLOCATION_NOT_YET_VISIBLE"
DYNAMIC_SUCCESSOR_STATUS_BLOCKED = "BLOCKED_ADDITIONAL_STORAGE_REQUIRED"
DYNAMIC_SUCCESSOR_STATUS_PASS = "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
DYNAMIC_SUCCESSOR_STATUSES = frozenset(
    {
        DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        DYNAMIC_SUCCESSOR_STATUS_BLOCKED,
        DYNAMIC_SUCCESSOR_STATUS_PASS,
    }
)
DYNAMIC_SUCCESSOR_INCREMENT_BYTES = 1_459_366_720_684
DYNAMIC_SUCCESSOR_QUOTA_RESERVE_BYTES = 200_000_000_000
DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES = 200_000_000_000
DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS = 3_500_000
DYNAMIC_SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES = 2
DYNAMIC_SUCCESSOR_MAXIMUM_AGE_SECONDS = 15 * 60
DYNAMIC_SUCCESSOR_MAXIMUM_FUTURE_SKEW_SECONDS = 5
DYNAMIC_SUCCESSOR_MOUNT_STATUS = (
    "PASS_RESEARCH_BACKED_DISTINCT_NO_SYMLINK_NO_BIND"
)
DYNAMIC_SUCCESSOR_SAFE_EXPORT_PROFILE = (
    "lvef_c3_dynamic_successor_capacity_summary_json"
)
DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME = (
    "dynamic_successor_capacity.restricted.json"
)
DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME = (
    "dynamic_successor_capacity.aggregate_safe.json"
)
R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME = (
    "r5e_pre_cleanup_dynamic_successor_capacity.restricted.json"
)
R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME = (
    "r5e_pre_cleanup_dynamic_successor_capacity.aggregate_safe.json"
)
R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME = (
    "r5e_r2_pre_action_dynamic_successor_capacity.restricted.json"
)
R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME = (
    "r5e_r2_pre_action_dynamic_successor_capacity.aggregate_safe.json"
)
R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME = (
    "r5e_post_cleanup_dynamic_successor_capacity.restricted.json"
)
R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME = (
    "r5e_post_cleanup_dynamic_successor_capacity.aggregate_safe.json"
)
R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME = (
    "r5e_r8_post_cleanup_dynamic_successor_capacity.restricted.json"
)
R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME = (
    "r5e_r8_post_cleanup_dynamic_successor_capacity.aggregate_safe.json"
)
DYNAMIC_SUCCESSOR_ALLOWED_EVIDENCE_BASENAME_PAIRS = frozenset(
    {
        (
            DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME,
            DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME,
        ),
        (
            R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
        (
            R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
            R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
        ),
        (
            R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
        (
            R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
    }
)


class DynamicSuccessorCapacityValidationContext(Enum):
    """Closed distinction between admission and immutable-event replay."""

    LIVE_PRECLAIM_ADMISSION = "LIVE_PRECLAIM_ADMISSION"
    SEALED_EXECUTION_REPLAY = "SEALED_EXECUTION_REPLAY"


LIVE_PRECLAIM_ADMISSION = (
    DynamicSuccessorCapacityValidationContext.LIVE_PRECLAIM_ADMISSION
)
SEALED_EXECUTION_REPLAY = (
    DynamicSuccessorCapacityValidationContext.SEALED_EXECUTION_REPLAY
)

DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "blocking_reason_codes",
        "governing_commit",
        "captured_at_utc",
        "command_authority_sha256",
        "native_quota_authority_sha256",
        "mount_authority_sha256",
        "mount_authority_status",
        "pquota_display_crosscheck",
        "pquota_executable_authority_status",
        "live_research_quota_bytes",
        "live_research_usage_bytes",
        "live_research_file_quota",
        "live_research_files_used",
        "live_research_filesystem_available_bytes",
        "backed_quota_bytes",
        "backed_usage_bytes",
        "backed_file_quota",
        "backed_files_used",
        "backed_tier_gate_passed",
        "historical_pre_allocation_research_quota_bytes",
        "historical_pre_allocation_research_file_quota",
        "fresh_successor_increment_bytes",
        "required_quota_reserve_bytes",
        "required_physical_reserve_bytes",
        "required_remaining_file_slots",
        "projected_fresh_successor_peak_bytes",
        "quota_slack_after_peak_bytes",
        "physical_slack_after_peak_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "remaining_file_slots",
        "file_slot_margin_after_demand",
        "active_extraction_caches",
        "preserved_terminal_failed_extraction_caches",
        "successor_attempt_root_absent",
        "successor_claim_absent",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "cache_inventory_gate_passed",
        "successor_collision_gate_passed",
        "minimum_additional_quota_bytes",
        "minimum_additional_physical_bytes",
        "minimum_additional_file_slots",
        "storage_allocation_visible",
        "underlying_filesystem_expansion_appears_necessary",
        "native_quota_authority_read_only",
        "cloud_requests",
        "qsub_submissions",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accesses",
        "files_moved",
        "files_deleted",
        "writes_performed",
    }
)
DYNAMIC_SUCCESSOR_OBSERVATION_KEYS = frozenset(
    {
        *DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS,
        "observation_authority_sha256",
        "restricted_receipt_size_bytes",
        "restricted_receipt_sha256",
    }
)
DYNAMIC_SUCCESSOR_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "governing_commit",
        "captured_at_utc",
        "capture_identity",
        "native_quota_authority",
        "commands",
        "paths",
        "observation_authority",
        "observation_authority_sha256",
    }
)
DYNAMIC_SUCCESSOR_CAPTURE_IDENTITY_KEYS = frozenset(
    {"effective_uid", "effective_username_sha256", "hostname_sha256"}
)
DYNAMIC_SUCCESSOR_NATIVE_AUTHORITY_KEYS = frozenset(
    {
        "path_sha256",
        "file_size_bytes",
        "file_sha256",
        "record_unit",
        "bytes_per_kib",
        "rows",
    }
)
DYNAMIC_SUCCESSOR_NATIVE_ROW_KEYS = frozenset(
    {
        "native_name_sha256",
        "fileset_name_sha256",
        "usage_kib",
        "quota_kib",
        "files_used",
        "file_quota",
        "raw_row_sha256",
    }
)
DYNAMIC_SUCCESSOR_PATH_KEYS = frozenset({"identities", "mounts", "df"})
DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS = frozenset(
    {
        "cloud_requests",
        "qsub_submissions",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accesses",
        "files_moved",
        "files_deleted",
        "writes_performed",
    }
)
DYNAMIC_SUCCESSOR_SAFE_FIELD_TYPES = MappingProxyType({
    **{
        key: "integer"
        for key in {
            "schema_version",
            "live_research_quota_bytes",
            "live_research_usage_bytes",
            "live_research_file_quota",
            "live_research_files_used",
            "live_research_filesystem_available_bytes",
            "backed_quota_bytes",
            "backed_usage_bytes",
            "backed_file_quota",
            "backed_files_used",
            "historical_pre_allocation_research_quota_bytes",
            "historical_pre_allocation_research_file_quota",
            "fresh_successor_increment_bytes",
            "required_quota_reserve_bytes",
            "required_physical_reserve_bytes",
            "required_remaining_file_slots",
            "projected_fresh_successor_peak_bytes",
            "quota_slack_after_peak_bytes",
            "physical_slack_after_peak_bytes",
            "quota_margin_beyond_reserve_bytes",
            "physical_margin_beyond_reserve_bytes",
            "remaining_file_slots",
            "file_slot_margin_after_demand",
            "active_extraction_caches",
            "preserved_terminal_failed_extraction_caches",
            "minimum_additional_quota_bytes",
            "minimum_additional_physical_bytes",
            "minimum_additional_file_slots",
            "restricted_receipt_size_bytes",
            *DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS,
        }
    },
    **{
        key: "boolean"
        for key in {
            "backed_tier_gate_passed",
            "successor_attempt_root_absent",
            "successor_claim_absent",
            "quota_reserve_gate_passed",
            "physical_reserve_gate_passed",
            "file_slot_gate_passed",
            "cache_inventory_gate_passed",
            "successor_collision_gate_passed",
            "storage_allocation_visible",
            "underlying_filesystem_expansion_appears_necessary",
            "native_quota_authority_read_only",
        }
    },
    **{
        key: "string"
        for key in {
            "artifact_type",
            "status",
            "governing_commit",
            "captured_at_utc",
            "command_authority_sha256",
            "native_quota_authority_sha256",
            "mount_authority_sha256",
            "mount_authority_status",
            "pquota_display_crosscheck",
            "pquota_executable_authority_status",
            "observation_authority_sha256",
            "restricted_receipt_sha256",
        }
    },
    "blocking_reason_codes": "array",
})
if set(DYNAMIC_SUCCESSOR_SAFE_FIELD_TYPES) != DYNAMIC_SUCCESSOR_OBSERVATION_KEYS:
    raise RuntimeError("DYNAMIC_SUCCESSOR_SAFE_FIELD_TYPE_REGISTRY_INVALID")
PRIOR_CAPACITY_AUTHORITIES = {
    "phase1ee_parent_capacity": (
        2_257,
        "267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71",
    ),
    "phase1ee_composite_capacity": (
        5_003,
        "28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c",
    ),
    "phase1ee_production_packet_005": (
        7_492,
        "2725570d1137640e0c00ae790f1ae3583d63b17c7f957e86e886892dd0e6ba07",
    ),
}

DISPLAY_CROSSCHECK_PASS = "PASS"
DISPLAY_CROSSCHECK_UNAVAILABLE = "UNAVAILABLE_NONBLOCKING"
DISPLAY_CROSSCHECK_FAIL = "FAIL_BLOCKING"
DISPLAY_CROSSCHECK_STATES = frozenset(
    {
        DISPLAY_CROSSCHECK_PASS,
        DISPLAY_CROSSCHECK_UNAVAILABLE,
        DISPLAY_CROSSCHECK_FAIL,
    }
)
DISPLAY_CROSSCHECK_REASONS = frozenset(
    {
        "MATCHED_NATIVE_AUTHORITY",
        "COMMAND_UNAVAILABLE",
        "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
        "DUPLICATE_EXPECTED_PROJECT_ROW",
        "NOMINAL_QUOTA_CONTRADICTION",
        "FILE_QUOTA_CONTRADICTION",
        "ROUNDED_USAGE_CONTRADICTION",
        "FILE_USAGE_CONTRADICTION",
    }
)
DISPLAY_CROSSCHECK_REASONS_BY_STATE = {
    DISPLAY_CROSSCHECK_PASS: frozenset({"MATCHED_NATIVE_AUTHORITY"}),
    DISPLAY_CROSSCHECK_UNAVAILABLE: frozenset(
        {"COMMAND_UNAVAILABLE", "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE"}
    ),
    DISPLAY_CROSSCHECK_FAIL: frozenset(
        {
            "DUPLICATE_EXPECTED_PROJECT_ROW",
            "NOMINAL_QUOTA_CONTRADICTION",
            "FILE_QUOTA_CONTRADICTION",
            "ROUNDED_USAGE_CONTRADICTION",
            "FILE_USAGE_CONTRADICTION",
        }
    ),
}
GATE_EVALUATION_PASS = "PASS"
GATE_EVALUATION_FAIL = "FAIL"
GATE_EVALUATION_NOT_EVALUATED = "NOT_EVALUATED"
GATE_EVALUATION_STATES = frozenset(
    {
        GATE_EVALUATION_PASS,
        GATE_EVALUATION_FAIL,
        GATE_EVALUATION_NOT_EVALUATED,
    }
)
CAPACITY_GATE_KEYS = (
    "research_quota_gate",
    "physical_filesystem_capacity_gate",
    "projected_200gb_reserve_gate",
    "research_file_quota_gate",
    "backed_control_tier_byte_gate",
    "backed_control_tier_file_gate",
    "backed_control_tier_gate",
)
AGGREGATE_GATE_STATUS_FIELDS = {
    gate: f"{gate}_status" for gate in CAPACITY_GATE_KEYS
}

RECEIPT_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "captured_at_utc", "capture_identity",
        "native_quota_authority", "commands", "paths", "prior_authorities",
        "frozen_plan", "pquota_display_crosscheck", "gate_evaluation_status",
        "no_mutation_attestations",
    }
)
AGGREGATE_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "created_at_utc", "units",
        "quota_display_unit_ruling", "restricted_receipt_size_bytes",
        "restricted_receipt_sha256", "prior_authorities_hash_verified",
        "prior_authorities_closed_schema_verified",
        "immutable_original_aggregate_count",
        "immutable_supplemental_aggregate_count",
        "prior_capacity_authority_count", "prior_production_authority_roles",
        "prior_production_semantic_gates", "research_quota_bytes",
        "research_usage_bytes", "research_quota_remaining_bytes",
        "research_file_quota", "research_files_used",
        "research_file_slots_remaining", "research_filesystem_total_bytes",
        "research_filesystem_used_bytes",
        "research_filesystem_available_bytes", "research_filesystem_type",
        "research_filesystem_identity_sha256", "backed_quota_bytes",
        "backed_usage_bytes", "backed_quota_remaining_bytes",
        "backed_file_quota", "backed_files_used",
        "backed_file_slots_remaining", "backed_filesystem_total_bytes",
        "backed_filesystem_used_bytes", "backed_filesystem_available_bytes",
        "backed_filesystem_type", "backed_filesystem_identity_sha256",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_display_fileset_mapping_verified",
        "pquota_current_not_snapshot_mode_verified",
        "pquota_executable_sha256", "pquota_executable_authority_status",
        "pquota_display_crosscheck", "pquota_display_crosscheck_reason",
        "pquota_display_backed_project_row_matches",
        "pquota_display_research_project_row_matches",
        "pquota_display_rounding_rule",
        *AGGREGATE_GATE_STATUS_FIELDS.values(),
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_path_is_symlink",
        "backed_path_is_symlink", "research_mount_is_bind",
        "backed_mount_is_bind",
        "additional_project_quota_row_for_same_principal_observed",
        "snapshot_capacity_double_counting_avoided",
        "snapshot_presence_independently_enumerated",
        "snapshot_accounting_ruling",
        "selected_source_bytes", "projected_peak_bytes",
        "required_free_headroom_bytes", "minimum_effective_quota_bytes",
        "preferred_research_quota_bytes", "research_remaining_write_bytes",
        "pretransfer_research_write_bound_bytes",
        "research_physical_required_available_bytes",
        "research_quota_margin_above_minimum_bytes",
        "research_quota_slack_after_projected_peak_bytes",
        "research_margin_beyond_200gb_reserve_bytes",
        "research_physical_slack_bytes", "control_burden_bytes",
        "backed_remaining_after_control_burden_bytes",
        "research_additional_file_demand", "backed_additional_file_demand",
        "research_quota_gate_passed", "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate_passed",
        "owner_reported_backed_free_pool_gb",
        "owner_reported_research_free_pool_gb",
        "owner_reported_research_saas_purchased_gb",
        "owner_reported_total_research_quota_gb",
        "purchased_saas_allocation_remains_on_research",
        "control_write_binding_evaluated_by_capacity_receipt", "cloud_requests",
        "object_listing_repeated", "storage_inventory_repeated",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "quota_changed", "files_moved",
        "files_deleted", "full_c3_authorized",
    }
)


class PostReallocationCapacityError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        gate_evaluation_status: Mapping[str, str] | None = None,
        pquota_display_crosscheck: str | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.gate_evaluation_status = gate_evaluation_status
        self.pquota_display_crosscheck = pquota_display_crosscheck


@dataclass(frozen=True)
class CurrentCanaryHeadroomAuthority:
    """Closed file/path authority for one current read-only headroom probe."""

    native_quota_path: Path
    pquota_path: Path
    findmnt_path: Path
    df_path: Path
    research_path: Path
    backed_path: Path


@dataclass(frozen=True)
class DynamicSuccessorCapacityCapture:
    """One internally bound dynamic receipt and aggregate-safe observation."""

    receipt: Mapping[str, Any]
    receipt_payload: bytes
    observation: Mapping[str, Any]


DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY = CurrentCanaryHeadroomAuthority(
    native_quota_path=EXPECTED_NATIVE_QUOTA_FILE,
    pquota_path=EXPECTED_PQUOTA_EXECUTABLE,
    findmnt_path=EXPECTED_FINDMNT_EXECUTABLE,
    df_path=EXPECTED_DF_EXECUTABLE,
    research_path=EXPECTED_RESTRICTED_PATHS["research"],
    backed_path=EXPECTED_RESTRICTED_PATHS["backed"],
)


@dataclass(frozen=True)
class CapacityCommandSpec:
    """One canonical read-only command contract for the capacity receipt."""

    logical_role: str
    command_kind: str
    argv_tail: tuple[str, ...]
    executable_authority: str
    parser_consumer: str
    output_type: str
    optional_nonblocking: bool
    exit_status_policy: str
    stderr_policy: str


CAPACITY_COMMAND_SPECS = (
    CapacityCommandSpec(
        logical_role="pquota",
        command_kind="pquota",
        argv_tail=("-u", EXPECTED_QUOTA_PRINCIPAL),
        executable_authority="PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE",
        parser_consumer="pquota_display",
        output_type="UTF8_TABLE",
        optional_nonblocking=True,
        exit_status_policy="UNAVAILABLE_NONBLOCKING_ALLOWED",
        stderr_policy="UNAVAILABLE_NONBLOCKING_ALLOWED",
    ),
    CapacityCommandSpec(
        logical_role="research_findmnt",
        command_kind="findmnt",
        argv_tail=(
            "--json", "--target", str(EXPECTED_RESTRICTED_PATHS["research"]),
            "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="research_mount",
        output_type="UTF8_JSON",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="backed_findmnt",
        command_kind="findmnt",
        argv_tail=(
            "--json", "--target", str(EXPECTED_RESTRICTED_PATHS["backed"]),
            "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="backed_mount",
        output_type="UTF8_JSON",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="research_df",
        command_kind="df",
        argv_tail=(
            "-B1", "--output=source,size,used,avail,target",
            str(EXPECTED_RESTRICTED_PATHS["research"]),
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="research_df",
        output_type="UTF8_TABLE",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="backed_df",
        command_kind="df",
        argv_tail=(
            "-B1", "--output=source,size,used,avail,target",
            str(EXPECTED_RESTRICTED_PATHS["backed"]),
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="backed_df",
        output_type="UTF8_TABLE",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
)


def _validated_command_registry(
    specifications: Sequence[CapacityCommandSpec],
    *,
    require_canonical_roles: bool,
) -> Mapping[str, CapacityCommandSpec]:
    roles = [item.logical_role for item in specifications]
    consumers = [item.parser_consumer for item in specifications]
    if (
        not specifications
        or len(roles) != len(set(roles))
        or len(consumers) != len(set(consumers))
        or any(not role or not re.fullmatch(r"[a-z][a-z0-9_]*", role) for role in roles)
        or any(
            not consumer
            or not re.fullmatch(r"[a-z][a-z0-9_]*", consumer)
            for consumer in consumers
        )
        or any(
            item.command_kind not in {"pquota", "findmnt", "df"}
            or item.executable_authority not in {
                "PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE",
                "ROOT_CONTROLLED_FIXED_RESOLVER",
            }
            or item.output_type not in {"UTF8_JSON", "UTF8_TABLE"}
            or item.output_type
            != {
                "pquota": "UTF8_TABLE",
                "findmnt": "UTF8_JSON",
                "df": "UTF8_TABLE",
            }[item.command_kind]
            or item.optional_nonblocking != (item.command_kind == "pquota")
            or item.executable_authority
            != (
                "PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE"
                if item.command_kind == "pquota"
                else "ROOT_CONTROLLED_FIXED_RESOLVER"
            )
            or not isinstance(item.argv_tail, tuple)
            or any(
                not isinstance(argument, str) or not argument
                for argument in item.argv_tail
            )
            or (
                item.optional_nonblocking
                and (
                    item.exit_status_policy
                    != "UNAVAILABLE_NONBLOCKING_ALLOWED"
                    or item.stderr_policy
                    != "UNAVAILABLE_NONBLOCKING_ALLOWED"
                )
            )
            or (
                not item.optional_nonblocking
                and (
                    item.exit_status_policy != "REQUIRED_ZERO"
                    or item.stderr_policy != "REQUIRED_EMPTY"
                )
            )
            for item in specifications
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_REGISTRY_INVALID")
    registry = {item.logical_role: item for item in specifications}
    if require_canonical_roles and (
        set(registry) != set(CAPACITY_COMMAND_REGISTRY)
        or tuple(specifications) != CAPACITY_COMMAND_SPECS
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_ROLE_SET_INVALID")
    return MappingProxyType(registry)


CAPACITY_COMMAND_REGISTRY = MappingProxyType(
    {item.logical_role: item for item in CAPACITY_COMMAND_SPECS}
)
if len(CAPACITY_COMMAND_REGISTRY) != len(CAPACITY_COMMAND_SPECS):
    raise RuntimeError("CAPACITY_COMMAND_REGISTRY_DUPLICATE_ROLE")
CAPACITY_COMMAND_ROLES = frozenset(CAPACITY_COMMAND_REGISTRY)
CAPACITY_COMMAND_CONSUMER_ROLES = MappingProxyType(
    {item.parser_consumer: item.logical_role for item in CAPACITY_COMMAND_SPECS}
)
if len(CAPACITY_COMMAND_CONSUMER_ROLES) != len(CAPACITY_COMMAND_SPECS):
    raise RuntimeError("CAPACITY_COMMAND_REGISTRY_DUPLICATE_CONSUMER")
AGGREGATE_COMMAND_ROLES = CAPACITY_COMMAND_ROLES
_validated_command_registry(
    CAPACITY_COMMAND_SPECS,
    require_canonical_roles=True,
)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PostReallocationCapacityError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_symlink_ancestors(path: Path, code: str) -> None:
    if not path.is_absolute():
        raise PostReallocationCapacityError(f"{code}_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    for part in path.absolute().parts[1:-1]:
        cursor /= part
        try:
            item = os.lstat(cursor)
        except OSError as exc:
            raise PostReallocationCapacityError(f"{code}_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(item.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise PostReallocationCapacityError(f"{code}_SYMLINK_ANCESTOR")


def _read_regular(path: Path, *, private: bool = False, maximum: int = 16_000_000) -> bytes:
    _no_symlink_ancestors(path, "INPUT")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PostReallocationCapacityError("INPUT_OPEN_FAILED") from exc
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_size <= 0 or item.st_size > maximum:
            raise PostReallocationCapacityError("INPUT_NOT_BOUNDED_REGULAR")
        if private and (item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600):
            raise PostReallocationCapacityError("INPUT_NOT_OWNER_PRIVATE")
        payload = b""
        while len(payload) <= maximum:
            block = os.read(descriptor, min(1_048_576, maximum + 1 - len(payload)))
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        if (
            len(payload) != item.st_size
            or (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise PostReallocationCapacityError("INPUT_CHANGED_OR_OVERSIZED")
        return payload
    finally:
        os.close(descriptor)


def _load_json(path: Path, *, private: bool = False) -> Mapping[str, Any]:
    try:
        value = json.loads(
            _read_regular(path, private=private).decode(),
            object_pairs_hook=_strict_pairs,
        )
    except PostReallocationCapacityError:
        raise
    except Exception as exc:
        raise PostReallocationCapacityError("INPUT_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise PostReallocationCapacityError("INPUT_JSON_NOT_MAPPING")
    return value


def _write_new(path: Path, payload: bytes, *, private: bool) -> None:
    if path.exists() or path.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_COLLISION")
    _no_symlink_ancestors(path, "OUTPUT")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_PARENT_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600 if private else 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _dynamic_identity(item: os.stat_result) -> tuple[int, ...]:
    return (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_gid,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )


def _dynamic_parent_stable_identity(item: os.stat_result) -> tuple[int, ...]:
    return (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_gid,
    )


def _dynamic_leaf_stable_identity(item: os.stat_result) -> tuple[int, ...]:
    """Return fields that must agree between an open leaf and its name.

    APFS can expose slightly different sub-second timestamp values through an
    open descriptor and a pathname immediately after a write.  The immutable
    binding is therefore the device/inode plus the closed owner, mode, link,
    and byte-count fields; the payload itself is read back through the same
    descriptor and compared exactly.
    """

    return (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_gid,
        item.st_nlink,
        item.st_size,
    )


def _open_dynamic_owner_private_directory(path: Path) -> tuple[int, os.stat_result]:
    """Open every directory component with O_NOFOLLOW and bind visibility."""

    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    descriptor = os.open(
        path.anchor,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        for component in path.parts[1:]:
            opened = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = opened
        bound = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(bound.st_mode)
            or stat.S_ISLNK(visible.st_mode)
            or _dynamic_identity(bound) != _dynamic_identity(visible)
            or bound.st_uid != os.geteuid()
            or stat.S_IMODE(bound.st_mode) not in {0o700, 0o2700}
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        return descriptor, bound
    except BaseException:
        os.close(descriptor)
        raise


def _require_dynamic_visible_parent(
    parent_fd: int,
    parent_path: Path,
    *,
    original: os.stat_result,
) -> os.stat_result:
    """Rebind a held owner-private parent fd to its fixed visible name."""

    current = os.fstat(parent_fd)
    visible = os.stat(parent_path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or current.st_uid != os.geteuid()
        or stat.S_IMODE(current.st_mode) not in {0o700, 0o2700}
        or _dynamic_parent_stable_identity(current)
        != _dynamic_parent_stable_identity(visible)
        or _dynamic_parent_stable_identity(current)
        != _dynamic_parent_stable_identity(original)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    return current


def _read_dynamic_owner_private_regular_at(
    parent_fd: int,
    parent_path: Path,
    parent_original: os.stat_result,
    name: str,
    *,
    maximum: int,
    expected_identity: tuple[int, ...] | None = None,
) -> tuple[bytes, tuple[int, ...]]:
    """Read a private regular leaf relative to one already-bound parent."""

    leaf_fd = -1
    try:
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        leaf_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        # The nonblocking flag prevents a raced FIFO replacement from hanging
        # between the metadata probe and open.
        leaf_fd = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(leaf_fd)
        if (
            not stat.S_ISREG(leaf_before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or _dynamic_identity(leaf_before) != _dynamic_identity(opened)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size < 1
            or opened.st_size > maximum
            or opened.st_dev != parent_original.st_dev
            or (
                expected_identity is not None
                and _dynamic_leaf_stable_identity(opened)
                != expected_identity
            )
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        payload = b""
        while len(payload) <= maximum:
            block = os.read(
                leaf_fd, min(1_048_576, maximum + 1 - len(payload))
            )
            if not block:
                break
            payload += block
        leaf_after = os.fstat(leaf_fd)
        visible_after = os.stat(
            name, dir_fd=parent_fd, follow_symlinks=False
        )
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        if (
            len(payload) != opened.st_size
            or _dynamic_identity(opened) != _dynamic_identity(leaf_after)
            or _dynamic_identity(opened) != _dynamic_identity(visible_after)
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
        return payload, _dynamic_leaf_stable_identity(opened)
    except PostReallocationCapacityError:
        raise
    except OSError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    finally:
        if leaf_fd >= 0:
            os.close(leaf_fd)


def _read_dynamic_owner_private_regular(
    path: Path,
    *,
    maximum: int,
) -> bytes:
    """Read one new dynamic artifact through a bound parent and leaf fd."""

    parent_fd = -1
    try:
        parent_fd, parent_before = _open_dynamic_owner_private_directory(
            path.parent
        )
        payload, _ = _read_dynamic_owner_private_regular_at(
            parent_fd,
            path.parent,
            parent_before,
            path.name,
            maximum=maximum,
        )
        return payload
    except PostReallocationCapacityError:
        raise
    except OSError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _write_dynamic_owner_private_new_at(
    parent_fd: int,
    parent_path: Path,
    parent_original: os.stat_result,
    name: str,
    payload: bytes,
) -> tuple[int, ...]:
    """Create and exactly read back one leaf under a held parent fd."""

    leaf_fd = -1
    try:
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
            )
        try:
            leaf_fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError as exc:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
            ) from exc
        created = os.fstat(leaf_fd)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_uid != os.geteuid()
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_nlink != 1
            or created.st_size != 0
            or created.st_dev != parent_original.st_dev
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        offset = 0
        while offset < len(payload):
            written = os.write(leaf_fd, payload[offset:])
            if written < 1:
                raise OSError("short dynamic capacity write")
            offset += written
        os.fsync(leaf_fd)
        written_stat = os.fstat(leaf_fd)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        readback = b""
        read_offset = 0
        while len(readback) < len(payload):
            block = os.pread(
                leaf_fd,
                min(1_048_576, len(payload) - len(readback)),
                read_offset,
            )
            if not block:
                break
            readback += block
            read_offset += len(block)
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        if (
            not stat.S_ISREG(written_stat.st_mode)
            or written_stat.st_uid != os.geteuid()
            or stat.S_IMODE(written_stat.st_mode) != 0o600
            or written_stat.st_nlink != 1
            or written_stat.st_size != len(payload)
            or _dynamic_leaf_stable_identity(written_stat)
            != _dynamic_leaf_stable_identity(visible)
            or readback != payload
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
        os.fsync(parent_fd)
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        return _dynamic_leaf_stable_identity(written_stat)
    except PostReallocationCapacityError:
        raise
    except OSError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    finally:
        if leaf_fd >= 0:
            os.close(leaf_fd)


def _write_dynamic_owner_private_new(path: Path, payload: bytes) -> None:
    """Create one new 0600/nlink-one artifact relative to a bound parent."""

    parent_fd = -1
    try:
        parent_fd, parent_before = _open_dynamic_owner_private_directory(
            path.parent
        )
        identity = _write_dynamic_owner_private_new_at(
            parent_fd, path.parent, parent_before, path.name, payload
        )
        reopened, _ = _read_dynamic_owner_private_regular_at(
            parent_fd,
            path.parent,
            parent_before,
            path.name,
            maximum=max(len(payload), 1),
            expected_identity=identity,
        )
        if reopened != payload:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _read_dynamic_owner_private_pair_at(
    parent_fd: int,
    parent_path: Path,
    parent_original: os.stat_result,
    restricted_receipt_name: str,
    aggregate_summary_name: str,
    *,
    expected_identities: Mapping[str, tuple[int, ...]] | None = None,
) -> tuple[bytes, bytes]:
    """Hold and double-read both sealed leaves in one directory snapshot."""

    names_and_maxima = (
        (restricted_receipt_name, 16_000_000),
        (aggregate_summary_name, 1_000_000),
    )
    leaf_fds: dict[str, int] = {}
    opened_stats: dict[str, os.stat_result] = {}
    try:
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_original
        )
        parent_snapshot = os.fstat(parent_fd)
        for name, maximum in names_and_maxima:
            before = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            leaf_fds[name] = descriptor
            opened = os.fstat(descriptor)
            opened_stats[name] = opened
            if (
                not stat.S_ISREG(before.st_mode)
                or not stat.S_ISREG(opened.st_mode)
                or _dynamic_identity(before) != _dynamic_identity(opened)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or opened.st_size < 1
                or opened.st_size > maximum
                or opened.st_dev != parent_original.st_dev
                or (
                    expected_identities is not None
                    and _dynamic_leaf_stable_identity(opened)
                    != expected_identities[name]
                )
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                )

        payloads: dict[str, bytes] = {}
        for name, _maximum in names_and_maxima:
            descriptor = leaf_fds[name]
            expected_size = opened_stats[name].st_size
            blocks: list[bytes] = []
            remaining = expected_size
            while remaining:
                block = os.read(descriptor, min(remaining, 1_048_576))
                if not block:
                    raise PostReallocationCapacityError(
                        "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
                    )
                blocks.append(block)
                remaining -= len(block)
            payloads[name] = b"".join(blocks)

        # A second descriptor-bound read after both initial reads catches an
        # in-place, same-size rewrite of the first leaf while the second leaf
        # was being consumed.
        for name, _maximum in names_and_maxima:
            descriptor = leaf_fds[name]
            expected_size = opened_stats[name].st_size
            second = b""
            offset = 0
            while len(second) < expected_size:
                block = os.pread(
                    descriptor,
                    min(1_048_576, expected_size - len(second)),
                    offset,
                )
                if not block:
                    break
                second += block
                offset += len(block)
            after = os.fstat(descriptor)
            visible = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
            if (
                second != payloads[name]
                or _dynamic_identity(opened_stats[name])
                != _dynamic_identity(after)
                or _dynamic_identity(after) != _dynamic_identity(visible)
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
                )

        # Do not finalize either leaf until both second reads have completed.
        # The receipt may otherwise be rewritten while the summary's second
        # read is in progress, after the receipt's per-leaf check.  Both fds
        # remain open, so rebind every full mutable identity to its original
        # descriptor and visible fixed name in one final pair-wide pass.
        for name, _maximum in names_and_maxima:
            final = os.fstat(leaf_fds[name])
            visible = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
            if (
                _dynamic_identity(opened_stats[name])
                != _dynamic_identity(final)
                or _dynamic_identity(final) != _dynamic_identity(visible)
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
                )
        parent_after = os.fstat(parent_fd)
        parent_visible = os.stat(parent_path, follow_symlinks=False)
        if (
            _dynamic_identity(parent_snapshot)
            != _dynamic_identity(parent_after)
            or _dynamic_identity(parent_after)
            != _dynamic_identity(parent_visible)
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
        return (
            payloads[restricted_receipt_name],
            payloads[aggregate_summary_name],
        )
    except PostReallocationCapacityError:
        raise
    except OSError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    finally:
        for descriptor in leaf_fds.values():
            os.close(descriptor)


def _write_dynamic_owner_private_pair(
    restricted_receipt_path: Path,
    receipt_payload: bytes,
    aggregate_summary_path: Path,
    summary_payload: bytes,
) -> tuple[bytes, bytes]:
    """Publish and reopen both leaves under one continuously bound parent."""

    if restricted_receipt_path.parent != aggregate_summary_path.parent:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    parent_path = restricted_receipt_path.parent
    parent_fd = -1
    try:
        parent_fd, parent_before = _open_dynamic_owner_private_directory(
            parent_path
        )
        for name in (
            restricted_receipt_path.name,
            aggregate_summary_path.name,
        ):
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
            )
        receipt_identity = _write_dynamic_owner_private_new_at(
            parent_fd,
            parent_path,
            parent_before,
            restricted_receipt_path.name,
            receipt_payload,
        )
        # A replacement of the fixed parent between leaf publications is
        # rejected before the second O_EXCL write.
        _require_dynamic_visible_parent(
            parent_fd, parent_path, original=parent_before
        )
        summary_identity = _write_dynamic_owner_private_new_at(
            parent_fd,
            parent_path,
            parent_before,
            aggregate_summary_path.name,
            summary_payload,
        )
        reopened_receipt, reopened_summary = (
            _read_dynamic_owner_private_pair_at(
            parent_fd,
            parent_path,
            parent_before,
            restricted_receipt_path.name,
            aggregate_summary_path.name,
            expected_identities={
                restricted_receipt_path.name: receipt_identity,
                aggregate_summary_path.name: summary_identity,
            },
            )
        )
        return reopened_receipt, reopened_summary
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _read_dynamic_owner_private_pair(
    restricted_receipt_path: Path,
    aggregate_summary_path: Path,
) -> tuple[bytes, bytes]:
    """Read both sealed leaves through one continuously bound parent fd."""

    if restricted_receipt_path.parent != aggregate_summary_path.parent:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    parent_path = restricted_receipt_path.parent
    parent_fd = -1
    try:
        parent_fd, parent_before = _open_dynamic_owner_private_directory(
            parent_path
        )
        return _read_dynamic_owner_private_pair_at(
            parent_fd,
            parent_path,
            parent_before,
            restricted_receipt_path.name,
            aggregate_summary_path.name,
        )
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _mkdir_private(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_DIRECTORY_COLLISION")
    _no_symlink_ancestors(path, "OUTPUT_DIRECTORY")
    os.mkdir(path, 0o700)


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    if result.returncode:
        raise PostReallocationCapacityError("GIT_AUTHORITY_FAILED")
    return result.stdout.strip()


def _validate_checkout(checkout: Path, commit: str) -> None:
    if (
        checkout.is_symlink() or not checkout.is_dir()
        or _git(checkout, "rev-parse", "--show-toplevel") != str(checkout.resolve())
        or _git(checkout, "branch", "--show-current") != EXPECTED_BRANCH
        or _git(checkout, "rev-parse", "HEAD") != commit
        or _git(checkout, "rev-parse", f"origin/{EXPECTED_BRANCH}") != commit
        or _git(checkout, "status", "--porcelain", "--untracked-files=no")
    ):
        raise PostReallocationCapacityError("GIT_AUTHORITY_MISMATCH")


def _serialize_command_record(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    executable_sha256: str,
    executable_size_bytes: int,
    exit_status: int,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, Any]:
    """Serialize command evidence using the registry's unique logical role."""
    argv_list = list(argv)
    return {
        "role": specification.logical_role,
        "argv": argv_list,
        "argv_sha256": _sha(
            json.dumps(argv_list, separators=(",", ":")).encode()
        ),
        "executable_sha256": executable_sha256,
        "executable_size_bytes": executable_size_bytes,
        "exit_status": exit_status,
        "stdout_bytes": len(stdout),
        "stdout_sha256": _sha(stdout),
        "stdout_text": stdout.decode("utf-8"),
        "stderr_bytes": len(stderr),
        "stderr_sha256": _sha(stderr),
    }


def _run(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    process_runner: Callable[..., Any] | None = None,
    permitted_owner_uids: frozenset[int] = frozenset({0}),
) -> Mapping[str, Any]:
    executable = Path(argv[0]).resolve(strict=True)
    before = executable.stat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in permitted_owner_uids
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_ROOT_CONTROLLED")
    runner = process_runner or subprocess.run
    result = runner(
        list(argv), capture_output=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    after = executable.stat()
    if result.returncode or result.stderr or len(result.stdout) > 2_000_000:
        raise PostReallocationCapacityError(
            f"{specification.logical_role.upper()}_COMMAND_FAILED"
        )
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    try:
        return _serialize_command_record(
            specification,
            argv,
            executable_sha256=_sha(
                _read_regular(executable, maximum=256_000_000)
            ),
            executable_size_bytes=before.st_size,
            exit_status=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except UnicodeDecodeError as exc:
        raise PostReallocationCapacityError(
            f"{specification.logical_role.upper()}_COMMAND_OUTPUT_NOT_UTF8"
        ) from exc


def _capture_optional_pquota(
    specification: CapacityCommandSpec,
    principal: str,
) -> Mapping[str, Any]:
    """Capture the corroborating display without making it byte authority."""
    if (
        specification.logical_role != "pquota"
        or specification.command_kind != "pquota"
        or principal != EXPECTED_QUOTA_PRINCIPAL
    ):
        raise PostReallocationCapacityError("PQUOTA_COMMAND_SPEC_INVALID")
    located = shutil.which("pquota", path="/usr/local/bin:/usr/bin:/bin")
    argv = [located or "pquota", "-u", principal]
    empty = _sha(b"")

    def unavailable(reason: str) -> Mapping[str, Any]:
        executable_sha256 = "UNAVAILABLE"
        executable_size_bytes = 0
        value = _serialize_command_record(
            specification,
            argv,
            executable_sha256=executable_sha256,
            executable_size_bytes=executable_size_bytes,
            exit_status=-1,
            stdout=b"",
            stderr=b"",
        )
        value.update({
            "availability_status": "UNAVAILABLE_NONBLOCKING",
            "availability_reason": reason,
        })
        return value

    if not located:
        return unavailable("EXECUTABLE_NOT_FOUND")
    executable = Path(located).resolve(strict=True)
    before = executable.stat()
    if (
        executable != EXPECTED_PQUOTA_EXECUTABLE
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size != EXPECTED_PQUOTA_SIZE_BYTES
        or _sha(_read_regular(executable, maximum=256_000_000))
        != EXPECTED_PQUOTA_SHA256
    ):
        return unavailable("EXECUTABLE_AUTHORITY_MISMATCH")
    result = subprocess.run(
        argv,
        capture_output=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    after = executable.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    reason = "AVAILABLE"
    availability = "AVAILABLE"
    if result.returncode:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_NONZERO_EXIT"
    elif result.stderr:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_STDERR_PRESENT"
    elif len(result.stdout) > 2_000_000:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_OVERSIZED"
    try:
        stdout_text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        availability, reason, stdout_text = (
            "UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_NOT_UTF8", ""
        )
    if len(result.stdout) > 2_000_000:
        stdout_text = ""
    value = _serialize_command_record(
        specification,
        argv,
        executable_sha256=EXPECTED_PQUOTA_SHA256,
        executable_size_bytes=before.st_size,
        exit_status=result.returncode,
        stdout=result.stdout if stdout_text else b"",
        stderr=result.stderr,
    )
    # Preserve the original byte/hash evidence for unavailable non-UTF8 or
    # oversized output while withholding an unsafe/unbounded decoded value.
    if not stdout_text and result.stdout:
        value["stdout_bytes"] = len(result.stdout)
        value["stdout_sha256"] = _sha(result.stdout)
    value["stdout_text"] = stdout_text
    value.update({
        "availability_status": availability,
        "availability_reason": reason,
    })
    return value


def _capture_capacity_commands(
    principal: str,
    *,
    specifications: Sequence[CapacityCommandSpec] = CAPACITY_COMMAND_SPECS,
    required_runner: Callable[
        [CapacityCommandSpec, Sequence[str]], Mapping[str, Any]
    ] | None = None,
    optional_runner: Callable[
        [CapacityCommandSpec, str], Mapping[str, Any]
    ] | None = None,
    resolver: Callable[..., str | None] | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    """Capture every production command from one canonical role registry."""
    if principal != EXPECTED_QUOTA_PRINCIPAL:
        raise PostReallocationCapacityError("CAPACITY_COMMAND_PRINCIPAL_INVALID")
    registry = _validated_command_registry(
        specifications, require_canonical_roles=True
    )
    required_runner = required_runner or _run
    optional_runner = optional_runner or _capture_optional_pquota
    resolver = resolver or shutil.which
    executables = {
        kind: resolver(kind, path="/usr/bin:/bin:/usr/local/bin")
        for kind in {item.command_kind for item in registry.values()}
        if kind != "pquota"
    }
    if any(not executable for executable in executables.values()):
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_FOUND")
    records: list[tuple[str, Mapping[str, Any]]] = []
    for specification in specifications:
        if specification.optional_nonblocking:
            record = optional_runner(specification, principal)
        else:
            executable = executables[specification.command_kind]
            if not executable:
                raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_FOUND")
            record = required_runner(
                specification,
                [executable, *specification.argv_tail],
            )
        records.append((specification.logical_role, record))
    if (
        len(records) != len(CAPACITY_COMMAND_ROLES)
        or len({role for role, _ in records}) != len(records)
        or {role for role, _ in records} != CAPACITY_COMMAND_ROLES
        or any(record.get("role") != role for role, record in records)
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_ROLE_SET_INVALID")
    return dict(records)


def _parse_native_quota_rows(
    payload: bytes,
) -> Mapping[str, Mapping[str, int | str]]:
    """Parse the fixed native quota row shape without allocation policy."""

    observed: dict[str, Mapping[str, int | str]] = {}
    principal_fileset_rows = 0
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PostReallocationCapacityError("NATIVE_QUOTA_NOT_UTF8") from exc
    for line in lines:
        fields = line.split()
        if (
            len(fields) == 14
            and fields[2] == "FILESET"
            and fields[0].endswith(EXPECTED_NATIVE_PRINCIPAL_SUFFIX)
        ):
            principal_fileset_rows += 1
        if not fields or fields[0] not in EXPECTED_NATIVE_ROWS.values():
            continue
        if len(fields) != 14 or fields[2] != "FILESET" or fields[8] != "|":
            raise PostReallocationCapacityError("NATIVE_QUOTA_ROW_LAYOUT_INVALID")
        role = next(key for key, name in EXPECTED_NATIVE_ROWS.items() if name == fields[0])
        if role in observed:
            raise PostReallocationCapacityError("NATIVE_QUOTA_ROW_NOT_UNIQUE")
        # The root-controlled SCC pquota implementation documents column 1 as
        # Name and column 2 as fileset.  Project FILESET rows therefore bind
        # the project authority in fields[0] and the native scope (currently
        # ``root``) in fields[1]; the latter is not the project principal.
        if fields[1] != EXPECTED_NATIVE_FILESET_FIELD:
            raise PostReallocationCapacityError(
                "NATIVE_QUOTA_FILESET_SCOPE_INVALID"
            )
        try:
            usage_kib, quota_kib = int(fields[3]), int(fields[4])
            files_used, file_quota = int(fields[9]), int(fields[10])
        except ValueError as exc:
            raise PostReallocationCapacityError("NATIVE_QUOTA_INTEGER_INVALID") from exc
        observed[role] = {
            "native_name_sha256": _sha(fields[0].encode()),
            "fileset_name_sha256": _sha(fields[1].encode()),
            "usage_kib": usage_kib,
            "quota_kib": quota_kib,
            "files_used": files_used,
            "file_quota": file_quota,
            "raw_row_sha256": _sha(line.encode()),
        }
    if set(observed) != {"backed", "research"}:
        raise PostReallocationCapacityError("NATIVE_QUOTA_ROWS_MISSING")
    if principal_fileset_rows != 2:
        raise PostReallocationCapacityError("NATIVE_QUOTA_ADDITIONAL_PRINCIPAL_ROW")
    if any(
        int(observed[role][field]) < 0
        for role in ("research", "backed")
        for field in ("usage_kib", "quota_kib", "files_used", "file_quota")
    ):
        raise PostReallocationCapacityError("NATIVE_QUOTA_USAGE_INVALID")
    if any(
        int(observed[role]["usage_kib"])
        > int(observed[role]["quota_kib"])
        or int(observed[role]["files_used"])
        > int(observed[role]["file_quota"])
        for role in ("research", "backed")
    ):
        raise PostReallocationCapacityError(
            "NATIVE_QUOTA_USAGE_EXCEEDS_ALLOCATION"
        )
    return observed


def _parse_native_quota(payload: bytes) -> Mapping[str, Mapping[str, int | str]]:
    """Preserve the exact historical allocation-bound parser contract."""

    observed = _parse_native_quota_rows(payload)
    if (
        observed["research"]["quota_kib"] != EXPECTED_RESEARCH_QUOTA_KIB
        or observed["backed"]["quota_kib"] != EXPECTED_BACKED_QUOTA_KIB
        or observed["research"]["file_quota"] != EXPECTED_RESEARCH_FILE_QUOTA
        or observed["backed"]["file_quota"] != EXPECTED_BACKED_FILE_QUOTA
    ):
        raise PostReallocationCapacityError("NATIVE_QUOTA_ALLOCATION_UNEXPECTED")
    return observed


def _parse_dynamic_native_quota(
    payload: bytes,
) -> Mapping[str, Mapping[str, int | str]]:
    """Parse a current allocation without weakening the historical wrapper."""

    try:
        observed = _parse_native_quota_rows(payload)
    except PostReallocationCapacityError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
        ) from exc
    research_quota = int(observed["research"]["quota_kib"])
    research_file_quota = int(observed["research"]["file_quota"])
    if (
        research_quota < EXPECTED_RESEARCH_QUOTA_KIB
        or research_file_quota < EXPECTED_RESEARCH_FILE_QUOTA
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_ALLOCATION_REGRESSION"
        )
    if (
        int(observed["backed"]["quota_kib"]) != EXPECTED_BACKED_QUOTA_KIB
        or int(observed["backed"]["file_quota"])
        != EXPECTED_BACKED_FILE_QUOTA
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_BACKED_TIER_CHANGED"
        )
    return observed


def _display_result(
    status: str,
    reason: str,
    row_counts: Mapping[str, int],
    precision: Mapping[str, int] | None = None,
) -> Mapping[str, Any]:
    result = {
        "status": status,
        "reason": reason,
        "project_row_match_counts": {
            role: int(row_counts.get(role, 0))
            for role in ("backed", "research")
        },
        "usage_decimal_places": dict(precision or {}),
        "rounding_rule": "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6",
    }
    if (
        status not in DISPLAY_CROSSCHECK_STATES
        or reason not in DISPLAY_CROSSCHECK_REASONS
        or reason not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(status, ())
        or set(result) != {
            "status", "reason", "project_row_match_counts",
            "usage_decimal_places", "rounding_rule",
        }
        or set(result["project_row_match_counts"]) != {"backed", "research"}
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in result["project_row_match_counts"].values()
        )
        or not set(result["usage_decimal_places"]).issubset({"backed", "research"})
        or any(
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 6
            for value in result["usage_decimal_places"].values()
        )
    ):
        raise PostReallocationCapacityError("PQUOTA_DISPLAY_RESULT_INTERNAL_INVALID")
    return result


def _parse_pquota(
    text: str,
    native: Mapping[str, Mapping[str, int | str]],
    *,
    command_available: bool = True,
) -> Mapping[str, Any]:
    if not command_available:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE, "COMMAND_UNAVAILABLE", {}
        )
    rows_by_role: dict[str, list[list[str]]] = {
        "backed": [], "research": [],
    }
    for line in text.splitlines():
        fields = re.split(r"[ \t]+", line.strip()) if line.strip() else []
        for role, aliases in EXPECTED_DISPLAY_ROW_ALIASES.items():
            if fields and fields[0] in aliases:
                rows_by_role[role].append(fields)
    row_counts = {
        role: len(matches) for role, matches in rows_by_role.items()
    }
    if any(count > 1 for count in row_counts.values()):
        return _display_result(
            DISPLAY_CROSSCHECK_FAIL,
            "DUPLICATE_EXPECTED_PROJECT_ROW",
            row_counts,
        )
    rows = {
        role: matches[0]
        for role, matches in rows_by_role.items()
        if matches
    }
    if set(rows) != {"backed", "research"}:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE,
            "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
            row_counts,
        )
    precision: dict[str, int] = {}
    contradictions: list[str] = []
    unparseable = False
    for role in ("backed", "research"):
        fields = rows[role]
        if len(fields) != 5:
            unparseable = True
            continue

        displayed_quota: Decimal | None = None
        displayed_file_quota: int | None = None
        displayed_usage: Decimal | None = None
        displayed_files_used: int | None = None
        try:
            displayed_quota = Decimal(fields[1])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_file_quota = int(fields[2])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_usage = Decimal(fields[3])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_files_used = int(fields[4])
        except (ValueError, ArithmeticError):
            unparseable = True

        native_quota_gib = Decimal(int(native[role]["quota_kib"])) / Decimal(1024 ** 2)
        native_usage_gib = Decimal(int(native[role]["usage_kib"])) / Decimal(1024 ** 2)
        if displayed_quota is not None:
            if not displayed_quota.is_finite() or displayed_quota < 0:
                unparseable = True
            elif displayed_quota != native_quota_gib:
                contradictions.append("NOMINAL_QUOTA_CONTRADICTION")
        if displayed_file_quota is not None:
            if displayed_file_quota < 0:
                unparseable = True
            elif displayed_file_quota != int(native[role]["file_quota"]):
                contradictions.append("FILE_QUOTA_CONTRADICTION")
        if displayed_usage is not None:
            if not displayed_usage.is_finite() or displayed_usage < 0:
                unparseable = True
            else:
                places = max(-displayed_usage.as_tuple().exponent, 0)
                if places > 6:
                    unparseable = True
                else:
                    precision[role] = places
                    quantum = Decimal(1).scaleb(-places)
                    if displayed_usage != native_usage_gib.quantize(
                        quantum, rounding=ROUND_HALF_UP
                    ):
                        contradictions.append("ROUNDED_USAGE_CONTRADICTION")
        if displayed_files_used is not None:
            if displayed_files_used < 0:
                unparseable = True
            elif displayed_files_used != int(native[role]["files_used"]):
                contradictions.append("FILE_USAGE_CONTRADICTION")

    if contradictions:
        return _display_result(
            DISPLAY_CROSSCHECK_FAIL, contradictions[0], row_counts, precision
        )
    if unparseable:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE,
            "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
            row_counts,
            precision,
        )
    return _display_result(
        DISPLAY_CROSSCHECK_PASS,
        "MATCHED_NATIVE_AUTHORITY",
        row_counts,
        precision,
    )


def _parse_findmnt(text: str, requested: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs)
    except Exception as exc:
        raise PostReallocationCapacityError("FINDMNT_JSON_INVALID") from exc
    if not isinstance(value, Mapping) or set(value) != {"filesystems"}:
        raise PostReallocationCapacityError("FINDMNT_SCHEMA_INVALID")
    rows = value["filesystems"]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise PostReallocationCapacityError("FINDMNT_ROW_COUNT_INVALID")
    row = rows[0]
    if set(row) != {"source", "target", "fstype", "options", "fsroot"}:
        raise PostReallocationCapacityError("FINDMNT_ROW_SCHEMA_INVALID")
    source, target, fstype, options, fsroot = (
        str(row[key]) for key in ("source", "target", "fstype", "options", "fsroot")
    )
    try:
        requested.relative_to(Path(target))
    except ValueError as exc:
        raise PostReallocationCapacityError("FINDMNT_PATH_NOT_ON_TARGET") from exc
    option_set = set(options.split(","))
    bind = "bind" in option_set or "rbind" in option_set or fsroot != "/"
    return {
        "source_sha256": _sha(source.encode()),
        "target_sha256": _sha(target.encode()),
        "fsroot_sha256": _sha(fsroot.encode()),
        "identity_sha256": _sha(_canonical({
            "source": source, "target": target, "fstype": fstype,
            "fsroot": fsroot,
        })),
        "source": source,
        "target": target,
        "fsroot": fsroot,
        "fstype": fstype,
        "bind": bind,
    }


def _parse_df(text: str, mount: Mapping[str, Any]) -> Mapping[str, int]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 2:
        raise PostReallocationCapacityError("DF_ROW_COUNT_INVALID")
    fields = lines[1].split(maxsplit=4)
    if len(fields) != 5 or fields[0] != mount["source"] or fields[4] != mount["target"]:
        raise PostReallocationCapacityError("DF_MOUNT_IDENTITY_MISMATCH")
    try:
        total, used, available = map(int, fields[1:4])
    except ValueError as exc:
        raise PostReallocationCapacityError("DF_BYTES_INVALID") from exc
    if min(total, used, available) < 0 or used + available > total:
        raise PostReallocationCapacityError("DF_BYTES_DO_NOT_RECONCILE")
    return {"total": total, "used": used, "available": available}


def validate_current_canary_headroom(value: Any) -> dict[str, Any]:
    """Validate the closed aggregate-safe result of the public probe."""

    if not isinstance(value, Mapping) or set(value) != CANARY_HEADROOM_KEYS:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_SCHEMA_NOT_CLOSED"
        )
    integer_fields = (
        "project_quota_remaining_bytes",
        "required_object_bytes",
        "frozen_overhead_bytes",
        "required_remaining_project_bytes",
        "project_file_slots_remaining",
        "required_remaining_file_slots",
        "physical_filesystem_available_bytes",
        "required_physical_available_bytes",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "writes_performed",
    )
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), int)
        or int(value[field]) < 0
        for field in integer_fields
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_VALUE_INVALID"
        )
    if (
        value.get("status") != CANARY_HEADROOM_STATUS
        or value.get("required_object_bytes")
        != CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES
        or value.get("frozen_overhead_bytes")
        != CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
        or value.get("required_remaining_project_bytes")
        != CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value.get("required_physical_available_bytes")
        != CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value.get("required_remaining_file_slots")
        != CANARY_REQUIRED_REMAINING_FILE_SLOTS
        or value.get("project_byte_headroom_passed") is not True
        or value.get("project_file_slot_headroom_passed") is not True
        or value.get("physical_byte_headroom_passed") is not True
        or value.get("native_quota_authority_read_only") is not True
        or value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or value.get("cloud_requests") != 0
        or value.get("scheduler_jobs_submitted") != 0
        or value.get("writes_performed") != 0
        or value["project_quota_remaining_bytes"]
        < CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value["project_file_slots_remaining"]
        < CANARY_REQUIRED_REMAINING_FILE_SLOTS
        or value["physical_filesystem_available_bytes"]
        < CANARY_REQUIRED_REMAINING_PROJECT_BYTES
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_INVARIANT_INVALID"
        )
    return dict(value)


def _validate_current_canary_headroom_authority(
    authority: CurrentCanaryHeadroomAuthority,
) -> bool:
    """Validate the closed path bundle; return whether it is production."""

    if type(authority) is not CurrentCanaryHeadroomAuthority:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
        )
    production = authority == DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    paths = (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
        authority.research_path,
        authority.backed_path,
    )
    if any(
        not isinstance(path, Path)
        or not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        for path in paths
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
        )
    production_file_authorities = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.native_quota_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.pquota_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.findmnt_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.df_path,
    )
    supplied_file_authorities = (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    )
    if not production and any(
        supplied == frozen
        for supplied, frozen in zip(
            supplied_file_authorities,
            production_file_authorities,
            strict=True,
        )
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_MIXED"
        )
    tools_by_role = {
        "pquota": authority.pquota_path,
        "findmnt": authority.findmnt_path,
        "df": authority.df_path,
    }
    if (
        len(set(tools_by_role.values())) != len(tools_by_role)
        or any(path.name != role for role, path in tools_by_role.items())
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_TOOL_ROLE_INVALID"
        )
    permitted_uids = {0} if production else {0, os.geteuid()}
    for path in (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    ):
        _no_symlink_ancestors(path, "CURRENT_CANARY_HEADROOM_AUTHORITY")
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in permitted_uids
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
            )
    for path in (
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    ):
        if not stat.S_IMODE(os.lstat(path).st_mode) & 0o111:
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_TOOL_NOT_EXECUTABLE"
            )
    _path_identity(authority.research_path)
    _path_identity(authority.backed_path)
    if production and (
        os.lstat(authority.pquota_path).st_size != EXPECTED_PQUOTA_SIZE_BYTES
        or _sha(_read_regular(authority.pquota_path, maximum=256_000_000))
        != EXPECTED_PQUOTA_SHA256
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_PQUOTA_AUTHORITY_MISMATCH"
        )
    return production


def _current_canary_command_argv(
    specification: CapacityCommandSpec,
    authority: CurrentCanaryHeadroomAuthority,
) -> list[str]:
    executables = {
        "pquota": authority.pquota_path,
        "findmnt": authority.findmnt_path,
        "df": authority.df_path,
    }
    targets = {
        "research_findmnt": authority.research_path,
        "backed_findmnt": authority.backed_path,
        "research_df": authority.research_path,
        "backed_df": authority.backed_path,
    }
    if specification.logical_role == "pquota":
        tail = ("-u", EXPECTED_QUOTA_PRINCIPAL)
    elif specification.command_kind == "findmnt":
        tail = (
            "--json",
            "--target",
            str(targets[specification.logical_role]),
            "--output",
            "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        )
    elif specification.command_kind == "df":
        tail = (
            "-B1",
            "--output=source,size,used,avail,target",
            str(targets[specification.logical_role]),
        )
    else:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_COMMAND_INVALID"
        )
    return [str(executables[specification.command_kind]), *tail]


def _run_current_canary_pquota(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    process_runner: Callable[..., Any] | None,
) -> Mapping[str, Any]:
    """Run the mandatory tool while keeping its display nonblocking."""

    executable = Path(argv[0])
    before = executable.stat()
    runner = process_runner or subprocess.run
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
    after = executable.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    availability, reason = "AVAILABLE", "AVAILABLE"
    if result.returncode:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_NONZERO_EXIT",
        )
    elif result.stderr:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_STDERR_PRESENT",
        )
    elif len(result.stdout) > 2_000_000:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_OUTPUT_OVERSIZED",
        )
    try:
        stdout = (
            result.stdout
            if len(result.stdout) <= 2_000_000
            else b""
        )
        stdout.decode("utf-8")
    except UnicodeDecodeError:
        availability, reason, stdout = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_OUTPUT_NOT_UTF8",
            b"",
        )
    record = _serialize_command_record(
        specification,
        argv,
        executable_sha256=_sha(
            _read_regular(executable, maximum=256_000_000)
        ),
        executable_size_bytes=before.st_size,
        exit_status=result.returncode,
        stdout=stdout,
        stderr=result.stderr,
    )
    record.update(
        {
            "availability_status": availability,
            "availability_reason": reason,
        }
    )
    return record


def probe_current_canary_headroom(
    authority: CurrentCanaryHeadroomAuthority = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    ),
    *,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read and validate contemporaneous exact-five canary headroom.

    The probe is deliberately read-only: it executes only the canonical
    pquota/findmnt/df registry and reads the fixed native quota authority.  It
    requires exact remaining project quota for the 5,000,000,000-byte object
    ceiling plus the tracked 5,000,000,000-byte manifest/metadata reserve,
    2,048 project file slots, and the same 10,000,000,000 physical bytes from
    ``df -B1``.  The closed path bundle defaults to the frozen SCC authority;
    a sandbox bundle and subprocess runner may be injected for synthetic tests.
    """

    production = _validate_current_canary_headroom_authority(authority)
    permitted_owner_uids = (
        frozenset({0}) if production else frozenset({0, os.geteuid()})
    )
    commands: dict[str, Mapping[str, Any]] = {}
    for specification in CAPACITY_COMMAND_SPECS:
        argv = _current_canary_command_argv(specification, authority)
        if specification.logical_role == "pquota":
            record = dict(
                _run_current_canary_pquota(
                    specification,
                    argv,
                    process_runner=process_runner,
                )
            )
        else:
            record = dict(
                _run(
                    specification,
                    argv,
                    process_runner=process_runner,
                    permitted_owner_uids=permitted_owner_uids,
                )
            )
        commands[specification.logical_role] = record
    if set(commands) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_COMMAND_INVALID"
        )
    native_payload = _read_regular(
        authority.native_quota_path, maximum=64_000_000
    )
    native = _parse_native_quota(native_payload)
    if any(
        int(native[role][field]) < 0
        for role in ("research", "backed")
        for field in ("usage_kib", "quota_kib", "files_used", "file_quota")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_NATIVE_USAGE_INVALID"
        )
    paths = {
        "research": _path_identity(authority.research_path),
        "backed": _path_identity(authority.backed_path),
    }
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]][
                "stdout_text"
            ],
            authority.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]][
                "stdout_text"
            ],
            authority.backed_path,
        ),
    }
    if production:
        _validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=mounts
        )
    elif any(
        paths[role]["is_symlink"] is not False
        or mounts[role]["bind"] is not False
        or mounts[role]["fsroot"] != "/"
        or native[role]["native_name_sha256"]
        != _sha(EXPECTED_NATIVE_ROWS[role].encode())
        for role in ("research", "backed")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_MOUNT_RECONCILIATION_FAILED"
        )
    research_df = _parse_df(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_df"]][
            "stdout_text"
        ],
        mounts["research"],
    )
    # Parse the backed result as well: the exact canonical command registry is
    # all-or-nothing even though this canary writes only to the research tier.
    _parse_df(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_df"]][
            "stdout_text"
        ],
        mounts["backed"],
    )
    display_command = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_command["stdout_text"],
        native,
        command_available=(
            display_command.get("availability_status") == "AVAILABLE"
        ),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_PQUOTA_CONTRADICTION"
        )

    quota_remaining = max(
        int(native["research"]["quota_kib"])
        - int(native["research"]["usage_kib"]),
        0,
    ) * 1024
    file_slots_remaining = max(
        int(native["research"]["file_quota"])
        - int(native["research"]["files_used"]),
        0,
    )
    physical_available = int(research_df["available"])
    if quota_remaining < CANARY_REQUIRED_REMAINING_PROJECT_BYTES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_PROJECT_BYTE_HEADROOM_INSUFFICIENT"
        )
    if file_slots_remaining < CANARY_REQUIRED_REMAINING_FILE_SLOTS:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_FILE_SLOT_HEADROOM_INSUFFICIENT"
        )
    if physical_available < CANARY_REQUIRED_REMAINING_PROJECT_BYTES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_PHYSICAL_BYTE_HEADROOM_INSUFFICIENT"
        )
    return validate_current_canary_headroom(
        {
            "status": CANARY_HEADROOM_STATUS,
            "project_quota_remaining_bytes": quota_remaining,
            "required_object_bytes": CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES,
            "frozen_overhead_bytes": (
                CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
            ),
            "required_remaining_project_bytes": (
                CANARY_REQUIRED_REMAINING_PROJECT_BYTES
            ),
            "project_file_slots_remaining": file_slots_remaining,
            "required_remaining_file_slots": (
                CANARY_REQUIRED_REMAINING_FILE_SLOTS
            ),
            "physical_filesystem_available_bytes": physical_available,
            "required_physical_available_bytes": (
                CANARY_REQUIRED_REMAINING_PROJECT_BYTES
            ),
            "project_byte_headroom_passed": True,
            "project_file_slot_headroom_passed": True,
            "physical_byte_headroom_passed": True,
            "native_quota_authority_read_only": True,
            "pquota_display_crosscheck": str(display["status"]),
            "cloud_requests": 0,
            "scheduler_jobs_submitted": 0,
            "writes_performed": 0,
        }
    )


def validate_current_full_headroom(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed aggregate-safe result of the full C3 probe."""

    if set(value) != FULL_HEADROOM_KEYS:
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_SCHEMA_INVALID")
    integer_fields = FULL_HEADROOM_KEYS - {
        "status",
        "pquota_display_crosscheck",
        "native_quota_authority_read_only",
        "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate_passed",
    }
    boolean_fields = FULL_HEADROOM_KEYS - integer_fields - {
        "status", "pquota_display_crosscheck"
    }
    if (
        value.get("status") != FULL_HEADROOM_STATUS
        or value.get("pquota_display_crosscheck") not in {
            DISPLAY_CROSSCHECK_PASS,
            DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or any(
            not isinstance(value.get(name), int)
            or isinstance(value.get(name), bool)
            or int(value[name]) < 0
            for name in integer_fields
        )
        or any(value.get(name) is not True for name in boolean_fields)
        or value.get("selected_source_bytes") != SELECTED_SOURCE_BYTES
        or value.get("projected_peak_bytes") != PROJECTED_PEAK_BYTES
        or value.get("required_free_headroom_bytes")
        != REQUIRED_FREE_HEADROOM_BYTES
        or value.get("research_additional_file_demand")
        != RESEARCH_ADDITIONAL_FILE_DEMAND
        or value.get("backed_control_burden_bytes")
        != PRESPECIFIED_CONTROL_BURDEN_BYTES
        or value.get("backed_additional_file_demand")
        != CONTROL_ADDITIONAL_FILE_DEMAND
        or value.get("cloud_requests") != 0
        or value.get("scheduler_jobs_submitted") != 0
        or value.get("writes_performed") != 0
    ):
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_INVALID")

    research_quota = int(value["research_quota_bytes"])
    research_usage = int(value["research_usage_bytes"])
    research_file_quota = int(value["research_file_quota"])
    research_files_used = int(value["research_files_used"])
    research_available = int(value["research_filesystem_available_bytes"])
    backed_quota = int(value["backed_quota_bytes"])
    backed_usage = int(value["backed_usage_bytes"])
    backed_file_quota = int(value["backed_file_quota"])
    backed_files_used = int(value["backed_files_used"])
    if (
        research_quota != EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or backed_quota != EXPECTED_BACKED_QUOTA_KIB * 1024
        or research_file_quota != EXPECTED_RESEARCH_FILE_QUOTA
        or backed_file_quota != EXPECTED_BACKED_FILE_QUOTA
        or research_usage > research_quota
        or backed_usage > backed_quota
        or research_files_used > research_file_quota
        or backed_files_used > backed_file_quota
    ):
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_INVALID")

    remaining_write = max(PROJECTED_PEAK_BYTES - research_usage, 0)
    physical_required = (
        remaining_write
        + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
    )
    quota_slack = research_quota - PROJECTED_PEAK_BYTES
    reserve_margin = quota_slack - REQUIRED_FREE_HEADROOM_BYTES
    derived: dict[str, int | bool] = {
        "research_quota_remaining_bytes": research_quota - research_usage,
        "research_file_slots_remaining": (
            research_file_quota - research_files_used
        ),
        "backed_quota_remaining_bytes": backed_quota - backed_usage,
        "backed_file_slots_remaining": backed_file_quota - backed_files_used,
        "research_physical_required_available_bytes": physical_required,
        "research_quota_slack_after_projected_peak_bytes": quota_slack,
        "research_margin_beyond_200gb_reserve_bytes": reserve_margin,
        "research_physical_slack_bytes": research_available - physical_required,
        "research_quota_gate_passed": (
            research_quota >= MINIMUM_EFFECTIVE_QUOTA_BYTES
        ),
        "physical_filesystem_capacity_gate_passed": (
            research_available >= physical_required
        ),
        "projected_200gb_reserve_gate_passed": (
            quota_slack >= REQUIRED_FREE_HEADROOM_BYTES
        ),
        "research_file_quota_gate_passed": (
            research_file_quota - research_files_used
            >= RESEARCH_ADDITIONAL_FILE_DEMAND
        ),
        "backed_control_tier_byte_gate_passed": (
            backed_quota - backed_usage
            >= PRESPECIFIED_CONTROL_BURDEN_BYTES
        ),
        "backed_control_tier_file_gate_passed": (
            backed_file_quota - backed_files_used
            >= CONTROL_ADDITIONAL_FILE_DEMAND
        ),
    }
    derived["backed_control_tier_gate_passed"] = bool(
        derived["backed_control_tier_byte_gate_passed"]
        and derived["backed_control_tier_file_gate_passed"]
    )
    if any(value.get(name) != expected for name, expected in derived.items()):
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_INVALID")
    return dict(value)


def _capture_current_capacity_snapshot(
    authority: CurrentCanaryHeadroomAuthority,
    *,
    process_runner: Callable[..., Any] | None,
) -> dict[str, Any]:
    """Capture the fixed native-quota/findmnt/df registry exactly once."""

    _validated_command_registry(
        CAPACITY_COMMAND_SPECS, require_canonical_roles=True
    )
    production = _validate_current_canary_headroom_authority(authority)
    permitted_owner_uids = (
        frozenset({0}) if production else frozenset({0, os.geteuid()})
    )
    commands: dict[str, Mapping[str, Any]] = {}
    for specification in CAPACITY_COMMAND_SPECS:
        argv = _current_canary_command_argv(specification, authority)
        if specification.logical_role == "pquota":
            record = _run_current_canary_pquota(
                specification, argv, process_runner=process_runner
            )
        else:
            record = _run(
                specification,
                argv,
                process_runner=process_runner,
                permitted_owner_uids=permitted_owner_uids,
            )
        commands[specification.logical_role] = dict(record)
    if set(commands) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "CURRENT_CAPACITY_SNAPSHOT_COMMAND_INVALID"
        )

    native = _parse_dynamic_native_quota(
        _read_regular(authority.native_quota_path, maximum=64_000_000)
    )
    paths = {
        "research": _path_identity(authority.research_path),
        "backed": _path_identity(authority.backed_path),
    }
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]][
                "stdout_text"
            ],
            authority.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]][
                "stdout_text"
            ],
            authority.backed_path,
        ),
    }
    if production:
        _validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=mounts
        )
    elif any(
        paths[role]["is_symlink"] is not False
        or mounts[role]["bind"] is not False
        or mounts[role]["fsroot"] != "/"
        or native[role]["native_name_sha256"]
        != _sha(EXPECTED_NATIVE_ROWS[role].encode())
        for role in ("research", "backed")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CAPACITY_SNAPSHOT_MOUNT_RECONCILIATION_FAILED"
        )
    dfs = {
        role: _parse_df(
            commands[
                CAPACITY_COMMAND_CONSUMER_ROLES[f"{role}_df"]
            ]["stdout_text"],
            mounts[role],
        )
        for role in ("research", "backed")
    }
    display_record = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_record["stdout_text"],
        native,
        command_available=(
            display_record.get("availability_status") == "AVAILABLE"
        ),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "CURRENT_CAPACITY_SNAPSHOT_PQUOTA_CONTRADICTION"
        )
    return {
        "native": native,
        "dfs": dfs,
        "pquota_display_crosscheck": str(display["status"]),
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": len(commands),
        "pquota_command_captures": sum(
            specification.command_kind == "pquota"
            for specification in CAPACITY_COMMAND_SPECS
        ),
        "findmnt_command_captures": sum(
            specification.command_kind == "findmnt"
            for specification in CAPACITY_COMMAND_SPECS
        ),
        "df_command_captures": sum(
            specification.command_kind == "df"
            for specification in CAPACITY_COMMAND_SPECS
        ),
    }


def probe_current_full_headroom(
    authority: CurrentCanaryHeadroomAuthority = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    ),
    *,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Capture current full-cohort quota, physical, and file headroom read-only.

    This uses the same fixed native quota file and root-controlled
    ``pquota``/``findmnt``/``df`` command registry as the exact-five probe.  It
    writes no receipt itself; the full launch claim may persist the returned
    closed mapping exactly once after all no-body gates pass.
    """

    production = _validate_current_canary_headroom_authority(authority)
    permitted_owner_uids = (
        frozenset({0}) if production else frozenset({0, os.geteuid()})
    )
    commands: dict[str, Mapping[str, Any]] = {}
    for specification in CAPACITY_COMMAND_SPECS:
        argv = _current_canary_command_argv(specification, authority)
        if specification.logical_role == "pquota":
            record = _run_current_canary_pquota(
                specification, argv, process_runner=process_runner
            )
        else:
            record = _run(
                specification,
                argv,
                process_runner=process_runner,
                permitted_owner_uids=permitted_owner_uids,
            )
        commands[specification.logical_role] = dict(record)
    if set(commands) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_COMMAND_INVALID")

    native = _parse_native_quota(
        _read_regular(authority.native_quota_path, maximum=64_000_000)
    )
    paths = {
        "research": _path_identity(authority.research_path),
        "backed": _path_identity(authority.backed_path),
    }
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]][
                "stdout_text"
            ],
            authority.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]][
                "stdout_text"
            ],
            authority.backed_path,
        ),
    }
    if production:
        _validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=mounts
        )
    elif any(
        paths[role]["is_symlink"] is not False
        or mounts[role]["bind"] is not False
        or mounts[role]["fsroot"] != "/"
        or native[role]["native_name_sha256"]
        != _sha(EXPECTED_NATIVE_ROWS[role].encode())
        for role in ("research", "backed")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_FULL_HEADROOM_MOUNT_RECONCILIATION_FAILED"
        )
    dfs = {
        role: _parse_df(
            commands[
                CAPACITY_COMMAND_CONSUMER_ROLES[f"{role}_df"]
            ]["stdout_text"],
            mounts[role],
        )
        for role in ("research", "backed")
    }
    display_record = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_record["stdout_text"],
        native,
        command_available=(display_record.get("availability_status") == "AVAILABLE"),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "CURRENT_FULL_HEADROOM_PQUOTA_CONTRADICTION"
        )

    research_quota = int(native["research"]["quota_kib"]) * 1024
    research_usage = int(native["research"]["usage_kib"]) * 1024
    research_remaining = max(research_quota - research_usage, 0)
    research_slots = max(
        int(native["research"]["file_quota"])
        - int(native["research"]["files_used"]),
        0,
    )
    backed_quota = int(native["backed"]["quota_kib"]) * 1024
    backed_usage = int(native["backed"]["usage_kib"]) * 1024
    backed_remaining = max(backed_quota - backed_usage, 0)
    backed_slots = max(
        int(native["backed"]["file_quota"])
        - int(native["backed"]["files_used"]),
        0,
    )
    remaining_write = max(PROJECTED_PEAK_BYTES - research_usage, 0)
    physical_required = (
        remaining_write
        + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
    )
    quota_slack = max(research_quota - PROJECTED_PEAK_BYTES, 0)
    reserve_margin = max(quota_slack - REQUIRED_FREE_HEADROOM_BYTES, 0)
    physical_available = int(dfs["research"]["available"])
    gates = {
        "research_quota_gate_passed": (
            research_quota >= MINIMUM_EFFECTIVE_QUOTA_BYTES
        ),
        "physical_filesystem_capacity_gate_passed": (
            physical_available >= physical_required
        ),
        "projected_200gb_reserve_gate_passed": (
            research_quota - PROJECTED_PEAK_BYTES
            >= REQUIRED_FREE_HEADROOM_BYTES
        ),
        "research_file_quota_gate_passed": (
            research_slots >= RESEARCH_ADDITIONAL_FILE_DEMAND
        ),
        "backed_control_tier_byte_gate_passed": (
            backed_remaining >= PRESPECIFIED_CONTROL_BURDEN_BYTES
        ),
        "backed_control_tier_file_gate_passed": (
            backed_slots >= CONTROL_ADDITIONAL_FILE_DEMAND
        ),
    }
    gates["backed_control_tier_gate_passed"] = (
        gates["backed_control_tier_byte_gate_passed"]
        and gates["backed_control_tier_file_gate_passed"]
    )
    if not all(gates.values()):
        raise PostReallocationCapacityError("CURRENT_FULL_HEADROOM_INSUFFICIENT")
    return validate_current_full_headroom(
        {
            "status": FULL_HEADROOM_STATUS,
            "research_quota_bytes": research_quota,
            "research_usage_bytes": research_usage,
            "research_quota_remaining_bytes": research_remaining,
            "research_file_quota": int(native["research"]["file_quota"]),
            "research_files_used": int(native["research"]["files_used"]),
            "research_file_slots_remaining": research_slots,
            "research_filesystem_available_bytes": physical_available,
            "backed_quota_bytes": backed_quota,
            "backed_usage_bytes": backed_usage,
            "backed_quota_remaining_bytes": backed_remaining,
            "backed_file_quota": int(native["backed"]["file_quota"]),
            "backed_files_used": int(native["backed"]["files_used"]),
            "backed_file_slots_remaining": backed_slots,
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "research_physical_required_available_bytes": physical_required,
            "research_quota_slack_after_projected_peak_bytes": quota_slack,
            "research_margin_beyond_200gb_reserve_bytes": reserve_margin,
            "research_physical_slack_bytes": max(
                physical_available - physical_required, 0
            ),
            "research_additional_file_demand": RESEARCH_ADDITIONAL_FILE_DEMAND,
            "backed_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "backed_additional_file_demand": CONTROL_ADDITIONAL_FILE_DEMAND,
            **gates,
            "native_quota_authority_read_only": True,
            "pquota_display_crosscheck": str(display["status"]),
            "cloud_requests": 0,
            "scheduler_jobs_submitted": 0,
            "writes_performed": 0,
        }
    )


def _fixed_r8r_continuation_batches(
    plan: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Bind one exact original plan and return only its fixed tasks 4--19."""

    if not isinstance(plan, Mapping):
        raise PostReallocationCapacityError("R8R_FIXED_PLAN_SCHEMA_INVALID")
    try:
        plan_payload = json.dumps(
            plan,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise PostReallocationCapacityError(
            "R8R_FIXED_PLAN_SCHEMA_INVALID"
        ) from exc
    if _sha(plan_payload) != R8R_ORIGINAL_PLAN_SHA256:
        raise PostReallocationCapacityError("R8R_FIXED_PLAN_SHA256_MISMATCH")

    authority = plan.get("authority")
    cohort = plan.get("cohort")
    batches = plan.get("batches")
    if (
        plan.get("schema_version") != 3
        or plan.get("artifact_type")
        != "lvef_c3_restricted_immutable_batch_plan_v3"
        or not isinstance(authority, Mapping)
        or authority.get("git_commit") != R8R_ORIGINAL_SCIENTIFIC_COMMIT
        or not isinstance(cohort, Mapping)
        or not isinstance(batches, list)
        or len(batches) != R8R_CONTINUATION_LAST_TASK
    ):
        raise PostReallocationCapacityError("R8R_FIXED_PLAN_AUTHORITY_INVALID")

    integer_fields = ("n_studies", "n_objects", "source_bytes")
    for ordinal, batch in enumerate(batches):
        if (
            not isinstance(batch, Mapping)
            or batch.get("batch_id") != f"c3_batch_{ordinal:03d}"
            or batch.get("ordinal") != ordinal
            or any(
                not isinstance(batch.get(field), int)
                or isinstance(batch.get(field), bool)
                or int(batch[field]) <= 0
                for field in integer_fields
            )
            or int(batch["n_studies"])
            != (30 if ordinal == 18 else 250)
        ):
            raise PostReallocationCapacityError(
                "R8R_FIXED_PLAN_TOPOLOGY_INVALID"
            )

    full_aggregates = (
        sum(int(batch["n_studies"]) for batch in batches),
        sum(int(batch["n_objects"]) for batch in batches),
        sum(int(batch["source_bytes"]) for batch in batches),
    )
    if (
        full_aggregates
        != (
            R8R_ORIGINAL_STUDIES,
            R8R_ORIGINAL_OBJECTS,
            R8R_ORIGINAL_SOURCE_BYTES,
        )
        or cohort.get("selected_studies") != R8R_ORIGINAL_STUDIES
        or cohort.get("normalized_source_objects") != R8R_ORIGINAL_OBJECTS
        or cohort.get("selected_source_bytes") != R8R_ORIGINAL_SOURCE_BYTES
    ):
        raise PostReallocationCapacityError(
            "R8R_FIXED_PLAN_AGGREGATE_INVALID"
        )

    remaining = batches[
        R8R_CONTINUATION_FIRST_BATCH_INDEX:
        R8R_CONTINUATION_EXCLUSIVE_LAST_BATCH_INDEX
    ]
    remaining_aggregates = (
        len(remaining),
        sum(int(batch["n_studies"]) for batch in remaining),
        sum(int(batch["n_objects"]) for batch in remaining),
        sum(int(batch["source_bytes"]) for batch in remaining),
    )
    if remaining_aggregates != (
        R8R_CONTINUATION_TASK_COUNT,
        R8R_CONTINUATION_REMAINING_STUDIES,
        R8R_CONTINUATION_REMAINING_OBJECTS,
        R8R_CONTINUATION_REMAINING_SOURCE_BYTES,
    ):
        raise PostReallocationCapacityError(
            "R8R_FIXED_PLAN_CONTINUATION_AGGREGATE_INVALID"
        )
    return remaining


def probe_fixed_r8r_continuation_capacity(
    plan: Mapping[str, Any],
    *,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Adjudicate fixed original-attempt tasks 4--19 without publication.

    Byte demand is derived only from ``plan["batches"][3:19]``: all remaining
    raw source bytes, the largest remaining transfer retry, one largest rolling
    extracted cache at 4,816,896 bytes per object, clip/study embedding upper
    bounds at 4,096 bytes per object/study, and the fixed audit, manifest, log,
    preservation/finalization, and safety burdens.  Required file slots are
    exactly ``remaining objects + largest rolling batch objects + 100,000``;
    the last term is the fixed control-file allowance.  Quota and physical
    margins must each retain 200,000,000,000 bytes after this increment.

    The caller can supply only the already-loaded exact plan and an optional
    subprocess runner for dependency-light tests.  The native quota file and
    canonical pquota/findmnt/df registry are captured once.  This function
    performs no write, scheduler submission, data-body read, or publication.
    """

    remaining = _fixed_r8r_continuation_batches(plan)
    snapshot = _capture_current_capacity_snapshot(
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=process_runner,
    )
    native = snapshot["native"]
    dfs = snapshot["dfs"]

    remaining_studies = sum(int(row["n_studies"]) for row in remaining)
    remaining_objects = sum(int(row["n_objects"]) for row in remaining)
    remaining_source_bytes = sum(
        int(row["source_bytes"]) for row in remaining
    )
    largest_remaining_objects = max(
        int(row["n_objects"]) for row in remaining
    )
    largest_remaining_source_bytes = max(
        int(row["source_bytes"]) for row in remaining
    )
    rolling_extracted_bytes = (
        largest_remaining_objects * R8R_EXTRACTED_BYTES_PER_OBJECT
    )
    clip_embedding_bytes = (
        remaining_objects * R8R_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_embedding_bytes = (
        remaining_studies * R8R_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    continuation_increment = sum(
        (
            remaining_source_bytes,
            largest_remaining_source_bytes,
            rolling_extracted_bytes,
            clip_embedding_bytes,
            study_embedding_bytes,
            R8R_RETAINED_EXTRACTED_AUDIT_BYTES,
            R8R_MANIFEST_AND_METADATA_BYTES,
            R8R_LOG_BYTES,
            R8R_PRESERVATION_AND_FINALIZATION_BYTES,
            R8R_SAFETY_BYTES,
        )
    )
    required_file_slots = (
        remaining_objects
        + largest_remaining_objects
        + R8R_FIXED_CONTROL_FILE_DEMAND
    )

    research_quota = int(native["research"]["quota_kib"]) * 1024
    research_usage = int(native["research"]["usage_kib"]) * 1024
    research_file_quota = int(native["research"]["file_quota"])
    research_files_used = int(native["research"]["files_used"])
    physical_available = int(dfs["research"]["available"])
    projected_usage = research_usage + continuation_increment
    quota_slack = research_quota - projected_usage
    physical_slack = physical_available - continuation_increment
    quota_margin = quota_slack - R8R_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8R_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = research_file_quota - research_files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for field, code in (
            (
                "quota_reserve_gate_passed",
                "R8R_CONTINUATION_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8R_CONTINUATION_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8R_CONTINUATION_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[field] is not True
    ]
    status = (
        R8R_CONTINUATION_STATUS_PASS
        if not blocking_reason_codes
        else R8R_CONTINUATION_STATUS_BLOCKED
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": R8R_CONTINUATION_ARTIFACT_TYPE,
        "status": status,
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8R_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8R_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8R_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "continuation_first_task": R8R_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8R_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8R_CONTINUATION_TASK_COUNT,
        "remaining_batch_count": len(remaining),
        "remaining_studies": remaining_studies,
        "remaining_objects": remaining_objects,
        "remaining_source_bytes": remaining_source_bytes,
        "largest_remaining_batch_objects": largest_remaining_objects,
        "largest_remaining_batch_source_bytes": (
            largest_remaining_source_bytes
        ),
        "remaining_raw_source_demand_bytes": remaining_source_bytes,
        "largest_remaining_transfer_retry_demand_bytes": (
            largest_remaining_source_bytes
        ),
        "largest_rolling_extracted_cache_demand_bytes": (
            rolling_extracted_bytes
        ),
        "remaining_clip_embedding_upper_bound_bytes": (
            clip_embedding_bytes
        ),
        "remaining_study_embedding_upper_bound_bytes": (
            study_embedding_bytes
        ),
        "retained_extracted_audit_demand_bytes": (
            R8R_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            R8R_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": R8R_LOG_BYTES,
        "preservation_and_finalization_demand_bytes": (
            R8R_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8R_SAFETY_BYTES,
        "continuation_increment_bytes": continuation_increment,
        "remaining_raw_object_file_demand": remaining_objects,
        "largest_rolling_object_file_demand": largest_remaining_objects,
        "fixed_control_file_demand": R8R_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": required_file_slots,
        "research_quota_bytes": research_quota,
        "research_usage_bytes": research_usage,
        "research_quota_remaining_bytes": research_quota - research_usage,
        "research_file_quota": research_file_quota,
        "research_files_used": research_files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": int(dfs["research"]["total"]),
        "research_filesystem_used_bytes": int(dfs["research"]["used"]),
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8R_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8R_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_continuation_bytes": quota_slack,
        "physical_slack_after_continuation_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": int(
            snapshot["native_capacity_snapshot_captures"]
        ),
        "native_quota_file_captures": int(
            snapshot["native_quota_file_captures"]
        ),
        "capacity_command_captures": int(
            snapshot["capacity_command_captures"]
        ),
        "pquota_command_captures": int(
            snapshot["pquota_command_captures"]
        ),
        "findmnt_command_captures": int(
            snapshot["findmnt_command_captures"]
        ),
        "df_command_captures": int(snapshot["df_command_captures"]),
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": str(
            snapshot["pquota_display_crosscheck"]
        ),
        **{key: 0 for key in R8R_CONTINUATION_ZERO_EFFECT_KEYS},
    }
    if set(result) != R8R_CONTINUATION_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8R_CONTINUATION_CAPACITY_SCHEMA_INVALID"
        )
    return validate_fixed_r8r_continuation_capacity(plan, result)


def validate_fixed_r8r_continuation_capacity(
    plan: Mapping[str, Any], value: Mapping[str, Any]
) -> dict[str, Any]:
    """Replay the fixed continuation arithmetic without a live probe.

    The live submitter captures the native capacity fields once. Delayed
    workers and the held finalizer use this pure validator to rederive every
    plan-dependent demand, margin, gate, and status from the sealed fields.
    """

    remaining = _fixed_r8r_continuation_batches(plan)
    if (
        not isinstance(value, Mapping)
        or set(value) != R8R_CONTINUATION_CAPACITY_KEYS
    ):
        raise PostReallocationCapacityError(
            "R8R_CONTINUATION_CAPACITY_SCHEMA_INVALID"
        )
    integer_fields = {
        "schema_version",
        "continuation_first_task",
        "continuation_last_task",
        "continuation_task_count",
        "remaining_batch_count",
        "remaining_studies",
        "remaining_objects",
        "remaining_source_bytes",
        "largest_remaining_batch_objects",
        "largest_remaining_batch_source_bytes",
        "remaining_raw_source_demand_bytes",
        "largest_remaining_transfer_retry_demand_bytes",
        "largest_rolling_extracted_cache_demand_bytes",
        "remaining_clip_embedding_upper_bound_bytes",
        "remaining_study_embedding_upper_bound_bytes",
        "retained_extracted_audit_demand_bytes",
        "manifest_and_metadata_demand_bytes",
        "log_demand_bytes",
        "preservation_and_finalization_demand_bytes",
        "safety_demand_bytes",
        "continuation_increment_bytes",
        "remaining_raw_object_file_demand",
        "largest_rolling_object_file_demand",
        "fixed_control_file_demand",
        "required_file_slots",
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
        "quota_slack_after_continuation_bytes",
        "physical_slack_after_continuation_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
        "native_capacity_snapshot_captures",
        "native_quota_file_captures",
        "capacity_command_captures",
        "pquota_command_captures",
        "findmnt_command_captures",
        "df_command_captures",
        *R8R_CONTINUATION_ZERO_EFFECT_KEYS,
    }
    boolean_fields = {
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "native_quota_authority_read_only",
    }
    if (
        any(type(value.get(key)) is not int for key in integer_fields)
        or any(type(value.get(key)) is not bool for key in boolean_fields)
    ):
        raise PostReallocationCapacityError(
            "R8R_CONTINUATION_CAPACITY_SCHEMA_INVALID"
        )

    remaining_studies = sum(int(row["n_studies"]) for row in remaining)
    remaining_objects = sum(int(row["n_objects"]) for row in remaining)
    remaining_source_bytes = sum(
        int(row["source_bytes"]) for row in remaining
    )
    largest_objects = max(int(row["n_objects"]) for row in remaining)
    largest_source_bytes = max(
        int(row["source_bytes"]) for row in remaining
    )
    rolling_extracted_bytes = (
        largest_objects * R8R_EXTRACTED_BYTES_PER_OBJECT
    )
    clip_embedding_bytes = (
        remaining_objects * R8R_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_embedding_bytes = (
        remaining_studies * R8R_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    continuation_increment = sum(
        (
            remaining_source_bytes,
            largest_source_bytes,
            rolling_extracted_bytes,
            clip_embedding_bytes,
            study_embedding_bytes,
            R8R_RETAINED_EXTRACTED_AUDIT_BYTES,
            R8R_MANIFEST_AND_METADATA_BYTES,
            R8R_LOG_BYTES,
            R8R_PRESERVATION_AND_FINALIZATION_BYTES,
            R8R_SAFETY_BYTES,
        )
    )
    required_file_slots = (
        remaining_objects
        + largest_objects
        + R8R_FIXED_CONTROL_FILE_DEMAND
    )
    quota = int(value["research_quota_bytes"])
    usage = int(value["research_usage_bytes"])
    file_quota = int(value["research_file_quota"])
    files_used = int(value["research_files_used"])
    physical_available = int(
        value["research_filesystem_available_bytes"]
    )
    if (
        min(
            quota,
            usage,
            file_quota,
            files_used,
            int(value["research_filesystem_total_bytes"]),
            int(value["research_filesystem_used_bytes"]),
            physical_available,
        )
        < 0
        or quota == 0
        or file_quota == 0
        or int(value["research_filesystem_total_bytes"]) == 0
        or quota % 1024 != 0
        or usage % 1024 != 0
        or quota < EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or file_quota < EXPECTED_RESEARCH_FILE_QUOTA
        or usage > quota
        or files_used > file_quota
        or int(value["research_filesystem_used_bytes"])
        + physical_available
        > int(value["research_filesystem_total_bytes"])
    ):
        raise PostReallocationCapacityError(
            "R8R_CONTINUATION_CAPACITY_SCHEMA_INVALID"
        )
    projected_usage = usage + continuation_increment
    quota_slack = quota - projected_usage
    physical_slack = physical_available - continuation_increment
    quota_margin = quota_slack - R8R_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8R_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = file_quota - files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for key, code in (
            (
                "quota_reserve_gate_passed",
                "R8R_CONTINUATION_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8R_CONTINUATION_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8R_CONTINUATION_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[key] is not True
    ]
    expected = {
        "schema_version": 1,
        "artifact_type": R8R_CONTINUATION_ARTIFACT_TYPE,
        "status": (
            R8R_CONTINUATION_STATUS_PASS
            if not blocking_reason_codes
            else R8R_CONTINUATION_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8R_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8R_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8R_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "continuation_first_task": R8R_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8R_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8R_CONTINUATION_TASK_COUNT,
        "remaining_batch_count": len(remaining),
        "remaining_studies": remaining_studies,
        "remaining_objects": remaining_objects,
        "remaining_source_bytes": remaining_source_bytes,
        "largest_remaining_batch_objects": largest_objects,
        "largest_remaining_batch_source_bytes": largest_source_bytes,
        "remaining_raw_source_demand_bytes": remaining_source_bytes,
        "largest_remaining_transfer_retry_demand_bytes": (
            largest_source_bytes
        ),
        "largest_rolling_extracted_cache_demand_bytes": (
            rolling_extracted_bytes
        ),
        "remaining_clip_embedding_upper_bound_bytes": (
            clip_embedding_bytes
        ),
        "remaining_study_embedding_upper_bound_bytes": (
            study_embedding_bytes
        ),
        "retained_extracted_audit_demand_bytes": (
            R8R_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            R8R_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": R8R_LOG_BYTES,
        "preservation_and_finalization_demand_bytes": (
            R8R_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8R_SAFETY_BYTES,
        "continuation_increment_bytes": continuation_increment,
        "remaining_raw_object_file_demand": remaining_objects,
        "largest_rolling_object_file_demand": largest_objects,
        "fixed_control_file_demand": R8R_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": required_file_slots,
        "research_quota_bytes": quota,
        "research_usage_bytes": usage,
        "research_quota_remaining_bytes": quota - usage,
        "research_file_quota": file_quota,
        "research_files_used": files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": int(
            value["research_filesystem_total_bytes"]
        ),
        "research_filesystem_used_bytes": int(
            value["research_filesystem_used_bytes"]
        ),
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8R_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8R_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_continuation_bytes": quota_slack,
        "physical_slack_after_continuation_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": value.get(
            "pquota_display_crosscheck"
        ),
        **{key: 0 for key in R8R_CONTINUATION_ZERO_EFFECT_KEYS},
    }
    if (
        value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or dict(value) != expected
    ):
        raise PostReallocationCapacityError(
            "R8R_CONTINUATION_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def _fixed_r8u_recovery_batches(
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Bind the original plan to Batch 16 recovery and Tasks 17--19."""

    if not isinstance(plan, Mapping):
        raise PostReallocationCapacityError("R8U_FIXED_PLAN_SCHEMA_INVALID")
    try:
        plan_payload = json.dumps(
            plan,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise PostReallocationCapacityError(
            "R8U_FIXED_PLAN_SCHEMA_INVALID"
        ) from exc
    if _sha(plan_payload) != R8U_ORIGINAL_PLAN_SHA256:
        raise PostReallocationCapacityError("R8U_FIXED_PLAN_SHA256_MISMATCH")

    authority = plan.get("authority")
    cohort = plan.get("cohort")
    batches = plan.get("batches")
    if (
        plan.get("schema_version") != 3
        or plan.get("artifact_type")
        != "lvef_c3_restricted_immutable_batch_plan_v3"
        or not isinstance(authority, Mapping)
        or authority.get("git_commit") != R8U_ORIGINAL_SCIENTIFIC_COMMIT
        or not isinstance(cohort, Mapping)
        or not isinstance(batches, list)
        or len(batches) != R8U_CONTINUATION_LAST_TASK
    ):
        raise PostReallocationCapacityError(
            "R8U_FIXED_PLAN_AUTHORITY_INVALID"
        )

    integer_fields = ("n_studies", "n_objects", "source_bytes")
    for ordinal, batch in enumerate(batches):
        if (
            not isinstance(batch, Mapping)
            or batch.get("batch_id") != f"c3_batch_{ordinal:03d}"
            or batch.get("ordinal") != ordinal
            or any(
                not isinstance(batch.get(field), int)
                or isinstance(batch.get(field), bool)
                or int(batch[field]) <= 0
                for field in integer_fields
            )
            or int(batch["n_studies"])
            != (30 if ordinal == 18 else 250)
        ):
            raise PostReallocationCapacityError(
                "R8U_FIXED_PLAN_TOPOLOGY_INVALID"
            )

    full_aggregates = (
        sum(int(batch["n_studies"]) for batch in batches),
        sum(int(batch["n_objects"]) for batch in batches),
        sum(int(batch["source_bytes"]) for batch in batches),
    )
    if (
        full_aggregates
        != (
            R8R_ORIGINAL_STUDIES,
            R8R_ORIGINAL_OBJECTS,
            R8R_ORIGINAL_SOURCE_BYTES,
        )
        or cohort.get("selected_studies") != R8R_ORIGINAL_STUDIES
        or cohort.get("normalized_source_objects") != R8R_ORIGINAL_OBJECTS
        or cohort.get("selected_source_bytes") != R8R_ORIGINAL_SOURCE_BYTES
    ):
        raise PostReallocationCapacityError(
            "R8U_FIXED_PLAN_AGGREGATE_INVALID"
        )

    recovery = batches[R8U_RECOVERY_BATCH_INDEX]
    continuation = batches[
        R8U_CONTINUATION_FIRST_BATCH_INDEX:R8U_EXCLUSIVE_LAST_BATCH_INDEX
    ]
    if (
        recovery.get("batch_id") != R8U_RECOVERY_BATCH_ID
        or int(recovery["n_objects"])
        != R8U_BATCH16_RAW_OBJECT_FILES_BASELINE
        or len(continuation) != R8U_CONTINUATION_TASK_COUNT
    ):
        raise PostReallocationCapacityError(
            "R8U_FIXED_PLAN_RECOVERY_TOPOLOGY_INVALID"
        )
    return recovery, continuation


def _derive_fixed_r8u_capacity_demands(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Purely derive fixed R8U baseline evidence and incremental demand."""

    recovery, continuation = _fixed_r8u_recovery_batches(plan)
    fresh_pipeline = [recovery, *continuation]
    fresh_studies = sum(int(row["n_studies"]) for row in fresh_pipeline)
    fresh_objects = sum(int(row["n_objects"]) for row in fresh_pipeline)
    fresh_source_bytes = sum(
        int(row["source_bytes"]) for row in fresh_pipeline
    )
    continuation_studies = sum(
        int(row["n_studies"]) for row in continuation
    )
    continuation_objects = sum(
        int(row["n_objects"]) for row in continuation
    )
    continuation_source_bytes = sum(
        int(row["source_bytes"]) for row in continuation
    )
    largest_fresh_objects = max(
        int(row["n_objects"]) for row in fresh_pipeline
    )
    largest_continuation_source_bytes = max(
        int(row["source_bytes"]) for row in continuation
    )
    rolling_extracted_bytes = (
        largest_fresh_objects * R8U_EXTRACTED_BYTES_PER_OBJECT
    )
    clip_embedding_bytes = (
        fresh_objects * R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_embedding_bytes = (
        fresh_studies * R8U_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    r8u_increment = sum(
        (
            continuation_source_bytes,
            largest_continuation_source_bytes,
            rolling_extracted_bytes,
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
        continuation_objects
        + largest_fresh_objects
        + R8U_FIXED_CONTROL_FILE_DEMAND
    )
    return {
        "recovery_task": R8U_RECOVERY_TASK,
        "recovery_batch_id": R8U_RECOVERY_BATCH_ID,
        "continuation_first_task": R8U_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8U_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8U_CONTINUATION_TASK_COUNT,
        "fresh_pipeline_batch_count": len(fresh_pipeline),
        "fresh_pipeline_studies": fresh_studies,
        "fresh_pipeline_objects": fresh_objects,
        "fresh_pipeline_source_bytes": fresh_source_bytes,
        "continuation_studies": continuation_studies,
        "continuation_objects": continuation_objects,
        "continuation_source_bytes": continuation_source_bytes,
        "largest_fresh_pipeline_batch_objects": largest_fresh_objects,
        "largest_continuation_batch_source_bytes": (
            largest_continuation_source_bytes
        ),
        "batch16_raw_reused": True,
        "batch16_raw_object_files_baseline": int(recovery["n_objects"]),
        "batch16_raw_source_bytes_baseline": int(recovery["source_bytes"]),
        "failed_partial_files_baseline": R8U_FAILED_PARTIAL_FILES_BASELINE,
        "failed_partial_bytes_baseline": R8U_FAILED_PARTIAL_BYTES_BASELINE,
        "batch16_redownload_demand_bytes": 0,
        "baseline_batch16_raw_bytes_added_to_increment": 0,
        "baseline_failed_partial_bytes_added_to_increment": 0,
        "baseline_batch16_raw_files_added_to_demand": 0,
        "baseline_failed_partial_files_added_to_demand": 0,
        "continuation_raw_source_demand_bytes": continuation_source_bytes,
        "largest_continuation_transfer_retry_demand_bytes": (
            largest_continuation_source_bytes
        ),
        "largest_rolling_fresh_extracted_cache_demand_bytes": (
            rolling_extracted_bytes
        ),
        "fresh_clip_embedding_upper_bound_bytes": clip_embedding_bytes,
        "fresh_study_embedding_upper_bound_bytes": study_embedding_bytes,
        "retained_extracted_audit_demand_bytes": (
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            R8U_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": R8U_LOG_BYTES,
        "preservation_and_finalization_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "r8u_increment_bytes": r8u_increment,
        "continuation_raw_object_file_demand": continuation_objects,
        "largest_rolling_fresh_object_file_demand": largest_fresh_objects,
        "fixed_control_file_demand": R8U_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": required_file_slots,
    }


def _fixed_r8u_implementation_authority_epochs(
    r8u_scheduler_log_repair_commit: str,
) -> dict[str, str]:
    """Return the closed five-epoch authority for one validated R8U-R2 HEAD.

    The recovery controller is responsible for proving that the supplied
    scheduler-log repair commit is the one direct child of the immutable R8U
    projection repair.  This capacity module accepts that already-validated
    identity only through a required keyword, binds it beside the four fixed
    historical epochs, and rejects malformed or historically reused
    identities before live capture.
    """

    if (
        type(r8u_scheduler_log_repair_commit) is not str
        or COMMIT_RE.fullmatch(r8u_scheduler_log_repair_commit) is None
        or r8u_scheduler_log_repair_commit
        in {
            R8U_ORIGINAL_SCIENTIFIC_COMMIT,
            R8U_R8R_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        }
    ):
        raise PostReallocationCapacityError(
            "R8U_IMPLEMENTATION_AUTHORITY_INVALID"
        )
    result = {
        "scientific_commit": R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": R8U_R8R_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            r8u_scheduler_log_repair_commit
        ),
    }
    if set(result) != R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        raise PostReallocationCapacityError(
            "R8U_IMPLEMENTATION_AUTHORITY_INVALID"
        )
    return result


def probe_fixed_r8u_batch16_recovery_capacity(
    plan: Mapping[str, Any],
    *,
    r8u_scheduler_log_repair_commit: str,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Adjudicate fixed Batch 16 recovery plus Tasks 17--19 once.

    Retained Batch-16 raw files and failed partial NPZs are baseline evidence:
    the native usage and file counts already include them, so this arithmetic
    cannot charge them again.  Batch 16 has no redownload envelope.  Fresh
    demand consists of Tasks 17--19 raw/retry space, one rolling largest
    extraction cache across Tasks 16--19, all fresh embeddings, and fixed
    control/finalization burdens.  Quota and physical margins each retain
    exactly 200,000,000,000 bytes, with the established 100,000 control-file
    allowance.  The live native/pquota/findmnt/df snapshot is captured once.
    """

    demands = _derive_fixed_r8u_capacity_demands(plan)
    implementation_authority_epochs = (
        _fixed_r8u_implementation_authority_epochs(
            r8u_scheduler_log_repair_commit
        )
    )
    snapshot = _capture_current_capacity_snapshot(
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=process_runner,
    )
    native = snapshot["native"]
    dfs = snapshot["dfs"]
    increment = int(demands["r8u_increment_bytes"])
    required_file_slots = int(demands["required_file_slots"])

    research_quota = int(native["research"]["quota_kib"]) * 1024
    research_usage = int(native["research"]["usage_kib"]) * 1024
    research_file_quota = int(native["research"]["file_quota"])
    research_files_used = int(native["research"]["files_used"])
    physical_available = int(dfs["research"]["available"])
    projected_usage = research_usage + increment
    quota_slack = research_quota - projected_usage
    physical_slack = physical_available - increment
    quota_margin = quota_slack - R8U_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8U_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = research_file_quota - research_files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for field, code in (
            (
                "quota_reserve_gate_passed",
                "R8U_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8U_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8U_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[field] is not True
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": R8U_CAPACITY_ARTIFACT_TYPE,
        "status": (
            R8U_CAPACITY_STATUS_PASS
            if not blocking_reason_codes
            else R8U_CAPACITY_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "implementation_authority_epochs": implementation_authority_epochs,
        **demands,
        "research_quota_bytes": research_quota,
        "research_usage_bytes": research_usage,
        "research_quota_remaining_bytes": research_quota - research_usage,
        "research_file_quota": research_file_quota,
        "research_files_used": research_files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": int(dfs["research"]["total"]),
        "research_filesystem_used_bytes": int(dfs["research"]["used"]),
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8U_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8U_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_r8u_bytes": quota_slack,
        "physical_slack_after_r8u_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": int(
            snapshot["native_capacity_snapshot_captures"]
        ),
        "native_quota_file_captures": int(
            snapshot["native_quota_file_captures"]
        ),
        "capacity_command_captures": int(
            snapshot["capacity_command_captures"]
        ),
        "pquota_command_captures": int(
            snapshot["pquota_command_captures"]
        ),
        "findmnt_command_captures": int(
            snapshot["findmnt_command_captures"]
        ),
        "df_command_captures": int(snapshot["df_command_captures"]),
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": str(
            snapshot["pquota_display_crosscheck"]
        ),
        **{key: 0 for key in R8U_CAPACITY_ZERO_EFFECT_KEYS},
    }
    if set(result) != R8U_CAPACITY_KEYS:
        raise PostReallocationCapacityError("R8U_CAPACITY_SCHEMA_INVALID")
    return validate_fixed_r8u_batch16_recovery_capacity(
        plan,
        result,
        r8u_scheduler_log_repair_commit=(
            r8u_scheduler_log_repair_commit
        ),
    )


def validate_fixed_r8u_batch16_recovery_capacity(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    r8u_scheduler_log_repair_commit: str,
) -> dict[str, Any]:
    """Purely replay every fixed R8U demand, margin, gate, and status."""

    demands = _derive_fixed_r8u_capacity_demands(plan)
    implementation_authority_epochs = (
        _fixed_r8u_implementation_authority_epochs(
            r8u_scheduler_log_repair_commit
        )
    )
    if not isinstance(value, Mapping) or set(value) != R8U_CAPACITY_KEYS:
        raise PostReallocationCapacityError("R8U_CAPACITY_SCHEMA_INVALID")

    text_fields = {
        "artifact_type",
        "status",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "recovery_batch_id",
        "pquota_display_crosscheck",
    }
    boolean_fields = {
        "batch16_raw_reused",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "native_quota_authority_read_only",
    }
    special_fields = text_fields | boolean_fields | {
        "blocking_reason_codes",
        "implementation_authority_epochs",
    }
    integer_fields = R8U_CAPACITY_KEYS - special_fields
    if (
        any(type(value.get(key)) is not int for key in integer_fields)
        or any(type(value.get(key)) is not bool for key in boolean_fields)
        or any(type(value.get(key)) is not str for key in text_fields)
        or not isinstance(value.get("blocking_reason_codes"), list)
        or not isinstance(
            value.get("implementation_authority_epochs"), Mapping
        )
        or set(value["implementation_authority_epochs"])
        != R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(
            type(value["implementation_authority_epochs"].get(key)) is not str
            for key in R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        )
    ):
        raise PostReallocationCapacityError("R8U_CAPACITY_SCHEMA_INVALID")
    if dict(value["implementation_authority_epochs"]) != (
        implementation_authority_epochs
    ):
        raise PostReallocationCapacityError(
            "R8U_IMPLEMENTATION_AUTHORITY_INVALID"
        )

    quota = int(value["research_quota_bytes"])
    usage = int(value["research_usage_bytes"])
    file_quota = int(value["research_file_quota"])
    files_used = int(value["research_files_used"])
    filesystem_total = int(value["research_filesystem_total_bytes"])
    filesystem_used = int(value["research_filesystem_used_bytes"])
    physical_available = int(value["research_filesystem_available_bytes"])
    if (
        min(
            quota,
            usage,
            file_quota,
            files_used,
            filesystem_total,
            filesystem_used,
            physical_available,
        )
        < 0
        or quota == 0
        or file_quota == 0
        or filesystem_total == 0
        or quota % 1024 != 0
        or usage % 1024 != 0
        or quota < EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or file_quota < EXPECTED_RESEARCH_FILE_QUOTA
        or usage > quota
        or files_used > file_quota
        or filesystem_used + physical_available > filesystem_total
    ):
        raise PostReallocationCapacityError("R8U_CAPACITY_SCHEMA_INVALID")

    increment = int(demands["r8u_increment_bytes"])
    required_file_slots = int(demands["required_file_slots"])
    projected_usage = usage + increment
    quota_slack = quota - projected_usage
    physical_slack = physical_available - increment
    quota_margin = quota_slack - R8U_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8U_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = file_quota - files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for key, code in (
            (
                "quota_reserve_gate_passed",
                "R8U_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8U_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            ("file_slot_gate_passed", "R8U_FILE_SLOTS_INSUFFICIENT"),
        )
        if gates[key] is not True
    ]
    expected = {
        "schema_version": 1,
        "artifact_type": R8U_CAPACITY_ARTIFACT_TYPE,
        "status": (
            R8U_CAPACITY_STATUS_PASS
            if not blocking_reason_codes
            else R8U_CAPACITY_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "implementation_authority_epochs": implementation_authority_epochs,
        **demands,
        "research_quota_bytes": quota,
        "research_usage_bytes": usage,
        "research_quota_remaining_bytes": quota - usage,
        "research_file_quota": file_quota,
        "research_files_used": files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": filesystem_total,
        "research_filesystem_used_bytes": filesystem_used,
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8U_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8U_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_r8u_bytes": quota_slack,
        "physical_slack_after_r8u_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": value.get(
            "pquota_display_crosscheck"
        ),
        **{key: 0 for key in R8U_CAPACITY_ZERO_EFFECT_KEYS},
    }
    if (
        value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or dict(value) != expected
    ):
        raise PostReallocationCapacityError(
            "R8U_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def _fixed_r8u_r3_implementation_authority_epochs(
    r8u_candidate_authority_repair_commit: str,
) -> dict[str, str]:
    """Return the closed seven-epoch authority for one validated R8U-R3 HEAD."""

    fixed_epochs = {
        R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        R8U_R8R_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
    }
    if (
        type(r8u_candidate_authority_repair_commit) is not str
        or COMMIT_RE.fullmatch(r8u_candidate_authority_repair_commit) is None
        or r8u_candidate_authority_repair_commit in fixed_epochs
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID"
        )
    result = {
        "scientific_commit": R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": R8U_R8R_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_publication_resume_repair_commit": (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_candidate_authority_repair_commit": (
            r8u_candidate_authority_repair_commit
        ),
    }
    if set(result) != R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID"
        )
    return result


def _fixed_r8u_r3_candidate_authority(
    *,
    completed_extraction_candidate_seal_sha256: str,
    completed_extraction_candidate_bytes: int,
) -> dict[str, Any]:
    """Validate the aggregate-only authority for the already-present cache."""

    if (
        type(completed_extraction_candidate_seal_sha256) is not str
        or SHA256_RE.fullmatch(
            completed_extraction_candidate_seal_sha256
        )
        is None
        or type(completed_extraction_candidate_bytes) is not int
        or completed_extraction_candidate_bytes <= 0
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_AUTHORITY_INVALID"
        )
    return {
        "completed_extraction_candidate_files_baseline": (
            R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_FILES
        ),
        "completed_extraction_candidate_bytes_baseline": (
            completed_extraction_candidate_bytes
        ),
        "completed_extraction_candidate_seal_sha256": (
            completed_extraction_candidate_seal_sha256
        ),
    }


def _derive_fixed_r8u_r3_capacity_demands(
    plan: Mapping[str, Any],
    *,
    completed_extraction_candidate_seal_sha256: str,
    completed_extraction_candidate_bytes: int,
) -> dict[str, Any]:
    """Derive remaining demand without charging completed Batch-16 inputs."""

    recovery, continuation = _fixed_r8u_recovery_batches(plan)
    candidate = _fixed_r8u_r3_candidate_authority(
        completed_extraction_candidate_seal_sha256=(
            completed_extraction_candidate_seal_sha256
        ),
        completed_extraction_candidate_bytes=(
            completed_extraction_candidate_bytes
        ),
    )
    continuation_studies = sum(
        int(row["n_studies"]) for row in continuation
    )
    continuation_objects = sum(
        int(row["n_objects"]) for row in continuation
    )
    continuation_source_bytes = sum(
        int(row["source_bytes"]) for row in continuation
    )
    largest_continuation_objects = max(
        int(row["n_objects"]) for row in continuation
    )
    largest_continuation_source_bytes = max(
        int(row["source_bytes"]) for row in continuation
    )
    remaining_studies = int(recovery["n_studies"]) + continuation_studies
    remaining_source_objects = (
        int(recovery["n_objects"]) + continuation_objects
    )
    remaining_source_bytes = (
        int(recovery["source_bytes"]) + continuation_source_bytes
    )
    remaining_clip_files = (
        R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_FILES
        + continuation_objects
    )
    rolling_continuation_extracted_bytes = (
        largest_continuation_objects * R8U_EXTRACTED_BYTES_PER_OBJECT
    )
    clip_embedding_bytes = (
        remaining_clip_files * R8U_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_embedding_bytes = (
        remaining_studies * R8U_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    increment = sum(
        (
            continuation_source_bytes,
            largest_continuation_source_bytes,
            rolling_continuation_extracted_bytes,
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
        continuation_objects
        + largest_continuation_objects
        + R8U_FIXED_CONTROL_FILE_DEMAND
    )
    return {
        "resume_task": R8U_RECOVERY_TASK,
        "resume_batch_id": R8U_RECOVERY_BATCH_ID,
        "continuation_first_task": R8U_CONTINUATION_FIRST_TASK,
        "continuation_last_task": R8U_CONTINUATION_LAST_TASK,
        "continuation_task_count": R8U_CONTINUATION_TASK_COUNT,
        "remaining_scope_batch_count": 1 + len(continuation),
        "remaining_scope_studies": remaining_studies,
        "remaining_scope_source_objects": remaining_source_objects,
        "remaining_scope_source_bytes": remaining_source_bytes,
        "continuation_studies": continuation_studies,
        "continuation_objects": continuation_objects,
        "continuation_source_bytes": continuation_source_bytes,
        "largest_continuation_batch_objects": (
            largest_continuation_objects
        ),
        "largest_continuation_batch_source_bytes": (
            largest_continuation_source_bytes
        ),
        "batch16_raw_reused": True,
        "batch16_raw_object_files_baseline": int(recovery["n_objects"]),
        "batch16_raw_source_bytes_baseline": int(recovery["source_bytes"]),
        "failed_partial_files_baseline": R8U_FAILED_PARTIAL_FILES_BASELINE,
        "failed_partial_bytes_baseline": R8U_FAILED_PARTIAL_BYTES_BASELINE,
        "completed_extraction_candidate_reused": True,
        **candidate,
        "batch16_redownload_demand_bytes": 0,
        "batch16_dicom_extraction_demand_bytes": 0,
        "batch16_dicom_extraction_file_demand": 0,
        "baseline_batch16_raw_bytes_added_to_increment": 0,
        "baseline_batch16_raw_files_added_to_demand": 0,
        "baseline_failed_partial_bytes_added_to_increment": 0,
        "baseline_failed_partial_files_added_to_demand": 0,
        "baseline_completed_candidate_bytes_added_to_increment": 0,
        "baseline_completed_candidate_files_added_to_demand": 0,
        "continuation_raw_source_demand_bytes": continuation_source_bytes,
        "largest_continuation_transfer_retry_demand_bytes": (
            largest_continuation_source_bytes
        ),
        "largest_rolling_continuation_extracted_cache_demand_bytes": (
            rolling_continuation_extracted_bytes
        ),
        "batch16_clip_embedding_file_upper_bound": (
            R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_FILES
        ),
        "continuation_clip_embedding_file_upper_bound": (
            continuation_objects
        ),
        "remaining_clip_embedding_file_upper_bound": remaining_clip_files,
        "remaining_clip_embedding_upper_bound_bytes": (
            clip_embedding_bytes
        ),
        "remaining_study_embedding_upper_bound_bytes": (
            study_embedding_bytes
        ),
        "retained_extracted_audit_demand_bytes": (
            R8U_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            R8U_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": R8U_LOG_BYTES,
        "preservation_and_finalization_demand_bytes": (
            R8U_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": R8U_SAFETY_BYTES,
        "r8u_r3_increment_bytes": increment,
        "continuation_raw_object_file_demand": continuation_objects,
        "largest_rolling_continuation_object_file_demand": (
            largest_continuation_objects
        ),
        "fixed_control_file_demand": R8U_FIXED_CONTROL_FILE_DEMAND,
        "required_file_slots": required_file_slots,
    }


def probe_fixed_r8u_r3_batch16_publication_resume_capacity(
    plan: Mapping[str, Any],
    *,
    completed_extraction_candidate_seal_sha256: str,
    completed_extraction_candidate_bytes: int,
    r8u_candidate_authority_repair_commit: str,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Capture one admission observation for R3 resume plus Tasks 17--19."""

    demands = _derive_fixed_r8u_r3_capacity_demands(
        plan,
        completed_extraction_candidate_seal_sha256=(
            completed_extraction_candidate_seal_sha256
        ),
        completed_extraction_candidate_bytes=(
            completed_extraction_candidate_bytes
        ),
    )
    implementation_authority_epochs = (
        _fixed_r8u_r3_implementation_authority_epochs(
            r8u_candidate_authority_repair_commit
        )
    )
    snapshot = _capture_current_capacity_snapshot(
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=process_runner,
    )
    native = snapshot["native"]
    dfs = snapshot["dfs"]
    increment = int(demands["r8u_r3_increment_bytes"])
    required_file_slots = int(demands["required_file_slots"])

    research_quota = int(native["research"]["quota_kib"]) * 1024
    research_usage = int(native["research"]["usage_kib"]) * 1024
    research_file_quota = int(native["research"]["file_quota"])
    research_files_used = int(native["research"]["files_used"])
    physical_available = int(dfs["research"]["available"])
    projected_usage = research_usage + increment
    quota_slack = research_quota - projected_usage
    physical_slack = physical_available - increment
    quota_margin = quota_slack - R8U_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8U_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = research_file_quota - research_files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for field, code in (
            (
                "quota_reserve_gate_passed",
                "R8U_R3_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8U_R3_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8U_R3_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[field] is not True
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": R8U_R3_CAPACITY_ARTIFACT_TYPE,
        "status": (
            R8U_R3_CAPACITY_STATUS_PASS
            if not blocking_reason_codes
            else R8U_R3_CAPACITY_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "implementation_authority_epochs": implementation_authority_epochs,
        **demands,
        "research_quota_bytes": research_quota,
        "research_usage_bytes": research_usage,
        "research_quota_remaining_bytes": research_quota - research_usage,
        "research_file_quota": research_file_quota,
        "research_files_used": research_files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": int(dfs["research"]["total"]),
        "research_filesystem_used_bytes": int(dfs["research"]["used"]),
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8U_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8U_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_r8u_r3_bytes": quota_slack,
        "physical_slack_after_r8u_r3_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": int(
            snapshot["native_capacity_snapshot_captures"]
        ),
        "native_quota_file_captures": int(
            snapshot["native_quota_file_captures"]
        ),
        "capacity_command_captures": int(
            snapshot["capacity_command_captures"]
        ),
        "pquota_command_captures": int(
            snapshot["pquota_command_captures"]
        ),
        "findmnt_command_captures": int(
            snapshot["findmnt_command_captures"]
        ),
        "df_command_captures": int(snapshot["df_command_captures"]),
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": str(
            snapshot["pquota_display_crosscheck"]
        ),
        **{key: 0 for key in R8U_R3_CAPACITY_ZERO_EFFECT_KEYS},
    }
    if set(result) != R8U_R3_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R3_CAPACITY_SCHEMA_INVALID"
        )
    return validate_fixed_r8u_r3_batch16_publication_resume_capacity(
        plan,
        result,
        completed_extraction_candidate_seal_sha256=(
            completed_extraction_candidate_seal_sha256
        ),
        completed_extraction_candidate_bytes=(
            completed_extraction_candidate_bytes
        ),
        r8u_candidate_authority_repair_commit=(
            r8u_candidate_authority_repair_commit
        ),
    )


def validate_fixed_r8u_r3_batch16_publication_resume_capacity(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    completed_extraction_candidate_seal_sha256: str,
    completed_extraction_candidate_bytes: int,
    r8u_candidate_authority_repair_commit: str,
) -> dict[str, Any]:
    """Purely replay every fixed R8U-R3 demand, margin, gate, and status."""

    demands = _derive_fixed_r8u_r3_capacity_demands(
        plan,
        completed_extraction_candidate_seal_sha256=(
            completed_extraction_candidate_seal_sha256
        ),
        completed_extraction_candidate_bytes=(
            completed_extraction_candidate_bytes
        ),
    )
    implementation_authority_epochs = (
        _fixed_r8u_r3_implementation_authority_epochs(
            r8u_candidate_authority_repair_commit
        )
    )
    if not isinstance(value, Mapping) or set(value) != R8U_R3_CAPACITY_KEYS:
        raise PostReallocationCapacityError(
            "R8U_R3_CAPACITY_SCHEMA_INVALID"
        )

    text_fields = {
        "artifact_type",
        "status",
        "original_attempt_id",
        "original_plan_sha256",
        "original_scientific_governing_commit",
        "resume_batch_id",
        "completed_extraction_candidate_seal_sha256",
        "pquota_display_crosscheck",
    }
    boolean_fields = {
        "batch16_raw_reused",
        "completed_extraction_candidate_reused",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "native_quota_authority_read_only",
    }
    special_fields = text_fields | boolean_fields | {
        "blocking_reason_codes",
        "implementation_authority_epochs",
    }
    integer_fields = R8U_R3_CAPACITY_KEYS - special_fields
    if (
        any(type(value.get(key)) is not int for key in integer_fields)
        or any(type(value.get(key)) is not bool for key in boolean_fields)
        or any(type(value.get(key)) is not str for key in text_fields)
        or not isinstance(value.get("blocking_reason_codes"), list)
        or not isinstance(
            value.get("implementation_authority_epochs"), Mapping
        )
        or set(value["implementation_authority_epochs"])
        != R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(
            type(value["implementation_authority_epochs"].get(key)) is not str
            for key in R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        )
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_CAPACITY_SCHEMA_INVALID"
        )
    if dict(value["implementation_authority_epochs"]) != (
        implementation_authority_epochs
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID"
        )

    quota = int(value["research_quota_bytes"])
    usage = int(value["research_usage_bytes"])
    file_quota = int(value["research_file_quota"])
    files_used = int(value["research_files_used"])
    filesystem_total = int(value["research_filesystem_total_bytes"])
    filesystem_used = int(value["research_filesystem_used_bytes"])
    physical_available = int(value["research_filesystem_available_bytes"])
    if (
        min(
            quota,
            usage,
            file_quota,
            files_used,
            filesystem_total,
            filesystem_used,
            physical_available,
        )
        < 0
        or quota == 0
        or file_quota == 0
        or filesystem_total == 0
        or quota % 1024 != 0
        or usage % 1024 != 0
        or quota < EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or file_quota < EXPECTED_RESEARCH_FILE_QUOTA
        or usage > quota
        or files_used > file_quota
        or filesystem_used + physical_available > filesystem_total
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_CAPACITY_SCHEMA_INVALID"
        )

    increment = int(demands["r8u_r3_increment_bytes"])
    required_file_slots = int(demands["required_file_slots"])
    projected_usage = usage + increment
    quota_slack = quota - projected_usage
    physical_slack = physical_available - increment
    quota_margin = quota_slack - R8U_QUOTA_RESERVE_BYTES
    physical_margin = physical_slack - R8U_PHYSICAL_RESERVE_BYTES
    file_slots_remaining = file_quota - files_used
    file_margin = file_slots_remaining - required_file_slots
    gates = {
        "quota_reserve_gate_passed": quota_margin >= 0,
        "physical_reserve_gate_passed": physical_margin >= 0,
        "file_slot_gate_passed": file_margin >= 0,
    }
    blocking_reason_codes = [
        code
        for field, code in (
            (
                "quota_reserve_gate_passed",
                "R8U_R3_QUOTA_RESERVE_INSUFFICIENT",
            ),
            (
                "physical_reserve_gate_passed",
                "R8U_R3_PHYSICAL_RESERVE_INSUFFICIENT",
            ),
            (
                "file_slot_gate_passed",
                "R8U_R3_FILE_SLOTS_INSUFFICIENT",
            ),
        )
        if gates[field] is not True
    ]
    expected = {
        "schema_version": 1,
        "artifact_type": R8U_R3_CAPACITY_ARTIFACT_TYPE,
        "status": (
            R8U_R3_CAPACITY_STATUS_PASS
            if not blocking_reason_codes
            else R8U_R3_CAPACITY_STATUS_BLOCKED
        ),
        "blocking_reason_codes": blocking_reason_codes,
        "original_attempt_id": R8U_ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": R8U_ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8U_ORIGINAL_SCIENTIFIC_COMMIT
        ),
        "implementation_authority_epochs": implementation_authority_epochs,
        **demands,
        "research_quota_bytes": quota,
        "research_usage_bytes": usage,
        "research_quota_remaining_bytes": quota - usage,
        "research_file_quota": file_quota,
        "research_files_used": files_used,
        "research_file_slots_remaining": file_slots_remaining,
        "research_filesystem_total_bytes": filesystem_total,
        "research_filesystem_used_bytes": filesystem_used,
        "research_filesystem_available_bytes": physical_available,
        "projected_research_usage_bytes": projected_usage,
        "required_quota_reserve_bytes": R8U_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": R8U_PHYSICAL_RESERVE_BYTES,
        "quota_slack_after_r8u_r3_bytes": quota_slack,
        "physical_slack_after_r8u_r3_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "file_slot_margin_after_demand": file_margin,
        **gates,
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "native_quota_authority_read_only": True,
        "pquota_display_crosscheck": value.get(
            "pquota_display_crosscheck"
        ),
        **{key: 0 for key in R8U_R3_CAPACITY_ZERO_EFFECT_KEYS},
    }
    if (
        value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or dict(value) != expected
    ):
        raise PostReallocationCapacityError(
            "R8U_R3_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def _dynamic_capture_time(
    value: datetime | None,
) -> tuple[datetime, str]:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    normalized = observed.astimezone(timezone.utc).replace(microsecond=0)
    return normalized, normalized.isoformat().replace("+00:00", "Z")


def _validate_dynamic_capture_time(
    value: object,
    *,
    now_utc: datetime | None,
) -> None:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        value,
    ) is None:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    try:
        captured = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    now, _ = _dynamic_capture_time(now_utc)
    age = (now - captured).total_seconds()
    if (
        age > DYNAMIC_SUCCESSOR_MAXIMUM_AGE_SECONDS
        or age < -DYNAMIC_SUCCESSOR_MAXIMUM_FUTURE_SKEW_SECONDS
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_STALE"
        )


def _dynamic_core_sha256(value: Mapping[str, Any]) -> str:
    if set(value) != DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return _sha(_canonical(value))


def _validate_dynamic_successor_core(
    value: Mapping[str, Any],
    *,
    expected_governing_commit: str | None,
    now_utc: datetime | None,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    governing_commit = value.get("governing_commit")
    if (
        type(governing_commit) is not str
        or COMMIT_RE.fullmatch(governing_commit) is None
        or (
            expected_governing_commit is not None
            and governing_commit != expected_governing_commit
        )
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    _validate_dynamic_capture_time(
        value.get("captured_at_utc"), now_utc=now_utc
    )
    signed_integer_fields = {
        "quota_slack_after_peak_bytes",
        "physical_slack_after_peak_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
    }
    nonnegative_integer_fields = {
        "live_research_quota_bytes",
        "live_research_usage_bytes",
        "live_research_file_quota",
        "live_research_files_used",
        "live_research_filesystem_available_bytes",
        "backed_quota_bytes",
        "backed_usage_bytes",
        "backed_file_quota",
        "backed_files_used",
        "historical_pre_allocation_research_quota_bytes",
        "historical_pre_allocation_research_file_quota",
        "fresh_successor_increment_bytes",
        "required_quota_reserve_bytes",
        "required_physical_reserve_bytes",
        "required_remaining_file_slots",
        "projected_fresh_successor_peak_bytes",
        "remaining_file_slots",
        "active_extraction_caches",
        "preserved_terminal_failed_extraction_caches",
        "minimum_additional_quota_bytes",
        "minimum_additional_physical_bytes",
        "minimum_additional_file_slots",
        *DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS,
    }
    boolean_fields = {
        "backed_tier_gate_passed",
        "successor_attempt_root_absent",
        "successor_claim_absent",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "cache_inventory_gate_passed",
        "successor_collision_gate_passed",
        "storage_allocation_visible",
        "underlying_filesystem_expansion_appears_necessary",
        "native_quota_authority_read_only",
    }
    if (
        any(
            type(value.get(field)) is not int or int(value[field]) < 0
            for field in nonnegative_integer_fields
        )
        or any(type(value.get(field)) is not int for field in signed_integer_fields)
        or any(type(value.get(field)) is not bool for field in boolean_fields)
        or any(value.get(field) != 0 for field in DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS)
        or value.get("schema_version") != 1
        or value.get("artifact_type") != DYNAMIC_SUCCESSOR_OBSERVATION_TYPE
        or value.get("status") not in DYNAMIC_SUCCESSOR_STATUSES
        or value.get("mount_authority_status") != DYNAMIC_SUCCESSOR_MOUNT_STATUS
        or value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or value.get("pquota_executable_authority_status")
        not in {"PASS_TRUSTED_ROOT_CONTROLLED", "UNAVAILABLE_NONBLOCKING"}
        or value.get("native_quota_authority_read_only") is not True
        or any(
            type(value.get(field)) is not str
            or SHA256_RE.fullmatch(value[field]) is None
            for field in (
                "command_authority_sha256",
                "native_quota_authority_sha256",
                "mount_authority_sha256",
            )
        )
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    reasons = value.get("blocking_reason_codes")
    if (
        not isinstance(reasons, list)
        or len(reasons) != len(set(reasons))
        or any(
            type(reason) is not str
            or re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", reason) is None
            for reason in reasons
        )
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )

    research_quota = int(value["live_research_quota_bytes"])
    research_usage = int(value["live_research_usage_bytes"])
    research_file_quota = int(value["live_research_file_quota"])
    research_files_used = int(value["live_research_files_used"])
    backed_quota = int(value["backed_quota_bytes"])
    backed_usage = int(value["backed_usage_bytes"])
    backed_file_quota = int(value["backed_file_quota"])
    backed_files_used = int(value["backed_files_used"])
    historical_quota = EXPECTED_RESEARCH_QUOTA_KIB * 1024
    if (
        research_quota < historical_quota
        or research_file_quota < EXPECTED_RESEARCH_FILE_QUOTA
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_ALLOCATION_REGRESSION"
        )
    if (
        backed_quota != EXPECTED_BACKED_QUOTA_KIB * 1024
        or backed_file_quota != EXPECTED_BACKED_FILE_QUOTA
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_BACKED_TIER_CHANGED"
        )
    if (
        research_quota <= 0
        or research_file_quota <= 0
        or research_usage > research_quota
        or research_files_used > research_file_quota
        or backed_usage > backed_quota
        or backed_files_used > backed_file_quota
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_ARITHMETIC_INVALID"
        )
    projected = research_usage + DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    quota_slack = research_quota - projected
    physical_slack = (
        int(value["live_research_filesystem_available_bytes"])
        - DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    )
    quota_margin = quota_slack - DYNAMIC_SUCCESSOR_QUOTA_RESERVE_BYTES
    physical_margin = (
        physical_slack - DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
    )
    remaining_slots = research_file_quota - research_files_used
    slot_margin = remaining_slots - DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS
    quota_gate = quota_margin >= 0
    physical_gate = physical_margin >= 0
    file_gate = slot_margin >= 0
    cache_gate = (
        value["active_extraction_caches"] == 0
        and value["preserved_terminal_failed_extraction_caches"]
        == DYNAMIC_SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    )
    collision_gate = (
        value["successor_attempt_root_absent"] is True
        and value["successor_claim_absent"] is True
    )
    visible = research_quota > historical_quota
    derived: dict[str, Any] = {
        "historical_pre_allocation_research_quota_bytes": historical_quota,
        "historical_pre_allocation_research_file_quota": (
            EXPECTED_RESEARCH_FILE_QUOTA
        ),
        "fresh_successor_increment_bytes": DYNAMIC_SUCCESSOR_INCREMENT_BYTES,
        "required_quota_reserve_bytes": (
            DYNAMIC_SUCCESSOR_QUOTA_RESERVE_BYTES
        ),
        "required_physical_reserve_bytes": (
            DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
        ),
        "required_remaining_file_slots": (
            DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS
        ),
        "projected_fresh_successor_peak_bytes": projected,
        "quota_slack_after_peak_bytes": quota_slack,
        "physical_slack_after_peak_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "remaining_file_slots": remaining_slots,
        "file_slot_margin_after_demand": slot_margin,
        "quota_reserve_gate_passed": quota_gate,
        "physical_reserve_gate_passed": physical_gate,
        "file_slot_gate_passed": file_gate,
        "cache_inventory_gate_passed": cache_gate,
        "successor_collision_gate_passed": collision_gate,
        "minimum_additional_quota_bytes": max(0, -quota_margin),
        "minimum_additional_physical_bytes": max(0, -physical_margin),
        "minimum_additional_file_slots": max(0, -slot_margin),
        "storage_allocation_visible": visible,
        "underlying_filesystem_expansion_appears_necessary": (
            physical_margin < 0
        ),
        "backed_tier_gate_passed": True,
    }
    if any(value.get(field) != expected for field, expected in derived.items()):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_ARITHMETIC_INVALID"
        )
    blocked_reasons: list[str] = []
    if not (quota_gate and physical_gate and file_gate):
        blocked_reasons.append("DYNAMIC_CAPACITY_INSUFFICIENT")
    if not cache_gate:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID"
        )
    if not collision_gate:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
        )
    if not blocked_reasons:
        expected_status = DYNAMIC_SUCCESSOR_STATUS_PASS
        expected_reasons = []
    elif not visible:
        expected_status = DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
        expected_reasons = [DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING]
    else:
        expected_status = DYNAMIC_SUCCESSOR_STATUS_BLOCKED
        expected_reasons = sorted(blocked_reasons)
    if value.get("status") != expected_status or reasons != expected_reasons:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_ARITHMETIC_INVALID"
        )
    return dict(value)


def _validate_dynamic_native_authority(
    value: object,
) -> Mapping[str, Mapping[str, Any]]:
    if (
        not isinstance(value, Mapping)
        or set(value) != DYNAMIC_SUCCESSOR_NATIVE_AUTHORITY_KEYS
        or type(value.get("path_sha256")) is not str
        or SHA256_RE.fullmatch(value["path_sha256"]) is None
        or type(value.get("file_size_bytes")) is not int
        or value["file_size_bytes"] < 1
        or type(value.get("file_sha256")) is not str
        or SHA256_RE.fullmatch(value["file_sha256"]) is None
        or value.get("record_unit") != "KIB"
        or value.get("bytes_per_kib") != 1024
        or not isinstance(value.get("rows"), Mapping)
        or set(value["rows"]) != {"backed", "research"}
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    rows = value["rows"]
    for role in ("backed", "research"):
        row = rows[role]
        if (
            not isinstance(row, Mapping)
            or set(row) != DYNAMIC_SUCCESSOR_NATIVE_ROW_KEYS
            or any(
                type(row.get(field)) is not str
                or SHA256_RE.fullmatch(row[field]) is None
                for field in (
                    "native_name_sha256",
                    "fileset_name_sha256",
                    "raw_row_sha256",
                )
            )
            or row.get("native_name_sha256")
            != _sha(EXPECTED_NATIVE_ROWS[role].encode())
            or row.get("fileset_name_sha256")
            != _sha(EXPECTED_NATIVE_FILESET_FIELD.encode())
            or any(
                type(row.get(field)) is not int or row[field] < 0
                for field in (
                    "usage_kib", "quota_kib", "files_used", "file_quota"
                )
            )
            or row["usage_kib"] > row["quota_kib"]
            or row["files_used"] > row["file_quota"]
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
    if (
        rows["research"]["quota_kib"] < EXPECTED_RESEARCH_QUOTA_KIB
        or rows["research"]["file_quota"] < EXPECTED_RESEARCH_FILE_QUOTA
        or rows["backed"]["quota_kib"] != EXPECTED_BACKED_QUOTA_KIB
        or rows["backed"]["file_quota"] != EXPECTED_BACKED_FILE_QUOTA
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return rows


def _validate_dynamic_paths(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != DYNAMIC_SUCCESSOR_PATH_KEYS:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    identities = value["identities"]
    mounts = value["mounts"]
    dfs = value["df"]
    if any(
        not isinstance(item, Mapping) or set(item) != {"backed", "research"}
        for item in (identities, mounts, dfs)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    for role in ("backed", "research"):
        identity = identities[role]
        mount = mounts[role]
        df_value = dfs[role]
        if (
            not isinstance(identity, Mapping)
            or set(identity)
            != {
                "path_sha256", "resolved_path_sha256", "device", "inode",
                "is_symlink",
            }
            or identity.get("is_symlink") is not False
            or any(
                type(identity.get(field)) is not str
                or SHA256_RE.fullmatch(identity[field]) is None
                for field in ("path_sha256", "resolved_path_sha256")
            )
            or any(
                type(identity.get(field)) is not int or identity[field] < 0
                for field in ("device", "inode")
            )
            or not isinstance(mount, Mapping)
            or set(mount)
            != {
                "source_sha256", "target_sha256", "fsroot_sha256",
                "identity_sha256", "source", "target", "fsroot", "fstype",
                "bind",
            }
            or mount.get("fsroot") != "/"
            or mount.get("bind") is not False
            or any(type(mount.get(field)) is not str or not mount[field]
                   for field in ("source", "target", "fstype"))
            or any(
                type(mount.get(field)) is not str
                or SHA256_RE.fullmatch(mount[field]) is None
                for field in (
                    "source_sha256", "target_sha256", "fsroot_sha256",
                    "identity_sha256",
                )
            )
            or mount["source_sha256"] != _sha(mount["source"].encode())
            or mount["target_sha256"] != _sha(mount["target"].encode())
            or mount["fsroot_sha256"] != _sha(b"/")
            or mount["identity_sha256"]
            != _sha(_canonical({
                "source": mount["source"],
                "target": mount["target"],
                "fstype": mount["fstype"],
                "fsroot": mount["fsroot"],
            }))
            or not isinstance(df_value, Mapping)
            or set(df_value) != {"total", "used", "available"}
            or any(
                type(df_value.get(field)) is not int or df_value[field] < 0
                for field in ("total", "used", "available")
            )
            or df_value["used"] + df_value["available"] > df_value["total"]
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
    if (
        mounts["research"]["source"] == mounts["backed"]["source"]
        or mounts["research"]["target"] == mounts["backed"]["target"]
        or identities["research"]["device"] == identities["backed"]["device"]
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )


def _validate_dynamic_commands(
    value: object,
    *,
    paths: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    executable_authorities: dict[str, tuple[str, int, str]] = {}
    for role, specification in CAPACITY_COMMAND_REGISTRY.items():
        item = value[role]
        base_keys = {
            "role", "argv", "argv_sha256", "executable_sha256",
            "executable_size_bytes", "exit_status", "stdout_bytes",
            "stdout_sha256", "stdout_text", "stderr_bytes", "stderr_sha256",
        }
        expected_keys = base_keys | (
            {"availability_status", "availability_reason"}
            if role == "pquota" else set()
        )
        if not isinstance(item, Mapping) or set(item) != expected_keys:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        argv = item.get("argv")
        if (
            item.get("role") != role
            or not isinstance(argv, list)
            or not argv
            or any(type(part) is not str or not part for part in argv)
            or item.get("argv_sha256")
            != _sha(json.dumps(argv, separators=(",", ":")).encode())
            or not PurePosixPath(argv[0]).is_absolute()
            or PurePosixPath(argv[0]).name != specification.command_kind
            or type(item.get("executable_size_bytes")) is not int
            or item["executable_size_bytes"] < 1
            or type(item.get("executable_sha256")) is not str
            or SHA256_RE.fullmatch(item["executable_sha256"]) is None
            or type(item.get("exit_status")) is not int
            or type(item.get("stdout_text")) is not str
            or type(item.get("stdout_bytes")) is not int
            or item["stdout_bytes"] != len(item["stdout_text"].encode())
            or item.get("stdout_sha256")
            != _sha(item["stdout_text"].encode())
            or type(item.get("stderr_bytes")) is not int
            or item["stderr_bytes"] < 0
            or type(item.get("stderr_sha256")) is not str
            or SHA256_RE.fullmatch(item["stderr_sha256"]) is None
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        executable_authority = (
            argv[0],
            item["executable_size_bytes"],
            item["executable_sha256"],
        )
        prior_executable_authority = executable_authorities.setdefault(
            specification.command_kind, executable_authority
        )
        if prior_executable_authority != executable_authority:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        if role == "pquota":
            availability = item.get("availability_status")
            reason = item.get("availability_reason")
            unavailable_reason_valid = (
                (reason == "COMMAND_NONZERO_EXIT" and item["exit_status"] != 0)
                or (
                    reason == "COMMAND_STDERR_PRESENT"
                    and item["exit_status"] == 0
                    and item["stderr_bytes"] > 0
                )
                or (
                    reason in {
                        "COMMAND_OUTPUT_OVERSIZED",
                        "COMMAND_OUTPUT_NOT_UTF8",
                    }
                    and item["exit_status"] == 0
                    and item["stderr_bytes"] == 0
                    and item["stdout_bytes"] == 0
                    and item["stdout_sha256"] == _sha(b"")
                    and item["stdout_text"] == ""
                )
            )
            if (
                argv[1:] != ["-u", EXPECTED_QUOTA_PRINCIPAL]
                or availability not in {"AVAILABLE", "UNAVAILABLE_NONBLOCKING"}
                or reason not in {
                    "AVAILABLE",
                    "COMMAND_NONZERO_EXIT",
                    "COMMAND_STDERR_PRESENT",
                    "COMMAND_OUTPUT_OVERSIZED",
                    "COMMAND_OUTPUT_NOT_UTF8",
                }
                or (
                    availability == "AVAILABLE"
                    and (
                        reason != "AVAILABLE"
                        or item["exit_status"] != 0
                        or item["stderr_bytes"] != 0
                    )
                )
                or (
                    availability == "UNAVAILABLE_NONBLOCKING"
                    and not unavailable_reason_valid
                )
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                )
        else:
            target_role = "research" if role.startswith("research_") else "backed"
            if specification.command_kind == "findmnt":
                valid_tail = (
                    len(argv) == 6
                    and argv[1:3] == ["--json", "--target"]
                    and argv[4:] == [
                        "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT"
                    ]
                )
                target_value = argv[3] if len(argv) == 6 else ""
            else:
                valid_tail = (
                    len(argv) == 4
                    and argv[1:3]
                    == ["-B1", "--output=source,size,used,avail,target"]
                )
                target_value = argv[3] if len(argv) == 4 else ""
            if (
                item["exit_status"] != 0
                or item["stderr_bytes"] != 0
                or item["stderr_sha256"] != _sha(b"")
                or not valid_tail
                or _sha(target_value.encode())
                != paths["identities"][target_role]["path_sha256"]
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                )
            try:
                if specification.command_kind == "findmnt":
                    reparsed = _parse_findmnt(
                        item["stdout_text"], Path(target_value)
                    )
                    if reparsed != paths["mounts"][target_role]:
                        raise PostReallocationCapacityError(
                            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                        )
                else:
                    reparsed = _parse_df(
                        item["stdout_text"], paths["mounts"][target_role]
                    )
                    if reparsed != paths["df"][target_role]:
                        raise PostReallocationCapacityError(
                            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                        )
            except PostReallocationCapacityError as exc:
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                ) from exc
    return value


def validate_dynamic_successor_capacity_receipt(
    value: Mapping[str, Any],
    *,
    expected_governing_commit: str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate one restricted detailed dynamic-capacity receipt."""

    if not isinstance(value, Mapping) or set(value) != DYNAMIC_SUCCESSOR_RECEIPT_KEYS:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    identity = value.get("capture_identity")
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type") != DYNAMIC_SUCCESSOR_RECEIPT_TYPE
        or value.get("status") not in DYNAMIC_SUCCESSOR_STATUSES
        or not isinstance(identity, Mapping)
        or set(identity) != DYNAMIC_SUCCESSOR_CAPTURE_IDENTITY_KEYS
        or type(identity.get("effective_uid")) is not int
        or identity["effective_uid"] < 0
        or any(
            type(identity.get(field)) is not str
            or SHA256_RE.fullmatch(identity[field]) is None
            for field in ("effective_username_sha256", "hostname_sha256")
        )
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    paths = value.get("paths")
    _validate_dynamic_paths(paths)
    commands = _validate_dynamic_commands(value.get("commands"), paths=paths)
    rows = _validate_dynamic_native_authority(
        value.get("native_quota_authority")
    )
    core_value = value.get("observation_authority")
    if not isinstance(core_value, Mapping):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    core = _validate_dynamic_successor_core(
        core_value,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    core_sha = _dynamic_core_sha256(core)
    display_command = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_command["stdout_text"],
        rows,
        command_available=(
            display_command.get("availability_status") == "AVAILABLE"
        ),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_DISPLAY_CONTRADICTION"
        )
    if (
        value.get("status") != core["status"]
        or value.get("governing_commit") != core["governing_commit"]
        or value.get("captured_at_utc") != core["captured_at_utc"]
        or value.get("observation_authority_sha256") != core_sha
        or core["command_authority_sha256"] != _sha(_canonical(commands))
        or core["native_quota_authority_sha256"]
        != _sha(_canonical(value["native_quota_authority"]))
        or core["mount_authority_sha256"] != _sha(_canonical(paths))
        or core["pquota_display_crosscheck"] != display["status"]
        or core["pquota_executable_authority_status"]
        != (
            "PASS_TRUSTED_ROOT_CONTROLLED"
            if display_command.get("availability_status") == "AVAILABLE"
            else "UNAVAILABLE_NONBLOCKING"
        )
        or core["live_research_quota_bytes"]
        != int(rows["research"]["quota_kib"]) * 1024
        or core["live_research_usage_bytes"]
        != int(rows["research"]["usage_kib"]) * 1024
        or core["live_research_file_quota"]
        != int(rows["research"]["file_quota"])
        or core["live_research_files_used"]
        != int(rows["research"]["files_used"])
        or core["live_research_filesystem_available_bytes"]
        != int(paths["df"]["research"]["available"])
        or core["backed_quota_bytes"]
        != int(rows["backed"]["quota_kib"]) * 1024
        or core["backed_usage_bytes"]
        != int(rows["backed"]["usage_kib"]) * 1024
        or core["backed_file_quota"]
        != int(rows["backed"]["file_quota"])
        or core["backed_files_used"]
        != int(rows["backed"]["files_used"])
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    return dict(value)


def validate_dynamic_successor_capacity_observation(
    value: Mapping[str, Any],
    *,
    expected_governing_commit: str | None = None,
    expected_receipt_payload: bytes | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate one aggregate-safe observation and optional receipt binding."""

    if (
        not isinstance(value, Mapping)
        or set(value) != DYNAMIC_SUCCESSOR_OBSERVATION_KEYS
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    core = {
        key: value[key] for key in DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS
    }
    _validate_dynamic_successor_core(
        core,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    if (
        type(value.get("observation_authority_sha256")) is not str
        or value["observation_authority_sha256"] != _dynamic_core_sha256(core)
        or type(value.get("restricted_receipt_size_bytes")) is not int
        or value["restricted_receipt_size_bytes"] < 1
        or type(value.get("restricted_receipt_sha256")) is not str
        or SHA256_RE.fullmatch(value["restricted_receipt_sha256"]) is None
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    if expected_receipt_payload is not None:
        if (
            not isinstance(expected_receipt_payload, bytes)
            or len(expected_receipt_payload)
            != value["restricted_receipt_size_bytes"]
            or _sha(expected_receipt_payload)
            != value["restricted_receipt_sha256"]
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
        try:
            receipt = json.loads(
                expected_receipt_payload.decode("utf-8"),
                object_pairs_hook=_strict_pairs,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            ) from exc
        if (
            not isinstance(receipt, Mapping)
            or expected_receipt_payload != _canonical(receipt)
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        validate_dynamic_successor_capacity_receipt(
            receipt,
            expected_governing_commit=expected_governing_commit,
            now_utc=now_utc,
        )
        if (
            receipt["observation_authority"] != core
            or receipt["observation_authority_sha256"]
            != value["observation_authority_sha256"]
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
            )
    return dict(value)


def _validate_closed_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Validate immutable admission evidence without consulting this node.

    The receipt and aggregate observation remain closed, canonical, mutually
    bound, and governed by their original commit and event time.  Captured
    UID, hostname, executable, mount, and path-identity fields are evidence of
    that event; this replay context deliberately performs no comparison with
    the current process or filesystem namespace.
    """

    if not isinstance(capture, DynamicSuccessorCapacityCapture):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    receipt_payload = capture.receipt_payload
    if (
        not isinstance(receipt_payload, bytes)
        or receipt_payload != _canonical(capture.receipt)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    receipt = validate_dynamic_successor_capacity_receipt(
        capture.receipt,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    observation = validate_dynamic_successor_capacity_observation(
        capture.observation,
        expected_governing_commit=expected_governing_commit,
        expected_receipt_payload=receipt_payload,
        now_utc=now_utc,
    )
    return DynamicSuccessorCapacityCapture(
        receipt=dict(receipt),
        receipt_payload=receipt_payload,
        observation=dict(observation),
    )


def _validate_production_node_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Require a generic sealed capture to name the exact SCC authorities.

    Synthetic authorities remain useful for dependency-light tests, but they
    may never authorize the fixed production seal consumed by full-C3.  This
    check is metadata/control-only: it does not rerun a capacity command or
    reinterpret the captured quota rows.
    """

    sealed = _validate_closed_dynamic_successor_capacity_capture(
        capture,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    receipt_payload = sealed.receipt_payload
    receipt = sealed.receipt
    observation = sealed.observation
    authority = DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    commands = receipt["commands"]
    paths = receipt["paths"]
    identity = receipt["capture_identity"]
    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    expected_path_by_role = {
        "research": authority.research_path,
        "backed": authority.backed_path,
    }
    expected_executable_by_kind = {
        "pquota": authority.pquota_path,
        "findmnt": authority.findmnt_path,
        "df": authority.df_path,
    }
    static_valid = (
        identity["effective_uid"] == os.geteuid()
        and identity["effective_username_sha256"]
        == _sha(username.encode())
        and receipt["native_quota_authority"]["path_sha256"]
        == _sha(str(authority.native_quota_path).encode())
        and commands["pquota"]["executable_size_bytes"]
        == EXPECTED_PQUOTA_SIZE_BYTES
        and commands["pquota"]["executable_sha256"]
        == EXPECTED_PQUOTA_SHA256
    )
    for role, specification in CAPACITY_COMMAND_REGISTRY.items():
        expected_argv = [
            str(expected_executable_by_kind[specification.command_kind]),
            *specification.argv_tail,
        ]
        if commands[role]["argv"] != expected_argv:
            static_valid = False
    for role, expected_path in expected_path_by_role.items():
        expected_text = str(expected_path)
        path_identity = paths["identities"][role]
        if (
            path_identity["path_sha256"] != _sha(expected_text.encode())
        ):
            static_valid = False
    if not static_valid:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    try:
        if _validate_current_canary_headroom_authority(authority) is not True:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        for role, specification in CAPACITY_COMMAND_REGISTRY.items():
            executable_path = expected_executable_by_kind[
                specification.command_kind
            ]
            executable_payload = _read_regular(
                executable_path, maximum=256_000_000
            )
            if (
                commands[role]["executable_size_bytes"]
                != len(executable_payload)
                or commands[role]["executable_sha256"]
                != _sha(executable_payload)
            ):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                )
        for role, expected_path in expected_path_by_role.items():
            if paths["identities"][role] != _path_identity(expected_path):
                raise PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
                )
        _validate_pquota_restricted_mount_reconciliation(
            native=receipt["native_quota_authority"]["rows"],
            paths=paths["identities"],
            mounts=paths["mounts"],
        )
    except (OSError, PostReallocationCapacityError) as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    return DynamicSuccessorCapacityCapture(
        receipt=dict(receipt),
        receipt_payload=receipt_payload,
        observation=dict(observation),
    )


def _validate_live_preclaim_admission_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    expected_governing_commit: str,
) -> DynamicSuccessorCapacityCapture:
    """Require strict current-node validation and a PASS observation."""

    validated = _validate_production_node_dynamic_successor_capacity_capture(
        capture,
        expected_governing_commit=expected_governing_commit,
        now_utc=None,
    )
    if validated.observation.get("status") != DYNAMIC_SUCCESSOR_STATUS_PASS:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return validated


def validate_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    validation_context: DynamicSuccessorCapacityValidationContext,
    expected_governing_commit: str,
    expected_captured_at_utc: str | None = None,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Dispatch one capture through exactly one closed validation context."""

    if type(validation_context) is not DynamicSuccessorCapacityValidationContext:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
        )
    if validation_context is LIVE_PRECLAIM_ADMISSION:
        if expected_captured_at_utc is not None or now_utc is not None:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
            )
        return _validate_live_preclaim_admission_dynamic_successor_capacity_capture(
            capture,
            expected_governing_commit=expected_governing_commit,
        )
    if validation_context is not SEALED_EXECUTION_REPLAY:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
        )
    if (
        type(expected_captured_at_utc) is not str
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            expected_captured_at_utc,
        )
        is None
        or now_utc is not None
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
        )
    if (
        not isinstance(capture, DynamicSuccessorCapacityCapture)
        or not isinstance(capture.receipt, Mapping)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    if capture.receipt.get("captured_at_utc") != expected_captured_at_utc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_CAPTURE_TIME_MISMATCH"
        )
    try:
        event_time = datetime.fromisoformat(
            expected_captured_at_utc.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
        ) from exc
    return _validate_closed_dynamic_successor_capacity_capture(
        capture,
        expected_governing_commit=expected_governing_commit,
        now_utc=event_time,
    )


def validate_sealed_execution_replay_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    expected_governing_commit: str,
    expected_captured_at_utc: str,
) -> DynamicSuccessorCapacityCapture:
    """Compatibility-named entry for the closed sealed-replay context."""

    return validate_dynamic_successor_capacity_capture(
        capture,
        validation_context=SEALED_EXECUTION_REPLAY,
        expected_governing_commit=expected_governing_commit,
        expected_captured_at_utc=expected_captured_at_utc,
    )


def validate_production_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Retain the strict legacy live-production validation entry point."""

    return _validate_production_node_dynamic_successor_capacity_capture(
        capture,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )


def probe_dynamic_successor_capacity_observation(
    *,
    governing_commit: str,
    active_extraction_caches: int,
    preserved_terminal_failed_extraction_caches: int,
    successor_attempt_root_absent: bool,
    successor_claim_absent: bool,
    authority: CurrentCanaryHeadroomAuthority = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    ),
    process_runner: Callable[..., Any] | None = None,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Capture one fresh dynamic successor observation without side effects."""

    if (
        type(governing_commit) is not str
        or COMMIT_RE.fullmatch(governing_commit) is None
        or type(active_extraction_caches) is not int
        or active_extraction_caches < 0
        or type(preserved_terminal_failed_extraction_caches) is not int
        or preserved_terminal_failed_extraction_caches < 0
        or type(successor_attempt_root_absent) is not bool
        or type(successor_claim_absent) is not bool
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID"
        )
    if (
        active_extraction_caches != 0
        or preserved_terminal_failed_extraction_caches
        != DYNAMIC_SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID"
        )
    if not successor_attempt_root_absent or not successor_claim_absent:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
        )
    if (
        authority == DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
        and process_runner is not None
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
        )
    captured, captured_text = _dynamic_capture_time(now_utc)
    production_candidate = authority == DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    permitted_native_uids = {0} if production_candidate else {0, os.geteuid()}
    try:
        _no_symlink_ancestors(
            authority.native_quota_path, "DYNAMIC_NATIVE_QUOTA_AUTHORITY"
        )
        native_metadata = os.lstat(authority.native_quota_path)
        if (
            stat.S_ISLNK(native_metadata.st_mode)
            or not stat.S_ISREG(native_metadata.st_mode)
            or native_metadata.st_uid not in permitted_native_uids
            or stat.S_IMODE(native_metadata.st_mode) & 0o022
        ):
            raise OSError("native authority invalid")
    except (AttributeError, OSError, PostReallocationCapacityError) as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
        ) from exc
    try:
        production = _validate_current_canary_headroom_authority(authority)
    except (PostReallocationCapacityError, OSError) as exc:
        pquota_invalid = (
            isinstance(exc, PostReallocationCapacityError)
            and exc.code
            == "CURRENT_CANARY_HEADROOM_PQUOTA_AUTHORITY_MISMATCH"
        )
        try:
            _no_symlink_ancestors(
                authority.pquota_path, "DYNAMIC_PQUOTA_AUTHORITY"
            )
            pquota_metadata = os.lstat(authority.pquota_path)
            if (
                stat.S_ISLNK(pquota_metadata.st_mode)
                or not stat.S_ISREG(pquota_metadata.st_mode)
                or pquota_metadata.st_uid not in permitted_native_uids
                or stat.S_IMODE(pquota_metadata.st_mode) & 0o022
                or not stat.S_IMODE(pquota_metadata.st_mode) & 0o111
                or (
                    production_candidate
                    and (
                        pquota_metadata.st_size
                        != EXPECTED_PQUOTA_SIZE_BYTES
                        or _sha(
                            _read_regular(
                                authority.pquota_path,
                                maximum=256_000_000,
                            )
                        )
                        != EXPECTED_PQUOTA_SHA256
                    )
                )
            ):
                pquota_invalid = True
        except (OSError, PostReallocationCapacityError):
            pquota_invalid = True
        raise PostReallocationCapacityError(
            (
                "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
                if pquota_invalid
                else "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
            )
        ) from exc
    permitted_owner_uids = (
        frozenset({0}) if production else frozenset({0, os.geteuid()})
    )
    commands: dict[str, Mapping[str, Any]] = {}
    for specification in CAPACITY_COMMAND_SPECS:
        try:
            argv = _current_canary_command_argv(specification, authority)
            if specification.logical_role == "pquota":
                record = _run_current_canary_pquota(
                    specification, argv, process_runner=process_runner
                )
            else:
                record = _run(
                    specification,
                    argv,
                    process_runner=process_runner,
                    permitted_owner_uids=permitted_owner_uids,
                )
            commands[specification.logical_role] = dict(record)
        except (PostReallocationCapacityError, OSError) as exc:
            code = (
                "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
                if specification.logical_role == "pquota"
                else "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
            )
            raise PostReallocationCapacityError(code) from exc
    if set(commands) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
        )

    try:
        native_payload = _read_regular(
            authority.native_quota_path, maximum=64_000_000
        )
        native = _parse_dynamic_native_quota(native_payload)
    except PostReallocationCapacityError as exc:
        if exc.code in {
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID",
            "DYNAMIC_CAPACITY_ALLOCATION_REGRESSION",
            "DYNAMIC_CAPACITY_BACKED_TIER_CHANGED",
        }:
            raise
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
        ) from exc
    except OSError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID"
        ) from exc
    try:
        identities = {
            "research": _path_identity(authority.research_path),
            "backed": _path_identity(authority.backed_path),
        }
        mounts = {
            "research": _parse_findmnt(
                commands[
                    CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]
                ]["stdout_text"],
                authority.research_path,
            ),
            "backed": _parse_findmnt(
                commands[
                    CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]
                ]["stdout_text"],
                authority.backed_path,
            ),
        }
        if production:
            _validate_pquota_restricted_mount_reconciliation(
                native=native, paths=identities, mounts=mounts
            )
        elif any(
            identities[role]["is_symlink"] is not False
            or mounts[role]["bind"] is not False
            or mounts[role]["fsroot"] != "/"
            or native[role]["native_name_sha256"]
            != _sha(EXPECTED_NATIVE_ROWS[role].encode())
            for role in ("research", "backed")
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
            )
        if (
            mounts["research"]["source"] == mounts["backed"]["source"]
            or mounts["research"]["target"] == mounts["backed"]["target"]
            or identities["research"]["device"]
            == identities["backed"]["device"]
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
            )
        dfs = {
            role: _parse_df(
                commands[
                    CAPACITY_COMMAND_CONSUMER_ROLES[f"{role}_df"]
                ]["stdout_text"],
                mounts[role],
            )
            for role in ("research", "backed")
        }
    except (PostReallocationCapacityError, OSError) as exc:
        if (
            isinstance(exc, PostReallocationCapacityError)
            and exc.code == "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
        ):
            raise
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID"
        ) from exc
    display_record = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_record["stdout_text"],
        native,
        command_available=(
            display_record.get("availability_status") == "AVAILABLE"
        ),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_DISPLAY_CONTRADICTION"
        )

    paths = {"identities": identities, "mounts": mounts, "df": dfs}
    research_quota = int(native["research"]["quota_kib"]) * 1024
    research_usage = int(native["research"]["usage_kib"]) * 1024
    research_file_quota = int(native["research"]["file_quota"])
    research_files_used = int(native["research"]["files_used"])
    physical_available = int(dfs["research"]["available"])
    projected = research_usage + DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    quota_slack = research_quota - projected
    physical_slack = physical_available - DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    quota_margin = quota_slack - DYNAMIC_SUCCESSOR_QUOTA_RESERVE_BYTES
    physical_margin = (
        physical_slack - DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
    )
    remaining_slots = research_file_quota - research_files_used
    slot_margin = remaining_slots - DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS
    quota_gate = quota_margin >= 0
    physical_gate = physical_margin >= 0
    file_gate = slot_margin >= 0
    cache_gate = (
        active_extraction_caches == 0
        and preserved_terminal_failed_extraction_caches
        == DYNAMIC_SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    )
    collision_gate = successor_attempt_root_absent and successor_claim_absent
    visible = research_quota > EXPECTED_RESEARCH_QUOTA_KIB * 1024
    blocked_reasons: list[str] = []
    if not (quota_gate and physical_gate and file_gate):
        blocked_reasons.append("DYNAMIC_CAPACITY_INSUFFICIENT")
    if not blocked_reasons:
        status = DYNAMIC_SUCCESSOR_STATUS_PASS
        reasons = []
    elif not visible:
        status = DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
        reasons = [DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING]
    else:
        status = DYNAMIC_SUCCESSOR_STATUS_BLOCKED
        reasons = sorted(blocked_reasons)
    native_authority = {
        "path_sha256": _sha(str(authority.native_quota_path).encode()),
        "file_size_bytes": len(native_payload),
        "file_sha256": _sha(native_payload),
        "record_unit": "KIB",
        "bytes_per_kib": 1024,
        "rows": native,
    }
    core: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": DYNAMIC_SUCCESSOR_OBSERVATION_TYPE,
        "status": status,
        "blocking_reason_codes": reasons,
        "governing_commit": governing_commit,
        "captured_at_utc": captured_text,
        "command_authority_sha256": _sha(_canonical(commands)),
        "native_quota_authority_sha256": _sha(_canonical(native_authority)),
        "mount_authority_sha256": _sha(_canonical(paths)),
        "mount_authority_status": DYNAMIC_SUCCESSOR_MOUNT_STATUS,
        "pquota_display_crosscheck": display["status"],
        "pquota_executable_authority_status": (
            "PASS_TRUSTED_ROOT_CONTROLLED"
            if display_record.get("availability_status") == "AVAILABLE"
            else "UNAVAILABLE_NONBLOCKING"
        ),
        "live_research_quota_bytes": research_quota,
        "live_research_usage_bytes": research_usage,
        "live_research_file_quota": research_file_quota,
        "live_research_files_used": research_files_used,
        "live_research_filesystem_available_bytes": physical_available,
        "backed_quota_bytes": int(native["backed"]["quota_kib"]) * 1024,
        "backed_usage_bytes": int(native["backed"]["usage_kib"]) * 1024,
        "backed_file_quota": int(native["backed"]["file_quota"]),
        "backed_files_used": int(native["backed"]["files_used"]),
        "backed_tier_gate_passed": True,
        "historical_pre_allocation_research_quota_bytes": (
            EXPECTED_RESEARCH_QUOTA_KIB * 1024
        ),
        "historical_pre_allocation_research_file_quota": (
            EXPECTED_RESEARCH_FILE_QUOTA
        ),
        "fresh_successor_increment_bytes": DYNAMIC_SUCCESSOR_INCREMENT_BYTES,
        "required_quota_reserve_bytes": (
            DYNAMIC_SUCCESSOR_QUOTA_RESERVE_BYTES
        ),
        "required_physical_reserve_bytes": (
            DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
        ),
        "required_remaining_file_slots": DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS,
        "projected_fresh_successor_peak_bytes": projected,
        "quota_slack_after_peak_bytes": quota_slack,
        "physical_slack_after_peak_bytes": physical_slack,
        "quota_margin_beyond_reserve_bytes": quota_margin,
        "physical_margin_beyond_reserve_bytes": physical_margin,
        "remaining_file_slots": remaining_slots,
        "file_slot_margin_after_demand": slot_margin,
        "active_extraction_caches": active_extraction_caches,
        "preserved_terminal_failed_extraction_caches": (
            preserved_terminal_failed_extraction_caches
        ),
        "successor_attempt_root_absent": successor_attempt_root_absent,
        "successor_claim_absent": successor_claim_absent,
        "quota_reserve_gate_passed": quota_gate,
        "physical_reserve_gate_passed": physical_gate,
        "file_slot_gate_passed": file_gate,
        "cache_inventory_gate_passed": cache_gate,
        "successor_collision_gate_passed": collision_gate,
        "minimum_additional_quota_bytes": max(0, -quota_margin),
        "minimum_additional_physical_bytes": max(0, -physical_margin),
        "minimum_additional_file_slots": max(0, -slot_margin),
        "storage_allocation_visible": visible,
        "underlying_filesystem_expansion_appears_necessary": (
            physical_margin < 0
        ),
        "native_quota_authority_read_only": True,
        **{key: 0 for key in DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS},
    }
    _validate_dynamic_successor_core(
        core,
        expected_governing_commit=governing_commit,
        now_utc=captured,
    )
    core_sha = _dynamic_core_sha256(core)
    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": DYNAMIC_SUCCESSOR_RECEIPT_TYPE,
        "status": status,
        "governing_commit": governing_commit,
        "captured_at_utc": captured_text,
        "capture_identity": {
            "effective_uid": os.geteuid(),
            "effective_username_sha256": _sha(username.encode()),
            "hostname_sha256": _sha(socket.gethostname().encode()),
        },
        "native_quota_authority": native_authority,
        "commands": commands,
        "paths": paths,
        "observation_authority": core,
        "observation_authority_sha256": core_sha,
    }
    validate_dynamic_successor_capacity_receipt(
        receipt,
        expected_governing_commit=governing_commit,
        now_utc=captured,
    )
    receipt_payload = _canonical(receipt)
    observation = {
        **core,
        "observation_authority_sha256": core_sha,
        "restricted_receipt_size_bytes": len(receipt_payload),
        "restricted_receipt_sha256": _sha(receipt_payload),
    }
    validate_dynamic_successor_capacity_observation(
        observation,
        expected_governing_commit=governing_commit,
        expected_receipt_payload=receipt_payload,
        now_utc=captured,
    )
    return DynamicSuccessorCapacityCapture(
        receipt=receipt,
        receipt_payload=receipt_payload,
        observation=observation,
    )


def validate_dynamic_successor_capacity_safe_export_policy(
    safe_export_policy_path: Path,
) -> Mapping[str, Any]:
    """Load the closed policy and require the dynamic summary profile."""

    try:
        analysis_modes = importlib.import_module(
            "lvef_multitask_analysis_modes"
        )
        policy, _ = analysis_modes.load_policy(safe_export_policy_path)
    except Exception as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    profiles = policy.get("export_profiles")
    if (
        not isinstance(profiles, Mapping)
        or DYNAMIC_SUCCESSOR_SAFE_EXPORT_PROFILE not in profiles
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    profile = profiles[DYNAMIC_SUCCESSOR_SAFE_EXPORT_PROFILE]
    if (
        not isinstance(profile, Mapping)
        or set(profile)
        != {
            "kind", "extensions", "max_bytes", "required_top_level_keys",
            "allowed_top_level_keys", "field_types",
        }
        or profile.get("kind") != "json"
        or profile.get("extensions") != [".json"]
        or profile.get("max_bytes") != 65_536
        or not isinstance(profile.get("required_top_level_keys"), list)
        or set(profile["required_top_level_keys"])
        != DYNAMIC_SUCCESSOR_OBSERVATION_KEYS
        or len(profile["required_top_level_keys"])
        != len(DYNAMIC_SUCCESSOR_OBSERVATION_KEYS)
        or not isinstance(profile.get("allowed_top_level_keys"), list)
        or set(profile["allowed_top_level_keys"])
        != DYNAMIC_SUCCESSOR_OBSERVATION_KEYS
        or len(profile["allowed_top_level_keys"])
        != len(DYNAMIC_SUCCESSOR_OBSERVATION_KEYS)
        or profile.get("field_types")
        != dict(DYNAMIC_SUCCESSOR_SAFE_FIELD_TYPES)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    return policy


def publish_dynamic_successor_capacity_capture(
    capture: DynamicSuccessorCapacityCapture,
    *,
    restricted_receipt_path: Path,
    aggregate_summary_path: Path,
    safe_export_policy_path: Path,
    now_utc: datetime | None = None,
) -> Mapping[str, Any]:
    """Publish one prevalidated no-clobber receipt/summary pair."""

    if not isinstance(capture, DynamicSuccessorCapacityCapture):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    if any(
        not isinstance(path, Path)
        or not path.is_absolute()
        or os.path.lexists(path)
        for path in (restricted_receipt_path, aggregate_summary_path)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION"
        )
    if (
        restricted_receipt_path.name,
        aggregate_summary_path.name,
    ) not in DYNAMIC_SUCCESSOR_ALLOWED_EVIDENCE_BASENAME_PAIRS:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    receipt = validate_dynamic_successor_capacity_receipt(
        capture.receipt,
        expected_governing_commit=capture.observation.get("governing_commit"),
        now_utc=now_utc,
    )
    receipt_payload = capture.receipt_payload
    if (
        not isinstance(receipt_payload, bytes)
        or receipt_payload != _canonical(receipt)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    observation = validate_dynamic_successor_capacity_observation(
        capture.observation,
        expected_governing_commit=receipt["governing_commit"],
        expected_receipt_payload=receipt_payload,
        now_utc=now_utc,
    )
    summary_payload = _canonical(observation)
    try:
        analysis_modes = importlib.import_module(
            "lvef_multitask_analysis_modes"
        )
        policy = validate_dynamic_successor_capacity_safe_export_policy(
            safe_export_policy_path
        )
        analysis_modes.validate_candidate_bytes(
            summary_payload,
            filename=aggregate_summary_path.name,
            profile_name=DYNAMIC_SUCCESSOR_SAFE_EXPORT_PROFILE,
            policy=policy,
        )
    except Exception as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    if restricted_receipt_path.parent != aggregate_summary_path.parent:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    output_parent = restricted_receipt_path.parent
    if not os.path.lexists(output_parent):
        _mkdir_private(output_parent)
    else:
        _no_symlink_ancestors(
            output_parent / ".dynamic_capacity_parent_probe", "OUTPUT"
        )
        parent_metadata = os.lstat(output_parent)
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) not in {0o700, 0o2700}
        ):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
    # Both collisions and safe-export validation have completed before the
    # first evidence write.  These two owner-authorized evidence files are not
    # effects of the read-only observation represented by writes_performed=0.
    reopened_receipt, reopened_summary = _write_dynamic_owner_private_pair(
        restricted_receipt_path,
        receipt_payload,
        aggregate_summary_path,
        summary_payload,
    )
    if reopened_receipt != receipt_payload or reopened_summary != summary_payload:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    validate_dynamic_successor_capacity_observation(
        json.loads(
            reopened_summary.decode("utf-8"), object_pairs_hook=_strict_pairs
        ),
        expected_governing_commit=receipt["governing_commit"],
        expected_receipt_payload=reopened_receipt,
        now_utc=now_utc,
    )
    return {
        "status": observation["status"],
        "restricted_receipt_basename": restricted_receipt_path.name,
        "restricted_receipt_bytes": len(receipt_payload),
        "restricted_receipt_sha256": _sha(receipt_payload),
        "aggregate_summary_basename": aggregate_summary_path.name,
        "aggregate_summary_bytes": len(summary_payload),
        "aggregate_summary_sha256": _sha(summary_payload),
        "observation": observation,
        "evidence_files_written": 2,
    }


def load_dynamic_successor_capacity_capture(
    *,
    restricted_receipt_path: Path,
    aggregate_summary_path: Path,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> DynamicSuccessorCapacityCapture:
    """Reopen and rebind one fixed owner-private dynamic capture pair."""

    try:
        receipt_payload, summary_payload = _read_dynamic_owner_private_pair(
            restricted_receipt_path,
            aggregate_summary_path,
        )
        receipt = json.loads(
            receipt_payload.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
        observation = json.loads(
            summary_payload.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    if (
        not isinstance(receipt, Mapping)
        or not isinstance(observation, Mapping)
        or receipt_payload != _canonical(receipt)
        or summary_payload != _canonical(observation)
    ):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    validate_dynamic_successor_capacity_receipt(
        receipt,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    validate_dynamic_successor_capacity_observation(
        observation,
        expected_governing_commit=expected_governing_commit,
        expected_receipt_payload=receipt_payload,
        now_utc=now_utc,
    )
    return DynamicSuccessorCapacityCapture(
        receipt=dict(receipt),
        receipt_payload=receipt_payload,
        observation=dict(observation),
    )


def write_dynamic_successor_capacity_receipt_payload(
    path: Path,
    payload: bytes,
    *,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> Mapping[str, Any]:
    """Persist one already-sealed receipt byte-for-byte under a fresh attempt."""

    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    if not isinstance(value, Mapping) or payload != _canonical(value):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    receipt = validate_dynamic_successor_capacity_receipt(
        value,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    _write_dynamic_owner_private_new(path, payload)
    if _read_dynamic_owner_private_regular(path, maximum=16_000_000) != payload:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
        )
    return receipt


def load_dynamic_successor_capacity_receipt_payload(
    path: Path,
    *,
    expected_governing_commit: str,
    now_utc: datetime | None = None,
) -> tuple[Mapping[str, Any], bytes]:
    """Strictly reopen one persisted dynamic receipt without a FIFO/hardlink seam."""

    payload = _read_dynamic_owner_private_regular(
        path, maximum=16_000_000
    )
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        ) from exc
    if not isinstance(value, Mapping) or payload != _canonical(value):
        raise PostReallocationCapacityError(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
        )
    if now_utc is None:
        captured_text = value.get("captured_at_utc")
        if not isinstance(captured_text, str):
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            )
        try:
            now_utc = datetime.fromisoformat(
                captured_text.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise PostReallocationCapacityError(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"
            ) from exc
    receipt = validate_dynamic_successor_capacity_receipt(
        value,
        expected_governing_commit=expected_governing_commit,
        now_utc=now_utc,
    )
    return receipt, payload


def _validate_prior(
    *, original_root: Path, supplemental_root: Path, parent: Path,
    composite: Path, packet_path: Path,
) -> Mapping[str, int]:
    lock = importlib.import_module("lock_lvef_c3_production_orchestration")
    result = lock.validate_immutable_aggregate_authorities(original_root, supplemental_root)
    lock.validate_supplemental_validation_receipt(
        supplemental_root,
        supplemental_authorities=lock.SUPPLEMENTAL_AGGREGATE_AUTHORITIES,
    )
    paths = {
        "phase1ee_parent_capacity": parent,
        "phase1ee_composite_capacity": composite,
        "phase1ee_production_packet_005": packet_path,
    }
    for role, path in paths.items():
        payload = _read_regular(path, private=True)
        expected_size, expected_sha = PRIOR_CAPACITY_AUTHORITIES[role]
        if len(payload) != expected_size or _sha(payload) != expected_sha:
            raise PostReallocationCapacityError("PRIOR_AUTHORITY_HASH_MISMATCH")
    live = importlib.import_module("capture_lvef_c3_live_quota")
    prior_capacity = importlib.import_module("capture_lvef_c3_post_expansion_capacity")
    packet = importlib.import_module("build_lvef_c3_production_authority_packet")
    live.validate_aggregate_output(_load_json(parent, private=True))
    prior_capacity.validate_aggregate_output(_load_json(composite, private=True))
    packet_value = _load_json(packet_path, private=True)
    packet.validate_packet(packet_value)
    return {
        "original": int(result["original_validated_file_count"]),
        "supplemental": int(result["supplemental_validated_file_count"]),
        "capacity": 2,
        "packet_roles": len(packet_value["authority"]),
        "packet_gates": len(packet_value["semantic_validation"]),
    }


def _path_identity(path: Path) -> Mapping[str, Any]:
    # Check the leaf and every ancestor without following any symlink.  Passing
    # a synthetic child makes the shared helper include ``path`` itself in the
    # ancestor walk while requiring no child to exist.
    _no_symlink_ancestors(path / ".phase1ef_nofollow_probe", "CAPACITY_PATH")
    item = os.lstat(path)
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
        raise PostReallocationCapacityError("CAPACITY_PATH_NOT_DIRECTORY_NOFOLLOW")
    return {
        "path_sha256": _sha(str(path).encode()),
        "resolved_path_sha256": _sha(str(path.resolve(strict=True)).encode()),
        "device": item.st_dev,
        "inode": item.st_ino,
        "is_symlink": False,
    }


def _validate_pquota_restricted_mount_reconciliation(
    *, native: Mapping[str, Mapping[str, int | str]],
    paths: Mapping[str, Mapping[str, Any]],
    mounts: Mapping[str, Mapping[str, Any]],
) -> None:
    """Bind native project filesets to the secure no-symlink mount roots.

    Human-facing display aliases are deliberately absent from this primary
    authority check.  The native fileset names and explicit restricted roots
    are independently bound to the mounted filesystems used for C3.
    """
    expected_targets = {
        "backed": Path("/restricted/project"),
        "research": Path("/restricted/projectnb"),
    }
    expected_mount_source_names = {
        "backed": "rproject", "research": "rprojectnb",
    }
    for role in ("backed", "research"):
        expected_path = EXPECTED_RESTRICTED_PATHS[role]
        expected_native_name = EXPECTED_NATIVE_ROWS[role]
        if (
            paths[role]["resolved_path_sha256"] != _sha(str(expected_path).encode())
            or paths[role]["is_symlink"] is not False
            or mounts[role]["target"] != str(expected_targets[role])
            or PurePosixPath(mounts[role]["source"].split(":")[-1]).name
            != expected_mount_source_names[role]
            or mounts[role]["fsroot"] != "/"
            or mounts[role]["bind"] is not False
            or native[role]["native_name_sha256"]
            != _sha(expected_native_name.encode())
        ):
            raise PostReallocationCapacityError(
                "PQUOTA_RESTRICTED_MOUNT_RECONCILIATION_FAILED"
            )


def _gate_evaluation_status(values: Mapping[str, bool]) -> Mapping[str, str]:
    if set(values) != set(CAPACITY_GATE_KEYS) or any(
        not isinstance(value, bool) for value in values.values()
    ):
        raise PostReallocationCapacityError("CAPACITY_GATE_INPUT_INTERNAL_INVALID")
    return {
        key: GATE_EVALUATION_PASS if values[key] else GATE_EVALUATION_FAIL
        for key in CAPACITY_GATE_KEYS
    }


def _not_evaluated_gate_status() -> Mapping[str, str]:
    return {key: GATE_EVALUATION_NOT_EVALUATED for key in CAPACITY_GATE_KEYS}


def _validate_gate_evaluation_status(value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != set(CAPACITY_GATE_KEYS)
        or any(item not in GATE_EVALUATION_STATES for item in value.values())
    ):
        raise PostReallocationCapacityError("CAPACITY_GATE_EVALUATION_SCHEMA_INVALID")


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != AGGREGATE_TYPE
        or value.get("status") != AGGREGATE_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not SHA256_RE.fullmatch(str(value.get("restricted_receipt_sha256", "")))
        or value.get("units") != "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER"
        or value.get("quota_display_unit_ruling")
        != "BINARY_GIB_ROUNDED_SECONDARY_ONLY"
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_AUTHORITY_INVALID")
    aggregate_gate_status = {
        gate: value.get(field)
        for gate, field in AGGREGATE_GATE_STATUS_FIELDS.items()
    }
    _validate_gate_evaluation_status(aggregate_gate_status)
    display_state = value.get("pquota_display_crosscheck")
    display_reason = value.get("pquota_display_crosscheck_reason")
    executable_authority = value.get("pquota_executable_authority_status")
    if (
        display_state not in {
            DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or display_reason not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(
            str(display_state), ()
        )
        or value.get("pquota_display_rounding_rule")
        != "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6"
        or value.get("pquota_display_backed_project_row_matches") not in {0, 1}
        or value.get("pquota_display_research_project_row_matches") not in {0, 1}
        or executable_authority not in {
            "PASS_TRUSTED_ROOT_CONTROLLED", "UNAVAILABLE_NONBLOCKING",
        }
        or (
            executable_authority == "PASS_TRUSTED_ROOT_CONTROLLED"
            and value.get("pquota_executable_sha256") != EXPECTED_PQUOTA_SHA256
        )
        or (
            executable_authority == "UNAVAILABLE_NONBLOCKING"
            and value.get("pquota_executable_sha256") not in {
                EXPECTED_PQUOTA_SHA256, "UNAVAILABLE",
            }
        )
        or (
            executable_authority == "UNAVAILABLE_NONBLOCKING"
            and display_reason != "COMMAND_UNAVAILABLE"
        )
        or value.get("pquota_display_fileset_mapping_verified")
        is not (display_state == DISPLAY_CROSSCHECK_PASS)
        or (
            display_state == DISPLAY_CROSSCHECK_PASS
            and (
                value.get("pquota_display_backed_project_row_matches") != 1
                or value.get("pquota_display_research_project_row_matches") != 1
            )
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_DISPLAY_AUTHORITY_INVALID")
    required_true = {
        "prior_authorities_hash_verified", "prior_authorities_closed_schema_verified",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_current_not_snapshot_mode_verified",
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed", "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed", "backed_control_tier_gate_passed",
        "purchased_saas_allocation_remains_on_research",
    }
    if any(value.get(key) is not True for key in required_true):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_GATE_NOT_PASS")
    gate_boolean_pairs = {
        "research_quota_gate": "research_quota_gate_passed",
        "physical_filesystem_capacity_gate": "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate": "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate": "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate": "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate": "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate": "backed_control_tier_gate_passed",
    }
    if any(
        aggregate_gate_status[gate] != GATE_EVALUATION_PASS
        or value.get(boolean_key) is not True
        for gate, boolean_key in gate_boolean_pairs.items()
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_GATE_STATUS_MISMATCH")
    expected_false = {
        "research_path_is_symlink", "backed_path_is_symlink",
        "research_mount_is_bind", "backed_mount_is_bind",
        "additional_project_quota_row_for_same_principal_observed",
        "snapshot_presence_independently_enumerated",
        "control_write_binding_evaluated_by_capacity_receipt",
        "object_listing_repeated",
        "storage_inventory_repeated", "real_dicom_extraction",
        "echoprime_inference", "model_fitting", "confirmatory_performance_accessed",
        "quota_changed", "full_c3_authorized",
    }
    if any(value.get(key) is not False for key in expected_false):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_BOUNDARY_INVALID")
    if (
        value.get("snapshot_capacity_double_counting_avoided") is not True
        or value.get("snapshot_accounting_ruling")
        != "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_SNAPSHOT_RULING_INVALID")
    for key in ("cloud_requests", "scheduler_jobs_submitted", "dicom_bodies_downloaded", "files_moved", "files_deleted"):
        if value.get(key) != 0:
            raise PostReallocationCapacityError("CAPACITY_AGGREGATE_EXECUTION_OCCURRED")
    if (
        value.get("research_quota_bytes") != EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or value.get("backed_quota_bytes") != EXPECTED_BACKED_QUOTA_KIB * 1024
        or value.get("research_quota_margin_above_minimum_bytes")
        != value["research_quota_bytes"] - MINIMUM_EFFECTIVE_QUOTA_BYTES
        or value.get("research_quota_slack_after_projected_peak_bytes")
        != value["research_quota_bytes"] - PROJECTED_PEAK_BYTES
        or value.get("research_margin_beyond_200gb_reserve_bytes")
        != value["research_quota_bytes"] - PROJECTED_PEAK_BYTES - REQUIRED_FREE_HEADROOM_BYTES
        or value.get("pretransfer_research_write_bound_bytes")
        != PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        or value.get("research_physical_required_available_bytes")
        != value["research_remaining_write_bytes"]
        + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        or value.get("research_physical_slack_bytes")
        != value["research_filesystem_available_bytes"]
        - value["research_physical_required_available_bytes"]
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_ARITHMETIC_INVALID")


def _validate_command_record_contract(
    specification: CapacityCommandSpec,
    item: Mapping[str, Any],
) -> None:
    argv = item.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or argv[1:] != list(specification.argv_tail)
        or item.get("argv_sha256")
        != _sha(json.dumps(argv, separators=(",", ":")).encode())
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_ARGV_INVALID")
    executable_value = argv[0]
    executable_path = PurePosixPath(executable_value)
    if specification.optional_nonblocking:
        availability = item.get("availability_status")
        reason = item.get("availability_reason")
        if reason == "EXECUTABLE_NOT_FOUND":
            valid_authority = (
                executable_value == specification.command_kind
                and item.get("executable_sha256") == "UNAVAILABLE"
                and item.get("executable_size_bytes") == 0
            )
        elif reason == "EXECUTABLE_AUTHORITY_MISMATCH":
            valid_authority = (
                executable_path.is_absolute()
                and executable_path.name == specification.command_kind
                and item.get("executable_sha256") == "UNAVAILABLE"
                and item.get("executable_size_bytes") == 0
            )
        else:
            valid_authority = (
                executable_path.is_absolute()
                and executable_path.name == specification.command_kind
                and item.get("executable_sha256") == EXPECTED_PQUOTA_SHA256
                and item.get("executable_size_bytes") == EXPECTED_PQUOTA_SIZE_BYTES
            )
        if availability == "AVAILABLE" and reason != "AVAILABLE":
            valid_authority = False
        if not valid_authority:
            raise PostReallocationCapacityError(
                "CAPACITY_RECEIPT_COMMAND_EXECUTABLE_INVALID"
            )
    elif (
        not executable_path.is_absolute()
        or executable_path.name != specification.command_kind
        or executable_path.parent
        not in {
            PurePosixPath("/usr/bin"),
            PurePosixPath("/bin"),
            PurePosixPath("/usr/local/bin"),
        }
        or not isinstance(item.get("executable_size_bytes"), int)
        or isinstance(item.get("executable_size_bytes"), bool)
        or item["executable_size_bytes"] <= 0
        or not SHA256_RE.fullmatch(str(item.get("executable_sha256", "")))
    ):
        raise PostReallocationCapacityError(
            "CAPACITY_RECEIPT_COMMAND_EXECUTABLE_INVALID"
        )


def validate_receipt_output(value: Mapping[str, Any]) -> None:
    """Validate the closed detailed receipt without trusting path contents."""
    if not isinstance(value, Mapping) or set(value) != RECEIPT_KEYS:
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != RECEIPT_TYPE
        or value.get("status") != RECEIPT_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_AUTHORITY_INVALID")
    commands = value.get("commands")
    paths = value.get("paths")
    native = value.get("native_quota_authority")
    frozen = value.get("frozen_plan")
    attestations = value.get("no_mutation_attestations")
    display = value.get("pquota_display_crosscheck")
    gate_status = value.get("gate_evaluation_status")
    if (
        not isinstance(commands, Mapping)
        or set(commands) != CAPACITY_COMMAND_ROLES
        or not isinstance(paths, Mapping)
        or set(paths) != {"identities", "mounts", "df"}
        or not isinstance(native, Mapping)
        or native.get("record_unit") != "KIB"
        or native.get("bytes_per_kib") != 1024
        or not isinstance(native.get("rows"), Mapping)
        or set(native["rows"]) != {"backed", "research"}
        or not isinstance(frozen, Mapping)
        or frozen != {
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes": PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
        }
        or not isinstance(attestations, Mapping)
        or attestations != {
            "cloud_requests": 0, "object_listing_repeated": False,
            "storage_inventory_repeated": False, "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0, "real_dicom_extraction": False,
            "echoprime_inference": False, "model_fitting": False,
            "confirmatory_performance_accessed": False, "quota_changed": False,
            "files_moved": 0, "files_deleted": 0, "full_c3_authorized": False,
        }
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_CONTENT_INVALID")
    _validate_gate_evaluation_status(gate_status)
    if any(item != GATE_EVALUATION_PASS for item in gate_status.values()):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_GATE_STATUS_INVALID")
    if (
        not isinstance(display, Mapping)
        or set(display) != {
            "status", "reason", "project_row_match_counts",
            "usage_decimal_places", "rounding_rule",
        }
        or display.get("status") not in {
            DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or display.get("reason") not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(
            str(display.get("status")), ()
        )
        or display.get("rounding_rule")
        != "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6"
        or not isinstance(display.get("project_row_match_counts"), Mapping)
        or set(display["project_row_match_counts"]) != {"backed", "research"}
        or any(value not in {0, 1} for value in display["project_row_match_counts"].values())
        or not isinstance(display.get("usage_decimal_places"), Mapping)
        or not set(display["usage_decimal_places"]).issubset({"backed", "research"})
        or any(
            not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 6
            for item in display["usage_decimal_places"].values()
        )
        or (
            display.get("status") == DISPLAY_CROSSCHECK_PASS
            and display["project_row_match_counts"] != {"backed": 1, "research": 1}
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_DISPLAY_SCHEMA_INVALID")
    registry = _validated_command_registry(
        CAPACITY_COMMAND_SPECS, require_canonical_roles=True
    )
    for role, specification in registry.items():
        item = commands[role]
        stdout_text = item.get("stdout_text")
        base_keys = {
            "role", "argv", "argv_sha256", "executable_sha256",
            "executable_size_bytes", "exit_status", "stdout_bytes",
            "stdout_sha256", "stdout_text", "stderr_bytes", "stderr_sha256",
        }
        expected_keys = base_keys | (
            {"availability_status", "availability_reason"}
            if role == "pquota" else set()
        )
        if not isinstance(item, Mapping) or set(item) != expected_keys or item.get("role") != role:
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_INVALID")
        if role == "pquota":
            availability = item.get("availability_status")
            reason = item.get("availability_reason")
            if (
                availability not in {"AVAILABLE", "UNAVAILABLE_NONBLOCKING"}
                or not isinstance(reason, str)
                or reason not in {
                    "AVAILABLE", "EXECUTABLE_NOT_FOUND",
                    "EXECUTABLE_AUTHORITY_MISMATCH", "COMMAND_NONZERO_EXIT",
                    "COMMAND_STDERR_PRESENT", "COMMAND_OUTPUT_OVERSIZED",
                    "COMMAND_OUTPUT_NOT_UTF8",
                }
                or not isinstance(stdout_text, str)
                or not isinstance(item.get("stdout_bytes"), int)
                or not isinstance(item.get("stderr_bytes"), int)
                or not SHA256_RE.fullmatch(str(item.get("stdout_sha256", "")))
                or not SHA256_RE.fullmatch(str(item.get("stderr_sha256", "")))
                or item.get("executable_sha256") not in {
                    EXPECTED_PQUOTA_SHA256, "UNAVAILABLE",
                }
                or (availability == "AVAILABLE" and reason != "AVAILABLE")
                or (
                    availability == "UNAVAILABLE_NONBLOCKING"
                    and reason == "AVAILABLE"
                )
                or (availability == "AVAILABLE" and item.get("exit_status") != 0)
                or (availability == "AVAILABLE" and item.get("stderr_bytes") != 0)
                or (
                    availability == "AVAILABLE"
                    and item.get("stdout_bytes") != len(stdout_text.encode("utf-8"))
                )
                or (
                    availability == "AVAILABLE"
                    and item.get("stdout_sha256") != _sha(stdout_text.encode("utf-8"))
                )
            ):
                raise PostReallocationCapacityError("CAPACITY_RECEIPT_PQUOTA_COMMAND_INVALID")
        elif (
            item.get("exit_status") != 0
            or item.get("stderr_bytes") != 0
            or item.get("stderr_sha256") != _sha(b"")
            or not isinstance(stdout_text, str)
            or item.get("stdout_bytes") != len(stdout_text.encode("utf-8"))
            or item.get("stdout_sha256") != _sha(stdout_text.encode("utf-8"))
            or not SHA256_RE.fullmatch(str(item.get("stdout_sha256", "")))
            or not SHA256_RE.fullmatch(str(item.get("executable_sha256", "")))
        ):
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_INVALID")
        _validate_command_record_contract(specification, item)
    pquota_available = commands["pquota"]["availability_status"] == "AVAILABLE"
    if (
        (not pquota_available and display.get("reason") != "COMMAND_UNAVAILABLE")
        or (pquota_available and display.get("reason") == "COMMAND_UNAVAILABLE")
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_DISPLAY_COMMAND_MISMATCH")
    identities = paths["identities"]
    mounts = paths["mounts"]
    df_values = paths["df"]
    if (
        not isinstance(identities, Mapping) or set(identities) != {"backed", "research"}
        or not isinstance(mounts, Mapping) or set(mounts) != {"backed", "research"}
        or not isinstance(df_values, Mapping) or set(df_values) != {"backed", "research"}
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_PATH_SCHEMA_INVALID")
    for role in ("backed", "research"):
        identity = identities[role]
        mount = mounts[role]
        df_value = df_values[role]
        if (
            not isinstance(identity, Mapping)
            or set(identity) != {
                "path_sha256", "resolved_path_sha256", "device", "inode",
                "is_symlink",
            }
            or identity.get("is_symlink") is not False
            or not SHA256_RE.fullmatch(str(identity.get("path_sha256", "")))
            or not SHA256_RE.fullmatch(str(identity.get("resolved_path_sha256", "")))
            or not isinstance(mount, Mapping)
            or set(mount) != {
                "source_sha256", "target_sha256", "fsroot_sha256",
                "identity_sha256", "source", "target", "fsroot", "fstype",
                "bind",
            }
            or mount.get("fsroot") != "/"
            or mount.get("bind") is not False
            or not all(
                SHA256_RE.fullmatch(str(mount.get(key, "")))
                for key in (
                    "source_sha256", "target_sha256", "fsroot_sha256",
                    "identity_sha256",
                )
            )
            or not isinstance(df_value, Mapping)
            or set(df_value) != {"total", "used", "available"}
            or any(
                not isinstance(df_value.get(key), int)
                or isinstance(df_value.get(key), bool)
                or df_value[key] < 0
                for key in ("total", "used", "available")
            )
        ):
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_PATH_AUTHORITY_INVALID")


def _build_capacity_receipt(
    *,
    attempt_id: str,
    governing_commit: str,
    native_quota_file: Path,
    native_payload: bytes,
    native: Mapping[str, Any],
    commands: Mapping[str, Any],
    identities: Mapping[str, Any],
    mounts: Mapping[str, Any],
    dfs: Mapping[str, Any],
    prior: Mapping[str, Any],
    display: Mapping[str, Any],
    gate_status: Mapping[str, str],
) -> Mapping[str, Any]:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": RECEIPT_TYPE,
        "status": RECEIPT_STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_identity": {
            "effective_uid": os.getuid(),
            "effective_username_sha256": _sha(
                pwd.getpwuid(os.getuid()).pw_name.encode()
            ),
            "hostname_sha256": _sha(socket.gethostname().encode()),
        },
        "native_quota_authority": {
            "path_sha256": _sha(str(native_quota_file).encode()),
            "file_size_bytes": len(native_payload),
            "file_sha256": _sha(native_payload),
            "record_unit": "KIB",
            "bytes_per_kib": 1024,
            "rows": native,
        },
        "commands": commands,
        "paths": {"identities": identities, "mounts": mounts, "df": dfs},
        "prior_authorities": prior,
        "frozen_plan": {
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes": (
                PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
            ),
        },
        "pquota_display_crosscheck": display,
        "gate_evaluation_status": gate_status,
        "no_mutation_attestations": {
            "cloud_requests": 0,
            "object_listing_repeated": False,
            "storage_inventory_repeated": False,
            "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0,
            "real_dicom_extraction": False,
            "echoprime_inference": False,
            "model_fitting": False,
            "confirmatory_performance_accessed": False,
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
            "full_c3_authorized": False,
        },
    }
    if set(receipt) != RECEIPT_KEYS:
        raise PostReallocationCapacityError(
            "CAPACITY_RECEIPT_SCHEMA_INTERNAL_ERROR"
        )
    validate_receipt_output(receipt)
    return receipt


def _project_capacity_aggregate(
    *,
    attempt_id: str,
    governing_commit: str,
    receipt: Mapping[str, Any],
    receipt_payload: bytes,
    commands: Mapping[str, Any],
    native: Mapping[str, Any],
    mounts: Mapping[str, Any],
    dfs: Mapping[str, Any],
    prior: Mapping[str, Any],
    display: Mapping[str, Any],
    gate_status: Mapping[str, str],
    gate_values: Mapping[str, bool],
    research_quota_bytes: int,
    research_usage_bytes: int,
    backed_quota_bytes: int,
    backed_usage_bytes: int,
    remaining_write_bytes: int,
    physical_required_bytes: int,
) -> Mapping[str, Any]:
    validate_receipt_output(receipt)
    if (
        receipt_payload != _canonical(receipt)
        or commands != receipt["commands"]
        or native != receipt["native_quota_authority"]["rows"]
        or mounts != receipt["paths"]["mounts"]
        or dfs != receipt["paths"]["df"]
        or prior != receipt["prior_authorities"]
        or display != receipt["pquota_display_crosscheck"]
        or gate_status != receipt["gate_evaluation_status"]
    ):
        raise PostReallocationCapacityError(
            "CAPACITY_AGGREGATE_RECEIPT_BINDING_INVALID"
        )
    if set(commands) != AGGREGATE_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "CAPACITY_AGGREGATE_COMMAND_ROLE_SET_INVALID"
        )
    pquota = commands[CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]]
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": AGGREGATE_TYPE,
        "status": AGGREGATE_STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "units": "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER",
        "quota_display_unit_ruling": "BINARY_GIB_ROUNDED_SECONDARY_ONLY",
        "restricted_receipt_size_bytes": len(receipt_payload),
        "restricted_receipt_sha256": _sha(receipt_payload),
        "prior_authorities_hash_verified": True,
        "prior_authorities_closed_schema_verified": True,
        "immutable_original_aggregate_count": prior["original"],
        "immutable_supplemental_aggregate_count": prior["supplemental"],
        "prior_capacity_authority_count": prior["capacity"],
        "prior_production_authority_roles": prior["packet_roles"],
        "prior_production_semantic_gates": prior["packet_gates"],
        "research_quota_bytes": research_quota_bytes,
        "research_usage_bytes": research_usage_bytes,
        "research_quota_remaining_bytes": (
            research_quota_bytes - research_usage_bytes
        ),
        "research_file_quota": int(native["research"]["file_quota"]),
        "research_files_used": int(native["research"]["files_used"]),
        "research_file_slots_remaining": (
            int(native["research"]["file_quota"])
            - int(native["research"]["files_used"])
        ),
        "research_filesystem_total_bytes": dfs["research"]["total"],
        "research_filesystem_used_bytes": dfs["research"]["used"],
        "research_filesystem_available_bytes": dfs["research"]["available"],
        "research_filesystem_type": mounts["research"]["fstype"],
        "research_filesystem_identity_sha256": mounts["research"]["identity_sha256"],
        "backed_quota_bytes": backed_quota_bytes,
        "backed_usage_bytes": backed_usage_bytes,
        "backed_quota_remaining_bytes": backed_quota_bytes - backed_usage_bytes,
        "backed_file_quota": int(native["backed"]["file_quota"]),
        "backed_files_used": int(native["backed"]["files_used"]),
        "backed_file_slots_remaining": (
            int(native["backed"]["file_quota"])
            - int(native["backed"]["files_used"])
        ),
        "backed_filesystem_total_bytes": dfs["backed"]["total"],
        "backed_filesystem_used_bytes": dfs["backed"]["used"],
        "backed_filesystem_available_bytes": dfs["backed"]["available"],
        "backed_filesystem_type": mounts["backed"]["fstype"],
        "backed_filesystem_identity_sha256": mounts["backed"]["identity_sha256"],
        "pquota_to_restricted_mount_reconciliation_verified": True,
        "pquota_display_fileset_mapping_verified": (
            display["status"] == DISPLAY_CROSSCHECK_PASS
        ),
        "pquota_current_not_snapshot_mode_verified": True,
        "pquota_executable_sha256": pquota["executable_sha256"],
        "pquota_executable_authority_status": (
            "PASS_TRUSTED_ROOT_CONTROLLED"
            if pquota["executable_sha256"] == EXPECTED_PQUOTA_SHA256
            else "UNAVAILABLE_NONBLOCKING"
        ),
        "pquota_display_crosscheck": display["status"],
        "pquota_display_crosscheck_reason": display["reason"],
        "pquota_display_backed_project_row_matches": (
            display["project_row_match_counts"]["backed"]
        ),
        "pquota_display_research_project_row_matches": (
            display["project_row_match_counts"]["research"]
        ),
        "pquota_display_rounding_rule": display["rounding_rule"],
        **{
            AGGREGATE_GATE_STATUS_FIELDS[gate]: status
            for gate, status in gate_status.items()
        },
        "research_mount_fsroot_is_root": mounts["research"]["fsroot"] == "/",
        "backed_mount_fsroot_is_root": mounts["backed"]["fsroot"] == "/",
        "mounted_filesystems_distinct": True,
        "mount_targets_distinct": True,
        "filesystem_devices_distinct": True,
        "research_path_is_symlink": False,
        "backed_path_is_symlink": False,
        "research_mount_is_bind": False,
        "backed_mount_is_bind": False,
        "additional_project_quota_row_for_same_principal_observed": False,
        "snapshot_capacity_double_counting_avoided": True,
        "snapshot_presence_independently_enumerated": False,
        "snapshot_accounting_ruling": (
            "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
        ),
        "selected_source_bytes": SELECTED_SOURCE_BYTES,
        "projected_peak_bytes": PROJECTED_PEAK_BYTES,
        "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
        "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
        "research_remaining_write_bytes": remaining_write_bytes,
        "pretransfer_research_write_bound_bytes": (
            PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        ),
        "research_physical_required_available_bytes": physical_required_bytes,
        "research_quota_margin_above_minimum_bytes": (
            research_quota_bytes - MINIMUM_EFFECTIVE_QUOTA_BYTES
        ),
        "research_quota_slack_after_projected_peak_bytes": (
            research_quota_bytes - PROJECTED_PEAK_BYTES
        ),
        "research_margin_beyond_200gb_reserve_bytes": (
            research_quota_bytes
            - PROJECTED_PEAK_BYTES
            - REQUIRED_FREE_HEADROOM_BYTES
        ),
        "research_physical_slack_bytes": (
            dfs["research"]["available"] - physical_required_bytes
        ),
        "control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "backed_remaining_after_control_burden_bytes": (
            backed_quota_bytes
            - backed_usage_bytes
            - PRESPECIFIED_CONTROL_BURDEN_BYTES
        ),
        "research_additional_file_demand": RESEARCH_ADDITIONAL_FILE_DEMAND,
        "backed_additional_file_demand": CONTROL_ADDITIONAL_FILE_DEMAND,
        "research_quota_gate_passed": gate_values["research_quota_gate"],
        "physical_filesystem_capacity_gate_passed": gate_values[
            "physical_filesystem_capacity_gate"
        ],
        "projected_200gb_reserve_gate_passed": gate_values[
            "projected_200gb_reserve_gate"
        ],
        "research_file_quota_gate_passed": gate_values[
            "research_file_quota_gate"
        ],
        "backed_control_tier_byte_gate_passed": gate_values[
            "backed_control_tier_byte_gate"
        ],
        "backed_control_tier_file_gate_passed": gate_values[
            "backed_control_tier_file_gate"
        ],
        "backed_control_tier_gate_passed": gate_values[
            "backed_control_tier_gate"
        ],
        "owner_reported_backed_free_pool_gb": 50,
        "owner_reported_research_free_pool_gb": 950,
        "owner_reported_research_saas_purchased_gb": 1000,
        "owner_reported_total_research_quota_gb": 1950,
        "purchased_saas_allocation_remains_on_research": True,
        "control_write_binding_evaluated_by_capacity_receipt": False,
        **receipt["no_mutation_attestations"],
    }
    validate_aggregate_output(aggregate)
    return aggregate


def capture(args: argparse.Namespace) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(args.attempt_id) or not COMMIT_RE.fullmatch(args.governing_commit):
        raise PostReallocationCapacityError("CAPACITY_IDENTITY_INVALID")
    _validate_checkout(args.checkout, args.governing_commit)
    if (
        args.research_path != EXPECTED_RESTRICTED_PATHS["research"]
        or args.backed_path != EXPECTED_RESTRICTED_PATHS["backed"]
        or args.native_quota_file != EXPECTED_NATIVE_QUOTA_FILE
        or args.quota_principal != EXPECTED_QUOTA_PRINCIPAL
    ):
        raise PostReallocationCapacityError("CAPACITY_RESTRICTED_PATH_MISMATCH")
    native_metadata = os.lstat(args.native_quota_file)
    if (
        not stat.S_ISREG(native_metadata.st_mode)
        or stat.S_ISLNK(native_metadata.st_mode)
        or native_metadata.st_uid != 0
        or stat.S_IMODE(native_metadata.st_mode) & 0o022
    ):
        raise PostReallocationCapacityError("NATIVE_QUOTA_AUTHORITY_NOT_ROOT_CONTROLLED")
    attempt_root = args.attempt_root
    if attempt_root.is_symlink() or not attempt_root.is_dir() or stat.S_IMODE(attempt_root.stat().st_mode) & 0o077:
        raise PostReallocationCapacityError("ATTEMPT_ROOT_NOT_PRIVATE")
    restricted_root = attempt_root / "restricted" / "capacity"
    aggregate_root = attempt_root / "aggregate"
    if not (attempt_root / "restricted").exists():
        _mkdir_private(attempt_root / "restricted")
    if not aggregate_root.exists():
        _mkdir_private(aggregate_root)
    _mkdir_private(restricted_root)

    prior = _validate_prior(
        original_root=args.original_aggregate_root,
        supplemental_root=args.supplemental_aggregate_root,
        parent=args.prior_capacity_parent,
        composite=args.prior_capacity_composite,
        packet_path=args.prior_production_packet,
    )
    commands = _capture_capacity_commands(args.quota_principal)
    native_payload = _read_regular(args.native_quota_file, maximum=64_000_000)
    native = _parse_native_quota(native_payload)
    paths = {"research": _path_identity(args.research_path), "backed": _path_identity(args.backed_path)}
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]]["stdout_text"],
            args.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]]["stdout_text"],
            args.backed_path,
        ),
    }
    _validate_pquota_restricted_mount_reconciliation(
        native=native, paths=paths, mounts=mounts,
    )
    dfs = {
        "research": _parse_df(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_df"]]["stdout_text"],
            mounts["research"],
        ),
        "backed": _parse_df(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_df"]]["stdout_text"],
            mounts["backed"],
        ),
    }
    if (
        mounts["research"]["source"] == mounts["backed"]["source"]
        or mounts["research"]["target"] == mounts["backed"]["target"]
        or paths["research"]["device"] == paths["backed"]["device"]
        or mounts["research"]["bind"] or mounts["backed"]["bind"]
    ):
        raise PostReallocationCapacityError("FILESYSTEM_DISTINCTNESS_GATE_FAILED")

    rq = int(native["research"]["quota_kib"]) * 1024
    ru = int(native["research"]["usage_kib"]) * 1024
    bq = int(native["backed"]["quota_kib"]) * 1024
    bu = int(native["backed"]["usage_kib"]) * 1024
    remaining_write = max(PROJECTED_PEAK_BYTES - ru, 0)
    physical_required = (
        remaining_write + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
    )
    gate_values = {
        "research_quota_gate": rq >= MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "physical_filesystem_capacity_gate":
            dfs["research"]["available"] >= physical_required,
        "projected_200gb_reserve_gate":
            rq - PROJECTED_PEAK_BYTES >= REQUIRED_FREE_HEADROOM_BYTES,
        "research_file_quota_gate":
            int(native["research"]["file_quota"])
            - int(native["research"]["files_used"])
            >= RESEARCH_ADDITIONAL_FILE_DEMAND,
        "backed_control_tier_byte_gate":
            bq - bu >= PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "backed_control_tier_file_gate":
            int(native["backed"]["file_quota"])
            - int(native["backed"]["files_used"])
            >= CONTROL_ADDITIONAL_FILE_DEMAND,
    }
    gate_values["backed_control_tier_gate"] = (
        gate_values["backed_control_tier_byte_gate"]
        and gate_values["backed_control_tier_file_gate"]
    )
    gate_status = _gate_evaluation_status(gate_values)
    display = _parse_pquota(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]]["stdout_text"],
        native,
        command_available=commands[
            CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
        ]["availability_status"] == "AVAILABLE",
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "PQUOTA_DISPLAY_CROSSCHECK_BLOCKING",
            gate_evaluation_status=gate_status,
            pquota_display_crosscheck=DISPLAY_CROSSCHECK_FAIL,
        )
    if any(status == GATE_EVALUATION_FAIL for status in gate_status.values()):
        raise PostReallocationCapacityError(
            "CAPACITY_GATE_FAILED",
            gate_evaluation_status=gate_status,
            pquota_display_crosscheck=str(display["status"]),
        )

    receipt = _build_capacity_receipt(
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        native_quota_file=args.native_quota_file,
        native_payload=native_payload,
        native=native,
        commands=commands,
        identities=paths,
        mounts=mounts,
        dfs=dfs,
        prior=prior,
        display=display,
        gate_status=gate_status,
    )
    receipt_path = restricted_root / "post_reallocation_capacity.restricted.json"
    receipt_payload = _canonical(receipt)
    _write_new(receipt_path, receipt_payload, private=True)

    aggregate = _project_capacity_aggregate(
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        receipt=receipt,
        receipt_payload=receipt_payload,
        commands=commands,
        native=native,
        mounts=mounts,
        dfs=dfs,
        prior=prior,
        display=display,
        gate_status=gate_status,
        gate_values=gate_values,
        research_quota_bytes=rq,
        research_usage_bytes=ru,
        backed_quota_bytes=bq,
        backed_usage_bytes=bu,
        remaining_write_bytes=remaining_write,
        physical_required_bytes=physical_required,
    )
    aggregate_path = aggregate_root / "lvef_c3_post_reallocation_capacity.summary.json"
    _write_new(aggregate_path, _canonical(aggregate), private=True)
    return aggregate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--quota-principal", default=EXPECTED_QUOTA_PRINCIPAL)
    parser.add_argument("--native-quota-file", type=Path, default=Path("/usr/local/etc/quota/project.quota"))
    parser.add_argument("--research-path", type=Path, required=True)
    parser.add_argument("--backed-path", type=Path, required=True)
    parser.add_argument("--original-aggregate-root", type=Path, required=True)
    parser.add_argument("--supplemental-aggregate-root", type=Path, required=True)
    parser.add_argument("--prior-capacity-parent", type=Path, required=True)
    parser.add_argument("--prior-capacity-composite", type=Path, required=True)
    parser.add_argument("--prior-production-packet", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        value = capture(build_parser().parse_args(argv))
    except (PostReallocationCapacityError, OSError) as exc:
        code = exc.code if isinstance(exc, PostReallocationCapacityError) else "CAPACITY_IO_ERROR"
        gate_status = (
            exc.gate_evaluation_status
            if isinstance(exc, PostReallocationCapacityError)
            and exc.gate_evaluation_status is not None
            else _not_evaluated_gate_status()
        )
        display_status = (
            exc.pquota_display_crosscheck
            if isinstance(exc, PostReallocationCapacityError)
            and exc.pquota_display_crosscheck is not None
            else GATE_EVALUATION_NOT_EVALUATED
        )
        print(json.dumps({
            "status": "FAIL", "error_code": code,
            "gate_evaluation_status": gate_status,
            "pquota_display_crosscheck": display_status,
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": value["status"],
        "research_quota_gate_passed": value["research_quota_gate_passed"],
        "physical_filesystem_capacity_gate_passed": value["physical_filesystem_capacity_gate_passed"],
        "projected_200gb_reserve_gate_passed": value["projected_200gb_reserve_gate_passed"],
        "research_file_quota_gate_passed": value["research_file_quota_gate_passed"],
        "backed_control_tier_gate_passed": value["backed_control_tier_gate_passed"],
        "gate_evaluation_status": {
            gate: value[field]
            for gate, field in AGGREGATE_GATE_STATUS_FIELDS.items()
        },
        "pquota_display_crosscheck": value["pquota_display_crosscheck"],
        "cloud_requests": 0, "scheduler_jobs_submitted": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
