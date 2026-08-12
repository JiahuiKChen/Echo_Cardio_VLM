#!/usr/bin/env python3
"""Fail-closed production wrappers for the C3 imaging stages.

The dependency-light validation surface is safe to exercise offline.  Real
DICOM decoding and EchoPrime inference are reachable only through explicit
subcommands that require a restricted, stage-specific owner-authorization
receipt.  Heavy dependencies are imported only after every authority and path
gate has passed.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CHECKPOINT_FILENAME = "echo_prime_encoder.pt"
CHECKPOINT_BYTES = 138_642_379
CHECKPOINT_SHA256 = (
    "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
)
EXPECTED_EMBEDDING_DIMENSION = 512
EXPECTED_EXTRACTED_SHAPE = (32, 224, 224, 3)
PROJECTNB_PREFIX = Path("/restricted/projectnb")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BATCH_RE = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")

AUTHORIZATION_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "authorization_scope",
    "stage",
    "batch_id",
    "attempt_id",
    "governing_commit",
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "launch_authority_sha256",
    "owner_authorized",
    "owner_authorization_date",
}
WRAPPER_STAGES = {"DICOM_EXTRACTION", "ECHOPRIME_EMBEDDING"}
SCIENTIFIC_AUTHORIZATION_STAGES = {
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "PRESERVATION_FINALIZATION",
}
STAGE_AUTHORIZATION_SCOPES = {
    "DICOM_EXTRACTION": "EXTRACTION_AUTHORIZATION",
    "ECHOPRIME_EMBEDDING": "ECHOPRIME_INFERENCE_AUTHORIZATION",
    "BATCH_PRESERVATION": "BATCH_PRESERVATION_AUTHORIZATION",
    "PRESERVATION_FINALIZATION": "PRESERVATION_FINALIZATION_AUTHORIZATION",
}
ENVIRONMENT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "governing_commit",
        "captured_at_utc",
        "source_environment_receipt_sha256",
        "python_executable_sha256",
        "python_version",
        "torch_version",
        "torchvision_version",
        "cuda_version",
        "cudnn_version",
        "crc32c_runtime_source",
        "crc32c_python_executable_sha256",
        "crc32c_python_version",
        "crc32c_worker_sha256",
        "crc32c_worker_protocol_version",
        "google_crc32c_version",
        "google_crc32c_implementation",
        "google_crc32c_distribution_sha256",
        "google_crc32c_distribution_file_count",
        "google_crc32c_known_vector_base64",
        "package_inventory_sha256",
        "package_count",
        "package_inventory",
        "operating_system",
        "gpu_execution_performed",
        "cloud_request_performed",
        "dicom_body_read",
        "model_fitted",
        "prediction_generated",
        "confirmatory_performance_accessed",
    }
)


class ProductionStageError(RuntimeError):
    """Fail-closed stage validation error with a stable, non-sensitive code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProductionStageError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def load_json_object(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError(f"{code}_NOT_REGULAR")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except ProductionStageError:
        raise
    except Exception as exc:
        raise ProductionStageError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ProductionStageError(f"{code}_NOT_OBJECT")
    return value


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("HASH_INPUT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolved_python_executable_sha256(path: Path) -> str:
    """Hash the regular target of an expected virtual-environment symlink."""
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProductionStageError("PYTHON_EXECUTABLE_RESOLUTION_FAILED") from exc
    return sha256_file(resolved)


def _validate_hash(value: Any, code: str) -> str:
    text = str(value)
    if not SHA256_RE.fullmatch(text):
        raise ProductionStageError(code)
    return text


def safe_relative_path(value: Any) -> str:
    text = str(value)
    if (
        not text
        or text != text.strip()
        or text.startswith(("/", "~"))
        or "\\" in text
        or any(ord(character) < 32 for character in text)
    ):
        raise ProductionStageError("UNSAFE_RELATIVE_PATH")
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != text:
        raise ProductionStageError("UNSAFE_RELATIVE_PATH")
    return text


def require_projectnb_path(path: Path, *, must_exist: bool = False) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ProductionStageError("OUTPUT_ROOT_NOT_ABSOLUTE_REGULAR_PROJECTNB_PATH")
    try:
        relative = path.relative_to(PROJECTNB_PREFIX)
    except ValueError as exc:
        raise ProductionStageError("OUTPUT_ROOT_OUTSIDE_PROJECTNB") from exc
    cursor = PROJECTNB_PREFIX
    for part in relative.parts[:-1] if relative.parts else ():
        cursor /= part
        if cursor.exists() or cursor.is_symlink():
            try:
                metadata = os.lstat(cursor)
            except OSError as exc:
                raise ProductionStageError("OUTPUT_ROOT_ANCESTOR_INVALID") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ProductionStageError("OUTPUT_ROOT_SYMLINK_ANCESTOR")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ProductionStageError("OUTPUT_ROOT_ANCESTOR_INVALID")
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(PROJECTNB_PREFIX)
    except ValueError as exc:
        raise ProductionStageError("OUTPUT_ROOT_OUTSIDE_PROJECTNB") from exc
    if must_exist and not resolved.is_dir():
        raise ProductionStageError("OUTPUT_ROOT_NOT_DIRECTORY")
    return resolved


def require_authority_worktree(path: Path, governing_commit: str) -> Path:
    if not COMMIT_RE.fullmatch(governing_commit):
        raise ProductionStageError("INVALID_GOVERNING_COMMIT")
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ProductionStageError("AUTHORITY_WORKTREE_INVALID")
    required = (
        path / "scripts" / "lvef_c3_orchestration_core.py",
        path / "scripts" / "lvef_c3_production_stages.py",
        path / "scripts" / "lvef_reconstruction_smoke.py",
    )
    if any(item.is_symlink() or not item.is_file() for item in required):
        raise ProductionStageError("AUTHORITY_WORKTREE_HELPER_MISSING")
    def git_value(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments], capture_output=True, text=True,
            check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode != 0:
            raise ProductionStageError("AUTHORITY_WORKTREE_GIT_CHECK_FAILED")
        return result.stdout.strip()
    if git_value("branch", "--show-current") != "codex/lvef-multitask-revalidation":
        raise ProductionStageError("AUTHORITY_WORKTREE_BRANCH_MISMATCH")
    if git_value("rev-parse", "HEAD") != governing_commit:
        raise ProductionStageError("AUTHORITY_WORKTREE_COMMIT_MISMATCH")
    if git_value("status", "--porcelain", "--untracked-files=no"):
        raise ProductionStageError("AUTHORITY_WORKTREE_TRACKED_DIRTY")
    return path.resolve()


def validate_stage_authorization(
    receipt_path: Path,
    *,
    stage: str,
    batch_id: str,
    attempt_id: str,
    governing_commit: str,
    orchestration_contract_sha256: str,
    batch_plan_sha256: str,
    launch_authority_sha256: str,
) -> dict[str, Any]:
    receipt = load_json_object(receipt_path, "STAGE_AUTHORIZATION_RECEIPT")
    return validate_stage_authorization_value(
        receipt,
        stage=stage,
        batch_id=batch_id,
        attempt_id=attempt_id,
        governing_commit=governing_commit,
        orchestration_contract_sha256=orchestration_contract_sha256,
        batch_plan_sha256=batch_plan_sha256,
        launch_authority_sha256=launch_authority_sha256,
    )


def validate_stage_authorization_value(
    receipt: Mapping[str, Any], *, stage: str, batch_id: str, attempt_id: str,
    governing_commit: str, orchestration_contract_sha256: str,
    batch_plan_sha256: str, launch_authority_sha256: str,
) -> dict[str, Any]:
    if stage not in SCIENTIFIC_AUTHORIZATION_STAGES:
        raise ProductionStageError("UNKNOWN_PRODUCTION_STAGE")
    if (
        stage == "PRESERVATION_FINALIZATION" and batch_id != "all_batches"
    ) or (
        stage != "PRESERVATION_FINALIZATION" and not BATCH_RE.fullmatch(batch_id)
    ):
        raise ProductionStageError("STAGE_AUTHORIZATION_BATCH_SCOPE_INVALID")
    if not ATTEMPT_RE.fullmatch(attempt_id) or not COMMIT_RE.fullmatch(governing_commit):
        raise ProductionStageError("STAGE_AUTHORIZATION_IDENTITY_INVALID")
    if set(receipt) != AUTHORIZATION_KEYS:
        raise ProductionStageError("STAGE_AUTHORIZATION_SCHEMA_MISMATCH")
    expected = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
        "status": "AUTHORIZED",
        "authorization_scope": STAGE_AUTHORIZATION_SCOPES[stage],
        "stage": stage,
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "orchestration_contract_sha256": orchestration_contract_sha256,
        "batch_plan_sha256": batch_plan_sha256,
        "launch_authority_sha256": _validate_hash(
            launch_authority_sha256, "LAUNCH_AUTHORITY_HASH_INVALID"
        ),
        "owner_authorized": True,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ProductionStageError("STAGE_AUTHORIZATION_AUTHORITY_MISMATCH")
    date = receipt.get("owner_authorization_date")
    if not isinstance(date, str) or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", date):
        raise ProductionStageError("STAGE_AUTHORIZATION_DATE_INVALID")
    return receipt


def validate_wrapper_authority(
    *,
    stage: str,
    batch_id: str,
    attempt_id: str,
    governing_commit: str,
    authority_worktree: Path,
    orchestration_contract: Path,
    batch_plan: Path,
    environment_receipt: Path,
    output_root: Path,
    requirements: Any | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if stage not in WRAPPER_STAGES:
        raise ProductionStageError("UNKNOWN_PRODUCTION_STAGE")
    if not BATCH_RE.fullmatch(batch_id):
        raise ProductionStageError("INVALID_BATCH_ID")
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise ProductionStageError("INVALID_ATTEMPT_ID")
    require_authority_worktree(authority_worktree, governing_commit)
    import lvef_c3_orchestration_core as core

    contract_hash = sha256_file(orchestration_contract)
    contract = core.load_orchestration_contract(orchestration_contract)
    plan = core.load_strict_json(batch_plan)
    scoped = requirements is not None or expected_runtime_authority is not None
    if scoped and (requirements is None or expected_runtime_authority is None):
        raise ProductionStageError("SCOPED_RUNTIME_AUTHORITY_ARGUMENTS_INCOMPLETE")
    effective_requirements = (
        requirements if requirements is not None else core.production_requirements(contract)
    )
    plan_hash = core.validate_batch_plan(plan, requirements=effective_requirements)
    if contract_hash != plan["authority"]["orchestration_contract_sha256"]:
        raise ProductionStageError("PLAN_CONTRACT_AUTHORITY_MISMATCH")
    if governing_commit != plan["authority"]["git_commit"]:
        raise ProductionStageError("PLAN_GOVERNING_COMMIT_MISMATCH")
    planned_batch = next(
        (item for item in plan["batches"] if item["batch_id"] == batch_id), None
    )
    if planned_batch is None:
        raise ProductionStageError("BATCH_NOT_PRESENT_IN_PLAN")
    environment_receipt_sha256 = sha256_file(environment_receipt)
    validate_environment_receipt_against_current_runtime(environment_receipt)
    if scoped:
        runtime_authority = core.validate_runtime_authority(expected_runtime_authority)
        if (
            runtime_authority["batch_plan_sha256"] != plan_hash
            or any(
                runtime_authority[key] != str(plan["authority"][key])
                for key in core.PLAN_AUTHORITY_KEYS
            )
            or runtime_authority["git_commit"] != governing_commit
            or runtime_authority["environment_receipt_sha256"]
            != environment_receipt_sha256
            or runtime_authority["checkpoint_sha256"] != CHECKPOINT_SHA256
            or plan["authority"]["state_machine_schema_sha256"]
            != str(contract["authority"]["state_machine_schema_sha256"])
            or plan["authority"]["resume_ledger_schema_sha256"]
            != str(contract["authority"]["resume_ledger_schema_sha256"])
        ):
            raise ProductionStageError("SCOPED_RUNTIME_AUTHORITY_MISMATCH")
    else:
        runtime_authority = core.derive_expected_runtime_authority(
            plan,
            requirements=effective_requirements,
            contract=contract,
            contract_path=orchestration_contract,
            governing_commit=governing_commit,
            environment_receipt_sha256=environment_receipt_sha256,
        )
    require_projectnb_path(output_root, must_exist=False)
    return {
        "stage": stage,
        "batch_id_valid": True,
        "attempt_id_valid": True,
        "governing_commit_valid": bool(COMMIT_RE.fullmatch(governing_commit)),
        "orchestration_contract_sha256": contract_hash,
        "batch_plan_sha256": plan_hash,
        "runtime_authority": runtime_authority,
        "expected_object_keys": {
            row["source_object_key"] for row in planned_batch["objects"]
        },
        "planned_batch": planned_batch,
        "output_root_projectnb_bound": True,
        "real_execution_performed": False,
    }


def validate_production_dicom_rows(
    rows: Sequence[Mapping[str, Any]], *, expected_objects: int, expected_studies: int
) -> dict[str, Any]:
    required = {
        "subject_id",
        "study_id",
        "source_relative_path",
        "read_ok",
        "is_multiframe",
        "pixel_decode_ok",
    }
    if len(rows) != expected_objects or expected_objects < 1:
        raise ProductionStageError("DICOM_OBJECT_COUNT_MISMATCH")
    locators: set[str] = set()
    studies: set[Any] = set()
    readable = multiframe = single_frame = decode_failures = 0
    for row in rows:
        if not required.issubset(row):
            raise ProductionStageError("DICOM_AUDIT_ROW_SCHEMA_MISMATCH")
        locator = safe_relative_path(row["source_relative_path"])
        if locator in locators:
            raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
        locators.add(locator)
        studies.add(row["study_id"])
        read_ok = row["read_ok"] is True
        is_multiframe = row["is_multiframe"] is True
        pixel_decode_ok = row["pixel_decode_ok"] is True
        readable += int(read_ok)
        multiframe += int(read_ok and is_multiframe)
        single_frame += int(read_ok and not is_multiframe)
        decode_failures += int(read_ok and is_multiframe and not pixel_decode_ok)
        if not read_ok and (is_multiframe or pixel_decode_ok):
            raise ProductionStageError("DICOM_AUDIT_STATE_CONTRADICTION")
        if pixel_decode_ok and not is_multiframe:
            raise ProductionStageError("PIXEL_DECODE_RECORDED_FOR_NONCINE")
    if len(studies) != expected_studies:
        raise ProductionStageError("DICOM_STUDY_COUNT_MISMATCH")
    return {
        "n_objects": len(rows),
        "n_studies": len(studies),
        "n_readable": readable,
        "n_unreadable": len(rows) - readable,
        "n_multiframe_candidates": multiframe,
        "n_single_frame": single_frame,
        "n_pixel_decode_failures": decode_failures,
        "physical_source_keys_unique": True,
    }


def validate_production_extraction_rows(
    rows: Sequence[Mapping[str, Any]], *, expected_cines: int
) -> dict[str, Any]:
    required = {
        "study_id",
        "clip_key",
        "physical_source_key",
        "write_ok",
        "frames_shape",
        "frames_dtype",
        "mask_status",
        "temporal_sampling_policy",
        "pixel_decode_ok",
        "npz_sha256",
    }
    if len(rows) != expected_cines:
        raise ProductionStageError("EXTRACTION_CINE_COUNT_MISMATCH")
    clip_keys: set[str] = set()
    source_keys: set[str] = set()
    studies: set[Any] = set()
    for row in rows:
        if not required.issubset(row):
            raise ProductionStageError("EXTRACTION_ROW_SCHEMA_MISMATCH")
        clip_key = _validate_hash(row["clip_key"], "INVALID_CLIP_KEY")
        source_key = _validate_hash(row["physical_source_key"], "INVALID_SOURCE_KEY")
        if clip_key in clip_keys:
            raise ProductionStageError("DUPLICATE_CLIP_KEY")
        if source_key in source_keys:
            raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
        clip_keys.add(clip_key)
        source_keys.add(source_key)
        studies.add(row["study_id"])
        if row["write_ok"] is not True or row["pixel_decode_ok"] is not True:
            raise ProductionStageError("INCOMPLETE_EXTRACTION_ROW")
        if row["frames_shape"] != "32x224x224x3" or row["frames_dtype"] != "uint8":
            raise ProductionStageError("EXTRACTION_SHAPE_OR_DTYPE_MISMATCH")
        if row["mask_status"] != "APPLIED":
            raise ProductionStageError("EXTRACTION_MASK_GATE_FAILED")
        if row["temporal_sampling_policy"] != (
            "historical_compatible_linspace_or_tail_repeat_v1"
        ):
            raise ProductionStageError("EXTRACTION_SAMPLING_POLICY_MISMATCH")
        _validate_hash(row["npz_sha256"], "INVALID_EXTRACTION_HASH")
    return {
        "n_extracted_clips": len(rows),
        "n_studies_with_extracted_clips": len(studies),
        "clip_keys_unique": True,
        "physical_source_keys_unique": True,
        "all_shapes_and_dtypes_valid": True,
        "all_pixel_decodes_passed": True,
    }


def validate_embedding_values(
    values: Sequence[Sequence[Any]], *, expected_rows: int
) -> dict[str, Any]:
    if len(values) != expected_rows or expected_rows < 1:
        raise ProductionStageError("EMBEDDING_ROW_COUNT_MISMATCH")
    for vector in values:
        if len(vector) != EXPECTED_EMBEDDING_DIMENSION:
            raise ProductionStageError("EMBEDDING_DIMENSION_MISMATCH")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
            raise ProductionStageError("EMBEDDING_VALUE_NOT_NUMERIC")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ProductionStageError("EMBEDDING_VALUE_NONFINITE")
    return {
        "n_embeddings": expected_rows,
        "embedding_dimension": EXPECTED_EMBEDDING_DIMENSION,
        "embedding_dtype": "float32",
        "all_finite": True,
    }


def validate_environment_receipt_payload(
    receipt: Mapping[str, Any], *, live_packages: Sequence[Mapping[str, str]],
    live_runtime: Mapping[str, str]
) -> None:
    """Validate the retained package preimage and exact live runtime authority."""
    if set(receipt) != ENVIRONMENT_RECEIPT_KEYS:
        raise ProductionStageError("ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH")
    if (
        receipt.get("schema_version") != 3
        or receipt.get("artifact_type")
        != "lvef_c3_production_environment_authority_v3"
        or receipt.get("status")
        != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        or not COMMIT_RE.fullmatch(str(receipt.get("governing_commit")))
    ):
        raise ProductionStageError("ENVIRONMENT_RECEIPT_IDENTITY_MISMATCH")
    _validate_hash(
        receipt.get("source_environment_receipt_sha256"),
        "SOURCE_ENVIRONMENT_HASH_INVALID",
    )
    _validate_hash(receipt.get("python_executable_sha256"), "PYTHON_HASH_INVALID")
    _validate_hash(
        receipt.get("crc32c_python_executable_sha256"),
        "CRC32C_PYTHON_HASH_INVALID",
    )
    _validate_hash(receipt.get("crc32c_worker_sha256"), "CRC32C_WORKER_HASH_INVALID")
    _validate_hash(
        receipt.get("google_crc32c_distribution_sha256"),
        "CRC32C_DISTRIBUTION_HASH_INVALID",
    )
    _validate_hash(receipt.get("package_inventory_sha256"), "PACKAGE_HASH_INVALID")
    if (
        receipt.get("crc32c_runtime_source")
        != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or receipt.get("crc32c_worker_protocol_version") != 1
        or receipt.get("google_crc32c_implementation") != "c"
        or receipt.get("google_crc32c_known_vector_base64") != "4waSgw=="
        or not isinstance(receipt.get("crc32c_python_version"), str)
        or not receipt["crc32c_python_version"]
        or not isinstance(receipt.get("google_crc32c_version"), str)
        or not receipt["google_crc32c_version"]
        or not isinstance(
            receipt.get("google_crc32c_distribution_file_count"), int
        )
        or isinstance(
            receipt.get("google_crc32c_distribution_file_count"), bool
        )
        or receipt["google_crc32c_distribution_file_count"] < 1
    ):
        raise ProductionStageError("CRC32C_AUXILIARY_AUTHORITY_INVALID")
    try:
        captured = datetime.fromisoformat(str(receipt.get("captured_at_utc")))
    except ValueError as exc:
        raise ProductionStageError("ENVIRONMENT_CAPTURE_TIMESTAMP_INVALID") from exc
    if captured.tzinfo is None or captured.utcoffset() != timezone.utc.utcoffset(captured):
        raise ProductionStageError("ENVIRONMENT_CAPTURE_TIMESTAMP_INVALID")
    for flag in (
        "gpu_execution_performed",
        "cloud_request_performed",
        "dicom_body_read",
        "model_fitted",
        "prediction_generated",
        "confirmatory_performance_accessed",
    ):
        if receipt.get(flag) is not False:
            raise ProductionStageError("ENVIRONMENT_RECEIPT_ACTIVITY_FLAG_INVALID")
    packages = receipt.get("package_inventory")
    if not isinstance(packages, list) or not packages:
        raise ProductionStageError("PACKAGE_INVENTORY_SCHEMA_INVALID")
    normalized_names: set[str] = set()
    prior_sort_key: tuple[str, str] | None = None
    for row in packages:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"name", "version"}
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
            or not isinstance(row.get("version"), str)
            or not row["version"].strip()
        ):
            raise ProductionStageError("PACKAGE_INVENTORY_SCHEMA_INVALID")
        normalized_name = re.sub(r"[-_.]+", "-", row["name"]).casefold()
        if normalized_name in normalized_names:
            raise ProductionStageError("PACKAGE_INVENTORY_DUPLICATE_NAME")
        normalized_names.add(normalized_name)
        sort_key = (row["name"].casefold(), row["version"])
        if prior_sort_key is not None and sort_key < prior_sort_key:
            raise ProductionStageError("PACKAGE_INVENTORY_NOT_SORTED")
        prior_sort_key = sort_key
    package_hash = hashlib.sha256(
        json.dumps(packages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        isinstance(receipt.get("package_count"), bool)
        or receipt.get("package_count") != len(packages)
        or package_hash != receipt.get("package_inventory_sha256")
        or packages != list(live_packages)
    ):
        raise ProductionStageError("RUNNING_PACKAGE_INVENTORY_MISMATCH")
    for key, value in live_runtime.items():
        if str(receipt.get(key)) != str(value):
            raise ProductionStageError("RUNNING_ENVIRONMENT_RUNTIME_MISMATCH")


def validate_environment_receipt_against_current_runtime(
    environment_receipt: Path,
) -> dict[str, Any]:
    receipt = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    try:
        import torch
        import torchvision
    except Exception as exc:
        raise ProductionStageError("ENVIRONMENT_RUNTIME_UNAVAILABLE") from exc
    cudnn_version = torch.backends.cudnn.version()
    if torch.version.cuda is None or cudnn_version is None:
        raise ProductionStageError("CUDA_CUDNN_RUNTIME_UNAVAILABLE")
    packages = sorted(
        (
            {"name": str(dist.metadata.get("Name")), "version": str(dist.version)}
            for dist in importlib.metadata.distributions()
            if dist.metadata.get("Name")
        ),
        key=lambda item: (item["name"].casefold(), item["version"]),
    )
    validate_environment_receipt_payload(
        receipt,
        live_packages=packages,
        live_runtime={
            "python_executable_sha256": resolved_python_executable_sha256(
                Path(sys.executable)
            ),
            "python_version": platform.python_version(),
            "torch_version": str(torch.__version__),
            "torchvision_version": str(torchvision.__version__),
            "cuda_version": str(torch.version.cuda),
            "cudnn_version": str(cudnn_version),
            "operating_system": platform.platform(),
        },
    )
    return receipt


def validate_crc32c_external_authority(
    environment_receipt: Path,
    crc32c_python: Path,
    crc32c_worker: Path,
) -> Mapping[str, Any]:
    import capture_lvef_c3_production_environment as capture

    receipt = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    if sha256_file(crc32c_python) != receipt.get(
        "crc32c_python_executable_sha256"
    ) or sha256_file(crc32c_worker) != receipt.get("crc32c_worker_sha256"):
        raise ProductionStageError("CRC32C_EXTERNAL_FILE_AUTHORITY_MISMATCH")
    try:
        probe = capture.probe_crc32c_runtime(
            crc32c_python,
            crc32c_worker,
            expected_python_sha256=str(
                receipt["crc32c_python_executable_sha256"]
            ),
        )
    except (capture.EnvironmentAuthorityError, OSError, subprocess.SubprocessError) as exc:
        raise ProductionStageError("CRC32C_EXTERNAL_RUNTIME_UNAVAILABLE") from exc
    expected = {
        "python_version": receipt.get("crc32c_python_version"),
        "google_crc32c_version": receipt.get("google_crc32c_version"),
        "google_crc32c_implementation": receipt.get(
            "google_crc32c_implementation"
        ),
        "google_crc32c_distribution_sha256": receipt.get(
            "google_crc32c_distribution_sha256"
        ),
        "google_crc32c_distribution_file_count": receipt.get(
            "google_crc32c_distribution_file_count"
        ),
        "known_vector_crc32c_base64": receipt.get(
            "google_crc32c_known_vector_base64"
        ),
    }
    if any(probe.get(key) != value for key, value in expected.items()):
        raise ProductionStageError("CRC32C_EXTERNAL_RUNTIME_AUTHORITY_MISMATCH")
    return receipt


def validate_checkpoint_and_environment(
    checkpoint: Path,
    environment_receipt: Path,
    *,
    crc32c_python: Path | None = None,
    crc32c_worker: Path | None = None,
) -> dict[str, Any]:
    if checkpoint.name != CHECKPOINT_FILENAME:
        raise ProductionStageError("CHECKPOINT_FILENAME_MISMATCH")
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise ProductionStageError("CHECKPOINT_NOT_REGULAR")
    if checkpoint.stat(follow_symlinks=False).st_size != CHECKPOINT_BYTES:
        raise ProductionStageError("CHECKPOINT_SIZE_MISMATCH")
    if sha256_file(checkpoint) != CHECKPOINT_SHA256:
        raise ProductionStageError("CHECKPOINT_SHA256_MISMATCH")
    validate_environment_receipt_against_current_runtime(environment_receipt)
    if (crc32c_python is None) is not (crc32c_worker is None):
        raise ProductionStageError("CRC32C_EXTERNAL_AUTHORITY_PAIR_INCOMPLETE")
    if crc32c_python is not None and crc32c_worker is not None:
        validate_crc32c_external_authority(
            environment_receipt, crc32c_python, crc32c_worker
        )
    return {
        "checkpoint_identity_passed": True,
        "environment_receipt_complete": True,
        "encoder_only_required": True,
        "view_classifier_permitted": False,
    }


def _atomic_finalize_stage_directory(partial: Path, final: Path) -> None:
    if final.exists() or final.is_symlink():
        raise ProductionStageError("STAGE_FINAL_OUTPUT_ALREADY_EXISTS")
    if partial.is_symlink() or not partial.is_dir():
        raise ProductionStageError("STAGE_PARTIAL_OUTPUT_INVALID")
    try:
        os.rename(partial, final)
    except OSError as exc:
        raise ProductionStageError("STAGE_ATOMIC_DIRECTORY_RENAME_FAILED") from exc


def _stage_completion_receipt(
    *, stage: str, stage_directory: Path, batch_id: str, attempt_id: str,
    runtime_authority: Mapping[str, Any], input_manifest: Path,
    artifact_names: Sequence[str],
) -> dict[str, Any]:
    artifacts = {
        name: sha256_file(stage_directory / name) for name in artifact_names
    }
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_stage_completion_receipt_v1",
        "status": "PASS_STAGE_OUTPUT_ATOMICALLY_FINALIZABLE",
        "stage": stage,
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "runtime_authority": dict(runtime_authority),
        "input_manifest_sha256": sha256_file(input_manifest),
        "artifacts": artifacts,
    }


def validate_completed_stage_for_recovery(
    *, stage_directory: Path, stage: str, batch_id: str, attempt_id: str,
    runtime_authority: Mapping[str, Any], input_manifest: Path,
    artifact_names: Sequence[str], summary_name: str,
) -> dict[str, Any]:
    """Validate a completed stage after a crash before its ledger promotion."""
    if stage_directory.is_symlink() or not stage_directory.is_dir():
        raise ProductionStageError("RECOVERY_STAGE_DIRECTORY_INVALID")
    receipt = load_json_object(
        stage_directory / "stage_completion_receipt.restricted.json",
        "STAGE_COMPLETION_RECEIPT",
    )
    expected = _stage_completion_receipt(
        stage=stage,
        stage_directory=stage_directory,
        batch_id=batch_id,
        attempt_id=attempt_id,
        runtime_authority=runtime_authority,
        input_manifest=input_manifest,
        artifact_names=artifact_names,
    )
    if receipt != expected:
        raise ProductionStageError("STAGE_COMPLETION_RECOVERY_AUTHORITY_MISMATCH")
    return load_json_object(stage_directory / summary_name, "RECOVERED_STAGE_SUMMARY")


def run_production_dicom_extraction(
    *,
    verified_download_manifest: Path,
    download_root: Path,
    batch_output_root: Path,
    workers: int,
    batch_id: str,
    attempt_id: str,
    runtime_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute header audit and extraction after an external authorization gate.

    This function itself performs no authorization inference; callers must run
    :func:`validate_stage_authorization` first.  Any failure leaves the uniquely
    named partial stage directory intact as restricted evidence.
    """

    if workers < 1:
        raise ProductionStageError("EXTRACTION_WORKERS_INVALID")
    require_projectnb_path(download_root, must_exist=True)
    batch_root = require_projectnb_path(batch_output_root, must_exist=True)
    if verified_download_manifest.is_symlink() or not verified_download_manifest.is_file():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_REGULAR")
    import pandas as pd  # optional; execution path only
    import lvef_reconstruction_smoke as smoke

    frame = pd.read_csv(verified_download_manifest, low_memory=False)
    required = {
        "subject_id",
        "study_id",
        "source_relative_path",
        "download_ok",
        "observed_sha256",
        "physical_source_key",
    }
    if set(frame.columns) != required or frame.empty:
        raise ProductionStageError("DOWNLOAD_MANIFEST_SCHEMA_MISMATCH")
    if not frame["download_ok"].map(smoke.parse_bool).all():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_FULLY_VERIFIED")
    if frame["source_relative_path"].duplicated().any():
        raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
    records = frame.sort_values("source_relative_path", kind="mergesort").to_dict(
        orient="records"
    )
    for record in records:
        record["source_authority_relative_path"] = safe_relative_path(
            record["source_relative_path"]
        )
        record["smoke_role"] = "production_selected"
        _validate_hash(record["observed_sha256"], "DOWNLOAD_SHA256_INVALID")
        _validate_hash(record["physical_source_key"], "PHYSICAL_SOURCE_KEY_INVALID")
        record["source_relative_path"] = f"{record['physical_source_key']}.dcm"

    partial = batch_root / "dicom_extraction.partial"
    final = batch_root / "dicom_extraction"
    if partial.exists() or partial.is_symlink() or final.exists() or final.is_symlink():
        raise ProductionStageError("DICOM_EXTRACTION_ATTEMPT_ALREADY_EXISTS")
    partial.mkdir(mode=0o700)
    clips_root = partial / "clips"
    clips_root.mkdir(mode=0o700)
    for record in records:
        downloaded = download_root / record["source_relative_path"]
        if downloaded.is_symlink() or not downloaded.is_file():
            smoke.write_json_atomic(
                partial / "failure.summary.json",
                {"status": "FAIL_DOWNLOAD_FILE_NOT_REGULAR", "identifiers_emitted": False, "paths_emitted": False},
            )
            raise ProductionStageError("DOWNLOADED_DICOM_NOT_REGULAR")
        if sha256_file(downloaded) != record["observed_sha256"]:
            smoke.write_json_atomic(
                partial / "failure.summary.json",
                {"status": "FAIL_POSTDOWNLOAD_HASH_MISMATCH", "identifiers_emitted": False, "paths_emitted": False},
            )
            raise ProductionStageError("POSTDOWNLOAD_DICOM_HASH_MISMATCH")
    if workers == 1:
        header_rows = [
            smoke._dicom_header_row(record, str(download_root)) for record in records
        ]
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            header_rows = list(
                pool.map(
                    smoke._dicom_header_row,
                    records,
                    [str(download_root)] * len(records),
                )
            )
    header = pd.DataFrame(header_rows).sort_values(
        "source_relative_path", kind="mergesort"
    ).reset_index(drop=True)
    physical_by_path = {
        record["source_relative_path"]: record["physical_source_key"] for record in records
    }
    cine_records = [
        record
        for record in header.to_dict(orient="records")
        if record["read_ok"] is True and record["is_multiframe"] is True
    ]
    if workers == 1:
        extraction_rows = [
            smoke._extract_one(record, str(download_root), str(clips_root))
            for record in cine_records
        ]
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            extraction_rows = list(
                pool.map(
                    smoke._extract_one,
                    cine_records,
                    [str(download_root)] * len(cine_records),
                    [str(clips_root)] * len(cine_records),
                )
            )
    for extracted in extraction_rows:
        extracted["physical_source_key"] = physical_by_path[
            extracted["source_relative_path"]
        ]
        extracted["pixel_decode_ok"] = extracted["write_ok"] is True
    extraction_rows.sort(key=lambda row: str(row["source_relative_path"]))
    decode_by_path = {
        row["source_relative_path"]: row["pixel_decode_ok"] for row in extraction_rows
    }
    header["pixel_decode_ok"] = header["source_relative_path"].map(decode_by_path).fillna(False)
    smoke.write_csv_atomic(partial / "dicom_audit.restricted.csv", header)
    extraction = pd.DataFrame(extraction_rows)
    smoke.write_csv_atomic(partial / "extraction_manifest.restricted.csv", extraction)
    dicom_summary = validate_production_dicom_rows(
        header.to_dict(orient="records"),
        expected_objects=len(records),
        expected_studies=int(frame["study_id"].nunique()),
    )
    if dicom_summary["n_unreadable"] or dicom_summary["n_pixel_decode_failures"]:
        smoke.write_json_atomic(
            partial / "failure.summary.json",
            {"status": "FAIL_DICOM_OR_PIXEL_DECODE_GATE", **dicom_summary, "identifiers_emitted": False, "paths_emitted": False},
        )
        raise ProductionStageError("DICOM_OR_PIXEL_DECODE_GATE_FAILED")
    try:
        extraction_summary = validate_production_extraction_rows(
            extraction.to_dict(orient="records"),
            expected_cines=dicom_summary["n_multiframe_candidates"],
        )
    except ProductionStageError as exc:
        smoke.write_json_atomic(
            partial / "failure.summary.json",
            {"status": "FAIL_EXTRACTION_GATE", "error_code": exc.code, "identifiers_emitted": False, "paths_emitted": False},
        )
        raise
    summary = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v1",
        "status": "PASS_DICOM_EXTRACTION",
        **dicom_summary,
        **extraction_summary,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }
    smoke.write_json_atomic(partial / "dicom_extraction.summary.json", summary)
    smoke.write_json_atomic(
        partial / "stage_completion_receipt.restricted.json",
        _stage_completion_receipt(
            stage="DICOM_EXTRACTION",
            stage_directory=partial,
            batch_id=batch_id,
            attempt_id=attempt_id,
            runtime_authority=runtime_authority,
            input_manifest=verified_download_manifest,
            artifact_names=(
                "dicom_audit.restricted.csv",
                "extraction_manifest.restricted.csv",
                "dicom_extraction.summary.json",
            ),
        ),
    )
    _atomic_finalize_stage_directory(partial, final)
    return summary


def run_production_echoprime(
    *,
    extraction_manifest: Path,
    extraction_root: Path,
    selected_batch_manifest: Path,
    checkpoint: Path,
    environment_receipt: Path,
    orchestration_contract: Path,
    batch_plan: Path,
    batch_id: str,
    batch_output_root: Path,
    batch_size: int,
    seed: int,
    attempt_id: str,
    runtime_authority: Mapping[str, Any],
    requirements: Any | None = None,
) -> dict[str, Any]:
    """Run encoder-only EchoPrime and deterministic study mean pooling.

    Heavy imports, checkpoint loading, and CUDA access occur only inside this
    explicitly called execution function, after the wrapper authorization gate.
    """

    if batch_size < 1:
        raise ProductionStageError("EMBEDDING_BATCH_SIZE_INVALID")
    validate_checkpoint_and_environment(checkpoint, environment_receipt)
    extracted_root = require_projectnb_path(extraction_root, must_exist=True)
    batch_root = require_projectnb_path(batch_output_root, must_exist=True)
    import numpy as np
    import pandas as pd
    import torch
    import torchvision
    import lvef_reconstruction_smoke as smoke
    import lvef_c3_orchestration_core as core
    import preserve_lvef_c3_production_batch as preservation

    environment = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    if (
        str(torch.__version__) != str(environment["torch_version"])
        or str(torchvision.__version__) != str(environment["torchvision_version"])
        or str(torch.version.cuda) != str(environment["cuda_version"])
        or str(torch.backends.cudnn.version()) != str(environment["cudnn_version"])
    ):
        raise ProductionStageError("RUNNING_TORCH_CUDA_ENVIRONMENT_MISMATCH")

    extraction = pd.read_csv(extraction_manifest, low_memory=False)
    selected = pd.read_csv(selected_batch_manifest, low_memory=False)
    if set(selected.columns) != {"subject_id", "study_id"} or selected.empty:
        raise ProductionStageError("SELECTED_BATCH_MANIFEST_SCHEMA_MISMATCH")
    if selected["study_id"].duplicated().any() or selected["subject_id"].duplicated().any():
        raise ProductionStageError("SELECTED_BATCH_OWNERSHIP_NOT_ONE_TO_ONE")
    contract = core.load_orchestration_contract(orchestration_contract)
    plan = core.load_strict_json(batch_plan)
    plan_sha256 = core.validate_batch_plan(
        plan,
        requirements=(
            requirements
            if requirements is not None
            else core.production_requirements(contract)
        ),
    )
    if requirements is not None:
        normalized_runtime = core.validate_runtime_authority(runtime_authority)
        if (
            normalized_runtime["batch_plan_sha256"] != plan_sha256
            or any(
                normalized_runtime[key] != str(plan["authority"][key])
                for key in core.PLAN_AUTHORITY_KEYS
            )
            or normalized_runtime["checkpoint_sha256"] != sha256_file(checkpoint)
            or normalized_runtime["environment_receipt_sha256"]
            != sha256_file(environment_receipt)
        ):
            raise ProductionStageError("SCOPED_ECHOPRIME_RUNTIME_AUTHORITY_MISMATCH")
    planned_batch = next((item for item in plan["batches"] if item["batch_id"] == batch_id), None)
    if planned_batch is None:
        raise ProductionStageError("SELECTED_BATCH_NOT_PLANNED")
    expected_selected = sorted(
        (str(item["subject_id"]), str(item["study_id"])) for item in planned_batch["studies"]
    )
    observed_selected = sorted(
        zip(selected["subject_id"].astype(str), selected["study_id"].astype(str))
    )
    if observed_selected != expected_selected:
        raise ProductionStageError("SELECTED_BATCH_PLAN_MEMBERSHIP_MISMATCH")
    expected_cines = len(extraction)
    validate_production_extraction_rows(
        extraction.to_dict(orient="records"), expected_cines=expected_cines
    )
    work = extraction.sort_values(["study_id", "clip_key"], kind="mergesort").reset_index(
        drop=True
    )
    if not set(work["study_id"]).issubset(set(selected["study_id"])):
        raise ProductionStageError("OUTSIDE_SELECTED_STUDY_IN_EXTRACTION")
    if not work.groupby("study_id", dropna=False)["subject_id"].nunique(dropna=False).eq(1).all():
        raise ProductionStageError("EXTRACTION_STUDY_OWNERSHIP_CONFLICT")

    partial = batch_root / "echoprime.partial"
    final = batch_root / "echoprime"
    if partial.exists() or partial.is_symlink() or final.exists() or final.is_symlink():
        raise ProductionStageError("ECHOPRIME_ATTEMPT_ALREADY_EXISTS")
    partial.mkdir(mode=0o700)
    smoke.configure_torch_determinism(torch, seed)
    if not torch.cuda.is_available():
        raise ProductionStageError("CUDA_UNAVAILABLE")
    device = torch.device("cuda")
    model = torchvision.models.video.mvit_v2_s(weights=None)
    model.head[-1] = torch.nn.Linear(model.head[-1].in_features, 512)
    state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval().to(device)
    for parameter in model.parameters():
        parameter.requires_grad = False
    ordered_records = work.to_dict(orient="records")
    vectors: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, len(ordered_records), batch_size):
            mini_records = ordered_records[start : start + batch_size]
            tensors = [
                smoke._prepare_encoder_input(
                    smoke._load_extracted_frames(record, extracted_root), torch
                )
                for record in mini_records
            ]
            batch = torch.stack(tensors, dim=0).to(device)
            result = model(batch).detach().cpu().numpy().astype(np.float32, copy=False)
            if result.shape != (len(tensors), 512) or not np.isfinite(result).all():
                raise ProductionStageError("ENCODER_OUTPUT_GATE_FAILED")
            vectors.extend(result)
            del batch, tensors, result
    clip_array = np.stack(vectors).astype(np.float32, copy=False)
    validate_embedding_values(clip_array.tolist(), expected_rows=len(work))
    clip_rows: list[dict[str, Any]] = []
    for index, (record, vector) in enumerate(zip(work.to_dict(orient="records"), clip_array)):
        clip_rows.append(
            {
                "embedding_idx": index,
                "subject_id": record["subject_id"],
                "study_id": record["study_id"],
                "clip_key": record["clip_key"],
                "physical_source_key": record["physical_source_key"],
                "embedding_l2_norm": float(np.linalg.norm(vector.astype(np.float64))),
                "embedding_sha256": smoke.array_content_sha256(vector),
                "write_ok": True,
            }
        )
    clip_manifest = pd.DataFrame(clip_rows)
    if clip_manifest["clip_key"].duplicated().any() or clip_manifest["physical_source_key"].duplicated().any():
        raise ProductionStageError("DUPLICATE_EMBEDDED_CLIP_OR_SOURCE")
    study_rows: list[dict[str, Any]] = []
    for study_id, group in clip_manifest.groupby("study_id", sort=True, dropna=False):
        study_rows.append(
            {
                "study_idx": len(study_rows),
                "subject_id": group["subject_id"].iloc[0],
                "study_id": study_id,
                "n_clips": len(group),
            }
        )
    try:
        study_array = preservation.mean_pool_study_embeddings(
            clip_embeddings=clip_array,
            clip_rows=clip_rows,
            study_rows=study_rows,
        )
    except preservation.BatchPreservationError as exc:
        raise ProductionStageError("POOLED_EMBEDDING_SEMANTICS_INVALID") from exc
    for row, vector in zip(study_rows, study_array, strict=True):
        row["embedding_sha256"] = smoke.array_content_sha256(vector)
    study_manifest = pd.DataFrame(study_rows)
    pooled = set(study_manifest["study_id"])
    disposition = selected.copy()
    disposition["disposition"] = disposition["study_id"].map(
        lambda value: "IMAGING_ELIGIBLE" if value in pooled else "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    )
    smoke.write_npz_atomic(partial / "clip_embeddings.restricted.npz", embeddings=clip_array)
    smoke.write_csv_atomic(partial / "clip_manifest.restricted.csv", clip_manifest)
    smoke.write_npz_atomic(partial / "study_embeddings.restricted.npz", embeddings=study_array)
    smoke.write_csv_atomic(partial / "study_manifest.restricted.csv", study_manifest)
    smoke.write_csv_atomic(partial / "study_disposition.restricted.csv", disposition)
    summary = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_batch_echoprime_pooling_summary_v1",
        "status": "PASS_ECHOPRIME_AND_POOLING",
        "n_clip_embeddings": int(len(clip_array)),
        "n_pooled_studies": int(len(study_array)),
        "n_no_cine_studies": int(len(selected) - len(study_array)),
        "embedding_dimension": 512,
        "embedding_dtype": "float32",
        "all_finite": True,
        "encoder_only": True,
        "view_classifier_used": False,
        "pooling": "stable_clip_key_order_float64_mean_then_float32",
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }
    smoke.write_json_atomic(partial / "echoprime_pooling.summary.json", summary)
    smoke.write_json_atomic(
        partial / "stage_completion_receipt.restricted.json",
        _stage_completion_receipt(
            stage="ECHOPRIME_EMBEDDING",
            stage_directory=partial,
            batch_id=batch_id,
            attempt_id=attempt_id,
            runtime_authority=runtime_authority,
            input_manifest=extraction_manifest,
            artifact_names=(
                "clip_embeddings.restricted.npz",
                "clip_manifest.restricted.csv",
                "study_embeddings.restricted.npz",
                "study_manifest.restricted.csv",
                "study_disposition.restricted.csv",
                "echoprime_pooling.summary.json",
            ),
        ),
    )
    _atomic_finalize_stage_directory(partial, final)
    return summary


def advance_stage_ledger(
    *, input_ledger: Path, output_ledger: Path, receipt_root: Path,
    batch_id: str, transitions: Sequence[tuple[str, str]],
    expected_authority: Mapping[str, Any], expected_attempt_id: str,
    expected_object_keys: set[str],
) -> dict[str, Any]:
    """Apply an exact consecutive transition chain and preserve every receipt."""
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    if not isinstance(ledger, Mapping):
        raise ProductionStageError("INPUT_LEDGER_NOT_MAPPING")
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    if batch_id not in ledger["batches"]:
        raise ProductionStageError("LEDGER_BATCH_NOT_PLANNED")
    if receipt_root.is_symlink() or (receipt_root.exists() and not receipt_root.is_dir()):
        raise ProductionStageError("TRANSITION_RECEIPT_ROOT_COLLISION")
    receipt_root.mkdir(mode=0o700, exist_ok=True)
    updated = ledger
    for target_state, output_sha in transitions:
        _validate_hash(output_sha, "TRANSITION_OUTPUT_HASH_INVALID")
        batch = updated["batches"][batch_id]
        predecessor = (
            batch["events"][-1]["receipt_sha256"]
            if batch["events"]
            else updated["authority"]["batch_plan_sha256"]
        )
        receipt = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": updated["attempt_id"],
            "batch_id": batch_id,
            "from_state": batch["state"],
            "to_state": target_state,
            "status": "PASS",
            "authority": updated["authority"],
            "input_receipt_sha256": [predecessor],
            "output_manifest_sha256": output_sha,
        }
        updated = core.apply_transition(updated, receipt)
        receipt_path = receipt_root / f"{target_state.lower()}.restricted.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            if core.load_strict_json(receipt_path) != receipt:
                raise ProductionStageError("TRANSITION_RECOVERY_RECEIPT_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                receipt_path,
                receipt,
                attempt_id=str(updated["attempt_id"]),
            )
    if output_ledger.exists() or output_ledger.is_symlink():
        if core.load_strict_json(output_ledger) != updated:
            raise ProductionStageError("TRANSITION_RECOVERY_LEDGER_MISMATCH")
    else:
        core.atomic_write_json_no_clobber(
            output_ledger, updated, attempt_id=str(updated["attempt_id"])
        )
    return updated


def validate_stage_predecessor(
    *, input_ledger: Path, batch_id: str, expected_state: str,
    expected_authority: Mapping[str, Any], expected_attempt_id: str,
    expected_object_keys: set[str], bound_manifest: Path,
    predecessor_transition_receipt: Path | None = None,
) -> dict[str, Any]:
    """Bind a stage input to the current authority and predecessor receipt."""
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    batch = ledger["batches"].get(batch_id)
    if not isinstance(batch, Mapping) or batch.get("state") != expected_state:
        raise ProductionStageError("PREDECESSOR_LEDGER_STATE_MISMATCH")
    manifest_sha = sha256_file(bound_manifest)
    if expected_state == "DOWNLOAD_VERIFIED":
        if batch.get("download_manifest_sha256") != manifest_sha:
            raise ProductionStageError("DOWNLOAD_MANIFEST_LEDGER_HASH_MISMATCH")
    else:
        events = batch.get("events")
        if (
            not isinstance(events, list)
            or not events
            or events[-1].get("to_state") != expected_state
        ):
            raise ProductionStageError("STAGE_MANIFEST_PREDECESSOR_HASH_MISMATCH")
        if predecessor_transition_receipt is None:
            raise ProductionStageError("PREDECESSOR_TRANSITION_RECEIPT_REQUIRED")
        transition = core.load_strict_json(predecessor_transition_receipt)
        if (
            core.canonical_json_sha256(transition) != events[-1].get("receipt_sha256")
            or transition.get("attempt_id") != expected_attempt_id
            or transition.get("batch_id") != batch_id
            or transition.get("to_state") != expected_state
            or transition.get("authority") != expected_authority
            or transition.get("output_manifest_sha256") != manifest_sha
        ):
            raise ProductionStageError("STAGE_MANIFEST_PREDECESSOR_HASH_MISMATCH")
    return ledger


def validate_download_manifest_plan_membership(
    path: Path, planned_batch: Mapping[str, Any]
) -> None:
    expected_header = [
        "subject_id", "study_id", "source_relative_path", "download_ok",
        "observed_sha256", "physical_source_key",
    ]
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_REGULAR")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_header or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ProductionStageError("DOWNLOAD_MANIFEST_SCHEMA_MISMATCH")
        rows = list(reader)
    expected = {
        (
            str(row["subject_id"]), str(row["study_id"]),
            str(row["source_relative_path"]), str(row["source_object_key"]),
        )
        for row in planned_batch["objects"]
    }
    observed = {
        (
            str(row["subject_id"]), str(row["study_id"]),
            str(row["source_relative_path"]), str(row["physical_source_key"]),
        )
        for row in rows
    }
    if len(rows) != planned_batch["n_objects"] or observed != expected:
        raise ProductionStageError("DOWNLOAD_MANIFEST_PLAN_MEMBERSHIP_MISMATCH")
    if any(row["download_ok"] != "true" or not SHA256_RE.fullmatch(row["observed_sha256"]) for row in rows):
        raise ProductionStageError("DOWNLOAD_MANIFEST_VERIFICATION_INVALID")


def validate_extraction_manifest_plan_membership(
    path: Path, planned_batch: Mapping[str, Any]
) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("EXTRACTION_MANIFEST_NOT_REGULAR")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ProductionStageError("EXTRACTION_MANIFEST_SCHEMA_MISMATCH")
        required = {"subject_id", "study_id", "physical_source_key", "clip_key", "npz_sha256"}
        if not required.issubset(reader.fieldnames):
            raise ProductionStageError("EXTRACTION_MANIFEST_SCHEMA_MISMATCH")
        rows = list(reader)
    expected_ownership = {
        str(row["source_object_key"]): (str(row["subject_id"]), str(row["study_id"]))
        for row in planned_batch["objects"]
    }
    seen: set[str] = set()
    for row in rows:
        key = str(row["physical_source_key"])
        if key in seen or expected_ownership.get(key) != (
            str(row["subject_id"]), str(row["study_id"])
        ):
            raise ProductionStageError("EXTRACTION_MANIFEST_PLAN_MEMBERSHIP_MISMATCH")
        seen.add(key)
        _validate_hash(row["clip_key"], "INVALID_CLIP_KEY")
        _validate_hash(row["npz_sha256"], "INVALID_EXTRACTION_HASH")


def record_stage_failure(
    *, input_ledger: Path, output_ledger: Path, receipt_root: Path,
    batch_id: str, expected_authority: Mapping[str, Any],
    expected_attempt_id: str, expected_object_keys: set[str],
    stage: str, error_code: str,
) -> None:
    """Persist a fail-closed stage failure without mutating prior evidence.

    DICOM/extraction and EchoPrime stage wrappers intentionally do not claim
    in-attempt retry support.  Their evidence paths and input ledgers are
    immutable, so every stage failure requires a new attempt with freshly
    bound authority.  Downloader retries remain governed separately by its
    per-object retry policy.
    """
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    batch = ledger["batches"][batch_id]
    predecessor = (
        batch["events"][-1]["receipt_sha256"]
        if batch["events"]
        else ledger["authority"]["batch_plan_sha256"]
    )
    receipt = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": expected_attempt_id,
        "batch_id": batch_id,
        "from_state": batch["state"],
        "to_state": "FAILED_NONRETRYABLE",
        "status": "PASS",
        "authority": ledger["authority"],
        "input_receipt_sha256": [predecessor],
        "output_manifest_sha256": hashlib.sha256(
            f"{stage}:{error_code}".encode("ascii", errors="replace")
        ).hexdigest(),
    }
    updated = core.apply_transition(ledger, receipt)
    if receipt_root.exists() or receipt_root.is_symlink():
        raise ProductionStageError("FAILURE_RECEIPT_ROOT_COLLISION")
    receipt_root.mkdir(mode=0o700)
    core.atomic_write_json_no_clobber(
        receipt_root / "stage_failure.restricted.json",
        receipt,
        attempt_id=expected_attempt_id,
    )
    core.atomic_write_json_no_clobber(
        output_ledger, updated, attempt_id=expected_attempt_id
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_authority_arguments(target: argparse.ArgumentParser, *, stage: str | None) -> None:
        if stage is None:
            target.add_argument("--stage", choices=sorted(WRAPPER_STAGES), required=True)
        else:
            target.set_defaults(stage=stage)
        target.add_argument("--batch-id", required=True)
        target.add_argument("--attempt-id", required=True)
        target.add_argument("--governing-commit", required=True)
        target.add_argument("--authority-worktree", type=Path, required=True)
        target.add_argument("--orchestration-contract", type=Path, required=True)
        target.add_argument("--batch-plan", type=Path, required=True)
        target.add_argument("--environment-receipt", type=Path, required=True)
        target.add_argument("--output-root", type=Path, required=True)

    validate = subparsers.add_parser("validate-wrapper")
    add_authority_arguments(validate, stage=None)
    validate_authorization = subparsers.add_parser("validate-stage-authorization")
    validate_authorization.add_argument(
        "--stage", choices=sorted(SCIENTIFIC_AUTHORIZATION_STAGES), required=True
    )
    validate_authorization.add_argument("--batch-id", required=True)
    validate_authorization.add_argument("--attempt-id", required=True)
    validate_authorization.add_argument("--governing-commit", required=True)
    validate_authorization.add_argument(
        "--orchestration-contract", type=Path, required=True
    )
    validate_authorization.add_argument("--batch-plan", type=Path, required=True)
    validate_authorization.add_argument(
        "--authorization-receipt", type=Path, required=True
    )
    validate_authorization.add_argument("--launch-authority-sha256", required=True)
    dicom = subparsers.add_parser("run-dicom-extraction")
    add_authority_arguments(dicom, stage="DICOM_EXTRACTION")
    dicom.add_argument("--authorization-receipt", type=Path, required=True)
    dicom.add_argument("--launch-authority-sha256", required=True)
    dicom.add_argument("--verified-download-manifest", type=Path, required=True)
    dicom.add_argument("--download-root", type=Path, required=True)
    dicom.add_argument("--workers", type=int, default=4)
    dicom.add_argument("--input-ledger", type=Path, required=True)
    dicom.add_argument("--output-ledger", type=Path, required=True)
    embed = subparsers.add_parser("run-echoprime")
    add_authority_arguments(embed, stage="ECHOPRIME_EMBEDDING")
    embed.add_argument("--authorization-receipt", type=Path, required=True)
    embed.add_argument("--launch-authority-sha256", required=True)
    embed.add_argument("--extraction-manifest", type=Path, required=True)
    embed.add_argument("--extraction-root", type=Path, required=True)
    embed.add_argument("--selected-batch-manifest", type=Path, required=True)
    embed.add_argument("--checkpoint", type=Path, required=True)
    embed.add_argument("--batch-size", type=int, default=8)
    embed.add_argument("--seed", type=int, default=20260803)
    embed.add_argument("--input-ledger", type=Path, required=True)
    embed.add_argument("--predecessor-transition-receipt", type=Path, required=True)
    embed.add_argument("--output-ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-stage-authorization":
        import lvef_c3_orchestration_core as core

        contract = core.load_orchestration_contract(args.orchestration_contract)
        plan = core.load_strict_json(args.batch_plan)
        plan_sha = core.validate_batch_plan(
            plan, requirements=core.production_requirements(contract)
        )
        validate_stage_authorization(
            args.authorization_receipt,
            stage=args.stage,
            batch_id=args.batch_id,
            attempt_id=args.attempt_id,
            governing_commit=args.governing_commit,
            orchestration_contract_sha256=sha256_file(args.orchestration_contract),
            batch_plan_sha256=plan_sha,
            launch_authority_sha256=args.launch_authority_sha256,
        )
        print(
            json.dumps(
                {
                    "status": "PASS_STAGE_SCIENTIFIC_AUTHORIZATION",
                    "stage": args.stage,
                    "owner_authorized": True,
                },
                sort_keys=True,
            )
        )
        return 0
    value = validate_wrapper_authority(
        stage=args.stage,
        batch_id=args.batch_id,
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        authority_worktree=args.authority_worktree,
        orchestration_contract=args.orchestration_contract,
        batch_plan=args.batch_plan,
        environment_receipt=args.environment_receipt,
        output_root=args.output_root,
    )
    if args.command == "validate-wrapper":
        print(
            json.dumps(
                {
                    "status": "PASS_OFFLINE_WRAPPER_AUTHORITY",
                    "stage": value["stage"],
                    "real_execution_performed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    validate_stage_authorization(
        args.authorization_receipt,
        stage=args.stage,
        batch_id=args.batch_id,
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        orchestration_contract_sha256=value["orchestration_contract_sha256"],
        batch_plan_sha256=value["batch_plan_sha256"],
        launch_authority_sha256=args.launch_authority_sha256,
    )
    if args.command == "run-dicom-extraction":
        validate_stage_predecessor(
            input_ledger=args.input_ledger,
            batch_id=args.batch_id,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
            bound_manifest=args.verified_download_manifest,
        )
        validate_download_manifest_plan_membership(
            args.verified_download_manifest, value["planned_batch"]
        )
        dicom_final = args.output_root / "dicom_extraction"
        if dicom_final.exists() or dicom_final.is_symlink():
            summary = validate_completed_stage_for_recovery(
                stage_directory=dicom_final,
                stage="DICOM_EXTRACTION",
                batch_id=args.batch_id,
                attempt_id=args.attempt_id,
                runtime_authority=value["runtime_authority"],
                input_manifest=args.verified_download_manifest,
                artifact_names=(
                    "dicom_audit.restricted.csv",
                    "extraction_manifest.restricted.csv",
                    "dicom_extraction.summary.json",
                ),
                summary_name="dicom_extraction.summary.json",
            )
        else:
            try:
                summary = run_production_dicom_extraction(
                    verified_download_manifest=args.verified_download_manifest,
                    download_root=args.download_root,
                    batch_output_root=args.output_root,
                    workers=args.workers,
                    batch_id=args.batch_id,
                    attempt_id=args.attempt_id,
                    runtime_authority=value["runtime_authority"],
                )
            except ProductionStageError as exc:
                record_stage_failure(
                    input_ledger=args.input_ledger,
                    output_ledger=args.output_ledger,
                    receipt_root=args.output_root / "dicom_extraction_failure_receipt",
                    batch_id=args.batch_id,
                    expected_authority=value["runtime_authority"],
                    expected_attempt_id=args.attempt_id,
                    expected_object_keys=value["expected_object_keys"],
                    stage="DICOM_EXTRACTION",
                    error_code=exc.code,
                )
                raise
        advance_stage_ledger(
            input_ledger=args.input_ledger,
            output_ledger=args.output_ledger,
            receipt_root=args.output_root / "dicom_extraction" / "transition_receipts",
            batch_id=args.batch_id,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", sha256_file(args.output_root / "dicom_extraction" / "dicom_audit.restricted.csv")),
                ("EXTRACTION_COMPLETE", sha256_file(args.output_root / "dicom_extraction" / "extraction_manifest.restricted.csv")),
            ),
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
        )
    elif args.command == "run-echoprime":
        if sha256_file(args.checkpoint) != value["runtime_authority"]["checkpoint_sha256"]:
            raise ProductionStageError("RUNTIME_CHECKPOINT_AUTHORITY_MISMATCH")
        if sha256_file(args.environment_receipt) != value["runtime_authority"]["environment_receipt_sha256"]:
            raise ProductionStageError("RUNTIME_ENVIRONMENT_AUTHORITY_MISMATCH")
        validate_stage_predecessor(
            input_ledger=args.input_ledger,
            batch_id=args.batch_id,
            expected_state="EXTRACTION_COMPLETE",
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
            bound_manifest=args.extraction_manifest,
            predecessor_transition_receipt=args.predecessor_transition_receipt,
        )
        validate_extraction_manifest_plan_membership(
            args.extraction_manifest, value["planned_batch"]
        )
        echoprime_final = args.output_root / "echoprime"
        if echoprime_final.exists() or echoprime_final.is_symlink():
            summary = validate_completed_stage_for_recovery(
                stage_directory=echoprime_final,
                stage="ECHOPRIME_EMBEDDING",
                batch_id=args.batch_id,
                attempt_id=args.attempt_id,
                runtime_authority=value["runtime_authority"],
                input_manifest=args.extraction_manifest,
                artifact_names=(
                    "clip_embeddings.restricted.npz",
                    "clip_manifest.restricted.csv",
                    "study_embeddings.restricted.npz",
                    "study_manifest.restricted.csv",
                    "study_disposition.restricted.csv",
                    "echoprime_pooling.summary.json",
                ),
                summary_name="echoprime_pooling.summary.json",
            )
        else:
            try:
                summary = run_production_echoprime(
                    extraction_manifest=args.extraction_manifest,
                    extraction_root=args.extraction_root,
                    selected_batch_manifest=args.selected_batch_manifest,
                    checkpoint=args.checkpoint,
                    environment_receipt=args.environment_receipt,
                    orchestration_contract=args.orchestration_contract,
                    batch_plan=args.batch_plan,
                    batch_id=args.batch_id,
                    batch_output_root=args.output_root,
                    batch_size=args.batch_size,
                    seed=args.seed,
                    attempt_id=args.attempt_id,
                    runtime_authority=value["runtime_authority"],
                )
            except ProductionStageError as exc:
                record_stage_failure(
                    input_ledger=args.input_ledger,
                    output_ledger=args.output_ledger,
                    receipt_root=args.output_root / "echoprime_failure_receipt",
                    batch_id=args.batch_id,
                    expected_authority=value["runtime_authority"],
                    expected_attempt_id=args.attempt_id,
                    expected_object_keys=value["expected_object_keys"],
                    stage="ECHOPRIME_EMBEDDING",
                    error_code=exc.code,
                )
                raise
        advance_stage_ledger(
            input_ledger=args.input_ledger,
            output_ledger=args.output_ledger,
            receipt_root=args.output_root / "echoprime" / "transition_receipts",
            batch_id=args.batch_id,
            transitions=(
                ("EMBEDDING_COMPLETE", sha256_file(args.output_root / "echoprime" / "clip_manifest.restricted.csv")),
                ("STUDY_POOLING_COMPLETE", sha256_file(args.output_root / "echoprime" / "study_manifest.restricted.csv")),
            ),
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
        )
    else:  # pragma: no cover
        raise ProductionStageError("UNKNOWN_COMMAND")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "stage": args.stage,
                "identifiers_emitted": False,
                "paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except SystemExit:
        raise
    except ProductionStageError as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": exc.code}, sort_keys=True))
        return 78
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_code": "UNEXPECTED_STAGE_EXCEPTION",
                    "error_type": type(exc).__name__,
                    "exception_message_emitted": False,
                    "identifiers_emitted": False,
                    "paths_emitted": False,
                },
                sort_keys=True,
            )
        )
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
