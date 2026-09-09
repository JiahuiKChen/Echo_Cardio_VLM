"""Synthetic scientific regressions; never load clinical/project inputs."""
from dataclasses import asdict, replace
from pathlib import Path
import copy
import json
import sys
from unittest.mock import Mock, patch

import numpy as np
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lvef_revalidation_analysis as a
import lvef_revalidation_inference as inf


class SyntheticAuthority:
    def __init__(self):
        self.calls = []
        self.used = set()

    def __call__(self, *, stage, spec_sha256, frozen_sha256, release):
        self.calls.append(stage)
        if stage == "test":
            a.require(release.release_receipt_sha256 not in self.used, "TEST_RELEASE_ALREADY_CONSUMED")
            self.used.add(release.release_receipt_sha256)
        return {"status": "PASS_ANALYSIS_AUTHORITY", "stage": stage, "spec_sha256": spec_sha256,
                "frozen_sha256": frozen_sha256, "authority_receipt_sha256": "a" * 64}


def synthetic_inputs():
    rng = np.random.default_rng(803)
    names = ("raw_label", "alias_label", "formula_label", "family_label", "alias_label__missing",
             "context_complete", "context_missing", "constant", "low_support")
    datasets = {s: {} for s in ("train", "val", "test")}
    for split, n in (("train", 160), ("val", 80), ("test", 80)):
        vision = rng.normal(size=(n, 3))
        ef = np.tile([28., 32., 38., 40., 45., 55., 60., 64.], n // 8)
        rng.shuffle(ef)
        x = rng.normal(size=(n, len(names)))
        x[:, 5] += vision[:, 0] * .1
        x[::3, 6] = np.nan
        x[:, 7] = 1
        x[:, 8] = np.nan
        x[:10, 8] = rng.normal(size=10)
        x[7, 5:7] = np.nan  # all permitted eligible context missing: retain the row
        for target, y in (("lvef", ef), ("lvot_vti", 12 + vision[:, 1] * 2 + rng.normal(size=n))):
            raw = x.copy()
            raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3] = y, y, y * 2, y + 1
            common = dict(subject_ids=tuple(f"SYN_{split}_SUBJECT_{j}" for j in range(n)),
                          study_ids=tuple(f"SYN_{split}_STUDY_{j}" for j in range(n)), split=split,
                          target=target, target_values=y)
            datasets[split][target] = {
                "vision_only": a.ModalityRows(**common, vision=vision),
                "structured_only": a.ModalityRows(**common, structured=raw, structured_names=names),
                "early_fusion": a.ModalityRows(**common, vision=vision, structured=raw, structured_names=names)}
    policies = []
    for target in ("lvef", "lvot_vti"):
        binary = {}
        if target == "lvef":
            for endpoint in a.ENDPOINTS:
                binary[endpoint] = {}
                for split, data in datasets.items():
                    labels = a.binary_labels(data[target]["early_fusion"].target_values, endpoint)
                    binary[endpoint][split] = (int(labels.sum()), int(len(labels) - labels.sum()))
        policies.append(a.TargetPolicy(target, "EF_percent" if target == "lvef" else "cm", "anchor" if target == "lvef" else "flow",
            ("context_complete", "context_missing", "constant", "low_support"), ("raw_label",),
            ("alias_label", "alias_label__missing"), ("formula_label",), (), ("family_label",),
            {s: a.row_fingerprint(d[target]["early_fusion"]) for s, d in datasets.items()},
            {s: len(d[target]["early_fusion"].subject_ids) for s, d in datasets.items()}, binary, True))
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/lvef_multitask_revalidation.yaml").read_text())
    spec = a.AnalysisSpec(tuple(policies), ("lvot_vti",), True, "strict",
        datasets["test"]["lvef"]["early_fusion"].subject_ids,
        {key: "b" * 64 for key in ("source_completion", "input_audit", "clinical_panel", "dependency_registry", "sap", "config", "environment", "owner_authorization", "safety")},
        a.validate_prescription(config))
    return spec, datasets


