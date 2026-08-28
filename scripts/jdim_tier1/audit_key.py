"""Secure creation and same-run reuse of the restricted audit-ID key."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


KEY_BYTES = 32
KEY_SCHEMA = "JDIM_AUDIT_KEY_V1"


class AuditKeySafetyError(RuntimeError):
    """Raised when secure audit-key invariants are not satisfied."""


@dataclass(frozen=True)
class AuditKeyResult:
    status: str
    created: bool


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _exclusive_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, mode)
        created = True
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("exclusive audit-key write did not make progress")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
    os.chmod(path, mode)


def _validate_existing(key_path: Path, marker_path: Path, run_id: str) -> None:
    if not key_path.is_file() or key_path.is_symlink():
        raise AuditKeySafetyError("existing audit key is not a regular file")
    if not marker_path.is_file() or marker_path.is_symlink():
        raise AuditKeySafetyError("same-run audit-key marker is missing or unsafe")
    if key_path.stat().st_size != KEY_BYTES:
        raise AuditKeySafetyError("existing audit key has an invalid length")
    if stat.S_IMODE(key_path.stat().st_mode) != 0o600:
        raise AuditKeySafetyError("existing audit key permissions are not 0600")
    if stat.S_IMODE(marker_path.stat().st_mode) != 0o600:
        raise AuditKeySafetyError("audit-key marker permissions are not 0600")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditKeySafetyError("same-run audit-key marker is invalid") from exc
    if marker != {
        "schema_version": KEY_SCHEMA,
        "run_id": run_id,
        "key_file_name": key_path.name,
    }:
        raise AuditKeySafetyError("existing audit key belongs to a different immutable run")


def create_or_resume_audit_key(
    output_root: Path,
    key_file: Path,
    run_id: str,
    *,
    resume_existing: bool = False,
    secret_factory: Callable[[int], bytes] = os.urandom,
) -> AuditKeyResult:
    """Create a restricted key once, or validate explicit same-run reuse."""

    if not run_id.strip():
        raise AuditKeySafetyError("immutable audit run ID must be nonempty")
    raw_output_root = output_root.expanduser()
    raw_key_path = key_file.expanduser()
    if not raw_output_root.is_absolute() or not raw_key_path.is_absolute():
        raise AuditKeySafetyError("audit output and key paths must be absolute")
    output_root = _resolved(raw_output_root)
    restricted_root = _resolved(raw_output_root / "restricted")
    key_path = _resolved(raw_key_path)
    if not _under(restricted_root, output_root):
        raise AuditKeySafetyError("restricted directory resolves outside the approved output root")
    if key_path == restricted_root or not _under(key_path.parent, restricted_root):
        raise AuditKeySafetyError("audit key must remain under the approved restricted output root")
    if "aggregate_safe" in key_path.parts:
        raise AuditKeySafetyError("audit key cannot be placed in aggregate-safe output")
    marker_path = key_path.with_name(f"{key_path.name}.run.json")

    key_exists = key_path.exists() or key_path.is_symlink()
    marker_exists = marker_path.exists() or marker_path.is_symlink()
    if key_exists or marker_exists:
        if not resume_existing:
            raise AuditKeySafetyError("existing audit key will not be overwritten")
        if not (key_exists and marker_exists):
            raise AuditKeySafetyError("incomplete existing audit-key state cannot be resumed")
        _validate_existing(key_path, marker_path, run_id)
        return AuditKeyResult(status="AUDIT_KEY_REUSED", created=False)

    restricted_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(restricted_root, 0o700)
    relative_parent = key_path.parent.relative_to(restricted_root)
    current = restricted_root
    for component in relative_parent.parts:
        current = current / component
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise AuditKeySafetyError("audit-key parent contains an unsafe path component")
        os.chmod(current, 0o700)
    if not _under(key_path.parent.resolve(), restricted_root.resolve()):
        raise AuditKeySafetyError("audit-key parent resolves outside the restricted root")

    secret = secret_factory(KEY_BYTES)
    if not isinstance(secret, bytes) or len(secret) != KEY_BYTES:
        raise AuditKeySafetyError("audit-key generator returned an invalid secret")
    marker = json.dumps(
        {
            "schema_version": KEY_SCHEMA,
            "run_id": run_id,
            "key_file_name": key_path.name,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    try:
        _exclusive_write(key_path, secret)
        _exclusive_write(marker_path, marker)
    except Exception:
        key_path.unlink(missing_ok=True)
        marker_path.unlink(missing_ok=True)
        raise
    return AuditKeyResult(status="AUDIT_KEY_CREATED", created=True)
