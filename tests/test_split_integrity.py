from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_subject_splits_and_denominators import audit_split_assignments


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
