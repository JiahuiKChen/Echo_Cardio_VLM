#!/usr/bin/env python3
"""Run multitask tabular-only baselines for structured measurement targets.

For each target task__* column in the panel:
- target = current task column
- features = all other task__* columns (leave-one-task-out to avoid identity leakage)
- model = median-impute + standardize + Ridge

Metrics are reported per task and as macro summaries over test splits.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-csv", type=Path, required=True, help="multitask_panel_wide.csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--min-train-n", type=int, default=120)
    parser.add_argument("--min-val-n", type=int, default=40)
    parser.add_argument("--min-test-n", type=int, default=40)
    parser.add_argument("--min-total-n", type=int, default=250)
    parser.add_argument("--seed", type=int, default=1337)
    return parser.parse_args()


def safe_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | None]:
    if len(y_true) == 0:
        return {"mae": None, "rmse": None, "r2": None}
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    if len(y_true) < 2 or np.allclose(np.std(y_true), 0.0):
        r2 = None
    else:
        r2 = float(r2_score(y_true, y_pred))
    return {"mae": mae, "rmse": rmse, "r2": r2}


def iqr(values: np.ndarray) -> float:
    if len(values) == 0:
        return np.nan
    return float(np.percentile(values, 75) - np.percentile(values, 25))


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    panel = pd.read_csv(args.panel_csv).copy()
    required_cols = {"study_id", "split"}
    missing_cols = required_cols - set(panel.columns)
    if missing_cols:
        raise ValueError(f"panel-csv missing required columns: {sorted(missing_cols)}")

    task_cols = [c for c in panel.columns if c.startswith(args.task_prefix)]
    if len(task_cols) < 2:
        raise RuntimeError("Need at least 2 task columns for leave-one-task-out tabular baseline.")

    split = panel["split"].astype(str).to_numpy()
    split_values = set(np.unique(split))
    if not split_values.issubset({"train", "val", "test"}):
        raise RuntimeError("Split column must contain only train/val/test labels.")

    metrics_rows: list[dict[str, Any]] = []
    pred_rows: list[pd.DataFrame] = []

    for task in task_cols:
        y = pd.to_numeric(panel[task], errors="coerce").to_numpy(dtype=np.float32)
        feature_cols = [c for c in task_cols if c != task]
        x = panel[feature_cols].to_numpy(dtype=np.float32)

        train_mask = (split == "train") & (~np.isnan(y))
        val_mask = (split == "val") & (~np.isnan(y))
        test_mask = (split == "test") & (~np.isnan(y))
        total_obs = int((~np.isnan(y)).sum())

        reason = ""
        if train_mask.sum() < args.min_train_n:
            reason = "insufficient_train"
        elif val_mask.sum() < args.min_val_n:
            reason = "insufficient_val"
        elif test_mask.sum() < args.min_test_n:
            reason = "insufficient_test"
        elif total_obs < args.min_total_n:
            reason = "insufficient_total"

        row_base = {
            "task_col": task,
            "n_features": int(len(feature_cols)),
            "n_total_with_value": total_obs,
            "n_train_with_value": int(train_mask.sum()),
            "n_val_with_value": int(val_mask.sum()),
            "n_test_with_value": int(test_mask.sum()),
            "status": "skipped" if reason else "ok",
            "skip_reason": reason,
        }

        if reason:
            metrics_rows.append(
                {
                    **row_base,
                    "train_mae": np.nan,
                    "train_rmse": np.nan,
                    "train_r2": np.nan,
                    "val_mae": np.nan,
                    "val_rmse": np.nan,
                    "val_r2": np.nan,
                    "test_mae": np.nan,
                    "test_rmse": np.nan,
                    "test_r2": np.nan,
                    "test_mae_norm_iqr": np.nan,
                }
            )
            continue

        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("ridge", Ridge(alpha=args.ridge_alpha, random_state=args.seed)),
            ]
        )
        model.fit(x[train_mask], y[train_mask])

        pred = np.full(shape=y.shape, fill_value=np.nan, dtype=np.float32)
        pred[train_mask] = model.predict(x[train_mask]).astype(np.float32)
        pred[val_mask] = model.predict(x[val_mask]).astype(np.float32)
        pred[test_mask] = model.predict(x[test_mask]).astype(np.float32)

        train_m = safe_metrics(y[train_mask], pred[train_mask])
        val_m = safe_metrics(y[val_mask], pred[val_mask])
        test_m = safe_metrics(y[test_mask], pred[test_mask])

        train_iqr = iqr(y[train_mask])
        test_mae_norm = (
            float(test_m["mae"] / train_iqr)
            if test_m["mae"] is not None and np.isfinite(train_iqr) and train_iqr > 0
            else np.nan
        )

        metrics_rows.append(
            {
                **row_base,
                "train_mae": train_m["mae"],
                "train_rmse": train_m["rmse"],
                "train_r2": train_m["r2"],
                "val_mae": val_m["mae"],
                "val_rmse": val_m["rmse"],
                "val_r2": val_m["r2"],
                "test_mae": test_m["mae"],
                "test_rmse": test_m["rmse"],
                "test_r2": test_m["r2"],
                "test_mae_norm_iqr": test_mae_norm,
            }
        )

        pred_task_cols = ["study_id", "split"]
        if "subject_id" in panel.columns:
            pred_task_cols = ["subject_id"] + pred_task_cols
        pred_task = panel[pred_task_cols].copy()
        pred_task["task_col"] = task
        pred_task["y_true"] = y
        pred_task["y_pred"] = pred
        pred_task = pred_task.dropna(subset=["y_true", "y_pred"])
        pred_rows.append(pred_task)

    metrics_df = pd.DataFrame(metrics_rows)
    metrics_df = metrics_df.sort_values(
        ["status", "test_r2", "n_test_with_value"],
        ascending=[True, False, False],
    ).reset_index(drop=True)

    ok_df = metrics_df[metrics_df["status"] == "ok"].copy()
    skipped_df = metrics_df[metrics_df["status"] != "ok"].copy()

    summary = {
        "panel_csv": str(args.panel_csv.resolve()),
        "n_panel_studies": int(panel["study_id"].nunique()),
        "n_panel_subjects": int(panel["subject_id"].nunique()) if "subject_id" in panel.columns else None,
        "n_tasks_input": int(len(task_cols)),
        "n_tasks_scored": int(len(ok_df)),
        "n_tasks_skipped": int(len(skipped_df)),
        "ridge_alpha": float(args.ridge_alpha),
        "thresholds": {
            "min_train_n": int(args.min_train_n),
            "min_val_n": int(args.min_val_n),
            "min_test_n": int(args.min_test_n),
            "min_total_n": int(args.min_total_n),
        },
        "macro_metrics_test": {
            "mean_r2": float(ok_df["test_r2"].dropna().mean()) if not ok_df.empty else None,
            "median_r2": float(ok_df["test_r2"].dropna().median()) if not ok_df.empty else None,
            "mean_mae": float(ok_df["test_mae"].dropna().mean()) if not ok_df.empty else None,
            "median_mae": float(ok_df["test_mae"].dropna().median()) if not ok_df.empty else None,
            "mean_mae_norm_iqr": (
                float(ok_df["test_mae_norm_iqr"].dropna().mean()) if not ok_df.empty else None
            ),
        },
        "top_tasks_by_test_r2": ok_df[["task_col", "test_r2", "test_mae", "test_mae_norm_iqr", "n_test_with_value"]]
        .head(15)
        .to_dict(orient="records"),
        "skipped_tasks": skipped_df[
            ["task_col", "skip_reason", "n_train_with_value", "n_val_with_value", "n_test_with_value"]
        ].to_dict(orient="records"),
    }

    metrics_csv = args.output_dir / "multitask_tabular_task_metrics.csv"
    predictions_csv = args.output_dir / "multitask_tabular_predictions_long.csv"
    summary_json = args.output_dir / "multitask_tabular.summary.json"

    metrics_df.to_csv(metrics_csv, index=False)
    if pred_rows:
        pred_df = pd.concat(pred_rows, ignore_index=True)
    else:
        pred_df = pd.DataFrame(columns=["study_id", "split", "task_col", "y_true", "y_pred"])
    pred_df.to_csv(predictions_csv, index=False)
    summary_json.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {metrics_csv.resolve()}")
    print(f"[written] {predictions_csv.resolve()}")
    print(f"[written] {summary_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

