from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_common_evaluation_denominators import compute_common_ids, labels_equal_on_common


def synthetic_frame(studies: list[str], labels: list[float] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame({"study_id": studies})
    if labels is not None:
        frame["y_true"] = labels
    return frame


def test_common_denominator_identity() -> None:
    frames = {
        "vision": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"]),
        "structured": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"]),
        "fusion": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"]),
    }
    sets, common = compute_common_ids(frames)
    assert common == {"SYN_STUDY_A", "SYN_STUDY_B"}
    assert all(values == common for values in sets.values())


def test_common_denominator_detects_one_modality_difference() -> None:
    frames = {
        "vision": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"]),
        "structured": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_C"]),
        "fusion": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"]),
    }
    sets, common = compute_common_ids(frames)
    assert common == {"SYN_STUDY_A"}
    assert sets["structured"] != sets["vision"]


def test_labels_must_match_on_common_ids() -> None:
    frames = {
        "vision": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"], [40.0, 50.0]),
        "fusion": synthetic_frame(["SYN_STUDY_A", "SYN_STUDY_B"], [40.0, 51.0]),
    }
    equal, mismatches = labels_equal_on_common(
        frames, {"SYN_STUDY_A", "SYN_STUDY_B"}, "y_true", ("study_id",)
    )
    assert equal is False
    assert mismatches == 1


def test_long_format_task_denominators_are_supported() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "task_col": ["task__alpha", "task__alpha"],
                "y_true": [1.0, 2.0],
            }
        ),
        "fusion": pd.DataFrame(
            {
                "study_id": ["SYN_STUDY_A"],
                "task_col": ["task__alpha"],
                "y_true": [1.0],
            }
        ),
    }
    sets, common = compute_common_ids(frames, target="task__alpha")
    assert common == {"SYN_STUDY_A"}
    assert sets["vision"] == {"SYN_STUDY_A", "SYN_STUDY_B"}