@pytest.fixture(scope="module")
def fitted():
    spec, data = synthetic_inputs()
    authority = SyntheticAuthority()
    frozen = a.select_development(spec, data["train"], data["val"], authorize=authority)
    return spec, data, frozen


def test_prescription_rejects_refit_grid_seed_and_indicator_drift():
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/lvef_multitask_revalidation.yaml").read_text())
    assert len(a.validate_prescription(config)) == 64
    for path, value in ((["models", "refit_train_plus_val"], True), (["models", "ridge_alpha_grid"], [1]),
                        (["models", "binary_logistic", "random_seed"], 42),
                        (["structured_preprocessing", "no_missing_indicator_sensitivity_required"], False),
                        (["paired_bootstrap", "replicates"], 99)):
        changed = copy.deepcopy(config)
        node = changed
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        with pytest.raises(a.AnalysisError):
            a.validate_prescription(changed)


def test_closed_mask_precedes_eligibility_imputation_scaling_and_indicators():
    spec, data = synthetic_inputs()
    p, row = spec.policies[0], data["train"]["lvef"]["early_fusion"]
    transform = a.fit_transform(row, p)
    changed = row.structured.copy()
    changed[:, :5] = np.inf
    assert a.fit_transform(replace(row, structured=changed), p) == transform
    assert transform["features"] == ["context_complete", "context_missing"]
    assert transform["indicator_indices"] == [0, 1]
    assert not p.prohibited & set(transform["features"])
    assert transform["excluded_allowed_features"] == ["constant", "low_support"]
    with pytest.raises(a.AnalysisError, match="PROHIBITED"):
        a.fit_transform(row, replace(p, allowed_predictors=(*p.allowed_predictors, "alias_label")))


def test_all_missing_context_row_stays_and_shared_transform_is_exact():
    spec, data = synthetic_inputs()
    row, p = data["train"]["lvef"]["early_fusion"], spec.policies[0]
    transform = a.fit_transform(row, p)
    matrices = a.apply_transform(row, transform)
    assert all(len(x) == len(row.subject_ids) for x in matrices.values())
    assert np.isfinite(matrices["structured_only"][7]).all()
    assert np.array_equal(matrices["early_fusion"][:, :3], matrices["vision_only"])
    assert np.array_equal(matrices["early_fusion"][:, 3:], matrices["structured_only"])
    assert matrices["structured_only"][7, -2:].tolist() == [1, 1]
    without = a.apply_transform(row, transform, indicators=False)
    assert without["structured_only"].shape[1] == 2


def test_training_complete_feature_has_no_indicator_even_if_test_is_missing():
    spec, data = synthetic_inputs()
    row = data["train"]["lvef"]["early_fusion"]
    raw = row.structured.copy()
    raw[7, 5] = 0.
    transform = a.fit_transform(replace(row, structured=raw), spec.policies[0])
    assert transform["indicator_indices"] == [1]
    test = data["test"]["lvef"]["early_fusion"]
    test_raw = test.structured.copy()
    test_raw[:, 5] = np.nan
    matrices = a.apply_transform(replace(test, structured=test_raw), transform)
    assert matrices["structured_only"].shape[1] == 3


@pytest.mark.parametrize("field", ["target_values", "subject_ids", "study_ids", "split", "target"])
def test_cross_modality_label_row_or_split_mismatch_fails_before_fit(field):
    spec, data = synthetic_inputs()
    row = data["val"]["lvot_vti"]["vision_only"]
    replacements = {"target_values": row.target_values[::-1], "subject_ids": row.subject_ids[::-1],
                    "study_ids": row.study_ids[::-1], "split": "test", "target": "lvef"}
    data["val"]["lvot_vti"]["vision_only"] = replace(row, **{field: replacements[field]})
    with patch.object(a, "fit_selected_model") as model:
        with pytest.raises(a.AnalysisError):
            a.select_development(spec, data["train"], data["val"], authorize=SyntheticAuthority())
        model.assert_not_called()


