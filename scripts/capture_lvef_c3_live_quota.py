#!/usr/bin/env python3
"""Validate a sealed SCC live-quota evidence bundle without running commands.

The input is an owner-private JSON receipt that binds the exact read-only
``pquota``, ``findmnt``, ``df``, and non-enumerating ``du`` invocations to
their raw stdout/stderr files and to the executable used for each command.
The validator performs no shell or network operation.  It also verifies the
actual restricted migration witness and classification files before emitting
an identifier-free, closed-schema aggregate.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import sys
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
RECEIPT_KIND = "LVEF_C3_LIVE_RESEARCH_QUOTA_RAW_COMMAND_RECEIPT"
RECEIPT_STATUS = "PASS_READ_ONLY_CAPTURE"
EXACT_BYTE_UNIT = "BYTES_EXACT_INTEGER"
MAX_RECEIPT_AGE_SECONDS = 6 * 60 * 60
MAX_RESTRICTED_JSON_BYTES = 8 * 1024 * 1024
MAX_RAW_TEXT_BYTES = 1024 * 1024

SELECTED_SOURCE_BYTES = 1_216_569_133_322
FROZEN_PROJECTED_PEAK_BYTES = 1_611_642_076_332
REQUIRED_HEADROOM_BYTES = 200_000_000_000
MINIMUM_EFFECTIVE_QUOTA_BYTES = 1_811_642_076_332
DECIMAL_GB_BYTES = 1_000_000_000
DECIMAL_TB_BYTES = 1_000_000_000_000

MIGRATION_PLANNED = "PLANNED_NOT_EXECUTED"

TOP_LEVEL_KEYS = {
    "schema_version",
    "receipt_kind",
    "status",
    "captured_at_utc",
    "capture_identity",
    "unit_authority",
    "research_path",
    "commands",
    "pquota_research_mapping",
    "resource_plan",
    "migration_authority",
    "no_mutation_attestations",
}
CAPTURE_IDENTITY_KEYS = {
    "effective_uid",
    "effective_username_sha256",
    "hostname_sha256",
    "git_commit",
}
UNIT_AUTHORITY_KEYS = {
    "quota_allocation_unit_system",
    "decimal_gb_bytes",
    "decimal_tb_bytes",
    "pquota_allocation_display_is_exact",
    "pquota_usage_display_may_be_rounded",
    "pquota_usage_used_as_exact",
    "exact_project_usage_source",
}
COMMAND_KEYS = {"pquota", "findmnt", "df", "du"}
COMMAND_RECORD_KEYS = {
    "tool_name",
    "resolved_executable_path",
    "executable_sha256",
    "executable_size_bytes",
    "executable_device",
    "executable_inode",
    "argv",
    "argv_sha256",
    "exit_status",
    "stdout",
    "stderr",
}
RAW_FILE_KEYS = {"path", "byte_count", "sha256"}
PQUOTA_MAPPING_KEYS = {
    "quota_principal",
    "research_filesystem_row",
    "research_row_role",
    "quota_display_value",
    "quota_display_unit",
    "quota_files_display_value",
    "usage_display_value",
    "usage_display_unit",
    "usage_files_display_value",
    "file_count_columns_used_for_bytes",
    "quota_bytes",
    "usage_display_is_rounded",
    "usage_display_used_as_exact",
    "mapped_filesystem_authority_sha256",
}
RESOURCE_PLAN_KEYS = {
    "selected_source_bytes",
    "frozen_projected_peak_bytes",
    "required_headroom_bytes",
    "minimum_effective_quota_bytes",
}
MIGRATION_AUTHORITY_KEYS = {
    "migration_state",
    "migration_witness_path",
    "migration_witness_sha256",
    "migration_classification_path",
    "migration_classification_sha256",
    "migration_completion_verified",
    "backup_verified",
}
NO_MUTATION_KEYS = {
    "quota_changed",
    "files_moved",
    "files_deleted",
    "cloud_requests",
    "object_bodies_downloaded",
}

MIGRATION_WITNESS_KEYS = {
    "schema_version",
    "witness_type",
    "status",
    "planning_mode",
    "classification_complete",
    "migration_state",
    "disaster_tier_inventory_bytes",
    "classified_migration_bytes",
    "classified_retained_bytes",
    "symlink_count",
    "symlink_scope_count",
    "blocking_symlink_count",
    "retained_symlink_scope_count",
    "nested_mount_count",
    "all_symlinks_internal_existing_same_scope",
    "full_migration_path_classification_supported",
    "symlink_target_content_followed_or_counted",
    "inventory_sha256",
    "classification_sha256",
    "backup_verified",
    "migration_executed",
    "owner_authorization_present",
    "full_c3_authorized",
}
MIGRATION_CLASSIFICATION_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "planning_mode",
    "source_storage_detail_sha256",
    "disaster_root",
    "disaster_tier_inventory_bytes",
    "direct_child_count",
    "direct_child_bytes",
    "root_files_or_overhead_bytes",
    "nested_inventory_row_count",
    "nested_mount_count",
    "symlink_count",
    "symlink_scope_count",
    "blocking_symlink_count",
    "retained_symlink_scope_count",
    "all_symlinks_internal_existing_same_scope",
    "symlink_target_content_followed_or_counted",
    "complete_classified_direct_child_coverage",
    "classified_migration_bytes",
    "classified_retained_bytes",
    "backup_verified",
    "migration_executed",
    "owner_authorization_present",
    "entries",
    "root_files_or_overhead",
}

OUTPUT_KEYS = {
    "schema_version",
    "status",
    "units",
    "receipt_sha256",
    "command_provenance_sha256",
    "raw_evidence_bundle_sha256",
    "tool_identity_bundle_sha256",
    "migration_witness_sha256",
    "migration_classification_sha256",
    "receipt_fresh",
    "capture_identity_verified",
    "command_contract_verified",
    "raw_output_hashes_verified",
    "pquota_research_mapping_verified",
    "quota_unit_authority_verified",
    "pquota_usage_used_as_exact",
    "quota_bytes",
    "exact_project_usage_bytes",
    "project_quota_available_bytes",
    "filesystem_capacity_bytes",
    "filesystem_used_bytes",
    "filesystem_available_bytes",
    "filesystem_unavailable_or_reserved_bytes",
    "selected_source_bytes",
    "selected_source_exceeds_quota",
    "projected_peak_bytes",
    "required_headroom_bytes",
    "minimum_effective_quota_bytes",
    "project_quota_slack_bytes",
    "filesystem_required_available_bytes",
    "filesystem_slack_bytes",
    "effective_capacity_ceiling_bytes",
    "effective_headroom_bytes",
    "source_fit_gate_passed",
    "project_quota_gate_passed",
    "filesystem_availability_gate_passed",
    "minimum_effective_quota_gate_passed",
    "migration_state",
    "planned_migration_bytes",
    "migration_classification_authority_passed",
    "migration_completion_authority_passed",
    "backup_authority_passed",
    "live_quota_evidence_passed",
    "full_c3_authorized",
    "dicom_body_transfer_authorized",
    "restricted_fields_exported",
}


class LiveQuotaError(ValueError):
    """Raised when restricted evidence cannot support a quota ruling."""


@dataclass(frozen=True)
class FileEvidence:
    path: Path
    payload: bytes
    sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiveQuotaError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def strict_json_loads(payload: str) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except LiveQuotaError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LiveQuotaError("JSON_INVALID") from exc


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveQuotaError(code)
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], code: str) -> None:
    if set(value) != keys:
        raise LiveQuotaError(code)


def _integer(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LiveQuotaError(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise LiveQuotaError(code)
    return value


def _string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise LiveQuotaError(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LiveQuotaError(code)
    return value


def _git_commit(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LiveQuotaError("GIT_COMMIT_INVALID")
    return value


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utc_second(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LiveQuotaError("CAPTURE_TIMESTAMP_INVALID")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise LiveQuotaError("CAPTURE_TIMESTAMP_INVALID") from exc


def _normalize_now(now_utc: datetime | None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise LiveQuotaError("CURRENT_TIME_MUST_BE_TIMEZONE_AWARE")
    return now.astimezone(timezone.utc).replace(microsecond=0)


def _validate_freshness(captured_at: datetime, now: datetime) -> None:
    age = now - captured_at
    if age < timedelta(0):
        raise LiveQuotaError("RECEIPT_CAPTURED_IN_FUTURE")
    if age > timedelta(seconds=MAX_RECEIPT_AGE_SECONDS):
        raise LiveQuotaError("RECEIPT_STALE")


def _read_file(
    path: Path,
    *,
    owner_private: bool,
    maximum_bytes: int,
    outside_repository: bool = True,
) -> FileEvidence:
    if not hasattr(os, "O_NOFOLLOW"):
        raise LiveQuotaError("PLATFORM_LACKS_NOFOLLOW_OPEN")
    if not path.is_absolute():
        raise LiveQuotaError("EVIDENCE_PATH_NOT_ABSOLUTE")
    repository_root = Path(__file__).resolve().parents[1]
    if outside_repository:
        try:
            path.resolve(strict=True).relative_to(repository_root)
        except ValueError:
            pass
        except OSError as exc:
            raise LiveQuotaError("EVIDENCE_PATH_RESOLUTION_FAILED") from exc
        else:
            raise LiveQuotaError("RESTRICTED_EVIDENCE_INSIDE_GIT_WORKTREE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise LiveQuotaError("EVIDENCE_OPEN_FAILED") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LiveQuotaError("EVIDENCE_NOT_REGULAR_FILE")
        if owner_private and (
            metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise LiveQuotaError("EVIDENCE_OWNER_OR_MODE_INVALID")
        if metadata.st_size > maximum_bytes:
            raise LiveQuotaError("EVIDENCE_SIZE_INVALID")
        payload = b""
        while len(payload) <= maximum_bytes:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(payload)))
            if not block:
                break
            payload += block
        if len(payload) != metadata.st_size or len(payload) > maximum_bytes:
            raise LiveQuotaError("EVIDENCE_SIZE_INVALID")
    finally:
        os.close(descriptor)
    return FileEvidence(path=path, payload=payload, sha256=_digest(payload))


def _read_json_evidence(path: Path) -> tuple[Mapping[str, Any], FileEvidence]:
    evidence = _read_file(
        path, owner_private=True, maximum_bytes=MAX_RESTRICTED_JSON_BYTES
    )
    try:
        text = evidence.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiveQuotaError("EVIDENCE_UTF8_INVALID") from exc
    return _mapping(strict_json_loads(text), "JSON_ROOT_NOT_MAPPING"), evidence


def _validate_capture_identity(identity: Mapping[str, Any], expected_commit: str) -> None:
    _exact_keys(identity, CAPTURE_IDENTITY_KEYS, "CAPTURE_IDENTITY_SCHEMA_NOT_EXACT")
    if _integer(identity.get("effective_uid"), "CAPTURE_UID_INVALID") != os.getuid():
        raise LiveQuotaError("CAPTURE_UID_MISMATCH")
    expected_user = _digest(pwd.getpwuid(os.getuid()).pw_name.encode("utf-8"))
    expected_host = _digest(socket.gethostname().encode("utf-8"))
    if _sha256(
        identity.get("effective_username_sha256"), "CAPTURE_USERNAME_SHA256_INVALID"
    ) != expected_user:
        raise LiveQuotaError("CAPTURE_USERNAME_MISMATCH")
    if _sha256(identity.get("hostname_sha256"), "HOSTNAME_SHA256_INVALID") != expected_host:
        raise LiveQuotaError("CAPTURE_HOSTNAME_MISMATCH")
    if _git_commit(identity.get("git_commit")) != expected_commit:
        raise LiveQuotaError("CAPTURE_COMMIT_MISMATCH")


def _validate_unit_authority(authority: Mapping[str, Any]) -> None:
    _exact_keys(authority, UNIT_AUTHORITY_KEYS, "UNIT_AUTHORITY_SCHEMA_NOT_EXACT")
    if (
        authority.get("quota_allocation_unit_system") != "DECIMAL_SI"
        or _integer(authority.get("decimal_gb_bytes"), "DECIMAL_GB_BYTES_INVALID")
        != DECIMAL_GB_BYTES
        or _integer(authority.get("decimal_tb_bytes"), "DECIMAL_TB_BYTES_INVALID")
        != DECIMAL_TB_BYTES
        or _boolean(
            authority.get("pquota_allocation_display_is_exact"),
            "PQUOTA_ALLOCATION_EXACT_FLAG_INVALID",
        )
        is not True
        or _boolean(
            authority.get("pquota_usage_display_may_be_rounded"),
            "PQUOTA_USAGE_ROUNDED_FLAG_INVALID",
        )
        is not True
        or _boolean(
            authority.get("pquota_usage_used_as_exact"),
            "PQUOTA_USAGE_EXACT_FLAG_INVALID",
        )
        is not False
        or authority.get("exact_project_usage_source") != "DU_X_S_B1_ALLOCATED_BYTES"
    ):
        raise LiveQuotaError("UNIT_AUTHORITY_INVALID")


def _validate_raw_file_spec(spec: Any) -> FileEvidence:
    record = _mapping(spec, "RAW_FILE_RECORD_NOT_MAPPING")
    _exact_keys(record, RAW_FILE_KEYS, "RAW_FILE_SCHEMA_NOT_EXACT")
    evidence = _read_file(
        Path(_string(record.get("path"), "RAW_FILE_PATH_INVALID")),
        owner_private=True,
        maximum_bytes=MAX_RAW_TEXT_BYTES,
    )
    if (
        _integer(record.get("byte_count"), "RAW_FILE_BYTE_COUNT_INVALID")
        != len(evidence.payload)
        or _sha256(record.get("sha256"), "RAW_FILE_SHA256_INVALID")
        != evidence.sha256
    ):
        raise LiveQuotaError("RAW_FILE_HASH_OR_SIZE_MISMATCH")
    return evidence


def _tool_identity_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "tool_name",
            "resolved_executable_path",
            "executable_sha256",
            "executable_size_bytes",
            "executable_device",
            "executable_inode",
        )
    }


def _validate_command_record(
    role: str,
    value: Any,
    *,
    expected_argv: list[str],
) -> tuple[Mapping[str, Any], FileEvidence, FileEvidence]:
    record = _mapping(value, "COMMAND_RECORD_NOT_MAPPING")
    _exact_keys(record, COMMAND_RECORD_KEYS, "COMMAND_RECORD_SCHEMA_NOT_EXACT")
    if record.get("tool_name") != role:
        raise LiveQuotaError("COMMAND_TOOL_ROLE_MISMATCH")
    executable_path = Path(
        _string(record.get("resolved_executable_path"), "EXECUTABLE_PATH_INVALID")
    )
    if not executable_path.is_absolute() or executable_path.name != role:
        raise LiveQuotaError("EXECUTABLE_IDENTITY_INVALID")
    try:
        resolved_executable = executable_path.resolve(strict=True)
    except OSError as exc:
        raise LiveQuotaError("EXECUTABLE_RESOLUTION_FAILED") from exc
    executable = _read_file(
        resolved_executable,
        owner_private=False,
        maximum_bytes=256 * 1024 * 1024,
        outside_repository=False,
    )
    metadata = resolved_executable.stat()
    if (
        _sha256(record.get("executable_sha256"), "EXECUTABLE_SHA256_INVALID")
        != executable.sha256
        or _integer(record.get("executable_size_bytes"), "EXECUTABLE_SIZE_INVALID")
        != metadata.st_size
        or _integer(record.get("executable_device"), "EXECUTABLE_DEVICE_INVALID")
        != metadata.st_dev
        or _integer(record.get("executable_inode"), "EXECUTABLE_INODE_INVALID")
        != metadata.st_ino
    ):
        raise LiveQuotaError("EXECUTABLE_IDENTITY_MISMATCH")
    argv = record.get("argv")
    if (
        not isinstance(argv, list)
        or any(not isinstance(item, str) for item in argv)
        or argv != expected_argv
        or argv[0] != str(executable_path)
    ):
        raise LiveQuotaError("COMMAND_ARGV_CONTRACT_INVALID")
    if _sha256(record.get("argv_sha256"), "COMMAND_ARGV_SHA256_INVALID") != _digest(
        _canonical_json_bytes(argv)
    ):
        raise LiveQuotaError("COMMAND_ARGV_HASH_MISMATCH")
    if _integer(record.get("exit_status"), "COMMAND_EXIT_STATUS_INVALID") != 0:
        raise LiveQuotaError("COMMAND_DID_NOT_SUCCEED")
    stdout = _validate_raw_file_spec(record.get("stdout"))
    stderr = _validate_raw_file_spec(record.get("stderr"))
    if stderr.payload:
        raise LiveQuotaError("COMMAND_STDERR_NOT_EMPTY")
    return record, stdout, stderr


def _decode_text(evidence: FileEvidence, code: str) -> str:
    try:
        return evidence.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiveQuotaError(code) from exc


def _parse_pquota(
    stdout: FileEvidence, mapping: Mapping[str, Any]
) -> tuple[int, Decimal]:
    _exact_keys(mapping, PQUOTA_MAPPING_KEYS, "PQUOTA_MAPPING_SCHEMA_NOT_EXACT")
    principal = _string(mapping.get("quota_principal"), "PQUOTA_PRINCIPAL_INVALID")
    research_filesystem_row = _string(
        mapping.get("research_filesystem_row"),
        "PQUOTA_RESEARCH_FILESYSTEM_ROW_INVALID",
    )
    if (
        research_filesystem_row != f"/rprojectnb/{principal}"
        or mapping.get("research_row_role") != "RESEARCH_NOT_BACKED_UP"
    ):
        raise LiveQuotaError("PQUOTA_RESEARCH_FILESYSTEM_MAPPING_INVALID")
    text = _decode_text(stdout, "PQUOTA_OUTPUT_UTF8_INVALID")
    lines = text.splitlines()
    header_indices = []
    for index in range(len(lines) - 2):
        group_tokens = [token.casefold() for token in lines[index].split()]
        column_tokens = [token.casefold() for token in lines[index + 1].split()]
        if group_tokens != ["quota", "quota", "usage", "usage"]:
            continue
        if column_tokens != [
            "project",
            "space",
            "(gb)",
            "(files)",
            "(gb)",
            "(files)",
        ]:
            continue
        separator_tokens = lines[index + 2].split()
        if (
            len(separator_tokens) != 5
            or any(len(token) < 3 or set(token) != {"-"} for token in separator_tokens)
        ):
            raise LiveQuotaError("PQUOTA_COLUMN_SEPARATOR_INVALID")
        header_indices.append(index)
    if not header_indices:
        raise LiveQuotaError("PQUOTA_COLUMN_HEADER_NOT_FOUND")
    if len(header_indices) != 1:
        raise LiveQuotaError("PQUOTA_COLUMN_HEADER_NOT_UNIQUE")
    research_rows = [
        (index, line.split())
        for index, line in enumerate(lines)
        if line.split() and line.split()[0].startswith("/rprojectnb/")
    ]
    if len(research_rows) != 1:
        raise LiveQuotaError("PQUOTA_RESEARCH_FILESYSTEM_ROW_NOT_UNIQUE")
    research_row_index, fields = research_rows[0]
    if research_row_index <= header_indices[0] + 2:
        raise LiveQuotaError("PQUOTA_RESEARCH_ROW_OUTSIDE_NATIVE_TABLE")
    if len(fields) != 5 or fields[0] != research_filesystem_row:
        raise LiveQuotaError("PQUOTA_RESEARCH_ROW_LAYOUT_INVALID")
    quota_value_text, quota_files_text, usage_value_text, usage_files_text = fields[1:]
    quota_unit = "GB"
    usage_unit = "GB"
    try:
        Decimal(quota_files_text)
        Decimal(usage_files_text)
    except InvalidOperation as exc:
        raise LiveQuotaError("PQUOTA_FILE_COUNT_COLUMNS_INVALID") from exc
    if (
        mapping.get("quota_display_value") != quota_value_text
        or mapping.get("quota_display_unit") != quota_unit
        or mapping.get("quota_files_display_value") != quota_files_text
        or mapping.get("usage_display_value") != usage_value_text
        or mapping.get("usage_display_unit") != usage_unit
        or mapping.get("usage_files_display_value") != usage_files_text
        or _boolean(
            mapping.get("file_count_columns_used_for_bytes"),
            "PQUOTA_FILE_COUNT_USAGE_FLAG_INVALID",
        )
        is not False
        or _boolean(
            mapping.get("usage_display_is_rounded"),
            "PQUOTA_MAPPING_USAGE_ROUNDED_FLAG_INVALID",
        )
        is not True
        or _boolean(
            mapping.get("usage_display_used_as_exact"),
            "PQUOTA_MAPPING_USAGE_EXACT_FLAG_INVALID",
        )
        is not False
    ):
        raise LiveQuotaError("PQUOTA_MAPPING_DOES_NOT_MATCH_RAW_ROW")
    multipliers = {
        "B": 1,
        "KB": 1_000,
        "MB": 1_000_000,
        "GB": DECIMAL_GB_BYTES,
        "TB": DECIMAL_TB_BYTES,
    }
    try:
        quota_decimal = Decimal(quota_value_text) * multipliers[quota_unit]
        usage_decimal = Decimal(usage_value_text) * multipliers[usage_unit]
    except (InvalidOperation, KeyError) as exc:
        raise LiveQuotaError("PQUOTA_DECIMAL_CONVERSION_INVALID") from exc
    if quota_decimal != quota_decimal.to_integral_value():
        raise LiveQuotaError("PQUOTA_QUOTA_NOT_EXACT_INTEGER_BYTES")
    quota_bytes = int(quota_decimal)
    if _integer(mapping.get("quota_bytes"), "PQUOTA_MAPPING_QUOTA_BYTES_INVALID") != quota_bytes:
        raise LiveQuotaError("PQUOTA_MAPPING_QUOTA_BYTES_MISMATCH")
    return quota_bytes, usage_decimal


def _parse_findmnt(stdout: FileEvidence, research_path: str) -> tuple[str, str, str]:
    payload = _mapping(
        strict_json_loads(_decode_text(stdout, "FINDMNT_OUTPUT_UTF8_INVALID")),
        "FINDMNT_OUTPUT_ROOT_NOT_MAPPING",
    )
    _exact_keys(payload, {"filesystems"}, "FINDMNT_OUTPUT_SCHEMA_NOT_EXACT")
    rows = payload.get("filesystems")
    if not isinstance(rows, list) or len(rows) != 1:
        raise LiveQuotaError("FINDMNT_FILESYSTEM_ROW_NOT_UNIQUE")
    row = _mapping(rows[0], "FINDMNT_FILESYSTEM_ROW_NOT_MAPPING")
    _exact_keys(row, {"source", "target", "fstype", "options"}, "FINDMNT_ROW_SCHEMA_NOT_EXACT")
    source = _string(row.get("source"), "FINDMNT_SOURCE_INVALID")
    target = _string(row.get("target"), "FINDMNT_TARGET_INVALID")
    fstype = _string(row.get("fstype"), "FINDMNT_FSTYPE_INVALID")
    _string(row.get("options"), "FINDMNT_OPTIONS_INVALID")
    try:
        Path(research_path).relative_to(Path(target))
    except ValueError as exc:
        raise LiveQuotaError("RESEARCH_PATH_NOT_ON_FINDMNT_TARGET") from exc
    authority = _digest(_canonical_json_bytes({"source": source, "target": target, "fstype": fstype}))
    return source, target, authority


def _parse_df(
    stdout: FileEvidence, expected_source: str, expected_mount_target: str
) -> tuple[int, int, int, int]:
    lines = [line for line in _decode_text(stdout, "DF_OUTPUT_UTF8_INVALID").splitlines() if line.strip()]
    if len(lines) != 2:
        raise LiveQuotaError("DF_OUTPUT_ROW_COUNT_INVALID")
    header = lines[0].lower().split()
    if header[:4] not in (["filesystem", "1b-blocks", "used", "avail"], ["filesystem", "1b-blocks", "used", "available"]):
        raise LiveQuotaError("DF_OUTPUT_HEADER_INVALID")
    fields = lines[1].split(maxsplit=4)
    if (
        len(fields) != 5
        or fields[0] != expected_source
        or fields[4] != expected_mount_target
    ):
        raise LiveQuotaError("DF_FILESYSTEM_MAPPING_INVALID")
    try:
        capacity, used, available = (int(fields[index]) for index in (1, 2, 3))
    except ValueError as exc:
        raise LiveQuotaError("DF_BYTE_FIELDS_INVALID") from exc
    if min(capacity, used, available) < 0 or used + available > capacity:
        raise LiveQuotaError("DF_BYTES_DO_NOT_RECONCILE")
    return capacity, used, available, capacity - used - available


def _parse_du(stdout: FileEvidence, research_path: str) -> int:
    lines = [line for line in _decode_text(stdout, "DU_OUTPUT_UTF8_INVALID").splitlines() if line.strip()]
    if len(lines) != 1:
        raise LiveQuotaError("DU_OUTPUT_ROW_COUNT_INVALID")
    fields = lines[0].split(maxsplit=1)
    if len(fields) != 2 or fields[1] != research_path or not fields[0].isdigit():
        raise LiveQuotaError("DU_EXACT_USAGE_ROW_INVALID")
    return int(fields[0])


def _validate_migration_authority(authority: Mapping[str, Any]) -> tuple[int, str, str]:
    _exact_keys(authority, MIGRATION_AUTHORITY_KEYS, "MIGRATION_AUTHORITY_SCHEMA_NOT_EXACT")
    if (
        authority.get("migration_state") != MIGRATION_PLANNED
        or _boolean(
            authority.get("migration_completion_verified"),
            "MIGRATION_COMPLETION_FLAG_INVALID",
        )
        is not False
        or _boolean(authority.get("backup_verified"), "BACKUP_FLAG_INVALID") is not False
    ):
        raise LiveQuotaError("MIGRATION_AUTHORITY_MUST_REMAIN_PLANNED_UNBACKED")
    witness, witness_file = _read_json_evidence(
        Path(_string(authority.get("migration_witness_path"), "MIGRATION_WITNESS_PATH_INVALID"))
    )
    classification, classification_file = _read_json_evidence(
        Path(
            _string(
                authority.get("migration_classification_path"),
                "MIGRATION_CLASSIFICATION_PATH_INVALID",
            )
        )
    )
    if (
        _sha256(authority.get("migration_witness_sha256"), "MIGRATION_WITNESS_SHA256_INVALID")
        != witness_file.sha256
        or _sha256(
            authority.get("migration_classification_sha256"),
            "MIGRATION_CLASSIFICATION_SHA256_INVALID",
        )
        != classification_file.sha256
    ):
        raise LiveQuotaError("MIGRATION_FILE_HASH_MISMATCH")
    _exact_keys(witness, MIGRATION_WITNESS_KEYS, "MIGRATION_WITNESS_SCHEMA_NOT_EXACT")
    _exact_keys(
        classification,
        MIGRATION_CLASSIFICATION_KEYS,
        "MIGRATION_CLASSIFICATION_SCHEMA_NOT_EXACT",
    )
    for document in (witness, classification):
        if document.get("schema_version") != 1:
            raise LiveQuotaError("MIGRATION_DOCUMENT_SCHEMA_VERSION_INVALID")
    if (
        witness.get("witness_type") != "lvef_c3_migration_witness_v1"
        or witness.get("status") != "PASS_CLASSIFIED_MIGRATION_WITNESS"
        or witness.get("planning_mode") != "FULL_MIGRATION_AFTER_BACKUP"
        or witness.get("classification_complete") is not True
        or witness.get("migration_state") != MIGRATION_PLANNED
        or witness.get("backup_verified") is not False
        or witness.get("migration_executed") is not False
        or witness.get("owner_authorization_present") is not False
        or witness.get("full_c3_authorized") is not False
        or classification.get("artifact_type")
        != "lvef_c3_disaster_tier_path_classification"
        or classification.get("status")
        != "PASS_COMPLETE_PLANNING_CLASSIFICATION_NOT_EXECUTED"
        or classification.get("planning_mode") != "FULL_MIGRATION_AFTER_BACKUP"
        or classification.get("complete_classified_direct_child_coverage") is not True
        or classification.get("backup_verified") is not False
        or classification.get("migration_executed") is not False
        or classification.get("owner_authorization_present") is not False
    ):
        raise LiveQuotaError("MIGRATION_WITNESS_NOT_PLANNING_ONLY_AUTHORITY")
    inventory = _integer(witness.get("disaster_tier_inventory_bytes"), "MIGRATION_INVENTORY_BYTES_INVALID")
    migrated = _integer(witness.get("classified_migration_bytes"), "MIGRATION_BYTES_INVALID")
    retained = _integer(witness.get("classified_retained_bytes"), "RETAINED_BYTES_INVALID")
    if migrated + retained != inventory:
        raise LiveQuotaError("MIGRATION_WITNESS_BYTES_DO_NOT_RECONCILE")
    matching_numeric = {
        "disaster_tier_inventory_bytes",
        "classified_migration_bytes",
        "classified_retained_bytes",
        "symlink_count",
        "symlink_scope_count",
        "blocking_symlink_count",
        "retained_symlink_scope_count",
        "nested_mount_count",
    }
    if any(witness[key] != classification[key] for key in matching_numeric):
        raise LiveQuotaError("MIGRATION_CLASSIFICATION_WITNESS_MISMATCH")
    if (
        _sha256(witness.get("classification_sha256"), "WITNESS_CLASSIFICATION_SHA256_INVALID")
        != classification_file.sha256
        or _sha256(witness.get("inventory_sha256"), "WITNESS_INVENTORY_SHA256_INVALID")
        != _sha256(
            classification.get("source_storage_detail_sha256"),
            "CLASSIFICATION_STORAGE_DETAIL_SHA256_INVALID",
        )
    ):
        raise LiveQuotaError("MIGRATION_PROVENANCE_HASH_MISMATCH")
    entries = classification.get("entries")
    if not isinstance(entries, list) or len(entries) != classification.get("direct_child_count"):
        raise LiveQuotaError("MIGRATION_CLASSIFICATION_ENTRY_COUNT_INVALID")
    for entry in entries:
        record = _mapping(entry, "MIGRATION_CLASSIFICATION_ENTRY_NOT_MAPPING")
        if record.get("backup_status") != "NOT_VERIFIED_BY_THIS_WITNESS" or record.get("migration_status") != "NOT_EXECUTED":
            raise LiveQuotaError("MIGRATION_CLASSIFICATION_FALSE_COMPLETION_CLAIM")
    root_overhead = _mapping(
        classification.get("root_files_or_overhead"),
        "MIGRATION_ROOT_OVERHEAD_NOT_MAPPING",
    )
    if root_overhead.get("backup_status") != "NOT_VERIFIED_BY_THIS_WITNESS" or root_overhead.get("migration_status") != "NOT_EXECUTED":
        raise LiveQuotaError("MIGRATION_ROOT_OVERHEAD_FALSE_COMPLETION_CLAIM")
    return migrated, witness_file.sha256, classification_file.sha256


def validate_restricted_evidence(
    receipt_path: Path,
    *,
    expected_commit: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate receipt plus bound files and return a Git-safe aggregate."""

    expected_commit = _git_commit(expected_commit)
    receipt, receipt_file = _read_json_evidence(receipt_path)
    _exact_keys(receipt, TOP_LEVEL_KEYS, "RECEIPT_SCHEMA_NOT_EXACT")
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("receipt_kind") != RECEIPT_KIND
        or receipt.get("status") != RECEIPT_STATUS
    ):
        raise LiveQuotaError("RECEIPT_AUTHORITY_INVALID")
    _validate_freshness(
        _utc_second(receipt.get("captured_at_utc")), _normalize_now(now_utc)
    )
    _validate_capture_identity(
        _mapping(receipt.get("capture_identity"), "CAPTURE_IDENTITY_NOT_MAPPING"),
        expected_commit,
    )
    _validate_unit_authority(
        _mapping(receipt.get("unit_authority"), "UNIT_AUTHORITY_NOT_MAPPING")
    )
    research_path = _string(receipt.get("research_path"), "RESEARCH_PATH_INVALID")
    if not Path(research_path).is_absolute():
        raise LiveQuotaError("RESEARCH_PATH_NOT_ABSOLUTE")

    mapping = _mapping(
        receipt.get("pquota_research_mapping"), "PQUOTA_MAPPING_NOT_MAPPING"
    )
    principal = _string(mapping.get("quota_principal"), "PQUOTA_PRINCIPAL_INVALID")
    commands = _mapping(receipt.get("commands"), "COMMANDS_NOT_MAPPING")
    _exact_keys(commands, COMMAND_KEYS, "COMMANDS_SCHEMA_NOT_EXACT")
    executable_paths = {
        role: _string(
            _mapping(commands[role], "COMMAND_RECORD_NOT_MAPPING").get(
                "resolved_executable_path"
            ),
            "EXECUTABLE_PATH_INVALID",
        )
        for role in COMMAND_KEYS
    }
    expected_argv = {
        "pquota": [executable_paths["pquota"], "-u", principal],
        "findmnt": [
            executable_paths["findmnt"],
            "--json",
            "--target",
            research_path,
            "--output",
            "SOURCE,TARGET,FSTYPE,OPTIONS",
        ],
        "df": [
            executable_paths["df"],
            "-B1",
            "--output=source,size,used,avail,target",
            research_path,
        ],
        "du": [executable_paths["du"], "-x", "-s", "-B1", research_path],
    }
    validated_commands: dict[str, tuple[Mapping[str, Any], FileEvidence, FileEvidence]] = {}
    for role in sorted(COMMAND_KEYS):
        validated_commands[role] = _validate_command_record(
            role, commands[role], expected_argv=expected_argv[role]
        )

    quota_bytes, _rounded_pquota_usage = _parse_pquota(
        validated_commands["pquota"][1], mapping
    )
    findmnt_source, findmnt_target, filesystem_authority = _parse_findmnt(
        validated_commands["findmnt"][1], research_path
    )
    if _sha256(
        mapping.get("mapped_filesystem_authority_sha256"),
        "PQUOTA_FILESYSTEM_AUTHORITY_SHA256_INVALID",
    ) != filesystem_authority:
        raise LiveQuotaError("PQUOTA_FILESYSTEM_MAPPING_MISMATCH")
    filesystem_capacity, filesystem_used, filesystem_available, filesystem_reserved = _parse_df(
        validated_commands["df"][1], findmnt_source, findmnt_target
    )
    exact_project_usage = _parse_du(validated_commands["du"][1], research_path)
    if exact_project_usage > quota_bytes:
        project_available = 0
    else:
        project_available = quota_bytes - exact_project_usage

    plan = _mapping(receipt.get("resource_plan"), "RESOURCE_PLAN_NOT_MAPPING")
    _exact_keys(plan, RESOURCE_PLAN_KEYS, "RESOURCE_PLAN_SCHEMA_NOT_EXACT")
    required_plan = {
        "selected_source_bytes": SELECTED_SOURCE_BYTES,
        "frozen_projected_peak_bytes": FROZEN_PROJECTED_PEAK_BYTES,
        "required_headroom_bytes": REQUIRED_HEADROOM_BYTES,
        "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
    }
    if any(plan.get(key) != value for key, value in required_plan.items()):
        raise LiveQuotaError("RESOURCE_PLAN_AUTHORITY_CHANGED")
    planned_migration_bytes, migration_witness_sha, migration_classification_sha = _validate_migration_authority(
        _mapping(receipt.get("migration_authority"), "MIGRATION_AUTHORITY_NOT_MAPPING")
    )

    no_mutation = _mapping(
        receipt.get("no_mutation_attestations"), "NO_MUTATION_ATTESTATIONS_NOT_MAPPING"
    )
    _exact_keys(no_mutation, NO_MUTATION_KEYS, "NO_MUTATION_SCHEMA_NOT_EXACT")
    if (
        _boolean(no_mutation.get("quota_changed"), "QUOTA_CHANGED_FLAG_INVALID")
        or _integer(no_mutation.get("files_moved"), "FILES_MOVED_INVALID") != 0
        or _integer(no_mutation.get("files_deleted"), "FILES_DELETED_INVALID") != 0
        or _integer(no_mutation.get("cloud_requests"), "CLOUD_REQUESTS_INVALID") != 0
        or _integer(
            no_mutation.get("object_bodies_downloaded"),
            "OBJECT_BODIES_DOWNLOADED_INVALID",
        )
        != 0
    ):
        raise LiveQuotaError("READ_ONLY_BOUNDARY_VIOLATED")

    command_provenance = {
        role: {
            "argv_sha256": validated_commands[role][0]["argv_sha256"],
            "tool": _tool_identity_payload(validated_commands[role][0]),
            "stdout_sha256": validated_commands[role][1].sha256,
            "stderr_sha256": validated_commands[role][2].sha256,
        }
        for role in sorted(COMMAND_KEYS)
    }
    raw_evidence = {
        role: {
            "stdout_sha256": validated_commands[role][1].sha256,
            "stdout_bytes": len(validated_commands[role][1].payload),
            "stderr_sha256": validated_commands[role][2].sha256,
            "stderr_bytes": len(validated_commands[role][2].payload),
        }
        for role in sorted(COMMAND_KEYS)
    }
    tool_identity = {
        role: _tool_identity_payload(validated_commands[role][0])
        for role in sorted(COMMAND_KEYS)
    }

    source_exceeds = SELECTED_SOURCE_BYTES > quota_bytes
    source_fit_gate = not source_exceeds
    project_slack = quota_bytes - FROZEN_PROJECTED_PEAK_BYTES
    incremental_to_peak = max(FROZEN_PROJECTED_PEAK_BYTES - exact_project_usage, 0)
    filesystem_required_available = incremental_to_peak + REQUIRED_HEADROOM_BYTES
    filesystem_slack = filesystem_available - filesystem_required_available
    effective_ceiling = min(quota_bytes, exact_project_usage + filesystem_available)
    effective_headroom = effective_ceiling - FROZEN_PROJECTED_PEAK_BYTES
    project_gate = (
        quota_bytes >= MINIMUM_EFFECTIVE_QUOTA_BYTES
        and project_slack >= REQUIRED_HEADROOM_BYTES
    )
    filesystem_gate = filesystem_available >= filesystem_required_available
    minimum_gate = (
        source_fit_gate
        and project_gate
        and filesystem_gate
        and effective_headroom >= REQUIRED_HEADROOM_BYTES
    )

    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS_LIVE_QUOTA_GATE" if minimum_gate else "FAIL_LIVE_QUOTA_GATE",
        "units": EXACT_BYTE_UNIT,
        "receipt_sha256": receipt_file.sha256,
        "command_provenance_sha256": _digest(_canonical_json_bytes(command_provenance)),
        "raw_evidence_bundle_sha256": _digest(_canonical_json_bytes(raw_evidence)),
        "tool_identity_bundle_sha256": _digest(_canonical_json_bytes(tool_identity)),
        "migration_witness_sha256": migration_witness_sha,
        "migration_classification_sha256": migration_classification_sha,
        "receipt_fresh": True,
        "capture_identity_verified": True,
        "command_contract_verified": True,
        "raw_output_hashes_verified": True,
        "pquota_research_mapping_verified": True,
        "quota_unit_authority_verified": True,
        "pquota_usage_used_as_exact": False,
        "quota_bytes": quota_bytes,
        "exact_project_usage_bytes": exact_project_usage,
        "project_quota_available_bytes": project_available,
        "filesystem_capacity_bytes": filesystem_capacity,
        "filesystem_used_bytes": filesystem_used,
        "filesystem_available_bytes": filesystem_available,
        "filesystem_unavailable_or_reserved_bytes": filesystem_reserved,
        "selected_source_bytes": SELECTED_SOURCE_BYTES,
        "selected_source_exceeds_quota": source_exceeds,
        "projected_peak_bytes": FROZEN_PROJECTED_PEAK_BYTES,
        "required_headroom_bytes": REQUIRED_HEADROOM_BYTES,
        "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "project_quota_slack_bytes": project_slack,
        "filesystem_required_available_bytes": filesystem_required_available,
        "filesystem_slack_bytes": filesystem_slack,
        "effective_capacity_ceiling_bytes": effective_ceiling,
        "effective_headroom_bytes": effective_headroom,
        "source_fit_gate_passed": source_fit_gate,
        "project_quota_gate_passed": project_gate,
        "filesystem_availability_gate_passed": filesystem_gate,
        "minimum_effective_quota_gate_passed": minimum_gate,
        "migration_state": MIGRATION_PLANNED,
        "planned_migration_bytes": planned_migration_bytes,
        "migration_classification_authority_passed": True,
        "migration_completion_authority_passed": False,
        "backup_authority_passed": False,
        "live_quota_evidence_passed": minimum_gate,
        "full_c3_authorized": False,
        "dicom_body_transfer_authorized": False,
        "restricted_fields_exported": False,
    }
    validate_aggregate_output(output)
    return output


