#!/usr/bin/env python3
"""Fixed R8U-R7H final-tail continuation controller.

This module is intentionally additive.  It reopens the consumed R7F/R7G
evidence, admits exactly original Tasks 17--19, and writes only the fixed R7H
control namespace plus the canonical R7G accounting receipts that the owner
authorized.  It is not a general resume or retry interface.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Final, Mapping, NoReturn, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import finalize_lvef_c3_production as finalizer
import lvef_c3_full_scheduler as scheduler
import lvef_c3_full_sequential as sequential
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import lvef_c3_r8u_r7d_capacity as capacity
import lvef_c3_r8u_r7g_accounting as accounting
import lvef_c3_r8u_r7g_evidence as r7g_evidence
import lvef_c3_r8u_r7g_terminal_logs as terminal_logs


def _recovery_controller_module() -> Any:
    """Reuse the live script module when the recovery CLI imports R7H."""

    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if main_file is not None and Path(main_file).resolve() == (
        SCRIPT_ROOT / "lvef_c3_r8r_recovery_continuation.py"
    ):
        return main
    return importlib.import_module("lvef_c3_r8r_recovery_continuation")


historical = _recovery_controller_module()


SCIENTIFIC_COMMIT: Final = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
R7F_RUNTIME_COMMIT: Final = "2223d9768a1cc23efbe95a3c5474ea747a383a10"
R7G_ADJUDICATION_COMMIT: Final = (
    "cf83c19521a2ed7b722c29a44c01f00cad0cf717"
)
R7H_CORRECTION_BASE_COMMIT: Final = (
    "8230d1535247256529616cb481dd48cce1f9a78f"
)
R7H_TOPOLOGY_CORRECTION_BASE_COMMIT: Final = (
    "95b105841fd1af69e3d29f3e1b4640de15ab25df"
)
PREDECESSOR_CONSUMED_EVIDENCE_SHA256: Final = (
    "40fb2c77f2eaebb67bbc34e085c23bd9ad83351593dcb67978e3a2da2ca7741d"
)
R7G_TERMINAL_AUTHORITY_SHA256: Final = (
    "3764a284f3b8ba2a5bf3e9abbc9708a6dc2c9cecfa8e2bbef05a2b507252610e"
)
CONSUMED_TASK17_ACCOUNTING_SHA256: Final = (
    "454c8204ff7b6fc6d23dac81efd56ea4464121634eb85ba5557790a6195fde7d"
)
CONSUMED_R7F_AUTHORITY_SHA256: Final = {
    "capacity": "4163c6faf46073ce79cd5dd6999407ec583d72663904b1bbda5c7bf20d45964d",
    "continuation_claim": "3eeb09049871ea79a48f7cd7015130909492ddf5e339f9fc9cb1f432204a9f14",
    "array_submission": "a2346272e02edc2584017361bf5404186a7a3eca1b51e25c704430bea95303a4",
    "finalizer_submission": "14c1d3913969aba5893e32de4524e525f891e43523a323b80f47313cb77214a5",
    "combined_submission": "4f6b1156e1580e175ed605c4a5002d6d180747a8bc82f0c1e90ebe3b80cbc306",
}
CONSUMED_ARRAY_JOB_ID: Final = "7480830"
CONSUMED_FINALIZER_JOB_ID: Final = "7480831"
TASK_IDS: Final = (17, 18, 19)
TASK_RANGE: Final = "17-19"
MAX_CONCURRENCY: Final = 1
PREFIX_FINAL_RECEIPT_SHA256: Final = (
    historical.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256
)
BATCH16_FINAL_RECEIPT_SHA256: Final = (
    historical.R8U_R7D_BATCH16_FINAL_RECEIPT_SHA256
)
EXPECTED_REMAINING_STUDIES: Final = 530
EXPECTED_REMAINING_OBJECTS: Final = 39_607
EXPECTED_REMAINING_SOURCE_BYTES: Final = 145_202_986_626
EXPECTED_PARTIAL_FILES: Final = 4_757
EXPECTED_PARTIAL_DIRECTORIES: Final = 259
EXPECTED_PARTIAL_BYTES: Final = 8_583_119_701
EXPECTED_PARTIAL_METADATA_SHA256: Final = (
    "1dcc53e52a468773128348225943125c926bcab942ac7c69c37344684249f83e"
)

PRODUCTION_ROOT: Final = sequential.PRODUCTION_ROOT
ATTEMPT_ROOT: Final = PRODUCTION_ROOT / "attempts" / ATTEMPT_ID
R7H_ROOT: Final = ATTEMPT_ROOT / "r8u_r7h_continuation_17_19"
CONSUMED_EVIDENCE_PATH: Final = (
    R7H_ROOT / "consumed_r7f_evidence.restricted.json"
)
TOPOLOGY_AUTHORITY_PATH: Final = (
    R7H_ROOT / "topology_authority.restricted.json"
)
CAPACITY_ROOT: Final = R7H_ROOT / "capacity"
STATIC_PLAN_PROJECTION_PATH: Final = (
    CAPACITY_ROOT / "static_plan_projection.restricted.json"
)
CAPACITY_PRODUCER_PATH: Final = (
    CAPACITY_ROOT / "r7f_producer_capacity.restricted.json"
)
CAPACITY_RECEIPT_PATH: Final = CAPACITY_ROOT / "capacity.restricted.json"
CAPACITY_RAW_CAPTURE_ROOT: Final = CAPACITY_ROOT / "raw_captures"
SCHEDULER_ACCOUNT_PATH: Final = (
    R7H_ROOT / "scheduler_account_authority.restricted.json"
)
PROBE_ROOT: Final = R7H_ROOT / "topology_probe"
PROBE_SCHEDULER_ROOT: Final = PROBE_ROOT / "scheduler"
PROBE_AUTHORITY_PATH: Final = PROBE_ROOT / "probe_authority.restricted.json"
PROBE_SUBMISSION_PATH: Final = (
    PROBE_ROOT / "submission_receipt.restricted.json"
)
PROBE_WORKER_RECEIPT_PATH: Final = (
    PROBE_ROOT / "worker_context_receipt.restricted.json"
)
PROBE_TOPOLOGY_PATH: Final = (
    PROBE_ROOT / "topology_diagnostic.restricted.json"
)
PROBE_ACCOUNTING_PATH: Final = (
    PROBE_ROOT / "accounting_receipt.restricted.json"
)
PROBE_TERMINAL_PATH: Final = PROBE_ROOT / "terminal_receipt.restricted.json"
CONTINUATION_CLAIM_PATH: Final = (
    R7H_ROOT / "continuation_claim.restricted.json"
)
CONTINUATION_SCHEDULER_ROOT: Final = R7H_ROOT / "scheduler"
ARRAY_SUBMISSION_PATH: Final = (
    CONTINUATION_SCHEDULER_ROOT / "array_submission_receipt.restricted.json"
)
FINALIZER_SUBMISSION_PATH: Final = (
    CONTINUATION_SCHEDULER_ROOT
    / "finalizer_submission_receipt.restricted.json"
)
CONTINUATION_SUBMISSION_PATH: Final = (
    CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
WORKER_CONTEXT_ROOT: Final = R7H_ROOT / "worker_context"
ARRAY_WORKER_RECEIPT_TEMPLATE: Final = (
    "array_task_{task_id}.worker_context.restricted.json"
)
FINALIZER_WORKER_RECEIPT_PATH: Final = (
    WORKER_CONTEXT_ROOT / "finalizer.worker_context.restricted.json"
)
FINALIZER_AUTHORITY_PATH: Final = (
    R7H_ROOT / "finalizer_authority.restricted.json"
)
FINALIZER_AGGREGATE_PATH: Final = (
    ATTEMPT_ROOT
    / "cohort_finalization"
    / "full_c3_finalization.aggregate_safe.json"
)
PRE_ARRAY_COLLISION_PATHS: Final = (
    CONTINUATION_CLAIM_PATH,
    CONTINUATION_SCHEDULER_ROOT,
    WORKER_CONTEXT_ROOT,
    FINALIZER_AUTHORITY_PATH,
    FINALIZER_AGGREGATE_PATH,
)
PRE_PROBE_COLLISION_PATHS: Final = (
    PROBE_ROOT,
    *PRE_ARRAY_COLLISION_PATHS,
)
RUNNER_PATH: Final = historical.RUNNER_PATH
FAILED_PARTIAL_SEAL_PATH: Final = r7g_evidence.FAILED_PARTIAL_SEAL_PATH
FAILED_PARTIAL_ROOT: Final = r7g_evidence.FAILED_PARTIAL_ROOT
QACCT_PATH: Final = accounting.QACCT_PATH

PROBE_ROLE: Final = "R8U_R7H_CONTINUATION_TOPOLOGY_PROBE"
ARRAY_ROLE: Final = "R8U_R7H_CONTINUATION_ARRAY"
FINALIZER_ROLE: Final = "R8U_R7H_COHORT_FINALIZER"
WORKER_ROLES: Final = (PROBE_ROLE, ARRAY_ROLE, FINALIZER_ROLE)

TOPOLOGY_PASS: Final = (
    "PASS_R7H_SEALED_SAME_ATTEMPT_PARTIAL_EXCLUDED_FROM_ACTIVE_TOPOLOGY"
)
CAPACITY_PASS: Final = "PASS_R7H_TASKS_17_19_REMAINING_CAPACITY"
PROBE_PASS: Final = "PASS_R7H_CONTINUATION_TOPOLOGY_PROBE"
SUBMISSION_PASS: Final = "FINAL_TASKS_17_19_AND_FINALIZER_RESUBMITTED"
FINALIZER_PASS: Final = "PASS_R8U_R7H_FIXED_CONTINUATION_FINALIZED"

COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
JOB_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_CODE_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,159}$")
BATCH_RE: Final = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
MAX_CONTROL_BYTES: Final = 128 * 1024 * 1024
MAX_TOPOLOGY_ENTRIES: Final = 200_000


class R7HContinuationError(historical.R8RControllerError):
    """One fail-closed, aggregate-safe R7H controller error."""

    def __init__(
        self,
        code: str,
        *,
        capacity_deficits: Mapping[str, int] | None = None,
        probe_qacct: Mapping[str, int] | None = None,
    ) -> None:
        self.code = code
        self.capacity_deficits = dict(capacity_deficits or {})
        self.probe_qacct = dict(probe_qacct or {})
        super().__init__(
            code,
            capacity_deficits=self.capacity_deficits,
            probe_qacct=self.probe_qacct,
        )


@dataclass(frozen=True)
class R7HTopologyProjection:
    status: str
    cache_bearing_attempt_roots: int
    current_attempt_cache_bearing_roots: int
    canonical_clips_roots: int
    partial_roots: int
    sealed_cross_attempt_terminal_failed_caches: int
    sealed_current_attempt_batch16_failed_partials: int
    other_active_scientific_caches: int
    unknown_or_unsealed_caches: int
    finalized_unretired_extraction_caches: int
    symlink_count: int
    nonregular_count: int
    owner_mode_anomalies: int
    active_job_references: int
    active_process_references: int
    batch16_partial_files: int
    batch16_partial_directories: int
    batch16_partial_bytes: int
    batch16_partial_metadata_sha256: str
    batch16_partial_seal_sha256: str
    batch16_partial_adopted: bool
    batch16_partial_modified: bool
    npz_body_reads: int
    paths_emitted: bool
    identifiers_emitted: bool


def _fail(code: str) -> NoReturn:
    if SAFE_CODE_RE.fullmatch(code) is None:
        code = "R7H_INTERNAL_ERROR_CODE_INVALID"
    raise R7HContinuationError(code)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return core.canonical_json_bytes(value)


def _exact(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(right, dict):
        return set(left) == set(right) and all(
            _exact(left[key], right[key]) for key in right
        )
    if isinstance(right, list):
        return len(left) == len(right) and all(
            _exact(one, two)
            for one, two in zip(left, right, strict=True)
        )
    return left == right


def _strict_json(payload: bytes, *, code: str) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject(_value: str) -> Any:
        _fail(code)

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R7HContinuationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7HContinuationError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _read_private_bytes(path: Path, *, code: str) -> bytes:
    descriptor = -1
    try:
        sequential._require_nonsymlink_components(path.parent)
        visible = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(visible.st_mode)
            or stat.S_ISLNK(visible.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size < 1
            or opened.st_size > MAX_CONTROL_BYTES
        ):
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
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            _fail(code)
        return b"".join(blocks)
    except R7HContinuationError:
        raise
    except OSError as exc:
        raise R7HContinuationError(code) from exc
    except Exception as exc:
        raise R7HContinuationError(code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_json(path: Path, *, code: str) -> tuple[dict[str, Any], bytes, str]:
    payload = _read_private_bytes(path, code=code)
    value = _strict_json(payload, code=code)
    if payload != _canonical(value):
        _fail(code)
    return value, payload, _sha256(payload)


def _read_indented_private_json(
    path: Path, *, code: str
) -> tuple[dict[str, Any], bytes, str]:
    """Read an exact ``indent=2, sort_keys=True`` producer artifact."""

    payload = _read_private_bytes(path, code=code)
    value = _strict_json(payload, code=code)
    expected = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if payload != expected:
        _fail(code)
    return value, payload, _sha256(payload)


def _write_private_json(path: Path, value: Mapping[str, Any], *, code: str) -> str:
    try:
        sequential._require_nonsymlink_components(path.parent)
        _validate_private_directory(path.parent, code=code)
        digest = core.atomic_write_json_no_clobber(
            path, value, attempt_id=ATTEMPT_ID
        )
    except Exception as exc:
        raise R7HContinuationError(code) from exc
    reopened, _payload, reopened_sha = _read_private_json(path, code=code)
    if digest != reopened_sha or not _exact(reopened, dict(value)):
        _fail(code)
    return digest


def _validate_private_directory(path: Path, *, code: str) -> None:
    try:
        sequential._require_nonsymlink_components(path.parent)
        info = os.lstat(path)
    except Exception as exc:
        raise R7HContinuationError(code) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) not in {0o700, 0o2700}
    ):
        _fail(code)


def _ensure_private_directory(path: Path, *, fresh: bool = False) -> None:
    if os.path.lexists(path):
        if fresh:
            _fail("R7H_EXECUTION_OUTPUT_COLLISION")
        _validate_private_directory(path, code="R7H_PRIVATE_DIRECTORY_INVALID")
        return
    _validate_private_directory(path.parent, code="R7H_PRIVATE_PARENT_INVALID")
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise R7HContinuationError("R7H_PRIVATE_DIRECTORY_INVALID") from exc
    _validate_private_directory(path, code="R7H_PRIVATE_DIRECTORY_INVALID")


def _common(
    *, artifact_type: str, status: str, implementation_commit: str,
) -> dict[str, Any]:
    if (
        not isinstance(artifact_type, str)
        or not isinstance(status, str)
        or SAFE_CODE_RE.fullmatch(status) is None
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in {
            SCIENTIFIC_COMMIT,
            R7F_RUNTIME_COMMIT,
            R7G_ADJUDICATION_COMMIT,
            R7H_CORRECTION_BASE_COMMIT,
            R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        }
    ):
        _fail("R7H_CONTROL_SCHEMA_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": status,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "r7f_consumed_runtime_commit": R7F_RUNTIME_COMMIT,
        "r7g_adjudication_commit": R7G_ADJUDICATION_COMMIT,
        "r7h_runtime_commit": implementation_commit,
        "attempt_id": ATTEMPT_ID,
        "batch_plan_sha256": PLAN_SHA256,
    }


def _current_r8u_r7h_implementation_commit() -> str:
    """Require the fixed topology correction and every concrete parent link."""

    try:
        current = sequential._current_commit()
        parent = sequential._git("rev-list", "--parents", "-n", "1", current)
        base_parent = sequential._git(
            "rev-list", "--parents", "-n", "1",
            R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        )
        runtime_base_parent = sequential._git(
            "rev-list", "--parents", "-n", "1", R7H_CORRECTION_BASE_COMMIT
        )
        distance = sequential._git(
            "rev-list", "--count",
            f"{R7H_TOPOLOGY_CORRECTION_BASE_COMMIT}..{current}",
        )
        relation = sequential._git(
            "merge-base", "--is-ancestor", SCIENTIFIC_COMMIT, current
        )
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_IMPLEMENTATION_GIT_AUTHORITY_INVALID"
        ) from exc
    if COMMIT_RE.fullmatch(current) is None:
        _fail("R7H_HEAD_MISMATCH")
    if (
        current in {
            SCIENTIFIC_COMMIT,
            R7F_RUNTIME_COMMIT,
            R7G_ADJUDICATION_COMMIT,
            R7H_CORRECTION_BASE_COMMIT,
            R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        }
        or parent != f"{current} {R7H_TOPOLOGY_CORRECTION_BASE_COMMIT}"
        or base_parent
        != f"{R7H_TOPOLOGY_CORRECTION_BASE_COMMIT} {R7H_CORRECTION_BASE_COMMIT}"
        or runtime_base_parent
        != f"{R7H_CORRECTION_BASE_COMMIT} {R7G_ADJUDICATION_COMMIT}"
    ):
        _fail("R7H_PARENT_MISMATCH")
    if distance != "1" or relation:
        _fail("R7H_ANCESTRY_DISTANCE")
    return current


def _runtime_authority_failure_code(error: BaseException) -> str:
    """Project known runtime failures to short, non-sensitive field codes."""

    fields = {
        "python_executable_sha256": "R7H_PYTHON_HASH",
        "python_version": "R7H_PYTHON_VERSION",
        "torch_version": "R7H_TORCH_VERSION",
        "torchvision_version": "R7H_TORCHVISION_VERSION",
        "cuda_version": "R7H_CUDA_VERSION",
        "cudnn_version": "R7H_CUDNN_VERSION",
        "operating_system": "R7H_OPERATING_SYSTEM",
    }
    codes = {
        "ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH": "R7H_ENV_RECEIPT_HASH",
        "MINIMAL_CURRENT_ENVIRONMENT_IDENTITY_MISMATCH": "R7H_ENV_RECEIPT_HASH",
        "MINIMAL_CURRENT_ENVIRONMENT_MISSING": "R7H_ENV_RECEIPT_MISSING",
        "MINIMAL_CURRENT_ENVIRONMENT_AMBIGUOUS": "R7H_ENV_RECEIPT_AMBIGUOUS",
        "MINIMAL_CURRENT_ENVIRONMENT_COMMIT_MISMATCH": "R7H_ENV_RECEIPT_COMMIT",
        "ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH": "R7H_ENV_RECEIPT_SCHEMA",
        "ENVIRONMENT_RECEIPT_IDENTITY_MISMATCH": "R7H_ENV_RECEIPT_IDENTITY",
        "RUNNING_PACKAGE_INVENTORY_MISMATCH": "R7H_PACKAGE_INVENTORY",
        "PACKAGE_INVENTORY_SCHEMA_INVALID": "R7H_PACKAGE_INVENTORY",
        "PACKAGE_INVENTORY_DUPLICATE_NAME": "R7H_PACKAGE_INVENTORY",
        "PACKAGE_INVENTORY_NOT_SORTED": "R7H_PACKAGE_INVENTORY",
        "PACKAGE_HASH_INVALID": "R7H_PACKAGE_INVENTORY",
        "PYTHON_HASH_INVALID": "R7H_PYTHON_HASH",
        "PYTHON_EXECUTABLE_RESOLUTION_FAILED": "R7H_PYTHON_HASH",
        "ENVIRONMENT_RUNTIME_UNAVAILABLE": "R7H_RUNTIME_UNAVAILABLE",
        "CUDA_CUDNN_RUNTIME_UNAVAILABLE": "R7H_CUDA_CUDNN_UNAVAILABLE",
        "CHECKPOINT_FILENAME_MISMATCH": "R7H_CHECKPOINT_NAME",
        "CHECKPOINT_NOT_REGULAR": "R7H_CHECKPOINT_FILE",
        "CHECKPOINT_SIZE_MISMATCH": "R7H_CHECKPOINT_SIZE",
        "CHECKPOINT_SHA256_MISMATCH": "R7H_CHECKPOINT_HASH",
        "CRC32C_EXTERNAL_FILE_AUTHORITY_MISMATCH": "R7H_CRC32C_FILE_HASH",
        "CRC32C_EXTERNAL_RUNTIME_AUTHORITY_MISMATCH": "R7H_CRC32C_RUNTIME",
        "MINIMAL_ROW_AUTHORITY_HASH_BINDING_MISMATCH": "R7H_SOURCE_COHORT_HASH",
    }
    # Wrappers can retain their established public error code. Follow only
    # explicit causes, with a fixed bound, and never stringify an exception.
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        code = getattr(current, "code", None)
        if (
            isinstance(current, stages.ProductionStageError)
            and code == "RUNNING_ENVIRONMENT_RUNTIME_MISMATCH"
        ):
            field_code = fields.get(getattr(current, "runtime_field", None))
            if field_code is not None:
                return field_code
        if isinstance(code, str) and code in codes:
            return codes[code]
        current = current.__cause__
    return "R7H_RUNTIME_AUTHORITY_INVALID"


def _script_authority() -> dict[str, str]:
    paths = {
        "r7h_controller_sha256": Path(__file__).resolve(),
        "full_sequential_sha256": Path(sequential.__file__).resolve(),
        "recovery_cli_sha256": Path(historical.__file__).resolve(),
        "finalizer_sha256": Path(finalizer.__file__).resolve(),
        "runner_sha256": RUNNER_PATH,
    }
    try:
        result = {key: core.sha256_file(path) for key, path in paths.items()}
    except Exception as exc:
        raise R7HContinuationError("R7H_SCRIPT_AUTHORITY_INVALID") from exc
    if any(SHA_RE.fullmatch(value) is None for value in result.values()):
        _fail("R7H_SCRIPT_AUTHORITY_INVALID")
    return dict(sorted(result.items()))


def _load_fixed_original_run(
    *,
    scheduler_job_identity: str,
    runtime_validation_context: stages.RuntimeAuthorityValidationContext,
) -> sequential.FullRun:
    """Rebuild the frozen scientific run under the current R7H runtime."""

    implementation_commit = _current_r8u_r7h_implementation_commit()
    try:
        current = minimal.discover_live_authority(
            runtime_validation_context=runtime_validation_context
        )
    except Exception as exc:
        raise R7HContinuationError(_runtime_authority_failure_code(exc)) from exc
    if current.governing_commit != implementation_commit:
        _fail("R7H_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    scientific = replace(current, governing_commit=SCIENTIFIC_COMMIT)
    try:
        plan, requirements, contract = sequential._build_frozen_plan(scientific)
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=requirements
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_ORIGINAL_PLAN_AUTHORITY_INVALID") from exc
    if plan_sha != PLAN_SHA256:
        _fail("R7H_ORIGINAL_PLAN_AUTHORITY_INVALID")
    plan_path = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
    try:
        materialized_plan = sequential._load_full_batch_plan_payload(
            plan_path,
            expected_bytes=historical.ORIGINAL_PLAN_BYTES,
            expected_sha256=PLAN_SHA256,
        )
        launch_payload = historical._read_private_exact(
            ATTEMPT_ROOT / "full_launch_authority.restricted.json",
            size=historical.ORIGINAL_LAUNCH_BYTES,
            digest=historical.ORIGINAL_LAUNCH_SHA256,
        )
        launch = dict(historical._strict_json(launch_payload))
    except Exception as exc:
        raise R7HContinuationError("R7H_ORIGINAL_RUN_AUTHORITY_INVALID") from exc
    if materialized_plan != plan:
        _fail("R7H_ORIGINAL_PLAN_AUTHORITY_INVALID")
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
        attempt_id=ATTEMPT_ID,
        production_root=PRODUCTION_ROOT,
        attempt_root=ATTEMPT_ROOT,
        plan_path=plan_path,
        launch_authority=launch,
        launch_authority_sha256=historical.ORIGINAL_LAUNCH_SHA256,
        scheduler_job_identity=scheduler_job_identity,
    )
    try:
        sequential._validate_private_directory(ATTEMPT_ROOT)
        sequential._validate_full_run(run)
    except Exception as exc:
        raise R7HContinuationError("R7H_ORIGINAL_RUN_AUTHORITY_INVALID") from exc
    return run


def load_r8u_r7h_sealed_history() -> dict[str, Any]:
    """Reopen the exact external Batch-16 partial seal without HEAD coupling."""

    try:
        value = sequential._r8u_r7h_fixed_sealed_history()
    except Exception as exc:
        raise R7HContinuationError("R7H_BATCH16_PARTIAL_SEAL_INVALID") from exc
    keys = getattr(sequential, "R8U_R7H_SEALED_HISTORY_KEYS", frozenset())
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail("R7H_BATCH16_PARTIAL_SEAL_INVALID")
    fixed = {
        "scientific_attempt_id": ATTEMPT_ID,
        "scientific_commit": SCIENTIFIC_COMMIT,
        "batch_plan_sha256": PLAN_SHA256,
        "batch_id": "c3_batch_015",
        "original_task_id": 16,
        "historical_failure_job_id": "7292691",
        "historical_failure_class": "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS",
        "failed_partial_files": EXPECTED_PARTIAL_FILES,
        "failed_partial_directories": EXPECTED_PARTIAL_DIRECTORIES,
        "failed_partial_bytes": EXPECTED_PARTIAL_BYTES,
        "batch16_failed_partial_metadata_projection_sha256": (
            EXPECTED_PARTIAL_METADATA_SHA256
        ),
        "active_finalized_extraction_caches": 0,
        "batch16_failed_partial_cache_retained": True,
        "batch16_failed_partial_cache_outside_active_topology": True,
        "batch16_failed_partial_cache_adopted": False,
        "batch16_failed_partial_cache_deleted": False,
        "batch16_failed_partial_cache_overwritten": False,
        "closed_failure_authority": True,
        "partial_outputs_modified": False,
        "partial_outputs_renamed": False,
        "npz_body_reads": 0,
    }
    if any(not _exact(value.get(key), expected) for key, expected in fixed.items()):
        _fail("R7H_BATCH16_PARTIAL_SEAL_INVALID")
    if SHA_RE.fullmatch(str(value.get("batch16_failed_partial_seal_sha256", ""))) is None:
        _fail("R7H_BATCH16_PARTIAL_SEAL_INVALID")
    return dict(value)


def validate_r8u_r7h_extraction_cache_topology(
    *,
    active_job_references: int = 0,
    active_process_references: int = 0,
    sealed_history_validator: Callable[[], Mapping[str, Any]] = (
        load_r8u_r7h_sealed_history
    ),
) -> dict[str, Any]:
    """Run the exact shared R7H PREBODY topology validator."""

    try:
        projection = sequential.validate_r8u_r7h_extraction_cache_topology(
            PRODUCTION_ROOT,
            current_attempt_id=ATTEMPT_ID,
            sealed_history_validator=sealed_history_validator,
            active_job_references=active_job_references,
            active_process_references=active_process_references,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "R7H_EXTRACTION_CACHE_TOPOLOGY_INVALID"))
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R7H_EXTRACTION_CACHE_TOPOLOGY_INVALID"
        raise R7HContinuationError(code) from exc
    value = asdict(projection)
    required_zero = (
        "canonical_clips_roots",
        "other_active_scientific_caches",
        "unknown_or_unsealed_caches",
        "finalized_but_unretired_caches",
        "symlink_or_nonregular_entries",
        "owner_or_mode_anomalies",
        "active_job_references",
        "active_process_references",
        "npz_body_reads",
    )
    if (
        value.get("status") != TOPOLOGY_PASS
        or value.get("sealed_current_attempt_batch16_failed_partials") != 1
        or any(
            isinstance(value.get(field), bool)
            or value.get(field) != 0
            for field in required_zero
        )
        or value.get("historical_partial_adoptable") is not False
        or value.get("historical_partial_mutable") is not False
        or value.get("historical_partial_outside_finalized_active_topology")
        is not True
        or value.get("paths_emitted") is not False
        or value.get("identifiers_emitted") is not False
    ):
        _fail("R7H_EXTRACTION_CACHE_TOPOLOGY_INVALID")
    return value


def _tree_metadata_counts(path: Path) -> dict[str, int]:
    """Count one fixed tree using lstat only; never open a scientific body."""

    counts = {"files": 0, "directories": 0, "bytes": 0, "anomalies": 0}
    if not os.path.lexists(path):
        return counts
    try:
        sequential._require_nonsymlink_components(path.parent)
        root = os.lstat(path)
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID"
        ) from exc
    if stat.S_ISLNK(root.st_mode):
        counts["anomalies"] += 1
        return counts
    if stat.S_ISREG(root.st_mode):
        if (
            root.st_uid != os.geteuid()
            or root.st_nlink != 1
            or stat.S_IMODE(root.st_mode) != 0o600
        ):
            counts["anomalies"] += 1
        counts["files"] = 1
        counts["bytes"] = int(root.st_size)
        return counts
    if not stat.S_ISDIR(root.st_mode):
        counts["anomalies"] += 1
        return counts
    if (
        root.st_uid != os.geteuid()
        or stat.S_IMODE(root.st_mode) not in {0o700, 0o2700}
    ):
        counts["anomalies"] += 1

    # scandir is consumed incrementally.  Unlike os.walk followed by sort,
    # this never materializes an arbitrarily wide directory before enforcing
    # the global metadata-entry budget.
    visited = 1
    counts["directories"] = 1
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_TOPOLOGY_ENTRIES:
                        _fail("R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID")
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise R7HContinuationError(
                            "R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID"
                        ) from exc
                    entry_path = current / entry.name
                    if stat.S_ISLNK(info.st_mode):
                        counts["anomalies"] += 1
                    elif stat.S_ISDIR(info.st_mode):
                        counts["directories"] += 1
                        if (
                            info.st_uid != os.geteuid()
                            or stat.S_IMODE(info.st_mode)
                            not in {0o700, 0o2700}
                        ):
                            counts["anomalies"] += 1
                        pending.append(entry_path)
                    elif stat.S_ISREG(info.st_mode):
                        if (
                            info.st_uid != os.geteuid()
                            or info.st_nlink != 1
                            or stat.S_IMODE(info.st_mode) != 0o600
                        ):
                            counts["anomalies"] += 1
                        counts["files"] += 1
                        counts["bytes"] += int(info.st_size)
                    else:
                        counts["anomalies"] += 1
        except R7HContinuationError:
            raise
        except OSError as exc:
            raise R7HContinuationError(
                "R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID"
            ) from exc
    return counts


def _consumed_tail_artifact_snapshot(run: sequential.FullRun) -> dict[str, Any]:
    """Snapshot only the three consumed tail roles and cohort output."""

    roles: dict[str, dict[str, Any]] = {}
    total_files = 0
    total_bytes = 0
    for task_id, ordinal in zip(TASK_IDS, range(16, 19), strict=True):
        batch_id = f"c3_batch_{ordinal:03d}"
        paths = sequential._batch_paths(run, batch_id)
        authorization = (
            run.attempt_root
            / "cache_retirement_authorizations"
            / f"{batch_id}.authorization.json"
        )
        roots = (
            paths["raw_batch"],
            paths["batch_root"],
            paths["extraction_batch_root"],
            authorization,
        )
        stats = [_tree_metadata_counts(path) for path in roots]
        if any(item["anomalies"] for item in stats):
            _fail("R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID")
        files = sum(item["files"] for item in stats)
        bytes_used = sum(item["bytes"] for item in stats)
        total_files += files
        total_bytes += bytes_used
        roles[f"task_{task_id}"] = {
            "task_id": task_id,
            "batch_id": batch_id,
            "download_began": any(
                os.path.lexists(path)
                for path in (paths["raw_batch"], paths["download_ledger"])
            ),
            "extraction_began": any(
                os.path.lexists(path)
                for path in (
                    paths["extraction_batch_root"],
                    paths["extraction_ledger"],
                )
            ),
            "echoprime_began": any(
                os.path.lexists(path)
                for path in (paths["echoprime"], paths["pooling_ledger"])
            ),
            "preservation_began": any(
                os.path.lexists(path)
                for path in (
                    paths["preservation"],
                    paths["eligibility_ledger"],
                    paths["final_ledger"],
                    paths["final_receipt"],
                    paths["final_transition"],
                    authorization,
                )
            ),
            "scientific_artifact_exists": files > 0,
            "preserved_scientific_artifact_files": files,
            "preserved_scientific_artifact_bytes": bytes_used,
            "scientific_body_reads": 0,
            "paths_emitted": False,
            "identifiers_emitted": False,
        }
    cohort_root = run.attempt_root / "cohort_finalization"
    cohort_stats = _tree_metadata_counts(cohort_root)
    if cohort_stats["anomalies"]:
        _fail("R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID")
    total_files += cohort_stats["files"]
    total_bytes += cohort_stats["bytes"]
    roles["finalizer"] = {
        "cohort_directory_exists": os.path.lexists(cohort_root),
        "cohort_directory_empty": (
            os.path.lexists(cohort_root)
            and cohort_stats["files"] == 0
            and cohort_stats["directories"] <= 1
        ),
        "scientific_artifact_exists": cohort_stats["files"] > 0,
        "preserved_scientific_artifact_files": cohort_stats["files"],
        "preserved_scientific_artifact_bytes": cohort_stats["bytes"],
        "scientific_body_reads": 0,
        "paths_emitted": False,
        "identifiers_emitted": False,
    }
    return {
        "roles": roles,
        "preserved_scientific_artifact_files": total_files,
        "preserved_scientific_artifact_bytes": total_bytes,
        "scientific_body_reads": 0,
        "paths_emitted": False,
        "identifiers_emitted": False,
    }


def _fixed_consumed_log(
    spec: accounting.FixedAccountingSpec,
    receipt: Mapping[str, Any],
    *,
    terminal_authority: Mapping[str, Any],
) -> dict[str, Any]:
    expected_markers = {
        17: (
            "R8U_R7D_STATUS=BLOCKED_FULL_SEQUENTIAL_"
            "R8U_R7D_EXTRACTION_CACHE_TOPOLOGY_INVALID"
        ),
        18: "R8U_R7D_STATUS=BLOCKED_PRIOR_FINAL_RECEIPT_NOT_REGULAR",
        19: "R8U_R7D_STATUS=BLOCKED_PRIOR_FINAL_RECEIPT_NOT_REGULAR",
        None: "R8U_R7D_STATUS=BLOCKED_BATCH_RECEIPT_NOT_REGULAR",
    }
    try:
        fixed_path = terminal_logs.fixed_scheduler_log_path(
            spec, terminal_authority=terminal_authority
        )
        expected_uid = terminal_logs._expected_owner_uid(terminal_authority)
        payload, metadata = terminal_logs._read_fixed_regular(
            fixed_path, expected_uid=expected_uid
        )
        blocked, failed_stage, passed = terminal_logs._fixed_markers(payload)
    except Exception as exc:
        raise R7HContinuationError("R7H_CONSUMED_R7F_LOG_INVALID") from exc
    if (
        blocked != expected_markers[spec.task_id]
        or passed is not None
        or type(receipt.get("failed")) is not int
        or type(receipt.get("exit_status")) is not int
        or receipt.get("failed") != 0
        or receipt.get("exit_status") != 78
        or receipt.get("terminal_classification") != "FAIL"
    ):
        _fail("R7H_CONSUMED_R7F_CLASSIFICATION_INVALID")
    return {
        "job_kind": spec.job_kind,
        "job_id": spec.job_id,
        "task_id": spec.task_id,
        "expected_job_role": spec.expected_job_role,
        "failed": 0,
        "exit_status": 78,
        "terminal_classification": "FAIL",
        "terminal_marker": blocked,
        "reported_failed_stage": failed_stage,
        "scheduler_log_sha256": _sha256(payload),
        "scheduler_log_bytes": len(payload),
        "scheduler_log_mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "application_failure_supported": False,
        "pre_scientific_or_predecessor_gate_failure_supported": True,
    }


def _fixed_terminal_authority() -> tuple[dict[str, Any], tuple[accounting.FixedAccountingSpec, ...]]:
    try:
        result = accounting.load_terminal_authority(
            adjudication_implementation_commit=R7G_ADJUDICATION_COMMIT
        )
        if (
            result.created is not False
            or result.authority_sha256 != R7G_TERMINAL_AUTHORITY_SHA256
        ):
            _fail("R7H_R7G_TERMINAL_AUTHORITY_INVALID")
        specs = accounting.fixed_accounting_specs(result.authority)
    except R7HContinuationError:
        raise
    except Exception as exc:
        raise R7HContinuationError("R7H_R7G_TERMINAL_AUTHORITY_INVALID") from exc
    identities = tuple((item.job_id, item.task_id) for item in specs)
    if identities != (
        (CONSUMED_ARRAY_JOB_ID, 17),
        (CONSUMED_ARRAY_JOB_ID, 18),
        (CONSUMED_ARRAY_JOB_ID, 19),
        (CONSUMED_FINALIZER_JOB_ID, None),
    ):
        _fail("R7H_CONSUMED_R7F_ACCOUNTING_SCOPE_INVALID")
    return dict(result.authority), specs


def _fixed_consumed_r7f_controls() -> dict[str, str]:
    paths = {
        "capacity": historical.R8U_R7F_CAPACITY_PATH,
        "continuation_claim": historical.R8U_R7F_CONTINUATION_CLAIM_PATH,
        "array_submission": historical.R8U_R7F_ARRAY_SUBMISSION_PATH,
        "finalizer_submission": historical.R8U_R7F_FINALIZER_SUBMISSION_PATH,
        "combined_submission": historical.R8U_R7D_CONTINUATION_SUBMISSION_PATH,
    }
    observed: dict[str, str] = {}
    for role, path in paths.items():
        payload = _read_private_bytes(
            Path(path), code="R7H_CONSUMED_R7F_AUTHORITY_INVALID"
        )
        digest = _sha256(payload)
        if digest != CONSUMED_R7F_AUTHORITY_SHA256[role]:
            _fail("R7H_CONSUMED_R7F_AUTHORITY_INVALID")
        # These are fixed control-plane JSON artifacts.  R7F capacity used
        # its declared indented producer; the controller controls used the
        # orchestration core's compact producer.  Accept only the exact
        # role-specific serializer, never arbitrary equivalent formatting.
        parsed = _strict_json(
            payload, code="R7H_CONSUMED_R7F_AUTHORITY_INVALID"
        )
        expected_payload = (
            (json.dumps(parsed, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            if role == "capacity"
            else _canonical(parsed)
        )
        if payload != expected_payload:
            _fail("R7H_CONSUMED_R7F_AUTHORITY_INVALID")
        observed[role] = digest
    return dict(sorted(observed.items()))


def _consumed_evidence_common(
    *, evidence_producer_commit: str,
) -> dict[str, Any]:
    """Reconstruct historical evidence without granting it execution authority."""

    current = _current_r8u_r7h_implementation_commit()
    if evidence_producer_commit not in {
        current, R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
    }:
        _fail("R7H_CONTROL_EPOCH_MISMATCH")
    value = _common(
        artifact_type="lvef_c3_r8u_r7h_consumed_r7f_evidence_v1",
        status="PASS_R7H_CONSUMED_R7F_ATTEMPT_CLOSED",
        implementation_commit=current,
    )
    # This is an in-memory replay of the named historical producer, never an
    # update of the sealed source receipt. New execution controls use _common
    # directly, which rejects every predecessor implementation.
    value["r7h_runtime_commit"] = evidence_producer_commit
    return value


def _derive_consumed_r7f_evidence(
    run: sequential.FullRun,
    *,
    capture_missing: bool,
    evidence_producer_commit: str | None = None,
) -> tuple[dict[str, Any], int]:
    producer = evidence_producer_commit or _current_r8u_r7h_implementation_commit()
    if capture_missing and producer != _current_r8u_r7h_implementation_commit():
        _fail("R7H_CONTROL_EPOCH_MISMATCH")
    authority, specs = _fixed_terminal_authority()
    consumed_controls = _fixed_consumed_r7f_controls()
    try:
        environment = accounting.validate_fixed_scheduler_accounting_environment(
            authority
        )
        task17, task17_sha = accounting.load_accounting_receipt(
            specs[0], terminal_authority=authority
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_CONSUMED_TASK17_ACCOUNTING_INVALID") from exc
    if task17_sha != CONSUMED_TASK17_ACCOUNTING_SHA256:
        _fail("R7H_CONSUMED_TASK17_ACCOUNTING_INVALID")

    receipts: dict[str, Mapping[str, Any]] = {"task_17": task17}
    hashes: dict[str, str] = {"task_17": task17_sha}
    query_count = 0
    for spec in specs[1:]:
        key = f"task_{spec.task_id}" if spec.task_id is not None else "finalizer"
        try:
            if capture_missing:
                result = accounting.reuse_or_query_fixed_accounting(
                    spec,
                    terminal_authority=authority,
                    environment=environment,
                )
                if (
                    result.qacct_query_count not in {0, 1}
                    or result.created is not (result.qacct_query_count == 1)
                ):
                    _fail("R7H_CONSUMED_R7F_QACCT_COUNT_INVALID")
                receipt, digest = result.receipt, result.receipt_sha256
                query_count += result.qacct_query_count
            else:
                receipt, digest = accounting.load_accounting_receipt(
                    spec, terminal_authority=authority
                )
        except R7HContinuationError:
            raise
        except Exception as exc:
            raise R7HContinuationError(
                f"R7H_CONSUMED_{key.upper()}_ACCOUNTING_INVALID"
            ) from exc
        receipts[key] = receipt
        hashes[key] = digest
    if query_count > 3:
        _fail("R7H_CONSUMED_R7F_QACCT_COUNT_INVALID")

    logs: dict[str, dict[str, Any]] = {}
    for spec in specs:
        key = f"task_{spec.task_id}" if spec.task_id is not None else "finalizer"
        logs[key] = _fixed_consumed_log(
            spec, receipts[key], terminal_authority=authority
        )
    snapshot = _consumed_tail_artifact_snapshot(run)
    for task_id in TASK_IDS:
        key = f"task_{task_id}"
        logs[key].update(snapshot["roles"][key])
        if task_id == 17:
            logs[key]["owner_classification"] = (
                "PRE_SCIENTIFIC_EXTRACTION_CACHE_TOPOLOGY_CONTROL_FAILURE"
            )
        else:
            logs[key]["owner_classification"] = (
                "PRE_SCIENTIFIC_PREDECESSOR_GATE_FAILURE"
            )
    logs["finalizer"].update(snapshot["roles"]["finalizer"])
    logs["finalizer"]["owner_classification"] = (
        "PRE_SCIENTIFIC_DOWNSTREAM_TAIL_RECEIPT_GATE_FAILURE"
    )

    control_paths = {
        "terminal_authority": accounting.TERMINAL_AUTHORITY_PATH,
        **{key: spec.receipt_path for key, spec in zip(
            ("task_17", "task_18", "task_19", "finalizer"), specs, strict=True
        )},
        "r7f_capacity": historical.R8U_R7F_CAPACITY_PATH,
        "r7f_continuation_claim": historical.R8U_R7F_CONTINUATION_CLAIM_PATH,
        "r7f_array_submission": historical.R8U_R7F_ARRAY_SUBMISSION_PATH,
        "r7f_finalizer_submission": historical.R8U_R7F_FINALIZER_SUBMISSION_PATH,
        "r7f_combined_submission": historical.R8U_R7D_CONTINUATION_SUBMISSION_PATH,
    }
    evidence_files = 0
    evidence_bytes = 0
    try:
        for path in control_paths.values():
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
            evidence_files += 1
            evidence_bytes += int(info.st_size)
        for log in logs.values():
            evidence_files += 1
            evidence_bytes += int(log["scheduler_log_bytes"])
    except R7HContinuationError:
        raise
    except OSError as exc:
        raise R7HContinuationError("R7H_CONSUMED_R7F_EVIDENCE_INVALID") from exc

    value = {
        **_consumed_evidence_common(
            evidence_producer_commit=producer,
        ),
        "r7g_terminal_authority_sha256": R7G_TERMINAL_AUTHORITY_SHA256,
        "consumed_r7f_authority_sha256": consumed_controls,
        "accounting_receipt_sha256": dict(sorted(hashes.items())),
        "records": dict(sorted(logs.items())),
        "qacct_queries_to_close_consumed_attempt": query_count,
        "task17_qacct_queries": 0,
        "maximum_qacct_queries_per_new_record": 1,
        "consumed_r7f_array_job_id": CONSUMED_ARRAY_JOB_ID,
        "consumed_r7f_finalizer_job_id": CONSUMED_FINALIZER_JOB_ID,
        "preserved_control_evidence_files": evidence_files,
        "preserved_control_evidence_bytes": evidence_bytes,
        "preserved_scientific_artifact_files": snapshot[
            "preserved_scientific_artifact_files"
        ],
        "preserved_scientific_artifact_bytes": snapshot[
            "preserved_scientific_artifact_bytes"
        ],
        "unexpected_scientific_artifacts_adopted": False,
        "unexpected_scientific_artifacts_overwritten": False,
        "scientific_body_reads": 0,
        "cloud_requests": 0,
        "npz_body_reads": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
        "paths_emitted": False,
        "identifiers_emitted": False,
    }
    return value, query_count


def _validate_consumed_evidence(
    value: Mapping[str, Any], *, run: sequential.FullRun,
    replay_tail_artifacts: bool = True,
) -> dict[str, Any]:
    producer = value.get("r7h_runtime_commit")
    if producer != R7H_TOPOLOGY_CORRECTION_BASE_COMMIT:
        _fail("R7H_CONTROL_EPOCH_MISMATCH")
    if _sha256(_canonical(value)) != PREDECESSOR_CONSUMED_EVIDENCE_SHA256:
        _fail("R7H_CONTROL_EVIDENCE_HASH")
    expected_common = _consumed_evidence_common(
        evidence_producer_commit=producer,
    )
    expected_keys = set(expected_common) | {
        "r7g_terminal_authority_sha256",
        "consumed_r7f_authority_sha256",
        "accounting_receipt_sha256",
        "records",
        "qacct_queries_to_close_consumed_attempt",
        "task17_qacct_queries",
        "maximum_qacct_queries_per_new_record",
        "consumed_r7f_array_job_id",
        "consumed_r7f_finalizer_job_id",
        "preserved_control_evidence_files",
        "preserved_control_evidence_bytes",
        "preserved_scientific_artifact_files",
        "preserved_scientific_artifact_bytes",
        "unexpected_scientific_artifacts_adopted",
        "unexpected_scientific_artifacts_overwritten",
        "scientific_body_reads",
        "cloud_requests",
        "npz_body_reads",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
        "paths_emitted",
        "identifiers_emitted",
    }
    hashes = value.get("accounting_receipt_sha256")
    consumed_controls = value.get("consumed_r7f_authority_sha256")
    records = value.get("records")
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or any(not _exact(value.get(key), item) for key, item in expected_common.items())
        or value.get("r7g_terminal_authority_sha256")
        != R7G_TERMINAL_AUTHORITY_SHA256
        or not isinstance(consumed_controls, Mapping)
        or not _exact(consumed_controls, CONSUMED_R7F_AUTHORITY_SHA256)
        or not isinstance(hashes, Mapping)
        or set(hashes) != {"task_17", "task_18", "task_19", "finalizer"}
        or hashes.get("task_17") != CONSUMED_TASK17_ACCOUNTING_SHA256
        or any(SHA_RE.fullmatch(str(item)) is None for item in hashes.values())
        or not isinstance(records, Mapping)
        or set(records) != set(hashes)
        or type(value.get("qacct_queries_to_close_consumed_attempt")) is not int
        or value["qacct_queries_to_close_consumed_attempt"] not in range(4)
        or value.get("task17_qacct_queries") != 0
        or value.get("maximum_qacct_queries_per_new_record") != 1
        or value.get("consumed_r7f_array_job_id") != CONSUMED_ARRAY_JOB_ID
        or value.get("consumed_r7f_finalizer_job_id")
        != CONSUMED_FINALIZER_JOB_ID
        or any(
            type(value.get(field)) is not int or int(value[field]) < 0
            for field in (
                "preserved_control_evidence_files",
                "preserved_control_evidence_bytes",
                "preserved_scientific_artifact_files",
                "preserved_scientific_artifact_bytes",
            )
        )
        or any(
            value.get(field) != 0
            for field in (
                "scientific_body_reads",
                "cloud_requests",
                "npz_body_reads",
                "model_fitting_count",
                "prediction_generation_count",
                "confirmatory_performance_access_count",
            )
        )
        or value.get("unexpected_scientific_artifacts_adopted") is not False
        or value.get("unexpected_scientific_artifacts_overwritten") is not False
        or value.get("paths_emitted") is not False
        or value.get("identifiers_emitted") is not False
    ):
        _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")

    # Reconstruct every immutable accounting/log field and every current
    # metadata-only tail-artifact field.  This binds the role booleans, the
    # per-role artifact counts, their aggregate totals, and the control-file
    # totals on every reuse without issuing any qacct query or opening a
    # scientific body.
    replay, replay_queries = _derive_consumed_r7f_evidence(
        run, capture_missing=False, evidence_producer_commit=producer,
    )
    if replay_queries != 0:
        _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
    replay["qacct_queries_to_close_consumed_attempt"] = value[
        "qacct_queries_to_close_consumed_attempt"
    ]
    if replay_tail_artifacts and not _exact(value, replay):
        _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
    if not replay_tail_artifacts:
        dynamic_top = {
            "records",
            "preserved_scientific_artifact_files",
            "preserved_scientific_artifact_bytes",
        }
        if any(
            not _exact(value.get(key), replay.get(key))
            for key in expected_keys - dynamic_top
        ):
            _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
        fixed_record_fields = {
            "job_kind",
            "job_id",
            "task_id",
            "expected_job_role",
            "failed",
            "exit_status",
            "terminal_classification",
            "terminal_marker",
            "reported_failed_stage",
            "scheduler_log_sha256",
            "scheduler_log_bytes",
            "scheduler_log_mode",
            "application_failure_supported",
            "pre_scientific_or_predecessor_gate_failure_supported",
            "owner_classification",
        }
        task_artifact_fields = {
            "batch_id",
            "download_began",
            "extraction_began",
            "echoprime_began",
            "preservation_began",
            "scientific_artifact_exists",
            "preserved_scientific_artifact_files",
            "preserved_scientific_artifact_bytes",
            "scientific_body_reads",
            "paths_emitted",
            "identifiers_emitted",
        }
        finalizer_artifact_fields = {
            "cohort_directory_exists",
            "cohort_directory_empty",
            "scientific_artifact_exists",
            "preserved_scientific_artifact_files",
            "preserved_scientific_artifact_bytes",
            "scientific_body_reads",
            "paths_emitted",
            "identifiers_emitted",
        }
        summed_files = 0
        summed_bytes = 0
        for key in ("task_17", "task_18", "task_19", "finalizer"):
            record = records.get(key)
            replay_record = replay["records"][key]
            artifact_fields = (
                finalizer_artifact_fields
                if key == "finalizer"
                else task_artifact_fields
            )
            if (
                not isinstance(record, Mapping)
                or set(record) != fixed_record_fields | artifact_fields
                or any(
                    not _exact(record.get(field), replay_record.get(field))
                    for field in fixed_record_fields
                )
                or any(
                    type(record.get(field)) is not bool
                    for field in artifact_fields
                    if field
                    not in {
                        "batch_id",
                        "preserved_scientific_artifact_files",
                        "preserved_scientific_artifact_bytes",
                        "scientific_body_reads",
                    }
                )
                or any(
                    type(record.get(field)) is not int or record[field] < 0
                    for field in (
                        "preserved_scientific_artifact_files",
                        "preserved_scientific_artifact_bytes",
                        "scientific_body_reads",
                    )
                )
                or record.get("scientific_body_reads") != 0
                or record.get("paths_emitted") is not False
                or record.get("identifiers_emitted") is not False
            ):
                _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
            if key != "finalizer" and record.get("batch_id") != replay_record.get(
                "batch_id"
            ):
                _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
            summed_files += record["preserved_scientific_artifact_files"]
            summed_bytes += record["preserved_scientific_artifact_bytes"]
        if (
            value.get("preserved_scientific_artifact_files") != summed_files
            or value.get("preserved_scientific_artifact_bytes") != summed_bytes
        ):
            _fail("R7H_CONSUMED_R7F_EVIDENCE_INVALID")
    return dict(value)


def _same_topology_execution_invariants(
    current: Mapping[str, Any], sealed: Mapping[str, Any],
) -> bool:
    """Compare payload safety while allowing validated provenance to grow."""

    if (
        not isinstance(current, Mapping)
        or not isinstance(sealed, Mapping)
        or set(current) != set(sealed)
    ):
        return False
    informational = {
        "retained_metadata_roots",
        "retained_metadata_files",
        "retained_metadata_bytes",
    }
    for value in (current, sealed):
        if any(
            type(value[field]) is not int or value[field] < 0
            for field in informational & set(value)
        ):
            return False
    return _exact(
        {key: value for key, value in current.items() if key not in informational},
        {key: value for key, value in sealed.items() if key not in informational},
    )


def _topology_authority(
    *, implementation_commit: str, topology: Mapping[str, Any],
    sealed_history: Mapping[str, Any], consumed_evidence_sha256: str,
    live_references: Mapping[str, Any],
) -> dict[str, Any]:
    validated_references = _validate_live_reference_projection(
        live_references, expected_job_count=0
    )
    if (
        topology.get("status") != TOPOLOGY_PASS
        or SHA_RE.fullmatch(consumed_evidence_sha256) is None
        or topology.get("active_job_references")
        != validated_references["active_job_references"]
        or topology.get("active_process_references")
        != validated_references["active_process_references"]
    ):
        _fail("R7H_TOPOLOGY_AUTHORITY_INVALID")
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_authority_v1",
            status=TOPOLOGY_PASS,
            implementation_commit=implementation_commit,
        ),
        "consumed_r7f_evidence_sha256": consumed_evidence_sha256,
        "batch16_failed_partial_seal_sha256": sealed_history[
            "batch16_failed_partial_seal_sha256"
        ],
        "batch16_failed_partial_metadata_sha256": (
            EXPECTED_PARTIAL_METADATA_SHA256
        ),
        "batch16_failed_partial_files": EXPECTED_PARTIAL_FILES,
        "batch16_failed_partial_directories": EXPECTED_PARTIAL_DIRECTORIES,
        "batch16_failed_partial_bytes": EXPECTED_PARTIAL_BYTES,
        "topology_projection": dict(topology),
        "live_reference_projection": dict(validated_references),
        "exact_historical_partial_count": 1,
        "other_active_cache_count": 0,
        "unknown_cache_count": 0,
        "finalized_unretired_cache_count": 0,
        "active_job_references": validated_references[
            "active_job_references"
        ],
        "active_process_references": validated_references[
            "active_process_references"
        ],
        "historical_partial_adopted": False,
        "historical_partial_modified": False,
        "historical_partial_deleted": False,
        "global_cache_policy_weakened": False,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
    }


def _validate_topology_authority(
    value: Mapping[str, Any], *, replay_tail_artifacts: bool = True
) -> dict[str, Any]:
    sealed = load_r8u_r7h_sealed_history()
    consumed, _payload, consumed_sha = _read_private_json(
        CONSUMED_EVIDENCE_PATH, code="R7H_CONSUMED_R7F_EVIDENCE_INVALID"
    )
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R7H_TOPOLOGY_AUTHORITY_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_consumed_evidence(
        consumed, run=run, replay_tail_artifacts=replay_tail_artifacts
    )
    projection = value.get("topology_projection")
    live_references = value.get("live_reference_projection")
    if not isinstance(projection, Mapping) or not isinstance(
        live_references, Mapping
    ):
        _fail("R7H_TOPOLOGY_AUTHORITY_INVALID")
    expected = _topology_authority(
        implementation_commit=_current_r8u_r7h_implementation_commit(),
        topology=projection,
        sealed_history=sealed,
        consumed_evidence_sha256=consumed_sha,
        live_references=live_references,
    )
    if not _exact(value, expected):
        _fail("R7H_TOPOLOGY_AUTHORITY_INVALID")
    return dict(value)


def _capacity_baselines(evidence: Mapping[str, Any]) -> dict[str, int]:
    control_bytes = evidence.get("preserved_control_evidence_bytes")
    control_files = evidence.get("preserved_control_evidence_files")
    scientific_bytes = evidence.get("preserved_scientific_artifact_bytes")
    scientific_files = evidence.get("preserved_scientific_artifact_files")
    values = (control_bytes, control_files, scientific_bytes, scientific_files)
    if any(type(item) is not int or item < 0 for item in values):
        _fail("R7H_CAPACITY_OBSERVATION_BASELINE_INVALID")
    return {
        "preserved_old_evidence_bytes": int(control_bytes),
        "preserved_old_evidence_files": int(control_files),
        "confirmed_partial_artifact_bytes": (
            EXPECTED_PARTIAL_BYTES + int(scientific_bytes)
        ),
        "confirmed_partial_artifact_files": (
            EXPECTED_PARTIAL_FILES + int(scientific_files)
        ),
    }


def _capacity_receipt(
    *,
    implementation_commit: str,
    producer: Mapping[str, Any],
    producer_sha256: str,
    consumed_evidence_sha256: str,
    topology_authority_sha256: str,
    baselines: Mapping[str, int],
) -> dict[str, Any]:
    projection = producer.get("capacity_projection")
    if (
        not isinstance(projection, Mapping)
        or SHA_RE.fullmatch(producer_sha256) is None
        or SHA_RE.fullmatch(consumed_evidence_sha256) is None
        or SHA_RE.fullmatch(topology_authority_sha256) is None
    ):
        _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")
    _raise_for_capacity_producer_status(producer)
    numeric_fields = (
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
        "research_usage_bytes",
        "research_files_used",
    )
    if any(
        type(projection.get(field)) is not int for field in numeric_fields
    ):
        _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")
    margins = (
        projection["quota_margin_beyond_reserve_bytes"],
        projection["physical_margin_beyond_reserve_bytes"],
    )
    if any(item < 0 for item in margins):
        _fail("BLOCKED_R7H_QUANTIFIED_CAPACITY_DEFICIT")
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_tasks17_19_capacity_v1",
            status=CAPACITY_PASS,
            implementation_commit=implementation_commit,
        ),
        "consumed_r7f_evidence_sha256": consumed_evidence_sha256,
        "topology_authority_sha256": topology_authority_sha256,
        "r7f_capacity_producer_sha256": producer_sha256,
        "r7f_capacity_producer_status": producer["status"],
        "finalized_prefix_receipt_sha256": list(PREFIX_FINAL_RECEIPT_SHA256),
        "batch16_final_receipt_sha256": BATCH16_FINAL_RECEIPT_SHA256,
        "batch16_failed_partial_seal_sha256": (
            load_r8u_r7h_sealed_history()[
                "batch16_failed_partial_seal_sha256"
            ]
        ),
        "remaining_studies": EXPECTED_REMAINING_STUDIES,
        "remaining_objects": EXPECTED_REMAINING_OBJECTS,
        "remaining_source_bytes": EXPECTED_REMAINING_SOURCE_BYTES,
        "maximum_simultaneous_active_extraction_caches": 1,
        "required_quota_reserve_bytes": 200_000_000_000,
        "required_physical_reserve_bytes": 200_000_000_000,
        "capacity_margin_bytes": min(margins),
        "quota_margin_beyond_reserve_bytes": margins[0],
        "physical_margin_beyond_reserve_bytes": margins[1],
        "file_slot_margin_after_demand": projection[
            "file_slot_margin_after_demand"
        ],
        "current_research_usage_bytes": projection["research_usage_bytes"],
        "current_research_files_used": projection["research_files_used"],
        "preserved_old_evidence_bytes_baseline": baselines[
            "preserved_old_evidence_bytes"
        ],
        "preserved_old_evidence_files_baseline": baselines[
            "preserved_old_evidence_files"
        ],
        "confirmed_partial_artifact_bytes_baseline": baselines[
            "confirmed_partial_artifact_bytes"
        ],
        "confirmed_partial_artifact_files_baseline": baselines[
            "confirmed_partial_artifact_files"
        ],
        "historical_partial_incremental_bytes": 0,
        "historical_partial_incremental_files": 0,
        "finalized_prefix_incremental_bytes": 0,
        "finalized_prefix_incremental_files": 0,
        "capacity_observation_count": 1,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "du_command_captures": 0,
        "scientific_body_reads": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "qsub_submissions": 0,
    }


def _validate_capacity_receipt(
    value: Mapping[str, Any], *, run: sequential.FullRun,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    producer, _payload, producer_sha = _read_indented_private_json(
        CAPACITY_PRODUCER_PATH,
        code="R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID",
    )
    baselines = _capacity_baselines(evidence)
    try:
        validated_producer = capacity.validate_fixed_r8u_r7f_tasks17_19_capacity(
            run.plan,
            producer,
            r7f_runtime_commit=_current_r8u_r7h_implementation_commit(),
            raw_capture_root=CAPACITY_RAW_CAPTURE_ROOT,
            **baselines,
        )
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID"
        ) from exc
    if not _exact(producer, validated_producer):
        _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")
    expected = _capacity_receipt(
        implementation_commit=_current_r8u_r7h_implementation_commit(),
        producer=producer,
        producer_sha256=producer_sha,
        consumed_evidence_sha256=core.sha256_file(CONSUMED_EVIDENCE_PATH),
        topology_authority_sha256=core.sha256_file(TOPOLOGY_AUTHORITY_PATH),
        baselines=baselines,
    )
    if not _exact(value, expected):
        _fail("R7H_CAPACITY_RECEIPT_INVALID")
    return dict(value)


def _raise_for_capacity_producer_status(
    producer: Mapping[str, Any],
) -> None:
    """Translate the mature producer's closed terminal states exactly."""

    status = producer.get("status")
    if status == capacity.R8U_R7F_CAPACITY_STATUS_PASS:
        return
    if status == capacity.R8U_R7F_CAPACITY_STATUS_DEFICIT:
        projection = producer.get("capacity_projection")
        if not isinstance(projection, Mapping):
            _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")
        field_map = {
            "quota_deficit_bytes": "quota_reserve_deficit_bytes",
            "physical_deficit_bytes": "physical_reserve_deficit_bytes",
            "file_slot_deficit": "file_slot_deficit",
        }
        deficits: dict[str, int] = {}
        for output_field, source_field in field_map.items():
            amount = projection.get(source_field)
            if type(amount) is not int or amount < 0:
                _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")
            deficits[output_field] = amount
        raise R7HContinuationError(
            "BLOCKED_R7H_QUANTIFIED_CAPACITY_DEFICIT",
            capacity_deficits=deficits,
        )
    if (
        isinstance(status, str)
        and status.startswith(capacity.R8U_R7F_CAPACITY_STATUS_OBSERVATION_PREFIX)
    ):
        suffix = status.removeprefix(
            capacity.R8U_R7F_CAPACITY_STATUS_OBSERVATION_PREFIX
        )
        code = f"BLOCKED_R7H_CAPACITY_OBSERVATION_{suffix}"
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "BLOCKED_R7H_CAPACITY_OBSERVATION_PRODUCER"
        raise R7HContinuationError(code)
    _fail("R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID")


