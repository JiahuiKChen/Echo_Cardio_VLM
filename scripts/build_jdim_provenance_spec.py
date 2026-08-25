#!/usr/bin/env python3
"""Build a restricted JDIM provenance specification from explicit file roles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jdim_tier1.safety import SAFE_ROLE_PATTERN, require_restricted_destination, write_json


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
    "scripts/scc_run_jdim_tier1.sh",
    "tests/test_jdim_tier1_*.py",
]


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
    payload = {
        "manifest_version": "jdim-provenance-v1",
        "mimic_iv_echo_release": args.mimic_iv_echo_release,
        "echoprime_code_release": args.echoprime_code_release,
        "files": files,
        **role_assignments,
        "script_arguments": parse_arguments(args.argument),
        "codex_assisted_artifacts": CODEX_ASSISTED_ARTIFACTS,
    }
    write_json(destination, payload)
    print(json.dumps({"status": "ok", "output_json": str(destination), "file_role_count": len(files)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