def validate_aggregate_output(output: Mapping[str, Any]) -> None:
    output = _mapping(output, "OUTPUT_NOT_MAPPING")
    _exact_keys(output, OUTPUT_KEYS, "OUTPUT_SCHEMA_NOT_EXACT")
    if output.get("schema_version") != SCHEMA_VERSION or output.get("units") != EXACT_BYTE_UNIT:
        raise LiveQuotaError("OUTPUT_AUTHORITY_INVALID")
    for key in {
        "receipt_sha256",
        "command_provenance_sha256",
        "raw_evidence_bundle_sha256",
        "tool_identity_bundle_sha256",
        "migration_witness_sha256",
        "migration_classification_sha256",
    }:
        _sha256(output.get(key), f"OUTPUT_HASH_INVALID_{key.upper()}")
    signed_fields = {
        "project_quota_slack_bytes",
        "filesystem_slack_bytes",
        "effective_headroom_bytes",
    }
    for key, value in output.items():
        if key.endswith("_bytes"):
            if key in signed_fields:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise LiveQuotaError(f"OUTPUT_INTEGER_INVALID_{key.upper()}")
            else:
                _integer(value, f"OUTPUT_INTEGER_INVALID_{key.upper()}")
    boolean_fields = {
        "receipt_fresh",
        "capture_identity_verified",
        "command_contract_verified",
        "raw_output_hashes_verified",
        "pquota_research_mapping_verified",
        "quota_unit_authority_verified",
        "pquota_usage_used_as_exact",
        "selected_source_exceeds_quota",
        "source_fit_gate_passed",
        "project_quota_gate_passed",
        "filesystem_availability_gate_passed",
        "minimum_effective_quota_gate_passed",
        "migration_classification_authority_passed",
        "migration_completion_authority_passed",
        "backup_authority_passed",
        "live_quota_evidence_passed",
        "full_c3_authorized",
        "dicom_body_transfer_authorized",
        "restricted_fields_exported",
    }
    for key in boolean_fields:
        _boolean(output.get(key), f"OUTPUT_BOOLEAN_INVALID_{key.upper()}")
    if output.get("status") not in {"PASS_LIVE_QUOTA_GATE", "FAIL_LIVE_QUOTA_GATE"}:
        raise LiveQuotaError("OUTPUT_STATUS_INVALID")
    if output.get("migration_state") != MIGRATION_PLANNED:
        raise LiveQuotaError("OUTPUT_MIGRATION_STATE_INVALID")
    expected_source_exceeds = output["selected_source_bytes"] > output["quota_bytes"]
    expected_source_fit = not expected_source_exceeds
    expected_project_gate = (
        output["quota_bytes"] >= MINIMUM_EFFECTIVE_QUOTA_BYTES
        and output["project_quota_slack_bytes"] >= REQUIRED_HEADROOM_BYTES
    )
    expected_filesystem_gate = output["filesystem_available_bytes"] >= output["filesystem_required_available_bytes"]
    expected_minimum = (
        expected_source_fit
        and expected_project_gate
        and expected_filesystem_gate
        and output["effective_headroom_bytes"] >= REQUIRED_HEADROOM_BYTES
    )
    fixed_authorities = (
        output["receipt_fresh"] is True
        and output["capture_identity_verified"] is True
        and output["command_contract_verified"] is True
        and output["raw_output_hashes_verified"] is True
        and output["pquota_research_mapping_verified"] is True
        and output["quota_unit_authority_verified"] is True
        and output["pquota_usage_used_as_exact"] is False
        and output["migration_classification_authority_passed"] is True
        and output["migration_completion_authority_passed"] is False
        and output["backup_authority_passed"] is False
        and output["full_c3_authorized"] is False
        and output["dicom_body_transfer_authorized"] is False
        and output["restricted_fields_exported"] is False
    )
    exact_arithmetic = (
        output["project_quota_available_bytes"]
        == max(output["quota_bytes"] - output["exact_project_usage_bytes"], 0)
        and output["filesystem_capacity_bytes"]
        == output["filesystem_used_bytes"]
        + output["filesystem_available_bytes"]
        + output["filesystem_unavailable_or_reserved_bytes"]
        and output["project_quota_slack_bytes"]
        == output["quota_bytes"] - output["projected_peak_bytes"]
        and output["filesystem_required_available_bytes"]
        == max(output["projected_peak_bytes"] - output["exact_project_usage_bytes"], 0)
        + output["required_headroom_bytes"]
        and output["filesystem_slack_bytes"]
        == output["filesystem_available_bytes"] - output["filesystem_required_available_bytes"]
        and output["effective_capacity_ceiling_bytes"]
        == min(
            output["quota_bytes"],
            output["exact_project_usage_bytes"] + output["filesystem_available_bytes"],
        )
        and output["effective_headroom_bytes"]
        == output["effective_capacity_ceiling_bytes"] - output["projected_peak_bytes"]
    )
    if (
        not fixed_authorities
        or not exact_arithmetic
        or output["selected_source_bytes"] != SELECTED_SOURCE_BYTES
        or output["projected_peak_bytes"] != FROZEN_PROJECTED_PEAK_BYTES
        or output["required_headroom_bytes"] != REQUIRED_HEADROOM_BYTES
        or output["minimum_effective_quota_bytes"] != MINIMUM_EFFECTIVE_QUOTA_BYTES
        or output["selected_source_exceeds_quota"] != expected_source_exceeds
        or output["source_fit_gate_passed"] != expected_source_fit
        or output["project_quota_gate_passed"] != expected_project_gate
        or output["filesystem_availability_gate_passed"] != expected_filesystem_gate
        or output["minimum_effective_quota_gate_passed"] != expected_minimum
        or output["live_quota_evidence_passed"] != expected_minimum
        or (output["status"] == "PASS_LIVE_QUOTA_GATE") != expected_minimum
    ):
        raise LiveQuotaError("OUTPUT_ARITHMETIC_OR_STATUS_INCONSISTENT")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_new_private(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise LiveQuotaError("OUTPUT_ALREADY_EXISTS_OR_IS_SYMLINK")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise LiveQuotaError("TEMPORARY_OUTPUT_COLLISION")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise LiveQuotaError("OUTPUT_ALREADY_EXISTS_OR_IS_SYMLINK") from exc
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restricted-receipt", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    try:
        aggregate = validate_restricted_evidence(
            args.restricted_receipt, expected_commit=args.expected_commit
        )
        _write_new_private(args.aggregate_output, _json_bytes(aggregate))
    except (LiveQuotaError, OSError) as exc:
        reason = str(exc) if isinstance(exc, LiveQuotaError) else "FILESYSTEM_IO_ERROR"
        print("LIVE_QUOTA_CAPTURE_VALIDATION=FAILED")
        print(f"LIVE_QUOTA_FAILURE_REASON={reason}")
        return 2
    print("LIVE_QUOTA_CAPTURE_VALIDATION=PASS")
    print(
        "MINIMUM_EFFECTIVE_QUOTA_GATE="
        + ("PASS" if aggregate["minimum_effective_quota_gate_passed"] else "FAIL")
    )
    print("MIGRATION_STATE=PLANNED_NOT_EXECUTED")
    print("MIGRATION_COMPLETION_AUTHORITY=NO")
    print("BACKUP_AUTHORITY=NO")
    print("FULL_C3_AUTHORIZED=NO")
    print("DICOM_BODY_TRANSFER_AUTHORIZED=NO")
    return 0 if aggregate["minimum_effective_quota_gate_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
