#!/usr/bin/env python3
"""Compute held-out observed-vs-predicted correlations from saved predictions.

This script reads patient-level prediction CSVs from restricted storage and
writes an aggregate manuscript-safe CSV. It does not fit or refit models.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_OUTPUT_COLUMNS = [
    "target",
    "model_name",
    "n_test",
    "pearson_r",
    "pearson_r_ci_low",
    "pearson_r_ci_high",
    "spearman_rho",
    "spearman_rho_ci_low",
    "spearman_rho_ci_high",
    "bootstrap_n",
    "bootstrap_seed",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predictions",
        type=Path,
        nargs="+",
        required=True,
        help="One or more saved prediction CSVs. Wide and long formats are supported.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("results/test_correlation_metrics.csv"),
        help="Aggregate output CSV path.",
    )
    parser.add_argument("--bootstrap-n", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--split", default="test")
    return parser.parse_args()


def pearson_r(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 3 or np.isclose(np.std(y_true), 0.0) or np.isclose(np.std(y_pred), 0.0):
        return np.nan
    return float(np.corrcoef(y_true, y_pred)[0, 1])


def spearman_rho(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 3 or np.isclose(np.std(y_true), 0.0) or np.isclose(np.std(y_pred), 0.0):
        return np.nan
    true_rank = pd.Series(y_true).rank(method="average").to_numpy(dtype=float)
    pred_rank = pd.Series(y_pred).rank(method="average").to_numpy(dtype=float)
    return pearson_r(true_rank, pred_rank)


def percentile_ci(values: list[float]) -> tuple[float, float]:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    if clean.size == 0:
        return np.nan, np.nan
    return float(np.percentile(clean, 2.5)), float(np.percentile(clean, 97.5))


def stable_seed(seed: int, *parts: str) -> int:
    payload = "::".join([str(seed), *parts]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:8]
    return int(digest, 16) % (2**31 - 1)


def bootstrap_corr_ci(
    frame: pd.DataFrame,
    statistic: str,
    bootstrap_n: int,
    seed: int,
) -> tuple[float, float]:
    if bootstrap_n <= 0:
        return np.nan, np.nan
    unit_col = "study_id" if "study_id" in frame.columns else None
    if unit_col is None or frame[unit_col].isna().any():
        units = np.arange(len(frame))
        frame = frame.copy()
        frame["_row_unit"] = units
        unit_col = "_row_unit"
    else:
        units = frame[unit_col].drop_duplicates().to_numpy()
    if len(units) < 5:
        return np.nan, np.nan

    y_true_all = frame["y_true"].to_numpy(dtype=float)
    y_pred_all = frame["y_pred"].to_numpy(dtype=float)
    grouped_indices = {
        unit: np.asarray(indices, dtype=int)
        for unit, indices in frame.groupby(unit_col, sort=False).indices.items()
    }
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(bootstrap_n):
        sampled_units = rng.choice(units, size=len(units), replace=True)
        sample_idx = np.concatenate([grouped_indices[unit] for unit in sampled_units])
        y_true = y_true_all[sample_idx]
        y_pred = y_pred_all[sample_idx]
        value = pearson_r(y_true, y_pred) if statistic == "pearson" else spearman_rho(y_true, y_pred)
        if np.isfinite(value):
            values.append(float(value))
    return percentile_ci(values)


def normalize_prediction_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()

    if {"target", "model_name", "split", "y_true", "y_pred"}.issubset(df.columns):
        out = df.copy()
    elif {"target", "split", "target_value"}.issubset(df.columns):
        id_cols = [c for c in ["target", "analysis_label", "subject_id", "study_id", "split"] if c in df.columns]
        pred_cols = [c for c in df.columns if c.startswith("pred_")]
        if not pred_cols:
            raise ValueError(f"{path} has no pred_* columns and is not in long prediction format.")
        out = df.melt(
            id_vars=id_cols + ["target_value"],
            value_vars=pred_cols,
            var_name="model_name",
            value_name="y_pred",
        )
        out["model_name"] = out["model_name"].str.replace(r"^pred_", "", regex=True)
        out = out.rename(columns={"target_value": "y_true"})
    else:
        raise ValueError(
            f"{path} must either include target/model_name/split/y_true/y_pred or "
            "target/split/target_value plus pred_* columns."
        )

    for col in ["study_id", "subject_id"]:
        if col not in out.columns:
            out[col] = pd.NA
    out["source_prediction_file"] = str(path)
    out["y_true"] = pd.to_numeric(out["y_true"], errors="coerce")
    out["y_pred"] = pd.to_numeric(out["y_pred"], errors="coerce")
    out = out[out["y_true"].notna() & out["y_pred"].notna()].copy()
    return out


def compute_rows(
    predictions: pd.DataFrame,
    split: str,
    bootstrap_n: int,
    bootstrap_seed: int,
) -> list[dict[str, Any]]:
    test = predictions[predictions["split"].astype(str).str.lower() == split.lower()].copy()
    rows: list[dict[str, Any]] = []
    for (target, model_name), group in test.groupby(["target", "model_name"], sort=True):
        group = group.reset_index(drop=True)
        y_true = group["y_true"].to_numpy(dtype=float)
        y_pred = group["y_pred"].to_numpy(dtype=float)
        pearson = pearson_r(y_true, y_pred)
        spearman = spearman_rho(y_true, y_pred)
        model_seed = stable_seed(bootstrap_seed, str(target), str(model_name), "test_correlation")
        pearson_low, pearson_high = bootstrap_corr_ci(
            group, "pearson", bootstrap_n, model_seed
        )
        spearman_low, spearman_high = bootstrap_corr_ci(
            group, "spearman", bootstrap_n, stable_seed(model_seed, "spearman")
        )
        rows.append(
            {
                "target": target,
                "model_name": model_name,
                "n_test": int(len(group)),
                "pearson_r": pearson,
                "pearson_r_ci_low": pearson_low,
                "pearson_r_ci_high": pearson_high,
                "spearman_rho": spearman,
                "spearman_rho_ci_low": spearman_low,
                "spearman_rho_ci_high": spearman_high,
                "bootstrap_n": int(bootstrap_n),
                "bootstrap_seed": int(bootstrap_seed),
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    if args.bootstrap_n < 1000:
        raise ValueError("--bootstrap-n should be at least 1000 for manuscript use.")
    frames = [normalize_prediction_frame(path) for path in args.predictions]
    predictions = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if predictions.empty:
        raise RuntimeError("No prediction rows available after loading inputs.")

    rows = compute_rows(predictions, args.split, args.bootstrap_n, args.bootstrap_seed)
    if not rows:
        raise RuntimeError(f"No rows found for split={args.split!r}.")
    out = pd.DataFrame(rows, columns=REQUIRED_OUTPUT_COLUMNS)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output_csv, index=False)
    print(out.to_string(index=False))
    print(f"[written] {args.output_csv.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
