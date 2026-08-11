from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: these tests never execute the SCC capture or network.
import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as capacity
import lvef_multitask_analysis_modes as analysis_modes


def _native() -> bytes:
    return (
        "rproject_mimicecho mimicecho FILESET 10690224 52428800 0 0 none | "
        "47379 1638400 0 0 none\n"
        "rprojectnb_mimicecho mimicecho FILESET 147117696 2044723200 0 0 none | "
        "106407 33554432 0 0 none\n"
    ).encode()


def _aggregate() -> dict[str, object]:
    rq = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    bq = capacity.EXPECTED_BACKED_QUOTA_KIB * 1024
    value: dict[str, object] = {key: False for key in capacity.AGGREGATE_KEYS}
    value.update(
        {
            "schema_version": 1,
            "artifact_type": capacity.AGGREGATE_TYPE,
            "status": capacity.AGGREGATE_STATUS,
            "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
            "governing_commit": "a" * 40,
            "created_at_utc": "2026-08-11T12:00:00+00:00",
            "units": "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER",
            "quota_display_unit_ruling": "BINARY_GIB_ROUNDED",
            "restricted_receipt_size_bytes": 10,
            "restricted_receipt_sha256": "b" * 64,
            "pquota_executable_sha256": capacity.EXPECTED_PQUOTA_SHA256,
            "research_quota_bytes": rq,
            "backed_quota_bytes": bq,
            "research_quota_margin_above_minimum_bytes": rq - capacity.MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "research_quota_slack_after_projected_peak_bytes": rq - capacity.PROJECTED_PEAK_BYTES,
            "research_margin_beyond_200gb_reserve_bytes": rq - capacity.PROJECTED_PEAK_BYTES - capacity.REQUIRED_FREE_HEADROOM_BYTES,
            "pretransfer_research_write_bound_bytes": capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_remaining_write_bytes": capacity.PROJECTED_PEAK_BYTES,
            "research_filesystem_available_bytes": capacity.PROJECTED_PEAK_BYTES
            + capacity.REQUIRED_FREE_HEADROOM_BYTES
            + capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_physical_required_available_bytes": capacity.PROJECTED_PEAK_BYTES
            + capacity.REQUIRED_FREE_HEADROOM_BYTES
            + capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_physical_slack_bytes": 0,
        }
    )
    for key in (
        "prior_authorities_hash_verified", "prior_authorities_closed_schema_verified",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_display_fileset_mapping_verified",
        "pquota_current_not_snapshot_mode_verified",
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed", "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed", "backed_control_tier_gate_passed",
        "purchased_saas_allocation_remains_on_research",
        "snapshot_capacity_double_counting_avoided",
    ):
        value[key] = True
    for key in (
        "cloud_requests", "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "files_moved", "files_deleted",
    ):
        value[key] = 0
    value["control_write_binding_evaluated_by_capacity_receipt"] = False
    value["additional_project_quota_row_for_same_principal_observed"] = False
    value["snapshot_presence_independently_enumerated"] = False
    value["snapshot_accounting_ruling"] = (
        "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
    )
    return value


def _expect(code: str, function) -> None:
    try:
        function()
    except capacity.PostReallocationCapacityError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected {code}")


def test_native_kib_rows_produce_exact_binary_scaled_bytes_and_file_counts() -> None:
    rows = capacity._parse_native_quota(_native())
    assert rows["research"]["quota_kib"] * 1024 == 2_093_796_556_800
    assert rows["backed"]["quota_kib"] * 1024 == 53_687_091_200
    assert rows["research"]["files_used"] == 106_407
    assert rows["backed"]["file_quota"] == 1_638_400


def test_native_quota_rejects_duplicate_missing_or_changed_allocation() -> None:
    _expect("NATIVE_QUOTA_ROW_NOT_UNIQUE", lambda: capacity._parse_native_quota(_native() + _native().splitlines()[0] + b"\n"))
    _expect("NATIVE_QUOTA_ROWS_MISSING", lambda: capacity._parse_native_quota(_native().splitlines()[0] + b"\n"))
    _expect("NATIVE_QUOTA_ALLOCATION_UNEXPECTED", lambda: capacity._parse_native_quota(_native().replace(b"2044723200", b"2044723199")))
    extra = b"snapshot_mimicecho mimicecho FILESET 0 1 0 0 none | 0 1 0 0 none\n"
    _expect("NATIVE_QUOTA_ADDITIONAL_PRINCIPAL_ROW", lambda: capacity._parse_native_quota(_native() + extra))


