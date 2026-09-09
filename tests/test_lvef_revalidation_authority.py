"""Synthetic evidence and staged access regressions; no SCC or clinical data."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import lvef_revalidation_authority as a
import run_lvef_revalidation as runner
import audit_lvef_analysis_readiness as readiness
from test_lvef_analysis_readiness import _clinical, _technical
from test_lvef_revalidation_analysis import synthetic_inputs, fitted
from test_render_lvef_revalidation_results import aggregate_sources


def write(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    body = value if isinstance(value, bytes) else a.canonical(value)
    path.write_bytes(body)
    path.chmod(0o600)
    return path


def spec_file(root, **bindings):
    spec, _ = synthetic_inputs()
    updated = dict(spec.bindings, source_completion=a.C3_COMPLETION, **bindings)
    return write(root / "spec.json", asdict(replace(spec, bindings=updated)))


def test_boolean_pass_receipts_cannot_authorize_fitting():
    gates = {name: {"status": "PASS", "gate": name, "spec_sha256": "a" * 64,
                   "maintained_signoff_validator_passed": True, "strict_panel_locked": True,
                   "new_test_performance_used": False} for name in a.REQUIRED_GATES}
    with pytest.raises(a.AuthorityError, match="ANALYSIS_REPLAYABLE_GATE_REQUIRED"):
        a.validate_prerequisites(gates, spec_sha256="a" * 64)


def test_owner_permission_does_not_supply_a_clinical_decision():
    with _clinical(False) as (root, rows, _, _):
        root, rows = root.resolve(), rows.resolve()
        rows.chmod(0o600)
        spec = spec_file(root)
        owner = a.owner_authorization(request_sha256=a.OWNER_REQUEST,
            authorization_date="2026-09-09", source_reference="synthetic test of recorded instruction")
        a.validate_owner(owner)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_CLINICAL_SIGNOFF_REQUIRED"):
            a.create_gate("clinical_signoff", spec_path=spec,
                parameters={"packet_dir": str(root), "review_rows_path": str(rows)})
        assert owner["clinical_signoff_substituted"] is False


def test_real_clinical_gate_replays_packet_and_binds_private_response_bytes():
    with _clinical(True) as (root, rows, response_path, response):
        root, rows, response_path = root.resolve(), rows.resolve(), response_path.resolve()
        rows.chmod(0o600)
        spec = spec_file(root)
        gate = a.create_gate("clinical_signoff", spec_path=spec,
            parameters={"packet_dir": str(root), "review_rows_path": str(rows)})
        a.replay_gate(gate, spec_sha256=a.digest(spec.read_bytes()))
        assert gate["proof"]["status"] == "PASS_CLINICIAN_SIGNOFF"
        assert gate["evidence"]["response"]["sha256"] == a.digest(response_path.read_bytes())
        response["reviewer"]["name_or_initials"] = "ANOTHER_SYNTHETIC_REVIEWER"
        write(response_path, response)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_GATE_EVIDENCE_CHANGED"):
            a.replay_gate(gate, spec_sha256=a.digest(spec.read_bytes()))


def test_clinical_gate_rejects_changed_metadata_and_unselected_option():
    with _clinical(True) as (root, rows, response_path, response):
        root, rows = root.resolve(), rows.resolve()
        rows.chmod(0o600)
        spec = spec_file(root)
        response["responses"][0]["selected_option"] = None
        write(response_path, response)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_CLINICAL_SIGNOFF_REQUIRED"):
            a.create_gate("clinical_signoff", spec_path=spec,
                parameters={"packet_dir": str(root), "review_rows_path": str(rows)})


def technical_decisions(root, inputs, manifest_path):
    manifest_sha = a.digest(manifest_path.read_bytes())
    value = {"artifact_type": "lvef_revalidation_technical_decisions_v1",
        "status": "APPROVED_TECHNICAL_DISPOSITIONS", "technical_manifest_sha256": manifest_sha,
        "input_checksums": inputs, "new_test_performance_used": False,
        "decisions": [{"issue_id": issue, "disposition": "CONSERVATIVE_EXCLUSION",
            "rationale": "Synthetic technical exclusion, not a clinical answer", "evidence_sha256": manifest_sha}
            for issue in readiness.TECHNICAL_ISSUE_IDS]}
    return write(root / "decisions.json", value), value


def test_technical_gate_requires_all_nine_evidence_bound_dispositions():
    with _technical() as (root, inputs, manifest_path, _):
        root, manifest_path = root.resolve(), manifest_path.resolve()
        spec = spec_file(root)
        path, value = technical_decisions(root, inputs, manifest_path)
        parameters = {"metadata_root": str(root), "input_checksums": inputs, "decisions_path": str(path)}
        gate = a.create_gate("technical_adjudication", spec_path=spec, parameters=parameters)
        assert gate["proof"]["n_dispositioned"] == 9
        value["decisions"].pop()
        write(path, value)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_TECHNICAL_ISSUE_SET_INVALID"):
            a.create_gate("technical_adjudication", spec_path=spec, parameters=parameters)


def test_operational_lvef_limitation_does_not_resolve_other_unit_questions():
    with _technical() as (root, inputs, manifest_path, _):
        root, manifest_path = root.resolve(), manifest_path.resolve()
        spec = spec_file(root)
        path, value = technical_decisions(root, inputs, manifest_path)
        for row in value["decisions"]:
            if row["issue_id"] == "LVEF_METHOD_MIXTURE":
                row["disposition"] = "OPERATIONAL_DEFINITION_WITH_LIMITATION"
        write(path, value)
        args = {"metadata_root": str(root), "input_checksums": inputs, "decisions_path": str(path)}
        assert a.create_gate("technical_adjudication", spec_path=spec, parameters=args)["status"] == "PASS"
        value["decisions"][0]["disposition"] = "OPERATIONAL_DEFINITION_WITH_LIMITATION"
        write(path, value)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_OPERATIONAL_DEFINITION_SCOPE_INVALID"):
            a.create_gate("technical_adjudication", spec_path=spec, parameters=args)


@contextmanager
def panel_fixture(targets=("lvot_vti",), choices=None):
    with _clinical(True) as (root, rows, response_path, response), _technical() as (technical_root, inputs, manifest_path, _):
        root, rows, technical_root, manifest_path = root.resolve(), rows.resolve(), technical_root.resolve(), manifest_path.resolve()
        rows.chmod(0o600)
        for row in response["responses"]:
            if row["issue_id"] in (choices or {}):
                row["selected_option"] = choices[row["issue_id"]]
        write(response_path, response)
        decision_path, _ = technical_decisions(technical_root, inputs, manifest_path)
        clinical_args = {"packet_dir": str(root), "review_rows_path": str(rows)}
        technical_args = {"metadata_root": str(technical_root), "input_checksums": inputs, "decisions_path": str(decision_path)}
        original, _ = synthetic_inputs()
        policies = (original.policies[0], *(replace(original.policies[1], name=name) for name in targets))
        review_bindings = {"clinical_response_sha256": a.digest(Path(response_path).read_bytes()),
                           "technical_decisions_sha256": a.digest(decision_path.read_bytes()), "new_test_performance_used": False}
        panel = {"artifact_type": "lvef_revalidation_panel_v1", "status": "APPROVED_CLINICAL_PANEL",
            **review_bindings, "strict_targets": list(targets), "target_approvals": {p.name: {
                "unit": p.unit, "source_raw_fields": ["raw_" + p.name], "valid_unit_raw_fields": ["raw_" + p.name],
                "aggregation_rule": "SYNTHETIC_EXPLICIT_MEDIAN", "construct_id": p.name,
                "label_definition_status": "OPERATIONAL_DEFINITION_WITH_LIMITATION" if p.name == "lvef" else "CLINICALLY_REVIEWED_MEASUREMENT",
                "rationale": "Synthetic independently approved construct"} for p in policies}}
        fields = ("name", "unit", "family", "allowed_predictors", "exact_target_fields", "aliases", "deterministic_fields", "near_deterministic_fields", "family_fields", "dependencies_resolved")
        dependencies = {"artifact_type": "lvef_revalidation_dependencies_v1", "status": "APPROVED_REVIEWED_DEPENDENCIES",
            **review_bindings, "policies": [{k: asdict(p)[k] for k in fields} for p in policies],
            "reviewed_predictor_universe": sorted({name for p in policies for name in p.allowed_predictors}),
            "predictor_decisions": [{"target": p.name, "raw_predictor": name, "disposition": "INDEPENDENT_ALLOWED",
                "evidence_sha256": a.digest(manifest_path.read_bytes()), "rationale": "Synthetic positive independence evidence"}
                for p in policies for name in p.allowed_predictors]}
        panel_path, dependency_path = root / "panel.json", root / "dependencies.json"
        spec_path = root / "spec.json"
        def refresh():
            write(panel_path, panel)
            write(dependency_path, dependencies)
            bindings = dict(original.bindings, source_completion=a.C3_COMPLETION,
                clinical_panel=a.digest(panel_path.read_bytes()), dependency_registry=a.digest(dependency_path.read_bytes()))
            write(spec_path, asdict(replace(original, policies=policies, strict_panel=targets, bindings=bindings)))
        refresh()
        args = {"panel_path": str(panel_path), "dependency_path": str(dependency_path),
                "clinical_parameters": clinical_args, "technical_parameters": technical_args}
        yield spec_path, args, panel, dependencies, refresh


def test_panel_positive_allowlist_is_explicit_and_missing_decision_is_not_independence():
    with panel_fixture() as (spec, args, _, dependencies, refresh):
        assert a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)["status"] == "PASS"
        dependencies["predictor_decisions"].pop()
        refresh()
        with pytest.raises(a.AuthorityError, match="ANALYSIS_POSITIVE_ALLOWLIST_MISMATCH"):
            a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)


def test_mixed_clinical_construct_is_excluded_without_a_new_stratified_authority():
    with panel_fixture(("arch_diam",), {"ARCH_DIAM_LEVEL": "MIXED_ARCH_LEVELS"}) as (spec, args, _, _, _):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_CLINICALLY_UNRESOLVED_TARGET_SURVIVED"):
            a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)


def test_same_mitral_e_construct_cannot_be_double_scored():
    with panel_fixture(("mv_peak_e", "mitral_e_velocity"), {"MITRAL_E_FIELD_RELATIONSHIP": "SAME_CONSTRUCT_SAME_UNIT"}) as (spec, args, _, _, _):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_DUPLICATE_MITRAL_E_CONSTRUCT"):
            a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)


def test_same_mitral_e_construct_requires_explicit_aggregation_approval():
    choice = "SAME_CONSTRUCT_SAME_UNIT"
    with panel_fixture(("mv_peak_e",), {"MITRAL_E_FIELD_RELATIONSHIP": choice}) as (spec, args, panel, _, refresh):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_MITRAL_E_AGGREGATION_APPROVAL_REQUIRED"):
            a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)
        panel["mitral_e_aggregation_approval"] = {"clinical_option": choice, "scored_targets": ["mv_peak_e"], "aggregation_explicitly_approved": True}
        refresh()
        assert a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)["status"] == "PASS"


def test_distinct_mitral_e_constructs_cannot_merge_the_same_raw_source():
    with panel_fixture(("mv_peak_e", "mitral_e_velocity"), {"MITRAL_E_FIELD_RELATIONSHIP": "DISTINCT_ACQUISITION_CONSTRUCTS"}) as (spec, args, panel, _, refresh):
        panel["target_approvals"]["mitral_e_velocity"]["source_raw_fields"] = panel["target_approvals"]["mv_peak_e"]["source_raw_fields"]
        refresh()
        with pytest.raises(a.AuthorityError, match="ANALYSIS_DISTINCT_MITRAL_E_MERGED"):
            a.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)


@contextmanager
def common_fixture():
    from prepare_lvef_revalidation_inputs import SOURCE_HASHES
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        spec, _ = synthetic_inputs()
        roster = tuple(f"SYNTHETIC_IMAGING_TEST_{index}" for index in range(679))
        artifacts = {split: {role: a._binding(write(root / (split + "." + role), (split + role).encode()))
                            for role in ("rows", "arrays")} for split in ("train", "val", "test")}
        targets = {}
        for p in spec.policies:
            targets[p.name] = {"unit": p.unit,
                "unit_status": "SEPARATE_EXACT_NAME_ANALYTICAL_SCALE_NATIVE_UNIT_UNVERIFIED" if p.name == "lvef" else "NORMALIZED_UNIT_COMPATIBLE_CLINICAL_DEFINITION_PENDING",
                "counts": dict(p.support_counts, total=sum(p.support_counts.values())),
                "training_iqr": 10., "row_fingerprints": p.row_fingerprints,
                "binary_class_counts": p.binary_class_counts}
        binary = spec.policies[0].binary_class_counts
        inputs = {"artifact_type": "lvef_revalidation_inputs_v1", "status": "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING",
            "c3_completion_sha256": a.C3_COMPLETION, "source_checksums": SOURCE_HASHES,
            "config_sha256": spec.bindings["config"], "all_missing_allowed_structured_rows_retained": True,
            "model_fitting_count": 0, "test_performance_access_count": 0,
            "lvef_label_authority": "EXACT_CASE_SENSITIVE_RAW_LVEF_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID",
            "split_artifacts": artifacts, "targets": targets,
            "selected_split_counts": {"train": 3171, "val": 679, "test": 680},
            "imaging_split_counts": {"train": 3168, "val": 678, "test": 679},
            "lvef_common_counts": spec.policies[0].support_counts,
            "exact40_counts": {split: binary["lvef_le_40"][split][0] - binary["lvef_lt_40"][split][0] for split in ("train", "val", "test")},
            "imaging_subject_roster_sha256": {"test": a.digest(a.canonical({"subject_ids": list(roster)}))}}
        path = write(root / "inputs.json", inputs)
        spec = replace(spec, test_subject_roster=roster,
            bindings=dict(spec.bindings, source_completion=a.C3_COMPLETION, input_audit=a.digest(path.read_bytes())))
        spec_path = write(root / "spec.json", asdict(spec))
        yield spec_path, path, spec


def test_complete_test_roster_is_bound_including_subjects_without_target_labels():
    with common_fixture() as (spec_path, path, spec):
        args = {"inputs_path": str(path)}
        assert a.create_gate("common_inputs", spec_path=spec_path, parameters=args)["status"] == "PASS"
        for roster in (spec.test_subject_roster[:-1], spec.test_subject_roster + ("SYNTHETIC_EXTRA",),
                       ("SYNTHETIC_SUBSTITUTION",) + spec.test_subject_roster[1:]):
            write(spec_path, asdict(replace(spec, test_subject_roster=roster)))
            with pytest.raises(a.AuthorityError, match="ANALYSIS_COMPLETE_TEST_ROSTER_MISMATCH"):
                a.create_gate("common_inputs", spec_path=spec_path, parameters=args)


def test_common_gate_detects_changed_split_bytes_without_parsing_test_features():
    with common_fixture() as (spec_path, path, _):
        inputs = a.decode(path.read_bytes())
        write(Path(inputs["split_artifacts"]["test"]["arrays"]["path"]), b"changed synthetic bytes")
        with pytest.raises(a.AuthorityError, match="ANALYSIS_INPUT_ARTIFACT_CHANGED"):
            a.create_gate("common_inputs", spec_path=spec_path, parameters={"inputs_path": str(path)})


def test_excluded_canonical_outside_candidate_targets_requires_complete_raw_alias_closure():
    # Gate replay has separate integration coverage; exercise the cross-gate join.
    with common_fixture() as (spec_path, path, spec):
        inputs = a.decode(path.read_bytes())
        raw = spec.policies[0].allowed_predictors[0]
        assert "mitral_e_velocity" not in inputs["targets"]
        inputs["source_raw_fields_by_canonical"] = {}
        write(path, inputs)
        gates = {name: {"artifact_type": "lvef_revalidation_replayed_gate_v1", "gate": name,
                        "parameters": {}, "proof": {}} for name in a.REQUIRED_GATES}
        gates["panel_and_dependencies"]["parameters"] = {"clinical_parameters": {}, "technical_parameters": {}}
        gates["panel_and_dependencies"]["proof"] = {"clinical_unresolved_excluded": ["mitral_e_velocity"], "target_approvals": {}}
        gates["clinical_signoff"]["evidence"] = {"review_rows": {"sha256": "f" * 64}}
        aliases = {"selected_studies": "selected", "subject_split_map": "split", "structured_measurements": "structured", "raw_canonical_mapping": "mapping"}
        checksums = {role: inputs["source_checksums"][key] for role, key in aliases.items()}
        checksums["clinical_review_rows"] = "f" * 64
        gates["technical_adjudication"]["parameters"] = {"input_checksums": checksums}
        gates["panel_and_dependencies"]["parameters"]["technical_parameters"] = {"input_checksums": checksums}
        gates["common_inputs"].update({"evidence": {"inputs": a._binding(path)}, "spec_path": str(spec_path)})
        with patch.object(a, "replay_gate"):
            with pytest.raises(a.AuthorityError, match="ANALYSIS_EXCLUDED_RAW_SOURCE_CLOSURE_MISSING"):
                a.validate_prerequisites(gates, spec_sha256=spec.sha256)
            inputs["source_raw_fields_by_canonical"]["mitral_e_velocity"] = [raw]
            write(path, inputs)
            gates["common_inputs"]["evidence"]["inputs"] = a._binding(path)
            with pytest.raises(a.AuthorityError, match="ANALYSIS_UNRESOLVED_RAW_ALIAS_SURVIVED"):
                a.validate_prerequisites(gates, spec_sha256=spec.sha256)


@contextmanager
def release_fixture():
    """Isolate the claim/freeze boundary; clinical replay is exercised above."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        spec = write(root / "spec.json", {"synthetic": True})
        spec_sha = a.digest(spec.read_bytes())
        owner = write(root / "owner.json", a.owner_authorization(request_sha256=a.OWNER_REQUEST,
            authorization_date="2026-09-09", source_reference="synthetic owner record"))
        gate_values = {
            "common_inputs": {"evidence": {"inputs": {"sha256": "a" * 64}}},
            "synthetic_validation": {"proof": {"analysis_commit": "c" * 40, "source_file_sha256": {}, "test_files": {}}}}
        gates = {name: a._binding(write(root / (name + ".json"), value)) for name, value in gate_values.items()}
        lock = write(root / "analysis_lock.restricted.json", {
            "status": "PASS_ANALYSIS_LOCK", "artifact_type": "lvef_revalidation_analysis_lock_v1",
            "analysis_commit": "c" * 40, "c3_completion_sha256": a.C3_COMPLETION,
            "spec": a._binding(spec), "owner_authorization": a._binding(owner), "gates": gates,
            "source_file_sha256": {}, "source_files": {}, "repository_root": str(root)})
        lock_sha = a.digest(lock.read_bytes())
        coefficients = write(root / "frozen_models.restricted.json", {"coefficients": [1.2, 3.4]})
        frozen_sha = a.digest(coefficients.read_bytes())
        frozen = write(root / "model_freeze.restricted.json", {"status": "PASS_SERIALIZED_MODEL_FREEZE",
            "analysis_lock_sha256": lock_sha, "spec_sha256": spec_sha, "input_sha256": "a" * 64, "frozen_sha256": frozen_sha})
        release = write(root / "test_release.restricted.json", {"status": "AUTHORIZED_FIXED_TEST_EVALUATION",
            "analysis_lock_sha256": lock_sha, "spec_sha256": spec_sha, "frozen_sha256": frozen_sha,
            "input_sha256": "a" * 64, "model_freeze_sha256": a.digest(frozen.read_bytes()), "training_validation_only": True})
        authority = a.AnalysisAuthority(lock, lock_sha, current_commit="c" * 40, source_files={},
            claim_path=root / "test_evaluation.claim.restricted.json")
        token = SimpleNamespace(spec_sha256=spec_sha, frozen_sha256=frozen_sha,
            release_receipt_sha256=a.digest(release.read_bytes()))
        with patch.object(a, "validate_prerequisites"), patch.object(a, "source_guard", return_value={}):
            yield root, authority, token


