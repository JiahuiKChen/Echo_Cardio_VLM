from pathlib import Path
import csv
from contextlib import redirect_stdout
import io
import json
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_common_evaluation_denominators import (
    binary_endpoint_reconciliation,
    compute_common_ids,
    labels_equal_on_common,
    main,
    normalize_lvef_label_authority,
    normalize_multitask_panel_authorities,
    subject_split_assignment_reconciliation,
    subject_study_pair_reconciliation,
    target_set_reconciliation,
    target_names,
)
from lvef_multitask_audit_utils import run_guarded


def synthetic_frame(
    studies: list[str], labels: list[float] | None = None, splits: list[str] | None = None
) -> pd.DataFrame:
    frame = pd.DataFrame({"study_id": studies})
    if labels is not None:
        frame["y_true"] = labels
    if splits is not None:
        frame["split"] = splits
    return frame


def run_denominator_main(arguments: list[str], *, guarded: bool = False) -> int:
    previous_argv = sys.argv
    sys.argv = ["build_common_evaluation_denominators.py", *arguments]
    try:
        return run_guarded(main) if guarded else main()
    finally:
        sys.argv = previous_argv


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


def test_nonfinite_continuous_labels_are_blocking() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "y_true": [float("inf")],
                "lvef_binary_reduced": [0],
            }
        ),
        "fusion": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "y_true": [float("inf")],
                "lvef_binary_reduced": [0],
            }
        ),
    }
    equal, mismatches = labels_equal_on_common(
        frames, {"SYN_STUDY_A"}, "y_true", ("study_id",)
    )
    assert equal is False
    assert mismatches > 0

    aggregate, restricted, failed = binary_endpoint_reconciliation(
        frames,
        {"SYN_STUDY_A"},
        continuous_label_column="y_true",
        binary_label_column="lvef_binary_reduced",
        threshold=40.0,
    )
    assert failed is True
    assert aggregate["n_binary_invalid_or_missing_rows"] == 2
    assert {row["status"] for row in restricted} == {"NONFINITE_CONTINUOUS_LABEL"}


def test_nonfinite_authority_and_long_panel_labels_are_rejected() -> None:
    lvef_authority = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A"],
            "split": ["test"],
            "lvef": [float("-inf")],
            "lvef_binary_reduced": [1],
        }
    )
    try:
        normalize_lvef_label_authority(
            lvef_authority,
            continuous_output_column="y_true",
            binary_output_column="binary_label",
        )
    except ValueError as error:
        assert "Non-finite values" in str(error)
    else:
        raise AssertionError("Non-finite LVEF authority label was accepted")

    wide = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A"],
            "split": ["test"],
            "task__alpha": [1.0],
        }
    )
    long = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A"],
            "task_col": ["task__alpha"],
            "task_value": [float("inf")],
        }
    )
    try:
        normalize_multitask_panel_authorities(
            wide,
            long,
            label_output_column="y_true",
            task_prefix="task__",
        )
    except ValueError as error:
        assert "Non-finite values" in str(error)
    else:
        raise AssertionError("Non-finite long-panel label was accepted")


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


def test_split_specific_identity_detects_a_test_set_swap() -> None:
    frames = {
        "vision": synthetic_frame(
            ["SYN_STUDY_A", "SYN_STUDY_B"], splits=["train", "test"]
        ),
        "fusion": synthetic_frame(
            ["SYN_STUDY_A", "SYN_STUDY_B"], splits=["test", "train"]
        ),
    }
    all_sets, all_common = compute_common_ids(frames)
    test_sets, test_common = compute_common_ids(frames, split="test")
    assert all_sets["vision"] == all_sets["fusion"] == all_common
    assert test_common == set()
    assert test_sets["vision"] != test_sets["fusion"]


def test_target_union_exposes_a_task_missing_from_one_modality() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "task_col": ["task__alpha", "task__beta"],
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
    assert target_names(frames, [], "task__") == ["task__alpha", "task__beta"]
    sets, common = compute_common_ids(frames, target="task__beta")
    assert sets["fusion"] == set()
    assert common == set()