def test_pquota_display_is_binary_gib_rounded_not_byte_authority() -> None:
    rows = capacity._parse_native_quota(_native())
    text = (
        "Quota Quota Usage Usage\nProject Space (GB) (files) (GB) (files)\n"
        "----- ----- ----- ----- -----\n"
        "/project/mimicecho 50 1638400 10.19 47379\n"
        "/projectnb/mimicecho 1950 33554432 140.30 106407\n"
    )
    capacity._parse_pquota(text, rows)
    _expect("PQUOTA_DISPLAY_NATIVE_MISMATCH", lambda: capacity._parse_pquota(text.replace("140.30", "140.29"), rows))


def test_findmnt_and_df_reject_aliasing_bind_and_byte_mismatch() -> None:
    text = json.dumps({"filesystems": [{"source": "/dev/synthetic-a", "target": "/restricted/projectnb", "fstype": "gpfs", "options": "rw", "fsroot": "/"}]})
    mount = capacity._parse_findmnt(text, Path("/restricted/projectnb/mimicecho"))
    assert mount["bind"] is False
    assert capacity._parse_df(
        "Filesystem 1B-blocks Used Avail Mounted on\n/dev/synthetic-a 2200000000000 100000000000 2100000000000 /restricted/projectnb\n",
        mount,
    )["available"] == 2_100_000_000_000
    bind = capacity._parse_findmnt(text.replace('"rw"', '"rw,bind"'), Path("/restricted/projectnb/mimicecho"))
    assert bind["bind"] is True
    subtree = capacity._parse_findmnt(text.replace('"fsroot": "/"', '"fsroot": "/subtree"'), Path("/restricted/projectnb/mimicecho"))
    assert subtree["bind"] is True
    _expect("DF_MOUNT_IDENTITY_MISMATCH", lambda: capacity._parse_df("Filesystem 1B-blocks Used Avail Mounted on\n/dev/other 2 1 1 /restricted/projectnb\n", mount))


def test_pquota_restricted_mount_reconciliation_is_not_a_hardcoded_boolean() -> None:
    native = capacity._parse_native_quota(_native())
    paths = {
        role: {
            "resolved_path_sha256": capacity._sha(str(path).encode()),
            "is_symlink": False,
        }
        for role, path in capacity.EXPECTED_RESTRICTED_PATHS.items()
    }
    mounts = {
        "backed": {"source": "host:/gpfs4/rproject", "target": "/restricted/project", "fsroot": "/", "bind": False},
        "research": {"source": "host:/gpfs4/rprojectnb", "target": "/restricted/projectnb", "fsroot": "/", "bind": False},
    }
    capacity._validate_pquota_restricted_mount_reconciliation(
        native=native, paths=paths, mounts=mounts
    )
    changed = copy.deepcopy(mounts)
    changed["research"]["fsroot"] = "/subtree"
    _expect(
        "PQUOTA_RESTRICTED_MOUNT_RECONCILIATION_FAILED",
        lambda: capacity._validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=changed
        ),
    )


def test_capacity_aggregate_is_closed_and_arithmetic_fail_closed() -> None:
    value = _aggregate()
    capacity.validate_aggregate_output(value)
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode(),
        filename="lvef_c3_post_reallocation_capacity.summary.json",
        profile_name="phase1ef_post_reallocation_capacity_json",
        policy=policy,
    )
    assert result["status"] == "PASS"
    changed = copy.deepcopy(value)
    changed["unexpected"] = False
    _expect("CAPACITY_AGGREGATE_SCHEMA_NOT_CLOSED", lambda: capacity.validate_aggregate_output(changed))
    changed = copy.deepcopy(value)
    changed["research_margin_beyond_200gb_reserve_bytes"] += 1
    _expect("CAPACITY_AGGREGATE_ARITHMETIC_INVALID", lambda: capacity.validate_aggregate_output(changed))


def test_capture_contract_has_no_storage_inventory_cloud_or_scheduler_path() -> None:
    source = (ROOT / "scripts" / "capture_lvef_c3_post_reallocation_capacity.py").read_text()
    wrapper = (ROOT / "scripts" / "scc_capture_lvef_c3_post_reallocation_capacity.sh").read_text()
    assert "objects.list" not in source
    assert "alt=media" not in source
    assert "qsub" not in source
    assert " du " not in wrapper
    assert "find " not in wrapper
    assert "pquota" in source and "findmnt" in source and "df" in source
