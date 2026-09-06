#!/usr/bin/env python3
"""Read only the four fixed R7 continuation scheduler logs after qacct.

R7 submitted both jobs with ``-j y`` and an output *directory*.  Grid Engine
therefore wrote one merged stdout/stderr file for each task/job.  This module
derives those filenames from immutable R7C accounting specifications; it has
no API that accepts a filesystem path or an arbitrary scheduler identity.

The returned projection deliberately contains only fixed markers and file
metadata.  Tracebacks and all other free-form log text remain on SCC.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Final, Mapping


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_r8u_r7c_accounting as accounting


SCHEDULER_ROOT: Final = (
    accounting.R7C_ROOT.parent / "r8u_r7_continuation_17_19" / "scheduler"
)
MAX_FIXED_LOG_BYTES: Final = 4 * 1024 * 1024

BLOCKED_STATUS_RE: Final = re.compile(
    rb"^R8U_R7_STATUS=BLOCKED_([A-Z][A-Z0-9_]*)$"
)
FAILED_STAGE_RE: Final = re.compile(
    rb"^R8U_R7_FAILED_STAGE=([A-Z][A-Z0-9_]*)$"
)
ARRAY_PASS_MARKER: Final = (
    b"R8U_R7_CONTINUATION_BATCH_STATUS=PASS_BATCH_FINALIZED"
)
FINALIZER_PASS_MARKER: Final = (
    b"R8U_R7_STATUS=PASS_R8U_R7_FIXED_CONTINUATION_FINALIZED"
)
PASS_MARKERS: Final = frozenset({ARRAY_PASS_MARKER, FINALIZER_PASS_MARKER})

# These are gates executed before scientific work or metadata-only cohort
# aggregation.  A failure carrying one of these tokens must not be described
# as an independently established scientific failure.
# Only these affirmative, known terminal markers establish that an application
# nonzero was confined to retrospective/runtime authority.  In particular, an
# unfamiliar marker must never become ``CONTROL_PLANE_ONLY`` merely because it
# is not recognized as scientific.
CONTROL_PLANE_CODES: Final = frozenset(
    {
        "FINAL_OUTPUT_ALREADY_EXISTS",
        "R8U_R7_CONTINUATION_ARRAY_CONTEXT_INVALID",
        "R8U_R7_CONTINUATION_CHAIN_INVALID",
        "R8U_R7_CONTINUATION_FINALIZER_CONTEXT_INVALID",
        "R8U_R7_CONTINUATION_OUTPUT_COLLISION",
        "R8U_R7_CONTINUATION_WORKER_AUTHORITY_INVALID",
        "R8U_R7_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
        "SCHEDULER_EFFECTIVE_UID_MISMATCH",
        "SCHEDULER_IMPLEMENTATION_COMMIT_MISMATCH",
        "SCHEDULER_JOB_ID_BINDING_MISMATCH",
        "SCHEDULER_JOB_ROLE_MISMATCH",
        "SCHEDULER_PYTHON_AUTHORITY_MISMATCH",
        "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH",
        "SCHEDULER_RUNNER_AUTHORITY_MISMATCH",
        "SCHEDULER_TASK_ID_BINDING_MISMATCH",
        "SCHEDULER_WORKER_CONTEXT_INVALID",
    }
)
APPLICATION_TOKENS: Final = (
    "SCIENTIFIC",
    "BATCH_NOT_FINALIZED",
    "BATCH_FINALIZATION",
    "DICOM",
    "EXTRACTION",
    "ECHOPRIME",
    "EMBEDDING",
    "PRESERV",
    "CACHE",
    "RETIRE",
    "LEDGER",
    "MANIFEST",
    "SOURCE_OBJECT",
    "CONTINUATION_FINALIZATION",
)


class R7CTerminalLogError(RuntimeError):
    """One stable, non-sensitive failure from fixed terminal-log inspection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7CTerminalLogError(code)


def _require_fixed_spec(
    spec: accounting.FixedAccountingSpec,
) -> accounting.FixedAccountingSpec:
    if (
        not isinstance(spec, accounting.FixedAccountingSpec)
        or spec not in accounting.fixed_accounting_specs()
    ):
        _fail("R8U_R7C_TERMINAL_LOG_SCOPE_INVALID")
    return spec


