"""Shared hashing, path, and export-safety utilities."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


BLOCKED_LINEAGE = "BLOCKED_MIXED_OR_UNRESOLVED_LINEAGE"
BLOCKED_UNSAFE_OUTPUT = "BLOCKED_UNSAFE_OUTPUT_PATH"

FORBIDDEN_SAFE_COLUMNS = {
    "subject_id",
    "subject_id_str",
    "study_id",
    "study_id_str",
    "dicom_id",
    "dicom_filepath",
    "dicom_abs_path",
    "sop_instance_uid",
    "clip_id",
    "filename",
    "file_path",
    "filepath",
    "absolute_path",
    "local_path",
    "restricted_path",
    "timestamp",
    "acquisition_datetime",
    "measurement_datetime",
    "target_value",
    "candidate_target_value",
    "confirmed_target_value",
    "y_true",
    "y_pred",
    "prediction",
    "residual",
}

SAFE_ROLE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
ABSOLUTE_PATH_TOKEN_PATTERN = re.compile(r"(?:^|[\s=:,;])(?:/|~/)\S+")


class Tier1BlockedError(RuntimeError):
    """A fail-closed condition with a machine-readable status."""

    def __init__(self, status: str, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def git_root(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def is_within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def require_restricted_destination(path: Path, worktree: Path | None = None) -> Path:
    """Require an absolute destination outside the active Git worktree."""

    destination = path.expanduser()
    if not destination.is_absolute():
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "restricted destination must be absolute")
    root = worktree.resolve() if worktree is not None else git_root()
    if root is not None and is_within(destination, root):
        raise Tier1BlockedError(
            BLOCKED_UNSAFE_OUTPUT,
            "restricted row-level output resolves inside the Git worktree",
        )
    return destination.resolve()


def forbidden_safe_columns(columns: Iterable[str]) -> list[str]:
    bad: list[str] = []
    for raw in columns:
        column = str(raw).strip().lower()
        if (
            column in FORBIDDEN_SAFE_COLUMNS
            or column.endswith("_filepath")
            or column.endswith("_filename")
            or column.endswith("_absolute_path")
            or column.endswith("_local_path")
            or column.endswith("_restricted_path")
        ):
            bad.append(str(raw))
    return sorted(set(bad))


def assert_export_safe_columns(columns: Iterable[str], label: str) -> None:
    bad = forbidden_safe_columns(columns)
    if bad:
        raise Tier1BlockedError(
            BLOCKED_UNSAFE_OUTPUT,
            f"export-safe {label} contains forbidden row-level columns: {bad}",
        )


def assert_export_safe_frame(frame: pd.DataFrame, label: str) -> None:
    assert_export_safe_columns(frame.columns, label)


def safe_file_record(role: str, path: Path, row_count: int | None = None) -> dict[str, Any]:
    """Return path-free provenance for one file."""

    if not SAFE_ROLE_PATTERN.fullmatch(str(role)):
        raise ValueError(f"Logical file role must be a path-free identifier: {role!r}")

    record: dict[str, Any] = {
        "logical_role": role,
        "sha256": sha256_file(path),
        "size_bytes": int(path.stat().st_size),
    }
    if row_count is not None:
        record["row_count"] = int(row_count)
    return record


def restricted_file_record(
    role: str,
    path: Path,
    row_count: int | None = None,
) -> dict[str, Any]:
    """Return path-bearing provenance for storage in a restricted manifest."""

    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        **safe_file_record(role, resolved, row_count),
    }


def schema_hash(frame: pd.DataFrame) -> str:
    schema = [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
    return sha256_json(schema)


def sanitize_for_safe_manifest(value: Any) -> Any:
    """Remove filesystem locations while preserving reproducibility metadata."""

    if isinstance(value, Path):
        return "[REDACTED_PATH]"
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower == "path" or lower.endswith("_path") or lower.endswith("_filepath"):
                continue
            clean[str(key)] = sanitize_for_safe_manifest(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_for_safe_manifest(item) for item in value]
    if isinstance(value, str):
        expanded = os.path.expanduser(value)
        if (
            value.startswith(("/", "~"))
            or expanded.startswith("/")
            or ABSOLUTE_PATH_TOKEN_PATTERN.search(value)
        ):
            return "[REDACTED_PATH]"
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_safe_csv(path: Path, frame: pd.DataFrame, label: str) -> None:
    assert_export_safe_frame(frame, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def require_columns(frame: pd.DataFrame, required: Sequence[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} missing required columns: {missing}")


def parse_named_paths(values: Sequence[str], label: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use NAME=PATH syntax: {value!r}")
        name, raw_path = value.split("=", 1)
        name = name.strip()
        if not name or name in parsed:
            raise ValueError(f"Duplicate or empty {label} name: {name!r}")
        if not SAFE_ROLE_PATTERN.fullmatch(name):
            raise ValueError(f"{label} name must be a path-free identifier: {name!r}")
        parsed[name] = Path(raw_path).expanduser()
    return parsed
