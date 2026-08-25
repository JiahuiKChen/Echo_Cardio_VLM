"""Validate and certify a complete duplicate-corrected fixed-protocol analysis."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .safety import (
    BLOCKED_LINEAGE,
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
    return roles, corrected_paths


def _validate_comparison_provenance(
    root: Path,
    comparison_name: str,
    scope: str,
    include_reviewer_metrics: bool,
) -> str:
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
    inputs = _records_by_role(
        payload.get("input_files"), f"{comparison_name} input matrix", tuple(expected_roles)
    )
    for role, source in corrected_paths.items():
        _require_record_matches(inputs[role], role, source, f"{comparison_name} input provenance")

    outputs = _records_by_role(
        payload.get("output_files"),
        f"{comparison_name} output matrix",
        tuple(COMPARISON_OUTPUT_FILES),
    )
    for role, filename in COMPARISON_OUTPUT_FILES.items():
        _require_record_matches(
            outputs[role], role, directory / filename, f"{comparison_name} output provenance"
        )
    return str(inputs["frozen_split_map"]["sha256"])


def _validate_upstream_provenance(root: Path) -> None:
    _validate_aggregation_provenance(root)
    main_split_hash = _validate_comparison_provenance(
        root, "original_vs_corrected_main", "all_clips", True
    )
    hard_split_hash = _validate_comparison_provenance(
        root,
        "original_vs_corrected_hard_extremes",
        "all_clips_exclude_hard_extremes",
        False,
    )
    if main_split_hash != hard_split_hash:
        raise Tier1BlockedError(
            BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION,
            "main and hard-extreme comparisons do not reference the same frozen split map",
        )


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
    for comparison in ("original_vs_corrected_main", "original_vs_corrected_hard_extremes"):
        for filename in COMPARISON_FILES:
            role = f"{comparison}_{filename.replace('.', '_')}"
            files[role] = corrected_root / "aggregate_safe" / comparison / filename
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
