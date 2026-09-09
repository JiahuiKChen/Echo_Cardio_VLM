"""Synthetic renderer fixtures only; no estimates are written to manuscript docs."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lvef_revalidation_analysis as a
import lvef_revalidation_inference as inf
import render_lvef_revalidation_results as r
from lvef_multitask_audit_utils import assert_aggregate_safe_json
from test_lvef_revalidation_analysis import SyntheticAuthority, fitted


@pytest.fixture(scope="module")
def aggregate_sources(fitted):
    spec, data, frozen = fitted
    evaluation = a.evaluate_locked_test(frozen, release=a.TestRelease(spec.sha256, frozen.sha256, "f" * 64),
        authorize=SyntheticAuthority(), test_loader=lambda: data["test"])
    report = {"status": "PASS_PRIVATE_PAIRED_REPORT", "spec_sha256": spec.sha256, "conditions": {}}
    # Degenerate synthetic draw collections exercise render contracts. They are
    # not scientific bootstrap results and remain only in temporary test outputs.
    for condition in a.EVALUATION_CONDITIONS:
        targets = {}
        for target, row in evaluation["targets"].items():
            states = dict(row["conditions"][condition])
            if condition == "no_indicators":
                states["vision_only"] = row["conditions"]["primary"]["vision_only"]
            models = {m: {key: inf.model_interval(states[m]["metrics"][key] if states[m]["metrics"][key] is not None else np.nan,
                np.full(10000, states[m]["metrics"][key] if states[m]["metrics"][key] is not None else np.nan)) for key in r.METRICS} for m in a.MODALITIES}
            contrasts = {}
            for left, right in inf.CONTRASTS:
                contrasts[f"{left}_minus_{right}"] = {}
                for key in ("mae", "normalized_mae"):
                    difference = states[left]["metrics"][key] - states[right]["metrics"][key]
                    contrasts[f"{left}_minus_{right}"][key] = inf.paired_summary(difference, np.full(10000, difference))
            targets[target] = {"continuous": {"model_metrics": models, "contrasts": contrasts}}
            if target == "lvef":
                targets[target]["binary"] = {endpoint: {"model_metrics": {m: {"auroc": inf.model_interval(states[m]["binary"][endpoint]["metrics"]["auroc"],
                    np.full(10000, states[m]["binary"][endpoint]["metrics"]["auroc"]))} for m in a.MODALITIES}} for endpoint in a.ENDPOINTS}
        report["conditions"][condition] = {"secondary_intervals": {"replicates": 10000, "condition": condition, "targets": targets},
            "primary_inference": {"core_multiplicity": {"family": "core_global_four_claim", "adjusted_p_values": dict.fromkeys(inf.CORE_CLAIMS, 1.), "strict_target_count": 1}},
            "panel_summary": inf.summarize_panel(evaluation, condition=condition)}
    audit = {"artifact_type": "lvef_revalidation_inputs_v1", "all_missing_allowed_structured_rows_retained": True,
        "counts": {"selected_studies": 320, "selected_subjects": 320, "imaging_eligible": 320, "no_cine": 0, "clip_embeddings": 640},
        "selected_split_counts": {"train": 160, "val": 80, "test": 80}, "imaging_split_counts": {"train": 160, "val": 80, "test": 80},
        "lvef_common_counts": {"train": 160, "val": 80, "test": 80}, "exact40_counts": {"train": 20, "val": 10, "test": 10}}
    return evaluation, report, audit


def test_projector_removes_all_patient_rows_and_requires_bound_safety(aggregate_sources, tmp_path):
    evaluation, report, audit = aggregate_sources
    candidate = r.extract_aggregate_bundle(evaluation, report, input_audit=audit)
    assert_aggregate_safe_json(candidate)
    encoded = json.dumps(candidate)
    assert "SYN_train_SUBJECT" not in encoded and "SYN_test_SUBJECT" not in encoded
    assert "target_values" not in encoded and '"prediction"' not in encoded and '"score"' not in encoded
    assert len(candidate["rows"]) == 36
    with pytest.raises(a.AnalysisError, match="VALIDATED_BUNDLE"):
        r.render(tmp_path / "refused", bundle=candidate)
    assert not (tmp_path / "refused").exists()
    safety = {"status": "PASS_REVALIDATION_AGGREGATE_SAFETY", "candidate_sha256": a.digest(candidate), "safety_receipt_sha256": "a" * 64}
    bundle = r.seal_aggregate_bundle(candidate, safety)
    assert r.validate_bundle(bundle) == candidate
    with pytest.raises(a.AnalysisError):
        r.seal_aggregate_bundle(candidate, {**safety, "candidate_sha256": "f" * 64})
    manifest = r.render(tmp_path / "figures", bundle=bundle)
    assert manifest["status"] == "PASS_RENDERED_VALIDATED_AGGREGATES"
    assert len(manifest["artifacts"]) == 12
    assert (tmp_path / "figures/complete_modality_results.validated.csv").exists()
    assert (tmp_path / "figures/lvef_paired_effects.validated.svg").stat().st_size > 1000


@pytest.mark.parametrize("mutation", ["rows", "label", "order", "row_hash", "bootstrap", "core"])
def test_missing_mismatched_or_unreviewed_results_are_refused(aggregate_sources, mutation):
    evaluation, report, audit = aggregate_sources
    candidate = r.extract_aggregate_bundle(evaluation, report, input_audit=audit)
    changed = copy.deepcopy(candidate)
    if mutation == "rows":
        changed["rows"].pop()
    elif mutation == "label":
        changed["rows"][0]["subject_ids"] = ["SYN_PRIVATE"]
    elif mutation == "order":
        changed["rows"][0], changed["rows"][1] = changed["rows"][1], changed["rows"][0]
    elif mutation == "row_hash":
        changed["rows"][0]["common_row_sha256"] = "d" * 64
    elif mutation == "bootstrap":
        changed["rows"][0]["metric_intervals"]["mae"]["replicates"] = 100
    else:
        changed["core_holm"].pop(inf.CORE_CLAIMS[-1])
    with pytest.raises(a.AnalysisError):
        r.validate_candidate(changed)


def test_pending_templates_have_no_fabricated_results_and_are_reproducible(tmp_path):
    first = r.render(tmp_path / "templates")
    assert first["status"] == "PENDING_RESULT_TEMPLATES" and first["bundle_sha256"] is None
    assert len(first["artifacts"]) == 10
    assert first == r.render(tmp_path / "templates")
    for path in (tmp_path / "templates").glob("*.svg"):
        text = path.read_text()
        assert "PENDING" in text or "Prespecified workflow" in text
        assert "SYN_" not in text
    assert first["poster_export_authorized"] is False


def test_incomplete_private_report_and_changed_funnel_are_refused(aggregate_sources):
    evaluation, report, audit = aggregate_sources
    with pytest.raises(a.AnalysisError):
        r.extract_aggregate_bundle({**evaluation, "strict_panel_locked": False}, report, input_audit=audit)
    changed = copy.deepcopy(report)
    del changed["conditions"]["random_10"]
    with pytest.raises(a.AnalysisError):
        r.extract_aggregate_bundle(evaluation, changed, input_audit=audit)
    with pytest.raises(a.AnalysisError):
        r.extract_aggregate_bundle(evaluation, report, input_audit={**audit, "lvef_common_counts": {"train": 160, "val": 80, "test": 81}})
