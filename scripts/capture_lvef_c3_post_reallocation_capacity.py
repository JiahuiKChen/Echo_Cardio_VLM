#!/usr/bin/env python3
"""Capture and adjudicate exact SCC post-reallocation capacity offline.

The only subprocesses implemented here are the fixed read-only commands
``pquota``, ``findmnt`` and ``df``.  The native quota file is read without
following symlinks so its integer-KiB FILESET rows, rather than the rounded
human display, govern byte and file-count arithmetic.  No directory walk,
``du``, cloud request, scheduler operation, or scientific-data read exists.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence


SCHEMA_VERSION = 2
RECEIPT_TYPE = "lvef_c3_post_reallocation_capacity_receipt_v2"
RECEIPT_STATUS = "PASS_READ_ONLY_POST_REALLOCATION_CAPACITY_CAPTURE"
AGGREGATE_TYPE = "lvef_c3_post_reallocation_capacity_summary_v2"
AGGREGATE_STATUS = "PASS_POST_REALLOCATION_CAPACITY"
ATTEMPT_RE = re.compile(
    r"^lvef_multitask_phase1ef_post_reallocation_lock_attempt_[0-9]{3}$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SELECTED_SOURCE_BYTES = 1_216_569_133_322
PROJECTED_PEAK_BYTES = 1_611_642_076_332
REQUIRED_FREE_HEADROOM_BYTES = 200_000_000_000
MINIMUM_EFFECTIVE_QUOTA_BYTES = 1_811_642_076_332
PREFERRED_RESEARCH_QUOTA_BYTES = 1_950_000_000_000
PRESPECIFIED_CONTROL_BURDEN_BYTES = 10_000_000_000
PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES = 10_000_000_000
RESEARCH_ADDITIONAL_FILE_DEMAND = 3_500_000
CONTROL_ADDITIONAL_FILE_DEMAND = 100_000
EXPECTED_RESEARCH_QUOTA_KIB = 2_044_723_200
EXPECTED_BACKED_QUOTA_KIB = 52_428_800
EXPECTED_RESEARCH_FILE_QUOTA = 33_554_432
EXPECTED_BACKED_FILE_QUOTA = 1_638_400
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
EXPECTED_QUOTA_PRINCIPAL = "mimicecho"
EXPECTED_NATIVE_ROWS = {
    "backed": "rproject_mimicecho",
    "research": "rprojectnb_mimicecho",
}
EXPECTED_NATIVE_FILESET_FIELD = "root"
EXPECTED_NATIVE_PRINCIPAL_SUFFIX = "_mimicecho"
EXPECTED_DISPLAY_ROW_ALIASES = {
    "backed": frozenset({"/rproject/mimicecho", "/project/mimicecho"}),
    "research": frozenset({"/rprojectnb/mimicecho", "/projectnb/mimicecho"}),
}
EXPECTED_RESTRICTED_PATHS = {
    "backed": Path("/restricted/project/mimicecho"),
    "research": Path("/restricted/projectnb/mimicecho"),
}
EXPECTED_NATIVE_QUOTA_FILE = Path("/usr/local/etc/quota/project.quota")
EXPECTED_PQUOTA_EXECUTABLE = Path("/usr/local/etc/quota/pquota")
EXPECTED_FINDMNT_EXECUTABLE = Path("/usr/bin/findmnt")
EXPECTED_DF_EXECUTABLE = Path("/usr/bin/df")
EXPECTED_PQUOTA_SIZE_BYTES = 5_840
EXPECTED_PQUOTA_SHA256 = (
    "d0aacf79af95e8a558e2b27f81b685210427fe773a3aff93b4f1b1ba5ba339ab"
)
# Exact-five canary current-headroom policy.  The overhead is the tracked
# ``storage.manifest_and_metadata_reserve_bytes`` value in
# configs/lvef_c3_resource_policy.yaml; it is frozen here so a current probe
# cannot silently reinterpret that tracked contract.
CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES = 5_000_000_000
CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES = 5_000_000_000
CANARY_REQUIRED_REMAINING_PROJECT_BYTES = (
    CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES
    + CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
)
CANARY_REQUIRED_REMAINING_FILE_SLOTS = 2_048
CANARY_HEADROOM_STATUS = "PASS_READ_ONLY_CURRENT_CANARY_HEADROOM"
CANARY_HEADROOM_KEYS = frozenset(
    {
        "status",
        "project_quota_remaining_bytes",
        "required_object_bytes",
        "frozen_overhead_bytes",
        "required_remaining_project_bytes",
        "project_file_slots_remaining",
        "required_remaining_file_slots",
        "physical_filesystem_available_bytes",
        "required_physical_available_bytes",
        "project_byte_headroom_passed",
        "project_file_slot_headroom_passed",
        "physical_byte_headroom_passed",
        "native_quota_authority_read_only",
        "pquota_display_crosscheck",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "writes_performed",
    }
)
PRIOR_CAPACITY_AUTHORITIES = {
    "phase1ee_parent_capacity": (
        2_257,
        "267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71",
    ),
    "phase1ee_composite_capacity": (
        5_003,
        "28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c",
    ),
    "phase1ee_production_packet_005": (
        7_492,
        "2725570d1137640e0c00ae790f1ae3583d63b17c7f957e86e886892dd0e6ba07",
    ),
}

DISPLAY_CROSSCHECK_PASS = "PASS"
DISPLAY_CROSSCHECK_UNAVAILABLE = "UNAVAILABLE_NONBLOCKING"
DISPLAY_CROSSCHECK_FAIL = "FAIL_BLOCKING"
DISPLAY_CROSSCHECK_STATES = frozenset(
    {
        DISPLAY_CROSSCHECK_PASS,
        DISPLAY_CROSSCHECK_UNAVAILABLE,
        DISPLAY_CROSSCHECK_FAIL,
    }
)
DISPLAY_CROSSCHECK_REASONS = frozenset(
    {
        "MATCHED_NATIVE_AUTHORITY",
        "COMMAND_UNAVAILABLE",
        "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
        "DUPLICATE_EXPECTED_PROJECT_ROW",
        "NOMINAL_QUOTA_CONTRADICTION",
        "FILE_QUOTA_CONTRADICTION",
        "ROUNDED_USAGE_CONTRADICTION",
        "FILE_USAGE_CONTRADICTION",
    }
)
DISPLAY_CROSSCHECK_REASONS_BY_STATE = {
    DISPLAY_CROSSCHECK_PASS: frozenset({"MATCHED_NATIVE_AUTHORITY"}),
    DISPLAY_CROSSCHECK_UNAVAILABLE: frozenset(
        {"COMMAND_UNAVAILABLE", "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE"}
    ),
    DISPLAY_CROSSCHECK_FAIL: frozenset(
        {
            "DUPLICATE_EXPECTED_PROJECT_ROW",
            "NOMINAL_QUOTA_CONTRADICTION",
            "FILE_QUOTA_CONTRADICTION",
            "ROUNDED_USAGE_CONTRADICTION",
            "FILE_USAGE_CONTRADICTION",
        }
    ),
}
GATE_EVALUATION_PASS = "PASS"
GATE_EVALUATION_FAIL = "FAIL"
GATE_EVALUATION_NOT_EVALUATED = "NOT_EVALUATED"
GATE_EVALUATION_STATES = frozenset(
    {
        GATE_EVALUATION_PASS,
        GATE_EVALUATION_FAIL,
        GATE_EVALUATION_NOT_EVALUATED,
    }
)
CAPACITY_GATE_KEYS = (
    "research_quota_gate",
    "physical_filesystem_capacity_gate",
    "projected_200gb_reserve_gate",
    "research_file_quota_gate",
    "backed_control_tier_byte_gate",
    "backed_control_tier_file_gate",
    "backed_control_tier_gate",
)
AGGREGATE_GATE_STATUS_FIELDS = {
    gate: f"{gate}_status" for gate in CAPACITY_GATE_KEYS
}

RECEIPT_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "captured_at_utc", "capture_identity",
        "native_quota_authority", "commands", "paths", "prior_authorities",
        "frozen_plan", "pquota_display_crosscheck", "gate_evaluation_status",
        "no_mutation_attestations",
    }
)
AGGREGATE_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "created_at_utc", "units",
        "quota_display_unit_ruling", "restricted_receipt_size_bytes",
        "restricted_receipt_sha256", "prior_authorities_hash_verified",
        "prior_authorities_closed_schema_verified",
        "immutable_original_aggregate_count",
        "immutable_supplemental_aggregate_count",
        "prior_capacity_authority_count", "prior_production_authority_roles",
        "prior_production_semantic_gates", "research_quota_bytes",
        "research_usage_bytes", "research_quota_remaining_bytes",
        "research_file_quota", "research_files_used",
        "research_file_slots_remaining", "research_filesystem_total_bytes",
        "research_filesystem_used_bytes",
        "research_filesystem_available_bytes", "research_filesystem_type",
        "research_filesystem_identity_sha256", "backed_quota_bytes",
        "backed_usage_bytes", "backed_quota_remaining_bytes",
        "backed_file_quota", "backed_files_used",
        "backed_file_slots_remaining", "backed_filesystem_total_bytes",
        "backed_filesystem_used_bytes", "backed_filesystem_available_bytes",
        "backed_filesystem_type", "backed_filesystem_identity_sha256",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_display_fileset_mapping_verified",
        "pquota_current_not_snapshot_mode_verified",
        "pquota_executable_sha256", "pquota_executable_authority_status",
        "pquota_display_crosscheck", "pquota_display_crosscheck_reason",
        "pquota_display_backed_project_row_matches",
        "pquota_display_research_project_row_matches",
        "pquota_display_rounding_rule",
        *AGGREGATE_GATE_STATUS_FIELDS.values(),
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_path_is_symlink",
        "backed_path_is_symlink", "research_mount_is_bind",
        "backed_mount_is_bind",
        "additional_project_quota_row_for_same_principal_observed",
        "snapshot_capacity_double_counting_avoided",
        "snapshot_presence_independently_enumerated",
        "snapshot_accounting_ruling",
        "selected_source_bytes", "projected_peak_bytes",
        "required_free_headroom_bytes", "minimum_effective_quota_bytes",
        "preferred_research_quota_bytes", "research_remaining_write_bytes",
        "pretransfer_research_write_bound_bytes",
        "research_physical_required_available_bytes",
        "research_quota_margin_above_minimum_bytes",
        "research_quota_slack_after_projected_peak_bytes",
        "research_margin_beyond_200gb_reserve_bytes",
        "research_physical_slack_bytes", "control_burden_bytes",
        "backed_remaining_after_control_burden_bytes",
        "research_additional_file_demand", "backed_additional_file_demand",
        "research_quota_gate_passed", "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate_passed",
        "owner_reported_backed_free_pool_gb",
        "owner_reported_research_free_pool_gb",
        "owner_reported_research_saas_purchased_gb",
        "owner_reported_total_research_quota_gb",
        "purchased_saas_allocation_remains_on_research",
        "control_write_binding_evaluated_by_capacity_receipt", "cloud_requests",
        "object_listing_repeated", "storage_inventory_repeated",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "quota_changed", "files_moved",
        "files_deleted", "full_c3_authorized",
    }
)


class PostReallocationCapacityError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        gate_evaluation_status: Mapping[str, str] | None = None,
        pquota_display_crosscheck: str | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.gate_evaluation_status = gate_evaluation_status
        self.pquota_display_crosscheck = pquota_display_crosscheck


@dataclass(frozen=True)
class CurrentCanaryHeadroomAuthority:
    """Closed file/path authority for one current read-only headroom probe."""

    native_quota_path: Path
    pquota_path: Path
    findmnt_path: Path
    df_path: Path
    research_path: Path
    backed_path: Path


DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY = CurrentCanaryHeadroomAuthority(
    native_quota_path=EXPECTED_NATIVE_QUOTA_FILE,
    pquota_path=EXPECTED_PQUOTA_EXECUTABLE,
    findmnt_path=EXPECTED_FINDMNT_EXECUTABLE,
    df_path=EXPECTED_DF_EXECUTABLE,
    research_path=EXPECTED_RESTRICTED_PATHS["research"],
    backed_path=EXPECTED_RESTRICTED_PATHS["backed"],
)


@dataclass(frozen=True)
class CapacityCommandSpec:
    """One canonical read-only command contract for the capacity receipt."""

    logical_role: str
    command_kind: str
    argv_tail: tuple[str, ...]
    executable_authority: str
    parser_consumer: str
    output_type: str
    optional_nonblocking: bool
    exit_status_policy: str
    stderr_policy: str


CAPACITY_COMMAND_SPECS = (
    CapacityCommandSpec(
        logical_role="pquota",
        command_kind="pquota",
        argv_tail=("-u", EXPECTED_QUOTA_PRINCIPAL),
        executable_authority="PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE",
        parser_consumer="pquota_display",
        output_type="UTF8_TABLE",
        optional_nonblocking=True,
        exit_status_policy="UNAVAILABLE_NONBLOCKING_ALLOWED",
        stderr_policy="UNAVAILABLE_NONBLOCKING_ALLOWED",
    ),
    CapacityCommandSpec(
        logical_role="research_findmnt",
        command_kind="findmnt",
        argv_tail=(
            "--json", "--target", str(EXPECTED_RESTRICTED_PATHS["research"]),
            "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="research_mount",
        output_type="UTF8_JSON",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="backed_findmnt",
        command_kind="findmnt",
        argv_tail=(
            "--json", "--target", str(EXPECTED_RESTRICTED_PATHS["backed"]),
            "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="backed_mount",
        output_type="UTF8_JSON",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="research_df",
        command_kind="df",
        argv_tail=(
            "-B1", "--output=source,size,used,avail,target",
            str(EXPECTED_RESTRICTED_PATHS["research"]),
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="research_df",
        output_type="UTF8_TABLE",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
    CapacityCommandSpec(
        logical_role="backed_df",
        command_kind="df",
        argv_tail=(
            "-B1", "--output=source,size,used,avail,target",
            str(EXPECTED_RESTRICTED_PATHS["backed"]),
        ),
        executable_authority="ROOT_CONTROLLED_FIXED_RESOLVER",
        parser_consumer="backed_df",
        output_type="UTF8_TABLE",
        optional_nonblocking=False,
        exit_status_policy="REQUIRED_ZERO",
        stderr_policy="REQUIRED_EMPTY",
    ),
)


def _validated_command_registry(
    specifications: Sequence[CapacityCommandSpec],
    *,
    require_canonical_roles: bool,
) -> Mapping[str, CapacityCommandSpec]:
    roles = [item.logical_role for item in specifications]
    consumers = [item.parser_consumer for item in specifications]
    if (
        not specifications
        or len(roles) != len(set(roles))
        or len(consumers) != len(set(consumers))
        or any(not role or not re.fullmatch(r"[a-z][a-z0-9_]*", role) for role in roles)
        or any(
            not consumer
            or not re.fullmatch(r"[a-z][a-z0-9_]*", consumer)
            for consumer in consumers
        )
        or any(
            item.command_kind not in {"pquota", "findmnt", "df"}
            or item.executable_authority not in {
                "PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE",
                "ROOT_CONTROLLED_FIXED_RESOLVER",
            }
            or item.output_type not in {"UTF8_JSON", "UTF8_TABLE"}
            or item.output_type
            != {
                "pquota": "UTF8_TABLE",
                "findmnt": "UTF8_JSON",
                "df": "UTF8_TABLE",
            }[item.command_kind]
            or item.optional_nonblocking != (item.command_kind == "pquota")
            or item.executable_authority
            != (
                "PINNED_ROOT_CONTROLLED_OR_UNAVAILABLE"
                if item.command_kind == "pquota"
                else "ROOT_CONTROLLED_FIXED_RESOLVER"
            )
            or not isinstance(item.argv_tail, tuple)
            or any(
                not isinstance(argument, str) or not argument
                for argument in item.argv_tail
            )
            or (
                item.optional_nonblocking
                and (
                    item.exit_status_policy
                    != "UNAVAILABLE_NONBLOCKING_ALLOWED"
                    or item.stderr_policy
                    != "UNAVAILABLE_NONBLOCKING_ALLOWED"
                )
            )
            or (
                not item.optional_nonblocking
                and (
                    item.exit_status_policy != "REQUIRED_ZERO"
                    or item.stderr_policy != "REQUIRED_EMPTY"
                )
            )
            for item in specifications
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_REGISTRY_INVALID")
    registry = {item.logical_role: item for item in specifications}
    if require_canonical_roles and (
        set(registry) != set(CAPACITY_COMMAND_REGISTRY)
        or tuple(specifications) != CAPACITY_COMMAND_SPECS
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_ROLE_SET_INVALID")
    return MappingProxyType(registry)


CAPACITY_COMMAND_REGISTRY = MappingProxyType(
    {item.logical_role: item for item in CAPACITY_COMMAND_SPECS}
)
if len(CAPACITY_COMMAND_REGISTRY) != len(CAPACITY_COMMAND_SPECS):
    raise RuntimeError("CAPACITY_COMMAND_REGISTRY_DUPLICATE_ROLE")
CAPACITY_COMMAND_ROLES = frozenset(CAPACITY_COMMAND_REGISTRY)
CAPACITY_COMMAND_CONSUMER_ROLES = MappingProxyType(
    {item.parser_consumer: item.logical_role for item in CAPACITY_COMMAND_SPECS}
)
if len(CAPACITY_COMMAND_CONSUMER_ROLES) != len(CAPACITY_COMMAND_SPECS):
    raise RuntimeError("CAPACITY_COMMAND_REGISTRY_DUPLICATE_CONSUMER")
AGGREGATE_COMMAND_ROLES = CAPACITY_COMMAND_ROLES
_validated_command_registry(
    CAPACITY_COMMAND_SPECS,
    require_canonical_roles=True,
)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PostReallocationCapacityError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_symlink_ancestors(path: Path, code: str) -> None:
    if not path.is_absolute():
        raise PostReallocationCapacityError(f"{code}_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    for part in path.absolute().parts[1:-1]:
        cursor /= part
        try:
            item = os.lstat(cursor)
        except OSError as exc:
            raise PostReallocationCapacityError(f"{code}_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(item.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise PostReallocationCapacityError(f"{code}_SYMLINK_ANCESTOR")


def _read_regular(path: Path, *, private: bool = False, maximum: int = 16_000_000) -> bytes:
    _no_symlink_ancestors(path, "INPUT")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PostReallocationCapacityError("INPUT_OPEN_FAILED") from exc
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_size <= 0 or item.st_size > maximum:
            raise PostReallocationCapacityError("INPUT_NOT_BOUNDED_REGULAR")
        if private and (item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600):
            raise PostReallocationCapacityError("INPUT_NOT_OWNER_PRIVATE")
        payload = b""
        while len(payload) <= maximum:
            block = os.read(descriptor, min(1_048_576, maximum + 1 - len(payload)))
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        if (
            len(payload) != item.st_size
            or (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise PostReallocationCapacityError("INPUT_CHANGED_OR_OVERSIZED")
        return payload
    finally:
        os.close(descriptor)


def _load_json(path: Path, *, private: bool = False) -> Mapping[str, Any]:
    try:
        value = json.loads(
            _read_regular(path, private=private).decode(),
            object_pairs_hook=_strict_pairs,
        )
    except PostReallocationCapacityError:
        raise
    except Exception as exc:
        raise PostReallocationCapacityError("INPUT_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise PostReallocationCapacityError("INPUT_JSON_NOT_MAPPING")
    return value


def _write_new(path: Path, payload: bytes, *, private: bool) -> None:
    if path.exists() or path.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_COLLISION")
    _no_symlink_ancestors(path, "OUTPUT")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_PARENT_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600 if private else 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _mkdir_private(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PostReallocationCapacityError("OUTPUT_DIRECTORY_COLLISION")
    _no_symlink_ancestors(path, "OUTPUT_DIRECTORY")
    os.mkdir(path, 0o700)


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    if result.returncode:
        raise PostReallocationCapacityError("GIT_AUTHORITY_FAILED")
    return result.stdout.strip()


def _validate_checkout(checkout: Path, commit: str) -> None:
    if (
        checkout.is_symlink() or not checkout.is_dir()
        or _git(checkout, "rev-parse", "--show-toplevel") != str(checkout.resolve())
        or _git(checkout, "branch", "--show-current") != EXPECTED_BRANCH
        or _git(checkout, "rev-parse", "HEAD") != commit
        or _git(checkout, "rev-parse", f"origin/{EXPECTED_BRANCH}") != commit
        or _git(checkout, "status", "--porcelain", "--untracked-files=no")
    ):
        raise PostReallocationCapacityError("GIT_AUTHORITY_MISMATCH")


def _serialize_command_record(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    executable_sha256: str,
    executable_size_bytes: int,
    exit_status: int,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, Any]:
    """Serialize command evidence using the registry's unique logical role."""
    argv_list = list(argv)
    return {
        "role": specification.logical_role,
        "argv": argv_list,
        "argv_sha256": _sha(
            json.dumps(argv_list, separators=(",", ":")).encode()
        ),
        "executable_sha256": executable_sha256,
        "executable_size_bytes": executable_size_bytes,
        "exit_status": exit_status,
        "stdout_bytes": len(stdout),
        "stdout_sha256": _sha(stdout),
        "stdout_text": stdout.decode("utf-8"),
        "stderr_bytes": len(stderr),
        "stderr_sha256": _sha(stderr),
    }


