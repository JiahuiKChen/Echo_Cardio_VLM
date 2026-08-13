#!/usr/bin/env python3
"""Closed owner-private authority for one future exact-five C3 canary.

The loader is deliberately read-only.  It validates already-created private
authority files but cannot create a manifest, authorize the canonical state,
submit a job, read a DICOM body, or invoke a cloud or GPU operation.  A valid
packet is necessary but explicitly not sufficient: the caller must separately
require a future canonical execution-state scope permitting canary execution.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from dataclasses import dataclass, field
from typing import Any, Final


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent

import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler_contract
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


BRANCH: Final = "codex/lvef-multitask-revalidation"
PRIVATE_ROOT: Final = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2/owner_private/"
    "exact_five_canary"
)
FIXED_PATH: Final = PRIVATE_ROOT / "execution_authorization_v1.json"
CANARY_RUN_ROOT: Final = PRIVATE_ROOT / "canary_runs"
TRACKED_WORKTREE: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
)
PRODUCTION_CONTRACT_PATH: Final = TRACKED_WORKTREE / "configs/lvef_c3_orchestration_v2.yaml"
EXECUTION_STATE_PATH: Final = TRACKED_WORKTREE / "configs/lvef_c3_execution_state_v1.yaml"
STATE_MACHINE_PATH: Final = TRACKED_WORKTREE / "configs/lvef_c3_state_machine_v2.json"
RESUME_LEDGER_PATH: Final = TRACKED_WORKTREE / "configs/lvef_c3_resume_ledger_v2.json"
STAGE_WORKER_PATH: Final = TRACKED_WORKTREE / "scripts/lvef_c3_canary_stage_worker.py"
STAGE_LAUNCHER_PATH: Final = TRACKED_WORKTREE / "scripts/scc_run_lvef_c3_canary_stage.sh"

SCHEMA_VERSION: Final = 1
ARTIFACT_TYPE: Final = "lvef_c3_exact_five_canary_execution_authorization_v1"
STATUS: Final = "OWNER_AUTHORIZED_EXACT_FIVE_CANARY"
RUN_ID_RE: Final = re.compile(r"^lvef_c3_exact_five_canary_[a-z0-9]{8}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
PROJECT_RE: Final = re.compile(r"^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
UTC_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)

STAGE_IDS: Final = scheduler_contract.ORDERED_STAGE_IDS
HARD_SCOPE: Final = {
    "studies": 5,
    "subjects": 5,
    "split": "train",
    "max_objects": 750,
    "max_bytes": 5_000_000_000,
    "batch_id": "c3_batch_000",
}
SCHEDULER_SCOPE: Final = {
    "ordered_stage_ids": list(STAGE_IDS),
    "scheduler_submission_count": 5,
    "maximum_scheduler_submission_count": 5,
    "gpu_stage_count": 1,
    "stage_retry_count": 0,
    "array_expansion_permitted": False,
    "automatic_resubmission_permitted": False,
    "production_continuation": False,
}
AUTHORIZATION_SCOPES: Final = {
    "canonical_execution_state_execute_permission_required": True,
    "exact_declared_object_body_transfer": True,
    "dicom_header_pixel_decode_and_cine_extraction": True,
    "echoprime_encoder_only_inference": True,
    "finite_float32_512d_clip_validation": True,
    "study_mean_pooling": True,
    "canary_preservation": True,
    "aggregate_safe_canary_finalization": True,
    "undeclared_object_access": False,
    "cohort_expansion": False,
    "object_listing": False,
    "scheduler_resubmission": False,
    "production_continuation": False,
    "model_fitting": False,
    "endpoint_prediction": False,
    "confirmatory_performance_access": False,
    "cache_retirement": False,
    "raw_dicom_deletion": False,
}

BINDING_KEYS = frozenset({"path", "file_sha256"})
MANIFEST_BINDING_KEYS = frozenset({*BINDING_KEYS, "embedded_sha256"})
CANONICAL_BINDING_KEYS = frozenset({*BINDING_KEYS, "canonical_sha256"})
PRESELECTION_BINDING_KEYS = frozenset(
    {*BINDING_KEYS, "semantic_sha256"}
)
SCHEDULER_TOOL_IDENTITY_KEYS = frozenset(
    {"path", "file_sha256", "size_bytes", "device_id", "inode"}
)
PRESELECTION_SCHEDULER_TOOL_KEYS = frozenset({"qsub", "qstat"})
STAGE_GRANT_KEYS = frozenset({"stage_id", "path", "file_sha256", "authorized"})
GCLOUD_KEYS = frozenset({"binary_path", "resolution_receipt_path", "cloudsdk_config_path"})
CRC32C_KEYS = frozenset({"python_path", "worker_path"})
REQUESTER_KEYS = frozenset({"billing_environment_variable", "billing_project"})
PRESELECTION_SCHEMA_VERSION: Final = 1
PRESELECTION_ARTIFACT_TYPE: Final = (
    "lvef_c3_exact_five_canary_preselection_authority_v1"
)
PRESELECTION_STATUS: Final = "OWNER_AUTHORIZED_EXACT_FIVE_CANARY_SCOPE"
PRESELECTION_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "created_at_utc",
        "governing_commit",
        "run_id",
        "owner_authorized",
        "hard_scope",
        "scheduler_scope",
        "scheduler_tools",
        "authorization_scopes",
        "preselection_authority_sha256",
    }
)


@dataclass(frozen=True)
class CanaryExecutionAuthorityPaths:
    """All filesystem authorities consumed by the packet validator.

    Production callers use the frozen defaults below.  Synthetic acceptance
    supplies a complete sandbox instance; it does not patch validator logic or
    relax any semantic, ownership, mode, hash, or no-follow check.
    """

    private_root: Path = field(default_factory=lambda: PRIVATE_ROOT)
    fixed_path: Path = field(default_factory=lambda: FIXED_PATH)
    canary_run_root: Path = field(default_factory=lambda: CANARY_RUN_ROOT)
    tracked_worktree: Path = field(default_factory=lambda: TRACKED_WORKTREE)
    production_contract_path: Path = field(
        default_factory=lambda: PRODUCTION_CONTRACT_PATH
    )
    execution_state_path: Path = field(default_factory=lambda: EXECUTION_STATE_PATH)
    state_machine_path: Path = field(default_factory=lambda: STATE_MACHINE_PATH)
    resume_ledger_path: Path = field(default_factory=lambda: RESUME_LEDGER_PATH)
    stage_worker_path: Path = field(default_factory=lambda: STAGE_WORKER_PATH)
    stage_launcher_path: Path = field(default_factory=lambda: STAGE_LAUNCHER_PATH)

PACKET_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "created_at_utc",
        "governing_commit", "branch",
        "run_id", "attempt_id", "output_root", "preselection_authority",
        "manifest", "batch_plan",
        "scheduler_plan", "production_contract", "environment_receipt", "checkpoint",
        "runtime_authority", "hard_scope", "scheduler", "stage_authorizations",
        "body_transfer_authorization", "launch_authority_sha256", "gcloud", "crc32c",
        "owner_authorized", "authorization_scopes", "qsub", "stage_worker",
        "stage_launcher", "requester_pays", "authorization_sha256",
    }
)


class CanaryExecutionAuthorityError(ValueError):
    """A fixed-code, aggregate-safe authorization validation failure."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_EXECUTION_AUTHORITY_INVALID"
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise CanaryExecutionAuthorityError(code)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    result: MutableMapping[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CANARY_EXECUTION_AUTHORITY_DUPLICATE_KEY")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_AUTHORITY_JSON_INVALID"
        ) from exc


