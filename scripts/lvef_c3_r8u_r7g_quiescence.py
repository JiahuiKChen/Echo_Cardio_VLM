#!/usr/bin/env python3
"""Body-free R7F/R7G scheduler and process quiescence observer.

This module takes one complete XML qstat snapshot and one numeric-owner process
snapshot.  It derives the target jobs from the sealed R7G terminal authority,
ignores unrelated owner activity, rejects truncated scheduler identities, and
projects only bounded control-plane metadata.  It has no scientific execution
or worker-context path.
"""
from __future__ import annotations

from dataclasses import dataclass
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
from typing import Any, Callable, Final, Mapping
import xml.etree.ElementTree as ET


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_r8u_r7g_accounting as accounting


OWNER: Final = "pkarim"
ARRAY_JOB_ID: Final = "7480830"
FINALIZER_JOB_ID: Final = "7480831"
ARRAY_JOB_NAME: Final = "lvef_c3_r8u_r7d_seq_2223d976"
FINALIZER_JOB_NAME: Final = "lvef_c3_r8u_r7d_fin_2223d976"
ARRAY_JOB_ROLE: Final = "R8U_R7D_CONTINUATION_ARRAY"
FINALIZER_JOB_ROLE: Final = "R8U_R7D_COHORT_FINALIZER"

QUERY_TOOL_PATH: Final = Path(
    "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qstat"
)
PROCESS_TOOL_PATH: Final = Path("/bin/ps")
SCHEDULER_COMMAND: Final = (str(QUERY_TOOL_PATH), "-xml", "-u", OWNER)
PROCESS_COMMAND: Final = (str(PROCESS_TOOL_PATH), "-axo", "pid=,uid=,command=")
MAX_SCHEDULER_BYTES: Final = 4 * 1024 * 1024
MAX_PROCESS_BYTES: Final = 16 * 1024 * 1024
MAX_PROCESS_COMMAND_CHARACTERS: Final = 32 * 1024

SAFE_JOB_NUMBER_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_JOB_FIELD_RE: Final = re.compile(r"^[A-Za-z0-9_.:@,+/=-]{0,1024}$")
RELEVANT_JOB_NAME_RE: Final = re.compile(
    r"(?:lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
    r"r8u_(?:rec|seq|fin)|r8u_r[3-9][a-z]?_(?:ctx|loc|res|rec|seq|fin))_"
    r"[0-9a-f]{8}|c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12})"
)
RELEVANT_JOB_NAME_IN_COMMAND_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_.-])" + RELEVANT_JOB_NAME_RE.pattern
    + r"(?![A-Za-z0-9_.-])"
)
RELATED_OR_TRUNCATED_PREFIXES: Final = (
    "lvef_c3_full_",
    "lvef_c3_r8r_",
    "lvef_c3_r8",
    "c3_dl1_",
    "c3_dlr_",
    "c3_ext_",
    "c3_emb_",
    "c3_pre_",
    "c3_ret_",
    "c3_fin_",
)

