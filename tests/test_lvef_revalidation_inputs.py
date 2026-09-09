"""Synthetic input lineage, units and exact common-row regressions."""
import io
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_lvef_revalidation_inputs as p
from lvef_revalidation_authority import AuthorityError, canonical, publish
from lvef_revalidation_analysis import row_fingerprint


def roster():
    return p.selected_roster(pd.DataFrame({"subject_id": [3, 1, 2], "study_id": [30, 10, 20],
        "measurement_id": [300, 100, 200]}), pd.DataFrame({"subject_id": [1, 2, 3], "split": ["train", "val", "test"]}))


def measurements(rows):
    return p.selected_measurements(pd.DataFrame(rows, columns=["subject_id", "study_id", "measurement_id", "measurement", "result", "unit"]), roster())


def test_one_study_owner_and_exact_split_membership():
    r = roster()
    assert r.subject_id.tolist() == ["1", "2", "3"]
    selected = r.drop(columns="split")
    split = r[["subject_id", "split"]]
    selected.loc[1, "study_id"] = "10"
    with pytest.raises(AuthorityError, match="ONE_STUDY_OWNERSHIP"):
        p.selected_roster(selected, split)
    with pytest.raises(AuthorityError, match="SPLIT_MEMBERSHIP"):
        p.selected_roster(r.drop(columns="split"), pd.concat([split, pd.DataFrame({"subject_id": [4], "split": ["train"]})]))
    with pytest.raises(AuthorityError, match="IDENTIFIER"):
        p.ids(pd.Series([1, 2.5]))


def test_selected_report_and_study_ownership_before_aggregation():
    rows = [[1, 10, 100, "lvef", 30, "%"], [1, 10, 999, "lvef", 99, "%"]]
    assert measurements(rows).numeric.tolist() == [30]
    rows.append([1, 99, 100, "lvef", 55, "%"])
    with pytest.raises(AuthorityError, match="REPORT_STUDY_OWNERSHIP"):
        measurements(rows)


def test_exact_lvef_is_separate_and_repeated_label_median_never_aliases():
    v = measurements([[1, 10, 100, "lvef", 30, ""], [1, 10, 100, "lvef", 50, ""],
        [1, 10, 100, "LVEF", 90, "%"], [2, 20, 200, "lvef", "not numeric", "%"]])
    mapping = pd.DataFrame({"measurement": ["LVEF"], "canonical_measurement": ["lvef"]})
    y, meta = p.candidate_targets(v, mapping, ["lvef"], roster())
    assert y.lvef.iloc[0] == 40 and y.lvef.iloc[1:].isna().all()
    assert meta["lvef"]["source_raw_fields"] == ["lvef"]
    assert meta["lvef"]["repeated_valid_rows_aggregated"] == 1
    assert not meta["lvef"]["source_raw_aggregation_clinically_approved"]


def test_explicit_unit_conversion_before_median_rejects_dimension_mismatch():
    v = measurements([[1, 10, 100, "length", 2, "cm"], [1, 10, 100, "length", 40, "mm"],
        [2, 20, 200, "velocity", 2, "m/s"], [2, 20, 200, "velocity", 20, "ms"],
        [3, 30, 300, "length", 4, "unknown"]])
    mapping = pd.DataFrame({"measurement": ["length", "velocity"], "canonical_measurement": ["lvot_vti", "av_pk_vel"]})
    y, meta = p.candidate_targets(v, mapping, ["lvot_vti", "av_pk_vel"], roster())
    assert y.lvot_vti.iloc[0] == 30 and y.av_pk_vel.iloc[1] == 200
    assert np.isnan(y.lvot_vti.iloc[2])
    assert meta["av_pk_vel"]["unit_incompatible_numeric_rows_excluded"] == 1
    assert meta["lvot_vti"]["observed_selected_before_imaging"] == 2
    assert meta["lvot_vti"]["valid_units_before_imaging"] == 1


def test_ambiguous_mapping_never_lexically_chooses_a_construct():
    v = measurements([[1, 10, 100, "ambiguous", 3, "cm"]])
    mapping = pd.DataFrame({"measurement": ["ambiguous", "ambiguous"], "canonical_measurement": ["lvot_vti", "lvot_diam"]})
    y, meta = p.candidate_targets(v, mapping, ["lvot_vti", "lvot_diam"], roster())
    assert y[["lvot_vti", "lvot_diam"]].isna().all().all()
    assert all(m["ambiguous_raw_mapping_fields_excluded"] == 1 for m in meta.values())


def test_raw_preparation_retains_all_missing_rows_and_does_no_imputation():
    v = measurements([[1, 10, 100, "context", 3, "cm"], [2, 20, 200, "context", "missing", "cm"]])
    mapping = pd.DataFrame({"measurement": ["context"], "unit": ["cm"]})
    x, names, units = p.raw_structured_matrix(v, roster(), mapping)
    assert names == ["context"] and units == {"context": "mm"}
    assert x.shape == (3, 1) and x[0, 0] == 30 and np.isnan(x[1:]).all()


def test_held_out_numeric_availability_and_incompatible_units_cannot_remove_training_feature():
    v = measurements([[1, 10, 100, "context", 3, "cm"], [2, 20, 200, "context", 5, "ms"],
        [3, 30, 300, "context", "missing", "ms"]])
    mapping = pd.DataFrame({"measurement": ["context"], "unit": ["cm"]})
    first, names, units = p.raw_structured_matrix(v, roster(), mapping)
    v.loc[v.subject_id != "1", ["numeric", "normalized_value"]] = 99999
    second, _, changed_units = p.raw_structured_matrix(v, roster(), mapping)
    assert units == changed_units == {"context": "mm"}
    assert first[0, 0] == second[0, 0] == 30 and np.isnan(first[1:]).all() and np.isnan(second[1:]).all()


def test_loader_exact_rows_labels_and_shared_values_with_tamper_rejection(tmp_path):
    root = tmp_path.resolve(); root.chmod(0o700)
    rows = pd.DataFrame({"subject_id": ["1", "2", "3"], "study_id": ["10", "20", "30"], "split": ["train"] * 3})
    arrays = io.BytesIO()
    np.savez_compressed(arrays, vision=np.ones((3, 2)), structured=np.array([[1.], [np.nan], [3.]]),
        targets=np.array([[40.], [50.], [np.nan]]), target_names=np.array(["lvef"]), structured_names=np.array(["context"]))
    art = {"rows": p.write_private(root / "rows.csv", rows.to_csv(index=False).encode()),
           "arrays": p.write_private(root / "arrays.npz", arrays.getvalue())}
    receipt = root / "inputs.json"
    publish(receipt, {"target_names": ["lvef"], "structured_names": ["context"], "split_artifacts": {"train": art}})
    result = p.load_modality_rows(receipt, "train", "lvef")
    common = rows.iloc[:2].assign(lvef=[40., 50.])
    assert {row_fingerprint(r) for r in result.values()} == {p.row_hash(common, "lvef")}
    assert all(r.subject_ids == ("1", "2") for r in result.values())
    assert np.isnan(result["structured_only"].structured[1, 0])
    assert np.array_equal(result["vision_only"].vision, result["early_fusion"].vision)
    (root / "rows.csv").write_text(rows.iloc[::-1].to_csv(index=False))
    with pytest.raises(AuthorityError, match="ARTIFACT_CHANGED"):
        p.load_modality_rows(receipt, "train", "lvef")