def test_feature_input_mismatch_and_duplicate_subject_are_not_equal_counts():
    spec, data = synthetic_inputs()
    p = spec.policies[0]
    group = data["train"]["lvef"].copy()
    row = group["vision_only"]
    group["vision_only"] = replace(row, vision=row.vision + 1)
    with pytest.raises(a.AnalysisError, match="VISION"):
        a.validate_common_rows(group, p, "train")
    group["vision_only"] = replace(row, subject_ids=(row.subject_ids[1], *row.subject_ids[1:]))
    with pytest.raises(a.AnalysisError, match="MULTIPLICITY"):
        a.validate_common_rows(group, p, "train")


def test_preprocessing_never_learns_validation_values():
    spec, data = synthetic_inputs()
    tr, val = data["train"]["lvef"]["early_fusion"], data["val"]["lvef"]["early_fusion"]
    with pytest.raises(a.AnalysisError, match="OUTSIDE_TRAIN"):
        a.fit_transform(val, spec.policies[0])
    state = a.fit_transform(tr, spec.policies[0])
    initial = a.canonical_bytes(state)
    transformed = a.apply_transform(replace(val, vision=val.vision + 1e8, structured=val.structured + 1e8), state)
    assert np.max(transformed["early_fusion"]) > 1e7
    assert a.canonical_bytes(state) == initial
    assert state["training_rows"] == 160


def test_exact_ties_regularize_more_full_precision_ties_only():
    scores = dict.fromkeys(a.GRID, 1.)
    assert a.select_grid(scores, kind="ridge") == 1000.
    assert a.select_grid(scores, kind="logistic") == .001
    scores[1.] = np.nextafter(1., 0.)
    assert a.select_grid(scores, kind="ridge") == 1.
    scores[1.] = np.nextafter(1., 2.)
    assert a.select_grid(scores, kind="logistic") == 1.


def test_platt_unweighted_validation_only_and_no_fallback():
    rng = np.random.default_rng(8)
    scores = rng.normal(size=300)
    y = rng.binomial(1, a.expit(.3 + .6 * scores))
    state = a.fit_platt(scores, y, split="val")
    assert state["weighting"] == "unweighted"
    assert state["fit_rows"] == 300 and state["validation_reused_after_selection"]
    p = a.platt_predict(state, scores)
    assert np.all((p > 0) & (p < 1))
    for split in ("train", "test"):
        with pytest.raises(a.AnalysisError, match="OUTSIDE_VALIDATION"):
            a.fit_platt(scores, y, split=split)
    with pytest.raises(a.AnalysisError):
        a.fit_platt(scores, np.ones(len(y)), split="val")
    with pytest.raises(a.AnalysisError, match="DEGENERATE"):
        a.fit_platt(np.zeros(len(y)), y, split="val")


def test_youden_ties_and_exact_inequality_boundaries():
    p = np.array([.2, .2, .8, .8])
    y = np.array([0, 1, 0, 1])
    assert a.select_youden(p, y, split="val") == .2  # J tie: greater sensitivity, then same-call larger cutoff
    with pytest.raises(a.AnalysisError):
        a.select_youden(p, y, split="test")
    values = np.array([39.999, 40., 49.999, 50.])
    assert a.binary_labels(values, "lvef_lt_40").tolist() == [1, 0, 0, 0]
    assert a.binary_labels(values, "lvef_le_40").tolist() == [1, 1, 0, 0]
    assert a.binary_labels(values, "lvef_lt_50").tolist() == [1, 1, 1, 0]


