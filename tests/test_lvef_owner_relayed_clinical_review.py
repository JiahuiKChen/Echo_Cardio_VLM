"""The fixed owner relay is distinct from direct expert entry and generic waiver."""
from contextlib import contextmanager
import copy
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import build_lvef_clinician_signoff_packet as clinician
import audit_lvef_analysis_readiness as readiness
import lvef_revalidation_authority as authority
from test_lvef_analysis_readiness import _clinical
from test_lvef_revalidation_authority import panel_fixture, spec_file, write


@contextmanager
def relay_fixture():
    with _clinical(False) as (root, rows, original, _):
        root, rows, original = root.resolve(), rows.resolve(), original.resolve()
        rows.chmod(0o600)
        packet = root / "clinical_metadata_clinician_signoff_restricted.md"
        # Only the fixed packet identity changes for this synthetic metadata fixture.
        with patch.object(clinician, "OWNER_RELAY_PACKET_SHA256", authority.digest(packet.read_bytes())):
            value = clinician.build_owner_relayed_response(packet_path=packet,
                original_response_path=original, review_rows_path=rows)
            relay = write(root / "owner_relayed_echo_review.restricted.json", value)
            yield root, rows, packet, original, relay, value


def test_owner_relay_preserves_original_controls_and_truthful_unknown_metadata():
    with relay_fixture() as (root, rows, packet, original, relay, value):
        before = {path: path.read_bytes() for path in (packet, original, rows)}
        result = readiness.inspect_clinician_packet(root, rows, owner_relayed_response_path=relay)
        assert result["status"] == "PASS_OWNER_RELAYED_QUALIFIED_ECHO_REVIEW"
        assert result["clinical_adjudication_complete"] is True
        assert result["human_signoff_complete"] is False and result["n_pending_questions"] == 0
        assert value["expert"] == {"name_or_initials": None, "direct_signature": None, "actual_review_date": None,
            "qualification_reported_by_owner": "QUALIFIED_ECHOCARDIOGRAPHER", "agreement_reported_by_owner": True}
        assert result["communication_observed_at"] == "2026-09-09T14:40:15Z"
        assert result["source_acquisition_conventions_verified"] is False
        assert all(path.read_bytes() == body for path, body in before.items())
        assert readiness.inspect_clinician_packet(root, rows)["status"] == "PENDING_HUMAN_SIGNOFF"
        assert value["unresolved_reference_identifiers"] == [f"[{i}]" for i in range(1, 17)]


@pytest.mark.parametrize("mutation", ["statement", "input", "reviewer", "date", "signature", "observed", "q6", "q3", "verified", "qualified_type"])
def test_altered_relay_evidence_cannot_pass(mutation):
    with relay_fixture() as (root, rows, packet, _, relay, value):
        changed = copy.deepcopy(value)
        if mutation == "statement": changed["owner_statement_sha256"] = "f" * 64
        elif mutation == "input": changed["input_audit_sha256"] = "f" * 64
        elif mutation == "reviewer": changed["expert"]["name_or_initials"] = "INVENTED"
        elif mutation == "date": changed["expert"]["actual_review_date"] = "2026-09-09"
        elif mutation == "signature": changed["expert"]["direct_signature"] = "OWNER"
        elif mutation == "observed": changed["communication_observed_at"] = "2026-09-10T00:00:00Z"
        elif mutation == "q6": changed["responses"][5]["selected_option"] = "SAME_CONSTRUCT_SAME_UNIT"
        elif mutation == "q3": changed["responses"][2]["accepted_nomenclature_synonyms"] = []
        elif mutation == "verified": changed["source_acquisition_conventions_verified"] = True
        else: changed["expert"]["agreement_reported_by_owner"] = 1
        write(relay, changed)
        assert clinician.validate_response(packet, changed)["status"] == "FAIL"
        with pytest.raises(readiness.ReadinessError, match="READINESS_OWNER_RELAY_EVIDENCE_CHANGED"):
            readiness.inspect_clinician_packet(root, rows, owner_relayed_response_path=relay)


def test_changed_source_bytes_and_original_response_are_rejected(tmp_path):
    with relay_fixture() as (root, rows, packet, original, relay, value):
        source = tmp_path / "altered_source.txt"
        source.write_bytes(clinician.OWNER_RELAY_SOURCE_PATH.read_bytes() + b"changed")
        with patch.object(clinician, "OWNER_RELAY_SOURCE_PATH", source):
            assert clinician.validate_response(packet, value)["status"] == "FAIL"
        modified = authority.decode(original.read_bytes())
        modified["reviewer"]["name_or_initials"] = "NOT_ORIGINAL"
        write(original, modified)
        with pytest.raises(ValueError, match="OWNER_RELAY_ORIGINAL_BLANK_RESPONSE_CHANGED"):
            readiness.inspect_clinician_packet(root, rows, owner_relayed_response_path=relay)


