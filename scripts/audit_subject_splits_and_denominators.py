#!/usr/bin/env python3
"""Audit subject split integrity and aggregate cohort/task denominators."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
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


VALID_SPLITS = {"train", "val", "test"}
MISSING_IDENTIFIER_TOKENS = {"null", "none", "nan", "<na>"}


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
    parser.add_argument("--split-map", type=Path, required=True)
    parser.add_argument("--cohort", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument(
        "--exact-split-cohort",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Require exact subject-set equality between this cohort and the split map.",
    )
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
    work["_audit_subject"] = canonical_identifier_series(work[subject_col])
    work["_audit_split"] = work[split_col].astype("string").str.strip().str.lower()
    missing_subject_mask = work["_audit_subject"].isna()
    if study_col:
        work["_audit_study"] = canonical_identifier_series(work[study_col])
        missing_study_mask = work["_audit_study"].isna()
    else:
        missing_study_mask = pd.Series(False, index=work.index)
    invalid = work[~work["_audit_split"].isin(VALID_SPLITS)]
    valid_subject_rows = work.loc[~missing_subject_mask]
    subject_n_splits = valid_subject_rows.groupby("_audit_subject")["_audit_split"].nunique(
        dropna=False
    )
    cross_subjects = set(subject_n_splits[subject_n_splits > 1].index.astype(str))
    duplicate_subject_rows = valid_subject_rows[
        valid_subject_rows["_audit_subject"].duplicated(keep=False)
    ]
    duplicate_subjects = set(duplicate_subject_rows["_audit_subject"].dropna().astype(str))
    cross_studies: set[str] = set()
    duplicate_study_rows = 0
    if study_col:
        valid_study_rows = work.loc[~missing_study_mask]
        study_n_splits = valid_study_rows.groupby("_audit_study")["_audit_split"].nunique(
            dropna=False
        )
        cross_studies = set(study_n_splits[study_n_splits > 1].index.astype(str))
        duplicate_study_rows = int(valid_study_rows["_audit_study"].duplicated().sum())

    warnings: list[dict[str, str]] = []
    warnings.extend({"warning_type": "SUBJECT_IN_MULTIPLE_SPLITS", "subject_id": value, "study_id": ""} for value in sorted(cross_subjects))
    warnings.extend(
        {"warning_type": "DUPLICATE_SUBJECT_SPLIT_ROW", "subject_id": value, "study_id": ""}
        for value in sorted(duplicate_subjects)
    )
    warnings.extend({"warning_type": "STUDY_IN_MULTIPLE_SPLITS", "subject_id": "", "study_id": value} for value in sorted(cross_studies))
    for index in work.index[missing_subject_mask]:
        study_value = work.at[index, "_audit_study"] if study_col else pd.NA
        warnings.append(
            {
                "warning_type": "MISSING_OR_BLANK_SUBJECT_ID",
                "subject_id": "",
                "study_id": "" if pd.isna(study_value) else str(study_value),
            }
        )
    for index in work.index[missing_study_mask]:
        subject_value = work.at[index, "_audit_subject"]
        warnings.append(
            {
                "warning_type": "MISSING_OR_BLANK_STUDY_ID",
                "subject_id": "" if pd.isna(subject_value) else str(subject_value),
                "study_id": "",
            }
        )
    for _, row in invalid.iterrows():
        warnings.append(
            {
                "warning_type": f"INVALID_SPLIT:{row['_audit_split']}",
                "subject_id": "" if pd.isna(row["_audit_subject"]) else str(row["_audit_subject"]),
                "study_id": (
                    ""
                    if not study_col or pd.isna(row["_audit_study"])
                    else str(row["_audit_study"])
                ),
            }
        )

    summary = {
        "n_rows": int(len(work)),
        "n_subjects": int(work["_audit_subject"].nunique(dropna=True)),
        "n_studies": int(work["_audit_study"].nunique(dropna=True)) if study_col else None,
        "n_rows_missing_subject_id": int(missing_subject_mask.sum()),
        "n_rows_missing_study_id": int(missing_study_mask.sum()) if study_col else None,
        "n_subjects_in_multiple_splits": len(cross_subjects),
        "n_duplicate_subject_rows": int(valid_subject_rows["_audit_subject"].duplicated().sum()),
        "n_studies_in_multiple_splits": len(cross_studies),
        "n_duplicate_study_rows": duplicate_study_rows,
        "n_invalid_split_rows": int(len(invalid)),
        "valid": (
            not cross_subjects
            and not duplicate_subjects
            and not cross_studies
            and duplicate_study_rows == 0
            and invalid.empty
            and not missing_subject_mask.any()
            and not missing_study_mask.any()
        ),
    }
    return summary, pd.DataFrame(warnings, columns=["warning_type", "subject_id", "study_id"])


def cohort_summary(name: str, frame: pd.DataFrame) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    subject_col = resolve_column(frame, SUBJECT_COLUMNS)
    study_col = resolve_column(frame, STUDY_COLUMNS)
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    subjects = set(canonical_identifier_series(frame[subject_col]).dropna()) if subject_col else set()
    studies = set(canonical_identifier_series(frame[study_col]).dropna()) if study_col else set()
    rows: list[dict[str, Any]] = []
    groups = [("all", frame)] if split_col is None else list(frame.groupby(split_col, dropna=False))
    for split_name, group in groups:
        rows.append(
            {
                "cohort": name,
                "split": str(split_name),
                "n_rows": int(len(group)),
                "n_subjects": (
                    int(canonical_identifier_series(group[subject_col]).nunique(dropna=True))
                    if subject_col
                    else None
                ),
                "n_studies": (
                    int(canonical_identifier_series(group[study_col]).nunique(dropna=True))
                    if study_col
                    else None
                ),
            }
        )
    return rows, subjects, studies


def exact_subject_coverage(
    name: str, split_map: pd.DataFrame, cohort: pd.DataFrame
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    split_subject_col = resolve_column(
        split_map, SUBJECT_COLUMNS, required=True, label="split-map subject identifier"
    )
    cohort_subject_col = resolve_column(
        cohort, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {name}"
    )
    assert split_subject_col is not None and cohort_subject_col is not None
    split_subject_values = canonical_identifier_series(split_map[split_subject_col])
    cohort_subject_values = canonical_identifier_series(cohort[cohort_subject_col])
    n_split_missing = int(split_subject_values.isna().sum())
    n_cohort_missing = int(cohort_subject_values.isna().sum())
    split_subjects = set(split_subject_values.dropna())
    cohort_subjects = set(cohort_subject_values.dropna())
    missing_from_split = cohort_subjects - split_subjects
    outside_cohort = split_subjects - cohort_subjects
    row = {
        "cohort": name,
        "n_cohort_subjects": len(cohort_subjects),
        "n_split_map_subjects": len(split_subjects),
        "n_cohort_rows_missing_subject_id": n_cohort_missing,
        "n_split_map_rows_missing_subject_id": n_split_missing,
        "n_cohort_subjects_missing_from_split": len(missing_from_split),
        "n_split_map_subjects_outside_cohort": len(outside_cohort),
        "subject_sets_identical": (
            cohort_subjects == split_subjects and n_cohort_missing == 0 and n_split_missing == 0
        ),
    }
    warnings = [
        {
            "cohort": name,
            "warning_type": "COHORT_SUBJECT_MISSING_FROM_SPLIT_MAP",
            "subject_id": identifier,
        }
        for identifier in sorted(missing_from_split)
    ]
    warnings.extend(
        {
            "cohort": name,
            "warning_type": "SPLIT_MAP_SUBJECT_OUTSIDE_COHORT",
            "subject_id": identifier,
        }
        for identifier in sorted(outside_cohort)
    )
    return row, warnings


def main() -> int:
    args = parse_args()
    if not args.split_map.exists():
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "n_missing_inputs": 1}, indent=2))
        return 2

    split_map = load_table(args.split_map)
    split_summary, split_warnings = audit_split_assignments(split_map)
    split_col = resolve_column(split_map, SPLIT_COLUMNS, required=True)
    subject_col = resolve_column(split_map, SUBJECT_COLUMNS, required=True)
    assert split_col is not None and subject_col is not None

    split_counts = (
        split_map.assign(
            _split=split_map[split_col].astype("string").str.strip().str.lower(),
            _subject=canonical_identifier_series(split_map[subject_col]),
        )
        .groupby("_split")["_subject"]
        .nunique(dropna=True)
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
        .assign(
            _subject=canonical_identifier_series(split_map[subject_col]),
            _split=split_map[split_col].astype("string").str.strip().str.lower(),
        )
        .dropna(subset=["_subject"])
        .loc[lambda frame: frame["_split"].isin(VALID_SPLITS)]
        .drop_duplicates("_subject")
        .set_index("_subject")["_split"]
        .to_dict()
    )
    for raw in args.cohort:
        name, path = parse_named_path(raw)
        if not path.exists():
            load_errors.append({"cohort": name, "error": "MISSING_INPUT"})
            continue
        frame = load_table(path)
        cohort_subject_col = resolve_column(frame, SUBJECT_COLUMNS)
        cohort_study_col = resolve_column(frame, STUDY_COLUMNS)
        if cohort_subject_col is None:
            load_errors.append({"cohort": name, "error": "MISSING_SUBJECT_ID_COLUMN"})
            continue
        if cohort_study_col is None:
            load_errors.append({"cohort": name, "error": "MISSING_STUDY_ID_COLUMN"})
            continue
        missing_subjects = int(missing_identifier_mask(frame[cohort_subject_col]).sum())
        missing_studies = int(missing_identifier_mask(frame[cohort_study_col]).sum())
        if missing_subjects:
            load_errors.append(
                {
                    "cohort": name,
                    "error": f"MISSING_OR_BLANK_SUBJECT_ID_ROWS:{missing_subjects}",
                }
            )
        if missing_studies:
            load_errors.append(
                {
                    "cohort": name,
                    "error": f"MISSING_OR_BLANK_STUDY_ID_ROWS:{missing_studies}",
                }
            )
        if missing_subjects or missing_studies:
            continue
        rows, subjects, studies = cohort_summary(name, frame)
        cohort_rows.extend(rows)
        cohort_sets[name] = (subjects, studies)
        cohort_split_col = resolve_column(frame, SPLIT_COLUMNS)
        cohort_subject_values = canonical_identifier_series(frame[cohort_subject_col])
        unique_subject_rows = frame[
            [cohort_subject_col] + ([cohort_split_col] if cohort_split_col else [])
        ].copy()
        unique_subject_rows["_audit_subject"] = cohort_subject_values
        unique_subject_rows = unique_subject_rows.drop_duplicates(
            ["_audit_subject"] + ([cohort_split_col] if cohort_split_col else [])
        )
        for _, cohort_row in unique_subject_rows.iterrows():
            identifier = str(cohort_row["_audit_subject"])
            expected_split = split_lookup.get(identifier)
            if expected_split is None:
                cohort_warning_rows.append(
                    {"cohort": name, "warning_type": "SUBJECT_ABSENT_FROM_SPLIT_MAP", "subject_id": identifier}
                )
            elif (
                cohort_split_col
                and str(cohort_row[cohort_split_col]).strip().lower() != expected_split
            ):
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
                }
            )

    exact_coverage_rows: list[dict[str, Any]] = []
    exact_coverage_warning_rows: list[dict[str, str]] = []
    for raw in args.exact_split_cohort:
        name, path = parse_named_path(raw)
        if not path.exists():
            load_errors.append({"cohort": name, "error": "MISSING_EXACT_SPLIT_COHORT"})
            continue
        frame = load_table(path)
        row, warnings = exact_subject_coverage(name, split_map, frame)
        exact_coverage_rows.append(row)
        exact_coverage_warning_rows.extend(warnings)

    task_rows: list[dict[str, Any]] = []
    if args.multitask_panel is not None:
        if not args.multitask_panel.exists():
            load_errors.append({"cohort": "multitask_panel", "error": "MISSING_INPUT"})
        else:
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
    write_aggregate_csv(
        pd.DataFrame(
            exact_coverage_rows,
            columns=[
                "cohort",
                "n_cohort_subjects",
                "n_split_map_subjects",
                "n_cohort_rows_missing_subject_id",
                "n_split_map_rows_missing_subject_id",
                "n_cohort_subjects_missing_from_split",
                "n_split_map_subjects_outside_cohort",
                "subject_sets_identical",
            ],
        ),
        args.output_dir / "exact_split_subject_coverage.csv",
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
        pd.DataFrame(
            exact_coverage_warning_rows,
            columns=["cohort", "warning_type", "subject_id"],
        ).to_csv(restricted_dir / "exact_split_coverage_warnings_restricted.csv", index=False)
        restricted_written = True

    identity_failures = sum(
        not row["subject_sets_identical"] or not row["study_sets_identical"] for row in comparison_rows
    )
    exact_coverage_failures = sum(not row["subject_sets_identical"] for row in exact_coverage_rows)
    summary = {
        "audit": "subject_splits_and_denominators",
        "split_integrity": split_summary,
        "n_cohorts_loaded": len(cohort_sets),
        "n_cohort_identity_failures": identity_failures,
        "n_exact_split_coverage_failures": exact_coverage_failures,
        "n_load_warnings": len(load_errors),
        "n_cohort_split_warnings": len(cohort_warning_rows),
        "restricted_warnings_written": restricted_written,
        "patient_level_output_in_repository": False,
    }
    write_json(summary, args.output_dir / "subject_split_denominator_audit.summary.json")
    print(json.dumps(summary, indent=2))
    if load_errors:
        return 2
    return 1 if (
        not split_summary["valid"]
        or identity_failures
        or exact_coverage_failures
        or cohort_warning_rows
    ) else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