def test_fitted_bundle_is_training_only_and_sensitivity_is_mandatory(fitted):
    spec, data, frozen = fitted
    assert frozen.payload["test_access_count"] == 0
    for target in frozen.payload["targets"].values():
        assert set(target["conditions"]) == {"primary", "no_indicators"}
        assert set(target["conditions"]["primary"]) == set(a.MODALITIES)
        assert set(target["conditions"]["no_indicators"]) == {"structured_only", "early_fusion"}
        for states in target["conditions"].values():
            for state in states.values():
                assert state["ridge"]["fit_rows"] == 160
                assert state["ridge"]["refit_train_plus_val"] is False
                for binary in state["binary"].values():
                    assert binary["model"]["fit_rows"] == 160
                    assert binary["calibrator"]["fit_rows"] == 80
    body, sha = a.freeze_analysis(frozen)
    assert a.load_frozen_analysis(body, expected_sha256=sha).sha256 == sha
    assert a.freeze_analysis(a.load_frozen_analysis(body, expected_sha256=sha))[0] == body
    with pytest.raises(a.AnalysisError, match="HASH"):
        a.load_frozen_analysis(body + b" ", expected_sha256=sha)


def test_serialized_selected_coefficients_equal_independent_training_only_fit(fitted):
    spec, data, frozen = fitted
    target = frozen.payload["targets"]["lvef"]
    tr, va = data["train"]["lvef"]["early_fusion"], data["val"]["lvef"]["early_fusion"]
    tx, vx = a.apply_transform(tr, target["transform"]), a.apply_transform(va, target["transform"])
    state = target["conditions"]["primary"]["early_fusion"]
    ridge = a.Ridge(alpha=state["ridge"]["selected_parameter"], fit_intercept=True, solver="lsqr", tol=1e-8, max_iter=10000)
    ridge.fit(tx["early_fusion"], tr.target_values)
    assert np.allclose(ridge.predict(vx["early_fusion"]), a.linear_predict(state["ridge"], vx["early_fusion"]), rtol=0, atol=1e-12)
    logistic = state["binary"]["lvef_lt_40"]["model"]
    expected = a.LogisticRegression(C=logistic["selected_parameter"], penalty="l2", solver="liblinear",
        max_iter=5000, tol=1e-4, fit_intercept=True, random_state=a.SEED, class_weight="balanced")
    expected.fit(tx["early_fusion"], a.binary_labels(tr.target_values, "lvef_lt_40"))
    assert np.allclose(expected.decision_function(vx["early_fusion"]), a.linear_predict(logistic, vx["early_fusion"]), rtol=0, atol=1e-12)


def test_test_loader_inaccessible_until_bound_authority_and_single_use_release(fitted):
    spec, data, frozen = fitted
    loader = Mock(return_value=data["test"])
    release = a.TestRelease(spec.sha256, frozen.sha256, "c" * 64)
    with pytest.raises(a.AnalysisError):
        a.evaluate_locked_test(frozen, release=replace(release, frozen_sha256="d" * 64), authorize=SyntheticAuthority(), test_loader=loader)
    loader.assert_not_called()
    with pytest.raises(a.AnalysisError):
        a.evaluate_locked_test(frozen, release=release, authorize=lambda **_: {}, test_loader=loader)
    loader.assert_not_called()
    authority = SyntheticAuthority()
    with patch.object(a, "fit_selected_model") as model:
        evaluated = a.evaluate_locked_test(frozen, release=release, authorize=authority, test_loader=loader)
        model.assert_not_called()
    assert evaluated["status"] == "PASS_LOCKED_TEST_EVALUATION"
    assert loader.call_count == 1
    with pytest.raises(a.AnalysisError, match="ALREADY_CONSUMED"):
        a.evaluate_locked_test(frozen, release=release, authorize=authority, test_loader=loader)
    assert loader.call_count == 1
    assert evaluated["targets"]["lvef"]["denominators"] == {"train": 160, "val": 80, "test": 80}
    for target in evaluated["targets"].values():
        assert set(target["conditions"]) == set(a.EVALUATION_CONDITIONS)
        for condition in a.CONTEXT_SENSITIVITIES:
            assert set(target["conditions"][condition]) == set(a.MODALITIES)
            assert target["conditions"][condition]["vision_only"]["prediction"] == target["conditions"]["primary"]["vision_only"]["prediction"]
            for state in target["conditions"][condition].values():
                assert len(state["prediction"]) == 80
                strata = state["information_burden_strata"]
                assert sum(v["n"] for v in strata.values()) == 80
                assert all(("metrics" not in v) == (v["n"] < 40) for v in strata.values())
    assert json.dumps(evaluated, allow_nan=False)


