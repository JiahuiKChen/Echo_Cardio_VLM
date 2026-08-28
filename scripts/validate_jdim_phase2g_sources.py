#!/usr/bin/env python3
"""Fail-closed validation of locked Phase 2G source evidence without writing outputs."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from jdim_tier1.safety import sha256_file


BLOCKED_PHASE2G_SOURCE_MISMATCH = "BLOCKED_PHASE2G_SOURCE_MISMATCH"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--canonical-certificate-json", type=Path, required=True)
    parser.add_argument("--expected-certificate-sha256", required=True)
    parser.add_argument("--cohort-blocker-json", type=Path, required=True)
    parser.add_argument("--expected-blocker-sha256", required=True)
    parser.add_argument("--duplicate-decision-rows-csv", type=Path, required=True)
    parser.add_argument("--duplicate-decision-summary-json", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-summary-json", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-groups-csv", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-stage-rows-csv", type=Path, required=True)
    parser.add_argument("--corrected-clip-manifest-csv", type=Path, required=True)
    parser.add_argument("--corrected-clip-embeddings-npz", type=Path, required=True)
    parser.add_argument("--corrected-study-manifest-csv", type=Path, required=True)
    parser.add_argument("--corrected-study-embeddings-npz", type=Path, required=True)
    parser.add_argument("--proposed-output-root", type=Path, required=True)
    return parser.parse_args()


def _blocked(detail: str) -> dict[str, Any]:
    return {"status": BLOCKED_PHASE2G_SOURCE_MISMATCH, "detail": detail}


def _git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def validate(args: argparse.Namespace) -> dict[str, Any]:
    required = (
        args.canonical_certificate_json,
        args.cohort_blocker_json,
        args.duplicate_decision_rows_csv,
        args.duplicate_decision_summary_json,
        args.duplicate_metadata_summary_json,
        args.duplicate_metadata_groups_csv,
        args.duplicate_metadata_stage_rows_csv,
        args.corrected_clip_manifest_csv,
        args.corrected_clip_embeddings_npz,
        args.corrected_study_manifest_csv,
        args.corrected_study_embeddings_npz,
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        return _blocked(f"missing required source artifacts: {sorted(missing)}")
    if args.proposed_output_root.exists():
        return _blocked("proposed immutable output root already exists")
    head = _git_output("rev-parse", "HEAD")
    if head != args.expected_source_commit:
        return _blocked("SCC checkout does not match the authorized source commit")
    if _git_output("status", "--porcelain"):
        return _blocked("SCC checkout is not clean")
    if sha256_file(args.canonical_certificate_json) != args.expected_certificate_sha256:
        return _blocked("canonical certificate SHA-256 mismatch")
    if sha256_file(args.cohort_blocker_json) != args.expected_blocker_sha256:
        return _blocked("preserved cohort blocker SHA-256 mismatch")

    certificate = json.loads(args.canonical_certificate_json.read_text(encoding="utf-8"))
    blocker = json.loads(args.cohort_blocker_json.read_text(encoding="utf-8"))
    if certificate.get("status") != "PHASE2E_CORRECTED_OUTPUTS_CANONICAL":
        return _blocked("canonical certificate status is not locked")
    if blocker.get("status") != "BLOCKED_COHORT_FLOW":
        return _blocked("preserved cohort blocker status differs")
    expected_corrected = {
        str(item.get("logical_role")): str(item.get("sha256"))
        for item in certificate.get("corrected_input_hashes", [])
    }
    corrected_paths = {
        "deduplicated_clip_manifest": args.corrected_clip_manifest_csv,
        "deduplicated_clip_embeddings": args.corrected_clip_embeddings_npz,
        "corrected_study_manifest": args.corrected_study_manifest_csv,
        "corrected_study_embeddings": args.corrected_study_embeddings_npz,
    }
    for role, path in corrected_paths.items():
        if expected_corrected.get(role) != sha256_file(path):
            return _blocked(f"locked corrected input hash mismatch: {role}")
    expected_decisions = {
        str(item.get("logical_role")): str(item.get("sha256"))
        for item in certificate.get("duplicate_decision_hashes", [])
    }
    if expected_decisions.get("duplicate_decision_rows") != sha256_file(
        args.duplicate_decision_rows_csv
    ):
        return _blocked("duplicate decision rows differ from the canonical certificate")
    if expected_decisions.get("duplicate_decision_summary") != sha256_file(
        args.duplicate_decision_summary_json
    ):
        return _blocked("duplicate decision summary differs from the canonical certificate")

    decision_summary = json.loads(args.duplicate_decision_summary_json.read_text(encoding="utf-8"))
    resolution = decision_summary.get("metadata_resolution", {})
    if resolution.get("metadata_summary_sha256") != sha256_file(
        args.duplicate_metadata_summary_json
    ):
        return _blocked("duplicate metadata summary hash differs from the decision packet")
    if resolution.get("metadata_group_classification_sha256") != sha256_file(
        args.duplicate_metadata_groups_csv
    ):
        return _blocked("duplicate metadata group hash differs from the decision packet")
    metadata_summary = json.loads(args.duplicate_metadata_summary_json.read_text(encoding="utf-8"))
    stage_hash = metadata_summary.get("restricted_evidence_packet_sha256", {}).get(
        "metadata_stage_rows.csv"
    )
    if stage_hash != sha256_file(args.duplicate_metadata_stage_rows_csv):
        return _blocked("duplicate metadata stage rows differ from the approved packet")
    return {
        "status": "ok",
        "mode": "phase2g_source_preflight",
        "source_commit": head,
        "canonical_certificate_sha256": args.expected_certificate_sha256,
        "preserved_cohort_blocker_sha256": args.expected_blocker_sha256,
        "corrected_inputs_verified": len(corrected_paths),
        "duplicate_decision_artifacts_verified": 2,
        "duplicate_metadata_artifacts_verified": 3,
        "proposed_output_root_absent": True,
        "scientific_analysis_executed": False,
        "output_written": False,
    }


def main() -> int:
    args = parse_args()
    try:
        payload = validate(args)
    except (json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        payload = _blocked(f"source validation failed: {exc}")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
