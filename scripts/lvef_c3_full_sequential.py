#!/usr/bin/env python3
"""Canary-proven sequential reconstruction of the frozen full C3 cohort.

This module is the sole scientific worker for the Phase 1I two-submission
topology.  It projects the already frozen selected-cohort authorities into one
immutable 19-batch plan, runs exactly one batch per SGE array task, and invokes
the cohort finalizer in the held CPU job.  It performs no bucket listing,
model fitting, endpoint prediction, or performance access.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

import capture_lvef_c3_post_reallocation_capacity as capacity
import finalize_lvef_c3_production as finalizer
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement
import validate_lvef_c3_prior_batch_finalization as prior_gate


EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
CONTRACT_PATH = REPOSITORY_ROOT / "configs/lvef_c3_orchestration_v2.yaml"
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
DYNAMIC_CAPACITY_SAFE_EXPORT_POLICY_PATH = (
    REPOSITORY_ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
)
OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / core.OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_FILENAME
)
DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME = (
    "full_dynamic_capacity_source.restricted.json"
)
ARRAY_RUNNER_PATH = SCRIPT_ROOT / "scc_run_lvef_c3_full_sequential.sh"
EXECUTION_NODE_PROBE_RUNNER_PATH = (
    SCRIPT_ROOT / "scc_run_lvef_c3_r8_execution_node_probe.sh"
)
STATE_MACHINE_PATH = REPOSITORY_ROOT / "configs/lvef_c3_state_machine_v2.json"
RESUME_LEDGER_PATH = REPOSITORY_ROOT / "configs/lvef_c3_resume_ledger_v2.json"
TASK_RE = re.compile(r"^(?:[1-9]|1[0-9])$")
JOB_RE = re.compile(r"^[1-9][0-9]{0,19}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ATTEMPT_RE = re.compile(r"^lvef_c3_full_[0-9a-f]{16}_[0-9a-f]{8}$")
GENERIC_PRIVATE_FILE_MAXIMUM_BYTES = 16_000_000
SUBMISSION_RECEIPT_MAXIMUM_BYTES = 4 * 1024 * 1024
QSUB_ENVIRONMENT_SHA256_NAME = "LVEF_C3_QSUB_ENVIRONMENT_SHA256"
TERMINAL_PARTIAL_MAXIMUM_ENTRIES = 50_000
FROZEN_FULL_PLAN_PROJECTED_PEAK_BYTES = 1_611_642_076_332
FROZEN_FULL_PLAN_ORIGINAL_CURRENT_USAGE_BYTES = 152_275_355_648
SUCCESSOR_INCREMENT_BYTES = 1_459_366_720_684
SUCCESSOR_REQUIRED_RESERVE_BYTES = 200_000_000_000
SUCCESSOR_REQUIRED_FILE_SLOTS = 3_500_000
SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES = 2
_DYNAMIC_CAPTURE_REUSE_TOKEN = object()
HISTORICAL_R5E_R2_PRE_ACTION_COMMIT = (
    "f201e22cce760402814be76e25899c0ba10bdfbd"
)
HISTORICAL_R5E_R2_PRE_ACTION_CAPTURED_AT_UTC = "2026-08-18T22:56:28Z"
HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES = 12_267
HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256 = (
    "b5a3340e9968633189f70d00a6593c39296d7b9dce20303bbe97671626b14b5e"
)
HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES = 3_084
HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256 = (
    "72201eedc716aa5bf39da6bda76ea4614c34f3fe4edaa1db3a36454ed2b7ec5e"
)
HISTORICAL_R5E_POST_CLEANUP_COMMIT = (
    "1fc48455492c989fc6bca549f2baa1aa9a82379c"
)
HISTORICAL_R5E_POST_CLEANUP_CAPTURED_AT_UTC = "2026-08-20T18:19:59Z"
HISTORICAL_R5E_POST_CLEANUP_RECEIPT_BYTES = 12_242
HISTORICAL_R5E_POST_CLEANUP_RECEIPT_SHA256 = (
    "a840864bbf37c04c133626346b7e4e5f7f6521d87b36ba83eaf3439261c9e33f"
)
HISTORICAL_R5E_POST_CLEANUP_SUMMARY_BYTES = 3_050
HISTORICAL_R5E_POST_CLEANUP_SUMMARY_SHA256 = (
    "5a8453a5b6a417ac31d427416a7757945a754640d9e5d598416188d7b05298ae"
)
HISTORICAL_R5E_POST_CLEANUP_GAIN_SOURCE = "CLEANUP"
RETIREMENT_EVENT_COMMIT = "ab5670f9db8d7ae06a43f3e5b10aa79e52edfb6b"
RETIREMENT_EVENT_ARTIFACTS = (
    (
        "older_raw_retirement_manifest.restricted.json",
        37_139_326,
        "04f116b644fcf289a6b0e5927e34410d1c2d38cf03debb615038c077656e51bd",
    ),
    (
        "older_raw_retirement_receipt.restricted.json",
        1_557,
        "dd7d4328a5f5cc1507e9029d94a6960e051a4ad6413dc865d8f0b75a08ef1fe1",
    ),
    (
        "older_raw_retirement_summary.aggregate_safe.json",
        1_218,
        "0124213dd3b687165babf554dc7831b255225d051af52dd03351f646922b5b67",
    ),
)
RETIREMENT_EVENT_RECEIPT_SHA256 = RETIREMENT_EVENT_ARTIFACTS[1][2]
RETIREMENT_EVENT_STATUS = "PASS_OLDER_RAW_DUPLICATES_RETIRED"
RETIREMENT_EVENT_DELETED_FILES = 37_068
RETIREMENT_EVENT_DELETED_BYTES = 133_822_359_346
RETIREMENT_EVENT_INTERPRETER_PATH = (
    "scripts/retire_lvef_c3_older_raw_duplicates.py"
)
SUCCESSOR_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "source_capacity_authority_sha256",
        "source_dynamic_receipt_bytes",
        "source_dynamic_receipt_sha256",
        "frozen_projected_peak_bytes",
        "frozen_original_current_usage_bytes",
        "successor_increment_bytes",
        "live_research_usage_bytes",
        "projected_total_research_usage_bytes",
        "research_quota_bytes",
        "quota_remaining_after_successor_bytes",
        "research_filesystem_available_bytes",
        "physical_remaining_after_successor_bytes",
        "research_file_slots_remaining",
        "required_reserve_bytes",
        "required_remaining_file_slots",
        "active_extraction_caches",
        "preserved_terminal_failed_extraction_caches",
        "quota_reserve_gate_passed",
        "physical_reserve_gate_passed",
        "file_slot_gate_passed",
        "terminal_failure_cache_gate_passed",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "dicom_body_reads",
        "writes_performed",
    }
)

TERMINAL_DOWNLOAD_FAILURE_SUMMARY_KEYS = frozenset(
    {"status", "identifiers_emitted", "paths_emitted"}
)
TERMINAL_DOWNLOAD_FAILURE_STATUSES = frozenset(
    {"FAIL_DOWNLOAD_FILE_NOT_REGULAR", "FAIL_POSTDOWNLOAD_HASH_MISMATCH"}
)
TERMINAL_DICOM_FAILURE_SUMMARY_KEYS = frozenset(
    {
        "status",
        "n_objects",
        "n_studies",
        "n_readable",
        "n_unreadable",
        "n_multiframe_candidates",
        "n_single_frame",
        "n_pixel_decode_failures",
        "physical_source_keys_unique",
        "identifiers_emitted",
        "paths_emitted",
    }
)
TERMINAL_EXTRACTION_FAILURE_SUMMARY_KEYS = frozenset(
    {"status", "error_code", "identifiers_emitted", "paths_emitted"}
)
TERMINAL_EXTRACTION_FAILURE_SUMMARY_V2_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "error_code",
        "extraction_provenance",
        "identifiers_emitted",
        "paths_emitted",
    }
)
TERMINAL_DICOM_FAILURE_SUMMARY_V2_KEYS = frozenset(
    {*TERMINAL_DICOM_FAILURE_SUMMARY_KEYS, "schema_version", "artifact_type",
     "extraction_provenance"}
)
EXTRACTION_PROVENANCE_INTEGER_KEYS = frozenset(
    {
        "n_requested_cines",
        "n_extracted_cines",
        "n_failed_cines",
        "n_unique_clip_keys",
        "n_duplicate_clip_key_rows",
        "n_studies_with_extracted_cine",
        "n_source_nonempty_sector_gate_passed",
        "n_source_nonzero_retained_pixel_gate_passed",
        "n_source_temporal_variation_gate_passed",
        "n_ordinary_post_crop_nonzero_retained_pixel_gate_passed",
        "n_ordinary_post_crop_temporal_variation_gate_passed",
        "n_post_crop_nonzero_retained_pixel_gate_passed",
        "n_post_crop_temporal_variation_gate_passed",
        "n_ordinary_sampled_nonzero_retained_pixel_gate_passed",
        "n_ordinary_sampled_temporal_variation_gate_passed",
        "n_sampled_nonzero_retained_pixel_gate_passed",
        "n_sampled_temporal_variation_gate_passed",
        "n_encoder_visible_nonzero_retained_pixel_gate_passed",
        "n_encoder_visible_temporal_variation_gate_passed",
    }
)
EXTRACTION_PROVENANCE_BOOLEAN_KEYS = frozenset(
    {
        "all_shapes_32x224x224x3_uint8",
        "all_masks_explicitly_applied",
        "all_preprocessing_signal_gates_passed",
        "all_successful_gate_counts_mechanically_consistent",
        "all_fallback_encoder_visible_signal_gates_passed",
        "selected_preprocessing_paths_valid",
        "preprocessing_path_trigger_consistent",
        "temporal_sampling_policy_locked_for_all_extracted_cines",
        "all_decoders_stored_color_raw_to_canonical_rgb",
        "row_values_emitted",
        "paths_emitted",
    }
)
EXTRACTION_PROVENANCE_COUNT_MAP_KEYS = frozenset(
    {
        "selected_preprocessing_path_counts",
        "fallback_status_counts",
        "failure_substage_counts",
        "decode_color_status_counts",
        "temporal_sampling_policy_counts",
        "photometric_interpretation_counts",
        "transfer_syntax_uid_counts",
        "decoder_backend_counts",
        "decoder_color_behavior_counts",
        "color_transform_counts",
    }
)
EXTRACTION_PROVENANCE_GATE_STATE_KEYS = frozenset(
    {
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed",
        "ordinary_post_crop_temporal_variation_gate_passed",
        "post_crop_nonzero_retained_pixel_gate_passed",
        "post_crop_temporal_variation_gate_passed",
        "ordinary_sampled_nonzero_retained_pixel_gate_passed",
        "ordinary_sampled_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_gate_passed",
        "encoder_visible_nonzero_retained_pixel_gate_passed",
        "encoder_visible_temporal_variation_gate_passed",
    }
)
EXTRACTION_PROVENANCE_KEYS = frozenset(
    {
        "audit",
        "status",
        *EXTRACTION_PROVENANCE_INTEGER_KEYS,
        *EXTRACTION_PROVENANCE_BOOLEAN_KEYS,
        *EXTRACTION_PROVENANCE_COUNT_MAP_KEYS,
        "preprocessing_gate_state_counts",
        "temporal_sampling_policy",
        "temporal_fallback_policy",
        "temporal_sampling_long_cine_rule",
        "temporal_sampling_short_cine_rule",
    }
)

COMPLETED_CANARY_RUN_ID = "lvef_c3_minimal_5907a1ac53b05036_e5ca24c4"
COMPLETED_CANARY_PRIVATE_TOPOLOGY = (
    ("minimal_canary_runs",),
    ("minimal_canary_runs", COMPLETED_CANARY_RUN_ID),
    ("minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts"),
    (
        "minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts",
        COMPLETED_CANARY_RUN_ID,
    ),
    (
        "minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts",
        COMPLETED_CANARY_RUN_ID, "batches",
    ),
    (
        "minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts",
        COMPLETED_CANARY_RUN_ID, "batches", "c3_batch_000",
    ),
    (
        "minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts",
        COMPLETED_CANARY_RUN_ID, "batches", "c3_batch_000", "preservation",
    ),
    ("owner_private",),
    ("owner_private", f"preservation_recovery_{COMPLETED_CANARY_RUN_ID}"),
    (
        "owner_private", f"preservation_recovery_{COMPLETED_CANARY_RUN_ID}",
        "submission_attempt_002",
    ),
)
COMPLETED_CANARY_EVIDENCE_AUTHORITIES = (
    (
        (
            "minimal_canary_runs", COMPLETED_CANARY_RUN_ID, "attempts",
            COMPLETED_CANARY_RUN_ID, "batches", "c3_batch_000", "preservation",
            "batch_preservation_receipt.restricted.json",
        ),
        3_814,
        "5554fcdc92ab3a92d4e6159faf790a73133327bfcccaaa50f747f709314c324d",
    ),
    (
        (
            "minimal_canary_runs", COMPLETED_CANARY_RUN_ID,
            "minimal_canary_finalization_receipt.aggregate_safe.json",
        ),
        1_414,
        "a8c124d35673c91349803b62efd05ccc6902c4a06449273947e3ac88ebd6a5b9",
    ),
    (
        (
            "owner_private", f"preservation_recovery_{COMPLETED_CANARY_RUN_ID}",
            "submission_attempt_002",
            "preservation_recovery_terminal.aggregate_safe.json",
        ),
        1_736,
        "0da8a518cece63cdafb0b9e4e7149fcf1cee0ba95f2953cff0b89ea0d9722529",
    ),
)

FULL_SUBMISSION_CLAIM_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "governing_commit",
        "attempt_id", "batch_plan_sha256", "plan_authority_sha256",
        "runtime_authority_sha256", "launch_authority_sha256",
        "capacity_receipt_sha256", "qsub_environment_sha256",
        "maximum_qsub_submissions", "array_tasks",
        "array_max_concurrency", "automatic_resubmission",
        "whole_batch_retry_authorized", "third_scheduler_submission_reachable",
        "raw_dicom_deletion_authorized", "bucket_listing_requests_before_claim",
        "cloud_requests_before_claim", "qsub_submissions_before_claim",
        "dicom_body_reads_before_claim", "gpu_executions_before_claim",
        "model_fitting_before_claim", "prediction_generation_before_claim",
        "confirmatory_performance_access_before_claim",
    }
)

# Historical R3E/R4D2C replay imports ``FULL_SUBMISSION_CLAIM_KEYS`` directly;
# that frozen v1 contract therefore remains byte-for-byte stable.  Only a
# newly materialized full successor may use this distinct v2 claim schema.
FRESH_FULL_SUBMISSION_CLAIM_V2_KEYS = frozenset(
    {*FULL_SUBMISSION_CLAIM_KEYS, "dynamic_capacity_receipt_sha256"}
)
FRESH_FULL_SUBMISSION_CLAIM_V3_KEYS = frozenset(
    {
        *FRESH_FULL_SUBMISSION_CLAIM_V2_KEYS,
        "capacity_evidence_role",
        "capacity_gain_source",
        "raw_retirement_status",
        "raw_retirement_receipt_sha256",
    }
)

ORDERED_STAGES = (
    "DOWNLOAD",
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "CACHE_RETIREMENT_ELIGIBILITY",
    "CACHE_RETIREMENT",
    "BATCH_FINALIZATION",
)


class FullSequentialError(RuntimeError):
    def __init__(self, code: str, *, stage: str | None = None):
        super().__init__(code)
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class FullRun:
    authority: minimal.LiveAuthority
    plan: Mapping[str, Any]
    requirements: core.PlanRequirements
    contract: Mapping[str, Any]
    contract_path: Path
    plan_sha256: str
    runtime_authority: Mapping[str, str]
    attempt_id: str
    production_root: Path
    attempt_root: Path
    plan_path: Path
    launch_authority: Mapping[str, Any]
    launch_authority_sha256: str
    scheduler_job_identity: str


@dataclass(frozen=True)
class ExtractionCacheInventory:
    active: int
    preserved_terminal_failed: int


@dataclass(frozen=True)
class CapacityAdmission:
    capture: capacity.DynamicSuccessorCapacityCapture
    evidence_role: str
    capacity_gain_source: str
    raw_retirement_status: str
    raw_retirement_receipt_sha256: str


@dataclass(frozen=True)
class HistoricalCapacityEvent:
    capture: capacity.DynamicSuccessorCapacityCapture
    authority: Mapping[str, Any]


class FullExecutionContext(Enum):
    """Closed scheduler context; the ordinary full launch remains the default."""

    ORIGINAL_FULL_SUBMISSION = "ORIGINAL_FULL_SUBMISSION"
    R8R_FIXED_CONTINUATION = "R8R_FIXED_CONTINUATION"
    R8U_R2_FIXED_CONTINUATION = "R8U_R2_FIXED_CONTINUATION"


ORIGINAL_FULL_SUBMISSION = FullExecutionContext.ORIGINAL_FULL_SUBMISSION
R8R_FIXED_CONTINUATION = FullExecutionContext.R8R_FIXED_CONTINUATION
R8U_R2_FIXED_CONTINUATION = FullExecutionContext.R8U_R2_FIXED_CONTINUATION
# Compatibility name used by the fixed controller; it resolves only to the
# fresh R2 continuation context and does not make the consumed R1 epoch live.
R8U_FIXED_CONTINUATION = R8U_R2_FIXED_CONTINUATION


@dataclass(frozen=True)
class FullDependencies:
    prior_batch_validator: Callable[..., Any] | None = None
    download: Callable[..., Mapping[str, Any]] | None = None
    dicom: Callable[..., Mapping[str, Any]] | None = None
    echoprime: Callable[..., Mapping[str, Any]] | None = None
    preserve: Callable[..., Mapping[str, Any]] | None = None
    retire: Callable[..., Mapping[str, Any]] | None = None
    finalize_batch: Callable[..., Mapping[str, Any]] | None = None
    cross_batch_finalize: Callable[..., Mapping[str, Any]] | None = None
    token_provider_factory: Callable[[minimal.LiveAuthority], Any] | None = None
    transport_factory: Callable[[], Any] | None = None
    digest_provider_factory: Callable[[minimal.LiveAuthority], Any] | None = None
    capacity_probe: Callable[
        [], capacity.DynamicSuccessorCapacityCapture
    ] | None = None
    environment_validator: Callable[..., Mapping[str, Any]] | None = None
    test_only_synthetic_full_scope: bool = False
    extraction_workers: int = 4
    echoprime_batch_size: int = 8
    monotonic_clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    execution_context: FullExecutionContext = ORIGINAL_FULL_SUBMISSION


def _fail(code: str) -> None:
    raise FullSequentialError(code)


@contextmanager
def _stage_boundary(stage: str) -> Iterator[None]:
    """Preserve a closed substantive code and its aggregate-safe stage."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", stage):
        _fail("FULL_SEQUENTIAL_INTERNAL_STAGE_INVALID")
    try:
        yield
    except FullSequentialError as exc:
        if exc.stage is None:
            exc.stage = stage
        raise
    except Exception as exc:
        observed = getattr(exc, "code", "")
        code = (
            str(observed)
            if isinstance(observed, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", observed)
            else "UNEXPECTED_SANITIZED_STAGE_FAILURE"
        )
        raise FullSequentialError(code, stage=stage) from exc


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        },
    )
    if result.returncode:
        _fail("FULL_SEQUENTIAL_GIT_AUTHORITY_UNAVAILABLE")
    return result.stdout.strip()


def _current_commit(*, require_clean: bool = True) -> str:
    head = _git("rev-parse", "HEAD")
    origin = _git(
        "rev-parse", "refs/remotes/origin/codex/lvef-multitask-revalidation"
    )
    if (
        _git("branch", "--show-current") != EXPECTED_BRANCH
        or head != origin
        or COMMIT_RE.fullmatch(head) is None
        or (
            require_clean
            and _git("status", "--porcelain", "--untracked-files=no")
        )
    ):
        _fail("FULL_SEQUENTIAL_GIT_AUTHORITY_MISMATCH")
    return head


def _production_functions() -> dict[str, Callable[..., Any]]:
    return {
        "prior_batch_validator": prior_gate.validate_prior_batch,
        "download": core.execute_exact_batch_download,
        "dicom": stages.run_production_dicom_extraction,
        "echoprime": stages.run_production_echoprime,
        "preserve": preservation.preserve_batch,
        "retire": _execute_cache_retirement,
        "finalize_batch": _validate_batch_finalization,
        "cross_batch_finalize": finalizer.finalize_receipts,
    }


def resolve_dependencies(value: FullDependencies | None = None) -> FullDependencies:
    source = value or FullDependencies()
    if (
        isinstance(source.extraction_workers, bool)
        or source.extraction_workers < 1
        or isinstance(source.echoprime_batch_size, bool)
        or source.echoprime_batch_size < 1
        or not isinstance(source.execution_context, FullExecutionContext)
    ):
        _fail("FULL_SEQUENTIAL_DEPENDENCY_CONFIGURATION_INVALID")
    functions = _production_functions()
    return FullDependencies(
        prior_batch_validator=(
            source.prior_batch_validator or functions["prior_batch_validator"]
        ),
        download=source.download or functions["download"],
        dicom=source.dicom or functions["dicom"],
        echoprime=source.echoprime or functions["echoprime"],
        preserve=source.preserve or functions["preserve"],
        retire=source.retire or functions["retire"],
        finalize_batch=source.finalize_batch or functions["finalize_batch"],
        cross_batch_finalize=(
            source.cross_batch_finalize or functions["cross_batch_finalize"]
        ),
        token_provider_factory=source.token_provider_factory,
        transport_factory=source.transport_factory,
        digest_provider_factory=source.digest_provider_factory,
        capacity_probe=source.capacity_probe,
        environment_validator=(
            source.environment_validator
            or stages.validate_environment_authority_for_scientific_commit
        ),
        test_only_synthetic_full_scope=source.test_only_synthetic_full_scope,
        extraction_workers=source.extraction_workers,
        echoprime_batch_size=source.echoprime_batch_size,
        monotonic_clock=source.monotonic_clock,
        sleeper=source.sleeper,
        execution_context=source.execution_context,
    )


