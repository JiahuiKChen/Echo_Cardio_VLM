from pathlib import Path
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analyze_measurement_missingness import missingness_rows, pairwise_rows
from audit_embedding_eligibility_overlap import classify_missing, stage_study_set
from build_target_dependency_registry import candidate_predictors, historical_tasks
from audit_lvef_multitask_artifacts import (
    audit_merged_clip_manifest_union,
    historical_selected_lvef_preimage,
    inspect_artifact,
    intersect_frame_by_studies,
    main as audit_artifact_main,
    numeric_lvef_subset,
    reconcile_lvef_label_provenance,
    selected_stage_reconciliation_rows,
    selected_cohort_integrity,
    selected_stage_containment,
    selected_stage_subject_mapping,
    stage_quality_counts,
    successful_stage_rows,
)


def test_stage_filters_distinguish_downloaded_readable_and_extracted() -> None:
    frame = pd.DataFrame(
        {
            "study_id": ["SYN_A", "SYN_B", "SYN_C"],
            "exists": [True, True, False],
            "read_ok": [True, False, False],
            "is_multiframe": [True, False, False],
            "write_ok": [True, False, False],
        }
    )
    downloaded, downloaded_flag = successful_stage_rows("downloaded_studies", frame)
    readable, readable_flag = successful_stage_rows("readable_dicoms", frame)
    cine, cine_flag = successful_stage_rows("cine_candidates", frame)
    extracted, extracted_flag = successful_stage_rows("extracted_clips", frame)
    assert downloaded_flag == "exists"
    assert readable_flag == "read_ok"
    assert cine_flag == "is_multiframe"
    assert extracted_flag == "write_ok"
    assert set(downloaded["study_id"]) == {"SYN_A", "SYN_B"}
    assert set(readable["study_id"]) == {"SYN_A"}
    assert set(cine["study_id"]) == {"SYN_A"}
    assert set(extracted["study_id"]) == {"SYN_A"}
    quality = stage_quality_counts(frame, downloaded, downloaded_flag)
    assert quality["n_input_studies"] == 3
    assert quality["n_studies_all_rows_successful"] == 2
    assert quality["study_success_rule"] == "AT_LEAST_ONE_SUCCESSFUL_ROW"


def test_stage_filter_blocks_when_required_success_flag_is_missing() -> None:
    wrong_schema = pd.DataFrame({"study_id": ["SYN_A"], "unrelated_status": [True]})
    for stage in (
        "downloaded_studies",
        "readable_dicoms",
        "cine_candidates",
        "extracted_clips",
        "clip_embeddings",
    ):
        try:
            successful_stage_rows(stage, wrong_schema)
        except ValueError:
            continue
        raise AssertionError(f"Wrong-schema stage manifest was accepted for {stage}")
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "wrong_schema.csv"
        wrong_schema.to_csv(artifact, index=False)
        try:
            stage_study_set([artifact], "downloaded_studies")
        except ValueError:
            return
        raise AssertionError("Overlap audit accepted a download manifest without a success flag")


def test_structured_artifact_blocks_without_measurement_and_value_columns() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "wrong_structured.csv"
        pd.DataFrame(
            {"subject_id": ["SYN_SUBJECT"], "study_id": ["SYN_STUDY"], "other": [1]}
        ).to_csv(artifact, index=False)
        row, successful, input_frame = inspect_artifact(
            "structured_measurements", [artifact]
        )
        assert row["status"] == "ERROR"
        assert successful is None
        assert input_frame is None


def test_selected_artifact_retains_measurement_id_for_label_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "selected.csv"
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT"],
                "study_id": ["SYN_STUDY"],
                "measurement_id": ["SYN_MEASUREMENT"],
                "nonessential": ["not retained"],
            }
        ).to_csv(artifact, index=False)
        row, successful, input_frame = inspect_artifact("selected_studies", [artifact])
        assert row["status"] == "OK"
        assert successful is not None and input_frame is not None
        assert list(successful.columns) == ["subject_id", "study_id", "measurement_id"]
        assert list(input_frame.columns) == ["subject_id", "study_id", "measurement_id"]


