"""Metadata-only lineage adjudication for repeated historical clip rows.

This module is intentionally incapable of opening DICOM pixels, processed NPZ
archives, or embedding arrays. It traces only the exact keys supplied by a
restricted prior-forensics CSV through explicitly named, small text manifests.
Row-level evidence stays in restricted output; aggregate output is path-free.
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .safety import (
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    assert_export_safe_frame,
    git_root,
    is_within,
    require_columns,
    require_restricted_destination,
    restricted_file_record,
    sha256_file,
    write_json,
    write_safe_csv,
)


BLOCKED_DUPLICATE_LINEAGE_CONFLICT = "BLOCKED_DUPLICATE_LINEAGE_CONFLICT"
DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE = (
    "DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE"
)
READY_FOR_DEFERRED_REHYDRATION_JOB = "READY_FOR_DEFERRED_REHYDRATION_JOB"
REQUIRES_REHYDRATION = "REQUIRES_REHYDRATION"

TRUE_DUPLICATE_EXPECTED_ROWS = "TRUE_DUPLICATE_EXPECTED_ROWS"
TRUE_DUPLICATE_EXTRACTION_ROWS = "TRUE_DUPLICATE_EXTRACTION_ROWS"
TRUE_DUPLICATE_EMBEDDING_ROWS = "TRUE_DUPLICATE_EMBEDDING_ROWS"
TRUE_DUPLICATE_MERGE_ROWS = "TRUE_DUPLICATE_MERGE_ROWS"
LEGITIMATE_DISTINCT_CLIPS = "LEGITIMATE_DISTINCT_CLIPS"

STAGE_ORDER = (
    "expected_records",
    "dicom_audit",
    "cine_candidates",
    "extraction_manifest",
    "batch_embedding_manifest",
    "merged_embedding_manifest",
)

CLASS_BY_FIRST_STAGE = {
    "expected_records": TRUE_DUPLICATE_EXPECTED_ROWS,
    "dicom_audit": TRUE_DUPLICATE_EXPECTED_ROWS,
    "cine_candidates": TRUE_DUPLICATE_EXPECTED_ROWS,
    "extraction_manifest": TRUE_DUPLICATE_EXTRACTION_ROWS,
    "batch_embedding_manifest": TRUE_DUPLICATE_EMBEDDING_ROWS,
    "merged_embedding_manifest": TRUE_DUPLICATE_MERGE_ROWS,
}

KEY_COLUMNS = ("study_id", "dicom_filepath")
BASE_IDENTITY_COLUMNS = ("subject_id", "study_id", "dicom_filepath")
SOURCE_IDENTITY_COLUMNS = (
    "dicom_filepath",
    "dicom_abs_path",
    "source_dicom_path",
    "sop_instance_uid",
    "dicom_id",
)
PROCESSED_IDENTITY_COLUMNS = ("output_path", "npz_path", "processed_npz_path")
WINDOW_COLUMNS = (
    "window_index",
    "window_idx",
    "window_id",
    "clip_index",
    "clip_idx",
    "clip_id",
    "frame_start",
    "frame_end",
    "window_start",
    "window_end",
    "source_frame_start",
    "source_frame_end",
    "sampled_indices",
    "frame_indices",
    "source_frame_indices",
    "temporal_offset",
    "segment_id",
)
NONSEMANTIC_COLUMNS = {
    "embedding_idx",
    "_manifest_row",
    "row_index",
    "status",
    "error",
    "write_ok",
    "embedding_l2_norm",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    if text.endswith(".0"):
        prefix = text[:-2]
        if prefix.lstrip("-").isdigit():
            return prefix
    return text


def _bool_text(value: Any) -> bool:
    return _text(value).lower() in {"1", "true", "t", "yes", "y"}


def _normalized_relative_path(value: Any) -> str:
    return _text(value).lstrip("/")


def _key(study_id: Any, dicom_filepath: Any) -> tuple[str, str]:
    return _text(study_id), _normalized_relative_path(dicom_filepath)


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _all_same_nonempty(rows: Sequence[Mapping[str, Any]], column: str) -> bool:
    return len(_unique_nonempty(row.get(column) for row in rows)) <= 1


@dataclass(frozen=True)
class MetadataStage:
    name: str
    path: Path


@dataclass(frozen=True)
class MetadataInspectionInputs:
    prior_forensics_rows: Path
    stages: Sequence[MetadataStage]
    output_root: Path
    source_roots: Sequence[Path] = ()
    npz_candidate_roots: Sequence[Path] = ()
    path_remaps: Sequence[tuple[str, str]] = ()
    preprocessing_git_sha: str = ""
    preprocessing_script: Path | None = None
    embedding_script: Path | None = None
    runner_script: Path | None = None
    encoder_checkpoint_path: Path | None = None
    encoder_checkpoint_sha256: str = ""
    target_frames: int = 32
    target_size: int = 224
    encoder_frames: int = 16
    temporal_stride: int = 2
    expected_group_count: int | None = 32
    expected_row_count: int | None = 64


@dataclass
class MetadataInspectionResult:
    status: str
    summary: dict[str, Any]
    restricted_rows: pd.DataFrame
    restricted_field_equality: pd.DataFrame
    restricted_groups: pd.DataFrame
    restricted_recovery: pd.DataFrame
    safe_stage_summary: pd.DataFrame
    safe_classification_summary: pd.DataFrame
    safe_proof_matrix: pd.DataFrame
    safe_recovery_summary: dict[str, Any]
    input_provenance: dict[str, Any]


def parse_path_remaps(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    remaps: list[tuple[str, str]] = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"path remap must use OLD=NEW syntax: {value!r}")
        old, new = value.split("=", 1)
        old = old.strip().rstrip("/")
        new = new.strip().rstrip("/")
        if not old or not new:
            raise ValueError(f"path remap has an empty prefix: {value!r}")
        remaps.append((old, new))
    return tuple(remaps)


def _stream_matching_rows(
    stage: MetadataStage,
    key_to_group: Mapping[tuple[str, str], str],
) -> tuple[list[dict[str, Any]], list[str], int]:
    if stage.name not in STAGE_ORDER:
        raise ValueError(f"unknown metadata stage: {stage.name}")
    if not stage.path.is_file():
        raise FileNotFoundError(stage.path)

    matched: list[dict[str, Any]] = []
    total_rows = 0
    with stage.path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = list(reader.fieldnames or [])
        missing = sorted(set(KEY_COLUMNS) - set(columns))
        if missing:
            raise Tier1BlockedError(
                BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
                f"{stage.name} is missing key columns: {missing}",
            )
        for ordinal, row in enumerate(reader):
            total_rows += 1
            token = key_to_group.get(_key(row.get("study_id"), row.get("dicom_filepath")))
            if token is None:
                continue
            matched.append(
                {
                    "group_token": token,
                    "stage": stage.name,
                    "stage_row_ordinal": int(ordinal),
                    **{str(column): _text(row.get(column)) for column in columns},
                }
            )
    return matched, columns, total_rows


def _load_prior_rows(
    path: Path,
    expected_group_count: int | None,
    expected_row_count: int | None,
) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    require_columns(
        frame,
        ["group_token", "study_id", "subject_id", "dicom_filepath"],
        "prior duplicate forensics rows",
    )
    if expected_row_count is not None and len(frame) != expected_row_count:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
            f"expected {expected_row_count} prior rows, found {len(frame)}",
        )
    group_sizes = frame.groupby("group_token").size()
    if expected_group_count is not None and len(group_sizes) != expected_group_count:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
            f"expected {expected_group_count} groups, found {len(group_sizes)}",
        )
    if not group_sizes.eq(2).all():
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
            "prior evidence must contain exactly two rows per affected group",
        )

    mapping: dict[tuple[str, str], str] = {}
    for row in frame.to_dict(orient="records"):
        key = _key(row["study_id"], row["dicom_filepath"])
        token = _text(row["group_token"])
        existing = mapping.get(key)
        if existing is not None and existing != token:
            raise Tier1BlockedError(
                BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
                "one affected key maps to multiple prior group tokens",
            )
        mapping[key] = token
    if len(mapping) != len(group_sizes):
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
            "prior group tokens do not map one-to-one to affected keys",
        )
    return frame, mapping


def _field_equality_rows(
    group_token: str,
    stage: str,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for column in columns:
        values = [_text(row.get(column)) for row in rows]
        unique_nonempty = _unique_nonempty(values)
        output.append(
            {
                "group_token": group_token,
                "stage": stage,
                "field": column,
                "row_count": int(len(rows)),
                "nonempty_count": int(sum(bool(value) for value in values)),
                "unique_nonempty_count": int(len(unique_nonempty)),
                "all_values_equal_including_missing": bool(len(set(values)) <= 1),
                "all_nonempty_values_equal": bool(len(unique_nonempty) <= 1),
                "semantic_field": bool(
                    column not in NONSEMANTIC_COLUMNS
                    and column not in {"group_token", "stage", "stage_row_ordinal"}
                ),
                "values_json": json.dumps(values, ensure_ascii=True, separators=(",", ":")),
            }
        )
    return output


def _stage_semantics(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> dict[str, Any]:
    identity_columns = [
        column
        for column in (*BASE_IDENTITY_COLUMNS, *SOURCE_IDENTITY_COLUMNS, *PROCESSED_IDENTITY_COLUMNS)
        if column in columns
    ]
    conflicting_identity = [
        column for column in identity_columns if not _all_same_nonempty(rows, column)
    ]
    present_window_columns = [column for column in WINDOW_COLUMNS if column in columns]
    distinct_window_columns = [
        column for column in present_window_columns if not _all_same_nonempty(rows, column)
    ]
    return {
        "identity_columns_json": json.dumps(identity_columns, separators=(",", ":")),
        "conflicting_identity_columns_json": json.dumps(
            conflicting_identity, separators=(",", ":")
        ),
        "window_columns_json": json.dumps(present_window_columns, separators=(",", ":")),
        "distinct_window_columns_json": json.dumps(
            distinct_window_columns, separators=(",", ":")
        ),
        "semantic_identity_equal": not conflicting_identity,
        "distinct_window_metadata": bool(distinct_window_columns),
    }


def _historical_vector_equality(prior_group: pd.DataFrame) -> tuple[bool, bool]:
    if "embedding_vector_sha256" not in prior_group.columns:
        return False, False
    hashes = _unique_nonempty(prior_group["embedding_vector_sha256"])
    complete = bool(len(prior_group) and prior_group["embedding_vector_sha256"].map(_text).ne("").all())
    return complete, bool(complete and len(hashes) == 1)


def _classify_group(
    token: str,
    prior_group: pd.DataFrame,
    stage_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    stage_columns: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    counts = {stage: len(stage_rows.get(stage, ())) for stage in STAGE_ORDER}
    first_stage = next((stage for stage in STAGE_ORDER if counts[stage] > 1), "")
    stage_semantics = {
        stage: _stage_semantics(stage_rows.get(stage, ()), stage_columns.get(stage, ()))
        for stage in STAGE_ORDER
        if counts[stage]
    }
    identity_conflict = any(
        not bool(details["semantic_identity_equal"]) for details in stage_semantics.values()
    )
    source_identity_conflict = any(
        not _all_same_nonempty(rows, column)
        for stage, rows in stage_rows.items()
        for column in (*BASE_IDENTITY_COLUMNS, *SOURCE_IDENTITY_COLUMNS)
        if column in stage_columns.get(stage, ())
    )
    distinct_windows = any(
        bool(details["distinct_window_metadata"]) for details in stage_semantics.values()
    )
    vectors_complete, vectors_exact = _historical_vector_equality(prior_group)

    processed_paths: list[str] = []
    for stage in ("extraction_manifest", "batch_embedding_manifest", "merged_embedding_manifest"):
        for row in stage_rows.get(stage, ()):
            for column in PROCESSED_IDENTITY_COLUMNS:
                value = _text(row.get(column))
                if value:
                    processed_paths.append(value)
                    break
    one_processed_path = len(set(processed_paths)) == 1 if processed_paths else False

    source_relative_paths: list[str] = []
    source_absolute_paths: list[str] = []
    for stage in ("expected_records", "dicom_audit", "cine_candidates"):
        for row in stage_rows.get(stage, ()):
            relative = _normalized_relative_path(row.get("dicom_filepath"))
            absolute = _text(row.get("dicom_abs_path"))
            if relative:
                source_relative_paths.append(relative)
            if absolute:
                source_absolute_paths.append(absolute)
    one_source_path = bool(
        source_relative_paths
        and len(set(source_relative_paths)) == 1
        and (not source_absolute_paths or len(set(source_absolute_paths)) == 1)
    )

    if distinct_windows and not source_identity_conflict:
        classification = LEGITIMATE_DISTINCT_CLIPS
        reason = "positive distinct clip/window metadata is present"
    elif not first_stage:
        classification = REQUIRES_REHYDRATION
        reason = "multiplicity was not reproduced in the supplied stage manifests"
    elif identity_conflict:
        classification = REQUIRES_REHYDRATION
        reason = "semantic identity fields conflict within at least one stage"
    elif not vectors_complete or not vectors_exact:
        classification = REQUIRES_REHYDRATION
        reason = "historical exact-vector evidence is incomplete or conflicting"
    elif not one_source_path or not one_processed_path:
        classification = REQUIRES_REHYDRATION
        reason = "one-to-one source and processed-path identity was not established"
    else:
        classification = CLASS_BY_FIRST_STAGE[first_stage]
        reason = (
            f"multiplicity first appears at {first_stage}; source and processed identity are "
            "single-valued, no distinct window metadata exists, and historical vectors are exact"
        )

    return {
        "group_token": token,
        "classification": classification,
        "classification_reason": reason,
        "first_duplicate_stage": first_stage or "not_observed",
        "semantic_identity_equal_all_stages": not identity_conflict,
        "distinct_window_metadata_present": distinct_windows,
        "historical_vector_hashes_complete": vectors_complete,
        "historical_vectors_exact_equal": vectors_exact,
        "one_source_path": one_source_path,
        "one_processed_path": one_processed_path,
        **{f"n_{stage}_rows": int(counts[stage]) for stage in STAGE_ORDER},
    }


def _apply_remaps(path: str, remaps: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    candidates = [("recorded", path)] if path else []
    for index, (old, new) in enumerate(remaps):
        if path == old or path.startswith(old + "/"):
            candidates.append((f"declared_remap_{index + 1}", new + path[len(old) :]))
    return candidates


def _path_metadata(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": False,
        "is_file": False,
        "is_symlink": False,
        "size_bytes": None,
        "mtime_ns": None,
        "symlink_target": "",
        "lstat_error": "",
    }
    try:
        stat = path.lstat()
        result.update(
            {
                "exists": True,
                "is_file": path.is_file(),
                "is_symlink": path.is_symlink(),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "symlink_target": os.readlink(path) if path.is_symlink() else "",
            }
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        result["lstat_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _recovery_rows(
    groups: pd.DataFrame,
    prior: pd.DataFrame,
    matched: pd.DataFrame,
    inputs: MetadataInspectionInputs,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group in groups.to_dict(orient="records"):
        token = _text(group["group_token"])
        group_prior = prior[prior["group_token"].map(_text) == token]
        group_matched = matched[matched["group_token"].map(_text) == token]

        npz_values: set[str] = set()
        for column in (*PROCESSED_IDENTITY_COLUMNS, "_resolved_npz_path"):
            if column in group_prior.columns:
                npz_values.update(_unique_nonempty(group_prior[column]))
            if column in group_matched.columns:
                npz_values.update(_unique_nonempty(group_matched[column]))

        relative_paths = _unique_nonempty(
            list(group_prior.get("dicom_filepath", pd.Series(dtype=str)))
            + list(group_matched.get("dicom_filepath", pd.Series(dtype=str)))
        )
        source_values: set[str] = set()
        if "dicom_abs_path" in group_matched.columns:
            source_values.update(_unique_nonempty(group_matched["dicom_abs_path"]))
        for root in inputs.source_roots:
            for relative in relative_paths:
                source_values.add(str(root.expanduser() / relative.lstrip("/")))

        candidate_records: list[dict[str, Any]] = []
        for value in sorted(npz_values):
            for kind, candidate in _apply_remaps(value, inputs.path_remaps):
                candidate_records.append(
                    {
                        "group_token": token,
                        "candidate_type": "processed_npz",
                        "candidate_kind": kind,
                        "candidate_path": candidate,
                        **_path_metadata(Path(candidate).expanduser()),
                    }
                )
            for index, root in enumerate(inputs.npz_candidate_roots):
                candidate = root.expanduser() / Path(value).name
                candidate_records.append(
                    {
                        "group_token": token,
                        "candidate_type": "processed_npz",
                        "candidate_kind": f"declared_basename_root_{index + 1}",
                        "candidate_path": str(candidate),
                        **_path_metadata(candidate),
                    }
                )
        for value in sorted(source_values):
            for kind, candidate in _apply_remaps(value, inputs.path_remaps):
                candidate_records.append(
                    {
                        "group_token": token,
                        "candidate_type": "source_dicom",
                        "candidate_kind": kind,
                        "candidate_path": candidate,
                        **_path_metadata(Path(candidate).expanduser()),
                    }
                )

        npz_available = any(
            row["candidate_type"] == "processed_npz" and row["exists"] and row["is_file"]
            for row in candidate_records
        )
        source_available = any(
            row["candidate_type"] == "source_dicom" and row["exists"] and row["is_file"]
            for row in candidate_records
        )
        unique_source_linkage = len(relative_paths) == 1 and bool(source_values)
        provenance_resolved = _text(group["classification"]) != REQUIRES_REHYDRATION
        preprocessing_pinned = bool(
            inputs.preprocessing_git_sha
            and inputs.preprocessing_script
            and inputs.embedding_script
            and inputs.runner_script
        )
        frame_selection_pinned = bool(
            preprocessing_pinned
            and inputs.target_frames == 32
            and inputs.target_size == 224
            and inputs.encoder_frames == 16
            and inputs.temporal_stride == 2
        )
        checkpoint_pinned = bool(inputs.encoder_checkpoint_sha256)
        technically_ready = bool(
            not provenance_resolved
            and unique_source_linkage
            and (npz_available or source_available)
            and preprocessing_pinned
            and frame_selection_pinned
            and checkpoint_pinned
        )
        remaining: list[str] = []
        if not provenance_resolved:
            if not unique_source_linkage:
                remaining.append("unique_source_linkage")
            if not npz_available and not source_available:
                remaining.append("historical_npz_or_source_dicom")
            if not preprocessing_pinned:
                remaining.append("preprocessing_provenance")
            if not frame_selection_pinned:
                remaining.append("frame_selection_provenance")
            if not checkpoint_pinned:
                remaining.append("checkpoint_provenance")

        for row in candidate_records:
            row.update(
                {
                    "classification": group["classification"],
                    "provenance_resolved": provenance_resolved,
                    "unique_source_linkage": unique_source_linkage,
                    "historical_preprocessing_pinned": preprocessing_pinned,
                    "frame_selection_pinned": frame_selection_pinned,
                    "encoder_checkpoint_pinned": checkpoint_pinned,
                    "rehydration_technically_ready": technically_ready,
                    "remaining_missing_dependency": ";".join(remaining),
                }
            )
            rows.append(row)

        if not candidate_records:
            rows.append(
                {
                    "group_token": token,
                    "candidate_type": "none",
                    "candidate_kind": "none_recorded",
                    "candidate_path": "",
                    **_path_metadata(Path("/__jdim_metadata_missing_candidate__")),
                    "classification": group["classification"],
                    "provenance_resolved": provenance_resolved,
                    "unique_source_linkage": unique_source_linkage,
                    "historical_preprocessing_pinned": preprocessing_pinned,
                    "frame_selection_pinned": frame_selection_pinned,
                    "encoder_checkpoint_pinned": checkpoint_pinned,
                    "rehydration_technically_ready": technically_ready,
                    "remaining_missing_dependency": ";".join(remaining),
                }
            )
    return pd.DataFrame(rows)


def _safe_stage_summary(groups: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for stage in (*STAGE_ORDER, "not_observed"):
        subset = groups[groups["first_duplicate_stage"] == stage]
        rows.append(
            {
                "first_duplicate_stage": stage,
                "n_groups": int(len(subset)),
                "n_groups_semantic_identity_equal": int(
                    subset["semantic_identity_equal_all_stages"].sum()
                ),
                "n_groups_with_distinct_window_metadata": int(
                    subset["distinct_window_metadata_present"].sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def _safe_classification_summary(groups: pd.DataFrame) -> pd.DataFrame:
    classes = (
        TRUE_DUPLICATE_EXPECTED_ROWS,
        TRUE_DUPLICATE_EXTRACTION_ROWS,
        TRUE_DUPLICATE_EMBEDDING_ROWS,
        TRUE_DUPLICATE_MERGE_ROWS,
        LEGITIMATE_DISTINCT_CLIPS,
        REQUIRES_REHYDRATION,
    )
    return pd.DataFrame(
        [
            {
                "classification": classification,
                "n_groups": int((groups["classification"] == classification).sum()),
            }
            for classification in classes
        ]
    )


def _proof_matrix(groups: pd.DataFrame) -> pd.DataFrame:
    counts = Counter(groups["classification"])
    first_counts = Counter(groups["first_duplicate_stage"])
    unresolved = int(counts[REQUIRES_REHYDRATION])
    interpretations = [
        {
            "interpretation": "Legitimate distinct clips",
            "evidence_for": f"{int(counts[LEGITIMATE_DISTINCT_CLIPS])} groups have positive distinct-window metadata",
            "evidence_against": f"{int((~groups['distinct_window_metadata_present']).sum())} groups lack a distinct window/clip field",
            "missing_evidence": "processed reconstruction only for unresolved groups",
            "status": "SUPPORTED" if counts[LEGITIMATE_DISTINCT_CLIPS] else "CONTRADICTED",
        },
        {
            "interpretation": "Duplicate expected-list rows",
            "evidence_for": f"{int(first_counts['expected_records'])} groups first repeat in expected records",
            "evidence_against": f"{int((groups['first_duplicate_stage'] != 'expected_records').sum())} groups first repeat elsewhere",
            "missing_evidence": "none for provenance-resolved expected-row groups",
            "status": "SUPPORTED" if first_counts["expected_records"] else "UNRESOLVED",
        },
        {
            "interpretation": "Duplicate extraction rows",
            "evidence_for": f"{int(first_counts['extraction_manifest'])} groups first repeat at extraction",
            "evidence_against": f"{int((groups['first_duplicate_stage'] != 'extraction_manifest').sum())} groups first repeat elsewhere",
            "missing_evidence": "none for provenance-resolved extraction groups",
            "status": "SUPPORTED" if first_counts["extraction_manifest"] else "CONTRADICTED",
        },
        {
            "interpretation": "Duplicate embedding rows",
            "evidence_for": f"{int(first_counts['batch_embedding_manifest'])} groups first repeat at embedding",
            "evidence_against": f"{int((groups['first_duplicate_stage'] != 'batch_embedding_manifest').sum())} groups first repeat elsewhere",
            "missing_evidence": "none for provenance-resolved embedding groups",
            "status": "SUPPORTED" if first_counts["batch_embedding_manifest"] else "CONTRADICTED",
        },
        {
            "interpretation": "Resume/concatenation duplication",
            "evidence_for": f"{int(first_counts['merged_embedding_manifest'])} groups first repeat only at merge",
            "evidence_against": f"{int((groups['first_duplicate_stage'] != 'merged_embedding_manifest').sum())} groups repeat before merge or remain unobserved",
            "missing_evidence": "historical text logs for any unresolved group",
            "status": "SUPPORTED" if first_counts["merged_embedding_manifest"] else "CONTRADICTED",
        },
        {
            "interpretation": "Coarse key only",
            "evidence_for": f"{int(groups['distinct_window_metadata_present'].sum())} groups have a finer semantic distinction",
            "evidence_against": f"{int((~groups['distinct_window_metadata_present']).sum())} groups have no finer clip/window metadata",
            "missing_evidence": "positive finer source/window identity",
            "status": "SUPPORTED" if counts[LEGITIMATE_DISTINCT_CLIPS] else "CONTRADICTED",
        },
    ]
    if unresolved:
        for row in interpretations:
            if row["status"] == "CONTRADICTED":
                row["status"] = "UNRESOLVED"
    return pd.DataFrame(interpretations)


def inspect_duplicate_metadata(inputs: MetadataInspectionInputs) -> MetadataInspectionResult:
    stage_names = [stage.name for stage in inputs.stages]
    if tuple(stage_names) != STAGE_ORDER:
        raise ValueError(f"stages must be supplied exactly in order: {STAGE_ORDER}")
    if len(set(stage_names)) != len(stage_names):
        raise ValueError("metadata stages must be unique")

    prior, key_to_group = _load_prior_rows(
        inputs.prior_forensics_rows,
        inputs.expected_group_count,
        inputs.expected_row_count,
    )
    matched_rows: list[dict[str, Any]] = []
    stage_columns: dict[str, list[str]] = {}
    stage_total_rows: dict[str, int] = {}
    provenance_stages: list[dict[str, Any]] = []
    for stage in inputs.stages:
        rows, columns, total_rows = _stream_matching_rows(stage, key_to_group)
        matched_rows.extend(rows)
        stage_columns[stage.name] = columns
        stage_total_rows[stage.name] = total_rows
        provenance_stages.append(
            {
                "stage": stage.name,
                **restricted_file_record(
                    f"metadata_stage_{stage.name}", stage.path, row_count=total_rows
                ),
                "matched_row_count": int(len(rows)),
            }
        )

    matched = pd.DataFrame(matched_rows)
    if matched.empty:
        raise Tier1BlockedError(
            BLOCKED_DUPLICATE_LINEAGE_CONFLICT,
            "none of the exact affected keys matched the supplied manifests",
        )

    by_token_stage: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in matched_rows:
        by_token_stage[_text(row["group_token"])][_text(row["stage"])].append(row)

    field_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    for token in sorted(prior["group_token"].map(_text).unique()):
        token_stages = by_token_stage[token]
        for stage in STAGE_ORDER:
            stage_group_rows = token_stages.get(stage, [])
            if stage_group_rows:
                field_rows.extend(
                    _field_equality_rows(
                        token,
                        stage,
                        stage_group_rows,
                        stage_columns[stage],
                    )
                )
        group_rows.append(
            _classify_group(
                token,
                prior[prior["group_token"].map(_text) == token],
                token_stages,
                stage_columns,
            )
        )

    groups = pd.DataFrame(group_rows)
    field_equality = pd.DataFrame(field_rows)
    recovery = _recovery_rows(groups, prior, matched, inputs)
    safe_stage = _safe_stage_summary(groups)
    safe_classes = _safe_classification_summary(groups)
    safe_proof = _proof_matrix(groups)

    unresolved = int((groups["classification"] == REQUIRES_REHYDRATION).sum())
    if unresolved:
        status = READY_FOR_DEFERRED_REHYDRATION_JOB
    else:
        status = DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE

    # Compute typed candidate availability without carrying paths to safe output.
    recovery_group_summary: list[dict[str, Any]] = []
    for token, subset in recovery.groupby("group_token", sort=False):
        npz = subset[subset["candidate_type"] == "processed_npz"]
        source = subset[subset["candidate_type"] == "source_dicom"]
        recovery_group_summary.append(
            {
                "group_token": token,
                "npz_available": bool((npz["exists"] & npz["is_file"]).any()) if len(npz) else False,
                "source_available": bool((source["exists"] & source["is_file"]).any()) if len(source) else False,
                "unique_source_linkage": bool(subset["unique_source_linkage"].all()),
                "preprocessing_pinned": bool(subset["historical_preprocessing_pinned"].all()),
                "frame_selection_pinned": bool(subset["frame_selection_pinned"].all()),
                "checkpoint_pinned": bool(subset["encoder_checkpoint_pinned"].all()),
                "technically_ready": bool(subset["rehydration_technically_ready"].all()),
                "provenance_resolved": bool(subset["provenance_resolved"].all()),
            }
        )
    recovery_groups = pd.DataFrame(recovery_group_summary)
    safe_recovery = {
        "n_groups": int(len(recovery_groups)),
        "n_groups_recoverable_from_existing_npz": int(recovery_groups["npz_available"].sum()),
        "n_groups_with_source_dicom_available": int(recovery_groups["source_available"].sum()),
        "n_groups_requiring_dicom_rehydration": int(
            ((~recovery_groups["npz_available"]) & ~recovery_groups["provenance_resolved"]).sum()
        ),
        "n_groups_lacking_unique_source_linkage": int(
            (~recovery_groups["unique_source_linkage"]).sum()
        ),
        "n_groups_lacking_pinned_preprocessing": int(
            (~recovery_groups["preprocessing_pinned"]).sum()
        ),
        "n_groups_lacking_pinned_frame_selection": int(
            (~recovery_groups["frame_selection_pinned"]).sum()
        ),
        "n_groups_lacking_pinned_checkpoint": int(
            (~recovery_groups["checkpoint_pinned"]).sum()
        ),
        "n_groups_ready_for_scheduled_reconstruction": int(
            recovery_groups["technically_ready"].sum()
        ),
        "n_groups_resolved_from_provenance_alone": int(
            recovery_groups["provenance_resolved"].sum()
        ),
        "n_groups_still_blocked": unresolved,
    }

    summary = {
        "status": status,
        "inspection_mode": "metadata_only_no_dicom_npz_or_embedding_array_reads",
        "n_groups": int(len(groups)),
        "n_prior_rows": int(len(prior)),
        "n_manifest_rows_matched": int(len(matched)),
        "n_groups_resolved_from_provenance": int(len(groups) - unresolved),
        "n_groups_requiring_rehydration": unresolved,
        "n_groups_with_identical_semantic_fields": int(
            groups["semantic_identity_equal_all_stages"].sum()
        ),
        "n_groups_with_distinct_window_or_clip_metadata": int(
            groups["distinct_window_metadata_present"].sum()
        ),
        "first_duplicate_stage_counts": {
            stage: int((groups["first_duplicate_stage"] == stage).sum())
            for stage in (*STAGE_ORDER, "not_observed")
        },
        "classification_counts": {
            row["classification"]: int(row["n_groups"])
            for row in safe_classes.to_dict(orient="records")
        },
        "recovery_readiness": safe_recovery,
        "pipeline_semantics": {
            "one_historical_npz_per_dicom_path": True,
            "one_historical_embedding_per_successful_extraction_row": True,
            "historical_manifest_has_window_or_segment_field": False,
            "embedding_index_semantics": "row_position_in_batch_or_concatenated_embedding_array",
        },
    }
    input_provenance = {
        "prior_forensics": restricted_file_record(
            "prior_duplicate_forensics_rows", inputs.prior_forensics_rows, row_count=len(prior)
        ),
        "stages": provenance_stages,
        "parameters": {
            "preprocessing_git_sha": inputs.preprocessing_git_sha,
            "target_frames": int(inputs.target_frames),
            "target_size": int(inputs.target_size),
            "encoder_frames": int(inputs.encoder_frames),
            "temporal_stride": int(inputs.temporal_stride),
            "encoder_checkpoint_sha256": inputs.encoder_checkpoint_sha256,
        },
    }
    for role, path in (
        ("preprocessing_script", inputs.preprocessing_script),
        ("embedding_script", inputs.embedding_script),
        ("runner_script", inputs.runner_script),
    ):
        if path is not None:
            input_provenance[role] = restricted_file_record(role, path)
    if inputs.encoder_checkpoint_path is not None:
        metadata = _path_metadata(inputs.encoder_checkpoint_path)
        input_provenance["encoder_checkpoint_lstat"] = {
            "path": str(inputs.encoder_checkpoint_path.expanduser()),
            **metadata,
            "declared_sha256": inputs.encoder_checkpoint_sha256,
        }

    for frame, label in (
        (safe_stage, "stage summary"),
        (safe_classes, "classification summary"),
        (safe_proof, "proof matrix"),
    ):
        assert_export_safe_frame(frame, label)

    return MetadataInspectionResult(
        status=status,
        summary=summary,
        restricted_rows=matched,
        restricted_field_equality=field_equality,
        restricted_groups=groups,
        restricted_recovery=recovery,
        safe_stage_summary=safe_stage,
        safe_classification_summary=safe_classes,
        safe_proof_matrix=safe_proof,
        safe_recovery_summary=safe_recovery,
        input_provenance=input_provenance,
    )


def write_metadata_inspection_outputs(
    result: MetadataInspectionResult,
    output_root: Path,
    worktree: Path | None = None,
) -> None:
    root = require_restricted_destination(output_root, worktree=worktree)
    repo = worktree.resolve() if worktree is not None else git_root()
    if repo is not None and is_within(root, repo):
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "output root is inside the Git worktree")
    if root.exists():
        raise FileExistsError(f"refusing to overwrite metadata output root: {root}")

    restricted = root / "restricted"
    safe = root / "aggregate_safe"
    restricted.mkdir(parents=True)
    safe.mkdir(parents=True)

    restricted_files = {
        "metadata_stage_rows.csv": result.restricted_rows,
        "metadata_field_equality.csv": result.restricted_field_equality,
        "metadata_group_classification.csv": result.restricted_groups,
        "recovery_readiness_rows.csv": result.restricted_recovery,
    }
    for name, frame in restricted_files.items():
        frame.to_csv(restricted / name, index=False)
    write_json(restricted / "input_provenance_restricted.json", result.input_provenance)

    restricted_hashes = {
        name: sha256_file(restricted / name) for name in sorted(restricted_files)
    }
    restricted_hashes["input_provenance_restricted.json"] = sha256_file(
        restricted / "input_provenance_restricted.json"
    )

    summary = dict(result.summary)
    summary["restricted_evidence_packet_sha256"] = restricted_hashes
    write_json(safe / "duplicate_metadata_summary.json", summary)
    write_json(safe / "recovery_readiness_summary.json", result.safe_recovery_summary)
    write_safe_csv(
        safe / "first_duplicate_stage_summary.csv",
        result.safe_stage_summary,
        "first duplicate stage summary",
    )
    write_safe_csv(
        safe / "classification_summary.csv",
        result.safe_classification_summary,
        "classification summary",
    )
    write_safe_csv(
        safe / "interpretation_proof_matrix.csv",
        result.safe_proof_matrix,
        "interpretation proof matrix",
    )
