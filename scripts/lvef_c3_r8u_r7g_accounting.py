#!/usr/bin/env python3
"""Fixed R7G authority and qacct receipts for the completed R7F jobs.

This additive module never selects a scheduler job, task, attempt, plan, or
destination from a caller.  It first reopens the five hash-pinned R7F
authorities and their cross-bound account/probe receipts, derives one closed
terminal authority, and then permits exactly the three array-task records and
one finalizer record named by that authority.  Importing or executing the
module performs no scheduler or scientific operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Final, Mapping, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_full_scheduler as scheduler
import lvef_c3_orchestration_core as core
import lvef_c3_r8r_recovery_continuation as r7
import lvef_c3_r8u_r7d_capacity as r7d_capacity


ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
SCIENTIFIC_COMMIT: Final = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
RUNTIME_IMPLEMENTATION_COMMIT: Final = (
    "2223d9768a1cc23efbe95a3c5474ea747a383a10"
)
R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT: Final = (
    "4dc4b2327f91ffd3912c91a7113f16d41d0562a8"
)

ARRAY_JOB_ID: Final = "7480830"
FINALIZER_JOB_ID: Final = "7480831"
PROBE_JOB_ID: Final = "7480822"
ARRAY_TASK_IDS: Final = (17, 18, 19)
ARRAY_TASK_RANGE: Final = "17-19"
ARRAY_MAX_CONCURRENCY: Final = 1
ARRAY_ROLE: Final = r7.R8U_R7D_ARRAY_ROLE
FINALIZER_ROLE: Final = r7.R8U_R7D_FINALIZER_ROLE
PROBE_ROLE: Final = r7.R8U_R7D_PROBE_ROLE

TRACKED_SUBMISSION_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "submit_r8u_r7f_continuation_17_19"
)
TRACKED_WORKER_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "run_r8u_r7d_continuation_array_task"
)
TRACKED_FINALIZER_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "run_r8u_r7d_continuation_finalizer"
)
TRACKED_RUNNER_PATH: Final = r7.RUNNER_PATH

CAPACITY_RECEIPT_PATH: Final = r7.R8U_R7F_CAPACITY_PATH
CONTINUATION_CLAIM_PATH: Final = r7.R8U_R7F_CONTINUATION_CLAIM_PATH
ARRAY_SUBMISSION_PATH: Final = r7.R8U_R7F_ARRAY_SUBMISSION_PATH
FINALIZER_SUBMISSION_PATH: Final = r7.R8U_R7F_FINALIZER_SUBMISSION_PATH
COMBINED_SUBMISSION_PATH: Final = r7.R8U_R7D_CONTINUATION_SUBMISSION_PATH
ACCOUNT_AUTHORITY_PATH: Final = r7.R8U_R7D_ACCOUNT_AUTHORITY_PATH
PROBE_AUTHORITY_PATH: Final = r7.R8U_R7D_PROBE_AUTHORITY_PATH
PROBE_SUBMISSION_PATH: Final = r7.R8U_R7D_PROBE_SUBMISSION_PATH
PROBE_WORKER_RECEIPT_PATH: Final = r7.R8U_R7D_PROBE_WORKER_RECEIPT_PATH
PROBE_ACCOUNTING_PATH: Final = r7.R8U_R7D_PROBE_ACCOUNTING_PATH
PROBE_TERMINAL_PATH: Final = r7.R8U_R7D_PROBE_TERMINAL_PATH
PROBE_SCHEDULER_ROOT: Final = r7.R8U_R7D_PROBE_SCHEDULER_ROOT
SCHEDULER_LOG_ROOT: Final = r7.R8U_R7D_CONTINUATION_SCHEDULER_ROOT

R7F_COMMON_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "prior_finalized_runtime_commit",
        "r7c_adjudication_commit",
        "implementation_commit",
        "implementation_authority_epochs",
        "attempt_id",
        "batch_plan_sha256",
    }
)
PROBE_SUBMISSION_KEYS: Final = R7F_COMMON_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "probe_authority_sha256",
        "probe_job_id",
        "probe_job_name",
        "worker_role",
        "probe_qsub_argv_sha256",
        "probe_qsub_evidence",
        "qsub_environment_sha256",
        "array_task_id",
        "array_task_count",
        "array_max_concurrency",
        "scheduler_submission_count",
        "scheduler_submission_maximum",
        "scientific_execution_authorized",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
        "r7f_continuation_claim_sha256",
        "r7f_capacity_receipt_sha256",
    }
)
PROBE_TERMINAL_KEYS: Final = R7F_COMMON_KEYS | frozenset(
    {
        "probe_submission_receipt_sha256",
        "worker_context_receipt_sha256",
        "accounting_receipt_sha256",
        "qstat_classification",
        "controlling_worker_identity",
        "failed",
        "exit_status",
        "task_id",
        "scientific_artifacts_created",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
    }
)

R7F_AUTHORITY_NAMES: Final = (
    "capacity",
    "continuation_claim",
    "array_submission",
    "finalizer_submission",
    "combined_submission",
)

HASH_PINNED_HISTORICAL_PRODUCER_JSON: Final = (
    "HASH_PINNED_HISTORICAL_PRODUCER_JSON"
)
PRODUCER_CANONICAL_JSON: Final = "PRODUCER_CANONICAL_JSON"
R7G_COMPACT_CANONICAL_JSON: Final = "R7G_COMPACT_CANONICAL_JSON"

# This is the closed artifact-instance audit requested for R7G-R1.  The
# separately hash-validated immutable plan is not an artifact in this table.
# The optional historical cohort role remains classified even when absent.
HISTORICAL_JSON_ROLE_POLICY: Final = {
    **{
        role: HASH_PINNED_HISTORICAL_PRODUCER_JSON
        for role in (
            "R7F_CAPACITY_RECEIPT",
            "R7F_CONTINUATION_CLAIM",
            "R7F_ARRAY_SUBMISSION_RECEIPT",
            "R7F_FINALIZER_SUBMISSION_RECEIPT",
            "R7F_COMBINED_SUBMISSION_RECEIPT",
            *(f"BATCH_{ordinal}_FINALIZATION_RECEIPT" for ordinal in range(1, 17)),
        )
    },
    **{
        role: PRODUCER_CANONICAL_JSON
        for role in (
            "SCHEDULER_ACCOUNT_AUTHORITY",
            "CPU_PROBE_SUBMISSION_RECEIPT",
            "CPU_PROBE_TERMINAL_RECEIPT",
            "BATCH_17_FINALIZATION_RECEIPT",
            "BATCH_18_FINALIZATION_RECEIPT",
            "BATCH_19_FINALIZATION_RECEIPT",
            "HISTORICAL_FULL_COHORT_RECEIPT",
        )
    },
    **{
        role: R7G_COMPACT_CANONICAL_JSON
        for role in (
            "R7G_TERMINAL_AUTHORITY",
            "R7G_TASK_17_ACCOUNTING_RECEIPT",
            "R7G_TASK_18_ACCOUNTING_RECEIPT",
            "R7G_TASK_19_ACCOUNTING_RECEIPT",
            "R7G_FINALIZER_ACCOUNTING_RECEIPT",
            "R7G_COHORT_FINALIZATION_RECEIPT",
            "R7G_POST_RECONSTRUCTION_LOCK_RECEIPT",
        )
    },
}
HISTORICAL_JSON_ROLES_AUDITED: Final = 35
if len(HISTORICAL_JSON_ROLE_POLICY) != HISTORICAL_JSON_ROLES_AUDITED:
    raise RuntimeError("R8U_R7G_HISTORICAL_JSON_ROLE_POLICY_INTERNAL_INVALID")

FIXED_AUTHORITY_SERIALIZATION_POLICY: Final = {
    "capacity": HASH_PINNED_HISTORICAL_PRODUCER_JSON,
    "continuation_claim": HASH_PINNED_HISTORICAL_PRODUCER_JSON,
    "array_submission": HASH_PINNED_HISTORICAL_PRODUCER_JSON,
    "finalizer_submission": HASH_PINNED_HISTORICAL_PRODUCER_JSON,
    "combined_submission": HASH_PINNED_HISTORICAL_PRODUCER_JSON,
    "scheduler_account": PRODUCER_CANONICAL_JSON,
    "probe_submission": PRODUCER_CANONICAL_JSON,
    "probe_terminal": PRODUCER_CANONICAL_JSON,
}
EXPECTED_R7F_RECEIPT_SHA256: Final = {
    "capacity": "4163c6faf46073ce79cd5dd6999407ec583d72663904b1bbda5c7bf20d45964d",
    "continuation_claim": "3eeb09049871ea79a48f7cd7015130909492ddf5e339f9fc9cb1f432204a9f14",
    "array_submission": "a2346272e02edc2584017361bf5404186a7a3eca1b51e25c704430bea95303a4",
    "finalizer_submission": "14c1d3913969aba5893e32de4524e525f891e43523a323b80f47313cb77214a5",
    "combined_submission": "4f6b1156e1580e175ed605c4a5002d6d180747a8bc82f0c1e90ebe3b80cbc306",
}

R7G_ROOT: Final = r7.ATTEMPT_ROOT / "r8u_r7g_r7f_terminal_adjudication"
TERMINAL_AUTHORITY_PATH: Final = R7G_ROOT / "terminal_authority.restricted.json"
ACCOUNTING_ROOT: Final = R7G_ROOT / "accounting"
QACCT_PATH: Final = scheduler.CANONICAL_SGE_ROOT / "bin/linux-x64/qacct"
MAX_QACCT_BYTES: Final = 4 * 1024 * 1024
MAX_RECEIPT_BYTES: Final = 4 * 1024 * 1024
QACCT_RECORD_NORMALIZATION: Final = "qacct-key-value-canonical-json-v1"
UTC_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"

COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
JOB_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
QACCT_KEY_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
NONNEGATIVE_INTEGER_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")
NONNEGATIVE_DECIMAL_RE: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:[.][0-9]+)?$"
)
FAILED_RE: Final = re.compile(
    r"^((?:0|[1-9][0-9]*))(?:[ \t]*:[ \t]*[^\x00\r\n]+)?$"
)


class R7GAccountingError(RuntimeError):
    """One stable fail-closed R7G accounting/authority error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7GAccountingError(code)


