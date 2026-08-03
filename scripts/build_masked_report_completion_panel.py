#!/usr/bin/env python3
"""Dry-run target/family masking for strict or pragmatic report completion.

No imputation, scaling, model fitting, or test-performance computation occurs.
Optional row-level masked panels are restricted outputs and are refused inside
the repository.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    load_table,
    require_restricted_path,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-csv", type=Path, required=True)
    parser.add_argument("--registry-csv", type=Path, required=True)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--mode", choices=("strict", "pragmatic", "both"), default="both")
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    parser.add_argument("--write-restricted-panels", action="store_true")
    return parser.parse_args()


def normalize_name(value: Any) -> str:
    text = str(value).strip()
    if text.startswith("task__"):
        text = text[len("task__") :]
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text)).strip("_").lower()


def resolve_targets(
    panel_targets: set[str], registry_targets: set[str], requested_targets: list[str]
) -> list[str]:
    """Fail closed unless every frozen-panel target has registry coverage."""
    missing_registry_coverage = panel_targets - registry_targets
    if missing_registry_coverage:
        raise ValueError(
            f"Dependency registry lacks coverage for {len(missing_registry_coverage)} panel target(s)"
        )
    if requested_targets:
        normalized_requested = [normalize_name(target) for target in requested_targets]
        unavailable = set(normalized_requested) - panel_targets
        if unavailable:
            raise ValueError(f"Requested {len(unavailable)} target(s) outside the frozen panel")
        return sorted(set(normalized_requested))
    return sorted(panel_targets)


def mask_columns_for_target(
    panel_columns: list[str], registry: pd.DataFrame, target: str, mode: str, task_prefix: str = "task__"
) -> tuple[list[str], list[str], str]:
    normalized_target = normalize_name(target)
    target_column_candidates = [column for column in panel_columns if normalize_name(column) == normalized_target]
    if not target_column_candidates:
        raise ValueError(f"Target {target} is not present in the panel")
    target_column = target_column_candidates[0]
    inclusion_col = f"{mode}_panel_inclusion"
    if inclusion_col not in registry.columns:
        raise ValueError(f"Registry missing {inclusion_col}")
    rows = registry[registry["target"].map(normalize_name) == normalized_target].copy()
    if rows.empty:
        raise ValueError(f"Registry has no rows for target {target}")
    allowed_by_predictor: dict[str, bool] = {}
    for predictor, group in rows.groupby(rows["canonical_name"].map(normalize_name)):
        values = group[inclusion_col].astype(str).str.lower().isin({"true", "1", "yes"})
        allowed_by_predictor[str(predictor)] = bool(values.all())

    predictor_columns = [column for column in panel_columns if str(column).startswith(task_prefix)]
    masked: list[str] = []
    retained: list[str] = []
    for column in predictor_columns:
        canonical = normalize_name(column)
        allowed = allowed_by_predictor.get(canonical, False)  # unmapped predictors fail closed
        if canonical == normalized_target or not allowed:
            masked.append(column)
        else:
            retained.append(column)
    if target_column not in masked:
        raise AssertionError("Direct target was not removed from predictor columns")
    return masked, retained, target_column


def apply_mask_before_preprocessing(
    panel: pd.DataFrame, registry: pd.DataFrame, target: str, mode: str, task_prefix: str = "task__"
) -> tuple[pd.DataFrame, pd.Series, dict[str, Any]]:
    masked, retained, target_column = mask_columns_for_target(list(panel.columns), registry, target, mode, task_prefix)
    target_values = panel[target_column].copy()
    non_task_columns = [column for column in panel.columns if not str(column).startswith(task_prefix)]
    predictor_frame = panel[non_task_columns + retained].copy()
    summary = {
        "target": normalize_name(target),
        "mode": mode,
        "target_column": target_column,
        "n_candidate_task_predictors": len(masked) + len(retained),
        "n_masked_task_predictors": len(masked),
        "n_retained_task_predictors": len(retained),
        "direct_target_removed_before_preprocessing": target_column not in predictor_frame.columns,
        "execution_order": "RAW_TARGET_AND_FAMILY_REMOVAL_THEN_FUTURE_IMPUTATION_SCALING",
    }
    return predictor_frame, target_values, summary


def main() -> int:
    args = parse_args()
    missing = [str(path) for path in (args.panel_csv, args.registry_csv) if not path.exists()]
    if missing:
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "missing": missing}, indent=2))
        return 2
    panel = load_table(args.panel_csv)
    registry = load_table(args.registry_csv)
    panel_targets = {
        normalize_name(column)
        for column in panel.columns
        if str(column).startswith(args.task_prefix)
    }
    registry_targets = set(registry["target"].dropna().map(normalize_name).unique().tolist())
    targets = resolve_targets(panel_targets, registry_targets, args.target)
    if not targets:
        print(json.dumps({"status": "BLOCKED_NO_COMMON_TARGETS"}, indent=2))
        return 2
    modes = ("strict", "pragmatic") if args.mode == "both" else (args.mode,)
    restricted_dir = None
    if args.write_restricted_panels:
        if args.restricted_output_dir is None:
            raise ValueError("--write-restricted-panels requires --restricted-output-dir")
        restricted_dir = require_restricted_path(args.restricted_output_dir)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for target in targets:
        for mode in modes:
            try:
                predictors, target_values, summary = apply_mask_before_preprocessing(
                    panel, registry, target, mode, args.task_prefix
                )
                rows.append(summary)
                if restricted_dir is not None:
                    restricted_panel = predictors.copy()
                    restricted_panel["target_value"] = target_values
                    restricted_panel.to_csv(restricted_dir / f"masked_{normalize_name(target)}_{mode}_restricted.csv", index=False)
            except Exception as exc:
                failures.append({"target": normalize_name(target), "mode": mode, "error": f"{type(exc).__name__}: {exc}"})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(pd.DataFrame(rows), args.output_dir / "masking_plan_summary.csv")
    write_aggregate_csv(pd.DataFrame(failures, columns=["target", "mode", "error"]), args.output_dir / "masking_plan_failures.csv")
    execution = {
        "audit": "masked_report_completion_panel",
        "order": [
            "identify target from the raw observed panel",
            "remove exact target, aliases, and disallowed dependency-family predictors",
            "freeze remaining predictor names and availability masks",
            "future train-only imputation",
            "future train-only scaling",
            "future validation-only selection",
        ],
        "n_plans": len(rows),
        "n_failures": len(failures),
        "restricted_panels_written": restricted_dir is not None,
        "model_fitting_performed": False,
        "test_performance_computed": False,
    }
    write_json(execution, args.output_dir / "masking_execution_order.json")
    print(json.dumps(execution, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