def test_fixed_test_release_is_exclusively_claimed_before_any_loader():
    with release_fixture() as (root, callback, token):
        result = callback(stage="test", spec_sha256=token.spec_sha256, frozen_sha256=token.frozen_sha256, release=token)
        assert result["status"] == "PASS_ANALYSIS_AUTHORITY"
        assert (root / "test_evaluation.claim.restricted.json").is_file()
        with pytest.raises(a.AuthorityError, match="ANALYSIS_TEST_RELEASE_ALREADY_CONSUMED"):
            callback(stage="test", spec_sha256=token.spec_sha256, frozen_sha256=token.frozen_sha256, release=token)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_DEVELOPMENT_AFTER_TEST_FORBIDDEN"):
            callback(stage="development", spec_sha256=token.spec_sha256)


def test_report_replays_existing_test_claim_without_consuming_another_release():
    with release_fixture() as (root, callback, token):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_REPORT_REQUIRES_TEST_CLAIM"):
            callback(stage="report", spec_sha256=token.spec_sha256)
        callback(stage="test", spec_sha256=token.spec_sha256, frozen_sha256=token.frozen_sha256, release=token)
        claim_path = root / "test_evaluation.claim.restricted.json"
        before = claim_path.read_bytes()
        assert callback(stage="report", spec_sha256=token.spec_sha256)["status"] == "PASS_ANALYSIS_AUTHORITY"
        assert claim_path.read_bytes() == before
        claim = a.decode(before)
        claim["spec_sha256"] = "e" * 64
        write(claim_path, claim)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_REPORT_CLAIM_BINDING_INVALID"):
            callback(stage="report", spec_sha256=token.spec_sha256)


