#!/usr/bin/env python3
"""Audit or narrowly repair reduced-audit queue-transition metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.reduced_audit_interface import (
    BLOCKED_QUEUE_STATE_MIGRATION,
    audit_queue_transition_state,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--expected-stale-pointers", type=int, default=0)
    parser.add_argument(
        "--repair-stale-pointers",
        action="store_true",
        help="Repair only validated queue pointers whose immutable lock already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.expected_stale_pointers < 0:
        raise ValueError("expected stale-pointer count must be nonnegative")
    try:
        result = audit_queue_transition_state(
            args.package_root,
            expected_stale_pointers=args.expected_stale_pointers,
            repair_stale_pointers=args.repair_stale_pointers,
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "error": str(exc)}, sort_keys=True))
        return 2
    except (FileNotFoundError, PermissionError, ValueError):
        print(
            json.dumps(
                {
                    "status": BLOCKED_QUEUE_STATE_MIGRATION,
                    "error": "production queue metadata did not pass restricted validation",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
