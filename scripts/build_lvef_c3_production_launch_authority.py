#!/usr/bin/env python3
"""Build or validate the final non-circular C3 launch-authority envelope.

The base execution environment is frozen before the production authority
packet, so neither artifact can safely contain the other's digest.  This
owner-private envelope is created last and binds both artifacts plus the
Phase 1E-F capacity/backup pretransfer authority.  It performs no cloud,
scheduler, DICOM, model, or deletion operation.
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
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_lvef_c3_production_authority_packet as packet
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "lvef_c3_production_launch_authority_v1"
STATUS = "PASS_PRODUCTION_LAUNCH_AUTHORITY_OWNER_AUTHORIZATION_STILL_REQUIRED"
ATTEMPT_RE = re.compile(r"^lvef_c3_phase1ee_[a-z0-9][a-z0-9_-]{5,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]+)?(?:Z|[+]00:00)$"
)
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "governing_commit",
        "created_at_utc",
        "execution_environment",
        "post_expansion_capacity_summary",
        "production_authority_packet",
        "all_pretransfer_capacity_gates_passed",
        "owner_stage_authorization_granted",
        "scheduler_submission_performed",
        "cloud_requests_performed",
        "object_bodies_downloaded",
    }
)
FILE_BINDING_KEYS = frozenset({"path", "size_bytes", "sha256"})
TERMINAL_FINAL_LOCK_FILENAME = (
    "lvef_c3_phase1ef_final_pretransfer_lock.summary.json"
)
TERMINAL_RECOVERY_SEAL_FILENAME = (
    "lvef_c3_phase1ef_terminal_recovery_seal.summary.json"
)
TERMINAL_RECOVERY_BACKED_PREFIX = Path("/restricted/project/mimicecho/audits")


class LaunchAuthorityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LaunchAuthorityError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _require_private_regular(path: Path, code: str) -> None:
    try:
        packet.require_no_symlink_ancestors(path)
    except packet.AuthorityPacketError as exc:
        raise LaunchAuthorityError(f"{code}_SYMLINK_ANCESTOR") from exc
    if path.is_symlink() or not path.is_file():
        raise LaunchAuthorityError(f"{code}_NOT_REGULAR")
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise LaunchAuthorityError(f"{code}_NOT_OWNER_PRIVATE")


def _load_json(path: Path, code: str) -> Mapping[str, Any]:
    _require_private_regular(path, code)
    try:
        value = json.loads(
            packet.read_regular_nofollow(path).decode("utf-8"),
            object_pairs_hook=_pairs,
        )
    except LaunchAuthorityError:
        raise
    except Exception as exc:
        raise LaunchAuthorityError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, Mapping):
        raise LaunchAuthorityError(f"{code}_NOT_MAPPING")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(packet.read_regular_nofollow(path)).hexdigest()


def _binding(path: Path) -> Mapping[str, Any]:
    payload = packet.read_regular_nofollow(path)
    return {
        "path": str(path.resolve(strict=True)),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _validate_capacity(value: Mapping[str, Any], *, governing_commit: str) -> None:
    try:
        pretransfer.validate_aggregate_output(value)
    except pretransfer.Phase1EFPretransferError as exc:
        raise LaunchAuthorityError("CAPACITY_SUMMARY_NOT_AUTHORITATIVE") from exc
    if (
        value.get("governing_commit") != governing_commit
        or not pretransfer.launch_ready(value)
    ):
        raise LaunchAuthorityError("PRETRANSFER_CAPACITY_GATE_NOT_PASS")
    if (
        value.get("authorization_scopes_granted") != 0
        or value.get("execution_attestations") != pretransfer.EXECUTION_ATTESTATIONS
    ):
        raise LaunchAuthorityError("CAPACITY_SUMMARY_EXECUTION_BOUNDARY_INVALID")


def _validate_packet(
    value: Mapping[str, Any], *, attempt_id: str, governing_commit: str,
    environment_sha256: str, capacity_sha256: str,
) -> None:
    try:
        packet.validate_packet(value)
    except packet.AuthorityPacketError as exc:
        raise LaunchAuthorityError("PRODUCTION_AUTHORITY_PACKET_INVALID") from exc
    authority = value.get("authority", {})
    if (
        value.get("attempt_id") != attempt_id
        or value.get("governing_commit") != governing_commit
        or value.get("full_c3_status")
        != "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION"
        or authority.get("execution_environment", {}).get("sha256")
        != environment_sha256
        or authority.get("post_expansion_capacity_summary", {}).get("sha256")
        != capacity_sha256
        or any(value.get("authorization_scopes", {}).values())
    ):
        raise LaunchAuthorityError("PRODUCTION_AUTHORITY_PACKET_NOT_LAUNCH_READY")


def build(
    *, attempt_id: str, governing_commit: str, execution_environment: Path,
    capacity_summary: Path, authority_packet: Path,
) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(attempt_id) or not COMMIT_RE.fullmatch(governing_commit):
        raise LaunchAuthorityError("LAUNCH_IDENTITY_INVALID")
    environment = packet._parse_execution_environment(execution_environment)
    if (
        environment.get("LVEF_C3_ATTEMPT_ID") != attempt_id
        or environment.get("LVEF_C3_GOVERNING_COMMIT") != governing_commit
    ):
        raise LaunchAuthorityError("EXECUTION_ENVIRONMENT_IDENTITY_MISMATCH")
    capacity_value = _load_json(capacity_summary, "CAPACITY_SUMMARY")
    _validate_capacity(capacity_value, governing_commit=governing_commit)
    packet_value = _load_json(authority_packet, "PRODUCTION_AUTHORITY_PACKET")
    environment_sha = sha256_file(execution_environment)
    capacity_sha = sha256_file(capacity_summary)
    _validate_packet(
        packet_value,
        attempt_id=attempt_id,
        governing_commit=governing_commit,
        environment_sha256=environment_sha,
        capacity_sha256=capacity_sha,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "status": STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_environment": _binding(execution_environment),
        "post_expansion_capacity_summary": _binding(capacity_summary),
        "production_authority_packet": _binding(authority_packet),
        "all_pretransfer_capacity_gates_passed": True,
        "owner_stage_authorization_granted": False,
        "scheduler_submission_performed": False,
        "cloud_requests_performed": 0,
        "object_bodies_downloaded": 0,
    }


def validate(
    value: Mapping[str, Any], *, envelope_path: Path, attempt_id: str,
    governing_commit: str, execution_environment: Path,
    require_terminal_lock: bool = True,
) -> Mapping[str, Any]:
    if set(value) != TOP_LEVEL_KEYS:
        raise LaunchAuthorityError("LAUNCH_AUTHORITY_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != ARTIFACT_TYPE
        or value.get("status") != STATUS
        or value.get("attempt_id") != attempt_id
        or value.get("governing_commit") != governing_commit
        or not UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or value.get("all_pretransfer_capacity_gates_passed") is not True
        or value.get("owner_stage_authorization_granted") is not False
        or value.get("scheduler_submission_performed") is not False
        or value.get("cloud_requests_performed") != 0
        or value.get("object_bodies_downloaded") != 0
    ):
        raise LaunchAuthorityError("LAUNCH_AUTHORITY_SEMANTICS_INVALID")
    bindings: dict[str, Path] = {}
    for key in (
        "execution_environment",
        "post_expansion_capacity_summary",
        "production_authority_packet",
    ):
        binding = value.get(key)
        if not isinstance(binding, Mapping) or set(binding) != FILE_BINDING_KEYS:
            raise LaunchAuthorityError("LAUNCH_FILE_BINDING_SCHEMA_INVALID")
        path = Path(str(binding.get("path", "")))
        _require_private_regular(path, f"LAUNCH_{key.upper()}")
        if (
            not isinstance(binding.get("size_bytes"), int)
            or isinstance(binding.get("size_bytes"), bool)
            or binding.get("size_bytes") <= 0
            or not SHA256_RE.fullmatch(str(binding.get("sha256", "")))
            or path.stat(follow_symlinks=False).st_size != binding.get("size_bytes")
            or sha256_file(path) != binding.get("sha256")
        ):
            raise LaunchAuthorityError("LAUNCH_FILE_BINDING_MISMATCH")
        bindings[key] = path
    if bindings["execution_environment"].resolve(strict=True) != execution_environment.resolve(strict=True):
        raise LaunchAuthorityError("LAUNCH_EXECUTION_ENVIRONMENT_PATH_MISMATCH")
    environment = packet._parse_execution_environment(execution_environment)
    if (
        environment.get("LVEF_C3_ATTEMPT_ID") != attempt_id
        or environment.get("LVEF_C3_GOVERNING_COMMIT") != governing_commit
    ):
        raise LaunchAuthorityError("EXECUTION_ENVIRONMENT_IDENTITY_MISMATCH")
    capacity_value = _load_json(
        bindings["post_expansion_capacity_summary"], "CAPACITY_SUMMARY"
    )
    _validate_capacity(capacity_value, governing_commit=governing_commit)
    packet_value = _load_json(
        bindings["production_authority_packet"], "PRODUCTION_AUTHORITY_PACKET"
    )
    _validate_packet(
        packet_value,
        attempt_id=attempt_id,
        governing_commit=governing_commit,
        environment_sha256=str(value["execution_environment"]["sha256"]),
        capacity_sha256=str(value["post_expansion_capacity_summary"]["sha256"]),
    )
    _require_private_regular(envelope_path, "LAUNCH_AUTHORITY")
    if require_terminal_lock:
        terminal_path = (
            bindings["post_expansion_capacity_summary"].parent
            / TERMINAL_FINAL_LOCK_FILENAME
        )
        try:
            finalizer = importlib.import_module(
                "finalize_lvef_c3_phase1ef_pretransfer_lock"
            )
            finalizer.validate_terminal_for_launch(
                terminal_path,
                launch_envelope_path=envelope_path,
                pretransfer_path=bindings["post_expansion_capacity_summary"],
                packet_path=bindings["production_authority_packet"],
                execution_environment=bindings["execution_environment"],
                production_attempt_id=attempt_id,
                governing_commit=governing_commit,
            )
            terminal_recovery = importlib.import_module(
                "build_lvef_c3_terminal_recovery_seal"
            )
            terminal_recovery.validate_terminal_for_launch(
                TERMINAL_RECOVERY_BACKED_PREFIX
                / str(capacity_value["attempt_id"])
                / "terminal_recovery_seal"
                / TERMINAL_RECOVERY_SEAL_FILENAME,
                launch_envelope_path=envelope_path,
                final_lock_path=terminal_path,
                pretransfer_path=bindings["post_expansion_capacity_summary"],
                packet_path=bindings["production_authority_packet"],
                execution_environment=bindings["execution_environment"],
                production_attempt_id=attempt_id,
                governing_commit=governing_commit,
            )
        except LaunchAuthorityError:
            raise
        except Exception as exc:
            raise LaunchAuthorityError("TERMINAL_FINAL_LOCK_NOT_AUTHORITATIVE") from exc
    return value


def write_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise LaunchAuthorityError("LAUNCH_OUTPUT_COLLISION")
    try:
        packet.require_no_symlink_ancestors(path)
    except packet.AuthorityPacketError as exc:
        raise LaunchAuthorityError("LAUNCH_OUTPUT_SYMLINK_ANCESTOR") from exc
    if not path.parent.is_dir():
        raise LaunchAuthorityError("LAUNCH_OUTPUT_PARENT_INVALID")
    parent_metadata = path.parent.stat(follow_symlinks=False)
    if parent_metadata.st_uid != os.getuid() or stat.S_IMODE(parent_metadata.st_mode) & 0o077:
        raise LaunchAuthorityError("LAUNCH_OUTPUT_PARENT_NOT_OWNER_PRIVATE")
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "validate"):
        command = subparsers.add_parser(name)
        command.add_argument("--attempt-id", required=True)
        command.add_argument("--governing-commit", required=True)
        command.add_argument("--execution-environment", type=Path, required=True)
        command.add_argument("--launch-authority", type=Path, required=True)
        if name == "build":
            command.add_argument("--capacity-summary", type=Path, required=True)
            command.add_argument("--authority-packet", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "build":
            value = build(
                attempt_id=args.attempt_id,
                governing_commit=args.governing_commit,
                execution_environment=args.execution_environment,
                capacity_summary=args.capacity_summary,
                authority_packet=args.authority_packet,
            )
            write_no_clobber(args.launch_authority, value)
        else:
            value = _load_json(args.launch_authority, "LAUNCH_AUTHORITY")
            validate(
                value,
                envelope_path=args.launch_authority,
                attempt_id=args.attempt_id,
                governing_commit=args.governing_commit,
                execution_environment=args.execution_environment,
            )
    except (LaunchAuthorityError, packet.AuthorityPacketError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "LAUNCH_AUTHORITY_VALIDATION_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 78
    except Exception:
        print(json.dumps({"status": "FAIL", "error_code": "LAUNCH_AUTHORITY_UNEXPECTED_SANITIZED"}, sort_keys=True))
        return 78
    print(
        json.dumps(
            {
                "status": STATUS,
                "owner_stage_authorization_granted": False,
                "scheduler_submission_performed": False,
                "cloud_requests_performed": 0,
                "object_bodies_downloaded": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
