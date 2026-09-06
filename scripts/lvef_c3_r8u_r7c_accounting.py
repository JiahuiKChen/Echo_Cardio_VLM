#!/usr/bin/env python3
"""Fixed R8U-R7C terminal-accounting receipts for completed R7 jobs.

This module is deliberately separate from the live R7 continuation worker.
It accepts no caller-selected job, task, attempt, or receipt destination.  The
only subprocess operation exposed here is one fixed qacct query for one of the
four immutable R7 records.  Importing or executing this module performs no
query and no scientific operation.
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


ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
SCIENTIFIC_COMMIT: Final = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
RUNTIME_IMPLEMENTATION_COMMIT: Final = (
    "1be99c6436293a7cad576e9855ba4cd58a71e156"
)
CONTINUATION_RECEIPT_SHA256: Final = (
    "9ccc876aa4de905ba6a6a69129a21fc1cdb5f6a2d83cc91f7f49fefd8c33a000"
)
ARRAY_JOB_ID: Final = "7478863"
FINALIZER_JOB_ID: Final = "7478864"
ARRAY_JOB_NAME: Final = "lvef_c3_r8u_r7_seq_1be99c64"
FINALIZER_JOB_NAME: Final = "lvef_c3_r8u_r7_fin_1be99c64"
OWNER: Final = "pkarim"
ARRAY_ROLE: Final = "R8U_R7_CONTINUATION_ARRAY"
FINALIZER_ROLE: Final = "R8U_R7_COHORT_FINALIZER"

RUNTIME_CONTROLLER_SHA256: Final = (
    "e1aae71122249300088b7ed5d99487cb73282fc3fb0da59b884c886ba904cb4b"
)
RUNTIME_RUNNER_SHA256: Final = (
    "6878b3ca63d190d3aeaf98e851671a477fefc163fa24fefa004a5c38e69bcd9f"
)
TRACKED_SUBMISSION_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "submit_r8u_r7_continuation_17_19"
)
TRACKED_WORKER_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "run_r8u_r7_continuation_array_task"
)
TRACKED_FINALIZER_ENTRYPOINT: Final = (
    "scripts/lvef_c3_r8r_recovery_continuation.py::"
    "run_r8u_r7_continuation_finalizer"
)

R7C_ROOT: Final = r7.ATTEMPT_ROOT / "r8u_r7c_terminal_adjudication"
ACCOUNTING_ROOT: Final = R7C_ROOT / "accounting"
CONTINUATION_RECEIPT_PATH: Final = r7.R8U_R7_CONTINUATION_SUBMISSION_PATH
ACCOUNT_AUTHORITY_PATH: Final = r7.R8U_R7_ACCOUNT_AUTHORITY_PATH
QACCT_PATH: Final = scheduler.CANONICAL_SGE_ROOT / "bin/linux-x64/qacct"
MAX_QACCT_BYTES: Final = 4 * 1024 * 1024
MAX_RECEIPT_BYTES: Final = 2 * 1024 * 1024
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


class R7CAccountingError(RuntimeError):
    """One stable fail-closed R7C accounting error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7CAccountingError(code)


@dataclass(frozen=True)
class FixedAccountingSpec:
    """Immutable identity of one authorized qacct record."""

    job_kind: str
    job_id: str
    task_id: int | None
    expected_job_role: str
    expected_job_name: str
    receipt_path: Path

    def qacct_argv(self) -> tuple[str, ...]:
        command = [str(QACCT_PATH), "-j", self.job_id]
        if self.task_id is not None:
            command.extend(("-t", str(self.task_id)))
        return tuple(command)


TASK_ACCOUNTING_SPECS: Final = {
    task_id: FixedAccountingSpec(
        job_kind="ARRAY_TASK",
        job_id=ARRAY_JOB_ID,
        task_id=task_id,
        expected_job_role=ARRAY_ROLE,
        expected_job_name=ARRAY_JOB_NAME,
        receipt_path=(
            ACCOUNTING_ROOT
            / f"array_{ARRAY_JOB_ID}_task_{task_id}_accounting.restricted.json"
        ),
    )
    for task_id in (17, 18, 19)
}
FINALIZER_ACCOUNTING_SPEC: Final = FixedAccountingSpec(
    job_kind="NON_ARRAY_FINALIZER",
    job_id=FINALIZER_JOB_ID,
    task_id=None,
    expected_job_role=FINALIZER_ROLE,
    expected_job_name=FINALIZER_JOB_NAME,
    receipt_path=(
        ACCOUNTING_ROOT
        / f"finalizer_{FINALIZER_JOB_ID}_accounting.restricted.json"
    ),
)
FIXED_ACCOUNTING_SPECS: Final = (
    TASK_ACCOUNTING_SPECS[17],
    TASK_ACCOUNTING_SPECS[18],
    TASK_ACCOUNTING_SPECS[19],
    FINALIZER_ACCOUNTING_SPEC,
)

