"""Shared, outcome-blind helpers for clip-provenance audits.

The helpers in this module inspect manifest metadata and file availability. They
never read labels, predictions, or model metrics. Identifier-bearing results
must be written only to a path accepted by ``require_restricted_path`` by the
calling audit.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from audit_lvef_multitask_artifacts import (
    _successful_clip_manifest,
    canonical_identifier_series,
)
from lvef_multitask_audit_utils import (
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    parse_named_path,
    resolve_column,
)


EXPECTED_COMPONENTS: tuple[str, ...] = ("stage_d",) + tuple(
    f"batch_{index:03d}" for index in range(9)
)

DICOM_LOCATOR_COLUMNS: tuple[str, ...] = (
    "dicom_abs_path",
    "source_dicom_path",
    "dicom_filepath",
)
NPZ_LOCATOR_COLUMNS: tuple[str, ...] = (
    "npz_path",
    "output_path",
    "npz_path_source",
)
DICOM_HASH_COLUMNS: tuple[str, ...] = (
    "dicom_sha256",
    "source_dicom_sha256",
    "source_sha256",
    "dicom_hash",
    "source_file_sha256",
)
NPZ_HASH_COLUMNS: tuple[str, ...] = (
    "npz_sha256",
    "extracted_sha256",
    "output_sha256",
    "clip_sha256",
    "npz_file_sha256",
)
FRAME_HASH_COLUMNS: tuple[str, ...] = (
    "frame_array_sha256",
    "frames_sha256",
    "content_sha256",
)
EXTRACTION_METADATA_COLUMNS: tuple[str, ...] = (
    "source_num_frames",
    "source_rows",
    "source_columns",
    "target_frames",
    "target_size",
    "output_size_bytes",
    "status",
)


def named_component_paths(
    values: Sequence[str],
    *,
    label: str,
    require_complete_set: bool = True,
) -> dict[str, Path]:
    """Parse NAME=PATH arguments under the fixed Stage-D/batch allowlist."""
    result: dict[str, Path] = {}
    for value in values:
        name, path = parse_named_path(value)
        if name not in EXPECTED_COMPONENTS:
            raise ValueError(f"Invalid {label} component label")
        if name in result:
            raise ValueError(f"Duplicate {label} component label")
        result[name] = path.expanduser()
    if require_complete_set and set(result) != set(EXPECTED_COMPONENTS):
        raise ValueError(f"{label} components are not exactly Stage-D and batch_000-008")
    return result


def parse_path_rewrites(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Parse ordered FROM=TO locator rewrites without touching the filesystem."""
    rewrites: list[tuple[str, str]] = []
    for value in values:
        if "=" not in value:
            raise ValueError("Path rewrite must be FROM=TO")
        source, replacement = value.split("=", 1)
        if not source:
            raise ValueError("Path rewrite source must be nonblank")
        rewrites.append((source, replacement))
    return tuple(rewrites)


def normalized_locator(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"na", "nan", "none", "null", "<missing>"}:
        return None
    return text


def first_locator(
    records: Sequence[Mapping[str, object]], candidates: Sequence[str]
) -> tuple[str | None, str | None]:
    """Return the first nonblank locator and its source column."""
    for record in records:
        lower = {str(key).casefold(): key for key in record}
        for candidate in candidates:
            key = lower.get(candidate.casefold())
            if key is None:
                continue
            value = normalized_locator(record.get(key))
            if value is not None:
                return value, str(key)
    return None, None


def first_manifest_hash(
    records: Sequence[Mapping[str, object]], candidates: Sequence[str]
) -> tuple[str | None, str | None]:
    """Return a declared 64-hex SHA-256 and its source column when present."""
    for record in records:
        lower = {str(key).casefold(): key for key in record}
        for candidate in candidates:
            key = lower.get(candidate.casefold())
            if key is None:
                continue
            value = normalized_locator(record.get(key))
            if value is not None and len(value) == 64:
                try:
                    int(value, 16)
                except ValueError:
                    continue
                return value.casefold(), str(key)
    return None, None