def task_to_batch(task_id: int, plan: Mapping[str, Any]) -> str:
    batches = plan.get("batches")
    if (
        isinstance(task_id, bool)
        or not isinstance(task_id, int)
        or task_id < 1
        or not isinstance(batches, list)
        or task_id > len(batches)
    ):
        _fail("FULL_SEQUENTIAL_TASK_ID_INVALID")
    if any(
        not isinstance(row, Mapping)
        or row.get("ordinal") != index
        or row.get("batch_id") != f"c3_batch_{index:03d}"
        for index, row in enumerate(batches)
    ):
        _fail("FULL_SEQUENTIAL_PLAN_ORDER_INVALID")
    expected = f"c3_batch_{task_id - 1:03d}"
    if batches[task_id - 1].get("batch_id") != expected:
        _fail("FULL_SEQUENTIAL_PLAN_TASK_MAPPING_INVALID")
    return expected


def _read_bound_rows(authority: minimal.LiveAuthority) -> tuple[
    list[dict[str, str]],
    list[dict[str, Any]],
    list[dict[str, str]],
    str,
    list[dict[str, str]],
]:
    selected_payload = minimal._read_regular(authority.selected_studies)
    source_payload = minimal._read_regular(authority.selected_source, private=True)
    metadata_payload = minimal._read_regular(authority.source_metadata, private=True)
    split_payload = minimal._read_regular(authority.split_map)
    historical_payload = minimal._bound_payload(
        minimal.HISTORICAL_STUDY_MANIFEST_PATH,
        expected_sha256=minimal.HISTORICAL_STUDY_MANIFEST_SHA256,
        private=False,
    )
    if (
        minimal._sha256_bytes(selected_payload)
        != core.EXPECTED_SELECTED_MANIFEST_SHA256
        or minimal._sha256_bytes(source_payload)
        != core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        or minimal._sha256_bytes(split_payload) != core.EXPECTED_SPLIT_MAP_SHA256
    ):
        _fail("FULL_SEQUENTIAL_FROZEN_ROW_AUTHORITY_MISMATCH")
    selected = minimal._strict_csv_rows(selected_payload)
    selected_source = minimal._strict_csv_rows(source_payload)
    metadata = minimal._strict_jsonl_rows(metadata_payload)
    split = minimal._strict_csv_rows(split_payload)
    historical = minimal._strict_csv_rows(historical_payload)
    try:
        normalized = core.reconcile_selected_source_metadata(
            selected_source, metadata, release="mimic-iv-echo/1.0"
        )
    except core.OrchestrationError as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_SOURCE_METADATA_RECONCILIATION_FAILED"
        ) from exc
    selected_ownership: dict[str, str] = {}
    selected_pairs: set[tuple[str, str]] = set()
    for row in selected:
        subject = str(row.get("subject_id", ""))
        study = str(row.get("study_id", ""))
        if (
            not subject.isdigit()
            or not study.isdigit()
            or str(int(subject)) != subject
            or str(int(study)) != study
            or study in selected_ownership
        ):
            _fail("FULL_SEQUENTIAL_PRESPECIFIED_NO_CINE_AUTHORITY_INVALID")
        selected_ownership[study] = subject
        selected_pairs.add((subject, study))
    historical_selected_pairs: set[tuple[str, str]] = set()
    for row in historical:
        subject = str(row.get("subject_id", ""))
        study = str(row.get("study_id", ""))
        owner = selected_ownership.get(study)
        if owner is None:
            continue
        if owner != subject:
            _fail("FULL_SEQUENTIAL_PRESPECIFIED_NO_CINE_AUTHORITY_INVALID")
        historical_selected_pairs.add((subject, study))
    no_cine_pairs = sorted(
        selected_pairs - historical_selected_pairs,
        key=lambda pair: (int(pair[0]), int(pair[1])),
    )
    if len(no_cine_pairs) != 5:
        _fail("FULL_SEQUENTIAL_PRESPECIFIED_NO_CINE_AUTHORITY_INVALID")
    no_cine_keys = [
        {"subject_id": subject, "study_id": study}
        for subject, study in no_cine_pairs
    ]
    return (
        selected,
        normalized,
        split,
        minimal._sha256_bytes(metadata_payload),
        no_cine_keys,
    )


def _build_frozen_plan(
    authority: minimal.LiveAuthority,
) -> tuple[dict[str, Any], core.PlanRequirements, Mapping[str, Any]]:
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    requirements = core.production_requirements(contract)
    if requirements != core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=4530,
        selected_subjects=4530,
        normalized_source_objects=335984,
        selected_source_bytes=1216569133322,
        batch_count=19,
        studies_per_full_batch=250,
        final_batch_studies=30,
        contract_id="lvef_multitask_c3_production_orchestration_v2",
    ):
        _fail("FULL_SEQUENTIAL_FROZEN_REQUIREMENTS_CHANGED")
    selected, normalized, split, metadata_sha, no_cine_keys = _read_bound_rows(
        authority
    )
    for path, expected, code in (
        (CONTRACT_PATH, core.sha256_file(CONTRACT_PATH), "CONTRACT"),
        (STATE_MACHINE_PATH, str(contract["authority"]["state_machine_schema_sha256"]), "STATE"),
        (RESUME_LEDGER_PATH, str(contract["authority"]["resume_ledger_schema_sha256"]), "LEDGER"),
        (authority.checkpoint, str(contract["embedding"]["checkpoint_sha256"]), "CHECKPOINT"),
        (authority.environment_receipt, authority.environment_receipt_sha256, "ENVIRONMENT"),
    ):
        if core.sha256_file(path) != expected:
            _fail(f"FULL_SEQUENTIAL_{code}_AUTHORITY_MISMATCH")
    plan_authority = {
        "git_commit": authority.governing_commit,
        "orchestration_contract_sha256": core.sha256_file(CONTRACT_PATH),
        "selected_manifest_sha256": core.EXPECTED_SELECTED_MANIFEST_SHA256,
        "selected_source_manifest_sha256": (
            core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        ),
        "source_metadata_sha256": metadata_sha,
        "split_map_sha256": core.EXPECTED_SPLIT_MAP_SHA256,
        "checkpoint_sha256": core.EXPECTED_CHECKPOINT_SHA256,
        "environment_receipt_sha256": authority.environment_receipt_sha256,
        "state_machine_schema_sha256": str(
            contract["authority"]["state_machine_schema_sha256"]
        ),
        "resume_ledger_schema_sha256": str(
            contract["authority"]["resume_ledger_schema_sha256"]
        ),
        "gcloud_resolution_receipt_sha256": authority.gcloud_receipt_sha256,
        "gcloud_executable_sha256": authority.gcloud_sha256,
        "crc32c_python_executable_sha256": authority.crc32c_python_sha256,
        "crc32c_worker_sha256": authority.crc32c_worker_sha256,
        "crc32c_distribution_sha256": authority.crc32c_distribution_sha256,
    }
    source_rows = [
        {
            "release_id": requirements.release,
            "subject_id": str(row["subject_id"]),
            "study_id": str(row["study_id"]),
            "split": str(row["split"]),
            "production_batch": str(row["production_batch"]),
            "source_relative_path": str(row["source_relative_path"]),
            "source_object_key": str(row["source_object_key"]),
            "remote_size_bytes": row["remote_size_bytes"],
            "remote_generation": row["remote_generation"],
            "remote_md5_base64": row["remote_md5_base64"],
            "remote_crc32c_base64": row["remote_crc32c_base64"],
        }
        for row in normalized
    ]
    plan = core.build_immutable_batch_plan(
        selected,
        source_rows,
        split,
        requirements=requirements,
        authority=plan_authority,
        prespecified_no_cine_studies=no_cine_keys,
    )
    if (
        plan["cohort"]["expected_no_cine_studies"] != 5
        or plan["cohort"]["prespecified_no_cine_study_set_sha256"]
        != core.canonical_json_sha256(no_cine_keys)
    ):
        _fail("FULL_SEQUENTIAL_PRESPECIFIED_NO_CINE_AUTHORITY_INVALID")
    core.validate_plan_authority_against_contract(
        plan["authority"], contract=contract, contract_path=CONTRACT_PATH
    )
    return plan, requirements, contract


def build_full_run(
    *,
    authority: minimal.LiveAuthority | None = None,
    scheduler_job_identity: str = "NO_BODY",
    production_root: Path = PRODUCTION_ROOT,
) -> FullRun:
    current = authority or minimal.discover_live_authority()
    if current.governing_commit != _current_commit():
        _fail("FULL_SEQUENTIAL_GOVERNING_COMMIT_MISMATCH")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", scheduler_job_identity):
        _fail("FULL_SEQUENTIAL_SCHEDULER_IDENTITY_INVALID")
    plan, requirements, contract = _build_frozen_plan(current)
    plan_sha = core.validate_current_batch_plan_v3(
        plan, requirements=requirements
    )
    attempt_id = f"lvef_c3_full_{plan_sha[:16]}_{current.governing_commit[:8]}"
    if ATTEMPT_RE.fullmatch(attempt_id) is None:
        _fail("FULL_SEQUENTIAL_ATTEMPT_ID_INVALID")
    runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha}
    )
    launch = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_full_selected_cohort_launch_authority_v2",
        "status": "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "governing_commit": current.governing_commit,
        "batch_plan_sha256": plan_sha,
        "selected_manifest_sha256": core.EXPECTED_SELECTED_MANIFEST_SHA256,
        "selected_source_manifest_sha256": (
            core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        ),
        "split_map_sha256": core.EXPECTED_SPLIT_MAP_SHA256,
        "checkpoint_sha256": core.EXPECTED_CHECKPOINT_SHA256,
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "normalized_source_objects": 335984,
        "selected_source_bytes": 1216569133322,
        "batch_count": 19,
        "expected_no_cine_studies": 5,
        "prespecified_no_cine_study_set_sha256": plan["cohort"][
            "prespecified_no_cine_study_set_sha256"
        ],
        "maximum_scheduler_submissions": 2,
        "array_task_range": "1-19",
        "array_max_concurrency": 1,
        "raw_dicom_deletion_authorized": False,
        "extracted_cache_retirement_authorized_after_preservation": True,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
    launch_sha = core.canonical_json_sha256(launch)
    attempt_root = production_root / "attempts" / attempt_id
    return FullRun(
        authority=current,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=CONTRACT_PATH,
        plan_sha256=plan_sha,
        runtime_authority=runtime,
        attempt_id=attempt_id,
        production_root=production_root,
        attempt_root=attempt_root,
        plan_path=attempt_root / "full_batch_plan.restricted.json",
        launch_authority=launch,
        launch_authority_sha256=launch_sha,
        scheduler_job_identity=scheduler_job_identity,
    )


def _validate_private_directory(path: Path) -> None:
    _require_nonsymlink_components(path)
    try:
        item = os.lstat(path)
    except OSError as exc:
        raise FullSequentialError("FULL_SEQUENTIAL_PRIVATE_DIRECTORY_INVALID") from exc
    mode = stat.S_IMODE(item.st_mode)
    if (
        stat.S_ISLNK(item.st_mode)
        or not stat.S_ISDIR(item.st_mode)
        or item.st_uid != os.geteuid()
        or mode & 0o700 != 0o700
        or mode & 0o077
        or mode & 0o7000 not in {0, stat.S_ISGID}
    ):
        _fail("FULL_SEQUENTIAL_PRIVATE_DIRECTORY_INVALID")


def _require_nonsymlink_components(path: Path) -> None:
    """Reject noncanonical paths and every existing symlink component."""

    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _fail("FULL_SEQUENTIAL_PATH_NOT_CANONICAL")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if os.path.lexists(cursor) and stat.S_ISLNK(os.lstat(cursor).st_mode):
            _fail("FULL_SEQUENTIAL_PATH_SYMLINK")


def _read_owner_private_regular(
    path: Path,
    *,
    maximum_bytes: int = GENERIC_PRIVATE_FILE_MAXIMUM_BYTES,
    exact_bytes: int | None = None,
    size_mismatch_code: str = "FULL_SEQUENTIAL_PRIVATE_FILE_INVALID",
) -> bytes:
    """Read one bounded owner-private file through a stable no-follow fd."""

    if (
        not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or maximum_bytes < 1
        or (
            exact_bytes is not None
            and (
                not isinstance(exact_bytes, int)
                or isinstance(exact_bytes, bool)
                or exact_bytes < 1
                or exact_bytes > maximum_bytes
                or not re.fullmatch(
                    r"[A-Z][A-Z0-9_]{1,127}", size_mismatch_code
                )
            )
        )
    ):
        _fail("FULL_SEQUENTIAL_PRIVATE_READ_CONTRACT_INVALID")

    _require_nonsymlink_components(path)
    try:
        before = os.lstat(path)
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_PRIVATE_FILE_INVALID"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (before.st_size, before.st_mtime_ns)
            != (opened.st_size, opened.st_mtime_ns)
        ):
            _fail("FULL_SEQUENTIAL_PRIVATE_FILE_INVALID")
        if exact_bytes is not None:
            if before.st_size != exact_bytes:
                _fail(size_mismatch_code)
        elif before.st_size < 1 or before.st_size > maximum_bytes:
            _fail("FULL_SEQUENTIAL_PRIVATE_FILE_INVALID")
        blocks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1_048_576))
            if not block:
                _fail("FULL_SEQUENTIAL_PRIVATE_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            _fail("FULL_SEQUENTIAL_PRIVATE_FILE_INVALID")
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def _strict_json_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("FULL_SEQUENTIAL_PRIVATE_JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _load_owner_private_json(path: Path) -> tuple[Mapping[str, Any], bytes]:
    payload = _read_owner_private_regular(path)
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_json_pairs
        )
    except FullSequentialError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_PRIVATE_JSON_INVALID"
        ) from exc
    if not isinstance(value, Mapping):
        _fail("FULL_SEQUENTIAL_PRIVATE_JSON_INVALID")
    return value, payload


def _derive_full_batch_plan_read_contract(run: FullRun) -> tuple[int, str]:
    """Derive the one exact materialized-plan bound from canonical bytes."""

    canonical_payload = core.canonical_json_bytes(run.plan)
    try:
        expected_bytes = len(canonical_payload)
        expected_sha256 = hashlib.sha256(canonical_payload).hexdigest()
    finally:
        # Do not retain the 100+ MB producer serialization while reading and
        # parsing the independent on-disk consumer copy.
        del canonical_payload
    if (
        expected_bytes < 1
        or SHA_RE.fullmatch(expected_sha256) is None
        or expected_sha256 != run.plan_sha256
    ):
        _fail("FULL_SEQUENTIAL_RUN_AUTHORITY_INVALID")
    return expected_bytes, expected_sha256


def _load_full_batch_plan_payload(
    path: Path, *, expected_bytes: int, expected_sha256: str
) -> Mapping[str, Any]:
    """Read and parse one plan under an already-derived exact contract."""

    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 1
        or SHA_RE.fullmatch(expected_sha256) is None
    ):
        _fail("FULL_SEQUENTIAL_RUN_AUTHORITY_INVALID")
    try:
        payload = _read_owner_private_regular(
            path,
            maximum_bytes=expected_bytes,
            exact_bytes=expected_bytes,
            size_mismatch_code="FULL_SEQUENTIAL_BATCH_PLAN_SIZE_MISMATCH",
        )
    except FullSequentialError as exc:
        if exc.code == "FULL_SEQUENTIAL_BATCH_PLAN_SIZE_MISMATCH":
            raise
        raise FullSequentialError(
            "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"
        ) from exc

    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if observed_sha256 != expected_sha256:
        del payload
        _fail("FULL_SEQUENTIAL_BATCH_PLAN_SHA256_MISMATCH")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_json_pairs
        )
    except (FullSequentialError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_BATCH_PLAN_JSON_INVALID"
        ) from exc
    finally:
        del payload
    if not isinstance(value, Mapping):
        _fail("FULL_SEQUENTIAL_BATCH_PLAN_JSON_INVALID")
    return value


def _load_full_batch_plan(run: FullRun) -> Mapping[str, Any]:
    """Read FULL_BATCH_PLAN under its dynamic canonical-size/SHA contract."""

    expected_bytes, expected_sha256 = _derive_full_batch_plan_read_contract(run)
    value = _load_full_batch_plan_payload(
        run.plan_path,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
    )
    if value != run.plan:
        _fail("FULL_SEQUENTIAL_PREPARED_AUTHORITY_MISMATCH")
    return value


def _validate_completed_canary_evidence() -> Mapping[str, str]:
    """Revalidate the fixed completed canary and recovery witness read-only."""

    for relative in ((), *COMPLETED_CANARY_PRIVATE_TOPOLOGY):
        path = PRODUCTION_ROOT.joinpath(*relative)
        try:
            _validate_private_directory(path)
        except FullSequentialError as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_CANARY_TOPOLOGY_DRIFT"
            ) from exc
    observed: dict[str, str] = {}
    for relative, expected_bytes, expected_sha256 in (
        COMPLETED_CANARY_EVIDENCE_AUTHORITIES
    ):
        path = PRODUCTION_ROOT.joinpath(*relative)
        try:
            payload = _read_owner_private_regular(
                path, maximum_bytes=expected_bytes
            )
        except FullSequentialError as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT"
            ) from exc
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_bytes or digest != expected_sha256:
            _fail("FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT")
        observed[path.name] = digest
    return dict(sorted(observed.items()))


def _ensure_private_directory(path: Path, *, parents: bool = False) -> None:
    _require_nonsymlink_components(path)
    if os.path.lexists(path):
        _validate_private_directory(path)
        return
    if parents:
        missing: list[Path] = []
        cursor = path
        while not os.path.lexists(cursor):
            missing.append(cursor)
            cursor = cursor.parent
        if cursor.is_symlink() or not cursor.is_dir():
            _fail("FULL_SEQUENTIAL_PRIVATE_PARENT_INVALID")
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            _validate_private_directory(directory)
    else:
        if path.parent.is_symlink() or not path.parent.is_dir():
            _fail("FULL_SEQUENTIAL_PRIVATE_PARENT_INVALID")
        path.mkdir(mode=0o700)
        _validate_private_directory(path)


def _write_private_json(path: Path, value: Mapping[str, Any], *, attempt_id: str) -> str:
    return core.atomic_write_json_no_clobber(path, value, attempt_id=attempt_id)


def _validate_full_run(run: FullRun) -> None:
    if (
        not isinstance(run, FullRun)
        or ATTEMPT_RE.fullmatch(run.attempt_id) is None
        or run.attempt_root
        != run.production_root / "attempts" / run.attempt_id
        or run.plan_path
        != run.attempt_root / "full_batch_plan.restricted.json"
        or core.validate_current_batch_plan_v3(
            run.plan, requirements=run.requirements
        )
        != run.plan_sha256
        or core.canonical_json_sha256(run.launch_authority)
        != run.launch_authority_sha256
        or set(run.launch_authority) != core.DIRECT_FULL_LAUNCH_KEYS
        or run.launch_authority.get("schema_version") != 2
        or run.launch_authority.get("artifact_type")
        != "lvef_c3_full_selected_cohort_launch_authority_v2"
        or run.launch_authority.get("prespecified_no_cine_study_set_sha256")
        != run.plan["cohort"]["prespecified_no_cine_study_set_sha256"]
        or core.validate_runtime_authority(run.runtime_authority)
        != dict(sorted(run.runtime_authority.items()))
    ):
        _fail("FULL_SEQUENTIAL_RUN_AUTHORITY_INVALID")


def _batch_paths(run: FullRun, batch_id: str) -> dict[str, Path]:
    raw_batch = run.attempt_root / "raw" / batch_id
    batch_root = run.attempt_root / "batches" / batch_id
    extraction = (
        run.attempt_root / "extracted_cache" / batch_id / "dicom_extraction"
    )
    preservation_root = batch_root / "preservation"
    return {
        "raw_root": run.attempt_root / "raw",
        "raw_batch": raw_batch,
        "batch_root": batch_root,
        "extraction_batch_root": extraction.parent,
        "extraction": extraction,
        "echoprime": batch_root / "echoprime",
        "preservation": preservation_root,
        "download_ledger": batch_root / "download_resume_ledger.restricted.json",
        "extraction_ledger": batch_root / "extraction_resume_ledger.restricted.json",
        "pooling_ledger": batch_root / "pooling_resume_ledger.restricted.json",
        "eligibility_ledger": batch_root / "cache_retirement_eligible_resume_ledger.restricted.json",
        "final_ledger": batch_root / "final_resume_ledger.restricted.json",
        "final_receipt": preservation_root / "batch_finalization_receipt.restricted.json",
        "final_transition": preservation_root / "cache_retirement_finalized.restricted.json",
    }


def _prior_batch_kwargs(run: FullRun, batch_id: str) -> dict[str, Any]:
    previous_id = f"c3_batch_{int(batch_id.rsplit('_', 1)[1]) - 1:03d}"
    previous = _batch_paths(run, previous_id)
    return {
        "current_batch_id": batch_id,
        "attempt_id": run.attempt_id,
        "governing_commit": run.authority.governing_commit,
        "contract_path": run.contract_path,
        "plan_path": run.plan_path,
        "environment_receipt": run.authority.environment_receipt,
        "final_receipt_path": previous["final_receipt"],
        "final_ledger_path": previous["final_ledger"],
        "transition_path": previous["final_transition"],
        "requirements": run.requirements,
        "expected_runtime_authority": run.runtime_authority,
    }


def _wait_for_submission_receipt(
    run: FullRun,
    *,
    current_job_id: str,
    role: str,
    monotonic_clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Mapping[str, Any]:
    if role not in {"array", "finalizer"}:
        _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID")
    if (
        not isinstance(current_job_id, str)
        or JOB_RE.fullmatch(current_job_id) is None
    ):
        _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH")
    path = run.attempt_root / "scheduler/submission_receipt.restricted.json"
    deadline = monotonic_clock() + 60.0
    while not os.path.lexists(path):
        if monotonic_clock() >= deadline:
            _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_TIMEOUT")
        sleeper(0.25)

    receipt = _load_submission_receipt(path)
    import lvef_c3_full_scheduler as scheduler

    if set(receipt) != scheduler.SUBMISSION_RECEIPT_KEYS:
        _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_SCHEMA_INVALID")
    expected_job = receipt.get(
        "array_job_id" if role == "array" else "finalizer_job_id"
    )
    if (
        not isinstance(expected_job, str)
        or JOB_RE.fullmatch(expected_job) is None
        or expected_job != current_job_id
    ):
        _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH")
    for key in ("array_job_id", "finalizer_job_id"):
        value = receipt.get(key)
        if not isinstance(value, str) or JOB_RE.fullmatch(value) is None:
            _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH")
    bound_environment_sha256 = _load_bound_submission_environment_sha256(run)
    try:
        topology = scheduler.build_topology(
            head=run.authority.governing_commit, attempt_id=run.attempt_id
        )
        scheduler.validate_submission_receipt(
            receipt,
            topology=topology,
            expected_qsub_environment_sha256=bound_environment_sha256,
        )
    except scheduler.FullSchedulerError as exc:
        mapping = {
            "SUBMISSION_RECEIPT_SCHEMA_INVALID": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_SCHEMA_INVALID"
            ),
            "SUBMISSION_RECEIPT_AUTHORITY_INVALID": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID"
            ),
            "SUBMISSION_RECEIPT_EVIDENCE_INVALID": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_EVIDENCE_INVALID"
            ),
            "SCHEDULER_QSUB_OUTPUT_AMBIGUOUS": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_EVIDENCE_INVALID"
            ),
            "SCHEDULER_ARRAY_QSUB_OUTPUT_AMBIGUOUS": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_EVIDENCE_INVALID"
            ),
            "SUBMISSION_RECEIPT_ENVIRONMENT_INVALID": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_ENVIRONMENT_BINDING_MISMATCH"
            ),
            "SCHEDULER_JOB_ID_INVALID": (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH"
            ),
        }
        raise FullSequentialError(
            mapping.get(
                exc.code,
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID",
            )
        ) from exc
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID"
        ) from exc
    return receipt


