from __future__ import annotations

import hashlib
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import prepare_lvef_revalidation_review as review
from test_lvef_analysis_readiness import _technical, _clinical, _write
from build_lvef_clinician_signoff_packet import CLINICAL_ISSUE_IDS
import build_lvef_clinician_signoff_packet as clinician
from prepare_lvef_revalidation_inputs import VELOCITY_TARGETS


def _grouped_fixture():
    mapping = pd.DataFrame([
        ("lvef", "left_ventricular_ejection_fraction"),
        ("raw_av", "av_pk_vel"), ("raw_tapse", "tricuspid_annular_plane_systolic_excursion"),
        ("raw_lvedd", "left_ventricular_end_diastolic_diameter"),
        ("raw_e", "mv_peak_e"), ("raw_time_e", "mitral_e_velocity"),
        ("raw_unknown", "unreviewed_external_construct"),
        ("raw_ambiguous", "av_pk_vel"), ("raw_ambiguous", "lvot_vti"),
    ], columns=["measurement", "canonical_measurement"])
    names = sorted(set(mapping.measurement))
    inputs = {"c3_completion_sha256": review.C3_COMPLETION,
              "status": "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING",
              "structured_names": names, "structured_units": {name: "mm" for name in names},
              "target_names": ["lvef", "av_pk_vel"],
              "targets": {target: {"unit": "EF_percentage_points" if target == "lvef" else "cm/s",
                                      "support_floors_passed": True,
                                      "counts": {"train": 150, "val": 50, "test": 50, "total": 250},
                                      "source_raw_fields": ["lvef" if target == "lvef" else "raw_av"],
                                      "valid_unit_raw_fields": ["lvef" if target == "lvef" else "raw_av"],
                                      "aggregation_rule": "MEDIAN_WITHIN_REPORT"}
                          for target in ["lvef", "av_pk_vel"]}}
    inputs["structured_units"]["raw_time_e"] = "ms"
    evidence = pd.DataFrame([{"issue_id": "LVEF_ALIASES", "raw_name": "lvef"}])
    clinical = {"questions": [{"issue_id": issue, "selected_option": None} for issue in CLINICAL_ISSUE_IDS],
                "packet_sha256": "a" * 64, "response_sha256": "b" * 64, "human_signoff_complete": False}
    registry = pd.read_csv(ROOT / "docs/lvef_multitask/target_dependency_registry.csv", keep_default_na=False)
    clinical_registry = pd.read_csv(ROOT / "docs/lvef_multitask/target_dependency_registry_clinical_draft.csv", keep_default_na=False)
    return inputs, mapping, evidence, registry, clinical_registry, clinical


def _draft():
    inputs, mapping, evidence, registry, clinical_registry, clinical = _grouped_fixture()
    return review.grouped_review(inputs, mapping, evidence, registry, clinical_registry,
                                 clinical=clinical, technical={"technical_manifest_sha256": "c" * 64})


def test_grouped_draft_removes_raw_aliases_of_pending_targets_outside_scored_panel() -> None:
    draft = _draft()
    assert draft["final_approval"] is False and draft["clinical_choices_generated"] is False
    for policy in draft["policies"]:
        assert "raw_time_e" in policy["masks"]["technical_exclusions"]
        assert "raw_e" in policy["masks"]["clinical_pending_fields"]
        assert policy["allowed_predictors"] == []
        assert policy["source_raw_aggregation_approval"]["final_approval"] is False


def test_exact_raw_lvef_uses_separate_authority_without_synthetic_mapping_row() -> None:
    inputs, mapping, evidence, registry, clinical_registry, clinical = _grouped_fixture()
    mapping = mapping[mapping.measurement != "lvef"].copy()
    before = mapping.copy(deep=True)
    draft = review.grouped_review(inputs, mapping, evidence, registry, clinical_registry,
                                 clinical=clinical, technical={"technical_manifest_sha256": "c" * 64})
    pd.testing.assert_frame_equal(mapping, before)
    lvef = next(policy for policy in draft["policies"] if policy["name"] == "lvef")
    assert lvef["masks"]["exact_target_fields"] == ["lvef"]
    assert draft["operational_raw_authorities"]["lvef"] == "SEPARATE_EXACT_CASE_SENSITIVE_NUMERIC_MEDIAN"
    inputs["structured_names"].append("unmapped_other")
    try:
        review.grouped_review(inputs, mapping, evidence, registry, clinical_registry,
                              clinical=clinical, technical={"technical_manifest_sha256": "c" * 64})
    except ValueError as exc:
        assert str(exc) == "REVIEW_RAW_UNIVERSE_MISMATCH"
    else:
        raise AssertionError("Unmapped non-LVEF raw field entered the review universe")


