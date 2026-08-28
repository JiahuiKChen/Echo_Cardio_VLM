#!/usr/bin/env python3
"""Assess source/processed input availability for the full locked JDIM audit roster."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_reconstruction import (
    BLOCKED_AUDIT_RECONSTRUCTION,
    assess_audit_source_availability,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-linkage-csv", type=Path, required=True)
    parser.add_argument("--canonical-clip-roster-csv", type=Path, required=True)
    parser.add_argument("--canonical-clip-manifest-csv", type=Path, required=True)
    parser.add_argument("--dicom-data-root", type=Path, required=True)
    parser.add_argument("--restricted-output-root", type=Path, required=True)
    parser.add_argument("--safe-output-dir", type=Path, required=True)
    parser.add_argument("--max-ready-pilot-studies", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = assess_audit_source_availability(
            audit_linkage_csv=args.audit_linkage_csv,
            clip_roster_csv=args.canonical_clip_roster_csv,
            clip_manifest_csv=args.canonical_clip_manifest_csv,
            dicom_data_root=args.dicom_data_root,
            output_root=args.restricted_output_root,
            safe_output_dir=args.safe_output_dir,
            max_ready_pilot_studies=args.max_ready_pilot_studies,
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
        print(
            json.dumps(
                {"status": BLOCKED_AUDIT_RECONSTRUCTION, "detail": str(exc)},
                indent=2,
            )
        )
        return 2
    print(json.dumps(result.safe_summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