COMMON_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_name",
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "batch_plan_sha256",
        "scientific_commit",
        "runtime_implementation_commit",
        "adjudication_implementation_commit",
        "continuation_submission_receipt_path",
        "continuation_submission_receipt_sha256",
        "job_kind",
        "job_id",
        "task_id",
        "expected_job_role",
        "expected_job_name",
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
ARRAY_RECEIPT_KEYS: Final = COMMON_RECEIPT_KEYS | {"array_job_id"}
FINALIZER_RECEIPT_KEYS: Final = COMMON_RECEIPT_KEYS | {"finalizer_job_id"}


@dataclass(frozen=True)
class AccountingReceiptResult:
    receipt: Mapping[str, Any]
    receipt_sha256: str
    created: bool
    qacct_query_count: int


def fixed_accounting_specs() -> tuple[FixedAccountingSpec, ...]:
    """Return the four immutable records in adjudication order."""

    return FIXED_ACCOUNTING_SPECS


def _require_fixed_spec(spec: FixedAccountingSpec) -> None:
    if spec not in FIXED_ACCOUNTING_SPECS:
        _fail("R8U_R7C_ACCOUNTING_SCOPE_INVALID")


def _require_adjudication_commit(value: str) -> None:
    if (
        not isinstance(value, str)
        or COMMIT_RE.fullmatch(value) is None
        or value == RUNTIME_IMPLEMENTATION_COMMIT
    ):
        _fail("R8U_R7C_ADJUDICATION_COMMIT_INVALID")


def normalize_qacct_record(record: Mapping[str, str]) -> dict[str, str]:
    """Normalize one qacct mapping without discarding any record field."""

    if not isinstance(record, Mapping) or not record:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    normalized: dict[str, str] = {}
    for key, value in record.items():
        if (
            not isinstance(key, str)
            or QACCT_KEY_RE.fullmatch(key) is None
            or not isinstance(value, str)
            or any(
                ord(character) < 32 and character not in "\t"
                for character in value
            )
            or "\x7f" in value
        ):
            _fail("R8U_R7C_QACCT_RECORD_INVALID")
        collapsed = re.sub(r"[ \t]+", " ", value.strip(" \t"))
        normalized[key] = collapsed
    return dict(sorted(normalized.items()))


def normalized_qacct_record_sha256(record: Mapping[str, str]) -> str:
    normalized = normalize_qacct_record(record)
    return core.canonical_json_sha256(
        {
            "normalization": QACCT_RECORD_NORMALIZATION,
            "record": normalized,
        }
    )


def parse_qacct_records(payload: bytes) -> tuple[dict[str, str], ...]:
    """Strictly parse bounded qacct key/value records.

    Whitespace and field order are normalized later.  Duplicate keys,
    malformed lines, control characters, and partial records are rejected.
    """

    if not isinstance(payload, bytes) or len(payload) > MAX_QACCT_BYTES:
        _fail("R8U_R7C_QACCT_OUTPUT_INVALID")
    try:
        decoded = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R7CAccountingError("R8U_R7C_QACCT_OUTPUT_INVALID") from exc
    if "\x00" in decoded:
        _fail("R8U_R7C_QACCT_OUTPUT_INVALID")

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
            _fail("R8U_R7C_QACCT_OUTPUT_INVALID")
        value = match.group(2)
        if any(ord(character) < 32 and character != "\t" for character in value):
            _fail("R8U_R7C_QACCT_OUTPUT_INVALID")
        current[match.group(1)] = value
    if current:
        records.append(normalize_qacct_record(current))
    return tuple(records)