def test_repeated_stage_manifests_are_concatenated_and_success_filtered() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "first.csv"
        second = root / "second.csv"
        pd.DataFrame({"study_id": ["SYN_A", "SYN_B"], "exists": [True, False]}).to_csv(first, index=False)
        pd.DataFrame({"study_id": ["SYN_C"], "exists": [True]}).to_csv(second, index=False)
        assert stage_study_set([first, second], "downloaded_studies") == {"SYN_A", "SYN_C"}


def test_selected_funnel_separates_prior_stage_extras() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"], "study_id": ["SYN_A", "SYN_B"]}
    )
    stage = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_EXTRA"],
            "study_id": ["SYN_A", "SYN_STAGE_D_EXTRA"],
        }
    )
    rows = selected_stage_reconciliation_rows(selected, {"study_embeddings": stage})
    assert rows == [
        {
            "stage": "study_embeddings",
            "n_stage_studies_all": 2,
            "n_stage_studies_in_selected": 1,
            "n_stage_studies_outside_selected": 1,
            "n_selected_studies_absent": 1,
            "n_selected_studies_all_rows_successful": 1,
            "n_rows_in_selected": 1,
            "n_subjects_in_selected": 1,
        }
    ]


def test_selected_funnel_reports_all_requested_rows_successful_separately() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"], "study_id": ["SYN_A", "SYN_B"]}
    )
    input_stage = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A", "SYN_SUBJECT_B"],
            "study_id": ["SYN_A", "SYN_A", "SYN_B"],
        }
    )
    successful_stage = input_stage.iloc[[0, 2]].copy()
    rows = selected_stage_reconciliation_rows(
        selected,
        {"downloaded_studies": successful_stage},
        {"downloaded_studies": input_stage},
    )
    assert rows[0]["n_stage_studies_in_selected"] == 2
    assert rows[0]["n_selected_studies_all_rows_successful"] == 1


def test_selected_cohort_rejects_multiple_studies_for_one_subject() -> None:
    selected = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
        }
    )
    integrity = selected_cohort_integrity(selected)
    assert integrity["one_study_per_subject_valid"] is False
    assert integrity["n_subjects_with_multiple_studies"] == 1


def test_selected_cohort_rejects_null_blank_and_whitespace_identifiers() -> None:
    selected = pd.DataFrame(
        {
            "subject_id": [None, "", "   ", "null", "SYN_E", "SYN_F", "SYN_G", "SYN_H"],
            "study_id": ["SYN_A", "SYN_B", "SYN_C", "SYN_D", None, "", "   ", "NULL"],
        }
    )
    integrity = selected_cohort_integrity(selected)
    assert integrity["one_study_per_subject_valid"] is False
    assert integrity["n_rows_missing_subject_id"] == 4
    assert integrity["n_rows_missing_study_id"] == 4
    assert integrity["n_rows_missing_subject_or_study"] == 8


def test_selected_stage_subject_mapping_reports_aggregate_counts_and_restricted_ids() -> None:
    selected = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
            "study_id": ["100", "200"],
        }
    )
    stages = {
        "stage_one": pd.DataFrame(
            {
                "subject_id": [
                    "SYN_SUBJECT_A",
                    "WRONG_SUBJECT",
                    "OUTSIDE_A",
                    "OUTSIDE_B",
                ],
                "study_id": [100.0, 200.0, 999.0, 999.0],
            }
        ),
        "stage_two": pd.DataFrame(
            {
                "subject_id": [None, "SYN_SUBJECT_B"],
                "study_id": [100, 200],
            }
        ),
    }
    rows, restricted = selected_stage_subject_mapping(selected, stages)
    by_stage = {row["stage"]: row for row in rows}
    assert set(by_stage) == {"stage_one", "stage_two"}
    assert by_stage["stage_one"]["n_selected_studies_subject_mapping_mismatch"] == 1
    assert by_stage["stage_one"]["n_stage_studies_with_multiple_subjects"] == 1
    assert by_stage["stage_one"]["stage_identifier_integrity_valid"] is False
    assert by_stage["stage_two"]["n_selected_studies_subject_mapping_mismatch"] == 1
    assert by_stage["stage_two"]["stage_identifier_integrity_valid"] is False
    assert {
        (row["stage"], row["study_id"])
        for row in restricted
    } == {("stage_one", "200"), ("stage_one", "999"), ("stage_two", "100")}