def test_relay_gate_binds_current_input_and_original_response_without_old_form_changes():
    with relay_fixture() as (root, rows, _, original, relay, _):
        spec = spec_file(root, input_audit=clinician.OWNER_RELAY_INPUT_SHA256)
        args = {"packet_dir": str(root), "review_rows_path": str(rows), "owner_relayed_response_path": str(relay)}
        gate = authority.create_gate("clinical_signoff", spec_path=spec, parameters=args)
        authority.replay_gate(gate, spec_sha256=authority.digest(spec.read_bytes()))
        assert gate["evidence"]["original_response"]["sha256"] == authority.digest(original.read_bytes())
        assert gate["proof"]["questions"][2]["accepted_nomenclature_synonyms"] == ["END_DIASTOLIC_POSTERIOR_WALL"]
        assert gate["proof"]["questions"][5]["selected_option"] == clinician.OWNER_RELAY_Q6
        spec_file(root, input_audit="f" * 64)
        with pytest.raises(authority.AuthorityError, match="ANALYSIS_CLINICAL_SIGNOFF_REQUIRED"):
            authority.create_gate("clinical_signoff", spec_path=spec, parameters=args)


@contextmanager
def relay_panel_fixture():
    with panel_fixture(("mv_peak_e", "inf_lat_thickness", "lvot_vti")) as (spec, args, panel, dependencies, refresh):
        root = Path(args["clinical_parameters"]["packet_dir"])
        rows = Path(args["clinical_parameters"]["review_rows_path"])
        packet = root / "clinical_metadata_clinician_signoff_restricted.md"
        original = root / "clinical_metadata_clinician_response_restricted.json"
        packet_sha = authority.digest(packet.read_bytes())
        write(original, clinician.response_template(packet_sha))
        with patch.object(clinician, "OWNER_RELAY_PACKET_SHA256", packet_sha):
            relay = write(root / "owner_relayed_echo_review.restricted.json",
                clinician.build_owner_relayed_response(packet_path=packet, original_response_path=original, review_rows_path=rows))
            args["clinical_parameters"]["owner_relayed_response_path"] = str(relay)
            proof = readiness.inspect_clinician_packet(root, rows, owner_relayed_response_path=relay)
            for item in (panel, dependencies):
                item["clinical_response_sha256"] = authority.digest(relay.read_bytes())
                item["clinical_review_provenance"] = clinician.clinical_review_provenance(proof)
            for name, row in panel["target_approvals"].items():
                expert_scoped = any(name in issue["targets"] for issue in clinician.CLINICAL_ISSUE_SPECS)
                if name != "lvef":
                    row["label_definition_status"] = ("EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION" if expert_scoped
                        else "TECHNICALLY_REVIEWED_OPERATIONAL_DEFINITION_WITH_LIMITATION")
                strength = ("SEPARATE_EXACT_NAME_LABEL_AUTHORITY_WITH_LIMITATION" if name == "lvef" else
                    clinician.OWNER_RELAY_EVIDENCE_STRENGTH if expert_scoped else "PROJECT_METADATA_AND_TECHNICAL_PROCESSING_REVIEW")
                row.update(evidence_strength=strength,
                    source_acquisition_conventions_verified=False, limitations=["Operational interpretation; original acquisition conventions unverified."])
            panel["mitral_e_processing"] = copy.deepcopy(clinician.OWNER_RELAY_Q6_PROCESSING)
            def update():
                refresh()
                value = authority.decode(spec.read_bytes())
                value["bindings"]["input_audit"] = clinician.OWNER_RELAY_INPUT_SHA256
                write(spec, value)
            update()
            yield spec, args, panel, dependencies, update


def test_operational_panel_retains_valid_e_and_excludes_only_incompatible_construct():
    with relay_panel_fixture() as (spec, args, _, _, _):
        gate = authority.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)
        assert gate["status"] == "PASS" and "mv_peak_e" in gate["proof"]["strict_targets"]
        assert gate["proof"]["clinical_unresolved_excluded"] == []
        assert gate["proof"]["processing_excluded_targets"] == ["mitral_e_velocity"]
        assert gate["proof"]["strict_targets"].count("inf_lat_thickness") == 1


@pytest.mark.parametrize("target", ["lvot_vti", "lvef"])
def test_target_outside_eight_question_scope_cannot_claim_individual_expert_adjudication(target):
    with relay_panel_fixture() as (spec, args, panel, _, refresh):
        gate = authority.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)
        approved = gate["proof"]["target_approvals"][target]
        expected = "SEPARATE_EXACT_NAME_LABEL_AUTHORITY_WITH_LIMITATION" if target == "lvef" else "PROJECT_METADATA_AND_TECHNICAL_PROCESSING_REVIEW"
        assert approved["evidence_strength"] == expected
        panel["target_approvals"][target]["evidence_strength"] = clinician.OWNER_RELAY_EVIDENCE_STRENGTH
        panel["target_approvals"][target]["label_definition_status"] = "EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION"
        refresh()
        with pytest.raises(authority.AuthorityError, match="ANALYSIS_OPERATIONAL_EVIDENCE_STRENGTH_INVALID"):
            authority.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)


@pytest.mark.parametrize("mutation", ["merge", "sameunit", "stronger_evidence", "name"])
def test_relay_panel_cannot_claim_merge_or_stronger_clinical_verification(mutation):
    with relay_panel_fixture() as (spec, args, panel, _, refresh):
        if mutation == "merge": panel["mitral_e_processing"]["merge_authorized"] = True
        elif mutation == "sameunit": panel["mitral_e_aggregation_approval"] = {"aggregation_explicitly_approved": True}
        elif mutation == "name": panel["clinical_review_provenance"]["expert_name_or_initials"] = "INVENTED"
        else: panel["target_approvals"]["mv_peak_e"]["source_acquisition_conventions_verified"] = True
        refresh()
        with pytest.raises(authority.AuthorityError):
            authority.create_gate("panel_and_dependencies", spec_path=spec, parameters=args)
