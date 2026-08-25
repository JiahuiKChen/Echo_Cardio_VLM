from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1 import PROTOCOL_VERSION  # noqa: E402
from jdim_tier1.corrected_completion import (  # noqa: E402
    AGGREGATION_INPUT_KEYS,
    AGGREGATION_OUTPUT_FILES,
    COMPARISON_BASELINE_TABLES,
    COMPARISON_OUTPUT_FILES,
    COMPARISON_REVIEWER_SUPPORT_FILES,
    COMPARISON_REVIEWER_TABLES,
    TARGETS,
    required_corrected_artifacts,
)
from jdim_tier1.safety import (  # noqa: E402
    restricted_file_record,
    safe_file_record,
    sha256_file,
)


def _write_metric_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "target,bootstrap_n,bootstrap_seed\nlvot_vti,2000,20260824\n",
        encoding="utf-8",
    )


def _write_fixed_metric_package(
    metric_root: Path,
    restricted_provenance: Path,
    prediction_paths: dict[str, Path],
) -> None:
    for filename in COMPARISON_REVIEWER_TABLES:
        _write_metric_csv(metric_root / filename)
    restricted_records = [
        restricted_file_record(f"imaging_predictions_{target}", path)
        for target, path in sorted(prediction_paths.items())
    ]
    restricted_payload = {
        "schema_version": "jdim-fixed-prediction-metrics-input-provenance-v1",
        "status": "FIXED_PREDICTION_INPUTS_LOCKED",
        "input_files": restricted_records,
    }
    restricted_provenance.parent.mkdir(parents=True, exist_ok=True)
    restricted_provenance.write_text(json.dumps(restricted_payload), encoding="utf-8")
    safe_payload = {
        "protocol_version": PROTOCOL_VERSION,
        "analysis_type": "fixed_saved_predictions_only",
        "model_refit": False,
        "prediction_regeneration": False,
        "prediction_recalibration": False,
        "threshold_optimization": False,
        "bootstrap_unit": "subject",
        "bootstrap_n": 2000,
        "bootstrap_seed": 20260824,
        "delta_mae_definition": "mae_comparator_minus_mae_imaging_ridge",
        "positive_delta_favors": "imaging_ridge",
        "input_files": [
            safe_file_record(f"imaging_predictions_{target}", path)
            for target, path in sorted(prediction_paths.items())
        ],
        "restricted_input_manifest_sha256": sha256_file(restricted_provenance),
    }
    boundaries = {
        "protocol_version": PROTOCOL_VERSION,
        "source_split": "train",
        "test_labels_used_to_define_boundaries": False,
        "targets": {
            target: {
                "source_split": "train",
                "quantile_method": "numpy_linear",
                "lower_boundary": 1.0,
                "upper_boundary": 2.0,
                "n_training_studies": 10,
                "n_training_subjects": 10,
                "source_prediction_file_sha256": sha256_file(path),
            }
            for target, path in sorted(prediction_paths.items())
        },
    }
    (metric_root / "training_tertile_boundaries.json").write_text(
        json.dumps(boundaries), encoding="utf-8"
    )
    output_paths = {
        "continuous_calibration_metrics": metric_root
        / "continuous_calibration_metrics.csv",
        "training_tertile_test_error": metric_root
        / "training_tertile_test_error.csv",
        "paired_delta_mae": metric_root / "paired_delta_mae.csv",
        "training_tertile_boundaries": metric_root
        / "training_tertile_boundaries.json",
    }
    safe_payload["output_files"] = [
        safe_file_record(role, path) for role, path in sorted(output_paths.items())
    ]
    (metric_root / "fixed_prediction_metrics_provenance.json").write_text(
        json.dumps(safe_payload), encoding="utf-8"
    )