def _submission_receipt_json_pairs(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JSON_INVALID")
        value[key] = item
    return value


def _reject_submission_receipt_json_constant(_value: str) -> Any:
    _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JSON_INVALID")


def _load_submission_receipt(path: Path) -> Mapping[str, Any]:
    """Load one bounded owner-private receipt through a stable no-follow fd."""

    try:
        payload = _read_owner_private_regular(
            path, maximum_bytes=SUBMISSION_RECEIPT_MAXIMUM_BYTES
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_FILE_INVALID"
        ) from exc
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_submission_receipt_json_pairs,
            parse_constant=_reject_submission_receipt_json_constant,
        )
    except FullSequentialError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JSON_INVALID"
        ) from exc
    if not isinstance(value, Mapping):
        _fail("FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JSON_INVALID")
    return value


@contextmanager
def _digest_provider(
    factory: Callable[[minimal.LiveAuthority], Any] | None,
    authority: minimal.LiveAuthority,
) -> Iterator[Callable[[Path, str], Mapping[str, Any]]]:
    if factory is not None:
        supplied = factory(authority)
        if hasattr(supplied, "__enter__"):
            with supplied as active:
                yield active.digest if hasattr(active, "digest") else active
        else:
            yield supplied.digest if hasattr(supplied, "digest") else supplied
        return
    with core.ExternalCRC32CDigestWorker(
        python_executable=authority.crc32c_python,
        worker_script=authority.crc32c_worker,
        expected_python_sha256=authority.crc32c_python_sha256,
        expected_worker_sha256=authority.crc32c_worker_sha256,
        expected_distribution_sha256=authority.crc32c_distribution_sha256,
    ) as worker:
        yield worker.digest


def _provider_and_transport(
    run: FullRun, dependencies: FullDependencies
) -> tuple[Any, Any]:
    if dependencies.token_provider_factory is None:
        provider = core.GcloudADCTokenProvider(
            run.authority.gcloud,
            cloudsdk_config=run.authority.cloudsdk_config,
            authority_receipt=run.authority.gcloud_receipt,
            authority_receipt_sha256=run.authority.gcloud_receipt_sha256,
        )
    else:
        provider = dependencies.token_provider_factory(run.authority)
    observed = provider.validate_authority()
    core.validate_gcloud_runtime_authority(
        observed, expected_runtime_authority=run.runtime_authority
    )
    transport = (
        core.GCSExactObjectBodyTransport()
        if dependencies.transport_factory is None
        else dependencies.transport_factory()
    )
    return provider, transport


def _execute_cache_retirement(**kwargs: Any) -> Mapping[str, Any]:
    """Delegate the destructive step to the existing exact retirement gate."""
    run = kwargs["run"]
    batch_id = str(kwargs["batch_id"])
    authorization = Path(kwargs["authorization_receipt_path"])
    paths = _batch_paths(run, batch_id)
    exit_status = retirement.main(
        [
            "--execute",
            "--contract", str(run.contract_path),
            "--plan", str(run.plan_path),
            "--environment-receipt", str(run.authority.environment_receipt),
            "--production-root", str(run.production_root),
            "--attempt-id", run.attempt_id,
            "--batch-id", batch_id,
            "--governing-commit", run.authority.governing_commit,
            "--final-ledger", str(paths["eligibility_ledger"]),
            "--preservation-receipt",
            str(paths["preservation"] / "batch_preservation_receipt.restricted.json"),
            "--authorization-receipt", str(authorization),
            "--launch-authority-sha256", run.launch_authority_sha256,
        ],
        requirements=kwargs.get("requirements"),
        expected_runtime_authority=kwargs.get("expected_runtime_authority"),
        allowed_production_prefix=run.production_root,
        _synthetic_test_capability=(
            retirement._SYNTHETIC_TEST_ROOT_CAPABILITY
            if kwargs.get("test_only_synthetic_full_scope") is True
            else None
        ),
        artifact_validation_context=kwargs.get(
            "artifact_validation_context",
            retirement.STRICT_CONTENT_HASH,
        ),
        scheduler_runner_path=kwargs.get("scheduler_runner_path"),
    )
    if exit_status != 0:
        _fail("FULL_SEQUENTIAL_CACHE_RETIREMENT_FAILED")
    return finalizer.load_json(paths["final_receipt"], "FULL_BATCH_FINAL_RECEIPT")


def _validate_batch_finalization(
    *, run: FullRun, batch_id: str, **_kwargs: Any
) -> Mapping[str, Any]:
    paths = _batch_paths(run, batch_id)
    receipt = finalizer.load_json(paths["final_receipt"], "FULL_BATCH_FINAL_RECEIPT")
    finalizer._validate_current_receipt_v3(receipt)
    planned_batch = next(
        (row for row in run.plan["batches"] if row["batch_id"] == batch_id),
        None,
    )
    if (
        not isinstance(planned_batch, Mapping)
        or receipt.get("prespecified_no_cine_study_set_sha256")
        != planned_batch["prespecified_no_cine_study_set_sha256"]
        or receipt.get("n_no_cine_studies")
        != planned_batch["expected_no_cine_studies"]
        or receipt.get("all_no_cine_studies_prespecified") is not True
        or receipt.get("attempt_id") != run.attempt_id
        or receipt.get("batch_id") != batch_id
        or receipt.get("governing_commit") != run.authority.governing_commit
        or receipt.get("batch_plan_sha256") != run.plan_sha256
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
    ):
        _fail("FULL_SEQUENTIAL_BATCH_FINALIZATION_INVALID")
    return receipt


def _cache_retirement_authorization(
    *, run: FullRun, batch_id: str, paths: Mapping[str, Path]
) -> Path:
    receipt_path = paths["preservation"] / "batch_preservation_receipt.restricted.json"
    receipt = preservation.load_json(receipt_path, "FULL_PRESERVATION_RECEIPT")
    ledger = core.load_strict_json(paths["eligibility_ledger"])
    cache_root = paths["extraction"] / "clips"
    tree_sha = retirement.cache_tree_sha256(cache_root)
    authorization_root = run.attempt_root / "cache_retirement_authorizations"
    _ensure_private_directory(authorization_root)
    authorization_path = authorization_root / f"{batch_id}.authorization.json"
    value = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_cache_retirement_owner_authorization_v2",
        "status": "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT",
        "authorization_scope": "EXTRACTED_CACHE_RETIREMENT",
        "owner_authorized": True,
        "owner_authorization_date_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "batch_id": batch_id,
        "attempt_id": run.attempt_id,
        "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
        "preservation_receipt_sha256": core.sha256_file(receipt_path),
        "cache_inventory_sha256": tree_sha,
        "launch_authority_sha256": run.launch_authority_sha256,
    }
    if receipt.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        _fail("FULL_SEQUENTIAL_RETIREMENT_BEFORE_PRESERVATION")
    _write_private_json(authorization_path, value, attempt_id=run.attempt_id)
    return authorization_path


def run_batch_task(
    *,
    task_id: int | None = None,
    run: FullRun | None = None,
    dependencies: FullDependencies | None = None,
) -> Mapping[str, Any]:
    """Run the seven validated stages for exactly one deterministic batch."""

    effective_run = run or build_full_run(
        scheduler_job_identity=str(os.environ.get("JOB_ID", ""))
    )
    effective_task = task_id
    if effective_task is None:
        raw_task = os.environ.get("SGE_TASK_ID", "")
        if TASK_RE.fullmatch(raw_task) is None:
            _fail("FULL_SEQUENTIAL_TASK_ID_INVALID")
        effective_task = int(raw_task)
    batch_id = task_to_batch(effective_task, effective_run.plan)
    dependency = resolve_dependencies(dependencies)
    if (
        dependency.execution_context is R8R_FIXED_CONTINUATION
        and effective_task not in range(4, 20)
    ):
        _fail("FULL_SEQUENTIAL_R8R_CONTINUATION_TASK_OUT_OF_SCOPE")
    if (
        dependency.execution_context is R8U_R2_FIXED_CONTINUATION
        and effective_task not in range(17, 20)
    ):
        _fail("FULL_SEQUENTIAL_R8U_CONTINUATION_TASK_OUT_OF_SCOPE")

    # This gate is deliberately first for tasks 2..N: no validation below may
    # construct a token provider, body transport, DICOM reader, or GPU object.
    if effective_task > 1:
        with _stage_boundary("PRIOR_BATCH_FINALIZATION"):
            if dependency.prior_batch_validator is prior_gate.validate_prior_batch:
                prior_kwargs = _prior_batch_kwargs(effective_run, batch_id)
                if dependency.test_only_synthetic_full_scope:
                    prior_kwargs["_synthetic_test_capability"] = (
                        retirement._SYNTHETIC_TEST_ROOT_CAPABILITY
                    )
                dependency.prior_batch_validator(**prior_kwargs)
            else:
                dependency.prior_batch_validator(
                    run=effective_run, batch_id=batch_id
                )

    # The array is constrained to one task, and every preceding batch must
    # have retired its extracted clips.  Recheck the physical topology before
    # any token or body boundary so an unexpected second cache fails closed.
    with _stage_boundary("PREBODY_AUTHORITY"):
        cache_inventory = _extraction_cache_inventory(
            effective_run.production_root,
            current_attempt_id=effective_run.attempt_id,
        )
        if dependency.execution_context is R8U_R2_FIXED_CONTINUATION:
            # The one Task-16 scheduler-failure partial is immutable evidence,
            # not a resumable cache.  The fixed R8U controller revalidates its
            # exact seal here; every other active cache remains prohibited.
            if cache_inventory.active != 1:
                _fail("FULL_SEQUENTIAL_R8U_EXTRACTION_CACHE_TOPOLOGY_INVALID")
            import lvef_c3_r8r_recovery_continuation as r8r

            r8r.validate_r8u_failed_partial_seal()
        elif cache_inventory.active != 0:
            _fail("FULL_SEQUENTIAL_ACTIVE_EXTRACTION_CACHE_PRESENT")

    with _stage_boundary("SUBMISSION_AUTHORITY"):
        if JOB_RE.fullmatch(str(os.environ.get("JOB_ID", ""))) is not None:
            if dependency.execution_context is R8R_FIXED_CONTINUATION:
                import lvef_c3_r8r_recovery_continuation as r8r

                r8r.validate_continuation_worker_submission(
                    effective_run,
                    current_job_id=str(os.environ.get("JOB_ID", "")),
                    role="array",
                )
            elif dependency.execution_context is R8U_R2_FIXED_CONTINUATION:
                import lvef_c3_r8r_recovery_continuation as r8r

                r8r.validate_r8u_continuation_worker_submission(
                    effective_run,
                    current_job_id=str(os.environ.get("JOB_ID", "")),
                    role="array",
                )
            else:
                _wait_for_submission_receipt(
                    effective_run,
                    current_job_id=str(os.environ.get("JOB_ID", "")),
                    role="array",
                    monotonic_clock=dependency.monotonic_clock,
                    sleeper=dependency.sleeper,
                )
        _validate_full_run(effective_run)
    paths = _batch_paths(effective_run, batch_id)
    planned = effective_run.plan["batches"][effective_task - 1]
    object_keys = {str(row["source_object_key"]) for row in planned["objects"]}
    with _stage_boundary("PREBODY_AUTHORITY"):
        try:
            environment_arguments: dict[str, Any] = {}
            if dependency.execution_context in {
                R8R_FIXED_CONTINUATION,
                R8U_R2_FIXED_CONTINUATION,
            }:
                environment_arguments["runtime_validation_context"] = (
                    stages.SEALED_SCHEDULER_RUNTIME_REPLAY
                )
            dependency.environment_validator(
                effective_run.authority.environment_receipt,
                expected_environment_receipt_sha256=(
                    effective_run.runtime_authority["environment_receipt_sha256"]
                ),
                scientific_governing_commit=effective_run.authority.governing_commit,
                **environment_arguments,
            )
        except Exception as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_PREBODY_ENVIRONMENT_AUTHORITY_FAILED"
            ) from exc
        if core.sha256_file(effective_run.authority.checkpoint) != (
            effective_run.runtime_authority["checkpoint_sha256"]
        ):
            _fail("FULL_SEQUENTIAL_PREBODY_CHECKPOINT_AUTHORITY_FAILED")

    with _stage_boundary("DOWNLOAD"):
        _ensure_private_directory(effective_run.attempt_root)
        _ensure_private_directory(paths["raw_root"])
        _ensure_private_directory(paths["batch_root"].parent, parents=True)
        _ensure_private_directory(paths["batch_root"])
        ledger = core.initialize_resume_ledger(
            effective_run.plan,
            requirements=effective_run.requirements,
            attempt_id=effective_run.attempt_id,
            authority=effective_run.runtime_authority,
            batch_ids=[batch_id],
        )
        provider, transport = _provider_and_transport(effective_run, dependency)
        previous_billing = os.environ.get(effective_run.authority.billing_variable)
        os.environ[effective_run.authority.billing_variable] = (
            effective_run.authority.billing_project
        )
        try:
            with _digest_provider(
                dependency.digest_provider_factory, effective_run.authority
            ) as digest:
                downloaded = dependency.download(
                    plan=effective_run.plan,
                    requirements=effective_run.requirements,
                    ledger=ledger,
                    contract=effective_run.contract,
                    batch_id=batch_id,
                    expected_runtime_authority=effective_run.runtime_authority,
                    authorization_receipt=None,
                    direct_full_authority=effective_run.launch_authority,
                    output_root=paths["raw_root"],
                    scoped_production_root=effective_run.production_root,
                    launch_authority_sha256=effective_run.launch_authority_sha256,
                    argv=(),
                    token_provider=provider,
                    transport=transport,
                    now=datetime.now(timezone.utc),
                    monotonic_clock=dependency.monotonic_clock,
                    sleeper=dependency.sleeper,
                    digest_provider=digest,
                    test_only_synthetic_full_scope=(
                        dependency.test_only_synthetic_full_scope
                    ),
                )
        finally:
            if previous_billing is None:
                os.environ.pop(effective_run.authority.billing_variable, None)
            else:
                os.environ[effective_run.authority.billing_variable] = previous_billing
        _write_private_json(
            paths["download_ledger"],
            downloaded,
            attempt_id=effective_run.attempt_id,
        )

    with _stage_boundary("DICOM_EXTRACTION"):
        _ensure_private_directory(paths["extraction_batch_root"].parent, parents=True)
        _ensure_private_directory(paths["extraction_batch_root"])
        verified = paths["raw_batch"] / "verified_download_manifest.restricted.csv"
        stages.validate_stage_predecessor(
            input_ledger=paths["download_ledger"],
            batch_id=batch_id,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=effective_run.runtime_authority,
            expected_attempt_id=effective_run.attempt_id,
            expected_object_keys=object_keys,
            bound_manifest=verified,
        )
        stages.validate_download_manifest_plan_membership(verified, planned)
        dicom_summary = dependency.dicom(
            verified_download_manifest=verified,
            download_root=paths["raw_batch"] / "objects",
            batch_output_root=paths["extraction_batch_root"],
            workers=dependency.extraction_workers,
            batch_id=batch_id,
            attempt_id=effective_run.attempt_id,
            runtime_authority=effective_run.runtime_authority,
            planned_batch=planned,
            embedding_output_root=paths["echoprime"],
        )
        stages.advance_stage_ledger(
            input_ledger=paths["download_ledger"],
            output_ledger=paths["extraction_ledger"],
            receipt_root=paths["extraction"] / "transition_receipts",
            batch_id=batch_id,
            transitions=(
                (
                    "DICOM_AUDIT_COMPLETE",
                    core.sha256_file(
                        paths["extraction"] / "dicom_audit.restricted.csv"
                    ),
                ),
                (
                    "EXTRACTION_COMPLETE",
                    core.sha256_file(
                        paths["extraction"]
                        / "extraction_manifest.restricted.csv"
                    ),
                ),
            ),
            expected_authority=effective_run.runtime_authority,
            expected_attempt_id=effective_run.attempt_id,
            expected_object_keys=object_keys,
        )

    with _stage_boundary("ECHOPRIME_EMBEDDING"):
        extraction_manifest = (
            paths["extraction"] / "extraction_manifest.restricted.csv"
        )
        stages.validate_extraction_manifest_plan_membership(
            extraction_manifest,
            planned,
            paths["extraction"]
            / "technical_disposition_manifest.restricted.csv",
        )
        echoprime_arguments: dict[str, Any] = {}
        if dependency.execution_context in {
            R8R_FIXED_CONTINUATION,
            R8U_R2_FIXED_CONTINUATION,
        }:
            echoprime_arguments["runtime_validation_context"] = (
                stages.SEALED_SCHEDULER_RUNTIME_REPLAY
            )
        embedding_summary = dependency.echoprime(
            extraction_manifest=extraction_manifest,
            extraction_root=paths["extraction"] / "clips",
            technical_disposition_manifest=(
                paths["extraction"]
                / "technical_disposition_manifest.restricted.csv"
            ),
            dicom_audit=paths["extraction"] / "dicom_audit.restricted.csv",
            dicom_extraction_summary=(
                paths["extraction"] / "dicom_extraction.summary.json"
            ),
            verified_download_manifest=(
                paths["raw_batch"] / "verified_download_manifest.restricted.csv"
            ),
            selected_batch_manifest=(
                paths["raw_batch"] / "selected_batch.restricted.csv"
            ),
            checkpoint=effective_run.authority.checkpoint,
            environment_receipt=effective_run.authority.environment_receipt,
            orchestration_contract=effective_run.contract_path,
            batch_plan=effective_run.plan_path,
            batch_id=batch_id,
            batch_output_root=paths["batch_root"],
            batch_size=dependency.echoprime_batch_size,
            seed=20260803,
            attempt_id=effective_run.attempt_id,
            runtime_authority=effective_run.runtime_authority,
            requirements=effective_run.requirements,
            **echoprime_arguments,
        )
        stages.advance_stage_ledger(
            input_ledger=paths["extraction_ledger"],
            output_ledger=paths["pooling_ledger"],
            receipt_root=paths["echoprime"] / "transition_receipts",
            batch_id=batch_id,
            transitions=(
                (
                    "EMBEDDING_COMPLETE",
                    core.sha256_file(
                        paths["echoprime"] / "clip_manifest.restricted.csv"
                    ),
                ),
                (
                    "STUDY_POOLING_COMPLETE",
                    core.sha256_file(
                        paths["echoprime"] / "study_manifest.restricted.csv"
                    ),
                ),
            ),
            expected_authority=effective_run.runtime_authority,
            expected_attempt_id=effective_run.attempt_id,
            expected_object_keys=object_keys,
        )
    with _stage_boundary("BATCH_PRESERVATION"):
        preservation_arguments: dict[str, Any] = {}
        scheduler_runner_path = ARRAY_RUNNER_PATH
        if dependency.execution_context in {
            R8R_FIXED_CONTINUATION,
            R8U_R2_FIXED_CONTINUATION,
        }:
            preservation_arguments["runtime_validation_context"] = (
                stages.SEALED_SCHEDULER_RUNTIME_REPLAY
            )
            scheduler_runner_path = (
                SCRIPT_ROOT
                / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
            )
        preservation_receipt = dependency.preserve(
            contract_path=effective_run.contract_path,
            plan_path=effective_run.plan_path,
            batch_id=batch_id,
            attempt_id=effective_run.attempt_id,
            governing_commit=effective_run.authority.governing_commit,
            production_root=effective_run.production_root,
            output_root=paths["preservation"],
            environment_receipt=effective_run.authority.environment_receipt,
            checkpoint=effective_run.authority.checkpoint,
            scheduler_job_identity=effective_run.scheduler_job_identity,
            input_ledger=paths["pooling_ledger"],
            requirements=effective_run.requirements,
            expected_runtime_authority=effective_run.runtime_authority,
            scheduler_runner_path=scheduler_runner_path,
            **preservation_arguments,
        )
    with _stage_boundary("CACHE_RETIREMENT_ELIGIBILITY"):
        authorization_path = _cache_retirement_authorization(
            run=effective_run, batch_id=batch_id, paths=paths
        )
    with _stage_boundary("CACHE_RETIREMENT"):
        retirement_arguments: dict[str, Any] = {}
        if dependency.execution_context in {
            R8R_FIXED_CONTINUATION,
            R8U_R2_FIXED_CONTINUATION,
        }:
            retirement_arguments["scheduler_runner_path"] = (
                SCRIPT_ROOT
                / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
            )
        dependency.retire(
            run=effective_run,
            batch_id=batch_id,
            authorization_receipt_path=authorization_path,
            requirements=effective_run.requirements,
            expected_runtime_authority=effective_run.runtime_authority,
            test_only_synthetic_full_scope=(
                dependency.test_only_synthetic_full_scope
            ),
            **retirement_arguments,
        )
    with _stage_boundary("BATCH_FINALIZATION"):
        result = dependency.finalize_batch(run=effective_run, batch_id=batch_id)
        if (
            result.get("status") != "PASS_BATCH_FINALIZED"
            or result.get("raw_dicoms_retained") is not True
            or result.get("extracted_cache_retired") is not True
        ):
            _fail("FULL_SEQUENTIAL_BATCH_TERMINAL_INVALID")
    return {
        **result,
        "ordered_stages": list(ORDERED_STAGES),
        "dicom_summary_status": dicom_summary.get("status"),
        "embedding_summary_status": embedding_summary.get("status"),
        "preservation_status": preservation_receipt.get("status"),
    }


