#!/usr/bin/env python3
"""Create and validate the private Phase 1E-F preexecution authority manifest.

The manifest binds attempt 004 to exactly seven tracked control-plane files and
to zero execution scopes.  It intentionally contains no cloud identity,
credential, billing, clinical, imaging, or patient-level authority.  Both the
writer and validator fail closed, reject duplicate JSON keys, and expose only
fixed aggregate-safe status codes on the command line.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Final


SCHEMA_NAME: Final = "lvef_c3_phase1ef_preexecution_authority_manifest"
SCHEMA_VERSION: Final = 1
AUTHORIZED_ATTEMPT_ID: Final = (
    "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
)
AUTHORIZED_BRANCH: Final = "codex/lvef-multitask-revalidation"
HISTORICAL_BASE_COMMIT: Final = "23c74ccfd145ab9a423b6942a431a1894a34ab67"

COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
UTC_RE: Final = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)

# These paths and modes are the trusted repository contract.  They are not
# accepted from an environment file or from the manifest being validated.
ROLE_SPECS: Final[dict[str, tuple[str, int]]] = {
    "capacity_parser": (
        "scripts/capture_lvef_c3_post_reallocation_capacity.py",
        0o755,
    ),
    "capacity_wrapper": (
        "scripts/scc_capture_lvef_c3_post_reallocation_capacity.sh",
        0o755,
    ),
    "environment_preparer": (
        "scripts/scc_prepare_lvef_c3_phase1ef_environment.sh",
        0o755,
    ),
    "phase1ef_runbook": (
        "docs/lvef_multitask/scc_phase1ef_pretransfer_commands.md",
        0o644,
    ),
    "safe_export_policy": (
        "configs/lvef_multitask_safe_export_policy.yaml",
        0o644,
    ),
    "backup_recovery_policy": (
        "configs/lvef_c3_backup_recovery_policy_v1.yaml",
        0o644,
    ),
    "tracked_attempt_dispatcher": (
        "scripts/scc_execute_lvef_c3_phase1ef_attempt.sh",
        0o755,
    ),
}
AUTHORITY_ROLES: Final = frozenset(ROLE_SPECS)

EXECUTION_SCOPE_FLAGS: Final[dict[str, bool]] = {
    "cloud_access": False,
    "object_listing": False,
    "object_body_transfer": False,
    "scheduler_submission": False,
    "dicom_processing": False,
    "echoprime_inference": False,
    "embedding_creation": False,
    "modeling": False,
    "predictions": False,
    "confirmatory_access": False,
}

TOP_LEVEL_KEYS: Final = frozenset(
    {
        "schema_name",
        "schema_version",
        "attempt_id",
        "git_branch",
        "git_commit",
        "historical_base_commit",
        "created_utc",
        "execution_scopes_granted",
        "execution_scope_flags",
        "authorities",
    }
)
AUTHORITY_KEYS: Final = frozenset(
    {
        "logical_role",
        "canonical_absolute_path",
        "sha256",
        "size_bytes",
        "required_file_type",
        "required_owner_policy",
        "required_mode_policy",
        "symlink_permitted",
    }
)


class AuthorityManifestError(ValueError):
    """A fail-closed error whose message is a fixed aggregate-safe code."""


def _fail(code: str) -> None:
    raise AuthorityManifestError(code)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], error_code: str
) -> None:
    if set(value) != expected:
        _fail(error_code)


def _canonical_existing_path(path: Path, *, expected_directory: bool) -> Path:
    if not path.is_absolute():
        _fail("PATH_NOT_ABSOLUTE")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("PATH_MISSING")
    if path != resolved:
        _fail("PATH_NOT_CANONICAL")
    try:
        metadata = os.lstat(path)
    except OSError:
        _fail("PATH_MISSING")
    if stat.S_ISLNK(metadata.st_mode):
        _fail("PATH_SYMLINK_FORBIDDEN")
    if expected_directory:
        if not stat.S_ISDIR(metadata.st_mode):
            _fail("PATH_NOT_DIRECTORY")
    elif not stat.S_ISREG(metadata.st_mode):
        _fail("AUTHORITY_NOT_REGULAR_FILE")
    return resolved


def _canonical_new_path(path: Path) -> Path:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        _fail("OUTPUT_PATH_NOT_ABSOLUTE")
    parent = _canonical_existing_path(path.parent, expected_directory=True)
    candidate = parent / path.name
    if candidate != path:
        _fail("OUTPUT_PATH_NOT_CANONICAL")
    if os.path.lexists(path):
        _fail("OUTPUT_ALREADY_EXISTS")
    parent_metadata = os.lstat(parent)
    if parent_metadata.st_uid != os.geteuid():
        _fail("OUTPUT_PARENT_OWNER_POLICY_FAILED")
    if stat.S_IMODE(parent_metadata.st_mode) not in {0o700, 0o2700}:
        _fail("OUTPUT_PARENT_MODE_POLICY_FAILED")
    return candidate


def _read_regular_current_owner(path: Path, required_mode: int) -> bytes:
    _canonical_existing_path(path, expected_directory=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("AUTHORITY_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("AUTHORITY_NOT_REGULAR_FILE")
        if metadata.st_uid != os.geteuid():
            _fail("AUTHORITY_OWNER_POLICY_FAILED")
        if stat.S_IMODE(metadata.st_mode) != required_mode:
            _fail("AUTHORITY_MODE_POLICY_FAILED")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size:
            _fail("AUTHORITY_SIZE_CHANGED_DURING_READ")
        try:
            final_path_metadata = os.lstat(path)
        except OSError:
            _fail("AUTHORITY_PATH_CHANGED_DURING_READ")
        if (
            final_path_metadata.st_dev != metadata.st_dev
            or final_path_metadata.st_ino != metadata.st_ino
            or final_path_metadata.st_size != metadata.st_size
            or final_path_metadata.st_mtime_ns != metadata.st_mtime_ns
            or final_path_metadata.st_uid != metadata.st_uid
            or stat.S_IMODE(final_path_metadata.st_mode)
            != stat.S_IMODE(metadata.st_mode)
        ):
            _fail("AUTHORITY_PATH_CHANGED_DURING_READ")
        return payload
    finally:
        os.close(descriptor)


def _read_private_manifest(path: Path) -> bytes:
    return _read_regular_current_owner(path, 0o600)


def _parse_created_utc(value: Any) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail("CREATED_UTC_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail("CREATED_UTC_INVALID")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("CREATED_UTC_INVALID")
    return value


def _validate_static_identity(
    *,
    attempt_id: Any,
    git_branch: Any,
    git_commit: Any,
    historical_base_commit: Any,
) -> None:
    if attempt_id != AUTHORIZED_ATTEMPT_ID:
        _fail("ATTEMPT_ID_NOT_AUTHORIZED")
    if git_branch != AUTHORIZED_BRANCH:
        _fail("GIT_BRANCH_NOT_AUTHORIZED")
    if not isinstance(git_commit, str) or COMMIT_RE.fullmatch(git_commit) is None:
        _fail("GIT_COMMIT_INVALID")
    if historical_base_commit != HISTORICAL_BASE_COMMIT:
        _fail("HISTORICAL_BASE_COMMIT_INVALID")


def _authority_path(worktree: Path, role: str) -> Path:
    relative_path, _ = ROLE_SPECS[role]
    expected = worktree / relative_path
    return _canonical_existing_path(expected, expected_directory=False)


def _authority_entry(worktree: Path, role: str) -> dict[str, Any]:
    expected_path = _authority_path(worktree, role)
    _, expected_mode = ROLE_SPECS[role]
    payload = _read_regular_current_owner(expected_path, expected_mode)
    if not payload:
        _fail("AUTHORITY_EMPTY_FILE")
    return {
        "logical_role": role,
        "canonical_absolute_path": str(expected_path),
        "sha256": _sha256(payload),
        "size_bytes": len(payload),
        "required_file_type": "REGULAR_FILE",
        "required_owner_policy": "CURRENT_EFFECTIVE_USER",
        "required_mode_policy": f"EXACT_{expected_mode:04o}",
        "symlink_permitted": False,
    }


def build_manifest_payload(
    *,
    worktree: Path,
    attempt_id: str,
    git_branch: str,
    git_commit: str,
    historical_base_commit: str,
    created_utc: str | None = None,
) -> dict[str, Any]:
    """Build a validated in-memory manifest from the exact tracked files."""

    canonical_worktree = _canonical_existing_path(worktree, expected_directory=True)
    _validate_static_identity(
        attempt_id=attempt_id,
        git_branch=git_branch,
        git_commit=git_commit,
        historical_base_commit=historical_base_commit,
    )
    timestamp = created_utc or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _parse_created_utc(timestamp)
    value: dict[str, Any] = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "historical_base_commit": historical_base_commit,
        "created_utc": timestamp,
        "execution_scopes_granted": 0,
        "execution_scope_flags": dict(EXECUTION_SCOPE_FLAGS),
        "authorities": [
            _authority_entry(canonical_worktree, role)
            for role in sorted(AUTHORITY_ROLES)
        ],
    }
    validate_manifest_payload(
        value,
        worktree=canonical_worktree,
        expected_attempt_id=attempt_id,
        expected_git_branch=git_branch,
        expected_git_commit=git_commit,
        expected_historical_base_commit=historical_base_commit,
    )
    return value


def _validate_authority_entry(
    entry: Any, *, worktree: Path, seen_roles: set[str]
) -> None:
    if not isinstance(entry, Mapping):
        _fail("AUTHORITY_ENTRY_NOT_MAPPING")
    _require_exact_keys(entry, AUTHORITY_KEYS, "AUTHORITY_ENTRY_SCHEMA_NOT_CLOSED")
    role = entry["logical_role"]
    if not isinstance(role, str) or role not in AUTHORITY_ROLES:
        _fail("AUTHORITY_ROLE_UNKNOWN")
    if role in seen_roles:
        _fail("AUTHORITY_ROLE_DUPLICATE")
    seen_roles.add(role)

    expected_path = _authority_path(worktree, role)
    _, expected_mode = ROLE_SPECS[role]
    if entry["canonical_absolute_path"] != str(expected_path):
        _fail("AUTHORITY_PATH_MISMATCH")
    if entry["required_file_type"] != "REGULAR_FILE":
        _fail("AUTHORITY_FILE_TYPE_POLICY_INVALID")
    if entry["required_owner_policy"] != "CURRENT_EFFECTIVE_USER":
        _fail("AUTHORITY_OWNER_POLICY_INVALID")
    if entry["required_mode_policy"] != f"EXACT_{expected_mode:04o}":
        _fail("AUTHORITY_MODE_POLICY_INVALID")
    if entry["symlink_permitted"] is not False:
        _fail("AUTHORITY_SYMLINK_POLICY_INVALID")
    if (
        not isinstance(entry["sha256"], str)
        or SHA256_RE.fullmatch(entry["sha256"]) is None
    ):
        _fail("AUTHORITY_SHA256_INVALID")
    if (
        type(entry["size_bytes"]) is not int
        or entry["size_bytes"] <= 0
    ):
        _fail("AUTHORITY_SIZE_INVALID")

    payload = _read_regular_current_owner(expected_path, expected_mode)
    if len(payload) != entry["size_bytes"]:
        _fail("AUTHORITY_SIZE_MISMATCH")
    if _sha256(payload) != entry["sha256"]:
        _fail("AUTHORITY_SHA256_MISMATCH")


def validate_manifest_payload(
    value: Any,
    *,
    worktree: Path,
    expected_attempt_id: str,
    expected_git_branch: str,
    expected_git_commit: str,
    expected_historical_base_commit: str,
) -> Mapping[str, Any]:
    """Validate the closed manifest and all seven live authority files."""

    canonical_worktree = _canonical_existing_path(worktree, expected_directory=True)
    if not isinstance(value, Mapping):
        _fail("MANIFEST_NOT_MAPPING")
    _require_exact_keys(value, TOP_LEVEL_KEYS, "MANIFEST_SCHEMA_NOT_CLOSED")
    if value["schema_name"] != SCHEMA_NAME:
        _fail("SCHEMA_NAME_INVALID")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_INVALID")
    _validate_static_identity(
        attempt_id=value["attempt_id"],
        git_branch=value["git_branch"],
        git_commit=value["git_commit"],
        historical_base_commit=value["historical_base_commit"],
    )
    if expected_attempt_id != AUTHORIZED_ATTEMPT_ID or value["attempt_id"] != expected_attempt_id:
        _fail("EXPECTED_ATTEMPT_ID_MISMATCH")
    if expected_git_branch != AUTHORIZED_BRANCH or value["git_branch"] != expected_git_branch:
        _fail("EXPECTED_GIT_BRANCH_MISMATCH")
    if (
        not isinstance(expected_git_commit, str)
        or COMMIT_RE.fullmatch(expected_git_commit) is None
        or value["git_commit"] != expected_git_commit
    ):
        _fail("EXPECTED_GIT_COMMIT_MISMATCH")
    if (
        expected_historical_base_commit != HISTORICAL_BASE_COMMIT
        or value["historical_base_commit"] != expected_historical_base_commit
    ):
        _fail("EXPECTED_HISTORICAL_BASE_MISMATCH")
    _parse_created_utc(value["created_utc"])
    if type(value["execution_scopes_granted"]) is not int:
        _fail("EXECUTION_SCOPE_COUNT_INVALID")
    if value["execution_scopes_granted"] != 0:
        _fail("EXECUTION_SCOPE_COUNT_NONZERO")
    flags = value["execution_scope_flags"]
    if not isinstance(flags, Mapping):
        _fail("EXECUTION_SCOPE_FLAGS_NOT_MAPPING")
    _require_exact_keys(
        flags,
        frozenset(EXECUTION_SCOPE_FLAGS),
        "EXECUTION_SCOPE_FLAGS_SCHEMA_NOT_CLOSED",
    )
    for key in EXECUTION_SCOPE_FLAGS:
        if flags[key] is not False:
            _fail("EXECUTION_SCOPE_FLAG_ENABLED")

    authorities = value["authorities"]
    if not isinstance(authorities, list):
        _fail("AUTHORITIES_NOT_LIST")
    if len(authorities) != len(AUTHORITY_ROLES):
        _fail("AUTHORITY_ROLE_COUNT_INVALID")
    seen_roles: set[str] = set()
    for entry in authorities:
        _validate_authority_entry(entry, worktree=canonical_worktree, seen_roles=seen_roles)
    if seen_roles != AUTHORITY_ROLES:
        _fail("AUTHORITY_ROLE_SET_INVALID")
    return value


def parse_manifest_bytes(payload: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except AuthorityManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("MANIFEST_JSON_INVALID")
    if not isinstance(value, Mapping):
        _fail("MANIFEST_NOT_MAPPING")
    return value


def load_and_validate_manifest(
    *,
    manifest_path: Path,
    manifest_sha256: str,
    worktree: Path,
    expected_attempt_id: str,
    expected_git_branch: str,
    expected_git_commit: str,
    expected_historical_base_commit: str,
) -> Mapping[str, Any]:
    """Validate a private manifest binding and its live file authorities."""

    if not isinstance(manifest_sha256, str) or SHA256_RE.fullmatch(manifest_sha256) is None:
        _fail("MANIFEST_SHA256_INVALID")
    payload = _read_private_manifest(manifest_path)
    if _sha256(payload) != manifest_sha256:
        _fail("MANIFEST_SHA256_MISMATCH")
    value = parse_manifest_bytes(payload)
    return validate_manifest_payload(
        value,
        worktree=worktree,
        expected_attempt_id=expected_attempt_id,
        expected_git_branch=expected_git_branch,
        expected_git_commit=expected_git_commit,
        expected_historical_base_commit=expected_historical_base_commit,
    )


def _manifest_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def write_manifest_atomic(
    *,
    output_path: Path,
    worktree: Path,
    attempt_id: str,
    git_branch: str,
    git_commit: str,
    historical_base_commit: str,
    created_utc: str | None = None,
) -> str:
    """Validate, privately write, and no-clobber promote one manifest."""

    canonical_output = _canonical_new_path(output_path)
    canonical_worktree = _canonical_existing_path(worktree, expected_directory=True)
    value = build_manifest_payload(
        worktree=canonical_worktree,
        attempt_id=attempt_id,
        git_branch=git_branch,
        git_commit=git_commit,
        historical_base_commit=historical_base_commit,
        created_utc=created_utc,
    )
    payload = _manifest_bytes(value)
    expected_sha = _sha256(payload)
    temporary = canonical_output.parent / (
        f".{canonical_output.name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    )
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        load_and_validate_manifest(
            manifest_path=temporary,
            manifest_sha256=expected_sha,
            worktree=canonical_worktree,
            expected_attempt_id=attempt_id,
            expected_git_branch=git_branch,
            expected_git_commit=git_commit,
            expected_historical_base_commit=historical_base_commit,
        )
        try:
            os.link(temporary, canonical_output, follow_symlinks=False)
        except FileExistsError:
            _fail("OUTPUT_ALREADY_EXISTS")
        directory_descriptor = os.open(canonical_output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        os.unlink(temporary)
        load_and_validate_manifest(
            manifest_path=canonical_output,
            manifest_sha256=expected_sha,
            worktree=canonical_worktree,
            expected_attempt_id=attempt_id,
            expected_git_branch=git_branch,
            expected_git_commit=git_commit,
            expected_historical_base_commit=historical_base_commit,
        )
        return expected_sha
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


class _SafeArgumentParser(argparse.ArgumentParser):
    """Argparse boundary that never reflects private argument text."""

    def error(self, message: str) -> None:  # noqa: ARG002 - intentionally hidden
        self.exit(64, "PHASE1EF_AUTHORITY_MANIFEST_ARGUMENTS=FAILED\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Write or validate the private Phase 1E-F authority manifest."
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_SafeArgumentParser
    )
    for name in ("write", "validate"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--worktree", type=Path, required=True)
        subparser.add_argument("--attempt-id", required=True)
        subparser.add_argument("--git-branch", required=True)
        subparser.add_argument("--git-commit", required=True)
        subparser.add_argument("--historical-base-commit", required=True)
        if name == "write":
            subparser.add_argument("--output", type=Path, required=True)
            subparser.add_argument("--created-utc")
        else:
            subparser.add_argument("--manifest", type=Path, required=True)
            subparser.add_argument("--manifest-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "write":
            write_manifest_atomic(
                output_path=args.output,
                worktree=args.worktree,
                attempt_id=args.attempt_id,
                git_branch=args.git_branch,
                git_commit=args.git_commit,
                historical_base_commit=args.historical_base_commit,
                created_utc=args.created_utc,
            )
            print("PHASE1EF_AUTHORITY_MANIFEST_WRITE=PASS")
        else:
            load_and_validate_manifest(
                manifest_path=args.manifest,
                manifest_sha256=args.manifest_sha256,
                worktree=args.worktree,
                expected_attempt_id=args.attempt_id,
                expected_git_branch=args.git_branch,
                expected_git_commit=args.git_commit,
                expected_historical_base_commit=args.historical_base_commit,
            )
            print("PHASE1EF_AUTHORITY_MANIFEST_VALIDATION=PASS")
    except AuthorityManifestError as exc:
        print("PHASE1EF_AUTHORITY_MANIFEST=FAILED", file=sys.stderr)
        print(f"PHASE1EF_AUTHORITY_MANIFEST_FAILURE_CODE={exc}", file=sys.stderr)
        return 65
    except Exception:
        # Filesystem failures and unexpected runtime errors may contain private
        # paths in their exception text.  The CLI boundary must never expose
        # that text or a traceback; detailed diagnosis remains local.
        print("PHASE1EF_AUTHORITY_MANIFEST=FAILED", file=sys.stderr)
        print(
            "PHASE1EF_AUTHORITY_MANIFEST_FAILURE_CODE="
            "FILESYSTEM_OR_RUNTIME_FAILURE",
            file=sys.stderr,
        )
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