def build_valid_corrected_fixture(
    corrected_root: Path,
    source_root: Path,
    aggregation_parents: dict[str, Path] | None = None,
) -> dict[str, Path]:
    """Create a complete synthetic corrected tree with fully bound provenance."""

    artifacts = required_corrected_artifacts(corrected_root)
    for path in artifacts.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic artifact\n")

    supplied = aggregation_parents or {}
    key_to_supplied_role = {
        "clip_manifest": "historical_clip_embedding_manifest",
        "clip_embeddings": "historical_clip_embedding_array",
        "frozen_study_manifest": "frozen_study_embedding_manifest",
        "frozen_study_embeddings": "frozen_study_embedding_array",
    }
    parents: dict[str, Path] = {}
    for role in sorted(AGGREGATION_INPUT_KEYS):
        supplied_role = key_to_supplied_role.get(role)
        parent = supplied.get(supplied_role) if supplied_role else None
        if parent is None:
            parent = source_root / "aggregation_parents" / f"{role}.bin"
            parent.parent.mkdir(parents=True, exist_ok=True)
            parent.write_bytes(f"synthetic {role}\n".encode("ascii"))
        elif not parent.is_file():
            raise FileNotFoundError(parent)
        parents[role] = parent

    aggregation = {
        "schema_version": "jdim-corrected-aggregation-v1",
        "status": "CORRECTED_AGGREGATION_BUILT",
        "canonical_identity_target_prediction_independent": True,
        "frozen_parent_replay_exact": True,
        "frozen_parent_clip_counts_exact": True,
        "original_inputs_preserved": True,
        "input_files": {
            role: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for role, path in parents.items()
        },
        "output_files": [
            safe_file_record(role, corrected_root / relative)
            for role, relative in sorted(AGGREGATION_OUTPUT_FILES.items())
        ],
    }
    aggregation_path = (
        corrected_root
        / "aggregation/restricted/corrected_aggregation_provenance_restricted.json"
    )
    aggregation_path.write_text(json.dumps(aggregation), encoding="utf-8")

    corrected_prediction_paths = {
        target: corrected_root
        / "restricted"
        / "analyses"
        / target
        / "all_clips"
        / "imaging_baseline_predictions.csv"
        for target in TARGETS
    }
    corrected_metric_root = corrected_root / "aggregate_safe" / "reviewer_metrics"
    corrected_metric_restricted = (
        corrected_root
        / "restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
    )
    _write_fixed_metric_package(
        corrected_metric_root,
        corrected_metric_restricted,
        corrected_prediction_paths,
    )

    split = source_root / "frozen_split_map.csv"
    split.parent.mkdir(parents=True, exist_ok=True)
    split.write_text("subject_id,split\n1,test\n", encoding="utf-8")
    for comparison_name, scope, include_reviewer in (
        ("original_vs_corrected_main", "all_clips", True),
        (
            "original_vs_corrected_hard_extremes",
            "all_clips_exclude_hard_extremes",
            False,
        ),
    ):
        safe_records: list[dict] = []
        restricted_records: list[dict] = []

        def add_record(role: str, path: Path) -> None:
            safe_records.append(safe_file_record(role, path))
            restricted_records.append(restricted_file_record(role, path))

        add_record("frozen_split_map", split)
        original_prediction_paths: dict[str, Path] = {}
        for target in TARGETS:
            analysis = corrected_root / "restricted" / "analyses" / target / scope
            add_record(
                f"corrected_predictions_{target}",
                analysis / "imaging_baseline_predictions.csv",
            )
            add_record(
                f"corrected_summary_{target}",
                analysis / "imaging_baseline_summary.json",
            )
            for label in ("predictions", "summary"):
                role = f"original_{label}_{target}"
                source = source_root / comparison_name / role
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"synthetic {role}\n".encode("ascii"))
                add_record(role, source)
                if label == "predictions":
                    original_prediction_paths[target] = source
            for filename in COMPARISON_BASELINE_TABLES:
                add_record(f"corrected_{target}_{filename}", analysis / filename)
                original_role = f"original_{target}_{filename}"
                source = source_root / comparison_name / original_role
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"synthetic {original_role}\n".encode("ascii"))
                add_record(original_role, source)

        if include_reviewer:
            original_metric_root = source_root / "original_reviewer_metrics"
            original_metric_restricted = (
                source_root / "original_reviewer_metrics_input_provenance_restricted.json"
            )
            _write_fixed_metric_package(
                original_metric_root,
                original_metric_restricted,
                original_prediction_paths,
            )
            for filename in COMPARISON_REVIEWER_TABLES:
                add_record(
                    f"corrected_reviewer_metrics_{filename}",
                    corrected_metric_root / filename,
                )
                add_record(
                    f"original_reviewer_metrics_{filename}",
                    original_metric_root / filename,
                )
            for filename in COMPARISON_REVIEWER_SUPPORT_FILES:
                add_record(
                    f"corrected_reviewer_metrics_{filename}",
                    corrected_metric_root / filename,
                )
                add_record(
                    f"original_reviewer_metrics_{filename}",
                    original_metric_root / filename,
                )
            add_record(
                "corrected_reviewer_metrics_input_provenance",
                corrected_metric_restricted,
            )
            add_record(
                "original_reviewer_metrics_input_provenance",
                original_metric_restricted,
            )

        directory = corrected_root / "aggregate_safe" / comparison_name
        restricted_comparison = (
            corrected_root
            / "restricted"
            / "comparisons"
            / comparison_name
            / "input_provenance_restricted.json"
        )
        restricted_comparison.parent.mkdir(parents=True, exist_ok=True)
        restricted_comparison.write_text(
            json.dumps(
                {
                    "schema_version": "jdim-original-corrected-input-provenance-v1",
                    "status": "ORIGINAL_CORRECTED_INPUTS_LOCKED",
                    "input_files": restricted_records,
                }
            ),
            encoding="utf-8",
        )
        comparison = {
            "schema_version": "jdim-original-corrected-comparison-v1",
            "status": "ORIGINAL_CORRECTED_IDENTITY_VERIFIED",
            "row_identity_verified": True,
            "study_subject_mapping_verified": True,
            "frozen_split_verified": True,
            "observed_report_label_identity_verified": True,
            "null_prediction_identity_verified": True,
            "run_protocol_verified": True,
            "targets": list(TARGETS),
            "input_files": safe_records,
            "restricted_input_manifest_sha256": sha256_file(restricted_comparison),
            "output_files": [
                safe_file_record(role, directory / filename)
                for role, filename in sorted(COMPARISON_OUTPUT_FILES.items())
            ],
        }
        (directory / "original_vs_corrected_comparison_provenance.json").write_text(
            json.dumps(comparison), encoding="utf-8"
        )
    return artifacts
