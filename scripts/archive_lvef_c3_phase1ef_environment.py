#!/usr/bin/env python3
"""Archive one superseded Phase 1E-F private environment without disclosure."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


SCHEMA_NAME = "lvef_c3_phase1ef_environment_archive_receipt"
SCHEMA_VERSION = 1
ARCHIVE_FILENAME = "phase1ef_attempt004_authority.environment.archived"
RECEIPT_FILENAME = "phase1ef_attempt004_environment_archive.restricted.json"
SOURCE_FILENAME = "phase1ef_attempt004_authority.env"
REASON = "canonical preexecution authority manifest introduced"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
RECEIPT_KEYS = frozenset(
    {
        "schema_name",
        "schema_version",
        "archived_role",
        "original_filename",
        "archive_filename",
        "governing_commit",
        "archived_at_utc",
        "reason",
        "source_size_bytes",
        "source_sha256",
        "archive_size_bytes",
        "archive_sha256",
        "source_mode",
        "archive_mode",
        "source_archive_distinct_inode",
        "byte_identity_verified",
        "credential_contents_inspected",
        "environment_contents_printed",
    }
)


class ArchiveError(ValueError):
    pass


def _fail(code: str) -> None:
    raise ArchiveError(code)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    result: MutableMapping[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _canonical_existing(path: Path, *, directory: bool) -> Path:
    if not path.is_absolute():
        _fail("PATH_NOT_ABSOLUTE")
    try:
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError:
        _fail("PATH_MISSING")
    if path != resolved:
        _fail("PATH_NOT_CANONICAL")
    if stat.S_ISLNK(metadata.st_mode):
        _fail("SYMLINK_FORBIDDEN")
    if directory and not stat.S_ISDIR(metadata.st_mode):
        _fail("DIRECTORY_REQUIRED")
    if not directory and not stat.S_ISREG(metadata.st_mode):
        _fail("REGULAR_FILE_REQUIRED")
    return resolved


def _history_parent_policy(metadata: os.stat_result) -> bool:
    """Accept owner-controlled parents that are not group/other writable."""
    return metadata.st_uid == os.geteuid() and not (
        stat.S_IMODE(metadata.st_mode) & 0o022
    )


def _read_private_file(path: Path) -> tuple[bytes, os.stat_result]:
    _canonical_existing(path, directory=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("SOURCE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("SOURCE_NOT_REGULAR")
        if metadata.st_uid != os.geteuid():
            _fail("SOURCE_OWNER_INVALID")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            _fail("SOURCE_MODE_INVALID")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size:
            _fail("SOURCE_SIZE_CHANGED")
        final = os.lstat(path)
        if (
            final.st_dev != metadata.st_dev
            or final.st_ino != metadata.st_ino
            or final.st_size != metadata.st_size
            or final.st_mtime_ns != metadata.st_mtime_ns
            or final.st_uid != metadata.st_uid
            or stat.S_IMODE(final.st_mode) != stat.S_IMODE(metadata.st_mode)
        ):
            _fail("SOURCE_CHANGED_DURING_READ")
        return payload, metadata
    finally:
        os.close(descriptor)


def _write_no_clobber(path: Path, payload: bytes) -> None:
    if os.path.lexists(path):
        _fail("ARCHIVE_COLLISION")
    temporary = path.parent / (
        f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    )
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _fail("ARCHIVE_COLLISION")
        os.unlink(temporary)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def validate_receipt(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != RECEIPT_KEYS:
        _fail("RECEIPT_SCHEMA_NOT_CLOSED")
    if value["schema_name"] != SCHEMA_NAME or value["schema_version"] != 1:
        _fail("RECEIPT_SCHEMA_INVALID")
    if value["archived_role"] != "superseded_attempt_004_environment":
        _fail("RECEIPT_ROLE_INVALID")
    if value["original_filename"] != SOURCE_FILENAME:
        _fail("RECEIPT_SOURCE_NAME_INVALID")
    if value["archive_filename"] != ARCHIVE_FILENAME:
        _fail("RECEIPT_ARCHIVE_NAME_INVALID")
    if value["reason"] != REASON:
        _fail("RECEIPT_REASON_INVALID")
    if not isinstance(value["governing_commit"], str) or not COMMIT_RE.fullmatch(
        value["governing_commit"]
    ):
        _fail("RECEIPT_COMMIT_INVALID")
    timestamp = value["archived_at_utc"]
    if not isinstance(timestamp, str) or UTC_RE.fullmatch(timestamp) is None:
        _fail("RECEIPT_TIMESTAMP_INVALID")
    try:
        parsed_timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("RECEIPT_TIMESTAMP_INVALID")
    if parsed_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") != timestamp:
        _fail("RECEIPT_TIMESTAMP_INVALID")
    if value["source_mode"] != "0600" or value["archive_mode"] != "0600":
        _fail("RECEIPT_MODE_INVALID")
    for key in ("source_size_bytes", "archive_size_bytes"):
        if type(value[key]) is not int or value[key] <= 0:
            _fail("RECEIPT_SIZE_INVALID")
    for key in ("source_sha256", "archive_sha256"):
        if not isinstance(value[key], str) or not SHA_RE.fullmatch(value[key]):
            _fail("RECEIPT_SHA_INVALID")
    if value["source_size_bytes"] != value["archive_size_bytes"]:
        _fail("RECEIPT_SIZE_IDENTITY_FAILED")
    if value["source_sha256"] != value["archive_sha256"]:
        _fail("RECEIPT_HASH_IDENTITY_FAILED")
    for key, expected in (
        ("source_archive_distinct_inode", True),
        ("byte_identity_verified", True),
        ("credential_contents_inspected", False),
        ("environment_contents_printed", False),
    ):
        if value[key] is not expected:
            _fail("RECEIPT_ATTESTATION_INVALID")
    return value


def archive_environment(
    *, source: Path, history_root: Path, governing_commit: str
) -> tuple[Path, Path]:
    if not COMMIT_RE.fullmatch(governing_commit):
        _fail("GOVERNING_COMMIT_INVALID")
    if source.name != SOURCE_FILENAME:
        _fail("SOURCE_NAME_INVALID")
    payload, source_metadata = _read_private_file(source)
    if not history_root.is_absolute() or history_root.name in {"", ".", ".."}:
        _fail("HISTORY_ROOT_INVALID")
    parent = _canonical_existing(history_root.parent, directory=True)
    parent_metadata = os.lstat(parent)
    # SCC project audit roots are owner-controlled but intentionally
    # traversable (and commonly setgid) for the project group.  The archive
    # itself is created as a private 0700 child, so the parent must be owned by
    # the invoking user and must not be writable by group or other; requiring
    # an exact 0700/2700 parent would reject the canonical 2755 audit root.
    if not _history_parent_policy(parent_metadata):
        _fail("HISTORY_PARENT_POLICY_FAILED")
    if os.path.lexists(history_root):
        _fail("HISTORY_ROOT_COLLISION")
    os.mkdir(history_root, 0o700)
    archive = history_root / ARCHIVE_FILENAME
    receipt = history_root / RECEIPT_FILENAME
    _write_no_clobber(archive, payload)
    archived_payload, archive_metadata = _read_private_file(archive)
    digest = hashlib.sha256(payload).hexdigest()
    archived_digest = hashlib.sha256(archived_payload).hexdigest()
    value = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "archived_role": "superseded_attempt_004_environment",
        "original_filename": source.name,
        "archive_filename": ARCHIVE_FILENAME,
        "governing_commit": governing_commit,
        "archived_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "reason": REASON,
        "source_size_bytes": len(payload),
        "source_sha256": digest,
        "archive_size_bytes": len(archived_payload),
        "archive_sha256": archived_digest,
        "source_mode": "0600",
        "archive_mode": "0600",
        "source_archive_distinct_inode": (
            (source_metadata.st_dev, source_metadata.st_ino)
            != (archive_metadata.st_dev, archive_metadata.st_ino)
        ),
        "byte_identity_verified": payload == archived_payload,
        "credential_contents_inspected": False,
        "environment_contents_printed": False,
    }
    validate_receipt(value)
    receipt_payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    _write_no_clobber(receipt, receipt_payload)
    parsed = json.loads(
        _read_private_file(receipt)[0].decode("utf-8"), object_pairs_hook=_strict_pairs
    )
    validate_receipt(parsed)
    return archive, receipt


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        print("PHASE1EF_ENVIRONMENT_ARCHIVE=FAILED", file=sys.stderr)
        print("PHASE1EF_ENVIRONMENT_ARCHIVE_FAILURE_CODE=ARGUMENT_INVALID", file=sys.stderr)
        raise SystemExit(64)


def main(argv: Sequence[str] | None = None) -> int:
    parser = SafeArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--governing-commit", required=True)
    args = parser.parse_args(argv)
    try:
        archive_environment(
            source=args.source,
            history_root=args.history_root,
            governing_commit=args.governing_commit,
        )
    except ArchiveError as exc:
        print("PHASE1EF_ENVIRONMENT_ARCHIVE=FAILED", file=sys.stderr)
        print(f"PHASE1EF_ENVIRONMENT_ARCHIVE_FAILURE_CODE={exc}", file=sys.stderr)
        return 65
    except Exception:
        print("PHASE1EF_ENVIRONMENT_ARCHIVE=FAILED", file=sys.stderr)
        print(
            "PHASE1EF_ENVIRONMENT_ARCHIVE_FAILURE_CODE=FILESYSTEM_OR_RUNTIME_FAILURE",
            file=sys.stderr,
        )
        return 70
    print("PHASE1EF_ENVIRONMENT_ARCHIVE=PASS_BYTE_IDENTICAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