def _parse_qacct_time(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
    except (TypeError, ValueError) as exc:
        raise R7CAccountingError("R8U_R7C_QACCT_RECORD_INVALID") from exc


def _parse_failed(value: str) -> int:
    match = FAILED_RE.fullmatch(value)
    if match is None:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    return int(match.group(1))


def _parse_nonnegative_integer(value: str) -> int:
    if NONNEGATIVE_INTEGER_RE.fullmatch(value) is None:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    return int(value)


def _parse_wall_seconds(value: str) -> int:
    """Return the whole elapsed seconds required by the reporting contract.

    Grid Engine may print fractional ``ru_wallclock`` values.  The complete
    normalized source value remains sealed in the receipt; this projection
    truncates a finite nonnegative value toward zero (equivalently, floors it)
    solely for the integer ``*_QACCT_WALL_SECONDS`` reporting field.
    """

    if NONNEGATIVE_DECIMAL_RE.fullmatch(value) is None:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise R7CAccountingError("R8U_R7C_QACCT_RECORD_INVALID") from exc
    if not parsed.is_finite() or parsed < 0:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    return int(parsed)


def _optional_qacct_field(record: Mapping[str, str], key: str) -> str | None:
    value = record.get(key)
    if value is None or value in {"", "NONE", "undefined"}:
        return None
    return value


def project_fixed_qacct_record(
    spec: FixedAccountingSpec, record: Mapping[str, str]
) -> dict[str, Any]:
    """Validate identity/chronology and project one fixed terminal record."""

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
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
    if (
        normalized["jobnumber"] != spec.job_id
        or normalized["jobname"] != spec.expected_job_name
        or normalized["owner"] != OWNER
    ):
        _fail("R8U_R7C_QACCT_IDENTITY_INVALID")
    if spec.task_id is None:
        if normalized["taskid"] not in {"", "NONE", "undefined"}:
            _fail("R8U_R7C_QACCT_IDENTITY_INVALID")
    elif (
        spec.task_id not in {17, 18, 19}
        or normalized["taskid"] != str(spec.task_id)
    ):
        _fail("R8U_R7C_QACCT_IDENTITY_INVALID")

    submission = _parse_qacct_time(normalized["qsub_time"])
    start = _parse_qacct_time(normalized["start_time"])
    end = _parse_qacct_time(normalized["end_time"])
    if not submission <= start <= end:
        _fail("R8U_R7C_QACCT_RECORD_INVALID")
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
    """Reuse the already-audited root-owned fixed qacct authority."""

    if r7.QACCT_PATH != QACCT_PATH:
        _fail("R8U_R7C_QACCT_TOOL_INVALID")
    try:
        r7._validate_qacct_tool()
    except Exception as exc:
        raise R7CAccountingError("R8U_R7C_QACCT_TOOL_INVALID") from exc


def query_fixed_qacct_record(
    spec: FixedAccountingSpec,
    *,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    tool_validator: Callable[[], None] = _validate_qacct_tool,
) -> dict[str, str]:
    """Perform exactly one fixed qacct invocation, without polling/retry."""

    _require_fixed_spec(spec)
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
        _fail("R8U_R7C_QACCT_ENVIRONMENT_INVALID")
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
        raise R7CAccountingError("R8U_R7C_ACCOUNTING_NOT_AVAILABLE") from exc
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > MAX_QACCT_BYTES
    ):
        _fail("R8U_R7C_ACCOUNTING_NOT_AVAILABLE")
    records = parse_qacct_records(stdout)
    if len(records) != 1:
        _fail("R8U_R7C_ACCOUNTING_NOT_AVAILABLE")
    project_fixed_qacct_record(spec, records[0])
    return records[0]


def _format_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("R8U_R7C_RECEIPT_TIMESTAMP_INVALID")
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("R8U_R7C_RECEIPT_TIMESTAMP_INVALID")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise R7CAccountingError("R8U_R7C_RECEIPT_TIMESTAMP_INVALID") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _receipt_schema(spec: FixedAccountingSpec) -> tuple[str, str, frozenset[str]]:
    if spec.job_kind == "ARRAY_TASK" and spec.task_id in {17, 18, 19}:
        return (
            "lvef_c3_r8u_r7c_array_task_accounting",
            "lvef_c3_r8u_r7c_array_task_accounting_v1",
            ARRAY_RECEIPT_KEYS,
        )
    if spec.job_kind == "NON_ARRAY_FINALIZER" and spec.task_id is None:
        return (
            "lvef_c3_r8u_r7c_finalizer_accounting",
            "lvef_c3_r8u_r7c_finalizer_accounting_v1",
            FINALIZER_RECEIPT_KEYS,
        )
    _fail("R8U_R7C_ACCOUNTING_SCOPE_INVALID")