def test_grouped_candidates_record_positive_basis_without_promoting_absent_edges() -> None:
    draft = _draft()
    lvef = next(policy for policy in draft["policies"] if policy["name"] == "lvef")
    candidates = {raw for group in lvef["candidate_positive_groups"].values() for raw in group}
    assert {"raw_av", "raw_tapse"} <= candidates
    assert "raw_unknown" in lvef["masks"]["uncertain_exclusions"]
    assert "raw_ambiguous" in lvef["masks"]["uncertain_exclusions"]
    assert "raw_lvedd" in lvef["masks"]["near_deterministic_fields"]
    assert draft["absence_of_registry_edge_implies_independence"] is False
    for row in draft["raw_field_decisions"]:
        assert row["review_status"] == "UNREVIEWED"
        if row["proposed_disposition"] == "CANDIDATE_DISTINCT_MEASUREMENT_CONTEXT":
            assert "not proof of independence" in row["rationale"]


def test_technical_constructor_uses_real_packet_replay_and_binds_nine_reviewed_rulings() -> None:
    with _technical() as (root, inputs, path, manifest):
        source_roles = {"raw_canonical_mapping": "mapping", "structured_measurements": "structured",
                        "selected_studies": "selected", "subject_split_map": "split"}
        inputs.update({role: review.SOURCE_HASHES[key] for role, key in source_roles.items()})
        manifest["input_checksums"] = inputs
        body = _write(path, manifest)
        with mock.patch.object(review, "REVIEWED_TECHNICAL_MANIFEST_SHA256", hashlib.sha256(body).hexdigest()):
            result = review.prepare_technical_decisions(root, inputs)
        assert result["status"] == "APPROVED_TECHNICAL_DISPOSITIONS"
        assert result["technical_manifest_sha256"] == hashlib.sha256(body).hexdigest()
        assert len(result["decisions"]) == 9
        assert all(row["evidence_sha256"] == result["technical_manifest_sha256"] for row in result["decisions"])
        method = next(row for row in result["decisions"] if row["issue_id"] == "LVEF_METHOD_MIXTURE")
        assert method["disposition"] == "OPERATIONAL_DEFINITION_WITH_LIMITATION"
        assert "mixture unknown" in method["rationale"]
        assert result["new_test_performance_used"] is False
        assert "selected_option" not in json.dumps(result)


def test_valid_unreviewed_technical_manifest_cannot_inherit_fixed_rulings() -> None:
    with _technical() as (root, inputs, path, manifest):
        roles = {"raw_canonical_mapping": "mapping", "structured_measurements": "structured",
                 "selected_studies": "selected", "subject_split_map": "split"}
        inputs.update({role: review.SOURCE_HASHES[key] for role, key in roles.items()})
        manifest["input_checksums"] = inputs
        _write(path, manifest)
        # This remains a valid hash-bound prepared packet, but it was never the
        # packet reviewed for these nine concrete metadata/unit dispositions.
        review.readiness.inspect_technical_packet(root, expected_input_checksums=inputs)
        try:
            review.prepare_technical_decisions(root, inputs)
        except ValueError as exc:
            assert str(exc) == "REVIEW_TECHNICAL_EVIDENCE_NOT_REVIEWED"
        else:
            raise AssertionError("Unreviewed prepared evidence inherited fixed technical approval")


def test_technical_constructor_refuses_foreign_scope_before_claiming_review_complete() -> None:
    with _technical() as (root, inputs, _, _):
        try:
            review.prepare_technical_decisions(root, inputs)
        except ValueError as exc:
            assert str(exc) == "REVIEW_TECHNICAL_SOURCE_SCOPE_MISMATCH"
        else:
            raise AssertionError("Foreign synthetic source passed fixed technical review")


def test_private_review_output_policy_blocks_unapproved_root_before_any_input_read() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        output = root / "unapproved"
        with mock.patch.object(review, "private_bytes", side_effect=AssertionError("input must not be read")):
            try:
                review.prepare_review(inputs_path=root / "input", metadata_root=root, review_rows_path=root / "rows",
                                      mapping_path=root / "mapping", packet_dir=root, output_dir=output,
                                      registry_path=root / "registry", clinical_registry_path=root / "clinical_registry")
            except ValueError:
                pass
            else:
                raise AssertionError("Unapproved review output path accepted")
        assert not output.exists()