def test_selected_numeric_lvef_intersection_excludes_nonselected_artifact_rows() -> None:
    numeric_lvef = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_B", "SYN_EXTRA"],
            "study_id": [100.0, 200.0, 999.0],
            "result": [55.0, 45.0, 60.0],
        }
    )
    selected = intersect_frame_by_studies(numeric_lvef, {"100", "200"})
    assert len(selected) == 2
    assert set(selected["subject_id"]) == {"SYN_A", "SYN_B"}


def test_numeric_lvef_subset_matches_historical_builder_exactly() -> None:
    frame = pd.DataFrame(
        {
            "measurement": ["lvef", "LVEF", " lvef ", "lvef"],
            "result": [55.0, 45.0, 35.0, "not numeric"],
            "result_numeric": [1.0, 2.0, 3.0, 99.0],
        }
    )
    selected = numeric_lvef_subset(frame)
    assert selected.index.tolist() == [0]
    assert selected.iloc[0]["result"] == 55.0


def test_selected_lvef_preimage_uses_subject_measurement_link_not_raw_study() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A"], "study_id": ["SELECTED"], "measurement_id": [10]}
    )
    structured = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_A"],
            "study_id": ["UNTRUSTED", "UNTRUSTED"],
            "measurement_id": [10, 10],
            "measurement": ["lvef", "lvef"],
            "result": [50.0, 60.0],
        }
    )
    linked = historical_selected_lvef_preimage(selected, structured)
    assert linked[["study_id", "lvef"]].to_dict("records") == [
        {"study_id": "SELECTED", "lvef": 55.0}
    ]


def test_lvef_label_provenance_matches_builder_median_and_restricts_discrepancy_ids() -> None:
    selected = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_B", "SYN_C"],
            "study_id": [100, 200, 300],
            "measurement_id": [10, 20, 30],
        }
    )
    structured = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_A", "SYN_B", "SYN_C", "SYN_A"],
            "study_id": [100, 100, 200, 300, 100],
            "measurement_id": [10, 10, 20, 30, 10],
            "measurement": ["lvef", "lvef", "lvef", "lvef", "lvot_vti"],
            "result": [50.0, 60.0, 40.0, 65.0, 22.0],
        }
    )
    manifest = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_A", "SYN_B", "SYN_OUTSIDE"],
            "study_id": [100, 100, 200, 999],
            "lvef": [55.0, 55.0, 41.0, 70.0],
            "lvef_binary_reduced": [0, 0, 0, 0],
        }
    )
    row, restricted = reconcile_lvef_label_provenance(selected, structured, manifest)
    assert row["n_structured_selected_lvef_studies"] == 3
    assert row["n_manifest_lvef_studies_all_artifact_diagnostic"] == 3
    assert row["n_manifest_selected_lvef_studies"] == 2
    assert row["n_structured_selected_only_studies"] == 1
    assert row["n_manifest_selected_only_studies"] == 0
    assert row["n_lvef_value_mismatch_studies"] == 1
    assert row["label_provenance_valid"] is False
    assert {(item["warning_type"], item["study_id"]) for item in restricted} == {
        ("STRUCTURED_SELECTED_LVEF_ABSENT_FROM_MANIFEST", "300"),
        ("LVEF_VALUE_MISMATCH", "200"),
    }


