#!/usr/bin/env python3
"""Classify duplicate historical clip keys without exporting restricted values.

The audit reads component and merged clip manifests plus their embedding arrays
on SCC. Identifier-, locator-, hash-, frame-, and vector-level evidence is
written only beneath ``--restricted-output-dir``. The aggregate directory
contains fixed-vocabulary grouped counts and summary flags; it is safe to copy
only after the emitted safety gate passes.

This script never fits a model, regenerates an embedding, or reads outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from audit_lvef_multitask_artifacts import (
    CLIP_LOCATOR_COLUMNS,
    _canonical_manifest_scalar,
    _prepare_clip_manifest_keys,
    _successful_clip_manifest,
    canonical_identifier_series,
)
from lvef_multitask_audit_utils import (
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    parse_named_path,
    require_restricted_path,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


COMPONENT_RE = re.compile(r"(?:stage_d|batch_[0-9]{3})\Z")
CLASSIFICATIONS = (
    "EXACT_REPEATED_MANIFEST_ROW",
    "SAME_CLIP_EMBEDDED_TWICE",
    "DIFFERENT_CLIPS_SHARE_NONUNIQUE_KEY",
    "MERGE_REINDEXING_DEFECT",
    "OTHER_UNRESOLVED",
)
RESOLUTIONS = {
    "EXACT_REPEATED_MANIFEST_ROW": (
        "DEDUPLICATE_EXACT_ROW_AND_REBUILD_MERGED_AND_STUDY_STORES"
    ),
    "SAME_CLIP_EMBEDDED_TWICE": (
        "RETAIN_ONE_CONTENT_IDENTICAL_CLIP_AND_REBUILD_MERGED_AND_STUDY_STORES"
    ),
    "DIFFERENT_CLIPS_SHARE_NONUNIQUE_KEY": (
        "CORRECT_KEY_SCHEMA_AND_REEXTRACT_OR_REEMBED_AFFECTED_CLIPS"
    ),
    "MERGE_REINDEXING_DEFECT": "REBUILD_MERGE_FROM_VALIDATED_COMPONENT_STORES",
    "OTHER_UNRESOLVED": "QUARANTINE_AND_REVIEW_OR_REPROCESS_AFFECTED_CLIPS",
}
AGGREGATE_COLUMNS = [
    "classification",
    "component",
    "selected_scope",
    "proposed_resolution",
    "n_unique_duplicate_groups",
]


@dataclass(frozen=True)
class GroupEvidence:
    """Identifier-free facts used to classify one restricted duplicate group."""

    source_count: int
    merged_count: int
    source_full_rows_equal: bool
    source_nonindex_rows_equal: bool
    source_vectors_exact_equal: bool
    source_vectors_numerically_equal: bool
    merged_vectors_exact_equal: bool
    merged_vectors_numerically_equal: bool
    source_merged_manifest_payload_equal_excluding_index_and_norm: bool
    source_merged_vectors_exact_multiset_equal: bool
    source_merged_vectors_numerically_match: bool
    extracted_hashes_complete: bool
    n_unique_extracted_hashes: int
    frame_hashes_complete: bool
    n_unique_frame_hashes: int
    dicom_hashes_complete: bool
    n_unique_dicom_hashes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--component-manifest",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="Repeat once for Stage D and each batch component.",
    )
    parser.add_argument(
        "--component-embedding-npz",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="Embedding NPZ paired to each component manifest.",
    )
    parser.add_argument("--merged-manifest", type=Path, required=True)
    parser.add_argument("--merged-embedding-npz", type=Path, required=True)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--expected-component-count", type=int, default=10)
    parser.add_argument("--expected-duplicate-keys", type=int, default=32)
    parser.add_argument("--expected-embedding-dim", type=int, default=512)
    parser.add_argument("--vector-rtol", type=float, default=1e-6)
    parser.add_argument("--vector-atol", type=float, default=1e-6)
    return parser.parse_args()


def _named_paths(values: Sequence[str], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, path = parse_named_path(value)
        if COMPONENT_RE.fullmatch(name) is None:
            raise ValueError(f"Invalid {label} component label")
        if name in result:
            raise ValueError(f"Duplicate {label} component label")
        result[name] = path.expanduser()
    return result


def _load_embeddings(path: Path, expected_dim: int) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError("Embedding NPZ is not a regular file")
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"embeddings"}:
            raise ValueError("Embedding NPZ must contain exactly the embeddings array")
        embeddings = np.asarray(data["embeddings"])
    if embeddings.ndim != 2 or embeddings.shape[1] != expected_dim:
        raise ValueError("Embedding array has an unexpected shape")
    if not np.issubdtype(embeddings.dtype, np.floating):
        raise ValueError("Embedding array is not floating point")
    if not np.isfinite(embeddings).all():
        raise ValueError("Embedding array contains nonfinite values")
    return embeddings


def _truthy(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def _attach_vectors(
    frame: pd.DataFrame, embeddings: np.ndarray, component: str
) -> pd.DataFrame:
    if "embedding_idx" not in frame.columns:
        raise ValueError("Clip manifest lacks embedding_idx")
    numeric = pd.to_numeric(frame["embedding_idx"], errors="coerce")
    if numeric.isna().any() or not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError("Clip manifest has a nonintegral embedding index")
    indices = numeric.astype(np.int64)
    if (indices < 0).any() or (indices >= len(embeddings)).any():
        raise ValueError("Clip manifest embedding index is outside the paired array")
    work = frame.copy()
    work["_component"] = component
    work["_component_row"] = [int(index) for index in work.index]
    work["_embedding_vector"] = [embeddings[index].copy() for index in indices]
    work["_embedding_vector_sha256"] = [
        hashlib.sha256(np.ascontiguousarray(vector).tobytes()).hexdigest()
        for vector in work["_embedding_vector"]
    ]
    return work


@lru_cache(maxsize=None)
def _sha256_file(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _path_or_none(value: object) -> Path | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser()


def _first_locator_path(
    row: pd.Series, candidates: Sequence[str]
) -> tuple[Path | None, str | None]:
    paths = [
        (path, column)
        for column in candidates
        if (path := _path_or_none(row.get(column))) is not None
    ]
    return next(
        ((path, column) for path, column in paths if path.is_file()),
        paths[0] if paths else (None, None),
    )


@lru_cache(maxsize=None)
def _npz_descriptor(path: Path | None) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "extracted_exists": False,
        "extracted_sha256": None,
        "frame_array_shape": None,
        "frame_array_dtype": None,
        "frame_array_sha256": None,
        "npz_array_metadata_json": None,
        "npz_inspection_status": "MISSING_OR_UNRESOLVED",
    }
    if path is None or not path.is_file() or path.is_symlink():
        return empty
    result = dict(empty)
    result["extracted_exists"] = True
    result["extracted_sha256"] = _sha256_file(path)
    metadata: list[dict[str, object]] = []
    try:
        with np.load(path, allow_pickle=False) as data:
            for name in sorted(data.files):
                array = np.asarray(data[name])
                array_sha = hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()
                metadata.append(
                    {
                        "name": name,
                        "shape": list(array.shape),
                        "dtype": str(array.dtype),
                        "sha256": array_sha,
                    }
                )
                if name == "frames":
                    result["frame_array_shape"] = json.dumps(list(array.shape))
                    result["frame_array_dtype"] = str(array.dtype)
                    result["frame_array_sha256"] = array_sha
        result["npz_array_metadata_json"] = json.dumps(metadata, sort_keys=True)
        result["npz_inspection_status"] = (
            "OK" if result["frame_array_sha256"] is not None else "NO_FRAMES_ARRAY"
        )
    except Exception:
        result["npz_inspection_status"] = "UNREADABLE"
    return result


@lru_cache(maxsize=None)
def _dicom_descriptor(path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dicom_exists": False,
        "dicom_sha256": None,
        "dicom_frame_metadata_json": None,
        "dicom_metadata_status": "MISSING_OR_UNRESOLVED",
    }
    if path is None or not path.is_file() or path.is_symlink():
        return result
    result["dicom_exists"] = True
    result["dicom_sha256"] = _sha256_file(path)
    try:
        import pydicom  # type: ignore[import-not-found]
    except ImportError:
        result["dicom_metadata_status"] = "PYDICOM_UNAVAILABLE"
        return result
    tags = [
        "Rows",
        "Columns",
        "NumberOfFrames",
        "FrameTime",
        "FrameTimeVector",
        "CineRate",
        "RecommendedDisplayFrameRate",
        "PhotometricInterpretation",
        "SamplesPerPixel",
        "BitsAllocated",
    ]
    try:
        dataset = pydicom.dcmread(
            str(path), stop_before_pixels=True, force=False, specific_tags=tags
        )
        metadata = {}
        for tag in tags:
            if hasattr(dataset, tag):
                value = getattr(dataset, tag)
                if isinstance(value, (list, tuple)):
                    metadata[tag] = [str(item) for item in value]
                else:
                    metadata[tag] = str(value)
        result["dicom_frame_metadata_json"] = json.dumps(metadata, sort_keys=True)
        result["dicom_metadata_status"] = "OK"
    except Exception:
        result["dicom_metadata_status"] = "UNREADABLE"
    return result


def _canonical_row_tuple(row: pd.Series, columns: Iterable[str]) -> tuple[str, ...]:
    return tuple(_canonical_manifest_scalar(row.get(column)) for column in columns)


def _all_vectors_equal(vectors: Sequence[np.ndarray], *, rtol: float, atol: float) -> tuple[bool, bool]:
    if not vectors:
        return False, False
    first = vectors[0]
    exact = all(np.array_equal(first, vector) for vector in vectors[1:])
    close = all(np.allclose(first, vector, rtol=rtol, atol=atol, equal_nan=False) for vector in vectors[1:])
    return exact, close


def _vector_multisets_match(
    left: Sequence[np.ndarray],
    right: Sequence[np.ndarray],
    *,
    rtol: float,
    atol: float,
) -> tuple[bool, bool]:
    if len(left) != len(right):
        return False, False
    left_hashes = Counter(
        hashlib.sha256(np.ascontiguousarray(vector).tobytes()).hexdigest() for vector in left
    )
    right_hashes = Counter(
        hashlib.sha256(np.ascontiguousarray(vector).tobytes()).hexdigest() for vector in right
    )
    exact = left_hashes == right_hashes
    unmatched = list(range(len(right)))
    for vector in left:
        match = next(
            (
                index
                for index in unmatched
                if np.allclose(vector, right[index], rtol=rtol, atol=atol, equal_nan=False)
            ),
            None,
        )
        if match is None:
            return exact, False
        unmatched.remove(match)
    return exact, True


def _coverage_and_unique(values: Sequence[object]) -> tuple[bool, int]:
    present = [str(value) for value in values if value is not None and not pd.isna(value)]
    return len(present) == len(values) and len(values) > 0, len(set(present))


def classify_duplicate_group(evidence: GroupEvidence) -> str:
    """Classify a duplicate group using an explicit precedence rule."""
    physical_difference = any(
        (
            complete and unique_count > 1
            for complete, unique_count in (
                (evidence.extracted_hashes_complete, evidence.n_unique_extracted_hashes),
                (evidence.frame_hashes_complete, evidence.n_unique_frame_hashes),
                (evidence.dicom_hashes_complete, evidence.n_unique_dicom_hashes),
            )
        )
    )
    if physical_difference:
        return "DIFFERENT_CLIPS_SHARE_NONUNIQUE_KEY"
    if (
        evidence.source_count != evidence.merged_count
        or evidence.source_count < 2
        or not evidence.source_merged_manifest_payload_equal_excluding_index_and_norm
        or not evidence.source_merged_vectors_numerically_match
    ):
        return "MERGE_REINDEXING_DEFECT"
    if (
        evidence.source_full_rows_equal
        and evidence.source_vectors_exact_equal
        and evidence.merged_vectors_exact_equal
        and evidence.source_merged_vectors_exact_multiset_equal
    ):
        return "EXACT_REPEATED_MANIFEST_ROW"
    physical_same = any(
        (
            complete and unique_count == 1
            for complete, unique_count in (
                (evidence.extracted_hashes_complete, evidence.n_unique_extracted_hashes),
                (evidence.frame_hashes_complete, evidence.n_unique_frame_hashes),
                (evidence.dicom_hashes_complete, evidence.n_unique_dicom_hashes),
            )
        )
    )
    if (
        physical_same
        and evidence.source_nonindex_rows_equal
        and evidence.source_vectors_numerically_equal
        and evidence.merged_vectors_numerically_equal
        and evidence.source_merged_vectors_numerically_match
    ):
        return "SAME_CLIP_EMBEDDED_TWICE"
    return "OTHER_UNRESOLVED"


def _selected_map(frame: pd.DataFrame) -> dict[str, str]:
    subject = resolve_column(frame, SUBJECT_COLUMNS, required=True, label="selected subject")
    study = resolve_column(frame, STUDY_COLUMNS, required=True, label="selected study")
    assert subject is not None and study is not None
    subjects = canonical_identifier_series(frame[subject])
    studies = canonical_identifier_series(frame[study])
    if subjects.isna().any() or studies.isna().any():
        raise ValueError("Selected cohort has missing identifiers")
    pairs = pd.DataFrame({"subject": subjects, "study": studies})
    if pairs["subject"].duplicated().any() or pairs["study"].duplicated().any():
        raise ValueError("Selected cohort is not one study per subject")
    return dict(zip(pairs["study"], pairs["subject"], strict=True))


def _group_scope(key: tuple[str, ...], selected: Mapping[str, str]) -> str:
    if len(key) < 2:
        return "unknown"
    subject, study = key[:2]
    if study in selected and selected[study] == subject:
        return "selected"
    if study not in selected:
        return "outside_selected"
    return "ownership_mismatch"


def _restricted_row_descriptor(row: pd.Series) -> dict[str, Any]:
    vector = np.asarray(row["_embedding_vector"])
    npz_path, npz_locator_source = _first_locator_path(
        row, ("npz_path", "output_path", "npz_path_source")
    )
    dicom_path, dicom_locator_source = _first_locator_path(
        row, ("dicom_filepath", "dicom_abs_path", "source_dicom_path")
    )
    result = {
        "component": str(row["_component"]),
        "component_row": int(row["_component_row"]),
        "subject_id": str(row["_audit_subject"]),
        "study_id": str(row["_audit_study"]),
        "clip_key": json.dumps(row["_audit_clip_key"], separators=(",", ":")),
        "dicom_filepath": None if dicom_path is None else str(dicom_path),
        "dicom_locator_source_column": dicom_locator_source,
        "npz_path": None if npz_path is None else str(npz_path),
        "extracted_locator_source_column": npz_locator_source,
        "embedding_idx": int(float(row["embedding_idx"])),
        "embedding_vector_sha256": str(row["_embedding_vector_sha256"]),
        "embedding_vector_dtype": str(vector.dtype),
        "embedding_vector_shape": json.dumps(list(vector.shape)),
        "embedding_l2_norm_manifest": row.get("embedding_l2_norm"),
        "embedding_l2_norm_computed": float(np.linalg.norm(vector)),
        "write_ok": _truthy(row.get("write_ok")),
    }
    result.update(_npz_descriptor(npz_path))
    result.update(_dicom_descriptor(dicom_path))
    return result


def _manifest_columns(frame: pd.DataFrame, *, include_index: bool) -> list[str]:
    excluded = {
        "_component",
        "_component_row",
        "_embedding_vector",
        "_embedding_vector_sha256",
    }
    excluded.update(column for column in frame.columns if column.startswith("_audit_"))
    if not include_index:
        excluded.add("embedding_idx")
    return sorted(column for column in frame.columns if column not in excluded)


def _numeric_values(frame: pd.DataFrame, column: str) -> list[float] | None:
    if column not in frame.columns:
        return None
    numeric = pd.to_numeric(frame[column], errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric).all():
        return None
    return [float(value) for value in numeric]


def _numeric_sequence_close(
    left: Sequence[float] | None,
    right: Sequence[float] | None,
    *,
    rtol: float,
    atol: float,
) -> bool:
    if left is None or right is None or len(left) != len(right):
        return False
    return all(
        math.isclose(a, b, rel_tol=rtol, abs_tol=atol)
        for a, b in zip(sorted(left), sorted(right), strict=True)
    )


def audit_duplicate_groups(
    source: pd.DataFrame,
    merged: pd.DataFrame,
    selected: Mapping[str, str],
    *,
    vector_rtol: float,
    vector_atol: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_counts = Counter(source["_audit_clip_key"])
    merged_counts = Counter(merged["_audit_clip_key"])
    duplicate_keys = {
        key
        for key in set(source_counts) | set(merged_counts)
        if source_counts[key] > 1 or merged_counts[key] > 1
    }
    group_rows: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, Any]] = []
    aggregate_memberships: defaultdict[tuple[str, str, str, str], set[tuple[str, ...]]] = defaultdict(set)
    for key in sorted(duplicate_keys):
        source_group = source[source["_audit_clip_key"] == key].copy()
        merged_group = merged[merged["_audit_clip_key"] == key].copy()
        source_descriptors = [
            _restricted_row_descriptor(row) for _, row in source_group.iterrows()
        ]
        merged_descriptors = [
            _restricted_row_descriptor(row) for _, row in merged_group.iterrows()
        ]
        for descriptor in source_descriptors + merged_descriptors:
            restricted_rows.append(descriptor)

        source_vectors = [np.asarray(value) for value in source_group["_embedding_vector"]]
        merged_vectors = [np.asarray(value) for value in merged_group["_embedding_vector"]]
        source_exact, source_close = _all_vectors_equal(
            source_vectors, rtol=vector_rtol, atol=vector_atol
        )
        merged_exact, merged_close = _all_vectors_equal(
            merged_vectors, rtol=vector_rtol, atol=vector_atol
        )
        cross_exact, cross_close = _vector_multisets_match(
            source_vectors,
            merged_vectors,
            rtol=vector_rtol,
            atol=vector_atol,
        )
        source_full_columns = _manifest_columns(source_group, include_index=True)
        source_nonindex_columns = _manifest_columns(source_group, include_index=False)
        full_rows = {
            _canonical_row_tuple(row, source_full_columns)
            for _, row in source_group.iterrows()
        }
        nonindex_rows = {
            _canonical_row_tuple(row, source_nonindex_columns)
            for _, row in source_group.iterrows()
        }
        cross_payload_columns = sorted(
            (
                set(_manifest_columns(source_group, include_index=False))
                | set(_manifest_columns(merged_group, include_index=False))
            )
            - {"embedding_l2_norm"}
        )
        source_cross_payload = Counter(
            _canonical_row_tuple(row, cross_payload_columns)
            for _, row in source_group.iterrows()
        )
        merged_cross_payload = Counter(
            _canonical_row_tuple(row, cross_payload_columns)
            for _, row in merged_group.iterrows()
        )
        extracted_complete, extracted_unique = _coverage_and_unique(
            [row["extracted_sha256"] for row in source_descriptors]
        )
        frames_complete, frames_unique = _coverage_and_unique(
            [row["frame_array_sha256"] for row in source_descriptors]
        )
        dicom_complete, dicom_unique = _coverage_and_unique(
            [row["dicom_sha256"] for row in source_descriptors]
        )
        evidence = GroupEvidence(
            source_count=len(source_group),
            merged_count=len(merged_group),
            source_full_rows_equal=len(full_rows) == 1,
            source_nonindex_rows_equal=len(nonindex_rows) == 1,
            source_vectors_exact_equal=source_exact,
            source_vectors_numerically_equal=source_close,
            merged_vectors_exact_equal=merged_exact,
            merged_vectors_numerically_equal=merged_close,
            source_merged_manifest_payload_equal_excluding_index_and_norm=(
                source_cross_payload == merged_cross_payload
            ),
            source_merged_vectors_exact_multiset_equal=cross_exact,
            source_merged_vectors_numerically_match=cross_close,
            extracted_hashes_complete=extracted_complete,
            n_unique_extracted_hashes=extracted_unique,
            frame_hashes_complete=frames_complete,
            n_unique_frame_hashes=frames_unique,
            dicom_hashes_complete=dicom_complete,
            n_unique_dicom_hashes=dicom_unique,
        )
        classification = classify_duplicate_group(evidence)
        scope = _group_scope(key, selected)
        components = sorted(set(source_group["_component"].astype(str))) or ["merged_only"]
        source_norms = _numeric_values(source_group, "embedding_l2_norm")
        merged_norms = _numeric_values(merged_group, "embedding_l2_norm")
        source_computed_norms = [float(np.linalg.norm(vector)) for vector in source_vectors]
        merged_computed_norms = [float(np.linalg.norm(vector)) for vector in merged_vectors]
        group_record: dict[str, Any] = {
            "subject_id": key[0] if key else "<MISSING>",
            "study_id": key[1] if len(key) > 1 else "<MISSING>",
            "clip_key": json.dumps(key, separators=(",", ":")),
            "components": ";".join(components),
            "selected_scope": scope,
            "classification": classification,
            "proposed_resolution": RESOLUTIONS[classification],
            "source_embedding_l2_norms_equal_within_tolerance": _numeric_sequence_close(
                source_norms,
                ([source_norms[0]] * len(source_norms)) if source_norms else None,
                rtol=vector_rtol,
                atol=vector_atol,
            ),
            "merged_embedding_l2_norms_equal_within_tolerance": _numeric_sequence_close(
                merged_norms,
                ([merged_norms[0]] * len(merged_norms)) if merged_norms else None,
                rtol=vector_rtol,
                atol=vector_atol,
            ),
            "source_merged_embedding_l2_norm_multisets_match": _numeric_sequence_close(
                source_norms,
                merged_norms,
                rtol=vector_rtol,
                atol=vector_atol,
            ),
            "source_manifest_norm_matches_computed_vectors": _numeric_sequence_close(
                source_norms,
                source_computed_norms,
                rtol=vector_rtol,
                atol=vector_atol,
            ),
            "merged_manifest_norm_matches_computed_vectors": _numeric_sequence_close(
                merged_norms,
                merged_computed_norms,
                rtol=vector_rtol,
                atol=vector_atol,
            ),
            **evidence.__dict__,
        }
        group_rows.append(group_record)
        for component in components:
            aggregate_memberships[
                (classification, component, scope, RESOLUTIONS[classification])
            ].add(key)

    aggregate_rows = [
        {
            "classification": classification,
            "component": component,
            "selected_scope": scope,
            "proposed_resolution": resolution,
            "n_unique_duplicate_groups": len(keys),
        }
        for (classification, component, scope, resolution), keys in sorted(
            aggregate_memberships.items()
        )
    ]
    return group_rows, restricted_rows, aggregate_rows


def _validate_aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        if set(row) != set(AGGREGATE_COLUMNS):
            raise ValueError("Unexpected aggregate duplicate-audit schema")
        if row["classification"] not in CLASSIFICATIONS:
            raise ValueError("Unexpected aggregate classification")
        if COMPONENT_RE.fullmatch(str(row["component"])) is None and row["component"] != "merged_only":
            raise ValueError("Unexpected aggregate component")
        if row["selected_scope"] not in {
            "selected",
            "outside_selected",
            "ownership_mismatch",
            "unknown",
        }:
            raise ValueError("Unexpected aggregate selected scope")
        if row["proposed_resolution"] != RESOLUTIONS[row["classification"]]:
            raise ValueError("Unexpected aggregate resolution")
        if not isinstance(row["n_unique_duplicate_groups"], int):
            raise ValueError("Aggregate count is not an integer")


def main() -> int:
    args = parse_args()
    if args.expected_component_count <= 0 or args.expected_duplicate_keys < 0:
        raise ValueError("Expected counts must be nonnegative and component count positive")
    if args.vector_rtol < 0 or args.vector_atol < 0:
        raise ValueError("Vector tolerances must be nonnegative")
    manifests = _named_paths(args.component_manifest, "manifest")
    embedding_paths = _named_paths(args.component_embedding_npz, "embedding")
    if set(manifests) != set(embedding_paths):
        raise ValueError("Component manifest and embedding labels differ")
    if len(manifests) != args.expected_component_count:
        raise ValueError("Component count differs from the prespecified count")
    for path in [
        *manifests.values(),
        *embedding_paths.values(),
        args.merged_manifest,
        args.merged_embedding_npz,
        args.selected_studies,
    ]:
        if not path.is_file():
            raise FileNotFoundError("Required duplicate-audit input is unavailable")

    component_success: dict[str, pd.DataFrame] = {}
    for name, path in sorted(manifests.items()):
        component_success[name] = _successful_clip_manifest(load_table(path))
    merged_success = _successful_clip_manifest(load_table(args.merged_manifest))
    all_frames = [merged_success, *component_success.values()]
    locators = tuple(
        column
        for column in CLIP_LOCATOR_COLUMNS
        if all(column in frame.columns for frame in all_frames)
    )
    if not locators:
        raise ValueError("No shared stable locator schema")

    prepared_components = {
        name: _prepare_clip_manifest_keys(frame, locators)
        for name, frame in sorted(component_success.items())
    }
    prepared_merged = _prepare_clip_manifest_keys(merged_success, locators)
    prepared_source = pd.concat(
        prepared_components.values(), ignore_index=True, sort=False
    )
    if (
        prepared_source["_audit_key_missing"].any()
        or prepared_merged["_audit_key_missing"].any()
    ):
        raise ValueError("Clip locator schema contains missing identifier fields")
    source_counts = Counter(prepared_source["_audit_clip_key"])
    merged_counts = Counter(prepared_merged["_audit_clip_key"])
    duplicate_keys = {
        key
        for key in set(source_counts) | set(merged_counts)
        if source_counts[key] > 1 or merged_counts[key] > 1
    }

    # Load the paired arrays for index validation, but retain vectors only for
    # the prespecified duplicate groups. This keeps the restricted diagnostic
    # lightweight instead of materializing ~192k Python vector objects.
    source_frames: list[pd.DataFrame] = []
    for name, prepared in prepared_components.items():
        duplicate_rows = prepared[prepared["_audit_clip_key"].isin(duplicate_keys)].copy()
        source_frames.append(
            _attach_vectors(
                duplicate_rows,
                _load_embeddings(embedding_paths[name], args.expected_embedding_dim),
                name,
            )
        )
    source = pd.concat(source_frames, ignore_index=True, sort=False)
    merged = _attach_vectors(
        prepared_merged[
            prepared_merged["_audit_clip_key"].isin(duplicate_keys)
        ].copy(),
        _load_embeddings(args.merged_embedding_npz, args.expected_embedding_dim),
        "merged",
    )

    selected = _selected_map(load_table(args.selected_studies))
    group_rows, restricted_rows, aggregate_rows = audit_duplicate_groups(
        source,
        merged,
        selected,
        vector_rtol=args.vector_rtol,
        vector_atol=args.vector_atol,
    )
    _validate_aggregate_rows(aggregate_rows)
    restricted_dir = require_restricted_path(args.restricted_output_dir)
    aggregate_dir = args.aggregate_output_dir.expanduser().resolve()
    restricted_resolved = restricted_dir.resolve()
    if (
        aggregate_dir == restricted_resolved
        or restricted_resolved in aggregate_dir.parents
        or aggregate_dir in restricted_resolved.parents
    ):
        raise ValueError("Aggregate and restricted outputs must be disjoint sibling trees")
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(group_rows).to_csv(
        restricted_dir / "duplicate_clip_groups_restricted.csv", index=False
    )
    pd.DataFrame(restricted_rows).to_csv(
        restricted_dir / "duplicate_clip_rows_restricted.csv", index=False
    )
    aggregate_frame = pd.DataFrame(aggregate_rows, columns=AGGREGATE_COLUMNS)
    write_aggregate_csv(
        aggregate_frame,
        aggregate_dir / "duplicate_clip_key_reason_counts.csv",
    )
    n_unresolved = sum(
        row["classification"] == "OTHER_UNRESOLVED" for row in group_rows
    )
    n_selected = sum(row["selected_scope"] == "selected" for row in group_rows)
    n_extracted_complete = sum(row["extracted_hashes_complete"] for row in group_rows)
    expected_count_matches = len(group_rows) == args.expected_duplicate_keys
    summary = {
        "audit": "duplicate_clip_key_adjudication",
        "status": (
            "AUDIT_COMPLETE_BLOCKING_DUPLICATES"
            if expected_count_matches
            else "BLOCKED_EXPECTED_DUPLICATE_COUNT_MISMATCH"
        ),
        "n_component_manifests": len(manifests),
        "n_duplicate_groups": len(group_rows),
        "n_expected_duplicate_groups": args.expected_duplicate_keys,
        "expected_duplicate_group_count_matches": expected_count_matches,
        "n_selected_duplicate_groups": n_selected,
        "all_duplicate_groups_selected": n_selected == len(group_rows),
        "n_unresolved_duplicate_groups": n_unresolved,
        "n_groups_with_complete_extracted_hashes": n_extracted_complete,
        "all_groups_have_complete_extracted_hashes": n_extracted_complete
        == len(group_rows),
        "embedding_dimension": args.expected_embedding_dim,
        "vector_rtol": args.vector_rtol,
        "vector_atol": args.vector_atol,
        "restricted_diagnostics_written": True,
        "identifier_values_emitted_to_aggregate": False,
        "model_fitting_performed": False,
        "test_performance_computed": False,
    }
    write_json(summary, aggregate_dir / "duplicate_clip_key_adjudication.summary.json")
    safety = {
        "n_aggregate_csv_rows_checked": len(aggregate_rows),
        "aggregate_schema_valid": True,
        "aggregate_vocabulary_valid": True,
        "identifier_values_emitted": False,
        "aggregate_safety_gate_passed": True,
    }
    write_json(safety, aggregate_dir / "duplicate_clip_key_safety_gate.json")
    print(json.dumps(summary, sort_keys=True))
    return 1 if group_rows else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
