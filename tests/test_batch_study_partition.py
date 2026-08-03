import json
from pathlib import Path
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_batch_study_partition import evaluate_partition, main as batch_partition_main


def manifest_payload() -> dict[str, object]:
    return {
        "n_total": 3,
        "n_already_done": 1,
        "n_remaining": 2,
        "n_batches": 2,
        "batches": [
            {"batch_id": 0, "n_studies": 1, "csv": "/old/batch_000_studies.csv"},
            {"batch_id": 1, "n_studies": 1, "csv": "/old/batch_001_studies.csv"},
        ],
    }


def test_exact_selected_minus_prior_batch_partition_passes() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
    )
    prior = pd.DataFrame({"subject_id": ["S1"], "study_id": ["A"]})
    batches = {
        "batch_000_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
        "batch_001_studies.csv": pd.DataFrame({"subject_id": ["S3"], "study_id": ["C"]}),
    }
    summary, restricted = evaluate_partition(
        selected_frame=selected,
        prior_frame=prior,
        batch_frames=batches,
        manifest_payload=manifest_payload(),
        expected_batches=2,
    )
    assert summary["selected_batch_partition_valid"] is True
    assert restricted == []
    assert "S1" not in json.dumps(summary)


def test_duplicate_batch_assignment_and_missing_selected_study_fail() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
    )
    prior = pd.DataFrame({"subject_id": ["S1"], "study_id": ["A"]})
    batches = {
        "batch_000_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
        "batch_001_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
    }
    summary, restricted = evaluate_partition(
        selected_frame=selected,
        prior_frame=prior,
        batch_frames=batches,
        manifest_payload=manifest_payload(),
        expected_batches=2,
    )
    assert summary["selected_batch_partition_valid"] is False
    assert summary["n_duplicate_batch_study_assignments"] == 1
    assert summary["n_expected_studies_missing_from_batches"] == 1
    statuses = {row["status"] for row in restricted}
    assert "DUPLICATE_BATCH_STUDY_ASSIGNMENT" in statuses
    assert "EXPECTED_SELECTED_STUDY_MISSING_FROM_BATCHES" in statuses


def test_duplicate_declared_batch_entry_never_passes_manifest_structure() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
    )
    prior = pd.DataFrame({"subject_id": ["S1"], "study_id": ["A"]})
    batches = {
        "batch_000_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
        "batch_001_studies.csv": pd.DataFrame({"subject_id": ["S3"], "study_id": ["C"]}),
    }
    payload = manifest_payload()
    payload["batches"] = [payload["batches"][0], payload["batches"][0]]  # type: ignore[index]
    summary, _ = evaluate_partition(
        selected_frame=selected,
        prior_frame=prior,
        batch_frames=batches,
        manifest_payload=payload,
        expected_batches=2,
    )
    assert summary["manifest_structure_valid"] is False
    assert summary["selected_batch_partition_valid"] is False


def test_partition_is_not_evaluable_without_prior_stage_manifest() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
    )
    batches = {
        "batch_000_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
        "batch_001_studies.csv": pd.DataFrame({"subject_id": ["S3"], "study_id": ["C"]}),
    }
    summary, _ = evaluate_partition(
        selected_frame=selected,
        prior_frame=None,
        batch_frames=batches,
        manifest_payload=manifest_payload(),
        expected_batches=2,
    )
    assert summary["partition_evaluable"] is False
    assert summary["selected_batch_partition_valid"] is False


def test_prior_stage_extras_are_counted_but_do_not_invalidate_selected_partition() -> None:
    selected = pd.DataFrame(
        {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
    )
    prior = pd.DataFrame(
        {"subject_id": ["S1", "S_EXTRA"], "study_id": ["A", "X"]}
    )
    batches = {
        "batch_000_studies.csv": pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}),
        "batch_001_studies.csv": pd.DataFrame({"subject_id": ["S3"], "study_id": ["C"]}),
    }
    payload = manifest_payload()
    payload["n_already_done"] = 2
    summary, restricted = evaluate_partition(
        selected_frame=selected,
        prior_frame=prior,
        batch_frames=batches,
        manifest_payload=payload,
        expected_batches=2,
    )
    assert summary["n_prior_stage_studies"] == 2
    assert summary["n_prior_stage_studies_in_selected"] == 1
    assert summary["n_prior_stage_studies_outside_selected"] == 1
    assert summary["n_expected_selected_minus_prior_studies"] == 2
    assert summary["top_level_manifest_counts_match_sources"] is True
    assert summary["selected_batch_partition_valid"] is True
    assert restricted == []


def test_explicit_missing_prior_stage_path_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path = root / "selected.csv"
        manifest_path = root / "batch_manifest.json"
        batch_paths = [root / "batch_000_studies.csv", root / "batch_001_studies.csv"]
        pd.DataFrame(
            {"subject_id": ["S1", "S2", "S3"], "study_id": ["A", "B", "C"]}
        ).to_csv(selected_path, index=False)
        manifest_path.write_text(json.dumps(manifest_payload()))
        pd.DataFrame({"subject_id": ["S2"], "study_id": ["B"]}).to_csv(
            batch_paths[0], index=False
        )
        pd.DataFrame({"subject_id": ["S3"], "study_id": ["C"]}).to_csv(
            batch_paths[1], index=False
        )
        previous_argv = sys.argv
        sys.argv = [
            "audit_batch_study_partition.py",
            "--selected-studies",
            str(selected_path),
            "--prior-stage-studies",
            str(root / "missing_prior.csv"),
            "--batch-manifest",
            str(manifest_path),
            "--batch-studies",
            str(batch_paths[0]),
            "--batch-studies",
            str(batch_paths[1]),
            "--expected-batches",
            "2",
            "--output-json",
            str(root / "output.json"),
        ]
        try:
            assert batch_partition_main() == 2
        finally:
            sys.argv = previous_argv