def test_unlocked_panel_support_and_any_target_failure_precede_fitting():
    spec, data = synthetic_inputs()
    variants = [replace(spec, strict_panel_locked=False), replace(spec, strict_panel=()),
                replace(spec, strict_panel=("lvef", "lvot_vti")),
                replace(spec, policies=(spec.policies[0], replace(spec.policies[1], dependencies_resolved=False)))]
    for variant in variants:
        with patch.object(a, "fit_selected_model") as fit:
            with pytest.raises(a.AnalysisError):
                a.select_development(variant, data["train"], data["val"], authorize=SyntheticAuthority())
            fit.assert_not_called()
    bad = copy.deepcopy(data)
    for modality in ("early_fusion", "structured_only"):
        row = bad["train"]["lvot_vti"][modality]
        x = row.structured.copy()
        x[:, 5:] = np.nan
        bad["train"]["lvot_vti"][modality] = replace(row, structured=x)
    with patch.object(a, "fit_selected_model") as fit:
        with pytest.raises(a.AnalysisError, match="NO_ELIGIBLE"):
            a.select_development(spec, bad["train"], bad["val"], authorize=SyntheticAuthority())
        fit.assert_not_called()


def test_different_subject_cannot_own_same_study_across_splits_before_fitting():
    spec, data = synthetic_inputs()
    changed_policies = []
    for policy in spec.policies:
        for modality, row in data["val"][policy.name].items():
            data["val"][policy.name][modality] = replace(row, study_ids=(data["train"][policy.name][modality].study_ids[0], *row.study_ids[1:]))
        changed_policies.append(replace(policy, row_fingerprints={**policy.row_fingerprints,
            "val": a.row_fingerprint(data["val"][policy.name]["early_fusion"])}))
    with patch.object(a, "fit_selected_model") as fit:
        with pytest.raises(a.AnalysisError, match="STUDY_SUBJECT_OWNERSHIP"):
            a.select_development(replace(spec, policies=tuple(changed_policies)), data["train"], data["val"], authorize=SyntheticAuthority())
        fit.assert_not_called()


def test_binary_primary_floor_blocks_all_fits_and_secondary_is_explicit_unsupported():
    spec, data = synthetic_inputs()
    binary = copy.deepcopy(spec.policies[0].binary_class_counts)
    binary["lvef_lt_40"]["test"] = (15, 65)
    bad = replace(spec, policies=(replace(spec.policies[0], binary_class_counts=binary), spec.policies[1]))
    with patch.object(a, "fit_selected_model") as fit:
        with pytest.raises(a.AnalysisError, match="PRIMARY_BINARY_SUPPORT_FAILED"):
            a.select_development(bad, data["train"], data["val"], authorize=SyntheticAuthority())
        fit.assert_not_called()
    binary = copy.deepcopy(spec.policies[0].binary_class_counts)
    binary["lvef_lt_50"]["test"] = (65, 15)
    supported = a._binary_support(replace(spec.policies[0], binary_class_counts=binary),
        data["train"]["lvef"]["early_fusion"], data["val"]["lvef"]["early_fusion"])
    assert supported == {"lvef_lt_40": True, "lvef_le_40": True, "lvef_lt_50": False}


