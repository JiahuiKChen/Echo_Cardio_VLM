from pathlib import Path
import json
import subprocess
import sys
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lvef_multitask_clinical_metadata import (
    ALLOWED_TARGETS,
    EVIDENCE_TYPES,
    REQUIRED_ISSUE_IDS,
    build_alias_summary,
    build_canonical_summary,
    build_clinician_questionnaire,
    build_review_rows,
    build_targeted_followup_prompt,
    build_unit_summary,
    classify_unresolved_questions,
    exact_target,
    normalized_text,
    requested_targets,
)


EXPECTED_AGGREGATE_FILES = {
    "clinical_metadata_review_packet_manifest.json",
    "clinical_metadata_schema_summary.json",
    "clinical_metadata_ambiguity_counts.csv",
    "clinical_metadata_unit_summary.csv",
    "clinical_metadata_alias_summary.csv",
    "clinical_metadata_safety_gate.json",
}


def _unit_for(target: str) -> tuple[str, str, str]:
    if target in {"lvef", "fs"}:
        return "%", "%", "fraction"
    if target == "body_surface_area":
        return "m²", "m²", "area"
    if target in {"resting_sbp", "resting_dbp", "tr_mmhg"}:
        return "mmHg", "mmHg", "pressure"
    if target == "resting_hr":
        return "bpm", "bpm", "rate"
    if target in {
        "mv_peak_e",
        "mitral_e_velocity",
        "av_pk_vel",
        "mv_peak_a",
        "tricuspid_regurgitant_peak_velocity",
        "lat_e_prime",
        "sept_e_prime",
    }:
        return "cm/s", "cm/s", "velocity"
    return "cm", "cm", "length"


def _description_for(target: str) -> str:
    return {
        "lvef": "Visual left ventricular ejection fraction",
        "tr_mmhg": "TR peak gradient",
        "mv_peak_e": "Transmitral peak E velocity",
        "mitral_e_velocity": "Transmitral peak E velocity",
        "inf_lat_thickness": "End-diastolic inferolateral wall thickness",
        "la_dimen": "PLAX anteroposterior LA dimension at end-systole",
        "arch_diam": "Transverse aortic arch diameter",
        "sinus_diam": "Sinus of Valsalva leading-edge diameter at end-diastole",
        "ascending_aorta_diameter": "Tubular ascending aorta leading-edge diameter at end-diastole",
        "ivc_diam": "End-expiratory IVC diameter with respiratory collapse context",
        "body_surface_area": "Body surface area; formula unspecified",
        "rv_diam": "SYNTHETIC_SECRET_DESCRIPTOR_Ω",
    }.get(target, f"Synthetic description for {target}")


