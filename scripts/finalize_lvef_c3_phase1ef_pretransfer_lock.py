#!/usr/bin/env python3
"""Finalize the aggregate-safe Phase 1E-F zero-scope authority chain."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import build_lvef_c3_backup_recovery_witness as backup
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer
import build_lvef_c3_production_authority_packet as packet
import build_lvef_c3_production_launch_authority as launch
import capture_lvef_c3_post_reallocation_capacity as capacity


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "lvef_c3_phase1ef_final_pretransfer_lock_v1"
STATUS = "PASS_CURRENT_COMMIT_PACKET_AND_LAUNCH_ZERO_SCOPE"
ATTEMPT_RE = pretransfer.ATTEMPT_RE
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
AUTHORITY_ROLES = frozenset(
    {
        "capacity_receipt", "capacity_aggregate", "backup_manifest",
        "backup_aggregate", "restore_receipt", "pretransfer_lock",
        "future_first_batch_command", "execution_environment",
        "production_authority_packet", "launch_envelope",
    }
)
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "production_attempt_id", "governing_commit", "created_at_utc",
        "authority", "capacity", "backup_recovery", "production_lock",
        "execution_attestations", "authorization_scopes_granted",
        "full_c3_status",
    }
)
CAPACITY_KEYS = frozenset(
    {
        "research_quota_bytes", "research_usage_bytes",
        "research_quota_remaining_bytes", "research_file_quota",
        "research_files_used", "research_file_slots_remaining",
        "research_filesystem_available_bytes", "backed_quota_bytes",
        "backed_usage_bytes", "backed_quota_remaining_bytes",
        "backed_file_quota", "backed_files_used",
        "backed_file_slots_remaining", "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed", "backed_control_tier_gate_passed",
        "research_quota_margin_above_minimum_bytes",
        "research_quota_slack_after_projected_peak_bytes",
        "research_margin_beyond_200gb_reserve_bytes",
        "research_physical_slack_bytes",
    }
)
CAPACITY_GATE_KEYS = frozenset(
    {
        "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_gate_passed",
    }
)
BACKUP_KEYS = frozenset(
    {
        "classification_counts", "declared_item_count", "copied_file_count",
        "copied_bytes", "git_bundle_bytes", "restored_file_count",
        "restored_bytes", "backup_verified", "restore_test_passed",
        "credential_material_absent", "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent", "unresolved_item_count",
    }
)
BACKUP_CLASSIFICATION_KEYS = frozenset(
    {
        "GIT_ORIGIN_PROTECTED", "COMMITTED_RECONSTRUCTABLE",
        "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE", "OWNER_RECREATABLE",
        "CHECKSUM_ONLY_NO_COPY_REQUIRED", "IRREPLACEABLE_BACKUP_REQUIRED",
        "EXCLUDED_CREDENTIAL_MATERIAL", "UNRESOLVED",
    }
)
PRODUCTION_KEYS = frozenset(
    {
        "authority_roles", "semantic_gates", "authorization_scope_count",
        "authorization_scopes_granted", "launch_envelope_created",
        "owner_stage_authorization_granted", "scheduler_submission_performed",
    }
)


class Phase1EFFinalizationError(RuntimeError):
    pass


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise Phase1EFFinalizationError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _read(path: Path) -> bytes:
    pretransfer._require_no_symlink_ancestors(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Phase1EFFinalizationError("AUTHORITY_OPEN_FAILED") from exc
    try:
        item = os.fstat(descriptor)
        if (
            not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
            or stat.S_IMODE(item.st_mode) != 0o600 or item.st_size <= 0
            or item.st_size > 1_000_000_000
        ):
            raise Phase1EFFinalizationError("AUTHORITY_NOT_PRIVATE_REGULAR")
        payload = b""
        while len(payload) <= item.st_size:
            block = os.read(descriptor, min(1_048_576, item.st_size + 1 - len(payload)))
            if not block:
                break
            payload += block
        if len(payload) != item.st_size:
            raise Phase1EFFinalizationError("AUTHORITY_CHANGED_DURING_READ")
        return payload
    finally:
        os.close(descriptor)


def _json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(_read(path).decode(), object_pairs_hook=_pairs)
    except Phase1EFFinalizationError:
        raise
    except Exception as exc:
        raise Phase1EFFinalizationError("AUTHORITY_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise Phase1EFFinalizationError("AUTHORITY_JSON_NOT_MAPPING")
    return value


def _binding(path: Path) -> Mapping[str, Any]:
    payload = _read(path)
    return {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != TOP_LEVEL_KEYS:
        raise Phase1EFFinalizationError("FINAL_LOCK_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != ARTIFACT_TYPE
        or value.get("status") != STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not packet.ATTEMPT_RE.fullmatch(str(value.get("production_attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not pretransfer.UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or value.get("authorization_scopes_granted") != 0
        or value.get("full_c3_status") != "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION"
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_IDENTITY_INVALID")
    authority = value.get("authority")
    if not isinstance(authority, Mapping) or set(authority) != AUTHORITY_ROLES:
        raise Phase1EFFinalizationError("FINAL_LOCK_AUTHORITY_ROLES_INVALID")
    for item in authority.values():
        if (
            not isinstance(item, Mapping) or set(item) != {"size_bytes", "sha256"}
            or not isinstance(item.get("size_bytes"), int)
            or isinstance(item.get("size_bytes"), bool) or item["size_bytes"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
        ):
            raise Phase1EFFinalizationError("FINAL_LOCK_BINDING_INVALID")
    capacity_value = value.get("capacity")
    backup_value = value.get("backup_recovery")
    production_value = value.get("production_lock")
    if not isinstance(capacity_value, Mapping) or set(capacity_value) != CAPACITY_KEYS:
        raise Phase1EFFinalizationError("FINAL_LOCK_CAPACITY_SCHEMA_NOT_CLOSED")
    if any(capacity_value.get(key) is not True for key in CAPACITY_GATE_KEYS):
        raise Phase1EFFinalizationError("FINAL_LOCK_GATE_NOT_PASS")
    for key in CAPACITY_KEYS - CAPACITY_GATE_KEYS:
        item = capacity_value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise Phase1EFFinalizationError("FINAL_LOCK_CAPACITY_VALUE_INVALID")
    if (
        capacity_value["research_quota_remaining_bytes"]
        != capacity_value["research_quota_bytes"]
        - capacity_value["research_usage_bytes"]
        or capacity_value["research_file_slots_remaining"]
        != capacity_value["research_file_quota"]
        - capacity_value["research_files_used"]
        or capacity_value["backed_quota_remaining_bytes"]
        != capacity_value["backed_quota_bytes"]
        - capacity_value["backed_usage_bytes"]
        or capacity_value["backed_file_slots_remaining"]
        != capacity_value["backed_file_quota"]
        - capacity_value["backed_files_used"]
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_CAPACITY_ARITHMETIC_INVALID")
    if not isinstance(backup_value, Mapping) or set(backup_value) != BACKUP_KEYS:
        raise Phase1EFFinalizationError("FINAL_LOCK_BACKUP_SCHEMA_NOT_CLOSED")
    classification = backup_value.get("classification_counts")
    if (
        not isinstance(classification, Mapping)
        or set(classification) != BACKUP_CLASSIFICATION_KEYS
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in classification.values()
        )
        or sum(classification.values()) != backup_value.get("declared_item_count")
        or classification.get("UNRESOLVED") != 0
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_BACKUP_COUNTS_INVALID")
    for key in BACKUP_KEYS - {
        "classification_counts", "backup_verified", "restore_test_passed",
        "credential_material_absent", "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent",
    }:
        item = backup_value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise Phase1EFFinalizationError("FINAL_LOCK_BACKUP_COUNTS_INVALID")
    if any(
        backup_value.get(key) is not True
        for key in (
            "backup_verified", "restore_test_passed", "credential_material_absent",
            "private_project_or_billing_material_absent",
            "unapproved_bulk_scientific_payload_absent",
        )
    ) or backup_value.get("unresolved_item_count") != 0:
        raise Phase1EFFinalizationError("FINAL_LOCK_GATE_NOT_PASS")
    if (
        not isinstance(production_value, Mapping)
        or set(production_value) != PRODUCTION_KEYS
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_PRODUCTION_SCHEMA_NOT_CLOSED")
    if production_value != {
        "authority_roles": len(packet.REQUIRED_ROLES),
        "semantic_gates": len(packet.SEMANTIC_VALIDATION_KEYS),
        "authorization_scope_count": len(packet.AUTHORIZATION_SCOPES),
        "authorization_scopes_granted": 0,
        "launch_envelope_created": True,
        "owner_stage_authorization_granted": False,
        "scheduler_submission_performed": False,
    }:
        raise Phase1EFFinalizationError("FINAL_LOCK_GATE_NOT_PASS")
    if value.get("execution_attestations") != pretransfer.EXECUTION_ATTESTATIONS:
        raise Phase1EFFinalizationError("FINAL_LOCK_EXECUTION_BOUNDARY_INVALID")


def _without_path(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "size_bytes": value.get("size_bytes"),
        "sha256": value.get("sha256"),
    }


def _validate_cross_bindings(
    *, bindings: Mapping[str, Mapping[str, Any]],
    capacity_value: Mapping[str, Any], backup_value: Mapping[str, Any],
    pretransfer_value: Mapping[str, Any], packet_value: Mapping[str, Any],
    launch_value: Mapping[str, Any], paths: Mapping[str, Path],
) -> None:
    """Reject any individually valid but mixed authority-chain artifact."""
    if (
        capacity_value.get("restricted_receipt_size_bytes")
        != bindings["capacity_receipt"]["size_bytes"]
        or capacity_value.get("restricted_receipt_sha256")
        != bindings["capacity_receipt"]["sha256"]
        or backup_value.get("backup_manifest_sha256")
        != bindings["backup_manifest"]["sha256"]
        or backup_value.get("restore_receipt_sha256")
        != bindings["restore_receipt"]["sha256"]
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_DETAIL_BINDING_MISMATCH")
    pretransfer_expected = {
        "post_reallocation_capacity_receipt": bindings["capacity_receipt"],
        "post_reallocation_capacity_aggregate": bindings["capacity_aggregate"],
        "backup_manifest": bindings["backup_manifest"],
        "backup_recovery_aggregate": bindings["backup_aggregate"],
        "restore_test_receipt": bindings["restore_receipt"],
        "future_first_batch_command": bindings["future_first_batch_command"],
    }
    if pretransfer_value.get("authority") != pretransfer_expected:
        raise Phase1EFFinalizationError("FINAL_LOCK_PRETRANSFER_BINDING_MISMATCH")
    packet_authority = packet_value.get("authority")
    if not isinstance(packet_authority, Mapping) or any(
        packet_authority.get(role) != bindings[binding_role]
        for role, binding_role in {
            "post_expansion_capacity_summary": "pretransfer_lock",
            "future_command_block": "future_first_batch_command",
            "execution_environment": "execution_environment",
        }.items()
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_PACKET_BINDING_MISMATCH")
    for launch_role, binding_role in {
        "execution_environment": "execution_environment",
        "post_expansion_capacity_summary": "pretransfer_lock",
        "production_authority_packet": "production_authority_packet",
    }.items():
        launch_binding = launch_value.get(launch_role)
        if (
            not isinstance(launch_binding, Mapping)
            or _without_path(launch_binding) != bindings[binding_role]
            or Path(str(launch_binding.get("path", ""))).resolve(strict=True)
            != paths[binding_role].resolve(strict=True)
        ):
            raise Phase1EFFinalizationError("FINAL_LOCK_LAUNCH_BINDING_MISMATCH")


def validate_terminal_for_launch(
    path: Path, *, launch_envelope_path: Path, pretransfer_path: Path,
    packet_path: Path, execution_environment: Path,
    production_attempt_id: str, governing_commit: str,
) -> Mapping[str, Any]:
    """Validate the terminal Phase 1E-F canary against the live launch chain."""
    value = _json(path)
    validate_aggregate_output(value)
    if (
        value.get("production_attempt_id") != production_attempt_id
        or value.get("governing_commit") != governing_commit
    ):
        raise Phase1EFFinalizationError("TERMINAL_LOCK_IDENTITY_MISMATCH")
    expected = {
        "launch_envelope": _binding(launch_envelope_path),
        "pretransfer_lock": _binding(pretransfer_path),
        "production_authority_packet": _binding(packet_path),
        "execution_environment": _binding(execution_environment),
    }
    authority = value.get("authority")
    if not isinstance(authority, Mapping) or any(
        authority.get(role) != binding for role, binding in expected.items()
    ):
        raise Phase1EFFinalizationError("TERMINAL_LOCK_CHAIN_BINDING_MISMATCH")
    return value


def build(args: argparse.Namespace) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(args.attempt_id) or not COMMIT_RE.fullmatch(args.governing_commit):
        raise Phase1EFFinalizationError("FINAL_LOCK_ARGUMENT_IDENTITY_INVALID")
    paths = {
        "capacity_receipt": args.capacity_receipt,
        "capacity_aggregate": args.capacity_aggregate,
        "backup_manifest": args.backup_manifest,
        "backup_aggregate": args.backup_aggregate,
        "restore_receipt": args.restore_receipt,
        "pretransfer_lock": args.pretransfer_lock,
        "future_first_batch_command": args.future_command,
        "execution_environment": args.execution_environment,
        "production_authority_packet": args.production_packet,
        "launch_envelope": args.launch_envelope,
    }
    bindings = {role: _binding(path) for role, path in paths.items()}
    capacity_receipt_value = _json(args.capacity_receipt)
    capacity_value = _json(args.capacity_aggregate)
    backup_manifest_value = _json(args.backup_manifest)
    backup_value = _json(args.backup_aggregate)
    restore_value = _json(args.restore_receipt)
    pretransfer_value = _json(args.pretransfer_lock)
    packet_value = _json(args.production_packet)
    launch_value = _json(args.launch_envelope)
    capacity.validate_receipt_output(capacity_receipt_value)
    capacity.validate_aggregate_output(capacity_value)
    backup.validate_backup_manifest(backup_manifest_value)
    backup.validate_restore_receipt(restore_value)
    backup.validate_aggregate_output(backup_value)
    pretransfer.validate_aggregate_output(pretransfer_value)
    packet.validate_packet(packet_value)
    production_attempt = str(packet_value["attempt_id"])
    if (
        capacity_receipt_value.get("attempt_id") != args.attempt_id
        or capacity_value.get("attempt_id") != args.attempt_id
        or backup_manifest_value.get("attempt_id") != args.attempt_id
        or backup_value.get("attempt_id") != args.attempt_id
        or restore_value.get("attempt_id") != args.attempt_id
        or pretransfer_value.get("attempt_id") != args.attempt_id
        or any(item.get("governing_commit") != args.governing_commit for item in (
            capacity_receipt_value, capacity_value, backup_manifest_value,
            backup_value, restore_value, pretransfer_value, packet_value, launch_value
        ))
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_CROSS_AUTHORITY_MISMATCH")
    if (
        restore_value.get("backup_manifest_sha256")
        != bindings["backup_manifest"]["sha256"]
        or restore_value.get("policy_sha256")
        != backup_manifest_value.get("policy_sha256")
        or backup_value.get("policy_sha256")
        != backup_manifest_value.get("policy_sha256")
        or backup_value.get("selection_sha256")
        != backup_manifest_value.get("selection_sha256")
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_BACKUP_CHAIN_MISMATCH")
    launch.validate(
        launch_value, envelope_path=args.launch_envelope,
        attempt_id=production_attempt, governing_commit=args.governing_commit,
        execution_environment=args.execution_environment,
        require_terminal_lock=False,
    )
    _validate_cross_bindings(
        bindings=bindings,
        capacity_value=capacity_value,
        backup_value=backup_value,
        pretransfer_value=pretransfer_value,
        packet_value=packet_value,
        launch_value=launch_value,
        paths=paths,
    )
    scopes = packet_value["authorization_scopes"]
    if (
        any(scopes.values())
        or packet_value["execution_attestations"] != packet.EXECUTION_ATTESTATIONS
    ):
        raise Phase1EFFinalizationError("FINAL_LOCK_PACKET_EXECUTION_BOUNDARY_INVALID")
    value = {
        "schema_version": SCHEMA_VERSION, "artifact_type": ARTIFACT_TYPE,
        "status": STATUS, "attempt_id": args.attempt_id,
        "production_attempt_id": production_attempt,
        "governing_commit": args.governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": dict(sorted(bindings.items())),
        "capacity": {
            key: capacity_value[key] for key in (
                "research_quota_bytes", "research_usage_bytes",
                "research_quota_remaining_bytes", "research_file_quota",
                "research_files_used", "research_file_slots_remaining",
                "research_filesystem_available_bytes", "backed_quota_bytes",
                "backed_usage_bytes", "backed_quota_remaining_bytes",
                "backed_file_quota", "backed_files_used",
                "backed_file_slots_remaining", "research_quota_gate_passed",
                "physical_filesystem_capacity_gate_passed",
                "projected_200gb_reserve_gate_passed",
                "research_file_quota_gate_passed",
                "backed_control_tier_gate_passed",
                "research_quota_margin_above_minimum_bytes",
                "research_quota_slack_after_projected_peak_bytes",
                "research_margin_beyond_200gb_reserve_bytes",
                "research_physical_slack_bytes",
            )
        },
        "backup_recovery": {
            key: backup_value[key] for key in (
                "classification_counts", "declared_item_count",
                "copied_file_count", "copied_bytes", "git_bundle_bytes",
                "restored_file_count", "restored_bytes", "backup_verified",
                "restore_test_passed", "credential_material_absent",
                "private_project_or_billing_material_absent",
                "unapproved_bulk_scientific_payload_absent",
                "unresolved_item_count",
            )
        },
        "production_lock": {
            "authority_roles": len(packet_value["authority"]),
            "semantic_gates": len(packet_value["semantic_validation"]),
            "authorization_scope_count": len(scopes),
            "authorization_scopes_granted": sum(bool(item) for item in scopes.values()),
            "launch_envelope_created": True,
            "owner_stage_authorization_granted": launch_value["owner_stage_authorization_granted"],
            "scheduler_submission_performed": launch_value["scheduler_submission_performed"],
        },
        "execution_attestations": dict(pretransfer.EXECUTION_ATTESTATIONS),
        "authorization_scopes_granted": 0,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }
    validate_aggregate_output(value)
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    pretransfer.write_no_clobber(path, value)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--attempt-id", required=True)
    value.add_argument("--governing-commit", required=True)
    for name in (
        "capacity_receipt", "capacity_aggregate", "backup_manifest",
        "backup_aggregate", "restore_receipt", "pretransfer_lock",
        "future_command", "execution_environment", "production_packet",
        "launch_envelope", "output",
    ):
        value.add_argument("--" + name.replace("_", "-"), dest=name, type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = build(args)
        _write(args.output, value)
    except Exception:
        print("PHASE1EF_FINAL_LOCK=FAILED")
        return 78
    print("PHASE1EF_FINAL_LOCK=PASS_CURRENT_COMMIT_ZERO_SCOPE")
    print("AUTHORIZATION_SCOPES_GRANTED=0")
    print("CLOUD_REQUESTS=0")
    print("SCHEDULER_JOBS_SUBMITTED=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