def fixed_scheduler_log_path(spec: accounting.FixedAccountingSpec) -> Path:
    """Derive the sole R7 merged-log path for an immutable accounting spec."""

    fixed = _require_fixed_spec(spec)
    suffix = "" if fixed.task_id is None else f".{fixed.task_id}"
    basename = f"{fixed.expected_job_name}.o{fixed.job_id}{suffix}"
    expected_basenames = {
        "lvef_c3_r8u_r7_seq_1be99c64.o7478863.17",
        "lvef_c3_r8u_r7_seq_1be99c64.o7478863.18",
        "lvef_c3_r8u_r7_seq_1be99c64.o7478863.19",
        "lvef_c3_r8u_r7_fin_1be99c64.o7478864",
    }
    if basename not in expected_basenames:
        _fail("R8U_R7C_TERMINAL_LOG_SCOPE_INVALID")
    return SCHEDULER_ROOT / basename


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_nonsymlink_components(path: Path) -> None:
    """Reject symlinks in every fixed path component without resolving them."""

    if not path.is_absolute():
        _fail("R8U_R7C_TERMINAL_LOG_PATH_INVALID")
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                _fail("R8U_R7C_TERMINAL_LOG_PATH_INVALID")
    except R7CTerminalLogError:
        raise
    except OSError as exc:
        raise R7CTerminalLogError(
            "R8U_R7C_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc


def _read_fixed_regular(path: Path) -> tuple[bytes, os.stat_result]:
    """Bounded stable read of one owner-controlled regular file, no-follow."""

    descriptor: int | None = None
    try:
        _require_nonsymlink_components(path)
        before = os.lstat(path)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or mode & 0o200 == 0
            or mode & 0o022
            or before.st_size < 0
            or before.st_size > MAX_FIXED_LOG_BYTES
        ):
            _fail("R8U_R7C_TERMINAL_LOG_FILE_INVALID")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened):
            _fail("R8U_R7C_TERMINAL_LOG_FILE_INVALID")
        blocks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("R8U_R7C_TERMINAL_LOG_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except R7CTerminalLogError:
        raise
    except OSError as exc:
        raise R7CTerminalLogError(
            "R8U_R7C_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        visible_after = os.lstat(path)
    except OSError as exc:
        raise R7CTerminalLogError(
            "R8U_R7C_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc
    if (
        _identity(opened) != _identity(after)
        or _identity(after) != _identity(visible_after)
    ):
        _fail("R8U_R7C_TERMINAL_LOG_FILE_INVALID")
    return b"".join(blocks), after


def _blocked_failure_scope(code: str) -> tuple[str, str]:
    if code in CONTROL_PLANE_CODES:
        return "CONTROL_PLANE_ONLY", "FIXED_CONTROL_PLANE_TERMINAL_MARKER"
    if any(token in code for token in APPLICATION_TOKENS):
        return "SCIENTIFIC_OR_APPLICATION", "FIXED_APPLICATION_TERMINAL_MARKER"
    _fail("R8U_R7C_TERMINAL_LOG_FAILURE_MARKER_UNCLASSIFIED")


def _expected_pass_marker(
    spec: accounting.FixedAccountingSpec,
) -> bytes:
    if spec.job_kind == "ARRAY_TASK" and spec.task_id in {17, 18, 19}:
        return ARRAY_PASS_MARKER
    if spec.job_kind == "NON_ARRAY_FINALIZER" and spec.task_id is None:
        return FINALIZER_PASS_MARKER
    _fail("R8U_R7C_TERMINAL_LOG_SCOPE_INVALID")


def _fixed_markers(payload: bytes) -> tuple[str | None, str | None, str | None]:
    blocked: list[tuple[int, str]] = []
    stages: list[tuple[int, str]] = []
    passed: list[tuple[int, str]] = []
    for index, line in enumerate(payload.splitlines()):
        blocked_match = BLOCKED_STATUS_RE.fullmatch(line)
        if blocked_match is not None:
            blocked.append((index, blocked_match.group(1).decode("ascii")))
        stage_match = FAILED_STAGE_RE.fullmatch(line)
        if stage_match is not None:
            stages.append((index, stage_match.group(1).decode("ascii")))
        if line in PASS_MARKERS:
            passed.append((index, line.decode("ascii")))
    if len(blocked) > 1 or len(stages) > 1 or len(passed) > 1:
        _fail("R8U_R7C_TERMINAL_LOG_MARKERS_INVALID")
    blocked_marker = (
        f"R8U_R7_STATUS=BLOCKED_{blocked[0][1]}" if blocked else None
    )
    failed_stage = stages[0][1] if stages else None
    pass_marker = passed[0][1] if passed else None
    return blocked_marker, failed_stage, pass_marker


def inspect_fixed_terminal_logs(
    spec: accounting.FixedAccountingSpec,
    accounting_receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate/classify one qacct-proven result from its sole fixed R7 log.

    The accounting receipt is fully revalidated before any scheduler log is
    opened.  Passing accounting requires the exact job-kind terminal marker;
    failed accounting is classified only from affirmative fixed evidence.
    """

    fixed = _require_fixed_spec(spec)
    if not isinstance(accounting_receipt, Mapping):
        _fail("R8U_R7C_TERMINAL_LOG_ACCOUNTING_INVALID")
    adjudication_commit = accounting_receipt.get(
        "adjudication_implementation_commit"
    )
    if not isinstance(adjudication_commit, str):
        _fail("R8U_R7C_TERMINAL_LOG_ACCOUNTING_INVALID")
    try:
        receipt = accounting.validate_accounting_receipt(
            accounting_receipt,
            fixed,
            adjudication_implementation_commit=adjudication_commit,
        )
    except Exception as exc:
        raise R7CTerminalLogError(
            "R8U_R7C_TERMINAL_LOG_ACCOUNTING_INVALID"
        ) from exc
    failed = receipt.get("failed")
    exit_status = receipt.get("exit_status")
    classification = receipt.get("terminal_classification")
    if (
        type(failed) is not int
        or type(exit_status) is not int
        or classification not in {"PASS", "FAIL"}
        or (classification == "PASS")
        is not (failed == 0 and exit_status == 0)
    ):
        _fail("R8U_R7C_TERMINAL_LOG_ACCOUNTING_INVALID")

    path = fixed_scheduler_log_path(fixed)
    payload, metadata = _read_fixed_regular(path)
    blocked_marker, failed_stage, pass_marker = _fixed_markers(payload)
    blocked_code = (
        blocked_marker.removeprefix("R8U_R7_STATUS=BLOCKED_")
        if blocked_marker is not None
        else None
    )
    expected_pass_marker = _expected_pass_marker(fixed).decode("ascii")

    if classification == "PASS":
        if blocked_marker is not None:
            _fail("R8U_R7C_TERMINAL_LOG_PASS_CONTAINS_BLOCKED_MARKER")
        if failed_stage is not None:
            _fail("R8U_R7C_TERMINAL_LOG_PASS_CONTAINS_FAILED_STAGE")
        if pass_marker is None:
            _fail("R8U_R7C_TERMINAL_LOG_PASS_MARKER_MISSING")
        if pass_marker != expected_pass_marker:
            _fail("R8U_R7C_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND")
        failure_scope = "NOT_APPLICABLE"
        basis = "FIXED_JOB_KIND_PASS_TERMINAL_MARKER"
        first_marker = "NOT_AVAILABLE"
        first_stage = "NOT_APPLICABLE"
        result_status = "FIXED_PASS_TERMINAL_LOG_VALIDATED"
    else:
        if blocked_marker is not None and pass_marker is not None:
            _fail("R8U_R7C_TERMINAL_LOG_MARKERS_INVALID")
        if failed_stage is not None and blocked_marker is None:
            _fail("R8U_R7C_TERMINAL_LOG_FAILED_STAGE_WITHOUT_FAILURE_MARKER")
        if pass_marker is not None and pass_marker != expected_pass_marker:
            _fail("R8U_R7C_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND")

        if blocked_code is not None:
            failure_scope, basis = _blocked_failure_scope(blocked_code)
        elif pass_marker is not None:
            failure_scope = "SCHEDULER"
            basis = "QACCT_NONZERO_AFTER_FIXED_PASS_MARKER"
        elif failed != 0:
            # ``failed`` is Grid Engine's own affirmative scheduler-failure
            # field.  Unlike an unmarked application exit, it independently
            # supports scheduler scope.
            failure_scope = "SCHEDULER"
            basis = "QACCT_FAILED_NONZERO"
        else:
            _fail("R8U_R7C_TERMINAL_LOG_FAILURE_MARKER_MISSING")

        if blocked_marker is not None:
            first_marker = blocked_marker
        elif failed != 0:
            first_marker = "QACCT_FAILED_NONZERO"
        else:
            first_marker = "QACCT_EXIT_STATUS_NONZERO_AFTER_PASS_MARKER"
        first_stage = failed_stage or (
            "CONTINUATION_WORKER_SUBMISSION_VALIDATION"
            if blocked_code == "SCHEDULER_JOB_ROLE_MISMATCH"
            else "NOT_REPORTED"
        )
        result_status = "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED"

    terminal_marker = blocked_marker or pass_marker or "NOT_AVAILABLE"

    return {
        "artifact_type": "lvef_c3_r8u_r7c_fixed_terminal_log_projection_v1",
        "status": result_status,
        "job_kind": fixed.job_kind,
        "job_id": fixed.job_id,
        "task_id": fixed.task_id,
        "expected_job_role": fixed.expected_job_role,
        "scheduler_log_basename": path.name,
        "scheduler_log_merged_stdout_stderr": True,
        "scheduler_log_bytes": len(payload),
        "scheduler_log_mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "scheduler_log_sha256": hashlib.sha256(payload).hexdigest(),
        "qacct_failed": failed,
        "qacct_exit_status": exit_status,
        "qacct_terminal_classification": classification,
        "terminal_marker": terminal_marker,
        "first_failure_marker": first_marker,
        "first_failed_stage": first_stage,
        "failure_scope": failure_scope,
        "classification_basis": basis,
        "application_failure_supported": (
            failure_scope == "SCIENTIFIC_OR_APPLICATION"
        ),
        "control_plane_failure_supported": (
            failure_scope == "CONTROL_PLANE_ONLY"
        ),
        "scheduler_failure_supported": failure_scope == "SCHEDULER",
    }


__all__ = [
    "R7CTerminalLogError",
    "fixed_scheduler_log_path",
    "inspect_fixed_terminal_logs",
]
