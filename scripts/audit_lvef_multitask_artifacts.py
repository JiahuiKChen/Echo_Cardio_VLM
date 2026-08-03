#!/usr/bin/env python3
"""Inventory LVEF/multitask artifacts and emit aggregate denominator summaries.

Missing optional SCC inputs are reported as MISSING rather than raising a stack
trace. Identifier-level discrepancies are never written to the aggregate output.
Use the companion overlap and split audits with --restricted-output-dir for
identifier-level review on SCC.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from numbers import Number
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    entity_counts,
    load_tables,
    require_restricted_path,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


ARTIFACT_ARGUMENTS = {
    "public_dicom_records": "public-records",
    "eligible_studies": "eligible-studies",
    "selected_studies": "selected-studies",
    "downloaded_studies": "downloaded-studies",
    "readable_dicoms": "readable-dicoms",
    "cine_candidates": "cine-candidates",
    "extracted_clips": "extracted-clips",
    "clip_embeddings": "clip-embeddings",
    "study_embeddings": "study-embeddings",
    "structured_measurements": "structured-measurements",
    "lvef_labels": "lvef-labels",
    "split_map": "split-map",
    "multitask_panel": "multitask-panel",
}
MISSING_IDENTIFIER_TOKENS = {"null", "none", "nan", "<na>"}
CLIP_LOCATOR_COLUMNS = ("dicom_filepath", "npz_path", "output_path")


def canonical_identifier_series(series: pd.Series) -> pd.Series:
    """Return whitespace-trimmed, missing-aware identifiers with integral numerics normalized."""

    def canonicalize(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        text = str(value).strip()
        if not text or text.casefold() in MISSING_IDENTIFIER_TOKENS:
            return pd.NA
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            return text
        if numeric.is_finite() and numeric == numeric.to_integral_value():
            return str(int(numeric))
        return text

    return series.map(canonicalize).astype("string")


def missing_identifier_mask(series: pd.Series) -> pd.Series:
    """Treat null, empty, and whitespace-only identifier cells as missing."""
    return canonical_identifier_series(series).isna()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for destination, flag in ARTIFACT_ARGUMENTS.items():
        parser.add_argument(
            f"--{flag}",
            dest=destination,
            type=Path,
            action="append",
            help="May be repeated for per-batch manifests from the same stage.",
        )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument(
        "--clip-embedding-component-manifest",
        type=Path,
        action="append",
        default=[],
        help=(
            "Successful source manifest used to construct the merged clip store. Repeat for "
            "Stage D and every batch; do not pass the merged manifest here."
        ),
    )
    parser.add_argument(
        "--clip-component-lineage-complete",
        action="store_true",
        help="Declare that Stage-D plus all batch clip-manifest lineage is complete and auditable.",
    )
    parser.add_argument(
        "--expected-clip-components",
        type=int,
        default=10,
        help="Expected component-manifest count when clip lineage is declared complete (default: 10).",
    )
    parser.add_argument(
        "--require-artifact",
        action="append",
        default=[],
        choices=tuple(ARTIFACT_ARGUMENTS),
        help="Fail with exit 2 if the named artifact is not supplied/readable. May be repeated.",
    )
    return parser.parse_args()


def successful_stage_rows(name: str, frame: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    flag_candidates = {
        "downloaded_studies": ("exists", "download_ok", "downloaded"),
        "readable_dicoms": ("read_ok",),
        "cine_candidates": ("is_multiframe",),
        "extracted_clips": ("write_ok", "extract_ok"),
        "clip_embeddings": ("write_ok", "embedding_ok"),
    }.get(name, ())
    flag = resolve_column(frame, flag_candidates) if flag_candidates else None
    if flag_candidates and flag is None:
        raise ValueError(
            f"{name} manifest is missing a required success flag; expected one of {flag_candidates}"
        )
    if flag is None:
        return frame, None
    values = frame[flag]
    if values.dtype == bool:
        mask = values.fillna(False)
    else:
        mask = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
    return frame[mask].copy(), flag


def stage_quality_counts(
    input_frame: pd.DataFrame, successful_frame: pd.DataFrame, success_flag: str | None
) -> dict[str, Any]:
    study_col = resolve_column(input_frame, STUDY_COLUMNS)
    result: dict[str, Any] = {
        "n_input_rows": int(len(input_frame)),
        "n_input_studies": int(input_frame[study_col].nunique(dropna=True)) if study_col else None,
        "study_success_rule": "AT_LEAST_ONE_SUCCESSFUL_ROW" if success_flag else "ALL_ROWS_INCLUDED",
        "n_studies_all_rows_successful": None,
    }
    if success_flag and study_col:
        input_sizes = input_frame.dropna(subset=[study_col]).groupby(study_col).size()
        success_sizes = successful_frame.dropna(subset=[study_col]).groupby(study_col).size()
        aligned_success = success_sizes.reindex(input_sizes.index, fill_value=0)
        result["n_studies_all_rows_successful"] = int((aligned_success == input_sizes).sum())
    return result


def validate_artifact_schema(name: str, frame: pd.DataFrame, task_prefix: str) -> None:
    resolve_column(frame, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {name}")
    if name != "split_map":
        resolve_column(frame, STUDY_COLUMNS, required=True, label=f"study identifier for {name}")
    if name in {"lvef_labels", "split_map", "multitask_panel"}:
        resolve_column(frame, SPLIT_COLUMNS, required=True, label=f"split for {name}")
    if name == "structured_measurements":
        resolve_column(
            frame,
            ("measurement", "measurement_name", "canonical_measurement"),
            required=True,
            label="structured measurement name",
        )
        resolve_column(
            frame,
            ("result_numeric", "result", "value"),
            required=True,
            label="structured measurement value",
        )
    if name == "lvef_labels":
        resolve_column(
            frame,
            ("lvef", "lvef_value", "target", "result_numeric", "result", "value"),
            required=True,
            label="LVEF value",
        )
        resolve_column(
            frame,
            ("lvef_binary_reduced",),
            required=True,
            label="reduced-LVEF binary label",
        )
    if name == "multitask_panel" and not any(
        str(column).startswith(task_prefix) for column in frame.columns
    ):
        raise ValueError(f"multitask panel has no columns with prefix {task_prefix}")


def inspect_artifact(
    name: str,
    paths: list[Path] | None,
    task_prefix: str = "task__",
    loaded_cache: dict[tuple[Path, ...], pd.DataFrame] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame | None, pd.DataFrame | None]:
    if not paths:
        return {"artifact": name, "status": "NOT_SUPPLIED", "n_files": 0, "n_rows": None, "n_studies": None, "n_subjects": None}, None, None
    expanded = [path.expanduser() for path in paths]
    missing = [path for path in expanded if not path.exists()]
    if missing:
        return {"artifact": name, "status": "MISSING", "n_files": len(expanded), "n_rows": None, "n_studies": None, "n_subjects": None}, None, None
    try:
        cache_key = tuple(path.resolve() for path in expanded)
        if loaded_cache is not None and cache_key in loaded_cache:
            frame = loaded_cache[cache_key]
        else:
            frame = load_tables(expanded)
            if loaded_cache is not None:
                loaded_cache[cache_key] = frame
        validate_artifact_schema(name, frame, task_prefix)
        successful, success_flag = successful_stage_rows(name, frame)
        counts = entity_counts(successful)
        projected_successful = successful
        projected_input = frame
        if name in {
            "public_dicom_records",
            "eligible_studies",
            "selected_studies",
            "downloaded_studies",
            "readable_dicoms",
            "cine_candidates",
            "extracted_clips",
            "clip_embeddings",
            "study_embeddings",
        }:
            subject_col = resolve_column(frame, SUBJECT_COLUMNS, required=True)
            study_col = resolve_column(frame, STUDY_COLUMNS, required=True)
            assert subject_col is not None and study_col is not None
            projected_columns = [subject_col, study_col]
            if name == "selected_studies":
                measurement_id_col = resolve_column(frame, ("measurement_id",))
                if measurement_id_col is not None:
                    projected_columns.append(measurement_id_col)
            projected_successful = successful[projected_columns].copy()
            projected_input = frame[projected_columns].copy()
        return {
            "artifact": name,
            "status": "OK",
            "n_files": len(expanded),
            "success_filter_column": success_flag,
            **stage_quality_counts(frame, successful, success_flag),
            **counts,
        }, projected_successful, projected_input
    except Exception as exc:  # audit inventory must fail gracefully per artifact
        return {
            "artifact": name,
            "status": "ERROR",
            "n_files": len(expanded),
            "n_rows": None,
            "n_studies": None,
            "n_subjects": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }, None, None


def numeric_lvef_subset(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the exact raw rows accepted by the historical LVEF builder.

    ``build_lvef_still_manifest.py`` uses a case-sensitive ``measurement ==
    "lvef"`` comparison and parses the ``result`` column.  Keep this funnel
    diagnostic aligned with that code instead of accepting aliases or a
    precomputed numeric export that the builder never read.
    """
    measurement_col = resolve_column(
        frame,
        ("measurement",),
        required=True,
        label="LVEF source measurement name",
    )
    value_col = resolve_column(
        frame,
        ("result",),
        required=True,
        label="LVEF source numeric value",
    )
    assert measurement_col is not None and value_col is not None
    frame = frame[frame[measurement_col].astype("string").eq("lvef").fillna(False)].copy()
    frame = frame[pd.to_numeric(frame[value_col], errors="coerce").notna()].copy()
    return frame