def _scheduler_account_authority(
    *, implementation_commit: str, environment: Mapping[str, str],
) -> dict[str, Any]:
    try:
        user = pwd.getpwuid(os.geteuid())
        if Path(sys.executable) != scheduler.ECHOPRIME_PYTHON:
            _fail("R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
        python_sha = stages.resolved_python_executable_sha256(
            scheduler.ECHOPRIME_PYTHON
        )
        environment_sha = scheduler.qsub_environment_sha256(environment)
    except R7HContinuationError:
        raise
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID"
        ) from exc
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_scheduler_account_authority_v1",
            status="AUTHORIZED_R7H_SCHEDULER_ACCOUNT",
            implementation_commit=implementation_commit,
        ),
        "expected_effective_uid": int(os.geteuid()),
        "expected_scheduler_username": str(user.pw_name),
        "canonical_home": str(user.pw_dir),
        "python_sha256": python_sha,
        "qsub_environment_sha256": environment_sha,
        "sealed_qsub_environment": dict(environment),
        "authorized_worker_roles": list(WORKER_ROLES),
        "script_authority": _script_authority(),
    }


def _validate_scheduler_account(value: Mapping[str, Any]) -> dict[str, Any]:
    environment = value.get("sealed_qsub_environment")
    common = _common(
        artifact_type="lvef_c3_r8u_r7h_scheduler_account_authority_v1",
        status="AUTHORIZED_R7H_SCHEDULER_ACCOUNT",
        implementation_commit=_current_r8u_r7h_implementation_commit(),
    )
    expected_keys = set(common) | {
        "expected_effective_uid",
        "expected_scheduler_username",
        "canonical_home",
        "python_sha256",
        "qsub_environment_sha256",
        "sealed_qsub_environment",
        "authorized_worker_roles",
        "script_authority",
    }
    try:
        python_sha = stages.resolved_python_executable_sha256(
            scheduler.ECHOPRIME_PYTHON
        )
        environment_sha = scheduler.qsub_environment_sha256(environment)
        script_authority = _script_authority()
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID"
        ) from exc
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or any(not _exact(value.get(key), item) for key, item in common.items())
        or not isinstance(environment, Mapping)
        or type(value.get("expected_effective_uid")) is not int
        or value.get("expected_effective_uid") != os.geteuid()
        or not isinstance(value.get("expected_scheduler_username"), str)
        or scheduler.SAFE_ACCOUNT_RE.fullmatch(
            value["expected_scheduler_username"]
        )
        is None
        or not scheduler._canonical_absolute_path(value.get("canonical_home"))
        or value.get("python_sha256") != python_sha
        or value.get("qsub_environment_sha256") != environment_sha
        or value.get("authorized_worker_roles") != list(WORKER_ROLES)
        or not _exact(value.get("script_authority"), script_authority)
        or environment.get("USER")
        != value.get("expected_scheduler_username")
        or environment.get("LOGNAME")
        != value.get("expected_scheduler_username")
        or environment.get("HOME") != value.get("canonical_home")
    ):
        _fail("R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
    return dict(value)


def _ensure_scheduler_account(*, implementation_commit: str) -> dict[str, Any]:
    environment, _input_class = scheduler.build_qsub_environment()
    account = _scheduler_account_authority(
        implementation_commit=implementation_commit,
        environment=environment,
    )
    if os.path.lexists(SCHEDULER_ACCOUNT_PATH):
        observed, _payload, _digest = _read_private_json(
            SCHEDULER_ACCOUNT_PATH,
            code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
        )
        _validate_scheduler_account(observed)
        if not _exact(observed, account):
            _fail("R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID")
        return dict(observed)
    _write_private_json(
        SCHEDULER_ACCOUNT_PATH,
        account,
        code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_PUBLICATION_INVALID",
    )
    return account


def capture_r8u_r7h_capacity(
    *,
    capacity_process_runner: Callable[..., Any] | None = None,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Close consumed evidence and perform the sole R7H capacity observation."""

    implementation_commit = _current_r8u_r7h_implementation_commit()
    run = _load_fixed_original_run(
        scheduler_job_identity="R8U_R7H_CAPACITY_SUBMITTER",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    prefix = historical._r8u_r7d_bounded_prefix(run)
    if tuple(prefix) != tuple(PREFIX_FINAL_RECEIPT_SHA256):
        _fail("R7H_FINALIZED_PREFIX_INVALID")
    sealed_history = load_r8u_r7h_sealed_history()
    # The failed 95b1058 control attempt already sealed this evidence.
    # Missing history cannot be repaired by generating a new producer epoch
    # or repeating historical accounting queries, even in the fixed namespace.
    if not os.path.lexists(CONSUMED_EVIDENCE_PATH):
        _fail("R7H_CONTROL_EPOCH_MISMATCH")

    if os.path.lexists(CAPACITY_RECEIPT_PATH):
        evidence, _payload, _digest = _read_private_json(
            CONSUMED_EVIDENCE_PATH, code="R7H_CONSUMED_R7F_EVIDENCE_INVALID"
        )
        _validate_consumed_evidence(evidence, run=run)
        receipt, _payload, _digest = _read_private_json(
            CAPACITY_RECEIPT_PATH, code="R7H_CAPACITY_RECEIPT_INVALID"
        )
        validated = _validate_capacity_receipt(
            receipt, run=run, evidence=evidence
        )
        topology_value, _payload, _digest = _read_private_json(
            TOPOLOGY_AUTHORITY_PATH, code="R7H_TOPOLOGY_AUTHORITY_INVALID"
        )
        _validate_topology_authority(topology_value)
        _ensure_scheduler_account(implementation_commit=implementation_commit)
        return validated

    _ensure_private_directory(R7H_ROOT)
    evidence, _payload, evidence_sha = _read_private_json(
        CONSUMED_EVIDENCE_PATH, code="R7H_CONSUMED_R7F_EVIDENCE_INVALID"
    )
    _validate_consumed_evidence(evidence, run=run)

    if os.path.lexists(TOPOLOGY_AUTHORITY_PATH):
        observed, _payload, topology_sha = _read_private_json(
            TOPOLOGY_AUTHORITY_PATH, code="R7H_TOPOLOGY_AUTHORITY_INVALID"
        )
        _validate_topology_authority(observed)
    else:
        environment, _input_class = scheduler.build_qsub_environment()
        live_references = _live_reference_projection(
            environment=environment,
            qstat_runner=qstat_runner,
            process_runner=process_runner,
            expected_jobs=None,
        )
        topology = validate_r8u_r7h_extraction_cache_topology(
            active_job_references=live_references[
                "active_job_references"
            ],
            active_process_references=live_references[
                "active_process_references"
            ],
        )
        topology_authority = _topology_authority(
            implementation_commit=implementation_commit,
            topology=topology,
            sealed_history=sealed_history,
            consumed_evidence_sha256=evidence_sha,
            live_references=live_references,
        )
        topology_sha = _write_private_json(
            TOPOLOGY_AUTHORITY_PATH,
            topology_authority,
            code="R7H_TOPOLOGY_AUTHORITY_PUBLICATION_INVALID",
        )

    _ensure_private_directory(CAPACITY_ROOT)
    projection = capacity.require_fixed_r8u_r7f_tasks17_19_plan_projection(
        run.plan
    )
    if os.path.lexists(STATIC_PLAN_PROJECTION_PATH):
        observed, _payload, _digest = _read_indented_private_json(
            STATIC_PLAN_PROJECTION_PATH,
            code="R7H_CAPACITY_STATIC_PLAN_PROJECTION_INVALID",
        )
        if not _exact(observed, projection):
            _fail("R7H_CAPACITY_STATIC_PLAN_PROJECTION_INVALID")
    else:
        try:
            capacity.write_r8u_r7f_static_plan_projection_no_clobber(
                STATIC_PLAN_PROJECTION_PATH, projection
            )
        except Exception as exc:
            raise R7HContinuationError(
                "R7H_CAPACITY_STATIC_PLAN_PROJECTION_INVALID"
            ) from exc
    baselines = _capacity_baselines(evidence)
    try:
        if os.path.lexists(CAPACITY_PRODUCER_PATH):
            producer, _payload, _producer_sha = _read_indented_private_json(
                CAPACITY_PRODUCER_PATH,
                code="R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID",
            )
            producer = capacity.validate_fixed_r8u_r7f_tasks17_19_capacity(
                run.plan,
                producer,
                r7f_runtime_commit=implementation_commit,
                raw_capture_root=CAPACITY_RAW_CAPTURE_ROOT,
                **baselines,
            )
        else:
            producer = (
                capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
                    run.plan,
                    r7f_runtime_commit=implementation_commit,
                    receipt_path=CAPACITY_PRODUCER_PATH,
                    raw_capture_root=CAPACITY_RAW_CAPTURE_ROOT,
                    process_runner=capacity_process_runner,
                    **baselines,
                )
            )
    except capacity.R8UR7FCapacityObservationError as exc:
        code = str(exc.code).replace("R8U_R7F", "R7H")
        if not code.startswith("BLOCKED_R7H_CAPACITY_OBSERVATION_"):
            code = "BLOCKED_R7H_CAPACITY_OBSERVATION_PRODUCER"
        raise R7HContinuationError(code) from exc
    except Exception as exc:
        code = str(getattr(exc, "code", ""))
        if code == capacity.R8U_R7F_CAPACITY_STATUS_DEFICIT:
            projection_value = getattr(exc, "receipt", None)
            deficits: dict[str, int] = {}
            if isinstance(projection_value, Mapping) and isinstance(
                projection_value.get("capacity_projection"), Mapping
            ):
                inner = projection_value["capacity_projection"]
                for output, source in (
                    ("quota_deficit_bytes", "quota_reserve_deficit_bytes"),
                    ("physical_deficit_bytes", "physical_reserve_deficit_bytes"),
                    ("file_slot_deficit", "file_slot_deficit"),
                ):
                    item = inner.get(source)
                    if type(item) is int and item >= 0:
                        deficits[output] = item
            raise R7HContinuationError(
                "BLOCKED_R7H_QUANTIFIED_CAPACITY_DEFICIT",
                capacity_deficits=deficits,
            ) from exc
        raise R7HContinuationError(
            "BLOCKED_R7H_CAPACITY_OBSERVATION_PRODUCER"
        ) from exc
    _raise_for_capacity_producer_status(producer)
    producer_sha = core.sha256_file(CAPACITY_PRODUCER_PATH)
    receipt = _capacity_receipt(
        implementation_commit=implementation_commit,
        producer=producer,
        producer_sha256=producer_sha,
        consumed_evidence_sha256=evidence_sha,
        topology_authority_sha256=topology_sha,
        baselines=baselines,
    )
    _write_private_json(
        CAPACITY_RECEIPT_PATH,
        receipt,
        code="R7H_CAPACITY_RECEIPT_PUBLICATION_INVALID",
    )

    _ensure_scheduler_account(implementation_commit=implementation_commit)
    return _validate_capacity_receipt(receipt, run=run, evidence=evidence)


def _probe_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r7h_ctx_{implementation_commit[:8]}"


def _array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r7h_seq_{implementation_commit[:8]}"


def _finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8u_r7h_fin_{implementation_commit[:8]}"


def _probe_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _probe_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(PROBE_SCHEDULER_ROOT),
        "-t",
        "17",
        "-tc",
        "1",
        "-l",
        "h_rt=00:10:00",
        "-pe",
        "omp",
        "1",
        "-l",
        "mem_per_core=1G",
        str(RUNNER_PATH),
    ]


def _array_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _array_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(CONTINUATION_SCHEDULER_ROOT),
        "-t",
        TASK_RANGE,
        "-tc",
        str(MAX_CONCURRENCY),
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


def _finalizer_qsub_command(
    implementation_commit: str, array_job_id: str
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R7H_CONTINUATION_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _finalizer_job_name(implementation_commit),
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


def _parse_probe_qsub_stdout(payload: bytes) -> str:
    match = re.fullmatch(
        rb"([1-9][0-9]{0,19})(?:[.](?:17|17-17:1))?\n?", payload
    )
    if match is None:
        _fail("R7H_PROBE_QSUB_OUTPUT_INVALID")
    return match.group(1).decode("ascii")


def _parse_array_qsub_stdout(payload: bytes) -> str:
    match = re.fullmatch(
        rb"([1-9][0-9]{0,19})(?:[.](?:17-19|17-19:1))?\n?", payload
    )
    if match is None:
        _fail("R7H_ARRAY_QSUB_OUTPUT_INVALID")
    return match.group(1).decode("ascii")


def _qsub_evidence(
    root: Path,
    label: str,
    *,
    parser: Callable[[bytes], str],
    expected_job_id: str,
) -> dict[str, Any]:
    if label not in {"probe", "array", "finalizer"}:
        _fail("R7H_QSUB_EVIDENCE_INVALID")
    evidence: dict[str, bytes] = {}
    try:
        for kind in ("stdout", "stderr", "exit_status"):
            evidence[kind] = scheduler._read_scheduler_evidence(
                root / f"{label}.qsub.{kind}.restricted"
            )
    except Exception as exc:
        raise R7HContinuationError("R7H_QSUB_EVIDENCE_INVALID") from exc
    if (
        evidence["exit_status"] != b"0\n"
        or evidence["stderr"] != b""
        or parser(evidence["stdout"]) != expected_job_id
    ):
        _fail("R7H_QSUB_EVIDENCE_INVALID")
    return {
        "stdout_bytes": len(evidence["stdout"]),
        "stdout_sha256": _sha256(evidence["stdout"]),
        "stderr_bytes": 0,
        "stderr_sha256": _sha256(evidence["stderr"]),
        "exit_status": 0,
    }


def _job_name_for_label(label: str, implementation_commit: str) -> str:
    names = {
        "probe": _probe_job_name(implementation_commit),
        "array": _array_job_name(implementation_commit),
        "finalizer": _finalizer_job_name(implementation_commit),
    }
    try:
        return names[label]
    except KeyError as exc:
        raise R7HContinuationError("R7H_QSTAT_SNAPSHOT_INVALID") from exc


def _login_qstat_snapshot(
    *,
    environment: Mapping[str, str],
    runner: Callable[..., Any],
    expected: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One full-XML owner snapshot, filtering only relevant production jobs."""

    command = [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]]
    try:
        completed = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=dict(environment),
            timeout=15,
        )
        payload = bytes(completed.stdout)
        stderr = bytes(completed.stderr)
    except Exception as exc:
        raise R7HContinuationError("R7H_QSTAT_SNAPSHOT_INVALID") from exc
    if completed.returncode != 0 or stderr:
        _fail("R7H_QSTAT_SNAPSHOT_INVALID")
    try:
        parsed = scheduler._r8u_r7d_parse_qstat_xml(payload)
    except Exception as exc:
        raise R7HContinuationError("R7H_QSTAT_SNAPSHOT_INVALID") from exc
    relevant_pattern = re.compile(
        r"(?:lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin)|"
        r"r8u_(?:rec|seq|fin)|r8u_r[3-7]_(?:ctx|loc|res|rec|seq|fin)|"
        r"r8u_r7d_(?:ctx|seq|fin)|r8u_r7h_(?:ctx|seq|fin))_[0-9a-f]{8}|"
        r"c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12})"
    )
    relevant = [
        row for row in parsed if relevant_pattern.fullmatch(row.full_job_name)
    ]
    implementation_commit = _current_r8u_r7h_implementation_commit()
    expected_rows: set[tuple[str, str]] = set()
    if expected is not None:
        if (
            not isinstance(expected, Mapping)
            or not set(expected) <= {"probe", "array", "finalizer"}
            or not expected
            or any(JOB_RE.fullmatch(str(item)) is None for item in expected.values())
        ):
            _fail("R7H_QSTAT_SNAPSHOT_INVALID")
        expected_rows = {
            (str(job_id), _job_name_for_label(label, implementation_commit))
            for label, job_id in expected.items()
        }
    visible_target_pairs: set[tuple[str, str]] = set()
    for row in relevant:
        pair = (row.job_id, row.full_job_name)
        if pair not in expected_rows or row.owner != environment["USER"]:
            _fail("R7H_COMPETING_ACTIVE_JOB")
        visible_target_pairs.add(pair)
    return {
        "status": "PASS_R7H_RELEVANT_QSTAT_SNAPSHOT",
        "expected_job_count": len(expected_rows),
        "target_rows_visible": len(visible_target_pairs),
        "competing_relevant_jobs": 0,
        "job_id_matches": True,
        "job_name_matches": True,
        "owner_matches": True,
        "qstat_snapshot_count": 1,
        "qstat_argv_sha256": core.canonical_json_sha256({"argv": command}),
        "qstat_stdout_sha256": _sha256(payload),
        "truncated_display_name_used": False,
    }