def synthetic_mapping() -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for target in ALLOWED_TARGETS:
        unit, normalized_unit, category = _unit_for(target)
        if target == "rv_diam":
            unit = normalized_unit = "SYNTHETIC_SECRET_UNIT_Ω"
            category = "unknown"
        rows.append(
            {
                "measurement": f"RAW_{target}",
                "measurement_description": _description_for(target),
                "canonical_measurement": target,
                "canonical_source": "synthetic_manual",
                "unit": unit,
                "unit_norm": normalized_unit,
                "unit_category": category,
            }
        )

    extras = [
        {
            "measurement": "RAW_EF—SIMPSON",
            "measurement_description": "Biplane Simpson ejection fraction",
            "canonical_measurement": "source_specific_simpson_ef",
            "canonical_source": "synthetic_source",
            "unit": "%",
            "unit_norm": "%",
            "unit_category": "fraction",
        },
        {
            "measurement": "RAW_LVEDV",
            "measurement_description": "Left ventricular end-diastolic volume",
            "canonical_measurement": "source_specific_lvedv",
            "canonical_source": "synthetic_source",
            "unit": "mL",
            "unit_norm": "mL",
            "unit_category": "volume",
        },
        {
            "measurement": "RAW_LVESV",
            "measurement_description": "Left ventricular end-systolic volume",
            "canonical_measurement": "source_specific_lvesv",
            "canonical_source": "synthetic_source",
            "unit": "mL",
            "unit_norm": "mL",
            "unit_category": "volume",
        },
        {
            "measurement": "RAW_LV_FUNCTION",
            "measurement_description": "Qualitative LV function: mildly reduced",
            "canonical_measurement": "source_specific_lv_function",
            "canonical_source": "synthetic_source",
            "unit": "category",
            "unit_norm": "category",
            "unit_category": "qualitative",
        },
        {
            "measurement": "RAW_RWMA",
            "measurement_description": "Regional wall-motion abnormality",
            "canonical_measurement": "source_specific_wall_motion",
            "canonical_source": "synthetic_source",
            "unit": "category",
            "unit_norm": "category",
            "unit_category": "qualitative",
        },
        {
            "measurement": "RAW_E_OVER_E_PRIME",
            "measurement_description": "Mitral E/e′ ratio",
            "canonical_measurement": "source_specific_e_eprime_ratio",
            "canonical_source": "synthetic_source",
            "unit": "ratio",
            "unit_norm": "ratio",
            "unit_category": "ratio",
        },
        {
            "measurement": "RAW_STROKE_VOLUME",
            "measurement_description": "LVOT stroke volume",
            "canonical_measurement": "source_specific_stroke_volume",
            "canonical_source": "synthetic_source",
            "unit": "mL",
            "unit_norm": "mL",
            "unit_category": "volume",
        },
        {
            "measurement": "RAW_CARDIAC_OUTPUT",
            "measurement_description": "Cardiac output",
            "canonical_measurement": "source_specific_cardiac_output",
            "canonical_source": "synthetic_source",
            "unit": "L/min",
            "unit_norm": "L/min",
            "unit_category": "flow",
        },
        {
            "measurement": "RAW_LV_MASS",
            "measurement_description": "Left ventricular mass",
            "canonical_measurement": "source_specific_lv_mass",
            "canonical_source": "synthetic_source",
            "unit": "g",
            "unit_norm": "g",
            "unit_category": "mass",
        },
        {
            "measurement": "RAW_RWT",
            "measurement_description": "Relative wall thickness",
            "canonical_measurement": "source_specific_rwt",
            "canonical_source": "synthetic_source",
            "unit": "ratio",
            "unit_norm": "ratio",
            "unit_category": "ratio",
        },
        {
            "measurement": "RAW_LAVI",
            "measurement_description": "Left atrial volume indexed to BSA",
            "canonical_measurement": "left_atrial_volume_index",
            "canonical_source": "synthetic_source",
            "unit": "mL/m²",
            "unit_norm": "mL/m²",
            "unit_category": "indexed_volume",
        },
        {
            "measurement": "RAW_LVOT_VTI_MM",
            "measurement_description": "PW Doppler LVOT VTI beat average",
            "canonical_measurement": "lvot_vti",
            "canonical_source": "synthetic_manual",
            "unit": "mm",
            "unit_norm": "mm",
            "unit_category": "length",
        },
        {
            "measurement": "RAW_ARCH_EMPTY_ALIAS",
            "measurement_description": "",
            "canonical_measurement": "arch_diam",
            "canonical_source": "synthetic_manual",
            "unit": "unknown",
            "unit_norm": "unknown",
            "unit_category": "unknown",
        },
    ]
    rows.extend(extras)
    rows.append(dict(extras[-2]))  # exact duplicate source-metadata row
    shared = {
        "measurement": "RAW_MITRAL_E_SHARED",
        "measurement_description": "Transmitral peak E velocity",
        "canonical_source": "synthetic_manual",
        "unit": "cm/s",
        "unit_norm": "cm/s",
        "unit_category": "velocity",
    }
    rows.append({**shared, "canonical_measurement": "mv_peak_e"})
    rows.append({**shared, "canonical_measurement": "mitral_e_velocity"})
    return pd.DataFrame(rows)


