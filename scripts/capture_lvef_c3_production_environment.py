#!/usr/bin/env python3
"""Capture a no-clobber, restricted C3 software authority without GPU work."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_PYTHON_SHA256 = "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"


class EnvironmentAuthorityError(RuntimeError):
    pass


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
    crc32c_version: str,
    crc32c_implementation: str,
    packages: Sequence[Mapping[str, str]],
    captured_at_utc: str,
) -> Mapping[str, Any]:
    if not COMMIT_RE.fullmatch(governing_commit):
        raise EnvironmentAuthorityError("GOVERNING_COMMIT_INVALID")
    if not SHA_RE.fullmatch(prior_sha256) or not SHA_RE.fullmatch(
        python_executable_sha256
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
    if crc32c_implementation != "c" or not crc32c_version:
        raise EnvironmentAuthorityError("GOOGLE_CRC32C_C_BACKEND_REQUIRED")
    inventory = validate_package_inventory(packages)
    return {
        "schema_version": 2,
        "artifact_type": "lvef_c3_production_environment_authority_v2",
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
        "google_crc32c_version": crc32c_version,
        "google_crc32c_implementation": crc32c_implementation,
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


def write_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
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
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
        raise EnvironmentAuthorityError("OUTPUT_MODE_INVALID")


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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-environment", type=Path, required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        import google_crc32c
        import torch
        import torchvision

        validate_checkout_authority(args.checkout_root, args.governing_commit)
        executable = Path(sys.executable).resolve(strict=True)
        prior = load_json(args.prior_environment)
        packages = package_inventory()
        cudnn = torch.backends.cudnn.version()
        if torch.version.cuda is None or cudnn is None:
            raise EnvironmentAuthorityError("CUDA_CUDNN_RUNTIME_UNAVAILABLE")
        value = build_receipt(
            prior=prior,
            prior_sha256=sha256_file(args.prior_environment),
            governing_commit=args.governing_commit,
            python_executable_sha256=sha256_file(executable),
            python_version=platform.python_version(),
            torch_version=str(torch.__version__),
            torchvision_version=str(torchvision.__version__),
            cuda_version=str(torch.version.cuda),
            cudnn_version=str(cudnn),
            crc32c_version=importlib.metadata.version("google-crc32c"),
            crc32c_implementation=str(getattr(google_crc32c, "implementation", "")),
            packages=packages,
            captured_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        write_no_clobber(args.output, value)
    except (EnvironmentAuthorityError, OSError, ValueError, TypeError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "ENVIRONMENT_AUTHORITY_CAPTURE_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    except Exception:
        print(
            json.dumps(
                {"status": "FAIL", "error_code": "ENVIRONMENT_RUNTIME_IMPORT_FAILED"},
                sort_keys=True,
            )
        )
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
