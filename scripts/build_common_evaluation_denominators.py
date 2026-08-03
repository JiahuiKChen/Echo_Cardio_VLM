#!/usr/bin/env python3
"""Dry-run common denominators across vision, structured, and fusion inputs.

The script computes set identity and intersections only. It never fits a model or
computes test performance. Identifier-level common sets may be written only to an
explicit restricted directory outside the repository.
"""
from __future__ import annotations

import argparse
import json
from functools import reduce
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    normalized_ids,
    parse_named_path,
    require_restricted_path,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


CONTINUOUS_LABEL_RTOL = 1e-6
CONTINUOUS_LABEL_ATOL = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modality", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--target-column", action="append", default=[])
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument(
        "--label-column",
        default=None,
        help="Optional continuous/common label column for equality checking.",
    )
    parser.add_argument(
        "--binary-label-column",
        default=None,
        help="Optional historical binary label column; requires --label-column and --binary-threshold.",
    )
    parser.add_argument(
        "--binary-threshold",
        type=float,
        default=None,
        help="Threshold used to verify binary_label == (label < threshold).",
    )
    parser.add_argument(
        "--label-authority-csv",
        type=Path,
        help="Optional LVEF label authority normalized and compared with every modality.",
    )
    parser.add_argument("--target-definition-csv", type=Path)
    parser.add_argument("--target-panel-wide-csv", type=Path)
    parser.add_argument("--target-panel-long-csv", type=Path)
    parser.add_argument("--expected-target-count", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    return parser.parse_args()


def target_names(frames: dict[str, pd.DataFrame], requested: list[str], prefix: str) -> list[str]:
    if requested:
        return list(dict.fromkeys(requested))
    long_task_sets: list[set[str]] = []
    for frame in frames.values():
        task_col = resolve_column(frame, ("task_col", "task", "target_name"))
        if task_col is None:
            long_task_sets = []
            break
        long_task_sets.append(set(frame[task_col].dropna().astype(str).tolist()))
    if long_task_sets:
        return sorted(set.union(*long_task_sets))
    all_columns = reduce(set.union, (set(frame.columns) for frame in frames.values()))
    tasks = sorted(column for column in all_columns if str(column).startswith(prefix))
    return tasks or ["__all__"]


def long_or_wide_task_set(frame: pd.DataFrame, prefix: str) -> set[str]:
    task_col = resolve_column(frame, ("task_col", "task", "target_name"))
    if task_col is not None:
        return set(frame[task_col].dropna().astype(str))
    return {str(column) for column in frame.columns if str(column).startswith(prefix)}


def target_set_reconciliation(
    expected: set[str], source_sets: dict[str, set[str]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, observed in source_sets.items():
        rows.append(
            {
                "source": source,
                "n_expected_targets": len(expected),
                "n_observed_targets": len(observed),
                "n_expected_missing": len(expected - observed),
                "n_unexpected_targets": len(observed - expected),
                "target_sets_identical": observed == expected,
            }
        )
    return rows


def rows_for_target_split(
    frame: pd.DataFrame, target: str, split: str | None = None
) -> pd.DataFrame:
    """Return only rows eligible for one target/split denominator."""
    scoped = frame
    if split is not None:
        split_col = resolve_column(scoped, SPLIT_COLUMNS, required=True, label="split")
        assert split_col is not None
        scoped = scoped[
            scoped[split_col].astype(str).str.strip().str.lower().eq(split)
        ]
    if target == "__all__":
        return scoped
    if target in scoped.columns:
        return scoped[pd.to_numeric(scoped[target], errors="coerce").notna()]
    task_col = resolve_column(scoped, ("task_col", "task", "target_name"))
    if task_col is None:
        return scoped.iloc[0:0]
    target_mask = scoped[task_col].astype(str).eq(target)
    value_col = resolve_column(scoped, ("y_true", "target_value", "label_value", "result"))
    if value_col:
        target_mask &= pd.to_numeric(scoped[value_col], errors="coerce").notna()
    return scoped[target_mask]


def normalized_id_series(series: pd.Series) -> pd.Series:
    """Normalize identifiers without converting missing values into string tokens."""
    values = series.astype("string").str.strip()
    return values.mask(values.eq(""))


def normalized_split_series(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip().str.lower()
    return values.mask(values.eq(""))


def finite_numeric_mask(series: pd.Series) -> pd.Series:
    """Return a same-index mask that is true only for finite numeric values."""
    values = series.to_numpy(dtype=float, na_value=np.nan)
    return pd.Series(np.isfinite(values), index=series.index)


def numeric_projection(series: pd.Series, *, label: str) -> pd.Series:
    """Coerce numeric labels while rejecting nonblank invalid source values."""
    numeric = pd.to_numeric(series, errors="coerce")
    text = series.astype("string").str.strip()
    supplied = series.notna() & text.ne("")
    if bool((supplied & numeric.isna()).any()):
        raise ValueError(f"Nonnumeric values in required {label}")
    if bool((supplied & ~finite_numeric_mask(numeric)).any()):
        raise ValueError(f"Non-finite values in required {label}")
    return numeric


def normalize_lvef_label_authority(
    frame: pd.DataFrame,
    *,
    continuous_output_column: str,
    binary_output_column: str,
) -> pd.DataFrame:
    """Project an LVEF authority to the prediction-comparison schema."""
    subject_col = resolve_column(frame, SUBJECT_COLUMNS, required=True, label="authority subject")
    study_col = resolve_column(frame, STUDY_COLUMNS, required=True, label="authority study")
    split_col = resolve_column(frame, SPLIT_COLUMNS, required=True, label="authority split")
    continuous_col = resolve_column(
        frame,
        (continuous_output_column, "lvef", "y_true", "target_value", "label_value"),
        required=True,
        label="authority continuous label",
    )
    binary_col = resolve_column(
        frame,
        (binary_output_column, "lvef_binary_reduced", "binary_label", "y_binary"),
        required=True,
        label="authority binary label",
    )
    assert all(
        column is not None
        for column in (subject_col, study_col, split_col, continuous_col, binary_col)
    )
    projected = pd.DataFrame(
        {
            "subject_id": normalized_id_series(frame[str(subject_col)]),
            "study_id": normalized_id_series(frame[str(study_col)]),
            "split": normalized_split_series(frame[str(split_col)]),
            continuous_output_column: numeric_projection(
                frame[str(continuous_col)], label="continuous authority label"
            ),
            binary_output_column: numeric_projection(
                frame[str(binary_col)], label="binary authority label"
            ),
        }
    )
    # Keyframe/row-level authorities may repeat the same study label. Exact
    # projected duplicates are benign; conflicting duplicates remain blocking.
    return projected.drop_duplicates().reset_index(drop=True)


def normalize_multitask_panel_authorities(
    wide: pd.DataFrame,
    long: pd.DataFrame,
    *,
    label_output_column: str,
    task_prefix: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize wide and long target panels into comparable long authorities."""
    wide_subject = resolve_column(wide, SUBJECT_COLUMNS, required=True, label="wide panel subject")
    wide_study = resolve_column(wide, STUDY_COLUMNS, required=True, label="wide panel study")
    wide_split = resolve_column(wide, SPLIT_COLUMNS, required=True, label="wide panel split")
    assert wide_subject is not None and wide_study is not None and wide_split is not None
    task_columns = sorted(str(column) for column in wide.columns if str(column).startswith(task_prefix))
    if not task_columns:
        raise ValueError("Wide target panel has no task columns")

    wide_projected = pd.DataFrame(
        {
            "subject_id": normalized_id_series(wide[wide_subject]),
            "study_id": normalized_id_series(wide[wide_study]),
            "split": normalized_split_series(wide[wide_split]),
        }
    )
    for task in task_columns:
        wide_projected[task] = numeric_projection(wide[task], label=f"wide target {task}")
    if bool(wide_projected[["subject_id", "study_id", "split"]].isna().any(axis=None)):
        raise ValueError("Wide target panel contains missing subject, study, or split")
    wide_authority = wide_projected.melt(
        id_vars=["subject_id", "study_id", "split"],
        value_vars=task_columns,
        var_name="task_col",
        value_name=label_output_column,
    )
    wide_authority = wide_authority[wide_authority[label_output_column].notna()]
    wide_authority = wide_authority.reset_index(drop=True)

    long_subject = resolve_column(long, SUBJECT_COLUMNS, required=True, label="long panel subject")
    long_study = resolve_column(long, STUDY_COLUMNS, required=True, label="long panel study")
    long_task = resolve_column(
        long, ("task_col", "task", "target_name"), required=True, label="long panel task"
    )
    long_value = resolve_column(
        long,
        (label_output_column, "task_value", "target_value", "label_value", "result"),
        required=True,
        label="long panel target value",
    )
    assert all(column is not None for column in (long_subject, long_study, long_task, long_value))
    long_projected = pd.DataFrame(
        {
            "_source_row": range(len(long)),
            "subject_id": normalized_id_series(long[str(long_subject)]),
            "study_id": normalized_id_series(long[str(long_study)]),
            "task_col": long[str(long_task)].astype("string").str.strip().mask(lambda x: x.eq("")),
            label_output_column: numeric_projection(
                long[str(long_value)], label="long panel target value"
            ),
        }
    )
    if bool(long_projected["task_col"].isna().any()):
        raise ValueError("Long target panel contains blank task names")
    if bool(
        long_projected[
            ["subject_id", "study_id", "task_col", label_output_column]
        ].isna().any(axis=None)
    ):
        raise ValueError(
            "Long target panel contains missing subject, study, task, or target value"
        )
    existing_long_split = resolve_column(long, SPLIT_COLUMNS)
    if existing_long_split is not None:
        long_projected["_source_split"] = normalized_split_series(long[str(existing_long_split)])

    # The wide panel is the split authority. Merge on both subject and study so
    # a subject-study conflict remains unmatched and therefore fails closed.
    wide_keys = wide_projected[["subject_id", "study_id", "split"]].drop_duplicates()
    long_authority = long_projected.merge(
        wide_keys,
        on=["subject_id", "study_id"],
        how="left",
    )
    if existing_long_split is not None:
        # A supplied long split may corroborate but never override the wide authority.
        supplied = long_authority["_source_split"].notna()
        if bool((supplied & long_authority["_source_split"].ne(long_authority["split"])).any()):
            raise ValueError("Long and wide target panel split assignments conflict")
    long_authority = long_authority.drop(columns=["_source_row", "_source_split"], errors="ignore")
    long_authority = long_authority.reset_index(drop=True)
    return wide_authority, long_authority


def ids_for_target(
    frame: pd.DataFrame, id_col: str, target: str, split: str | None = None
) -> set[str]:
    scoped = rows_for_target_split(frame, target, split)
    return normalized_ids(scoped[id_col])


def restricted_discrepancy(
    *,
    target: str,
    split: str,
    identifier_level: str,
    modality: str,
    status: str,
    subject_id: str = "",
    study_id: str = "",
    identifier: str = "",
    source_row: str = "",
    observed_split: str = "",
    observed_continuous: str = "",
    observed_binary: str = "",
    expected_binary: str = "",
) -> dict[str, str]:
    return {
        "target": target,
        "split": split,
        "identifier_level": identifier_level,
        "modality": modality,
        "identifier": identifier,
        "subject_id": subject_id,
        "study_id": study_id,
        "source_row": source_row,
        "observed_split": observed_split,
        "observed_continuous": observed_continuous,
        "observed_binary": observed_binary,
        "expected_binary": expected_binary,
        "status": status,
    }


def subject_study_pair_reconciliation(
    frames: dict[str, pd.DataFrame],
    *,
    target: str = "__all__",
    split: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    """Compare pair keys and study ownership without exposing IDs in aggregates."""
    split_label = "all" if split is None else split
    pair_sets: dict[str, set[tuple[str, str]]] = {}
    study_subject_maps: dict[str, dict[str, set[str]]] = {}
    details_by_modality: dict[str, dict[str, int]] = {}
    restricted_rows: list[dict[str, str]] = []

    for name, frame in frames.items():
        subject_col = resolve_column(
            frame, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {name}"
        )
        study_col = resolve_column(
            frame, STUDY_COLUMNS, required=True, label=f"study identifier for {name}"
        )
        split_col = resolve_column(frame, SPLIT_COLUMNS, required=True, label=f"split for {name}")
        assert subject_col is not None and study_col is not None and split_col is not None
        scoped = rows_for_target_split(frame, target, split)
        task_col = resolve_column(scoped, ("task_col", "task", "target_name"))
        if task_col is not None:
            task_values = scoped[task_col].astype("string").str.strip().mask(lambda x: x.eq(""))
        else:
            task_values = pd.Series(target, index=scoped.index, dtype="string")
        projected = pd.DataFrame(
            {
                "_subject": normalized_id_series(scoped[subject_col]),
                "_study": normalized_id_series(scoped[study_col]),
                "_task": task_values,
                "_split": scoped[split_col]
                .astype("string")
                .str.strip()
                .str.lower()
                .mask(lambda x: x.eq("")),
            },
            index=scoped.index,
        )
        missing_pair = projected[["_subject", "_study"]].isna().any(axis=1)
        missing_key = projected[["_study", "_task", "_split"]].isna().any(axis=1)
        valid_pairs = projected.loc[~missing_pair, ["_subject", "_study"]]
        pairs = set(valid_pairs.itertuples(index=False, name=None))
        pair_sets[name] = pairs

        valid_keys = projected.loc[~missing_key, ["_study", "_task", "_split"]]
        duplicate_key_mask = valid_keys.duplicated(["_study", "_task", "_split"], keep=False)
        n_duplicate_keys = int(
            valid_keys.loc[duplicate_key_mask]
            .drop_duplicates(["_study", "_task", "_split"])
            .shape[0]
        )
        n_duplicate_key_rows = int(duplicate_key_mask.sum())

        mapping: dict[str, set[str]] = {}
        for study_id, group in valid_pairs.groupby("_study"):
            mapping[str(study_id)] = set(group["_subject"].astype(str))
        study_subject_maps[name] = mapping
        conflicted_studies = {study_id for study_id, subjects in mapping.items() if len(subjects) > 1}
        details_by_modality[name] = {
            "n_missing_pair": int(missing_pair.sum()),
            "n_missing_key": int(missing_key.sum()),
            "n_duplicate_keys": n_duplicate_keys,
            "n_duplicate_key_rows": n_duplicate_key_rows,
            "n_conflicted_studies": len(conflicted_studies),
        }

        for row_index, values in projected.loc[missing_pair | missing_key].iterrows():
            status = (
                "MISSING_SUBJECT_OR_STUDY_IDENTIFIER"
                if bool(missing_pair.loc[row_index])
                else "MISSING_PREDICTION_KEY_COMPONENT"
            )
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="subject_study_pair",
                    modality=name,
                    status=status,
                    subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                    study_id="" if pd.isna(values["_study"]) else str(values["_study"]),
                    source_row=str(row_index),
                    observed_split="" if pd.isna(values["_split"]) else str(values["_split"]),
                )
            )
        for row_index in valid_keys.index[duplicate_key_mask]:
            values = projected.loc[row_index]
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="prediction_key",
                    modality=name,
                    status="DUPLICATE_PREDICTION_KEY",
                    subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                    study_id=str(values["_study"]),
                    source_row=str(row_index),
                    observed_split=str(values["_split"]),
                )
            )
        for study_id in sorted(conflicted_studies):
            for subject_id in sorted(mapping[study_id]):
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split=split_label,
                        identifier_level="subject_study_pair",
                        modality=name,
                        status="STUDY_MAPPED_TO_MULTIPLE_SUBJECTS",
                        subject_id=subject_id,
                        study_id=study_id,
                        observed_split=split_label,
                    )
                )

    common_pairs = set.intersection(*pair_sets.values()) if pair_sets else set()
    first_pairs = next(iter(pair_sets.values())) if pair_sets else set()
    pairs_identical = bool(pair_sets) and all(values == first_pairs for values in pair_sets.values())
    common_studies = (
        set.intersection(*(set(mapping) for mapping in study_subject_maps.values()))
        if study_subject_maps
        else set()
    )
    mismatched_studies = {
        study_id
        for study_id in common_studies
        if len(
            {
                tuple(sorted(mapping[study_id]))
                for mapping in study_subject_maps.values()
            }
        )
        > 1
    }
    internal_valid = all(
        details["n_missing_pair"] == 0
        and details["n_missing_key"] == 0
        and details["n_duplicate_keys"] == 0
        and details["n_conflicted_studies"] == 0
        for details in details_by_modality.values()
    )
    failed = not pairs_identical or not common_pairs or not internal_valid or bool(mismatched_studies)
    row: dict[str, Any] = {
        "target": target,
        "split": split_label,
        "identifier_level": "subject_study_pair",
        "sets_identical": pairs_identical,
        "common_empty": len(common_pairs) == 0,
        "n_common": len(common_pairs),
        "subject_study_mapping_valid": internal_valid,
        "subject_assignment_identical_on_common_studies": not mismatched_studies,
        "n_common_studies_with_subject_assignment_mismatch": len(mismatched_studies),
    }
    for name, pairs in sorted(pair_sets.items()):
        details = details_by_modality[name]
        row[f"n_{name}"] = len(pairs)
        row[f"n_{name}_outside_common"] = len(pairs - common_pairs)
        row[f"n_{name}_rows_missing_pair_ids"] = details["n_missing_pair"]
        row[f"n_{name}_rows_missing_prediction_key"] = details["n_missing_key"]
        row[f"n_{name}_duplicate_prediction_keys"] = details["n_duplicate_keys"]
        row[f"n_{name}_duplicate_prediction_key_rows"] = details["n_duplicate_key_rows"]
        row[f"n_{name}_studies_with_multiple_subjects"] = details["n_conflicted_studies"]
        for subject_id, study_id in sorted(pairs - common_pairs):
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="subject_study_pair",
                    modality=name,
                    status="SUBJECT_STUDY_PAIR_OUTSIDE_COMMON_INTERSECTION",
                    subject_id=subject_id,
                    study_id=study_id,
                    observed_split=split_label,
                )
            )
        for study_id in sorted(mismatched_studies):
            for subject_id in sorted(study_subject_maps[name][study_id]):
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split=split_label,
                        identifier_level="subject_study_pair",
                        modality=name,
                        status="SUBJECT_ASSIGNMENT_MISMATCH_ON_COMMON_STUDY",
                        subject_id=subject_id,
                        study_id=study_id,
                        observed_split=split_label,
                    )
                )
    return row, restricted_rows, failed


def subject_split_assignment_reconciliation(
    frames: dict[str, pd.DataFrame], *, target: str = "__all__"
) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    """Require one consistent split assignment per subject and modality."""
    assignment_sets: dict[str, set[tuple[str, str]]] = {}
    subject_split_maps: dict[str, dict[str, set[str]]] = {}
    details_by_modality: dict[str, dict[str, int]] = {}
    restricted_rows: list[dict[str, str]] = []
    for name, frame in frames.items():
        subject_col = resolve_column(
            frame, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {name}"
        )
        split_col = resolve_column(frame, SPLIT_COLUMNS, required=True, label=f"split for {name}")
        assert subject_col is not None and split_col is not None
        scoped = rows_for_target_split(frame, target)
        projected = pd.DataFrame(
            {
                "_subject": normalized_id_series(scoped[subject_col]),
                "_split": scoped[split_col]
                .astype("string")
                .str.strip()
                .str.lower()
                .mask(lambda x: x.eq("")),
            },
            index=scoped.index,
        )
        missing_mask = projected[["_subject", "_split"]].isna().any(axis=1)
        valid = projected.loc[~missing_mask]
        assignments = set(valid.itertuples(index=False, name=None))
        assignment_sets[name] = assignments
        mapping: dict[str, set[str]] = {}
        for subject_id, group in valid.groupby("_subject"):
            mapping[str(subject_id)] = set(group["_split"].astype(str))
        subject_split_maps[name] = mapping
        conflicted_subjects = {subject_id for subject_id, splits in mapping.items() if len(splits) > 1}
        details_by_modality[name] = {
            "n_missing": int(missing_mask.sum()),
            "n_conflicted_subjects": len(conflicted_subjects),
        }
        for row_index, values in projected.loc[missing_mask].iterrows():
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split="all",
                    identifier_level="subject_split_assignment",
                    modality=name,
                    status="MISSING_SUBJECT_OR_SPLIT_ASSIGNMENT",
                    subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                    source_row=str(row_index),
                    observed_split="" if pd.isna(values["_split"]) else str(values["_split"]),
                )
            )
        for subject_id in sorted(conflicted_subjects):
            for observed_split in sorted(mapping[subject_id]):
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split="all",
                        identifier_level="subject_split_assignment",
                        modality=name,
                        status="SUBJECT_ASSIGNED_TO_MULTIPLE_SPLITS",
                        subject_id=subject_id,
                        observed_split=observed_split,
                    )
                )

    common_assignments = set.intersection(*assignment_sets.values()) if assignment_sets else set()
    first_assignments = next(iter(assignment_sets.values())) if assignment_sets else set()
    assignments_identical = bool(assignment_sets) and all(
        values == first_assignments for values in assignment_sets.values()
    )
    common_subjects = (
        set.intersection(*(set(mapping) for mapping in subject_split_maps.values()))
        if subject_split_maps
        else set()
    )
    mismatched_subjects = {
        subject_id
        for subject_id in common_subjects
        if len(
            {
                tuple(sorted(mapping[subject_id]))
                for mapping in subject_split_maps.values()
            }
        )
        > 1
    }
    internal_valid = all(
        details["n_missing"] == 0 and details["n_conflicted_subjects"] == 0
        for details in details_by_modality.values()
    )
    failed = (
        not assignments_identical
        or not common_assignments
        or not internal_valid
        or bool(mismatched_subjects)
    )
    row: dict[str, Any] = {
        "target": target,
        "split": "all",
        "identifier_level": "subject_split_assignment",
        "sets_identical": assignments_identical,
        "common_empty": len(common_assignments) == 0,
        "n_common": len(common_assignments),
        "subject_split_mapping_valid": internal_valid,
        "split_assignment_identical_on_common_subjects": not mismatched_subjects,
        "n_common_subjects_with_split_assignment_mismatch": len(mismatched_subjects),
    }
    for name, assignments in sorted(assignment_sets.items()):
        details = details_by_modality[name]
        row[f"n_{name}"] = len(assignments)
        row[f"n_{name}_outside_common"] = len(assignments - common_assignments)
        row[f"n_{name}_rows_missing_subject_or_split"] = details["n_missing"]
        row[f"n_{name}_subjects_with_multiple_splits"] = details["n_conflicted_subjects"]
        for subject_id, observed_split in sorted(assignments - common_assignments):
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split="all",
                    identifier_level="subject_split_assignment",
                    modality=name,
                    status="SUBJECT_SPLIT_ASSIGNMENT_OUTSIDE_COMMON_INTERSECTION",
                    subject_id=subject_id,
                    observed_split=observed_split,
                )
            )
        for subject_id in sorted(mismatched_subjects):
            for observed_split in sorted(subject_split_maps[name][subject_id]):
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split="all",
                        identifier_level="subject_split_assignment",
                        modality=name,
                        status="SPLIT_ASSIGNMENT_MISMATCH_ON_COMMON_SUBJECT",
                        subject_id=subject_id,
                        observed_split=observed_split,
                    )
                )
    return row, restricted_rows, failed


def compute_common_ids(
    modality_frames: dict[str, pd.DataFrame],
    *,
    id_candidates: tuple[str, ...] = STUDY_COLUMNS,
    target: str = "__all__",
    split: str | None = None,
) -> tuple[dict[str, set[str]], set[str]]:
    sets: dict[str, set[str]] = {}
    for name, frame in modality_frames.items():
        id_col = resolve_column(frame, id_candidates, required=True, label=f"identifier for {name}")
        assert id_col is not None
        sets[name] = ids_for_target(frame, id_col, target, split)
    intersection = set.intersection(*sets.values()) if sets else set()
    return sets, intersection


def continuous_labels_equal_on_common(
    frames: dict[str, pd.DataFrame],
    common_ids: set[str],
    label_column: str,
    id_candidates: tuple[str, ...],
    target: str = "__all__",
    split: str | None = None,
    rtol: float = CONTINUOUS_LABEL_RTOL,
    atol: float = CONTINUOUS_LABEL_ATOL,
) -> tuple[bool, int, list[dict[str, str]]]:
    merged: pd.DataFrame | None = None
    within_modality_mismatch_studies = 0
    missing_label_studies = 0
    restricted_rows: list[dict[str, str]] = []
    split_label = "all" if split is None else split
    for name, frame in frames.items():
        id_col = resolve_column(frame, id_candidates, required=True)
        if label_column not in frame.columns:
            raise ValueError(f"{name} missing continuous label audit column")
        assert id_col is not None
        subject_col = resolve_column(frame, SUBJECT_COLUMNS)
        scoped = rows_for_target_split(frame, target, split)
        projected = pd.DataFrame(
            {
                "_id": normalized_id_series(scoped[id_col]),
                "_subject": (
                    normalized_id_series(scoped[subject_col])
                    if subject_col is not None
                    else pd.Series("", index=scoped.index, dtype="string")
                ),
                "_value": pd.to_numeric(scoped[label_column], errors="coerce"),
            },
            index=scoped.index,
        )
        projected = projected[projected["_id"].isin(common_ids)].copy()
        non_finite = projected["_value"].notna() & ~finite_numeric_mask(projected["_value"])
        invalid = projected["_value"].isna() | non_finite
        missing_ids = set(projected.loc[invalid, "_id"].dropna().astype(str))
        missing_label_studies += len(missing_ids)
        for row_index, values in projected.loc[invalid].iterrows():
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="continuous_label",
                    modality=name,
                    status=(
                        "NONFINITE_CONTINUOUS_LABEL"
                        if bool(non_finite.loc[row_index])
                        else "MISSING_OR_NONNUMERIC_CONTINUOUS_LABEL"
                    ),
                    subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                    study_id="" if pd.isna(values["_id"]) else str(values["_id"]),
                    source_row=str(row_index),
                )
            )
        # Downstream duplicate and cross-source comparisons must never treat
        # matching infinities as valid labels.
        projected.loc[non_finite, "_value"] = np.nan
        conflict_flags = projected.groupby("_id")["_value"].apply(
            lambda values: (
                len(values.dropna()) > 0
                and not bool(
                    np.isclose(
                        values.dropna().to_numpy(dtype=float),
                        float(values.dropna().iloc[0]),
                        rtol=rtol,
                        atol=atol,
                        equal_nan=False,
                    ).all()
                )
            )
        )
        conflict_ids = set(conflict_flags[conflict_flags].index.astype(str))
        within_modality_mismatch_studies += len(conflict_ids)
        for study_id in sorted(conflict_ids):
            for row_index, values in projected[projected["_id"].astype(str).eq(study_id)].iterrows():
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split=split_label,
                        identifier_level="continuous_label",
                        modality=name,
                        status="CONTINUOUS_LABEL_CONFLICT_WITHIN_MODALITY",
                        subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                        study_id=study_id,
                        source_row=str(row_index),
                        observed_continuous=(
                            "" if pd.isna(values["_value"]) else str(values["_value"])
                        ),
                    )
                )
        part = projected.drop_duplicates("_id").rename(
            columns={"_value": f"label__{name}", "_subject": f"subject__{name}"}
        )
        merged = part if merged is None else merged.merge(part, on="_id", how="outer")
    if merged is None or merged.empty:
        mismatch_count = within_modality_mismatch_studies + missing_label_studies
        return mismatch_count == 0, mismatch_count, restricted_rows
    label_cols = [column for column in merged.columns if column.startswith("label__")]
    def labels_mismatch(values: pd.Series) -> bool:
        numeric = pd.to_numeric(values, errors="coerce")
        if bool(numeric.isna().any()) or numeric.empty:
            return True
        if not bool(finite_numeric_mask(numeric).all()):
            return True
        return not bool(
            np.isclose(
                numeric.to_numpy(dtype=float),
                float(numeric.iloc[0]),
                rtol=rtol,
                atol=atol,
                equal_nan=False,
            ).all()
        )

    mismatch = merged[label_cols].apply(labels_mismatch, axis=1)
    cross_mismatch_ids = set(merged.loc[mismatch, "_id"].dropna().astype(str))
    for study_id in sorted(cross_mismatch_ids):
        row = merged[merged["_id"].astype(str).eq(study_id)].iloc[0]
        for name in frames:
            value_col = f"label__{name}"
            subject_name = f"subject__{name}"
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="continuous_label",
                    modality=name,
                    status="CONTINUOUS_LABEL_MISMATCH_ACROSS_MODALITIES",
                    subject_id=(
                        "" if subject_name not in row or pd.isna(row[subject_name]) else str(row[subject_name])
                    ),
                    study_id=study_id,
                    observed_continuous=(
                        "" if value_col not in row or pd.isna(row[value_col]) else str(row[value_col])
                    ),
                )
            )
    mismatch_count = (
        len(cross_mismatch_ids)
        + within_modality_mismatch_studies
        + missing_label_studies
    )
    return mismatch_count == 0, mismatch_count, restricted_rows


def labels_equal_on_common(
    frames: dict[str, pd.DataFrame],
    common_ids: set[str],
    label_column: str,
    id_candidates: tuple[str, ...],
    target: str = "__all__",
    split: str | None = None,
    rtol: float = CONTINUOUS_LABEL_RTOL,
    atol: float = CONTINUOUS_LABEL_ATOL,
) -> tuple[bool, int]:
    equal, mismatch_count, _ = continuous_labels_equal_on_common(
        frames,
        common_ids,
        label_column,
        id_candidates,
        target=target,
        split=split,
        rtol=rtol,
        atol=atol,
    )
    return equal, mismatch_count


def binary_endpoint_reconciliation(
    frames: dict[str, pd.DataFrame],
    common_ids: set[str],
    *,
    continuous_label_column: str,
    binary_label_column: str,
    threshold: float,
    target: str = "__all__",
    split: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    """Audit binary-label equality and continuous-threshold consistency only."""
    if not np.isfinite(threshold):
        raise ValueError("Binary endpoint threshold must be finite")
    split_label = "all" if split is None else split
    modality_parts: dict[str, pd.DataFrame] = {}
    details_by_modality: dict[str, dict[str, int]] = {}
    restricted_rows: list[dict[str, str]] = []

    for name, frame in frames.items():
        study_col = resolve_column(
            frame, STUDY_COLUMNS, required=True, label=f"study identifier for {name}"
        )
        subject_col = resolve_column(
            frame, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {name}"
        )
        assert study_col is not None and subject_col is not None
        missing_columns = [
            column
            for column in (continuous_label_column, binary_label_column)
            if column not in frame.columns
        ]
        if missing_columns:
            raise ValueError(f"{name} missing binary endpoint audit columns")
        scoped = rows_for_target_split(frame, target, split)
        projected = pd.DataFrame(
            {
                "_subject": normalized_id_series(scoped[subject_col]),
                "_study": normalized_id_series(scoped[study_col]),
                "_continuous": pd.to_numeric(scoped[continuous_label_column], errors="coerce"),
                "_binary": pd.to_numeric(scoped[binary_label_column], errors="coerce"),
            },
            index=scoped.index,
        )
        projected = projected[projected["_study"].isin(common_ids)].copy()
        missing_value = projected[["_subject", "_study", "_continuous", "_binary"]].isna().any(axis=1)
        non_finite_continuous = (
            projected["_continuous"].notna()
            & ~finite_numeric_mask(projected["_continuous"])
        )
        invalid_binary = projected["_binary"].notna() & (
            ~finite_numeric_mask(projected["_binary"])
            | ~projected["_binary"].isin([0.0, 1.0])
        )
        invalid_value = missing_value | non_finite_continuous | invalid_binary
        comparable = ~invalid_value
        expected_binary = (projected["_continuous"] < threshold).astype("Int64")
        threshold_mismatch = comparable & projected["_binary"].ne(expected_binary.astype(float))

        valid = projected.loc[~projected["_study"].isna()].copy()
        continuous_conflicts = (
            valid.groupby("_study")["_continuous"].nunique(dropna=False) > 1
        )
        binary_conflicts = valid.groupby("_study")["_binary"].nunique(dropna=False) > 1
        conflict_studies = set(continuous_conflicts[continuous_conflicts].index.astype(str)) | set(
            binary_conflicts[binary_conflicts].index.astype(str)
        )
        observed_studies = set(valid["_study"].dropna().astype(str))
        missing_common_studies = common_ids - observed_studies

        part = (
            valid.sort_index()
            .drop_duplicates("_study", keep="first")
            [["_study", "_subject", "_continuous", "_binary"]]
            .rename(
                columns={
                    "_subject": f"subject__{name}",
                    "_continuous": f"continuous__{name}",
                    "_binary": f"binary__{name}",
                }
            )
        )
        modality_parts[name] = part
        details_by_modality[name] = {
            "n_missing_or_invalid": int(invalid_value.sum()),
            "n_threshold_mismatches": int(threshold_mismatch.sum()),
            "n_within_modality_conflicts": len(conflict_studies),
            "n_missing_common_studies": len(missing_common_studies),
        }

        for row_index in projected.index[invalid_value]:
            values = projected.loc[row_index]
            if bool(non_finite_continuous.loc[row_index]):
                status = "NONFINITE_CONTINUOUS_LABEL"
            elif bool(invalid_binary.loc[row_index]):
                status = "INVALID_BINARY_LABEL"
            else:
                status = "MISSING_BINARY_ENDPOINT_VALUE"
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="binary_endpoint",
                    modality=name,
                    status=status,
                    subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                    study_id="" if pd.isna(values["_study"]) else str(values["_study"]),
                    source_row=str(row_index),
                    observed_continuous="" if pd.isna(values["_continuous"]) else str(values["_continuous"]),
                    observed_binary="" if pd.isna(values["_binary"]) else str(values["_binary"]),
                )
            )
        for row_index in projected.index[threshold_mismatch]:
            values = projected.loc[row_index]
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="binary_endpoint",
                    modality=name,
                    status="BINARY_LABEL_THRESHOLD_INCONSISTENT",
                    subject_id=str(values["_subject"]),
                    study_id=str(values["_study"]),
                    source_row=str(row_index),
                    observed_continuous=str(values["_continuous"]),
                    observed_binary=str(values["_binary"]),
                    expected_binary=str(int(expected_binary.loc[row_index])),
                )
            )
        for study_id in sorted(conflict_studies):
            conflict_rows = projected[projected["_study"].astype(str).eq(study_id)]
            for row_index, values in conflict_rows.iterrows():
                restricted_rows.append(
                    restricted_discrepancy(
                        target=target,
                        split=split_label,
                        identifier_level="binary_endpoint",
                        modality=name,
                        status="WITHIN_MODALITY_BINARY_ENDPOINT_CONFLICT",
                        subject_id="" if pd.isna(values["_subject"]) else str(values["_subject"]),
                        study_id=study_id,
                        source_row=str(row_index),
                        observed_continuous="" if pd.isna(values["_continuous"]) else str(values["_continuous"]),
                        observed_binary="" if pd.isna(values["_binary"]) else str(values["_binary"]),
                    )
                )
        for study_id in sorted(missing_common_studies):
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="binary_endpoint",
                    modality=name,
                    status="COMMON_STUDY_MISSING_BINARY_ENDPOINT_ROW",
                    study_id=study_id,
                )
            )

    merged: pd.DataFrame | None = None
    for part in modality_parts.values():
        merged = part if merged is None else merged.merge(part, on="_study", how="outer")
    if merged is None:
        merged = pd.DataFrame(columns=["_study"])
    binary_cols = [column for column in merged if column.startswith("binary__")]
    cross_mismatch_mask = (
        merged[binary_cols].nunique(axis=1, dropna=False) > 1
        if binary_cols
        else pd.Series(False, index=merged.index)
    )
    mismatch_studies = set(merged.loc[cross_mismatch_mask, "_study"].dropna().astype(str))
    for name, part in modality_parts.items():
        binary_col = f"binary__{name}"
        subject_col = f"subject__{name}"
        for study_id in sorted(mismatch_studies):
            match = part[part["_study"].astype(str).eq(study_id)]
            if match.empty:
                continue
            values = match.iloc[0]
            restricted_rows.append(
                restricted_discrepancy(
                    target=target,
                    split=split_label,
                    identifier_level="binary_endpoint",
                    modality=name,
                    status="BINARY_LABEL_MISMATCH_ACROSS_MODALITIES",
                    subject_id="" if pd.isna(values[subject_col]) else str(values[subject_col]),
                    study_id=study_id,
                    observed_binary="" if pd.isna(values[binary_col]) else str(values[binary_col]),
                )
            )

    n_invalid = sum(details["n_missing_or_invalid"] for details in details_by_modality.values())
    n_threshold = sum(details["n_threshold_mismatches"] for details in details_by_modality.values())
    n_within = sum(details["n_within_modality_conflicts"] for details in details_by_modality.values())
    n_missing_common = sum(details["n_missing_common_studies"] for details in details_by_modality.values())
    labels_identical = not mismatch_studies and n_invalid == 0 and n_within == 0 and n_missing_common == 0
    threshold_consistent = n_threshold == 0 and n_invalid == 0 and n_within == 0
    failed = not common_ids or not labels_identical or not threshold_consistent
    row: dict[str, Any] = {
        "target": target,
        "split": split_label,
        "identifier_level": "binary_endpoint",
        "sets_identical": labels_identical,
        "common_empty": len(common_ids) == 0,
        "n_common": len(common_ids),
        "binary_labels_identical_on_common": labels_identical,
        "binary_labels_consistent_with_continuous_threshold": threshold_consistent,
        "n_cross_modality_binary_label_mismatches": len(mismatch_studies),
        "n_binary_threshold_inconsistencies": n_threshold,
        "n_binary_invalid_or_missing_rows": n_invalid,
        "n_within_modality_binary_endpoint_conflicts": n_within,
        "n_common_binary_endpoint_rows_missing": n_missing_common,
        "binary_threshold": threshold,
    }
    for name, details in sorted(details_by_modality.items()):
        row[f"n_{name}_binary_invalid_or_missing_rows"] = details["n_missing_or_invalid"]
        row[f"n_{name}_binary_threshold_inconsistencies"] = details["n_threshold_mismatches"]
        row[f"n_{name}_within_binary_endpoint_conflicts"] = details["n_within_modality_conflicts"]
        row[f"n_{name}_common_binary_endpoint_rows_missing"] = details["n_missing_common_studies"]
    return row, restricted_rows, failed


def main() -> int:
    args = parse_args()
    binary_audit_requested = args.binary_label_column is not None or args.binary_threshold is not None
    panel_audit_requested = (
        args.target_panel_wide_csv is not None or args.target_panel_long_csv is not None
    )
    if (args.target_panel_wide_csv is None) != (args.target_panel_long_csv is None):
        print(
            json.dumps(
                {
                    "status": "BLOCKED_INCOMPLETE_MULTITASK_AUTHORITY_ARGUMENTS",
                    "requires": ["target-panel-wide-csv", "target-panel-long-csv"],
                },
                indent=2,
            )
        )
        return 2
    if args.label_authority_csv is not None and panel_audit_requested:
        print(json.dumps({"status": "BLOCKED_MIXED_AUTHORITY_MODES"}, indent=2))
        return 2
    if panel_audit_requested and args.label_column is None:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_MULTITASK_LABEL_COLUMN_REQUIRED",
                    "requires": ["label-column"],
                },
                indent=2,
            )
        )
        return 2
    if binary_audit_requested and (
        args.label_column is None
        or args.binary_label_column is None
        or args.binary_threshold is None
    ):
        print(
            json.dumps(
                {
                    "status": "BLOCKED_INCOMPLETE_BINARY_ENDPOINT_AUDIT_ARGUMENTS",
                    "requires": ["label-column", "binary-label-column", "binary-threshold"],
                },
                indent=2,
            )
        )
        return 2
    if args.label_authority_csv is not None and not binary_audit_requested:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_LVEF_AUTHORITY_REQUIRES_BINARY_AUDIT",
                    "requires": ["label-column", "binary-label-column", "binary-threshold"],
                },
                indent=2,
            )
        )
        return 2
    frames: dict[str, pd.DataFrame] = {}
    missing_names: list[str] = []
    for raw in args.modality:
        name, path = parse_named_path(raw)
        if not path.exists():
            missing_names.append(name)
            continue
        if name in frames or name in {
            "label_authority",
            "panel_wide_authority",
            "panel_long_authority",
        }:
            print(json.dumps({"status": "BLOCKED_DUPLICATE_OR_RESERVED_SOURCE_NAME"}, indent=2))
            return 2
        frames[name] = load_table(path)
    if missing_names or len(frames) < 2:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_MISSING_INPUT",
                    "n_missing_inputs": len(missing_names),
                    "missing_input_names": sorted(missing_names),
                    "n_modalities_loaded": len(frames),
                },
                indent=2,
            )
        )
        return 2
    prediction_modalities = sorted(frames)
    authority_sources: list[str] = []
    if args.label_authority_csv is not None:
        if not args.label_authority_csv.exists():
            print(json.dumps({"status": "BLOCKED_MISSING_LABEL_AUTHORITY"}, indent=2))
            return 2
        assert args.label_column is not None and args.binary_label_column is not None
        frames["label_authority"] = normalize_lvef_label_authority(
            load_table(args.label_authority_csv),
            continuous_output_column=args.label_column,
            binary_output_column=args.binary_label_column,
        )
        authority_sources.append("label_authority")

    definition_targets: set[str] = set()
    definition_integrity: dict[str, Any] = {
        "source": "target_definition",
        "definition_supplied": args.target_definition_csv is not None,
        "n_definition_rows": 0,
        "n_definition_nonblank_rows": 0,
        "n_definition_unique_targets": 0,
        "n_definition_blank_rows": 0,
        "n_definition_duplicate_rows": 0,
        "expected_target_count": args.expected_target_count,
        "raw_row_count_matches_expected": args.expected_target_count is None,
        "unique_target_count_matches_expected": args.expected_target_count is None,
        "definition_names_nonblank_and_unique": args.target_definition_csv is None,
        "target_definition_valid": args.target_definition_csv is None and args.expected_target_count is None,
    }
    panel_source_sets: dict[str, set[str]] = {}
    if args.target_definition_csv is not None:
        if not args.target_definition_csv.exists():
            print(json.dumps({"status": "BLOCKED_MISSING_TARGET_DEFINITION"}, indent=2))
            return 2
        definition_frame = load_table(args.target_definition_csv)
        definition_col = resolve_column(
            definition_frame,
            ("task_col", "task", "target_name"),
            required=True,
            label="target definition task column",
        )
        assert definition_col is not None
        definition_values = definition_frame[definition_col].astype("string").str.strip()
        blank_definition = definition_values.isna() | definition_values.eq("")
        nonblank_definition = definition_values.loc[~blank_definition]
        definition_targets = set(nonblank_definition.astype(str))
        duplicate_rows = int(len(nonblank_definition) - nonblank_definition.nunique())
        raw_matches = (
            args.expected_target_count is None
            or len(definition_frame) == args.expected_target_count
        )
        unique_matches = (
            args.expected_target_count is None
            or len(definition_targets) == args.expected_target_count
        )
        names_valid = (
            len(definition_frame) > 0
            and int(blank_definition.sum()) == 0
            and duplicate_rows == 0
        )
        definition_integrity.update(
            {
                "n_definition_rows": int(len(definition_frame)),
                "n_definition_nonblank_rows": int(len(nonblank_definition)),
                "n_definition_unique_targets": int(len(definition_targets)),
                "n_definition_blank_rows": int(blank_definition.sum()),
                "n_definition_duplicate_rows": duplicate_rows,
                "raw_row_count_matches_expected": raw_matches,
                "unique_target_count_matches_expected": unique_matches,
                "definition_names_nonblank_and_unique": names_valid,
                "target_definition_valid": names_valid and raw_matches and unique_matches,
            }
        )
    if panel_audit_requested:
        assert args.target_panel_wide_csv is not None
        assert args.target_panel_long_csv is not None
        assert args.label_column is not None
        for label, path in (
            ("panel_wide", args.target_panel_wide_csv),
            ("panel_long", args.target_panel_long_csv),
        ):
            if not path.exists():
                print(json.dumps({"status": "BLOCKED_MISSING_TARGET_PANEL", "panel": label}, indent=2))
                return 2
        wide_authority, long_authority = normalize_multitask_panel_authorities(
            load_table(args.target_panel_wide_csv),
            load_table(args.target_panel_long_csv),
            label_output_column=args.label_column,
            task_prefix=args.task_prefix,
        )
        frames["panel_wide_authority"] = wide_authority
        frames["panel_long_authority"] = long_authority
        authority_sources.extend(["panel_wide_authority", "panel_long_authority"])
        panel_source_sets["panel_wide"] = long_or_wide_task_set(
            wide_authority, args.task_prefix
        )
        panel_source_sets["panel_long"] = long_or_wide_task_set(
            long_authority, args.task_prefix
        )

    missing_subject_id = [
        name for name, frame in frames.items() if resolve_column(frame, SUBJECT_COLUMNS) is None
    ]
    if missing_subject_id:
        print(
            json.dumps(
                {"status": "BLOCKED_SUBJECT_ID_REQUIRED", "modalities": missing_subject_id},
                indent=2,
            )
        )
        return 2

    missing_split = [
        name for name, frame in frames.items() if resolve_column(frame, SPLIT_COLUMNS) is None
    ]
    if missing_split:
        print(
            json.dumps(
                {"status": "BLOCKED_SPLIT_COLUMN_REQUIRED", "modalities": missing_split},
                indent=2,
            )
        )
        return 2

    split_names: set[str] = set()
    for frame in frames.values():
        split_col = resolve_column(frame, SPLIT_COLUMNS, required=True)
        assert split_col is not None
        observed_splits = frame[split_col].dropna().astype(str).str.strip().str.lower()
        split_names.update(value for value in observed_splits if value)
    ordered_splits: list[str | None] = [None] + sorted(split_names)

    observed_targets = target_names(frames, args.target_column, args.task_prefix)
    targets = sorted(set(observed_targets) | definition_targets)
    target_source_sets = {
        **panel_source_sets,
        **{
            f"modality_{name}": long_or_wide_task_set(frames[name], args.task_prefix)
            for name in prediction_modalities
        },
    }
    target_set_rows = (
        target_set_reconciliation(definition_targets, target_source_sets)
        if definition_targets
        else []
    )
    target_set_failures = sum(not row["target_sets_identical"] for row in target_set_rows)
    if not definition_integrity["target_definition_valid"]:
        target_set_failures += 1
    aggregate_rows: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, str]] = []
    identity_failures = 0
    subject_study_pair_failures = 0
    subject_split_assignment_failures = 0
    binary_endpoint_failures = 0
    for target in targets:
        for split in ordered_splits:
            split_label = "all" if split is None else split
            study_common: set[str] = set()
            for identifier_level, candidates in (("study", STUDY_COLUMNS), ("subject", SUBJECT_COLUMNS)):
                try:
                    sets, common = compute_common_ids(
                        frames,
                        id_candidates=candidates,
                        target=target,
                        split=split,
                    )
                except ValueError:
                    if identifier_level == "subject":
                        continue
                    raise
                all_identical = all(values == next(iter(sets.values())) for values in sets.values())
                common_empty = len(common) == 0
                if common_empty:
                    all_identical = False
                if identifier_level == "study":
                    study_common = common
                if not all_identical:
                    identity_failures += 1
                row: dict[str, Any] = {
                    "target": target,
                    "split": split_label,
                    "identifier_level": identifier_level,
                    "sets_identical": all_identical,
                    "common_empty": common_empty,
                    "n_common": len(common),
                }
                for name, values in sorted(sets.items()):
                    row[f"n_{name}"] = len(values)
                    row[f"n_{name}_outside_common"] = len(values - common)
                if args.label_column and identifier_level == "study":
                    equal, mismatch_count, label_restricted = continuous_labels_equal_on_common(
                        frames,
                        common,
                        args.label_column,
                        candidates,
                        target=target,
                        split=split,
                    )
                    row["labels_identical_on_common"] = equal
                    row["n_label_mismatches"] = mismatch_count
                    if not equal:
                        identity_failures += 1
                    if args.restricted_output_dir is not None:
                        restricted_rows.extend(label_restricted)
                aggregate_rows.append(row)
                if args.restricted_output_dir is not None:
                    for name, values in sets.items():
                        for identifier in sorted(values - common):
                            restricted_rows.append(
                                restricted_discrepancy(
                                    target=target,
                                    split=split_label,
                                    identifier_level=identifier_level,
                                    modality=name,
                                    identifier=identifier,
                                    status="OUTSIDE_COMMON_INTERSECTION",
                                )
                            )

            pair_row, pair_restricted, pair_failed = subject_study_pair_reconciliation(
                frames,
                target=target,
                split=split,
            )
            aggregate_rows.append(pair_row)
            if args.restricted_output_dir is not None:
                restricted_rows.extend(pair_restricted)
            if pair_failed:
                identity_failures += 1
                subject_study_pair_failures += 1

            if binary_audit_requested:
                assert args.label_column is not None
                assert args.binary_label_column is not None
                assert args.binary_threshold is not None
                binary_row, binary_restricted, binary_failed = binary_endpoint_reconciliation(
                    frames,
                    study_common,
                    continuous_label_column=args.label_column,
                    binary_label_column=args.binary_label_column,
                    threshold=args.binary_threshold,
                    target=target,
                    split=split,
                )
                aggregate_rows.append(binary_row)
                if args.restricted_output_dir is not None:
                    restricted_rows.extend(binary_restricted)
                if binary_failed:
                    identity_failures += 1
                    binary_endpoint_failures += 1

        split_row, split_restricted, split_failed = subject_split_assignment_reconciliation(
            frames,
            target=target,
        )
        aggregate_rows.append(split_row)
        if args.restricted_output_dir is not None:
            restricted_rows.extend(split_restricted)
        if split_failed:
            identity_failures += 1
            subject_split_assignment_failures += 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(pd.DataFrame(aggregate_rows), args.output_dir / "common_denominator_audit.csv")
    write_aggregate_csv(
        pd.DataFrame(
            target_set_rows,
            columns=[
                "source",
                "n_expected_targets",
                "n_observed_targets",
                "n_expected_missing",
                "n_unexpected_targets",
                "target_sets_identical",
            ],
        ),
        args.output_dir / "target_set_reconciliation.csv",
    )
    write_aggregate_csv(
        pd.DataFrame([definition_integrity]),
        args.output_dir / "target_definition_integrity.csv",
    )
    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(
            restricted_rows,
            columns=[
                "target",
                "split",
                "identifier_level",
                "modality",
                "identifier",
                "subject_id",
                "study_id",
                "source_row",
                "observed_split",
                "observed_continuous",
                "observed_binary",
                "expected_binary",
                "status",
            ],
        ).to_csv(restricted_dir / "common_denominator_discrepancies_restricted.csv", index=False)
        restricted_written = True

    summary = {
        "audit": "common_evaluation_denominators",
        "modalities": prediction_modalities,
        "comparison_sources": sorted(frames),
        "authority_sources": sorted(authority_sources),
        "label_authority_supplied": args.label_authority_csv is not None,
        "multitask_panel_authorities_supplied": panel_audit_requested,
        "n_targets": len(targets),
        "n_definition_targets": len(definition_targets),
        "n_definition_rows": definition_integrity["n_definition_rows"],
        "n_definition_blank_rows": definition_integrity["n_definition_blank_rows"],
        "n_definition_duplicate_rows": definition_integrity["n_definition_duplicate_rows"],
        "target_definition_valid": definition_integrity["target_definition_valid"],
        "expected_target_count": args.expected_target_count,
        "n_target_set_failures": target_set_failures,
        "splits_audited": ["all"] + sorted(split_names),
        "n_target_split_combinations": len(targets) * len(ordered_splits),
        "n_identity_failures": identity_failures,
        "n_subject_study_pair_failures": subject_study_pair_failures,
        "n_subject_split_assignment_failures": subject_split_assignment_failures,
        "binary_endpoint_audit_requested": binary_audit_requested,
        "binary_threshold": args.binary_threshold,
        "continuous_label_comparison_rtol": CONTINUOUS_LABEL_RTOL,
        "continuous_label_comparison_atol": CONTINUOUS_LABEL_ATOL,
        "n_binary_endpoint_failures": binary_endpoint_failures,
        "n_aggregate_audit_rows": len(aggregate_rows),
        "restricted_discrepancies_written": restricted_written,
        "model_fitting_performed": False,
        "test_performance_computed": False,
    }
    write_json(summary, args.output_dir / "common_denominator_audit.summary.json")
    print(json.dumps(summary, indent=2))
    return 1 if identity_failures or target_set_failures else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