# Execution markers include the historical worker surface used by R7F and the
# new retrospective R7G accounting/finalization/lock surface.  The one current
# R7G adjudicator observer is exempted separately by exact PID and argv shape.
RELEVANT_PROCESS_MARKERS: Final = (
    "--run-array-task",
    "--run-cohort-finalizer",
    "--recover-batch3-preservation",
    "--run-continuation-array-task",
    "--run-continuation-finalizer",
    "--run-batch16-recovery",
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
    "--run-r8u-r6-locality-sequence-probe",
    "--run-r8u-r6-batch16-publication-resume",
    "--run-r8u-r6-continuation-17-19-array-task",
    "--run-r8u-r6-continuation-finalizer",
    "--run-r8u-r7-batch16-preservation-recovery",
    "--run-r8u-r7-continuation-17-19-array-task",
    "--run-r8u-r7-continuation-finalizer",
    "--run-r8u-r7d-continuation-context-probe",
    "--run-r8u-r7d-continuation-17-19-array-task",
    "--run-r8u-r7d-continuation-finalizer",
    "--account-fixed-r8u-r7f-existing-jobs",
    "--finalize-r8u-r7g-cohort-metadata-only",
    "--review-r8u-r7g-post-reconstruction-lock",
    "lvef_c3_r8u_r7g_accounting.py",
    "lvef_c3_r8u_r7g_cohort_finalizer.py",
    "lvef_c3_r8u_r7g_metadata.py",
    "lvef_c3_r8u_r7g_lock_review.py",
    "run_production_dicom_extraction",
    "run_production_echoprime",
    "preserve_lvef_c3_production_batch",
    "retire_lvef_c3_extracted_cache",
    "finalize_lvef_c3_production",
    "scc_run_lvef_c3_full_sequential.sh",
    "scc_run_lvef_c3_full_finalizer.sh",
    "scc_run_lvef_c3_r8r_recovery_continuation.sh",
)
OBSERVER_SCRIPT_BASENAME: Final = "lvef_c3_r8u_r7g_terminal_adjudicator.py"
OBSERVER_ENTRYPOINT_FLAG: Final = "--adjudicate-fixed-r8u-r7f-existing-jobs"

SCHEDULER_PROJECTION_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "scheduler_username",
        "snapshot_count",
        "query_argv_sha256",
        "query_stdout_sha256",
        "parsed_job_records",
        "matching_active_jobs",
        "target_array_job_present",
        "target_finalizer_job_present",
        "matching_job_records",
    }
)
PROCESS_PROJECTION_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "effective_uid",
        "snapshot_count",
        "ps_argv_sha256",
        "ps_stdout_sha256",
        "parsed_process_records",
        "owner_process_records",
        "matching_active_processes",
        "matching_process_records",
    }
)
SCHEDULER_STATE_KEYS: Final = frozenset(
    {
        "matching_active_jobs",
        "matching_active_processes",
        "qstat_projection_sha256",
        "process_projection_sha256",
    }
)
FORBIDDEN_WORKER_ENVIRONMENT_NAMES: Final = frozenset(
    {
        "JOB_ID",
        "JOB_NAME",
        "SGE_TASK_ID",
        "SGE_TASK_FIRST",
        "SGE_TASK_LAST",
        "SGE_TASK_STEPSIZE",
        "NSLOTS",
        "PE_HOSTFILE",
        "QUEUE",
        "REQUEST",
    }
)


