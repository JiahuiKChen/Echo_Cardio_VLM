from __future__ import annotations

import hashlib
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
from test_lvef_analysis_readiness import _technical, _write
from build_lvef_clinician_signoff_packet import CLINICAL_ISSUE_IDS


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
