#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a lightweight still-image baseline for LVEF regression and reduced-EF classification."
    )
    parser.add_argument("--manifest-csv", type=Path, required=True, help="Path to lvef_still_manifest.csv")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for predictions + metrics JSON")
    parser.add_argument("--image-size", type=int, default=112, help="Square resize size for grayscale inputs")
    parser.add_argument("--pca-components", type=int, default=64, help="Upper bound for PCA components")
    parser.add_argument("--ridge-alpha", type=float, default=1.0, help="Ridge alpha")
    parser.add_argument("--seed", type=int, default=1337, help="Random seed")
    return parser.parse_args()


def load_image_vector(path: Path, size: int) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return (img.astype(np.float32) / 255.0).reshape(-1)


def build_matrix(df: pd.DataFrame, image_size: int) -> np.ndarray:
    return np.stack([load_image_vector(Path(p), size=image_size) for p in df["keyframe_path"].astype(str)], axis=0)


def safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, y_prob))


def safe_ap(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(average_precision_score(y_true, y_prob))


def evaluate_split(
    split_name: str,
    split_df: pd.DataFrame,
    reg_pred: np.ndarray,
    clf_prob: np.ndarray,
    threshold: float = 0.5,
) -> dict[str, Any]:
    y_reg = split_df["lvef"].to_numpy(dtype=np.float32)
    y_bin = split_df["lvef_binary_reduced"].to_numpy(dtype=np.int32)
    pred_bin = (clf_prob >= threshold).astype(np.int32)

    metrics: dict[str, Any] = {
        "split": split_name,
        "n_rows": int(len(split_df)),
        "n_subjects": int(split_df["subject_id"].nunique()),
        "n_studies": int(split_df["study_id"].nunique()),
        "reg_mae": float(mean_absolute_error(y_reg, reg_pred)),
        "reg_rmse": float(np.sqrt(mean_squared_error(y_reg, reg_pred))),
        "reg_r2": float(r2_score(y_reg, reg_pred)),
        "clf_auc": safe_auc(y_bin, clf_prob),
        "clf_ap": safe_ap(y_bin, clf_prob),
        "clf_f1_at_0p5": float(f1_score(y_bin, pred_bin, zero_division=0)),
        "clf_prevalence": float(np.mean(y_bin)),
        "clf_pred_positive_rate_at_0p5": float(np.mean(pred_bin)),
    }
    return metrics


def choose_pca_components(n_train: int, n_features: int, max_components: int) -> int:
    upper = min(max_components, n_train - 1, n_features)
    return max(2, upper)


def main() -> int:
    args = parse_args()
    np.random.seed(args.seed)

    df = pd.read_csv(args.manifest_csv)
    needed = {"split", "keyframe_path", "lvef", "lvef_binary_reduced", "subject_id", "study_id"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Manifest missing required columns: {sorted(missing)}")

    train_df = df[df["split"] == "train"].copy().reset_index(drop=True)
    val_df = df[df["split"] == "val"].copy().reset_index(drop=True)
    test_df = df[df["split"] == "test"].copy().reset_index(drop=True)
    if train_df.empty or val_df.empty or test_df.empty:
        raise RuntimeError("Manifest must contain non-empty train/val/test splits.")

    x_train = build_matrix(train_df, image_size=args.image_size)
    x_val = build_matrix(val_df, image_size=args.image_size)
    x_test = build_matrix(test_df, image_size=args.image_size)

    y_train_reg = train_df["lvef"].to_numpy(dtype=np.float32)
    y_val_reg = val_df["lvef"].to_numpy(dtype=np.float32)
    y_test_reg = test_df["lvef"].to_numpy(dtype=np.float32)

    y_train_bin = train_df["lvef_binary_reduced"].to_numpy(dtype=np.int32)

    n_components = choose_pca_components(
        n_train=len(train_df),
        n_features=x_train.shape[1],
        max_components=args.pca_components,
    )

    reg_model = Pipeline(
        steps=[
            ("scaler", StandardScaler(with_mean=True, with_std=True)),
            ("pca", PCA(n_components=n_components, random_state=args.seed)),
            ("ridge", Ridge(alpha=args.ridge_alpha, random_state=args.seed)),
        ]
    )
    reg_model.fit(x_train, y_train_reg)
    reg_val = reg_model.predict(x_val)
    reg_test = reg_model.predict(x_test)

    clf_model = Pipeline(
        steps=[
            ("scaler", StandardScaler(with_mean=True, with_std=True)),
            ("pca", PCA(n_components=n_components, random_state=args.seed)),
            (
                "logreg",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    random_state=args.seed,
                ),
            ),
        ]
    )
    clf_model.fit(x_train, y_train_bin)
    clf_val = clf_model.predict_proba(x_val)[:, 1]
    clf_test = clf_model.predict_proba(x_test)[:, 1]

    val_metrics = evaluate_split("val", val_df, reg_pred=reg_val, clf_prob=clf_val)
    test_metrics = evaluate_split("test", test_df, reg_pred=reg_test, clf_prob=clf_test)
    train_metrics = evaluate_split(
        "train",
        train_df,
        reg_pred=reg_model.predict(x_train),
        clf_prob=clf_model.predict_proba(x_train)[:, 1],
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    pred_val = val_df.copy()
    pred_val["pred_lvef"] = reg_val
    pred_val["pred_reduced_prob"] = clf_val
    pred_val["split_eval"] = "val"

    pred_test = test_df.copy()
    pred_test["pred_lvef"] = reg_test
    pred_test["pred_reduced_prob"] = clf_test
    pred_test["split_eval"] = "test"

    pred_train = train_df.copy()
    pred_train["pred_lvef"] = reg_model.predict(x_train)
    pred_train["pred_reduced_prob"] = clf_model.predict_proba(x_train)[:, 1]
    pred_train["split_eval"] = "train"

    pred_df = pd.concat([pred_train, pred_val, pred_test], axis=0, ignore_index=True)
    pred_csv = args.output_dir / "lvef_still_baseline_predictions.csv"
    pred_df.to_csv(pred_csv, index=False)

    metrics = {
        "manifest_csv": str(args.manifest_csv.resolve()),
        "output_dir": str(args.output_dir.resolve()),
        "image_size": int(args.image_size),
        "pca_components_used": int(n_components),
        "ridge_alpha": float(args.ridge_alpha),
        "split_metrics": {
            "train": train_metrics,
            "val": val_metrics,
            "test": test_metrics,
        },
    }

    metrics_json = args.output_dir / "lvef_still_baseline_metrics.json"
    metrics_json.write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))
    print(f"[written] {pred_csv.resolve()}")
    print(f"[written] {metrics_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
