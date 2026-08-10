#!/usr/bin/env python3
"""Adjudicate post-expansion C3 capacity from immutable and supplemental evidence.

This validator performs no command execution and no network access.  It binds
an immutable Phase 1E-E research-capacity receipt/aggregate to one new
owner-private receipt containing only control-tier ``findmnt``, ``df``, and
non-enumerating ``du`` captures.  The Git-safe output keeps evidence validity,
research quota, physical filesystem capacity, and the backed-control-tier
operational gate as separate claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import capture_lvef_c3_live_quota as prior


SCHEMA_VERSION = 1
RECEIPT_KIND = "LVEF_C3_POST_EXPANSION_CONTROL_TIER_RAW_COMMAND_RECEIPT"
RECEIPT_STATUS = "PASS_READ_ONLY_CONTROL_CAPTURE"
OUTPUT_STATUS = "PASS_POST_EXPANSION_CAPACITY_EVIDENCE"
EXACT_BYTE_UNIT = "BYTES_EXACT_INTEGER"

MINIMUM_CONTROL_TIER_OPTION_BYTES = 25_000_000_000
MINIMUM_OPTION_RESEARCH_QUOTA_BYTES = 1_975_000_000_000
PREFERRED_CONTROL_TIER_OPTION_BYTES = 50_000_000_000
PREFERRED_OPTION_RESEARCH_QUOTA_BYTES = 1_950_000_000_000

CONTROL_POLICY_KEYS = {
    "schema_version",
    "policy_id",
    "additional_control_write_burden_components",
    "maximum_additional_control_file_count_burden",
    "maximum_additional_control_write_burden_bytes",
    "minimum_control_quota_option_bytes",
    "minimum_option_research_quota_bytes",
    "preferred_control_quota_option_bytes",
    "preferred_option_research_quota_bytes",
    "production_additional_research_file_count_upper_bound",
    "purchased_saas_bytes_remaining_on_projectnb",
    "all_substantial_production_writes_must_target_projectnb",
    "current_control_quota_may_be_reduced",
}
CONTROL_BURDEN_COMPONENT_KEYS = {
    "git_pack_repack_and_recovery_transient",
    "authority_and_receipt_growth",
    "environment_checkpoint_control_metadata",
    "operational_contingency",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "receipt_kind",
    "status",
    "captured_at_utc",
    "capture_identity",
    "parent_authority",
    "control_path",
    "commands",
    "no_mutation_attestations",
}
PARENT_AUTHORITY_KEYS = {
    "restricted_receipt_path",
    "restricted_receipt_sha256",
    "restricted_receipt_byte_count",
    "aggregate_path",
    "aggregate_sha256",
    "aggregate_byte_count",
    "governing_commit",
}
COMMAND_KEYS = {"findmnt", "df", "du"}
NO_MUTATION_KEYS = {
    "quota_changed",
    "files_moved",
    "files_deleted",
    "cloud_requests",
    "object_bodies_downloaded",
    "pquota_commands_repeated",
    "research_filesystem_commands_repeated",
}

OUTPUT_KEYS = {
    "schema_version",
    "status",
    "units",
    "parent_receipt_sha256",
    "parent_aggregate_sha256",
    "supplemental_receipt_sha256",
    "supplemental_command_provenance_sha256",
    "supplemental_raw_evidence_bundle_sha256",
    "supplemental_tool_identity_bundle_sha256",
    "control_tier_policy_sha256",
    "capture_fresh",
    "parent_authority_hash_verified",
    "parent_closed_schema_verified",
    "parent_raw_receipt_revalidated",
    "control_command_contract_verified",
    "control_raw_output_hashes_verified",
    "quota_unit_authority_verified",
    "pquota_usage_used_as_exact",
    "quota_allocation_unit_system",
    "management_display_ruling",
    "research_quota_bytes",
    "research_file_quota_reported_count",
    "research_file_usage_reported_count",
    "research_file_quota_available_count",
    "research_additional_file_count_upper_bound",
    "research_file_quota_interpretation",
    "research_file_quota_gate_passed",
    "research_exact_allocated_usage_bytes",
    "research_quota_available_bytes",
    "research_filesystem_capacity_bytes",
    "research_filesystem_used_bytes",
    "research_filesystem_available_bytes",
    "research_filesystem_unavailable_or_reserved_bytes",
    "control_quota_bytes",
    "control_file_quota_reported_count",
    "control_file_usage_reported_count",
    "control_file_quota_available_count",
    "control_additional_file_count_upper_bound",
    "control_file_quota_interpretation",
    "control_file_quota_gate_passed",
    "control_exact_allocated_usage_bytes",
    "control_quota_available_bytes",
    "control_filesystem_capacity_bytes",
    "control_filesystem_used_bytes",
    "control_filesystem_available_bytes",
    "control_filesystem_unavailable_or_reserved_bytes",
    "research_filesystem_identity_sha256",
    "control_filesystem_identity_sha256",
    "research_filesystem_type",
    "control_filesystem_type",
    "filesystem_sources_distinct",
    "mount_targets_distinct",
    "mounted_filesystems_distinct",
    "research_bind_mount",
    "control_bind_mount",
    "selected_source_bytes",
    "projected_peak_bytes",
    "required_headroom_bytes",
    "minimum_effective_quota_bytes",
    "research_incremental_write_bytes",
    "research_filesystem_required_available_bytes",
    "research_projected_peak_slack_bytes",
    "research_filesystem_slack_bytes",
    "research_effective_capacity_ceiling_bytes",
    "research_effective_headroom_bytes",
    "live_quota_evidence_passed",
    "minimum_effective_quota_gate_passed",
    "physical_filesystem_capacity_gate_passed",
    "projected_200gb_reserve_gate_passed",
    "research_capacity_gate_passed",
    "control_tier_quota_evidence_passed",
    "control_tier_operational_burden_authority_bound",
    "control_tier_max_additional_write_burden_bytes",
    "control_tier_available_after_burden_bytes",
    "backed_control_tier_byte_gate_passed",
    "backed_control_tier_file_gate_passed",
    "backed_control_tier_gate_passed",
    "minimum_control_tier_option_bytes",
    "minimum_option_research_quota_bytes",
    "minimum_option_quota_gate_passed",
    "minimum_option_reserve_gate_passed",
    "minimum_option_control_gate_passed",
    "preferred_control_tier_option_bytes",
    "preferred_option_research_quota_bytes",
    "preferred_option_quota_gate_passed",
    "preferred_option_reserve_gate_passed",
    "preferred_option_control_gate_passed",
    "quota_changed",
    "files_moved",
    "files_deleted",
    "cloud_requests",
    "object_bodies_downloaded",
    "pquota_commands_repeated",
    "research_filesystem_commands_repeated",
    "full_c3_authorized",
    "dicom_body_transfer_authorized",
    "restricted_fields_exported",
}


class PostExpansionCapacityError(ValueError):
    """Raised when the composite capacity authority fails closed."""


def _error(code: str) -> PostExpansionCapacityError:
    return PostExpansionCapacityError(code)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(code)
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise _error(code)


def _integer(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _error(code)
    return value


def _signed_integer(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _error(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise _error(code)
    return value


def _string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _error(code)
    return value


def _git_commit(value: Any, code: str = "GIT_COMMIT_INVALID") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _error(code)
    return value


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path) -> tuple[Mapping[str, Any], prior.FileEvidence]:
    try:
        value, evidence = prior._read_json_evidence(path)
    except prior.LiveQuotaError as exc:
        raise _error(f"BOUND_EVIDENCE_INVALID_{exc}") from exc
    return value, evidence


def _validate_bound_file(
    spec: Mapping[str, Any], *, role: str
) -> tuple[Mapping[str, Any], prior.FileEvidence]:
    path = Path(_string(spec.get(f"{role}_path"), f"{role.upper()}_PATH_INVALID"))
    value, evidence = _read_json(path)
    if (
        evidence.sha256
        != _sha256(spec.get(f"{role}_sha256"), f"{role.upper()}_SHA256_INVALID")
        or len(evidence.payload)
        != _integer(
            spec.get(f"{role}_byte_count"), f"{role.upper()}_BYTE_COUNT_INVALID"
        )
    ):
        raise _error(f"{role.upper()}_HASH_OR_SIZE_MISMATCH")
    return value, evidence


def _validate_control_policy(
    path: Path,
) -> tuple[Mapping[str, Any], prior.FileEvidence]:
    try:
        if path.is_symlink() or not path.is_file():
            raise _error("CONTROL_POLICY_NOT_REGULAR_NOFOLLOW_FILE")
        evidence = prior._read_file(
            path.resolve(strict=True),
            owner_private=False,
            maximum_bytes=64 * 1024,
            outside_repository=False,
        )
        policy = _mapping(
            prior.strict_json_loads(evidence.payload.decode("utf-8")),
            "CONTROL_POLICY_ROOT_NOT_MAPPING",
        )
    except (OSError, UnicodeDecodeError, prior.LiveQuotaError) as exc:
        raise _error("CONTROL_POLICY_READ_FAILED") from exc
    _exact_keys(policy, CONTROL_POLICY_KEYS, "CONTROL_POLICY_SCHEMA_NOT_EXACT")
    components = _mapping(
        policy.get("additional_control_write_burden_components"),
        "CONTROL_POLICY_COMPONENTS_NOT_MAPPING",
    )
    _exact_keys(
        components,
        CONTROL_BURDEN_COMPONENT_KEYS,
        "CONTROL_POLICY_COMPONENT_SCHEMA_NOT_EXACT",
    )
    values = {
        key: _integer(value, f"CONTROL_POLICY_INTEGER_INVALID_{key.upper()}")
        for key, value in components.items()
    }
    maximum = _integer(
        policy.get("maximum_additional_control_write_burden_bytes"),
        "CONTROL_POLICY_MAXIMUM_BURDEN_INVALID",
    )
    if sum(values.values()) != maximum or maximum != 10_000_000_000:
        raise _error("CONTROL_POLICY_BURDEN_ARITHMETIC_INVALID")
    exact_values = {
        "schema_version": 1,
        "policy_id": "lvef_c3_backed_control_tier_v1",
        "minimum_control_quota_option_bytes": MINIMUM_CONTROL_TIER_OPTION_BYTES,
        "minimum_option_research_quota_bytes": MINIMUM_OPTION_RESEARCH_QUOTA_BYTES,
        "preferred_control_quota_option_bytes": PREFERRED_CONTROL_TIER_OPTION_BYTES,
        "preferred_option_research_quota_bytes": PREFERRED_OPTION_RESEARCH_QUOTA_BYTES,
        "purchased_saas_bytes_remaining_on_projectnb": 1_000_000_000_000,
        "all_substantial_production_writes_must_target_projectnb": True,
        "current_control_quota_may_be_reduced": False,
        "maximum_additional_control_file_count_burden": 100_000,
        "production_additional_research_file_count_upper_bound": 3_500_000,
    }
    for key, expected in exact_values.items():
        if policy.get(key) != expected:
            raise _error(f"CONTROL_POLICY_FIXED_VALUE_INVALID_{key.upper()}")
    return policy, evidence


def _parse_findmnt_full(
    stdout: prior.FileEvidence, expected_path: str
) -> tuple[str, str, str, str, bool]:
    try:
        payload = _mapping(
            prior.strict_json_loads(
                prior._decode_text(stdout, "FINDMNT_OUTPUT_UTF8_INVALID")
            ),
            "FINDMNT_OUTPUT_ROOT_NOT_MAPPING",
        )
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc
    _exact_keys(payload, {"filesystems"}, "FINDMNT_OUTPUT_SCHEMA_NOT_EXACT")
    rows = payload.get("filesystems")
    if not isinstance(rows, list) or len(rows) != 1:
        raise _error("FINDMNT_FILESYSTEM_ROW_NOT_UNIQUE")
    row = _mapping(rows[0], "FINDMNT_FILESYSTEM_ROW_NOT_MAPPING")
    _exact_keys(
        row,
        {"source", "target", "fstype", "options"},
        "FINDMNT_ROW_SCHEMA_NOT_EXACT",
    )
    source = _string(row.get("source"), "FINDMNT_SOURCE_INVALID")
    target = _string(row.get("target"), "FINDMNT_TARGET_INVALID")
    fstype = _string(row.get("fstype"), "FINDMNT_FSTYPE_INVALID")
    options = _string(row.get("options"), "FINDMNT_OPTIONS_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,32}", fstype):
        raise _error("FINDMNT_FSTYPE_NOT_EXPORT_SAFE")
    try:
        Path(expected_path).relative_to(Path(target))
    except ValueError as exc:
        raise _error("PATH_NOT_ON_FINDMNT_TARGET") from exc
    identity = _digest(_canonical({"source": source, "target": target, "fstype": fstype}))
    option_tokens = {token.strip().casefold() for token in options.split(",")}
    is_bind = bool(option_tokens.intersection({"bind", "rbind"}))
    return source, target, fstype, identity, is_bind


def _native_pquota_rows(payload: str) -> tuple[list[str], int]:
    lines = payload.splitlines()
    headers: list[int] = []
    for index in range(len(lines) - 2):
        if [item.casefold() for item in lines[index].split()] != [
            "quota",
            "quota",
            "usage",
            "usage",
        ]:
            continue
        if [item.casefold() for item in lines[index + 1].split()] != [
            "project",
            "space",
            "(gb)",
            "(files)",
            "(gb)",
            "(files)",
        ]:
            continue
        separators = lines[index + 2].split()
        if (
            len(separators) != 5
            or any(len(item) < 3 or set(item) != {"-"} for item in separators)
        ):
            raise _error("PQUOTA_COLUMN_SEPARATOR_INVALID")
        headers.append(index)
    if len(headers) != 1:
        raise _error(
            "PQUOTA_COLUMN_HEADER_NOT_FOUND"
            if not headers
            else "PQUOTA_COLUMN_HEADER_NOT_UNIQUE"
        )
    return lines, headers[0]


def _parse_native_quota_row(
    parent_receipt: Mapping[str, Any], *, row_prefix: str
) -> tuple[int, int, int]:
    mapping = _mapping(
        parent_receipt.get("pquota_research_mapping"),
        "PARENT_PQUOTA_MAPPING_NOT_MAPPING",
    )
    principal = _string(mapping.get("quota_principal"), "PQUOTA_PRINCIPAL_INVALID")
    commands = _mapping(parent_receipt.get("commands"), "PARENT_COMMANDS_NOT_MAPPING")
    pquota_record = _mapping(commands.get("pquota"), "PARENT_PQUOTA_RECORD_INVALID")
    try:
        stdout = prior._validate_raw_file_spec(pquota_record.get("stdout"))
        text = prior._decode_text(stdout, "PQUOTA_OUTPUT_UTF8_INVALID")
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc
    lines, header_index = _native_pquota_rows(text)
    if row_prefix not in {"/rproject", "/rprojectnb"}:
        raise _error("PQUOTA_ROW_PREFIX_INVALID")
    expected_row = f"{row_prefix}/{principal}"
    rows = [
        (index, line.split())
        for index, line in enumerate(lines)
        if line.split() and line.split()[0].startswith(f"{row_prefix}/")
    ]
    if len(rows) != 1:
        raise _error("PQUOTA_FILESYSTEM_ROW_NOT_UNIQUE")
    row_index, fields = rows[0]
    if row_index <= header_index + 2:
        raise _error("PQUOTA_ROW_OUTSIDE_NATIVE_TABLE")
    if len(fields) != 5 or fields[0] != expected_row:
        raise _error("PQUOTA_ROW_LAYOUT_INVALID")
    quota_text, quota_files, usage_text, usage_files = fields[1:]
    try:
        quota_files_decimal = Decimal(quota_files)
        Decimal(usage_text)
        usage_files_decimal = Decimal(usage_files)
        quota_decimal = Decimal(quota_text) * prior.DECIMAL_GB_BYTES
    except InvalidOperation as exc:
        raise _error("PQUOTA_DECIMAL_CONVERSION_INVALID") from exc
    if (
        quota_decimal != quota_decimal.to_integral_value()
        or quota_files_decimal != quota_files_decimal.to_integral_value()
        or usage_files_decimal != usage_files_decimal.to_integral_value()
        or quota_files_decimal < 0
        or usage_files_decimal < 0
    ):
        raise _error("PQUOTA_QUOTA_OR_FILE_COUNT_NOT_INTEGER")
    return int(quota_decimal), int(quota_files_decimal), int(usage_files_decimal)


def _parse_control_quota(parent_receipt: Mapping[str, Any]) -> tuple[int, int, int]:
    return _parse_native_quota_row(parent_receipt, row_prefix="/rproject")


def _parent_capture_time(parent_receipt: Mapping[str, Any]) -> datetime:
    try:
        return prior._utc_second(parent_receipt.get("captured_at_utc"))
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc


def validate_composite_capacity(
    supplemental_receipt_path: Path,
    *,
    control_policy_path: Path,
    expected_commit: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate immutable research authority plus one control-only capture."""

    expected_commit = _git_commit(expected_commit)
    control_policy, control_policy_file = _validate_control_policy(control_policy_path)
    receipt, receipt_file = _read_json(supplemental_receipt_path)
    _exact_keys(receipt, TOP_LEVEL_KEYS, "SUPPLEMENTAL_RECEIPT_SCHEMA_NOT_EXACT")
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("receipt_kind") != RECEIPT_KIND
        or receipt.get("status") != RECEIPT_STATUS
    ):
        raise _error("SUPPLEMENTAL_RECEIPT_AUTHORITY_INVALID")
    try:
        captured_at = prior._utc_second(receipt.get("captured_at_utc"))
        prior._validate_freshness(captured_at, prior._normalize_now(now_utc))
        prior._validate_capture_identity(
            _mapping(receipt.get("capture_identity"), "CAPTURE_IDENTITY_NOT_MAPPING"),
            expected_commit,
        )
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc

    parent = _mapping(receipt.get("parent_authority"), "PARENT_AUTHORITY_NOT_MAPPING")
    _exact_keys(parent, PARENT_AUTHORITY_KEYS, "PARENT_AUTHORITY_SCHEMA_NOT_EXACT")
    parent_commit = _git_commit(
        parent.get("governing_commit"), "PARENT_GOVERNING_COMMIT_INVALID"
    )
    parent_receipt, parent_receipt_file = _validate_bound_file(
        parent, role="restricted_receipt"
    )
    parent_aggregate, parent_aggregate_file = _validate_bound_file(
        parent, role="aggregate"
    )
    try:
        prior.validate_aggregate_output(parent_aggregate)
        rederived_parent = prior.validate_restricted_evidence(
            parent_receipt_file.path,
            expected_commit=parent_commit,
            now_utc=_parent_capture_time(parent_receipt),
        )
    except prior.LiveQuotaError as exc:
        raise _error(f"PARENT_AUTHORITY_REVALIDATION_FAILED_{exc}") from exc
    if dict(parent_aggregate) != rederived_parent:
        raise _error("PARENT_AGGREGATE_NOT_EXACT_REDERIVATION")
    if parent_aggregate.get("status") != "PASS_LIVE_QUOTA_GATE":
        raise _error("PARENT_RESEARCH_CAPACITY_DID_NOT_PASS")

    control_path = _string(receipt.get("control_path"), "CONTROL_PATH_INVALID")
    if not Path(control_path).is_absolute():
        raise _error("CONTROL_PATH_NOT_ABSOLUTE")
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
        "findmnt": [
            executable_paths["findmnt"],
            "--json",
            "--target",
            control_path,
            "--output",
            "SOURCE,TARGET,FSTYPE,OPTIONS",
        ],
        "df": [
            executable_paths["df"],
            "-B1",
            "--output=source,size,used,avail,target",
            control_path,
        ],
        "du": [executable_paths["du"], "-x", "-s", "-B1", control_path],
    }
    validated: dict[
        str, tuple[Mapping[str, Any], prior.FileEvidence, prior.FileEvidence]
    ] = {}
    try:
        for role in sorted(COMMAND_KEYS):
            validated[role] = prior._validate_command_record(
                role, commands[role], expected_argv=expected_argv[role]
            )
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc

    no_mutation = _mapping(
        receipt.get("no_mutation_attestations"), "NO_MUTATION_NOT_MAPPING"
    )
    _exact_keys(no_mutation, NO_MUTATION_KEYS, "NO_MUTATION_SCHEMA_NOT_EXACT")
    if (
        _boolean(no_mutation.get("quota_changed"), "QUOTA_CHANGED_INVALID")
        or _integer(no_mutation.get("files_moved"), "FILES_MOVED_INVALID") != 0
        or _integer(no_mutation.get("files_deleted"), "FILES_DELETED_INVALID") != 0
        or _integer(no_mutation.get("cloud_requests"), "CLOUD_REQUESTS_INVALID") != 0
        or _integer(
            no_mutation.get("object_bodies_downloaded"), "BODY_DOWNLOADS_INVALID"
        )
        != 0
        or _integer(
            no_mutation.get("pquota_commands_repeated"), "PQUOTA_REPEAT_INVALID"
        )
        != 0
        or _integer(
            no_mutation.get("research_filesystem_commands_repeated"),
            "RESEARCH_COMMAND_REPEAT_INVALID",
        )
        != 0
    ):
        raise _error("READ_ONLY_OR_NO_REPEAT_BOUNDARY_VIOLATED")

    try:
        control_source, control_target, control_fstype, control_identity, control_bind = (
            _parse_findmnt_full(validated["findmnt"][1], control_path)
        )
        control_capacity, control_used, control_available, control_reserved = (
            prior._parse_df(validated["df"][1], control_source, control_target)
        )
        control_exact_usage = prior._parse_du(validated["du"][1], control_path)
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc

    parent_commands = _mapping(
        parent_receipt.get("commands"), "PARENT_COMMANDS_NOT_MAPPING"
    )
    try:
        research_stdout = prior._validate_raw_file_spec(
            _mapping(parent_commands.get("findmnt"), "PARENT_FINDMNT_INVALID").get(
                "stdout"
            )
        )
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc
    research_path = _string(parent_receipt.get("research_path"), "RESEARCH_PATH_INVALID")
    research_source, research_target, research_fstype, research_identity, research_bind = (
        _parse_findmnt_full(research_stdout, research_path)
    )
    control_quota, control_file_quota, control_file_usage = _parse_control_quota(
        parent_receipt
    )
    control_quota_available = max(control_quota - control_exact_usage, 0)

    command_provenance = {
        role: {
            "argv_sha256": validated[role][0]["argv_sha256"],
            "tool": prior._tool_identity_payload(validated[role][0]),
            "stdout_sha256": validated[role][1].sha256,
            "stderr_sha256": validated[role][2].sha256,
        }
        for role in sorted(COMMAND_KEYS)
    }
    raw_evidence = {
        role: {
            "stdout_sha256": validated[role][1].sha256,
            "stdout_bytes": len(validated[role][1].payload),
            "stderr_sha256": validated[role][2].sha256,
            "stderr_bytes": len(validated[role][2].payload),
        }
        for role in sorted(COMMAND_KEYS)
    }
    tool_identity = {
        role: prior._tool_identity_payload(validated[role][0])
        for role in sorted(COMMAND_KEYS)
    }

    research_quota = _integer(parent_aggregate.get("quota_bytes"), "RESEARCH_QUOTA_INVALID")
    (
        research_quota_from_receipt,
        research_file_quota,
        research_file_usage,
    ) = _parse_native_quota_row(parent_receipt, row_prefix="/rprojectnb")
    if research_quota_from_receipt != research_quota:
        raise _error("RESEARCH_QUOTA_PARENT_RECEIPT_AGGREGATE_MISMATCH")
    research_usage = _integer(
        parent_aggregate.get("exact_project_usage_bytes"), "RESEARCH_USAGE_INVALID"
    )
    projected_peak = prior.FROZEN_PROJECTED_PEAK_BYTES
    reserve = prior.REQUIRED_HEADROOM_BYTES
    minimum = prior.MINIMUM_EFFECTIVE_QUOTA_BYTES
    incremental = max(projected_peak - research_usage, 0)
    filesystem_required = incremental + reserve
    research_fs_available = _integer(
        parent_aggregate.get("filesystem_available_bytes"),
        "RESEARCH_FILESYSTEM_AVAILABLE_INVALID",
    )
    quota_gate = research_quota >= minimum
    filesystem_gate = research_fs_available >= filesystem_required
    reserve_gate = (
        research_quota - projected_peak >= reserve
        and _signed_integer(
            parent_aggregate.get("effective_headroom_bytes"),
            "RESEARCH_EFFECTIVE_HEADROOM_INVALID",
        )
        >= reserve
    )
    research_file_upper_bound = _integer(
        control_policy["production_additional_research_file_count_upper_bound"],
        "RESEARCH_FILE_UPPER_BOUND_INVALID",
    )
    research_file_available = max(research_file_quota - research_file_usage, 0)
    research_file_interpretation = (
        "FINITE_LIMIT" if research_file_quota > 0 else "ZERO_REPORTED_UNRESOLVED"
    )
    research_file_gate = (
        research_file_quota > 0
        and research_file_available >= research_file_upper_bound
    )
    research_gate = quota_gate and filesystem_gate and reserve_gate
    sources_distinct = research_source != control_source
    targets_distinct = research_target != control_target
    control_burden = _integer(
        control_policy["maximum_additional_control_write_burden_bytes"],
        "CONTROL_POLICY_MAXIMUM_BURDEN_INVALID",
    )
    control_available_after_burden = control_quota_available - control_burden
    control_file_burden = _integer(
        control_policy["maximum_additional_control_file_count_burden"],
        "CONTROL_FILE_BURDEN_INVALID",
    )
    control_file_available = max(control_file_quota - control_file_usage, 0)
    control_file_interpretation = (
        "FINITE_LIMIT" if control_file_quota > 0 else "ZERO_REPORTED_UNRESOLVED"
    )
    control_file_gate = (
        control_file_quota > 0 and control_file_available >= control_file_burden
    )
    backed_control_byte_gate = control_available_after_burden >= 0
    backed_control_gate = backed_control_byte_gate and control_file_gate
    minimum_option_control_gate = (
        MINIMUM_CONTROL_TIER_OPTION_BYTES - control_exact_usage >= control_burden
        and control_file_gate
    )
    preferred_option_control_gate = (
        PREFERRED_CONTROL_TIER_OPTION_BYTES - control_exact_usage >= control_burden
        and control_file_gate
    )

    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": OUTPUT_STATUS,
        "units": EXACT_BYTE_UNIT,
        "parent_receipt_sha256": parent_receipt_file.sha256,
        "parent_aggregate_sha256": parent_aggregate_file.sha256,
        "supplemental_receipt_sha256": receipt_file.sha256,
        "supplemental_command_provenance_sha256": _digest(_canonical(command_provenance)),
        "supplemental_raw_evidence_bundle_sha256": _digest(_canonical(raw_evidence)),
        "supplemental_tool_identity_bundle_sha256": _digest(_canonical(tool_identity)),
        "control_tier_policy_sha256": control_policy_file.sha256,
        "capture_fresh": True,
        "parent_authority_hash_verified": True,
        "parent_closed_schema_verified": True,
        "parent_raw_receipt_revalidated": True,
        "control_command_contract_verified": True,
        "control_raw_output_hashes_verified": True,
        "quota_unit_authority_verified": True,
        "pquota_usage_used_as_exact": False,
        "quota_allocation_unit_system": "DECIMAL_SI",
        "management_display_ruling": "QUOTA_DECIMAL_GB_USAGE_ROUNDED_DU_B1_EXACT_ALLOCATED",
        "research_quota_bytes": research_quota,
        "research_file_quota_reported_count": research_file_quota,
        "research_file_usage_reported_count": research_file_usage,
        "research_file_quota_available_count": research_file_available,
        "research_additional_file_count_upper_bound": research_file_upper_bound,
        "research_file_quota_interpretation": research_file_interpretation,
        "research_file_quota_gate_passed": research_file_gate,
        "research_exact_allocated_usage_bytes": research_usage,
        "research_quota_available_bytes": max(research_quota - research_usage, 0),
        "research_filesystem_capacity_bytes": parent_aggregate["filesystem_capacity_bytes"],
        "research_filesystem_used_bytes": parent_aggregate["filesystem_used_bytes"],
        "research_filesystem_available_bytes": research_fs_available,
        "research_filesystem_unavailable_or_reserved_bytes": parent_aggregate[
            "filesystem_unavailable_or_reserved_bytes"
        ],
        "control_quota_bytes": control_quota,
        "control_file_quota_reported_count": control_file_quota,
        "control_file_usage_reported_count": control_file_usage,
        "control_file_quota_available_count": control_file_available,
        "control_additional_file_count_upper_bound": control_file_burden,
        "control_file_quota_interpretation": control_file_interpretation,
        "control_file_quota_gate_passed": control_file_gate,
        "control_exact_allocated_usage_bytes": control_exact_usage,
        "control_quota_available_bytes": control_quota_available,
        "control_filesystem_capacity_bytes": control_capacity,
        "control_filesystem_used_bytes": control_used,
        "control_filesystem_available_bytes": control_available,
        "control_filesystem_unavailable_or_reserved_bytes": control_reserved,
        "research_filesystem_identity_sha256": research_identity,
        "control_filesystem_identity_sha256": control_identity,
        "research_filesystem_type": research_fstype,
        "control_filesystem_type": control_fstype,
        "filesystem_sources_distinct": sources_distinct,
        "mount_targets_distinct": targets_distinct,
        "mounted_filesystems_distinct": sources_distinct and targets_distinct,
        "research_bind_mount": research_bind,
        "control_bind_mount": control_bind,
        "selected_source_bytes": prior.SELECTED_SOURCE_BYTES,
        "projected_peak_bytes": projected_peak,
        "required_headroom_bytes": reserve,
        "minimum_effective_quota_bytes": minimum,
        "research_incremental_write_bytes": incremental,
        "research_filesystem_required_available_bytes": filesystem_required,
        "research_projected_peak_slack_bytes": research_quota - projected_peak,
        "research_filesystem_slack_bytes": research_fs_available - filesystem_required,
        "research_effective_capacity_ceiling_bytes": parent_aggregate[
            "effective_capacity_ceiling_bytes"
        ],
        "research_effective_headroom_bytes": parent_aggregate[
            "effective_headroom_bytes"
        ],
        "live_quota_evidence_passed": True,
        "minimum_effective_quota_gate_passed": quota_gate,
        "physical_filesystem_capacity_gate_passed": filesystem_gate,
        "projected_200gb_reserve_gate_passed": reserve_gate,
        "research_capacity_gate_passed": research_gate,
        "control_tier_quota_evidence_passed": True,
        "control_tier_operational_burden_authority_bound": True,
        "control_tier_max_additional_write_burden_bytes": control_burden,
        "control_tier_available_after_burden_bytes": control_available_after_burden,
        "backed_control_tier_byte_gate_passed": backed_control_byte_gate,
        "backed_control_tier_file_gate_passed": control_file_gate,
        "backed_control_tier_gate_passed": backed_control_gate,
        "minimum_control_tier_option_bytes": MINIMUM_CONTROL_TIER_OPTION_BYTES,
        "minimum_option_research_quota_bytes": MINIMUM_OPTION_RESEARCH_QUOTA_BYTES,
        "minimum_option_quota_gate_passed": MINIMUM_OPTION_RESEARCH_QUOTA_BYTES >= minimum,
        "minimum_option_reserve_gate_passed": MINIMUM_OPTION_RESEARCH_QUOTA_BYTES - projected_peak >= reserve,
        "minimum_option_control_gate_passed": minimum_option_control_gate,
        "preferred_control_tier_option_bytes": PREFERRED_CONTROL_TIER_OPTION_BYTES,
        "preferred_option_research_quota_bytes": PREFERRED_OPTION_RESEARCH_QUOTA_BYTES,
        "preferred_option_quota_gate_passed": PREFERRED_OPTION_RESEARCH_QUOTA_BYTES >= minimum,
        "preferred_option_reserve_gate_passed": PREFERRED_OPTION_RESEARCH_QUOTA_BYTES - projected_peak >= reserve,
        "preferred_option_control_gate_passed": preferred_option_control_gate,
        "quota_changed": False,
        "files_moved": 0,
        "files_deleted": 0,
        "cloud_requests": 0,
        "object_bodies_downloaded": 0,
        "pquota_commands_repeated": 0,
        "research_filesystem_commands_repeated": 0,
        "full_c3_authorized": False,
        "dicom_body_transfer_authorized": False,
        "restricted_fields_exported": False,
    }
    validate_aggregate_output(output)
    return output


