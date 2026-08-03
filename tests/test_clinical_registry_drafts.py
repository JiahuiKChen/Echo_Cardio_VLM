from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lvef_multitask_clinical_metadata import ALLOWED_TARGET_SET, ALLOWED_TARGETS


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(ROOT / "docs" / "lvef_multitask" / name, keep_default_na=False)


def test_dependency_draft_uses_only_exact_allowlisted_identifiers() -> None:
    frame = load("target_dependency_registry_clinical_draft.csv")
    assert set(frame["target"]).issubset(ALLOWED_TARGET_SET)
    assert set(frame["predictor"]).issubset(ALLOWED_TARGET_SET)
    assert not frame["target"].str.contains("*", regex=False).any()
    assert not frame["predictor"].str.contains("*", regex=False).any()
    direct = frame[frame["relationship_category"] == "DIRECT_TARGET"]
    assert set(direct["target"]) == ALLOWED_TARGET_SET
    assert (direct["target"] == direct["predictor"]).all()
    assert (direct[["strict_predictor_allowed", "family_mask_predictor_allowed", "pragmatic_predictor_allowed"]] == "No").all().all()


def test_threshold_draft_covers_every_target_and_preserves_endpoint_inequalities() -> None:
    frame = load("clinical_threshold_registry_draft.csv")
    assert set(frame["target"]) == ALLOWED_TARGET_SET
    historical = frame[frame["threshold_id"] == "historical_primary_lt40"].iloc[0]
    assert historical["target"] == "lvef"
    assert historical["operator"] == "<"
    assert float(historical["value"]) == 40.0
    secondary = frame.set_index("threshold_id")
    assert secondary.loc["secondary_le40", "operator"] == "<="
    assert float(secondary.loc["secondary_le40", "value"]) == 40.0
    assert secondary.loc["secondary_lt50", "operator"] == "<"
    assert float(secondary.loc["secondary_lt50", "value"]) == 50.0

    exact_common = secondary.loc["exact_40_label_count_common"]
    assert exact_common["operator"] == "=="
    assert float(exact_common["value"]) == 40.0
    assert exact_common["evidence_status"] == "VERIFIED_PHASE1C_AGGREGATE_LABEL_AUDIT"
    assert "n=2833" in exact_common["conditions"]
    assert "103 labels" in exact_common["notes"]

    exact_test = secondary.loc["exact_40_label_count_common_test"]
    assert exact_test["operator"] == "=="
    assert float(exact_test["value"]) == 40.0
    assert "n=426" in exact_test["conditions"]
    assert "20 test labels" in exact_test["notes"]


def test_margin_draft_marks_all_lvef_candidate_values_as_expert_inference() -> None:
    frame = load("clinical_margin_registry_draft.csv")
    assert set(frame["target"]) == ALLOWED_TARGET_SET
    lvef = frame[frame["target"] == "lvef"]
    assert len(lvef) == 8
    assert set(lvef["evidence_tier"]) == {"EXPERT_INFERENCE"}
    assert not lvef["analysis_role"].str.contains("CLINICAL_TIE", regex=False).any()
    locked_non_lvef = frame[(frame["target"] != "lvef") & (frame["decision_status"] == "READY_FOR_OWNER_LOCK")]
    assert locked_non_lvef.empty


def test_context_targets_are_not_presented_as_primary_echo_measurement_candidates() -> None:
    frame = load("clinical_margin_registry_draft.csv")
    context = {
        "body_surface_area",
        "height_cm",
        "resting_sbp",
        "resting_dbp",
        "resting_hr",
    }
    rows = frame[frame["target"].isin(context)]
    assert set(rows["target"]) == context
    assert set(rows["analysis_role"]) == {"NOT_IN_PRIMARY_ECHO_MACRO"}


def test_exact_allowlist_order_starts_with_anchor_then_verified_legacy_order() -> None:
    assert ALLOWED_TARGETS[0] == "lvef"
    assert ALLOWED_TARGETS[1:5] == (
        "body_surface_area",
        "resting_sbp",
        "resting_dbp",
        "resting_hr",
    )
    assert len(ALLOWED_TARGETS) == 30