def test_runner_reports_all_six_conditions_without_reloading_test_features():
    import lvef_revalidation_analysis as engine
    import lvef_revalidation_inference as inference
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        evaluation = {"construct": "family_masked"}
        value = write(root / "test_predictions.restricted.json", evaluation)
        write(root / "test_evaluation.restricted.json", {"status": "PASS_LOCKED_TEST_EVALUATION",
            "analysis_lock_sha256": "a" * 64, "input_sha256": "b" * 64, "spec_sha256": "c" * 64,
            "predictions_sha256": a.digest(value.read_bytes())})
        ctx = {"root": root, "run": {"spec_sha256": "c" * 64, "input_sha256": "b" * 64}}
        callback = Mock()
        with patch.object(runner, "lock_callback", return_value=(callback, "a" * 64)), \
             patch.object(runner, "_load_split") as loader, \
             patch.object(inference, "paired_inference", return_value={}) as paired, \
             patch.object(inference, "complete_paired_intervals", return_value={}), \
             patch.object(inference, "summarize_panel", return_value={}):
            assert runner.report(ctx)["status"] == "PASS_PRIVATE_PAIRED_REPORT"
        assert [call.kwargs["condition"] for call in paired.call_args_list] == list(engine.EVALUATION_CONDITIONS)
        assert all(call.kwargs["activate_core"] is False for call in paired.call_args_list)
        loader.assert_not_called()
        callback.assert_called_once_with(stage="report", spec_sha256="c" * 64)