def _run(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    process_runner: Callable[..., Any] | None = None,
    permitted_owner_uids: frozenset[int] = frozenset({0}),
) -> Mapping[str, Any]:
    executable = Path(argv[0]).resolve(strict=True)
    before = executable.stat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in permitted_owner_uids
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_ROOT_CONTROLLED")
    runner = process_runner or subprocess.run
    result = runner(
        list(argv), capture_output=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    after = executable.stat()
    if result.returncode or result.stderr or len(result.stdout) > 2_000_000:
        raise PostReallocationCapacityError(
            f"{specification.logical_role.upper()}_COMMAND_FAILED"
        )
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    try:
        return _serialize_command_record(
            specification,
            argv,
            executable_sha256=_sha(
                _read_regular(executable, maximum=256_000_000)
            ),
            executable_size_bytes=before.st_size,
            exit_status=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except UnicodeDecodeError as exc:
        raise PostReallocationCapacityError(
            f"{specification.logical_role.upper()}_COMMAND_OUTPUT_NOT_UTF8"
        ) from exc


def _capture_optional_pquota(
    specification: CapacityCommandSpec,
    principal: str,
) -> Mapping[str, Any]:
    """Capture the corroborating display without making it byte authority."""
    if (
        specification.logical_role != "pquota"
        or specification.command_kind != "pquota"
        or principal != EXPECTED_QUOTA_PRINCIPAL
    ):
        raise PostReallocationCapacityError("PQUOTA_COMMAND_SPEC_INVALID")
    located = shutil.which("pquota", path="/usr/local/bin:/usr/bin:/bin")
    argv = [located or "pquota", "-u", principal]
    empty = _sha(b"")

    def unavailable(reason: str) -> Mapping[str, Any]:
        executable_sha256 = "UNAVAILABLE"
        executable_size_bytes = 0
        value = _serialize_command_record(
            specification,
            argv,
            executable_sha256=executable_sha256,
            executable_size_bytes=executable_size_bytes,
            exit_status=-1,
            stdout=b"",
            stderr=b"",
        )
        value.update({
            "availability_status": "UNAVAILABLE_NONBLOCKING",
            "availability_reason": reason,
        })
        return value

    if not located:
        return unavailable("EXECUTABLE_NOT_FOUND")
    executable = Path(located).resolve(strict=True)
    before = executable.stat()
    if (
        executable != EXPECTED_PQUOTA_EXECUTABLE
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size != EXPECTED_PQUOTA_SIZE_BYTES
        or _sha(_read_regular(executable, maximum=256_000_000))
        != EXPECTED_PQUOTA_SHA256
    ):
        return unavailable("EXECUTABLE_AUTHORITY_MISMATCH")
    result = subprocess.run(
        argv,
        capture_output=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    after = executable.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    reason = "AVAILABLE"
    availability = "AVAILABLE"
    if result.returncode:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_NONZERO_EXIT"
    elif result.stderr:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_STDERR_PRESENT"
    elif len(result.stdout) > 2_000_000:
        availability, reason = "UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_OVERSIZED"
    try:
        stdout_text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        availability, reason, stdout_text = (
            "UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_NOT_UTF8", ""
        )
    if len(result.stdout) > 2_000_000:
        stdout_text = ""
    value = _serialize_command_record(
        specification,
        argv,
        executable_sha256=EXPECTED_PQUOTA_SHA256,
        executable_size_bytes=before.st_size,
        exit_status=result.returncode,
        stdout=result.stdout if stdout_text else b"",
        stderr=result.stderr,
    )
    # Preserve the original byte/hash evidence for unavailable non-UTF8 or
    # oversized output while withholding an unsafe/unbounded decoded value.
    if not stdout_text and result.stdout:
        value["stdout_bytes"] = len(result.stdout)
        value["stdout_sha256"] = _sha(result.stdout)
    value["stdout_text"] = stdout_text
    value.update({
        "availability_status": availability,
        "availability_reason": reason,
    })
    return value


def _capture_capacity_commands(
    principal: str,
    *,
    specifications: Sequence[CapacityCommandSpec] = CAPACITY_COMMAND_SPECS,
    required_runner: Callable[
        [CapacityCommandSpec, Sequence[str]], Mapping[str, Any]
    ] | None = None,
    optional_runner: Callable[
        [CapacityCommandSpec, str], Mapping[str, Any]
    ] | None = None,
    resolver: Callable[..., str | None] | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    """Capture every production command from one canonical role registry."""
    if principal != EXPECTED_QUOTA_PRINCIPAL:
        raise PostReallocationCapacityError("CAPACITY_COMMAND_PRINCIPAL_INVALID")
    registry = _validated_command_registry(
        specifications, require_canonical_roles=True
    )
    required_runner = required_runner or _run
    optional_runner = optional_runner or _capture_optional_pquota
    resolver = resolver or shutil.which
    executables = {
        kind: resolver(kind, path="/usr/bin:/bin:/usr/local/bin")
        for kind in {item.command_kind for item in registry.values()}
        if kind != "pquota"
    }
    if any(not executable for executable in executables.values()):
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_FOUND")
    records: list[tuple[str, Mapping[str, Any]]] = []
    for specification in specifications:
        if specification.optional_nonblocking:
            record = optional_runner(specification, principal)
        else:
            executable = executables[specification.command_kind]
            if not executable:
                raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_FOUND")
            record = required_runner(
                specification,
                [executable, *specification.argv_tail],
            )
        records.append((specification.logical_role, record))
    if (
        len(records) != len(CAPACITY_COMMAND_ROLES)
        or len({role for role, _ in records}) != len(records)
        or {role for role, _ in records} != CAPACITY_COMMAND_ROLES
        or any(record.get("role") != role for role, record in records)
    ):
        raise PostReallocationCapacityError("CAPACITY_COMMAND_ROLE_SET_INVALID")
    return dict(records)


def _parse_native_quota(payload: bytes) -> Mapping[str, Mapping[str, int | str]]:
    observed: dict[str, Mapping[str, int | str]] = {}
    principal_fileset_rows = 0
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PostReallocationCapacityError("NATIVE_QUOTA_NOT_UTF8") from exc
    for line in lines:
        fields = line.split()
        if (
            len(fields) == 14
            and fields[2] == "FILESET"
            and fields[0].endswith(EXPECTED_NATIVE_PRINCIPAL_SUFFIX)
        ):
            principal_fileset_rows += 1
        if not fields or fields[0] not in EXPECTED_NATIVE_ROWS.values():
            continue
        if len(fields) != 14 or fields[2] != "FILESET" or fields[8] != "|":
            raise PostReallocationCapacityError("NATIVE_QUOTA_ROW_LAYOUT_INVALID")
        role = next(key for key, name in EXPECTED_NATIVE_ROWS.items() if name == fields[0])
        if role in observed:
            raise PostReallocationCapacityError("NATIVE_QUOTA_ROW_NOT_UNIQUE")
        # The root-controlled SCC pquota implementation documents column 1 as
        # Name and column 2 as fileset.  Project FILESET rows therefore bind
        # the project authority in fields[0] and the native scope (currently
        # ``root``) in fields[1]; the latter is not the project principal.
        if fields[1] != EXPECTED_NATIVE_FILESET_FIELD:
            raise PostReallocationCapacityError(
                "NATIVE_QUOTA_FILESET_SCOPE_INVALID"
            )
        try:
            usage_kib, quota_kib = int(fields[3]), int(fields[4])
            files_used, file_quota = int(fields[9]), int(fields[10])
        except ValueError as exc:
            raise PostReallocationCapacityError("NATIVE_QUOTA_INTEGER_INVALID") from exc
        observed[role] = {
            "native_name_sha256": _sha(fields[0].encode()),
            "fileset_name_sha256": _sha(fields[1].encode()),
            "usage_kib": usage_kib,
            "quota_kib": quota_kib,
            "files_used": files_used,
            "file_quota": file_quota,
            "raw_row_sha256": _sha(line.encode()),
        }
    if set(observed) != {"backed", "research"}:
        raise PostReallocationCapacityError("NATIVE_QUOTA_ROWS_MISSING")
    if principal_fileset_rows != 2:
        raise PostReallocationCapacityError("NATIVE_QUOTA_ADDITIONAL_PRINCIPAL_ROW")
    if (
        observed["research"]["quota_kib"] != EXPECTED_RESEARCH_QUOTA_KIB
        or observed["backed"]["quota_kib"] != EXPECTED_BACKED_QUOTA_KIB
        or observed["research"]["file_quota"] != EXPECTED_RESEARCH_FILE_QUOTA
        or observed["backed"]["file_quota"] != EXPECTED_BACKED_FILE_QUOTA
    ):
        raise PostReallocationCapacityError("NATIVE_QUOTA_ALLOCATION_UNEXPECTED")
    return observed


def _display_result(
    status: str,
    reason: str,
    row_counts: Mapping[str, int],
    precision: Mapping[str, int] | None = None,
) -> Mapping[str, Any]:
    result = {
        "status": status,
        "reason": reason,
        "project_row_match_counts": {
            role: int(row_counts.get(role, 0))
            for role in ("backed", "research")
        },
        "usage_decimal_places": dict(precision or {}),
        "rounding_rule": "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6",
    }
    if (
        status not in DISPLAY_CROSSCHECK_STATES
        or reason not in DISPLAY_CROSSCHECK_REASONS
        or reason not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(status, ())
        or set(result) != {
            "status", "reason", "project_row_match_counts",
            "usage_decimal_places", "rounding_rule",
        }
        or set(result["project_row_match_counts"]) != {"backed", "research"}
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in result["project_row_match_counts"].values()
        )
        or not set(result["usage_decimal_places"]).issubset({"backed", "research"})
        or any(
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 6
            for value in result["usage_decimal_places"].values()
        )
    ):
        raise PostReallocationCapacityError("PQUOTA_DISPLAY_RESULT_INTERNAL_INVALID")
    return result


def _parse_pquota(
    text: str,
    native: Mapping[str, Mapping[str, int | str]],
    *,
    command_available: bool = True,
) -> Mapping[str, Any]:
    if not command_available:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE, "COMMAND_UNAVAILABLE", {}
        )
    rows_by_role: dict[str, list[list[str]]] = {
        "backed": [], "research": [],
    }
    for line in text.splitlines():
        fields = re.split(r"[ \t]+", line.strip()) if line.strip() else []
        for role, aliases in EXPECTED_DISPLAY_ROW_ALIASES.items():
            if fields and fields[0] in aliases:
                rows_by_role[role].append(fields)
    row_counts = {
        role: len(matches) for role, matches in rows_by_role.items()
    }
    if any(count > 1 for count in row_counts.values()):
        return _display_result(
            DISPLAY_CROSSCHECK_FAIL,
            "DUPLICATE_EXPECTED_PROJECT_ROW",
            row_counts,
        )
    rows = {
        role: matches[0]
        for role, matches in rows_by_role.items()
        if matches
    }
    if set(rows) != {"backed", "research"}:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE,
            "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
            row_counts,
        )
    precision: dict[str, int] = {}
    contradictions: list[str] = []
    unparseable = False
    for role in ("backed", "research"):
        fields = rows[role]
        if len(fields) != 5:
            unparseable = True
            continue

        displayed_quota: Decimal | None = None
        displayed_file_quota: int | None = None
        displayed_usage: Decimal | None = None
        displayed_files_used: int | None = None
        try:
            displayed_quota = Decimal(fields[1])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_file_quota = int(fields[2])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_usage = Decimal(fields[3])
        except (ValueError, ArithmeticError):
            unparseable = True
        try:
            displayed_files_used = int(fields[4])
        except (ValueError, ArithmeticError):
            unparseable = True

        native_quota_gib = Decimal(int(native[role]["quota_kib"])) / Decimal(1024 ** 2)
        native_usage_gib = Decimal(int(native[role]["usage_kib"])) / Decimal(1024 ** 2)
        if displayed_quota is not None:
            if not displayed_quota.is_finite() or displayed_quota < 0:
                unparseable = True
            elif displayed_quota != native_quota_gib:
                contradictions.append("NOMINAL_QUOTA_CONTRADICTION")
        if displayed_file_quota is not None:
            if displayed_file_quota < 0:
                unparseable = True
            elif displayed_file_quota != int(native[role]["file_quota"]):
                contradictions.append("FILE_QUOTA_CONTRADICTION")
        if displayed_usage is not None:
            if not displayed_usage.is_finite() or displayed_usage < 0:
                unparseable = True
            else:
                places = max(-displayed_usage.as_tuple().exponent, 0)
                if places > 6:
                    unparseable = True
                else:
                    precision[role] = places
                    quantum = Decimal(1).scaleb(-places)
                    if displayed_usage != native_usage_gib.quantize(
                        quantum, rounding=ROUND_HALF_UP
                    ):
                        contradictions.append("ROUNDED_USAGE_CONTRADICTION")
        if displayed_files_used is not None:
            if displayed_files_used < 0:
                unparseable = True
            elif displayed_files_used != int(native[role]["files_used"]):
                contradictions.append("FILE_USAGE_CONTRADICTION")

    if contradictions:
        return _display_result(
            DISPLAY_CROSSCHECK_FAIL, contradictions[0], row_counts, precision
        )
    if unparseable:
        return _display_result(
            DISPLAY_CROSSCHECK_UNAVAILABLE,
            "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE",
            row_counts,
            precision,
        )
    return _display_result(
        DISPLAY_CROSSCHECK_PASS,
        "MATCHED_NATIVE_AUTHORITY",
        row_counts,
        precision,
    )


def _parse_findmnt(text: str, requested: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs)
    except Exception as exc:
        raise PostReallocationCapacityError("FINDMNT_JSON_INVALID") from exc
    if not isinstance(value, Mapping) or set(value) != {"filesystems"}:
        raise PostReallocationCapacityError("FINDMNT_SCHEMA_INVALID")
    rows = value["filesystems"]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise PostReallocationCapacityError("FINDMNT_ROW_COUNT_INVALID")
    row = rows[0]
    if set(row) != {"source", "target", "fstype", "options", "fsroot"}:
        raise PostReallocationCapacityError("FINDMNT_ROW_SCHEMA_INVALID")
    source, target, fstype, options, fsroot = (
        str(row[key]) for key in ("source", "target", "fstype", "options", "fsroot")
    )
    try:
        requested.relative_to(Path(target))
    except ValueError as exc:
        raise PostReallocationCapacityError("FINDMNT_PATH_NOT_ON_TARGET") from exc
    option_set = set(options.split(","))
    bind = "bind" in option_set or "rbind" in option_set or fsroot != "/"
    return {
        "source_sha256": _sha(source.encode()),
        "target_sha256": _sha(target.encode()),
        "fsroot_sha256": _sha(fsroot.encode()),
        "identity_sha256": _sha(_canonical({
            "source": source, "target": target, "fstype": fstype,
            "fsroot": fsroot,
        })),
        "source": source,
        "target": target,
        "fsroot": fsroot,
        "fstype": fstype,
        "bind": bind,
    }


def _parse_df(text: str, mount: Mapping[str, Any]) -> Mapping[str, int]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 2:
        raise PostReallocationCapacityError("DF_ROW_COUNT_INVALID")
    fields = lines[1].split(maxsplit=4)
    if len(fields) != 5 or fields[0] != mount["source"] or fields[4] != mount["target"]:
        raise PostReallocationCapacityError("DF_MOUNT_IDENTITY_MISMATCH")
    try:
        total, used, available = map(int, fields[1:4])
    except ValueError as exc:
        raise PostReallocationCapacityError("DF_BYTES_INVALID") from exc
    if min(total, used, available) < 0 or used + available > total:
        raise PostReallocationCapacityError("DF_BYTES_DO_NOT_RECONCILE")
    return {"total": total, "used": used, "available": available}


def validate_current_canary_headroom(value: Any) -> dict[str, Any]:
    """Validate the closed aggregate-safe result of the public probe."""

    if not isinstance(value, Mapping) or set(value) != CANARY_HEADROOM_KEYS:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_SCHEMA_NOT_CLOSED"
        )
    integer_fields = (
        "project_quota_remaining_bytes",
        "required_object_bytes",
        "frozen_overhead_bytes",
        "required_remaining_project_bytes",
        "project_file_slots_remaining",
        "required_remaining_file_slots",
        "physical_filesystem_available_bytes",
        "required_physical_available_bytes",
        "cloud_requests",
        "scheduler_jobs_submitted",
        "writes_performed",
    )
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), int)
        or int(value[field]) < 0
        for field in integer_fields
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_VALUE_INVALID"
        )
    if (
        value.get("status") != CANARY_HEADROOM_STATUS
        or value.get("required_object_bytes")
        != CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES
        or value.get("frozen_overhead_bytes")
        != CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
        or value.get("required_remaining_project_bytes")
        != CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value.get("required_physical_available_bytes")
        != CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value.get("required_remaining_file_slots")
        != CANARY_REQUIRED_REMAINING_FILE_SLOTS
        or value.get("project_byte_headroom_passed") is not True
        or value.get("project_file_slot_headroom_passed") is not True
        or value.get("physical_byte_headroom_passed") is not True
        or value.get("native_quota_authority_read_only") is not True
        or value.get("pquota_display_crosscheck")
        not in {DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE}
        or value.get("cloud_requests") != 0
        or value.get("scheduler_jobs_submitted") != 0
        or value.get("writes_performed") != 0
        or value["project_quota_remaining_bytes"]
        < CANARY_REQUIRED_REMAINING_PROJECT_BYTES
        or value["project_file_slots_remaining"]
        < CANARY_REQUIRED_REMAINING_FILE_SLOTS
        or value["physical_filesystem_available_bytes"]
        < CANARY_REQUIRED_REMAINING_PROJECT_BYTES
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_INVARIANT_INVALID"
        )
    return dict(value)