def historical_selected_lvef_preimage(
    selected_frame: pd.DataFrame, structured_frame: pd.DataFrame
) -> pd.DataFrame:
    """Reconstruct the builder's selected-study LVEF preimage before imaging linkage."""
    selected_subject_col = resolve_column(
        selected_frame, SUBJECT_COLUMNS, required=True, label="selected subject identifier"
    )
    selected_study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected study identifier"
    )
    selected_measurement_col = resolve_column(
        selected_frame, ("measurement_id",), required=True, label="selected measurement identifier"
    )
    structured_subject_col = resolve_column(
        structured_frame, SUBJECT_COLUMNS, required=True, label="structured subject identifier"
    )
    structured_measurement_col = resolve_column(
        structured_frame,
        ("measurement_id",),
        required=True,
        label="structured measurement identifier",
    )
    for required_column in ("measurement", "result"):
        if required_column not in structured_frame.columns:
            raise ValueError(f"structured measurements missing historical builder column {required_column}")
    assert all(
        column is not None
        for column in (
            selected_subject_col,
            selected_study_col,
            selected_measurement_col,
            structured_subject_col,
            structured_measurement_col,
        )
    )
    selected = pd.DataFrame(
        {
            "subject_id": canonical_identifier_series(selected_frame[selected_subject_col]),
            "study_id": canonical_identifier_series(selected_frame[selected_study_col]),
            "measurement_id": canonical_identifier_series(
                selected_frame[selected_measurement_col]
            ),
        }
    ).dropna()
    structured = pd.DataFrame(
        {
            "subject_id": canonical_identifier_series(structured_frame[structured_subject_col]),
            "measurement_id": canonical_identifier_series(
                structured_frame[structured_measurement_col]
            ),
            "lvef": pd.to_numeric(structured_frame["result"], errors="coerce"),
        }
    ).loc[structured_frame["measurement"].astype("string").eq("lvef").fillna(False)]
    structured = structured.dropna().groupby(
        ["subject_id", "measurement_id"], as_index=False
    )["lvef"].median()
    return selected.merge(
        structured,
        how="inner",
        on=["subject_id", "measurement_id"],
        validate="one_to_one",
    )


def selected_cohort_integrity(frame: pd.DataFrame) -> dict[str, Any]:
    subject_col = resolve_column(frame, SUBJECT_COLUMNS, required=True, label="selected subject identifier")
    study_col = resolve_column(frame, STUDY_COLUMNS, required=True, label="selected study identifier")
    assert subject_col is not None and study_col is not None
    normalized_subjects = canonical_identifier_series(frame[subject_col])
    normalized_studies = canonical_identifier_series(frame[study_col])
    missing_subject = normalized_subjects.isna()
    missing_study = normalized_studies.isna()
    missing_either = missing_subject | missing_study
    subject_study = pd.DataFrame(
        {"_subject": normalized_subjects, "_study": normalized_studies}
    ).loc[~missing_either]
    subjects_per_study = subject_study.groupby("_study")["_subject"].nunique(dropna=True)
    studies_per_subject = subject_study.groupby("_subject")["_study"].nunique(dropna=True)
    n_duplicate_study_rows = int(subject_study["_study"].duplicated().sum())
    n_duplicate_subject_study_rows = int(subject_study.duplicated(["_subject", "_study"]).sum())
    n_studies_with_multiple_subjects = int((subjects_per_study > 1).sum())
    n_subjects_with_multiple_studies = int((studies_per_subject > 1).sum())
    valid = not any(
        (
            n_duplicate_study_rows,
            n_duplicate_subject_study_rows,
            n_studies_with_multiple_subjects,
            n_subjects_with_multiple_studies,
            int(missing_either.sum()),
        )
    )
    return {
        "n_rows": int(len(frame)),
        "n_subjects": int(subject_study["_subject"].nunique(dropna=True)),
        "n_studies": int(subject_study["_study"].nunique(dropna=True)),
        "n_rows_missing_subject_id": int(missing_subject.sum()),
        "n_rows_missing_study_id": int(missing_study.sum()),
        "n_rows_missing_subject_or_study": int(missing_either.sum()),
        "n_duplicate_study_rows": n_duplicate_study_rows,
        "n_duplicate_subject_study_rows": n_duplicate_subject_study_rows,
        "n_studies_with_multiple_subjects": n_studies_with_multiple_subjects,
        "n_subjects_with_multiple_studies": n_subjects_with_multiple_studies,
        "one_study_per_subject_valid": valid,
    }


def selected_study_ids(selected_frame: pd.DataFrame) -> set[str]:
    study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected-study identifier"
    )
    assert study_col is not None
    return set(canonical_identifier_series(selected_frame[study_col]).dropna())


def intersect_frame_by_studies(frame: pd.DataFrame, allowed_study_ids: set[str]) -> pd.DataFrame:
    study_col = resolve_column(frame, STUDY_COLUMNS, required=True, label="study identifier")
    assert study_col is not None
    normalized_studies = canonical_identifier_series(frame[study_col])
    return frame.loc[normalized_studies.isin(allowed_study_ids)].copy()