def test_malformed_frozen_transforms_or_sensitivity_block_before_test_loader(fitted):
    spec, data, frozen = fitted
    for mode in ("masked_feature", "missing_sensitivity", "refit", "mask_seed", "train_patterns"):
        payload = copy.deepcopy(frozen.payload)
        target = payload["targets"]["lvef"]
        if mode == "masked_feature":
            target["transform"]["features"][0] = "alias_label"
        elif mode == "missing_sensitivity":
            del target["conditions"]["no_indicators"]
        elif mode == "refit":
            target["conditions"]["primary"]["vision_only"]["ridge"]["refit_train_plus_val"] = True
        elif mode == "mask_seed":
            payload["test_context_sensitivities"]["random_10"]["seed"] = 10
        else:
            target["missingness_patterns"]["fitted_split"] = "test"
        invalid = a.FrozenAnalysis(payload)
        loader = Mock(return_value=data["test"])
        with pytest.raises(a.AnalysisError):
            a.evaluate_locked_test(invalid, release=a.TestRelease(spec.sha256, invalid.sha256, "f" * 64),
                                  authorize=SyntheticAuthority(), test_loader=loader)
        loader.assert_not_called()


def test_simulated_missingness_uses_training_patterns_and_updates_only_allowed_indicators():
    spec, data = synthetic_inputs()
    tr, va = data["train"]["lvef"]["early_fusion"], data["val"]["lvef"]["early_fusion"]
    state = a.fit_transform(tr, spec.policies[0])
    patterns = a.fit_missingness_patterns(tr, state)
    with pytest.raises(a.AnalysisError):
        a.fit_missingness_patterns(va, state)
    masked = a.simulate_missingness(va, state, scheme="single_field", fields=("context_complete",))
    assert a.row_fingerprint(masked) == a.row_fingerprint(va)
    assert np.array_equal(masked.structured[:, :5], va.structured[:, :5])
    assert np.all(a.apply_transform(masked, state)["structured_only"][:, 2] == 1)
    with pytest.raises(a.AnalysisError, match="NOT_ALLOWED"):
        a.simulate_missingness(va, state, scheme="single_field", fields=("alias_label",))
    first = a.simulate_missingness(va, state, scheme="training_joint_pattern", empirical=patterns)
    second = a.simulate_missingness(va, state, scheme="training_joint_pattern", empirical=patterns)
    assert np.array_equal(first.structured, second.structured, equal_nan=True)
    assert a.row_fingerprint(first) == a.row_fingerprint(va)


def test_metrics_native_units_calibration_undefined_and_lvef_tolerances():
    y, p = np.array([30., 40., 50., 60.]), np.array([35., 35., 50., 68.])
    result = inf.regression_metrics(y, p, train_iqr=20, lvef=True)
    assert result["mae"] == 4.5 and result["normalized_mae"] == .225
    assert result["tolerance_coverage"] == {"4.0": .25, "5.0": .75, "8.0": 1.}
    assert inf.regression_metrics(y, np.ones(4), train_iqr=20)["calibration_slope"] is None
    assert inf.subgroup_metrics(y, p, train_iqr=20)["status"] == "SUPPRESSED_N_LT_40"
    binary = inf.binary_metrics([0, 1, 0, 1], [.1, .9, .2, .7], [.2, .8, .3, .6], cutoff=.5)
    assert binary["auroc"] == 1 and binary["tp"] == binary["tn"] == 2
    assert binary["calibrated_brier_score"] == pytest.approx(.0825)


def tiny_task(subjects, y, errors, iqr=2):
    y = np.asarray(y, float)
    return inf.TaskPredictions(tuple(subjects), y, {m: y + np.asarray(errors[j]) for j, m in enumerate(a.MODALITIES)}, iqr)