def _final_fixture():
    targets = ["lvef", *sorted(review.STRICT_TARGETS)]
    mapping_rows = [{"measurement": "raw_" + name, "canonical_measurement": name}
                    for name in targets[1:] + ["mitral_e_velocity", "fs", "tr_mmhg", "resting_hr", "resting_sbp", "resting_dbp"]]
    mapping_rows += [{"measurement": name, "canonical_measurement": "ascending_aorta_diameter"}
                     for name in ("raw_ascending_category_a", "raw_ascending_category_b")]
    mapping_rows += [{"measurement": "raw_unknown", "canonical_measurement": "UNKNOWN_SOURCE_CONSTRUCT"}]
    mapping = pd.DataFrame(mapping_rows)
    names = ["lvef", *sorted(set(mapping.measurement))]
    units = {"lvef": "EF_percentage_points"}
    for row in mapping_rows:
        name = row["canonical_measurement"]
        units[row["measurement"]] = "cm/s" if name in VELOCITY_TARGETS else "mm"
    units.update(raw_mitral_e_velocity="ms", raw_fs="%", raw_tr_mmhg="mmhg", raw_resting_hr="bpm",
                 raw_resting_sbp="mmhg", raw_resting_dbp="mmhg", raw_unknown="mm",
                 raw_ascending_category_a="UNRESOLVED", raw_ascending_category_b="UNRESOLVED")
    policies = {}
    for name in targets:
        source = ["lvef"] if name == "lvef" else sorted(mapping.loc[mapping.canonical_measurement == name, "measurement"])
        valid = [raw for raw in source if units[raw] != "UNRESOLVED"]
        counts = dict(train=150, val=50, test=50, total=250)
        policies[name] = {"source_raw_fields": source, "valid_unit_raw_fields": valid,
            "aggregation_rule": "MEDIAN_OF_VALID_UNIT_NORMALIZED_ROWS_WITHIN_SELECTED_SUBJECT_REPORT_STUDY",
            "unit": "EF_percentage_points" if name == "lvef" else "cm/s" if name in VELOCITY_TARGETS else "mm",
            "support_floors_passed": True, "counts": counts, "training_iqr": 3.0,
            "row_fingerprints": {s: "a" * 64 for s in ("train", "val", "test")},
            "binary_class_counts": {e: {s: [25, counts[s] - 25] for s in ("train", "val", "test")}
                                    for e in ("lvef_lt_40", "lvef_le_40", "lvef_lt_50")} if name == "lvef" else {}}
    inputs = {"c3_completion_sha256": review.C3_COMPLETION, "status": "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING",
              "structured_names": names, "structured_units": units, "target_names": targets, "targets": policies}
    evidence = pd.DataFrame([{"issue_id": "LVEF_ALIASES", "raw_name": "lvef"}])
    clinical = {"status": "PASS_OWNER_RELAYED_QUALIFIED_ECHO_REVIEW", "review_mode": clinician.OWNER_RELAY_MODE,
        "clinical_adjudication_complete": True, "human_signoff_complete": False, "n_pending_questions": 0,
        "packet_sha256": clinician.OWNER_RELAY_PACKET_SHA256, "response_sha256": "b" * 64,
        "original_response_sha256": "c" * 64, "review_rows_sha256": "d" * 64,
        "questions": [{"issue_id": issue, "selected_option": choice[0]}
                      for issue, choice in zip(CLINICAL_ISSUE_IDS, clinician.OWNER_RELAY_DECISIONS)]}
    registry = pd.read_csv(ROOT / "docs/lvef_multitask/target_dependency_registry.csv", keep_default_na=False)
    clinical_registry = pd.read_csv(ROOT / "docs/lvef_multitask/target_dependency_registry_clinical_draft.csv", keep_default_na=False)
    technical = {"technical_manifest_sha256": review.REVIEWED_TECHNICAL_MANIFEST_SHA256}
    return inputs, mapping, evidence, registry, clinical_registry, clinical, technical


def _finalized(fixture=None):
    inputs, mapping, evidence, registry, clinical_registry, clinical, technical = fixture or _final_fixture()
    return review.finalized_records(inputs, mapping, evidence, registry, clinical_registry,
                                    clinical=clinical, technical=technical)


