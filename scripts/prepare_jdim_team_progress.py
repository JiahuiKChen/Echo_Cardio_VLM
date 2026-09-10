#!/usr/bin/env python3
"""Dry-run or activate JDIM V3 team progress and owner coordination."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_team_progress import (
    activate_team_progress,
    metadata_only_team_progress_dry_run,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run")
    dry_run.add_argument("--package-root", type=Path, required=True)
    dry_run.add_argument("--source-commit", required=True)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--package-root", type=Path, required=True)
    activate.add_argument("--source-commit", required=True)
    activate.add_argument("--backup-certificate", type=Path, required=True)
    activate.add_argument("--owner-quiet-window-confirmed", required=True)
    activate.add_argument("--created-at-utc")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "dry-run":
        result = metadata_only_team_progress_dry_run(
            args.package_root,
            source_commit=args.source_commit,
        )
    else:
        if args.owner_quiet_window_confirmed != "TRUE":
            raise PermissionError("OWNER_QUIET_WINDOW_CONFIRMED=TRUE is required")
        result = activate_team_progress(
            args.package_root,
            source_commit=args.source_commit,
            backup_certificate_path=args.backup_certificate,
            owner_quiet_window_confirmed=True,
            created_at_utc=args.created_at_utc,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
