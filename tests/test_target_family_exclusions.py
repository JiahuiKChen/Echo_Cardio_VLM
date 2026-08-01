from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_masked_report_completion_panel import mask_columns_for_target


def test_strict_mask_removes_target_and_deterministic_family() -> None:
    registry = pd.DataFrame(
        [
            {
                "target": "fs",
                "canonical_name": "fs",
                "strict_panel_inclusion": False,
                "pragmatic_panel_inclusion": False,
            },
            {
                "target": "fs",
                "canonical_name": "left_ventricular_end_systolic_diameter",
                "strict_panel_inclusion": False,
                "pragmatic_panel_inclusion": False,
            },
            {
                "target": "fs",
                "canonical_name": "height_cm",
                "strict_panel_inclusion": True,
                "pragmatic_panel_inclusion": True,
            },
        ]
    )
    columns = [
        "subject_id",
        "task__fs",
        "task__left_ventricular_end_systolic_diameter",
        "task__height_cm",
    ]
    masked, retained, target_column = mask_columns_for_target(columns, registry, "fs", "strict")
    assert target_column == "task__fs"
    assert "task__fs" in masked
    assert "task__left_ventricular_end_systolic_diameter" in masked
    assert retained == ["task__height_cm"]


def test_unmapped_predictor_fails_closed() -> None:
    registry = pd.DataFrame(
        [
            {
                "target": "fs",
                "canonical_name": "fs",
                "strict_panel_inclusion": False,
                "pragmatic_panel_inclusion": False,
            }
        ]
    )
    masked, retained, _ = mask_columns_for_target(
        ["task__fs", "task__unreviewed_field"], registry, "fs", "strict"
    )
    assert "task__unreviewed_field" in masked
    assert retained == []
