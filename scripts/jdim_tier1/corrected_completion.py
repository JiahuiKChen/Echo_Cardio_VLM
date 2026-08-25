"""Validate and certify a complete duplicate-corrected fixed-protocol analysis."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import PROTOCOL_VERSION
from .safety import (
    Tier1BlockedError,
    require_restricted_destination,
    safe_file_record,
    sha256_file,
    write_json,
)


BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION = "BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION"
TARGETS = ("lvot_vti", "tapse")
SCOPES = ("all_clips", "all_clips_exclude_hard_extremes")
BASELINE_FILES = (
    "imaging_baseline_metrics.csv",
    "imaging_baseline_binary_metrics.csv",
    "imaging_baseline_ridge_alpha_selection.csv",
    "imaging_baseline_bootstrap_ci.csv",
    "test_correlation_metrics.csv",
    "imaging_baseline_predictions.csv",
    "imaging_baseline_summary.json",
    "imaging_baseline_warnings.json",
)
REVIEWER_METRIC_FILES = (
    "continuous_calibration_metrics.csv",
    "training_tertile_test_error.csv",
    "paired_delta_mae.csv",
    "training_tertile_boundaries.json",
    "fixed_prediction_metrics_provenance.json",
)
COMPARISON_FILES = (
    "original_vs_corrected_prediction_changes.csv",
    "original_vs_corrected_prediction_metrics.csv",
    "original_vs_corrected_aggregate_metrics.csv",
    "original_vs_corrected_comparison_provenance.json",
)
AGGREGATION_FILES = (
    "aggregation/restricted/deduplicated_clip_embeddings.npz",
    "aggregation/restricted/deduplicated_clip_manifest.csv",
    "aggregation/restricted/corrected_study_embeddings.npz",
    "aggregation/restricted/corrected_study_embedding_manifest.csv",
    "aggregation/restricted/study_embedding_changes_restricted.csv",
    "aggregation/restricted/corrected_aggregation_provenance_restricted.json",
    "aggregation/aggregate_safe/embedding_change_counts.csv",
    "aggregation/aggregate_safe/embedding_change_distribution.csv",
)
AGGREGATION_OUTPUT_FILES = {
    "corrected_clip_embedding_array": "aggregation/restricted/deduplicated_clip_embeddings.npz",
    "corrected_clip_embedding_manifest": "aggregation/restricted/deduplicated_clip_manifest.csv",
    "corrected_study_embedding_array": "aggregation/restricted/corrected_study_embeddings.npz",
    "corrected_study_embedding_manifest": "aggregation/restricted/corrected_study_embedding_manifest.csv",
    "study_embedding_changes_restricted": "aggregation/restricted/study_embedding_changes_restricted.csv",
    "embedding_change_counts": "aggregation/aggregate_safe/embedding_change_counts.csv",
    "embedding_change_distribution": "aggregation/aggregate_safe/embedding_change_distribution.csv",
}
COMPARISON_OUTPUT_FILES = {
    "comparison_prediction_changes": "original_vs_corrected_prediction_changes.csv",
    "comparison_prediction_metrics": "original_vs_corrected_prediction_metrics.csv",
    "comparison_aggregate_metrics": "original_vs_corrected_aggregate_metrics.csv",
}
COMPARISON_BASELINE_TABLES = (
    "imaging_baseline_metrics.csv",
    "imaging_baseline_binary_metrics.csv",
    "imaging_baseline_ridge_alpha_selection.csv",
    "imaging_baseline_bootstrap_ci.csv",
    "test_correlation_metrics.csv",
)
COMPARISON_REVIEWER_TABLES = (
    "continuous_calibration_metrics.csv",
    "training_tertile_test_error.csv",
    "paired_delta_mae.csv",
)
COMPARISON_REVIEWER_SUPPORT_FILES = (
    "training_tertile_boundaries.json",
    "fixed_prediction_metrics_provenance.json",
)
FIXED_METRIC_PROTOCOL_FIELDS = (
    "protocol_version",
    "analysis_type",
    "model_refit",
    "prediction_regeneration",
    "prediction_recalibration",
    "threshold_optimization",
    "bootstrap_unit",
    "bootstrap_n",
    "bootstrap_seed",
    "delta_mae_definition",
    "positive_delta_favors",
)
FIXED_METRIC_OUTPUT_FILES = {
    "continuous_calibration_metrics": "continuous_calibration_metrics.csv",
    "training_tertile_test_error": "training_tertile_test_error.csv",
    "paired_delta_mae": "paired_delta_mae.csv",
    "training_tertile_boundaries": "training_tertile_boundaries.json",
}
AGGREGATION_INPUT_KEYS = {
    "forensic_evidence",
    "clip_manifest",
    "clip_embeddings",
    "frozen_study_manifest",
    "frozen_study_embeddings",
}


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"invalid {label}: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"invalid {label}: expected a JSON object",
        )
    return payload


def _is_sha256(value: Any) -> bool:
    text = str(value)
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _records_by_role(
    records: Any,
    label: str,
    expected_roles: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} is not a file-record list",
        )
    by_role: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not str(record.get("logical_role", "")):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} contains an invalid file record",
            )
        role = str(record["logical_role"])
        if role in by_role:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} contains duplicate role {role!r}",
            )
        if not _is_sha256(record.get("sha256")):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} has an invalid SHA-256 for role {role!r}",
            )
        size = record.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} has an invalid size for role {role!r}",
            )
        row_count = record.get("row_count")
        if row_count is not None and (
            not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} has an invalid row count for role {role!r}",
            )
        by_role[role] = record
    if expected_roles is not None and set(by_role) != set(expected_roles):
        missing = sorted(set(expected_roles) - set(by_role))
        unexpected = sorted(set(by_role) - set(expected_roles))
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} roles differ from the fixed protocol; missing={missing}, "
            f"unexpected={unexpected}",
        )
    return by_role


def _require_record_matches(
    record: Mapping[str, Any],
    role: str,
    path: Path,
    label: str,
) -> None:
    if not path.is_file():
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} source is missing for role {role!r}",
        )
    observed = safe_file_record(role, path)
    if (
        record.get("sha256") != observed["sha256"]
        or record.get("size_bytes") != observed["size_bytes"]
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} hash/size mismatch for role {role!r}",
        )


def _require_restricted_record_path(
    record: Mapping[str, Any],
    role: str,
    label: str,
) -> Path:
    raw_path = str(record.get("path", ""))
    path = Path(raw_path).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} has an invalid source path for role {role!r}",
        )
    resolved = path.resolve()
    _require_record_matches(record, role, resolved, label)
    return resolved


def _records_match_after_path_redaction(
    safe_record: Mapping[str, Any],
    restricted_record: Mapping[str, Any],
) -> bool:
    keys = {"logical_role", "sha256", "size_bytes", "row_count"}
    return all(safe_record.get(key) == restricted_record.get(key) for key in keys)


def _validate_aggregation_provenance(root: Path) -> dict[str, Any]:
    path = root / "aggregation/restricted/corrected_aggregation_provenance_restricted.json"
    payload = _load_json_object(path, "corrected aggregation provenance")
    required_true = (
        "canonical_identity_target_prediction_independent",
        "frozen_parent_replay_exact",
        "frozen_parent_clip_counts_exact",
        "original_inputs_preserved",
    )
    if (
        payload.get("schema_version") != "jdim-corrected-aggregation-v1"
        or payload.get("status") != "CORRECTED_AGGREGATION_BUILT"
        or any(payload.get(field) is not True for field in required_true)
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected aggregation provenance does not certify the fixed aggregation protocol",
        )

    inputs = payload.get("input_files")
    if not isinstance(inputs, Mapping) or set(inputs) != AGGREGATION_INPUT_KEYS:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected aggregation provenance has an incomplete parent-input matrix",
        )
    parent_paths: list[Path] = []
    for role in sorted(AGGREGATION_INPUT_KEYS):
        record = inputs[role]
        if not isinstance(record, Mapping):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected aggregation parent record is invalid for {role!r}",
            )
        raw_path = str(record.get("path", ""))
        parent = Path(raw_path).expanduser()
        if not parent.is_absolute() or not parent.is_file():
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected aggregation parent path is invalid for {role!r}",
            )
        parent = parent.resolve()
        if parent == root or root in parent.parents:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected aggregation parent aliases the corrected child tree for {role!r}",
            )
        if not _is_sha256(record.get("sha256")) or record.get("sha256") != sha256_file(parent):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected aggregation parent hash mismatch for {role!r}",
            )
        parent_paths.append(parent)
    if len(set(parent_paths)) != len(parent_paths):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected aggregation parent roles do not identify distinct files",
        )

    outputs = _records_by_role(
        payload.get("output_files"),
        "corrected aggregation output matrix",
        tuple(AGGREGATION_OUTPUT_FILES),
    )
    for role, relative in AGGREGATION_OUTPUT_FILES.items():
        _require_record_matches(
            outputs[role], role, root / relative, "corrected aggregation output provenance"
        )
    return payload


def _comparison_expected_inputs(
    root: Path,
    scope: str,
    include_reviewer_metrics: bool,
) -> tuple[set[str], dict[str, Path]]:
    roles = {"frozen_split_map"}
    corrected_paths: dict[str, Path] = {}
    for target in TARGETS:
        analysis = root / "restricted" / "analyses" / target / scope
        role_paths = {
            f"corrected_predictions_{target}": analysis / "imaging_baseline_predictions.csv",
            f"corrected_summary_{target}": analysis / "imaging_baseline_summary.json",
        }
        corrected_paths.update(role_paths)
        roles.update(role_paths)
        roles.update(
            {
                f"original_predictions_{target}",
                f"original_summary_{target}",
            }
        )
        for filename in COMPARISON_BASELINE_TABLES:
            roles.add(f"original_{target}_{filename}")
            corrected_role = f"corrected_{target}_{filename}"
            roles.add(corrected_role)
            corrected_paths[corrected_role] = analysis / filename
    if include_reviewer_metrics:
        reviewer_root = root / "aggregate_safe" / "reviewer_metrics"
        for filename in COMPARISON_REVIEWER_TABLES:
            roles.add(f"original_reviewer_metrics_{filename}")
            corrected_role = f"corrected_reviewer_metrics_{filename}"
            roles.add(corrected_role)
            corrected_paths[corrected_role] = reviewer_root / filename
        for filename in COMPARISON_REVIEWER_SUPPORT_FILES:
            roles.add(f"original_reviewer_metrics_{filename}")
            corrected_role = f"corrected_reviewer_metrics_{filename}"
            roles.add(corrected_role)
            corrected_paths[corrected_role] = reviewer_root / filename
        roles.update(
            {
                "original_reviewer_metrics_input_provenance",
                "corrected_reviewer_metrics_input_provenance",
            }
        )
        corrected_paths["corrected_reviewer_metrics_input_provenance"] = (
            root
            / "restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
        )
    return roles, corrected_paths


def _validate_comparison_provenance(
    root: Path,
    comparison_name: str,
    scope: str,
    include_reviewer_metrics: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    directory = root / "aggregate_safe" / comparison_name
    path = directory / "original_vs_corrected_comparison_provenance.json"
    payload = _load_json_object(path, f"{comparison_name} provenance")
    required_true = (
        "row_identity_verified",
        "study_subject_mapping_verified",
        "frozen_split_verified",
        "observed_report_label_identity_verified",
        "null_prediction_identity_verified",
        "run_protocol_verified",
    )
    if (
        payload.get("schema_version") != "jdim-original-corrected-comparison-v1"
        or payload.get("status") != "ORIGINAL_CORRECTED_IDENTITY_VERIFIED"
        or any(payload.get(field) is not True for field in required_true)
        or payload.get("targets") != list(TARGETS)
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{comparison_name} provenance does not certify the fixed comparison protocol",
        )

    expected_roles, corrected_paths = _comparison_expected_inputs(
        root, scope, include_reviewer_metrics
    )
    safe_inputs = _records_by_role(
        payload.get("input_files"), f"{comparison_name} input matrix", tuple(expected_roles)
    )
    restricted_path = (
        root
        / "restricted"
        / "comparisons"
        / comparison_name
        / "input_provenance_restricted.json"
    )
    if payload.get("restricted_input_manifest_sha256") != sha256_file(restricted_path):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{comparison_name} safe provenance is not bound to its restricted input manifest",
        )
    restricted_payload = _load_json_object(
        restricted_path, f"{comparison_name} restricted input provenance"
    )
    if (
        restricted_payload.get("schema_version")
        != "jdim-original-corrected-input-provenance-v1"
        or restricted_payload.get("status") != "ORIGINAL_CORRECTED_INPUTS_LOCKED"
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{comparison_name} restricted input provenance is invalid",
        )
    restricted_inputs = _records_by_role(
        restricted_payload.get("input_files"),
        f"{comparison_name} restricted input matrix",
        tuple(expected_roles),
    )
    input_paths: dict[str, Path] = {}
    for role in sorted(expected_roles):
        if not _records_match_after_path_redaction(
            safe_inputs[role], restricted_inputs[role]
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{comparison_name} safe/restricted input records differ for role {role!r}",
            )
        input_paths[role] = _require_restricted_record_path(
            restricted_inputs[role], role, f"{comparison_name} restricted input provenance"
        )
    for role, source in corrected_paths.items():
        if input_paths[role] != source.resolve():
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{comparison_name} corrected source path is mislabeled for role {role!r}",
            )
    for role, source in input_paths.items():
        if role.startswith("original_") and (source == root or root in source.parents):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{comparison_name} original source aliases the corrected tree for role {role!r}",
            )

    outputs = _records_by_role(
        payload.get("output_files"),
        f"{comparison_name} output matrix",
        tuple(COMPARISON_OUTPUT_FILES),
    )
    for role, filename in COMPARISON_OUTPUT_FILES.items():
        _require_record_matches(
            outputs[role], role, directory / filename, f"{comparison_name} output provenance"
        )
    return restricted_inputs, input_paths


def _validate_fixed_metric_table_protocol(
    path: Path,
    bootstrap_n: int,
    bootstrap_seed: int,
    label: str,
) -> None:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, csv.Error) as exc:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"invalid {label}: {exc}",
        ) from exc
    if not rows:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} is empty",
        )
    for row in rows:
        try:
            row_n = int(row["bootstrap_n"])
            row_seed = int(row["bootstrap_seed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} lacks valid bootstrap protocol columns",
            ) from exc
        if row_n != bootstrap_n or row_seed != bootstrap_seed:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} bootstrap protocol differs from its provenance",
            )


def _validate_fixed_metric_package(
    safe_provenance_path: Path,
    restricted_provenance_path: Path,
    expected_imaging_paths: Mapping[str, Path],
    metric_root: Path,
    corrected_root: Path,
    label: str,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Path],
    dict[str, Any],
]:
    safe_payload = _load_json_object(safe_provenance_path, f"{label} safe provenance")
    fixed_values = {
        "protocol_version": PROTOCOL_VERSION,
        "analysis_type": "fixed_saved_predictions_only",
        "model_refit": False,
        "prediction_regeneration": False,
        "prediction_recalibration": False,
        "threshold_optimization": False,
        "bootstrap_unit": "subject",
        "delta_mae_definition": "mae_comparator_minus_mae_imaging_ridge",
        "positive_delta_favors": "imaging_ridge",
    }
    if any(safe_payload.get(key) != value for key, value in fixed_values.items()):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} does not certify fixed saved-prediction metrics",
        )
    bootstrap_n = safe_payload.get("bootstrap_n")
    bootstrap_seed = safe_payload.get("bootstrap_seed")
    if (
        not isinstance(bootstrap_n, int)
        or isinstance(bootstrap_n, bool)
        or bootstrap_n <= 0
        or not isinstance(bootstrap_seed, int)
        or isinstance(bootstrap_seed, bool)
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} has an invalid bootstrap protocol",
        )
    if safe_payload.get("restricted_input_manifest_sha256") != sha256_file(
        restricted_provenance_path
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} is not bound to its restricted input provenance",
        )

    restricted_payload = _load_json_object(
        restricted_provenance_path, f"{label} restricted input provenance"
    )
    if (
        restricted_payload.get("schema_version")
        != "jdim-fixed-prediction-metrics-input-provenance-v1"
        or restricted_payload.get("status") != "FIXED_PREDICTION_INPUTS_LOCKED"
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} restricted input provenance is invalid",
        )
    safe_inputs = _records_by_role(
        safe_payload.get("input_files"), f"{label} safe input matrix"
    )
    restricted_inputs = _records_by_role(
        restricted_payload.get("input_files"), f"{label} restricted input matrix"
    )
    if set(safe_inputs) != set(restricted_inputs):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} safe/restricted input roles differ",
        )
    required_imaging_roles = {
        f"imaging_predictions_{target}" for target in TARGETS
    }
    unexpected = sorted(
        role
        for role in restricted_inputs
        if role not in required_imaging_roles and not role.startswith("nonimage_predictions_")
    )
    if not required_imaging_roles.issubset(restricted_inputs) or unexpected:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} has an invalid fixed-metric input role matrix: {unexpected}",
        )
    input_paths: dict[str, Path] = {}
    for role in sorted(restricted_inputs):
        if not _records_match_after_path_redaction(
            safe_inputs[role], restricted_inputs[role]
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} safe/restricted records differ for role {role!r}",
            )
        input_paths[role] = _require_restricted_record_path(
            restricted_inputs[role], role, f"{label} restricted input provenance"
        )
    for target, expected in expected_imaging_paths.items():
        role = f"imaging_predictions_{target}"
        if input_paths.get(role) != expected.resolve():
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} imaging source is mislabeled for target {target!r}",
            )
    for role, source in input_paths.items():
        if role.startswith("nonimage_predictions_") and (
            source == corrected_root or corrected_root in source.parents
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} non-image comparator aliases the corrected output tree",
            )

    for filename in COMPARISON_REVIEWER_TABLES:
        _validate_fixed_metric_table_protocol(
            metric_root / filename,
            bootstrap_n,
            bootstrap_seed,
            f"{label} {filename}",
        )
    boundaries = _load_json_object(
        metric_root / "training_tertile_boundaries.json",
        f"{label} training-tertile boundaries",
    )
    if (
        boundaries.get("protocol_version") != PROTOCOL_VERSION
        or boundaries.get("source_split") != "train"
        or boundaries.get("test_labels_used_to_define_boundaries") is not False
        or not isinstance(boundaries.get("targets"), Mapping)
        or set(boundaries["targets"]) != set(TARGETS)
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"{label} training-tertile provenance is invalid",
        )
    for target in TARGETS:
        target_payload = boundaries["targets"].get(target)
        role = f"imaging_predictions_{target}"
        if (
            not isinstance(target_payload, Mapping)
            or target_payload.get("source_split") != "train"
            or target_payload.get("quantile_method") != "numpy_linear"
            or not isinstance(target_payload.get("lower_boundary"), (int, float))
            or not isinstance(target_payload.get("upper_boundary"), (int, float))
            or target_payload["lower_boundary"] >= target_payload["upper_boundary"]
            or not isinstance(target_payload.get("n_training_studies"), int)
            or target_payload["n_training_studies"] <= 0
            or not isinstance(target_payload.get("n_training_subjects"), int)
            or target_payload["n_training_subjects"] <= 0
            or target_payload.get("source_prediction_file_sha256")
            != restricted_inputs[role]["sha256"]
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"{label} tertile boundaries are not bound to {target} predictions",
            )
    output_records = _records_by_role(
        safe_payload.get("output_files"),
        f"{label} output matrix",
        tuple(FIXED_METRIC_OUTPUT_FILES),
    )
    for role, filename in FIXED_METRIC_OUTPUT_FILES.items():
        _require_record_matches(
            output_records[role],
            role,
            metric_root / filename,
            f"{label} output provenance",
        )
    return safe_payload, restricted_inputs, input_paths, boundaries


def _validate_reviewer_metric_provenance(
    root: Path,
    main_paths: Mapping[str, Path],
) -> None:
    corrected_metric_root = root / "aggregate_safe" / "reviewer_metrics"
    corrected_restricted = (
        root
        / "restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
    )
    corrected_expected = {
        target: root
        / "restricted"
        / "analyses"
        / target
        / "all_clips"
        / "imaging_baseline_predictions.csv"
        for target in TARGETS
    }
    (
        corrected_safe,
        corrected_inputs,
        corrected_paths,
        corrected_boundaries,
    ) = _validate_fixed_metric_package(
        corrected_metric_root / "fixed_prediction_metrics_provenance.json",
        corrected_restricted,
        corrected_expected,
        corrected_metric_root,
        root,
        "corrected reviewer metrics",
    )

    original_safe_path = main_paths[
        "original_reviewer_metrics_fixed_prediction_metrics_provenance.json"
    ]
    original_metric_root = original_safe_path.parent
    original_restricted = main_paths[
        "original_reviewer_metrics_input_provenance"
    ]
    original_expected = {
        target: main_paths[f"original_predictions_{target}"] for target in TARGETS
    }
    (
        original_safe,
        original_inputs,
        original_paths,
        original_boundaries,
    ) = _validate_fixed_metric_package(
        original_safe_path,
        original_restricted,
        original_expected,
        original_metric_root,
        root,
        "original reviewer metrics",
    )
    for field in FIXED_METRIC_PROTOCOL_FIELDS:
        if original_safe.get(field) != corrected_safe.get(field):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"original/corrected reviewer-metric protocol differs for {field!r}",
            )
    for filename in (
        *COMPARISON_REVIEWER_TABLES,
        *COMPARISON_REVIEWER_SUPPORT_FILES,
    ):
        original_role = f"original_reviewer_metrics_{filename}"
        corrected_role = f"corrected_reviewer_metrics_{filename}"
        if (
            main_paths.get(original_role) != (original_metric_root / filename).resolve()
            or main_paths.get(corrected_role)
            != (corrected_metric_root / filename).resolve()
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"main comparison reviewer file is outside its validated package: {filename}",
            )
    for target in TARGETS:
        original_target = dict(original_boundaries["targets"][target])
        corrected_target = dict(corrected_boundaries["targets"][target])
        original_target.pop("source_prediction_file_sha256", None)
        corrected_target.pop("source_prediction_file_sha256", None)
        if original_target != corrected_target:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"original/corrected training-tertile definitions differ for {target!r}",
            )
    original_nonimage = {
        role for role in original_inputs if role.startswith("nonimage_predictions_")
    }
    corrected_nonimage = {
        role for role in corrected_inputs if role.startswith("nonimage_predictions_")
    }
    if original_nonimage != corrected_nonimage:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "original/corrected reviewer metrics use different non-image comparators",
        )
    for role in sorted(original_nonimage):
        if (
            not _records_match_after_path_redaction(
                original_inputs[role], corrected_inputs[role]
            )
            or original_paths[role] != corrected_paths[role]
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"original/corrected reviewer metrics use different source for {role!r}",
            )

    expected_bound_sources = {
        "original_reviewer_metrics_input_provenance": original_restricted,
        "corrected_reviewer_metrics_input_provenance": corrected_restricted,
    }
    for role, expected in expected_bound_sources.items():
        if main_paths.get(role) != expected.resolve():
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"main comparison is not bound to reviewer input provenance role {role!r}",
            )


def _validate_upstream_provenance(root: Path) -> None:
    _validate_aggregation_provenance(root)
    main_inputs, main_paths = _validate_comparison_provenance(
        root, "original_vs_corrected_main", "all_clips", True
    )
    hard_inputs, hard_paths = _validate_comparison_provenance(
        root,
        "original_vs_corrected_hard_extremes",
        "all_clips_exclude_hard_extremes",
        False,
    )
    if (
        main_inputs["frozen_split_map"]["sha256"]
        != hard_inputs["frozen_split_map"]["sha256"]
        or main_paths["frozen_split_map"] != hard_paths["frozen_split_map"]
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "main and hard-extreme comparisons do not reference the same frozen split map",
        )
    _validate_reviewer_metric_provenance(root, main_paths)


def required_corrected_artifacts(corrected_root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {
        f"corrected_{Path(relative).name.replace('.', '_')}": corrected_root / relative
        for relative in AGGREGATION_FILES
    }
    for target in TARGETS:
        for scope in SCOPES:
            for filename in BASELINE_FILES:
                role = f"corrected_{target}_{scope}_{filename.replace('.', '_')}"
                files[role] = corrected_root / "restricted" / "analyses" / target / scope / filename
    for filename in REVIEWER_METRIC_FILES:
        role = f"corrected_reviewer_metrics_{filename.replace('.', '_')}"
        files[role] = corrected_root / "aggregate_safe" / "reviewer_metrics" / filename
    files["corrected_reviewer_metrics_input_provenance_restricted_json"] = (
        corrected_root
        / "restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
    )
    for comparison in ("original_vs_corrected_main", "original_vs_corrected_hard_extremes"):
        for filename in COMPARISON_FILES:
            role = f"{comparison}_{filename.replace('.', '_')}"
            files[role] = corrected_root / "aggregate_safe" / comparison / filename
        files[f"{comparison}_input_provenance_restricted_json"] = (
            corrected_root
            / "restricted"
            / "comparisons"
            / comparison
            / "input_provenance_restricted.json"
        )
    return files


def build_corrected_completion_manifest(
    corrected_root: Path,
    output_json: Path,
) -> dict[str, Any]:
    root = corrected_root.expanduser().resolve()
    destination = require_restricted_destination(output_json)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite corrected completion manifest: {destination}")
    if destination == root or root not in destination.parents:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "completion manifest must be written inside the immutable corrected root",
        )

    artifacts = required_corrected_artifacts(root)
    missing = sorted(role for role, path in artifacts.items() if not path.is_file())
    if missing:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"corrected workflow is incomplete; missing roles: {missing}",
        )

    _validate_upstream_provenance(root)

    payload = {
        "schema_version": "jdim-corrected-analysis-completion-v1",
        "status": "CORRECTED_ANALYSIS_COMPLETE",
        "model_refit": True,
        "corrected_analysis_complete": True,
        "required_artifact_count": int(len(artifacts)),
        "artifacts": [
            safe_file_record(role, path) for role, path in sorted(artifacts.items())
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, payload)
    return payload


def validate_corrected_completion_manifest(
    corrected_root: Path,
    completion_json: Path,
) -> dict[str, Any]:
    """Verify that a completion certificate still matches every required artifact."""

    root = corrected_root.expanduser().resolve()
    certificate = completion_json.expanduser().resolve()
    if certificate == root or root not in certificate.parents:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "completion manifest is not inside the corrected root",
        )
    try:
        payload = json.loads(certificate.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            f"invalid corrected completion manifest: {exc}",
        ) from exc
    if (
        payload.get("schema_version") != "jdim-corrected-analysis-completion-v1"
        or payload.get("status") != "CORRECTED_ANALYSIS_COMPLETE"
        or payload.get("model_refit") is not True
        or payload.get("corrected_analysis_complete") is not True
    ):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected completion manifest does not certify the full workflow",
        )

    artifacts = required_corrected_artifacts(root)
    records = payload.get("artifacts")
    if not isinstance(records, list) or payload.get("required_artifact_count") != len(artifacts):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected completion manifest has an incomplete artifact matrix",
        )
    by_role: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not str(record.get("logical_role", "")):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                "corrected completion manifest contains an invalid artifact record",
            )
        role = str(record["logical_role"])
        if role in by_role:
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                "corrected completion manifest contains duplicate artifact roles",
            )
        by_role[role] = record
    if set(by_role) != set(artifacts):
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "corrected completion manifest roles do not match the required artifact matrix",
        )
    for role, path in artifacts.items():
        if not path.is_file():
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected artifact is missing after certification: {role}",
            )
        observed = safe_file_record(role, path)
        declared = by_role[role]
        if (
            declared.get("sha256") != observed["sha256"]
            or declared.get("size_bytes") != observed["size_bytes"]
        ):
            raise Tier1BlockedError(
                BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
                f"corrected artifact changed after certification: {role}",
            )
    _validate_upstream_provenance(root)
    return payload