def test_owner_review_finalizes_21_operational_targets_and_one_wall_construct_without_source_claim():
    fixture = _final_fixture()
    before = copy.deepcopy(fixture[0])
    panel, dependencies, policies = _finalized(fixture)
    assert len(panel["strict_targets"]) == 21 and "lvef" not in panel["strict_targets"]
    assert len(policies) == 22 and fixture[0] == before
    approvals = panel["target_approvals"]
    assert len({approvals[name]["construct_id"] for name in panel["strict_targets"]}) == 21
    wall = approvals["inf_lat_thickness"]
    assert wall["construct_id"] == "END_DIASTOLIC_INFEROLATERAL_POSTERIOR_WALL"
    assert wall["source_raw_fields"] == ["raw_inf_lat_thickness"]
    assert all(r["source_acquisition_conventions_verified"] is False for r in approvals.values())
    assert wall["label_definition_status"] == "EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION"
    assert approvals["lvef"]["label_definition_status"] == "OPERATIONAL_DEFINITION_WITH_LIMITATION"
    assert sum(r["label_definition_status"] == "EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION" for r in approvals.values()) == 7
    assert sum(r["label_definition_status"] == "TECHNICALLY_REVIEWED_OPERATIONAL_DEFINITION_WITH_LIMITATION" for r in approvals.values()) == 14
    assert approvals["av_pk_vel"]["evidence_strength"] == "PROJECT_METADATA_AND_TECHNICAL_PROCESSING_REVIEW"
    assert approvals["lvef"]["evidence_strength"] == "SEPARATE_EXACT_NAME_LABEL_AUTHORITY_WITH_LIMITATION"
    assert panel["clinical_review_provenance"]["expert_name_or_initials"] is None
    assert panel["clinical_review_provenance"]["actual_expert_review_date"] is None
    ascending = approvals["ascending_aorta_diameter"]
    assert len(ascending["source_raw_fields"]) == 3
    assert ascending["valid_unit_raw_fields"] == ["raw_ascending_aorta_diameter"]
    assert dependencies["statistical_independence_claimed"] is False
    for policy in policies:
        assert "+" not in policy["family"]
        assert set(policy["family"].split("__")) == review._families(policy["name"])


def test_final_q6_retains_valid_e_target_excludes_time_source_and_masks_joint_family():
    panel, dependencies, policies = _finalized()
    assert "mv_peak_e" in panel["strict_targets"] and "mitral_e_velocity" not in panel["strict_targets"]
    assert panel["mitral_e_processing"] == clinician.OWNER_RELAY_Q6_PROCESSING
    assert panel["target_approvals"]["mv_peak_e"]["source_raw_fields"] == ["raw_mv_peak_e"]
    for policy in policies:
        assert "raw_mitral_e_velocity" not in policy["allowed_predictors"]
        if policy["name"] in review.FAMILIES["mitral_diastolic"]:
            assert {"raw_mitral_e_velocity", "raw_mv_peak_e", "raw_mv_peak_a", "raw_lat_e_prime", "raw_sept_e_prime"} <= set(policy["family_fields"])
    assert all(row["raw_predictor"] != "raw_mitral_e_velocity" for row in dependencies["predictor_decisions"])
    panel["mitral_e_processing"]["scored_targets"].append("changed_synthetic")
    assert clinician.OWNER_RELAY_Q6_PROCESSING["scored_targets"] == ["mv_peak_e"]


