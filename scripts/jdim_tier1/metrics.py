"""Post-hoc reviewer metrics from unchanged saved predictions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from . import DEFAULT_BOOTSTRAP_N, DEFAULT_SEED, PROTOCOL_VERSION
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    assert_export_safe_frame,
    require_columns,
    safe_file_record,
    write_json,
    write_safe_csv,
)


PAIRED_NONIMAGE_UNAVAILABLE = "PAIRED_NONIMAGE_COMPARISON_UNAVAILABLE"
VALID_SPLITS = {"train", "val", "test"}
FORBIDDEN_ANALYSIS_OPTIONS = {
    "fit",
    "refit",
    "train_model",
    "ridge_alpha",
    "alpha_grid",
    "recalibrate_predictions",
    "optimize_threshold",
    "regenerate_predictions",
}


@dataclass
class FixedMetricResult:
    calibration: pd.DataFrame
    range_error: pd.DataFrame
    paired_delta_mae: pd.DataFrame
    tertile_boundaries: dict[str, Any]
    provenance: dict[str, Any]


def validate_prediction_file_schemas(
    imaging_sources: Mapping[str, Path],
    nonimage_sources: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    for target, path in imaging_sources.items():
        if not path.exists():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing imaging predictions for {target}")
        header = pd.read_csv(path, nrows=0)
        base = {"subject_id", "study_id", "split", "pred_null_median"}
        if not base.issubset(header.columns):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} imaging prediction schema missing {sorted(base - set(header.columns))}")
        if not {"target_value", "y_true"}.intersection(header.columns):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} imaging prediction schema lacks observed values")
        if not {"pred_ridge", "y_pred"}.intersection(header.columns):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} imaging prediction schema lacks Ridge predictions")
    for name, path in (nonimage_sources or {}).items():
        if not path.exists():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing nonimage predictions for {name}")
        header = pd.read_csv(path, nrows=0)
        base = {"target", "subject_id", "study_id", "split"}
        if not base.issubset(header.columns):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{name} prediction schema missing {sorted(base - set(header.columns))}")
        if not {"target_value", "y_true"}.intersection(header.columns):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{name} prediction schema lacks observed values")
        prediction_columns = {"y_pred", "prediction", "pred_ridge"}.intersection(header.columns)
        prediction_columns.update(column for column in header.columns if column.startswith("pred_"))
        if not prediction_columns:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{name} prediction schema lacks comparator predictions")
    return {
        "status": "ok",
        "mode": "schema_only",
        "imaging_targets_checked": sorted(imaging_sources),
        "nonimage_comparators_checked": sorted(nonimage_sources or {}),
        "row_level_metrics_computed": False,
        "model_refit": False,
    }


def validate_fixed_prediction_request(options: Mapping[str, Any] | None = None) -> None:
    options = options or {}
    attempted = sorted(key for key, value in options.items() if key in FORBIDDEN_ANALYSIS_OPTIONS and value)
    if attempted:
        raise ValueError(f"Fixed-prediction analysis forbids model/prediction changes: {attempted}")


def _stable_seed(seed: int, *parts: str) -> int:
    digest = hashlib.sha256("::".join([str(seed), *parts]).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def normalize_imaging_predictions(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    aliases = {}
    if "target_value" not in frame.columns and "y_true" in frame.columns:
        aliases["y_true"] = "target_value"
    if "pred_ridge" not in frame.columns and "y_pred" in frame.columns:
        aliases["y_pred"] = "pred_ridge"
    out = frame.rename(columns=aliases).copy()
    require_columns(
        out,
        ["subject_id", "study_id", "split", "target_value", "pred_ridge", "pred_null_median"],
        f"{target} imaging predictions",
    )
    if "target" in out.columns:
        observed_targets = set(out["target"].dropna().astype(str))
        if observed_targets != {target}:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} prediction file contains targets {sorted(observed_targets)}")
    out["target"] = target
    out["subject_id"] = out["subject_id"].astype(str)
    out["study_id"] = out["study_id"].astype(str)
    out["split"] = out["split"].astype(str).str.lower()
    invalid_splits = sorted(set(out["split"]) - VALID_SPLITS)
    if invalid_splits:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} predictions contain invalid splits: {invalid_splits}")
    for column in ("target_value", "pred_ridge", "pred_null_median"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
        if out[column].isna().any():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} predictions contain missing/non-numeric {column}")
    duplicate_studies = int(out.duplicated(["target", "study_id"]).sum())
    if duplicate_studies:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} imaging predictions duplicate {duplicate_studies} study rows")
    subject_conflicts = out.groupby("study_id")["subject_id"].nunique()
    split_conflicts = out.groupby("subject_id")["split"].nunique()
    if int((subject_conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} predictions conflict on study-to-subject mapping")
    if int((split_conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} predictions contain subject split overlap")
    return out.reset_index(drop=True)


def normalize_comparator_predictions(frame: pd.DataFrame, comparator: str) -> pd.DataFrame:
    out = frame.copy()
    aliases = {}
    if "target_value" not in out.columns and "y_true" in out.columns:
        aliases["y_true"] = "target_value"
    if "y_pred" in out.columns:
        aliases["y_pred"] = "comparator_prediction"
    elif "prediction" in out.columns:
        aliases["prediction"] = "comparator_prediction"
    elif "pred_ridge" in out.columns:
        aliases["pred_ridge"] = "comparator_prediction"
    else:
        candidate_columns = [column for column in out.columns if column.startswith("pred_")]
        if len(candidate_columns) == 1:
            aliases[candidate_columns[0]] = "comparator_prediction"
    out = out.rename(columns=aliases)
    require_columns(
        out,
        ["target", "subject_id", "study_id", "split", "target_value", "comparator_prediction"],
        f"{comparator} predictions",
    )
    out["target"] = out["target"].astype(str)
    out["subject_id"] = out["subject_id"].astype(str)
    out["study_id"] = out["study_id"].astype(str)
    out["split"] = out["split"].astype(str).str.lower()
    out["target_value"] = pd.to_numeric(out["target_value"], errors="coerce")
    out["comparator_prediction"] = pd.to_numeric(out["comparator_prediction"], errors="coerce")
    if out[["target_value", "comparator_prediction"]].isna().any().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{comparator} predictions contain missing numeric values")
    return out


def calibration_parameters(observed: np.ndarray, predicted: np.ndarray) -> tuple[float, float]:
    if len(observed) < 3 or np.allclose(np.std(predicted), 0.0):
        return np.nan, np.nan
    design = np.column_stack([np.ones(len(predicted)), predicted.astype(float)])
    intercept, slope = np.linalg.lstsq(design, observed.astype(float), rcond=None)[0]
    return float(intercept), float(slope)


def _subject_groups(frame: pd.DataFrame) -> tuple[np.ndarray, dict[str, pd.DataFrame]]:
    subjects = frame["subject_id"].drop_duplicates().to_numpy()
    groups = {subject: group.copy() for subject, group in frame.groupby("subject_id", sort=False)}
    return subjects, groups


def subject_bootstrap(
    frame: pd.DataFrame,
    metric: Callable[[pd.DataFrame], Sequence[float]],
    n_bootstrap: int,
    seed: int,
) -> np.ndarray:
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive")
    subjects, groups = _subject_groups(frame)
    if len(subjects) < 2:
        return np.empty((0, 0), dtype=float)
    rng = np.random.default_rng(seed)
    rows: list[np.ndarray] = []
    for _ in range(n_bootstrap):
        sampled = rng.choice(subjects, size=len(subjects), replace=True)
        sample = pd.concat([groups[subject] for subject in sampled], ignore_index=True)
        values = np.asarray(metric(sample), dtype=float)
        if values.ndim == 0:
            values = values.reshape(1)
        if np.all(np.isfinite(values)):
            rows.append(values)
    if not rows:
        return np.empty((0, 0), dtype=float)
    return np.stack(rows, axis=0)


def percentile_ci(values: np.ndarray, column: int = 0) -> tuple[float, float]:
    if values.size == 0:
        return np.nan, np.nan
    low, high = np.percentile(values[:, column], [2.5, 97.5])
    return float(low), float(high)


def calibration_summary(
    frame: pd.DataFrame,
    target: str,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    test = frame[frame["split"] == "test"].copy()
    intercept, slope = calibration_parameters(
        test["target_value"].to_numpy(),
        test["pred_ridge"].to_numpy(),
    )
    boot = subject_bootstrap(
        test,
        lambda sample: calibration_parameters(
            sample["target_value"].to_numpy(),
            sample["pred_ridge"].to_numpy(),
        ),
        n_bootstrap,
        _stable_seed(seed, target, "calibration"),
    )
    intercept_low, intercept_high = percentile_ci(boot, 0)
    slope_low, slope_high = percentile_ci(boot, 1)
    return {
        "target": target,
        "model_name": "imaging_ridge",
        "definition": "observed_equals_intercept_plus_slope_times_predicted",
        "n_studies": int(len(test)),
        "n_subjects": int(test["subject_id"].nunique()),
        "calibration_intercept": intercept,
        "calibration_intercept_ci_low": intercept_low,
        "calibration_intercept_ci_high": intercept_high,
        "calibration_slope": slope,
        "calibration_slope_ci_low": slope_low,
        "calibration_slope_ci_high": slope_high,
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_valid_n": int(len(boot)),
        "bootstrap_seed": int(seed),
    }


def derive_training_tertiles(frame: pd.DataFrame, source_split: str = "train") -> dict[str, Any]:
    if source_split != "train":
        raise ValueError("Tertile boundaries must be derived from the training split only")
    train = frame[frame["split"] == "train"].copy()
    if train.empty:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "training labels unavailable for tertile derivation")
    lower, upper = np.quantile(train["target_value"].to_numpy(dtype=float), [1 / 3, 2 / 3], method="linear")
    if not np.isfinite(lower) or not np.isfinite(upper) or lower >= upper:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "training-label tertile boundaries are not distinct")
    return {
        "source_split": "train",
        "quantile_method": "numpy_linear",
        "lower_boundary": float(lower),
        "upper_boundary": float(upper),
        "n_training_studies": int(len(train)),
        "n_training_subjects": int(train["subject_id"].nunique()),
    }


def _assign_tertiles(values: pd.Series, lower: float, upper: float) -> pd.Series:
    return pd.cut(
        values,
        bins=[-np.inf, lower, upper, np.inf],
        labels=[
            "lower_training_distribution_tertile",
            "middle_training_distribution_tertile",
            "upper_training_distribution_tertile",
        ],
        include_lowest=True,
        right=True,
    ).astype(str)


def _error_metrics(frame: pd.DataFrame, prediction_column: str = "pred_ridge") -> tuple[float, float, float]:
    error = frame[prediction_column].to_numpy(dtype=float) - frame["target_value"].to_numpy(dtype=float)
    return (
        float(np.mean(np.abs(error))),
        float(np.sqrt(np.mean(np.square(error)))),
        float(np.mean(error)),
    )


def range_error_summaries(
    frame: pd.DataFrame,
    target: str,
    boundaries: Mapping[str, Any],
    n_bootstrap: int,
    seed: int,
) -> list[dict[str, Any]]:
    test = frame[frame["split"] == "test"].copy()
    test["range_group"] = _assign_tertiles(
        test["target_value"],
        float(boundaries["lower_boundary"]),
        float(boundaries["upper_boundary"]),
    )
    rows: list[dict[str, Any]] = []
    for group_name, group in test.groupby("range_group", sort=False):
        mae, rmse, bias = _error_metrics(group)
        boot = subject_bootstrap(
            group,
            lambda sample: (_error_metrics(sample)[0], _error_metrics(sample)[2]),
            n_bootstrap,
            _stable_seed(seed, target, group_name, "range_error"),
        )
        mae_low, mae_high = percentile_ci(boot, 0)
        bias_low, bias_high = percentile_ci(boot, 1)
        rows.append(
            {
                "target": target,
                "model_name": "imaging_ridge",
                "range_group": group_name,
                "range_definition": "training_distribution_tertiles",
                "n_studies": int(len(group)),
                "n_subjects": int(group["subject_id"].nunique()),
                "mae": mae,
                "mae_ci_low": mae_low,
                "mae_ci_high": mae_high,
                "rmse": rmse,
                "bias_prediction_minus_observed": bias,
                "bias_ci_low": bias_low,
                "bias_ci_high": bias_high,
                "bootstrap_n": int(n_bootstrap),
                "bootstrap_valid_n": int(len(boot)),
                "bootstrap_seed": int(seed),
            }
        )
    return rows


def paired_delta_mae(
    frame: pd.DataFrame,
    comparator_column: str,
    comparator_name: str,
    target: str,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    test = frame[frame["split"] == "test"].copy()
    require_columns(test, [comparator_column], f"{target} paired comparator")

    def metric(sample: pd.DataFrame) -> tuple[float]:
        observed = sample["target_value"].to_numpy(dtype=float)
        imaging_mae = float(np.mean(np.abs(sample["pred_ridge"].to_numpy(dtype=float) - observed)))
        comparator_mae = float(np.mean(np.abs(sample[comparator_column].to_numpy(dtype=float) - observed)))
        return (comparator_mae - imaging_mae,)

    point = metric(test)[0]
    boot = subject_bootstrap(
        test,
        metric,
        n_bootstrap,
        _stable_seed(seed, target, comparator_name, "delta_mae"),
    )
    low, high = percentile_ci(boot, 0)
    return {
        "target": target,
        "comparator": comparator_name,
        "status": "ok",
        "reason": "",
        "n_studies": int(len(test)),
        "n_subjects": int(test["subject_id"].nunique()),
        "delta_mae_comparator_minus_imaging": point,
        "delta_mae_ci_low": low,
        "delta_mae_ci_high": high,
        "positive_value_favors": "imaging_ridge",
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_valid_n": int(len(boot)),
        "bootstrap_seed": int(seed),
    }


def strict_pair_nonimage(
    imaging: pd.DataFrame,
    comparator: pd.DataFrame,
    target: str,
) -> tuple[pd.DataFrame | None, str]:
    imaging_test = imaging[imaging["split"] == "test"].copy()
    comparator_test = comparator[
        (comparator["target"] == target) & (comparator["split"] == "test")
    ].copy()
    key = ["target", "study_id"]
    if imaging_test.duplicated(key).any():
        return None, "duplicate_imaging_study_keys"
    if comparator_test.duplicated(key).any():
        return None, "duplicate_comparator_study_keys"
    imaging_keys = set(map(tuple, imaging_test[key].itertuples(index=False, name=None)))
    comparator_keys = set(map(tuple, comparator_test[key].itertuples(index=False, name=None)))
    if imaging_keys != comparator_keys:
        return None, f"study_key_mismatch_count={len(imaging_keys.symmetric_difference(comparator_keys))}"
    merged = imaging_test.merge(
        comparator_test[key + ["subject_id", "split", "target_value", "comparator_prediction"]],
        on=key,
        how="inner",
        validate="one_to_one",
        suffixes=("_imaging", "_comparator"),
    )
    if len(merged) != len(imaging_test):
        return None, "silent_row_drop_detected"
    if not merged["subject_id_imaging"].equals(merged["subject_id_comparator"]):
        return None, "subject_assignment_mismatch"
    if not merged["split_imaging"].equals(merged["split_comparator"]):
        return None, "split_assignment_mismatch"
    if not np.array_equal(
        merged["target_value_imaging"].to_numpy(dtype=float),
        merged["target_value_comparator"].to_numpy(dtype=float),
    ):
        return None, "target_value_mismatch"
    paired = pd.DataFrame(
        {
            "target": merged["target"],
            "study_id": merged["study_id"],
            "subject_id": merged["subject_id_imaging"],
            "split": merged["split_imaging"],
            "target_value": merged["target_value_imaging"],
            "pred_ridge": merged["pred_ridge"],
            "comparator_prediction": merged["comparator_prediction"],
        }
    )
    return paired, ""


def unavailable_delta_row(target: str, comparator: str, reason: str, n_bootstrap: int, seed: int) -> dict[str, Any]:
    return {
        "target": target,
        "comparator": comparator,
        "status": PAIRED_NONIMAGE_UNAVAILABLE,
        "reason": reason,
        "n_studies": 0,
        "n_subjects": 0,
        "delta_mae_comparator_minus_imaging": np.nan,
        "delta_mae_ci_low": np.nan,
        "delta_mae_ci_high": np.nan,
        "positive_value_favors": "imaging_ridge",
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_valid_n": 0,
        "bootstrap_seed": int(seed),
    }


def compute_fixed_prediction_metrics(
    imaging_predictions: Mapping[str, pd.DataFrame],
    imaging_sources: Mapping[str, Path] | None = None,
    nonimage_predictions: Mapping[str, pd.DataFrame] | None = None,
    nonimage_sources: Mapping[str, Path] | None = None,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_N,
    seed: int = DEFAULT_SEED,
    analysis_options: Mapping[str, Any] | None = None,
) -> FixedMetricResult:
    validate_fixed_prediction_request(analysis_options)
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive")
    normalized = {
        target: normalize_imaging_predictions(frame, target)
        for target, frame in imaging_predictions.items()
    }
    calibration_rows: list[dict[str, Any]] = []
    range_rows: list[dict[str, Any]] = []
    delta_rows: list[dict[str, Any]] = []
    boundaries_payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "source_split": "train",
        "test_labels_used_to_define_boundaries": False,
        "targets": {},
    }

    for target, frame in normalized.items():
        calibration_rows.append(calibration_summary(frame, target, n_bootstrap, seed))
        boundaries = derive_training_tertiles(frame, source_split="train")
        if imaging_sources and target in imaging_sources:
            boundaries["source_prediction_file_sha256"] = safe_file_record(
                f"imaging_predictions_{target}", imaging_sources[target]
            )["sha256"]
        boundaries_payload["targets"][target] = boundaries
        range_rows.extend(range_error_summaries(frame, target, boundaries, n_bootstrap, seed))
        delta_rows.append(
            paired_delta_mae(
                frame,
                "pred_null_median",
                "null_median",
                target,
                n_bootstrap,
                seed,
            )
        )

    normalized_nonimage: dict[str, pd.DataFrame] = {}
    for name, frame in (nonimage_predictions or {}).items():
        normalized_nonimage[name] = normalize_comparator_predictions(frame, name)
        for target, imaging in normalized.items():
            paired, reason = strict_pair_nonimage(imaging, normalized_nonimage[name], target)
            if paired is None:
                delta_rows.append(unavailable_delta_row(target, name, reason, n_bootstrap, seed))
                continue
            delta_rows.append(
                paired_delta_mae(
                    paired,
                    "comparator_prediction",
                    name,
                    target,
                    n_bootstrap,
                    seed,
                )
            )

    calibration = pd.DataFrame(calibration_rows)
    range_error = pd.DataFrame(range_rows)
    delta = pd.DataFrame(delta_rows)
    for label, frame in (
        ("calibration metrics", calibration),
        ("range error metrics", range_error),
        ("paired delta MAE", delta),
    ):
        assert_export_safe_frame(frame, label)

    input_records: list[dict[str, Any]] = []
    for target, path in (imaging_sources or {}).items():
        input_records.append(safe_file_record(f"imaging_predictions_{target}", path, len(normalized[target])))
    for name, path in (nonimage_sources or {}).items():
        input_records.append(safe_file_record(f"nonimage_predictions_{name}", path, len(normalized_nonimage[name])))
    provenance = {
        "protocol_version": PROTOCOL_VERSION,
        "analysis_type": "fixed_saved_predictions_only",
        "model_refit": False,
        "prediction_regeneration": False,
        "prediction_recalibration": False,
        "threshold_optimization": False,
        "bootstrap_unit": "subject",
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_seed": int(seed),
        "delta_mae_definition": "mae_comparator_minus_mae_imaging_ridge",
        "positive_delta_favors": "imaging_ridge",
        "input_files": input_records,
    }
    return FixedMetricResult(calibration, range_error, delta, boundaries_payload, provenance)


def write_fixed_metric_outputs(result: FixedMetricResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_safe_csv(output_dir / "continuous_calibration_metrics.csv", result.calibration, "calibration metrics")
    write_safe_csv(output_dir / "training_tertile_test_error.csv", result.range_error, "range error metrics")
    write_safe_csv(output_dir / "paired_delta_mae.csv", result.paired_delta_mae, "paired delta MAE")
    write_json(output_dir / "training_tertile_boundaries.json", result.tertile_boundaries)
    write_json(output_dir / "fixed_prediction_metrics_provenance.json", result.provenance)