def _validate_current_canary_headroom_authority(
    authority: CurrentCanaryHeadroomAuthority,
) -> bool:
    """Validate the closed path bundle; return whether it is production."""

    if type(authority) is not CurrentCanaryHeadroomAuthority:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
        )
    production = authority == DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    paths = (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
        authority.research_path,
        authority.backed_path,
    )
    if any(
        not isinstance(path, Path)
        or not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        for path in paths
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
        )
    production_file_authorities = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.native_quota_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.pquota_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.findmnt_path,
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY.df_path,
    )
    supplied_file_authorities = (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    )
    if not production and any(
        supplied == frozen
        for supplied, frozen in zip(
            supplied_file_authorities,
            production_file_authorities,
            strict=True,
        )
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_AUTHORITY_MIXED"
        )
    tools_by_role = {
        "pquota": authority.pquota_path,
        "findmnt": authority.findmnt_path,
        "df": authority.df_path,
    }
    if (
        len(set(tools_by_role.values())) != len(tools_by_role)
        or any(path.name != role for role, path in tools_by_role.items())
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_TOOL_ROLE_INVALID"
        )
    permitted_uids = {0} if production else {0, os.geteuid()}
    for path in (
        authority.native_quota_path,
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    ):
        _no_symlink_ancestors(path, "CURRENT_CANARY_HEADROOM_AUTHORITY")
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in permitted_uids
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_AUTHORITY_INVALID"
            )
    for path in (
        authority.pquota_path,
        authority.findmnt_path,
        authority.df_path,
    ):
        if not stat.S_IMODE(os.lstat(path).st_mode) & 0o111:
            raise PostReallocationCapacityError(
                "CURRENT_CANARY_HEADROOM_TOOL_NOT_EXECUTABLE"
            )
    _path_identity(authority.research_path)
    _path_identity(authority.backed_path)
    if production and (
        os.lstat(authority.pquota_path).st_size != EXPECTED_PQUOTA_SIZE_BYTES
        or _sha(_read_regular(authority.pquota_path, maximum=256_000_000))
        != EXPECTED_PQUOTA_SHA256
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_PQUOTA_AUTHORITY_MISMATCH"
        )
    return production


