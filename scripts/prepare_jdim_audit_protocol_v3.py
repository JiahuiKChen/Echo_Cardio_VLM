#!/usr/bin/env python3
"""Dry-run, activate, or validate the clarified JDIM human-audit V3 protocol."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_protocol_v3 import (
    apply_side_by_side_addendum,
    apply_protocol_v3_transition,
    plan_protocol_v3_transition,
    validate_active_protocol_v3,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run")
    dry_run.add_argument("--package-root", type=Path, required=True)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--package-root", type=Path, required=True)
    activate.add_argument("--source-commit", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--package-root", type=Path, required=True)
    validate.add_argument("--require-fresh", action="store_true")
    addendum = subparsers.add_parser("apply-side-by-side-addendum")
    addendum.add_argument("--package-root", type=Path, required=True)
    addendum.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "dry-run":
            result = plan_protocol_v3_transition(args.package_root).aggregate_safe()
        elif args.command == "activate":
            plan = plan_protocol_v3_transition(args.package_root)
            result = apply_protocol_v3_transition(plan, source_commit=args.source_commit)
        elif args.command == "apply-side-by-side-addendum":
            result = apply_side_by_side_addendum(
                args.package_root,
                source_commit=args.source_commit,
            )
        else:
            result = validate_active_protocol_v3(
                args.package_root,
                require_fresh=args.require_fresh,
            )
    except (FileExistsError, FileNotFoundError, PermissionError, Tier1BlockedError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_PROTOCOL_V3",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
