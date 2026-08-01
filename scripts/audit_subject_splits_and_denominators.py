#!/usr/bin/env python3
"""Audit subject split integrity and aggregate cohort/task denominators."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    id_set_hash,
    load_table,
    normalized_ids,
    parse_named_path,
    require_restricted_path,
    resolve_column,
    write_aggregate_csv,
    write_json,
)


VALID_SPLITS = {"train", "val", "test"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-map", type=Path, required=True)
    parser.add_argument("--cohort", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--multitask-panel", type=Path)
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    return parser.parse_args()


def audit_split_assignments(split_map: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    subject_col = resolve_column(split_map, SUBJECT_COLUMNS, required=True, label="subject identifier")
    split_col = resolve_column(split_map, SPLIT_COLUMNS, required=True, label="split")
    study_col = resolve_column(split_map, STUDY_COLUMNS)
    assert subject_col is not None and split_col is not None

    work = split_map.copy()
    work[split_col] = work[split_col].astype(str).str.strip().str.lower()
    invalid = work[~work[split_col].isin(VALID_SPLITS)]
    subject_n_splits = work.groupby(subject_col)[split_col].nunique(dropna=False)
    cross_subjects = set(subject_n_splits[subject_n_splits > 1].index.astype(str))
    cross_studies: set[str] = set()
    if study_col:
        study_n_splits = work.groupby(study_col)[split_col].nunique(dropna=False)
        cross_studies = set(study_n_splits[study_n_splits > 1].index.astype(str))

    warnings: list[dict[str, str]] = []
    warnings.extend({"warning_type": "SUBJECT_IN_MULTIPLE_SPLITS", "subject_id": value, "study_id": ""} for value in sorted(cross_subjects))
    warnings.extend({"warning_type": "STUDY_IN_MULTIPLE_SPLITS", "subject_id": "", "study_id": value} for value in sorted(cross_studies))
    for _, row in invalid.iterrows():
        warnings.append(
            {
                "warning_type": f"INVALID_SPLIT:{row[split_col]}",
                "subject_id": str(row[subject_col]),
                "study_id": str(row[study_col]) if study_col else "",
            }
        )

    summary = {
        "n_rows": int(len(work)),
        "n_subjects": int(work[subject_col].nunique(dropna=True)),
        "n_studies": int(work[study_col].nunique(dropna=True)) if study_col else None,
        "n_subjects_in_multiple_splits": len(cross_subjects),
        "n_studies_in_multiple_splits": len(cross_studies),
        "n_invalid_split_rows": int(len(invalid)),
        "valid": not cross_subjects and not cross_studies and invalid.empty,
    }
    return summary, pd.DataFrame(warnings, columns=["warning_type", "subject_id", "study_id"])


def cohort_summary(name: str, frame: pd.DataFrame) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    subject_col = resolve_column(frame, SUBJECT_COLUMNS)
    study_col = resolve_column(frame, STUDY_COLUMNS)
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    subjects = normalized_ids(frame[subject_col]) if subject_col else set()
    studies = normalized_ids(frame[study_col]) if study_col else set()
    rows: list[dict[str, Any]] = []
    groups = [("all", frame)] if split_col is None else list(frame.groupby(split_col, dropna=False))
    for split_name, group in groups:
        rows.append(
            {
                "cohort": name,
                "split": str(split_name),
                "n_rows": int(len(group)),
                "n_subjects": int(group[subject_col].nunique()) if subject_col else None,
                "n_studies": int(group[study_col].nunique()) if study_col else None,
            }
        )
    return rows, subjects, studies


def main() -> int:
    args = parse_args()
    if not args.split_map.exists():
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "missing": str(args.split_map)}, indent=2))
        return 2

    split_map = load_table(args.split_map)
    split_summary, split_warnings = audit_split_assignments(split_map)
    split_col = resolve_column(split_map, SPLIT_COLUMNS, required=True)
    subject_col = resolve_column(split_map, SUBJECT_COLUMNS, required=True)
    assert split_col is not None and subject_col is not None

    split_counts = (
        split_map.assign(_split=split_map[split_col].astype(str).str.lower())
        .groupby("_split")[subject_col]
        .nunique()
        .rename("n_subjects")
        .reset_index()
        .rename(columns={"_split": "split"})
    )

    cohort_rows: list[dict[str, Any]] = []
    cohort_sets: dict[str, tuple[set[str], set[str]]] = {}
    load_errors: list[dict[str, str]] = []
    cohort_warning_rows: list[dict[str, str]] = []
    split_lookup = (
        split_map[[subject_col, split_col]]
        .assign(**{subject_col: split_map[subject_col].astype(str), split_col: split_map[split_col].astype(str).str.lower()})
        .drop_duplicates(subject_col)
        .set_index(subject_col)[split_col]
        .to_dict()
    )
    for raw in args.cohort:
        name, path = parse_named_path(raw)
        if not path.exists():
            load_errors.append({"cohort": name, "error": "MISSING_INPUT"})
            continue
        frame = load_table(path)
        rows, subjects, studies = cohort_summary(name, frame)
        cohort_rows.extend(rows)
        cohort_sets[name] = (subjects, studies)
        cohort_subject_col = resolve_column(frame, SUBJECT_COLUMNS)
        cohort_split_col = resolve_column(frame, SPLIT_COLUMNS)
        if cohort_subject_col:
            unique_subject_rows = frame[[cohort_subject_col] + ([cohort_split_col] if cohort_split_col else [])].drop_duplicates()
            for _, cohort_row in unique_subject_rows.iterrows():
                identifier = str(cohort_row[cohort_subject_col])
                expected_split = split_lookup.get(identifier)
                if expected_split is None:
                    cohort_warning_rows.append(
                        {"cohort": name, "warning_type": "SUBJECT_ABSENT_FROM_SPLIT_MAP", "subject_id": identifier}
                    )
                elif cohort_split_col and str(cohort_row[cohort_split_col]).lower() != expected_split:
                    cohort_warning_rows.append(
                        {"cohort": name, "warning_type": "COHORT_SPLIT_DISAGREES_WITH_AUTHORITY", "subject_id": identifier}
                    )

    comparison_rows: list[dict[str, Any]] = []
    names = sorted(cohort_sets)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            left_subjects, left_studies = cohort_sets[left]
            right_subjects, right_studies = cohort_sets[right]
            comparison_rows.append(
                {
                    "left_cohort": left,
                    "right_cohort": right,
                    "subject_sets_identical": left_subjects == right_subjects,
                    "study_sets_identical": left_studies == right_studies,
                    "n_subjects_left_only": len(left_subjects - right_subjects),
                    "n_subjects_right_only": len(right_subjects - left_subjects),
                    "n_studies_left_only": len(left_studies - right_studies),
                    "n_studies_right_only": len(right_studies - left_studies),
                    "subject_intersection_sha256": id_set_hash(left_subjects & right_subjects),
                    "study_intersection_sha256": id_set_hash(left_studies & right_studies),
                }
            )

    task_rows: list[dict[str, Any]] = []
    if args.multitask_panel is not None and args.multitask_panel.exists():
        panel = load_table(args.multitask_panel)
        panel_split_col = resolve_column(panel, SPLIT_COLUMNS)
        groups = [("all", panel)] if panel_split_col is None else list(panel.groupby(panel_split_col, dropna=False))
        for task in [column for column in panel.columns if str(column).startswith(args.task_prefix)]:
            numeric = pd.to_numeric(panel[task], errors="coerce")
            for split_name, group in groups:
                task_rows.append(
                    {
                        "task": str(task),
                        "split": str(split_name),
                        "n_with_target": int(numeric.loc[group.index].notna().sum()),
                    }
                )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(split_counts, args.output_dir / "subject_split_counts.csv")
    write_aggregate_csv(
        pd.DataFrame(cohort_rows, columns=["cohort", "split", "n_rows", "n_subjects", "n_studies"]),
        args.output_dir / "cohort_denominators.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(comparison_rows), args.output_dir / "cohort_set_comparisons.csv"
    )
    write_aggregate_csv(
        pd.DataFrame(task_rows, columns=["task", "split", "n_with_target"]),
        args.output_dir / "task_specific_denominators.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(load_errors, columns=["cohort", "error"]), args.output_dir / "cohort_load_warnings.csv"
    )
    if cohort_warning_rows:
        warning_counts = (
            pd.DataFrame(cohort_warning_rows)
            .groupby(["cohort", "warning_type"])
            .size()
            .reset_index(name="n_warnings")
        )
    else:
        warning_counts = pd.DataFrame(columns=["cohort", "warning_type", "n_warnings"])
    write_aggregate_csv(warning_counts, args.output_dir / "cohort_split_warning_counts.csv")

    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        split_warnings.to_csv(restricted_dir / "split_integrity_warnings_restricted.csv", index=False)
        pd.DataFrame(cohort_warning_rows, columns=["cohort", "warning_type", "subject_id"]).to_csv(
            restricted_dir / "cohort_split_warnings_restricted.csv", index=False
        )
        restricted_written = True

    identity_failures = sum(
        not row["subject_sets_identical"] or not row["study_sets_identical"] for row in comparison_rows
    )
    summary = {
        "audit": "subject_splits_and_denominators",
        "split_integrity": split_summary,
        "n_cohorts_loaded": len(cohort_sets),
        "n_cohort_identity_failures": identity_failures,
        "n_load_warnings": len(load_errors),
        "n_cohort_split_warnings": len(cohort_warning_rows),
        "restricted_warnings_written": restricted_written,
        "patient_level_output_in_repository": False,
    }
    write_json(summary, args.output_dir / "subject_split_denominator_audit.summary.json")
    print(json.dumps(summary, indent=2))
    return 1 if not split_summary["valid"] or identity_failures or cohort_warning_rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