def build_accounting_receipt(
    spec: FixedAccountingSpec,
    record: Mapping[str, str],
    *,
    adjudication_implementation_commit: str,
    accounting_query_timestamp: datetime,
    creation_timestamp: datetime,
) -> dict[str, Any]:
    """Build a strict receipt for either success or nonzero accounting."""

    _require_fixed_spec(spec)
    _require_adjudication_commit(adjudication_implementation_commit)
    normalized = normalize_qacct_record(record)
    projection = project_fixed_qacct_record(spec, normalized)
    query_timestamp = _format_utc_timestamp(accounting_query_timestamp)
    created_timestamp = _format_utc_timestamp(creation_timestamp)
    if _parse_utc_timestamp(created_timestamp) < _parse_utc_timestamp(query_timestamp):
        _fail("R8U_R7C_RECEIPT_TIMESTAMP_INVALID")
    schema_name, artifact_type, _keys = _receipt_schema(spec)
    argv = list(spec.qacct_argv())
    receipt: dict[str, Any] = {
        "schema_name": schema_name,
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": "PASS_CANONICAL_FIXED_QACCT_RECORD_CAPTURED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "continuation_submission_receipt_path": str(CONTINUATION_RECEIPT_PATH),
        "continuation_submission_receipt_sha256": CONTINUATION_RECEIPT_SHA256,
        "job_kind": spec.job_kind,
        "job_id": spec.job_id,
        "task_id": spec.task_id,
        "expected_job_role": spec.expected_job_role,
        "expected_job_name": spec.expected_job_name,
        **projection,
        "accounting_query_timestamp_utc": query_timestamp,
        "creation_timestamp_utc": created_timestamp,
        "fixed_qacct_argv": argv,
        "fixed_qacct_argv_sha256": core.canonical_json_sha256({"argv": argv}),
        "qacct_record_normalization": QACCT_RECORD_NORMALIZATION,
        "normalized_qacct_record": normalized,
        "normalized_raw_qacct_record_sha256": (
            normalized_qacct_record_sha256(normalized)
        ),
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "runtime_controller_sha256": RUNTIME_CONTROLLER_SHA256,
        "runtime_runner_sha256": RUNTIME_RUNNER_SHA256,
    }
    if spec.task_id is None:
        receipt["finalizer_job_id"] = spec.job_id
    else:
        receipt["array_job_id"] = spec.job_id
    return validate_accounting_receipt(
        receipt,
        spec,
        adjudication_implementation_commit=adjudication_implementation_commit,
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


def validate_accounting_receipt(
    value: Mapping[str, Any],
    spec: FixedAccountingSpec,
    *,
    adjudication_implementation_commit: str,
) -> dict[str, Any]:
    """Fully rederive all fixed and qacct-projected receipt fields."""

    _require_fixed_spec(spec)
    schema_name, artifact_type, keys = _receipt_schema(spec)
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_SCHEMA_INVALID")
    fixed = {
        "schema_name": schema_name,
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": "PASS_CANONICAL_FIXED_QACCT_RECORD_CAPTURED",
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "adjudication_implementation_commit": adjudication_implementation_commit,
        "continuation_submission_receipt_path": str(CONTINUATION_RECEIPT_PATH),
        "continuation_submission_receipt_sha256": CONTINUATION_RECEIPT_SHA256,
        "job_kind": spec.job_kind,
        "job_id": spec.job_id,
        "task_id": spec.task_id,
        "expected_job_role": spec.expected_job_role,
        "expected_job_name": spec.expected_job_name,
        "fixed_qacct_argv": list(spec.qacct_argv()),
        "fixed_qacct_argv_sha256": core.canonical_json_sha256(
            {"argv": list(spec.qacct_argv())}
        ),
        "qacct_record_normalization": QACCT_RECORD_NORMALIZATION,
        "tracked_submission_entrypoint": TRACKED_SUBMISSION_ENTRYPOINT,
        "tracked_worker_entrypoint": TRACKED_WORKER_ENTRYPOINT,
        "tracked_finalizer_entrypoint": TRACKED_FINALIZER_ENTRYPOINT,
        "runtime_controller_sha256": RUNTIME_CONTROLLER_SHA256,
        "runtime_runner_sha256": RUNTIME_RUNNER_SHA256,
    }
    _require_adjudication_commit(adjudication_implementation_commit)
    if any(
        not _exact_typed_equal(value.get(key), item)
        for key, item in fixed.items()
    ):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")
    if spec.task_id is None:
        if value.get("finalizer_job_id") != spec.job_id:
            _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")
    elif value.get("array_job_id") != spec.job_id:
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")

    query_timestamp = _parse_utc_timestamp(
        value.get("accounting_query_timestamp_utc")
    )
    creation_timestamp = _parse_utc_timestamp(value.get("creation_timestamp_utc"))
    if creation_timestamp < query_timestamp:
        _fail("R8U_R7C_RECEIPT_TIMESTAMP_INVALID")
    record = value.get("normalized_qacct_record")
    if not isinstance(record, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in record.items()
    ):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_SCHEMA_INVALID")
    normalized = normalize_qacct_record(record)
    if not _exact_typed_equal(dict(record), normalized):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")
    projection = project_fixed_qacct_record(spec, normalized)
    if any(
        not _exact_typed_equal(value.get(key), item)
        for key, item in projection.items()
    ):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")
    digest = normalized_qacct_record_sha256(normalized)
    if (
        value.get("normalized_raw_qacct_record_sha256") != digest
        or SHA_RE.fullmatch(str(value.get("normalized_raw_qacct_record_sha256", "")))
        is None
    ):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID")
    return dict(value)


def _strict_json_object(payload: bytes) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID")

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except R7CAccountingError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID"
        ) from exc
    if not isinstance(value, dict):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID")
    return value


