from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_lvef_threshold_counts import threshold_count_rows  # noqa: E402


def _synthetic_authorities() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected = pd.DataFrame(
        {
            "subject_id": ["A", "B", "C", "D"],
            "study_id": ["SA", "SB", "SC", "SD"],
            "measurement_id": ["MA", "MB", "MC", "MD"],
        }
    )
    structured = pd.DataFrame(
        {
            "subject_id": ["A", "A", "B", "C", "D"],
            "measurement_id": ["MA", "MA", "MB", "MC", "MD"],
            "measurement": ["lvef", "lvef", "lvef", "lvef", "lvef"],
            "result": [38.0, 42.0, 40.0, 39.0, 50.0],
        }
    )
    imaging = selected.loc[selected["study_id"] != "SB", ["subject_id", "study_id"]]
    split_map = pd.DataFrame(
        {
            "subject_id": ["A", "B", "C", "D"],
            "split": ["train", "val", "test", "test"],
        }
    )
    return selected, structured, imaging, split_map


def test_exact_40_counts_use_historical_median_and_common_imaging_scope() -> None:
    selected, structured, imaging, split_map = _synthetic_authorities()
    rows = threshold_count_rows(
        selected, structured, imaging, split_map, threshold=40.0
    )
    keyed = {(row["cohort_scope"], row["split"]): row for row in rows}
    assert keyed[("selected_preimaging", "all")]["n_observed_lvef"] == 4
    assert (
        keyed[("selected_preimaging", "all")][
            "n_labels_exactly_equal_threshold"
        ]
        == 2
    )
    assert (
        keyed[("selected_preimaging", "train")][
            "n_labels_exactly_equal_threshold"
        ]
        == 1
    )
    assert (
        keyed[("selected_preimaging", "val")][
            "n_labels_exactly_equal_threshold"
        ]
        == 1
    )
    assert (
        keyed[("primary_common_imaging_eligible", "all")][
            "n_labels_exactly_equal_threshold"
        ]
        == 1
    )
    assert (
        keyed[("primary_common_imaging_eligible", "val")][
            "n_labels_exactly_equal_threshold"
        ]
        == 0
    )
    assert all(
        set(row)
        == {
            "cohort_scope",
            "split",
            "threshold",
            "comparison",
            "n_observed_lvef",
            "n_labels_exactly_equal_threshold",
        }
        for row in rows
    )
    rendered = repr(rows)
    assert "subject_id" not in rendered
    assert "study_id" not in rendered
    assert "SA" not in rendered


def test_threshold_count_rejects_nonselected_imaging_study() -> None:
    selected, structured, imaging, split_map = _synthetic_authorities()
    imaging = pd.concat(
        [
            imaging,
            pd.DataFrame({"subject_id": ["X"], "study_id": ["SX"]}),
        ],
        ignore_index=True,
    )
    try:
        threshold_count_rows(
            selected, structured, imaging, split_map, threshold=40.0
        )
    except ValueError:
        return
    raise AssertionError("Nonselected imaging study was accepted")


def test_threshold_count_rejects_inexact_split_authority() -> None:
    selected, structured, imaging, split_map = _synthetic_authorities()
    split_map = split_map.iloc[:-1].copy()
    try:
        threshold_count_rows(
            selected, structured, imaging, split_map, threshold=40.0
        )
    except ValueError:
        return
    raise AssertionError("Incomplete split authority was accepted")