def test_lvef_label_provenance_rejects_conflicting_repeated_manifest_values() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A"], "study_id": [100], "measurement_id": [10]}
    )
    structured = pd.DataFrame(
        {
            "subject_id": ["SYN_A"],
            "study_id": [100],
            "measurement_id": [10],
            "measurement": ["lvef"],
            "result": [55.0],
        }
    )
    manifest = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_A"],
            "study_id": [100, 100],
            "lvef": [54.0, 56.0],
            "lvef_binary_reduced": [0, 0],
        }
    )
    row, restricted = reconcile_lvef_label_provenance(selected, structured, manifest)
    assert row["n_manifest_selected_lvef_conflict_studies"] == 1
    assert row["lvef_values_match_on_intersection"] is True
    assert row["label_provenance_valid"] is False
    assert {
        (item["warning_type"], item["study_id"]) for item in restricted
    } == {("CONFLICTING_LVEF_VALUES_WITHIN_MANIFEST_STUDY", "100")}


def test_lvef_label_provenance_rejects_missing_and_threshold_inconsistent_rows() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A"], "study_id": [100], "measurement_id": [10]}
    )
    structured = pd.DataFrame(
        {
            "subject_id": ["SYN_A"],
            "study_id": [100],
            "measurement_id": [10],
            "measurement": ["lvef"],
            "result": [35.0],
        }
    )
    manifest = pd.DataFrame(
        {
            "subject_id": ["SYN_A", "SYN_A"],
            "study_id": [100, 100],
            "lvef": [35.0, None],
            "lvef_binary_reduced": [0, 1],
        }
    )
    row, restricted = reconcile_lvef_label_provenance(selected, structured, manifest)
    assert row["n_manifest_rows_missing_study_or_lvef"] == 1
    assert row["n_manifest_rows_binary_threshold_mismatch"] == 1
    assert row["manifest_binary_labels_valid_and_threshold_consistent"] is False
    assert row["label_provenance_valid"] is False
    assert {item["warning_type"] for item in restricted} == {
        "CONFLICTING_BINARY_LABELS_WITHIN_MANIFEST_STUDY",
        "MANIFEST_ROW_MISSING_STUDY_OR_NUMERIC_LVEF",
        "MANIFEST_ROW_BINARY_THRESHOLD_MISMATCH",
    }


def test_artifact_main_emits_selected_only_lvef_funnel_and_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path = root / "selected.csv"
        structured_path = root / "structured.csv"
        labels_path = root / "labels.csv"
        selected = pd.DataFrame(
            {"subject_id": ["SYN_A"], "study_id": [100], "measurement_id": [10]}
        )
        selected.to_csv(selected_path, index=False)
        pd.DataFrame(
            {
                "subject_id": ["SYN_A", "SYN_EXTRA"],
                "study_id": [100, 999],
                "measurement_id": [10, 99],
                "measurement": ["lvef", "lvef"],
                "result": [55.0, 70.0],
            }
        ).to_csv(structured_path, index=False)
        pd.DataFrame(
            {
                "subject_id": ["SYN_A"],
                "study_id": [100],
                "split": ["test"],
                "lvef": [55.0],
                "lvef_binary_reduced": [0],
            }
        ).to_csv(labels_path, index=False)

        stage_flags = {
            "downloaded": ("exists", True),
            "readable": ("read_ok", True),
            "cine": ("is_multiframe", True),
            "extracted": ("write_ok", True),
            "clip": ("write_ok", True),
        }
        stage_paths: dict[str, Path] = {}
        for name, (flag, value) in stage_flags.items():
            path = root / f"{name}.csv"
            pd.DataFrame(
                {"subject_id": ["SYN_A"], "study_id": [100], flag: [value]}
            ).to_csv(path, index=False)
            stage_paths[name] = path
        study_embedding_path = root / "study_embeddings.csv"
        selected[["subject_id", "study_id"]].to_csv(study_embedding_path, index=False)

        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"
        previous_argv = sys.argv
        sys.argv = [
            "audit_lvef_multitask_artifacts.py",
            "--selected-studies",
            str(selected_path),
            "--downloaded-studies",
            str(stage_paths["downloaded"]),
            "--readable-dicoms",
            str(stage_paths["readable"]),
            "--cine-candidates",
            str(stage_paths["cine"]),
            "--extracted-clips",
            str(stage_paths["extracted"]),
            "--clip-embeddings",
            str(stage_paths["clip"]),
            "--study-embeddings",
            str(study_embedding_path),
            "--structured-measurements",
            str(structured_path),
            "--lvef-labels",
            str(labels_path),
            "--output-dir",
            str(output_dir),
            "--restricted-output-dir",
            str(restricted_dir),
        ]
        try:
            assert audit_artifact_main() == 0
        finally:
            sys.argv = previous_argv

        denominator = pd.read_csv(output_dir / "denominator_summary.csv")
        by_stage = denominator.set_index("stage")
        assert by_stage.loc["numeric_lvef_all_artifact_diagnostic", "n_studies"] == 2
        assert by_stage.loc["selected_numeric_lvef_before_imaging", "n_studies"] == 1
        assert by_stage.loc["selected_numeric_lvef_plus_embedding", "n_studies"] == 1
        provenance = pd.read_csv(output_dir / "lvef_label_provenance.csv").iloc[0]
        assert bool(provenance["label_provenance_valid"]) is True


