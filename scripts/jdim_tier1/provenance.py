"""Restricted and export-safe reproducibility manifests."""
from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from . import PROTOCOL_VERSION
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sanitize_for_safe_manifest,
    schema_hash,
    sha256_file,
    write_json,
)


REQUIRED_SPEC_KEYS = {
    "manifest_version",
    "mimic_iv_echo_release",
    "echoprime_code_release",
    "files",
    "split_map_role",
    "selected_study_universe_role",
    "structured_measurement_role",
    "embedding_manifest_role",
    "video_encoder_checkpoint_role",
    "audit_configuration_role",
    "script_arguments",
    "codex_assisted_artifacts",
}


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _package_versions(names: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    return versions


def _validate_spec(spec: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_SPEC_KEYS - set(spec))
    if missing:
        raise ValueError(f"Provenance specification missing required keys: {missing}")
    if spec["manifest_version"] != "jdim-provenance-v1":
        raise ValueError("manifest_version must be jdim-provenance-v1")
    if not str(spec["mimic_iv_echo_release"]).strip():
        raise ValueError("mimic_iv_echo_release must be explicit")
    if not isinstance(spec["files"], Mapping) or not spec["files"]:
        raise ValueError("files must be a nonempty logical-role mapping")
    roles = set(spec["files"])
    required_roles = {
        spec["split_map_role"],
        spec["selected_study_universe_role"],
        spec["structured_measurement_role"],
        spec["embedding_manifest_role"],
        spec["video_encoder_checkpoint_role"],
        spec["audit_configuration_role"],
    }
    missing_roles = sorted(required_roles - roles)
    if missing_roles:
        raise ValueError(f"Required logical file roles are absent: {missing_roles}")


def _file_metadata(role: str, raw: Any) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame | None]:
    if isinstance(raw, str):
        path = Path(raw)
        classification = "restricted"
    elif isinstance(raw, Mapping):
        path = Path(str(raw.get("path", "")))
        classification = str(raw.get("classification", "restricted"))
    else:
        raise ValueError(f"Invalid file specification for {role}")
    if not path.is_absolute():
        raise ValueError(f"Provenance file path for {role} must be absolute")
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"provenance input role {role} is missing")
    restricted = {
        "logical_role": role,
        "path": str(path.resolve()),
        "classification": classification,
        "sha256": sha256_file(path),
        "size_bytes": int(path.stat().st_size),
    }
    safe = safe_file_record(role, path)
    safe["classification"] = classification
    frame: pd.DataFrame | None = None
    if path.suffix.lower() == ".csv":
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        restricted["row_count"] = int(len(frame))
        restricted["schema"] = [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
        safe["row_count"] = int(len(frame))
        safe["schema_sha256"] = schema_hash(frame)
        safe["field_count"] = int(len(frame.columns))
    return restricted, safe, frame


def _split_summary(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    require_columns(frame, ["subject_id", "split"], "split map")
    work = frame.copy()
    work["subject_id"] = work["subject_id"].astype(str)
    work["split"] = work["split"].astype(str).str.lower()
    invalid = sorted(set(work["split"]) - {"train", "val", "test"})
    conflicts = work.groupby("subject_id")["split"].nunique()
    overlap_n = int((conflicts > 1).sum())
    if invalid or overlap_n:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"split map invalid labels={invalid}, subject_overlap_count={overlap_n}",
        )
    deduplicated = work.drop_duplicates("subject_id", keep="first")
    counts = {split: int((deduplicated["split"] == split).sum()) for split in ("train", "val", "test")}
    restricted = {
        "schema": [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()],
        "n_rows": int(len(frame)),
        "n_unique_subjects": int(deduplicated["subject_id"].nunique()),
        "subjects_per_split": counts,
        "subject_overlap_count": overlap_n,
        "invalid_split_labels": invalid,
    }
    safe = {
        "schema_sha256": schema_hash(frame),
        "field_count": int(len(frame.columns)),
        "n_rows": int(len(frame)),
        "n_unique_subjects": int(deduplicated["subject_id"].nunique()),
        "subjects_per_split": counts,
        "subject_overlap_count": overlap_n,
        "invalid_split_label_count": len(invalid),
    }
    return restricted, safe


def _analysis_universe_split_summary(
    selected: pd.DataFrame,
    split_map: pd.DataFrame,
) -> dict[str, Any]:
    require_columns(selected, ["subject_id", "study_id"], "selected study universe")
    require_columns(split_map, ["subject_id", "split"], "split map")
    studies = selected[["subject_id", "study_id"]].copy()
    studies["subject_id"] = studies["subject_id"].astype(str)
    studies["study_id"] = studies["study_id"].astype(str)
    conflicts = studies.groupby("study_id")["subject_id"].nunique()
    if int((conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "selected study universe has conflicting subject assignments")
    studies = studies.drop_duplicates("study_id", keep="first")
    splits = split_map[["subject_id", "split"]].copy()
    splits["subject_id"] = splits["subject_id"].astype(str)
    splits["split"] = splits["split"].astype(str).str.lower()
    splits = splits.drop_duplicates("subject_id", keep="first")
    joined = studies.merge(splits, on="subject_id", how="left", validate="many_to_one")
    missing = int(joined["split"].isna().sum())
    counts = {
        split: {
            "n_studies": int((joined["split"] == split).sum()),
            "n_subjects": int(joined.loc[joined["split"] == split, "subject_id"].nunique()),
        }
        for split in ("train", "val", "test")
    }
    return {
        "n_studies": int(len(studies)),
        "n_subjects": int(studies["subject_id"].nunique()),
        "studies_missing_split_assignment": missing,
        "per_split": counts,
    }


def build_provenance_manifests(
    spec: Mapping[str, Any],
    repo_root: Path,
    allow_dirty_for_synthetic_tests: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_spec(spec)
    repo_root = repo_root.resolve()
    status = _git(repo_root, "status", "--short")
    dirty = bool(status.strip())
    if dirty and not allow_dirty_for_synthetic_tests:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "repository is dirty; provenance run requires a clean checkout")
    repository = {
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "branch": _git(repo_root, "branch", "--show-current"),
        "dirty": dirty,
        "dirty_status": status.splitlines() if dirty else [],
        "dirty_state_prohibited": True,
    }

    restricted_files: list[dict[str, Any]] = []
    safe_files: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    for role, raw in spec["files"].items():
        restricted, safe, frame = _file_metadata(str(role), raw)
        restricted_files.append(restricted)
        safe_files.append(safe)
        if frame is not None:
            frames[str(role)] = frame

    split_role = str(spec["split_map_role"])
    if split_role not in frames:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "split-map role must point to a CSV")
    restricted_split, safe_split = _split_summary(frames[split_role])
    selected_role = str(spec["selected_study_universe_role"])
    if selected_role not in frames:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "selected-study-universe role must point to a CSV")
    universe_split_summary = _analysis_universe_split_summary(frames[selected_role], frames[split_role])

    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": _package_versions(["numpy", "pandas", "scikit-learn", "scipy", "pydicom", "torch", "torchvision"]),
    }
    restricted = {
        "manifest_version": spec["manifest_version"],
        "protocol_version": PROTOCOL_VERSION,
        "repository": repository,
        "mimic_iv_echo_release": spec["mimic_iv_echo_release"],
        "echoprime_code_release": spec["echoprime_code_release"],
        "files": restricted_files,
        "role_assignments": {
            key: spec[key]
            for key in (
                "split_map_role",
                "selected_study_universe_role",
                "structured_measurement_role",
                "embedding_manifest_role",
                "video_encoder_checkpoint_role",
                "audit_configuration_role",
            )
        },
        "split_map": restricted_split,
        "analysis_universe_split_counts": universe_split_summary,
        "environment": environment,
        "script_arguments": spec["script_arguments"],
        "codex_assisted_artifacts": spec["codex_assisted_artifacts"],
    }
    safe_repository = {key: value for key, value in repository.items() if key != "dirty_status"}
    safe = {
        "manifest_version": spec["manifest_version"],
        "protocol_version": PROTOCOL_VERSION,
        "repository": safe_repository,
        "mimic_iv_echo_release": spec["mimic_iv_echo_release"],
        "echoprime_code_release": spec["echoprime_code_release"],
        "files": safe_files,
        "role_assignments": {
            key: spec[key]
            for key in (
                "split_map_role",
                "selected_study_universe_role",
                "structured_measurement_role",
                "embedding_manifest_role",
                "video_encoder_checkpoint_role",
                "audit_configuration_role",
            )
        },
        "split_map": safe_split,
        "analysis_universe_split_counts": universe_split_summary,
        "environment": environment,
        "script_arguments": sanitize_for_safe_manifest(spec["script_arguments"]),
        "codex_assisted_artifacts": sanitize_for_safe_manifest(spec["codex_assisted_artifacts"]),
        "contains_absolute_or_restricted_paths": False,
    }
    safe = sanitize_for_safe_manifest(safe)
    serialized = json.dumps(safe, sort_keys=True)
    for restricted_path in [item["path"] for item in restricted_files]:
        if restricted_path in serialized:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "export-safe manifest retained a restricted path")
    return restricted, safe


def write_provenance_manifests(
    restricted: Mapping[str, Any],
    safe: Mapping[str, Any],
    restricted_output: Path,
    safe_output: Path,
) -> None:
    restricted_destination = require_restricted_destination(restricted_output)
    write_json(restricted_destination, restricted)
    write_json(safe_output, safe)
