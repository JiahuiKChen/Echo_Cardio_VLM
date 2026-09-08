#!/usr/bin/env python3
"""One bound successor for the sealed R7H zero-payload ADC failure.

The original terminal execution remains immutable.  This controller owns fresh
control publications and scheduler identities; scientific payloads retain the
original attempt's canonical paths.  It never makes AUTHENTICATION retryable.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_r8u_r7h_continuation as original
import lvef_c3_orchestration_core as core
import lvef_c3_full_sequential as sequential
import lvef_c3_production_stages as stages
import finalize_lvef_c3_production as finalizer

capacity = original.capacity
scheduler = original.scheduler
historical = original.historical
accounting = original.accounting
BASE_COMMIT = "91277df0157ff806694a1d84317d25f3df8edb8f"
EXECUTION_ID = "r7h_auth_successor_v1"
ATTEMPT_ID = original.ATTEMPT_ID
PLAN_SHA256 = original.PLAN_SHA256
SCIENTIFIC_COMMIT = original.SCIENTIFIC_COMMIT
ATTEMPT_ROOT = original.ATTEMPT_ROOT
PRODUCTION_ROOT = original.PRODUCTION_ROOT
SUCCESSOR_ROOT = ATTEMPT_ROOT / EXECUTION_ID
RECONCILIATION_PATH = original.R7H_ROOT / "overnight_terminal_reconciliation.restricted.json"
RECONCILIATION_SHA256 = "e5828f7c45a77c154a3c66fafd5061ec023b39a93a11e1cb7a039dbddc81e07a"
TERMINAL_JOURNAL_HEAD_SHA256 = "80c89fbcdb4b268ce76f306c959745ef01f4b5ba8bd9c56540c10d5e3eec0824"
TERMINAL_JOURNAL_SHA256 = "45fa7f0049a2a72305dad8515edc34e549e7fa7fc51ea7d8a65938896d54470a"
TERMINAL_FAILURE_SHA256 = "5f31459f81c050d88e3c8e3cef0a3ba68b319de1b8cbfb2c9e902eb0858b736b"
CONSUMED_ARRAY_JOB_ID = "7489283"
CONSUMED_FINALIZER_JOB_ID = "7489284"
CONSUMED_CONTROL_SHA256 = {
    "reconciliation": RECONCILIATION_SHA256,
    "array_submission": "95431da2f867b3662c00d066eb3160fcf0f0fadb482b1b2189db708b4ca7c637",
    "finalizer_submission": "f0cedcda2041240931db055b8a924cf1c1fea6210823f05bae737568d64524c5",
    "submission": "4dcb334a0f02cb65b1900f252384cde2dac28a868b8635d45740863a87fcfc51",
    "claim": "6b0cf54f6a38287c33187b94c7246c1022846ff5f26c9612729026637a3d35b1",
    "finalizer_authority": "e8444355808e9132a926075a92d8ee21f5bb8358098616d63e3e451c9ffe7af0",
}
CONSUMED_PATHS = {
    "reconciliation": RECONCILIATION_PATH,
    "array_submission": original.ARRAY_SUBMISSION_PATH,
    "finalizer_submission": original.FINALIZER_SUBMISSION_PATH,
    "submission": original.CONTINUATION_SUBMISSION_PATH,
    "claim": original.CONTINUATION_CLAIM_PATH,
    "finalizer_authority": original.FINALIZER_AUTHORITY_PATH,
}
CONSUMED_RECEIPT_PATH = SUCCESSOR_ROOT / "consumed_authentication_failure.restricted.json"
ACCOUNT_PATH = SUCCESSOR_ROOT / "scheduler_account_authority.restricted.json"
CAPACITY_ROOT = SUCCESSOR_ROOT / "capacity"
CAPACITY_RECEIPT_PATH = CAPACITY_ROOT / "capacity.restricted.json"
CAPACITY_PRODUCER_PATH = CAPACITY_ROOT / "producer.restricted.json"
CAPACITY_RAW_ROOT = CAPACITY_ROOT / "raw_captures"
STATIC_PLAN_PATH = CAPACITY_ROOT / "static_plan.restricted.json"
PROBE_ROOT = SUCCESSOR_ROOT / "credential_probe"
PROBE_SCHEDULER_ROOT = PROBE_ROOT / "scheduler"
PROBE_AUTHORITY_PATH = PROBE_ROOT / "authority.restricted.json"
PROBE_SUBMISSION_PATH = PROBE_ROOT / "submission.restricted.json"
PROBE_RESULT_PATH = PROBE_ROOT / "result.restricted.json"
PROBE_ACCOUNTING_PATH = PROBE_ROOT / "accounting.restricted.json"
PROBE_TERMINAL_PATH = PROBE_ROOT / "terminal.restricted.json"
CLAIM_PATH = SUCCESSOR_ROOT / "continuation_claim.restricted.json"
SCHEDULER_ROOT = SUCCESSOR_ROOT / "scheduler"
ARRAY_SUBMISSION_PATH = SCHEDULER_ROOT / "array_submission.restricted.json"
FINALIZER_SUBMISSION_PATH = SCHEDULER_ROOT / "finalizer_submission.restricted.json"
SUBMISSION_PATH = SCHEDULER_ROOT / "submission.restricted.json"
DOWNLOAD_AUTHORITY_PATH = SUCCESSOR_ROOT / "download_authority.restricted.json"
WORKER_ROOT = SUCCESSOR_ROOT / "worker_context"
FINALIZER_AUTHORITY_PATH = SUCCESSOR_ROOT / "finalizer_authority.restricted.json"
FINALIZER_BINDING_PATH = SUCCESSOR_ROOT / "finalizer_successor_binding.restricted.json"
FINALIZER_AGGREGATE_PATH = original.FINALIZER_AGGREGATE_PATH
RUNNER_PATH = original.RUNNER_PATH
ADC_PASS = "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS"
PROBE_PASS = "PASS_R7H_AUTH_SUCCESSOR_CREDENTIAL_PROBE"
SUBMISSION_PASS = "PASS_R7H_AUTH_SUCCESSOR_TASKS_17_19_SUBMITTED"
SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,127}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
JOB = re.compile(r"[1-9][0-9]{0,19}\Z")


class AuthenticationSuccessorError(RuntimeError):
    def __init__(self, code: str, *, stage: str | None = None):
        self.code = code if SAFE_CODE.fullmatch(code) else "R7HA_UNEXPECTED_CONTROL_FAILURE"
        self.stage = stage if isinstance(stage, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", stage) else None
        super().__init__(self.code)


def _fail(code: str) -> None:
    raise AuthenticationSuccessorError(code)


def _exact(left: object, right: object) -> bool:
    return original._exact(left, right)


def _read(path: Path) -> tuple[dict[str, Any], str]:
    value, _body, digest = original._read_private_json(path, code="R7HA_CONTROL_INVALID")
    return value, digest


def _read_bound(path: Path, digest: str) -> dict[str, Any]:
    body = original._read_private_bytes(path, code="R7HA_CONSUMED_EVIDENCE_INVALID")
    if hashlib.sha256(body).hexdigest() != digest:
        _fail("R7HA_CONSUMED_EVIDENCE_HASH_MISMATCH")
    return original._strict_json(body, code="R7HA_CONSUMED_EVIDENCE_INVALID")


def _write(path: Path, value: Mapping[str, Any]) -> str:
    return original._write_private_json(path, value, code="R7HA_OUTPUT_COLLISION_OR_PUBLICATION_FAILURE")


def _directory(path: Path, *, fresh: bool = False) -> None:
    original._ensure_private_directory(path, fresh=fresh)


def current_successor_commit() -> str:
    """Accept a clean, published, linear correction of the consumed producer."""
    current = sequential._current_commit()
    if current == BASE_COMMIT or sequential._git("merge-base", BASE_COMMIT, current) != BASE_COMMIT:
        _fail("R7HA_CORRECTIVE_LINEAGE_INVALID")
    if sequential._git("rev-list", "--merges", f"{BASE_COMMIT}..{current}"):
        _fail("R7HA_CORRECTIVE_LINEAGE_INVALID")
    # Every descendant must remain within the authorized authentication and
    # execution-control correction.  This is not an unrestricted ancestor gate.
    exact = {
        "scripts/lvef_c3_r8u_r7h_auth_successor.py", "scripts/lvef_c3_r7h_adc.py",
        "scripts/lvef_c3_orchestration_core.py", "scripts/lvef_c3_full_sequential.py",
        "scripts/lvef_c3_production_stages.py", "scripts/finalize_lvef_c3_production.py",
        "scripts/preserve_lvef_c3_production_batch.py", "scripts/retire_lvef_c3_extracted_cache_v2.py",
        "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh",
        "scripts/lvef_c3_r8r_recovery_continuation.py",
        "configs/lvef_c3_minimal_canary.yaml",
        "configs/lvef_c3_canary_scheduler_plan_v1.json",
    }
    changed = sequential._git("diff", "--name-only", BASE_COMMIT, current).splitlines()
    if not changed or any(
        path not in exact
        and not (path.startswith("tests/test_lvef_c3_") and path.endswith(".py"))
        and not (path.startswith("docs/lvef_multitask/phase1i_") and path.endswith(".md"))
        for path in changed
    ):
        _fail("R7HA_CORRECTIVE_SCOPE_INVALID")
    return current


def _common(kind: str, status: str, *, commit: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1, "artifact_type": f"lvef_c3_r7h_auth_successor_{kind}_v1",
        "status": status, "execution_id": EXECUTION_ID,
        "implementation_commit": commit or current_successor_commit(),
        "consumed_implementation_commit": BASE_COMMIT, "scientific_commit": SCIENTIFIC_COMMIT,
        "attempt_id": ATTEMPT_ID, "batch_plan_sha256": PLAN_SHA256,
        "consumed_array_job_id": CONSUMED_ARRAY_JOB_ID,
        "consumed_finalizer_job_id": CONSUMED_FINALIZER_JOB_ID,
        "consumed_reconciliation_sha256": RECONCILIATION_SHA256,
        "identifiers_emitted": False, "restricted_paths_emitted": False,
    }


def _validate_common(value: Mapping[str, Any], kind: str, status: str, extra: set[str]) -> None:
    expected = _common(kind, status)
    if set(value) != set(expected) | extra or any(not _exact(value.get(k), v) for k, v in expected.items()):
        _fail("R7HA_CONTROL_AUTHORITY_MISMATCH")


def load_consumed_authentication_failure(*, require_pristine_tail: bool = True, run: sequential.FullRun | None = None) -> dict[str, Any]:
    values = {role: _read_bound(path, CONSUMED_CONTROL_SHA256[role]) for role, path in CONSUMED_PATHS.items()}
    if values["array_submission"].get("array_job_id") != CONSUMED_ARRAY_JOB_ID or values["finalizer_submission"].get("finalizer_job_id") != CONSUMED_FINALIZER_JOB_ID:
        _fail("R7HA_CONSUMED_JOB_MISMATCH")
    if values["reconciliation"].get("task17_journal_sha256") != TERMINAL_JOURNAL_HEAD_SHA256 or values["reconciliation"].get("task17_failure_receipt_sha256") != TERMINAL_FAILURE_SHA256:
        _fail("R7HA_RECONCILIATION_JOURNAL_BINDING_MISMATCH")
    for index, digest in enumerate(original.PREFIX_FINAL_RECEIPT_SHA256):
        _read_bound(ATTEMPT_ROOT / "batches" / f"c3_batch_{index:03d}" / "preservation" / "batch_finalization_receipt.restricted.json", digest)
    effective_run = run or _load_run("R7HA_CONSUMED_EVIDENCE")
    raw = core.validate_consumed_authentication_failure(
        plan=effective_run.plan, requirements=effective_run.requirements,
        authority=effective_run.runtime_authority, attempt_id=ATTEMPT_ID,
        output_root=ATTEMPT_ROOT / "raw", require_pristine_tail=require_pristine_tail)
    if raw.get("terminal_journal_sha256") != TERMINAL_JOURNAL_SHA256 or TERMINAL_FAILURE_SHA256 not in raw.get("control_file_sha256", {}).values():
        _fail("R7HA_TERMINAL_DOWNLOAD_BINDING_MISMATCH")
    if require_pristine_tail:
        for index in range(16, 19):
            batch = f"c3_batch_{index:03d}"
            for path in (ATTEMPT_ROOT / "extracted_cache" / batch, ATTEMPT_ROOT / "batches" / batch):
                if os.path.lexists(path):
                    original._validate_private_directory(path, code="R7HA_TAIL_OUTPUT_INVALID")
                    if any(path.iterdir()):
                        _fail("R7HA_PREEXISTING_SCIENTIFIC_OUTPUT")
        if os.path.lexists(FINALIZER_AGGREGATE_PATH.parent):
            original._validate_private_directory(FINALIZER_AGGREGATE_PATH.parent, code="R7HA_COHORT_OUTPUT_INVALID")
            if any(FINALIZER_AGGREGATE_PATH.parent.iterdir()):
                _fail("R7HA_PREEXISTING_COHORT_OUTPUT")
    return {
        "consumed_control_sha256": dict(CONSUMED_CONTROL_SHA256),
        "terminal_journal_sha256": TERMINAL_JOURNAL_SHA256,
        "terminal_failure_sha256": TERMINAL_FAILURE_SHA256,
        "prefix_final_receipt_sha256": list(original.PREFIX_FINAL_RECEIPT_SHA256),
        "raw_failure_projection": raw,
    }


def _load_run(job_id: str) -> sequential.FullRun:
    commit = current_successor_commit()
    authority = original.minimal.discover_live_authority(runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY)
    if authority.governing_commit != commit:
        _fail("R7HA_RUNTIME_AUTHORITY_MISMATCH")
    scientific = replace(authority, governing_commit=SCIENTIFIC_COMMIT)
    plan, requirements, contract = sequential._build_frozen_plan(scientific)
    if core.validate_current_batch_plan_v3(plan, requirements=requirements) != PLAN_SHA256:
        _fail("R7HA_PLAN_MISMATCH")
    plan_path = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
    observed = sequential._load_full_batch_plan_payload(plan_path, expected_bytes=historical.ORIGINAL_PLAN_BYTES, expected_sha256=PLAN_SHA256)
    if not _exact(observed, plan):
        _fail("R7HA_PLAN_MISMATCH")
    launch = _read_bound(ATTEMPT_ROOT / "full_launch_authority.restricted.json", historical.ORIGINAL_LAUNCH_SHA256)
    run = sequential.FullRun(
        authority=scientific, plan=plan, requirements=requirements, contract=contract,
        contract_path=sequential.CONTRACT_PATH, plan_sha256=PLAN_SHA256,
        runtime_authority=core.validate_runtime_authority({**plan["authority"], "batch_plan_sha256": PLAN_SHA256}),
        attempt_id=ATTEMPT_ID, production_root=PRODUCTION_ROOT, attempt_root=ATTEMPT_ROOT,
        plan_path=plan_path, launch_authority=launch,
        launch_authority_sha256=historical.ORIGINAL_LAUNCH_SHA256, scheduler_job_identity=job_id,
    )
    sequential._validate_full_run(run)
    return run


def _script_authority() -> dict[str, str]:
    return {role: core.sha256_file(path) for role, path in {
        "controller": Path(__file__).resolve(), "runner": RUNNER_PATH,
        "sequential": Path(sequential.__file__).resolve(), "core": Path(core.__file__).resolve(),
        "stages": Path(stages.__file__).resolve(), "finalizer": Path(finalizer.__file__).resolve(),
        "credential": Path(__file__).resolve().with_name("lvef_c3_r7h_adc.py"),
    }.items()}


def _build_account(environment: Mapping[str, str]) -> dict[str, Any]:
    if Path(sys.executable) != scheduler.ECHOPRIME_PYTHON:
        _fail("R7HA_PYTHON_AUTHORITY_MISMATCH")
    user = pwd.getpwuid(os.geteuid())
    return {**_common("scheduler_account", "AUTHORIZED_R7HA_SCHEDULER_ACCOUNT"),
        "expected_effective_uid": os.geteuid(), "expected_scheduler_username": user.pw_name,
        "canonical_home": user.pw_dir, "sealed_qsub_environment": dict(environment),
        "qsub_environment_sha256": scheduler.qsub_environment_sha256(environment),
        "python_sha256": stages.resolved_python_executable_sha256(scheduler.ECHOPRIME_PYTHON),
        "script_authority": _script_authority()}


def _account() -> dict[str, Any]:
    value, _digest = _read(ACCOUNT_PATH)
    environment = value.get("sealed_qsub_environment")
    _validate_common(value, "scheduler_account", "AUTHORIZED_R7HA_SCHEDULER_ACCOUNT", {
        "expected_effective_uid", "expected_scheduler_username", "canonical_home", "sealed_qsub_environment",
        "qsub_environment_sha256", "python_sha256", "script_authority"})
    if (not isinstance(environment, Mapping) or value["expected_effective_uid"] != os.geteuid()
        or value["qsub_environment_sha256"] != scheduler.qsub_environment_sha256(environment)
        or value["script_authority"] != _script_authority()
        or Path(sys.executable) != scheduler.ECHOPRIME_PYTHON
        or value["python_sha256"] != stages.resolved_python_executable_sha256(scheduler.ECHOPRIME_PYTHON)):
        _fail("R7HA_SCHEDULER_ACCOUNT_INVALID")
    return value


def _qsub_command(role: str, *, array_job_id: str | None = None) -> list[str]:
    commit = current_successor_commit()
    if role == "probe":
        command = original._probe_qsub_command(commit)
    elif role == "array":
        command = original._array_qsub_command(commit)
    elif role == "finalizer" and array_job_id is not None:
        command = original._finalizer_qsub_command(commit, array_job_id)
    else:
        _fail("R7HA_SUBMISSION_ROLE_INVALID")
    command[command.index("-N") + 1] = _job_name(role, commit)
    command[command.index("-o") + 1] = str(PROBE_SCHEDULER_ROOT if role == "probe" else SCHEDULER_ROOT)
    return command


def _submission(role: str, job_id: str, *, authority_sha: str, array_job_id: str | None = None) -> dict[str, Any]:
    if JOB.fullmatch(job_id) is None or job_id in {CONSUMED_ARRAY_JOB_ID, CONSUMED_FINALIZER_JOB_ID}:
        _fail("R7HA_SUBMISSION_JOB_INVALID")
    root = PROBE_SCHEDULER_ROOT if role == "probe" else SCHEDULER_ROOT
    parser = original._parse_probe_qsub_stdout if role == "probe" else original._parse_array_qsub_stdout if role == "array" else scheduler.parse_numeric_qsub_stdout
    evidence = original._qsub_evidence(root, role, parser=parser, expected_job_id=job_id)
    return {**_common(f"{role}_submission", "PASS_R7HA_EXACT_QSUB"),
        "role": role, "job_id": job_id, "job_name": _job_name(role),
        "authority_sha256": authority_sha, "scheduler_account_sha256": core.sha256_file(ACCOUNT_PATH),
        "qsub_argv_sha256": core.canonical_json_sha256({"argv": _qsub_command(role, array_job_id=array_job_id)}),
        "qsub_environment_sha256": _account()["qsub_environment_sha256"],
        "qsub_evidence": evidence, "held_on_array_job_id": array_job_id,
        "task_range": "17" if role == "probe" else "17-19" if role == "array" else None,
        "array_max_concurrency": 1 if role in {"probe", "array"} else None}


def _validate_submission(role: str) -> dict[str, Any]:
    path = {"probe": PROBE_SUBMISSION_PATH, "array": ARRAY_SUBMISSION_PATH, "finalizer": FINALIZER_SUBMISSION_PATH}[role]
    value, _ = _read(path)
    authority_path = PROBE_AUTHORITY_PATH if role == "probe" else CLAIM_PATH
    expected = _submission(role, str(value.get("job_id", "")), authority_sha=core.sha256_file(authority_path),
        array_job_id=(str(_read(ARRAY_SUBMISSION_PATH)[0]["job_id"]) if role == "finalizer" else None))
    if not _exact(value, expected):
        _fail("R7HA_SUBMISSION_BINDING_MISMATCH")
    if role == "probe":
        _validate_probe_authority()
    return value


def _validate_probe_authority(run: sequential.FullRun | None = None) -> dict[str, Any]:
    value, _ = _read(PROBE_AUTHORITY_PATH)
    _validate_common(value, "probe_authority", "AUTHORIZED_R7HA_CPU_CREDENTIAL_PROBE", {
        "capacity_receipt_sha256", "consumed_failure_sha256", "scheduler_account_sha256",
        "login_credential_check", "topology", "live_references", "cpu_slots", "wall_seconds_maximum",
        "gpu_requested", "metadata_requests_authorized", "scientific_body_reads_authorized"})
    if (value["capacity_receipt_sha256"] != core.sha256_file(CAPACITY_RECEIPT_PATH)
        or value["consumed_failure_sha256"] != core.sha256_file(CONSUMED_RECEIPT_PATH)
        or value["scheduler_account_sha256"] != core.sha256_file(ACCOUNT_PATH)
        or type(value["cpu_slots"]) is not int or value["cpu_slots"] != 1
        or type(value["wall_seconds_maximum"]) is not int or value["wall_seconds_maximum"] != 600
        or value["gpu_requested"] is not False or value["metadata_requests_authorized"] is not True
        or value["scientific_body_reads_authorized"] is not False
        or value["login_credential_check"].get("status") != ADC_PASS):
        _fail("R7HA_PROBE_AUTHORITY_INVALID")
    if run is not None:
        _replay_credential(value["login_credential_check"], run=run, role="login", job_id=None)
    return value


def submit_auth_successor_probe(*, qsub_runner: Callable[..., Any] = subprocess.run,
        qstat_runner: Callable[..., Any] = subprocess.run, process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    scheduler.validate_scheduler_tools()
    run = _load_run("R7HA_PROBE_SUBMITTER")
    load_consumed_authentication_failure(run=run)
    _capacity(run)
    account = _account()
    credential = _credential(run, role="login")
    references = _references(environment=account["sealed_qsub_environment"], qstat_runner=qstat_runner, process_runner=process_runner)
    topology = _topology(references)
    _directory(PROBE_ROOT, fresh=True)
    _directory(PROBE_SCHEDULER_ROOT, fresh=True)
    authority = {**_common("probe_authority", "AUTHORIZED_R7HA_CPU_CREDENTIAL_PROBE"),
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "consumed_failure_sha256": core.sha256_file(CONSUMED_RECEIPT_PATH),
        "scheduler_account_sha256": core.sha256_file(ACCOUNT_PATH),
        "login_credential_check": credential, "topology": dict(topology), "live_references": references,
        "cpu_slots": 1, "wall_seconds_maximum": 600, "gpu_requested": False,
        "metadata_requests_authorized": True, "scientific_body_reads_authorized": False}
    digest = _write(PROBE_AUTHORITY_PATH, authority)
    job_id = scheduler._capture_qsub("probe", _qsub_command("probe"), root=PROBE_SCHEDULER_ROOT,
        environment=account["sealed_qsub_environment"], runner=qsub_runner, parser=original._parse_probe_qsub_stdout)
    _write(PROBE_SUBMISSION_PATH, _submission("probe", job_id, authority_sha=digest))
    _validate_submission("probe")
    return {"status": "PASS_R7HA_CPU_PROBE_SUBMITTED", "probe_job_id": job_id}


def _worker_path(role: str, task: str | None) -> Path:
    if role == "probe":
        return PROBE_ROOT / "worker_context.restricted.json"
    return WORKER_ROOT / (f"array_task_{task}.restricted.json" if role == "array" else "finalizer.restricted.json")


def _validate_recorded_worker(role: str, task: str | None) -> dict[str, Any]:
    """Replay recorded worker evidence without impersonating a live dispatch."""
    value, _ = _read(_worker_path(role, task))
    _validate_common(value, "worker_context", "PASS_R7HA_CURRENT_WORKER_IDENTITY", {
        "role", "job_id", "task_id", "job_name", "submission_sha256", "scheduler_account_sha256",
        "authority_sha256", "worker_diagnostics", "qstat_diagnostic"})
    submission = _validate_submission(role)
    submission_path = PROBE_SUBMISSION_PATH if role == "probe" else ARRAY_SUBMISSION_PATH if role == "array" else FINALIZER_SUBMISSION_PATH
    worker = value["worker_diagnostics"]
    qstat = value["qstat_diagnostic"]
    if (value["role"] != role or value["task_id"] != task or value["job_id"] != submission["job_id"]
        or value["job_name"] != _job_name(role) or value["submission_sha256"] != core.sha256_file(submission_path)
        or value["scheduler_account_sha256"] != core.sha256_file(ACCOUNT_PATH)
        or value["authority_sha256"] != submission["authority_sha256"]
        or not isinstance(worker, Mapping) or set(worker) != set(scheduler.WorkerSchedulerDiagnostics._fields)
        or any(worker.get(field) is not True for field in (
            "effective_uid_match", "job_id_match", "task_context_match", "job_role_match", "runner_sha256_match",
            "python_sha256_match", "implementation_commit_match", "qsub_environment_sha256_match"))
        or not isinstance(worker.get("classifications"), list) or not worker["classifications"]
        or any(item not in scheduler.WORKER_DIAGNOSTIC_CLASSIFICATIONS for item in worker["classifications"])
        or not isinstance(qstat, Mapping) or set(qstat) != set(scheduler.R8UR7DQstatDiagnostic._fields)
        or qstat.get("classification") not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS
        or any(qstat.get(field) is not True for field in ("job_id_equality", "task_id_equality", "owner_equality", "full_job_name_equality"))
        or type(qstat.get("observation_count")) is not int or qstat["observation_count"] < 1
        or not isinstance(qstat.get("observations"), list) or len(qstat["observations"]) != qstat["observation_count"]):
        _fail("R7HA_RECORDED_WORKER_INVALID")
    for observation in qstat["observations"]:
        if (not isinstance(observation, Mapping) or set(observation) != set(scheduler.R8UR7DQstatObservation._fields)
            or type(observation.get("record_present")) is not bool
            or (observation["record_present"] and any(observation.get(field) is not True for field in (
                "unique", "job_id_match", "task_id_match", "owner_match", "full_job_name_match")))):
            _fail("R7HA_RECORDED_WORKER_INVALID")
    return value


def validate_successor_worker_submission(*, current_job_id: str, role: str,
        qstat_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    account = _account()
    if role not in {"probe", "array", "finalizer"} or JOB.fullmatch(current_job_id) is None:
        _fail("R7HA_WORKER_ROLE_INVALID")
    controls = (PROBE_SUBMISSION_PATH,) if role == "probe" else (SUBMISSION_PATH, DOWNLOAD_AUTHORITY_PATH)
    for control in controls:
        original._wait_for_private_control(control, code="R7HA_SUBMISSION_RECEIPT_TIMEOUT")
    submission = _validate_submission(role)
    if role != "probe":
        _validate_continuation()
    expected_job = submission["job_id"]
    task = str(os.environ.get("SGE_TASK_ID", "undefined"))
    expected_task = "17" if role == "probe" else task if role == "array" else None
    if (current_job_id != expected_job or os.environ.get("JOB_ID") != expected_job
        or os.environ.get("JOB_NAME") != _job_name(role)
        or (role == "probe" and task != "17") or (role == "array" and task not in {"17", "18", "19"})
        or (role == "finalizer" and task not in {"", "undefined"})
        or os.environ.get("NSLOTS") != ("1" if role == "probe" else "4")
        or bool(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != (role == "array")):
        _fail("R7HA_CURRENT_WORKER_IDENTITY_MISMATCH")
    commit = current_successor_commit()
    context = scheduler.build_worker_scheduler_context(
        expected_effective_uid=account["expected_effective_uid"], expected_scheduler_username=account["expected_scheduler_username"],
        canonical_home=account["canonical_home"], expected_job_id=expected_job,
        expected_job_role=f"R7HA_{role.upper()}", observed_job_role=f"R7HA_{role.upper()}",
        expected_qsub_environment_sha256=account["qsub_environment_sha256"], sealed_qsub_environment=account["sealed_qsub_environment"],
        expected_implementation_commit=commit, observed_implementation_commit=commit,
        expected_runner_sha256=account["script_authority"]["runner"], observed_runner_sha256=core.sha256_file(RUNNER_PATH),
        expected_python_sha256=account["python_sha256"], observed_python_sha256=stages.resolved_python_executable_sha256(scheduler.ECHOPRIME_PYTHON),
        source_environment=os.environ, expected_task_id=expected_task)
    path = _worker_path(role, expected_task)
    stable = {**_common("worker_context", "PASS_R7HA_CURRENT_WORKER_IDENTITY"),
        "role": role, "job_id": expected_job, "task_id": expected_task, "job_name": _job_name(role),
        "submission_sha256": core.sha256_file(PROBE_SUBMISSION_PATH if role == "probe" else ARRAY_SUBMISSION_PATH if role == "array" else FINALIZER_SUBMISSION_PATH),
        "scheduler_account_sha256": core.sha256_file(ACCOUNT_PATH),
        "authority_sha256": submission["authority_sha256"],
        "worker_diagnostics": original._worker_diagnostics_value(context.diagnostics)}
    if os.path.lexists(path):
        existing, _ = _read(path)
        if set(existing) != set(stable) | {"qstat_diagnostic"} or any(not _exact(existing.get(k), v) for k, v in stable.items()):
            _fail("R7HA_WORKER_RECEIPT_MISMATCH")
        if existing["qstat_diagnostic"].get("classification") not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS:
            _fail("R7HA_WORKER_RECEIPT_MISMATCH")
        return existing
    diagnostic = scheduler.diagnose_r8u_r7d_qstat_self(environment=context.environment,
        expected_job_id=expected_job, expected_task_id=expected_task,
        expected_owner=account["expected_scheduler_username"], expected_full_job_name=_job_name(role), runner=qstat_runner)
    if diagnostic.classification not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS:
        _fail("R7HA_WORKER_QSTAT_CONTRADICTION")
    if role != "probe":
        _directory(WORKER_ROOT)
    value = {**stable, "qstat_diagnostic": original._qstat_diagnostic_value(diagnostic)}
    _write(path, value)
    return value


def run_auth_successor_probe(*, qstat_runner: Callable[..., Any] = subprocess.run,
        process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    worker = validate_successor_worker_submission(current_job_id=job_id, role="probe", qstat_runner=qstat_runner)
    run = _load_run(job_id)
    load_consumed_authentication_failure(run=run)
    _capacity(run)
    _validate_probe_authority(run)
    account = _account()
    references = _references(environment=account["sealed_qsub_environment"], expected={"probe": job_id}, qstat_runner=qstat_runner, process_runner=process_runner)
    topology = _topology(references)
    credential = _credential(run, role="probe", job_id=job_id)
    result = {**_common("probe_result", PROBE_PASS), "job_id": job_id, "task_id": 17,
        "submission_sha256": core.sha256_file(PROBE_SUBMISSION_PATH),
        "worker_receipt_sha256": core.sha256_file(_worker_path("probe", "17")),
        "credential_check": credential, "credential_readiness_sha256": core.canonical_json_sha256(credential),
        "topology": dict(topology), "live_references": references,
        "gpu_executions": 0, "scientific_body_reads": 0}
    _write(PROBE_RESULT_PATH, result)
    return {"status": PROBE_PASS, "probe_job_id": job_id, "credential_status": credential["status"]}


def _validate_probe_result(run: sequential.FullRun | None = None) -> dict[str, Any]:
    value, _ = _read(PROBE_RESULT_PATH)
    _validate_common(value, "probe_result", PROBE_PASS, {"job_id", "task_id", "submission_sha256", "worker_receipt_sha256",
        "credential_check", "credential_readiness_sha256", "topology", "live_references", "gpu_executions", "scientific_body_reads"})
    submission = _validate_submission("probe")
    if (value["job_id"] != submission["job_id"] or value["task_id"] != 17
        or value["submission_sha256"] != core.sha256_file(PROBE_SUBMISSION_PATH)
        or value["worker_receipt_sha256"] != core.sha256_file(_worker_path("probe", "17"))
        or value["credential_check"].get("status") != ADC_PASS
        or value["credential_readiness_sha256"] != core.canonical_json_sha256(value["credential_check"])
        or value["gpu_executions"] != 0 or value["scientific_body_reads"] != 0):
        _fail("R7HA_PROBE_RESULT_INVALID")
    authority = _validate_probe_authority(run)
    credential = value["credential_check"]
    login = authority["login_credential_check"]
    if (credential.get("role") != "probe" or credential.get("job_id") != value["job_id"]
        or any(credential.get(key) != login.get(key) for key in set(login) - {"sampled_at_utc", "role", "job_id"})):
        _fail("R7HA_PROBE_CREDENTIAL_BINDING_MISMATCH")
    if run is not None:
        _replay_credential(credential, run=run, role="probe", job_id=value["job_id"])
    return value


def adjudicate_auth_successor_probe(*, qacct_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    if os.path.lexists(PROBE_TERMINAL_PATH):
        return _probe_terminal()
    submission = _validate_submission("probe")
    job_id = submission["job_id"]
    account = _account()
    accounting._validate_qacct_tool()
    command = [str(original.QACCT_PATH), "-j", job_id, "-t", "17"]
    completed = qacct_runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env=dict(account["sealed_qsub_environment"]), timeout=60)
    if completed.returncode != 0:
        return {"status": "PENDING_R7HA_PROBE_ACCOUNTING", "probe_job_id": job_id}
    if completed.stderr or len(completed.stdout) > 4 * 1024 * 1024:
        _fail("R7HA_PROBE_ACCOUNTING_INVALID")
    records = accounting.parse_qacct_records(bytes(completed.stdout))
    if not records:
        return {"status": "PENDING_R7HA_PROBE_ACCOUNTING", "probe_job_id": job_id}
    if len(records) != 1:
        _fail("R7HA_PROBE_ACCOUNTING_AMBIGUOUS")
    record = dict(records[0])
    _validate_probe_accounting_record(record, job_id=job_id, account=account)
    result = _validate_probe_result()
    log = PROBE_SCHEDULER_ROOT / f"{_job_name('probe')}.o{job_id}.17"
    payload = original._read_private_bytes(log, code="R7HA_PROBE_LOG_INVALID")
    marker = f"R7HA_STATUS={PROBE_PASS}\n".encode("ascii")
    if payload.count(marker) != 1 or b"R7HA_STATUS=BLOCKED" in payload:
        _fail("R7HA_PROBE_LOG_INVALID")
    receipt = {**_common("probe_accounting", "PASS_R7HA_PROBE_TERMINAL_ACCOUNTING"),
        "job_id": job_id, "record": record, "qacct_argv_sha256": core.canonical_json_sha256({"argv": command}),
        "qacct_stdout_sha256": hashlib.sha256(bytes(completed.stdout)).hexdigest(), "query_count": 1,
        "probe_result_sha256": core.sha256_file(PROBE_RESULT_PATH), "scheduler_log_sha256": hashlib.sha256(payload).hexdigest()}
    _write(PROBE_ACCOUNTING_PATH, receipt)
    terminal = {**_common("probe_terminal", PROBE_PASS), "probe_job_id": job_id,
        "accounting_receipt_sha256": core.sha256_file(PROBE_ACCOUNTING_PATH),
        "probe_result_sha256": core.sha256_file(PROBE_RESULT_PATH),
        "credential_readiness_sha256": result["credential_readiness_sha256"], "failed": 0, "exit_status": 0}
    _write(PROBE_TERMINAL_PATH, terminal)
    return _probe_terminal()


def _validate_probe_accounting_record(record: Mapping[str, str], *, job_id: str, account: Mapping[str, Any]) -> None:
    try:
        valid = (record["jobnumber"] == job_id and record["taskid"] == "17" and record["jobname"] == _job_name("probe")
            and record["owner"] == account["expected_scheduler_username"] and accounting._parse_failed(record["failed"]) == 0
            and accounting._parse_nonnegative_integer(record["exit_status"]) == 0
            and accounting._parse_wall_seconds(record["ru_wallclock"]) <= 600
            and accounting._parse_qacct_time(record["qsub_time"]) <= accounting._parse_qacct_time(record["start_time"]) <= accounting._parse_qacct_time(record["end_time"]))
    except Exception as exc:
        raise AuthenticationSuccessorError("R7HA_PROBE_ACCOUNTING_INVALID") from exc
    if not valid:
        _fail("R7HA_PROBE_TERMINAL_FAILURE")


def _probe_terminal(run: sequential.FullRun | None = None) -> dict[str, Any]:
    value, _ = _read(PROBE_TERMINAL_PATH)
    _validate_common(value, "probe_terminal", PROBE_PASS, {"probe_job_id", "accounting_receipt_sha256", "probe_result_sha256", "credential_readiness_sha256", "failed", "exit_status"})
    result = _validate_probe_result(run)
    receipt, digest = _read(PROBE_ACCOUNTING_PATH)
    _validate_common(receipt, "probe_accounting", "PASS_R7HA_PROBE_TERMINAL_ACCOUNTING", {
        "job_id", "record", "qacct_argv_sha256", "qacct_stdout_sha256", "query_count", "probe_result_sha256", "scheduler_log_sha256"})
    _validate_probe_accounting_record(receipt["record"], job_id=result["job_id"], account=_account())
    log = PROBE_SCHEDULER_ROOT / f"{_job_name('probe')}.o{result['job_id']}.17"
    payload = original._read_private_bytes(log, code="R7HA_PROBE_LOG_INVALID")
    if (value["probe_job_id"] != result["job_id"] or value["failed"] != 0 or value["exit_status"] != 0
        or value["accounting_receipt_sha256"] != digest or value["probe_result_sha256"] != core.sha256_file(PROBE_RESULT_PATH)
        or value["credential_readiness_sha256"] != result["credential_readiness_sha256"]
        or receipt["probe_result_sha256"] != core.sha256_file(PROBE_RESULT_PATH)
        or receipt["job_id"] != result["job_id"] or receipt["query_count"] != 1
        or receipt["qacct_argv_sha256"] != core.canonical_json_sha256({"argv": [str(original.QACCT_PATH), "-j", result["job_id"], "-t", "17"]})
        or receipt["scheduler_log_sha256"] != hashlib.sha256(payload).hexdigest()
        or payload.count(f"R7HA_STATUS={PROBE_PASS}\n".encode("ascii")) != 1
        or b"R7HA_STATUS=BLOCKED" in payload):
        _fail("R7HA_PROBE_TERMINAL_INVALID")
    return value


def _job_name(role: str, commit: str | None = None) -> str:
    labels = {"probe": "ctx", "array": "seq", "finalizer": "fin"}
    if role not in labels:
        _fail("R7HA_WORKER_ROLE_INVALID")
    return f"lvef_c3_r8u_r7ha_{labels[role]}_{(commit or current_successor_commit())[:8]}"


def _references(*, environment: Mapping[str, str], expected: Mapping[str, str] | None = None,
                qstat_runner: Callable[..., Any] = subprocess.run,
                process_runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    command = [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]]
    completed = qstat_runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=dict(environment), timeout=15)
    payload = bytes(completed.stdout)
    if completed.returncode or completed.stderr or len(payload) > 4 * 1024 * 1024:
        _fail("R7HA_QSTAT_INVALID")
    allowed = {(job, _job_name(role)) for role, job in (expected or {}).items()}
    rows = scheduler._r8u_r7d_parse_qstat_xml(payload)
    for row in rows:
        if row.full_job_name.startswith(("lvef_c3_", "c3_")) and ((row.job_id, row.full_job_name) not in allowed or row.owner != environment["USER"]):
            _fail("R7HA_COMPETING_JOB")
    processes = historical._r8u_r5_process_projection(
        environment=environment, runner=process_runner, worker_self_marker=Path(sys.argv[0]).name,
        additional_markers=("lvef_c3_r8u_r7h_auth_successor.py",),
    )
    if processes.get("matching_processes") != 0:
        _fail("R7HA_COMPETING_PROCESS")
    return {"active_job_references": 0, "active_process_references": 0,
        "qstat_stdout_sha256": hashlib.sha256(payload).hexdigest(),
        "qstat_argv_sha256": core.canonical_json_sha256({"argv": command}),
        "process_projection": dict(processes), "expected_jobs": dict(expected or {})}


def _topology(references: Mapping[str, Any]) -> Mapping[str, Any]:
    return original.validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )


def _credential(run: sequential.FullRun, *, role: str, job_id: str | None = None, batch_id: str | None = None) -> dict[str, Any]:
    from lvef_c3_r7h_adc import validate_adc_access, validate_adc_receipt
    value = validate_adc_access(run, role=role, job_id=job_id, batch_id=batch_id)
    return validate_adc_receipt(value, run=run, role=role, job_id=job_id, batch_id=batch_id)


def _replay_credential(value: Mapping[str, Any], *, run: sequential.FullRun, role: str, job_id: str | None, batch_id: str | None = None) -> None:
    from lvef_c3_r7h_adc import validate_adc_receipt
    # A recorded probe remains historical evidence.  Fresh readiness is checked
    # again by the real provider immediately before each new DOWNLOAD.
    try:
        sampled = datetime.fromisoformat(str(value["sampled_at_utc"]))
    except Exception as exc:
        raise AuthenticationSuccessorError("ADC_READINESS_RECEIPT_INVALID") from exc
    validate_adc_receipt(value, run=run, role=role, job_id=job_id, batch_id=batch_id, now=sampled)


def capture_auth_successor_capacity(*, capacity_process_runner: Callable[..., Any] | None = None,
        qstat_runner: Callable[..., Any] = subprocess.run, process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    """Perform a fresh pquota/findmnt/df capture; never relabel the old receipt."""
    run = _load_run("R7HA_CAPACITY")
    consumed = load_consumed_authentication_failure(run=run)
    environment, _ = scheduler.build_qsub_environment()
    references = _references(environment=environment, qstat_runner=qstat_runner, process_runner=process_runner)
    topology = _topology(references)
    _directory(SUCCESSOR_ROOT)
    if os.path.lexists(CAPACITY_RECEIPT_PATH) or os.path.lexists(CAPACITY_PRODUCER_PATH):
        _fail("R7HA_CAPACITY_OBSERVATION_ALREADY_CONSUMED")
    _directory(CAPACITY_ROOT, fresh=True)
    consumed_value = {**_common("consumed_failure", "PASS_R7HA_SEALED_ZERO_PAYLOAD_AUTHENTICATION"), **consumed}
    _write(CONSUMED_RECEIPT_PATH, consumed_value)
    _write(ACCOUNT_PATH, _build_account(environment))
    projection = capacity.require_fixed_r8u_r7f_tasks17_19_plan_projection(run.plan)
    capacity.write_r8u_r7f_static_plan_projection_no_clobber(STATIC_PLAN_PATH, projection)
    old_evidence = _read_bound(original.CONSUMED_EVIDENCE_PATH, original.PREDECESSOR_CONSUMED_EVIDENCE_SHA256)
    baselines = original._capacity_baselines(old_evidence)
    producer = capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
        run.plan, r7f_runtime_commit=current_successor_commit(), receipt_path=CAPACITY_PRODUCER_PATH,
        raw_capture_root=CAPACITY_RAW_ROOT, process_runner=capacity_process_runner, **baselines,
    )
    original._raise_for_capacity_producer_status(producer)
    result = {**_common("capacity", "PASS_R7HA_FRESH_REMAINING_CAPACITY"),
        "producer_sha256": core.sha256_file(CAPACITY_PRODUCER_PATH),
        "consumed_failure_sha256": core.sha256_file(CONSUMED_RECEIPT_PATH),
        "baselines": baselines, "topology": dict(topology), "live_references": references,
        "capacity_projection": dict(producer["capacity_projection"]),
        "resource_observation_count": 1, "pquota_commands": 1, "findmnt_commands": 2, "df_commands": 2}
    _write(CAPACITY_RECEIPT_PATH, result)
    return result


def _capacity(run: sequential.FullRun) -> dict[str, Any]:
    value, _ = _read(CAPACITY_RECEIPT_PATH)
    _validate_common(value, "capacity", "PASS_R7HA_FRESH_REMAINING_CAPACITY", {
        "producer_sha256", "consumed_failure_sha256", "baselines", "topology", "live_references",
        "capacity_projection", "resource_observation_count", "pquota_commands", "findmnt_commands", "df_commands"})
    producer, _, digest = original._read_indented_private_json(CAPACITY_PRODUCER_PATH, code="R7HA_CAPACITY_INVALID")
    expected_baselines = original._capacity_baselines(_read_bound(original.CONSUMED_EVIDENCE_PATH, original.PREDECESSOR_CONSUMED_EVIDENCE_SHA256))
    if not _exact(value["baselines"], expected_baselines) or any(
        type(value.get(key)) is not int or value[key] != count for key, count in {
            "resource_observation_count": 1, "pquota_commands": 1, "findmnt_commands": 2, "df_commands": 2}.items()):
        _fail("R7HA_CAPACITY_BASELINE_MISMATCH")
    validated = capacity.validate_fixed_r8u_r7f_tasks17_19_capacity(run.plan, producer,
        r7f_runtime_commit=current_successor_commit(), raw_capture_root=CAPACITY_RAW_ROOT, **value["baselines"])
    original._raise_for_capacity_producer_status(validated)
    if digest != value["producer_sha256"] or not _exact(value["capacity_projection"], validated["capacity_projection"]) or value["consumed_failure_sha256"] != core.sha256_file(CONSUMED_RECEIPT_PATH):
        _fail("R7HA_CAPACITY_BINDING_MISMATCH")
    return value


def _claim_payload(*, references: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]:
    return {**_common("continuation_claim", "AUTHORIZED_R7HA_EXCLUSIVE_TASKS_17_19"),
        "consumed_failure_sha256": core.sha256_file(CONSUMED_RECEIPT_PATH),
        "consumed_finalizer_authority_sha256": CONSUMED_CONTROL_SHA256["finalizer_authority"],
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "probe_terminal_sha256": core.sha256_file(PROBE_TERMINAL_PATH),
        "scheduler_account_sha256": core.sha256_file(ACCOUNT_PATH),
        "remaining_tasks": [17, 18, 19], "remaining_studies": 530,
        "remaining_source_objects": 39607, "remaining_source_bytes": 145202986626,
        "prefix_final_receipt_sha256": list(original.PREFIX_FINAL_RECEIPT_SHA256),
        "array_max_concurrency": 1, "new_scientific_qsubs_maximum": 2,
        "original_terminal_state_mutated": False, "original_controls_reused_as_live": False,
        "credential_check": dict(credential), "live_references": dict(references),
        "script_authority": _script_authority()}


def _download_authority_payload() -> dict[str, Any]:
    return core.authentication_successor_download_receipt(_make_download_authority(""))


def _download_authority() -> core.AuthenticationSuccessorDownloadAuthority:
    value, digest = _read(DOWNLOAD_AUTHORITY_PATH)
    if not _exact(value, _download_authority_payload()):
        _fail("R7HA_DOWNLOAD_AUTHORITY_MISMATCH")
    return _make_download_authority(digest)


def _make_download_authority(digest: str) -> core.AuthenticationSuccessorDownloadAuthority:
    probe = _probe_terminal()
    array = _validate_submission("array")
    return core.AuthenticationSuccessorDownloadAuthority(
        execution_id=EXECUTION_ID, implementation_commit=current_successor_commit(),
        attempt_id=ATTEMPT_ID, plan_sha256=PLAN_SHA256, authority_receipt_sha256=digest,
        consumed_terminal_journal_sha256=TERMINAL_JOURNAL_SHA256,
        consumed_reconciliation_sha256=RECONCILIATION_SHA256,
        consumed_array_submission_sha256=CONSUMED_CONTROL_SHA256["array_submission"],
        consumed_finalizer_submission_sha256=CONSUMED_CONTROL_SHA256["finalizer_submission"],
        credential_readiness_sha256=probe["credential_readiness_sha256"],
        successor_claim_sha256=core.sha256_file(CLAIM_PATH), successor_array_job_id=array["job_id"])


def _combined_payload(*, references: Mapping[str, Any]) -> dict[str, Any]:
    array = _validate_submission("array")
    fin = _validate_submission("finalizer")
    if array["job_id"] == fin["job_id"]:
        _fail("R7HA_SUBMISSION_JOB_SUBSTITUTION")
    return {**_common("submission", SUBMISSION_PASS),
        "array_job_id": array["job_id"], "finalizer_job_id": fin["job_id"],
        "array_submission_sha256": core.sha256_file(ARRAY_SUBMISSION_PATH),
        "finalizer_submission_sha256": core.sha256_file(FINALIZER_SUBMISSION_PATH),
        "download_authority_sha256": core.sha256_file(DOWNLOAD_AUTHORITY_PATH),
        "claim_sha256": core.sha256_file(CLAIM_PATH), "probe_terminal_sha256": core.sha256_file(PROBE_TERMINAL_PATH),
        "task_range": "17-19", "array_max_concurrency": 1,
        "finalizer_held_on_array": True, "new_qsub_count": 2,
        "live_references": dict(references)}


def _validate_continuation(run: sequential.FullRun | None = None) -> dict[str, Any]:
    claim, _ = _read(CLAIM_PATH)
    if not _exact(claim, _claim_payload(references=claim["live_references"], credential=claim["credential_check"])):
        _fail("R7HA_CLAIM_BINDING_MISMATCH")
    if run is not None:
        _replay_credential(claim["credential_check"], run=run, role="login", job_id=None)
        _probe_terminal(run)
    combined, _ = _read(SUBMISSION_PATH)
    if not _exact(combined, _combined_payload(references=combined["live_references"])):
        _fail("R7HA_CONTINUATION_BINDING_MISMATCH")
    _download_authority()
    return combined


def submit_auth_successor(*, qsub_runner: Callable[..., Any] = subprocess.run,
        qstat_runner: Callable[..., Any] = subprocess.run, process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    """One exclusive fresh array and its exact dependent CPU finalizer."""
    scheduler.validate_scheduler_tools()
    run = _load_run("R7HA_SUBMITTER")
    load_consumed_authentication_failure(run=run)
    _capacity(run)
    _probe_terminal(run)
    account = _account()
    environment, _ = scheduler.build_qsub_environment()
    if scheduler.qsub_environment_sha256(environment) != account["qsub_environment_sha256"]:
        _fail("R7HA_QSUB_ENVIRONMENT_MISMATCH")
    credential = _credential(run, role="login")
    references = _references(environment=environment, qstat_runner=qstat_runner, process_runner=process_runner)
    _topology(references)
    if any(os.path.lexists(path) for path in (CLAIM_PATH, SCHEDULER_ROOT, DOWNLOAD_AUTHORITY_PATH, WORKER_ROOT, FINALIZER_AUTHORITY_PATH, FINALIZER_BINDING_PATH)):
        _fail("R7HA_SUBMISSION_ALREADY_CLAIMED")
    claim_sha = _write(CLAIM_PATH, _claim_payload(references=references, credential=credential))
    _directory(SCHEDULER_ROOT, fresh=True)
    # An uncertain qsub result leaves the immutable claim and raw capture in
    # place.  This entry point never retries it or issues a replacement array.
    array_job = scheduler._capture_qsub("array", _qsub_command("array"), root=SCHEDULER_ROOT,
        environment=environment, runner=qsub_runner, parser=original._parse_array_qsub_stdout)
    _write(ARRAY_SUBMISSION_PATH, _submission("array", array_job, authority_sha=claim_sha))
    _validate_submission("array")
    finalizer_job = scheduler._capture_qsub("finalizer", _qsub_command("finalizer", array_job_id=array_job),
        root=SCHEDULER_ROOT, environment=environment, runner=qsub_runner)
    _write(FINALIZER_SUBMISSION_PATH, _submission("finalizer", finalizer_job, authority_sha=claim_sha, array_job_id=array_job))
    _validate_submission("finalizer")
    _write(DOWNLOAD_AUTHORITY_PATH, _download_authority_payload())
    references = _references(environment=environment, expected={"array": array_job, "finalizer": finalizer_job},
        qstat_runner=qstat_runner, process_runner=process_runner)
    _write(SUBMISSION_PATH, _combined_payload(references=references))
    result = _validate_continuation()
    return {"status": SUBMISSION_PASS, "array_job_id": result["array_job_id"], "finalizer_job_id": result["finalizer_job_id"],
        "held_on_array_job_id": result["array_job_id"], "task_range": "17-19", "array_max_concurrency": 1,
        "submission_receipt_sha256": core.sha256_file(SUBMISSION_PATH)}


def pre_download_credential_check(*, run: sequential.FullRun, batch_id: str) -> Mapping[str, Any]:
    continuation = _validate_continuation(run)
    task = str(int(batch_id[-3:]) + 1)
    job_id = str(os.environ.get("JOB_ID", ""))
    if task not in {"17", "18", "19"} or job_id != continuation["array_job_id"] or os.environ.get("SGE_TASK_ID") != task:
        _fail("R7HA_PREDOWNLOAD_WORKER_MISMATCH")
    # Network calls occur now, after the queue delay, before provider/transport
    # construction.  The probe receipt is not substituted for this check.
    result = _credential(run, role="array", job_id=job_id, batch_id=batch_id)
    path = WORKER_ROOT / f"array_task_{task}.credential.restricted.json"
    value = {**_common("worker_credential", "PASS_R7HA_IMMEDIATE_PREDOWNLOAD_CREDENTIAL"),
        "job_id": job_id, "task_id": int(task), "batch_id": batch_id,
        "claim_sha256": core.sha256_file(CLAIM_PATH), "download_authority_sha256": core.sha256_file(DOWNLOAD_AUTHORITY_PATH),
        "credential_check": result}
    _write(path, value)
    return result


def run_auth_successor_array_task(*, qstat_runner: Callable[..., Any] = subprocess.run,
        process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    validate_successor_worker_submission(current_job_id=job_id, role="array", qstat_runner=qstat_runner)
    run = _load_run(job_id)
    load_consumed_authentication_failure(require_pristine_tail=False, run=run)
    _capacity(run)
    continuation = _validate_continuation(run)
    account = _account()
    references = _references(environment=account["sealed_qsub_environment"],
        expected={"array": continuation["array_job_id"], "finalizer": continuation["finalizer_job_id"]},
        qstat_runner=qstat_runner, process_runner=process_runner)
    _topology(references)
    run = replace(run, authentication_successor_download_authority=_download_authority())
    dependency = sequential.FullDependencies(
        execution_context=sequential.R8U_R7H_FIXED_CONTINUATION,
        r8u_r7h_worker_submission_validator=validate_successor_worker_submission,
        r8u_r7h_sealed_history_validator=original.load_r8u_r7h_sealed_history,
        r8u_r7h_active_job_references=references["active_job_references"],
        r8u_r7h_active_process_references=references["active_process_references"],
        pre_download_validator=pre_download_credential_check)
    try:
        result = sequential.run_batch_task(task_id=int(os.environ["SGE_TASK_ID"]), run=run, dependencies=dependency)
    except Exception as exc:
        code = str(getattr(exc, "code", "R7HA_BATCH_EXECUTION_FAILED"))
        raise AuthenticationSuccessorError(code, stage=getattr(exc, "stage", None)) from exc
    if result.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R7HA_BATCH_NOT_FINALIZED")
    return {"status": "PASS_R7HA_BATCH_FINALIZED", "task_id": int(os.environ["SGE_TASK_ID"]), "job_id": job_id}


def _finalizer_authority() -> finalizer.R8UR7HImplementationAuthority:
    evidence = _read_bound(original.CONSUMED_EVIDENCE_PATH, original.PREDECESSOR_CONSUMED_EVIDENCE_SHA256)
    authority = original._r7h_finalizer_authority(implementation_commit=current_successor_commit(), evidence=evidence)
    return replace(authority,
        capacity_receipt_sha256=core.sha256_file(CAPACITY_RECEIPT_PATH),
        scheduler_account_authority_sha256=core.sha256_file(ACCOUNT_PATH),
        probe_terminal_receipt_sha256=core.sha256_file(PROBE_TERMINAL_PATH),
        continuation_claim_sha256=core.sha256_file(CLAIM_PATH),
        array_submission_receipt_sha256=core.sha256_file(ARRAY_SUBMISSION_PATH),
        finalizer_submission_receipt_sha256=core.sha256_file(FINALIZER_SUBMISSION_PATH),
        continuation_submission_receipt_sha256=core.sha256_file(SUBMISSION_PATH))


def _finalizer_binding_payload(run: sequential.FullRun | None = None) -> dict[str, Any]:
    continuation = _validate_continuation(run)
    return {**_common("finalizer_binding", "AUTHORIZED_R7HA_COHORT_FINALIZER"),
        "consumed_finalizer_authority_sha256": CONSUMED_CONTROL_SHA256["finalizer_authority"],
        "finalizer_authority_sha256": core.sha256_file(FINALIZER_AUTHORITY_PATH),
        "download_authority_sha256": core.sha256_file(DOWNLOAD_AUTHORITY_PATH),
        "submission_receipt_sha256": core.sha256_file(SUBMISSION_PATH),
        "worker_receipt_sha256": core.sha256_file(_worker_path("finalizer", None)),
        "finalizer_job_id": continuation["finalizer_job_id"], "array_job_id": continuation["array_job_id"],
        "prefix_final_receipt_sha256": list(original.PREFIX_FINAL_RECEIPT_SHA256),
        "scientific_implementation_epoch_count": 4}


def validate_finalizer_successor_binding(authority: finalizer.R8UR7HImplementationAuthority, *, binding_path: Path,
        receipts: Any = None) -> None:
    """Explicit opt-in for the new finalizer; never reinterpret old authority."""
    if binding_path != FINALIZER_BINDING_PATH or type(authority) is not finalizer.R8UR7HImplementationAuthority:
        _fail("R7HA_FINALIZER_BINDING_PATH_INVALID")
    current_successor_commit()
    run = _load_run("R7HA_FINALIZER_REPLAY")
    load_consumed_authentication_failure(require_pristine_tail=False, run=run)
    value, _ = _read(binding_path)
    if not _exact(value, _finalizer_binding_payload(run)) or authority != _finalizer_authority():
        _fail("R7HA_FINALIZER_SUCCESSOR_BINDING_MISMATCH")
    authority_value, _ = _read(FINALIZER_AUTHORITY_PATH)
    if not _exact(authority_value, original._finalizer_authority_payload(authority)):
        _fail("R7HA_FINALIZER_SUCCESSOR_BINDING_MISMATCH")
    _validate_recorded_worker("finalizer", None)
    if receipts is not None:
        if len(receipts) != 19 or any(
            row.get("scheduler_job_identity") != value["array_job_id"]
            or row.get("batch_id") != f"c3_batch_{index:03d}"
            for index, row in enumerate(receipts[16:], start=16)):
            _fail("R7HA_TAIL_RECEIPT_JOB_SUBSTITUTION")
        for task in ("17", "18", "19"):
            _validate_recorded_worker("array", task)


def run_auth_successor_finalizer(*, qstat_runner: Callable[..., Any] = subprocess.run,
        process_runner: Callable[..., Any] = subprocess.run) -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    validate_successor_worker_submission(current_job_id=job_id, role="finalizer", qstat_runner=qstat_runner)
    run = _load_run(job_id)
    load_consumed_authentication_failure(require_pristine_tail=False, run=run)
    continuation = _validate_continuation(run)
    account = _account()
    references = _references(environment=account["sealed_qsub_environment"],
        expected={"array": continuation["array_job_id"], "finalizer": continuation["finalizer_job_id"]},
        qstat_runner=qstat_runner, process_runner=process_runner)
    _topology(references)
    paths = [sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"] for index in range(19)]
    # Fail before authority publication if a downstream consequence recurs.
    for index, path in enumerate(paths):
        value = finalizer.load_json(path, "R7HA_BATCH_RECEIPT")
        finalizer._validate_current_receipt_v3(value)
        if index < 16 and core.sha256_file(path) != original.PREFIX_FINAL_RECEIPT_SHA256[index]:
            _fail("R7HA_PREFIX_CHANGED")
    output_root = FINALIZER_AGGREGATE_PATH.parent
    if os.path.lexists(output_root):
        original._validate_private_directory(output_root, code="R7HA_COHORT_OUTPUT_INVALID")
        if any(output_root.iterdir()):
            _fail("R7HA_COHORT_OUTPUT_ALREADY_EXISTS")
    else:
        _directory(output_root)
    authority = _finalizer_authority()
    _write(FINALIZER_AUTHORITY_PATH, original._finalizer_authority_payload(authority))
    _write(FINALIZER_BINDING_PATH, _finalizer_binding_payload(run))
    summary = finalizer.finalize_receipts(paths, expected_governing_commit=SCIENTIFIC_COMMIT,
        expected_attempt_id=ATTEMPT_ID, plan=run.plan, requirements=run.requirements,
        production_root=PRODUCTION_ROOT, contract=run.contract, contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=ATTEMPT_ROOT / "cache_retirement_authorizations",
        canonical_output_root=output_root, expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=5, r8u_r7h_implementation_authority=authority,
        r8u_r7h_auth_successor_binding=FINALIZER_BINDING_PATH)
    finalizer.validate_closed_final_summary(summary)
    expected = {"status": "PASS_PRODUCTION_C3_FINALIZED", "production_batches": 19,
        "selected_studies": 4530, "selected_subjects": 4530, "verified_source_objects": 335984,
        "selected_source_bytes": 1216569133322, "pooled_imaging_eligible_studies": 4525,
        "no_cine_studies": 5, "new_no_cine_studies": 0, "implementation_authority_epoch_count": 4,
        "r8u_r7h_implementation_commit": current_successor_commit(),
        "r8u_r7h_continuation_authority_sha256": core.sha256_file(FINALIZER_AUTHORITY_PATH),
        "model_fitting_count": 0, "endpoint_prediction_count": 0, "confirmatory_performance_access_count": 0}
    if any(not _exact(summary.get(key), value) for key, value in expected.items()):
        _fail("R7HA_COHORT_FINALIZATION_INVALID")
    finalizer.write_json_atomic(FINALIZER_AGGREGATE_PATH, summary)
    reopened, _, _ = original._read_indented_private_json(FINALIZER_AGGREGATE_PATH, code="R7HA_AGGREGATE_READBACK_INVALID")
    if not _exact(reopened, summary):
        _fail("R7HA_AGGREGATE_READBACK_INVALID")
    return {"status": "PASS_PRODUCTION_C3_FINALIZED", "production_batches": 19, "selected_studies": 4530}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capacity", "submit-probe", "probe", "adjudicate-probe", "submit", "array", "finalizer"))
    args = parser.parse_args(argv)
    functions = {"capacity": capture_auth_successor_capacity, "submit-probe": submit_auth_successor_probe,
        "probe": run_auth_successor_probe, "adjudicate-probe": adjudicate_auth_successor_probe,
        "submit": submit_auth_successor, "array": run_auth_successor_array_task, "finalizer": run_auth_successor_finalizer}
    try:
        result = functions[args.command]()
        allowed = {"status", "probe_job_id", "array_job_id", "finalizer_job_id", "held_on_array_job_id",
            "task_range", "array_max_concurrency", "task_id", "job_id", "credential_status", "production_batches", "selected_studies"}
        safe = {key: value for key, value in result.items() if key in allowed}
        print(json.dumps(safe, sort_keys=True))
        print(f"R7HA_STATUS={result['status']}")
        return 0
    except Exception as exc:
        code = str(getattr(exc, "code", "R7HA_UNEXPECTED_CONTROL_FAILURE"))
        if SAFE_CODE.fullmatch(code) is None:
            code = "R7HA_UNEXPECTED_CONTROL_FAILURE"
        safe = {"status": "BLOCKED", "error_code": code, "exception_message_emitted": False}
        stage = getattr(exc, "stage", None)
        if isinstance(stage, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", stage):
            safe["failed_stage"] = stage
        print(json.dumps(safe, sort_keys=True))
        print("R7HA_STATUS=BLOCKED")
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
