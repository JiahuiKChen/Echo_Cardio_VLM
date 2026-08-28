#!/usr/bin/env python3
"""Run bounded JDIM Phase 2I replay, restoration, tiering, and interface stages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_interface import build_blinded_interface_package
from jdim_tier1.phase2i import (
    BLOCKED_PHASE2I_SOURCE_MISMATCH,
    OFFICIAL_SOURCE_BASE,
    build_audit_media,
    build_technical_inventory,
    diagnose_existing_pilot,
    restore_locked_sources,
    verify_locked_phase2i_counts,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-locked", help="Validate locked Phase 2I counts")
    verify.add_argument("--roster-lock-json", type=Path, required=True)
    verify.add_argument("--source-availability-json", type=Path, required=True)

    diagnose = subparsers.add_parser("diagnose-pilot", help="Diagnose exactly the prior 18 clips")
    diagnose.add_argument("--pilot-rows-csv", type=Path, required=True)
    diagnose.add_argument("--clip-roster-csv", type=Path, required=True)
    diagnose.add_argument("--restricted-output-root", type=Path, required=True)
    diagnose.add_argument("--safe-output-dir", type=Path, required=True)
    diagnose.add_argument("--expected-attempts", type=int, default=18)

    restore = subparsers.add_parser("restore-sources", help="Restore only locked-roster DICOMs")
    restore.add_argument("--restoration-manifest-csv", type=Path, required=True)
    restore.add_argument("--restricted-output-root", type=Path, required=True)
    restore.add_argument("--safe-output-dir", type=Path, required=True)
    restore.add_argument("--source-destination-root", type=Path, required=True)
    restore.add_argument("--netrc-path", type=Path, required=True)
    restore.add_argument("--official-base-url", default=OFFICIAL_SOURCE_BASE)
    restore.add_argument("--max-workers", type=int, default=4)
    restore.add_argument("--job-a-command", default="qsub <validated Phase 2I Job A script>")
    restore.add_argument("--plan-only", action="store_true")

    inventory = subparsers.add_parser("build-inventory", help="Build conservative technical tiers")
    inventory.add_argument("--clip-roster-csv", type=Path, required=True)
    inventory.add_argument("--audit-linkage-csv", type=Path, required=True)
    inventory.add_argument("--restored-source-results-csv", type=Path, required=True)
    inventory.add_argument("--restricted-output-root", type=Path, required=True)
    inventory.add_argument("--safe-output-dir", type=Path, required=True)

    media = subparsers.add_parser("build-media", help="Render restricted opaque audit displays")
    media.add_argument("--technical-inventory-csv", type=Path, required=True)
    media.add_argument("--restricted-output-root", type=Path, required=True)

    interface = subparsers.add_parser("build-interface", help="Build the restricted blinded interface")
    interface.add_argument("--technical-manifest-csv", type=Path, required=True)
    interface.add_argument("--reader-manifest-csv", type=Path, required=True)
    interface.add_argument("--second-reader-manifest-csv", type=Path, required=True)
    interface.add_argument("--media-root", type=Path, required=True)
    interface.add_argument("--restricted-output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "verify-locked":
            result = verify_locked_phase2i_counts(
                roster_lock_json=args.roster_lock_json,
                source_availability_json=args.source_availability_json,
            )
        elif args.command == "diagnose-pilot":
            result = diagnose_existing_pilot(
                pilot_rows_csv=args.pilot_rows_csv,
                clip_roster_csv=args.clip_roster_csv,
                output_root=args.restricted_output_root,
                safe_output_dir=args.safe_output_dir,
                expected_attempts=args.expected_attempts,
            ).safe_summary
        elif args.command == "restore-sources":
            result = restore_locked_sources(
                restoration_manifest_csv=args.restoration_manifest_csv,
                output_root=args.restricted_output_root,
                safe_output_dir=args.safe_output_dir,
                source_destination_root=args.source_destination_root,
                netrc_path=args.netrc_path,
                official_base_url=args.official_base_url,
                max_workers=args.max_workers,
                perform_downloads=not args.plan_only,
                job_a_command=args.job_a_command,
            ).safe_summary
        elif args.command == "build-inventory":
            result = build_technical_inventory(
                clip_roster_csv=args.clip_roster_csv,
                audit_linkage_csv=args.audit_linkage_csv,
                restored_source_results_csv=args.restored_source_results_csv,
                output_root=args.restricted_output_root,
                safe_output_dir=args.safe_output_dir,
            ).safe_summary
        elif args.command == "build-media":
            result = build_audit_media(
                technical_inventory_csv=args.technical_inventory_csv,
                output_root=args.restricted_output_root,
            ).safe_summary
        elif args.command == "build-interface":
            result = build_blinded_interface_package(
                technical_manifest_csv=args.technical_manifest_csv,
                reader_manifest_csv=args.reader_manifest_csv,
                second_reader_manifest_csv=args.second_reader_manifest_csv,
                media_root=args.media_root,
                output_root=args.restricted_output_root,
            ).summary
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": BLOCKED_PHASE2I_SOURCE_MISMATCH, "detail": str(exc)},
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
