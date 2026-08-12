#!/usr/bin/env python3
"""Capture a no-clobber, restricted C3 software authority without GPU work."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence
import uuid


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_PYTHON_SHA256 = "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
CRC32C_PROBE_KEYS = frozenset(
    {
        "protocol_version",
        "status",
        "python_version",
        "google_crc32c_version",
        "google_crc32c_implementation",
        "google_crc32c_distribution_sha256",
        "google_crc32c_distribution_file_count",
        "known_vector_crc32c_base64",
        "cloud_requests",
    }
)
CAPTURE_STAGES = (
    "INTERPRETER_AUTHORITY",
    "TORCH_IMPORT",
    "TORCHVISION_IMPORT",
    "OPTIONAL_PACKAGE_IMPORTS",
    "CHECKOUT_AUTHORITY",
    "PRIOR_RECEIPT_LOAD",
    "PACKAGE_INVENTORY",
    "CRC32C_RUNTIME_PROBE",
    "CUDA_METADATA",
    "CUDNN_METADATA",
    "RECEIPT_BUILD",
    "RECEIPT_SCHEMA_VALIDATION",
    "RECEIPT_TEMP_WRITE",
    "RECEIPT_ATOMIC_PROMOTION",
)
STAGE_FAILURE_CODES = {
    stage: f"{stage}_FAILED" for stage in CAPTURE_STAGES
}
PACKAGE_INVENTORY_FAILURE_CODES = frozenset(
    {
        "PACKAGE_DISTRIBUTION_NAME_MISSING",
        "PACKAGE_INVENTORY_DUPLICATE_NAME",
        "PACKAGE_INVENTORY_EMPTY",
        "PACKAGE_INVENTORY_NOT_SORTED",
        "PACKAGE_INVENTORY_ROW_INVALID",
        "PACKAGE_INVENTORY_ROW_SCHEMA_INVALID",
        "PACKAGE_INVENTORY_SUBPROCESS_FAILED",
        "PACKAGE_INVENTORY_SUBPROCESS_INVALID",
        "PACKAGE_INVENTORY_TIMEOUT",
    }
)
HASH_INPUT_FAILURE_CODES = frozenset(
    {
        "HASH_INPUT_COMPONENT_MISSING",
        "HASH_INPUT_NOT_REGULAR",
        "HASH_INPUT_SYMLINK_COMPONENT",
    }
)
STAGE_ALLOWED_FAILURE_CODES = {
    "INTERPRETER_AUTHORITY": frozenset({"PYTHON_AUTHORITY_CHANGED"})
    | HASH_INPUT_FAILURE_CODES,
    "TORCH_IMPORT": frozenset(),
    "TORCHVISION_IMPORT": frozenset(),
    "OPTIONAL_PACKAGE_IMPORTS": frozenset(),
    "CHECKOUT_AUTHORITY": frozenset(
        {
            "CHECKOUT_AUTHORITY_MISMATCH",
            "CHECKOUT_COMPONENT_MISSING",
            "CHECKOUT_GIT_INSPECTION_FAILED",
            "CHECKOUT_NOT_REGULAR",
            "CHECKOUT_SYMLINK_COMPONENT",
        }
    ),
    "PRIOR_RECEIPT_LOAD": frozenset(
        {
            "DUPLICATE_JSON_KEY",
            "PRIOR_ENVIRONMENT_COMPONENT_MISSING",
            "PRIOR_ENVIRONMENT_INVALID_JSON",
            "PRIOR_ENVIRONMENT_NOT_MAPPING",
            "PRIOR_ENVIRONMENT_NOT_REGULAR",
            "PRIOR_ENVIRONMENT_SYMLINK_COMPONENT",
        }
    )
    | HASH_INPUT_FAILURE_CODES,
    "PACKAGE_INVENTORY": PACKAGE_INVENTORY_FAILURE_CODES,
    "CRC32C_RUNTIME_PROBE": frozenset(
        {
            "CRC32C_PYTHON_COMPONENT_MISSING",
            "CRC32C_PYTHON_EXPECTED_HASH_INVALID",
            "CRC32C_PYTHON_HASH_MISMATCH",
            "CRC32C_PYTHON_NOT_EXECUTABLE",
            "CRC32C_PYTHON_NOT_REGULAR",
            "CRC32C_PYTHON_SYMLINK_COMPONENT",
            "CRC32C_RUNTIME_PROBE_FAILED",
            "CRC32C_RUNTIME_PROBE_INVALID",
            "CRC32C_RUNTIME_PROBE_TIMEOUT",
            "CRC32C_WORKER_COMPONENT_MISSING",
            "CRC32C_WORKER_NOT_CHECKOUT_BOUND",
            "CRC32C_WORKER_NOT_REGULAR",
            "CRC32C_WORKER_SYMLINK_COMPONENT",
            "DUPLICATE_JSON_KEY",
        }
    )
    | HASH_INPUT_FAILURE_CODES,
    "CUDA_METADATA": frozenset({"CUDA_RUNTIME_UNAVAILABLE"}),
    "CUDNN_METADATA": frozenset({"CUDNN_RUNTIME_UNAVAILABLE"}),
    "RECEIPT_BUILD": frozenset(
        {
            "ENVIRONMENT_HASH_INVALID",
            "GOOGLE_CRC32C_AUXILIARY_AUTHORITY_INVALID",
            "GOVERNING_COMMIT_INVALID",
            "PRIOR_ENVIRONMENT_RUNTIME_MISMATCH",
            "PYTHON_AUTHORITY_CHANGED",
        }
    )
    | PACKAGE_INVENTORY_FAILURE_CODES,
    "RECEIPT_SCHEMA_VALIDATION": frozenset(
        {
            "ENVIRONMENT_RECEIPT_ACTIVITY_INVALID",
            "ENVIRONMENT_RECEIPT_SCHEMA_INVALID",
            "ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH",
        }
    )
    | PACKAGE_INVENTORY_FAILURE_CODES,
    "RECEIPT_TEMP_WRITE": frozenset(
        {
            "OUTPUT_ALREADY_EXISTS",
            "OUTPUT_COMPONENT_MISSING",
            "OUTPUT_MODE_INVALID",
            "OUTPUT_OUTSIDE_PROJECTNB",
            "OUTPUT_PARENT_COMPONENT_MISSING",
            "OUTPUT_PARENT_INVALID",
            "OUTPUT_PARENT_NOT_PRIVATE",
            "OUTPUT_PARENT_SYMLINK_COMPONENT",
            "OUTPUT_SYMLINK_COMPONENT",
            "OUTPUT_TEMP_COMPONENT_MISSING",
            "OUTPUT_TEMP_SYMLINK_COMPONENT",
        }
    ),
    "RECEIPT_ATOMIC_PROMOTION": frozenset(
        {
            "OUTPUT_ALREADY_EXISTS",
            "OUTPUT_COMPONENT_MISSING",
            "OUTPUT_MODE_INVALID",
            "OUTPUT_OUTSIDE_PROJECTNB",
            "OUTPUT_PARENT_COMPONENT_MISSING",
            "OUTPUT_PARENT_INVALID",
            "OUTPUT_PARENT_NOT_PRIVATE",
            "OUTPUT_PARENT_SYMLINK_COMPONENT",
            "OUTPUT_SYMLINK_COMPONENT",
            "OUTPUT_TEMP_COMPONENT_MISSING",
            "OUTPUT_TEMP_NOT_REGULAR",
            "OUTPUT_TEMP_NOT_SIBLING",
            "OUTPUT_TEMP_SYMLINK_COMPONENT",
        }
    ),
}
if set(STAGE_ALLOWED_FAILURE_CODES) != set(CAPTURE_STAGES):
    raise RuntimeError("CAPTURE_STAGE_FAILURE_CODE_REGISTRY_MISMATCH")
SAFE_EXCEPTION_CLASSES = frozenset(
    {
        "ImportError",
        "ModuleNotFoundError",
        "EnvironmentAuthorityError",
        "RuntimeError",
        "TimeoutExpired",
        "OSError",
        "FileNotFoundError",
        "ValueError",
        "TypeError",
        "FileExistsError",
        "PermissionError",
    }
)
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


class EnvironmentAuthorityError(RuntimeError):
    pass


class CaptureStageError(RuntimeError):
    """A fixed-code, path-free failure at one closed capture stage."""

    def __init__(self, stage: str, code: str, exception_class: str):
        if stage not in CAPTURE_STAGES or not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValueError("INVALID_CAPTURE_STAGE_ERROR")
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.exception_class = (
            exception_class
            if exception_class in SAFE_EXCEPTION_CLASSES
            else "OTHER_EXCEPTION"
        )


@dataclass(frozen=True)
class CaptureHooks:
    """Test seams around the production capture stages, never a parallel path."""

    import_module: Callable[[str], Any] = importlib.import_module
    find_optional_package: Callable[[str], Any] = importlib.util.find_spec
    interpreter_authority: Callable[[], tuple[Path, str]] | None = None
    validate_checkout: Callable[[Path, str], None] | None = None
    load_prior: Callable[[Path], Mapping[str, Any]] | None = None
    hash_file: Callable[[Path], str] | None = None
    inventory: Callable[[], list[dict[str, str]]] | None = None
    crc_probe: Callable[..., Mapping[str, Any]] | None = None
    build: Callable[..., Mapping[str, Any]] | None = None
    validate_receipt: Callable[[Mapping[str, Any]], None] | None = None
    write_temp: Callable[[Path, Mapping[str, Any]], Path] | None = None
    promote: Callable[[Path, Path], None] | None = None


def require_no_symlink_ancestors(
    path: Path, *, code: str, require_leaf: bool = True
) -> None:
    """Reject a symlink in any existing component of an absolute authority path."""
    if not path.is_absolute():
        raise EnvironmentAuthorityError(f"{code}_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    components = path.parts[1:]
    for index, component in enumerate(components):
        cursor /= component
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            if not require_leaf and index == len(components) - 1:
                return
            raise EnvironmentAuthorityError(f"{code}_COMPONENT_MISSING") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise EnvironmentAuthorityError(f"{code}_SYMLINK_COMPONENT")


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    result: MutableMapping[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentAuthorityError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def sha256_file(path: Path) -> str:
    require_no_symlink_ancestors(path, code="HASH_INPUT")
    if path.is_symlink() or not path.is_file():
        raise EnvironmentAuthorityError("HASH_INPUT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Mapping[str, Any]:
    require_no_symlink_ancestors(path, code="PRIOR_ENVIRONMENT")
    if path.is_symlink() or not path.is_file():
        raise EnvironmentAuthorityError("PRIOR_ENVIRONMENT_NOT_REGULAR")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except EnvironmentAuthorityError:
        raise
    except Exception as exc:
        raise EnvironmentAuthorityError("PRIOR_ENVIRONMENT_INVALID_JSON") from exc
    if not isinstance(value, Mapping):
        raise EnvironmentAuthorityError("PRIOR_ENVIRONMENT_NOT_MAPPING")
    return value


def package_inventory() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            raise EnvironmentAuthorityError("PACKAGE_DISTRIBUTION_NAME_MISSING")
        items.append({"name": str(name), "version": str(dist.version)})
    items.sort(key=lambda row: (row["name"].casefold(), row["version"]))
    return validate_package_inventory(items)


def package_inventory_subprocess(
    *,
    python_executable: Path | None = None,
    script_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: int = 60,
) -> list[dict[str, str]]:
    """Capture inventory in the same isolated interpreter used by production."""
    python_executable = python_executable or Path(sys.executable)
    script_path = script_path or Path(__file__).resolve(strict=True)
    completed = runner(
        [
            str(python_executable),
            "-I",
            str(script_path),
            "--package-inventory-worker",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        cwd="/",
        env={
            "LC_ALL": "C",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if completed.returncode != 0 or completed.stderr:
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_SUBPROCESS_FAILED")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=_pairs)
    except Exception as exc:
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_SUBPROCESS_INVALID") from exc
    if not isinstance(value, Mapping) or set(value) != {"packages"}:
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_SUBPROCESS_INVALID")
    packages = value.get("packages")
    if not isinstance(packages, list):
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_SUBPROCESS_INVALID")
    return validate_package_inventory(packages)


def validate_package_inventory(
    items: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in items:
        if set(row) != {"name", "version"}:
            raise EnvironmentAuthorityError("PACKAGE_INVENTORY_ROW_SCHEMA_INVALID")
        name = row.get("name")
        version = row.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise EnvironmentAuthorityError("PACKAGE_INVENTORY_ROW_INVALID")
        normalized_name = re.sub(r"[-_.]+", "-", name).casefold()
        if normalized_name in seen:
            raise EnvironmentAuthorityError("PACKAGE_INVENTORY_DUPLICATE_NAME")
        seen.add(normalized_name)
        normalized.append({"name": name, "version": version})
    if not normalized:
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_EMPTY")
    expected = sorted(
        normalized, key=lambda row: (row["name"].casefold(), row["version"])
    )
    if normalized != expected:
        raise EnvironmentAuthorityError("PACKAGE_INVENTORY_NOT_SORTED")
    return normalized


def package_inventory_sha256(items: Sequence[Mapping[str, str]]) -> str:
    items = validate_package_inventory(items)
    return hashlib.sha256(
        json.dumps(list(items), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_receipt(
    *,
    prior: Mapping[str, Any],
    prior_sha256: str,
    governing_commit: str,
    python_executable_sha256: str,
    python_version: str,
    torch_version: str,
    torchvision_version: str,
    cuda_version: str,
    cudnn_version: str,
    crc32c_python_executable_sha256: str,
    crc32c_worker_sha256: str,
    crc32c_probe: Mapping[str, Any],
    packages: Sequence[Mapping[str, str]],
    captured_at_utc: str,
) -> Mapping[str, Any]:
    if not COMMIT_RE.fullmatch(governing_commit):
        raise EnvironmentAuthorityError("GOVERNING_COMMIT_INVALID")
    if any(
        SHA_RE.fullmatch(value) is None
        for value in (
            prior_sha256,
            python_executable_sha256,
            crc32c_python_executable_sha256,
            crc32c_worker_sha256,
        )
    ):
        raise EnvironmentAuthorityError("ENVIRONMENT_HASH_INVALID")
    if python_executable_sha256 != EXPECTED_PYTHON_SHA256:
        raise EnvironmentAuthorityError("PYTHON_AUTHORITY_CHANGED")
    for key, observed in (
        ("python_version", python_version),
        ("torch_version", torch_version),
        ("torchvision_version", torchvision_version),
        ("cuda_version", cuda_version),
        ("cudnn_version", cudnn_version),
    ):
        if str(prior.get(key)) != str(observed):
            raise EnvironmentAuthorityError("PRIOR_ENVIRONMENT_RUNTIME_MISMATCH")
    if (
        set(crc32c_probe) != CRC32C_PROBE_KEYS
        or crc32c_probe.get("protocol_version") != 1
        or crc32c_probe.get("status") != "PASS_CRC32C_AUXILIARY_RUNTIME"
        or crc32c_probe.get("google_crc32c_implementation") != "c"
        or not isinstance(crc32c_probe.get("google_crc32c_version"), str)
        or not crc32c_probe["google_crc32c_version"]
        or not isinstance(crc32c_probe.get("python_version"), str)
        or not crc32c_probe["python_version"]
        or crc32c_probe.get("known_vector_crc32c_base64") != "4waSgw=="
        or crc32c_probe.get("cloud_requests") != 0
        or SHA_RE.fullmatch(
            str(crc32c_probe.get("google_crc32c_distribution_sha256"))
        )
        is None
        or not isinstance(
            crc32c_probe.get("google_crc32c_distribution_file_count"), int
        )
        or isinstance(
            crc32c_probe.get("google_crc32c_distribution_file_count"), bool
        )
        or crc32c_probe["google_crc32c_distribution_file_count"] < 1
    ):
        raise EnvironmentAuthorityError("GOOGLE_CRC32C_AUXILIARY_AUTHORITY_INVALID")
    inventory = validate_package_inventory(packages)
    return {
        "schema_version": 3,
        "artifact_type": "lvef_c3_production_environment_authority_v3",
        "status": "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION",
        "governing_commit": governing_commit,
        "captured_at_utc": captured_at_utc,
        "source_environment_receipt_sha256": prior_sha256,
        "python_executable_sha256": python_executable_sha256,
        "python_version": python_version,
        "torch_version": torch_version,
        "torchvision_version": torchvision_version,
        "cuda_version": cuda_version,
        "cudnn_version": cudnn_version,
        "crc32c_runtime_source": "PINNED_CLOUDSDK_BUNDLED_PYTHON",
        "crc32c_python_executable_sha256": crc32c_python_executable_sha256,
        "crc32c_python_version": crc32c_probe["python_version"],
        "crc32c_worker_sha256": crc32c_worker_sha256,
        "crc32c_worker_protocol_version": crc32c_probe["protocol_version"],
        "google_crc32c_version": crc32c_probe["google_crc32c_version"],
        "google_crc32c_implementation": crc32c_probe[
            "google_crc32c_implementation"
        ],
        "google_crc32c_distribution_sha256": crc32c_probe[
            "google_crc32c_distribution_sha256"
        ],
        "google_crc32c_distribution_file_count": crc32c_probe[
            "google_crc32c_distribution_file_count"
        ],
        "google_crc32c_known_vector_base64": crc32c_probe[
            "known_vector_crc32c_base64"
        ],
        "package_inventory_sha256": package_inventory_sha256(inventory),
        "package_count": len(inventory),
        "package_inventory": inventory,
        "operating_system": platform.platform(),
        "gpu_execution_performed": False,
        "cloud_request_performed": False,
        "dicom_body_read": False,
        "model_fitted": False,
        "prediction_generated": False,
        "confirmatory_performance_accessed": False,
    }


def probe_crc32c_runtime(
    python_executable: Path,
    worker_script: Path,
    *,
    expected_python_sha256: str,
) -> Mapping[str, Any]:
    for path, code in (
        (python_executable, "CRC32C_PYTHON"),
        (worker_script, "CRC32C_WORKER"),
    ):
        require_no_symlink_ancestors(path, code=code)
        if path.is_symlink() or not path.is_file():
            raise EnvironmentAuthorityError(f"{code}_NOT_REGULAR")
    if not os.access(python_executable, os.X_OK):
        raise EnvironmentAuthorityError("CRC32C_PYTHON_NOT_EXECUTABLE")
    if not SHA_RE.fullmatch(expected_python_sha256):
        raise EnvironmentAuthorityError("CRC32C_PYTHON_EXPECTED_HASH_INVALID")
    if sha256_file(python_executable) != expected_python_sha256:
        raise EnvironmentAuthorityError("CRC32C_PYTHON_HASH_MISMATCH")
    completed = subprocess.run(
        [str(python_executable), "-I", str(worker_script), "--probe"],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        cwd="/",
        env={
            "LC_ALL": "C",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if completed.returncode != 0 or completed.stderr:
        raise EnvironmentAuthorityError("CRC32C_RUNTIME_PROBE_FAILED")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=_pairs)
    except Exception as exc:
        raise EnvironmentAuthorityError("CRC32C_RUNTIME_PROBE_INVALID") from exc
    if not isinstance(value, Mapping) or set(value) != CRC32C_PROBE_KEYS:
        raise EnvironmentAuthorityError("CRC32C_RUNTIME_PROBE_INVALID")
    return value


def _validate_output_destination(path: Path) -> None:
    if not path.is_absolute() or not str(path).startswith("/restricted/projectnb/"):
        raise EnvironmentAuthorityError("OUTPUT_OUTSIDE_PROJECTNB")
    require_no_symlink_ancestors(path.parent, code="OUTPUT_PARENT")
    if not path.parent.is_dir() or path.parent.stat().st_uid != os.getuid():
        raise EnvironmentAuthorityError("OUTPUT_PARENT_INVALID")
    if stat.S_IMODE(path.parent.stat(follow_symlinks=False).st_mode) not in {
        0o700,
        0o2700,
    }:
        raise EnvironmentAuthorityError("OUTPUT_PARENT_NOT_PRIVATE")
    if path.exists() or path.is_symlink():
        raise EnvironmentAuthorityError("OUTPUT_ALREADY_EXISTS")
    require_no_symlink_ancestors(path, code="OUTPUT", require_leaf=False)


def write_receipt_temp(path: Path, value: Mapping[str, Any]) -> Path:
    """Write and fsync an owner-private sibling temporary receipt."""
    _validate_output_destination(path)
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    require_no_symlink_ancestors(temporary, code="OUTPUT_TEMP", require_leaf=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if stat.S_IMODE(temporary.stat(follow_symlinks=False).st_mode) != 0o600:
            raise EnvironmentAuthorityError("OUTPUT_MODE_INVALID")
        return temporary
    except Exception:
        try:
            if created and temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def promote_receipt_atomic(temporary: Path, path: Path) -> None:
    """Atomically promote without replacing a raced or pre-existing receipt."""
    _validate_output_destination(path)
    if temporary.parent != path.parent:
        raise EnvironmentAuthorityError("OUTPUT_TEMP_NOT_SIBLING")
    require_no_symlink_ancestors(temporary, code="OUTPUT_TEMP")
    if temporary.is_symlink() or not temporary.is_file():
        raise EnvironmentAuthorityError("OUTPUT_TEMP_NOT_REGULAR")
    temporary_metadata = temporary.stat(follow_symlinks=False)
    linked = False
    directory_descriptor = -1
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise EnvironmentAuthorityError("OUTPUT_ALREADY_EXISTS") from exc
        linked = True
        if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
            raise EnvironmentAuthorityError("OUTPUT_MODE_INVALID")
        directory_descriptor = os.open(path.parent, directory_flags)
        os.fsync(directory_descriptor)
        temporary.unlink()
        os.fsync(directory_descriptor)
    except Exception:
        if linked:
            try:
                final_metadata = path.stat(follow_symlinks=False)
                if (
                    stat.S_ISREG(final_metadata.st_mode)
                    and final_metadata.st_dev == temporary_metadata.st_dev
                    and final_metadata.st_ino == temporary_metadata.st_ino
                ):
                    path.unlink()
                    if directory_descriptor >= 0:
                        os.fsync(directory_descriptor)
            except OSError:
                pass
        raise
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def write_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
    """Compatibility wrapper around staged temporary write and promotion."""
    temporary = write_receipt_temp(path, value)
    try:
        promote_receipt_atomic(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def validate_checkout_authority(checkout: Path, governing_commit: str) -> None:
    require_no_symlink_ancestors(checkout, code="CHECKOUT")
    if checkout.is_symlink() or not checkout.is_dir():
        raise EnvironmentAuthorityError("CHECKOUT_NOT_REGULAR")
    root = checkout.resolve(strict=True)

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise EnvironmentAuthorityError("CHECKOUT_GIT_INSPECTION_FAILED")
        return completed.stdout.strip()

    if (
        git("rev-parse", "--show-toplevel") != str(root)
        or git("branch", "--show-current")
        != "codex/lvef-multitask-revalidation"
        or git("rev-parse", "HEAD") != governing_commit
        or git("status", "--porcelain", "--untracked-files=no")
    ):
        raise EnvironmentAuthorityError("CHECKOUT_AUTHORITY_MISMATCH")


def validate_receipt_schema(value: Mapping[str, Any]) -> None:
    """Validate the exact v3 producer schema before any filesystem write."""
    if set(value) != ENVIRONMENT_RECEIPT_KEYS:
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH")
    if (
        value.get("schema_version") != 3
        or value.get("artifact_type")
        != "lvef_c3_production_environment_authority_v3"
        or value.get("status")
        != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit")))
        or not isinstance(value.get("package_inventory"), list)
    ):
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    for key in (
        "source_environment_receipt_sha256",
        "python_executable_sha256",
        "crc32c_python_executable_sha256",
        "crc32c_worker_sha256",
        "google_crc32c_distribution_sha256",
        "package_inventory_sha256",
    ):
        if not SHA_RE.fullmatch(str(value.get(key))):
            raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    if value.get("python_executable_sha256") != EXPECTED_PYTHON_SHA256:
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    for key in (
        "python_version",
        "torch_version",
        "torchvision_version",
        "cuda_version",
        "cudnn_version",
        "crc32c_python_version",
        "google_crc32c_version",
        "operating_system",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    if (
        value.get("crc32c_runtime_source")
        != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or value.get("crc32c_worker_protocol_version") != 1
        or value.get("google_crc32c_implementation") != "c"
        or value.get("google_crc32c_known_vector_base64") != "4waSgw=="
        or not isinstance(value.get("google_crc32c_distribution_file_count"), int)
        or isinstance(value.get("google_crc32c_distribution_file_count"), bool)
        or value["google_crc32c_distribution_file_count"] < 1
    ):
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    try:
        captured = datetime.fromisoformat(str(value.get("captured_at_utc")))
    except ValueError as exc:
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID") from exc
    if captured.tzinfo is None or captured.utcoffset() != timezone.utc.utcoffset(captured):
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")
    for flag in (
        "gpu_execution_performed",
        "cloud_request_performed",
        "dicom_body_read",
        "model_fitted",
        "prediction_generated",
        "confirmatory_performance_accessed",
    ):
        if value.get(flag) is not False:
            raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_ACTIVITY_INVALID")
    packages = validate_package_inventory(value["package_inventory"])
    if (
        isinstance(value.get("package_count"), bool)
        or value.get("package_count") != len(packages)
        or value.get("package_inventory_sha256") != package_inventory_sha256(packages)
    ):
        raise EnvironmentAuthorityError("ENVIRONMENT_RECEIPT_SCHEMA_INVALID")


def _stage_code(stage: str, exc: BaseException) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        if stage == "PACKAGE_INVENTORY":
            return "PACKAGE_INVENTORY_TIMEOUT"
        if stage == "CRC32C_RUNTIME_PROBE":
            return "CRC32C_RUNTIME_PROBE_TIMEOUT"
    if isinstance(exc, EnvironmentAuthorityError):
        candidate = str(exc)
        if candidate in STAGE_ALLOWED_FAILURE_CODES[stage]:
            return candidate
    return STAGE_FAILURE_CODES[stage]


def _run_stage(stage: str, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except CaptureStageError:
        raise
    except Exception as exc:
        raise CaptureStageError(
            stage, _stage_code(stage, exc), type(exc).__name__
        ) from None


def capture_environment(
    args: argparse.Namespace, *, hooks: CaptureHooks | None = None
) -> Mapping[str, Any]:
    """Run the exact production capture through closed, observable stages."""
    hooks = hooks or CaptureHooks()
    validate_checkout = hooks.validate_checkout or validate_checkout_authority
    load_prior = hooks.load_prior or load_json
    hash_file = hooks.hash_file or sha256_file
    inventory = hooks.inventory or package_inventory_subprocess
    crc_probe = hooks.crc_probe or probe_crc32c_runtime
    build = hooks.build or build_receipt
    receipt_validator = hooks.validate_receipt or validate_receipt_schema
    temp_writer = hooks.write_temp or write_receipt_temp
    promoter = hooks.promote or promote_receipt_atomic

    def interpreter_authority() -> tuple[Path, str]:
        executable = Path(sys.executable).resolve(strict=True)
        digest = sha256_file(executable)
        if digest != EXPECTED_PYTHON_SHA256:
            raise EnvironmentAuthorityError("PYTHON_AUTHORITY_CHANGED")
        return executable, digest

    executable, python_sha256 = _run_stage(
        "INTERPRETER_AUTHORITY",
        hooks.interpreter_authority or interpreter_authority,
    )
    torch = _run_stage("TORCH_IMPORT", lambda: hooks.import_module("torch"))
    torchvision = _run_stage(
        "TORCHVISION_IMPORT", lambda: hooks.import_module("torchvision")
    )
    # google-crc32c is intentionally optional in the primary EchoPrime runtime;
    # its independently pinned compiled auxiliary runtime remains mandatory.
    _run_stage(
        "OPTIONAL_PACKAGE_IMPORTS",
        lambda: hooks.find_optional_package("google_crc32c"),
    )
    _run_stage(
        "CHECKOUT_AUTHORITY",
        lambda: validate_checkout(args.checkout_root, args.governing_commit),
    )

    def prior_authority() -> tuple[Mapping[str, Any], str]:
        return load_prior(args.prior_environment), hash_file(args.prior_environment)

    prior, prior_sha256 = _run_stage("PRIOR_RECEIPT_LOAD", prior_authority)
    packages = _run_stage("PACKAGE_INVENTORY", inventory)

    def crc32c_authority() -> tuple[Mapping[str, Any], str]:
        expected_worker = args.checkout_root / "scripts/lvef_c3_crc32c_worker.py"
        if args.crc32c_worker.resolve(strict=True) != expected_worker.resolve(
            strict=True
        ):
            raise EnvironmentAuthorityError("CRC32C_WORKER_NOT_CHECKOUT_BOUND")
        probe = crc_probe(
            args.crc32c_python,
            args.crc32c_worker,
            expected_python_sha256=args.crc32c_python_sha256,
        )
        return probe, hash_file(args.crc32c_worker)

    crc32c_probe, crc32c_worker_sha256 = _run_stage(
        "CRC32C_RUNTIME_PROBE", crc32c_authority
    )

    def cuda_metadata() -> str:
        value = torch.version.cuda
        if value is None:
            raise EnvironmentAuthorityError("CUDA_RUNTIME_UNAVAILABLE")
        return str(value)

    cuda_version = _run_stage("CUDA_METADATA", cuda_metadata)

    def cudnn_metadata() -> str:
        value = torch.backends.cudnn.version()
        if value is None:
            raise EnvironmentAuthorityError("CUDNN_RUNTIME_UNAVAILABLE")
        return str(value)

    cudnn_version = _run_stage("CUDNN_METADATA", cudnn_metadata)
    value = _run_stage(
        "RECEIPT_BUILD",
        lambda: build(
            prior=prior,
            prior_sha256=prior_sha256,
            governing_commit=args.governing_commit,
            python_executable_sha256=python_sha256,
            python_version=platform.python_version(),
            torch_version=str(torch.__version__),
            torchvision_version=str(torchvision.__version__),
            cuda_version=cuda_version,
            cudnn_version=cudnn_version,
            crc32c_python_executable_sha256=args.crc32c_python_sha256,
            crc32c_worker_sha256=crc32c_worker_sha256,
            crc32c_probe=crc32c_probe,
            packages=packages,
            captured_at_utc=datetime.now(timezone.utc).isoformat(),
        ),
    )
    _run_stage("RECEIPT_SCHEMA_VALIDATION", lambda: receipt_validator(value))
    temporary = _run_stage(
        "RECEIPT_TEMP_WRITE", lambda: temp_writer(args.output, value)
    )

    def atomic_promotion() -> None:
        try:
            promoter(temporary, args.output)
        finally:
            # Cleanup remains inside the same observable stage so even an
            # unexpected unlink failure is converted to the closed,
            # path-free atomic-promotion diagnostic.
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()

    _run_stage("RECEIPT_ATOMIC_PROMOTION", atomic_promotion)
    return value


def failure_payload(exc: CaptureStageError) -> Mapping[str, str]:
    return {
        "status": "FAIL",
        "failure_stage": exc.stage,
        "stable_error_code": exc.code,
        "exception_class": exc.exception_class,
    }


def package_inventory_worker_main() -> int:
    try:
        packages = package_inventory()
    except Exception:
        return 2
    print(json.dumps({"packages": packages}, sort_keys=True, separators=(",", ":")))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-environment", type=Path, required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--crc32c-python", type=Path, required=True)
    parser.add_argument(
        "--crc32c-python-expected-sha256",
        dest="crc32c_python_sha256",
        required=True,
    )
    parser.add_argument("--crc32c-worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if list(argv if argv is not None else sys.argv[1:]) == [
        "--package-inventory-worker"
    ]:
        return package_inventory_worker_main()
    args = parse_args(argv)
    try:
        capture_environment(args)
    except CaptureStageError as exc:
        print(json.dumps(failure_payload(exc), sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION",
                "google_crc32c_c_backend": True,
                "cloud_requests": 0,
                "gpu_execution": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
