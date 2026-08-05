#!/usr/bin/env python3
"""Build a restricted, checksum-bound C3 disaster-tier migration witness.

This is a planning-only SCC helper.  It consumes the current read-only storage
detail produced by ``audit_lvef_c3_storage.py`` and, in the conservative
``FULL_MIGRATION_AFTER_BACKUP`` mode, classifies every direct child of the
disaster-recovery root for migration *after* a separately verified backup.
Neither a backup nor a migration is performed or claimed by this script.

Both outputs are restricted artifacts.  The path-level classification must
never be copied into Git or a manuscript export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)


PLANNING_MODE = "FULL_MIGRATION_AFTER_BACKUP"
EXPECTED_DISASTER_ROOT = Path("/restricted/project/mimicecho")


class MigrationWitnessError(ValueError):
    """Raised when the storage inventory cannot support a planning witness."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _require_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MigrationWitnessError(code)
    return value


def _require_nonnegative_integer(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MigrationWitnessError(code)
    return value


def _relative_inventory_path(path: Path, root: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise MigrationWitnessError("INVENTORY_PATH_NOT_SAFE_ABSOLUTE")
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise MigrationWitnessError("INVENTORY_PATH_ESCAPES_DISASTER_ROOT") from exc


def build_artifacts(
    storage_detail: Mapping[str, Any],
    *,
    storage_detail_sha256: str,
    expected_disaster_root: Path = EXPECTED_DISASTER_ROOT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the audit detail and construct deterministic restricted artifacts."""

    if (
        storage_detail.get("schema_version") != 1
        or storage_detail.get("status") != "PASS_READ_ONLY"
        or storage_detail.get("files_moved") != 0
        or storage_detail.get("files_deleted") != 0
    ):
        raise MigrationWitnessError("STORAGE_DETAIL_NOT_READ_ONLY_AUTHORITY")
    if (
        not isinstance(storage_detail_sha256, str)
        or len(storage_detail_sha256) != 64
        or any(character not in "0123456789abcdef" for character in storage_detail_sha256)
    ):
        raise MigrationWitnessError("STORAGE_DETAIL_SHA256_INVALID")

    roots = storage_detail.get("roots")
    if not isinstance(roots, list):
        raise MigrationWitnessError("STORAGE_ROOTS_NOT_LIST")
    disaster_rows = [
        _require_mapping(row, "STORAGE_ROOT_NOT_MAPPING")
        for row in roots
        if isinstance(row, Mapping) and row.get("label") == "disaster_recovery"
    ]
    if len(disaster_rows) != 1:
        raise MigrationWitnessError("EXACTLY_ONE_DISASTER_ROOT_REQUIRED")
    disaster = disaster_rows[0]
    disaster_root = Path(str(disaster.get("path", "")))
    if disaster_root != expected_disaster_root:
        raise MigrationWitnessError("UNEXPECTED_DISASTER_ROOT")
    if disaster.get("exists") is not True:
        raise MigrationWitnessError("DISASTER_ROOT_ABSENT")
    symlinks = disaster.get("symlinks")
    symlink_records = disaster.get("symlink_records", [])
    if not isinstance(symlinks, list) or not isinstance(symlink_records, list):
        raise MigrationWitnessError("DISASTER_SYMLINK_INVENTORY_INVALID")
    symlink_paths = [str(value) for value in symlinks]
    if len(symlink_paths) != len(set(symlink_paths)):
        raise MigrationWitnessError("DUPLICATE_DISASTER_SYMLINK_PATH")
    if len(symlink_records) != len(symlink_paths):
        raise MigrationWitnessError("DISASTER_SYMLINK_RECORD_COVERAGE_INCOMPLETE")
    validated_symlink_records: list[Mapping[str, Any]] = []
    seen_symlink_records: set[str] = set()
    for raw_record in symlink_records:
        record = _require_mapping(raw_record, "DISASTER_SYMLINK_RECORD_NOT_MAPPING")
        link_path = Path(str(record.get("path", "")))
        _relative_inventory_path(link_path, disaster_root)
        resolved_target = Path(str(record.get("resolved_target", "")))
        _relative_inventory_path(resolved_target, disaster_root)
        link_text = link_path.as_posix()
        if link_text in seen_symlink_records:
            raise MigrationWitnessError("DUPLICATE_DISASTER_SYMLINK_RECORD")
        seen_symlink_records.add(link_text)
        if (
            link_text not in symlink_paths
            or record.get("status") != "INTERNAL_EXISTING_SAME_SCOPE"
            or record.get("target_exists") is not True
            or record.get("target_within_disaster_root") is not True
            or record.get("same_top_level_scope") is not True
            or record.get("target_content_followed_or_counted") is not False
        ):
            raise MigrationWitnessError("DISASTER_ROOT_CONTAINS_BLOCKING_SYMLINKS")
        link_relative = link_path.relative_to(disaster_root)
        target_relative = resolved_target.relative_to(disaster_root)
        if (
            not link_relative.parts
            or not target_relative.parts
            or link_relative.parts[0] != target_relative.parts[0]
            or record.get("link_top_level_scope") != link_relative.parts[0]
            or record.get("target_top_level_scope") != target_relative.parts[0]
        ):
            raise MigrationWitnessError("DISASTER_SYMLINK_SCOPE_INCONSISTENT")
        validated_symlink_records.append(record)
    if seen_symlink_records != set(symlink_paths):
        raise MigrationWitnessError("DISASTER_SYMLINK_RECORD_PATH_MISMATCH")
    mount = _require_mapping(disaster.get("mount"), "DISASTER_MOUNT_NOT_MAPPING")
    if mount.get("status") != "PASS" or mount.get("is_bind_mount") is True:
        raise MigrationWitnessError("DISASTER_MOUNT_NOT_VALIDATED_OR_IS_BIND")
    submount_inventory = _require_mapping(
        disaster.get("submount_inventory"), "DISASTER_SUBMOUNT_INVENTORY_NOT_MAPPING"
    )
    if submount_inventory.get("status") != "PASS":
        raise MigrationWitnessError("DISASTER_SUBMOUNT_INVENTORY_NOT_VALIDATED")
    nested_mounts = submount_inventory.get("nested_mounts")
    if not isinstance(nested_mounts, list):
        raise MigrationWitnessError("DISASTER_NESTED_MOUNTS_NOT_LIST")
    if nested_mounts:
        raise MigrationWitnessError("DISASTER_ROOT_CONTAINS_NESTED_MOUNTS")

    inventory = disaster.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        raise MigrationWitnessError("DISASTER_INVENTORY_EMPTY")

    seen_paths: set[Path] = set()
    root_rows: list[tuple[Mapping[str, Any], int]] = []
    direct_rows: list[tuple[Path, Mapping[str, Any], int]] = []
    nested_rows: list[tuple[Path, Mapping[str, Any], int]] = []
    for raw in inventory:
        row = _require_mapping(raw, "INVENTORY_ROW_NOT_MAPPING")
        path = Path(str(row.get("path", "")))
        relative = _relative_inventory_path(path, disaster_root)
        if path in seen_paths:
            raise MigrationWitnessError("DUPLICATE_INVENTORY_PATH")
        seen_paths.add(path)
        size = _require_nonnegative_integer(row.get("size_bytes"), "INVENTORY_SIZE_INVALID")
        if relative == Path("."):
            root_rows.append((row, size))
        elif len(relative.parts) == 1:
            direct_rows.append((relative, row, size))
        else:
            nested_rows.append((relative, row, size))

    if len(root_rows) != 1:
        raise MigrationWitnessError("EXACTLY_ONE_ROOT_INVENTORY_ROW_REQUIRED")
    if not direct_rows:
        raise MigrationWitnessError("DIRECT_CHILD_INVENTORY_EMPTY")
    direct_names = {relative.parts[0] for relative, _, _ in direct_rows}
    if len(direct_names) != len(direct_rows):
        raise MigrationWitnessError("DIRECT_CHILD_INVENTORY_NONUNIQUE")
    if any(relative.parts[0] not in direct_names for relative, _, _ in nested_rows):
        raise MigrationWitnessError("NESTED_ROW_WITHOUT_DIRECT_CHILD_COVERAGE")

    inventory_bytes = root_rows[0][1]
    direct_child_bytes = sum(size for _, _, size in direct_rows)
    if direct_child_bytes > inventory_bytes:
        raise MigrationWitnessError("DIRECT_CHILD_BYTES_EXCEED_ROOT_INVENTORY")
    root_files_or_overhead_bytes = inventory_bytes - direct_child_bytes

    entries: list[dict[str, Any]] = []
    symlink_counts_by_scope: dict[str, int] = {}
    for record in validated_symlink_records:
        scope = str(record["link_top_level_scope"])
        symlink_counts_by_scope[scope] = symlink_counts_by_scope.get(scope, 0) + 1
    for relative, row, size in sorted(direct_rows, key=lambda item: item[0].as_posix()):
        recovery_class = row.get("recovery_class")
        audit_disposition = row.get("migration_disposition")
        if not isinstance(recovery_class, str) or not recovery_class:
            raise MigrationWitnessError("DIRECT_CHILD_RECOVERY_CLASS_MISSING")
        if not isinstance(audit_disposition, str) or not audit_disposition:
            raise MigrationWitnessError("DIRECT_CHILD_AUDIT_DISPOSITION_MISSING")
        entries.append(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": size,
                "audit_recovery_class": recovery_class,
                "audit_migration_disposition": audit_disposition,
                "planning_disposition": "MIGRATE_AFTER_VERIFIED_BACKUP",
                "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
                "migration_status": "NOT_EXECUTED",
                "internal_same_scope_symlink_count": symlink_counts_by_scope.get(
                    relative.parts[0], 0
                ),
                "symlink_handling": (
                    "PRESERVE_LINK_OBJECT_AND_INTERNAL_TARGET_AFTER_VERIFIED_BACKUP"
                    if symlink_counts_by_scope.get(relative.parts[0], 0)
                    else "NO_SYMLINK_IN_SCOPE"
                ),
            }
        )

    classification = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_disaster_tier_path_classification",
        "status": "PASS_COMPLETE_PLANNING_CLASSIFICATION_NOT_EXECUTED",
        "planning_mode": PLANNING_MODE,
        "source_storage_detail_sha256": storage_detail_sha256,
        "disaster_root": disaster_root.as_posix(),
        "disaster_tier_inventory_bytes": inventory_bytes,
        "direct_child_count": len(entries),
        "direct_child_bytes": direct_child_bytes,
        "root_files_or_overhead_bytes": root_files_or_overhead_bytes,
        "nested_inventory_row_count": len(nested_rows),
        "nested_mount_count": 0,
        "symlink_count": len(validated_symlink_records),
        "symlink_scope_count": len(symlink_counts_by_scope),
        "all_symlinks_internal_existing_same_scope": True,
        "symlink_target_content_followed_or_counted": False,
        "complete_classified_direct_child_coverage": True,
        "classified_migration_bytes": inventory_bytes,
        "classified_retained_bytes": 0,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "entries": entries,
        "root_files_or_overhead": {
            "relative_scope": "ROOT_FILES_OR_FILESYSTEM_OVERHEAD_NOT_IN_DIRECT_CHILDREN",
            "size_bytes": root_files_or_overhead_bytes,
            "planning_disposition": "MIGRATE_AFTER_VERIFIED_BACKUP",
            "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
            "migration_status": "NOT_EXECUTED",
        },
    }
    classification_payload = _json_bytes(classification)
    classification_sha256 = sha256_bytes(classification_payload)

    witness = {
        "schema_version": 1,
        "witness_type": "lvef_c3_migration_witness_v1",
        "status": "PASS_CLASSIFIED_MIGRATION_WITNESS",
        "planning_mode": PLANNING_MODE,
        "classification_complete": True,
        "migration_state": "PLANNED_NOT_EXECUTED",
        "disaster_tier_inventory_bytes": inventory_bytes,
        "classified_migration_bytes": inventory_bytes,
        "classified_retained_bytes": 0,
        "symlink_count": len(validated_symlink_records),
        "symlink_scope_count": len(symlink_counts_by_scope),
        "nested_mount_count": 0,
        "all_symlinks_internal_existing_same_scope": True,
        "symlink_target_content_followed_or_counted": False,
        "inventory_sha256": storage_detail_sha256,
        "classification_sha256": classification_sha256,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "full_c3_authorized": False,
    }
    return classification, witness


def _write_or_validate(path: Path, payload: bytes) -> str:
    """Create a mode-600 artifact or validate an identical prior artifact."""

    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise MigrationWitnessError("EXISTING_OUTPUT_NOT_REGULAR_FILE")
        if path.read_bytes() != payload:
            raise MigrationWitnessError("EXISTING_OUTPUT_DIFFERS_FROM_DETERMINISTIC_ARTIFACT")
        if sha256_file(path) != sha256_bytes(payload):
            raise MigrationWitnessError("EXISTING_OUTPUT_HASH_CHANGED")
        return "VALIDATED_EXISTING"
    try:
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise MigrationWitnessError("OUTPUT_APPEARED_DURING_CREATION") from exc
    if sha256_file(path) != sha256_bytes(payload):
        raise MigrationWitnessError("OUTPUT_HASH_CHANGED_DURING_CREATION")
    return "CREATED"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-detail", type=Path, required=True)
    parser.add_argument("--classification-output", type=Path, required=True)
    parser.add_argument("--witness-output", type=Path, required=True)
    parser.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    parser.add_argument("--planning-mode", choices=(PLANNING_MODE,), default=PLANNING_MODE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
        storage_detail_path = bind_approved_restricted_path(
            args.storage_detail,
            policy=safe_policy,
            must_exist=True,
            expect="file",
            root_kind="direct",
        )
        classification_output = bind_approved_restricted_path(
            args.classification_output,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="direct",
            create=True,
        )
        witness_output = bind_approved_restricted_path(
            args.witness_output,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="direct",
            create=True,
        )
        if classification_output == witness_output:
            raise MigrationWitnessError("OUTPUT_PATHS_MUST_DIFFER")
        storage_bytes = storage_detail_path.read_bytes()
        detail = json.loads(storage_bytes.decode("utf-8"))
        detail_mapping = _require_mapping(detail, "STORAGE_DETAIL_NOT_MAPPING")
        classification, witness = build_artifacts(
            detail_mapping,
            storage_detail_sha256=sha256_bytes(storage_bytes),
        )
        classification_state = _write_or_validate(
            classification_output, _json_bytes(classification)
        )
        witness_state = _write_or_validate(witness_output, _json_bytes(witness))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": witness["status"],
                "planning_mode": witness["planning_mode"],
                "migration_state": witness["migration_state"],
                "classification_complete": witness["classification_complete"],
                "direct_child_count": classification["direct_child_count"],
                "disaster_tier_inventory_bytes": witness["disaster_tier_inventory_bytes"],
                "classified_migration_bytes": witness["classified_migration_bytes"],
                "classified_retained_bytes": witness["classified_retained_bytes"],
                "backup_verified": witness["backup_verified"],
                "migration_executed": witness["migration_executed"],
                "classification_output_state": classification_state,
                "witness_output_state": witness_state,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