def test_strict_report_publishes_bound_aggregate_only_after_actual_safety_check(aggregate_sources, tmp_path):
    import lvef_revalidation_inference as inference
    import lvef_multitask_audit_utils as audit
    import render_lvef_revalidation_results as renderer
    evaluation, report, inputs = aggregate_sources
    root = tmp_path.resolve()
    inputs_path = write(root / "inputs.json", inputs)
    predictions = write(root / "test_predictions.restricted.json", evaluation)
    input_sha = a.digest(inputs_path.read_bytes())
    write(root / "test_evaluation.restricted.json", {"status": "PASS_LOCKED_TEST_EVALUATION",
        "analysis_lock_sha256": "a" * 64, "input_sha256": input_sha, "spec_sha256": evaluation["spec_sha256"],
        "predictions_sha256": a.digest(predictions.read_bytes())})
    ctx = {"root": root, "run": {"spec_sha256": evaluation["spec_sha256"], "input_sha256": input_sha,
        "inputs_path": str(inputs_path)}, "source_hashes": dict.fromkeys(("renderer", "audit_utils", "safety_policy"), "c" * 64)}
    def component(name):
        return lambda _, *, condition, **kwargs: report["conditions"][condition][name]
    with patch.object(runner, "lock_callback", return_value=(Mock(), "a" * 64)), \
         patch.object(runner, "_load_split") as loader, \
         patch.object(inference, "paired_inference", side_effect=component("primary_inference")), \
         patch.object(inference, "complete_paired_intervals", side_effect=component("secondary_intervals")), \
         patch.object(inference, "summarize_panel", side_effect=component("panel_summary")), \
         patch.object(audit, "assert_aggregate_safe_json", wraps=audit.assert_aggregate_safe_json) as checker:
        result = runner.report(ctx)
    assert result["status"] == "PASS_VALIDATED_COMPLETE_AGGREGATE_BUNDLE"
    assert checker.call_count > 0
    loader.assert_not_called()
    safety = a.read_bound(root / "aggregate_safety.restricted.json", result["aggregate_safety_sha256"])
    bundle = a.read_bound(root / "aggregate_bundle.restricted.json", result["aggregate_bundle_sha256"])
    candidate = renderer.validate_bundle(bundle)
    assert safety["candidate_sha256"] == a.digest(a.canonical(candidate))
    assert safety["input_sha256"] == input_sha and safety["report_sha256"] == result["report_sha256"]
    assert safety["checks"] == ["CLOSED_COMPLETE_AGGREGATE_SCHEMA", "MAINTAINED_AGGREGATE_SAFE_JSON"]
    assert bundle["poster_export_authorized"] is False
    assert "SYN_test_SUBJECT" not in a.canonical(bundle).decode()