def test_target_definition_reconciliation_requires_exact_panel_and_modality_sets() -> None:
    expected = {"task__alpha", "task__beta"}
    rows = target_set_reconciliation(
        expected,
        {
            "panel_wide": {"task__alpha", "task__beta"},
            "modality_fusion": {"task__alpha"},
        },
    )
    by_source = {row["source"]: row for row in rows}
    assert by_source["panel_wide"]["target_sets_identical"] is True
    assert by_source["modality_fusion"]["target_sets_identical"] is False
    assert by_source["modality_fusion"]["n_expected_missing"] == 1


def test_subject_study_pairs_detect_swapped_ownership_when_independent_sets_match() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "split": ["test", "test"],
            }
        ),
        "fusion": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_B", "SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "split": ["test", "test"],
            }
        ),
    }
    study_sets, _ = compute_common_ids(frames, id_candidates=("study_id",), split="test")
    subject_sets, _ = compute_common_ids(frames, id_candidates=("subject_id",), split="test")
    assert study_sets["vision"] == study_sets["fusion"]
    assert subject_sets["vision"] == subject_sets["fusion"]

    aggregate, restricted, failed = subject_study_pair_reconciliation(
        frames, split="test"
    )
    assert failed is True
    assert aggregate["sets_identical"] is False
    assert aggregate["subject_assignment_identical_on_common_studies"] is False
    assert aggregate["n_common_studies_with_subject_assignment_mismatch"] == 2
    assert any(
        row["status"] == "SUBJECT_ASSIGNMENT_MISMATCH_ON_COMMON_STUDY"
        for row in restricted
    )
    assert "subject_id" not in aggregate
    assert "study_id" not in aggregate


def test_duplicate_identical_prediction_key_is_blocking() -> None:
    duplicate = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_A"],
            "task_col": ["task__alpha", "task__alpha"],
            "split": ["test", "test"],
            "y_true": [1.0, 1.0],
            "y_pred": [1.1, 1.1],
        }
    )
    frames = {
        "vision": duplicate,
        "fusion": duplicate.iloc[[0]].copy(),
    }
    aggregate, restricted, failed = subject_study_pair_reconciliation(
        frames, target="task__alpha", split="test"
    )
    assert failed is True
    assert aggregate["sets_identical"] is True
    assert aggregate["n_vision_duplicate_prediction_keys"] == 1
    assert aggregate["n_vision_duplicate_prediction_key_rows"] == 2
    assert aggregate["subject_study_mapping_valid"] is False
    assert sum(row["status"] == "DUPLICATE_PREDICTION_KEY" for row in restricted) == 2


def test_subject_in_multiple_splits_is_blocking_even_when_modalities_match() -> None:
    invalid = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
            "split": ["train", "test"],
        }
    )
    frames = {"vision": invalid, "fusion": invalid.copy()}
    aggregate, restricted, failed = subject_split_assignment_reconciliation(frames)
    assert failed is True
    assert aggregate["sets_identical"] is True
    assert aggregate["subject_split_mapping_valid"] is False
    assert aggregate["n_vision_subjects_with_multiple_splits"] == 1
    assert any(row["status"] == "SUBJECT_ASSIGNED_TO_MULTIPLE_SPLITS" for row in restricted)


def test_binary_endpoint_audit_uses_only_partial_common_denominator() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_D", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_D", "SYN_STUDY_B"],
                "lvef": [35.0, 40.0, 55.0],
                "lvef_binary_reduced": [1, 0, 0],
            }
        ),
        "fusion": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_D", "SYN_SUBJECT_C"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_D", "SYN_STUDY_C"],
                "lvef": [35.0, 40.0, 60.0],
                "lvef_binary_reduced": [1, 0, 0],
            }
        ),
    }
    aggregate, restricted, failed = binary_endpoint_reconciliation(
        frames,
        {"SYN_STUDY_A", "SYN_STUDY_D"},
        continuous_label_column="lvef",
        binary_label_column="lvef_binary_reduced",
        threshold=40.0,
    )
    assert failed is False
    assert restricted == []
    assert aggregate["n_common"] == 2
    assert aggregate["binary_labels_identical_on_common"] is True
    assert aggregate["binary_labels_consistent_with_continuous_threshold"] is True


