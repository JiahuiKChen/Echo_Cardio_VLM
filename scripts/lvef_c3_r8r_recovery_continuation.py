#!/usr/bin/env python3
"""Fixed R8R recovery and same-attempt continuation controller.

This is deliberately not a general resume interface.  Every scientific
identity, path, batch, and task range is fixed below.  The only accepted live
variation is the repair implementation commit, which must be the clean
origin-equal descendant of the original scientific commit.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Final, Mapping, Sequence
import xml.etree.ElementTree as ET


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import capture_lvef_c3_post_reallocation_capacity as capacity
import finalize_lvef_c3_production as finalizer
import lvef_c3_full_scheduler as scheduler
import lvef_c3_full_sequential as sequential
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement
import validate_lvef_c3_prior_batch_finalization as prior_gate


ORIGINAL_SCIENTIFIC_COMMIT: Final = (
    "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
)
ORIGINAL_ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
ORIGINAL_PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
ORIGINAL_PLAN_BYTES: Final = 115_548_986
ORIGINAL_LAUNCH_BYTES: Final = 1_239
ORIGINAL_LAUNCH_SHA256: Final = (
    "124c041f274a0148c694079d3d8d5918afad01c36f9e4371b91339f8e9a00467"
)
ORIGINAL_CLAIM_BYTES: Final = 1_648
ORIGINAL_CLAIM_SHA256: Final = (
    "482b084b7e6407349b4c330a8029bae19a88c06d2a390a9fe2f385d5c7349b99"
)
ORIGINAL_CAPACITY_BYTES: Final = 1_228
ORIGINAL_CAPACITY_SHA256: Final = (
    "8209b30f20e3cd6e6b8d74606f4b6e693dd47ed3095cc0d86a884e92eb263af6"
)
ORIGINAL_DYNAMIC_CAPACITY_BYTES: Final = 12_242
ORIGINAL_DYNAMIC_CAPACITY_SHA256: Final = (
    "b2e7f4d0db7de4ce05d6c3978d04073dbf520833a72a7e708ae32178c47f05ae"
)
ORIGINAL_SUBMISSION_BYTES: Final = 1_543
ORIGINAL_SUBMISSION_SHA256: Final = (
    "5a8b942cef716b3cc90411638024146a5189781f6f4c70654bbfe0efc41e9c46"
)
PREFIX_RECEIPT_AUTHORITIES: Final = (
    (
        "c3_batch_000",
        4_730,
        "e8f1b505422af64fc98c44f1cb85da52528f014c904e1cbfe319ca0109f31277",
    ),
    (
        "c3_batch_001",
        4_725,
        "54c536f5c2faa712bc3a97d18c6c6fde804941d096dc307dbaf50e6c33e32c98",
    ),
)
FIXED_BATCH3_ID: Final = "c3_batch_002"
FIXED_RECOVERY_TASK_ID: Final = 3
FIXED_CONTINUATION_TASK_IDS: Final = tuple(range(4, 20))
FIXED_CONTINUATION_TASK_RANGE: Final = "4-19"
FIXED_CONTINUATION_MAX_CONCURRENCY: Final = 1
ORIGINAL_SCHEDULER_JOB_IDS: Final = frozenset({"7253130", "7253131"})
PRODUCTION_ROOT: Final = sequential.PRODUCTION_ROOT
ATTEMPT_ROOT: Final = PRODUCTION_ROOT / "attempts" / ORIGINAL_ATTEMPT_ID
RECOVERY_ROOT: Final = ATTEMPT_ROOT / "r8r_batch3_recovery"
RECOVERY_SCHEDULER_ROOT: Final = RECOVERY_ROOT / "scheduler"
RECOVERY_AUTHORITY_PATH: Final = RECOVERY_ROOT / "recovery_authority.restricted.json"
RECOVERY_SUBMISSION_PATH: Final = (
    RECOVERY_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
RECOVERY_TERMINAL_PATH: Final = (
    RECOVERY_ROOT / "recovery_terminal.aggregate_safe.json"
)
CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8r_continuation"
CONTINUATION_SCHEDULER_ROOT: Final = CONTINUATION_ROOT / "scheduler"
CONTINUATION_CAPACITY_PATH: Final = (
    CONTINUATION_ROOT / "continuation_capacity.restricted.json"
)
CONTINUATION_CAPACITY_SUMMARY_PATH: Final = (
    CONTINUATION_ROOT / "continuation_capacity.aggregate_safe.json"
)
CONTINUATION_CLAIM_PATH: Final = (
    CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
CONTINUATION_SUBMISSION_PATH: Final = (
    CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
RUNNER_PATH: Final = (
    SCRIPT_ROOT / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
)
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
JOB_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_CODE_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
QACCT_PATH: Final = scheduler.QSTAT_PATH.with_name("qacct")
MAX_CONTROL_BYTES: Final = 128 * 1024 * 1024
RECOVERY_STATUS: Final = "PASS_BATCH3_PRESERVATION_RECOVERY"
CONTINUATION_CAPACITY_STATUS: Final = (
    "PASS_FIXED_CONTINUATION_4_19_WITH_200GB_RESERVE"
)

# R8U is a second, destination-specific repair epoch.  These constants are
# deliberately additive: the completed R8R chain remains immutable and the
# new entry points accept no caller-supplied target, attempt, range, or path.
R8U_STARTING_IMPLEMENTATION_COMMIT: Final = (
    "fe3b6c40162d16d5021558bc686ba93c05ab03f5"
)
R8U_BASE_IMPLEMENTATION_COMMIT: Final = (
    "cbd54ec67a24bc26e538be0423df38cee8a9eb6f"
)
R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT: Final = (
    "f3df5cd969ff70c87378657767c5bf2b92d4e074"
)
R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS: Final = frozenset(
    {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
    }
)
R8U_FIXED_BATCH_ID: Final = "c3_batch_015"
R8U_FIXED_RECOVERY_TASK_ID: Final = 16
R8U_FIXED_CONTINUATION_TASK_IDS: Final = tuple(range(17, 20))
R8U_FIXED_CONTINUATION_TASK_RANGE: Final = "17-19"
R8U_FIXED_CONTINUATION_MAX_CONCURRENCY: Final = 1
R8U_FAILED_ARRAY_JOB_ID: Final = "7292691"
R8U_FAILED_FINALIZER_JOB_ID: Final = "7292692"
R8U_PRIOR_RECOVERY_JOB_ID: Final = "7269865"
R8U_FAILED_RECOVERY_JOB_ID: Final = "7352656"
R8U_FAILED_RECOVERY_JOB_NAME: Final = "lvef_c3_r8u_rec_f3df5cd9"
R8U_FAILED_RECOVERY_TERMINAL_CODE: Final = (
    "BLOCKED_R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID"
)
R8U_FAILED_RECOVERY_QSUB_EXIT: Final = 0
R8U_FAILED_RECOVERY_QACCT_FAILED: Final = 0
R8U_FAILED_RECOVERY_QACCT_EXIT_STATUS: Final = 78
R8U_SCHEDULER_LOG_MAX_BYTES: Final = 16 * 1024 * 1024
R8U_SCHEDULER_LOG_ROLE: Final = (
    "GRID_ENGINE_MERGED_SCHEDULER_EVIDENCE"
)
R8U_FAILED_PARTIAL_FILES: Final = 4_757
R8U_FAILED_PARTIAL_DIRECTORIES: Final = 259
R8U_FAILED_PARTIAL_BYTES: Final = 8_583_119_701
R8U_FAILED_PARTIAL_METADATA_SHA256: Final = (
    "1dcc53e52a468773128348225943125c926bcab942ac7c69c37344684249f83e"
)
R8U_BATCH16_RAW_FILES: Final = 18_677
R8U_BATCH16_RAW_BYTES: Final = 66_687_050_120
R8U_BATCH16_DOWNLOAD_LEDGER_BYTES: Final = 3_793_910
R8U_BATCH16_DOWNLOAD_LEDGER_SHA256: Final = (
    "675fa5d0f41b1886ed8278244d47a0cafd82c253906790bcc4e373df27863cc5"
)
R8U_BATCH16_VERIFIED_MANIFEST_BYTES: Final = 3_754_166
R8U_BATCH16_VERIFIED_MANIFEST_SHA256: Final = (
    "662511208b4658b6c656bebcf7a0b8ee7b4726bf7f5a69277a7f692cb67cf5b7"
)
R8U_BATCH16_SELECTED_MANIFEST_BYTES: Final = 4_520
R8U_BATCH16_SELECTED_MANIFEST_SHA256: Final = (
    "fe968a6cb2fc8ee0425705267b5f735b4eb8c7d8a1a34c2f99600c9afc2bc90a"
)
R8U_PREFIX_RECEIPT_AUTHORITIES: Final = (
    ("c3_batch_000", 4_730, "e8f1b505422af64fc98c44f1cb85da52528f014c904e1cbfe319ca0109f31277"),
    ("c3_batch_001", 4_725, "54c536f5c2faa712bc3a97d18c6c6fde804941d096dc307dbaf50e6c33e32c98"),
    ("c3_batch_002", 4_761, "23ef029943578f56ca41212ea8d98c05eddaeb2356c288f370c27b0759f06b22"),
    ("c3_batch_003", 4_756, "7acbb2325b4bafa4e57dca284161ade600dd319da4ecbea6fd04ebb73493bb1d"),
    ("c3_batch_004", 4_728, "0b14603822c4d869c605000f766f6b78aa73c19358721403c0f66dc3ed4ee7c6"),
    ("c3_batch_005", 4_728, "447d9e55bc1684135736bd0cfdf63ccee29e50f5a6d25c4ac53144e8caf22f31"),
    ("c3_batch_006", 4_723, "43c531839aef2c7c84eca87dfd88225435f9b9df6930010c336948b3c97515c2"),
    ("c3_batch_007", 4_723, "19e4ec6bdf60932987879b20b00ad0744a2e153e24cd508d85b2dfc7a76da8e6"),
    ("c3_batch_008", 4_761, "3d121bf49f64905fd00bbe93f25f7c8833a6a621f5c44d70079e9b625cf14bb5"),
    ("c3_batch_009", 4_761, "5f3cbb6aca0790393d5cbb9f12f97a760f3e91ab21971f8952ad852aaaa28e08"),
    ("c3_batch_010", 4_728, "5abfb7b45e7e79659fe47c288fe27f96ab1723c2c0d580536a71483c4d9b77cf"),
    ("c3_batch_011", 4_728, "fff43167e0cb0f7feb92cacbfc17ae84aa3a26728d5d143cce3ccecb161cdcfb"),
    ("c3_batch_012", 4_728, "ad6e88dc36d991a4a873bf776f48dacfe0a3eba7c5611a4b354c9e910fbb2297"),
    ("c3_batch_013", 4_728, "d0a7e48072f075539775d826be1e5fc1c28cdb55e2f67169303b78933eb850e0"),
    ("c3_batch_014", 4_728, "9b140c5a053474152c3c1276c532048b4e824476369bb000a7d349e9098717af"),
)
R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES: Final = {
    "recovery_authority_sha256": "9cd0c609ecfd587fc529f9465031973d6d0148e138656816cdc5b307dcbb0b64",
    "recovery_terminal_receipt_sha256": "0072ef8c12a7a4b1c1ae204d0b7d269fe3d2f9970697cf2832fbcd045f30713a",
    "continuation_capacity_receipt_sha256": "a5fddc7764a004b4d79c33aff969e240acbc84ca5aab7978d14c95bc32c53115",
    "continuation_claim_sha256": "418ad72fb488b12d6a5a7bbc6c92bc77cb15cd1484bebd6f59677663d7937305",
    "continuation_submission_receipt_sha256": "3d093415e50fd98b553dc4fd935f8f3be81080d659afd39cabcebe3e400177ab",
}
R8U_FAILED_RECOVERY_ROOT: Final = ATTEMPT_ROOT / "r8u_batch16_recovery"
R8U_FAILED_RECOVERY_SCHEDULER_ROOT: Final = (
    R8U_FAILED_RECOVERY_ROOT / "scheduler"
)
R8U_FAILED_RECOVERY_FILE_AUTHORITIES: Final = {
    "failed_partial_seal.restricted.json": (
        1_267,
        "7188e749dc7ca74ceeb1cc616a5f2a637f4da4649c1a2996530eac90b3aa0d7c",
    ),
    "recovery_authority.restricted.json": (
        4_720,
        "5cb0dd8b79ba31bd1502b20a64dedb48ecef1c313e5004bbd24d3c13aaf381f6",
    ),
    "recovery_capacity.restricted.json": (
        3_723,
        "9adcdd378bfc4e456b8d66f65c8ee572a835d2de2605c7e349b1783c83f23895",
    ),
    "scheduler/lvef_c3_r8u_rec_f3df5cd9.o7352656": (
        116,
        "ce34ae86faad07306c8ffd0850ffdf174c748be982e1406460a10f782e37e805",
    ),
    "scheduler/recovery.qsub.exit_status.restricted": (
        2,
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    ),
    "scheduler/recovery.qsub.stderr.restricted": (
        0,
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ),
    "scheduler/recovery.qsub.stdout.restricted": (
        8,
        "c7b4c678cedc27333a6f1ed32bfe54c9e2188ad34116da7e23c6c350bc2690fa",
    ),
    "scheduler/submission_receipt.restricted.json": (
        1_822,
        "e68e0f503680c6ec9cbff37328738fe812eb83cb9c9cc094c720f8f8a293097e",
    ),
}
R8U_RECOVERY_ROOT: Final = ATTEMPT_ROOT / "r8u_r2_batch16_recovery"
R8U_RECOVERY_SCHEDULER_ROOT: Final = R8U_RECOVERY_ROOT / "scheduler"
R8U_FAILED_PARTIAL_SEAL_PATH: Final = (
    R8U_RECOVERY_ROOT / "failed_partial_seal.restricted.json"
)
R8U_RECOVERY_CAPACITY_PATH: Final = (
    R8U_RECOVERY_ROOT / "recovery_capacity.restricted.json"
)
R8U_RECOVERY_AUTHORITY_PATH: Final = (
    R8U_RECOVERY_ROOT / "recovery_authority.restricted.json"
)
R8U_RECOVERY_SUBMISSION_PATH: Final = (
    R8U_RECOVERY_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_RECOVERY_ACCOUNTING_PATH: Final = (
    R8U_RECOVERY_ROOT / "recovery_accounting.restricted.json"
)
R8U_RECOVERY_TERMINAL_PATH: Final = (
    R8U_RECOVERY_ROOT / "recovery_terminal.aggregate_safe.json"
)
R8U_FRESH_PUBLICATION_PATH: Final = (
    R8U_RECOVERY_ROOT / "fresh_extraction_publication.restricted.json"
)
R8U_FRESH_EXTRACTION_BATCH_ROOT: Final = (
    R8U_RECOVERY_ROOT / "fresh_extracted_cache" / R8U_FIXED_BATCH_ID
)
R8U_CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8u_r2_continuation_17_19"
R8U_CONTINUATION_SCHEDULER_ROOT: Final = R8U_CONTINUATION_ROOT / "scheduler"
R8U_CONTINUATION_CLAIM_PATH: Final = (
    R8U_CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
R8U_CONTINUATION_SUBMISSION_PATH: Final = (
    R8U_CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_RECOVERY_STATUS: Final = "PASS_BATCH16_RECOVERY_FINALIZED"
R8U_CAPACITY_STATUS: Final = (
    "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE"
)

# Phase 1I-R8U-R3 is an additive publication-resume epoch.  The R2 recovery
# below is immutable historical evidence: it completed the scientific
# extraction but failed while publishing that complete directory.  R3 never
# accepts a path, batch, attempt, or retry from a caller.
R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT: Final = (
    "4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff"
)
R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT: Final = (
    "ce3326a23f149dd864c5aa534225b959d7b5abbe"
)
R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS: Final = frozenset(
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
R8U_R2_COMPLETED_EXTRACTION_JOB_ID: Final = "7354951"
R8U_R2_COMPLETED_EXTRACTION_JOB_NAME: Final = "lvef_c3_r8u_rec_4fd8f4bf"
R8U_R2_COMPLETED_EXTRACTION_QACCT_FAILED: Final = 0
R8U_R2_COMPLETED_EXTRACTION_QACCT_EXIT_STATUS: Final = 78
R8U_R2_COMPLETED_EXTRACTION_WALL_SECONDS: Final = 8_992
R8U_R2_COMPLETED_EXTRACTION_FAILURE_CODE: Final = (
    "R8U_FRESH_EXTRACTION_PUBLICATION_FAILED"
)
R8U_R2_COMPLETED_EXTRACTION_LOG_BYTES: Final = 152
R8U_R2_COMPLETED_EXTRACTION_LOG_SHA256: Final = (
    "f4f634690a16c92681b248fffabdee8393624c591791aee0ce6f173071271b65"
)
R8U_R2_SCRIPT_AUTHORITY: Final = {
    "controller_sha256": (
        "3999512325b6605ad99b388446db735955610d604479dcfdbbea2009caeee4e3"
    ),
    "finalizer_sha256": (
        "6656f128605f16319bc98b54ea402a75552c9b14ff6f5f5d273f3a50ab7ebe54"
    ),
    "full_sequential_sha256": (
        "18f37041718d4cedb3a27c9cef30b988e00c56653a372d31f0a2a3b45b395621"
    ),
    "preservation_sha256": (
        "c9360f7dbfb792e33e1c783d8a80ebd45083ad361262d1197deb12a665df16fe"
    ),
    "production_stages_sha256": (
        "caf71e1ebdd5def78105a23363368ef64ee2775027b1c2a2bf05d78292ebe0ac"
    ),
    "retirement_sha256": (
        "15251dd3ba2726875e2df55f76c19d4d07dee03cc5111dc4e48a9f95823871df"
    ),
    "runner_sha256": (
        "a71d97e19ba9d5bb82fd1d793dbc3f5db186fdaf796f0066b29af16e13d6b747"
    ),
}
R8U_R3_ROOT: Final = ATTEMPT_ROOT / "r8u_r3_batch16_publication_resume"
R8U_R3_SCHEDULER_ROOT: Final = R8U_R3_ROOT / "scheduler"
R8U_R3_PROBE_WORK_ROOT: Final = R8U_R3_ROOT / "primitive_probe"
R8U_R3_PROBE_PATH: Final = (
    R8U_R3_ROOT / "publication_primitive_probe.restricted.json"
)
R8U_R3_CANDIDATE_SEAL_PATH: Final = (
    R8U_R3_ROOT / "extraction_candidate_seal.restricted.json"
)
R8U_R3_CAPACITY_PATH: Final = R8U_R3_ROOT / "resume_capacity.restricted.json"
R8U_R3_AUTHORITY_PATH: Final = R8U_R3_ROOT / "resume_authority.restricted.json"
R8U_R3_SUBMISSION_PATH: Final = (
    R8U_R3_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_R3_ACCOUNTING_PATH: Final = R8U_R3_ROOT / "resume_accounting.restricted.json"
R8U_R3_TERMINAL_PATH: Final = (
    R8U_R3_ROOT / "resume_terminal.aggregate_safe.json"
)
R8U_R3_PUBLICATION_PATH: Final = R8U_R3_ROOT / "publication_receipt.restricted.json"
R8U_R3_EXTRACTION_TRANSITION_ROOT: Final = (
    R8U_R3_ROOT / "extraction_transition_receipts"
)
R8U_R3_PUBLICATION_CLAIM_ROOT: Final = (
    ATTEMPT_ROOT
    / "extracted_cache"
    / R8U_FIXED_BATCH_ID
    / ".r8u_r3_publication_claim"
)
R8U_R3_PUBLICATION_CLAIM_PATH: Final = (
    R8U_R3_PUBLICATION_CLAIM_ROOT / "claim.restricted.json"
)
R8U_R3_CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8u_r3_continuation_17_19"
R8U_R3_CONTINUATION_SCHEDULER_ROOT: Final = (
    R8U_R3_CONTINUATION_ROOT / "scheduler"
)
R8U_R3_CONTINUATION_CLAIM_PATH: Final = (
    R8U_R3_CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
R8U_R3_CONTINUATION_SUBMISSION_PATH: Final = (
    R8U_R3_CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_R3_STATUS: Final = "PASS_BATCH16_PUBLICATION_RESUME_FINALIZED"
R8U_R3_CAPACITY_STATUS: Final = (
    "PASS_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE"
)
R8U_R3_CANDIDATE_NPZ_FILES: Final = 10_187
R8U_R3_CANDIDATE_CONTROL_FILES: Final = frozenset(
    {
        "dicom_audit.restricted.csv",
        "extraction_manifest.restricted.csv",
        "technical_disposition_manifest.restricted.csv",
        "dicom_extraction.summary.json",
        "stage_completion_receipt.restricted.json",
    }
)
R8U_R3_CANDIDATE_FAILURE_CODES: Final = frozenset(
    {
        "CANDIDATE_ROOT_AUTHORITY_INVALID",
        "CANDIDATE_TARGET_STATE_INVALID",
        "CANDIDATE_STAGE_COMPLETION_RECEIPT_INVALID",
        "CANDIDATE_STAGE_EVENT_COMMIT_BINDING_INVALID",
        "CANDIDATE_CONTROL_SET_INVALID",
        "CANDIDATE_CONTROL_FILE_MODE_INVALID",
        "CANDIDATE_UNEXPECTED_TRANSIENT_FILE",
        "CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID",
        "CANDIDATE_DICOM_AUDIT_BOOLEAN_DIALECT_MISMATCH",
        "CANDIDATE_DICOM_AUDIT_SEMANTIC_MISMATCH",
        "CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID",
        "CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH",
        "CANDIDATE_EXTRACTION_MANIFEST_PLAN_MISMATCH",
        "CANDIDATE_TECHNICAL_DISPOSITION_MANIFEST_INVALID",
        "CANDIDATE_NPZ_PATH_SET_MISMATCH",
        "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID",
        "CANDIDATE_EXTRACTION_SUMMARY_MISMATCH",
        "CANDIDATE_MOUNT_AUTHORITY_INVALID",
        "GENUINE_COMPLETED_EXTRACTION_INCONSISTENCY",
        "CANDIDATE_FAILURE_UNRESOLVED",
    }
)
R8U_R3_PROCEEDABLE_PROBE_RESULTS: Final = frozenset(
    {
        "RENAME_NOREPLACE_SUPPORTED",
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }
)
R8U_R3_PROCESS_PROJECTION_KEYS: Final = frozenset(
    {
        "status", "matching_processes", "process_snapshot_count",
        "ps_argv_sha256", "ps_stdout_sha256",
    }
)
R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS: Final = frozenset(
    {
        "status", "resume_job_id", "resume_job_name", "state", "category",
        "target_matches", "competing_matching_jobs", "qstat_snapshot_count",
        "qstat_projection_sha256",
    }
)

# The R8T terminal-adjudication projection is retained verbatim as historical
# evidence.  Its directory count includes the attempt root itself.  The
# rejected R8U scanner started at the root's children and therefore reported
# 472 directories even though the same root-inclusive tree still has 473; no
# filesystem directory disappeared.
R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION: Final = {
    "regular_file_count": 1_190_913,
    "directory_count_including_attempt_root": 473,
    "regular_file_bytes": 1_081_833_737_569,
    "symlink_count": 0,
    "nonregular_count": 0,
    "metadata_projection_sha256": (
        "32bf9ad2317b544bcfbfbd47f553d3c4f6476dad5e11fae88e8c8b6d6d28a429"
    ),
}
R8U_DIRECTORY_COMPATIBILITY_ROLE: Final = (
    "ATTEMPT_ROOT_PROJECTION_ROW_OMITTED"
)
# The prompt's closed compatibility vocabulary has no projection-convention
# class.  Retain its proceedable aggregate-safe bucket while the role above
# records the exact fact: no filesystem directory was missing or empty.
R8U_DIRECTORY_DIFFERENCE_CLASS: Final = (
    "BENIGN_OPTIONAL_EMPTY_DIRECTORY_LIFECYCLE"
)
R8U_ATTEMPT_CONTENT_AUTHORITY_KEYS: Final = frozenset(
    {
        "regular_file_count",
        "regular_file_bytes",
        "regular_file_projection_sha256",
        "required_directory_count",
        "required_directory_projection_sha256",
        "optional_empty_directory_count",
        "symlink_count",
        "nonregular_count",
        "path_escape_count",
    }
)

# These four portable baseline values are produced by the metadata-only SCC
# scanner implemented below.  The old digest cannot be reused: it mixed every
# directory (including optional empty directories) with node-local st_dev and
# st_ino values.  Empty placeholders fail closed until the one read-only live
# capture is transcribed into this repair commit.
R8U_BASELINE_REGULAR_FILE_PATH_SET_SHA256: Final = (
    "36d41e28ee46ae2c70735965bafc3af429cc6d62e9aa5a9b67db07b8dff4dfe4"
)
R8U_BASELINE_REGULAR_FILE_PROJECTION_SHA256: Final = (
    "11ec20f9b5d81024affddfa521695a779389ee296f1f2899630a6a1dfaaf5536"
)
R8U_BASELINE_REQUIRED_DIRECTORY_COUNT: Final = 471
R8U_BASELINE_REQUIRED_DIRECTORY_PROJECTION_SHA256: Final = (
    "cb9308386a77950eaba7ab4e17c9f3f82a9fcd4b9ec99590452501c74a2f92a1"
)


def _r8u_relative_role(*parts: str) -> PurePosixPath:
    return PurePosixPath(*parts)


def _r8u_fixed_required_directory_paths() -> frozenset[PurePosixPath]:
    """Return every fixed empty-capable directory role in the sealed prefix."""

    roles = {
        _r8u_relative_role("."),
        _r8u_relative_role("raw"),
        _r8u_relative_role("batches"),
        _r8u_relative_role("extracted_cache"),
        _r8u_relative_role("cache_retirement_authorizations"),
        _r8u_relative_role("scheduler"),
        _r8u_relative_role("r8r_batch3_recovery"),
        _r8u_relative_role("r8r_batch3_recovery", "scheduler"),
        _r8u_relative_role("r8r_continuation"),
        _r8u_relative_role("r8r_continuation", "scheduler"),
        _r8u_relative_role("batches", R8U_FIXED_BATCH_ID),
        _r8u_relative_role("extracted_cache", R8U_FIXED_BATCH_ID),
        _r8u_relative_role(
            "extracted_cache", R8U_FIXED_BATCH_ID, "dicom_extraction.partial"
        ),
        _r8u_relative_role(
            "extracted_cache",
            R8U_FIXED_BATCH_ID,
            "dicom_extraction.partial",
            "clips",
        ),
    }
    for index in range(15):
        batch_id = f"c3_batch_{index:03d}"
        roles.update(
            {
                _r8u_relative_role("raw", batch_id),
                _r8u_relative_role("raw", batch_id, "objects"),
                _r8u_relative_role("raw", batch_id, "receipts"),
                _r8u_relative_role("batches", batch_id),
                _r8u_relative_role("batches", batch_id, "echoprime"),
                _r8u_relative_role("batches", batch_id, "preservation"),
            }
        )
    roles.update(
        {
            _r8u_relative_role("raw", R8U_FIXED_BATCH_ID),
            _r8u_relative_role("raw", R8U_FIXED_BATCH_ID, "objects"),
            _r8u_relative_role("raw", R8U_FIXED_BATCH_ID, "receipts"),
        }
    )
    return frozenset(roles)


R8U_FIXED_REQUIRED_DIRECTORY_PATHS: Final = (
    _r8u_fixed_required_directory_paths()
)
R8U_PROTECTED_DIRECTORY_ROLE_ROOTS: Final = frozenset(
    {
        _r8u_relative_role("raw"),
        _r8u_relative_role("cache_retirement_authorizations"),
        _r8u_relative_role("scheduler"),
        _r8u_relative_role("r8r_batch3_recovery"),
        _r8u_relative_role("r8r_continuation"),
        _r8u_relative_role(
            "extracted_cache", R8U_FIXED_BATCH_ID, "dicom_extraction.partial"
        ),
        *(
            _r8u_relative_role("batches", f"c3_batch_{index:03d}")
            for index in range(16)
        ),
    }
)


def _r8u_successor_exclusion_paths() -> frozenset[PurePosixPath]:
    """Return only fixed paths created by the authorized R8U successor."""

    paths = {
        # The consumed R8U-R1 epoch remains traversed as immutable evidence.
        _r8u_relative_role("r8u_batch16_recovery"),
        _r8u_relative_role("r8u_r2_batch16_recovery"),
        _r8u_relative_role("r8u_r2_continuation_17_19"),
        _r8u_relative_role("r8u_r3_batch16_publication_resume"),
        _r8u_relative_role("r8u_r3_continuation_17_19"),
        _r8u_relative_role(
            "extracted_cache", R8U_FIXED_BATCH_ID,
            ".r8u_r3_publication_claim",
        ),
        _r8u_relative_role(
            "extracted_cache", R8U_FIXED_BATCH_ID, "dicom_extraction"
        ),
        _r8u_relative_role("batches", R8U_FIXED_BATCH_ID, "echoprime"),
        _r8u_relative_role("batches", R8U_FIXED_BATCH_ID, "preservation"),
        _r8u_relative_role(
            "batches", R8U_FIXED_BATCH_ID,
            "extraction_resume_ledger.restricted.json",
        ),
        _r8u_relative_role(
            "batches", R8U_FIXED_BATCH_ID,
            "pooling_resume_ledger.restricted.json",
        ),
        _r8u_relative_role(
            "batches", R8U_FIXED_BATCH_ID,
            "cache_retirement_eligible_resume_ledger.restricted.json",
        ),
        _r8u_relative_role(
            "batches", R8U_FIXED_BATCH_ID,
            "final_resume_ledger.restricted.json",
        ),
        _r8u_relative_role(
            "cache_retirement_authorizations",
            f"{R8U_FIXED_BATCH_ID}.authorization.json",
        ),
        _r8u_relative_role("cohort_finalization"),
    }
    for index in R8U_FIXED_CONTINUATION_TASK_IDS:
        batch_id = f"c3_batch_{index - 1:03d}"
        paths.update(
            {
                _r8u_relative_role("raw", batch_id),
                _r8u_relative_role("batches", batch_id),
                _r8u_relative_role("extracted_cache", batch_id),
                _r8u_relative_role(
                    "cache_retirement_authorizations",
                    f"{batch_id}.authorization.json",
                ),
            }
        )
    return frozenset(paths)


R8U_SUCCESSOR_EXCLUSION_PATHS: Final = _r8u_successor_exclusion_paths()
R8U_FAILED_RECOVERY_RELATIVE_ROOT: Final = PurePosixPath(
    "r8u_batch16_recovery"
)


def _r8u_continuation_pristine_paths() -> frozenset[PurePosixPath]:
    """Return outputs that must still be pristine before Tasks 17--19."""

    paths = {
        _r8u_relative_role("r8u_r2_continuation_17_19"),
        _r8u_relative_role("cohort_finalization"),
    }
    for index in R8U_FIXED_CONTINUATION_TASK_IDS:
        batch_id = f"c3_batch_{index - 1:03d}"
        paths.update(
            {
                _r8u_relative_role("raw", batch_id),
                _r8u_relative_role("batches", batch_id),
                _r8u_relative_role("extracted_cache", batch_id),
                _r8u_relative_role(
                    "cache_retirement_authorizations",
                    f"{batch_id}.authorization.json",
                ),
            }
        )
    return frozenset(paths)


R8U_CONTINUATION_PRISTINE_PATHS: Final = (
    _r8u_continuation_pristine_paths()
)
R8U_CONTINUATION_TASK_OUTPUT_PRISTINE_PATHS: Final = frozenset(
    path
    for path in R8U_CONTINUATION_PRISTINE_PATHS
    if path != _r8u_relative_role("r8u_r2_continuation_17_19")
)

R8U_R3_COMMON_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "original_scientific_commit", "implementation_commit",
        "implementation_authority_epochs", "attempt_id",
        "batch_plan_sha256", "batch_id",
    }
)
R8U_R3_CANDIDATE_SEAL_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "r2_recovery_job_id", "r2_recovery_capacity_receipt_sha256",
        "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256",
        "failed_partial_seal_sha256", "stage_completion_receipt_sha256",
        "extraction_manifest_sha256", "dicom_audit_sha256",
        "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
        "candidate_regular_files", "candidate_directories",
        "candidate_total_bytes", "candidate_npz_files",
        "candidate_npz_bytes", "candidate_relative_file_projection_sha256",
        "candidate_relative_directory_projection_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_root_identity_sha256", "source_parent_identity_sha256",
        "source_mount_identity_sha256", "target_parent_identity_sha256",
        "target_mount_identity_sha256",
        "source_target_same_mounted_filesystem", "target_absent",
        "symlink_count", "nonregular_count", "n_selected_studies",
        "n_source_objects", "source_bytes", "n_readable", "n_unreadable",
        "n_multiframe_candidates", "n_single_frame",
        "n_pixel_decode_failures", "n_successfully_extracted_cines",
        "n_object_technical_dispositions", "n_blocking_failures",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "object_substitution_count", "extraction_status", "npz_body_reads",
        "dicom_body_reads", "dicom_extraction_executions",
        "cloud_requests", "downloads",
    }
)
R8U_R3_PROBE_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "primary_primitive", "primary_result", "primary_errno",
        "primary_errno_number",
        "primary_returned_success", "real_source_parent_identity_sha256",
        "real_target_parent_identity_sha256",
        "real_source_mount_identity_sha256",
        "real_target_mount_identity_sha256",
        "real_parents_same_mounted_filesystem",
        "probe_mount_identity_sha256", "probe_mount_matches_real_parents",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "probe_directories_created", "probe_directories_removed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R3_PUBLICATION_CLAIM_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "resume_job_id", "r2_recovery_job_id",
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256", "resume_authority_sha256",
        "resume_submission_receipt_sha256", "failed_partial_seal_sha256",
        "candidate_relative_file_projection_sha256", "target_role",
        "publication_primitive_selected", "primary_result", "target_absent",
        "source_target_same_mounted_filesystem",
        "source_parent_identity_sha256", "target_parent_identity_sha256",
        "source_mount_identity_sha256", "target_mount_identity_sha256",
        "pre_qsub_process_projection_sha256",
        "initial_qstat_projection_sha256",
        "worker_process_projection",
        "competing_active_jobs", "competing_active_processes",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads",
    }
)
R8U_R3_PUBLICATION_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "primary_errno",
        "fallback_used", "fallback_primitive",
        "rename_returned_success", "real_rename_returned_success",
        "real_rename_errno", "real_rename_errno_number",
        "real_rename_errno_classification",
        "publication_ruling",
        "prepublication_candidate_sha256",
        "postpublication_target_sha256", "source_absent", "target_exact",
        "candidate_npz_files", "candidate_total_bytes", "files_moved",
        "files_copied", "files_deleted_independently", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads", "cloud_requests",
        "downloads",
    }
)
R8U_R3_AUTHORITY_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "prior_implementation_commit", "original_task_id",
        "continuation_task_range", "prefix_final_receipt_sha256",
        "historical_r8r_chain_authority",
        "failed_r8u_recovery_epoch_authority_sha256",
        "r2_recovery_job_id", "r2_recovery_log_sha256",
        "r2_recovery_capacity_receipt_sha256",
        "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256",
        "failed_partial_seal_sha256", "extraction_candidate_seal_sha256",
        "resume_capacity_sha256", "runtime_authority_sha256",
        "qsub_environment_sha256", "script_authority",
        "runtime_validation_context", "target_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "failed_partial_adoption_authorized",
        "failed_partial_mutation_authorized", "raw_dicom_deletion_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_qsub_submissions", "pre_qsub_process_projection",
    }
)
R8U_R3_SUBMISSION_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "original_task_id", "resume_job_name", "resume_job_id",
        "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_authority_sha256", "extraction_candidate_seal_sha256",
        "resume_capacity_sha256", "resume_qsub_evidence",
        "scheduler_submission_count", "resume_is_array", "gpu_requested",
        "automatic_retry_authorized", "cloud_requests", "downloads",
        "dicom_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "npz_body_reads_by_submitter",
        "model_fitting_count", "prediction_generation_count",
        "confirmatory_performance_access_count", "pre_qsub_process_projection",
        "initial_qstat_projection",
    }
)
R8U_R3_TERMINAL_KEYS: Final = R8U_R3_COMMON_KEYS | frozenset(
    {
        "original_task_id", "failed_partial_seal_sha256",
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "n_selected_studies",
        "n_expected_objects", "expected_source_bytes",
        "n_successfully_extracted_cines", "n_object_technical_dispositions",
        "n_blocking_failures", "n_clip_embeddings", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
        "object_substitution_count", "unaccounted_multiframe_objects",
        "raw_dicoms_retained", "canonical_extraction_cache_retired",
        "failed_partial_cache_retained", "source_candidate_npz_files",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R3_CONTINUATION_LINK_KEYS: Final = frozenset(
    {
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R3_CONTINUATION_CLAIM_KEYS: Final = (
    R8U_R3_COMMON_KEYS | R8U_R3_CONTINUATION_LINK_KEYS | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fourth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized",
            "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R3_CONTINUATION_SUBMISSION_KEYS: Final = (
    R8U_R3_COMMON_KEYS | R8U_R3_CONTINUATION_LINK_KEYS | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fourth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        }
    )
)

# Phase 1I-R8U-R4 is a strictly additive portability repair.  R3 remains a
# consumed, immutable failure epoch; none of these paths aliases an R3 path.
R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT: Final = (
    "a6e80b606a76dde2512a8eedf4eb8fa4f87211ef"
)
R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID: Final = "7364184"
R8U_R3_FAILED_PUBLICATION_RESUME_JOB_NAME: Final = (
    "lvef_c3_r8u_r3_res_a6e80b60"
)
R8U_R3_CANDIDATE_SEAL_SHA256: Final = (
    "cfb0b19044db53742c1fd6121f8d33b567f6a62c2661050cf4df2cc4e6f2cbf2"
)
R8U_R3_FAILED_PUBLICATION_RESUME_LOG_BYTES: Final = 110
R8U_R3_FAILED_PUBLICATION_RESUME_LOG_SHA256: Final = (
    "bc2feef6398e3f9be408a77c80fe6eba38dad98506671d96b893371a5b59f824"
)
R8U_R3_FAILED_PUBLICATION_RESUME_LOG_MODE: Final = "0644"
R8U_R3_FAILED_PUBLICATION_RESUME_LOG_PATH: Final = (
    R8U_R3_SCHEDULER_ROOT
    / f"{R8U_R3_FAILED_PUBLICATION_RESUME_JOB_NAME}.o"
    f"{R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID}"
)

R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS: Final = frozenset(
    {
        "scientific_commit", "r8r_implementation_commit",
        "r8u_base_implementation_commit", "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
        "r8u_publication_resume_repair_commit",
        "r8u_candidate_authority_repair_commit",
        "r8u_portability_repair_commit",
    }
)
R8U_R4_ROOT: Final = ATTEMPT_ROOT / "r8u_r4_batch16_publication_resume"
R8U_R4_SCHEDULER_ROOT: Final = R8U_R4_ROOT / "scheduler"
R8U_R4_DIAGNOSIS_PATH: Final = (
    R8U_R4_ROOT / "candidate_replay_diagnosis.restricted.json"
)
R8U_R4_PORTABLE_AUTHORITY_PATH: Final = (
    R8U_R4_ROOT / "portable_candidate_authority.restricted.json"
)
R8U_R4_CAPACITY_PATH: Final = R8U_R4_ROOT / "resume_capacity.restricted.json"
R8U_R4_AUTHORITY_PATH: Final = R8U_R4_ROOT / "resume_authority.restricted.json"
R8U_R4_SUBMISSION_PATH: Final = (
    R8U_R4_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_R4_LOCALITY_PATH: Final = (
    R8U_R4_ROOT / "live_publication_locality.restricted.json"
)
R8U_R4_PROBE_PATH: Final = (
    R8U_R4_ROOT / "publication_primitive_probe.restricted.json"
)
R8U_R4_PUBLICATION_PATH: Final = (
    R8U_R4_ROOT / "publication_receipt.restricted.json"
)
R8U_R4_ACCOUNTING_PATH: Final = (
    R8U_R4_ROOT / "resume_accounting.restricted.json"
)
R8U_R4_TERMINAL_PATH: Final = (
    R8U_R4_ROOT / "resume_terminal.aggregate_safe.json"
)
R8U_R4_EXTRACTION_TRANSITION_ROOT: Final = (
    R8U_R4_ROOT / "extraction_transition_receipts"
)
R8U_R4_PUBLICATION_CLAIM_ROOT: Final = (
    ATTEMPT_ROOT / "extracted_cache" / R8U_FIXED_BATCH_ID
    / ".r8u_r4_publication_claim"
)
R8U_R4_PUBLICATION_CLAIM_PATH: Final = (
    R8U_R4_PUBLICATION_CLAIM_ROOT / "claim.restricted.json"
)
R8U_R4_PROBE_WORK_ROOT: Final = (
    R8U_R4_ROOT / "primitive_probe"
)
R8U_R4_CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8u_r4_continuation_17_19"
R8U_R4_CONTINUATION_SCHEDULER_ROOT: Final = (
    R8U_R4_CONTINUATION_ROOT / "scheduler"
)
R8U_R4_CONTINUATION_CLAIM_PATH: Final = (
    R8U_R4_CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
R8U_R4_CONTINUATION_SUBMISSION_PATH: Final = (
    R8U_R4_CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_R4_STATUS: Final = "PASS_BATCH16_R8U_R4_PUBLICATION_RESUME_FINALIZED"
R8U_R4_CAPACITY_STATUS: Final = (
    "PASS_R8U_R4_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE"
)

R8U_R4_COMMON_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "original_scientific_commit", "implementation_commit",
        "implementation_authority_epochs", "attempt_id",
        "batch_plan_sha256", "batch_id",
    }
)
R8U_R4_PORTABLE_METADATA_FIELDS: Final = frozenset(
    {
        "candidate_regular_files", "candidate_directories",
        "candidate_total_bytes", "candidate_npz_files", "candidate_npz_bytes",
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_relative_file_portable_projection_sha256",
        "candidate_relative_directory_path_set_sha256",
        "candidate_relative_directory_portable_projection_sha256",
        "candidate_root_portable_identity_sha256", "symlink_count",
        "nonregular_count",
    }
)
R8U_R4_CONTROL_HASH_FIELDS: Final = frozenset(
    {
        "stage_completion_receipt_sha256", "extraction_manifest_sha256",
        "dicom_audit_sha256", "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
    }
)
R8U_R4_SEMANTIC_FIELDS: Final = frozenset(
    {
        "canonical_stage_event_authority_sha256", "input_manifest_sha256",
        "candidate_npz_manifest_projection_sha256", "n_selected_studies",
        "n_source_objects", "source_bytes", "n_readable", "n_unreadable",
        "n_multiframe_candidates", "n_single_frame",
        "n_pixel_decode_failures", "n_successfully_extracted_cines",
        "n_object_technical_dispositions", "n_blocking_failures",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "object_substitution_count", "extraction_status",
    }
)
R8U_R4_PORTABLE_CONTENT_FIELDS: Final = (
    R8U_R4_PORTABLE_METADATA_FIELDS | R8U_R4_CONTROL_HASH_FIELDS
    | R8U_R4_SEMANTIC_FIELDS
)
R8U_R4_NODE_LOCAL_DIAGNOSTIC_FIELDS: Final = frozenset(
    {
        "source_parent_identity_sha256", "target_parent_identity_sha256",
        "source_mount_identity_sha256", "target_mount_identity_sha256",
        "source_parent_st_dev_inode_authority",
        "target_parent_st_dev_inode_authority", "mount_id",
        "mount_source_identity", "candidate_file_mtime_ctime_projection",
        "candidate_file_mtime_projection_sha256",
        "candidate_file_ctime_projection_sha256",
        "directory_nlink_projection_sha256",
        "directory_link_count_projection", "candidate_root_identity_sha256",
        "candidate_relative_file_projection_sha256",
        "candidate_relative_directory_projection_sha256",
    }
)
R8U_R4_ROLE_SPECIFIC_ERRORS: Final = frozenset(
    {
        "R8U_PORTABLE_CANDIDATE_CONTROL_HASH_MISMATCH",
        "R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH",
        "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH",
        "R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH",
        "R8U_LIVE_PUBLICATION_SOURCE_INVALID",
        "R8U_LIVE_PUBLICATION_TARGET_INVALID",
        "R8U_LIVE_PUBLICATION_CROSS_MOUNT",
        "R8U_LIVE_PUBLICATION_PARENT_CHANGED",
        "R8U_LIVE_PUBLICATION_CLAIM_INVALID",
    }
)
R8U_R4_DIAGNOSIS_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status", "job_id",
        "candidate_seal_sha256", "first_differing_field",
        "differing_fields", "portable_differing_fields",
        "node_local_differing_fields", "portable_candidate_fields_equal",
        "node_local_only_differences", "candidate_path_set_equal",
        "candidate_count_and_bytes_equal", "zero_body_reads",
        "dicom_body_reads", "npz_body_reads", "final_classification",
    }
)
R8U_R4_PORTABLE_AUTHORITY_KEYS: Final = (
    R8U_R4_COMMON_KEYS | R8U_R4_PORTABLE_CONTENT_FIELDS | frozenset(
        {
            "failed_r8u_r3_job_id", "r8u_r3_candidate_seal_sha256",
            "candidate_replay_diagnosis_sha256", "portable_projection_status",
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
        }
    )
)
R8U_R4_LOCALITY_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status", "publication_claim_sha256",
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call",
        "source_identity_sha256", "source_parent_identity_sha256",
        "target_parent_identity_sha256", "source_mount_identity_sha256",
        "target_mount_identity_sha256", "competing_active_jobs",
        "competing_active_processes",
    }
)
R8U_R4_PUBLICATION_CLAIM_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "resume_job_id", "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256", "resume_authority_sha256",
        "resume_submission_receipt_sha256", "worker_process_projection_sha256",
        "worker_qstat_projection_sha256", "target_role", "target_absent",
        "competing_active_jobs", "competing_active_processes",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads",
    }
)
R8U_R4_PROBE_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "live_publication_locality_sha256", "publication_claim_sha256",
        "primary_primitive", "primary_result", "primary_errno",
        "primary_errno_number", "primary_returned_success",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R4_PUBLICATION_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "fallback_used",
        "rename_returned_success", "real_rename_errno",
        "real_rename_errno_number", "real_rename_errno_classification",
        "publication_ruling", "prepublication_candidate_sha256",
        "postpublication_root_identity_sha256", "source_absent",
        "target_exact", "candidate_npz_files", "candidate_total_bytes",
        "files_moved", "files_copied", "files_deleted_independently",
        "dicom_body_reads", "dicom_extraction_executions", "npz_body_reads",
        "cloud_requests", "downloads",
    }
)
R8U_R4_AUTHORITY_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "prior_implementation_commit", "original_task_id",
        "continuation_task_range", "prefix_final_receipt_sha256",
        "historical_r8r_chain_authority", "failed_partial_seal_sha256",
        "r2_recovery_capacity_receipt_sha256", "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256", "failed_r8u_r3_job_id",
        "r8u_r3_candidate_seal_sha256", "r8u_r3_scheduler_log_sha256",
        "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256", "resume_capacity_sha256",
        "runtime_authority_sha256", "qsub_environment_sha256",
        "script_authority", "runtime_validation_context", "target_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "failed_partial_adoption_authorized", "failed_partial_mutation_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_qsub_submissions", "pre_qsub_process_projection",
    }
)
R8U_R4_SUBMISSION_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "original_task_id", "resume_job_name", "resume_job_id",
        "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_authority_sha256", "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256", "resume_capacity_sha256",
        "resume_qsub_evidence", "scheduler_submission_count",
        "resume_is_array", "gpu_requested", "automatic_retry_authorized",
        "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "npz_body_reads_by_submitter",
        "model_fitting_count", "prediction_generation_count",
        "confirmatory_performance_access_count", "pre_qsub_process_projection",
        "initial_qstat_projection",
    }
)
R8U_R4_ACCOUNTING_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "original_task_id", "resume_job_id", "failed", "exit_status",
        "accounting_projection",
    }
)
R8U_R4_TERMINAL_KEYS: Final = R8U_R4_COMMON_KEYS | frozenset(
    {
        "original_task_id", "failed_partial_seal_sha256",
        "r8u_r3_candidate_seal_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "n_selected_studies",
        "n_expected_objects", "expected_source_bytes",
        "n_successfully_extracted_cines", "n_object_technical_dispositions",
        "n_blocking_failures", "n_clip_embeddings", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
        "object_substitution_count", "unaccounted_multiframe_objects",
        "raw_dicoms_retained", "canonical_extraction_cache_retired",
        "failed_partial_cache_retained", "source_candidate_npz_files",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R4_CONTINUATION_LINK_KEYS: Final = frozenset(
    {
        "r8u_r3_candidate_seal_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R4_CONTINUATION_CLAIM_KEYS: Final = (
    R8U_R4_COMMON_KEYS | R8U_R4_CONTINUATION_LINK_KEYS | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fourth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized",
            "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R4_CONTINUATION_SUBMISSION_KEYS: Final = (
    R8U_R4_COMMON_KEYS | R8U_R4_CONTINUATION_LINK_KEYS | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fourth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        }
    )
)

# Phase 1I-R8U-R5 is a fresh, no-clobber scheduler-context repair epoch.  The
# consumed R4 namespace remains immutable and is referenced only by digest.
R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT: Final = (
    "6eb5c9a4337ca4569ecd0d3157084fb4b76adfac"
)
R8U_R4_FAILED_SCHEDULER_IDENTITY_JOB_ID: Final = "7375270"
R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BASENAME: Final = (
    "lvef_c3_r8u_r4_res_6eb5c9a4.o7375270"
)
R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BYTES: Final = 108
R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_MODE: Final = 0o644
R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_SHA256: Final = (
    "c27f6cca757a3ffc26ca1f7211f44a691137fb736f62a5db13825e9b2ae632e8"
)
R8U_R5_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS: Final = (
    R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    | {"r8u_worker_context_repair_commit"}
)
R8U_R5_ROOT: Final = ATTEMPT_ROOT / "r8u_r5_batch16_publication_resume"
R8U_R5_SCHEDULER_ROOT: Final = R8U_R5_ROOT / "scheduler"
R8U_R5_ACCOUNT_AUTHORITY_PATH: Final = (
    R8U_R5_ROOT / "scheduler_account_authority.restricted.json"
)
R8U_R5_R4_FAILURE_EVIDENCE_PATH: Final = (
    R8U_R5_ROOT / "r8u_r4_failure_evidence.restricted.json"
)
R8U_R5_PROBE_AUTHORITY_PATH: Final = (
    R8U_R5_ROOT / "worker_context_probe_authority.restricted.json"
)
R8U_R5_PROBE_SUBMISSION_PATH: Final = (
    R8U_R5_SCHEDULER_ROOT / "probe_submission_receipt.restricted.json"
)
R8U_R5_PROBE_DIAGNOSTIC_PATH: Final = (
    R8U_R5_ROOT / "worker_context_probe_diagnostic.restricted.json"
)
R8U_R5_GPU_DIAGNOSTIC_PATH: Final = (
    R8U_R5_ROOT / "gpu_worker_context_diagnostic.restricted.json"
)
R8U_R5_PROBE_RECEIPT_PATH: Final = (
    R8U_R5_ROOT / "worker_context_probe_receipt.restricted.json"
)
R8U_R5_PROBE_ACCOUNTING_PATH: Final = (
    R8U_R5_ROOT / "worker_context_probe_accounting.restricted.json"
)
R8U_R5_CAPACITY_PATH: Final = R8U_R5_ROOT / "resume_capacity.restricted.json"
R8U_R5_AUTHORITY_PATH: Final = R8U_R5_ROOT / "resume_authority.restricted.json"
R8U_R5_SUBMISSION_PATH: Final = (
    R8U_R5_SCHEDULER_ROOT / "resume_submission_receipt.restricted.json"
)
R8U_R5_LOCALITY_PATH: Final = (
    R8U_R5_ROOT / "live_publication_locality.restricted.json"
)
R8U_R5_PUBLICATION_CLAIM_ROOT: Final = (
    ATTEMPT_ROOT / "extracted_cache" / R8U_FIXED_BATCH_ID
    / ".r8u_r5_publication_claim"
)
R8U_R5_PUBLICATION_CLAIM_PATH: Final = (
    R8U_R5_PUBLICATION_CLAIM_ROOT / "claim.restricted.json"
)
R8U_R5_PRIMITIVE_PROBE_PATH: Final = (
    R8U_R5_ROOT / "publication_primitive_probe.restricted.json"
)
R8U_R5_PRIMITIVE_PROBE_WORK_ROOT: Final = R8U_R5_ROOT / "primitive_probe"
R8U_R5_PUBLICATION_PATH: Final = R8U_R5_ROOT / "publication_receipt.restricted.json"
R8U_R5_EXTRACTION_TRANSITION_ROOT: Final = (
    R8U_R5_ROOT / "extraction_transition_receipts"
)
R8U_R5_ACCOUNTING_PATH: Final = R8U_R5_ROOT / "resume_accounting.restricted.json"
R8U_R5_TERMINAL_PATH: Final = (
    R8U_R5_ROOT / "resume_terminal.aggregate_safe.json"
)
R8U_R5_CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8u_r5_continuation_17_19"
R8U_R5_CONTINUATION_SCHEDULER_ROOT: Final = (
    R8U_R5_CONTINUATION_ROOT / "scheduler"
)
R8U_R5_CONTINUATION_CLAIM_PATH: Final = (
    R8U_R5_CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
R8U_R5_CONTINUATION_SUBMISSION_PATH: Final = (
    R8U_R5_CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
R8U_R5_PROBE_ROLE: Final = "R8U_R5_WORKER_CONTEXT_PROBE"
R8U_R5_RESUME_ROLE: Final = "R8U_R5_BATCH16_PUBLICATION_RESUME"
R8U_R5_ARRAY_ROLE: Final = "R8U_R5_CONTINUATION_ARRAY"
R8U_R5_FINALIZER_ROLE: Final = "R8U_R5_COHORT_FINALIZER"
R8U_R5_WORKER_ROLES: Final = (
    R8U_R5_PROBE_ROLE,
    R8U_R5_RESUME_ROLE,
    R8U_R5_ARRAY_ROLE,
    R8U_R5_FINALIZER_ROLE,
)
R8U_R5_CAPACITY_STATUS: Final = (
    "PASS_R8U_R5_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE"
)
R8U_R5_TERMINAL_STATUS: Final = (
    "PASS_BATCH16_R8U_R5_PUBLICATION_RESUME_FINALIZED"
)
R8U_R5_COMMON_KEYS: Final = R8U_R4_COMMON_KEYS
R8U_R5_ACCOUNT_AUTHORITY_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "expected_effective_uid", "expected_scheduler_username",
        "canonical_home", "submitter_passwd_lookup_available",
        "runner_sha256", "python_sha256", "qsub_environment_sha256",
        "sealed_qsub_environment", "authorized_worker_roles",
    }
)
R8U_R5_R4_FAILURE_EVIDENCE_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "failed_job_id", "scheduler_failed", "application_exit_status",
        "wall_seconds", "first_failed_stage", "exact_failure_code",
        "scheduler_log_basename", "scheduler_log_bytes", "scheduler_log_mode",
        "scheduler_log_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256", "r8u_r4_capacity_sha256",
        "r8u_r4_resume_authority_sha256", "r8u_r4_submission_sha256",
        "portable_candidate_pass", "publication_locality_ran",
        "publication_ran", "echoprime_ran",
    }
)
R8U_R5_PROBE_AUTHORITY_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "qsub_environment_sha256",
        "r8u_r4_failure_evidence_sha256",
        "portable_candidate_authority_sha256", "script_authority",
        "worker_role", "wall_seconds_maximum",
        "cpu_slots", "gpu_requested", "array_requested",
        "candidate_scan_authorized", "cloud_requests_authorized",
        "dicom_body_reads_authorized", "npz_body_reads_authorized",
        "publication_authorized", "extraction_authorized",
        "embedding_generation_authorized", "preservation_authorized",
        "scientific_attempt_mutation_authorized",
    }
)
R8U_R5_PROBE_SUBMISSION_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "r8u_r4_failure_evidence_sha256",
        "portable_candidate_authority_sha256",
        "probe_job_name", "probe_job_id", "probe_qsub_argv_sha256",
        "qsub_environment_sha256", "probe_qsub_evidence",
        "scheduler_submission_count", "cpu_slots", "gpu_requested",
        "probe_is_array", "automatic_retry_authorized",
    }
)
R8U_R5_WORKER_DIAGNOSTIC_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "user_present", "logname_present", "home_present", "shell_present",
        "observed_user_match", "observed_logname_match",
        "observed_home_match", "observed_shell_match",
        "passwd_lookup_available", "passwd_name_match",
        "passwd_home_match", "passwd_shell_match", "passwd_lookup_status",
        "effective_uid_match", "job_id_match", "task_context_match",
        "job_role_match",
        "runner_sha256_match", "python_sha256_match",
        "implementation_commit_match", "qsub_environment_sha256_match",
        "classifications", "canonical_worker_environment_status",
    }
)
R8U_R5_PROBE_RECEIPT_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256", "probe_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "probe_job_id", "worker_role", "effective_uid_match",
        "job_id_match", "task_context_match", "job_role_match",
        "canonical_worker_environment_pass",
        "candidate_scans", "cloud_requests", "dicom_body_reads",
        "npz_body_reads", "publication_executions", "extraction_executions",
        "embedding_generations", "preservation_executions",
        "scientific_attempt_mutations",
    }
)
R8U_R5_PROBE_ACCOUNTING_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256",
        "probe_job_id", "failed", "exit_status", "accounting_projection",
        "probe_receipt_sha256", "probe_scheduler_log_sha256",
    }
)
R8U_R5_CAPACITY_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "worker_context_probe_accounting_sha256",
        "r8u_r4_capacity_authority_sha256", "fresh_capacity_observation",
        "fresh_capacity_observation_sha256", "candidate_seal_sha256",
        "candidate_total_bytes", "quota_reserve_bytes",
        "physical_reserve_bytes", "file_slot_reserve_pass",
    }
)
R8U_R5_RESUME_AUTHORITY_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_authority_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256",
        "portable_candidate_authority_sha256", "resume_capacity_sha256",
        "prefix_final_receipt_sha256", "runtime_authority_sha256",
        "qsub_environment_sha256", "script_authority", "worker_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_gpu_resume_qsubs",
    }
)
R8U_R5_RESUME_SUBMISSION_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_authority_sha256",
        "worker_context_probe_authority_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256", "resume_capacity_sha256",
        "portable_candidate_authority_sha256", "resume_job_name",
        "resume_job_id", "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_qsub_evidence", "initial_qstat_projection",
        "scheduler_submission_count", "resume_is_array", "gpu_requested",
        "automatic_retry_authorized", "cloud_requests", "downloads",
        "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R5_LOCALITY_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "schema_version", "artifact_type", "status",
        "scheduler_account_authority_sha256", "worker_context_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call", "source_identity_sha256",
        "source_parent_identity_sha256", "target_parent_identity_sha256",
        "source_mount_identity_sha256", "target_mount_identity_sha256",
        "competing_active_jobs", "competing_active_processes",
    }
)
R8U_R5_PUBLICATION_CLAIM_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_job_id",
        "portable_candidate_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "live_publication_locality_sha256", "worker_context_diagnostic_sha256",
        "worker_process_projection_sha256", "worker_qstat_projection_sha256",
        "target_role", "target_absent", "competing_active_jobs",
        "competing_active_processes", "cloud_requests", "downloads",
        "dicom_body_reads", "dicom_extraction_executions", "npz_body_reads",
    }
)
R8U_R5_PRIMITIVE_PROBE_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "live_publication_locality_sha256",
        "publication_claim_sha256", "primary_primitive", "primary_result",
        "primary_errno", "primary_errno_number", "primary_returned_success",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "probe_directories_created", "probe_directories_removed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R5_PUBLICATION_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "portable_candidate_authority_sha256",
        "r8u_r4_failure_evidence_sha256", "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "fallback_used",
        "rename_returned_success", "real_rename_errno",
        "real_rename_errno_number", "real_rename_errno_classification",
        "publication_ruling", "prepublication_candidate_sha256",
        "postpublication_root_identity_sha256", "source_absent", "target_exact",
        "candidate_npz_files", "candidate_total_bytes", "files_moved",
        "files_copied", "files_deleted_independently", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads", "cloud_requests",
        "downloads",
    }
)
R8U_R5_TERMINAL_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256",
        "worker_context_diagnostic_sha256", "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "preservation_receipt_sha256", "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "n_selected_studies",
        "n_expected_objects", "expected_source_bytes",
        "n_successfully_extracted_cines", "n_object_technical_dispositions",
        "n_blocking_failures", "n_clip_embeddings", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
        "object_substitution_count", "unaccounted_multiframe_objects",
        "raw_dicoms_retained", "canonical_extraction_cache_retired",
        "failed_partial_cache_retained", "source_candidate_npz_files",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R5_RESUME_ACCOUNTING_KEYS: Final = R8U_R5_COMMON_KEYS | frozenset(
    {
        "original_task_id", "resume_job_id", "failed", "exit_status",
        "accounting_projection",
    }
)
R8U_R5_CONTINUATION_LINK_KEYS: Final = frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R5_CONTINUATION_CLAIM_KEYS: Final = (
    R8U_R5_COMMON_KEYS
    | R8U_R5_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fifth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized", "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R5_CONTINUATION_SUBMISSION_KEYS: Final = (
    R8U_R5_COMMON_KEYS
    | R8U_R5_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fifth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        }
    )
)
R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS: Final = (
    R8U_R5_COMMON_KEYS
    | frozenset(
        {
            "scheduler_account_authority_sha256",
            "continuation_claim_sha256", "continuation_submission_sha256",
            "worker_diagnostic_sha256", "worker_qstat_projection_sha256",
            "worker_process_projection_sha256", "job_id", "worker_role",
            "task_id", "effective_uid_match", "job_id_match",
            "task_context_match", "job_role_match",
            "canonical_worker_environment_pass",
        }
    )
)


@dataclass(frozen=True)
class _R8UR3RenameResult:
    returned_success: bool
    errno_number: int
    errno_name: str


@dataclass(frozen=True)
class _R8UR3CandidateProjection:
    value: Mapping[str, Any]
    expected_npz_paths: frozenset[PurePosixPath]


@dataclass(frozen=True)
class _R8UR3CanonicalExtractionManifest:
    rows: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    expected_npz_paths: frozenset[PurePosixPath]
    manifest_projection: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _R8UR4PortableProjection:
    """Portable public projection plus worker-local same-call race guards."""

    value: Mapping[str, Any]
    expected_npz_paths: frozenset[PurePosixPath]
    regular_file_paths: frozenset[PurePosixPath]
    directory_paths: frozenset[PurePosixPath]
    file_rows: tuple[tuple[Any, ...], ...]
    directory_rows: tuple[tuple[Any, ...], ...]
    root_local_identity: Mapping[str, Any]


@dataclass(frozen=True)
class _R8UR4LivePublicationLocality(Mapping[str, Any]):
    """Persistable locality facts with opaque in-process identities hidden."""

    value: Mapping[str, Any]
    source_identity: Mapping[str, Any]
    source_parent_identity: Mapping[str, Any]
    target_parent_identity: Mapping[str, Any]
    source_mount_key: Any
    target_mount_key: Any

    def __getitem__(self, key: str) -> Any:
        return self.value[key]

    def __iter__(self):
        return iter(self.value)

    def __len__(self) -> int:
        return len(self.value)


class R8RControllerError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        stage: str | None = None,
        validation_substage: str | None = None,
        capacity_deficits: Mapping[str, int] | None = None,
    ):
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R8R_UNEXPECTED_SANITIZED_FAILURE"
        allowed_substages = {
            item.value for item in preservation.PreservationValidationSubstage
        }
        if (
            validation_substage is not None
            and validation_substage not in allowed_substages
        ):
            validation_substage = None
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.validation_substage = validation_substage
        allowed_deficits = {
            "quota_deficit_bytes",
            "physical_deficit_bytes",
            "file_slot_deficit",
        }
        if capacity_deficits is None:
            self.capacity_deficits: dict[str, int] = {}
        elif (
            set(capacity_deficits) != allowed_deficits
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in capacity_deficits.values()
            )
        ):
            self.capacity_deficits = {}
        else:
            self.capacity_deficits = dict(capacity_deficits)


def _fail(code: str, *, stage: str | None = None) -> None:
    raise R8RControllerError(code, stage=stage)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise R8RControllerError("R8R_CONTROL_SCHEMA_INVALID") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_private(path: Path, *, maximum: int = MAX_CONTROL_BYTES) -> bytes:
    try:
        payload = sequential._read_owner_private_regular(
            path, maximum_bytes=maximum
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTROL_FILE_INVALID") from exc
    return payload


def _read_private_exact(path: Path, *, size: int, digest: str) -> bytes:
    if size == 0:
        descriptor = -1
        try:
            sequential._require_nonsymlink_components(path)
            before = os.lstat(path)
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            payload = os.read(descriptor, 1)
            after = os.fstat(descriptor)
            visible_after = os.lstat(path)
        except Exception as exc:
            raise R8RControllerError(
                "R8R_EXACT_CONTROL_FILE_INVALID"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        identity = lambda value: (
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_dev,
            value.st_ino,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if (
            digest
            != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            or payload != b""
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or int(opened.st_uid) != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or int(opened.st_nlink) != 1
            or int(opened.st_size) != 0
            or identity(before) != identity(opened)
            or identity(opened) != identity(after)
            or identity(after) != identity(visible_after)
        ):
            _fail("R8R_EXACT_CONTROL_FILE_INVALID")
        return b""
    try:
        payload = sequential._read_owner_private_regular(
            path,
            maximum_bytes=size,
            exact_bytes=size,
            size_mismatch_code="R8R_EXACT_CONTROL_SIZE_MISMATCH",
        )
    except Exception as exc:
        raise R8RControllerError("R8R_EXACT_CONTROL_FILE_INVALID") from exc
    if _sha256_bytes(payload) != digest:
        _fail("R8R_EXACT_CONTROL_HASH_MISMATCH")
    return payload


def _strict_json(payload: bytes) -> Mapping[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("R8R_CONTROL_JSON_INVALID")
            result[name] = value
        return result

    def reject(_value: str) -> Any:
        _fail("R8R_CONTROL_JSON_INVALID")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R8RControllerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R8RControllerError("R8R_CONTROL_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        _fail("R8R_CONTROL_JSON_INVALID")
    return value


def _load_private_json(path: Path) -> tuple[Mapping[str, Any], bytes]:
    payload = _read_private(path)
    return _strict_json(payload), payload


def _exact_typed_value_equal(observed: object, expected: object) -> bool:
    """Compare sealed JSON values without bool/int/float equivalence."""

    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _exact_typed_value_equal(observed[key], expected[key])
            for key in expected
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _exact_typed_value_equal(left, right)
            for left, right in zip(observed, expected, strict=True)
        )
    return observed == expected


def _write_private_json(path: Path, value: Mapping[str, Any]) -> str:
    try:
        return core.atomic_write_json_no_clobber(
            path, value, attempt_id=ORIGINAL_ATTEMPT_ID
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTROL_NO_CLOBBER_FAILED") from exc


def _ensure_private_directory(path: Path, *, parents: bool = False) -> None:
    try:
        sequential._ensure_private_directory(path, parents=parents)
    except Exception as exc:
        raise R8RControllerError("R8R_PRIVATE_DIRECTORY_INVALID") from exc


def _create_private_directory_no_clobber(path: Path) -> None:
    """Create one exact private control root and reject every competitor."""

    try:
        sequential._require_nonsymlink_components(path.parent)
        sequential._validate_private_directory(path.parent)
        path.mkdir(mode=0o700)
        sequential._validate_private_directory(path)
    except FileExistsError as exc:
        raise R8RControllerError("R8R_CONTROL_ROOT_COLLISION") from exc
    except Exception as exc:
        raise R8RControllerError("R8R_PRIVATE_DIRECTORY_INVALID") from exc


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    """Atomically publish one directory without replacing any target."""

    if (
        not source.is_absolute()
        or not target.is_absolute()
        or Path(os.path.abspath(source)) != source
        or Path(os.path.abspath(target)) != target
    ):
        _fail("R8U_FRESH_EXTRACTION_PUBLICATION_INVALID")
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    try:
        if sys.platform.startswith("linux"):
            rename_noreplace = library.renameat2
            rename_noreplace.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_noreplace.restype = ctypes.c_int
            arguments = (-100, source_bytes, -100, target_bytes, 1)
        elif sys.platform == "darwin":
            rename_noreplace = library.renamex_np
            rename_noreplace.argtypes = (
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_noreplace.restype = ctypes.c_int
            arguments = (source_bytes, target_bytes, 0x00000004)
        else:
            _fail("R8U_ATOMIC_NOREPLACE_UNAVAILABLE")
        ctypes.set_errno(0)
        result = rename_noreplace(*arguments)
    except AttributeError as exc:
        raise R8RControllerError("R8U_ATOMIC_NOREPLACE_UNAVAILABLE") from exc
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            _fail("R8U_FRESH_EXTRACTION_PUBLICATION_COLLISION")
        raise R8RControllerError("R8U_FRESH_EXTRACTION_PUBLICATION_FAILED") from OSError(
            error_number, os.strerror(error_number)
        )


def _current_implementation_commit() -> str:
    try:
        current = sequential._current_commit()
    except Exception as exc:
        raise R8RControllerError("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID") from exc
    if COMMIT_RE.fullmatch(current) is None:
        _fail("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    relation = sequential._git(
        "merge-base", "--is-ancestor", ORIGINAL_SCIENTIFIC_COMMIT, current
    )
    distance = sequential._git(
        "rev-list", "--count", f"{ORIGINAL_SCIENTIFIC_COMMIT}..{current}"
    )
    # `git merge-base --is-ancestor` writes no output; `_git` already requires rc 0.
    if relation or distance != "1":
        _fail("R8R_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _current_r8u_implementation_commit() -> str:
    """Require the exact five-epoch R8U-R2 implementation chain."""

    try:
        current = sequential._current_commit()
    except Exception as exc:
        raise R8RControllerError("R8U_IMPLEMENTATION_GIT_AUTHORITY_INVALID") from exc
    if COMMIT_RE.fullmatch(current) is None or current in {
        ORIGINAL_SCIENTIFIC_COMMIT,
        R8U_STARTING_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
    }:
        _fail("R8U_IMPLEMENTATION_COMMIT_REQUIRED")
    relation = sequential._git(
        "merge-base", "--is-ancestor",
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT, current,
    )
    distance = sequential._git(
        "rev-list", "--count",
        f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}..{current}",
    )
    parent_line = sequential._git(
        "rev-list", "--parents", "-n", "1", current
    )
    base_parent_line = sequential._git(
        "rev-list", "--parents", "-n", "1", R8U_BASE_IMPLEMENTATION_COMMIT
    )
    projection_parent_line = sequential._git(
        "rev-list", "--parents", "-n", "1",
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
    )
    r8r_parent_line = sequential._git(
        "rev-list", "--parents", "-n", "1", R8U_STARTING_IMPLEMENTATION_COMMIT
    )
    science_distance = sequential._git(
        "rev-list", "--count", f"{ORIGINAL_SCIENTIFIC_COMMIT}..{current}"
    )
    if (
        relation
        or distance != "1"
        or parent_line
        != f"{current} {R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}"
        or projection_parent_line
        != (
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}"
        )
        or base_parent_line
        != (
            f"{R8U_BASE_IMPLEMENTATION_COMMIT} "
            f"{R8U_STARTING_IMPLEMENTATION_COMMIT}"
        )
        or r8r_parent_line
        != (
            f"{R8U_STARTING_IMPLEMENTATION_COMMIT} "
            f"{ORIGINAL_SCIENTIFIC_COMMIT}"
        )
        or science_distance != "4"
    ):
        _fail("R8U_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _r8u_implementation_authority_epochs(
    implementation_commit: str,
) -> Mapping[str, str]:
    """Bind every new R8U-R2 artifact to the exact five-commit authority."""

    if (
        COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in {
            ORIGINAL_SCIENTIFIC_COMMIT,
            R8U_STARTING_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        }
    ):
        _fail("R8U_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    value = {
        "scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": R8U_STARTING_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": implementation_commit,
    }
    if set(value) != R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        _fail("R8U_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    return dict(sorted(value.items()))


def _current_r8u_r3_implementation_commit() -> str:
    """Require the one direct candidate-authority child of frozen R8U-R3."""

    try:
        current = sequential._current_commit()
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R3_IMPLEMENTATION_GIT_AUTHORITY_INVALID"
        ) from exc
    fixed = (
        ORIGINAL_SCIENTIFIC_COMMIT,
        R8U_STARTING_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if COMMIT_RE.fullmatch(current) is None or current in set(fixed):
        _fail("R8U_R3_IMPLEMENTATION_COMMIT_REQUIRED")
    parent_lines = tuple(
        sequential._git("rev-list", "--parents", "-n", "1", commit)
        for commit in (*fixed[1:], current)
    )
    expected_parent_lines = (
        f"{fixed[1]} {fixed[0]}",
        f"{fixed[2]} {fixed[1]}",
        f"{fixed[3]} {fixed[2]}",
        f"{fixed[4]} {fixed[3]}",
        f"{fixed[5]} {fixed[4]}",
        f"{current} {fixed[5]}",
    )
    relation = sequential._git(
        "merge-base", "--is-ancestor", fixed[5], current
    )
    distance = sequential._git(
        "rev-list", "--count", f"{fixed[5]}..{current}"
    )
    science_distance = sequential._git(
        "rev-list", "--count", f"{fixed[0]}..{current}"
    )
    if (
        relation
        or parent_lines != expected_parent_lines
        or distance != "1"
        or science_distance != "6"
    ):
        _fail("R8U_R3_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _r8u_r3_implementation_authority_epochs(
    implementation_commit: str,
) -> Mapping[str, str]:
    if (
        COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit
        in {
            ORIGINAL_SCIENTIFIC_COMMIT,
            R8U_STARTING_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        }
    ):
        _fail("R8U_R3_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    value = {
        "scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": R8U_STARTING_IMPLEMENTATION_COMMIT,
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
        "r8u_candidate_authority_repair_commit": implementation_commit,
    }
    if set(value) != R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        _fail("R8U_R3_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    return dict(sorted(value.items()))


def _current_r8u_r4_implementation_commit() -> str:
    """Require exactly one direct portability-repair child of frozen R3."""

    try:
        current = sequential._current_commit()
        parent_line = sequential._git(
            "rev-list", "--parents", "-n", "1", current
        )
        relation = sequential._git(
            "merge-base", "--is-ancestor",
            R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT, current,
        )
        distance = sequential._git(
            "rev-list", "--count",
            f"{R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT}..{current}",
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R4_IMPLEMENTATION_GIT_AUTHORITY_INVALID"
        ) from exc
    if (
        COMMIT_RE.fullmatch(current) is None
        or current == R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        or parent_line
        != f"{current} {R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT}"
        or relation
        or distance != "1"
    ):
        _fail("R8U_R4_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _r8u_r4_implementation_authority_epochs(
    implementation_commit: str,
) -> Mapping[str, str]:
    if (
        COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit
        == R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
    ):
        _fail("R8U_R4_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    value = {
        **dict(
            _r8u_r3_implementation_authority_epochs(
                R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            )
        ),
        "r8u_portability_repair_commit": implementation_commit,
    }
    if set(value) != R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        _fail("R8U_R4_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    return dict(sorted(value.items()))


def _current_r8u_r5_implementation_commit() -> str:
    """Require exactly one direct worker-context-repair child of frozen R4."""

    try:
        current = sequential._current_commit()
        parent_line = sequential._git(
            "rev-list", "--parents", "-n", "1", current
        )
        relation = sequential._git(
            "merge-base", "--is-ancestor",
            R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT, current,
        )
        distance = sequential._git(
            "rev-list", "--count",
            f"{R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT}..{current}",
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R5_IMPLEMENTATION_GIT_AUTHORITY_INVALID"
        ) from exc
    if (
        COMMIT_RE.fullmatch(current) is None
        or current == R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        or parent_line
        != f"{current} {R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT}"
        or relation
        or distance != "1"
    ):
        _fail("R8U_R5_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _r8u_r5_implementation_authority_epochs(
    implementation_commit: str,
) -> Mapping[str, str]:
    if (
        COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit == R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
    ):
        _fail("R8U_R5_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    value = {
        **dict(
            _r8u_r4_implementation_authority_epochs(
                R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            )
        ),
        "r8u_worker_context_repair_commit": implementation_commit,
    }
    if set(value) != R8U_R5_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        _fail("R8U_R5_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    return dict(sorted(value.items()))


def _r8u_r2_historical_implementation_authority_epochs() -> Mapping[str, str]:
    """Return the exact five epochs embedded by consumed job 7354951."""

    return {
        "r8r_implementation_commit": R8U_STARTING_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        "r8u_scheduler_log_repair_commit": (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
    }


def _load_fixed_original_run(
    *,
    scheduler_job_identity: str,
    runtime_validation_context: stages.RuntimeAuthorityValidationContext,
    r8u: bool = False,
    r8u_r3: bool = False,
    r8u_r4: bool = False,
    r8u_r5: bool = False,
) -> sequential.FullRun:
    if not isinstance(
        runtime_validation_context, stages.RuntimeAuthorityValidationContext
    ):
        _fail("R8R_RUNTIME_VALIDATION_CONTEXT_INVALID")
    if sum((r8u, r8u_r3, r8u_r4, r8u_r5)) > 1:
        _fail("R8R_RUNTIME_VALIDATION_CONTEXT_INVALID")
    implementation_commit = (
        _current_r8u_r5_implementation_commit()
        if r8u_r5
        else (
            _current_r8u_r4_implementation_commit()
            if r8u_r4
            else (
                _current_r8u_r3_implementation_commit()
                if r8u_r3
                else (
                    _current_r8u_implementation_commit()
                    if r8u
                    else _current_implementation_commit()
                )
            )
        )
    )
    try:
        current = minimal.discover_live_authority(
            runtime_validation_context=runtime_validation_context
        )
    except Exception as exc:
        raise R8RControllerError("R8R_RUNTIME_AUTHORITY_INVALID") from exc
    if current.governing_commit != implementation_commit:
        _fail("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    scientific = replace(
        current, governing_commit=ORIGINAL_SCIENTIFIC_COMMIT
    )
    try:
        plan, requirements, contract = sequential._build_frozen_plan(scientific)
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=requirements
        )
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID") from exc
    if plan_sha != ORIGINAL_PLAN_SHA256:
        _fail("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID")
    plan_path = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
    try:
        materialized_plan = sequential._load_full_batch_plan_payload(
            plan_path,
            expected_bytes=ORIGINAL_PLAN_BYTES,
            expected_sha256=ORIGINAL_PLAN_SHA256,
        )
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID") from exc
    if materialized_plan != plan:
        _fail("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID")
    launch_path = ATTEMPT_ROOT / "full_launch_authority.restricted.json"
    launch_payload = _read_private_exact(
        launch_path,
        size=ORIGINAL_LAUNCH_BYTES,
        digest=ORIGINAL_LAUNCH_SHA256,
    )
    launch = _strict_json(launch_payload)
    if (
        set(launch) != core.DIRECT_FULL_LAUNCH_KEYS
        or launch.get("governing_commit") != ORIGINAL_SCIENTIFIC_COMMIT
        or launch.get("batch_plan_sha256") != ORIGINAL_PLAN_SHA256
        or launch.get("array_task_range") != "1-19"
        or launch.get("array_max_concurrency") != 1
        or launch.get("maximum_scheduler_submissions") != 2
        or launch.get("model_fitting_authorized") is not False
        or launch.get("prediction_authorized") is not False
        or launch.get("confirmatory_performance_access_authorized") is not False
    ):
        _fail("R8R_ORIGINAL_LAUNCH_AUTHORITY_INVALID")
    runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha}
    )
    run = sequential.FullRun(
        authority=scientific,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=sequential.CONTRACT_PATH,
        plan_sha256=plan_sha,
        runtime_authority=runtime,
        attempt_id=ORIGINAL_ATTEMPT_ID,
        production_root=PRODUCTION_ROOT,
        attempt_root=ATTEMPT_ROOT,
        plan_path=plan_path,
        launch_authority=launch,
        launch_authority_sha256=ORIGINAL_LAUNCH_SHA256,
        scheduler_job_identity=scheduler_job_identity,
    )
    try:
        sequential._validate_private_directory(ATTEMPT_ROOT)
        sequential._validate_full_run(run)
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_RUN_AUTHORITY_INVALID") from exc
    return run


def _validate_original_controls() -> Mapping[str, str]:
    controls = {
        "original_capacity_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_capacity_receipt.restricted.json",
                size=ORIGINAL_CAPACITY_BYTES,
                digest=ORIGINAL_CAPACITY_SHA256,
            )
        ),
        "original_claim_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_submission_claim.restricted.json",
                size=ORIGINAL_CLAIM_BYTES,
                digest=ORIGINAL_CLAIM_SHA256,
            )
        ),
        "original_dynamic_capacity_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT
                / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME,
                size=ORIGINAL_DYNAMIC_CAPACITY_BYTES,
                digest=ORIGINAL_DYNAMIC_CAPACITY_SHA256,
            )
        ),
        "original_launch_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_launch_authority.restricted.json",
                size=ORIGINAL_LAUNCH_BYTES,
                digest=ORIGINAL_LAUNCH_SHA256,
            )
        ),
        "original_plan_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_batch_plan.restricted.json",
                size=ORIGINAL_PLAN_BYTES,
                digest=ORIGINAL_PLAN_SHA256,
            )
        ),
        "original_submission_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "scheduler/submission_receipt.restricted.json",
                size=ORIGINAL_SUBMISSION_BYTES,
                digest=ORIGINAL_SUBMISSION_SHA256,
            )
        ),
    }
    return dict(sorted(controls.items()))


def _validate_frozen_prefix(
    run: sequential.FullRun, *, include_batch3: bool
) -> tuple[str, ...]:
    observed: list[str] = []
    for batch_id, expected_bytes, expected_sha in PREFIX_RECEIPT_AUTHORITIES:
        paths = sequential._batch_paths(run, batch_id)
        payload = _read_private_exact(
            paths["final_receipt"], size=expected_bytes, digest=expected_sha
        )
        receipt = _strict_json(payload)
        try:
            finalizer._validate_current_receipt_v3(receipt)
            sequential._validate_batch_finalization(
                run=run, batch_id=batch_id
            )
        except Exception as exc:
            raise R8RControllerError("R8R_FROZEN_PREFIX_INVALID") from exc
        observed.append(expected_sha)
    for current_batch in ("c3_batch_001", "c3_batch_002"):
        try:
            prior_gate.validate_prior_batch(
                **sequential._prior_batch_kwargs(run, current_batch)
            )
        except Exception as exc:
            raise R8RControllerError("R8R_FROZEN_PREFIX_INVALID") from exc
    if include_batch3:
        try:
            receipt = sequential._validate_batch_finalization(
                run=run, batch_id=FIXED_BATCH3_ID
            )
        except Exception as exc:
            raise R8RControllerError("R8R_RECOVERED_BATCH3_INVALID") from exc
        observed.append(core.sha256_file(
            sequential._batch_paths(run, FIXED_BATCH3_ID)["final_receipt"]
        ))
        if (
            receipt.get("n_selected_studies") != 250
            or receipt.get("n_expected_objects") != 18_653
            or receipt.get("expected_source_bytes") != 68_725_707_170
            or receipt.get("n_successfully_extracted_cines") != 10_256
            or receipt.get("n_object_technical_dispositions") != 1
            or receipt.get("n_blocking_failures") != 0
            or receipt.get("n_clip_embeddings") != 10_256
            or receipt.get("n_pooled_studies") != 249
            or receipt.get("n_no_cine_studies") != 1
            or receipt.get("n_new_no_cine_studies") != 0
        ):
            _fail("R8R_RECOVERED_BATCH3_SCIENTIFIC_AUTHORITY_INVALID")
    return tuple(observed)


def _metadata_row(path: Path, *, root: Path, kind: str) -> list[Any]:
    try:
        before = os.lstat(path)
        relative = path.relative_to(root).as_posix()
        after = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise R8RControllerError("R8R_RETAINED_TOPOLOGY_INVALID") from exc
    if stat.S_ISLNK(before.st_mode):
        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
    expected_type = stat.S_ISDIR if kind == "D" else stat.S_ISREG
    local_identity = lambda value: (
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if (
        not expected_type(before.st_mode)
        or before.st_uid != os.geteuid()
        or local_identity(before) != local_identity(after)
    ):
        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
    # Device and inode are checked above for same-call replacement, but are
    # intentionally excluded from the sealed projection because SCC execution
    # nodes may expose the same owner-private filesystem through different
    # device/inode namespaces.
    return [
        relative,
        kind,
        stat.S_IMODE(before.st_mode),
        before.st_uid,
        before.st_gid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ]


def _read_control_nofollow(path: Path) -> bytes:
    try:
        info = os.lstat(path)
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise R8RControllerError("R8R_RETAINED_CONTROL_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            or info.st_size < 1
            or info.st_size > MAX_CONTROL_BYTES
        ):
            _fail("R8R_RETAINED_CONTROL_INVALID")
        remaining = info.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 1_048_576))
            if not block:
                _fail("R8R_RETAINED_CONTROL_INVALID")
            chunks.append(block)
            remaining -= len(block)
        if os.fstat(descriptor).st_size != info.st_size:
            _fail("R8R_RETAINED_CONTROL_INVALID")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _batch3_retained_authority() -> Mapping[str, Any]:
    """Freeze Batch 3 without opening a DICOM or retained NPZ body."""

    raw_batch = ATTEMPT_ROOT / "raw" / FIXED_BATCH3_ID
    raw_objects = raw_batch / "objects"
    extraction = (
        ATTEMPT_ROOT
        / "extracted_cache"
        / FIXED_BATCH3_ID
        / "dicom_extraction"
    )
    clips = extraction / "clips"
    echoprime = ATTEMPT_ROOT / "batches" / FIXED_BATCH3_ID / "echoprime"
    fixed_roots = (raw_batch, raw_objects, extraction, clips, echoprime)
    for root in fixed_roots:
        _metadata_row(root, root=ATTEMPT_ROOT, kind="D")

    metadata_rows: list[list[Any]] = []
    control_rows: list[list[Any]] = []
    raw_count = 0
    raw_bytes = 0
    clip_count = 0
    clip_bytes = 0
    embedding_sizes: dict[str, int] = {}
    for root in (raw_batch, extraction, echoprime):
        for directory, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_names.sort()
            file_names.sort()
            current = Path(directory)
            metadata_rows.append(
                _metadata_row(current, root=ATTEMPT_ROOT, kind="D")
            )
            for name in directory_names:
                child = current / name
                if child.is_symlink():
                    _fail("R8R_RETAINED_TOPOLOGY_INVALID")
            for name in file_names:
                path = current / name
                row = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
                metadata_rows.append(row)
                if path.is_relative_to(raw_objects):
                    if path.parent != raw_objects or path.suffix != ".dcm":
                        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                    raw_count += 1
                    raw_bytes += int(row[6])
                    continue
                if path.suffix.casefold() == ".dcm":
                    _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                if path.suffix.casefold() == ".npz":
                    if path.is_relative_to(clips) and path.suffix == ".npz":
                        clip_count += 1
                        clip_bytes += int(row[6])
                    elif path in {
                        echoprime / "clip_embeddings.restricted.npz",
                        echoprime / "study_embeddings.restricted.npz",
                    }:
                        embedding_sizes[path.name] = int(row[6])
                    else:
                        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                    continue
                payload = _read_control_nofollow(path)
                control_rows.append(
                    [
                        path.relative_to(ATTEMPT_ROOT).as_posix(),
                        len(payload),
                        _sha256_bytes(payload),
                    ]
                )

    if (
        raw_count != 18_653
        or raw_bytes != 68_725_707_170
        or clip_count != 10_256
        or clip_bytes != 18_634_511_516
        or embedding_sizes
        != {
            "clip_embeddings.restricted.npz": 19_421_157,
            "study_embeddings.restricted.npz": 472_069,
        }
    ):
        _fail("R8R_BATCH3_RETAINED_AGGREGATE_INVALID")
    metadata_payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n"
        for row in sorted(metadata_rows)
    )
    control_payload = _canonical_bytes({"controls": sorted(control_rows)})
    return {
        "raw_dicom_files": raw_count,
        "raw_dicom_bytes": raw_bytes,
        "extracted_clip_files": clip_count,
        "extracted_clip_bytes": clip_bytes,
        "clip_embedding_artifact_bytes": embedding_sizes[
            "clip_embeddings.restricted.npz"
        ],
        "study_embedding_artifact_bytes": embedding_sizes[
            "study_embeddings.restricted.npz"
        ],
        "metadata_entry_count": len(metadata_rows),
        "metadata_projection_sha256": _sha256_bytes(metadata_payload),
        "nonbody_control_count": len(control_rows),
        "nonbody_control_authority_sha256": _sha256_bytes(control_payload),
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
    }


def _require_batch3_recovery_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(
        run,
        FIXED_BATCH3_ID,
    )
    expected_absent = (
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        paths["preservation"] / "batch_preservation_manifest.restricted.tsv",
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{FIXED_BATCH3_ID}.authorization.json",
        paths["preservation"] / "cache_retirement_intent.restricted.json",
        paths["preservation"] / "cache_atomically_staged.restricted.json",
        paths["preservation"] / "cache_retirement_finalized.restricted.json",
        paths["eligibility_ledger"],
        paths["final_ledger"],
        paths["final_receipt"],
        RECOVERY_TERMINAL_PATH,
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8R_BATCH3_RECOVERY_OUTPUT_COLLISION")


def _script_authority() -> Mapping[str, str]:
    paths = {
        "controller_sha256": Path(__file__).resolve(),
        "full_sequential_sha256": SCRIPT_ROOT / "lvef_c3_full_sequential.py",
        "production_stages_sha256": SCRIPT_ROOT / "lvef_c3_production_stages.py",
        "preservation_sha256": SCRIPT_ROOT / "preserve_lvef_c3_production_batch.py",
        "retirement_sha256": SCRIPT_ROOT / "retire_lvef_c3_extracted_cache_v2.py",
        "finalizer_sha256": SCRIPT_ROOT / "finalize_lvef_c3_production.py",
        "runner_sha256": RUNNER_PATH,
    }
    result: dict[str, str] = {}
    for key, path in paths.items():
        try:
            result[key] = core.sha256_file(path)
        except Exception as exc:
            raise R8RControllerError("R8R_SCRIPT_AUTHORITY_INVALID") from exc
    return dict(sorted(result.items()))


def _r8u_historical_r8r_chain_authority() -> Mapping[str, str]:
    """Validate the completed R8R chain without reinterpreting it."""

    specifications = {
        "recovery_authority_sha256": (
            ATTEMPT_ROOT / "r8r_batch3_recovery/recovery_authority.restricted.json",
            2_901,
        ),
        "recovery_terminal_receipt_sha256": (
            ATTEMPT_ROOT / "r8r_batch3_recovery/recovery_terminal.aggregate_safe.json",
            1_929,
        ),
        "continuation_capacity_receipt_sha256": (
            ATTEMPT_ROOT / "r8r_continuation/continuation_capacity.restricted.json",
            4_395,
        ),
        "continuation_claim_sha256": (
            ATTEMPT_ROOT / "r8r_continuation/continuation_claim.restricted.json",
            2_523,
        ),
        "continuation_submission_receipt_sha256": (
            ATTEMPT_ROOT / "r8r_continuation/scheduler/submission_receipt.restricted.json",
            1_899,
        ),
    }
    observed: dict[str, str] = {}
    for field, (path, expected_bytes) in specifications.items():
        expected_sha = R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[field]
        payload = _read_private_exact(path, size=expected_bytes, digest=expected_sha)
        value = _strict_json(payload)
        if (
            value.get("original_scientific_commit") != ORIGINAL_SCIENTIFIC_COMMIT
            or value.get("implementation_commit") != R8U_STARTING_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != ORIGINAL_ATTEMPT_ID
            or value.get("batch_plan_sha256") != ORIGINAL_PLAN_SHA256
        ):
            _fail("R8U_HISTORICAL_R8R_CHAIN_INVALID")
        observed[field] = _sha256_bytes(payload)
    if observed != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        _fail("R8U_HISTORICAL_R8R_CHAIN_INVALID")
    return dict(sorted(observed.items()))


def _r8u_failed_partial_observation() -> Mapping[str, Any]:
    """Project the failed cache without opening a retained NPZ body."""

    root = ATTEMPT_ROOT / "extracted_cache" / R8U_FIXED_BATCH_ID / "dicom_extraction.partial"
    try:
        root_info = os.lstat(root)
    except OSError as exc:
        raise R8RControllerError("R8U_FAILED_PARTIAL_EVIDENCE_INVALID") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) not in {0o700, 0o2700}
    ):
        _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
    rows: list[list[Any]] = []
    file_count = directory_count = total_bytes = 0
    for current, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        current_path = Path(current)
        directory_row = _metadata_row(current_path, root=ATTEMPT_ROOT, kind="D")
        if directory_row[2] not in {0o700, 0o2700}:
            _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
        rows.append(directory_row)
        directory_count += 1
        for name in names:
            child = current_path / name
            try:
                info = os.lstat(child)
            except OSError as exc:
                raise R8RControllerError("R8U_FAILED_PARTIAL_EVIDENCE_INVALID") from exc
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
            ):
                _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
        for name in files:
            path = current_path / name
            if path.suffix != ".npz" or not path.is_relative_to(root / "clips"):
                _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
            row = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
            if row[2] != 0o600 or row[5] != 1:
                _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
            rows.append(row)
            file_count += 1
            total_bytes += int(row[6])
    payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        for row in sorted(rows)
    )
    projection = {
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "symlink_count": 0,
        "nonregular_count": 0,
        "metadata_projection_sha256": _sha256_bytes(payload),
        "npz_body_reads": 0,
    }
    expected = {
        "file_count": R8U_FAILED_PARTIAL_FILES,
        "directory_count": R8U_FAILED_PARTIAL_DIRECTORIES,
        "total_bytes": R8U_FAILED_PARTIAL_BYTES,
        "symlink_count": 0,
        "nonregular_count": 0,
        "metadata_projection_sha256": R8U_FAILED_PARTIAL_METADATA_SHA256,
        "npz_body_reads": 0,
    }
    if projection != expected:
        _fail("R8U_FAILED_PARTIAL_EVIDENCE_INVALID")
    return projection


def _r8u_failed_partial_seal(*, implementation_commit: str) -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "status": "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "failed_array_job_id": R8U_FAILED_ARRAY_JOB_ID,
        "failure_class": "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS",
        "observation": dict(_r8u_failed_partial_observation()),
        "partial_outputs_adopted": False,
        "partial_outputs_modified": False,
        "partial_outputs_deleted": False,
        "partial_outputs_renamed": False,
        "npz_body_reads": 0,
    }


def validate_r8u_failed_partial_seal() -> Mapping[str, Any]:
    value, payload = _load_private_json(R8U_FAILED_PARTIAL_SEAL_PATH)
    expected = _r8u_failed_partial_seal(
        implementation_commit=_current_r8u_implementation_commit()
    )
    if (
        not _exact_typed_value_equal(value, expected)
        or _sha256_bytes(payload) != core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH)
    ):
        _fail("R8U_FAILED_PARTIAL_SEAL_INVALID")
    return value


def _r8u_validate_frozen_prefix(
    run: sequential.FullRun, *, include_batch16: bool
) -> tuple[str, ...]:
    """Validate exact original receipts and all required prefix semantics."""

    total_keys = (
        "n_selected_studies", "n_selected_subjects", "n_expected_objects",
        "expected_source_bytes", "n_download_verified", "n_dicom_readable",
        "n_dicom_unreadable", "n_multiframe_cines", "n_single_frame_objects",
        "n_successfully_extracted_cines", "n_clip_embeddings",
        "n_object_technical_dispositions", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
    )
    totals = {key: 0 for key in total_keys}
    preprocessing = [0, 0, 0, 0]
    retired_bytes = 0
    hashes: list[str] = []
    for index, (batch_id, expected_bytes, expected_sha) in enumerate(
        R8U_PREFIX_RECEIPT_AUTHORITIES
    ):
        paths = sequential._batch_paths(run, batch_id)
        payload = _read_private_exact(
            paths["final_receipt"], size=expected_bytes, digest=expected_sha
        )
        receipt = _strict_json(payload)
        try:
            finalizer._validate_current_receipt_v3(receipt)
            sequential._validate_batch_finalization(run=run, batch_id=batch_id)
            ledger = core.load_strict_json(paths["final_ledger"])
            core.validate_resume_authority(
                ledger,
                expected_authority=run.runtime_authority,
                attempt_id=run.attempt_id,
                expected_object_keys={
                    batch_id: {
                        str(row["source_object_key"])
                        for row in run.plan["batches"][index]["objects"]
                    }
                },
            )
            if (
                ledger.get("status") != "COMPLETE"
                or ledger["batches"][batch_id].get("state") != "FINALIZED"
            ):
                _fail("R8U_FROZEN_PREFIX_INVALID")
            preserved = preservation.load_json(
                paths["preservation"] / "batch_preservation_receipt.restricted.json",
                "R8U_PREFIX_PRESERVATION_RECEIPT",
            )
            if preserved.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
                _fail("R8U_FROZEN_PREFIX_INVALID")
            summary = stages.load_json_object(
                paths["extraction"] / "dicom_extraction.summary.json",
                "R8U_PREFIX_EXTRACTION_SUMMARY",
            )
            for ordinal, key in enumerate((
                "n_ordinary_preprocessing_path",
                "n_spatial_fallback_preprocessing_path",
                "n_temporal_fallback_preprocessing_path",
                "n_spatial_temporal_fallback_preprocessing_path",
            )):
                preprocessing[ordinal] += int(summary[key])
            manifest_path = (
                paths["preservation"] / "batch_preservation_manifest.restricted.tsv"
            )
            with manifest_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    if row.get("role") == "extracted_npz_cache_owner_retirable":
                        retired_bytes += int(row["size_bytes"])
            prior_gate.validate_prior_batch(
                **sequential._prior_batch_kwargs(run, f"c3_batch_{index + 1:03d}")
            )
        except R8RControllerError:
            raise
        except Exception as exc:
            raise R8RControllerError("R8U_FROZEN_PREFIX_INVALID") from exc
        for key in total_keys:
            totals[key] += int(receipt[key])
        hashes.append(expected_sha)
    expected_totals = {
        "n_selected_studies": 3_750,
        "n_selected_subjects": 3_750,
        "n_expected_objects": 277_700,
        "expected_source_bytes": 1_004_679_096_576,
        "n_download_verified": 277_700,
        "n_dicom_readable": 277_700,
        "n_dicom_unreadable": 0,
        "n_multiframe_cines": 152_581,
        "n_single_frame_objects": 125_119,
        "n_successfully_extracted_cines": 152_577,
        "n_clip_embeddings": 152_577,
        "n_object_technical_dispositions": 4,
        "n_pooled_studies": 3_745,
        "n_no_cine_studies": 5,
        "n_new_no_cine_studies": 0,
    }
    if (
        totals != expected_totals
        or tuple(preprocessing) != (152_575, 2, 0, 0)
        or retired_bytes != 277_442_949_475
        or totals["n_pooled_studies"] + totals["n_no_cine_studies"] != 3_750
    ):
        _fail("R8U_FROZEN_PREFIX_AGGREGATE_INVALID")
    if include_batch16:
        receipt = sequential._validate_batch_finalization(
            run=run, batch_id=R8U_FIXED_BATCH_ID
        )
        if (
            receipt.get("n_selected_studies") != 250
            or receipt.get("n_expected_objects") != R8U_BATCH16_RAW_FILES
            or receipt.get("expected_source_bytes") != R8U_BATCH16_RAW_BYTES
            or receipt.get("n_new_no_cine_studies") != 0
            or receipt.get("n_blocking_failures") != 0
            or receipt.get("object_substitution_count") != 0
            or receipt.get("unaccounted_multiframe_objects") != 0
        ):
            _fail("R8U_BATCH16_FINALIZATION_SCIENTIFIC_AUTHORITY_INVALID")
        hashes.append(core.sha256_file(
            sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["final_receipt"]
        ))
        prior_gate.validate_prior_batch(
            **sequential._prior_batch_kwargs(run, "c3_batch_016")
        )
    return tuple(hashes)


def _r8u_batch16_raw_control_authority(
    run: sequential.FullRun,
) -> Mapping[str, Any]:
    """Validate retained download controls and portable file metadata only."""

    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    raw_batch = paths["raw_batch"]
    raw_objects = raw_batch / "objects"
    raw_receipts = raw_batch / "receipts"
    guarded_directories = (
        raw_batch,
        raw_objects,
        raw_receipts,
        paths["batch_root"],
    )
    directory_identities: dict[Path, tuple[int, ...]] = {}
    try:
        for directory in guarded_directories:
            sequential._require_nonsymlink_components(directory)
            sequential._validate_private_directory(directory)
            info = os.lstat(directory)
            directory_identities[directory] = (
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
    except Exception as exc:
        raise R8RControllerError("R8U_BATCH16_RAW_TOPOLOGY_INVALID") from exc
    ledger_payload = _read_private_exact(
        paths["download_ledger"],
        size=R8U_BATCH16_DOWNLOAD_LEDGER_BYTES,
        digest=R8U_BATCH16_DOWNLOAD_LEDGER_SHA256,
    )
    ledger = _strict_json(ledger_payload)
    verified_payload = _read_private_exact(
        raw_batch / "verified_download_manifest.restricted.csv",
        size=R8U_BATCH16_VERIFIED_MANIFEST_BYTES,
        digest=R8U_BATCH16_VERIFIED_MANIFEST_SHA256,
    )
    _read_private_exact(
        raw_batch / "selected_batch.restricted.csv",
        size=R8U_BATCH16_SELECTED_MANIFEST_BYTES,
        digest=R8U_BATCH16_SELECTED_MANIFEST_SHA256,
    )
    expected_objects = {
        str(row["source_object_key"]): row for row in planned["objects"]
    }
    try:
        core.validate_resume_authority(
            ledger,
            expected_authority=run.runtime_authority,
            attempt_id=run.attempt_id,
            expected_object_keys={R8U_FIXED_BATCH_ID: set(expected_objects)},
        )
        batch_ledger = ledger["batches"][R8U_FIXED_BATCH_ID]
        if (
            ledger.get("status") != "ACTIVE"
            or batch_ledger.get("state") != "DOWNLOAD_VERIFIED"
            or batch_ledger.get("download_manifest_sha256")
            != R8U_BATCH16_VERIFIED_MANIFEST_SHA256
            or batch_ledger.get("selected_batch_manifest_sha256")
            != R8U_BATCH16_SELECTED_MANIFEST_SHA256
            or set(batch_ledger.get("download_verification_receipts", {}))
            != set(expected_objects)
        ):
            _fail("R8U_BATCH16_DOWNLOAD_LEDGER_INVALID")
        stages.validate_stage_predecessor(
            input_ledger=paths["download_ledger"],
            batch_id=R8U_FIXED_BATCH_ID,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=set(expected_objects),
            bound_manifest=raw_batch / "verified_download_manifest.restricted.csv",
        )
        stages.validate_download_manifest_plan_membership(
            raw_batch / "verified_download_manifest.restricted.csv", planned
        )
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8U_BATCH16_DOWNLOAD_AUTHORITY_INVALID") from exc
    try:
        decoded = verified_payload.decode("utf-8", "strict")
        manifest_rows = list(csv.DictReader(decoded.splitlines()))
    except (UnicodeError, csv.Error) as exc:
        raise R8RControllerError("R8U_BATCH16_DOWNLOAD_MANIFEST_INVALID") from exc
    if (
        len(manifest_rows) != R8U_BATCH16_RAW_FILES
        or set(manifest_rows[0]) != {
            "subject_id", "study_id", "source_relative_path", "download_ok",
            "observed_sha256", "physical_source_key",
        }
    ):
        _fail("R8U_BATCH16_DOWNLOAD_MANIFEST_INVALID")
    manifest_by_key = {
        str(row["physical_source_key"]): row for row in manifest_rows
    }
    if len(manifest_by_key) != len(manifest_rows) or set(manifest_by_key) != set(expected_objects):
        _fail("R8U_BATCH16_DOWNLOAD_MANIFEST_INVALID")
    verification_keys = {
        "schema_version", "status", "source_object_key", "size_bytes",
        "generation", "md5_base64", "crc32c_base64", "local_sha256",
        "file_device", "file_inode", "file_mtime_ns", "digest_backend",
        "digest_chunk_size_bytes",
    }
    metadata_rows: list[list[Any]] = []
    control_rows: list[list[Any]] = []
    total_bytes = 0
    seen: set[str] = set()
    try:
        entries = sorted(os.scandir(raw_objects), key=lambda item: item.name)
    except OSError as exc:
        raise R8RControllerError("R8U_BATCH16_RAW_TOPOLOGY_INVALID") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            _fail("R8U_BATCH16_RAW_TOPOLOGY_INVALID")
        if not entry.name.endswith(".dcm"):
            _fail("R8U_BATCH16_RAW_TOPOLOGY_INVALID")
        key = entry.name[:-4]
        planned_row = expected_objects.get(key)
        if planned_row is None or key in seen:
            _fail("R8U_BATCH16_RAW_TOPOLOGY_INVALID")
        seen.add(key)
        path = Path(entry.path)
        metadata = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
        if metadata[2] != 0o600 or metadata[5] != 1:
            _fail("R8U_BATCH16_RAW_TOPOLOGY_INVALID")
        if int(metadata[6]) != int(planned_row["size_bytes"]):
            _fail("R8U_BATCH16_RAW_SIZE_MISMATCH")
        receipt_path = raw_receipts / f"{key}.verification.json"
        receipt_payload = _read_control_nofollow(receipt_path)
        receipt = _strict_json(receipt_payload)
        manifest = manifest_by_key[key]
        expected_receipt_sha = ledger["batches"][R8U_FIXED_BATCH_ID][
            "download_verification_receipts"
        ][key]
        if (
            set(receipt) != verification_keys
            or receipt.get("schema_version") != 2
            or receipt.get("status") != "PASS_DOWNLOAD_VERIFICATION"
            or receipt.get("source_object_key") != key
            or receipt.get("size_bytes") != planned_row["size_bytes"]
            or str(receipt.get("generation")) != str(planned_row["generation"])
            or receipt.get("md5_base64") != planned_row["md5_base64"]
            or receipt.get("crc32c_base64") != planned_row["crc32c_base64"]
            or SHA_RE.fullmatch(str(receipt.get("local_sha256"))) is None
            or _sha256_bytes(receipt_payload) != expected_receipt_sha
            or manifest.get("download_ok") != "true"
            or manifest.get("observed_sha256") != receipt.get("local_sha256")
            or manifest.get("source_relative_path") != planned_row["source_relative_path"]
        ):
            _fail("R8U_BATCH16_DOWNLOAD_RECEIPT_INVALID")
        metadata_rows.append(metadata)
        control_rows.append([
            key,
            receipt["size_bytes"],
            receipt["generation"],
            receipt["md5_base64"],
            receipt["crc32c_base64"],
            receipt["local_sha256"],
            expected_receipt_sha,
        ])
        total_bytes += int(metadata[6])
    if seen != set(expected_objects) or len(seen) != R8U_BATCH16_RAW_FILES or total_bytes != R8U_BATCH16_RAW_BYTES:
        _fail("R8U_BATCH16_RAW_AGGREGATE_INVALID")
    try:
        for directory, identity in directory_identities.items():
            info = os.lstat(directory)
            if identity != (
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ):
                _fail("R8U_BATCH16_RAW_TOPOLOGY_CHANGED_DURING_SCAN")
            sequential._validate_private_directory(directory)
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_BATCH16_RAW_TOPOLOGY_CHANGED_DURING_SCAN"
        ) from exc
    metadata_payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        for row in sorted(metadata_rows)
    )
    return {
        "raw_dicom_files": len(seen),
        "raw_dicom_bytes": total_bytes,
        "raw_metadata_projection_sha256": _sha256_bytes(metadata_payload),
        "download_control_projection_sha256": core.canonical_json_sha256(
            sorted(control_rows)
        ),
        "download_ledger_sha256": R8U_BATCH16_DOWNLOAD_LEDGER_SHA256,
        "verified_download_manifest_sha256": R8U_BATCH16_VERIFIED_MANIFEST_SHA256,
        "selected_batch_manifest_sha256": R8U_BATCH16_SELECTED_MANIFEST_SHA256,
        "historical_st_dev_required": False,
        "raw_dicom_body_reads": 0,
        "cloud_requests": 0,
    }


def _r8u_validate_batch16_raw_bodies(
    run: sequential.FullRun,
    *,
    digest_provider_factory: Callable[[minimal.LiveAuthority], Any] | None = None,
) -> Mapping[str, Any]:
    """One stable body pass; historical device/inode fields are nonportable."""

    control = _r8u_batch16_raw_control_authority(run)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    raw_batch = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["raw_batch"]
    with sequential._digest_provider(digest_provider_factory, run.authority) as digest:
        for row in planned["objects"]:
            key = str(row["source_object_key"])
            path = raw_batch / "objects" / f"{key}.dcm"
            receipt = core.load_strict_json(
                raw_batch / "receipts" / f"{key}.verification.json"
            )
            try:
                before = os.lstat(path)
                observed = digest(path, key)
                after = os.lstat(path)
            except Exception as exc:
                raise R8RControllerError("R8U_BATCH16_RAW_DIGEST_FAILED") from exc
            current_identity = lambda value: (
                value.st_mode, value.st_uid, value.st_gid, value.st_dev,
                value.st_ino, value.st_nlink, value.st_size, value.st_mtime_ns,
            )
            if (
                current_identity(before) != current_identity(after)
                or not stat.S_ISREG(after.st_mode)
                or stat.S_ISLNK(after.st_mode)
                or after.st_uid != os.geteuid()
                or stat.S_IMODE(after.st_mode) != 0o600
                or after.st_nlink != 1
                or observed.get("size_bytes") != row["size_bytes"]
                or observed.get("md5_base64") != row["md5_base64"]
                or observed.get("crc32c_base64") != row["crc32c_base64"]
                or observed.get("sha256") != receipt.get("local_sha256")
                or observed.get("file_device") != after.st_dev
                or observed.get("file_inode") != after.st_ino
                or observed.get("file_mtime_ns") != after.st_mtime_ns
            ):
                _fail("R8U_BATCH16_RAW_CONTENT_AUTHORITY_MISMATCH")
    return {
        **dict(control),
        "raw_dicom_body_reads": R8U_BATCH16_RAW_FILES,
        "status": "PASS_BATCH16_RETAINED_RAW_CONTENT_AUTHORITY",
    }


def _validate_no_active_jobs(
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8R_ACTIVE_JOB_CHECK_FAILED")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8R_ACTIVE_JOB_CHECK_FAILED") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local(element) for element in list(root)]
    if (
        local(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(
            name not in {"queue_info", "job_info"}
            for name in direct_children
        )
    ):
        _fail("R8R_ACTIVE_JOB_CHECK_FAILED")
    names = {
        element.text or ""
        for element in root.iter()
        if local(element) == "JB_name"
    }
    job_ids = {
        element.text or ""
        for element in root.iter()
        if local(element) == "JB_job_number"
    }
    if any(
        re.fullmatch(
            r"lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
            r"r8u_(?:rec|seq|fin)|r8u_r3_(?:res|seq|fin))_[0-9a-f]{8}",
            name,
        )
        or re.fullmatch(
            r"c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}",
            name,
        )
        for name in names
    ) or not (
        ORIGINAL_SCHEDULER_JOB_IDS
        | {
            R8U_PRIOR_RECOVERY_JOB_ID,
            R8U_FAILED_ARRAY_JOB_ID,
            R8U_FAILED_FINALIZER_JOB_ID,
            R8U_FAILED_RECOVERY_JOB_ID,
            R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        }
    ).isdisjoint(job_ids):
        _fail("R8R_ACTIVE_MATCHING_JOB_EXISTS")


def _validate_r8u_no_active_processes(
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    completed = runner(
        ["/bin/ps", "-axo", "user=,command="],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if completed.returncode != 0 or completed.stderr or len(payload) > 16 * 1024 * 1024:
        _fail("R8U_ACTIVE_PROCESS_CHECK_FAILED")
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R8RControllerError("R8U_ACTIVE_PROCESS_CHECK_FAILED") from exc
    markers = (
        "--run-array-task",
        "--run-cohort-finalizer",
        "--recover-batch3-preservation",
        "--run-continuation-array-task",
        "--run-continuation-finalizer",
        "--run-batch16-recovery",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
        "--run-r8u-r3-batch16-publication-resume",
        "run_production_dicom_extraction",
        "run_production_echoprime",
        "preserve_lvef_c3_production_batch",
        "retire_lvef_c3_extracted_cache",
        "finalize_lvef_c3_production",
        "lvef_c3_r8u_rec_",
        "lvef_c3_r8u_seq_",
        "lvef_c3_r8u_fin_",
        "lvef_c3_r8u_r3_",
    )
    active: list[str] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or parts[0] != environment["USER"]:
            continue
        command = parts[1]
        if any(marker in command for marker in markers):
            active.append(command)
    if active:
        _fail("R8U_ACTIVE_MATCHING_PROCESS_EXISTS")


@dataclass(frozen=True)
class _R8USchedulerLogBinding:
    role: str
    scheduler_root: Path
    job_name: str
    job_id: str
    task_id: str
    terminal_state: str


@dataclass(frozen=True)
class _R8UAttemptContentScan:
    authority: Mapping[str, Any]
    regular_file_path_set_sha256: str
    duplicate_relative_path_count: int
    scheduler_evidence: tuple[Mapping[str, Any], ...]
    scheduler_evidence_projection_sha256: str


def _r8u_is_at_or_beneath(
    relative: PurePosixPath, roots: frozenset[PurePosixPath]
) -> bool:
    return any(relative == root or root in relative.parents for root in roots)


def _r8u_projection_row_bytes(row: Sequence[object]) -> bytes:
    return (
        json.dumps(
            list(row), separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8", "surrogateescape")


def _r8u_stable_lstat(path: Path) -> os.stat_result:
    try:
        before = os.lstat(path)
        after = os.lstat(path)
    except OSError as exc:
        raise R8RControllerError(
            "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID"
        ) from exc
    identity = lambda value: (
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
    return before


def _r8u_private_directory_metadata_valid(
    *, mode: int, uid: int, device: int, approved_device: int
) -> bool:
    return (
        uid == os.geteuid()
        and mode & 0o700 == 0o700
        and not mode & 0o077
        and mode & 0o7000 in {0, stat.S_ISGID}
        and device == approved_device
    )


def _r8u_scheduler_log_basename(binding: _R8USchedulerLogBinding) -> str:
    fixed_roots = {
        "FAILED_R8U_BATCH16_RECOVERY": (
            ATTEMPT_ROOT / "r8u_batch16_recovery" / "scheduler",
            "rec",
            frozenset({"NONE"}),
        ),
        "FRESH_R8U_R2_BATCH16_RECOVERY": (
            ATTEMPT_ROOT / "r8u_r2_batch16_recovery" / "scheduler",
            "rec",
            frozenset({"NONE"}),
        ),
        "R8U_R2_CONTINUATION_ARRAY_TASK": (
            ATTEMPT_ROOT / "r8u_r2_continuation_17_19" / "scheduler",
            "seq",
            frozenset({"17", "18", "19"}),
        ),
        "R8U_R2_COHORT_FINALIZER": (
            ATTEMPT_ROOT / "r8u_r2_continuation_17_19" / "scheduler",
            "fin",
            frozenset({"NONE"}),
        ),
    }
    role_authority = fixed_roots.get(binding.role)
    if role_authority is None:
        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
    expected_root, job_kind, task_ids = role_authority
    if (
        binding.scheduler_root != expected_root
        or JOB_RE.fullmatch(binding.job_id) is None
        or re.fullmatch(
            rf"lvef_c3_r8u_{job_kind}_[0-9a-f]{{8}}",
            binding.job_name,
        )
        is None
        or binding.task_id not in task_ids
        or SAFE_CODE_RE.fullmatch(binding.terminal_state) is None
    ):
        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
    if binding.role == "FAILED_R8U_BATCH16_RECOVERY" and (
        binding.job_name != R8U_FAILED_RECOVERY_JOB_NAME
        or binding.job_id != R8U_FAILED_RECOVERY_JOB_ID
        or binding.terminal_state
        != "TERMINAL_FAILED_APPLICATION_EXIT_78"
    ):
        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
    suffix = "" if binding.task_id == "NONE" else f".{binding.task_id}"
    return f"{binding.job_name}.o{binding.job_id}{suffix}"


def _r8u_scheduler_log_relative_path(
    binding: _R8USchedulerLogBinding,
) -> PurePosixPath:
    try:
        scheduler_relative = binding.scheduler_root.relative_to(ATTEMPT_ROOT)
    except ValueError as exc:
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID"
        ) from exc
    allowed_roots = {
        ATTEMPT_ROOT / "r8u_batch16_recovery" / "scheduler",
        ATTEMPT_ROOT / "r8u_r2_batch16_recovery" / "scheduler",
        ATTEMPT_ROOT / "r8u_r2_continuation_17_19" / "scheduler",
    }
    if binding.scheduler_root not in allowed_roots:
        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
    return PurePosixPath(
        (scheduler_relative / _r8u_scheduler_log_basename(binding)).as_posix()
    )


def _r8u_log_shaped_basename(value: str) -> bool:
    return (
        re.fullmatch(
            r"[^/]+\.o[1-9][0-9]{0,19}(?:\.[1-9][0-9]{0,5})?",
            value,
        )
        is not None
    )


def _r8u_scheduler_log_evidence(
    *,
    path: Path,
    binding: _R8USchedulerLogBinding,
    approved_device: int,
) -> Mapping[str, Any]:
    """Read one receipt-bound Grid Engine merged log without following links."""

    expected_relative = _r8u_scheduler_log_relative_path(binding)
    expected_path = ATTEMPT_ROOT / Path(expected_relative.as_posix())
    if (
        not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        or path != expected_path
        or path.parent != binding.scheduler_root
        or path.name != _r8u_scheduler_log_basename(binding)
    ):
        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")

    current = ATTEMPT_ROOT
    try:
        sequential._require_nonsymlink_components(path)
        nested_mounts = _r8u_nested_mount_paths(ATTEMPT_ROOT)
        root_info = _r8u_stable_lstat(current)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(root_info.st_mode)
            or int(root_info.st_dev) != approved_device
        ):
            _fail("R8U_SCHEDULER_LOG_TOPOLOGY_INVALID")
        for part in path.parent.relative_to(ATTEMPT_ROOT).parts:
            current = current / part
            info = _r8u_stable_lstat(current)
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or not _r8u_private_directory_metadata_valid(
                    mode=stat.S_IMODE(info.st_mode),
                    uid=int(info.st_uid),
                    device=int(info.st_dev),
                    approved_device=approved_device,
                )
                or os.path.ismount(current)
                or current in nested_mounts
            ):
                _fail("R8U_SCHEDULER_LOG_TOPOLOGY_INVALID")
    except R8RControllerError as exc:
        if exc.code == "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID":
            raise
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID"
        ) from exc
    except Exception as exc:
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID"
        ) from exc

    flags = os.O_RDONLY
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        visible_before = _r8u_stable_lstat(path)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(visible_before.st_mode)
            or int(opened.st_nlink) != 1
            or int(opened.st_dev) != approved_device
            or mode & 0o033
            or mode & 0o7000
        ):
            _fail("R8U_SCHEDULER_LOG_TOPOLOGY_INVALID")
        if int(opened.st_uid) != os.geteuid() or mode not in {0o600, 0o644}:
            _fail("R8U_SCHEDULER_LOG_AUTHORITY_INVALID")
        if (
            int(opened.st_size) < 0
            or int(opened.st_size) > R8U_SCHEDULER_LOG_MAX_BYTES
        ):
            _fail("R8U_SCHEDULER_LOG_SIZE_INVALID")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > R8U_SCHEDULER_LOG_MAX_BYTES:
                _fail("R8U_SCHEDULER_LOG_SIZE_INVALID")
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
        visible_after = _r8u_stable_lstat(path)
    except R8RControllerError as exc:
        if exc.code in {
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
            "R8U_SCHEDULER_LOG_AUTHORITY_INVALID",
            "R8U_SCHEDULER_LOG_SIZE_INVALID",
        }:
            raise
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID"
        ) from exc
    except OSError as exc:
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    identity = lambda value: (
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if (
        total != int(opened.st_size)
        or not (
            identity(visible_before)
            == identity(opened)
            == identity(opened_after)
            == identity(visible_after)
        )
    ):
        _fail("R8U_SCHEDULER_LOG_TOPOLOGY_INVALID")
    digest_value = digest.hexdigest()
    if binding.role == "FAILED_R8U_BATCH16_RECOVERY":
        expected_size, expected_sha256 = (
            R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/"
                f"{R8U_FAILED_RECOVERY_JOB_NAME}.o"
                f"{R8U_FAILED_RECOVERY_JOB_ID}"
            ]
        )
        if total != expected_size or digest_value != expected_sha256:
            _fail("R8U_SCHEDULER_LOG_AUTHORITY_INVALID")
    return {
        "artifact_type": "lvef_c3_r8u_r2_scheduler_log_evidence_v1",
        "status": "PASS_ROLE_BOUND_GRID_ENGINE_MERGED_STDOUT_STDERR_LOG",
        "evidence_class": R8U_SCHEDULER_LOG_ROLE,
        "role": binding.role,
        "basename": path.name,
        "job_id": binding.job_id,
        "task_id": binding.task_id,
        "mode": f"{stat.S_IMODE(opened.st_mode):04o}",
        "size_bytes": total,
        "sha256": digest_value,
        "owner_uid": int(opened.st_uid),
        "terminal_state": binding.terminal_state,
    }


def _r8u_failed_recovery_scheduler_binding() -> _R8USchedulerLogBinding:
    return _R8USchedulerLogBinding(
        role="FAILED_R8U_BATCH16_RECOVERY",
        scheduler_root=(
            ATTEMPT_ROOT / "r8u_batch16_recovery" / "scheduler"
        ),
        job_name=R8U_FAILED_RECOVERY_JOB_NAME,
        job_id=R8U_FAILED_RECOVERY_JOB_ID,
        task_id="NONE",
        terminal_state="TERMINAL_FAILED_APPLICATION_EXIT_78",
    )


def _r8u_fresh_recovery_terminal_state() -> str:
    if os.path.lexists(R8U_RECOVERY_ACCOUNTING_PATH):
        return "TERMINAL_PASS_APPLICATION_EXIT_0"
    if os.path.lexists(R8U_RECOVERY_TERMINAL_PATH):
        return "APPLICATION_PASS_AWAITING_ACCOUNTING"
    return "SUBMITTED_OR_RUNNING"


def _r8u_scheduler_log_bindings() -> Mapping[
    PurePosixPath, _R8USchedulerLogBinding
]:
    """Build the closed scheduler-log allowlist only from fixed receipts."""

    bindings: dict[PurePosixPath, _R8USchedulerLogBinding] = {}

    def add(binding: _R8USchedulerLogBinding) -> None:
        relative = _r8u_scheduler_log_relative_path(binding)
        if relative in bindings:
            _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
        bindings[relative] = binding

    add(_r8u_failed_recovery_scheduler_binding())
    if not os.path.lexists(R8U_RECOVERY_SUBMISSION_PATH):
        return dict(bindings)

    try:
        implementation_commit = _current_r8u_implementation_commit()
        recovery, _ = _load_private_json(R8U_RECOVERY_SUBMISSION_PATH)
        recovery_job_id = str(recovery.get("recovery_job_id", ""))
        expected_recovery = _r8u_recovery_submission_receipt(
            implementation_commit=implementation_commit,
            recovery_job_id=recovery_job_id,
            qsub_environment_sha256=str(
                recovery.get("qsub_environment_sha256", "")
            ),
            recovery_authority_sha256=str(
                recovery.get("recovery_authority_sha256", "")
            ),
            partial_seal_sha256=str(
                recovery.get("failed_partial_seal_sha256", "")
            ),
            capacity_sha256=str(
                recovery.get("recovery_capacity_sha256", "")
            ),
        )
        if not _exact_typed_value_equal(recovery, expected_recovery):
            _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
        add(
            _R8USchedulerLogBinding(
                role="FRESH_R8U_R2_BATCH16_RECOVERY",
                scheduler_root=(
                    ATTEMPT_ROOT
                    / "r8u_r2_batch16_recovery"
                    / "scheduler"
                ),
                job_name=_r8u_recovery_job_name(implementation_commit),
                job_id=recovery_job_id,
                task_id="NONE",
                terminal_state=_r8u_fresh_recovery_terminal_state(),
            )
        )

        if not os.path.lexists(R8U_CONTINUATION_SUBMISSION_PATH):
            return dict(bindings)
        continuation, _ = _load_private_json(
            R8U_CONTINUATION_SUBMISSION_PATH
        )
        claim_payload = _read_private(R8U_CONTINUATION_CLAIM_PATH)
        array_job_id = str(continuation.get("array_job_id", ""))
        finalizer_job_id = str(continuation.get("finalizer_job_id", ""))
        expected_continuation = _r8u_continuation_submission_receipt(
            implementation_commit=implementation_commit,
            recovery_job_id=recovery_job_id,
            array_job_id=array_job_id,
            finalizer_job_id=finalizer_job_id,
            qsub_environment_sha256=str(
                continuation.get("qsub_environment_sha256", "")
            ),
            continuation_claim_sha256=_sha256_bytes(claim_payload),
        )
        if not _exact_typed_value_equal(
            continuation, expected_continuation
        ):
            _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
        for task_id in R8U_FIXED_CONTINUATION_TASK_IDS:
            add(
                _R8USchedulerLogBinding(
                    role="R8U_R2_CONTINUATION_ARRAY_TASK",
                    scheduler_root=(
                        ATTEMPT_ROOT
                        / "r8u_r2_continuation_17_19"
                        / "scheduler"
                    ),
                    job_name=_r8u_continuation_array_job_name(
                        implementation_commit
                    ),
                    job_id=array_job_id,
                    task_id=str(task_id),
                    terminal_state="SUBMITTED_OR_RUNNING",
                )
            )
        add(
            _R8USchedulerLogBinding(
                role="R8U_R2_COHORT_FINALIZER",
                scheduler_root=(
                    ATTEMPT_ROOT
                    / "r8u_r2_continuation_17_19"
                    / "scheduler"
                ),
                job_name=_r8u_continuation_finalizer_job_name(
                    implementation_commit
                ),
                job_id=finalizer_job_id,
                task_id="NONE",
                terminal_state="HELD_OR_RUNNING",
            )
        )
    except R8RControllerError as exc:
        if exc.code == "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID":
            raise
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID"
        ) from exc
    except Exception as exc:
        raise R8RControllerError(
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID"
        ) from exc
    return dict(bindings)


def _r8u_failed_recovery_epoch_authority() -> Mapping[str, Any]:
    """Validate the consumed R8U-R1 namespace without writing to it."""

    root = ATTEMPT_ROOT / "r8u_batch16_recovery"
    scheduler_root = root / "scheduler"
    expected_files = set(R8U_FAILED_RECOVERY_FILE_AUTHORITIES)
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    try:
        root_info = _r8u_stable_lstat(ATTEMPT_ROOT)
        approved_device = int(root_info.st_dev)
        for directory, names, files in os.walk(
            root, topdown=True, followlinks=False
        ):
            names.sort()
            files.sort()
            directory_path = Path(directory)
            relative_directory = directory_path.relative_to(root).as_posix()
            observed_directories.add(relative_directory)
            info = _r8u_stable_lstat(directory_path)
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or not _r8u_private_directory_metadata_valid(
                    mode=stat.S_IMODE(info.st_mode),
                    uid=int(info.st_uid),
                    device=int(info.st_dev),
                    approved_device=approved_device,
                )
                or os.path.ismount(directory_path)
            ):
                _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")
            for name in names:
                child = directory_path / name
                child_info = _r8u_stable_lstat(child)
                if (
                    not stat.S_ISDIR(child_info.st_mode)
                    or stat.S_ISLNK(child_info.st_mode)
                ):
                    _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")
            for name in files:
                path = directory_path / name
                relative = path.relative_to(root).as_posix()
                file_info = _r8u_stable_lstat(path)
                expected_log_relative = (
                    "scheduler/"
                    f"{R8U_FAILED_RECOVERY_JOB_NAME}.o"
                    f"{R8U_FAILED_RECOVERY_JOB_ID}"
                )
                if (
                    not stat.S_ISREG(file_info.st_mode)
                    or stat.S_ISLNK(file_info.st_mode)
                    or int(file_info.st_uid) != os.geteuid()
                    or int(file_info.st_nlink) != 1
                    or int(file_info.st_dev) != approved_device
                    or (
                        relative != expected_log_relative
                        and stat.S_IMODE(file_info.st_mode) != 0o600
                    )
                ):
                    _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")
                observed_files.add(relative)
        if (
            observed_directories != {".", "scheduler"}
            or observed_files != expected_files
        ):
            _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")

        binding = _r8u_failed_recovery_scheduler_binding()
        log_relative = _r8u_scheduler_log_relative_path(binding)
        log_path = ATTEMPT_ROOT / Path(log_relative.as_posix())
        scheduler_evidence = _r8u_scheduler_log_evidence(
            path=log_path,
            binding=binding,
            approved_device=approved_device,
        )
        expected_log = R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
            f"scheduler/{log_path.name}"
        ]
        if (
            scheduler_evidence.get("mode") != "0644"
            or scheduler_evidence.get("size_bytes") != expected_log[0]
            or scheduler_evidence.get("sha256") != expected_log[1]
        ):
            _fail("R8U_SCHEDULER_LOG_AUTHORITY_INVALID")

        inventory_rows: list[list[Any]] = []
        for relative in sorted(expected_files):
            expected_size, expected_sha = (
                R8U_FAILED_RECOVERY_FILE_AUTHORITIES[relative]
            )
            path = root / Path(relative)
            if relative == f"scheduler/{log_path.name}":
                payload_sha = str(scheduler_evidence["sha256"])
                payload_size = int(scheduler_evidence["size_bytes"])
            else:
                payload = _read_private_exact(
                    path, size=expected_size, digest=expected_sha
                )
                payload_sha = _sha256_bytes(payload)
                payload_size = len(payload)
            if payload_size != expected_size or payload_sha != expected_sha:
                _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")
            inventory_rows.append(
                [relative, expected_size, expected_sha]
            )

        submission_payload = _read_private_exact(
            scheduler_root / "submission_receipt.restricted.json",
            size=R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/submission_receipt.restricted.json"
            ][0],
            digest=R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/submission_receipt.restricted.json"
            ][1],
        )
        submission = _strict_json(submission_payload)
        stdout = _read_private_exact(
            scheduler_root / "recovery.qsub.stdout.restricted",
            size=8,
            digest=R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/recovery.qsub.stdout.restricted"
            ][1],
        )
        stderr = _read_private_exact(
            scheduler_root / "recovery.qsub.stderr.restricted",
            size=0,
            digest=R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/recovery.qsub.stderr.restricted"
            ][1],
        )
        qsub_exit = _read_private_exact(
            scheduler_root / "recovery.qsub.exit_status.restricted",
            size=2,
            digest=R8U_FAILED_RECOVERY_FILE_AUTHORITIES[
                "scheduler/recovery.qsub.exit_status.restricted"
            ][1],
        )
        if (
            stdout != f"{R8U_FAILED_RECOVERY_JOB_ID}\n".encode("ascii")
            or stderr != b""
            or qsub_exit != b"0\n"
            or submission.get("implementation_commit")
            != R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
            or submission.get("recovery_job_name")
            != R8U_FAILED_RECOVERY_JOB_NAME
            or str(submission.get("recovery_job_id", ""))
            != R8U_FAILED_RECOVERY_JOB_ID
            or submission.get("scheduler_submission_count") != 1
            or submission.get("cloud_requests") != 0
            or submission.get("download_reruns") != 0
        ):
            _fail("R8U_FAILED_RECOVERY_EVIDENCE_INVALID")
    except R8RControllerError as exc:
        if exc.code.startswith("R8U_SCHEDULER_LOG_"):
            raise
        raise R8RControllerError(
            "R8U_FAILED_RECOVERY_EVIDENCE_INVALID"
        ) from exc
    except Exception as exc:
        raise R8RControllerError(
            "R8U_FAILED_RECOVERY_EVIDENCE_INVALID"
        ) from exc

    authority = {
        "artifact_type": "lvef_c3_r8u_r1_failed_recovery_epoch_authority_v1",
        "status": "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78",
        "implementation_commit": R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        "job_id": R8U_FAILED_RECOVERY_JOB_ID,
        "job_name": R8U_FAILED_RECOVERY_JOB_NAME,
        "qsub_exit": R8U_FAILED_RECOVERY_QSUB_EXIT,
        "qacct_failed": R8U_FAILED_RECOVERY_QACCT_FAILED,
        "qacct_exit_status": R8U_FAILED_RECOVERY_QACCT_EXIT_STATUS,
        "qacct_task_id": "NONE",
        "terminal_code": R8U_FAILED_RECOVERY_TERMINAL_CODE,
        "scheduler_evidence": dict(scheduler_evidence),
        "namespace_file_count": len(inventory_rows),
        "namespace_inventory_sha256": core.canonical_json_sha256(
            inventory_rows
        ),
        "dicom_body_reads": 0,
        "cloud_requests": 0,
        "download_reruns": 0,
        "extraction_reruns": 0,
        "echoprime_reruns": 0,
        "embedding_generations": 0,
    }
    return dict(sorted(authority.items()))


def _r8u_scheduler_evidence_projection() -> Mapping[str, Any]:
    """Observe only existing, receipt-bound scheduler logs."""

    root_info = _r8u_stable_lstat(ATTEMPT_ROOT)
    approved_device = int(root_info.st_dev)
    evidence: list[Mapping[str, Any]] = []
    for relative, binding in sorted(
        _r8u_scheduler_log_bindings().items(),
        key=lambda item: item[0].as_posix(),
    ):
        path = ATTEMPT_ROOT / Path(relative.as_posix())
        if os.path.lexists(path):
            evidence.append(
                _r8u_scheduler_log_evidence(
                    path=path,
                    binding=binding,
                    approved_device=approved_device,
                )
            )
    ordered = sorted(
        (dict(value) for value in evidence),
        key=lambda value: (
            str(value["role"]),
            str(value["job_id"]),
            str(value["task_id"]),
        ),
    )
    return {
        "status": "PASS_CLOSED_ROLE_BOUND_SCHEDULER_EVIDENCE",
        "scheduler_log_count": len(ordered),
        "scheduler_log_evidence": ordered,
        "scheduler_log_evidence_sha256": core.canonical_json_sha256(
            ordered
        ),
    }


def _r8u_decode_mountinfo_path(value: str) -> Path:
    # Linux mountinfo escapes space, tab, newline, and backslash as octal.
    decoded = re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )
    return Path(decoded)


def _r8u_nested_mount_paths(attempt_root: Path) -> frozenset[Path]:
    """Detect ordinary and bind mounts strictly below the attempt root."""

    nested: set[Path] = set()
    if sys.platform.startswith("linux"):
        try:
            with Path("/proc/self/mountinfo").open(
                "r", encoding="utf-8", errors="strict"
            ) as handle:
                for line in handle:
                    fields = line.rstrip("\n").split(" ")
                    if len(fields) < 6:
                        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
                    mount_path = _r8u_decode_mountinfo_path(fields[4])
                    if (
                        mount_path != attempt_root
                        and mount_path.is_relative_to(attempt_root)
                    ):
                        nested.add(mount_path)
        except R8RControllerError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise R8RControllerError(
                "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID"
            ) from exc
    return frozenset(nested)


def _r8u_scan_attempt_content() -> _R8UAttemptContentScan:
    """Project stable baseline metadata without opening DICOM or NPZ bodies.

    Fixed successor paths are deliberately absent from this baseline.  Their
    pre-creation absence is enforced by the existing no-clobber/collision
    gates, and their post-creation contents are governed by their closed R8U
    receipts.  In particular, retained Batch-16 raw data and the failed
    ``dicom_extraction.partial`` evidence are never excluded here.
    """

    root = ATTEMPT_ROOT
    if (
        not root.is_absolute()
        or Path(os.path.abspath(root)) != root
    ):
        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
    root_info = _r8u_stable_lstat(root)
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")

    approved_device = int(root_info.st_dev)
    if not _r8u_private_directory_metadata_valid(
        mode=stat.S_IMODE(root_info.st_mode),
        uid=int(root_info.st_uid),
        device=approved_device,
        approved_device=approved_device,
    ):
        _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")
    nested_mounts = _r8u_nested_mount_paths(root)
    scheduler_bindings = _r8u_scheduler_log_bindings()
    scheduler_roots = frozenset(
        {
            PurePosixPath("r8u_batch16_recovery/scheduler"),
            PurePosixPath("r8u_r2_batch16_recovery/scheduler"),
            PurePosixPath("r8u_r2_continuation_17_19/scheduler"),
        }
    )
    scheduler_evidence: list[Mapping[str, Any]] = []
    directory_metadata: dict[
        PurePosixPath, tuple[int, int, int, int]
    ] = {
        PurePosixPath("."): (
            stat.S_IMODE(root_info.st_mode),
            int(root_info.st_uid),
            int(root_info.st_gid),
            approved_device,
        )
    }
    required_directories: set[PurePosixPath] = {PurePosixPath(".")}
    regular_digest = hashlib.sha256()
    path_set_digest = hashlib.sha256()
    regular_file_count = 0
    regular_file_bytes = 0
    symlink_count = 0
    nonregular_count = 0
    path_escape_count = 0
    duplicate_relative_path_count = 0
    successor_directory_metadata: dict[
        PurePosixPath, tuple[int, int, int, int]
    ] = {}
    stack = [root]

    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
            child_directories: list[Path] = []
            names: set[str] = set()
            for entry in entries:
                if entry.name in names:
                    duplicate_relative_path_count += 1
                    continue
                names.add(entry.name)
                path = Path(entry.path)
                try:
                    relative_path = path.relative_to(root)
                except ValueError:
                    path_escape_count += 1
                    continue
                relative = PurePosixPath(relative_path.as_posix())
                if (
                    path.parent != current
                    or relative.is_absolute()
                    or ".." in relative.parts
                ):
                    path_escape_count += 1
                    continue
                info = _r8u_stable_lstat(path)
                excluded = _r8u_is_at_or_beneath(
                    relative, R8U_SUCCESSOR_EXCLUSION_PATHS
                )
                scheduler_binding = scheduler_bindings.get(relative)
                if scheduler_binding is not None:
                    if not excluded:
                        _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
                    scheduler_evidence.append(
                        _r8u_scheduler_log_evidence(
                            path=path,
                            binding=scheduler_binding,
                            approved_device=approved_device,
                        )
                    )
                    continue
                if (
                    relative.parent in scheduler_roots
                    and _r8u_log_shaped_basename(path.name)
                ):
                    _fail("R8U_SCHEDULER_LOG_JOB_BINDING_INVALID")
                if info.st_dev != approved_device:
                    _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
                if stat.S_ISDIR(info.st_mode):
                    directory_safe = (
                        _r8u_private_directory_metadata_valid(
                            mode=stat.S_IMODE(info.st_mode),
                            uid=int(info.st_uid),
                            device=int(info.st_dev),
                            approved_device=approved_device,
                        )
                        and path not in nested_mounts
                        and not os.path.ismount(path)
                    )
                    if not directory_safe:
                        if excluded:
                            _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
                        if (
                            relative in R8U_FIXED_REQUIRED_DIRECTORY_PATHS
                            or _r8u_is_at_or_beneath(
                                relative, R8U_PROTECTED_DIRECTORY_ROLE_ROOTS
                            )
                        ):
                            _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")
                        _fail(
                            "R8U_OPTIONAL_EMPTY_DIRECTORY_COMPATIBILITY_INVALID"
                        )
                if excluded:
                    # Successor files cannot participate in the immutable
                    # pre-R8U baseline because the authorized live actions
                    # create them.  They are nevertheless traversed so an
                    # excluded namespace cannot conceal an unsafe link,
                    # special entry, nested mount, or filesystem escape.
                    if stat.S_ISDIR(info.st_mode):
                        if relative in successor_directory_metadata:
                            duplicate_relative_path_count += 1
                            continue
                        successor_directory_metadata[relative] = (
                            stat.S_IMODE(info.st_mode),
                            int(info.st_uid),
                            int(info.st_gid),
                            int(info.st_dev),
                        )
                        child_directories.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        if (
                            int(info.st_uid) != os.geteuid()
                            or int(info.st_nlink) != 1
                        ):
                            _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
                        if stat.S_IMODE(info.st_mode) & 0o077:
                            _fail(
                                "R8U_UNCLASSIFIED_PUBLIC_FILE_IN_"
                                "SUCCESSOR_NAMESPACE"
                            )
                    elif stat.S_ISLNK(info.st_mode):
                        symlink_count += 1
                    else:
                        nonregular_count += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if relative in directory_metadata:
                        duplicate_relative_path_count += 1
                        continue
                    directory_metadata[relative] = (
                        stat.S_IMODE(info.st_mode),
                        int(info.st_uid),
                        int(info.st_gid),
                        int(info.st_dev),
                    )
                    child_directories.append(path)
                elif stat.S_ISREG(info.st_mode):
                    regular_file_count += 1
                    regular_file_bytes += int(info.st_size)
                    regular_digest.update(
                        _r8u_projection_row_bytes(
                            (
                                relative.as_posix(),
                                "F",
                                stat.S_IMODE(info.st_mode),
                                int(info.st_uid),
                                int(info.st_gid),
                                int(info.st_nlink),
                                int(info.st_size),
                                int(info.st_mtime_ns),
                                int(info.st_ctime_ns),
                            )
                        )
                    )
                    path_set_digest.update(
                        _r8u_projection_row_bytes((relative.as_posix(),))
                    )
                    required_directories.update(relative.parents)
                elif stat.S_ISLNK(info.st_mode):
                    symlink_count += 1
                else:
                    nonregular_count += 1
            stack.extend(reversed(child_directories))
    except R8RControllerError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise R8RControllerError(
            "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID"
        ) from exc

    required_directories.update(R8U_FIXED_REQUIRED_DIRECTORY_PATHS)
    required_directories.update(
        relative
        for relative in directory_metadata
        if _r8u_is_at_or_beneath(
            relative, R8U_PROTECTED_DIRECTORY_ROLE_ROOTS
        )
    )
    for relative in tuple(required_directories):
        required_directories.update(relative.parents)

    missing_fixed = R8U_FIXED_REQUIRED_DIRECTORY_PATHS.difference(
        directory_metadata
    )
    if missing_fixed:
        for relative in missing_fixed:
            fixed_path = root / Path(relative.as_posix())
            if os.path.lexists(fixed_path):
                _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")
        _fail("R8U_REQUIRED_DIRECTORY_MISSING")
    if symlink_count or nonregular_count:
        _fail("R8U_UNSAFE_NONREGULAR_ENTRY")
    if path_escape_count or duplicate_relative_path_count:
        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")

    required_directory_digest = hashlib.sha256()
    for relative in sorted(
        required_directories, key=lambda value: value.as_posix()
    ):
        metadata = directory_metadata.get(relative)
        if metadata is None:
            # A non-fixed protected empty directory was removed or replaced.
            _fail("R8U_REQUIRED_DIRECTORY_MISSING")
        mode, uid, gid, device = metadata
        absolute = root / Path(relative.as_posix())
        if (
            not _r8u_private_directory_metadata_valid(
                mode=mode,
                uid=uid,
                device=device,
                approved_device=approved_device,
            )
            or absolute in nested_mounts
            or (absolute != root and os.path.ismount(absolute))
        ):
            _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")
        required_directory_digest.update(
            _r8u_projection_row_bytes(
                (relative.as_posix(), "D", mode, uid, gid)
            )
        )

    optional_directories = set(directory_metadata).difference(
        required_directories
    )
    for relative in optional_directories:
        mode, uid, _gid, device = directory_metadata[relative]
        absolute = root / Path(relative.as_posix())
        if (
            not _r8u_private_directory_metadata_valid(
                mode=mode,
                uid=uid,
                device=device,
                approved_device=approved_device,
            )
            or absolute in nested_mounts
            or os.path.ismount(absolute)
            or any(
                relative in protected.parents
                for protected in required_directories
            )
        ):
            _fail("R8U_OPTIONAL_EMPTY_DIRECTORY_COMPATIBILITY_INVALID")

    authority: dict[str, Any] = {
        "regular_file_count": regular_file_count,
        "regular_file_bytes": regular_file_bytes,
        "regular_file_projection_sha256": regular_digest.hexdigest(),
        "required_directory_count": len(required_directories),
        "required_directory_projection_sha256": (
            required_directory_digest.hexdigest()
        ),
        "optional_empty_directory_count": len(optional_directories),
        "symlink_count": symlink_count,
        "nonregular_count": nonregular_count,
        "path_escape_count": path_escape_count,
    }
    if set(authority) != R8U_ATTEMPT_CONTENT_AUTHORITY_KEYS:
        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
    ordered_scheduler_evidence = tuple(
        sorted(
            (dict(value) for value in scheduler_evidence),
            key=lambda value: (
                str(value["role"]),
                str(value["job_id"]),
                str(value["task_id"]),
            ),
        )
    )
    return _R8UAttemptContentScan(
        authority=dict(sorted(authority.items())),
        regular_file_path_set_sha256=path_set_digest.hexdigest(),
        duplicate_relative_path_count=duplicate_relative_path_count,
        scheduler_evidence=ordered_scheduler_evidence,
        scheduler_evidence_projection_sha256=core.canonical_json_sha256(
            list(ordered_scheduler_evidence)
        ),
    )


def _r8u_validate_pristine_exclusion_paths(
    paths: frozenset[PurePosixPath],
) -> None:
    """Prove one fixed set of successor namespaces is still pristine.

    Every excluded path must be absent.  The sole compatibility exception is
    an already-created ``cohort_finalization`` directory, which is accepted
    only when it is an owner-private, same-filesystem, non-mount empty
    directory.  The fixed recovery and sequential collision gates provide a
    second proof immediately before their respective outputs are created.
    """

    root_info = _r8u_stable_lstat(ATTEMPT_ROOT)
    approved_device = int(root_info.st_dev)
    nested_mounts = _r8u_nested_mount_paths(ATTEMPT_ROOT)
    cohort_relative = PurePosixPath("cohort_finalization")
    for relative in sorted(
        paths, key=lambda value: value.as_posix()
    ):
        path = ATTEMPT_ROOT / Path(relative.as_posix())
        if not os.path.lexists(path):
            continue
        if relative != cohort_relative:
            _fail("R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE")
        info = _r8u_stable_lstat(path)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or not _r8u_private_directory_metadata_valid(
                mode=stat.S_IMODE(info.st_mode),
                uid=int(info.st_uid),
                device=int(info.st_dev),
                approved_device=approved_device,
            )
            or os.path.ismount(path)
            or path in nested_mounts
        ):
            _fail("R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE")
        try:
            with os.scandir(path) as iterator:
                empty = next(iterator, None) is None
        except OSError as exc:
            raise R8RControllerError(
                "R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE"
            ) from exc
        if not empty:
            _fail("R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE")


def _r8u_validate_pristine_successor_exclusions() -> None:
    """Prove the fresh R2 namespaces pristine without rejecting failed R1."""

    _r8u_validate_pristine_exclusion_paths(
        frozenset(
            path
            for path in R8U_SUCCESSOR_EXCLUSION_PATHS
            if path != R8U_FAILED_RECOVERY_RELATIVE_ROOT
        )
    )


def _r8u_validate_pristine_continuation_exclusions() -> None:
    """Re-prove future Task-17--19 namespaces after Batch-16 recovery."""

    _r8u_validate_pristine_exclusion_paths(R8U_CONTINUATION_PRISTINE_PATHS)


def _r8u_validate_pristine_continuation_task_outputs() -> None:
    """Late barrier for scientific outputs immediately before array qsub."""

    _r8u_validate_pristine_exclusion_paths(
        R8U_CONTINUATION_TASK_OUTPUT_PRISTINE_PATHS
    )


def _r8u_validate_attempt_content_authority() -> Mapping[str, Any]:
    """Validate the portable sealed prefix before or after an R8U action."""

    if (
        SHA_RE.fullmatch(R8U_BASELINE_REGULAR_FILE_PATH_SET_SHA256) is None
        or SHA_RE.fullmatch(
            R8U_BASELINE_REGULAR_FILE_PROJECTION_SHA256
        ) is None
        or isinstance(R8U_BASELINE_REQUIRED_DIRECTORY_COUNT, bool)
        or R8U_BASELINE_REQUIRED_DIRECTORY_COUNT < 1
        or SHA_RE.fullmatch(
            R8U_BASELINE_REQUIRED_DIRECTORY_PROJECTION_SHA256
        ) is None
    ):
        _fail("R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID")
    scan = _r8u_scan_attempt_content()
    authority = scan.authority
    if (
        scan.regular_file_path_set_sha256
        != R8U_BASELINE_REGULAR_FILE_PATH_SET_SHA256
        or authority["regular_file_count"]
        != R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION["regular_file_count"]
    ):
        _fail("R8U_REGULAR_FILE_PATH_SET_CHANGED")
    if (
        authority["regular_file_bytes"]
        != R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION["regular_file_bytes"]
    ):
        _fail("R8U_REGULAR_FILE_BYTES_CHANGED")
    if (
        authority["regular_file_projection_sha256"]
        != R8U_BASELINE_REGULAR_FILE_PROJECTION_SHA256
    ):
        _fail("R8U_REGULAR_FILE_PROJECTION_CHANGED")
    observed_required_count = int(authority["required_directory_count"])
    if observed_required_count < R8U_BASELINE_REQUIRED_DIRECTORY_COUNT:
        _fail("R8U_REQUIRED_DIRECTORY_MISSING")
    if (
        observed_required_count != R8U_BASELINE_REQUIRED_DIRECTORY_COUNT
        or authority["required_directory_projection_sha256"]
        != R8U_BASELINE_REQUIRED_DIRECTORY_PROJECTION_SHA256
    ):
        _fail("R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID")
    return authority


def _r8u_validate_pre_mutation_projections() -> Mapping[str, Any]:
    """Validate portable attempt content and preserve the independent R4 gate."""

    authority = _r8u_validate_attempt_content_authority()
    try:
        import retire_lvef_c3_older_raw_duplicates as older

        r4 = older._validate_r4()
    except Exception as exc:
        raise R8RControllerError("R8U_R4_PROJECTION_INVALID") from exc
    if (
        r4.get("file_count") != 233_257
        or r4.get("directory_count") != 300
        or r4.get("total_bytes") != 221_586_476_241
        or r4.get("metadata_stat_sha256")
        != "c820806c26ba9644061e7d1c92e79de48d69b7b91fa3d0d09763d028284fc4c6"
    ):
        _fail("R8U_R4_PROJECTION_INVALID")
    return authority


def _recovery_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_rec_{implementation_commit[:8]}"


def _continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_seq_{implementation_commit[:8]}"


def _continuation_finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_fin_{implementation_commit[:8]}"


def _recovery_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _recovery_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(RECOVERY_SCHEDULER_ROOT),
        "-l",
        "h_rt=02:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(RUNNER_PATH),
    ]


def _continuation_array_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _continuation_array_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(CONTINUATION_SCHEDULER_ROOT),
        "-t",
        FIXED_CONTINUATION_TASK_RANGE,
        "-tc",
        str(FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(RUNNER_PATH),
    ]


def _continuation_finalizer_command(
    implementation_commit: str, array_job_id: str
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8R_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _continuation_finalizer_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        str(RUNNER_PATH),
    ]


def _r8u_recovery_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_rec_{implementation_commit[:8]}"


def _r8u_continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_seq_{implementation_commit[:8]}"


def _r8u_continuation_finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_fin_{implementation_commit[:8]}"


def _r8u_recovery_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N", _r8u_recovery_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_RECOVERY_SCHEDULER_ROOT),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_continuation_array_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_continuation_array_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_CONTINUATION_SCHEDULER_ROOT),
        "-t", R8U_FIXED_CONTINUATION_TASK_RANGE,
        "-tc", str(R8U_FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_continuation_finalizer_command(
    implementation_commit: str, array_job_id: str
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8U_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_continuation_finalizer_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid", array_job_id, "-l", "h_rt=12:00:00",
        "-pe", "omp", "4", "-l", "mem_per_core=8G", str(RUNNER_PATH),
    ]


def _parse_r8u_array_qsub_stdout(payload: bytes) -> str:
    match = re.fullmatch(rb"([1-9][0-9]{0,19})(?:[.]17-19:1)?\n?", payload)
    if match is None:
        _fail("R8U_ARRAY_QSUB_OUTPUT_INVALID")
    return match.group(1).decode("ascii")


def _r8u_require_recovery_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    expected_absent = (
        R8U_RECOVERY_ROOT,
        R8U_CONTINUATION_ROOT,
        paths["extraction"],
        paths["extraction_ledger"],
        paths["pooling_ledger"],
        paths["eligibility_ledger"],
        paths["echoprime"],
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        ATTEMPT_ROOT / "cache_retirement_authorizations" / f"{R8U_FIXED_BATCH_ID}.authorization.json",
        paths["final_ledger"],
        paths["final_receipt"],
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8U_RECOVERY_OUTPUT_COLLISION")


def _r8u_recovery_authority(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    prefix_receipts: Sequence[str],
    partial_seal_sha256: str,
    capacity_sha256: str,
    raw_authority: Mapping[str, Any],
    failed_recovery_epoch_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    failed_epoch_sha256 = core.canonical_json_sha256(
        failed_recovery_epoch_authority
    )
    if (
        tuple(prefix_receipts)
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(partial_seal_sha256) is None
        or SHA_RE.fullmatch(capacity_sha256) is None
        or SHA_RE.fullmatch(failed_epoch_sha256) is None
        or failed_recovery_epoch_authority.get("job_id")
        != R8U_FAILED_RECOVERY_JOB_ID
        or failed_recovery_epoch_authority.get("status")
        != "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78"
    ):
        _fail("R8U_RECOVERY_AUTHORITY_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "status": "AUTHORIZED_FIXED_BATCH16_RECOVERY",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "prior_implementation_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "historical_r8r_chain_authority": dict(_r8u_historical_r8r_chain_authority()),
        "failed_r8u_recovery_epoch_authority": dict(
            failed_recovery_epoch_authority
        ),
        "failed_r8u_recovery_epoch_authority_sha256": failed_epoch_sha256,
        "failed_partial_seal_sha256": partial_seal_sha256,
        "recovery_capacity_sha256": capacity_sha256,
        "retained_raw_authority": dict(raw_authority),
        "runtime_authority_sha256": core.canonical_json_sha256(run.runtime_authority),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "runtime_validation_context": stages.SEALED_SCHEDULER_RUNTIME_REPLAY.value,
        "fresh_extraction_relative_root": (
            R8U_FRESH_EXTRACTION_BATCH_ROOT.relative_to(ATTEMPT_ROOT).as_posix()
        ),
        "cloud_requests_authorized": 0,
        "download_reruns_authorized": 0,
        "dicom_extraction_reruns_authorized": 1,
        "echoprime_reruns_authorized": 1,
        "gpu_executions_authorized": 1,
        "failed_partial_adoption_authorized": False,
        "failed_partial_mutation_authorized": False,
        "raw_dicom_deletion_authorized": False,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
        "maximum_new_qsub_submissions": 3,
    }


def _r8u_recovery_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    qsub_environment_sha256: str,
    recovery_authority_sha256: str,
    partial_seal_sha256: str,
    capacity_sha256: str,
) -> Mapping[str, Any]:
    if (
        JOB_RE.fullmatch(recovery_job_id) is None
        or any(
            SHA_RE.fullmatch(value) is None
            for value in (
                qsub_environment_sha256, recovery_authority_sha256,
                partial_seal_sha256, capacity_sha256,
            )
        )
    ):
        _fail("R8U_RECOVERY_SUBMISSION_INVALID")
    command = _r8u_recovery_qsub_command(implementation_commit)
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "status": "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "recovery_job_name": _r8u_recovery_job_name(implementation_commit),
        "recovery_job_id": recovery_job_id,
        "recovery_qsub_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": command})),
        "qsub_environment_sha256": qsub_environment_sha256,
        "recovery_authority_sha256": recovery_authority_sha256,
        "failed_partial_seal_sha256": partial_seal_sha256,
        "recovery_capacity_sha256": capacity_sha256,
        "recovery_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_RECOVERY_SCHEDULER_ROOT, "recovery")
        ),
        "scheduler_submission_count": 1,
        "recovery_is_array": False,
        "gpu_requested": True,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "download_reruns": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def _validate_r8u_recovery_submission(
    *, current_job_id: str | None = None, wait: bool = False
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(R8U_RECOVERY_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8U_RECOVERY_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id or "R8U_RECOVERY_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u=True,
    )
    authority, authority_payload = _load_private_json(R8U_RECOVERY_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_RECOVERY_SUBMISSION_PATH)
    implementation_commit = _current_r8u_implementation_commit()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    seal = validate_r8u_failed_partial_seal()
    capacity_value, capacity_payload = _load_private_json(R8U_RECOVERY_CAPACITY_PATH)
    try:
        capacity.validate_fixed_r8u_batch16_recovery_capacity(
            run.plan,
            capacity_value,
            r8u_scheduler_log_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_RECOVERY_CAPACITY_INVALID") from exc
    qsub_sha = str(authority.get("qsub_environment_sha256", ""))
    expected_authority = _r8u_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=qsub_sha,
        prefix_receipts=prefix,
        partial_seal_sha256=core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH),
        capacity_sha256=_sha256_bytes(capacity_payload),
        raw_authority=_r8u_batch16_raw_control_authority(run),
        failed_recovery_epoch_authority=(
            _r8u_failed_recovery_epoch_authority()
        ),
    )
    if not _exact_typed_value_equal(authority, expected_authority):
        _fail("R8U_RECOVERY_AUTHORITY_INVALID")
    job_id = str(submission.get("recovery_job_id", ""))
    expected_submission = _r8u_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=job_id,
        qsub_environment_sha256=qsub_sha,
        recovery_authority_sha256=_sha256_bytes(authority_payload),
        partial_seal_sha256=core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH),
        capacity_sha256=_sha256_bytes(capacity_payload),
    )
    if not _exact_typed_value_equal(submission, expected_submission):
        _fail("R8U_RECOVERY_SUBMISSION_RECEIPT_INVALID")
    if current_job_id is not None and current_job_id != job_id:
        _fail("R8U_RECOVERY_JOB_ID_MISMATCH")
    if seal.get("status") != "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE":
        _fail("R8U_FAILED_PARTIAL_SEAL_INVALID")
    return authority, submission


def _r8u_capacity_deficits(value: Mapping[str, Any]) -> Mapping[str, int]:
    """Return exact nonnegative deficits from one validated live observation."""

    fields = {
        "quota_deficit_bytes": "quota_margin_beyond_reserve_bytes",
        "physical_deficit_bytes": "physical_margin_beyond_reserve_bytes",
        "file_slot_deficit": "file_slot_margin_after_demand",
    }
    result: dict[str, int] = {}
    for output, source in fields.items():
        observed = value.get(source)
        if isinstance(observed, bool) or not isinstance(observed, int):
            _fail("R8U_RECOVERY_CAPACITY_INVALID")
        result[output] = max(0, -observed)
    return result


def _validate_r8u_initial_recovery_qstat(
    *,
    environment: Mapping[str, str],
    recovery_job_id: str,
    implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> str:
    """Take one non-polling qstat snapshot for the fresh recovery job."""

    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_INITIAL_QSTAT_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8U_INITIAL_QSTAT_INVALID") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local(element) for element in list(root)]
    if (
        local(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(
            name not in {"queue_info", "job_info"}
            for name in direct_children
        )
    ):
        _fail("R8U_INITIAL_QSTAT_INVALID")
    matches: list[tuple[str, str, str]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local(child), []).append(child.text or "")
        if recovery_job_id not in fields.get("JB_job_number", []):
            continue
        names = fields.get("JB_name", [])
        states = fields.get("state", [])
        attribute_state = str(job.get("state", ""))
        if len(names) != 1 or len(states) != 1:
            _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
        matches.append((names[0], states[0], attribute_state))
    if len(matches) != 1:
        _fail("R8U_INITIAL_QSTAT_TOPOLOGY_INVALID")
    job_name, state, attribute_state = matches[0]
    if job_name != _r8u_recovery_job_name(implementation_commit):
        _fail("R8U_INITIAL_QSTAT_TOPOLOGY_INVALID")
    if state not in {"r", "qw", "t", "Rr"}:
        _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
    expected_category = "running" if state in {"r", "t", "Rr"} else "pending"
    if attribute_state != expected_category:
        _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
    return state


def submit_r8u_batch16_recovery(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    """Create one fixed claim and submit one non-array GPU recovery."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    _validate_r8u_no_active_processes(environment, runner=process_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_RECOVERY_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
        r8u=True,
    )
    _validate_original_controls()
    _r8u_validate_pristine_successor_exclusions()
    _r8u_historical_r8r_chain_authority()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    partial_seal = _r8u_failed_partial_seal(
        implementation_commit=implementation_commit
    )
    raw_authority = _r8u_batch16_raw_control_authority(run)
    failed_recovery_epoch_authority = (
        _r8u_failed_recovery_epoch_authority()
    )
    _r8u_require_recovery_absent(run)
    # This is intentionally the sole live capacity observation in R8U.
    try:
        capacity_value = capacity.probe_fixed_r8u_batch16_recovery_capacity(
            run.plan,
            r8u_scheduler_log_repair_commit=implementation_commit,
            process_runner=capacity_process_runner,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_RECOVERY_CAPACITY_PROBE_FAILED") from exc
    try:
        capacity.validate_fixed_r8u_batch16_recovery_capacity(
            run.plan,
            capacity_value,
            r8u_scheduler_log_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_RECOVERY_CAPACITY_INVALID") from exc
    if capacity_value.get("status") != R8U_CAPACITY_STATUS:
        raise R8RControllerError(
            "R8U_RECOVERY_CAPACITY_BLOCKED",
            capacity_deficits=_r8u_capacity_deficits(capacity_value),
        )
    _create_private_directory_no_clobber(R8U_RECOVERY_ROOT)
    _create_private_directory_no_clobber(R8U_RECOVERY_SCHEDULER_ROOT)
    partial_sha = _write_private_json(R8U_FAILED_PARTIAL_SEAL_PATH, partial_seal)
    capacity_sha = _write_private_json(R8U_RECOVERY_CAPACITY_PATH, capacity_value)
    authority = _r8u_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix,
        partial_seal_sha256=partial_sha,
        capacity_sha256=capacity_sha,
        raw_authority=raw_authority,
        failed_recovery_epoch_authority=failed_recovery_epoch_authority,
    )
    authority_sha = _write_private_json(R8U_RECOVERY_AUTHORITY_PATH, authority)
    job_id = scheduler._capture_qsub(
        "recovery",
        _r8u_recovery_qsub_command(implementation_commit),
        root=R8U_RECOVERY_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _r8u_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=job_id,
        qsub_environment_sha256=environment_sha,
        recovery_authority_sha256=authority_sha,
        partial_seal_sha256=partial_sha,
        capacity_sha256=capacity_sha,
    )
    _write_private_json(R8U_RECOVERY_SUBMISSION_PATH, receipt)
    receipt_readback, _ = _load_private_json(R8U_RECOVERY_SUBMISSION_PATH)
    if not _exact_typed_value_equal(receipt_readback, receipt):
        _fail("R8U_RECOVERY_SUBMISSION_RECEIPT_INVALID")
    initial_state = _validate_r8u_initial_recovery_qstat(
        environment=environment,
        recovery_job_id=job_id,
        implementation_commit=implementation_commit,
        runner=qstat_runner,
    )
    return {
        "status": "BATCH16_RECOVERY_SUBMITTED_AWAITING_TERMINAL",
        "recovery_job_id": job_id,
        "capacity_status": R8U_CAPACITY_STATUS,
        "initial_state": initial_state,
        "new_qsub_submissions": 1,
        "login_node_polling_started": False,
        "continuation_submitted": False,
        "finalizer_submitted": False,
        "cloud_requests": 0,
        "download_reruns": 0,
    }


def _r8u_publish_fresh_extraction(
    *, run: sequential.FullRun, raw_validation: Mapping[str, Any]
) -> Mapping[str, Any]:
    implementation_commit = _current_r8u_implementation_commit()
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    fresh = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    canonical = paths["extraction"]
    if (
        not fresh.is_dir()
        or fresh.is_symlink()
        or os.path.lexists(canonical)
        or os.path.lexists(R8U_FRESH_PUBLICATION_PATH)
    ):
        _fail("R8U_FRESH_EXTRACTION_PUBLICATION_INVALID")
    try:
        before = os.lstat(fresh)
        canonical_parent = canonical.parent
        parent_info = os.lstat(canonical_parent)
        if (
            not stat.S_ISDIR(before.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or stat.S_ISLNK(parent_info.st_mode)
            or before.st_uid != os.geteuid()
            or parent_info.st_uid != os.geteuid()
            or before.st_dev != parent_info.st_dev
        ):
            _fail("R8U_FRESH_EXTRACTION_PUBLICATION_INVALID")
        _rename_directory_noreplace(fresh, canonical)
        after = os.lstat(canonical)
    except R8RControllerError:
        raise
    except OSError as exc:
        raise R8RControllerError("R8U_FRESH_EXTRACTION_PUBLICATION_FAILED") from exc
    if (
        not stat.S_ISDIR(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or os.path.lexists(fresh)
    ):
        _fail("R8U_FRESH_EXTRACTION_PUBLICATION_INVALID")
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_fresh_batch16_extraction_publication_v1",
        "status": "PASS_FRESH_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "failed_partial_seal_sha256": core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH),
        "raw_content_authority": dict(raw_validation),
        "stage_completion_receipt_sha256": core.sha256_file(
            canonical / "stage_completion_receipt.restricted.json"
        ),
        "fresh_stage_promoted_to_canonical": True,
        "canonical_target_absent_before_promotion": True,
        "failed_partial_modified": False,
        "partial_npz_adopted": 0,
        "cloud_requests": 0,
        "download_reruns": 0,
        "dicom_extraction_reruns": 1,
    }
    _write_private_json(R8U_FRESH_PUBLICATION_PATH, receipt)
    return receipt


def _r8u_recovery_terminal_receipt(
    *, run: sequential.FullRun, final_receipt: Mapping[str, Any]
) -> Mapping[str, Any]:
    implementation_commit = _current_r8u_implementation_commit()
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    required = {
        "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_PATH,
        "recovery_capacity_sha256": R8U_RECOVERY_CAPACITY_PATH,
        "recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_PATH,
        "recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_PATH,
        "fresh_extraction_publication_sha256": R8U_FRESH_PUBLICATION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"] / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            ATTEMPT_ROOT / "cache_retirement_authorizations"
            / f"{R8U_FIXED_BATCH_ID}.authorization.json"
        ),
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    hashes: dict[str, str] = {}
    for field, path in required.items():
        try:
            hashes[field] = core.sha256_file(path)
        except Exception as exc:
            raise R8RControllerError("R8U_RECOVERY_TERMINAL_AUTHORITY_INVALID") from exc
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_batch16_recovery_terminal_v1",
        "status": R8U_RECOVERY_STATUS,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        **hashes,
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "object_substitution_count": int(final_receipt["object_substitution_count"]),
        "unaccounted_multiframe_objects": int(
            final_receipt["unaccounted_multiframe_objects"]
        ),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "fresh_recovery_cache_retired": final_receipt["extracted_cache_retired"],
        "failed_partial_cache_retained": True,
        "batch16_raw_reused": True,
        "failed_partial_files": R8U_FAILED_PARTIAL_FILES,
        "failed_partial_bytes": R8U_FAILED_PARTIAL_BYTES,
        "cloud_requests": 0,
        "download_reruns": 0,
        "dicom_extraction_reruns": 1,
        "echoprime_reruns": 1,
        "embedding_generations": 1,
        "gpu_executions": 1,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def run_r8u_batch16_recovery(
    *, dependencies: sequential.FullDependencies | None = None
) -> Mapping[str, Any]:
    """Recover exactly Batch 16 from retained raw data, without download."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if JOB_RE.fullmatch(job_id) is None or task_text not in {"", "undefined"}:
        _fail("R8U_RECOVERY_SCHEDULER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u=True,
    )
    _validate_r8u_recovery_submission(current_job_id=job_id, wait=True)
    _validate_original_controls()
    _r8u_validate_attempt_content_authority()
    _r8u_validate_frozen_prefix(run, include_batch16=False)
    validate_r8u_failed_partial_seal()
    if os.path.lexists(R8U_RECOVERY_TERMINAL_PATH):
        _fail("R8U_RECOVERY_ALREADY_COMPLETE")
    cache_inventory = sequential._extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if cache_inventory.active != 1:
        _fail("R8U_RECOVERY_EXTRACTION_CACHE_TOPOLOGY_INVALID")
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    object_keys = {str(row["source_object_key"]) for row in planned["objects"]}
    dependency = sequential.resolve_dependencies(dependencies)
    try:
        dependency.environment_validator(
            run.authority.environment_receipt,
            expected_environment_receipt_sha256=(
                run.runtime_authority["environment_receipt_sha256"]
            ),
            scientific_governing_commit=run.authority.governing_commit,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        if core.sha256_file(run.authority.checkpoint) != run.runtime_authority["checkpoint_sha256"]:
            _fail("R8U_RECOVERY_CHECKPOINT_AUTHORITY_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_RECOVERY_PREBODY_AUTHORITY_FAILED", stage="PREBODY_AUTHORITY"
        ) from exc
    raw_validation = _r8u_validate_batch16_raw_bodies(
        run, digest_provider_factory=dependency.digest_provider_factory
    )
    try:
        _create_private_directory_no_clobber(
            R8U_FRESH_EXTRACTION_BATCH_ROOT.parent
        )
        _create_private_directory_no_clobber(R8U_FRESH_EXTRACTION_BATCH_ROOT)
        verified = paths["raw_batch"] / "verified_download_manifest.restricted.csv"
        dicom_summary = dependency.dicom(
            verified_download_manifest=verified,
            download_root=paths["raw_batch"] / "objects",
            batch_output_root=R8U_FRESH_EXTRACTION_BATCH_ROOT,
            workers=dependency.extraction_workers,
            batch_id=R8U_FIXED_BATCH_ID,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            planned_batch=planned,
            embedding_output_root=paths["echoprime"],
        )
        _r8u_publish_fresh_extraction(run=run, raw_validation=raw_validation)
        stages.advance_stage_ledger(
            input_ledger=paths["download_ledger"],
            output_ledger=paths["extraction_ledger"],
            receipt_root=paths["extraction"] / "transition_receipts",
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", core.sha256_file(paths["extraction"] / "dicom_audit.restricted.csv")),
                ("EXTRACTION_COMPLETE", core.sha256_file(paths["extraction"] / "extraction_manifest.restricted.csv")),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_BATCH16_EXTRACTION_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else "R8U_BATCH16_EXTRACTION_FAILED",
            stage="DICOM_EXTRACTION",
        ) from exc
    try:
        extraction_manifest = paths["extraction"] / "extraction_manifest.restricted.csv"
        stages.validate_extraction_manifest_plan_membership(
            extraction_manifest,
            planned,
            paths["extraction"] / "technical_disposition_manifest.restricted.csv",
        )
        embedding_summary = dependency.echoprime(
            extraction_manifest=extraction_manifest,
            extraction_root=paths["extraction"] / "clips",
            technical_disposition_manifest=(
                paths["extraction"] / "technical_disposition_manifest.restricted.csv"
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
            checkpoint=run.authority.checkpoint,
            environment_receipt=run.authority.environment_receipt,
            orchestration_contract=run.contract_path,
            batch_plan=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID,
            batch_output_root=paths["batch_root"],
            batch_size=dependency.echoprime_batch_size,
            seed=20260803,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            requirements=run.requirements,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        stages.advance_stage_ledger(
            input_ledger=paths["extraction_ledger"],
            output_ledger=paths["pooling_ledger"],
            receipt_root=paths["echoprime"] / "transition_receipts",
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("EMBEDDING_COMPLETE", core.sha256_file(paths["echoprime"] / "clip_manifest.restricted.csv")),
                ("STUDY_POOLING_COMPLETE", core.sha256_file(paths["echoprime"] / "study_manifest.restricted.csv")),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_BATCH16_ECHOPRIME_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else "R8U_BATCH16_ECHOPRIME_FAILED",
            stage="ECHOPRIME_EMBEDDING",
        ) from exc
    try:
        preserved = dependency.preserve(
            contract_path=run.contract_path,
            plan_path=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID,
            attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root,
            output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=run.scheduler_job_identity,
            input_ledger=paths["pooling_ledger"],
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=R8U_FIXED_BATCH_ID, paths=paths
        )
        dependency.retire(
            run=run,
            batch_id=R8U_FIXED_BATCH_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            test_only_synthetic_full_scope=False,
        )
        final_receipt = dependency.finalize_batch(
            run=run, batch_id=R8U_FIXED_BATCH_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_BATCH16_FINALIZATION_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else "R8U_BATCH16_FINALIZATION_FAILED",
            stage="PRESERVATION_RETIREMENT_FINALIZATION",
        ) from exc
    if (
        preserved.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or final_receipt.get("status") != "PASS_BATCH_FINALIZED"
        or final_receipt.get("raw_dicoms_retained") is not True
        or final_receipt.get("extracted_cache_retired") is not True
        or final_receipt.get("n_new_no_cine_studies") != 0
        or final_receipt.get("object_substitution_count") != 0
        or final_receipt.get("unaccounted_multiframe_objects") != 0
        or os.path.lexists(paths["extraction"] / "clips")
    ):
        _fail("R8U_BATCH16_FINALIZATION_INVALID")
    validate_r8u_failed_partial_seal()
    terminal = _r8u_recovery_terminal_receipt(run=run, final_receipt=final_receipt)
    _write_private_json(R8U_RECOVERY_TERMINAL_PATH, terminal)
    _r8u_validate_attempt_content_authority()
    return {
        **dict(terminal),
        "dicom_summary_status": dicom_summary.get("status"),
        "embedding_summary_status": embedding_summary.get("status"),
    }


def _r8u_validate_post_recovery_cache_topology(
    run: sequential.FullRun,
) -> Mapping[str, int]:
    """Require only the sealed failed Task-16 partial to remain active."""

    validate_r8u_failed_partial_seal()
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    if (
        os.path.lexists(paths["extraction"] / "clips")
        or os.path.lexists(R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
    ):
        _fail("R8U_POST_RECOVERY_CACHE_TOPOLOGY_INVALID")
    try:
        inventory = sequential._extraction_cache_inventory(
            run.production_root, current_attempt_id=run.attempt_id
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_POST_RECOVERY_CACHE_TOPOLOGY_INVALID"
        ) from exc
    if inventory.active != 1:
        _fail("R8U_POST_RECOVERY_CACHE_TOPOLOGY_INVALID")
    return {
        "active_extraction_caches": inventory.active,
        "preserved_terminal_failed_extraction_caches": (
            inventory.preserved_terminal_failed
        ),
    }


def validate_r8u_recovery_terminal() -> Mapping[str, Any]:
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_RECOVERY_TERMINAL_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u=True,
    )
    _validate_r8u_recovery_submission()
    _validate_original_controls()
    validate_r8u_failed_partial_seal()
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=R8U_FIXED_BATCH_ID
    )
    value, payload = _load_private_json(R8U_RECOVERY_TERMINAL_PATH)
    expected = _r8u_recovery_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    if (
        not _exact_typed_value_equal(value, expected)
        or _sha256_bytes(payload) != core.sha256_file(R8U_RECOVERY_TERMINAL_PATH)
        or os.path.lexists(paths["extraction"] / "clips")
        or os.path.lexists(R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
        or value.get("status") != R8U_RECOVERY_STATUS
    ):
        _fail("R8U_RECOVERY_TERMINAL_RECEIPT_INVALID")
    _r8u_validate_post_recovery_cache_topology(run)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return value


def _r8u_recovery_accounting_receipt(
    *, implementation_commit: str, recovery_job_id: str,
    accounting: Mapping[str, Any]
) -> Mapping[str, Any]:
    validated = _validate_recovery_accounting_projection(
        accounting, expected_job_id=recovery_job_id
    )
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_batch16_recovery_accounting_v1",
        "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "recovery_job_id": recovery_job_id,
        "failed": 0,
        "exit_status": 0,
        "accounting_projection": dict(validated),
    }


def _validate_r8u_recovery_accounting() -> Mapping[str, Any]:
    _, submission = _validate_r8u_recovery_submission()
    job_id = str(submission.get("recovery_job_id", ""))
    value, payload = _load_private_json(R8U_RECOVERY_ACCOUNTING_PATH)
    accounting = value.get("accounting_projection")
    if not isinstance(accounting, Mapping):
        _fail("R8U_RECOVERY_ACCOUNTING_INVALID")
    expected = _r8u_recovery_accounting_receipt(
        implementation_commit=_current_r8u_implementation_commit(),
        recovery_job_id=job_id,
        accounting=accounting,
    )
    if (
        not _exact_typed_value_equal(value, expected)
        or _sha256_bytes(payload) != core.sha256_file(R8U_RECOVERY_ACCOUNTING_PATH)
    ):
        _fail("R8U_RECOVERY_ACCOUNTING_INVALID")
    return value


def _r8u_continuation_claim(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    prefix_receipts: Sequence[str],
) -> Mapping[str, Any]:
    if (
        len(prefix_receipts) != 16
        or tuple(prefix_receipts[:15])
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or any(SHA_RE.fullmatch(value) is None for value in prefix_receipts)
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
    ):
        _fail("R8U_CONTINUATION_CLAIM_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_fixed_continuation_claim_v1",
        "status": "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "prior_implementation_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "failed_partial_seal_sha256": core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH),
        "recovery_capacity_sha256": core.sha256_file(R8U_RECOVERY_CAPACITY_PATH),
        "recovery_authority_sha256": core.sha256_file(R8U_RECOVERY_AUTHORITY_PATH),
        "recovery_submission_receipt_sha256": core.sha256_file(R8U_RECOVERY_SUBMISSION_PATH),
        "recovery_accounting_sha256": core.sha256_file(R8U_RECOVERY_ACCOUNTING_PATH),
        "recovery_terminal_receipt_sha256": core.sha256_file(R8U_RECOVERY_TERMINAL_PATH),
        "runtime_authority_sha256": core.canonical_json_sha256(run.runtime_authority),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1,
        "total_new_qsub_maximum": 3,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _r8u_continuation_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    array_job_id: str,
    finalizer_job_id: str,
    qsub_environment_sha256: str,
    continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        any(JOB_RE.fullmatch(value) is None for value in (recovery_job_id, array_job_id, finalizer_job_id))
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8U_CONTINUATION_SUBMISSION_INVALID")
    array_command = _r8u_continuation_array_command(implementation_commit)
    finalizer_command = _r8u_continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r2_fixed_continuation_submission_v1",
        "status": "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "recovery_job_id": recovery_job_id,
        "array_job_name": _r8u_continuation_array_job_name(implementation_commit),
        "finalizer_job_name": _r8u_continuation_finalizer_job_name(implementation_commit),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": array_command})),
        "finalizer_qsub_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": finalizer_command})),
        "qsub_environment_sha256": qsub_environment_sha256,
        "failed_partial_seal_sha256": core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH),
        "recovery_capacity_sha256": core.sha256_file(R8U_RECOVERY_CAPACITY_PATH),
        "recovery_authority_sha256": core.sha256_file(R8U_RECOVERY_AUTHORITY_PATH),
        "recovery_accounting_sha256": core.sha256_file(R8U_RECOVERY_ACCOUNTING_PATH),
        "recovery_terminal_receipt_sha256": core.sha256_file(R8U_RECOVERY_TERMINAL_PATH),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_CONTINUATION_SCHEDULER_ROOT, "array")
        ),
        "finalizer_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_CONTINUATION_SCHEDULER_ROOT, "finalizer")
        ),
        "scheduler_submission_count": 2,
        "total_new_qsub_submissions": 3,
        "scheduler_submission_maximum": 3,
        "array_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def _validate_r8u_continuation_chain(
    run: sequential.FullRun,
    *, current_job_id: str | None, role: str, wait: bool
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if role not in {"array", "finalizer"}:
        _fail("R8U_CONTINUATION_ROLE_INVALID")
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(R8U_CONTINUATION_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8U_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    terminal = validate_r8u_recovery_terminal()
    accounting = _validate_r8u_recovery_accounting()
    implementation_commit = _current_r8u_implementation_commit()
    capacity_value, _ = _load_private_json(R8U_RECOVERY_CAPACITY_PATH)
    try:
        capacity.validate_fixed_r8u_batch16_recovery_capacity(
            run.plan,
            capacity_value,
            r8u_scheduler_log_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_RECOVERY_CAPACITY_INVALID") from exc
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    claim, claim_payload = _load_private_json(R8U_CONTINUATION_CLAIM_PATH)
    submission, _ = _load_private_json(R8U_CONTINUATION_SUBMISSION_PATH)
    qsub_sha = str(claim.get("qsub_environment_sha256", ""))
    expected_claim = _r8u_continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=qsub_sha,
        prefix_receipts=prefix,
    )
    if not _exact_typed_value_equal(claim, expected_claim):
        _fail("R8U_CONTINUATION_CLAIM_INVALID")
    _, recovery_submission = _validate_r8u_recovery_submission()
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    array_job_id = str(submission.get("array_job_id", ""))
    finalizer_job_id = str(submission.get("finalizer_job_id", ""))
    expected_submission = _r8u_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=qsub_sha,
        continuation_claim_sha256=_sha256_bytes(claim_payload),
    )
    if not _exact_typed_value_equal(submission, expected_submission):
        _fail("R8U_CONTINUATION_SUBMISSION_RECEIPT_INVALID")
    expected_job_id = array_job_id if role == "array" else finalizer_job_id
    if current_job_id is not None and current_job_id != expected_job_id:
        _fail("R8U_CONTINUATION_JOB_ID_MISMATCH")
    if (
        terminal.get("status") != R8U_RECOVERY_STATUS
        or accounting.get("failed") != 0
        or accounting.get("exit_status") != 0
    ):
        _fail("R8U_RECOVERY_TERMINAL_AUTHORITY_INVALID")
    return claim, submission


def validate_r8u_continuation_worker_submission(
    run: sequential.FullRun,
    *, current_job_id: str, role: str
) -> Mapping[str, Any]:
    return _validate_r8u_continuation_chain(
        run, current_job_id=current_job_id, role=role, wait=True
    )[1]


def _validate_r8u_initial_scheduler_topology(
    *,
    environment: Mapping[str, str],
    array_job_id: str,
    finalizer_job_id: str,
    implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_INITIAL_QSTAT_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8U_INITIAL_QSTAT_INVALID") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local(element) for element in list(root)]
    if (
        local(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(name not in {"queue_info", "job_info"} for name in direct_children)
    ):
        _fail("R8U_INITIAL_QSTAT_INVALID")
    records: list[dict[str, list[str]]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        record: dict[str, list[str]] = {}
        if job.get("state"):
            record["__attribute_state__"] = [str(job.get("state"))]
        for child in list(job):
            record.setdefault(local(child), []).append(child.text or "")
        records.append(record)
    array_records = [
        row for row in records if array_job_id in row.get("JB_job_number", [])
    ]
    final_records = [
        row for row in records if finalizer_job_id in row.get("JB_job_number", [])
    ]
    if (
        not array_records
        or not final_records
        or any(
            _r8u_continuation_array_job_name(implementation_commit)
            not in row.get("JB_name", [])
            for row in array_records
        )
        or any(
            _r8u_continuation_finalizer_job_name(implementation_commit)
            not in row.get("JB_name", [])
            for row in final_records
        )
    ):
        _fail("R8U_INITIAL_QSTAT_TOPOLOGY_INVALID")
    def scheduler_states(row: Mapping[str, Sequence[str]]) -> set[str]:
        child = set(row.get("state", ()))
        attribute = set(row.get("__attribute_state__", ()))
        if len(child) != 1 or len(attribute) != 1:
            _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
        child_state = next(iter(child))
        expected_category = (
            "running" if child_state in {"r", "Rr", "t"} else "pending"
        )
        if attribute != {expected_category}:
            _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
        return child

    array_states = {value for row in array_records for value in scheduler_states(row)}
    final_states = {value for row in final_records for value in scheduler_states(row)}
    if (
        not array_states
        or any(value not in {"r", "qw", "t", "Rr"} for value in array_states)
        or not final_states
        or final_states != {"hqw"}
    ):
        _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
    covered: set[int] = set()
    task_states: dict[int, set[str]] = {
        task_id: set() for task_id in R8U_FIXED_CONTINUATION_TASK_IDS
    }
    for row in array_records:
        row_tasks: set[int] = set()
        task_fields = (
            *row.get("JAT_task_number", ()),
            *row.get("ja_task_id", ()),
            *row.get("tasks", ()),
        )
        if not task_fields:
            _fail("R8U_INITIAL_QSTAT_TASK_RANGE_INVALID")
        for text_value in task_fields:
            for token in text_value.split(","):
                match = re.fullmatch(
                    r"([0-9]{1,2})(?:-([0-9]{1,2})(?::([0-9]{1,2}))?)?",
                    token,
                )
                if match is None:
                    _fail("R8U_INITIAL_QSTAT_TASK_RANGE_INVALID")
                first = int(match.group(1))
                last = int(match.group(2) or first)
                step = int(match.group(3) or 1)
                if (
                    first not in R8U_FIXED_CONTINUATION_TASK_IDS
                    or last not in R8U_FIXED_CONTINUATION_TASK_IDS
                    or first > last
                    or step != 1
                ):
                    _fail("R8U_INITIAL_QSTAT_TASK_RANGE_INVALID")
                row_tasks.update(range(first, last + 1, step))
        row_states = scheduler_states(row)
        if len(row_states) != 1:
            _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
        covered.update(row_tasks)
        for task_id in row_tasks:
            if task_id in task_states:
                task_states[task_id].update(row_states)
    if covered != set(R8U_FIXED_CONTINUATION_TASK_IDS):
        _fail("R8U_INITIAL_QSTAT_TASK_RANGE_INVALID")
    if (
        not task_states[17]
        or any(value not in {"r", "qw", "t", "Rr"} for value in task_states[17])
        or task_states[18] != {"qw"}
        or task_states[19] != {"qw"}
    ):
        _fail("R8U_INITIAL_QSTAT_STATE_INVALID")
    return {
        "status": "PASS_INITIAL_R8U_SCHEDULER_TOPOLOGY",
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "task17_state": "RUNNING_OR_QUEUED",
        "tasks18_19_state": "QUEUED",
        "finalizer_state": "HELD",
    }


def submit_r8u_continuation_17_19(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """After successful Batch 16, submit one 17--19 array and held finalizer."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    _validate_r8u_no_active_processes(environment, runner=process_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_CONTINUATION_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
        r8u=True,
    )
    _r8u_failed_recovery_epoch_authority()
    _r8u_scheduler_evidence_projection()
    _r8u_validate_pristine_continuation_exclusions()
    validate_r8u_recovery_terminal()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    if os.path.lexists(R8U_RECOVERY_ACCOUNTING_PATH):
        _fail("R8U_RECOVERY_ACCOUNTING_COLLISION")
    if os.path.lexists(R8U_CONTINUATION_ROOT):
        _fail("R8U_CONTINUATION_ROOT_COLLISION")
    _, recovery_submission = _validate_r8u_recovery_submission()
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    accounting_projection = _query_recovery_accounting(
        recovery_job_id=recovery_job_id,
        environment=environment,
        runner=qacct_runner,
    )
    accounting_receipt = _r8u_recovery_accounting_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        accounting=accounting_projection,
    )
    _write_private_json(R8U_RECOVERY_ACCOUNTING_PATH, accounting_receipt)
    capacity_value, _ = _load_private_json(R8U_RECOVERY_CAPACITY_PATH)
    try:
        capacity.validate_fixed_r8u_batch16_recovery_capacity(
            run.plan,
            capacity_value,
            r8u_scheduler_log_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_RECOVERY_CAPACITY_INVALID") from exc
    if capacity_value.get("status") != R8U_CAPACITY_STATUS:
        _fail("R8U_RECOVERY_CAPACITY_BLOCKED")
    _create_private_directory_no_clobber(R8U_CONTINUATION_ROOT)
    _create_private_directory_no_clobber(R8U_CONTINUATION_SCHEDULER_ROOT)
    claim = _r8u_continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix,
    )
    claim_sha = _write_private_json(R8U_CONTINUATION_CLAIM_PATH, claim)
    _r8u_validate_pristine_continuation_task_outputs()
    array_job_id = scheduler._capture_qsub(
        "array",
        _r8u_continuation_array_command(implementation_commit),
        root=R8U_CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
        parser=_parse_r8u_array_qsub_stdout,
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer",
        _r8u_continuation_finalizer_command(implementation_commit, array_job_id),
        root=R8U_CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _r8u_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha,
        continuation_claim_sha256=claim_sha,
    )
    initial = _validate_r8u_initial_scheduler_topology(
        environment=environment,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        implementation_commit=implementation_commit,
        runner=qstat_runner,
    )
    _write_private_json(R8U_CONTINUATION_SUBMISSION_PATH, receipt)
    _validate_r8u_continuation_chain(
        run, current_job_id=None, role="array", wait=False
    )
    return {
        "status": "R8U_CONTINUATION_17_19_SUBMITTED",
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "new_qsub_submissions": 2,
        "total_new_qsub_submissions": 3,
        "initial_scheduler_topology": initial,
        "cloud_requests": 0,
    }


def run_r8u_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or not task_text.isdigit()
        or int(task_text) not in R8U_FIXED_CONTINUATION_TASK_IDS
    ):
        _fail("R8U_CONTINUATION_ARRAY_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u=True,
    )
    _validate_r8u_continuation_chain(
        run, current_job_id=job_id, role="array", wait=True
    )
    _r8u_validate_attempt_content_authority()
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8U_FIXED_CONTINUATION
    )
    result = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if result.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8U_CONTINUATION_BATCH_NOT_FINALIZED")
    _r8u_validate_attempt_content_authority()
    return result


def run_r8u_continuation_finalizer() -> Mapping[str, Any]:
    """Finalize the fixed three-epoch receipt set after Tasks 17--19."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8U_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u=True,
    )
    implementation_commit = _current_r8u_implementation_commit()
    _validate_r8u_continuation_chain(
        run, current_job_id=job_id, role="finalizer", wait=True
    )
    _r8u_validate_attempt_content_authority()
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    historical = _r8u_historical_r8r_chain_authority()
    authority = finalizer.R8UImplementationAuthority(
        implementation_commit=implementation_commit,
        historical_r8r_recovery_authority_sha256=(
            historical["recovery_authority_sha256"]
        ),
        historical_r8r_recovery_terminal_receipt_sha256=(
            historical["recovery_terminal_receipt_sha256"]
        ),
        historical_r8r_continuation_capacity_receipt_sha256=(
            historical["continuation_capacity_receipt_sha256"]
        ),
        historical_r8r_continuation_claim_sha256=(
            historical["continuation_claim_sha256"]
        ),
        historical_r8r_continuation_submission_receipt_sha256=(
            historical["continuation_submission_receipt_sha256"]
        ),
        failed_partial_seal_sha256=core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        recovery_capacity_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_CAPACITY_PATH
        ),
        recovery_authority_sha256=core.sha256_file(
            R8U_RECOVERY_AUTHORITY_PATH
        ),
        recovery_submission_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_SUBMISSION_PATH
        ),
        recovery_accounting_sha256=core.sha256_file(
            R8U_RECOVERY_ACCOUNTING_PATH
        ),
        recovery_terminal_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_TERMINAL_PATH
        ),
        continuation_claim_sha256=core.sha256_file(
            R8U_CONTINUATION_CLAIM_PATH
        ),
        continuation_submission_receipt_sha256=core.sha256_file(
            R8U_CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan,
        requirements=run.requirements,
        production_root=run.production_root,
        contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8u_implementation_authority=authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("selected_subjects") != 4_530
        or summary.get("verified_source_objects") != 335_984
        or summary.get("selected_source_bytes") != 1_216_569_133_322
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("new_no_cine_studies") != 0
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical") is not True
        or summary.get("implementation_authority_epoch_count") != 3
        or summary.get("r8u_implementation_commit") != implementation_commit
        or SHA_RE.fullmatch(
            str(summary.get("r8u_recovery_continuation_authority_sha256"))
        )
        is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8U_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    _r8u_validate_attempt_content_authority()
    return summary


def _parse_r8r_array_qsub_stdout(payload: bytes) -> str:
    match = re.fullmatch(rb"([1-9][0-9]{0,19})(?:\.4-19:1)?\n?", payload)
    if match is None:
        _fail("R8R_ARRAY_QSUB_OUTPUT_INVALID")
    return match.group(1).decode("ascii")


def _qsub_evidence_authority(root: Path, label: str) -> Mapping[str, Any]:
    evidence: dict[str, bytes] = {}
    for kind in ("stdout", "stderr", "exit_status"):
        try:
            evidence[kind] = scheduler._read_scheduler_evidence(
                root / f"{label}.qsub.{kind}.restricted"
            )
        except Exception as exc:
            raise R8RControllerError("R8R_QSUB_EVIDENCE_INVALID") from exc
    if evidence["exit_status"] != b"0\n" or evidence["stderr"] != b"":
        _fail("R8R_QSUB_EVIDENCE_INVALID")
    return {
        "stdout_bytes": len(evidence["stdout"]),
        "stdout_sha256": _sha256_bytes(evidence["stdout"]),
        "stderr_bytes": 0,
        "stderr_sha256": _sha256_bytes(evidence["stderr"]),
        "exit_status": 0,
    }


def _validate_recovery_accounting_projection(
    value: Mapping[str, Any], *, expected_job_id: str
) -> Mapping[str, Any]:
    keys = {
        "status",
        "job_id",
        "task_id",
        "failed",
        "exit_status",
        "start_time",
        "end_time",
        "ru_wallclock_seconds",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != keys
        or value.get("status") != "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0"
        or value.get("job_id") != expected_job_id
        or value.get("task_id") not in {"NONE", "undefined"}
        or type(value.get("failed")) is not int
        or value.get("failed") != 0
        or type(value.get("exit_status")) is not int
        or value.get("exit_status") != 0
        or not isinstance(value.get("start_time"), str)
        or not isinstance(value.get("end_time"), str)
        or not isinstance(value.get("ru_wallclock_seconds"), str)
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:[.][0-9]+)?",
            str(value.get("ru_wallclock_seconds")),
        )
        is None
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    try:
        start = datetime.strptime(
            str(value["start_time"]), "%a %b %d %H:%M:%S %Y"
        )
        end = datetime.strptime(
            str(value["end_time"]), "%a %b %d %H:%M:%S %Y"
        )
    except ValueError as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    if end < start:
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    return dict(value)


def _validate_qacct_tool() -> None:
    expected = scheduler.CANONICAL_SGE_ROOT / "bin/linux-x64/qacct"
    if QACCT_PATH != expected:
        _fail("R8R_RECOVERY_ACCOUNTING_TOOL_INVALID")
    try:
        scheduler._require_nonsymlink_components(
            QACCT_PATH, "R8R_RECOVERY_ACCOUNTING_TOOL_INVALID"
        )
        info = os.lstat(QACCT_PATH)
    except Exception as exc:
        raise R8RControllerError(
            "R8R_RECOVERY_ACCOUNTING_TOOL_INVALID"
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not stat.S_IMODE(info.st_mode) & stat.S_IXUSR
        or not os.access(QACCT_PATH, os.X_OK)
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_TOOL_INVALID")


def _query_recovery_accounting(
    *,
    recovery_job_id: str,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(recovery_job_id) is None:
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    _validate_qacct_tool()
    completed = runner(
        [str(QACCT_PATH), "-j", recovery_job_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_UNAVAILABLE")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    try:
        decoded = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    for raw_line in decoded.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if set(line) == {"="}:
            if current:
                records.append(current)
                current = {}
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] in current:
            _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
        current[parts[0]] = parts[1].strip()
    if current:
        records.append(current)
    if (
        len(records) != 1
        or records[0].get("jobnumber") != recovery_job_id
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_UNAVAILABLE")
    record = records[0]
    required = {
        "jobnumber",
        "taskid",
        "failed",
        "exit_status",
        "start_time",
        "end_time",
        "ru_wallclock",
    }
    if not required.issubset(record):
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    try:
        projection = {
            "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
            "job_id": recovery_job_id,
            "task_id": (
                "NONE" if record["taskid"] in {"", "NONE"} else record["taskid"]
            ),
            "failed": int(record["failed"]),
            "exit_status": int(record["exit_status"]),
            "start_time": record["start_time"],
            "end_time": record["end_time"],
            "ru_wallclock_seconds": record["ru_wallclock"],
        }
    except (TypeError, ValueError) as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    return _validate_recovery_accounting_projection(
        projection, expected_job_id=recovery_job_id
    )


def _build_recovery_authority(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    retained_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        run.attempt_id != ORIGINAL_ATTEMPT_ID
        or run.plan_sha256 != ORIGINAL_PLAN_SHA256
        or run.authority.governing_commit != ORIGINAL_SCIENTIFIC_COMMIT
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_authority_v1",
        "status": "AUTHORIZED_FIXED_BATCH3_PRESERVATION_RECOVERY",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        "original_control_authority": _validate_original_controls(),
        "prefix_final_receipt_sha256": [
            item[2] for item in PREFIX_RECEIPT_AUTHORITIES
        ],
        "batch3_retained_authority": dict(retained_authority),
        "script_authority": _script_authority(),
        "runtime_validation_context": (
            stages.SEALED_SCHEDULER_RUNTIME_REPLAY.value
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "fixed_recovery_task": FIXED_RECOVERY_TASK_ID,
        "cloud_requests_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "dicom_extraction_reruns_authorized": 0,
        "echoprime_reruns_authorized": 0,
        "embedding_generations_authorized": 0,
        "gpu_executions_authorized": 0,
        "raw_dicom_deletion_authorized": False,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _validate_recovery_authority(
    run: sequential.FullRun,
    value: Mapping[str, Any],
    *,
    require_retained_current: bool,
) -> None:
    implementation_commit = _current_implementation_commit()
    bound_qsub_environment_sha256 = value.get("qsub_environment_sha256")
    if (
        not isinstance(bound_qsub_environment_sha256, str)
        or SHA_RE.fullmatch(bound_qsub_environment_sha256) is None
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    retained = value.get("batch3_retained_authority")
    if not isinstance(retained, Mapping):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    if require_retained_current and not _exact_typed_value_equal(
        retained, _batch3_retained_authority()
    ):
        _fail("R8R_RECOVERY_RETAINED_AUTHORITY_CHANGED")
    expected = _build_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=bound_qsub_environment_sha256,
        retained_authority=retained,
    )
    if not _exact_typed_value_equal(value, expected):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")


def _build_recovery_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    qsub_environment_sha256: str,
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(recovery_job_id) is None:
        _fail("R8R_JOB_ID_INVALID")
    try:
        if scheduler.parse_numeric_qsub_stdout(
            scheduler._read_scheduler_evidence(
                RECOVERY_SCHEDULER_ROOT
                / "recovery.qsub.stdout.restricted"
            )
        ) != recovery_job_id:
            _fail("R8R_QSUB_EVIDENCE_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8R_QSUB_EVIDENCE_INVALID") from exc
    command = _recovery_qsub_command(implementation_commit)
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_submission_v1",
        "status": "PASS_EXACT_ONE_CPU_RECOVERY_QSUB",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        "recovery_job_name": _recovery_job_name(implementation_commit),
        "recovery_job_id": recovery_job_id,
        "recovery_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "recovery_qsub_evidence": dict(
            _qsub_evidence_authority(RECOVERY_SCHEDULER_ROOT, "recovery")
        ),
        "scheduler_submission_count": 1,
        "recovery_is_array": False,
        "gpu_requested": False,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def _validate_recovery_submission(
    *,
    current_job_id: str | None = None,
    wait: bool = False,
    require_retained_current: bool = True,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(RECOVERY_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8R_RECOVERY_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    authority, authority_payload = _load_private_json(RECOVERY_AUTHORITY_PATH)
    receipt, _ = _load_private_json(RECOVERY_SUBMISSION_PATH)
    run = _load_fixed_original_run(
        scheduler_job_identity=(current_job_id or "R8R_RECOVERY_VALIDATION"),
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_recovery_authority(
        run,
        authority,
        require_retained_current=require_retained_current,
    )
    implementation_commit = _current_implementation_commit()
    qsub_environment_sha256 = str(authority.get("qsub_environment_sha256", ""))
    job_id = str(receipt.get("recovery_job_id", ""))
    expected = _build_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=job_id,
        qsub_environment_sha256=qsub_environment_sha256,
    )
    if not _exact_typed_value_equal(receipt, expected):
        _fail("R8R_RECOVERY_SUBMISSION_RECEIPT_INVALID")
    if current_job_id is not None and current_job_id != job_id:
        _fail("R8R_RECOVERY_JOB_ID_MISMATCH")
    if _sha256_bytes(authority_payload) != core.sha256_file(
        RECOVERY_AUTHORITY_PATH
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    return authority, receipt


def submit_batch3_recovery(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    scheduler.validate_scheduler_tools()
    implementation_commit = _current_implementation_commit()
    if implementation_commit == ORIGINAL_SCIENTIFIC_COMMIT:
        _fail("R8R_REPAIR_IMPLEMENTATION_COMMIT_REQUIRED")
    environment, _ = scheduler.build_qsub_environment()
    environment_sha256 = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_RECOVERY_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
    )
    _validate_original_controls()
    _validate_frozen_prefix(run, include_batch3=False)
    _require_batch3_recovery_absent(run)
    retained = _batch3_retained_authority()
    if os.path.lexists(RECOVERY_ROOT):
        _fail("R8R_RECOVERY_ROOT_COLLISION")
    _create_private_directory_no_clobber(RECOVERY_ROOT)
    _create_private_directory_no_clobber(RECOVERY_SCHEDULER_ROOT)
    authority = _build_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha256,
        retained_authority=retained,
    )
    _write_private_json(RECOVERY_AUTHORITY_PATH, authority)
    command = _recovery_qsub_command(implementation_commit)
    recovery_job_id = scheduler._capture_qsub(
        "recovery",
        command,
        root=RECOVERY_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _build_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        qsub_environment_sha256=environment_sha256,
    )
    _write_private_json(RECOVERY_SUBMISSION_PATH, receipt)
    _validate_recovery_submission(
        wait=False,
        require_retained_current=False,
    )
    return {
        "status": "RECOVERY_SUBMITTED",
        "recovery_job_id": recovery_job_id,
        "new_qsub_submissions": 1,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "embedding_generations": 0,
    }


def _raw_batch3_post_authority() -> Mapping[str, Any]:
    root = ATTEMPT_ROOT / "raw" / FIXED_BATCH3_ID / "objects"
    rows: list[list[Any]] = []
    count = 0
    total = 0
    try:
        entries = sorted(os.scandir(root), key=lambda item: item.name)
    except OSError as exc:
        raise R8RControllerError("R8R_BATCH3_RAW_RETENTION_INVALID") from exc
    for entry in entries:
        if (
            entry.is_symlink()
            or not entry.is_file(follow_symlinks=False)
            or not entry.name.endswith(".dcm")
        ):
            _fail("R8R_BATCH3_RAW_RETENTION_INVALID")
        path = Path(entry.path)
        row = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
        rows.append(row)
        count += 1
        total += int(row[6])
    if count != 18_653 or total != 68_725_707_170:
        _fail("R8R_BATCH3_RAW_RETENTION_INVALID")
    payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n"
        for row in rows
    )
    return {
        "raw_dicom_files": count,
        "raw_dicom_bytes": total,
        "raw_metadata_projection_sha256": _sha256_bytes(payload),
        "raw_dicom_body_reads": 0,
    }


def _recovery_terminal_receipt(
    *,
    run: sequential.FullRun,
    final_receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    paths = sequential._batch_paths(run, FIXED_BATCH3_ID)
    authorization = (
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{FIXED_BATCH3_ID}.authorization.json"
    )
    required = {
        "recovery_authority_sha256": RECOVERY_AUTHORITY_PATH,
        "recovery_submission_receipt_sha256": RECOVERY_SUBMISSION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"]
            / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": authorization,
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    hashes: dict[str, str] = {}
    for key, path in required.items():
        try:
            hashes[key] = core.sha256_file(path)
        except Exception as exc:
            raise R8RControllerError("R8R_RECOVERY_TERMINAL_AUTHORITY_INVALID") from exc
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_terminal_v1",
        "status": RECOVERY_STATUS,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": _current_implementation_commit(),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        **hashes,
        "raw_retention_authority": dict(_raw_batch3_post_authority()),
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "extracted_cache_retired": final_receipt["extracted_cache_retired"],
        "download_reruns": 0,
        "dicom_extraction_reruns": 0,
        "echoprime_reruns": 0,
        "embedding_generations": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def recover_batch3_preservation() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_id = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_id not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8R_RECOVERY_SCHEDULER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_recovery_submission(current_job_id=job_id, wait=True)
    _validate_original_controls()
    _validate_frozen_prefix(run, include_batch3=False)
    if os.path.lexists(RECOVERY_TERMINAL_PATH):
        _fail("R8R_RECOVERY_ALREADY_COMPLETE")
    paths = sequential._batch_paths(run, FIXED_BATCH3_ID)
    try:
        preservation_receipt = preservation.preserve_batch(
            contract_path=run.contract_path,
            plan_path=run.plan_path,
            batch_id=FIXED_BATCH3_ID,
            attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root,
            output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=job_id,
            input_ledger=paths["pooling_ledger"],
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            runtime_validation_context=(
                stages.SEALED_SCHEDULER_RUNTIME_REPLAY
            ),
            artifact_validation_context=(
                preservation.R8R_FIXED_BATCH3_NO_DICOM_BODY
            ),
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8R_BATCH3_PRESERVATION_FAILED")
        raw_substage = getattr(exc, "validation_substage", None)
        validation_substage = (
            raw_substage.value
            if isinstance(
                raw_substage,
                preservation.PreservationValidationSubstage,
            )
            else None
        )
        raise R8RControllerError(
            code if isinstance(code, str) else "R8R_BATCH3_PRESERVATION_FAILED",
            stage="BATCH_PRESERVATION",
            validation_substage=validation_substage,
        ) from exc
    try:
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=FIXED_BATCH3_ID, paths=paths
        )
        sequential._execute_cache_retirement(
            run=run,
            batch_id=FIXED_BATCH3_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            test_only_synthetic_full_scope=False,
            artifact_validation_context=(
                retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
            ),
            scheduler_runner_path=RUNNER_PATH,
        )
        final_receipt = sequential._validate_batch_finalization(
            run=run, batch_id=FIXED_BATCH3_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8R_BATCH3_FINALIZATION_FAILED")
        raise R8RControllerError(
            code if isinstance(code, str) else "R8R_BATCH3_FINALIZATION_FAILED",
            stage="BATCH3_CACHE_RETIREMENT_AND_FINALIZATION",
        ) from exc
    if preservation_receipt.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        _fail("R8R_BATCH3_PRESERVATION_FAILED")
    terminal = _recovery_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    _write_private_json(RECOVERY_TERMINAL_PATH, terminal)
    return terminal


def validate_recovery_terminal() -> Mapping[str, Any]:
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_RECOVERY_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_original_controls()
    _validate_recovery_submission(require_retained_current=False)
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=FIXED_BATCH3_ID
    )
    value, payload = _load_private_json(RECOVERY_TERMINAL_PATH)
    expected = _recovery_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    if not _exact_typed_value_equal(
        value, expected
    ) or _sha256_bytes(payload) != core.sha256_file(
        RECOVERY_TERMINAL_PATH
    ):
        _fail("R8R_RECOVERY_TERMINAL_RECEIPT_INVALID")
    cache = sequential._batch_paths(run, FIXED_BATCH3_ID)["extraction"] / "clips"
    if os.path.lexists(cache):
        _fail("R8R_RECOVERY_CACHE_RETIREMENT_INVALID")
    _validate_frozen_prefix(run, include_batch3=True)
    return value


def _continuation_capacity_receipt(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    observation: Mapping[str, Any],
    prefix_receipts: Sequence[str],
    recovery_terminal_sha256: str,
    recovery_job_id: str,
    recovery_scheduler_accounting: Mapping[str, Any],
    captured_at_utc: str,
) -> Mapping[str, Any]:
    try:
        validated_observation = (
            capacity.validate_fixed_r8r_continuation_capacity(
                run.plan, observation
            )
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8R_CONTINUATION_CAPACITY_INVALID"
        ) from exc
    accounting = _validate_recovery_accounting_projection(
        recovery_scheduler_accounting,
        expected_job_id=recovery_job_id,
    )
    if (
        validated_observation.get("status") != CONTINUATION_CAPACITY_STATUS
        or set(validated_observation) != capacity.R8R_CONTINUATION_CAPACITY_KEYS
        or validated_observation.get("original_attempt_id") != ORIGINAL_ATTEMPT_ID
        or validated_observation.get("original_plan_sha256") != ORIGINAL_PLAN_SHA256
        or validated_observation.get("original_scientific_governing_commit")
        != ORIGINAL_SCIENTIFIC_COMMIT
        or validated_observation.get("continuation_first_task") != 4
        or validated_observation.get("continuation_last_task") != 19
        or validated_observation.get("continuation_task_count") != 16
        or validated_observation.get("native_capacity_snapshot_captures") != 1
        or validated_observation.get("native_quota_file_captures") != 1
        or validated_observation.get("capacity_command_captures") != 5
        or validated_observation.get("quota_reserve_gate_passed") is not True
        or validated_observation.get("physical_reserve_gate_passed") is not True
        or validated_observation.get("file_slot_gate_passed") is not True
        or any(
            validated_observation.get(key) != 0
            for key in capacity.R8R_CONTINUATION_ZERO_EFFECT_KEYS
        )
        or tuple(prefix_receipts[:2])
        != tuple(item[2] for item in PREFIX_RECEIPT_AUTHORITIES)
        or len(prefix_receipts) != 3
        or any(SHA_RE.fullmatch(item) is None for item in prefix_receipts)
        or SHA_RE.fullmatch(recovery_terminal_sha256) is None
        or re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            captured_at_utc,
        )
        is None
    ):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_capacity_v1",
        "status": CONTINUATION_CAPACITY_STATUS,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "captured_at_utc": captured_at_utc,
        "continuation_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "recovery_terminal_receipt_sha256": recovery_terminal_sha256,
        "recovery_scheduler_accounting": dict(accounting),
        "capacity_observation": dict(validated_observation),
        "active_extraction_caches": 0,
        "continuation_root_absent_at_capture": True,
        "continuation_claim_absent_at_capture": True,
        "continuation_submission_receipt_absent_at_capture": True,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "scheduler_submissions": 0,
        "gpu_executions": 0,
        "embedding_generations": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def _continuation_capacity_summary(
    receipt: Mapping[str, Any], receipt_sha256: str
) -> Mapping[str, Any]:
    observation = receipt.get("capacity_observation")
    if not isinstance(observation, Mapping):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    keys = (
        "remaining_batch_count",
        "remaining_studies",
        "remaining_objects",
        "remaining_source_bytes",
        "continuation_increment_bytes",
        "required_file_slots",
        "research_quota_bytes",
        "research_usage_bytes",
        "research_filesystem_available_bytes",
        "research_file_slots_remaining",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
    )
    projected = {key: observation.get(key) for key in keys}
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_capacity_aggregate_safe_v1",
        "status": CONTINUATION_CAPACITY_STATUS,
        **projected,
        "capacity_receipt_sha256": receipt_sha256,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
        "cloud_requests": 0,
        "scheduler_submissions": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
    }


def _continuation_claim(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    prefix_receipts: Sequence[str],
    recovery_terminal_sha256: str,
    capacity_receipt_sha256: str,
    recovery_job_id: str,
    recovery_accounting_sha256: str,
) -> Mapping[str, Any]:
    if (
        SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(recovery_terminal_sha256) is None
        or SHA_RE.fullmatch(capacity_receipt_sha256) is None
        or JOB_RE.fullmatch(recovery_job_id) is None
        or SHA_RE.fullmatch(recovery_accounting_sha256) is None
    ):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_claim_v1",
        "status": "AUTHORIZED_FIXED_CONTINUATION_4_19",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "original_claim_sha256": ORIGINAL_CLAIM_SHA256,
        "original_submission_receipt_sha256": ORIGINAL_SUBMISSION_SHA256,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "recovery_terminal_receipt_sha256": recovery_terminal_sha256,
        "continuation_capacity_receipt_sha256": capacity_receipt_sha256,
        "recovery_job_id": recovery_job_id,
        "recovery_scheduler_accounting_sha256": (
            recovery_accounting_sha256
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "third_continuation_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _build_continuation_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    array_job_id: str,
    finalizer_job_id: str,
    qsub_environment_sha256: str,
    continuation_capacity_receipt_sha256: str,
    continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        JOB_RE.fullmatch(recovery_job_id) is None
        or JOB_RE.fullmatch(array_job_id) is None
        or JOB_RE.fullmatch(finalizer_job_id) is None
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_capacity_receipt_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    try:
        if (
            _parse_r8r_array_qsub_stdout(
                scheduler._read_scheduler_evidence(
                    CONTINUATION_SCHEDULER_ROOT
                    / "array.qsub.stdout.restricted"
                )
            )
            != array_job_id
            or scheduler.parse_numeric_qsub_stdout(
                scheduler._read_scheduler_evidence(
                    CONTINUATION_SCHEDULER_ROOT
                    / "finalizer.qsub.stdout.restricted"
                )
            )
            != finalizer_job_id
        ):
            _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8R_CONTINUATION_SUBMISSION_INVALID") from exc
    array_command = _continuation_array_command(implementation_commit)
    finalizer_command = _continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_submission_v1",
        "status": "PASS_EXACT_ARRAY_4_19_AND_HELD_FINALIZER",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "array_job_name": _continuation_array_job_name(implementation_commit),
        "finalizer_job_name": _continuation_finalizer_job_name(
            implementation_commit
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": finalizer_command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "continuation_capacity_receipt_sha256": (
            continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(
            _qsub_evidence_authority(CONTINUATION_SCHEDULER_ROOT, "array")
        ),
        "finalizer_qsub_evidence": dict(
            _qsub_evidence_authority(
                CONTINUATION_SCHEDULER_ROOT, "finalizer"
            )
        ),
        "scheduler_submission_count": 2,
        "scheduler_submission_maximum": 2,
        "array_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "third_continuation_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def submit_continuation(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    scheduler.validate_scheduler_tools()
    implementation_commit = _current_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha256 = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_CONTINUATION_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
    )
    _validate_original_controls()
    terminal = validate_recovery_terminal()
    _, recovery_submission = _validate_recovery_submission(
        require_retained_current=False
    )
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    recovery_accounting = _query_recovery_accounting(
        recovery_job_id=recovery_job_id,
        environment=environment,
        runner=qacct_runner,
    )
    prefix_receipts = _validate_frozen_prefix(run, include_batch3=True)
    recovery_terminal_sha256 = core.sha256_file(RECOVERY_TERMINAL_PATH)
    if terminal.get("status") != RECOVERY_STATUS:
        _fail("R8R_RECOVERY_TERMINAL_RECEIPT_INVALID")
    cache_inventory = sequential._extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if cache_inventory.active != 0:
        _fail("R8R_ACTIVE_EXTRACTION_CACHE_PRESENT")
    if os.path.lexists(CONTINUATION_ROOT):
        _fail("R8R_CONTINUATION_ROOT_COLLISION")
    try:
        observation = capacity.probe_fixed_r8r_continuation_capacity(
            run.plan, process_runner=capacity_process_runner
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTINUATION_CAPACITY_PROBE_FAILED") from exc
    if observation.get("status") != CONTINUATION_CAPACITY_STATUS:
        _fail("R8R_CONTINUATION_CAPACITY_BLOCKED")
    capacity_receipt = _continuation_capacity_receipt(
        run=run,
        implementation_commit=implementation_commit,
        observation=observation,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_terminal_sha256,
        recovery_job_id=recovery_job_id,
        recovery_scheduler_accounting=recovery_accounting,
        captured_at_utc=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )
    _create_private_directory_no_clobber(CONTINUATION_ROOT)
    _create_private_directory_no_clobber(CONTINUATION_SCHEDULER_ROOT)
    capacity_sha = _write_private_json(
        CONTINUATION_CAPACITY_PATH, capacity_receipt
    )
    summary = _continuation_capacity_summary(capacity_receipt, capacity_sha)
    _write_private_json(CONTINUATION_CAPACITY_SUMMARY_PATH, summary)
    claim = _continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha256,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_terminal_sha256,
        capacity_receipt_sha256=capacity_sha,
        recovery_job_id=recovery_job_id,
        recovery_accounting_sha256=core.canonical_json_sha256(
            recovery_accounting
        ),
    )
    claim_sha = _write_private_json(CONTINUATION_CLAIM_PATH, claim)
    array_command = _continuation_array_command(implementation_commit)
    array_job_id = scheduler._capture_qsub(
        "array",
        array_command,
        root=CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
        parser=_parse_r8r_array_qsub_stdout,
    )
    finalizer_command = _continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer",
        finalizer_command,
        root=CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _build_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha256,
        continuation_capacity_receipt_sha256=capacity_sha,
        continuation_claim_sha256=claim_sha,
    )
    _write_private_json(CONTINUATION_SUBMISSION_PATH, receipt)
    _validate_continuation_chain(
        run,
        current_job_id=None,
        role="array",
        wait=False,
    )
    return {
        "status": "CONTINUATION_SUBMITTED",
        "capacity_status": CONTINUATION_CAPACITY_STATUS,
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "new_qsub_submissions": 2,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions_before_continuation": 0,
        "embedding_generations": 0,
    }


def _validate_continuation_chain(
    run: sequential.FullRun,
    *,
    current_job_id: str | None,
    role: str,
    wait: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if role not in {"array", "finalizer"}:
        _fail("R8R_CONTINUATION_ROLE_INVALID")
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(CONTINUATION_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8R_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    terminal = validate_recovery_terminal()
    _, recovery_submission = _validate_recovery_submission(
        require_retained_current=False
    )
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    prefix_receipts = _validate_frozen_prefix(run, include_batch3=True)
    capacity_value, capacity_payload = _load_private_json(
        CONTINUATION_CAPACITY_PATH
    )
    summary_value, _ = _load_private_json(
        CONTINUATION_CAPACITY_SUMMARY_PATH
    )
    claim_value, claim_payload = _load_private_json(CONTINUATION_CLAIM_PATH)
    submission_value, submission_payload = _load_private_json(
        CONTINUATION_SUBMISSION_PATH
    )
    implementation_commit = _current_implementation_commit()
    recovery_sha = core.sha256_file(RECOVERY_TERMINAL_PATH)
    expected_capacity = _continuation_capacity_receipt(
        run=run,
        implementation_commit=implementation_commit,
        observation=capacity_value.get("capacity_observation", {}),
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_sha,
        recovery_job_id=recovery_job_id,
        recovery_scheduler_accounting=capacity_value.get(
            "recovery_scheduler_accounting", {}
        ),
        captured_at_utc=str(capacity_value.get("captured_at_utc", "")),
    )
    capacity_sha = _sha256_bytes(capacity_payload)
    if (
        not _exact_typed_value_equal(capacity_value, expected_capacity)
        or not _exact_typed_value_equal(
            summary_value,
            _continuation_capacity_summary(expected_capacity, capacity_sha),
        )
    ):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    bound_qsub_sha = claim_value.get("qsub_environment_sha256")
    if not isinstance(bound_qsub_sha, str):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    expected_claim = _continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=bound_qsub_sha,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_sha,
        capacity_receipt_sha256=capacity_sha,
        recovery_job_id=recovery_job_id,
        recovery_accounting_sha256=core.canonical_json_sha256(
            expected_capacity["recovery_scheduler_accounting"]
        ),
    )
    if not _exact_typed_value_equal(claim_value, expected_claim):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    array_id = str(submission_value.get("array_job_id", ""))
    finalizer_id = str(submission_value.get("finalizer_job_id", ""))
    expected_submission = _build_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_id,
        finalizer_job_id=finalizer_id,
        qsub_environment_sha256=bound_qsub_sha,
        continuation_capacity_receipt_sha256=capacity_sha,
        continuation_claim_sha256=_sha256_bytes(claim_payload),
    )
    if not _exact_typed_value_equal(submission_value, expected_submission):
        _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    expected_job_id = array_id if role == "array" else finalizer_id
    if current_job_id is not None and current_job_id != expected_job_id:
        _fail("R8R_CONTINUATION_JOB_ID_MISMATCH")
    if (
        terminal.get("status") != RECOVERY_STATUS
        or _sha256_bytes(claim_payload) != core.sha256_file(CONTINUATION_CLAIM_PATH)
        or _sha256_bytes(submission_payload)
        != core.sha256_file(CONTINUATION_SUBMISSION_PATH)
    ):
        _fail("R8R_CONTINUATION_CHAIN_INVALID")
    return capacity_value, claim_value, submission_value


def validate_continuation_worker_submission(
    run: sequential.FullRun,
    *,
    current_job_id: str,
    role: str,
) -> Mapping[str, Any]:
    if (
        not isinstance(run, sequential.FullRun)
        or run.attempt_id != ORIGINAL_ATTEMPT_ID
        or run.plan_sha256 != ORIGINAL_PLAN_SHA256
        or run.authority.governing_commit != ORIGINAL_SCIENTIFIC_COMMIT
        or JOB_RE.fullmatch(current_job_id) is None
    ):
        _fail("R8R_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return _validate_continuation_chain(
        run,
        current_job_id=current_job_id,
        role=role,
        wait=True,
    )[2]


def run_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or not task_text.isdigit()
        or int(task_text) not in FIXED_CONTINUATION_TASK_IDS
    ):
        _fail("R8R_CONTINUATION_ARRAY_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8R_FIXED_CONTINUATION
    )
    result = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if result.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8R_CONTINUATION_BATCH_NOT_FINALIZED")
    return result


def run_continuation_finalizer() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8R_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_continuation_chain(
        run, current_job_id=job_id, role="finalizer", wait=True
    )
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    r8r_authority = finalizer.R8RImplementationAuthority(
        implementation_commit=_current_implementation_commit(),
        recovery_authority_sha256=core.sha256_file(RECOVERY_AUTHORITY_PATH),
        recovery_terminal_receipt_sha256=core.sha256_file(
            RECOVERY_TERMINAL_PATH
        ),
        continuation_capacity_receipt_sha256=core.sha256_file(
            CONTINUATION_CAPACITY_PATH
        ),
        continuation_claim_sha256=core.sha256_file(CONTINUATION_CLAIM_PATH),
        continuation_submission_receipt_sha256=core.sha256_file(
            CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan,
        requirements=run.requirements,
        production_root=run.production_root,
        contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8r_implementation_authority=r8r_authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical")
        is not True
        or summary.get("implementation_authority_epoch_count") != 2
        or summary.get("r8r_implementation_commit")
        != _current_implementation_commit()
        or SHA_RE.fullmatch(
            str(
                summary.get(
                    "r8r_recovery_continuation_authority_sha256"
                )
            )
        )
        is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8R_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return summary


# ---------------------------------------------------------------------------
# Fixed R8U-R3 Batch-16 publication resume
# ---------------------------------------------------------------------------


def _r8u_r3_common(
    *, artifact_type: str, status: str, implementation_commit: str
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": status,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_r3_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
    }
    if set(value) != R8U_R3_COMMON_KEYS:
        _fail("R8U_R3_CONTROL_SCHEMA_INVALID")
    return value


def _r8u_r3_stable_identity(path: Path, *, directory: bool) -> Mapping[str, int]:
    try:
        sequential._require_nonsymlink_components(path)
        before = os.lstat(path)
        after = os.lstat(path)
    except Exception as exc:
        raise R8RControllerError("R8U_R3_PATH_AUTHORITY_INVALID") from exc
    local_projection = lambda value: {
        "device": int(value.st_dev), "inode": int(value.st_ino),
        "mode": int(stat.S_IMODE(value.st_mode)), "uid": int(value.st_uid),
        "gid": int(value.st_gid),
    }
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        local_projection(before) != local_projection(after)
        or not expected_type(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or int(before.st_uid) != os.geteuid()
    ):
        _fail("R8U_R3_PATH_AUTHORITY_INVALID")
    # Device and inode are same-call replacement guards only.  SCC nodes may
    # expose the same owner-private filesystem through different namespaces,
    # so neither value is persisted into a login-to-worker seal.
    portable_projection = {
        "mode": int(stat.S_IMODE(before.st_mode)),
        "uid": int(before.st_uid),
        "gid": int(before.st_gid),
    }
    return dict(sorted(portable_projection.items()))


def _r8u_r3_identity_sha256(path: Path, *, directory: bool) -> str:
    return core.canonical_json_sha256(
        _r8u_r3_stable_identity(path, directory=directory)
    )


def _r8u_r3_mount_authority(path: Path) -> tuple[tuple[Any, ...], str]:
    """Return a path-free current mount identity and canonical digest."""

    _r8u_r3_stable_identity(path, directory=True)
    if not sys.platform.startswith("linux"):
        value = {
            "authority_kind": "SAME_CALL_MOUNT_ONLY_NON_LINUX_V2",
            "filesystem_type": "NON_LINUX_TEST_AUTHORITY",
        }
        try:
            before = os.lstat(path)
            after = os.lstat(path)
        except OSError as exc:
            raise R8RControllerError("R8U_R3_MOUNT_AUTHORITY_INVALID") from exc
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            _fail("R8U_R3_MOUNT_AUTHORITY_INVALID")
        return ("device", int(before.st_dev)), core.canonical_json_sha256(value)
    try:
        payload = Path("/proc/self/mountinfo").read_bytes()
    except OSError as exc:
        raise R8RControllerError("R8U_R3_MOUNT_AUTHORITY_INVALID") from exc
    if len(payload) < 1 or len(payload) > 4 * 1024 * 1024:
        _fail("R8U_R3_MOUNT_AUTHORITY_INVALID")
    matches: list[tuple[int, int, str, str, str]] = []
    absolute = Path(os.path.abspath(path))
    try:
        for raw in payload.decode("utf-8", "strict").splitlines():
            left, separator, right = raw.partition(" - ")
            if not separator:
                _fail("R8U_R3_MOUNT_AUTHORITY_INVALID")
            fields = left.split()
            right_fields = right.split()
            if len(fields) < 6 or len(right_fields) < 3:
                _fail("R8U_R3_MOUNT_AUTHORITY_INVALID")
            mount_point = _r8u_decode_mountinfo_path(fields[4])
            if absolute == mount_point or mount_point in absolute.parents:
                matches.append(
                    (len(mount_point.parts), int(fields[0]), fields[2],
                     right_fields[0], right_fields[1])
                )
    except (UnicodeError, ValueError) as exc:
        raise R8RControllerError("R8U_R3_MOUNT_AUTHORITY_INVALID") from exc
    if not matches:
        _fail("R8U_R3_MOUNT_AUTHORITY_INVALID")
    _depth, mount_id, major_minor, filesystem_type, source = max(matches)
    value = {
        "authority_kind": "PORTABLE_MOUNT_SOURCE_V2",
        "filesystem_type": filesystem_type,
        "mount_source_sha256": _sha256_bytes(source.encode("utf-8")),
    }
    # The key is consumed only inside this invocation to prove that source and
    # target resolve to the same mounted filesystem.  Node-local mount IDs and
    # device numbers are deliberately absent from the persisted digest.
    key = (mount_id, major_minor, filesystem_type, source)
    return key, core.canonical_json_sha256(value)


def _r8u_r3_validate_r2_history(run: sequential.FullRun) -> Mapping[str, Any]:
    """Validate only fixed R2 controls and the exact failed scheduler log."""

    implementation_commit = R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
    seal, seal_payload = _load_private_json(R8U_FAILED_PARTIAL_SEAL_PATH)
    expected_seal = _r8u_failed_partial_seal(
        implementation_commit=implementation_commit
    )
    capacity_value, capacity_payload = _load_private_json(R8U_RECOVERY_CAPACITY_PATH)
    authority, authority_payload = _load_private_json(R8U_RECOVERY_AUTHORITY_PATH)
    submission, submission_payload = _load_private_json(R8U_RECOVERY_SUBMISSION_PATH)
    try:
        # Bind the consumed R2 validator as a frozen local callable so the
        # additive R3 path does not become a fifth live R2 capacity call site.
        historical_capacity_validator = (
            capacity.validate_fixed_r8u_batch16_recovery_capacity
        )
        historical_capacity_validator(
            run.plan, capacity_value,
            r8u_scheduler_log_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R3_R2_CAPACITY_INVALID") from exc
    authority_sha = _sha256_bytes(authority_payload)
    capacity_sha = _sha256_bytes(capacity_payload)
    seal_sha = _sha256_bytes(seal_payload)
    expected_submission = _r8u_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        qsub_environment_sha256=str(submission.get("qsub_environment_sha256", "")),
        recovery_authority_sha256=authority_sha,
        partial_seal_sha256=seal_sha, capacity_sha256=capacity_sha,
    )
    historical_epochs = _r8u_r2_historical_implementation_authority_epochs()
    if (
        authority.get("implementation_commit") != implementation_commit
        or authority.get("implementation_authority_epochs") != historical_epochs
        or submission.get("implementation_commit") != implementation_commit
        or submission.get("implementation_authority_epochs") != historical_epochs
    ):
        _fail("CANDIDATE_STAGE_EVENT_COMMIT_BINDING_INVALID")
    if (
        not _exact_typed_value_equal(seal, expected_seal)
        or not _exact_typed_value_equal(submission, expected_submission)
        or authority.get("artifact_type")
        != "lvef_c3_r8u_r2_batch16_recovery_authority_v1"
        or authority.get("status") != "AUTHORIZED_FIXED_BATCH16_RECOVERY"
        or authority.get("script_authority") != R8U_R2_SCRIPT_AUTHORITY
        or authority.get("recovery_capacity_sha256") != capacity_sha
        or authority.get("failed_partial_seal_sha256") != seal_sha
        or authority.get("fresh_extraction_relative_root")
        != R8U_FRESH_EXTRACTION_BATCH_ROOT.relative_to(ATTEMPT_ROOT).as_posix()
        or submission.get("recovery_job_name")
        != R8U_R2_COMPLETED_EXTRACTION_JOB_NAME
        or submission.get("recovery_job_id")
        != R8U_R2_COMPLETED_EXTRACTION_JOB_ID
        or os.path.lexists(R8U_FRESH_PUBLICATION_PATH)
        or os.path.lexists(R8U_RECOVERY_TERMINAL_PATH)
    ):
        _fail("R8U_R3_R2_RECOVERY_AUTHORITY_INVALID")
    root_info = _r8u_stable_lstat(ATTEMPT_ROOT)
    binding = _R8USchedulerLogBinding(
        role="FRESH_R8U_R2_BATCH16_RECOVERY",
        scheduler_root=R8U_RECOVERY_SCHEDULER_ROOT,
        job_name=R8U_R2_COMPLETED_EXTRACTION_JOB_NAME,
        job_id=R8U_R2_COMPLETED_EXTRACTION_JOB_ID, task_id="NONE",
        terminal_state="TERMINAL_FAILED_APPLICATION_EXIT_78",
    )
    evidence = _r8u_scheduler_log_evidence(
        path=(R8U_RECOVERY_SCHEDULER_ROOT /
              f"{R8U_R2_COMPLETED_EXTRACTION_JOB_NAME}.o"
              f"{R8U_R2_COMPLETED_EXTRACTION_JOB_ID}"),
        binding=binding, approved_device=int(root_info.st_dev),
    )
    if (
        evidence.get("mode") != "0644"
        or evidence.get("size_bytes") != R8U_R2_COMPLETED_EXTRACTION_LOG_BYTES
        or evidence.get("sha256") != R8U_R2_COMPLETED_EXTRACTION_LOG_SHA256
    ):
        _fail("R8U_R3_R2_SCHEDULER_LOG_INVALID")
    return {
        "failed_partial_seal_sha256": seal_sha,
        "r2_recovery_capacity_receipt_sha256": capacity_sha,
        "r2_recovery_authority_sha256": authority_sha,
        "r2_recovery_submission_receipt_sha256": _sha256_bytes(submission_payload),
        "r2_recovery_log_sha256": str(evidence["sha256"]),
    }


def _r8u_r3_csv_rows(
    path: Path, *, failure_code: str = "CANDIDATE_FAILURE_UNRESOLVED"
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Read one extraction control CSV without following links."""

    try:
        payload = _read_control_nofollow(path)
        decoded = payload.decode("utf-8", "strict")
        reader = csv.DictReader(decoded.splitlines())
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    except Exception as exc:
        raise R8RControllerError(failure_code) from exc
    if (
        not header
        or len(header) != len(set(header))
        or any(None in row or set(row) != set(header) for row in rows)
    ):
        _fail(failure_code)
    return header, rows


def _r8u_r3_metadata_projection(
    root: Path,
    *,
    expected_npz_paths: frozenset[PurePosixPath] | None = None,
) -> _R8UR3CandidateProjection:
    """Seal the candidate tree using metadata only; never open an NPZ."""

    try:
        sequential._require_nonsymlink_components(root)
        root_info = os.lstat(root)
        root_after = os.lstat(root)
    except Exception as exc:
        raise R8RControllerError("CANDIDATE_ROOT_AUTHORITY_INVALID") from exc
    identity = lambda value: (
        value.st_mode, value.st_uid, value.st_gid, value.st_dev,
        value.st_ino, value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )
    approved_device = int(root_info.st_dev)
    if (
        identity(root_info) != identity(root_after)
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or not _r8u_private_directory_metadata_valid(
            mode=stat.S_IMODE(root_info.st_mode),
            uid=int(root_info.st_uid),
            device=approved_device,
            approved_device=approved_device,
        )
    ):
        _fail("CANDIDATE_ROOT_AUTHORITY_INVALID")

    file_rows: list[list[Any]] = []
    directory_rows: list[list[Any]] = []
    observed_npz: set[PurePosixPath] = set()
    total_bytes = 0
    npz_bytes = 0
    symlink_count = 0
    nonregular_count = 0
    unsafe_metadata = False
    control_mode_invalid = False
    transient_files: set[str] = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            before = os.lstat(directory)
            relative_directory = directory.relative_to(root).as_posix()
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
            after = os.lstat(directory)
        except (OSError, ValueError) as exc:
            raise R8RControllerError(
                "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID"
            ) from exc
        mode = stat.S_IMODE(before.st_mode)
        if (
            identity(before) != identity(after)
            or not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not _r8u_private_directory_metadata_valid(
                mode=mode, uid=int(before.st_uid),
                device=int(before.st_dev), approved_device=approved_device,
            )
            or (directory != root and os.path.ismount(directory))
        ):
            _fail("CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID")
        # Directory timestamps are deliberately omitted: rename changes the
        # moved root's ctime, and claim mkdir changes the target parent's
        # timestamps.  File timestamps remain sealed below.
        directory_rows.append(
            [relative_directory, "D", mode, int(before.st_uid),
             int(before.st_gid), int(before.st_nlink)]
        )
        for entry in reversed(entries):
            path = Path(entry.path)
            try:
                info_before = os.lstat(path)
                info_after = os.lstat(path)
                relative = PurePosixPath(path.relative_to(root).as_posix())
            except (OSError, ValueError) as exc:
                raise R8RControllerError(
                    "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID"
                ) from exc
            if identity(info_before) != identity(info_after):
                _fail("CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID")
            if stat.S_ISLNK(info_before.st_mode):
                symlink_count += 1
                continue
            if stat.S_ISDIR(info_before.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(info_before.st_mode):
                nonregular_count += 1
                continue
            file_mode = stat.S_IMODE(info_before.st_mode)
            if (
                int(info_before.st_uid) != os.geteuid()
                or int(info_before.st_nlink) != 1
                or int(info_before.st_dev) != approved_device
                or int(info_before.st_size) < 0
            ):
                unsafe_metadata = True
            size = int(info_before.st_size)
            total_bytes += size
            if relative.suffix == ".npz":
                observed_npz.add(relative)
                npz_bytes += size
                if file_mode != 0o600 or size == 0:
                    unsafe_metadata = True
            elif relative.as_posix() in R8U_R3_CANDIDATE_CONTROL_FILES:
                if file_mode != 0o600 or size == 0:
                    control_mode_invalid = True
            elif (
                relative.name.startswith(".nfs")
                or relative.name.startswith(".")
                or ".tmp." in relative.name
            ):
                transient_files.add(relative.as_posix())
            file_rows.append(
                [relative.as_posix(), "F", file_mode,
                 int(info_before.st_uid), int(info_before.st_gid),
                 int(info_before.st_nlink), size,
                 int(info_before.st_mtime_ns), int(info_before.st_ctime_ns)]
            )
    file_rows.sort(key=lambda row: str(row[0]))
    directory_rows.sort(key=lambda row: str(row[0]))
    observed_controls = {
        path.as_posix() for path in (
            PurePosixPath(str(row[0])) for row in file_rows
        ) if path.suffix != ".npz"
    }
    if symlink_count != 0 or nonregular_count != 0 or unsafe_metadata:
        _fail("CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID")
    if transient_files:
        _fail("CANDIDATE_UNEXPECTED_TRANSIENT_FILE")
    if observed_controls != R8U_R3_CANDIDATE_CONTROL_FILES:
        _fail("CANDIDATE_CONTROL_SET_INVALID")
    if control_mode_invalid:
        _fail("CANDIDATE_CONTROL_FILE_MODE_INVALID")
    canonical_npz_re = re.compile(
        r"^clips/clips/[0-9a-f]{2}/[0-9a-f]{64}\.npz$"
    )
    if (
        len(observed_npz) != R8U_R3_CANDIDATE_NPZ_FILES
        or any(canonical_npz_re.fullmatch(path.as_posix()) is None
               for path in observed_npz)
        or (
            expected_npz_paths is not None
            and observed_npz != set(expected_npz_paths)
        )
    ):
        _fail("CANDIDATE_NPZ_PATH_SET_MISMATCH")
    expected_directories = {"."}
    for relative in observed_npz:
        expected_directories.update(
            parent.as_posix() for parent in relative.parents
        )
    observed_directories = {str(row[0]) for row in directory_rows}
    if (
        observed_directories != expected_directories
        or len(file_rows)
        != R8U_R3_CANDIDATE_NPZ_FILES + len(R8U_R3_CANDIDATE_CONTROL_FILES)
    ):
        _fail("CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID")
    root_projection = {
        "mode": stat.S_IMODE(root_info.st_mode),
        "uid": int(root_info.st_uid),
        "gid": int(root_info.st_gid),
        "nlink": int(root_info.st_nlink),
    }
    value = {
        "candidate_regular_files": len(file_rows),
        "candidate_directories": len(directory_rows),
        "candidate_total_bytes": total_bytes,
        "candidate_npz_files": len(observed_npz),
        "candidate_npz_bytes": npz_bytes,
        "candidate_relative_file_projection_sha256": (
            core.canonical_json_sha256(file_rows)
        ),
        "candidate_relative_directory_projection_sha256": (
            core.canonical_json_sha256(directory_rows)
        ),
        "candidate_root_identity_sha256": (
            core.canonical_json_sha256(root_projection)
        ),
        "symlink_count": symlink_count,
        "nonregular_count": nonregular_count,
    }
    return _R8UR3CandidateProjection(
        value=dict(sorted(value.items())),
        expected_npz_paths=frozenset(observed_npz),
    )


def _r8u_r3_normalize_dicom_audit(path: Path) -> Mapping[str, Any]:
    """Parse DICOM Booleans through the production parser, then validate."""

    header, raw_rows = _r8u_r3_csv_rows(
        path, failure_code="CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID"
    )
    required = {
        "subject_id", "study_id", "source_relative_path", "read_ok",
        "is_multiframe", "pixel_decode_ok",
    }
    if not required.issubset(header):
        _fail("CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID")
    try:
        import pandas as pd
        import lvef_reconstruction_smoke as smoke

        payload = _read_control_nofollow(path)
        frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
        if not required.issubset(str(value) for value in frame.columns):
            _fail("CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID")
        for field in ("read_ok", "is_multiframe", "pixel_decode_ok"):
            frame[field] = frame[field].map(smoke.parse_bool)
        records = frame.to_dict(orient="records")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_DICOM_AUDIT_BOOLEAN_DIALECT_MISMATCH"
        ) from exc
    if len(records) != len(raw_rows):
        _fail("CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID")
    try:
        return stages.validate_production_dicom_rows(
            records,
            expected_objects=R8U_BATCH16_RAW_FILES,
            expected_studies=250,
        )
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_DICOM_AUDIT_SEMANTIC_MISMATCH"
        ) from exc


def _r8u_r3_normalize_extraction_manifest(
    path: Path,
) -> _R8UR3CanonicalExtractionManifest:
    """Apply the producer's pandas dialect and canonical row validator once."""

    header, raw_rows = _r8u_r3_csv_rows(
        path, failure_code="CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID"
    )
    if set(header) != stages.EXTRACTION_PRODUCER_FIELDS or not raw_rows:
        _fail("CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID")
    allowed_write_tokens = {"True", "False"}
    allowed_failure_tokens = {"NONE", *stages.ALLOWED_FAILURE_SUBSTAGES}
    if (
        any(row.get("write_ok") not in allowed_write_tokens for row in raw_rows)
        or any(
            row.get("failure_substage") not in allowed_failure_tokens
            for row in raw_rows
        )
    ):
        _fail("CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH")
    try:
        import pandas as pd

        payload = _read_control_nofollow(path)
        frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
        if set(str(value) for value in frame.columns) != stages.EXTRACTION_PRODUCER_FIELDS:
            _fail("CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID")
        records = tuple(frame.to_dict(orient="records"))
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH"
        ) from exc
    if (
        len(records) != len(raw_rows)
        or any(type(row.get("write_ok")) is not bool for row in records)
    ):
        _fail("CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH")
    try:
        summary = stages.validate_production_extraction_rows(
            records, expected_cines=len(records), clips_root=None
        )
    except Exception as exc:
        raise R8RControllerError(
            "GENUINE_COMPLETED_EXTRACTION_INCONSISTENCY"
        ) from exc

    expected_npz: set[PurePosixPath] = set()
    projection: list[tuple[str, str]] = []
    for row in records:
        if row.get("write_ok") is not True:
            continue
        output_relative = row.get("output_relative_path")
        digest = row.get("npz_sha256")
        if not isinstance(output_relative, str) or not isinstance(digest, str):
            _fail("GENUINE_COMPLETED_EXTRACTION_INCONSISTENCY")
        # The producer received <stage>/clips as output_root while each row's
        # canonical locator already begins with clips/.  EchoPrime consumes
        # the same <stage>/clips root, so stage-relative closure is clips/<row>.
        stage_relative = PurePosixPath("clips") / PurePosixPath(output_relative)
        if stage_relative in expected_npz:
            _fail("GENUINE_COMPLETED_EXTRACTION_INCONSISTENCY")
        expected_npz.add(stage_relative)
        projection.append((stage_relative.as_posix(), digest))
    projection.sort()
    return _R8UR3CanonicalExtractionManifest(
        rows=records,
        summary=dict(summary),
        expected_npz_paths=frozenset(expected_npz),
        manifest_projection=tuple(projection),
    )


def _r8u_r3_candidate_projection(
    run: sequential.FullRun,
) -> _R8UR3CandidateProjection:
    """Validate the exact completed job-7354951 extraction candidate."""

    source = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    if (
        not source.is_absolute()
        or not target.is_absolute()
        or os.path.lexists(R8U_FRESH_PUBLICATION_PATH)
        or os.path.lexists(target)
    ):
        _fail("CANDIDATE_TARGET_STATE_INVALID")
    # Establish the complete no-follow metadata/control closure before any
    # control is opened.  The returned path set is reused below; no rescan and
    # no scientific body read is permitted during candidate adjudication.
    tree = _r8u_r3_metadata_projection(source)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    controls = {
        name: source / name for name in R8U_R3_CANDIDATE_CONTROL_FILES
    }
    try:
        summary = stages.validate_completed_stage_for_recovery(
            stage_directory=source,
            stage="DICOM_EXTRACTION",
            batch_id=R8U_FIXED_BATCH_ID,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            input_manifest=(
                sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["raw_batch"]
                / "verified_download_manifest.restricted.csv"
            ),
            artifact_names=(
                "dicom_audit.restricted.csv",
                "extraction_manifest.restricted.csv",
                "technical_disposition_manifest.restricted.csv",
                "dicom_extraction.summary.json",
            ),
            summary_name="dicom_extraction.summary.json",
        )
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_STAGE_COMPLETION_RECEIPT_INVALID"
        ) from exc

    audit_summary = _r8u_r3_normalize_dicom_audit(
        controls["dicom_audit.restricted.csv"]
    )
    manifest = _r8u_r3_normalize_extraction_manifest(
        controls["extraction_manifest.restricted.csv"]
    )
    try:
        stages.validate_extraction_manifest_plan_membership(
            controls["extraction_manifest.restricted.csv"],
            planned,
            controls["technical_disposition_manifest.restricted.csv"],
        )
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_EXTRACTION_MANIFEST_PLAN_MISMATCH"
        ) from exc
    try:
        dispositions = stages.read_technical_disposition_manifest(
            controls["technical_disposition_manifest.restricted.csv"]
        )
        technical_summary = stages.validate_technical_disposition_manifest_rows(
            manifest.rows, dispositions
        )
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_TECHNICAL_DISPOSITION_MANIFEST_INVALID"
        ) from exc
    if tree.expected_npz_paths != manifest.expected_npz_paths:
        _fail("CANDIDATE_NPZ_PATH_SET_MISMATCH")

    expected_summary = {
        "status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "n_objects": R8U_BATCH16_RAW_FILES,
        "n_studies": 250,
        "n_readable": R8U_BATCH16_RAW_FILES,
        "n_unreadable": 0,
        "n_multiframe_candidates": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_single_frame": 8_490,
        "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
    }
    manifest_bindings = {
        "status": "status",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_ordinary_preprocessing_path": "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path": (
            "n_spatial_fallback_preprocessing_path"
        ),
        "n_temporal_fallback_preprocessing_path": (
            "n_temporal_fallback_preprocessing_path"
        ),
        "n_spatial_temporal_fallback_preprocessing_path": (
            "n_spatial_temporal_fallback_preprocessing_path"
        ),
        "object_substitution_count": "object_substitution_count",
    }
    if (
        any(summary.get(key) != expected for key, expected in expected_summary.items())
        or any(
            audit_summary.get(key) != expected_summary[key]
            for key in (
                "n_objects", "n_studies", "n_readable", "n_unreadable",
                "n_multiframe_candidates", "n_single_frame",
                "n_pixel_decode_failures",
            )
        )
        or any(
            manifest.summary.get(source_key) != expected_summary[summary_key]
            for summary_key, source_key in manifest_bindings.items()
        )
        or technical_summary.get("n_object_technical_dispositions") != 0
        or technical_summary.get("object_substitution_count") != 0
        or len(dispositions) != 0
        or len(manifest.expected_npz_paths) != R8U_R3_CANDIDATE_NPZ_FILES
    ):
        _fail("CANDIDATE_EXTRACTION_SUMMARY_MISMATCH")
    try:
        source_mount_key, source_mount_sha = _r8u_r3_mount_authority(source.parent)
        target_mount_key, target_mount_sha = _r8u_r3_mount_authority(target.parent)
    except Exception as exc:
        raise R8RControllerError("CANDIDATE_MOUNT_AUTHORITY_INVALID") from exc
    if source_mount_key != target_mount_key:
        _fail("CANDIDATE_MOUNT_AUTHORITY_INVALID")
    try:
        control_hashes = {
            "stage_completion_receipt_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["stage_completion_receipt.restricted.json"]
                )
            ),
            "extraction_manifest_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["extraction_manifest.restricted.csv"]
                )
            ),
            "dicom_audit_sha256": _sha256_bytes(
                _read_control_nofollow(controls["dicom_audit.restricted.csv"])
            ),
            "extraction_summary_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["dicom_extraction.summary.json"]
                )
            ),
            "technical_disposition_manifest_sha256": (
                stages.technical_disposition_manifest_sha256(
                    controls["technical_disposition_manifest.restricted.csv"]
                )
            ),
        }
    except Exception as exc:
        raise R8RControllerError("CANDIDATE_CONTROL_SET_INVALID") from exc
    try:
        source_parent_sha = _r8u_r3_identity_sha256(
            source.parent, directory=True
        )
    except Exception as exc:
        raise R8RControllerError("CANDIDATE_ROOT_AUTHORITY_INVALID") from exc
    try:
        target_parent_sha = _r8u_r3_identity_sha256(
            target.parent, directory=True
        )
    except Exception as exc:
        raise R8RControllerError("CANDIDATE_TARGET_STATE_INVALID") from exc
    value = {
        **dict(tree.value),
        **control_hashes,
        "candidate_npz_manifest_projection_sha256": (
            core.canonical_json_sha256(manifest.manifest_projection)
        ),
        "source_parent_identity_sha256": source_parent_sha,
        "source_mount_identity_sha256": source_mount_sha,
        "target_parent_identity_sha256": target_parent_sha,
        "target_mount_identity_sha256": target_mount_sha,
        "source_target_same_mounted_filesystem": True,
        "target_absent": True,
        "n_selected_studies": 250,
        "n_source_objects": R8U_BATCH16_RAW_FILES,
        "source_bytes": R8U_BATCH16_RAW_BYTES,
        "n_readable": R8U_BATCH16_RAW_FILES,
        "n_unreadable": 0,
        "n_multiframe_candidates": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_single_frame": 8_490,
        "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
        "extraction_status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    return _R8UR3CandidateProjection(
        value=dict(sorted(value.items())),
        expected_npz_paths=manifest.expected_npz_paths,
    )


def _r8u_r3_candidate_seal(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    history: Mapping[str, Any],
    projection: _R8UR3CandidateProjection,
) -> Mapping[str, Any]:
    value = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
            status="PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
            implementation_commit=implementation_commit,
        ),
        "r2_recovery_job_id": R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        "r2_recovery_capacity_receipt_sha256": history[
            "r2_recovery_capacity_receipt_sha256"
        ],
        "r2_recovery_authority_sha256": history["r2_recovery_authority_sha256"],
        "r2_recovery_submission_receipt_sha256": history[
            "r2_recovery_submission_receipt_sha256"
        ],
        "failed_partial_seal_sha256": history["failed_partial_seal_sha256"],
        **dict(projection.value),
    }
    if set(value) != R8U_R3_CANDIDATE_SEAL_KEYS:
        _fail("R8U_R3_CANDIDATE_SEAL_SCHEMA_INVALID")
    return value


def _r8u_r3_validate_candidate_seal_static(
    candidate: Mapping[str, Any], *, implementation_commit: str,
    history: Mapping[str, Any],
) -> None:
    """Replay every post-move static/history/zero candidate assertion."""

    expected = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
            status="PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
            implementation_commit=implementation_commit,
        ),
        "r2_recovery_job_id": R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        "r2_recovery_capacity_receipt_sha256": history[
            "r2_recovery_capacity_receipt_sha256"
        ],
        "r2_recovery_authority_sha256": history["r2_recovery_authority_sha256"],
        "r2_recovery_submission_receipt_sha256": history[
            "r2_recovery_submission_receipt_sha256"
        ],
        "failed_partial_seal_sha256": history["failed_partial_seal_sha256"],
        "candidate_regular_files": (
            R8U_R3_CANDIDATE_NPZ_FILES + len(R8U_R3_CANDIDATE_CONTROL_FILES)
        ),
        "candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "source_target_same_mounted_filesystem": True,
        "target_absent": True,
        "symlink_count": 0,
        "nonregular_count": 0,
        "n_selected_studies": 250,
        "n_source_objects": R8U_BATCH16_RAW_FILES,
        "source_bytes": R8U_BATCH16_RAW_BYTES,
        "n_readable": R8U_BATCH16_RAW_FILES,
        "n_unreadable": 0,
        "n_multiframe_candidates": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_single_frame": 8_490,
        "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
        "extraction_status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    digest_fields = (
        "stage_completion_receipt_sha256", "extraction_manifest_sha256",
        "dicom_audit_sha256", "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
        "candidate_relative_file_projection_sha256",
        "candidate_relative_directory_projection_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_root_identity_sha256", "source_parent_identity_sha256",
        "source_mount_identity_sha256", "target_parent_identity_sha256",
        "target_mount_identity_sha256",
    )
    dynamic_integers = (
        "candidate_directories", "candidate_total_bytes", "candidate_npz_bytes",
    )
    if (
        set(candidate) != R8U_R3_CANDIDATE_SEAL_KEYS
        or any(
            not _exact_typed_value_equal(candidate.get(key), value)
            for key, value in expected.items()
        )
        or any(
            SHA_RE.fullmatch(str(candidate.get(field, ""))) is None
            for field in digest_fields
        )
        or any(
            type(candidate.get(field)) is not int or candidate[field] <= 0
            for field in dynamic_integers
        )
        or candidate["candidate_npz_bytes"] > candidate["candidate_total_bytes"]
    ):
        _fail("CANDIDATE_FAILURE_UNRESOLVED")


def validate_r8u_r3_extraction_candidate_seal(
    run: sequential.FullRun,
    *,
    history: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Revalidate the sealed candidate without reading any scientific body."""

    implementation_commit = _current_r8u_r3_implementation_commit()
    resolved_history = history or _r8u_r3_validate_r2_history(run)
    observed, _ = _load_private_json(R8U_R3_CANDIDATE_SEAL_PATH)
    expected = _r8u_r3_candidate_seal(
        run=run,
        implementation_commit=implementation_commit,
        history=resolved_history,
        projection=_r8u_r3_candidate_projection(run),
    )
    if not _exact_typed_value_equal(observed, expected):
        _fail("CANDIDATE_FAILURE_UNRESOLVED")
    _r8u_r3_validate_candidate_seal_static(
        observed, implementation_commit=implementation_commit,
        history=resolved_history,
    )
    return observed


def _r8u_r3_raw_rename_noreplace(
    source: Path,
    target: Path,
    *,
    invoker: Callable[[Path, Path], Any] | None = None,
) -> _R8UR3RenameResult:
    """Invoke the preferred no-replace primitive exactly once."""

    if (
        not source.is_absolute()
        or not target.is_absolute()
        or Path(os.path.abspath(source)) != source
        or Path(os.path.abspath(target)) != target
    ):
        _fail("CANDIDATE_FAILURE_UNRESOLVED")
    if invoker is not None:
        try:
            value = invoker(source, target)
        except OSError as exc:
            number = int(exc.errno or errno.EIO)
            return _R8UR3RenameResult(False, number, errno.errorcode.get(number, "UNKNOWN"))
        if isinstance(value, _R8UR3RenameResult):
            return value
        if value is None or value == 0:
            return _R8UR3RenameResult(True, 0, "NONE")
        if isinstance(value, int):
            return _R8UR3RenameResult(False, value, errno.errorcode.get(value, "UNKNOWN"))
        _fail("R8U_PUBLICATION_RENAME_FAILED")
    if not sys.platform.startswith("linux"):
        return _R8UR3RenameResult(False, errno.ENOSYS, "ENOSYS")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        primitive = library.renameat2
    except (AttributeError, OSError):
        return _R8UR3RenameResult(False, errno.ENOSYS, "ENOSYS")
    primitive.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    primitive.restype = ctypes.c_int
    ctypes.set_errno(0)
    returned = primitive(
        -100, os.fsencode(source), -100, os.fsencode(target), 1
    )
    if returned == 0:
        return _R8UR3RenameResult(True, 0, "NONE")
    number = int(ctypes.get_errno() or errno.EIO)
    return _R8UR3RenameResult(
        False, number, errno.errorcode.get(number, "UNKNOWN")
    )


def _r8u_r3_primary_classification(result: _R8UR3RenameResult) -> str:
    if result.returned_success:
        return "RENAME_NOREPLACE_SUPPORTED"
    classes = {
        errno.EINVAL: "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        errno.ENOSYS: "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP): (
            "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP"
        ),
        errno.EXDEV: "RENAME_CROSS_MOUNT_EXDEV",
        errno.EACCES: "RENAME_PERMISSION_FAILURE",
        errno.EPERM: "RENAME_PERMISSION_FAILURE",
    }
    return classes.get(result.errno_number, "OTHER_EXACT_ERRNO_CLASS")


def _r8u_r3_real_rename_classification(result: _R8UR3RenameResult) -> str:
    if result.returned_success:
        return "RENAME_RETURNED_SUCCESS"
    if result.errno_number in {
        errno.EINVAL, errno.ENOSYS,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }:
        return "RENAME_ERROR_UNSUPPORTED"
    if result.errno_number == errno.EXDEV:
        return "RENAME_ERROR_CROSS_MOUNT"
    if result.errno_number in {errno.EACCES, errno.EPERM}:
        return "RENAME_ERROR_PERMISSION"
    if result.errno_number in {errno.EEXIST, errno.ENOTEMPTY}:
        return "RENAME_ERROR_COLLISION"
    if result.errno_number == errno.ENOENT:
        return "RENAME_ERROR_SOURCE_MISSING"
    if result.errno_number == errno.EIO:
        return "RENAME_ERROR_IO"
    return "RENAME_ERROR_OTHER"


def _r8u_r3_primitive_probe(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    candidate_seal: Mapping[str, Any],
    invoker: Callable[[Path, Path], Any] | None = None,
) -> Mapping[str, Any]:
    """Run one empty-directory probe on the worker's current mount."""

    source = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    source_key, source_mount = _r8u_r3_mount_authority(source.parent)
    target_key, target_mount = _r8u_r3_mount_authority(target.parent)
    probe_key, probe_mount = _r8u_r3_mount_authority(
        R8U_R3_PROBE_WORK_ROOT.parent
    )
    if os.path.lexists(R8U_R3_PROBE_PATH) or os.path.lexists(R8U_R3_PROBE_WORK_ROOT):
        _fail("R8U_R3_PROBE_COLLISION")
    source_parent_identity = _r8u_r3_identity_sha256(
        source.parent, directory=True
    )
    target_parent_identity = _r8u_r3_identity_sha256(
        target.parent, directory=True
    )
    same_mounted_filesystem = source_key == target_key
    probe_matches_real_parents = (
        same_mounted_filesystem and probe_key == source_key
    )
    created: list[Path] = []
    removed = 0
    probe_source = R8U_R3_PROBE_WORK_ROOT / "source"
    probe_target = R8U_R3_PROBE_WORK_ROOT / "target"
    result = _R8UR3RenameResult(
        False,
        errno.EXDEV if not same_mounted_filesystem else errno.EIO,
        "EXDEV" if not same_mounted_filesystem else "EIO",
    )
    source_present = False
    target_present = False
    target_exact = False
    classification = (
        "RENAME_CROSS_MOUNT_EXDEV"
        if not same_mounted_filesystem else "OTHER_EXACT_ERRNO_CLASS"
    )
    primitive_completed = False
    if probe_matches_real_parents:
        try:
            for directory in (R8U_R3_PROBE_WORK_ROOT, probe_source):
                _create_private_directory_no_clobber(directory)
                created.append(directory)
            result = _r8u_r3_raw_rename_noreplace(
                probe_source, probe_target, invoker=invoker
            )
            primitive_completed = True
            source_present = os.path.lexists(probe_source)
            target_present = os.path.lexists(probe_target)
            target_exact = (
                target_present
                and not probe_target.is_symlink()
                and probe_target.is_dir()
                and next(os.scandir(probe_target), None) is None
            )
            classification = _r8u_r3_primary_classification(result)
            if result.returned_success:
                if source_present or not target_exact:
                    classification = "RENAME_AMBIGUOUS_SERVER_RESULT"
            elif not (source_present and not target_present):
                classification = "RENAME_AMBIGUOUS_SERVER_RESULT"
        except R8RControllerError:
            # The closed receipt records setup failure without exposing a path
            # or attempting any scientific read.  Enforcement happens only
            # after this durable diagnostic has been written.
            classification = "OTHER_EXACT_ERRNO_CLASS"
        except OSError:
            # The primitive result remains exact.  A metadata RPC failure
            # while resolving its post-state is a separate ambiguous NFS
            # ruling, not a second rename or a replacement errno.
            classification = (
                "RENAME_AMBIGUOUS_SERVER_RESULT"
                if primitive_completed else "OTHER_EXACT_ERRNO_CLASS"
            )
        finally:
            for directory in (probe_target, probe_source, R8U_R3_PROBE_WORK_ROOT):
                try:
                    directory.rmdir()
                    removed += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
    cleanup_passed = (
        not os.path.lexists(R8U_R3_PROBE_WORK_ROOT)
        and removed == len(created)
    )
    proceedable = (
        probe_matches_real_parents
        and cleanup_passed
        and classification in R8U_R3_PROCEEDABLE_PROBE_RESULTS
        and candidate_seal.get("target_absent") is True
    )
    value = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_publication_primitive_probe_v1",
            status=(
                "PASS_PUBLICATION_PRIMITIVE_PROBE"
                if proceedable else "BLOCKED_PUBLICATION_PRIMITIVE_PROBE"
            ),
            implementation_commit=implementation_commit,
        ),
        "primary_primitive": "RENAMEAT2_RENAME_NOREPLACE",
        "primary_result": classification,
        "primary_errno": result.errno_name,
        "primary_errno_number": result.errno_number,
        "primary_returned_success": result.returned_success,
        "real_source_parent_identity_sha256": source_parent_identity,
        "real_target_parent_identity_sha256": target_parent_identity,
        "real_source_mount_identity_sha256": source_mount,
        "real_target_mount_identity_sha256": target_mount,
        "real_parents_same_mounted_filesystem": same_mounted_filesystem,
        "probe_mount_identity_sha256": probe_mount,
        "probe_mount_matches_real_parents": probe_matches_real_parents,
        "probe_source_present_after": source_present,
        "probe_target_present_after": target_present,
        "probe_target_exact_after": target_exact,
        "probe_cleanup_passed": cleanup_passed,
        "probe_directories_created": len(created),
        "probe_directories_removed": removed,
        "scientific_file_body_reads": 0,
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
    }
    if set(value) != R8U_R3_PROBE_KEYS:
        _fail("R8U_R3_PROBE_SCHEMA_INVALID")
    # A completed diagnosis is durable evidence even when its classification
    # blocks the real candidate.  Publish it before enforcing proceedability.
    _write_private_json(R8U_R3_PROBE_PATH, value)
    if not proceedable:
        if (
            not probe_matches_real_parents
            or classification == "RENAME_CROSS_MOUNT_EXDEV"
        ):
            _fail("R8U_PUBLICATION_CROSS_MOUNT")
        if classification == "RENAME_PERMISSION_FAILURE":
            _fail("R8U_PUBLICATION_PERMISSION_DENIED")
        if result.errno_number in {errno.EEXIST, errno.ENOTEMPTY}:
            _fail("R8U_PUBLICATION_TARGET_ALREADY_EXISTS")
        if not cleanup_passed:
            _fail("R8U_R3_PROBE_CLEANUP_FAILED")
        if candidate_seal.get("target_absent") is not True:
            _fail("R8U_PUBLICATION_TARGET_ALREADY_EXISTS")
        if classification == "RENAME_AMBIGUOUS_SERVER_RESULT":
            _fail("R8U_PUBLICATION_AMBIGUOUS_STATE")
        _fail("R8U_PUBLICATION_RENAME_FAILED")
    return value


def _r8u_r3_publication_claim(
    *,
    implementation_commit: str,
    resume_job_id: str,
    history: Mapping[str, Any],
    candidate_seal: Mapping[str, Any],
    probe: Mapping[str, Any],
    worker_process_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    _r8u_r3_validate_process_projection(
        worker_process_projection, worker_local=True
    )
    submission, _ = _load_private_json(R8U_R3_SUBMISSION_PATH)
    pre_projection = submission.get("pre_qsub_process_projection")
    qstat_projection = submission.get("initial_qstat_projection")
    if not isinstance(pre_projection, Mapping) or not isinstance(qstat_projection, Mapping):
        _fail("R8U_R3_PUBLICATION_CLAIM_SCHEMA_INVALID")
    _r8u_r3_validate_process_projection(pre_projection, worker_local=False)
    _r8u_r3_validate_initial_qstat_projection(
        qstat_projection,
        resume_job_id=str(submission.get("resume_job_id", "")),
        implementation_commit=implementation_commit,
    )
    selected = (
        "RENAMEAT2_RENAME_NOREPLACE"
        if probe["primary_result"] == "RENAME_NOREPLACE_SUPPORTED"
        else "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
    )
    value = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_publication_claim_v1",
            status="AUTHORIZED_EXCLUSIVE_BATCH16_PUBLICATION",
            implementation_commit=implementation_commit,
        ),
        "resume_job_id": resume_job_id,
        "r2_recovery_job_id": R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        "extraction_candidate_seal_sha256": core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        "publication_primitive_probe_sha256": core.sha256_file(R8U_R3_PROBE_PATH),
        "resume_authority_sha256": core.sha256_file(R8U_R3_AUTHORITY_PATH),
        "resume_submission_receipt_sha256": core.sha256_file(R8U_R3_SUBMISSION_PATH),
        "failed_partial_seal_sha256": history["failed_partial_seal_sha256"],
        "candidate_relative_file_projection_sha256": candidate_seal[
            "candidate_relative_file_projection_sha256"
        ],
        "target_role": "extracted_cache/c3_batch_015/dicom_extraction",
        "publication_primitive_selected": selected,
        "primary_result": probe["primary_result"],
        "target_absent": True,
        "source_target_same_mounted_filesystem": True,
        "source_parent_identity_sha256": candidate_seal[
            "source_parent_identity_sha256"
        ],
        "target_parent_identity_sha256": candidate_seal[
            "target_parent_identity_sha256"
        ],
        "source_mount_identity_sha256": candidate_seal[
            "source_mount_identity_sha256"
        ],
        "target_mount_identity_sha256": candidate_seal[
            "target_mount_identity_sha256"
        ],
        "pre_qsub_process_projection_sha256": (
            core.canonical_json_sha256(pre_projection)
        ),
        "initial_qstat_projection_sha256": (
            core.canonical_json_sha256(qstat_projection)
        ),
        "worker_process_projection": dict(worker_process_projection),
        "competing_active_jobs": 0,
        "competing_active_processes": 0,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "npz_body_reads": 0,
    }
    if set(value) != R8U_R3_PUBLICATION_CLAIM_KEYS:
        _fail("R8U_R3_PUBLICATION_CLAIM_SCHEMA_INVALID")
    return value


def _r8u_r3_create_publication_claim(value: Mapping[str, Any]) -> str:
    try:
        sequential._require_nonsymlink_components(R8U_R3_PUBLICATION_CLAIM_ROOT.parent)
        sequential._validate_private_directory(R8U_R3_PUBLICATION_CLAIM_ROOT.parent)
        R8U_R3_PUBLICATION_CLAIM_ROOT.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise R8RControllerError("R8U_PUBLICATION_CLAIM_COLLISION") from exc
    except Exception as exc:
        raise R8RControllerError("R8U_PUBLICATION_CLAIM_COLLISION") from exc
    try:
        return _write_private_json(R8U_R3_PUBLICATION_CLAIM_PATH, value)
    except Exception as exc:
        raise R8RControllerError("R8U_PUBLICATION_CLAIM_COLLISION") from exc


def _r8u_r3_validate_publication_claim(
    *, run: sequential.FullRun, candidate_seal: Mapping[str, Any],
    probe: Mapping[str, Any], expected_sha256: str,
) -> Mapping[str, Any]:
    claim, payload = _load_private_json(R8U_R3_PUBLICATION_CLAIM_PATH)
    _submission_run, _authority, submission = (
        _validate_r8u_r3_resume_submission(require_candidate_live=True)
    )
    worker_projection = claim.get("worker_process_projection")
    if not isinstance(worker_projection, Mapping):
        _fail("R8U_R3_PUBLICATION_CLAIM_INVALID")
    expected = _r8u_r3_publication_claim(
        implementation_commit=_current_r8u_r3_implementation_commit(),
        resume_job_id=str(submission.get("resume_job_id", "")),
        history=_r8u_r3_validate_r2_history(run),
        candidate_seal=candidate_seal, probe=probe,
        worker_process_projection=worker_projection,
    )
    if (
        not _exact_typed_value_equal(claim, expected)
        or _sha256_bytes(payload) != expected_sha256
        or core.sha256_file(R8U_R3_PUBLICATION_CLAIM_PATH) != expected_sha256
    ):
        _fail("R8U_R3_PUBLICATION_CLAIM_INVALID")
    return claim


def _r8u_r3_target_projection(
    run: sequential.FullRun, candidate_seal: Mapping[str, Any]
) -> Mapping[str, Any]:
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    if not target.is_dir() or target.is_symlink():
        _fail("CANDIDATE_TARGET_STATE_INVALID")
    tree = _r8u_r3_metadata_projection(target)
    manifest = _r8u_r3_normalize_extraction_manifest(
        target / "extraction_manifest.restricted.csv"
    )
    if tree.expected_npz_paths != manifest.expected_npz_paths:
        _fail("CANDIDATE_NPZ_PATH_SET_MISMATCH")
    try:
        stages.validate_extraction_manifest_plan_membership(
            target / "extraction_manifest.restricted.csv",
            run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1],
            target / "technical_disposition_manifest.restricted.csv",
        )
    except Exception as exc:
        raise R8RControllerError(
            "CANDIDATE_EXTRACTION_MANIFEST_PLAN_MISMATCH"
        ) from exc
    observed = {
        **dict(tree.value),
        "candidate_npz_manifest_projection_sha256": (
            core.canonical_json_sha256(manifest.manifest_projection)
        ),
        "stage_completion_receipt_sha256": _sha256_bytes(
            _read_control_nofollow(target / "stage_completion_receipt.restricted.json")
        ),
        "extraction_manifest_sha256": _sha256_bytes(
            _read_control_nofollow(target / "extraction_manifest.restricted.csv")
        ),
        "dicom_audit_sha256": _sha256_bytes(
            _read_control_nofollow(target / "dicom_audit.restricted.csv")
        ),
        "extraction_summary_sha256": _sha256_bytes(
            _read_control_nofollow(target / "dicom_extraction.summary.json")
        ),
        "technical_disposition_manifest_sha256": (
            stages.technical_disposition_manifest_sha256(
                target / "technical_disposition_manifest.restricted.csv"
            )
        ),
    }
    compared = frozenset(observed)
    if any(observed[key] != candidate_seal.get(key) for key in compared):
        _fail("CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID")
    return dict(sorted(observed.items()))


def _r8u_r3_publish_candidate(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    candidate_seal: Mapping[str, Any],
    probe: Mapping[str, Any],
    publication_claim_sha256: str,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
) -> Mapping[str, Any]:
    """Publish once under the persistent claim, then rule on post-state."""

    source = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    history = _r8u_r3_validate_r2_history(run)
    revalidated_candidate = validate_r8u_r3_extraction_candidate_seal(
        run, history=history
    )
    if not _exact_typed_value_equal(revalidated_candidate, candidate_seal):
        _fail("CANDIDATE_FAILURE_UNRESOLVED")
    _r8u_r3_validate_publication_claim(
        run=run, candidate_seal=candidate_seal, probe=probe,
        expected_sha256=publication_claim_sha256,
    )
    if os.path.lexists(target):
        _fail("R8U_PUBLICATION_TARGET_ALREADY_EXISTS")
    source_key, source_mount = _r8u_r3_mount_authority(source.parent)
    target_key, target_mount = _r8u_r3_mount_authority(target.parent)
    if source_key != target_key:
        _fail("R8U_PUBLICATION_CROSS_MOUNT")
    if (
        _r8u_r3_identity_sha256(source.parent, directory=True)
        != candidate_seal.get("source_parent_identity_sha256")
        or _r8u_r3_identity_sha256(target.parent, directory=True)
        != candidate_seal.get("target_parent_identity_sha256")
        or source_mount != candidate_seal.get("source_mount_identity_sha256")
        or target_mount != candidate_seal.get("target_mount_identity_sha256")
    ):
        _fail("R8U_PUBLICATION_PARENT_AUTHORITY_INVALID")
    fallback_used = probe["primary_result"] != "RENAME_NOREPLACE_SUPPORTED"
    if fallback_used and probe["primary_result"] not in {
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }:
        _fail("R8U_RENAME_NOREPLACE_UNSUPPORTED")
    if fallback_used:
        try:
            returned = fallback_invoker(source, target)
            result = _R8UR3RenameResult(True, 0, "NONE")
            if returned not in {None, 0}:
                _fail("R8U_PUBLICATION_RENAME_FAILED")
        except OSError as exc:
            number = int(exc.errno or errno.EIO)
            result = _R8UR3RenameResult(
                False, number, errno.errorcode.get(number, "UNKNOWN")
            )
        primitive = "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
    else:
        result = _r8u_r3_raw_rename_noreplace(
            source, target, invoker=primary_invoker
        )
        primitive = "RENAMEAT2_RENAME_NOREPLACE"

    source_present = os.path.lexists(source)
    target_present = os.path.lexists(target)
    target_exact = False
    target_projection: Mapping[str, Any] | None = None
    if target_present:
        try:
            target_projection = _r8u_r3_target_projection(run, candidate_seal)
            target_exact = True
        except R8RControllerError:
            target_exact = False
    if not source_present and target_exact:
        ruling = (
            "PUBLICATION_PASS"
            if result.returned_success
            else "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
        )
    elif source_present and not target_present:
        if result.errno_number == errno.EXDEV:
            _fail("R8U_PUBLICATION_CROSS_MOUNT")
        if result.errno_number in {errno.EACCES, errno.EPERM}:
            _fail("R8U_PUBLICATION_PERMISSION_DENIED")
        if result.errno_number in {errno.EEXIST, errno.ENOTEMPTY}:
            _fail("R8U_PUBLICATION_TARGET_ALREADY_EXISTS")
        _fail("R8U_PUBLICATION_RENAME_FAILED")
    elif source_present and target_present:
        _fail("R8U_PUBLICATION_AMBIGUOUS_STATE")
    else:
        _fail("R8U_PUBLICATION_AMBIGUOUS_STATE")
    if target_projection is None or not target_exact:
        _fail("R8U_PUBLICATION_POSTVALIDATION_FAILED")
    receipt = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_batch16_publication_v1",
            status="PASS_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
            implementation_commit=implementation_commit,
        ),
        "extraction_candidate_seal_sha256": core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        "publication_primitive_probe_sha256": core.sha256_file(R8U_R3_PROBE_PATH),
        "publication_claim_sha256": publication_claim_sha256,
        "primitive_attempted": primitive,
        "primary_result": probe["primary_result"],
        "primary_errno": probe["primary_errno"],
        "fallback_used": fallback_used,
        "fallback_primitive": (
            "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME" if fallback_used else "NONE"
        ),
        "rename_returned_success": result.returned_success,
        "real_rename_returned_success": result.returned_success,
        "real_rename_errno": result.errno_name,
        "real_rename_errno_number": result.errno_number,
        "real_rename_errno_classification": (
            _r8u_r3_real_rename_classification(result)
        ),
        "publication_ruling": ruling,
        "prepublication_candidate_sha256": candidate_seal[
            "candidate_relative_file_projection_sha256"
        ],
        "postpublication_target_sha256": target_projection[
            "candidate_relative_file_projection_sha256"
        ],
        "source_absent": True,
        "target_exact": True,
        "candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "candidate_total_bytes": candidate_seal["candidate_total_bytes"],
        "files_moved": R8U_R3_CANDIDATE_NPZ_FILES,
        "files_copied": 0,
        "files_deleted_independently": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "npz_body_reads": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    if set(receipt) != R8U_R3_PUBLICATION_KEYS:
        _fail("R8U_PUBLICATION_RECEIPT_FAILED")
    try:
        _write_private_json(R8U_R3_PUBLICATION_PATH, receipt)
    except Exception as exc:
        raise R8RControllerError("R8U_PUBLICATION_RECEIPT_FAILED") from exc
    return receipt


def _r8u_r3_validate_probe_receipt(
    probe: Mapping[str, Any], *, candidate: Mapping[str, Any],
    implementation_commit: str,
) -> None:
    classification = probe.get("primary_result")
    errno_number_by_class = {
        "RENAME_NOREPLACE_SUPPORTED": 0,
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL": errno.EINVAL,
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS": errno.ENOSYS,
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP": getattr(
            errno, "EOPNOTSUPP", errno.ENOTSUP
        ),
    }
    supported = classification == "RENAME_NOREPLACE_SUPPORTED"
    expected_common = _r8u_r3_common(
        artifact_type="lvef_c3_r8u_r3_publication_primitive_probe_v1",
        status="PASS_PUBLICATION_PRIMITIVE_PROBE",
        implementation_commit=implementation_commit,
    )
    errno_number = probe.get("primary_errno_number")
    expected_errno_name = (
        "NONE" if errno_number == 0
        else errno.errorcode.get(errno_number, "UNKNOWN")
        if type(errno_number) is int else "INVALID"
    )
    if (
        set(probe) != R8U_R3_PROBE_KEYS
        or any(
            not _exact_typed_value_equal(probe.get(key), value)
            for key, value in expected_common.items()
        )
        or probe.get("primary_primitive") != "RENAMEAT2_RENAME_NOREPLACE"
        or classification not in R8U_R3_PROCEEDABLE_PROBE_RESULTS
        or type(errno_number) is not int
        or errno_number != errno_number_by_class.get(classification)
        or probe.get("primary_errno") != expected_errno_name
        or probe.get("primary_returned_success") is not supported
        or probe.get("real_source_parent_identity_sha256")
        != candidate.get("source_parent_identity_sha256")
        or probe.get("real_target_parent_identity_sha256")
        != candidate.get("target_parent_identity_sha256")
        or probe.get("real_source_mount_identity_sha256")
        != candidate.get("source_mount_identity_sha256")
        or probe.get("real_target_mount_identity_sha256")
        != candidate.get("target_mount_identity_sha256")
        or probe.get("real_parents_same_mounted_filesystem") is not True
        or probe.get("probe_mount_identity_sha256")
        != candidate.get("source_mount_identity_sha256")
        or probe.get("probe_mount_matches_real_parents") is not True
        or probe.get("probe_source_present_after") is supported
        or probe.get("probe_target_present_after") is not supported
        or probe.get("probe_target_exact_after") is not supported
        or probe.get("probe_cleanup_passed") is not True
        or probe.get("probe_directories_created") != 2
        or type(probe.get("probe_directories_created")) is not int
        or probe.get("probe_directories_removed") != 2
        or type(probe.get("probe_directories_removed")) is not int
        or any(
            probe.get(field) != 0 or type(probe.get(field)) is not int
            for field in (
                "scientific_file_body_reads", "npz_body_reads",
                "dicom_body_reads", "dicom_extraction_executions",
            )
        )
    ):
        _fail("R8U_R3_PROBE_RECEIPT_INVALID")


def validate_r8u_r3_publication_receipt(
    run: sequential.FullRun,
) -> Mapping[str, Any]:
    implementation_commit = _current_r8u_r3_implementation_commit()
    candidate, _ = _load_private_json(R8U_R3_CANDIDATE_SEAL_PATH)
    probe, _ = _load_private_json(R8U_R3_PROBE_PATH)
    claim, _ = _load_private_json(R8U_R3_PUBLICATION_CLAIM_PATH)
    publication, _ = _load_private_json(R8U_R3_PUBLICATION_PATH)
    history = _r8u_r3_validate_r2_history(run)
    _r8u_r3_validate_candidate_seal_static(
        candidate, implementation_commit=implementation_commit,
        history=history,
    )
    _r8u_r3_validate_probe_receipt(
        probe, candidate=candidate,
        implementation_commit=implementation_commit,
    )
    _run, _authority, submission = _validate_r8u_r3_resume_submission(
        require_candidate_live=False
    )
    worker_projection = claim.get("worker_process_projection")
    if not isinstance(worker_projection, Mapping):
        _fail("R8U_PUBLICATION_RECEIPT_FAILED")
    expected_claim = _r8u_r3_publication_claim(
        implementation_commit=implementation_commit,
        resume_job_id=str(submission.get("resume_job_id", "")),
        history=history,
        candidate_seal=candidate, probe=probe,
        worker_process_projection=worker_projection,
    )
    target_projection = _r8u_r3_target_projection(run, candidate)
    fallback_used = probe["primary_result"] != "RENAME_NOREPLACE_SUPPORTED"
    actual_success = publication.get("real_rename_returned_success")
    actual_errno = publication.get("real_rename_errno")
    actual_errno_number = publication.get("real_rename_errno_number")
    if (
        type(actual_success) is not bool
        or not isinstance(actual_errno, str)
        or type(actual_errno_number) is not int
        or actual_errno_number < 0
        or (actual_success and actual_errno_number != 0)
        or (not actual_success and actual_errno_number == 0)
        or (
            "NONE" if actual_errno_number == 0
            else errno.errorcode.get(actual_errno_number, "UNKNOWN")
        ) != actual_errno
    ):
        _fail("R8U_PUBLICATION_RECEIPT_FAILED")
    result = _R8UR3RenameResult(
        actual_success, actual_errno_number, actual_errno
    )
    ruling = (
        "PUBLICATION_PASS" if actual_success
        else "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
    )
    expected_publication = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_batch16_publication_v1",
            status="PASS_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
            implementation_commit=implementation_commit,
        ),
        "extraction_candidate_seal_sha256": core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        "publication_primitive_probe_sha256": core.sha256_file(R8U_R3_PROBE_PATH),
        "publication_claim_sha256": core.sha256_file(
            R8U_R3_PUBLICATION_CLAIM_PATH
        ),
        "primitive_attempted": (
            "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
            if fallback_used else "RENAMEAT2_RENAME_NOREPLACE"
        ),
        "primary_result": probe["primary_result"],
        "primary_errno": probe["primary_errno"],
        "fallback_used": fallback_used,
        "fallback_primitive": (
            "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
            if fallback_used else "NONE"
        ),
        "rename_returned_success": actual_success,
        "real_rename_returned_success": actual_success,
        "real_rename_errno": actual_errno,
        "real_rename_errno_number": actual_errno_number,
        "real_rename_errno_classification": (
            _r8u_r3_real_rename_classification(result)
        ),
        "publication_ruling": ruling,
        "prepublication_candidate_sha256": candidate[
            "candidate_relative_file_projection_sha256"
        ],
        "postpublication_target_sha256": target_projection[
            "candidate_relative_file_projection_sha256"
        ],
        "source_absent": True,
        "target_exact": True,
        "candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "candidate_total_bytes": candidate["candidate_total_bytes"],
        "files_moved": R8U_R3_CANDIDATE_NPZ_FILES,
        "files_copied": 0,
        "files_deleted_independently": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "npz_body_reads": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    if (
        not _exact_typed_value_equal(claim, expected_claim)
        or not _exact_typed_value_equal(publication, expected_publication)
        or set(publication) != R8U_R3_PUBLICATION_KEYS
        or os.path.lexists(R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
    ):
        _fail("R8U_PUBLICATION_RECEIPT_FAILED")
    return publication


def _r8u_r3_resume_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r3_res_{implementation_commit[:8]}"


def _r8u_r3_resume_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N", _r8u_r3_resume_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R3_SCHEDULER_ROOT),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_r3_resume_authority(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    prefix_receipts: Sequence[str],
    history: Mapping[str, Any],
    candidate_seal_sha256: str,
    capacity_sha256: str,
    pre_qsub_process_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    _r8u_r3_validate_process_projection(
        pre_qsub_process_projection, worker_local=False
    )
    failed_epoch = _r8u_failed_recovery_epoch_authority()
    failed_epoch_sha = core.canonical_json_sha256(failed_epoch)
    if (
        tuple(prefix_receipts)
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or any(
            SHA_RE.fullmatch(value) is None
            for value in (
                qsub_environment_sha256, candidate_seal_sha256,
                capacity_sha256, failed_epoch_sha,
            )
        )
    ):
        _fail("R8U_R3_RESUME_AUTHORITY_INVALID")
    value = {
        **_r8u_r3_common(
            artifact_type=(
                "lvef_c3_r8u_r3_batch16_publication_resume_authority_v1"
            ),
            status="AUTHORIZED_FIXED_BATCH16_PUBLICATION_RESUME",
            implementation_commit=implementation_commit,
        ),
        "prior_implementation_commit": (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "historical_r8r_chain_authority": dict(
            _r8u_historical_r8r_chain_authority()
        ),
        "failed_r8u_recovery_epoch_authority_sha256": failed_epoch_sha,
        "r2_recovery_job_id": R8U_R2_COMPLETED_EXTRACTION_JOB_ID,
        "r2_recovery_log_sha256": history["r2_recovery_log_sha256"],
        "r2_recovery_capacity_receipt_sha256": history[
            "r2_recovery_capacity_receipt_sha256"
        ],
        "r2_recovery_authority_sha256": history["r2_recovery_authority_sha256"],
        "r2_recovery_submission_receipt_sha256": history[
            "r2_recovery_submission_receipt_sha256"
        ],
        "failed_partial_seal_sha256": history["failed_partial_seal_sha256"],
        "extraction_candidate_seal_sha256": candidate_seal_sha256,
        "resume_capacity_sha256": capacity_sha256,
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "runtime_validation_context": stages.SEALED_SCHEDULER_RUNTIME_REPLAY.value,
        "target_role": "extracted_cache/c3_batch_015/dicom_extraction",
        "cloud_requests_authorized": 0,
        "downloads_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "dicom_extraction_executions_authorized": 0,
        "echoprime_executions_authorized": 1,
        "gpu_executions_authorized": 1,
        "failed_partial_adoption_authorized": False,
        "failed_partial_mutation_authorized": False,
        "raw_dicom_deletion_authorized": False,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
        "maximum_new_qsub_submissions": 1,
        "pre_qsub_process_projection": dict(pre_qsub_process_projection),
    }
    if set(value) != R8U_R3_AUTHORITY_KEYS:
        _fail("R8U_R3_RESUME_AUTHORITY_INVALID")
    return value


def _r8u_r3_resume_submission_receipt(
    *,
    implementation_commit: str,
    resume_job_id: str,
    qsub_environment_sha256: str,
    resume_authority_sha256: str,
    candidate_seal_sha256: str,
    capacity_sha256: str,
    pre_qsub_process_projection: Mapping[str, Any],
    initial_qstat_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    _r8u_r3_validate_process_projection(
        pre_qsub_process_projection, worker_local=False
    )
    _r8u_r3_validate_initial_qstat_projection(
        initial_qstat_projection, resume_job_id=resume_job_id,
        implementation_commit=implementation_commit,
    )
    if (
        JOB_RE.fullmatch(resume_job_id) is None
        or any(
            SHA_RE.fullmatch(value) is None
            for value in (
                qsub_environment_sha256, resume_authority_sha256,
                candidate_seal_sha256, capacity_sha256,
            )
        )
    ):
        _fail("R8U_R3_RESUME_SUBMISSION_INVALID")
    command = _r8u_r3_resume_qsub_command(implementation_commit)
    value = {
        **_r8u_r3_common(
            artifact_type=(
                "lvef_c3_r8u_r3_batch16_publication_resume_submission_v1"
            ),
            status="PASS_EXACT_ONE_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
            implementation_commit=implementation_commit,
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "resume_job_name": _r8u_r3_resume_job_name(implementation_commit),
        "resume_job_id": resume_job_id,
        "resume_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "resume_authority_sha256": resume_authority_sha256,
        "extraction_candidate_seal_sha256": candidate_seal_sha256,
        "resume_capacity_sha256": capacity_sha256,
        "resume_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_R3_SCHEDULER_ROOT, "resume")
        ),
        "scheduler_submission_count": 1,
        "resume_is_array": False,
        "gpu_requested": True,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads_by_submitter": 0,
        "dicom_extraction_executions_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
        "pre_qsub_process_projection": dict(pre_qsub_process_projection),
        "initial_qstat_projection": dict(initial_qstat_projection),
    }
    if set(value) != R8U_R3_SUBMISSION_KEYS:
        _fail("R8U_R3_RESUME_SUBMISSION_INVALID")
    return value


def _r8u_r3_process_projection(
    *, environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    worker_local: bool,
) -> Mapping[str, Any]:
    command = ["/bin/ps", "-axo", "pid=,user=,command="]
    completed = runner(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if completed.returncode != 0 or completed.stderr or len(payload) > 16 * 1024 * 1024:
        _fail("R8U_R3_ACTIVE_PROCESS_CHECK_FAILED")
    try:
        text_value = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R8RControllerError("R8U_R3_ACTIVE_PROCESS_CHECK_FAILED") from exc
    markers = (
        "--run-array-task", "--run-cohort-finalizer",
        "--recover-batch3-preservation", "--run-continuation-array-task",
        "--run-continuation-finalizer", "--run-batch16-recovery",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
        "--run-r8u-r3-batch16-publication-resume",
        "--run-r8u-r3-continuation-17-19-array-task",
        "--run-r8u-r3-continuation-finalizer",
        "run_production_dicom_extraction", "run_production_echoprime",
        "preserve_lvef_c3_production_batch",
        "retire_lvef_c3_extracted_cache", "finalize_lvef_c3_production",
        "lvef_c3_r8u_rec_", "lvef_c3_r8u_seq_", "lvef_c3_r8u_fin_",
        "lvef_c3_r8u_r3_",
    )
    matching = 0
    worker_self_seen = 0
    for line in text_value.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or parts[1] != environment["USER"]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            _fail("R8U_R3_ACTIVE_PROCESS_CHECK_FAILED")
        if worker_local and pid == os.getpid():
            command_text = parts[2].rstrip()
            if (
                "lvef_c3_r8r_recovery_continuation.py" not in command_text
                or not command_text.endswith(
                    "--run-r8u-r3-batch16-publication-resume"
                )
            ):
                _fail("R8U_R3_WORKER_PROCESS_IDENTITY_INVALID")
            worker_self_seen += 1
            continue
        if any(marker in parts[2] for marker in markers):
            matching += 1
    value = {
        "status": (
            "PASS_ZERO_COMPETING_R8U_R3_WORKER_PROCESSES"
            if worker_local
            else "PASS_ZERO_COMPETING_R8U_R3_PROCESSES"
        ),
        "matching_processes": matching,
        "process_snapshot_count": 1,
        "ps_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": command})),
        "ps_stdout_sha256": _sha256_bytes(payload),
    }
    if (
        set(value) != R8U_R3_PROCESS_PROJECTION_KEYS
        or matching != 0
        or (worker_local and worker_self_seen != 1)
    ):
        _fail("R8U_R3_ACTIVE_MATCHING_PROCESS_EXISTS")
    return value


def _r8u_r3_validate_process_projection(
    value: Mapping[str, Any], *, worker_local: bool
) -> None:
    expected_status = (
        "PASS_ZERO_COMPETING_R8U_R3_WORKER_PROCESSES"
        if worker_local else "PASS_ZERO_COMPETING_R8U_R3_PROCESSES"
    )
    if (
        set(value) != R8U_R3_PROCESS_PROJECTION_KEYS
        or value.get("status") != expected_status
        or type(value.get("matching_processes")) is not int
        or value.get("matching_processes") != 0
        or type(value.get("process_snapshot_count")) is not int
        or value.get("process_snapshot_count") != 1
        or any(
            SHA_RE.fullmatch(str(value.get(field, ""))) is None
            for field in ("ps_argv_sha256", "ps_stdout_sha256")
        )
    ):
        _fail("R8U_R3_PROCESS_PROJECTION_INVALID")


def _r8u_r3_validate_initial_qstat_projection(
    value: Mapping[str, Any], *, resume_job_id: str,
    implementation_commit: str,
) -> None:
    body = {key: observed for key, observed in value.items()
            if key != "qstat_projection_sha256"}
    if (
        set(value) != R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS
        or value.get("status")
        != "PASS_EXACT_ONE_R8U_R3_RESUME_JOB_ZERO_COMPETITORS"
        or value.get("resume_job_id") != resume_job_id
        or value.get("resume_job_name")
        != _r8u_r3_resume_job_name(implementation_commit)
        or value.get("state") not in {"r", "qw", "t", "Rr"}
        or value.get("category")
        != ("running" if value.get("state") in {"r", "t", "Rr"} else "pending")
        or type(value.get("target_matches")) is not int
        or value.get("target_matches") != 1
        or type(value.get("competing_matching_jobs")) is not int
        or value.get("competing_matching_jobs") != 0
        or type(value.get("qstat_snapshot_count")) is not int
        or value.get("qstat_snapshot_count") != 1
        or value.get("qstat_projection_sha256")
        != core.canonical_json_sha256(body)
    ):
        _fail("R8U_R3_INITIAL_QSTAT_INVALID")


def _validate_r8u_r3_initial_qstat(
    *,
    environment: Mapping[str, str],
    resume_job_id: str,
    implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    """Capture the phase's sole qstat snapshot; never poll."""

    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0 or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_R3_INITIAL_QSTAT_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8U_R3_INITIAL_QSTAT_INVALID") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local(element) for element in list(root)]
    if (
        local(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(
            name not in {"queue_info", "job_info"}
            for name in direct_children
        )
    ):
        _fail("R8U_R3_INITIAL_QSTAT_INVALID")
    records: list[tuple[str, str, str, str]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local(child), []).append(child.text or "")
        if any(len(fields.get(key, ())) != 1 for key in ("JB_job_number", "JB_name", "state")):
            _fail("R8U_R3_INITIAL_QSTAT_INVALID")
        records.append((
            fields["JB_job_number"][0], fields["JB_name"][0],
            fields["state"][0], str(job.get("state", "")),
        ))
    matching_names = re.compile(
        r"lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
        r"r8u_(?:rec|seq|fin)|r8u_r3_(?:res|seq|fin))_[0-9a-f]{8}"
        r"|c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}"
    )
    competing = [
        record for record in records
        if record[0] != resume_job_id and matching_names.fullmatch(record[1])
    ]
    matches = [record for record in records if record[0] == resume_job_id]
    if competing or len(matches) != 1:
        _fail("R8U_R3_INITIAL_QSTAT_TOPOLOGY_INVALID")
    _job, name, state, category = matches[0]
    expected_category = "running" if state in {"r", "t", "Rr"} else "pending"
    if (
        name != _r8u_r3_resume_job_name(implementation_commit)
        or state not in {"r", "qw", "t", "Rr"}
        or category != expected_category
    ):
        _fail("R8U_R3_INITIAL_QSTAT_STATE_INVALID")
    body: dict[str, Any] = {
        "status": "PASS_EXACT_ONE_R8U_R3_RESUME_JOB_ZERO_COMPETITORS",
        "resume_job_id": resume_job_id,
        "resume_job_name": name,
        "state": state,
        "category": category,
        "target_matches": len(matches),
        "competing_matching_jobs": len(competing),
        "qstat_snapshot_count": 1,
    }
    value = {
        **body,
        "qstat_projection_sha256": core.canonical_json_sha256(body),
    }
    _r8u_r3_validate_initial_qstat_projection(
        value, resume_job_id=resume_job_id,
        implementation_commit=implementation_commit,
    )
    return value


def _validate_r8u_r3_resume_submission(
    *, current_job_id: str | None = None, wait: bool = False,
    require_candidate_live: bool = True,
) -> tuple[sequential.FullRun, Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(R8U_R3_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8U_R3_RESUME_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    implementation_commit = _current_r8u_r3_implementation_commit()
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id or "R8U_R3_RESUME_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r3=True,
    )
    history = _r8u_r3_validate_r2_history(run)
    if require_candidate_live:
        candidate = validate_r8u_r3_extraction_candidate_seal(
            run, history=history
        )
    else:
        candidate, _ = _load_private_json(R8U_R3_CANDIDATE_SEAL_PATH)
        _r8u_r3_validate_candidate_seal_static(
            candidate, implementation_commit=implementation_commit,
            history=history,
        )
    capacity_value, capacity_payload = _load_private_json(R8U_R3_CAPACITY_PATH)
    candidate_sha = core.sha256_file(R8U_R3_CANDIDATE_SEAL_PATH)
    try:
        capacity.validate_fixed_r8u_r3_batch16_publication_resume_capacity(
            run.plan, capacity_value,
            completed_extraction_candidate_seal_sha256=candidate_sha,
            completed_extraction_candidate_bytes=int(candidate["candidate_total_bytes"]),
            r8u_candidate_authority_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R3_RESUME_CAPACITY_INVALID") from exc
    authority, authority_payload = _load_private_json(R8U_R3_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_R3_SUBMISSION_PATH)
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    expected_authority = _r8u_r3_resume_authority(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=str(authority.get("qsub_environment_sha256", "")),
        prefix_receipts=prefix, history=history,
        candidate_seal_sha256=candidate_sha,
        capacity_sha256=_sha256_bytes(capacity_payload),
        pre_qsub_process_projection=(
            authority.get("pre_qsub_process_projection", {})
            if isinstance(authority.get("pre_qsub_process_projection"), Mapping)
            else {}
        ),
    )
    job_id = str(submission.get("resume_job_id", ""))
    expected_submission = _r8u_r3_resume_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=job_id,
        qsub_environment_sha256=str(authority.get("qsub_environment_sha256", "")),
        resume_authority_sha256=_sha256_bytes(authority_payload),
        candidate_seal_sha256=candidate_sha,
        capacity_sha256=_sha256_bytes(capacity_payload),
        pre_qsub_process_projection=(
            submission.get("pre_qsub_process_projection", {})
            if isinstance(submission.get("pre_qsub_process_projection"), Mapping)
            else {}
        ),
        initial_qstat_projection=(
            submission.get("initial_qstat_projection", {})
            if isinstance(submission.get("initial_qstat_projection"), Mapping)
            else {}
        ),
    )
    if (
        not _exact_typed_value_equal(authority, expected_authority)
        or not _exact_typed_value_equal(submission, expected_submission)
        or submission.get("pre_qsub_process_projection")
        != authority.get("pre_qsub_process_projection")
        or capacity_value.get("status") != R8U_R3_CAPACITY_STATUS
        or (current_job_id is not None and current_job_id != job_id)
    ):
        _fail("R8U_R3_RESUME_SUBMISSION_INVALID")
    return run, authority, submission


def _r8u_r3_require_submit_outputs_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    expected_absent = (
        R8U_R3_ROOT, R8U_R3_PUBLICATION_CLAIM_ROOT,
        R8U_R3_CONTINUATION_ROOT, paths["extraction"],
        paths["extraction_ledger"], paths["pooling_ledger"],
        paths["eligibility_ledger"], paths["echoprime"],
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        ATTEMPT_ROOT / "cache_retirement_authorizations"
        / f"{R8U_FIXED_BATCH_ID}.authorization.json",
        paths["final_ledger"], paths["final_receipt"],
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8U_R3_RESUME_OUTPUT_COLLISION")


def submit_r8u_r3_batch16_publication_resume(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    """Seal the completed extraction and submit exactly one resume job."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r3_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    pre_qsub_process_projection = _r8u_r3_process_projection(
        environment=environment, runner=process_runner, worker_local=False
    )
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R3_RESUME_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
        r8u_r3=True,
    )
    _validate_original_controls()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    _r8u_historical_r8r_chain_authority()
    history = _r8u_r3_validate_r2_history(run)
    _r8u_r3_require_submit_outputs_absent(run)
    projection = _r8u_r3_candidate_projection(run)
    candidate = _r8u_r3_candidate_seal(
        run=run, implementation_commit=implementation_commit,
        history=history, projection=projection,
    )
    candidate_payload = _canonical_bytes(candidate)
    candidate_sha = _sha256_bytes(candidate_payload)
    _create_private_directory_no_clobber(R8U_R3_ROOT)
    written_candidate_sha = _write_private_json(
        R8U_R3_CANDIDATE_SEAL_PATH, candidate
    )
    if written_candidate_sha != candidate_sha:
        _fail("R8U_R3_CANDIDATE_SEAL_SCHEMA_INVALID")
    try:
        capacity_value = capacity.probe_fixed_r8u_r3_batch16_publication_resume_capacity(
            run.plan,
            completed_extraction_candidate_seal_sha256=candidate_sha,
            completed_extraction_candidate_bytes=int(candidate["candidate_total_bytes"]),
            r8u_candidate_authority_repair_commit=implementation_commit,
            process_runner=capacity_process_runner,
        )
        capacity.validate_fixed_r8u_r3_batch16_publication_resume_capacity(
            run.plan, capacity_value,
            completed_extraction_candidate_seal_sha256=candidate_sha,
            completed_extraction_candidate_bytes=int(candidate["candidate_total_bytes"]),
            r8u_candidate_authority_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R3_RESUME_CAPACITY_INVALID") from exc
    if capacity_value.get("status") != R8U_R3_CAPACITY_STATUS:
        raise R8RControllerError(
            "R8U_R3_RESUME_CAPACITY_BLOCKED",
            capacity_deficits=_r8u_capacity_deficits(capacity_value),
        )
    _create_private_directory_no_clobber(R8U_R3_SCHEDULER_ROOT)
    capacity_sha = _write_private_json(R8U_R3_CAPACITY_PATH, capacity_value)
    authority = _r8u_r3_resume_authority(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix, history=history,
        candidate_seal_sha256=candidate_sha, capacity_sha256=capacity_sha,
        pre_qsub_process_projection=pre_qsub_process_projection,
    )
    authority_sha = _write_private_json(R8U_R3_AUTHORITY_PATH, authority)
    resume_job_id = scheduler._capture_qsub(
        "resume", _r8u_r3_resume_qsub_command(implementation_commit),
        root=R8U_R3_SCHEDULER_ROOT, environment=environment,
        runner=qsub_runner,
    )
    initial_qstat = _validate_r8u_r3_initial_qstat(
        environment=environment, resume_job_id=resume_job_id,
        implementation_commit=implementation_commit, runner=qstat_runner,
    )
    receipt = _r8u_r3_resume_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id,
        qsub_environment_sha256=environment_sha,
        resume_authority_sha256=authority_sha,
        candidate_seal_sha256=candidate_sha,
        capacity_sha256=capacity_sha,
        pre_qsub_process_projection=pre_qsub_process_projection,
        initial_qstat_projection=initial_qstat,
    )
    _write_private_json(R8U_R3_SUBMISSION_PATH, receipt)
    return {
        "status": "BATCH16_PUBLICATION_RESUME_SUBMITTED_AWAITING_TERMINAL",
        "resume_job_id": resume_job_id,
        "capacity_status": R8U_R3_CAPACITY_STATUS,
        "candidate_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "candidate_status": "PASS",
        "initial_state": initial_qstat["state"],
        "new_qsub_submissions": 1,
        "login_node_polling_started": False,
        "continuation_submitted": False,
        "finalizer_submitted": False,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
    }


def _r8u_r3_terminal_receipt(
    *, run: sequential.FullRun, final_receipt: Mapping[str, Any]
) -> Mapping[str, Any]:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    required = {
        "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_PATH,
        "extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_PATH,
        "publication_primitive_probe_sha256": R8U_R3_PROBE_PATH,
        "publication_claim_sha256": R8U_R3_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R3_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R3_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R3_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R3_SUBMISSION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"] / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            ATTEMPT_ROOT / "cache_retirement_authorizations"
            / f"{R8U_FIXED_BATCH_ID}.authorization.json"
        ),
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    try:
        hashes = {key: core.sha256_file(path) for key, path in required.items()}
    except Exception as exc:
        raise R8RControllerError("R8U_R3_TERMINAL_AUTHORITY_INVALID") from exc
    value = {
        **_r8u_r3_common(
            artifact_type=(
                "lvef_c3_r8u_r3_batch16_publication_resume_terminal_v1"
            ),
            status=R8U_R3_STATUS,
            implementation_commit=_current_r8u_r3_implementation_commit(),
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        **hashes,
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "object_substitution_count": int(final_receipt["object_substitution_count"]),
        "unaccounted_multiframe_objects": int(
            final_receipt["unaccounted_multiframe_objects"]
        ),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "canonical_extraction_cache_retired": final_receipt[
            "extracted_cache_retired"
        ],
        "failed_partial_cache_retained": True,
        "source_candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "echoprime_executions": 1,
        "embedding_generations": 1,
        "gpu_executions": 1,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R3_TERMINAL_KEYS:
        _fail("R8U_R3_TERMINAL_AUTHORITY_INVALID")
    return value


def run_r8u_r3_batch16_publication_resume(
    *,
    dependencies: sequential.FullDependencies | None = None,
    probe_invoker: Callable[[Path, Path], Any] | None = None,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Publish the completed extraction, then resume at EchoPrime."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if JOB_RE.fullmatch(job_id) is None or task_text not in {"", "undefined"}:
        _fail("R8U_R3_RESUME_SCHEDULER_CONTEXT_INVALID")
    run, _authority, _submission = _validate_r8u_r3_resume_submission(
        current_job_id=job_id, wait=True
    )
    implementation_commit = _current_r8u_r3_implementation_commit()
    history = _r8u_r3_validate_r2_history(run)
    candidate = validate_r8u_r3_extraction_candidate_seal(run, history=history)
    if any(
        os.path.lexists(path)
        for path in (
            R8U_R3_PROBE_PATH, R8U_R3_PUBLICATION_CLAIM_ROOT,
            R8U_R3_PUBLICATION_PATH, R8U_R3_EXTRACTION_TRANSITION_ROOT,
            R8U_R3_TERMINAL_PATH,
        )
    ):
        _fail("R8U_R3_RESUME_OUTPUT_COLLISION")
    dependency = sequential.resolve_dependencies(dependencies)
    try:
        dependency.environment_validator(
            run.authority.environment_receipt,
            expected_environment_receipt_sha256=(
                run.runtime_authority["environment_receipt_sha256"]
            ),
            scientific_governing_commit=run.authority.governing_commit,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        if core.sha256_file(run.authority.checkpoint) != run.runtime_authority[
            "checkpoint_sha256"
        ]:
            _fail("R8U_R3_CHECKPOINT_AUTHORITY_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R3_PREBODY_AUTHORITY_FAILED", stage="PREBODY_AUTHORITY"
        ) from exc
    try:
        probe = _r8u_r3_primitive_probe(
            run=run, implementation_commit=implementation_commit,
            candidate_seal=candidate, invoker=probe_invoker,
        )
        # The probe touched only empty directories; revalidate the complete
        # candidate and both real parents before acquiring the claim.
        candidate = validate_r8u_r3_extraction_candidate_seal(
            run, history=history
        )
        worker_environment, _ = scheduler.build_qsub_environment()
        worker_process_projection = _r8u_r3_process_projection(
            environment=worker_environment, runner=process_runner,
            worker_local=True,
        )
        claim = _r8u_r3_publication_claim(
            implementation_commit=implementation_commit,
            resume_job_id=job_id, history=history,
            candidate_seal=candidate, probe=probe,
            worker_process_projection=worker_process_projection,
        )
        claim_sha = _r8u_r3_create_publication_claim(claim)
        candidate = validate_r8u_r3_extraction_candidate_seal(
            run, history=history
        )
        publication = _r8u_r3_publish_candidate(
            run=run, implementation_commit=implementation_commit,
            candidate_seal=candidate, probe=probe,
            publication_claim_sha256=claim_sha,
            primary_invoker=primary_invoker,
            fallback_invoker=fallback_invoker,
        )
        validate_r8u_r3_publication_receipt(run)
    except R8RControllerError as exc:
        raise R8RControllerError(exc.code, stage="EXTRACTION_PUBLICATION") from exc
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    object_keys = {str(row["source_object_key"]) for row in planned["objects"]}
    try:
        stages.advance_stage_ledger(
            input_ledger=paths["download_ledger"],
            output_ledger=paths["extraction_ledger"],
            receipt_root=R8U_R3_EXTRACTION_TRANSITION_ROOT,
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", core.sha256_file(
                    paths["extraction"] / "dicom_audit.restricted.csv"
                )),
                ("EXTRACTION_COMPLETE", core.sha256_file(
                    paths["extraction"] / "extraction_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
        embedding_summary = dependency.echoprime(
            extraction_manifest=(
                paths["extraction"] / "extraction_manifest.restricted.csv"
            ),
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
            checkpoint=run.authority.checkpoint,
            environment_receipt=run.authority.environment_receipt,
            orchestration_contract=run.contract_path,
            batch_plan=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID,
            batch_output_root=paths["batch_root"],
            batch_size=dependency.echoprime_batch_size,
            seed=20260803,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            requirements=run.requirements,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        # First independent post-EchoPrime candidate check.
        _r8u_r3_target_projection(run, candidate)
        stages.advance_stage_ledger(
            input_ledger=paths["extraction_ledger"],
            output_ledger=paths["pooling_ledger"],
            receipt_root=paths["echoprime"] / "transition_receipts",
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("EMBEDDING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "clip_manifest.restricted.csv"
                )),
                ("STUDY_POOLING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "study_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R3_BATCH16_ECHOPRIME_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R3_BATCH16_ECHOPRIME_FAILED"
            ),
            stage="ECHOPRIME_EMBEDDING",
        ) from exc
    try:
        # Second independent check at the preservation/retirement boundary.
        _r8u_r3_target_projection(run, candidate)
        preserved = dependency.preserve(
            contract_path=run.contract_path, plan_path=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID, attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root, output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=run.scheduler_job_identity,
            input_ledger=paths["pooling_ledger"], requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            artifact_validation_context=(
                preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
            ),
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=R8U_FIXED_BATCH_ID, paths=paths
        )
        dependency.retire(
            run=run, batch_id=R8U_FIXED_BATCH_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            test_only_synthetic_full_scope=False,
            artifact_validation_context=(
                retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
            ),
        )
        final_receipt = dependency.finalize_batch(
            run=run, batch_id=R8U_FIXED_BATCH_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R3_BATCH16_FINALIZATION_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R3_BATCH16_FINALIZATION_FAILED"
            ),
            stage="PRESERVATION_RETIREMENT_FINALIZATION",
        ) from exc
    if (
        preserved.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or final_receipt.get("status") != "PASS_BATCH_FINALIZED"
        or final_receipt.get("raw_dicoms_retained") is not True
        or final_receipt.get("extracted_cache_retired") is not True
        or final_receipt.get("n_successfully_extracted_cines")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_clip_embeddings") != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_object_technical_dispositions") != 0
        or final_receipt.get("n_blocking_failures") != 0
        or final_receipt.get("n_new_no_cine_studies") != 0
        or final_receipt.get("object_substitution_count") != 0
        or final_receipt.get("unaccounted_multiframe_objects") != 0
        or os.path.lexists(paths["extraction"] / "clips")
    ):
        _fail("R8U_R3_BATCH16_FINALIZATION_INVALID")
    # After retirement, validate only durable receipts; clips are expected gone.
    _r8u_r3_validate_r2_history(run)
    terminal = _r8u_r3_terminal_receipt(run=run, final_receipt=final_receipt)
    _write_private_json(R8U_R3_TERMINAL_PATH, terminal)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return {
        **dict(terminal),
        "publication_ruling": publication["publication_ruling"],
        "embedding_summary_status": embedding_summary.get("status"),
    }


def _r8u_r3_resume_accounting_receipt(
    *, implementation_commit: str, resume_job_id: str,
    accounting: Mapping[str, Any]
) -> Mapping[str, Any]:
    validated = _validate_recovery_accounting_projection(
        accounting, expected_job_id=resume_job_id
    )
    value = {
        **_r8u_r3_common(
            artifact_type=(
                "lvef_c3_r8u_r3_batch16_publication_resume_accounting_v1"
            ),
            status="PASS_RESUME_QACCT_FAILED_0_EXIT_0",
            implementation_commit=implementation_commit,
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "resume_job_id": resume_job_id,
        "failed": 0,
        "exit_status": 0,
        "accounting_projection": dict(validated),
    }
    if set(value) != finalizer.R8U_R3_ACCOUNTING_KEYS:
        _fail("R8U_R3_RESUME_ACCOUNTING_INVALID")
    return value


def _validate_r8u_r3_resume_accounting() -> Mapping[str, Any]:
    _run, _authority, submission = _validate_r8u_r3_resume_submission(
        require_candidate_live=False
    )
    resume_job_id = str(submission.get("resume_job_id", ""))
    value, _ = _load_private_json(R8U_R3_ACCOUNTING_PATH)
    accounting = value.get("accounting_projection")
    if not isinstance(accounting, Mapping):
        _fail("R8U_R3_RESUME_ACCOUNTING_INVALID")
    expected = _r8u_r3_resume_accounting_receipt(
        implementation_commit=_current_r8u_r3_implementation_commit(),
        resume_job_id=resume_job_id, accounting=accounting,
    )
    if not _exact_typed_value_equal(value, expected):
        _fail("R8U_R3_RESUME_ACCOUNTING_INVALID")
    return value


def validate_r8u_r3_resume_terminal() -> Mapping[str, Any]:
    run, _authority, _submission = _validate_r8u_r3_resume_submission(
        require_candidate_live=False
    )
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=R8U_FIXED_BATCH_ID
    )
    value, _ = _load_private_json(R8U_R3_TERMINAL_PATH)
    expected = _r8u_r3_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    if (
        not _exact_typed_value_equal(value, expected)
        or value.get("status") != R8U_R3_STATUS
        or os.path.lexists(
            R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
        )
        or os.path.lexists(
            sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
            / "clips"
        )
    ):
        _fail("R8U_R3_TERMINAL_RECEIPT_INVALID")
    _r8u_r3_validate_r2_history(run)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return value


def validate_r8u_r3_frozen_partial_evidence() -> Mapping[str, Any]:
    """R3-safe closed validator for the immutable 4,757-file partial."""

    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R3_FROZEN_PARTIAL_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r3=True,
    )
    history = _r8u_r3_validate_r2_history(run)
    value, _ = _load_private_json(R8U_FAILED_PARTIAL_SEAL_PATH)
    if (
        value.get("status") != "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE"
        or core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH)
        != history["failed_partial_seal_sha256"]
    ):
        _fail("R8U_R3_FAILED_PARTIAL_SEAL_INVALID")
    return value


def _r8u_r3_continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r3_seq_{implementation_commit[:8]}"


def _r8u_r3_continuation_finalizer_job_name(
    implementation_commit: str,
) -> str:
    return f"lvef_c3_r8u_r3_fin_{implementation_commit[:8]}"


def _r8u_r3_continuation_array_command(
    implementation_commit: str,
) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r3_continuation_array_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R3_CONTINUATION_SCHEDULER_ROOT),
        "-t", R8U_FIXED_CONTINUATION_TASK_RANGE,
        "-tc", str(R8U_FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_r3_continuation_finalizer_command(
    implementation_commit: str, array_job_id: str
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8U_R3_CONTINUATION_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r3_continuation_finalizer_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R3_CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid", array_job_id, "-l", "h_rt=12:00:00",
        "-pe", "omp", "4", "-l", "mem_per_core=8G", str(RUNNER_PATH),
    ]


def _r8u_r3_continuation_links() -> Mapping[str, str]:
    paths = {
        "extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_PATH,
        "publication_primitive_probe_sha256": R8U_R3_PROBE_PATH,
        "publication_claim_sha256": R8U_R3_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R3_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R3_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R3_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R3_SUBMISSION_PATH,
        "resume_accounting_sha256": R8U_R3_ACCOUNTING_PATH,
        "resume_terminal_receipt_sha256": R8U_R3_TERMINAL_PATH,
    }
    value = {key: core.sha256_file(path) for key, path in paths.items()}
    if set(value) != R8U_R3_CONTINUATION_LINK_KEYS:
        _fail("R8U_R3_CONTINUATION_CHAIN_INVALID")
    return dict(sorted(value.items()))


def _r8u_r3_continuation_claim(
    *, run: sequential.FullRun, implementation_commit: str,
    qsub_environment_sha256: str, prefix_receipts: Sequence[str]
) -> Mapping[str, Any]:
    if (
        len(prefix_receipts) != 16
        or tuple(prefix_receipts[:15])
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or any(SHA_RE.fullmatch(value) is None for value in prefix_receipts)
    ):
        _fail("R8U_R3_CONTINUATION_CLAIM_INVALID")
    value = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_fixed_continuation_claim_v1",
            status="AUTHORIZED_FIXED_CONTINUATION_17_19",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r3_continuation_links()),
        "prior_implementation_commit": (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1,
        "total_new_qsub_maximum": 3,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
    if set(value) != R8U_R3_CONTINUATION_CLAIM_KEYS:
        _fail("R8U_R3_CONTINUATION_CLAIM_INVALID")
    return value


def _r8u_r3_continuation_submission_receipt(
    *, implementation_commit: str, resume_job_id: str,
    array_job_id: str, finalizer_job_id: str,
    qsub_environment_sha256: str, continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        any(
            JOB_RE.fullmatch(value) is None
            for value in (resume_job_id, array_job_id, finalizer_job_id)
        )
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8U_R3_CONTINUATION_SUBMISSION_INVALID")
    array_command = _r8u_r3_continuation_array_command(implementation_commit)
    finalizer_command = _r8u_r3_continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    value = {
        **_r8u_r3_common(
            artifact_type="lvef_c3_r8u_r3_fixed_continuation_submission_v1",
            status="PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r3_continuation_links()),
        "resume_job_id": resume_job_id,
        "array_job_name": _r8u_r3_continuation_array_job_name(
            implementation_commit
        ),
        "finalizer_job_name": _r8u_r3_continuation_finalizer_job_name(
            implementation_commit
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": finalizer_command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(
            _qsub_evidence_authority(
                R8U_R3_CONTINUATION_SCHEDULER_ROOT, "array"
            )
        ),
        "finalizer_qsub_evidence": dict(
            _qsub_evidence_authority(
                R8U_R3_CONTINUATION_SCHEDULER_ROOT, "finalizer"
            )
        ),
        "scheduler_submission_count": 2,
        "total_new_qsub_submissions": 3,
        "scheduler_submission_maximum": 3,
        "array_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R3_CONTINUATION_SUBMISSION_KEYS:
        _fail("R8U_R3_CONTINUATION_SUBMISSION_INVALID")
    return value


def _validate_r8u_r3_continuation_chain(
    run: sequential.FullRun,
    *, current_job_id: str | None, role: str, wait: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if role not in {"array", "finalizer"}:
        _fail("R8U_R3_CONTINUATION_ROLE_INVALID")
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(R8U_R3_CONTINUATION_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8U_R3_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    validate_r8u_r3_resume_terminal()
    _validate_r8u_r3_resume_accounting()
    implementation_commit = _current_r8u_r3_implementation_commit()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    claim, claim_payload = _load_private_json(R8U_R3_CONTINUATION_CLAIM_PATH)
    submission, _ = _load_private_json(R8U_R3_CONTINUATION_SUBMISSION_PATH)
    qsub_sha = str(claim.get("qsub_environment_sha256", ""))
    expected_claim = _r8u_r3_continuation_claim(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=qsub_sha, prefix_receipts=prefix,
    )
    _run, _authority, resume_submission = _validate_r8u_r3_resume_submission(
        require_candidate_live=False
    )
    array_job_id = str(submission.get("array_job_id", ""))
    finalizer_job_id = str(submission.get("finalizer_job_id", ""))
    expected_submission = _r8u_r3_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=str(resume_submission.get("resume_job_id", "")),
        array_job_id=array_job_id, finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=qsub_sha,
        continuation_claim_sha256=_sha256_bytes(claim_payload),
    )
    expected_job_id = array_job_id if role == "array" else finalizer_job_id
    if (
        not _exact_typed_value_equal(claim, expected_claim)
        or not _exact_typed_value_equal(submission, expected_submission)
        or (current_job_id is not None and current_job_id != expected_job_id)
    ):
        _fail("R8U_R3_CONTINUATION_CHAIN_INVALID")
    return claim, submission


def validate_r8u_r3_continuation_worker_submission(
    run: sequential.FullRun,
    *, current_job_id: str, role: str,
) -> Mapping[str, Any]:
    if (
        not isinstance(run, sequential.FullRun)
        or run.attempt_id != ORIGINAL_ATTEMPT_ID
        or run.plan_sha256 != ORIGINAL_PLAN_SHA256
        or run.authority.governing_commit != ORIGINAL_SCIENTIFIC_COMMIT
        or JOB_RE.fullmatch(current_job_id) is None
    ):
        _fail("R8U_R3_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return _validate_r8u_r3_continuation_chain(
        run, current_job_id=current_job_id, role=role, wait=True
    )[1]


def _validate_r8u_r3_initial_continuation_qstat(
    *, environment: Mapping[str, str], array_job_id: str,
    finalizer_job_id: str, implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0 or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_R3_INITIAL_QSTAT_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8U_R3_INITIAL_QSTAT_INVALID") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    records: list[tuple[str, str, str, str, tuple[str, ...]]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local(child), []).append(child.text or "")
        if any(len(fields.get(key, ())) != 1 for key in ("JB_job_number", "JB_name", "state")):
            _fail("R8U_R3_INITIAL_QSTAT_INVALID")
        task_fields = tuple(
            value
            for key in ("JAT_task_number", "ja_task_id", "tasks")
            for value in fields.get(key, ())
        )
        records.append((
            fields["JB_job_number"][0], fields["JB_name"][0],
            fields["state"][0], str(job.get("state", "")), task_fields,
        ))
    arrays = [row for row in records if row[0] == array_job_id]
    finals = [row for row in records if row[0] == finalizer_job_id]
    if len(arrays) < 1 or len(finals) != 1:
        _fail("R8U_R3_INITIAL_QSTAT_TOPOLOGY_INVALID")
    if any(
        row[1] != _r8u_r3_continuation_array_job_name(implementation_commit)
        or row[2] not in {"r", "qw", "t", "Rr"}
        for row in arrays
    ) or (
        finals[0][1]
        != _r8u_r3_continuation_finalizer_job_name(implementation_commit)
        or finals[0][2] != "hqw"
        or finals[0][3] != "pending"
    ):
        _fail("R8U_R3_INITIAL_QSTAT_STATE_INVALID")
    task_text = ",".join(value for row in arrays for value in row[4])
    covered: set[int] = set()
    for token in filter(None, task_text.split(",")):
        match = re.fullmatch(r"(1[7-9])(?:-(1[7-9])(?::1)?)?", token)
        if match is None:
            _fail("R8U_R3_INITIAL_QSTAT_TASK_RANGE_INVALID")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        covered.update(range(first, last + 1))
    if covered != set(R8U_FIXED_CONTINUATION_TASK_IDS):
        _fail("R8U_R3_INITIAL_QSTAT_TASK_RANGE_INVALID")
    return {
        "status": "PASS_INITIAL_R8U_R3_SCHEDULER_TOPOLOGY",
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "finalizer_state": "HELD",
    }


def submit_r8u_r3_continuation_17_19(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Future-only submitter after separately confirmed R3 terminal PASS."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r3_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    _validate_r8u_no_active_processes(environment, runner=process_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R3_CONTINUATION_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
        r8u_r3=True,
    )
    validate_r8u_r3_resume_terminal()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    if (
        os.path.lexists(R8U_R3_ACCOUNTING_PATH)
        or os.path.lexists(R8U_R3_CONTINUATION_ROOT)
    ):
        _fail("R8U_R3_CONTINUATION_OUTPUT_COLLISION")
    _run, _authority, resume_submission = _validate_r8u_r3_resume_submission(
        require_candidate_live=False
    )
    resume_job_id = str(resume_submission.get("resume_job_id", ""))
    if environment_sha != resume_submission.get("qsub_environment_sha256"):
        _fail("R8U_R3_CONTINUATION_QSUB_ENVIRONMENT_INVALID")
    accounting_projection = _query_recovery_accounting(
        recovery_job_id=resume_job_id,
        environment=environment, runner=qacct_runner,
    )
    accounting = _r8u_r3_resume_accounting_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id, accounting=accounting_projection,
    )
    _write_private_json(R8U_R3_ACCOUNTING_PATH, accounting)
    _create_private_directory_no_clobber(R8U_R3_CONTINUATION_ROOT)
    _create_private_directory_no_clobber(R8U_R3_CONTINUATION_SCHEDULER_ROOT)
    claim = _r8u_r3_continuation_claim(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha, prefix_receipts=prefix,
    )
    claim_sha = _write_private_json(R8U_R3_CONTINUATION_CLAIM_PATH, claim)
    array_job_id = scheduler._capture_qsub(
        "array", _r8u_r3_continuation_array_command(implementation_commit),
        root=R8U_R3_CONTINUATION_SCHEDULER_ROOT,
        environment=environment, runner=qsub_runner,
        parser=_parse_r8u_array_qsub_stdout,
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer",
        _r8u_r3_continuation_finalizer_command(
            implementation_commit, array_job_id
        ),
        root=R8U_R3_CONTINUATION_SCHEDULER_ROOT,
        environment=environment, runner=qsub_runner,
    )
    submission = _r8u_r3_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id, array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha,
        continuation_claim_sha256=claim_sha,
    )
    _write_private_json(R8U_R3_CONTINUATION_SUBMISSION_PATH, submission)
    initial = _validate_r8u_r3_initial_continuation_qstat(
        environment=environment, array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        implementation_commit=implementation_commit, runner=qstat_runner,
    )
    return {
        "status": "R8U_R3_CONTINUATION_17_19_SUBMITTED",
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "new_qsub_submissions": 2,
        "total_new_qsub_submissions": 3,
        "initial_scheduler_topology": initial,
        "cloud_requests": 0,
    }


def run_r8u_r3_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or not task_text.isdigit()
        or int(task_text) not in R8U_FIXED_CONTINUATION_TASK_IDS
    ):
        _fail("R8U_R3_CONTINUATION_ARRAY_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r3=True,
    )
    _validate_r8u_r3_continuation_chain(
        run, current_job_id=job_id, role="array", wait=True
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8U_R3_FIXED_CONTINUATION
    )
    result = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if result.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8U_R3_CONTINUATION_BATCH_NOT_FINALIZED")
    return result


def run_r8u_r3_continuation_finalizer() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8U_R3_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r3=True,
    )
    implementation_commit = _current_r8u_r3_implementation_commit()
    _validate_r8u_r3_continuation_chain(
        run, current_job_id=job_id, role="finalizer", wait=True
    )
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    historical = _r8u_historical_r8r_chain_authority()
    authority = finalizer.R8UR3ImplementationAuthority(
        implementation_commit=implementation_commit,
        historical_r8r_recovery_authority_sha256=(
            historical["recovery_authority_sha256"]
        ),
        historical_r8r_recovery_terminal_receipt_sha256=(
            historical["recovery_terminal_receipt_sha256"]
        ),
        historical_r8r_continuation_capacity_receipt_sha256=(
            historical["continuation_capacity_receipt_sha256"]
        ),
        historical_r8r_continuation_claim_sha256=(
            historical["continuation_claim_sha256"]
        ),
        historical_r8r_continuation_submission_receipt_sha256=(
            historical["continuation_submission_receipt_sha256"]
        ),
        failed_partial_seal_sha256=core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        r2_recovery_capacity_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_CAPACITY_PATH
        ),
        r2_recovery_authority_sha256=core.sha256_file(
            R8U_RECOVERY_AUTHORITY_PATH
        ),
        r2_recovery_submission_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_SUBMISSION_PATH
        ),
        extraction_candidate_seal_sha256=core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        publication_primitive_probe_sha256=core.sha256_file(R8U_R3_PROBE_PATH),
        publication_claim_sha256=core.sha256_file(
            R8U_R3_PUBLICATION_CLAIM_PATH
        ),
        publication_receipt_sha256=core.sha256_file(R8U_R3_PUBLICATION_PATH),
        resume_capacity_receipt_sha256=core.sha256_file(R8U_R3_CAPACITY_PATH),
        resume_authority_sha256=core.sha256_file(R8U_R3_AUTHORITY_PATH),
        resume_submission_receipt_sha256=core.sha256_file(
            R8U_R3_SUBMISSION_PATH
        ),
        resume_accounting_sha256=core.sha256_file(R8U_R3_ACCOUNTING_PATH),
        resume_terminal_receipt_sha256=core.sha256_file(R8U_R3_TERMINAL_PATH),
        continuation_claim_sha256=core.sha256_file(
            R8U_R3_CONTINUATION_CLAIM_PATH
        ),
        continuation_submission_receipt_sha256=core.sha256_file(
            R8U_R3_CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan, requirements=run.requirements,
        production_root=run.production_root, contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8u_r3_implementation_authority=authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("selected_subjects") != 4_530
        or summary.get("verified_source_objects") != 335_984
        or summary.get("selected_source_bytes") != 1_216_569_133_322
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("new_no_cine_studies") != 0
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical") is not True
        or summary.get("implementation_authority_epoch_count") != 3
        or summary.get("r8u_implementation_commit") != implementation_commit
        or SHA_RE.fullmatch(str(
            summary.get("r8u_recovery_continuation_authority_sha256")
        )) is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8U_R3_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return summary


# ---------------------------------------------------------------------------
# Fixed R8U-R4 portable candidate replay and worker-local publication
# ---------------------------------------------------------------------------


def _r8u_r4_common(
    *, artifact_type: str, status: str, implementation_commit: str
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": status,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_r4_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
    }
    if set(value) != R8U_R4_COMMON_KEYS:
        _fail("R8U_R4_CONTROL_SCHEMA_INVALID")
    return value


def _r8u_r4_local_identity(path: Path) -> Mapping[str, Any]:
    """Read a replacement-sensitive identity for this process only."""

    info = os.lstat(path)
    kind = (
        "directory" if stat.S_ISDIR(info.st_mode)
        else "regular" if stat.S_ISREG(info.st_mode)
        else "symlink" if stat.S_ISLNK(info.st_mode)
        else "other"
    )
    return {
        "device": int(info.st_dev), "inode": int(info.st_ino), "type": kind,
        "mode": int(stat.S_IMODE(info.st_mode)), "uid": int(info.st_uid),
        "gid": int(info.st_gid), "nlink": int(info.st_nlink),
        "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
    }


def _r8u_r4_portable_metadata_projection(
    root: Path,
    expected_npz_paths: Sequence[PurePosixPath | str] | None = None,
) -> _R8UR4PortableProjection:
    """Scan metadata once; exclude every cross-node locality/timestamp field."""

    root = Path(root)
    try:
        sequential._require_nonsymlink_components(root)
        root_before = _r8u_r4_local_identity(root)
        if root_before.get("type") != "directory":
            _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH"
        ) from exc

    files: list[tuple[Any, ...]] = []
    directories: list[tuple[Any, ...]] = []
    regular_paths: set[PurePosixPath] = set()
    directory_paths: set[PurePosixPath] = set()
    npz_paths: set[PurePosixPath] = set()
    total_bytes = 0
    npz_bytes = 0
    symlinks = 0
    nonregular = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            before = _r8u_r4_local_identity(directory)
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
            after = _r8u_r4_local_identity(directory)
            relative_directory = PurePosixPath(
                directory.relative_to(root).as_posix()
            )
        except Exception as exc:
            raise R8RControllerError(
                "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH"
            ) from exc
        if before != after or before.get("type") != "directory":
            _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
        directory_paths.add(relative_directory)
        # Directory nlink and all timestamps are diagnostic-only.  Device and
        # inode remain solely in the before/after comparison above.
        directories.append(
            (
                relative_directory.as_posix(), "D", before["mode"],
                before["uid"], before["gid"],
            )
        )
        for entry in reversed(entries):
            path = Path(entry.path)
            try:
                item_before = _r8u_r4_local_identity(path)
                item_after = _r8u_r4_local_identity(path)
                relative = PurePosixPath(path.relative_to(root).as_posix())
            except Exception as exc:
                raise R8RControllerError(
                    "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH"
                ) from exc
            if item_before != item_after:
                _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
            kind = item_before["type"]
            if kind == "symlink":
                _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
            if kind == "directory":
                stack.append(path)
                continue
            if kind != "regular":
                _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
            size = int(item_before["size"])
            if (
                size <= 0 or item_before["mode"] != 0o600
                or item_before["uid"] != os.geteuid()
                or item_before["nlink"] != 1
            ):
                _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
            regular_paths.add(relative)
            total_bytes += size
            if relative.suffix == ".npz":
                npz_paths.add(relative)
                npz_bytes += size
            # File nlink is portable safety authority; timestamps are not.
            files.append(
                (
                    relative.as_posix(), "F", item_before["mode"],
                    item_before["uid"], item_before["gid"],
                    item_before["nlink"], size,
                )
            )
    try:
        root_after = _r8u_r4_local_identity(root)
    except OSError as exc:
        raise R8RControllerError(
            "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH"
        ) from exc
    if root_before != root_after:
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    if any(
        row[3] != os.geteuid() or int(row[2]) & 0o077
        for row in directories
    ):
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    files.sort(key=lambda row: str(row[0]))
    directories.sort(key=lambda row: str(row[0]))
    normalized_expected = (
        None if expected_npz_paths is None
        else {PurePosixPath(str(path)) for path in expected_npz_paths}
    )
    if normalized_expected is not None and npz_paths != normalized_expected:
        _fail("R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH")
    file_path_rows = tuple(sorted(path.as_posix() for path in regular_paths))
    npz_path_rows = tuple(sorted(path.as_posix() for path in npz_paths))
    directory_path_rows = tuple(
        sorted(path.as_posix() for path in directory_paths)
    )
    root_portable = {
        "type": "directory", "mode": root_before["mode"],
        "uid": root_before["uid"], "gid": root_before["gid"],
    }
    value = {
        "candidate_regular_files": len(files),
        "candidate_directories": len(directories),
        "candidate_total_bytes": total_bytes,
        "candidate_npz_files": len(npz_paths),
        "candidate_npz_bytes": npz_bytes,
        "candidate_relative_file_path_set_sha256": (
            core.canonical_json_sha256(file_path_rows)
        ),
        "candidate_relative_npz_path_set_sha256": (
            core.canonical_json_sha256(npz_path_rows)
        ),
        "candidate_relative_file_portable_projection_sha256": (
            core.canonical_json_sha256(files)
        ),
        "candidate_relative_directory_path_set_sha256": (
            core.canonical_json_sha256(directory_path_rows)
        ),
        "candidate_relative_directory_portable_projection_sha256": (
            core.canonical_json_sha256(directories)
        ),
        "candidate_root_portable_identity_sha256": (
            core.canonical_json_sha256(root_portable)
        ),
        "symlink_count": symlinks,
        "nonregular_count": nonregular,
    }
    if set(value) != R8U_R4_PORTABLE_METADATA_FIELDS:
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    return _R8UR4PortableProjection(
        value=dict(sorted(value.items())),
        expected_npz_paths=frozenset(npz_paths),
        regular_file_paths=frozenset(regular_paths),
        directory_paths=frozenset(directory_paths),
        file_rows=tuple(files), directory_rows=tuple(directories),
        root_local_identity=dict(root_before),
    )


def _r8u_r4_projection_value(value: Any) -> Mapping[str, Any]:
    projected = getattr(value, "value", value)
    if not isinstance(projected, Mapping):
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    return projected


def _r8u_r4_compare_candidate_projection(
    sealed: Mapping[str, Any] | _R8UR4PortableProjection,
    observed: Mapping[str, Any] | _R8UR4PortableProjection,
) -> Mapping[str, Any]:
    """Compare by field name without ever exposing a field's live value."""

    left = _r8u_r4_projection_value(sealed)
    right = _r8u_r4_projection_value(observed)
    strict_r4 = str(left.get("artifact_type", "")).startswith(
        "lvef_c3_r8u_r4_portable_candidate_authority"
    )
    portable_fields = (
        R8U_R4_PORTABLE_CONTENT_FIELDS
        if strict_r4 else R8U_R4_PORTABLE_CONTENT_FIELDS & set(left) & set(right)
    )
    portable_differences = sorted(
        field for field in portable_fields
        if field not in left or field not in right
        or not _exact_typed_value_equal(left.get(field), right.get(field))
    )
    node_differences = sorted(
        field for field in R8U_R4_NODE_LOCAL_DIAGNOSTIC_FIELDS & set(left) & set(right)
        if not _exact_typed_value_equal(left.get(field), right.get(field))
    )
    all_differences = sorted({*portable_differences, *node_differences})
    path_fields = {
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_npz_manifest_projection_sha256", "candidate_npz_files",
    }
    count_fields = {
        "candidate_regular_files", "candidate_npz_files",
        "candidate_total_bytes", "candidate_npz_bytes",
    }
    return {
        "first_differing_field": (
            all_differences[0] if all_differences else "NONE"
        ),
        "differing_fields": all_differences,
        "portable_differing_fields": portable_differences,
        "node_local_differing_fields": node_differences,
        "portable_candidate_fields_equal": not portable_differences,
        "node_local_only_differences": (
            not portable_differences and bool(node_differences)
        ),
        "candidate_path_set_equal": not bool(path_fields & set(portable_differences)),
        "candidate_count_and_bytes_equal": not bool(
            count_fields & set(portable_differences)
        ),
    }


def _r8u_r4_candidate_replay_diagnosis(
    *,
    job_id: str = R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID,
    candidate_seal_sha256: str = R8U_R3_CANDIDATE_SEAL_SHA256,
    comparison: Mapping[str, Any] | None = None,
    sealed: Mapping[str, Any] | _R8UR4PortableProjection | None = None,
    observed: Mapping[str, Any] | _R8UR4PortableProjection | None = None,
    candidate_path_set_equal: bool | None = None,
    candidate_count_and_bytes_equal: bool | None = None,
) -> Mapping[str, Any]:
    if comparison is None:
        if sealed is None or observed is None:
            _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
        comparison = _r8u_r4_compare_candidate_projection(sealed, observed)
    portable_equal = comparison.get("portable_candidate_fields_equal") is True
    differences = list(comparison.get("differing_fields", ()))
    portable_differences = list(comparison.get("portable_differing_fields", ()))
    node_differences = list(comparison.get("node_local_differing_fields", ()))
    # Failed job 7364184 emitted only its outer code.  An empty field list is
    # therefore honest: no exact worker-node value was persisted.  The fixed
    # R3 schema itself proves that its replay mixed node/non-content metadata.
    unrecorded_failed_worker_difference = (
        job_id == R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID
        and portable_equal and not differences
    )
    node_only = (
        comparison.get("node_local_only_differences") is True
        or unrecorded_failed_worker_difference
    )
    path_equal = (
        comparison.get("candidate_path_set_equal") is True
        if candidate_path_set_equal is None else candidate_path_set_equal
    )
    counts_equal = (
        comparison.get("candidate_count_and_bytes_equal") is True
        if candidate_count_and_bytes_equal is None
        else candidate_count_and_bytes_equal
    )
    if not portable_equal or not path_equal or not counts_equal:
        status = "BLOCKED_PORTABLE_CANDIDATE_CONTENT_MISMATCH"
    elif any("ctime" in field or "mtime" in field or "link_count" in field
             for field in differences):
        status = "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE"
    else:
        status = "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE"
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r4_candidate_replay_diagnosis_v1",
        "status": status,
        "job_id": job_id,
        "candidate_seal_sha256": candidate_seal_sha256,
        "first_differing_field": (
            str(comparison.get("first_differing_field", "NONE"))
            if differences else "NOT_PERSISTED_BY_FAILED_WORKER"
        ),
        "differing_fields": sorted(str(field) for field in differences),
        "portable_differing_fields": sorted(
            str(field) for field in portable_differences
        ),
        "node_local_differing_fields": sorted(
            str(field) for field in node_differences
        ),
        "portable_candidate_fields_equal": portable_equal,
        "node_local_only_differences": node_only,
        "candidate_path_set_equal": bool(path_equal),
        "candidate_count_and_bytes_equal": bool(counts_equal),
        "zero_body_reads": True,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "final_classification": status,
    }
    if set(value) != R8U_R4_DIAGNOSIS_KEYS:
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    return value


def _r8u_r4_write_diagnosis_no_clobber(
    path: Path | Mapping[str, Any],
    value: Mapping[str, Any] | None = None,
) -> str:
    if value is None:
        value = _r8u_r4_projection_value(path)
        path = R8U_R4_DIAGNOSIS_PATH
    if Path(path) != R8U_R4_DIAGNOSIS_PATH and not Path(path).is_absolute():
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    if set(value) != R8U_R4_DIAGNOSIS_KEYS:
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    try:
        return _write_private_json(Path(path), value)
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R4_DIAGNOSIS_NO_CLOBBER_FAILED"
        ) from exc


def _r8u_r4_validate_immutable_r3_failure(
    run: sequential.FullRun | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Validate R3 as immutable history, never under the current R4 commit."""

    resolved_run = run or _load_fixed_original_run(
        scheduler_job_identity="R8U_R4_R3_HISTORY_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    history = _r8u_r3_validate_r2_history(resolved_run)
    candidate, _ = _load_private_json(R8U_R3_CANDIDATE_SEAL_PATH)
    _r8u_r3_validate_candidate_seal_static(
        candidate,
        implementation_commit=(
            R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        history=history,
    )
    submission, _ = _load_private_json(R8U_R3_SUBMISSION_PATH)
    try:
        descriptor = os.open(
            R8U_R3_FAILED_PUBLICATION_RESUME_LOG_PATH,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            before = os.fstat(descriptor)
            payload = b""
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                payload += chunk
                if len(payload) > R8U_SCHEDULER_LOG_MAX_BYTES:
                    _fail("R8U_R4_IMMUTABLE_R3_EVIDENCE_INVALID")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except R8RControllerError:
        raise
    except OSError as exc:
        raise R8RControllerError(
            "R8U_R4_IMMUTABLE_R3_EVIDENCE_INVALID"
        ) from exc
    if (
        core.sha256_file(R8U_R3_CANDIDATE_SEAL_PATH)
        != R8U_R3_CANDIDATE_SEAL_SHA256
        or candidate.get("implementation_commit")
        != R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        or submission.get("resume_job_id")
        != R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID
        or submission.get("resume_job_name")
        != R8U_R3_FAILED_PUBLICATION_RESUME_JOB_NAME
        or before.st_dev != after.st_dev or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or stat.S_IMODE(before.st_mode)
        != int(R8U_R3_FAILED_PUBLICATION_RESUME_LOG_MODE, 8)
        or len(payload) != R8U_R3_FAILED_PUBLICATION_RESUME_LOG_BYTES
        or _sha256_bytes(payload)
        != R8U_R3_FAILED_PUBLICATION_RESUME_LOG_SHA256
        or os.path.lexists(R8U_R3_TERMINAL_PATH)
        or os.path.lexists(R8U_R3_PUBLICATION_PATH)
    ):
        _fail("R8U_R4_IMMUTABLE_R3_EVIDENCE_INVALID")
    return candidate, history


def _r8u_r4_portable_candidate_projection(
    run: sequential.FullRun,
    *, root: Path | None = None,
) -> _R8UR4PortableProjection:
    """Build the closed portable content authority with zero NPZ body reads."""

    source = root or (R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
    tree = _r8u_r4_portable_metadata_projection(source)
    control_paths = {
        PurePosixPath(name) for name in R8U_R3_CANDIDATE_CONTROL_FILES
    }
    observed_controls = tree.regular_file_paths - tree.expected_npz_paths
    canonical_npz_re = re.compile(
        r"^clips/clips/[0-9a-f]{2}/[0-9a-f]{64}\.npz$"
    )
    if (
        observed_controls != control_paths
        or len(tree.expected_npz_paths) != R8U_R3_CANDIDATE_NPZ_FILES
        or any(
            canonical_npz_re.fullmatch(path.as_posix()) is None
            for path in tree.expected_npz_paths
        )
    ):
        _fail("R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH")
    if (
        tree.value["symlink_count"] != 0
        or tree.value["nonregular_count"] != 0
        or any(
            row[2] != 0o600 or row[3] != os.geteuid()
            or row[5] != 1 or row[6] <= 0
            for row in tree.file_rows
        )
        or any(
            row[3] != os.geteuid() or int(row[2]) & 0o077
            for row in tree.directory_rows
        )
    ):
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    controls = {
        name: source / name for name in R8U_R3_CANDIDATE_CONTROL_FILES
    }
    manifest = _r8u_r3_normalize_extraction_manifest(
        controls["extraction_manifest.restricted.csv"]
    )
    if manifest.expected_npz_paths != tree.expected_npz_paths:
        _fail("R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH")
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    try:
        stage_summary = stages.validate_completed_stage_for_recovery(
            stage_directory=source, stage="DICOM_EXTRACTION",
            batch_id=R8U_FIXED_BATCH_ID, attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            input_manifest=(
                sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["raw_batch"]
                / "verified_download_manifest.restricted.csv"
            ),
            artifact_names=(
                "dicom_audit.restricted.csv",
                "extraction_manifest.restricted.csv",
                "technical_disposition_manifest.restricted.csv",
                "dicom_extraction.summary.json",
            ),
            summary_name="dicom_extraction.summary.json",
        )
        audit_summary = _r8u_r3_normalize_dicom_audit(
            controls["dicom_audit.restricted.csv"]
        )
        stages.validate_extraction_manifest_plan_membership(
            controls["extraction_manifest.restricted.csv"], planned,
            controls["technical_disposition_manifest.restricted.csv"],
        )
        dispositions = stages.read_technical_disposition_manifest(
            controls["technical_disposition_manifest.restricted.csv"]
        )
        technical_summary = stages.validate_technical_disposition_manifest_rows(
            manifest.rows, dispositions
        )
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH"
        ) from exc
    expected = {
        "status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "n_objects": R8U_BATCH16_RAW_FILES, "n_studies": 250,
        "n_readable": R8U_BATCH16_RAW_FILES, "n_unreadable": 0,
        "n_multiframe_candidates": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_single_frame": 8_490, "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_object_technical_dispositions": 0, "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
    }
    if (
        any(stage_summary.get(key) != value for key, value in expected.items())
        or any(
            audit_summary.get(key) != expected[key]
            for key in (
                "n_objects", "n_studies", "n_readable", "n_unreadable",
                "n_multiframe_candidates", "n_single_frame",
                "n_pixel_decode_failures",
            )
        )
        or manifest.summary.get("n_successfully_extracted_cines")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or manifest.summary.get("n_object_technical_dispositions") != 0
        or manifest.summary.get("n_blocking_failures") != 0
        or technical_summary.get("n_object_technical_dispositions") != 0
        or technical_summary.get("object_substitution_count") != 0
        or dispositions
    ):
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    try:
        control_hashes = {
            "stage_completion_receipt_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["stage_completion_receipt.restricted.json"]
                )
            ),
            "extraction_manifest_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["extraction_manifest.restricted.csv"]
                )
            ),
            "dicom_audit_sha256": _sha256_bytes(
                _read_control_nofollow(controls["dicom_audit.restricted.csv"])
            ),
            "extraction_summary_sha256": _sha256_bytes(
                _read_control_nofollow(
                    controls["dicom_extraction.summary.json"]
                )
            ),
            "technical_disposition_manifest_sha256": (
                stages.technical_disposition_manifest_sha256(
                    controls["technical_disposition_manifest.restricted.csv"]
                )
            ),
        }
        stage_event = _strict_json(
            _read_control_nofollow(
                controls["stage_completion_receipt.restricted.json"]
            )
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_PORTABLE_CANDIDATE_CONTROL_HASH_MISMATCH"
        ) from exc
    value = {
        **dict(tree.value), **control_hashes,
        "canonical_stage_event_authority_sha256": (
            core.canonical_json_sha256(stage_event)
        ),
        "input_manifest_sha256": R8U_BATCH16_VERIFIED_MANIFEST_SHA256,
        "candidate_npz_manifest_projection_sha256": (
            core.canonical_json_sha256(manifest.manifest_projection)
        ),
        "n_selected_studies": 250,
        "n_source_objects": R8U_BATCH16_RAW_FILES,
        "source_bytes": R8U_BATCH16_RAW_BYTES,
        "n_readable": R8U_BATCH16_RAW_FILES, "n_unreadable": 0,
        "n_multiframe_candidates": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_single_frame": 8_490, "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_object_technical_dispositions": 0, "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": R8U_R3_CANDIDATE_NPZ_FILES,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
        "extraction_status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
    }
    if set(value) != R8U_R4_PORTABLE_CONTENT_FIELDS:
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    return replace(tree, value=dict(sorted(value.items())))


def _r8u_r4_portable_candidate_authority(
    *, implementation_commit: str,
    projection: _R8UR4PortableProjection,
    diagnosis_sha256: str,
) -> Mapping[str, Any]:
    if SHA_RE.fullmatch(diagnosis_sha256) is None:
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    value = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_portable_candidate_authority_v1",
            status="PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY",
            implementation_commit=implementation_commit,
        ),
        "failed_r8u_r3_job_id": R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID,
        "r8u_r3_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_SHA256,
        "candidate_replay_diagnosis_sha256": diagnosis_sha256,
        "portable_projection_status": "PASS_EXACT_PORTABLE_CONTENT",
        **dict(projection.value),
        "missing_npz_files": 0, "additional_npz_files": 0,
        "substituted_npz_files": 0, "npz_body_reads": 0,
        "dicom_body_reads": 0,
    }
    if set(value) != R8U_R4_PORTABLE_AUTHORITY_KEYS:
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    return value


def _r8u_r4_raise_portable_comparison(
    comparison: Mapping[str, Any],
) -> None:
    differences = set(comparison.get("portable_differing_fields", ()))
    if not differences:
        return
    if differences & R8U_R4_CONTROL_HASH_FIELDS:
        _fail("R8U_PORTABLE_CANDIDATE_CONTROL_HASH_MISMATCH")
    if differences & {
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_npz_manifest_projection_sha256", "candidate_npz_files",
    }:
        _fail("R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH")
    if differences & R8U_R4_PORTABLE_METADATA_FIELDS:
        _fail("R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH")
    _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")


def validate_r8u_r4_portable_candidate_authority(
    value: Mapping[str, Any] | None = None,
    *, observed_projection: _R8UR4PortableProjection | Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    authority = value
    if authority is None:
        authority, _ = _load_private_json(R8U_R4_PORTABLE_AUTHORITY_PATH)
    if (
        set(authority) != R8U_R4_PORTABLE_AUTHORITY_KEYS
        or authority.get("status") != "PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY"
        or authority.get("failed_r8u_r3_job_id")
        != R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID
        or authority.get("r8u_r3_candidate_seal_sha256")
        != R8U_R3_CANDIDATE_SEAL_SHA256
        or authority.get("portable_projection_status")
        != "PASS_EXACT_PORTABLE_CONTENT"
        or any(authority.get(field) != 0 for field in (
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
        ))
        or authority.get("candidate_npz_files")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or authority.get("candidate_regular_files")
        != R8U_R3_CANDIDATE_NPZ_FILES + len(R8U_R3_CANDIDATE_CONTROL_FILES)
    ):
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    if observed_projection is not None:
        comparison = _r8u_r4_compare_candidate_projection(
            authority, observed_projection
        )
        _r8u_r4_raise_portable_comparison(comparison)
    return authority


def validate_r8u_r4_replay_diagnosis(
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    diagnosis = value
    if diagnosis is None:
        diagnosis, _ = _load_private_json(R8U_R4_DIAGNOSIS_PATH)
    if (
        set(diagnosis) != R8U_R4_DIAGNOSIS_KEYS
        or diagnosis.get("job_id") != R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID
        or diagnosis.get("candidate_seal_sha256")
        != R8U_R3_CANDIDATE_SEAL_SHA256
        or diagnosis.get("status")
        not in {
            "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE",
            "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE",
        }
        or diagnosis.get("portable_candidate_fields_equal") is not True
        or diagnosis.get("node_local_only_differences") is not True
        or diagnosis.get("candidate_path_set_equal") is not True
        or diagnosis.get("candidate_count_and_bytes_equal") is not True
        or diagnosis.get("zero_body_reads") is not True
        or diagnosis.get("dicom_body_reads") != 0
        or diagnosis.get("npz_body_reads") != 0
    ):
        _fail("R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH")
    return diagnosis


def _r8u_r4_live_publication_locality(
    *,
    source: Path,
    target: Path,
    publication_claim: Mapping[str, Any],
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
    claim_validator: Callable[[Mapping[str, Any]], Any] | None = None,
    competing_active_jobs: int = 0,
    competing_active_processes: int = 0,
) -> _R8UR4LivePublicationLocality:
    """Capture locality only on the worker and retain raw tokens in memory."""

    source = Path(source)
    target = Path(target)
    read_identity = identity_reader or _r8u_r4_local_identity
    read_mount = mount_reader or (
        lambda path: _r8u_r3_mount_authority(path)[0]
    )
    try:
        if claim_validator is not None:
            claim_result = claim_validator(publication_claim)
            if claim_result is False:
                _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
        elif publication_claim.get("status") not in {
            "AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION",
            "AUTHORIZED_EXCLUSIVE_BATCH16_PUBLICATION",
        }:
            _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
        sequential._require_nonsymlink_components(source)
        sequential._require_nonsymlink_components(target.parent)
        if not os.path.lexists(source):
            _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
        if os.path.lexists(target):
            _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
        source_before = dict(read_identity(source))
        source_parent_before = dict(read_identity(source.parent))
        target_parent_before = dict(read_identity(target.parent))
        if source_before.get("type") != "directory":
            _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
        if (
            source_parent_before.get("type") != "directory"
            or target_parent_before.get("type") != "directory"
        ):
            _fail("R8U_LIVE_PUBLICATION_PARENT_CHANGED")
        source_mount = read_mount(source.parent)
        target_mount = read_mount(target.parent)
        source_after = dict(read_identity(source))
        source_parent_after = dict(read_identity(source.parent))
        target_parent_after = dict(read_identity(target.parent))
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_SOURCE_INVALID"
        ) from exc
    if source_before != source_after:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if (
        source_parent_before != source_parent_after
        or target_parent_before != target_parent_after
    ):
        _fail("R8U_LIVE_PUBLICATION_PARENT_CHANGED")
    if source_mount != target_mount:
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
    if competing_active_jobs != 0 or competing_active_processes != 0:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    owner_mode_valid = all(
        identity.get("uid") == os.geteuid()
        and int(identity.get("mode", -1)) >= 0
        and int(identity.get("mode", -1)) & 0o077 == 0
        for identity in (
            source_before, source_parent_before, target_parent_before
        )
    )
    if not owner_mode_valid:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    try:
        claim_sha = core.canonical_json_sha256(publication_claim)
        source_sha = core.canonical_json_sha256(source_before)
        source_parent_sha = core.canonical_json_sha256(source_parent_before)
        target_parent_sha = core.canonical_json_sha256(target_parent_before)
        source_mount_sha = core.canonical_json_sha256(
            {"opaque_current_process_mount_key": repr(source_mount)}
        )
        target_mount_sha = core.canonical_json_sha256(
            {"opaque_current_process_mount_key": repr(target_mount)}
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_PARENT_CHANGED"
        ) from exc
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r4_live_publication_locality_v1",
        "status": "PASS_WORKER_LOCAL_PUBLICATION_LOCALITY",
        "publication_claim_sha256": claim_sha,
        "source_exists_safe_directory": True,
        "target_absent": True,
        "source_target_same_mounted_filesystem": True,
        "parents_nonsymlinked": True,
        "owner_mode_valid": True,
        "source_identity_stable_same_call": True,
        "source_parent_identity_stable_same_call": True,
        "target_parent_identity_stable_same_call": True,
        "source_identity_sha256": source_sha,
        "source_parent_identity_sha256": source_parent_sha,
        "target_parent_identity_sha256": target_parent_sha,
        "source_mount_identity_sha256": source_mount_sha,
        "target_mount_identity_sha256": target_mount_sha,
        "competing_active_jobs": competing_active_jobs,
        "competing_active_processes": competing_active_processes,
    }
    if set(value) != R8U_R4_LOCALITY_KEYS:
        _fail("R8U_LIVE_PUBLICATION_PARENT_CHANGED")
    return _R8UR4LivePublicationLocality(
        value=dict(value), source_identity=source_before,
        source_parent_identity=source_parent_before,
        target_parent_identity=target_parent_before,
        source_mount_key=source_mount, target_mount_key=target_mount,
    )


def _r8u_r4_revalidate_live_locality(
    locality: _R8UR4LivePublicationLocality,
    *, source: Path, target: Path,
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
) -> None:
    read_identity = identity_reader or _r8u_r4_local_identity
    read_mount = mount_reader or (
        lambda path: _r8u_r3_mount_authority(path)[0]
    )
    if os.path.lexists(target):
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    try:
        source_identity = dict(read_identity(source))
        source_parent_identity = dict(read_identity(source.parent))
        target_parent_identity = dict(read_identity(target.parent))
        source_mount = read_mount(source.parent)
        target_mount = read_mount(target.parent)
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_PARENT_CHANGED"
        ) from exc
    if source_identity != locality.source_identity:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if (
        source_parent_identity != locality.source_parent_identity
        or target_parent_identity != locality.target_parent_identity
    ):
        _fail("R8U_LIVE_PUBLICATION_PARENT_CHANGED")
    if (
        source_mount != locality.source_mount_key
        or target_mount != locality.target_mount_key
        or source_mount != target_mount
    ):
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")


def validate_r8u_r4_live_publication_locality(
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    locality = value
    if locality is None:
        locality, _ = _load_private_json(R8U_R4_LOCALITY_PATH)
    if (
        set(locality) != R8U_R4_LOCALITY_KEYS
        or locality.get("status") != "PASS_WORKER_LOCAL_PUBLICATION_LOCALITY"
        or any(
            locality.get(field) is not True
            for field in (
                "source_exists_safe_directory", "target_absent",
                "source_target_same_mounted_filesystem",
                "parents_nonsymlinked", "owner_mode_valid",
                "source_identity_stable_same_call",
                "source_parent_identity_stable_same_call",
                "target_parent_identity_stable_same_call",
            )
        )
        or locality.get("competing_active_jobs") != 0
        or locality.get("competing_active_processes") != 0
        or any(
            SHA_RE.fullmatch(str(locality.get(field, ""))) is None
            for field in (
                "publication_claim_sha256", "source_identity_sha256",
                "source_parent_identity_sha256",
                "target_parent_identity_sha256",
                "source_mount_identity_sha256",
                "target_mount_identity_sha256",
            )
        )
    ):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    return locality


def _r8u_r4_publication_claim(
    *, implementation_commit: str, resume_job_id: str,
    worker_process_projection: Mapping[str, Any],
    worker_qstat_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        JOB_RE.fullmatch(resume_job_id) is None
        or worker_process_projection.get("matching_processes") != 0
        or worker_qstat_projection.get("competing_matching_jobs") != 0
    ):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    value = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_publication_claim_v1",
            status="AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION",
            implementation_commit=implementation_commit,
        ),
        "resume_job_id": resume_job_id,
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "resume_authority_sha256": core.sha256_file(R8U_R4_AUTHORITY_PATH),
        "resume_submission_receipt_sha256": core.sha256_file(
            R8U_R4_SUBMISSION_PATH
        ),
        "worker_process_projection_sha256": core.canonical_json_sha256(
            worker_process_projection
        ),
        "worker_qstat_projection_sha256": core.canonical_json_sha256(
            worker_qstat_projection
        ),
        "target_role": "extracted_cache/c3_batch_015/dicom_extraction",
        "target_absent": True,
        "competing_active_jobs": 0, "competing_active_processes": 0,
        "cloud_requests": 0, "downloads": 0, "dicom_body_reads": 0,
        "dicom_extraction_executions": 0, "npz_body_reads": 0,
    }
    if set(value) != R8U_R4_PUBLICATION_CLAIM_KEYS:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    return value


def _r8u_r4_create_publication_claim(value: Mapping[str, Any]) -> str:
    if set(value) != R8U_R4_PUBLICATION_CLAIM_KEYS:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        _create_private_directory_no_clobber(R8U_R4_PUBLICATION_CLAIM_ROOT)
        return _write_private_json(R8U_R4_PUBLICATION_CLAIM_PATH, value)
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_CLAIM_INVALID"
        ) from exc


def _r8u_r4_primitive_probe(
    *, implementation_commit: str,
    locality: _R8UR4LivePublicationLocality,
    publication_claim: Mapping[str, Any],
    invoker: Callable[[Path, Path], Any] | None = None,
) -> Mapping[str, Any]:
    """Probe RENAME_NOREPLACE on the worker-local target filesystem."""

    if os.path.lexists(R8U_R4_PROBE_PATH) or os.path.lexists(R8U_R4_PROBE_WORK_ROOT):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        probe_mount_key = _r8u_r3_mount_authority(
            R8U_R4_PROBE_WORK_ROOT.parent
        )[0]
    except Exception as exc:
        raise R8RControllerError("R8U_LIVE_PUBLICATION_CROSS_MOUNT") from exc
    if probe_mount_key != locality.target_mount_key:
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
    probe_source = R8U_R4_PROBE_WORK_ROOT / "source"
    probe_target = R8U_R4_PROBE_WORK_ROOT / "target"
    created: list[Path] = []
    removed = 0
    result = _R8UR3RenameResult(False, errno.EIO, "EIO")
    source_present = False
    target_present = False
    target_exact = False
    try:
        for directory in (R8U_R4_PROBE_WORK_ROOT, probe_source):
            _create_private_directory_no_clobber(directory)
            created.append(directory)
        result = _r8u_r3_raw_rename_noreplace(
            probe_source, probe_target, invoker=invoker
        )
        source_present = os.path.lexists(probe_source)
        target_present = os.path.lexists(probe_target)
        target_exact = (
            target_present and not probe_target.is_symlink()
            and probe_target.is_dir()
            and next(os.scandir(probe_target), None) is None
        )
    finally:
        for directory in (probe_target, probe_source, R8U_R4_PROBE_WORK_ROOT):
            try:
                directory.rmdir()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
    classification = _r8u_r3_primary_classification(result)
    supported = result.returned_success
    if supported and (source_present or not target_exact):
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if not supported and not (source_present and not target_present):
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if classification == "RENAME_CROSS_MOUNT_EXDEV":
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
    if classification not in R8U_R3_PROCEEDABLE_PROBE_RESULTS:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    cleanup = not os.path.lexists(R8U_R4_PROBE_WORK_ROOT) and removed == len(created)
    if not cleanup:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    value = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_publication_primitive_probe_v1",
            status="PASS_R8U_R4_PUBLICATION_PRIMITIVE_PROBE",
            implementation_commit=implementation_commit,
        ),
        "live_publication_locality_sha256": core.sha256_file(
            R8U_R4_LOCALITY_PATH
        ),
        "publication_claim_sha256": core.canonical_json_sha256(
            publication_claim
        ),
        "primary_primitive": "RENAMEAT2_RENAME_NOREPLACE",
        "primary_result": classification,
        "primary_errno": result.errno_name,
        "primary_errno_number": result.errno_number,
        "primary_returned_success": supported,
        "probe_source_present_after": source_present,
        "probe_target_present_after": target_present,
        "probe_target_exact_after": target_exact,
        "probe_cleanup_passed": cleanup,
        "scientific_file_body_reads": 0, "npz_body_reads": 0,
        "dicom_body_reads": 0, "dicom_extraction_executions": 0,
    }
    if set(value) != R8U_R4_PROBE_KEYS:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    _write_private_json(R8U_R4_PROBE_PATH, value)
    return value


def _r8u_r4_publish_candidate(
    *,
    source: Path,
    target: Path,
    implementation_commit: str,
    portable_authority: Mapping[str, Any],
    portable_projection: _R8UR4PortableProjection,
    live_locality: _R8UR4LivePublicationLocality,
    publication_claim: Mapping[str, Any],
    probe: Mapping[str, Any],
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
    write_receipt: bool = True,
) -> Mapping[str, Any]:
    """Publish once; continuity uses root identity, never a second tree scan."""

    source = Path(source)
    target = Path(target)
    validate_r8u_r4_portable_candidate_authority(
        portable_authority, observed_projection=portable_projection
    )
    validate_r8u_r4_live_publication_locality(live_locality)
    if (
        core.canonical_json_sha256(publication_claim)
        != live_locality["publication_claim_sha256"]
        or publication_claim.get("status")
        != "AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION"
    ):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    _r8u_r4_revalidate_live_locality(
        live_locality, source=source, target=target,
        identity_reader=identity_reader, mount_reader=mount_reader,
    )
    if portable_projection.root_local_identity != live_locality.source_identity:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    fallback_used = probe.get("primary_result") != "RENAME_NOREPLACE_SUPPORTED"
    allowed_fallback = {
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }
    if fallback_used and probe.get("primary_result") not in allowed_fallback:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    if fallback_used:
        try:
            returned = fallback_invoker(source, target)
            if returned not in {None, 0}:
                _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
            result = _R8UR3RenameResult(True, 0, "NONE")
        except OSError as exc:
            number = int(exc.errno or errno.EIO)
            result = _R8UR3RenameResult(
                False, number, errno.errorcode.get(number, "UNKNOWN")
            )
        primitive = "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
    else:
        result = _r8u_r3_raw_rename_noreplace(
            source, target, invoker=primary_invoker
        )
        primitive = "RENAMEAT2_RENAME_NOREPLACE"
    source_present = os.path.lexists(source)
    target_present = os.path.lexists(target)
    if source_present and target_present:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    if source_present and not target_present:
        if result.errno_number == errno.EXDEV:
            _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if not target_present:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    read_identity = identity_reader or _r8u_r4_local_identity
    try:
        target_identity = dict(read_identity(target))
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_TARGET_INVALID"
        ) from exc
    continuity_fields = ("device", "inode", "type", "mode", "uid", "gid")
    if (
        target_identity.get("type") != "directory"
        or any(
            target_identity.get(field) != live_locality.source_identity.get(field)
            for field in continuity_fields
        )
    ):
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    ruling = (
        "PUBLICATION_PASS" if result.returned_success
        else "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
    )
    receipt = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_batch16_publication_v1",
            status="PASS_R8U_R4_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
            implementation_commit=implementation_commit,
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "live_publication_locality_sha256": core.sha256_file(
            R8U_R4_LOCALITY_PATH
        ),
        "publication_primitive_probe_sha256": core.sha256_file(
            R8U_R4_PROBE_PATH
        ),
        "publication_claim_sha256": core.sha256_file(
            R8U_R4_PUBLICATION_CLAIM_PATH
        ),
        "primitive_attempted": primitive,
        "primary_result": probe["primary_result"],
        "fallback_used": fallback_used,
        "rename_returned_success": result.returned_success,
        "real_rename_errno": result.errno_name,
        "real_rename_errno_number": result.errno_number,
        "real_rename_errno_classification": (
            _r8u_r3_real_rename_classification(result)
        ),
        "publication_ruling": ruling,
        "prepublication_candidate_sha256": portable_authority[
            "candidate_relative_file_portable_projection_sha256"
        ],
        "postpublication_root_identity_sha256": (
            core.canonical_json_sha256(target_identity)
        ),
        "source_absent": True, "target_exact": True,
        "candidate_npz_files": portable_authority["candidate_npz_files"],
        "candidate_total_bytes": portable_authority["candidate_total_bytes"],
        "files_moved": portable_authority["candidate_npz_files"],
        "files_copied": 0, "files_deleted_independently": 0,
        "dicom_body_reads": 0, "dicom_extraction_executions": 0,
        "npz_body_reads": 0, "cloud_requests": 0, "downloads": 0,
    }
    if set(receipt) != R8U_R4_PUBLICATION_KEYS:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    if write_receipt:
        _write_private_json(R8U_R4_PUBLICATION_PATH, receipt)
    return receipt


def _r8u_r4_resume_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r4_res_{implementation_commit[:8]}"


def _r8u_r4_resume_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N", _r8u_r4_resume_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R4_SCHEDULER_ROOT),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_r4_process_projection(
    *, environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    worker_local: bool,
) -> Mapping[str, Any]:
    command = ["/bin/ps", "-axo", "pid=,user=,command="]
    completed = runner(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if completed.returncode != 0 or completed.stderr or len(payload) > 16 * 1024 * 1024:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        lines = payload.decode("utf-8", "strict").splitlines()
    except UnicodeError as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_CLAIM_INVALID"
        ) from exc
    markers = (
        "--run-array-task", "--run-cohort-finalizer",
        "--recover-batch3-preservation", "--run-continuation-array-task",
        "--run-continuation-finalizer", "--run-batch16-recovery",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
        "--run-r8u-r3-batch16-publication-resume",
        "--run-r8u-r3-continuation-17-19-array-task",
        "--run-r8u-r3-continuation-finalizer",
        "--run-r8u-r4-batch16-publication-resume",
        "--run-r8u-r4-continuation-17-19-array-task",
        "--run-r8u-r4-continuation-finalizer",
        "run_production_dicom_extraction", "run_production_echoprime",
        "preserve_lvef_c3_production_batch", "retire_lvef_c3_extracted_cache",
        "finalize_lvef_c3_production", "lvef_c3_r8u_",
    )
    matching = 0
    self_seen = 0
    for line in lines:
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or fields[1] != environment["USER"]:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
        if worker_local and pid == os.getpid():
            if "--run-r8u-r4-batch16-publication-resume" not in fields[2]:
                _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
            self_seen += 1
            continue
        if any(marker in fields[2] for marker in markers):
            matching += 1
    if matching != 0 or (worker_local and self_seen != 1):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    value = {
        "status": (
            "PASS_ZERO_COMPETING_R8U_R4_WORKER_PROCESSES"
            if worker_local else "PASS_ZERO_COMPETING_R8U_R4_PROCESSES"
        ),
        "matching_processes": 0, "process_snapshot_count": 1,
        "ps_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": command})),
        "ps_stdout_sha256": _sha256_bytes(payload),
    }
    if set(value) != R8U_R3_PROCESS_PROJECTION_KEYS:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    return value


def _r8u_r4_qstat_projection(
    *, environment: Mapping[str, str], resume_job_id: str,
    implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    worker_local: bool,
) -> Mapping[str, Any]:
    """Capture one qstat snapshot; this function never polls."""

    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0 or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_CLAIM_INVALID"
        ) from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    records: list[tuple[str, str, str, str]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local(child), []).append(child.text or "")
        if any(len(fields.get(key, ())) != 1 for key in (
            "JB_job_number", "JB_name", "state"
        )):
            _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
        records.append((
            fields["JB_job_number"][0], fields["JB_name"][0],
            fields["state"][0], str(job.get("state", "")),
        ))
    names = re.compile(
        r"lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
        r"r8u_(?:rec|seq|fin)|r8u_r3_(?:res|seq|fin)|"
        r"r8u_r4_(?:res|seq|fin))_[0-9a-f]{8}"
        r"|c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}"
    )
    matches = [record for record in records if record[0] == resume_job_id]
    competing = [
        record for record in records
        if record[0] != resume_job_id and names.fullmatch(record[1])
    ]
    if len(matches) != 1 or competing:
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    _job, name, state, category = matches[0]
    expected_category = "running" if state in {"r", "t", "Rr"} else "pending"
    if (
        name != _r8u_r4_resume_job_name(implementation_commit)
        or state not in {"r", "qw", "t", "Rr"}
        or category != expected_category
    ):
        _fail("R8U_LIVE_PUBLICATION_CLAIM_INVALID")
    body = {
        "status": (
            "PASS_EXACT_ONE_R8U_R4_WORKER_JOB_ZERO_COMPETITORS"
            if worker_local
            else "PASS_EXACT_ONE_R8U_R4_RESUME_JOB_ZERO_COMPETITORS"
        ),
        "resume_job_id": resume_job_id, "resume_job_name": name,
        "state": state, "category": category, "target_matches": 1,
        "competing_matching_jobs": 0, "qstat_snapshot_count": 1,
    }
    return {
        **body, "qstat_projection_sha256": core.canonical_json_sha256(body)
    }


def _validate_r8u_r4_initial_qstat(
    *, environment: Mapping[str, str], resume_job_id: str,
    implementation_commit: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    return _r8u_r4_qstat_projection(
        environment=environment, resume_job_id=resume_job_id,
        implementation_commit=implementation_commit, runner=runner,
        worker_local=False,
    )


def _r8u_r4_resume_authority(
    *, run: sequential.FullRun, implementation_commit: str,
    qsub_environment_sha256: str, prefix_receipts: Sequence[str],
    history: Mapping[str, Any], capacity_sha256: str,
    pre_qsub_process_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = {
        **_r8u_r4_common(
            artifact_type=(
                "lvef_c3_r8u_r4_batch16_publication_resume_authority_v1"
            ),
            status="AUTHORIZED_FIXED_R8U_R4_BATCH16_PUBLICATION_RESUME",
            implementation_commit=implementation_commit,
        ),
        "prior_implementation_commit": (
            R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "historical_r8r_chain_authority": dict(
            _r8u_historical_r8r_chain_authority()
        ),
        "failed_partial_seal_sha256": history["failed_partial_seal_sha256"],
        "r2_recovery_capacity_receipt_sha256": history[
            "r2_recovery_capacity_receipt_sha256"
        ],
        "r2_recovery_authority_sha256": history["r2_recovery_authority_sha256"],
        "r2_recovery_submission_receipt_sha256": history[
            "r2_recovery_submission_receipt_sha256"
        ],
        "failed_r8u_r3_job_id": R8U_R3_FAILED_PUBLICATION_RESUME_JOB_ID,
        "r8u_r3_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_SHA256,
        "r8u_r3_scheduler_log_sha256": (
            R8U_R3_FAILED_PUBLICATION_RESUME_LOG_SHA256
        ),
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "resume_capacity_sha256": capacity_sha256,
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "runtime_validation_context": stages.SEALED_SCHEDULER_RUNTIME_REPLAY.value,
        "target_role": "extracted_cache/c3_batch_015/dicom_extraction",
        "cloud_requests_authorized": 0, "downloads_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "dicom_extraction_executions_authorized": 0,
        "echoprime_executions_authorized": 1, "gpu_executions_authorized": 1,
        "failed_partial_adoption_authorized": False,
        "failed_partial_mutation_authorized": False,
        "model_fitting_authorized": False, "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
        "maximum_new_qsub_submissions": 1,
        "pre_qsub_process_projection": dict(pre_qsub_process_projection),
    }
    if set(value) != R8U_R4_AUTHORITY_KEYS:
        _fail("R8U_R4_RESUME_AUTHORITY_INVALID")
    return value


def _r8u_r4_resume_submission_receipt(
    *, implementation_commit: str, resume_job_id: str,
    qsub_environment_sha256: str, resume_authority_sha256: str,
    capacity_sha256: str, pre_qsub_process_projection: Mapping[str, Any],
    initial_qstat_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(resume_job_id) is None:
        _fail("R8U_R4_RESUME_SUBMISSION_INVALID")
    value = {
        **_r8u_r4_common(
            artifact_type=(
                "lvef_c3_r8u_r4_batch16_publication_resume_submission_v1"
            ),
            status="PASS_EXACT_ONE_R8U_R4_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
            implementation_commit=implementation_commit,
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "resume_job_name": _r8u_r4_resume_job_name(implementation_commit),
        "resume_job_id": resume_job_id,
        "resume_qsub_argv_sha256": _sha256_bytes(_canonical_bytes(
            {"argv": _r8u_r4_resume_qsub_command(implementation_commit)}
        )),
        "qsub_environment_sha256": qsub_environment_sha256,
        "resume_authority_sha256": resume_authority_sha256,
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "resume_capacity_sha256": capacity_sha256,
        "resume_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_R4_SCHEDULER_ROOT, "resume")
        ),
        "scheduler_submission_count": 1, "resume_is_array": False,
        "gpu_requested": True, "automatic_retry_authorized": False,
        "cloud_requests": 0, "downloads": 0,
        "dicom_body_reads_by_submitter": 0,
        "dicom_extraction_executions_by_submitter": 0,
        "npz_body_reads_by_submitter": 0, "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
        "pre_qsub_process_projection": dict(pre_qsub_process_projection),
        "initial_qstat_projection": dict(initial_qstat_projection),
    }
    if set(value) != R8U_R4_SUBMISSION_KEYS:
        _fail("R8U_R4_RESUME_SUBMISSION_INVALID")
    return value


def _r8u_r4_require_submit_outputs_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    expected_absent = (
        R8U_R4_ROOT, R8U_R4_PUBLICATION_CLAIM_ROOT,
        R8U_R4_PROBE_WORK_ROOT, R8U_R4_CONTINUATION_ROOT,
        paths["extraction"], paths["extraction_ledger"],
        paths["pooling_ledger"], paths["eligibility_ledger"],
        paths["echoprime"],
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        ATTEMPT_ROOT / "cache_retirement_authorizations"
        / f"{R8U_FIXED_BATCH_ID}.authorization.json",
        paths["final_ledger"], paths["final_receipt"],
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8U_R4_RESUME_OUTPUT_COLLISION")


def _validate_r8u_r4_resume_submission(
    *, current_job_id: str | None = None, wait: bool = False,
) -> tuple[sequential.FullRun, Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(R8U_R4_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8U_R4_RESUME_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    implementation_commit = _current_r8u_r4_implementation_commit()
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id or "R8U_R4_RESUME_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    _candidate, history = _r8u_r4_validate_immutable_r3_failure(run)
    diagnosis = validate_r8u_r4_replay_diagnosis()
    portable = validate_r8u_r4_portable_candidate_authority()
    capacity_value, capacity_payload = _load_private_json(R8U_R4_CAPACITY_PATH)
    try:
        capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
            run.plan, capacity_value,
            completed_extraction_candidate_seal_sha256=(
                R8U_R3_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=int(
                portable["candidate_total_bytes"]
            ),
            r8u_portability_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R4_RESUME_CAPACITY_INVALID") from exc
    authority, authority_payload = _load_private_json(R8U_R4_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_R4_SUBMISSION_PATH)
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    pre_projection = authority.get("pre_qsub_process_projection")
    initial_qstat = submission.get("initial_qstat_projection")
    if not isinstance(pre_projection, Mapping) or not isinstance(initial_qstat, Mapping):
        _fail("R8U_R4_RESUME_SUBMISSION_INVALID")
    expected_authority = _r8u_r4_resume_authority(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=str(authority.get("qsub_environment_sha256", "")),
        prefix_receipts=prefix, history=history,
        capacity_sha256=_sha256_bytes(capacity_payload),
        pre_qsub_process_projection=pre_projection,
    )
    job_id = str(submission.get("resume_job_id", ""))
    expected_submission = _r8u_r4_resume_submission_receipt(
        implementation_commit=implementation_commit, resume_job_id=job_id,
        qsub_environment_sha256=str(authority.get("qsub_environment_sha256", "")),
        resume_authority_sha256=_sha256_bytes(authority_payload),
        capacity_sha256=_sha256_bytes(capacity_payload),
        pre_qsub_process_projection=pre_projection,
        initial_qstat_projection=initial_qstat,
    )
    if (
        diagnosis.get("portable_candidate_fields_equal") is not True
        or capacity_value.get("status") != R8U_R4_CAPACITY_STATUS
        or not _exact_typed_value_equal(authority, expected_authority)
        or not _exact_typed_value_equal(submission, expected_submission)
        or submission.get("pre_qsub_process_projection") != pre_projection
        or (current_job_id is not None and current_job_id != job_id)
    ):
        _fail("R8U_R4_RESUME_SUBMISSION_INVALID")
    return run, authority, submission


def submit_r8u_r4_batch16_publication_resume(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    """Seal portable content, capture capacity once, and issue one qsub."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r4_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    pre_process = _r8u_r4_process_projection(
        environment=environment, runner=process_runner, worker_local=False
    )
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R4_RESUME_SUBMITTER",
        # Login and execution nodes may report different operating-system
        # identities despite the exact sealed Python/CUDA/package runtime.
        # Use the established portable replay context here as well
        # as in the scheduled worker; it excludes only that node-local field.
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    _validate_original_controls()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    _r8u_historical_r8r_chain_authority()
    r3_candidate, history = _r8u_r4_validate_immutable_r3_failure(run)
    _r8u_r4_require_submit_outputs_absent(run)
    # The login-node portable seal is one metadata/control scan.  No NPZ or
    # DICOM body is opened, and no whole-attempt tree is traversed.
    projection = _r8u_r4_portable_candidate_projection(run)
    comparison = _r8u_r4_compare_candidate_projection(
        r3_candidate, projection
    )
    _r8u_r4_raise_portable_comparison(comparison)
    diagnosis = _r8u_r4_candidate_replay_diagnosis(
        comparison=comparison,
        candidate_path_set_equal=True,
        candidate_count_and_bytes_equal=True,
    )
    validate_r8u_r4_replay_diagnosis(diagnosis)
    _create_private_directory_no_clobber(R8U_R4_ROOT)
    diagnosis_sha = _r8u_r4_write_diagnosis_no_clobber(
        R8U_R4_DIAGNOSIS_PATH, diagnosis
    )
    portable = _r8u_r4_portable_candidate_authority(
        implementation_commit=implementation_commit,
        projection=projection, diagnosis_sha256=diagnosis_sha,
    )
    validate_r8u_r4_portable_candidate_authority(
        portable, observed_projection=projection
    )
    portable_sha = _write_private_json(R8U_R4_PORTABLE_AUTHORITY_PATH, portable)
    try:
        capacity_value = capacity.probe_fixed_r8u_r4_batch16_publication_resume_capacity(
            run.plan,
            completed_extraction_candidate_seal_sha256=(
                R8U_R3_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=int(
                portable["candidate_total_bytes"]
            ),
            r8u_portability_repair_commit=implementation_commit,
            process_runner=capacity_process_runner,
        )
        capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
            run.plan, capacity_value,
            completed_extraction_candidate_seal_sha256=(
                R8U_R3_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=int(
                portable["candidate_total_bytes"]
            ),
            r8u_portability_repair_commit=implementation_commit,
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R4_RESUME_CAPACITY_INVALID") from exc
    if capacity_value.get("status") != R8U_R4_CAPACITY_STATUS:
        raise R8RControllerError(
            "R8U_R4_RESUME_CAPACITY_BLOCKED",
            capacity_deficits=_r8u_capacity_deficits(capacity_value),
        )
    _create_private_directory_no_clobber(R8U_R4_SCHEDULER_ROOT)
    capacity_sha = _write_private_json(R8U_R4_CAPACITY_PATH, capacity_value)
    authority = _r8u_r4_resume_authority(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix, history=history,
        capacity_sha256=capacity_sha,
        pre_qsub_process_projection=pre_process,
    )
    authority_sha = _write_private_json(R8U_R4_AUTHORITY_PATH, authority)
    resume_job_id = scheduler._capture_qsub(
        "resume", _r8u_r4_resume_qsub_command(implementation_commit),
        root=R8U_R4_SCHEDULER_ROOT, environment=environment,
        runner=qsub_runner,
    )
    initial_qstat = _validate_r8u_r4_initial_qstat(
        environment=environment, resume_job_id=resume_job_id,
        implementation_commit=implementation_commit,
        runner=qstat_runner,
    )
    submission = _r8u_r4_resume_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id,
        qsub_environment_sha256=environment_sha,
        resume_authority_sha256=authority_sha,
        capacity_sha256=capacity_sha,
        pre_qsub_process_projection=pre_process,
        initial_qstat_projection=initial_qstat,
    )
    _write_private_json(R8U_R4_SUBMISSION_PATH, submission)
    return {
        "status": "BATCH16_PUBLICATION_RESUME_SUBMITTED_AWAITING_TERMINAL",
        "resume_job_id": resume_job_id,
        "capacity_status": R8U_R4_CAPACITY_STATUS,
        "portable_candidate_authority_sha256": portable_sha,
        "candidate_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "observed_npz_files": projection.value["candidate_npz_files"],
        "missing_npz_files": 0, "additional_npz_files": 0,
        "initial_state": initial_qstat["state"],
        "new_qsub_submissions": 1, "login_node_polling_started": False,
        "continuation_submitted": False, "finalizer_submitted": False,
        "cloud_requests": 0, "downloads": 0, "dicom_body_reads": 0,
        "dicom_extraction_executions": 0, "npz_body_reads": 0,
    }


def _r8u_r4_terminal_receipt(
    *, run: sequential.FullRun, final_receipt: Mapping[str, Any]
) -> Mapping[str, Any]:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    required = {
        "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_PATH,
        "r8u_r3_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_PATH,
        "candidate_replay_diagnosis_sha256": R8U_R4_DIAGNOSIS_PATH,
        "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_PATH,
        "live_publication_locality_sha256": R8U_R4_LOCALITY_PATH,
        "publication_primitive_probe_sha256": R8U_R4_PROBE_PATH,
        "publication_claim_sha256": R8U_R4_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R4_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R4_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R4_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R4_SUBMISSION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"] / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            ATTEMPT_ROOT / "cache_retirement_authorizations"
            / f"{R8U_FIXED_BATCH_ID}.authorization.json"
        ),
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    try:
        hashes = {key: core.sha256_file(path) for key, path in required.items()}
    except Exception as exc:
        raise R8RControllerError("R8U_R4_TERMINAL_AUTHORITY_INVALID") from exc
    value = {
        **_r8u_r4_common(
            artifact_type=(
                "lvef_c3_r8u_r4_batch16_publication_resume_terminal_v1"
            ),
            status=R8U_R4_STATUS,
            implementation_commit=_current_r8u_r4_implementation_commit(),
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        **hashes,
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "object_substitution_count": int(final_receipt["object_substitution_count"]),
        "unaccounted_multiframe_objects": int(
            final_receipt["unaccounted_multiframe_objects"]
        ),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "canonical_extraction_cache_retired": final_receipt[
            "extracted_cache_retired"
        ],
        "failed_partial_cache_retained": True,
        "source_candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "cloud_requests": 0, "downloads": 0, "dicom_body_reads": 0,
        "dicom_extraction_executions": 0, "echoprime_executions": 1,
        "embedding_generations": 1, "gpu_executions": 1,
        "model_fitting_count": 0, "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R4_TERMINAL_KEYS:
        _fail("R8U_R4_TERMINAL_AUTHORITY_INVALID")
    return value


def run_r8u_r4_batch16_publication_resume(
    *,
    dependencies: sequential.FullDependencies | None = None,
    probe_invoker: Callable[[Path, Path], Any] | None = None,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Worker-only publication, EchoPrime, preservation, and finalization."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if JOB_RE.fullmatch(job_id) is None or task_text not in {"", "undefined"}:
        _fail("R8U_R4_RESUME_SCHEDULER_CONTEXT_INVALID")
    run, _authority, _submission = _validate_r8u_r4_resume_submission(
        current_job_id=job_id, wait=True
    )
    implementation_commit = _current_r8u_r4_implementation_commit()
    if any(
        os.path.lexists(path)
        for path in (
            R8U_R4_LOCALITY_PATH, R8U_R4_PROBE_PATH,
            R8U_R4_PUBLICATION_CLAIM_ROOT, R8U_R4_PROBE_WORK_ROOT,
            R8U_R4_PUBLICATION_PATH, R8U_R4_EXTRACTION_TRANSITION_ROOT,
            R8U_R4_TERMINAL_PATH,
        )
    ):
        _fail("R8U_R4_RESUME_OUTPUT_COLLISION")
    dependency = sequential.resolve_dependencies(dependencies)
    try:
        dependency.environment_validator(
            run.authority.environment_receipt,
            expected_environment_receipt_sha256=(
                run.runtime_authority["environment_receipt_sha256"]
            ),
            scientific_governing_commit=run.authority.governing_commit,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        if core.sha256_file(run.authority.checkpoint) != run.runtime_authority[
            "checkpoint_sha256"
        ]:
            _fail("R8U_R4_CHECKPOINT_AUTHORITY_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R4_PREBODY_AUTHORITY_FAILED", stage="PREBODY_AUTHORITY"
        ) from exc
    source = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    portable_authority = validate_r8u_r4_portable_candidate_authority()
    # The sole worker candidate scan is reused through publication.  It opens
    # control files only; EchoPrime remains the first NPZ body/hash reader.
    portable_projection = _r8u_r4_portable_candidate_projection(run)
    validate_r8u_r4_portable_candidate_authority(
        portable_authority, observed_projection=portable_projection
    )
    worker_environment, _ = scheduler.build_qsub_environment()
    worker_process = _r8u_r4_process_projection(
        environment=worker_environment, runner=process_runner,
        worker_local=True,
    )
    worker_qstat = _r8u_r4_qstat_projection(
        environment=worker_environment, resume_job_id=job_id,
        implementation_commit=implementation_commit,
        runner=qstat_runner, worker_local=True,
    )
    try:
        claim = _r8u_r4_publication_claim(
            implementation_commit=implementation_commit,
            resume_job_id=job_id,
            worker_process_projection=worker_process,
            worker_qstat_projection=worker_qstat,
        )
        _r8u_r4_create_publication_claim(claim)
        locality = _r8u_r4_live_publication_locality(
            source=source, target=target, publication_claim=claim,
            competing_active_jobs=int(worker_qstat["competing_matching_jobs"]),
            competing_active_processes=int(worker_process["matching_processes"]),
        )
        _write_private_json(R8U_R4_LOCALITY_PATH, locality.value)
        probe = _r8u_r4_primitive_probe(
            implementation_commit=implementation_commit,
            locality=locality, publication_claim=claim,
            invoker=probe_invoker,
        )
        publication = _r8u_r4_publish_candidate(
            source=source, target=target,
            implementation_commit=implementation_commit,
            portable_authority=portable_authority,
            portable_projection=portable_projection,
            live_locality=locality, publication_claim=claim, probe=probe,
            primary_invoker=primary_invoker,
            fallback_invoker=fallback_invoker, write_receipt=True,
        )
    except R8RControllerError as exc:
        raise R8RControllerError(
            exc.code, stage="EXTRACTION_PUBLICATION"
        ) from exc
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    object_keys = {str(row["source_object_key"]) for row in planned["objects"]}
    try:
        stages.advance_stage_ledger(
            input_ledger=paths["download_ledger"],
            output_ledger=paths["extraction_ledger"],
            receipt_root=R8U_R4_EXTRACTION_TRANSITION_ROOT,
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", core.sha256_file(
                    paths["extraction"] / "dicom_audit.restricted.csv"
                )),
                ("EXTRACTION_COMPLETE", core.sha256_file(
                    paths["extraction"] / "extraction_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
        embedding_summary = dependency.echoprime(
            extraction_manifest=(
                paths["extraction"] / "extraction_manifest.restricted.csv"
            ),
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
            checkpoint=run.authority.checkpoint,
            environment_receipt=run.authority.environment_receipt,
            orchestration_contract=run.contract_path,
            batch_plan=run.plan_path, batch_id=R8U_FIXED_BATCH_ID,
            batch_output_root=paths["batch_root"],
            batch_size=dependency.echoprime_batch_size, seed=20260803,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            requirements=run.requirements,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        stages.advance_stage_ledger(
            input_ledger=paths["extraction_ledger"],
            output_ledger=paths["pooling_ledger"],
            receipt_root=paths["echoprime"] / "transition_receipts",
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("EMBEDDING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "clip_manifest.restricted.csv"
                )),
                ("STUDY_POOLING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "study_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R4_BATCH16_ECHOPRIME_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R4_BATCH16_ECHOPRIME_FAILED"
            ), stage="ECHOPRIME_EMBEDDING",
        ) from exc
    try:
        preserved = dependency.preserve(
            contract_path=run.contract_path, plan_path=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID, attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root, output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=run.scheduler_job_identity,
            input_ledger=paths["pooling_ledger"], requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            artifact_validation_context=getattr(
                preservation, "R8U_R4_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY,
            ),
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=R8U_FIXED_BATCH_ID, paths=paths
        )
        dependency.retire(
            run=run, batch_id=R8U_FIXED_BATCH_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            test_only_synthetic_full_scope=False,
            artifact_validation_context=getattr(
                retirement, "R8U_R4_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY,
            ),
        )
        final_receipt = dependency.finalize_batch(
            run=run, batch_id=R8U_FIXED_BATCH_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R4_BATCH16_FINALIZATION_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R4_BATCH16_FINALIZATION_FAILED"
            ), stage="PRESERVATION_RETIREMENT_FINALIZATION",
        ) from exc
    if (
        preserved.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or final_receipt.get("status") != "PASS_BATCH_FINALIZED"
        or final_receipt.get("raw_dicoms_retained") is not True
        or final_receipt.get("extracted_cache_retired") is not True
        or final_receipt.get("n_successfully_extracted_cines")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_clip_embeddings")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_object_technical_dispositions") != 0
        or final_receipt.get("n_blocking_failures") != 0
        or final_receipt.get("n_new_no_cine_studies") != 0
        or final_receipt.get("object_substitution_count") != 0
        or final_receipt.get("unaccounted_multiframe_objects") != 0
        or os.path.lexists(paths["extraction"] / "clips")
    ):
        _fail("R8U_R4_BATCH16_FINALIZATION_INVALID")
    terminal = _r8u_r4_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    _write_private_json(R8U_R4_TERMINAL_PATH, terminal)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return {
        **dict(terminal),
        "publication_ruling": publication["publication_ruling"],
        "embedding_summary_status": embedding_summary.get("status"),
    }


def _r8u_r4_resume_accounting_receipt(
    *, implementation_commit: str, resume_job_id: str,
    accounting: Mapping[str, Any],
) -> Mapping[str, Any]:
    validated = _validate_recovery_accounting_projection(
        accounting, expected_job_id=resume_job_id
    )
    value = {
        **_r8u_r4_common(
            artifact_type=(
                "lvef_c3_r8u_r4_batch16_publication_resume_accounting_v1"
            ),
            status="PASS_R8U_R4_RESUME_QACCT_FAILED_0_EXIT_0",
            implementation_commit=implementation_commit,
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "resume_job_id": resume_job_id, "failed": 0, "exit_status": 0,
        "accounting_projection": dict(validated),
    }
    if set(value) != R8U_R4_ACCOUNTING_KEYS:
        _fail("R8U_R4_RESUME_ACCOUNTING_INVALID")
    return value


def _validate_r8u_r4_resume_accounting() -> Mapping[str, Any]:
    _run, _authority, submission = _validate_r8u_r4_resume_submission()
    value, _ = _load_private_json(R8U_R4_ACCOUNTING_PATH)
    accounting = value.get("accounting_projection")
    if not isinstance(accounting, Mapping):
        _fail("R8U_R4_RESUME_ACCOUNTING_INVALID")
    expected = _r8u_r4_resume_accounting_receipt(
        implementation_commit=_current_r8u_r4_implementation_commit(),
        resume_job_id=str(submission.get("resume_job_id", "")),
        accounting=accounting,
    )
    if not _exact_typed_value_equal(value, expected):
        _fail("R8U_R4_RESUME_ACCOUNTING_INVALID")
    return value


def validate_r8u_r4_resume_terminal() -> Mapping[str, Any]:
    run, _authority, _submission = _validate_r8u_r4_resume_submission()
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=R8U_FIXED_BATCH_ID
    )
    value, _ = _load_private_json(R8U_R4_TERMINAL_PATH)
    expected = _r8u_r4_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    if (
        not _exact_typed_value_equal(value, expected)
        or value.get("status") != R8U_R4_STATUS
        or os.path.lexists(R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
        or os.path.lexists(
            sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
            / "clips"
        )
    ):
        _fail("R8U_R4_TERMINAL_RECEIPT_INVALID")
    _r8u_r4_validate_immutable_r3_failure(run)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return value


def validate_r8u_r4_frozen_partial_evidence() -> Mapping[str, Any]:
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R4_FROZEN_PARTIAL_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    _candidate, history = _r8u_r4_validate_immutable_r3_failure(run)
    value, _ = _load_private_json(R8U_FAILED_PARTIAL_SEAL_PATH)
    if (
        value.get("status") != "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE"
        or core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH)
        != history["failed_partial_seal_sha256"]
    ):
        _fail("R8U_R4_FAILED_PARTIAL_SEAL_INVALID")
    return value


def _r8u_r4_continuation_links() -> Mapping[str, str]:
    paths = {
        "r8u_r3_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_PATH,
        "candidate_replay_diagnosis_sha256": R8U_R4_DIAGNOSIS_PATH,
        "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_PATH,
        "live_publication_locality_sha256": R8U_R4_LOCALITY_PATH,
        "publication_primitive_probe_sha256": R8U_R4_PROBE_PATH,
        "publication_claim_sha256": R8U_R4_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R4_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R4_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R4_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R4_SUBMISSION_PATH,
        "resume_accounting_sha256": R8U_R4_ACCOUNTING_PATH,
        "resume_terminal_receipt_sha256": R8U_R4_TERMINAL_PATH,
    }
    value = {key: core.sha256_file(path) for key, path in paths.items()}
    if set(value) != R8U_R4_CONTINUATION_LINK_KEYS:
        _fail("R8U_R4_CONTINUATION_CHAIN_INVALID")
    return value


def _r8u_r4_continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r4_seq_{implementation_commit[:8]}"


def _r8u_r4_continuation_finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r4_fin_{implementation_commit[:8]}"


def _r8u_r4_continuation_array_command(
    implementation_commit: str,
) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r4_continuation_array_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R4_CONTINUATION_SCHEDULER_ROOT),
        "-t", R8U_FIXED_CONTINUATION_TASK_RANGE,
        "-tc", str(R8U_FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_r4_continuation_finalizer_command(
    implementation_commit: str, array_job_id: str,
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8U_R4_CONTINUATION_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r4_continuation_finalizer_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R4_CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid", array_job_id, "-l", "h_rt=12:00:00",
        "-pe", "omp", "4", "-l", "mem_per_core=8G", str(RUNNER_PATH),
    ]


def _r8u_r4_continuation_claim(
    *, run: sequential.FullRun, implementation_commit: str,
    qsub_environment_sha256: str, prefix_receipts: Sequence[str],
) -> Mapping[str, Any]:
    if (
        len(prefix_receipts) != 16
        or tuple(prefix_receipts[:15])
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
    ):
        _fail("R8U_R4_CONTINUATION_CLAIM_INVALID")
    value = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_fixed_continuation_claim_v1",
            status="AUTHORIZED_FIXED_CONTINUATION_17_19",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r4_continuation_links()),
        "prior_implementation_commit": (
            R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1, "total_new_qsub_maximum": 3,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "cloud_requests_by_submitter": 0, "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0, "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False, "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
    if set(value) != R8U_R4_CONTINUATION_CLAIM_KEYS:
        _fail("R8U_R4_CONTINUATION_CLAIM_INVALID")
    return value


def _r8u_r4_continuation_submission_receipt(
    *, implementation_commit: str, resume_job_id: str,
    array_job_id: str, finalizer_job_id: str,
    qsub_environment_sha256: str, continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        any(JOB_RE.fullmatch(value) is None for value in (
            resume_job_id, array_job_id, finalizer_job_id
        ))
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8U_R4_CONTINUATION_SUBMISSION_INVALID")
    array_command = _r8u_r4_continuation_array_command(implementation_commit)
    finalizer_command = _r8u_r4_continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    value = {
        **_r8u_r4_common(
            artifact_type="lvef_c3_r8u_r4_fixed_continuation_submission_v1",
            status="PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r4_continuation_links()),
        "resume_job_id": resume_job_id,
        "array_job_name": _r8u_r4_continuation_array_job_name(
            implementation_commit
        ),
        "finalizer_job_name": _r8u_r4_continuation_finalizer_job_name(
            implementation_commit
        ),
        "array_job_id": array_job_id, "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": finalizer_command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(_qsub_evidence_authority(
            R8U_R4_CONTINUATION_SCHEDULER_ROOT, "array"
        )),
        "finalizer_qsub_evidence": dict(_qsub_evidence_authority(
            R8U_R4_CONTINUATION_SCHEDULER_ROOT, "finalizer"
        )),
        "scheduler_submission_count": 2, "total_new_qsub_submissions": 3,
        "scheduler_submission_maximum": 3,
        "array_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False, "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0, "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0, "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R4_CONTINUATION_SUBMISSION_KEYS:
        _fail("R8U_R4_CONTINUATION_SUBMISSION_INVALID")
    return value


def submit_r8u_r4_continuation_17_19(
    *, qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Future-only two-qsub continuation, reachable after R4 terminal PASS."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r4_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    run, _authority, resume_submission = _validate_r8u_r4_resume_submission()
    validate_r8u_r4_resume_terminal()
    if os.path.lexists(R8U_R4_ACCOUNTING_PATH) or os.path.lexists(
        R8U_R4_CONTINUATION_ROOT
    ):
        _fail("R8U_R4_CONTINUATION_OUTPUT_COLLISION")
    resume_job_id = str(resume_submission.get("resume_job_id", ""))
    accounting_projection = _query_recovery_accounting(
        recovery_job_id=resume_job_id, environment=environment,
        runner=qacct_runner,
    )
    accounting = _r8u_r4_resume_accounting_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id, accounting=accounting_projection,
    )
    _write_private_json(R8U_R4_ACCOUNTING_PATH, accounting)
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    _create_private_directory_no_clobber(R8U_R4_CONTINUATION_ROOT)
    _create_private_directory_no_clobber(R8U_R4_CONTINUATION_SCHEDULER_ROOT)
    claim = _r8u_r4_continuation_claim(
        run=run, implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha, prefix_receipts=prefix,
    )
    claim_sha = _write_private_json(R8U_R4_CONTINUATION_CLAIM_PATH, claim)
    array_job_id = scheduler._capture_qsub(
        "array", _r8u_r4_continuation_array_command(implementation_commit),
        root=R8U_R4_CONTINUATION_SCHEDULER_ROOT,
        environment=environment, runner=qsub_runner,
        parser=_parse_r8u_array_qsub_stdout,
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer", _r8u_r4_continuation_finalizer_command(
            implementation_commit, array_job_id
        ),
        root=R8U_R4_CONTINUATION_SCHEDULER_ROOT,
        environment=environment, runner=qsub_runner,
    )
    submission = _r8u_r4_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id, array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha,
        continuation_claim_sha256=claim_sha,
    )
    _write_private_json(R8U_R4_CONTINUATION_SUBMISSION_PATH, submission)
    return {
        "status": "R8U_R4_CONTINUATION_17_19_SUBMITTED",
        "array_job_id": array_job_id, "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": 1, "new_qsub_submissions": 2,
        "total_new_qsub_submissions": 3, "cloud_requests": 0,
    }


def validate_r8u_r4_continuation_worker_submission(
    *, current_job_id: str,
) -> Mapping[str, Any]:
    """Closed future hook for Tasks 17--19 and their held finalizer."""

    if JOB_RE.fullmatch(current_job_id) is None:
        _fail("R8U_R4_CONTINUATION_WORKER_AUTHORITY_INVALID")
    deadline = time.monotonic() + 60.0
    while not os.path.lexists(R8U_R4_CONTINUATION_SUBMISSION_PATH):
        if time.monotonic() >= deadline:
            _fail("R8U_R4_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT")
        time.sleep(0.25)
    validate_r8u_r4_resume_terminal()
    _validate_r8u_r4_resume_accounting()
    claim, _ = _load_private_json(R8U_R4_CONTINUATION_CLAIM_PATH)
    submission, _ = _load_private_json(R8U_R4_CONTINUATION_SUBMISSION_PATH)
    links = _r8u_r4_continuation_links()
    if (
        set(claim) != R8U_R4_CONTINUATION_CLAIM_KEYS
        or set(submission) != R8U_R4_CONTINUATION_SUBMISSION_KEYS
        or any(claim.get(key) != value for key, value in links.items())
        or any(submission.get(key) != value for key, value in links.items())
        or current_job_id
        not in {
            str(submission.get("array_job_id", "")),
            str(submission.get("finalizer_job_id", "")),
        }
        or submission.get("array_task_range") != R8U_FIXED_CONTINUATION_TASK_RANGE
        or submission.get("array_task_count") != 3
        or submission.get("array_max_concurrency") != 1
        or submission.get("scheduler_submission_count") != 2
        or submission.get("total_new_qsub_submissions") != 3
        or submission.get("fourth_submission_reachable") is not False
    ):
        _fail("R8U_R4_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return submission


def run_r8u_r4_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None or not task_text.isdigit()
        or int(task_text) not in R8U_FIXED_CONTINUATION_TASK_IDS
    ):
        _fail("R8U_R4_CONTINUATION_ARRAY_CONTEXT_INVALID")
    validate_r8u_r4_continuation_worker_submission(current_job_id=job_id)
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8U_R4_FIXED_CONTINUATION
    )
    value = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if value.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8U_R4_CONTINUATION_BATCH_NOT_FINALIZED")
    return value


def run_r8u_r4_continuation_finalizer() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8U_R4_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    validate_r8u_r4_continuation_worker_submission(current_job_id=job_id)
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r4=True,
    )
    implementation_commit = _current_r8u_r4_implementation_commit()
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    historical = _r8u_historical_r8r_chain_authority()
    authority = finalizer.R8UR4ImplementationAuthority(
        implementation_commit=implementation_commit,
        historical_r8r_recovery_authority_sha256=(
            historical["recovery_authority_sha256"]
        ),
        historical_r8r_recovery_terminal_receipt_sha256=(
            historical["recovery_terminal_receipt_sha256"]
        ),
        historical_r8r_continuation_capacity_receipt_sha256=(
            historical["continuation_capacity_receipt_sha256"]
        ),
        historical_r8r_continuation_claim_sha256=(
            historical["continuation_claim_sha256"]
        ),
        historical_r8r_continuation_submission_receipt_sha256=(
            historical["continuation_submission_receipt_sha256"]
        ),
        failed_partial_seal_sha256=core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        r2_recovery_capacity_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_CAPACITY_PATH
        ),
        r2_recovery_authority_sha256=core.sha256_file(
            R8U_RECOVERY_AUTHORITY_PATH
        ),
        r2_recovery_submission_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_SUBMISSION_PATH
        ),
        r3_extraction_candidate_seal_sha256=core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        replay_diagnosis_sha256=core.sha256_file(R8U_R4_DIAGNOSIS_PATH),
        portable_candidate_authority_sha256=core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        live_publication_locality_sha256=core.sha256_file(
            R8U_R4_LOCALITY_PATH
        ),
        publication_primitive_probe_sha256=core.sha256_file(R8U_R4_PROBE_PATH),
        publication_claim_sha256=core.sha256_file(
            R8U_R4_PUBLICATION_CLAIM_PATH
        ),
        publication_receipt_sha256=core.sha256_file(R8U_R4_PUBLICATION_PATH),
        resume_capacity_receipt_sha256=core.sha256_file(R8U_R4_CAPACITY_PATH),
        resume_authority_sha256=core.sha256_file(R8U_R4_AUTHORITY_PATH),
        resume_submission_receipt_sha256=core.sha256_file(
            R8U_R4_SUBMISSION_PATH
        ),
        resume_accounting_sha256=core.sha256_file(R8U_R4_ACCOUNTING_PATH),
        resume_terminal_receipt_sha256=core.sha256_file(R8U_R4_TERMINAL_PATH),
        continuation_claim_sha256=core.sha256_file(
            R8U_R4_CONTINUATION_CLAIM_PATH
        ),
        continuation_submission_receipt_sha256=core.sha256_file(
            R8U_R4_CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan, requirements=run.requirements,
        production_root=run.production_root, contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8u_r4_implementation_authority=authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("selected_subjects") != 4_530
        or summary.get("verified_source_objects") != 335_984
        or summary.get("selected_source_bytes") != 1_216_569_133_322
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("new_no_cine_studies") != 0
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical") is not True
        or summary.get("implementation_authority_epoch_count") != 3
        or summary.get("r8u_implementation_commit") != implementation_commit
        or SHA_RE.fullmatch(str(
            summary.get("r8u_recovery_continuation_authority_sha256")
        )) is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8U_R4_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return summary


# ---------------------------------------------------------------------------
# Fixed R8U-R5 submitter/compute-worker identity separation
# ---------------------------------------------------------------------------


def _r8u_r5_common(
    *, artifact_type: str, status: str, implementation_commit: str,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": status,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "implementation_authority_epochs": dict(
            _r8u_r5_implementation_authority_epochs(implementation_commit)
        ),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": R8U_FIXED_BATCH_ID,
    }
    if set(value) != R8U_R5_COMMON_KEYS:
        _fail("R8U_R5_CONTROL_SCHEMA_INVALID")
    return value


def _r8u_r5_read_fixed_r4_scheduler_log() -> tuple[bytes, str]:
    path = (
        R8U_R4_SCHEDULER_ROOT
        / R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BASENAME
    )
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > R8U_SCHEDULER_LOG_MAX_BYTES:
                    _fail("R8U_R5_R4_FAILURE_EVIDENCE_INVALID")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except R8RControllerError:
        raise
    except OSError as exc:
        raise R8RControllerError(
            "R8U_R5_R4_FAILURE_EVIDENCE_INVALID"
        ) from exc
    payload = b"".join(chunks)
    digest = _sha256_bytes(payload)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode)
        != R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_MODE
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(payload) != R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BYTES
        or digest != R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_SHA256
        or b"R8U_R4_STATUS=BLOCKED_SCHEDULER_IDENTITY_INVALID\n" not in payload
        or b"R8U_BATCH16_CLOUD_REQUESTS=0\n" not in payload
        or b"R8U_BATCH16_DOWNLOAD_RERUNS=0\n" not in payload
    ):
        _fail("R8U_R5_R4_FAILURE_EVIDENCE_INVALID")
    return payload, digest


def _r8u_r5_r4_failure_evidence(
    *, implementation_commit: str, run: sequential.FullRun,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Validate consumed R4 controls without re-running its candidate scan."""

    _candidate, _history = _r8u_r4_validate_immutable_r3_failure(run)
    diagnosis = validate_r8u_r4_replay_diagnosis()
    portable = validate_r8u_r4_portable_candidate_authority()
    capacity_value, _ = _load_private_json(R8U_R4_CAPACITY_PATH)
    try:
        capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
            run.plan,
            capacity_value,
            completed_extraction_candidate_seal_sha256=(
                R8U_R3_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=int(
                portable["candidate_total_bytes"]
            ),
            r8u_portability_repair_commit=(
                R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R5_R4_FAILURE_EVIDENCE_INVALID"
        ) from exc
    authority, _ = _load_private_json(R8U_R4_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_R4_SUBMISSION_PATH)
    _payload, log_sha = _r8u_r5_read_fixed_r4_scheduler_log()
    if (
        diagnosis.get("portable_candidate_fields_equal") is not True
        or portable.get("status") != "PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY"
        or portable.get("candidate_npz_files") != R8U_R3_CANDIDATE_NPZ_FILES
        or authority.get("implementation_commit")
        != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        or authority.get("status")
        != "AUTHORIZED_FIXED_R8U_R4_BATCH16_PUBLICATION_RESUME"
        or authority.get("portable_candidate_authority_sha256")
        != core.sha256_file(R8U_R4_PORTABLE_AUTHORITY_PATH)
        or submission.get("implementation_commit")
        != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        or submission.get("status")
        != "PASS_EXACT_ONE_R8U_R4_GPU_BATCH16_PUBLICATION_RESUME_QSUB"
        or submission.get("resume_job_id")
        != R8U_R4_FAILED_SCHEDULER_IDENTITY_JOB_ID
        or submission.get("resume_job_name") != "lvef_c3_r8u_r4_res_6eb5c9a4"
        or submission.get("resume_authority_sha256")
        != core.sha256_file(R8U_R4_AUTHORITY_PATH)
        or submission.get("portable_candidate_authority_sha256")
        != core.sha256_file(R8U_R4_PORTABLE_AUTHORITY_PATH)
        or submission.get("resume_capacity_sha256")
        != core.sha256_file(R8U_R4_CAPACITY_PATH)
        or os.path.lexists(R8U_R4_LOCALITY_PATH)
        or os.path.lexists(R8U_R4_PROBE_PATH)
        or os.path.lexists(R8U_R4_PUBLICATION_CLAIM_ROOT)
        or os.path.lexists(R8U_R4_PUBLICATION_PATH)
        or os.path.lexists(R8U_R4_TERMINAL_PATH)
    ):
        _fail("R8U_R5_R4_FAILURE_EVIDENCE_INVALID")
    evidence = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_r4_scheduler_identity_failure_v1",
            status="PASS_IMMUTABLE_R8U_R4_SCHEDULER_IDENTITY_FAILURE",
            implementation_commit=implementation_commit,
        ),
        "failed_job_id": R8U_R4_FAILED_SCHEDULER_IDENTITY_JOB_ID,
        "scheduler_failed": 0,
        "application_exit_status": 78,
        "wall_seconds": 275,
        "first_failed_stage": "PRE_BODY_WORKER_SCHEDULER_IDENTITY_VALIDATION",
        "exact_failure_code": "SCHEDULER_IDENTITY_INVALID",
        "scheduler_log_basename": R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BASENAME,
        "scheduler_log_bytes": R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BYTES,
        "scheduler_log_mode": "0644",
        "scheduler_log_sha256": log_sha,
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "r8u_r4_capacity_sha256": core.sha256_file(R8U_R4_CAPACITY_PATH),
        "r8u_r4_resume_authority_sha256": core.sha256_file(
            R8U_R4_AUTHORITY_PATH
        ),
        "r8u_r4_submission_sha256": core.sha256_file(R8U_R4_SUBMISSION_PATH),
        "portable_candidate_pass": True,
        "publication_locality_ran": False,
        "publication_ran": False,
        "echoprime_ran": False,
    }
    if set(evidence) != R8U_R5_R4_FAILURE_EVIDENCE_KEYS:
        _fail("R8U_R5_R4_FAILURE_EVIDENCE_INVALID")
    return evidence, portable


def _r8u_r5_scheduler_account_authority(
    *, implementation_commit: str, environment: Mapping[str, str],
    qsub_environment_sha256: str,
) -> Mapping[str, Any]:
    try:
        account = pwd.getpwuid(os.geteuid())
        runner_sha = core.sha256_file(RUNNER_PATH)
        if Path(sys.executable) != scheduler.ECHOPRIME_PYTHON:
            _fail("R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
        python_sha = stages.resolved_python_executable_sha256(
            scheduler.ECHOPRIME_PYTHON
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID"
        ) from exc
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_scheduler_account_authority_v1",
            status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
            implementation_commit=implementation_commit,
        ),
        "expected_effective_uid": int(os.geteuid()),
        "expected_scheduler_username": str(account.pw_name),
        "canonical_home": str(account.pw_dir),
        "submitter_passwd_lookup_available": True,
        "runner_sha256": runner_sha,
        "python_sha256": python_sha,
        "qsub_environment_sha256": qsub_environment_sha256,
        "sealed_qsub_environment": dict(sorted(environment.items())),
        "authorized_worker_roles": list(R8U_R5_WORKER_ROLES),
    }
    if (
        set(value) != R8U_R5_ACCOUNT_AUTHORITY_KEYS
        or environment.get("USER") != account.pw_name
        or environment.get("LOGNAME") != account.pw_name
        or environment.get("HOME") != account.pw_dir
        or scheduler.qsub_environment_sha256(environment)
        != qsub_environment_sha256
    ):
        _fail("R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
    return value


def validate_r8u_r5_scheduler_account_authority(
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    authority = value
    if authority is None:
        authority, _ = _load_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    if not isinstance(authority, Mapping):
        _fail("R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
    implementation_commit = str(authority.get("implementation_commit", ""))
    expected_common = (
        _r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_scheduler_account_authority_v1",
            status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
            implementation_commit=implementation_commit,
        )
        if COMMIT_RE.fullmatch(implementation_commit) is not None
        else {}
    )
    sealed = authority.get("sealed_qsub_environment")
    username = authority.get("expected_scheduler_username")
    canonical_home = authority.get("canonical_home")
    if (
        set(authority) != R8U_R5_ACCOUNT_AUTHORITY_KEYS
        or not expected_common
        or any(authority.get(key) != item for key, item in expected_common.items())
        or isinstance(authority.get("expected_effective_uid"), bool)
        or not isinstance(authority.get("expected_effective_uid"), int)
        or authority.get("expected_effective_uid", -1) < 0
        or not isinstance(username, str)
        or scheduler.SAFE_ACCOUNT_RE.fullmatch(username) is None
        or not isinstance(canonical_home, str)
        or not Path(canonical_home).is_absolute()
        or os.path.normpath(canonical_home) != canonical_home
        or authority.get("submitter_passwd_lookup_available") is not True
        or authority.get("authorized_worker_roles") != list(R8U_R5_WORKER_ROLES)
        or any(
            SHA_RE.fullmatch(str(authority.get(field, ""))) is None
            for field in (
                "runner_sha256", "python_sha256",
                "qsub_environment_sha256",
            )
        )
        or not isinstance(sealed, Mapping)
        or not sealed
        or any(
            not isinstance(name, str)
            or not isinstance(item, str)
            or not name
            or any(character in name + item for character in ("\x00", "\n", "\r"))
            for name, item in sealed.items()
        )
    ):
        _fail("R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
    return authority


def _r8u_r5_require_account_file_owner_matches_effective_uid() -> None:
    """Expose a true kernel-EUID contradiction before owner-private reads."""

    try:
        info = os.lstat(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    except OSError as exc:
        raise R8RControllerError(
            "R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID"
        ) from exc
    if info.st_uid != os.geteuid():
        _fail("SCHEDULER_EFFECTIVE_UID_MISMATCH")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        _fail("R8U_R5_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")


def _r8u_r5_build_worker_context(
    *, account_authority: Mapping[str, Any], expected_job_id: str,
    expected_role: str, expected_task_id: str | None = None,
) -> scheduler.WorkerSchedulerContext:
    account = validate_r8u_r5_scheduler_account_authority(account_authority)
    if expected_role not in R8U_R5_WORKER_ROLES:
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    try:
        observed_commit = _current_r8u_r5_implementation_commit()
    except Exception as exc:
        raise R8RControllerError(
            "SCHEDULER_IMPLEMENTATION_COMMIT_MISMATCH"
        ) from exc
    try:
        observed_runner_sha = core.sha256_file(RUNNER_PATH)
    except Exception as exc:
        raise R8RControllerError("SCHEDULER_RUNNER_AUTHORITY_MISMATCH") from exc
    if Path(sys.executable) != scheduler.ECHOPRIME_PYTHON:
        _fail("SCHEDULER_PYTHON_AUTHORITY_MISMATCH")
    try:
        observed_python_sha = stages.resolved_python_executable_sha256(
            Path(sys.executable)
        )
    except Exception as exc:
        raise R8RControllerError("SCHEDULER_PYTHON_AUTHORITY_MISMATCH") from exc
    try:
        return scheduler.build_worker_scheduler_context(
            expected_effective_uid=int(account["expected_effective_uid"]),
            expected_scheduler_username=str(account["expected_scheduler_username"]),
            canonical_home=str(account["canonical_home"]),
            expected_job_id=expected_job_id,
            expected_job_role=expected_role,
            observed_job_role=expected_role,
            expected_qsub_environment_sha256=str(
                account["qsub_environment_sha256"]
            ),
            sealed_qsub_environment=dict(account["sealed_qsub_environment"]),
            expected_implementation_commit=str(account["implementation_commit"]),
            observed_implementation_commit=observed_commit,
            expected_runner_sha256=str(account["runner_sha256"]),
            observed_runner_sha256=observed_runner_sha,
            expected_python_sha256=str(account["python_sha256"]),
            observed_python_sha256=observed_python_sha,
            source_environment=os.environ,
            expected_task_id=expected_task_id,
        )
    except scheduler.FullSchedulerError as exc:
        raise R8RControllerError(exc.code) from exc


def _r8u_r5_process_projection(
    *, environment: Mapping[str, str], runner: Callable[..., Any],
    worker_self_marker: str | None,
) -> Mapping[str, Any]:
    command = ["/bin/ps", "-axo", "pid=,uid=,command="]
    completed = runner(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if completed.returncode != 0 or completed.stderr or len(payload) > 16 * 1024 * 1024:
        _fail("R8U_R5_WORKER_PROCESS_PROJECTION_INVALID")
    try:
        lines = payload.decode("utf-8", "strict").splitlines()
    except UnicodeError as exc:
        raise R8RControllerError(
            "R8U_R5_WORKER_PROCESS_PROJECTION_INVALID"
        ) from exc
    markers = (
        "--run-array-task", "--run-cohort-finalizer",
        "--recover-batch3-preservation", "--run-continuation-array-task",
        "--run-continuation-finalizer", "--run-batch16-recovery",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
        "--run-r8u-r3-batch16-publication-resume",
        "--run-r8u-r3-continuation-17-19-array-task",
        "--run-r8u-r3-continuation-finalizer",
        "--run-r8u-r4-batch16-publication-resume",
        "--run-r8u-r4-continuation-17-19-array-task",
        "--run-r8u-r4-continuation-finalizer",
        "--run-r8u-r5-worker-context-probe",
        "--run-r8u-r5-batch16-publication-resume",
        "--run-r8u-r5-continuation-17-19-array-task",
        "--run-r8u-r5-continuation-finalizer",
        "run_production_dicom_extraction", "run_production_echoprime",
        "preserve_lvef_c3_production_batch", "retire_lvef_c3_extracted_cache",
        "finalize_lvef_c3_production", "lvef_c3_r8u_",
    )
    matching = 0
    self_seen = 0
    for line in lines:
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid = int(fields[0])
            uid = int(fields[1])
        except ValueError:
            _fail("R8U_R5_WORKER_PROCESS_PROJECTION_INVALID")
        if uid != os.geteuid():
            continue
        if worker_self_marker is not None and pid == os.getpid():
            if worker_self_marker not in fields[2]:
                _fail("R8U_R5_WORKER_PROCESS_PROJECTION_INVALID")
            self_seen += 1
            continue
        if any(marker in fields[2] for marker in markers):
            matching += 1
    if matching != 0 or (worker_self_marker is not None and self_seen != 1):
        _fail("R8U_R5_WORKER_PROCESS_PROJECTION_INVALID")
    value = {
        "status": "PASS_ZERO_COMPETING_R8U_R5_WORKER_PROCESSES",
        "matching_processes": 0,
        "process_snapshot_count": 1,
        "ps_argv_sha256": _sha256_bytes(_canonical_bytes({"argv": command})),
        "ps_stdout_sha256": _sha256_bytes(payload),
    }
    if set(value) != R8U_R3_PROCESS_PROJECTION_KEYS:
        _fail("R8U_R5_WORKER_PROCESS_PROJECTION_INVALID")
    return value


def _r8u_r5_qstat_projection(
    *, environment: Mapping[str, str], expected_job_id: str,
    expected_job_name: str, runner: Callable[..., Any], worker_local: bool,
    expected_task_id: str | None = None,
    allowed_companion_job_id: str | None = None,
    allowed_companion_job_name: str | None = None,
) -> Mapping[str, Any]:
    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0 or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper()
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("SCHEDULER_JOB_ROLE_MISMATCH") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    records: list[tuple[str, str, str, str, str | None]] = []
    for job in root.iter():
        if local(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local(child), []).append(child.text or "")
        if any(len(fields.get(key, ())) != 1 for key in (
            "JB_job_number", "JB_name", "state"
        )):
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        tasks = fields.get("tasks", [])
        if len(tasks) > 1:
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        records.append((
            fields["JB_job_number"][0], fields["JB_name"][0],
            fields["state"][0], str(job.get("state", "")),
            tasks[0] if tasks else None,
        ))
    role_names = re.compile(
        r"lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
        r"r8u_(?:rec|seq|fin)|r8u_r3_(?:res|seq|fin)|"
        r"r8u_r4_(?:res|seq|fin)|r8u_r5_(?:ctx|res|seq|fin))_"
        r"[0-9a-f]{8}|c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}"
    )
    if (allowed_companion_job_id is None) != (
        allowed_companion_job_name is None
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    if allowed_companion_job_id is not None and (
        JOB_RE.fullmatch(allowed_companion_job_id) is None
        or allowed_companion_job_id == expected_job_id
        or allowed_companion_job_name is None
        or re.fullmatch(
            r"lvef_c3_r8u_r5_(?:ctx|res|seq|fin)_[0-9a-f]{8}",
            allowed_companion_job_name,
        )
        is None
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    expected_records = [
        record for record in records if record[0] == expected_job_id
    ]
    if not expected_records or any(
        record[1] != expected_job_name for record in expected_records
    ) or any(
        record[2] not in {"r", "qw", "t", "Rr"}
        or record[3]
        != ("running" if record[2] in {"r", "t", "Rr"} else "pending")
        for record in expected_records
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")

    def task_set(text: str | None) -> set[str]:
        if text is None:
            return set()
        if re.fullmatch(r"1[7-9]", text):
            return {text}
        match = re.fullmatch(r"(1[7-9])-(1[7-9])(?::1)?", text)
        if match is None:
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        first, last = int(match.group(1)), int(match.group(2))
        if first > last:
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        return {str(task) for task in range(first, last + 1)}

    if expected_task_id is None:
        if any(record[4] not in {None, "", "undefined"} for record in expected_records):
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        matches = expected_records
    else:
        if expected_task_id not in {"17", "18", "19"}:
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        projected_tasks: set[str] = set()
        for record in expected_records:
            tasks = task_set(record[4])
            if projected_tasks & tasks:
                _fail("SCHEDULER_JOB_ROLE_MISMATCH")
            projected_tasks.update(tasks)
        if not projected_tasks <= {"17", "18", "19"}:
            _fail("SCHEDULER_JOB_ROLE_MISMATCH")
        matches = [
            record for record in expected_records
            if expected_task_id in task_set(record[4])
        ]
    companion_records = [
        record
        for record in records
        if allowed_companion_job_id is not None
        and record[0] == allowed_companion_job_id
    ]
    if allowed_companion_job_id is not None and (
        len(companion_records) != 1
        or companion_records[0][1] != allowed_companion_job_name
        or companion_records[0][2] != "hqw"
        or companion_records[0][3] != "pending"
        or companion_records[0][4] not in {None, "", "undefined"}
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    competing = [
        record for record in records
        if record[0] not in {
            expected_job_id,
            allowed_companion_job_id,
        }
        and role_names.fullmatch(record[1])
    ]
    if len(matches) != 1 or competing:
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    _job_id, name, state, category, _tasks = matches[0]
    expected_category = "running" if state in {"r", "t", "Rr"} else "pending"
    if (
        name != expected_job_name
        or state not in {"r", "qw", "t", "Rr"}
        or (expected_task_id is not None and state not in {"r", "t", "Rr"})
        or category != expected_category
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    body = {
        "status": (
            "PASS_EXACT_ONE_R8U_R5_WORKER_JOB_ZERO_COMPETITORS"
            if worker_local
            else "PASS_EXACT_ONE_R8U_R5_SUBMITTED_JOB_ZERO_COMPETITORS"
        ),
        "resume_job_id": expected_job_id,
        "resume_job_name": name,
        "state": state,
        "category": category,
        "target_matches": 1,
        "competing_matching_jobs": 0,
        "qstat_snapshot_count": 1,
    }
    return {**body, "qstat_projection_sha256": core.canonical_json_sha256(body)}


def _r8u_r5_probe_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r5_ctx_{implementation_commit[:8]}"


def _r8u_r5_resume_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r5_res_{implementation_commit[:8]}"


def _r8u_r5_probe_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N", _r8u_r5_probe_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R5_SCHEDULER_ROOT),
        "-l", "h_rt=00:10:00", "-pe", "omp", "1",
        "-l", "mem_per_core=1G", str(RUNNER_PATH),
    ]


def _r8u_r5_resume_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N", _r8u_r5_resume_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R5_SCHEDULER_ROOT),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _validate_r8u_r5_r4_failure_evidence(
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    evidence = value
    if evidence is None:
        evidence, _ = _load_private_json(R8U_R5_R4_FAILURE_EVIDENCE_PATH)
    implementation_commit = _current_r8u_r5_implementation_commit()
    common = _r8u_r5_common(
        artifact_type="lvef_c3_r8u_r5_r4_scheduler_identity_failure_v1",
        status="PASS_IMMUTABLE_R8U_R4_SCHEDULER_IDENTITY_FAILURE",
        implementation_commit=implementation_commit,
    )
    fixed = {
        "failed_job_id": R8U_R4_FAILED_SCHEDULER_IDENTITY_JOB_ID,
        "scheduler_failed": 0,
        "application_exit_status": 78,
        "wall_seconds": 275,
        "first_failed_stage": "PRE_BODY_WORKER_SCHEDULER_IDENTITY_VALIDATION",
        "exact_failure_code": "SCHEDULER_IDENTITY_INVALID",
        "scheduler_log_basename": R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BASENAME,
        "scheduler_log_bytes": R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BYTES,
        "scheduler_log_mode": "0644",
        "scheduler_log_sha256": R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_SHA256,
        "candidate_replay_diagnosis_sha256": core.sha256_file(
            R8U_R4_DIAGNOSIS_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "r8u_r4_capacity_sha256": core.sha256_file(R8U_R4_CAPACITY_PATH),
        "r8u_r4_resume_authority_sha256": core.sha256_file(
            R8U_R4_AUTHORITY_PATH
        ),
        "r8u_r4_submission_sha256": core.sha256_file(R8U_R4_SUBMISSION_PATH),
        "portable_candidate_pass": True,
        "publication_locality_ran": False,
        "publication_ran": False,
        "echoprime_ran": False,
    }
    if (
        not isinstance(evidence, Mapping)
        or set(evidence) != R8U_R5_R4_FAILURE_EVIDENCE_KEYS
        or any(evidence.get(key) != item for key, item in common.items())
        or any(evidence.get(key) != item for key, item in fixed.items())
    ):
        _fail("R8U_R5_R4_FAILURE_EVIDENCE_INVALID")
    _r8u_r5_read_fixed_r4_scheduler_log()
    return evidence


def _r8u_r5_worker_diagnostic(
    context: scheduler.WorkerSchedulerContext,
) -> Mapping[str, Any]:
    diagnostics = context.diagnostics
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r5_worker_scheduler_context_v1",
        "status": "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        **diagnostics._asdict(),
        "classifications": list(diagnostics.classifications),
        "canonical_worker_environment_status": "PASS",
    }
    boolean_fields = R8U_R5_WORKER_DIAGNOSTIC_KEYS - {
        "schema_version", "artifact_type", "status", "passwd_lookup_status",
        "classifications", "canonical_worker_environment_status",
    }
    if (
        set(value) != R8U_R5_WORKER_DIAGNOSTIC_KEYS
        or any(type(value.get(field)) is not bool for field in boolean_fields)
        or value.get("passwd_lookup_status")
        not in scheduler.WORKER_PASSWD_LOOKUP_STATUSES
        or not isinstance(value.get("classifications"), list)
        or not value["classifications"]
        or any(
            item not in scheduler.WORKER_DIAGNOSTIC_CLASSIFICATIONS
            for item in value["classifications"]
        )
        or value.get("effective_uid_match") is not True
        or value.get("job_id_match") is not True
        or value.get("task_context_match") is not True
        or value.get("job_role_match") is not True
        or value.get("runner_sha256_match") is not True
        or value.get("python_sha256_match") is not True
        or value.get("implementation_commit_match") is not True
        or value.get("qsub_environment_sha256_match") is not True
    ):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")
    return value


def _validate_r8u_r5_worker_diagnostic(
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    boolean_fields = R8U_R5_WORKER_DIAGNOSTIC_KEYS - {
        "schema_version", "artifact_type", "status", "passwd_lookup_status",
        "classifications", "canonical_worker_environment_status",
    }
    if (
        set(value) != R8U_R5_WORKER_DIAGNOSTIC_KEYS
        or value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_r8u_r5_worker_scheduler_context_v1"
        or value.get("status") != "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT"
        or any(type(value.get(field)) is not bool for field in boolean_fields)
        or value.get("passwd_lookup_status")
        not in scheduler.WORKER_PASSWD_LOOKUP_STATUSES
        or value.get("canonical_worker_environment_status") != "PASS"
        or not isinstance(value.get("classifications"), list)
        or not value["classifications"]
        or any(
            item not in scheduler.WORKER_DIAGNOSTIC_CLASSIFICATIONS
            for item in value["classifications"]
        )
        or any(value.get(field) is not True for field in (
            "effective_uid_match", "job_id_match", "task_context_match",
            "job_role_match", "runner_sha256_match", "python_sha256_match",
            "implementation_commit_match", "qsub_environment_sha256_match",
        ))
    ):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")
    return value


def _r8u_r5_aggregate_match_label(
    diagnostic: Mapping[str, Any], *, field: str,
) -> str:
    present_field = {
        "observed_user_match": "user_present",
        "observed_logname_match": "logname_present",
        "observed_home_match": "home_present",
        "observed_shell_match": "shell_present",
    }.get(field)
    if (
        present_field is None
        or type(diagnostic.get(present_field)) is not bool
        or type(diagnostic.get(field)) is not bool
    ):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")
    if diagnostic[present_field] is not True:
        return "NOT_AVAILABLE"
    return "YES" if diagnostic[field] is True else "NO"


def _r8u_r5_aggregate_passwd_status(value: object) -> str:
    if value == "PASS":
        return "PASS"
    if value == "LOOKUP_UNAVAILABLE":
        return "UNAVAILABLE"
    if value in {"NAME_MISMATCH", "HOME_MISMATCH", "SHELL_MISMATCH"}:
        return "MISMATCH"
    _fail("SCHEDULER_WORKER_CONTEXT_INVALID")


def _r8u_r5_probe_authority(
    *, implementation_commit: str, account_authority_sha256: str,
    r4_failure_evidence_sha256: str, qsub_environment_sha256: str,
) -> Mapping[str, Any]:
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_worker_context_probe_authority_v1",
            status="AUTHORIZED_R8U_R5_WORKER_CONTEXT_PROBE",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": account_authority_sha256,
        "r8u_r4_failure_evidence_sha256": r4_failure_evidence_sha256,
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "worker_role": R8U_R5_PROBE_ROLE,
        "wall_seconds_maximum": 600,
        "cpu_slots": 1,
        "gpu_requested": False,
        "array_requested": False,
        "candidate_scan_authorized": False,
        "cloud_requests_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "npz_body_reads_authorized": 0,
        "publication_authorized": False,
        "extraction_authorized": False,
        "embedding_generation_authorized": False,
        "preservation_authorized": False,
        "scientific_attempt_mutation_authorized": False,
    }
    if (
        set(value) != R8U_R5_PROBE_AUTHORITY_KEYS
        or any(
            SHA_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "scheduler_account_authority_sha256",
                "r8u_r4_failure_evidence_sha256",
                "portable_candidate_authority_sha256",
                "qsub_environment_sha256",
            )
        )
    ):
        _fail("R8U_R5_PROBE_AUTHORITY_INVALID")
    return value


def _r8u_r5_probe_submission_receipt(
    *, implementation_commit: str, probe_job_id: str,
    account_authority_sha256: str, r4_failure_evidence_sha256: str,
    probe_authority_sha256: str, qsub_environment_sha256: str,
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(probe_job_id) is None:
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_worker_context_probe_submission_v1",
            status="PASS_EXACT_ONE_R8U_R5_CPU_WORKER_CONTEXT_PROBE_QSUB",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": account_authority_sha256,
        "r8u_r4_failure_evidence_sha256": r4_failure_evidence_sha256,
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "probe_authority_sha256": probe_authority_sha256,
        "probe_job_name": _r8u_r5_probe_job_name(implementation_commit),
        "probe_job_id": probe_job_id,
        "probe_qsub_argv_sha256": _sha256_bytes(_canonical_bytes({
            "argv": _r8u_r5_probe_qsub_command(implementation_commit)
        })),
        "qsub_environment_sha256": qsub_environment_sha256,
        "probe_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_R5_SCHEDULER_ROOT, "probe")
        ),
        "scheduler_submission_count": 1,
        "cpu_slots": 1,
        "gpu_requested": False,
        "probe_is_array": False,
        "automatic_retry_authorized": False,
    }
    if set(value) != R8U_R5_PROBE_SUBMISSION_KEYS:
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    return value


def _wait_for_r8u_r5_control(path: Path, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not os.path.lexists(path):
        if time.monotonic() >= deadline:
            _fail("R8U_R5_CONTROL_RECEIPT_TIMEOUT")
        time.sleep(0.25)


def _read_r8u_r5_probe_submission_minimal(
    *, current_job_id: str, wait: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Validate the probe receipt without measuring live worker executables."""

    if JOB_RE.fullmatch(current_job_id) is None:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    _r8u_r5_require_account_file_owner_matches_effective_uid()
    if wait:
        _wait_for_r8u_r5_control(R8U_R5_PROBE_SUBMISSION_PATH)
    account, _ = _load_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    validate_r8u_r5_scheduler_account_authority(account)
    implementation_commit = str(account.get("implementation_commit", ""))
    submission, _ = _load_private_json(R8U_R5_PROBE_SUBMISSION_PATH)
    job_id = str(submission.get("probe_job_id", ""))
    if JOB_RE.fullmatch(job_id) is None:
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    if job_id != current_job_id:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    common = _r8u_r5_common(
        artifact_type="lvef_c3_r8u_r5_worker_context_probe_submission_v1",
        status="PASS_EXACT_ONE_R8U_R5_CPU_WORKER_CONTEXT_PROBE_QSUB",
        implementation_commit=implementation_commit,
    )
    expected_hashes = {
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "r8u_r4_failure_evidence_sha256": core.sha256_file(
            R8U_R5_R4_FAILURE_EVIDENCE_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "probe_authority_sha256": core.sha256_file(R8U_R5_PROBE_AUTHORITY_PATH),
    }
    if (
        set(submission) != R8U_R5_PROBE_SUBMISSION_KEYS
        or any(submission.get(key) != item for key, item in common.items())
        or any(submission.get(key) != item for key, item in expected_hashes.items())
        or submission.get("probe_job_name")
        != _r8u_r5_probe_job_name(implementation_commit)
        or submission.get("probe_qsub_argv_sha256")
        != _sha256_bytes(_canonical_bytes({
            "argv": _r8u_r5_probe_qsub_command(implementation_commit)
        }))
        or submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or submission.get("probe_qsub_evidence")
        != dict(_qsub_evidence_authority(R8U_R5_SCHEDULER_ROOT, "probe"))
        or submission.get("scheduler_submission_count") != 1
        or submission.get("cpu_slots") != 1
        or submission.get("gpu_requested") is not False
        or submission.get("probe_is_array") is not False
        or submission.get("automatic_retry_authorized") is not False
    ):
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    return account, submission


def _validate_r8u_r5_probe_submission(
    *, current_job_id: str | None = None, wait: bool = False,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        _wait_for_r8u_r5_control(R8U_R5_PROBE_SUBMISSION_PATH)
    implementation_commit = _current_r8u_r5_implementation_commit()
    account, account_payload = _load_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    validate_r8u_r5_scheduler_account_authority(account)
    failure, failure_payload = _load_private_json(R8U_R5_R4_FAILURE_EVIDENCE_PATH)
    _validate_r8u_r5_r4_failure_evidence(failure)
    authority, authority_payload = _load_private_json(R8U_R5_PROBE_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_R5_PROBE_SUBMISSION_PATH)
    expected_authority = _r8u_r5_probe_authority(
        implementation_commit=implementation_commit,
        account_authority_sha256=_sha256_bytes(account_payload),
        r4_failure_evidence_sha256=_sha256_bytes(failure_payload),
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
    )
    job_id = str(submission.get("probe_job_id", ""))
    expected_submission = _r8u_r5_probe_submission_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=job_id,
        account_authority_sha256=_sha256_bytes(account_payload),
        r4_failure_evidence_sha256=_sha256_bytes(failure_payload),
        probe_authority_sha256=_sha256_bytes(authority_payload),
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
    )
    if current_job_id is not None and current_job_id != job_id:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    if (
        not _exact_typed_value_equal(authority, expected_authority)
        or not _exact_typed_value_equal(submission, expected_submission)
    ):
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    return account, failure, authority, submission


def _r8u_r5_probe_receipt(
    *, implementation_commit: str, probe_job_id: str,
    account_authority_sha256: str, probe_authority_sha256: str,
    probe_submission_sha256: str, probe_diagnostic_sha256: str,
    worker_qstat_projection_sha256: str,
    worker_process_projection_sha256: str,
) -> Mapping[str, Any]:
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_worker_context_probe_receipt_v1",
            status="PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": account_authority_sha256,
        "probe_authority_sha256": probe_authority_sha256,
        "probe_submission_receipt_sha256": probe_submission_sha256,
        "probe_diagnostic_sha256": probe_diagnostic_sha256,
        "worker_qstat_projection_sha256": worker_qstat_projection_sha256,
        "worker_process_projection_sha256": worker_process_projection_sha256,
        "probe_job_id": probe_job_id,
        "worker_role": R8U_R5_PROBE_ROLE,
        "effective_uid_match": True,
        "job_id_match": True,
        "task_context_match": True,
        "job_role_match": True,
        "canonical_worker_environment_pass": True,
        "candidate_scans": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "publication_executions": 0,
        "extraction_executions": 0,
        "embedding_generations": 0,
        "preservation_executions": 0,
        "scientific_attempt_mutations": 0,
    }
    if (
        set(value) != R8U_R5_PROBE_RECEIPT_KEYS
        or JOB_RE.fullmatch(probe_job_id) is None
        or any(
            SHA_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "scheduler_account_authority_sha256", "probe_authority_sha256",
                "probe_submission_receipt_sha256", "probe_diagnostic_sha256",
                "worker_qstat_projection_sha256",
                "worker_process_projection_sha256",
            )
        )
    ):
        _fail("R8U_R5_PROBE_RECEIPT_INVALID")
    return value


def submit_r8u_r5_worker_context_probe(
    *, qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Create the fresh R5 account chain and issue one CPU-only qsub."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r5_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    if any(os.path.lexists(path) for path in (
        R8U_R5_ROOT, R8U_R5_PUBLICATION_CLAIM_ROOT,
        R8U_R5_CONTINUATION_ROOT,
    )):
        _fail("R8U_R5_NAMESPACE_COLLISION")
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R5_CONTEXT_PROBE_SUBMITTER",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    _validate_original_controls()
    _r8u_validate_frozen_prefix(run, include_batch16=False)
    _r8u_historical_r8r_chain_authority()
    failure_evidence, portable = _r8u_r5_r4_failure_evidence(
        implementation_commit=implementation_commit, run=run
    )
    validate_r8u_r4_portable_candidate_authority(portable)
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    if os.path.lexists(target):
        _fail("R8U_R5_PUBLICATION_TARGET_COLLISION")
    _create_private_directory_no_clobber(R8U_R5_ROOT)
    _create_private_directory_no_clobber(R8U_R5_SCHEDULER_ROOT)
    account = _r8u_r5_scheduler_account_authority(
        implementation_commit=implementation_commit,
        environment=environment,
        qsub_environment_sha256=environment_sha,
    )
    account_sha = _write_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH, account)
    failure_sha = _write_private_json(
        R8U_R5_R4_FAILURE_EVIDENCE_PATH, failure_evidence
    )
    authority = _r8u_r5_probe_authority(
        implementation_commit=implementation_commit,
        account_authority_sha256=account_sha,
        r4_failure_evidence_sha256=failure_sha,
        qsub_environment_sha256=environment_sha,
    )
    authority_sha = _write_private_json(R8U_R5_PROBE_AUTHORITY_PATH, authority)
    probe_job_id = scheduler._capture_qsub(
        "probe", _r8u_r5_probe_qsub_command(implementation_commit),
        root=R8U_R5_SCHEDULER_ROOT, environment=environment,
        runner=qsub_runner,
    )
    submission = _r8u_r5_probe_submission_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        account_authority_sha256=account_sha,
        r4_failure_evidence_sha256=failure_sha,
        probe_authority_sha256=authority_sha,
        qsub_environment_sha256=environment_sha,
    )
    _write_private_json(R8U_R5_PROBE_SUBMISSION_PATH, submission)
    return {
        "status": "WORKER_CONTEXT_PROBE_SUBMITTED_AWAITING_ACCOUNTING",
        "probe_job_id": probe_job_id,
        "qsub_exit": 0,
        "new_qsub_submissions": 1,
        "gpu_requested": False,
        "array_requested": False,
        "wall_seconds_maximum": 600,
        "candidate_scans": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
    }


def run_r8u_r5_worker_context_probe(
    *, process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Validate only the common compute-worker scheduler boundary."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if JOB_RE.fullmatch(job_id) is None:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    if task_text not in {"", "undefined"}:
        _fail("SCHEDULER_TASK_ID_BINDING_MISMATCH")
    account, submission = _read_r8u_r5_probe_submission_minimal(
        current_job_id=job_id, wait=True
    )
    context = _r8u_r5_build_worker_context(
        account_authority=account,
        expected_job_id=str(submission["probe_job_id"]),
        expected_role=R8U_R5_PROBE_ROLE,
        expected_task_id=None,
    )
    worker_qstat = _r8u_r5_qstat_projection(
        environment=context.environment,
        expected_job_id=job_id,
        expected_job_name=str(submission["probe_job_name"]),
        runner=qstat_runner,
        worker_local=True,
    )
    worker_process = _r8u_r5_process_projection(
        environment=context.environment,
        runner=process_runner,
        worker_self_marker="--run-r8u-r5-worker-context-probe",
    )
    deep_account, _failure, authority, deep_submission = (
        _validate_r8u_r5_probe_submission(current_job_id=job_id)
    )
    if (
        not _exact_typed_value_equal(account, deep_account)
        or not _exact_typed_value_equal(submission, deep_submission)
    ):
        _fail("R8U_R5_PROBE_SUBMISSION_INVALID")
    if any(os.path.lexists(path) for path in (
        R8U_R5_PROBE_DIAGNOSTIC_PATH, R8U_R5_PROBE_RECEIPT_PATH,
    )):
        _fail("R8U_R5_PROBE_OUTPUT_COLLISION")
    diagnostic = _r8u_r5_worker_diagnostic(context)
    diagnostic_sha = _write_private_json(
        R8U_R5_PROBE_DIAGNOSTIC_PATH, diagnostic
    )
    receipt = _r8u_r5_probe_receipt(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        probe_job_id=job_id,
        account_authority_sha256=core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        probe_authority_sha256=core.sha256_file(R8U_R5_PROBE_AUTHORITY_PATH),
        probe_submission_sha256=core.sha256_file(
            R8U_R5_PROBE_SUBMISSION_PATH
        ),
        probe_diagnostic_sha256=diagnostic_sha,
        worker_qstat_projection_sha256=core.canonical_json_sha256(
            worker_qstat
        ),
        worker_process_projection_sha256=core.canonical_json_sha256(
            worker_process
        ),
    )
    _write_private_json(R8U_R5_PROBE_RECEIPT_PATH, receipt)
    if authority.get("scientific_attempt_mutation_authorized") is not False:
        _fail("R8U_R5_PROBE_AUTHORITY_INVALID")
    return {**dict(receipt), "diagnostic": dict(diagnostic)}


def _validate_r8u_r5_probe_receipt() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    account, _failure, _authority, submission = _validate_r8u_r5_probe_submission()
    diagnostic, _ = _load_private_json(R8U_R5_PROBE_DIAGNOSTIC_PATH)
    _validate_r8u_r5_worker_diagnostic(diagnostic)
    receipt, _ = _load_private_json(R8U_R5_PROBE_RECEIPT_PATH)
    if (
        set(receipt) != R8U_R5_PROBE_RECEIPT_KEYS
        or receipt.get("status") != "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT"
        or receipt.get("scheduler_account_authority_sha256")
        != core.sha256_file(R8U_R5_ACCOUNT_AUTHORITY_PATH)
        or receipt.get("probe_authority_sha256")
        != core.sha256_file(R8U_R5_PROBE_AUTHORITY_PATH)
        or receipt.get("probe_submission_receipt_sha256")
        != core.sha256_file(R8U_R5_PROBE_SUBMISSION_PATH)
        or receipt.get("probe_diagnostic_sha256")
        != core.sha256_file(R8U_R5_PROBE_DIAGNOSTIC_PATH)
        or receipt.get("probe_job_id") != submission.get("probe_job_id")
        or receipt.get("worker_role") != R8U_R5_PROBE_ROLE
        or any(receipt.get(field) is not True for field in (
            "effective_uid_match", "job_id_match", "task_context_match",
            "job_role_match", "canonical_worker_environment_pass",
        ))
        or any(receipt.get(field) != 0 for field in (
            "candidate_scans", "cloud_requests", "dicom_body_reads",
            "npz_body_reads", "publication_executions",
            "extraction_executions", "embedding_generations",
            "preservation_executions", "scientific_attempt_mutations",
        ))
        or any(
            SHA_RE.fullmatch(str(receipt.get(field, ""))) is None
            for field in (
                "worker_qstat_projection_sha256",
                "worker_process_projection_sha256",
            )
        )
    ):
        _fail("R8U_R5_PROBE_RECEIPT_INVALID")
    return receipt, diagnostic


def _r8u_r5_probe_scheduler_log(
    *, probe_job_id: str, probe_job_name: str,
) -> tuple[bytes, str]:
    if (
        JOB_RE.fullmatch(probe_job_id) is None
        or probe_job_name != _r8u_r5_probe_job_name(
            _current_r8u_r5_implementation_commit()
        )
    ):
        _fail("R8U_R5_PROBE_SCHEDULER_LOG_INVALID")
    path = R8U_R5_SCHEDULER_ROOT / f"{probe_job_name}.o{probe_job_id}"
    _wait_for_r8u_r5_control(path, timeout_seconds=30.0)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > R8U_SCHEDULER_LOG_MAX_BYTES:
                    _fail("R8U_R5_PROBE_SCHEDULER_LOG_INVALID")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except R8RControllerError:
        raise
    except OSError as exc:
        raise R8RControllerError(
            "R8U_R5_PROBE_SCHEDULER_LOG_INVALID"
        ) from exc
    payload = b"".join(chunks)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or payload.count(
            b"R8U_R5_STATUS=PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT\n"
        ) != 1
        or b"BLOCKED_" in payload
    ):
        _fail("R8U_R5_PROBE_SCHEDULER_LOG_INVALID")
    return payload, _sha256_bytes(payload)


def _r8u_r5_probe_accounting_receipt(
    *, implementation_commit: str, probe_job_id: str,
    accounting: Mapping[str, Any], probe_scheduler_log_sha256: str,
) -> Mapping[str, Any]:
    validated = _validate_recovery_accounting_projection(
        accounting, expected_job_id=probe_job_id
    )
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_worker_context_probe_accounting_v1",
            status="PASS_R8U_R5_PROBE_QACCT_FAILED_0_EXIT_0",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "probe_authority_sha256": core.sha256_file(R8U_R5_PROBE_AUTHORITY_PATH),
        "probe_submission_receipt_sha256": core.sha256_file(
            R8U_R5_PROBE_SUBMISSION_PATH
        ),
        "probe_job_id": probe_job_id,
        "failed": 0,
        "exit_status": 0,
        "accounting_projection": dict(validated),
        "probe_receipt_sha256": core.sha256_file(R8U_R5_PROBE_RECEIPT_PATH),
        "probe_scheduler_log_sha256": probe_scheduler_log_sha256,
    }
    if set(value) != R8U_R5_PROBE_ACCOUNTING_KEYS:
        _fail("R8U_R5_PROBE_ACCOUNTING_INVALID")
    return value


def _wait_r8u_r5_probe_accounting(
    *, probe_job_id: str, environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return _query_recovery_accounting(
                recovery_job_id=probe_job_id,
                environment=environment,
                runner=runner,
            )
        except R8RControllerError as exc:
            if exc.code != "R8R_RECOVERY_ACCOUNTING_UNAVAILABLE":
                raise
            if time.monotonic() >= deadline:
                _fail("R8U_R5_PROBE_ACCOUNTING_TIMEOUT")
            time.sleep(5.0)


def adjudicate_r8u_r5_worker_context_probe(
    *, qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    timeout_seconds: float = 900.0,
) -> Mapping[str, Any]:
    """Perform the one bounded qacct wait and seal the CPU-probe result."""

    environment, _ = scheduler.build_qsub_environment()
    account, _failure, _authority, submission = (
        _validate_r8u_r5_probe_submission()
    )
    if (
        scheduler.qsub_environment_sha256(environment)
        != account.get("qsub_environment_sha256")
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    if os.path.lexists(R8U_R5_PROBE_ACCOUNTING_PATH):
        _fail("R8U_R5_PROBE_ACCOUNTING_COLLISION")
    probe_job_id = str(submission["probe_job_id"])
    accounting = _wait_r8u_r5_probe_accounting(
        probe_job_id=probe_job_id,
        environment=environment,
        runner=qacct_runner,
        timeout_seconds=timeout_seconds,
    )
    _wait_for_r8u_r5_control(R8U_R5_PROBE_RECEIPT_PATH, timeout_seconds=30.0)
    receipt, diagnostic = _validate_r8u_r5_probe_receipt()
    _payload, log_sha = _r8u_r5_probe_scheduler_log(
        probe_job_id=probe_job_id,
        probe_job_name=str(submission["probe_job_name"]),
    )
    value = _r8u_r5_probe_accounting_receipt(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        probe_job_id=probe_job_id,
        accounting=accounting,
        probe_scheduler_log_sha256=log_sha,
    )
    _write_private_json(R8U_R5_PROBE_ACCOUNTING_PATH, value)
    return {
        "status": "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        "probe_job_id": probe_job_id,
        "failed": 0,
        "exit_status": 0,
        "effective_uid_match": receipt["effective_uid_match"],
        "job_id_match": receipt["job_id_match"],
        "job_role_match": receipt["job_role_match"],
        "observed_user_match": diagnostic["observed_user_match"],
        "observed_logname_match": diagnostic["observed_logname_match"],
        "observed_home_match": diagnostic["observed_home_match"],
        "observed_shell_match": diagnostic["observed_shell_match"],
        "user_present": diagnostic["user_present"],
        "logname_present": diagnostic["logname_present"],
        "home_present": diagnostic["home_present"],
        "shell_present": diagnostic["shell_present"],
        "passwd_lookup_status": diagnostic["passwd_lookup_status"],
    }


def _validate_r8u_r5_probe_accounting() -> Mapping[str, Any]:
    _account, _failure, _authority, submission = _validate_r8u_r5_probe_submission()
    _validate_r8u_r5_probe_receipt()
    value, _ = _load_private_json(R8U_R5_PROBE_ACCOUNTING_PATH)
    accounting = value.get("accounting_projection")
    if not isinstance(accounting, Mapping):
        _fail("R8U_R5_PROBE_ACCOUNTING_INVALID")
    expected = _r8u_r5_probe_accounting_receipt(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        probe_job_id=str(submission["probe_job_id"]),
        accounting=accounting,
        probe_scheduler_log_sha256=str(
            value.get("probe_scheduler_log_sha256", "")
        ),
    )
    _payload, current_log_sha = _r8u_r5_probe_scheduler_log(
        probe_job_id=str(submission.get("probe_job_id", "")),
        probe_job_name=str(submission.get("probe_job_name", "")),
    )
    if (
        not _exact_typed_value_equal(value, expected)
        or value.get("status") != "PASS_R8U_R5_PROBE_QACCT_FAILED_0_EXIT_0"
        or SHA_RE.fullmatch(str(value.get("probe_scheduler_log_sha256", "")))
        is None
        or value.get("probe_scheduler_log_sha256") != current_log_sha
    ):
        _fail("R8U_R5_PROBE_ACCOUNTING_INVALID")
    return value


def _r8u_r5_validate_post_probe_namespace(
    submission: Mapping[str, Any],
) -> None:
    """Require the tiny post-probe R5 namespace to be closed and pristine."""

    job_id = str(submission.get("probe_job_id", ""))
    job_name = str(submission.get("probe_job_name", ""))
    allowed_files = {
        R8U_R5_ACCOUNT_AUTHORITY_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_R4_FAILURE_EVIDENCE_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_PROBE_AUTHORITY_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_PROBE_SUBMISSION_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_PROBE_DIAGNOSTIC_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_PROBE_RECEIPT_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        R8U_R5_PROBE_ACCOUNTING_PATH.relative_to(R8U_R5_ROOT).as_posix(),
        "scheduler/probe.qsub.stdout.restricted",
        "scheduler/probe.qsub.stderr.restricted",
        "scheduler/probe.qsub.exit_status.restricted",
        f"scheduler/{job_name}.o{job_id}",
    }
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    try:
        sequential._require_nonsymlink_components(R8U_R5_ROOT)
        for current, directories, files in os.walk(
            R8U_R5_ROOT, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            relative = current_path.relative_to(R8U_R5_ROOT).as_posix()
            observed_directories.add(relative)
            for name in (*directories, *files):
                if (current_path / name).is_symlink():
                    _fail("R8U_R5_NAMESPACE_INVALID")
            observed_files.update(
                (current_path / name).relative_to(R8U_R5_ROOT).as_posix()
                for name in files
            )
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8U_R5_NAMESPACE_INVALID") from exc
    if (
        observed_directories != {".", "scheduler"}
        or observed_files != allowed_files
    ):
        _fail("R8U_R5_NAMESPACE_INVALID")


def _r8u_r5_capacity_receipt(
    *, implementation_commit: str,
    fresh_capacity_observation: Mapping[str, Any],
    portable_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_resume_capacity_v1",
            status=R8U_R5_CAPACITY_STATUS,
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "worker_context_probe_accounting_sha256": core.sha256_file(
            R8U_R5_PROBE_ACCOUNTING_PATH
        ),
        "r8u_r4_capacity_authority_sha256": core.sha256_file(
            R8U_R4_CAPACITY_PATH
        ),
        "fresh_capacity_observation": dict(fresh_capacity_observation),
        "fresh_capacity_observation_sha256": core.canonical_json_sha256(
            fresh_capacity_observation
        ),
        "candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_SHA256,
        "candidate_total_bytes": int(portable_authority["candidate_total_bytes"]),
        "quota_reserve_bytes": int(
            fresh_capacity_observation["required_quota_reserve_bytes"]
        ),
        "physical_reserve_bytes": int(
            fresh_capacity_observation["required_physical_reserve_bytes"]
        ),
        "file_slot_reserve_pass": fresh_capacity_observation[
            "file_slot_gate_passed"
        ],
    }
    if (
        set(value) != R8U_R5_CAPACITY_KEYS
        or value.get("file_slot_reserve_pass") is not True
        or value.get("quota_reserve_bytes") != capacity.R8U_QUOTA_RESERVE_BYTES
        or value.get("physical_reserve_bytes")
        != capacity.R8U_PHYSICAL_RESERVE_BYTES
    ):
        _fail("R8U_R5_RESUME_CAPACITY_INVALID")
    return value


def _validate_r8u_r5_capacity(
    *, run: sequential.FullRun, portable_authority: Mapping[str, Any],
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    capacity_value = value
    if capacity_value is None:
        capacity_value, _ = _load_private_json(R8U_R5_CAPACITY_PATH)
    fresh = capacity_value.get("fresh_capacity_observation")
    if not isinstance(fresh, Mapping):
        _fail("R8U_R5_RESUME_CAPACITY_INVALID")
    try:
        capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
            run.plan,
            fresh,
            completed_extraction_candidate_seal_sha256=(
                R8U_R3_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=int(
                portable_authority["candidate_total_bytes"]
            ),
            r8u_portability_repair_commit=(
                R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R5_RESUME_CAPACITY_INVALID") from exc
    expected = _r8u_r5_capacity_receipt(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        fresh_capacity_observation=fresh,
        portable_authority=portable_authority,
    )
    if (
        not _exact_typed_value_equal(capacity_value, expected)
        or fresh.get("status") != capacity.R8U_R4_CAPACITY_STATUS_PASS
        or any(fresh.get(field) is not True for field in (
            "quota_reserve_gate_passed", "physical_reserve_gate_passed",
            "file_slot_gate_passed",
        ))
    ):
        _fail("R8U_R5_RESUME_CAPACITY_INVALID")
    return capacity_value


def _r8u_r5_resume_authority(
    *, run: sequential.FullRun, implementation_commit: str,
    qsub_environment_sha256: str, prefix_receipts: Sequence[str],
) -> Mapping[str, Any]:
    if (
        len(prefix_receipts) != 15
        or tuple(prefix_receipts)
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
    ):
        _fail("R8U_R5_RESUME_AUTHORITY_INVALID")
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_publication_resume_authority_v1",
            status="AUTHORIZED_FIXED_R8U_R5_BATCH16_PUBLICATION_RESUME",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "r8u_r4_failure_evidence_sha256": core.sha256_file(
            R8U_R5_R4_FAILURE_EVIDENCE_PATH
        ),
        "worker_context_probe_authority_sha256": core.sha256_file(
            R8U_R5_PROBE_AUTHORITY_PATH
        ),
        "worker_context_probe_receipt_sha256": core.sha256_file(
            R8U_R5_PROBE_RECEIPT_PATH
        ),
        "worker_context_probe_accounting_sha256": core.sha256_file(
            R8U_R5_PROBE_ACCOUNTING_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "resume_capacity_sha256": core.sha256_file(R8U_R5_CAPACITY_PATH),
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "worker_role": R8U_R5_RESUME_ROLE,
        "cloud_requests_authorized": 0,
        "downloads_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "dicom_extraction_executions_authorized": 0,
        "echoprime_executions_authorized": 1,
        "gpu_executions_authorized": 1,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
        "maximum_new_gpu_resume_qsubs": 1,
    }
    if set(value) != R8U_R5_RESUME_AUTHORITY_KEYS:
        _fail("R8U_R5_RESUME_AUTHORITY_INVALID")
    return value


def _validate_r8u_r5_qstat_projection(
    value: Mapping[str, Any], *, expected_job_id: str,
    expected_job_name: str, worker_local: bool,
) -> Mapping[str, Any]:
    keys = {
        "status", "resume_job_id", "resume_job_name", "state", "category",
        "target_matches", "competing_matching_jobs", "qstat_snapshot_count",
        "qstat_projection_sha256",
    }
    body = {key: item for key, item in value.items() if key != "qstat_projection_sha256"}
    expected_status = (
        "PASS_EXACT_ONE_R8U_R5_WORKER_JOB_ZERO_COMPETITORS"
        if worker_local
        else "PASS_EXACT_ONE_R8U_R5_SUBMITTED_JOB_ZERO_COMPETITORS"
    )
    if (
        set(value) != keys
        or value.get("status") != expected_status
        or value.get("resume_job_id") != expected_job_id
        or value.get("resume_job_name") != expected_job_name
        or value.get("state") not in {"r", "qw", "t", "Rr"}
        or value.get("category")
        != ("running" if value.get("state") in {"r", "t", "Rr"} else "pending")
        or value.get("target_matches") != 1
        or value.get("competing_matching_jobs") != 0
        or value.get("qstat_snapshot_count") != 1
        or value.get("qstat_projection_sha256")
        != core.canonical_json_sha256(body)
    ):
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")
    return value


def _r8u_r5_resume_submission_receipt(
    *, implementation_commit: str, resume_job_id: str,
    qsub_environment_sha256: str, initial_qstat_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(resume_job_id) is None:
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    _validate_r8u_r5_qstat_projection(
        initial_qstat_projection,
        expected_job_id=resume_job_id,
        expected_job_name=_r8u_r5_resume_job_name(implementation_commit),
        worker_local=False,
    )
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_publication_resume_submission_v1",
            status="PASS_EXACT_ONE_R8U_R5_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "resume_authority_sha256": core.sha256_file(R8U_R5_AUTHORITY_PATH),
        "worker_context_probe_authority_sha256": core.sha256_file(
            R8U_R5_PROBE_AUTHORITY_PATH
        ),
        "worker_context_probe_receipt_sha256": core.sha256_file(
            R8U_R5_PROBE_RECEIPT_PATH
        ),
        "worker_context_probe_accounting_sha256": core.sha256_file(
            R8U_R5_PROBE_ACCOUNTING_PATH
        ),
        "resume_capacity_sha256": core.sha256_file(R8U_R5_CAPACITY_PATH),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "resume_job_name": _r8u_r5_resume_job_name(implementation_commit),
        "resume_job_id": resume_job_id,
        "resume_qsub_argv_sha256": _sha256_bytes(_canonical_bytes({
            "argv": _r8u_r5_resume_qsub_command(implementation_commit)
        })),
        "qsub_environment_sha256": qsub_environment_sha256,
        "resume_qsub_evidence": dict(
            _qsub_evidence_authority(R8U_R5_SCHEDULER_ROOT, "resume")
        ),
        "initial_qstat_projection": dict(initial_qstat_projection),
        "scheduler_submission_count": 1,
        "resume_is_array": False,
        "gpu_requested": True,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "dicom_extraction_executions_by_submitter": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R5_RESUME_SUBMISSION_KEYS:
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    return value


def _r8u_r5_require_gpu_outputs_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    expected_absent = (
        R8U_R5_CAPACITY_PATH, R8U_R5_AUTHORITY_PATH,
        R8U_R5_SUBMISSION_PATH, R8U_R5_GPU_DIAGNOSTIC_PATH,
        R8U_R5_LOCALITY_PATH, R8U_R5_PUBLICATION_CLAIM_ROOT,
        R8U_R5_PRIMITIVE_PROBE_PATH, R8U_R5_PRIMITIVE_PROBE_WORK_ROOT,
        R8U_R5_PUBLICATION_PATH, R8U_R5_EXTRACTION_TRANSITION_ROOT,
        R8U_R5_ACCOUNTING_PATH, R8U_R5_TERMINAL_PATH,
        R8U_R5_CONTINUATION_ROOT,
        paths["extraction"], paths["extraction_ledger"],
        paths["pooling_ledger"], paths["eligibility_ledger"],
        paths["echoprime"],
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        ATTEMPT_ROOT / "cache_retirement_authorizations"
        / f"{R8U_FIXED_BATCH_ID}.authorization.json",
        paths["final_ledger"], paths["final_receipt"],
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8U_R5_RESUME_OUTPUT_COLLISION")


def _validate_r8u_r5_resume_submission(
    *, current_job_id: str | None = None, wait: bool = False,
) -> tuple[
    sequential.FullRun, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]
]:
    if wait:
        _wait_for_r8u_r5_control(R8U_R5_SUBMISSION_PATH)
    implementation_commit = _current_r8u_r5_implementation_commit()
    account = validate_r8u_r5_scheduler_account_authority()
    _validate_r8u_r5_r4_failure_evidence()
    _validate_r8u_r5_probe_accounting()
    portable = validate_r8u_r4_portable_candidate_authority()
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id or "R8U_R5_RESUME_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    capacity_value = _validate_r8u_r5_capacity(
        run=run, portable_authority=portable
    )
    authority, _ = _load_private_json(R8U_R5_AUTHORITY_PATH)
    submission, _ = _load_private_json(R8U_R5_SUBMISSION_PATH)
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    expected_authority = _r8u_r5_resume_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
        prefix_receipts=prefix,
    )
    job_id = str(submission.get("resume_job_id", ""))
    initial_qstat = submission.get("initial_qstat_projection")
    if not isinstance(initial_qstat, Mapping):
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    expected_submission = _r8u_r5_resume_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=job_id,
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
        initial_qstat_projection=initial_qstat,
    )
    if current_job_id is not None and current_job_id != job_id:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    if (
        capacity_value.get("status") != R8U_R5_CAPACITY_STATUS
        or not _exact_typed_value_equal(authority, expected_authority)
        or not _exact_typed_value_equal(submission, expected_submission)
    ):
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    return run, account, authority, submission


def submit_r8u_r5_batch16_publication_resume(
    *, qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    """After CPU PASS, scan once, capture capacity once, and issue one GPU qsub."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r5_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    account, _failure, _probe_authority, probe_submission = (
        _validate_r8u_r5_probe_submission()
    )
    _validate_r8u_r5_probe_accounting()
    if environment_sha != account.get("qsub_environment_sha256"):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    _r8u_r5_validate_post_probe_namespace(probe_submission)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R5_RESUME_SUBMITTER",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    _validate_original_controls()
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=False)
    _r8u_historical_r8r_chain_authority()
    _validate_r8u_r5_r4_failure_evidence()
    _r8u_r5_require_gpu_outputs_absent(run)
    portable = validate_r8u_r4_portable_candidate_authority()
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    if os.path.lexists(target):
        _fail("R8U_R5_PUBLICATION_TARGET_COLLISION")
    # The immutable R4 authority already sealed the exact 10,187-file
    # closure.  R5 deliberately does not repeat that tree scan on the login
    # node; the scheduled GPU worker performs the one live metadata scan only
    # after its worker-context and runtime gates pass.
    try:
        fresh_capacity = (
            capacity.probe_fixed_r8u_r4_batch16_publication_resume_capacity(
                run.plan,
                completed_extraction_candidate_seal_sha256=(
                    R8U_R3_CANDIDATE_SEAL_SHA256
                ),
                completed_extraction_candidate_bytes=int(
                    portable["candidate_total_bytes"]
                ),
                r8u_portability_repair_commit=(
                    R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
                ),
                process_runner=capacity_process_runner,
            )
        )
    except Exception as exc:
        raise R8RControllerError("R8U_R5_RESUME_CAPACITY_INVALID") from exc
    if (
        fresh_capacity.get("status") != capacity.R8U_R4_CAPACITY_STATUS_PASS
        or any(fresh_capacity.get(field) is not True for field in (
            "quota_reserve_gate_passed", "physical_reserve_gate_passed",
            "file_slot_gate_passed",
        ))
    ):
        raise R8RControllerError(
            "R8U_R5_RESUME_CAPACITY_BLOCKED",
            capacity_deficits=_r8u_capacity_deficits(fresh_capacity),
        )
    capacity_value = _r8u_r5_capacity_receipt(
        implementation_commit=implementation_commit,
        fresh_capacity_observation=fresh_capacity,
        portable_authority=portable,
    )
    _write_private_json(R8U_R5_CAPACITY_PATH, capacity_value)
    authority = _r8u_r5_resume_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix,
    )
    _write_private_json(R8U_R5_AUTHORITY_PATH, authority)
    resume_job_id = scheduler._capture_qsub(
        "resume", _r8u_r5_resume_qsub_command(implementation_commit),
        root=R8U_R5_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    initial_qstat = _r8u_r5_qstat_projection(
        environment=environment,
        expected_job_id=resume_job_id,
        expected_job_name=_r8u_r5_resume_job_name(implementation_commit),
        runner=qstat_runner,
        worker_local=False,
    )
    submission = _r8u_r5_resume_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id,
        qsub_environment_sha256=environment_sha,
        initial_qstat_projection=initial_qstat,
    )
    _write_private_json(R8U_R5_SUBMISSION_PATH, submission)
    return {
        "status": "BATCH16_R8U_R5_RESUME_SUBMITTED_AWAITING_TERMINAL",
        "resume_job_id": resume_job_id,
        "qsub_exit": 0,
        "initial_state": initial_qstat["state"],
        "capacity_status": R8U_R5_CAPACITY_STATUS,
        "portable_candidate_status": "PASS",
        "observed_npz_files": int(portable["candidate_npz_files"]),
        "missing_npz_files": 0,
        "additional_npz_files": 0,
        "new_qsub_submissions": 1,
        "total_new_qsub_submissions": 2,
        "continuation_submitted": False,
        "finalizer_submitted": False,
        "login_node_polling_started": False,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
    }


def _r8u_r5_live_publication_locality(
    *, source: Path, target: Path, implementation_commit: str,
    worker_context_diagnostic_sha256: str,
    worker_qstat_projection: Mapping[str, Any],
    worker_process_projection: Mapping[str, Any],
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
) -> _R8UR4LivePublicationLocality:
    """Capture worker-local identities before the R5 no-clobber claim."""

    placeholder = {
        "status": "AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION"
    }
    captured = _r8u_r4_live_publication_locality(
        source=source,
        target=target,
        publication_claim=placeholder,
        identity_reader=identity_reader,
        mount_reader=mount_reader,
        competing_active_jobs=int(
            worker_qstat_projection.get("competing_matching_jobs", -1)
        ),
        competing_active_processes=int(
            worker_process_projection.get("matching_processes", -1)
        ),
    )
    inherited = {
        key: captured.value[key]
        for key in (
            "source_exists_safe_directory", "target_absent",
            "source_target_same_mounted_filesystem", "parents_nonsymlinked",
            "owner_mode_valid", "source_identity_stable_same_call",
            "source_parent_identity_stable_same_call",
            "target_parent_identity_stable_same_call", "source_identity_sha256",
            "source_parent_identity_sha256", "target_parent_identity_sha256",
            "source_mount_identity_sha256", "target_mount_identity_sha256",
            "competing_active_jobs", "competing_active_processes",
        )
    }
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_live_publication_locality_v1",
            status="PASS_R8U_R5_WORKER_LOCAL_PUBLICATION_LOCALITY",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "worker_context_diagnostic_sha256": worker_context_diagnostic_sha256,
        "worker_qstat_projection_sha256": core.canonical_json_sha256(
            worker_qstat_projection
        ),
        "worker_process_projection_sha256": core.canonical_json_sha256(
            worker_process_projection
        ),
        **inherited,
    }
    if set(value) != R8U_R5_LOCALITY_KEYS:
        _fail("R8U_R5_LIVE_PUBLICATION_LOCALITY_INVALID")
    return _R8UR4LivePublicationLocality(
        value=value,
        source_identity=captured.source_identity,
        source_parent_identity=captured.source_parent_identity,
        target_parent_identity=captured.target_parent_identity,
        source_mount_key=captured.source_mount_key,
        target_mount_key=captured.target_mount_key,
    )


def validate_r8u_r5_live_publication_locality(
    value: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    locality = value
    if locality is None:
        locality, _ = _load_private_json(R8U_R5_LOCALITY_PATH)
    if (
        set(locality) != R8U_R5_LOCALITY_KEYS
        or locality.get("artifact_type")
        != "lvef_c3_r8u_r5_live_publication_locality_v1"
        or locality.get("status")
        != "PASS_R8U_R5_WORKER_LOCAL_PUBLICATION_LOCALITY"
        or locality.get("implementation_commit")
        != _current_r8u_r5_implementation_commit()
        or locality.get("scheduler_account_authority_sha256")
        != core.sha256_file(R8U_R5_ACCOUNT_AUTHORITY_PATH)
        or locality.get("worker_context_diagnostic_sha256")
        != core.sha256_file(R8U_R5_GPU_DIAGNOSTIC_PATH)
        or any(locality.get(field) is not True for field in (
            "source_exists_safe_directory", "target_absent",
            "source_target_same_mounted_filesystem", "parents_nonsymlinked",
            "owner_mode_valid", "source_identity_stable_same_call",
            "source_parent_identity_stable_same_call",
            "target_parent_identity_stable_same_call",
        ))
        or locality.get("competing_active_jobs") != 0
        or locality.get("competing_active_processes") != 0
        or any(
            SHA_RE.fullmatch(str(locality.get(field, ""))) is None
            for field in (
                "worker_qstat_projection_sha256",
                "worker_process_projection_sha256", "source_identity_sha256",
                "source_parent_identity_sha256", "target_parent_identity_sha256",
                "source_mount_identity_sha256", "target_mount_identity_sha256",
            )
        )
    ):
        _fail("R8U_R5_LIVE_PUBLICATION_LOCALITY_INVALID")
    return locality


def _r8u_r5_publication_claim(
    *, implementation_commit: str, resume_job_id: str,
    live_locality: Mapping[str, Any],
    worker_context_diagnostic_sha256: str,
    worker_qstat_projection: Mapping[str, Any],
    worker_process_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    validate_r8u_r5_live_publication_locality(live_locality)
    if (
        JOB_RE.fullmatch(resume_job_id) is None
        or live_locality.get("competing_active_jobs") != 0
        or live_locality.get("competing_active_processes") != 0
    ):
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_publication_claim_v1",
            status="AUTHORIZED_EXCLUSIVE_R8U_R5_BATCH16_PUBLICATION",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "resume_job_id": resume_job_id,
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "r8u_r4_failure_evidence_sha256": core.sha256_file(
            R8U_R5_R4_FAILURE_EVIDENCE_PATH
        ),
        "resume_authority_sha256": core.sha256_file(R8U_R5_AUTHORITY_PATH),
        "resume_submission_receipt_sha256": core.sha256_file(
            R8U_R5_SUBMISSION_PATH
        ),
        "live_publication_locality_sha256": core.sha256_file(
            R8U_R5_LOCALITY_PATH
        ),
        "worker_context_diagnostic_sha256": worker_context_diagnostic_sha256,
        "worker_process_projection_sha256": core.canonical_json_sha256(
            worker_process_projection
        ),
        "worker_qstat_projection_sha256": core.canonical_json_sha256(
            worker_qstat_projection
        ),
        "target_role": "extracted_cache/c3_batch_015/dicom_extraction",
        "target_absent": True,
        "competing_active_jobs": 0,
        "competing_active_processes": 0,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "npz_body_reads": 0,
    }
    if set(value) != R8U_R5_PUBLICATION_CLAIM_KEYS:
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    return value


def _r8u_r5_create_publication_claim(value: Mapping[str, Any]) -> str:
    if set(value) != R8U_R5_PUBLICATION_CLAIM_KEYS:
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        _create_private_directory_no_clobber(R8U_R5_PUBLICATION_CLAIM_ROOT)
        return _write_private_json(R8U_R5_PUBLICATION_CLAIM_PATH, value)
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID"
        ) from exc


def _r8u_r5_primitive_probe(
    *, implementation_commit: str,
    locality: _R8UR4LivePublicationLocality,
    publication_claim: Mapping[str, Any],
    invoker: Callable[[Path, Path], Any] | None = None,
) -> Mapping[str, Any]:
    if (
        os.path.lexists(R8U_R5_PRIMITIVE_PROBE_PATH)
        or os.path.lexists(R8U_R5_PRIMITIVE_PROBE_WORK_ROOT)
    ):
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    try:
        probe_mount_key = _r8u_r3_mount_authority(
            R8U_R5_PRIMITIVE_PROBE_WORK_ROOT.parent
        )[0]
    except Exception as exc:
        raise R8RControllerError("R8U_LIVE_PUBLICATION_CROSS_MOUNT") from exc
    if probe_mount_key != locality.target_mount_key:
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
    probe_source = R8U_R5_PRIMITIVE_PROBE_WORK_ROOT / "source"
    probe_target = R8U_R5_PRIMITIVE_PROBE_WORK_ROOT / "target"
    created: list[Path] = []
    removed = 0
    result = _R8UR3RenameResult(False, errno.EIO, "EIO")
    source_present = False
    target_present = False
    target_exact = False
    try:
        for directory in (R8U_R5_PRIMITIVE_PROBE_WORK_ROOT, probe_source):
            _create_private_directory_no_clobber(directory)
            created.append(directory)
        result = _r8u_r3_raw_rename_noreplace(
            probe_source, probe_target, invoker=invoker
        )
        source_present = os.path.lexists(probe_source)
        target_present = os.path.lexists(probe_target)
        target_exact = (
            target_present
            and not probe_target.is_symlink()
            and probe_target.is_dir()
            and next(os.scandir(probe_target), None) is None
        )
    finally:
        for directory in (
            probe_target, probe_source, R8U_R5_PRIMITIVE_PROBE_WORK_ROOT
        ):
            try:
                directory.rmdir()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
    classification = _r8u_r3_primary_classification(result)
    supported = result.returned_success
    if supported and (source_present or not target_exact):
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if not supported and not (source_present and not target_present):
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if classification == "RENAME_CROSS_MOUNT_EXDEV":
        _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
    if classification not in R8U_R3_PROCEEDABLE_PROBE_RESULTS:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    cleanup = (
        not os.path.lexists(R8U_R5_PRIMITIVE_PROBE_WORK_ROOT)
        and removed == len(created)
    )
    if not cleanup:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_publication_primitive_probe_v1",
            status="PASS_R8U_R5_PUBLICATION_PRIMITIVE_PROBE",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "live_publication_locality_sha256": core.sha256_file(
            R8U_R5_LOCALITY_PATH
        ),
        "publication_claim_sha256": core.canonical_json_sha256(
            publication_claim
        ),
        "primary_primitive": "RENAMEAT2_RENAME_NOREPLACE",
        "primary_result": classification,
        "primary_errno": result.errno_name,
        "primary_errno_number": result.errno_number,
        "primary_returned_success": supported,
        "probe_source_present_after": source_present,
        "probe_target_present_after": target_present,
        "probe_target_exact_after": target_exact,
        "probe_cleanup_passed": cleanup,
        "probe_directories_created": len(created),
        "probe_directories_removed": removed,
        "scientific_file_body_reads": 0,
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
    }
    if set(value) != R8U_R5_PRIMITIVE_PROBE_KEYS:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    _write_private_json(R8U_R5_PRIMITIVE_PROBE_PATH, value)
    return value


def _r8u_r5_publish_candidate(
    *, source: Path, target: Path, implementation_commit: str,
    portable_authority: Mapping[str, Any],
    portable_projection: _R8UR4PortableProjection,
    live_locality: _R8UR4LivePublicationLocality,
    publication_claim: Mapping[str, Any], probe: Mapping[str, Any],
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
) -> Mapping[str, Any]:
    source = Path(source)
    target = Path(target)
    validate_r8u_r4_portable_candidate_authority(
        portable_authority, observed_projection=portable_projection
    )
    validate_r8u_r5_live_publication_locality(live_locality)
    if (
        publication_claim.get("status")
        != "AUTHORIZED_EXCLUSIVE_R8U_R5_BATCH16_PUBLICATION"
        or core.canonical_json_sha256(publication_claim)
        != core.sha256_file(R8U_R5_PUBLICATION_CLAIM_PATH)
        or publication_claim.get("live_publication_locality_sha256")
        != core.sha256_file(R8U_R5_LOCALITY_PATH)
        or probe.get("publication_claim_sha256")
        != core.sha256_file(R8U_R5_PUBLICATION_CLAIM_PATH)
    ):
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    _r8u_r4_revalidate_live_locality(
        live_locality,
        source=source,
        target=target,
        identity_reader=identity_reader,
        mount_reader=mount_reader,
    )
    if portable_projection.root_local_identity != live_locality.source_identity:
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    fallback_used = probe.get("primary_result") != "RENAME_NOREPLACE_SUPPORTED"
    allowed_fallback = {
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }
    if fallback_used and probe.get("primary_result") not in allowed_fallback:
        _fail("R8U_R5_LIVE_PUBLICATION_CLAIM_INVALID")
    if fallback_used:
        try:
            returned = fallback_invoker(source, target)
            if returned not in {None, 0}:
                _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
            result = _R8UR3RenameResult(True, 0, "NONE")
        except OSError as exc:
            number = int(exc.errno or errno.EIO)
            result = _R8UR3RenameResult(
                False, number, errno.errorcode.get(number, "UNKNOWN")
            )
        primitive = "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
    else:
        result = _r8u_r3_raw_rename_noreplace(
            source, target, invoker=primary_invoker
        )
        primitive = "RENAMEAT2_RENAME_NOREPLACE"
    source_present = os.path.lexists(source)
    target_present = os.path.lexists(target)
    if source_present and target_present:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    if source_present and not target_present:
        if result.errno_number == errno.EXDEV:
            _fail("R8U_LIVE_PUBLICATION_CROSS_MOUNT")
        _fail("R8U_LIVE_PUBLICATION_SOURCE_INVALID")
    if not target_present:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    read_identity = identity_reader or _r8u_r4_local_identity
    try:
        target_identity = dict(read_identity(target))
    except Exception as exc:
        raise R8RControllerError(
            "R8U_LIVE_PUBLICATION_TARGET_INVALID"
        ) from exc
    continuity_fields = ("device", "inode", "type", "mode", "uid", "gid")
    if (
        target_identity.get("type") != "directory"
        or any(
            target_identity.get(field)
            != live_locality.source_identity.get(field)
            for field in continuity_fields
        )
    ):
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    ruling = (
        "PUBLICATION_PASS"
        if result.returned_success
        else "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
    )
    receipt = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_publication_v1",
            status="PASS_R8U_R5_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        "r8u_r4_failure_evidence_sha256": core.sha256_file(
            R8U_R5_R4_FAILURE_EVIDENCE_PATH
        ),
        "live_publication_locality_sha256": core.sha256_file(
            R8U_R5_LOCALITY_PATH
        ),
        "publication_primitive_probe_sha256": core.sha256_file(
            R8U_R5_PRIMITIVE_PROBE_PATH
        ),
        "publication_claim_sha256": core.sha256_file(
            R8U_R5_PUBLICATION_CLAIM_PATH
        ),
        "primitive_attempted": primitive,
        "primary_result": probe["primary_result"],
        "fallback_used": fallback_used,
        "rename_returned_success": result.returned_success,
        "real_rename_errno": result.errno_name,
        "real_rename_errno_number": result.errno_number,
        "real_rename_errno_classification": (
            _r8u_r3_real_rename_classification(result)
        ),
        "publication_ruling": ruling,
        "prepublication_candidate_sha256": portable_authority[
            "candidate_relative_file_portable_projection_sha256"
        ],
        "postpublication_root_identity_sha256": core.canonical_json_sha256(
            target_identity
        ),
        "source_absent": True,
        "target_exact": True,
        "candidate_npz_files": portable_authority["candidate_npz_files"],
        "candidate_total_bytes": portable_authority["candidate_total_bytes"],
        "files_moved": portable_authority["candidate_npz_files"],
        "files_copied": 0,
        "files_deleted_independently": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "npz_body_reads": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    if set(receipt) != R8U_R5_PUBLICATION_KEYS:
        _fail("R8U_LIVE_PUBLICATION_TARGET_INVALID")
    _write_private_json(R8U_R5_PUBLICATION_PATH, receipt)
    return receipt


def _read_r8u_r5_resume_submission_minimal(
    *, current_job_id: str, wait: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Gate 2: validate the exact job receipt without deep run/candidate work."""

    if JOB_RE.fullmatch(current_job_id) is None:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    _r8u_r5_require_account_file_owner_matches_effective_uid()
    if wait:
        _wait_for_r8u_r5_control(R8U_R5_SUBMISSION_PATH)
    account, _ = _load_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    validate_r8u_r5_scheduler_account_authority(account)
    implementation_commit = str(account.get("implementation_commit", ""))
    submission, _ = _load_private_json(R8U_R5_SUBMISSION_PATH)
    initial_qstat = submission.get("initial_qstat_projection")
    if not isinstance(initial_qstat, Mapping):
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    job_id = str(submission.get("resume_job_id", ""))
    if JOB_RE.fullmatch(job_id) is None:
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    if job_id != current_job_id:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    _validate_r8u_r5_qstat_projection(
        initial_qstat,
        expected_job_id=job_id,
        expected_job_name=_r8u_r5_resume_job_name(implementation_commit),
        worker_local=False,
    )
    common = _r8u_r5_common(
        artifact_type="lvef_c3_r8u_r5_batch16_publication_resume_submission_v1",
        status="PASS_EXACT_ONE_R8U_R5_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
        implementation_commit=implementation_commit,
    )
    expected_hashes = {
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "resume_authority_sha256": core.sha256_file(R8U_R5_AUTHORITY_PATH),
        "worker_context_probe_authority_sha256": core.sha256_file(
            R8U_R5_PROBE_AUTHORITY_PATH
        ),
        "worker_context_probe_receipt_sha256": core.sha256_file(
            R8U_R5_PROBE_RECEIPT_PATH
        ),
        "worker_context_probe_accounting_sha256": core.sha256_file(
            R8U_R5_PROBE_ACCOUNTING_PATH
        ),
        "resume_capacity_sha256": core.sha256_file(R8U_R5_CAPACITY_PATH),
        "portable_candidate_authority_sha256": core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
    }
    if (
        set(submission) != R8U_R5_RESUME_SUBMISSION_KEYS
        or any(submission.get(key) != item for key, item in common.items())
        or any(submission.get(key) != item for key, item in expected_hashes.items())
        or submission.get("resume_job_name")
        != _r8u_r5_resume_job_name(implementation_commit)
        or submission.get("resume_qsub_argv_sha256")
        != _sha256_bytes(_canonical_bytes({
            "argv": _r8u_r5_resume_qsub_command(implementation_commit)
        }))
        or submission.get("resume_qsub_evidence")
        != dict(_qsub_evidence_authority(R8U_R5_SCHEDULER_ROOT, "resume"))
        or submission.get("scheduler_submission_count") != 1
        or submission.get("resume_is_array") is not False
        or submission.get("gpu_requested") is not True
        or submission.get("automatic_retry_authorized") is not False
        or any(submission.get(field) != 0 for field in (
            "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        ))
    ):
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    return account, submission


def _r8u_r5_complete_batch16_after_publication(
    *, run: sequential.FullRun, dependency: sequential.FullDependencies,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Run the unchanged post-publication Batch-16 completion sequence."""

    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    planned = run.plan["batches"][R8U_FIXED_RECOVERY_TASK_ID - 1]
    object_keys = {str(row["source_object_key"]) for row in planned["objects"]}
    try:
        stages.advance_stage_ledger(
            input_ledger=paths["download_ledger"],
            output_ledger=paths["extraction_ledger"],
            receipt_root=R8U_R5_EXTRACTION_TRANSITION_ROOT,
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", core.sha256_file(
                    paths["extraction"] / "dicom_audit.restricted.csv"
                )),
                ("EXTRACTION_COMPLETE", core.sha256_file(
                    paths["extraction"] / "extraction_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
        embedding_summary = dependency.echoprime(
            extraction_manifest=(
                paths["extraction"] / "extraction_manifest.restricted.csv"
            ),
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
            checkpoint=run.authority.checkpoint,
            environment_receipt=run.authority.environment_receipt,
            orchestration_contract=run.contract_path,
            batch_plan=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID,
            batch_output_root=paths["batch_root"],
            batch_size=dependency.echoprime_batch_size,
            seed=20260803,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            requirements=run.requirements,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        stages.advance_stage_ledger(
            input_ledger=paths["extraction_ledger"],
            output_ledger=paths["pooling_ledger"],
            receipt_root=paths["echoprime"] / "transition_receipts",
            batch_id=R8U_FIXED_BATCH_ID,
            transitions=(
                ("EMBEDDING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "clip_manifest.restricted.csv"
                )),
                ("STUDY_POOLING_COMPLETE", core.sha256_file(
                    paths["echoprime"] / "study_manifest.restricted.csv"
                )),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=object_keys,
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R5_BATCH16_ECHOPRIME_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R5_BATCH16_ECHOPRIME_FAILED"
            ),
            stage="ECHOPRIME_EMBEDDING",
        ) from exc
    try:
        preserved = dependency.preserve(
            contract_path=run.contract_path,
            plan_path=run.plan_path,
            batch_id=R8U_FIXED_BATCH_ID,
            attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root,
            output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=run.scheduler_job_identity,
            input_ledger=paths["pooling_ledger"],
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            artifact_validation_context=getattr(
                preservation,
                "R8U_R5_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                getattr(
                    preservation,
                    "R8U_R4_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                    preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY,
                ),
            ),
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=R8U_FIXED_BATCH_ID, paths=paths
        )
        dependency.retire(
            run=run,
            batch_id=R8U_FIXED_BATCH_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            test_only_synthetic_full_scope=False,
            artifact_validation_context=getattr(
                retirement,
                "R8U_R5_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                getattr(
                    retirement,
                    "R8U_R4_FIXED_BATCH16_NO_SCIENTIFIC_BODY",
                    retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY,
                ),
            ),
        )
        final_receipt = dependency.finalize_batch(
            run=run, batch_id=R8U_FIXED_BATCH_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R5_BATCH16_FINALIZATION_FAILED")
        raise R8RControllerError(
            str(code) if SAFE_CODE_RE.fullmatch(str(code)) else (
                "R8U_R5_BATCH16_FINALIZATION_FAILED"
            ),
            stage="PRESERVATION_RETIREMENT_FINALIZATION",
        ) from exc
    if (
        preserved.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or final_receipt.get("status") != "PASS_BATCH_FINALIZED"
        or final_receipt.get("raw_dicoms_retained") is not True
        or final_receipt.get("extracted_cache_retired") is not True
        or final_receipt.get("n_successfully_extracted_cines")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_clip_embeddings")
        != R8U_R3_CANDIDATE_NPZ_FILES
        or final_receipt.get("n_object_technical_dispositions") != 0
        or final_receipt.get("n_blocking_failures") != 0
        or final_receipt.get("n_new_no_cine_studies") != 0
        or final_receipt.get("object_substitution_count") != 0
        or final_receipt.get("unaccounted_multiframe_objects") != 0
        or os.path.lexists(paths["extraction"] / "clips")
    ):
        _fail("R8U_R5_BATCH16_FINALIZATION_INVALID")
    return embedding_summary, preserved, final_receipt


def _r8u_r5_terminal_receipt(
    *, run: sequential.FullRun, final_receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    paths = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)
    required = {
        "scheduler_account_authority_sha256": R8U_R5_ACCOUNT_AUTHORITY_PATH,
        "r8u_r4_failure_evidence_sha256": R8U_R5_R4_FAILURE_EVIDENCE_PATH,
        "worker_context_probe_receipt_sha256": R8U_R5_PROBE_RECEIPT_PATH,
        "worker_context_probe_accounting_sha256": R8U_R5_PROBE_ACCOUNTING_PATH,
        "worker_context_diagnostic_sha256": R8U_R5_GPU_DIAGNOSTIC_PATH,
        "live_publication_locality_sha256": R8U_R5_LOCALITY_PATH,
        "publication_primitive_probe_sha256": R8U_R5_PRIMITIVE_PROBE_PATH,
        "publication_claim_sha256": R8U_R5_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R5_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R5_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R5_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R5_SUBMISSION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"] / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            ATTEMPT_ROOT / "cache_retirement_authorizations"
            / f"{R8U_FIXED_BATCH_ID}.authorization.json"
        ),
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    try:
        hashes = {key: core.sha256_file(path) for key, path in required.items()}
    except Exception as exc:
        raise R8RControllerError("R8U_R5_TERMINAL_AUTHORITY_INVALID") from exc
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_publication_resume_terminal_v1",
            status=R8U_R5_TERMINAL_STATUS,
            implementation_commit=_current_r8u_r5_implementation_commit(),
        ),
        **hashes,
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "object_substitution_count": int(
            final_receipt["object_substitution_count"]
        ),
        "unaccounted_multiframe_objects": int(
            final_receipt["unaccounted_multiframe_objects"]
        ),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "canonical_extraction_cache_retired": final_receipt[
            "extracted_cache_retired"
        ],
        "failed_partial_cache_retained": True,
        "source_candidate_npz_files": R8U_R3_CANDIDATE_NPZ_FILES,
        "cloud_requests": 0,
        "downloads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "echoprime_executions": 1,
        "embedding_generations": 1,
        "gpu_executions": 1,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R5_TERMINAL_KEYS:
        _fail("R8U_R5_TERMINAL_AUTHORITY_INVALID")
    return value


def run_r8u_r5_batch16_publication_resume(
    *, dependencies: sequential.FullDependencies | None = None,
    probe_invoker: Callable[[Path, Path], Any] | None = None,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """R5 GPU worker with scheduler gates strictly before candidate scanning."""

    # 1. Kernel-visible numeric non-array dispatch.
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if JOB_RE.fullmatch(job_id) is None:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    if task_text not in {"", "undefined"}:
        _fail("SCHEDULER_TASK_ID_BINDING_MISMATCH")
    # 2. Exact role-bound post-qsub receipt, without loading scientific run data.
    account, submission = _read_r8u_r5_resume_submission_minimal(
        current_job_id=job_id, wait=True
    )
    # 3-4. Effective UID, sealed account, job/task/role, executable, commit,
    # and qsub-environment bindings; never reconstruct the submitter environment.
    context = _r8u_r5_build_worker_context(
        account_authority=account,
        expected_job_id=job_id,
        expected_role=R8U_R5_RESUME_ROLE,
        expected_task_id=None,
    )
    # 5. Worker-local qstat role projection.
    worker_qstat = _r8u_r5_qstat_projection(
        environment=context.environment,
        expected_job_id=job_id,
        expected_job_name=str(submission["resume_job_name"]),
        runner=qstat_runner,
        worker_local=True,
    )
    _validate_r8u_r5_qstat_projection(
        worker_qstat,
        expected_job_id=job_id,
        expected_job_name=str(submission["resume_job_name"]),
        worker_local=True,
    )
    # 6. Numeric-UID worker process projection.
    worker_process = _r8u_r5_process_projection(
        environment=context.environment,
        runner=process_runner,
        worker_self_marker="--run-r8u-r5-batch16-publication-resume",
    )
    if os.path.lexists(R8U_R5_GPU_DIAGNOSTIC_PATH):
        _fail("R8U_R5_RESUME_OUTPUT_COLLISION")
    diagnostic = _r8u_r5_worker_diagnostic(context)
    diagnostic_sha = _write_private_json(R8U_R5_GPU_DIAGNOSTIC_PATH, diagnostic)
    # 7. Sealed runtime/environment and checkpoint.
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    dependency = sequential.resolve_dependencies(dependencies)
    try:
        dependency.environment_validator(
            run.authority.environment_receipt,
            expected_environment_receipt_sha256=(
                run.runtime_authority["environment_receipt_sha256"]
            ),
            scientific_governing_commit=run.authority.governing_commit,
            runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        )
        if core.sha256_file(run.authority.checkpoint) != run.runtime_authority[
            "checkpoint_sha256"
        ]:
            _fail("R8U_R5_CHECKPOINT_AUTHORITY_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError(
            "R8U_R5_PREBODY_AUTHORITY_FAILED", stage="PREBODY_AUTHORITY"
        ) from exc
    # 8. Deep control chain and portable authority, still without a tree scan.
    deep_run, _account, _authority, _submission = (
        _validate_r8u_r5_resume_submission(current_job_id=job_id)
    )
    if deep_run.attempt_id != run.attempt_id:
        _fail("R8U_R5_RESUME_SUBMISSION_INVALID")
    portable_authority = validate_r8u_r4_portable_candidate_authority()
    source = R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction"
    target = sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
    # 9. The sole R5 live candidate metadata/control scan.
    portable_projection = _r8u_r4_portable_candidate_projection(run)
    validate_r8u_r4_portable_candidate_authority(
        portable_authority, observed_projection=portable_projection
    )
    # 10. Fresh worker-local locality before any claim.
    locality = _r8u_r5_live_publication_locality(
        source=source,
        target=target,
        implementation_commit=_current_r8u_r5_implementation_commit(),
        worker_context_diagnostic_sha256=diagnostic_sha,
        worker_qstat_projection=worker_qstat,
        worker_process_projection=worker_process,
    )
    _write_private_json(R8U_R5_LOCALITY_PATH, locality.value)
    # 11. Fresh no-clobber claim and primitive probe, then one publication.
    claim = _r8u_r5_publication_claim(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        resume_job_id=job_id,
        live_locality=locality.value,
        worker_context_diagnostic_sha256=diagnostic_sha,
        worker_qstat_projection=worker_qstat,
        worker_process_projection=worker_process,
    )
    _r8u_r5_create_publication_claim(claim)
    probe = _r8u_r5_primitive_probe(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        locality=locality,
        publication_claim=claim,
        invoker=probe_invoker,
    )
    publication = _r8u_r5_publish_candidate(
        source=source,
        target=target,
        implementation_commit=_current_r8u_r5_implementation_commit(),
        portable_authority=portable_authority,
        portable_projection=portable_projection,
        live_locality=locality,
        publication_claim=claim,
        probe=probe,
        primary_invoker=primary_invoker,
        fallback_invoker=fallback_invoker,
    )
    embedding_summary, _preserved, final_receipt = (
        _r8u_r5_complete_batch16_after_publication(
            run=run, dependency=dependency
        )
    )
    terminal = _r8u_r5_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    _write_private_json(R8U_R5_TERMINAL_PATH, terminal)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return {
        **dict(terminal),
        "publication_ruling": publication["publication_ruling"],
        "embedding_summary_status": embedding_summary.get("status"),
    }


def _r8u_r5_resume_accounting_receipt(
    *, implementation_commit: str, resume_job_id: str,
    accounting: Mapping[str, Any],
) -> Mapping[str, Any]:
    validated = _validate_recovery_accounting_projection(
        accounting, expected_job_id=resume_job_id
    )
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_batch16_publication_resume_accounting_v1",
            status="PASS_R8U_R5_RESUME_QACCT_FAILED_0_EXIT_0",
            implementation_commit=implementation_commit,
        ),
        "original_task_id": R8U_FIXED_RECOVERY_TASK_ID,
        "resume_job_id": resume_job_id,
        "failed": 0,
        "exit_status": 0,
        "accounting_projection": dict(validated),
    }
    if set(value) != R8U_R5_RESUME_ACCOUNTING_KEYS:
        _fail("R8U_R5_RESUME_ACCOUNTING_INVALID")
    return value


def _validate_r8u_r5_resume_accounting() -> Mapping[str, Any]:
    _run, _account, _authority, submission = _validate_r8u_r5_resume_submission()
    value, _ = _load_private_json(R8U_R5_ACCOUNTING_PATH)
    accounting = value.get("accounting_projection")
    if not isinstance(accounting, Mapping):
        _fail("R8U_R5_RESUME_ACCOUNTING_INVALID")
    expected = _r8u_r5_resume_accounting_receipt(
        implementation_commit=_current_r8u_r5_implementation_commit(),
        resume_job_id=str(submission.get("resume_job_id", "")),
        accounting=accounting,
    )
    if not _exact_typed_value_equal(value, expected):
        _fail("R8U_R5_RESUME_ACCOUNTING_INVALID")
    return value


def validate_r8u_r5_resume_terminal() -> Mapping[str, Any]:
    run, _account, _authority, _submission = _validate_r8u_r5_resume_submission()
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=R8U_FIXED_BATCH_ID
    )
    value, _ = _load_private_json(R8U_R5_TERMINAL_PATH)
    expected = _r8u_r5_terminal_receipt(run=run, final_receipt=final_receipt)
    if (
        not _exact_typed_value_equal(value, expected)
        or value.get("status") != R8U_R5_TERMINAL_STATUS
        or os.path.lexists(R8U_FRESH_EXTRACTION_BATCH_ROOT / "dicom_extraction")
        or os.path.lexists(
            sequential._batch_paths(run, R8U_FIXED_BATCH_ID)["extraction"]
            / "clips"
        )
    ):
        _fail("R8U_R5_TERMINAL_RECEIPT_INVALID")
    _r8u_r4_validate_immutable_r3_failure(run)
    _r8u_validate_frozen_prefix(run, include_batch16=True)
    return value


def validate_r8u_r5_frozen_partial_evidence() -> Mapping[str, Any]:
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R5_FROZEN_PARTIAL_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    _candidate, history = _r8u_r4_validate_immutable_r3_failure(run)
    value, _ = _load_private_json(R8U_FAILED_PARTIAL_SEAL_PATH)
    if (
        value.get("status") != "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE"
        or core.sha256_file(R8U_FAILED_PARTIAL_SEAL_PATH)
        != history["failed_partial_seal_sha256"]
    ):
        _fail("R8U_R5_FAILED_PARTIAL_SEAL_INVALID")
    return value


def _r8u_r5_continuation_links() -> Mapping[str, str]:
    paths = {
        "scheduler_account_authority_sha256": R8U_R5_ACCOUNT_AUTHORITY_PATH,
        "r8u_r4_failure_evidence_sha256": R8U_R5_R4_FAILURE_EVIDENCE_PATH,
        "worker_context_probe_receipt_sha256": R8U_R5_PROBE_RECEIPT_PATH,
        "worker_context_probe_accounting_sha256": R8U_R5_PROBE_ACCOUNTING_PATH,
        "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_PATH,
        "live_publication_locality_sha256": R8U_R5_LOCALITY_PATH,
        "publication_primitive_probe_sha256": R8U_R5_PRIMITIVE_PROBE_PATH,
        "publication_claim_sha256": R8U_R5_PUBLICATION_CLAIM_PATH,
        "publication_receipt_sha256": R8U_R5_PUBLICATION_PATH,
        "resume_capacity_sha256": R8U_R5_CAPACITY_PATH,
        "resume_authority_sha256": R8U_R5_AUTHORITY_PATH,
        "resume_submission_receipt_sha256": R8U_R5_SUBMISSION_PATH,
        "resume_accounting_sha256": R8U_R5_ACCOUNTING_PATH,
        "resume_terminal_receipt_sha256": R8U_R5_TERMINAL_PATH,
    }
    value = {key: core.sha256_file(path) for key, path in paths.items()}
    if set(value) != R8U_R5_CONTINUATION_LINK_KEYS:
        _fail("R8U_R5_CONTINUATION_CHAIN_INVALID")
    return value


def _r8u_r5_continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r5_seq_{implementation_commit[:8]}"


def _r8u_r5_continuation_finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r5_fin_{implementation_commit[:8]}"


def _r8u_r5_continuation_array_command(
    implementation_commit: str,
) -> list[str]:
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r5_continuation_array_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R5_CONTINUATION_SCHEDULER_ROOT),
        "-t", R8U_FIXED_CONTINUATION_TASK_RANGE,
        "-tc", str(R8U_FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", str(RUNNER_PATH),
    ]


def _r8u_r5_continuation_finalizer_command(
    implementation_commit: str, array_job_id: str,
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8U_R5_CONTINUATION_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH), "-clear", "-terse", "-r", "n",
        "-P", "mimicecho", "-N",
        _r8u_r5_continuation_finalizer_job_name(implementation_commit),
        "-j", "y", "-o", str(R8U_R5_CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid", array_job_id, "-l", "h_rt=12:00:00",
        "-pe", "omp", "4", "-l", "mem_per_core=8G", str(RUNNER_PATH),
    ]


def _r8u_r5_continuation_claim(
    *, run: sequential.FullRun, implementation_commit: str,
    qsub_environment_sha256: str, prefix_receipts: Sequence[str],
) -> Mapping[str, Any]:
    if (
        len(prefix_receipts) != 16
        or tuple(prefix_receipts[:15])
        != tuple(item[2] for item in R8U_PREFIX_RECEIPT_AUTHORITIES)
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
    ):
        _fail("R8U_R5_CONTINUATION_CLAIM_INVALID")
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_fixed_continuation_claim_v1",
            status="AUTHORIZED_FIXED_CONTINUATION_17_19",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r5_continuation_links()),
        "prior_implementation_commit": (
            R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1,
        "total_new_qsub_maximum": 4,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "fifth_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
    if set(value) != R8U_R5_CONTINUATION_CLAIM_KEYS:
        _fail("R8U_R5_CONTINUATION_CLAIM_INVALID")
    return value


def _r8u_r5_continuation_submission_receipt(
    *, implementation_commit: str, resume_job_id: str,
    array_job_id: str, finalizer_job_id: str,
    qsub_environment_sha256: str, continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        any(JOB_RE.fullmatch(value) is None for value in (
            resume_job_id, array_job_id, finalizer_job_id
        ))
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8U_R5_CONTINUATION_SUBMISSION_INVALID")
    array_command = _r8u_r5_continuation_array_command(implementation_commit)
    finalizer_command = _r8u_r5_continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_fixed_continuation_submission_v1",
            status="PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
            implementation_commit=implementation_commit,
        ),
        **dict(_r8u_r5_continuation_links()),
        "resume_job_id": resume_job_id,
        "array_job_name": _r8u_r5_continuation_array_job_name(
            implementation_commit
        ),
        "finalizer_job_name": _r8u_r5_continuation_finalizer_job_name(
            implementation_commit
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": finalizer_command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "failed_partial_seal_sha256": core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(_qsub_evidence_authority(
            R8U_R5_CONTINUATION_SCHEDULER_ROOT, "array"
        )),
        "finalizer_qsub_evidence": dict(_qsub_evidence_authority(
            R8U_R5_CONTINUATION_SCHEDULER_ROOT, "finalizer"
        )),
        "scheduler_submission_count": 2,
        "total_new_qsub_submissions": 4,
        "scheduler_submission_maximum": 4,
        "array_task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(R8U_FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": R8U_FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "fifth_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    if set(value) != R8U_R5_CONTINUATION_SUBMISSION_KEYS:
        _fail("R8U_R5_CONTINUATION_SUBMISSION_INVALID")
    return value


def submit_r8u_r5_continuation_17_19(
    *, qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Future-only array/finalizer submitter; never called by this R5 phase."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r5_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha = scheduler.qsub_environment_sha256(environment)
    run, account, _authority, resume_submission = (
        _validate_r8u_r5_resume_submission()
    )
    validate_r8u_r5_resume_terminal()
    if environment_sha != account.get("qsub_environment_sha256"):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    if (
        os.path.lexists(R8U_R5_ACCOUNTING_PATH)
        or os.path.lexists(R8U_R5_CONTINUATION_ROOT)
    ):
        _fail("R8U_R5_CONTINUATION_OUTPUT_COLLISION")
    resume_job_id = str(resume_submission.get("resume_job_id", ""))
    accounting_projection = _query_recovery_accounting(
        recovery_job_id=resume_job_id,
        environment=environment,
        runner=qacct_runner,
    )
    accounting = _r8u_r5_resume_accounting_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id,
        accounting=accounting_projection,
    )
    _write_private_json(R8U_R5_ACCOUNTING_PATH, accounting)
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    _create_private_directory_no_clobber(R8U_R5_CONTINUATION_ROOT)
    _create_private_directory_no_clobber(R8U_R5_CONTINUATION_SCHEDULER_ROOT)
    claim = _r8u_r5_continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha,
        prefix_receipts=prefix,
    )
    claim_sha = _write_private_json(R8U_R5_CONTINUATION_CLAIM_PATH, claim)
    array_job_id = scheduler._capture_qsub(
        "array", _r8u_r5_continuation_array_command(implementation_commit),
        root=R8U_R5_CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
        parser=_parse_r8u_array_qsub_stdout,
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer",
        _r8u_r5_continuation_finalizer_command(
            implementation_commit, array_job_id
        ),
        root=R8U_R5_CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    submission = _r8u_r5_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=resume_job_id,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha,
        continuation_claim_sha256=claim_sha,
    )
    _write_private_json(R8U_R5_CONTINUATION_SUBMISSION_PATH, submission)
    return {
        "status": "R8U_R5_CONTINUATION_17_19_SUBMITTED",
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": 1,
        "new_qsub_submissions": 2,
        "total_new_qsub_submissions": 4,
        "cloud_requests": 0,
    }


def _r8u_r5_continuation_worker_paths(
    *, expected_role: str, expected_task_id: str | None,
) -> tuple[Path, Path]:
    if expected_role == R8U_R5_ARRAY_ROLE and expected_task_id in {"17", "18", "19"}:
        stem = f"array_task_{expected_task_id}_worker_context"
    elif expected_role == R8U_R5_FINALIZER_ROLE and expected_task_id is None:
        stem = "finalizer_worker_context"
    else:
        _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return (
        R8U_R5_CONTINUATION_ROOT / f"{stem}_diagnostic.restricted.json",
        R8U_R5_CONTINUATION_ROOT / f"{stem}_receipt.restricted.json",
    )


def _r8u_r5_continuation_worker_receipt(
    *, implementation_commit: str, job_id: str, expected_role: str,
    expected_task_id: str | None, diagnostic_sha256: str,
    worker_qstat_projection: Mapping[str, Any],
    worker_process_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = {
        **_r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_continuation_worker_context_v1",
            status="PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        "continuation_claim_sha256": core.sha256_file(
            R8U_R5_CONTINUATION_CLAIM_PATH
        ),
        "continuation_submission_sha256": core.sha256_file(
            R8U_R5_CONTINUATION_SUBMISSION_PATH
        ),
        "worker_diagnostic_sha256": diagnostic_sha256,
        "worker_qstat_projection_sha256": core.canonical_json_sha256(
            worker_qstat_projection
        ),
        "worker_process_projection_sha256": core.canonical_json_sha256(
            worker_process_projection
        ),
        "job_id": job_id,
        "worker_role": expected_role,
        "task_id": expected_task_id if expected_task_id is not None else "NONE",
        "effective_uid_match": True,
        "job_id_match": True,
        "task_context_match": True,
        "job_role_match": True,
        "canonical_worker_environment_pass": True,
    }
    if (
        set(value) != R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS
        or JOB_RE.fullmatch(job_id) is None
        or any(
            SHA_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "scheduler_account_authority_sha256",
                "continuation_claim_sha256", "continuation_submission_sha256",
                "worker_diagnostic_sha256", "worker_qstat_projection_sha256",
                "worker_process_projection_sha256",
            )
        )
    ):
        _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return value


def validate_r8u_r5_continuation_worker_submission(
    *, current_job_id: str,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Validate shared R5 worker context before any array/finalizer work."""

    if JOB_RE.fullmatch(current_job_id) is None:
        _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    _r8u_r5_require_account_file_owner_matches_effective_uid()
    _wait_for_r8u_r5_control(R8U_R5_CONTINUATION_SUBMISSION_PATH)
    account, _ = _load_private_json(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    validate_r8u_r5_scheduler_account_authority(account)
    implementation_commit = str(account.get("implementation_commit", ""))
    claim, _ = _load_private_json(R8U_R5_CONTINUATION_CLAIM_PATH)
    submission, _ = _load_private_json(R8U_R5_CONTINUATION_SUBMISSION_PATH)
    array_job_id = str(submission.get("array_job_id", ""))
    finalizer_job_id = str(submission.get("finalizer_job_id", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if current_job_id == array_job_id:
        if task_text not in {"17", "18", "19"}:
            _fail("SCHEDULER_TASK_ID_BINDING_MISMATCH")
        expected_role = R8U_R5_ARRAY_ROLE
        expected_task_id: str | None = task_text
        expected_job_name = _r8u_r5_continuation_array_job_name(
            implementation_commit
        )
        self_marker = "--run-r8u-r5-continuation-17-19-array-task"
    elif current_job_id == finalizer_job_id:
        if task_text not in {"", "undefined"}:
            _fail("SCHEDULER_TASK_ID_BINDING_MISMATCH")
        expected_role = R8U_R5_FINALIZER_ROLE
        expected_task_id = None
        expected_job_name = _r8u_r5_continuation_finalizer_job_name(
            implementation_commit
        )
        self_marker = "--run-r8u-r5-continuation-finalizer"
    else:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")
    common_claim = _r8u_r5_common(
        artifact_type="lvef_c3_r8u_r5_fixed_continuation_claim_v1",
        status="AUTHORIZED_FIXED_CONTINUATION_17_19",
        implementation_commit=implementation_commit,
    )
    common_submission = _r8u_r5_common(
        artifact_type="lvef_c3_r8u_r5_fixed_continuation_submission_v1",
        status="PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        implementation_commit=implementation_commit,
    )
    account_sha = core.sha256_file(R8U_R5_ACCOUNT_AUTHORITY_PATH)
    if (
        set(claim) != R8U_R5_CONTINUATION_CLAIM_KEYS
        or set(submission) != R8U_R5_CONTINUATION_SUBMISSION_KEYS
        or any(claim.get(key) != item for key, item in common_claim.items())
        or any(
            submission.get(key) != item
            for key, item in common_submission.items()
        )
        or claim.get("scheduler_account_authority_sha256") != account_sha
        or submission.get("scheduler_account_authority_sha256") != account_sha
        or claim.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or submission.get("array_job_name")
        != _r8u_r5_continuation_array_job_name(implementation_commit)
        or submission.get("finalizer_job_name")
        != _r8u_r5_continuation_finalizer_job_name(implementation_commit)
    ):
        _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    diagnostic_path, receipt_path = _r8u_r5_continuation_worker_paths(
        expected_role=expected_role, expected_task_id=expected_task_id
    )
    diagnostic_exists = os.path.lexists(diagnostic_path)
    receipt_exists = os.path.lexists(receipt_path)
    if diagnostic_exists != receipt_exists:
        _fail("R8U_R5_CONTINUATION_WORKER_OUTPUT_COLLISION")
    if diagnostic_exists:
        # ``run_r8u_r5_continuation_array_task`` establishes the context before
        # entering the shared sequential worker.  The shared worker calls this
        # validator again at its submission boundary, so the second call must
        # validate the immutable readback instead of colliding or re-querying
        # scheduler/process state.
        diagnostic, _ = _load_private_json(diagnostic_path)
        _validate_r8u_r5_worker_diagnostic(diagnostic)
        worker_receipt, _ = _load_private_json(receipt_path)
        expected_worker_common = _r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_continuation_worker_context_v1",
            status="PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
            implementation_commit=implementation_commit,
        )
        if (
            set(worker_receipt) != R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS
            or any(
                worker_receipt.get(key) != item
                for key, item in expected_worker_common.items()
            )
            or worker_receipt.get("scheduler_account_authority_sha256")
            != account_sha
            or worker_receipt.get("continuation_claim_sha256")
            != core.sha256_file(R8U_R5_CONTINUATION_CLAIM_PATH)
            or worker_receipt.get("continuation_submission_sha256")
            != core.sha256_file(R8U_R5_CONTINUATION_SUBMISSION_PATH)
            or worker_receipt.get("worker_diagnostic_sha256")
            != core.sha256_file(diagnostic_path)
            or worker_receipt.get("job_id") != current_job_id
            or worker_receipt.get("worker_role") != expected_role
            or worker_receipt.get("task_id")
            != (expected_task_id if expected_task_id is not None else "NONE")
            or any(
                worker_receipt.get(field) is not True
                for field in (
                    "effective_uid_match", "job_id_match",
                    "task_context_match", "job_role_match",
                    "canonical_worker_environment_pass",
                )
            )
            or any(
                SHA_RE.fullmatch(str(worker_receipt.get(field, ""))) is None
                for field in (
                    "worker_qstat_projection_sha256",
                    "worker_process_projection_sha256",
                )
            )
        ):
            _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    else:
        # Kernel/sealed identity, exact job/task/role, and runtime hashes are
        # established before any continuation science or body access.
        context = _r8u_r5_build_worker_context(
            account_authority=account,
            expected_job_id=current_job_id,
            expected_role=expected_role,
            expected_task_id=expected_task_id,
        )
        worker_qstat = _r8u_r5_qstat_projection(
            environment=context.environment,
            expected_job_id=current_job_id,
            expected_job_name=expected_job_name,
            runner=qstat_runner,
            worker_local=True,
            expected_task_id=expected_task_id,
            allowed_companion_job_id=(
                finalizer_job_id if expected_role == R8U_R5_ARRAY_ROLE else None
            ),
            allowed_companion_job_name=(
                _r8u_r5_continuation_finalizer_job_name(implementation_commit)
                if expected_role == R8U_R5_ARRAY_ROLE
                else None
            ),
        )
        _validate_r8u_r5_qstat_projection(
            worker_qstat,
            expected_job_id=current_job_id,
            expected_job_name=expected_job_name,
            worker_local=True,
        )
        worker_process = _r8u_r5_process_projection(
            environment=context.environment,
            runner=process_runner,
            worker_self_marker=self_marker,
        )
        diagnostic = _r8u_r5_worker_diagnostic(context)
        diagnostic_sha = _write_private_json(diagnostic_path, diagnostic)
        worker_receipt = _r8u_r5_continuation_worker_receipt(
            implementation_commit=implementation_commit,
            job_id=current_job_id,
            expected_role=expected_role,
            expected_task_id=expected_task_id,
            diagnostic_sha256=diagnostic_sha,
            worker_qstat_projection=worker_qstat,
            worker_process_projection=worker_process,
        )
        _write_private_json(receipt_path, worker_receipt)
    # Only after worker context passes, validate the scientific continuation chain.
    validate_r8u_r5_resume_terminal()
    _validate_r8u_r5_resume_accounting()
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    prefix = _r8u_validate_frozen_prefix(run, include_batch16=True)
    expected_claim = _r8u_r5_continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
        prefix_receipts=prefix,
    )
    expected_submission = _r8u_r5_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        resume_job_id=str(submission.get("resume_job_id", "")),
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=str(account["qsub_environment_sha256"]),
        continuation_claim_sha256=core.sha256_file(
            R8U_R5_CONTINUATION_CLAIM_PATH
        ),
    )
    if (
        not _exact_typed_value_equal(claim, expected_claim)
        or not _exact_typed_value_equal(submission, expected_submission)
        or submission.get("array_task_range")
        != R8U_FIXED_CONTINUATION_TASK_RANGE
        or submission.get("array_task_count") != 3
        or submission.get("array_max_concurrency") != 1
        or submission.get("scheduler_submission_count") != 2
        or submission.get("total_new_qsub_submissions") != 4
        or submission.get("scheduler_submission_maximum") != 4
        or submission.get("fifth_submission_reachable") is not False
    ):
        _fail("R8U_R5_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return submission


def run_r8u_r5_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"17", "18", "19"}
    ):
        _fail("R8U_R5_CONTINUATION_ARRAY_CONTEXT_INVALID")
    validate_r8u_r5_continuation_worker_submission(current_job_id=job_id)
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8U_R5_FIXED_CONTINUATION
    )
    value = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if value.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8U_R5_CONTINUATION_BATCH_NOT_FINALIZED")
    return value


def run_r8u_r5_continuation_finalizer() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8U_R5_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    # Shared effective-UID/job/role/qstat/process context is the first
    # substantive finalizer gate and never calls the qsub environment builder.
    validate_r8u_r5_continuation_worker_submission(current_job_id=job_id)
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
        r8u_r5=True,
    )
    implementation_commit = _current_r8u_r5_implementation_commit()
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    historical = _r8u_historical_r8r_chain_authority()
    authority = finalizer.R8UR5ImplementationAuthority(
        implementation_commit=implementation_commit,
        historical_r8r_recovery_authority_sha256=(
            historical["recovery_authority_sha256"]
        ),
        historical_r8r_recovery_terminal_receipt_sha256=(
            historical["recovery_terminal_receipt_sha256"]
        ),
        historical_r8r_continuation_capacity_receipt_sha256=(
            historical["continuation_capacity_receipt_sha256"]
        ),
        historical_r8r_continuation_claim_sha256=(
            historical["continuation_claim_sha256"]
        ),
        historical_r8r_continuation_submission_receipt_sha256=(
            historical["continuation_submission_receipt_sha256"]
        ),
        failed_partial_seal_sha256=core.sha256_file(
            R8U_FAILED_PARTIAL_SEAL_PATH
        ),
        r2_recovery_capacity_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_CAPACITY_PATH
        ),
        r2_recovery_authority_sha256=core.sha256_file(
            R8U_RECOVERY_AUTHORITY_PATH
        ),
        r2_recovery_submission_receipt_sha256=core.sha256_file(
            R8U_RECOVERY_SUBMISSION_PATH
        ),
        r3_extraction_candidate_seal_sha256=core.sha256_file(
            R8U_R3_CANDIDATE_SEAL_PATH
        ),
        replay_diagnosis_sha256=core.sha256_file(R8U_R4_DIAGNOSIS_PATH),
        scheduler_account_authority_sha256=core.sha256_file(
            R8U_R5_ACCOUNT_AUTHORITY_PATH
        ),
        r8u_r4_failure_evidence_sha256=core.sha256_file(
            R8U_R5_R4_FAILURE_EVIDENCE_PATH
        ),
        worker_context_probe_receipt_sha256=core.sha256_file(
            R8U_R5_PROBE_RECEIPT_PATH
        ),
        worker_context_probe_accounting_sha256=core.sha256_file(
            R8U_R5_PROBE_ACCOUNTING_PATH
        ),
        portable_candidate_authority_sha256=core.sha256_file(
            R8U_R4_PORTABLE_AUTHORITY_PATH
        ),
        live_publication_locality_sha256=core.sha256_file(
            R8U_R5_LOCALITY_PATH
        ),
        publication_primitive_probe_sha256=core.sha256_file(
            R8U_R5_PRIMITIVE_PROBE_PATH
        ),
        publication_claim_sha256=core.sha256_file(
            R8U_R5_PUBLICATION_CLAIM_PATH
        ),
        publication_receipt_sha256=core.sha256_file(R8U_R5_PUBLICATION_PATH),
        resume_capacity_receipt_sha256=core.sha256_file(R8U_R5_CAPACITY_PATH),
        resume_authority_sha256=core.sha256_file(R8U_R5_AUTHORITY_PATH),
        resume_submission_receipt_sha256=core.sha256_file(
            R8U_R5_SUBMISSION_PATH
        ),
        resume_accounting_sha256=core.sha256_file(R8U_R5_ACCOUNTING_PATH),
        resume_terminal_receipt_sha256=core.sha256_file(R8U_R5_TERMINAL_PATH),
        continuation_claim_sha256=core.sha256_file(
            R8U_R5_CONTINUATION_CLAIM_PATH
        ),
        continuation_submission_receipt_sha256=core.sha256_file(
            R8U_R5_CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan,
        requirements=run.requirements,
        production_root=run.production_root,
        contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8u_r5_implementation_authority=authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("selected_subjects") != 4_530
        or summary.get("verified_source_objects") != 335_984
        or summary.get("selected_source_bytes") != 1_216_569_133_322
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("new_no_cine_studies") != 0
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical") is not True
        or summary.get("implementation_authority_epoch_count") != 3
        or summary.get("r8u_implementation_commit") != implementation_commit
        or SHA_RE.fullmatch(str(
            summary.get("r8u_recovery_continuation_authority_sha256")
        )) is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8U_R5_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--submit-batch3-recovery", action="store_true")
    modes.add_argument("--recover-batch3-preservation", action="store_true")
    modes.add_argument("--validate-batch3-recovery", action="store_true")
    modes.add_argument("--submit-continuation", action="store_true")
    modes.add_argument("--run-continuation-array-task", action="store_true")
    modes.add_argument("--run-continuation-finalizer", action="store_true")
    modes.add_argument("--submit-batch16-recovery", action="store_true")
    modes.add_argument("--run-batch16-recovery", action="store_true")
    modes.add_argument("--validate-batch16-recovery", action="store_true")
    modes.add_argument("--submit-continuation-17-19", action="store_true")
    modes.add_argument(
        "--run-continuation-17-19-array-task", action="store_true"
    )
    modes.add_argument("--run-r8u-continuation-finalizer", action="store_true")
    return parser


def _r8u_r3_parser() -> argparse.ArgumentParser:
    """Closed parser for additive R3 resume and fixed continuation."""

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--submit-r8u-r3-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r3-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--submit-r8u-r3-continuation-17-19", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r3-continuation-17-19-array-task", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r3-continuation-finalizer", action="store_true"
    )
    return parser


def _r8u_r4_parser() -> argparse.ArgumentParser:
    """Closed parser for additive R4 resume and fixed continuation."""

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--submit-r8u-r4-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r4-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--submit-r8u-r4-continuation-17-19", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r4-continuation-17-19-array-task", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r4-continuation-finalizer", action="store_true"
    )
    return parser


def _r8u_r5_parser() -> argparse.ArgumentParser:
    """Closed parser for the R5 probe, resume, and future continuation."""

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--submit-r8u-r5-worker-context-probe", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r5-worker-context-probe", action="store_true"
    )
    modes.add_argument(
        "--adjudicate-r8u-r5-worker-context-probe", action="store_true"
    )
    modes.add_argument(
        "--submit-r8u-r5-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r5-batch16-publication-resume", action="store_true"
    )
    modes.add_argument(
        "--submit-r8u-r5-continuation-17-19", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r5-continuation-17-19-array-task", action="store_true"
    )
    modes.add_argument(
        "--run-r8u-r5-continuation-finalizer", action="store_true"
    )
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    mode_prefix = "R8R"
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        r8u_r3_options = {
            "--submit-r8u-r3-batch16-publication-resume",
            "--run-r8u-r3-batch16-publication-resume",
            "--submit-r8u-r3-continuation-17-19",
            "--run-r8u-r3-continuation-17-19-array-task",
            "--run-r8u-r3-continuation-finalizer",
        }
        r8u_r4_options = {
            "--submit-r8u-r4-batch16-publication-resume",
            "--run-r8u-r4-batch16-publication-resume",
            "--submit-r8u-r4-continuation-17-19",
            "--run-r8u-r4-continuation-17-19-array-task",
            "--run-r8u-r4-continuation-finalizer",
        }
        r8u_r5_options = {
            "--submit-r8u-r5-worker-context-probe",
            "--run-r8u-r5-worker-context-probe",
            "--adjudicate-r8u-r5-worker-context-probe",
            "--submit-r8u-r5-batch16-publication-resume",
            "--run-r8u-r5-batch16-publication-resume",
            "--submit-r8u-r5-continuation-17-19",
            "--run-r8u-r5-continuation-17-19-array-task",
            "--run-r8u-r5-continuation-finalizer",
        }
        if any(argument in r8u_r5_options for argument in arguments):
            mode_prefix = "R8U_R5"
            r5_args = _r8u_r5_parser().parse_args(arguments)
            if r5_args.submit_r8u_r5_worker_context_probe:
                value = submit_r8u_r5_worker_context_probe()
                print(f"R8U_R5_STATUS={value['status']}")
                print(f"R8U_R5_PROBE_JOB_ID={value['probe_job_id']}")
                print("R8U_R5_PROBE_QSUB_EXIT=0")
                print("R8U_R5_PROBE_CPU_SLOTS=1")
                print("R8U_R5_PROBE_GPU_REQUESTED=NO")
                print("R8U_R5_NEW_QSUB_SUBMISSIONS=1")
            elif r5_args.run_r8u_r5_worker_context_probe:
                value = run_r8u_r5_worker_context_probe()
                diagnostic = value["diagnostic"]
                print("R8U_R5_STATUS=PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT")
                print("R8U_R5_EFFECTIVE_UID_MATCH=YES")
                print("R8U_R5_JOB_ID_MATCH=YES")
                print("R8U_R5_JOB_ROLE_MATCH=YES")
                for field, label in (
                    ("observed_user_match", "OBSERVED_USER_MATCH"),
                    ("observed_logname_match", "OBSERVED_LOGNAME_MATCH"),
                    ("observed_home_match", "OBSERVED_HOME_MATCH"),
                    ("observed_shell_match", "OBSERVED_SHELL_MATCH"),
                ):
                    print(
                        f"R8U_R5_{label}="
                        f"{_r8u_r5_aggregate_match_label(diagnostic, field=field)}"
                    )
                print(
                    "R8U_R5_PASSWD_LOOKUP_STATUS="
                    f"{_r8u_r5_aggregate_passwd_status(diagnostic['passwd_lookup_status'])}"
                )
                print("R8U_R5_CANDIDATE_SCANS=0")
                print("R8U_R5_DICOM_BODY_READS=0")
                print("R8U_R5_NPZ_BODY_READS=0")
                print("R8U_R5_CLOUD_REQUESTS=0")
            elif r5_args.adjudicate_r8u_r5_worker_context_probe:
                value = adjudicate_r8u_r5_worker_context_probe()
                print("R8U_R5_STATUS=PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT")
                print(f"R8U_R5_PROBE_JOB_ID={value['probe_job_id']}")
                print("R8U_R5_PROBE_QACCT_FAILED=0")
                print("R8U_R5_PROBE_QACCT_EXIT_STATUS=0")
                for field, label in (
                    ("observed_user_match", "OBSERVED_USER_MATCH"),
                    ("observed_logname_match", "OBSERVED_LOGNAME_MATCH"),
                    ("observed_home_match", "OBSERVED_HOME_MATCH"),
                    ("observed_shell_match", "OBSERVED_SHELL_MATCH"),
                ):
                    print(
                        f"R8U_R5_{label}="
                        f"{_r8u_r5_aggregate_match_label(value, field=field)}"
                    )
                print(
                    "R8U_R5_PASSWD_LOOKUP_STATUS="
                    f"{_r8u_r5_aggregate_passwd_status(value['passwd_lookup_status'])}"
                )
            elif r5_args.submit_r8u_r5_batch16_publication_resume:
                value = submit_r8u_r5_batch16_publication_resume()
                print(f"R8U_R5_STATUS={value['status']}")
                print(f"R8U_R5_RESUME_JOB_ID={value['resume_job_id']}")
                print("R8U_R5_RESUME_QSUB_EXIT=0")
                print(
                    "R8U_R5_RESUME_INITIAL_STATE="
                    f"{value['initial_state']}"
                )
                print(
                    "R8U_R5_CAPACITY_STATUS="
                    f"{value['capacity_status']}"
                )
                print(
                    "R8U_R5_OBSERVED_NPZ_FILES="
                    f"{value['observed_npz_files']}"
                )
                print("R8U_R5_MISSING_NPZ_FILES=0")
                print("R8U_R5_ADDITIONAL_NPZ_FILES=0")
                print("R8U_R5_TOTAL_NEW_QSUB_SUBMISSIONS=2")
                print("R8U_R5_LOGIN_NODE_POLLING_STARTED=NO")
                print("R8U_R5_CONTINUATION_17_19_SUBMITTED=NO")
                print("R8U_R5_FINALIZER_SUBMITTED=NO")
            elif r5_args.run_r8u_r5_batch16_publication_resume:
                run_r8u_r5_batch16_publication_resume()
                print("R8U_R5_STATUS=PASS_BATCH16_R8U_R5_PUBLICATION_RESUME_FINALIZED")
            elif r5_args.submit_r8u_r5_continuation_17_19:
                value = submit_r8u_r5_continuation_17_19()
                print("R8U_R5_STATUS=CONTINUATION_17_19_SUBMITTED")
                print(f"R8U_R5_ARRAY_JOB_ID={value['array_job_id']}")
                print(f"R8U_R5_FINALIZER_JOB_ID={value['finalizer_job_id']}")
                print("R8U_R5_NEW_QSUB_SUBMISSIONS=2")
                print("R8U_R5_TOTAL_NEW_QSUB_SUBMISSIONS=4")
            elif r5_args.run_r8u_r5_continuation_17_19_array_task:
                value = run_r8u_r5_continuation_array_task()
                print(f"R8U_R5_CONTINUATION_BATCH_STATUS={value['status']}")
            else:
                run_r8u_r5_continuation_finalizer()
                print("R8U_R5_STATUS=PASS_R8U_R5_FIXED_CONTINUATION_FINALIZED")
            return 0
        if any(argument in r8u_r4_options for argument in arguments):
            mode_prefix = "R8U_R4"
            r4_args = _r8u_r4_parser().parse_args(arguments)
            if r4_args.submit_r8u_r4_batch16_publication_resume:
                value = submit_r8u_r4_batch16_publication_resume()
                print(
                    "R8U_R4_STATUS="
                    "BATCH16_PUBLICATION_RESUME_SUBMITTED_AWAITING_TERMINAL"
                )
                print(f"R8U_R4_RESUME_JOB_ID={value['resume_job_id']}")
                print(f"R8U_R4_CAPACITY_STATUS={value['capacity_status']}")
                print(f"R8U_R4_RESUME_INITIAL_STATE={value['initial_state']}")
                print(f"R8U_R4_OBSERVED_NPZ_FILES={value['observed_npz_files']}")
                print("R8U_R4_MISSING_NPZ_FILES=0")
                print("R8U_R4_ADDITIONAL_NPZ_FILES=0")
                print("R8U_R4_CANDIDATE_DICOM_BODY_READS=0")
                print("R8U_R4_CANDIDATE_NPZ_BODY_READS=0")
                print("R8U_R4_NEW_QSUB_SUBMISSIONS=1")
                print("R8U_R4_LOGIN_NODE_POLLING_STARTED=NO")
                print("R8U_R4_CONTINUATION_17_19_SUBMITTED=NO")
                print("R8U_R4_FINALIZER_SUBMITTED=NO")
            elif r4_args.run_r8u_r4_batch16_publication_resume:
                run_r8u_r4_batch16_publication_resume()
                print("PASS_BATCH16_R8U_R4_PUBLICATION_RESUME_FINALIZED")
            elif r4_args.submit_r8u_r4_continuation_17_19:
                value = submit_r8u_r4_continuation_17_19()
                print("R8U_R4_STATUS=CONTINUATION_17_19_SUBMITTED")
                print(f"R8U_R4_ARRAY_JOB_ID={value['array_job_id']}")
                print(f"R8U_R4_FINALIZER_JOB_ID={value['finalizer_job_id']}")
                print("R8U_R4_NEW_QSUB_SUBMISSIONS=2")
                print("R8U_R4_TOTAL_NEW_QSUB_SUBMISSIONS=3")
            elif r4_args.run_r8u_r4_continuation_17_19_array_task:
                value = run_r8u_r4_continuation_array_task()
                print(f"R8U_R4_CONTINUATION_BATCH_STATUS={value['status']}")
            else:
                run_r8u_r4_continuation_finalizer()
                print("PASS_R8U_R4_FIXED_CONTINUATION_FINALIZED")
            return 0
        if any(argument in r8u_r3_options for argument in arguments):
            mode_prefix = "R8U_R3"
            r3_args = _r8u_r3_parser().parse_args(arguments)
            if r3_args.submit_r8u_r3_batch16_publication_resume:
                value = submit_r8u_r3_batch16_publication_resume()
                print(
                    "R8U_R3_STATUS="
                    "BATCH16_PUBLICATION_RESUME_SUBMITTED_AWAITING_TERMINAL"
                )
                print(f"R8U_R3_RESUME_JOB_ID={value['resume_job_id']}")
                print(f"R8U_R3_CAPACITY_STATUS={value['capacity_status']}")
                print(f"R8U_R3_RESUME_INITIAL_STATE={value['initial_state']}")
                print("R8U_R3_NEW_QSUB_SUBMISSIONS=1")
                print("R8U_R3_LOGIN_NODE_POLLING_STARTED=NO")
                print("R8U_R3_CONTINUATION_17_19_SUBMITTED=NO")
                print("R8U_R3_FINALIZER_SUBMITTED=NO")
            elif r3_args.run_r8u_r3_batch16_publication_resume:
                run_r8u_r3_batch16_publication_resume()
                print("PASS_BATCH16_PUBLICATION_RESUME_FINALIZED")
            elif r3_args.submit_r8u_r3_continuation_17_19:
                value = submit_r8u_r3_continuation_17_19()
                print("R8U_R3_STATUS=CONTINUATION_17_19_SUBMITTED")
                print(f"R8U_R3_ARRAY_JOB_ID={value['array_job_id']}")
                print(f"R8U_R3_FINALIZER_JOB_ID={value['finalizer_job_id']}")
                print("R8U_R3_NEW_QSUB_SUBMISSIONS=2")
                print("R8U_R3_TOTAL_NEW_QSUB_SUBMISSIONS=3")
                print("R8U_R3_LOGIN_NODE_POLLING_STARTED=NO")
            elif r3_args.run_r8u_r3_continuation_17_19_array_task:
                value = run_r8u_r3_continuation_array_task()
                print(f"R8U_R3_CONTINUATION_BATCH_STATUS={value['status']}")
            else:
                run_r8u_r3_continuation_finalizer()
                print("PASS_R8U_R3_FIXED_CONTINUATION_FINALIZED")
            return 0
        args = _parser().parse_args(arguments)
        r8u_mode = any(
            (
                args.submit_batch16_recovery,
                args.run_batch16_recovery,
                args.validate_batch16_recovery,
                args.submit_continuation_17_19,
                args.run_continuation_17_19_array_task,
                args.run_r8u_continuation_finalizer,
            )
        )
        if r8u_mode:
            mode_prefix = "R8U"
        if args.submit_batch3_recovery:
            value = submit_batch3_recovery()
            print(f"R8R_BATCH3_RECOVERY_JOB_ID={value['recovery_job_id']}")
            print("R8R_NEW_QSUB_SUBMISSIONS=1")
        elif args.recover_batch3_preservation:
            recover_batch3_preservation()
            print("PASS_BATCH3_PRESERVATION_RECOVERY")
        elif args.validate_batch3_recovery:
            validate_recovery_terminal()
            print("R8R_BATCH3_RECOVERY_VALIDATION=PASS")
        elif args.submit_continuation:
            value = submit_continuation()
            print(f"R8R_CONTINUATION_ARRAY_JOB_ID={value['array_job_id']}")
            print(f"R8R_CONTINUATION_FINALIZER_JOB_ID={value['finalizer_job_id']}")
            print("R8R_CONTINUATION_TASK_RANGE=4-19")
            print("R8R_CONTINUATION_MAX_CONCURRENCY=1")
            print("R8R_NEW_QSUB_SUBMISSIONS=2")
        elif args.run_continuation_array_task:
            run_continuation_array_task()
            print("FULL_C3_ARRAY_TASK=PASS")
        elif args.run_continuation_finalizer:
            run_continuation_finalizer()
            print("FULL_C3_COHORT_FINALIZER=PASS")
        elif args.submit_batch16_recovery:
            value = submit_r8u_batch16_recovery()
            print("R8U_STATUS=BATCH16_RECOVERY_SUBMITTED_AWAITING_TERMINAL")
            print(f"R8U_BATCH16_RECOVERY_JOB_ID={value['recovery_job_id']}")
            print(f"R8U_BATCH16_RECOVERY_CAPACITY_STATUS={value['capacity_status']}")
            print(
                "R8U_BATCH16_RECOVERY_INITIAL_STATE="
                f"{value['initial_state']}"
            )
            print("R8U_NEW_QSUB_SUBMISSIONS=1")
            print("R8U_LOGIN_NODE_POLLING_STARTED=NO")
        elif args.run_batch16_recovery:
            run_r8u_batch16_recovery()
            print("PASS_BATCH16_RECOVERY_FINALIZED")
        elif args.validate_batch16_recovery:
            validate_r8u_recovery_terminal()
            print("R8U_BATCH16_RECOVERY_VALIDATION=PASS")
        elif args.submit_continuation_17_19:
            value = submit_r8u_continuation_17_19()
            print(f"R8U_CONTINUATION_ARRAY_JOB_ID={value['array_job_id']}")
            print(
                "R8U_CONTINUATION_FINALIZER_JOB_ID="
                f"{value['finalizer_job_id']}"
            )
            print("R8U_CONTINUATION_TASK_RANGE=17-19")
            print("R8U_CONTINUATION_MAX_CONCURRENCY=1")
            print("R8U_NEW_QSUB_SUBMISSIONS=2")
            print("R8U_TOTAL_NEW_QSUB_SUBMISSIONS=3")
        elif args.run_continuation_17_19_array_task:
            run_r8u_continuation_array_task()
            print("FULL_C3_R8U_ARRAY_TASK=PASS")
        else:
            run_r8u_continuation_finalizer()
            print("FULL_C3_R8U_COHORT_FINALIZER=PASS")
        return 0
    except R8RControllerError as exc:
        print(f"{mode_prefix}_STATUS=BLOCKED_{exc.code}")
        if exc.stage is not None:
            print(f"{mode_prefix}_FAILED_STAGE={exc.stage}")
        if exc.validation_substage is not None:
            print(
                f"{mode_prefix}_PRESERVATION_VALIDATION_SUBSTAGE="
                f"{exc.validation_substage}"
            )
        if mode_prefix.startswith("R8U"):
            print("R8U_BATCH16_CLOUD_REQUESTS=0")
            print("R8U_BATCH16_DOWNLOAD_RERUNS=0")
            for field, label in (
                ("quota_deficit_bytes", "R8U_CAPACITY_QUOTA_DEFICIT_BYTES"),
                (
                    "physical_deficit_bytes",
                    "R8U_CAPACITY_PHYSICAL_DEFICIT_BYTES",
                ),
                ("file_slot_deficit", "R8U_CAPACITY_FILE_SLOT_DEFICIT"),
            ):
                if field in exc.capacity_deficits:
                    print(f"{label}={exc.capacity_deficits[field]}")
        else:
            print("R8R_NEW_CLOUD_REQUESTS=0")
            print("R8R_NEW_DICOM_BODY_READS=0")
            print("R8R_NEW_GPU_EXECUTIONS_BEFORE_CONTINUATION=0")
            print("R8R_NEW_EMBEDDING_GENERATIONS=0")
        return 78
    except Exception as exc:
        code = getattr(
            exc, "code", f"{mode_prefix}_UNEXPECTED_SANITIZED_FAILURE"
        )
        safe = (
            code
            if isinstance(code, str) and SAFE_CODE_RE.fullmatch(code)
            else f"{mode_prefix}_UNEXPECTED_SANITIZED_FAILURE"
        )
        print(f"{mode_prefix}_STATUS=BLOCKED_{safe}")
        if mode_prefix.startswith("R8U"):
            print("R8U_BATCH16_CLOUD_REQUESTS=0")
            print("R8U_BATCH16_DOWNLOAD_RERUNS=0")
        else:
            print("R8R_NEW_CLOUD_REQUESTS=0")
            print("R8R_NEW_DICOM_BODY_READS=0")
            print("R8R_NEW_GPU_EXECUTIONS_BEFORE_CONTINUATION=0")
            print("R8R_NEW_EMBEDDING_GENERATIONS=0")
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