def test_shared_draws_preserve_modalities_tasks_and_undefined_task_invalidates_macro():
    roster = ("SYN_A", "SYN_B", "SYN_C")
    tasks = {"a": tiny_task(roster[:2], [1, 2], ([2, 0], [1, 3], [0, 1])),
             "b": tiny_task(roster[2:], [4], ([6], [4], [2]))}
    weights = np.array([[1, 1, 1], [3, 0, 0], [0, 0, 3], [0, 2, 1]])
    result = inf.panel_bootstrap_from_multiplicities(tasks, locked_panel=("a", "b"), roster=roster, multiplicities=weights)
    effect = result["macro_contrasts"]["early_fusion_minus_vision_only"]
    assert effect["valid_replicates"] == 2 and effect["undefined_replicates"] == 2
    assert effect["effect"] == pytest.approx(-1.125)
    assert result["locked_task_count"] == 2
    assert result["task_native_mae_contrasts"]["a"]["early_fusion_minus_vision_only"]["valid_replicates"] == 3
    with pytest.raises(a.AnalysisError):
        inf.panel_bootstrap_from_multiplicities({"a": tasks["a"]}, locked_panel=("a", "b"), roster=roster, multiplicities=weights)


def test_bootstrap_default_10000_seed_and_nonstratified_single_class_replicates():
    roster = ("SYN_A", "SYN_B")
    first = inf.subject_multiplicities(roster)
    assert first.shape == (10000, 2) and np.array_equal(first, inf.subject_multiplicities(roster))
    assert np.all(first.sum(axis=1) == 2) and np.any(first[:, 0] == 2)
    task = inf.TaskPredictions(roster, np.array([0, 1]), {m: np.array([0., 1.]) for m in a.MODALITIES}, 1.)
    draws = np.array([[2, 0], [0, 2], [1, 1]])
    result = inf.paired_metric_bootstrap(task, metric=a.roc_auc_score, multiplicities=draws, binary=True)
    assert result["one_class_replicates"] == 2
    assert result["contrasts"]["early_fusion_minus_vision_only"]["valid_replicates"] == 1


def test_real_ten_thousand_panel_replicates_share_subject_identity_across_reordered_tasks():
    roster = ("SYN_A", "SYN_B")
    tasks = {"first": tiny_task(roster, [1, 2], ([0, 4], [2, 2], [4, 0]), iqr=1),
             "second": tiny_task(roster[::-1], [3, 4], ([0, 4], [2, 2], [4, 0]), iqr=1)}
    result = inf.panel_bootstrap_from_multiplicities(tasks, locked_panel=("first", "second"),
        roster=roster, multiplicities=inf.subject_multiplicities(roster))
    # Opposite subject-specific errors cancel on every shared draw. Independent
    # task resampling or pairing by row position would create a spurious interval.
    for value in result["macro_contrasts"].values():
        assert value["valid_replicates"] == 10000 and value["undefined_replicates"] == 0
        assert value["effect"] == 0 and value["interval"] == [0., 0.] and value["p_value"] == 1.


def test_paired_pvalue_centering_plus_one_holm_and_strict_four_claim_requirement():
    summary = inf.paired_summary(2., [0., 1., 2., 3., 4., np.nan])
    assert summary["p_value"] == .5 and summary["valid_replicates"] == 5
    assert summary["interval"] == pytest.approx([.1, 3.9])
    p = dict(zip(inf.CORE_CLAIMS, [.01, .04, .03, .2]))
    assert list(inf.core_holm(p, strict_panel=("x",), strict_panel_locked=True)["adjusted_p_values"].values()) == [.04, .09, .09, .2]
    for values, panel, locked in ((dict(list(p.items())[:2]), ("x",), True), (p, (), True), (p, ("x",), False)):
        with pytest.raises(a.AnalysisError):
            inf.core_holm(values, strict_panel=panel, strict_panel_locked=locked)
    assert inf.practical_margin([-.2, .3], delta=None) == "MARGIN_UNRESOLVED_NO_WIN_TIE_LOSS_CLAIM"
    assert inf.practical_margin([-1.1, -.1], delta=1) == "LOWER_ERROR_MAGNITUDE_NOT_ESTABLISHED"


