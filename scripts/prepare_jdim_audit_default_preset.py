#!/usr/bin/env python3
"""Dry-run, back up, activate, and validate the JDIM Audit V3.1 preset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_default_preset import (
    activate_default_preset,
    create_production_review_backup,
    metadata_only_default_preset_dry_run,
    validate_default_preset_deployment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "backup", "validate"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--package-root", type=Path, required=True)
        subparser.add_argument("--source-commit", required=True)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--package-root", type=Path, required=True)
    activate.add_argument("--source-commit", required=True)
    activate.add_argument("--backup-certificate", type=Path, required=True)
    activate.add_argument(
        "--owner-quiet-window-confirmed",
        choices=("TRUE",),
        required=True,
        help="Literal owner gate required immediately before production cutover.",
    )
    activate.add_argument("--cutover-timestamp-utc")
    activate.add_argument("--cutover-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "dry-run":
        result = metadata_only_default_preset_dry_run(
            args.package_root,
            source_commit=args.source_commit,
        )
    elif args.command == "backup":
        result = create_production_review_backup(
            args.package_root,
            source_commit=args.source_commit,
        )
    elif args.command == "activate":
        result = activate_default_preset(
            args.package_root,
            source_commit=args.source_commit,
            backup_certificate_path=args.backup_certificate,
            owner_quiet_window_confirmed=(
                args.owner_quiet_window_confirmed == "TRUE"
            ),
            cutover_timestamp_utc=args.cutover_timestamp_utc,
            cutover_id=args.cutover_id,
        )
    else:
        result = validate_default_preset_deployment(
            args.package_root,
            source_commit=args.source_commit,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