def _current_canary_command_argv(
    specification: CapacityCommandSpec,
    authority: CurrentCanaryHeadroomAuthority,
) -> list[str]:
    executables = {
        "pquota": authority.pquota_path,
        "findmnt": authority.findmnt_path,
        "df": authority.df_path,
    }
    targets = {
        "research_findmnt": authority.research_path,
        "backed_findmnt": authority.backed_path,
        "research_df": authority.research_path,
        "backed_df": authority.backed_path,
    }
    if specification.logical_role == "pquota":
        tail = ("-u", EXPECTED_QUOTA_PRINCIPAL)
    elif specification.command_kind == "findmnt":
        tail = (
            "--json",
            "--target",
            str(targets[specification.logical_role]),
            "--output",
            "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT",
        )
    elif specification.command_kind == "df":
        tail = (
            "-B1",
            "--output=source,size,used,avail,target",
            str(targets[specification.logical_role]),
        )
    else:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_COMMAND_INVALID"
        )
    return [str(executables[specification.command_kind]), *tail]


def _run_current_canary_pquota(
    specification: CapacityCommandSpec,
    argv: Sequence[str],
    *,
    process_runner: Callable[..., Any] | None,
) -> Mapping[str, Any]:
    """Run the mandatory tool while keeping its display nonblocking."""

    executable = Path(argv[0])
    before = executable.stat()
    runner = process_runner or subprocess.run
    result = runner(
        list(argv),
        capture_output=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        },
        check=False,
    )
    after = executable.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    availability, reason = "AVAILABLE", "AVAILABLE"
    if result.returncode:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_NONZERO_EXIT",
        )
    elif result.stderr:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_STDERR_PRESENT",
        )
    elif len(result.stdout) > 2_000_000:
        availability, reason = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_OUTPUT_OVERSIZED",
        )
    try:
        stdout = (
            result.stdout
            if len(result.stdout) <= 2_000_000
            else b""
        )
        stdout.decode("utf-8")
    except UnicodeDecodeError:
        availability, reason, stdout = (
            "UNAVAILABLE_NONBLOCKING",
            "COMMAND_OUTPUT_NOT_UTF8",
            b"",
        )
    record = _serialize_command_record(
        specification,
        argv,
        executable_sha256=_sha(
            _read_regular(executable, maximum=256_000_000)
        ),
        executable_size_bytes=before.st_size,
        exit_status=result.returncode,
        stdout=stdout,
        stderr=result.stderr,
    )
    record.update(
        {
            "availability_status": availability,
            "availability_reason": reason,
        }
    )
    return record