def _process_quiescence(
    *, environment: Mapping[str, str], runner: Callable[..., Any]
) -> dict[str, Any]:
    try:
        projection = historical._r8u_r5_process_projection(
            environment=environment,
            runner=runner,
            worker_self_marker=Path(sys.argv[0]).name,
            additional_markers=(
                "--capture-r8u-r7h-capacity",
                "--submit-r8u-r7h-continuation-context-probe",
                "--run-r8u-r7h-continuation-context-probe",
                "--adjudicate-r8u-r7h-continuation-context-probe",
                "--submit-r8u-r7h-continuation-17-19",
                "--run-r8u-r7h-continuation-17-19-array-task",
                "--run-r8u-r7h-continuation-finalizer",
            ),
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_ACTIVE_PROCESS_REFERENCE") from exc
    if (
        not isinstance(projection, Mapping)
        or projection.get("matching_processes") != 0
        or type(projection.get("process_snapshot_count")) is not int
        or projection.get("process_snapshot_count") != 1
    ):
        _fail("R7H_ACTIVE_PROCESS_REFERENCE")
    return {
        "status": "PASS_ZERO_R7H_RELEVANT_PROCESSES",
        "matching_processes": 0,
        "process_snapshot_count": 1,
        "ps_argv_sha256": projection["ps_argv_sha256"],
        "ps_stdout_sha256": projection["ps_stdout_sha256"],
    }


def _live_reference_projection(
    *,
    environment: Mapping[str, str],
    qstat_runner: Callable[..., Any],
    process_runner: Callable[..., Any],
    expected_jobs: Mapping[str, str] | None,
) -> dict[str, Any]:
    qstat = _login_qstat_snapshot(
        environment=environment, runner=qstat_runner, expected=expected_jobs
    )
    processes = _process_quiescence(
        environment=environment, runner=process_runner
    )
    if (
        qstat.get("competing_relevant_jobs") != 0
        or processes.get("matching_processes") != 0
    ):
        _fail("R7H_ACTIVE_REFERENCE_CONTRADICTION")
    return {
        "status": "PASS_R7H_ZERO_COMPETING_LIVE_REFERENCES",
        "active_job_references": 0,
        "active_process_references": 0,
        "qstat_projection": qstat,
        "process_projection": processes,
    }


def _validate_live_reference_projection(
    value: Mapping[str, Any], *, expected_job_count: int
) -> dict[str, Any]:
    qstat = value.get("qstat_projection")
    processes = value.get("process_projection")
    qstat_keys = {
        "status",
        "expected_job_count",
        "target_rows_visible",
        "competing_relevant_jobs",
        "job_id_matches",
        "job_name_matches",
        "owner_matches",
        "qstat_snapshot_count",
        "qstat_argv_sha256",
        "qstat_stdout_sha256",
        "truncated_display_name_used",
    }
    process_keys = {
        "status",
        "matching_processes",
        "process_snapshot_count",
        "ps_argv_sha256",
        "ps_stdout_sha256",
    }
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {
            "status",
            "active_job_references",
            "active_process_references",
            "qstat_projection",
            "process_projection",
        }
        or value.get("status")
        != "PASS_R7H_ZERO_COMPETING_LIVE_REFERENCES"
        or value.get("active_job_references") != 0
        or value.get("active_process_references") != 0
        or not isinstance(qstat, Mapping)
        or set(qstat) != qstat_keys
        or qstat.get("status") != "PASS_R7H_RELEVANT_QSTAT_SNAPSHOT"
        or qstat.get("expected_job_count") != expected_job_count
        or type(qstat.get("target_rows_visible")) is not int
        or not 0 <= qstat["target_rows_visible"] <= expected_job_count
        or qstat.get("competing_relevant_jobs") != 0
        or any(
            qstat.get(field) is not True
            for field in ("job_id_matches", "job_name_matches", "owner_matches")
        )
        or qstat.get("qstat_snapshot_count") != 1
        or qstat.get("truncated_display_name_used") is not False
        or not isinstance(processes, Mapping)
        or set(processes) != process_keys
        or processes.get("status") != "PASS_ZERO_R7H_RELEVANT_PROCESSES"
        or processes.get("matching_processes") != 0
        or processes.get("process_snapshot_count") != 1
        or any(
            SHA_RE.fullmatch(str(item)) is None
            for item in (
                qstat.get("qstat_argv_sha256"),
                qstat.get("qstat_stdout_sha256"),
                processes.get("ps_argv_sha256"),
                processes.get("ps_stdout_sha256"),
            )
        )
    ):
        _fail("R7H_LIVE_REFERENCE_PROJECTION_INVALID")
    return dict(value)