def test_alternate_claim_filename_cannot_reuse_test_release():
    with release_fixture() as (root, _, _):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_TEST_CLAIM_PATH_INVALID"):
            a.AnalysisAuthority(root / "analysis_lock.restricted.json", "b" * 64, current_commit="c" * 40,
                source_files={}, claim_path=root / "another.claim.json")


def test_changed_actual_coefficient_bytes_fail_before_claim():
    with release_fixture() as (root, callback, token):
        write(root / "frozen_models.restricted.json", {"coefficients": [1.2, 999.0]})
        with pytest.raises(a.AuthorityError, match="ANALYSIS_FROZEN_COEFFICIENTS_CHANGED"):
            callback(stage="test", spec_sha256=token.spec_sha256, frozen_sha256=token.frozen_sha256, release=token)
        assert not (root / "test_evaluation.claim.restricted.json").exists()


def test_changed_input_binding_fails_before_claim_even_with_updated_release_hash():
    with release_fixture() as (root, callback, token):
        path = root / "test_release.restricted.json"
        value = a.decode(path.read_bytes())
        value["input_sha256"] = "f" * 64
        write(path, value)
        token.release_receipt_sha256 = a.digest(path.read_bytes())
        with pytest.raises(a.AuthorityError, match="ANALYSIS_TEST_RELEASE_INVALID"):
            callback(stage="test", spec_sha256=token.spec_sha256, frozen_sha256=token.frozen_sha256, release=token)
        assert not (root / "test_evaluation.claim.restricted.json").exists()