def test_binary_endpoint_audit_detects_cross_modality_and_threshold_mismatch() -> None:
    frames = {
        "vision": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "lvef": [35.0],
                "lvef_binary_reduced": [1],
            }
        ),
        "fusion": pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "lvef": [35.0],
                "lvef_binary_reduced": [0],
            }
        ),
    }
    aggregate, restricted, failed = binary_endpoint_reconciliation(
        frames,
        {"SYN_STUDY_A"},
        continuous_label_column="lvef",
        binary_label_column="lvef_binary_reduced",
        threshold=40.0,
    )
    assert failed is True
    assert aggregate["n_cross_modality_binary_label_mismatches"] == 1
    assert aggregate["n_binary_threshold_inconsistencies"] == 1
    statuses = {row["status"] for row in restricted}
    assert "BINARY_LABEL_MISMATCH_ACROSS_MODALITIES" in statuses
    assert "BINARY_LABEL_THRESHOLD_INCONSISTENT" in statuses


def test_expected_target_count_rejects_duplicate_and_blank_definition_rows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths = []
        for name in ("vision", "fusion"):
            path = root / f"{name}.csv"
            pd.DataFrame(
                {
                    "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                    "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                    "split": ["test", "test"],
                    "task_col": ["task__alpha", "task__beta"],
                    "y_true": [1.0, 2.0],
                }
            ).to_csv(path, index=False)
            modality_paths.append((name, path))
        definition_path = root / "definition.csv"
        pd.DataFrame({"task_col": ["task__alpha", "task__alpha", None]}).to_csv(
            definition_path, index=False, quoting=csv.QUOTE_ALL
        )
        output_dir = root / "aggregate"
        previous_argv = sys.argv
        sys.argv = [
            "build_common_evaluation_denominators.py",
            "--modality",
            f"{modality_paths[0][0]}={modality_paths[0][1]}",
            "--modality",
            f"{modality_paths[1][0]}={modality_paths[1][1]}",
            "--target-definition-csv",
            str(definition_path),
            "--expected-target-count",
            "3",
            "--output-dir",
            str(output_dir),
        ]
        try:
            return_code = main()
        finally:
            sys.argv = previous_argv
        summary = json.loads((output_dir / "common_denominator_audit.summary.json").read_text())
        integrity = pd.read_csv(output_dir / "target_definition_integrity.csv").iloc[0]
        assert return_code == 1
        assert summary["target_definition_valid"] is False
        assert summary["n_definition_rows"] == 3
        assert summary["n_definition_blank_rows"] == 1
        assert summary["n_definition_duplicate_rows"] == 1
        assert bool(integrity["raw_row_count_matches_expected"]) is True
        assert bool(integrity["unique_target_count_matches_expected"]) is False


def test_pair_mismatch_cli_writes_only_aggregate_flags_and_restricted_ids() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vision_path = root / "vision.csv"
        fusion_path = root / "fusion.csv"
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "split": ["train", "train"],
            }
        ).to_csv(vision_path, index=False)
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_B", "SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "split": ["train", "train"],
            }
        ).to_csv(fusion_path, index=False)
        aggregate_dir = root / "aggregate"
        restricted_dir = root / "restricted"
        previous_argv = sys.argv
        sys.argv = [
            "build_common_evaluation_denominators.py",
            "--modality",
            f"vision={vision_path}",
            "--modality",
            f"fusion={fusion_path}",
            "--output-dir",
            str(aggregate_dir),
            "--restricted-output-dir",
            str(restricted_dir),
        ]
        try:
            return_code = main()
        finally:
            sys.argv = previous_argv

        aggregate = pd.read_csv(aggregate_dir / "common_denominator_audit.csv")
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        summary = json.loads(
            (aggregate_dir / "common_denominator_audit.summary.json").read_text()
        )
        assert return_code == 1
        assert summary["n_subject_study_pair_failures"] == 2
        assert "subject_id" not in aggregate.columns
        assert "study_id" not in aggregate.columns
        pair_rows = aggregate[aggregate["identifier_level"] == "subject_study_pair"]
        assert pair_rows["sets_identical"].eq(False).all()
        assert {"subject_id", "study_id"}.issubset(restricted.columns)
        assert "SUBJECT_ASSIGNMENT_MISMATCH_ON_COMMON_STUDY" in set(restricted["status"])