def calculate_authorization_sha256(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("authorization_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def serialize_authorization(value: Mapping[str, Any]) -> bytes:
    """Return the sole permitted on-disk representation."""
    return canonical_json_bytes(value) + b"\n"


def calculate_preselection_authority_sha256(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("preselection_authority_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def serialize_preselection_authority(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def validate_preselection_authority_value(
    value: Any,
    *,
    expected_governing_commit: str | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PRESELECTION_KEYS:
        _fail("CANARY_PRESELECTION_AUTHORITY_SCHEMA_NOT_CLOSED")
    governing_commit = value.get("governing_commit")
    run_id = value.get("run_id")
    semantic_sha = value.get("preselection_authority_sha256")
    scheduler_tools = value.get("scheduler_tools")
    if (
        value.get("schema_version") != PRESELECTION_SCHEMA_VERSION
        or value.get("artifact_type") != PRESELECTION_ARTIFACT_TYPE
        or value.get("status") != PRESELECTION_STATUS
        or value.get("owner_authorized") is not True
        or not isinstance(governing_commit, str)
        or COMMIT_RE.fullmatch(governing_commit) is None
        or not isinstance(run_id, str)
        or RUN_ID_RE.fullmatch(run_id) is None
        or value.get("hard_scope") != HARD_SCOPE
        or value.get("scheduler_scope") != SCHEDULER_SCOPE
        or not isinstance(scheduler_tools, Mapping)
        or set(scheduler_tools) != PRESELECTION_SCHEDULER_TOOL_KEYS
        or value.get("authorization_scopes") != AUTHORIZATION_SCOPES
        or not isinstance(semantic_sha, str)
        or SHA256_RE.fullmatch(semantic_sha) is None
        or semantic_sha != calculate_preselection_authority_sha256(value)
    ):
        _fail("CANARY_PRESELECTION_AUTHORITY_INVALID")
    normalized_scheduler_tools: dict[str, dict[str, Any]] = {}
    scheduler_paths: set[Path] = set()
    scheduler_inodes: set[tuple[int, int]] = set()
    for role in sorted(PRESELECTION_SCHEDULER_TOOL_KEYS):
        identity = scheduler_tools.get(role)
        if (
            not isinstance(identity, Mapping)
            or set(identity) != SCHEDULER_TOOL_IDENTITY_KEYS
        ):
            _fail("CANARY_PRESELECTION_SCHEDULER_TOOL_INVALID")
        path = _path(
            identity.get("path"), "CANARY_PRESELECTION_SCHEDULER_TOOL_INVALID"
        )
        digest = _sha(
            identity.get("file_sha256"),
            "CANARY_PRESELECTION_SCHEDULER_TOOL_INVALID",
        )
        size_bytes = identity.get("size_bytes")
        device_id = identity.get("device_id")
        inode = identity.get("inode")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes <= 0
            or not isinstance(device_id, int)
            or isinstance(device_id, bool)
            or device_id < 0
            or not isinstance(inode, int)
            or isinstance(inode, bool)
            or inode <= 0
            or path in scheduler_paths
            or (device_id, inode) in scheduler_inodes
        ):
            _fail("CANARY_PRESELECTION_SCHEDULER_TOOL_INVALID")
        scheduler_paths.add(path)
        scheduler_inodes.add((device_id, inode))
        normalized_scheduler_tools[role] = {
            "path": str(path),
            "file_sha256": digest,
            "size_bytes": size_bytes,
            "device_id": device_id,
            "inode": inode,
        }
    _validate_created_at_utc(value.get("created_at_utc"))
    if (
        expected_governing_commit is not None
        and governing_commit != expected_governing_commit
    ):
        _fail("CANARY_PRESELECTION_GOVERNING_COMMIT_MISMATCH")
    if expected_run_id is not None and run_id != expected_run_id:
        _fail("CANARY_PRESELECTION_RUN_ID_MISMATCH")
    normalized = json.loads(canonical_json_bytes(value).decode("ascii"))
    normalized["scheduler_tools"] = normalized_scheduler_tools
    return normalized


def build_preselection_authority(
    *,
    governing_commit: str,
    run_id: str,
    created_at_utc: str,
    scheduler_tools: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": PRESELECTION_SCHEMA_VERSION,
        "artifact_type": PRESELECTION_ARTIFACT_TYPE,
        "status": PRESELECTION_STATUS,
        "created_at_utc": created_at_utc,
        "governing_commit": governing_commit,
        "run_id": run_id,
        "owner_authorized": True,
        "hard_scope": dict(HARD_SCOPE),
        "scheduler_scope": dict(SCHEDULER_SCOPE),
        "scheduler_tools": {
            str(role): dict(identity)
            for role, identity in scheduler_tools.items()
        },
        "authorization_scopes": dict(AUTHORIZATION_SCOPES),
    }
    value["preselection_authority_sha256"] = (
        calculate_preselection_authority_sha256(value)
    )
    return validate_preselection_authority_value(
        value,
        expected_governing_commit=governing_commit,
        expected_run_id=run_id,
    )


def _sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _mapping(value: Any, keys: frozenset[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(code)
    return value


def _path(value: Any, code: str) -> Path:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or value.startswith("//")
    ):
        _fail(code)
    path = Path(value)
    if (
        not path.is_absolute()
        or path == Path(path.anchor)
        or ".." in path.parts
        or str(path) != value
        or path != Path(os.path.normpath(value))
    ):
        _fail(code)
    return path


def _validate_authority_paths(
    value: CanaryExecutionAuthorityPaths,
) -> CanaryExecutionAuthorityPaths:
    if not isinstance(value, CanaryExecutionAuthorityPaths):
        _fail("CANARY_EXECUTION_AUTHORITY_PATHS_INVALID")
    declared = {
        "private_root": value.private_root,
        "fixed_path": value.fixed_path,
        "canary_run_root": value.canary_run_root,
        "tracked_worktree": value.tracked_worktree,
        "production_contract_path": value.production_contract_path,
        "execution_state_path": value.execution_state_path,
        "state_machine_path": value.state_machine_path,
        "resume_ledger_path": value.resume_ledger_path,
        "stage_worker_path": value.stage_worker_path,
        "stage_launcher_path": value.stage_launcher_path,
    }
    if any(
        not isinstance(path, Path)
        or not path.is_absolute()
        or path != Path(os.path.normpath(str(path)))
        or ".." in path.parts
        or path == Path(path.anchor)
        for path in declared.values()
    ):
        _fail("CANARY_EXECUTION_AUTHORITY_PATHS_INVALID")
    expected = {
        "fixed_path": value.private_root / "execution_authorization_v1.json",
        "canary_run_root": value.private_root / "canary_runs",
        "production_contract_path": value.tracked_worktree
        / "configs/lvef_c3_orchestration_v2.yaml",
        "execution_state_path": value.tracked_worktree
        / "configs/lvef_c3_execution_state_v1.yaml",
        "state_machine_path": value.tracked_worktree
        / "configs/lvef_c3_state_machine_v2.json",
        "resume_ledger_path": value.tracked_worktree
        / "configs/lvef_c3_resume_ledger_v2.json",
        "stage_worker_path": value.tracked_worktree
        / "scripts/lvef_c3_canary_stage_worker.py",
        "stage_launcher_path": value.tracked_worktree
        / "scripts/scc_run_lvef_c3_canary_stage.sh",
    }
    if (
        value.private_root == value.tracked_worktree
        or value.private_root.is_relative_to(value.tracked_worktree)
        or value.tracked_worktree.is_relative_to(value.private_root)
        or any(
            declared[name] != path for name, path in expected.items()
        )
    ):
        _fail("CANARY_EXECUTION_AUTHORITY_PATHS_INVALID")
    return value


def _validate_created_at_utc(value: Any) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail("CANARY_EXECUTION_CREATED_AT_UTC_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("CANARY_EXECUTION_CREATED_AT_UTC_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("CANARY_EXECUTION_CREATED_AT_UTC_INVALID")
    return value


def _require_nonsymlink_ancestors(path: Path) -> None:
    cursor = Path(path.anchor)
    for component in path.parts[1:-1]:
        cursor /= component
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise CanaryExecutionAuthorityError(
                "CANARY_EXECUTION_AUTHORITY_ANCESTOR_INVALID"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail("CANARY_EXECUTION_AUTHORITY_ANCESTOR_INVALID")


def _read_regular(path: Path, *, owner_private: bool, executable: bool = False) -> bytes:
    _require_nonsymlink_ancestors(path)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_AUTHORITY_FILE_MISSING"
        ) from exc
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail("CANARY_EXECUTION_AUTHORITY_FILE_NOT_REGULAR")
    if owner_private and (before.st_uid != os.geteuid() or mode != 0o600):
        _fail("CANARY_EXECUTION_AUTHORITY_FILE_NOT_PRIVATE")
    if not owner_private and (mode & 0o022):
        _fail("CANARY_EXECUTION_AUTHORITY_FILE_WRITABLE")
    if executable and not (mode & stat.S_IXUSR):
        _fail("CANARY_EXECUTION_AUTHORITY_EXECUTABLE_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_AUTHORITY_FILE_OPEN_FAILED"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _fail("CANARY_EXECUTION_AUTHORITY_FILE_CHANGED")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        payload = b"".join(blocks)
        final = os.fstat(descriptor)
        if (
            final.st_size != len(payload)
            or (final.st_size, final.st_mtime_ns, final.st_dev, final.st_ino)
            != (opened.st_size, opened.st_mtime_ns, opened.st_dev, opened.st_ino)
        ):
            _fail("CANARY_EXECUTION_AUTHORITY_FILE_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def _current_scheduler_tool_identity(path: Path) -> dict[str, Any]:
    """Read and identify one scheduler executable without following its leaf."""

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_PRESELECTION_SCHEDULER_TOOL_CHANGED"
        ) from exc
    payload = _read_regular(path, owner_private=False, executable=True)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_PRESELECTION_SCHEDULER_TOOL_CHANGED"
        ) from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(payload) != after.st_size
    ):
        _fail("CANARY_PRESELECTION_SCHEDULER_TOOL_CHANGED")
    return {
        "path": str(path),
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": after.st_size,
        "device_id": after.st_dev,
        "inode": after.st_ino,
    }


def _require_private_regular_metadata(path: Path) -> None:
    """Validate a private regular inode without reading credential bytes."""
    _require_nonsymlink_ancestors(path)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_PRIVATE_METADATA_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        _fail("CANARY_EXECUTION_PRIVATE_METADATA_INVALID")


def _require_private_directory(path: Path) -> None:
    """Require an existing owner-only directory with no symlink component."""
    _require_nonsymlink_ancestors(path / ".authority-child")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_PRIVATE_DIRECTORY_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("CANARY_EXECUTION_PRIVATE_DIRECTORY_INVALID")


def _require_private_tree_file(
    path: Path, expected_sha: str, *, private_root: Path
) -> bytes:
    try:
        relative = path.relative_to(private_root)
    except ValueError:
        _fail("CANARY_EXECUTION_PRIVATE_PATH_OUTSIDE_ROOT")
    if not relative.parts:
        _fail("CANARY_EXECUTION_PRIVATE_PATH_INVALID")
    cursor = private_root
    for component in relative.parts[:-1]:
        cursor /= component
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise CanaryExecutionAuthorityError(
                "CANARY_EXECUTION_PRIVATE_TREE_INVALID"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("CANARY_EXECUTION_PRIVATE_TREE_INVALID")
    payload = _read_regular(path, owner_private=True)
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        _fail("CANARY_EXECUTION_PRIVATE_FILE_HASH_MISMATCH")
    return payload


def _private_binding(
    value: Any, keys: frozenset[str], code: str, *, private_root: Path
) -> tuple[Mapping[str, Any], Path, bytes]:
    item = _mapping(value, keys, code)
    path = _path(item.get("path"), code)
    digest = _sha(item.get("file_sha256"), code)
    return item, path, _require_private_tree_file(
        path, digest, private_root=private_root
    )


def _public_binding(
    value: Any, *, fixed_path: Path | None = None, executable: bool = False
) -> tuple[Mapping[str, Any], Path, bytes]:
    item = _mapping(value, BINDING_KEYS, "CANARY_EXECUTION_PUBLIC_BINDING_INVALID")
    path = _path(item.get("path"), "CANARY_EXECUTION_PUBLIC_BINDING_INVALID")
    if fixed_path is not None and path != fixed_path:
        _fail("CANARY_EXECUTION_TRACKED_PATH_MISMATCH")
    expected = _sha(item.get("file_sha256"), "CANARY_EXECUTION_PUBLIC_BINDING_INVALID")
    payload = _read_regular(path, owner_private=False, executable=executable)
    if hashlib.sha256(payload).hexdigest() != expected:
        _fail("CANARY_EXECUTION_PUBLIC_FILE_HASH_MISMATCH")
    return item, path, payload


def _owner_private_binding(
    value: Any, code: str
) -> tuple[Mapping[str, Any], Path, bytes]:
    """Bind an existing owner-private authority that may live outside the canary tree."""
    item = _mapping(value, BINDING_KEYS, code)
    path = _path(item.get("path"), code)
    expected = _sha(item.get("file_sha256"), code)
    payload = _read_regular(path, owner_private=True)
    if hashlib.sha256(payload).hexdigest() != expected:
        _fail("CANARY_EXECUTION_OWNER_PRIVATE_FILE_HASH_MISMATCH")
    return item, path, payload


def _load_json(payload: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except CanaryExecutionAuthorityError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryExecutionAuthorityError(code) from exc
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _manifest_configuration(manifest: Mapping[str, Any]) -> dict[str, str]:
    rows = manifest["manifest"]["source_configuration_hashes"]
    result = {str(row["logical_name"]): str(row["sha256"]) for row in rows}
    required = {
        "execution_state", "production_contract", "source_metadata", "split_map",
        "historical_study_manifest", "prior_smoke_source_manifest",
        "prior_smoke_source_summary", "prior_smoke_source_safety",
        "prior_smoke_preservation_manifest",
        "checkpoint", "environment_receipt", "state_machine_schema",
        "resume_ledger_schema", "gcloud_resolution_receipt", "gcloud_executable",
        "crc32c_python_executable", "crc32c_worker", "crc32c_distribution",
    }
    if set(result) != required:
        _fail("CANARY_EXECUTION_MANIFEST_CONFIGURATION_SET_INVALID")
    return result


def load_and_validate_execution_authority(
    path: Path | None = None,
    *,
    expected_governing_commit: str | None = None,
    require_output_absent: bool = True,
    paths: CanaryExecutionAuthorityPaths | None = None,
) -> dict[str, Any]:
    """Load one canonical packet and return the normalized worker mapping."""

    authority_paths = _validate_authority_paths(
        paths if paths is not None else CanaryExecutionAuthorityPaths()
    )
    path = authority_paths.fixed_path if path is None else Path(path)
    if path != authority_paths.fixed_path:
        _fail("CANARY_EXECUTION_AUTHORITY_PATH_INVALID")
    payload = _read_regular(path, owner_private=True)
    packet = _load_json(payload, "CANARY_EXECUTION_AUTHORITY_JSON_INVALID")
    if set(packet) != PACKET_KEYS:
        _fail("CANARY_EXECUTION_AUTHORITY_SCHEMA_NOT_CLOSED")
    if payload != serialize_authorization(packet):
        _fail("CANARY_EXECUTION_AUTHORITY_SERIALIZATION_NOT_CANONICAL")
    semantic_sha = _sha(
        packet.get("authorization_sha256"),
        "CANARY_EXECUTION_AUTHORIZATION_SHA256_INVALID",
    )
    if semantic_sha != calculate_authorization_sha256(packet):
        _fail("CANARY_EXECUTION_AUTHORIZATION_SHA256_MISMATCH")
    governing_commit = packet.get("governing_commit")
    run_id = packet.get("run_id")
    attempt_id = packet.get("attempt_id")
    if (
        packet.get("schema_version") != SCHEMA_VERSION
        or packet.get("artifact_type") != ARTIFACT_TYPE
        or packet.get("status") != STATUS
        or packet.get("branch") != BRANCH
        or packet.get("owner_authorized") is not True
        or not isinstance(governing_commit, str)
        or COMMIT_RE.fullmatch(governing_commit) is None
        or not isinstance(run_id, str)
        or RUN_ID_RE.fullmatch(run_id) is None
        or attempt_id != run_id
    ):
        _fail("CANARY_EXECUTION_AUTHORITY_IDENTITY_INVALID")
    if expected_governing_commit is not None and governing_commit != expected_governing_commit:
        _fail("CANARY_EXECUTION_GOVERNING_COMMIT_MISMATCH")
    if packet.get("hard_scope") != HARD_SCOPE:
        _fail("CANARY_EXECUTION_HARD_SCOPE_INVALID")
    if packet.get("scheduler") != SCHEDULER_SCOPE:
        _fail("CANARY_EXECUTION_SCHEDULER_SCOPE_INVALID")
    if packet.get("authorization_scopes") != AUTHORIZATION_SCOPES:
        _fail("CANARY_EXECUTION_AUTHORIZATION_SCOPES_INVALID")
    _validate_created_at_utc(packet.get("created_at_utc"))

    _require_private_directory(authority_paths.private_root)
    _require_private_directory(authority_paths.canary_run_root)
    output_root = _path(packet.get("output_root"), "CANARY_EXECUTION_OUTPUT_ROOT_INVALID")
    if output_root != authority_paths.canary_run_root / run_id:
        _fail("CANARY_EXECUTION_OUTPUT_ROOT_INVALID")
    if require_output_absent:
        if os.path.lexists(output_root):
            _fail("CANARY_EXECUTION_OUTPUT_ROOT_COLLISION")
    else:
        _require_nonsymlink_ancestors(output_root)
        try:
            output_metadata = os.lstat(output_root)
        except OSError as exc:
            raise CanaryExecutionAuthorityError(
                "CANARY_EXECUTION_OUTPUT_ROOT_INVALID"
            ) from exc
        if (
            stat.S_ISLNK(output_metadata.st_mode)
            or not stat.S_ISDIR(output_metadata.st_mode)
            or output_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(output_metadata.st_mode) != 0o700
        ):
            _fail("CANARY_EXECUTION_OUTPUT_ROOT_INVALID")

    preselection_binding, _, preselection_payload = _private_binding(
        packet.get("preselection_authority"),
        PRESELECTION_BINDING_KEYS,
        "CANARY_EXECUTION_PRESELECTION_BINDING_INVALID",
        private_root=authority_paths.private_root,
    )
    preselection_value = _load_json(
        preselection_payload, "CANARY_PRESELECTION_AUTHORITY_JSON_INVALID"
    )
    if preselection_payload != serialize_preselection_authority(
        preselection_value
    ):
        _fail("CANARY_PRESELECTION_AUTHORITY_SERIALIZATION_NOT_CANONICAL")
    preselection = validate_preselection_authority_value(
        preselection_value,
        expected_governing_commit=governing_commit,
        expected_run_id=run_id,
    )
    preselection_sha = _sha(
        preselection_binding.get("semantic_sha256"),
        "CANARY_EXECUTION_PRESELECTION_BINDING_INVALID",
    )
    if (
        preselection_sha != preselection["preselection_authority_sha256"]
        or packet.get("created_at_utc") != preselection.get("created_at_utc")
    ):
        _fail("CANARY_EXECUTION_PRESELECTION_BINDING_MISMATCH")
    scheduler_tool_identities = preselection["scheduler_tools"]
    for role in sorted(PRESELECTION_SCHEDULER_TOOL_KEYS):
        expected_identity = scheduler_tool_identities[role]
        current_identity = _current_scheduler_tool_identity(
            Path(str(expected_identity["path"]))
        )
        if current_identity != expected_identity:
            _fail("CANARY_PRESELECTION_SCHEDULER_TOOL_CHANGED")

    manifest_binding, manifest_path, _ = _private_binding(
        packet.get("manifest"), MANIFEST_BINDING_KEYS,
        "CANARY_EXECUTION_MANIFEST_BINDING_INVALID",
        private_root=authority_paths.private_root,
    )
    manifest = manifest_contract.load_and_validate_manifest(
        manifest_path,
        expected_file_sha256=str(manifest_binding["file_sha256"]),
        expected_manifest_sha256=_sha(
            manifest_binding.get("embedded_sha256"),
            "CANARY_EXECUTION_MANIFEST_BINDING_INVALID",
        ),
        expected_source_authority_commit=governing_commit,
    )
    configuration = _manifest_configuration(manifest)

    plan_binding, _, plan_payload = _private_binding(
        packet.get("batch_plan"), CANONICAL_BINDING_KEYS,
        "CANARY_EXECUTION_BATCH_PLAN_BINDING_INVALID",
        private_root=authority_paths.private_root,
    )
    plan = _load_json(plan_payload, "CANARY_EXECUTION_BATCH_PLAN_JSON_INVALID")
    body = manifest["manifest"]
    requirements = core.PlanRequirements(
        release=str(body["source_release"]), selected_studies=5,
        selected_subjects=5,
        normalized_source_objects=int(body["expected_object_count"]),
        selected_source_bytes=int(body["expected_byte_total"]), batch_count=1,
        studies_per_full_batch=5, final_batch_studies=5,
        contract_id="lvef_multitask_c3_exact_five_canary_v1",
    )
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    if plan_sha != _sha(
        plan_binding.get("canonical_sha256"),
        "CANARY_EXECUTION_BATCH_PLAN_CANONICAL_SHA_INVALID",
    ):
        _fail("CANARY_EXECUTION_BATCH_PLAN_CANONICAL_SHA_MISMATCH")

    scheduler_binding, scheduler_path, _ = _private_binding(
        packet.get("scheduler_plan"), CANONICAL_BINDING_KEYS,
        "CANARY_EXECUTION_SCHEDULER_PLAN_BINDING_INVALID",
        private_root=authority_paths.private_root,
    )
    scheduler_plan = scheduler_contract.load_scheduler_plan(scheduler_path)
    scheduler_sha = scheduler_contract.validate_scheduler_plan(
        scheduler_plan,
        repository_root=authority_paths.tracked_worktree,
        require_bound_manifest=True,
    )
    if (
        scheduler_sha != _sha(
            scheduler_binding.get("canonical_sha256"),
            "CANARY_EXECUTION_SCHEDULER_PLAN_CANONICAL_SHA_INVALID",
        )
        or scheduler_plan.get("canary_manifest_sha256") != manifest["manifest_sha256"]
    ):
        _fail("CANARY_EXECUTION_MANIFEST_SCHEDULER_BINDING_MISMATCH")

    contract_binding, _, contract_payload = _public_binding(
        packet.get("production_contract"),
        fixed_path=authority_paths.production_contract_path,
    )
    # Semantic contract validation also revalidates its tracked control schemas.
    contract = core.load_orchestration_contract(
        authority_paths.production_contract_path
    )
    if configuration["production_contract"] != hashlib.sha256(contract_payload).hexdigest():
        _fail("CANARY_EXECUTION_CONTRACT_MANIFEST_BINDING_MISMATCH")
    if configuration["execution_state"] != hashlib.sha256(
        _read_regular(authority_paths.execution_state_path, owner_private=False)
    ).hexdigest():
        _fail("CANARY_EXECUTION_STATE_MANIFEST_BINDING_MISMATCH")

    environment_binding, environment_path, environment_payload = _owner_private_binding(
        packet.get("environment_receipt"),
        "CANARY_EXECUTION_ENVIRONMENT_BINDING_INVALID",
    )
    checkpoint_binding = _mapping(
        packet.get("checkpoint"), BINDING_KEYS,
        "CANARY_EXECUTION_CHECKPOINT_BINDING_INVALID",
    )
    checkpoint_path = _path(
        checkpoint_binding.get("path"), "CANARY_EXECUTION_CHECKPOINT_BINDING_INVALID"
    )
    checkpoint_payload = _read_regular(checkpoint_path, owner_private=False)
    checkpoint_sha = hashlib.sha256(checkpoint_payload).hexdigest()
    if checkpoint_sha != _sha(
        checkpoint_binding.get("file_sha256"),
        "CANARY_EXECUTION_CHECKPOINT_BINDING_INVALID",
    ):
        _fail("CANARY_EXECUTION_CHECKPOINT_HASH_MISMATCH")

    gcloud = _mapping(
        packet.get("gcloud"), GCLOUD_KEYS, "CANARY_EXECUTION_GCLOUD_BINDING_INVALID"
    )
    gcloud_binary = _path(gcloud.get("binary_path"), "CANARY_EXECUTION_GCLOUD_BINDING_INVALID")
    gcloud_payload = _read_regular(gcloud_binary, owner_private=False, executable=True)
    gcloud_receipt = _path(
        gcloud.get("resolution_receipt_path"), "CANARY_EXECUTION_GCLOUD_BINDING_INVALID"
    )
    gcloud_receipt_payload = _read_regular(gcloud_receipt, owner_private=True)
    cloudsdk = _path(
        gcloud.get("cloudsdk_config_path"), "CANARY_EXECUTION_GCLOUD_BINDING_INVALID"
    )
    _require_nonsymlink_ancestors(cloudsdk)
    try:
        cloudsdk_meta = os.lstat(cloudsdk)
    except OSError as exc:
        raise CanaryExecutionAuthorityError("CANARY_EXECUTION_CLOUDSDK_INVALID") from exc
    if (
        stat.S_ISLNK(cloudsdk_meta.st_mode)
        or not stat.S_ISDIR(cloudsdk_meta.st_mode)
        or cloudsdk_meta.st_uid != os.geteuid()
        or stat.S_IMODE(cloudsdk_meta.st_mode) != 0o700
    ):
        _fail("CANARY_EXECUTION_CLOUDSDK_INVALID")
    _require_private_regular_metadata(
        cloudsdk / "application_default_credentials.json"
    )  # Never read credential bytes or invoke a token command.

    crc32c = _mapping(
        packet.get("crc32c"), CRC32C_KEYS, "CANARY_EXECUTION_CRC32C_BINDING_INVALID"
    )
    crc_python = _path(crc32c.get("python_path"), "CANARY_EXECUTION_CRC32C_BINDING_INVALID")
    crc_worker = _path(crc32c.get("worker_path"), "CANARY_EXECUTION_CRC32C_BINDING_INVALID")
    crc_python_payload = _read_regular(crc_python, owner_private=False, executable=True)
    crc_worker_payload = _read_regular(crc_worker, owner_private=False)

    actual = {
        "git_commit": governing_commit,
        "orchestration_contract_sha256": hashlib.sha256(contract_payload).hexdigest(),
        "selected_manifest_sha256": str(manifest["manifest_sha256"]),
        "selected_source_manifest_sha256": str(body["source_manifest_sha256"]),
        "source_metadata_sha256": configuration["source_metadata"],
        "split_map_sha256": configuration["split_map"],
        "checkpoint_sha256": checkpoint_sha,
        "environment_receipt_sha256": hashlib.sha256(environment_payload).hexdigest(),
        "state_machine_schema_sha256": hashlib.sha256(
            _read_regular(authority_paths.state_machine_path, owner_private=False)
        ).hexdigest(),
        "resume_ledger_schema_sha256": hashlib.sha256(
            _read_regular(authority_paths.resume_ledger_path, owner_private=False)
        ).hexdigest(),
        "gcloud_resolution_receipt_sha256": hashlib.sha256(gcloud_receipt_payload).hexdigest(),
        "gcloud_executable_sha256": hashlib.sha256(gcloud_payload).hexdigest(),
        "crc32c_python_executable_sha256": hashlib.sha256(crc_python_payload).hexdigest(),
        "crc32c_worker_sha256": hashlib.sha256(crc_worker_payload).hexdigest(),
        "crc32c_distribution_sha256": configuration["crc32c_distribution"],
        "batch_plan_sha256": plan_sha,
    }
    runtime = core.validate_runtime_authority(
        _mapping(
            packet.get("runtime_authority"), core.RUNTIME_AUTHORITY_KEYS,
            "CANARY_EXECUTION_RUNTIME_AUTHORITY_INVALID",
        )
    )
    if runtime != core.validate_runtime_authority(actual):
        _fail("CANARY_EXECUTION_RUNTIME_AUTHORITY_MISMATCH")
    if any(runtime[key] != str(plan["authority"][key]) for key in core.PLAN_AUTHORITY_KEYS):
        _fail("CANARY_EXECUTION_PLAN_RUNTIME_AUTHORITY_MISMATCH")
    for name, observed in (
        ("checkpoint", checkpoint_sha),
        ("environment_receipt", actual["environment_receipt_sha256"]),
        ("state_machine_schema", actual["state_machine_schema_sha256"]),
        ("resume_ledger_schema", actual["resume_ledger_schema_sha256"]),
        ("gcloud_resolution_receipt", actual["gcloud_resolution_receipt_sha256"]),
        ("gcloud_executable", actual["gcloud_executable_sha256"]),
        ("crc32c_python_executable", actual["crc32c_python_executable_sha256"]),
        ("crc32c_worker", actual["crc32c_worker_sha256"]),
    ):
        if configuration[name] != observed:
            _fail("CANARY_EXECUTION_MANIFEST_RUNTIME_AUTHORITY_MISMATCH")

    launch_authority_sha256 = _sha(
        packet.get("launch_authority_sha256"),
        "CANARY_EXECUTION_LAUNCH_HASH_INVALID",
    )
    if launch_authority_sha256 != preselection_sha:
        _fail("CANARY_EXECUTION_PRESELECTION_LAUNCH_BINDING_MISMATCH")
    grants = _mapping(
        packet.get("stage_authorizations"), frozenset(STAGE_IDS),
        "CANARY_EXECUTION_STAGE_AUTHORIZATIONS_INVALID",
    )
    grant_payloads: dict[str, Mapping[str, Any]] = {}
    for stage_id in STAGE_IDS:
        grant = _mapping(
            grants[stage_id], STAGE_GRANT_KEYS,
            "CANARY_EXECUTION_STAGE_AUTHORIZATION_INVALID",
        )
        if grant.get("stage_id") != stage_id or grant.get("authorized") is not True:
            _fail("CANARY_EXECUTION_STAGE_AUTHORIZATION_INVALID")
        grant_path = _path(grant.get("path"), "CANARY_EXECUTION_STAGE_AUTHORIZATION_INVALID")
        grant_payload = _require_private_tree_file(
            grant_path,
            _sha(grant.get("file_sha256"), "CANARY_EXECUTION_STAGE_AUTHORIZATION_INVALID"),
            private_root=authority_paths.private_root,
        )
        grant_payloads[stage_id] = _load_json(
            grant_payload, "CANARY_EXECUTION_STAGE_AUTHORIZATION_JSON_INVALID"
        )
    body_binding, body_path, body_payload = _private_binding(
        packet.get("body_transfer_authorization"), BINDING_KEYS,
        "CANARY_EXECUTION_BODY_AUTHORIZATION_INVALID",
        private_root=authority_paths.private_root,
    )
    download_grant = grants["DOWNLOAD"]
    body_value = _load_json(
        body_payload, "CANARY_EXECUTION_BODY_AUTHORIZATION_JSON_INVALID"
    )
    if (
        download_grant.get("path") != str(body_path)
        or download_grant.get("file_sha256") != body_binding.get("file_sha256")
        or grant_payloads["DOWNLOAD"] != body_value
    ):
        _fail("CANARY_EXECUTION_DOWNLOAD_AUTHORIZATION_BINDING_MISMATCH")
    try:
        for stage_id, authorization_stage, batch_id in (
            ("DICOM_EXTRACTION", "DICOM_EXTRACTION", "c3_batch_000"),
            ("ECHOPRIME_EMBEDDING", "ECHOPRIME_EMBEDDING", "c3_batch_000"),
            ("BATCH_PRESERVATION", "BATCH_PRESERVATION", "c3_batch_000"),
            ("CANARY_FINALIZATION", "PRESERVATION_FINALIZATION", "all_batches"),
        ):
            stages.validate_stage_authorization_value(
                grant_payloads[stage_id],
                stage=authorization_stage,
                batch_id=batch_id,
                attempt_id=attempt_id,
                governing_commit=governing_commit,
                orchestration_contract_sha256=str(
                    contract_binding["file_sha256"]
                ),
                batch_plan_sha256=plan_sha,
                launch_authority_sha256=launch_authority_sha256,
            )
        ledger = core.initialize_resume_ledger(
            plan,
            requirements=requirements,
            attempt_id=attempt_id,
            authority=runtime,
            batch_ids=["c3_batch_000"],
        )
        core.validate_body_transfer_authorization(
            body_value,
            ledger=ledger,
            plan=plan,
            batch_id="c3_batch_000",
            maximum_attempts_per_object=int(
                contract["downloader"]["maximum_attempts_per_object"]
            ),
            expected_launch_authority_sha256=launch_authority_sha256,
        )
    except (
        core.OrchestrationError,
        stages.ProductionStageError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise CanaryExecutionAuthorityError(
            "CANARY_EXECUTION_SCIENTIFIC_AUTHORIZATION_INVALID"
        ) from exc

    _public_binding(
        packet.get("stage_worker"), fixed_path=authority_paths.stage_worker_path
    )
    _public_binding(
        packet.get("stage_launcher"),
        fixed_path=authority_paths.stage_launcher_path,
        executable=True,
    )
    qsub_binding, qsub_path, _ = _public_binding(
        packet.get("qsub"), executable=True
    )
    preselection_qsub = scheduler_tool_identities["qsub"]
    if (
        str(qsub_path) != preselection_qsub["path"]
        or qsub_binding.get("file_sha256")
        != preselection_qsub["file_sha256"]
    ):
        _fail("CANARY_EXECUTION_PRESELECTION_QSUB_BINDING_MISMATCH")
    requester = _mapping(
        packet.get("requester_pays"), REQUESTER_KEYS,
        "CANARY_EXECUTION_REQUESTER_PAYS_INVALID",
    )
    billing_project = requester.get("billing_project")
    if (
        requester.get("billing_environment_variable")
        != "LVEF_C3_GCP_BILLING_PROJECT"
        or not isinstance(billing_project, str)
        or PROJECT_RE.fullmatch(billing_project) is None
    ):
        _fail("CANARY_EXECUTION_REQUESTER_PAYS_INVALID")

    normalized = dict(packet)
    normalized["scheduler_tool_identities"] = scheduler_tool_identities
    normalized["authorization_path"] = str(path)
    normalized["authorization_file_sha256"] = hashlib.sha256(payload).hexdigest()
    return normalized


__all__ = [
    "AUTHORIZATION_SCOPES", "CanaryExecutionAuthorityError",
    "CanaryExecutionAuthorityPaths", "FIXED_PATH",
    "HARD_SCOPE", "SCHEDULER_SCOPE", "calculate_authorization_sha256",
    "build_preselection_authority", "calculate_preselection_authority_sha256",
    "load_and_validate_execution_authority", "serialize_authorization",
    "serialize_preselection_authority", "validate_preselection_authority_value",
]
