from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: every path, identity, quota, and byte value is a fixture.

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_live_quota as quota


NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
RESEARCH_PATH = "/synthetic/research"
MOUNT_TARGET = "/synthetic"
PRINCIPAL = "synthetic_project"
EXACT_DU_USAGE = 140_000_000_123
FILESYSTEM_CAPACITY = 3_000_000_000_000
FILESYSTEM_USED = 900_000_000_000
FILESYSTEM_AVAILABLE = 2_000_000_000_000
FILESYSTEM_RESERVED = 100_000_000_000


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _classification() -> dict:
    inventory = 10_954_752_000
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_disaster_tier_path_classification",
        "status": "PASS_COMPLETE_PLANNING_CLASSIFICATION_NOT_EXECUTED",
        "planning_mode": "FULL_MIGRATION_AFTER_BACKUP",
        "source_storage_detail_sha256": "b" * 64,
        "disaster_root": "/synthetic/disaster",
        "disaster_tier_inventory_bytes": inventory,
        "direct_child_count": 1,
        "direct_child_bytes": inventory,
        "root_files_or_overhead_bytes": 0,
        "nested_inventory_row_count": 1,
        "nested_mount_count": 0,
        "symlink_count": 0,
        "symlink_scope_count": 0,
        "blocking_symlink_count": 0,
        "retained_symlink_scope_count": 0,
        "all_symlinks_internal_existing_same_scope": True,
        "symlink_target_content_followed_or_counted": False,
        "complete_classified_direct_child_coverage": True,
        "classified_migration_bytes": inventory,
        "classified_retained_bytes": 0,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "entries": [
            {
                "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
                "migration_status": "NOT_EXECUTED",
            }
        ],
        "root_files_or_overhead": {
            "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
            "migration_status": "NOT_EXECUTED",
        },
    }


def _witness(classification_sha256: str) -> dict:
    inventory = 10_954_752_000
    return {
        "schema_version": 1,
        "witness_type": "lvef_c3_migration_witness_v1",
        "status": "PASS_CLASSIFIED_MIGRATION_WITNESS",
        "planning_mode": "FULL_MIGRATION_AFTER_BACKUP",
        "classification_complete": True,
        "migration_state": quota.MIGRATION_PLANNED,
        "disaster_tier_inventory_bytes": inventory,
        "classified_migration_bytes": inventory,
        "classified_retained_bytes": 0,
        "symlink_count": 0,
        "symlink_scope_count": 0,
        "blocking_symlink_count": 0,
        "retained_symlink_scope_count": 0,
        "nested_mount_count": 0,
        "all_symlinks_internal_existing_same_scope": True,
        "full_migration_path_classification_supported": True,
        "symlink_target_content_followed_or_counted": False,
        "inventory_sha256": "b" * 64,
        "classification_sha256": classification_sha256,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "full_c3_authorized": False,
    }


def _raw_spec(path: Path) -> dict:
    payload = path.read_bytes()
    return {"path": str(path), "byte_count": len(payload), "sha256": _digest(payload)}


def _tool_record(root: Path, role: str, argv_tail: list[str], stdout: bytes) -> dict:
    executable = root / "tools" / role
    executable.parent.mkdir(exist_ok=True)
    executable.write_bytes(("synthetic executable " + role).encode("ascii"))
    executable.chmod(0o755)
    stdout_path = root / f"{role}.stdout"
    stderr_path = root / f"{role}.stderr"
    _write_private(stdout_path, stdout)
    _write_private(stderr_path, b"")
    argv = [str(executable), *argv_tail]
    metadata = executable.stat()
    return {
        "tool_name": role,
        "resolved_executable_path": str(executable),
        "executable_sha256": _digest(executable.read_bytes()),
        "executable_size_bytes": metadata.st_size,
        "executable_device": metadata.st_dev,
        "executable_inode": metadata.st_ino,
        "argv": argv,
        "argv_sha256": _digest(_canonical(argv)),
        "exit_status": 0,
        "stdout": _raw_spec(stdout_path),
        "stderr": _raw_spec(stderr_path),
    }


