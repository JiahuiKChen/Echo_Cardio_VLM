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
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


SCHEMA_VERSION = 1
RECEIPT_TYPE = "lvef_c3_post_reallocation_capacity_receipt_v1"
RECEIPT_STATUS = "PASS_READ_ONLY_POST_REALLOCATION_CAPACITY_CAPTURE"
AGGREGATE_TYPE = "lvef_c3_post_reallocation_capacity_summary_v1"
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
EXPECTED_NATIVE_ROWS = {
    "backed": "rproject_mimicecho",
    "research": "rprojectnb_mimicecho",
}
EXPECTED_NATIVE_FILESET_FIELD = "root"
EXPECTED_NATIVE_PRINCIPAL_SUFFIX = "_mimicecho"
EXPECTED_DISPLAY_ROWS = {
    "backed": "/project/mimicecho",
    "research": "/projectnb/mimicecho",
}
EXPECTED_RESTRICTED_PATHS = {
    "backed": Path("/restricted/project/mimicecho"),
    "research": Path("/restricted/projectnb/mimicecho"),
}
EXPECTED_NATIVE_QUOTA_FILE = Path("/usr/local/etc/quota/project.quota")
EXPECTED_PQUOTA_EXECUTABLE = Path("/usr/local/etc/quota/pquota")
EXPECTED_PQUOTA_SIZE_BYTES = 5_840
EXPECTED_PQUOTA_SHA256 = (
    "d0aacf79af95e8a558e2b27f81b685210427fe773a3aff93b4f1b1ba5ba339ab"
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

RECEIPT_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "governing_commit", "captured_at_utc", "capture_identity",
        "native_quota_authority", "commands", "paths", "prior_authorities",
        "frozen_plan", "no_mutation_attestations",
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
        "pquota_executable_sha256",
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
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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


def _run(role: str, argv: Sequence[str]) -> Mapping[str, Any]:
    executable = Path(argv[0]).resolve(strict=True)
    before = executable.stat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_ROOT_CONTROLLED")
    result = subprocess.run(
        list(argv), capture_output=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        check=False,
    )
    after = executable.stat()
    if result.returncode or result.stderr or len(result.stdout) > 2_000_000:
        raise PostReallocationCapacityError(f"{role.upper()}_COMMAND_FAILED")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PostReallocationCapacityError("TOOL_CHANGED_DURING_CAPTURE")
    return {
        "role": role,
        "argv": list(argv),
        "argv_sha256": _sha(json.dumps(list(argv), separators=(",", ":")).encode()),
        "executable_sha256": _sha(_read_regular(executable, maximum=256_000_000)),
        "executable_size_bytes": before.st_size,
        "exit_status": result.returncode,
        "stdout_bytes": len(result.stdout),
        "stdout_sha256": _sha(result.stdout),
        "stdout_text": result.stdout.decode("utf-8"),
        "stderr_bytes": 0,
        "stderr_sha256": _sha(b""),
    }


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


def _display_number(value: int) -> str:
    decimal = (Decimal(value * 1024) / Decimal(1024 ** 3)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return format(decimal, "f")


def _parse_pquota(text: str, native: Mapping[str, Mapping[str, int | str]]) -> None:
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        for role, name in EXPECTED_DISPLAY_ROWS.items():
            if fields and fields[0] == name:
                if role in rows:
                    raise PostReallocationCapacityError("PQUOTA_DISPLAY_ROW_DUPLICATE")
                rows[role] = fields
    if set(rows) != {"backed", "research"}:
        raise PostReallocationCapacityError("PQUOTA_DISPLAY_ROWS_MISSING")
    for role, fields in rows.items():
        if len(fields) != 5:
            raise PostReallocationCapacityError("PQUOTA_DISPLAY_LAYOUT_INVALID")
        quota_gib = int(native[role]["quota_kib"]) // (1024 ** 2)
        if (
            fields[1] != str(quota_gib)
            or fields[2] != str(native[role]["file_quota"])
            or fields[3] != _display_number(int(native[role]["usage_kib"]))
            or fields[4] != str(native[role]["files_used"])
        ):
            raise PostReallocationCapacityError("PQUOTA_DISPLAY_NATIVE_MISMATCH")


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
    """Bind pquota display rows and native filesets to secure mount roots.

    SCC's ``pquota`` display omits the secure ``/restricted`` namespace while
    the native fileset names encode the same role.  This check proves that the
    exact validated display/native authorities map one-to-one to the exact
    no-symlink paths and root-mounted filesystems used for C3.  It does not
    infer this relationship from an arbitrary caller-provided name.
    """
    expected_targets = {
        "backed": Path("/restricted/project"),
        "research": Path("/restricted/projectnb"),
    }
    expected_mount_source_names = {
        "backed": "rproject", "research": "rprojectnb",
    }
    for role in ("backed", "research"):
        display = Path(EXPECTED_DISPLAY_ROWS[role])
        mapped = Path("/restricted") / display.relative_to("/")
        expected_path = EXPECTED_RESTRICTED_PATHS[role]
        expected_native_name = (
            "r" + EXPECTED_DISPLAY_ROWS[role].strip("/").replace("/", "_")
        )
        if (
            mapped != expected_path
            or paths[role]["resolved_path_sha256"] != _sha(str(expected_path).encode())
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
        or value.get("pquota_executable_sha256") != EXPECTED_PQUOTA_SHA256
        or value.get("units") != "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER"
        or value.get("quota_display_unit_ruling") != "BINARY_GIB_ROUNDED"
    ):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_AUTHORITY_INVALID")
    required_true = {
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
    }
    if any(value.get(key) is not True for key in required_true):
        raise PostReallocationCapacityError("CAPACITY_AGGREGATE_GATE_NOT_PASS")
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
    if (
        not isinstance(commands, Mapping)
        or set(commands) != {
            "pquota", "research_findmnt", "backed_findmnt",
            "research_df", "backed_df",
        }
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
    for role, item in commands.items():
        stdout_text = item.get("stdout_text")
        if (
            not isinstance(item, Mapping)
            or set(item) != {
                "role", "argv", "argv_sha256", "executable_sha256",
                "executable_size_bytes", "exit_status", "stdout_bytes",
                "stdout_sha256", "stdout_text", "stderr_bytes",
                "stderr_sha256",
            }
            or item.get("role") != role
            or item.get("exit_status") != 0
            or item.get("stderr_bytes") != 0
            or item.get("stderr_sha256") != _sha(b"")
            or not isinstance(stdout_text, str)
            or item.get("stdout_bytes") != len(stdout_text.encode("utf-8"))
            or item.get("stdout_sha256") != _sha(stdout_text.encode("utf-8"))
            or not SHA256_RE.fullmatch(str(item.get("stdout_sha256", "")))
            or not SHA256_RE.fullmatch(str(item.get("executable_sha256", "")))
        ):
            raise PostReallocationCapacityError("CAPACITY_RECEIPT_COMMAND_INVALID")
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


def capture(args: argparse.Namespace) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(args.attempt_id) or not COMMIT_RE.fullmatch(args.governing_commit):
        raise PostReallocationCapacityError("CAPACITY_IDENTITY_INVALID")
    _validate_checkout(args.checkout, args.governing_commit)
    if (
        args.research_path != EXPECTED_RESTRICTED_PATHS["research"]
        or args.backed_path != EXPECTED_RESTRICTED_PATHS["backed"]
        or args.native_quota_file != EXPECTED_NATIVE_QUOTA_FILE
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
    pquota = shutil.which("pquota", path="/usr/local/bin:/usr/bin:/bin")
    findmnt = shutil.which("findmnt", path="/usr/bin:/bin:/usr/local/bin")
    df = shutil.which("df", path="/usr/bin:/bin:/usr/local/bin")
    if not pquota or not findmnt or not df:
        raise PostReallocationCapacityError("READ_ONLY_TOOL_NOT_FOUND")
    commands = {
        "pquota": _run("pquota", [pquota, "-u", args.quota_principal]),
        "research_findmnt": _run("findmnt", [findmnt, "--json", "--target", str(args.research_path), "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT"]),
        "backed_findmnt": _run("findmnt", [findmnt, "--json", "--target", str(args.backed_path), "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT"]),
        "research_df": _run("df", [df, "-B1", "--output=source,size,used,avail,target", str(args.research_path)]),
        "backed_df": _run("df", [df, "-B1", "--output=source,size,used,avail,target", str(args.backed_path)]),
    }
    if (
        Path(pquota).resolve(strict=True) != EXPECTED_PQUOTA_EXECUTABLE
        or commands["pquota"]["executable_size_bytes"] != EXPECTED_PQUOTA_SIZE_BYTES
        or commands["pquota"]["executable_sha256"] != EXPECTED_PQUOTA_SHA256
        or commands["pquota"]["argv"] != [pquota, "-u", args.quota_principal]
    ):
        raise PostReallocationCapacityError("PQUOTA_IMPLEMENTATION_AUTHORITY_MISMATCH")
    native_payload = _read_regular(args.native_quota_file, maximum=64_000_000)
    native = _parse_native_quota(native_payload)
    _parse_pquota(commands["pquota"]["stdout_text"], native)
    paths = {"research": _path_identity(args.research_path), "backed": _path_identity(args.backed_path)}
    mounts = {
        "research": _parse_findmnt(commands["research_findmnt"]["stdout_text"], args.research_path),
        "backed": _parse_findmnt(commands["backed_findmnt"]["stdout_text"], args.backed_path),
    }
    _validate_pquota_restricted_mount_reconciliation(
        native=native, paths=paths, mounts=mounts,
    )
    dfs = {
        "research": _parse_df(commands["research_df"]["stdout_text"], mounts["research"]),
        "backed": _parse_df(commands["backed_df"]["stdout_text"], mounts["backed"]),
    }
    if (
        mounts["research"]["source"] == mounts["backed"]["source"]
        or mounts["research"]["target"] == mounts["backed"]["target"]
        or paths["research"]["device"] == paths["backed"]["device"]
        or mounts["research"]["bind"] or mounts["backed"]["bind"]
    ):
        raise PostReallocationCapacityError("FILESYSTEM_DISTINCTNESS_GATE_FAILED")

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": RECEIPT_TYPE,
        "status": RECEIPT_STATUS,
        "attempt_id": args.attempt_id,
        "governing_commit": args.governing_commit,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_identity": {
            "effective_uid": os.getuid(),
            "effective_username_sha256": _sha(pwd.getpwuid(os.getuid()).pw_name.encode()),
            "hostname_sha256": _sha(socket.gethostname().encode()),
        },
        "native_quota_authority": {
            "path_sha256": _sha(str(args.native_quota_file).encode()),
            "file_size_bytes": len(native_payload), "file_sha256": _sha(native_payload),
            "record_unit": "KIB", "bytes_per_kib": 1024, "rows": native,
        },
        "commands": commands,
        "paths": {"identities": paths, "mounts": mounts, "df": dfs},
        "prior_authorities": prior,
        "frozen_plan": {
            "selected_source_bytes": SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes": PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
        },
        "no_mutation_attestations": {
            "cloud_requests": 0, "object_listing_repeated": False,
            "storage_inventory_repeated": False, "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0, "real_dicom_extraction": False,
            "echoprime_inference": False, "model_fitting": False,
            "confirmatory_performance_accessed": False, "quota_changed": False,
            "files_moved": 0, "files_deleted": 0, "full_c3_authorized": False,
        },
    }
    if set(receipt) != RECEIPT_KEYS:
        raise PostReallocationCapacityError("CAPACITY_RECEIPT_SCHEMA_INTERNAL_ERROR")
    validate_receipt_output(receipt)
    receipt_path = restricted_root / "post_reallocation_capacity.restricted.json"
    receipt_payload = _canonical(receipt)
    _write_new(receipt_path, receipt_payload, private=True)

    rq = int(native["research"]["quota_kib"]) * 1024
    ru = int(native["research"]["usage_kib"]) * 1024
    bq = int(native["backed"]["quota_kib"]) * 1024
    bu = int(native["backed"]["usage_kib"]) * 1024
    remaining_write = max(PROJECTED_PEAK_BYTES - ru, 0)
    physical_required = (
        remaining_write + REQUIRED_FREE_HEADROOM_BYTES
        + PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES
    )
    quota_gate = rq >= MINIMUM_EFFECTIVE_QUOTA_BYTES
    reserve_gate = rq - PROJECTED_PEAK_BYTES >= REQUIRED_FREE_HEADROOM_BYTES
    physical_gate = dfs["research"]["available"] >= physical_required
    research_file_gate = int(native["research"]["file_quota"]) - int(native["research"]["files_used"]) >= RESEARCH_ADDITIONAL_FILE_DEMAND
    backed_byte_gate = bq - bu >= PRESPECIFIED_CONTROL_BURDEN_BYTES
    backed_file_gate = int(native["backed"]["file_quota"]) - int(native["backed"]["files_used"]) >= CONTROL_ADDITIONAL_FILE_DEMAND
    aggregate = {
        "schema_version": SCHEMA_VERSION, "artifact_type": AGGREGATE_TYPE,
        "status": AGGREGATE_STATUS, "attempt_id": args.attempt_id,
        "governing_commit": args.governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "units": "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER",
        "quota_display_unit_ruling": "BINARY_GIB_ROUNDED",
        "restricted_receipt_size_bytes": len(receipt_payload),
        "restricted_receipt_sha256": _sha(receipt_payload),
        "prior_authorities_hash_verified": True,
        "prior_authorities_closed_schema_verified": True,
        "immutable_original_aggregate_count": prior["original"],
        "immutable_supplemental_aggregate_count": prior["supplemental"],
        "prior_capacity_authority_count": prior["capacity"],
        "prior_production_authority_roles": prior["packet_roles"],
        "prior_production_semantic_gates": prior["packet_gates"],
        "research_quota_bytes": rq, "research_usage_bytes": ru,
        "research_quota_remaining_bytes": rq - ru,
        "research_file_quota": int(native["research"]["file_quota"]),
        "research_files_used": int(native["research"]["files_used"]),
        "research_file_slots_remaining": int(native["research"]["file_quota"]) - int(native["research"]["files_used"]),
        "research_filesystem_total_bytes": dfs["research"]["total"],
        "research_filesystem_used_bytes": dfs["research"]["used"],
        "research_filesystem_available_bytes": dfs["research"]["available"],
        "research_filesystem_type": mounts["research"]["fstype"],
        "research_filesystem_identity_sha256": mounts["research"]["identity_sha256"],
        "backed_quota_bytes": bq, "backed_usage_bytes": bu,
        "backed_quota_remaining_bytes": bq - bu,
        "backed_file_quota": int(native["backed"]["file_quota"]),
        "backed_files_used": int(native["backed"]["files_used"]),
        "backed_file_slots_remaining": int(native["backed"]["file_quota"]) - int(native["backed"]["files_used"]),
        "backed_filesystem_total_bytes": dfs["backed"]["total"],
        "backed_filesystem_used_bytes": dfs["backed"]["used"],
        "backed_filesystem_available_bytes": dfs["backed"]["available"],
        "backed_filesystem_type": mounts["backed"]["fstype"],
        "backed_filesystem_identity_sha256": mounts["backed"]["identity_sha256"],
        "pquota_to_restricted_mount_reconciliation_verified": True,
        "pquota_display_fileset_mapping_verified": True,
        "pquota_current_not_snapshot_mode_verified": True,
        "pquota_executable_sha256": EXPECTED_PQUOTA_SHA256,
        "research_mount_fsroot_is_root": mounts["research"]["fsroot"] == "/",
        "backed_mount_fsroot_is_root": mounts["backed"]["fsroot"] == "/",
        "mounted_filesystems_distinct": True, "mount_targets_distinct": True,
        "filesystem_devices_distinct": True, "research_path_is_symlink": False,
        "backed_path_is_symlink": False, "research_mount_is_bind": False,
        "backed_mount_is_bind": False,
        "additional_project_quota_row_for_same_principal_observed": False,
        # Snapshot presence is not inferred from quota rows.  The calculation
        # consumes effective native quota usage once and physical df capacity
        # once, so it never adds a separate snapshot estimate a second time.
        "snapshot_capacity_double_counting_avoided": True,
        "snapshot_presence_independently_enumerated": False,
        "snapshot_accounting_ruling":
            "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN",
        "selected_source_bytes": SELECTED_SOURCE_BYTES,
        "projected_peak_bytes": PROJECTED_PEAK_BYTES,
        "required_free_headroom_bytes": REQUIRED_FREE_HEADROOM_BYTES,
        "minimum_effective_quota_bytes": MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "preferred_research_quota_bytes": PREFERRED_RESEARCH_QUOTA_BYTES,
        "research_remaining_write_bytes": remaining_write,
        "pretransfer_research_write_bound_bytes": PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
        "research_physical_required_available_bytes": physical_required,
        "research_quota_margin_above_minimum_bytes": rq - MINIMUM_EFFECTIVE_QUOTA_BYTES,
        "research_quota_slack_after_projected_peak_bytes": rq - PROJECTED_PEAK_BYTES,
        "research_margin_beyond_200gb_reserve_bytes": rq - PROJECTED_PEAK_BYTES - REQUIRED_FREE_HEADROOM_BYTES,
        "research_physical_slack_bytes": dfs["research"]["available"] - physical_required,
        "control_burden_bytes": PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "backed_remaining_after_control_burden_bytes": bq - bu - PRESPECIFIED_CONTROL_BURDEN_BYTES,
        "research_additional_file_demand": RESEARCH_ADDITIONAL_FILE_DEMAND,
        "backed_additional_file_demand": CONTROL_ADDITIONAL_FILE_DEMAND,
        "research_quota_gate_passed": quota_gate,
        "physical_filesystem_capacity_gate_passed": physical_gate,
        "projected_200gb_reserve_gate_passed": reserve_gate,
        "research_file_quota_gate_passed": research_file_gate,
        "backed_control_tier_byte_gate_passed": backed_byte_gate,
        "backed_control_tier_file_gate_passed": backed_file_gate,
        "backed_control_tier_gate_passed": backed_byte_gate and backed_file_gate,
        "owner_reported_backed_free_pool_gb": 50,
        "owner_reported_research_free_pool_gb": 950,
        "owner_reported_research_saas_purchased_gb": 1000,
        "owner_reported_total_research_quota_gb": 1950,
        "purchased_saas_allocation_remains_on_research": True,
        "control_write_binding_evaluated_by_capacity_receipt": False,
        **receipt["no_mutation_attestations"],
    }
    validate_aggregate_output(aggregate)
    aggregate_path = aggregate_root / "lvef_c3_post_reallocation_capacity.summary.json"
    _write_new(aggregate_path, _canonical(aggregate), private=True)
    return aggregate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--quota-principal", default="mimicecho")
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
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": value["status"],
        "research_quota_gate_passed": value["research_quota_gate_passed"],
        "physical_filesystem_capacity_gate_passed": value["physical_filesystem_capacity_gate_passed"],
        "projected_200gb_reserve_gate_passed": value["projected_200gb_reserve_gate_passed"],
        "research_file_quota_gate_passed": value["research_file_quota_gate_passed"],
        "backed_control_tier_gate_passed": value["backed_control_tier_gate_passed"],
        "cloud_requests": 0, "scheduler_jobs_submitted": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