class R7GQuiescenceError(RuntimeError):
    """One stable fail-closed R7G quiescence error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7GQuiescenceError(code)


@dataclass(frozen=True)
class FixedQuiescenceEvidence:
    """Lock projection and its two independently hashed source projections."""

    scheduler_state: dict[str, Any]
    scheduler_projection: dict[str, Any]
    process_projection: dict[str, Any]


@dataclass(frozen=True)
class _FixedIdentity:
    owner: str
    array_job_id: str
    array_job_name: str
    finalizer_job_id: str
    finalizer_job_name: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_PROJECTION_INVALID"
        ) from exc
    return _sha256(payload)


def _argv_sha256(argv: tuple[str, ...]) -> str:
    return _canonical_json_sha256({"argv": list(argv)})


def _fixed_identity(terminal_authority: Mapping[str, Any]) -> _FixedIdentity:
    if not isinstance(terminal_authority, Mapping):
        _fail("R8U_R7G_QUIESCENCE_AUTHORITY_INVALID")
    try:
        specs = tuple(accounting.fixed_accounting_specs(terminal_authority))
    except Exception as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_AUTHORITY_INVALID"
        ) from exc
    expected_fields = {
        "expected_owner": OWNER,
        "array_job_id": ARRAY_JOB_ID,
        "array_job_name": ARRAY_JOB_NAME,
        "array_role": ARRAY_JOB_ROLE,
        "array_task_ids": [17, 18, 19],
        "array_task_range": "17-19",
        "array_task_count": 3,
        "array_max_concurrency": 1,
        "finalizer_job_id": FINALIZER_JOB_ID,
        "finalizer_job_name": FINALIZER_JOB_NAME,
        "finalizer_role": FINALIZER_JOB_ROLE,
    }
    if (
        len(specs) != 4
        or any(
            terminal_authority.get(key) != value
            for key, value in expected_fields.items()
        )
    ):
        _fail("R8U_R7G_QUIESCENCE_AUTHORITY_INVALID")
    array_specs = tuple(spec for spec in specs if spec.job_kind == "ARRAY_TASK")
    finalizer_specs = tuple(
        spec for spec in specs if spec.job_kind == "NON_ARRAY_FINALIZER"
    )
    if (
        tuple(spec.task_id for spec in array_specs) != (17, 18, 19)
        or any(
            spec.job_id != ARRAY_JOB_ID
            or spec.expected_job_name != ARRAY_JOB_NAME
            or spec.expected_job_role != ARRAY_JOB_ROLE
            for spec in array_specs
        )
        or len(finalizer_specs) != 1
        or finalizer_specs[0].job_id != FINALIZER_JOB_ID
        or finalizer_specs[0].task_id is not None
        or finalizer_specs[0].expected_job_name != FINALIZER_JOB_NAME
        or finalizer_specs[0].expected_job_role != FINALIZER_JOB_ROLE
    ):
        _fail("R8U_R7G_QUIESCENCE_AUTHORITY_INVALID")
    return _FixedIdentity(
        owner=OWNER,
        array_job_id=ARRAY_JOB_ID,
        array_job_name=ARRAY_JOB_NAME,
        finalizer_job_id=FINALIZER_JOB_ID,
        finalizer_job_name=FINALIZER_JOB_NAME,
    )


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _validated_environment(
    environment: Mapping[str, str], *, identity: _FixedIdentity
) -> dict[str, str]:
    if not isinstance(environment, Mapping) or not environment:
        _fail("R8U_R7G_QUIESCENCE_ENVIRONMENT_INVALID")
    normalized: dict[str, str] = {}
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or any(character in key + value for character in ("\x00", "\r", "\n"))
        ):
            _fail("R8U_R7G_QUIESCENCE_ENVIRONMENT_INVALID")
        normalized[key] = value
    if (
        normalized.get("USER") != identity.owner
        or normalized.get("LOGNAME") != identity.owner
        or normalized.get("SGE_ROOT") != str(QUERY_TOOL_PATH.parents[2])
        or any(name in normalized for name in FORBIDDEN_WORKER_ENVIRONMENT_NAMES)
    ):
        _fail("R8U_R7G_QUIESCENCE_ENVIRONMENT_INVALID")
    return dict(sorted(normalized.items()))


def _require_observer_tools() -> None:
    expected = Path(
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qstat"
    )
    if QUERY_TOOL_PATH != expected or not QUERY_TOOL_PATH.is_absolute():
        _fail("R8U_R7G_QUIESCENCE_QUERY_TOOL_INVALID")
    current = Path(QUERY_TOOL_PATH.anchor)
    try:
        for component in QUERY_TOOL_PATH.parts[1:]:
            current /= component
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                _fail("R8U_R7G_QUIESCENCE_QUERY_TOOL_INVALID")
        info = os.lstat(QUERY_TOOL_PATH)
    except R7GQuiescenceError:
        raise
    except OSError as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_QUERY_TOOL_INVALID"
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not stat.S_IMODE(info.st_mode) & stat.S_IXUSR
        or not os.access(QUERY_TOOL_PATH, os.X_OK)
    ):
        _fail("R8U_R7G_QUIESCENCE_QUERY_TOOL_INVALID")
    try:
        process_info = os.lstat(PROCESS_TOOL_PATH)
    except OSError as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_PROCESS_TOOL_INVALID"
        ) from exc
    if (
        PROCESS_TOOL_PATH != Path("/bin/ps")
        or not stat.S_ISREG(process_info.st_mode)
        or process_info.st_uid != 0
        or stat.S_IMODE(process_info.st_mode) & 0o022
        or not stat.S_IMODE(process_info.st_mode) & stat.S_IXUSR
        or not os.access(PROCESS_TOOL_PATH, os.X_OK)
    ):
        _fail("R8U_R7G_QUIESCENCE_PROCESS_TOOL_INVALID")


def _query_payload(
    argv: tuple[str, ...],
    *,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    maximum_bytes: int,
    failure_code: str,
) -> bytes:
    try:
        completed = runner(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=dict(environment),
        )
        payload = bytes(completed.stdout)
        error = bytes(completed.stderr)
    except Exception as exc:
        raise R7GQuiescenceError(failure_code) from exc
    if completed.returncode != 0 or error or len(payload) > maximum_bytes:
        _fail(failure_code)
    return payload


def project_fixed_scheduler_snapshot(
    payload: bytes, *, terminal_authority: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize one complete XML snapshot without display-column names."""

    identity = _fixed_identity(terminal_authority)
    if (
        not isinstance(payload, bytes)
        or len(payload) > MAX_SCHEDULER_BYTES
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8U_R7G_SCHEDULER_SNAPSHOT_INVALID")
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError) as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_SCHEDULER_SNAPSHOT_INVALID"
        ) from exc

    parsed_records = 0
    matching_records: list[dict[str, str]] = []
    matching_job_ids: set[str] = set()
    target_array_present = False
    target_finalizer_present = False
    for job in root.iter():
        if _local_name(job) != "job_list":
            continue
        parsed_records += 1
        fields: dict[str, list[str]] = {}
        for child in list(job):
            fields.setdefault(_local_name(child), []).append(child.text or "")
        if any(
            len(fields.get(key, ())) != 1
            for key in ("JB_job_number", "JB_name", "JB_owner", "state")
        ) or len(fields.get("tasks", ())) > 1:
            _fail("R8U_R7G_SCHEDULER_SNAPSHOT_INVALID")
        job_id = fields["JB_job_number"][0]
        name = fields["JB_name"][0]
        owner = fields["JB_owner"][0]
        state_value = fields["state"][0]
        category = str(job.get("state", ""))
        tasks = fields.get("tasks", [""])[0]
        if (
            SAFE_JOB_NUMBER_RE.fullmatch(job_id) is None
            or not name
            or len(name) > 512
            or SAFE_JOB_FIELD_RE.fullmatch(name) is None
            or not state_value
            or SAFE_JOB_FIELD_RE.fullmatch(state_value) is None
            or SAFE_JOB_FIELD_RE.fullmatch(category) is None
            or SAFE_JOB_FIELD_RE.fullmatch(owner) is None
            or SAFE_JOB_FIELD_RE.fullmatch(tasks) is None
        ):
            _fail("R8U_R7G_SCHEDULER_SNAPSHOT_INVALID")

        exact_name = RELEVANT_JOB_NAME_RE.fullmatch(name) is not None
        fixed_target_id = job_id in {
            identity.array_job_id,
            identity.finalizer_job_id,
        }
        if name.startswith(RELATED_OR_TRUNCATED_PREFIXES) and not exact_name:
            _fail("R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS")
        if fixed_target_id:
            expected_name = (
                identity.array_job_name
                if job_id == identity.array_job_id
                else identity.finalizer_job_name
            )
            if name != expected_name:
                _fail("R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS")
        if not (exact_name or fixed_target_id):
            continue
        if owner != identity.owner:
            _fail("R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS")
        matching_job_ids.add(job_id)
        target_array_present = (
            target_array_present or job_id == identity.array_job_id
        )
        target_finalizer_present = (
            target_finalizer_present or job_id == identity.finalizer_job_id
        )
        matching_records.append(
            {
                "category": category,
                "job_id": job_id,
                "job_name": name,
                "owner": owner,
                "state": state_value,
                "tasks": tasks,
            }
        )

    matching_records.sort(
        key=lambda item: (
            int(item["job_id"]),
            item["tasks"],
            item["state"],
            item["category"],
        )
    )
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7g_scheduler_quiescence_projection_v1",
        "status": (
            "PASS_ZERO_MATCHING_ACTIVE_SCHEDULER_JOBS"
            if not matching_job_ids
            else "CONTRADICTION_MATCHING_ACTIVE_SCHEDULER_JOBS"
        ),
        "scheduler_username": identity.owner,
        "snapshot_count": 1,
        "query_argv_sha256": _argv_sha256(SCHEDULER_COMMAND),
        "query_stdout_sha256": _sha256(payload),
        "parsed_job_records": parsed_records,
        "matching_active_jobs": len(matching_job_ids),
        "target_array_job_present": target_array_present,
        "target_finalizer_job_present": target_finalizer_present,
        "matching_job_records": matching_records,
    }
    if set(value) != SCHEDULER_PROJECTION_KEYS:
        _fail("R8U_R7G_SCHEDULER_PROJECTION_INVALID")
    return value