def _bundle(root: Path, *, quota_gb: str = "2000") -> tuple[Path, dict]:
    root.mkdir(parents=True, exist_ok=True)
    classification_path = root / "classification.json"
    witness_path = root / "witness.json"
    classification = _classification()
    classification_payload = _json_bytes(classification)
    _write_private(classification_path, classification_payload)
    witness = _witness(_digest(classification_payload))
    witness_payload = _json_bytes(witness)
    _write_private(witness_path, witness_payload)

    findmnt_payload = _json_bytes(
        {
            "filesystems": [
                {
                    "source": "syntheticfs",
                    "target": MOUNT_TARGET,
                    "fstype": "gpfs",
                    "options": "rw,synthetic",
                }
            ]
        }
    )
    filesystem_authority = _digest(
        _canonical(
            {
                "source": "syntheticfs",
                "target": MOUNT_TARGET,
                "fstype": "gpfs",
            }
        )
    )
    pquota_payload = (
        "                              quota     quota     usage     usage\n"
        "project space                 (GB)      (files)   (GB)      (files)\n"
        "----------------------------- --------- --------- --------- ---------\n"
        f"/rproject/{PRINCIPAL}          11        25000        10.19     20123\n"
        "synthetic_owner                10.19     20122\n"
        f"/rprojectnb/{PRINCIPAL}        {quota_gb}      500000       140.04    335984\n"
        "synthetic_owner                140.04    335983\n"
    ).encode("utf-8")
    df_payload = (
        "Filesystem 1B-blocks Used Avail Mounted on\n"
        f"syntheticfs {FILESYSTEM_CAPACITY} {FILESYSTEM_USED} "
        f"{FILESYSTEM_AVAILABLE} {MOUNT_TARGET}\n"
    ).encode("utf-8")
    du_payload = f"{EXACT_DU_USAGE}\t{RESEARCH_PATH}\n".encode("utf-8")

    commands = {
        "pquota": _tool_record(root, "pquota", ["-u", PRINCIPAL], pquota_payload),
        "findmnt": _tool_record(
            root,
            "findmnt",
            [
                "--json",
                "--target",
                RESEARCH_PATH,
                "--output",
                "SOURCE,TARGET,FSTYPE,OPTIONS",
            ],
            findmnt_payload,
        ),
        "df": _tool_record(
            root,
            "df",
            ["-B1", "--output=source,size,used,avail,target", RESEARCH_PATH],
            df_payload,
        ),
        "du": _tool_record(
            root, "du", ["-x", "-s", "-B1", RESEARCH_PATH], du_payload
        ),
    }
    quota_bytes = int(Decimal(quota_gb) * quota.DECIMAL_GB_BYTES)
    receipt = {
        "schema_version": quota.SCHEMA_VERSION,
        "receipt_kind": quota.RECEIPT_KIND,
        "status": quota.RECEIPT_STATUS,
        "captured_at_utc": "2026-08-10T11:30:00Z",
        "capture_identity": {
            "effective_uid": os.getuid(),
            "effective_username_sha256": _digest(
                pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")
            ),
            "hostname_sha256": _digest(socket.gethostname().encode("utf-8")),
            "git_commit": COMMIT,
        },
        "unit_authority": {
            "quota_allocation_unit_system": "DECIMAL_SI",
            "decimal_gb_bytes": quota.DECIMAL_GB_BYTES,
            "decimal_tb_bytes": quota.DECIMAL_TB_BYTES,
            "pquota_allocation_display_is_exact": True,
            "pquota_usage_display_may_be_rounded": True,
            "pquota_usage_used_as_exact": False,
            "exact_project_usage_source": "DU_X_S_B1_ALLOCATED_BYTES",
        },
        "research_path": RESEARCH_PATH,
        "commands": commands,
        "pquota_research_mapping": {
            "quota_principal": PRINCIPAL,
            "research_filesystem_row": f"/rprojectnb/{PRINCIPAL}",
            "research_row_role": "RESEARCH_NOT_BACKED_UP",
            "quota_display_value": quota_gb,
            "quota_display_unit": "GB",
            "quota_files_display_value": "500000",
            "usage_display_value": "140.04",
            "usage_display_unit": "GB",
            "usage_files_display_value": "335984",
            "file_count_columns_used_for_bytes": False,
            "quota_bytes": quota_bytes,
            "usage_display_is_rounded": True,
            "usage_display_used_as_exact": False,
            "mapped_filesystem_authority_sha256": filesystem_authority,
        },
        "resource_plan": {
            "selected_source_bytes": quota.SELECTED_SOURCE_BYTES,
            "frozen_projected_peak_bytes": quota.FROZEN_PROJECTED_PEAK_BYTES,
            "required_headroom_bytes": quota.REQUIRED_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": quota.MINIMUM_EFFECTIVE_QUOTA_BYTES,
        },
        "migration_authority": {
            "migration_state": quota.MIGRATION_PLANNED,
            "migration_witness_path": str(witness_path),
            "migration_witness_sha256": _digest(witness_payload),
            "migration_classification_path": str(classification_path),
            "migration_classification_sha256": _digest(classification_payload),
            "migration_completion_verified": False,
            "backup_verified": False,
        },
        "no_mutation_attestations": {
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
            "cloud_requests": 0,
            "object_bodies_downloaded": 0,
        },
    }
    receipt_path = root / "live_quota_raw_receipt.json"
    _write_private(receipt_path, _json_bytes(receipt))
    return receipt_path, receipt


