from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: all paths and byte counts in this file are fixtures.

import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_migration_witness as builder
import audit_lvef_c3_storage as storage_audit
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
                "submount_inventory": {"status": "PASS", "nested_mounts": []},
                "statvfs": {
                    "capacity_bytes": 2_000,
                    "available_bytes": 976,
                    "free_bytes": 976,
                },
                "symlinks": [],
                "symlink_records": [],
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
                "submount_inventory": {"status": "PASS", "nested_mounts": []},
                "statvfs": {},
                "symlinks": [],
                "symlink_records": [],
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


def _same_scope_symlink_record() -> dict:
    link = SYNTHETIC_ROOT / "outputs" / "latest"
    target = SYNTHETIC_ROOT / "outputs" / "run_001"
    return {
        "path": str(link),
        "raw_target": str(target),
        "resolved_target": str(target),
        "target_exists": True,
        "target_within_disaster_root": True,
        "link_top_level_scope": "outputs",
        "target_top_level_scope": "outputs",
        "same_top_level_scope": True,
        "status": "INTERNAL_EXISTING_SAME_SCOPE",
        "target_content_followed_or_counted": False,
    }


def test_internal_existing_same_scope_symlink_is_planned_without_following() -> None:
    symlinked = _detail()
    record = _same_scope_symlink_record()
    symlinked["roots"][0]["symlinks"] = [record["path"]]
    symlinked["roots"][0]["symlink_records"] = [record]
    classification, witness = _build(symlinked)
    assert classification["symlink_count"] == 1
    assert classification["all_symlinks_internal_existing_same_scope"] is True
    assert classification["symlink_target_content_followed_or_counted"] is False
    output_entry = next(
        row for row in classification["entries"] if row["relative_path"] == "outputs"
    )
    assert output_entry["internal_same_scope_symlink_count"] == 1
    assert witness["symlink_count"] == 1
    assert witness["migration_state"] == "PLANNED_NOT_EXECUTED"


def test_uncovered_or_blocking_symlink_inventory_fails_closed() -> None:
    uncovered = _detail()
    record = _same_scope_symlink_record()
    uncovered["roots"][0]["symlinks"] = [record["path"]]
    try:
        _build(uncovered)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_SYMLINK_RECORD_COVERAGE_INCOMPLETE"
    else:
        raise AssertionError("An uncovered symlink must fail closed")

    outside = _detail()
    record = _same_scope_symlink_record()
    record.update(
        {
            "resolved_target": "/synthetic/outside",
            "target_within_disaster_root": False,
            "target_top_level_scope": None,
            "same_top_level_scope": False,
            "status": "OUTSIDE_DISASTER_ROOT",
        }
    )
    outside["roots"][0]["symlinks"] = [record["path"]]
    outside["roots"][0]["symlink_records"] = [record]
    try:
        _build(outside)
    except builder.MigrationWitnessError as exc:
        assert str(exc) in {
            "INVENTORY_PATH_ESCAPES_DISASTER_ROOT",
            "DISASTER_ROOT_CONTAINS_BLOCKING_SYMLINKS",
        }
    else:
        raise AssertionError("An external symlink target must fail closed")


def test_bind_mount_inventory_fails_closed() -> None:

    bound = _detail()
    bound["roots"][0]["mount"]["is_bind_mount"] = True
    try:
        _build(bound)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_MOUNT_NOT_VALIDATED_OR_IS_BIND"
    else:
        raise AssertionError("A bind-mounted root must fail closed")


def test_storage_audit_classifies_internal_same_scope_symlink_without_following() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "outputs" / "run_001"
        target.mkdir(parents=True)
        (target / "payload.txt").write_text("synthetic\n", encoding="utf-8")
        link = root / "outputs" / "latest"
        os.symlink(target, link)
        rows = storage_audit._symlink_records(root)
    assert len(rows) == 1
    assert rows[0]["status"] == "INTERNAL_EXISTING_SAME_SCOPE"
    assert rows[0]["target_exists"] is True
    assert rows[0]["target_within_disaster_root"] is True
    assert rows[0]["same_top_level_scope"] is True
    assert rows[0]["target_content_followed_or_counted"] is False


