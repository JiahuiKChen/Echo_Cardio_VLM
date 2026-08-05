from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_lvef_multitask_technical_metadata import (
    TECHNICAL_ISSUE_IDS,
    build_issue_summary,
    build_lvef_authority,
    build_pairwise_scale_diagnostics,
    build_restricted_evidence,
    build_train_completeness,
    build_train_nonnumeric_profile,
    build_training_distributions,
    prepare_selected_structured,
    validate_full_mapping_authority,
)
from lvef_multitask_clinical_metadata import ALLOWED_TARGETS, build_review_rows


def review_rows() -> pd.DataFrame:
    defaults = {
        "raw_description": "synthetic description",
        "canonical_source": "synthetic",
        "native_unit": "cm",
        "normalized_unit": "cm",
        "candidate_lvef_alias_or_method": False,
        "candidate_lvef_method": False,
        "candidate_lvedv_lvesv": False,
        "candidate_wall_motion": False,
        "candidate_ratio": False,
        "candidate_lv_mass_or_rwt": False,
        "candidate_bsa_or_weight": False,
    }
    rows = []
    for values in (
        {
            "allowlisted_target": "body_surface_area",
            "raw_name": "synthetic_bsa",
            "canonical_mapping": "body_surface_area",
            "candidate_bsa_or_weight": True,
        },
        {
            "allowlisted_target": "la_dimen",
            "raw_name": "synthetic_la",
            "canonical_mapping": "la_dimen",
        },
        {
            "allowlisted_target": "",
            "raw_name": "synthetic_edv",
            "canonical_mapping": "source_specific_edv",
            "candidate_lvedv_lvesv": True,
        },
        {
            "allowlisted_target": "",
            "raw_name": "synthetic_ef_alias",
            "canonical_mapping": "source_specific_ef",
            "candidate_lvef_alias_or_method": True,
            "candidate_lvef_method": True,
        },
        {
            "allowlisted_target": "",
            "raw_name": "synthetic_mass",
            "canonical_mapping": "source_specific_mass",
            "candidate_lv_mass_or_rwt": True,
        },
        {
            "allowlisted_target": "",
            "raw_name": "synthetic_ratio",
            "canonical_mapping": "source_specific_ratio",
            "candidate_ratio": True,
        },
        {
            "allowlisted_target": "mv_peak_e",
            "raw_name": "synthetic_e",
            "canonical_mapping": "mv_peak_e",
        },
        {
            "allowlisted_target": "mv_peak_e",
            "raw_name": "synthetic_e_mps",
            "canonical_mapping": "mv_peak_e",
            "native_unit": "m/s",
            "normalized_unit": "m/s",
        },
        {
            "allowlisted_target": "",
            "raw_name": "synthetic_wall_motion",
            "canonical_mapping": "source_specific_wall_motion",
            "candidate_wall_motion": True,
        },
    ):
        row = defaults.copy()
        row.update(values)
        rows.append(row)
    return pd.DataFrame(rows)


def test_nine_issue_set_and_aggregate_summary_are_fixed() -> None:
    evidence = build_restricted_evidence(review_rows())
    summary = build_issue_summary(evidence)
    assert tuple(summary["issue_id"]) == TECHNICAL_ISSUE_IDS
    assert len(summary) == 9
    disposition = dict(zip(summary["issue_id"], summary["disposition"]))
    assert disposition["LVEF_METHOD_MIXTURE"] == "PENDING_COMPLETE_LVEF_METHOD_LINEAGE_REVIEW"
    assert disposition["WALL_MOTION_FIELDS"] == "PENDING_REGIONAL_VS_GLOBAL_SUBCLASSIFICATION"
    assert disposition["LVEF_ALIASES"] == "PENDING_RESTRICTED_TECHNICAL_REVIEW"
    assert not summary["mapping_universe_completeness_proven"].any()
    assert not summary["absence_claim_authorized"].any()
    assert not summary["confirmatory_performance_accessed"].any()
    assert "raw_name" not in summary.columns
    assert "raw_description" not in summary.columns
    assert "native_unit" not in summary.columns
    for column in (
        "evidence_inspected",
        "residual_limitation",
        "leakage_consequence",
        "strict_panel_consequence",
        "family_mask_consequence",
        "pragmatic_panel_consequence",
    ):
        assert summary[column].astype(str).str.strip().ne("").all()
    wall_motion = summary[summary["issue_id"] == "WALL_MOTION_FIELDS"].iloc[0]
    assert "wall-motion score/index" in wall_motion["leakage_consequence"]
    assert "segment" in wall_motion["strict_panel_consequence"]
    method = summary[summary["issue_id"] == "LVEF_METHOD_MIXTURE"].iloc[0]
    assert "does not prove a mixed-method" in method["residual_limitation"]
    volumes = summary[summary["issue_id"] == "LVEDV_LVESV_FIELDS"].iloc[0]
    assert "volume-derived EF" in volumes["leakage_consequence"]
    assert "target provenance" in volumes["leakage_consequence"]