@dataclass(frozen=True)
class TerminalAuthorityResult:
    authority: Mapping[str, Any]
    authority_sha256: str
    created: bool


@dataclass(frozen=True)
class FixedAccountingSpec:
    """Identity and destinations for one of four immutable R7F records."""

    job_kind: str
    job_id: str
    task_id: int | None
    expected_job_role: str
    expected_job_name: str
    expected_owner: str
    receipt_path: Path
    scheduler_log_path: Path
    scheduler_log_basename: str

    def qacct_argv(self) -> tuple[str, ...]:
        command = [str(QACCT_PATH), "-j", self.job_id]
        if self.task_id is not None:
            command.extend(("-t", str(self.task_id)))
        return tuple(command)


@dataclass(frozen=True)
class AccountingReceiptResult:
    receipt: Mapping[str, Any]
    receipt_sha256: str
    created: bool
    qacct_query_count: int


TERMINAL_AUTHORITY_KEYS: Final = frozenset(
    {
        "schema_name",
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "base_adjudication_implementation_commit",
        "adjudication_implementation_commit",
        "r7f_authority_receipt_paths",
        "r7f_authority_receipt_sha256",
        "scheduler_account_authority_path",
        "scheduler_account_authority_sha256",
        "probe_submission_receipt_path",
        "probe_submission_receipt_sha256",
        "probe_terminal_receipt_path",
        "probe_terminal_receipt_sha256",
        "probe_job_id",
        "probe_job_name",
        "probe_role",
        "expected_owner",
        "qsub_environment_sha256",
        "array_job_id",
        "array_job_name",
        "array_role",
        "array_task_range",
        "array_task_ids",
        "array_task_count",
        "array_max_concurrency",
        "finalizer_job_id",
        "finalizer_job_name",
        "finalizer_role",
        "scheduler_log_root",
        "scheduler_log_basenames",
        "tracked_submission_entrypoint",
        "tracked_worker_entrypoint",
        "tracked_finalizer_entrypoint",
        "tracked_runner_path",
        "runtime_script_authority",
        "prohibited_actions",
    }
)

ZERO_PROHIBITED_ACTIONS: Final = {
    "qsub_submissions": 0,
    "cloud_requests": 0,
    "dicom_body_reads": 0,
    "npz_body_reads": 0,
    "extraction_executions": 0,
    "echoprime_executions": 0,
    "embedding_generations": 0,
    "preservation_executions": 0,
    "scientific_finalization_executions": 0,
    "model_fitting_count": 0,
    "prediction_generation_count": 0,
    "confirmatory_performance_access_count": 0,
}

COMMON_ACCOUNTING_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_name",
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "base_adjudication_implementation_commit",
        "adjudication_implementation_commit",
        "terminal_authority_receipt_path",
        "terminal_authority_receipt_sha256",
        "r7f_authority_receipt_sha256",
        "continuation_submission_receipt_path",
        "continuation_submission_receipt_sha256",
        "scheduler_account_authority_path",
        "scheduler_account_authority_sha256",
        "probe_terminal_receipt_path",
        "probe_terminal_receipt_sha256",
        "job_kind",
        "job_id",
        "task_id",
        "expected_job_role",
        "expected_job_name",
        "expected_owner",
        "scheduler_log_path",
        "scheduler_log_basename",
        "observed_qacct_job_name",
        "qacct_job_number",
        "qacct_task_number",
        "owner",
        "submission_time",
        "start_time",
        "end_time",
        "wall_seconds",
        "failed",
        "exit_status",
        "queue_name",
        "execution_host",
        "accounting_query_timestamp_utc",
        "creation_timestamp_utc",
        "fixed_qacct_argv",
        "fixed_qacct_argv_sha256",
        "qacct_record_normalization",
        "normalized_qacct_record",
        "normalized_raw_qacct_record_sha256",
        "tracked_submission_entrypoint",
        "tracked_worker_entrypoint",
        "tracked_finalizer_entrypoint",
        "runtime_controller_sha256",
        "runtime_runner_sha256",
        "terminal_classification",
    }
)
ARRAY_ACCOUNTING_RECEIPT_KEYS: Final = (
    COMMON_ACCOUNTING_RECEIPT_KEYS | {"array_job_id"}
)
FINALIZER_ACCOUNTING_RECEIPT_KEYS: Final = (
    COMMON_ACCOUNTING_RECEIPT_KEYS | {"finalizer_job_id"}
)


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


def _require_adjudication_commit(value: str) -> None:
    if (
        not isinstance(value, str)
        or COMMIT_RE.fullmatch(value) is None
        or value in {
            RUNTIME_IMPLEMENTATION_COMMIT,
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
        }
    ):
        _fail("R8U_R7G_ADJUDICATION_COMMIT_INVALID")


