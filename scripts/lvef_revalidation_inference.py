"""Metrics and paired inference for fixed, released ASA models.

No fitting of prediction models, test access, panel selection or file I/O occurs
here. Probability calibration fits below estimate descriptive test calibration
parameters only; they never replace the frozen probabilities being evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
import warnings

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score, roc_auc_score

from lvef_revalidation_analysis import EVALUATION_CONDITIONS, MODALITIES, SEED, require

CONTRASTS = (("early_fusion", "vision_only"), ("early_fusion", "structured_only"), ("structured_only", "vision_only"))
CORE_CLAIMS = (
    "lvef_mae_early_fusion_minus_vision_only",
    "lvef_mae_early_fusion_minus_structured_only",
    "locked_strict_panel_mean_normalized_mae_early_fusion_minus_vision_only",
    "locked_strict_panel_mean_normalized_mae_early_fusion_minus_structured_only",
)


def metric_orientation(metric: str) -> str:
    if metric.endswith(("mae", "rmse", "brier_score")):
        return "lower_is_better"
    if metric.endswith("calibration_slope"):
        return "descriptive_ideal_one"
    if metric.endswith(("calibration_intercept", "mean_signed_error")):
        return "descriptive_ideal_zero"
    if metric == "prevalence" or metric.endswith("_prevalence"):
        return "descriptive_no_favorable_direction"
    return "higher_is_better"


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def regression_metrics(y: Sequence[float], predicted: Sequence[float], *, train_iqr: float, lvef: bool = False) -> dict[str, Any]:
    y, p = np.asarray(y, float), np.asarray(predicted, float)
    require(y.ndim == 1 and y.shape == p.shape and len(y) > 0 and np.isfinite(y).all()
            and np.isfinite(p).all() and np.isfinite(train_iqr) and train_iqr > 0, "REGRESSION_METRIC_INPUT_INVALID")
    error, variance = p - y, float(np.var(y))
    pvar = float(np.var(p))
    covariance = float(np.mean((y - y.mean()) * (p - p.mean())))
    pearson = covariance / np.sqrt(variance * pvar) if variance > 0 and pvar > 0 else np.nan
    yr, pr = rankdata(y), rankdata(p)
    spearman = float(np.corrcoef(yr, pr)[0, 1]) if np.std(yr) > 0 and np.std(pr) > 0 else np.nan
    slope = covariance / pvar if pvar > 0 else np.nan
    mae = float(np.mean(np.abs(error)))
    result = {"n": len(y), "mae": mae, "normalized_mae": mae / train_iqr,
        "rmse": float(np.sqrt(np.mean(error ** 2))), "mean_signed_error": float(error.mean()),
        "r2": _finite(1 - np.mean(error ** 2) / variance) if variance > 0 else None,
        "pearson_correlation": _finite(pearson), "spearman_correlation": _finite(spearman),
        "calibration_slope": _finite(slope), "calibration_intercept": _finite(y.mean() - slope * p.mean())}
    if lvef:
        result["tolerance_coverage"] = {str(v): float(np.mean(np.abs(error) <= v)) for v in (4., 5., 8.)}
        result["threshold_bands"] = {}
        for lo, hi in ((35., 45.), (37., 43.)):
            inside = (y >= lo) & (y <= hi)
            result["threshold_bands"][f"{lo:g}_{hi:g}"] = {
                label: {"n": int(mask.sum()), "mae": float(np.mean(np.abs(error[mask]))) if mask.any() else None}
                for label, mask in (("inside", inside), ("outside", ~inside))}
    result["undefined_metrics"] = [key for key, value in result.items() if value is None]
    return result


def _probability_calibration(y: np.ndarray, probabilities: np.ndarray) -> tuple[float | None, float | None]:
    if set(y) != {0, 1} or np.any((probabilities <= 0) | (probabilities >= 1)):
        return None, None
    z = logit(probabilities)
    if np.std(z) == 0:
        return None, None
    x = np.column_stack((np.ones(len(z)), z))
    def objective(beta):
        eta = x @ beta
        return float(np.mean(np.logaddexp(0, eta) - y * eta)), x.T @ (expit(eta) - y) / len(y)
    fit = minimize(objective, np.array([0., 1.]), jac=True, method="BFGS", options={"gtol": 1e-8, "maxiter": 10000})
    if not np.isfinite(fit.x).all() or np.max(np.abs(objective(fit.x)[1])) > 1e-6 or np.max(np.abs(fit.x)) >= 1e4:
        return None, None
    return float(fit.x[0]), float(fit.x[1])


def binary_metrics(y: Sequence[int], score: Sequence[float], probabilities: Sequence[float] | None,
                   *, cutoff: float | None = None, called: Sequence[int] | None = None) -> dict[str, Any]:
    require(set(np.asarray(y)) <= {0, 1}, "BINARY_METRIC_INPUT_INVALID")
    y, score = np.asarray(y, int), np.asarray(score, float)
    require(y.shape == score.shape and y.ndim == 1 and len(y) > 0 and set(y) <= {0, 1}
            and np.isfinite(score).all(), "BINARY_METRIC_INPUT_INVALID")
    if called is None:
        require(probabilities is not None and cutoff is not None, "BINARY_OPERATING_POINT_MISSING")
        called = np.asarray(probabilities) >= cutoff
    called = np.asarray(called, int)
    require(called.shape == y.shape and set(called) <= {0, 1}, "BINARY_CALL_INVALID")
    tp, tn = int(((y == 1) & (called == 1)).sum()), int(((y == 0) & (called == 0)).sum())
    fp, fn = int(((y == 0) & (called == 1)).sum()), int(((y == 1) & (called == 0)).sum())
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    sensitivity, specificity = ratio(tp, tp + fn), ratio(tn, tn + fp)
    both = set(y) == {0, 1}
    result = {"n": len(y), "events": int(y.sum()), "nonevents": int(len(y) - y.sum()),
        "prevalence": float(y.mean()), "auroc": float(roc_auc_score(y, score)) if both else None,
        "average_precision": float(average_precision_score(y, score)) if both else None,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn, "sensitivity": sensitivity, "specificity": specificity,
        "ppv": ratio(tp, tp + fp), "npv": ratio(tn, tn + fn), "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "balanced_accuracy": (sensitivity + specificity) / 2 if both else None}
    if probabilities is not None:
        p = np.asarray(probabilities, float)
        require(p.shape == y.shape and np.isfinite(p).all() and np.all((p >= 0) & (p <= 1)), "BINARY_PROBABILITY_INVALID")
        intercept, slope = _probability_calibration(y, p)
        result.update(calibrated_brier_score=float(np.mean((p - y) ** 2)), calibration_intercept=intercept, calibration_slope=slope)
        result["calibration_bins"] = [{"lower": j / 10, "upper": (j + 1) / 10,
            "n": int(((p >= j / 10) & ((p < (j + 1) / 10) if j < 9 else (p <= 1))).sum()),
            "mean_probability": float(p[m].mean()) if m.any() else None,
            "observed_fraction": float(y[m].mean()) if m.any() else None}
            for j in range(10) for m in [(p >= j / 10) & ((p < (j + 1) / 10) if j < 9 else (p <= 1))]]
    result["undefined_metrics"] = [key for key, value in result.items() if value is None]
    return result


def paired_summary(observed: float, replicates: Sequence[float]) -> dict[str, Any]:
    values = np.asarray(replicates, float)
    valid = values[np.isfinite(values)]
    require(np.isfinite(observed) and values.ndim == 1 and len(values) > 0, "PAIRED_EFFECT_INVALID")
    return {"effect": float(observed), "interval": [float(v) for v in np.percentile(valid, [2.5, 97.5])] if len(valid) else None,
        "p_value": float(min(1., (1 + np.count_nonzero(np.abs(valid - observed) >= abs(observed))) / (len(valid) + 1))) if len(valid) else None,
        "valid_replicates": len(valid), "undefined_replicates": int(len(values) - len(valid)),
        "undefined_frequency": float(1 - len(valid) / len(values)), "replicates": len(values),
        "interval_method": "percentile_95", "p_value_method": "null_centered_two_sided_plus_one"}


def model_interval(observed: float, replicates: Sequence[float]) -> dict[str, Any]:
    """Descriptive model interval; deliberately carries no superiority p-value."""
    values = np.asarray(replicates, float)
    valid = values[np.isfinite(values)]
    return {"estimate": _finite(observed),
        "interval": [float(v) for v in np.percentile(valid, [2.5, 97.5])] if len(valid) and np.isfinite(observed) else None,
        "valid_replicates": len(valid), "undefined_replicates": len(values) - len(valid),
        "undefined_frequency": (len(values) - len(valid)) / len(values), "replicates": len(values),
        "interval_method": "percentile_95", "supports_paired_superiority_claim": False}


def holm(p_values: Mapping[str, float]) -> dict[str, float]:
    require(p_values and all(type(v) in {int, float} and np.isfinite(v) and 0 <= v <= 1 for v in p_values.values()), "HOLM_PVALUE_UNDEFINED")
    order = sorted(p_values, key=lambda k: (p_values[k], k))
    adjusted, prior = {}, 0.
    for i, key in enumerate(order):
        prior = max(prior, (len(order) - i) * p_values[key])
        adjusted[key] = min(1., prior)
    return adjusted


def core_holm(p_values: Mapping[str, float], *, strict_panel: Sequence[str], strict_panel_locked: bool,
              construct: str = "strict") -> dict[str, Any]:
    require(strict_panel_locked is True and len(strict_panel) > 0 and len(set(strict_panel)) == len(strict_panel)
            and "lvef" not in strict_panel and construct == "strict", "CORE_REQUIRES_LOCKED_STRICT_PANEL")
    require(set(p_values) == set(CORE_CLAIMS), "CORE_REQUIRES_ALL_FOUR_CLAIMS")
    return {"family": "core_global_four_claim", "alpha": .05, "method": "holm_two_sided",
            "adjusted_p_values": holm(p_values), "strict_target_count": len(strict_panel)}


def benjamini_hochberg(p_values: Mapping[str, float]) -> dict[str, float]:
    require(p_values and all(type(v) in {int, float} and np.isfinite(v) and 0 <= v <= 1 for v in p_values.values()), "FDR_PVALUE_UNDEFINED")
    order, adjusted, prior = sorted(p_values, key=lambda k: (p_values[k], k)), {}, 1.
    for i in range(len(order) - 1, -1, -1):
        key = order[i]
        prior = min(prior, p_values[key] * len(order) / (i + 1))
        adjusted[key] = prior
    return adjusted


@dataclass(frozen=True)
class TaskPredictions:
    subjects: tuple[str, ...]
    labels: np.ndarray
    predictions: Mapping[str, np.ndarray]
    train_iqr: float


def subject_multiplicities(roster: Sequence[str], *, replicates: int = 10000, seed: int = SEED) -> np.ndarray:
    require(len(roster) == len(set(roster)) > 0 and type(replicates) is int and replicates > 0, "BOOTSTRAP_ROSTER_INVALID")
    return np.random.default_rng(seed).multinomial(len(roster), np.full(len(roster), 1 / len(roster)), size=replicates)


def _validate_task(task: TaskPredictions, roster: Sequence[str]) -> None:
    require(len(task.subjects) == len(set(task.subjects)) == len(task.labels) > 0
            and set(task.subjects) <= set(roster) and set(task.predictions) == set(MODALITIES)
            and np.isfinite(task.labels).all() and np.isfinite(task.train_iqr) and task.train_iqr > 0,
            "BOOTSTRAP_TASK_INVALID")
    require(all(np.asarray(p).shape == np.asarray(task.labels).shape and np.isfinite(p).all()
                for p in task.predictions.values()), "BOOTSTRAP_PREDICTIONS_INVALID")


def panel_bootstrap_from_multiplicities(tasks: Mapping[str, TaskPredictions], *, locked_panel: Sequence[str],
                                      roster: Sequence[str], multiplicities: np.ndarray) -> dict[str, Any]:
    """Lower-level deterministic seam; callers may supply explicit draws for testing."""
    require(set(tasks) == set(locked_panel) and len(locked_panel) == len(set(locked_panel)) > 0
            and "lvef" not in locked_panel, "BOOTSTRAP_LOCKED_PANEL_MISMATCH")
    weights = np.asarray(multiplicities)
    require(weights.ndim == 2 and weights.shape[1] == len(roster) and len(set(roster)) == len(roster)
            and np.issubdtype(weights.dtype, np.integer) and np.all(weights >= 0)
            and np.all(weights.sum(axis=1) == len(roster)), "BOOTSTRAP_MULTIPLICITIES_INVALID")
    index = {s: i for i, s in enumerate(roster)}
    observed, task_draws, task_effects = {}, {}, {}
    for name in locked_panel:
        task = tasks[name]
        _validate_task(task, roster)
        tw = weights[:, [index[s] for s in task.subjects]]
        denominator = tw.sum(axis=1)
        observed[name], task_draws[name], task_effects[name] = {}, {}, {}
        for modality in MODALITIES:
            errors = np.abs(np.asarray(task.predictions[modality]) - task.labels)
            observed[name][modality] = float(errors.mean())
            task_draws[name][modality] = np.divide(tw @ errors, denominator,
                out=np.full(len(weights), np.nan), where=denominator > 0)
        for left, right in CONTRASTS:
            key = f"{left}_minus_{right}"
            task_effects[name][key] = paired_summary(observed[name][left] - observed[name][right], task_draws[name][left] - task_draws[name][right])
    macro_draws, macro_observed = {}, {}
    for modality in MODALITIES:
        # np.mean deliberately propagates NaN from ANY locked task; never nanmean.
        macro_draws[modality] = np.mean(np.stack([task_draws[name][modality] / tasks[name].train_iqr for name in locked_panel]), axis=0)
        macro_observed[modality] = float(np.mean([observed[name][modality] / tasks[name].train_iqr for name in locked_panel]))
    effects = {f"{left}_minus_{right}": paired_summary(macro_observed[left] - macro_observed[right], macro_draws[left] - macro_draws[right]) for left, right in CONTRASTS}
    return {"task_native_mae_contrasts": task_effects, "macro_normalized_mae": macro_observed,
            "macro_contrasts": effects, "locked_task_count": len(locked_panel), "roster_subjects": len(roster),
            "effect_orientation": "named_left_modality_minus_named_right_modality", "metric_direction": "lower_is_better",
            "shared_subject_multiplicities": True, "undefined_task_invalidates_macro": True}


def paired_metric_bootstrap(task: TaskPredictions, *, metric: Callable[[np.ndarray, np.ndarray], float],
                            multiplicities: np.ndarray | None = None, binary: bool = False) -> dict[str, Any]:
    """Generic paired intervals for any fixed metric; undefined values stay visible."""
    _validate_task(task, task.subjects)
    weights = subject_multiplicities(task.subjects) if multiplicities is None else np.asarray(multiplicities)
    require(weights.ndim == 2 and weights.shape[1] == len(task.subjects)
            and np.issubdtype(weights.dtype, np.integer) and np.all(weights >= 0)
            and np.all(weights.sum(axis=1) == len(task.subjects)), "BOOTSTRAP_MULTIPLICITIES_INVALID")
    observed = {m: float(metric(task.labels, np.asarray(task.predictions[m]))) for m in MODALITIES}
    draws = {m: np.full(len(weights), np.nan) for m in MODALITIES}
    one_class = 0
    for i, w in enumerate(weights):
        indices = np.repeat(np.arange(len(w)), w)
        y = task.labels[indices]
        if binary and len(np.unique(y)) != 2:
            one_class += 1
            continue
        for m in MODALITIES:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    draws[m][i] = float(metric(y, np.asarray(task.predictions[m])[indices]))
                except (ValueError, FloatingPointError, ZeroDivisionError):
                    pass
    return {"contrasts": {f"{l}_minus_{r}": paired_summary(observed[l] - observed[r], draws[l] - draws[r]) for l, r in CONTRASTS},
            "one_class_replicates": one_class, "one_class_frequency": one_class / len(weights),
            "observed": observed, "replicates": len(weights), "shared_subject_multiplicities": True}


def paired_inference(evaluation: Mapping[str, Any], *, condition: str = "primary",
                     endpoint: str = "lvef_lt_40", activate_core: bool = True) -> dict[str, Any]:
    """The fixed 10,000-draw primary families; no LVEF-only core replacement."""
    require(evaluation["status"] == "PASS_LOCKED_TEST_EVALUATION" and evaluation["fixed_models"] is True,
            "INFERENCE_REQUIRES_RELEASED_FIXED_MODELS")
    panel = evaluation["strict_panel"]
    require(evaluation["strict_panel_locked"] is True and panel,
            "CORE_REQUIRES_LOCKED_STRICT_PANEL")
    require(condition in EVALUATION_CONDITIONS and endpoint in {"lvef_lt_40", "lvef_le_40", "lvef_lt_50"}, "INFERENCE_CONDITION_INVALID")
    require(not activate_core or (condition == "primary" and endpoint == "lvef_lt_40" and evaluation["construct"] == "strict"),
            "SENSITIVITY_CANNOT_ACTIVATE_CORE")
    def task_for(name, binary=False):
        row = evaluation["targets"][name]
        states = dict(row["conditions"][condition])
        if condition == "no_indicators":
            states["vision_only"] = row["conditions"]["primary"]["vision_only"]
        y = np.asarray(row["target_values"], float)
        predictions = {m: np.asarray(states[m]["binary"][endpoint]["score"] if binary else states[m]["prediction"], float) for m in MODALITIES}
        from lvef_revalidation_analysis import binary_labels
        return TaskPredictions(tuple(row["subject_ids"]), binary_labels(y, endpoint) if binary else y, predictions, row["train_iqr"])
    roster = evaluation["test_subject_roster"]
    panel_result = panel_bootstrap_from_multiplicities({name: task_for(name) for name in panel}, locked_panel=panel,
        roster=roster, multiplicities=subject_multiplicities(roster))
    anchor = task_for("lvef")
    anchor_weights = subject_multiplicities(anchor.subjects)
    anchor_result = paired_metric_bootstrap(anchor, metric=lambda y, p: float(np.mean(np.abs(y - p))), multiplicities=anchor_weights)
    binary_states = evaluation["targets"]["lvef"]["conditions"][condition]
    supported = all(state["binary"][endpoint]["status"] == "EVALUATED" for state in binary_states.values())
    binary_result = paired_metric_bootstrap(task_for("lvef", binary=True), metric=roc_auc_score, multiplicities=anchor_weights, binary=True) if supported else {"status": "UNSUPPORTED_CLASS_COUNTS"}
    primary_p = {}
    for i, (left, right) in enumerate(CONTRASTS[:2]):
        key = f"{left}_minus_{right}"
        primary_p[CORE_CLAIMS[i]] = anchor_result["contrasts"][key]["p_value"]
        primary_p[CORE_CLAIMS[i + 2]] = panel_result["macro_contrasts"][key]["p_value"]
    core = core_holm(primary_p, strict_panel=panel, strict_panel_locked=True) if activate_core else {"status": "SECONDARY_NO_CORE_CLAIM"}
    binary_p = {f"{l}_minus_{r}": binary_result["contrasts"][f"{l}_minus_{r}"]["p_value"] for l, r in CONTRASTS[:2]} if supported else {}
    binary_family_active = condition == "primary" and endpoint == "lvef_lt_40" and evaluation["construct"] == "strict"
    return {"replicates": 10000, "seed": SEED, "lvef_mae": anchor_result, "strict_panel": panel_result,
            "condition": condition, "endpoint": endpoint, "core_multiplicity": core, "secondary_logistic_auroc": binary_result,
            "secondary_binary_holm": (holm(binary_p) if supported else {"status": "UNSUPPORTED_CLASS_COUNTS"}) if binary_family_active else {"status": "DESCRIPTIVE_NO_ADDITIONAL_HOLM_FAMILY"},
            "binary_family_role": "secondary_not_core_global_fwer",
            "inference_scope": "conditional_on_fixed_selected_models"}


def _numeric_metrics(value: Mapping[str, Any]) -> dict[str, float]:
    excluded = {"n", "events", "nonevents", "tp", "tn", "fp", "fn"}
    result = {key: float(v) if v is not None else np.nan for key, v in value.items()
              if key not in excluded and (v is None or type(v) in {float, int})}
    for key, v in value.get("tolerance_coverage", {}).items():
        result["tolerance_" + key] = float(v)
    for band, strata in value.get("threshold_bands", {}).items():
        for name, record in strata.items():
            result[f"band_{band}_{name}_mae"] = float(record["mae"]) if record["mae"] is not None else np.nan
    return result


def paired_metric_collection_from_multiplicities(*, subjects: Sequence[str], roster: Sequence[str],
        multiplicities: np.ndarray, scorers: Mapping[str, Callable[[np.ndarray], Mapping[str, float]]]) -> dict[str, Any]:
    """Shared draws for a complete metric collection, including undefined metrics.

    A scorer receives repeated local row indices. This also handles different
    frozen operating cutoffs across modalities without changing the pairing.
    """
    weights = np.asarray(multiplicities)
    require(set(scorers) == set(MODALITIES) and len(set(subjects)) == len(subjects) > 0
            and set(subjects) <= set(roster) and len(set(roster)) == len(roster)
            and weights.ndim == 2 and weights.shape[1] == len(roster)
            and np.issubdtype(weights.dtype, np.integer) and np.all(weights >= 0)
            and np.all(weights.sum(axis=1) == len(roster)), "METRIC_COLLECTION_INPUT_INVALID")
    observed = {m: dict(scorers[m](np.arange(len(subjects)))) for m in MODALITIES}
    keys = set(observed[MODALITIES[0]])
    require(all(set(v) == keys for v in observed.values()), "METRIC_COLLECTION_SCHEMA_MISMATCH")
    samples = {m: {k: np.full(len(weights), np.nan) for k in keys} for m in MODALITIES}
    position = {s: i for i, s in enumerate(roster)}
    local_weights = weights[:, [position[s] for s in subjects]]
    for b, row_weights in enumerate(local_weights):
        indices = np.repeat(np.arange(len(subjects)), row_weights)
        if not len(indices):
            continue
        for modality in MODALITIES:
            result = scorers[modality](indices)
            require(set(result) == keys, "METRIC_COLLECTION_SCHEMA_MISMATCH")
            for key in keys:
                samples[modality][key][b] = result[key]
    contrasts = {}
    for left, right in CONTRASTS:
        contrasts[f"{left}_minus_{right}"] = {}
        for key in sorted(keys):
            observed_effect = observed[left][key] - observed[right][key]
            sample_effect = samples[left][key] - samples[right][key]
            if np.isfinite(observed_effect):
                result = paired_summary(observed_effect, sample_effect)
            else:
                valid = int(np.isfinite(sample_effect).sum())
                result = {"status": "OBSERVED_METRIC_UNDEFINED", "effect": None, "interval": None, "p_value": None,
                          "valid_replicates": valid, "undefined_replicates": len(weights) - valid,
                          "undefined_frequency": (len(weights) - valid) / len(weights), "replicates": len(weights)}
            contrasts[f"{left}_minus_{right}"][key] = result
    return {"contrasts": contrasts, "model_metrics": {
        m: {key: model_interval(observed[m][key], samples[m][key]) for key in sorted(keys)} for m in MODALITIES},
            "replicates": len(weights), "shared_subject_multiplicities": True,
            "effect_orientation": "named_left_modality_minus_named_right_modality",
            "metric_directions": {key: metric_orientation(key) for key in sorted(keys)},
            "scope": "secondary_effects_and_intervals_no_additional_superiority_claim"}


def complete_paired_intervals(evaluation: Mapping[str, Any], *, condition: str = "primary") -> dict[str, Any]:
    """Dispatch SAP secondary metrics with 10,000 shared draws and frozen models.

    Includes all continuous metrics/tolerances/bands, all supported logistic
    endpoints, fixed-0.5 descriptions, and thresholded-Ridge coherence. Plot bins
    and confusion counts remain descriptive denominators, not extra hypotheses.
    """
    require(evaluation["status"] == "PASS_LOCKED_TEST_EVALUATION" and evaluation["fixed_models"] is True,
            "INFERENCE_REQUIRES_RELEASED_FIXED_MODELS")
    require(condition in EVALUATION_CONDITIONS, "INFERENCE_CONDITION_INVALID")
    roster = evaluation["test_subject_roster"]
    panel_weights = subject_multiplicities(roster)
    anchor_subjects = evaluation["targets"]["lvef"]["subject_ids"]
    anchor_weights = subject_multiplicities(anchor_subjects)
    output = {}
    for target, row in evaluation["targets"].items():
        states = dict(row["conditions"][condition])
        if condition == "no_indicators":
            states["vision_only"] = row["conditions"]["primary"]["vision_only"]
        labels = np.asarray(row["target_values"], float)
        sampling_roster, draws = (anchor_subjects, anchor_weights) if target == "lvef" else (roster, panel_weights)
        def regression_scorer(state):
            prediction = np.asarray(state["prediction"], float)
            return lambda idx: _numeric_metrics(regression_metrics(labels[idx], prediction[idx], train_iqr=row["train_iqr"], lvef=target == "lvef"))
        output[target] = {"continuous": paired_metric_collection_from_multiplicities(
            subjects=row["subject_ids"], roster=sampling_roster, multiplicities=draws,
            scorers={m: regression_scorer(states[m]) for m in MODALITIES})}
        if target != "lvef":
            continue
        from lvef_revalidation_analysis import binary_labels, ENDPOINTS
        output[target]["binary"] = {}
        for endpoint in ENDPOINTS:
            if any(states[m]["binary"][endpoint]["status"] != "EVALUATED" for m in MODALITIES):
                output[target]["binary"][endpoint] = {"status": "UNSUPPORTED_CLASS_COUNTS"}
                continue
            y = binary_labels(labels, endpoint)
            def binary_scorer(state):
                bs = state["binary"][endpoint]
                score, probability = np.asarray(bs["score"]), np.asarray(bs["calibrated_probability"])
                ridge = np.asarray(state["prediction"])
                def score_all(idx):
                    result = _numeric_metrics(binary_metrics(y[idx], score[idx], probability[idx], cutoff=bs["frozen_cutoff"]))
                    descriptive = _numeric_metrics(binary_metrics(y[idx], score[idx], None, called=probability[idx] >= .5))
                    coherence = _numeric_metrics(binary_metrics(y[idx], -ridge[idx], None, called=binary_labels(ridge[idx], endpoint)))
                    result.update({"fixed_0_5_" + k: v for k, v in descriptive.items()})
                    result.update({"coherence_" + k: v for k, v in coherence.items()})
                    return result
                return score_all
            output[target]["binary"][endpoint] = paired_metric_collection_from_multiplicities(
                subjects=row["subject_ids"], roster=anchor_subjects, multiplicities=anchor_weights,
                scorers={m: binary_scorer(states[m]) for m in MODALITIES})
            events = anchor_weights @ y
            one_class = int(np.count_nonzero((events == 0) | (events == len(y))))
            output[target]["binary"][endpoint].update(one_class_replicates=one_class, one_class_frequency=one_class / len(anchor_weights))
    return {"replicates": 10000, "seed": SEED, "condition": condition, "targets": output,
            "shared_panel_draws_across_tasks": True, "fixed_models": True}


def summarize_panel(evaluation: Mapping[str, Any], *, condition: str = "primary") -> dict[str, Any]:
    panel = evaluation["strict_panel"]
    require(evaluation["strict_panel_locked"] is True and panel and "lvef" not in panel, "PANEL_SUMMARY_NOT_LOCKED")
    result = {}
    for modality in MODALITIES:
        rows = []
        for name in panel:
            row = evaluation["targets"][name]
            actual_condition = "primary" if modality == "vision_only" else condition
            metrics = row["conditions"][actual_condition][modality]["metrics"]
            require(np.isfinite(metrics["normalized_mae"]), "LOCKED_PANEL_METRIC_UNDEFINED")
            rows.append((name, row["family"], metrics))
        values = np.array([m["normalized_mae"] for _, _, m in rows])
        families = {family: float(np.mean([m["normalized_mae"] for _, f, m in rows if f == family])) for _, family, _ in rows}
        result[modality] = {"target_count": len(panel), "macro_normalized_mae": float(values.mean()),
            "median_normalized_mae": float(np.median(values)), "iqr_normalized_mae": float(np.percentile(values, 75) - np.percentile(values, 25)),
            "family_means": families, "family_balanced_normalized_mae": float(np.mean(list(families.values()))),
            "negative_r2_count": sum(m["r2"] is not None and m["r2"] < 0 for _, _, m in rows),
            "undefined_r2_count": sum(m["r2"] is None for _, _, m in rows),
            "worst_tasks": [name for name, _, _ in sorted(rows, key=lambda r: (-r[2]["normalized_mae"], r[0]))]}
    return result


def subgroup_metrics(y: Sequence[float], prediction: Sequence[float], *, train_iqr: float,
                     binary_score: Sequence[float] | None = None) -> dict[str, Any]:
    """Only an already locked subgroup is supplied; no data-driven category merging."""
    y = np.asarray(y, float)
    if len(y) < 40:
        return {"n": len(y), "status": "SUPPRESSED_N_LT_40"}
    output = {"status": "DESCRIPTIVE_SUBGROUP", "regression": regression_metrics(y, prediction, train_iqr=train_iqr, lvef=True)}
    labels = (y < 40).astype(int)
    if binary_score is not None:
        output["binary_auroc"] = float(roc_auc_score(labels, binary_score)) if min(labels.sum(), len(y) - labels.sum()) >= 10 else None
        output["binary_status"] = "DESCRIPTIVE" if output["binary_auroc"] is not None else "SUPPRESSED_CLASS_LT_10"
    return output


def practical_margin(interval: Sequence[float], *, delta: float | None) -> str:
    if delta is None:
        return "MARGIN_UNRESOLVED_NO_WIN_TIE_LOSS_CLAIM"
    require(len(interval) == 2 and np.isfinite(interval).all() and interval[0] <= interval[1]
            and np.isfinite(delta) and delta > 0, "MARGIN_INPUT_INVALID")
    low, high = interval
    if high < -delta:
        return "MARGIN_EXCEEDING_LOWER_ERROR"
    if low > delta:
        return "MARGIN_EXCEEDING_HIGHER_ERROR"
    if low >= -delta and high <= delta:
        return "PRACTICAL_EQUIVALENCE_UNDER_RESEARCH_MARGIN"
    if high < 0:
        return "LOWER_ERROR_MAGNITUDE_NOT_ESTABLISHED"
    return "INDETERMINATE"
