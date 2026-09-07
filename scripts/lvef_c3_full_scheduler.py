#!/usr/bin/env python3
"""Fail-closed two-submission scheduler for Phase 1I full reconstruction.

This module owns scheduler context and submission only.  The tracked
``lvef_c3_full_sequential.py`` worker owns all scientific validation and work.
Render and preflight modes cannot create an attempt, contact cloud storage, or
invoke qsub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import sys
import time
from typing import Callable, Final, Mapping, NamedTuple, Sequence
import xml.etree.ElementTree as ET


AUTHORITY_WORKTREE: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
)
PRODUCTION_ROOT: Final = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
ECHOPRIME_PYTHON: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
)
SCIENCE_WORKER: Final = AUTHORITY_WORKTREE / "scripts/lvef_c3_full_sequential.py"
ARRAY_RUNNER: Final = (
    AUTHORITY_WORKTREE / "scripts/scc_run_lvef_c3_full_sequential.sh"
)
FINALIZER_RUNNER: Final = (
    AUTHORITY_WORKTREE / "scripts/scc_run_lvef_c3_full_finalizer.sh"
)
QSUB_PATH: Final = Path(
    "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
)
QSTAT_PATH: Final = QSUB_PATH.with_name("qstat")
CANONICAL_SGE_ROOT: Final = QSUB_PATH.parents[2]
APPROVED_SGE_ROOT_ALIAS: Final = Path("/usr/local/sge/sge_root")
EXPECTED_BRANCH: Final = "codex/lvef-multitask-revalidation"
ATTEMPT_RE: Final = re.compile(
    r"^lvef_c3_full_([0-9a-f]{16})_([0-9a-f]{8})$"
)
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
JOB_ID_BYTES_RE: Final = re.compile(rb"[1-9][0-9]{0,19}(?:\n)?")
ARRAY_JOB_ID_BYTES_RE: Final = re.compile(
    rb"([1-9][0-9]{0,19})(?:\.1-19:1)?(?:\n)?"
)
SAFE_ACCOUNT_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CONTROLLED_QSUB_ENVIRONMENT: Final = {
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "LC_ALL": "C",
}
SCHEDULER_CONTEXT_NAMES: Final = frozenset(
    {"SGE_ROOT", "SGE_CELL", "SGE_QMASTER_PORT", "HOME", "USER", "LOGNAME", "SHELL"}
)
WORKER_SCHEDULER_CONTEXT_NAMES: Final = frozenset(
    {"SGE_ROOT", "SGE_CELL", "SGE_QMASTER_PORT", "HOME", "USER", "LOGNAME"}
)
WORKER_ROLE_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
WORKER_JOB_ID_RE: Final = re.compile(r"[1-9][0-9]{0,19}")
WORKER_TASK_ID_RE: Final = re.compile(r"[1-9][0-9]{0,9}")
WORKER_PASSWD_LOOKUP_STATUSES: Final = frozenset(
    {
        "PASS",
        "LOOKUP_UNAVAILABLE",
        "NAME_MISMATCH",
        "HOME_MISMATCH",
        "SHELL_MISMATCH",
    }
)
WORKER_DIAGNOSTIC_CLASSIFICATIONS: Final = frozenset(
    {
        "PASS",
        "SCHEDULER_ACCOUNT_LOOKUP_UNAVAILABLE",
        "SCHEDULER_ACCOUNT_NAME_MISMATCH",
        "SCHEDULER_ACCOUNT_HOME_MISMATCH",
        "SCHEDULER_ACCOUNT_SHELL_MISMATCH",
        "SCHEDULER_ENV_USER_MISMATCH",
        "SCHEDULER_ENV_LOGNAME_MISMATCH",
        "SCHEDULER_ENV_HOME_MISMATCH",
        "SCHEDULER_ENV_SHELL_MISMATCH",
    }
)
R8U_R7D_QSTAT_MAXIMUM_OBSERVATIONS: Final = 3
R8U_R7D_QSTAT_MAXIMUM_ELAPSED_SECONDS: Final = 15.0
R8U_R7D_QSTAT_COMMAND_TIMEOUT_SECONDS: Final = 3.0
R8U_R7D_QSTAT_RETRY_INTERVAL_SECONDS: Final = 2.0
R8U_R7D_QSTAT_PASS_CLASSIFICATIONS: Final = frozenset(
    {
        "PASS_QSTAT_SELF_RECORD_MATCHED",
        "PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE",
        "PASS_QSTAT_TRANSITIONAL_STATE",
    }
)
R8U_R7D_QSTAT_BLOCKING_CLASSIFICATIONS: Final = frozenset(
    {
        "BLOCKED_QSTAT_SELF_JOB_ID_CONTRADICTION",
        "BLOCKED_QSTAT_SELF_TASK_ID_CONTRADICTION",
        "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION",
        "BLOCKED_QSTAT_SELF_JOB_NAME_CONTRADICTION",
        "BLOCKED_QSTAT_SELF_MULTIPLE_RECORDS",
        "BLOCKED_QSTAT_SELF_OBSERVATION_FAILED",
        "BLOCKED_QSTAT_SELF_XML_INVALID",
    }
)
SCIENCE_MARKERS: Final = {
    "--validate-installation": "FULL_C3_INSTALLATION=PASS",
    "--preflight-only": "FULL_C3_NO_BODY_PREFLIGHT=PASS",
    "--claim-submission": "FULL_C3_SUBMISSION_CLAIM=READY",
    "--validate-claimed-submission": "FULL_C3_MATERIALIZED_CLAIM_READBACK=PASS",
}
QSUB_ENVIRONMENT_SHA256_NAME: Final = "LVEF_C3_QSUB_ENVIRONMENT_SHA256"
QSUB_ENVIRONMENT_BOUND_SCIENCE_MODES: Final = frozenset(
    {"--claim-submission", "--validate-claimed-submission"}
)
SCIENCE_CONTROL_FAILURE_MAXIMUM_BYTES: Final = 512
SCIENCE_CONTROL_FAILURE_CODE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{1,127}")
SCIENCE_CONTROL_ZERO_EFFECT_LINES: Final = (
    "CLOUD_REQUESTS=0",
    "QSUB_SUBMISSIONS=0",
    "DICOM_BODY_READS=0",
    "GPU_EXECUTIONS=0",
)
SUBMISSION_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "array_job_name", "finalizer_job_name",
        "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
        "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
        "array_qsub_stdout_bytes", "array_qsub_stdout_sha256",
        "array_qsub_stderr_bytes", "array_qsub_stderr_sha256",
        "array_qsub_exit_status", "finalizer_qsub_stdout_bytes",
        "finalizer_qsub_stdout_sha256", "finalizer_qsub_stderr_bytes",
        "finalizer_qsub_stderr_sha256", "finalizer_qsub_exit_status",
        "scheduler_submission_count", "scheduler_submission_maximum",
        "array_task_range", "array_max_concurrency",
        "finalizer_held_on_array", "whole_batch_retry_authorized",
        "third_scheduler_submission_reachable", "cloud_requests",
        "dicom_body_reads_by_submitter", "gpu_executions_by_submitter",
    }
)


class FullSchedulerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise FullSchedulerError(code)


def _safe_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
    )


def _resolve(path: Path, code: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise FullSchedulerError(code) from exc


def _lstat(path: Path, code: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise FullSchedulerError(code) from exc


def _require_nonsymlink_components(path: Path, code: str) -> None:
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        info = _lstat(cursor, code)
        if stat.S_ISLNK(info.st_mode):
            _fail(code)


def _validate_pinned_sge_root() -> Path:
    _require_nonsymlink_components(CANONICAL_SGE_ROOT, "SGE_ROOT_TARGET_INVALID")
    info = _lstat(CANONICAL_SGE_ROOT, "SGE_ROOT_TARGET_INVALID")
    resolved = _resolve(CANONICAL_SGE_ROOT, "SGE_ROOT_TARGET_INVALID")
    if not stat.S_ISDIR(info.st_mode) or resolved != CANONICAL_SGE_ROOT:
        _fail("SGE_ROOT_TARGET_INVALID")
    return resolved


def _validate_approved_sge_alias_control() -> None:
    for component in (
        Path("/usr"),
        Path("/usr/local"),
        Path("/usr/local/sge"),
        APPROVED_SGE_ROOT_ALIAS,
    ):
        info = _lstat(component, "SGE_ROOT_ALIAS_CONTROL_INVALID")
        if info.st_uid != 0 or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        ):
            _fail("SGE_ROOT_ALIAS_CONTROL_INVALID")
        parent = _resolve(component.parent, "SGE_ROOT_ALIAS_CONTROL_INVALID")
        parent_info = _lstat(parent, "SGE_ROOT_ALIAS_CONTROL_INVALID")
        if (
            parent_info.st_uid != 0
            or not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_IMODE(parent_info.st_mode) & 0o022
        ):
            _fail("SGE_ROOT_ALIAS_CONTROL_INVALID")


def validate_sge_root_authority(value: object) -> str:
    if (
        not _safe_text(value)
        or not Path(str(value)).is_absolute()
        or any(part in {".", ".."} for part in str(value).split("/"))
        or value not in {str(CANONICAL_SGE_ROOT), str(APPROVED_SGE_ROOT_ALIAS)}
    ):
        _fail("SGE_ROOT_UNAPPROVED_LEXICAL_PATH")
    canonical = _validate_pinned_sge_root()
    if value == str(CANONICAL_SGE_ROOT):
        return "SGE_ROOT_CANONICAL_INPUT"
    if _resolve(APPROVED_SGE_ROOT_ALIAS, "SGE_ROOT_ALIAS_TARGET_MISMATCH") != canonical:
        _fail("SGE_ROOT_ALIAS_TARGET_MISMATCH")
    _validate_approved_sge_alias_control()
    if _resolve(APPROVED_SGE_ROOT_ALIAS, "SGE_ROOT_ALIAS_TARGET_MISMATCH") != canonical:
        _fail("SGE_ROOT_ALIAS_TARGET_MISMATCH")
    return "SGE_ROOT_APPROVED_ALIAS_RESOLVED"


def build_qsub_environment(
    source: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], str]:
    observed = os.environ if source is None else source
    input_class = validate_sge_root_authority(observed.get("SGE_ROOT"))
    result = dict(CONTROLLED_QSUB_ENVIRONMENT)
    result["SGE_ROOT"] = str(CANONICAL_SGE_ROOT)
    cell = observed.get("SGE_CELL")
    if cell is not None:
        if SAFE_ACCOUNT_RE.fullmatch(cell) is None:
            _fail("SCHEDULER_CONTEXT_INVALID")
        result["SGE_CELL"] = cell
    port = observed.get("SGE_QMASTER_PORT")
    if port is not None:
        if re.fullmatch(r"[1-9][0-9]{0,4}", port) is None or int(port) > 65535:
            _fail("SCHEDULER_CONTEXT_INVALID")
        result["SGE_QMASTER_PORT"] = port
    try:
        account = pwd.getpwuid(os.geteuid())
    except (KeyError, OSError) as exc:
        raise FullSchedulerError("SCHEDULER_IDENTITY_INVALID") from exc
    for name in ("HOME", "SHELL"):
        value = observed.get(name)
        if value is None:
            _fail("SCHEDULER_CONTEXT_MISSING")
        path = Path(value)
        if (
            not _safe_text(value)
            or not path.is_absolute()
            or Path(os.path.abspath(path)) != path
        ):
            _fail("SCHEDULER_CONTEXT_INVALID")
        result[name] = value
    for name in ("USER", "LOGNAME"):
        value = observed.get(name)
        if value is None:
            _fail("SCHEDULER_CONTEXT_MISSING")
        if SAFE_ACCOUNT_RE.fullmatch(value) is None:
            _fail("SCHEDULER_CONTEXT_INVALID")
        result[name] = value
    if result["USER"] != result["LOGNAME"]:
        _fail("SCHEDULER_CONTEXT_CONFLICT")
    if (
        result["USER"] != account.pw_name
        or Path(result["HOME"]) != Path(account.pw_dir)
        or Path(result["SHELL"]) != Path(account.pw_shell)
    ):
        _fail("SCHEDULER_IDENTITY_INVALID")
    allowed = set(CONTROLLED_QSUB_ENVIRONMENT) | SCHEDULER_CONTEXT_NAMES
    if set(result) - allowed:
        _fail("QSUB_ENVIRONMENT_NOT_CLOSED")
    return result, input_class


class WorkerSchedulerDiagnostics(NamedTuple):
    """Aggregate-safe observations about one compute-worker context."""

    user_present: bool
    logname_present: bool
    home_present: bool
    shell_present: bool
    observed_user_match: bool
    observed_logname_match: bool
    observed_home_match: bool
    observed_shell_match: bool
    passwd_lookup_available: bool
    passwd_name_match: bool
    passwd_home_match: bool
    passwd_shell_match: bool
    passwd_lookup_status: str
    effective_uid_match: bool
    job_id_match: bool
    task_context_match: bool
    job_role_match: bool
    runner_sha256_match: bool
    python_sha256_match: bool
    implementation_commit_match: bool
    qsub_environment_sha256_match: bool
    classifications: tuple[str, ...]


class WorkerSchedulerContext(NamedTuple):
    """Closed subprocess environment and non-secret worker diagnostics."""

    environment: dict[str, str]
    diagnostics: WorkerSchedulerDiagnostics


class R8UR7DQstatObservation(NamedTuple):
    """One aggregate-safe observation of the current scheduler task."""

    observation_ordinal: int
    record_present: bool
    unique: bool
    job_id_match: bool | None
    task_id_match: bool | None
    owner_match: bool | None
    full_job_name_match: bool | None
    state_token: str | None


class R8UR7DQstatDiagnostic(NamedTuple):
    """Bounded qstat contradiction diagnostic, never a worker identity source."""

    classification: str
    observation_count: int
    row_ever_visible: bool
    job_id_equality: bool
    task_id_equality: bool
    owner_equality: bool
    full_job_name_equality: bool
    observed_scheduler_state_category: str
    observations: tuple[R8UR7DQstatObservation, ...]


class _R8UR7DQstatRecord(NamedTuple):
    """Private full-XML scheduler record; never written to aggregate output."""

    job_id: str
    full_job_name: str
    owner: str
    state_token: str
    tasks: str | None


def _canonical_absolute_path(value: object) -> bool:
    if not _safe_text(value):
        return False
    text = str(value)
    path = Path(text)
    return (
        path.is_absolute()
        and all(part not in {".", ".."} for part in text.split("/"))
        and os.path.normpath(text) == text
        and Path(os.path.abspath(text)) == path
    )


def _worker_binding_match(
    expected: object, observed: object, pattern: re.Pattern[str]
) -> bool:
    return (
        isinstance(expected, str)
        and isinstance(observed, str)
        and pattern.fullmatch(expected) is not None
        and pattern.fullmatch(observed) is not None
        and expected == observed
    )


def _validate_sealed_qsub_environment(
    environment: Mapping[str, str],
    *,
    expected_scheduler_username: str,
    canonical_home: str,
) -> str:
    """Validate a submitter-built environment without consulting worker identity."""

    required = set(CONTROLLED_QSUB_ENVIRONMENT) | {
        "SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL"
    }
    allowed = required | {"SGE_CELL", "SGE_QMASTER_PORT"}
    if (
        not isinstance(environment, Mapping)
        or not required <= set(environment)
        or set(environment) - allowed
        or any(
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or "\x00" in name + value
            or "\n" in name + value
            or "\r" in name + value
            for name, value in environment.items()
        )
        or any(
            environment.get(name) != value
            for name, value in CONTROLLED_QSUB_ENVIRONMENT.items()
        )
        or environment.get("SGE_ROOT") != str(CANONICAL_SGE_ROOT)
        or environment.get("USER") != expected_scheduler_username
        or environment.get("LOGNAME") != expected_scheduler_username
        or environment.get("HOME") != canonical_home
        or not _canonical_absolute_path(environment.get("SHELL"))
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    cell = environment.get("SGE_CELL")
    if cell is not None and SAFE_ACCOUNT_RE.fullmatch(cell) is None:
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    port = environment.get("SGE_QMASTER_PORT")
    if port is not None and (
        re.fullmatch(r"[1-9][0-9]{0,4}", port) is None or int(port) > 65535
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    try:
        input_class = validate_sge_root_authority(environment["SGE_ROOT"])
    except FullSchedulerError as exc:
        raise FullSchedulerError(
            "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH"
        ) from exc
    if input_class != "SGE_ROOT_CANONICAL_INPUT":
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    return input_class


def build_worker_scheduler_context(
    *,
    expected_effective_uid: int,
    expected_scheduler_username: str,
    canonical_home: str,
    expected_job_id: str,
    expected_job_role: str,
    observed_job_role: str,
    expected_qsub_environment_sha256: str,
    sealed_qsub_environment: Mapping[str, str],
    expected_implementation_commit: str,
    observed_implementation_commit: str,
    expected_runner_sha256: str,
    observed_runner_sha256: str,
    expected_python_sha256: str,
    observed_python_sha256: str,
    source_environment: Mapping[str, str] | None = None,
    expected_task_id: str | None = None,
) -> WorkerSchedulerContext:
    """Build a compute-worker context from kernel and sealed authorities.

    Unlike :func:`build_qsub_environment`, this function never treats ambient
    account variables or passwd/NSS as controlling identity.  It returns only
    a closed subprocess environment and equality-only diagnostics.  Callers
    must measure the observed commit and executable hashes independently and
    pass a fixed dispatch role; qstat role validation remains the next gate.
    """

    observed_source = os.environ if source_environment is None else source_environment
    if not isinstance(observed_source, Mapping):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")
    observed = dict(observed_source)
    if not isinstance(sealed_qsub_environment, Mapping):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    sealed_environment = dict(sealed_qsub_environment)
    if (
        isinstance(expected_effective_uid, bool)
        or not isinstance(expected_effective_uid, int)
        or expected_effective_uid < 0
        or not isinstance(expected_scheduler_username, str)
        or SAFE_ACCOUNT_RE.fullmatch(expected_scheduler_username) is None
        or not _canonical_absolute_path(canonical_home)
    ):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")

    effective_uid_match = os.geteuid() == expected_effective_uid
    if not effective_uid_match:
        _fail("SCHEDULER_EFFECTIVE_UID_MISMATCH")

    observed_job_id = observed.get("JOB_ID")
    job_id_match = (
        isinstance(expected_job_id, str)
        and isinstance(observed_job_id, str)
        and WORKER_JOB_ID_RE.fullmatch(expected_job_id) is not None
        and WORKER_JOB_ID_RE.fullmatch(observed_job_id) is not None
        and expected_job_id == observed_job_id
    )
    if not job_id_match:
        _fail("SCHEDULER_JOB_ID_BINDING_MISMATCH")

    observed_task_id = observed.get("SGE_TASK_ID")
    if expected_task_id is None:
        task_context_match = observed_task_id in {None, "", "undefined"}
    else:
        task_context_match = (
            isinstance(expected_task_id, str)
            and isinstance(observed_task_id, str)
            and WORKER_TASK_ID_RE.fullmatch(expected_task_id) is not None
            and WORKER_TASK_ID_RE.fullmatch(observed_task_id) is not None
            and expected_task_id == observed_task_id
        )
    if not task_context_match:
        _fail("SCHEDULER_TASK_ID_BINDING_MISMATCH")

    job_role_match = _worker_binding_match(
        expected_job_role, observed_job_role, WORKER_ROLE_RE
    )
    if not job_role_match:
        _fail("SCHEDULER_JOB_ROLE_MISMATCH")

    runner_sha256_match = _worker_binding_match(
        expected_runner_sha256, observed_runner_sha256, SHA256_RE
    )
    if not runner_sha256_match:
        _fail("SCHEDULER_RUNNER_AUTHORITY_MISMATCH")
    python_sha256_match = _worker_binding_match(
        expected_python_sha256, observed_python_sha256, SHA256_RE
    )
    if not python_sha256_match:
        _fail("SCHEDULER_PYTHON_AUTHORITY_MISMATCH")
    implementation_commit_match = _worker_binding_match(
        expected_implementation_commit, observed_implementation_commit, COMMIT_RE
    )
    if not implementation_commit_match:
        _fail("SCHEDULER_IMPLEMENTATION_COMMIT_MISMATCH")

    try:
        observed_qsub_environment_sha256 = qsub_environment_sha256(
            sealed_environment
        )
    except FullSchedulerError as exc:
        raise FullSchedulerError(
            "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH"
        ) from exc
    qsub_environment_sha256_match = (
        isinstance(expected_qsub_environment_sha256, str)
        and SHA256_RE.fullmatch(expected_qsub_environment_sha256) is not None
        and observed_qsub_environment_sha256 == expected_qsub_environment_sha256
    )
    if not qsub_environment_sha256_match:
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    _validate_sealed_qsub_environment(
        sealed_environment,
        expected_scheduler_username=expected_scheduler_username,
        canonical_home=canonical_home,
    )

    sealed_shell = sealed_environment["SHELL"]
    user_present = "USER" in observed
    logname_present = "LOGNAME" in observed
    home_present = "HOME" in observed
    shell_present = "SHELL" in observed
    observed_user_match = observed.get("USER") == expected_scheduler_username
    observed_logname_match = observed.get("LOGNAME") == expected_scheduler_username
    observed_home_match = observed.get("HOME") == canonical_home
    observed_shell_match = observed.get("SHELL") == sealed_shell

    passwd_lookup_available = True
    try:
        account = pwd.getpwuid(os.geteuid())
    except (KeyError, OSError):
        passwd_lookup_available = False
        passwd_name_match = False
        passwd_home_match = False
        passwd_shell_match = False
        passwd_lookup_status = "LOOKUP_UNAVAILABLE"
    else:
        passwd_name_match = account.pw_name == expected_scheduler_username
        passwd_home_match = account.pw_dir == canonical_home
        passwd_shell_match = account.pw_shell == sealed_shell
        if not passwd_name_match:
            passwd_lookup_status = "NAME_MISMATCH"
        elif not passwd_home_match:
            passwd_lookup_status = "HOME_MISMATCH"
        elif not passwd_shell_match:
            passwd_lookup_status = "SHELL_MISMATCH"
        else:
            passwd_lookup_status = "PASS"
    if passwd_lookup_status not in WORKER_PASSWD_LOOKUP_STATUSES:
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")

    classifications: list[str] = []
    if not passwd_lookup_available:
        classifications.append("SCHEDULER_ACCOUNT_LOOKUP_UNAVAILABLE")
    elif passwd_lookup_status != "PASS":
        classifications.append(
            {
                "NAME_MISMATCH": "SCHEDULER_ACCOUNT_NAME_MISMATCH",
                "HOME_MISMATCH": "SCHEDULER_ACCOUNT_HOME_MISMATCH",
                "SHELL_MISMATCH": "SCHEDULER_ACCOUNT_SHELL_MISMATCH",
            }[passwd_lookup_status]
        )
    for matches, code in (
        (observed_user_match, "SCHEDULER_ENV_USER_MISMATCH"),
        (observed_logname_match, "SCHEDULER_ENV_LOGNAME_MISMATCH"),
        (observed_home_match, "SCHEDULER_ENV_HOME_MISMATCH"),
        (observed_shell_match, "SCHEDULER_ENV_SHELL_MISMATCH"),
    ):
        if not matches:
            classifications.append(code)
    if not classifications:
        classifications.append("PASS")
    if any(item not in WORKER_DIAGNOSTIC_CLASSIFICATIONS for item in classifications):
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")

    environment = dict(CONTROLLED_QSUB_ENVIRONMENT)
    environment["SGE_ROOT"] = str(CANONICAL_SGE_ROOT)
    for name in ("SGE_CELL", "SGE_QMASTER_PORT"):
        if name in sealed_environment:
            environment[name] = sealed_environment[name]
    environment["USER"] = expected_scheduler_username
    environment["LOGNAME"] = expected_scheduler_username
    environment["HOME"] = canonical_home
    if set(environment) - (
        set(CONTROLLED_QSUB_ENVIRONMENT) | WORKER_SCHEDULER_CONTEXT_NAMES
    ) or "SHELL" in environment:
        _fail("SCHEDULER_WORKER_CONTEXT_INVALID")

    diagnostics = WorkerSchedulerDiagnostics(
        user_present=user_present,
        logname_present=logname_present,
        home_present=home_present,
        shell_present=shell_present,
        observed_user_match=observed_user_match,
        observed_logname_match=observed_logname_match,
        observed_home_match=observed_home_match,
        observed_shell_match=observed_shell_match,
        passwd_lookup_available=passwd_lookup_available,
        passwd_name_match=passwd_name_match,
        passwd_home_match=passwd_home_match,
        passwd_shell_match=passwd_shell_match,
        passwd_lookup_status=passwd_lookup_status,
        effective_uid_match=effective_uid_match,
        job_id_match=job_id_match,
        task_context_match=task_context_match,
        job_role_match=job_role_match,
        runner_sha256_match=runner_sha256_match,
        python_sha256_match=python_sha256_match,
        implementation_commit_match=implementation_commit_match,
        qsub_environment_sha256_match=qsub_environment_sha256_match,
        classifications=tuple(classifications),
    )
    return WorkerSchedulerContext(environment=environment, diagnostics=diagnostics)


def _r8u_r7d_parse_qstat_xml(payload: bytes) -> tuple[_R8UR7DQstatRecord, ...]:
    """Parse only full qstat XML, retaining no data in public diagnostics."""

    if (
        not payload
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("BLOCKED_QSTAT_SELF_XML_INVALID")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FullSchedulerError("BLOCKED_QSTAT_SELF_XML_INVALID") from exc

    def local_name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    direct_children = [local_name(element) for element in list(root)]
    if (
        local_name(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(name not in {"queue_info", "job_info"} for name in direct_children)
    ):
        _fail("BLOCKED_QSTAT_SELF_XML_INVALID")

    records: list[_R8UR7DQstatRecord] = []
    for job in root.iter():
        if local_name(job) != "job_list":
            continue
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(local_name(child), []).append(child.text or "")
        if any(
            len(fields.get(name, ())) != 1
            for name in ("JB_job_number", "JB_name", "JB_owner", "state")
        ) or len(fields.get("tasks", ())) > 1:
            _fail("BLOCKED_QSTAT_SELF_XML_INVALID")
        job_id = fields["JB_job_number"][0]
        full_job_name = fields["JB_name"][0]
        owner = fields["JB_owner"][0]
        state_token = fields["state"][0]
        tasks = fields.get("tasks", [None])[0]
        if (
            WORKER_JOB_ID_RE.fullmatch(job_id) is None
            or not _safe_text(full_job_name)
            or len(full_job_name) > 256
            or SAFE_ACCOUNT_RE.fullmatch(owner) is None
            or re.fullmatch(r"[A-Za-z]{1,8}", state_token) is None
            or (
                tasks is not None
                and (
                    len(tasks) > 256
                    or "\x00" in tasks
                    or "\n" in tasks
                    or "\r" in tasks
                )
            )
        ):
            _fail("BLOCKED_QSTAT_SELF_XML_INVALID")
        records.append(
            _R8UR7DQstatRecord(
                job_id=job_id,
                full_job_name=full_job_name,
                owner=owner,
                state_token=state_token,
                tasks=tasks,
            )
        )
    return tuple(records)


def _r8u_r7d_task_matches(
    tasks: str | None, expected_task_id: str | None
) -> bool:
    """Return membership without confusing other array partitions as duplicates."""

    if expected_task_id is None:
        return tasks in {None, "", "undefined"}
    if tasks in {None, "", "undefined"}:
        return False
    assert tasks is not None
    number = r"[1-9][0-9]{0,9}"
    segment_pattern = re.compile(
        rf"(?P<first>{number})(?:-(?P<last>{number})(?::(?P<step>{number}))?)?"
    )
    expected = int(expected_task_id)
    matched = False
    for segment in tasks.split(","):
        match = segment_pattern.fullmatch(segment)
        if match is None:
            _fail("BLOCKED_QSTAT_SELF_XML_INVALID")
        first = int(match.group("first"))
        last_text = match.group("last")
        last = first if last_text is None else int(last_text)
        step_text = match.group("step")
        step = 1 if step_text is None else int(step_text)
        if first > last:
            _fail("BLOCKED_QSTAT_SELF_XML_INVALID")
        if first <= expected <= last and (expected - first) % step == 0:
            matched = True
    return matched


def _r8u_r7d_qstat_observation(
    records: Sequence[_R8UR7DQstatRecord],
    *,
    expected_job_id: str,
    expected_task_id: str | None,
    expected_owner: str,
    expected_full_job_name: str,
    observation_ordinal: int,
) -> tuple[R8UR7DQstatObservation, str | None]:
    """Project one snapshot and block only on a concrete self contradiction."""

    related = [
        record
        for record in records
        if record.job_id == expected_job_id
        or record.full_job_name == expected_full_job_name
    ]
    current_records = [
        record
        for record in related
        if _r8u_r7d_task_matches(record.tasks, expected_task_id)
    ]
    if len(current_records) > 1:
        return (
            R8UR7DQstatObservation(
                observation_ordinal=observation_ordinal,
                record_present=True,
                unique=False,
                job_id_match=all(
                    record.job_id == expected_job_id for record in current_records
                ),
                task_id_match=True,
                owner_match=all(
                    record.owner == expected_owner for record in current_records
                ),
                full_job_name_match=all(
                    record.full_job_name == expected_full_job_name
                    for record in current_records
                ),
                state_token=None,
            ),
            "BLOCKED_QSTAT_SELF_MULTIPLE_RECORDS",
        )

    # Evaluate fields that independently tie a row to this submission before
    # treating a nonmatching task partition as temporary absence.  Otherwise
    # an exact full name with the wrong job ID (or the inverse) can disappear
    # behind the task-membership filter.  Exact ID/name/owner sibling rows are
    # handled by the task logic below and remain nonduplicates.
    field_contradictions = (
        (
            [
                record
                for record in related
                if record.full_job_name == expected_full_job_name
                and record.job_id != expected_job_id
            ],
            "BLOCKED_QSTAT_SELF_JOB_ID_CONTRADICTION",
        ),
        (
            [
                record
                for record in related
                if record.job_id == expected_job_id
                and record.full_job_name != expected_full_job_name
            ],
            "BLOCKED_QSTAT_SELF_JOB_NAME_CONTRADICTION",
        ),
        (
            [
                record
                for record in related
                if record.job_id == expected_job_id
                and record.full_job_name == expected_full_job_name
                and record.owner != expected_owner
            ],
            "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION",
        ),
    )
    for contradictory, classification in field_contradictions:
        if not contradictory:
            continue
        record = contradictory[0]
        unique = len(contradictory) == 1
        return (
            R8UR7DQstatObservation(
                observation_ordinal=observation_ordinal,
                record_present=True,
                unique=unique,
                job_id_match=all(
                    item.job_id == expected_job_id for item in contradictory
                ),
                task_id_match=all(
                    _r8u_r7d_task_matches(item.tasks, expected_task_id)
                    for item in contradictory
                ),
                owner_match=all(
                    item.owner == expected_owner for item in contradictory
                ),
                full_job_name_match=all(
                    item.full_job_name == expected_full_job_name
                    for item in contradictory
                ),
                state_token=record.state_token if unique else None,
            ),
            classification,
        )
    if not current_records:
        # A running record for the exact array job but a different task is a
        # concrete contradiction.  Pending rows for sibling partitions are
        # not: those may remain visible while this task's row is in transit.
        active_wrong_task = [
            record
            for record in related
            if expected_task_id is not None
            and record.job_id == expected_job_id
            and record.full_job_name == expected_full_job_name
            and record.owner == expected_owner
            and record.state_token in {"r", "t", "Rr"}
        ]
        if expected_task_id is None:
            active_wrong_task = [
                record
                for record in related
                if record.job_id == expected_job_id
                and record.full_job_name == expected_full_job_name
                and record.owner == expected_owner
                and record.tasks not in {None, "", "undefined"}
            ]
        if active_wrong_task:
            record = active_wrong_task[0]
            return (
                R8UR7DQstatObservation(
                    observation_ordinal=observation_ordinal,
                    record_present=True,
                    unique=len(active_wrong_task) == 1,
                    job_id_match=True,
                    task_id_match=False,
                    owner_match=True,
                    full_job_name_match=True,
                    state_token=(
                        record.state_token if len(active_wrong_task) == 1 else None
                    ),
                ),
                "BLOCKED_QSTAT_SELF_TASK_ID_CONTRADICTION",
            )
        return (
            R8UR7DQstatObservation(
                observation_ordinal=observation_ordinal,
                record_present=False,
                unique=False,
                job_id_match=None,
                task_id_match=None,
                owner_match=None,
                full_job_name_match=None,
                state_token=None,
            ),
            None,
        )

    record = current_records[0]
    observation = R8UR7DQstatObservation(
        observation_ordinal=observation_ordinal,
        record_present=True,
        unique=True,
        job_id_match=record.job_id == expected_job_id,
        task_id_match=True,
        owner_match=record.owner == expected_owner,
        full_job_name_match=record.full_job_name == expected_full_job_name,
        state_token=record.state_token,
    )
    if not observation.job_id_match:
        classification = "BLOCKED_QSTAT_SELF_JOB_ID_CONTRADICTION"
    elif not observation.owner_match:
        classification = "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION"
    elif not observation.full_job_name_match:
        classification = "BLOCKED_QSTAT_SELF_JOB_NAME_CONTRADICTION"
    else:
        classification = None
    return observation, classification


def _r8u_r7d_qstat_diagnostic(
    classification: str,
    observations: Sequence[R8UR7DQstatObservation],
    state_category: str,
) -> R8UR7DQstatDiagnostic:
    if classification not in (
        R8U_R7D_QSTAT_PASS_CLASSIFICATIONS
        | R8U_R7D_QSTAT_BLOCKING_CLASSIFICATIONS
    ):
        _fail("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED")
    return R8UR7DQstatDiagnostic(
        classification=classification,
        observation_count=len(observations),
        row_ever_visible=any(item.record_present for item in observations),
        job_id_equality=not any(
            item.job_id_match is False for item in observations
        ),
        task_id_equality=not any(
            item.task_id_match is False for item in observations
        ),
        owner_equality=not any(item.owner_match is False for item in observations),
        full_job_name_equality=not any(
            item.full_job_name_match is False for item in observations
        ),
        observed_scheduler_state_category=state_category,
        observations=tuple(observations),
    )


def _r8u_r7d_clock_value(clock: Callable[[], float]) -> float:
    try:
        value = float(clock())
    except (TypeError, ValueError, OverflowError) as exc:
        raise FullSchedulerError("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED") from exc
    if (
        value < 0
        or value == float("inf")
        or value == float("-inf")
        or value != value
    ):
        _fail("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED")
    return value


def diagnose_r8u_r7d_qstat_self(
    *,
    environment: Mapping[str, str],
    expected_job_id: str,
    expected_task_id: str | None,
    expected_owner: str,
    expected_full_job_name: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> R8UR7DQstatDiagnostic:
    """Use bounded full-XML qstat observations only as a contradiction detector.

    Kernel identity and sealed submission authorities must be validated by the
    caller before this diagnostic runs.  Absence and state alone never become
    identity failures.  No raw job, owner, or name values leave this function.
    """

    if (
        not isinstance(environment, Mapping)
        or not isinstance(expected_job_id, str)
        or WORKER_JOB_ID_RE.fullmatch(expected_job_id) is None
        or not (
            expected_task_id is None
            or (
                isinstance(expected_task_id, str)
                and expected_task_id in {"17", "18", "19"}
            )
        )
        or not isinstance(expected_owner, str)
        or SAFE_ACCOUNT_RE.fullmatch(expected_owner) is None
        or not isinstance(expected_full_job_name, str)
        or WORKER_ROLE_RE.fullmatch(expected_full_job_name) is None
        or environment.get("USER") != expected_owner
    ):
        _fail("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED")

    started = _r8u_r7d_clock_value(clock)
    deadline = started + R8U_R7D_QSTAT_MAXIMUM_ELAPSED_SECONDS
    observations: list[R8UR7DQstatObservation] = []

    for ordinal in range(1, R8U_R7D_QSTAT_MAXIMUM_OBSERVATIONS + 1):
        now = _r8u_r7d_clock_value(clock)
        remaining = deadline - now
        if remaining <= 0:
            break
        command = [str(QSTAT_PATH), "-xml", "-u", expected_owner]
        try:
            completed = runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=dict(environment),
                timeout=min(R8U_R7D_QSTAT_COMMAND_TIMEOUT_SECONDS, remaining),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FullSchedulerError("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED") from exc
        if (
            completed.returncode != 0
            or not isinstance(completed.stdout, bytes)
            or not isinstance(completed.stderr, bytes)
            or completed.stderr
        ):
            _fail("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED")
        records = _r8u_r7d_parse_qstat_xml(completed.stdout)
        observation, blocking_classification = _r8u_r7d_qstat_observation(
            records,
            expected_job_id=expected_job_id,
            expected_task_id=expected_task_id,
            expected_owner=expected_owner,
            expected_full_job_name=expected_full_job_name,
            observation_ordinal=ordinal,
        )
        observations.append(observation)
        if blocking_classification is not None:
            state_category = (
                "MULTIPLE_RECORDS"
                if observation.state_token is None
                else (
                    "RUNNING_R"
                    if observation.state_token == "r"
                    else "TRANSITIONAL_NON_R"
                )
            )
            return _r8u_r7d_qstat_diagnostic(
                blocking_classification, observations, state_category
            )
        if observation.record_present:
            assert observation.state_token is not None
            if observation.state_token == "r":
                classification = "PASS_QSTAT_SELF_RECORD_MATCHED"
                state_category = "RUNNING_R"
            else:
                classification = "PASS_QSTAT_TRANSITIONAL_STATE"
                state_category = "TRANSITIONAL_NON_R"
            return _r8u_r7d_qstat_diagnostic(
                classification, observations, state_category
            )
        if ordinal == R8U_R7D_QSTAT_MAXIMUM_OBSERVATIONS:
            break
        remaining = deadline - _r8u_r7d_clock_value(clock)
        if remaining <= 0:
            break
        try:
            sleeper(min(R8U_R7D_QSTAT_RETRY_INTERVAL_SECONDS, remaining))
        except (OSError, ValueError, OverflowError) as exc:
            raise FullSchedulerError(
                "BLOCKED_QSTAT_SELF_OBSERVATION_FAILED"
            ) from exc

    if not observations:
        _fail("BLOCKED_QSTAT_SELF_OBSERVATION_FAILED")
    return _r8u_r7d_qstat_diagnostic(
        "PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE", observations, "NOT_OBSERVED"
    )


def validate_scheduler_tools() -> None:
    if QSUB_PATH != CANONICAL_SGE_ROOT / "bin/linux-x64/qsub":
        _fail("SCHEDULER_TOOL_AUTHORITY_INVALID")
    if QSTAT_PATH != CANONICAL_SGE_ROOT / "bin/linux-x64/qstat":
        _fail("SCHEDULER_TOOL_AUTHORITY_INVALID")
    for path in (QSUB_PATH, QSTAT_PATH):
        _require_nonsymlink_components(path, "SCHEDULER_TOOL_AUTHORITY_INVALID")
        info = _lstat(path, "SCHEDULER_TOOL_AUTHORITY_INVALID")
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022
            or not stat.S_IMODE(info.st_mode) & stat.S_IXUSR
            or not os.access(path, os.X_OK)
        ):
            _fail("SCHEDULER_TOOL_AUTHORITY_INVALID")


def _git(
    worktree: Path,
    *args: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> bytes:
    completed = runner(
        ["/usr/bin/git", "-C", str(worktree), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        },
    )
    if completed.returncode != 0 or completed.stderr:
        _fail("GIT_AUTHORITY_UNAVAILABLE")
    return bytes(completed.stdout)


def validate_git_authority(
    *,
    worktree: Path = AUTHORITY_WORKTREE,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    if worktree.is_symlink() or not worktree.is_dir():
        _fail("AUTHORITY_WORKTREE_INVALID")
    head = _git(worktree, "rev-parse", "HEAD", runner=runner).decode().strip()
    branch = _git(worktree, "branch", "--show-current", runner=runner).decode().strip()
    origin = _git(
        worktree, "rev-parse", f"origin/{EXPECTED_BRANCH}", runner=runner
    ).decode().strip()
    dirty = _git(
        worktree, "status", "--porcelain", "--untracked-files=no", runner=runner
    )
    import_candidates = _git(
        worktree, "ls-files", "--others", "--", "scripts", runner=runner
    ).decode().splitlines()
    unsafe_candidates = [
        item
        for item in import_candidates
        if not re.fullmatch(r"scripts/__pycache__/[^/]+", item)
    ]
    if (
        COMMIT_RE.fullmatch(head) is None
        or branch != EXPECTED_BRANCH
        or origin != head
        or dirty
        or unsafe_candidates
    ):
        _fail("GIT_AUTHORITY_MISMATCH")
    return head


def science_command(mode: str) -> list[str]:
    if mode not in {*SCIENCE_MARKERS, "--print-fixed-identity"}:
        _fail("SCIENCE_MODE_INVALID")
    return [
        str(ECHOPRIME_PYTHON),
        "-I",
        "-B",
        "-X",
        "pycache_prefix=/dev/null/lvef_c3_full_scheduler",
        str(SCIENCE_WORKER),
        mode,
    ]


def parse_science_control_failure(
    completed: subprocess.CompletedProcess[bytes],
) -> str:
    """Return only the safe code from one exact five-line child failure."""

    stdout, stderr = bytes(completed.stdout), bytes(completed.stderr)
    if (
        completed.returncode != 78
        or stderr
        or not stdout
        or len(stdout) > SCIENCE_CONTROL_FAILURE_MAXIMUM_BYTES
        or not stdout.isascii()
        or b"\x00" in stdout
        or b"\r" in stdout
        or not stdout.endswith(b"\n")
    ):
        _fail("SCIENCE_CONTROL_COMMAND_FAILED")
    lines = stdout[:-1].decode("ascii").split("\n")
    prefix = "FULL_C3_STATUS=BLOCKED_"
    if (
        len(lines) != 5
        or any(not line for line in lines)
        or not lines[0].startswith(prefix)
        or tuple(lines[1:]) != SCIENCE_CONTROL_ZERO_EFFECT_LINES
    ):
        _fail("SCIENCE_CONTROL_COMMAND_FAILED")
    code = lines[0][len(prefix):]
    if SCIENCE_CONTROL_FAILURE_CODE_RE.fullmatch(code) is None:
        _fail("SCIENCE_CONTROL_COMMAND_FAILED")
    return code


def run_science_mode(
    mode: str,
    *,
    qsub_environment_sha256: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C",
    }
    if mode in QSUB_ENVIRONMENT_BOUND_SCIENCE_MODES:
        if (
            not isinstance(qsub_environment_sha256, str)
            or SHA256_RE.fullmatch(qsub_environment_sha256) is None
        ):
            _fail("SCIENCE_QSUB_ENVIRONMENT_BINDING_INVALID")
        environment[QSUB_ENVIRONMENT_SHA256_NAME] = qsub_environment_sha256
    elif qsub_environment_sha256 is not None:
        _fail("SCIENCE_QSUB_ENVIRONMENT_BINDING_INVALID")
    completed = runner(
        science_command(mode),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        if completed.returncode != 78:
            _fail("SCIENCE_CONTROL_COMMAND_FAILED")
        raise FullSchedulerError(parse_science_control_failure(completed))
    if completed.stderr or len(completed.stdout) > 512:
        _fail("SCIENCE_CONTROL_COMMAND_FAILED")
    raw_stdout = bytes(completed.stdout)
    if raw_stdout.startswith(b"FULL_C3_STATUS=BLOCKED_"):
        _fail("SCIENCE_CONTROL_COMMAND_FAILED")
    if raw_stdout.endswith(b"\n"):
        raw_stdout = raw_stdout[:-1]
    if b"\n" in raw_stdout or b"\r" in raw_stdout:
        _fail("SCIENCE_CONTROL_OUTPUT_INVALID")
    try:
        line = raw_stdout.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FullSchedulerError("SCIENCE_CONTROL_OUTPUT_INVALID") from exc
    if mode == "--print-fixed-identity":
        if re.fullmatch(r"FULL_C3_ATTEMPT_ID=lvef_c3_full_[0-9a-f]{16}_[0-9a-f]{8}", line) is None:
            _fail("SCIENCE_IDENTITY_OUTPUT_INVALID")
        return line.split("=", 1)[1]
    if line != SCIENCE_MARKERS[mode]:
        _fail("SCIENCE_CONTROL_OUTPUT_INVALID")
    return line


def validate_attempt_identity(attempt_id: str, head: str) -> None:
    match = ATTEMPT_RE.fullmatch(attempt_id)
    if match is None or match.group(2) != head[:8]:
        _fail("FULL_ATTEMPT_IDENTITY_MISMATCH")


class SchedulerTopology(NamedTuple):
    head: str
    attempt_id: str
    scheduler_root: Path
    array_job_name: str
    finalizer_job_name: str

    def array_command(self) -> list[str]:
        return [
            str(QSUB_PATH), "-clear", "-terse", "-r", "n", "-P", "mimicecho",
            "-N", self.array_job_name, "-j", "y", "-o", str(self.scheduler_root),
            "-t", "1-19", "-tc", "1", "-l", "h_rt=48:00:00",
            "-l", "gpus=1", "-l", "gpu_c=8.0", "-l", "gpu_memory=48G",
            "-pe", "omp", "4", "-l", "mem_per_core=16G", str(ARRAY_RUNNER),
        ]

    def finalizer_command(self, array_job_id: str) -> list[str]:
        validate_numeric_job_id(array_job_id)
        return [
            str(QSUB_PATH), "-clear", "-terse", "-r", "n", "-P", "mimicecho",
            "-N", self.finalizer_job_name, "-j", "y", "-o", str(self.scheduler_root),
            "-hold_jid", array_job_id, "-l", "h_rt=12:00:00",
            "-pe", "omp", "4", "-l", "mem_per_core=8G", str(FINALIZER_RUNNER),
        ]


def build_topology(*, head: str, attempt_id: str) -> SchedulerTopology:
    if COMMIT_RE.fullmatch(head) is None:
        _fail("GIT_HEAD_INVALID")
    validate_attempt_identity(attempt_id, head)
    scheduler_root = PRODUCTION_ROOT / "attempts" / attempt_id / "scheduler"
    return SchedulerTopology(
        head=head,
        attempt_id=attempt_id,
        scheduler_root=scheduler_root,
        array_job_name=f"lvef_c3_full_seq_{head[:8]}",
        finalizer_job_name=f"lvef_c3_full_fin_{head[:8]}",
    )


def validate_numeric_job_id(value: str) -> str:
    encoded = value.encode("ascii", errors="strict")
    if JOB_ID_BYTES_RE.fullmatch(encoded) is None or encoded.endswith(b"\n"):
        _fail("SCHEDULER_JOB_ID_INVALID")
    return value


def parse_numeric_qsub_stdout(value: bytes) -> str:
    if JOB_ID_BYTES_RE.fullmatch(value) is None:
        _fail("SCHEDULER_QSUB_OUTPUT_AMBIGUOUS")
    return value.rstrip(b"\n").decode("ascii")


def parse_array_qsub_stdout(value: bytes) -> str:
    match = ARRAY_JOB_ID_BYTES_RE.fullmatch(value)
    if match is None:
        _fail("SCHEDULER_ARRAY_QSUB_OUTPUT_AMBIGUOUS")
    return match.group(1).decode("ascii")


def validate_no_active_jobs(
    topology: SchedulerTopology,
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    completed = runner(
        [str(QSTAT_PATH), "-xml", "-u", environment["USER"]],
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
        _fail("ACTIVE_JOB_CHECK_FAILED")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FullSchedulerError("ACTIVE_JOB_CHECK_FAILED") from exc
    local_name = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local_name(element) for element in list(root)]
    if (
        local_name(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(name not in {"queue_info", "job_info"} for name in direct_children)
    ):
        _fail("ACTIVE_JOB_CHECK_FAILED")
    names = {
        element.text or ""
        for element in root.iter()
        if local_name(element) == "JB_name"
    }
    if any(
        re.fullmatch(r"lvef_c3_full_(?:seq|fin)_[0-9a-f]{8}", name)
        or re.fullmatch(r"c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}", name)
        for name in names
    ):
        _fail("ACTIVE_MATCHING_PRODUCTION_JOB_EXISTS")


def _require_attempt_root(topology: SchedulerTopology) -> Path:
    attempt_root = topology.scheduler_root.parent
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        _fail("SUBMISSION_CLAIM_ATTEMPT_ROOT_MISSING")
    info = attempt_root.stat(follow_symlinks=False)
    mode = stat.S_IMODE(info.st_mode)
    if (
        info.st_uid != os.geteuid()
        or mode not in {0o700, 0o2700}
    ):
        _fail("SUBMISSION_CLAIM_ATTEMPT_ROOT_INVALID")
    if os.path.lexists(topology.scheduler_root):
        _fail("SCHEDULER_EVIDENCE_ROOT_ALREADY_EXISTS")
    try:
        os.mkdir(topology.scheduler_root, 0o700)
    except OSError as exc:
        raise FullSchedulerError("SCHEDULER_EVIDENCE_ROOT_CREATE_FAILED") from exc
    mode = stat.S_IMODE(topology.scheduler_root.stat(follow_symlinks=False).st_mode)
    if mode & 0o700 != 0o700 or mode & 0o077 or mode & ~(0o2700):
        _fail("SCHEDULER_EVIDENCE_ROOT_MODE_INVALID")
    return topology.scheduler_root


def _write_new(path: Path, payload: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        _fail("SCHEDULER_EVIDENCE_PARENT_INVALID")
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as exc:
        raise FullSchedulerError("SCHEDULER_EVIDENCE_NO_CLOBBER") from exc


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def qsub_environment_sha256(environment: Mapping[str, str]) -> str:
    if (
        not isinstance(environment, Mapping)
        or not environment
        or any(
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or "\x00" in name + value
            or "\n" in name + value
            or "\r" in name + value
            for name, value in environment.items()
        )
    ):
        _fail("SUBMISSION_RECEIPT_ENVIRONMENT_INVALID")
    return _sha256_bytes(
        _canonical_json({"environment": dict(sorted(environment.items()))})
    )


def _read_scheduler_evidence(path: Path) -> bytes:
    try:
        info = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise FullSchedulerError("SUBMISSION_RECEIPT_EVIDENCE_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 4 * 1024 * 1024
        ):
            _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
            chunks.append(block)
            remaining -= len(block)
        if os.fstat(descriptor).st_size != info.st_size:
            _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def validate_submission_receipt(
    value: Mapping[str, object], *, topology: SchedulerTopology,
    expected_qsub_environment_sha256: str,
) -> Mapping[str, object]:
    if set(value) != SUBMISSION_RECEIPT_KEYS:
        _fail("SUBMISSION_RECEIPT_SCHEMA_INVALID")
    array_command = topology.array_command()
    array_id = str(value.get("array_job_id", ""))
    finalizer_id = str(value.get("finalizer_job_id", ""))
    validate_numeric_job_id(array_id)
    validate_numeric_job_id(finalizer_id)
    finalizer_command = topology.finalizer_command(array_id)
    expected = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_two_submission_receipt_v1",
        "status": "PASS_EXACT_TWO_QSUB_SUBMISSIONS",
        "attempt_id": topology.attempt_id,
        "governing_commit": topology.head,
        "array_job_name": topology.array_job_name,
        "finalizer_job_name": topology.finalizer_job_name,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_json({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_json({"argv": finalizer_command})
        ),
        "array_qsub_exit_status": 0,
        "finalizer_qsub_exit_status": 0,
        "scheduler_submission_count": 2,
        "scheduler_submission_maximum": 2,
        "array_task_range": "1-19",
        "array_max_concurrency": 1,
        "finalizer_held_on_array": True,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        _fail("SUBMISSION_RECEIPT_AUTHORITY_INVALID")
    for prefix in ("array_qsub", "finalizer_qsub"):
        if (
            not isinstance(value.get(f"{prefix}_stdout_bytes"), int)
            or int(value[f"{prefix}_stdout_bytes"]) < 1
            or not isinstance(value.get(f"{prefix}_stderr_bytes"), int)
            or int(value[f"{prefix}_stderr_bytes"]) < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(value.get(f"{prefix}_stdout_sha256", ""))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(value.get(f"{prefix}_stderr_sha256", ""))) is None
        ):
            _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_qsub_environment_sha256) is None
        or value.get("qsub_environment_sha256")
        != expected_qsub_environment_sha256
    ):
        _fail("SUBMISSION_RECEIPT_ENVIRONMENT_INVALID")
    root_info = _lstat(topology.scheduler_root, "SUBMISSION_RECEIPT_EVIDENCE_INVALID")
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) not in {0o700, 0o2700}
    ):
        _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
    evidence: dict[str, bytes] = {}
    for label in ("array", "finalizer"):
        for kind in ("stdout", "stderr", "exit_status"):
            evidence[f"{label}_{kind}"] = _read_scheduler_evidence(
                topology.scheduler_root / f"{label}.qsub.{kind}.restricted"
            )
        for kind in ("stdout", "stderr"):
            payload = evidence[f"{label}_{kind}"]
            if (
                value[f"{label}_qsub_{kind}_bytes"] != len(payload)
                or value[f"{label}_qsub_{kind}_sha256"] != _sha256_bytes(payload)
            ):
                _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
        if (
            evidence[f"{label}_exit_status"] != b"0\n"
            or evidence[f"{label}_stderr"] != b""
        ):
            _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
    if (
        parse_array_qsub_stdout(evidence["array_stdout"]) != array_id
        or parse_numeric_qsub_stdout(evidence["finalizer_stdout"]) != finalizer_id
    ):
        _fail("SUBMISSION_RECEIPT_EVIDENCE_INVALID")
    return value


def _capture_qsub(
    label: str,
    command: Sequence[str],
    *,
    root: Path,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    parser: Callable[[bytes], str] = parse_numeric_qsub_stdout,
) -> str:
    completed = runner(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    stdout, stderr = bytes(completed.stdout), bytes(completed.stderr)
    _write_new(root / f"{label}.qsub.stdout.restricted", stdout)
    _write_new(root / f"{label}.qsub.stderr.restricted", stderr)
    _write_new(
        root / f"{label}.qsub.exit_status.restricted",
        f"{completed.returncode}\n".encode("ascii"),
    )
    if completed.returncode != 0 or stderr:
        _fail(f"{label.upper()}_QSUB_PROCESS_FAILED")
    return parser(stdout)


def _identity_and_topology(
    *,
    science_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    git_runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> SchedulerTopology:
    head = validate_git_authority(runner=git_runner)
    attempt_id = run_science_mode("--print-fixed-identity", runner=science_runner)
    return build_topology(head=head, attempt_id=attempt_id)


def validate_installation(
    *,
    source_environment: Mapping[str, str] | None = None,
    science_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    git_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[SchedulerTopology, dict[str, str], str]:
    validate_scheduler_tools()
    environment, input_class = build_qsub_environment(source_environment)
    run_science_mode("--validate-installation", runner=science_runner)
    topology = _identity_and_topology(
        science_runner=science_runner, git_runner=git_runner
    )
    return topology, environment, input_class


def preflight(
    *,
    run_science_preflight: bool = True,
    source_environment: Mapping[str, str] | None = None,
    science_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    git_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> SchedulerTopology:
    topology, environment, _ = validate_installation(
        source_environment=source_environment,
        science_runner=science_runner,
        git_runner=git_runner,
    )
    if run_science_preflight:
        run_science_mode("--preflight-only", runner=science_runner)
    validate_no_active_jobs(topology, environment, runner=qstat_runner)
    return topology


def submit(
    *,
    source_environment: Mapping[str, str] | None = None,
    science_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    git_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[str, str]:
    topology, environment, _ = validate_installation(
        source_environment=source_environment,
        science_runner=science_runner,
        git_runner=git_runner,
    )
    environment_sha256 = qsub_environment_sha256(environment)
    run_science_mode("--preflight-only", runner=science_runner)
    validate_no_active_jobs(topology, environment, runner=qstat_runner)
    # Materialize the no-clobber claim only after the explicit no-body gate.
    # Do not insert another scheduler or cloud effect between these commands.
    run_science_mode(
        "--claim-submission",
        qsub_environment_sha256=environment_sha256,
        runner=science_runner,
    )
    # Re-open the materialized claim through the production reader before any
    # scheduler effect.  This command must emit its sole exact PASS marker;
    # every failure leaves qsub and scheduler-receipt creation unreachable.
    run_science_mode(
        "--validate-claimed-submission",
        qsub_environment_sha256=environment_sha256,
        runner=science_runner,
    )
    validate_no_active_jobs(topology, environment, runner=qstat_runner)
    evidence_root = _require_attempt_root(topology)
    array_command = topology.array_command()
    # Reserve every fixed evidence name before the first scheduler call.
    for label in ("array", "finalizer"):
        for suffix in (
            "qsub.stdout.restricted",
            "qsub.stderr.restricted",
            "qsub.exit_status.restricted",
        ):
            if os.path.lexists(evidence_root / f"{label}.{suffix}"):
                _fail("SCHEDULER_EVIDENCE_NO_CLOBBER")
    array_id = _capture_qsub(
        "array", array_command, root=evidence_root,
        environment=environment, runner=qsub_runner,
        parser=parse_array_qsub_stdout,
    )
    finalizer_command = topology.finalizer_command(array_id)
    finalizer_id = _capture_qsub(
        "finalizer", finalizer_command, root=evidence_root,
        environment=environment, runner=qsub_runner,
    )
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_two_submission_receipt_v1",
        "status": "PASS_EXACT_TWO_QSUB_SUBMISSIONS",
        "attempt_id": topology.attempt_id,
        "governing_commit": topology.head,
        "array_job_name": topology.array_job_name,
        "finalizer_job_name": topology.finalizer_job_name,
        "array_job_id": array_id,
        "finalizer_job_id": finalizer_id,
        "array_qsub_argv_sha256": hashlib.sha256(
            _canonical_json({"argv": array_command})
        ).hexdigest(),
        "finalizer_qsub_argv_sha256": hashlib.sha256(
            _canonical_json({"argv": finalizer_command})
        ).hexdigest(),
        "qsub_environment_sha256": environment_sha256,
        "array_qsub_stdout_bytes": (
            evidence_root / "array.qsub.stdout.restricted"
        ).stat(follow_symlinks=False).st_size,
        "array_qsub_stdout_sha256": _sha256_bytes(
            (evidence_root / "array.qsub.stdout.restricted").read_bytes()
        ),
        "array_qsub_stderr_bytes": (
            evidence_root / "array.qsub.stderr.restricted"
        ).stat(follow_symlinks=False).st_size,
        "array_qsub_stderr_sha256": _sha256_bytes(
            (evidence_root / "array.qsub.stderr.restricted").read_bytes()
        ),
        "array_qsub_exit_status": 0,
        "finalizer_qsub_stdout_bytes": (
            evidence_root / "finalizer.qsub.stdout.restricted"
        ).stat(follow_symlinks=False).st_size,
        "finalizer_qsub_stdout_sha256": _sha256_bytes(
            (evidence_root / "finalizer.qsub.stdout.restricted").read_bytes()
        ),
        "finalizer_qsub_stderr_bytes": (
            evidence_root / "finalizer.qsub.stderr.restricted"
        ).stat(follow_symlinks=False).st_size,
        "finalizer_qsub_stderr_sha256": _sha256_bytes(
            (evidence_root / "finalizer.qsub.stderr.restricted").read_bytes()
        ),
        "finalizer_qsub_exit_status": 0,
        "scheduler_submission_count": 2,
        "scheduler_submission_maximum": 2,
        "array_task_range": "1-19",
        "array_max_concurrency": 1,
        "finalizer_held_on_array": True,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }
    validate_submission_receipt(
        receipt,
        topology=topology,
        expected_qsub_environment_sha256=environment_sha256,
    )
    _write_new(evidence_root / "submission_receipt.restricted.json", _canonical_json(receipt))
    return array_id, finalizer_id


def render(topology: SchedulerTopology) -> tuple[str, str]:
    array = shlex.join(topology.array_command())
    finalizer_tokens = topology.finalizer_command("999999999")
    finalizer_tokens[finalizer_tokens.index("-hold_jid") + 1] = "ARRAY_JOB_ID"
    finalizer = shlex.join(finalizer_tokens)
    return array, finalizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in (
        "--validate-installation", "--preflight-only", "--render", "--submit"
    ):
        modes.add_argument(mode, action="store_const", const=mode, dest="mode")
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.mode == "--validate-installation":
            topology, _, input_class = validate_installation()
            print("FULL_C3_SCHEDULER_INSTALLATION=PASS")
            print(f"FULL_C3_SGE_ROOT_INPUT={input_class}")
            print(f"FULL_C3_ATTEMPT_ID={topology.attempt_id}")
        elif args.mode in {"--preflight-only", "--render"}:
            topology = preflight(
                run_science_preflight=args.mode == "--preflight-only"
            )
            if args.mode == "--render":
                array, finalizer = render(topology)
                print(
                    "FULL_C3_ARRAY_QSUB_ARGV_SHA256="
                    f"{hashlib.sha256(array.encode('utf-8')).hexdigest()}"
                )
                print(
                    "FULL_C3_FINALIZER_QSUB_TEMPLATE_ARGV_SHA256="
                    f"{hashlib.sha256(finalizer.encode('utf-8')).hexdigest()}"
                )
                print("FULL_C3_QSUB_COMMAND_RENDERING=PASS")
            print("FULL_C3_SCHEDULER_CONTEXT=PASS")
            print("FULL_C3_ACTIVE_MATCHING_PRODUCTION_JOBS=0")
            print(f"FULL_C3_GOVERNING_COMMIT={topology.head}")
            print(f"FULL_C3_ATTEMPT_ID={topology.attempt_id}")
            print("FULL_C3_SEQUENTIAL_TWO_SUBMISSION_TOPOLOGY=PASS")
            print("FULL_C3_SCHEDULER_PREFLIGHT=PASS")
            print("QSUB_SUBMISSIONS=0")
        else:
            array_id, finalizer_id = submit()
            print(f"FULL_C3_ARRAY_JOB_ID={array_id}")
            print(f"FULL_C3_FINALIZER_JOB_ID={finalizer_id}")
            print("FULL_C3_TOTAL_QSUB_SUBMISSIONS=2")
        return 0
    except FullSchedulerError as exc:
        print(f"FULL_C3_SCHEDULER=BLOCKED_{exc.code}")
        return 78
    except Exception:
        print("FULL_C3_SCHEDULER=BLOCKED_UNEXPECTED_SANITIZED")
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
