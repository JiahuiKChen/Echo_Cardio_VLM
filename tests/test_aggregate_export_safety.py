from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lvef_multitask_audit_utils import (
    assert_aggregate_safe_columns,
    assert_aggregate_safe_json,
    require_restricted_path,
    repository_root,
    run_guarded,
)


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


def test_patient_level_json_keys_are_rejected_recursively() -> None:
    for payload in ({"study_id": "SYNTHETIC"}, {"nested": [{"y_pred": 1.0}]}):
        try:
            assert_aggregate_safe_json(payload)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe aggregate JSON key was accepted: {payload}")


def test_guarded_main_converts_schema_exception_to_blocking_exit() -> None:
    def raises_schema_error() -> int:
        raise ValueError("synthetic sensitive message is not emitted")

    assert run_guarded(raises_schema_error) == 2
