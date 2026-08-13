#!/usr/bin/env python3
"""Minimal, single-job exact-five MIMIC-IV-ECHO canary adapter.

The controlling input is one canonical, owner-private sealed manifest.  This
module deliberately has no execution packet, stage grant, scheduler DAG,
attempt counter, or mutable tracked state.  The live job calls the existing
production functions sequentially and stops on the first failure.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Final


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = SCRIPT_ROOT.parent
CONFIG_ROOT: Final = REPOSITORY_ROOT / "configs"
CONTRACT_PATH: Final = CONFIG_ROOT / "lvef_c3_orchestration_v2.yaml"
STATE_MACHINE_PATH: Final = CONFIG_ROOT / "lvef_c3_state_machine_v2.json"
RESUME_LEDGER_PATH: Final = CONFIG_ROOT / "lvef_c3_resume_ledger_v2.json"
AUDIT_ROOT: Final = Path("/restricted/projectnb/mimicecho/audits")
SESSION_AUTHORITY_PATH: Final = (
    AUDIT_ROOT / "lvef_multitask_phase1ebc_session.env"
)
PRODUCTION_ROOT: Final = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
CHECKPOINT_PATH: Final = Path(
    "/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt"
)
CRC32C_PYTHON_PATH: Final = Path(
    "/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/"
    "google-cloud-sdk/platform/bundledpythonunix/bin/python3.14"
)
QSUB_PATH: Final = Path(
    "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
)
SAFE_TEMPORARY_ROOT: Final = (
    Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
)
EXPECTED_BRANCH: Final = "codex/lvef-multitask-revalidation"
BATCH_ID: Final = "c3_batch_000"
ORDERED_STAGES: Final = (
    "DOWNLOAD",
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "CANARY_FINALIZATION",
)
MAX_OBJECTS: Final = 750
MAX_BYTES: Final = 5_000_000_000
CURRENT_ENVIRONMENT_COMMIT: Final = (
    "0bfcba9973fa6592ca75aa23c6cf0e42d432c5cb"
)
CURRENT_ENVIRONMENT_BYTES: Final = 6_026
CURRENT_ENVIRONMENT_SHA256: Final = (
    "182a03baa5b66b103fe80a3d4b4f3ab2941b7c5e6abf368d5f9b49781a8b8683"
)
CURRENT_ENVIRONMENT_DIRECTORY_RE: Final = re.compile(
    r"^lvef_multitask_phase1ef_d3_environment_diagnostic_[A-Za-z0-9]{8}$"
)
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
ENV_NAME_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENV_VALUE_RE: Final = re.compile(r"^[A-Za-z0-9_@%+,./:=-]+$")


class MinimalCanaryError(RuntimeError):
    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "MINIMAL_CANARY_INVALID"
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise MinimalCanaryError(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _require_nonsymlink_components(path: Path) -> None:
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _fail("MINIMAL_AUTHORITY_PATH_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise MinimalCanaryError("MINIMAL_AUTHORITY_ANCESTOR_INVALID") from exc
        if stat.S_ISLNK(info.st_mode):
            _fail("MINIMAL_AUTHORITY_ANCESTOR_INVALID")


def _read_regular(path: Path, *, private: bool = False) -> bytes:
    _require_nonsymlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_AUTHORITY_FILE_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (private and (opened.st_uid != os.geteuid() or mode != 0o600))
            or (not private and mode & 0o022)
        ):
            _fail("MINIMAL_AUTHORITY_FILE_INVALID")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            after.st_size != len(payload)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            _fail("MINIMAL_AUTHORITY_FILE_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def validate_private_directory(path: Path, *, expected_group: int | None = None) -> None:
    """Accept owner 0700 with optional inherited setgid, never group/other access."""

    _require_nonsymlink_components(path)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_PRIVATE_DIRECTORY_INVALID") from exc
    mode = stat.S_IMODE(info.st_mode)
    special = mode & 0o7000
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or (expected_group is not None and info.st_gid != expected_group)
        or mode & 0o700 != 0o700
        or mode & 0o077
        or special not in {0, stat.S_ISGID}
    ):
        _fail("MINIMAL_PRIVATE_DIRECTORY_INVALID")


def _parse_literal_environment(
    path: Path, *, required_names: frozenset[str] | None = None
) -> dict[str, str]:
    """Parse literal required assignments without ever sourcing shell text."""

    text = _read_regular(path, private=True).decode("utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            if required_names is None:
                _fail("MINIMAL_PRIVATE_ENVIRONMENT_INVALID")
            continue
        name, value = line.split("=", 1)
        if required_names is not None and name not in required_names:
            continue
        if (
            ENV_NAME_RE.fullmatch(name) is None
            or name in result
            or not value
            or ENV_VALUE_RE.fullmatch(value) is None
        ):
            _fail("MINIMAL_PRIVATE_ENVIRONMENT_INVALID")
        result[name] = value
    if required_names is not None and set(result) != set(required_names):
        _fail("MINIMAL_PRIVATE_ENVIRONMENT_INCOMPLETE")
    return result


def _git(*arguments: str, repository: Path = REPOSITORY_ROOT) -> str:
    value = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    if value.returncode:
        _fail("MINIMAL_GIT_AUTHORITY_INVALID")
    return value.stdout.strip()


@dataclass(frozen=True)
class LiveAuthority:
    governing_commit: str
    selection_authority_commit: str
    checkpoint: Path
    checkpoint_sha256: str
    environment_receipt: Path
    environment_receipt_sha256: str
    gcloud: Path
    gcloud_sha256: str
    gcloud_receipt: Path
    gcloud_receipt_sha256: str
    cloudsdk_config: Path
    crc32c_python: Path
    crc32c_worker: Path
    crc32c_python_sha256: str
    crc32c_worker_sha256: str
    crc32c_distribution_sha256: str
    billing_variable: str
    billing_project: str
    selected_studies: Path
    selected_studies_sha256: str
    selected_source: Path
    selected_source_sha256: str
    source_metadata: Path
    split_map: Path
    split_map_sha256: str


def _validate_row_authority_metadata(path: Path) -> int:
    """Validate a row-bearing file without opening or hashing its body."""

    _require_nonsymlink_components(path)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_ROW_AUTHORITY_INVALID") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_size < 1
    ):
        _fail("MINIMAL_ROW_AUTHORITY_INVALID")
    return int(info.st_size)


def _git_is_ancestor(commit: str, head: str, *, repository: Path) -> bool:
    if COMMIT_RE.fullmatch(commit) is None or COMMIT_RE.fullmatch(head) is None:
        return False
    completed = subprocess.run(
        ["/usr/bin/git", "merge-base", "--is-ancestor", commit, head],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    return completed.returncode == 0


def _discover_current_environment_receipt(*, repository: Path) -> Path:
    try:
        roots = [
            item
            for item in AUDIT_ROOT.iterdir()
            if CURRENT_ENVIRONMENT_DIRECTORY_RE.fullmatch(item.name)
        ]
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_CURRENT_ENVIRONMENT_MISSING") from exc
    if len(roots) != 1:
        _fail("MINIMAL_CURRENT_ENVIRONMENT_AMBIGUOUS")
    try:
        expected_group = os.lstat(PRODUCTION_ROOT.parent).st_gid
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_PRODUCTION_ROOT_INVALID") from exc
    validate_private_directory(PRODUCTION_ROOT, expected_group=expected_group)
    validate_private_directory(roots[0], expected_group=expected_group)
    receipt = roots[0] / (
        f"current_environment_{CURRENT_ENVIRONMENT_COMMIT}.restricted.json"
    )
    payload = _read_regular(receipt, private=True)
    if (
        len(payload) != CURRENT_ENVIRONMENT_BYTES
        or _sha256_bytes(payload) != CURRENT_ENVIRONMENT_SHA256
    ):
        _fail("MINIMAL_CURRENT_ENVIRONMENT_IDENTITY_MISMATCH")
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MinimalCanaryError("MINIMAL_CURRENT_ENVIRONMENT_INVALID") from exc
    import capture_lvef_c3_production_environment as environment_capture

    environment_capture.validate_receipt_schema(value)
    if (
        value.get("governing_commit") != CURRENT_ENVIRONMENT_COMMIT
        or not _git_is_ancestor(
            CURRENT_ENVIRONMENT_COMMIT,
            _git("rev-parse", "HEAD", repository=repository),
            repository=repository,
        )
    ):
        _fail("MINIMAL_CURRENT_ENVIRONMENT_COMMIT_MISMATCH")
    return receipt


def discover_live_authority(*, repository: Path = REPOSITORY_ROOT) -> LiveAuthority:
    """Read only fixed current authorities; never open a selected-study row body."""

    import lvef_c3_orchestration_core as core
    import lvef_c3_production_stages as production_stages

    session_names = frozenset(
        {
            "EXPECTED_COMMIT", "PREFLIGHT_ENV", "RUN_ROOT", "PYTHON",
            "SELECTED_STUDIES", "EXPECTED_SELECTED_STUDIES_SHA256",
            "SELECTED_SOURCE_MANIFEST", "EXPECTED_SELECTED_SOURCE_SHA256",
            "SPLIT_MAP", "EXPECTED_SPLIT_MAP_SHA256", "GCLOUD",
            "GCLOUD_RESOLUTION_RECORD",
            "EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256", "CLOUDSDK_CONFIG",
        }
    )
    values = _parse_literal_environment(
        SESSION_AUTHORITY_PATH, required_names=session_names
    )
    billing_values = _parse_literal_environment(
        Path(values["PREFLIGHT_ENV"]),
        required_names=frozenset({"LVEF_C3_GCP_BILLING_PROJECT"}),
    )
    head = _git("rev-parse", "HEAD", repository=repository)
    selection_commit = values["EXPECTED_COMMIT"]
    if not _git_is_ancestor(selection_commit, head, repository=repository):
        _fail("MINIMAL_SELECTION_COMMIT_NOT_ANCESTOR")

    try:
        expected_group = os.lstat(PRODUCTION_ROOT.parent).st_gid
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_PRODUCTION_ROOT_INVALID") from exc
    cloudsdk = Path(values["CLOUDSDK_CONFIG"])
    validate_private_directory(cloudsdk, expected_group=expected_group)
    environment = _discover_current_environment_receipt(repository=repository)
    production_stages.validate_checkpoint_and_environment(
        CHECKPOINT_PATH,
        environment,
        crc32c_python=CRC32C_PYTHON_PATH,
        crc32c_worker=SCRIPT_ROOT / "lvef_c3_crc32c_worker.py",
    )

    selected_studies = Path(values["SELECTED_STUDIES"])
    selected_source = Path(values["SELECTED_SOURCE_MANIFEST"])
    split_map = Path(values["SPLIT_MAP"])
    source_metadata = (
        Path(values["RUN_ROOT"])
        / "restricted/source_preflight/c3_full_source_object_metadata.restricted.jsonl"
    )
    for row_path in (selected_studies, selected_source, split_map, source_metadata):
        _validate_row_authority_metadata(row_path)
    expected_row_hashes = (
        (
            values["EXPECTED_SELECTED_STUDIES_SHA256"],
            core.EXPECTED_SELECTED_MANIFEST_SHA256,
        ),
        (
            values["EXPECTED_SELECTED_SOURCE_SHA256"],
            core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256,
        ),
        (values["EXPECTED_SPLIT_MAP_SHA256"], core.EXPECTED_SPLIT_MAP_SHA256),
    )
    if any(observed != frozen for observed, frozen in expected_row_hashes):
        _fail("MINIMAL_ROW_AUTHORITY_HASH_BINDING_MISMATCH")

    gcloud = Path(values["GCLOUD"])
    gcloud_receipt = Path(values["GCLOUD_RESOLUTION_RECORD"])
    receipt_sha = values["EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"]
    if SHA256_RE.fullmatch(receipt_sha) is None:
        _fail("MINIMAL_GCLOUD_AUTHORITY_INVALID")
    provider = core.GcloudADCTokenProvider(
        gcloud,
        cloudsdk_config=cloudsdk,
        authority_receipt=gcloud_receipt,
        authority_receipt_sha256=receipt_sha,
    )
    observed_gcloud = provider.validate_authority()
    crc_worker = SCRIPT_ROOT / "lvef_c3_crc32c_worker.py"
    crc_python_sha = _sha256_bytes(_read_regular(CRC32C_PYTHON_PATH))
    crc_worker_sha = _sha256_bytes(_read_regular(crc_worker))
    environment_value = core.load_strict_json(environment)
    billing = billing_values["LVEF_C3_GCP_BILLING_PROJECT"]
    if re.fullmatch(r"[a-z][a-z0-9-]{4,62}[a-z0-9]", billing) is None:
        _fail("MINIMAL_REQUESTER_PAYS_AUTHORITY_INVALID")
    core.validate_private_billing_environment(
        "LVEF_C3_GCP_BILLING_PROJECT",
        argv=(),
        environ={"LVEF_C3_GCP_BILLING_PROJECT": billing},
    )
    return LiveAuthority(
        governing_commit=head,
        selection_authority_commit=selection_commit,
        checkpoint=CHECKPOINT_PATH,
        checkpoint_sha256=core.EXPECTED_CHECKPOINT_SHA256,
        environment_receipt=environment,
        environment_receipt_sha256=CURRENT_ENVIRONMENT_SHA256,
        gcloud=gcloud,
        gcloud_sha256=observed_gcloud["gcloud_executable_sha256"],
        gcloud_receipt=gcloud_receipt,
        gcloud_receipt_sha256=receipt_sha,
        cloudsdk_config=cloudsdk,
        crc32c_python=CRC32C_PYTHON_PATH,
        crc32c_worker=crc_worker,
        crc32c_python_sha256=crc_python_sha,
        crc32c_worker_sha256=crc_worker_sha,
        crc32c_distribution_sha256=str(
            environment_value["google_crc32c_distribution_sha256"]
        ),
        billing_variable="LVEF_C3_GCP_BILLING_PROJECT",
        billing_project=billing,
        selected_studies=selected_studies,
        selected_studies_sha256=values["EXPECTED_SELECTED_STUDIES_SHA256"],
        selected_source=selected_source,
        selected_source_sha256=values["EXPECTED_SELECTED_SOURCE_SHA256"],
        source_metadata=source_metadata,
        split_map=split_map,
        split_map_sha256=values["EXPECTED_SPLIT_MAP_SHA256"],
    )


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes, str]:
    import lvef_c3_canary_manifest as manifest_contract
    import lvef_c3_orchestration_core as core

    payload = manifest_contract._read_private_regular_file(path)
    value = manifest_contract.parse_manifest_bytes(payload)
    if payload != manifest_contract.serialize_manifest(value):
        _fail("MINIMAL_MANIFEST_NOT_CANONICAL")
    body = value["manifest"]
    configuration = {
        str(item["logical_name"]): str(item["sha256"])
        for item in body["source_configuration_hashes"]
    }
    required_configuration = {
        "production_contract": _sha256_bytes(_read_regular(CONTRACT_PATH)),
        "split_map": core.EXPECTED_SPLIT_MAP_SHA256,
        "checkpoint": core.EXPECTED_CHECKPOINT_SHA256,
        "environment_receipt": CURRENT_ENVIRONMENT_SHA256,
    }
    if (
        body["study_count"] != 5
        or body["subject_count"] != 5
        or body["split"] != "train"
        or body["complete_object_membership"] is not True
        or not 5 <= body["expected_object_count"] <= MAX_OBJECTS
        or not 1 <= body["expected_byte_total"] <= MAX_BYTES
        or body["source_manifest_sha256"]
        != core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        or any(
            configuration.get(name) != expected
            for name, expected in required_configuration.items()
        )
        or any(
            row["split"] != "train"
            or row["known_no_cine"] is not False
            or row["prior_reconstruction_smoke"] is not False
            or row["complete_object_membership"] is not True
            for row in body["studies"]
        )
    ):
        _fail("MINIMAL_MANIFEST_SCOPE_INVALID")
    return value, payload, _sha256_bytes(payload)


def _production_functions() -> dict[str, Callable[..., Mapping[str, Any]]]:
    import finalize_lvef_c3_production as finalizer
    import lvef_c3_orchestration_core as core
    import lvef_c3_production_stages as stages
    import preserve_lvef_c3_production_batch as preservation

    return {
        "DOWNLOAD": core.execute_exact_batch_download,
        "DICOM_EXTRACTION": stages.run_production_dicom_extraction,
        "ECHOPRIME_EMBEDDING": stages.run_production_echoprime,
        "BATCH_PRESERVATION": preservation.preserve_batch,
        "CANARY_FINALIZATION": finalizer.finalize_canary_preservation_receipt,
    }


def _validate_installation_files(repository: Path) -> str:
    required = (
        repository / "scripts/lvef_c3_minimal_canary.py",
        repository / "scripts/scc_run_lvef_c3_minimal_canary.sh",
        repository / "scripts/scc_submit_lvef_c3_minimal_canary.sh",
        repository / "scripts/lvef_c3_canary_manifest.py",
        repository / "scripts/lvef_c3_orchestration_core.py",
        repository / "scripts/lvef_c3_production_stages.py",
        repository / "scripts/preserve_lvef_c3_production_batch.py",
        repository / "scripts/finalize_lvef_c3_production.py",
        repository / "tests/test_lvef_c3_minimal_canary_integration.py",
    )
    for path in required:
        if path.is_symlink() or not path.is_file():
            _fail("MINIMAL_TRACKED_CONTROL_MISSING")
    head = _git("rev-parse", "HEAD", repository=repository)
    branch = _git("branch", "--show-current", repository=repository)
    origin = _git(
        "rev-parse", "refs/remotes/origin/codex/lvef-multitask-revalidation",
        repository=repository,
    )
    if branch != EXPECTED_BRANCH or head != origin or COMMIT_RE.fullmatch(head) is None:
        _fail("MINIMAL_GIT_AUTHORITY_MISMATCH")
    if _git("status", "--porcelain", "--untracked-files=no", repository=repository):
        _fail("MINIMAL_TRACKED_WORKTREE_DIRTY")
    functions = _production_functions()
    if tuple(functions) != ORDERED_STAGES or any(
        not callable(value) for value in functions.values()
    ):
        _fail("MINIMAL_PRODUCTION_FUNCTION_IDENTITY_INVALID")
    return head


def validate_installation(*, repository: Path = REPOSITORY_ROOT, enforce_git: bool = True) -> dict[str, Any]:
    head = (
        _validate_installation_files(repository)
        if enforce_git
        else _git("rev-parse", "HEAD", repository=repository)
    )
    return {
        "status": "PASS_MINIMAL_CANARY_INSTALLATION",
        "governing_commit": head,
        "scheduler_submission_count": 1,
        "ordered_stages": list(ORDERED_STAGES),
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
    }


@dataclass
class SequentialContext:
    manifest: Mapping[str, Any]
    output_root: Path
    artifacts: dict[str, Any]


StageOperation = Callable[[SequentialContext], Mapping[str, Any]]
OperationBuilder = Callable[[SequentialContext], Mapping[str, StageOperation]]


@dataclass(frozen=True)
class ProductionDependencies:
    """Only effect boundaries and test performance knobs are injectable."""

    download: Callable[..., Mapping[str, Any]] | None = None
    dicom: Callable[..., Mapping[str, Any]] | None = None
    echoprime: Callable[..., Mapping[str, Any]] | None = None
    preserve: Callable[..., Mapping[str, Any]] | None = None
    finalize: Callable[..., Mapping[str, Any]] | None = None
    token_provider_factory: Callable[..., Any] | None = None
    transport_factory: Callable[[], Any] | None = None
    digest_provider_factory: Callable[[LiveAuthority], Any] | None = None
    extraction_workers: int = 4
    echoprime_batch_size: int = 8
    monotonic_clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ProductionRun:
    """Resolved direct-manifest inputs for one scheduler job."""

    manifest: Mapping[str, Any]
    manifest_file_sha256: str
    authority: LiveAuthority
    run_root: Path
    attempt_id: str
    scheduler_job_identity: str
    plan: Mapping[str, Any]
    plan_path: Path
    plan_sha256: str
    requirements: Any
    runtime_authority: Mapping[str, str]
    scheduler_binding_sha256: str


def _mkdir_private(path: Path, *, parents: bool = False) -> None:
    """Create, then accept SCC setgid inheritance with no group/other access."""

    if os.path.lexists(path):
        _fail("MINIMAL_OUTPUT_COLLISION")
    if parents:
        missing: list[Path] = []
        cursor = path
        while not os.path.lexists(cursor):
            missing.append(cursor)
            cursor = cursor.parent
        if cursor.is_symlink() or not cursor.is_dir():
            _fail("MINIMAL_OUTPUT_PARENT_INVALID")
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            validate_private_directory(
                directory, expected_group=os.lstat(directory.parent).st_gid
            )
        return
    path.mkdir(mode=0o700)
    validate_private_directory(path, expected_group=os.lstat(path.parent).st_gid)


def _validate_private_child(path: Path) -> None:
    try:
        expected_group = os.lstat(path.parent).st_gid
    except OSError as exc:
        raise MinimalCanaryError("MINIMAL_OUTPUT_PARENT_INVALID") from exc
    validate_private_directory(path, expected_group=expected_group)


def _write_private_json_no_clobber(path: Path, value: Mapping[str, Any]) -> str:
    import lvef_c3_orchestration_core as core

    return core.atomic_write_json_no_clobber(
        path, value, attempt_id="lvef_c3_minimal_canary_write"
    )


def _minimal_run_identity(manifest_file_sha256: str, governing_commit: str) -> str:
    if (
        SHA256_RE.fullmatch(manifest_file_sha256) is None
        or COMMIT_RE.fullmatch(governing_commit) is None
    ):
        _fail("MINIMAL_PRODUCTION_RUN_IDENTITY_INVALID")
    return f"lvef_c3_minimal_{manifest_file_sha256[:16]}_{governing_commit[:8]}"


def _manifest_requirements(manifest: Mapping[str, Any]) -> Any:
    import lvef_c3_orchestration_core as core

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


def _manifest_configuration(manifest: Mapping[str, Any]) -> dict[str, str]:
    values = {
        str(row["logical_name"]): str(row["sha256"])
        for row in manifest["manifest"]["source_configuration_hashes"]
    }
    required = {
        "production_contract",
        "source_metadata",
        "split_map",
        "checkpoint",
        "environment_receipt",
        "state_machine_schema",
        "resume_ledger_schema",
        "gcloud_resolution_receipt",
        "gcloud_executable",
        "crc32c_python_executable",
        "crc32c_worker",
        "crc32c_distribution",
    }
    if not required.issubset(values) or any(
        SHA256_RE.fullmatch(values[name]) is None for name in required
    ):
        _fail("MINIMAL_MANIFEST_RUNTIME_AUTHORITY_INCOMPLETE")
    return values


def _manifest_object_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = manifest["manifest"]
    return sorted(
        [
            {
                "subject_id": str(study["subject_id"]),
                "study_id": str(study["study_id"]),
                "split": "train",
                "source_object_key": str(source["source_object_key"]),
                "source_relative_path": str(source["source_relative_path"]),
                "size_bytes": int(source["size_bytes"]),
                "generation": str(source["generation"]),
                "md5_base64": str(source["md5_base64"]),
                "crc32c_base64": str(source["crc32c_base64"]),
            }
            for study in body["studies"]
            for source in study["objects"]
        ],
        key=lambda row: (
            int(row["subject_id"]),
            int(row["study_id"]),
            row["source_relative_path"],
        ),
    )


def _reconcile_manifest_membership(
    manifest: Mapping[str, Any], *, authority: LiveAuthority
) -> list[dict[str, Any]]:
    """Prove every sealed object is an exact member of the frozen sources.

    This intentionally opens row-bearing authorities only in the queued job,
    immediately before plan construction and before token acquisition or the
    first object-body request.  The no-body live preflight performs metadata
    checks only.
    """

    import lvef_c3_orchestration_core as core

    body = manifest["manifest"]
    try:
        if (
            core.sha256_file(authority.selected_studies)
            != authority.selected_studies_sha256
            or core.sha256_file(authority.selected_source)
            != authority.selected_source_sha256
            or str(body["source_manifest_sha256"])
            != authority.selected_source_sha256
        ):
            _fail("MINIMAL_SELECTED_SOURCE_AUTHORITY_MISMATCH")

        selected_rows = core._read_csv_rows(authority.selected_studies)
        selected_source_rows = core._read_csv_rows(authority.selected_source)
        metadata_rows = core._read_jsonl_rows(authority.source_metadata)
        split_rows = core._read_csv_rows(authority.split_map)
        normalized = core.reconcile_selected_source_metadata(
            selected_source_rows,
            metadata_rows,
            release=str(body["source_release"]),
        )

        selected_pairs: set[tuple[str, str]] = set()
        for row in selected_rows:
            pair = (str(row.get("subject_id", "")), str(row.get("study_id", "")))
            if pair in selected_pairs:
                _fail("MINIMAL_SELECTED_STUDY_AUTHORITY_INVALID")
            selected_pairs.add(pair)

        split_by_subject: dict[str, str] = {}
        for row in split_rows:
            subject = str(row.get("subject_id", ""))
            split = str(row.get("split", ""))
            if subject in split_by_subject or split not in {"train", "val", "test"}:
                _fail("MINIMAL_SPLIT_AUTHORITY_INVALID")
            split_by_subject[subject] = split

        manifest_pairs = {
            (str(row["subject_id"]), str(row["study_id"]))
            for row in body["studies"]
        }
        if (
            not manifest_pairs.issubset(selected_pairs)
            or any(split_by_subject.get(subject) != "train" for subject, _ in manifest_pairs)
        ):
            _fail("MINIMAL_SELECTED_STUDY_MEMBERSHIP_MISMATCH")

        projected: list[dict[str, Any]] = []
        for row in normalized:
            pair = (str(row["subject_id"]), str(row["study_id"]))
            if pair not in manifest_pairs:
                continue
            if str(row["split"]) != "train":
                _fail("MINIMAL_SELECTED_SOURCE_SPLIT_MISMATCH")
            projected.append(
                {
                    "subject_id": pair[0],
                    "study_id": pair[1],
                    "split": "train",
                    "source_object_key": str(row["source_object_key"]),
                    "source_relative_path": str(row["source_relative_path"]),
                    "size_bytes": int(row["remote_size_bytes"]),
                    "generation": str(row["remote_generation"]),
                    "md5_base64": str(row["remote_md5_base64"]),
                    "crc32c_base64": str(row["remote_crc32c_base64"]),
                }
            )
        projected.sort(
            key=lambda row: (
                int(row["subject_id"]),
                int(row["study_id"]),
                row["source_relative_path"],
            )
        )
        if projected != _manifest_object_rows(manifest):
            _fail("MINIMAL_SELECTED_SOURCE_MEMBERSHIP_MISMATCH")
        return projected
    except MinimalCanaryError:
        raise
    except Exception as exc:
        raise MinimalCanaryError("MINIMAL_SELECTED_SOURCE_RECONCILIATION_FAILED") from exc


def _build_direct_manifest_plan(
    manifest: Mapping[str, Any], *, authority: LiveAuthority
) -> tuple[dict[str, Any], Any, dict[str, str]]:
    """Project the sealed exact-five membership into the production plan type."""

    import lvef_c3_orchestration_core as core

    body = manifest["manifest"]
    configuration = _manifest_configuration(manifest)
    fixed_files = {
        "production_contract": CONTRACT_PATH,
        "checkpoint": authority.checkpoint,
        "environment_receipt": authority.environment_receipt,
        "state_machine_schema": STATE_MACHINE_PATH,
        "resume_ledger_schema": RESUME_LEDGER_PATH,
        "gcloud_resolution_receipt": authority.gcloud_receipt,
        "gcloud_executable": authority.gcloud,
        "crc32c_python_executable": authority.crc32c_python,
        "crc32c_worker": authority.crc32c_worker,
    }
    for name, path in fixed_files.items():
        if core.sha256_file(path) != configuration[name]:
            _fail("MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH")
    # Row-bearing authorities are not opened in no-body preflight. Their
    # detached identities are fixed by the preserved source selection, then
    # re-hashed only inside the authorized queued job before any token/body.
    if (
        configuration["split_map"] != authority.split_map_sha256
        or configuration["checkpoint"] != authority.checkpoint_sha256
        or configuration["environment_receipt"]
        != authority.environment_receipt_sha256
        or configuration["gcloud_resolution_receipt"]
        != authority.gcloud_receipt_sha256
        or configuration["gcloud_executable"] != authority.gcloud_sha256
        or configuration["crc32c_python_executable"]
        != authority.crc32c_python_sha256
        or configuration["crc32c_worker"] != authority.crc32c_worker_sha256
        or configuration["crc32c_distribution"]
        != authority.crc32c_distribution_sha256
        or core.sha256_file(authority.split_map) != authority.split_map_sha256
        or core.sha256_file(authority.source_metadata)
        != configuration["source_metadata"]
    ):
        _fail("MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH")
    environment = json.loads(_read_regular(authority.environment_receipt, private=True))
    if (
        not isinstance(environment, Mapping)
        or environment.get("google_crc32c_distribution_sha256")
        != configuration["crc32c_distribution"]
    ):
        _fail("MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH")
    if (
        body["source_authority_commit"] != authority.selection_authority_commit
        or not _git_is_ancestor(
            authority.selection_authority_commit,
            authority.governing_commit,
            repository=REPOSITORY_ROOT,
        )
    ):
        _fail("MINIMAL_MANIFEST_SELECTION_COMMIT_MISMATCH")
    plan_authority = {
        "git_commit": authority.governing_commit,
        "orchestration_contract_sha256": configuration["production_contract"],
        "selected_manifest_sha256": str(manifest["manifest_sha256"]),
        "selected_source_manifest_sha256": str(body["source_manifest_sha256"]),
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
    requirements = _manifest_requirements(manifest)
    reconciled_objects = _reconcile_manifest_membership(
        manifest, authority=authority
    )
    selected_rows = [
        {"subject_id": row["subject_id"], "study_id": row["study_id"]}
        for row in body["studies"]
    ]
    split_rows = [
        {"subject_id": row["subject_id"], "split": "train"}
        for row in body["studies"]
    ]
    source_rows = [
        {
            "release_id": body["source_release"],
            "subject_id": source["subject_id"],
            "study_id": source["study_id"],
            "split": source["split"],
            "production_batch": BATCH_ID,
            **source,
        }
        for source in reconciled_objects
    ]
    plan = core.build_immutable_batch_plan(
        selected_rows,
        source_rows,
        split_rows,
        requirements=requirements,
        authority=plan_authority,
    )
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    return plan, requirements, core.validate_runtime_authority(
        {**plan_authority, "batch_plan_sha256": plan_sha}
    )


@contextmanager
def _digest_provider(
    factory: Callable[[LiveAuthority], Any] | None,
    authority: LiveAuthority,
    *,
    expected_distribution_sha256: str,
) -> Any:
    """Keep the production CRC32C worker open for the complete download stage."""

    import lvef_c3_orchestration_core as core

    if factory is not None:
        supplied = factory(authority)
        if hasattr(supplied, "__enter__"):
            with supplied as active:
                yield active.digest if hasattr(active, "digest") else active
        else:
            yield supplied.digest if hasattr(supplied, "digest") else supplied
        return
    with core.ExternalCRC32CDigestWorker(
        python_executable=authority.crc32c_python,
        worker_script=authority.crc32c_worker,
        expected_python_sha256=core.sha256_file(authority.crc32c_python),
        expected_worker_sha256=core.sha256_file(authority.crc32c_worker),
        expected_distribution_sha256=expected_distribution_sha256,
    ) as active:
        yield active.digest


def _resolved_dependencies(value: ProductionDependencies | None) -> ProductionDependencies:
    functions = _production_functions()
    source = value or ProductionDependencies()
    if (
        isinstance(source.extraction_workers, bool)
        or source.extraction_workers < 1
        or isinstance(source.echoprime_batch_size, bool)
        or source.echoprime_batch_size < 1
    ):
        _fail("MINIMAL_OPERATION_CONFIGURATION_INVALID")
    return ProductionDependencies(
        download=source.download or functions["DOWNLOAD"],
        dicom=source.dicom or functions["DICOM_EXTRACTION"],
        echoprime=source.echoprime or functions["ECHOPRIME_EMBEDDING"],
        preserve=source.preserve or functions["BATCH_PRESERVATION"],
        finalize=source.finalize or functions["CANARY_FINALIZATION"],
        token_provider_factory=source.token_provider_factory,
        transport_factory=source.transport_factory,
        digest_provider_factory=source.digest_provider_factory,
        extraction_workers=source.extraction_workers,
        echoprime_batch_size=source.echoprime_batch_size,
        monotonic_clock=source.monotonic_clock,
        sleeper=source.sleeper,
    )


def _production_run(
    *,
    manifest: Mapping[str, Any],
    manifest_file_sha256: str,
    authority: LiveAuthority,
    run_root: Path,
    scheduler_job_identity: str,
) -> ProductionRun:
    import lvef_c3_orchestration_core as core

    if (
        SHA256_RE.fullmatch(manifest_file_sha256) is None
        or not run_root.is_absolute()
        or run_root.is_symlink()
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", scheduler_job_identity)
    ):
        _fail("MINIMAL_PRODUCTION_RUN_IDENTITY_INVALID")
    plan, requirements, runtime_authority = _build_direct_manifest_plan(
        manifest, authority=authority
    )
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    attempt_id = _minimal_run_identity(
        manifest_file_sha256, authority.governing_commit
    )
    expected_name = attempt_id
    if run_root.name != expected_name:
        _fail("MINIMAL_PRODUCTION_RUN_ROOT_NOT_MANIFEST_BOUND")
    scheduler_binding = core.canonical_json_sha256(
        {
            "schema_version": 1,
            "job_model": "ONE_SEQUENTIAL_SCC_JOB",
            "scheduler_submission_count": 1,
            "ordered_stages": list(ORDERED_STAGES),
            "manifest_sha256": manifest["manifest_sha256"],
            "manifest_file_sha256": manifest_file_sha256,
            "batch_plan_sha256": plan_sha,
            "automatic_resubmission": False,
            "array_expansion": False,
            "production_continuation": False,
        }
    )
    return ProductionRun(
        manifest=manifest,
        manifest_file_sha256=manifest_file_sha256,
        authority=authority,
        run_root=run_root,
        attempt_id=attempt_id,
        scheduler_job_identity=scheduler_job_identity,
        plan=plan,
        plan_path=run_root / "minimal_batch_plan.restricted.json",
        plan_sha256=plan_sha,
        requirements=requirements,
        runtime_authority=runtime_authority,
        scheduler_binding_sha256=scheduler_binding,
    )


def build_production_operations(
    *,
    manifest: Mapping[str, Any],
    manifest_file_sha256: str,
    authority: LiveAuthority,
    run_root: Path,
    scheduler_job_identity: str,
    dependencies: ProductionDependencies | None = None,
) -> dict[str, StageOperation]:
    """Build one exact producer-to-consumer chain over production callables."""

    import finalize_lvef_c3_production as finalizer
    import lvef_c3_orchestration_core as core
    import lvef_c3_production_stages as stages
    import preserve_lvef_c3_production_batch as preservation

    run = _production_run(
        manifest=manifest,
        manifest_file_sha256=manifest_file_sha256,
        authority=authority,
        run_root=run_root,
        scheduler_job_identity=scheduler_job_identity,
    )
    dependency = _resolved_dependencies(dependencies)
    attempt_root = run.run_root / "attempts" / run.attempt_id
    raw_root = attempt_root / "raw"
    raw_batch_root = raw_root / BATCH_ID
    batch_root = attempt_root / "batches" / BATCH_ID
    cache_root = attempt_root / "extracted_cache" / BATCH_ID
    extraction_root = cache_root / "dicom_extraction"
    echoprime_root = batch_root / "echoprime"
    expected_object_keys = {
        str(row["source_object_key"]) for row in run.plan["batches"][0]["objects"]
    }
    configuration = _manifest_configuration(run.manifest)

    def download(context: SequentialContext) -> Mapping[str, Any]:
        _mkdir_private(attempt_root, parents=True)
        _mkdir_private(raw_root)
        _mkdir_private(batch_root.parent, parents=True)
        _mkdir_private(batch_root)
        _write_private_json_no_clobber(run.plan_path, run.plan)
        ledger = core.initialize_resume_ledger(
            run.plan,
            requirements=run.requirements,
            attempt_id=run.attempt_id,
            authority=run.runtime_authority,
            batch_ids=[BATCH_ID],
        )
        if dependency.token_provider_factory is None:
            provider = core.GcloudADCTokenProvider(
                run.authority.gcloud,
                cloudsdk_config=run.authority.cloudsdk_config,
                authority_receipt=run.authority.gcloud_receipt,
                authority_receipt_sha256=configuration[
                    "gcloud_resolution_receipt"
                ],
            )
        else:
            provider = dependency.token_provider_factory(run.authority)
        observed = provider.validate_authority()
        core.validate_gcloud_runtime_authority(
            observed, expected_runtime_authority=run.runtime_authority
        )
        transport = (
            core.GCSExactObjectBodyTransport()
            if dependency.transport_factory is None
            else dependency.transport_factory()
        )
        current = datetime.now(timezone.utc)
        previous_billing = os.environ.get(run.authority.billing_variable)
        os.environ[run.authority.billing_variable] = run.authority.billing_project
        try:
            with _digest_provider(
                dependency.digest_provider_factory,
                run.authority,
                expected_distribution_sha256=configuration[
                    "crc32c_distribution"
                ],
            ) as digest:
                updated = dependency.download(
                    plan=run.plan,
                    requirements=run.requirements,
                    ledger=ledger,
                    contract=core.load_orchestration_contract(CONTRACT_PATH),
                    batch_id=BATCH_ID,
                    expected_runtime_authority=run.runtime_authority,
                    authorization_receipt=None,
                    direct_manifest=run.manifest,
                    output_root=raw_root,
                    scoped_production_root=run.run_root,
                    launch_authority_sha256=run.scheduler_binding_sha256,
                    argv=(),
                    token_provider=provider,
                    transport=transport,
                    now=current,
                    monotonic_clock=dependency.monotonic_clock,
                    sleeper=dependency.sleeper,
                    digest_provider=digest,
                )
        finally:
            if previous_billing is None:
                os.environ.pop(run.authority.billing_variable, None)
            else:
                os.environ[run.authority.billing_variable] = previous_billing
        ledger_path = batch_root / "download_resume_ledger.restricted.json"
        _write_private_json_no_clobber(ledger_path, updated)
        context.artifacts["run"] = run
        context.artifacts["download_ledger"] = updated
        return {
            "status": "PASS_DOWNLOAD_VERIFIED",
            "verified_objects": run.requirements.normalized_source_objects,
            "selected_source_bytes": run.requirements.selected_source_bytes,
        }

    def dicom(context: SequentialContext) -> Mapping[str, Any]:
        _mkdir_private(cache_root.parent)
        _mkdir_private(cache_root)
        verified = raw_batch_root / "verified_download_manifest.restricted.csv"
        input_ledger = batch_root / "download_resume_ledger.restricted.json"
        stages.validate_stage_predecessor(
            input_ledger=input_ledger,
            batch_id=BATCH_ID,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=expected_object_keys,
            bound_manifest=verified,
        )
        stages.validate_download_manifest_plan_membership(
            verified, run.plan["batches"][0]
        )
        summary = dependency.dicom(
            verified_download_manifest=verified,
            download_root=raw_batch_root / "objects",
            batch_output_root=cache_root,
            workers=dependency.extraction_workers,
            batch_id=BATCH_ID,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
        )
        stages.advance_stage_ledger(
            input_ledger=input_ledger,
            output_ledger=batch_root / "extraction_resume_ledger.restricted.json",
            receipt_root=extraction_root / "transition_receipts",
            batch_id=BATCH_ID,
            transitions=(
                (
                    "DICOM_AUDIT_COMPLETE",
                    stages.sha256_file(
                        extraction_root / "dicom_audit.restricted.csv"
                    ),
                ),
                (
                    "EXTRACTION_COMPLETE",
                    stages.sha256_file(
                        extraction_root / "extraction_manifest.restricted.csv"
                    ),
                ),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=expected_object_keys,
        )
        return summary

    def echoprime(context: SequentialContext) -> Mapping[str, Any]:
        extraction = extraction_root / "extraction_manifest.restricted.csv"
        input_ledger = batch_root / "extraction_resume_ledger.restricted.json"
        predecessor = (
            extraction_root
            / "transition_receipts"
            / "extraction_complete.restricted.json"
        )
        stages.validate_stage_predecessor(
            input_ledger=input_ledger,
            batch_id=BATCH_ID,
            expected_state="EXTRACTION_COMPLETE",
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=expected_object_keys,
            bound_manifest=extraction,
            predecessor_transition_receipt=predecessor,
        )
        stages.validate_extraction_manifest_plan_membership(
            extraction, run.plan["batches"][0]
        )
        summary = dependency.echoprime(
            extraction_manifest=extraction,
            extraction_root=extraction_root / "clips",
            selected_batch_manifest=raw_batch_root
            / "selected_batch.restricted.csv",
            checkpoint=run.authority.checkpoint,
            environment_receipt=run.authority.environment_receipt,
            orchestration_contract=CONTRACT_PATH,
            batch_plan=run.plan_path,
            batch_id=BATCH_ID,
            batch_output_root=batch_root,
            batch_size=dependency.echoprime_batch_size,
            seed=20260803,
            attempt_id=run.attempt_id,
            runtime_authority=run.runtime_authority,
            requirements=run.requirements,
        )
        stages.advance_stage_ledger(
            input_ledger=input_ledger,
            output_ledger=batch_root / "pooling_resume_ledger.restricted.json",
            receipt_root=echoprime_root / "transition_receipts",
            batch_id=BATCH_ID,
            transitions=(
                (
                    "EMBEDDING_COMPLETE",
                    stages.sha256_file(
                        echoprime_root / "clip_manifest.restricted.csv"
                    ),
                ),
                (
                    "STUDY_POOLING_COMPLETE",
                    stages.sha256_file(
                        echoprime_root / "study_manifest.restricted.csv"
                    ),
                ),
            ),
            expected_authority=run.runtime_authority,
            expected_attempt_id=run.attempt_id,
            expected_object_keys=expected_object_keys,
        )
        return summary

    def preserve(context: SequentialContext) -> Mapping[str, Any]:
        result = dependency.preserve(
            contract_path=CONTRACT_PATH,
            plan_path=run.plan_path,
            batch_id=BATCH_ID,
            attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.run_root,
            output_root=batch_root / "preservation",
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=run.scheduler_job_identity,
            input_ledger=batch_root / "pooling_resume_ledger.restricted.json",
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=SCRIPT_ROOT
            / "scc_run_lvef_c3_minimal_canary.sh",
        )
        context.artifacts["preservation_receipt"] = result
        return result

    def finalize(context: SequentialContext) -> Mapping[str, Any]:
        receipt = preservation.load_json(
            batch_root
            / "preservation"
            / "batch_preservation_receipt.restricted.json",
            "MINIMAL_CANARY_PRESERVATION_RECEIPT",
        )
        summary = dependency.finalize(
            receipt,
            expected_governing_commit=run.authority.governing_commit,
            expected_attempt_id=run.attempt_id,
            expected_canary_manifest_sha256=str(run.manifest["manifest_sha256"]),
            expected_batch_plan_sha256=run.plan_sha256,
            expected_scheduler_plan_sha256=run.scheduler_binding_sha256,
            expected_object_count=int(run.requirements.normalized_source_objects),
            expected_source_bytes=int(run.requirements.selected_source_bytes),
        )
        finalizer.write_json_atomic(
            run.run_root
            / "minimal_canary_finalization_receipt.aggregate_safe.json",
            summary,
        )
        return summary

    return {
        "DOWNLOAD": download,
        "DICOM_EXTRACTION": dicom,
        "ECHOPRIME_EMBEDDING": echoprime,
        "BATCH_PRESERVATION": preserve,
        "CANARY_FINALIZATION": finalize,
    }


def production_operation_builder(
    *,
    manifest_file_sha256: str,
    authority: LiveAuthority,
    scheduler_job_identity: str,
    dependencies: ProductionDependencies | None = None,
) -> OperationBuilder:
    """Defer path-sensitive operation construction until the run root exists."""

    def build(context: SequentialContext) -> Mapping[str, StageOperation]:
        return build_production_operations(
            manifest=context.manifest,
            manifest_file_sha256=manifest_file_sha256,
            authority=authority,
            run_root=context.output_root,
            scheduler_job_identity=scheduler_job_identity,
            dependencies=dependencies,
        )

    return build


def run_sealed_manifest(
    *,
    manifest_path: Path,
    scheduler_job_identity: str,
    authority: LiveAuthority | None = None,
    run_root: Path | None = None,
    dependencies: ProductionDependencies | None = None,
) -> dict[str, Any]:
    """Execute the direct sealed manifest once in the current scheduler job."""

    manifest, _, file_sha256 = _load_manifest(manifest_path)
    current_authority = authority or discover_live_authority()
    attempt_id = _minimal_run_identity(
        file_sha256, current_authority.governing_commit
    )
    destination = run_root or (
        PRODUCTION_ROOT / "minimal_canary_runs" / attempt_id
    )
    if not os.path.lexists(destination):
        _fail("MINIMAL_PREPARED_RUN_MISSING")
    return run_sequential_adapter(
        manifest=manifest,
        output_root=destination,
        operations=production_operation_builder(
            manifest_file_sha256=file_sha256,
            authority=current_authority,
            scheduler_job_identity=scheduler_job_identity,
            dependencies=dependencies,
        ),
        scheduler_submission_count=1,
        adopt_prepared=True,
        manifest_file_sha256=file_sha256,
        governing_commit=current_authority.governing_commit,
        scheduler_job_identity=scheduler_job_identity,
    )


def claim_sealed_manifest_submission(
    *,
    manifest_path: Path,
    authority: LiveAuthority | None = None,
) -> dict[str, Any]:
    """Publish the sole PREPARED run root before the one authorized qsub.

    The claim is operational evidence, not another authority: it contains no
    identifiers or locators and merely prevents a second scheduler submission
    for the same sealed manifest and governing commit.
    """

    manifest, _, file_sha256 = _load_manifest(manifest_path)
    current_authority = authority or discover_live_authority()
    # Complete every row-bearing scientific check before claiming a queue slot.
    _build_direct_manifest_plan(manifest, authority=current_authority)
    run_id = _minimal_run_identity(file_sha256, current_authority.governing_commit)
    parent = PRODUCTION_ROOT / "minimal_canary_runs"
    if not os.path.lexists(parent):
        _mkdir_private(parent, parents=True)
    else:
        _validate_private_child(parent)
    root = parent / run_id
    _mkdir_private(root)
    ledger = _initial_ledger(
        manifest_sha256=str(manifest["manifest_sha256"]),
        manifest_file_sha256=file_sha256,
        governing_commit=current_authority.governing_commit,
    )
    _atomic_json(
        root / "minimal_canary_stage_ledger.restricted.json",
        ledger,
        replace=False,
    )
    return {
        "status": "PASS_MINIMAL_SUBMISSION_CLAIMED",
        "governing_commit": current_authority.governing_commit,
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "manifest_file_sha256": file_sha256,
        "cloud_requests": 0,
        "qsub_submissions": 0,
    }


def _atomic_json(path: Path, value: Mapping[str, Any], *, replace: bool) -> str:
    payload = _canonical_bytes(value)
    temporary = path.with_name(
        f".{path.name}.partial.{os.getpid()}.{secrets.token_hex(8)}"
    )
    if not replace and os.path.lexists(path):
        _fail("MINIMAL_OUTPUT_COLLISION")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False)
            os.unlink(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return _sha256_bytes(payload)


def _initial_ledger(
    *,
    manifest_sha256: str | None = None,
    manifest_file_sha256: str | None = None,
    governing_commit: str | None = None,
) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_minimal_canary_stage_ledger_v1",
        "status": "PREPARED",
        "scheduler_submission_count": 1,
        "ordered_stages": list(ORDERED_STAGES),
        "completed_stages": [],
        "active_stage": None,
        "failed_stage": None,
        "stage_result_sha256": {},
        "production_continuation": False,
    }
    if manifest_sha256 is not None:
        ledger["manifest_sha256"] = manifest_sha256
    if manifest_file_sha256 is not None:
        ledger["manifest_file_sha256"] = manifest_file_sha256
    if governing_commit is not None:
        ledger["governing_commit"] = governing_commit
    return ledger


def run_sequential_adapter(
    *, manifest: Mapping[str, Any], output_root: Path,
    operations: Mapping[str, StageOperation] | OperationBuilder,
    scheduler_submission_count: int = 1,
    adopt_prepared: bool = False,
    manifest_file_sha256: str | None = None,
    governing_commit: str | None = None,
    scheduler_job_identity: str | None = None,
) -> dict[str, Any]:
    """Run five declared operations in order with one simple durable ledger."""

    if scheduler_submission_count != 1:
        _fail("MINIMAL_SEQUENTIAL_SCOPE_INVALID")
    if (
        scheduler_job_identity is not None
        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", scheduler_job_identity) is None
    ):
        _fail("MINIMAL_SCHEDULER_JOB_IDENTITY_INVALID")
    ledger_path = output_root / "minimal_canary_stage_ledger.restricted.json"
    terminal_path = output_root / "minimal_canary_terminal_receipt.aggregate_safe.json"
    if adopt_prepared:
        _validate_private_child(output_root)
        try:
            ledger = json.loads(_read_regular(ledger_path, private=True))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MinimalCanaryError("MINIMAL_PREPARED_RUN_INVALID") from exc
        expected_ledger = _initial_ledger(
            manifest_sha256=str(manifest["manifest_sha256"]),
            manifest_file_sha256=manifest_file_sha256,
            governing_commit=governing_commit,
        )
        if (
            ledger != expected_ledger
            or os.path.lexists(terminal_path)
        ):
            _fail("MINIMAL_PREPARED_RUN_INVALID")
    else:
        if os.path.lexists(output_root):
            _fail("MINIMAL_RUN_ROOT_COLLISION")
        output_root.mkdir(mode=0o700)
        _validate_private_child(output_root)
        ledger = _initial_ledger(
            manifest_sha256=(
                str(manifest["manifest_sha256"])
                if manifest_file_sha256 is not None
                else None
            ),
            manifest_file_sha256=manifest_file_sha256,
            governing_commit=governing_commit,
        )
        _atomic_json(ledger_path, ledger, replace=False)
    context = SequentialContext(manifest=manifest, output_root=output_root, artifacts={})
    try:
        ledger["status"] = "RUNNING"
        ledger["active_stage"] = ORDERED_STAGES[0]
        if scheduler_job_identity is not None:
            ledger["scheduler_job_identity"] = scheduler_job_identity
        _atomic_json(ledger_path, ledger, replace=True)
        resolved_operations = operations(context) if callable(operations) else operations
        if tuple(resolved_operations) != ORDERED_STAGES:
            _fail("MINIMAL_SEQUENTIAL_SCOPE_INVALID")
        for stage in ORDERED_STAGES:
            ledger["status"] = "RUNNING"
            ledger["active_stage"] = stage
            _atomic_json(ledger_path, ledger, replace=True)
            result = dict(resolved_operations[stage](context))
            digest = _sha256_bytes(_canonical_bytes(result))
            ledger["stage_result_sha256"][stage] = digest
            ledger["completed_stages"].append(stage)
            ledger["active_stage"] = None
            _atomic_json(ledger_path, ledger, replace=True)
    except Exception as exc:
        ledger["status"] = "FAIL"
        ledger["failed_stage"] = ledger["active_stage"]
        ledger["active_stage"] = None
        ledger_sha256 = _atomic_json(ledger_path, ledger, replace=True)
        terminal = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_minimal_canary_terminal_receipt_v1",
            "status": "FAIL",
            "completed_stage_count": len(ledger["completed_stages"]),
            "failed_stage": ledger["failed_stage"],
            "completed_stages": list(ledger["completed_stages"]),
            "stage_result_sha256": dict(ledger["stage_result_sha256"]),
            "stage_ledger_sha256": ledger_sha256,
            "scheduler_submission_count": 1,
            "production_continuation": False,
            "identifiers_emitted": False,
            "restricted_paths_emitted": False,
        }
        for name in ("manifest_sha256", "manifest_file_sha256", "governing_commit"):
            if name in ledger:
                terminal[name] = ledger[name]
        if scheduler_job_identity is not None:
            terminal["scheduler_job_identity"] = scheduler_job_identity
        _atomic_json(terminal_path, terminal, replace=False)
        raise MinimalCanaryError(getattr(exc, "code", "MINIMAL_STAGE_FAILED")) from exc
    ledger["status"] = "PASS"
    ledger_sha256 = _atomic_json(ledger_path, ledger, replace=True)
    terminal = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_minimal_canary_terminal_receipt_v1",
        "status": "PASS",
        "completed_stage_count": 5,
        "failed_stage": None,
        "completed_stages": list(ledger["completed_stages"]),
        "stage_result_sha256": dict(ledger["stage_result_sha256"]),
        "stage_ledger_sha256": ledger_sha256,
        "scheduler_submission_count": 1,
        "production_continuation": False,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    for name in ("manifest_sha256", "manifest_file_sha256", "governing_commit"):
        if name in ledger:
            terminal[name] = ledger[name]
    if scheduler_job_identity is not None:
        terminal["scheduler_job_identity"] = scheduler_job_identity
    _atomic_json(terminal_path, terminal, replace=False)
    return terminal


def _synthetic_manifest() -> dict[str, Any]:
    import base64
    import lvef_c3_canary_manifest as manifest_contract

    candidates = []
    objects = []
    for index in range(5):
        subject = str(9_100_000 + index)
        study = str(9_200_000 + index)
        relative = f"files/p09/p{subject}/s{study}/synthetic_{index}.dcm"
        candidates.append({
            "study_id": study, "subject_id": subject, "split": "train",
            "expected_object_count": 1, "expected_byte_total": 1,
            "known_no_cine": False, "prior_reconstruction_smoke": False,
        })
        objects.append({
            "subject_id": subject, "study_id": study, "split": "train",
            "source_object_key": hashlib.sha256(
                f"mimic-iv-echo/1.0\0{relative}".encode("utf-8")
            ).hexdigest(),
            "source_relative_path": relative,
            "size_bytes": 1, "generation": str(index + 1),
            "md5_base64": base64.b64encode(bytes([index]) * 16).decode(),
            "crc32c_base64": base64.b64encode(bytes([index]) * 4).decode(),
        })
    selected = manifest_contract.select_exact_five(candidates)
    return manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=objects,
        source_authority_commit="a" * 40,
        source_manifest_sha256="b" * 64,
        source_configuration_hashes={"synthetic": "c" * 64},
    )


def synthetic_preflight() -> dict[str, Any]:
    import tempfile
    manifest = _synthetic_manifest()
    calls: list[str] = []

    def operation(stage: str) -> StageOperation:
        def run(context: SequentialContext) -> Mapping[str, Any]:
            calls.append(stage)
            return {"status": "PASS", "stage": stage, "synthetic": True}
        return run

    with tempfile.TemporaryDirectory(
        prefix="lvef_c3_minimal_", dir=str(SAFE_TEMPORARY_ROOT)
    ) as directory:
        terminal = run_sequential_adapter(
            manifest=manifest,
            output_root=Path(directory) / "run",
            operations={stage: operation(stage) for stage in ORDERED_STAGES},
        )
    if tuple(calls) != ORDERED_STAGES or terminal["status"] != "PASS":
        _fail("MINIMAL_SYNTHETIC_PREFLIGHT_FAILED")
    return {
        "status": "PASS_MINIMAL_EXACT_FIVE_SYNTHETIC_PREFLIGHT",
        "studies": 5,
        "subjects": 5,
        "scheduler_submission_count": 1,
        "ordered_stages": list(ORDERED_STAGES),
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
    }


def live_authority_no_body_preflight() -> dict[str, Any]:
    authority = discover_live_authority()
    import capture_lvef_c3_post_reallocation_capacity as capacity

    headroom = capacity.validate_current_canary_headroom(
        capacity.probe_current_canary_headroom()
    )
    scheduler_payload = _read_regular(QSUB_PATH)
    scheduler_metadata = os.lstat(QSUB_PATH)
    if (
        headroom["cloud_requests"] != 0
        or headroom["scheduler_jobs_submitted"] != 0
        or headroom["writes_performed"] != 0
        or scheduler_metadata.st_uid != 0
        or not stat.S_IMODE(scheduler_metadata.st_mode) & stat.S_IXUSR
        or len(scheduler_payload) != scheduler_metadata.st_size
    ):
        _fail("MINIMAL_LIVE_PREFLIGHT_EFFECT_OR_SCHEDULER_INVALID")
    # Validate the scientific shape in memory. No real manifest or output is
    # created and the first body boundary (transport.fetch) is not called.
    manifest = _synthetic_manifest()
    if len(manifest["manifest"]["studies"]) != 5:
        _fail("MINIMAL_LIVE_PREFLIGHT_SCOPE_INVALID")
    return {
        "status": "PASS_MINIMAL_LIVE_AUTHORITY_NO_BODY_PREFLIGHT",
        "governing_commit": authority.governing_commit,
        "ready_for_first_body_request": "YES",
        "real_manifest_created": False,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
    }


def _print_result(value: Mapping[str, Any]) -> None:
    print("LVEF_C3_MINIMAL_CANARY=PASS")
    print(f"STATUS={value['status']}")
    if "governing_commit" in value:
        print(f"GOVERNING_COMMIT={value['governing_commit']}")
    if value.get("ready_for_first_body_request") == "YES":
        print("READY_FOR_FIRST_BODY_REQUEST=YES")
    print(f"CLOUD_REQUESTS={value.get('cloud_requests', 0)}")
    print(f"QSUB_SUBMISSIONS={value.get('qsub_submissions', 0)}")
    print("DICOM_BODIES_DOWNLOADED=NO")
    print("GPU_EXECUTION=NO")


def _failure_effect_markers(*, live_run: bool) -> tuple[str, ...]:
    if live_run:
        return (
            "CLOUD_REQUESTS=NOT_ATTESTED",
            "QSUB_SUBMISSIONS=1",
            "DICOM_BODIES_DOWNLOADED=NOT_ATTESTED",
            "GPU_EXECUTION=NOT_ATTESTED",
        )
    return (
        "CLOUD_REQUESTS=0",
        "QSUB_SUBMISSIONS=0",
        "DICOM_BODIES_DOWNLOADED=NO",
        "GPU_EXECUTION=NO",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-installation", action="store_true")
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--preflight-live-authority", action="store_true")
    modes.add_argument("--validate-sealed-manifest", type=Path)
    modes.add_argument("--claim-sealed-manifest", type=Path)
    modes.add_argument("--run-sealed-manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.validate_installation:
        result = validate_installation()
    elif args.preflight_only:
        validate_installation()
        result = synthetic_preflight()
    elif args.preflight_live_authority:
        validate_installation()
        result = live_authority_no_body_preflight()
    elif args.validate_sealed_manifest is not None:
        validate_installation()
        manifest, _, file_sha = _load_manifest(args.validate_sealed_manifest)
        result = {
            "status": "PASS_MINIMAL_SEALED_MANIFEST",
            "manifest_sha256": manifest["manifest_sha256"],
            "manifest_file_sha256": file_sha,
            "cloud_requests": 0,
            "qsub_submissions": 0,
        }
    elif args.claim_sealed_manifest is not None:
        validate_installation()
        result = claim_sealed_manifest_submission(
            manifest_path=args.claim_sealed_manifest
        )
    else:
        validate_installation()
        job_id = os.environ.get("JOB_ID", "")
        if re.fullmatch(r"[1-9][0-9]{0,19}", job_id) is None:
            _fail("MINIMAL_SCHEDULER_JOB_IDENTITY_INVALID")
        terminal = run_sealed_manifest(
            manifest_path=args.run_sealed_manifest,
            scheduler_job_identity=job_id,
        )
        print("LVEF_C3_MINIMAL_CANARY=PASS")
        print("STATUS=PASS_MINIMAL_LIVE_SEQUENTIAL_CANARY")
        print(f"COMPLETED_STAGE_COUNT={terminal['completed_stage_count']}")
        print("PRODUCTION_CONTINUATION=NO")
        return 0
    _print_result(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MinimalCanaryError as exc:
        print("LVEF_C3_MINIMAL_CANARY=BLOCKED")
        print(f"STATUS=BLOCKED_{exc.code}")
        for marker in _failure_effect_markers(
            live_run="--run-sealed-manifest" in sys.argv
        ):
            print(marker)
        raise SystemExit(78)
    except Exception:
        print("LVEF_C3_MINIMAL_CANARY=BLOCKED")
        print("STATUS=BLOCKED_MINIMAL_CANARY_UNEXPECTED_FAILURE")
        for marker in _failure_effect_markers(
            live_run="--run-sealed-manifest" in sys.argv
        ):
            print(marker)
        raise SystemExit(78)
