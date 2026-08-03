from pathlib import Path
import sys

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_masked_report_completion_panel import apply_mask_before_preprocessing, resolve_targets


def test_target_removed_before_any_future_preprocessing() -> None:
    panel = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
            "task__fs": [30.0, 40.0],
            "task__height_cm": [170.0, None],
        }
    )
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
                "canonical_name": "height_cm",
                "strict_panel_inclusion": True,
                "pragmatic_panel_inclusion": True,
            },
        ]
    )
    predictors, target, summary = apply_mask_before_preprocessing(panel, registry, "fs", "strict")
    assert "task__fs" not in predictors.columns
    assert predictors["task__height_cm"].isna().sum() == 1
    assert target.tolist() == [30.0, 40.0]
    assert summary["direct_target_removed_before_preprocessing"] is True
    assert summary["execution_order"].startswith("RAW_TARGET_AND_FAMILY_REMOVAL")


def test_default_masking_fails_if_registry_omits_a_frozen_panel_target() -> None:
    try:
        resolve_targets({"fs", "lvidd"}, {"fs", "lvef"}, [])
    except ValueError:
        return
    raise AssertionError("Incomplete dependency registry coverage was accepted")


def test_default_masking_uses_all_panel_targets_and_allows_registry_only_lvef() -> None:
    assert resolve_targets({"fs", "lvidd"}, {"fs", "lvidd", "lvef"}, []) == ["fs", "lvidd"]