def _rewrite_receipt(path: Path, receipt: dict) -> None:
    _write_private(path, _json_bytes(receipt))


def _rewrite_raw(receipt: dict, role: str, stream: str, payload: bytes) -> None:
    spec = receipt["commands"][role][stream]
    path = Path(spec["path"])
    _write_private(path, payload)
    spec.update({"byte_count": len(payload), "sha256": _digest(payload)})


def _error(path: Path) -> str:
    try:
        quota.validate_restricted_evidence(path, expected_commit=COMMIT, now_utc=NOW)
    except quota.LiveQuotaError as exc:
        return str(exc)
    raise AssertionError("Evidence unexpectedly passed")


def test_closed_v2_passes_with_2000_decimal_gb_and_exact_du_usage() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory), quota_gb="2000")
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        assert result["status"] == "PASS_LIVE_QUOTA_GATE"
        assert result["quota_bytes"] == 2_000_000_000_000
        assert result["exact_project_usage_bytes"] == EXACT_DU_USAGE
        assert result["pquota_usage_used_as_exact"] is False
        assert result["migration_state"] == "PLANNED_NOT_EXECUTED"
        assert result["migration_completion_authority_passed"] is False
        assert result["backup_authority_passed"] is False
        assert result["full_c3_authorized"] is False


def test_observed_989_decimal_gb_fails_safely_and_source_exceeds_quota() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory), quota_gb="989")
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        assert result["quota_bytes"] == 989_000_000_000
        assert result["selected_source_bytes"] == 1_216_569_133_322
        assert result["selected_source_exceeds_quota"] is True
        assert result["source_fit_gate_passed"] is False
        assert result["project_quota_gate_passed"] is False
        assert result["minimum_effective_quota_gate_passed"] is False
        assert result["status"] == "FAIL_LIVE_QUOTA_GATE"
        assert result["dicom_body_transfer_authorized"] is False


def test_rounded_pquota_usage_can_never_be_promoted_to_exact() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["unit_authority"]["pquota_usage_used_as_exact"] = True
        _rewrite_receipt(path, receipt)
        assert _error(path) == "UNIT_AUTHORITY_INVALID"

        receipt["unit_authority"]["pquota_usage_used_as_exact"] = False
        receipt["pquota_research_mapping"]["usage_display_used_as_exact"] = True
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_MAPPING_DOES_NOT_MATCH_RAW_ROW"


def test_pquota_display_usage_is_not_compared_as_exact_du_usage() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        assert result["exact_project_usage_bytes"] == 140_000_000_123
        assert result["exact_project_usage_bytes"] != 140_040_000_000


def test_pquota_file_count_columns_are_bound_but_never_used_as_bytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory), quota_gb="989")
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        text = raw.read_text(encoding="utf-8")
        text = text.replace("500000       140.04    335984", "999999999999 140.04    888888888888")
        _rewrite_raw(receipt, "pquota", "stdout", text.encode("utf-8"))
        mapping = receipt["pquota_research_mapping"]
        mapping["quota_files_display_value"] = "999999999999"
        mapping["usage_files_display_value"] = "888888888888"
        _rewrite_receipt(path, receipt)
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        assert result["quota_bytes"] == 989_000_000_000
        assert result["exact_project_usage_bytes"] == EXACT_DU_USAGE


def test_pquota_requires_exactly_one_research_filesystem_row() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        payload = raw.read_bytes() + b"/rprojectnb/other 1 1 1 1\n"
        _rewrite_raw(receipt, "pquota", "stdout", payload)
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_RESEARCH_FILESYSTEM_ROW_NOT_UNIQUE"


