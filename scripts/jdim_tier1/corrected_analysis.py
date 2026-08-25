"""Fail-closed duplicate correction and original-versus-corrected comparison.

Row-level manifests, embeddings, and predictions handled here are restricted.
Only explicitly aggregate outputs are suitable for export.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    assert_export_safe_frame,
    require_columns,
    require_restricted_destination,
    restricted_file_record,
    safe_file_record,
    sha256_file,
)


BLOCKED_DUPLICATE_SEMANTICS = "BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED"
BLOCKED_CANONICAL_IDENTITY = "BLOCKED_CANONICAL_CLIP_IDENTITY"
BLOCKED_CORRECTED_ANALYSIS = "BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION"
CORRECTION_NOT_REQUIRED = "CORRECTED_AGGREGATION_NOT_REQUIRED"

VALID_SPLITS = {"train", "val", "test"}
TRUE_DUPLICATE_CLASSES = {
    "TRUE_DUPLICATE_MANIFEST_ROWS",
    "TRUE_DUPLICATE_EMBEDDING_ROWS",
}
NONDUPLICATE_CLASSES = {"LEGITIMATE_DISTINCT_CLIPS", "KEY_GRANULARITY_TOO_COARSE"}
BLOCKING_CLASSES = {"AMBIGUOUS_REQUIRES_AUTHOR_REVIEW"}
VALID_CLASSES = TRUE_DUPLICATE_CLASSES | NONDUPLICATE_CLASSES | BLOCKING_CLASSES

PATH_IDENTITY_COLUMNS = (
    "sop_instance_uid",
    "dicom_abs_path",
    "source_dicom_path",
    "dicom_path",
    "dicom_filepath",
    "processed_npz_path",
    "output_path",
    "npz_path",
)
WINDOW_IDENTITY_COLUMNS = (
    "window_index",
    "window_idx",
    "clip_index",
    "clip_idx",
    "window_id",
    "clip_id",
    "source_frame_start",
    "frame_start",
    "window_start",
    "source_frame_end",
    "frame_end",
    "window_end",
    "sampled_indices",
    "frame_indices",
    "source_frame_indices",
)

SUPPORTED_AGGREGATE_TABLES: dict[str, tuple[str, ...]] = {
    "imaging_baseline_metrics.csv": ("target", "split", "model"),
    "imaging_baseline_binary_metrics.csv": (
        "target",
        "split",
        "model",
        "threshold_label",
        "threshold_value",
    ),
    "imaging_baseline_ridge_alpha_selection.csv": ("target", "alpha"),
    "imaging_baseline_bootstrap_ci.csv": (
        "target",
        "model",
        "split",
        "bootstrap_unit",
        "metric",
    ),
    "test_correlation_metrics.csv": ("target", "model_name"),
    "continuous_calibration_metrics.csv": ("target", "model_name"),
    "training_tertile_test_error.csv": ("target", "model_name", "range_group"),
    "paired_delta_mae.csv": ("target", "comparator"),
}
REVIEWER_SUPPORT_FILES = (
    "training_tertile_boundaries.json",
    "fixed_prediction_metrics_provenance.json",
)

RUN_PROTOCOL_FIELDS = (
    "target",
    "targets",
    "clinical_units",
    "analysis_label",
    "ridge_solver",
    "features_standardized",
    "ridge_alphas",
    "n_bootstrap",
    "bootstrap_unit",
    "random_seed",
    "legacy_seed_arg",
    "hard_extremes_excluded",
    "sklearn_version",
)

TARGET_PROTOCOL_FIELDS = (
    "target",
    "clinical_unit",
    "numeric_rows_before_exclusions",
    "hard_invalid_or_extreme_before_exclusions",
    "numeric_rows",
    "numeric_studies",
    "numeric_subjects",
    "studies_with_multiple_numeric_values",
    "outside_primary_range_studies",
    "hard_invalid_or_extreme_studies",
    "hard_invalid_or_extreme_excluded",
    "aggregation",
    "analysis_label",
    "joined_target_embedding_studies",
    "joined_target_embedding_subjects",
    "split_counts",
    "status",
    "skip_reason",
    "ridge_solver",
    "features_standardized",
    "ridge_alpha_grid",
    "random_seed",
    "hard_extremes_excluded",
    "train_target_iqr",
)


@dataclass(frozen=True)
class CorrectedAggregationResult:
    corrected_clip_embeddings: np.ndarray
    corrected_clip_manifest: pd.DataFrame
    corrected_study_embeddings: np.ndarray
    corrected_study_manifest: pd.DataFrame
    restricted_study_changes: pd.DataFrame
    aggregate_change_counts: pd.DataFrame
    aggregate_change_distribution: pd.DataFrame
    restricted_provenance: dict[str, Any]


@dataclass(frozen=True)
class OriginalCorrectedComparison:
    prediction_changes: pd.DataFrame
    prediction_metrics: pd.DataFrame
    aggregate_metrics: pd.DataFrame
    safe_provenance: dict[str, Any]
    restricted_provenance: dict[str, Any]


def _stable_scalar(value: Any, column: str) -> str:
    if pd.isna(value):
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            f"canonical identity column {column!r} contains missing values",
        )
    if isinstance(value, (bool, np.bool_)):
        return "true" if bool(value) else "false"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise Tier1BlockedError(
                BLOCKED_CANONICAL_IDENTITY,
                f"canonical identity column {column!r} contains a non-finite value",
            )
        if numeric.is_integer():
            return str(int(numeric))
        return format(numeric, ".17g")
    text = str(value).strip()
    if not text:
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            f"canonical identity column {column!r} contains an empty value",
        )
    if "path" in column.lower() or "file" in column.lower():
        return os.path.normpath(text)
    return text


def _stable_id(value: Any) -> str:
    normalized = _stable_scalar(value, "identifier")
    try:
        numeric = float(normalized)
    except ValueError:
        return normalized
    if math.isfinite(numeric) and numeric.is_integer():
        return str(int(numeric))
    return normalized


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _truthy_value(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def _normalized_field(value: Any, column: str) -> str:
    if pd.isna(value) or str(value).strip() == "":
        return ""
    return _stable_scalar(value, column)


def _window_values_from_evidence(row: Mapping[str, Any]) -> dict[str, str]:
    raw = row.get("explicit_window_spec_json", "")
    if pd.isna(raw) or not str(raw).strip():
        return {}
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            "forensic evidence contains invalid explicit_window_spec_json",
        ) from exc
    if not isinstance(payload, Mapping):
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            "forensic window specification must be a JSON object",
        )
    return {str(key): str(value).strip() for key, value in payload.items() if str(value).strip()}


def _manifest_window_column(canonical: str, columns: Sequence[str]) -> str | None:
    aliases = {
        "window_index": ("window_index", "window_idx", "clip_index", "clip_idx"),
        "window_id": ("window_id", "clip_id"),
        "frame_start": ("source_frame_start", "frame_start", "window_start"),
        "frame_end": ("source_frame_end", "frame_end", "window_end"),
        "frame_indices": ("sampled_indices", "frame_indices", "source_frame_indices"),
    }
    return next((column for column in aliases.get(canonical, ()) if column in columns), None)


def _evidence_candidates(
    evidence_row: Mapping[str, Any],
    manifest: pd.DataFrame,
    vector_hashes: Mapping[int, str],
) -> list[int]:
    study = _stable_id(evidence_row.get("study_id"))
    subject = _stable_id(evidence_row.get("subject_id"))
    candidates = manifest[(manifest["_study"] == study) & (manifest["_subject"] == subject)].copy()
    if candidates.empty:
        return []
    compared_fields = 0
    for column in PATH_IDENTITY_COLUMNS:
        if column not in manifest.columns or column not in evidence_row:
            continue
        expected = _normalized_field(evidence_row.get(column), column)
        if not expected:
            continue
        compared_fields += 1
        candidates = candidates[
            candidates[column].map(lambda value: _normalized_field(value, column)) == expected
        ]
    for canonical, expected in _window_values_from_evidence(evidence_row).items():
        manifest_column = _manifest_window_column(canonical, manifest.columns)
        if manifest_column is None:
            continue
        compared_fields += 1
        candidates = candidates[
            candidates[manifest_column].map(
                lambda value: _normalized_field(value, manifest_column)
            )
            == _normalized_field(expected, manifest_column)
        ]
    expected_vector_hash = str(evidence_row.get("embedding_vector_sha256", "")).strip()
    if expected_vector_hash:
        compared_fields += 1
        candidates = candidates[
            candidates["embedding_idx"].map(lambda index: vector_hashes[int(index)])
            == expected_vector_hash
        ]
    if compared_fields == 0:
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            "forensic row cannot be linked beyond study and subject identifiers",
        )
    return sorted(candidates["embedding_idx"].astype(int).tolist())


def _default_manifest_identity(row: pd.Series) -> str:
    columns = ["study_id"]
    columns.extend(
        column
        for column in (*PATH_IDENTITY_COLUMNS, *WINDOW_IDENTITY_COLUMNS)
        if column in row.index and _normalized_field(row[column], column)
    )
    if len(columns) == 1:
        raise Tier1BlockedError(
            BLOCKED_CANONICAL_IDENTITY,
            "an unreviewed clip row lacks a stable path/window identity",
        )
    payload = [["identity_namespace", "manifest_clip_v1"]]
    payload.extend([column, _normalized_field(row[column], column)] for column in columns)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decisions_from_restricted_evidence(
    evidence_path: Path,
    manifest: pd.DataFrame,
    embeddings: np.ndarray,
) -> tuple[dict[frozenset[int], dict[str, Any]], dict[str, Any]]:
    evidence = pd.read_csv(evidence_path)
    require_columns(
        evidence,
        [
            "group_token",
            "classification",
            "study_id",
            "subject_id",
            "embedding_vector_sha256",
            "processed_array_sha256",
            "dedup_keep_candidate",
        ],
        "restricted duplicate-forensics evidence",
    )
    if evidence.empty:
        raise Tier1BlockedError(CORRECTION_NOT_REQUIRED, "forensic evidence contains no repeated groups")
    evidence["classification"] = evidence["classification"].astype(str).str.strip().str.upper()
    invalid_classes = sorted(set(evidence["classification"]) - VALID_CLASSES)
    if invalid_classes:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            f"restricted forensic evidence contains invalid classifications: {invalid_classes}",
        )
    if evidence["classification"].isin(BLOCKING_CLASSES).any():
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "restricted forensic evidence contains unresolved ambiguous groups",
        )
    group_class_counts = evidence.groupby("group_token")["classification"].nunique()
    if int((group_class_counts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_DUPLICATE_SEMANTICS, "a forensic group has conflicting classifications")

    vector_hashes = {
        index: _array_sha256(embeddings[index]) for index in range(len(embeddings))
    }
    mapped_indices: set[int] = set()
    manifest["canonical_clip_id"] = manifest.apply(_default_manifest_identity, axis=1)
    decisions: dict[frozenset[int], dict[str, Any]] = {}
    actionable_groups = 0

    for group_number, (token, group) in enumerate(
        evidence.groupby("group_token", sort=True), start=1
    ):
        classification = str(group["classification"].iloc[0])
        ordered_group = group.copy()
        order_columns = [
            column
            for column in ("_batch", "_manifest_row", "embedding_idx")
            if column in ordered_group.columns
        ]
        if order_columns:
            ordered_group = ordered_group.sort_values(order_columns, kind="mergesort")
        candidate_union: set[int] = set()
        for _, evidence_row in ordered_group.iterrows():
            candidate_union.update(
                index
                for index in _evidence_candidates(evidence_row, manifest, vector_hashes)
                if index not in mapped_indices
            )
        if len(candidate_union) != len(group):
            raise Tier1BlockedError(
                BLOCKED_CANONICAL_IDENTITY,
                f"forensic group {group_number} maps to {len(candidate_union)} merged rows; "
                f"expected exactly {len(group)}",
            )
        row_mapping: dict[int, int] = {}
        used_in_group: set[int] = set()
        for evidence_index, evidence_row in ordered_group.iterrows():
            candidates = [
                index
                for index in _evidence_candidates(evidence_row, manifest, vector_hashes)
                if index not in mapped_indices and index not in used_in_group
            ]
            if not candidates:
                raise Tier1BlockedError(
                    BLOCKED_CANONICAL_IDENTITY,
                    f"forensic group {group_number} cannot be linked one-to-one to the merged clip manifest",
                )
            selected = min(candidates)
            row_mapping[int(evidence_index)] = selected
            used_in_group.add(selected)
        if len(used_in_group) != len(group):
            raise Tier1BlockedError(
                BLOCKED_CANONICAL_IDENTITY,
                f"forensic group {group_number} does not map to the expected number of clip rows",
            )
        mapped_indices.update(used_in_group)

        if classification in TRUE_DUPLICATE_CLASSES:
            actionable_groups += 1
            processed_hashes = {
                str(value).strip()
                for value in group["processed_array_sha256"]
                if str(value).strip() and str(value).strip().lower() != "nan"
            }
            if len(processed_hashes) != 1:
                raise Tier1BlockedError(
                    BLOCKED_CANONICAL_IDENTITY,
                    f"true-duplicate forensic group {group_number} lacks one processed-input identity",
                )
            keep_rows = [
                int(index)
                for index, value in group["dedup_keep_candidate"].items()
                if _truthy_value(value)
            ]
            if len(keep_rows) != 1:
                raise Tier1BlockedError(
                    BLOCKED_DUPLICATE_SEMANTICS,
                    f"true-duplicate forensic group {group_number} must declare exactly one keep row",
                )
            retained = row_mapping[keep_rows[0]]
            canonical = hashlib.sha256(
                json.dumps(
                    ["forensic_true_duplicate_v1", str(token), next(iter(processed_hashes))],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            manifest.loc[manifest["embedding_idx"].isin(used_in_group), "canonical_clip_id"] = canonical
            decisions[frozenset(used_in_group)] = {
                "classification": classification,
                "members": sorted(used_in_group),
                "retained": retained,
                "group_number": group_number,
            }
        else:
            canonical_values: list[str] = []
            for evidence_index, manifest_index in row_mapping.items():
                evidence_row = evidence.loc[evidence_index]
                processed_hash = str(evidence_row.get("processed_array_sha256", "")).strip()
                selector = str(evidence_row.get("processed_array_selector", "")).strip()
                if not processed_hash or processed_hash.lower() == "nan":
                    raise Tier1BlockedError(
                        BLOCKED_CANONICAL_IDENTITY,
                        f"nonduplicate forensic group {group_number} lacks processed-input identity",
                    )
                canonical = hashlib.sha256(
                    json.dumps(
                        ["forensic_distinct_clip_v1", str(token), processed_hash, selector],
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                manifest.loc[manifest["embedding_idx"] == manifest_index, "canonical_clip_id"] = canonical
                canonical_values.append(canonical)
            if len(canonical_values) != len(set(canonical_values)):
                raise Tier1BlockedError(
                    BLOCKED_CANONICAL_IDENTITY,
                    f"nonduplicate forensic group {group_number} is not distinct under processed-input identity",
                )
            decisions[frozenset(used_in_group)] = {
                "classification": classification,
                "members": sorted(used_in_group),
                "retained": min(used_in_group),
                "group_number": group_number,
            }

    if actionable_groups == 0:
        raise Tier1BlockedError(CORRECTION_NOT_REQUIRED, "forensic evidence confirms no actionable duplicates")
    duplicate_default = manifest[
        ~manifest["embedding_idx"].isin(mapped_indices)
    ].duplicated("canonical_clip_id", keep=False)
    if duplicate_default.any():
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "unreviewed clip rows collide under the stable manifest identity",
        )
    return decisions, {
        "identity_group_count": int(manifest["canonical_clip_id"].nunique()),
        "repeated_identity_group_count": int(
            (manifest.groupby("canonical_clip_id").size() > 1).sum()
        ),
        "actionable_group_count": actionable_groups,
        "forensic_group_count": int(evidence["group_token"].nunique()),
        "forensic_rows_mapped": int(len(mapped_indices)),
    }


def _load_embeddings(path: Path, label: str) -> np.ndarray:
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing {label}")
    with np.load(path) as data:
        if "embeddings" not in data:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} NPZ lacks an embeddings array")
        embeddings = np.asarray(data["embeddings"])
    if embeddings.ndim != 2 or embeddings.shape[0] == 0 or embeddings.shape[1] == 0:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"{label} embeddings must be a nonempty two-dimensional array",
        )
    if not np.issubdtype(embeddings.dtype, np.number) or not np.isfinite(embeddings).all():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} embeddings contain invalid values")
    return embeddings.astype(np.float32, copy=False)


def _successful_clip_manifest(frame: pd.DataFrame, n_embeddings: int) -> pd.DataFrame:
    require_columns(frame, ["embedding_idx", "study_id", "subject_id"], "clip embedding manifest")
    work = frame.copy()
    if "write_ok" in work.columns:
        values = work["write_ok"]
        if pd.api.types.is_bool_dtype(values):
            success = values.fillna(False).astype(bool)
        else:
            success = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        work = work[success].copy()
    work["embedding_idx"] = pd.to_numeric(work["embedding_idx"], errors="coerce")
    if work["embedding_idx"].isna().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "successful clip rows contain invalid embedding_idx")
    work["embedding_idx"] = work["embedding_idx"].astype(int)
    if work["embedding_idx"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip manifest contains duplicate embedding_idx values")
    expected = set(range(n_embeddings))
    observed = set(work["embedding_idx"].tolist())
    if observed != expected:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "successful clip manifest embedding_idx values do not exactly cover the embedding array",
        )
    if work[["study_id", "subject_id"]].isna().any().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip manifest contains missing study/subject identifiers")
    work["_study"] = work["study_id"].map(_stable_id)
    work["_subject"] = work["subject_id"].map(_stable_id)
    conflicts = work.groupby("_study")["_subject"].nunique()
    if int((conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip manifest conflicts on study-to-subject mapping")
    return work.reset_index(drop=True)


def _frozen_study_manifest(frame: pd.DataFrame, n_embeddings: int) -> pd.DataFrame:
    require_columns(
        frame,
        ["study_idx", "study_id", "subject_id", "n_clips"],
        "frozen study embedding manifest",
    )
    work = frame.copy()
    for column in ("study_idx", "n_clips"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
        if work[column].isna().any() or not np.all(work[column] == np.floor(work[column])):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"frozen study manifest has invalid {column}")
        work[column] = work[column].astype(int)
    if work["study_idx"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen study manifest has duplicate study_idx values")
    if set(work["study_idx"]) != set(range(n_embeddings)):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "frozen study manifest study_idx values do not exactly cover the embedding array",
        )
    if (work["n_clips"] <= 0).any() or work[["study_id", "subject_id"]].isna().any().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen study manifest has invalid identity or clip counts")
    work["_study"] = work["study_id"].map(_stable_id)
    work["_subject"] = work["subject_id"].map(_stable_id)
    if work["_study"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen study manifest has duplicate study identities")
    return work.sort_values("study_idx", kind="mergesort").reset_index(drop=True)


def _validate_identity_against_decisions(
    manifest: pd.DataFrame,
    decision_groups: Mapping[frozenset[int], Mapping[str, Any]],
) -> dict[str, Any]:
    identity_groups = {
        identity: sorted(group["embedding_idx"].astype(int).tolist())
        for identity, group in manifest.groupby("canonical_clip_id", sort=False)
    }
    repeated = {identity: members for identity, members in identity_groups.items() if len(members) > 1}
    actionable_decisions = {
        members: decision
        for members, decision in decision_groups.items()
        if decision["classification"] in TRUE_DUPLICATE_CLASSES
    }
    nonduplicate_decisions = {
        members: decision
        for members, decision in decision_groups.items()
        if decision["classification"] in NONDUPLICATE_CLASSES
    }

    for members, decision in actionable_decisions.items():
        keys = set(
            manifest.loc[manifest["embedding_idx"].isin(members), "canonical_clip_id"].tolist()
        )
        if len(keys) != 1:
            raise Tier1BlockedError(
                BLOCKED_CANONICAL_IDENTITY,
                f"true-duplicate forensic group {decision['group_number']} does not share one canonical identity",
            )
    for members, decision in nonduplicate_decisions.items():
        keys = manifest.loc[manifest["embedding_idx"].isin(members), "canonical_clip_id"]
        if keys.nunique() != len(members):
            raise Tier1BlockedError(
                BLOCKED_CANONICAL_IDENTITY,
                f"legitimate-distinct group {decision['group_number']} collides under the canonical identity",
            )

    actionable_member_sets = set(actionable_decisions)
    for identity, members in repeated.items():
        member_set = frozenset(members)
        if member_set not in actionable_member_sets:
            raise Tier1BlockedError(
                BLOCKED_DUPLICATE_SEMANTICS,
                "a repeated canonical clip identity lacks an exact true-duplicate forensic decision",
            )
    if not actionable_decisions:
        raise Tier1BlockedError(CORRECTION_NOT_REQUIRED, "forensic evidence confirms no actionable duplicate rows")
    return {
        "identity_group_count": len(identity_groups),
        "repeated_identity_group_count": len(repeated),
        "actionable_group_count": len(actionable_decisions),
    }


def _pool_studies(
    embeddings: np.ndarray,
    manifest: pd.DataFrame,
    frozen_manifest: pd.DataFrame,
) -> tuple[np.ndarray, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    manifest_studies = set(manifest["_study"])
    frozen_studies = set(frozen_manifest["_study"])
    if manifest_studies != frozen_studies:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "clip and frozen study stores contain different study identities",
        )
    for _, frozen_row in frozen_manifest.iterrows():
        study = str(frozen_row["_study"])
        group = manifest[manifest["_study"] == study].sort_values("embedding_idx", kind="mergesort")
        subjects = group["_subject"].drop_duplicates().tolist()
        if len(subjects) != 1 or subjects[0] != str(frozen_row["_subject"]):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"study {study!r} maps to multiple subjects")
        source_indices = group["embedding_idx"].to_numpy(dtype=int)
        pooled = embeddings[source_indices].mean(axis=0).astype(np.float32)
        study_idx = len(vectors)
        vectors.append(pooled)
        rows.append(
            {
                "study_idx": study_idx,
                "study_id": frozen_row["study_id"],
                "subject_id": frozen_row["subject_id"],
                "n_clips": int(len(group)),
                "embedding_l2_norm": float(np.linalg.norm(pooled)),
                "_study": study,
                "_subject": subjects[0],
            }
        )
    if not vectors:
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, "no study embeddings remain after correction")
    return np.stack(vectors).astype(np.float32), pd.DataFrame(rows)


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 1.0 if np.array_equal(left, right) else np.nan
    return float(np.dot(left, right) / denominator)


def _distribution_rows(changes: pd.DataFrame) -> pd.DataFrame:
    populations = {
        "all_studies": changes,
        "dedup_affected_studies": changes[changes["dedup_affected"]],
        "embedding_changed_studies": changes[changes["embedding_changed_exact"]],
    }
    columns = {
        "l2_distance": "l2_distance",
        "cosine_similarity": "cosine_similarity",
        "max_absolute_component_difference": "max_absolute_component_difference",
    }
    rows: list[dict[str, Any]] = []
    for population, frame in populations.items():
        for output_name, source_column in columns.items():
            values = frame[source_column].to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            row: dict[str, Any] = {
                "population": population,
                "distance_metric": output_name,
                "n_studies": int(len(frame)),
                "n_finite": int(len(values)),
            }
            if len(values):
                row.update(
                    {
                        "minimum": float(np.min(values)),
                        "q25": float(np.percentile(values, 25)),
                        "median": float(np.median(values)),
                        "mean": float(np.mean(values)),
                        "q75": float(np.percentile(values, 75)),
                        "maximum": float(np.max(values)),
                    }
                )
            else:
                row.update(
                    {
                        "minimum": np.nan,
                        "q25": np.nan,
                        "median": np.nan,
                        "mean": np.nan,
                        "q75": np.nan,
                        "maximum": np.nan,
                    }
                )
            rows.append(row)
    output = pd.DataFrame(rows)
    assert_export_safe_frame(output, "embedding-change distributions")
    return output


def build_corrected_aggregation(
    forensic_evidence_file: Path,
    clip_manifest_csv: Path,
    clip_embedding_npz: Path,
    frozen_study_manifest_csv: Path,
    frozen_study_embedding_npz: Path,
) -> CorrectedAggregationResult:
    """Build corrected clip/study arrays in memory after complete forensic validation."""

    for path in (
        forensic_evidence_file,
        clip_manifest_csv,
        clip_embedding_npz,
        frozen_study_manifest_csv,
        frozen_study_embedding_npz,
    ):
        require_restricted_destination(path)
    embeddings = _load_embeddings(clip_embedding_npz, "clip")
    if not clip_manifest_csv.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip manifest is missing")
    raw_manifest = pd.read_csv(clip_manifest_csv)
    manifest = _successful_clip_manifest(raw_manifest, len(embeddings))
    frozen_study_embeddings = _load_embeddings(frozen_study_embedding_npz, "frozen study")
    if not frozen_study_manifest_csv.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen study manifest is missing")
    frozen_manifest = _frozen_study_manifest(
        pd.read_csv(frozen_study_manifest_csv), len(frozen_study_embeddings)
    )
    if forensic_evidence_file.suffix.lower() != ".csv":
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_SEMANTICS,
            "corrected aggregation requires restricted duplicate_forensics_rows.csv evidence",
        )
    decision_groups, source_identity_summary = _decisions_from_restricted_evidence(
        forensic_evidence_file,
        manifest,
        embeddings,
    )
    identity_basis = [
        "forensic_group_token",
        "processed_array_sha256",
        "processed_array_selector",
        "stable_manifest_path_window_identity_for_unreviewed_rows",
    ]

    identity_summary = {
        **source_identity_summary,
        **_validate_identity_against_decisions(manifest, decision_groups),
    }

    decision_by_member: dict[int, Mapping[str, Any]] = {}
    retained_indices: set[int] = set(manifest["embedding_idx"].tolist())
    removed_indices: set[int] = set()
    for members, decision in decision_groups.items():
        for member in members:
            decision_by_member[member] = decision
        if decision["classification"] in TRUE_DUPLICATE_CLASSES:
            group_indices = sorted(members)
            retained = int(decision["retained"])
            retained_vector = embeddings[retained]
            exact_equal = all(np.array_equal(embeddings[index], retained_vector) for index in group_indices)
            near_equal = all(
                np.allclose(embeddings[index], retained_vector, rtol=1e-6, atol=1e-7, equal_nan=False)
                for index in group_indices
            )
            if not exact_equal and not near_equal:
                raise Tier1BlockedError(
                    BLOCKED_CORRECTED_ANALYSIS,
                    f"forensic group {decision['group_number']} contains materially divergent vectors",
                )
            to_remove = set(group_indices) - {retained}
            retained_indices.difference_update(to_remove)
            removed_indices.update(to_remove)

    retained_manifest = manifest[manifest["embedding_idx"].isin(retained_indices)].copy()
    retained_manifest["source_embedding_idx"] = retained_manifest["embedding_idx"].astype(int)
    retained_manifest["forensic_classification"] = retained_manifest["embedding_idx"].map(
        lambda index: decision_by_member.get(int(index), {}).get("classification", "UNIQUE")
    )
    group_sizes = manifest.groupby("canonical_clip_id").size().to_dict()
    retained_manifest["dedup_group_size"] = retained_manifest["canonical_clip_id"].map(group_sizes).astype(int)
    retained_manifest = retained_manifest.sort_values("source_embedding_idx", kind="mergesort").reset_index(
        drop=True
    )
    retained_manifest["embedding_idx"] = np.arange(len(retained_manifest), dtype=int)
    corrected_clip_embeddings = embeddings[
        retained_manifest["source_embedding_idx"].to_numpy(dtype=int)
    ].copy()

    original_for_pool = manifest.copy()
    corrected_for_pool = retained_manifest.copy()
    corrected_for_pool["embedding_idx"] = np.arange(len(corrected_for_pool), dtype=int)
    original_study_embeddings, original_study_manifest = _pool_studies(
        embeddings, original_for_pool, frozen_manifest
    )
    replay_counts = original_study_manifest["n_clips"].to_numpy(dtype=int)
    frozen_counts = frozen_manifest["n_clips"].to_numpy(dtype=int)
    if not np.array_equal(replay_counts, frozen_counts):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "historical clip replay does not reproduce frozen study clip counts",
        )
    if not np.array_equal(original_study_embeddings, frozen_study_embeddings):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "historical clip replay does not exactly reproduce the frozen Phase 2 study embeddings",
        )
    corrected_study_embeddings, corrected_study_manifest = _pool_studies(
        corrected_clip_embeddings, corrected_for_pool, frozen_manifest
    )

    original_index = original_study_manifest.set_index("_study")["study_idx"].astype(int)
    corrected_index = corrected_study_manifest.set_index("_study")["study_idx"].astype(int)
    if set(original_index.index) != set(corrected_index.index):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, "study identity changed after clip deduplication")
    original_subject = original_study_manifest.set_index("_study")["_subject"].to_dict()
    corrected_subject = corrected_study_manifest.set_index("_study")["_subject"].to_dict()
    if original_subject != corrected_subject:
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, "study-to-subject mapping changed after correction")

    affected_studies = set(manifest.loc[manifest["embedding_idx"].isin(removed_indices), "_study"])
    change_rows: list[dict[str, Any]] = []
    for study in sorted(original_index.index):
        old = original_study_embeddings[int(original_index.loc[study])]
        new = corrected_study_embeddings[int(corrected_index.loc[study])]
        difference = new.astype(np.float64) - old.astype(np.float64)
        original_row = original_study_manifest.loc[original_study_manifest["_study"] == study].iloc[0]
        corrected_row = corrected_study_manifest.loc[corrected_study_manifest["_study"] == study].iloc[0]
        change_rows.append(
            {
                "study_id": original_row["study_id"],
                "subject_id": original_row["subject_id"],
                "n_original_clips": int(original_row["n_clips"]),
                "n_corrected_clips": int(corrected_row["n_clips"]),
                "n_removed_clips": int(original_row["n_clips"] - corrected_row["n_clips"]),
                "dedup_affected": study in affected_studies,
                "embedding_changed_exact": not np.array_equal(old, new),
                "l2_distance": float(np.linalg.norm(difference)),
                "cosine_similarity": _cosine_similarity(old.astype(np.float64), new.astype(np.float64)),
                "max_absolute_component_difference": float(np.max(np.abs(difference))),
            }
        )
    changes = pd.DataFrame(change_rows)
    changed_subjects = int(changes.loc[changes["embedding_changed_exact"], "subject_id"].astype(str).nunique())
    affected_subjects = int(changes.loc[changes["dedup_affected"], "subject_id"].astype(str).nunique())
    counts = pd.DataFrame(
        [
            {
                "status": "CORRECTED_AGGREGATION_BUILT",
                "n_input_clip_rows": int(len(manifest)),
                "n_canonical_clip_rows": int(len(retained_manifest)),
                "n_removed_duplicate_rows": int(len(removed_indices)),
                "n_actionable_duplicate_groups": int(identity_summary["actionable_group_count"]),
                "n_studies": int(len(changes)),
                "n_subjects": int(changes["subject_id"].astype(str).nunique()),
                "n_dedup_affected_studies": int(changes["dedup_affected"].sum()),
                "n_dedup_affected_subjects": affected_subjects,
                "n_embedding_changed_studies": int(changes["embedding_changed_exact"].sum()),
                "n_embedding_changed_subjects": changed_subjects,
                "percentage_embedding_changed_studies": float(
                    100.0 * changes["embedding_changed_exact"].mean()
                ),
                "embedding_dimension": int(embeddings.shape[1]),
            }
        ]
    )
    assert_export_safe_frame(counts, "embedding-change counts")
    distribution = _distribution_rows(changes)

    restricted_provenance = {
        "schema_version": "jdim-corrected-aggregation-v1",
        "status": "CORRECTED_AGGREGATION_BUILT",
        "forensic_evidence_format": "duplicate_forensics_rows_v1",
        "canonical_identity_basis": identity_basis,
        "canonical_identity_target_prediction_independent": True,
        "representative_rule": "forensic content-derived keep candidate with immutable embedding-index tie-break",
        "pooling": "arithmetic mean of unique canonical clip embeddings per study",
        "frozen_parent_replay_exact": True,
        "frozen_parent_clip_counts_exact": True,
        "original_inputs_preserved": True,
        "input_files": {
            "forensic_evidence": {
                "path": str(forensic_evidence_file.resolve()),
                "sha256": sha256_file(forensic_evidence_file),
            },
            "clip_manifest": {
                "path": str(clip_manifest_csv.resolve()),
                "sha256": sha256_file(clip_manifest_csv),
            },
            "clip_embeddings": {
                "path": str(clip_embedding_npz.resolve()),
                "sha256": sha256_file(clip_embedding_npz),
            },
            "frozen_study_manifest": {
                "path": str(frozen_study_manifest_csv.resolve()),
                "sha256": sha256_file(frozen_study_manifest_csv),
            },
            "frozen_study_embeddings": {
                "path": str(frozen_study_embedding_npz.resolve()),
                "sha256": sha256_file(frozen_study_embedding_npz),
            },
        },
        "removed_source_embedding_indices": sorted(removed_indices),
        "identity_summary": identity_summary,
        "counts": counts.iloc[0].to_dict(),
    }

    public_clip_manifest = retained_manifest.drop(columns=["_study", "_subject"], errors="ignore")
    public_study_manifest = corrected_study_manifest.drop(columns=["_study", "_subject"], errors="ignore")
    return CorrectedAggregationResult(
        corrected_clip_embeddings=corrected_clip_embeddings,
        corrected_clip_manifest=public_clip_manifest,
        corrected_study_embeddings=corrected_study_embeddings,
        corrected_study_manifest=public_study_manifest,
        restricted_study_changes=changes,
        aggregate_change_counts=counts,
        aggregate_change_distribution=distribution,
        restricted_provenance=restricted_provenance,
    )


def _atomic_output_root(output_root: Path) -> tuple[Path, Path]:
    destination = require_restricted_destination(output_root)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing output root: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    return destination, temporary


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_ready(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_corrected_aggregation(
    result: CorrectedAggregationResult,
    output_root: Path,
) -> Path:
    """Atomically write a new restricted tree and refuse all overwrite."""

    destination, temporary = _atomic_output_root(output_root)
    try:
        restricted = temporary / "restricted"
        safe = temporary / "aggregate_safe"
        restricted.mkdir(parents=True)
        safe.mkdir(parents=True)
        np.savez_compressed(
            restricted / "deduplicated_clip_embeddings.npz",
            embeddings=result.corrected_clip_embeddings,
        )
        result.corrected_clip_manifest.to_csv(
            restricted / "deduplicated_clip_manifest.csv", index=False
        )
        np.savez_compressed(
            restricted / "corrected_study_embeddings.npz",
            embeddings=result.corrected_study_embeddings,
        )
        result.corrected_study_manifest.to_csv(
            restricted / "corrected_study_embedding_manifest.csv", index=False
        )
        result.restricted_study_changes.to_csv(
            restricted / "study_embedding_changes_restricted.csv", index=False
        )
        result.aggregate_change_counts.to_csv(
            safe / "embedding_change_counts.csv", index=False
        )
        result.aggregate_change_distribution.to_csv(
            safe / "embedding_change_distribution.csv", index=False
        )
        output_paths = {
            "corrected_clip_embedding_array": restricted / "deduplicated_clip_embeddings.npz",
            "corrected_clip_embedding_manifest": restricted / "deduplicated_clip_manifest.csv",
            "corrected_study_embedding_array": restricted / "corrected_study_embeddings.npz",
            "corrected_study_embedding_manifest": restricted / "corrected_study_embedding_manifest.csv",
            "study_embedding_changes_restricted": restricted / "study_embedding_changes_restricted.csv",
            "embedding_change_counts": safe / "embedding_change_counts.csv",
            "embedding_change_distribution": safe / "embedding_change_distribution.csv",
        }
        provenance = dict(result.restricted_provenance)
        provenance["output_files"] = [
            safe_file_record(role, path) for role, path in sorted(output_paths.items())
        ]
        _write_json(
            restricted / "corrected_aggregation_provenance_restricted.json",
            provenance,
        )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _normalize_split_map(frame: pd.DataFrame) -> pd.DataFrame:
    require_columns(frame, ["subject_id", "split"], "frozen split map")
    work = frame[["subject_id", "split"]].copy()
    if work.isna().any().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen split map contains missing values")
    work["_subject"] = work["subject_id"].map(_stable_id)
    work["split"] = work["split"].astype(str).str.strip().str.lower()
    invalid = sorted(set(work["split"]) - VALID_SPLITS)
    if invalid:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"frozen split map contains invalid labels: {invalid}")
    conflicts = work.groupby("_subject")["split"].nunique()
    if int((conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen split map assigns subjects to multiple splits")
    return work.drop_duplicates("_subject", keep="first")[["_subject", "split"]]


def _normalize_predictions(frame: pd.DataFrame, target: str, label: str) -> pd.DataFrame:
    work = frame.copy()
    aliases: dict[str, str] = {}
    if "target_value" not in work.columns and "y_true" in work.columns:
        aliases["y_true"] = "target_value"
    if "pred_ridge" not in work.columns and "y_pred" in work.columns:
        aliases["y_pred"] = "pred_ridge"
    work = work.rename(columns=aliases)
    require_columns(
        work,
        ["subject_id", "study_id", "split", "target_value", "pred_ridge", "pred_null_median"],
        label,
    )
    if "target" in work.columns:
        targets = set(work["target"].dropna().astype(str))
        if targets != {target}:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains targets {sorted(targets)}")
    work["target"] = target
    work["_subject"] = work["subject_id"].map(_stable_id)
    work["_study"] = work["study_id"].map(_stable_id)
    work["split"] = work["split"].astype(str).str.strip().str.lower()
    invalid = sorted(set(work["split"]) - VALID_SPLITS)
    if invalid:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains invalid splits: {invalid}")
    for column in ("target_value", "pred_ridge"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
        if work[column].isna().any() or not np.isfinite(work[column].to_numpy(dtype=float)).all():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains invalid {column}")
    if work.duplicated(["target", "_study"]).any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains duplicate target/study rows")
    mapping_conflicts = work.groupby("_study")["_subject"].nunique()
    split_conflicts = work.groupby("_subject")["split"].nunique()
    if int((mapping_conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} conflicts on study-to-subject mapping")
    if int((split_conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains subject split overlap")
    work["pred_null_median"] = pd.to_numeric(work["pred_null_median"], errors="coerce")
    if work["pred_null_median"].isna().any() or not np.isfinite(
        work["pred_null_median"].to_numpy(dtype=float)
    ).all():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains invalid null predictions")
    return work.reset_index(drop=True)


def _verify_against_frozen_split(
    predictions: pd.DataFrame,
    split_map: pd.DataFrame,
    label: str,
) -> None:
    joined = predictions[["_subject", "split"]].drop_duplicates().merge(
        split_map,
        on="_subject",
        how="left",
        validate="one_to_one",
        suffixes=("_prediction", "_frozen"),
    )
    if joined["split_frozen"].isna().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} includes subjects absent from the frozen split map")
    mismatch = joined["split_prediction"] != joined["split_frozen"]
    if mismatch.any():
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            f"{label} disagrees with the frozen split map for {int(mismatch.sum())} subjects",
        )


def _continuous_metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - observed
    result: dict[str, float] = {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "bias_pred_minus_true": float(np.mean(error)),
    }
    if len(observed) > 1:
        error_sd = float(np.std(error, ddof=1))
        result["bland_altman_lower"] = result["bias_pred_minus_true"] - 1.96 * error_sd
        result["bland_altman_upper"] = result["bias_pred_minus_true"] + 1.96 * error_sd
        denominator = float(np.sum(np.square(observed - np.mean(observed))))
        result["r2"] = (
            float(1.0 - np.sum(np.square(error)) / denominator) if denominator > 0 else np.nan
        )
    else:
        result.update({"bland_altman_lower": np.nan, "bland_altman_upper": np.nan, "r2": np.nan})
    if len(observed) >= 3 and np.std(observed) > 0 and np.std(predicted) > 0:
        result["pearson"] = float(np.corrcoef(observed, predicted)[0, 1])
        result["spearman"] = float(pd.Series(observed).corr(pd.Series(predicted), method="spearman"))
        design = np.column_stack([np.ones(len(predicted)), predicted])
        intercept, slope = np.linalg.lstsq(design, observed, rcond=None)[0]
        result["calibration_slope_true_on_pred"] = float(slope)
        result["calibration_intercept_true_on_pred"] = float(intercept)
    else:
        result.update(
            {
                "pearson": np.nan,
                "spearman": np.nan,
                "calibration_slope_true_on_pred": np.nan,
                "calibration_intercept_true_on_pred": np.nan,
            }
        )
    return result


def _pair_predictions(
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    target: str,
    split_map: pd.DataFrame,
) -> pd.DataFrame:
    _verify_against_frozen_split(original, split_map, f"{target} original predictions")
    _verify_against_frozen_split(corrected, split_map, f"{target} corrected predictions")
    key = ["target", "_study"]
    original_keys = set(map(tuple, original[key].itertuples(index=False, name=None)))
    corrected_keys = set(map(tuple, corrected[key].itertuples(index=False, name=None)))
    if original_keys != corrected_keys:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            f"{target} original/corrected prediction row identity differs for "
            f"{len(original_keys.symmetric_difference(corrected_keys))} study keys",
        )
    null_columns = ["pred_null_median"]
    immutable_metadata = [
        column
        for column in ("n_target_rows", "outside_primary_range", "hard_invalid_or_extreme")
        if column in original.columns or column in corrected.columns
    ]
    metadata_schema_mismatch = [
        column
        for column in immutable_metadata
        if (column in original.columns) != (column in corrected.columns)
    ]
    if metadata_schema_mismatch:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            f"{target} immutable prediction metadata schema differs: {metadata_schema_mismatch}",
        )
    merged = original[
        key + ["_subject", "split", "target_value", "pred_ridge", *null_columns, *immutable_metadata]
    ].merge(
        corrected[
            key + ["_subject", "split", "target_value", "pred_ridge", *null_columns, *immutable_metadata]
        ],
        on=key,
        how="inner",
        validate="one_to_one",
        suffixes=("_original", "_corrected"),
    )
    if not merged["_subject_original"].equals(merged["_subject_corrected"]):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"{target} subject mapping changed")
    if not merged["split_original"].equals(merged["split_corrected"]):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"{target} frozen split assignments changed")
    if not np.array_equal(
        merged["target_value_original"].to_numpy(dtype=float),
        merged["target_value_corrected"].to_numpy(dtype=float),
    ):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"{target} observed report-label rows changed")
    if not np.array_equal(
        merged["pred_null_median_original"].to_numpy(dtype=float),
        merged["pred_null_median_corrected"].to_numpy(dtype=float),
    ):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"{target} null predictions changed")
    for column in immutable_metadata:
        left_source = merged[f"{column}_original"].astype(object)
        right_source = merged[f"{column}_corrected"].astype(object)
        left = left_source.where(left_source.notna(), "[MISSING]").astype(str)
        right = right_source.where(right_source.notna(), "[MISSING]").astype(str)
        if not left.equals(right):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} immutable prediction metadata changed for {column}",
            )
    return merged


def _prediction_summaries(paired: Mapping[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    change_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    for target, frame in paired.items():
        for split in ("all", "train", "val", "test"):
            group = frame if split == "all" else frame[frame["split_original"] == split]
            if group.empty:
                continue
            original = group["pred_ridge_original"].to_numpy(dtype=float)
            corrected = group["pred_ridge_corrected"].to_numpy(dtype=float)
            difference = corrected - original
            changed = difference != 0.0
            change_rows.append(
                {
                    "target": target,
                    "split": split,
                    "n_studies": int(len(group)),
                    "n_subjects": int(group["_subject_original"].nunique()),
                    "n_predictions_changed_exact": int(changed.sum()),
                    "percentage_predictions_changed_exact": float(100.0 * changed.mean()),
                    "mean_signed_prediction_change": float(np.mean(difference)),
                    "mean_absolute_prediction_change": float(np.mean(np.abs(difference))),
                    "median_absolute_prediction_change": float(np.median(np.abs(difference))),
                    "p95_absolute_prediction_change": float(np.percentile(np.abs(difference), 95)),
                    "maximum_absolute_prediction_change": float(np.max(np.abs(difference))),
                }
            )
            observed = group["target_value_original"].to_numpy(dtype=float)
            original_metrics = _continuous_metrics(observed, original)
            corrected_metrics = _continuous_metrics(observed, corrected)
            for metric in original_metrics:
                old = original_metrics[metric]
                new = corrected_metrics[metric]
                metric_rows.append(
                    {
                        "target": target,
                        "split": split,
                        "model": "ridge",
                        "metric": metric,
                        "original_value": old,
                        "corrected_value": new,
                        "delta_corrected_minus_original": new - old,
                    }
                )
    changes = pd.DataFrame(change_rows)
    metrics = pd.DataFrame(metric_rows)
    assert_export_safe_frame(changes, "prediction-change summary")
    assert_export_safe_frame(metrics, "prediction metric comparison")
    return changes, metrics


def _safe_key_value(value: Any) -> str:
    if pd.isna(value):
        return "NA"
    if isinstance(value, (float, np.floating)):
        return format(float(value), ".15g")
    return str(value).strip()


def _compare_one_aggregate_table(
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    filename: str,
    target_scope: str,
) -> pd.DataFrame:
    assert_export_safe_frame(original, f"original {filename}")
    assert_export_safe_frame(corrected, f"corrected {filename}")
    if set(original.columns) != set(corrected.columns):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"aggregate schema changed for {filename}")
    required_keys = list(SUPPORTED_AGGREGATE_TABLES[filename])
    require_columns(original, required_keys, f"aggregate table {filename}")
    if original.duplicated(required_keys).any() or corrected.duplicated(required_keys).any():
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"aggregate keys are duplicated in {filename}")
    original_work = original.copy()
    corrected_work = corrected.copy()
    original_work["_key"] = original_work[required_keys].apply(
        lambda row: tuple(_safe_key_value(value) for value in row), axis=1
    )
    corrected_work["_key"] = corrected_work[required_keys].apply(
        lambda row: tuple(_safe_key_value(value) for value in row), axis=1
    )
    if set(original_work["_key"]) != set(corrected_work["_key"]):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, f"aggregate row identity changed for {filename}")
    merged = original_work.merge(
        corrected_work,
        on="_key",
        how="inner",
        validate="one_to_one",
        suffixes=("_original", "_corrected"),
    )
    numeric_columns = [
        column
        for column in original.columns
        if column not in required_keys
        and pd.api.types.is_numeric_dtype(original[column])
        and pd.api.types.is_numeric_dtype(corrected[column])
    ]
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        stratum = "|".join(
            f"{column}={_safe_key_value(row[f'{column}_original'])}"
            for column in required_keys
        )
        target = (
            _safe_key_value(row["target_original"])
            if "target" in required_keys
            else target_scope
        )
        for metric in numeric_columns:
            old = pd.to_numeric(pd.Series([row[f"{metric}_original"]]), errors="coerce").iloc[0]
            new = pd.to_numeric(pd.Series([row[f"{metric}_corrected"]]), errors="coerce").iloc[0]
            delta = float(new - old) if pd.notna(old) and pd.notna(new) else np.nan
            rows.append(
                {
                    "target": target,
                    "source_file": filename,
                    "stratum": stratum,
                    "metric": metric,
                    "original_value": float(old) if pd.notna(old) else np.nan,
                    "corrected_value": float(new) if pd.notna(new) else np.nan,
                    "delta_corrected_minus_original": delta,
                }
            )
    return pd.DataFrame(rows)


def _compare_aggregate_directories(
    original_dirs: Mapping[str, Path],
    corrected_dirs: Mapping[str, Path],
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    if set(original_dirs) != set(corrected_dirs):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, "original/corrected aggregate target scopes differ")
    frames: list[pd.DataFrame] = []
    records: list[dict[str, Any]] = []
    restricted_records: list[dict[str, Any]] = []
    for target_scope in sorted(original_dirs):
        original_dir = original_dirs[target_scope]
        corrected_dir = corrected_dirs[target_scope]
        if not original_dir.is_dir() or not corrected_dir.is_dir():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"aggregate result directory is missing for {target_scope}")
        found = 0
        for filename in SUPPORTED_AGGREGATE_TABLES:
            old_path = original_dir / filename
            new_path = corrected_dir / filename
            if old_path.exists() != new_path.exists():
                raise Tier1BlockedError(
                    BLOCKED_CORRECTED_ANALYSIS,
                    f"aggregate output presence differs for {target_scope}/{filename}",
                )
            if not old_path.exists():
                continue
            found += 1
            old_frame = pd.read_csv(old_path)
            new_frame = pd.read_csv(new_path)
            frames.append(_compare_one_aggregate_table(old_frame, new_frame, filename, target_scope))
            records.extend(
                [
                    safe_file_record(f"original_{target_scope}_{filename}", old_path, len(old_frame)),
                    safe_file_record(f"corrected_{target_scope}_{filename}", new_path, len(new_frame)),
                ]
            )
            restricted_records.extend(
                [
                    restricted_file_record(
                        f"original_{target_scope}_{filename}", old_path, len(old_frame)
                    ),
                    restricted_file_record(
                        f"corrected_{target_scope}_{filename}", new_path, len(new_frame)
                    ),
                ]
            )
        if target_scope == "reviewer_metrics":
            for filename in REVIEWER_SUPPORT_FILES:
                old_path = original_dir / filename
                new_path = corrected_dir / filename
                if not old_path.is_file() or not new_path.is_file():
                    raise Tier1BlockedError(
                        BLOCKED_CORRECTED_ANALYSIS,
                        f"reviewer-metric support file is missing: {filename}",
                    )
                records.extend(
                    [
                        safe_file_record(f"original_{target_scope}_{filename}", old_path),
                        safe_file_record(f"corrected_{target_scope}_{filename}", new_path),
                    ]
                )
                restricted_records.extend(
                    [
                        restricted_file_record(
                            f"original_{target_scope}_{filename}", old_path
                        ),
                        restricted_file_record(
                            f"corrected_{target_scope}_{filename}", new_path
                        ),
                    ]
                )
        if found == 0:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"no supported aggregate CSVs found for {target_scope}",
            )
    output = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    assert_export_safe_frame(output, "aggregate metric comparison")
    return output, records, restricted_records


def _validate_main_metrics_against_predictions(
    prediction_metrics: pd.DataFrame,
    aggregate_comparison: pd.DataFrame,
    tolerance: float,
) -> None:
    if tolerance < 0:
        raise ValueError("metric tolerance must be nonnegative")
    mappings = {
        "mae": "mae",
        "rmse": "rmse",
        "r2": "r2",
        "pearson": "pearson",
        "spearman": "spearman",
        "bias_pred_minus_true": "bias_pred_minus_true",
        "bland_altman_lower": "bland_altman_lower",
        "bland_altman_upper": "bland_altman_upper",
        "calibration_slope_true_on_pred": "calibration_slope_true_on_pred",
        "calibration_intercept_true_on_pred": "calibration_intercept_true_on_pred",
    }
    aggregate = aggregate_comparison[
        aggregate_comparison["source_file"] == "imaging_baseline_metrics.csv"
    ]
    if aggregate.empty:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "imaging_baseline_metrics.csv is required to reconcile aggregate outputs with predictions",
        )
    for _, direct in prediction_metrics.iterrows():
        if direct["split"] == "all" or direct["metric"] not in mappings:
            continue
        prefix = f"target={direct['target']}|split={direct['split']}|model=ridge"
        rows = aggregate[
            (aggregate["metric"] == mappings[direct["metric"]])
            & (aggregate["stratum"] == prefix)
        ]
        if len(rows) != 1:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"cannot uniquely reconcile {direct['target']} {direct['split']} {direct['metric']}",
            )
        aggregate_row = rows.iloc[0]
        for version in ("original", "corrected"):
            direct_value = float(direct[f"{version}_value"])
            aggregate_value = float(aggregate_row[f"{version}_value"])
            if np.isnan(direct_value) and np.isnan(aggregate_value):
                continue
            if not np.isclose(direct_value, aggregate_value, rtol=0.0, atol=tolerance, equal_nan=True):
                raise Tier1BlockedError(
                    BLOCKED_CORRECTED_ANALYSIS,
                    f"{version} aggregate {direct['metric']} does not reconcile with restricted predictions",
                )


def _verify_run_protocol_summaries(
    original: Mapping[str, Mapping[str, Any]],
    corrected: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if set(original) != set(corrected):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "original/corrected run-summary targets differ",
        )
    verified: dict[str, Any] = {}
    for target in sorted(original):
        old = original[target]
        new = corrected[target]
        missing = [
            field
            for field in RUN_PROTOCOL_FIELDS
            if field not in old or field not in new
        ]
        if missing:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} run summaries lack protocol fields: {missing}",
            )
        mismatched = [field for field in RUN_PROTOCOL_FIELDS if old[field] != new[field]]
        if mismatched:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} corrected run changed protocol fields: {mismatched}",
            )

        old_targets = old.get("target_summaries")
        new_targets = new.get("target_summaries")
        if not isinstance(old_targets, list) or not isinstance(new_targets, list):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} run summaries lack target_summaries lists",
            )
        old_by_target = {
            str(summary.get("target")): summary
            for summary in old_targets
            if isinstance(summary, Mapping)
        }
        new_by_target = {
            str(summary.get("target")): summary
            for summary in new_targets
            if isinstance(summary, Mapping)
        }
        if set(old_by_target) != set(new_by_target) or set(old_by_target) != {target}:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} target-summary identity changed",
            )
        old_target = old_by_target[target]
        new_target = new_by_target[target]
        missing_target = [
            field
            for field in TARGET_PROTOCOL_FIELDS
            if field not in old_target or field not in new_target
        ]
        if missing_target:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} target summaries lack invariant fields: {missing_target}",
            )
        mismatched_target = [
            field
            for field in TARGET_PROTOCOL_FIELDS
            if old_target[field] != new_target[field]
        ]
        if mismatched_target:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS,
                f"{target} corrected run changed target/cohort protocol fields: "
                f"{mismatched_target}",
            )
        verified[target] = {
            "run_protocol_fields": list(RUN_PROTOCOL_FIELDS),
            "target_protocol_fields": list(TARGET_PROTOCOL_FIELDS),
        }
    return verified


def compare_original_corrected(
    original_prediction_frames: Mapping[str, pd.DataFrame],
    corrected_prediction_frames: Mapping[str, pd.DataFrame],
    frozen_split_map: pd.DataFrame,
    original_aggregate_dirs: Mapping[str, Path],
    corrected_aggregate_dirs: Mapping[str, Path],
    original_run_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    corrected_run_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    metric_tolerance: float = 1e-6,
    input_records: Sequence[dict[str, Any]] | None = None,
    restricted_input_records: Sequence[dict[str, Any]] | None = None,
) -> OriginalCorrectedComparison:
    """Prove row/split identity and compare aggregate-safe outcomes."""

    if set(original_prediction_frames) != set(corrected_prediction_frames):
        raise Tier1BlockedError(BLOCKED_CORRECTED_ANALYSIS, "original/corrected prediction targets differ")
    split_map = _normalize_split_map(frozen_split_map)
    paired: dict[str, pd.DataFrame] = {}
    for target in sorted(original_prediction_frames):
        original = _normalize_predictions(
            original_prediction_frames[target], target, f"{target} original predictions"
        )
        corrected = _normalize_predictions(
            corrected_prediction_frames[target], target, f"{target} corrected predictions"
        )
        paired[target] = _pair_predictions(original, corrected, target, split_map)
    prediction_changes, prediction_metrics = _prediction_summaries(paired)
    aggregate_metrics, aggregate_records, restricted_aggregate_records = _compare_aggregate_directories(
        original_aggregate_dirs, corrected_aggregate_dirs
    )
    _validate_main_metrics_against_predictions(prediction_metrics, aggregate_metrics, metric_tolerance)
    if (original_run_summaries is None) != (corrected_run_summaries is None):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS,
            "original and corrected run summaries must be supplied together",
        )
    protocol_verification = (
        _verify_run_protocol_summaries(
            original_run_summaries or {}, corrected_run_summaries or {}
        )
        if original_run_summaries is not None
        else {}
    )

    provenance = {
        "schema_version": "jdim-original-corrected-comparison-v1",
        "status": "ORIGINAL_CORRECTED_IDENTITY_VERIFIED",
        "row_identity_verified": True,
        "study_subject_mapping_verified": True,
        "frozen_split_verified": True,
        "observed_report_label_identity_verified": True,
        "null_prediction_identity_verified": True,
        "run_protocol_verified": bool(protocol_verification),
        "run_protocol_verification": protocol_verification,
        "prediction_change_definition": "exact floating-point inequality after CSV parsing",
        "metric_delta_definition": "corrected_minus_original",
        "metric_reconciliation_absolute_tolerance": float(metric_tolerance),
        "targets": sorted(paired),
        "input_files": [*(input_records or []), *aggregate_records],
    }
    restricted_provenance = {
        "schema_version": "jdim-original-corrected-input-provenance-v1",
        "status": "ORIGINAL_CORRECTED_INPUTS_LOCKED",
        "input_files": [
            *(restricted_input_records or []),
            *restricted_aggregate_records,
        ],
    }
    return OriginalCorrectedComparison(
        prediction_changes=prediction_changes,
        prediction_metrics=prediction_metrics,
        aggregate_metrics=aggregate_metrics,
        safe_provenance=provenance,
        restricted_provenance=restricted_provenance,
    )


def write_original_corrected_comparison(
    result: OriginalCorrectedComparison,
    output_root: Path,
    restricted_input_provenance_json: Path,
) -> Path:
    output_destination = require_restricted_destination(output_root)
    if output_destination.exists():
        raise FileExistsError(
            f"refusing to overwrite existing output root: {output_destination}"
        )
    restricted_destination = require_restricted_destination(
        restricted_input_provenance_json
    )
    if restricted_destination.exists():
        raise FileExistsError(
            "refusing to overwrite comparison input provenance: "
            f"{restricted_destination}"
        )
    restricted_destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json(restricted_destination, result.restricted_provenance)
    destination, temporary = _atomic_output_root(output_root)
    try:
        result.prediction_changes.to_csv(
            temporary / "original_vs_corrected_prediction_changes.csv", index=False
        )
        result.prediction_metrics.to_csv(
            temporary / "original_vs_corrected_prediction_metrics.csv", index=False
        )
        result.aggregate_metrics.to_csv(
            temporary / "original_vs_corrected_aggregate_metrics.csv", index=False
        )
        provenance = dict(result.safe_provenance)
        provenance["restricted_input_manifest_sha256"] = sha256_file(
            restricted_destination
        )
        provenance["output_files"] = [
            safe_file_record(
                "comparison_prediction_changes",
                temporary / "original_vs_corrected_prediction_changes.csv",
            ),
            safe_file_record(
                "comparison_prediction_metrics",
                temporary / "original_vs_corrected_prediction_metrics.csv",
            ),
            safe_file_record(
                "comparison_aggregate_metrics",
                temporary / "original_vs_corrected_aggregate_metrics.csv",
            ),
        ]
        _write_json(
            temporary / "original_vs_corrected_comparison_provenance.json",
            provenance,
        )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination
