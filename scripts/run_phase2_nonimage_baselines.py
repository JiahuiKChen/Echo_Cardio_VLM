#!/usr/bin/env python3
"""Run leakage-safe non-image baselines for Phase 2 TAPSE/LVOT VTI targets.

This script is intended to run on SCC against the same target, embedding
availability, and subject-level split denominator used by the Phase 2 imaging
baseline. It writes aggregate metrics only by default. It never writes
per-study predictions, subject IDs, or study IDs.
"""
from __future__ import annotations

import argparse
import json
import math
import warnings as py_warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    average_precision_score,
    mean_absolute_error,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from run_tapse_lvot_vti_imaging_baseline import (
    DEFAULT_RIDGE_ALPHAS,
    TARGETS,
    binary_metrics,
    continuous_metrics,
    extract_target,
    load_embeddings,
    load_splits,
    parse_float_list,
    stable_seed,
)


DEFAULT_FULLSCALE_ROOT = Path("outputs/cloud_cohorts/fullscale_all")
DEFAULT_STRUCTURED = DEFAULT_FULLSCALE_ROOT / "manifests" / "structured_measurements.csv"
DEFAULT_STUDY_EMB_NPZ = DEFAULT_FULLSCALE_ROOT / "study_embeddings_512" / "study_embeddings_512.npz"
DEFAULT_STUDY_EMB_MANIFEST = DEFAULT_FULLSCALE_ROOT / "study_embeddings_512" / "study_embedding_manifest.csv"
DEFAULT_SPLIT_MAP = DEFAULT_FULLSCALE_ROOT / "manifests" / "subject_split_map_v1.csv"
DEFAULT_SELECTED_STUDIES = DEFAULT_FULLSCALE_ROOT / "manifests" / "all_eligible_studies.csv"

DEMOGRAPHIC_NUMERIC_CANDIDATES = [
    "age",
    "anchor_age",
    "patient_age",
    "age_at_study",
]
DEMOGRAPHIC_CATEGORICAL_CANDIDATES = [
    "sex",
    "gender",
    "race",
    "ethnicity",
]
STUDY_METADATA_NUMERIC_CANDIDATES = [
    "n_clips",
    "n_selected_clips",
    "n_dicoms",
    "n_candidate_multiframe_clips",
    "n_successful_extractions",
    "n_failed_extractions",
    "n_successful_embeddings",
    "n_failed_embeddings",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="Phase 2 restricted output root.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for aggregate-only outputs.")
    parser.add_argument("--structured-measurements-csv", type=Path, default=DEFAULT_STRUCTURED)
    parser.add_argument("--study-embedding-npz", type=Path, default=DEFAULT_STUDY_EMB_NPZ)
    parser.add_argument("--study-embedding-manifest", type=Path, default=DEFAULT_STUDY_EMB_MANIFEST)
    parser.add_argument("--subject-split-map-csv", type=Path, default=DEFAULT_SPLIT_MAP)
    parser.add_argument("--selected-studies-csv", type=Path, default=DEFAULT_SELECTED_STUDIES)
    parser.add_argument(
        "--demographics-csv",
        type=Path,
        default=None,
        help="Optional approved demographics CSV with subject_id plus age/sex/race/ethnicity columns.",
    )
    parser.add_argument("--targets", default="lvot_vti,tapse")
    parser.add_argument(
        "--baselines",
        default="null,demographics,study_metadata,demographics_plus_study_metadata",
        help=(
            "Comma-separated baseline tiers. Supported: null, demographics, study_metadata, "
            "demographics_plus_study_metadata, imaging_plus_demographics."
        ),
    )
    parser.add_argument("--include-imaging-plus-demographics", action="store_true")
    parser.add_argument("--ridge-alphas", default=DEFAULT_RIDGE_ALPHAS)
    parser.add_argument("--ridge-solver", default="svd")
    parser.add_argument("--standardize-features", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--n-bootstrap", type=int, default=2000)
    parser.add_argument("--binary-n-bootstrap", type=int, default=2000)
    parser.add_argument("--bootstrap-unit", choices=["subject", "study"], default="subject")
    parser.add_argument("--random-seed", type=int, default=1337)
    parser.add_argument("--min-train-n", type=int, default=120)
    parser.add_argument("--min-val-n", type=int, default=40)
    parser.add_argument("--min-test-n", type=int, default=40)
    parser.add_argument("--min-binary-positives", type=int, default=10)
    parser.add_argument("--exclude-hard-extremes", action="store_true")
    parser.add_argument(
        "--aggregate-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write aggregate metrics only. Row-level outputs are not supported by this script.",
    )
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit aggregate output inside the git worktree for synthetic tests only.",
    )
    return parser.parse_args()


def parse_csv_list(text: str) -> list[str]:
    return [part.strip() for part in str(text).split(",") if part.strip()]


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def resolve_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir is not None:
        out = args.output_dir
    elif args.output_root is not None:
        out = args.output_root / "nonimage_baselines"
    else:
        out = Path("outputs") / "phase2_nonimage_baselines"
    if inside_current_worktree(out) and not args.allow_repo_output_for_testing:
        raise RuntimeError(
            f"Refusing to write outputs inside the git worktree: {out}. "
            "Use a restricted SCC output directory or --allow-repo-output-for-testing for synthetic tests."
        )
    return out


def read_optional_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def normalize_id_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "subject_id" in out.columns:
        out["subject_id_str"] = out["subject_id"].astype(str)
    if "study_id" in out.columns:
        out["study_id_str"] = out["study_id"].astype(str)
    return out


def load_study_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing study embedding manifest: {path}")
    df = pd.read_csv(path)
    required = {"study_id", "subject_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Study embedding manifest missing required columns: {sorted(missing)}")
    return normalize_id_columns(df)


def join_target_frame(
    target: str,
    measures: pd.DataFrame,
    study_manifest: pd.DataFrame,
    splits: pd.DataFrame,
    exclude_hard_extremes: bool,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    target_df, target_summary = extract_target(measures, target, exclude_hard_extremes)
    emb_cols = ["study_id_str", "subject_id"]
    if "subject_id_str" in study_manifest.columns:
        emb_cols.append("subject_id_str")
    for col in ["study_idx", "embedding_idx", "n_clips", "n_selected_clips"]:
        if col in study_manifest.columns:
            emb_cols.append(col)
    emb_cols = list(dict.fromkeys(emb_cols))

    joined = target_df.merge(study_manifest[emb_cols].drop_duplicates("study_id_str"), on="study_id_str", how="inner")
    warnings: list[str] = []
    if "subject_id_str_y" in joined.columns:
        mismatch = joined["subject_id_str_y"].notna() & (joined["subject_id_str_y"] != joined["subject_id_str_x"])
        if int(mismatch.sum()):
            raise RuntimeError(f"{int(mismatch.sum())} rows have subject_id mismatch after embedding manifest join.")
        joined = joined.rename(columns={"subject_id_str_x": "subject_id_str"}).drop(columns=["subject_id_str_y"])
    elif "subject_id_str_x" in joined.columns:
        joined = joined.rename(columns={"subject_id_str_x": "subject_id_str"})
    joined = joined.merge(splits, on="subject_id_str", how="left")
    missing_split = int(joined["split"].isna().sum())
    if missing_split:
        warnings.append(f"{missing_split} target+embedding rows lack split assignment and were excluded.")
    joined = joined[joined["split"].isin(["train", "val", "test"])].copy()
    if joined.empty:
        raise RuntimeError(f"No rows remain for {target} after joining target, embedding manifest, and splits.")
    target_summary.update(
        {
            "joined_target_embedding_studies": int(joined["study_id_str"].nunique()),
            "joined_target_embedding_subjects": int(joined["subject_id_str"].nunique()),
            "split_counts": {split: int((joined["split"] == split).sum()) for split in ["train", "val", "test"]},
        }
    )
    return joined.reset_index(drop=True), target_summary, warnings


def available_columns(df: pd.DataFrame, candidates: list[str]) -> list[str]:
    return [col for col in candidates if col in df.columns]


def build_demographics_features(frame: pd.DataFrame, demographics: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str], str]:
    if demographics.empty:
        return pd.DataFrame(index=frame.index), [], [], "missing_demographics_csv"
    demo = normalize_id_columns(demographics)
    if "subject_id_str" not in demo.columns:
        return pd.DataFrame(index=frame.index), [], [], "demographics_missing_subject_id"
    numeric = available_columns(demo, DEMOGRAPHIC_NUMERIC_CANDIDATES)
    categorical = available_columns(demo, DEMOGRAPHIC_CATEGORICAL_CANDIDATES)
    cols = ["subject_id_str"] + numeric + categorical
    merged = frame[["subject_id_str"]].merge(demo[cols].drop_duplicates("subject_id_str"), on="subject_id_str", how="left")
    x = merged[numeric + categorical].copy()
    if not numeric and not categorical:
        return x, [], [], "no_supported_demographic_columns"
    return x, numeric, categorical, ""


def build_study_metadata_features(
    frame: pd.DataFrame,
    study_manifest: pd.DataFrame,
    selected_studies: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[str], str]:
    base_cols = ["study_id_str"] + available_columns(study_manifest, STUDY_METADATA_NUMERIC_CANDIDATES)
    feature_source = study_manifest[base_cols].drop_duplicates("study_id_str").copy()
    if not selected_studies.empty:
        selected = normalize_id_columns(selected_studies)
        selected_cols = ["study_id_str"] + [c for c in ["n_dicoms"] if c in selected.columns]
        if len(selected_cols) > 1:
            feature_source = feature_source.merge(
                selected[selected_cols].drop_duplicates("study_id_str"),
                on="study_id_str",
                how="left",
                suffixes=("", "_selected"),
            )
            if "n_dicoms_selected" in feature_source.columns and "n_dicoms" not in feature_source.columns:
                feature_source = feature_source.rename(columns={"n_dicoms_selected": "n_dicoms"})
    numeric = [col for col in STUDY_METADATA_NUMERIC_CANDIDATES if col in feature_source.columns]
    merged = frame[["study_id_str"]].merge(feature_source[["study_id_str"] + numeric], on="study_id_str", how="left")
    x = merged[numeric].copy()
    if not numeric:
        return x, [], [], "no_supported_study_metadata_columns"
    return x, numeric, [], ""


def make_one_hot_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # pragma: no cover - older sklearn compatibility
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def build_preprocess(numeric_cols: list[str], categorical_cols: list[str], standardize: bool) -> ColumnTransformer:
    transformers: list[tuple[str, Pipeline, list[str]]] = []
    if numeric_cols:
        numeric_steps: list[tuple[str, Any]] = [("imputer", SimpleImputer(strategy="median"))]
        if standardize:
            numeric_steps.append(("scaler", StandardScaler()))
        transformers.append(("numeric", Pipeline(numeric_steps), numeric_cols))
    if categorical_cols:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", make_one_hot_encoder()),
                    ]
                ),
                categorical_cols,
            )
        )
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.0)


def fit_ridge_predictions(
    x: pd.DataFrame,
    y: np.ndarray,
    split: pd.Series,
    numeric_cols: list[str],
    categorical_cols: list[str],
    alphas: list[float],
    solver: str,
    standardize: bool,
    seed: int,
) -> tuple[np.ndarray, float, pd.DataFrame, list[dict[str, Any]]]:
    train_mask = split.eq("train").to_numpy()
    val_mask = split.eq("val").to_numpy()
    if not numeric_cols and not categorical_cols:
        raise ValueError("No feature columns available.")

    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    best_alpha = alphas[0]
    best_val_mae = math.inf
    best_pred: np.ndarray | None = None
    for alpha in alphas:
        model = Pipeline(
            [
                ("preprocess", build_preprocess(numeric_cols, categorical_cols, standardize)),
                ("ridge", Ridge(alpha=alpha, solver=solver, random_state=seed)),
            ]
        )
        with py_warnings.catch_warnings(record=True) as caught:
            py_warnings.simplefilter("always")
            model.fit(x.loc[train_mask, :], y[train_mask])
            pred = model.predict(x).astype(np.float32)
        warning_messages = [str(item.message) for item in caught]
        for item in caught:
            warnings.append({"alpha": float(alpha), "category": item.category.__name__, "message": str(item.message)})
        val_mae = float(mean_absolute_error(y[val_mask], pred[val_mask]))
        rows.append(
            {
                "alpha": float(alpha),
                "val_mae": val_mae,
                "warning_count": int(len(warning_messages)),
                "warning_messages": " | ".join(dict.fromkeys(warning_messages)),
            }
        )
        if val_mae < best_val_mae:
            best_alpha = alpha
            best_val_mae = val_mae
            best_pred = pred
    if best_pred is None:
        raise RuntimeError("No Ridge model was fit.")
    selection = pd.DataFrame(rows)
    selection["selected"] = selection["alpha"].eq(best_alpha)
    return best_pred, float(best_alpha), selection, warnings


def bootstrap_continuous_ci(
    frame: pd.DataFrame,
    target: str,
    pred_col: str,
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
    metrics: dict[str, list[float]] = {"mae": [], "rmse": [], "r2": [], "median_absolute_error": [], "bias_pred_minus_true": []}
    for _ in range(n_bootstrap):
        sampled_units = rng.choice(units, size=len(units), replace=True)
        sample = pd.concat([frame[frame[unit_col] == unit] for unit in sampled_units], ignore_index=True)
        y_true = sample["target_value"].to_numpy(dtype=np.float32)
        y_pred = sample[pred_col].to_numpy(dtype=np.float32)
        row = continuous_metrics(y_true, y_pred, target, "test", model_name, sample["subject_id_str"].nunique())
        for metric in metrics:
            value = row.get(metric)
            if value is not None and pd.notna(value):
                metrics[metric].append(float(value))
    out: list[dict[str, Any]] = []
    for metric, values in metrics.items():
        if values:
            arr = np.asarray(values, dtype=float)
            out.append(
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
    return out


def bootstrap_binary_ci(
    frame: pd.DataFrame,
    target: str,
    pred_col: str,
    model_name: str,
    bootstrap_unit: str,
    n_bootstrap: int,
    min_positives: int,
    seed: int,
) -> list[dict[str, Any]]:
    if n_bootstrap <= 0 or frame.empty:
        return []
    unit_col = "subject_id_str" if bootstrap_unit == "subject" else "study_id_str"
    units = frame[unit_col].drop_duplicates().to_numpy()
    if len(units) < 5:
        return []
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for threshold_label, threshold in TARGETS[target]["binary_thresholds"].items():
        true_abn = (frame["target_value"].to_numpy(dtype=float) < threshold).astype(int)
        n_pos = int(true_abn.sum())
        n_neg = int(len(true_abn) - n_pos)
        if n_pos < min_positives or n_neg < min_positives:
            rows.append(
                {
                    "target": target,
                    "model": model_name,
                    "threshold_label": threshold_label,
                    "threshold_value": float(threshold),
                    "metric": "auroc",
                    "status": "underpowered",
                    "n_positive": n_pos,
                    "n_negative": n_neg,
                    "n_bootstrap": int(n_bootstrap),
                    "mean": np.nan,
                    "ci_lower_2_5": np.nan,
                    "ci_upper_97_5": np.nan,
                }
            )
            continue
        values: dict[str, list[float]] = {"auroc": [], "average_precision": []}
        for _ in range(n_bootstrap):
            sampled_units = rng.choice(units, size=len(units), replace=True)
            sample = pd.concat([frame[frame[unit_col] == unit] for unit in sampled_units], ignore_index=True)
            sample_true = (sample["target_value"].to_numpy(dtype=float) < threshold).astype(int)
            if len(np.unique(sample_true)) < 2:
                continue
            score = -sample[pred_col].to_numpy(dtype=float)
            values["auroc"].append(float(roc_auc_score(sample_true, score)))
            values["average_precision"].append(float(average_precision_score(sample_true, score)))
        for metric, metric_values in values.items():
            if metric_values:
                arr = np.asarray(metric_values, dtype=float)
                rows.append(
                    {
                        "target": target,
                        "model": model_name,
                        "threshold_label": threshold_label,
                        "threshold_value": float(threshold),
                        "metric": metric,
                        "status": "ok",
                        "n_positive": n_pos,
                        "n_negative": n_neg,
                        "n_bootstrap": int(n_bootstrap),
                        "n_valid_bootstrap": int(len(arr)),
                        "mean": float(np.mean(arr)),
                        "ci_lower_2_5": float(np.percentile(arr, 2.5)),
                        "ci_upper_97_5": float(np.percentile(arr, 97.5)),
                    }
                )
    return rows


def read_imaging_metrics(output_root: Path | None) -> dict[tuple[str, str], dict[str, float]]:
    if output_root is None:
        return {}
    out: dict[tuple[str, str], dict[str, float]] = {}
    for target in ["lvot_vti", "tapse"]:
        path = output_root / target / "all_clips" / "imaging_baseline_metrics.csv"
        if not path.exists():
            continue
        metrics = pd.read_csv(path)
        rows = metrics[
            metrics.get("split", pd.Series(dtype=str)).astype(str).eq("test")
            & metrics.get("model", pd.Series(dtype=str)).astype(str).eq("ridge")
        ]
        if rows.empty:
            continue
        row = rows.iloc[0]
        out[(target, "all_clips_study_embeddings_stable_v2")] = {
            "imaging_ridge_mae": float(row["mae"]) if pd.notna(row.get("mae")) else np.nan,
            "imaging_ridge_rmse": float(row["rmse"]) if pd.notna(row.get("rmse")) else np.nan,
            "imaging_ridge_r2": float(row["r2"]) if pd.notna(row.get("r2")) else np.nan,
        }
    return out


def append_comparison(row: dict[str, Any], imaging: dict[tuple[str, str], dict[str, float]]) -> dict[str, Any]:
    comp = imaging.get((row["target"], "all_clips_study_embeddings_stable_v2"), {})
    out = dict(row)
    out.update(comp)
    if row.get("split") == "test" and "imaging_ridge_mae" in comp and pd.notna(row.get("mae")):
        out["delta_mae_vs_imaging_ridge"] = float(row["mae"]) - float(comp["imaging_ridge_mae"])
    if row.get("split") == "test" and "imaging_ridge_r2" in comp and pd.notna(row.get("r2")):
        out["delta_r2_vs_imaging_ridge"] = float(row["r2"]) - float(comp["imaging_ridge_r2"])
    return out


def add_metrics_for_predictions(
    rows: list[dict[str, Any]],
    binary_rows: list[dict[str, Any]],
    ci_rows: list[dict[str, Any]],
    binary_ci_rows: list[dict[str, Any]],
    frame: pd.DataFrame,
    target: str,
    baseline_tier: str,
    model_name: str,
    pred_col: str,
    selected_alpha: float | None,
    args: argparse.Namespace,
    imaging_metrics: dict[tuple[str, str], dict[str, float]],
) -> None:
    for split in ["train", "val", "test"]:
        subset = frame[frame["split"] == split]
        y_true = subset["target_value"].to_numpy(dtype=np.float32)
        y_pred = subset[pred_col].to_numpy(dtype=np.float32)
        metric = continuous_metrics(y_true, y_pred, target, split, model_name, subset["subject_id_str"].nunique())
        metric.update(
            {
                "baseline_tier": baseline_tier,
                "selected_alpha": selected_alpha if selected_alpha is not None else np.nan,
                "status": "ok",
                "skip_reason": "",
            }
        )
        rows.append(append_comparison(metric, imaging_metrics))
        for binary in binary_metrics(y_true, y_pred, target, split, model_name):
            binary.update({"baseline_tier": baseline_tier, "analysis_label": "phase2_nonimage_baseline"})
            binary_rows.append(binary)
    test = frame[frame["split"] == "test"].copy()
    ci_rows.extend(
        {
            **row,
            "baseline_tier": baseline_tier,
        }
        for row in bootstrap_continuous_ci(
            test,
            target,
            pred_col,
            model_name,
            args.bootstrap_unit,
            args.n_bootstrap,
            stable_seed(args.random_seed, target, baseline_tier, model_name, "bootstrap"),
        )
    )
    binary_ci_rows.extend(
        {
            **row,
            "baseline_tier": baseline_tier,
        }
        for row in bootstrap_binary_ci(
            test,
            target,
            pred_col,
            model_name,
            args.bootstrap_unit,
            args.binary_n_bootstrap,
            args.min_binary_positives,
            stable_seed(args.random_seed, target, baseline_tier, model_name, "binary_bootstrap"),
        )
    )


def run_feature_baseline(
    frame: pd.DataFrame,
    x: pd.DataFrame,
    numeric_cols: list[str],
    categorical_cols: list[str],
    target: str,
    baseline_tier: str,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, float, pd.DataFrame, list[dict[str, Any]], str]:
    counts = {split: int((frame["split"] == split).sum()) for split in ["train", "val", "test"]}
    if counts["train"] < args.min_train_n:
        return frame, np.nan, pd.DataFrame(), [], "insufficient_train"
    if counts["val"] < args.min_val_n:
        return frame, np.nan, pd.DataFrame(), [], "insufficient_val"
    if counts["test"] < args.min_test_n:
        return frame, np.nan, pd.DataFrame(), [], "insufficient_test"
    if not numeric_cols and not categorical_cols:
        return frame, np.nan, pd.DataFrame(), [], "no_features"
    y = frame["target_value"].to_numpy(dtype=np.float32)
    pred, alpha, selection, warnings = fit_ridge_predictions(
        x,
        y,
        frame["split"],
        numeric_cols,
        categorical_cols,
        parse_float_list(args.ridge_alphas),
        args.ridge_solver,
        bool(args.standardize_features),
        stable_seed(args.random_seed, target, baseline_tier, "ridge"),
    )
    out = frame.copy()
    out[f"pred_{baseline_tier}_ridge"] = pred
    return out, alpha, selection, warnings, ""


def main() -> int:
    args = parse_args()
    if not args.aggregate_only:
        raise RuntimeError("This script supports aggregate-only outputs only.")
    output_dir = resolve_output_dir(args)

    required = [args.structured_measurements_csv, args.study_embedding_manifest, args.subject_split_map_csv]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print(json.dumps({"blocked_for_missing_inputs": True, "missing": missing}, indent=2))
        return 2

    measures = pd.read_csv(args.structured_measurements_csv)
    study_manifest = load_study_manifest(args.study_embedding_manifest)
    splits, split_warnings = load_splits(args.subject_split_map_csv)
    selected_studies = read_optional_csv(args.selected_studies_csv)
    demographics = read_optional_csv(args.demographics_csv)
    targets = parse_csv_list(args.targets)
    baselines = parse_csv_list(args.baselines)
    if args.include_imaging_plus_demographics and "imaging_plus_demographics" not in baselines:
        baselines.append("imaging_plus_demographics")

    embeddings: np.ndarray | None = None
    embedding_manifest: pd.DataFrame | None = None
    embedding_index_col: str | None = None
    if "imaging_plus_demographics" in baselines:
        if not args.study_embedding_npz.exists():
            raise FileNotFoundError(f"Missing study embedding NPZ needed for imaging_plus_demographics: {args.study_embedding_npz}")
        embeddings, embedding_manifest, embedding_index_col = load_embeddings(args.study_embedding_npz, args.study_embedding_manifest)

    imaging_metrics = read_imaging_metrics(args.output_root)
    metric_rows: list[dict[str, Any]] = []
    binary_rows: list[dict[str, Any]] = []
    ci_rows: list[dict[str, Any]] = []
    binary_ci_rows: list[dict[str, Any]] = []
    alpha_rows: list[dict[str, Any]] = []
    summary_targets: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for target in targets:
        if target not in TARGETS:
            raise ValueError(f"Unsupported target: {target}")
        frame, target_summary, join_warnings = join_target_frame(
            target,
            measures,
            study_manifest,
            splits,
            bool(args.exclude_hard_extremes),
        )
        if split_warnings or join_warnings:
            warnings.append({"target": target, "warnings": split_warnings + join_warnings})

        train_mask = frame["split"].eq("train").to_numpy()
        train_median = float(np.median(frame.loc[train_mask, "target_value"].to_numpy(dtype=np.float32)))
        frame = frame.copy()
        frame["pred_null_median"] = train_median
        if "null" in baselines:
            add_metrics_for_predictions(
                metric_rows,
                binary_rows,
                ci_rows,
                binary_ci_rows,
                frame,
                target,
                "null",
                "null_median",
                "pred_null_median",
                None,
                args,
                imaging_metrics,
            )

        target_summary["baseline_summaries"] = []

        feature_specs: dict[str, tuple[pd.DataFrame, list[str], list[str], str]] = {}
        demo_x, demo_num, demo_cat, demo_skip = build_demographics_features(frame, demographics)
        study_x, study_num, study_cat, study_skip = build_study_metadata_features(frame, study_manifest, selected_studies)
        if "demographics" in baselines:
            feature_specs["demographics"] = (demo_x, demo_num, demo_cat, demo_skip)
        if "study_metadata" in baselines:
            feature_specs["study_metadata"] = (study_x, study_num, study_cat, study_skip)
        if "demographics_plus_study_metadata" in baselines:
            combined = pd.concat([demo_x, study_x], axis=1)
            feature_specs["demographics_plus_study_metadata"] = (
                combined,
                demo_num + study_num,
                demo_cat + study_cat,
                demo_skip if demo_skip else study_skip,
            )
        if "imaging_plus_demographics" in baselines:
            if embeddings is None or embedding_manifest is None or embedding_index_col is None:
                feature_specs["imaging_plus_demographics"] = (pd.DataFrame(index=frame.index), [], [], "missing_embedding_npz")
            else:
                emb_cols = ["study_id_str", embedding_index_col]
                emb_join = frame[["study_id_str"]].merge(
                    embedding_manifest[emb_cols].drop_duplicates("study_id_str"),
                    on="study_id_str",
                    how="left",
                )
                idx = pd.to_numeric(emb_join[embedding_index_col], errors="coerce")
                if idx.isna().any():
                    feature_specs["imaging_plus_demographics"] = (pd.DataFrame(index=frame.index), [], [], "missing_embedding_index")
                else:
                    emb_matrix = embeddings[idx.astype(int).to_numpy()]
                    emb_df = pd.DataFrame(emb_matrix, columns=[f"embedding_{i}" for i in range(emb_matrix.shape[1])])
                    combined = pd.concat([emb_df.reset_index(drop=True), demo_x.reset_index(drop=True)], axis=1)
                    feature_specs["imaging_plus_demographics"] = (
                        combined,
                        list(emb_df.columns) + demo_num,
                        demo_cat,
                        demo_skip,
                    )

        for tier, (x, numeric_cols, categorical_cols, skip_reason) in feature_specs.items():
            if skip_reason:
                target_summary["baseline_summaries"].append(
                    {
                        "baseline_tier": tier,
                        "status": "skipped",
                        "skip_reason": skip_reason,
                        "numeric_features": numeric_cols,
                        "categorical_features": categorical_cols,
                    }
                )
                warnings.append({"target": target, "baseline_tier": tier, "warning": skip_reason})
                continue
            model_frame, alpha, selection, ridge_warnings, model_skip = run_feature_baseline(
                frame,
                x,
                numeric_cols,
                categorical_cols,
                target,
                tier,
                args,
            )
            if model_skip:
                target_summary["baseline_summaries"].append(
                    {
                        "baseline_tier": tier,
                        "status": "skipped",
                        "skip_reason": model_skip,
                        "numeric_features": numeric_cols,
                        "categorical_features": categorical_cols,
                    }
                )
                warnings.append({"target": target, "baseline_tier": tier, "warning": model_skip})
                continue
            pred_col = f"pred_{tier}_ridge"
            add_metrics_for_predictions(
                metric_rows,
                binary_rows,
                ci_rows,
                binary_ci_rows,
                model_frame,
                target,
                tier,
                "ridge",
                pred_col,
                alpha,
                args,
                imaging_metrics,
            )
            selection.insert(0, "target", target)
            selection.insert(1, "baseline_tier", tier)
            selection.insert(2, "model", "ridge")
            alpha_rows.extend(selection.to_dict(orient="records"))
            target_summary["baseline_summaries"].append(
                {
                    "baseline_tier": tier,
                    "status": "ok",
                    "skip_reason": "",
                    "selected_alpha": alpha,
                    "numeric_features": numeric_cols,
                    "categorical_features": categorical_cols,
                    "ridge_warning_count": len(ridge_warnings),
                }
            )
            if ridge_warnings:
                warnings.append({"target": target, "baseline_tier": tier, "ridge_warnings": ridge_warnings})

        summary_targets.append(target_summary)

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "phase2_nonimage_baseline_metrics.csv"
    binary_path = output_dir / "phase2_nonimage_baseline_binary_metrics.csv"
    ci_path = output_dir / "phase2_nonimage_baseline_bootstrap_ci.csv"
    binary_ci_path = output_dir / "phase2_nonimage_baseline_binary_bootstrap_ci.csv"
    alpha_path = output_dir / "phase2_nonimage_baseline_alpha_selection.csv"
    summary_path = output_dir / "phase2_nonimage_baseline_summary.json"
    warnings_path = output_dir / "phase2_nonimage_baseline_warnings.json"

    pd.DataFrame(metric_rows).to_csv(metrics_path, index=False)
    pd.DataFrame(binary_rows).to_csv(binary_path, index=False)
    pd.DataFrame(ci_rows).to_csv(ci_path, index=False)
    pd.DataFrame(binary_ci_rows).to_csv(binary_ci_path, index=False)
    pd.DataFrame(alpha_rows).to_csv(alpha_path, index=False)

    warnings_payload = {
        "warnings": warnings,
        "patient_level_outputs_written": False,
        "row_level_outputs_written": False,
    }
    warnings_path.write_text(json.dumps(warnings_payload, indent=2))

    summary_payload = {
        "targets": targets,
        "baselines_requested": baselines,
        "structured_measurements_csv": str(args.structured_measurements_csv),
        "study_embedding_manifest": str(args.study_embedding_manifest),
        "subject_split_map_csv": str(args.subject_split_map_csv),
        "selected_studies_csv": str(args.selected_studies_csv) if args.selected_studies_csv else "",
        "demographics_csv": str(args.demographics_csv) if args.demographics_csv else "",
        "ridge_alphas": parse_float_list(args.ridge_alphas),
        "ridge_solver": args.ridge_solver,
        "features_standardized": bool(args.standardize_features),
        "n_bootstrap": int(args.n_bootstrap),
        "binary_n_bootstrap": int(args.binary_n_bootstrap),
        "bootstrap_unit": args.bootstrap_unit,
        "random_seed": int(args.random_seed),
        "patient_level_outputs_written": False,
        "row_level_outputs_written": False,
        "aggregate_outputs": [
            str(metrics_path),
            str(binary_path),
            str(ci_path),
            str(binary_ci_path),
            str(alpha_path),
            str(summary_path),
            str(warnings_path),
        ],
        "target_summaries": summary_targets,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2))

    print(json.dumps({
        "output_dir": str(output_dir),
        "metrics": str(metrics_path),
        "binary_metrics": str(binary_path),
        "bootstrap_ci": str(ci_path),
        "binary_bootstrap_ci": str(binary_ci_path),
        "alpha_selection": str(alpha_path),
        "summary": str(summary_path),
        "warnings": str(warnings_path),
        "patient_level_outputs_written": False,
        "row_level_outputs_written": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