def _is_exact_observer_process(command: str) -> bool:
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError:
        return False
    script_count = sum(
        Path(argument).name == OBSERVER_SCRIPT_BASENAME for argument in arguments
    )
    flag_count = arguments.count(OBSERVER_ENTRYPOINT_FLAG)
    return script_count == 1 and flag_count == 1 and not any(
        marker in command for marker in RELEVANT_PROCESS_MARKERS
    )


def _is_relevant_process(command: str) -> bool:
    if any(marker in command for marker in RELEVANT_PROCESS_MARKERS):
        return True
    if OBSERVER_SCRIPT_BASENAME in command:
        return True
    if RELEVANT_JOB_NAME_IN_COMMAND_RE.search(command) is not None:
        return True
    return "qacct" in command and any(
        job_id in command for job_id in (ARRAY_JOB_ID, FINALIZER_JOB_ID)
    )


def project_fixed_process_snapshot(
    payload: bytes,
    *,
    effective_uid: int,
    observer_pid: int | None = None,
) -> dict[str, Any]:
    """Normalize a process snapshot, excluding only the exact observer PID."""

    if (
        not isinstance(payload, bytes)
        or isinstance(effective_uid, bool)
        or not isinstance(effective_uid, int)
        or effective_uid < 0
        or len(payload) > MAX_PROCESS_BYTES
        or (
            observer_pid is not None
            and (
                isinstance(observer_pid, bool)
                or not isinstance(observer_pid, int)
                or observer_pid <= 0
                or observer_pid != os.getpid()
            )
        )
    ):
        _fail("R8U_R7G_PROCESS_SNAPSHOT_INVALID")
    try:
        lines = payload.decode("utf-8", "strict").splitlines()
    except UnicodeError as exc:
        raise R7GQuiescenceError("R8U_R7G_PROCESS_SNAPSHOT_INVALID") from exc

    parsed_records = 0
    owner_records = 0
    matching: list[dict[str, Any]] = []
    seen_pids: set[int] = set()
    for raw_line in lines:
        if not raw_line.strip():
            continue
        fields = raw_line.strip().split(None, 2)
        if len(fields) != 3:
            _fail("R8U_R7G_PROCESS_SNAPSHOT_INVALID")
        try:
            pid = int(fields[0])
            uid = int(fields[1])
        except ValueError as exc:
            raise R7GQuiescenceError(
                "R8U_R7G_PROCESS_SNAPSHOT_INVALID"
            ) from exc
        command = fields[2]
        if (
            pid <= 0
            or uid < 0
            or pid in seen_pids
            or len(command) > MAX_PROCESS_COMMAND_CHARACTERS
            or any(character in command for character in ("\x00", "\r", "\n"))
        ):
            _fail("R8U_R7G_PROCESS_SNAPSHOT_INVALID")
        seen_pids.add(pid)
        parsed_records += 1
        if uid != effective_uid:
            continue
        owner_records += 1
        if (
            observer_pid is not None
            and pid == observer_pid
            and _is_exact_observer_process(command)
        ):
            continue
        if _is_relevant_process(command):
            matching.append(
                {
                    "command_sha256": _sha256(command.encode("utf-8")),
                    "pid": pid,
                    "uid": uid,
                }
            )

    matching.sort(key=lambda item: item["pid"])
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7g_process_quiescence_projection_v1",
        "status": (
            "PASS_ZERO_MATCHING_ACTIVE_PROCESSES"
            if not matching
            else "CONTRADICTION_MATCHING_ACTIVE_PROCESSES"
        ),
        "effective_uid": effective_uid,
        "snapshot_count": 1,
        "ps_argv_sha256": _argv_sha256(PROCESS_COMMAND),
        "ps_stdout_sha256": _sha256(payload),
        "parsed_process_records": parsed_records,
        "owner_process_records": owner_records,
        "matching_active_processes": len(matching),
        "matching_process_records": matching,
    }
    if set(value) != PROCESS_PROJECTION_KEYS:
        _fail("R8U_R7G_PROCESS_PROJECTION_INVALID")
    return value


