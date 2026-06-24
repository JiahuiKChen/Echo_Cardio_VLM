#!/usr/bin/env python3
"""Run Phase 2 imaging-only baselines for TAPSE and LVOT VTI.

Primary intended use is LVOT VTI continuous regression from study-level
EchoPrime embeddings. TAPSE is supported as a cautious secondary target.
Prediction outputs are patient-level and must remain in approved restricted
storage; aggregate metric outputs may be reviewed for manuscript use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import warnings as py_warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


TARGETS: dict[str, dict[str, Any]] = {
    "lvot_vti": {
        "measurement": "lvot_vti",
        "clinical_unit": "cm",
        "plausible_low": 5.0,
        "plausible_high": 35.0,
        "hard_high": 60.0,
        "primary_tolerance": 2.0,
        "secondary_tolerance": 3.0,
        "binary_thresholds": {"low_lt_18cm": 18.0, "low_lt_20cm": 20.0},
        "phase2_role": "primary",
    },
    "tapse": {
        "measurement": "tapse",
        "clinical_unit": "mm",
        "plausible_low": 5.0,
        "plausible_high": 40.0,
        "hard_high": 50.0,
        "primary_tolerance": 3.0,
        "secondary_tolerance": 5.0,
        "binary_thresholds": {"low_lt_17mm": 17.0},
        "phase2_role": "cautious_secondary",
    },
}

DEFAULT_RIDGE_ALPHAS = "0.01,0.03,0.1,0.3,1,3,10,30,100,300,1000"
RIDGE_SOLVERS = ["auto", "svd", "cholesky", "lsqr", "sparse_cg", "sag", "saga"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structured-measurements-csv", type=Path, required=True)
    parser.add_argument("--study-embedding-npz", type=Path, required=True)
    parser.add_argument("--study-embedding-manifest", type=Path, required=True)
    parser.add_argument("--subject-split-map-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", choices=["lvot_vti", "tapse", "all"], default="lvot_vti")
    parser.add_argument("--analysis-label", default="all_clips_study_embeddings")
    parser.add_argument("--ridge-alphas", default=DEFAULT_RIDGE_ALPHAS)
    parser.add_argument(
        "--ridge-solver",
        choices=RIDGE_SOLVERS,
        default="svd",
        help="Ridge solver. stable-v2 defaults to svd for numerical stability.",
    )
    parser.add_argument(
        "--standardize-features",
        dest="standardize_features",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fit StandardScaler on the training split before Ridge. Enabled by default.",
    )
    parser.add_argument("--n-bootstrap", type=int, default=2000)
    parser.add_argument("--bootstrap-unit", choices=["subject", "study"], default="subject")
    parser.add_argument("--random-seed", type=int, default=None, help="Primary reproducibility seed.")
    parser.add_argument("--seed", type=int, default=1337, help="Legacy alias used when --random-seed is omitted.")
    parser.add_argument("--min-train-n", type=int, default=120)
    parser.add_argument("--min-val-n", type=int, default=40)
    parser.add_argument("--min-test-n", type=int, default=40)
    parser.add_argument(
        "--exclude-hard-extremes",
        action="store_true",
        help="Exclude values <=0 or above target hard_high. Counts are reported; default keeps all values.",
    )
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit patient-level prediction output inside the git worktree. Use only for synthetic tests.",
    )
    parser.add_argument(
        "--write-patient-predictions",
        dest="write_patient_predictions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write per-study prediction CSV. Requires --allow-restricted-patient-outputs outside tests.",
    )
    parser.add_argument(
        "--allow-restricted-patient-outputs",
        action="store_true",
        help="Acknowledge that patient-level predictions are being written to approved restricted storage.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9%+\-'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_unit(value: Any) -> str:
    text = normalize_text(value)
    aliases = {
        "centimeter": "cm",
        "centimeters": "cm",
        "cm": "cm",
        "millimeter": "mm",
        "millimeters": "mm",
        "mm": "mm",
    }
    return aliases.get(text, text or "unknown")


def parse_float_list(text: str) -> list[float]:
    out = [float(piece.strip()) for piece in text.split(",") if piece.strip()]
    if not out:
        raise ValueError("At least one ridge alpha is required.")
    return out


def effective_random_seed(args: argparse.Namespace) -> int:
    if args.random_seed is not None:
        return int(args.random_seed)
    return int(args.seed)


def existing_required_paths(args: argparse.Namespace) -> list[Path]:
    return [
        args.structured_measurements_csv,
        args.study_embedding_npz,
        args.study_embedding_manifest,
        args.subject_split_map_csv,
    ]


def example_scc_command() -> str:
    return """cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2