def test_pquota_requires_native_two_line_header_schema() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        text = raw.read_text(encoding="utf-8")
        text = text.replace(
            "                              quota     quota     usage     usage\n"
            "project space                 (GB)      (files)   (GB)      (files)\n"
            "----------------------------- --------- --------- --------- ---------\n",
            "Filesystem quota(GB) quota(files) usage(GB) usage(files)\n",
        )
        _rewrite_raw(receipt, "pquota", "stdout", text.encode("utf-8"))
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_COLUMN_HEADER_NOT_FOUND"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        text = raw.read_text(encoding="utf-8").replace(
            "project space                 (GB)      (files)   (GB)      (files)",
            "project space                 (files)   (GB)      (GB)      (files)",
        )
        _rewrite_raw(receipt, "pquota", "stdout", text.encode("utf-8"))
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_COLUMN_HEADER_NOT_FOUND"


def test_pquota_native_header_separator_and_uniqueness_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        text = raw.read_text(encoding="utf-8").replace(
            "----------------------------- --------- --------- --------- ---------",
            "----------------------------- --------- INVALID   --------- ---------",
        )
        _rewrite_raw(receipt, "pquota", "stdout", text.encode("utf-8"))
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_COLUMN_SEPARATOR_INVALID"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        text = raw.read_text(encoding="utf-8")
        header = "\n".join(text.splitlines()[:3]) + "\n"
        _rewrite_raw(receipt, "pquota", "stdout", (header + text).encode("utf-8"))
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_COLUMN_HEADER_NOT_UNIQUE"


def test_pquota_subordinate_owner_rows_are_not_project_rows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        assert result["quota_bytes"] == 2_000_000_000_000


def test_pquota_research_row_and_filesystem_mapping_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["pquota_research_mapping"]["research_filesystem_row"] = (
            f"/rproject/{PRINCIPAL}"
        )
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_RESEARCH_FILESYSTEM_MAPPING_INVALID"

        receipt["pquota_research_mapping"]["research_filesystem_row"] = (
            f"/rprojectnb/{PRINCIPAL}"
        )
        receipt["pquota_research_mapping"]["mapped_filesystem_authority_sha256"] = "f" * 64
        _rewrite_receipt(path, receipt)
        assert _error(path) == "PQUOTA_FILESYSTEM_MAPPING_MISMATCH"


def test_command_argv_hash_tool_identity_and_raw_files_are_bound() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["commands"]["du"]["argv"][1] = "--apparent-size"
        receipt["commands"]["du"]["argv_sha256"] = _digest(
            _canonical(receipt["commands"]["du"]["argv"])
        )
        _rewrite_receipt(path, receipt)
        assert _error(path) == "COMMAND_ARGV_CONTRACT_INVALID"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        executable = Path(receipt["commands"]["df"]["resolved_executable_path"])
        executable.write_bytes(b"tampered")
        assert _error(path) == "EXECUTABLE_IDENTITY_MISMATCH"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        Path(receipt["commands"]["findmnt"]["stdout"]["path"]).write_bytes(b"tampered")
        assert _error(path) == "RAW_FILE_HASH_OR_SIZE_MISMATCH"


def test_du_contract_is_nonenumerating_and_exact_row_is_required() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        _rewrite_raw(receipt, "du", "stdout", b"140000000123 /wrong/path\n")
        _rewrite_receipt(path, receipt)
        assert _error(path) == "DU_EXACT_USAGE_ROW_INVALID"


def test_migration_witness_hash_schema_and_false_completion_claims_fail() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["migration_authority"]["migration_witness_sha256"] = "f" * 64
        _rewrite_receipt(path, receipt)
        assert _error(path) == "MIGRATION_FILE_HASH_MISMATCH"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        witness_path = Path(receipt["migration_authority"]["migration_witness_path"])
        witness = json.loads(witness_path.read_text(encoding="utf-8"))
        witness["backup_verified"] = True
        payload = _json_bytes(witness)
        _write_private(witness_path, payload)
        receipt["migration_authority"]["migration_witness_sha256"] = _digest(payload)
        _rewrite_receipt(path, receipt)
        assert _error(path) == "MIGRATION_WITNESS_NOT_PLANNING_ONLY_AUTHORITY"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["migration_authority"]["migration_completion_verified"] = True
        _rewrite_receipt(path, receipt)
        assert _error(path) == "MIGRATION_AUTHORITY_MUST_REMAIN_PLANNED_UNBACKED"


