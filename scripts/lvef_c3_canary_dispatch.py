#!/usr/bin/env python3
"""Durable, exactly-five SGE dispatcher for an owner-authorized C3 canary.

This module has no import-time scheduler effect.  The only function that may
invoke qsub is :func:`dispatch_authorized_canary`, and callers must supply the
already validated, owner-private execution authority.  Every submission is
claimed in a no-clobber snapshot before qsub is invoked; a submission failure
is terminal and can never be retried by this dispatcher.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable, Mapping, Sequence


ORDERED_STAGE_IDS = (
    "DOWNLOAD",
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "CANARY_FINALIZATION",
)
STAGE_RESOURCES = {
    "DOWNLOAD": ("24:00:00", "16G", ()),
    "DICOM_EXTRACTION": ("48:00:00", "64G", ()),
    "ECHOPRIME_EMBEDDING": (
        "24:00:00",
        "64G",
        ("-l", "gpus=1", "-l", "gpu_c=8.0", "-l", "gpu_memory=48G"),
    ),
    "BATCH_PRESERVATION": ("12:00:00", "32G", ()),
    "CANARY_FINALIZATION": ("12:00:00", "32G", ()),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
JOB_ID_RE = re.compile(r"^[0-9]+(?:[.][0-9-]+:[0-9]+)?$")
RUN_ID_RE = re.compile(r"^lvef_c3_exact_five_canary_[a-z0-9]{8}$")

LEDGER_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "authorization_sha256",
        "authorization_file_sha256",
        "run_id", "attempt_id", "output_root", "scheduler_plan_sha256",
        "manifest_file_sha256", "manifest_sha256", "declared_submission_count",
        "submission_count", "active_claim", "failed_stage_id", "stages",
        "production_continuation_triggered",
    }
)
STAGE_RECORD_KEYS = frozenset(
    {
        "stage_id", "ordinal", "status", "predecessor_stage_id",
        "predecessor_job_id", "job_id", "command_sha256", "failure_code",
    }
)


class CanaryDispatchError(RuntimeError):
    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_DISPATCH_INVALID"
        super().__init__(code)
        self.code = code


Submitter = Callable[[Sequence[str]], str]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CanaryDispatchError("CANARY_DISPATCH_EXECUTABLE_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _authority_value(authority: Mapping[str, Any], name: str) -> Any:
    if name not in authority:
        raise CanaryDispatchError("CANARY_DISPATCH_AUTHORITY_INCOMPLETE")
    return authority[name]


def _validate_dispatch_authority(authority: Mapping[str, Any]) -> None:
    if authority.get("owner_authorized") is not True:
        raise CanaryDispatchError("CANARY_DISPATCH_NOT_OWNER_AUTHORIZED")
    run_id = str(_authority_value(authority, "run_id"))
    if RUN_ID_RE.fullmatch(run_id) is None or authority.get("attempt_id") != run_id:
        raise CanaryDispatchError("CANARY_DISPATCH_RUN_ID_INVALID")
    for name in (
        "authorization_sha256", "authorization_file_sha256",
        "launch_authority_sha256",
    ):
        if SHA256_RE.fullmatch(str(_authority_value(authority, name))) is None:
            raise CanaryDispatchError("CANARY_DISPATCH_AUTHORITY_HASH_INVALID")
    manifest = _authority_value(authority, "manifest")
    scheduler_plan = _authority_value(authority, "scheduler_plan")
    scheduler = _authority_value(authority, "scheduler")
    qsub = _authority_value(authority, "qsub")
    worker = _authority_value(authority, "stage_worker")
    launcher = _authority_value(authority, "stage_launcher")
    if not all(isinstance(value, Mapping) for value in (manifest, scheduler_plan, scheduler, qsub, worker, launcher)):
        raise CanaryDispatchError("CANARY_DISPATCH_AUTHORITY_INCOMPLETE")
    if (
        set(scheduler) != {
            "ordered_stage_ids", "scheduler_submission_count",
            "maximum_scheduler_submission_count", "gpu_stage_count",
            "stage_retry_count", "array_expansion_permitted",
            "automatic_resubmission_permitted", "production_continuation",
        }
        or tuple(scheduler.get("ordered_stage_ids", ())) != ORDERED_STAGE_IDS
        or scheduler.get("scheduler_submission_count") != 5
        or scheduler.get("maximum_scheduler_submission_count") != 5
        or scheduler.get("gpu_stage_count") != 1
        or scheduler.get("stage_retry_count") != 0
        or scheduler.get("array_expansion_permitted") is not False
        or scheduler.get("automatic_resubmission_permitted") is not False
        or scheduler.get("production_continuation") is not False
    ):
        raise CanaryDispatchError("CANARY_DISPATCH_SCHEDULER_SCOPE_INVALID")
    for value in (
        manifest.get("file_sha256"), manifest.get("embedded_sha256"),
        scheduler_plan.get("canonical_sha256"), qsub.get("file_sha256"),
        worker.get("file_sha256"), launcher.get("file_sha256"),
    ):
        if SHA256_RE.fullmatch(str(value)) is None:
            raise CanaryDispatchError("CANARY_DISPATCH_AUTHORITY_HASH_INVALID")
    output_root = Path(str(_authority_value(authority, "output_root")))
    if not output_root.is_absolute() or output_root.name != run_id:
        raise CanaryDispatchError("CANARY_DISPATCH_OUTPUT_ROOT_INVALID")
    for executable in (qsub, worker, launcher):
        path = Path(str(executable.get("path")))
        if not path.is_absolute() or sha256_file(path) != executable.get("file_sha256"):
            raise CanaryDispatchError("CANARY_DISPATCH_EXECUTABLE_HASH_MISMATCH")


def initialize_dispatch_ledger(authority: Mapping[str, Any]) -> dict[str, Any]:
    _validate_dispatch_authority(authority)
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_durable_dispatch_ledger_v1",
        "status": "READY",
        "authorization_sha256": str(authority["authorization_sha256"]),
        "authorization_file_sha256": str(authority["authorization_file_sha256"]),
        "run_id": str(authority["run_id"]),
        "attempt_id": str(authority["attempt_id"]),
        "output_root": str(authority["output_root"]),
        "scheduler_plan_sha256": str(authority["scheduler_plan"]["canonical_sha256"]),
        "manifest_file_sha256": str(authority["manifest"]["file_sha256"]),
        "manifest_sha256": str(authority["manifest"]["embedded_sha256"]),
        "declared_submission_count": 5,
        "submission_count": 0,
        "active_claim": None,
        "failed_stage_id": None,
        "stages": [
            {
                "stage_id": stage_id,
                "ordinal": index + 1,
                "status": "PENDING",
                "predecessor_stage_id": None if index == 0 else ORDERED_STAGE_IDS[index - 1],
                "predecessor_job_id": None,
                "job_id": None,
                "command_sha256": None,
                "failure_code": None,
            }
            for index, stage_id in enumerate(ORDERED_STAGE_IDS)
        ],
        "production_continuation_triggered": False,
    }


def validate_dispatch_ledger(ledger: Mapping[str, Any]) -> None:
    if set(ledger) != LEDGER_KEYS:
        raise CanaryDispatchError("CANARY_DISPATCH_LEDGER_SCHEMA_INVALID")
    if (
        ledger.get("schema_version") != 1
        or ledger.get("artifact_type") != "lvef_c3_canary_durable_dispatch_ledger_v1"
        or ledger.get("declared_submission_count") != 5
        or ledger.get("production_continuation_triggered") is not False
        or SHA256_RE.fullmatch(str(ledger.get("authorization_sha256"))) is None
        or SHA256_RE.fullmatch(str(ledger.get("authorization_file_sha256"))) is None
        or SHA256_RE.fullmatch(str(ledger.get("scheduler_plan_sha256"))) is None
        or SHA256_RE.fullmatch(str(ledger.get("manifest_file_sha256"))) is None
        or SHA256_RE.fullmatch(str(ledger.get("manifest_sha256"))) is None
    ):
        raise CanaryDispatchError("CANARY_DISPATCH_LEDGER_AUTHORITY_INVALID")
    stages = ledger.get("stages")
    if not isinstance(stages, list) or len(stages) != 5:
        raise CanaryDispatchError("CANARY_DISPATCH_LEDGER_STAGE_COUNT_INVALID")
    submitted = 0
    active: list[str] = []
    failed: list[str] = []
    prior_job_id: str | None = None
    encountered_pending = False
    for index, raw in enumerate(stages):
        if not isinstance(raw, Mapping) or set(raw) != STAGE_RECORD_KEYS:
            raise CanaryDispatchError("CANARY_DISPATCH_STAGE_RECORD_SCHEMA_INVALID")
        stage_id = ORDERED_STAGE_IDS[index]
        status = raw.get("status")
        if raw.get("stage_id") != stage_id or raw.get("ordinal") != index + 1:
            raise CanaryDispatchError("CANARY_DISPATCH_STAGE_ORDER_INVALID")
        if raw.get("predecessor_stage_id") != (None if index == 0 else ORDERED_STAGE_IDS[index - 1]):
            raise CanaryDispatchError("CANARY_DISPATCH_PREDECESSOR_INVALID")
        if status == "PENDING":
            encountered_pending = True
            if any(raw.get(key) is not None for key in ("predecessor_job_id", "job_id", "command_sha256", "failure_code")):
                raise CanaryDispatchError("CANARY_DISPATCH_PENDING_STAGE_DIRTY")
            continue
        if encountered_pending or status not in {"CLAIMED", "SUBMITTED", "SUBMISSION_FAILED"}:
            raise CanaryDispatchError("CANARY_DISPATCH_STAGE_STATE_INVALID")
        if raw.get("predecessor_job_id") != (None if index == 0 else prior_job_id):
            raise CanaryDispatchError("CANARY_DISPATCH_PREDECESSOR_JOB_MISMATCH")
        if SHA256_RE.fullmatch(str(raw.get("command_sha256"))) is None:
            raise CanaryDispatchError("CANARY_DISPATCH_COMMAND_HASH_INVALID")
        if status == "CLAIMED":
            if raw.get("job_id") is not None or raw.get("failure_code") is not None:
                raise CanaryDispatchError("CANARY_DISPATCH_ACTIVE_CLAIM_INVALID")
            active.append(stage_id)
        elif status == "SUBMITTED":
            if JOB_ID_RE.fullmatch(str(raw.get("job_id"))) is None or raw.get("failure_code") is not None:
                raise CanaryDispatchError("CANARY_DISPATCH_JOB_ID_INVALID")
            prior_job_id = str(raw["job_id"])
            submitted += 1
        else:
            if raw.get("job_id") is not None or raw.get("failure_code") != "QSUB_SUBMISSION_FAILED":
                raise CanaryDispatchError("CANARY_DISPATCH_FAILURE_RECORD_INVALID")
            failed.append(stage_id)
    if len(active) > 1 or len(failed) > 1 or (active and failed):
        raise CanaryDispatchError("CANARY_DISPATCH_TERMINAL_STATE_INVALID")
    if ledger.get("submission_count") != submitted:
        raise CanaryDispatchError("CANARY_DISPATCH_SUBMISSION_COUNT_INVALID")
    if ledger.get("active_claim") != (active[0] if active else None):
        raise CanaryDispatchError("CANARY_DISPATCH_ACTIVE_CLAIM_MISMATCH")
    if ledger.get("failed_stage_id") != (failed[0] if failed else None):
        raise CanaryDispatchError("CANARY_DISPATCH_FAILED_STAGE_MISMATCH")
    expected_status = (
        "SUBMISSION_FAILED" if failed else "CLAIM_ACTIVE" if active
        else "DISPATCHED_FROZEN_DAG" if submitted == 5 else "READY"
    )
    if ledger.get("status") != expected_status:
        raise CanaryDispatchError("CANARY_DISPATCH_STATUS_INVALID")


def _write_snapshot(directory: Path, sequence: int, ledger: Mapping[str, Any]) -> None:
    validate_dispatch_ledger(ledger)
    path = directory / f"dispatch_ledger_{sequence:02d}.restricted.json"
    if os.path.lexists(path):
        raise CanaryDispatchError("CANARY_DISPATCH_SNAPSHOT_COLLISION")
    payload = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                raise CanaryDispatchError("CANARY_DISPATCH_SNAPSHOT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CanaryDispatchError("CANARY_DISPATCH_SNAPSHOT_MODE_INVALID")
    finally:
        os.close(descriptor)


def build_qsub_command(
    authority: Mapping[str, Any], stage_id: str, *, predecessor_job_id: str | None,
    log_root: Path, work_root: Path,
) -> tuple[str, ...]:
    if stage_id not in ORDERED_STAGE_IDS:
        raise CanaryDispatchError("CANARY_DISPATCH_UNDECLARED_STAGE")
    qsub = str(authority["qsub"]["path"])
    launcher = str(authority["stage_launcher"]["path"])
    authorization_path = str(authority["authorization_path"])
    run_tag = hashlib.sha256(
        f"{authority['run_id']}:{stage_id}".encode("utf-8")
    ).hexdigest()[:12]
    runtime, memory, gpu = STAGE_RESOURCES[stage_id]
    command = [
        qsub, "-terse", "-r", "n", "-P", "mimicecho", "-N", f"c3c_{stage_id.lower()[:8]}_{run_tag}",
        "-wd", str(work_root), "-o", str(log_root), "-e", str(log_root),
        "-l", f"h_rt={runtime}", "-l", f"mem_total={memory}", *gpu,
    ]
    if predecessor_job_id is not None:
        if JOB_ID_RE.fullmatch(predecessor_job_id) is None:
            raise CanaryDispatchError("CANARY_DISPATCH_PREDECESSOR_JOB_INVALID")
        command.extend(("-hold_jid", predecessor_job_id))
    command.extend((launcher, authorization_path, stage_id))
    return tuple(command)


def default_qsub_submitter(command: Sequence[str]) -> str:
    result = subprocess.run(
        list(command), check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise CanaryDispatchError("CANARY_QSUB_SUBMISSION_FAILED")
    value = result.stdout.strip()
    if JOB_ID_RE.fullmatch(value) is None:
        raise CanaryDispatchError("CANARY_QSUB_JOB_ID_INVALID")
    return value


def dispatch_authorized_canary(
    authority: Mapping[str, Any], *, submitter: Submitter = default_qsub_submitter,
) -> dict[str, Any]:
    """Submit only the frozen five jobs and persist every no-retry claim."""

    ledger = initialize_dispatch_ledger(authority)
    output_root = Path(str(authority["output_root"]))
    parent = output_root.parent
    if parent.is_symlink() or not parent.is_dir() or os.path.lexists(output_root):
        raise CanaryDispatchError("CANARY_DISPATCH_RUN_ROOT_COLLISION")
    output_root.mkdir(mode=0o700)
    claims_root = output_root / "scheduler_claims"
    logs_root = output_root / "scheduler_logs"
    work_root = output_root / "scheduler_work"
    execution_claims_root = output_root / "stage_execution_claims"
    stage_results_root = output_root / "stage_results"
    for directory in (
        claims_root,
        logs_root,
        work_root,
        execution_claims_root,
        stage_results_root,
    ):
        directory.mkdir(mode=0o700)
    sequence = 0
    _write_snapshot(claims_root, sequence, ledger)
    predecessor_job_id: str | None = None
    for index, stage_id in enumerate(ORDERED_STAGE_IDS):
        stage_log = logs_root / stage_id
        stage_work = work_root / stage_id
        stage_log.mkdir(mode=0o700)
        stage_work.mkdir(mode=0o700)
        command = build_qsub_command(
            authority, stage_id, predecessor_job_id=predecessor_job_id,
            log_root=stage_log, work_root=stage_work,
        )
        claimed = copy.deepcopy(ledger)
        record = claimed["stages"][index]
        record["status"] = "CLAIMED"
        record["predecessor_job_id"] = predecessor_job_id
        record["command_sha256"] = canonical_json_sha256(list(command))
        claimed["active_claim"] = stage_id
        claimed["status"] = "CLAIM_ACTIVE"
        sequence += 1
        _write_snapshot(claims_root, sequence, claimed)
        try:
            job_id = submitter(command)
            if JOB_ID_RE.fullmatch(str(job_id)) is None:
                raise CanaryDispatchError("CANARY_QSUB_JOB_ID_INVALID")
        except Exception as exc:
            failed = copy.deepcopy(claimed)
            failed["stages"][index]["status"] = "SUBMISSION_FAILED"
            failed["stages"][index]["failure_code"] = "QSUB_SUBMISSION_FAILED"
            failed["active_claim"] = None
            failed["failed_stage_id"] = stage_id
            failed["status"] = "SUBMISSION_FAILED"
            sequence += 1
            _write_snapshot(claims_root, sequence, failed)
            raise CanaryDispatchError("CANARY_QSUB_SUBMISSION_FAILED") from exc
        ledger = copy.deepcopy(claimed)
        ledger["stages"][index]["status"] = "SUBMITTED"
        ledger["stages"][index]["job_id"] = str(job_id)
        ledger["submission_count"] += 1
        ledger["active_claim"] = None
        ledger["status"] = (
            "DISPATCHED_FROZEN_DAG" if ledger["submission_count"] == 5 else "READY"
        )
        sequence += 1
        _write_snapshot(claims_root, sequence, ledger)
        predecessor_job_id = str(job_id)
    validate_dispatch_ledger(ledger)
    return ledger
