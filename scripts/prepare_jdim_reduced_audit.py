#!/usr/bin/env python3
"""Build, validate, and administer the reduced role-aware JDIM audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.reduced_audit import (
    ParentAuditPaths,
    build_reduced_audit_result,
    validate_locked_reduced_roster,
    write_reduced_audit_roster,
)
from jdim_tier1.reduced_audit_interface import (
    ReviewerRegistry,
    RoleAwareCheckpointStore,
    RoleQueueStore,
    build_role_aware_interface_package,
    validate_role_aware_interface_package,
    write_interface_ready_certificate,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file, write_json


AUDIT_ROOT = Path("/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1")
PARENT_INTERFACE_ROOT = Path(
    "/restricted/project/mimicecho/outputs/jdim_phase2j_audit_interface_v2"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/restricted/project/mimicecho/outputs/jdim_reduced_audit_15_per_target_v1"
)


def add_parent_arguments(parser: argparse.ArgumentParser) -> None:
    locked = AUDIT_ROOT / "restricted/input_content_audit/locked_roster"
    interface = PARENT_INTERFACE_ROOT / "restricted/interface"
    parser.add_argument(
        "--audit-roster-lock",
        type=Path,
        default=AUDIT_ROOT / "aggregate_safe/input_content_audit/locked_roster/audit_roster_lock.json",
    )
    parser.add_argument("--audit-linkage", type=Path, default=locked / "audit_linkage.csv")
    parser.add_argument(
        "--canonical-clip-roster",
        type=Path,
        default=locked / "canonical_clip_roster_restricted.csv",
    )
    parser.add_argument("--parent-reader-manifest", type=Path, default=locked / "reader_manifest.csv")
    parser.add_argument(
        "--parent-second-reader-manifest",
        type=Path,
        default=locked / "second_reader_manifest.csv",
    )
    parser.add_argument(
        "--ready-certificate",
        type=Path,
        default=PARENT_INTERFACE_ROOT / "aggregate_safe/phase2jr_ready_certificate.json",
    )
    parser.add_argument(
        "--technical-lock-certificate",
        type=Path,
        default=PARENT_INTERFACE_ROOT / "aggregate_safe/technical_lock_certificate.json",
    )
    parser.add_argument(
        "--technical-interface-manifest",
        type=Path,
        default=PARENT_INTERFACE_ROOT
        / "restricted/audit_media/technical_interface_manifest_restricted.csv",
    )
    parser.add_argument(
        "--parent-primary-interface-manifest",
        type=Path,
        default=interface / "primary_reader_manifest.json",
    )
    parser.add_argument(
        "--parent-secondary-interface-manifest",
        type=Path,
        default=interface / "second_reader_manifest.json",
    )
    parser.add_argument(
        "--parent-interface-policy",
        type=Path,
        default=interface / "interface_policy.json",
    )
    parser.add_argument(
        "--parent-media-root",
        type=Path,
        default=PARENT_INTERFACE_ROOT / "restricted/audit_media/media",
    )
    parser.add_argument(
        "--pilot-checkpoint",
        type=Path,
        default=PARENT_INTERFACE_ROOT / "restricted/checkpoints/primary.reader1/checkpoint.json",
    )


def parent_paths(args: argparse.Namespace) -> ParentAuditPaths:
    return ParentAuditPaths(
        audit_roster_lock=args.audit_roster_lock,
        audit_linkage=args.audit_linkage,
        canonical_clip_roster=args.canonical_clip_roster,
        parent_reader_manifest=args.parent_reader_manifest,
        parent_second_reader_manifest=args.parent_second_reader_manifest,
        ready_certificate=args.ready_certificate,
        technical_lock_certificate=args.technical_lock_certificate,
        technical_interface_manifest=args.technical_interface_manifest,
        parent_primary_interface_manifest=args.parent_primary_interface_manifest,
        parent_secondary_interface_manifest=args.parent_secondary_interface_manifest,
        parent_interface_policy=args.parent_interface_policy,
        parent_media_root=args.parent_media_root,
        pilot_checkpoint=args.pilot_checkpoint,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    add_parent_arguments(preflight)

    build = subparsers.add_parser("build")
    add_parent_arguments(build)
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    build.add_argument("--source-commit", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)

    register = subparsers.add_parser("register-reviewer")
    register.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    register.add_argument("--reviewer-code", required=True)
    register.add_argument("--owner-confirmed-qualified", action="store_true")

    activate = subparsers.add_parser("set-reviewer-active")
    activate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    activate.add_argument("--reviewer-code", required=True)
    activate.add_argument("--active", choices=("yes", "no"), required=True)

    reassign = subparsers.add_parser("reassign-incomplete")
    reassign.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    reassign.add_argument("--event-id", required=True)
    reassign.add_argument("--reason", required=True)
    reassign.add_argument("--owner-confirmed", action="store_true")

    launch = subparsers.add_parser("record-start-screen-validation")
    launch.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    launch.add_argument("--server-node", required=True)
    launch.add_argument("--localhost-port", type=int, default=8765)
    return parser


def _administrative_stores(output_root: Path) -> tuple[ReviewerRegistry, RoleQueueStore, RoleAwareCheckpointStore]:
    interface = output_root / "restricted/interface"
    manifest = json.loads((interface / "study_manifest_restricted.json").read_text(encoding="utf-8"))
    registry = ReviewerRegistry(output_root / "restricted/reviewer_registry")
    queue = RoleQueueStore(
        output_root / "restricted/queue",
        interface / "queue_policy_restricted.json",
        registry,
    )
    checkpoints = RoleAwareCheckpointStore(
        output_root / "restricted/checkpoints",
        queue,
        manifest,
    )
    return registry, queue, checkpoints


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "preflight":
            result = build_reduced_audit_result(parent_paths(args))
            print(json.dumps({**result.summary, "pilot": result.pilot.aggregate_safe()}, indent=2, sort_keys=True))
            return 0
        if args.command == "build":
            paths = parent_paths(args)
            result = build_reduced_audit_result(paths)
            protocol = write_reduced_audit_roster(
                result,
                args.output_root,
                source_commit=args.source_commit,
            )
            interface = build_role_aware_interface_package(
                reduced_output_root=args.output_root,
                parent_media_root=paths.parent_media_root,
            )
            validation = validate_role_aware_interface_package(args.output_root)
            ready = write_interface_ready_certificate(args.output_root, validation)
            print(
                json.dumps(
                    {
                        "protocol_status": protocol["status"],
                        "interface_status": interface.summary["status"],
                        "status": ready["status"],
                        "protocol_lock_sha256": ready["protocol_lock_sha256"],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "validate":
            roster = validate_locked_reduced_roster(args.output_root)
            validation = validate_role_aware_interface_package(args.output_root)
            print(json.dumps({"roster": roster, "interface": validation}, indent=2, sort_keys=True))
            return 0
        if args.command == "register-reviewer":
            registry, _queue, _checkpoints = _administrative_stores(args.output_root)
            record = registry.register(
                args.reviewer_code,
                qualified=args.owner_confirmed_qualified,
            )
            print(
                json.dumps(
                    {
                        "status": "REVIEWER_REGISTERED",
                        "reviewer_code": record["reviewer_code"],
                        "owner_confirmed_qualified": record["owner_confirmed_qualified"],
                        "active": record["active"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "set-reviewer-active":
            registry, _queue, _checkpoints = _administrative_stores(args.output_root)
            record = registry.set_active(args.reviewer_code, args.active == "yes")
            print(
                json.dumps(
                    {
                        "status": "REVIEWER_STATUS_UPDATED",
                        "reviewer_code": record["reviewer_code"],
                        "active": record["active"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "reassign-incomplete":
            _registry, _queue, checkpoints = _administrative_stores(args.output_root)
            result = checkpoints.archive_for_reassignment(
                args.event_id,
                owner_confirmed=args.owner_confirmed,
                reason=args.reason,
            )
            print(
                json.dumps(
                    {
                        "status": "INCOMPLETE_REVIEW_ARCHIVED_FOR_REASSIGNMENT",
                        "record_sha256": result["record_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "record-start-screen-validation":
            ready_path = args.output_root / "aggregate_safe/reduced_audit_ready_certificate.json"
            launch_path = args.output_root / "aggregate_safe/start_screen_launch_validation.json"
            if launch_path.exists():
                raise FileExistsError("start-screen validation is already recorded")
            payload = {
                "status": "READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT",
                "ready_certificate_sha256": sha256_file(ready_path),
                "server_node": args.server_node,
                "localhost_port": int(args.localhost_port),
                "localhost_only": True,
                "start_screen_visible": True,
                "reviewer_code_control_visible": True,
                "primary_secondary_selector_visible": True,
                "no_study_claimed_during_launch_validation": True,
                "no_media_inspected_during_launch_validation": True,
            }
            write_json(launch_path, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, sort_keys=True))
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