def _require_tail_final_receipts_absent(run: sequential.FullRun) -> None:
    for ordinal in range(16, 19):
        receipt = sequential._batch_paths(
            run, f"c3_batch_{ordinal:03d}"
        )["final_receipt"]
        if os.path.lexists(receipt):
            _fail("R7H_PREEXISTING_TAIL_FINAL_RECEIPT")


def _load_capacity_chain(
    *, scheduler_job_identity: str, replay_tail_artifacts: bool = True
) -> tuple[
    sequential.FullRun,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    run = _load_fixed_original_run(
        scheduler_job_identity=scheduler_job_identity,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    prefix = historical._r8u_r7d_bounded_prefix(run)
    if tuple(prefix) != tuple(PREFIX_FINAL_RECEIPT_SHA256):
        _fail("R7H_FINALIZED_PREFIX_INVALID")
    evidence, _payload, _digest = _read_private_json(
        CONSUMED_EVIDENCE_PATH, code="R7H_CONSUMED_R7F_EVIDENCE_INVALID"
    )
    _validate_consumed_evidence(
        evidence, run=run, replay_tail_artifacts=replay_tail_artifacts
    )
    topology, _payload, _digest = _read_private_json(
        TOPOLOGY_AUTHORITY_PATH, code="R7H_TOPOLOGY_AUTHORITY_INVALID"
    )
    _validate_topology_authority(
        topology, replay_tail_artifacts=replay_tail_artifacts
    )
    capacity_value, _payload, _digest = _read_private_json(
        CAPACITY_RECEIPT_PATH, code="R7H_CAPACITY_RECEIPT_INVALID"
    )
    _validate_capacity_receipt(capacity_value, run=run, evidence=evidence)
    account, _payload, _digest = _read_private_json(
        SCHEDULER_ACCOUNT_PATH,
        code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
    )
    _validate_scheduler_account(account)
    return run, evidence, topology, capacity_value, account


def _probe_authority(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    account: Mapping[str, Any],
    live_references: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_live_reference_projection(live_references, expected_job_count=0)
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_probe_authority_v1",
            status="AUTHORIZED_R7H_CPU_ONLY_TOPOLOGY_PROBE",
            implementation_commit=implementation_commit,
        ),
        "consumed_r7f_evidence_sha256": core.sha256_file(
            CONSUMED_EVIDENCE_PATH
        ),
        "topology_authority_sha256": core.sha256_file(
            TOPOLOGY_AUTHORITY_PATH
        ),
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "prefix_final_receipt_sha256": list(PREFIX_FINAL_RECEIPT_SHA256),
        "batch16_final_receipt_sha256": BATCH16_FINAL_RECEIPT_SHA256,
        "batch16_failed_partial_seal_sha256": load_r8u_r7h_sealed_history()[
            "batch16_failed_partial_seal_sha256"
        ],
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "script_authority": _script_authority(),
        "live_reference_projection": dict(live_references),
        "worker_role": PROBE_ROLE,
        "array_task_id": 17,
        "array_task_count": 1,
        "array_max_concurrency": 1,
        "cpu_slots": 1,
        "wall_seconds_maximum": 600,
        "gpu_requested": False,
        "scientific_execution_authorized": False,
        "continuation_claim_created": False,
        "cloud_requests_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "npz_body_reads_authorized": 0,
        "gpu_executions_authorized": 0,
        "extraction_executions_authorized": 0,
        "echoprime_executions_authorized": 0,
        "embedding_generations_authorized": 0,
        "preservation_executions_authorized": 0,
        "finalization_executions_authorized": 0,
        "scientific_artifact_writes_authorized": 0,
    }


def _probe_submission(
    *,
    implementation_commit: str,
    probe_job_id: str,
    account: Mapping[str, Any],
) -> dict[str, Any]:
    if JOB_RE.fullmatch(probe_job_id) is None:
        _fail("R7H_PROBE_SUBMISSION_INVALID")
    command = _probe_qsub_command(implementation_commit)
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_probe_submission_v1",
            status="PASS_EXACT_ONE_R7H_CPU_TOPOLOGY_PROBE_QSUB",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "probe_authority_sha256": core.sha256_file(PROBE_AUTHORITY_PATH),
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "probe_job_id": probe_job_id,
        "probe_job_name": _probe_job_name(implementation_commit),
        "worker_role": PROBE_ROLE,
        "probe_qsub_argv_sha256": core.canonical_json_sha256(
            {"argv": command}
        ),
        "probe_qsub_evidence": _qsub_evidence(
            PROBE_SCHEDULER_ROOT,
            "probe",
            parser=_parse_probe_qsub_stdout,
            expected_job_id=probe_job_id,
        ),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "array_task_id": 17,
        "array_task_count": 1,
        "array_max_concurrency": 1,
        "scheduler_submission_count": 1,
        "scheduler_submission_maximum": 3,
        "scientific_execution_authorized": False,
        "continuation_claim_created": False,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "scientific_artifact_writes": 0,
    }