def test_continuous_label_mismatch_cli_writes_restricted_diagnostics_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths: list[tuple[str, Path]] = []
        for name, label in (("vision", 50.0), ("structured", 51.0)):
            path = root / f"{name}.csv"
            pd.DataFrame(
                {
                    "subject_id": ["SYN_SUBJECT_A"],
                    "study_id": ["SYN_STUDY_A"],
                    "split": ["test"],
                    "lvef": [label],
                }
            ).to_csv(path, index=False)
            modality_paths.append((name, path))
        aggregate_dir = root / "aggregate"
        restricted_dir = root / "restricted"
        previous_argv = sys.argv
        sys.argv = [
            "build_common_evaluation_denominators.py",
            "--modality",
            f"{modality_paths[0][0]}={modality_paths[0][1]}",
            "--modality",
            f"{modality_paths[1][0]}={modality_paths[1][1]}",
            "--label-column",
            "lvef",
            "--output-dir",
            str(aggregate_dir),
            "--restricted-output-dir",
            str(restricted_dir),
        ]
        try:
            assert main() == 1
        finally:
            sys.argv = previous_argv
        aggregate = pd.read_csv(aggregate_dir / "common_denominator_audit.csv")
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        assert "study_id" not in aggregate.columns
        assert "observed_continuous" not in aggregate.columns
        assert "CONTINUOUS_LABEL_MISMATCH_ACROSS_MODALITIES" in set(
            restricted["status"]
        )
        assert "SYN_STUDY_A" in set(restricted["study_id"])


def test_lvef_label_authority_is_compared_and_exact_duplicates_collapse() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prediction = pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
                "split": ["train", "test"],
                "y_true": [35.0, 55.0],
                "lvef_binary_reduced": [1, 0],
            }
        )
        modality_paths: list[Path] = []
        for name in ("vision", "fusion"):
            path = root / f"{name}.csv"
            prediction.to_csv(path, index=False)
            modality_paths.append(path)
        authority_path = root / "lvef_authority.csv"
        pd.concat(
            [
                prediction.rename(columns={"y_true": "lvef"}),
                prediction.rename(columns={"y_true": "lvef"}),
            ],
            ignore_index=True,
        ).to_csv(authority_path, index=False)
        output_dir = root / "aggregate"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--label-authority-csv",
                str(authority_path),
                "--label-column",
                "y_true",
                "--binary-label-column",
                "lvef_binary_reduced",
                "--binary-threshold",
                "40",
                "--output-dir",
                str(output_dir),
            ]
        )

        summary = json.loads(
            (output_dir / "common_denominator_audit.summary.json").read_text()
        )
        aggregate = pd.read_csv(output_dir / "common_denominator_audit.csv")
        all_studies = aggregate[
            (aggregate["target"] == "__all__")
            & (aggregate["split"] == "all")
            & (aggregate["identifier_level"] == "study")
        ].iloc[0]
        assert return_code == 0
        assert summary["modalities"] == ["fusion", "vision"]
        assert summary["authority_sources"] == ["label_authority"]
        assert set(summary["comparison_sources"]) == {
            "fusion",
            "vision",
            "label_authority",
        }
        assert summary["continuous_label_comparison_rtol"] == 1e-6
        assert summary["continuous_label_comparison_atol"] == 1e-6
        assert all_studies["n_label_authority"] == 2
        assert bool(all_studies["labels_identical_on_common"]) is True