def _strict_json_object(payload: bytes, *, code: str) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        _fail(code)

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except R7GAccountingError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7GAccountingError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _read_owner_private_regular(path: Path, *, code: str) -> bytes:
    """Read one stable mode-0600, owner-controlled file without links."""

    descriptor = -1
    try:
        r7.sequential._require_nonsymlink_components(path)
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > MAX_RECEIPT_BYTES
        ):
            _fail(code)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        identity = lambda item: (
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
        if identity(before) != identity(opened):
            _fail(code)
        blocks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail(code)
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except R7GAccountingError:
        raise
    except Exception as exc:
        raise R7GAccountingError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        visible_after = os.lstat(path)
    except OSError as exc:
        raise R7GAccountingError(code) from exc
    if identity(opened) != identity(after) or identity(after) != identity(visible_after):
        _fail(code)
    return b"".join(blocks)


def _fixed_authority_input_paths() -> dict[str, Path]:
    """Return the closed role-to-path map at call time for test patchability."""

    return {
        "capacity": CAPACITY_RECEIPT_PATH,
        "continuation_claim": CONTINUATION_CLAIM_PATH,
        "array_submission": ARRAY_SUBMISSION_PATH,
        "finalizer_submission": FINALIZER_SUBMISSION_PATH,
        "combined_submission": COMBINED_SUBMISSION_PATH,
        "scheduler_account": ACCOUNT_AUTHORITY_PATH,
        "probe_submission": PROBE_SUBMISSION_PATH,
        "probe_terminal": PROBE_TERMINAL_PATH,
    }


def _strict_authority_json_object(payload: bytes) -> dict[str, Any]:
    """Parse an authority object with stable, representation-specific errors."""

    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R7GAccountingError("R8U_R7G_AUTHORITY_UTF8_INVALID") from exc

    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("R8U_R7G_AUTHORITY_DUPLICATE_KEY")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        _fail("R8U_R7G_AUTHORITY_NONFINITE_VALUE")

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except R7GAccountingError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise R7GAccountingError(
            "R8U_R7G_AUTHORITY_JSON_SYNTAX_INVALID"
        ) from exc
    if not isinstance(value, dict):
        _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")
    return value


def _read_hash_pinned_historical_json(
    role: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Read one fixed byte-authenticated producer JSON without reserializing."""

    if (
        FIXED_AUTHORITY_SERIALIZATION_POLICY.get(role)
        != HASH_PINNED_HISTORICAL_PRODUCER_JSON
        or role not in R7F_AUTHORITY_NAMES
    ):
        _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")
    expected_sha256 = EXPECTED_R7F_RECEIPT_SHA256.get(role)
    if (
        not isinstance(expected_sha256, str)
        or SHA_RE.fullmatch(expected_sha256) is None
    ):
        _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")
    path = _fixed_authority_input_paths()[role]
    payload = _read_owner_private_regular(
        path, code="R8U_R7G_AUTHORITY_FILE_INVALID"
    )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        _fail("R8U_R7G_AUTHORITY_HASH_MISMATCH")
    value = _strict_authority_json_object(payload)
    return value, payload, digest


def _read_producer_canonical_json(
    role: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Read one fixed compact producer artifact under its own byte contract."""

    if (
        FIXED_AUTHORITY_SERIALIZATION_POLICY.get(role)
        != PRODUCER_CANONICAL_JSON
        or role not in {"scheduler_account", "probe_submission", "probe_terminal"}
    ):
        _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")
    path = _fixed_authority_input_paths()[role]
    payload = _read_owner_private_regular(
        path, code="R8U_R7G_AUTHORITY_FILE_INVALID"
    )
    value = _strict_authority_json_object(payload)
    if payload != core.canonical_json_bytes(value):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
    return value, payload, hashlib.sha256(payload).hexdigest()


def _read_r7g_compact_json(
    path: Path, *, file_code: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Read a newly produced R7G object and retain compact-byte authority."""

    payload = _read_owner_private_regular(path, code=file_code)
    value = _strict_authority_json_object(payload)
    if payload != core.canonical_json_bytes(value):
        _fail("R8U_R7G_CURRENT_OUTPUT_CANONICAL_BYTES_INVALID")
    return value, payload, hashlib.sha256(payload).hexdigest()


def _validate_private_directory(path: Path) -> None:
    try:
        r7.sequential._require_nonsymlink_components(path)
        info = os.lstat(path)
    except Exception as exc:
        raise R7GAccountingError("R8U_R7G_ACCOUNTING_DIRECTORY_INVALID") from exc
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or mode & 0o777 != 0o700
        or mode & 0o7000 not in {0, stat.S_ISGID}
    ):
        _fail("R8U_R7G_ACCOUNTING_DIRECTORY_INVALID")


def ensure_fixed_accounting_directory() -> None:
    """Create/reuse only the fixed R7G authority and accounting directories."""

    _validate_private_directory(R7G_ROOT.parent)
    for path in (R7G_ROOT, ACCOUNTING_ROOT):
        if os.path.lexists(path):
            _validate_private_directory(path)
            continue
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise R7GAccountingError(
                "R8U_R7G_ACCOUNTING_DIRECTORY_INVALID"
            ) from exc
        _validate_private_directory(path)


def _atomic_write_private_json_no_clobber(
    path: Path, value: Mapping[str, Any], *, collision_code: str,
    publication_code: str,
) -> str:
    body = core.canonical_json_bytes(value)
    temporary = path.parent / f".{path.name}.{ATTEMPT_ID}.partial"
    if os.path.lexists(path) or os.path.lexists(temporary):
        _fail(collision_code)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        temporary_identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size != 0
        ):
            _fail(publication_code)
        view = memoryview(body)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count < 1:
                _fail(publication_code)
            written += count
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            (completed.st_dev, completed.st_ino) != temporary_identity
            or not stat.S_ISREG(completed.st_mode)
            or completed.st_uid != os.geteuid()
            or completed.st_nlink != 1
            or stat.S_IMODE(completed.st_mode) != 0o600
            or completed.st_size != len(body)
        ):
            _fail(publication_code)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except R7GAccountingError:
        raise
    except Exception as exc:
        raise R7GAccountingError(collision_code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_identity is not None and os.path.lexists(temporary):
            try:
                visible = os.lstat(temporary)
                if (
                    (visible.st_dev, visible.st_ino) == temporary_identity
                    and stat.S_ISREG(visible.st_mode)
                    and not stat.S_ISLNK(visible.st_mode)
                    and visible.st_uid == os.geteuid()
                ):
                    os.unlink(temporary)
            except OSError:
                pass
    return hashlib.sha256(body).hexdigest()


def _r7f_common(value: Mapping[str, Any], *, artifact_type: str,
                statuses: frozenset[str]) -> None:
    status = value.get("status")
    expected = dict(
        r7._r8u_r7f_common(
            artifact_type=artifact_type,
            status=str(status),
            implementation_commit=RUNTIME_IMPLEMENTATION_COMMIT,
        )
    )
    if (
        not isinstance(value, Mapping)
        or status not in statuses
        or set(expected) != R7F_COMMON_KEYS
        or any(
            not _exact_typed_equal(value.get(key), item)
            for key, item in expected.items()
        )
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")


def _validate_qsub_evidence(value: object) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {"stdout_bytes", "stdout_sha256", "stderr_bytes", "stderr_sha256", "exit_status"}
        or isinstance(value.get("stdout_bytes"), bool)
        or not isinstance(value.get("stdout_bytes"), int)
        or int(value.get("stdout_bytes", -1)) < 1
        or not _exact_typed_equal(value.get("stderr_bytes"), 0)
        or not _exact_typed_equal(value.get("exit_status"), 0)
        or SHA_RE.fullmatch(str(value.get("stdout_sha256", ""))) is None
        or SHA_RE.fullmatch(str(value.get("stderr_sha256", ""))) is None
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")


def _fixed_control_sha256(path: Path) -> str:
    payload = _read_owner_private_regular(
        path, code="R8U_R7G_AUTHORITY_FILE_INVALID"
    )
    return hashlib.sha256(payload).hexdigest()


def _fixed_probe_qsub_evidence() -> dict[str, Any]:
    evidence: dict[str, bytes] = {}
    try:
        for kind in ("stdout", "stderr", "exit_status"):
            evidence[kind] = scheduler._read_scheduler_evidence(
                PROBE_SCHEDULER_ROOT / f"probe.qsub.{kind}.restricted"
            )
    except Exception as exc:
        raise R7GAccountingError(
            "R8U_R7G_AUTHORITY_FILE_INVALID"
        ) from exc
    if (
        not evidence["stdout"]
        or evidence["stderr"] != b""
        or evidence["exit_status"] != b"0\n"
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
    try:
        captured_job_id = r7._parse_r8u_r7d_probe_qsub_stdout(
            evidence["stdout"]
        )
    except Exception as exc:
        raise R7GAccountingError(
            "R8U_R7G_AUTHORITY_SEMANTIC_INVALID"
        ) from exc
    if captured_job_id != PROBE_JOB_ID:
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
    return {
        "stdout_bytes": len(evidence["stdout"]),
        "stdout_sha256": hashlib.sha256(evidence["stdout"]).hexdigest(),
        "stderr_bytes": 0,
        "stderr_sha256": hashlib.sha256(evidence["stderr"]).hexdigest(),
        "exit_status": 0,
    }


def _validate_capacity_producer_receipt(
    value: Mapping[str, Any], *, plan: Mapping[str, Any] | None,
) -> None:
    """Apply the fixed R7F producer schema and, with a plan, full replay."""

    fixed = {
        "artifact_type": r7d_capacity.R8U_R7F_CAPACITY_ARTIFACT_TYPE,
        "status": r7d_capacity.R8U_R7F_CAPACITY_STATUS_PASS,
        "original_attempt_id": ATTEMPT_ID,
        "original_plan_sha256": PLAN_SHA256,
        "original_scientific_governing_commit": SCIENTIFIC_COMMIT,
        "r7f_runtime_commit": RUNTIME_IMPLEMENTATION_COMMIT,
    }
    if any(not _exact_typed_equal(value.get(key), item) for key, item in fixed.items()):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
    if plan is None:
        return
    baselines = (
        "preserved_old_control_evidence_bytes_baseline",
        "preserved_old_control_evidence_files_baseline",
        "confirmed_partial_artifact_bytes_baseline",
        "confirmed_partial_artifact_files_baseline",
    )
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), int)
        or int(value.get(field, -1)) < 0
        for field in baselines
    ):
        _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")
    try:
        validated = r7d_capacity.validate_fixed_r8u_r7f_tasks17_19_capacity(
            plan,
            value,
            r7f_runtime_commit=RUNTIME_IMPLEMENTATION_COMMIT,
            preserved_old_evidence_bytes=int(value[baselines[0]]),
            preserved_old_evidence_files=int(value[baselines[1]]),
            confirmed_partial_artifact_bytes=int(value[baselines[2]]),
            confirmed_partial_artifact_files=int(value[baselines[3]]),
            raw_capture_root=None,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", ""))
        classified = (
            "R8U_R7G_AUTHORITY_SCHEMA_INVALID"
            if "SCHEMA" in code
            else "R8U_R7G_AUTHORITY_SEMANTIC_INVALID"
        )
        raise R7GAccountingError(classified) from exc
    if not _exact_typed_equal(validated, value):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")


def _validate_fixed_authority_role(
    role: str,
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any] | None,
) -> None:
    """Dispatch only the eight closed historical authority roles."""

    if role == "capacity":
        _validate_capacity_producer_receipt(value, plan=plan)
        return
    r7f_shapes = {
        "continuation_claim": (
            "lvef_c3_r8u_r7f_fixed_continuation_claim_v1",
            frozenset({"AUTHORIZED_FRESH_R7F_CONTINUATION_17_19"}),
        ),
        "array_submission": (
            "lvef_c3_r8u_r7f_array_submission_v1",
            frozenset({"PASS_EXACT_R7F_ARRAY_17_19_QSUB"}),
        ),
        "finalizer_submission": (
            "lvef_c3_r8u_r7f_finalizer_submission_v1",
            frozenset({"PASS_EXACT_R7F_HELD_FINALIZER_QSUB"}),
        ),
        "combined_submission": (
            "lvef_c3_r8u_r7f_fixed_continuation_submission_v1",
            frozenset(
                {
                    "PASS_EXACT_R7F_ARRAY_17_19_AND_HELD_FINALIZER",
                    "BLOCKED_R7F_POST_SUBMISSION_QSTAT_DIAGNOSTIC",
                }
            ),
        ),
        "probe_submission": (
            "lvef_c3_r8u_r7f_context_probe_submission_v1",
            frozenset({"PASS_EXACT_ONE_R7F_CPU_ARRAY_CONTEXT_PROBE_QSUB"}),
        ),
        "probe_terminal": (
            "lvef_c3_r8u_r7f_context_probe_terminal_v1",
            frozenset({"PASS_R7F_CONTINUATION_WORKER_CONTEXT_PROBE"}),
        ),
    }
    if role in r7f_shapes:
        artifact_type, statuses = r7f_shapes[role]
        exact_probe_keys = {
            "probe_submission": PROBE_SUBMISSION_KEYS,
            "probe_terminal": PROBE_TERMINAL_KEYS,
        }
        if role in exact_probe_keys and set(value) != exact_probe_keys[role]:
            _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")
        _r7f_common(value, artifact_type=artifact_type, statuses=statuses)
        return
    if role == "scheduler_account":
        try:
            validated = r7.validate_r8u_r7d_scheduler_account_authority(value)
        except Exception as exc:
            raise R7GAccountingError(
                "R8U_R7G_AUTHORITY_SEMANTIC_INVALID"
            ) from exc
        if not _exact_typed_equal(validated, value):
            _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
        return
    _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")


def _r7f_qsub_argv_sha256(argv: Sequence[str]) -> str:
    """Reproduce the newline-terminated R7F submission digest exactly."""

    return hashlib.sha256(r7._canonical_bytes({"argv": list(argv)})).hexdigest()


def _fixed_authority_paths() -> dict[str, str]:
    return {
        "capacity": str(CAPACITY_RECEIPT_PATH),
        "continuation_claim": str(CONTINUATION_CLAIM_PATH),
        "array_submission": str(ARRAY_SUBMISSION_PATH),
        "finalizer_submission": str(FINALIZER_SUBMISSION_PATH),
        "combined_submission": str(COMBINED_SUBMISSION_PATH),
    }


def _fixed_log_basenames(array_name: str, finalizer_name: str) -> dict[str, str]:
    return {
        **{
            f"array_task_{task_id}": f"{array_name}.o{ARRAY_JOB_ID}.{task_id}"
            for task_id in ARRAY_TASK_IDS
        },
        "finalizer": f"{finalizer_name}.o{FINALIZER_JOB_ID}",
    }


def _validate_terminal_authority_shape(
    value: Mapping[str, Any], *, adjudication_implementation_commit: str,
) -> dict[str, Any]:
    _require_adjudication_commit(adjudication_implementation_commit)
    array_name = r7._r8u_r7d_array_job_name(RUNTIME_IMPLEMENTATION_COMMIT)
    finalizer_name = r7._r8u_r7d_finalizer_job_name(RUNTIME_IMPLEMENTATION_COMMIT)
    probe_name = r7._r8u_r7d_probe_job_name(RUNTIME_IMPLEMENTATION_COMMIT)
    fixed = {
        "schema_name": "lvef_c3_r8u_r7g_r7f_terminal_authority",
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7g_r7f_terminal_authority_v1",
        "status": "PASS_R7F_FIXED_TERMINAL_AUTHORITY_SEALED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "r7f_authority_receipt_paths": _fixed_authority_paths(),
        "r7f_authority_receipt_sha256": dict(EXPECTED_R7F_RECEIPT_SHA256),
        "scheduler_account_authority_path": str(ACCOUNT_AUTHORITY_PATH),
        "probe_submission_receipt_path": str(PROBE_SUBMISSION_PATH),
        "probe_terminal_receipt_path": str(PROBE_TERMINAL_PATH),
        "probe_job_id": PROBE_JOB_ID,
        "probe_job_name": probe_name,
        "probe_role": PROBE_ROLE,
        "array_job_id": ARRAY_JOB_ID,
        "array_job_name": array_name,
        "array_role": ARRAY_ROLE,
        "array_task_range": ARRAY_TASK_RANGE,
        "array_task_ids": list(ARRAY_TASK_IDS),
        "array_task_count": len(ARRAY_TASK_IDS),
        "array_max_concurrency": ARRAY_MAX_CONCURRENCY,
        "finalizer_job_id": FINALIZER_JOB_ID,
        "finalizer_job_name": finalizer_name,
        "finalizer_role": FINALIZER_ROLE,
        "scheduler_log_root": str(SCHEDULER_LOG_ROOT),
        "scheduler_log_basenames": _fixed_log_basenames(array_name, finalizer_name),
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "tracked_runner_path": str(TRACKED_RUNNER_PATH),
        "prohibited_actions": dict(ZERO_PROHIBITED_ACTIONS),
    }
    if not isinstance(value, Mapping) or set(value) != set(TERMINAL_AUTHORITY_KEYS):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_SCHEMA_INVALID")
    if any(not _exact_typed_equal(value.get(key), item) for key, item in fixed.items()):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
    if (
        scheduler.SAFE_ACCOUNT_RE.fullmatch(str(value.get("expected_owner", ""))) is None
        or SHA_RE.fullmatch(str(value.get("qsub_environment_sha256", ""))) is None
        or any(
            SHA_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "scheduler_account_authority_sha256",
                "probe_submission_receipt_sha256",
                "probe_terminal_receipt_sha256",
            )
        )
    ):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
    scripts = value.get("runtime_script_authority")
    expected_script_keys = {
        "controller_sha256",
        "full_sequential_sha256",
        "production_stages_sha256",
        "preservation_sha256",
        "retirement_sha256",
        "finalizer_sha256",
        "runner_sha256",
    }
    if (
        not isinstance(scripts, Mapping)
        or set(scripts) != expected_script_keys
        or any(SHA_RE.fullmatch(str(item)) is None for item in scripts.values())
    ):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
    return dict(value)


def validate_terminal_authority(
    value: Mapping[str, Any], *, adjudication_implementation_commit: str,
) -> dict[str, Any]:
    """Validate a caller's value against the sealed, rederived authority."""

    shaped = _validate_terminal_authority_shape(
        value,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    sealed = load_terminal_authority(
        adjudication_implementation_commit=adjudication_implementation_commit
    ).authority
    if not _exact_typed_equal(shaped, sealed):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
    return dict(sealed)


def _derive_terminal_authority(
    *, adjudication_implementation_commit: str,
    receipts: Mapping[str, Mapping[str, Any]],
    receipt_sha256: Mapping[str, str],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Cross-bind already hash-checked R7F and probe/account authorities."""

    _require_adjudication_commit(adjudication_implementation_commit)
    if set(receipts) != {
        *R7F_AUTHORITY_NAMES,
        "scheduler_account",
        "probe_submission",
        "probe_terminal",
    }:
        _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")
    if set(receipt_sha256) != set(receipts):
        _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")

    capacity = receipts["capacity"]
    _validate_capacity_producer_receipt(capacity, plan=plan)

    account = receipts["scheduler_account"]
    try:
        account = r7.validate_r8u_r7d_scheduler_account_authority(account)
    except Exception as exc:
        raise R7GAccountingError("R8U_R7G_AUTHORITY_SEMANTIC_INVALID") from exc
    owner = str(account.get("expected_scheduler_username", ""))
    qsub_environment_sha256 = str(account.get("qsub_environment_sha256", ""))
    account_sha256 = receipt_sha256["scheduler_account"]

    claim = receipts["continuation_claim"]
    _r7f_common(
        claim,
        artifact_type="lvef_c3_r8u_r7f_fixed_continuation_claim_v1",
        statuses=frozenset({"AUTHORIZED_FRESH_R7F_CONTINUATION_17_19"}),
    )
    script_authority = claim.get("script_authority")
    if (
        claim.get("capacity_receipt_sha256") != receipt_sha256["capacity"]
        or claim.get("scheduler_account_authority_sha256") != account_sha256
        or claim.get("qsub_environment_sha256") != qsub_environment_sha256
        or claim.get("continuation_task_range") != ARRAY_TASK_RANGE
        or claim.get("continuation_task_ids") != list(ARRAY_TASK_IDS)
        or claim.get("continuation_task_count") != len(ARRAY_TASK_IDS)
        or claim.get("continuation_max_concurrency") != ARRAY_MAX_CONCURRENCY
        or claim.get("probe_task_id") != 17
        or claim.get("probe_submission_count") != 1
        or claim.get("scientific_array_submission_count") != 1
        or claim.get("held_finalizer_submission_count") != 1
        or claim.get("total_new_qsub_maximum") != 3
        or claim.get("automatic_retry_authorized") is not False
        or claim.get("whole_stage_retry_authorized") is not False
        or claim.get("fourth_submission_reachable") is not False
        or not isinstance(script_authority, Mapping)
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    probe_submission = receipts["probe_submission"]
    _r7f_common(
        probe_submission,
        artifact_type="lvef_c3_r8u_r7f_context_probe_submission_v1",
        statuses=frozenset({"PASS_EXACT_ONE_R7F_CPU_ARRAY_CONTEXT_PROBE_QSUB"}),
    )
    probe_name = str(probe_submission.get("probe_job_name", ""))
    expected_probe_name = r7._r8u_r7d_probe_job_name(RUNTIME_IMPLEMENTATION_COMMIT)
    expected_probe_argv = _r7f_qsub_argv_sha256(
        r7._r8u_r7d_probe_command(RUNTIME_IMPLEMENTATION_COMMIT)
    )
    _validate_qsub_evidence(probe_submission.get("probe_qsub_evidence"))
    expected_probe_evidence = _fixed_probe_qsub_evidence()
    expected_probe_authority_sha256 = _fixed_control_sha256(
        PROBE_AUTHORITY_PATH
    )
    if (
        probe_submission.get("scheduler_account_authority_sha256") != account_sha256
        or probe_submission.get("r7f_continuation_claim_sha256")
        != receipt_sha256["continuation_claim"]
        or probe_submission.get("r7f_capacity_receipt_sha256")
        != receipt_sha256["capacity"]
        or probe_submission.get("probe_job_id") != PROBE_JOB_ID
        or probe_name != expected_probe_name
        or probe_submission.get("worker_role") != PROBE_ROLE
        or probe_submission.get("probe_qsub_argv_sha256") != expected_probe_argv
        or probe_submission.get("qsub_environment_sha256") != qsub_environment_sha256
        or not _exact_typed_equal(probe_submission.get("array_task_id"), 17)
        or not _exact_typed_equal(probe_submission.get("array_task_count"), 1)
        or not _exact_typed_equal(
            probe_submission.get("array_max_concurrency"), 1
        )
        or not _exact_typed_equal(
            probe_submission.get("scheduler_submission_count"), 1
        )
        or not _exact_typed_equal(
            probe_submission.get("scheduler_submission_maximum"), 3
        )
        or probe_submission.get("scientific_execution_authorized") is not False
        or probe_submission.get("probe_authority_sha256")
        != expected_probe_authority_sha256
        or not _exact_typed_equal(
            probe_submission.get("probe_qsub_evidence"),
            expected_probe_evidence,
        )
        or any(
            not _exact_typed_equal(probe_submission.get(field), 0)
            for field in (
                "cloud_requests",
                "dicom_body_reads",
                "npz_body_reads",
                "gpu_executions",
            )
        )
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")
    probe_job_id = str(probe_submission["probe_job_id"])

    probe_terminal = receipts["probe_terminal"]
    _r7f_common(
        probe_terminal,
        artifact_type="lvef_c3_r8u_r7f_context_probe_terminal_v1",
        statuses=frozenset({"PASS_R7F_CONTINUATION_WORKER_CONTEXT_PROBE"}),
    )
    expected_worker_receipt_sha256 = _fixed_control_sha256(
        PROBE_WORKER_RECEIPT_PATH
    )
    expected_probe_accounting_sha256 = _fixed_control_sha256(
        PROBE_ACCOUNTING_PATH
    )
    if (
        probe_terminal.get("probe_submission_receipt_sha256")
        != receipt_sha256["probe_submission"]
        or probe_terminal.get("worker_context_receipt_sha256")
        != expected_worker_receipt_sha256
        or probe_terminal.get("accounting_receipt_sha256")
        != expected_probe_accounting_sha256
        or probe_terminal.get("qstat_classification")
        not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS
        or probe_terminal.get("controlling_worker_identity") != "PASS"
        or not _exact_typed_equal(probe_terminal.get("failed"), 0)
        or not _exact_typed_equal(probe_terminal.get("exit_status"), 0)
        or not _exact_typed_equal(probe_terminal.get("task_id"), 17)
        or any(
            not _exact_typed_equal(probe_terminal.get(field), 0)
            for field in (
                "scientific_artifacts_created",
                "cloud_requests",
                "dicom_body_reads",
                "npz_body_reads",
                "gpu_executions",
            )
        )
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    array = receipts["array_submission"]
    _r7f_common(
        array,
        artifact_type="lvef_c3_r8u_r7f_array_submission_v1",
        statuses=frozenset({"PASS_EXACT_R7F_ARRAY_17_19_QSUB"}),
    )
    array_name = str(array.get("array_job_name", ""))
    expected_array_name = r7._r8u_r7d_array_job_name(RUNTIME_IMPLEMENTATION_COMMIT)
    expected_array_argv = _r7f_qsub_argv_sha256(
        r7._r8u_r7d_array_command(RUNTIME_IMPLEMENTATION_COMMIT)
    )
    _validate_qsub_evidence(array.get("array_qsub_evidence"))
    if (
        array.get("capacity_receipt_sha256") != receipt_sha256["capacity"]
        or array.get("continuation_claim_sha256")
        != receipt_sha256["continuation_claim"]
        or array.get("probe_terminal_receipt_sha256")
        != receipt_sha256["probe_terminal"]
        or array.get("scheduler_account_authority_sha256") != account_sha256
        or array.get("array_job_id") != ARRAY_JOB_ID
        or array_name != expected_array_name
        or array.get("array_worker_role") != ARRAY_ROLE
        or array.get("array_qsub_argv_sha256") != expected_array_argv
        or array.get("qsub_environment_sha256") != qsub_environment_sha256
        or array.get("array_task_range") != ARRAY_TASK_RANGE
        or array.get("array_task_ids") != list(ARRAY_TASK_IDS)
        or array.get("array_task_count") != len(ARRAY_TASK_IDS)
        or array.get("array_max_concurrency") != ARRAY_MAX_CONCURRENCY
        or array.get("scheduler_submission_count") != 1
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    finalizer = receipts["finalizer_submission"]
    _r7f_common(
        finalizer,
        artifact_type="lvef_c3_r8u_r7f_finalizer_submission_v1",
        statuses=frozenset({"PASS_EXACT_R7F_HELD_FINALIZER_QSUB"}),
    )
    finalizer_name = str(finalizer.get("finalizer_job_name", ""))
    expected_finalizer_name = r7._r8u_r7d_finalizer_job_name(
        RUNTIME_IMPLEMENTATION_COMMIT
    )
    expected_finalizer_argv = _r7f_qsub_argv_sha256(
        r7._r8u_r7d_finalizer_command(
            RUNTIME_IMPLEMENTATION_COMMIT, ARRAY_JOB_ID
        )
    )
    _validate_qsub_evidence(finalizer.get("finalizer_qsub_evidence"))
    if (
        finalizer.get("capacity_receipt_sha256") != receipt_sha256["capacity"]
        or finalizer.get("continuation_claim_sha256")
        != receipt_sha256["continuation_claim"]
        or finalizer.get("probe_terminal_receipt_sha256")
        != receipt_sha256["probe_terminal"]
        or finalizer.get("scheduler_account_authority_sha256") != account_sha256
        or finalizer.get("array_submission_receipt_sha256")
        != receipt_sha256["array_submission"]
        or finalizer.get("array_job_id") != ARRAY_JOB_ID
        or finalizer.get("finalizer_job_id") != FINALIZER_JOB_ID
        or finalizer_name != expected_finalizer_name
        or finalizer.get("finalizer_worker_role") != FINALIZER_ROLE
        or finalizer.get("finalizer_qsub_argv_sha256") != expected_finalizer_argv
        or finalizer.get("qsub_environment_sha256") != qsub_environment_sha256
        or finalizer.get("hold_jid") != ARRAY_JOB_ID
        or finalizer.get("finalizer_held_on_array") is not True
        or finalizer.get("scheduler_submission_count") != 1
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    combined = receipts["combined_submission"]
    _r7f_common(
        combined,
        artifact_type="lvef_c3_r8u_r7f_fixed_continuation_submission_v1",
        statuses=frozenset(
            {
                "PASS_EXACT_R7F_ARRAY_17_19_AND_HELD_FINALIZER",
                "BLOCKED_R7F_POST_SUBMISSION_QSTAT_DIAGNOSTIC",
            }
        ),
    )
    if (
        combined.get("capacity_receipt_sha256") != receipt_sha256["capacity"]
        or combined.get("probe_terminal_receipt_sha256")
        != receipt_sha256["probe_terminal"]
        or combined.get("continuation_claim_sha256")
        != receipt_sha256["continuation_claim"]
        or combined.get("array_submission_receipt_sha256")
        != receipt_sha256["array_submission"]
        or combined.get("finalizer_submission_receipt_sha256")
        != receipt_sha256["finalizer_submission"]
        or combined.get("array_job_id") != ARRAY_JOB_ID
        or combined.get("array_job_name") != array_name
        or combined.get("array_worker_role") != ARRAY_ROLE
        or combined.get("finalizer_job_id") != FINALIZER_JOB_ID
        or combined.get("finalizer_job_name") != finalizer_name
        or combined.get("finalizer_worker_role") != FINALIZER_ROLE
        or combined.get("qsub_environment_sha256") != qsub_environment_sha256
        or combined.get("array_task_range") != ARRAY_TASK_RANGE
        or combined.get("array_task_ids") != list(ARRAY_TASK_IDS)
        or combined.get("array_task_count") != len(ARRAY_TASK_IDS)
        or combined.get("array_max_concurrency") != ARRAY_MAX_CONCURRENCY
        or combined.get("finalizer_held_on_array") is not True
        or combined.get("scheduler_submission_count") != 2
        or combined.get("total_new_qsub_submissions") != 3
        or combined.get("scheduler_submission_maximum") != 3
        or combined.get("whole_stage_retry_authorized") is not False
        or combined.get("fourth_submission_reachable") is not False
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    if (
        scheduler.SAFE_ACCOUNT_RE.fullmatch(owner) is None
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or account.get("authorized_worker_roles")
        != list(r7.R8U_R7D_WORKER_ROLES)
        or account.get("runner_sha256") != script_authority.get("runner_sha256")
    ):
        _fail("R8U_R7G_AUTHORITY_SEMANTIC_INVALID")

    authority = {
        "schema_name": "lvef_c3_r8u_r7g_r7f_terminal_authority",
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7g_r7f_terminal_authority_v1",
        "status": "PASS_R7F_FIXED_TERMINAL_AUTHORITY_SEALED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "r7f_authority_receipt_paths": _fixed_authority_paths(),
        "r7f_authority_receipt_sha256": dict(EXPECTED_R7F_RECEIPT_SHA256),
        "scheduler_account_authority_path": str(ACCOUNT_AUTHORITY_PATH),
        "scheduler_account_authority_sha256": account_sha256,
        "probe_submission_receipt_path": str(PROBE_SUBMISSION_PATH),
        "probe_submission_receipt_sha256": receipt_sha256["probe_submission"],
        "probe_terminal_receipt_path": str(PROBE_TERMINAL_PATH),
        "probe_terminal_receipt_sha256": receipt_sha256["probe_terminal"],
        "probe_job_id": probe_job_id,
        "probe_job_name": probe_name,
        "probe_role": PROBE_ROLE,
        "expected_owner": owner,
        "qsub_environment_sha256": qsub_environment_sha256,
        "array_job_id": ARRAY_JOB_ID,
        "array_job_name": array_name,
        "array_role": ARRAY_ROLE,
        "array_task_range": ARRAY_TASK_RANGE,
        "array_task_ids": list(ARRAY_TASK_IDS),
        "array_task_count": len(ARRAY_TASK_IDS),
        "array_max_concurrency": ARRAY_MAX_CONCURRENCY,
        "finalizer_job_id": FINALIZER_JOB_ID,
        "finalizer_job_name": finalizer_name,
        "finalizer_role": FINALIZER_ROLE,
        "scheduler_log_root": str(SCHEDULER_LOG_ROOT),
        "scheduler_log_basenames": _fixed_log_basenames(array_name, finalizer_name),
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "tracked_runner_path": str(TRACKED_RUNNER_PATH),
        "runtime_script_authority": dict(script_authority),
        "prohibited_actions": dict(ZERO_PROHIBITED_ACTIONS),
    }
    return _validate_terminal_authority_shape(
        authority,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )


def _load_and_derive_fixed_terminal_authority(
    *,
    adjudication_implementation_commit: str,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if set(FIXED_AUTHORITY_SERIALIZATION_POLICY) != {
        *R7F_AUTHORITY_NAMES,
        "scheduler_account",
        "probe_submission",
        "probe_terminal",
    }:
        _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")
    values: dict[str, Mapping[str, Any]] = {}
    digests: dict[str, str] = {}
    for name, policy in FIXED_AUTHORITY_SERIALIZATION_POLICY.items():
        if policy == HASH_PINNED_HISTORICAL_PRODUCER_JSON:
            value, _payload, digest = _read_hash_pinned_historical_json(name)
        elif policy == PRODUCER_CANONICAL_JSON:
            value, _payload, digest = _read_producer_canonical_json(name)
        else:
            _fail("R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID")
        _validate_fixed_authority_role(name, value, plan=plan)
        values[name] = value
        digests[name] = digest
    return _derive_terminal_authority(
        adjudication_implementation_commit=adjudication_implementation_commit,
        receipts=values,
        receipt_sha256=digests,
        plan=plan,
    )


def preflight_fixed_terminal_authority(
    *,
    plan: Mapping[str, Any],
    adjudication_implementation_commit: str,
) -> dict[str, Any]:
    """Purely read and validate all eight inputs; create/query nothing."""

    if not isinstance(plan, Mapping):
        _fail("R8U_R7G_AUTHORITY_SCHEMA_INVALID")
    return _load_and_derive_fixed_terminal_authority(
        adjudication_implementation_commit=adjudication_implementation_commit,
        plan=plan,
    )


def _load_terminal_authority_receipt(
    *, adjudication_implementation_commit: str,
) -> tuple[dict[str, Any], str]:
    value, _payload, digest = _read_r7g_compact_json(
        TERMINAL_AUTHORITY_PATH,
        file_code="R8U_R7G_TERMINAL_AUTHORITY_FILE_INVALID",
    )
    validated = _validate_terminal_authority_shape(
        value,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    return validated, digest


def load_terminal_authority(
    *, adjudication_implementation_commit: str,
) -> TerminalAuthorityResult:
    """Reopen the sole existing terminal authority and rederive its inputs."""

    expected = _load_and_derive_fixed_terminal_authority(
        adjudication_implementation_commit=adjudication_implementation_commit
    )
    value, digest = _load_terminal_authority_receipt(
        adjudication_implementation_commit=adjudication_implementation_commit
    )
    if not _exact_typed_equal(value, expected):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
    return TerminalAuthorityResult(value, digest, False)


def ensure_terminal_authority(
    *,
    plan: Mapping[str, Any],
    adjudication_implementation_commit: str,
) -> TerminalAuthorityResult:
    """Create or reuse the one fixed, hash-derived R7F terminal authority."""

    _require_adjudication_commit(adjudication_implementation_commit)
    expected = _load_and_derive_fixed_terminal_authority(
        adjudication_implementation_commit=adjudication_implementation_commit,
        plan=plan,
    )
    ensure_fixed_accounting_directory()
    if os.path.lexists(TERMINAL_AUTHORITY_PATH):
        value, digest = _load_terminal_authority_receipt(
            adjudication_implementation_commit=adjudication_implementation_commit
        )
        if not _exact_typed_equal(value, expected):
            _fail("R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID")
        return TerminalAuthorityResult(value, digest, False)
    digest = _atomic_write_private_json_no_clobber(
        TERMINAL_AUTHORITY_PATH,
        expected,
        collision_code="R8U_R7G_TERMINAL_AUTHORITY_COLLISION",
        publication_code="R8U_R7G_TERMINAL_AUTHORITY_PUBLICATION_INVALID",
    )
    value, reopened_digest = _load_terminal_authority_receipt(
        adjudication_implementation_commit=adjudication_implementation_commit
    )
    if digest != reopened_digest or not _exact_typed_equal(value, expected):
        _fail("R8U_R7G_TERMINAL_AUTHORITY_REOPEN_INVALID")
    return TerminalAuthorityResult(value, digest, True)


def _specs_from_authority(
    terminal_authority: Mapping[str, Any],
) -> tuple[FixedAccountingSpec, ...]:
    owner = str(terminal_authority["expected_owner"])
    array_name = str(terminal_authority["array_job_name"])
    finalizer_name = str(terminal_authority["finalizer_job_name"])
    basenames = terminal_authority["scheduler_log_basenames"]
    log_root = Path(str(terminal_authority["scheduler_log_root"]))
    task_specs = tuple(
        FixedAccountingSpec(
            job_kind="ARRAY_TASK",
            job_id=ARRAY_JOB_ID,
            task_id=task_id,
            expected_job_role=ARRAY_ROLE,
            expected_job_name=array_name,
            expected_owner=owner,
            receipt_path=(
                ACCOUNTING_ROOT
                / f"array_{ARRAY_JOB_ID}_task_{task_id}_accounting.restricted.json"
            ),
            scheduler_log_path=log_root / str(basenames[f"array_task_{task_id}"]),
            scheduler_log_basename=str(basenames[f"array_task_{task_id}"]),
        )
        for task_id in ARRAY_TASK_IDS
    )
    finalizer_spec = FixedAccountingSpec(
        job_kind="NON_ARRAY_FINALIZER",
        job_id=FINALIZER_JOB_ID,
        task_id=None,
        expected_job_role=FINALIZER_ROLE,
        expected_job_name=finalizer_name,
        expected_owner=owner,
        receipt_path=(
            ACCOUNTING_ROOT
            / f"finalizer_{FINALIZER_JOB_ID}_accounting.restricted.json"
        ),
        scheduler_log_path=log_root / str(basenames["finalizer"]),
        scheduler_log_basename=str(basenames["finalizer"]),
    )
    return (*task_specs, finalizer_spec)


def fixed_accounting_specs(
    terminal_authority: Mapping[str, Any],
) -> tuple[FixedAccountingSpec, ...]:
    """Return exactly the three R7F tasks and fixed R7F finalizer."""

    commit = str(terminal_authority.get("adjudication_implementation_commit", ""))
    validated = validate_terminal_authority(
        terminal_authority,
        adjudication_implementation_commit=commit,
    )
    return _specs_from_authority(validated)


def _require_fixed_spec(
    spec: FixedAccountingSpec, terminal_authority: Mapping[str, Any]
) -> None:
    if spec not in fixed_accounting_specs(terminal_authority):
        _fail("R8U_R7G_ACCOUNTING_SCOPE_INVALID")


def normalize_qacct_record(record: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(record, Mapping) or not record:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    normalized: dict[str, str] = {}
    for key, value in record.items():
        if (
            not isinstance(key, str)
            or QACCT_KEY_RE.fullmatch(key) is None
            or not isinstance(value, str)
            or any(ord(character) < 32 and character != "\t" for character in value)
            or "\x7f" in value
        ):
            _fail("R8U_R7G_QACCT_RECORD_INVALID")
        normalized[key] = re.sub(r"[ \t]+", " ", value.strip(" \t"))
    return dict(sorted(normalized.items()))


def normalized_qacct_record_sha256(record: Mapping[str, str]) -> str:
    return core.canonical_json_sha256(
        {
            "normalization": QACCT_RECORD_NORMALIZATION,
            "record": normalize_qacct_record(record),
        }
    )


def parse_qacct_records(payload: bytes) -> tuple[dict[str, str], ...]:
    """Strictly parse bounded qacct records; never discard duplicate fields."""

    if not isinstance(payload, bytes) or len(payload) > MAX_QACCT_BYTES:
        _fail("R8U_R7G_QACCT_OUTPUT_INVALID")
    try:
        decoded = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R7GAccountingError("R8U_R7G_QACCT_OUTPUT_INVALID") from exc
    if "\x00" in decoded:
        _fail("R8U_R7G_QACCT_OUTPUT_INVALID")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in decoded.splitlines():
        line = raw_line.strip(" \t")
        if not line:
            continue
        if re.fullmatch(r"={8,}", line) is not None:
            if current:
                records.append(normalize_qacct_record(current))
                current = {}
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)[ \t]+(.*)", line)
        if match is None or match.group(1) in current:
            _fail("R8U_R7G_QACCT_OUTPUT_INVALID")
        raw_value = match.group(2)
        if any(ord(character) < 32 and character != "\t" for character in raw_value):
            _fail("R8U_R7G_QACCT_OUTPUT_INVALID")
        current[match.group(1)] = raw_value
    if current:
        records.append(normalize_qacct_record(current))
    return tuple(records)


def _parse_qacct_time(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
    except (TypeError, ValueError) as exc:
        raise R7GAccountingError("R8U_R7G_QACCT_RECORD_INVALID") from exc


def _parse_failed(value: str) -> int:
    match = FAILED_RE.fullmatch(value)
    if match is None:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    return int(match.group(1))


def _parse_nonnegative_integer(value: str) -> int:
    if NONNEGATIVE_INTEGER_RE.fullmatch(value) is None:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    return int(value)


def _parse_wall_seconds(value: str) -> int:
    if NONNEGATIVE_DECIMAL_RE.fullmatch(value) is None:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise R7GAccountingError("R8U_R7G_QACCT_RECORD_INVALID") from exc
    if not parsed.is_finite() or parsed < 0:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    return int(parsed)


def _optional_qacct_field(record: Mapping[str, str], key: str) -> str | None:
    value = record.get(key)
    if value is None or value in {"", "NONE", "undefined"}:
        return None
    return value


def project_fixed_qacct_record(
    spec: FixedAccountingSpec, record: Mapping[str, str],
) -> dict[str, Any]:
    normalized = normalize_qacct_record(record)
    required = {
        "jobnumber",
        "taskid",
        "jobname",
        "owner",
        "qsub_time",
        "start_time",
        "end_time",
        "failed",
        "exit_status",
        "ru_wallclock",
    }
    if not required <= set(normalized):
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    if (
        normalized["jobnumber"] != spec.job_id
        or normalized["jobname"] != spec.expected_job_name
        or normalized["owner"] != spec.expected_owner
    ):
        _fail("R8U_R7G_QACCT_IDENTITY_INVALID")
    if spec.task_id is None:
        if normalized["taskid"] not in {"", "NONE", "undefined"}:
            _fail("R8U_R7G_QACCT_IDENTITY_INVALID")
    elif (
        spec.task_id not in ARRAY_TASK_IDS
        or normalized["taskid"] != str(spec.task_id)
    ):
        _fail("R8U_R7G_QACCT_IDENTITY_INVALID")
    submission = _parse_qacct_time(normalized["qsub_time"])
    start = _parse_qacct_time(normalized["start_time"])
    end = _parse_qacct_time(normalized["end_time"])
    if not submission <= start <= end:
        _fail("R8U_R7G_QACCT_RECORD_INVALID")
    failed = _parse_failed(normalized["failed"])
    exit_status = _parse_nonnegative_integer(normalized["exit_status"])
    wall_seconds = _parse_wall_seconds(normalized["ru_wallclock"])
    return {
        "observed_qacct_job_name": normalized["jobname"],
        "qacct_job_number": normalized["jobnumber"],
        "qacct_task_number": normalized["taskid"],
        "owner": normalized["owner"],
        "submission_time": normalized["qsub_time"],
        "start_time": normalized["start_time"],
        "end_time": normalized["end_time"],
        "wall_seconds": wall_seconds,
        "failed": failed,
        "exit_status": exit_status,
        "queue_name": _optional_qacct_field(normalized, "qname"),
        "execution_host": _optional_qacct_field(normalized, "hostname"),
        "terminal_classification": (
            "PASS" if failed == 0 and exit_status == 0 else "FAIL"
        ),
    }


def _validate_qacct_tool() -> None:
    if r7.QACCT_PATH != QACCT_PATH:
        _fail("R8U_R7G_QACCT_TOOL_INVALID")
    try:
        r7._validate_qacct_tool()
    except Exception as exc:
        raise R7GAccountingError("R8U_R7G_QACCT_TOOL_INVALID") from exc


def query_fixed_qacct_record(
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    tool_validator: Callable[[], None] = _validate_qacct_tool,
) -> dict[str, str]:
    """Perform exactly one immutable qacct invocation, without retry/polling."""

    _require_fixed_spec(spec, terminal_authority)
    if (
        not isinstance(environment, Mapping)
        or not environment
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or any(character in key + value for character in ("\x00", "\n", "\r"))
            for key, value in environment.items()
        )
    ):
        _fail("R8U_R7G_QACCT_ENVIRONMENT_INVALID")
    tool_validator()
    try:
        completed = runner(
            list(spec.qacct_argv()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=dict(environment),
            timeout=60,
        )
        stdout = bytes(completed.stdout)
        stderr = bytes(completed.stderr)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        raise R7GAccountingError("R8U_R7G_ACCOUNTING_NOT_AVAILABLE") from exc
    if completed.returncode != 0 or stderr or len(stdout) > MAX_QACCT_BYTES:
        _fail("R8U_R7G_ACCOUNTING_NOT_AVAILABLE")
    records = parse_qacct_records(stdout)
    if len(records) != 1:
        _fail("R8U_R7G_ACCOUNTING_NOT_AVAILABLE")
    project_fixed_qacct_record(spec, records[0])
    return records[0]


def _format_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("R8U_R7G_RECEIPT_TIMESTAMP_INVALID")
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("R8U_R7G_RECEIPT_TIMESTAMP_INVALID")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise R7GAccountingError("R8U_R7G_RECEIPT_TIMESTAMP_INVALID") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _receipt_schema(
    spec: FixedAccountingSpec,
) -> tuple[str, str, frozenset[str]]:
    if spec.job_kind == "ARRAY_TASK" and spec.task_id in ARRAY_TASK_IDS:
        return (
            "lvef_c3_r8u_r7g_array_task_accounting",
            "lvef_c3_r8u_r7g_array_task_accounting_v1",
            ARRAY_ACCOUNTING_RECEIPT_KEYS,
        )
    if spec.job_kind == "NON_ARRAY_FINALIZER" and spec.task_id is None:
        return (
            "lvef_c3_r8u_r7g_finalizer_accounting",
            "lvef_c3_r8u_r7g_finalizer_accounting_v1",
            FINALIZER_ACCOUNTING_RECEIPT_KEYS,
        )
    _fail("R8U_R7G_ACCOUNTING_SCOPE_INVALID")


def _terminal_authority_sha256(terminal_authority: Mapping[str, Any]) -> str:
    return hashlib.sha256(core.canonical_json_bytes(terminal_authority)).hexdigest()


def build_accounting_receipt(
    spec: FixedAccountingSpec,
    record: Mapping[str, str],
    *,
    terminal_authority: Mapping[str, Any],
    accounting_query_timestamp: datetime,
    creation_timestamp: datetime,
) -> dict[str, Any]:
    _require_fixed_spec(spec, terminal_authority)
    normalized = normalize_qacct_record(record)
    projection = project_fixed_qacct_record(spec, normalized)
    query_timestamp = _format_utc_timestamp(accounting_query_timestamp)
    created_timestamp = _format_utc_timestamp(creation_timestamp)
    if _parse_utc_timestamp(created_timestamp) < _parse_utc_timestamp(query_timestamp):
        _fail("R8U_R7G_RECEIPT_TIMESTAMP_INVALID")
    schema_name, artifact_type, _keys = _receipt_schema(spec)
    argv = list(spec.qacct_argv())
    scripts = terminal_authority["runtime_script_authority"]
    receipt: dict[str, Any] = {
        "schema_name": schema_name,
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": "PASS_CANONICAL_FIXED_QACCT_RECORD_CAPTURED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": terminal_authority[
            "adjudication_implementation_commit"
        ],
        "terminal_authority_receipt_path": str(TERMINAL_AUTHORITY_PATH),
        "terminal_authority_receipt_sha256": _terminal_authority_sha256(
            terminal_authority
        ),
        "r7f_authority_receipt_sha256": dict(EXPECTED_R7F_RECEIPT_SHA256),
        "continuation_submission_receipt_path": str(COMBINED_SUBMISSION_PATH),
        "continuation_submission_receipt_sha256": EXPECTED_R7F_RECEIPT_SHA256[
            "combined_submission"
        ],
        "scheduler_account_authority_path": str(ACCOUNT_AUTHORITY_PATH),
        "scheduler_account_authority_sha256": terminal_authority[
            "scheduler_account_authority_sha256"
        ],
        "probe_terminal_receipt_path": str(PROBE_TERMINAL_PATH),
        "probe_terminal_receipt_sha256": terminal_authority[
            "probe_terminal_receipt_sha256"
        ],
        "job_kind": spec.job_kind,
        "job_id": spec.job_id,
        "task_id": spec.task_id,
        "expected_job_role": spec.expected_job_role,
        "expected_job_name": spec.expected_job_name,
        "expected_owner": spec.expected_owner,
        "scheduler_log_path": str(spec.scheduler_log_path),
        "scheduler_log_basename": spec.scheduler_log_basename,
        **projection,
        "accounting_query_timestamp_utc": query_timestamp,
        "creation_timestamp_utc": created_timestamp,
        "fixed_qacct_argv": argv,
        "fixed_qacct_argv_sha256": core.canonical_json_sha256({"argv": argv}),
        "qacct_record_normalization": QACCT_RECORD_NORMALIZATION,
        "normalized_qacct_record": normalized,
        "normalized_raw_qacct_record_sha256": normalized_qacct_record_sha256(
            normalized
        ),
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "runtime_controller_sha256": scripts["controller_sha256"],
        "runtime_runner_sha256": scripts["runner_sha256"],
    }
    if spec.task_id is None:
        receipt["finalizer_job_id"] = spec.job_id
    else:
        receipt["array_job_id"] = spec.job_id
    return validate_accounting_receipt(
        receipt, spec, terminal_authority=terminal_authority
    )


def validate_accounting_receipt(
    value: Mapping[str, Any],
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Fully rederive fixed bindings and the normalized qacct projection."""

    _require_fixed_spec(spec, terminal_authority)
    schema_name, artifact_type, keys = _receipt_schema(spec)
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_SCHEMA_INVALID")
    scripts = terminal_authority["runtime_script_authority"]
    fixed = {
        "schema_name": schema_name,
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": "PASS_CANONICAL_FIXED_QACCT_RECORD_CAPTURED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": terminal_authority[
            "adjudication_implementation_commit"
        ],
        "terminal_authority_receipt_path": str(TERMINAL_AUTHORITY_PATH),
        "terminal_authority_receipt_sha256": _terminal_authority_sha256(
            terminal_authority
        ),
        "r7f_authority_receipt_sha256": dict(EXPECTED_R7F_RECEIPT_SHA256),
        "continuation_submission_receipt_path": str(COMBINED_SUBMISSION_PATH),
        "continuation_submission_receipt_sha256": EXPECTED_R7F_RECEIPT_SHA256[
            "combined_submission"
        ],
        "scheduler_account_authority_path": str(ACCOUNT_AUTHORITY_PATH),
        "scheduler_account_authority_sha256": terminal_authority[
            "scheduler_account_authority_sha256"
        ],
        "probe_terminal_receipt_path": str(PROBE_TERMINAL_PATH),
        "probe_terminal_receipt_sha256": terminal_authority[
            "probe_terminal_receipt_sha256"
        ],
        "job_kind": spec.job_kind,
        "job_id": spec.job_id,
        "task_id": spec.task_id,
        "expected_job_role": spec.expected_job_role,
        "expected_job_name": spec.expected_job_name,
        "expected_owner": spec.expected_owner,
        "scheduler_log_path": str(spec.scheduler_log_path),
        "scheduler_log_basename": spec.scheduler_log_basename,
        "fixed_qacct_argv": list(spec.qacct_argv()),
        "fixed_qacct_argv_sha256": core.canonical_json_sha256(
            {"argv": list(spec.qacct_argv())}
        ),
        "qacct_record_normalization": QACCT_RECORD_NORMALIZATION,
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "runtime_controller_sha256": scripts["controller_sha256"],
        "runtime_runner_sha256": scripts["runner_sha256"],
    }
    if any(not _exact_typed_equal(value.get(key), item) for key, item in fixed.items()):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    if spec.task_id is None:
        if value.get("finalizer_job_id") != spec.job_id:
            _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    elif value.get("array_job_id") != spec.job_id:
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    query_timestamp = _parse_utc_timestamp(value.get("accounting_query_timestamp_utc"))
    creation_timestamp = _parse_utc_timestamp(value.get("creation_timestamp_utc"))
    if creation_timestamp < query_timestamp:
        _fail("R8U_R7G_RECEIPT_TIMESTAMP_INVALID")
    record = value.get("normalized_qacct_record")
    if not isinstance(record, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in record.items()
    ):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_SCHEMA_INVALID")
    normalized = normalize_qacct_record(record)
    if not _exact_typed_equal(dict(record), normalized):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    projection = project_fixed_qacct_record(spec, normalized)
    if any(not _exact_typed_equal(value.get(key), item) for key, item in projection.items()):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    digest = normalized_qacct_record_sha256(normalized)
    if (
        value.get("normalized_raw_qacct_record_sha256") != digest
        or SHA_RE.fullmatch(str(value.get("normalized_raw_qacct_record_sha256", "")))
        is None
    ):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_BINDING_INVALID")
    return dict(value)


def _load_accounting_receipt_at_path(
    path: Path,
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    value, _payload, digest = _read_r7g_compact_json(
        path, file_code="R8U_R7G_ACCOUNTING_RECEIPT_FILE_INVALID"
    )
    validated = validate_accounting_receipt(
        value, spec, terminal_authority=terminal_authority
    )
    return validated, digest


def load_accounting_receipt(
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    _require_fixed_spec(spec, terminal_authority)
    return _load_accounting_receipt_at_path(
        spec.receipt_path, spec, terminal_authority=terminal_authority
    )


def _publish_accounting_receipt_at_path(
    path: Path,
    value: Mapping[str, Any],
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    validate_accounting_receipt(value, spec, terminal_authority=terminal_authority)
    _validate_private_directory(path.parent)
    if os.path.lexists(path):
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_COLLISION")
    digest = _atomic_write_private_json_no_clobber(
        path,
        value,
        collision_code="R8U_R7G_ACCOUNTING_RECEIPT_COLLISION",
        publication_code="R8U_R7G_ACCOUNTING_RECEIPT_PUBLICATION_INVALID",
    )
    reopened, reopened_digest = _load_accounting_receipt_at_path(
        path, spec, terminal_authority=terminal_authority
    )
    if digest != reopened_digest:
        _fail("R8U_R7G_ACCOUNTING_RECEIPT_REOPEN_INVALID")
    return reopened, reopened_digest


def publish_accounting_receipt(
    value: Mapping[str, Any],
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    _require_fixed_spec(spec, terminal_authority)
    return _publish_accounting_receipt_at_path(
        spec.receipt_path, value, spec, terminal_authority=terminal_authority
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _reuse_or_query_accounting_at_path(
    spec: FixedAccountingSpec,
    path: Path,
    *,
    terminal_authority: Mapping[str, Any],
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    tool_validator: Callable[[], None],
    clock: Callable[[], datetime],
) -> AccountingReceiptResult:
    _require_fixed_spec(spec, terminal_authority)
    if os.path.lexists(path):
        value, digest = _load_accounting_receipt_at_path(
            path, spec, terminal_authority=terminal_authority
        )
        return AccountingReceiptResult(value, digest, False, 0)
    query_timestamp = clock()
    record = query_fixed_qacct_record(
        spec,
        terminal_authority=terminal_authority,
        environment=environment,
        runner=runner,
        tool_validator=tool_validator,
    )
    creation_timestamp = clock()
    receipt = build_accounting_receipt(
        spec,
        record,
        terminal_authority=terminal_authority,
        accounting_query_timestamp=query_timestamp,
        creation_timestamp=creation_timestamp,
    )
    value, digest = _publish_accounting_receipt_at_path(
        path, receipt, spec, terminal_authority=terminal_authority
    )
    return AccountingReceiptResult(value, digest, True, 1)


def validate_fixed_scheduler_accounting_environment(
    terminal_authority: Mapping[str, Any],
) -> dict[str, str]:
    commit = str(terminal_authority.get("adjudication_implementation_commit", ""))
    authority = validate_terminal_authority(
        terminal_authority, adjudication_implementation_commit=commit
    )
    try:
        account, _payload, digest = _read_producer_canonical_json(
            "scheduler_account"
        )
        account = r7.validate_r8u_r7d_scheduler_account_authority(account)
    except R7GAccountingError:
        raise
    except Exception as exc:
        raise R7GAccountingError(
            "R8U_R7G_QACCT_ENVIRONMENT_INVALID"
        ) from exc
    environment = account.get("sealed_qsub_environment")
    if (
        digest != authority["scheduler_account_authority_sha256"]
        or account.get("expected_scheduler_username") != authority["expected_owner"]
        or account.get("qsub_environment_sha256")
        != authority["qsub_environment_sha256"]
        or not isinstance(environment, Mapping)
    ):
        _fail("R8U_R7G_QACCT_ENVIRONMENT_INVALID")
    return {str(key): str(value) for key, value in environment.items()}


def preflight_fixed_accounting_receipts(
    *, terminal_authority: Mapping[str, Any]
) -> dict[str, AccountingReceiptResult | None]:
    ensure_fixed_accounting_directory()
    result: dict[str, AccountingReceiptResult | None] = {}
    for spec in fixed_accounting_specs(terminal_authority):
        key = f"task_{spec.task_id}" if spec.task_id is not None else "finalizer"
        if not os.path.lexists(spec.receipt_path):
            result[key] = None
            continue
        value, digest = load_accounting_receipt(
            spec, terminal_authority=terminal_authority
        )
        result[key] = AccountingReceiptResult(value, digest, False, 0)
    return result


def reuse_or_query_fixed_accounting(
    spec: FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    tool_validator: Callable[[], None] = _validate_qacct_tool,
    clock: Callable[[], datetime] = _utc_now,
) -> AccountingReceiptResult:
    _require_fixed_spec(spec, terminal_authority)
    ensure_fixed_accounting_directory()
    return _reuse_or_query_accounting_at_path(
        spec,
        spec.receipt_path,
        terminal_authority=terminal_authority,
        environment=environment,
        runner=runner,
        tool_validator=tool_validator,
        clock=clock,
    )


__all__ = (
    "ACCOUNTING_ROOT",
    "ACCOUNT_AUTHORITY_PATH",
    "ARRAY_JOB_ID",
    "ARRAY_MAX_CONCURRENCY",
    "ARRAY_ROLE",
    "ARRAY_TASK_IDS",
    "ARRAY_TASK_RANGE",
    "AccountingReceiptResult",
    "COMBINED_SUBMISSION_PATH",
    "EXPECTED_R7F_RECEIPT_SHA256",
    "FINALIZER_JOB_ID",
    "FINALIZER_ROLE",
    "FixedAccountingSpec",
    "HISTORICAL_JSON_ROLES_AUDITED",
    "HISTORICAL_JSON_ROLE_POLICY",
    "PROBE_JOB_ID",
    "PROBE_ROLE",
    "QACCT_PATH",
    "QACCT_RECORD_NORMALIZATION",
    "R7GAccountingError",
    "R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT",
    "R7G_ROOT",
    "RUNTIME_IMPLEMENTATION_COMMIT",
    "SCHEDULER_LOG_ROOT",
    "TERMINAL_AUTHORITY_PATH",
    "TRACKED_FINALIZER_ENTRYPOINT",
    "TRACKED_SUBMISSION_ENTRYPOINT",
    "TRACKED_WORKER_ENTRYPOINT",
    "TerminalAuthorityResult",
    "build_accounting_receipt",
    "ensure_fixed_accounting_directory",
    "ensure_terminal_authority",
    "fixed_accounting_specs",
    "load_accounting_receipt",
    "load_terminal_authority",
    "normalize_qacct_record",
    "normalized_qacct_record_sha256",
    "parse_qacct_records",
    "preflight_fixed_accounting_receipts",
    "preflight_fixed_terminal_authority",
    "project_fixed_qacct_record",
    "publish_accounting_receipt",
    "query_fixed_qacct_record",
    "reuse_or_query_fixed_accounting",
    "validate_accounting_receipt",
    "validate_fixed_scheduler_accounting_environment",
    "validate_terminal_authority",
)