def test_classification_file_hash_and_schema_are_actual_not_attested_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        classification_path = Path(
            receipt["migration_authority"]["migration_classification_path"]
        )
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        classification["unexpected"] = True
        payload = _json_bytes(classification)
        _write_private(classification_path, payload)
        receipt["migration_authority"]["migration_classification_sha256"] = _digest(payload)
        witness_path = Path(receipt["migration_authority"]["migration_witness_path"])
        witness = json.loads(witness_path.read_text(encoding="utf-8"))
        witness["classification_sha256"] = _digest(payload)
        witness_payload = _json_bytes(witness)
        _write_private(witness_path, witness_payload)
        receipt["migration_authority"]["migration_witness_sha256"] = _digest(witness_payload)
        _rewrite_receipt(path, receipt)
        assert _error(path) == "MIGRATION_CLASSIFICATION_SCHEMA_NOT_EXACT"


def test_receipt_and_bound_restricted_files_must_be_owner_mode_600() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        path.chmod(0o640)
        assert _error(path) == "EVIDENCE_OWNER_OR_MODE_INVALID"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        raw_path = Path(receipt["commands"]["pquota"]["stdout"]["path"])
        raw_path.chmod(0o644)
        assert _error(path) == "EVIDENCE_OWNER_OR_MODE_INVALID"


def test_stale_capture_or_commit_identity_mismatch_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        receipt["captured_at_utc"] = "2026-08-10T05:59:59Z"
        _rewrite_receipt(path, receipt)
        assert _error(path) == "RECEIPT_STALE"
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        try:
            quota.validate_restricted_evidence(
                path, expected_commit="c" * 40, now_utc=NOW
            )
        except quota.LiveQuotaError as exc:
            assert str(exc) == "CAPTURE_COMMIT_MISMATCH"
        else:
            raise AssertionError("Commit mismatch was accepted")


def test_aggregate_exports_hashes_but_no_paths_or_restricted_identities() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        quota.validate_aggregate_output(result)
        serialized = json.dumps(result, sort_keys=True).lower()
        for forbidden in (
            PRINCIPAL,
            RESEARCH_PATH,
            "hostname",
            "username",
            "effective_uid",
            "resolved_executable_path",
            "argv",
            "disaster_root",
        ):
            assert forbidden.lower() not in serialized
        for key in (
            "receipt_sha256",
            "command_provenance_sha256",
            "raw_evidence_bundle_sha256",
            "tool_identity_bundle_sha256",
            "migration_witness_sha256",
            "migration_classification_sha256",
        ):
            assert len(result[key]) == 64


def test_aggregate_tampering_and_authorization_expansion_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        result = quota.validate_restricted_evidence(
            path, expected_commit=COMMIT, now_utc=NOW
        )
        altered = deepcopy(result)
        altered["pquota_usage_used_as_exact"] = True
        try:
            quota.validate_aggregate_output(altered)
        except quota.LiveQuotaError as exc:
            assert str(exc) == "OUTPUT_ARITHMETIC_OR_STATUS_INCONSISTENT"
        else:
            raise AssertionError("Rounded quota usage promotion was accepted")
        altered = deepcopy(result)
        altered["dicom_body_transfer_authorized"] = True
        try:
            quota.validate_aggregate_output(altered)
        except quota.LiveQuotaError as exc:
            assert str(exc) == "OUTPUT_ARITHMETIC_OR_STATUS_INCONSISTENT"
        else:
            raise AssertionError("Authorization expansion was accepted")


def test_cli_writes_new_mode_600_aggregate_and_989gb_returns_gate_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, _ = _bundle(root, quota_gb="989")
        output = root / "aggregate.json"
        assert quota.main(
            [
                "--restricted-receipt",
                str(path),
                "--aggregate-output",
                str(output),
                "--expected-commit",
                COMMIT,
            ]
        ) == 3
        assert output.stat().st_mode & 0o777 == 0o600
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAIL_LIVE_QUOTA_GATE"
        assert quota.main(
            [
                "--restricted-receipt",
                str(path),
                "--aggregate-output",
                str(output),
                "--expected-commit",
                COMMIT,
            ]
        ) == 2


def test_validator_contains_no_shell_network_or_command_execution_api() -> None:
    source = (ROOT / "scripts" / "capture_lvef_c3_live_quota.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "import subprocess",
        "os.system(",
        "os.popen(",
        "urllib",
        "requests.",
        "socket.create_connection",
    ):
        assert forbidden not in source