def _validate_probe_chain(
    *, run: sequential.FullRun, account: Mapping[str, Any]
) -> dict[str, Any]:
    implementation_commit = _current_r8u_r7h_implementation_commit()
    authority, _payload, _digest = _read_private_json(
        PROBE_AUTHORITY_PATH, code="R7H_PROBE_AUTHORITY_INVALID"
    )
    references = authority.get("live_reference_projection")
    if not isinstance(references, Mapping):
        _fail("R7H_PROBE_AUTHORITY_INVALID")
    expected_authority = _probe_authority(
        run=run,
        implementation_commit=implementation_commit,
        account=account,
        live_references=references,
    )
    if not _exact(authority, expected_authority):
        _fail("R7H_PROBE_AUTHORITY_INVALID")
    submission, _payload, _digest = _read_private_json(
        PROBE_SUBMISSION_PATH, code="R7H_PROBE_SUBMISSION_INVALID"
    )
    job_id = submission.get("probe_job_id")
    if not isinstance(job_id, str):
        _fail("R7H_PROBE_SUBMISSION_INVALID")
    expected_submission = _probe_submission(
        implementation_commit=implementation_commit,
        probe_job_id=job_id,
        account=account,
    )
    if not _exact(submission, expected_submission):
        _fail("R7H_PROBE_SUBMISSION_INVALID")
    return dict(submission)


def submit_r8u_r7h_continuation_topology_probe(
    *,
    qsub_runner: Callable[..., Any] = subprocess.run,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Submit the only R7H qsub allowed before scientific execution."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r7h_implementation_commit()
    environment, _input_class = scheduler.build_qsub_environment()
    run, _evidence, topology_authority, _capacity_value, account = (
        _load_capacity_chain(scheduler_job_identity="R8U_R7H_PROBE_SUBMITTER")
    )
    if scheduler.qsub_environment_sha256(environment) != account.get(
        "qsub_environment_sha256"
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    _require_tail_final_receipts_absent(run)
    if any(os.path.lexists(path) for path in PRE_PROBE_COLLISION_PATHS):
        _fail("R7H_PROBE_OR_CONTINUATION_OUTPUT_COLLISION")
    references = _live_reference_projection(
        environment=environment,
        qstat_runner=qstat_runner,
        process_runner=process_runner,
        expected_jobs=None,
    )
    topology = validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )
    if not _same_topology_execution_invariants(
        topology, topology_authority.get("topology_projection")
    ):
        _fail("R7H_TOPOLOGY_AUTHORITY_INVALID")
    _ensure_private_directory(PROBE_ROOT, fresh=True)
    _ensure_private_directory(PROBE_SCHEDULER_ROOT, fresh=True)
    authority = _probe_authority(
        run=run,
        implementation_commit=implementation_commit,
        account=account,
        live_references=references,
    )
    _write_private_json(
        PROBE_AUTHORITY_PATH,
        authority,
        code="R7H_PROBE_AUTHORITY_PUBLICATION_INVALID",
    )
    try:
        probe_job_id = scheduler._capture_qsub(
            "probe",
            _probe_qsub_command(implementation_commit),
            root=PROBE_SCHEDULER_ROOT,
            environment=environment,
            runner=qsub_runner,
            parser=_parse_probe_qsub_stdout,
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_QSUB_FAILED") from exc
    submission = _probe_submission(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        account=account,
    )
    _write_private_json(
        PROBE_SUBMISSION_PATH,
        submission,
        code="R7H_PROBE_SUBMISSION_PUBLICATION_INVALID",
    )
    _validate_probe_chain(run=run, account=account)
    return {
        "status": "R7H_TOPOLOGY_PROBE_SUBMITTED_AWAITING_TERMINAL",
        "probe_job_id": probe_job_id,
        "qsub_exit": 0,
        "new_qsub_submissions": 1,
        "total_new_qsub_submissions": 1,
        "scientific_execution_authorized": False,
    }


def _worker_receipt_path(*, role: str, task_id: str | None) -> Path:
    if role == "probe" and task_id == "17":
        return PROBE_WORKER_RECEIPT_PATH
    if role == "array" and task_id in {"17", "18", "19"}:
        return WORKER_CONTEXT_ROOT / ARRAY_WORKER_RECEIPT_TEMPLATE.format(
            task_id=task_id
        )
    if role == "finalizer" and task_id is None:
        return FINALIZER_WORKER_RECEIPT_PATH
    _fail("R7H_WORKER_ROLE_INVALID")


def _wait_for_private_control(
    path: Path,
    *,
    code: str,
    timeout_seconds: float = 60.0,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> None:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not 0 < float(timeout_seconds) <= 60
    ):
        _fail(code)
    deadline = float(monotonic_clock()) + float(timeout_seconds)
    while not os.path.lexists(path):
        if float(monotonic_clock()) >= deadline:
            _fail(code)
        sleeper(0.25)


def _worker_diagnostics_value(
    diagnostic: scheduler.WorkerSchedulerDiagnostics,
) -> dict[str, Any]:
    value = diagnostic._asdict()
    value["classifications"] = list(diagnostic.classifications)
    return value


def _qstat_diagnostic_value(
    diagnostic: scheduler.R8UR7DQstatDiagnostic,
) -> dict[str, Any]:
    return {
        "classification": diagnostic.classification,
        "observation_count": diagnostic.observation_count,
        "row_ever_visible": diagnostic.row_ever_visible,
        "job_id_equality": diagnostic.job_id_equality,
        "task_id_equality": diagnostic.task_id_equality,
        "owner_equality": diagnostic.owner_equality,
        "full_job_name_equality": diagnostic.full_job_name_equality,
        "observed_scheduler_state_category": (
            diagnostic.observed_scheduler_state_category
        ),
        "observations": [item._asdict() for item in diagnostic.observations],
    }


def _worker_receipt(
    *,
    implementation_commit: str,
    role: str,
    logical_role: str,
    expected_job_id: str,
    expected_task_id: str | None,
    expected_job_name: str,
    account_sha256: str,
    authority_sha256: str,
    submission_sha256: str,
    context: scheduler.WorkerSchedulerContext,
    diagnostic: scheduler.R8UR7DQstatDiagnostic,
) -> dict[str, Any]:
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_worker_context_receipt_v1",
            status="PASS_R7H_CONTROLLING_WORKER_IDENTITY",
            implementation_commit=implementation_commit,
        ),
        "scheduler_account_authority_sha256": account_sha256,
        "role_authority_sha256": authority_sha256,
        "role_submission_receipt_sha256": submission_sha256,
        "role": role,
        "logical_worker_role": logical_role,
        "job_id": expected_job_id,
        "task_id": None if expected_task_id is None else int(expected_task_id),
        "expected_full_job_name": expected_job_name,
        "worker_diagnostics": _worker_diagnostics_value(context.diagnostics),
        "qstat_diagnostic": _qstat_diagnostic_value(diagnostic),
        "final_closed_classification": diagnostic.classification,
        "controlling_worker_identity": "PASS",
        "qstat_is_identity_authority": False,
        "qstat_absence_is_failure": False,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_requested": role == "array",
        "gpu_executions": 0,
        "scientific_stage_executions": 0,
    }


