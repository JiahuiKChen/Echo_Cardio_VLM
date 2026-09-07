#!/usr/bin/env python3
"""Fixed R7F terminal-log inspection for the additive R8U-R7G adjudicator.

The four merged Grid Engine logs are selected only through a fully validated
R7G terminal authority and one of its four derived accounting specifications.
No public API accepts a path, basename, scheduler identity, or owner.  The
projection contains only fixed markers and file metadata; free-form log text
never leaves this module.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pwd
import re
import stat
import sys
from typing import Any, Final, Mapping


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_r8u_r7g_accounting as accounting


RUNTIME_IMPLEMENTATION_COMMIT: Final = (
    "2223d9768a1cc23efbe95a3c5474ea747a383a10"
)
ARRAY_JOB_ID: Final = "7480830"
FINALIZER_JOB_ID: Final = "7480831"
ARRAY_JOB_NAME: Final = "lvef_c3_r8u_r7d_seq_2223d976"
FINALIZER_JOB_NAME: Final = "lvef_c3_r8u_r7d_fin_2223d976"
ARRAY_ROLE: Final = "R8U_R7D_CONTINUATION_ARRAY"
FINALIZER_ROLE: Final = "R8U_R7D_COHORT_FINALIZER"
OWNER: Final = "pkarim"

MAX_FIXED_LOG_BYTES: Final = 4 * 1024 * 1024
SAFE_BASENAME_RE: Final = re.compile(
    r"^lvef_c3_r8u_r7d_(?:seq|fin)_[0-9a-f]{8}[.]o[1-9][0-9]{0,19}"
    r"(?:[.](?:17|18|19))?$"
)
BLOCKED_STATUS_RE: Final = re.compile(
    rb"^R8U_R7D_STATUS=BLOCKED_([A-Z][A-Z0-9_]*)$"
)
FAILED_STAGE_RE: Final = re.compile(
    rb"^R8U_R7D_FAILED_STAGE=([A-Z][A-Z0-9_]*)$"
)
ARRAY_PASS_MARKER: Final = (
    b"R8U_R7D_CONTINUATION_BATCH_STATUS=PASS_BATCH_FINALIZED"
)
FINALIZER_PASS_MARKER: Final = (
    b"R8U_R7D_STATUS=PASS_R8U_R7D_FIXED_CONTINUATION_FINALIZED"
)
PASS_MARKERS: Final = frozenset({ARRAY_PASS_MARKER, FINALIZER_PASS_MARKER})

# Only affirmative, known codes can support a control-plane-only conclusion.
# The adjudicator must additionally prove complete independent batch closure
# before using such a finalizer result as a metadata-only fallback.
CONTROL_PLANE_CODES: Final = frozenset(
    {
        "FINAL_OUTPUT_ALREADY_EXISTS",
        "R8U_R7D_COHORT_OUTPUT_NOT_PRISTINE",
        "R8U_R7D_CONTINUATION_ARRAY_CONTEXT_INVALID",
        "R8U_R7D_CONTINUATION_CLAIM_INVALID",
        "R8U_R7D_CONTINUATION_FINALIZER_CONTEXT_INVALID",
        "R8U_R7D_CONTINUATION_OUTPUT_COLLISION",
        "R8U_R7D_CONTINUATION_SUBMISSION_INVALID",
        "R8U_R7D_CONTINUATION_SUBMISSION_READBACK_INVALID",
        "R8U_R7D_IMPLEMENTATION_ANCESTRY_INVALID",
        "R8U_R7D_IMPLEMENTATION_GIT_AUTHORITY_INVALID",
        "R8U_R7D_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
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
    "CONTINUATION_FINALIZATION",
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
)


class R7GTerminalLogError(RuntimeError):
    """One stable, non-sensitive failure from fixed R7F log inspection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7GTerminalLogError(code)


