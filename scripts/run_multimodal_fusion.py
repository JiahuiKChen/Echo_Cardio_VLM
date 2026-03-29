#!/usr/bin/env python3
"""Phase 5 / E5: Multimodal fusion — vision embeddings + structured measurements.

Runs a controlled comparison of three input configurations using identical
model architectures (Ridge/LogReg and optionally MLP), ensuring the fusion
result is directly comparable to E2b (vision-only) and E3 (tabular-only).

Also computes bootstrap confidence intervals on test AUC to assess whether
differences between modalities are statistically meaningful.
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
from sklearn.neural_network import MLPClassifier, MLPRegressor
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
        description="Multimodal fusion: vision + measurements → LVEF prediction."
    )
    parser.add_argument(
        "--study-embedding-npz", type=Path, required=True,
        help="Study-level vision embedding NPZ (from aggregate_study_embeddings.py).",
    )
    parser.add_argument(
        "--study-embedding-manifest", type=Path, required=True,
        help="Study-level embedding manifest CSV.",
    )
    parser.add_argument(
        "--measurements-csv", type=Path, required=True,
        help="Structured measurements CSV (long format).",
    )
    parser.add_argument(
        "--label-manifest", type=Path, required=True,
        help="LVEF still manifest CSV (study_id, subject_id, split, lvef, lvef_binary_reduced).",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--min-coverage", type=float, default=0.05)
    parser.add_argument("--n-bootstrap", type=int, default=1000)
    parser.add_argument(
        "--run-mlp", action="store_true",
        help="Also train MLP models for nonlinear fusion comparison.",
    )
    return parser.parse_args()


def is_lvef_leakage(name: str) -> bool:
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


def bootstrap_auc(
    y_true: np.ndarray, y_prob: np.ndarray, n_bootstrap: int, seed: int,
) -> dict[str, float | None]:
    if len(np.unique(y_true)) < 2:
        return {"auc_mean": None, "auc_ci_lo": None, "auc_ci_hi": None}
    rng = np.random.default_rng(seed)
    n = len(y_true)
    aucs = []
    for _ in range(n_bootstrap):
        idx = rng.choice(n, size=n, replace=True)
        if len(np.unique(y_true[idx])) < 2:
            continue
        aucs.append(float(roc_auc_score(y_true[idx], y_prob[idx])))
    if not aucs:
        return {"auc_mean": None, "auc_ci_lo": None, "auc_ci_hi": None}
    return {
        "auc_mean": round(float(np.mean(aucs)), 4),
        "auc_ci_lo": round(float(np.percentile(aucs, 2.5)), 4),
        "auc_ci_hi": round(float(np.percentile(aucs, 97.5)), 4),
    }


def evaluate_split(
    y_true_reg: np.ndarray,
    y_pred_reg: np.ndarray,
    y_true_bin: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    y_bin_pred = (y_prob >= threshold).astype(np.int32)
    result: dict[str, Any] = {
        "reg_mae": float(mean_absolute_error(y_true_reg, y_pred_reg)),
        "reg_rmse": float(np.sqrt(mean_squared_error(y_true_reg, y_pred_reg))),
        "reg_r2": float(r2_score(y_true_reg, y_pred_reg)),
        "clf_auc": safe_auc(y_true_bin, y_prob),
        "clf_ap": safe_ap(y_true_bin, y_prob),
        "clf_f1_at_threshold": float(f1_score(y_true_bin, y_bin_pred, zero_division=0)),
        "clf_prevalence": float(np.mean(y_true_bin)),
        "threshold": float(threshold),
    }
    result["bootstrap"] = bootstrap_auc(y_true_bin, y_prob, n_bootstrap, seed)
    return result


def build_tabular_features(
    meas_df: pd.DataFrame, study_ids: np.ndarray, min_coverage: float,
) -> tuple[np.ndarray, list[str], list[str], list[str]]:
    """Build wide tabular feature matrix aligned to study_ids ordering."""
    meas_df = meas_df.copy()
    meas_df["result"] = pd.to_numeric(meas_df["result"], errors="coerce")
    meas_numeric = meas_df.dropna(subset=["result", "measurement"])

    study_meas = (
        meas_numeric.groupby(["study_id", "measurement"], as_index=False)["result"]
        .median()
    )
    wide = study_meas.pivot(index="study_id", columns="measurement", values="result")

    all_meas = sorted([c for c in wide.columns])
    leakage_excluded = [m for m in all_meas if is_lvef_leakage(m)]
    safe_meas = [m for m in all_meas if not is_lvef_leakage(m)]

    coverage = wide[safe_meas].notna().mean()
    low_coverage = coverage[coverage < min_coverage].index.tolist()
    feature_cols = [m for m in safe_meas if m not in low_coverage]

    aligned = pd.DataFrame({"study_id": study_ids}).merge(
        wide[["study_id" if "study_id" in wide.columns else wide.index.name] + feature_cols].reset_index()
        if wide.index.name == "study_id"
        else wide[feature_cols].reset_index(),
        how="left", on="study_id",
    )
    tab_matrix = aligned[feature_cols].to_numpy(dtype=np.float32)

    return tab_matrix, feature_cols, leakage_excluded, low_coverage


def train_and_evaluate(
    X_train: np.ndarray,
    y_reg_train: np.ndarray,
    y_bin_train: np.ndarray,
    X_val: np.ndarray,
    y_reg_val: np.ndarray,
    y_bin_val: np.ndarray,
    X_test: np.ndarray,
    y_reg_test: np.ndarray,
    y_bin_test: np.ndarray,
    config_name: str,
    model_type: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Train Ridge+LogReg (or MLP) and evaluate on all splits."""
    if model_type == "linear":
        reg = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=args.ridge_alpha, random_state=args.seed)),
        ])
        clf = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("logreg", LogisticRegression(
                max_iter=1000, class_weight="balanced", random_state=args.seed,
            )),
        ])
    else:
        reg = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("mlp", MLPRegressor(
                hidden_layer_sizes=(128, 64),
                max_iter=500,
                early_stopping=True,
                validation_fraction=0.15,
                random_state=args.seed,
            )),
        ])
        clf = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("mlp", MLPClassifier(
                hidden_layer_sizes=(128, 64),
                max_iter=500,
                early_stopping=True,
                validation_fraction=0.15,
                random_state=args.seed,
            )),
        ])

    reg.fit(X_train, y_reg_train)
    clf.fit(X_train, y_bin_train)

    result: dict[str, Any] = {"config": config_name, "model_type": model_type}
    for split_name, X_s, y_r, y_b in [
        ("train", X_train, y_reg_train, y_bin_train),
        ("val", X_val, y_reg_val, y_bin_val),
        ("test", X_test, y_reg_test, y_bin_test),
    ]:
        pred_reg = reg.predict(X_s).astype(np.float32)
        pred_prob = clf.predict_proba(X_s)[:, 1].astype(np.float32)
        result[split_name] = evaluate_split(
            y_true_reg=y_r, y_pred_reg=pred_reg,
            y_true_bin=y_b, y_prob=pred_prob,
            threshold=args.threshold,
            n_bootstrap=args.n_bootstrap,
            seed=args.seed,
        )
        result[split_name]["n_rows"] = int(len(X_s))
    return result


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load vision embeddings ---
    with np.load(args.study_embedding_npz) as data:
        vision_embs = data["embeddings"]  # (N_studies, 512)
    vision_manifest = pd.read_csv(args.study_embedding_manifest)
    vision_manifest = vision_manifest.copy()

    # --- Load labels ---
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

    # --- Join vision embeddings with labels ---
    drop_overlap = [c for c in ["subject_id", "split", "lvef", "lvef_binary_reduced"]
                    if c in vision_manifest.columns]
    joined = vision_manifest.drop(columns=drop_overlap, errors="ignore").merge(
        label_agg, how="inner", on="study_id",
    )
    if joined.empty:
        raise RuntimeError("No rows after joining vision embeddings with labels.")

    idx_col = "study_idx" if "study_idx" in joined.columns else "embedding_idx"
    X_vision = vision_embs[joined[idx_col].to_numpy(dtype=int)]

    # --- Build tabular features aligned to the same study order ---
    meas_df = pd.read_csv(args.measurements_csv)
    study_ids = joined["study_id"].to_numpy()
    X_tab, feature_cols, leakage_excl, low_cov = build_tabular_features(
        meas_df, study_ids, args.min_coverage,
    )

    # --- Fusion: concatenate ---
    X_fusion = np.concatenate([X_vision, X_tab], axis=1)

    print(f"[info] Vision dim: {X_vision.shape[1]}, Tabular dim: {X_tab.shape[1]}, "
          f"Fusion dim: {X_fusion.shape[1]}")
    print(f"[info] {len(joined)} studies, {len(feature_cols)} tabular features, "
          f"{len(leakage_excl)} excluded (leakage), {len(low_cov)} excluded (low coverage)")

    # --- Split ---
    splits = joined["split"].to_numpy()
    y_reg = joined["lvef"].to_numpy(dtype=np.float32)
    y_bin = joined["lvef_binary_reduced"].to_numpy(dtype=np.int32)

    train_m = splits == "train"
    val_m = splits == "val"
    test_m = splits == "test"
    if train_m.sum() == 0 or val_m.sum() == 0 or test_m.sum() == 0:
        raise RuntimeError("Expected non-empty train/val/test splits.")

    # --- Run all configurations ---
    configs = [
        ("vision_only", X_vision),
        ("tabular_only", X_tab),
        ("fusion_concat", X_fusion),
    ]

    all_results: dict[str, Any] = {}
    model_types = ["linear"]
    if args.run_mlp:
        model_types.append("mlp")

    for model_type in model_types:
        for config_name, X in configs:
            full_name = f"{config_name}__{model_type}"
            print(f"[info] Training {full_name}...")
            result = train_and_evaluate(
                X_train=X[train_m], y_reg_train=y_reg[train_m], y_bin_train=y_bin[train_m],
                X_val=X[val_m], y_reg_val=y_reg[val_m], y_bin_val=y_bin[val_m],
                X_test=X[test_m], y_reg_test=y_reg[test_m], y_bin_test=y_bin[test_m],
                config_name=config_name,
                model_type=model_type,
                args=args,
            )
            all_results[full_name] = result

    # --- Summary comparison table ---
    comparison = []
    for name, res in all_results.items():
        test = res["test"]
        comparison.append({
            "config": name,
            "test_auc": test["clf_auc"],
            "test_auc_ci": f"[{test['bootstrap']['auc_ci_lo']}, {test['bootstrap']['auc_ci_hi']}]",
            "test_ap": test["clf_ap"],
            "test_mae": test["reg_mae"],
            "test_rmse": test["reg_rmse"],
            "test_r2": test["reg_r2"],
            "val_auc": res["val"]["clf_auc"],
        })

    print("\n=== Comparison Table ===")
    comp_df = pd.DataFrame(comparison)
    print(comp_df.to_string(index=False))

    # --- Save outputs ---
    metrics = {
        "experiment": "E5_multimodal_fusion",
        "n_studies": int(len(joined)),
        "n_vision_features": int(X_vision.shape[1]),
        "n_tabular_features": int(X_tab.shape[1]),
        "n_fusion_features": int(X_fusion.shape[1]),
        "leakage_excluded": leakage_excl,
        "feature_cols": feature_cols,
        "n_train": int(train_m.sum()),
        "n_val": int(val_m.sum()),
        "n_test": int(test_m.sum()),
        "ridge_alpha": args.ridge_alpha,
        "threshold": args.threshold,
        "n_bootstrap": args.n_bootstrap,
        "results": all_results,
        "comparison_table": comparison,
    }

    metrics_path = args.output_dir / "fusion_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2))

    comp_csv = args.output_dir / "fusion_comparison_table.csv"
    comp_df.to_csv(comp_csv, index=False)

    print(f"\n[written] {metrics_path.resolve()}")
    print(f"[written] {comp_csv.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