def build_fixed_scheduler_state(
    scheduler_projection: Mapping[str, Any],
    process_projection: Mapping[str, Any],
    *,
    terminal_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact scheduler-state projection consumed by the R7G lock."""

    identity = _fixed_identity(terminal_authority)
    if not isinstance(scheduler_projection, Mapping) or not isinstance(
        process_projection, Mapping
    ):
        _fail("R8U_R7G_QUIESCENCE_PROJECTION_INVALID")
    sha_re = re.compile(r"^[0-9a-f]{64}$")
    matching_jobs = scheduler_projection.get("matching_active_jobs")
    matching_processes = process_projection.get("matching_active_processes")
    scheduler_records = scheduler_projection.get("matching_job_records")
    process_records = process_projection.get("matching_process_records")
    if (
        set(scheduler_projection) != SCHEDULER_PROJECTION_KEYS
        or set(process_projection) != PROCESS_PROJECTION_KEYS
        or scheduler_projection.get("schema_version") != 1
        or scheduler_projection.get("artifact_type")
        != "lvef_c3_r8u_r7g_scheduler_quiescence_projection_v1"
        or scheduler_projection.get("scheduler_username") != identity.owner
        or scheduler_projection.get("snapshot_count") != 1
        or scheduler_projection.get("query_argv_sha256")
        != _argv_sha256(SCHEDULER_COMMAND)
        or any(
            sha_re.fullmatch(str(scheduler_projection.get(key, ""))) is None
            for key in ("query_argv_sha256", "query_stdout_sha256")
        )
        or isinstance(scheduler_projection.get("parsed_job_records"), bool)
        or not isinstance(scheduler_projection.get("parsed_job_records"), int)
        or scheduler_projection.get("parsed_job_records", -1) < 0
        or isinstance(matching_jobs, bool)
        or not isinstance(matching_jobs, int)
        or matching_jobs < 0
        or not isinstance(scheduler_records, list)
        or len(scheduler_records) < matching_jobs
        or (matching_jobs == 0) != (scheduler_records == [])
        or process_projection.get("schema_version") != 1
        or process_projection.get("artifact_type")
        != "lvef_c3_r8u_r7g_process_quiescence_projection_v1"
        or process_projection.get("snapshot_count") != 1
        or process_projection.get("ps_argv_sha256")
        != _argv_sha256(PROCESS_COMMAND)
        or any(
            sha_re.fullmatch(str(process_projection.get(key, ""))) is None
            for key in ("ps_argv_sha256", "ps_stdout_sha256")
        )
        or any(
            isinstance(process_projection.get(key), bool)
            or not isinstance(process_projection.get(key), int)
            or process_projection.get(key, -1) < 0
            for key in (
                "effective_uid",
                "parsed_process_records",
                "owner_process_records",
            )
        )
        or process_projection.get("owner_process_records", 0)
        > process_projection.get("parsed_process_records", -1)
        or isinstance(matching_processes, bool)
        or not isinstance(matching_processes, int)
        or matching_processes < 0
        or not isinstance(process_records, list)
        or len(process_records) != matching_processes
    ):
        _fail("R8U_R7G_QUIESCENCE_PROJECTION_INVALID")
    if (
        matching_jobs != 0
        or matching_processes != 0
        or scheduler_projection.get("status")
        != "PASS_ZERO_MATCHING_ACTIVE_SCHEDULER_JOBS"
        or process_projection.get("status")
        != "PASS_ZERO_MATCHING_ACTIVE_PROCESSES"
        or scheduler_projection.get("target_array_job_present") is not False
        or scheduler_projection.get("target_finalizer_job_present") is not False
    ):
        _fail("R8U_R7G_ACTIVE_EXECUTION_CONTRADICTION")
    result = {
        "matching_active_jobs": 0,
        "matching_active_processes": 0,
        "qstat_projection_sha256": _canonical_json_sha256(
            dict(scheduler_projection)
        ),
        "process_projection_sha256": _canonical_json_sha256(
            dict(process_projection)
        ),
    }
    if set(result) != SCHEDULER_STATE_KEYS:
        _fail("R8U_R7G_QUIESCENCE_PROJECTION_INVALID")
    return result


def _default_effective_uid(owner: str) -> int:
    try:
        effective_uid = os.geteuid()
        account = pwd.getpwuid(effective_uid)
    except (KeyError, OSError) as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_IDENTITY_INVALID"
        ) from exc
    if account.pw_name != owner:
        _fail("R8U_R7G_QUIESCENCE_IDENTITY_INVALID")
    return effective_uid


def capture_fixed_quiescence(
    *,
    terminal_authority: Mapping[str, Any],
    environment: Mapping[str, str],
    scheduler_runner: Callable[
        ..., subprocess.CompletedProcess[bytes]
    ] = subprocess.run,
    process_runner: Callable[
        ..., subprocess.CompletedProcess[bytes]
    ] = subprocess.run,
    tool_validator: Callable[[], None] = _require_observer_tools,
) -> FixedQuiescenceEvidence:
    """Capture exactly one scheduler and one process snapshot for the lock."""

    identity = _fixed_identity(terminal_authority)
    sealed_environment = _validated_environment(
        environment, identity=identity
    )
    try:
        tool_validator()
    except R7GQuiescenceError:
        raise
    except Exception as exc:
        raise R7GQuiescenceError(
            "R8U_R7G_QUIESCENCE_QUERY_TOOL_INVALID"
        ) from exc
    observed_uid = _default_effective_uid(identity.owner)
    observer_pid = os.getpid()
    if (
        isinstance(observed_uid, bool)
        or not isinstance(observed_uid, int)
        or observed_uid < 0
        or isinstance(observer_pid, bool)
        or not isinstance(observer_pid, int)
        or observer_pid <= 0
    ):
        _fail("R8U_R7G_QUIESCENCE_IDENTITY_INVALID")

    scheduler_payload = _query_payload(
        SCHEDULER_COMMAND,
        environment=sealed_environment,
        runner=scheduler_runner,
        maximum_bytes=MAX_SCHEDULER_BYTES,
        failure_code="R8U_R7G_SCHEDULER_QUERY_FAILED",
    )
    process_payload = _query_payload(
        PROCESS_COMMAND,
        environment=sealed_environment,
        runner=process_runner,
        maximum_bytes=MAX_PROCESS_BYTES,
        failure_code="R8U_R7G_PROCESS_QUERY_FAILED",
    )
    scheduler_projection = project_fixed_scheduler_snapshot(
        scheduler_payload, terminal_authority=terminal_authority
    )
    process_projection = project_fixed_process_snapshot(
        process_payload,
        effective_uid=observed_uid,
        observer_pid=observer_pid,
    )
    scheduler_state = build_fixed_scheduler_state(
        scheduler_projection,
        process_projection,
        terminal_authority=terminal_authority,
    )
    return FixedQuiescenceEvidence(
        scheduler_state=scheduler_state,
        scheduler_projection=scheduler_projection,
        process_projection=process_projection,
    )


__all__ = [
    "ARRAY_JOB_ID",
    "ARRAY_JOB_NAME",
    "FINALIZER_JOB_ID",
    "FINALIZER_JOB_NAME",
    "FixedQuiescenceEvidence",
    "OWNER",
    "R7GQuiescenceError",
    "build_fixed_scheduler_state",
    "capture_fixed_quiescence",
    "project_fixed_process_snapshot",
    "project_fixed_scheduler_snapshot",
]
