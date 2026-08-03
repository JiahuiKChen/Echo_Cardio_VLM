from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_subject_splits_and_denominators import audit_split_assignments, exact_subject_coverage


def test_valid_subject_split_has_no_overlap() -> None:
    split_map = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B", "SYN_SUBJECT_C"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_B", "SYN_STUDY_C"],
            "split": ["train", "val", "test"],
        }
    )
    summary, warnings = audit_split_assignments(split_map)
    assert summary["valid"] is True
    assert summary["n_subjects_in_multiple_splits"] == 0
    assert warnings.empty


def test_subject_crossing_splits_fails() -> None:
    split_map = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A1", "SYN_STUDY_A2"],
            "split": ["train", "test"],
        }
    )
    summary, warnings = audit_split_assignments(split_map)
    assert summary["valid"] is False
    assert summary["n_subjects_in_multiple_splits"] == 1
    assert "SUBJECT_IN_MULTIPLE_SPLITS" in set(warnings["warning_type"])


def test_duplicate_subject_row_in_split_map_fails_even_with_same_split() -> None:
    split_map = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
            "split": ["train", "train"],
        }
    )
    summary, warnings = audit_split_assignments(split_map)
    assert summary["valid"] is False
    assert summary["n_duplicate_subject_rows"] == 1
    assert "DUPLICATE_SUBJECT_SPLIT_ROW" in set(warnings["warning_type"])


def test_split_map_rejects_null_blank_and_whitespace_identifiers() -> None:
    split_map = pd.DataFrame(
        {
            "subject_id": [None, "", "   ", "null", "SYN_E", "SYN_F", "SYN_G", "SYN_H"],
            "study_id": ["SYN_A", "SYN_B", "SYN_C", "SYN_D", None, "", "   ", "NULL"],
            "split": ["train", "val", "test", "train", "val", "test", "train", "test"],
        }
    )
    summary, warnings = audit_split_assignments(split_map)
    assert summary["valid"] is False
    assert summary["n_rows_missing_subject_id"] == 4
    assert summary["n_rows_missing_study_id"] == 4
    assert (warnings["warning_type"] == "MISSING_OR_BLANK_SUBJECT_ID").sum() == 4
    assert (warnings["warning_type"] == "MISSING_OR_BLANK_STUDY_ID").sum() == 4


def test_exact_selected_split_coverage_detects_extras_and_missing_subjects() -> None:
    split_map = pd.DataFrame(
        {"subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_EXTRA"], "split": ["train", "test"]}
    )
    selected = pd.DataFrame(
        {"subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_MISSING"], "study_id": ["SYN_A", "SYN_B"]}
    )
    row, warnings = exact_subject_coverage("selected", split_map, selected)
    assert row["subject_sets_identical"] is False
    assert row["n_cohort_subjects_missing_from_split"] == 1
    assert row["n_split_map_subjects_outside_cohort"] == 1
    assert {item["warning_type"] for item in warnings} == {
        "COHORT_SUBJECT_MISSING_FROM_SPLIT_MAP",
        "SPLIT_MAP_SUBJECT_OUTSIDE_COHORT",
    }


def test_exact_selected_split_coverage_fails_closed_on_blank_subject() -> None:
    split_map = pd.DataFrame({"subject_id": ["SYN_A"], "split": ["train"]})
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A", "   "], "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"]}
    )
    row, _ = exact_subject_coverage("selected", split_map, selected)
    assert row["n_cohort_rows_missing_subject_id"] == 1
    assert row["subject_sets_identical"] is False
