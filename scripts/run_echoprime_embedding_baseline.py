#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
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
        description="Train/evaluate lightweight LVEF baselines from EchoPrime clip embeddings."
    )
    parser.add_argument("--embedding-npz", type=Path, required=True, help="NPZ from extract_echoprime_embeddings.py")
    parser.add_argument("--embedding-manifest", type=Path, required=True, help="CSV from extract_echoprime_embeddings.py")
    parser.add_argument("--label-manifest", type=Path, required=True, help="lvef_still_manifest.csv")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for predictions + metrics.")
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--clip-threshold", type=float, default=0.5, help="Binary threshold for clip-level eval.")
    parser.add_argument(
        "--join-key",
        choices=["clip", "study_id"],
        default="clip",
        help="Join strategy: 'clip' uses (subject_id, study_id, dicom_filepath); "
        "'study_id' uses (study_id) for study-level aggregated embeddings.",
    )
    return parser.parse_args()


def safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, y_prob))


def safe_ap(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    return float(average_precision_score(y_true, y_prob))


def evaluate_frame(
    df: pd.DataFrame,
    y_reg_pred: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    level: str,
) -> dict[str, Any]:
    y_reg_true = df["lvef"].to_numpy(dtype=np.float32)
    y_bin_true = df["lvef_binary_reduced"].to_numpy(dtype=np.int32)
    y_bin_pred = (y_prob >= threshold).astype(np.int32)

    return {
        "level": level,
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

    emb_data = np.load(args.embedding_npz)
    if "embeddings" not in emb_data:
        raise ValueError(f"{args.embedding_npz} missing 'embeddings' array")
    emb = emb_data["embeddings"].astype(np.float32)
    if emb.ndim != 2:
        raise ValueError(f"Expected 2D embeddings, got shape {emb.shape}")

    emb_meta = pd.read_csv(args.embedding_manifest)

    if "write_ok" in emb_meta.columns:
        emb_meta_ok = emb_meta[emb_meta["write_ok"].fillna(False)].copy().reset_index(drop=True)
    else:
        emb_meta_ok = emb_meta.copy()

    idx_col = "embedding_idx" if "embedding_idx" not in emb_meta_ok.columns else "study_idx"
    if idx_col == "study_idx" and "study_idx" in emb_meta_ok.columns:
        pass  # study-level manifest already has study_idx
    else:
        emb_meta_ok["embedding_idx"] = np.arange(len(emb_meta_ok), dtype=int)
        idx_col = "embedding_idx"

    if len(emb_meta_ok) != emb.shape[0]:
        raise RuntimeError(
            f"Embedding matrix row count ({emb.shape[0]}) does not match manifest rows ({len(emb_meta_ok)})."
        )

    labels = pd.read_csv(args.label_manifest)

    if args.join_key == "study_id":
        key_cols = ["study_id"]
        label_agg = (
            labels.groupby("study_id", as_index=False)
            .agg(
                subject_id=("subject_id", "first"),
                split=("split", "first"),
                lvef=("lvef", "median"),
                lvef_binary_reduced=("lvef_binary_reduced", "max"),
            )
        )
        join_cols = key_cols + ["subject_id", "split", "lvef", "lvef_binary_reduced"]
        model_df = emb_meta_ok.merge(label_agg[join_cols], how="inner", on=key_cols)
    else:
        key_cols = ["subject_id", "study_id", "dicom_filepath"]
        for c in key_cols:
            if c not in labels.columns:
                raise ValueError(f"Label manifest missing required key column: {c}")
        join_cols = key_cols + ["split", "lvef", "lvef_binary_reduced"]
        model_df = emb_meta_ok.merge(labels[join_cols], how="inner", on=key_cols, validate="one_to_one")

    for c in ["split", "lvef", "lvef_binary_reduced"]:
        if c not in model_df.columns:
            raise ValueError(f"Missing required column after join: {c}")

    if model_df.empty:
        raise RuntimeError("No rows after joining embeddings with labels.")

    x = emb[model_df[idx_col].to_numpy(dtype=int)]
    y_reg = model_df["lvef"].to_numpy(dtype=np.float32)
    y_bin = model_df["lvef_binary_reduced"].to_numpy(dtype=np.int32)

    train_mask = model_df["split"] == "train"
    val_mask = model_df["split"] == "val"
    test_mask = model_df["split"] == "test"
    if train_mask.sum() == 0 or val_mask.sum() == 0 or test_mask.sum() == 0:
        raise RuntimeError("Expected non-empty train/val/test after merge.")

    reg_model = Pipeline(
        steps=[
            ("scaler", StandardScaler(with_mean=True, with_std=True)),
            ("ridge", Ridge(alpha=args.ridge_alpha, random_state=args.seed)),
        ]
    )
    clf_model = Pipeline(
        steps=[
            ("scaler", StandardScaler(with_mean=True, with_std=True)),
            ("logreg", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=args.seed)),
        ]
    )

    reg_model.fit(x[train_mask], y_reg[train_mask])
    clf_model.fit(x[train_mask], y_bin[train_mask])

    model_df = model_df.copy()
    model_df["pred_lvef"] = reg_model.predict(x).astype(np.float32)
    model_df["pred_reduced_prob"] = clf_model.predict_proba(x)[:, 1].astype(np.float32)

    clip_metrics = {
        split: evaluate_frame(
            df=model_df[model_df["split"] == split].reset_index(drop=True),
            y_reg_pred=model_df.loc[model_df["split"] == split, "pred_lvef"].to_numpy(dtype=np.float32),
            y_prob=model_df.loc[model_df["split"] == split, "pred_reduced_prob"].to_numpy(dtype=np.float32),
            threshold=args.clip_threshold,
            level="clip",
        )
        for split in ["train", "val", "test"]
    }

    study_df = (
        model_df.groupby(["split", "subject_id", "study_id"], as_index=False)
        .agg(
            lvef=("lvef", "median"),
            lvef_binary_reduced=("lvef_binary_reduced", "max"),
            pred_lvef=("pred_lvef", "mean"),
            pred_reduced_prob=("pred_reduced_prob", "mean"),
            n_clips=(idx_col, "count"),
        )
        .reset_index(drop=True)
    )
    study_metrics = {
        split: evaluate_frame(
            df=study_df[study_df["split"] == split].reset_index(drop=True),
            y_reg_pred=study_df.loc[study_df["split"] == split, "pred_lvef"].to_numpy(dtype=np.float32),
            y_prob=study_df.loc[study_df["split"] == split, "pred_reduced_prob"].to_numpy(dtype=np.float32),
            threshold=args.clip_threshold,
            level="study",
        )
        for split in ["train", "val", "test"]
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    clip_pred_csv = args.output_dir / "echoprime_embedding_clip_predictions.csv"
    study_pred_csv = args.output_dir / "echoprime_embedding_study_predictions.csv"
    metrics_json = args.output_dir / "echoprime_embedding_baseline_metrics.json"
    model_df.to_csv(clip_pred_csv, index=False)
    study_df.to_csv(study_pred_csv, index=False)

    metrics = {
        "embedding_npz": str(args.embedding_npz.resolve()),
        "embedding_manifest": str(args.embedding_manifest.resolve()),
        "label_manifest": str(args.label_manifest.resolve()),
        "n_joined_rows": int(len(model_df)),
        "n_joined_studies": int(model_df["study_id"].nunique()),
        "n_joined_subjects": int(model_df["subject_id"].nunique()),
        "ridge_alpha": float(args.ridge_alpha),
        "clip_threshold": float(args.clip_threshold),
        "clip_metrics": clip_metrics,
        "study_metrics": study_metrics,
        "outputs": {
            "clip_predictions_csv": str(clip_pred_csv.resolve()),
            "study_predictions_csv": str(study_pred_csv.resolve()),
            "metrics_json": str(metrics_json.resolve()),
        },
    }
    metrics_json.write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))
    print(f"[written] {clip_pred_csv.resolve()}")
    print(f"[written] {study_pred_csv.resolve()}")
    print(f"[written] {metrics_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