def test_lvef_label_authority_conflicting_duplicate_is_blocking_and_restricted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prediction = pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "split": ["test"],
                "y_true": [55.0],
                "lvef_binary_reduced": [0],
            }
        )
        modality_paths: list[Path] = []
        for name in ("vision", "fusion"):
            path = root / f"{name}.csv"
            prediction.to_csv(path, index=False)
            modality_paths.append(path)
        authority_path = root / "lvef_authority.csv"
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_A"],
                "split": ["test", "test"],
                "lvef": [55.0, 56.0],
                "lvef_binary_reduced": [0, 0],
            }
        ).to_csv(authority_path, index=False)
        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--label-authority-csv",
                str(authority_path),
                "--label-column",
                "y_true",
                "--binary-label-column",
                "lvef_binary_reduced",
                "--binary-threshold",
                "40",
                "--output-dir",
                str(output_dir),
                "--restricted-output-dir",
                str(restricted_dir),
            ]
        )

        aggregate = pd.read_csv(output_dir / "common_denominator_audit.csv")
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        pair_rows = aggregate[
            aggregate["identifier_level"] == "subject_study_pair"
        ]
        assert return_code == 1
        assert pair_rows["n_label_authority_duplicate_prediction_keys"].gt(0).any()
        assert "DUPLICATE_PREDICTION_KEY" in set(restricted["status"])
        assert "CONTINUOUS_LABEL_CONFLICT_WITHIN_MODALITY" in set(
            restricted["status"]
        )
        assert "subject_id" not in aggregate.columns
        assert "study_id" not in aggregate.columns


def test_lvef_stale_prediction_label_is_blocked_by_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prediction = pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_A"],
                "study_id": ["SYN_STUDY_A"],
                "split": ["test"],
                "y_true": [55.0],
                "lvef_binary_reduced": [0],
            }
        )
        modality_paths: list[Path] = []
        for name in ("vision", "fusion"):
            path = root / f"{name}.csv"
            prediction.to_csv(path, index=False)
            modality_paths.append(path)
        authority_path = root / "lvef_authority.csv"
        prediction.assign(y_true=54.0).rename(columns={"y_true": "lvef"}).to_csv(
            authority_path, index=False
        )
        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--label-authority-csv",
                str(authority_path),
                "--label-column",
                "y_true",
                "--binary-label-column",
                "lvef_binary_reduced",
                "--binary-threshold",
                "40",
                "--output-dir",
                str(output_dir),
                "--restricted-output-dir",
                str(restricted_dir),
            ]
        )
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        assert return_code == 1
        assert "CONTINUOUS_LABEL_MISMATCH_ACROSS_MODALITIES" in set(
            restricted["status"]
        )


def _write_multitask_authority_inputs(
    root: Path, *, material_long_mismatch: bool = False
) -> tuple[list[Path], Path, Path]:
    wide = pd.DataFrame(
        {
            "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
            "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
            "split": ["train", "test"],
            "task__alpha": [0.123456789, 2.0],
            "task__beta": [10.0, 20.0],
        }
    )
    long = wide.melt(
        id_vars=["subject_id", "study_id"],
        value_vars=["task__alpha", "task__beta"],
        var_name="task_col",
        value_name="task_value",
    )
    if material_long_mismatch:
        long.loc[
            (long["study_id"] == "SYN_STUDY_A")
            & (long["task_col"] == "task__alpha"),
            "task_value",
        ] = 0.25
    wide_path = root / "panel_wide.csv"
    long_path = root / "panel_long.csv"
    wide.to_csv(wide_path, index=False)
    long.to_csv(long_path, index=False)

    prediction_long = wide.melt(
        id_vars=["subject_id", "study_id", "split"],
        value_vars=["task__alpha", "task__beta"],
        var_name="task_col",
        value_name="y_true",
    )
    # Match the runner's float32 label roundtrip without making it a corruption.
    prediction_long["y_true"] = prediction_long["y_true"].astype("float32")
    modality_paths: list[Path] = []
    for name in ("vision", "fusion"):
        path = root / f"{name}.csv"
        prediction_long.to_csv(path, index=False)
        modality_paths.append(path)
    return modality_paths, wide_path, long_path


def test_multitask_wide_and_long_are_authorities_with_float32_tolerance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths, wide_path, long_path = _write_multitask_authority_inputs(root)
        output_dir = root / "aggregate"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--target-panel-wide-csv",
                str(wide_path),
                "--target-panel-long-csv",
                str(long_path),
                "--label-column",
                "y_true",
                "--output-dir",
                str(output_dir),
            ]
        )

        summary = json.loads(
            (output_dir / "common_denominator_audit.summary.json").read_text()
        )
        aggregate = pd.read_csv(output_dir / "common_denominator_audit.csv")
        study_rows = aggregate[aggregate["identifier_level"] == "study"]
        assert return_code == 0
        assert set(summary["authority_sources"]) == {
            "panel_wide_authority",
            "panel_long_authority",
        }
        assert summary["n_targets"] == 2
        assert study_rows["labels_identical_on_common"].eq(True).all()
        assert study_rows["n_panel_wide_authority"].eq(study_rows["n_common"]).all()
        assert study_rows["n_panel_long_authority"].eq(study_rows["n_common"]).all()


