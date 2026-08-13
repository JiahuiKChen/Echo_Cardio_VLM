#!/usr/bin/env python3
"""No-clobber owner-private authority materializer for one exact-five canary.

This is a control-plane producer only.  It reads an already-authorized private
candidate inventory and exact-object metadata inventory, reuses the tracked
selection/manifest/plan/scheduler/grant validators, and publishes one closed
execution packet.  It has no cloud client, scheduler submission, DICOM, GPU,
model, prediction, or confirmatory-performance path.
"""
from __future__ import annotations

import base64
import csv
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
from typing import Any, Callable, Final, Mapping, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPT_ROOT))

import finalize_lvef_phase1ef_d3 as phase1eg_authority
import capture_lvef_c3_post_reallocation_capacity as capacity_authority
import lvef_c3_canary_execution_authority as execution_authority
import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler_contract
import lvef_c3_canary_state as canary_state
import lvef_c3_execution_state as tracked_execution_state
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


RUN_ID_RE: Final = re.compile(r"^lvef_c3_exact_five_canary_[a-z0-9]{8}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
PROJECT_RE: Final = re.compile(r"^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
PRESELECTION_SCOPE_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "governing_commit",
        "run_id",
        "owner_authorized",
        "hard_scope",
        "scheduler_scope",
        "scheduler_tools",
        "authorization_scopes",
        "created_at_utc",
        "preselection_authority_sha256",
    }
)
HISTORICAL_STUDY_MANIFEST_SHA256: Final = (
    "feaf0cf7da58ae3d9c8901a583e4319d1b34a8cc26ae57dfcecd309940f81a60"
)
PRIOR_SMOKE_PRESERVATION_MANIFEST_SHA256: Final = (
    "7be4f39e7ce9123d3f59407432cd39257082f13ceebbca8390266979f81fcc8b"
)
PRIOR_SMOKE_RUN_NAME: Final = (
    "lvef_multitask_phase1e_a_20260804T125829Z_022d7581eee4"
)
# Git has no historical qsub/qstat identity.  A metadata-only SCC probe on
# 2026-08-12 established these root-owned lexical entrypoints.  Live discovery
# resolves each one to a root-owned regular executable, seals its exact current
# identity in the preselection authority, and revalidates it before use.
SCC_QSUB_LEXICAL_PATH: Final = Path("/usr/local/bin/qsub")
SCC_QSTAT_LEXICAL_PATH: Final = Path("/usr/local/bin/qstat")
SCC_FINDMNT_PATH: Final = Path("/usr/bin/findmnt")
SCC_DF_PATH: Final = Path("/usr/bin/df")
TRANSACTION_MARKER_NAME: Final = ".materialization_transaction.restricted.json"
TRANSACTION_MARKER_KEYS: Final = frozenset(
    {"schema_version", "artifact_type", "governing_commit", "run_id", "status"}
)