def test_exact_identifier_allowlist_rejects_mangled_and_unknown_names() -> None:
    assert exact_target("lvef") == "lvef"
    assert requested_targets([]) == ALLOWED_TARGETS
    for invalid in ("left*ventricular*end*diastolic*diameter", "LVEF", "new_target"):
        try:
            exact_target(invalid)
        except ValueError:
            continue
        raise AssertionError(f"Corrupted or unknown target was accepted: {invalid}")


def test_requested_target_allowlist_rejects_duplicates() -> None:
    try:
        requested_targets(["lvef", "lvef"])
    except ValueError:
        return
    raise AssertionError("Duplicate requested targets were accepted")


def test_unicode_and_punctuation_are_safe_and_candidate_categories_are_complete() -> None:
    assert normalized_text("Mitral E/e′ — 3-D") == "mitral e/e prime - 3-d"
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    expected_flags = {
        "candidate_lvef_alias_or_method",
        "candidate_lvef_method",
        "candidate_lvedv_lvesv",
        "candidate_qualitative_lv_function",
        "candidate_wall_motion",
        "candidate_formula_derived",
        "candidate_ratio",
        "candidate_stroke_volume_or_cardiac_output",
        "candidate_lv_mass_or_rwt",
        "candidate_indexed",
        "candidate_bsa_or_weight",
    }
    assert all(bool(rows[column].any()) for column in expected_flags)
    ratio = rows[rows["raw_name"] == "RAW_E_OVER_E_PRIME"].iloc[0]
    assert bool(ratio["candidate_ratio"])
    candidate = rows[rows["canonical_mapping"] == "source_specific_simpson_ef"].iloc[0]
    assert candidate["allowlisted_target"] == ""
    assert candidate["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED"


def test_alias_and_unit_summaries_detect_duplicates_conflicts_empty_and_multiple_units() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    alias = build_alias_summary(rows, ALLOWED_TARGETS).set_index("target")
    units = build_unit_summary(rows, ALLOWED_TARGETS).set_index("target")
    assert int(alias.loc["lvot_vti", "n_exact_duplicate_rows_excess"]) == 1
    assert int(alias.loc["arch_diam", "n_empty_description_rows"]) == 1
    assert int(alias.loc["mv_peak_e", "n_aliases_mapping_to_multiple_canonicals"]) == 1
    assert units.loc["lvot_vti", "unit_resolution_status"] == "MULTIPLE_SAME_FAMILY_CONVERSION_REQUIRED"
    assert units.loc["rv_diam", "unit_family_match_status"] == "MISMATCH_OR_MIXED"
    assert not bool(rows[rows["canonical_mapping"] == "body_surface_area"]["candidate_indexed"].any())
    assert bool(rows[rows["canonical_mapping"] == "left_atrial_volume_index"]["candidate_indexed"].all())


def test_canonical_summary_retains_only_counts_and_flags() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    summary = build_canonical_summary(rows, ALLOWED_TARGETS)
    vti = summary[summary["canonical_mapping"] == "lvot_vti"].iloc[0]
    assert bool(vti["multiple_nonunknown_normalized_units"])
    assert int(vti["n_normalized_units"]) == 2
    assert int(vti["n_exact_duplicate_rows_excess"]) == 1


def test_issue_classification_covers_required_concepts_and_evidence_vocabulary() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    issues = classify_unresolved_questions(rows, build_unit_summary(rows, ALLOWED_TARGETS))
    issue_map = {issue["issue_id"]: issue for issue in issues}
    assert REQUIRED_ISSUE_IDS.issubset(issue_map)
    assert {issue["evidence_type"] for issue in issues}.issubset(EVIDENCE_TYPES)
    assert issue_map["TR_MMHG_DEFINITION"]["evidence_type"] == "RESOLVED_BY_PROJECT_METADATA"
    assert issue_map["MITRAL_E_FIELD_RELATIONSHIP"]["evidence_type"] == "REQUIRES_VALUE_DISTRIBUTION_AUDIT"
    assert issue_map["LVEF_ALIASES"]["evidence_type"] == "REQUIRES_TECHNICAL_PIPELINE_REVIEW"
    assert issue_map["LVEDV_LVESV_FIELDS"]["evidence_type"] == "REQUIRES_TECHNICAL_PIPELINE_REVIEW"
    assert issue_map["LA_DIMEN_MEASUREMENT_EVIDENCE"]["evidence_type"] == "LITERATURE_ANSWERABLE"


def test_absent_project_field_is_not_claimed_from_candidate_patterns() -> None:
    frame = synthetic_mapping()
    frame = frame[frame["canonical_measurement"] == "lvef"].copy()
    rows = build_review_rows(frame, ("lvef",))
    issues = classify_unresolved_questions(rows, build_unit_summary(rows, ("lvef",)))
    issue_map = {issue["issue_id"]: issue for issue in issues}
    assert issue_map["MITRAL_E_FIELD_RELATIONSHIP"]["evidence_type"] == "NOT_RESOLVABLE_FROM_AVAILABLE_DATA"
    assert issue_map["LVEDV_LVESV_FIELDS"]["reason_code"] == "COMPLETE_SEARCH_NO_CANDIDATE"


def test_mitral_e_lexical_nonidentity_never_proves_distinct_constructs() -> None:
    frame = synthetic_mapping()
    frame = frame[frame["measurement"] != "RAW_MITRAL_E_SHARED"].copy()
    frame.loc[frame["canonical_measurement"] == "mv_peak_e", "measurement_description"] = "MV E wave velocity"
    frame.loc[
        frame["canonical_measurement"] == "mitral_e_velocity", "measurement_description"
    ] = "Mitral inflow peak E velocity"
    rows = build_review_rows(frame, ALLOWED_TARGETS)
    issues = classify_unresolved_questions(rows, build_unit_summary(rows, ALLOWED_TARGETS))
    relation = {issue["issue_id"]: issue for issue in issues}["MITRAL_E_FIELD_RELATIONSHIP"]
    assert relation["evidence_type"] == "REQUIRES_VALUE_DISTRIBUTION_AUDIT"

    frame.loc[
        frame["canonical_measurement"] == "mitral_e_velocity", "measurement_description"
    ] = "Lateral mitral annular e-prime tissue Doppler velocity"
    rows = build_review_rows(frame, ALLOWED_TARGETS)
    issues = classify_unresolved_questions(rows, build_unit_summary(rows, ALLOWED_TARGETS))
    relation = {issue["issue_id"]: issue for issue in issues}["MITRAL_E_FIELD_RELATIONSHIP"]
    assert relation["evidence_type"] == "RESOLVED_BY_PROJECT_METADATA"
    assert relation["reason_code"] == "EXPLICIT_INCOMPATIBLE_ACQUISITION_OR_SITE_SEMANTICS"


def test_questionnaire_contains_only_clinician_adjudication_issues() -> None:
    frame = synthetic_mapping()
    frame.loc[frame["canonical_measurement"] == "la_dimen", "measurement_description"] = "LA linear dimension"
    rows = build_review_rows(frame, ALLOWED_TARGETS)
    issues = classify_unresolved_questions(rows, build_unit_summary(rows, ALLOWED_TARGETS))
    questionnaire = build_clinician_questionnaire(issues, rows)
    assert "LA dimension plane" in questionnaire
    assert "Relationship between mitral E fields" not in questionnaire
    assert "LVEF_ALIASES" not in questionnaire
    for issue in issues:
        if issue["issue_id"] in questionnaire:
            assert issue["evidence_type"] == "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION"


def test_followup_prompt_uses_only_literature_issues_and_fails_unsafe_text_closed() -> None:
    columns = [
        "issue_id",
        "evidence_type",
        "allowlisted_target",
        "raw_name",
        "raw_description",
        "native_unit",
        "normalized_unit",
        "claim_level_question_pending_safe_review",
    ]
    safe = pd.DataFrame(
        [
            {
                "issue_id": "LA_DIMEN_MEASUREMENT_EVIDENCE",
                "evidence_type": "LITERATURE_ANSWERABLE",
                "allowlisted_target": "la_dimen",
                "raw_name": "RESTRICTED_RAW_NAME",
                "raw_description": "PLAX anteroposterior LA dimension at end-systole",
                "native_unit": "cm",
                "normalized_unit": "cm",
                "claim_level_question_pending_safe_review": True,
            },
            {
                "issue_id": "LVEF_ALIASES",
                "evidence_type": "REQUIRES_TECHNICAL_PIPELINE_REVIEW",
                "allowlisted_target": "lvef",
                "raw_name": "RESTRICTED_ALIAS",
                "raw_description": "Visual EF",
                "native_unit": "%",
                "normalized_unit": "%",
                "claim_level_question_pending_safe_review": True,
            },
        ],
        columns=columns,
    )
    prompt, gate = build_targeted_followup_prompt(safe)
    assert gate["status"] == "PASS_COPY_READY"
    assert "Exact repository canonical target: la_dimen" in prompt
    assert "RESTRICTED_RAW_NAME" not in prompt
    assert "LVEF_ALIASES" not in prompt

    unsafe = safe.iloc[[0]].copy()
    unsafe.loc[:, "raw_description"] = "Study ID 12345678 LA dimension"
    blocked_prompt, blocked_gate = build_targeted_followup_prompt(unsafe)
    assert blocked_gate["status"] == "FAIL"
    assert blocked_gate["prompt_generated"] is False
    assert "12345678" not in blocked_prompt


def test_metadata_review_rejects_patient_values_identifiers_and_blank_names() -> None:
    for forbidden_column in ("subject_id", "study_id", "result", "embedding_idx", "dicom_path"):
        frame = synthetic_mapping()
        frame[forbidden_column] = "SYNTHETIC"
        try:
            build_review_rows(frame, ALLOWED_TARGETS)
        except ValueError:
            continue
        raise AssertionError(f"Patient/value-level column was accepted: {forbidden_column}")
    for column in ("measurement", "canonical_measurement"):
        frame = synthetic_mapping()
        frame.loc[0, column] = " "
        try:
            build_review_rows(frame, ALLOWED_TARGETS)
        except ValueError:
            continue
        raise AssertionError(f"Blank required metadata was accepted: {column}")


def test_cli_separates_restricted_packet_from_exact_aggregate_inventory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        mapping = temporary / "mapping.csv"
        restricted = temporary / "restricted_packet"
        aggregate = temporary / "aggregate_packet"
        followup = temporary / "followup_packet"
        synthetic_mapping().to_csv(mapping, index=False)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "lvef_multitask_clinical_metadata.py"),
                "--mapping-csv",
                str(mapping),
                "--restricted-output-dir",
                str(restricted),
                "--aggregate-output-dir",
                str(aggregate),
                "--followup-output-dir",
                str(followup),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        stdout = json.loads(completed.stdout)
        assert stdout["status"] == "COMPLETE"
        assert stdout["safety_gate_passed"] is True
        assert "RAW_" not in completed.stdout
        assert str(temporary) not in completed.stdout
        assert {path.name for path in aggregate.iterdir()} == EXPECTED_AGGREGATE_FILES
        assert {path.name for path in followup.iterdir()} == {
            "openevidence_targeted_followup_prompt_generated.md",
            "openevidence_targeted_followup_safety_gate.json",
        }

        restricted_payload = "\n".join(path.read_text() for path in restricted.iterdir())
        aggregate_payload = "\n".join(path.read_text() for path in aggregate.iterdir())
        assert "SYNTHETIC_SECRET_DESCRIPTOR_Ω" in restricted_payload
        assert "SYNTHETIC_SECRET_UNIT_Ω" in restricted_payload
        assert "SYNTHETIC_SECRET_DESCRIPTOR_Ω" not in aggregate_payload
        assert "SYNTHETIC_SECRET_UNIT_Ω" not in aggregate_payload
        assert "RAW_lvef" not in aggregate_payload
        assert "RAW_MITRAL_E_SHARED" not in aggregate_payload

        manifest = json.loads((aggregate / "clinical_metadata_review_packet_manifest.json").read_text())
        gate = json.loads((aggregate / "clinical_metadata_safety_gate.json").read_text())
        schema = json.loads((aggregate / "clinical_metadata_schema_summary.json").read_text())
        assert set(manifest["expected_aggregate_output_files"]) == EXPECTED_AGGREGATE_FILES
        assert manifest["n_allowlisted_targets_present"] == len(ALLOWED_TARGETS)
        assert gate["status"] == "PASS"
        assert gate["aggregate_file_inventory_exact"] is True
        assert set(schema["evidence_classification_vocabulary"]) == EVIDENCE_TYPES
        followup_gate = json.loads((followup / "openevidence_targeted_followup_safety_gate.json").read_text())
        followup_prompt = (followup / "openevidence_targeted_followup_prompt_generated.md").read_text()
        assert followup_gate["status"] == "PASS_COPY_READY"
        assert "RAW_" not in followup_prompt