def _closed_extraction_provenance(value: object) -> bool:
    """Accept only the exact aggregate-only output of ``summarize_extraction``."""

    if not isinstance(value, Mapping) or frozenset(value) != EXTRACTION_PROVENANCE_KEYS:
        return False
    if (
        value.get("audit") != "prospective_cine_extraction"
        or value.get("status") not in {"PASS", "FAIL"}
        or value.get("temporal_sampling_policy")
        != "historical_compatible_linspace_or_tail_repeat_v1"
        or value.get("temporal_fallback_policy")
        != "stride2_signal_coverage_pair_repeat_v1"
        or value.get("temporal_sampling_long_cine_rule")
        != "endpoint_inclusive_integer_linspace"
        or value.get("temporal_sampling_short_cine_rule")
        != "ordered_source_frames_then_repeat_final_frame"
        or value.get("row_values_emitted") is not False
        or value.get("paths_emitted") is not False
    ):
        return False
    if any(
        not isinstance(value.get(key), bool)
        for key in EXTRACTION_PROVENANCE_BOOLEAN_KEYS
    ):
        return False
    if any(
        not isinstance(value.get(key), int)
        or isinstance(value.get(key), bool)
        or int(value[key]) < 0
        for key in EXTRACTION_PROVENANCE_INTEGER_KEYS
    ):
        return False
    requested = int(value["n_requested_cines"])
    extracted = int(value["n_extracted_cines"])
    if (
        requested < 1
        or extracted > requested
        or int(value["n_failed_cines"]) != requested - extracted
        or any(
            int(value[key]) > extracted
            for key in EXTRACTION_PROVENANCE_INTEGER_KEYS
            if key not in {"n_requested_cines", "n_failed_cines"}
        )
    ):
        return False

    gate_state_counts = value.get("preprocessing_gate_state_counts")
    gate_states = frozenset({"PASS", "FAIL", "NOT_EVALUATED", "INVALID"})
    if (
        not isinstance(gate_state_counts, Mapping)
        or len(gate_state_counts) != len(EXTRACTION_PROVENANCE_GATE_STATE_KEYS)
        or frozenset(gate_state_counts) != EXTRACTION_PROVENANCE_GATE_STATE_KEYS
    ):
        return False
    for gate in EXTRACTION_PROVENANCE_GATE_STATE_KEYS:
        states = gate_state_counts.get(gate)
        if (
            not isinstance(states, Mapping)
            or len(states) != 4
            or frozenset(states) != gate_states
            or any(
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                for count in states.values()
            )
            or sum(states.values()) != requested
        ):
            return False

    allowed_map_keys: Mapping[str, frozenset[str] | None] = {
        "selected_preprocessing_path_counts": frozenset(
            {
                "ORDINARY_CENTER_CROP_RESIZE_HISTORICAL_TEMPORAL_V1",
                "SECTOR_BOUND_SQUARE_PAD_SPATIAL_FALLBACK_V1",
                "STRIDE2_SIGNAL_COVERAGE_TEMPORAL_FALLBACK_V1",
                "SECTOR_BOUND_SQUARE_PAD_AND_STRIDE2_SIGNAL_COVERAGE_FALLBACK_V1",
                "NOT_SELECTED",
                "MISSING",
                "INVALID",
            }
        ),
        "fallback_status_counts": frozenset(
            {
                "NOT_ATTEMPTED",
                "FALLBACK_PATH_PASS",
                "FALLBACK_PATH_FAILED",
                "MISSING",
                "INVALID",
            }
        ),
        "failure_substage_counts": frozenset(
            {
                "NONE",
                "DECODE_OR_COLOR_CONVERSION_FAILURE",
                "SOURCE_SIGNAL_QUALITY_FAILURE",
                "SPATIAL_CROP_RESIZE_FAILURE",
                "POST_CROP_SIGNAL_QUALITY_FAILURE",
                "TEMPORAL_SAMPLING_FAILURE",
                "SAMPLED_NONZERO_SIGNAL_FAILURE",
                "SAMPLED_TEMPORAL_VARIATION_FAILURE",
                "OUTPUT_WRITE_FAILURE",
                "MISSING",
                "INVALID",
            }
        ),
        "decode_color_status_counts": frozenset(
            {
                "PASS",
                "NOT_REACHED",
                "DECODE_OR_COLOR_CONVERSION_FAILURE",
                "MISSING",
                "INVALID",
            }
        ),
        "temporal_sampling_policy_counts": frozenset(
            {
                "historical_compatible_linspace_or_tail_repeat_v1",
                "stride2_signal_coverage_pair_repeat_v1",
                "MISSING",
                "INVALID",
            }
        ),
        "photometric_interpretation_counts": frozenset(
            {"MONOCHROME1", "MONOCHROME2", "RGB", "YBR_FULL", "YBR_FULL_422",
             "MISSING", "INVALID"}
        ),
        "transfer_syntax_uid_counts": None,
        "decoder_backend_counts": None,
        "decoder_color_behavior_counts": frozenset(
            {"STORED_COLOR_RAW", "MISSING", "INVALID"}
        ),
        "color_transform_counts": frozenset(
            {
                "MONOCHROME1_INVERT_REPLICATE_TO_RGB",
                "MONOCHROME2_REPLICATE_TO_RGB",
                "NONE_RGB",
                "EXPLICIT_YBR_FULL_TO_RGB",
                "EXPLICIT_YBR_FULL_422_TO_RGB",
                "MISSING",
                "INVALID",
            }
        ),
    }
    whole_frame_maps = {
        "fallback_status_counts",
        "failure_substage_counts",
        "decode_color_status_counts",
    }
    for field in EXTRACTION_PROVENANCE_COUNT_MAP_KEYS:
        observed = value.get(field)
        if not isinstance(observed, Mapping) or len(observed) > 64:
            return False
        allowed = allowed_map_keys[field]
        total = 0
        for key, count in observed.items():
            if not isinstance(key, str) or not key or len(key) > 120:
                return False
            if allowed is not None and key not in allowed:
                return False
            if field == "transfer_syntax_uid_counts" and key not in {
                "MISSING", "INVALID"
            } and re.fullmatch(r"1\.2\.840\.10008\.1\.2(?:\.[0-9]+)*", key) is None:
                return False
            if field == "decoder_backend_counts" and key not in {
                "MISSING", "INVALID"
            } and re.fullmatch(
                r"pydicom_pixels_raw:(?:native|pylibjpeg|gdcm|pillow|pyjpegls)",
                key,
            ) is None:
                return False
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return False
            total += count
        expected_total = requested if field in whole_frame_maps else extracted
        if total != expected_total:
            return False
    return True


def _closed_terminal_dicom_counts(value: Mapping[str, Any]) -> bool:
    count_names = (
        "n_objects",
        "n_studies",
        "n_readable",
        "n_unreadable",
        "n_multiframe_candidates",
        "n_single_frame",
        "n_pixel_decode_failures",
    )
    if any(
        not isinstance(value.get(name), int)
        or isinstance(value.get(name), bool)
        or int(value[name]) < 0
        for name in count_names
    ):
        return False
    return bool(
        value["n_objects"] >= 1
        and value["n_studies"] >= 1
        and value["n_readable"] + value["n_unreadable"]
        == value["n_objects"]
        and value["n_multiframe_candidates"] + value["n_single_frame"]
        == value["n_readable"]
        and value["n_pixel_decode_failures"]
        <= value["n_multiframe_candidates"]
        and (
            value["n_unreadable"] > 0
            or value["n_pixel_decode_failures"] > 0
        )
        and value.get("physical_source_keys_unique") is True
    )


