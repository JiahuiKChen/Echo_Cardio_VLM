#!/usr/bin/env python3
"""Build the closed Phase 1E-F capacity/backup pretransfer authority.

This aggregate binds, without exporting paths, the independently validated
post-reallocation capacity evidence, backup and restore evidence, and exact
unexecuted first-batch command.  It has no cloud, scheduler, DICOM, model, or
authorization operation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import yaml


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "lvef_c3_phase1ef_pretransfer_lock_v1"
STATUS = "PASS_POST_REALLOCATION_CAPACITY_BACKUP_AND_OFFLINE_LOCK"
ATTEMPT_RE = re.compile(
    r"^lvef_multitask_phase1ef_post_reallocation_lock_attempt_[0-9]{3}$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]+)?(?:Z|[+]00:00)$"
)

AUTHORITY_ROLES = frozenset(
    {
        "post_reallocation_capacity_receipt",
        "post_reallocation_capacity_aggregate",
        "backup_manifest",
        "backup_recovery_aggregate",
        "restore_test_receipt",
        "future_first_batch_command",
    }
)
CAPACITY_GATE_KEYS = frozenset(
    {
        "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed",
        "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed",
        "backed_control_tier_gate_passed",
        "pquota_to_restricted_mount_reconciliation_verified",
    }
)
BACKUP_GATE_KEYS = frozenset(
    {
        "backup_verified",
        "restore_test_passed",
        "exact_git_commit_restored",
        "git_fsck_passed",
        "linked_worktree_recreated",
        "manifest_restore_checksum_equality",
        "owner_private_permissions_passed",
        "no_symlinks",
        "no_special_files",
        "credential_material_absent",
        "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent",
        "live_git_unchanged",
    }
)
WRITE_BINDING_KEYS = frozenset(
    {
        "all_substantial_writes_bound_to_research",
        "backed_control_writes_bounded",
        "raw_dicom_deletion_prohibited",
        "cache_retirement_requires_separate_authorization",
    }
)
EXECUTION_ATTESTATIONS = {
    "cloud_requests": 0,
    "object_listing_repeated": False,
    "storage_inventory_repeated": False,
    "scheduler_jobs_submitted": 0,
    "dicom_bodies_downloaded": 0,
    "real_dicom_extraction": False,
    "echoprime_inference": False,
    "model_fitting": False,
    "confirmatory_performance_accessed": False,
    "files_moved_from_live_worktrees": 0,
    "files_deleted": 0,
    "quota_changed": False,
}
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "governing_commit",
        "created_at_utc",
        "authority",
        "capacity_gates",
        "backup_recovery_gates",
        "write_binding_gates",
        "execution_attestations",
        "authorization_scopes_granted",
        "full_c3_status",
    }
)

BACKUP_ARTIFACT_TYPE = "lvef_c3_control_authority_backup_recovery_summary_v1"
BACKUP_STATUS = "PASS_BACKUP_RECOVERY_WITNESS"


class Phase1EFPretransferError(ValueError):
    """Fail-closed aggregate-safe error."""


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise Phase1EFPretransferError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _require_no_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise Phase1EFPretransferError("AUTHORITY_PATH_NOT_ABSOLUTE")
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise Phase1EFPretransferError("AUTHORITY_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(metadata.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise Phase1EFPretransferError("AUTHORITY_SYMLINK_ANCESTOR")
        if not stat.S_ISDIR(metadata.st_mode):
            raise Phase1EFPretransferError("AUTHORITY_ANCESTOR_NOT_DIRECTORY")


def _read_private(path: Path) -> bytes:
    _require_no_symlink_ancestors(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Phase1EFPretransferError("AUTHORITY_NOT_REGULAR") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise Phase1EFPretransferError("AUTHORITY_NOT_OWNER_PRIVATE")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_private_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            _read_private(path).decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except Phase1EFPretransferError:
        raise
    except Exception as exc:
        raise Phase1EFPretransferError("AUTHORITY_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise Phase1EFPretransferError("AUTHORITY_JSON_NOT_MAPPING")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _binding(path: Path) -> Mapping[str, Any]:
    payload = _read_private(path)
    return {"size_bytes": len(payload), "sha256": _sha256(payload)}


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise Phase1EFPretransferError("GIT_AUTHORITY_INSPECTION_FAILED")
    return completed.stdout.strip()


def _validate_checkout(checkout: Path, governing_commit: str) -> Path:
    _require_no_symlink_ancestors(checkout / ".authority_leaf")
    if checkout.is_symlink() or not checkout.is_dir():
        raise Phase1EFPretransferError("CHECKOUT_NOT_REGULAR")
    root = checkout.resolve(strict=True)
    if (
        _git(root, "rev-parse", "--show-toplevel") != str(root)
        or _git(root, "branch", "--show-current")
        != "codex/lvef-multitask-revalidation"
        or _git(root, "rev-parse", "HEAD") != governing_commit
        or _git(root, "status", "--porcelain", "--untracked-files=no")
    ):
        raise Phase1EFPretransferError("CHECKOUT_AUTHORITY_MISMATCH")
    for relative in (
        "scripts/build_lvef_c3_phase1ef_pretransfer_lock.py",
        "scripts/build_lvef_c3_first_batch_command.py",
    ):
        if _git(root, "ls-files", "--error-unmatch", relative) != relative:
            raise Phase1EFPretransferError("REQUIRED_BUILDER_NOT_TRACKED")
    return root


def _validate_write_bindings(checkout: Path) -> Mapping[str, bool]:
    """Revalidate the committed production-path and deletion boundaries."""
    contract_path = checkout / "configs/lvef_c3_orchestration_v2.yaml"
    scheduler_path = checkout / "scripts/lvef_c3_production_scheduler_common.sh"
    dispatcher_path = checkout / "scripts/scc_dispatch_lvef_c3_production_v2.sh"
    control_path = checkout / "scripts/prepare_lvef_c3_production_control_plane.py"
    try:
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        scheduler = scheduler_path.read_text(encoding="utf-8")
        dispatcher = dispatcher_path.read_text(encoding="utf-8")
        control = control_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise Phase1EFPretransferError("WRITE_BINDING_AUTHORITY_UNREADABLE") from exc
    if not isinstance(contract, Mapping):
        raise Phase1EFPretransferError("WRITE_BINDING_CONTRACT_INVALID")
    storage = contract.get("storage")
    cache = contract.get("cache_retirement")
    batching = contract.get("batching")
    if not all(isinstance(item, Mapping) for item in (storage, cache, batching)):
        raise Phase1EFPretransferError("WRITE_BINDING_CONTRACT_INVALID")
    path_values = [
        storage.get("production_root"), storage.get("raw_root"),
        storage.get("extracted_cache_root"), storage.get("state_root"),
    ]
    if any(
        not isinstance(value, str)
        or not value.startswith("/restricted/projectnb/mimicecho/")
        or "/../" in value
        for value in path_values
    ):
        raise Phase1EFPretransferError("SUBSTANTIAL_WRITE_OUTSIDE_RESEARCH")
    required_scheduler_fragments = (
        'export TMPDIR="$job_root/tmp"',
        'export XDG_CACHE_HOME="$job_root/cache/xdg"',
        'export TORCH_HOME="$job_root/cache/torch"',
        'export MPLCONFIGDIR="$job_root/cache/matplotlib"',
        'export NUMBA_CACHE_DIR="$job_root/cache/numba"',
        'export PIP_CACHE_DIR="$job_root/cache/pip"',
        "[[ \"$candidate\" == /restricted/projectnb/* ]]",
    )
    if any(fragment not in scheduler for fragment in required_scheduler_fragments):
        raise Phase1EFPretransferError("SCHEDULER_WRITE_BINDING_INCOMPLETE")
    if (
        'LOG_ROOT="$LVEF_C3_PRODUCTION_ROOT/' not in dispatcher
        or 'WORK_ROOT="$LVEF_C3_PRODUCTION_ROOT/' not in dispatcher
        or '-o "$LOG_ROOT" -e "$LOG_ROOT"' not in dispatcher
        or "qsub -V" in dispatcher
        or 'startswith("/restricted/projectnb/")' not in control
    ):
        raise Phase1EFPretransferError("DISPATCH_OR_CONTROL_WRITE_BINDING_INVALID")
    if (
        storage.get("raw_deletion_enabled") is not False
        or cache.get("raw_dicom_deletion_default_authorized") is not False
        or cache.get("raw_dicom_deletion_owner_authorizable") is not False
        or cache.get("extracted_cache_retirement_default_authorized") is not False
        or cache.get("explicit_owner_authorization_required") is not True
        or batching.get("default_active_batches") != 1
    ):
        raise Phase1EFPretransferError("DELETION_OR_CONCURRENCY_POLICY_INVALID")
    return {key: True for key in WRITE_BINDING_KEYS}


def _validate_capacity(
    value: Mapping[str, Any], *, receipt: Mapping[str, Any],
    receipt_binding: Mapping[str, Any], attempt_id: str, governing_commit: str,
) -> None:
    try:
        module = importlib.import_module("capture_lvef_c3_post_reallocation_capacity")
        module.validate_receipt_output(receipt)
        module.validate_aggregate_output(value)
    except (ImportError, AttributeError) as exc:
        raise Phase1EFPretransferError("CAPACITY_VALIDATOR_UNAVAILABLE") from exc
    except Exception as exc:
        raise Phase1EFPretransferError("CAPACITY_AGGREGATE_INVALID") from exc
    if (
        receipt.get("attempt_id") != attempt_id
        or receipt.get("governing_commit") != governing_commit
        or value.get("attempt_id") != attempt_id
        or value.get("governing_commit") != governing_commit
    ):
        raise Phase1EFPretransferError("CAPACITY_AUTHORITY_IDENTITY_MISMATCH")
    if (
        value.get("restricted_receipt_sha256") != receipt_binding.get("sha256")
        or value.get("restricted_receipt_size_bytes")
        != receipt_binding.get("size_bytes")
    ):
        raise Phase1EFPretransferError("CAPACITY_RECEIPT_BINDING_MISMATCH")
    if any(value.get(key) is not True for key in CAPACITY_GATE_KEYS):
        raise Phase1EFPretransferError("CAPACITY_GATE_NOT_PASS")


def _validate_backup(
    value: Mapping[str, Any], *, manifest: Mapping[str, Any],
    restore: Mapping[str, Any], attempt_id: str, governing_commit: str,
    manifest_binding: Mapping[str, Any], restore_binding: Mapping[str, Any],
) -> None:
    try:
        module = importlib.import_module("build_lvef_c3_backup_recovery_witness")
        module.validate_backup_manifest(manifest)
        module.validate_restore_receipt(restore)
        module.validate_aggregate_output(value)
    except (ImportError, AttributeError) as exc:
        raise Phase1EFPretransferError("BACKUP_VALIDATOR_UNAVAILABLE") from exc
    except Exception as exc:
        raise Phase1EFPretransferError("BACKUP_AGGREGATE_INVALID") from exc
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type") != BACKUP_ARTIFACT_TYPE
        or value.get("status") != BACKUP_STATUS
        or value.get("attempt_id") != attempt_id
        or value.get("governing_commit") != governing_commit
        or manifest.get("attempt_id") != attempt_id
        or manifest.get("governing_commit") != governing_commit
        or restore.get("attempt_id") != attempt_id
        or restore.get("governing_commit") != governing_commit
        or value.get("backup_manifest_sha256") != manifest_binding.get("sha256")
        or value.get("restore_receipt_sha256") != restore_binding.get("sha256")
        or restore.get("backup_manifest_sha256") != manifest_binding.get("sha256")
        or value.get("policy_sha256") != manifest.get("policy_sha256")
        or value.get("policy_sha256") != restore.get("policy_sha256")
        or value.get("selection_sha256") != manifest.get("selection_sha256")
        or value.get("recovery_documentation_sha256")
        != manifest.get("recovery_documentation_sha256")
        or value.get("git_bundle_sha256") != restore.get("git_bundle_sha256")
        or value.get("tracked_file_set_sha256")
        != restore.get("tracked_file_set_sha256")
        or any(value.get(key) is not True for key in BACKUP_GATE_KEYS)
        or value.get("cloud_requests") != 0
        or value.get("scheduler_jobs_submitted") != 0
        or value.get("object_listing_repeated") is not False
        or value.get("dicom_bodies_downloaded") != 0
        or value.get("full_c3_authorized") is not False
    ):
        raise Phase1EFPretransferError("BACKUP_RECOVERY_AUTHORITY_INVALID")
    for key in (
        "policy_sha256",
        "selection_sha256",
        "backup_manifest_sha256",
        "restore_receipt_sha256",
        "git_bundle_sha256",
    ):
        if not SHA256_RE.fullmatch(str(value.get(key, ""))):
            raise Phase1EFPretransferError("BACKUP_HASH_INVALID")
    for key in (
        "copied_file_count",
        "copied_bytes",
        "restored_file_count",
        "restored_bytes",
        "git_bundle_bytes",
    ):
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise Phase1EFPretransferError("BACKUP_COUNT_INVALID")
    classification = value.get("classification_counts")
    expected_classes = {
        "GIT_ORIGIN_PROTECTED",
        "COMMITTED_RECONSTRUCTABLE",
        "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE",
        "OWNER_RECREATABLE",
        "CHECKSUM_ONLY_NO_COPY_REQUIRED",
        "IRREPLACEABLE_BACKUP_REQUIRED",
        "EXCLUDED_CREDENTIAL_MATERIAL",
        "UNRESOLVED",
    }
    if not isinstance(classification, Mapping) or set(classification) != expected_classes:
        raise Phase1EFPretransferError("BACKUP_CLASSIFICATION_SCHEMA_INVALID")
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in classification.values()
    ) or classification["UNRESOLVED"] != 0:
        raise Phase1EFPretransferError("BACKUP_CLASSIFICATION_UNRESOLVED")


def build(
    *, governing_commit: str, attempt_id: str, checkout_root: Path,
    capacity_receipt: Path, capacity_aggregate: Path, backup_manifest: Path,
    backup_aggregate: Path, restore_receipt: Path, future_command: Path,
    write_binding_gates: Mapping[str, bool],
) -> Mapping[str, Any]:
    if not COMMIT_RE.fullmatch(governing_commit) or not ATTEMPT_RE.fullmatch(attempt_id):
        raise Phase1EFPretransferError("PRETRANSFER_IDENTITY_INVALID")
    checkout = _validate_checkout(checkout_root, governing_commit)
    paths = {
        "post_reallocation_capacity_receipt": capacity_receipt,
        "post_reallocation_capacity_aggregate": capacity_aggregate,
        "backup_manifest": backup_manifest,
        "backup_recovery_aggregate": backup_aggregate,
        "restore_test_receipt": restore_receipt,
        "future_first_batch_command": future_command,
    }
    if set(paths) != AUTHORITY_ROLES:
        raise Phase1EFPretransferError("AUTHORITY_ROLE_SET_INVALID")
    bindings = {role: _binding(path) for role, path in paths.items()}
    capacity_receipt_value = _load_private_json(capacity_receipt)
    capacity_value = _load_private_json(capacity_aggregate)
    _validate_capacity(
        capacity_value,
        receipt=capacity_receipt_value,
        receipt_binding=bindings["post_reallocation_capacity_receipt"],
        attempt_id=attempt_id,
        governing_commit=governing_commit,
    )
    backup_manifest_value = _load_private_json(backup_manifest)
    backup_value = _load_private_json(backup_aggregate)
    restore_value = _load_private_json(restore_receipt)
    _validate_backup(
        backup_value,
        manifest=backup_manifest_value,
        restore=restore_value,
        attempt_id=attempt_id,
        governing_commit=governing_commit,
        manifest_binding=bindings["backup_manifest"],
        restore_binding=bindings["restore_test_receipt"],
    )
    computed_write_bindings = _validate_write_bindings(checkout)
    if (
        set(write_binding_gates) != WRITE_BINDING_KEYS
        or any(value is not True for value in write_binding_gates.values())
        or dict(write_binding_gates) != dict(computed_write_bindings)
    ):
        raise Phase1EFPretransferError("WRITE_BINDING_GATE_NOT_PASS")
    value = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "status": STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": dict(sorted(bindings.items())),
        "capacity_gates": {key: True for key in sorted(CAPACITY_GATE_KEYS)},
        "backup_recovery_gates": {
            key: True for key in sorted(BACKUP_GATE_KEYS)
        },
        "write_binding_gates": dict(sorted(computed_write_bindings.items())),
        "execution_attestations": dict(EXECUTION_ATTESTATIONS),
        "authorization_scopes_granted": 0,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }
    validate_aggregate_output(value)
    return value


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != TOP_LEVEL_KEYS:
        raise Phase1EFPretransferError("PRETRANSFER_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != ARTIFACT_TYPE
        or value.get("status") != STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or value.get("authorization_scopes_granted") != 0
        or value.get("full_c3_status")
        != "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION"
    ):
        raise Phase1EFPretransferError("PRETRANSFER_AUTHORITY_FIELDS_INVALID")
    authority = value.get("authority")
    if not isinstance(authority, Mapping) or set(authority) != AUTHORITY_ROLES:
        raise Phase1EFPretransferError("PRETRANSFER_AUTHORITY_ROLES_INVALID")
    for binding in authority.values():
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"size_bytes", "sha256"}
            or not isinstance(binding.get("size_bytes"), int)
            or isinstance(binding.get("size_bytes"), bool)
            or binding["size_bytes"] <= 0
            or not SHA256_RE.fullmatch(str(binding.get("sha256", "")))
        ):
            raise Phase1EFPretransferError("PRETRANSFER_BINDING_INVALID")
    for field, keys in (
        ("capacity_gates", CAPACITY_GATE_KEYS),
        ("backup_recovery_gates", BACKUP_GATE_KEYS),
        ("write_binding_gates", WRITE_BINDING_KEYS),
    ):
        gates = value.get(field)
        if (
            not isinstance(gates, Mapping)
            or set(gates) != keys
            or any(item is not True for item in gates.values())
        ):
            raise Phase1EFPretransferError("PRETRANSFER_GATE_NOT_PASS")
    if value.get("execution_attestations") != EXECUTION_ATTESTATIONS:
        raise Phase1EFPretransferError("PRETRANSFER_EXECUTION_BOUNDARY_INVALID")


def launch_ready(value: Mapping[str, Any]) -> bool:
    try:
        validate_aggregate_output(value)
    except Phase1EFPretransferError:
        return False
    return True


def write_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if path.exists() or path.is_symlink():
        raise Phase1EFPretransferError("PRETRANSFER_OUTPUT_COLLISION")
    _require_no_symlink_ancestors(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Phase1EFPretransferError("PRETRANSFER_OUTPUT_PARENT_INVALID")
    metadata = path.parent.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise Phase1EFPretransferError("PRETRANSFER_OUTPUT_PARENT_NOT_PRIVATE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--capacity-receipt", type=Path, required=True)
    parser.add_argument("--capacity-aggregate", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--backup-aggregate", type=Path, required=True)
    parser.add_argument("--restore-receipt", type=Path, required=True)
    parser.add_argument("--future-command", type=Path, required=True)
    parser.add_argument(
        "--all-substantial-writes-bound-to-research", choices=("YES",), required=True
    )
    parser.add_argument("--backed-control-writes-bounded", choices=("YES",), required=True)
    parser.add_argument("--raw-dicom-deletion-prohibited", choices=("YES",), required=True)
    parser.add_argument(
        "--cache-retirement-requires-separate-authorization",
        choices=("YES",), required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        value = build(
            governing_commit=args.governing_commit,
            attempt_id=args.attempt_id,
            checkout_root=args.checkout_root,
            capacity_receipt=args.capacity_receipt,
            capacity_aggregate=args.capacity_aggregate,
            backup_manifest=args.backup_manifest,
            backup_aggregate=args.backup_aggregate,
            restore_receipt=args.restore_receipt,
            future_command=args.future_command,
            write_binding_gates={key: True for key in WRITE_BINDING_KEYS},
        )
        write_no_clobber(args.output, value)
    except (Phase1EFPretransferError, OSError):
        print("PHASE1EF_PRETRANSFER_LOCK=FAILED")
        return 78
    print("PHASE1EF_PRETRANSFER_LOCK=PASS")
    print("AUTHORIZATION_SCOPES_GRANTED=0")
    print("CLOUD_REQUESTS=0")
    print("SCHEDULER_JOBS_SUBMITTED=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