def test_missing_real_lock_prevents_runner_loading_training_rows():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        with patch.object(runner, "_load_split") as loader:
            with pytest.raises(FileNotFoundError):
                runner.development({"root": root, "run": {}})
        loader.assert_not_called()


def test_source_guard_rejects_other_head_and_dirty_tracked_tree():
    with patch.object(a.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"d" * 40 + b"\n")):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_CURRENT_COMMIT_MISMATCH"):
            a.source_guard(ROOT, "c" * 40, {})
    outputs = [SimpleNamespace(returncode=0, stdout=b"c" * 40 + b"\n"), SimpleNamespace(returncode=0, stdout=b" M scripts/engine.py\n")]
    with patch.object(a.subprocess, "run", side_effect=outputs):
        with pytest.raises(a.AuthorityError, match="ANALYSIS_TRACKED_TREE_DIRTY"):
            a.source_guard(ROOT, "c" * 40, {})


def test_owner_private_publisher_is_exclusive_and_rejects_hardlink_or_symlink():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        path = root / "receipt.json"
        sha = a.publish(path, {"status": "SYNTHETIC"})
        assert a.read_bound(path, sha) == {"status": "SYNTHETIC"}
        with pytest.raises(FileExistsError):
            a.publish(path, {"status": "REPLACED"})
        alias = root / "alias.json"
        os.link(path, alias)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_PRIVATE_FILE_INVALID"):
            a.private_bytes(path)
        alias.unlink()
        alias.symlink_to(path)
        with pytest.raises(a.AuthorityError, match="ANALYSIS_SYMLINK_OR_RELATIVE_PATH"):
            a.private_bytes(alias)
