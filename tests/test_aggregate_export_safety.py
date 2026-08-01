from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lvef_multitask_audit_utils import assert_aggregate_safe_columns, require_restricted_path, repository_root


def test_aggregate_counts_are_safe() -> None:
    frame = pd.DataFrame({"stage": ["synthetic"], "n_subjects": [3], "n_studies": [3]})
    assert_aggregate_safe_columns(frame)


def test_patient_level_columns_are_rejected() -> None:
    for column in ["subject_id", "study_id", "y_true", "y_pred", "embedding"]:
        try:
            assert_aggregate_safe_columns(pd.DataFrame({column: ["SYNTHETIC"]}))
        except ValueError:
            continue
        raise AssertionError(f"Unsafe aggregate column was accepted: {column}")


def test_restricted_output_inside_repo_is_rejected() -> None:
    try:
        require_restricted_path(repository_root() / "outputs" / "restricted")
    except ValueError:
        return
    raise AssertionError("Repository-local restricted output path was accepted")