def validate_aggregate_output(output: Mapping[str, Any]) -> None:
    output = _mapping(output, "OUTPUT_NOT_MAPPING")
    _exact_keys(output, OUTPUT_KEYS, "OUTPUT_SCHEMA_NOT_EXACT")
    if (
        output.get("schema_version") != SCHEMA_VERSION
        or output.get("status") != OUTPUT_STATUS
        or output.get("units") != EXACT_BYTE_UNIT
    ):
        raise _error("OUTPUT_AUTHORITY_INVALID")
    for key in OUTPUT_KEYS:
        value = output[key]
        if key.endswith("_sha256"):
            _sha256(value, f"OUTPUT_HASH_INVALID_{key.upper()}")
        elif key.endswith("_count"):
            _integer(value, f"OUTPUT_INTEGER_INVALID_{key.upper()}")
        elif key.endswith("_bytes"):
            if key in {
                "research_projected_peak_slack_bytes",
                "research_filesystem_slack_bytes",
                "research_effective_headroom_bytes",
                "control_tier_available_after_burden_bytes",
            }:
                _signed_integer(value, f"OUTPUT_INTEGER_INVALID_{key.upper()}")
            else:
                _integer(value, f"OUTPUT_INTEGER_INVALID_{key.upper()}")
    true_flags = {
        "capture_fresh",
        "parent_authority_hash_verified",
        "parent_closed_schema_verified",
        "parent_raw_receipt_revalidated",
        "control_command_contract_verified",
        "control_raw_output_hashes_verified",
        "quota_unit_authority_verified",
        "live_quota_evidence_passed",
        "control_tier_quota_evidence_passed",
        "control_tier_operational_burden_authority_bound",
        "minimum_option_quota_gate_passed",
        "minimum_option_reserve_gate_passed",
        "minimum_option_control_gate_passed",
        "preferred_option_quota_gate_passed",
        "preferred_option_reserve_gate_passed",
        "preferred_option_control_gate_passed",
    }
    false_flags = {
        "pquota_usage_used_as_exact",
        "quota_changed",
        "full_c3_authorized",
        "dicom_body_transfer_authorized",
        "restricted_fields_exported",
    }
    for key in true_flags:
        if _boolean(output.get(key), f"OUTPUT_BOOLEAN_INVALID_{key.upper()}") is not True:
            raise _error("OUTPUT_FIXED_TRUE_AUTHORITY_INVALID")
    for key in false_flags:
        if _boolean(output.get(key), f"OUTPUT_BOOLEAN_INVALID_{key.upper()}") is not False:
            raise _error("OUTPUT_FIXED_FALSE_AUTHORITY_INVALID")
    for key in {
        "filesystem_sources_distinct",
        "mount_targets_distinct",
        "mounted_filesystems_distinct",
        "research_bind_mount",
        "control_bind_mount",
        "minimum_effective_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_capacity_gate_passed",
        "backed_control_tier_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "research_file_quota_gate_passed",
        "control_file_quota_gate_passed",
    }:
        _boolean(output.get(key), f"OUTPUT_BOOLEAN_INVALID_{key.upper()}")
    for key in {
        "files_moved",
        "files_deleted",
        "cloud_requests",
        "object_bodies_downloaded",
        "pquota_commands_repeated",
        "research_filesystem_commands_repeated",
    }:
        if _integer(output.get(key), f"OUTPUT_COUNTER_INVALID_{key.upper()}") != 0:
            raise _error("OUTPUT_NO_MUTATION_COUNTER_INVALID")
    if (
        output.get("quota_allocation_unit_system") != "DECIMAL_SI"
        or output.get("management_display_ruling")
        != "QUOTA_DECIMAL_GB_USAGE_ROUNDED_DU_B1_EXACT_ALLOCATED"
        or output.get("research_file_quota_interpretation")
        not in {"FINITE_LIMIT", "ZERO_REPORTED_UNRESOLVED"}
        or output.get("control_file_quota_interpretation")
        not in {"FINITE_LIMIT", "ZERO_REPORTED_UNRESOLVED"}
        or not re.fullmatch(r"[A-Za-z0-9._+-]{1,32}", str(output.get("research_filesystem_type", "")))
        or not re.fullmatch(r"[A-Za-z0-9._+-]{1,32}", str(output.get("control_filesystem_type", "")))
    ):
        raise _error("OUTPUT_UNIT_OR_FILESYSTEM_RULING_INVALID")

    expected_quota = output["research_quota_bytes"] >= prior.MINIMUM_EFFECTIVE_QUOTA_BYTES
    expected_fs = output["research_filesystem_available_bytes"] >= output[
        "research_filesystem_required_available_bytes"
    ]
    expected_reserve = (
        output["research_projected_peak_slack_bytes"] >= prior.REQUIRED_HEADROOM_BYTES
        and output["research_effective_headroom_bytes"] >= prior.REQUIRED_HEADROOM_BYTES
    )
    expected_research = expected_quota and expected_fs and expected_reserve
    exact_arithmetic = (
        output["research_quota_available_bytes"]
        == max(
            output["research_quota_bytes"]
            - output["research_exact_allocated_usage_bytes"],
            0,
        )
        and output["control_quota_available_bytes"]
        == max(
            output["control_quota_bytes"]
            - output["control_exact_allocated_usage_bytes"],
            0,
        )
        and output["research_file_quota_available_count"]
        == max(
            output["research_file_quota_reported_count"]
            - output["research_file_usage_reported_count"],
            0,
        )
        and output["control_file_quota_available_count"]
        == max(
            output["control_file_quota_reported_count"]
            - output["control_file_usage_reported_count"],
            0,
        )
        and output["research_file_quota_gate_passed"]
        == (
            output["research_file_quota_reported_count"] > 0
            and output["research_file_quota_available_count"]
            >= output["research_additional_file_count_upper_bound"]
        )
        and output["research_file_quota_interpretation"]
        == (
            "FINITE_LIMIT"
            if output["research_file_quota_reported_count"] > 0
            else "ZERO_REPORTED_UNRESOLVED"
        )
        and output["control_file_quota_gate_passed"]
        == (
            output["control_file_quota_reported_count"] > 0
            and output["control_file_quota_available_count"]
            >= output["control_additional_file_count_upper_bound"]
        )
        and output["control_file_quota_interpretation"]
        == (
            "FINITE_LIMIT"
            if output["control_file_quota_reported_count"] > 0
            else "ZERO_REPORTED_UNRESOLVED"
        )
        and output["research_filesystem_capacity_bytes"]
        == output["research_filesystem_used_bytes"]
        + output["research_filesystem_available_bytes"]
        + output["research_filesystem_unavailable_or_reserved_bytes"]
        and output["control_filesystem_capacity_bytes"]
        == output["control_filesystem_used_bytes"]
        + output["control_filesystem_available_bytes"]
        + output["control_filesystem_unavailable_or_reserved_bytes"]
        and output["research_incremental_write_bytes"]
        == max(
            output["projected_peak_bytes"]
            - output["research_exact_allocated_usage_bytes"],
            0,
        )
        and output["research_filesystem_required_available_bytes"]
        == output["research_incremental_write_bytes"] + output["required_headroom_bytes"]
        and output["research_projected_peak_slack_bytes"]
        == output["research_quota_bytes"] - output["projected_peak_bytes"]
        and output["research_filesystem_slack_bytes"]
        == output["research_filesystem_available_bytes"]
        - output["research_filesystem_required_available_bytes"]
        and output["mounted_filesystems_distinct"]
        == (output["filesystem_sources_distinct"] and output["mount_targets_distinct"])
        and output["control_tier_available_after_burden_bytes"]
        == output["control_quota_available_bytes"]
        - output["control_tier_max_additional_write_burden_bytes"]
        and output["backed_control_tier_gate_passed"]
        == (
            output["backed_control_tier_byte_gate_passed"]
            and output["backed_control_tier_file_gate_passed"]
        )
        and output["backed_control_tier_byte_gate_passed"]
        == (output["control_tier_available_after_burden_bytes"] >= 0)
        and output["backed_control_tier_file_gate_passed"]
        == output["control_file_quota_gate_passed"]
        and output["minimum_option_control_gate_passed"]
        == (
            output["minimum_control_tier_option_bytes"]
            - output["control_exact_allocated_usage_bytes"]
            >= output["control_tier_max_additional_write_burden_bytes"]
            and output["control_file_quota_gate_passed"]
        )
        and output["preferred_option_control_gate_passed"]
        == (
            output["preferred_control_tier_option_bytes"]
            - output["control_exact_allocated_usage_bytes"]
            >= output["control_tier_max_additional_write_burden_bytes"]
            and output["control_file_quota_gate_passed"]
        )
    )
    if (
        not exact_arithmetic
        or output["selected_source_bytes"] != prior.SELECTED_SOURCE_BYTES
        or output["projected_peak_bytes"] != prior.FROZEN_PROJECTED_PEAK_BYTES
        or output["required_headroom_bytes"] != prior.REQUIRED_HEADROOM_BYTES
        or output["minimum_effective_quota_bytes"]
        != prior.MINIMUM_EFFECTIVE_QUOTA_BYTES
        or output["minimum_effective_quota_gate_passed"] != expected_quota
        or output["physical_filesystem_capacity_gate_passed"] != expected_fs
        or output["projected_200gb_reserve_gate_passed"] != expected_reserve
        or output["research_capacity_gate_passed"] != expected_research
        or output["minimum_control_tier_option_bytes"]
        != MINIMUM_CONTROL_TIER_OPTION_BYTES
        or output["minimum_option_research_quota_bytes"]
        != MINIMUM_OPTION_RESEARCH_QUOTA_BYTES
        or output["preferred_control_tier_option_bytes"]
        != PREFERRED_CONTROL_TIER_OPTION_BYTES
        or output["preferred_option_research_quota_bytes"]
        != PREFERRED_OPTION_RESEARCH_QUOTA_BYTES
        or output["control_tier_max_additional_write_burden_bytes"]
        != 10_000_000_000
        or output["control_additional_file_count_upper_bound"] != 100_000
        or output["research_additional_file_count_upper_bound"] != 3_500_000
    ):
        raise _error("OUTPUT_ARITHMETIC_OR_GATE_INCONSISTENT")


