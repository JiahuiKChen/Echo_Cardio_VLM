#!/usr/bin/env python3
"""Build a restricted JDIM provenance specification from explicit file roles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jdim_tier1.corrected_completion import (
    validate_corrected_completion_manifest,
)
from jdim_tier1.safety import (
    SAFE_ROLE_PATTERN,
    Tier1BlockedError,
    require_restricted_destination,
    sha256_file,
    write_json,
)


REQUIRED_ROLE_ARGUMENTS = (
    "split_map_role",
    "selected_study_universe_role",
    "structured_measurement_role",
    "embedding_manifest_role",
    "video_encoder_checkpoint_role",
    "audit_configuration_role",
)

CODEX_ASSISTED_ARTIFACTS = [
    "docs/jdim_major_revision_tier1_design.md",
    "docs/jdim_major_revision_tier1_runbook.md",
    "configs/jdim_cohort_lineage_v1.template.json",
    "configs/jdim_input_content_audit_v1.yaml",
    "configs/jdim_provenance_v1.template.json",
    "scripts/jdim_tier1/",
    "scripts/reconstruct_jdim_cohort_flow.py",
    "scripts/prepare_jdim_input_audit.py",
    "scripts/compute_jdim_fixed_prediction_metrics.py",
    "scripts/build_jdim_cohort_lineage_metadata.py",
    "scripts/build_jdim_provenance_spec.py",
    "scripts/build_jdim_provenance_manifests.py",
    "scripts/validate_jdim_corrected_completion.py",
    "scripts/scc_run_jdim_tier1.sh",
    "tests/test_jdim_tier1_*.py",
]

CORRECTED_REQUIRED_ROLES = {
    "frozen_study_embedding_manifest",
    "frozen_study_embedding_array",
    "historical_clip_embedding_manifest",
    "historical_clip_embedding_array",
    "corrected_study_embedding_manifest",
    "corrected_study_embedding_array",
    "corrected_clip_embedding_manifest",
    "corrected_clip_embedding_array",
    "corrected_aggregation_provenance",
    "corrected_main_comparison_provenance",
    "corrected_hard_extremes_comparison_provenance",
    "corrected_analysis_completion",
}

CORRECTED_PARENT_INPUT_KEYS = {
    "frozen_study_embedding_manifest": "frozen_study_manifest",
    "frozen_study_embedding_array": "frozen_study_embeddings",
    "historical_clip_embedding_manifest": "clip_manifest",
    "historical_clip_embedding_array": "clip_embeddings",
}


def parse_file_specs(values: list[str]) -> dict[str, dict[str, str]]:
    files: dict[str, dict[str, str]] = {}
    for value in values:
        pieces = value.split("=", 2)
        if len(pieces) != 3:
            raise ValueError(f"--file requires ROLE=CLASSIFICATION=ABSOLUTE_PATH: {value!r}")
        role, classification, raw_path = pieces
        if not SAFE_ROLE_PATTERN.fullmatch(role):
            raise ValueError(f"File role must be a path-free identifier: {role!r}")
        if role in files:
            raise ValueError(f"Duplicate file role: {role}")
        if classification not in {"restricted", "aggregate_safe"}:
            raise ValueError(f"Invalid classification for {role}: {classification!r}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ValueError(f"File path for {role} must be absolute")
        if not path.exists():
            raise FileNotFoundError(path)
        files[role] = {"path": str(path.resolve()), "classification": classification}
    if not files:
        raise ValueError("At least one --file is required")
    return files


def parse_arguments(values: list[str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--argument requires KEY=VALUE: {value!r}")
        key, raw = value.split("=", 1)
        if not SAFE_ROLE_PATTERN.fullmatch(key) or key in arguments:
            raise ValueError(f"Duplicate or invalid argument key: {key!r}")
        try:
            arguments[key] = json.loads(raw)
        except json.JSONDecodeError:
            arguments[key] = raw
    return arguments


def validate_corrected_completion_roles(
    files: dict[str, dict[str, str]],
    arguments: dict[str, Any],
) -> None:
    model_refit = arguments.get("model_refit", False)
    analysis_complete = arguments.get("corrected_analysis_complete", False)
    if not isinstance(model_refit, bool) or not isinstance(analysis_complete, bool):
        raise ValueError("model_refit and corrected_analysis_complete must be JSON booleans")
    if model_refit != analysis_complete:
        raise ValueError("model_refit and corrected_analysis_complete must agree")
    if not analysis_complete:
        return
    missing = sorted(CORRECTED_REQUIRED_ROLES - set(files))
    if missing:
        raise ValueError(f"Corrected provenance roles are incomplete: {missing}")
    completion_path = Path(files["corrected_analysis_completion"]["path"])
    if (
        completion_path.name != "corrected_analysis_completion_v1.json"
        or completion_path.parent.name != "aggregate_safe"
    ):
        raise ValueError("Corrected completion manifest is not at its canonical role path")
    corrected_root = completion_path.expanduser().resolve().parents[1]
    expected_role_paths = {
        "corrected_study_embedding_manifest": corrected_root
        / "aggregation/restricted/corrected_study_embedding_manifest.csv",
        "corrected_study_embedding_array": corrected_root
        / "aggregation/restricted/corrected_study_embeddings.npz",
        "corrected_clip_embedding_manifest": corrected_root
        / "aggregation/restricted/deduplicated_clip_manifest.csv",
        "corrected_clip_embedding_array": corrected_root
        / "aggregation/restricted/deduplicated_clip_embeddings.npz",
        "corrected_aggregation_provenance": corrected_root
        / "aggregation/restricted/corrected_aggregation_provenance_restricted.json",
        "corrected_main_comparison_provenance": corrected_root
        / "aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json",
        "corrected_hard_extremes_comparison_provenance": corrected_root
        / "aggregate_safe/original_vs_corrected_hard_extremes/original_vs_corrected_comparison_provenance.json",
        "corrected_analysis_completion": completion_path.expanduser().resolve(),
    }
    mislabeled = sorted(
        role
        for role, expected in expected_role_paths.items()
        if Path(files[role]["path"]).expanduser().resolve() != expected.resolve()
    )
    if mislabeled:
        raise ValueError(f"Corrected provenance roles are mislabeled: {mislabeled}")
    expected_classifications = {
        "corrected_study_embedding_manifest": "restricted",
        "corrected_study_embedding_array": "restricted",
        "corrected_clip_embedding_manifest": "restricted",
        "corrected_clip_embedding_array": "restricted",
        "corrected_aggregation_provenance": "restricted",
        "corrected_main_comparison_provenance": "aggregate_safe",
        "corrected_hard_extremes_comparison_provenance": "aggregate_safe",
        "corrected_analysis_completion": "aggregate_safe",
    }
    misclassified = sorted(
        role
        for role, expected in expected_classifications.items()
        if files[role]["classification"] != expected
    )
    if misclassified:
        raise ValueError(f"Corrected provenance roles are misclassified: {misclassified}")
    aggregation_provenance_path = expected_role_paths["corrected_aggregation_provenance"]
    try:
        aggregation_provenance = json.loads(
            aggregation_provenance_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid corrected aggregation provenance: {exc}") from exc
    if (
        aggregation_provenance.get("schema_version") != "jdim-corrected-aggregation-v1"
        or aggregation_provenance.get("status") != "CORRECTED_AGGREGATION_BUILT"
        or aggregation_provenance.get("frozen_parent_replay_exact") is not True
        or aggregation_provenance.get("frozen_parent_clip_counts_exact") is not True
    ):
        raise ValueError("Corrected aggregation provenance does not certify frozen-parent replay")
    provenance_inputs = aggregation_provenance.get("input_files")
    if not isinstance(provenance_inputs, dict):
        raise ValueError("Corrected aggregation provenance lacks its input-file matrix")
    parent_paths: dict[str, Path] = {}
    for role, provenance_key in CORRECTED_PARENT_INPUT_KEYS.items():
        record = provenance_inputs.get(provenance_key)
        if not isinstance(record, dict):
            raise ValueError(f"Corrected aggregation provenance lacks parent input {provenance_key}")
        declared_path = Path(str(record.get("path", ""))).expanduser()
        if not declared_path.is_absolute() or not declared_path.is_file():
            raise ValueError(f"Corrected aggregation parent path is invalid for {role}")
        actual_path = Path(files[role]["path"]).expanduser().resolve()
        if declared_path.resolve() != actual_path:
            raise ValueError(f"Corrected parent role does not match aggregation provenance: {role}")
        if files[role]["classification"] != "restricted":
            raise ValueError(f"Corrected parent role must be restricted: {role}")
        if str(record.get("sha256", "")) != sha256_file(actual_path):
            raise ValueError(f"Corrected parent hash does not match aggregation provenance: {role}")
        parent_paths[role] = actual_path
    if len(set(parent_paths.values())) != len(parent_paths):
        raise ValueError("Corrected parent roles must identify distinct artifacts")
    child_paths = {path.resolve() for path in expected_role_paths.values()}
    overlapping = sorted(role for role, path in parent_paths.items() if path in child_paths)
    if overlapping:
        raise ValueError(f"Corrected parent roles cannot alias corrected child artifacts: {overlapping}")
    try:
        validate_corrected_completion_manifest(corrected_root, completion_path)
    except Tier1BlockedError as exc:
        raise ValueError(str(exc)) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mimic-iv-echo-release", required=True)
    parser.add_argument("--echoprime-code-release", required=True)
    parser.add_argument("--file", action="append", default=[], metavar="ROLE=CLASSIFICATION=ABSOLUTE_PATH")
    parser.add_argument("--argument", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--split-map-role", default="split_map")
    parser.add_argument("--selected-study-universe-role", default="selected_study_universe")
    parser.add_argument("--structured-measurement-role", default="structured_measurements")
    parser.add_argument("--embedding-manifest-role", default="embedding_manifest")
    parser.add_argument("--video-encoder-checkpoint-role", default="video_encoder_checkpoint")
    parser.add_argument("--audit-configuration-role", default="audit_configuration")
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.mimic_iv_echo_release.strip():
        raise ValueError("--mimic-iv-echo-release must be explicit")
    if not args.echoprime_code_release.strip():
        raise ValueError("--echoprime-code-release must be explicit")
    files = parse_file_specs(args.file)
    role_assignments = {name: str(getattr(args, name)) for name in REQUIRED_ROLE_ARGUMENTS}
    missing = sorted(set(role_assignments.values()) - set(files))
    if missing:
        raise ValueError(f"Required provenance file roles are absent: {missing}")
    destination = require_restricted_destination(args.output_json)
    script_arguments = parse_arguments(args.argument)
    validate_corrected_completion_roles(files, script_arguments)
    payload = {
        "manifest_version": "jdim-provenance-v1",
        "mimic_iv_echo_release": args.mimic_iv_echo_release,
        "echoprime_code_release": args.echoprime_code_release,
        "files": files,
        **role_assignments,
        "script_arguments": script_arguments,
        "codex_assisted_artifacts": CODEX_ASSISTED_ARTIFACTS,
    }
    write_json(destination, payload)
    print(json.dumps({"status": "ok", "output_json": str(destination), "file_role_count": len(files)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
