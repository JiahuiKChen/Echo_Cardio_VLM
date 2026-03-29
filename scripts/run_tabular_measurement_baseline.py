#!/usr/bin/env python3
"""Phase 4c / E3: Tabular-only baseline using structured measurements (excl. LVEF).

Pivots the long-format structured_measurements.csv into a wide feature matrix,
excludes LVEF and LVEF-correlated measurements, and trains Ridge/LogReg
baselines to predict LVEF from tabular features alone.

This establishes the "tabular ceiling" — how well can non-imaging data predict
cardiac function? The result frames the multimodal fusion experiment (E5).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


LVEF_LEAKAGE_PATTERNS = [
    r"^lvef$",
    r"^ef$",
    r"^ejection.?fraction",
    r"^lv.?ef",
    r"^lv_ef",
    r"^lvef_",
    r"^biplane.*ef",
    r"^simpson.*ef",
    r"^mod.*a[24]c.*ef",
    r"^teich.*ef",
    r"^lv_systolic_function",
    r"^lv.*function.*grade",
    r"^visual.*ef",
    r"^ef_",
    r"^fractional.?shortening",
    r"^lv.?fs",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tabular-only baseline: structured measurements → LVEF prediction."
    )
    parser.add_argument(
        "--measurements-csv", type=Path, required=True,
        help="Path to structured_measurements.csv (long format).",
    )
    parser.add_argument(
        "--label-manifest", type=Path, required=True,
        help="Path to lvef_still_manifest.csv (has study_id, subject_id, split, lvef, lvef_binary_reduced).",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Directory for predictions, metrics, and leakage audit.",
    )
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument(
        "--min-coverage", type=float, default=0.05,
        help="Drop measurements present in fewer than this fraction of studies (default 5%%).",
    )
    return parser.parse_args()


def is_lvef_leakage(name: str) -> bool:
    """Check if a measurement name is LVEF or LVEF-derived."""
    lower = name.lower().strip()
    return any(re.search(pat, lower) for pat in LVEF_LEAKAGE_PATTERNS)


def safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, y_prob))


def safe_ap(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(average_precision_score(y_true, y_prob))


def evaluate_split(
    df: pd.DataFrame,
    y_reg_pred: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    split_name: str,
) -> dict[str, Any]:
    y_reg_true = df["lvef"].to_numpy(dtype=np.float32)
    y_bin_true = df["lvef_binary_reduced"].to_numpy(dtype=np.int32)
    y_bin_pred = (y_prob >= threshold).astype(np.int32)

    return {
        "split": split_name,
        "n_rows": int(len(df)),
        "n_subjects": int(df["subject_id"].nunique()),
        "n_studies": int(df["study_id"].nunique()),
        "reg_mae": float(mean_absolute_error(y_reg_true, y_reg_pred)),
        "reg_rmse": float(np.sqrt(mean_squared_error(y_reg_true, y_reg_pred))),
        "reg_r2": float(r2_score(y_reg_true, y_reg_pred)),
        "clf_auc": safe_auc(y_bin_true, y_prob),
        "clf_ap": safe_ap(y_bin_true, y_prob),
        "clf_f1_at_threshold": float(f1_score(y_bin_true, y_bin_pred, zero_division=0)),
        "clf_prevalence": float(np.mean(y_bin_true)),
        "clf_pred_positive_rate": float(np.mean(y_bin_pred)),
        "threshold": float(threshold),
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load data ---
    meas_df = pd.read_csv(args.measurements_csv)
    labels_df = pd.read_csv(args.label_manifest)

    label_agg = (
        labels_df.groupby("study_id", as_index=False)
        .agg(
            subject_id=("subject_id", "first"),
            split=("split", "first"),
            lvef=("lvef", "median"),
            lvef_binary_reduced=("lvef_binary_reduced", "max"),
        )
    )

    # --- Pivot measurements to wide format ---
    meas_df["result"] = pd.to_numeric(meas_df["result"], errors="coerce")
    meas_numeric = meas_df.dropna(subset=["result", "measurement"]).copy()

    study_meas = (
        meas_numeric.groupby(["study_id", "measurement"], as_index=False)["result"]
        .median()
    )
    wide = study_meas.pivot(index="study_id", columns="measurement", values="result")
    wide = wide.reset_index()

    all_measurements = sorted([c for c in wide.columns if c != "study_id"])
    n_studies_total = len(wide)

    # --- Leakage audit ---
    leakage_excluded = [m for m in all_measurements if is_lvef_leakage(m)]
    safe_measurements = [m for m in all_measurements if not is_lvef_leakage(m)]

    # --- Coverage filter ---
    coverage = wide[safe_measurements].notna().mean()
    low_coverage = coverage[coverage < args.min_coverage].index.tolist()
    feature_cols = [m for m in safe_measurements if m not in low_coverage]

    audit = {
        "total_unique_measurements": len(all_measurements),
        "excluded_as_lvef_leakage": leakage_excluded,
        "n_excluded_leakage": len(leakage_excluded),
        "excluded_low_coverage": low_coverage,
        "n_excluded_low_coverage": len(low_coverage),
        "retained_features": feature_cols,
        "n_retained_features": len(feature_cols),
        "min_coverage_threshold": args.min_coverage,
        "coverage_stats": {
            m: round(float(coverage[m]), 4) for m in feature_cols
        },
    }
    audit_path = args.output_dir / "measurement_leakage_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2))
    print(f"[info] Leakage audit: {len(leakage_excluded)} excluded, "
          f"{len(low_coverage)} low-coverage, {len(feature_cols)} retained features")
    print(f"[written] {audit_path.resolve()}")

    if not feature_cols:
        raise RuntimeError("No features remain after leakage exclusion and coverage filter.")

    # --- Join with labels ---
    model_df = wide[["study_id"] + feature_cols].merge(
        label_agg, how="inner", on="study_id",
    )
    if model_df.empty:
        raise RuntimeError("No rows after joining measurements with labels.")

    print(f"[info] {len(model_df)} studies with labels and measurements")

    # --- Split ---
    train_mask = model_df["split"] == "train"
    val_mask = model_df["split"] == "val"
    test_mask = model_df["split"] == "test"
    if train_mask.sum() == 0 or val_mask.sum() == 0 or test_mask.sum() == 0:
        raise RuntimeError("Expected non-empty train/val/test after merge.")

    X = model_df[feature_cols].to_numpy(dtype=np.float32)

    y_reg = model_df["lvef"].to_numpy(dtype=np.float32)
    y_bin = model_df["lvef_binary_reduced"].to_numpy(dtype=np.int32)

    # --- Train models (with imputation for missing values) ---
    reg_model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("ridge", Ridge(alpha=args.ridge_alpha, random_state=args.seed)),
    ])
    clf_model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("logreg", LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=args.seed,
        )),
    ])

    reg_model.fit(X[train_mask], y_reg[train_mask])
    clf_model.fit(X[train_mask], y_bin[train_mask])

    model_df = model_df.copy()
    model_df["pred_lvef"] = reg_model.predict(X).astype(np.float32)
    model_df["pred_reduced_prob"] = clf_model.predict_proba(X)[:, 1].astype(np.float32)

    # --- Evaluate ---
    split_metrics = {}
    for split_name in ["train", "val", "test"]:
        mask = model_df["split"] == split_name
        split_metrics[split_name] = evaluate_split(
            df=model_df[mask].reset_index(drop=True),
            y_reg_pred=model_df.loc[mask, "pred_lvef"].to_numpy(dtype=np.float32),
            y_prob=model_df.loc[mask, "pred_reduced_prob"].to_numpy(dtype=np.float32),
            threshold=args.threshold,
            split_name=split_name,
        )

    # --- Feature importance (Ridge coefficients) ---
    ridge_coefs = reg_model.named_steps["ridge"].coef_
    importance = sorted(
        zip(feature_cols, ridge_coefs.tolist()),
        key=lambda x: abs(x[1]),
        reverse=True,
    )

    # --- Save outputs ---
    pred_csv = args.output_dir / "tabular_predictions.csv"
    model_df.to_csv(pred_csv, index=False)

    metrics = {
        "experiment": "E3_tabular_only",
        "description": "Structured measurements (excl. LVEF) → LVEF prediction",
        "measurements_csv": str(args.measurements_csv.resolve()),
        "label_manifest": str(args.label_manifest.resolve()),
        "n_joined_studies": int(len(model_df)),
        "n_features": len(feature_cols),
        "n_excluded_leakage": len(leakage_excluded),
        "excluded_leakage_names": leakage_excluded,
        "ridge_alpha": float(args.ridge_alpha),
        "threshold": float(args.threshold),
        "missing_rate_mean": round(float(np.isnan(X).mean()), 4),
        "split_metrics": split_metrics,
        "feature_importance_top20": [
            {"feature": name, "ridge_coef": round(coef, 6)}
            for name, coef in importance[:20]
        ],
        "outputs": {
            "predictions_csv": str(pred_csv.resolve()),
            "leakage_audit_json": str(audit_path.resolve()),
            "metrics_json": str((args.output_dir / "tabular_baseline_metrics.json").resolve()),
        },
    }
    metrics_path = args.output_dir / "tabular_baseline_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))
    print(f"[written] {pred_csv.resolve()}")
    print(f"[written] {metrics_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