def _write_new_private(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        prior._write_new_private(path, payload)
    except prior.LiveQuotaError as exc:
        raise _error(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supplemental-receipt", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--control-policy", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    try:
        output = validate_composite_capacity(
            args.supplemental_receipt,
            control_policy_path=args.control_policy,
            expected_commit=args.expected_commit,
        )
        _write_new_private(args.aggregate_output, output)
    except (PostExpansionCapacityError, OSError) as exc:
        reason = str(exc) if isinstance(exc, PostExpansionCapacityError) else "FILESYSTEM_IO_ERROR"
        print("POST_EXPANSION_CAPACITY_VALIDATION=FAILED")
        print(f"POST_EXPANSION_CAPACITY_FAILURE_REASON={reason}")
        return 2
    print("POST_EXPANSION_CAPACITY_VALIDATION=PASS")
    print(
        "MINIMUM_EFFECTIVE_QUOTA_GATE="
        + ("PASS" if output["minimum_effective_quota_gate_passed"] else "FAIL")
    )
    print(
        "PHYSICAL_FILESYSTEM_CAPACITY_GATE="
        + ("PASS" if output["physical_filesystem_capacity_gate_passed"] else "FAIL")
    )
    print(
        "PROJECTED_200GB_RESERVE_GATE="
        + ("PASS" if output["projected_200gb_reserve_gate_passed"] else "FAIL")
    )
    print(
        "BACKED_CONTROL_TIER_GATE="
        + ("PASS" if output["backed_control_tier_gate_passed"] else "FAIL")
    )
    print("FULL_C3_AUTHORIZED=NO")
    print("DICOM_BODY_TRANSFER_AUTHORIZED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