def apply_path_rewrites(locator: str, rewrites: Sequence[tuple[str, str]]) -> str:
    for source, replacement in rewrites:
        if locator.startswith(source):
            return replacement + locator[len(source) :]
    return locator


def resolve_locator_path(
    locator: str | None,
    *,
    roots: Sequence[Path] = (),
    rewrites: Sequence[tuple[str, str]] = (),
) -> Path | None:
    if locator is None:
        return None
    rewritten = apply_path_rewrites(locator, rewrites)
    path = Path(rewritten).expanduser()
    candidates = [path] if path.is_absolute() else [root / rewritten.lstrip("/") for root in roots]
    if not candidates:
        candidates = [path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


@lru_cache(maxsize=None)
def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=None)
def npz_descriptor(path: Path | None, *, compute_hash: bool = True) -> dict[str, Any]:
    """Inspect an extracted cine without returning pixel values."""
    result: dict[str, Any] = {
        "exists": bool(path is not None and path.is_file() and not path.is_symlink()),
        "computed_sha256": None,
        "frames_shape": None,
        "frames_count": None,
        "frames_dtype": None,
        "frames_sha256": None,
        "array_metadata_json": None,
        "status": "MISSING",
    }
    if not result["exists"]:
        return result
    if compute_hash:
        result["computed_sha256"] = sha256_file(path)
    metadata: list[dict[str, object]] = []
    try:
        assert path is not None
        with np.load(path, allow_pickle=False) as data:
            for name in sorted(data.files):
                array = np.asarray(data[name])
                descriptor = {
                    "name": name,
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "sha256": hashlib.sha256(
                        np.ascontiguousarray(array).tobytes()
                    ).hexdigest(),
                }
                metadata.append(descriptor)
                if name == "frames":
                    result["frames_shape"] = json.dumps(list(array.shape))
                    result["frames_count"] = int(array.shape[0]) if array.ndim else None
                    result["frames_dtype"] = str(array.dtype)
                    result["frames_sha256"] = descriptor["sha256"]
        result["array_metadata_json"] = json.dumps(metadata, sort_keys=True)
        result["status"] = "OK" if result["frames_shape"] is not None else "NO_FRAMES_ARRAY"
    except Exception:
        result["status"] = "UNREADABLE"
    return result


def successful_extraction_manifest(frame: pd.DataFrame) -> pd.DataFrame:
    """Return successful extraction rows with case-normalized columns."""
    lower_columns = [str(column).casefold() for column in frame.columns]
    if len(lower_columns) != len(set(lower_columns)):
        raise ValueError("Extraction manifest has duplicate case-insensitive columns")
    work = frame.rename(columns=dict(zip(frame.columns, lower_columns, strict=True))).copy()
    success_column = resolve_column(work, ("write_ok", "extract_ok"), required=True)
    assert success_column is not None
    values = work[success_column]
    if pd.api.types.is_bool_dtype(values):
        mask = values.fillna(False).astype(bool)
    else:
        mask = values.astype("string").str.strip().str.casefold().isin(
            {"true", "1", "yes", "y"}
        )
    return work.loc[mask].copy()


def prepared_clip_manifest(path: Path) -> pd.DataFrame:
    return _successful_clip_manifest(load_table(path))


def canonical_subject_study(frame: pd.DataFrame) -> pd.DataFrame:
    subject = resolve_column(frame, SUBJECT_COLUMNS, required=True, label="subject")
    study = resolve_column(frame, STUDY_COLUMNS, required=True, label="study")
    assert subject is not None and study is not None
    result = pd.DataFrame(
        {
            "subject_id": canonical_identifier_series(frame[subject]),
            "study_id": canonical_identifier_series(frame[study]),
        },
        index=frame.index,
    )
    if result.isna().any().any():
        raise ValueError("Manifest contains missing subject or study identifiers")
    return result


def selected_study_map(frame: pd.DataFrame) -> dict[str, str]:
    pairs = canonical_subject_study(frame)
    if pairs["subject_id"].duplicated().any() or pairs["study_id"].duplicated().any():
        raise ValueError("Selected cohort is not one study per subject")
    return dict(zip(pairs["study_id"], pairs["subject_id"], strict=True))