def probe_current_canary_headroom(
    authority: CurrentCanaryHeadroomAuthority = (
        DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY
    ),
    *,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read and validate contemporaneous exact-five canary headroom.

    The probe is deliberately read-only: it executes only the canonical
    pquota/findmnt/df registry and reads the fixed native quota authority.  It
    requires exact remaining project quota for the 5,000,000,000-byte object
    ceiling plus the tracked 5,000,000,000-byte manifest/metadata reserve,
    2,048 project file slots, and the same 10,000,000,000 physical bytes from
    ``df -B1``.  The closed path bundle defaults to the frozen SCC authority;
    a sandbox bundle and subprocess runner may be injected for synthetic tests.
    """

    production = _validate_current_canary_headroom_authority(authority)
    permitted_owner_uids = (
        frozenset({0}) if production else frozenset({0, os.geteuid()})
    )
    commands: dict[str, Mapping[str, Any]] = {}
    for specification in CAPACITY_COMMAND_SPECS:
        argv = _current_canary_command_argv(specification, authority)
        if specification.logical_role == "pquota":
            record = dict(
                _run_current_canary_pquota(
                    specification,
                    argv,
                    process_runner=process_runner,
                )
            )
        else:
            record = dict(
                _run(
                    specification,
                    argv,
                    process_runner=process_runner,
                    permitted_owner_uids=permitted_owner_uids,
                )
            )
        commands[specification.logical_role] = record
    if set(commands) != CAPACITY_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_COMMAND_INVALID"
        )
    native_payload = _read_regular(
        authority.native_quota_path, maximum=64_000_000
    )
    native = _parse_native_quota(native_payload)
    if any(
        int(native[role][field]) < 0
        for role in ("research", "backed")
        for field in ("usage_kib", "quota_kib", "files_used", "file_quota")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_NATIVE_USAGE_INVALID"
        )
    paths = {
        "research": _path_identity(authority.research_path),
        "backed": _path_identity(authority.backed_path),
    }
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]][
                "stdout_text"
            ],
            authority.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]][
                "stdout_text"
            ],
            authority.backed_path,
        ),
    }
    if production:
        _validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=mounts
        )
    elif any(
        paths[role]["is_symlink"] is not False
        or mounts[role]["bind"] is not False
        or mounts[role]["fsroot"] != "/"
        or native[role]["native_name_sha256"]
        != _sha(EXPECTED_NATIVE_ROWS[role].encode())
        for role in ("research", "backed")
    ):
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_MOUNT_RECONCILIATION_FAILED"
        )
    research_df = _parse_df(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_df"]][
            "stdout_text"
        ],
        mounts["research"],
    )
    # Parse the backed result as well: the exact canonical command registry is
    # all-or-nothing even though this canary writes only to the research tier.
    _parse_df(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_df"]][
            "stdout_text"
        ],
        mounts["backed"],
    )
    display_command = commands[
        CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
    ]
    display = _parse_pquota(
        display_command["stdout_text"],
        native,
        command_available=(
            display_command.get("availability_status") == "AVAILABLE"
        ),
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_HEADROOM_PQUOTA_CONTRADICTION"
        )

    quota_remaining = max(
        int(native["research"]["quota_kib"])
        - int(native["research"]["usage_kib"]),
        0,
    ) * 1024
    file_slots_remaining = max(
        int(native["research"]["file_quota"])
        - int(native["research"]["files_used"]),
        0,
    )
    physical_available = int(research_df["available"])
    if quota_remaining < CANARY_REQUIRED_REMAINING_PROJECT_BYTES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_PROJECT_BYTE_HEADROOM_INSUFFICIENT"
        )
    if file_slots_remaining < CANARY_REQUIRED_REMAINING_FILE_SLOTS:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_FILE_SLOT_HEADROOM_INSUFFICIENT"
        )
    if physical_available < CANARY_REQUIRED_REMAINING_PROJECT_BYTES:
        raise PostReallocationCapacityError(
            "CURRENT_CANARY_PHYSICAL_BYTE_HEADROOM_INSUFFICIENT"
        )
    return validate_current_canary_headroom(
        {
            "status": CANARY_HEADROOM_STATUS,
            "project_quota_remaining_bytes": quota_remaining,
            "required_object_bytes": CANARY_MAXIMUM_EXPECTED_OBJECT_BYTES,
            "frozen_overhead_bytes": (
                CANARY_FROZEN_MANIFEST_AND_METADATA_OVERHEAD_BYTES
            ),
            "required_remaining_project_bytes": (
                CANARY_REQUIRED_REMAINING_PROJECT_BYTES
            ),
            "project_file_slots_remaining": file_slots_remaining,
            "required_remaining_file_slots": (
                CANARY_REQUIRED_REMAINING_FILE_SLOTS
            ),
            "physical_filesystem_available_bytes": physical_available,
            "required_physical_available_bytes": (
                CANARY_REQUIRED_REMAINING_PROJECT_BYTES
            ),
            "project_byte_headroom_passed": True,
            "project_file_slot_headroom_passed": True,
            "physical_byte_headroom_passed": True,
            "native_quota_authority_read_only": True,
            "pquota_display_crosscheck": str(display["status"]),
            "cloud_requests": 0,
            "scheduler_jobs_submitted": 0,
            "writes_performed": 0,
        }
    )


def _validate_prior(
    *, original_root: Path, supplemental_root: Path, parent: Path,
    composite: Path, packet_path: Path,
) -> Mapping[str, int]:
    lock = importlib.import_module("lock_lvef_c3_production_orchestration")
    result = lock.validate_immutable_aggregate_authorities(original_root, supplemental_root)
    lock.validate_supplemental_validation_receipt(
        supplemental_root,
        supplemental_authorities=lock.SUPPLEMENTAL_AGGREGATE_AUTHORITIES,
    )
    paths = {
        "phase1ee_parent_capacity": parent,
        "phase1ee_composite_capacity": composite,
        "phase1ee_production_packet_005": packet_path,
    }
    for role, path in paths.items():
        payload = _read_regular(path, private=True)
        expected_size, expected_sha = PRIOR_CAPACITY_AUTHORITIES[role]
        if len(payload) != expected_size or _sha(payload) != expected_sha:
            raise PostReallocationCapacityError("PRIOR_AUTHORITY_HASH_MISMATCH")
    live = importlib.import_module("capture_lvef_c3_live_quota")
    prior_capacity = importlib.import_module("capture_lvef_c3_post_expansion_capacity")
    packet = importlib.import_module("build_lvef_c3_production_authority_packet")
    live.validate_aggregate_output(_load_json(parent, private=True))
    prior_capacity.validate_aggregate_output(_load_json(composite, private=True))
    packet_value = _load_json(packet_path, private=True)
    packet.validate_packet(packet_value)
    return {
        "original": int(result["original_validated_file_count"]),
        "supplemental": int(result["supplemental_validated_file_count"]),
        "capacity": 2,
        "packet_roles": len(packet_value["authority"]),
        "packet_gates": len(packet_value["semantic_validation"]),
    }


def _path_identity(path: Path) -> Mapping[str, Any]:
    # Check the leaf and every ancestor without following any symlink.  Passing
    # a synthetic child makes the shared helper include ``path`` itself in the
    # ancestor walk while requiring no child to exist.
    _no_symlink_ancestors(path / ".phase1ef_nofollow_probe", "CAPACITY_PATH")
    item = os.lstat(path)
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
        raise PostReallocationCapacityError("CAPACITY_PATH_NOT_DIRECTORY_NOFOLLOW")
    return {
        "path_sha256": _sha(str(path).encode()),
        "resolved_path_sha256": _sha(str(path.resolve(strict=True)).encode()),
        "device": item.st_dev,
        "inode": item.st_ino,
        "is_symlink": False,
    }


def _validate_pquota_restricted_mount_reconciliation(
    *, native: Mapping[str, Mapping[str, int | str]],
    paths: Mapping[str, Mapping[str, Any]],
    mounts: Mapping[str, Mapping[str, Any]],
) -> None:
    """Bind native project filesets to the secure no-symlink mount roots.

    Human-facing display aliases are deliberately absent from this primary
    authority check.  The native fileset names and explicit restricted roots
    are independently bound to the mounted filesystems used for C3.
    """
    expected_targets = {
        "backed": Path("/restricted/project"),
        "research": Path("/restricted/projectnb"),
    }
    expected_mount_source_names = {
        "backed": "rproject", "research": "rprojectnb",
    }
    for role in ("backed", "research"):
        expected_path = EXPECTED_RESTRICTED_PATHS[role]
        expected_native_name = EXPECTED_NATIVE_ROWS[role]
        if (
            paths[role]["resolved_path_sha256"] != _sha(str(expected_path).encode())
            or paths[role]["is_symlink"] is not False
            or mounts[role]["target"] != str(expected_targets[role])
            or PurePosixPath(mounts[role]["source"].split(":")[-1]).name
            != expected_mount_source_names[role]
            or mounts[role]["fsroot"] != "/"
            or mounts[role]["bind"] is not False
            or native[role]["native_name_sha256"]
            != _sha(expected_native_name.encode())
        ):
            raise PostReallocationCapacityError(
                "PQUOTA_RESTRICTED_MOUNT_RECONCILIATION_FAILED"
            )


def _gate_evaluation_status(values: Mapping[str, bool]) -> Mapping[str, str]:
    if set(values) != set(CAPACITY_GATE_KEYS) or any(
        not isinstance(value, bool) for value in values.values()
    ):
        raise PostReallocationCapacityError("CAPACITY_GATE_INPUT_INTERNAL_INVALID")
    return {
        key: GATE_EVALUATION_PASS if values[key] else GATE_EVALUATION_FAIL
        for key in CAPACITY_GATE_KEYS
    }


def _not_evaluated_gate_status() -> Mapping[str, str]:
    return {key: GATE_EVALUATION_NOT_EVALUATED for key in CAPACITY_GATE_KEYS}


def _validate_gate_evaluation_status(value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != set(CAPACITY_GATE_KEYS)
        or any(item not in GATE_EVALUATION_STATES for item in value.values())
    ):
        raise PostReallocationCapacityError("CAPACITY_GATE_EVALUATION_SCHEMA_INVALID")


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != AGGREGATE_TYPE
        or value.get("status") != AGGREGATE_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not SHA256_RE.fullmatch(str(value.get("restricted_receipt_sha256", "")))
        or value.get("units") != "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER"
        or value.get("quota_display_unit_ruling")
        != "BINARY_GIB_ROUNDED_SECONDARY_ONLY"
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_AUTHORITY_INVALID")
    aggregate_gate_status = {
        gate: value.get(field)
        for gate, field in AGGREGATE_GATE_STATUS_FIELDS.items()
    }
    _validate_gate_evaluation_status(aggregate_gate_status)
    display_state = value.get("pquota_display_crosscheck")
    display_reason = value.get("pquota_display_crosscheck_reason")
    executable_authority = value.get("pquota_executable_authority_status")
    if (
        display_state not in {
            DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or display_reason not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(
            str(display_state), ()
        )
        or value.get("pquota_display_rounding_rule")
        != "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6"
        or value.get("pquota_display_backed_project_row_matches") not in {0, 1}
        or value.get("pquota_display_research_project_row_matches") not in {0, 1}
        or executable_authority not in {
            "PASS_TRUSTED_ROOT_CONTROLLED", "UNAVAILABLE_NONBLOCKING",
        }
        or (
            executable_authority == "PASS_TRUSTED_ROOT_CONTROLLED"
            and value.get("pquota_executable_sha256") != EXPECTED_PQUOTA_SHA256
        )
        or (
            executable_authority == "UNAVAILABLE_NONBLOCKING"
            and value.get("pquota_executable_sha256") not in {
                EXPECTED_PQUOTA_SHA256, "UNAVAILABLE",
            }
        )
        or (
            executable_authority == "UNAVAILABLE_NONBLOCKING"
            and display_reason != "COMMAND_UNAVAILABLE"
        )
        or value.get("pquota_display_fileset_mapping_verified")
        is not (display_state == DISPLAY_CROSSCHECK_PASS)
        or (
            display_state == DISPLAY_CROSSCHECK_PASS
            and (
                value.get("pquota_display_backed_project_row_matches") != 1
                or value.get("pquota_display_research_project_row_matches") != 1
            )
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_DISPLAY_AUTHORITY_INVALID")
    required_true = {
        "prior_authorities_hash_verified", "prior_authorities_closed_schema_verified",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_current_not_snapshot_mode_verified",
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed", "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed", "backed_control_tier_gate_passed",
        "purchased_saas_allocation_remains_on_research",
    }
    if any(value.get(key) is not True for key in required_true):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_GATE_NOT_PASS")
    gate_boolean_pairs = {
        "research_quota_gate": "research_quota_gate_passed",
        "physical_filesystem_capacity_gate": "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate": "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate": "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate": "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate": "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate": "backed_control_tier_gate_passed",
    }
    if any(
        aggregate_gate_status[gate] != GATE_EVALUATION_PASS
        or value.get(boolean_key) is not True
        for gate, boolean_key in gate_boolean_pairs.items()
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_GATE_STATUS_MISMATCH")
    expected_false = {
        "research_path_is_symlink", "backed_path_is_symlink",
        "research_mount_is_bind", "backed_mount_is_bind",
        "additional_project_quota_row_for_same_principal_observed",
        "snapshot_presence_independently_enumerated",
        "control_write_binding_evaluated_by_capacity_receipt",
        "object_listing_repeated",
        "storage_inventory_repeated", "real_dicom_extraction",
        "echoprime_inference", "model_fitting", "confirmatory_performance_accessed",
        "quota_changed", "full_c3_authorized",
    }
    if any(value.get(key) is not False for key in expected_false):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_BOUNDARY_INVALID")
    if (
        value.get("snapshot_capacity_double_counting_avoided") is not True
        or value.get("snapshot_accounting_ruling")
        != "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_SNAPSHOT_RULING_INVALID")
    for key in ("cloud_requests", "scheduler_jobs_submitted", "dicom_bodies_downloaded", "files_moved", "files_deleted"):
        if value.get(key) != 0:
            raise PostReallocationCapacityError("CAPACITY_AGGREGATE_EXECUTION_OCCURRED")
    if (
        value.get("research_quota_bytes") != EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or value.get("backed_quota_bytes") != EXPECTED_BACKED_QUOTA_KIB * 1024
        or value.get("research_quota_margin_above_minimum_bytes")
        != value["research_quota_bytes"] - MINIMUM_EFFECTIVE_QUOTA_BYTES
        or value.get("research_quota_slack_after_projected_peak_bytes")
        != value["research_quota_bytes"] - PROJECTED_PEAK_BYTES
        or value.get("research_margin_beyond_200gb_reserve_bytes")
        != value["research_quota_bytes"] - PROJECTED_PEAK_BYTES - REQUIRED_FREE_HEADROOM_BYTES
        or value.get("pretransfer_research_write_bound_bytes")
        != PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        or value.get("research_physical_required_available_bytes")
        != value["research_remaining_write_bytes"]
        + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        or value.get("research_physical_slack_bytes")
        != value["research_filesystem_available_bytes"]
        - value["research_physical_required_available_bytes"]
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_ARITHMETIC_INVALID")


def _validate_command_record_contract(
    specification: CapacityCommandSpec,
    item: Mapping[str, Any],
) -> None:
    argv = item.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or argv[1:] != list(specification.argv_tail)
        or item.get("argv_sha256")
        != _sha(json.dumps(argv, separators=(",", ":")).encode())
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_ARGV_INVALID")
    executable_value = argv[0]
    executable_path = PurePosixPath(executable_value)
    if specification.optional_nonblocking:
        availability = item.get("availability_status")
        reason = item.get("availability_reason")
        if reason == "EXECUTABLE_NOT_FOUND":
            valid_authority = (
                executable_value == specification.command_kind
                and item.get("executable_sha256") == "UNAVAILABLE"
                and item.get("executable_size_bytes") == 0
            )
        elif reason == "EXECUTABLE_AUTHORITY_MISMATCH":
            valid_authority = (
                executable_path.is_absolute()
                and executable_path.name == specification.command_kind
                and item.get("executable_sha256") == "UNAVAILABLE"
                and item.get("executable_size_bytes") == 0
            )
        else:
            valid_authority = (
                executable_path.is_absolute()
                and executable_path.name == specification.command_kind
                and item.get("executable_sha256") == EXPECTED_PQUOTA_SHA256
                and item.get("executable_size_bytes") == EXPECTED_PQUOTA_SIZE_BYTES
            )
        if availability == "AVAILABLE" and reason != "AVAILABLE":
            valid_authority = False
        if not valid_authority:
            raise PostReallocationCapacityError(
                "CAPACITY_RECEIPT_COMMAND_EXECUTABLE_INVALID"
            )
    elif (
        not executable_path.is_absolute()
        or executable_path.name != specification.command_kind
        or executable_path.parent
        not in {
            PurePosixPath("/usr/bin"),
            PurePosixPath("/bin"),
            PurePosixPath("/usr/local/bin"),
        }
        or not isinstance(item.get("executable_size_bytes"), int)
        or isinstance(item.get("executable_size_bytes"), bool)
        or item["executable_size_bytes"] <= 0
        or not SHA256_RE.fullmatch(str(item.get("executable_sha256", "")))
    ):
        raise PostReallocationCapacityError(
            "CAPACITY_RECEIPT_COMMAND_EXECUTABLE_INVALID"
        )


def validate_receipt_output(value: Mapping[str, Any]) -> None:
    """Validate the closed detailed receipt without trusting path contents."""
    if not isinstance(value, Mapping) or set(value) != RECEIPT_KEYS:
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != RECEIPT_TYPE
        or value.get("status") != RECEIPT_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_AUTHORITY_INVALID")
    commands = value.get("commands")
    paths = value.get("paths")
    native = value.get("native_quota_authority")
    frozen = value.get("frozen_plan")
    attestations = value.get("no_mutation_attestations")
    display = value.get("pquota_display_crosscheck")
    gate_status = value.get("gate_evaluation_status")
    if (
        not isinstance(commands, Mapping)
        or set(commands) != CAPACITY_COMMAND_ROLES
        or not isinstance(paths, Mapping)
        or set(paths) != {"identities", "mounts", "df"}
        or not isinstance(native, Mapping)
        or native.get("record_unit") != "KIB"
        or native.get("bytes_per_kib") != 1024
        or not isinstance(native.get("rows"), Mapping)
        or set(native["rows"]) != {"backed", "research"}
        or not isinstance(frozen, Mapping)
        or frozen != {
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes": PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
        }
        or not isinstance(attestations, Mapping)
        or attestations != {
            "cloud_requests": 0, "object_listing_repeated": False,
            "storage_inventory_repeated": False, "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0, "real_dicom_extraction": False,
            "echoprime_inference": False, "model_fitting": False,
            "confirmatory_performance_accessed": False, "quota_changed": False,
            "files_moved": 0, "files_deleted": 0, "full_c3_authorized": False,
        }
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_CONTENT_INVALID")
    _validate_gate_evaluation_status(gate_status)
    if any(item != GATE_EVALUATION_PASS for item in gate_status.values()):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_GATE_STATUS_INVALID")
    if (
        not isinstance(display, Mapping)
        or set(display) != {
            "status", "reason", "project_row_match_counts",
            "usage_decimal_places", "rounding_rule",
        }
        or display.get("status") not in {
            DISPLAY_CROSSCHECK_PASS, DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or display.get("reason") not in DISPLAY_CROSSCHECK_REASONS_BY_STATE.get(
            str(display.get("status")), ()
        )
        or display.get("rounding_rule")
        != "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6"
        or not isinstance(display.get("project_row_match_counts"), Mapping)
        or set(display["project_row_match_counts"]) != {"backed", "research"}
        or any(value not in {0, 1} for value in display["project_row_match_counts"].values())
        or not isinstance(display.get("usage_decimal_places"), Mapping)
        or not set(display["usage_decimal_places"]).issubset({"backed", "research"})
        or any(
            not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 6
            for item in display["usage_decimal_places"].values()
        )
        or (
            display.get("status") == DISPLAY_CROSSCHECK_PASS
            and display["project_row_match_counts"] != {"backed": 1, "research": 1}
        )
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_DISPLAY_SCHEMA_INVALID")
    registry = _validated_command_registry(
        CAPACITY_COMMAND_SPECS, require_canonical_roles=True
    )
    for role, specification in registry.items():
        item = commands[role]
        stdout_text = item.get("stdout_text")
        base_keys = {
            "role", "argv", "argv_sha256", "executable_sha256",
            "executable_size_bytes", "exit_status", "stdout_bytes",
            "stdout_sha256", "stdout_text", "stderr_bytes", "stderr_sha256",
        }
        expected_keys = base_keys | (
            {"availability_status", "availability_reason"}
            if role == "pquota" else set()
        )
        if not isinstance(item, Mapping) or set(item) != expected_keys or item.get("role") != role:
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_INVALID")
        if role == "pquota":
            availability = item.get("availability_status")
            reason = item.get("availability_reason")
            if (
                availability not in {"AVAILABLE", "UNAVAILABLE_NONBLOCKING"}
                or not isinstance(reason, str)
                or reason not in {
                    "AVAILABLE", "EXECUTABLE_NOT_FOUND",
                    "EXECUTABLE_AUTHORITY_MISMATCH", "COMMAND_NONZERO_EXIT",
                    "COMMAND_STDERR_PRESENT", "COMMAND_OUTPUT_OVERSIZED",
                    "COMMAND_OUTPUT_NOT_UTF8",
                }
                or not isinstance(stdout_text, str)
                or not isinstance(item.get("stdout_bytes"), int)
                or not isinstance(item.get("stderr_bytes"), int)
                or not SHA256_RE.fullmatch(str(item.get("stdout_sha256", "")))
                or not SHA256_RE.fullmatch(str(item.get("stderr_sha256", "")))
                or item.get("executable_sha256") not in {
                    EXPECTED_PQUOTA_SHA256, "UNAVAILABLE",
                }
                or (availability == "AVAILABLE" and reason != "AVAILABLE")
                or (
                    availability == "UNAVAILABLE_NONBLOCKING"
                    and reason == "AVAILABLE"
                )
                or (availability == "AVAILABLE" and item.get("exit_status") != 0)
                or (availability == "AVAILABLE" and item.get("stderr_bytes") != 0)
                or (
                    availability == "AVAILABLE"
                    and item.get("stdout_bytes") != len(stdout_text.encode("utf-8"))
                )
                or (
                    availability == "AVAILABLE"
                    and item.get("stdout_sha256") != _sha(stdout_text.encode("utf-8"))
                )
            ):
                raise PostReallocationCapacityError("CAPACITY_RECEIPT_PQUOTA_COMMAND_INVALID")
        elif (
            item.get("exit_status") != 0
            or item.get("stderr_bytes") != 0
            or item.get("stderr_sha256") != _sha(b"")
            or not isinstance(stdout_text, str)
            or item.get("stdout_bytes") != len(stdout_text.encode("utf-8"))
            or item.get("stdout_sha256") != _sha(stdout_text.encode("utf-8"))
            or not SHA256_RE.fullmatch(str(item.get("stdout_sha256", "")))
            or not SHA256_RE.fullmatch(str(item.get("executable_sha256", "")))
        ):
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_INVALID")
        _validate_command_record_contract(specification, item)
    pquota_available = commands["pquota"]["availability_status"] == "AVAILABLE"
    if (
        (not pquota_available and display.get("reason") != "COMMAND_UNAVAILABLE")
        or (pquota_available and display.get("reason") == "COMMAND_UNAVAILABLE")
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_DISPLAY_COMMAND_MISMATCH")
    identities = paths["identities"]
    mounts = paths["mounts"]
    df_values = paths["df"]
    if (
        not isinstance(identities, Mapping) or set(identities) != {"backed", "research"}
        or not isinstance(mounts, Mapping) or set(mounts) != {"backed", "research"}
        or not isinstance(df_values, Mapping) or set(df_values) != {"backed", "research"}
    ):
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_PATH_SCHEMA_INVALID")
    for role in ("backed", "research"):
        identity = identities[role]
        mount = mounts[role]
        df_value = df_values[role]
        if (
            not isinstance(identity, Mapping)
            or set(identity) != {
                "path_sha256", "resolved_path_sha256", "device", "inode",
                "is_symlink",
            }
            or identity.get("is_symlink") is not False
            or not SHA256_RE.fullmatch(str(identity.get("path_sha256", "")))
            or not SHA256_RE.fullmatch(str(identity.get("resolved_path_sha256", "")))
            or not isinstance(mount, Mapping)
            or set(mount) != {
                "source_sha256", "target_sha256", "fsroot_sha256",
                "identity_sha256", "source", "target", "fsroot", "fstype",
                "bind",
            }
            or mount.get("fsroot") != "/"
            or mount.get("bind") is not False
            or not all(
                SHA256_RE.fullmatch(str(mount.get(key, "")))
                for key in (
                    "source_sha256", "target_sha256", "fsroot_sha256",
                    "identity_sha256",
                )
            )
            or not isinstance(df_value, Mapping)
            or set(df_value) != {"total", "used", "available"}
            or any(
                not isinstance(df_value.get(key), int)
                or isinstance(df_value.get(key), bool)
                or df_value[key] < 0
                for key in ("total", "used", "available")
            )
        ):
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_PATH_AUTHORITY_INVALID")


def _build_capacity_receipt(
    *,
    attempt_id: str,
    governing_commit: str,
    native_quota_file: Path,
    native_payload: bytes,
    native: Mapping[str, Any],
    commands: Mapping[str, Any],
    identities: Mapping[str, Any],
    mounts: Mapping[str, Any],
    dfs: Mapping[str, Any],
    prior: Mapping[str, Any],
    display: Mapping[str, Any],
    gate_status: Mapping[str, str],
) -> Mapping[str, Any]:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": RECEIPT_TYPE,
        "status": RECEIPT_STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_identity": {
            "effective_uid": os.getuid(),
            "effective_username_sha256": _sha(
                pwd.getpwuid(os.getuid()).pw_name.encode()
            ),
            "hostname_sha256": _sha(socket.gethostname().encode()),
        },
        "native_quota_authority": {
            "path_sha256": _sha(str(native_quota_file).encode()),
            "file_size_bytes": len(native_payload),
            "file_sha256": _sha(native_payload),
            "record_unit": "KIB",
            "bytes_per_kib": 1024,
            "rows": native,
        },
        "commands": commands,
        "paths": {"identities": identities, "mounts": mounts, "df": dfs},
        "prior_authorities": prior,
        "frozen_plan": {
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes": (
                PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
            ),
        },
        "pquota_display_crosscheck": display,
        "gate_evaluation_status": gate_status,
        "no_mutation_attestations": {
            "cloud_requests": 0,
            "object_listing_repeated": False,
            "storage_inventory_repeated": False,
            "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0,
            "real_dicom_extraction": False,
            "echoprime_inference": False,
            "model_fitting": False,
            "confirmatory_performance_accessed": False,
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
            "full_c3_authorized": False,
        },
    }
    if set(receipt) != RECEIPT_KEYS:
        raise PostReallocationCapacityError(
            "CAPACITY_RECEIPT_SCHEMA_INTERNAL_ERROR"
        )
    validate_receipt_output(receipt)
    return receipt


def _project_capacity_aggregate(
    *,
    attempt_id: str,
    governing_commit: str,
    receipt: Mapping[str, Any],
    receipt_payload: bytes,
    commands: Mapping[str, Any],
    native: Mapping[str, Any],
    mounts: Mapping[str, Any],
    dfs: Mapping[str, Any],
    prior: Mapping[str, Any],
    display: Mapping[str, Any],
    gate_status: Mapping[str, str],
    gate_values: Mapping[str, bool],
    research_quota_bytes: int,
    research_usage_bytes: int,
    backed_quota_bytes: int,
    backed_usage_bytes: int,
    remaining_write_bytes: int,
    physical_required_bytes: int,
) -> Mapping[str, Any]:
    validate_receipt_output(receipt)
    if (
        receipt_payload != _canonical(receipt)
        or commands != receipt["commands"]
        or native != receipt["native_quota_authority"]["rows"]
        or mounts != receipt["paths"]["mounts"]
        or dfs != receipt["paths"]["df"]
        or prior != receipt["prior_authorities"]
        or display != receipt["pquota_display_crosscheck"]
        or gate_status != receipt["gate_evaluation_status"]
    ):
        raise PostReallocationCapacityError(
            "CAPACITY_AGGREGATE_RECEIPT_BINDING_INVALID"
        )
    if set(commands) != AGGREGATE_COMMAND_ROLES:
        raise PostReallocationCapacityError(
            "CAPACITY_AGGREGATE_COMMAND_ROLE_SET_INVALID"
        )
    pquota = commands[CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]]
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": AGGREGATE_TYPE,
        "status": AGGREGATE_STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "units": "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER",
        "quota_display_unit_ruling": "BINARY_GIB_ROUNDED_SECONDARY_ONLY",
        "restricted_receipt_size_bytes": len(receipt_payload),
        "restricted_receipt_sha256": _sha(receipt_payload),
        "prior_authorities_hash_verified": True,
        "prior_authorities_closed_schema_verified": True,
        "immutable_original_aggregate_count": prior["original"],
        "immutable_supplemental_aggregate_count": prior["supplemental"],
        "prior_capacity_authority_count": prior["capacity"],
        "prior_production_authority_roles": prior["packet_roles"],
        "prior_production_semantic_gates": prior["packet_gates"],
        "research_quota_bytes": research_quota_bytes,
        "research_usage_bytes": research_usage_bytes,
        "research_quota_remaining_bytes": (
            research_quota_bytes - research_usage_bytes
        ),
        "research_file_quota": int(native["research"]["file_quota"]),
        "research_files_used": int(native["research"]["files_used"]),
        "research_file_slots_remaining": (
            int(native["research"]["file_quota"])
            - int(native["research"]["files_used"])
        ),
        "research_filesystem_total_bytes": dfs["research"]["total"],
        "research_filesystem_used_bytes": dfs["research"]["used"],
        "research_filesystem_available_bytes": dfs["research"]["available"],
        "research_filesystem_type": mounts["research"]["fstype"],
        "research_filesystem_identity_sha256": mounts["research"]["identity_sha256"],
        "backed_quota_bytes": backed_quota_bytes,
        "backed_usage_bytes": backed_usage_bytes,
        "backed_quota_remaining_bytes": backed_quota_bytes - backed_usage_bytes,
        "backed_file_quota": int(native["backed"]["file_quota"]),
        "backed_files_used": int(native["backed"]["files_used"]),
        "backed_file_slots_remaining": (
            int(native["backed"]["file_quota"])
            - int(native["backed"]["files_used"])
        ),
        "backed_filesystem_total_bytes": dfs["backed"]["total"],
        "backed_filesystem_used_bytes": dfs["backed"]["used"],
        "backed_filesystem_available_bytes": dfs["backed"]["available"],
        "backed_filesystem_type": mounts["backed"]["fstype"],
        "backed_filesystem_identity_sha256": mounts["backed"]["identity_sha256"],
        "pquota_to_restricted_mount_reconciliation_verified": True,
        "pquota_display_fileset_mapping_verified": (
            display["status"] == DISPLAY_CROSSCHECK_PASS
        ),
        "pquota_current_not_snapshot_mode_verified": True,
        "pquota_executable_sha256": pquota["executable_sha256"],
        "pquota_executable_authority_status": (
            "PASS_TRUSTED_ROOT_CONTROLLED"
            if pquota["executable_sha256"] == EXPECTED_PQUOTA_SHA256
            else "UNAVAILABLE_NONBLOCKING"
        ),
        "pquota_display_crosscheck": display["status"],
        "pquota_display_crosscheck_reason": display["reason"],
        "pquota_display_backed_project_row_matches": (
            display["project_row_match_counts"]["backed"]
        ),
        "pquota_display_research_project_row_matches": (
            display["project_row_match_counts"]["research"]
        ),
        "pquota_display_rounding_rule": display["rounding_rule"],
        **{
            AGGREGATE_GATE_STATUS_FIELDS[gate]: status
            for gate, status in gate_status.items()
        },
        "research_mount_fsroot_is_root": mounts["research"]["fsroot"] == "/",
        "backed_mount_fsroot_is_root": mounts["backed"]["fsroot"] == "/",
        "mounted_filesystems_distinct": True,
        "mount_targets_distinct": True,
        "filesystem_devices_distinct": True,
        "research_path_is_symlink": False,
        "backed_path_is_symlink": False,
        "research_mount_is_bind": False,
        "backed_mount_is_bind": False,
        "additional_project_quota_row_for_same_principal_observed": False,
        "snapshot_capacity_double_counting_avoided": True,
        "snapshot_presence_independently_enumerated": False,
        "snapshot_accounting_ruling": (
            "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
        ),
        "selected_source_bytes": SELECTED_SOURCE_BYTES,
        "projected_peak_bytes": PROJECTED_PEAK_BYTES,
        "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
        "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
        "research_remaining_write_bytes": remaining_write_bytes,
        "pretransfer_research_write_bound_bytes": (
            PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
        ),
        "research_physical_required_available_bytes": physical_required_bytes,
        "research_quota_margin_above_minimum_bytes": (
            research_quota_bytes - MINIMUM_EFFECTIVE_QUOTA_BYTES
        ),
        "research_quota_slack_after_projected_peak_bytes": (
            research_quota_bytes - PROJECTED_PEAK_BYTES
        ),
        "research_margin_beyond_200gb_reserve_bytes": (
            research_quota_bytes
            - PROJECTED_PEAK_BYTES
            - REQUIRED_FREE_HEADROOM_BYTES
        ),
        "research_physical_slack_bytes": (
            dfs["research"]["available"] - physical_required_bytes
        ),
        "control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "backed_remaining_after_control_burden_bytes": (
            backed_quota_bytes
            - backed_usage_bytes
            - PRESPECIFIED_CONTROL_BURDEN_BYTES
        ),
        "research_additional_file_demand": RESEARCH_ADDITIONAL_FILE_DEMAND,
        "backed_additional_file_demand": CONTROL_ADDITIONAL_FILE_DEMAND,
        "research_quota_gate_passed": gate_values["research_quota_gate"],
        "physical_filesystem_capacity_gate_passed": gate_values[
            "physical_filesystem_capacity_gate"
        ],
        "projected_200gb_reserve_gate_passed": gate_values[
            "projected_200gb_reserve_gate"
        ],
        "research_file_quota_gate_passed": gate_values[
            "research_file_quota_gate"
        ],
        "backed_control_tier_byte_gate_passed": gate_values[
            "backed_control_tier_byte_gate"
        ],
        "backed_control_tier_file_gate_passed": gate_values[
            "backed_control_tier_file_gate"
        ],
        "backed_control_tier_gate_passed": gate_values[
            "backed_control_tier_gate"
        ],
        "owner_reported_backed_free_pool_gb": 50,
        "owner_reported_research_free_pool_gb": 950,
        "owner_reported_research_saas_purchased_gb": 1000,
        "owner_reported_total_research_quota_gb": 1950,
        "purchased_saas_allocation_remains_on_research": True,
        "control_write_binding_evaluated_by_capacity_receipt": False,
        **receipt["no_mutation_attestations"],
    }
    validate_aggregate_output(aggregate)
    return aggregate


def capture(args: argparse.Namespace) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(args.attempt_id) or not COMMIT_RE.fullmatch(args.governing_commit):
        raise PostReallocationCapacityError("CAPACITY_IDENTITY_INVALID")
    _validate_checkout(args.checkout, args.governing_commit)
    if (
        args.research_path != EXPECTED_RESTRICTED_PATHS["research"]
        or args.backed_path != EXPECTED_RESTRICTED_PATHS["backed"]
        or args.native_quota_file != EXPECTED_NATIVE_QUOTA_FILE
        or args.quota_principal != EXPECTED_QUOTA_PRINCIPAL
    ):
        raise PostReallocationCapacityError("CAPACITY_RESTRICTED_PATH_MISMATCH")
    native_metadata = os.lstat(args.native_quota_file)
    if (
        not stat.S_ISREG(native_metadata.st_mode)
        or stat.S_ISLNK(native_metadata.st_mode)
        or native_metadata.st_uid != 0
        or stat.S_IMODE(native_metadata.st_mode) & 0o022
    ):
        raise PostReallocationCapacityError("NATIVE_QUOTA_AUTHORITY_NOT_ROOT_CONTROLLED")
    attempt_root = args.attempt_root
    if attempt_root.is_symlink() or not attempt_root.is_dir() or stat.S_IMODE(attempt_root.stat().st_mode) & 0o077:
        raise PostReallocationCapacityError("ATTEMPT_ROOT_NOT_PRIVATE")
    restricted_root = attempt_root / "restricted" / "capacity"
    aggregate_root = attempt_root / "aggregate"
    if not (attempt_root / "restricted").exists():
        _mkdir_private(attempt_root / "restricted")
    if not aggregate_root.exists():
        _mkdir_private(aggregate_root)
    _mkdir_private(restricted_root)

    prior = _validate_prior(
        original_root=args.original_aggregate_root,
        supplemental_root=args.supplemental_aggregate_root,
        parent=args.prior_capacity_parent,
        composite=args.prior_capacity_composite,
        packet_path=args.prior_production_packet,
    )
    commands = _capture_capacity_commands(args.quota_principal)
    native_payload = _read_regular(args.native_quota_file, maximum=64_000_000)
    native = _parse_native_quota(native_payload)
    paths = {"research": _path_identity(args.research_path), "backed": _path_identity(args.backed_path)}
    mounts = {
        "research": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_mount"]]["stdout_text"],
            args.research_path,
        ),
        "backed": _parse_findmnt(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_mount"]]["stdout_text"],
            args.backed_path,
        ),
    }
    _validate_pquota_restricted_mount_reconciliation(
        native=native, paths=paths, mounts=mounts,
    )
    dfs = {
        "research": _parse_df(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["research_df"]]["stdout_text"],
            mounts["research"],
        ),
        "backed": _parse_df(
            commands[CAPACITY_COMMAND_CONSUMER_ROLES["backed_df"]]["stdout_text"],
            mounts["backed"],
        ),
    }
    if (
        mounts["research"]["source"] == mounts["backed"]["source"]
        or mounts["research"]["target"] == mounts["backed"]["target"]
        or paths["research"]["device"] == paths["backed"]["device"]
        or mounts["research"]["bind"] or mounts["backed"]["bind"]
    ):
        raise PostReallocationCapacityError("FILESYSTEM_DISTINCTNESS_GATE_FAILED")

    rq = int(native["research"]["quota_kib"]) * 1024
    ru = int(native["research"]["usage_kib"]) * 1024
    bq = int(native["backed"]["quota_kib"]) * 1024
    bu = int(native["backed"]["usage_kib"]) * 1024
    remaining_write = max(PROJECTED_PEAK_BYTES - ru, 0)
    physical_required = (
        remaining_write + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
    )
    gate_values = {
        "research_quota_gate": rq >= MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "physical_filesystem_capacity_gate":
            dfs["research"]["available"] >= physical_required,
        "projected_200gb_reserve_gate":
            rq - PROJECTED_PEAK_BYTES >= REQUIRED_FREE_HEADROOM_BYTES,
        "research_file_quota_gate":
            int(native["research"]["file_quota"])
            - int(native["research"]["files_used"])
            >= RESEARCH_ADDITIONAL_FILE_DEMAND,
        "backed_control_tier_byte_gate":
            bq - bu >= PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "backed_control_tier_file_gate":
            int(native["backed"]["file_quota"])
            - int(native["backed"]["files_used"])
            >= CONTROL_ADDITIONAL_FILE_DEMAND,
    }
    gate_values["backed_control_tier_gate"] = (
        gate_values["backed_control_tier_byte_gate"]
        and gate_values["backed_control_tier_file_gate"]
    )
    gate_status = _gate_evaluation_status(gate_values)
    display = _parse_pquota(
        commands[CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]]["stdout_text"],
        native,
        command_available=commands[
            CAPACITY_COMMAND_CONSUMER_ROLES["pquota_display"]
        ]["availability_status"] == "AVAILABLE",
    )
    if display["status"] == DISPLAY_CROSSCHECK_FAIL:
        raise PostReallocationCapacityError(
            "PQUOTA_DISPLAY_CROSSCHECK_BLOCKING",
            gate_evaluation_status=gate_status,
            pquota_display_crosscheck=DISPLAY_CROSSCHECK_FAIL,
        )
    if any(status == GATE_EVALUATION_FAIL for status in gate_status.values()):
        raise PostReallocationCapacityError(
            "CAPACITY_GATE_FAILED",
            gate_evaluation_status=gate_status,
            pquota_display_crosscheck=str(display["status"]),
        )

    receipt = _build_capacity_receipt(
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        native_quota_file=args.native_quota_file,
        native_payload=native_payload,
        native=native,
        commands=commands,
        identities=paths,
        mounts=mounts,
        dfs=dfs,
        prior=prior,
        display=display,
        gate_status=gate_status,
    )
    receipt_path = restricted_root / "post_reallocation_capacity.restricted.json"
    receipt_payload = _canonical(receipt)
    _write_new(receipt_path, receipt_payload, private=True)

    aggregate = _project_capacity_aggregate(
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        receipt=receipt,
        receipt_payload=receipt_payload,
        commands=commands,
        native=native,
        mounts=mounts,
        dfs=dfs,
        prior=prior,
        display=display,
        gate_status=gate_status,
        gate_values=gate_values,
        research_quota_bytes=rq,
        research_usage_bytes=ru,
        backed_quota_bytes=bq,
        backed_usage_bytes=bu,
        remaining_write_bytes=remaining_write,
        physical_required_bytes=physical_required,
    )
    aggregate_path = aggregate_root / "lvef_c3_post_reallocation_capacity.summary.json"
    _write_new(aggregate_path, _canonical(aggregate), private=True)
    return aggregate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--quota-principal", default=EXPECTED_QUOTA_PRINCIPAL)
    parser.add_argument("--native-quota-file", type=Path, default=Path("/usr/local/etc/quota/project.quota"))
    parser.add_argument("--research-path", type=Path, required=True)
    parser.add_argument("--backed-path", type=Path, required=True)
    parser.add_argument("--original-aggregate-root", type=Path, required=True)
    parser.add_argument("--supplemental-aggregate-root", type=Path, required=True)
    parser.add_argument("--prior-capacity-parent", type=Path, required=True)
    parser.add_argument("--prior-capacity-composite", type=Path, required=True)
    parser.add_argument("--prior-production-packet", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        value = capture(build_parser().parse_args(argv))
    except (PostReallocationCapacityError, OSError) as exc:
        code = exc.code if isinstance(exc, PostReallocationCapacityError) else "CAPACITY_IO_ERROR"
        gate_status = (
            exc.gate_evaluation_status
            if isinstance(exc, PostReallocationCapacityError)
            and exc.gate_evaluation_status is not None
            else _not_evaluated_gate_status()
        )
        display_status = (
            exc.pquota_display_crosscheck
            if isinstance(exc, PostReallocationCapacityError)
            and exc.pquota_display_crosscheck is not None
            else GATE_EVALUATION_NOT_EVALUATED
        )
        print(json.dumps({
            "status": "FAIL", "error_code": code,
            "gate_evaluation_status": gate_status,
            "pquota_display_crosscheck": display_status,
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": value["status"],
        "research_quota_gate_passed": value["research_quota_gate_passed"],
        "physical_filesystem_capacity_gate_passed": value["physical_filesystem_capacity_gate_passed"],
        "projected_200gb_reserve_gate_passed": value["projected_200gb_reserve_gate_passed"],
        "research_file_quota_gate_passed": value["research_file_quota_gate_passed"],
        "backed_control_tier_gate_passed": value["backed_control_tier_gate_passed"],
        "gate_evaluation_status": {
            gate: value[field]
            for gate, field in AGGREGATE_GATE_STATUS_FIELDS.items()
        },
        "pquota_display_crosscheck": value["pquota_display_crosscheck"],
        "cloud_requests": 0, "scheduler_jobs_submitted": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
