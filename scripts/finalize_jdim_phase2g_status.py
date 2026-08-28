#!/usr/bin/env python3
"""Write a path-free Phase 2G stage-status certificate from completed artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_reconstruction import (
    BLOCKED_AUDIT_RECONSTRUCTION,
    BLOCKED_AUDIT_SOURCE_RESTORATION,
)
from jdim_tier1.safety import sha256_file, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--cohort-lock-json", type=Path, required=True)
    parser.add_argument("--roster-lock-json", type=Path, required=True)
    parser.add_argument("--source-availability-json", type=Path, required=True)
    parser.add_argument("--pilot-summary-json", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cohort = json.loads(args.cohort_lock_json.read_text(encoding="utf-8"))
    roster = json.loads(args.roster_lock_json.read_text(encoding="utf-8"))
    availability = json.loads(args.source_availability_json.read_text(encoding="utf-8"))
    if cohort.get("status") != "JDIM_COHORT_FLOW_LOCKED":
        raise ValueError("cohort lock status is not valid")
    if roster.get("status") != "AUDIT_ROSTER_LOCKED":
        raise ValueError("audit roster lock status is not valid")
    availability_status = str(availability.get("status", ""))
    pilot_status = BLOCKED_AUDIT_SOURCE_RESTORATION
    pilot_hash = None
    if args.pilot_summary_json is not None:
        pilot = json.loads(args.pilot_summary_json.read_text(encoding="utf-8"))
        pilot_hash = sha256_file(args.pilot_summary_json)
        pilot_status = (
            "AUDIT_PACKET_READY_FOR_HUMAN_REVIEW"
            if pilot.get("status") == "ok"
            else BLOCKED_AUDIT_RECONSTRUCTION
        )
    elif availability_status != BLOCKED_AUDIT_SOURCE_RESTORATION:
        pilot_status = BLOCKED_AUDIT_RECONSTRUCTION
    payload = {
        "status": "PHASE2G_COHORT_AND_ROSTER_COMPLETE",
        "source_commit": args.source_commit,
        "cohort_status": "JDIM_COHORT_FLOW_LOCKED",
        "audit_roster_status": "AUDIT_ROSTER_LOCKED",
        "technical_pilot_status": pilot_status,
        "cohort_lock_sha256": sha256_file(args.cohort_lock_json),
        "audit_roster_lock_sha256": sha256_file(args.roster_lock_json),
        "source_availability_sha256": sha256_file(args.source_availability_json),
        "pilot_summary_sha256": pilot_hash,
        "scientific_analysis_rerun": False,
        "roster_modified_after_lock": False,
        "human_audit_conducted": False,
    }
    write_json(args.output_json, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