def test_complete_metric_collection_reuses_rows_and_retains_undefined_metrics():
    seen = {m: [] for m in a.MODALITIES}
    def scorer(modality):
        def score(idx):
            seen[modality].append(idx.tolist())
            return {"mae": float(np.mean(idx)) if len(idx) else np.nan,
                    "undefined_correlation": np.nan}
        return score
    result = inf.paired_metric_collection_from_multiplicities(subjects=("s1", "s2"), roster=("s1", "s2", "s3"),
        multiplicities=np.array([[1, 1, 1], [0, 0, 3], [2, 0, 1]]), scorers={m: scorer(m) for m in a.MODALITIES})
    assert seen["vision_only"] == seen["structured_only"] == seen["early_fusion"]
    contrast = result["contrasts"]["early_fusion_minus_vision_only"]
    assert contrast["mae"]["valid_replicates"] == 2
    assert contrast["undefined_correlation"]["status"] == "OBSERVED_METRIC_UNDEFINED"
    assert contrast["undefined_correlation"]["undefined_replicates"] == 3
    model = result["model_metrics"]["early_fusion"]["mae"]
    assert model["estimate"] == .5 and model["valid_replicates"] == 2
    assert model["supports_paired_superiority_claim"] is False and "p_value" not in model


def test_complete_metric_dispatch_includes_every_endpoint_and_no_indicator_sensitivity(fitted):
    spec, data, frozen = fitted
    evaluated = a.evaluate_locked_test(frozen, release=a.TestRelease(spec.sha256, frozen.sha256, "e" * 64),
                                      authorize=SyntheticAuthority(), test_loader=lambda: data["test"])
    # Exercise the real complete dispatcher/scorers with three explicit draws;
    # default production generator is separately asserted to emit exactly10,000.
    def small_draws(roster):
        n = len(roster)
        return np.array([np.ones(n, int), np.r_[n, np.zeros(n - 1, int)], np.ones(n, int)])
    with patch.object(inf, "subject_multiplicities", side_effect=small_draws):
        result = inf.complete_paired_intervals(evaluated, condition="no_indicators")
        primary = inf.paired_inference(evaluated)
    ef = result["targets"]["lvef"]
    assert set(ef["binary"]) == set(a.ENDPOINTS)
    continuous = ef["continuous"]["contrasts"]["early_fusion_minus_vision_only"]
    assert {"mae", "rmse", "normalized_mae", "calibration_slope", "tolerance_5.0", "spearman_correlation"} <= set(continuous)
    binary = ef["binary"]["lvef_lt_40"]["contrasts"]["early_fusion_minus_vision_only"]
    assert {"auroc", "average_precision", "calibrated_brier_score", "calibration_intercept", "sensitivity", "coherence_auroc", "fixed_0_5_f1"} <= set(binary)
    assert binary["auroc"]["undefined_replicates"] == 1
    assert ef["binary"]["lvef_lt_40"]["one_class_replicates"] == 1
    assert len(primary["core_multiplicity"]["adjusted_p_values"]) == 4
    summary = inf.summarize_panel(evaluated, condition="no_indicators")
    assert all(value["target_count"] == 1 for value in summary.values())
    with pytest.raises(a.AnalysisError, match="SENSITIVITY_CANNOT_ACTIVATE_CORE"):
        inf.paired_inference(evaluated, condition="no_indicators")
    for condition in ("primary", "no_indicators"):
        for state in evaluated["targets"]["lvef"]["conditions"][condition].values():
            state["binary"]["lvef_lt_50"] = {"status": "UNSUPPORTED_CLASS_COUNTS"}
    with patch.object(inf, "subject_multiplicities", side_effect=small_draws):
        unsupported = inf.paired_inference(evaluated, endpoint="lvef_lt_50", activate_core=False)
    assert unsupported["secondary_logistic_auroc"] == {"status": "UNSUPPORTED_CLASS_COUNTS"}
    assert unsupported["secondary_binary_holm"]["status"] == "DESCRIPTIVE_NO_ADDITIONAL_HOLM_FAMILY"