def _fixed_specs(
    terminal_authority: Mapping[str, Any],
) -> tuple[accounting.FixedAccountingSpec, ...]:
    if not isinstance(terminal_authority, Mapping):
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    try:
        specs = tuple(accounting.fixed_accounting_specs(terminal_authority))
    except Exception as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID"
        ) from exc
    if len(specs) != 4:
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    return specs


def _require_fixed_spec(
    spec: accounting.FixedAccountingSpec,
    terminal_authority: Mapping[str, Any],
) -> accounting.FixedAccountingSpec:
    specs = _fixed_specs(terminal_authority)
    if not isinstance(spec, accounting.FixedAccountingSpec) or spec not in specs:
        _fail("R8U_R7G_TERMINAL_LOG_SCOPE_INVALID")
    expected = {
        "array_job_id": ARRAY_JOB_ID,
        "array_job_name": ARRAY_JOB_NAME,
        "array_role": ARRAY_ROLE,
        "finalizer_job_id": FINALIZER_JOB_ID,
        "finalizer_job_name": FINALIZER_JOB_NAME,
        "finalizer_role": FINALIZER_ROLE,
        "expected_owner": OWNER,
    }
    if any(terminal_authority.get(key) != value for key, value in expected.items()):
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    return spec


def _binding_key(spec: accounting.FixedAccountingSpec) -> str:
    if spec.job_kind == "ARRAY_TASK" and spec.task_id in {17, 18, 19}:
        return f"array_task_{spec.task_id}"
    if spec.job_kind == "NON_ARRAY_FINALIZER" and spec.task_id is None:
        return "finalizer"
    _fail("R8U_R7G_TERMINAL_LOG_SCOPE_INVALID")


def fixed_scheduler_log_path(
    spec: accounting.FixedAccountingSpec,
    *,
    terminal_authority: Mapping[str, Any],
) -> Path:
    """Return the one authority-bound merged-log path for ``spec``."""

    fixed = _require_fixed_spec(spec, terminal_authority)
    root_value = terminal_authority.get("scheduler_log_root")
    basenames = terminal_authority.get("scheduler_log_basenames")
    if not isinstance(root_value, str) or not isinstance(basenames, Mapping):
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    root = Path(root_value)
    key = _binding_key(fixed)
    basename = basenames.get(key)
    expected_keys = {
        "array_task_17",
        "array_task_18",
        "array_task_19",
        "finalizer",
    }
    suffix = "" if fixed.task_id is None else f".{fixed.task_id}"
    expected_basename = (
        f"{fixed.expected_job_name}.o{fixed.job_id}{suffix}"
    )
    if (
        not root.is_absolute()
        or Path(os.path.abspath(root)) != root
        or root.name != "scheduler"
        or set(basenames) != expected_keys
        or not isinstance(basename, str)
        or SAFE_BASENAME_RE.fullmatch(basename) is None
        or Path(basename).name != basename
        or basename != expected_basename
        or fixed.expected_owner != terminal_authority.get("expected_owner")
        or fixed.scheduler_log_basename != basename
        or fixed.scheduler_log_path != root / basename
    ):
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    path = root / basename
    if path.parent != root:
        _fail("R8U_R7G_TERMINAL_LOG_PATH_INVALID")
    return path


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
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _fail("R8U_R7G_TERMINAL_LOG_PATH_INVALID")
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                _fail("R8U_R7G_TERMINAL_LOG_PATH_INVALID")
    except R7GTerminalLogError:
        raise
    except OSError as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc


def _expected_owner_uid(terminal_authority: Mapping[str, Any]) -> int:
    owner = terminal_authority.get("expected_owner")
    if owner != OWNER:
        _fail("R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID")
    try:
        uid = int(pwd.getpwnam(owner).pw_uid)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_OWNER_INVALID"
        ) from exc
    if uid != os.geteuid():
        _fail("R8U_R7G_TERMINAL_LOG_OWNER_INVALID")
    return uid


