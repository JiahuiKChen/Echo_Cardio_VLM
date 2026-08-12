#!/usr/bin/env python3
"""Finalize the bounded Phase 1E-F D3 runtime-authority repair.

This module is intentionally control-plane only. It can validate or create one
post-commit environment receipt, but it has no source-listing, download,
scheduler, imaging, inference, modeling, or prediction capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# The SCC wrapper deliberately runs Python with ``-E -s`` and may use ``-I``
# in direct validation.  Import only from this resolved tracked scripts tree.
_TRACKED_SCRIPTS_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(_TRACKED_SCRIPTS_DIRECTORY))

from lvef_c3_execution_state import (
    DEFAULT_STATE_PATH,
    ExecutionState,
    ExecutionStateError,
    load_execution_state,
)


# Compatibility names for historical component tests; every value is derived
# from the canonical execution-state authority rather than independently set.
_TRACKED_STATE = load_execution_state(DEFAULT_STATE_PATH)
BRANCH = _TRACKED_STATE.branch
HISTORICAL_BASE = _TRACKED_STATE.historical_base_commit
LEGACY_D3_COMMIT = _TRACKED_STATE.logical_execution_governing_commit
PREPARATION_DIRECTORY = _TRACKED_STATE.preparation_sequence_id
PREPARATION_ENVIRONMENT = _TRACKED_STATE.preparation_environment_filename
PREPARATION_ENVIRONMENT_BYTES = _TRACKED_STATE.preparation_environment_bytes


WORKTREE = Path("/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask")
AUDIT_ROOT = Path("/restricted/projectnb/mimicecho/audits")
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
LEXICAL_ECHOPRIME_PYTHON = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
)
ECHOPRIME_PYTHON_SHA256 = "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
CRC32C_PYTHON_SHA256 = "52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5"
CRC32C_WORKER_SHA256 = "4a7d49d36920aeede6cb80e734a2f3971e4dcd283130bb63385de77d1ba13a55"
CAPTURE_SCRIPT_SHA256 = "b9495060cf281ef0e25d04964eff94df2417de0f8eda236e6031106dd51b2bfc"
DIAGNOSTIC_RE = re.compile(r"^lvef_multitask_phase1ef_d3_environment_diagnostic_[A-Za-z0-9]{8}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9_@%+,./:=-]+$")
MANIFEST_SCOPE_KEYS = frozenset(
    {
        "cloud_access", "object_listing", "object_body_transfer",
        "scheduler_submission", "dicom_processing", "echoprime_inference",
        "embedding_creation", "modeling", "predictions", "confirmatory_access",
    }
)
MANIFEST_AUTHORITY_ROLES = frozenset(
    {
        "capacity_parser", "capacity_wrapper", "environment_preparer",
        "phase1ef_runbook", "safe_export_policy", "backup_recovery_policy",
        "tracked_attempt_dispatcher",
    }
)
MANIFEST_AUTHORITY_ENTRY_KEYS = frozenset(
    {
        "logical_role", "canonical_absolute_path", "sha256", "size_bytes",
        "required_file_type", "required_owner_policy", "required_mode_policy",
        "symlink_permitted",
    }
)
ENVIRONMENT_RECEIPT_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "governing_commit",
        "captured_at_utc", "source_environment_receipt_sha256",
        "python_executable_sha256", "python_version", "torch_version",
        "torchvision_version", "cuda_version", "cudnn_version",
        "crc32c_runtime_source", "crc32c_python_executable_sha256",
        "crc32c_python_version", "crc32c_worker_sha256",
        "crc32c_worker_protocol_version", "google_crc32c_version",
        "google_crc32c_implementation", "google_crc32c_distribution_sha256",
        "google_crc32c_distribution_file_count", "google_crc32c_known_vector_base64",
        "package_inventory_sha256", "package_count", "package_inventory",
        "operating_system", "gpu_execution_performed", "cloud_request_performed",
        "dicom_body_read", "model_fitted", "prediction_generated",
        "confirmatory_performance_accessed",
    }
)

ATTEMPT_004_EXECUTION_COUNT = _TRACKED_STATE.attempt_004_execution_count
ATTEMPT_ROOTS = tuple(
    AUDIT_ROOT / f"{_TRACKED_STATE.execution_attempt_namespace}_{number:03d}"
    for number in range(1, _TRACKED_STATE.next_unused_execution_attempt + 1)
)
PRODUCTION_ATTEMPT_006 = (
    PRODUCTION_ROOT / "attempts" / _TRACKED_STATE.next_unused_production_attempt_id
)
FROZEN_RESTRICTED_CAPACITY = (
    AUDIT_ROOT
    / _TRACKED_STATE.logical_execution_attempt_id
    / "restricted"
    / "capacity"
    / "post_reallocation_capacity.restricted.json"
)
FROZEN_AGGREGATE_CAPACITY = (
    AUDIT_ROOT
    / _TRACKED_STATE.logical_execution_attempt_id
    / "aggregate"
    / "lvef_c3_post_reallocation_capacity.summary.json"
)

FROZEN_RESTRICTED_CAPACITY_BYTES = 10907
FROZEN_RESTRICTED_CAPACITY_SHA256 = "b3bae07dcd6958b7cdfdd827a0de565ae0972753e04955fd03cd137b2e30ab62"
FROZEN_AGGREGATE_CAPACITY_BYTES = 5399
FROZEN_AGGREGATE_CAPACITY_SHA256 = "4d15b0a1a80188ce95d31659eca50cf18c8b6a1ebdef4022a56975f6e3e4bd20"

SAFE_RESULT_COMMON_KEYS = (
    "D3_TRACKED_RECOVERY", "EXECUTION_STATE_VALIDATION", "SCC_COMMIT_EQUALITY",
    "PREPARATION_BINDING_VALIDATION", "ATTEMPT_STATE_INVARIANTS",
)
SAFE_CAPTURE_KEYS = SAFE_RESULT_COMMON_KEYS + (
    "CURRENT_ENVIRONMENT_POSTCOMMIT_VALIDATION", "CURRENT_ENVIRONMENT_RECEIPT_BYTES",
    "CURRENT_ENVIRONMENT_RECEIPT_SHA256", "LOGICAL_EXECUTION_ATTEMPT_RERUN",
    "NEXT_UNUSED_EXECUTION_ATTEMPT_CREATED", "NEXT_UNUSED_PRODUCTION_ATTEMPT_CREATED",
    "CLOUD_REQUESTS", "QSUB_SUBMISSIONS", "DICOM_BODIES_DOWNLOADED",
    "ECHOPRIME_INFERENCE", "GPU_EXECUTION", "MODEL_FITTING",
    "CONFIRMATORY_PERFORMANCE_ACCESSED",
)
SAFE_PREFLIGHT_KEYS = SAFE_RESULT_COMMON_KEYS + (
    "CURRENT_ENVIRONMENT_CAPTURE", "LOGICAL_EXECUTION_ATTEMPT_RERUN",
    "NEXT_UNUSED_EXECUTION_ATTEMPT_CREATED", "NEXT_UNUSED_PRODUCTION_ATTEMPT_CREATED",
    "CLOUD_REQUESTS", "QSUB_SUBMISSIONS", "DICOM_BODIES_DOWNLOADED",
    "ECHOPRIME_INFERENCE", "GPU_EXECUTION", "MODEL_FITTING",
    "CONFIRMATORY_PERFORMANCE_ACCESSED",
)


class D3RecoveryError(RuntimeError):
    """A closed, path-free D3 recovery failure."""

    def __init__(self, code: str):
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "UNCLASSIFIED_FAILURE"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RecoveryConfig:
    worktree: Path = WORKTREE
    audit_root: Path = AUDIT_ROOT
    production_root: Path = PRODUCTION_ROOT
    lexical_python: Path = LEXICAL_ECHOPRIME_PYTHON
    state_path: Path | None = None


@dataclass(frozen=True)
class ExecutionPaths:
    immutable_execution_attempt_roots: tuple[Path, ...]
    logical_execution_attempt_root: Path
    next_unused_execution_attempt_root: Path
    next_unused_production_attempt_root: Path
    frozen_restricted_capacity: Path
    frozen_aggregate_capacity: Path


@dataclass(frozen=True)
class PrivateAuthorities:
    diagnostic_root: Path
    prior_environment: Path
    crc32c_python: Path
    output_receipt: Path
    prior_environment_size: int
    prior_environment_sha256: str


RunCommand = Callable[[Sequence[str], Path | None, Mapping[str, str]], subprocess.CompletedProcess[str]]


def load_canonical_state(config: RecoveryConfig) -> ExecutionState:
    path = config.state_path or (
        config.worktree / "configs/lvef_c3_execution_state_v1.yaml"
    )
    try:
        return load_execution_state(path)
    except ExecutionStateError as exc:
        raise D3RecoveryError(exc.code) from exc


def derive_execution_paths(config: RecoveryConfig, state: ExecutionState) -> ExecutionPaths:
    immutable_roots = tuple(
        config.audit_root / f"{state.execution_attempt_namespace}_{number:03d}"
        for number in range(1, state.logical_execution_attempt + 1)
    )
    logical_root = config.audit_root / state.logical_execution_attempt_id
    if not immutable_roots or immutable_roots[-1] != logical_root:
        raise D3RecoveryError("EXECUTION_STATE_PATH_DERIVATION_INVALID")
    next_execution_root = config.audit_root / state.next_unused_execution_attempt_id
    next_production_root = (
        config.production_root / "attempts" / state.next_unused_production_attempt_id
    )
    return ExecutionPaths(
        immutable_execution_attempt_roots=immutable_roots,
        logical_execution_attempt_root=logical_root,
        next_unused_execution_attempt_root=next_execution_root,
        next_unused_production_attempt_root=next_production_root,
        frozen_restricted_capacity=(
            logical_root
            / "restricted"
            / "capacity"
            / "post_reallocation_capacity.restricted.json"
        ),
        frozen_aggregate_capacity=(
            logical_root
            / "aggregate"
            / "lvef_c3_post_reallocation_capacity.summary.json"
        ),
    )


def _open_nofollow(path: Path) -> int:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise D3RecoveryError("AUTHORITY_PATH_NOT_ABSOLUTE")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        parent = os.open(path.anchor, directory_flags)
    except OSError as exc:
        raise D3RecoveryError("AUTHORITY_OPEN_FAILED") from exc
    try:
        for component in path.parts[1:-1]:
            try:
                following = os.open(component, directory_flags, dir_fd=parent)
            except OSError as exc:
                raise D3RecoveryError("AUTHORITY_SYMLINK_COMPONENT") from exc
            os.close(parent)
            parent = following
        leaf_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            leaf_flags |= os.O_NOFOLLOW
        try:
            return os.open(path.name, leaf_flags, dir_fd=parent)
        except OSError as exc:
            raise D3RecoveryError("AUTHORITY_OPEN_FAILED") from exc
    finally:
        os.close(parent)


def sha256_file(path: Path) -> str:
    descriptor = _open_nofollow(path)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise D3RecoveryError("AUTHORITY_FILE_INVALID")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise D3RecoveryError("AUTHORITY_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def read_regular_bytes(path: Path) -> bytes:
    descriptor = _open_nofollow(path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise D3RecoveryError("AUTHORITY_FILE_INVALID")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise D3RecoveryError("AUTHORITY_CHANGED_DURING_READ")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return True


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise D3RecoveryError("AUTHORITY_MISSING") from exc


def require_nonsymlink_components(path: Path) -> None:
    if not path.is_absolute():
        raise D3RecoveryError("AUTHORITY_PATH_NOT_ABSOLUTE")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if stat.S_ISLNK(_lstat(current).st_mode):
            raise D3RecoveryError("AUTHORITY_SYMLINK_COMPONENT")


def require_private_directory(path: Path) -> None:
    require_nonsymlink_components(path)
    info = _lstat(path)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise D3RecoveryError("PRIVATE_DIRECTORY_INVALID")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o700, 0o2700}:
        raise D3RecoveryError("PRIVATE_DIRECTORY_PERMISSIONS_INVALID")


def require_search_root(path: Path) -> None:
    """Validate the fixed shared audit parent without calling it private."""
    require_nonsymlink_components(path)
    info = _lstat(path)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise D3RecoveryError("AUTHORITY_SEARCH_ROOT_INVALID")


def require_regular_file(
    path: Path, *, owner_only: bool, executable: bool = False,
    size: int | None = None, digest: str | None = None,
    owner_required: bool = True,
) -> None:
    require_nonsymlink_components(path)
    info = _lstat(path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise D3RecoveryError("AUTHORITY_FILE_INVALID")
    if owner_required and info.st_uid != os.geteuid():
        raise D3RecoveryError("AUTHORITY_OWNER_INVALID")
    mode = stat.S_IMODE(info.st_mode)
    if owner_only and mode != 0o600:
        raise D3RecoveryError("AUTHORITY_MODE_INVALID")
    if not owner_only and mode & 0o022:
        raise D3RecoveryError("AUTHORITY_MODE_INVALID")
    if executable and not mode & stat.S_IXUSR:
        raise D3RecoveryError("AUTHORITY_EXECUTABLE_INVALID")
    if size is not None and info.st_size != size:
        raise D3RecoveryError("AUTHORITY_SIZE_MISMATCH")
    if digest is not None and sha256_file(path) != digest:
        raise D3RecoveryError("AUTHORITY_HASH_MISMATCH")


def require_trusted_executable(path: Path, digest: str) -> None:
    require_regular_file(
        path,
        owner_only=False,
        executable=True,
        digest=digest,
        owner_required=False,
    )
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o7022 or mode & 0o500 != 0o500:
        raise D3RecoveryError("EXECUTABLE_MODE_INVALID")
    if info.st_uid in {0, os.geteuid()}:
        return
    pinned_external = Path("/share/pkg.8/python3/3.10.12/install/bin/python3.10")
    if path != pinned_external:
        raise D3RecoveryError("EXECUTABLE_OWNER_INVALID")
    parent = path.parent
    parent_info = _lstat(parent)
    if parent_info.st_uid != info.st_uid or os.access(path, os.W_OK) or os.access(parent, os.W_OK):
        raise D3RecoveryError("EXECUTABLE_EXTERNAL_AUTHORITY_INVALID")


def require_lexical_launcher(path: Path) -> None:
    if path != LEXICAL_ECHOPRIME_PYTHON:
        raise D3RecoveryError("LEXICAL_LAUNCHER_SUBSTITUTION")
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        info = _lstat(current)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise D3RecoveryError("LEXICAL_LAUNCHER_PARENT_INVALID")
    leaf = _lstat(path)
    if not (stat.S_ISREG(leaf.st_mode) or stat.S_ISLNK(leaf.st_mode)):
        raise D3RecoveryError("LEXICAL_LAUNCHER_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise D3RecoveryError("LEXICAL_LAUNCHER_UNRESOLVED") from exc
    require_trusted_executable(resolved, ECHOPRIME_PYTHON_SHA256)


def parse_literal_environment(path: Path, *, expected_size: int) -> dict[str, str]:
    require_regular_file(path, owner_only=True, size=expected_size)
    try:
        text = read_regular_bytes(path).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise D3RecoveryError("PRIVATE_ENVIRONMENT_UNREADABLE") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise D3RecoveryError("PRIVATE_ENVIRONMENT_SYNTAX_INVALID")
        name, value = line.split("=", 1)
        if not ENV_NAME_RE.fullmatch(name) or name in values:
            raise D3RecoveryError("PRIVATE_ENVIRONMENT_SCHEMA_INVALID")
        if not value or not ENV_VALUE_RE.fullmatch(value):
            raise D3RecoveryError("PRIVATE_ENVIRONMENT_VALUE_INVALID")
        values[name] = value
    return values


def discover_preparation(
    config: RecoveryConfig, state: ExecutionState
) -> tuple[Path, dict[str, str]]:
    """Discover one opaque preparation sequence without parsing its identifier."""
    preparation_root = config.audit_root / state.preparation_sequence_id
    require_private_directory(preparation_root)
    values = parse_literal_environment(
        preparation_root / state.preparation_environment_filename,
        expected_size=state.preparation_environment_bytes,
    )
    return preparation_root, values


def validate_preparation_binding(
    config: RecoveryConfig,
    state: ExecutionState,
    preparation_root: Path,
    values: Mapping[str, str],
) -> None:
    """Bind legacy manifest fields to logical attempt 004, not sequence 005."""
    expected_logical_execution_attempt_id = state.logical_execution_attempt_id
    required_bindings = {
        "WORKTREE": str(config.worktree),
        "EXPECTED_COMMIT": state.logical_execution_governing_commit,
        "ATTEMPT_ID": expected_logical_execution_attempt_id,
        "PHASE1EF_ATTEMPT_ID": expected_logical_execution_attempt_id,
        "PHASE1EF_EXECUTION_SCOPES_GRANTED": "0",
        "PYTHON": str(config.lexical_python),
    }
    if any(values.get(key) != value for key, value in required_bindings.items()):
        raise D3RecoveryError("PREPARATION_BINDING_INVALID")
    try:
        manifest = Path(values["PHASE1EF_AUTHORITY_MANIFEST"])
        manifest_digest = values["PHASE1EF_AUTHORITY_MANIFEST_SHA256"]
    except KeyError as exc:
        raise D3RecoveryError("PREPARATION_MANIFEST_BINDING_MISSING") from exc
    if manifest.parent != preparation_root or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise D3RecoveryError("PREPARATION_MANIFEST_BINDING_INVALID")
    require_regular_file(manifest, owner_only=True, digest=manifest_digest)
    manifest_value = load_strict_json(manifest)
    manifest_scopes = manifest_value.get("execution_scope_flags")
    manifest_authorities = manifest_value.get("authorities")
    expected_manifest_keys = {
        "schema_name", "schema_version", "attempt_id", "git_branch", "git_commit",
        "historical_base_commit", "created_utc", "execution_scopes_granted",
        "execution_scope_flags", "authorities",
    }
    if (
        set(manifest_value) != expected_manifest_keys
        or manifest_value.get("schema_name") != "lvef_c3_phase1ef_preexecution_authority_manifest"
        or manifest_value.get("schema_version") != 1
        or manifest_value.get("attempt_id") != expected_logical_execution_attempt_id
        or manifest_value.get("git_branch") != state.branch
        or manifest_value.get("git_commit") != state.logical_execution_governing_commit
        or manifest_value.get("historical_base_commit") != state.historical_base_commit
        or manifest_value.get("execution_scopes_granted") != 0
        or not isinstance(manifest_scopes, dict)
        or set(manifest_scopes) != MANIFEST_SCOPE_KEYS
        or any(value is not False for value in manifest_scopes.values())
        or not isinstance(manifest_authorities, list)
        or len(manifest_authorities) != 7
    ):
        raise D3RecoveryError("PREPARATION_MANIFEST_AUTHORITY_INVALID")
    roles: set[str] = set()
    for entry in manifest_authorities:
        if (
            not isinstance(entry, dict)
            or set(entry) != MANIFEST_AUTHORITY_ENTRY_KEYS
            or not isinstance(entry.get("logical_role"), str)
            or entry["logical_role"] in roles
            or entry.get("required_file_type") != "REGULAR_FILE"
            or entry.get("required_owner_policy") != "CURRENT_EFFECTIVE_USER"
            or entry.get("symlink_permitted") is not False
            or not isinstance(entry.get("size_bytes"), int)
            or isinstance(entry.get("size_bytes"), bool)
            or entry["size_bytes"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256")))
        ):
            raise D3RecoveryError("PREPARATION_MANIFEST_AUTHORITY_INVALID")
        roles.add(entry["logical_role"])
    if roles != MANIFEST_AUTHORITY_ROLES:
        raise D3RecoveryError("PREPARATION_MANIFEST_AUTHORITY_INVALID")


def resolve_private_authorities(
    config: RecoveryConfig,
    state: ExecutionState,
    paths: ExecutionPaths,
    expected_commit: str,
) -> PrivateAuthorities:
    require_search_root(config.audit_root)
    try:
        candidates = [candidate for candidate in config.audit_root.iterdir()
                      if DIAGNOSTIC_RE.fullmatch(candidate.name)]
    except OSError as exc:
        raise D3RecoveryError("DIAGNOSTIC_AUTHORITY_UNREADABLE") from exc
    if len(candidates) != 1:
        raise D3RecoveryError("DIAGNOSTIC_AUTHORITY_AMBIGUOUS")
    diagnostic_root = candidates[0]
    require_private_directory(diagnostic_root)
    protected_roots = (
        *paths.immutable_execution_attempt_roots,
        paths.next_unused_execution_attempt_root,
        paths.next_unused_production_attempt_root,
    )
    for attempt_root in protected_roots:
        if _is_within(diagnostic_root, attempt_root):
            raise D3RecoveryError("DIAGNOSTIC_AUTHORITY_IN_ATTEMPT_ROOT")

    preparation_root, values = discover_preparation(config, state)
    validate_preparation_binding(config, state, preparation_root, values)
    try:
        prior_environment = Path(values["PRIOR_ENVIRONMENT_RECEIPT"])
        crc32c_python = Path(values["CRC32C_PYTHON"])
    except KeyError as exc:
        raise D3RecoveryError("PRIVATE_AUTHORITY_ROLE_MISSING") from exc
    try:
        prior_size = int(values["PRIOR_ENVIRONMENT_EXPECTED_SIZE"])
        prior_digest = values["PRIOR_ENVIRONMENT_EXPECTED_SHA"]
    except (KeyError, ValueError) as exc:
        raise D3RecoveryError("PRIOR_ENVIRONMENT_BINDING_INVALID") from exc
    if prior_size < 1 or not re.fullmatch(r"[0-9a-f]{64}", prior_digest):
        raise D3RecoveryError("PRIOR_ENVIRONMENT_BINDING_INVALID")
    require_regular_file(prior_environment, owner_only=True, size=prior_size,
                         digest=prior_digest)
    require_regular_file(crc32c_python, owner_only=False, executable=True,
                         digest=CRC32C_PYTHON_SHA256)
    for value in (prior_environment, crc32c_python):
        if any(_is_within(value, attempt_root) for attempt_root in protected_roots):
            raise D3RecoveryError("PRIVATE_AUTHORITY_IN_ATTEMPT_ROOT")
    output = diagnostic_root / f"current_environment_{expected_commit}.restricted.json"
    return PrivateAuthorities(
        diagnostic_root, prior_environment, crc32c_python, output, prior_size, prior_digest
    )


def default_run_command(command: Sequence[str], cwd: Path | None,
                        environment: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), cwd=cwd, env=dict(environment), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def clean_environment() -> dict[str, str]:
    allowed = {"USER", "LOGNAME", "LANG", "LC_ALL"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update({"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                        "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def capture_environment() -> dict[str, str]:
    """Preserve only nonsecret runtime-discovery variables used by the pinned venv."""
    allowed = {
        "USER", "LOGNAME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "LIBRARY_PATH",
        "CPATH", "CUDA_HOME", "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "MODULEPATH",
        "MODULESHOME", "LOADEDMODULES", "LMOD_CMD", "LMOD_DIR", "LMOD_SYSTEM_DEFAULT_MODULES",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update({
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CUDA_VISIBLE_DEVICES": "",
    })
    return environment


def git(config: RecoveryConfig, runner: RunCommand, environment: Mapping[str, str],
        *arguments: str) -> str:
    result = runner(("/usr/bin/git", *arguments), config.worktree, environment)
    if result.returncode != 0:
        raise D3RecoveryError("GIT_AUTHORITY_FAILURE")
    return result.stdout.strip()


def validate_clean_status(status_text: str) -> None:
    allowed = {"?? .DS_Store", "?? docs/.DS_Store"}
    if any(line and line not in allowed for line in status_text.splitlines()):
        raise D3RecoveryError("TRACKED_WORKTREE_DIRTY")


def validate_attempts_and_capacity(
    state: ExecutionState, paths: ExecutionPaths
) -> None:
    if state.attempt_004_execution_count != 1:
        raise D3RecoveryError("LOGICAL_ATTEMPT_EXECUTION_AUTHORITY_INVALID")
    if len(paths.immutable_execution_attempt_roots) != state.logical_execution_attempt:
        raise D3RecoveryError("ATTEMPT_ROOT_CONFIGURATION_INVALID")
    for path in paths.immutable_execution_attempt_roots:
        require_nonsymlink_components(path)
        info = _lstat(path)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in {0o700, 0o2700}
        ):
            raise D3RecoveryError("IMMUTABLE_ATTEMPT_AUTHORITY_INVALID")
    for path in (
        paths.next_unused_execution_attempt_root,
        paths.next_unused_production_attempt_root,
    ):
        require_nonsymlink_components(path.parent)
        if os.path.lexists(path):
            raise D3RecoveryError("PROHIBITED_ATTEMPT_ROOT_PRESENT")
    require_regular_file(paths.frozen_restricted_capacity, owner_only=True,
                         size=FROZEN_RESTRICTED_CAPACITY_BYTES,
                         digest=FROZEN_RESTRICTED_CAPACITY_SHA256)
    require_regular_file(paths.frozen_aggregate_capacity, owner_only=False,
                         size=FROZEN_AGGREGATE_CAPACITY_BYTES,
                         digest=FROZEN_AGGREGATE_CAPACITY_SHA256)


def load_strict_json(path: Path) -> dict[str, object]:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise D3RecoveryError("RECEIPT_DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(
            read_regular_bytes(path).decode("utf-8"), object_pairs_hook=reject_duplicate
        )
    except D3RecoveryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise D3RecoveryError("RECEIPT_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    return value


def validate_environment_receipt_schema(value: Mapping[str, Any]) -> None:
    if set(value) != ENVIRONMENT_RECEIPT_KEYS:
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    strings = (
        "python_version", "torch_version", "torchvision_version", "cuda_version",
        "cudnn_version", "crc32c_python_version", "google_crc32c_version",
        "operating_system",
    )
    digests = (
        "source_environment_receipt_sha256", "python_executable_sha256",
        "crc32c_python_executable_sha256", "crc32c_worker_sha256",
        "google_crc32c_distribution_sha256", "package_inventory_sha256",
    )
    if (
        value.get("schema_version") != 3
        or value.get("artifact_type") != "lvef_c3_production_environment_authority_v3"
        or value.get("status") != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit")))
        or any(not isinstance(value.get(key), str) or not value[key] for key in strings)
        or any(not re.fullmatch(r"[0-9a-f]{64}", str(value.get(key))) for key in digests)
        or value.get("crc32c_runtime_source") != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or value.get("crc32c_worker_protocol_version") != 1
        or value.get("google_crc32c_implementation") != "c"
        or value.get("google_crc32c_known_vector_base64") != "4waSgw=="
    ):
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    file_count = value.get("google_crc32c_distribution_file_count")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 1:
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    try:
        captured = datetime.fromisoformat(str(value.get("captured_at_utc")))
    except ValueError as exc:
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID") from exc
    if captured.tzinfo is None or captured.utcoffset() != timezone.utc.utcoffset(captured):
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    packages = value.get("package_inventory")
    if not isinstance(packages, list) or not packages:
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    normalized: list[dict[str, str]] = []
    for item in packages:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "version"}
            or not isinstance(item["name"], str)
            or not item["name"]
            or not isinstance(item["version"], str)
            or not item["version"]
        ):
            raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
        normalized.append({"name": item["name"], "version": item["version"]})
    expected_order = sorted(normalized, key=lambda item: (item["name"].casefold(), item["name"]))
    if normalized != expected_order or len({item["name"].casefold() for item in normalized}) != len(normalized):
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")
    package_digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        isinstance(value.get("package_count"), bool)
        or value.get("package_count") != len(normalized)
        or value.get("package_inventory_sha256") != package_digest
    ):
        raise D3RecoveryError("RECEIPT_SCHEMA_INVALID")


def validate_current_receipt(
    path: Path,
    expected_commit: str,
    *,
    prior_environment_sha256: str | None = None,
) -> tuple[int, str]:
    require_regular_file(path, owner_only=True)
    value = load_strict_json(path)
    validate_environment_receipt_schema(value)
    if value.get("governing_commit") != expected_commit:
        raise D3RecoveryError("RECEIPT_COMMIT_MISMATCH")
    if value.get("status") != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION":
        raise D3RecoveryError("RECEIPT_STATUS_INVALID")
    for key in ("gpu_execution_performed", "cloud_request_performed", "dicom_body_read",
                "model_fitted", "prediction_generated", "confirmatory_performance_accessed"):
        if value.get(key) is not False:
            raise D3RecoveryError("RECEIPT_ACTIVITY_FLAG_INVALID")
    if value.get("python_executable_sha256") != ECHOPRIME_PYTHON_SHA256:
        raise D3RecoveryError("RECEIPT_PYTHON_AUTHORITY_INVALID")
    if value.get("crc32c_python_executable_sha256") != CRC32C_PYTHON_SHA256:
        raise D3RecoveryError("RECEIPT_CRC32C_AUTHORITY_INVALID")
    if value.get("crc32c_worker_sha256") != CRC32C_WORKER_SHA256:
        raise D3RecoveryError("RECEIPT_CRC32C_WORKER_INVALID")
    if (
        prior_environment_sha256 is not None
        and value.get("source_environment_receipt_sha256") != prior_environment_sha256
    ):
        raise D3RecoveryError("RECEIPT_PRIOR_ENVIRONMENT_INVALID")
    return path.stat().st_size, sha256_file(path)


def _safe_common_result() -> dict[str, str]:
    return {
        "D3_TRACKED_RECOVERY": "PASS",
        "EXECUTION_STATE_VALIDATION": "PASS",
        "SCC_COMMIT_EQUALITY": "PASS",
        "PREPARATION_BINDING_VALIDATION": "PASS_LOGICAL_EXECUTION_ATTEMPT",
        "ATTEMPT_STATE_INVARIANTS": "PASS",
    }


def run_recovery(
    mode: str,
    *,
    config: RecoveryConfig = RecoveryConfig(),
    runner: RunCommand = default_run_command,
) -> dict[str, str]:
    if mode not in {"--preflight-only", "--capture-current-environment"}:
        raise D3RecoveryError("DISPATCH_MODE_INVALID")
    state = load_canonical_state(config)
    scope = mode.removeprefix("--").replace("-", "_")
    if not state.permits(scope):
        raise D3RecoveryError("EXECUTION_SCOPE_NOT_PERMITTED")
    paths = derive_execution_paths(config, state)
    environment = clean_environment()
    if git(config, runner, environment, "branch", "--show-current") != state.branch:
        raise D3RecoveryError("BRANCH_AUTHORITY_MISMATCH")
    expected_commit = git(config, runner, environment, "rev-parse", "HEAD")
    if not COMMIT_RE.fullmatch(expected_commit):
        raise D3RecoveryError("EXPECTED_COMMIT_INVALID")
    if expected_commit == state.starting_authority_commit:
        raise D3RecoveryError("STARTING_AUTHORITY_REQUIRES_FAST_FORWARD")
    authorities = resolve_private_authorities(config, state, paths, expected_commit)
    validate_attempts_and_capacity(state, paths)
    capture_script = config.worktree / "scripts" / "capture_lvef_c3_production_environment.py"
    require_regular_file(capture_script, owner_only=False, digest=CAPTURE_SCRIPT_SHA256)
    require_lexical_launcher(config.lexical_python)
    if os.path.lexists(authorities.output_receipt):
        # An invalid no-clobber candidate must block before Git is fetched or
        # advanced. A valid candidate is revalidated again after migration.
        validate_current_receipt(
            authorities.output_receipt,
            expected_commit,
            prior_environment_sha256=authorities.prior_environment_sha256,
        )

    validate_clean_status(git(config, runner, environment, "status", "--porcelain=v1",
                              "--untracked-files=all"))
    git(
        config, runner, environment, "merge-base", "--is-ancestor",
        state.starting_authority_commit, expected_commit,
    )
    git(
        config, runner, environment, "merge-base", "--is-ancestor",
        state.historical_base_commit, expected_commit,
    )
    remote = git(
        config, runner, environment, "rev-parse", f"refs/remotes/origin/{state.branch}"
    )
    if remote != expected_commit:
        raise D3RecoveryError("ORIGIN_COMMIT_MISMATCH")

    if mode == "--preflight-only":
        result = {
            **_safe_common_result(),
            "CURRENT_ENVIRONMENT_CAPTURE": "NOT_PERFORMED_PREFLIGHT_ONLY",
            "LOGICAL_EXECUTION_ATTEMPT_RERUN": "NO",
            "NEXT_UNUSED_EXECUTION_ATTEMPT_CREATED": "NO",
            "NEXT_UNUSED_PRODUCTION_ATTEMPT_CREATED": "NO",
            "CLOUD_REQUESTS": "0",
            "QSUB_SUBMISSIONS": "0",
            "DICOM_BODIES_DOWNLOADED": "NO",
            "ECHOPRIME_INFERENCE": "NO",
            "GPU_EXECUTION": "NO",
            "MODEL_FITTING": "NO",
            "CONFIRMATORY_PERFORMANCE_ACCESSED": "NO",
        }
        if tuple(result) != SAFE_PREFLIGHT_KEYS:
            raise D3RecoveryError("SAFE_OUTPUT_SCHEMA_INVALID")
        return result

    receipt = authorities.output_receipt
    if not os.path.lexists(receipt):
        worker = config.worktree / "scripts" / "lvef_c3_crc32c_worker.py"
        require_regular_file(worker, owner_only=False, digest=CRC32C_WORKER_SHA256)
        command = (
            str(config.lexical_python), "-E", "-s", "-B", str(capture_script), "--prior-environment",
            str(authorities.prior_environment), "--governing-commit", expected_commit,
            "--checkout-root", str(config.worktree), "--crc32c-python",
            str(authorities.crc32c_python), "--crc32c-python-expected-sha256",
            CRC32C_PYTHON_SHA256, "--crc32c-worker", str(worker), "--output", str(receipt),
        )
        if runner(command, config.worktree, capture_environment()).returncode != 0:
            raise D3RecoveryError("CURRENT_ENVIRONMENT_CAPTURE_FAILED")

    validate_attempts_and_capacity(state, paths)
    require_lexical_launcher(config.lexical_python)
    if git(config, runner, environment, "rev-parse", "HEAD") != expected_commit:
        raise D3RecoveryError("FINAL_COMMIT_AUTHORITY_MISMATCH")
    validate_clean_status(git(config, runner, environment, "status", "--porcelain=v1",
                              "--untracked-files=all"))
    receipt_bytes, receipt_digest = validate_current_receipt(
        receipt,
        expected_commit,
        prior_environment_sha256=authorities.prior_environment_sha256,
    )
    result = {
        **_safe_common_result(),
        "CURRENT_ENVIRONMENT_POSTCOMMIT_VALIDATION": "PASS",
        "CURRENT_ENVIRONMENT_RECEIPT_BYTES": str(receipt_bytes),
        "CURRENT_ENVIRONMENT_RECEIPT_SHA256": receipt_digest,
        "LOGICAL_EXECUTION_ATTEMPT_RERUN": "NO",
        "NEXT_UNUSED_EXECUTION_ATTEMPT_CREATED": "NO",
        "NEXT_UNUSED_PRODUCTION_ATTEMPT_CREATED": "NO", "CLOUD_REQUESTS": "0",
        "QSUB_SUBMISSIONS": "0", "DICOM_BODIES_DOWNLOADED": "NO",
        "ECHOPRIME_INFERENCE": "NO", "GPU_EXECUTION": "NO", "MODEL_FITTING": "NO",
        "CONFIRMATORY_PERFORMANCE_ACCESSED": "NO",
    }
    if tuple(result) != SAFE_CAPTURE_KEYS:
        raise D3RecoveryError("SAFE_OUTPUT_SCHEMA_INVALID")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize bounded Phase 1E-F D3 authority")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--preflight-only",
        dest="mode",
        action="store_const",
        const="--preflight-only",
    )
    modes.add_argument(
        "--capture-current-environment",
        dest="mode",
        action="store_const",
        const="--capture-current-environment",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    config: RecoveryConfig = RecoveryConfig(),
    runner: RunCommand = default_run_command,
) -> int:
    args = parse_args(argv)
    try:
        result = run_recovery(args.mode, config=config, runner=runner)
    except D3RecoveryError as exc:
        print("D3_TRACKED_RECOVERY=FAILED")
        print(f"D3_TRACKED_RECOVERY_ERROR={exc.code}")
        return 65
    except BaseException:
        print("D3_TRACKED_RECOVERY=FAILED")
        print("D3_TRACKED_RECOVERY_ERROR=UNEXPECTED_RUNTIME_FAILURE")
        return 70
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
