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
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
STAGE_GRANT_KEYS = frozenset({"stage_id", "path", "file_sha256", "authorized"})
GCLOUD_KEYS = frozenset({"binary_path", "resolution_receipt_path", "cloudsdk_config_path"})
CRC32C_KEYS = frozenset({"python_path", "worker_path"})
REQUESTER_KEYS = frozenset({"billing_environment_variable", "billing_project"})

PACKET_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "governing_commit", "branch",
        "run_id", "attempt_id", "output_root", "manifest", "batch_plan",
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


def _sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _mapping(value: Any, keys: frozenset[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(code)
    return value


def _path(value: Any, code: str) -> Path:
    if not isinstance(value, str):
        _fail(code)
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        _fail(code)
    return path


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


def _require_private_tree_file(path: Path, expected_sha: str) -> bytes:
    try:
        relative = path.relative_to(PRIVATE_ROOT)
    except ValueError:
        _fail("CANARY_EXECUTION_PRIVATE_PATH_OUTSIDE_ROOT")
    if not relative.parts:
        _fail("CANARY_EXECUTION_PRIVATE_PATH_INVALID")
    cursor = PRIVATE_ROOT
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


def _binding(value: Any, keys: frozenset[str], code: str) -> tuple[Mapping[str, Any], Path, bytes]:
    item = _mapping(value, keys, code)
    path = _path(item.get("path"), code)
    digest = _sha(item.get("file_sha256"), code)
    return item, path, _require_private_tree_file(path, digest)


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
        "checkpoint", "environment_receipt", "state_machine_schema",
        "resume_ledger_schema", "gcloud_resolution_receipt", "gcloud_executable",
        "crc32c_python_executable", "crc32c_worker", "crc32c_distribution",
    }
    if set(result) != required:
        _fail("CANARY_EXECUTION_MANIFEST_CONFIGURATION_SET_INVALID")
    return result


def load_and_validate_execution_authority(
    path: Path = FIXED_PATH,
    *,
    expected_governing_commit: str | None = None,
    require_output_absent: bool = True,
) -> dict[str, Any]:
    """Load one canonical packet and return the normalized worker mapping."""

    path = Path(path)
    if path != FIXED_PATH:
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

    _require_private_directory(PRIVATE_ROOT)
    _require_private_directory(CANARY_RUN_ROOT)
    output_root = _path(packet.get("output_root"), "CANARY_EXECUTION_OUTPUT_ROOT_INVALID")
    if output_root != CANARY_RUN_ROOT / run_id:
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

    manifest_binding, manifest_path, _ = _binding(
        packet.get("manifest"), MANIFEST_BINDING_KEYS,
        "CANARY_EXECUTION_MANIFEST_BINDING_INVALID",
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

    plan_binding, _, plan_payload = _binding(
        packet.get("batch_plan"), CANONICAL_BINDING_KEYS,
        "CANARY_EXECUTION_BATCH_PLAN_BINDING_INVALID",
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

    scheduler_binding, scheduler_path, _ = _binding(
        packet.get("scheduler_plan"), CANONICAL_BINDING_KEYS,
        "CANARY_EXECUTION_SCHEDULER_PLAN_BINDING_INVALID",
    )
    scheduler_plan = scheduler_contract.load_scheduler_plan(scheduler_path)
    scheduler_sha = scheduler_contract.validate_scheduler_plan(
        scheduler_plan, repository_root=REPOSITORY_ROOT, require_bound_manifest=True
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
        packet.get("production_contract"), fixed_path=PRODUCTION_CONTRACT_PATH
    )
    # Semantic contract validation also revalidates its tracked control schemas.
    contract = core.load_orchestration_contract(PRODUCTION_CONTRACT_PATH)
    if configuration["production_contract"] != hashlib.sha256(contract_payload).hexdigest():
        _fail("CANARY_EXECUTION_CONTRACT_MANIFEST_BINDING_MISMATCH")
    if configuration["execution_state"] != hashlib.sha256(
        _read_regular(EXECUTION_STATE_PATH, owner_private=False)
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
            _read_regular(STATE_MACHINE_PATH, owner_private=False)
        ).hexdigest(),
        "resume_ledger_schema_sha256": hashlib.sha256(
            _read_regular(RESUME_LEDGER_PATH, owner_private=False)
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
        )
        grant_payloads[stage_id] = _load_json(
            grant_payload, "CANARY_EXECUTION_STAGE_AUTHORIZATION_JSON_INVALID"
        )
    body_binding, body_path, body_payload = _binding(
        packet.get("body_transfer_authorization"), BINDING_KEYS,
        "CANARY_EXECUTION_BODY_AUTHORIZATION_INVALID",
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

    _public_binding(packet.get("stage_worker"), fixed_path=STAGE_WORKER_PATH)
    _public_binding(
        packet.get("stage_launcher"), fixed_path=STAGE_LAUNCHER_PATH, executable=True
    )
    _public_binding(packet.get("qsub"), executable=True)
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
    normalized["authorization_path"] = str(path)
    normalized["authorization_file_sha256"] = hashlib.sha256(payload).hexdigest()
    return normalized


__all__ = [
    "AUTHORIZATION_SCOPES", "CanaryExecutionAuthorityError", "FIXED_PATH",
    "HARD_SCOPE", "SCHEDULER_SCOPE", "calculate_authorization_sha256",
    "load_and_validate_execution_authority", "serialize_authorization",
]