$PY scripts/run_tapse_lvot_vti_imaging_baseline.py \\
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \\
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \\
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \\
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \\
  --target lvot_vti \\
  --analysis-label all_clips_study_embeddings_stable_v2 \\
  --ridge-solver svd \\
  --standardize-features \\
  --allow-restricted-patient-outputs \\
  --output-dir $OUT/lvot_vti/all_clips"""


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def resolve_patient_prediction_policy(args: argparse.Namespace, warnings: list[str]) -> bool:
    if not args.write_patient_predictions:
        warnings.append("Patient-level predictions disabled by --no-write-patient-predictions.")
        return False

    if inside_current_worktree(args.output_dir) and not args.allow_repo_output_for_testing:
        warnings.append(
            "Output directory is inside the git worktree; patient-level predictions will not be written."
        )
        return False

    if not args.allow_restricted_patient_outputs and not args.allow_repo_output_for_testing:
        warnings.append(
            "Patient-level predictions requested without --allow-restricted-patient-outputs; "
            "predictions will not be written."
        )
        return False

    return True


def stable_seed(seed: int, *parts: str) -> int:
    payload = "::".join([str(seed), *parts]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:8]
    return int(digest, 16) % (2**31 - 1)


def to_clinical_value(target: str, values: pd.Series, units: pd.Series) -> pd.Series:
    vals = pd.to_numeric(values, errors="coerce")
    unit_norm = units.map(normalize_unit)
    converted = vals.copy()
    if target == "tapse":
        converted.loc[unit_norm == "cm"] = vals.loc[unit_norm == "cm"] * 10.0
        converted.loc[unit_norm == "mm"] = vals.loc[unit_norm == "mm"]
    elif target == "lvot_vti":
        converted.loc[unit_norm == "mm"] = vals.loc[unit_norm == "mm"] / 10.0
        converted.loc[unit_norm == "cm"] = vals.loc[unit_norm == "cm"]
    return converted


def extract_target(measures: pd.DataFrame, target: str, exclude_hard_extremes: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    cfg = TARGETS[target]
    required = {"subject_id", "study_id", "measurement", "result"}
    missing = required - set(measures.columns)
    if missing:
        raise ValueError(f"Structured measurements missing required columns: {sorted(missing)}")

    df = measures.copy()
    unit_col = "unit" if "unit" in df.columns else "units" if "units" in df.columns else None
    if unit_col is None:
        df["_unit"] = "unknown"
        unit_col = "_unit"
    df["measurement_norm"] = df["measurement"].map(normalize_text)
    rows = df[df["measurement_norm"] == normalize_text(cfg["measurement"])].copy()
    rows["result_num"] = pd.to_numeric(rows["result"], errors="coerce")
    rows = rows[rows["result_num"].notna()].copy()
    rows["target_value"] = to_clinical_value(target, rows["result_num"], rows[unit_col])
    rows["outside_primary_range"] = (rows["target_value"] < cfg["plausible_low"]) | (
        rows["target_value"] > cfg["plausible_high"]
    )
    rows["hard_invalid_or_extreme"] = (rows["target_value"] <= 0.0) | (rows["target_value"] > cfg["hard_high"])

    numeric_rows_before_exclusions = int(len(rows))
    hard_invalid_or_extreme_before_exclusions = int(rows["hard_invalid_or_extreme"].sum())
    excluded_hard = int(rows["hard_invalid_or_extreme"].sum()) if exclude_hard_extremes else 0
    if exclude_hard_extremes:
        rows = rows[~rows["hard_invalid_or_extreme"]].copy()

    rows["study_id_str"] = rows["study_id"].astype(str)
    rows["subject_id_str"] = rows["subject_id"].astype(str)
    grouped = (
        rows.groupby(["study_id_str", "subject_id_str"], as_index=False)
        .agg(
            study_id=("study_id", "first"),
            subject_id=("subject_id", "first"),
            target_value=("target_value", "median"),
            n_target_rows=("target_value", "size"),
            outside_primary_range=("outside_primary_range", "max"),
            hard_invalid_or_extreme=("hard_invalid_or_extreme", "max"),
        )
        .reset_index(drop=True)
    )
    summary = {
        "target": target,
        "clinical_unit": cfg["clinical_unit"],
        "numeric_rows_before_exclusions": numeric_rows_before_exclusions,
        "hard_invalid_or_extreme_before_exclusions": hard_invalid_or_extreme_before_exclusions,
        "numeric_rows": int(len(rows)),
        "numeric_studies": int(grouped["study_id_str"].nunique()),
        "numeric_subjects": int(grouped["subject_id_str"].nunique()),
        "studies_with_multiple_numeric_values": int((grouped["n_target_rows"] > 1).sum()),
        "outside_primary_range_studies": int(grouped["outside_primary_range"].sum()) if not grouped.empty else 0,
        "hard_invalid_or_extreme_studies": int(grouped["hard_invalid_or_extreme"].sum()) if not grouped.empty else 0,
        "hard_invalid_or_extreme_excluded": excluded_hard,
        "aggregation": "median per study",
    }
    return grouped, summary


def load_embeddings(npz_path: Path, manifest_path: Path) -> tuple[np.ndarray, pd.DataFrame, str]:
    with np.load(npz_path) as data:
        if "embeddings" not in data:
            raise ValueError(f"{npz_path} is missing an 'embeddings' array")
        embeddings = data["embeddings"].astype(np.float32)
    if embeddings.ndim != 2:
        raise ValueError(f"Expected 2D embeddings, got shape {embeddings.shape}")

    manifest = pd.read_csv(manifest_path).copy()
    if "study_id" not in manifest.columns:
        raise ValueError("Study embedding manifest must include study_id")
    if "study_idx" in manifest.columns:
        idx_col = "study_idx"
    elif "embedding_idx" in manifest.columns:
        idx_col = "embedding_idx"
    else:
        if len(manifest) != embeddings.shape[0]:
            raise ValueError(
                "Study embedding manifest has no index column and row count does not match embeddings: "
                f"{len(manifest)} vs {embeddings.shape[0]}"
            )
        manifest["_study_idx"] = np.arange(len(manifest), dtype=int)
        idx_col = "_study_idx"
    manifest[idx_col] = pd.to_numeric(manifest[idx_col], errors="coerce")
    manifest = manifest[manifest[idx_col].notna()].copy()
    manifest[idx_col] = manifest[idx_col].astype(int)
    if manifest[idx_col].max() >= embeddings.shape[0] or manifest[idx_col].min() < 0:
        raise ValueError(
            f"Embedding index out of range for {idx_col}: "
            f"min={manifest[idx_col].min()}, max={manifest[idx_col].max()}, rows={embeddings.shape[0]}"
        )
    manifest["study_id_str"] = manifest["study_id"].astype(str)
    if "subject_id" in manifest.columns:
        manifest["embedding_subject_id_str"] = manifest["subject_id"].astype(str)
    return embeddings, manifest, idx_col


def load_splits(path: Path) -> tuple[pd.DataFrame, list[str]]:
    splits = pd.read_csv(path).copy()
    warnings: list[str] = []
    if "subject_id" not in splits.columns or "split" not in splits.columns:
        raise ValueError("Subject split map must include subject_id and split columns.")
    splits["subject_id_str"] = splits["subject_id"].astype(str)
    splits["split"] = splits["split"].astype(str).str.lower()
    bad_splits = sorted(set(splits["split"]) - {"train", "val", "test"})
    if bad_splits:
        warnings.append(f"Unexpected split labels: {bad_splits}")
    subject_split_counts = splits.groupby("subject_id_str")["split"].nunique()
    leaking_subjects = int((subject_split_counts > 1).sum())
    if leaking_subjects:
        raise RuntimeError(f"Subject split leakage detected: {leaking_subjects} subjects have multiple splits.")
    splits = splits.drop_duplicates(subset=["subject_id_str"], keep="first").copy()
    return splits[["subject_id_str", "split"]], warnings


def join_model_frame(
    target_df: pd.DataFrame,
    embeddings: np.ndarray,
    emb_manifest: pd.DataFrame,
    idx_col: str,
    splits: pd.DataFrame,
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    warnings: list[str] = []
    emb_cols = ["study_id_str", idx_col]
    if "embedding_subject_id_str" in emb_manifest.columns:
        emb_cols.append("embedding_subject_id_str")
    for optional_col in ["n_selected_clips", "view_policy", "threshold", "pooling", "view_policy_score_max"]:
        if optional_col in emb_manifest.columns:
            emb_cols.append(optional_col)

    joined = target_df.merge(emb_manifest[emb_cols].drop_duplicates("study_id_str"), on="study_id_str", how="inner")
    if joined.empty:
        raise RuntimeError("No rows after joining targets with study embeddings.")

    if "embedding_subject_id_str" in joined.columns:
        mismatch = joined["embedding_subject_id_str"].notna() & (
            joined["embedding_subject_id_str"] != joined["subject_id_str"]
        )
        if int(mismatch.sum()):
            raise RuntimeError(f"{int(mismatch.sum())} study rows have subject_id mismatch after embedding join.")

    joined = joined.merge(splits, on="subject_id_str", how="left")
    missing_split = int(joined["split"].isna().sum())
    if missing_split:
        warnings.append(f"{missing_split} target+embedding rows lack split assignment and will be excluded.")
    joined = joined[joined["split"].isin(["train", "val", "test"])].copy()
    if joined.empty:
        raise RuntimeError("No rows remain after joining subject splits.")

    x = embeddings[joined[idx_col].to_numpy(dtype=int)]
    return joined.reset_index(drop=True), x, warnings


def safe_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    if len(y_true) < 2 or np.allclose(np.std(y_true), 0.0):
        return None
    return float(r2_score(y_true, y_pred))


def safe_corr(y_true: np.ndarray, y_pred: np.ndarray, method: str) -> float | None:
    if len(y_true) < 3 or np.allclose(np.std(y_true), 0.0) or np.allclose(np.std(y_pred), 0.0):
        return None
    frame = pd.DataFrame({"y_true": y_true, "y_pred": y_pred})
    value = frame["y_true"].corr(frame["y_pred"], method=method)
    return float(value) if pd.notna(value) else None


def calibration(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float | None, float | None]:
    if len(y_true) < 3 or np.allclose(np.std(y_pred), 0.0):
        return None, None
    slope, intercept = np.polyfit(y_pred.astype(float), y_true.astype(float), deg=1)
    return float(slope), float(intercept)


def continuous_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    target: str,
    split: str,
    model_name: str,
    n_subjects: int,
) -> dict[str, Any]:
    cfg = TARGETS[target]
    err = y_pred - y_true
    abs_err = np.abs(err)
    q25, q75 = np.percentile(y_true, [25, 75]) if len(y_true) else (np.nan, np.nan)
    target_iqr = float(q75 - q25) if np.isfinite(q75 - q25) else np.nan
    slope, intercept = calibration(y_true, y_pred)
    bias = float(np.mean(err)) if len(err) else np.nan
    sd_err = float(np.std(err, ddof=1)) if len(err) > 1 else np.nan
    return {
        "target": target,
        "target_phase2_role": cfg["phase2_role"],
        "split": split,
        "model": model_name,
        "n_studies": int(len(y_true)),
        "n_subjects": int(n_subjects),
        "target_unit": cfg["clinical_unit"],
        "target_median": float(np.median(y_true)) if len(y_true) else np.nan,
        "target_iqr": target_iqr,
        "mae": float(mean_absolute_error(y_true, y_pred)) if len(y_true) else np.nan,
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))) if len(y_true) else np.nan,
        "r2": safe_r2(y_true, y_pred),
        "median_absolute_error": float(np.median(abs_err)) if len(abs_err) else np.nan,
        "mae_over_train_iqr": np.nan,
        "bias_pred_minus_true": bias,
        "bland_altman_lower": float(bias - 1.96 * sd_err) if np.isfinite(sd_err) else np.nan,
        "bland_altman_upper": float(bias + 1.96 * sd_err) if np.isfinite(sd_err) else np.nan,
        "pearson": safe_corr(y_true, y_pred, "pearson"),
        "spearman": safe_corr(y_true, y_pred, "spearman"),
        "calibration_slope_true_on_pred": slope,
        "calibration_intercept_true_on_pred": intercept,
        f"within_{cfg['primary_tolerance']:g}_{cfg['clinical_unit']}": float(np.mean(abs_err <= cfg["primary_tolerance"]))
        if len(abs_err)
        else np.nan,
        f"within_{cfg['secondary_tolerance']:g}_{cfg['clinical_unit']}": float(
            np.mean(abs_err <= cfg["secondary_tolerance"])
        )
        if len(abs_err)
        else np.nan,
    }


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray, target: str, split: str, model_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, threshold in TARGETS[target]["binary_thresholds"].items():
        true_abn = (y_true < threshold).astype(int)
        pred_abn = (y_pred < threshold).astype(int)
        tn, fp, fn, tp = confusion_matrix(true_abn, pred_abn, labels=[0, 1]).ravel()
        if len(np.unique(true_abn)) == 2:
            auc = float(roc_auc_score(true_abn, -y_pred))
            ap = float(average_precision_score(true_abn, -y_pred))
        else:
            auc = None
            ap = None
        sensitivity = float(tp / (tp + fn)) if (tp + fn) else np.nan
        specificity = float(tn / (tn + fp)) if (tn + fp) else np.nan
        ppv = float(tp / (tp + fp)) if (tp + fp) else np.nan
        npv = float(tn / (tn + fn)) if (tn + fn) else np.nan
        rows.append(
            {
                "target": target,
                "split": split,
                "model": model_name,
                "threshold_label": label,
                "threshold_value": float(threshold),
                "n": int(len(y_true)),
                "prevalence": float(true_abn.mean()) if len(true_abn) else np.nan,
                "predicted_positive_rate": float(pred_abn.mean()) if len(pred_abn) else np.nan,
                "accuracy": float(accuracy_score(true_abn, pred_abn)) if len(true_abn) else np.nan,
                "f1": float(f1_score(true_abn, pred_abn, zero_division=0)) if len(true_abn) else np.nan,
                "sensitivity": sensitivity,
                "specificity": specificity,
                "ppv": ppv,
                "npv": npv,
                "auroc_continuous_score": auc if auc is not None else np.nan,
                "average_precision_continuous_score": ap if ap is not None else np.nan,
                "tp": int(tp),
                "fp": int(fp),
                "tn": int(tn),
                "fn": int(fn),
            }
        )
    return rows


def build_ridge_pipeline(alpha: float, solver: str, standardize_features: bool, seed: int) -> Pipeline:
    steps: list[tuple[str, Any]] = []
    if standardize_features:
        steps.append(("scaler", StandardScaler()))
    steps.append(("ridge", Ridge(alpha=alpha, solver=solver, random_state=seed)))
    return Pipeline(steps)


def fit_ridge_with_val_selection(
    x: np.ndarray,
    y: np.ndarray,
    split: pd.Series,
    alphas: list[float],
    seed: int,
    solver: str,
    standardize_features: bool,
) -> tuple[np.ndarray, float, pd.DataFrame, list[dict[str, Any]]]:
    train_mask = split.eq("train").to_numpy()
    val_mask = split.eq("val").to_numpy()
    selection_rows: list[dict[str, Any]] = []
    warning_records: list[dict[str, Any]] = []
    best_alpha = alphas[0]
    best_val_mae = np.inf
    best_predictions: np.ndarray | None = None

    for alpha in alphas:
        model = build_ridge_pipeline(alpha, solver, standardize_features, seed)
        with py_warnings.catch_warnings(record=True) as caught:
            py_warnings.simplefilter("always")
            model.fit(x[train_mask], y[train_mask])
            pred = model.predict(x).astype(np.float32)
        warning_messages = [str(item.message) for item in caught]
        for item in caught:
            warning_records.append(
                {
                    "alpha": float(alpha),
                    "category": item.category.__name__,
                    "message": str(item.message),
                }
            )
        val_mae = float(mean_absolute_error(y[val_mask], pred[val_mask]))
        selection_rows.append(
            {
                "alpha": float(alpha),
                "val_mae": val_mae,
                "ridge_solver": solver,
                "features_standardized": bool(standardize_features),
                "warning_count": int(len(warning_messages)),
                "warning_messages": " | ".join(dict.fromkeys(warning_messages)),
            }
        )
        if val_mae < best_val_mae:
            best_alpha = alpha
            best_val_mae = val_mae
            best_predictions = pred

    if best_predictions is None:  # pragma: no cover - alphas validation prevents this
        raise RuntimeError("No Ridge model was fit.")
    selection = pd.DataFrame(selection_rows)
    selection["selected"] = selection["alpha"].eq(best_alpha)
    return best_predictions, float(best_alpha), selection, warning_records


def split_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {split: int((frame["split"] == split).sum()) for split in ["train", "val", "test"]}


def bootstrap_ci(
    frame: pd.DataFrame,
    target: str,
    model_name: str,
    bootstrap_unit: str,
    n_bootstrap: int,
    seed: int,
) -> list[dict[str, Any]]:
    if n_bootstrap <= 0 or frame.empty:
        return []
    unit_col = "subject_id_str" if bootstrap_unit == "subject" else "study_id_str"
    units = frame[unit_col].drop_duplicates().to_numpy()
    if len(units) < 5:
        return []
    rng = np.random.default_rng(seed)
    metrics: dict[str, list[float]] = {
        "mae": [],
        "rmse": [],
        "r2": [],
        "median_absolute_error": [],
        "bias_pred_minus_true": [],
    }
    pred_col = f"pred_{model_name}"
    for _ in range(n_bootstrap):
        sampled_units = rng.choice(units, size=len(units), replace=True)
        parts = [frame[frame[unit_col] == unit] for unit in sampled_units]
        sample = pd.concat(parts, ignore_index=True)
        y_true = sample["target_value"].to_numpy(dtype=np.float32)
        y_pred = sample[pred_col].to_numpy(dtype=np.float32)
        row = continuous_metrics(y_true, y_pred, target, "test", model_name, sample["subject_id_str"].nunique())
        for metric in metrics:
            value = row.get(metric)
            if value is not None and pd.notna(value):
                metrics[metric].append(float(value))

    rows: list[dict[str, Any]] = []
    for metric, values in metrics.items():
        if not values:
            continue
        arr = np.asarray(values, dtype=float)
        rows.append(
            {
                "target": target,
                "model": model_name,
                "split": "test",
                "bootstrap_unit": bootstrap_unit,
                "n_bootstrap": int(n_bootstrap),
                "metric": metric,
                "mean": float(np.mean(arr)),
                "ci_lower_2_5": float(np.percentile(arr, 2.5)),
                "ci_upper_97_5": float(np.percentile(arr, 97.5)),
            }
        )
    return rows


def run_target(
    target: str,
    measures: pd.DataFrame,
    embeddings: np.ndarray,
    emb_manifest: pd.DataFrame,
    idx_col: str,
    splits: pd.DataFrame,
    args: argparse.Namespace,
    split_warnings: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    target_df, target_summary = extract_target(measures, target, args.exclude_hard_extremes)
    frame, x, join_warnings = join_model_frame(target_df, embeddings, emb_manifest, idx_col, splits)
    y = frame["target_value"].to_numpy(dtype=np.float32)

    counts = split_counts(frame)
    skip_reason = ""
    if counts["train"] < args.min_train_n:
        skip_reason = "insufficient_train"
    elif counts["val"] < args.min_val_n:
        skip_reason = "insufficient_val"
    elif counts["test"] < args.min_test_n:
        skip_reason = "insufficient_test"

    summary = {
        **target_summary,
        "analysis_label": args.analysis_label,
        "joined_target_embedding_studies": int(frame["study_id_str"].nunique()),
        "joined_target_embedding_subjects": int(frame["subject_id_str"].nunique()),
        "split_counts": counts,
        "status": "skipped" if skip_reason else "ok",
        "skip_reason": skip_reason,
        "ridge_solver": args.ridge_solver,
        "features_standardized": bool(args.standardize_features),
        "ridge_alpha_grid": parse_float_list(args.ridge_alphas),
        "random_seed": effective_random_seed(args),
        "hard_extremes_excluded": bool(args.exclude_hard_extremes),
        "warnings": split_warnings + join_warnings,
    }

    if skip_reason:
        skip_row = {
            "target": target,
            "target_phase2_role": TARGETS[target]["phase2_role"],
            "split": "all",
            "model": "not_run",
            "n_studies": int(len(frame)),
            "n_subjects": int(frame["subject_id_str"].nunique()),
            "status": "skipped",
            "skip_reason": skip_reason,
        }
        return (
            pd.DataFrame([skip_row]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            frame.assign(pred_null_median=np.nan, pred_ridge=np.nan),
            summary,
        )

    train_mask = frame["split"].eq("train").to_numpy()
    train_median = float(np.median(y[train_mask]))
    frame = frame.copy()
    frame["pred_null_median"] = train_median

    random_seed = effective_random_seed(args)
    ridge_seed = stable_seed(random_seed, target, args.analysis_label, "ridge")
    frame["pred_ridge"], selected_alpha, alpha_selection, ridge_warning_records = fit_ridge_with_val_selection(
        x,
        y,
        frame["split"],
        parse_float_list(args.ridge_alphas),
        ridge_seed,
        args.ridge_solver,
        bool(args.standardize_features),
    )
    alpha_selection.insert(0, "target", target)
    alpha_selection.insert(1, "analysis_label", args.analysis_label)

    train_iqr = float(np.percentile(y[train_mask], 75) - np.percentile(y[train_mask], 25))

    metric_rows: list[dict[str, Any]] = []
    binary_rows: list[dict[str, Any]] = []
    for model_name in ["null_median", "ridge"]:
        pred_col = f"pred_{model_name}"
        for split in ["train", "val", "test"]:
            subset = frame[frame["split"] == split]
            y_true = subset["target_value"].to_numpy(dtype=np.float32)
            y_pred = subset[pred_col].to_numpy(dtype=np.float32)
            row = continuous_metrics(
                y_true,
                y_pred,
                target,
                split,
                model_name,
                subset["subject_id_str"].nunique(),
            )
            row["status"] = "ok"
            row["skip_reason"] = ""
            row["analysis_label"] = args.analysis_label
            row["ridge_alpha_selected"] = selected_alpha if model_name == "ridge" else np.nan
            row["ridge_solver"] = args.ridge_solver if model_name == "ridge" else ""
            row["features_standardized"] = bool(args.standardize_features) if model_name == "ridge" else np.nan
            if np.isfinite(train_iqr) and train_iqr > 0 and pd.notna(row["mae"]):
                row["mae_over_train_iqr"] = float(row["mae"] / train_iqr)
            metric_rows.append(row)
            binary_rows.extend(binary_metrics(y_true, y_pred, target, split, model_name))

    ci_rows: list[dict[str, Any]] = []
    test_frame = frame[frame["split"] == "test"].copy()
    for model_name in ["null_median", "ridge"]:
        ci_rows.extend(
            bootstrap_ci(
                test_frame,
                target,
                model_name,
                args.bootstrap_unit,
                args.n_bootstrap,
                stable_seed(random_seed, target, args.analysis_label, model_name, "bootstrap"),
            )
        )

    summary["ridge_alpha_selected"] = selected_alpha
    summary["train_target_iqr"] = train_iqr
    summary["ridge_warning_count"] = int(len(ridge_warning_records))
    summary["ridge_warnings"] = ridge_warning_records
    if ridge_warning_records:
        summary["warnings"] = summary["warnings"] + [
            f"Ridge emitted {len(ridge_warning_records)} warning(s); see imaging_baseline_warnings.json."
        ]
    return (
        pd.DataFrame(metric_rows),
        pd.DataFrame(binary_rows),
        alpha_selection,
        pd.DataFrame(ci_rows),
        frame,
        summary,
    )


def main() -> int:
    args = parse_args()
    run_warnings: list[str] = []
    write_patient_predictions = resolve_patient_prediction_policy(args, run_warnings)

    missing_paths = [str(path) for path in existing_required_paths(args) if not path.exists()]
    if missing_paths:
        payload = {
            "blocked_for_modeling": True,
            "missing_paths": missing_paths,
            "example_scc_command": example_scc_command(),
            "warnings": run_warnings,
        }
        print(json.dumps(payload, indent=2))
        return 2

    measures = pd.read_csv(args.structured_measurements_csv)
    embeddings, emb_manifest, idx_col = load_embeddings(args.study_embedding_npz, args.study_embedding_manifest)
    splits, split_warnings = load_splits(args.subject_split_map_csv)

    targets = ["lvot_vti", "tapse"] if args.target == "all" else [args.target]
    metric_frames: list[pd.DataFrame] = []
    binary_frames: list[pd.DataFrame] = []
    alpha_frames: list[pd.DataFrame] = []
    ci_frames: list[pd.DataFrame] = []
    prediction_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for target in targets:
        metrics, binary, alpha_selection, bootstrap_ci_df, preds, summary = run_target(
            target,
            measures,
            embeddings,
            emb_manifest,
            idx_col,
            splits,
            args,
            split_warnings,
        )
        metric_frames.append(metrics)
        binary_frames.append(binary)
        alpha_frames.append(alpha_selection)
        ci_frames.append(bootstrap_ci_df)
        if write_patient_predictions:
            prediction_cols = [
                "target",
                "analysis_label",
                "subject_id",
                "study_id",
                "split",
                "target_value",
                "outside_primary_range",
                "hard_invalid_or_extreme",
                "n_target_rows",
                "pred_null_median",
                "pred_ridge",
            ]
            preds = preds.copy()
            preds["target"] = target
            preds["analysis_label"] = args.analysis_label
            optional_cols = [
                c
                for c in ["n_selected_clips", "view_policy", "threshold", "pooling", "view_policy_score_max"]
                if c in preds.columns
            ]
            prediction_frames.append(preds[[c for c in prediction_cols + optional_cols if c in preds.columns]])
        summaries.append(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / "imaging_baseline_metrics.csv"
    binary_path = args.output_dir / "imaging_baseline_binary_metrics.csv"
    alpha_path = args.output_dir / "imaging_baseline_ridge_alpha_selection.csv"
    ci_path = args.output_dir / "imaging_baseline_bootstrap_ci.csv"
    predictions_path = args.output_dir / "imaging_baseline_predictions.csv"
    summary_path = args.output_dir / "imaging_baseline_summary.json"
    warnings_path = args.output_dir / "imaging_baseline_warnings.json"

    metrics_df = pd.concat(metric_frames, ignore_index=True, sort=False) if metric_frames else pd.DataFrame()
    binary_df = pd.concat(binary_frames, ignore_index=True, sort=False) if binary_frames else pd.DataFrame()
    alpha_df = pd.concat(alpha_frames, ignore_index=True, sort=False) if alpha_frames else pd.DataFrame()
    ci_df = pd.concat(ci_frames, ignore_index=True, sort=False) if ci_frames else pd.DataFrame()
    predictions_df = pd.concat(prediction_frames, ignore_index=True, sort=False) if prediction_frames else pd.DataFrame()

    metrics_df.to_csv(metrics_path, index=False)
    binary_df.to_csv(binary_path, index=False)
    alpha_df.to_csv(alpha_path, index=False)
    ci_df.to_csv(ci_path, index=False)
    if write_patient_predictions:
        predictions_df.to_csv(predictions_path, index=False)

    all_target_warnings = [
        warning
        for summary in summaries
        for warning in summary.get("warnings", [])
    ]
    ridge_warning_records = [
        {
            "target": summary.get("target"),
            **warning,
        }
        for summary in summaries
        for warning in summary.get("ridge_warnings", [])
    ]
    all_warnings = list(dict.fromkeys(run_warnings + all_target_warnings))
    warnings_payload = {
        "warnings": all_warnings,
        "ridge_warning_count": int(len(ridge_warning_records)),
        "ridge_warnings": ridge_warning_records,
        "patient_level_predictions_written": bool(write_patient_predictions),
        "patient_level_prediction_path": str(predictions_path) if write_patient_predictions else "",
    }
    warnings_path.write_text(json.dumps(warnings_payload, indent=2))

    summary_payload = {
        "target": targets[0] if len(targets) == 1 else "all",
        "analysis_label": args.analysis_label,
        "targets": targets,
        "clinical_units": {target: TARGETS[target]["clinical_unit"] for target in targets},
        "structured_measurements_csv": str(args.structured_measurements_csv),
        "study_embedding_npz": str(args.study_embedding_npz),
        "study_embedding_manifest": str(args.study_embedding_manifest),
        "subject_split_map_csv": str(args.subject_split_map_csv),
        "embedding_index_column": idx_col,
        "embedding_shape": list(embeddings.shape),
        "ridge_solver": args.ridge_solver,
        "features_standardized": bool(args.standardize_features),
        "ridge_alphas": parse_float_list(args.ridge_alphas),
        "sklearn_version": sklearn.__version__,
        "n_bootstrap": int(args.n_bootstrap),
        "bootstrap_unit": args.bootstrap_unit,
        "random_seed": effective_random_seed(args),
        "legacy_seed_arg": int(args.seed),
        "hard_extremes_excluded": bool(args.exclude_hard_extremes),
        "patient_level_outputs_written": bool(write_patient_predictions),
        "patient_level_outputs": [str(predictions_path)] if write_patient_predictions else [],
        "aggregate_outputs": [
            str(metrics_path),
            str(binary_path),
            str(alpha_path),
            str(ci_path),
            str(warnings_path),
            str(summary_path),
        ],
        "manuscript_safe_after_review": [
            str(metrics_path),
            str(binary_path),
            str(alpha_path),
            str(ci_path),
            str(warnings_path),
            str(summary_path),
        ],
        "governance_note": (
            "Prediction CSVs are patient-level restricted outputs and must not be committed. "
            "Aggregate metrics, alpha selection, bootstrap CIs, warnings, and summary JSON may be "
            "manuscript-safe after review."
        ),
        "warnings": all_warnings,
        "target_summaries": summaries,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2))

    print(json.dumps(summary_payload, indent=2))
    print(f"[written] {metrics_path.resolve()}")
    print(f"[written] {binary_path.resolve()}")
    print(f"[written] {alpha_path.resolve()}")
    print(f"[written] {ci_path.resolve()}")
    print(f"[written] {warnings_path.resolve()}")
    if write_patient_predictions:
        print(f"[written] {predictions_path.resolve()}")
    else:
        print(f"[not-written] {predictions_path.resolve()} (patient-level predictions disabled)")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