def test_multitask_long_authority_material_label_mismatch_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths, wide_path, long_path = _write_multitask_authority_inputs(
            root, material_long_mismatch=True
        )
        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--target-panel-wide-csv",
                str(wide_path),
                "--target-panel-long-csv",
                str(long_path),
                "--label-column",
                "y_true",
                "--output-dir",
                str(output_dir),
                "--restricted-output-dir",
                str(restricted_dir),
            ]
        )

        aggregate = pd.read_csv(output_dir / "common_denominator_audit.csv")
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        assert return_code == 1
        assert "CONTINUOUS_LABEL_MISMATCH_ACROSS_MODALITIES" in set(
            restricted["status"]
        )
        assert "subject_id" not in aggregate.columns
        assert "study_id" not in aggregate.columns


def test_multitask_long_authority_missing_required_schema_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths, wide_path, long_path = _write_multitask_authority_inputs(root)
        invalid_long = pd.read_csv(long_path).drop(columns=["task_col"])
        invalid_long.to_csv(long_path, index=False)

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--target-panel-wide-csv",
                str(wide_path),
                "--target-panel-long-csv",
                str(long_path),
                "--label-column",
                "y_true",
                "--output-dir",
                str(root / "aggregate"),
            ],
            guarded=True,
        )
        assert return_code == 2

        # Observed long rows are denominator authorities; a missing task value
        # cannot be silently treated like expected wide-panel missingness.
        _, _, long_path = _write_multitask_authority_inputs(root)
        missing_value_long = pd.read_csv(long_path)
        missing_value_long.loc[0, "task_value"] = None
        missing_value_long.to_csv(long_path, index=False)
        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--target-panel-wide-csv",
                str(wide_path),
                "--target-panel-long-csv",
                str(long_path),
                "--label-column",
                "y_true",
                "--output-dir",
                str(root / "aggregate_missing_value"),
            ],
            guarded=True,
        )
        assert return_code == 2


def test_multitask_exact_duplicate_authority_row_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        modality_paths, wide_path, long_path = _write_multitask_authority_inputs(root)
        long = pd.read_csv(long_path)
        pd.concat([long, long.iloc[[0]]], ignore_index=True).to_csv(long_path, index=False)
        output_dir = root / "aggregate"
        restricted_dir = root / "restricted"

        return_code = run_denominator_main(
            [
                "--modality",
                f"vision={modality_paths[0]}",
                "--modality",
                f"fusion={modality_paths[1]}",
                "--target-panel-wide-csv",
                str(wide_path),
                "--target-panel-long-csv",
                str(long_path),
                "--label-column",
                "y_true",
                "--output-dir",
                str(output_dir),
                "--restricted-output-dir",
                str(restricted_dir),
            ]
        )
        restricted = pd.read_csv(
            restricted_dir / "common_denominator_discrepancies_restricted.csv"
        )
        assert return_code == 1
        assert any(
            (restricted["modality"] == "panel_long_authority")
            & (restricted["status"] == "DUPLICATE_PREDICTION_KEY")
        )


def test_missing_modality_error_reports_name_not_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        existing = root / "fusion.csv"
        pd.DataFrame({"placeholder": [1]}).to_csv(existing, index=False)
        missing = root / "restricted_patient_area" / "vision.csv"
        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run_denominator_main(
                [
                    "--modality",
                    f"vision={missing}",
                    "--modality",
                    f"fusion={existing}",
                    "--output-dir",
                    str(root / "aggregate"),
                ]
            )
        payload = json.loads(output.getvalue())
        assert return_code == 2
        assert payload["n_missing_inputs"] == 1
        assert payload["missing_input_names"] == ["vision"]
        assert str(missing) not in output.getvalue()