def _read_fixed_regular(
    path: Path, *, expected_uid: int
) -> tuple[bytes, os.stat_result]:
    """Read a bounded owner-controlled log and prove stable file identity."""

    descriptor: int | None = None
    opened: os.stat_result | None = None
    after: os.stat_result | None = None
    try:
        _require_nonsymlink_components(path)
        before = os.lstat(path)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != expected_uid
            or before.st_nlink != 1
            or mode & 0o600 != 0o600
            or mode & 0o133
            or before.st_size < 0
            or before.st_size > MAX_FIXED_LOG_BYTES
        ):
            _fail("R8U_R7G_TERMINAL_LOG_FILE_INVALID")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened):
            _fail("R8U_R7G_TERMINAL_LOG_FILE_INVALID")
        blocks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("R8U_R7G_TERMINAL_LOG_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except R7GTerminalLogError:
        raise
    except OSError as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if opened is None or after is None:
        _fail("R8U_R7G_TERMINAL_LOG_FILE_INVALID")
    try:
        visible_after = os.lstat(path)
    except OSError as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_NOT_AVAILABLE"
        ) from exc
    if (
        _identity(opened) != _identity(after)
        or _identity(after) != _identity(visible_after)
    ):
        _fail("R8U_R7G_TERMINAL_LOG_FILE_INVALID")
    return b"".join(blocks), after


def _blocked_failure_scope(code: str) -> tuple[str, str]:
    if code in CONTROL_PLANE_CODES:
        return "CONTROL_PLANE_ONLY", "FIXED_CONTROL_PLANE_TERMINAL_MARKER"
    if any(token in code for token in APPLICATION_TOKENS):
        return "SCIENTIFIC_OR_APPLICATION", "FIXED_APPLICATION_TERMINAL_MARKER"
    _fail("R8U_R7G_TERMINAL_LOG_FAILURE_MARKER_UNCLASSIFIED")


def _expected_pass_marker(spec: accounting.FixedAccountingSpec) -> bytes:
    if spec.job_kind == "ARRAY_TASK" and spec.task_id in {17, 18, 19}:
        return ARRAY_PASS_MARKER
    if spec.job_kind == "NON_ARRAY_FINALIZER" and spec.task_id is None:
        return FINALIZER_PASS_MARKER
    _fail("R8U_R7G_TERMINAL_LOG_SCOPE_INVALID")


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
        _fail("R8U_R7G_TERMINAL_LOG_MARKERS_INVALID")
    blocked_marker = (
        f"R8U_R7D_STATUS=BLOCKED_{blocked[0][1]}" if blocked else None
    )
    failed_stage = stages[0][1] if stages else None
    pass_marker = passed[0][1] if passed else None
    return blocked_marker, failed_stage, pass_marker