class CanaryAuthorityMaterializationError(RuntimeError):
    """A stable aggregate-safe materialization failure."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_AUTHORITY_MATERIALIZATION_INVALID"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MaterializationConfig:
    """Complete file authority needed to create and validate one packet."""

    tracked_worktree: Path
    execution_state_path: Path
    private_root: Path
    lifecycle_path: Path
    authority_path: Path
    candidate_csv: Path
    source_object_csv: Path
    governing_commit: str
    run_id: str
    production_contract_path: Path
    state_machine_path: Path
    resume_ledger_path: Path
    scheduler_plan_path: Path
    environment_receipt_path: Path
    checkpoint_path: Path
    gcloud_binary_path: Path
    gcloud_resolution_receipt_path: Path
    cloudsdk_config_path: Path
    crc32c_python_path: Path
    crc32c_worker_path: Path
    crc32c_distribution_sha256: str
    qsub_path: Path
    qstat_path: Path
    native_quota_path: Path
    pquota_path: Path
    findmnt_path: Path
    df_path: Path
    research_path: Path
    backed_path: Path
    stage_worker_path: Path
    stage_launcher_path: Path
    requester_pays_billing_project: str
    source_metadata_sha256: str
    split_map_sha256: str
    historical_study_manifest_sha256: str
    historical_study_manifest_size: int
    prior_smoke_source_manifest_sha256: str
    prior_smoke_source_manifest_size: int
    prior_smoke_source_summary_sha256: str
    prior_smoke_source_summary_size: int
    prior_smoke_source_safety_sha256: str
    prior_smoke_source_safety_size: int
    prior_smoke_preservation_manifest_sha256: str
    prior_smoke_preservation_manifest_size: int
    launch_authority_sha256: str
    synthetic_mode: bool = False
    transaction_created_files: tuple[Path, ...] = ()
    transaction_created_directories: tuple[Path, ...] = ()
    transaction_marker_path: Path | None = None
    transaction_marker_sha256: str | None = None


@dataclass(frozen=True)
class MaterializationAuthoritySource:
    """Validated preserved inputs projected into the common discovery path."""

    governing_commit: str
    run_id: str
    production_root: Path
    environment_receipt_path: Path
    checkpoint_path: Path
    gcloud_binary_path: Path
    gcloud_resolution_receipt_path: Path
    cloudsdk_config_path: Path
    crc32c_python_path: Path
    crc32c_distribution_sha256: str
    requester_pays_billing_project: str
    selected_studies_path: Path
    selected_studies_size: int
    selected_studies_sha256: str
    selected_source_path: Path
    selected_source_size: int
    selected_source_sha256: str
    source_metadata_path: Path
    source_metadata_size: int
    source_metadata_sha256: str
    split_map_path: Path
    split_map_size: int
    split_map_sha256: str
    historical_study_manifest_path: Path
    historical_study_manifest_size: int
    historical_study_manifest_sha256: str
    prior_smoke_source_manifest_path: Path
    prior_smoke_source_manifest_size: int
    prior_smoke_source_manifest_sha256: str
    prior_smoke_source_summary_path: Path
    prior_smoke_source_summary_size: int
    prior_smoke_source_summary_sha256: str
    prior_smoke_source_safety_path: Path
    prior_smoke_source_safety_size: int
    prior_smoke_source_safety_sha256: str
    prior_smoke_preservation_manifest_path: Path
    prior_smoke_preservation_manifest_size: int
    prior_smoke_preservation_manifest_sha256: str
    qsub_path: Path
    qstat_path: Path
    native_quota_path: Path
    pquota_path: Path
    findmnt_path: Path
    df_path: Path
    research_path: Path
    backed_path: Path
    synthetic_mode: bool = False


ConflictProbe = Callable[[MaterializationConfig], bool]
QuotaProbe = Callable[[MaterializationConfig], bool]
AttemptProbe = Callable[[tracked_execution_state.ExecutionState], bool]
Clock = Callable[[], datetime]


def _default_conflict_probe(config: MaterializationConfig) -> bool:
    """Require unused outputs plus no conflicting live process or SGE job."""

    root = config.private_root
    outputs = (
        config.authority_path,
        root / "exact_five_manifest.restricted.json",
        root / "exact_five_batch_plan.restricted.json",
        root / "exact_five_scheduler_plan.restricted.json",
        root / "body_transfer_authorization.restricted.json",
        root / "canary_runs" / config.run_id,
    )
    if any(os.path.lexists(path) for path in outputs):
        return False
    if config.synthetic_mode:
        # The sandbox has no SGE namespace.  Output-root collision semantics
        # above are identical; live mode alone consults the host process/job
        # namespace so repository test commands cannot self-match as jobs.
        return True
    qstat = config.qstat_path
    if not qstat.is_absolute():
        return False
    try:
        _read_regular(qstat, private=False, executable=True)
    except CanaryAuthorityMaterializationError:
        return False
    try:
        account_name = pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):
        return False
    if not account_name or re.fullmatch(r"[A-Za-z0-9_.-]+", account_name) is None:
        return False
    safe_environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    try:
        process_result = subprocess.run(
            ["/bin/ps", "-axo", "command="],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=safe_environment,
        )
    except OSError:
        return False
    if process_result.returncode != 0:
        return False
    conflict_tokens = (
        "scc_run_lvef_c3_canary_stage.sh",
        "lvef_c3_canary_stage_worker.py",
        "execute_exact_batch_download",
        "run_production_dicom_extraction",
        "run_production_echoprime",
        "preserve_lvef_c3_production_batch.py",
    )
    if any(token in process_result.stdout for token in conflict_tokens):
        return False
    try:
        scheduler_result = subprocess.run(
            [str(qstat), "-u", account_name],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=safe_environment,
        )
    except OSError:
        return False
    return scheduler_result.returncode == 0 and "c3c_" not in scheduler_result.stdout


def _default_quota_probe(config: MaterializationConfig) -> bool:
    """Reuse the sealed native-quota/current-df no-body headroom probe."""

    try:
        result = capacity_authority.probe_current_canary_headroom(
            capacity_authority.CurrentCanaryHeadroomAuthority(
                native_quota_path=config.native_quota_path,
                pquota_path=config.pquota_path,
                findmnt_path=config.findmnt_path,
                df_path=config.df_path,
                research_path=config.research_path,
                backed_path=config.backed_path,
            )
        )
        capacity_authority.validate_current_canary_headroom(result)
    except (OSError, capacity_authority.PostReallocationCapacityError):
        return False
    return True


def _default_attempt_probe(state: tracked_execution_state.ExecutionState) -> bool:
    return (
        state.logical_execution_attempt == 4
        and state.attempt_004_execution_count == 1
        and state.next_unused_execution_attempt == 5
        and state.attempt_005_exists is False
        and state.next_unused_production_attempt == 6
        and state.production_attempt_006_exists is False
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MaterializationProbes:
    """Read-only environmental gates, injectable only at the programmatic API."""

    conflict_probe: ConflictProbe = _default_conflict_probe
    quota_probe: QuotaProbe = _default_quota_probe
    attempt_probe: AttemptProbe = _default_attempt_probe
    clock: Clock = _utc_now


@dataclass(frozen=True)
class MaterializationResult:
    status: str
    state: Mapping[str, Any]
    preselection_identifier_fields: int
    manifest_study_count: int
    manifest_subject_count: int
    manifest_object_count: int
    manifest_byte_count: int
    manifest_sha256: str
    manifest_file_sha256: str
    batch_plan_sha256: str
    scheduler_plan_sha256: str
    authorization_sha256: str
    authorization_file_sha256: str
    lifecycle_state: str
    lifecycle_root: Path
    run_id: str
    authority_paths: execution_authority.CanaryExecutionAuthorityPaths
    manifest_path: Path
    batch_plan_path: Path
    scheduler_plan_path: Path
    authority_path: Path


def _fail(code: str) -> None:
    raise CanaryAuthorityMaterializationError(code)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(_read_regular(path, private=False, executable=False))


def _require_no_symlink_ancestors(path: Path) -> Path:
    """Return a lexical absolute path after rejecting every ancestor symlink."""

    candidate = Path(path)
    if (
        not candidate.is_absolute()
        or candidate == Path(candidate.anchor)
        or ".." in candidate.parts
    ):
        _fail("CANARY_MATERIALIZATION_PATH_INVALID")
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    current = Path(lexical.anchor)
    for component in lexical.parts[1:-1]:
        current /= component
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_ANCESTOR_INVALID"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            _fail("CANARY_MATERIALIZATION_ANCESTOR_SYMLINK_FORBIDDEN")
        if not stat.S_ISDIR(metadata.st_mode):
            _fail("CANARY_MATERIALIZATION_ANCESTOR_INVALID")
    return lexical


def _canonical_private_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _read_regular(path: Path, *, private: bool, executable: bool = False) -> bytes:
    path = _require_no_symlink_ancestors(Path(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_INPUT_NOT_REGULAR"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (private and (opened.st_uid != os.geteuid() or mode != 0o600))
            or (not private and mode & 0o022)
            or (executable and not mode & stat.S_IXUSR)
        ):
            _fail("CANARY_MATERIALIZATION_INPUT_NOT_REGULAR")
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
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            _fail("CANARY_MATERIALIZATION_INPUT_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def _require_private_directory(path: Path, *, create: bool = False) -> bool:
    path = _require_no_symlink_ancestors(Path(path))
    created = False
    if not os.path.lexists(path):
        if not create:
            _fail("CANARY_MATERIALIZATION_PRIVATE_DIRECTORY_MISSING")
        try:
            os.mkdir(path, mode=0o700)
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_PRIVATE_DIRECTORY_CREATE_FAILED"
            ) from exc
        created = True
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_PRIVATE_DIRECTORY_INVALID"
        ) from exc
    if (
        not path.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("CANARY_MATERIALIZATION_PRIVATE_DIRECTORY_INVALID")
    return created


def _require_private_regular_metadata(path: Path) -> None:
    """Validate credential-file metadata without opening or reading its bytes."""

    path = _require_no_symlink_ancestors(Path(path))
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_PRIVATE_METADATA_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        _fail("CANARY_MATERIALIZATION_PRIVATE_METADATA_INVALID")


def _write_bytes_no_clobber(path: Path, payload: bytes) -> str:
    path = _require_no_symlink_ancestors(Path(path))
    if not path.is_absolute() or os.path.lexists(path):
        _fail("CANARY_MATERIALIZATION_OUTPUT_COLLISION")
    _require_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.materialization.partial"
    if os.path.lexists(temporary):
        _fail("CANARY_MATERIALIZATION_TEMPORARY_COLLISION")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    published = False
    published_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                _fail("CANARY_MATERIALIZATION_OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
        ):
            _fail("CANARY_MATERIALIZATION_OUTPUT_POSTWRITE_INVALID")
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _fail("CANARY_MATERIALIZATION_OUTPUT_COLLISION")
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_OUTPUT_PUBLISH_FAILED"
            ) from exc
        source_metadata = os.lstat(temporary)
        destination_metadata = os.lstat(path)
        if (
            not stat.S_ISREG(destination_metadata.st_mode)
            or stat.S_ISLNK(destination_metadata.st_mode)
            or destination_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(destination_metadata.st_mode) != 0o600
            or (destination_metadata.st_dev, destination_metadata.st_ino)
            != (source_metadata.st_dev, source_metadata.st_ino)
        ):
            _fail("CANARY_MATERIALIZATION_OUTPUT_PUBLISH_FAILED")
        published_identity = (
            destination_metadata.st_dev,
            destination_metadata.st_ino,
        )
        try:
            os.unlink(temporary)
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_TEMPORARY_CLEANUP_FAILED"
            ) from exc
        published = True
        return _sha256(payload)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not published and published_identity is not None:
            try:
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) == published_identity:
                    os.unlink(path)
            except FileNotFoundError:
                pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_TEMPORARY_CLEANUP_FAILED"
            ) from exc


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path), "file_sha256": _sha256_file(path)}


def _scheduler_tool_identity(path: Path) -> dict[str, Any]:
    """Seal one stable scheduler executable identity without following its leaf."""

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_SCHEDULER_TOOL_INVALID"
        ) from exc
    payload = _read_regular(path, private=False, executable=True)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_SCHEDULER_TOOL_INVALID"
        ) from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(payload) != after.st_size
    ):
        _fail("CANARY_MATERIALIZATION_SCHEDULER_TOOL_CHANGED")
    return {
        "path": str(path),
        "file_sha256": _sha256(payload),
        "size_bytes": after.st_size,
        "device_id": after.st_dev,
        "inode": after.st_ino,
    }


def _validate_config(config: MaterializationConfig) -> None:
    if (
        COMMIT_RE.fullmatch(config.governing_commit) is None
        or RUN_ID_RE.fullmatch(config.run_id) is None
        or SHA256_RE.fullmatch(config.crc32c_distribution_sha256) is None
        or SHA256_RE.fullmatch(config.source_metadata_sha256) is None
        or SHA256_RE.fullmatch(config.split_map_sha256) is None
        or SHA256_RE.fullmatch(config.historical_study_manifest_sha256) is None
        or SHA256_RE.fullmatch(config.prior_smoke_source_manifest_sha256) is None
        or SHA256_RE.fullmatch(config.prior_smoke_source_summary_sha256) is None
        or SHA256_RE.fullmatch(config.prior_smoke_source_safety_sha256) is None
        or SHA256_RE.fullmatch(config.prior_smoke_preservation_manifest_sha256)
        is None
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                config.historical_study_manifest_size,
                config.prior_smoke_source_manifest_size,
                config.prior_smoke_source_summary_size,
                config.prior_smoke_source_safety_size,
                config.prior_smoke_preservation_manifest_size,
            )
        )
        or PROJECT_RE.fullmatch(config.requester_pays_billing_project) is None
        or type(config.synthetic_mode) is not bool
        or (
            config.synthetic_mode is False
            and (
                config.native_quota_path
                != capacity_authority.EXPECTED_NATIVE_QUOTA_FILE
                or config.pquota_path
                != capacity_authority.EXPECTED_PQUOTA_EXECUTABLE
                or config.findmnt_path != SCC_FINDMNT_PATH
                or config.df_path != SCC_DF_PATH
                or config.research_path
                != capacity_authority.EXPECTED_RESTRICTED_PATHS["research"]
                or config.backed_path
                != capacity_authority.EXPECTED_RESTRICTED_PATHS["backed"]
            )
        )
    ):
        _fail("CANARY_MATERIALIZATION_CONFIG_IDENTITY_INVALID")
    declared_paths = {
        name: Path(getattr(config, name))
        for name in (
            "tracked_worktree",
            "execution_state_path",
            "private_root",
            "lifecycle_path",
            "authority_path",
            "candidate_csv",
            "source_object_csv",
            "production_contract_path",
            "state_machine_path",
            "resume_ledger_path",
            "scheduler_plan_path",
            "environment_receipt_path",
            "checkpoint_path",
            "gcloud_binary_path",
            "gcloud_resolution_receipt_path",
            "cloudsdk_config_path",
            "crc32c_python_path",
            "crc32c_worker_path",
            "qsub_path",
            "qstat_path",
            "native_quota_path",
            "pquota_path",
            "findmnt_path",
            "df_path",
            "research_path",
            "backed_path",
            "stage_worker_path",
            "stage_launcher_path",
        )
    }
    if any(not path.is_absolute() or ".." in path.parts for path in declared_paths.values()):
        _fail("CANARY_MATERIALIZATION_CONFIG_PATH_INVALID")
    root = config.tracked_worktree
    private = config.private_root
    expected = {
        "execution_state_path": root / "configs/lvef_c3_execution_state_v1.yaml",
        "production_contract_path": root / "configs/lvef_c3_orchestration_v2.yaml",
        "state_machine_path": root / "configs/lvef_c3_state_machine_v2.json",
        "resume_ledger_path": root / "configs/lvef_c3_resume_ledger_v2.json",
        "scheduler_plan_path": root / "configs/lvef_c3_canary_scheduler_plan_v1.json",
        "stage_worker_path": root / "scripts/lvef_c3_canary_stage_worker.py",
        "stage_launcher_path": root / "scripts/scc_run_lvef_c3_canary_stage.sh",
        "authority_path": private / "execution_authorization_v1.json",
        "lifecycle_path": private
        / "lifecycle_state"
        / "canary_state.restricted.json",
    }
    if any(declared_paths[name] != path for name, path in expected.items()):
        _fail("CANARY_MATERIALIZATION_CONFIG_PATH_INVALID")
    _require_private_directory(private)
    for path in (
        config.execution_state_path,
        config.production_contract_path,
        config.state_machine_path,
        config.resume_ledger_path,
        config.scheduler_plan_path,
        config.checkpoint_path,
        config.gcloud_resolution_receipt_path,
        config.crc32c_worker_path,
        config.stage_worker_path,
    ):
        _read_regular(
            path,
            private=path
            in {config.environment_receipt_path, config.gcloud_resolution_receipt_path},
        )
    _read_regular(config.environment_receipt_path, private=True)
    for path in (
        config.gcloud_binary_path,
        config.crc32c_python_path,
        config.qsub_path,
        config.qstat_path,
        config.pquota_path,
        config.findmnt_path,
        config.df_path,
        config.stage_launcher_path,
    ):
        _read_regular(path, private=False, executable=True)
    _read_regular(config.native_quota_path, private=False)
    _require_private_directory(config.cloudsdk_config_path)
    _require_private_regular_metadata(
        config.cloudsdk_config_path / "application_default_credentials.json"
    )  # Credential bytes are deliberately never opened.


def _preselection_scope(config: MaterializationConfig, now: datetime) -> dict[str, Any]:
    try:
        value = execution_authority.build_preselection_authority(
            governing_commit=config.governing_commit,
            run_id=config.run_id,
            created_at_utc=now.isoformat(),
            scheduler_tools={
                "qsub": _scheduler_tool_identity(config.qsub_path),
                "qstat": _scheduler_tool_identity(config.qstat_path),
            },
        )
        execution_authority.validate_preselection_authority_value(
            value,
            expected_governing_commit=config.governing_commit,
            expected_run_id=config.run_id,
        )
    except AttributeError:
        # This branch can only occur while the tracked producer is absent; the
        # top-level control path rejects that installation before preparation.
        _fail("CANARY_PRESELECTION_PRODUCER_MISSING")
    if (
        set(value) != PRESELECTION_SCOPE_KEYS
        or value["hard_scope"].get("studies") != 5
        or value["hard_scope"].get("subjects") != 5
        or value["hard_scope"].get("split") != "train"
        or value["scheduler_scope"].get("scheduler_submission_count") != 5
        or value["scheduler_scope"].get("gpu_stage_count") != 1
        or value["scheduler_scope"].get("production_continuation") is not False
        or value["authorization_scopes"].get("model_fitting") is not False
        or value["authorization_scopes"].get("endpoint_prediction") is not False
        or value["authorization_scopes"].get("confirmatory_performance_access")
        is not False
    ):
        _fail("CANARY_PRESELECTION_SCOPE_INVALID")
    forbidden = {
        "subject_id",
        "study_id",
        "source_object_key",
        "source_relative_path",
        "label",
        "prediction",
        "clinical_row",
    }
    if forbidden.intersection(value):
        _fail("CANARY_PRESELECTION_IDENTIFIER_LEAK")
    return value


def _plan_requirements(manifest: Mapping[str, Any]) -> core.PlanRequirements:
    body = manifest["manifest"]
    return core.PlanRequirements(
        release=str(body["source_release"]),
        selected_studies=5,
        selected_subjects=5,
        normalized_source_objects=int(body["expected_object_count"]),
        selected_source_bytes=int(body["expected_byte_total"]),
        batch_count=1,
        studies_per_full_batch=5,
        final_batch_studies=5,
        contract_id="lvef_multitask_c3_exact_five_canary_v1",
    )


def _build_plan(
    manifest: Mapping[str, Any], authority: Mapping[str, str]
) -> tuple[dict[str, Any], core.PlanRequirements]:
    body = manifest["manifest"]
    selected_rows = [
        {"subject_id": row["subject_id"], "study_id": row["study_id"]}
        for row in body["studies"]
    ]
    split_rows = [
        {"subject_id": row["subject_id"], "split": "train"}
        for row in body["studies"]
    ]
    source_rows: list[dict[str, Any]] = []
    for study in body["studies"]:
        for item in study["objects"]:
            source_rows.append(
                {
                    "release_id": body["source_release"],
                    "subject_id": study["subject_id"],
                    "study_id": study["study_id"],
                    "split": "train",
                    "production_batch": "c3_batch_000",
                    **item,
                }
            )
    requirements = _plan_requirements(manifest)
    plan = core.build_immutable_batch_plan(
        selected_rows,
        source_rows,
        split_rows,
        requirements=requirements,
        authority=authority,
    )
    core.validate_batch_plan(plan, requirements=requirements)
    return plan, requirements


def _authority_paths(config: MaterializationConfig) -> execution_authority.CanaryExecutionAuthorityPaths:
    return execution_authority.CanaryExecutionAuthorityPaths(
        private_root=config.private_root,
        fixed_path=config.authority_path,
        canary_run_root=config.private_root / "canary_runs",
        tracked_worktree=config.tracked_worktree,
        production_contract_path=config.production_contract_path,
        execution_state_path=config.execution_state_path,
        state_machine_path=config.state_machine_path,
        resume_ledger_path=config.resume_ledger_path,
        stage_worker_path=config.stage_worker_path,
        stage_launcher_path=config.stage_launcher_path,
    )


def _stage_grant(
    *,
    stage: str,
    batch_id: str,
    config: MaterializationConfig,
    contract_sha256: str,
    plan_sha256: str,
    launch_authority_sha256: str,
    owner_date: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
        "status": "AUTHORIZED",
        "authorization_scope": stages.STAGE_AUTHORIZATION_SCOPES[stage],
        "stage": stage,
        "batch_id": batch_id,
        "attempt_id": config.run_id,
        "governing_commit": config.governing_commit,
        "orchestration_contract_sha256": contract_sha256,
        "batch_plan_sha256": plan_sha256,
        "launch_authority_sha256": launch_authority_sha256,
        "owner_authorized": True,
        "owner_authorization_date": owner_date,
    }


def _safe_cleanup(files: Sequence[Path], directories: Sequence[Path]) -> bool:
    for path in reversed(tuple(files)):
        try:
            path = _require_no_symlink_ancestors(Path(path))
            metadata = os.lstat(path)
            if (
                stat.S_ISREG(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_uid == os.geteuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600
            ):
                os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    for path in reversed(tuple(directories)):
        try:
            path = _require_no_symlink_ancestors(Path(path))
            os.rmdir(path)
        except OSError:
            pass
    return not any(
        os.path.lexists(path) for path in (*tuple(files), *tuple(directories))
    )


def _restore_lifecycle(
    path: Path,
    *,
    prior_payload: bytes | None,
    root_existed: bool,
) -> None:
    """Restore the exact pre-call lifecycle state after any failed prepare."""

    root = path.parent
    lock = root / f"{path.name}.lock"
    temporary = root / f"{path.name}.partial"
    if prior_payload is None:
        if not root_existed and not os.path.lexists(root):
            if os.path.lexists(path):
                _fail("CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED")
            return
        _require_no_symlink_ancestors(path)
        for candidate in (path, temporary, lock):
            try:
                metadata = os.lstat(candidate)
                if metadata.st_uid == os.geteuid() and stat.S_ISREG(metadata.st_mode):
                    os.unlink(candidate)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise CanaryAuthorityMaterializationError(
                    "CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED"
                ) from exc
        if not root_existed:
            try:
                os.rmdir(root)
            except OSError as exc:
                raise CanaryAuthorityMaterializationError(
                    "CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED"
                ) from exc
        if os.path.lexists(path) or (not root_existed and os.path.lexists(root)):
            _fail("CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED")
        return
    _require_no_symlink_ancestors(path)
    try:
        current = _read_regular(path, private=True)
    except CanaryAuthorityMaterializationError:
        current = None
    if current == prior_payload:
        return
    rollback = root / ".canary_state.restricted.rollback.partial"
    try:
        if os.path.lexists(rollback):
            _fail("CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED")
        descriptor = os.open(
            rollback,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            offset = 0
            while offset < len(prior_payload):
                written = os.write(descriptor, prior_payload[offset:])
                if written < 1:
                    _fail("CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(rollback, path)
    except CanaryAuthorityMaterializationError:
        raise
    except OSError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED"
        ) from exc
    finally:
        try:
            os.unlink(rollback)
        except FileNotFoundError:
            pass
    try:
        restored = _read_regular(path, private=True)
    except CanaryAuthorityMaterializationError as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED"
        ) from exc
    if restored != prior_payload:
        _fail("CANARY_MATERIALIZATION_LIFECYCLE_ROLLBACK_FAILED")


def _adopt_discovery_transaction(
    config: MaterializationConfig,
) -> tuple[list[Path], list[Path]]:
    """Validate and take rollback ownership of the discovery artifacts."""

    marker = config.transaction_marker_path
    digest = config.transaction_marker_sha256
    files = list(config.transaction_created_files)
    directories = list(config.transaction_created_directories)
    if marker is None or digest is None or marker not in files:
        _fail("CANARY_MATERIALIZATION_TRANSACTION_MISSING")
    if SHA256_RE.fullmatch(digest) is None:
        _fail("CANARY_MATERIALIZATION_TRANSACTION_INVALID")
    allowed_files = {
        marker,
        config.private_root / "preselection_scope.restricted.json",
        config.candidate_csv,
        config.source_object_csv,
    }
    allowed_directories = {
        config.private_root.parent,
        config.private_root,
        config.candidate_csv.parent,
    }
    if (
        set(files) != allowed_files
        or not set(directories).issubset(allowed_directories)
        or len(files) != len(set(files))
        or len(directories) != len(set(directories))
        or _sha256(_read_regular(marker, private=True)) != digest
    ):
        _fail("CANARY_MATERIALIZATION_TRANSACTION_INVALID")
    try:
        value = json.loads(_read_regular(marker, private=True))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_TRANSACTION_INVALID"
        ) from exc
    if (
        not isinstance(value, Mapping)
        or set(value) != TRANSACTION_MARKER_KEYS
        or value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_canary_materialization_transaction_v1"
        or value.get("governing_commit") != config.governing_commit
        or value.get("run_id") != config.run_id
        or value.get("status") != "DISCOVERY_ARTIFACTS_PENDING_SEAL"
    ):
        _fail("CANARY_MATERIALIZATION_TRANSACTION_INVALID")
    return files, directories


def prepare_live_authority(
    config: MaterializationConfig,
    *,
    probes: MaterializationProbes | None = None,
) -> MaterializationResult:
    """Create, validate, and lifecycle-seal exactly one private authority."""

    probes = probes or MaterializationProbes()
    created_files, created_directories = _adopt_discovery_transaction(config)
    lifecycle_root_existed = os.path.lexists(config.lifecycle_path.parent)
    lifecycle_prior: bytes | None = None
    lifecycle_checkpointed = False
    try:
        _validate_config(config)
        if os.path.lexists(config.lifecycle_path):
            lifecycle_prior = _read_regular(config.lifecycle_path, private=True)
        lifecycle_checkpointed = True
        tracked = tracked_execution_state.load_execution_state(
            config.execution_state_path
        )
        if (
            tracked.branch != execution_authority.BRANCH
            or not tracked.permits("prepare_exact_five_canary_authority")
            or not tracked.permits("seal_exact_five_canary_manifest")
            or not probes.attempt_probe(tracked)
        ):
            _fail("CANARY_MATERIALIZATION_ATTEMPT_INVARIANT_INVALID")
        now = probes.clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            _fail("CANARY_MATERIALIZATION_CLOCK_INVALID")
        preselection_path = (
            config.private_root / "preselection_scope.restricted.json"
        )
        if os.path.lexists(preselection_path):
            preselection_payload = _read_regular(preselection_path, private=True)
            try:
                preselection = json.loads(preselection_payload)
                created_at = datetime.fromisoformat(
                    str(preselection["created_at_utc"])
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CanaryAuthorityMaterializationError(
                    "CANARY_PRESELECTION_SCOPE_INVALID"
                ) from exc
            if (
                preselection_payload
                != execution_authority.serialize_preselection_authority(
                    preselection
                )
                or preselection != _preselection_scope(config, created_at)
                or preselection["preselection_authority_sha256"]
                != config.launch_authority_sha256
            ):
                _fail("CANARY_PRESELECTION_SCOPE_INVALID")
            now = created_at
        else:
            preselection = _preselection_scope(config, now)
            _write_bytes_no_clobber(
                preselection_path,
                execution_authority.serialize_preselection_authority(
                    preselection
                ),
            )
            created_files.append(preselection_path)
        launch_authority_sha256 = str(
            preselection["preselection_authority_sha256"]
        )
        # Revalidate the adopted, exact scheduler identities before the second
        # process/qstat and quota gate. No executable is invoked first.
        if not probes.conflict_probe(config):
            _fail("CANARY_MATERIALIZATION_CONFLICT_PRESENT")
        if not probes.quota_probe(config):
            _fail("CANARY_MATERIALIZATION_QUOTA_INSUFFICIENT")
        # Persist the identifier-free authorization envelope before opening any
        # row-bearing inventory.  It is no-clobber and is cleaned transactionally
        # if any later selection, validation, or lifecycle transition fails.

        candidate_payload = _read_regular(config.candidate_csv, private=True)
        source_payload = _read_regular(config.source_object_csv, private=True)
        candidates = manifest_contract.parse_candidate_csv_bytes(candidate_payload)
        source_objects = manifest_contract.parse_source_object_csv_bytes(
            source_payload
        )
        selected = manifest_contract.select_exact_five(candidates)
        selected_pairs = {
            (item.subject_id, item.study_id) for item in selected
        }
        selected_source_objects = [
            row
            for row in source_objects
            if (str(row["subject_id"]), str(row["study_id"])) in selected_pairs
        ]

        configuration = {
            "execution_state": _sha256_file(config.execution_state_path),
            "production_contract": _sha256_file(config.production_contract_path),
            "source_metadata": config.source_metadata_sha256,
            "split_map": config.split_map_sha256,
            "historical_study_manifest": (
                config.historical_study_manifest_sha256
            ),
            "prior_smoke_source_manifest": (
                config.prior_smoke_source_manifest_sha256
            ),
            "prior_smoke_source_summary": config.prior_smoke_source_summary_sha256,
            "prior_smoke_source_safety": config.prior_smoke_source_safety_sha256,
            "prior_smoke_preservation_manifest": (
                config.prior_smoke_preservation_manifest_sha256
            ),
            "checkpoint": _sha256_file(config.checkpoint_path),
            "environment_receipt": _sha256(
                _read_regular(config.environment_receipt_path, private=True)
            ),
            "state_machine_schema": _sha256_file(config.state_machine_path),
            "resume_ledger_schema": _sha256_file(config.resume_ledger_path),
            "gcloud_resolution_receipt": _sha256(
                _read_regular(config.gcloud_resolution_receipt_path, private=True)
            ),
            "gcloud_executable": _sha256_file(config.gcloud_binary_path),
            "crc32c_python_executable": _sha256_file(config.crc32c_python_path),
            "crc32c_worker": _sha256_file(config.crc32c_worker_path),
            "crc32c_distribution": config.crc32c_distribution_sha256,
        }
        manifest = manifest_contract.build_sealed_manifest(
            selected_studies=selected,
            source_objects=selected_source_objects,
            source_authority_commit=config.governing_commit,
            source_manifest_sha256=_sha256(source_payload),
            source_configuration_hashes=configuration,
        )
        manifest_path = config.private_root / "exact_five_manifest.restricted.json"
        manifest_file_sha, manifest_sha = (
            manifest_contract.write_private_manifest_no_clobber(
                manifest_path, manifest
            )
        )
        created_files.append(manifest_path)

        plan_authority = {
            "git_commit": config.governing_commit,
            "orchestration_contract_sha256": configuration["production_contract"],
            "selected_manifest_sha256": manifest_sha,
            "selected_source_manifest_sha256": _sha256(source_payload),
            "source_metadata_sha256": configuration["source_metadata"],
            "split_map_sha256": configuration["split_map"],
            "checkpoint_sha256": configuration["checkpoint"],
            "environment_receipt_sha256": configuration["environment_receipt"],
            "state_machine_schema_sha256": configuration["state_machine_schema"],
            "resume_ledger_schema_sha256": configuration["resume_ledger_schema"],
            "gcloud_resolution_receipt_sha256": configuration[
                "gcloud_resolution_receipt"
            ],
            "gcloud_executable_sha256": configuration["gcloud_executable"],
            "crc32c_python_executable_sha256": configuration[
                "crc32c_python_executable"
            ],
            "crc32c_worker_sha256": configuration["crc32c_worker"],
            "crc32c_distribution_sha256": configuration["crc32c_distribution"],
        }
        plan, requirements = _build_plan(manifest, plan_authority)
        plan_sha = core.validate_batch_plan(plan, requirements=requirements)
        plan_path = config.private_root / "exact_five_batch_plan.restricted.json"
        plan_file_sha = _write_bytes_no_clobber(
            plan_path, _canonical_private_json(plan)
        )
        created_files.append(plan_path)

        scheduler_template = scheduler_contract.load_scheduler_plan(
            config.scheduler_plan_path
        )
        bound_scheduler = scheduler_contract.bind_scheduler_plan(
            scheduler_template,
            manifest_sha,
            repository_root=config.tracked_worktree,
        )
        scheduler_sha = scheduler_contract.validate_scheduler_plan(
            bound_scheduler,
            repository_root=config.tracked_worktree,
            require_bound_manifest=True,
        )
        bound_scheduler_path = (
            config.private_root / "exact_five_scheduler_plan.restricted.json"
        )
        scheduler_file_sha = _write_bytes_no_clobber(
            bound_scheduler_path, _canonical_private_json(bound_scheduler)
        )
        created_files.append(bound_scheduler_path)

        canary_runs = config.private_root / "canary_runs"
        if _require_private_directory(canary_runs, create=True):
            created_directories.append(canary_runs)
        stage_root = config.private_root / "stage_authorizations"
        if _require_private_directory(stage_root, create=True):
            created_directories.append(stage_root)

        runtime_authority = {**plan_authority, "batch_plan_sha256": plan_sha}
        ledger = core.initialize_resume_ledger(
            plan,
            requirements=requirements,
            attempt_id=config.run_id,
            authority=runtime_authority,
            batch_ids=("c3_batch_000",),
        )
        contract = core.load_orchestration_contract(config.production_contract_path)
        maximum_attempts = int(contract["downloader"]["maximum_attempts_per_object"])
        issued = now - timedelta(seconds=1)
        expires = now + timedelta(days=7)
        body_value = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_body_transfer_authorization_v2",
            "status": "AUTHORIZED_C3_DICOM_BODY_TRANSFER",
            "attempt_id": config.run_id,
            "scope": "FIRST_BATCH_ONLY",
            "batch_ids": ["c3_batch_000"],
            "authority_sha256": core.canonical_json_sha256(
                ledger["authority"]
            ),
            "batch_plan_sha256": plan_sha,
            "launch_authority_sha256": launch_authority_sha256,
            "maximum_requests": requirements.normalized_source_objects
            * maximum_attempts,
            "issued_at_utc": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at_utc": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "owner_authorization_recorded": True,
            "body_download_only": True,
            "scientific_actions_authorized": False,
        }
        core.validate_body_transfer_authorization(
            body_value,
            ledger=ledger,
            plan=plan,
            batch_id="c3_batch_000",
            maximum_attempts_per_object=maximum_attempts,
            expected_launch_authority_sha256=launch_authority_sha256,
            now=now,
        )
        body_path = (
            config.private_root / "body_transfer_authorization.restricted.json"
        )
        _write_bytes_no_clobber(body_path, _canonical_private_json(body_value))
        created_files.append(body_path)

        grants: dict[str, dict[str, Any]] = {
            "DOWNLOAD": {
                "stage_id": "DOWNLOAD",
                **_binding(body_path),
                "authorized": True,
            }
        }
        owner_date = now.date().isoformat()
        stage_roles = {
            "DICOM_EXTRACTION": ("DICOM_EXTRACTION", "c3_batch_000"),
            "ECHOPRIME_EMBEDDING": ("ECHOPRIME_EMBEDDING", "c3_batch_000"),
            "BATCH_PRESERVATION": ("BATCH_PRESERVATION", "c3_batch_000"),
            "CANARY_FINALIZATION": ("PRESERVATION_FINALIZATION", "all_batches"),
        }
        for stage_id, (authorization_stage, batch_id) in stage_roles.items():
            grant_value = _stage_grant(
                stage=authorization_stage,
                batch_id=batch_id,
                config=config,
                contract_sha256=configuration["production_contract"],
                plan_sha256=plan_sha,
                owner_date=owner_date,
                launch_authority_sha256=launch_authority_sha256,
            )
            stages.validate_stage_authorization_value(
                grant_value,
                stage=authorization_stage,
                batch_id=batch_id,
                attempt_id=config.run_id,
                governing_commit=config.governing_commit,
                orchestration_contract_sha256=configuration[
                    "production_contract"
                ],
                batch_plan_sha256=plan_sha,
                launch_authority_sha256=launch_authority_sha256,
            )
            grant_path = stage_root / f"{stage_id}.authorization.restricted.json"
            _write_bytes_no_clobber(
                grant_path, _canonical_private_json(grant_value)
            )
            created_files.append(grant_path)
            grants[stage_id] = {
                "stage_id": stage_id,
                **_binding(grant_path),
                "authorized": True,
            }

        packet: dict[str, Any] = {
            "schema_version": execution_authority.SCHEMA_VERSION,
            "artifact_type": execution_authority.ARTIFACT_TYPE,
            "status": execution_authority.STATUS,
            "created_at_utc": now.isoformat(),
            "governing_commit": config.governing_commit,
            "branch": execution_authority.BRANCH,
            "run_id": config.run_id,
            "attempt_id": config.run_id,
            "output_root": str(canary_runs / config.run_id),
            "manifest": {
                "path": str(manifest_path),
                "file_sha256": manifest_file_sha,
                "embedded_sha256": manifest_sha,
            },
            "batch_plan": {
                "path": str(plan_path),
                "file_sha256": plan_file_sha,
                "canonical_sha256": plan_sha,
            },
            "scheduler_plan": {
                "path": str(bound_scheduler_path),
                "file_sha256": scheduler_file_sha,
                "canonical_sha256": scheduler_sha,
            },
            "production_contract": _binding(config.production_contract_path),
            "environment_receipt": _binding(config.environment_receipt_path),
            "checkpoint": _binding(config.checkpoint_path),
            "runtime_authority": runtime_authority,
            "hard_scope": dict(execution_authority.HARD_SCOPE),
            "scheduler": dict(execution_authority.SCHEDULER_SCOPE),
            "stage_authorizations": grants,
            "body_transfer_authorization": _binding(body_path),
            "preselection_authority": {
                **_binding(preselection_path),
                "semantic_sha256": str(
                    preselection["preselection_authority_sha256"]
                ),
            },
            "launch_authority_sha256": launch_authority_sha256,
            "gcloud": {
                "binary_path": str(config.gcloud_binary_path),
                "resolution_receipt_path": str(
                    config.gcloud_resolution_receipt_path
                ),
                "cloudsdk_config_path": str(config.cloudsdk_config_path),
            },
            "crc32c": {
                "python_path": str(config.crc32c_python_path),
                "worker_path": str(config.crc32c_worker_path),
            },
            "owner_authorized": True,
            "authorization_scopes": dict(
                execution_authority.AUTHORIZATION_SCOPES
            ),
            "qsub": _binding(config.qsub_path),
            "stage_worker": _binding(config.stage_worker_path),
            "stage_launcher": _binding(config.stage_launcher_path),
            "requester_pays": {
                "billing_environment_variable": "LVEF_C3_GCP_BILLING_PROJECT",
                "billing_project": config.requester_pays_billing_project,
            },
        }
        if set(packet) != execution_authority.PACKET_KEYS - {
            "authorization_sha256"
        }:
            _fail("CANARY_MATERIALIZATION_PACKET_SCHEMA_INVALID")
        packet["authorization_sha256"] = (
            execution_authority.calculate_authorization_sha256(packet)
        )
        packet_payload = execution_authority.serialize_authorization(packet)
        authorization_file_sha = _write_bytes_no_clobber(
            config.authority_path, packet_payload
        )
        created_files.append(config.authority_path)

        normalized = execution_authority.load_and_validate_execution_authority(
            config.authority_path,
            expected_governing_commit=config.governing_commit,
            require_output_absent=True,
            paths=_authority_paths(config),
        )
        if (
            normalized["authorization_sha256"]
            != packet["authorization_sha256"]
            or normalized["authorization_file_sha256"]
            != authorization_file_sha
        ):
            _fail("CANARY_MATERIALIZATION_PACKET_VALIDATION_MISMATCH")

        lifecycle_root = config.lifecycle_path.parent
        if os.path.lexists(config.lifecycle_path):
            lifecycle_before = canary_state.load_state(
                root=lifecycle_root,
                execution_state_path=config.execution_state_path,
                expected_governing_commit=config.governing_commit,
                expected_run_id=config.run_id,
            )
            if lifecycle_before["current_state"] != "PRECANARY_READY":
                _fail("CANARY_MATERIALIZATION_STATE_NOT_READY")
        else:
            canary_state.initialize_state(
                root=lifecycle_root,
                execution_state_path=config.execution_state_path,
                governing_commit=config.governing_commit,
                run_id=config.run_id,
            )
        state = canary_state.transition_sequence(
            root=lifecycle_root,
            execution_state_path=config.execution_state_path,
            expected_current="PRECANARY_READY",
            targets=("CANARY_AUTHORITY_PREPARED", "CANARY_MANIFEST_SEALED"),
            governing_commit=config.governing_commit,
            run_id=config.run_id,
            reason_codes=(
                "OWNER_PRIVATE_AUTHORITY_PACKET_VALIDATED",
                "EXACT_FIVE_MANIFEST_VALIDATED_AND_SEALED",
            ),
            bindings=(
                {
                    "preselection_authority_file_sha256": _sha256_file(
                        preselection_path
                    ),
                    "preselection_authority_sha256": str(
                        preselection["preselection_authority_sha256"]
                    ),
                },
                {
                    "authorization_sha256": str(
                        packet["authorization_sha256"]
                    ),
                    "authorization_file_sha256": authorization_file_sha,
                    "manifest_sha256": manifest_sha,
                    "manifest_file_sha256": manifest_file_sha,
                    "scheduler_plan_sha256": scheduler_sha,
                },
            ),
        )
        marker = config.transaction_marker_path
        assert marker is not None
        _require_no_symlink_ancestors(marker)
        try:
            os.unlink(marker)
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_MATERIALIZATION_TRANSACTION_FINALIZE_FAILED"
            ) from exc
        if os.path.lexists(marker):
            _fail("CANARY_MATERIALIZATION_TRANSACTION_FINALIZE_FAILED")
        body = manifest["manifest"]
        return MaterializationResult(
            status="PASS_OWNER_PRIVATE_AUTHORITY_MATERIALIZED_AND_MANIFEST_SEALED",
            state=state,
            preselection_identifier_fields=(
                len(
                    {
                        "subject_id",
                        "study_id",
                        "source_object_key",
                        "source_relative_path",
                    }.intersection(preselection)
                )
            ),
            manifest_study_count=int(body["study_count"]),
            manifest_subject_count=int(body["subject_count"]),
            manifest_object_count=int(body["expected_object_count"]),
            manifest_byte_count=int(body["expected_byte_total"]),
            manifest_sha256=manifest_sha,
            manifest_file_sha256=manifest_file_sha,
            batch_plan_sha256=plan_sha,
            scheduler_plan_sha256=scheduler_sha,
            authorization_sha256=str(packet["authorization_sha256"]),
            authorization_file_sha256=authorization_file_sha,
            lifecycle_state=str(state["current_state"]),
            lifecycle_root=lifecycle_root,
            run_id=config.run_id,
            authority_paths=_authority_paths(config),
            manifest_path=manifest_path,
            batch_plan_path=plan_path,
            scheduler_plan_path=bound_scheduler_path,
            authority_path=config.authority_path,
        )
    except CanaryAuthorityMaterializationError as original:
        rollback_error: CanaryAuthorityMaterializationError | None = None
        if lifecycle_checkpointed:
            try:
                _restore_lifecycle(
                    config.lifecycle_path,
                    prior_payload=lifecycle_prior,
                    root_existed=lifecycle_root_existed,
                )
            except CanaryAuthorityMaterializationError as exc:
                rollback_error = exc
        cleanup_ok = _safe_cleanup(created_files, created_directories)
        if rollback_error is not None:
            raise rollback_error from original
        if not cleanup_ok:
            _fail("CANARY_MATERIALIZATION_OUTPUT_ROLLBACK_FAILED")
        raise
    except Exception as exc:
        rollback_error = None
        if lifecycle_checkpointed:
            try:
                _restore_lifecycle(
                    config.lifecycle_path,
                    prior_payload=lifecycle_prior,
                    root_existed=lifecycle_root_existed,
                )
            except CanaryAuthorityMaterializationError as rollback_exc:
                rollback_error = rollback_exc
        cleanup_ok = _safe_cleanup(created_files, created_directories)
        if rollback_error is not None:
            raise rollback_error from exc
        if not cleanup_ok:
            _fail("CANARY_MATERIALIZATION_OUTPUT_ROLLBACK_FAILED")
        code = getattr(exc, "code", "CANARY_AUTHORITY_MATERIALIZATION_FAILED")
        raise CanaryAuthorityMaterializationError(str(code)) from exc


def _write_private_csv(
    path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    output = __import__("io").StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: str(value).lower() if isinstance(value, bool) else value
                for key, value in row.items()
            }
        )
    _write_bytes_no_clobber(path, output.getvalue().encode("utf-8"))


def _strict_csv_rows(payload: bytes, *, delimiter: str = ",") -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_LIVE_ROW_AUTHORITY_INVALID"
        ) from exc
    if not rows or not rows[0] or len(rows[0]) != len(set(rows[0])):
        _fail("CANARY_LIVE_ROW_AUTHORITY_INVALID")
    header = rows[0]
    if any(not name or name.strip() != name for name in header):
        _fail("CANARY_LIVE_ROW_AUTHORITY_INVALID")
    result: list[dict[str, str]] = []
    for cells in rows[1:]:
        if len(cells) != len(header):
            _fail("CANARY_LIVE_ROW_AUTHORITY_INVALID")
        result.append(dict(zip(header, cells)))
    if not result:
        _fail("CANARY_LIVE_ROW_AUTHORITY_EMPTY")
    return result


def _strict_jsonl_rows(payload: bytes) -> list[dict[str, Any]]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                _fail("CANARY_LIVE_ROW_AUTHORITY_DUPLICATE_KEY")
            value[key] = item
        return value

    try:
        lines = payload.decode("utf-8").splitlines()
        rows = [json.loads(line, object_pairs_hook=pairs) for line in lines if line]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_LIVE_ROW_AUTHORITY_INVALID"
        ) from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        _fail("CANARY_LIVE_ROW_AUTHORITY_INVALID")
    return rows


def _bound_payload(
    path: Path,
    *,
    size: int,
    digest: str,
    private: bool,
) -> bytes:
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 1
        or SHA256_RE.fullmatch(str(digest)) is None
    ):
        _fail("CANARY_LIVE_AUTHORITY_BINDING_INVALID")
    payload = _read_regular(path, private=private)
    if len(payload) != size or _sha256(payload) != digest:
        _fail("CANARY_LIVE_AUTHORITY_BINDING_MISMATCH")
    return payload


def _resolve_scheduler_tool(lexical_path: Path) -> Path:
    """Resolve one frozen SCC lexical tool to a root-owned regular binary."""

    lexical = _require_no_symlink_ancestors(lexical_path)
    try:
        link_metadata = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        target_metadata = os.lstat(resolved)
    except (OSError, RuntimeError) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_MATERIALIZATION_SCHEDULER_TOOL_INVALID"
        ) from exc
    if (
        link_metadata.st_uid != 0
        or stat.S_IMODE(link_metadata.st_mode) & 0o022
        or not resolved.is_absolute()
        or not str(resolved).startswith("/usr/local/")
        or not stat.S_ISREG(target_metadata.st_mode)
        or target_metadata.st_uid != 0
        or stat.S_IMODE(target_metadata.st_mode) & 0o022
        or not stat.S_IMODE(target_metadata.st_mode) & stat.S_IXUSR
    ):
        _fail("CANARY_MATERIALIZATION_SCHEDULER_TOOL_INVALID")
    _read_regular(resolved, private=False, executable=True)
    return resolved


def _transaction_marker(config: MaterializationConfig) -> tuple[Path, bytes]:
    path = config.private_root / TRANSACTION_MARKER_NAME
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_materialization_transaction_v1",
        "governing_commit": config.governing_commit,
        "run_id": config.run_id,
        "status": "DISCOVERY_ARTIFACTS_PENDING_SEAL",
    }
    return path, _canonical_private_json(value)


def _preserved_smoke_binding(
    source: MaterializationAuthoritySource,
) -> tuple[int, str]:
    payload = _bound_payload(
        source.prior_smoke_preservation_manifest_path,
        size=source.prior_smoke_preservation_manifest_size,
        digest=source.prior_smoke_preservation_manifest_sha256,
        private=False,
    )
    rows = _strict_csv_rows(payload, delimiter="\t")
    expected_relative = (
        "restricted/source/technical_smoke_source_manifest_restricted.csv"
    )
    matches = [row for row in rows if row.get("relative_path") == expected_relative]
    if len(matches) != 1:
        _fail("CANARY_PRIOR_SMOKE_PRESERVATION_BINDING_INVALID")
    row = matches[0]
    try:
        size = int(row["size_bytes"])
        digest = str(row["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_PRIOR_SMOKE_PRESERVATION_BINDING_INVALID"
        ) from exc
    if size < 1 or SHA256_RE.fullmatch(digest) is None:
        _fail("CANARY_PRIOR_SMOKE_PRESERVATION_BINDING_INVALID")
    return size, digest


def _base_config(
    *,
    source: MaterializationAuthoritySource,
    repository: Path,
    execution_state_path: Path,
    private_root: Path,
    candidate_csv: Path,
    source_csv: Path,
    smoke_size: int,
    smoke_sha256: str,
) -> MaterializationConfig:
    return MaterializationConfig(
        tracked_worktree=repository,
        execution_state_path=execution_state_path,
        private_root=private_root,
        lifecycle_path=private_root / "lifecycle_state" / "canary_state.restricted.json",
        authority_path=private_root / "execution_authorization_v1.json",
        candidate_csv=candidate_csv,
        source_object_csv=source_csv,
        governing_commit=source.governing_commit,
        run_id=source.run_id,
        production_contract_path=repository / "configs/lvef_c3_orchestration_v2.yaml",
        state_machine_path=repository / "configs/lvef_c3_state_machine_v2.json",
        resume_ledger_path=repository / "configs/lvef_c3_resume_ledger_v2.json",
        scheduler_plan_path=repository / "configs/lvef_c3_canary_scheduler_plan_v1.json",
        environment_receipt_path=source.environment_receipt_path,
        checkpoint_path=source.checkpoint_path,
        gcloud_binary_path=source.gcloud_binary_path,
        gcloud_resolution_receipt_path=source.gcloud_resolution_receipt_path,
        cloudsdk_config_path=source.cloudsdk_config_path,
        crc32c_python_path=source.crc32c_python_path,
        crc32c_worker_path=repository / "scripts/lvef_c3_crc32c_worker.py",
        crc32c_distribution_sha256=source.crc32c_distribution_sha256,
        qsub_path=source.qsub_path,
        qstat_path=source.qstat_path,
        native_quota_path=source.native_quota_path,
        pquota_path=source.pquota_path,
        findmnt_path=source.findmnt_path,
        df_path=source.df_path,
        research_path=source.research_path,
        backed_path=source.backed_path,
        stage_worker_path=repository / "scripts/lvef_c3_canary_stage_worker.py",
        stage_launcher_path=repository / "scripts/scc_run_lvef_c3_canary_stage.sh",
        requester_pays_billing_project=source.requester_pays_billing_project,
        source_metadata_sha256=source.source_metadata_sha256,
        split_map_sha256=source.split_map_sha256,
        historical_study_manifest_sha256=source.historical_study_manifest_sha256,
        historical_study_manifest_size=source.historical_study_manifest_size,
        prior_smoke_source_manifest_sha256=smoke_sha256,
        prior_smoke_source_manifest_size=smoke_size,
        prior_smoke_source_summary_sha256=source.prior_smoke_source_summary_sha256,
        prior_smoke_source_summary_size=source.prior_smoke_source_summary_size,
        prior_smoke_source_safety_sha256=source.prior_smoke_source_safety_sha256,
        prior_smoke_source_safety_size=source.prior_smoke_source_safety_size,
        prior_smoke_preservation_manifest_sha256=(
            source.prior_smoke_preservation_manifest_sha256
        ),
        prior_smoke_preservation_manifest_size=(
            source.prior_smoke_preservation_manifest_size
        ),
        launch_authority_sha256="0" * 64,
        synthetic_mode=source.synthetic_mode,
    )


def build_synthetic_materialization_authority_source(
    root: Path,
    *,
    repository: Path,
    governing_commit: str,
    run_id: str = "lvef_c3_exact_five_canary_ab12cd34",
) -> MaterializationAuthoritySource:
    """Build sanitized preserved-authority inputs, not a final canary manifest."""

    root = Path(root).resolve()
    repository = Path(repository).resolve()
    _require_private_directory(root)
    inputs = root / "synthetic-preserved-authorities"
    runtime = root / "synthetic-runtime"
    production = root / "synthetic-production"
    for path in (inputs, runtime, production):
        _require_private_directory(path, create=True)

    def write(path: Path, payload: bytes, mode: int = 0o600) -> Path:
        _write_bytes_no_clobber(path, payload)
        path.chmod(mode)
        return path

    selected_rows: list[dict[str, Any]] = []
    locator_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    historical_rows: list[dict[str, Any]] = []
    for index in range(1, 6):
        subject = str(810_000 + index)
        study = str(910_000 + index)
        relative = (
            f"files/p{int(subject) // 1_000_000:02d}/p{subject}/"
            f"s{study}/synthetic_{index:03d}.dcm"
        )
        key = hashlib.sha256(
            f"{manifest_contract.SOURCE_RELEASE}\0{relative}".encode("utf-8")
        ).hexdigest()
        selected_rows.append({"subject_id": subject, "study_id": study})
        split_rows.append({"subject_id": subject, "split": "train"})
        locator_rows.append(
            {
                "release_id": manifest_contract.SOURCE_RELEASE,
                "subject_id": subject,
                "study_id": study,
                "split": "train",
                "source_object_key": key,
                "source_relative_path": relative,
            }
        )
        metadata_rows.append(
            {
                **locator_rows[-1],
                "production_batch": "c3_batch_000",
                "preflight_status": "PASS",
                "discrepancy_reasons": [],
                "remote_size_bytes": index,
                "remote_generation": str(index),
                "remote_md5_base64": base64.b64encode(bytes([index]) * 16).decode(),
                "remote_crc32c_base64": base64.b64encode(bytes([index]) * 4).decode(),
            }
        )
        historical_rows.append({"study_id": study})

    selected = inputs / "selected-studies.restricted.csv"
    _write_private_csv(selected, ("subject_id", "study_id"), selected_rows)
    locators = inputs / "selected-source.restricted.csv"
    _write_private_csv(
        locators,
        (
            "release_id", "subject_id", "study_id", "split",
            "source_object_key", "source_relative_path",
        ),
        locator_rows,
    )
    metadata = write(
        inputs / "source-metadata.restricted.jsonl",
        b"".join(
            execution_authority.canonical_json_bytes(row) + b"\n"
            for row in metadata_rows
        ),
    )
    split = inputs / "split-map.restricted.csv"
    _write_private_csv(split, ("subject_id", "split"), split_rows)
    historical = inputs / "historical-study-manifest.csv"
    _write_private_csv(historical, ("study_id",), historical_rows)
    smoke = inputs / "technical-smoke-source-manifest.restricted.csv"
    _write_private_csv(
        smoke,
        ("subject_id", "study_id"),
        [{"subject_id": "899999", "study_id": "999999"}],
    )
    summary_value = {
        "schema_version": 2,
        "status": "PASS",
        "historical_study_manifest_sha256": _sha256_file(historical),
        "technical_smoke_source_manifest_sha256": _sha256_file(smoke),
    }
    summary = write(inputs / "source-summary.json", _canonical_private_json(summary_value))
    safety = write(
        inputs / "source-safety.json",
        _canonical_private_json(
            {
                "status": "PASS",
                "aggregate_contains_identifiers": False,
                "aggregate_contains_object_locators": False,
                "source_locator_conflict_gate_passed": True,
                "raw_n_dicoms_reconciliation_gate_passed": True,
            }
        ),
    )
    preservation = inputs / "preservation-manifest.tsv"
    preservation_output = io.StringIO(newline="")
    preservation_writer = csv.writer(
        preservation_output, delimiter="\t", lineterminator="\n"
    )
    preservation_writer.writerow(("relative_path", "size_bytes", "sha256"))
    preservation_writer.writerow(
        (
            "restricted/source/technical_smoke_source_manifest_restricted.csv",
            smoke.stat().st_size,
            _sha256_file(smoke),
        )
    )
    _write_bytes_no_clobber(
        preservation, preservation_output.getvalue().encode("utf-8")
    )

    environment = write(
        inputs / "environment.restricted.json",
        b'{"status":"PASS_SYNTHETIC_OFFLINE_RUNTIME"}\n',
    )
    gcloud_receipt = write(
        inputs / "gcloud-resolution.restricted.json",
        b'{"status":"PASS_SYNTHETIC_NO_CREDENTIAL_ACCESS"}\n',
    )
    cloudsdk = inputs / "cloudsdk"
    _require_private_directory(cloudsdk, create=True)
    write(
        cloudsdk / "application_default_credentials.json",
        b"synthetic placeholder; must never be used\n",
    )
    checkpoint = write(runtime / "echoprime-encoder.pt", b"synthetic checkpoint\n")
    gcloud = write(runtime / "gcloud", b"#!/bin/sh\nexit 97\n", 0o700)
    qsub = write(runtime / "qsub", b"#!/bin/sh\nexit 97\n", 0o700)
    qstat = write(runtime / "qstat", b"#!/bin/sh\nexit 0\n", 0o700)
    crc_python = write(runtime / "crc-python", b"#!/bin/sh\nexit 97\n", 0o700)
    required_kib = (
        capacity_authority.CANARY_REQUIRED_REMAINING_PROJECT_BYTES // 1024
    )
    research_usage_kib = capacity_authority.EXPECTED_RESEARCH_QUOTA_KIB - required_kib
    research_files_used = (
        capacity_authority.EXPECTED_RESEARCH_FILE_QUOTA
        - capacity_authority.CANARY_REQUIRED_REMAINING_FILE_SLOTS
    )
    native_quota = write(
        runtime / "project.quota",
        (
            "rproject_mimicecho root FILESET 1 52428800 0 0 none | "
            "1 1638400 0 0 none\n"
            "rprojectnb_mimicecho root FILESET "
            f"{research_usage_kib} {capacity_authority.EXPECTED_RESEARCH_QUOTA_KIB} "
            "0 0 none | "
            f"{research_files_used} {capacity_authority.EXPECTED_RESEARCH_FILE_QUOTA} "
            "0 0 none\n"
        ).encode("ascii"),
        0o600,
    )
    pquota = write(runtime / "pquota", b"#!/bin/sh\nexit 1\n", 0o700)
    research = root / "synthetic-research"
    backed = root / "synthetic-backed"
    _require_private_directory(research, create=True)
    _require_private_directory(backed, create=True)
    findmnt = write(
        runtime / "findmnt",
        (
            "#!/bin/sh\n"
            "target=\nprevious=\n"
            "for argument do\n"
            "  if [ \"$previous\" = --target ]; then target=$argument; fi\n"
            "  previous=$argument\n"
            "done\n"
            "case $target in *synthetic-research) role=research ;; *) role=backed ;; esac\n"
            "printf '{\"filesystems\":[{\"source\":\"synthetic:/%s\",' \"$role\"\n"
            "printf '\"target\":\"%s\",\"fstype\":\"syntheticfs\",' \"$target\"\n"
            "printf '\"options\":\"rw\",\"fsroot\":\"/\"}]}\\n'\n"
        ).encode("ascii"),
        0o700,
    )
    df = write(
        runtime / "df",
        (
            "#!/bin/sh\n"
            "target=\nfor argument do target=$argument; done\n"
            "case $target in *synthetic-research) role=research ;; *) role=backed ;; esac\n"
            "printf 'Filesystem 1B-blocks Used Avail Mounted on\\n'\n"
            "printf 'synthetic:/%s 20000000001 1 20000000000 %s\\n' \"$role\" \"$target\"\n"
        ).encode("ascii"),
        0o700,
    )
    return MaterializationAuthoritySource(
        governing_commit=governing_commit,
        run_id=run_id,
        production_root=production,
        environment_receipt_path=environment,
        checkpoint_path=checkpoint,
        gcloud_binary_path=gcloud,
        gcloud_resolution_receipt_path=gcloud_receipt,
        cloudsdk_config_path=cloudsdk,
        crc32c_python_path=crc_python,
        crc32c_distribution_sha256="d" * 64,
        requester_pays_billing_project="synthetic-private-project",
        selected_studies_path=selected,
        selected_studies_size=selected.stat().st_size,
        selected_studies_sha256=_sha256_file(selected),
        selected_source_path=locators,
        selected_source_size=locators.stat().st_size,
        selected_source_sha256=_sha256_file(locators),
        source_metadata_path=metadata,
        source_metadata_size=metadata.stat().st_size,
        source_metadata_sha256=_sha256_file(metadata),
        split_map_path=split,
        split_map_size=split.stat().st_size,
        split_map_sha256=_sha256_file(split),
        historical_study_manifest_path=historical,
        historical_study_manifest_size=historical.stat().st_size,
        historical_study_manifest_sha256=_sha256_file(historical),
        prior_smoke_source_manifest_path=smoke,
        prior_smoke_source_manifest_size=smoke.stat().st_size,
        prior_smoke_source_manifest_sha256=_sha256_file(smoke),
        prior_smoke_source_summary_path=summary,
        prior_smoke_source_summary_size=summary.stat().st_size,
        prior_smoke_source_summary_sha256=_sha256_file(summary),
        prior_smoke_source_safety_path=safety,
        prior_smoke_source_safety_size=safety.stat().st_size,
        prior_smoke_source_safety_sha256=_sha256_file(safety),
        prior_smoke_preservation_manifest_path=preservation,
        prior_smoke_preservation_manifest_size=preservation.stat().st_size,
        prior_smoke_preservation_manifest_sha256=_sha256_file(preservation),
        qsub_path=qsub,
        qstat_path=qstat,
        native_quota_path=native_quota,
        pquota_path=pquota,
        findmnt_path=findmnt,
        df_path=df,
        research_path=research,
        backed_path=backed,
        synthetic_mode=True,
    )


def _discover_scc_authority_source(
    *,
    repository: Path,
    execution_state_path: Path,
    private_authority_config: Any,
) -> MaterializationAuthoritySource:
    """Resolve metadata-only SCC authorities without opening row-bearing files."""

    recovery = private_authority_config.recovery
    state = phase1eg_authority.load_canonical_state(recovery)
    paths = phase1eg_authority.derive_execution_paths(recovery, state)
    phase1eg_authority.validate_attempts_and_capacity(state, paths)
    preparation_root, values = phase1eg_authority.discover_preparation(recovery, state)
    phase1eg_authority.validate_preparation_binding(
        recovery, state, preparation_root, values
    )
    authorities = phase1eg_authority.resolve_private_authorities(
        recovery, state, paths, private_authority_config.current_environment_commit
    )
    phase1eg_authority.validate_current_receipt(
        authorities.output_receipt,
        private_authority_config.current_environment_commit,
        prior_environment_sha256=authorities.prior_environment_sha256,
    )
    environment = phase1eg_authority.load_strict_json(authorities.output_receipt)
    git_result = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=repository,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    governing_commit = git_result.stdout.strip()
    if git_result.returncode != 0 or COMMIT_RE.fullmatch(governing_commit) is None:
        _fail("CANARY_MATERIALIZATION_GIT_HEAD_INVALID")
    run_id = f"lvef_c3_exact_five_canary_{governing_commit[:8]}"
    historical = (
        recovery.worktree.parent
        / "Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/"
        "study_embeddings_512/study_embedding_manifest.csv"
    )
    smoke_root = Path("/restricted/project/mimicecho/audits") / PRIOR_SMOKE_RUN_NAME
    smoke = smoke_root / "restricted/source/technical_smoke_source_manifest_restricted.csv"
    summary = smoke_root / "aggregate/source/reconstruction_source_manifest.summary.json"
    safety = smoke_root / "aggregate/source/reconstruction_source_manifest_safety_gate.json"
    preservation = Path(f"{smoke_root}_preservation") / "preservation_manifest.tsv"

    def metadata_size(path: Path, *, private: bool) -> int:
        path = _require_no_symlink_ancestors(path)
        try:
            value = os.lstat(path)
        except OSError as exc:
            raise CanaryAuthorityMaterializationError(
                "CANARY_LIVE_AUTHORITY_BINDING_MISSING"
            ) from exc
        mode = stat.S_IMODE(value.st_mode)
        if (
            stat.S_ISLNK(value.st_mode)
            or not stat.S_ISREG(value.st_mode)
            or value.st_size < 1
            or (private and (value.st_uid != os.geteuid() or mode != 0o600))
            or (not private and mode & 0o022)
        ):
            _fail("CANARY_LIVE_AUTHORITY_BINDING_INVALID")
        return int(value.st_size)

    qsub = _resolve_scheduler_tool(SCC_QSUB_LEXICAL_PATH)
    qstat = _resolve_scheduler_tool(SCC_QSTAT_LEXICAL_PATH)
    summary_payload = _read_regular(summary, private=False)
    safety_payload = _read_regular(safety, private=False)
    try:
        summary_value = json.loads(summary_payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryAuthorityMaterializationError(
            "CANARY_PRIOR_SOURCE_AUTHORITY_INVALID"
        ) from exc
    smoke_sha256 = (
        summary_value.get("technical_smoke_source_manifest_sha256")
        if isinstance(summary_value, Mapping)
        else None
    )
    if (
        not isinstance(smoke_sha256, str)
        or SHA256_RE.fullmatch(smoke_sha256) is None
        or summary_value.get("historical_study_manifest_sha256")
        != HISTORICAL_STUDY_MANIFEST_SHA256
    ):
        _fail("CANARY_PRIOR_SOURCE_AUTHORITY_INVALID")
    preservation_size = metadata_size(preservation, private=False)
    return MaterializationAuthoritySource(
        governing_commit=governing_commit,
        run_id=run_id,
        production_root=recovery.production_root,
        environment_receipt_path=authorities.output_receipt,
        checkpoint_path=Path(values["CHECKPOINT"]),
        gcloud_binary_path=Path(values["GCLOUD"]),
        gcloud_resolution_receipt_path=Path(values["GCLOUD_RECEIPT"]),
        cloudsdk_config_path=Path(values["CLOUDSDK_CONFIG"]),
        crc32c_python_path=authorities.crc32c_python,
        crc32c_distribution_sha256=str(environment["google_crc32c_distribution_sha256"]),
        requester_pays_billing_project=str(values["LVEF_C3_GCP_BILLING_PROJECT"]),
        selected_studies_path=Path(values["SELECTED_STUDIES"]),
        selected_studies_size=int(values["SELECTED_STUDIES_EXPECTED_SIZE"]),
        selected_studies_sha256=str(values["SELECTED_STUDIES_EXPECTED_SHA"]),
        selected_source_path=Path(values["SELECTED_SOURCE"]),
        selected_source_size=int(values["SELECTED_SOURCE_EXPECTED_SIZE"]),
        selected_source_sha256=str(values["SELECTED_SOURCE_EXPECTED_SHA"]),
        source_metadata_path=Path(values["SOURCE_METADATA"]),
        source_metadata_size=int(values["SOURCE_METADATA_EXPECTED_SIZE"]),
        source_metadata_sha256=str(values["SOURCE_METADATA_EXPECTED_SHA"]),
        split_map_path=Path(values["SPLIT_MAP"]),
        split_map_size=int(values["SPLIT_MAP_EXPECTED_SIZE"]),
        split_map_sha256=str(values["SPLIT_MAP_EXPECTED_SHA"]),
        historical_study_manifest_path=historical,
        historical_study_manifest_size=metadata_size(historical, private=False),
        historical_study_manifest_sha256=HISTORICAL_STUDY_MANIFEST_SHA256,
        prior_smoke_source_manifest_path=smoke,
        prior_smoke_source_manifest_size=metadata_size(smoke, private=True),
        prior_smoke_source_manifest_sha256=smoke_sha256,
        prior_smoke_source_summary_path=summary,
        prior_smoke_source_summary_size=len(summary_payload),
        prior_smoke_source_summary_sha256=_sha256(summary_payload),
        prior_smoke_source_safety_path=safety,
        prior_smoke_source_safety_size=len(safety_payload),
        prior_smoke_source_safety_sha256=_sha256(safety_payload),
        prior_smoke_preservation_manifest_path=preservation,
        prior_smoke_preservation_manifest_size=preservation_size,
        prior_smoke_preservation_manifest_sha256=(
            PRIOR_SMOKE_PRESERVATION_MANIFEST_SHA256
        ),
        qsub_path=qsub,
        qstat_path=qstat,
        native_quota_path=capacity_authority.EXPECTED_NATIVE_QUOTA_FILE,
        pquota_path=capacity_authority.EXPECTED_PQUOTA_EXECUTABLE,
        findmnt_path=SCC_FINDMNT_PATH,
        df_path=SCC_DF_PATH,
        research_path=capacity_authority.EXPECTED_RESTRICTED_PATHS["research"],
        backed_path=capacity_authority.EXPECTED_RESTRICTED_PATHS["backed"],
        synthetic_mode=False,
    )


def discover_live_materialization_config(
    *,
    repository: Path,
    execution_state_path: Path,
    private_authority_config: Any | None = None,
    authority_source: MaterializationAuthoritySource | None = None,
    probes: MaterializationProbes | None = None,
) -> MaterializationConfig:
    """One gated transaction from preserved authorities to sealed input rows."""

    repository = Path(repository).resolve()
    execution_state_path = Path(execution_state_path).resolve()
    probes = probes or MaterializationProbes()
    if authority_source is None:
        if private_authority_config is None:
            _fail("CANARY_LIVE_AUTHORITY_SOURCE_MISSING")
        authority_source = _discover_scc_authority_source(
            repository=repository,
            execution_state_path=execution_state_path,
            private_authority_config=private_authority_config,
        )
    source = authority_source
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        git_result = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        )
        if git_result.returncode != 0 or git_result.stdout.strip() != source.governing_commit:
            _fail("CANARY_MATERIALIZATION_GIT_HEAD_INVALID")
        tracked = tracked_execution_state.load_execution_state(execution_state_path)
        if (
            not tracked.permits("prepare_exact_five_canary_authority")
            or not tracked.permits("seal_exact_five_canary_manifest")
            or not probes.attempt_probe(tracked)
        ):
            _fail("CANARY_MATERIALIZATION_ATTEMPT_INVARIANT_INVALID")

        owner_parent = source.production_root / "owner_private"
        if _require_private_directory(owner_parent, create=True):
            created_directories.append(owner_parent)
        private_root = owner_parent / "exact_five_canary"
        if _require_private_directory(private_root, create=True):
            created_directories.append(private_root)
        inputs_root = private_root / "source_authority_inputs"
        if _require_private_directory(inputs_root, create=True):
            created_directories.append(inputs_root)
        candidate_csv = inputs_root / "candidate-studies.restricted.csv"
        source_csv = inputs_root / "source-objects.restricted.csv"

        config = _base_config(
            source=source,
            repository=repository,
            execution_state_path=execution_state_path,
            private_root=private_root,
            candidate_csv=candidate_csv,
            source_csv=source_csv,
            smoke_size=source.prior_smoke_source_manifest_size,
            smoke_sha256=source.prior_smoke_source_manifest_sha256,
        )
        _validate_config(config)
        now = probes.clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            _fail("CANARY_MATERIALIZATION_CLOCK_INVALID")

        marker_path, marker_payload = _transaction_marker(config)
        marker_sha = _write_bytes_no_clobber(marker_path, marker_payload)
        created_files.append(marker_path)
        preselection = _preselection_scope(config, now)
        preselection_path = private_root / "preselection_scope.restricted.json"
        _write_bytes_no_clobber(
            preselection_path,
            execution_authority.serialize_preselection_authority(preselection),
        )
        created_files.append(preselection_path)

        # The identifier-free receipt is durable before the scheduler/process
        # and current quota probes.  Rebuilding it immediately before qstat
        # proves the sealed qsub/qstat inode identities are still current.
        if _preselection_scope(config, now) != preselection:
            _fail("CANARY_MATERIALIZATION_SCHEDULER_TOOL_CHANGED")
        if not probes.conflict_probe(config):
            _fail("CANARY_MATERIALIZATION_CONFLICT_PRESENT")
        if not probes.quota_probe(config):
            _fail("CANARY_MATERIALIZATION_QUOTA_INSUFFICIENT")
        # Row-bearing authority access starts only after the conflict and
        # contemporaneous byte/file headroom gates pass.
        smoke_size, smoke_sha256 = _preserved_smoke_binding(source)
        if (
            source.prior_smoke_source_manifest_size != smoke_size
            or source.prior_smoke_source_manifest_sha256 != smoke_sha256
        ):
            _fail("CANARY_PRIOR_SMOKE_PRESERVATION_BINDING_INVALID")

        selected_rows = _strict_csv_rows(
            _bound_payload(
                source.selected_studies_path,
                size=source.selected_studies_size,
                digest=source.selected_studies_sha256,
                private=True,
            )
        )
        selected_source_rows = _strict_csv_rows(
            _bound_payload(
                source.selected_source_path,
                size=source.selected_source_size,
                digest=source.selected_source_sha256,
                private=True,
            )
        )
        metadata_rows = _strict_jsonl_rows(
            _bound_payload(
                source.source_metadata_path,
                size=source.source_metadata_size,
                digest=source.source_metadata_sha256,
                private=True,
            )
        )
        split_rows = _strict_csv_rows(
            _bound_payload(
                source.split_map_path,
                size=source.split_map_size,
                digest=source.split_map_sha256,
                private=True,
            )
        )
        historical_rows = _strict_csv_rows(
            _bound_payload(
                source.historical_study_manifest_path,
                size=source.historical_study_manifest_size,
                digest=source.historical_study_manifest_sha256,
                private=False,
            )
        )
        smoke_rows = _strict_csv_rows(
            _bound_payload(
                source.prior_smoke_source_manifest_path,
                size=smoke_size,
                digest=smoke_sha256,
                private=True,
            )
        )
        summary = json.loads(
            _bound_payload(
                source.prior_smoke_source_summary_path,
                size=source.prior_smoke_source_summary_size,
                digest=source.prior_smoke_source_summary_sha256,
                private=False,
            )
        )
        safety = json.loads(
            _bound_payload(
                source.prior_smoke_source_safety_path,
                size=source.prior_smoke_source_safety_size,
                digest=source.prior_smoke_source_safety_sha256,
                private=False,
            )
        )
        if (
            not isinstance(summary, Mapping)
            or summary.get("status") != "PASS"
            or summary.get("schema_version") != 2
            or summary.get("historical_study_manifest_sha256")
            != source.historical_study_manifest_sha256
            or summary.get("technical_smoke_source_manifest_sha256") != smoke_sha256
            or not isinstance(safety, Mapping)
            or safety.get("status") != "PASS"
            or safety.get("aggregate_contains_identifiers") is not False
            or safety.get("aggregate_contains_object_locators") is not False
            or safety.get("source_locator_conflict_gate_passed") is not True
            or safety.get("raw_n_dicoms_reconciliation_gate_passed") is not True
        ):
            _fail("CANARY_PRIOR_SOURCE_AUTHORITY_INVALID")

        normalized = core.reconcile_selected_source_metadata(
            selected_source_rows, metadata_rows, release=manifest_contract.SOURCE_RELEASE
        )
        historical_studies = {str(row["study_id"]) for row in historical_rows}
        smoke_studies = {str(row["study_id"]) for row in smoke_rows}
        split_by_subject: dict[str, str] = {}
        for row in split_rows:
            subject = str(row["subject_id"])
            split = str(row["split"])
            if subject in split_by_subject or split not in {"train", "val", "test"}:
                _fail("CANARY_LIVE_SPLIT_MAP_INVALID")
            split_by_subject[subject] = split
        objects_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        source_projection: list[dict[str, Any]] = []
        for row in normalized:
            subject, study = str(row["subject_id"]), str(row["study_id"])
            if str(row["split"]) != split_by_subject.get(subject):
                _fail("CANARY_LIVE_SPLIT_BINDING_MISMATCH")
            projected = {
                "subject_id": subject,
                "study_id": study,
                "split": str(row["split"]),
                "source_object_key": str(row["source_object_key"]),
                "source_relative_path": str(row["source_relative_path"]),
                "size_bytes": int(row["remote_size_bytes"]),
                "generation": str(row["remote_generation"]),
                "md5_base64": str(row["remote_md5_base64"]),
                "crc32c_base64": str(row["remote_crc32c_base64"]),
            }
            objects_by_pair.setdefault((subject, study), []).append(projected)
            source_projection.append(projected)
        candidates: list[dict[str, Any]] = []
        seen_selected: set[tuple[str, str]] = set()
        for row in selected_rows:
            subject, study = str(row["subject_id"]), str(row["study_id"])
            pair = (subject, study)
            if pair in seen_selected or subject not in split_by_subject:
                _fail("CANARY_LIVE_SELECTED_AUTHORITY_INVALID")
            seen_selected.add(pair)
            objects = objects_by_pair.get(pair, [])
            if not objects:
                _fail("CANARY_LIVE_SELECTED_OBJECTS_MISSING")
            candidates.append(
                {
                    "study_id": study,
                    "subject_id": subject,
                    "split": split_by_subject[subject],
                    "expected_object_count": len(objects),
                    "expected_byte_total": sum(int(item["size_bytes"]) for item in objects),
                    "known_no_cine": study not in historical_studies,
                    "prior_reconstruction_smoke": study in smoke_studies,
                }
            )
        if set(objects_by_pair) != seen_selected:
            _fail("CANARY_LIVE_SOURCE_SCOPE_MISMATCH")
        candidates.sort(key=lambda row: (int(row["subject_id"]), int(row["study_id"])))
        source_projection.sort(
            key=lambda row: (
                int(row["subject_id"]), int(row["study_id"]), row["source_relative_path"]
            )
        )
        _write_private_csv(candidate_csv, manifest_contract.CANDIDATE_COLUMNS, candidates)
        created_files.append(candidate_csv)
        _write_private_csv(source_csv, manifest_contract.SOURCE_OBJECT_COLUMNS, source_projection)
        created_files.append(source_csv)
        return replace(
            config,
            launch_authority_sha256=str(preselection["preselection_authority_sha256"]),
            transaction_created_files=tuple(created_files),
            transaction_created_directories=tuple(created_directories),
            transaction_marker_path=marker_path,
            transaction_marker_sha256=marker_sha,
        )
    except CanaryAuthorityMaterializationError:
        if not _safe_cleanup(created_files, created_directories):
            _fail("CANARY_MATERIALIZATION_OUTPUT_ROLLBACK_FAILED")
        raise
    except Exception as exc:
        if not _safe_cleanup(created_files, created_directories):
            _fail("CANARY_MATERIALIZATION_OUTPUT_ROLLBACK_FAILED")
        code = getattr(exc, "code", "CANARY_LIVE_MATERIALIZATION_BINDING_MISSING")
        raise CanaryAuthorityMaterializationError(str(code)) from exc


def build_synthetic_materialization_config(
    root: Path,
    *,
    repository: Path,
    governing_commit: str,
    run_id: str = "lvef_c3_exact_five_canary_ab12cd34",
) -> MaterializationConfig:
    """Compatibility wrapper routed through the common discovery transaction."""

    source = build_synthetic_materialization_authority_source(
        root, repository=repository, governing_commit=governing_commit, run_id=run_id
    )
    return discover_live_materialization_config(
        repository=repository,
        execution_state_path=repository / "configs/lvef_c3_execution_state_v1.yaml",
        authority_source=source,
    )


__all__ = [
    "CanaryAuthorityMaterializationError",
    "MaterializationAuthoritySource",
    "MaterializationConfig",
    "MaterializationProbes",
    "MaterializationResult",
    "build_synthetic_materialization_authority_source",
    "build_synthetic_materialization_config",
    "discover_live_materialization_config",
    "prepare_live_authority",
]
