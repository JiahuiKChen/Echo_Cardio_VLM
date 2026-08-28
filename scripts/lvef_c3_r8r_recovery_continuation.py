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
import json
import os
from pathlib import Path, PurePosixPath
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


def _load_fixed_original_run(
    *,
    scheduler_job_identity: str,
    runtime_validation_context: stages.RuntimeAuthorityValidationContext,
    r8u: bool = False,
) -> sequential.FullRun:
    if not isinstance(
        runtime_validation_context, stages.RuntimeAuthorityValidationContext
    ):
        _fail("R8R_RUNTIME_VALIDATION_CONTEXT_INVALID")
    implementation_commit = (
        _current_r8u_implementation_commit()
        if r8u
        else _current_implementation_commit()
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
            r"lvef_c3_(?:full_(?:seq|fin)|r8[ru]_(?:rec|seq|fin))_[0-9a-f]{8}",
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
        "run_production_dicom_extraction",
        "run_production_echoprime",
        "preserve_lvef_c3_production_batch",
        "retire_lvef_c3_extracted_cache",
        "finalize_lvef_c3_production",
        "lvef_c3_r8u_rec_",
        "lvef_c3_r8u_seq_",
        "lvef_c3_r8u_fin_",
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


def guarded_main(argv: Sequence[str] | None = None) -> int:
    mode_prefix = "R8R"
    try:
        args = _parser().parse_args(argv)
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
        if mode_prefix == "R8U":
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
        if mode_prefix == "R8U":
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