def _closed_owner_private_partial_tree(partial: Path) -> bool:
    """Require a bounded all-owner, no-follow, closed partial evidence tree."""

    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        _validate_private_directory(partial)
        entries = 0
        for current_text, directories, filenames in os.walk(
            partial,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            current = Path(current_text)
            directories.sort()
            filenames.sort()
            paths = [(current / name, False) for name in filenames]
            if current == partial:
                paths.insert(0, (current, True))
            paths.extend((current / name, True) for name in directories)
            for path, expected_directory in paths:
                item = os.lstat(path)
                entries += 1
                if entries > TERMINAL_PARTIAL_MAXIMUM_ENTRIES:
                    return False
                if stat.S_ISLNK(item.st_mode) or item.st_uid != os.geteuid():
                    return False
                mode = stat.S_IMODE(item.st_mode)
                if expected_directory:
                    if not stat.S_ISDIR(item.st_mode) or mode not in {0o700, 0o2700}:
                        return False
                elif (
                    not stat.S_ISREG(item.st_mode)
                    or mode != 0o600
                    or item.st_nlink != 1
                ):
                    return False
    except (FullSequentialError, OSError):
        return False
    return True


def _closed_terminal_failure_summary(partial: Path) -> bool:
    """Recognize only an atomically written, owner-private closed failure.

    A prior attempt's partial clip tree is immutable failure evidence only after
    the production writer has published one of its exact terminal summaries.
    Ambiguous, malformed, symlinked, or permission-drifted summaries are never
    treated as terminal and therefore remain blocking caches.
    """

    if not _closed_owner_private_partial_tree(partial):
        return False
    try:
        value, _ = _load_owner_private_json(partial / "failure.summary.json")
    except (FullSequentialError, OSError):
        return False
    if (
        value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
    ):
        return False
    keys = frozenset(value)
    status = value.get("status")
    if keys == TERMINAL_DOWNLOAD_FAILURE_SUMMARY_KEYS:
        return status in TERMINAL_DOWNLOAD_FAILURE_STATUSES
    if keys == TERMINAL_EXTRACTION_FAILURE_SUMMARY_KEYS:
        error_code = value.get("error_code")
        return (
            status == "FAIL_EXTRACTION_GATE"
            and isinstance(error_code, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", error_code) is not None
        )
    if keys == TERMINAL_EXTRACTION_FAILURE_SUMMARY_V2_KEYS:
        error_code = value.get("error_code")
        return bool(
            value.get("schema_version") == 2
            and value.get("artifact_type")
            == "lvef_c3_batch_extraction_failure_summary_v2"
            and status == "FAIL_EXTRACTION_GATE"
            and isinstance(error_code, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", error_code) is not None
            and _closed_extraction_provenance(value.get("extraction_provenance"))
        )
    if keys == TERMINAL_DICOM_FAILURE_SUMMARY_KEYS:
        return bool(
            status == "FAIL_DICOM_OR_PIXEL_DECODE_GATE"
            and _closed_terminal_dicom_counts(value)
        )
    if keys == TERMINAL_DICOM_FAILURE_SUMMARY_V2_KEYS:
        return bool(
            value.get("schema_version") == 2
            and value.get("artifact_type")
            == "lvef_c3_batch_dicom_or_extraction_failure_summary_v2"
            and status == "FAIL_DICOM_OR_PIXEL_DECODE_GATE"
            and _closed_terminal_dicom_counts(value)
            and _closed_extraction_provenance(value.get("extraction_provenance"))
        )
    return False


def _extraction_cache_inventory(
    production_root: Path, *, current_attempt_id: str
) -> ExtractionCacheInventory:
    """Separate live caches from immutable terminal evidence across attempts."""

    if ATTEMPT_RE.fullmatch(current_attempt_id) is None:
        _fail("FULL_SEQUENTIAL_ATTEMPT_ID_INVALID")
    attempts = production_root / "attempts"
    if not os.path.lexists(attempts):
        return ExtractionCacheInventory(active=0, preserved_terminal_failed=0)
    if attempts.is_symlink() or not attempts.is_dir():
        _fail("FULL_SEQUENTIAL_ATTEMPTS_ROOT_INVALID")
    active = 0
    preserved = 0
    for attempt in attempts.iterdir():
        if attempt.is_symlink() or not attempt.is_dir():
            _fail("FULL_SEQUENTIAL_ATTEMPT_TOPOLOGY_INVALID")
        cache_root = attempt / "extracted_cache"
        if not os.path.lexists(cache_root):
            continue
        if cache_root.is_symlink() or not cache_root.is_dir():
            _fail("FULL_SEQUENTIAL_CACHE_TOPOLOGY_INVALID")
        for batch in cache_root.iterdir():
            if (
                batch.is_symlink()
                or not batch.is_dir()
                or re.fullmatch(r"c3_batch_(?:00[0-9]|01[0-8])", batch.name)
                is None
            ):
                active += 1
                continue
            clips = batch / "dicom_extraction" / "clips"
            partial = batch / "dicom_extraction.partial"
            clips_present = os.path.lexists(clips)
            partial_present = os.path.lexists(partial)
            if not clips_present and not partial_present:
                continue
            if (
                ATTEMPT_RE.fullmatch(attempt.name) is not None
                and attempt.name != current_attempt_id
                and partial_present
                and not clips_present
                and _closed_terminal_failure_summary(partial)
            ):
                preserved += 1
            else:
                active += 1
    return ExtractionCacheInventory(
        active=active, preserved_terminal_failed=preserved
    )


def validate_successor_capacity_authority(
    value: Mapping[str, Any]
) -> None:
    if set(value) != SUCCESSOR_CAPACITY_KEYS:
        _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SCHEMA_INVALID")
    integer_keys = (
        "frozen_projected_peak_bytes",
        "frozen_original_current_usage_bytes",
        "successor_increment_bytes",
        "live_research_usage_bytes",
        "projected_total_research_usage_bytes",
        "research_quota_bytes",
        "quota_remaining_after_successor_bytes",
        "research_filesystem_available_bytes",
        "physical_remaining_after_successor_bytes",
        "research_file_slots_remaining",
        "required_reserve_bytes",
        "required_remaining_file_slots",
        "active_extraction_caches",
        "preserved_terminal_failed_extraction_caches",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "dicom_body_reads",
        "writes_performed",
    )
    if (
        value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_fresh_successor_capacity_authority_v2"
        or value.get("status")
        != "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
        or SHA_RE.fullmatch(
            str(value.get("source_capacity_authority_sha256"))
        )
        is None
        or not isinstance(value.get("source_dynamic_receipt_bytes"), int)
        or isinstance(value.get("source_dynamic_receipt_bytes"), bool)
        or value["source_dynamic_receipt_bytes"] < 1
        or not isinstance(value.get("source_dynamic_receipt_sha256"), str)
        or SHA_RE.fullmatch(value["source_dynamic_receipt_sha256"]) is None
        or any(
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            for key in integer_keys
        )
        or value.get("frozen_projected_peak_bytes")
        != FROZEN_FULL_PLAN_PROJECTED_PEAK_BYTES
        or value.get("frozen_original_current_usage_bytes")
        != FROZEN_FULL_PLAN_ORIGINAL_CURRENT_USAGE_BYTES
        or value.get("successor_increment_bytes") != SUCCESSOR_INCREMENT_BYTES
        or value.get("successor_increment_bytes")
        != value.get("frozen_projected_peak_bytes")
        - value.get("frozen_original_current_usage_bytes")
        or value.get("projected_total_research_usage_bytes")
        != value.get("live_research_usage_bytes") + SUCCESSOR_INCREMENT_BYTES
        or value.get("quota_remaining_after_successor_bytes")
        != value.get("research_quota_bytes")
        - value.get("projected_total_research_usage_bytes")
        or value.get("physical_remaining_after_successor_bytes")
        != value.get("research_filesystem_available_bytes")
        - SUCCESSOR_INCREMENT_BYTES
        or value.get("required_reserve_bytes")
        != SUCCESSOR_REQUIRED_RESERVE_BYTES
        or value.get("required_remaining_file_slots")
        != SUCCESSOR_REQUIRED_FILE_SLOTS
        or value.get("active_extraction_caches") != 0
        or value.get("preserved_terminal_failed_extraction_caches")
        != SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
        or value.get("quota_remaining_after_successor_bytes")
        < SUCCESSOR_REQUIRED_RESERVE_BYTES
        or value.get("physical_remaining_after_successor_bytes")
        < SUCCESSOR_REQUIRED_RESERVE_BYTES
        or value.get("research_file_slots_remaining")
        < SUCCESSOR_REQUIRED_FILE_SLOTS
        or any(
            value.get(key) is not True
            for key in (
                "quota_reserve_gate_passed",
                "physical_reserve_gate_passed",
                "file_slot_gate_passed",
                "terminal_failure_cache_gate_passed",
            )
        )
        or any(
            value.get(key) != 0
            for key in (
                "cloud_requests",
                "scheduler_jobs_submitted",
                "dicom_body_reads",
                "writes_performed",
            )
        )
    ):
        _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_INVALID")


def build_successor_capacity_authority(
    observed_capture: capacity.DynamicSuccessorCapacityCapture,
    cache_inventory: ExtractionCacheInventory,
) -> dict[str, Any]:
    try:
        if not isinstance(
            observed_capture, capacity.DynamicSuccessorCapacityCapture
        ):
            raise TypeError("dynamic capture required")
        observed_capacity = observed_capture.observation
        capacity.validate_dynamic_successor_capacity_observation(
            observed_capacity,
            expected_receipt_payload=observed_capture.receipt_payload,
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SOURCE_INVALID"
        ) from exc
    if observed_capacity.get("status") != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_NOT_PASS")
    live_usage = int(observed_capacity["live_research_usage_bytes"])
    research_quota = int(observed_capacity["live_research_quota_bytes"])
    physical_available = int(
        observed_capacity["live_research_filesystem_available_bytes"]
    )
    file_slots = int(observed_capacity["remaining_file_slots"])
    if (
        cache_inventory.active
        != observed_capacity["active_extraction_caches"]
        or cache_inventory.preserved_terminal_failed
        != observed_capacity["preserved_terminal_failed_extraction_caches"]
    ):
        _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SOURCE_INVALID")
    projected_total = live_usage + SUCCESSOR_INCREMENT_BYTES
    quota_remaining = research_quota - projected_total
    physical_remaining = physical_available - SUCCESSOR_INCREMENT_BYTES
    value = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_fresh_successor_capacity_authority_v2",
        "status": "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE",
        "source_capacity_authority_sha256": core.canonical_json_sha256(
            observed_capacity
        ),
        "source_dynamic_receipt_bytes": observed_capacity[
            "restricted_receipt_size_bytes"
        ],
        "source_dynamic_receipt_sha256": observed_capacity[
            "restricted_receipt_sha256"
        ],
        "frozen_projected_peak_bytes": FROZEN_FULL_PLAN_PROJECTED_PEAK_BYTES,
        "frozen_original_current_usage_bytes": (
            FROZEN_FULL_PLAN_ORIGINAL_CURRENT_USAGE_BYTES
        ),
        "successor_increment_bytes": SUCCESSOR_INCREMENT_BYTES,
        "live_research_usage_bytes": live_usage,
        "projected_total_research_usage_bytes": projected_total,
        "research_quota_bytes": research_quota,
        "quota_remaining_after_successor_bytes": quota_remaining,
        "research_filesystem_available_bytes": physical_available,
        "physical_remaining_after_successor_bytes": physical_remaining,
        "research_file_slots_remaining": file_slots,
        "required_reserve_bytes": SUCCESSOR_REQUIRED_RESERVE_BYTES,
        "required_remaining_file_slots": SUCCESSOR_REQUIRED_FILE_SLOTS,
        "active_extraction_caches": cache_inventory.active,
        "preserved_terminal_failed_extraction_caches": (
            cache_inventory.preserved_terminal_failed
        ),
        "quota_reserve_gate_passed": (
            quota_remaining >= SUCCESSOR_REQUIRED_RESERVE_BYTES
        ),
        "physical_reserve_gate_passed": (
            physical_remaining >= SUCCESSOR_REQUIRED_RESERVE_BYTES
        ),
        "file_slot_gate_passed": file_slots >= SUCCESSOR_REQUIRED_FILE_SLOTS,
        "terminal_failure_cache_gate_passed": (
            cache_inventory.active == 0
            and cache_inventory.preserved_terminal_failed
            == SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
        ),
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "dicom_body_reads": 0,
        "writes_performed": 0,
    }
    validate_successor_capacity_authority(value)
    return value


def _headroom_passes(
    *, quota: int, usage: int, physical: int, file_slots: int
) -> bool:
    return (
        quota - (usage + SUCCESSOR_INCREMENT_BYTES)
        >= SUCCESSOR_REQUIRED_RESERVE_BYTES
        and physical - SUCCESSOR_INCREMENT_BYTES
        >= SUCCESSOR_REQUIRED_RESERVE_BYTES
        and file_slots >= SUCCESSOR_REQUIRED_FILE_SLOTS
    )


def _capacity_gain_source(
    observation: Mapping[str, Any],
    *,
    evidence_role: str,
    pre_cleanup_observation: Mapping[str, Any] | None = None,
) -> str:
    if observation.get("status") != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        return "NONE"
    historical_quota = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    current_quota = int(observation["live_research_quota_bytes"])
    if evidence_role in {
        "R5B_HISTORICAL", "R5E_PRE_CLEANUP", "R5E_R2_PRE_ACTION"
    }:
        return (
            "ALLOCATION"
            if current_quota > historical_quota
            else "EXISTING_HEADROOM"
        )
    if evidence_role not in {
        "R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP"
    } or pre_cleanup_observation is None:
        _fail("FULL_SEQUENTIAL_CAPACITY_GAIN_SOURCE_INVALID")
    if pre_cleanup_observation.get("status") == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        _fail("FULL_SEQUENTIAL_CAPACITY_GAIN_SOURCE_INVALID")
    allocation_alone = (
        current_quota > historical_quota
        and _headroom_passes(
            quota=current_quota,
            usage=int(pre_cleanup_observation["live_research_usage_bytes"]),
            physical=int(
                pre_cleanup_observation[
                    "live_research_filesystem_available_bytes"
                ]
            ),
            file_slots=int(pre_cleanup_observation["remaining_file_slots"]),
        )
    )
    cleanup_alone = _headroom_passes(
        quota=historical_quota,
        usage=int(observation["live_research_usage_bytes"]),
        physical=int(observation["live_research_filesystem_available_bytes"]),
        file_slots=int(observation["remaining_file_slots"]),
    )
    if allocation_alone:
        return "ALLOCATION"
    if cleanup_alone:
        return "CLEANUP"
    return "BOTH"


def _sealed_r5e_r8_gain_source(
    observation: Mapping[str, Any],
) -> str:
    """Derive the claimable R8 gain solely from its sealed local receipt.

    A production R8 admission rejects the ALLOCATION-only case before claim
    materialization.  Among the two claimable values, the current observation
    itself therefore closes the distinction: historical-quota headroom means
    CLEANUP; otherwise the admitted result necessarily required BOTH.
    """

    if observation.get("status") != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    try:
        cleanup_alone = _headroom_passes(
            quota=capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024,
            usage=int(observation["live_research_usage_bytes"]),
            physical=int(
                observation["live_research_filesystem_available_bytes"]
            ),
            file_slots=int(observation["remaining_file_slots"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
        ) from exc
    return "CLEANUP" if cleanup_alone else "BOTH"


def _validate_capacity_admission(value: CapacityAdmission) -> None:
    if not isinstance(value, CapacityAdmission):
        _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")
    if value.evidence_role not in {
        "R5B_HISTORICAL", "R5E_PRE_CLEANUP", "R5E_R2_PRE_ACTION",
        "R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP",
    }:
        _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")
    if value.capacity_gain_source not in {
        "ALLOCATION", "CLEANUP", "BOTH", "EXISTING_HEADROOM", "NONE"
    }:
        _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")
    if value.evidence_role in {
        "R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP"
    }:
        if (
            value.raw_retirement_status
            != "PASS_OLDER_RAW_DUPLICATES_RETIRED"
            or SHA_RE.fullmatch(value.raw_retirement_receipt_sha256) is None
            or (
                value.evidence_role == "R5E_R8_POST_CLEANUP"
                and (
                    value.raw_retirement_receipt_sha256
                    != RETIREMENT_EVENT_RECEIPT_SHA256
                    or value.capacity_gain_source not in {"CLEANUP", "BOTH"}
                )
            )
        ):
            _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")
    elif value.evidence_role == "R5E_R2_PRE_ACTION":
        expected = (
            "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
            if value.capture.observation.get("status")
            == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
            else "NOT_APPLICABLE_CLEANUP_SKIPPED"
        )
        if (
            value.raw_retirement_status != expected
            or value.raw_retirement_receipt_sha256 != expected
        ):
            _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")
    elif (
        value.raw_retirement_status != "NOT_APPLICABLE_CLEANUP_SKIPPED"
        or value.raw_retirement_receipt_sha256
        != "NOT_APPLICABLE_CLEANUP_SKIPPED"
    ):
        _fail("FULL_SEQUENTIAL_CAPACITY_ADMISSION_INVALID")


def validate_installation() -> Mapping[str, Any]:
    required = (
        Path(__file__).resolve(),
        ARRAY_RUNNER_PATH,
        EXECUTION_NODE_PROBE_RUNNER_PATH,
        SCRIPT_ROOT / "scc_run_lvef_c3_full_finalizer.sh",
        SCRIPT_ROOT / "scc_submit_lvef_c3_full_sequential.sh",
        SCRIPT_ROOT / "lvef_c3_orchestration_core.py",
        SCRIPT_ROOT / "capture_lvef_c3_post_reallocation_capacity.py",
        SCRIPT_ROOT / "lvef_c3_production_stages.py",
        SCRIPT_ROOT / "preserve_lvef_c3_production_batch.py",
        SCRIPT_ROOT / "retire_lvef_c3_extracted_cache_v2.py",
        SCRIPT_ROOT / "retire_lvef_c3_older_raw_duplicates.py",
        SCRIPT_ROOT / "finalize_lvef_c3_production.py",
        SCRIPT_ROOT / "replay_lvef_c3_failed_extraction_one_object.py",
        OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_PATH,
    )
    if any(path.is_symlink() or not path.is_file() for path in required):
        _fail("FULL_SEQUENTIAL_TRACKED_CONTROL_MISSING")
    policy_payload = minimal._read_regular(
        OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_PATH
    )
    if (
        hashlib.sha256(policy_payload).hexdigest()
        != core.OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_SHA256
    ):
        _fail("FULL_SEQUENTIAL_TECHNICAL_DISPOSITION_V2_POLICY_INVALID")
    try:
        policy_value = core.load_strict_json(
            OBJECT_TECHNICAL_DISPOSITION_POLICY_V2_PATH
        )
        core.validate_object_technical_disposition_policy_v2(policy_value)
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_TECHNICAL_DISPOSITION_V2_POLICY_INVALID"
        ) from exc
    head = _current_commit()
    minimal._validate_two_runtime_installation(repository=REPOSITORY_ROOT)
    return {
        "status": "PASS_FULL_C3_INSTALLATION",
        "governing_commit": head,
        "echoprime_runtime": "PASS",
        "crc32c_external_runtime": "PASS",
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
    }


def preflight_full(
    *,
    dependencies: FullDependencies | None = None,
    _validated_dynamic_capture: (
        capacity.DynamicSuccessorCapacityCapture | None
    ) = None,
    _validated_capacity_admission: CapacityAdmission | None = None,
    _capture_reuse_token: object | None = None,
) -> Mapping[str, Any]:
    dependency = resolve_dependencies(dependencies)
    if (
        dependency.capacity_probe is not None
        and not dependency.test_only_synthetic_full_scope
    ):
        _fail("FULL_SEQUENTIAL_SYNTHETIC_CAPACITY_NOT_AUTHORIZED")
    if (
        (
            _validated_dynamic_capture is not None
            or _validated_capacity_admission is not None
        )
        and (
            dependency.capacity_probe is not None
            or _capture_reuse_token is not _DYNAMIC_CAPTURE_REUSE_TOKEN
            or (
                _validated_dynamic_capture is not None
                and _validated_capacity_admission is not None
            )
        )
    ):
        _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SOURCE_INVALID")
    installation = validate_installation()
    run = build_full_run()
    if os.path.lexists(run.attempt_root):
        _fail("FULL_SEQUENTIAL_ATTEMPT_ALREADY_EXISTS")
    canary_evidence = _validate_completed_canary_evidence()
    dependency.environment_validator(
        run.authority.environment_receipt,
        expected_environment_receipt_sha256=(
            run.runtime_authority["environment_receipt_sha256"]
        ),
        scientific_governing_commit=run.authority.governing_commit,
    )
    cache_inventory = _extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if cache_inventory.active != 0:
        _fail("FULL_SEQUENTIAL_ACTIVE_EXTRACTION_CACHE_PRESENT")
    if (
        cache_inventory.preserved_terminal_failed
        != SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    ):
        _fail("FULL_SEQUENTIAL_TERMINAL_FAILURE_CACHE_AUTHORITY_INVALID")
    claim_path = run.attempt_root / "full_submission_claim.restricted.json"
    attempt_absent = not os.path.lexists(run.attempt_root)
    claim_absent = not os.path.lexists(claim_path)
    if not attempt_absent or not claim_absent:
        _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
    try:
        if _validated_capacity_admission is not None:
            admission = _validated_capacity_admission
        elif _validated_dynamic_capture is not None:
            admission = CapacityAdmission(
                capture=_validated_dynamic_capture,
                evidence_role="R5B_HISTORICAL",
                capacity_gain_source=_capacity_gain_source(
                    _validated_dynamic_capture.observation,
                    evidence_role="R5B_HISTORICAL",
                ),
                raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
                raw_retirement_receipt_sha256=(
                    "NOT_APPLICABLE_CLEANUP_SKIPPED"
                ),
            )
        elif dependency.capacity_probe is not None:
            synthetic_capture = dependency.capacity_probe()
            admission = CapacityAdmission(
                capture=synthetic_capture,
                evidence_role="R5B_HISTORICAL",
                capacity_gain_source=_capacity_gain_source(
                    synthetic_capture.observation,
                    evidence_role="R5B_HISTORICAL",
                ),
                raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
                raw_retirement_receipt_sha256=(
                    "NOT_APPLICABLE_CLEANUP_SKIPPED"
                ),
            )
        else:
            admission = _load_fixed_capacity_admission(run)
        _validate_capacity_admission(admission)
        observed_capture = admission.capture
        if not isinstance(
            observed_capture, capacity.DynamicSuccessorCapacityCapture
        ):
            _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SOURCE_INVALID")
        if not dependency.test_only_synthetic_full_scope:
            observed_capture = capacity.validate_dynamic_successor_capacity_capture(
                observed_capture,
                validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
                expected_governing_commit=run.authority.governing_commit,
            )
        observed_capacity = dict(observed_capture.observation)
        capacity.validate_dynamic_successor_capacity_observation(
            observed_capacity,
            expected_governing_commit=run.authority.governing_commit,
            expected_receipt_payload=observed_capture.receipt_payload,
        )
    except capacity.PostReallocationCapacityError as exc:
        raise FullSequentialError(exc.code) from exc
    if observed_capacity.get("status") != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_NOT_PASS")
    if (
        os.path.lexists(run.attempt_root)
        or os.path.lexists(claim_path)
        or observed_capacity["successor_attempt_root_absent"] is not True
        or observed_capacity["successor_claim_absent"] is not True
    ):
        _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
    successor_capacity = build_successor_capacity_authority(
        observed_capture, cache_inventory
    )
    aggregate = core.aggregate_batch_plan(
        run.plan, requirements=run.requirements
    )
    return {
        "status": "PASS_FULL_C3_NO_BODY_PREFLIGHT",
        "governing_commit": installation["governing_commit"],
        "attempt_id": run.attempt_id,
        "selected_studies": aggregate["selected_studies"],
        "selected_subjects": aggregate["selected_subjects"],
        "normalized_source_objects": aggregate["normalized_source_objects"],
        "selected_source_bytes": aggregate["selected_source_bytes"],
        "batch_count": aggregate["batch_count"],
        "expected_study_embeddings": 4525,
        "expected_no_cine_studies": 5,
        "active_extraction_caches": cache_inventory.active,
        "preserved_terminal_failed_extraction_caches": (
            cache_inventory.preserved_terminal_failed
        ),
        "storage_reserve": "PASS",
        "echoprime_runtime": "PASS",
        "crc32c_external_runtime": "PASS",
        "completed_canary_evidence": "PASS",
        "completed_canary_evidence_sha256": canary_evidence,
        "task_mappings": [
            {
                "task_id": index,
                "batch_id": task_to_batch(index, run.plan),
                "n_studies": run.plan["batches"][index - 1]["n_studies"],
                "n_objects": run.plan["batches"][index - 1]["n_objects"],
                "source_bytes": run.plan["batches"][index - 1]["source_bytes"],
            }
            for index in range(1, 20)
        ],
        "capacity": observed_capacity,
        "successor_capacity": successor_capacity,
        "capacity_evidence_role": admission.evidence_role,
        "capacity_gain_source": admission.capacity_gain_source,
        "raw_retirement_status": admission.raw_retirement_status,
        "raw_retirement_receipt_sha256": (
            admission.raw_retirement_receipt_sha256
        ),
        "bucket_listing_requests": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "writes_performed": 0,
    }


def _load_r5e_r8_integrated_module(
    module_name: str, relative_path: str
) -> Any:
    """Load one fixed dependency-light acceptance module from this checkout."""

    path = REPOSITORY_ROOT / relative_path
    if path.is_symlink() or not path.is_file():
        _fail("FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID")
    specification = spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        _fail("FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID")
    module = module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException as exc:
        sys.modules.pop(module_name, None)
        raise FullSequentialError(
            "FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID"
        ) from exc
    return module


def _run_r5e_r8_integrated_no_body_acceptance() -> Mapping[str, Any]:
    """Run the exact synthetic gates, then one live no-body preflight.

    This is intentionally the implementation behind the scheduler's sole
    ``--preflight-only`` call.  Capacity sealing does not call it, and claim
    creation does not repeat it.
    """

    # Never execute checkout-provided acceptance code before proving that the
    # branch, HEAD, cached origin, and tracked worktree are the exact current
    # authority.  ``preflight_full`` repeats this gate after the synthetic
    # checks to close the intervening TOCTOU window before live admission.
    _current_commit()
    canonical_name = "lvef_c3_full_sequential"
    previous_module = sys.modules.get(canonical_name)
    sys.modules[canonical_name] = sys.modules[__name__]
    try:
        focused = _load_r5e_r8_integrated_module(
            "lvef_c3_r8_integrated_technical",
            "tests/test_lvef_c3_object_technical_disposition.py",
        )
        cohort = _load_r5e_r8_integrated_module(
            "lvef_c3_r8_integrated_cohort",
            "tests/test_lvef_c3_production_stages_and_finalizer.py",
        )
        checks = (
            (
                focused,
                "test_r5e_v2_policy_is_closed_and_v1_policy_remains_byte_valid",
            ),
            (
                focused,
                "test_case_02_r4d2c_is_bound_provenance_but_unreachable_to_classifier",
            ),
            (
                focused,
                "test_case_01_exact_batch3_pattern_is_one_disposition_and_one_affected_study",
            ),
            (
                cohort,
                "test_production_finalizer_reconciles_exact_cohort_and_five_no_cine",
            ),
        )
        for module, name in checks:
            check = getattr(module, name, None)
            if not callable(check):
                _fail("FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID")
            check()
        result = preflight_full()
    except FullSequentialError:
        raise
    except BaseException as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID"
        ) from exc
    finally:
        if previous_module is None:
            sys.modules.pop(canonical_name, None)
        else:
            sys.modules[canonical_name] = previous_module
    if (
        result.get("status") != "PASS_FULL_C3_NO_BODY_PREFLIGHT"
        or result.get("capacity_evidence_role") != "R5E_R8_POST_CLEANUP"
        or result.get("capacity_gain_source") not in {"CLEANUP", "BOTH"}
        or result.get("raw_retirement_status") != RETIREMENT_EVENT_STATUS
        or result.get("raw_retirement_receipt_sha256")
        != RETIREMENT_EVENT_RECEIPT_SHA256
        or any(
            result.get(key) != 0
            for key in (
                "bucket_listing_requests",
                "cloud_requests",
                "qsub_submissions",
                "dicom_body_reads",
                "gpu_executions",
                "writes_performed",
            )
        )
    ):
        _fail("FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID")
    run = build_full_run()
    if (
        os.path.lexists(run.attempt_root)
        or os.path.lexists(
            run.attempt_root / "full_submission_claim.restricted.json"
        )
    ):
        _fail("FULL_SEQUENTIAL_INTEGRATED_NO_BODY_ACCEPTANCE_INVALID")
    return result


def run_dynamic_successor_capacity_seal(
    *,
    capture: capacity.DynamicSuccessorCapacityCapture | None = None,
    dependencies: FullDependencies | None = None,
    evidence_role: str = "R5B_HISTORICAL",
) -> Mapping[str, Any]:
    """Seal exactly one observation, then conditionally reuse it in preflight."""

    dependency = resolve_dependencies(dependencies)
    evidence_names = {
        "R5B_HISTORICAL": (
            capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME,
            capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME,
        ),
        "R5E_PRE_CLEANUP": (
            capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
        "R5E_R2_PRE_ACTION": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
        ),
        "R5E_POST_CLEANUP": (
            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
        "R5E_R8_POST_CLEANUP": (
            capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ),
    }
    if evidence_role not in evidence_names:
        _fail("FULL_SEQUENTIAL_CAPACITY_EVIDENCE_ROLE_INVALID")
    if (
        capture is not None
        and not dependency.test_only_synthetic_full_scope
    ):
        _fail("FULL_SEQUENTIAL_SYNTHETIC_CAPACITY_NOT_AUTHORIZED")
    installation = validate_installation()
    run = build_full_run()
    if os.path.lexists(run.attempt_root):
        _fail("FULL_SEQUENTIAL_ATTEMPT_ALREADY_EXISTS")
    _validate_completed_canary_evidence()
    dependency.environment_validator(
        run.authority.environment_receipt,
        expected_environment_receipt_sha256=(
            run.runtime_authority["environment_receipt_sha256"]
        ),
        scientific_governing_commit=run.authority.governing_commit,
    )
    cache_inventory = _extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if (
        cache_inventory.active != 0
        or cache_inventory.preserved_terminal_failed
        != SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    ):
        _fail("DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID")
    evidence_root = run.production_root / "owner_private"
    _validate_private_directory(evidence_root)
    try:
        capacity.validate_dynamic_successor_capacity_safe_export_policy(
            DYNAMIC_CAPACITY_SAFE_EXPORT_POLICY_PATH
        )
    except capacity.PostReallocationCapacityError as exc:
        raise FullSequentialError(exc.code) from exc
    restricted_receipt_path = evidence_root / evidence_names[evidence_role][0]
    aggregate_summary_path = evidence_root / evidence_names[evidence_role][1]
    # Both fixed evidence-leaf collision gates precede the only live probe.
    # Any race after this gate is caught again by the O_EXCL publisher.
    if (
        os.path.lexists(restricted_receipt_path)
        or os.path.lexists(aggregate_summary_path)
    ):
        _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
    claim_path = run.attempt_root / "full_submission_claim.restricted.json"
    attempt_absent = not os.path.lexists(run.attempt_root)
    claim_absent = not os.path.lexists(claim_path)
    if not attempt_absent or not claim_absent:
        _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
    retirement_status = "NOT_APPLICABLE_CLEANUP_SKIPPED"
    retirement_sha = "NOT_APPLICABLE_CLEANUP_SKIPPED"
    pre_cleanup_observation: Mapping[str, Any] | None = None
    if evidence_role in {"R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP"}:
        # The former generic ``require_pass=False`` path still imposed the
        # current commit and live clock on this immutable historical event.
        historical = _load_historical_r5e_r2_pre_action_capacity()
        retired = _load_completed_retirement_event()
        if evidence_role == "R5E_R8_POST_CLEANUP":
            historical_post = _load_historical_r5e_post_cleanup_capacity()
            if (
                _capacity_gain_source(
                    historical_post.capture.observation,
                    evidence_role="R5E_POST_CLEANUP",
                    pre_cleanup_observation=historical.capture.observation,
                )
                != HISTORICAL_R5E_POST_CLEANUP_GAIN_SOURCE
            ):
                _fail("FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID")
        if retired["pre_cleanup_capacity_authority"] != historical.authority:
            _fail("FULL_SEQUENTIAL_RETIREMENT_STATE_INVALID")
        retirement_status = str(retired["status"])
        retirement_sha = str(retired["receipt_sha256"])
        pre_cleanup_observation = historical.capture.observation
    try:
        observed_capture = capture or (
            capacity.probe_dynamic_successor_capacity_observation(
                governing_commit=run.authority.governing_commit,
                active_extraction_caches=cache_inventory.active,
                preserved_terminal_failed_extraction_caches=(
                    cache_inventory.preserved_terminal_failed
                ),
                successor_attempt_root_absent=attempt_absent,
                successor_claim_absent=claim_absent,
            )
        )
        if not isinstance(
            observed_capture, capacity.DynamicSuccessorCapacityCapture
        ):
            _fail("FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SOURCE_INVALID")
        observation = capacity.validate_dynamic_successor_capacity_observation(
            observed_capture.observation,
            expected_governing_commit=run.authority.governing_commit,
            expected_receipt_payload=observed_capture.receipt_payload,
        )
        # A blocked observation is still publishable evidence, but it is not
        # live admission authority.  The closed LIVE context is PASS-only.
        if (
            observation["status"]
            == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
            and not dependency.test_only_synthetic_full_scope
        ):
            observed_capture = capacity.validate_dynamic_successor_capacity_capture(
                observed_capture,
                validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
                expected_governing_commit=run.authority.governing_commit,
            )
        if (
            observation["active_extraction_caches"]
            != cache_inventory.active
            or observation["preserved_terminal_failed_extraction_caches"]
            != cache_inventory.preserved_terminal_failed
        ):
            _fail("DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID")
        if (
            observation["successor_attempt_root_absent"] is not True
            or observation["successor_claim_absent"] is not True
        ):
            _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
        if (
            evidence_role == "R5E_R2_PRE_ACTION"
            and observation["status"]
            == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        ):
            retirement_status = "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
            retirement_sha = "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
        if (
            os.path.lexists(run.attempt_root)
            or os.path.lexists(
                run.attempt_root
                / "full_submission_claim.restricted.json"
            )
            or os.path.lexists(restricted_receipt_path)
            or os.path.lexists(aggregate_summary_path)
        ):
            _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
        artifacts = capacity.publish_dynamic_successor_capacity_capture(
            observed_capture,
            restricted_receipt_path=restricted_receipt_path,
            aggregate_summary_path=aggregate_summary_path,
            safe_export_policy_path=DYNAMIC_CAPACITY_SAFE_EXPORT_POLICY_PATH,
        )
    except capacity.PostReallocationCapacityError as exc:
        if evidence_role == "R5E_R8_POST_CLEANUP":
            code = (
                "FULL_SEQUENTIAL_CURRENT_CAPACITY_STALE"
                if exc.code == "DYNAMIC_CAPACITY_RECEIPT_STALE"
                else "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
            )
            raise FullSequentialError(code) from exc
        if evidence_role == "R5E_POST_CLEANUP":
            code = (
                "FULL_SEQUENTIAL_CURRENT_POST_CLEANUP_CAPACITY_STALE"
                if exc.code == "DYNAMIC_CAPACITY_RECEIPT_STALE"
                else "FULL_SEQUENTIAL_CURRENT_POST_CLEANUP_CAPACITY_INVALID"
            )
            raise FullSequentialError(code) from exc
        raise FullSequentialError(exc.code) from exc

    integrated: Mapping[str, Any] | None = None
    gain_source = _capacity_gain_source(
        observation,
        evidence_role=evidence_role,
        pre_cleanup_observation=pre_cleanup_observation,
    )
    admission = CapacityAdmission(
        capture=observed_capture,
        evidence_role=evidence_role,
        capacity_gain_source=gain_source,
        raw_retirement_status=retirement_status,
        raw_retirement_receipt_sha256=retirement_sha,
    )
    if (
        evidence_role != "R5E_R8_POST_CLEANUP"
        or observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    ):
        _validate_capacity_admission(admission)
    if (
        observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        and evidence_role != "R5E_R8_POST_CLEANUP"
    ):
        integrated = preflight_full(
            dependencies=replace(dependency, capacity_probe=None),
            _validated_capacity_admission=admission,
            _capture_reuse_token=_DYNAMIC_CAPTURE_REUSE_TOKEN,
        )
    return {
        "status": observation["status"],
        "governing_commit": installation["governing_commit"],
        "storage_allocation_visible": observation[
            "storage_allocation_visible"
        ],
        "capacity_evidence_role": evidence_role,
        "capacity_gain_source": gain_source,
        "raw_retirement_status": retirement_status,
        "raw_retirement_receipt_sha256": retirement_sha,
        "minimum_additional_quota_bytes": observation[
            "minimum_additional_quota_bytes"
        ],
        "minimum_additional_physical_bytes": observation[
            "minimum_additional_physical_bytes"
        ],
        "minimum_additional_file_slots": observation[
            "minimum_additional_file_slots"
        ],
        "restricted_receipt_basename": artifacts[
            "restricted_receipt_basename"
        ],
        "restricted_receipt_bytes": artifacts["restricted_receipt_bytes"],
        "restricted_receipt_sha256": artifacts[
            "restricted_receipt_sha256"
        ],
        "aggregate_summary_basename": artifacts[
            "aggregate_summary_basename"
        ],
        "aggregate_summary_bytes": artifacts["aggregate_summary_bytes"],
        "aggregate_summary_sha256": artifacts["aggregate_summary_sha256"],
        "integrated_no_body_preflight": (
            "PASS" if integrated is not None else "NOT_RUN"
        ),
        "integrated_preflight": integrated,
        "observation": observation,
        "successor_attempt_root_absent": observation[
            "successor_attempt_root_absent"
        ],
        "successor_claim_absent": observation["successor_claim_absent"],
        **{
            key: observation[key]
            for key in capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS
        },
        "writes_performed_by_observation": 0,
        "evidence_files_written": 2,
    }


def run_r5e_r8_post_cleanup_capacity_seal() -> Mapping[str, Any]:
    """Publish the sole append-only R8 current-admission pair."""

    return run_dynamic_successor_capacity_seal(
        evidence_role="R5E_R8_POST_CLEANUP"
    )


def format_dynamic_successor_capacity_seal_report(
    value: Mapping[str, Any],
) -> tuple[str, ...]:
    """Project the sealed dynamic result to closed aggregate-safe markers."""

    expected_keys = {
        "status",
        "governing_commit",
        "storage_allocation_visible",
        "capacity_evidence_role",
        "capacity_gain_source",
        "raw_retirement_status",
        "raw_retirement_receipt_sha256",
        "minimum_additional_quota_bytes",
        "minimum_additional_physical_bytes",
        "minimum_additional_file_slots",
        "restricted_receipt_basename",
        "restricted_receipt_bytes",
        "restricted_receipt_sha256",
        "aggregate_summary_basename",
        "aggregate_summary_bytes",
        "aggregate_summary_sha256",
        "integrated_no_body_preflight",
        "integrated_preflight",
        "observation",
        "successor_attempt_root_absent",
        "successor_claim_absent",
        "writes_performed_by_observation",
        "evidence_files_written",
        *capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS,
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    observation = value.get("observation")
    if not isinstance(observation, Mapping):
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    try:
        capacity.validate_dynamic_successor_capacity_observation(
            observation,
            expected_governing_commit=value.get("governing_commit"),
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID"
        ) from exc
    summary_payload = capacity._canonical(observation)
    if (
        value.get("status") != observation["status"]
        or value.get("capacity_evidence_role") != "R5B_HISTORICAL"
        or value.get("capacity_gain_source")
        != _capacity_gain_source(
            observation, evidence_role="R5B_HISTORICAL"
        )
        or value.get("raw_retirement_status")
        != "NOT_APPLICABLE_CLEANUP_SKIPPED"
        or value.get("raw_retirement_receipt_sha256")
        != "NOT_APPLICABLE_CLEANUP_SKIPPED"
        or value.get("storage_allocation_visible")
        is not observation["storage_allocation_visible"]
        or value.get("restricted_receipt_basename")
        != capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        or value.get("aggregate_summary_basename")
        != capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        or value.get("restricted_receipt_bytes")
        != observation["restricted_receipt_size_bytes"]
        or value.get("restricted_receipt_sha256")
        != observation["restricted_receipt_sha256"]
        or value.get("minimum_additional_quota_bytes")
        != observation["minimum_additional_quota_bytes"]
        or value.get("minimum_additional_physical_bytes")
        != observation["minimum_additional_physical_bytes"]
        or value.get("minimum_additional_file_slots")
        != observation["minimum_additional_file_slots"]
        or value.get("successor_attempt_root_absent")
        is not observation["successor_attempt_root_absent"]
        or value.get("successor_claim_absent")
        is not observation["successor_claim_absent"]
        or value.get("aggregate_summary_bytes") != len(summary_payload)
        or value.get("aggregate_summary_sha256")
        != hashlib.sha256(summary_payload).hexdigest()
        or any(value.get(key) != 0 for key in capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS)
        or value.get("writes_performed_by_observation") != 0
        or value.get("evidence_files_written") != 2
    ):
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    expected_integrated = (
        "PASS"
        if observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        else "NOT_RUN"
    )
    integrated = value.get("integrated_preflight")
    if value.get("integrated_no_body_preflight") != expected_integrated:
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    if expected_integrated == "PASS":
        if (
            not isinstance(integrated, Mapping)
            or integrated.get("governing_commit")
            != value.get("governing_commit")
            or integrated.get("capacity") != observation
        ):
            _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
        try:
            format_preflight_report(integrated)
        except Exception as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID"
            ) from exc
    elif integrated is not None:
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    next_action = {
        capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING: (
            "WAIT_FOR_STORAGE_ALLOCATION_VISIBILITY"
        ),
        capacity.DYNAMIC_SUCCESSOR_STATUS_BLOCKED: (
            "SATISFY_REPORTED_CAPACITY_MINIMUMS"
        ),
        capacity.DYNAMIC_SUCCESSOR_STATUS_PASS: (
            "REVIEW_SEPARATE_FRESH_SUCCESSOR_AUTHORIZATION"
        ),
    }[observation["status"]]
    fields = (
        (
            "R5B_R1_STARTING_COMMIT",
            "738195d1fa4ae4e2a23bbd9482b5acd008317eb4",
        ),
        ("R5B_R1_ENDING_COMMIT", value["governing_commit"]),
        ("STATIC_CAPACITY_INCOMPATIBILITY", "CONFIRMED"),
        ("HISTORICAL_STATIC_CAPACITY_PATH_UNCHANGED", "YES"),
        ("DYNAMIC_SUCCESSOR_CAPACITY_AUTHORITY", "PASS"),
        ("HARD_CODED_ANTICIPATED_QUOTA_ADDED", "NO"),
        ("CURRENT_CAPACITY_STATUS", observation["status"]),
        ("CURRENT_QUOTA_BYTES", observation["live_research_quota_bytes"]),
        ("CURRENT_USAGE_BYTES", observation["live_research_usage_bytes"]),
        (
            "CURRENT_PHYSICAL_AVAILABLE_BYTES",
            observation["live_research_filesystem_available_bytes"],
        ),
        ("CURRENT_REMAINING_FILE_SLOTS", observation["remaining_file_slots"]),
        (
            "FRESH_SUCCESSOR_INCREMENT_BYTES",
            observation["fresh_successor_increment_bytes"],
        ),
        (
            "QUOTA_MARGIN_BEYOND_200GB_RESERVE_BYTES",
            observation["quota_margin_beyond_reserve_bytes"],
        ),
        (
            "PHYSICAL_MARGIN_BEYOND_200GB_RESERVE_BYTES",
            observation["physical_margin_beyond_reserve_bytes"],
        ),
        ("SCC_INTEGRATED_NO_BODY_ACCEPTANCE", expected_integrated),
        ("R5D_RETIREMENT_ROUTE", "CLOSED_COMPLEXITY_DISPROPORTIONATE"),
        ("PREFIX_ADOPTION_IMPLEMENTED", "NO"),
        ("CROSS_ATTEMPT_RECOVERY_IMPLEMENTED", "NO"),
        ("DYNAMIC_SUCCESSOR_CAPACITY_STATUS", observation["status"]),
        (
            "STORAGE_ALLOCATION_VISIBLE",
            "YES" if observation["storage_allocation_visible"] else "NO",
        ),
        ("LIVE_RESEARCH_QUOTA_BYTES", observation["live_research_quota_bytes"]),
        ("LIVE_RESEARCH_USAGE_BYTES", observation["live_research_usage_bytes"]),
        ("LIVE_RESEARCH_FILE_QUOTA", observation["live_research_file_quota"]),
        ("LIVE_RESEARCH_FILES_USED", observation["live_research_files_used"]),
        (
            "LIVE_RESEARCH_FILESYSTEM_AVAILABLE_BYTES",
            observation["live_research_filesystem_available_bytes"],
        ),
        (
            "PROJECTED_FRESH_SUCCESSOR_PEAK_BYTES",
            observation["projected_fresh_successor_peak_bytes"],
        ),
        ("QUOTA_SLACK_AFTER_PEAK_BYTES", observation["quota_slack_after_peak_bytes"]),
        (
            "PHYSICAL_SLACK_AFTER_PEAK_BYTES",
            observation["physical_slack_after_peak_bytes"],
        ),
        (
            "QUOTA_MARGIN_BEYOND_RESERVE_BYTES",
            observation["quota_margin_beyond_reserve_bytes"],
        ),
        (
            "PHYSICAL_MARGIN_BEYOND_RESERVE_BYTES",
            observation["physical_margin_beyond_reserve_bytes"],
        ),
        ("REMAINING_FILE_SLOTS", observation["remaining_file_slots"]),
        (
            "FILE_SLOT_MARGIN_AFTER_DEMAND",
            observation["file_slot_margin_after_demand"],
        ),
        (
            "MINIMUM_ADDITIONAL_QUOTA_BYTES",
            observation["minimum_additional_quota_bytes"],
        ),
        (
            "MINIMUM_ADDITIONAL_PHYSICAL_BYTES",
            observation["minimum_additional_physical_bytes"],
        ),
        (
            "MINIMUM_ADDITIONAL_FILE_SLOTS",
            observation["minimum_additional_file_slots"],
        ),
        ("ACTIVE_EXTRACTION_CACHES", observation["active_extraction_caches"]),
        (
            "PRESERVED_TERMINAL_FAILED_EXTRACTION_CACHES",
            observation["preserved_terminal_failed_extraction_caches"],
        ),
        ("SUCCESSOR_ATTEMPT_ROOT_ABSENT", "YES"),
        ("SUCCESSOR_CLAIM_ABSENT", "YES"),
        ("SUCCESSOR_ATTEMPT_ROOT_CREATED", "NO"),
        ("SUCCESSOR_CLAIM_CREATED", "NO"),
        ("CAPACITY_RECEIPT_BASENAME", value["restricted_receipt_basename"]),
        ("CAPACITY_RECEIPT_BYTES", value["restricted_receipt_bytes"]),
        ("CAPACITY_RECEIPT_SHA256", value["restricted_receipt_sha256"]),
        ("CAPACITY_SUMMARY_BASENAME", value["aggregate_summary_basename"]),
        ("CAPACITY_SUMMARY_BYTES", value["aggregate_summary_bytes"]),
        ("CAPACITY_SUMMARY_SHA256", value["aggregate_summary_sha256"]),
        ("INTEGRATED_NO_BODY_PREFLIGHT", expected_integrated),
        ("NEW_CLOUD_REQUESTS", 0),
        ("NEW_QSUB_SUBMISSIONS", 0),
        ("NEW_DICOM_BODY_READS", 0),
        ("NEW_NPZ_BODY_READS", 0),
        ("NEW_GPU_EXECUTIONS", 0),
        ("NEW_ECHOPRIME_EXECUTIONS", 0),
        ("NEW_EMBEDDING_GENERATIONS", 0),
        ("NEW_MODEL_FITTING", 0),
        ("NEW_PREDICTION_GENERATION", 0),
        ("NEW_CONFIRMATORY_PERFORMANCE_ACCESSES", 0),
        ("NEW_FILES_MOVED", 0),
        ("NEW_FILES_DELETED", 0),
        ("NEW_WRITES_PERFORMED_BY_OBSERVATION", 0),
        ("EVIDENCE_FILES_WRITTEN", 2),
        ("CONFIRMATORY_PERFORMANCE_ACCESSED", "NO"),
        (
            "FULL_C3_STATUS",
            "NO_GO_PENDING_DYNAMIC_CAPACITY_SEAL_AND_"
            "SEPARATE_SUCCESSOR_AUTHORIZATION",
        ),
        ("EXACT_NEXT_ACTION", next_action),
        ("CLOUD_REQUESTS", 0),
        ("QSUB_SUBMISSIONS", 0),
        ("DICOM_BODY_READS", 0),
        ("NPZ_BODY_READS", 0),
        ("GPU_EXECUTIONS", 0),
        ("ECHOPRIME_EXECUTIONS", 0),
        ("EMBEDDING_GENERATIONS", 0),
        ("MODEL_FITTING", 0),
        ("PREDICTION_GENERATION", 0),
        ("CONFIRMATORY_PERFORMANCE_ACCESSES", 0),
        ("FILES_MOVED", 0),
        ("FILES_DELETED", 0),
    )
    if len(fields) != len({name for name, _ in fields}):
        _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
    return tuple(f"{name}={item}" for name, item in fields)


def format_preflight_report(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the closed aggregate-safe no-body acceptance marker surface."""

    scalar_expectations = {
        "status": "PASS_FULL_C3_NO_BODY_PREFLIGHT",
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "normalized_source_objects": 335984,
        "selected_source_bytes": 1216569133322,
        "batch_count": 19,
        "expected_study_embeddings": 4525,
        "expected_no_cine_studies": 5,
        "active_extraction_caches": 0,
        "storage_reserve": "PASS",
        "echoprime_runtime": "PASS",
        "crc32c_external_runtime": "PASS",
        "completed_canary_evidence": "PASS",
        "bucket_listing_requests": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "writes_performed": 0,
    }
    if any(value.get(key) != expected for key, expected in scalar_expectations.items()):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    preserved_failed_caches = value.get(
        "preserved_terminal_failed_extraction_caches"
    )
    if (
        not isinstance(preserved_failed_caches, int)
        or isinstance(preserved_failed_caches, bool)
        or preserved_failed_caches != SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    ):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    governing_commit = value.get("governing_commit")
    if not isinstance(governing_commit, str) or COMMIT_RE.fullmatch(governing_commit) is None:
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    capacity_value = value.get("capacity")
    if not isinstance(capacity_value, Mapping):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    try:
        capacity.validate_dynamic_successor_capacity_observation(
            capacity_value,
            expected_governing_commit=governing_commit,
        )
    except Exception as exc:
        raise FullSequentialError("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID") from exc
    successor_capacity = value.get("successor_capacity")
    if not isinstance(successor_capacity, Mapping):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    try:
        validate_successor_capacity_authority(successor_capacity)
    except FullSequentialError as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID"
        ) from exc
    if (
        successor_capacity.get("source_capacity_authority_sha256")
        != core.canonical_json_sha256(capacity_value)
        or successor_capacity.get("source_dynamic_receipt_bytes")
        != capacity_value.get("restricted_receipt_size_bytes")
        or successor_capacity.get("source_dynamic_receipt_sha256")
        != capacity_value.get("restricted_receipt_sha256")
        or successor_capacity.get("preserved_terminal_failed_extraction_caches")
        != preserved_failed_caches
    ):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    mappings = value.get("task_mappings")
    if not isinstance(mappings, list) or len(mappings) != 19:
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    expected_mapping_keys = {
        "task_id", "batch_id", "n_studies", "n_objects", "source_bytes"
    }
    mapping_lines: list[str] = []
    totals = {"n_studies": 0, "n_objects": 0, "source_bytes": 0}
    for ordinal, mapping in enumerate(mappings, start=1):
        if (
            not isinstance(mapping, Mapping)
            or set(mapping) != expected_mapping_keys
            or mapping.get("task_id") != ordinal
            or mapping.get("batch_id") != f"c3_batch_{ordinal - 1:03d}"
            or mapping.get("n_studies") != (250 if ordinal < 19 else 30)
            or any(
                not isinstance(mapping.get(key), int)
                or isinstance(mapping.get(key), bool)
                or int(mapping[key]) <= 0
                for key in ("n_objects", "source_bytes")
            )
        ):
            _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
        for key in totals:
            totals[key] += int(mapping[key])
        prefix = f"FULL_C3_TASK_{ordinal:02d}"
        mapping_lines.extend(
            (
                f"{prefix}_BATCH={mapping['batch_id']}",
                f"{prefix}_STUDIES={mapping['n_studies']}",
                f"{prefix}_OBJECTS={mapping['n_objects']}",
                f"{prefix}_BYTES={mapping['source_bytes']}",
            )
        )
    if totals != {
        "n_studies": 4530,
        "n_objects": 335984,
        "source_bytes": 1216569133322,
    }:
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    lines = [
        "FULL_C3_NO_BODY_PREFLIGHT=PASS",
        f"FULL_C3_GOVERNING_COMMIT={governing_commit}",
        "FULL_C3_SELECTED_STUDIES=4530",
        "FULL_C3_SELECTED_SUBJECTS=4530",
        "FULL_C3_DECLARED_OBJECTS=335984",
        "FULL_C3_DECLARED_BYTES=1216569133322",
        "FULL_C3_BATCHES=19",
        "FULL_C3_EXPECTED_STUDY_EMBEDDINGS=4525",
        "FULL_C3_EXPECTED_NO_CINE_STUDIES=5",
        "FULL_C3_ACTIVE_EXTRACTION_CACHES=0",
        "FULL_C3_PRESERVED_TERMINAL_FAILED_EXTRACTION_CACHES="
        f"{preserved_failed_caches}",
        "FULL_C3_STORAGE_RESERVE=PASS",
        "ECHOPRIME_RUNTIME=PASS",
        "CRC32C_EXTERNAL_RUNTIME=PASS",
        "COMPLETED_CANARY_EVIDENCE=PASS",
        "FULL_C3_CAPACITY=PASS",
        "FULL_C3_FRESH_SUCCESSOR_CAPACITY=PASS",
        "FULL_C3_SUCCESSOR_INCREMENT_BYTES="
        f"{successor_capacity['successor_increment_bytes']}",
        "FULL_C3_SUCCESSOR_PROJECTED_TOTAL_BYTES="
        f"{successor_capacity['projected_total_research_usage_bytes']}",
        "FULL_C3_SUCCESSOR_QUOTA_REMAINING_BYTES="
        f"{successor_capacity['quota_remaining_after_successor_bytes']}",
        "FULL_C3_SUCCESSOR_PHYSICAL_REMAINING_BYTES="
        f"{successor_capacity['physical_remaining_after_successor_bytes']}",
        "FULL_C3_SOURCE_PLAN_VALIDATION=PASS",
        "FULL_C3_TASK_MAPPING_VALIDATION=PASS",
        "FULL_C3_TASK_MAPPINGS=19",
        "FULL_C3_RESEARCH_QUOTA_REMAINING_BYTES="
        f"{capacity_value['live_research_quota_bytes'] - capacity_value['live_research_usage_bytes']}",
        "FULL_C3_RESEARCH_FILESYSTEM_AVAILABLE_BYTES="
        f"{capacity_value['live_research_filesystem_available_bytes']}",
        "FULL_C3_RESEARCH_FILE_SLOTS_REMAINING="
        f"{capacity_value['remaining_file_slots']}",
        "FULL_C3_RESEARCH_MARGIN_BEYOND_200GB_RESERVE_BYTES="
        f"{capacity_value['quota_margin_beyond_reserve_bytes']}",
        "FULL_C3_BACKED_QUOTA_REMAINING_BYTES="
        f"{capacity_value['backed_quota_bytes'] - capacity_value['backed_usage_bytes']}",
        "FULL_C3_BACKED_FILE_SLOTS_REMAINING="
        f"{capacity_value['backed_file_quota'] - capacity_value['backed_files_used']}",
        *mapping_lines,
        "BUCKET_LISTING_REQUESTS=0",
        "CLOUD_REQUESTS=0",
        "QSUB_SUBMISSIONS=0",
        "DICOM_BODY_READS=0",
        "GPU_EXECUTIONS=0",
        "WRITES_PERFORMED=0",
    ]
    if len(lines) != len(set(line.split("=", 1)[0] for line in lines)):
        _fail("FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID")
    return tuple(lines)


def _require_qsub_environment_sha256(value: object) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        _fail("FULL_SEQUENTIAL_QSUB_ENVIRONMENT_BINDING_INVALID")
    return value


def _expected_submission_claim(
    run: FullRun,
    *,
    capacity_receipt_sha256: str,
    dynamic_capacity_receipt_sha256: str,
    capacity_evidence_role: str,
    capacity_gain_source: str,
    raw_retirement_status: str,
    raw_retirement_receipt_sha256: str,
    qsub_environment_sha256: str,
) -> dict[str, Any]:
    production_contract = (
        getattr(
            getattr(run, "requirements", None),
            "contract_id",
            core.TEST_ONLY_FULL_CONTRACT_ID,
        )
        != core.TEST_ONLY_FULL_CONTRACT_ID
    )
    if (
        SHA_RE.fullmatch(capacity_receipt_sha256) is None
        or SHA_RE.fullmatch(dynamic_capacity_receipt_sha256) is None
    ):
        _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    if capacity_evidence_role not in {
        "R5B_HISTORICAL", "R5E_R2_PRE_ACTION",
        "R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP",
    } or capacity_gain_source not in {
        "ALLOCATION", "CLEANUP", "BOTH", "EXISTING_HEADROOM"
    }:
        _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    if production_contract and (
        capacity_evidence_role != "R5E_R8_POST_CLEANUP"
        or capacity_gain_source not in {"CLEANUP", "BOTH"}
    ):
        _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    if capacity_evidence_role in {
        "R5E_POST_CLEANUP", "R5E_R8_POST_CLEANUP"
    }:
        if (
            raw_retirement_status != "PASS_OLDER_RAW_DUPLICATES_RETIRED"
            or SHA_RE.fullmatch(raw_retirement_receipt_sha256) is None
            or (
                capacity_evidence_role == "R5E_R8_POST_CLEANUP"
                and raw_retirement_receipt_sha256
                != RETIREMENT_EVENT_RECEIPT_SHA256
            )
        ):
            _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    elif capacity_evidence_role == "R5E_R2_PRE_ACTION":
        if (
            raw_retirement_status
            != "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
            or raw_retirement_receipt_sha256
            != "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
        ):
            _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    elif (
        raw_retirement_status != "NOT_APPLICABLE_CLEANUP_SKIPPED"
        or raw_retirement_receipt_sha256
        != "NOT_APPLICABLE_CLEANUP_SKIPPED"
    ):
        _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
    qsub_environment_sha256 = _require_qsub_environment_sha256(
        qsub_environment_sha256
    )
    value = {
        "schema_version": 3,
        "artifact_type": "lvef_c3_full_submission_claim_v3",
        "status": "PREPARED_TWO_SUBMISSION_FULL_RECONSTRUCTION",
        "governing_commit": run.authority.governing_commit,
        "attempt_id": run.attempt_id,
        "batch_plan_sha256": run.plan_sha256,
        "plan_authority_sha256": core.canonical_json_sha256(
            run.plan["authority"]
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "launch_authority_sha256": run.launch_authority_sha256,
        "capacity_receipt_sha256": capacity_receipt_sha256,
        "dynamic_capacity_receipt_sha256": (
            dynamic_capacity_receipt_sha256
        ),
        "capacity_evidence_role": capacity_evidence_role,
        "capacity_gain_source": capacity_gain_source,
        "raw_retirement_status": raw_retirement_status,
        "raw_retirement_receipt_sha256": raw_retirement_receipt_sha256,
        "qsub_environment_sha256": qsub_environment_sha256,
        "maximum_qsub_submissions": 2,
        "array_tasks": 19,
        "array_max_concurrency": 1,
        "automatic_resubmission": False,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "raw_dicom_deletion_authorized": False,
        "bucket_listing_requests_before_claim": 0,
        "cloud_requests_before_claim": 0,
        "qsub_submissions_before_claim": 0,
        "dicom_body_reads_before_claim": 0,
        "gpu_executions_before_claim": 0,
        "model_fitting_before_claim": 0,
        "prediction_generation_before_claim": 0,
        "confirmatory_performance_access_before_claim": 0,
    }
    if set(value) != FRESH_FULL_SUBMISSION_CLAIM_V3_KEYS:
        _fail("FULL_SEQUENTIAL_SUBMISSION_CLAIM_INTERNAL_INVALID")
    return value


def _load_bound_submission_environment_sha256(
    run: FullRun,
    *,
    expected_qsub_environment_sha256: str | None = None,
) -> str:
    """Revalidate the no-clobber claim and return its client-env binding."""

    capacity_path = (
        run.attempt_root / "full_capacity_receipt.restricted.json"
    )
    dynamic_path = (
        run.attempt_root / DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
    )
    try:
        capacity_value, capacity_payload = _load_owner_private_json(
            capacity_path
        )
        validate_successor_capacity_authority(capacity_value)
        dynamic_value, dynamic_payload = (
            capacity.load_dynamic_successor_capacity_receipt_payload(
                dynamic_path,
                expected_governing_commit=(
                    run.authority.governing_commit
                ),
            )
        )
        dynamic_core = dynamic_value["observation_authority"]
        dynamic_observation = {
            **dynamic_core,
            "observation_authority_sha256": dynamic_value[
                "observation_authority_sha256"
            ],
            "restricted_receipt_size_bytes": len(dynamic_payload),
            "restricted_receipt_sha256": hashlib.sha256(
                dynamic_payload
            ).hexdigest(),
        }
        sealed_local = capacity.validate_dynamic_successor_capacity_capture(
            capacity.DynamicSuccessorCapacityCapture(
                receipt=dict(dynamic_value),
                receipt_payload=dynamic_payload,
                observation=dynamic_observation,
            ),
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            expected_governing_commit=run.authority.governing_commit,
            expected_captured_at_utc=dynamic_value["captured_at_utc"],
        )
        dynamic_observation = sealed_local.observation
        if (
            dynamic_observation["status"]
            != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
            or capacity_value["source_capacity_authority_sha256"]
            != core.canonical_json_sha256(dynamic_observation)
            or capacity_value["source_dynamic_receipt_bytes"]
            != len(dynamic_payload)
            or capacity_value["source_dynamic_receipt_sha256"]
            != hashlib.sha256(dynamic_payload).hexdigest()
        ):
            raise ValueError("dynamic capacity binding mismatch")
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_ATTEMPT_LOCAL_CAPACITY_MISMATCH"
        ) from exc
    try:
        claim, _ = _load_owner_private_json(
            run.attempt_root / "full_submission_claim.restricted.json"
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
        ) from exc
    if set(claim) != FRESH_FULL_SUBMISSION_CLAIM_V3_KEYS:
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    observed = claim.get("qsub_environment_sha256")
    if not isinstance(observed, str) or SHA_RE.fullmatch(observed) is None:
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    if expected_qsub_environment_sha256 is not None:
        try:
            expected_binding = _require_qsub_environment_sha256(
                expected_qsub_environment_sha256
            )
        except FullSequentialError as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
            ) from exc
        if observed != expected_binding:
            _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    capacity_evidence_role = claim.get("capacity_evidence_role")
    capacity_gain_source = claim.get("capacity_gain_source")
    raw_retirement_status = claim.get("raw_retirement_status")
    raw_retirement_receipt_sha256 = claim.get(
        "raw_retirement_receipt_sha256"
    )
    if (
        getattr(
            run.requirements, "contract_id", core.TEST_ONLY_FULL_CONTRACT_ID
        )
        != core.TEST_ONLY_FULL_CONTRACT_ID
        and capacity_evidence_role != "R5E_R8_POST_CLEANUP"
    ):
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    if (
        claim.get("capacity_receipt_sha256")
        != hashlib.sha256(capacity_payload).hexdigest()
        or claim.get("dynamic_capacity_receipt_sha256")
        != hashlib.sha256(dynamic_payload).hexdigest()
        or capacity_value.get("source_dynamic_receipt_sha256")
        != claim.get("dynamic_capacity_receipt_sha256")
        or capacity_value.get("source_dynamic_receipt_bytes")
        != len(dynamic_payload)
        or (
            capacity_evidence_role == "R5E_R8_POST_CLEANUP"
            and (
                capacity_gain_source
                != _sealed_r5e_r8_gain_source(dynamic_observation)
                or raw_retirement_status != RETIREMENT_EVENT_STATUS
                or raw_retirement_receipt_sha256
                != RETIREMENT_EVENT_RECEIPT_SHA256
            )
        )
    ):
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    expected_claim = _expected_submission_claim(
        run,
        capacity_receipt_sha256=hashlib.sha256(capacity_payload).hexdigest(),
        dynamic_capacity_receipt_sha256=hashlib.sha256(
            dynamic_payload
        ).hexdigest(),
        capacity_evidence_role=capacity_evidence_role,
        capacity_gain_source=capacity_gain_source,
        raw_retirement_status=raw_retirement_status,
        raw_retirement_receipt_sha256=raw_retirement_receipt_sha256,
        qsub_environment_sha256=observed,
    )
    if claim != expected_claim:
        _fail("FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH")
    return observed


def _fixed_historical_pre_cleanup_capacity_authority() -> dict[str, Any]:
    return {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        "receipt_basename": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        "receipt_bytes": HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES,
        "receipt_sha256": HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256,
        "summary_basename": (
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
        "summary_bytes": HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES,
        "summary_sha256": HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256,
    }


def _load_fixed_historical_capacity_event(
    *,
    receipt_basename: str,
    summary_basename: str,
    receipt_bytes: int,
    receipt_sha256: str,
    summary_bytes: int,
    summary_sha256: str,
    governing_commit: str,
    captured_at_utc: str,
    allowed_statuses: frozenset[str],
) -> HistoricalCapacityEvent:
    """Load one internally fixed event without consulting live node state."""

    owner_private = PRODUCTION_ROOT / "owner_private"
    receipt_path = owner_private / receipt_basename
    summary_path = owner_private / summary_basename
    try:
        _validate_private_directory(owner_private)
        receipt_payload = capacity._read_dynamic_owner_private_regular(
            receipt_path, maximum=GENERIC_PRIVATE_FILE_MAXIMUM_BYTES
        )
        summary_payload = capacity._read_dynamic_owner_private_regular(
            summary_path, maximum=GENERIC_PRIVATE_FILE_MAXIMUM_BYTES
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_HISTORICAL_EVENT_FILE_INVALID"
        ) from exc
    if (
        len(receipt_payload) != receipt_bytes
        or len(summary_payload) != summary_bytes
        or hashlib.sha256(receipt_payload).hexdigest() != receipt_sha256
        or hashlib.sha256(summary_payload).hexdigest() != summary_sha256
    ):
        _fail("FULL_SEQUENTIAL_HISTORICAL_EVENT_HASH_MISMATCH")
    try:
        receipt_value = json.loads(
            receipt_payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID"
        ) from exc
    if not isinstance(receipt_value, Mapping):
        _fail("FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID")
    if (
        receipt_value.get("governing_commit") != governing_commit
        or receipt_value.get("captured_at_utc") != captured_at_utc
    ):
        _fail("FULL_SEQUENTIAL_HISTORICAL_EVENT_COMMIT_INVALID")
    event_time = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    try:
        captured = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=receipt_path,
            aggregate_summary_path=summary_path,
            expected_governing_commit=governing_commit,
            now_utc=event_time,
        )
        captured = capacity.validate_dynamic_successor_capacity_capture(
            captured,
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            expected_governing_commit=governing_commit,
            expected_captured_at_utc=captured_at_utc,
        )
    except capacity.PostReallocationCapacityError as exc:
        code = (
            "FULL_SEQUENTIAL_EXECUTION_REPLAY_LIVE_NODE_DEPENDENCY"
            if exc.code == "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID"
            else (
                "FULL_SEQUENTIAL_HISTORICAL_EVENT_COMMIT_INVALID"
                if exc.code == "DYNAMIC_CAPACITY_CAPTURE_TIME_MISMATCH"
                else (
                    "FULL_SEQUENTIAL_HISTORICAL_EVENT_HASH_MISMATCH"
                    if exc.code == "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH"
                    else "FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID"
                )
            )
        )
        raise FullSequentialError(code) from exc
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID"
        ) from exc
    if (
        captured.receipt_payload != receipt_payload
        or capacity._canonical(captured.observation) != summary_payload
        or captured.observation.get("status") not in allowed_statuses
    ):
        _fail("FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID")
    authority = {
        "status": captured.observation["status"],
        "receipt_basename": receipt_path.name,
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": hashlib.sha256(receipt_payload).hexdigest(),
        "summary_basename": summary_path.name,
        "summary_bytes": len(summary_payload),
        "summary_sha256": hashlib.sha256(summary_payload).hexdigest(),
    }
    return HistoricalCapacityEvent(capture=captured, authority=authority)


def _load_historical_r5e_r2_pre_action_capacity(
) -> HistoricalCapacityEvent:
    """Reopen the exact f201 cleanup-provenance event at event time."""

    return _load_fixed_historical_capacity_event(
        receipt_basename=(
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        summary_basename=(
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
        receipt_bytes=HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES,
        receipt_sha256=HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256,
        summary_bytes=HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES,
        summary_sha256=HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256,
        governing_commit=HISTORICAL_R5E_R2_PRE_ACTION_COMMIT,
        captured_at_utc=HISTORICAL_R5E_R2_PRE_ACTION_CAPTURED_AT_UTC,
        allowed_statuses=frozenset({
            capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
            capacity.DYNAMIC_SUCCESSOR_STATUS_BLOCKED,
        }),
    )


def _load_historical_r5e_post_cleanup_capacity(
) -> HistoricalCapacityEvent:
    """Reopen the exact 1fc launch-capacity event as provenance only."""

    return _load_fixed_historical_capacity_event(
        receipt_basename=capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        summary_basename=capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        receipt_bytes=HISTORICAL_R5E_POST_CLEANUP_RECEIPT_BYTES,
        receipt_sha256=HISTORICAL_R5E_POST_CLEANUP_RECEIPT_SHA256,
        summary_bytes=HISTORICAL_R5E_POST_CLEANUP_SUMMARY_BYTES,
        summary_sha256=HISTORICAL_R5E_POST_CLEANUP_SUMMARY_SHA256,
        governing_commit=HISTORICAL_R5E_POST_CLEANUP_COMMIT,
        captured_at_utc=HISTORICAL_R5E_POST_CLEANUP_CAPTURED_AT_UTC,
        allowed_statuses=frozenset({capacity.DYNAMIC_SUCCESSOR_STATUS_PASS}),
    )


def _retirement_event_artifact_payloads() -> Mapping[str, bytes]:
    root = (
        PRODUCTION_ROOT / "owner_private" / "r5e_older_raw_retirement"
    )
    try:
        _validate_private_directory(root)
        payloads: dict[str, bytes] = {}
        for basename, expected_bytes, expected_sha256 in (
            RETIREMENT_EVENT_ARTIFACTS
        ):
            path = root / basename
            before = os.lstat(path)
            if before.st_nlink != 1:
                _fail("FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID")
            payload = _read_owner_private_regular(
                path,
                maximum_bytes=expected_bytes,
                exact_bytes=expected_bytes,
                size_mismatch_code=(
                    "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID"
                ),
            )
            after = os.lstat(path)
            if (
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_uid,
                    before.st_nlink,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_uid,
                    after.st_nlink,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                or hashlib.sha256(payload).hexdigest() != expected_sha256
            ):
                _fail("FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID")
            payloads[basename] = payload
    except FullSequentialError as exc:
        if exc.code == "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID":
            raise
        raise FullSequentialError(
            "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID"
        ) from exc
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID"
        ) from exc
    return payloads


def _require_retirement_event_git_authority() -> str:
    try:
        current_commit = _current_commit()
        merge_base = _git(
            "merge-base", RETIREMENT_EVENT_COMMIT, current_commit
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_RETIREMENT_EVENT_ANCESTRY_INVALID"
        ) from exc
    if merge_base != RETIREMENT_EVENT_COMMIT:
        _fail("FULL_SEQUENTIAL_RETIREMENT_EVENT_ANCESTRY_INVALID")
    try:
        event_blob = _git(
            "rev-parse",
            f"{RETIREMENT_EVENT_COMMIT}:{RETIREMENT_EVENT_INTERPRETER_PATH}",
        )
        current_blob = _git(
            "rev-parse",
            f"{current_commit}:{RETIREMENT_EVENT_INTERPRETER_PATH}",
        )
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID"
        ) from exc
    if event_blob != current_blob or COMMIT_RE.fullmatch(event_blob) is None:
        _fail("FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID")
    return current_commit


def _load_completed_retirement_event_common() -> Mapping[str, Any]:
    _require_retirement_event_git_authority()
    payloads = _retirement_event_artifact_payloads()
    historical_authority = (
        _fixed_historical_pre_cleanup_capacity_authority()
    )
    try:
        import retire_lvef_c3_older_raw_duplicates as older_raw

        manifest_payload = payloads[RETIREMENT_EVENT_ARTIFACTS[0][0]]
        receipt_payload = payloads[RETIREMENT_EVENT_ARTIFACTS[1][0]]
        summary_payload = payloads[RETIREMENT_EVENT_ARTIFACTS[2][0]]
        manifest = json.loads(
            manifest_payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
        receipt = json.loads(
            receipt_payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
        summary = json.loads(
            summary_payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
        if not all(
            isinstance(value, Mapping)
            for value in (manifest, receipt, summary)
        ):
            raise ValueError("retirement event artifact is not a mapping")
        # These raw-retirement helpers validate only the immutable payloads;
        # unlike the public replay entry points they do not reopen the f201
        # capacity event against this execution node.
        older_raw._validate_manifest(manifest)
        older_raw._validate_receipt(
            receipt, manifest_payload=manifest_payload
        )
        expected_summary = older_raw._summary_from_receipt(
            receipt, receipt_payload=receipt_payload
        )
        if (
            summary != expected_summary
            or summary_payload != older_raw._canonical(expected_summary)
        ):
            raise ValueError("retirement summary binding changed")
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_RETIREMENT_STATE_INVALID"
        ) from exc
    if (
        manifest.get("governing_commit") != RETIREMENT_EVENT_COMMIT
        or receipt.get("governing_commit") != RETIREMENT_EVENT_COMMIT
        or receipt.get("status") != RETIREMENT_EVENT_STATUS
        or receipt.get("actual_deleted_files") != RETIREMENT_EVENT_DELETED_FILES
        or receipt.get("actual_deleted_bytes") != RETIREMENT_EVENT_DELETED_BYTES
        or manifest.get("pre_cleanup_capacity_authority")
        != historical_authority
    ):
        _fail("FULL_SEQUENTIAL_RETIREMENT_STATE_INVALID")
    diagnostics = manifest["diagnostic_evidence_authority"]
    retired = {
        "status": receipt["status"],
        "receipt_basename": RETIREMENT_EVENT_ARTIFACTS[1][0],
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": hashlib.sha256(receipt_payload).hexdigest(),
        "actual_deleted_files": receipt["actual_deleted_files"],
        "actual_deleted_bytes": receipt["actual_deleted_bytes"],
        "auxiliary_receipt_files": manifest[
            "older_receipt_tree_authority"
        ]["auxiliary_receipt_files"],
        "auxiliary_receipt_bytes": manifest[
            "older_receipt_tree_authority"
        ]["auxiliary_receipt_bytes"],
        "retained_file_metadata_sha256": receipt[
            "retained_file_metadata_sha256"
        ],
        "retained_directory_topology_sha256": receipt[
            "retained_directory_topology_sha256"
        ],
        "retained_role_inventory_sha256": receipt[
            "retained_role_inventory_sha256"
        ],
        "retained_control_content_sha256": receipt[
            "retained_control_content_sha256"
        ],
        "diagnostic_evidence_authority_sha256": receipt[
            "diagnostic_evidence_authority_sha256"
        ],
        "diagnostic_root_count": diagnostics["diagnostic_root_count"],
        "diagnostic_file_count": diagnostics["diagnostic_file_count"],
        "diagnostic_directory_count": diagnostics[
            "diagnostic_directory_count"
        ],
        "diagnostic_total_bytes": diagnostics["diagnostic_total_bytes"],
        "diagnostics_retained": diagnostics["diagnostics_retained"],
        "diagnostic_body_reads": diagnostics["body_reads"],
        "pre_cleanup_capacity_authority": dict(
            manifest["pre_cleanup_capacity_authority"]
        ),
    }
    retained_state_authority = {
        key: value
        for key, value in retired.items()
        if key
        not in {
            "status", "receipt_basename", "receipt_bytes", "receipt_sha256",
            "pre_cleanup_capacity_authority",
        }
    }
    return {
        "status": RETIREMENT_EVENT_STATUS,
        "receipt_sha256": RETIREMENT_EVENT_RECEIPT_SHA256,
        "pre_cleanup_capacity_authority": historical_authority,
        "retained_state_authority": retained_state_authority,
    }


def _load_completed_retirement_event() -> Mapping[str, Any]:
    return _load_completed_retirement_event_common()


def _load_completed_retirement_receipt_event() -> Mapping[str, Any]:
    return _load_completed_retirement_event_common()


def _load_test_only_capacity_pair(
    run: FullRun,
    *,
    restricted_basename: str,
    summary_basename: str,
    require_pass: bool = True,
    now_utc: datetime | None = None,
) -> capacity.DynamicSuccessorCapacityCapture:
    if (
        getattr(
            getattr(run, "requirements", None),
            "contract_id",
            core.TEST_ONLY_FULL_CONTRACT_ID,
        )
        != core.TEST_ONLY_FULL_CONTRACT_ID
    ):
        _fail("FULL_SEQUENTIAL_SYNTHETIC_CAPACITY_NOT_AUTHORIZED")
    owner_private = run.production_root / "owner_private"
    _validate_private_directory(owner_private)
    try:
        captured = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=(
                owner_private / restricted_basename
            ),
            aggregate_summary_path=(
                owner_private / summary_basename
            ),
            expected_governing_commit=run.authority.governing_commit,
            now_utc=now_utc,
        )
        if require_pass and (
            captured.observation.get("status")
            != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        ):
            _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_NOT_PASS")
        captured = capacity.validate_production_dynamic_successor_capacity_capture(
            captured,
            expected_governing_commit=run.authority.governing_commit,
            now_utc=now_utc,
        )
    except capacity.PostReallocationCapacityError as exc:
        raise FullSequentialError(exc.code) from exc
    return captured


def _load_current_r5e_r8_post_cleanup_capacity(
    run: FullRun,
) -> capacity.DynamicSuccessorCapacityCapture:
    """Load the sole current R8 pair in strict live-admission context."""

    owner_private = run.production_root / "owner_private"
    receipt_path = (
        owner_private
        / capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
    )
    summary_path = (
        owner_private
        / capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
    )
    try:
        _validate_private_directory(owner_private)
        present = tuple(
            os.path.lexists(path) for path in (receipt_path, summary_path)
        )
        if not all(present):
            raise ValueError("current R8 capacity pair missing or partial")
        captured = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=receipt_path,
            aggregate_summary_path=summary_path,
            expected_governing_commit=run.authority.governing_commit,
        )
    except capacity.PostReallocationCapacityError as exc:
        code = (
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_STALE"
            if exc.code == "DYNAMIC_CAPACITY_RECEIPT_STALE"
            else "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
        )
        raise FullSequentialError(code) from exc
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
        ) from exc
    if captured.observation.get("status") != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
        _fail("FULL_SEQUENTIAL_CURRENT_CAPACITY_NOT_PASS")
    try:
        captured = capacity.validate_dynamic_successor_capacity_capture(
            captured,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            expected_governing_commit=run.authority.governing_commit,
        )
    except capacity.PostReallocationCapacityError as exc:
        code = (
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_STALE"
            if exc.code == "DYNAMIC_CAPACITY_RECEIPT_STALE"
            else "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
        )
        raise FullSequentialError(code) from exc
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
        ) from exc
    return captured


def _load_fixed_capacity_admission(
    run: FullRun,
) -> CapacityAdmission:
    production_contract = (
        getattr(
            getattr(run, "requirements", None),
            "contract_id",
            core.TEST_ONLY_FULL_CONTRACT_ID,
        )
        != core.TEST_ONLY_FULL_CONTRACT_ID
    )
    if production_contract:
        if Path(run.production_root) != PRODUCTION_ROOT:
            _fail("FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID")
        owner_private = PRODUCTION_ROOT / "owner_private"
        r8_paths = (
            owner_private
            / capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            owner_private
            / capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        )
        historical = _load_historical_r5e_r2_pre_action_capacity()
        _load_historical_r5e_post_cleanup_capacity()
        r8_present = tuple(os.path.lexists(path) for path in r8_paths)
        if not all(r8_present):
            _fail("FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID")
        post = _load_current_r5e_r8_post_cleanup_capacity(run)
        retired = _load_completed_retirement_event()
        if retired["pre_cleanup_capacity_authority"] != historical.authority:
            _fail("FULL_SEQUENTIAL_RETIREMENT_STATE_INVALID")
        admission = CapacityAdmission(
            capture=post,
            evidence_role="R5E_R8_POST_CLEANUP",
            capacity_gain_source=_capacity_gain_source(
                post.observation,
                evidence_role="R5E_R8_POST_CLEANUP",
                pre_cleanup_observation=historical.capture.observation,
            ),
            raw_retirement_status=str(retired["status"]),
            raw_retirement_receipt_sha256=str(retired["receipt_sha256"]),
        )
        _validate_capacity_admission(admission)
        return admission

    # Frozen test-only compatibility for pre-R7 synthetic fixtures. No
    # production FullRun can enter this branch, and a historical R5E_R2 pair
    # therefore cannot satisfy a real successor claim.
    owner_private = run.production_root / "owner_private"
    post_paths = (
        owner_private / capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        owner_private / capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
    )
    pre_paths = (
        owner_private
        / capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
        owner_private
        / capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
    )
    post_present = tuple(os.path.lexists(path) for path in post_paths)
    pre_present = tuple(os.path.lexists(path) for path in pre_paths)
    if any(post_present):
        if not all(post_present) or not all(pre_present):
            _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_PAIR_INVALID")
        pre = _load_test_only_capacity_pair(
            run,
            restricted_basename=pre_paths[0].name,
            summary_basename=pre_paths[1].name,
            require_pass=False,
        )
        post = _load_test_only_capacity_pair(
            run,
            restricted_basename=post_paths[0].name,
            summary_basename=post_paths[1].name,
        )
        if pre.observation.get("status") == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
            _fail("FULL_SEQUENTIAL_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS")
        try:
            import retire_lvef_c3_older_raw_duplicates as older_raw

            retired = older_raw.validate_retired_state(
                expected_governing_commit=run.authority.governing_commit
            )
        except Exception as exc:
            raise FullSequentialError(
                "FULL_SEQUENTIAL_OLDER_RAW_RETIREMENT_INVALID"
            ) from exc
        observed_pre_authority = {
            "status": pre.observation.get("status"),
            "receipt_basename": pre_paths[0].name,
            "receipt_bytes": len(pre.receipt_payload),
            "receipt_sha256": hashlib.sha256(
                pre.receipt_payload
            ).hexdigest(),
            "summary_basename": pre_paths[1].name,
            "summary_bytes": len(capacity._canonical(pre.observation)),
            "summary_sha256": hashlib.sha256(
                capacity._canonical(pre.observation)
            ).hexdigest(),
        }
        if retired.get("pre_cleanup_capacity_authority") != (
            observed_pre_authority
        ):
            _fail("FULL_SEQUENTIAL_OLDER_RAW_RETIREMENT_INVALID")
        admission = CapacityAdmission(
            capture=post,
            evidence_role="R5E_POST_CLEANUP",
            capacity_gain_source=_capacity_gain_source(
                post.observation,
                evidence_role="R5E_POST_CLEANUP",
                pre_cleanup_observation=pre.observation,
            ),
            raw_retirement_status=str(retired["status"]),
            raw_retirement_receipt_sha256=str(retired["receipt_sha256"]),
        )
    elif any(pre_present):
        if not all(pre_present):
            _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_PAIR_INVALID")
        pre = _load_test_only_capacity_pair(
            run,
            restricted_basename=pre_paths[0].name,
            summary_basename=pre_paths[1].name,
        )
        admission = CapacityAdmission(
            capture=pre,
            evidence_role="R5E_R2_PRE_ACTION",
            capacity_gain_source=_capacity_gain_source(
                pre.observation, evidence_role="R5E_R2_PRE_ACTION"
            ),
            raw_retirement_status=(
                "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
            ),
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
            ),
        )
    else:
        historical = _load_test_only_capacity_pair(
            run,
            restricted_basename=(
                capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        admission = CapacityAdmission(
            capture=historical,
            evidence_role="R5B_HISTORICAL",
            capacity_gain_source=_capacity_gain_source(
                historical.observation, evidence_role="R5B_HISTORICAL"
            ),
            raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CLEANUP_SKIPPED"
            ),
        )
    _validate_capacity_admission(admission)
    return admission


def _load_fixed_dynamic_capacity_capture(
    run: FullRun,
) -> capacity.DynamicSuccessorCapacityCapture:
    """Compatibility projection for maintained callers and tests."""

    return _load_fixed_capacity_admission(run).capture


def validate_execution_node_sealed_authorities() -> Mapping[str, Any]:
    """Replay fixed admission events without consulting node-local capacity."""

    try:
        installation = validate_installation()
        historical = _load_historical_r5e_r2_pre_action_capacity()
        retired = _load_completed_retirement_receipt_event()
        historical_post = _load_historical_r5e_post_cleanup_capacity()
        if (
            retired["pre_cleanup_capacity_authority"] != historical.authority
            or retired["status"] != RETIREMENT_EVENT_STATUS
            or retired["receipt_sha256"] != RETIREMENT_EVENT_RECEIPT_SHA256
            or historical_post.capture.observation.get("status")
            != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
            or _capacity_gain_source(
                historical_post.capture.observation,
                evidence_role="R5E_POST_CLEANUP",
                pre_cleanup_observation=historical.capture.observation,
            )
            != HISTORICAL_R5E_POST_CLEANUP_GAIN_SOURCE
        ):
            _fail("FULL_SEQUENTIAL_EXECUTION_NODE_AUTHORITY_REPLAY_FAILED")
    except FullSequentialError:
        raise
    except Exception as exc:
        raise FullSequentialError(
            "FULL_SEQUENTIAL_EXECUTION_NODE_AUTHORITY_REPLAY_FAILED"
        ) from exc
    return {
        "status": "PASS_SEALED_EXECUTION_NODE_AUTHORITY_REPLAY",
        "governing_commit": installation["governing_commit"],
        "historical_pre_cleanup": "PASS",
        "retirement_event": "PASS",
        "historical_post_cleanup": "PASS",
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "successor_claims_created": 0,
        "successor_attempt_roots_created": 0,
    }


def claim_submission(
    *, qsub_environment_sha256: str
) -> Mapping[str, Any]:
    qsub_environment_sha256 = _require_qsub_environment_sha256(
        qsub_environment_sha256
    )
    run = build_full_run()
    if os.path.lexists(run.attempt_root):
        _fail("FULL_SEQUENTIAL_ATTEMPT_ALREADY_EXISTS")
    validate_installation()
    _validate_completed_canary_evidence()
    dependency = resolve_dependencies()
    dependency.environment_validator(
        run.authority.environment_receipt,
        expected_environment_receipt_sha256=(
            run.runtime_authority["environment_receipt_sha256"]
        ),
        scientific_governing_commit=run.authority.governing_commit,
    )
    cache_inventory = _extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if (
        cache_inventory.active != 0
        or cache_inventory.preserved_terminal_failed
        != SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
    ):
        _fail("FULL_SEQUENTIAL_TERMINAL_FAILURE_CACHE_AUTHORITY_INVALID")
    admission = _load_fixed_capacity_admission(run)
    if (
        getattr(
            run.requirements, "contract_id", core.TEST_ONLY_FULL_CONTRACT_ID
        )
        != core.TEST_ONLY_FULL_CONTRACT_ID
        and admission.evidence_role != "R5E_R8_POST_CLEANUP"
    ):
        _fail("FULL_SEQUENTIAL_CAPACITY_EVIDENCE_ROLE_INVALID")
    dynamic_capture = admission.capture
    _validate_capacity_admission(admission)
    if (
        dynamic_capture.observation.get("status")
        != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        or dynamic_capture.observation.get("successor_attempt_root_absent")
        is not True
        or dynamic_capture.observation.get("successor_claim_absent") is not True
    ):
        _fail("FULL_SEQUENTIAL_CURRENT_CAPACITY_NOT_PASS")
    successor_capacity = build_successor_capacity_authority(
        dynamic_capture, cache_inventory
    )
    if os.path.lexists(run.attempt_root):
        _fail("DYNAMIC_CAPACITY_SUCCESSOR_COLLISION")
    _ensure_private_directory(run.production_root)
    _ensure_private_directory(run.production_root / "attempts")
    _ensure_private_directory(run.attempt_root)
    try:
        _write_private_json(run.plan_path, run.plan, attempt_id=run.attempt_id)
        _write_private_json(
            run.attempt_root / "full_launch_authority.restricted.json",
            run.launch_authority,
            attempt_id=run.attempt_id,
        )
        dynamic_path = (
            run.attempt_root / DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
        )
        capacity.write_dynamic_successor_capacity_receipt_payload(
            dynamic_path,
            dynamic_capture.receipt_payload,
            expected_governing_commit=run.authority.governing_commit,
        )
        _, dynamic_payload = (
            capacity.load_dynamic_successor_capacity_receipt_payload(
                dynamic_path,
                expected_governing_commit=run.authority.governing_commit,
            )
        )
        if dynamic_payload != dynamic_capture.receipt_payload:
            _fail("FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID")
        _write_private_json(
            run.attempt_root / "full_capacity_receipt.restricted.json",
            successor_capacity,
            attempt_id=run.attempt_id,
        )
        claim = _expected_submission_claim(
            run,
            capacity_receipt_sha256=core.sha256_file(
                run.attempt_root / "full_capacity_receipt.restricted.json"
            ),
            dynamic_capacity_receipt_sha256=hashlib.sha256(
                dynamic_payload
            ).hexdigest(),
            capacity_evidence_role=admission.evidence_role,
            capacity_gain_source=admission.capacity_gain_source,
            raw_retirement_status=admission.raw_retirement_status,
            raw_retirement_receipt_sha256=(
                admission.raw_retirement_receipt_sha256
            ),
            qsub_environment_sha256=qsub_environment_sha256,
        )
        _write_private_json(
            run.attempt_root / "full_submission_claim.restricted.json",
            claim,
            attempt_id=run.attempt_id,
        )
    except Exception:
        # The no-clobber evidence is deliberately preserved on any partial
        # claim failure; a second attempt is not authorized.
        raise
    return {
        "status": "READY",
        "attempt_id": run.attempt_id,
        "governing_commit": run.authority.governing_commit,
        "batch_plan_sha256": run.plan_sha256,
        "launch_authority_sha256": run.launch_authority_sha256,
        "cloud_requests": 0,
        "qsub_submissions": 0,
    }


def _adopt_claimed_run(
    *,
    scheduler_job_identity: str,
    expected_qsub_environment_sha256: str | None = None,
) -> FullRun:
    run = build_full_run(scheduler_job_identity=scheduler_job_identity)
    if run.attempt_root.is_symlink() or not run.attempt_root.is_dir():
        _fail("FULL_SEQUENTIAL_PREPARED_ATTEMPT_MISSING")
    _validate_private_directory(run.attempt_root)
    _validate_completed_canary_evidence()
    _validate_full_run(run)
    _load_full_batch_plan(run)
    launch, launch_payload = _load_owner_private_json(
        run.attempt_root / "full_launch_authority.restricted.json"
    )
    if (
        launch != run.launch_authority
        or hashlib.sha256(launch_payload).hexdigest()
        != run.launch_authority_sha256
    ):
        _fail("FULL_SEQUENTIAL_PREPARED_AUTHORITY_MISMATCH")
    _load_bound_submission_environment_sha256(
        run,
        expected_qsub_environment_sha256=(
            expected_qsub_environment_sha256
        ),
    )
    return run


def run_cross_batch_finalizer(
    *, run: FullRun | None = None
) -> Mapping[str, Any]:
    effective = run or _adopt_claimed_run(
        scheduler_job_identity=str(os.environ.get("JOB_ID", ""))
    )
    if JOB_RE.fullmatch(str(os.environ.get("JOB_ID", ""))) is not None:
        _wait_for_submission_receipt(
            effective,
            current_job_id=str(os.environ.get("JOB_ID", "")),
            role="finalizer",
            monotonic_clock=time.monotonic,
            sleeper=time.sleep,
        )
    _validate_full_run(effective)
    receipts = [
        _batch_paths(effective, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(effective.requirements.batch_count)
    ]
    output_root = effective.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=effective.authority.governing_commit,
        expected_attempt_id=effective.attempt_id,
        plan=effective.plan,
        requirements=effective.requirements,
        production_root=effective.production_root,
        contract=effective.contract,
        contract_path=effective.contract_path,
        environment_receipt=effective.authority.environment_receipt,
        cache_retirement_authorization_root=(
            effective.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=effective.runtime_authority,
        expected_no_cine_studies=int(
            effective.launch_authority["expected_no_cine_studies"]
        ),
    )
    result = {
        **summary,
        "status": "PASS_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "n_pooled_studies": summary["pooled_imaging_eligible_studies"],
        "n_no_cine_studies": summary["no_cine_studies"],
        "exact_pooling_replay_passed": True,
        "model_fitting_count": summary["model_fitting_count"],
        "endpoint_prediction_count": summary["endpoint_prediction_count"],
        "confirmatory_performance_access_count": summary[
            "confirmatory_performance_access_count"
        ],
    }
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-installation", action="store_true")
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--preflight-report", action="store_true")
    modes.add_argument("--seal-dynamic-capacity", action="store_true")
    modes.add_argument("--seal-r5e-pre-cleanup-capacity", action="store_true")
    modes.add_argument("--seal-r5e-r2-pre-action-capacity", action="store_true")
    modes.add_argument("--seal-r5e-post-cleanup-capacity", action="store_true")
    modes.add_argument("--seal-r5e-r8-post-cleanup-capacity", action="store_true")
    modes.add_argument(
        "--validate-execution-node-sealed-authorities", action="store_true"
    )
    modes.add_argument("--claim-submission", action="store_true")
    modes.add_argument("--validate-claimed-submission", action="store_true")
    modes.add_argument("--print-fixed-identity", action="store_true")
    modes.add_argument("--run-array-task", action="store_true")
    modes.add_argument("--run-cohort-finalizer", action="store_true")
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    live_mode = args.run_array_task or args.run_cohort_finalizer
    try:
        bound_control_mode = (
            args.claim_submission or args.validate_claimed_submission
        )
        if bound_control_mode:
            qsub_environment_sha256 = _require_qsub_environment_sha256(
                os.environ.pop(QSUB_ENVIRONMENT_SHA256_NAME, None)
            )
        else:
            qsub_environment_sha256 = None
            if QSUB_ENVIRONMENT_SHA256_NAME in os.environ:
                _fail("FULL_SEQUENTIAL_QSUB_ENVIRONMENT_BINDING_INVALID")
        if args.validate_installation:
            validate_installation()
            print("FULL_C3_INSTALLATION=PASS")
        elif args.preflight_only:
            _run_r5e_r8_integrated_no_body_acceptance()
            print("FULL_C3_NO_BODY_PREFLIGHT=PASS")
        elif args.preflight_report:
            print("\n".join(format_preflight_report(preflight_full())))
        elif args.seal_dynamic_capacity:
            sealed = run_dynamic_successor_capacity_seal()
            print("\n".join(
                format_dynamic_successor_capacity_seal_report(sealed)
            ))
            if sealed["status"] != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
                return 78
        elif (
            args.seal_r5e_pre_cleanup_capacity
            or args.seal_r5e_r2_pre_action_capacity
            or args.seal_r5e_post_cleanup_capacity
            or args.seal_r5e_r8_post_cleanup_capacity
        ):
            role = (
                "R5E_PRE_CLEANUP"
                if args.seal_r5e_pre_cleanup_capacity
                else (
                    "R5E_R2_PRE_ACTION"
                    if args.seal_r5e_r2_pre_action_capacity
                    else (
                        "R5E_R8_POST_CLEANUP"
                        if args.seal_r5e_r8_post_cleanup_capacity
                        else "R5E_POST_CLEANUP"
                    )
                )
            )
            sealed = (
                run_r5e_r8_post_cleanup_capacity_seal()
                if role == "R5E_R8_POST_CLEANUP"
                else run_dynamic_successor_capacity_seal(
                    evidence_role=role
                )
            )
            observation = sealed["observation"]
            markers = (
                f"R5E_CAPACITY_EVIDENCE_ROLE={role}",
                f"CURRENT_CAPACITY_STATUS={sealed['status']}",
                "STORAGE_ALLOCATION_VISIBLE="
                + ("YES" if sealed["storage_allocation_visible"] else "NO"),
                f"CAPACITY_GAIN_SOURCE={sealed['capacity_gain_source']}",
                f"CURRENT_QUOTA_BYTES={observation['live_research_quota_bytes']}",
                f"CURRENT_USAGE_BYTES={observation['live_research_usage_bytes']}",
                "CURRENT_PHYSICAL_AVAILABLE_BYTES="
                f"{observation['live_research_filesystem_available_bytes']}",
                f"CURRENT_REMAINING_FILE_SLOTS={observation['remaining_file_slots']}",
                "PROJECTED_FRESH_SUCCESSOR_PEAK_BYTES="
                f"{observation['projected_fresh_successor_peak_bytes']}",
                "QUOTA_MARGIN_BEYOND_200GB_RESERVE_BYTES="
                f"{observation['quota_margin_beyond_reserve_bytes']}",
                "PHYSICAL_MARGIN_BEYOND_200GB_RESERVE_BYTES="
                f"{observation['physical_margin_beyond_reserve_bytes']}",
                "FILE_SLOT_MARGIN_AFTER_DEMAND="
                f"{observation['file_slot_margin_after_demand']}",
                "MINIMUM_ADDITIONAL_QUOTA_BYTES="
                f"{sealed['minimum_additional_quota_bytes']}",
                "MINIMUM_ADDITIONAL_PHYSICAL_BYTES="
                f"{sealed['minimum_additional_physical_bytes']}",
                "MINIMUM_ADDITIONAL_FILE_SLOTS="
                f"{sealed['minimum_additional_file_slots']}",
                f"CAPACITY_RECEIPT_BASENAME={sealed['restricted_receipt_basename']}",
                f"CAPACITY_RECEIPT_BYTES={sealed['restricted_receipt_bytes']}",
                f"CAPACITY_RECEIPT_SHA256={sealed['restricted_receipt_sha256']}",
                f"CAPACITY_SUMMARY_BASENAME={sealed['aggregate_summary_basename']}",
                f"CAPACITY_SUMMARY_BYTES={sealed['aggregate_summary_bytes']}",
                f"CAPACITY_SUMMARY_SHA256={sealed['aggregate_summary_sha256']}",
                "INTEGRATED_NO_BODY_PREFLIGHT="
                f"{sealed['integrated_no_body_preflight']}",
                f"RAW_RETIREMENT_STATUS={sealed['raw_retirement_status']}",
                "RAW_RETIREMENT_RECEIPT_SHA256="
                f"{sealed['raw_retirement_receipt_sha256']}",
                "SUCCESSOR_CLAIM_CREATED=NO",
                "SUCCESSOR_ATTEMPT_ROOT_CREATED=NO",
                "NEW_CLOUD_REQUESTS=0",
                "NEW_DICOM_BODY_READS=0",
                "NEW_GPU_EXECUTIONS=0",
                "NEW_MODEL_FITTING=0",
                "NEW_PREDICTION_GENERATION=0",
                "CONFIRMATORY_PERFORMANCE_ACCESSED=NO",
            )
            if len(markers) != len({line.split("=", 1)[0] for line in markers}):
                _fail("FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID")
            print("\n".join(markers))
            if sealed["status"] != capacity.DYNAMIC_SUCCESSOR_STATUS_PASS:
                return 78
        elif args.validate_execution_node_sealed_authorities:
            validate_execution_node_sealed_authorities()
            print("FULL_C3_EXECUTION_NODE_SEALED_AUTHORITY_REPLAY=PASS")
            print("CLOUD_REQUESTS=0")
            print("DICOM_BODY_READS=0")
            print("NPZ_BODY_READS=0")
            print("GPU_EXECUTIONS=0")
            print("SUCCESSOR_CLAIMS_CREATED=0")
            print("SUCCESSOR_ATTEMPT_ROOTS_CREATED=0")
        elif args.claim_submission:
            claim_submission(
                qsub_environment_sha256=qsub_environment_sha256
            )
            print("FULL_C3_SUBMISSION_CLAIM=READY")
        elif args.validate_claimed_submission:
            _adopt_claimed_run(
                scheduler_job_identity="MATERIALIZED_CLAIM_READBACK",
                expected_qsub_environment_sha256=(
                    qsub_environment_sha256
                ),
            )
            print("FULL_C3_MATERIALIZED_CLAIM_READBACK=PASS")
        elif args.print_fixed_identity:
            print(f"FULL_C3_ATTEMPT_ID={build_full_run().attempt_id}")
        elif args.run_array_task:
            job_id = os.environ.get("JOB_ID", "")
            task = os.environ.get("SGE_TASK_ID", "")
            if JOB_RE.fullmatch(job_id) is None or TASK_RE.fullmatch(task) is None:
                _fail("FULL_SEQUENTIAL_SCHEDULER_IDENTITY_INVALID")
            with _stage_boundary("SUBMISSION_AUTHORITY"):
                run_batch_task(
                    task_id=int(task),
                    run=_adopt_claimed_run(
                        scheduler_job_identity=f"{job_id}.{task}"
                    ),
                )
            print("FULL_C3_ARRAY_TASK=PASS")
        else:
            job_id = os.environ.get("JOB_ID", "")
            if JOB_RE.fullmatch(job_id) is None or os.environ.get("SGE_TASK_ID") not in {
                None, "", "undefined"
            }:
                _fail("FULL_SEQUENTIAL_SCHEDULER_IDENTITY_INVALID")
            with _stage_boundary("CROSS_BATCH_FINALIZATION"):
                run_cross_batch_finalizer(
                    run=_adopt_claimed_run(scheduler_job_identity=job_id)
                )
            print("FULL_C3_COHORT_FINALIZER=PASS")
        return 0
    except FullSequentialError as exc:
        print(f"FULL_C3_STATUS=BLOCKED_{exc.code}")
        if live_mode and exc.stage is not None:
            print(f"FULL_C3_FAILED_STAGE={exc.stage}")
        if args.run_array_task and TASK_RE.fullmatch(
            os.environ.get("SGE_TASK_ID", "")
        ):
            print(f"FULL_C3_FAILED_BATCH={int(os.environ['SGE_TASK_ID'])}")
    except Exception:
        print("FULL_C3_STATUS=BLOCKED_UNEXPECTED_SANITIZED_EXCEPTION")
    if not live_mode:
        print("CLOUD_REQUESTS=0")
        print("QSUB_SUBMISSIONS=0")
        print("DICOM_BODY_READS=0")
        print("GPU_EXECUTIONS=0")
    return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