def _synthetic_clip_manifest(
    subject: str, study: str, locator: str, *, embedding_idx: int = 0, norm: float = 1.0
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "embedding_idx": [embedding_idx],
            "subject_id": [subject],
            "study_id": [study],
            "dicom_filepath": [f"/restricted/dicom/{locator}.dcm"],
            "npz_path": [f"/restricted/clip/{locator}.npz"],
            "view_id": [1],
            "embedding_l2_norm": [norm],
            "write_ok": [True],
            "error": [None],
        }
    )


def test_merged_clip_manifest_equals_component_union_after_index_rewrite() -> None:
    first = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
    second = _synthetic_clip_manifest("SYN_B", "200", "clip_b")
    merged = pd.concat([second, first], ignore_index=True)
    merged["embedding_idx"] = [0, 1]
    row, restricted = audit_merged_clip_manifest_union(
        merged, [first, second], expected_components=2
    )
    assert row["component_union_provenance_valid"] is True
    assert row["success_row_counts_equal"] is True
    assert row["clip_key_sets_equal"] is True
    assert row["clip_key_multisets_equal"] is True
    assert row["row_payload_multisets_equal"] is True
    assert row["study_sets_equal"] is True
    assert restricted == []


def test_merged_clip_manifest_reports_key_and_row_payload_discrepancies_restricted_only() -> None:
    first = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
    second = _synthetic_clip_manifest("SYN_B", "200", "clip_b")
    merged_first = first.copy()
    merged_first["embedding_l2_norm"] = 9.0
    merged_extra = _synthetic_clip_manifest("SYN_C", "300", "clip_c", embedding_idx=1)
    merged = pd.concat([merged_first, merged_extra], ignore_index=True)
    row, restricted = audit_merged_clip_manifest_union(
        merged, [first, second], expected_components=2
    )
    assert row["component_union_provenance_valid"] is False
    assert row["n_source_only_clip_keys"] == 1
    assert row["n_merged_only_clip_keys"] == 1
    assert row["n_clip_keys_with_row_payload_mismatch"] == 1
    warning_types = {item["warning_type"] for item in restricted}
    assert "SOURCE_CLIP_KEY_NOT_IN_MERGED" in warning_types
    assert "MERGED_CLIP_KEY_NOT_IN_SOURCE_UNION" in warning_types
    assert "CLIP_ROW_PAYLOAD_MISMATCH" in warning_types
    assert {item["study_id"] for item in restricted} >= {"100", "200", "300"}


def test_merged_clip_manifest_rejects_duplicate_clip_keys_even_when_multisets_match() -> None:
    component = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
    duplicated_source = pd.concat([component, component], ignore_index=True)
    duplicated_merged = duplicated_source.copy()
    duplicated_merged["embedding_idx"] = [0, 1]
    row, restricted = audit_merged_clip_manifest_union(
        duplicated_merged, [duplicated_source], expected_components=1
    )
    assert row["clip_key_multisets_equal"] is True
    assert row["n_source_duplicate_clip_key_rows"] == 1
    assert row["component_union_provenance_valid"] is False
    assert any(
        item["warning_type"] == "DUPLICATE_CLIP_KEY_IN_SOURCE_COMPONENTS"
        for item in restricted
    )


