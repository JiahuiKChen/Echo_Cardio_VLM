"""Restricted duplicate-key forensics for historical clip embeddings.

The classifier intentionally does not read target values, predictions, or errors.
Only input lineage, processed arrays, and frozen embedding vectors may determine a
classification. Row-level evidence is retained for restricted output; public
outputs are aggregate and path-free.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .safety import (
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    assert_export_safe_frame,
    git_root,
    is_within,
    require_columns,
    require_restricted_destination,
    sha256_file,
    sha256_json,
    write_json,
    write_safe_csv,
)


LEGITIMATE_DISTINCT_CLIPS = "LEGITIMATE_DISTINCT_CLIPS"
TRUE_DUPLICATE_MANIFEST_ROWS = "TRUE_DUPLICATE_MANIFEST_ROWS"
TRUE_DUPLICATE_EMBEDDING_ROWS = "TRUE_DUPLICATE_EMBEDDING_ROWS"
KEY_GRANULARITY_TOO_COARSE = "KEY_GRANULARITY_TOO_COARSE"
AMBIGUOUS_REQUIRES_AUTHOR_REVIEW = "AMBIGUOUS_REQUIRES_AUTHOR_REVIEW"
BLOCKED_DUPLICATE_SEMANTICS = "BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED"

CLASSIFICATIONS = (
    LEGITIMATE_DISTINCT_CLIPS,
    TRUE_DUPLICATE_MANIFEST_ROWS,
    TRUE_DUPLICATE_EMBEDDING_ROWS,
    KEY_GRANULARITY_TOO_COARSE,
    AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
)
VALID_DEDUP_CLASSES = {
    TRUE_DUPLICATE_MANIFEST_ROWS,
    TRUE_DUPLICATE_EMBEDDING_ROWS,
}
VALID_SPLITS = ("train", "val", "test")

NPZ_PATH_COLUMNS = ("npz_path", "output_path", "processed_npz_path")
FINE_DICOM_COLUMNS = (
    "sop_instance_uid",
    "source_dicom_path",
    "dicom_abs_path",
    "dicom_path",
    "dicom_filepath",
)
WINDOW_INDEX_COLUMNS = ("window_index", "window_idx", "clip_index", "clip_idx")
WINDOW_ID_COLUMNS = ("window_id", "clip_id")
WINDOW_START_COLUMNS = ("source_frame_start", "frame_start", "window_start")
WINDOW_END_COLUMNS = ("source_frame_end", "frame_end", "window_end")
WINDOW_SEQUENCE_COLUMNS = ("sampled_indices", "frame_indices", "source_frame_indices")


@dataclass(frozen=True)
class BatchArtifacts:
    extraction_manifest: Path
    embedding_manifest: Path
    embedding_npz: Path


@dataclass(frozen=True)
class DuplicateForensicsInputs:
    batches: Mapping[str, BatchArtifacts]
    split_map: Path
    target_cohorts: Mapping[str, Path]
    coarse_key_columns: Sequence[str] = ("study_id", "dicom_filepath")
    near_rtol: float = 1e-6
    near_atol: float = 1e-7


@dataclass
class DuplicateForensicsResult:
    status: str
    summary: dict[str, Any]
    restricted_rows: pd.DataFrame
    restricted_groups: pd.DataFrame
    restricted_study_changes: pd.DataFrame
    class_summary: pd.DataFrame
    batch_summary: pd.DataFrame
    split_summary: pd.DataFrame
    target_overlap_summary: pd.DataFrame
    embedding_change_summary: pd.DataFrame
    safe_provenance: dict[str, Any]


def _text(value: Any) -> str:
    if value is None or (not isinstance(value, (list, tuple, dict, np.ndarray)) and pd.isna(value)):
        return ""
    if isinstance(value, (np.integer, int)):
        return str(int(value))
    if isinstance(value, (np.floating, float)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _truthy(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.map(lambda value: _text(value).lower() in {"1", "true", "t", "yes", "y"})


def _resolved_path(value: Any, base: Path) -> str:
    raw = _text(value)
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _first_value(row: Mapping[str, Any], columns: Sequence[str]) -> tuple[str, str]:
    for column in columns:
        value = _text(row.get(column))
        if value:
            return column, value
    return "", ""


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _window_spec(row: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    spec: dict[str, str] = {}
    index_column, index_value = _first_value(row, WINDOW_INDEX_COLUMNS)
    id_column, id_value = _first_value(row, WINDOW_ID_COLUMNS)
    start_column, start_value = _first_value(row, WINDOW_START_COLUMNS)
    end_column, end_value = _first_value(row, WINDOW_END_COLUMNS)
    sequence_column, sequence_value = _first_value(row, WINDOW_SEQUENCE_COLUMNS)
    for column, value, canonical in (
        (index_column, index_value, "window_index"),
        (id_column, id_value, "window_id"),
        (start_column, start_value, "frame_start"),
        (end_column, end_value, "frame_end"),
        (sequence_column, sequence_value, "frame_indices"),
    ):
        if column and value:
            spec[canonical] = value
    complete = bool(
        "window_index" in spec
        or "window_id" in spec
        or "frame_indices" in spec
        or {"frame_start", "frame_end"}.issubset(spec)
    )
    if ("frame_start" in spec) != ("frame_end" in spec):
        complete = False
    return spec, complete


def _integer_from_spec(spec: Mapping[str, str], key: str) -> int | None:
    value = spec.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not number.is_integer():
        return None
    return int(number)


def _select_processed_array(
    archive: Mapping[str, np.ndarray], spec: Mapping[str, str]
) -> tuple[np.ndarray, str]:
    window_index = _integer_from_spec(spec, "window_index")
    if window_index is not None:
        for key in (f"frames_{window_index}", f"clip_{window_index}"):
            if key in archive:
                return np.asarray(archive[key]), key

    if "frames" not in archive:
        raise KeyError("processed NPZ has no frames array")
    frames = np.asarray(archive["frames"])
    if frames.ndim == 5:
        if window_index is None:
            if frames.shape[0] != 1:
                raise ValueError("multi-window frames array lacks an explicit window index")
            return frames[0], "frames[0]"
        if window_index < 0 or window_index >= frames.shape[0]:
            raise IndexError("window index is outside the processed frames array")
        return frames[window_index], f"frames[{window_index}]"
    if frames.ndim != 4:
        raise ValueError(f"processed frames must be 4D or 5D, found {frames.ndim}D")

    start = _integer_from_spec(spec, "frame_start")
    end = _integer_from_spec(spec, "frame_end")
    if start is not None or end is not None:
        if start is None or end is None or start < 0 or end <= start or end > frames.shape[0]:
            raise ValueError("invalid explicit frame window for processed frames")
        return frames[start:end], f"frames[{start}:{end}]"
    return frames, "frames"


def _inspect_processed_npz(path: Path, spec: Mapping[str, str]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "processed_npz_sha256": "",
        "processed_array_sha256": "",
        "processed_array_shape": "",
        "processed_array_dtype": "",
        "processed_array_selector": "",
        "npz_members_json": "",
        "npz_metadata_json": "",
        "sampled_indices_json": "",
        "processed_inspection_error": "",
    }
    if not path.exists() or not path.is_file():
        evidence["processed_inspection_error"] = "processed NPZ is missing"
        return evidence
    try:
        evidence["processed_npz_sha256"] = sha256_file(path)
        with np.load(path, allow_pickle=False) as archive:
            members = {
                key: {"shape": list(archive[key].shape), "dtype": str(archive[key].dtype)}
                for key in sorted(archive.files)
            }
            selected, selector = _select_processed_array(archive, spec)
            evidence["processed_array_sha256"] = _array_sha256(selected)
            evidence["processed_array_shape"] = json.dumps(list(selected.shape), separators=(",", ":"))
            evidence["processed_array_dtype"] = str(selected.dtype)
            evidence["processed_array_selector"] = selector
            evidence["npz_members_json"] = json.dumps(members, sort_keys=True, separators=(",", ":"))

            metadata: dict[str, Any] = {}
            for key in (
                "source_num_frames",
                "source_rows",
                "source_columns",
                "target_frames",
                "target_size",
            ):
                if key in archive and archive[key].size <= 16:
                    metadata[key] = np.asarray(archive[key]).reshape(-1).tolist()
            evidence["npz_metadata_json"] = json.dumps(metadata, sort_keys=True, separators=(",", ":"))

            if "sampled_indices" in archive:
                sampled = np.asarray(archive["sampled_indices"])
                index = _integer_from_spec(spec, "window_index")
                if sampled.ndim >= 2 and index is not None and 0 <= index < sampled.shape[0]:
                    sampled = sampled[index]
                if sampled.size <= 1024:
                    evidence["sampled_indices_json"] = json.dumps(
                        sampled.reshape(-1).tolist(), separators=(",", ":")
                    )
    except Exception as exc:  # Evidence must record, not conceal, unreadable provenance.
        evidence["processed_inspection_error"] = f"{type(exc).__name__}: {exc}"
    return evidence


def _load_target_sets(paths: Mapping[str, Path]) -> tuple[dict[str, set[str]], dict[str, Any]]:
    if set(paths) != {"lvot_vti", "tapse"}:
        raise ValueError("target cohorts must be exactly lvot_vti and tapse")
    target_sets: dict[str, set[str]] = {}
    provenance: dict[str, Any] = {}
    for target, path in sorted(paths.items()):
        header = pd.read_csv(path, nrows=0)
        require_columns(header, ["study_id"], f"{target} target cohort")
        # Deliberately read only membership; target values and model output are excluded.
        frame = pd.read_csv(path, usecols=["study_id"])
        target_sets[target] = {_text(value) for value in frame["study_id"] if _text(value)}
        provenance[target] = {
            "logical_role": f"target_cohort_{target}",
            "sha256": sha256_file(path),
            "row_count": int(len(frame)),
        }
    return target_sets, provenance


def _load_split_map(path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    header = pd.read_csv(path, nrows=0)
    require_columns(header, ["subject_id", "split"], "subject split map")
    frame = pd.read_csv(path, usecols=["subject_id", "split"])
    frame["_subject"] = frame["subject_id"].map(_text)
    frame["_split"] = frame["split"].map(lambda value: _text(value).lower())
    invalid = sorted(set(frame["_split"]) - set(VALID_SPLITS))
    conflicts = frame.groupby("_subject")["_split"].nunique()
    if invalid or int((conflicts > 1).sum()):
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "split map has invalid values or conflicting subject assignments",
        )
    mapping = frame.drop_duplicates("_subject").set_index("_subject")["_split"].to_dict()
    provenance = {
        "logical_role": "subject_split_map",
        "sha256": sha256_file(path),
        "row_count": int(len(frame)),
    }
    return mapping, provenance


def _match_extraction_row(
    manifest_row: Mapping[str, Any],
    extraction: pd.DataFrame,
    manifest_base: Path,
) -> tuple[dict[str, Any], int, bool]:
    row = dict(manifest_row)
    _, raw_npz = _first_value(row, NPZ_PATH_COLUMNS)
    resolved_npz = _resolved_path(raw_npz, manifest_base) if raw_npz else ""
    candidates = extraction
    if resolved_npz:
        exact = extraction[extraction["_resolved_npz_path"] == resolved_npz]
        if not exact.empty:
            candidates = exact
        else:
            candidates = extraction.iloc[0:0]
    if candidates.empty:
        study = _text(row.get("study_id"))
        dicom = _text(row.get("dicom_filepath"))
        fallback = extraction[extraction["_study"] == study]
        if dicom and "dicom_filepath" in fallback.columns:
            fallback = fallback[fallback["dicom_filepath"].map(_text) == dicom]
        candidates = fallback

    spec, _ = _window_spec(row)
    if len(candidates) > 1 and spec:
        narrowed = candidates
        for canonical, aliases in (
            ("window_index", WINDOW_INDEX_COLUMNS),
            ("window_id", WINDOW_ID_COLUMNS),
            ("frame_start", WINDOW_START_COLUMNS),
            ("frame_end", WINDOW_END_COLUMNS),
            ("frame_indices", WINDOW_SEQUENCE_COLUMNS),
        ):
            if canonical not in spec:
                continue
            available = next((column for column in aliases if column in narrowed.columns), None)
            if available is not None:
                narrowed = narrowed[narrowed[available].map(_text) == spec[canonical]]
        if not narrowed.empty:
            candidates = narrowed

    equivalent = False
    if len(candidates) > 1:
        relevant = [
            column
            for column in (
                "study_id",
                "subject_id",
                "dicom_filepath",
                *NPZ_PATH_COLUMNS,
                *WINDOW_INDEX_COLUMNS,
                *WINDOW_ID_COLUMNS,
                *WINDOW_START_COLUMNS,
                *WINDOW_END_COLUMNS,
                *WINDOW_SEQUENCE_COLUMNS,
            )
            if column in candidates.columns
        ]
        equivalent = len(candidates[relevant].fillna("").drop_duplicates()) == 1

    if len(candidates):
        source = candidates.iloc[0].to_dict()
        for key, value in source.items():
            if key.startswith("_"):
                continue
            if not _text(row.get(key)):
                row[key] = value
        if not resolved_npz:
            resolved_npz = _text(source.get("_resolved_npz_path"))
    row["_resolved_npz_path"] = resolved_npz
    return row, int(len(candidates)), bool(equivalent)


def _load_batch(
    name: str, artifacts: BatchArtifacts
) -> tuple[list[dict[str, Any]], dict[str, Any], pd.DataFrame]:
    for path in (
        artifacts.extraction_manifest,
        artifacts.embedding_manifest,
        artifacts.embedding_npz,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    extraction = pd.read_csv(artifacts.extraction_manifest)
    require_columns(extraction, ["study_id", "subject_id"], f"{name} extraction manifest")
    path_column = next((column for column in NPZ_PATH_COLUMNS if column in extraction.columns), None)
    if path_column is None:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            f"{name} extraction manifest has no processed NPZ path column",
        )
    if "write_ok" in extraction.columns:
        extraction = extraction[_truthy(extraction["write_ok"])].copy()
    extraction["_study"] = extraction["study_id"].map(_text)
    extraction["_resolved_npz_path"] = extraction[path_column].map(
        lambda value: _resolved_path(value, artifacts.extraction_manifest.parent)
    )

    manifest = pd.read_csv(artifacts.embedding_manifest)
    require_columns(
        manifest,
        ["study_id", "subject_id", "embedding_idx"],
        f"{name} embedding manifest",
    )
    if "write_ok" in manifest.columns:
        manifest = manifest[_truthy(manifest["write_ok"])].copy()
    manifest = manifest.reset_index(drop=False).rename(columns={"index": "_manifest_row"})
    if manifest.empty:
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, f"{name} has no successful embedding rows")
    numeric_indices = pd.to_numeric(manifest["embedding_idx"], errors="coerce")
    if numeric_indices.isna().any() or not np.all(numeric_indices == np.floor(numeric_indices)):
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, f"{name} has invalid embedding_idx values")
    manifest["embedding_idx"] = numeric_indices.astype(int)

    with np.load(artifacts.embedding_npz, allow_pickle=False) as archive:
        if "embeddings" not in archive:
            raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, f"{name} NPZ lacks embeddings")
        vectors = np.asarray(archive["embeddings"])
    if vectors.ndim != 2:
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, f"{name} embeddings must be two-dimensional")
    if int(manifest["embedding_idx"].min()) < 0 or int(manifest["embedding_idx"].max()) >= len(vectors):
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, f"{name} embedding_idx is out of bounds")

    rows: list[dict[str, Any]] = []
    for raw in manifest.to_dict(orient="records"):
        row = dict(raw)
        _, raw_npz = _first_value(row, NPZ_PATH_COLUMNS)
        row["_resolved_npz_path"] = (
            _resolved_path(raw_npz, artifacts.embedding_manifest.parent) if raw_npz else ""
        )
        vector = np.asarray(vectors[int(row["embedding_idx"])])
        vector_error = "" if np.isfinite(vector).all() else "embedding vector contains non-finite values"
        row.update(
            {
                "_batch": name,
                "_study": _text(row.get("study_id")),
                "_subject": _text(row.get("subject_id")),
                "_vector": vector,
                "embedding_vector_error": vector_error,
            }
        )
        rows.append(row)

    provenance = {
        "batch_name": name,
        "extraction_manifest_sha256": sha256_file(artifacts.extraction_manifest),
        "embedding_manifest_sha256": sha256_file(artifacts.embedding_manifest),
        "embedding_npz_sha256": sha256_file(artifacts.embedding_npz),
        "successful_embedding_rows": int(len(rows)),
        "embedding_dimension": int(vectors.shape[1]),
    }
    return rows, provenance, extraction


def _pairwise_vector_flags(
    rows: pd.DataFrame, near_rtol: float, near_atol: float
) -> dict[str, Any]:
    exact_pairs = 0
    near_pairs = 0
    near_nonexact_pairs = 0
    total_pairs = 0
    vectors = list(rows["_vector"])
    for left, right in combinations(vectors, 2):
        total_pairs += 1
        exact = bool(np.array_equal(left, right))
        near = bool(np.allclose(left, right, rtol=near_rtol, atol=near_atol, equal_nan=False))
        exact_pairs += int(exact)
        near_pairs += int(near)
        near_nonexact_pairs += int(near and not exact)
    return {
        "vector_pair_count": total_pairs,
        "exact_vector_pair_count": exact_pairs,
        "near_vector_pair_count": near_pairs,
        "near_nonexact_vector_pair_count": near_nonexact_pairs,
        "all_vectors_exact_equal": bool(total_pairs and exact_pairs == total_pairs),
        "all_vectors_near_equal": bool(total_pairs and near_pairs == total_pairs),
        "any_exact_vector_pair": bool(exact_pairs),
        "any_near_nonexact_vector_pair": bool(near_nonexact_pairs),
    }


def _fine_identity(group: pd.DataFrame, coarse_columns: Sequence[str]) -> tuple[str, list[str], bool]:
    partial = False
    for column in FINE_DICOM_COLUMNS:
        if column in coarse_columns or column not in group.columns:
            continue
        values = group[column].map(_text)
        nonempty = values[values != ""]
        if nonempty.empty:
            continue
        if len(nonempty) != len(group):
            partial = True
            continue
        unique = sorted(set(nonempty))
        if len(unique) > 1:
            return column, unique, partial
    return "", [], partial


def _classify_group(
    group: pd.DataFrame,
    coarse_columns: Sequence[str],
    near_rtol: float,
    near_atol: float,
) -> tuple[str, str, dict[str, Any]]:
    flags = _pairwise_vector_flags(group, near_rtol, near_atol)
    errors = [
        value
        for value in [
            *group["processed_inspection_error"].map(_text),
            *group["embedding_vector_error"].map(_text),
        ]
        if value
    ]
    zero_matches = int((group["extraction_match_count"] == 0).sum())
    unresolved_multi = int(
        ((group["extraction_match_count"] > 1) & ~group["extraction_matches_equivalent"]).sum()
    )
    fine_column, fine_values, partial_fine = _fine_identity(group, coarse_columns)
    processed = group["processed_array_sha256"].map(_text)
    all_processed = bool((processed != "").all())
    unique_processed = int(processed.nunique()) if all_processed else 0
    specs = group["explicit_window_spec_json"].map(_text)
    nonempty_specs = specs[specs != "{}"]
    all_specs_complete = bool(group["explicit_window_spec_complete"].all())
    unique_specs = int(nonempty_specs.nunique()) if len(nonempty_specs) == len(group) else 0
    batch_indices = {
        (str(row["_batch"]), int(row["embedding_idx"]))
        for _, row in group.iterrows()
    }

    flags.update(
        {
            "processed_input_hashes_complete": all_processed,
            "unique_processed_input_hashes": unique_processed,
            "unique_explicit_window_specs": unique_specs,
            "fine_identity_column": fine_column,
            "unique_fine_dicom_identities": len(fine_values),
            "valid_dedup_rule": False,
        }
    )

    if errors or zero_matches or unresolved_multi or partial_fine:
        detail = (
            "processed/vector provenance is missing or inconsistent "
            f"(inspection_errors={len(errors)}, no_extraction_match={zero_matches}, "
            f"unresolved_extraction_matches={unresolved_multi}, partial_fine_identity={partial_fine})"
        )
        return AMBIGUOUS_REQUIRES_AUTHOR_REVIEW, detail, flags

    if fine_column and len(fine_values) > 1:
        if all_processed and unique_processed > 1 and len(fine_values) == len(group):
            return (
                KEY_GRANULARITY_TOO_COARSE,
                f"coarse key combines distinct {fine_column} values with distinct processed inputs",
                flags,
            )
        return (
            AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
            "fine DICOM identities differ but do not map one-to-one to distinct processed inputs",
            flags,
        )

    if all_specs_complete and unique_specs > 1:
        if unique_specs == len(group) and all_processed and unique_processed == len(group):
            return (
                LEGITIMATE_DISTINCT_CLIPS,
                "distinct explicit clip/window specifications map one-to-one to distinct processed inputs",
                flags,
            )
        return (
            AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
            "explicit clip/window specifications conflict with processed-input identity",
            flags,
        )

    if not all_processed:
        return (
            AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
            "processed-input hashes are incomplete",
            flags,
        )

    if unique_processed == 1:
        if len(batch_indices) == 1:
            flags["valid_dedup_rule"] = True
            return (
                TRUE_DUPLICATE_MANIFEST_ROWS,
                "multiple manifest rows reference one batch embedding index and one processed input",
                flags,
            )
        if flags["all_vectors_near_equal"]:
            flags["valid_dedup_rule"] = True
            equality = "exactly equal" if flags["all_vectors_exact_equal"] else "near-equal"
            return (
                TRUE_DUPLICATE_EMBEDDING_ROWS,
                f"separate embedding indices for one processed input are {equality}",
                flags,
            )
        return (
            AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
            "one processed input maps to materially different embedding vectors",
            flags,
        )

    return (
        AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
        "distinct processed inputs lack complete explicit clip/window or finer DICOM provenance",
        flags,
    )


def _target_overlap(study_ids: set[str], target_sets: Mapping[str, set[str]]) -> str:
    lvot = bool(study_ids & target_sets["lvot_vti"])
    tapse = bool(study_ids & target_sets["tapse"])
    if lvot and tapse:
        return "both_targets"
    if lvot:
        return "lvot_vti_only"
    if tapse:
        return "tapse_only"
    return "neither_target"


def _safe_class_summary(groups: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for classification in CLASSIFICATIONS:
        selected_groups = groups[groups["classification"] == classification]
        tokens = set(selected_groups["group_token"])
        selected_rows = rows[rows["group_token"].isin(tokens)]
        output.append(
            {
                "classification": classification,
                "n_groups": int(len(selected_groups)),
                "n_rows": int(len(selected_rows)),
                "n_studies": int(selected_rows["_study"].nunique()),
                "n_subjects": int(selected_rows["_subject"].nunique()),
                "n_groups_with_any_exact_vector_pair": int(
                    selected_groups["any_exact_vector_pair"].sum()
                ),
                "n_groups_with_near_nonexact_vector_pair": int(
                    selected_groups["any_near_nonexact_vector_pair"].sum()
                ),
                "n_groups_with_valid_dedup_rule": int(selected_groups["valid_dedup_rule"].sum()),
            }
        )
    return pd.DataFrame(output)


def _safe_dimension_summary(
    groups: pd.DataFrame,
    rows: pd.DataFrame,
    dimension: str,
    values: Sequence[str] | None = None,
) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    observed = list(values or sorted(set(rows[dimension].map(_text))))
    for value in observed:
        dimension_rows = rows[rows[dimension].map(_text) == value]
        for classification in CLASSIFICATIONS:
            selected = dimension_rows[dimension_rows["classification"] == classification]
            output.append(
                {
                    dimension.lstrip("_"): value,
                    "classification": classification,
                    "n_groups": int(selected["group_token"].nunique()),
                    "n_rows": int(len(selected)),
                    "n_studies": int(selected["_study"].nunique()),
                    "n_subjects": int(selected["_subject"].nunique()),
                }
            )
    return pd.DataFrame(output)


def _safe_overlap_summary(groups: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    categories = ("lvot_vti_only", "tapse_only", "both_targets", "neither_target")
    output: list[dict[str, Any]] = []
    for category in categories:
        category_groups = groups[groups["target_overlap"] == category]
        for classification in CLASSIFICATIONS:
            selected_groups = category_groups[category_groups["classification"] == classification]
            selected_rows = rows[rows["group_token"].isin(set(selected_groups["group_token"]))]
            output.append(
                {
                    "target_overlap": category,
                    "classification": classification,
                    "n_groups": int(len(selected_groups)),
                    "n_rows": int(len(selected_rows)),
                    "n_studies": int(selected_rows["_study"].nunique()),
                    "n_subjects": int(selected_rows["_subject"].nunique()),
                }
            )
    return pd.DataFrame(output)


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0:
        return 1.0 if np.array_equal(left, right) else float("nan")
    return float(np.dot(left, right) / denominator)


def _embedding_changes(
    all_rows: pd.DataFrame,
    repeated_rows: pd.DataFrame,
    groups: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    keep = pd.Series(True, index=all_rows.index, dtype=bool)
    duplicate_groups = groups[groups["classification"].isin(VALID_DEDUP_CLASSES)]
    affected_studies: set[str] = set()
    for token in duplicate_groups["group_token"]:
        indexes = list(repeated_rows.index[repeated_rows["group_token"] == token])
        ordered = sorted(
            indexes,
            key=lambda index: (
                str(all_rows.at[index, "_batch"]),
                int(all_rows.at[index, "_manifest_row"]),
                int(all_rows.at[index, "embedding_idx"]),
            ),
        )
        for index in ordered[1:]:
            keep.at[index] = False
        affected_studies.update(all_rows.loc[ordered, "_study"].map(_text))

    change_columns = [
        "study_id",
        "subject_id",
        "split",
        "intersects_lvot_vti",
        "intersects_tapse",
        "historical_contribution_count",
        "deduplicated_contribution_count",
        "embedding_changed",
        "l2_distance",
        "cosine_similarity",
        "max_absolute_component_difference",
    ]
    change_rows: list[dict[str, Any]] = []
    for study in sorted(affected_studies):
        study_rows = all_rows[all_rows["_study"] == study]
        historical = np.stack(list(study_rows["_vector"]), axis=0).mean(axis=0)
        corrected_rows = study_rows[keep.loc[study_rows.index]]
        corrected = np.stack(list(corrected_rows["_vector"]), axis=0).mean(axis=0)
        delta = corrected - historical
        subjects = sorted(set(study_rows["_subject"].map(_text)))
        splits = sorted(set(study_rows["split"].map(_text)))
        change_rows.append(
            {
                "study_id": study,
                "subject_id": subjects[0] if len(subjects) == 1 else "",
                "split": splits[0] if len(splits) == 1 else "",
                "intersects_lvot_vti": bool(study_rows["intersects_lvot_vti"].any()),
                "intersects_tapse": bool(study_rows["intersects_tapse"].any()),
                "historical_contribution_count": int(len(study_rows)),
                "deduplicated_contribution_count": int(len(corrected_rows)),
                "embedding_changed": bool(not np.array_equal(historical, corrected)),
                "l2_distance": float(np.linalg.norm(delta)),
                "cosine_similarity": _cosine(historical, corrected),
                "max_absolute_component_difference": float(np.max(np.abs(delta))),
            }
        )
    changes = pd.DataFrame(change_rows, columns=change_columns)
    columns = [
        "metric",
        "n_studies_evaluated",
        "n_studies_changed",
        "minimum",
        "p25",
        "median",
        "p75",
        "maximum",
        "mean",
    ]
    if changes.empty:
        return changes, pd.DataFrame(columns=columns), keep

    summary_rows: list[dict[str, Any]] = []
    for metric in ("l2_distance", "cosine_similarity", "max_absolute_component_difference"):
        values = pd.to_numeric(changes[metric], errors="coerce").dropna()
        summary_rows.append(
            {
                "metric": metric,
                "n_studies_evaluated": int(len(changes)),
                "n_studies_changed": int(changes["embedding_changed"].sum()),
                "minimum": float(values.min()) if len(values) else np.nan,
                "p25": float(values.quantile(0.25)) if len(values) else np.nan,
                "median": float(values.median()) if len(values) else np.nan,
                "p75": float(values.quantile(0.75)) if len(values) else np.nan,
                "maximum": float(values.max()) if len(values) else np.nan,
                "mean": float(values.mean()) if len(values) else np.nan,
            }
        )
    return changes, pd.DataFrame(summary_rows, columns=columns), keep


def analyze_duplicate_keys(inputs: DuplicateForensicsInputs) -> DuplicateForensicsResult:
    if not inputs.batches:
        raise ValueError("at least one batch is required")
    if inputs.near_rtol < 0 or inputs.near_atol < 0:
        raise ValueError("near-equality tolerances must be nonnegative")
    coarse_columns = tuple(inputs.coarse_key_columns)
    if "study_id" not in coarse_columns:
        raise ValueError("coarse key must include study_id")
    prohibited_key_tokens = {
        "target",
        "label",
        "y_true",
        "y_pred",
        "prediction",
        "residual",
        "error",
        "mae",
        "rmse",
        "r2",
    }
    unsafe_coarse_columns = [
        column
        for column in coarse_columns
        if any(token in str(column).lower() for token in prohibited_key_tokens)
    ]
    if unsafe_coarse_columns:
        raise ValueError(
            "coarse key cannot use targets, labels, predictions, or errors: "
            f"{unsafe_coarse_columns}"
        )

    split_map, split_provenance = _load_split_map(inputs.split_map)
    target_sets, target_provenance = _load_target_sets(inputs.target_cohorts)
    all_records: list[dict[str, Any]] = []
    batch_provenance: list[dict[str, Any]] = []
    batch_extractions: dict[str, pd.DataFrame] = {}
    embedding_dimensions: set[int] = set()
    for name, artifacts in sorted(inputs.batches.items()):
        allowed_name_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        if not name or any(character not in allowed_name_characters for character in name):
            raise ValueError(f"batch name must be path-free: {name!r}")
        rows, provenance, extraction = _load_batch(name, artifacts)
        all_records.extend(rows)
        batch_provenance.append(provenance)
        batch_extractions[name] = extraction
        embedding_dimensions.add(int(provenance["embedding_dimension"]))
    if len(embedding_dimensions) != 1:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "embedding dimensions differ across supplied batches",
        )

    all_rows = pd.DataFrame(all_records)
    require_columns(all_rows, coarse_columns, "combined embedding manifests")
    all_rows["_coarse_key"] = all_rows.apply(
        lambda row: tuple(_text(row[column]) for column in coarse_columns), axis=1
    )
    if any(any(not value for value in key) for key in all_rows["_coarse_key"]):
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, "coarse duplicate key contains missing values")
    if all_rows["_study"].eq("").any() or all_rows["_subject"].eq("").any():
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, "study or subject provenance is missing")
    if all_rows["embedding_vector_error"].map(_text).ne("").any():
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "one or more frozen embedding vectors are non-finite",
        )

    all_rows["split"] = all_rows["_subject"].map(split_map).fillna("")
    if all_rows["split"].eq("").any():
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, "one or more subjects are absent from split map")
    all_rows["intersects_lvot_vti"] = all_rows["_study"].isin(target_sets["lvot_vti"])
    all_rows["intersects_tapse"] = all_rows["_study"].isin(target_sets["tapse"])

    counts = all_rows["_coarse_key"].value_counts()
    repeated_keys = set(counts[counts > 1].index)
    repeated_rows = all_rows[all_rows["_coarse_key"].isin(repeated_keys)].copy()

    empty_npz_evidence = {
        "processed_npz_sha256": "",
        "processed_array_sha256": "",
        "processed_array_shape": "",
        "processed_array_dtype": "",
        "processed_array_selector": "",
        "npz_members_json": "",
        "npz_metadata_json": "",
        "sampled_indices_json": "",
        "processed_inspection_error": "processed NPZ provenance is missing",
    }
    for column, default in {
        **empty_npz_evidence,
        "embedding_vector_sha256": "",
        "embedding_vector_shape": "",
        "explicit_window_spec_json": "{}",
        "explicit_window_spec_complete": False,
        "extraction_match_count": 0,
        "extraction_matches_equivalent": False,
    }.items():
        repeated_rows[column] = default

    inspection_cache: dict[tuple[str, str], dict[str, Any]] = {}
    enrichment_columns = set(
        (
            *FINE_DICOM_COLUMNS,
            *NPZ_PATH_COLUMNS,
            *WINDOW_INDEX_COLUMNS,
            *WINDOW_ID_COLUMNS,
            *WINDOW_START_COLUMNS,
            *WINDOW_END_COLUMNS,
            *WINDOW_SEQUENCE_COLUMNS,
        )
    )
    for index, source_row in repeated_rows.iterrows():
        batch = str(source_row["_batch"])
        enriched, extraction_matches, equivalent_matches = _match_extraction_row(
            source_row.to_dict(),
            batch_extractions[batch],
            inputs.batches[batch].embedding_manifest.parent,
        )
        for column in enrichment_columns:
            if column in enriched:
                repeated_rows.at[index, column] = enriched[column]
        repeated_rows.at[index, "_resolved_npz_path"] = enriched["_resolved_npz_path"]
        spec, spec_complete = _window_spec(enriched)
        spec_json = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        npz_path = Path(enriched["_resolved_npz_path"]) if enriched["_resolved_npz_path"] else None
        cache_key = (str(npz_path) if npz_path is not None else "", spec_json)
        if cache_key not in inspection_cache:
            inspection_cache[cache_key] = (
                _inspect_processed_npz(npz_path, spec)
                if npz_path is not None
                else dict(empty_npz_evidence)
            )
        for column, value in inspection_cache[cache_key].items():
            repeated_rows.at[index, column] = value
        vector = source_row["_vector"]
        repeated_rows.at[index, "embedding_vector_sha256"] = _array_sha256(vector)
        repeated_rows.at[index, "embedding_vector_shape"] = json.dumps(
            list(vector.shape), separators=(",", ":")
        )
        repeated_rows.at[index, "explicit_window_spec_json"] = spec_json
        repeated_rows.at[index, "explicit_window_spec_complete"] = bool(spec_complete)
        repeated_rows.at[index, "extraction_match_count"] = extraction_matches
        repeated_rows.at[index, "extraction_matches_equivalent"] = equivalent_matches

    group_records: list[dict[str, Any]] = []
    for coarse_key, group in repeated_rows.groupby("_coarse_key", sort=True):
        token = sha256_json({"coarse_key_columns": coarse_columns, "coarse_key": coarse_key})
        classification, reason, flags = _classify_group(
            group, coarse_columns, inputs.near_rtol, inputs.near_atol
        )
        studies = set(group["_study"].map(_text))
        subjects = set(group["_subject"].map(_text))
        splits = set(group["split"].map(_text))
        if len(studies) != 1 or len(subjects) != 1 or len(splits) != 1:
            classification = AMBIGUOUS_REQUIRES_AUTHOR_REVIEW
            reason = "repeated coarse group spans conflicting study, subject, or split provenance"
            flags["valid_dedup_rule"] = False
        overlap = _target_overlap(studies, target_sets)
        record = {
            "group_token": token,
            "classification": classification,
            "classification_reason": reason,
            "coarse_key_json": json.dumps(
                dict(zip(coarse_columns, coarse_key)), sort_keys=True, separators=(",", ":")
            ),
            "n_rows": int(len(group)),
            "n_studies": int(len(studies)),
            "n_subjects": int(len(subjects)),
            "source_batches_json": json.dumps(sorted(set(group["_batch"])), separators=(",", ":")),
            "split": next(iter(splits)) if len(splits) == 1 else "",
            "target_overlap": overlap,
            "intersects_lvot_vti": bool(studies & target_sets["lvot_vti"]),
            "intersects_tapse": bool(studies & target_sets["tapse"]),
            "historical_contribution_count": int(len(group)),
            "canonical_contribution_count_under_rule": 1 if flags["valid_dedup_rule"] else np.nan,
            **flags,
        }
        group_records.append(record)
        repeated_rows.loc[group.index, "group_token"] = token
        repeated_rows.loc[group.index, "classification"] = classification
        repeated_rows.loc[group.index, "classification_reason"] = reason

    groups = pd.DataFrame(group_records)
    if groups.empty:
        groups = pd.DataFrame(
            columns=[
                "group_token",
                "classification",
                "classification_reason",
                "coarse_key_json",
                "n_rows",
                "n_studies",
                "n_subjects",
                "source_batches_json",
                "split",
                "target_overlap",
                "intersects_lvot_vti",
                "intersects_tapse",
                "valid_dedup_rule",
                "any_exact_vector_pair",
                "any_near_nonexact_vector_pair",
            ]
        )
        repeated_rows["group_token"] = pd.Series(dtype=str)
        repeated_rows["classification"] = pd.Series(dtype=str)
        repeated_rows["classification_reason"] = pd.Series(dtype=str)

    ambiguous = int((groups["classification"] == AMBIGUOUS_REQUIRES_AUTHOR_REVIEW).sum())
    status = BLOCKED_DUPLICATE_SEMANTICS if ambiguous else "ok"
    if ambiguous:
        study_changes = pd.DataFrame(
            columns=[
                "study_id",
                "subject_id",
                "split",
                "intersects_lvot_vti",
                "intersects_tapse",
                "historical_contribution_count",
                "deduplicated_contribution_count",
                "embedding_changed",
                "l2_distance",
                "cosine_similarity",
                "max_absolute_component_difference",
            ]
        )
        embedding_change_summary = pd.DataFrame(
            columns=[
                "metric",
                "n_studies_evaluated",
                "n_studies_changed",
                "minimum",
                "p25",
                "median",
                "p75",
                "maximum",
                "mean",
            ]
        )
        all_rows["dedup_keep_candidate"] = pd.NA
    else:
        study_changes, embedding_change_summary, keep = _embedding_changes(
            all_rows, repeated_rows, groups
        )
        all_rows["dedup_keep_candidate"] = keep
        repeated_rows["dedup_keep_candidate"] = keep.loc[repeated_rows.index]

    historical_counts = all_rows.groupby("_study").size().to_dict()
    repeated_rows["historical_study_contribution_count"] = repeated_rows["_study"].map(historical_counts)
    repeated_rows["same_physical_processed_clip"] = repeated_rows.groupby("group_token")[
        "processed_array_sha256"
    ].transform(lambda values: bool((values.map(_text) != "").all() and values.nunique() == 1))
    repeated_rows["group_row_count"] = repeated_rows.groupby("group_token")["group_token"].transform("size")

    for _, group in repeated_rows.groupby("group_token"):
        for index, row in group.iterrows():
            exact_peers = 0
            near_peers = 0
            for other_index, other in group.iterrows():
                if index == other_index:
                    continue
                exact = np.array_equal(row["_vector"], other["_vector"])
                near = np.allclose(
                    row["_vector"],
                    other["_vector"],
                    rtol=inputs.near_rtol,
                    atol=inputs.near_atol,
                    equal_nan=False,
                )
                exact_peers += int(exact)
                near_peers += int(near)
            repeated_rows.at[index, "exact_vector_peer_count"] = exact_peers
            repeated_rows.at[index, "near_vector_peer_count"] = near_peers

    class_summary = _safe_class_summary(groups, repeated_rows)
    batch_summary = _safe_dimension_summary(
        groups, repeated_rows, "_batch", values=sorted(inputs.batches)
    )
    split_summary = _safe_dimension_summary(groups, repeated_rows, "split", values=VALID_SPLITS)
    target_overlap_summary = _safe_overlap_summary(groups, repeated_rows)

    exact_groups = int(groups["any_exact_vector_pair"].sum()) if len(groups) else 0
    near_nonexact_groups = int(groups["any_near_nonexact_vector_pair"].sum()) if len(groups) else 0
    duplicate_groups = int(groups["classification"].isin(VALID_DEDUP_CLASSES).sum()) if len(groups) else 0
    n_changed = int(study_changes["embedding_changed"].sum()) if len(study_changes) else 0
    summary = {
        "status": status,
        "classification_scope": "input_lineage_processed_arrays_and_frozen_embeddings_only",
        "target_values_predictions_and_errors_used_for_classification": False,
        "coarse_key_columns": list(coarse_columns),
        "near_equality_rtol": float(inputs.near_rtol),
        "near_equality_atol": float(inputs.near_atol),
        "n_batches": int(len(inputs.batches)),
        "n_successful_embedding_rows_inspected": int(len(all_rows)),
        "n_processed_npz_inputs_inspected": int(len(inspection_cache)),
        "n_repeated_coarse_groups": int(len(groups)),
        "n_affected_rows": int(len(repeated_rows)),
        "n_affected_studies": int(repeated_rows["_study"].nunique()),
        "n_affected_subjects": int(repeated_rows["_subject"].nunique()),
        "n_ambiguous_groups": ambiguous,
        "n_groups_with_valid_dedup_rule": duplicate_groups,
        "n_groups_with_any_exact_vector_pair": exact_groups,
        "exact_vector_pair_group_frequency": float(exact_groups / len(groups)) if len(groups) else 0.0,
        "n_groups_with_near_nonexact_vector_pair": near_nonexact_groups,
        "near_nonexact_vector_pair_group_frequency": (
            float(near_nonexact_groups / len(groups)) if len(groups) else 0.0
        ),
        "n_studies_evaluated_for_embedding_change": int(len(study_changes)),
        "n_studies_whose_embedding_changes_under_valid_rule": n_changed,
        "embedding_change_status": (
            "withheld_due_to_ambiguity"
            if ambiguous
            else "computed" if duplicate_groups else "not_applicable_no_true_duplicates"
        ),
        "deduplication_rule": (
            "within each adjudicated duplicate coarse group, retain the stable first "
            "batch/manifest-row/embedding-index contribution"
            if duplicate_groups and not ambiguous
            else None
        ),
        "classification_counts": {
            row["classification"]: int(row["n_groups"])
            for row in class_summary.to_dict(orient="records")
        },
        "target_overlap_group_counts": {
            category: int((groups["target_overlap"] == category).sum())
            for category in ("lvot_vti_only", "tapse_only", "both_targets", "neither_target")
        },
        "n_groups_intersecting_lvot_vti": int(groups["intersects_lvot_vti"].sum()),
        "n_groups_intersecting_tapse": int(groups["intersects_tapse"].sum()),
        "n_groups_intersecting_neither_target": int(
            (~groups["intersects_lvot_vti"] & ~groups["intersects_tapse"]).sum()
        ),
    }
    safe_provenance = {
        "split_map": split_provenance,
        "target_cohorts": target_provenance,
        "batches": batch_provenance,
    }

    restricted_columns = [
        "group_token",
        "classification",
        "classification_reason",
        "_batch",
        "_manifest_row",
        "study_id",
        "subject_id",
        "split",
        "intersects_lvot_vti",
        "intersects_tapse",
        *[column for column in coarse_columns if column not in {"study_id"}],
        *[column for column in FINE_DICOM_COLUMNS if column in repeated_rows.columns],
        *[column for column in NPZ_PATH_COLUMNS if column in repeated_rows.columns],
        "_resolved_npz_path",
        "embedding_idx",
        "explicit_window_spec_json",
        "explicit_window_spec_complete",
        "extraction_match_count",
        "extraction_matches_equivalent",
        "processed_npz_sha256",
        "processed_array_sha256",
        "processed_array_shape",
        "processed_array_dtype",
        "processed_array_selector",
        "npz_members_json",
        "npz_metadata_json",
        "sampled_indices_json",
        "processed_inspection_error",
        "embedding_vector_sha256",
        "embedding_vector_shape",
        "embedding_vector_error",
        "exact_vector_peer_count",
        "near_vector_peer_count",
        "same_physical_processed_clip",
        "group_row_count",
        "historical_study_contribution_count",
        "dedup_keep_candidate",
    ]
    restricted_columns = list(
        dict.fromkeys(column for column in restricted_columns if column in repeated_rows.columns)
    )
    restricted_rows = repeated_rows[restricted_columns].copy()

    return DuplicateForensicsResult(
        status=status,
        summary=summary,
        restricted_rows=restricted_rows,
        restricted_groups=groups,
        restricted_study_changes=study_changes,
        class_summary=class_summary,
        batch_summary=batch_summary,
        split_summary=split_summary,
        target_overlap_summary=target_overlap_summary,
        embedding_change_summary=embedding_change_summary,
        safe_provenance=safe_provenance,
    )


def _require_output_destinations(
    restricted_output_dir: Path, safe_output_dir: Path, worktree: Path | None = None
) -> tuple[Path, Path]:
    restricted = require_restricted_destination(restricted_output_dir, worktree=worktree)
    safe = safe_output_dir.expanduser()
    if not safe.is_absolute():
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "aggregate-safe destination must be absolute")
    safe = safe.resolve()
    if is_within(safe, restricted) or is_within(restricted, safe):
        raise Tier1BlockedError(
            BLOCKED_UNSAFE_OUTPUT,
            "restricted and aggregate-safe destinations must be disjoint",
        )
    root = worktree.resolve() if worktree is not None else git_root()
    if root is not None and is_within(restricted, root):
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "restricted output is inside the Git worktree")
    if root is not None and is_within(safe, root):
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "aggregate-safe output is inside the Git worktree")
    return restricted, safe


def _assert_safe_payload(payload: Any, context: str = "root") -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            lower = str(key).lower()
            if lower in {
                "study_id",
                "subject_id",
                "dicom_id",
                "dicom_filepath",
                "npz_path",
                "target_value",
                "y_true",
                "y_pred",
                "prediction",
                "residual",
            } or lower.endswith("_path"):
                raise Tier1BlockedError(
                    BLOCKED_UNSAFE_OUTPUT, f"unsafe key in aggregate payload at {context}: {key}"
                )
            _assert_safe_payload(value, f"{context}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            _assert_safe_payload(value, f"{context}[{index}]")
    elif isinstance(payload, str):
        expanded = Path(payload).expanduser()
        if payload.startswith(("/", "~/")) or expanded.is_absolute():
            raise Tier1BlockedError(
                BLOCKED_UNSAFE_OUTPUT, f"absolute path in aggregate payload at {context}"
            )


def write_duplicate_forensics_outputs(
    result: DuplicateForensicsResult,
    restricted_output_dir: Path,
    safe_output_dir: Path,
    worktree: Path | None = None,
) -> None:
    restricted, safe = _require_output_destinations(
        restricted_output_dir, safe_output_dir, worktree=worktree
    )
    for frame, label in (
        (result.class_summary, "duplicate class summary"),
        (result.batch_summary, "duplicate batch summary"),
        (result.split_summary, "duplicate split summary"),
        (result.target_overlap_summary, "duplicate target-overlap summary"),
        (result.embedding_change_summary, "embedding-change summary"),
    ):
        assert_export_safe_frame(frame, label)
    safe_summary = dict(result.summary)
    coarse_key_columns = safe_summary.pop("coarse_key_columns", [])
    safe_summary.pop("target_values_predictions_and_errors_used_for_classification", None)
    safe_summary["coarse_key_component_count"] = int(len(coarse_key_columns))
    safe_summary["classification_uses_input_lineage_and_frozen_embeddings_only"] = True
    safe_payload = {**safe_summary, "input_provenance": result.safe_provenance}
    _assert_safe_payload(safe_payload)

    restricted_files = (
        "duplicate_forensics_rows.csv",
        "duplicate_forensics_groups.csv",
        "duplicate_embedding_change_by_study.csv",
        "duplicate_forensics_restricted_summary.json",
    )
    safe_files = (
        "duplicate_class_summary.csv",
        "duplicate_batch_summary.csv",
        "duplicate_split_summary.csv",
        "duplicate_target_overlap_summary.csv",
        "duplicate_embedding_change_summary.csv",
        "duplicate_forensics_summary.json",
    )
    collisions = [
        path.name
        for path in (
            *(restricted / name for name in restricted_files),
            *(safe / name for name in safe_files),
        )
        if path.exists()
    ]
    if collisions:
        raise Tier1BlockedError(
            BLOCKED_UNSAFE_OUTPUT,
            f"forensic output files already exist and will not be overwritten: {sorted(collisions)}",
        )

    restricted.mkdir(parents=True, exist_ok=True)
    result.restricted_rows.to_csv(restricted / "duplicate_forensics_rows.csv", index=False)
    result.restricted_groups.to_csv(restricted / "duplicate_forensics_groups.csv", index=False)
    result.restricted_study_changes.to_csv(
        restricted / "duplicate_embedding_change_by_study.csv", index=False
    )
    write_json(
        restricted / "duplicate_forensics_restricted_summary.json",
        {
            **result.summary,
            "restricted_artifacts": [
                "duplicate_forensics_rows.csv",
                "duplicate_forensics_groups.csv",
                "duplicate_embedding_change_by_study.csv",
            ],
        },
    )

    safe.mkdir(parents=True, exist_ok=True)
    write_safe_csv(safe / "duplicate_class_summary.csv", result.class_summary, "class summary")
    write_safe_csv(safe / "duplicate_batch_summary.csv", result.batch_summary, "batch summary")
    write_safe_csv(safe / "duplicate_split_summary.csv", result.split_summary, "split summary")
    write_safe_csv(
        safe / "duplicate_target_overlap_summary.csv",
        result.target_overlap_summary,
        "target-overlap summary",
    )
    write_safe_csv(
        safe / "duplicate_embedding_change_summary.csv",
        result.embedding_change_summary,
        "embedding-change summary",
    )
    write_json(safe / "duplicate_forensics_summary.json", safe_payload)

    if result.status == BLOCKED_DUPLICATE_SEMANTICS:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            f"{result.summary['n_ambiguous_groups']} repeated group(s) require author review",
        )