def extraction_lookup(frame: pd.DataFrame) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Index extraction rows by subject, study, and DICOM locator."""
    work = successful_extraction_manifest(frame)
    ids = canonical_subject_study(work)
    # Join on the stable source locator used by clip/extraction manifests, while
    # retaining any absolute audit locator as evidence in the matched record.
    dicom_column = resolve_column(
        work,
        ("dicom_filepath", "source_dicom_path", "dicom_abs_path"),
        required=True,
        label="DICOM locator",
    )
    assert dicom_column is not None
    result: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for index, row in work.iterrows():
        locator = normalized_locator(row[dicom_column])
        if locator is None:
            continue
        key = (str(ids.at[index, "subject_id"]), str(ids.at[index, "study_id"]), locator)
        result.setdefault(key, []).append(row.to_dict())
    return result


def dicom_audit_lookup(frame: pd.DataFrame) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Index readable DICOM-audit rows by subject, study, and source locator."""
    lower_columns = [str(column).casefold() for column in frame.columns]
    if len(lower_columns) != len(set(lower_columns)):
        raise ValueError("DICOM audit has duplicate case-insensitive columns")
    work = frame.rename(columns=dict(zip(frame.columns, lower_columns, strict=True))).copy()
    read_column = resolve_column(work, ("read_ok",))
    if read_column is not None:
        values = work[read_column]
        if pd.api.types.is_bool_dtype(values):
            mask = values.fillna(False).astype(bool)
        else:
            mask = values.astype("string").str.strip().str.casefold().isin(
                {"true", "1", "yes", "y"}
            )
        work = work.loc[mask].copy()
    ids = canonical_subject_study(work)
    dicom_column = resolve_column(
        work,
        ("dicom_filepath", "source_dicom_path", "dicom_abs_path"),
        required=True,
        label="DICOM locator",
    )
    assert dicom_column is not None
    result: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for index, row in work.iterrows():
        locator = normalized_locator(row[dicom_column])
        if locator is None:
            continue
        key = (str(ids.at[index, "subject_id"]), str(ids.at[index, "study_id"]), locator)
        result.setdefault(key, []).append(row.to_dict())
    return result


def extraction_records_for_row(
    row: Mapping[str, object],
    lookup: Mapping[tuple[str, str, str], Sequence[Mapping[str, object]]],
) -> list[Mapping[str, object]]:
    record: Mapping[str, object] = (
        row.to_dict() if isinstance(row, pd.Series) else row
    )
    subject = str(record.get("_audit_subject", ""))
    study = str(record.get("_audit_study", ""))
    locator, _ = first_locator([record], DICOM_LOCATOR_COLUMNS)
    if not subject or not study or locator is None:
        return []
    return list(lookup.get((subject, study, locator), ()))


def canonical_metadata_json(records: Sequence[Mapping[str, object]]) -> str | None:
    """Return stable extraction metadata without identifiers, locators, or values arrays."""
    payloads: list[dict[str, str]] = []
    for record in records:
        lower = {str(key).casefold(): key for key in record}
        payload: dict[str, str] = {}
        for candidate in EXTRACTION_METADATA_COLUMNS:
            key = lower.get(candidate.casefold())
            if key is None:
                continue
            value = record.get(key)
            if value is None or pd.isna(value):
                continue
            payload[candidate] = str(value).strip()
        if payload:
            payloads.append(payload)
    if not payloads:
        return None
    return json.dumps(payloads, sort_keys=True, separators=(",", ":"))


def equality_status(values: Iterable[object], expected_count: int) -> str:
    present = [str(value) for value in values if value is not None and not pd.isna(value)]
    if not present:
        return "MISSING"
    if len(present) < expected_count:
        return "PARTIAL"
    return "COMPLETE_EQUAL" if len(set(present)) == 1 else "COMPLETE_DIFFERENT"


def existence_status(values: Iterable[bool], expected_count: int) -> str:
    flags = list(values)
    if len(flags) < expected_count:
        return "PARTIAL"
    if flags and all(flags):
        return "ALL_EXIST"
    if any(flags):
        return "SOME_EXIST"
    return "NONE_EXIST"