def test_merged_clip_manifest_detects_swapped_payload_combinations_with_equal_column_multisets(
) -> None:
    component = pd.concat(
        [_synthetic_clip_manifest("SYN_SUBJECT", "100", "clip_a")] * 2,
        ignore_index=True,
    )
    component["embedding_idx"] = [0, 1]
    component["view_id"] = [1, 2]
    component["embedding_l2_norm"] = [10.0, 20.0]
    merged = component.copy()
    merged["embedding_l2_norm"] = [20.0, 10.0]

    for column in ("view_id", "embedding_l2_norm"):
        assert sorted(component[column].tolist()) == sorted(merged[column].tolist())
    assert sorted(zip(component["view_id"], component["embedding_l2_norm"])) != sorted(
        zip(merged["view_id"], merged["embedding_l2_norm"])
    )

    row, restricted = audit_merged_clip_manifest_union(
        merged, [component], expected_components=1
    )
    assert row["clip_key_multisets_equal"] is True
    assert row["row_payload_multisets_equal"] is False
    assert row["n_clip_keys_with_row_payload_mismatch"] == 1
    assert row["component_union_provenance_valid"] is False
    assert {
        "subject_id",
        "study_id",
        "clip_key",
        "dicom_filepath",
        "npz_path",
    }.isdisjoint(row)
    assert "SYN_SUBJECT" not in repr(row)
    assert "clip_a" not in repr(row)
    assert any(
        item["warning_type"] == "CLIP_ROW_PAYLOAD_MISMATCH"
        for item in restricted
    )


def test_merged_clip_manifest_gate_does_not_evaluate_incomplete_lineage() -> None:
    manifest = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
    row, restricted = audit_merged_clip_manifest_union(
        manifest,
        [manifest],
        expected_components=2,
        lineage_complete=False,
    )
    assert row["status"] == "NOT_EVALUATED_INCOMPLETE_COMPONENT_LINEAGE"
    assert row["component_union_provenance_valid"] is None
    assert restricted == []


def test_merged_clip_manifest_gate_cli_keeps_merged_and_components_separate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first_path = root / "component_first.csv"
        second_path = root / "component_second.csv"
        merged_path = root / "merged.csv"
        first = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
        second = _synthetic_clip_manifest("SYN_B", "200", "clip_b")
        merged = pd.concat([first, second], ignore_index=True)
        merged["embedding_idx"] = [0, 1]
        first.to_csv(first_path, index=False)
        second.to_csv(second_path, index=False)
        merged.to_csv(merged_path, index=False)
        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"
        previous_argv = sys.argv
        sys.argv = [
            "audit_lvef_multitask_artifacts.py",
            "--clip-embeddings",
            str(merged_path),
            "--clip-embedding-component-manifest",
            str(first_path),
            "--clip-embedding-component-manifest",
            str(second_path),
            "--clip-component-lineage-complete",
            "--expected-clip-components",
            "2",
            "--output-dir",
            str(output_dir),
            "--restricted-output-dir",
            str(restricted_dir),
        ]
        try:
            assert audit_artifact_main() == 0
        finally:
            sys.argv = previous_argv
        result = pd.read_csv(
            output_dir / "clip_embedding_component_union_provenance.csv"
        ).iloc[0]
        assert bool(result["component_union_provenance_valid"]) is True
        restricted = pd.read_csv(
            restricted_dir / "clip_embedding_component_union_warnings_restricted.csv"
        )
        assert list(restricted.columns) == ["warning_type", "study_id", "clip_key"]
        assert restricted.empty


