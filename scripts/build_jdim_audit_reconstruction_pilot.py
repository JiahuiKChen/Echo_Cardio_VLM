#!/usr/bin/env python3
"""Build a restricted source-to-encoder-frame reconstruction pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_reconstruction import (
    BLOCKED_AUDIT_RECONSTRUCTION,
    build_reconstruction_pilot,
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
    parser.add_argument("--max-studies", type=int, default=6)
    parser.add_argument("--max-clips-per-study", type=int, default=3)
    parser.add_argument("--failure-rate-stop", type=float, default=0.05)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_reconstruction_pilot(
            audit_linkage_csv=args.audit_linkage_csv,
            clip_roster_csv=args.canonical_clip_roster_csv,
            clip_manifest_csv=args.canonical_clip_manifest_csv,
            dicom_data_root=args.dicom_data_root,
            output_root=args.restricted_output_root,
            safe_output_dir=args.safe_output_dir,
            max_studies=args.max_studies,
            max_clips_per_study=args.max_clips_per_study,
            failure_rate_stop=args.failure_rate_stop,
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
        print(json.dumps({"status": BLOCKED_AUDIT_RECONSTRUCTION, "detail": str(exc)}, indent=2))
        return 2
    print(json.dumps(result.safe_summary, indent=2, sort_keys=True))
    return 0 if result.safe_summary["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