def _read_owner_private_regular(path: Path) -> bytes:
    """Read one stable owner-private, single-link file through O_NOFOLLOW."""

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
            _fail("R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except R7CAccountingError:
        raise
    except Exception as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID"
        ) from exc
    try:
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
            _fail("R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID")
        blocks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except R7CAccountingError:
        raise
    except OSError as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID"
        ) from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        visible_after = os.lstat(path)
    except OSError as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID"
        ) from exc
    if identity(opened) != identity(after) or identity(after) != identity(visible_after):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID")
    return b"".join(blocks)


def load_accounting_receipt(
    path: Path,
    spec: FixedAccountingSpec,
    *,
    adjudication_implementation_commit: str,
) -> tuple[dict[str, Any], str]:
    payload = _read_owner_private_regular(path)
    value = _strict_json_object(payload)
    if payload != core.canonical_json_bytes(value):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID")
    validated = validate_accounting_receipt(
        value,
        spec,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    return validated, hashlib.sha256(payload).hexdigest()


def _validate_private_directory(path: Path) -> None:
    try:
        r7.sequential._require_nonsymlink_components(path)
        metadata = os.lstat(path)
    except Exception as exc:
        raise R7CAccountingError("R8U_R7C_ACCOUNTING_DIRECTORY_INVALID") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o777 != 0o700
        or mode & 0o7000 not in {0, stat.S_ISGID}
    ):
        _fail("R8U_R7C_ACCOUNTING_DIRECTORY_INVALID")


def ensure_fixed_accounting_directory() -> None:
    """Create only the two fixed private R7C directories, or reuse them."""

    _validate_private_directory(R7C_ROOT.parent)
    for path in (R7C_ROOT, ACCOUNTING_ROOT):
        if os.path.lexists(path):
            _validate_private_directory(path)
            continue
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise R7CAccountingError(
                "R8U_R7C_ACCOUNTING_DIRECTORY_INVALID"
            ) from exc
        _validate_private_directory(path)


def _atomic_write_private_json_no_clobber(
    path: Path, value: Mapping[str, Any]
) -> str:
    """Publish canonical JSON through an explicitly mode-hardened temp inode."""

    body = core.canonical_json_bytes(value)
    temporary = path.parent / f".{path.name}.{ATTEMPT_ID}.partial"
    if os.path.lexists(path) or os.path.lexists(temporary):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_COLLISION")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        # os.open's mode is filtered by the process umask.  Restore the exact
        # owner-private mode on the already-open inode before it can become the
        # canonical path.
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
            _fail("R8U_R7C_ACCOUNTING_RECEIPT_PUBLICATION_INVALID")
        view = memoryview(body)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count < 1:
                _fail("R8U_R7C_ACCOUNTING_RECEIPT_PUBLICATION_INVALID")
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
            _fail("R8U_R7C_ACCOUNTING_RECEIPT_PUBLICATION_INVALID")
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except R7CAccountingError:
        raise
    except Exception as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_COLLISION"
        ) from exc
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