def test_storage_audit_marks_external_and_dangling_symlinks_blocking() -> None:
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
        root = Path(directory)
        (root / "outputs").mkdir()
        external = Path(outside) / "target"
        external.mkdir()
        os.symlink(external, root / "outputs" / "external")
        os.symlink(root / "outputs" / "missing", root / "outputs" / "dangling")
        rows = storage_audit._symlink_records(root)
    assert {row["status"] for row in rows} == {
        "DANGLING_OR_UNRESOLVABLE",
        "OUTSIDE_DISASTER_ROOT",
    }
    assert all(row["target_content_followed_or_counted"] is False for row in rows)


def test_storage_audit_finds_symlinks_deeper_than_byte_inventory_depth() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "outputs" / "one" / "two" / "three" / "four" / "run"
        target.mkdir(parents=True)
        link = target.parent / "latest"
        os.symlink(target, link)
        rows = storage_audit._symlink_records(root)
    assert [row["path"] for row in rows] == [str(link)]
    assert rows[0]["status"] == "INTERNAL_EXISTING_SAME_SCOPE"


def test_nested_mount_or_unvalidated_mount_inventory_fails_closed() -> None:
    nested = _detail()
    nested["roots"][0]["submount_inventory"] = {
        "status": "PASS",
        "nested_mounts": [
            {
                "target": str(SYNTHETIC_ROOT / "outputs" / "nested"),
                "source": "synthetic",
                "fstype": "syntheticfs",
                "options": "rw,bind",
                "is_bind_mount": True,
            }
        ],
    }
    try:
        _build(nested)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_ROOT_CONTAINS_NESTED_MOUNTS"
    else:
        raise AssertionError("A nested mount must fail closed")

    unavailable = _detail()
    unavailable["roots"][0]["submount_inventory"] = {"status": "UNPARSEABLE"}
    try:
        _build(unavailable)
    except builder.MigrationWitnessError as exc:
        assert str(exc) == "DISASTER_SUBMOUNT_INVENTORY_NOT_VALIDATED"
    else:
        raise AssertionError("An unvalidated submount inventory must fail closed")


def test_recursive_findmnt_inventory_detects_nested_mount_without_paths_in_safe_layer() -> None:
    original_run = storage_audit._run
    storage_audit._run = lambda command: {
        "returncode": 0,
        "stdout": json.dumps(
            {
                "filesystems": [
                    {
                        "target": "/synthetic/restricted/project",
                        "source": "synthetic-root",
                        "fstype": "syntheticfs",
                        "options": "rw",
                        "children": [
                            {
                                "target": str(SYNTHETIC_ROOT / "outputs" / "nested"),
                                "source": "synthetic-bind",
                                "fstype": "syntheticfs",
                                "options": "rw,bind",
                            }
                        ],
                    }
                ]
            }
        ),
        "stderr_sha256": "a" * 64,
    }
    try:
        result = storage_audit._findmnt_submounts(SYNTHETIC_ROOT)
    finally:
        storage_audit._run = original_run
    assert result["status"] == "PASS"
    assert len(result["nested_mounts"]) == 1
    assert result["nested_mounts"][0]["is_bind_mount"] is True


def test_recursive_findmnt_unparseable_tree_fails_closed() -> None:
    original_run = storage_audit._run
    storage_audit._run = lambda command: {
        "returncode": 0,
        "stdout": '{"filesystems": [{"target": 7}]}',
        "stderr_sha256": "a" * 64,
    }
    try:
        result = storage_audit._findmnt_submounts(SYNTHETIC_ROOT)
    finally:
        storage_audit._run = original_run
    assert result == {"status": "UNPARSEABLE"}


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
