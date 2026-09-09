"""Restricted ASA revalidation engine; never an access or clinical-signoff authority.

The caller validates real receipts in ``authorize``. For stage='test' it must
atomically reserve a single-use, frozen-artifact-bound release before returning.
This module does not discover input files, inspect test data during selection,
write outputs, alter the panel, or refit selected models. All returned rows,
coefficients, fingerprints and predictions are restricted artifacts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import re
import warnings
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score

GRID = (0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
SEED = 20260801
MODALITIES = ("vision_only", "structured_only", "early_fusion")
ENDPOINTS = ("lvef_lt_40", "lvef_le_40", "lvef_lt_50")
CONTEXT_SENSITIVITIES = {
    "random_10": {"scheme": "random", "rate": .1, "seed": SEED},
    "random_30": {"scheme": "random", "rate": .3, "seed": SEED},
    "random_50": {"scheme": "random", "rate": .5, "seed": SEED},
    "training_joint_pattern": {"scheme": "training_joint_pattern", "rate": None, "seed": SEED},
}
EVALUATION_CONDITIONS = ("primary", "no_indicators", *CONTEXT_SENSITIVITIES)
CONTEXT_TARGETS = {"body_surface_area", "height_cm", "resting_hr", "resting_dbp", "resting_sbp"}
SHA = re.compile(r"[0-9a-f]{64}")


class AnalysisError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(condition: Any, code: str) -> None:
    if not condition:
        raise AnalysisError(code)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class TargetPolicy:
    name: str
    unit: str
    family: str
    allowed_predictors: tuple[str, ...]
    exact_target_fields: tuple[str, ...]
    aliases: tuple[str, ...]
    deterministic_fields: tuple[str, ...]
    near_deterministic_fields: tuple[str, ...]
    family_fields: tuple[str, ...]
    row_fingerprints: Mapping[str, str]
    support_counts: Mapping[str, int]
    binary_class_counts: Mapping[str, Mapping[str, tuple[int, int]]]
    dependencies_resolved: bool = False
    null_reference: str | None = None

    @property
    def prohibited(self) -> set[str]:
        return {self.name, *self.exact_target_fields, *self.aliases,
                *self.deterministic_fields, *self.near_deterministic_fields, *self.family_fields}


@dataclass(frozen=True)
class AnalysisSpec:
    policies: tuple[TargetPolicy, ...]
    strict_panel: tuple[str, ...]
    strict_panel_locked: bool
    construct: str
    test_subject_roster: tuple[str, ...]
    bindings: Mapping[str, str]
    prescription_sha256: str

    @property
    def sha256(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class ModalityRows:
    subject_ids: tuple[str, ...]
    study_ids: tuple[str, ...]
    split: str
    target: str
    target_values: np.ndarray
    vision: np.ndarray | None = None
    structured: np.ndarray | None = None
    structured_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TestRelease:
    spec_sha256: str
    frozen_sha256: str
    release_receipt_sha256: str


@dataclass(frozen=True)
class FrozenAnalysis:
    payload: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return digest(self.payload)


def row_fingerprint(rows: ModalityRows) -> str:
    return digest({"subject_id": list(rows.subject_ids), "study_id": list(rows.study_ids),
                   "split": rows.split, "target": rows.target,
                   "target_values": np.asarray(rows.target_values, dtype=float).tolist()})


def validate_prescription(config: Mapping[str, Any]) -> str:
    """Bind the existing SAP choices, while leaving stale execution-status flags alone."""
    expected = {
        "lvef.historical_primary_binary_definition": {"operator": "<", "threshold_percent": 40.0},
        "models": {"modalities": list(MODALITIES), "regression_model": "ridge",
            "ridge_alpha_grid": list(GRID), "ridge_solver": "lsqr", "ridge_tolerance": 1e-8,
            "ridge_max_iter": 10000, "ridge_fit_intercept": True, "alpha_selection_split": "val",
            "alpha_selection_metric": "mae", "alpha_tie_break": "larger_alpha",
            "refit_train_plus_val": False, "use_identical_grid_across_modalities": True,
            "binary_primary_model": "separately_trained_class_weighted_logistic_regression",
            "binary_secondary_model": "thresholded_continuous_ridge_prediction"},
        "models.binary_logistic": {"penalty": "l2", "c_grid": list(GRID), "solver": "liblinear",
            "max_iter": 5000, "tolerance": 1e-4, "fit_intercept": True, "random_seed": SEED,
            "class_weight_rule": "balanced_from_training_labels", "validation_selection_metric": "auroc",
            "selection_tie_break": "smaller_c", "separate_model_per_binary_definition": True},
        "models.probability_calibration": {"policy": "platt_sigmoid_on_selected_model_decision_score",
            "base_model_fitting_split": "train", "calibrator_fitting_split": "val",
            "calibrator_weighting": "unweighted", "method_selection_permitted": False,
            "validation_reused_after_c_selection": True, "no_silent_fallback": True},
        "models.validation_operating_point": {"selection_split": "val", "probability_source": "calibrated_probability",
            "criterion": "maximize_youden_j", "tie_break": ["greater_sensitivity", "greater_specificity", "larger_cutoff"]},
        "structured_preprocessing": {"prohibited_fields_removed_before_all_transforms": True,
            "feature_eligibility_learned_from": "train_only",
            "minimum_finite_observed_rule": "max_20_or_5_percent_of_target_specific_training_rows",
            "require_two_distinct_finite_values": True, "imputation": "training_median",
            "continuous_scaling": "training_mean_and_standard_deviation", "vision_scaling": "training_mean_and_standard_deviation",
            "missing_indicator_rule": "create_for_eligible_allowed_predictor_with_observed_and_missing_training_values",
            "missing_indicators_scaled": False, "missing_indicator_primary_analysis": True,
            "no_missing_indicator_sensitivity_required": True,
            "identical_structured_features_and_transforms_for_structured_and_fusion": True},
        "target_support": {"min_train_observed": 120, "min_val_observed": 40, "min_test_observed": 40,
            "min_total_observed": 250, "require_positive_finite_train_iqr": True,
            "binary_min_events_per_split": 20, "binary_min_nonevents_per_split": 20, "no_postfit_target_removal": True},
        "paired_bootstrap": {"unit": "subject", "replicates": 10000, "seed": SEED,
            "interval": "percentile_95", "reuse_subject_draw_across_modalities": True,
            "reuse_subject_draw_across_tasks": True, "resampling": "nonstratified_with_replacement",
            "fixed_fitted_models_and_preprocessing": True,
            "panel_sampling_roster": "locked_imaging_eligible_test_subjects",
            "panel_macro_replicate_requires_every_locked_task_defined": True,
            "retain_undefined_replicate_counts": True,
            "two_sided_pvalue": "centered_bootstrap_plus_one_count_abs_dstar_minus_d_ge_abs_d_over_valid_b_plus_one"},
    }
    projection = {}
    for path, fields in expected.items():
        observed = config
        for part in path.split("."):
            observed = observed[part]
        require(all(canonical_bytes(observed.get(k)) == canonical_bytes(v) for k, v in fields.items()),
                "ANALYSIS_PRESCRIPTION_MISMATCH")
        projection[path] = fields
    family = config["multiplicity"]["core_global_family"]
    from lvef_revalidation_inference import CORE_CLAIMS
    require(family["hypotheses"] == list(CORE_CLAIMS)
            and family["activation_requires_locked_strict_panel"] is True
            and family["method"] == "holm_familywise_two_sided_0.05", "CORE_FAMILY_SPECIFICATION_MISMATCH")
    projection["core_family"] = family
    return digest(projection)


def validate_spec(spec: AnalysisSpec) -> None:
    names = tuple(policy.name for policy in spec.policies)
    require(spec.construct in {"strict", "family_masked", "pragmatic"}, "CONSTRUCT_INVALID")
    require(spec.strict_panel_locked is True and len(spec.strict_panel) > 0
            and len(set(spec.strict_panel)) == len(spec.strict_panel)
            and not ({"lvef"} | CONTEXT_TARGETS) & set(spec.strict_panel), "STRICT_PANEL_NOT_LOCKED")
    require(set(names) == {"lvef", *spec.strict_panel} and len(set(names)) == len(names), "TARGET_SET_MISMATCH")
    required_bindings = {"source_completion", "input_audit", "clinical_panel", "dependency_registry",
                         "sap", "config", "environment", "owner_authorization", "safety"}
    require(set(spec.bindings) == required_bindings and all(SHA.fullmatch(v) for v in spec.bindings.values())
            and SHA.fullmatch(spec.prescription_sha256), "SPEC_AUTHORITY_BINDING_INVALID")
    require(len(spec.test_subject_roster) == len(set(spec.test_subject_roster)) > 0
            and all(type(x) is str and x for x in spec.test_subject_roster), "TEST_ROSTER_INVALID")
    for p in spec.policies:
        require(p.dependencies_resolved is True and p.unit and p.family and p.exact_target_fields,
                "TARGET_DEPENDENCIES_UNRESOLVED")
        require(len(p.allowed_predictors) == len(set(p.allowed_predictors)) > 0
                and not set(p.allowed_predictors) & p.prohibited,
                "PROHIBITED_PREDICTOR_SURVIVED_MASK")
        require(set(p.row_fingerprints) == {"train", "val", "test"}
                and all(SHA.fullmatch(v) for v in p.row_fingerprints.values()), "ROW_AUTHORITY_INVALID")
        require(set(p.support_counts) == {"train", "val", "test"}
                and all(type(v) is int for v in p.support_counts.values())
                and p.support_counts["train"] >= 120 and p.support_counts["val"] >= 40
                and p.support_counts["test"] >= 40 and sum(p.support_counts.values()) >= 250,
                "TARGET_SUPPORT_FAILED")
        require(p.null_reference in {None, "training_median"}, "NULL_REFERENCE_UNSPECIFIED")
        require(set(p.binary_class_counts) == (set(ENDPOINTS) if p.name == "lvef" else set()), "BINARY_SUPPORT_AUDIT_INVALID")
        for counts in p.binary_class_counts.values():
            require(set(counts) == {"train", "val", "test"}
                    and all(len(v) == 2 and all(type(n) is int and n >= 0 for n in v)
                            and sum(v) == p.support_counts[s] for s, v in counts.items()), "BINARY_SUPPORT_AUDIT_INVALID")
        if p.name == "lvef":
            require(all(min(v) >= 20 for v in p.binary_class_counts["lvef_lt_40"].values()), "PRIMARY_BINARY_SUPPORT_FAILED")


def validate_common_rows(modalities: Mapping[str, ModalityRows], policy: TargetPolicy, split: str) -> ModalityRows:
    require(set(modalities) == set(MODALITIES), "MODALITY_SET_MISMATCH")
    reference = modalities["early_fusion"]
    for name, row in modalities.items():
        n = len(row.subject_ids)
        require(row.split == split and row.target == policy.name and split in {"train", "val", "test"}, "ROW_SPLIT_TARGET_MISMATCH")
        require(n == len(row.study_ids) == len(row.target_values) == policy.support_counts[split]
                and len(set(row.subject_ids)) == len(set(row.study_ids)) == n, "ROW_MULTIPLICITY_INVALID")
        require(all(type(v) is str and v for v in (*row.subject_ids, *row.study_ids)), "ROW_IDENTIFIER_INVALID")
        require(np.asarray(row.target_values).shape == (n,) and np.isfinite(row.target_values).all(), "TARGET_NOT_FINITE")
        require(row_fingerprint(row) == policy.row_fingerprints[split]
                and row_fingerprint(row) == row_fingerprint(reference), "CROSS_MODALITY_ROW_OR_LABEL_MISMATCH")
        if name != "structured_only":
            require(row.vision is not None and row.vision.ndim == 2 and row.vision.shape[0] == n
                    and row.vision.shape[1] > 0 and np.isfinite(row.vision).all(), "VISION_INPUT_INVALID")
            require(np.array_equal(row.vision, reference.vision), "SHARED_VISION_INPUT_MISMATCH")
        if name != "vision_only":
            require(row.structured is not None and row.structured.shape == (n, len(row.structured_names))
                    and len(set(row.structured_names)) == len(row.structured_names), "STRUCTURED_INPUT_INVALID")
            require(row.structured_names == reference.structured_names
                    and np.array_equal(row.structured, reference.structured, equal_nan=True), "SHARED_STRUCTURED_INPUT_MISMATCH")
            require(set(policy.allowed_predictors) <= set(row.structured_names), "ALLOWED_PREDICTOR_ABSENT")
    return reference


def fit_transform(train: ModalityRows, policy: TargetPolicy) -> dict[str, Any]:
    require(train.split == "train", "PREPROCESSING_OUTSIDE_TRAIN")
    # Index the closed allowlist BEFORE any finite counts, imputation or indicators.
    allowed = list(policy.allowed_predictors)
    require(not set(allowed) & policy.prohibited, "PROHIBITED_PREDICTOR_SURVIVED_MASK")
    x = np.asarray(train.structured[:, [train.structured_names.index(v) for v in allowed]], dtype=float)
    finite = np.isfinite(x)
    counts = finite.sum(axis=0)
    minimum = max(20, math.ceil(.05 * len(x)))
    eligible = [j for j in range(len(allowed)) if counts[j] >= minimum and np.unique(x[finite[:, j], j]).size >= 2]
    require(eligible, "NO_ELIGIBLE_STRUCTURED_FEATURES")
    x, finite = x[:, eligible], finite[:, eligible]
    medians = np.array([np.median(x[finite[:, j], j]) for j in range(x.shape[1])])
    imputed = np.where(finite, x, medians)
    vision = np.asarray(train.vision, dtype=float)
    require(np.isfinite(vision).all(), "VISION_INPUT_INVALID")
    scale = imputed.std(axis=0)
    vscale = vision.std(axis=0)
    return {"features": [allowed[j] for j in eligible], "feature_sha256": digest([allowed[j] for j in eligible]),
        "allowed_schema_sha256": digest(allowed), "observed_training_counts": [int(v) for v in counts],
        "eligibility_minimum": minimum, "excluded_allowed_features": [v for j, v in enumerate(allowed) if j not in eligible],
        "medians": medians.tolist(), "structured_mean": imputed.mean(axis=0).tolist(),
        "structured_scale": np.where(scale > 0, scale, 1).tolist(),
        "indicator_indices": [j for j in range(x.shape[1]) if 0 < finite[:, j].sum() < len(x)],
        "vision_mean": vision.mean(axis=0).tolist(), "vision_scale": np.where(vscale > 0, vscale, 1).tolist(),
        "fitted_split": "train", "training_rows": len(x), "training_row_sha256": row_fingerprint(train)}


def apply_transform(row: ModalityRows, transform: Mapping[str, Any], *, indicators: bool = True) -> dict[str, np.ndarray]:
    x = np.asarray(row.structured[:, [row.structured_names.index(v) for v in transform["features"]]], dtype=float)
    finite = np.isfinite(x)
    structured = (np.where(finite, x, transform["medians"]) - transform["structured_mean"]) / transform["structured_scale"]
    if indicators and transform["indicator_indices"]:
        structured = np.column_stack((structured, (~finite[:, transform["indicator_indices"]]).astype(float)))
    vision = (np.asarray(row.vision, dtype=float) - transform["vision_mean"]) / transform["vision_scale"]
    require(np.isfinite(structured).all() and np.isfinite(vision).all(), "TRANSFORM_NOT_FINITE")
    return {"vision_only": vision, "structured_only": structured, "early_fusion": np.column_stack((vision, structured))}


def fit_missingness_patterns(train: ModalityRows, transform: Mapping[str, Any]) -> dict[str, Any]:
    require(train.split == "train" and row_fingerprint(train) == transform["training_row_sha256"], "MISSINGNESS_LEARNING_OUTSIDE_TRAIN")
    x = train.structured[:, [train.structured_names.index(v) for v in transform["features"]]]
    patterns, counts = np.unique(~np.isfinite(x), axis=0, return_counts=True)
    burden = np.isfinite(x).sum(axis=1) / x.shape[1]
    return {"features": list(transform["features"]), "patterns": patterns.astype(int).tolist(),
            "counts": counts.tolist(), "training_rows": len(x), "training_row_sha256": row_fingerprint(train),
            "burden_quartiles": np.percentile(burden, [25, 50, 75]).tolist(), "fitted_split": "train"}


def simulate_missingness(row: ModalityRows, transform: Mapping[str, Any], *, scheme: str,
                         seed: int = SEED, rate: float | None = None, fields: Sequence[str] = (),
                         empirical: Mapping[str, Any] | None = None) -> ModalityRows:
    """Withhold allowed context only; labels and the paired denominator never change."""
    require(scheme in {"single_field", "whole_family", "random", "training_joint_pattern"}, "MASK_SIMULATION_UNSPECIFIED")
    features = list(transform["features"])
    indices = [row.structured_names.index(v) for v in features]
    rng = np.random.default_rng(seed)
    mask = np.zeros((len(row.subject_ids), len(features)), dtype=bool)
    if scheme in {"single_field", "whole_family"}:
        require(fields and set(fields) <= set(features) and (scheme != "single_field" or len(fields) == 1), "SIMULATED_FIELD_NOT_ALLOWED")
        mask[:, [features.index(v) for v in fields]] = True
    elif scheme == "random":
        require(rate in {.1, .3, .5}, "RANDOM_MASK_RATE_UNSPECIFIED")
        mask = rng.random(mask.shape) < rate
    else:
        require(empirical is not None and empirical["fitted_split"] == "train"
                and empirical["features"] == features
                and empirical["training_row_sha256"] == transform["training_row_sha256"], "EMPIRICAL_MASK_AUTHORITY_INVALID")
        patterns, counts = np.asarray(empirical["patterns"], bool), np.asarray(empirical["counts"], int)
        require(patterns.shape == (len(counts), len(features)) and np.all(counts > 0)
                and counts.sum() == empirical["training_rows"], "EMPIRICAL_MASK_INVALID")
        mask = patterns[rng.choice(len(counts), size=len(mask), p=counts / counts.sum())]
    x = np.asarray(row.structured, float).copy()
    block = x[:, indices].copy()
    block[mask] = np.nan
    x[:, indices] = block
    return replace(row, structured=x)


def binary_labels(values: np.ndarray, endpoint: str) -> np.ndarray:
    require(endpoint in ENDPOINTS, "BINARY_ENDPOINT_INVALID")
    if endpoint == "lvef_le_40":
        return (np.asarray(values) <= 40).astype(int)
    return (np.asarray(values) < (50 if endpoint == "lvef_lt_50" else 40)).astype(int)


def select_grid(scores: Mapping[float, float], *, kind: str) -> float:
    require(set(scores) == set(GRID) and all(np.isfinite(v) for v in scores.values()), "GRID_SCORES_INVALID")
    require(kind in {"ridge", "logistic"}, "GRID_KIND_INVALID")
    return min(GRID, key=lambda x: (scores[x], -x)) if kind == "ridge" else min(GRID, key=lambda x: (-scores[x], x))


def _linear_state(model: Any, parameter: float, scores: Mapping[float, float], n: int) -> dict[str, Any]:
    coef = np.asarray(model.coef_).reshape(-1)
    intercept = float(np.asarray(model.intercept_).reshape(-1)[0])
    require(np.isfinite(coef).all() and np.isfinite(intercept), "MODEL_COEFFICIENT_NOT_FINITE")
    return {"coef": coef.tolist(), "intercept": intercept, "selected_parameter": parameter,
            "validation_scores": {str(k): float(v) for k, v in scores.items()}, "fit_rows": n,
            "fit_split": "train", "refit_train_plus_val": False}


def linear_predict(model: Mapping[str, Any], x: np.ndarray) -> np.ndarray:
    result = x @ np.asarray(model["coef"]) + model["intercept"]
    require(np.isfinite(result).all(), "PREDICTION_NOT_FINITE")
    return result


def fit_selected_model(train_x: np.ndarray, y: np.ndarray, val_x: np.ndarray, val_y: np.ndarray, *, kind: str) -> dict[str, Any]:
    require(kind in {"ridge", "logistic"}, "MODEL_KIND_UNSPECIFIED")
    fitted, scores = {}, {}
    for parameter in GRID:
        if kind == "ridge":
            model = Ridge(alpha=parameter, fit_intercept=True, solver="lsqr", tol=1e-8, max_iter=10000)
        else:
            model = LogisticRegression(C=parameter, penalty="l2", solver="liblinear", max_iter=5000,
                                       tol=1e-4, fit_intercept=True, random_state=SEED, class_weight="balanced")
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            try:
                model.fit(train_x, y)
            except (ConvergenceWarning, ValueError, FloatingPointError) as exc:
                raise AnalysisError("MODEL_FIT_FAILED") from exc
        fitted[parameter] = model
        scores[parameter] = float(np.mean(np.abs(model.predict(val_x) - val_y))) if kind == "ridge" else float(roc_auc_score(val_y, model.decision_function(val_x)))
    selected = select_grid(scores, kind=kind)
    return _linear_state(fitted[selected], selected, scores, len(y))


def fit_platt(scores: np.ndarray, labels: np.ndarray, *, split: str) -> dict[str, Any]:
    """Unweighted, unpenalized logistic MLE; fixed optimizer, no method selection."""
    require(split == "val", "CALIBRATION_OUTSIDE_VALIDATION")
    require(set(np.asarray(labels)) == {0, 1}, "CALIBRATION_INPUT_INVALID")
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    require(scores.shape == labels.shape and np.isfinite(scores).all() and set(labels) == {0, 1}, "CALIBRATION_INPUT_INVALID")
    center, scale = float(scores.mean()), float(scores.std())
    require(scale > 0, "CALIBRATION_SCORE_DEGENERATE")
    x = np.column_stack((np.ones(len(scores)), (scores - center) / scale))
    def objective(beta):
        z = x @ beta
        return float(np.mean(np.logaddexp(0, z) - labels * z)), x.T @ (expit(z) - labels) / len(z)
    fit = minimize(objective, np.zeros(2), jac=True, method="BFGS", options={"gtol": 1e-8, "maxiter": 10000})
    gradient = objective(fit.x)[1]
    require(np.isfinite(fit.x).all() and np.isfinite(fit.fun)
            and np.max(np.abs(gradient)) <= 1e-6 and np.max(np.abs(fit.x)) < 1e4,
            "CALIBRATION_NUMERICAL_FAILURE")
    slope = float(fit.x[1] / scale)
    intercept = float(fit.x[0] - slope * center)
    probabilities = expit(intercept + slope * scores)
    require(np.all((probabilities > 0) & (probabilities < 1)), "CALIBRATION_SATURATED")
    return {"intercept": intercept, "slope": slope, "fit_split": "val", "fit_rows": len(scores),
            "weighting": "unweighted", "method": "unpenalized_platt", "validation_reused_after_selection": True}


def platt_predict(calibrator: Mapping[str, Any], scores: np.ndarray) -> np.ndarray:
    result = expit(calibrator["intercept"] + calibrator["slope"] * scores)
    require(np.isfinite(result).all(), "CALIBRATION_PREDICTION_INVALID")
    return result


def select_youden(probabilities: np.ndarray, labels: np.ndarray, *, split: str) -> float:
    require(split == "val", "OPERATING_POINT_OUTSIDE_VALIDATION")
    p, y = np.asarray(probabilities, float), np.asarray(labels, int)
    require(p.shape == y.shape and set(y) == {0, 1} and np.all((p >= 0) & (p <= 1)), "OPERATING_POINT_INPUT_INVALID")
    cutoffs = np.unique(np.r_[0., p, np.nextafter(1., 2.)])
    def quality(cutoff):
        called = p >= cutoff
        sensitivity = float(called[y == 1].mean())
        specificity = float((~called[y == 0]).mean())
        return sensitivity + specificity - 1, sensitivity, specificity, float(cutoff)
    return float(max(cutoffs, key=quality))


def _authorize(callback: Callable[..., Mapping[str, Any]], stage: str, spec_sha: str,
               frozen_sha: str | None = None, release: TestRelease | None = None) -> dict[str, Any]:
    require(callable(callback), "EXTERNAL_ANALYSIS_AUTHORITY_REQUIRED")
    receipt = callback(stage=stage, spec_sha256=spec_sha, frozen_sha256=frozen_sha, release=release)
    require(isinstance(receipt, Mapping) and set(receipt) == {
        "status", "stage", "spec_sha256", "frozen_sha256", "authority_receipt_sha256"}
        and receipt["status"] == "PASS_ANALYSIS_AUTHORITY" and receipt["stage"] == stage
        and receipt["spec_sha256"] == spec_sha and receipt["frozen_sha256"] == frozen_sha
        and SHA.fullmatch(receipt["authority_receipt_sha256"]), "EXTERNAL_ANALYSIS_AUTHORITY_INVALID")
    return dict(receipt)


def _binary_support(p: TargetPolicy, tr: ModalityRows, va: ModalityRows) -> dict[str, bool]:
    supported = {}
    if p.name != "lvef":
        return supported
    require(set(p.binary_class_counts) == set(ENDPOINTS), "BINARY_SUPPORT_AUDIT_INVALID")
    for endpoint in ENDPOINTS:
        counts = p.binary_class_counts[endpoint]
        require(set(counts) == {"train", "val", "test"}, "BINARY_SUPPORT_AUDIT_INVALID")
        require(all(len(v) == 2 and all(type(n) is int and n >= 0 for n in v)
                    and sum(v) == p.support_counts[s] for s, v in counts.items()), "BINARY_SUPPORT_AUDIT_INVALID")
        for row in (tr, va):
            y = binary_labels(row.target_values, endpoint)
            require(tuple(counts[row.split]) == (int(y.sum()), int(len(y) - y.sum())), "BINARY_SUPPORT_AUDIT_MISMATCH")
        supported[endpoint] = all(min(v) >= 20 for v in counts.values())
    require(supported["lvef_lt_40"], "PRIMARY_BINARY_SUPPORT_FAILED")
    return supported


def select_development(spec: AnalysisSpec, train: Mapping[str, Mapping[str, ModalityRows]],
                       validation: Mapping[str, Mapping[str, ModalityRows]], *, authorize: Callable[..., Mapping[str, Any]]) -> FrozenAnalysis:
    validate_spec(spec)
    authority = _authorize(authorize, "development", spec.sha256)
    names = {p.name for p in spec.policies}
    require(set(train) == set(validation) == names, "DEVELOPMENT_TARGET_SET_MISMATCH")
    checked, ownership, study_ownership = {}, {}, {}
    # Validate EVERY target and split before the first estimator is constructed.
    for p in spec.policies:
        tr, va = validate_common_rows(train[p.name], p, "train"), validate_common_rows(validation[p.name], p, "val")
        for row in (tr, va):
            for subject, study in zip(row.subject_ids, row.study_ids):
                require(subject not in spec.test_subject_roster, "DEVELOPMENT_TEST_SUBJECT_OVERLAP")
                require(subject not in ownership or ownership[subject] == (study, row.split), "SPLIT_OR_STUDY_OWNERSHIP_MISMATCH")
                require(study not in study_ownership or study_ownership[study] == (subject, row.split), "STUDY_SUBJECT_OWNERSHIP_MISMATCH")
                ownership[subject] = study, row.split
                study_ownership[study] = subject, row.split
        iqr = float(np.percentile(tr.target_values, 75) - np.percentile(tr.target_values, 25))
        require(np.isfinite(iqr) and iqr > 0, "TRAIN_TARGET_IQR_INVALID")
        checked[p.name] = tr, va, iqr, fit_transform(tr, p), _binary_support(p, tr, va)
    targets = {}
    for p in spec.policies:
        tr, va, iqr, transform, binary_supported = checked[p.name]
        conditions = {}
        for condition, indicators in (("primary", True), ("no_indicators", False)):
            tx, vx = apply_transform(tr, transform, indicators=indicators), apply_transform(va, transform, indicators=indicators)
            models = {}
            for modality in MODALITIES:
                if condition == "no_indicators" and modality == "vision_only":
                    continue
                state = {"ridge": fit_selected_model(tx[modality], tr.target_values, vx[modality], va.target_values, kind="ridge"), "binary": {}}
                for endpoint, supported in binary_supported.items():
                    if not supported:
                        state["binary"][endpoint] = {"status": "UNSUPPORTED_CLASS_COUNTS"}
                        continue
                    y, vy = binary_labels(tr.target_values, endpoint), binary_labels(va.target_values, endpoint)
                    model = fit_selected_model(tx[modality], y, vx[modality], vy, kind="logistic")
                    score = linear_predict(model, vx[modality])
                    calibration = fit_platt(score, vy, split="val")
                    cutoff = select_youden(platt_predict(calibration, score), vy, split="val")
                    state["binary"][endpoint] = {"status": "FROZEN", "model": model, "calibrator": calibration, "cutoff": cutoff}
                models[modality] = state
            conditions[condition] = models
        targets[p.name] = {"transform": transform, "missingness_patterns": fit_missingness_patterns(tr, transform),
                           "train_iqr": iqr, "training_median": float(np.median(tr.target_values)),
                           "conditions": conditions, "unit": p.unit, "family": p.family}
    return FrozenAnalysis({"schema_version": 1, "artifact_type": "lvef_revalidation_frozen_analysis_v1",
        "status": "FROZEN_BEFORE_TEST", "spec": asdict(spec), "spec_sha256": spec.sha256,
        "development_authority": authority, "targets": targets, "test_access_count": 0,
        "test_context_sensitivities": {k: dict(v) for k, v in CONTEXT_SENSITIVITIES.items()},
        "single_field_and_family_simulation_status": "UNACTIVATED_REQUIRES_CLINICALLY_LOCKED_FIELD_GROUPS",
        "mandatory_no_indicator_sensitivity_fitted": True, "primary_train_plus_validation_refit": False})


def freeze_analysis(frozen: FrozenAnalysis) -> tuple[bytes, str]:
    body = canonical_bytes(frozen.payload)
    return body, hashlib.sha256(body).hexdigest()


def spec_from_payload(value: Mapping[str, Any]) -> AnalysisSpec:
    policies = []
    for p in value["policies"]:
        converted = dict(p)
        for key in ("allowed_predictors", "exact_target_fields", "aliases", "deterministic_fields", "near_deterministic_fields", "family_fields"):
            converted[key] = tuple(converted[key])
        policies.append(TargetPolicy(**converted))
    return AnalysisSpec(tuple(policies), tuple(value["strict_panel"]), value["strict_panel_locked"],
                        value["construct"], tuple(value["test_subject_roster"]), value["bindings"], value["prescription_sha256"])


def load_frozen_analysis(body: bytes, *, expected_sha256: str) -> FrozenAnalysis:
    require(hashlib.sha256(body).hexdigest() == expected_sha256, "FROZEN_ARTIFACT_HASH_MISMATCH")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "FROZEN_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    value = json.loads(body, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(AnalysisError("FROZEN_NONFINITE_JSON")))
    require(canonical_bytes(value) == body and value.get("status") == "FROZEN_BEFORE_TEST"
            and value.get("test_access_count") == 0 and value.get("mandatory_no_indicator_sensitivity_fitted") is True
            and value.get("primary_train_plus_validation_refit") is False, "FROZEN_ARTIFACT_INVALID")
    require(value.get("test_context_sensitivities") == CONTEXT_SENSITIVITIES
            and value.get("single_field_and_family_simulation_status") == "UNACTIVATED_REQUIRES_CLINICALLY_LOCKED_FIELD_GROUPS", "FROZEN_CONTEXT_SENSITIVITY_INVALID")
    spec = spec_from_payload(value["spec"])
    validate_spec(spec)
    require(spec.sha256 == value["spec_sha256"] and set(value["targets"]) == {p.name for p in spec.policies}, "FROZEN_SPEC_MISMATCH")
    for p in spec.policies:
        target = value["targets"][p.name]
        transform = target["transform"]
        features = transform["features"]
        require(features and len(set(features)) == len(features) and set(features) <= set(p.allowed_predictors)
                and not set(features) & p.prohibited and transform["feature_sha256"] == digest(features)
                and transform["fitted_split"] == "train" and transform["training_rows"] == p.support_counts["train"]
                and transform["training_row_sha256"] == p.row_fingerprints["train"]
                and np.isfinite(target["train_iqr"]) and target["train_iqr"] > 0, "FROZEN_TRANSFORM_INVALID")
        for key in ("medians", "structured_mean", "structured_scale"):
            require(len(transform[key]) == len(features) and np.isfinite(transform[key]).all(), "FROZEN_TRANSFORM_INVALID")
        require(len(transform["vision_mean"]) == len(transform["vision_scale"]) > 0
                and np.isfinite(transform["vision_mean"]).all() and np.isfinite(transform["vision_scale"]).all()
                and np.all(np.asarray(transform["structured_scale"]) > 0) and np.all(np.asarray(transform["vision_scale"]) > 0),
                "FROZEN_TRANSFORM_INVALID")
        indicator = transform["indicator_indices"]
        require(len(set(indicator)) == len(indicator) and all(type(j) is int and 0 <= j < len(features) for j in indicator), "FROZEN_INDICATOR_INVALID")
        patterns = target["missingness_patterns"]
        cuts = patterns["burden_quartiles"]
        require(patterns["features"] == features and patterns["fitted_split"] == "train"
                and patterns["training_rows"] == p.support_counts["train"]
                and patterns["training_row_sha256"] == p.row_fingerprints["train"]
                and np.asarray(patterns["patterns"]).shape == (len(patterns["counts"]), len(features))
                and np.isin(patterns["patterns"], [0, 1]).all()
                and all(type(n) is int and n > 0 for n in patterns["counts"])
                and sum(patterns["counts"]) == p.support_counts["train"]
                and len(cuts) == 3 and all(type(x) in {int, float} and math.isfinite(x) and 0 <= x <= 1 for x in cuts)
                and list(cuts) == sorted(cuts), "FROZEN_MISSINGNESS_PATTERN_INVALID")
        require(set(target["conditions"]) == {"primary", "no_indicators"}, "FROZEN_SENSITIVITY_MISSING")
        for condition, states in target["conditions"].items():
            expected = set(MODALITIES) if condition == "primary" else {"structured_only", "early_fusion"}
            require(set(states) == expected, "FROZEN_MODALITIES_INVALID")
            for modality, state in states.items():
                dimension = (len(transform["vision_mean"]) if modality != "structured_only" else 0)
                dimension += (len(features) + (len(indicator) if condition == "primary" else 0)) if modality != "vision_only" else 0
                models = [(state["ridge"], "ridge")]
                require(set(state["binary"]) == (set(ENDPOINTS) if p.name == "lvef" else set()), "FROZEN_BINARY_ENDPOINT_SET_INVALID")
                for endpoint, binary in state["binary"].items():
                    supported = all(min(v) >= 20 for v in p.binary_class_counts[endpoint].values())
                    require(binary["status"] == ("FROZEN" if supported else "UNSUPPORTED_CLASS_COUNTS"), "FROZEN_BINARY_SUPPORT_MISMATCH")
                    if supported:
                        calibration = binary["calibrator"]
                        require(calibration["fit_split"] == "val" and calibration["fit_rows"] == p.support_counts["val"]
                                and calibration["weighting"] == "unweighted"
                                and np.isfinite([calibration["intercept"], calibration["slope"], binary["cutoff"]]).all(), "FROZEN_CALIBRATION_INVALID")
                        models.append((binary["model"], "logistic"))
                for model, kind in models:
                    require(len(model["coef"]) == dimension and np.isfinite(model["coef"]).all()
                            and np.isfinite(model["intercept"]) and model["fit_split"] == "train"
                            and model["fit_rows"] == p.support_counts["train"] and model["refit_train_plus_val"] is False
                            and model["selected_parameter"] == select_grid({float(k): v for k, v in model["validation_scores"].items()}, kind=kind),
                            "FROZEN_MODEL_INVALID")
    return FrozenAnalysis(value)


def evaluate_locked_test(frozen: FrozenAnalysis, *, release: TestRelease,
                         authorize: Callable[..., Mapping[str, Any]], test_loader: Callable[[], Mapping[str, Mapping[str, ModalityRows]]]) -> dict[str, Any]:
    body, frozen_sha = freeze_analysis(frozen)
    frozen = load_frozen_analysis(body, expected_sha256=frozen_sha)
    spec = spec_from_payload(frozen.payload["spec"])
    require(release.spec_sha256 == spec.sha256 and release.frozen_sha256 == frozen_sha
            and SHA.fullmatch(release.release_receipt_sha256), "TEST_RELEASE_BINDING_MISMATCH")
    authority = _authorize(authorize, "test", spec.sha256, frozen_sha, release)
    # First invocation capable of obtaining test feature/label arrays occurs here.
    test = test_loader()
    require(set(test) == {p.name for p in spec.policies}, "TEST_TARGET_SET_MISMATCH")
    checked, ownership, study_ownership = {}, {}, {}
    for p in spec.policies:
        row = validate_common_rows(test[p.name], p, "test")
        require(set(row.subject_ids) <= set(spec.test_subject_roster), "TEST_ROSTER_MISMATCH")
        for subject, study in zip(row.subject_ids, row.study_ids):
            require(subject not in ownership or ownership[subject] == study, "TEST_STUDY_OWNERSHIP_MISMATCH")
            require(study not in study_ownership or study_ownership[study] == subject, "TEST_SUBJECT_OWNERSHIP_MISMATCH")
            ownership[subject] = study
            study_ownership[study] = subject
        if p.name == "lvef":
            for endpoint in ENDPOINTS:
                y = binary_labels(row.target_values, endpoint)
                require(tuple(p.binary_class_counts[endpoint]["test"]) == (int(y.sum()), int(len(y) - y.sum())), "BINARY_SUPPORT_AUDIT_MISMATCH")
        checked[p.name] = row
    from lvef_revalidation_inference import regression_metrics, binary_metrics
    output = {}
    for p in spec.policies:
        row, fitted = checked[p.name], frozen.payload["targets"][p.name]
        conditions = {}
        features = fitted["transform"]["features"]
        observed_fraction = np.isfinite(row.structured[:, [row.structured_names.index(v) for v in features]]).mean(axis=1)
        quartiles = fitted["missingness_patterns"]["burden_quartiles"]
        burden_stratum = np.searchsorted(quartiles, observed_fraction, side="right")
        for condition in EVALUATION_CONDITIONS:
            effective_row = row
            if condition in CONTEXT_SENSITIVITIES:
                mask_spec = frozen.payload["test_context_sensitivities"][condition]
                effective_row = simulate_missingness(row, fitted["transform"], **mask_spec, empirical=fitted["missingness_patterns"])
                require(row_fingerprint(effective_row) == row_fingerprint(row), "CONTEXT_MASK_CHANGED_TARGET_ROWS")
            matrices = apply_transform(effective_row, fitted["transform"], indicators=condition != "no_indicators")
            predictions = {}
            model_condition = condition if condition in {"primary", "no_indicators"} else "primary"
            for modality, state in fitted["conditions"][model_condition].items():
                prediction = linear_predict(state["ridge"], matrices[modality])
                metrics = regression_metrics(row.target_values, prediction, train_iqr=fitted["train_iqr"], lvef=p.name == "lvef")
                if p.null_reference == "training_median":
                    metrics["training_median_null_mae"] = float(np.mean(np.abs(row.target_values - fitted["training_median"])))
                binary = {}
                for endpoint, bs in state["binary"].items():
                    if bs["status"] != "FROZEN":
                        binary[endpoint] = dict(bs)
                        continue
                    y = binary_labels(row.target_values, endpoint)
                    scores = linear_predict(bs["model"], matrices[modality])
                    probabilities = platt_predict(bs["calibrator"], scores)
                    binary[endpoint] = {"status": "EVALUATED", "score": scores.tolist(),
                        "uncalibrated_probability": expit(scores).tolist(), "calibrated_probability": probabilities.tolist(),
                        "metrics": binary_metrics(y, scores, probabilities, cutoff=bs["cutoff"]),
                        "fixed_0_5_metrics": binary_metrics(y, scores, probabilities, cutoff=.5),
                        "coherence_metrics": binary_metrics(y, -prediction, None, called=binary_labels(prediction, endpoint)),
                        "frozen_cutoff": bs["cutoff"]}
                strata = {}
                for q in range(4):
                    membership = burden_stratum == q
                    n = int(membership.sum())
                    strata[f"Q{q + 1}"] = {"n": n, "status": "SUPPRESSED_N_LT_40"} if n < 40 else {
                        "n": n, "status": "DESCRIPTIVE_TRAIN_DEFINED_BURDEN_STRATUM",
                        "metrics": regression_metrics(row.target_values[membership], prediction[membership], train_iqr=fitted["train_iqr"], lvef=p.name == "lvef")}
                predictions[modality] = {"prediction": prediction.tolist(), "metrics": metrics, "binary": binary,
                                        "information_burden_strata": strata}
            conditions[condition] = predictions
        output[p.name] = {"subject_ids": list(row.subject_ids), "study_ids": list(row.study_ids),
            "target_values": row.target_values.tolist(), "row_sha256": row_fingerprint(row),
            "train_iqr": fitted["train_iqr"], "unit": p.unit, "family": p.family,
            "denominators": dict(p.support_counts), "conditions": conditions,
            "training_information_burden_quartiles": quartiles,
            "burden_definition": "ORIGINAL_ALLOWED_CONTEXT_FRACTION_TRAIN_QUARTILE_CUTPOINTS_TIES_RIGHT_ALL_CONDITIONS"}
    return {"status": "PASS_LOCKED_TEST_EVALUATION", "spec_sha256": spec.sha256, "frozen_sha256": frozen_sha,
            "test_authority": authority, "test_release_sha256": release.release_receipt_sha256,
            "strict_panel": list(spec.strict_panel), "strict_panel_locked": spec.strict_panel_locked,
            "construct": spec.construct, "test_subject_roster": list(spec.test_subject_roster), "targets": output,
            "fixed_models": True, "train_plus_validation_refit": False, "test_loader_invocations": 1}
