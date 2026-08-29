#!/usr/bin/env python3
"""Run the bounded, wall-time-safe JDIM Phase 2J-R continuation stages."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jdim_tier1.phase2jr import (
    BLOCKED_AUDIT_INTERFACE,
    BLOCKED_RESTORATION_CONTINUATION_STATE,
    INTERFACE_INCOMPLETE_RESUMABLE,
    LOCKED_ROSTER_SOURCE_RESTORED,
    RESTORATION_INCOMPLETE_RESUMABLE,
    _atomic_write_json,
    assess_restoration_state,
    continue_audit_interface,
    resume_locked_restoration,
    verify_interface_continuation_certificate,
    verify_phase2jr2_preflight_state,
    verify_qacct_accounting,
    verify_qacct_certificate_agreement,
    verify_restoration_certificate,
    verify_restoration_continuation_certificate,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file


def _restoration_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--locked-url-list", type=Path, required=True)
    parser.add_argument("--restoration-manifest-csv", type=Path, required=True)
    parser.add_argument("--source-destination-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    assess = subparsers.add_parser("assess-restoration")
    _restoration_inputs(assess)
    assess.add_argument("--restricted-output-csv", type=Path)
    assess.add_argument("--safe-output-json", type=Path)
    assess.add_argument("--no-write", action="store_true")
    assess.add_argument("--require-phase2jr2-current-state", action="store_true")

    qacct = subparsers.add_parser("verify-qacct")
    qacct.add_argument("--expected-jobnumber", type=int, required=True)
    qacct.add_argument("--expected-failed", type=int, required=True)
    qacct.add_argument("--expected-exit-status", type=int, required=True)
    qacct.add_argument("--expected-ru-wallclock", required=True)

    qacct_certificate = subparsers.add_parser("verify-qacct-certificate")
    qacct_certificate.add_argument("--expected-jobnumber", type=int, required=True)
    qacct_certificate.add_argument("--certificate-status", required=True)

    resume = subparsers.add_parser("resume-restoration")
    _restoration_inputs(resume)
    resume.add_argument("--run-root", type=Path, required=True)
    resume.add_argument("--netrc-path", type=Path, required=True)
    resume.add_argument("--continuation-label", required=True)
    resume.add_argument("--max-workers", type=int, default=2)
    resume.add_argument("--checkpoint-every", type=int, default=25)
    resume.add_argument("--checkpoint-seconds", type=float, default=900.0)
    resume.add_argument("--soft-stop-seconds", type=float, default=38_700.0)
    resume.add_argument(
        "--incomplete-status",
        choices=[RESTORATION_INCOMPLETE_RESUMABLE, "BLOCKED_LOCKED_SOURCE_RESTORATION"],
        default=RESTORATION_INCOMPLETE_RESUMABLE,
    )

    verify = subparsers.add_parser("verify-restoration")
    verify.add_argument("--certificate", type=Path, required=True)
    verify.add_argument("--results-csv", type=Path, required=True)
    verify.add_argument("--expected-source-commit", required=True)
    verify.add_argument("--expected-url-list-sha256", required=True)
    verify.add_argument("--safe-output-json", type=Path)

    verify_resume = subparsers.add_parser("verify-restoration-continuation")
    verify_resume.add_argument("--certificate", type=Path, required=True)
    verify_resume.add_argument("--results-csv", type=Path, required=True)
    verify_resume.add_argument("--expected-source-commit", required=True)
    verify_resume.add_argument("--expected-url-list-sha256", required=True)
    verify_resume.add_argument("--expected-destination-root", type=Path, required=True)

    verify_interface = subparsers.add_parser("verify-interface-continuation")
    verify_interface.add_argument("--certificate", type=Path, required=True)
    verify_interface.add_argument("--expected-source-commit", required=True)

    interface = subparsers.add_parser("continue-interface")
    interface.add_argument("--persistent-root", type=Path, required=True)
    interface.add_argument("--run-root", type=Path, required=True)
    interface.add_argument("--restored-source-results-csv", type=Path, required=True)
    interface.add_argument("--clip-roster-csv", type=Path, required=True)
    interface.add_argument("--audit-linkage-csv", type=Path, required=True)
    interface.add_argument("--primary-reader-manifest-csv", type=Path, required=True)
    interface.add_argument("--second-reader-manifest-csv", type=Path, required=True)
    interface.add_argument("--source-commit", required=True)
    interface.add_argument("--checkpoint-every", type=int, default=25)
    interface.add_argument("--checkpoint-seconds", type=float, default=900.0)
    interface.add_argument("--soft-stop-seconds", type=float, default=38_700.0)
    interface.add_argument(
        "--incomplete-status",
        choices=[INTERFACE_INCOMPLETE_RESUMABLE, BLOCKED_AUDIT_INTERFACE],
        default=INTERFACE_INCOMPLETE_RESUMABLE,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "assess-restoration":
            if args.no_write:
                if args.restricted_output_csv is not None or args.safe_output_json is not None:
                    raise ValueError("--no-write cannot be combined with assessment output paths")
            elif args.restricted_output_csv is None or args.safe_output_json is None:
                raise ValueError("assessment output paths are required unless --no-write is used")
            result = assess_restoration_state(
                locked_url_list=args.locked_url_list,
                restoration_manifest_csv=args.restoration_manifest_csv,
                source_destination_root=args.source_destination_root,
                restricted_output_csv=args.restricted_output_csv,
                safe_output_json=args.safe_output_json,
                source_commit=args.source_commit,
            ).safe_summary
            if args.require_phase2jr2_current_state:
                verify_phase2jr2_preflight_state(result)
        elif args.command == "verify-qacct":
            result = verify_qacct_accounting(
                sys.stdin.read(),
                expected_jobnumber=args.expected_jobnumber,
                expected_failed=args.expected_failed,
                expected_exit_status=args.expected_exit_status,
                expected_ru_wallclock=args.expected_ru_wallclock,
            )
        elif args.command == "verify-qacct-certificate":
            result = verify_qacct_certificate_agreement(
                sys.stdin.read(),
                expected_jobnumber=args.expected_jobnumber,
                certificate_status=args.certificate_status,
            )
        elif args.command == "resume-restoration":
            result = resume_locked_restoration(
                locked_url_list=args.locked_url_list,
                restoration_manifest_csv=args.restoration_manifest_csv,
                source_destination_root=args.source_destination_root,
                run_root=args.run_root,
                netrc_path=args.netrc_path,
                source_commit=args.source_commit,
                continuation_label=args.continuation_label,
                max_workers=args.max_workers,
                checkpoint_every=args.checkpoint_every,
                checkpoint_seconds=args.checkpoint_seconds,
                soft_stop_seconds=args.soft_stop_seconds,
                incomplete_status=args.incomplete_status,
            ).safe_summary
        elif args.command == "verify-restoration":
            result = verify_restoration_certificate(
                certificate_path=args.certificate,
                results_path=args.results_csv,
                expected_source_commit=args.expected_source_commit,
                expected_url_list_sha256=args.expected_url_list_sha256,
            )
            if args.safe_output_json is not None:
                no_op = {
                    "status": LOCKED_ROSTER_SOURCE_RESTORED,
                    "source_commit": args.expected_source_commit,
                    "no_op_validation": True,
                    "completion_certificate_sha256": sha256_file(args.certificate),
                    "restoration_results_sha256": sha256_file(args.results_csv),
                }
                _atomic_write_json(args.safe_output_json, no_op)
        elif args.command == "verify-restoration-continuation":
            result = verify_restoration_continuation_certificate(
                certificate_path=args.certificate,
                results_path=args.results_csv,
                expected_source_commit=args.expected_source_commit,
                expected_url_list_sha256=args.expected_url_list_sha256,
                expected_destination_root=args.expected_destination_root,
            )
        elif args.command == "verify-interface-continuation":
            result = verify_interface_continuation_certificate(
                certificate_path=args.certificate,
                expected_source_commit=args.expected_source_commit,
            )
        elif args.command == "continue-interface":
            result = continue_audit_interface(
                persistent_root=args.persistent_root,
                run_root=args.run_root,
                restored_source_results_csv=args.restored_source_results_csv,
                clip_roster_csv=args.clip_roster_csv,
                audit_linkage_csv=args.audit_linkage_csv,
                primary_reader_manifest_csv=args.primary_reader_manifest_csv,
                second_reader_manifest_csv=args.second_reader_manifest_csv,
                source_commit=args.source_commit,
                checkpoint_every=args.checkpoint_every,
                checkpoint_seconds=args.checkpoint_seconds,
                soft_stop_seconds=args.soft_stop_seconds,
                incomplete_status=args.incomplete_status,
            ).safe_summary
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, OSError, ValueError) as exc:
        fallback = (
            BLOCKED_AUDIT_INTERFACE
            if args.command in {"continue-interface", "verify-interface-continuation"}
            else BLOCKED_RESTORATION_CONTINUATION_STATE
        )
        print(json.dumps({"status": fallback, "detail": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
