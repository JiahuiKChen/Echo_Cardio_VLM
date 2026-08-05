from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_lvef_clinician_signoff_packet import (
    CLINICAL_ISSUE_IDS,
    CLINICAL_ISSUE_SPECS,
    build_packet,
    response_template,
    sha256_file,
    validate_response,
)


def metadata_rows() -> pd.DataFrame:
    targets = sorted({target for spec in CLINICAL_ISSUE_SPECS for target in spec["targets"]})
    return pd.DataFrame(
        {
            "allowlisted_target": targets,
            "raw_name": [f"RAW_{target}" for target in targets],
            "raw_description": [f"Synthetic restricted description for {target}" for target in targets],
            "native_unit": ["synthetic_unit"] * len(targets),
            "normalized_unit": ["synthetic_unit"] * len(targets),
            "canonical_source": ["synthetic_mapping"] * len(targets),
        }
    )


def test_packet_has_exact_fixed_question_set_and_required_fields() -> None:
    packet = build_packet(metadata_rows(), "a" * 40)
    assert packet.count("## Q") == 8
    for issue_id in CLINICAL_ISSUE_IDS:
        assert f"`{issue_id}`" in packet
    assert packet.count("- [ ] `UNRESOLVED_EXCLUDE`") == 8
    assert packet.count("Do not merge aliases; apply the conservative unresolved-family mask") == 8
    assert "Reviewer name or initials" in packet
    assert "Role / echocardiographic expertise" in packet
    assert "Echo measurement expertise attestation" in packet
    assert "Rationale (required" in packet
    assert "Exact raw description" in packet
    assert "Decision consequence" in packet


def test_complete_response_validates_against_packet_checksum(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.md"
    packet_path.write_text(build_packet(metadata_rows(), "b" * 40))
    response = response_template(sha256_file(packet_path))
    response["reviewer"] = {
        "name_or_initials": "Synthetic Reviewer",
        "role_expertise": "Echocardiography measurement specialist",
        "echo_measurement_expertise_attested": True,
        "signoff_date": "2026-08-05",
    }
    specs = {spec["issue_id"]: spec for spec in CLINICAL_ISSUE_SPECS}
    for item in response["responses"]:
        item["selected_option"] = specs[item["issue_id"]]["options"][0]
        item["rationale"] = "Synthetic rationale for validator test."
    result = validate_response(packet_path, response)
    assert result["status"] == "PASS"
    assert result["signoff_complete"] is True
    assert result["packet_checksum_verified"] is True
    assert result["n_responses"] == 8
    assert "name_or_initials" not in result


def test_validator_fails_closed_on_checksum_choice_and_rationale(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.md"
    packet_path.write_text(build_packet(metadata_rows(), "c" * 40))
    response = response_template("0" * 64)
    response["reviewer"] = {
        "name_or_initials": "X",
        "role_expertise": "Echo",
        "echo_measurement_expertise_attested": True,
        "signoff_date": "2026-08-05",
    }
    result = validate_response(packet_path, response)
    assert result["status"] == "FAIL"
    assert result["signoff_complete"] is False
    assert "PACKET_CHECKSUM_MISMATCH" in result["validation_issues"]
    assert any(issue.startswith("INVALID_OR_MISSING_OPTION:") for issue in result["validation_issues"])
    assert any(issue.startswith("RATIONALE_MISSING:") for issue in result["validation_issues"])


def test_git_safe_summary_template_has_exact_seventeen_issue_rows() -> None:
    frame = pd.read_csv(
        ROOT / "docs" / "lvef_multitask" / "clinical_metadata_adjudication_summary_template.csv",
        keep_default_na=False,
    )
    technical = {
        "BSA_FORMULA_WEIGHT_AVAILABILITY",
        "DIMENSION_CM_MM_UNITS",
        "LVEDV_LVESV_FIELDS",
        "LVEF_ALIASES",
        "LVEF_METHOD_MIXTURE",
        "LV_MASS_RWT_FIELDS",
        "MITRAL_EA_EEPRIME_RATIO_FIELDS",
        "VELOCITY_MPS_CMPS_UNITS",
        "WALL_MOTION_FIELDS",
    }
    assert len(frame) == 17
    assert not frame["question_id"].duplicated().any()
    assert set(frame["question_id"]) == set(CLINICAL_ISSUE_IDS) | technical
    assert set(frame["final_disposition_category"]) == {"PENDING"}
    assert set(frame["unresolved_flag"].astype(str).str.upper()) == {"TRUE"}
    forbidden = {"raw_description", "raw_name", "native_unit", "normalized_unit", "alias"}
    assert forbidden.isdisjoint(frame.columns)
