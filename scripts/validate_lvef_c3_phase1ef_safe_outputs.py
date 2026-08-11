#!/usr/bin/env python3
"""Validate the five live Phase 1E-F aggregate bytes before any GO marker.

This is an offline boundary gate, not an export or release action.  It applies
the committed safe-export profiles to the exact owner-private aggregate files
and writes only a bounded restricted receipt.  No candidate bytes or paths are
printed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import lvef_multitask_analysis_modes as modes


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "lvef_c3_phase1ef_live_safe_output_gate_v1"
STATUS = "PASS_LIVE_AGGREGATE_BYTES_SAFE_PROFILE_VALIDATED"
ATTEMPT_RE = re.compile(
    r"^lvef_multitask_phase1ef_post_reallocation_lock_attempt_[0-9]{3}$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ROLE_SPECS = {
    "capacity": (
        "lvef_c3_post_reallocation_capacity.summary.json",
        "phase1ef_post_reallocation_capacity_json",
    ),
    "backup": (
        "lvef_c3_backup_recovery.summary.json",
        "phase1ef_backup_recovery_json",
    ),
    "pretransfer": (
        "lvef_c3_phase1ef_pretransfer_lock.summary.json",
        "phase1ef_pretransfer_lock_json",
    ),
    "final": (
        "lvef_c3_phase1ef_final_pretransfer_lock.summary.json",
        "phase1ef_final_pretransfer_lock_json",
    ),
    "terminal": (
        "lvef_c3_phase1ef_terminal_recovery_seal.summary.json",
        "phase1ef_terminal_recovery_seal_json",
    ),
}


class Phase1EFSafeOutputError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_symlink_ancestors(path: Path, *, leaf_may_be_absent: bool = False) -> None:
    if not path.is_absolute():
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_PATH_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    parts = path.absolute().parts[1:]
    for index, part in enumerate(parts):
        cursor /= part
        try:
            item = os.lstat(cursor)
        except FileNotFoundError:
            if leaf_may_be_absent and index == len(parts) - 1:
                return
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_PATH_COMPONENT_MISSING") from None
        if stat.S_ISLNK(item.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_PATH_SYMLINK")


def _read_private(path: Path, code: str, *, maximum: int = 1_048_576) -> bytes:
    _no_symlink_ancestors(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Phase1EFSafeOutputError(f"{code}_OPEN_FAILED") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise Phase1EFSafeOutputError(f"{code}_NOT_BOUNDED_PRIVATE_REGULAR")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(131_072, maximum + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum:
                raise Phase1EFSafeOutputError(f"{code}_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        total != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise Phase1EFSafeOutputError(f"{code}_CHANGED_DURING_READ")
    return b"".join(chunks)


def _parse_artifacts(raw: Sequence[str]) -> Mapping[str, Path]:
    observed: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_ARTIFACT_ARGUMENT_INVALID")
        role, raw_path = item.split("=", 1)
        if role in observed or role not in ROLE_SPECS or not raw_path:
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_ARTIFACT_ROLE_INVALID")
        path = Path(raw_path)
        if not path.is_absolute() or path.name != ROLE_SPECS[role][0]:
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_ARTIFACT_PATH_INVALID")
        observed[role] = path
    if set(observed) != set(ROLE_SPECS):
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_ARTIFACT_SET_NOT_EXACT")
    if len({path.resolve(strict=True) for path in observed.values()}) != len(observed):
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_ARTIFACT_PATH_DUPLICATE")
    return observed


def execute(
    *, attempt_id: str, governing_commit: str, policy_path: Path,
    artifacts_raw: Sequence[str], receipt_path: Path,
) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(attempt_id) or not COMMIT_RE.fullmatch(governing_commit):
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_IDENTITY_INVALID")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_RECEIPT_COLLISION")
    policy, policy_sha = modes.load_policy(policy_path)
    artifacts = _parse_artifacts(artifacts_raw)
    records = {}
    for role, path in sorted(artifacts.items()):
        payload = _read_private(path, f"SAFE_OUTPUT_{role.upper()}")
        filename, profile = ROLE_SPECS[role]
        result = modes.validate_candidate_bytes(
            payload, filename=filename, profile_name=profile, policy=policy,
        )
        if result.get("status") != "PASS":
            raise Phase1EFSafeOutputError("SAFE_OUTPUT_PROFILE_NOT_PASS")
        records[role] = {
            "filename": filename,
            "profile": profile,
            "size_bytes": len(payload),
            "sha256": _sha(payload),
            "safe_profile_status": "PASS",
        }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "status": STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "safe_export_policy_sha256": policy_sha,
        "artifacts": records,
        "validated_artifact_count": len(records),
        "closed_schema_profiles_applied": True,
        "candidate_bytes_unchanged": True,
        "restricted_outputs_exported": False,
        "release_authority_granted": False,
        "authorization_scopes_granted": 0,
        "cloud_requests": 0,
        "object_listing_repeated": False,
        "storage_inventory_repeated": False,
        "scheduler_jobs_submitted": 0,
        "dicom_bodies_downloaded": 0,
    }
    payload = _canonical(receipt)
    _no_symlink_ancestors(receipt_path, leaf_may_be_absent=True)
    if not receipt_path.parent.is_dir() or receipt_path.parent.is_symlink():
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_RECEIPT_PARENT_INVALID")
    parent = receipt_path.parent.stat(follow_symlinks=False)
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) not in {0o700, 0o2700}:
        raise Phase1EFSafeOutputError("SAFE_OUTPUT_RECEIPT_PARENT_NOT_PRIVATE")
    descriptor = os.open(
        receipt_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return receipt


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--attempt-id", required=True)
    value.add_argument("--governing-commit", required=True)
    value.add_argument("--policy", type=Path, required=True)
    value.add_argument("--artifact", action="append", default=[])
    value.add_argument("--receipt", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        receipt = execute(
            attempt_id=args.attempt_id,
            governing_commit=args.governing_commit,
            policy_path=args.policy,
            artifacts_raw=args.artifact,
            receipt_path=args.receipt,
        )
    except (Phase1EFSafeOutputError, modes.SafetyPolicyError, OSError) as exc:
        code = exc.code if isinstance(exc, Phase1EFSafeOutputError) else "SAFE_OUTPUT_GATE_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 79
    print(json.dumps({
        "status": receipt["status"],
        "validated_artifact_count": receipt["validated_artifact_count"],
        "restricted_outputs_exported": False,
        "release_authority_granted": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