def test_training_distributions_exclude_validation_and_test_rows() -> None:
    structured = pd.DataFrame(
        {
            "subject_id": [1, 2, 3, 1, 2],
            "measurement_id": [11, 22, 33, 11, 22],
            "measurement": ["synthetic_e", "synthetic_e", "synthetic_e", "lvef", "lvef"],
            "result": [80.0, 999.0, -999.0, 40.0, 50.0],
        }
    )
    selected = pd.DataFrame({"subject_id": [1, 2, 3], "measurement_id": [11, 22, 33]})
    split_map = pd.DataFrame({"subject_id": [1, 2, 3], "split": ["train", "val", "test"]})
    values = prepare_selected_structured(structured, selected, split_map)
    evidence = build_restricted_evidence(review_rows())
    distributions = build_training_distributions(values, evidence)
    velocity = distributions[
        (distributions["issue_id"] == "VELOCITY_MPS_CMPS_UNITS")
        & (distributions["raw_name"] == "synthetic_e")
    ].iloc[0]
    assert velocity["split"] == "train"
    assert velocity["n_numeric_rows"] == 1
    assert velocity["median"] == 80.0


def test_selected_and_split_authorities_must_be_exact_and_unique() -> None:
    structured = pd.DataFrame(
        {
            "subject_id": [1],
            "measurement_id": [11],
            "measurement": ["lvef"],
            "result": [40.0],
        }
    )
    duplicate_selected = pd.DataFrame(
        {"subject_id": [1, 1], "measurement_id": [11, 11]}
    )
    split_map = pd.DataFrame({"subject_id": [1], "split": ["train"]})
    try:
        prepare_selected_structured(structured, duplicate_selected, split_map)
    except ValueError as exc:
        assert "not unique" in str(exc)
    else:
        raise AssertionError("Duplicate selected linkage key was accepted")

    selected = pd.DataFrame({"subject_id": [1], "measurement_id": [11]})
    wrong_split = pd.DataFrame({"subject_id": [2], "split": ["train"]})
    try:
        prepare_selected_structured(structured, selected, wrong_split)
    except ValueError as exc:
        assert "do not exactly equal" in str(exc)
    else:
        raise AssertionError("Nonidentical selected/split subject sets were accepted")


def test_nonnumeric_values_and_units_are_retained_but_train_audit_is_scoped() -> None:
    structured = pd.DataFrame(
        {
            "subject_id": [1, 1, 2],
            "measurement_id": [11, 11, 22],
            "measurement": ["synthetic_wall_motion", "synthetic_e", "validation_only"],
            "result": ["akinetic", "80", "test poison"],
            "unit": ["category", "cm/s", "category"],
        }
    )
    selected = pd.DataFrame({"subject_id": [1, 2], "measurement_id": [11, 22]})
    split_map = pd.DataFrame({"subject_id": [1, 2], "split": ["train", "val"]})
    values = prepare_selected_structured(structured, selected, split_map)
    wall = values[values["_raw_name"] == "synthetic_wall_motion"].iloc[0]
    assert wall["_raw_value"] == "akinetic"
    assert wall["_unit"] == "category"
    assert not wall["_value_is_finite_numeric"]

    rows = review_rows()
    evidence = build_restricted_evidence(rows)
    full_mapping = rows[["raw_name", "canonical_mapping"]].copy()
    completeness, summary = build_train_completeness(
        values,
        rows,
        evidence,
        full_mapping,
    )
    wall_complete = completeness[completeness["raw_name"] == "synthetic_wall_motion"].iloc[0]
    assert wall_complete["n_train_nonnumeric_rows"] == 1
    assert wall_complete["train_unit_tokens"] == "category"
    assert "validation_only" not in set(completeness.loc[completeness["present_in_train"], "raw_name"])
    assert summary["validation_or_test_values_used"] is False
    assert summary["mapping_universe_completeness_proven"] is True
    assert summary["absence_claim_authorized"] is True
    assert "NOT_SOURCE_GENERATION_LINEAGE" in summary["absence_claim_scope"]
    profile = build_train_nonnumeric_profile(values, evidence)
    wall_profile = profile[profile["raw_name"] == "synthetic_wall_motion"].iloc[0]
    assert wall_profile["raw_value"] == "akinetic"
    assert wall_profile["unit"] == "category"
    assert wall_profile["n_train_rows"] == 1
    assert "test poison" not in set(profile["raw_value"])