def test_merged_clip_manifest_gate_cli_blocks_incomplete_declared_component_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        component_path = root / "component.csv"
        merged_path = root / "merged.csv"
        manifest = _synthetic_clip_manifest("SYN_A", "100", "clip_a")
        manifest.to_csv(component_path, index=False)
        manifest.to_csv(merged_path, index=False)
        output_dir = root / "aggregate"
        previous_argv = sys.argv
        sys.argv = [
            "audit_lvef_multitask_artifacts.py",
            "--clip-embeddings",
            str(merged_path),
            "--clip-embedding-component-manifest",
            str(component_path),
            "--clip-component-lineage-complete",
            "--expected-clip-components",
            "2",
            "--output-dir",
            str(output_dir),
        ]
        try:
            assert audit_artifact_main() == 2
        finally:
            sys.argv = previous_argv
        result = pd.read_csv(
            output_dir / "clip_embedding_component_union_provenance.csv"
        ).iloc[0]
        assert result["status"] == "BLOCKED_INVALID_OR_INCOMPLETE_COMPONENT_INPUT"
        assert bool(result["component_manifest_count_matches_expected"]) is False


def test_selected_stage_containment_detects_downstream_set_substitution() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A", "SYN_B", "SYN_C"], "study_id": ["A", "B", "C"]}
    )
    stages = {
        "downloaded_studies": pd.DataFrame({"study_id": ["A", "B"]}),
        "readable_dicoms": pd.DataFrame({"study_id": ["A", "C"]}),
    }
    rows, restricted = selected_stage_containment(selected, stages)
    transition = next(
        row
        for row in rows
        if row["upstream_stage"] == "downloaded_studies"
        and row["downstream_stage"] == "readable_dicoms"
    )
    assert transition["downstream_subset_of_upstream"] is False
    assert transition["n_downstream_not_upstream"] == 1
    assert any(row["study_id"] == "C" for row in restricted)


def test_selected_stage_containment_rejects_labels_or_panel_outside_selected() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A", "SYN_B"], "study_id": ["A", "B"]}
    )
    stages = {
        "lvef_labels": pd.DataFrame(
            {"subject_id": ["SYN_A", "SYN_A"], "study_id": ["A", "LVEF_EXTRA"]}
        ),
        "multitask_panel": pd.DataFrame(
            {"subject_id": ["SYN_B", "SYN_B"], "study_id": ["B", "PANEL_EXTRA"]}
        ),
    }
    rows, restricted = selected_stage_containment(selected, stages)
    by_downstream = {row["downstream_stage"]: row for row in rows}
    assert by_downstream["lvef_labels"]["relation_valid"] is False
    assert by_downstream["multitask_panel"]["relation_valid"] is False
    assert {row["study_id"] for row in restricted} == {
        "A",
        "LVEF_EXTRA",
        "PANEL_EXTRA",
    }


def test_multitask_panel_must_retain_every_selected_base_row() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["SYN_A", "SYN_B"], "study_id": ["A", "B"]}
    )
    stages = {
        "multitask_panel": pd.DataFrame(
            {"subject_id": ["SYN_A"], "study_id": ["A"]}
        )
    }
    rows, restricted = selected_stage_containment(selected, stages)
    panel_row = next(row for row in rows if row["downstream_stage"] == "multitask_panel")
    assert panel_row["expected_relation"] == "EQUAL"
    assert panel_row["n_upstream_not_downstream"] == 1
    assert panel_row["relation_valid"] is False
    assert {
        (row["status"], row["study_id"])
        for row in restricted
        if row["transition"] == "selected_studies_TO_multitask_panel"
    } == {("UPSTREAM_NOT_IN_DOWNSTREAM", "B")}