def test_final_positive_definitions_require_exact_supported_raw_identity_not_absent_edge():
    panel, dependencies, policies = _finalized()
    lvef = next(p for p in policies if p["name"] == "lvef")
    assert {"raw_av_pk_vel", "raw_resting_hr", "raw_resting_sbp", "raw_resting_dbp", "raw_arch_diam"} <= set(lvef["allowed_predictors"])
    assert not {"lvef", "raw_fs", "raw_left_ventricular_end_diastolic_diameter", "raw_left_ventricular_end_systolic_diameter", "raw_unknown"} & set(lvef["allowed_predictors"])
    assert {"raw_fs", "raw_left_ventricular_end_diastolic_diameter", "raw_left_ventricular_end_systolic_diameter"} <= set(lvef["near_deterministic_fields"])
    by_name = {p["name"]: p for p in policies}
    assert "raw_fs" in by_name["left_ventricular_end_diastolic_diameter"]["deterministic_fields"]
    assert "raw_tr_mmhg" in by_name["tricuspid_regurgitant_peak_velocity"]["deterministic_fields"]
    for p in policies:
        assert not {"raw_ascending_category_a", "raw_ascending_category_b", "raw_fs", "raw_tr_mmhg", "raw_unknown"} & set(p["allowed_predictors"])
    assert len(dependencies["predictor_decisions"]) == sum(len(p["allowed_predictors"]) for p in policies)
    assert all("not statistical independence" in row["rationale"] for row in dependencies["predictor_decisions"])
    assert dependencies["absence_of_registry_edge_implies_independence"] is False
    # An unambiguous row becoming ambiguous must lose positive authority, even
    # though its familiar first mapping and absence of prohibited edges persist.
    fixture = _final_fixture()
    fixture = (*fixture[:1], pd.concat([fixture[1], pd.DataFrame([{"measurement": "raw_av_pk_vel", "canonical_measurement": "UNKNOWN_SOURCE_CONSTRUCT"}])]), *fixture[2:])
    _, _, mutated = _finalized(fixture)
    assert all("raw_av_pk_vel" not in p["allowed_predictors"] for p in mutated)
    fixture = _final_fixture()
    fixture[0]["structured_units"]["raw_av_pk_vel"] = "ms"
    _, _, mutated = _finalized(fixture)
    assert all("raw_av_pk_vel" not in p["allowed_predictors"] for p in mutated)


def test_final_review_refuses_unchosen_or_inaccurate_same_unit_clinical_claim():
    for changed in (None, "SAME_CONSTRUCT_SAME_UNIT", "UNRESOLVED_EXCLUDE"):
        fixture = _final_fixture()
        fixture[5]["questions"][5]["selected_option"] = changed
        try:
            _finalized(fixture)
        except ValueError as exc:
            assert str(exc) == "REVIEW_FIXED_CLINICAL_DECISIONS_MISMATCH"
        else:
            raise AssertionError("A changed Q6 inherited the fixed owner-relayed processing authority")


def test_finalization_checks_fixed_input_before_reading_metadata_or_publishing():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        input_path = root / "input.json"; technical_path = root / "technical.json"
        _write(input_path, {"changed": True}); _write(technical_path, {})
        with mock.patch.object(review, "bind_approved_restricted_path", side_effect=lambda p, **kw: p), \
             mock.patch.object(review.readiness, "inspect_technical_packet", side_effect=AssertionError("must reject before metadata read")):
            try:
                review.finalize_review(inputs_path=input_path, technical_decisions_path=technical_path,
                    metadata_root=root, review_rows_path=root / "rows", mapping_path=root / "map", packet_dir=root,
                    owner_relayed_response_path=root / "relay", output_dir=root / "output",
                    registry_path=root / "registry", clinical_registry_path=root / "clinical_registry")
            except ValueError as exc:
                assert str(exc) == "REVIEW_FIXED_INPUT_HASH_MISMATCH"
            else:
                raise AssertionError("Changed numerical inputs entered fixed finalization")
        assert not (root / "output").exists()