def _validate_worker_receipt(
    value: Mapping[str, Any],
    *,
    role: str,
    logical_role: str,
    expected_job_id: str,
    expected_task_id: str | None,
    expected_job_name: str,
    account_sha256: str,
    authority_sha256: str,
    submission_sha256: str,
) -> dict[str, Any]:
    common = _common(
        artifact_type="lvef_c3_r8u_r7h_worker_context_receipt_v1",
        status="PASS_R7H_CONTROLLING_WORKER_IDENTITY",
        implementation_commit=_current_r8u_r7h_implementation_commit(),
    )
    expected_keys = set(common) | {
        "scheduler_account_authority_sha256",
        "role_authority_sha256",
        "role_submission_receipt_sha256",
        "role",
        "logical_worker_role",
        "job_id",
        "task_id",
        "expected_full_job_name",
        "worker_diagnostics",
        "qstat_diagnostic",
        "final_closed_classification",
        "controlling_worker_identity",
        "qstat_is_identity_authority",
        "qstat_absence_is_failure",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_requested",
        "gpu_executions",
        "scientific_stage_executions",
    }
    worker = value.get("worker_diagnostics")
    qstat = value.get("qstat_diagnostic")
    worker_keys = set(scheduler.WorkerSchedulerDiagnostics._fields)
    qstat_keys = set(scheduler.R8UR7DQstatDiagnostic._fields)
    equality_fields = (
        "effective_uid_match",
        "job_id_match",
        "task_context_match",
        "job_role_match",
        "runner_sha256_match",
        "python_sha256_match",
        "implementation_commit_match",
        "qsub_environment_sha256_match",
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or any(not _exact(value.get(key), item) for key, item in common.items())
        or value.get("scheduler_account_authority_sha256") != account_sha256
        or value.get("role_authority_sha256") != authority_sha256
        or value.get("role_submission_receipt_sha256") != submission_sha256
        or value.get("role") != role
        or value.get("logical_worker_role") != logical_role
        or value.get("job_id") != expected_job_id
        or value.get("task_id")
        != (None if expected_task_id is None else int(expected_task_id))
        or value.get("expected_full_job_name") != expected_job_name
        or value.get("controlling_worker_identity") != "PASS"
        or value.get("qstat_is_identity_authority") is not False
        or value.get("qstat_absence_is_failure") is not False
        or not isinstance(worker, Mapping)
        or set(worker) != worker_keys
        or any(worker.get(field) is not True for field in equality_fields)
        or not isinstance(worker.get("classifications"), list)
        or not worker["classifications"]
        or any(
            item not in scheduler.WORKER_DIAGNOSTIC_CLASSIFICATIONS
            for item in worker["classifications"]
        )
        or not isinstance(qstat, Mapping)
        or set(qstat) != qstat_keys
        or qstat.get("classification")
        not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS
        or qstat.get("classification")
        != value.get("final_closed_classification")
        or qstat.get("job_id_equality") is not True
        or qstat.get("task_id_equality") is not True
        or qstat.get("owner_equality") is not True
        or qstat.get("full_job_name_equality") is not True
        or type(qstat.get("observation_count")) is not int
        or qstat["observation_count"] < 1
        or not isinstance(qstat.get("observations"), list)
        or len(qstat["observations"]) != qstat["observation_count"]
        or any(
            value.get(field) != 0
            for field in (
                "cloud_requests",
                "dicom_body_reads",
                "npz_body_reads",
                "scientific_stage_executions",
            )
        )
        or value.get("gpu_requested") is not (role == "array")
        or value.get("gpu_executions") != 0
    ):
        _fail("R7H_WORKER_CONTEXT_RECEIPT_INVALID")
    observation_keys = set(scheduler.R8UR7DQstatObservation._fields)
    for observation in qstat["observations"]:
        if (
            not isinstance(observation, Mapping)
            or set(observation) != observation_keys
            or type(observation.get("observation_ordinal")) is not int
            or observation["observation_ordinal"] < 1
            or type(observation.get("record_present")) is not bool
            or type(observation.get("unique")) is not bool
        ):
            _fail("R7H_WORKER_CONTEXT_RECEIPT_INVALID")
        if observation["record_present"]:
            if (
                observation.get("unique") is not True
                or observation.get("job_id_match") is not True
                or observation.get("task_id_match") is not True
                or observation.get("owner_match") is not True
                or observation.get("full_job_name_match") is not True
            ):
                _fail("R7H_WORKER_CONTEXT_RECEIPT_INVALID")
        elif any(
            observation.get(field) is not None
            for field in (
                "job_id_match",
                "task_id_match",
                "owner_match",
                "full_job_name_match",
                "state_token",
            )
        ):
            _fail("R7H_WORKER_CONTEXT_RECEIPT_INVALID")
    return dict(value)


def validate_r8u_r7h_continuation_worker_submission(
    *,
    current_job_id: str,
    role: str,
    qstat_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Bind one worker to the exact sealed R7H scheduler submission."""

    if JOB_RE.fullmatch(current_job_id) is None or role not in {
        "probe",
        "array",
        "finalizer",
    }:
        _fail("R7H_WORKER_CONTEXT_INVALID")
    account, _payload, account_sha = _read_private_json(
        SCHEDULER_ACCOUNT_PATH,
        code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
    )
    _validate_scheduler_account(account)
    implementation_commit = _current_r8u_r7h_implementation_commit()
    run = _load_fixed_original_run(
        scheduler_job_identity=current_job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if role == "probe":
        _wait_for_private_control(
            PROBE_SUBMISSION_PATH,
            code="R7H_PROBE_SUBMISSION_RECEIPT_TIMEOUT",
        )
        submission = _validate_probe_chain(run=run, account=account)
        expected_job_id = str(submission["probe_job_id"])
        expected_task_id: str | None = "17"
        logical_role = PROBE_ROLE
        expected_job_name = _probe_job_name(implementation_commit)
        authority_path = PROBE_AUTHORITY_PATH
        submission_path = PROBE_SUBMISSION_PATH
    else:
        # The array can be dispatched before the login submitter has created the
        # held-finalizer and combined receipts.  Wait for every downstream
        # control explicitly so a fast Task 17 cannot fail before publication.
        for control_path in (
            ARRAY_SUBMISSION_PATH,
            FINALIZER_SUBMISSION_PATH,
            CONTINUATION_SUBMISSION_PATH,
        ):
            _wait_for_private_control(
                control_path,
                code="R7H_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT",
            )
        submission = _validate_continuation_chain(run=run, account=account)
        if role == "array":
            if task_text not in {"17", "18", "19"}:
                _fail("R7H_WORKER_CONTEXT_INVALID")
            expected_job_id = str(submission["array_job_id"])
            expected_task_id = task_text
            logical_role = ARRAY_ROLE
            expected_job_name = _array_job_name(implementation_commit)
            submission_path = ARRAY_SUBMISSION_PATH
        else:
            expected_job_id = str(submission["finalizer_job_id"])
            expected_task_id = None
            logical_role = FINALIZER_ROLE
            expected_job_name = _finalizer_job_name(implementation_commit)
            submission_path = FINALIZER_SUBMISSION_PATH
        authority_path = CONTINUATION_CLAIM_PATH
    if current_job_id != expected_job_id:
        _fail("R7H_JOB_ID_MISMATCH")
    # A sealed receipt is evidence for its original worker observation; each
    # invocation must still match the exact current dispatch, including the
    # role and implementation suffix carried by its scheduler job name.
    if os.environ.get("JOB_NAME") != expected_job_name:
        _fail("R7H_JOB_NAME_MISMATCH")
    try:
        observed_runner_sha = core.sha256_file(RUNNER_PATH)
        if Path(sys.executable) != scheduler.ECHOPRIME_PYTHON:
            _fail("SCHEDULER_PYTHON_AUTHORITY_MISMATCH")
        observed_python_sha = stages.resolved_python_executable_sha256(
            scheduler.ECHOPRIME_PYTHON
        )
        context = scheduler.build_worker_scheduler_context(
            expected_effective_uid=account["expected_effective_uid"],
            expected_scheduler_username=account["expected_scheduler_username"],
            canonical_home=account["canonical_home"],
            expected_job_id=expected_job_id,
            expected_job_role=logical_role,
            observed_job_role=logical_role,
            expected_qsub_environment_sha256=account[
                "qsub_environment_sha256"
            ],
            sealed_qsub_environment=account["sealed_qsub_environment"],
            expected_implementation_commit=implementation_commit,
            observed_implementation_commit=implementation_commit,
            expected_runner_sha256=account["script_authority"][
                "runner_sha256"
            ],
            observed_runner_sha256=observed_runner_sha,
            expected_python_sha256=account["python_sha256"],
            observed_python_sha256=observed_python_sha,
            source_environment=os.environ,
            expected_task_id=expected_task_id,
        )
    except R7HContinuationError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "SCHEDULER_WORKER_CONTEXT_INVALID"))
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "SCHEDULER_WORKER_CONTEXT_INVALID"
        raise R7HContinuationError(code) from exc
    authority_sha = core.sha256_file(authority_path)
    submission_sha = core.sha256_file(submission_path)
    path = _worker_receipt_path(role=role, task_id=expected_task_id)
    if os.path.lexists(path):
        existing, _payload, _digest = _read_private_json(
            path, code="R7H_WORKER_CONTEXT_RECEIPT_INVALID"
        )
        return _validate_worker_receipt(
            existing,
            role=role,
            logical_role=logical_role,
            expected_job_id=expected_job_id,
            expected_task_id=expected_task_id,
            expected_job_name=expected_job_name,
            account_sha256=account_sha,
            authority_sha256=authority_sha,
            submission_sha256=submission_sha,
        )
    try:
        diagnostic = scheduler.diagnose_r8u_r7d_qstat_self(
            environment=context.environment,
            expected_job_id=expected_job_id,
            expected_task_id=expected_task_id,
            expected_owner=account["expected_scheduler_username"],
            expected_full_job_name=expected_job_name,
            runner=qstat_runner,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "R7H_QSTAT_SELF_INVALID"))
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R7H_QSTAT_SELF_INVALID"
        raise R7HContinuationError(code) from exc
    if diagnostic.classification in scheduler.R8U_R7D_QSTAT_BLOCKING_CLASSIFICATIONS:
        _fail(diagnostic.classification)
    if diagnostic.classification not in scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS:
        _fail("R7H_QSTAT_SELF_INVALID")
    if role != "probe":
        _ensure_private_directory(WORKER_CONTEXT_ROOT)
    receipt = _worker_receipt(
        implementation_commit=implementation_commit,
        role=role,
        logical_role=logical_role,
        expected_job_id=expected_job_id,
        expected_task_id=expected_task_id,
        expected_job_name=expected_job_name,
        account_sha256=account_sha,
        authority_sha256=authority_sha,
        submission_sha256=submission_sha,
        context=context,
        diagnostic=diagnostic,
    )
    _write_private_json(
        path, receipt, code="R7H_WORKER_CONTEXT_RECEIPT_PUBLICATION_INVALID"
    )
    validated = _validate_worker_receipt(
        receipt,
        role=role,
        logical_role=logical_role,
        expected_job_id=expected_job_id,
        expected_task_id=expected_task_id,
        expected_job_name=expected_job_name,
        account_sha256=account_sha,
        authority_sha256=authority_sha,
        submission_sha256=submission_sha,
    )
    return validated


def _probe_topology_diagnostic(
    *,
    implementation_commit: str,
    probe_job_id: str,
    topology: Mapping[str, Any],
    live_references: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_live_reference_projection(live_references, expected_job_count=1)
    if topology.get("status") != TOPOLOGY_PASS:
        _fail("R7H_PROBE_TOPOLOGY_DIAGNOSTIC_INVALID")
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_probe_diagnostic_v1",
            status=PROBE_PASS,
            implementation_commit=implementation_commit,
        ),
        "probe_submission_receipt_sha256": core.sha256_file(
            PROBE_SUBMISSION_PATH
        ),
        "worker_context_receipt_sha256": core.sha256_file(
            PROBE_WORKER_RECEIPT_PATH
        ),
        "probe_job_id": probe_job_id,
        "task_id": 17,
        "topology_projection": dict(topology),
        "live_reference_projection": dict(live_references),
        "sealed_current_attempt_historical_partials": 1,
        "other_active_caches": 0,
        "unknown_caches": 0,
        "historical_partial_adopted": False,
        "historical_partial_modified": False,
        "scientific_artifacts_created": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "extraction_executions": 0,
        "echoprime_executions": 0,
        "embedding_generations": 0,
        "preservation_executions": 0,
        "finalization_executions": 0,
    }


def run_r8u_r7h_continuation_context_probe(
    *,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Run only identity, live-reference, seal, and topology gates."""

    job_id = str(os.environ.get("JOB_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or str(os.environ.get("SGE_TASK_ID", "")) != "17"
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
        or str(os.environ.get("NSLOTS", "")) != "1"
    ):
        _fail("R7H_PROBE_CONTEXT_INVALID")
    worker = validate_r8u_r7h_continuation_worker_submission(
        current_job_id=job_id, role="probe", qstat_runner=qstat_runner
    )
    account, _payload, _digest = _read_private_json(
        SCHEDULER_ACCOUNT_PATH,
        code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
    )
    _validate_scheduler_account(account)
    references = _live_reference_projection(
        environment=account["sealed_qsub_environment"],
        qstat_runner=qstat_runner,
        process_runner=process_runner,
        expected_jobs={"probe": job_id},
    )
    topology = validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )
    diagnostic = _probe_topology_diagnostic(
        implementation_commit=_current_r8u_r7h_implementation_commit(),
        probe_job_id=job_id,
        topology=topology,
        live_references=references,
    )
    if os.path.lexists(PROBE_TOPOLOGY_PATH):
        _fail("R7H_PROBE_TOPOLOGY_DIAGNOSTIC_COLLISION")
    _write_private_json(
        PROBE_TOPOLOGY_PATH,
        diagnostic,
        code="R7H_PROBE_TOPOLOGY_DIAGNOSTIC_PUBLICATION_INVALID",
    )
    return {
        "status": PROBE_PASS,
        "qstat_classification": worker["final_closed_classification"],
        "controlling_worker_identity": "PASS",
        "fixed_partial": "PASS",
        "other_active_caches": 0,
        "unknown_caches": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "scientific_stage_executions": 0,
    }


def _project_probe_qacct_record(
    *,
    normalized: Mapping[str, str],
    probe_job_id: str,
    account: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        record = accounting.normalize_qacct_record(normalized)
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
        if not required <= set(record):
            _fail("R7H_PROBE_ACCOUNTING_INVALID")
        failed = accounting._parse_failed(record["failed"])
        exit_status = accounting._parse_nonnegative_integer(
            record["exit_status"]
        )
        wall_seconds = accounting._parse_wall_seconds(record["ru_wallclock"])
        submission_time = accounting._parse_qacct_time(record["qsub_time"])
        start_time = accounting._parse_qacct_time(record["start_time"])
        end_time = accounting._parse_qacct_time(record["end_time"])
    except R7HContinuationError:
        raise
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_ACCOUNTING_INVALID") from exc
    command = [str(QACCT_PATH), "-j", probe_job_id, "-t", "17"]
    return {
        "fixed_qacct_argv": command,
        "fixed_qacct_argv_sha256": core.canonical_json_sha256(
            {"argv": command}
        ),
        "normalized_qacct_record": record,
        "normalized_qacct_record_sha256": (
            accounting.normalized_qacct_record_sha256(record)
        ),
        "failed": failed,
        "exit_status": exit_status,
        "wall_seconds": wall_seconds,
        "job_id_match": record["jobnumber"] == probe_job_id,
        "task_id_match": record["taskid"] == "17",
        "job_name_match": record["jobname"]
        == _probe_job_name(_current_r8u_r7h_implementation_commit()),
        "owner_match": record["owner"]
        == account["expected_scheduler_username"],
        "chronology_valid": submission_time <= start_time <= end_time,
    }


def _probe_qacct_once(
    *,
    probe_job_id: str,
    environment: Mapping[str, str],
    account: Mapping[str, Any],
    runner: Callable[..., Any],
) -> dict[str, Any] | None:
    try:
        accounting._validate_qacct_tool()
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_ACCOUNTING_UNAVAILABLE") from exc
    command = [str(QACCT_PATH), "-j", probe_job_id, "-t", "17"]
    try:
        completed = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=dict(environment),
            timeout=60,
        )
        stdout = bytes(completed.stdout)
        stderr = bytes(completed.stderr)
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_ACCOUNTING_UNAVAILABLE") from exc
    if completed.returncode != 0:
        return None
    if stderr or len(stdout) > 4 * 1024 * 1024:
        _fail("R7H_PROBE_ACCOUNTING_INVALID")
    try:
        records = accounting.parse_qacct_records(stdout)
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_ACCOUNTING_INVALID") from exc
    if len(records) != 1:
        return None
    return _project_probe_qacct_record(
        normalized=records[0], probe_job_id=probe_job_id, account=account
    )


def _validate_probe_qacct_projection(
    value: Mapping[str, Any],
    *,
    probe_job_id: str,
    account: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = value.get("normalized_qacct_record")
    if not isinstance(normalized, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in normalized.items()
    ):
        _fail("R7H_PROBE_ACCOUNTING_INVALID")
    expected = _project_probe_qacct_record(
        normalized=normalized, probe_job_id=probe_job_id, account=account
    )
    if not _exact(value, expected):
        _fail("R7H_PROBE_ACCOUNTING_INVALID")
    return dict(value)


def _probe_accounting_failure_code(
    projection: Mapping[str, Any]
) -> str | None:
    for field, code in (
        ("job_id_match", "R7H_PROBE_QACCT_JOB_ID_MISMATCH"),
        ("task_id_match", "R7H_PROBE_QACCT_TASK_ID_MISMATCH"),
        ("job_name_match", "R7H_PROBE_QACCT_JOB_NAME_MISMATCH"),
        ("owner_match", "R7H_PROBE_QACCT_OWNER_MISMATCH"),
        ("chronology_valid", "R7H_PROBE_QACCT_CHRONOLOGY_INVALID"),
    ):
        if projection.get(field) is not True:
            return code
    for field in ("failed", "exit_status", "wall_seconds"):
        item = projection.get(field)
        if type(item) is not int or item < 0:
            return "R7H_PROBE_ACCOUNTING_INVALID"
    if projection["failed"] != 0:
        return "R7H_PROBE_QACCT_FAILED_NONZERO"
    if projection["exit_status"] != 0:
        return "R7H_PROBE_QACCT_EXIT_STATUS_NONZERO"
    if projection["wall_seconds"] > 600:
        return "R7H_PROBE_QACCT_WALLCLOCK_EXCEEDED"
    return None


def _read_probe_log(
    *, probe_job_id: str, probe_job_name: str
) -> tuple[bytes, str]:
    path = PROBE_SCHEDULER_ROOT / f"{probe_job_name}.o{probe_job_id}.17"
    historical._wait_for_r8u_r5_control(path, timeout_seconds=30.0)
    descriptor = -1
    try:
        sequential._require_nonsymlink_components(path.parent)
        visible = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 64 * 1024))
            if not block:
                _fail("R7H_PROBE_LOG_INVALID")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except R7HContinuationError:
        raise
    except Exception as exc:
        raise R7HContinuationError("R7H_PROBE_LOG_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    payload = b"".join(chunks)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    marker = f"R8U_R7H_STATUS={PROBE_PASS}\n".encode("ascii")
    if (
        identity(visible) != identity(opened)
        or identity(opened) != identity(after)
        or not stat.S_ISREG(opened.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o022
        or not 1 <= len(payload) <= 4 * 1024 * 1024
        or payload.count(marker) != 1
        or b"BLOCKED_" in payload
    ):
        _fail("R7H_PROBE_LOG_INVALID")
    return payload, _sha256(payload)


def _validate_probe_topology_diagnostic(
    value: Mapping[str, Any], *, probe_job_id: str
) -> dict[str, Any]:
    topology = value.get("topology_projection")
    references = value.get("live_reference_projection")
    if not isinstance(topology, Mapping) or not isinstance(references, Mapping):
        _fail("R7H_PROBE_TOPOLOGY_DIAGNOSTIC_INVALID")
    expected = _probe_topology_diagnostic(
        implementation_commit=_current_r8u_r7h_implementation_commit(),
        probe_job_id=probe_job_id,
        topology=topology,
        live_references=references,
    )
    if not _exact(value, expected):
        _fail("R7H_PROBE_TOPOLOGY_DIAGNOSTIC_INVALID")
    return dict(value)


def _probe_accounting_receipt(
    *,
    implementation_commit: str,
    probe_job_id: str,
    projection: Mapping[str, Any],
    query_count: int,
    worker_sha256: str | None,
    topology_sha256: str | None,
    scheduler_log_sha256: str | None,
    failure_code: str | None,
) -> dict[str, Any]:
    if (
        type(query_count) is not int
        or query_count < 1
        or (failure_code is None)
        != (
            worker_sha256 is not None
            and topology_sha256 is not None
            and scheduler_log_sha256 is not None
        )
    ):
        _fail("R7H_PROBE_ACCOUNTING_RECEIPT_INVALID")
    if any(
        item is not None and SHA_RE.fullmatch(item) is None
        for item in (worker_sha256, topology_sha256, scheduler_log_sha256)
    ):
        _fail("R7H_PROBE_ACCOUNTING_RECEIPT_INVALID")
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_probe_accounting_v1",
            status=(
                "PASS_R7H_PROBE_QACCT_FAILED_0_EXIT_0"
                if failure_code is None
                else "FAIL_R7H_TOPOLOGY_PROBE_QACCT"
            ),
            implementation_commit=implementation_commit,
        ),
        "probe_submission_receipt_sha256": core.sha256_file(
            PROBE_SUBMISSION_PATH
        ),
        "probe_job_id": probe_job_id,
        "worker_context_receipt_sha256": worker_sha256,
        "topology_diagnostic_receipt_sha256": topology_sha256,
        "scheduler_log_sha256": scheduler_log_sha256,
        "accounting_projection": dict(projection),
        "qacct_query_count": query_count,
        "failure_code": failure_code,
        "failed": projection["failed"],
        "exit_status": projection["exit_status"],
    }