def test_pairwise_diagnostics_use_prespecified_scales_and_train_only_common_reports() -> None:
    structured = pd.DataFrame(
        {
            "subject_id": [1, 1, 1, 1, 2, 2],
            "measurement_id": [11, 11, 11, 11, 22, 22],
            "measurement": [
                "synthetic_e",
                "synthetic_e_mps",
                "synthetic_ef_alias",
                "lvef",
                "synthetic_e",
                "synthetic_e_mps",
            ],
            "result": [80.0, 0.8, 0.4, 40.0, 999.0, 0.1],
            "unit": ["cm/s", "m/s", "fraction", "%", "cm/s", "m/s"],
        }
    )
    selected = pd.DataFrame({"subject_id": [1, 2], "measurement_id": [11, 22]})
    split_map = pd.DataFrame({"subject_id": [1, 2], "split": ["train", "test"]})
    values = prepare_selected_structured(structured, selected, split_map)
    diagnostics = build_pairwise_scale_diagnostics(
        values,
        build_restricted_evidence(review_rows()),
    )

    velocity = diagnostics[
        (diagnostics["issue_id"] == "VELOCITY_MPS_CMPS_UNITS")
        & (diagnostics["scale_applied_to_right"] == 100.0)
    ].iloc[0]
    assert velocity["n_same_report_pairs"] == 1
    assert velocity["n_exact_equal"] == 1
    assert velocity["fraction_numerically_equal"] == 1.0

    ef_alias = diagnostics[
        (diagnostics["issue_id"] == "LVEF_ALIASES")
        & (diagnostics["scale_applied_to_right"] == 0.01)
    ].iloc[0]
    assert ef_alias["n_same_report_pairs"] == 1
    assert ef_alias["n_exact_equal"] == 1
    assert set(diagnostics["diagnostic_authority"]) == {
        "DIAGNOSTIC_ONLY_NO_ALIAS_OR_UNIT_DECISION"
    }


def test_lvef_authority_uses_exact_name_median_and_preserves_inequalities() -> None:
    values = pd.DataFrame(
        {
            "_subject": [1, 1, 2, 3, 4],
            "_measurement_id": [11, 11, 22, 33, 44],
            "_raw_name": ["lvef", "lvef", "lvef_alias", "lvef", "lvef"],
            "_value": [38.0, 42.0, 10.0, 39.0, 50.0],
            "_split": ["train", "train", "val", "test", "val"],
        }
    )
    expected = {
        "all": {"n_observed": 3, "n_equal_40": 1},
        "train": {"n_observed": 1, "n_equal_40": 1},
        "val": {"n_observed": 1, "n_equal_40": 0},
        "test": {"n_observed": 1, "n_equal_40": 0},
    }
    counts, authority = build_lvef_authority(values, expected_counts=expected)
    all_row = counts[counts["split"] == "all"].iloc[0]
    assert all_row["n_observed"] == 3
    assert all_row["n_equal_40"] == 1
    assert all_row["n_lt_40"] == 1
    assert all_row["n_le_40"] == 2
    assert all_row["n_lt_50"] == 2
    assert authority["historical_primary_binary_endpoint"] == "lvef < 40"
    assert authority["mapping_row_required"] is False
    assert authority["method_mixture_resolved"] is False
    assert authority["selected_preimaging_denominators_reconciled"] is True
    assert authority["exact_40_counts_reconciled"] is True


def test_lvef_authority_fails_on_preserved_count_mismatch() -> None:
    values = pd.DataFrame(
        {
            "_subject": [1],
            "_measurement_id": [11],
            "_raw_name": ["lvef"],
            "_value": [40.0],
            "_split": ["train"],
        }
    )
    wrong = {
        "all": {"n_observed": 2, "n_equal_40": 1},
        "train": {"n_observed": 1, "n_equal_40": 1},
        "val": {"n_observed": 0, "n_equal_40": 0},
        "test": {"n_observed": 0, "n_equal_40": 0},
    }
    try:
        build_lvef_authority(values, expected_counts=wrong)
    except ValueError as exc:
        assert "reconciliation failed" in str(exc)
    else:
        raise AssertionError("Mismatched preserved LVEF counts were accepted")


def test_complete_mapping_must_exactly_regenerate_filtered_packet() -> None:
    mapping = pd.DataFrame(
        {
            "measurement": list(ALLOWED_TARGETS[1:]),
            "measurement_description": ["synthetic description"] * (len(ALLOWED_TARGETS) - 1),
            "canonical_measurement": list(ALLOWED_TARGETS[1:]),
            "canonical_source": ["synthetic"] * (len(ALLOWED_TARGETS) - 1),
            "unit": ["unit"] * (len(ALLOWED_TARGETS) - 1),
            "unit_norm": ["unit"] * (len(ALLOWED_TARGETS) - 1),
            "unit_category": ["synthetic"] * (len(ALLOWED_TARGETS) - 1),
        }
    )
    packet = build_review_rows(mapping, ALLOWED_TARGETS)
    normalized, summary = validate_full_mapping_authority(
        mapping,
        packet,
        expected_mapping_rows=len(mapping),
        expected_review_rows=len(packet),
    )
    assert len(normalized) == len(mapping)
    assert summary["review_packet_exactly_regenerated"] is True
    assert summary["mapping_universe_completeness_proven"] is True

    tampered = packet.copy()
    tampered.loc[0, "raw_description"] = "changed after packet generation"
    try:
        validate_full_mapping_authority(
            mapping,
            tampered,
            expected_mapping_rows=len(mapping),
            expected_review_rows=len(packet),
        )
    except ValueError as exc:
        assert "not the deterministic filtered view" in str(exc)
    else:
        raise AssertionError("A packet that diverged from the complete mapping was accepted")