def test_real_finalization_publication_replays_clinical_technical_and_panel_gates():
    import lvef_revalidation_authority as authority
    with _clinical(False) as (packet_root, review_rows, original_response, _), \
         _technical() as (metadata_root, expected, manifest_path, manifest), ExitStack() as stack:
        packet_root, metadata_root = packet_root.resolve(), metadata_root.resolve()
        review_rows, original_response, manifest_path = review_rows.resolve(), original_response.resolve(), manifest_path.resolve()
        review_rows.chmod(0o600)
        packet_path = packet_root / "clinical_metadata_clinician_signoff_restricted.md"
        stack.enter_context(mock.patch.object(clinician, "OWNER_RELAY_PACKET_SHA256", hashlib.sha256(packet_path.read_bytes()).hexdigest()))
        relay_path = packet_root / "owner_relay.json"
        inputs, mapping, evidence, _, _, _, _ = _final_fixture()
        mapping_path = packet_root / "mapping.csv"
        mapping_bytes = _write(mapping_path, mapping.to_csv(index=False).encode())
        source_hashes = dict(review.SOURCE_HASHES, mapping=hashlib.sha256(mapping_bytes).hexdigest())
        stack.enter_context(mock.patch.object(review, "SOURCE_HASHES", source_hashes))
        expected.update(clinical_review_rows=hashlib.sha256(review_rows.read_bytes()).hexdigest(),
            raw_canonical_mapping=source_hashes["mapping"], structured_measurements=source_hashes["structured"],
            selected_studies=source_hashes["selected"], subject_split_map=source_hashes["split"])
        manifest["input_checksums"] = expected
        evidence_bytes = _write(metadata_root / "restricted/technical_metadata/technical_metadata_evidence_restricted.csv",
                                evidence.to_csv(index=False).encode())
        ref = next(r for r in manifest["output_checksums"] if r["relative_name"] == "technical_metadata_evidence_restricted.csv")
        ref.update(bytes=len(evidence_bytes), sha256=hashlib.sha256(evidence_bytes).hexdigest())
        manifest_bytes = _write(manifest_path, manifest)
        stack.enter_context(mock.patch.object(review, "REVIEWED_TECHNICAL_MANIFEST_SHA256", hashlib.sha256(manifest_bytes).hexdigest()))
        technical_path = packet_root / "technical.json"
        technical_bytes = _write(technical_path, review.prepare_technical_decisions(metadata_root, expected))
        stack.enter_context(mock.patch.object(review, "REVIEWED_TECHNICAL_DECISIONS_SHA256", hashlib.sha256(technical_bytes).hexdigest()))
        input_path = packet_root / "inputs.json"
        input_bytes = _write(input_path, inputs)
        stack.enter_context(mock.patch.object(review, "REVIEWED_INPUT_SHA256", hashlib.sha256(input_bytes).hexdigest()))
        stack.enter_context(mock.patch.object(clinician, "OWNER_RELAY_INPUT_SHA256", hashlib.sha256(input_bytes).hexdigest()))
        stack.enter_context(mock.patch.object(review.readiness, "OWNER_RELAY_INPUT_SHA256", hashlib.sha256(input_bytes).hexdigest()))
        _write(relay_path, clinician.build_owner_relayed_response(packet_path=packet_path,
            original_response_path=original_response, review_rows_path=review_rows))
        stack.enter_context(mock.patch.object(review, "bind_approved_restricted_path", side_effect=lambda p, **kw: p))
        before_packet, before_response = packet_path.read_bytes(), original_response.read_bytes()
        output = packet_root / "final_review"
        result = review.finalize_review(inputs_path=input_path, metadata_root=metadata_root, review_rows_path=review_rows,
            mapping_path=mapping_path, packet_dir=packet_root, owner_relayed_response_path=relay_path,
            technical_decisions_path=technical_path, output_dir=output,
            registry_path=ROOT / "docs/lvef_multitask/target_dependency_registry.csv",
            clinical_registry_path=ROOT / "docs/lvef_multitask/target_dependency_registry_clinical_draft.csv")
        assert result["status"] == "PASS_FINALIZED_PANEL_AND_DEPENDENCIES" and result["strict_target_count"] == 21
        assert packet_path.read_bytes() == before_packet and original_response.read_bytes() == before_response
        assert input_path.read_bytes() == input_bytes and technical_path.read_bytes() == technical_bytes
        policy = json.loads((output / "policy_specification.restricted.json").read_bytes())
        spec = {"policies": policy["policies"], "strict_panel": policy["strict_panel"], "strict_panel_locked": True,
            "construct": "strict", "test_subject_roster": [str(i) for i in range(50)], "prescription_sha256": "a" * 64,
            "bindings": {key: "a" * 64 for key in ("source_completion", "input_audit", "clinical_panel", "dependency_registry",
                                                      "sap", "config", "environment", "owner_authorization", "safety")}}
        spec["bindings"].update(source_completion=authority.C3_COMPLETION,
            input_audit=hashlib.sha256(input_bytes).hexdigest(),
            clinical_panel=result["panel_sha256"], dependency_registry=result["dependencies_sha256"])
        spec_path = packet_root / "spec.json"
        _write(spec_path, authority.canonical(spec))
        gate = authority.create_gate("panel_and_dependencies", spec_path=spec_path, parameters={
            "panel_path": str(output / "panel.restricted.json"), "dependency_path": str(output / "dependencies.restricted.json"),
            "clinical_parameters": {"packet_dir": str(packet_root), "review_rows_path": str(review_rows),
                                    "owner_relayed_response_path": str(relay_path)},
            "technical_parameters": {"metadata_root": str(metadata_root), "input_checksums": expected,
                                     "decisions_path": str(technical_path)}})
        assert gate["status"] == "PASS" and gate["proof"]["processing_excluded_targets"] == ["mitral_e_velocity"]
        assert gate["proof"]["reviewed_policy_count"] == 22