def test_cli_records_missing_exact_lvef_without_inventing_mapping_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        mapping = temporary / "mapping_without_exact_lvef.csv"
        restricted = temporary / "restricted_packet"
        aggregate = temporary / "aggregate_packet"
        frame = synthetic_mapping()
        frame = frame[frame["canonical_measurement"] != "lvef"].copy()
        assert (frame["canonical_measurement"] == "source_specific_simpson_ef").any()
        frame.to_csv(mapping, index=False)

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "lvef_multitask_clinical_metadata.py"),
                "--mapping-csv",
                str(mapping),
                "--restricted-output-dir",
                str(restricted),
                "--aggregate-output-dir",
                str(aggregate),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

        schema = json.loads((aggregate / "clinical_metadata_schema_summary.json").read_text())
        manifest = json.loads((aggregate / "clinical_metadata_review_packet_manifest.json").read_text())
        for payload in (schema, manifest):
            assert payload["status"] == "COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS"
            assert payload["missing_allowlisted_targets"] == ["lvef"]
            assert payload["all_allowlisted_targets_present"] is False
            assert payload["missing_target_mapping_is_registry_authority"] is False

        stdout = json.loads(completed.stdout)
        assert stdout["status"] == "COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS"
        assert manifest["n_allowlisted_targets_present"] == len(ALLOWED_TARGETS) - 1

        restricted_rows = pd.read_csv(
            restricted / "clinical_metadata_review_rows_restricted.csv",
            keep_default_na=False,
        )
        assert not (restricted_rows["allowlisted_target"] == "lvef").any()
        candidate = restricted_rows[
            restricted_rows["canonical_mapping"] == "source_specific_simpson_ef"
        ]
        assert len(candidate) == 1
        assert candidate.iloc[0]["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED"
        assert candidate.iloc[0]["allowlisted_target"] == ""

        alias = pd.read_csv(aggregate / "clinical_metadata_alias_summary.csv").set_index("target")
        units = pd.read_csv(aggregate / "clinical_metadata_unit_summary.csv").set_index("target")
        assert int(alias.loc["lvef", "n_source_rows"]) == 0
        assert alias.loc["lvef", "alias_resolution_status"] == "NO_SOURCE_ROWS"
        assert int(units.loc["lvef", "n_source_rows"]) == 0
        assert units.loc["lvef", "unit_resolution_status"] == "NO_SOURCE_ROWS"


def test_cli_refuses_to_reuse_either_nonempty_output_directory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        mapping = temporary / "mapping.csv"
        restricted = temporary / "restricted_packet"
        aggregate = temporary / "aggregate_packet"
        restricted.mkdir()
        (restricted / "existing.txt").write_text("synthetic")
        synthetic_mapping().to_csv(mapping, index=False)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "lvef_multitask_clinical_metadata.py"),
                "--mapping-csv",
                str(mapping),
                "--restricted-output-dir",
                str(restricted),
                "--aggregate-output-dir",
                str(aggregate),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2
        payload = json.loads(completed.stdout)
        assert payload["status"] == "BLOCKED_AUDIT_EXCEPTION"
        assert (restricted / "existing.txt").read_text() == "synthetic"
