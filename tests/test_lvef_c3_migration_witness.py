from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: all paths and byte counts in this file are fixtures.

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_migration_witness as builder
import plan_lvef_c3_resources as planner


SYNTHETIC_ROOT = Path("/synthetic/restricted/project/mimicecho")


def _detail(*, root_bytes: int = 1_024, direct_bytes: tuple[int, int] = (600, 400)) -> dict:
    root = SYNTHETIC_ROOT
    return {
        "schema_version": 1,
        "status": "PASS_READ_ONLY",
        "roots": [
            {
                "label": "disaster_recovery",
                "path": str(root),
                "exists": True,
                "device": 11,
                "mount": {
                    "status": "PASS",
                    "source": "synthetic-device",
                    "target": "/synthetic/restricted/project",
                    "fstype": "syntheticfs",
                    "options": "rw",
                    "is_bind_mount": False,
                },
                "statvfs": {
                    "capacity_bytes": 2_000,
                    "available_bytes": 976,
                    "free_bytes": 976,
                },
                "symlinks": [],
                "inventory": [
                    {
                        "path": str(root / "code" / "repo"),
                        "size_bytes": 300,
                        "recovery_class": "git_recoverable",
                        "migration_disposition": "safe_to_migrate_after_clean_git_verification",
                    },
                    {
                        "path": str(root),
                        "size_bytes": root_bytes,
                        "recovery_class": "restricted_irreplaceability_unresolved",
                        "migration_disposition": "requires_approved_backup_copy_or_owner_adjudication",
                    },
                    {
                        "path": str(root / "outputs"),
                        "size_bytes": direct_bytes[1],
                        "recovery_class": "restricted_deterministically_regenerable",
                        "migration_disposition": "safe_to_migrate_after_checksum_copy",
                    },
                    {
                        "path": str(root / "code"),
                        "size_bytes": direct_bytes[0],
                        "recovery_class": "git_recoverable",
                        "migration_disposition": "safe_to_migrate_after_clean_git_verification",
                    },
                ],
            },
            {
                "label": "research",
                "path": "/synthetic/restricted/projectnb/mimicecho",
                "exists": True,
                "device": 12,
                "mount": {"status": "PASS", "is_bind_mount": False},
                "statvfs": {},
                "symlinks": [],
                "inventory": [],
            },
        ],
        "scheduler_paths": [],
        "quota_command": {},
        "administrative_questions": {},
        "files_moved": 0,
        "files_deleted": 0,
    }


def _build(detail: dict | None = None) -> tuple[dict, dict]:
    return builder.build_artifacts(
        detail or _detail(),
        storage_detail_sha256="a" * 64,
        expected_disaster_root=SYNTHETIC_ROOT,
    )


def test_full_migration_witness_reconciles_and_does_not_claim_execution() -> None:
    classification, witness = _build()
    assert classification["complete_classified_direct_child_coverage"] is True
    assert classification["direct_child_count"] == 2
    assert classification["direct_child_bytes"] == 1_000
    assert classification["root_files_or_overhead_bytes"] == 24
    assert classification["backup_verified"] is False
    assert classification["migration_executed"] is False
    assert witness["status"] == "PASS_CLASSIFIED_MIGRATION_WITNESS"
    assert witness["migration_state"] == "PLANNED_NOT_EXECUTED"
    assert witness["disaster_tier_inventory_bytes"] == 1_024
    assert witness["classified_migration_bytes"] == 1_024
    assert witness["classified_retained_bytes"] == 0
    assert witness["backup_verified"] is False
    assert witness["migration_executed"] is False
    assert witness["full_c3_authorized"] is False
    assert witness["classification_sha256"] == builder.sha256_bytes(
        builder._json_bytes(classification)
    )


def test_witness_is_accepted_by_resource_planner_loader() -> None:
    _, witness = _build()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "witness.json"
        path.write_bytes(builder._json_bytes(witness))
        loaded = planner.load_migration_witness(path)
    assert loaded["classified_migration_bytes"] == 1_024
    assert loaded["classified_retained_bytes"] == 0


def test_nested_inventory_requires_a_classified_direct_ancestor() -> None:
    detail = _detail()
    inventory = detail["roots"][0]["inventory"]
    inventory[:] = [row for row in inventory if row["path"] != str(SYNTHETIC_ROOT / "code")]
    try:
        _build(detail)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "NESTED_ROW_WITHOUT_DIRECT_CHILD_COVERAGE"
    else:
        raise AssertionError("Nested inventory without direct-child coverage must fail closed")


def test_direct_child_bytes_cannot_exceed_root_inventory() -> None:
    try:
        _build(_detail(root_bytes=999))
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DIRECT_CHILD_BYTES_EXCEED_ROOT_INVENTORY"
    else:
        raise AssertionError("Overlapping or inconsistent direct-child bytes must fail closed")


def test_symlink_or_bind_mount_inventory_fails_closed() -> None:
    symlinked = _detail()
    symlinked["roots"][0]["symlinks"] = [str(SYNTHETIC_ROOT / "link")]
    try:
        _build(symlinked)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_ROOT_CONTAINS_SYMLINKS"
    else:
        raise AssertionError("A symlink-bearing inventory must fail closed")

    bound = _detail()
    bound["roots"][0]["mount"]["is_bind_mount"] = True
    try:
        _build(bound)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_MOUNT_NOT_VALIDATED_OR_IS_BIND"
    else:
        raise AssertionError("A bind-mounted root must fail closed")


def test_write_or_validate_is_deterministic_and_never_overwrites() -> None:
    payload = json.dumps({"status": "synthetic"}, sort_keys=True).encode("utf-8")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "artifact.json"
        assert builder._write_or_validate(path, payload) == "CREATED"
        assert builder._write_or_validate(path, payload) == "VALIDATED_EXISTING"
        try:
            builder._write_or_validate(path, b"different")
        except builder.MigrationWitnessError as exc:
            assert str(exc) == "EXISTING_OUTPUT_DIFFERS_FROM_DETERMINISTIC_ARTIFACT"
        else:
            raise AssertionError("A different existing artifact must not be overwritten")