def _probe_terminal_receipt(
    *,
    implementation_commit: str,
    probe_job_id: str,
    projection: Mapping[str, Any],
    worker_sha256: str | None,
    topology_sha256: str | None,
    failure_code: str | None,
) -> dict[str, Any]:
    success = failure_code is None
    if success != (worker_sha256 is not None and topology_sha256 is not None):
        _fail("R7H_PROBE_TERMINAL_INVALID")
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_topology_probe_terminal_v1",
            status=PROBE_PASS if success else "FAIL_R7H_TOPOLOGY_PROBE",
            implementation_commit=implementation_commit,
        ),
        "probe_submission_receipt_sha256": core.sha256_file(
            PROBE_SUBMISSION_PATH
        ),
        "probe_job_id": probe_job_id,
        "worker_context_receipt_sha256": worker_sha256,
        "topology_diagnostic_receipt_sha256": topology_sha256,
        "accounting_receipt_sha256": core.sha256_file(PROBE_ACCOUNTING_PATH),
        "failure_code": failure_code,
        "failed": projection["failed"],
        "exit_status": projection["exit_status"],
        "task_id": 17,
        "controlling_worker_identity": "PASS" if success else "NOT_PROVED",
        "fixed_partial": "PASS" if success else "NOT_PROVED",
        "other_active_caches": 0 if success else None,
        "unknown_caches": 0 if success else None,
        "scientific_artifacts_created": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "scientific_stage_executions": 0,
    }


def _seal_probe_failure(
    *,
    implementation_commit: str,
    probe_job_id: str,
    projection: Mapping[str, Any],
    query_count: int,
    failure_code: str,
    worker_sha256: str | None = None,
    topology_sha256: str | None = None,
) -> NoReturn:
    accounting_receipt = _probe_accounting_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        query_count=query_count,
        worker_sha256=None,
        topology_sha256=None,
        scheduler_log_sha256=None,
        failure_code=failure_code,
    )
    _write_private_json(
        PROBE_ACCOUNTING_PATH,
        accounting_receipt,
        code="R7H_PROBE_ACCOUNTING_PUBLICATION_INVALID",
    )
    terminal = _probe_terminal_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        worker_sha256=None,
        topology_sha256=None,
        failure_code=failure_code,
    )
    _write_private_json(
        PROBE_TERMINAL_PATH,
        terminal,
        code="R7H_PROBE_TERMINAL_PUBLICATION_INVALID",
    )
    raise R7HContinuationError(
        failure_code,
        probe_qacct={
            "failed": projection["failed"],
            "exit_status": projection["exit_status"],
        },
    )


def _validate_probe_terminal() -> dict[str, Any]:
    terminal, _payload, _digest = _read_private_json(
        PROBE_TERMINAL_PATH, code="R7H_PROBE_TERMINAL_INVALID"
    )
    accounting_receipt, _payload, _digest = _read_private_json(
        PROBE_ACCOUNTING_PATH, code="R7H_PROBE_ACCOUNTING_RECEIPT_INVALID"
    )
    submission, _payload, _digest = _read_private_json(
        PROBE_SUBMISSION_PATH, code="R7H_PROBE_SUBMISSION_INVALID"
    )
    account, _payload, _digest = _read_private_json(
        SCHEDULER_ACCOUNT_PATH,
        code="R7H_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
    )
    probe_job_id = submission.get("probe_job_id")
    projection = accounting_receipt.get("accounting_projection")
    if not isinstance(probe_job_id, str) or not isinstance(projection, Mapping):
        _fail("R7H_PROBE_TERMINAL_INVALID")
    _validate_scheduler_account(account)
    _validate_probe_qacct_projection(
        projection, probe_job_id=probe_job_id, account=account
    )
    if _probe_accounting_failure_code(projection) is not None:
        _fail("R7H_PROBE_ACCOUNTING_RECEIPT_INVALID")
    implementation_commit = _current_r8u_r7h_implementation_commit()
    expected_submission = _probe_submission(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        account=account,
    )
    if not _exact(submission, expected_submission):
        _fail("R7H_PROBE_SUBMISSION_INVALID")
    worker, _payload, worker_sha = _read_private_json(
        PROBE_WORKER_RECEIPT_PATH,
        code="R7H_WORKER_CONTEXT_RECEIPT_INVALID",
    )
    _validate_worker_receipt(
        worker,
        role="probe",
        logical_role=PROBE_ROLE,
        expected_job_id=probe_job_id,
        expected_task_id="17",
        expected_job_name=_probe_job_name(implementation_commit),
        account_sha256=core.sha256_file(SCHEDULER_ACCOUNT_PATH),
        authority_sha256=core.sha256_file(PROBE_AUTHORITY_PATH),
        submission_sha256=core.sha256_file(PROBE_SUBMISSION_PATH),
    )
    topology, _payload, topology_sha = _read_private_json(
        PROBE_TOPOLOGY_PATH,
        code="R7H_PROBE_TOPOLOGY_DIAGNOSTIC_INVALID",
    )
    _validate_probe_topology_diagnostic(
        topology, probe_job_id=probe_job_id
    )
    _log, log_sha = _read_probe_log(
        probe_job_id=probe_job_id,
        probe_job_name=str(submission["probe_job_name"]),
    )
    expected_accounting = _probe_accounting_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        query_count=accounting_receipt.get("qacct_query_count"),
        worker_sha256=worker_sha,
        topology_sha256=topology_sha,
        scheduler_log_sha256=log_sha,
        failure_code=None,
    )
    if not _exact(accounting_receipt, expected_accounting):
        _fail("R7H_PROBE_ACCOUNTING_RECEIPT_INVALID")
    expected_terminal = _probe_terminal_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        worker_sha256=worker_sha,
        topology_sha256=topology_sha,
        failure_code=None,
    )
    if not _exact(terminal, expected_terminal):
        _fail("R7H_PROBE_TERMINAL_INVALID")
    return dict(terminal)


def adjudicate_r8u_r7h_continuation_topology_probe(
    *,
    qacct_runner: Callable[..., Any] = subprocess.run,
    timeout_seconds: float = 900.0,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """Boundedly seal the one probe's exact qacct and topology PASS."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < float(timeout_seconds) <= 900
        or any(
            os.path.lexists(path)
            for path in (PROBE_ACCOUNTING_PATH, PROBE_TERMINAL_PATH)
        )
    ):
        _fail("R7H_PROBE_ACCOUNTING_COLLISION_OR_BOUND_INVALID")
    run, _evidence, _topology, _capacity_value, account = _load_capacity_chain(
        scheduler_job_identity="R8U_R7H_PROBE_ADJUDICATOR"
    )
    submission = _validate_probe_chain(run=run, account=account)
    environment, _input_class = scheduler.build_qsub_environment()
    if scheduler.qsub_environment_sha256(environment) != account.get(
        "qsub_environment_sha256"
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    probe_job_id = str(submission["probe_job_id"])
    deadline = float(monotonic_clock()) + float(timeout_seconds)
    projection: dict[str, Any] | None = None
    query_count = 0
    while projection is None:
        projection = _probe_qacct_once(
            probe_job_id=probe_job_id,
            environment=environment,
            account=account,
            runner=qacct_runner,
        )
        query_count += 1
        if projection is not None:
            break
        if float(monotonic_clock()) >= deadline:
            _fail("R7H_PROBE_ACCOUNTING_TIMEOUT")
        sleeper(15.0)
    _validate_probe_qacct_projection(
        projection, probe_job_id=probe_job_id, account=account
    )
    implementation_commit = _current_r8u_r7h_implementation_commit()
    failure_code = _probe_accounting_failure_code(projection)
    if failure_code is not None:
        _seal_probe_failure(
            implementation_commit=implementation_commit,
            probe_job_id=probe_job_id,
            projection=projection,
            query_count=query_count,
            failure_code=failure_code,
        )
    historical._wait_for_r8u_r5_control(
        PROBE_WORKER_RECEIPT_PATH, timeout_seconds=30.0
    )
    historical._wait_for_r8u_r5_control(
        PROBE_TOPOLOGY_PATH, timeout_seconds=30.0
    )
    worker, _payload, worker_sha = _read_private_json(
        PROBE_WORKER_RECEIPT_PATH,
        code="R7H_WORKER_CONTEXT_RECEIPT_INVALID",
    )
    _validate_worker_receipt(
        worker,
        role="probe",
        logical_role=PROBE_ROLE,
        expected_job_id=probe_job_id,
        expected_task_id="17",
        expected_job_name=_probe_job_name(implementation_commit),
        account_sha256=core.sha256_file(SCHEDULER_ACCOUNT_PATH),
        authority_sha256=core.sha256_file(PROBE_AUTHORITY_PATH),
        submission_sha256=core.sha256_file(PROBE_SUBMISSION_PATH),
    )
    topology_value, _payload, topology_sha = _read_private_json(
        PROBE_TOPOLOGY_PATH,
        code="R7H_PROBE_TOPOLOGY_DIAGNOSTIC_INVALID",
    )
    _validate_probe_topology_diagnostic(
        topology_value, probe_job_id=probe_job_id
    )
    _log, log_sha = _read_probe_log(
        probe_job_id=probe_job_id,
        probe_job_name=str(submission["probe_job_name"]),
    )
    accounting_receipt = _probe_accounting_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        query_count=query_count,
        worker_sha256=worker_sha,
        topology_sha256=topology_sha,
        scheduler_log_sha256=log_sha,
        failure_code=None,
    )
    _write_private_json(
        PROBE_ACCOUNTING_PATH,
        accounting_receipt,
        code="R7H_PROBE_ACCOUNTING_PUBLICATION_INVALID",
    )
    terminal = _probe_terminal_receipt(
        implementation_commit=implementation_commit,
        probe_job_id=probe_job_id,
        projection=projection,
        worker_sha256=worker_sha,
        topology_sha256=topology_sha,
        failure_code=None,
    )
    _write_private_json(
        PROBE_TERMINAL_PATH,
        terminal,
        code="R7H_PROBE_TERMINAL_PUBLICATION_INVALID",
    )
    return _validate_probe_terminal()


def _continuation_claim(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    account: Mapping[str, Any],
    live_references: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_live_reference_projection(live_references, expected_job_count=0)
    consumed, _payload, _digest = _read_private_json(
        CONSUMED_EVIDENCE_PATH, code="R7H_CONSUMED_R7F_EVIDENCE_INVALID"
    )
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_fixed_continuation_claim_v1",
            status="AUTHORIZED_FRESH_R7H_CONTINUATION_17_19",
            implementation_commit=implementation_commit,
        ),
        "prefix_final_receipt_sha256": list(PREFIX_FINAL_RECEIPT_SHA256),
        "batch16_final_receipt_sha256": BATCH16_FINAL_RECEIPT_SHA256,
        "batch16_failed_partial_seal_sha256": load_r8u_r7h_sealed_history()[
            "batch16_failed_partial_seal_sha256"
        ],
        "batch16_failed_partial_metadata_sha256": (
            EXPECTED_PARTIAL_METADATA_SHA256
        ),
        "consumed_r7f_authority_sha256": dict(
            sorted(CONSUMED_R7F_AUTHORITY_SHA256.items())
        ),
        "consumed_r7f_accounting_receipt_sha256": dict(
            consumed["accounting_receipt_sha256"]
        ),
        "consumed_r7f_evidence_sha256": core.sha256_file(
            CONSUMED_EVIDENCE_PATH
        ),
        "topology_authority_sha256": core.sha256_file(
            TOPOLOGY_AUTHORITY_PATH
        ),
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "probe_terminal_receipt_sha256": core.sha256_file(
            PROBE_TERMINAL_PATH
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "script_authority": _script_authority(),
        "pre_submission_live_reference_projection": dict(live_references),
        "continuation_task_range": TASK_RANGE,
        "continuation_task_ids": list(TASK_IDS),
        "continuation_task_count": 3,
        "continuation_max_concurrency": MAX_CONCURRENCY,
        "terminal_batch_id": "c3_batch_018",
        "terminal_batch_studies": 30,
        "held_finalizer_count": 1,
        "probe_submission_count": 1,
        "scientific_array_submission_count": 1,
        "held_finalizer_submission_count": 1,
        "total_new_qsub_maximum": 3,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "fourth_submission_reachable": False,
        "batches_1_16_mutation_authorized": False,
        "historical_partial_adoption_authorized": False,
        "historical_partial_mutation_authorized": False,
        "consumed_r7f_receipt_scientific_success": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _validate_continuation_claim(
    *, run: sequential.FullRun, account: Mapping[str, Any]
) -> dict[str, Any]:
    value, _payload, _digest = _read_private_json(
        CONTINUATION_CLAIM_PATH, code="R7H_CONTINUATION_CLAIM_INVALID"
    )
    references = value.get("pre_submission_live_reference_projection")
    if not isinstance(references, Mapping):
        _fail("R7H_CONTINUATION_CLAIM_INVALID")
    expected = _continuation_claim(
        run=run,
        implementation_commit=_current_r8u_r7h_implementation_commit(),
        account=account,
        live_references=references,
    )
    if not _exact(value, expected):
        _fail("R7H_CONTINUATION_CLAIM_INVALID")
    return dict(value)


def _array_submission_receipt(
    *,
    implementation_commit: str,
    array_job_id: str,
    account: Mapping[str, Any],
    claim_sha256: str,
) -> dict[str, Any]:
    if JOB_RE.fullmatch(array_job_id) is None or SHA_RE.fullmatch(
        claim_sha256
    ) is None:
        _fail("R7H_ARRAY_SUBMISSION_INVALID")
    command = _array_qsub_command(implementation_commit)
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_array_submission_v1",
            status="PASS_EXACT_ONE_R7H_ARRAY_17_19_QSUB",
            implementation_commit=implementation_commit,
        ),
        "continuation_claim_sha256": claim_sha256,
        "capacity_receipt_sha256": core.sha256_file(CAPACITY_RECEIPT_PATH),
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "probe_terminal_receipt_sha256": core.sha256_file(
            PROBE_TERMINAL_PATH
        ),
        "array_job_id": array_job_id,
        "array_job_name": _array_job_name(implementation_commit),
        "worker_role": ARRAY_ROLE,
        "array_qsub_argv_sha256": core.canonical_json_sha256(
            {"argv": command}
        ),
        "array_qsub_evidence": _qsub_evidence(
            CONTINUATION_SCHEDULER_ROOT,
            "array",
            parser=_parse_array_qsub_stdout,
            expected_job_id=array_job_id,
        ),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "array_task_range": TASK_RANGE,
        "array_task_ids": list(TASK_IDS),
        "array_task_count": 3,
        "array_max_concurrency": MAX_CONCURRENCY,
        "gpu_count_per_task": 1,
        "gpu_compute_capability": "8.0",
        "gpu_memory": "48G",
        "cpu_slots_per_task": 4,
        "memory_per_core": "16G",
        "wall_time": "48:00:00",
        "scheduler_submission_ordinal": 2,
        "total_scheduler_submission_maximum": 3,
        "automatic_retry_authorized": False,
        "batches_1_16_mutation_authorized": False,
        "historical_partial_adoption_authorized": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def _finalizer_submission_receipt(
    *,
    implementation_commit: str,
    array_job_id: str,
    finalizer_job_id: str,
    account: Mapping[str, Any],
    claim_sha256: str,
    array_submission_sha256: str,
) -> dict[str, Any]:
    if (
        JOB_RE.fullmatch(array_job_id) is None
        or JOB_RE.fullmatch(finalizer_job_id) is None
        or any(
            SHA_RE.fullmatch(item) is None
            for item in (claim_sha256, array_submission_sha256)
        )
    ):
        _fail("R7H_FINALIZER_SUBMISSION_INVALID")
    command = _finalizer_qsub_command(implementation_commit, array_job_id)
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_finalizer_submission_v1",
            status="PASS_EXACT_ONE_R7H_HELD_FINALIZER_QSUB",
            implementation_commit=implementation_commit,
        ),
        "continuation_claim_sha256": claim_sha256,
        "array_submission_receipt_sha256": array_submission_sha256,
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "probe_terminal_receipt_sha256": core.sha256_file(
            PROBE_TERMINAL_PATH
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "finalizer_job_name": _finalizer_job_name(implementation_commit),
        "worker_role": FINALIZER_ROLE,
        "finalizer_qsub_argv_sha256": core.canonical_json_sha256(
            {"argv": command}
        ),
        "finalizer_qsub_evidence": _qsub_evidence(
            CONTINUATION_SCHEDULER_ROOT,
            "finalizer",
            parser=scheduler.parse_numeric_qsub_stdout,
            expected_job_id=finalizer_job_id,
        ),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "held_on_array_job_id": array_job_id,
        "finalizer_is_array": False,
        "gpu_requested": False,
        "cpu_slots": 4,
        "memory_per_core": "8G",
        "wall_time": "12:00:00",
        "scheduler_submission_ordinal": 3,
        "total_scheduler_submission_maximum": 3,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
    }


def _continuation_submission_receipt(
    *,
    implementation_commit: str,
    array_job_id: str,
    finalizer_job_id: str,
    account: Mapping[str, Any],
    claim_sha256: str,
    array_submission_sha256: str,
    finalizer_submission_sha256: str,
    initial_qstat_projection: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_live_reference_projection(
        {
            "status": "PASS_R7H_ZERO_COMPETING_LIVE_REFERENCES",
            "active_job_references": 0,
            "active_process_references": 0,
            "qstat_projection": dict(initial_qstat_projection),
            "process_projection": {
                "status": "PASS_ZERO_R7H_RELEVANT_PROCESSES",
                "matching_processes": 0,
                "process_snapshot_count": 1,
                "ps_argv_sha256": "0" * 64,
                "ps_stdout_sha256": "0" * 64,
            },
        },
        expected_job_count=2,
    )
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_fixed_continuation_submission_v1",
            status="PASS_EXACT_R7H_ARRAY_17_19_AND_HELD_FINALIZER",
            implementation_commit=implementation_commit,
        ),
        "continuation_claim_sha256": claim_sha256,
        "array_submission_receipt_sha256": array_submission_sha256,
        "finalizer_submission_receipt_sha256": finalizer_submission_sha256,
        "scheduler_account_authority_sha256": core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        "probe_terminal_receipt_sha256": core.sha256_file(
            PROBE_TERMINAL_PATH
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_job_name": _array_job_name(implementation_commit),
        "finalizer_job_name": _finalizer_job_name(implementation_commit),
        "qsub_environment_sha256": account["qsub_environment_sha256"],
        "initial_qstat_projection": dict(initial_qstat_projection),
        "array_task_range": TASK_RANGE,
        "array_task_ids": list(TASK_IDS),
        "array_max_concurrency": MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "probe_submission_count": 1,
        "array_submission_count": 1,
        "finalizer_submission_count": 1,
        "scheduler_submission_count": 3,
        "scheduler_submission_maximum": 3,
        "fourth_submission_reachable": False,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def _validate_continuation_chain(
    *, run: sequential.FullRun, account: Mapping[str, Any]
) -> dict[str, Any]:
    implementation_commit = _current_r8u_r7h_implementation_commit()
    claim = _validate_continuation_claim(run=run, account=account)
    claim_sha = core.sha256_file(CONTINUATION_CLAIM_PATH)
    array_value, _payload, array_sha = _read_private_json(
        ARRAY_SUBMISSION_PATH, code="R7H_ARRAY_SUBMISSION_INVALID"
    )
    array_job_id = array_value.get("array_job_id")
    if not isinstance(array_job_id, str):
        _fail("R7H_ARRAY_SUBMISSION_INVALID")
    expected_array = _array_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        account=account,
        claim_sha256=claim_sha,
    )
    if not _exact(array_value, expected_array):
        _fail("R7H_ARRAY_SUBMISSION_INVALID")
    finalizer_value, _payload, finalizer_sha = _read_private_json(
        FINALIZER_SUBMISSION_PATH, code="R7H_FINALIZER_SUBMISSION_INVALID"
    )
    finalizer_job_id = finalizer_value.get("finalizer_job_id")
    if not isinstance(finalizer_job_id, str):
        _fail("R7H_FINALIZER_SUBMISSION_INVALID")
    expected_finalizer = _finalizer_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        account=account,
        claim_sha256=claim_sha,
        array_submission_sha256=array_sha,
    )
    if not _exact(finalizer_value, expected_finalizer):
        _fail("R7H_FINALIZER_SUBMISSION_INVALID")
    combined, _payload, _digest = _read_private_json(
        CONTINUATION_SUBMISSION_PATH,
        code="R7H_CONTINUATION_SUBMISSION_INVALID",
    )
    qstat = combined.get("initial_qstat_projection")
    if not isinstance(qstat, Mapping):
        _fail("R7H_CONTINUATION_SUBMISSION_INVALID")
    expected_combined = _continuation_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        account=account,
        claim_sha256=claim_sha,
        array_submission_sha256=array_sha,
        finalizer_submission_sha256=finalizer_sha,
        initial_qstat_projection=qstat,
    )
    if not _exact(combined, expected_combined):
        _fail("R7H_CONTINUATION_SUBMISSION_INVALID")
    if claim.get("consumed_r7f_receipt_scientific_success") is not False:
        _fail("R7H_CONSUMED_FAILURE_USED_AS_SCIENTIFIC_SUCCESS")
    return dict(combined)


def _blocked_combined_submission(
    *,
    implementation_commit: str,
    array_job_id: str,
    finalizer_job_id: str,
    claim_sha256: str,
    array_submission_sha256: str,
    finalizer_submission_sha256: str,
    failure_code: str,
) -> dict[str, Any]:
    return {
        **_common(
            artifact_type="lvef_c3_r8u_r7h_fixed_continuation_submission_v1",
            status="FAIL_R7H_POST_SUBMISSION_QSTAT",
            implementation_commit=implementation_commit,
        ),
        "continuation_claim_sha256": claim_sha256,
        "array_submission_receipt_sha256": array_submission_sha256,
        "finalizer_submission_receipt_sha256": finalizer_submission_sha256,
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "failure_code": failure_code,
        "qstat_snapshot_attempt_count": 1,
        "array_task_range": TASK_RANGE,
        "array_max_concurrency": MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "scheduler_submission_count": 3,
        "scheduler_submission_maximum": 3,
        "fourth_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def submit_r8u_r7h_continuation_17_19(
    *,
    qsub_runner: Callable[..., Any] = subprocess.run,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Submit one fixed GPU tail array and its one held CPU finalizer."""

    scheduler.validate_scheduler_tools()
    implementation_commit = _current_r8u_r7h_implementation_commit()
    environment, _input_class = scheduler.build_qsub_environment()
    run, _evidence, topology_authority, _capacity_value, account = (
        _load_capacity_chain(
            scheduler_job_identity="R8U_R7H_CONTINUATION_SUBMITTER"
        )
    )
    _validate_probe_chain(run=run, account=account)
    _validate_probe_terminal()
    if scheduler.qsub_environment_sha256(environment) != account.get(
        "qsub_environment_sha256"
    ):
        _fail("SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH")
    _require_tail_final_receipts_absent(run)
    if any(os.path.lexists(path) for path in PRE_ARRAY_COLLISION_PATHS):
        _fail("R7H_CONTINUATION_OUTPUT_COLLISION")
    references = _live_reference_projection(
        environment=environment,
        qstat_runner=qstat_runner,
        process_runner=process_runner,
        expected_jobs=None,
    )
    topology = validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )
    if not _same_topology_execution_invariants(
        topology, topology_authority.get("topology_projection")
    ):
        _fail("R7H_TOPOLOGY_AUTHORITY_INVALID")
    claim = _continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        account=account,
        live_references=references,
    )
    claim_sha = _write_private_json(
        CONTINUATION_CLAIM_PATH,
        claim,
        code="R7H_CONTINUATION_CLAIM_PUBLICATION_INVALID",
    )
    _validate_continuation_claim(run=run, account=account)
    _ensure_private_directory(CONTINUATION_SCHEDULER_ROOT, fresh=True)
    try:
        array_job_id = scheduler._capture_qsub(
            "array",
            _array_qsub_command(implementation_commit),
            root=CONTINUATION_SCHEDULER_ROOT,
            environment=environment,
            runner=qsub_runner,
            parser=_parse_array_qsub_stdout,
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_ARRAY_QSUB_FAILED") from exc
    array_receipt = _array_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        account=account,
        claim_sha256=claim_sha,
    )
    array_sha = _write_private_json(
        ARRAY_SUBMISSION_PATH,
        array_receipt,
        code="R7H_ARRAY_SUBMISSION_PUBLICATION_INVALID",
    )
    reopened_array, _payload, _digest = _read_private_json(
        ARRAY_SUBMISSION_PATH, code="R7H_ARRAY_SUBMISSION_INVALID"
    )
    if not _exact(reopened_array, array_receipt):
        _fail("R7H_ARRAY_SUBMISSION_READBACK_INVALID")
    try:
        finalizer_job_id = scheduler._capture_qsub(
            "finalizer",
            _finalizer_qsub_command(implementation_commit, array_job_id),
            root=CONTINUATION_SCHEDULER_ROOT,
            environment=environment,
            runner=qsub_runner,
        )
    except Exception as exc:
        raise R7HContinuationError("R7H_FINALIZER_QSUB_FAILED") from exc
    finalizer_receipt = _finalizer_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        account=account,
        claim_sha256=claim_sha,
        array_submission_sha256=array_sha,
    )
    finalizer_sha = _write_private_json(
        FINALIZER_SUBMISSION_PATH,
        finalizer_receipt,
        code="R7H_FINALIZER_SUBMISSION_PUBLICATION_INVALID",
    )
    reopened_finalizer, _payload, _digest = _read_private_json(
        FINALIZER_SUBMISSION_PATH, code="R7H_FINALIZER_SUBMISSION_INVALID"
    )
    if not _exact(reopened_finalizer, finalizer_receipt):
        _fail("R7H_FINALIZER_SUBMISSION_READBACK_INVALID")
    try:
        initial_qstat = _login_qstat_snapshot(
            environment=environment,
            runner=qstat_runner,
            expected={"array": array_job_id, "finalizer": finalizer_job_id},
        )
    except R7HContinuationError as exc:
        blocked = _blocked_combined_submission(
            implementation_commit=implementation_commit,
            array_job_id=array_job_id,
            finalizer_job_id=finalizer_job_id,
            claim_sha256=claim_sha,
            array_submission_sha256=array_sha,
            finalizer_submission_sha256=finalizer_sha,
            failure_code=exc.code,
        )
        _write_private_json(
            CONTINUATION_SUBMISSION_PATH,
            blocked,
            code="R7H_CONTINUATION_SUBMISSION_PUBLICATION_INVALID",
        )
        raise
    combined = _continuation_submission_receipt(
        implementation_commit=implementation_commit,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        account=account,
        claim_sha256=claim_sha,
        array_submission_sha256=array_sha,
        finalizer_submission_sha256=finalizer_sha,
        initial_qstat_projection=initial_qstat,
    )
    combined_sha = _write_private_json(
        CONTINUATION_SUBMISSION_PATH,
        combined,
        code="R7H_CONTINUATION_SUBMISSION_PUBLICATION_INVALID",
    )
    _validate_continuation_chain(run=run, account=account)
    return {
        "status": SUBMISSION_PASS,
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "continuation_claim_sha256": claim_sha,
        "array_submission_receipt_sha256": array_sha,
        "finalizer_submission_receipt_sha256": finalizer_sha,
        "continuation_receipt_sha256": combined_sha,
        "task_range": TASK_RANGE,
        "array_max_concurrency": MAX_CONCURRENCY,
        "new_qsub_submissions": 2,
        "total_new_qsub_submissions": 3,
        "initial_qstat_projection": initial_qstat,
    }


