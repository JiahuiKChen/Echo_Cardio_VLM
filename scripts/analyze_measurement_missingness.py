#!/usr/bin/env python3
"""Describe observed structured-measurement missingness without fitting models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    load_table,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument("--min-pattern-count", type=int, default=10)
    parser.add_argument(
        "--analysis-split",
        choices=("train", "val", "test", "all"),
        default="train",
        help="Defaults to train so empirical missingness masks cannot use validation/test information.",
    )
    return parser.parse_args()


def missingness_rows(
    panel: pd.DataFrame, task_columns: list[str], min_count: int
) -> tuple[list[dict[str, Any]], int]:
    split_col = resolve_column(panel, SPLIT_COLUMNS)
    groups = [("all", panel)] if split_col is None else list(panel.groupby(split_col, dropna=False))
    rows: list[dict[str, Any]] = []
    suppressed = 0
    for split_name, group in groups:
        for task in task_columns:
            observed = pd.to_numeric(group[task], errors="coerce").notna()
            n_observed = int(observed.sum())
            n_missing = int((~observed).sum())
            suppress_cell = n_observed < min_count or n_missing < min_count
            suppressed += int(suppress_cell)
            rows.append(
                {
                    "split": str(split_name),
                    "task": task,
                    "n_rows": int(len(group)),
                    "n_observed": None if suppress_cell else n_observed,
                    "n_missing": None if suppress_cell else n_missing,
                    "missing_rate": None if suppress_cell or not len(group) else float((~observed).mean()),
                    "cell_suppressed": suppress_cell,
                }
            )
    return rows, suppressed


def pairwise_rows(
    panel: pd.DataFrame, task_columns: list[str], min_count: int
) -> tuple[list[dict[str, Any]], int]:
    observed = panel[task_columns].apply(pd.to_numeric, errors="coerce").notna()
    rows: list[dict[str, Any]] = []
    suppressed = 0
    for index, left in enumerate(task_columns):
        for right in task_columns[index:]:
            both = observed[left] & observed[right]
            either = observed[left] | observed[right]
            cell_counts = (
                int(both.sum()),
                int((either & ~both).sum()),
                int((~either).sum()),
            )
            if any(count < min_count for count in cell_counts):
                suppressed += 1
                continue
            rows.append(
                {
                    "left_task": left,
                    "right_task": right,
                    "n_both_observed": int(both.sum()),
                    "n_either_observed": int(either.sum()),
                    "jaccard_observed": float(both.sum() / either.sum()) if either.any() else None,
                }
            )
    return rows, suppressed


def pattern_rows(panel: pd.DataFrame, task_columns: list[str], min_count: int) -> tuple[list[dict[str, Any]], int]:
    observed = panel[task_columns].apply(pd.to_numeric, errors="coerce").notna()
    pattern = observed.astype("int8").astype(str).agg("".join, axis=1)
    counts = pattern.value_counts()
    kept = counts[counts >= min_count]
    rows = [
        {
            "availability_pattern": value,
            "n_rows": int(count),
            "fraction": float(count / len(panel)) if len(panel) else None,
        }
        for value, count in kept.items()
    ]
    suppressed = int(counts[counts < min_count].sum())
    return rows, suppressed


def main() -> int:
    args = parse_args()
    if not args.panel_csv.exists():
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "n_missing_inputs": 1}, indent=2))
        return 2
    panel = load_table(args.panel_csv)
    split_col = resolve_column(panel, SPLIT_COLUMNS)
    if args.analysis_split != "all":
        if split_col is None:
            print(json.dumps({"status": "BLOCKED_SPLIT_COLUMN_REQUIRED", "analysis_split": args.analysis_split}, indent=2))
            return 2
        panel = panel[panel[split_col].astype(str).str.strip().str.lower().eq(args.analysis_split)].copy()
        if panel.empty:
            print(json.dumps({"status": "BLOCKED_EMPTY_ANALYSIS_SPLIT", "analysis_split": args.analysis_split}, indent=2))
            return 2
    tasks = [column for column in panel.columns if str(column).startswith(args.task_prefix)]
    if not tasks:
        print(json.dumps({"status": "BLOCKED_NO_TASK_COLUMNS", "task_prefix": args.task_prefix}, indent=2))
        return 2

    missing_rows, suppressed_task_rows = missingness_rows(panel, tasks, args.min_pattern_count)
    co_observation_rows, suppressed_pairs = pairwise_rows(panel, tasks, args.min_pattern_count)
    patterns, suppressed_rows = pattern_rows(panel, tasks, args.min_pattern_count)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(pd.DataFrame(missing_rows), args.output_dir / "measurement_missingness_by_task.csv")
    write_aggregate_csv(pd.DataFrame(co_observation_rows), args.output_dir / "measurement_pairwise_coobservation.csv")
    write_aggregate_csv(
        pd.DataFrame(patterns, columns=["availability_pattern", "n_rows", "fraction"]),
        args.output_dir / "measurement_availability_patterns_suppression_safe.csv",
    )
    summary = {
        "audit": "measurement_missingness",
        "n_rows": int(len(panel)),
        "n_tasks": len(tasks),
        "analysis_split": args.analysis_split,
        "min_pattern_count": args.min_pattern_count,
        "n_rows_in_suppressed_patterns": suppressed_rows,
        "n_suppressed_task_rows": suppressed_task_rows,
        "n_suppressed_task_pairs": suppressed_pairs,
        "availability_pattern_task_order": tasks,
        "interpretation": [
            "Missingness is a property of the historical reporting process and may be MNAR.",
            "Observed-label evaluation does not establish accuracy for genuinely missing labels.",
            "Natural patterns may inform simulated masking only after patterns are learned from training data.",
            "Reference remeasurement is required to validate predictions for truly missing targets.",
        ],
        "model_fitting_performed": False,
        "test_performance_computed": False,
        "patient_level_output_written": False,
    }
    write_json(summary, args.output_dir / "measurement_missingness.summary.json")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