def split_count_rows(name: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    if split_col is None:
        return []
    subject_col = resolve_column(frame, SUBJECT_COLUMNS)
    study_col = resolve_column(frame, STUDY_COLUMNS)
    rows: list[dict[str, Any]] = []
    for split_name, group in frame.groupby(split_col, dropna=False):
        rows.append(
            {
                "artifact": name,
                "split": str(split_name),
                "n_rows": int(len(group)),
                "n_studies": int(group[study_col].nunique()) if study_col else None,
                "n_subjects": int(group[subject_col].nunique()) if subject_col else None,
            }
        )
    return rows


def task_denominator_rows(frame: pd.DataFrame, prefix: str) -> list[dict[str, Any]]:
    task_columns = [column for column in frame.columns if str(column).startswith(prefix)]
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    rows: list[dict[str, Any]] = []
    split_groups = [("all", frame)] if split_col is None else list(frame.groupby(split_col, dropna=False))
    for task in task_columns:
        numeric = pd.to_numeric(frame[task], errors="coerce")
        for split_name, group in split_groups:
            mask = numeric.loc[group.index].notna()
            rows.append({"task": str(task), "split": str(split_name), "n_with_target": int(mask.sum())})
    return rows


def selected_stage_reconciliation_rows(
    selected_frame: pd.DataFrame,
    stage_frames: dict[str, pd.DataFrame],
    input_frames: dict[str, pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    selected_study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected-study identifier"
    )
    assert selected_study_col is not None
    selected_ids = selected_study_ids(selected_frame)
    rows: list[dict[str, Any]] = []
    for stage, frame in stage_frames.items():
        study_col = resolve_column(frame, STUDY_COLUMNS)
        if study_col is None:
            continue
        normalized_stage_values = canonical_identifier_series(frame[study_col])
        stage_ids = set(normalized_stage_values.dropna())
        in_selected_ids = stage_ids & selected_ids
        in_selected_frame = frame[normalized_stage_values.isin(in_selected_ids)].copy()
        subject_col = resolve_column(in_selected_frame, SUBJECT_COLUMNS)
        input_frame = (input_frames or {}).get(stage, frame)
        input_study_col = resolve_column(input_frame, STUDY_COLUMNS)
        n_selected_studies_all_rows_successful: int | None = None
        if input_study_col is not None:
            normalized_input_values = canonical_identifier_series(input_frame[input_study_col])
            selected_input = input_frame[normalized_input_values.isin(selected_ids)].copy()
            selected_input = selected_input.assign(_audit_study=normalized_input_values.loc[selected_input.index])
            in_selected_frame = in_selected_frame.assign(
                _audit_study=normalized_stage_values.loc[in_selected_frame.index]
            )
            input_sizes = selected_input.groupby("_audit_study").size()
            success_sizes = in_selected_frame.groupby("_audit_study").size()
            aligned_success = success_sizes.reindex(input_sizes.index, fill_value=0)
            n_selected_studies_all_rows_successful = int((aligned_success == input_sizes).sum())
        rows.append(
            {
                "stage": stage,
                "n_stage_studies_all": len(stage_ids),
                "n_stage_studies_in_selected": len(in_selected_ids),
                "n_stage_studies_outside_selected": len(stage_ids - selected_ids),
                "n_selected_studies_absent": len(selected_ids - stage_ids),
                "n_selected_studies_all_rows_successful": n_selected_studies_all_rows_successful,
                "n_rows_in_selected": int(len(in_selected_frame)),
                "n_subjects_in_selected": (
                    int(in_selected_frame[subject_col].nunique(dropna=True)) if subject_col else None
                ),
            }
        )
    return rows


def selected_stage_containment(
    selected_frame: pd.DataFrame, stage_frames: dict[str, pd.DataFrame]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    selected_study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected-study identifier"
    )
    assert selected_study_col is not None
    selected_ids = selected_study_ids(selected_frame)
    stage_sets: dict[str, set[str]] = {"selected_studies": selected_ids}
    raw_stage_sets: dict[str, set[str]] = {"selected_studies": selected_ids}
    for stage, frame in stage_frames.items():
        study_col = resolve_column(frame, STUDY_COLUMNS)
        if study_col is not None:
            raw_ids = set(canonical_identifier_series(frame[study_col]).dropna())
            raw_stage_sets[stage] = raw_ids
            stage_sets[stage] = raw_ids & selected_ids
    ordered_pairs = [
        ("selected_studies", "downloaded_studies", "DOWNSTREAM_SUBSET", "SELECTED_INTERSECTION"),
        ("downloaded_studies", "readable_dicoms", "DOWNSTREAM_SUBSET", "SELECTED_INTERSECTION"),
        ("readable_dicoms", "cine_candidates", "DOWNSTREAM_SUBSET", "SELECTED_INTERSECTION"),
        ("cine_candidates", "extracted_clips", "DOWNSTREAM_SUBSET", "SELECTED_INTERSECTION"),
        ("extracted_clips", "clip_embeddings", "DOWNSTREAM_SUBSET", "SELECTED_INTERSECTION"),
        ("clip_embeddings", "study_embeddings", "EQUAL", "SELECTED_INTERSECTION"),
    ]
    if "clip_embeddings" in raw_stage_sets and "study_embeddings" in raw_stage_sets:
        ordered_pairs.append(
            ("clip_embeddings", "study_embeddings", "EQUAL", "ALL_ARTIFACT")
        )
    if "lvef_labels" in stage_sets:
        ordered_pairs.append(
            ("selected_studies", "lvef_labels", "DOWNSTREAM_SUBSET", "ALL_ARTIFACT")
        )
    if "multitask_panel" in stage_sets:
        # The panel builder uses the selected cohort as its reference index and
        # preserves rows even when every task is missing. Missing base rows would
        # bias the natural-missingness audit and therefore fail closed.
        ordered_pairs.append(
            ("selected_studies", "multitask_panel", "EQUAL", "ALL_ARTIFACT")
        )
    aggregate_rows: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, str]] = []
    for upstream, downstream, expected_relation, comparison_scope in ordered_pairs:
        if upstream not in stage_sets or downstream not in stage_sets:
            aggregate_rows.append(
                {
                    "upstream_stage": upstream,
                    "downstream_stage": downstream,
                    "expected_relation": expected_relation,
                    "comparison_scope": comparison_scope,
                    "status": "NOT_EVALUABLE_MISSING_STAGE",
                    "n_upstream_studies_compared": None,
                    "n_downstream_studies_compared": None,
                    "n_downstream_not_upstream": None,
                    "n_upstream_not_downstream": None,
                    "downstream_subset_of_upstream": False,
                    "sets_equal": False,
                    "relation_valid": False,
                }
            )
            continue
        source_sets = raw_stage_sets if comparison_scope == "ALL_ARTIFACT" else stage_sets
        upstream_ids = source_sets[upstream]
        downstream_ids = source_sets[downstream]
        downstream_not_upstream = downstream_ids - upstream_ids
        upstream_not_downstream = upstream_ids - downstream_ids
        is_subset = not downstream_not_upstream
        sets_equal = upstream_ids == downstream_ids
        relation_valid = sets_equal if expected_relation == "EQUAL" else is_subset
        aggregate_rows.append(
            {
                "upstream_stage": upstream,
                "downstream_stage": downstream,
                "expected_relation": expected_relation,
                "comparison_scope": comparison_scope,
                "status": "EVALUATED",
                "n_upstream_studies_compared": len(upstream_ids),
                "n_downstream_studies_compared": len(downstream_ids),
                "n_downstream_not_upstream": len(downstream_not_upstream),
                "n_upstream_not_downstream": len(upstream_not_downstream),
                "downstream_subset_of_upstream": is_subset,
                "sets_equal": sets_equal,
                "relation_valid": relation_valid,
            }
        )
        restricted_rows.extend(
            {
                "transition": f"{upstream}_TO_{downstream}",
                "comparison_scope": comparison_scope,
                "status": "DOWNSTREAM_NOT_IN_UPSTREAM",
                "study_id": identifier,
            }
            for identifier in sorted(downstream_not_upstream)
        )
        if expected_relation == "EQUAL":
            restricted_rows.extend(
                {
                    "transition": f"{upstream}_TO_{downstream}",
                    "comparison_scope": comparison_scope,
                    "status": "UPSTREAM_NOT_IN_DOWNSTREAM",
                    "study_id": identifier,
                }
                for identifier in sorted(upstream_not_downstream)
            )
    return aggregate_rows, restricted_rows


def selected_stage_subject_mapping(
    selected_frame: pd.DataFrame, stage_frames: dict[str, pd.DataFrame]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Compare each selected study's subject with every supplied stage carrying both IDs."""
    if not selected_cohort_integrity(selected_frame)["one_study_per_subject_valid"]:
        raise ValueError("Selected cohort identifier integrity is invalid")
    selected_subject_col = resolve_column(
        selected_frame, SUBJECT_COLUMNS, required=True, label="selected subject identifier"
    )
    selected_study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected study identifier"
    )
    assert selected_subject_col is not None and selected_study_col is not None
    selected_mapping = dict(
        zip(
            canonical_identifier_series(selected_frame[selected_study_col]),
            canonical_identifier_series(selected_frame[selected_subject_col]),
            strict=True,
        )
    )

    aggregate_rows: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, str]] = []
    for stage, frame in stage_frames.items():
        subject_col = resolve_column(frame, SUBJECT_COLUMNS)
        study_col = resolve_column(frame, STUDY_COLUMNS)
        if subject_col is None or study_col is None:
            continue
        stage_subjects = canonical_identifier_series(frame[subject_col])
        stage_studies = canonical_identifier_series(frame[study_col])
        missing_either = stage_subjects.isna() | stage_studies.isna()
        valid_stage_ids = pd.DataFrame(
            {"_subject": stage_subjects, "_study": stage_studies}
        ).loc[~missing_either]
        subjects_per_stage_study = valid_stage_ids.groupby("_study")["_subject"].nunique(
            dropna=False
        )
        conflicted_stage_studies = set(
            subjects_per_stage_study[subjects_per_stage_study > 1].index.astype(str)
        )
        selected_row_mask = stage_studies.isin(selected_mapping)
        expected_subjects = stage_studies.map(selected_mapping).astype("string")
        mismatch_mask = selected_row_mask & (
            stage_subjects.isna() | stage_subjects.ne(expected_subjects).fillna(True)
        )
        mismatch_studies = set(stage_studies.loc[mismatch_mask].dropna())
        compared_studies = set(stage_studies.loc[selected_row_mask].dropna())
        aggregate_rows.append(
            {
                "stage": stage,
                "n_stage_rows": int(len(frame)),
                "n_stage_rows_missing_subject_or_study": int(missing_either.sum()),
                "n_stage_studies_with_multiple_subjects": len(conflicted_stage_studies),
                "n_stage_rows_for_selected_studies": int(selected_row_mask.sum()),
                "n_selected_studies_compared": len(compared_studies),
                "n_selected_study_rows_subject_mapping_mismatch": int(mismatch_mask.sum()),
                "n_selected_studies_subject_mapping_mismatch": len(mismatch_studies),
                "stage_identifier_integrity_valid": (
                    not missing_either.any() and not conflicted_stage_studies
                ),
                "selected_study_subject_mapping_valid": not mismatch_studies,
                "stage_mapping_audit_valid": (
                    not missing_either.any()
                    and not conflicted_stage_studies
                    and not mismatch_studies
                ),
            }
        )
        restricted_rows.extend(
            {
                "stage": stage,
                "warning_type": "SELECTED_STUDY_SUBJECT_MAPPING_MISMATCH",
                "study_id": identifier,
            }
            for identifier in sorted(mismatch_studies)
        )
        restricted_rows.extend(
            {
                "stage": stage,
                "warning_type": "STAGE_STUDY_MAPPED_TO_MULTIPLE_SUBJECTS",
                "study_id": identifier,
            }
            for identifier in sorted(conflicted_stage_studies)
        )
    return aggregate_rows, restricted_rows


