#!/usr/bin/env python3
"""Run multitask vision-only baselines for structured measurement targets.

Given:
- study-level embeddings (NPZ + manifest with study_id and index column)
- multitask panel (one row per study with split and task__* columns)

This script trains one Ridge regressor per task and reports per-task and macro
metrics on train/val/test, using only vision embeddings as features.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-embedding-npz", type=Path, required=True)
    parser.add_argument("--study-embedding-manifest", type=Path, required=True)
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


def summarize_split(mask: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    y_obs = y[mask]
    y_obs = y_obs[~np.isnan(y_obs)]
    return {
        "n_with_value": int(len(y_obs)),
        "y_median": float(np.median(y_obs)) if len(y_obs) else None,
        "y_iqr": float(iqr(y_obs)) if len(y_obs) else None,
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with np.load(args.study_embedding_npz) as data:
        if "embeddings" not in data:
            raise ValueError(f"{args.study_embedding_npz} missing 'embeddings' array")
        emb = data["embeddings"].astype(np.float32)
    if emb.ndim != 2:
        raise ValueError(f"Expected 2D embeddings, got shape {emb.shape}")

    emb_manifest = pd.read_csv(args.study_embedding_manifest).copy()
    panel = pd.read_csv(args.panel_csv).copy()

    required_panel_cols = {"study_id", "split"}
    missing_panel = required_panel_cols - set(panel.columns)
    if missing_panel:
        raise ValueError(f"panel-csv missing required columns: {sorted(missing_panel)}")

    # Resolve embedding index column
    if "study_idx" in emb_manifest.columns:
        idx_col = "study_idx"
    elif "embedding_idx" in emb_manifest.columns:
        idx_col = "embedding_idx"
    else:
        emb_manifest["embedding_idx"] = np.arange(len(emb_manifest), dtype=int)
        idx_col = "embedding_idx"

    if "study_id" not in emb_manifest.columns:
        raise ValueError("study-embedding-manifest must include study_id")

    emb_manifest = emb_manifest.drop_duplicates(subset=["study_id"], keep="first").copy()
    emb_manifest[idx_col] = emb_manifest[idx_col].astype(int)

    joined = panel.merge(
        emb_manifest[["study_id", idx_col]],
        how="inner",
        on="study_id",
    )
    if joined.empty:
        raise RuntimeError("No rows after joining panel with study embedding manifest.")

    max_idx = int(joined[idx_col].max())
    if max_idx >= emb.shape[0]:
        raise RuntimeError(
            f"Embedding index out of range: max manifest idx {max_idx}, embedding rows {emb.shape[0]}"
        )

    task_cols = [c for c in joined.columns if c.startswith(args.task_prefix)]
    if not task_cols:
        raise RuntimeError(f"No task columns found with prefix '{args.task_prefix}'")

    split = joined["split"].astype(str).to_numpy()
    if not set(np.unique(split)).issubset({"train", "val", "test"}):
        raise RuntimeError("Split column must contain only train/val/test.")

    x = emb[joined[idx_col].to_numpy(dtype=int)]

    metrics_rows: list[dict[str, Any]] = []
    pred_rows: list[pd.DataFrame] = []

    for task in task_cols:
        y = pd.to_numeric(joined[task], errors="coerce").to_numpy(dtype=np.float32)

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

        pred_task = joined[["subject_id", "study_id", "split"]].copy() if "subject_id" in joined.columns else joined[["study_id", "split"]].copy()
        pred_task["task_col"] = task
        pred_task["y_true"] = y
        pred_task["y_pred"] = pred
        pred_task = pred_task.dropna(subset=["y_true", "y_pred"])
        pred_rows.append(pred_task)

    metrics_df = pd.DataFrame(metrics_rows)
    metrics_df = metrics_df.sort_values(["status", "test_r2", "n_test_with_value"], ascending=[True, False, False]).reset_index(drop=True)

    ok_df = metrics_df[metrics_df["status"] == "ok"].copy()
    skipped_df = metrics_df[metrics_df["status"] != "ok"].copy()

    summary = {
        "study_embedding_npz": str(args.study_embedding_npz.resolve()),
        "study_embedding_manifest": str(args.study_embedding_manifest.resolve()),
        "panel_csv": str(args.panel_csv.resolve()),
        "n_joined_studies": int(joined["study_id"].nunique()),
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
        "top_tasks_by_test_r2": ok_df[["task_col", "test_r2", "test_mae", "n_test_with_value"]]
        .head(15)
        .to_dict(orient="records"),
        "skipped_tasks": skipped_df[["task_col", "skip_reason", "n_train_with_value", "n_val_with_value", "n_test_with_value"]]
        .to_dict(orient="records"),
    }

    metrics_csv = args.output_dir / "multitask_vision_task_metrics.csv"
    summary_json = args.output_dir / "multitask_vision.summary.json"
    predictions_csv = args.output_dir / "multitask_vision_predictions_long.csv"

    metrics_df.to_csv(metrics_csv, index=False)
    summary_json.write_text(json.dumps(summary, indent=2))

    if pred_rows:
        pred_df = pd.concat(pred_rows, ignore_index=True)
    else:
        pred_df = pd.DataFrame(columns=["study_id", "split", "task_col", "y_true", "y_pred"])
    pred_df.to_csv(predictions_csv, index=False)

    print(json.dumps(summary, indent=2))
    print(f"[written] {metrics_csv.resolve()}")
    print(f"[written] {predictions_csv.resolve()}")
    print(f"[written] {summary_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