def publish_accounting_receipt(
    path: Path,
    value: Mapping[str, Any],
    spec: FixedAccountingSpec,
    *,
    adjudication_implementation_commit: str,
) -> tuple[dict[str, Any], str]:
    """Atomically publish with no replacement, then reopen and revalidate."""

    validate_accounting_receipt(
        value,
        spec,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    _validate_private_directory(path.parent)
    if os.path.lexists(path):
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_COLLISION")
    try:
        written_sha256 = _atomic_write_private_json_no_clobber(path, value)
    except Exception as exc:
        raise R7CAccountingError(
            "R8U_R7C_ACCOUNTING_RECEIPT_COLLISION"
        ) from exc
    reopened, reopened_sha256 = load_accounting_receipt(
        path,
        spec,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    if written_sha256 != reopened_sha256:
        _fail("R8U_R7C_ACCOUNTING_RECEIPT_REOPEN_INVALID")
    return reopened, reopened_sha256


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _reuse_or_query_accounting_at_path(
    spec: FixedAccountingSpec,
    path: Path,
    *,
    adjudication_implementation_commit: str,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    tool_validator: Callable[[], None],
    clock: Callable[[], datetime],
) -> AccountingReceiptResult:
    _require_adjudication_commit(adjudication_implementation_commit)
    if os.path.lexists(path):
        value, digest = load_accounting_receipt(
            path,
            spec,
            adjudication_implementation_commit=adjudication_implementation_commit,
        )
        return AccountingReceiptResult(value, digest, False, 0)

    query_timestamp = clock()
    record = query_fixed_qacct_record(
        spec,
        environment=environment,
        runner=runner,
        tool_validator=tool_validator,
    )
    creation_timestamp = clock()
    receipt = build_accounting_receipt(
        spec,
        record,
        adjudication_implementation_commit=adjudication_implementation_commit,
        accounting_query_timestamp=query_timestamp,
        creation_timestamp=creation_timestamp,
    )
    value, digest = publish_accounting_receipt(
        path,
        receipt,
        spec,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    return AccountingReceiptResult(value, digest, True, 1)


def _load_fixed_continuation_receipt() -> tuple[dict[str, Any], str]:
    payload = _read_owner_private_regular(CONTINUATION_RECEIPT_PATH)
    digest = hashlib.sha256(payload).hexdigest()
    value = _strict_json_object(payload)
    if (
        digest != CONTINUATION_RECEIPT_SHA256
        or set(value) != set(r7.R8U_R7_CONTINUATION_SUBMISSION_KEYS)
        or value.get("attempt_id") != ATTEMPT_ID
        or value.get("batch_plan_sha256") != PLAN_SHA256
        or value.get("original_scientific_commit") != SCIENTIFIC_COMMIT
        or value.get("implementation_commit") != RUNTIME_IMPLEMENTATION_COMMIT
        or value.get("array_job_id") != ARRAY_JOB_ID
        or value.get("finalizer_job_id") != FINALIZER_JOB_ID
        or value.get("array_job_name") != ARRAY_JOB_NAME
        or value.get("finalizer_job_name") != FINALIZER_JOB_NAME
        or value.get("array_task_range") != "17-19"
        or value.get("array_task_count") != 3
        or value.get("array_max_concurrency") != 1
    ):
        _fail("R8U_R7C_CONTINUATION_RECEIPT_INVALID")
    return value, digest


def validate_fixed_continuation_receipt() -> str:
    """Validate the immutable R7A receipt without invoking a worker gate."""

    _value, digest = _load_fixed_continuation_receipt()
    return digest


def validate_fixed_scheduler_accounting_environment() -> dict[str, str]:
    """Return the sealed qacct environment; never inspect worker variables."""

    try:
        continuation, _continuation_digest = _load_fixed_continuation_receipt()
        payload = _read_owner_private_regular(ACCOUNT_AUTHORITY_PATH)
        value = _strict_json_object(payload)
        authority = r7.validate_r8u_r7_scheduler_account_authority(value)
    except Exception as exc:
        raise R7CAccountingError(
            "R8U_R7C_QACCT_ENVIRONMENT_INVALID"
        ) from exc
    if (
        hashlib.sha256(payload).hexdigest()
        != continuation.get("scheduler_account_authority_sha256")
        or authority.get("implementation_commit")
        != RUNTIME_IMPLEMENTATION_COMMIT
        or authority.get("expected_scheduler_username") != OWNER
        or authority.get("runner_sha256") != RUNTIME_RUNNER_SHA256
    ):
        _fail("R8U_R7C_QACCT_ENVIRONMENT_INVALID")
    environment = authority.get("sealed_qsub_environment")
    if not isinstance(environment, Mapping):
        _fail("R8U_R7C_QACCT_ENVIRONMENT_INVALID")
    return {str(key): str(value) for key, value in environment.items()}


def preflight_fixed_accounting_receipts(
    *, adjudication_implementation_commit: str
) -> dict[str, AccountingReceiptResult | None]:
    """Reject every occupied-invalid path before any qacct query is allowed."""

    _require_adjudication_commit(adjudication_implementation_commit)
    ensure_fixed_accounting_directory()
    result: dict[str, AccountingReceiptResult | None] = {}
    for spec in FIXED_ACCOUNTING_SPECS:
        key = (
            f"task_{spec.task_id}"
            if spec.task_id is not None
            else "finalizer"
        )
        if not os.path.lexists(spec.receipt_path):
            result[key] = None
            continue
        value, digest = load_accounting_receipt(
            spec.receipt_path,
            spec,
            adjudication_implementation_commit=adjudication_implementation_commit,
        )
        result[key] = AccountingReceiptResult(value, digest, False, 0)
    return result


def reuse_or_query_fixed_accounting(
    spec: FixedAccountingSpec,
    *,
    adjudication_implementation_commit: str,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    tool_validator: Callable[[], None] = _validate_qacct_tool,
    clock: Callable[[], datetime] = _utc_now,
) -> AccountingReceiptResult:
    """Reuse one valid receipt or make its one authorized fixed qacct query."""

    _require_fixed_spec(spec)
    _require_adjudication_commit(adjudication_implementation_commit)
    ensure_fixed_accounting_directory()
    return _reuse_or_query_accounting_at_path(
        spec,
        spec.receipt_path,
        adjudication_implementation_commit=adjudication_implementation_commit,
        environment=environment,
        runner=runner,
        tool_validator=tool_validator,
        clock=clock,
    )


__all__ = (
    "ACCOUNTING_ROOT",
    "ACCOUNT_AUTHORITY_PATH",
    "ATTEMPT_ID",
    "ARRAY_JOB_ID",
    "ARRAY_JOB_NAME",
    "ARRAY_ROLE",
    "AccountingReceiptResult",
    "CONTINUATION_RECEIPT_PATH",
    "CONTINUATION_RECEIPT_SHA256",
    "FINALIZER_ACCOUNTING_SPEC",
    "FINALIZER_JOB_ID",
    "FINALIZER_JOB_NAME",
    "FINALIZER_ROLE",
    "FIXED_ACCOUNTING_SPECS",
    "FixedAccountingSpec",
    "OWNER",
    "PLAN_SHA256",
    "QACCT_PATH",
    "QACCT_RECORD_NORMALIZATION",
    "R7CAccountingError",
    "R7C_ROOT",
    "RUNTIME_CONTROLLER_SHA256",
    "RUNTIME_IMPLEMENTATION_COMMIT",
    "RUNTIME_RUNNER_SHA256",
    "SCIENTIFIC_COMMIT",
    "TASK_ACCOUNTING_SPECS",
    "TRACKED_FINALIZER_ENTRYPOINT",
    "TRACKED_SUBMISSION_ENTRYPOINT",
    "TRACKED_WORKER_ENTRYPOINT",
    "build_accounting_receipt",
    "ensure_fixed_accounting_directory",
    "fixed_accounting_specs",
    "load_accounting_receipt",
    "normalize_qacct_record",
    "normalized_qacct_record_sha256",
    "parse_qacct_records",
    "preflight_fixed_accounting_receipts",
    "project_fixed_qacct_record",
    "publish_accounting_receipt",
    "query_fixed_qacct_record",
    "reuse_or_query_fixed_accounting",
    "validate_accounting_receipt",
    "validate_fixed_continuation_receipt",
    "validate_fixed_scheduler_accounting_environment",
)
