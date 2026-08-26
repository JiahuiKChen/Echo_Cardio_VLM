#!/usr/bin/env python3
"""Create correction-ready decisions from metadata-resolved duplicate lineage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.duplicate_resolution import (
    resolve_duplicate_decisions,
    resolve_duplicate_decisions_from_recovery,
    write_resolved_duplicate_decisions,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-forensics-rows", type=Path, required=True)
    parser.add_argument("--prior-forensics-summary", type=Path, required=True)
    parser.add_argument("--metadata-groups-csv", type=Path, required=True)
    parser.add_argument("--metadata-summary-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--recovery-rows", type=Path)
    parser.add_argument("--recovery-summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if bool(args.recovery_rows) != bool(args.recovery_summary):
            raise ValueError("--recovery-rows and --recovery-summary must be provided together")
        resolver = (
            resolve_duplicate_decisions_from_recovery
            if args.recovery_rows is not None
            else resolve_duplicate_decisions
        )
        positional = [
            args.prior_forensics_rows,
            args.prior_forensics_summary,
            args.metadata_groups_csv,
            args.metadata_summary_json,
        ]
        if args.recovery_rows is not None and args.recovery_summary is not None:
            positional.extend([args.recovery_rows, args.recovery_summary])
        result = resolver(*positional)
        write_resolved_duplicate_decisions(result, args.output_root)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED_DUPLICATE_RESOLUTION_INPUT", "detail": str(exc)},
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result.summary["resolution_status"],
                "n_groups": result.summary["n_groups_with_valid_dedup_rule"],
                "classification_counts": result.summary["classification_counts"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