def _worker_live_references(
    *,
    account: Mapping[str, Any],
    continuation: Mapping[str, Any],
    qstat_runner: Callable[..., Any],
    process_runner: Callable[..., Any],
) -> dict[str, Any]:
    return _live_reference_projection(
        environment=account["sealed_qsub_environment"],
        qstat_runner=qstat_runner,
        process_runner=process_runner,
        expected_jobs={
            "array": str(continuation["array_job_id"]),
            "finalizer": str(continuation["finalizer_job_id"]),
        },
    )


def run_r8u_r7h_continuation_array_task(
    *,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Run exactly one original tail batch under the additive R7H context."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"17", "18", "19"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) == ""
        or str(os.environ.get("NSLOTS", "")) != "4"
    ):
        _fail("R7H_CONTINUATION_ARRAY_CONTEXT_INVALID")
    validate_r8u_r7h_continuation_worker_submission(
        current_job_id=job_id, role="array", qstat_runner=qstat_runner
    )
    run, _evidence, _topology, _capacity_value, account = _load_capacity_chain(
        scheduler_job_identity=job_id, replay_tail_artifacts=False
    )
    continuation = _validate_continuation_chain(run=run, account=account)
    references = _worker_live_references(
        account=account,
        continuation=continuation,
        qstat_runner=qstat_runner,
        process_runner=process_runner,
    )
    # This is the same exact function invoked again inside run_batch_task,
    # before any cloud/body/GPU construction.  The explicit zero values come
    # only from the live scheduler/process proof immediately above.
    validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8U_R7H_FIXED_CONTINUATION,
        r8u_r7h_worker_submission_validator=(
            validate_r8u_r7h_continuation_worker_submission
        ),
        r8u_r7h_sealed_history_validator=load_r8u_r7h_sealed_history,
        r8u_r7h_active_job_references=references["active_job_references"],
        r8u_r7h_active_process_references=references[
            "active_process_references"
        ],
    )
    try:
        value = sequential.run_batch_task(
            task_id=int(task_text), run=run, dependencies=dependencies
        )
    except R7HContinuationError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "R7H_CONTINUATION_BATCH_FAILED"))
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R7H_CONTINUATION_BATCH_FAILED"
        raise R7HContinuationError(code) from exc
    if value.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R7H_CONTINUATION_BATCH_NOT_FINALIZED")
    return value


def _r7h_finalizer_authority(
    *,
    implementation_commit: str,
    evidence: Mapping[str, Any],
) -> finalizer.R8UR7HImplementationAuthority:
    hashes = evidence.get("accounting_receipt_sha256")
    if not isinstance(hashes, Mapping) or set(hashes) != {
        "task_17",
        "task_18",
        "task_19",
        "finalizer",
    }:
        _fail("R7H_FINALIZER_AUTHORITY_INVALID")
    return finalizer.R8UR7HImplementationAuthority(
        implementation_commit=implementation_commit,
        r7f_runtime_commit=R7F_RUNTIME_COMMIT,
        r7g_adjudication_commit=R7G_ADJUDICATION_COMMIT,
        finalized_prefix_receipt_sha256=tuple(
            PREFIX_FINAL_RECEIPT_SHA256
        ),
        historical_failed_partial_seal_sha256=(
            load_r8u_r7h_sealed_history()[
                "batch16_failed_partial_seal_sha256"
            ]
        ),
        consumed_r7f_continuation_receipt_sha256=(
            CONSUMED_R7F_AUTHORITY_SHA256["combined_submission"]
        ),
        consumed_task17_accounting_receipt_sha256=str(hashes["task_17"]),
        consumed_task18_accounting_receipt_sha256=str(hashes["task_18"]),
        consumed_task19_accounting_receipt_sha256=str(hashes["task_19"]),
        consumed_finalizer_accounting_receipt_sha256=str(hashes["finalizer"]),
        consumed_r7f_evidence_sha256=core.sha256_file(
            CONSUMED_EVIDENCE_PATH
        ),
        topology_authority_sha256=core.sha256_file(TOPOLOGY_AUTHORITY_PATH),
        capacity_receipt_sha256=core.sha256_file(CAPACITY_RECEIPT_PATH),
        scheduler_account_authority_sha256=core.sha256_file(
            SCHEDULER_ACCOUNT_PATH
        ),
        probe_terminal_receipt_sha256=core.sha256_file(PROBE_TERMINAL_PATH),
        continuation_claim_sha256=core.sha256_file(CONTINUATION_CLAIM_PATH),
        array_submission_receipt_sha256=core.sha256_file(
            ARRAY_SUBMISSION_PATH
        ),
        finalizer_submission_receipt_sha256=core.sha256_file(
            FINALIZER_SUBMISSION_PATH
        ),
        continuation_submission_receipt_sha256=core.sha256_file(
            CONTINUATION_SUBMISSION_PATH
        ),
    )


def _finalizer_authority_payload(
    authority: finalizer.R8UR7HImplementationAuthority,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7h_finalizer_authority_v1",
        **{
            field: getattr(authority, field)
            for field in authority.__dataclass_fields__
        },
    }
    value["finalized_prefix_receipt_sha256"] = list(
        authority.finalized_prefix_receipt_sha256
    )
    return value


def run_r8u_r7h_continuation_finalizer(
    *,
    qstat_runner: Callable[..., Any] = subprocess.run,
    process_runner: Callable[..., Any] = subprocess.run,
) -> Mapping[str, Any]:
    """Finalize only the immutable prefix plus the three fresh R7H receipts."""

    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
        or str(os.environ.get("NSLOTS", "")) != "4"
    ):
        _fail("R7H_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    validate_r8u_r7h_continuation_worker_submission(
        current_job_id=job_id, role="finalizer", qstat_runner=qstat_runner
    )
    run, evidence, _topology, _capacity_value, account = _load_capacity_chain(
        scheduler_job_identity=job_id, replay_tail_artifacts=False
    )
    continuation = _validate_continuation_chain(run=run, account=account)
    references = _worker_live_references(
        account=account,
        continuation=continuation,
        qstat_runner=qstat_runner,
        process_runner=process_runner,
    )
    validate_r8u_r7h_extraction_cache_topology(
        active_job_references=references["active_job_references"],
        active_process_references=references["active_process_references"],
    )
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")[
            "final_receipt"
        ]
        for index in range(run.requirements.batch_count)
    ]
    # The historical consumed finalizer can have created the directory, but
    # it never created a valid aggregate.  Reuse only that canonical parent;
    # every scientific output below remains individually no-clobber.
    output_root = FINALIZER_AGGREGATE_PATH.parent
    if os.path.lexists(FINALIZER_AGGREGATE_PATH):
        _fail("R7H_COHORT_OUTPUT_ALREADY_EXISTS")
    if os.path.lexists(output_root):
        _validate_private_directory(
            output_root, code="R7H_COHORT_OUTPUT_ROOT_INVALID"
        )
    else:
        _ensure_private_directory(output_root)
    implementation_commit = _current_r8u_r7h_implementation_commit()
    authority = _r7h_finalizer_authority(
        implementation_commit=implementation_commit, evidence=evidence
    )
    authority_payload = _finalizer_authority_payload(authority)
    authority_sha = _write_private_json(
        FINALIZER_AUTHORITY_PATH,
        authority_payload,
        code="R7H_FINALIZER_AUTHORITY_PUBLICATION_INVALID",
    )
    try:
        summary = finalizer.finalize_receipts(
            receipts,
            expected_governing_commit=SCIENTIFIC_COMMIT,
            expected_attempt_id=ATTEMPT_ID,
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
            r8u_r7h_implementation_authority=authority,
        )
        finalizer.validate_closed_final_summary(summary)
    except Exception as exc:
        code = str(getattr(exc, "code", "R7H_CONTINUATION_FINALIZATION_INVALID"))
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R7H_CONTINUATION_FINALIZATION_INVALID"
        raise R7HContinuationError(code) from exc
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
        or summary.get("all_scientific_authority_bindings_identical") is not True
        or summary.get("implementation_authority_epoch_count") != 4
        or summary.get("r8u_r7h_implementation_commit")
        != implementation_commit
        or summary.get("r8u_r7h_continuation_authority_sha256")
        != authority_sha
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R7H_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(FINALIZER_AGGREGATE_PATH, summary)
    try:
        payload = _read_private_bytes(
            FINALIZER_AGGREGATE_PATH,
            code="R7H_FINALIZER_AGGREGATE_READBACK_INVALID",
        )
        reopened = _strict_json(
            payload, code="R7H_FINALIZER_AGGREGATE_READBACK_INVALID"
        )
        expected_payload = (
            json.dumps(reopened, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        finalizer.validate_closed_final_summary(reopened)
    except R7HContinuationError:
        raise
    except Exception as exc:
        raise R7HContinuationError(
            "R7H_FINALIZER_AGGREGATE_READBACK_INVALID"
        ) from exc
    if payload != expected_payload or not _exact(reopened, summary):
        _fail("R7H_FINALIZER_AGGREGATE_READBACK_INVALID")
    return summary
