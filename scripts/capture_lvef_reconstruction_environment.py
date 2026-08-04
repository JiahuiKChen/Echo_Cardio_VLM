#!/usr/bin/env python3
"""Capture the restricted embedding-smoke software and accelerator environment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Sequence


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHECKPOINT_FILENAME = "echo_prime_encoder.pt"
CHECKPOINT_BYTES = 138_642_379
CHECKPOINT_SHA256 = "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
SCRIPT_NAMES = {
    "audit_lvef_reconstruction_smoke_run.py",
    "build_lvef_reconstruction_source_manifest.py",
    "capture_lvef_reconstruction_environment.py",
    "download_lvef_reconstruction_smoke.py",
    "lvef_reconstruction_smoke.py",
    "preserve_lvef_reconstruction_smoke.py",
}


class EnvironmentCaptureError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_output_outside_repository(path: Path) -> None:
    if not path.is_absolute():
        raise EnvironmentCaptureError("Environment output must be absolute")
    repository = Path(__file__).resolve().parents[1]
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(repository)
    except ValueError:
        return
    raise EnvironmentCaptureError("Environment output must remain outside Git")


def parse_nvidia_smi_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 4 or not parts[0].isdigit() or not parts[1] or not parts[2]:
            raise EnvironmentCaptureError("Unexpected nvidia-smi schema")
        rows.append(
            {
                "index": parts[0],
                "uuid": parts[1],
                "name": parts[2],
                "driver_version": parts[3],
            }
        )
    if not rows:
        raise EnvironmentCaptureError("nvidia-smi returned no GPUs")
    return rows


def select_allocated_gpu(rows: list[dict[str, str]], visible: str) -> dict[str, str]:
    tokens = [token.strip() for token in visible.split(",") if token.strip()]
    if len(tokens) != 1:
        raise EnvironmentCaptureError("Exactly one CUDA_VISIBLE_DEVICES token is required")
    token = tokens[0]
    matches = [
        row
        for row in rows
        if row["index"] == token
        or row["uuid"] == token
        or row["uuid"].startswith(token)
    ]
    if len(matches) != 1:
        raise EnvironmentCaptureError("Allocated GPU cannot be uniquely reconciled")
    return matches[0]


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise EnvironmentCaptureError(f"{label} is not a regular file")
    return path.resolve()


def _git_text(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EnvironmentCaptureError("Git authority inspection failed")
    return completed.stdout.strip()


def installed_package_inventory() -> list[dict[str, str]]:
    """Return the complete installed-distribution inventory in stable order."""
    inventory: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            raise EnvironmentCaptureError("Installed distribution has no name")
        inventory.append({"name": str(name), "version": str(distribution.version)})
    inventory.sort(key=lambda item: (item["name"].casefold(), item["version"]))
    if not inventory:
        raise EnvironmentCaptureError("Installed package inventory is empty")
    return inventory


def pydicom_pixel_handler_inventory(pydicom_module: Any) -> list[dict[str, Any]]:
    handlers: list[dict[str, Any]] = []
    for handler in getattr(pydicom_module.config, "pixel_data_handlers", []):
        available = False
        try:
            available = bool(handler.is_available())
        except Exception:
            available = False
        handlers.append(
            {
                "module": str(getattr(handler, "__name__", type(handler).__name__)),
                "available": available,
            }
        )
    return sorted(handlers, key=lambda item: item["module"])


def capture(args: argparse.Namespace) -> dict[str, Any]:
    require_output_outside_repository(args.output)
    require_output_outside_repository(args.smoke_source_manifest)
    if args.output.exists() or args.output.is_symlink():
        raise EnvironmentCaptureError("Environment output already exists")
    if not COMMIT_RE.fullmatch(args.source_commit):
        raise EnvironmentCaptureError("Source commit is invalid")
    config = _regular_file(args.config, "Config")
    checkpoint = _regular_file(args.checkpoint, "Checkpoint")
    smoke_source_manifest = _regular_file(
        args.smoke_source_manifest, "Technical smoke source manifest"
    )
    if (
        checkpoint.name != CHECKPOINT_FILENAME
        or checkpoint.stat().st_size != CHECKPOINT_BYTES
        or sha256_file(checkpoint) != CHECKPOINT_SHA256
    ):
        raise EnvironmentCaptureError("Checkpoint identity is not locked")
    expected_smoke_sha256 = str(args.expected_smoke_source_manifest_sha256).lower()
    if not SHA256_RE.fullmatch(expected_smoke_sha256):
        raise EnvironmentCaptureError("Expected smoke-manifest SHA-256 is invalid")
    observed_smoke_sha256 = sha256_file(smoke_source_manifest)
    if observed_smoke_sha256 != expected_smoke_sha256:
        raise EnvironmentCaptureError("Technical smoke source manifest identity changed")

    import cv2
    import numpy
    import pandas
    import pydicom
    import sklearn
    import torch
    import torchvision

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise EnvironmentCaptureError("Exactly one visible CUDA accelerator is required")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EnvironmentCaptureError("nvidia-smi query failed")
    allocated = select_allocated_gpu(parse_nvidia_smi_rows(completed.stdout), visible)
    properties = torch.cuda.get_device_properties(0)
    if str(properties.name).strip() != allocated["name"]:
        raise EnvironmentCaptureError("PyTorch and scheduler GPU names disagree")

    repository = Path(__file__).resolve().parents[1]
    repository_head = _git_text(repository, "rev-parse", "HEAD")
    repository_branch = _git_text(repository, "branch", "--show-current")
    repository_status = _git_text(
        repository, "status", "--porcelain", "--untracked-files=normal"
    )
    if repository_head != args.source_commit:
        raise EnvironmentCaptureError("Source commit does not match repository HEAD")
    if repository_branch != "codex/lvef-multitask-revalidation":
        raise EnvironmentCaptureError("Unexpected reconstruction branch")
    if repository_status:
        raise EnvironmentCaptureError("Repository is not clean")
    python_executable = Path(sys.executable).resolve()
    cudnn_version = torch.backends.cudnn.version()
    if torch.version.cuda is None or cudnn_version is None:
        raise EnvironmentCaptureError("CUDA/cuDNN version is unavailable")
    payload = {
        "schema_version": 1,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "python_executable": str(python_executable),
        "python_executable_sha256": sha256_file(python_executable),
        "numpy_version": str(numpy.__version__),
        "pandas_version": str(pandas.__version__),
        "pydicom_version": str(pydicom.__version__),
        "pydicom_pixel_data_handlers": pydicom_pixel_handler_inventory(pydicom),
        "opencv_version": str(cv2.__version__),
        "torch_version": str(torch.__version__),
        "torchvision_version": str(torchvision.__version__),
        "scikit_learn_version": str(sklearn.__version__),
        "cuda_version": str(torch.version.cuda),
        "cudnn_version": str(cudnn_version),
        "gpu_name": allocated["name"],
        "gpu_uuid": allocated["uuid"],
        "gpu_driver_version": allocated["driver_version"],
        "gpu_compute_capability": f"{properties.major}.{properties.minor}",
        "gpu_total_memory_bytes": int(properties.total_memory),
        "cuda_visible_devices": visible,
        "operating_system": platform.platform(),
        "package_inventory": installed_package_inventory(),
        "source_commit": args.source_commit,
        "repository_head": repository_head,
        "repository_branch": repository_branch,
        "repository_clean": True,
        "config_sha256": sha256_file(config),
        "checkpoint_sha256": sha256_file(checkpoint),
        "technical_smoke_source_manifest_sha256": observed_smoke_sha256,
        "script_sha256": {
            name: sha256_file(repository / "scripts" / name)
            for name in sorted(SCRIPT_NAMES)
        },
        "scheduler": "SGE",
        "scheduler_job_id": str(os.environ.get("JOB_ID", "")),
        "scheduler_job_name": str(os.environ.get("JOB_NAME", "")),
        "scheduler_queue": str(os.environ.get("QUEUE", "")),
        "cohort_version": "phase1e-a-smoke4-v1",
        "split": "train",
        "model_version": "echoprime-encoder-512-pinned-checkpoint",
        "models_fitted": False,
        "predictions_generated": False,
        "confirmatory_performance_accessed": False,
    }
    if not payload["scheduler_job_id"].isdigit():
        raise EnvironmentCaptureError("Numeric SGE JOB_ID is required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(args.output, 0o600)
    return {
        "status": "PASS",
        "single_cuda_accelerator": True,
        "checkpoint_identity_locked": True,
        "script_identity_set_exact": set(payload["script_sha256"]) == SCRIPT_NAMES,
        "complete_package_inventory_recorded": True,
        "repository_authority_verified": True,
        "restricted_environment_written": True,
        "paths_emitted": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--smoke-source-manifest", type=Path, required=True)
    parser.add_argument("--expected-smoke-source-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        aggregate = capture(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_ENVIRONMENT_CAPTURE",
                    "error_type": type(exc).__name__,
                    "exception_message_emitted": False,
                    "paths_emitted": False,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(aggregate, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