def test_embedding_containment_checks_outside_selected_clip_study_equality() -> None:
    selected = pd.DataFrame({"subject_id": ["SYN_A"], "study_id": ["A"]})
    stages = {
        "clip_embeddings": pd.DataFrame(
            {"subject_id": ["SYN_A", "SYN_X"], "study_id": ["A", "X"]}
        ),
        "study_embeddings": pd.DataFrame(
            {"subject_id": ["SYN_A", "SYN_Y"], "study_id": ["A", "Y"]}
        ),
    }
    rows, restricted = selected_stage_containment(selected, stages)
    selected_row = next(
        row
        for row in rows
        if row["upstream_stage"] == "clip_embeddings"
        and row["downstream_stage"] == "study_embeddings"
        and row["comparison_scope"] == "SELECTED_INTERSECTION"
    )
    all_row = next(
        row
        for row in rows
        if row["upstream_stage"] == "clip_embeddings"
        and row["downstream_stage"] == "study_embeddings"
        and row["comparison_scope"] == "ALL_ARTIFACT"
    )
    assert selected_row["relation_valid"] is True
    assert all_row["relation_valid"] is False
    assert any(
        row["study_id"] == "Y" and row["comparison_scope"] == "ALL_ARTIFACT"
        for row in restricted
    )
    assert any(
        row["study_id"] == "X"
        and row["comparison_scope"] == "ALL_ARTIFACT"
        and row["status"] == "UPSTREAM_NOT_IN_DOWNSTREAM"
        for row in restricted
    )


def test_incomplete_lineage_never_assigns_a_specific_failure_stage() -> None:
    stage_sets = [("downloaded_studies", {"SYN_OTHER"})]
    assert classify_missing("SYN_TARGET", stage_sets, lineage_complete=False) == "STAGE_LINEAGE_INCOMPLETE"
    assert classify_missing("SYN_TARGET", stage_sets, lineage_complete=True) == "ABSENT_FROM_DOWNLOADED_STUDIES"


def test_missingness_suppresses_low_cells() -> None:
    panel = pd.DataFrame(
        {
            "task__complete": list(range(20)),
            "task__balanced": list(range(10)) + [None] * 10,
        }
    )
    rows, suppressed = missingness_rows(panel, ["task__complete", "task__balanced"], min_count=10)
    by_task = {row["task"]: row for row in rows}
    assert by_task["task__complete"]["cell_suppressed"] is True
    assert by_task["task__complete"]["n_missing"] is None
    assert by_task["task__balanced"]["cell_suppressed"] is False
    assert by_task["task__balanced"]["n_observed"] == 10
    assert suppressed == 1


def test_pairwise_missingness_suppresses_sparse_contingency_cells() -> None:
    panel = pd.DataFrame(
        {
            "task__a": [1.0] * 10 + [None] * 10,
            "task__b": [1.0] * 5 + [None] * 5 + [1.0] * 5 + [None] * 5,
        }
    )
    rows, suppressed = pairwise_rows(panel, ["task__a", "task__b"], min_count=6)
    assert rows == []
    assert suppressed == 3


def test_explicit_dependency_registry_inputs_never_silently_fall_back() -> None:
    with tempfile.TemporaryDirectory() as directory:
        missing = Path(directory) / "missing.csv"
        for operation in (
            lambda: historical_tasks(missing),
            lambda: candidate_predictors(["lvef"], missing, None),
            lambda: candidate_predictors(["lvef"], None, missing),
        ):
            try:
                operation()
            except FileNotFoundError:
                continue
            raise AssertionError("An explicitly supplied missing registry input was ignored")


def test_dependency_registry_rejects_blank_duplicate_or_wrong_schema_authorities() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        blank_tasks = root / "blank_tasks.csv"
        duplicate_tasks = root / "duplicate_tasks.csv"
        wrong_metadata = root / "wrong_metadata.csv"
        pd.DataFrame({"task_col": ["task__a", "___"]}).to_csv(blank_tasks, index=False)
        pd.DataFrame({"task_col": ["task__a", "a"]}).to_csv(duplicate_tasks, index=False)
        pd.DataFrame({"unrelated": ["a"]}).to_csv(wrong_metadata, index=False)
        operations = (
            lambda: historical_tasks(blank_tasks),
            lambda: historical_tasks(duplicate_tasks),
            lambda: candidate_predictors(["lvef"], None, wrong_metadata),
        )
        for operation in operations:
            try:
                operation()
            except ValueError:
                continue
            raise AssertionError("An invalid registry authority schema was accepted")