def inspect_fixed_terminal_logs(
    spec: accounting.FixedAccountingSpec,
    accounting_receipt: Mapping[str, Any],
    *,
    terminal_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate and classify one qacct-proven result from its fixed R7F log."""

    fixed = _require_fixed_spec(spec, terminal_authority)
    if not isinstance(accounting_receipt, Mapping):
        _fail("R8U_R7G_TERMINAL_LOG_ACCOUNTING_INVALID")
    try:
        receipt = accounting.validate_accounting_receipt(
            accounting_receipt,
            fixed,
            terminal_authority=terminal_authority,
        )
    except Exception as exc:
        raise R7GTerminalLogError(
            "R8U_R7G_TERMINAL_LOG_ACCOUNTING_INVALID"
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
        _fail("R8U_R7G_TERMINAL_LOG_ACCOUNTING_INVALID")

    path = fixed_scheduler_log_path(
        fixed, terminal_authority=terminal_authority
    )
    expected_uid = _expected_owner_uid(terminal_authority)
    payload, file_metadata = _read_fixed_regular(
        path, expected_uid=expected_uid
    )
    blocked_marker, failed_stage, pass_marker = _fixed_markers(payload)
    blocked_code = (
        blocked_marker.removeprefix("R8U_R7D_STATUS=BLOCKED_")
        if blocked_marker is not None
        else None
    )
    expected_pass_marker = _expected_pass_marker(fixed).decode("ascii")

    if classification == "PASS":
        if blocked_marker is not None:
            _fail("R8U_R7G_TERMINAL_LOG_PASS_CONTAINS_BLOCKED_MARKER")
        if failed_stage is not None:
            _fail("R8U_R7G_TERMINAL_LOG_PASS_CONTAINS_FAILED_STAGE")
        if pass_marker is None:
            _fail("R8U_R7G_TERMINAL_LOG_PASS_MARKER_MISSING")
        if pass_marker != expected_pass_marker:
            _fail("R8U_R7G_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND")
        failure_scope = "NOT_APPLICABLE"
        basis = "FIXED_JOB_KIND_PASS_TERMINAL_MARKER"
        first_marker = "NOT_AVAILABLE"
        first_stage = "NOT_APPLICABLE"
        result_status = "FIXED_PASS_TERMINAL_LOG_VALIDATED"
    else:
        if blocked_marker is not None and pass_marker is not None:
            _fail("R8U_R7G_TERMINAL_LOG_MARKERS_INVALID")
        if failed_stage is not None and blocked_marker is None:
            _fail("R8U_R7G_TERMINAL_LOG_FAILED_STAGE_WITHOUT_FAILURE_MARKER")
        if pass_marker is not None and pass_marker != expected_pass_marker:
            _fail("R8U_R7G_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND")

        if blocked_code is not None:
            failure_scope, basis = _blocked_failure_scope(blocked_code)
        elif failed != 0:
            failure_scope = "SCHEDULER"
            basis = "QACCT_FAILED_NONZERO"
        elif exit_status != 0:
            failure_scope = "SCIENTIFIC_OR_APPLICATION"
            basis = (
                "QACCT_EXIT_STATUS_NONZERO_AFTER_FIXED_PASS_MARKER"
                if pass_marker is not None
                else "QACCT_EXIT_STATUS_NONZERO"
            )
        else:
            _fail("R8U_R7G_TERMINAL_LOG_FAILURE_MARKER_MISSING")

        if blocked_marker is not None:
            first_marker = blocked_marker
        elif failed != 0:
            first_marker = "QACCT_FAILED_NONZERO"
        else:
            first_marker = "QACCT_EXIT_STATUS_NONZERO"
        first_stage = failed_stage or (
            "CONTINUATION_WORKER_SUBMISSION_VALIDATION"
            if blocked_code == "SCHEDULER_JOB_ROLE_MISMATCH"
            else "NOT_REPORTED"
        )
        result_status = "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED"

    terminal_marker = blocked_marker or pass_marker or "NOT_AVAILABLE"
    mode = stat.S_IMODE(file_metadata.st_mode)
    return {
        "artifact_type": "lvef_c3_r8u_r7g_fixed_terminal_log_projection_v1",
        "status": result_status,
        "runtime_implementation_commit": RUNTIME_IMPLEMENTATION_COMMIT,
        "job_kind": fixed.job_kind,
        "job_id": fixed.job_id,
        "task_id": fixed.task_id,
        "expected_job_role": fixed.expected_job_role,
        "expected_owner": terminal_authority["expected_owner"],
        "scheduler_log_basename": path.name,
        "scheduler_log_merged_stdout_stderr": True,
        "scheduler_log_bytes": len(payload),
        "scheduler_log_mode": f"{mode:04o}",
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
    "ARRAY_PASS_MARKER",
    "FINALIZER_PASS_MARKER",
    "R7GTerminalLogError",
    "fixed_scheduler_log_path",
    "inspect_fixed_terminal_logs",
]