def reconcile_lvef_label_provenance(
    selected_frame: pd.DataFrame,
    structured_frame: pd.DataFrame,
    lvef_manifest: pd.DataFrame,
    *,
    value_tolerance: float = 1e-12,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Rebuild selected-study LVEF labels with the historical manifest builder semantics."""
    selected_subject_col = resolve_column(
        selected_frame, SUBJECT_COLUMNS, required=True, label="selected subject identifier"
    )
    selected_study_col = resolve_column(
        selected_frame, STUDY_COLUMNS, required=True, label="selected study identifier"
    )
    selected_measurement_col = resolve_column(
        selected_frame, ("measurement_id",), required=True, label="selected measurement identifier"
    )
    structured_subject_col = resolve_column(
        structured_frame, SUBJECT_COLUMNS, required=True, label="structured subject identifier"
    )
    structured_measurement_id_col = resolve_column(
        structured_frame,
        ("measurement_id",),
        required=True,
        label="structured measurement identifier",
    )
    structured_name_col = resolve_column(
        structured_frame,
        ("measurement",),
        required=True,
        label="structured measurement name",
    )
    structured_value_col = resolve_column(
        structured_frame,
        ("result",),
        required=True,
        label="structured LVEF value",
    )
    manifest_study_col = resolve_column(
        lvef_manifest, STUDY_COLUMNS, required=True, label="LVEF manifest study identifier"
    )
    manifest_value_col = resolve_column(
        lvef_manifest,
        ("lvef", "lvef_value", "target", "result_numeric", "result", "value"),
        required=True,
        label="LVEF manifest value",
    )
    manifest_binary_col = resolve_column(
        lvef_manifest,
        ("lvef_binary_reduced",),
        required=True,
        label="reduced-LVEF binary label",
    )
    assert all(
        column is not None
        for column in (
            selected_subject_col,
            selected_study_col,
            selected_measurement_col,
            structured_subject_col,
            structured_measurement_id_col,
            structured_name_col,
            structured_value_col,
            manifest_study_col,
            manifest_value_col,
            manifest_binary_col,
        )
    )

    selected = pd.DataFrame(
        {
            "_subject": canonical_identifier_series(selected_frame[selected_subject_col]),
            "_study": canonical_identifier_series(selected_frame[selected_study_col]),
            "_measurement": canonical_identifier_series(selected_frame[selected_measurement_col]),
        }
    )
    n_selected_rows_missing_measurement_id = int(selected["_measurement"].isna().sum())
    selected = selected.dropna(subset=["_subject", "_study", "_measurement"])

    # This intentionally matches build_lvef_still_manifest.py: exact name == "lvef",
    # numeric result, then median by (subject_id, measurement_id).
    structured_lvef_mask = structured_frame[structured_name_col].astype("string").eq("lvef")
    structured = pd.DataFrame(
        {
            "_subject": canonical_identifier_series(structured_frame[structured_subject_col]),
            "_measurement": canonical_identifier_series(
                structured_frame[structured_measurement_id_col]
            ),
            "_lvef": pd.to_numeric(structured_frame[structured_value_col], errors="coerce"),
        }
    ).loc[structured_lvef_mask.fillna(False)]
    structured = structured.dropna(subset=["_subject", "_measurement", "_lvef"])
    structured = (
        structured.groupby(["_subject", "_measurement"], as_index=False)["_lvef"].median()
    )
    derived = selected.merge(
        structured,
        how="inner",
        on=["_subject", "_measurement"],
        validate="one_to_one",
    )[["_study", "_lvef"]]

    selected_ids = set(selected["_study"])
    manifest_studies = canonical_identifier_series(lvef_manifest[manifest_study_col])
    manifest_values = pd.to_numeric(lvef_manifest[manifest_value_col], errors="coerce")
    manifest_binary = pd.to_numeric(lvef_manifest[manifest_binary_col], errors="coerce")
    manifest_rows = pd.DataFrame(
        {
            "_study": manifest_studies,
            "_lvef_manifest": manifest_values,
            "_binary_manifest": manifest_binary,
            "_source_row": lvef_manifest.index.astype(str),
        }
    )
    missing_study_or_lvef = manifest_rows[["_study", "_lvef_manifest"]].isna().any(axis=1)
    invalid_binary = manifest_rows["_binary_manifest"].isna() | ~manifest_rows[
        "_binary_manifest"
    ].isin([0.0, 1.0])
    comparable_binary = ~(missing_study_or_lvef | invalid_binary)
    expected_binary = (manifest_rows["_lvef_manifest"] < 40.0).astype("Int64")
    binary_threshold_mismatch = comparable_binary & manifest_rows["_binary_manifest"].ne(
        expected_binary.astype(float)
    )
    manifest_all_numeric = pd.DataFrame(
        {"_study": manifest_studies, "_lvef_manifest": manifest_values}
    ).dropna(subset=["_study", "_lvef_manifest"])
    n_manifest_lvef_studies_all_artifact = int(manifest_all_numeric["_study"].nunique())
    manifest_conflicts_all = manifest_all_numeric.groupby("_study")["_lvef_manifest"].nunique()
    conflict_ids_all = set(
        manifest_conflicts_all[manifest_conflicts_all > 1].index.astype(str)
    )
    valid_binary_rows = manifest_rows.loc[
        manifest_rows["_study"].notna() & ~invalid_binary
    ]
    binary_conflicts_all = valid_binary_rows.groupby("_study")["_binary_manifest"].nunique()
    binary_conflict_ids_all = set(
        binary_conflicts_all[binary_conflicts_all > 1].index.astype(str)
    )
    manifest_selected_raw = manifest_all_numeric.loc[
        manifest_all_numeric["_study"].isin(selected_ids)
    ]
    manifest_conflicts_selected = manifest_selected_raw.groupby("_study")[
        "_lvef_manifest"
    ].nunique()
    conflict_ids_selected = set(
        manifest_conflicts_selected[manifest_conflicts_selected > 1].index.astype(str)
    )
    binary_conflict_ids_selected = binary_conflict_ids_all & selected_ids
    manifest_selected = (
        manifest_selected_raw.groupby("_study", as_index=False)["_lvef_manifest"].median()
    )

    derived_by_study = derived.groupby("_study", as_index=False)["_lvef"].median()
    derived_ids = set(derived_by_study["_study"])
    manifest_ids = set(manifest_selected["_study"])
    structured_only = derived_ids - manifest_ids
    manifest_only = manifest_ids - derived_ids
    paired = derived_by_study.merge(manifest_selected, how="inner", on="_study", validate="one_to_one")
    value_mismatch_mask = (
        (paired["_lvef"] - paired["_lvef_manifest"]).abs() > float(value_tolerance)
    )
    value_mismatch_ids = set(paired.loc[value_mismatch_mask, "_study"])
    manifest_subset = not manifest_only
    values_match = not value_mismatch_ids
    result = {
        "status": "EVALUATED",
        "aggregation_semantics": "EXACT_LVEF_NAME_NUMERIC_MEDIAN_BY_SUBJECT_MEASUREMENT_ID",
        "value_tolerance": float(value_tolerance),
        "n_selected_rows_missing_measurement_id": n_selected_rows_missing_measurement_id,
        "n_structured_selected_lvef_studies": len(derived_ids),
        "n_manifest_lvef_studies_all_artifact_diagnostic": n_manifest_lvef_studies_all_artifact,
        "n_manifest_rows_missing_study_or_lvef": int(missing_study_or_lvef.sum()),
        "n_manifest_rows_invalid_binary_label": int(invalid_binary.sum()),
        "n_manifest_rows_binary_threshold_mismatch": int(binary_threshold_mismatch.sum()),
        "n_manifest_lvef_conflict_studies_all_artifact_diagnostic": len(conflict_ids_all),
        "n_manifest_binary_conflict_studies_all_artifact_diagnostic": len(
            binary_conflict_ids_all
        ),
        "n_manifest_selected_lvef_studies": len(manifest_ids),
        "n_manifest_selected_lvef_conflict_studies": len(conflict_ids_selected),
        "n_manifest_selected_binary_conflict_studies": len(binary_conflict_ids_selected),
        "n_studies_compared": int(len(paired)),
        "n_structured_selected_only_studies": len(structured_only),
        "n_manifest_selected_only_studies": len(manifest_only),
        "n_lvef_value_mismatch_studies": len(value_mismatch_ids),
        "study_sets_identical": derived_ids == manifest_ids,
        "manifest_selected_studies_subset_of_structured_derivation": manifest_subset,
        "lvef_values_match_on_intersection": values_match,
        "binary_threshold": 40.0,
        "manifest_binary_labels_valid_and_threshold_consistent": (
            not invalid_binary.any()
            and not binary_threshold_mismatch.any()
            and not binary_conflict_ids_all
        ),
        "label_provenance_valid": (
            n_selected_rows_missing_measurement_id == 0
            and not missing_study_or_lvef.any()
            and not invalid_binary.any()
            and not binary_threshold_mismatch.any()
            and not conflict_ids_selected
            and not binary_conflict_ids_selected
            and manifest_subset
            and values_match
        ),
    }
    restricted_rows = [
        {
            "warning_type": "STRUCTURED_SELECTED_LVEF_ABSENT_FROM_MANIFEST",
            "study_id": identifier,
            "source_row": "",
        }
        for identifier in sorted(structured_only)
    ]
    restricted_rows.extend(
        {
            "warning_type": "MANIFEST_LVEF_ABSENT_FROM_STRUCTURED_SELECTED_DERIVATION",
            "study_id": identifier,
            "source_row": "",
        }
        for identifier in sorted(manifest_only)
    )
    restricted_rows.extend(
        {"warning_type": "LVEF_VALUE_MISMATCH", "study_id": identifier, "source_row": ""}
        for identifier in sorted(value_mismatch_ids)
    )
    restricted_rows.extend(
        {
            "warning_type": "CONFLICTING_LVEF_VALUES_WITHIN_MANIFEST_STUDY",
            "study_id": identifier,
            "source_row": "",
        }
        for identifier in sorted(conflict_ids_selected)
    )
    restricted_rows.extend(
        {
            "warning_type": "CONFLICTING_BINARY_LABELS_WITHIN_MANIFEST_STUDY",
            "study_id": identifier,
            "source_row": "",
        }
        for identifier in sorted(binary_conflict_ids_selected)
    )
    for index in manifest_rows.index[
        missing_study_or_lvef | invalid_binary | binary_threshold_mismatch
    ]:
        if bool(missing_study_or_lvef.loc[index]):
            warning_type = "MANIFEST_ROW_MISSING_STUDY_OR_NUMERIC_LVEF"
        elif bool(invalid_binary.loc[index]):
            warning_type = "MANIFEST_ROW_INVALID_BINARY_LABEL"
        else:
            warning_type = "MANIFEST_ROW_BINARY_THRESHOLD_MISMATCH"
        restricted_rows.append(
            {
                "warning_type": warning_type,
                "study_id": (
                    "" if pd.isna(manifest_rows.at[index, "_study"]) else str(manifest_rows.at[index, "_study"])
                ),
                "source_row": str(manifest_rows.at[index, "_source_row"]),
            }
        )
    return result, restricted_rows


def _canonical_manifest_scalar(value: object) -> str:
    if pd.isna(value):
        return "<MISSING>"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Number):
        numeric = Decimal(str(value))
        if numeric.is_finite():
            if numeric == numeric.to_integral_value():
                return str(int(numeric))
            return format(numeric.normalize(), "f")
    return str(value)


def _successful_clip_manifest(frame: pd.DataFrame) -> pd.DataFrame:
    successful, _ = successful_stage_rows("clip_embeddings", frame)
    lower_columns = [str(column).lower() for column in successful.columns]
    if len(lower_columns) != len(set(lower_columns)):
        raise ValueError("Clip manifest has duplicate case-insensitive column names")
    return successful.rename(columns=dict(zip(successful.columns, lower_columns, strict=True))).copy()


def _prepare_clip_manifest_keys(
    frame: pd.DataFrame, locator_columns: tuple[str, ...]
) -> pd.DataFrame:
    subject_col = resolve_column(
        frame, SUBJECT_COLUMNS, required=True, label="clip-manifest subject identifier"
    )
    study_col = resolve_column(
        frame, STUDY_COLUMNS, required=True, label="clip-manifest study identifier"
    )
    assert subject_col is not None and study_col is not None
    work = frame.copy()
    work["_audit_subject"] = canonical_identifier_series(work[subject_col])
    work["_audit_study"] = canonical_identifier_series(work[study_col])
    for column in locator_columns:
        values = work[column].astype("string").str.strip()
        work[f"_audit_locator_{column}"] = values.mask(
            values.isna() | values.eq("") | values.str.casefold().isin(MISSING_IDENTIFIER_TOKENS)
        )
    key_columns = [
        "_audit_subject",
        "_audit_study",
        *(f"_audit_locator_{column}" for column in locator_columns),
    ]
    work["_audit_key_missing"] = work[key_columns].isna().any(axis=1)
    work["_audit_clip_key"] = [
        tuple("<MISSING>" if pd.isna(value) else str(value) for value in values)
        for values in work[key_columns].itertuples(index=False, name=None)
    ]
    return work


def audit_merged_clip_manifest_union(
    merged_manifest: pd.DataFrame,
    component_manifests: list[pd.DataFrame],
    *,
    expected_components: int = 10,
    component_arguments_unique: bool = True,
    lineage_complete: bool = True,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Verify that merged successful clip rows exactly equal the component-manifest union."""
    if not lineage_complete:
        return {
            "status": "NOT_EVALUATED_INCOMPLETE_COMPONENT_LINEAGE",
            "lineage_complete_declared": False,
            "n_expected_component_manifests": int(expected_components),
            "n_component_manifests": len(component_manifests),
            "component_union_provenance_valid": None,
        }, []
    if expected_components <= 0:
        raise ValueError("expected_components must be positive")

    merged_success = _successful_clip_manifest(merged_manifest)
    component_success = [_successful_clip_manifest(frame) for frame in component_manifests]
    all_frames = [merged_success, *component_success]
    shared_locators = tuple(
        column for column in CLIP_LOCATOR_COLUMNS if all(column in frame.columns for frame in all_frames)
    )
    if not shared_locators:
        raise ValueError(
            "Merged/component clip manifests have no shared stable locator column; expected "
            f"one of {CLIP_LOCATOR_COLUMNS}"
        )

    prepared_merged = _prepare_clip_manifest_keys(merged_success, shared_locators)
    prepared_components = [
        _prepare_clip_manifest_keys(frame, shared_locators) for frame in component_success
    ]
    if prepared_components:
        prepared_source = pd.concat(prepared_components, ignore_index=True, sort=False)
    else:
        prepared_source = pd.DataFrame(columns=prepared_merged.columns)

    source_key_counter = Counter(prepared_source["_audit_clip_key"])
    merged_key_counter = Counter(prepared_merged["_audit_clip_key"])
    source_keys = set(source_key_counter)
    merged_keys = set(merged_key_counter)
    source_only_keys = source_keys - merged_keys
    merged_only_keys = merged_keys - source_keys
    key_count_mismatches = {
        key
        for key in source_keys | merged_keys
        if source_key_counter[key] != merged_key_counter[key]
    }

    excluded_payload_columns = {"embedding_idx"}
    payload_columns = sorted(
        (
            set(prepared_source.columns)
            | set(prepared_merged.columns)
        )
        - excluded_payload_columns
        - {column for column in set(prepared_source.columns) | set(prepared_merged.columns) if column.startswith("_audit_")}
    )
    source_payload_frame = prepared_source.reindex(columns=payload_columns)
    merged_payload_frame = prepared_merged.reindex(columns=payload_columns)
    source_payload_by_key: defaultdict[tuple[str, ...], Counter[tuple[str, ...]]] = defaultdict(Counter)
    merged_payload_by_key: defaultdict[tuple[str, ...], Counter[tuple[str, ...]]] = defaultdict(Counter)
    for key, values in zip(
        prepared_source["_audit_clip_key"],
        source_payload_frame.itertuples(index=False, name=None),
        strict=True,
    ):
        source_payload_by_key[key][tuple(_canonical_manifest_scalar(value) for value in values)] += 1
    for key, values in zip(
        prepared_merged["_audit_clip_key"],
        merged_payload_frame.itertuples(index=False, name=None),
        strict=True,
    ):
        merged_payload_by_key[key][tuple(_canonical_manifest_scalar(value) for value in values)] += 1
    payload_mismatch_keys = {
        key
        for key in source_keys & merged_keys
        if source_payload_by_key[key] != merged_payload_by_key[key]
    }

    source_subjects = set(prepared_source["_audit_subject"].dropna())
    merged_subjects = set(prepared_merged["_audit_subject"].dropna())
    source_studies = set(prepared_source["_audit_study"].dropna())
    merged_studies = set(prepared_merged["_audit_study"].dropna())
    source_pairs = set(
        prepared_source.dropna(subset=["_audit_subject", "_audit_study"])[
            ["_audit_subject", "_audit_study"]
        ].itertuples(index=False, name=None)
    )
    merged_pairs = set(
        prepared_merged.dropna(subset=["_audit_subject", "_audit_study"])[
            ["_audit_subject", "_audit_study"]
        ].itertuples(index=False, name=None)
    )
    source_mapping_valid = bool(
        prepared_source.dropna(subset=["_audit_study"])
        .groupby("_audit_study")["_audit_subject"]
        .nunique(dropna=False)
        .le(1)
        .all()
    )
    merged_mapping_valid = bool(
        prepared_merged.dropna(subset=["_audit_study"])
        .groupby("_audit_study")["_audit_subject"]
        .nunique(dropna=False)
        .le(1)
        .all()
    )

    component_count_matches = len(component_manifests) == expected_components
    source_duplicate_excess = sum(max(count - 1, 0) for count in source_key_counter.values())
    merged_duplicate_excess = sum(max(count - 1, 0) for count in merged_key_counter.values())
    key_sets_equal = source_keys == merged_keys
    key_multisets_equal = source_key_counter == merged_key_counter
    payloads_equal = not payload_mismatch_keys
    row_counts_equal = len(prepared_source) == len(prepared_merged)
    subject_sets_equal = source_subjects == merged_subjects
    study_sets_equal = source_studies == merged_studies
    subject_study_pairs_equal = source_pairs == merged_pairs
    identifiers_complete = bool(
        not prepared_source["_audit_key_missing"].any()
        and not prepared_merged["_audit_key_missing"].any()
    )
    provenance_valid = bool(
        component_count_matches
        and component_arguments_unique
        and row_counts_equal
        and identifiers_complete
        and source_duplicate_excess == 0
        and merged_duplicate_excess == 0
        and key_sets_equal
        and key_multisets_equal
        and payloads_equal
        and subject_sets_equal
        and study_sets_equal
        and subject_study_pairs_equal
        and source_mapping_valid
        and merged_mapping_valid
    )
    result = {
        "status": "EVALUATED",
        "lineage_complete_declared": True,
        "n_expected_component_manifests": int(expected_components),
        "n_component_manifests": len(component_manifests),
        "component_manifest_count_matches_expected": component_count_matches,
        "component_manifest_arguments_unique": bool(component_arguments_unique),
        "clip_key_locator_schema": "+".join(shared_locators),
        "n_payload_columns_compared_excluding_embedding_index": len(payload_columns),
        "n_source_success_rows": int(len(prepared_source)),
        "n_merged_success_rows": int(len(prepared_merged)),
        "success_row_counts_equal": row_counts_equal,
        "n_source_unique_clip_keys": len(source_keys),
        "n_merged_unique_clip_keys": len(merged_keys),
        "n_source_duplicate_clip_key_rows": int(source_duplicate_excess),
        "n_merged_duplicate_clip_key_rows": int(merged_duplicate_excess),
        "n_source_only_clip_keys": len(source_only_keys),
        "n_merged_only_clip_keys": len(merged_only_keys),
        "n_clip_key_multiplicity_mismatches": len(key_count_mismatches),
        "clip_key_sets_equal": key_sets_equal,
        "clip_key_multisets_equal": key_multisets_equal,
        "n_clip_keys_with_row_payload_mismatch": len(payload_mismatch_keys),
        "row_payload_multisets_equal": payloads_equal,
        "n_source_subjects": len(source_subjects),
        "n_merged_subjects": len(merged_subjects),
        "subject_sets_equal": subject_sets_equal,
        "n_source_studies": len(source_studies),
        "n_merged_studies": len(merged_studies),
        "study_sets_equal": study_sets_equal,
        "subject_study_pair_sets_equal": subject_study_pairs_equal,
        "source_study_subject_mapping_valid": source_mapping_valid,
        "merged_study_subject_mapping_valid": merged_mapping_valid,
        "clip_keys_complete": identifiers_complete,
        "component_union_provenance_valid": provenance_valid,
    }

    restricted: set[tuple[str, str, str]] = set()

    def add_key_warning(warning_type: str, key: tuple[str, ...]) -> None:
        study_id = "" if len(key) < 2 or key[1] == "<MISSING>" else key[1]
        locator_key = json.dumps(list(key[2:]), separators=(",", ":"))
        restricted.add((warning_type, study_id, locator_key))

    for key in source_only_keys:
        add_key_warning("SOURCE_CLIP_KEY_NOT_IN_MERGED", key)
    for key in merged_only_keys:
        add_key_warning("MERGED_CLIP_KEY_NOT_IN_SOURCE_UNION", key)
    for key in key_count_mismatches:
        warning_type = (
            "SOURCE_CLIP_KEY_MULTIPLICITY_EXCESS"
            if source_key_counter[key] > merged_key_counter[key]
            else "MERGED_CLIP_KEY_MULTIPLICITY_EXCESS"
        )
        add_key_warning(warning_type, key)
    for key in payload_mismatch_keys:
        add_key_warning("CLIP_ROW_PAYLOAD_MISMATCH", key)
    for key, count in source_key_counter.items():
        if count > 1:
            add_key_warning("DUPLICATE_CLIP_KEY_IN_SOURCE_COMPONENTS", key)
    for key, count in merged_key_counter.items():
        if count > 1:
            add_key_warning("DUPLICATE_CLIP_KEY_IN_MERGED_MANIFEST", key)
    for key in prepared_source.loc[
        prepared_source["_audit_key_missing"], "_audit_clip_key"
    ]:
        add_key_warning("SOURCE_ROW_MISSING_CLIP_KEY_FIELD", key)
    for key in prepared_merged.loc[
        prepared_merged["_audit_key_missing"], "_audit_clip_key"
    ]:
        add_key_warning("MERGED_ROW_MISSING_CLIP_KEY_FIELD", key)
    for _, study_id in source_pairs - merged_pairs:
        restricted.add(("SOURCE_SUBJECT_STUDY_PAIR_NOT_IN_MERGED", str(study_id), ""))
    for _, study_id in merged_pairs - source_pairs:
        restricted.add(("MERGED_SUBJECT_STUDY_PAIR_NOT_IN_SOURCE_UNION", str(study_id), ""))
    restricted_rows = [
        {"warning_type": warning_type, "study_id": study_id, "clip_key": clip_key}
        for warning_type, study_id, clip_key in sorted(restricted)
    ]
    return result, restricted_rows


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory_rows: list[dict[str, Any]] = []
    restricted_warning_rows: list[dict[str, str]] = []
    frames: dict[str, pd.DataFrame] = {}
    input_frames: dict[str, pd.DataFrame] = {}
    loaded_cache: dict[tuple[Path, ...], pd.DataFrame] = {}
    for name in ARTIFACT_ARGUMENTS:
        row, frame, input_frame = inspect_artifact(
            name,
            getattr(args, name),
            args.task_prefix,
            loaded_cache,
        )
        if name in args.require_artifact and row["status"] == "NOT_SUPPLIED":
            row["status"] = "REQUIRED_NOT_SUPPLIED"
        inventory_rows.append(row)
        if row["status"] in {"MISSING", "ERROR", "REQUIRED_NOT_SUPPLIED"}:
            supplied_paths = getattr(args, name)
            restricted_warning_rows.append(
                {
                    "artifact": name,
                    "status": str(row["status"]),
                    "supplied_path": ";".join(str(path) for path in supplied_paths or []),
                    "error_type": str(row.get("error_type", "")),
                    "error_message": str(row.get("error_message", "")),
                }
            )
        if frame is not None:
            frames[name] = frame
        if input_frame is not None:
            input_frames[name] = input_frame
    loaded_cache.clear()

    clip_component_restricted_rows: list[dict[str, str]] = []
    clip_component_input_blocked = False
    component_paths = [path.expanduser() for path in args.clip_embedding_component_manifest]
    component_path_keys = [str(path.resolve()) for path in component_paths]
    component_arguments_unique = len(component_path_keys) == len(set(component_path_keys))
    if args.clip_component_lineage_complete:
        merged_paths = args.clip_embeddings or []
        missing_components = sum(not path.is_file() for path in component_paths)
        missing_merged = sum(not path.is_file() for path in merged_paths)
        input_checks_passed = bool(
            args.expected_clip_components > 0
            and len(component_paths) == args.expected_clip_components
            and component_arguments_unique
            and len(merged_paths) == 1
            and missing_components == 0
            and missing_merged == 0
        )
        if not input_checks_passed:
            clip_component_input_blocked = True
            clip_component_result: dict[str, Any] = {
                "status": "BLOCKED_INVALID_OR_INCOMPLETE_COMPONENT_INPUT",
                "lineage_complete_declared": True,
                "n_expected_component_manifests": int(args.expected_clip_components),
                "n_component_manifests": len(component_paths),
                "component_manifest_count_matches_expected": (
                    len(component_paths) == args.expected_clip_components
                ),
                "component_manifest_arguments_unique": component_arguments_unique,
                "n_merged_manifest_arguments": len(merged_paths),
                "n_missing_component_manifests": int(missing_components),
                "n_missing_merged_manifests": int(missing_merged),
                "component_union_provenance_valid": False,
            }
        else:
            try:
                merged_manifest = load_tables(merged_paths)
                component_manifests = [load_tables([path]) for path in component_paths]
                clip_component_result, clip_component_restricted_rows = (
                    audit_merged_clip_manifest_union(
                        merged_manifest,
                        component_manifests,
                        expected_components=args.expected_clip_components,
                        component_arguments_unique=component_arguments_unique,
                        lineage_complete=True,
                    )
                )
            except Exception as exc:
                clip_component_input_blocked = True
                clip_component_result = {
                    "status": "BLOCKED_COMPONENT_SCHEMA_OR_LOAD_ERROR",
                    "lineage_complete_declared": True,
                    "n_expected_component_manifests": int(args.expected_clip_components),
                    "n_component_manifests": len(component_paths),
                    "error_type": type(exc).__name__,
                    "component_union_provenance_valid": False,
                }
    elif component_paths:
        clip_component_result = {
            "status": "NOT_EVALUATED_INCOMPLETE_COMPONENT_LINEAGE",
            "lineage_complete_declared": False,
            "n_expected_component_manifests": int(args.expected_clip_components),
            "n_component_manifests": len(component_paths),
            "component_union_provenance_valid": None,
        }
    else:
        clip_component_result = {
            "status": "NOT_REQUESTED",
            "lineage_complete_declared": False,
            "n_expected_component_manifests": int(args.expected_clip_components),
            "n_component_manifests": 0,
            "component_union_provenance_valid": None,
        }

    denominator_rows: list[dict[str, Any]] = []
    for row in inventory_rows:
        denominator_rows.append(
            {
                "stage": row["artifact"],
                "scope": "ALL_ARTIFACT_ROWS_NOT_SELECTED_FUNNEL",
                "status": row["status"],
                "n_files": row.get("n_files"),
                "success_filter_column": row.get("success_filter_column"),
                "study_success_rule": row.get("study_success_rule"),
                "n_input_rows": row.get("n_input_rows"),
                "n_input_studies": row.get("n_input_studies"),
                "n_studies_all_rows_successful": row.get("n_studies_all_rows_successful"),
                "n_rows": row.get("n_rows"),
                "n_studies": row.get("n_studies"),
                "n_subjects": row.get("n_subjects"),
            }
        )

    selected_integrity: dict[str, Any] | None = None
    selected_valid = False
    selected_ids: set[str] = set()
    if "selected_studies" in frames:
        selected_integrity = selected_cohort_integrity(frames["selected_studies"])
        selected_valid = bool(selected_integrity["one_study_per_subject_valid"])
        if selected_valid:
            selected_ids = selected_study_ids(frames["selected_studies"])

    if "structured_measurements" in frames:
        lvef_preimage_all = numeric_lvef_subset(frames["structured_measurements"])
        counts = entity_counts(lvef_preimage_all)
        denominator_rows.append(
            {
                "stage": "numeric_lvef_all_artifact_diagnostic",
                "scope": "DIAGNOSTIC_ALL_STRUCTURED_ARTIFACT_ROWS_NOT_SELECTED_FUNNEL",
                "status": "DERIVED",
                **counts,
            }
        )
        frames["numeric_lvef_all_artifact_diagnostic"] = lvef_preimage_all

        if selected_valid:
            selected_lvef = historical_selected_lvef_preimage(
                frames["selected_studies"], frames["structured_measurements"]
            )
            counts = entity_counts(selected_lvef)
            denominator_rows.append(
                {
                    "stage": "selected_numeric_lvef_before_imaging",
                    "scope": "SELECTED_STUDY_INTERSECTION",
                    "status": "DERIVED",
                    **counts,
                }
            )
            frames["selected_numeric_lvef_before_imaging"] = selected_lvef

            if "study_embeddings" in frames:
                embedding_study_col = resolve_column(
                    frames["study_embeddings"], STUDY_COLUMNS, required=True
                )
                assert embedding_study_col is not None
                embedded_selected_ids = (
                    set(
                        canonical_identifier_series(
                            frames["study_embeddings"][embedding_study_col]
                        ).dropna()
                    )
                    & selected_ids
                )
                linked = intersect_frame_by_studies(selected_lvef, embedded_selected_ids)
                counts = entity_counts(linked)
                denominator_rows.append(
                    {
                        "stage": "selected_numeric_lvef_plus_embedding",
                        "scope": "SELECTED_LABEL_EMBEDDING_INTERSECTION",
                        "status": "DERIVED",
                        **counts,
                    }
                )
                frames["selected_numeric_lvef_plus_embedding"] = linked

    if selected_valid and "lvef_labels" in frames:
        manifest_value_col = resolve_column(
            frames["lvef_labels"],
            ("lvef", "lvef_value", "target", "result_numeric", "result", "value"),
            required=True,
            label="LVEF manifest value",
        )
        assert manifest_value_col is not None
        numeric_manifest = frames["lvef_labels"].loc[
            pd.to_numeric(frames["lvef_labels"][manifest_value_col], errors="coerce").notna()
        ]
        selected_manifest = intersect_frame_by_studies(numeric_manifest, selected_ids)
        denominator_rows.append(
            {
                "stage": "selected_numeric_lvef_manifest",
                "scope": "SELECTED_STUDY_INTERSECTION_OF_LVEF_MANIFEST",
                "status": "DERIVED",
                **entity_counts(selected_manifest),
            }
        )
        frames["selected_numeric_lvef_manifest"] = selected_manifest
        if "study_embeddings" in frames:
            embedding_study_col = resolve_column(
                frames["study_embeddings"], STUDY_COLUMNS, required=True
            )
            assert embedding_study_col is not None
            embedded_selected_ids = (
                set(
                    canonical_identifier_series(
                        frames["study_embeddings"][embedding_study_col]
                    ).dropna()
                )
                & selected_ids
            )
            selected_manifest_linked = intersect_frame_by_studies(
                selected_manifest, embedded_selected_ids
            )
            denominator_rows.append(
                {
                    "stage": "selected_numeric_lvef_manifest_plus_embedding",
                    "scope": "SELECTED_MANIFEST_LABEL_EMBEDDING_INTERSECTION",
                    "status": "DERIVED",
                    **entity_counts(selected_manifest_linked),
                }
            )
            frames["selected_numeric_lvef_manifest_plus_embedding"] = selected_manifest_linked

    selected_reconciliation_rows: list[dict[str, Any]] = []
    selected_containment_rows: list[dict[str, Any]] = []
    selected_containment_restricted_rows: list[dict[str, str]] = []
    selected_mapping_rows: list[dict[str, Any]] = []
    selected_mapping_restricted_rows: list[dict[str, str]] = []
    lvef_provenance: dict[str, Any] | None = None
    lvef_provenance_restricted_rows: list[dict[str, str]] = []
    if selected_valid:
        selected_reconciliation_rows = selected_stage_reconciliation_rows(
            frames["selected_studies"], frames, input_frames
        )
        selected_containment_rows, selected_containment_restricted_rows = selected_stage_containment(
            frames["selected_studies"], frames
        )
        selected_mapping_rows, selected_mapping_restricted_rows = selected_stage_subject_mapping(
            frames["selected_studies"], frames
        )
        if "structured_measurements" in frames and "lvef_labels" in frames:
            lvef_provenance, lvef_provenance_restricted_rows = reconcile_lvef_label_provenance(
                frames["selected_studies"],
                frames["structured_measurements"],
                frames["lvef_labels"],
            )

    split_rows: list[dict[str, Any]] = []
    for name, frame in frames.items():
        split_rows.extend(split_count_rows(name, frame))

    task_rows = task_denominator_rows(frames["multitask_panel"], args.task_prefix) if "multitask_panel" in frames else []

    inventory_df = pd.DataFrame(inventory_rows)
    safe_inventory_columns = [column for column in inventory_df.columns if column not in {"error_message"}]
    write_aggregate_csv(inventory_df[safe_inventory_columns], args.output_dir / "artifact_inventory.csv")
    write_aggregate_csv(pd.DataFrame(denominator_rows), args.output_dir / "denominator_summary.csv")
    write_aggregate_csv(
        pd.DataFrame([selected_integrity] if selected_integrity is not None else []),
        args.output_dir / "selected_cohort_integrity.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(
            selected_reconciliation_rows,
            columns=[
                "stage",
                "n_stage_studies_all",
                "n_stage_studies_in_selected",
                "n_stage_studies_outside_selected",
                "n_selected_studies_absent",
                "n_selected_studies_all_rows_successful",
                "n_rows_in_selected",
                "n_subjects_in_selected",
            ],
        ),
        args.output_dir / "selected_cohort_stage_reconciliation.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(
            selected_containment_rows,
            columns=[
                "upstream_stage",
                "downstream_stage",
                "expected_relation",
                "comparison_scope",
                "status",
                "n_upstream_studies_compared",
                "n_downstream_studies_compared",
                "n_downstream_not_upstream",
                "n_upstream_not_downstream",
                "downstream_subset_of_upstream",
                "sets_equal",
                "relation_valid",
            ],
        ),
        args.output_dir / "selected_cohort_stage_containment.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(
            selected_mapping_rows,
            columns=[
                "stage",
                "n_stage_rows",
                "n_stage_rows_missing_subject_or_study",
                "n_stage_studies_with_multiple_subjects",
                "n_stage_rows_for_selected_studies",
                "n_selected_studies_compared",
                "n_selected_study_rows_subject_mapping_mismatch",
                "n_selected_studies_subject_mapping_mismatch",
                "stage_identifier_integrity_valid",
                "selected_study_subject_mapping_valid",
                "stage_mapping_audit_valid",
            ],
        ),
        args.output_dir / "selected_cohort_stage_subject_mapping.csv",
    )
    write_aggregate_csv(
        pd.DataFrame([lvef_provenance] if lvef_provenance is not None else []),
        args.output_dir / "lvef_label_provenance.csv",
    )
    write_aggregate_csv(
        pd.DataFrame([clip_component_result]),
        args.output_dir / "clip_embedding_component_union_provenance.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(split_rows, columns=["artifact", "split", "n_rows", "n_studies", "n_subjects"]),
        args.output_dir / "split_counts.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(task_rows, columns=["task", "split", "n_with_target"]),
        args.output_dir / "multitask_task_denominators.csv",
    )

    status_counts = inventory_df["status"].value_counts().to_dict()
    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(
            restricted_warning_rows,
            columns=["artifact", "status", "supplied_path", "error_type", "error_message"],
        ).to_csv(restricted_dir / "artifact_warnings_restricted.csv", index=False)
        pd.DataFrame(
            selected_containment_restricted_rows,
            columns=["transition", "comparison_scope", "status", "study_id"],
        ).to_csv(restricted_dir / "stage_containment_warnings_restricted.csv", index=False)
        pd.DataFrame(
            selected_mapping_restricted_rows,
            columns=["stage", "warning_type", "study_id"],
        ).to_csv(restricted_dir / "stage_subject_mapping_warnings_restricted.csv", index=False)
        pd.DataFrame(
            lvef_provenance_restricted_rows,
            columns=["warning_type", "study_id", "source_row"],
        ).to_csv(restricted_dir / "lvef_label_provenance_warnings_restricted.csv", index=False)
        pd.DataFrame(
            clip_component_restricted_rows,
            columns=["warning_type", "study_id", "clip_key"],
        ).to_csv(
            restricted_dir / "clip_embedding_component_union_warnings_restricted.csv",
            index=False,
        )
        restricted_written = True
    summary = {
        "audit": "lvef_multitask_artifacts",
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "n_split_rows": len(split_rows),
        "n_task_denominator_rows": len(task_rows),
        "n_selected_stage_reconciliation_rows": len(selected_reconciliation_rows),
        "n_selected_stage_containment_failures": sum(
            not row["relation_valid"] for row in selected_containment_rows
        ),
        "n_selected_stage_subject_mapping_failures": sum(
            not row["stage_mapping_audit_valid"] for row in selected_mapping_rows
        ),
        "selected_cohort_integrity_valid": (
            selected_integrity["one_study_per_subject_valid"] if selected_integrity else None
        ),
        "lvef_label_provenance_valid": (
            lvef_provenance["label_provenance_valid"] if lvef_provenance else None
        ),
        "clip_component_union_status": clip_component_result["status"],
        "clip_component_union_provenance_valid": clip_component_result.get(
            "component_union_provenance_valid"
        ),
        "patient_level_output_written": False,
        "restricted_warnings_written": restricted_written,
        "notes": [
            "NOT_SUPPLIED and MISSING artifacts are expected during local development.",
            "Use restricted SCC paths for identifier-level overlap and split warnings.",
            "No model fitting or test-performance computation is performed.",
        ],
    }
    write_json(summary, args.output_dir / "audit_summary.json")
    print(json.dumps(summary, indent=2))
    blocking_statuses = int(
        inventory_df["status"].isin({"MISSING", "ERROR", "REQUIRED_NOT_SUPPLIED"}).sum()
    )
    if blocking_statuses:
        return 2
    if clip_component_input_blocked:
        return 2
    if selected_integrity is not None and not selected_integrity["one_study_per_subject_valid"]:
        return 1
    if any(not row["relation_valid"] for row in selected_containment_rows):
        return 1
    if any(not row["stage_mapping_audit_valid"] for row in selected_mapping_rows):
        return 1
    if lvef_provenance is not None and not lvef_provenance["label_provenance_valid"]:
        return 1
    if (
        clip_component_result["status"] == "EVALUATED"
        and not clip_component_result["component_union_provenance_valid"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
