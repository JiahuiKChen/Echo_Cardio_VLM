#!/usr/bin/env python3
"""Shared utilities for aggregate-safe LVEF/multitask audit scripts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


SUBJECT_COLUMNS = ("subject_id", "patient_id", "person_id")
STUDY_COLUMNS = ("study_id", "dicom_study_id", "study")
SPLIT_COLUMNS = ("split", "data_split", "partition")
FORBIDDEN_AGGREGATE_COLUMNS = {
    "subject_id",
    "patient_id",
    "person_id",
    "study_id",
    "dicom_study_id",
    "y_true",
    "y_pred",
    "label",
    "prediction",
    "embedding",
    "path",
    "file_path",
}


def load_table(path: Path) -> pd.DataFrame:
    """Load a CSV/TSV/Parquet/JSON-lines table without changing identifiers."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False)
    if suffix in {".tsv", ".txt"}:
        return pd.read_csv(path, sep="\t", low_memory=False)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(path, lines=True)
    if suffix == ".json":
        payload = json.loads(path.read_text())
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return pd.DataFrame(payload["records"])
        raise ValueError(f"JSON table must be a list or contain a records list: {path}")
    raise ValueError(f"Unsupported table format for {path}; use CSV, TSV, Parquet, or JSON-lines")


def resolve_column(
    frame: pd.DataFrame,
    candidates: Sequence[str],
    *,
    required: bool = False,
    label: str = "column",
) -> str | None:
    lower_map = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    if required:
        raise ValueError(f"Could not resolve {label}; tried {list(candidates)}")
    return None


def normalized_ids(series: pd.Series) -> set[str]:
    values = series.dropna().astype(str).str.strip()
    return set(values[values != ""].tolist())


def id_set_hash(values: Iterable[str]) -> str:
    payload = "\n".join(sorted(set(str(value) for value in values))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def entity_counts(frame: pd.DataFrame) -> dict[str, int | None]:
    subject_col = resolve_column(frame, SUBJECT_COLUMNS)
    study_col = resolve_column(frame, STUDY_COLUMNS)
    return {
        "n_rows": int(len(frame)),
        "n_subjects": int(frame[subject_col].nunique(dropna=True)) if subject_col else None,
        "n_studies": int(frame[study_col].nunique(dropna=True)) if study_col else None,
    }


def assert_aggregate_safe_columns(frame: pd.DataFrame) -> None:
    unsafe = [column for column in frame.columns if str(column).lower() in FORBIDDEN_AGGREGATE_COLUMNS]
    if unsafe:
        raise ValueError(f"Aggregate output contains identifier/patient-level columns: {unsafe}")


def write_aggregate_csv(frame: pd.DataFrame, path: Path) -> None:
    assert_aggregate_safe_columns(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def write_json(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def require_restricted_path(path: Path) -> Path:
    """Reject patient/study-level output paths inside the repository."""
    resolved = path.expanduser().resolve()
    root = repository_root()
    if resolved == root or root in resolved.parents:
        raise ValueError(f"Restricted output must be outside the repository: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(f"Expected NAME=PATH, received: {value}")
    name, raw_path = value.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"Artifact/cohort name is empty: {value}")
    return name, Path(raw_path).expanduser()
