#!/usr/bin/env python3
"""Build and restore-test a bounded C3 control-authority backup.

The tool is deliberately offline.  It creates a branch-scoped Git bundle and
copies only explicitly allowlisted non-Git authorities into an owner-private,
no-clobber backup root.  It then restores both into a new isolated root and
verifies exact hashes, linked-worktree authority, permissions, symlinks, and
credential exclusions.  Detailed manifests remain restricted; the aggregate
summary contains no paths, identifiers, credentials, or cloud values.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Sequence

import yaml


SCHEMA_VERSION = 1
MANIFEST_TYPE = "lvef_c3_control_authority_backup_manifest_v1"
MANIFEST_STATUS = "PASS_OWNER_PRIVATE_CONTROL_AUTHORITY_BACKUP"
RESTORE_TYPE = "lvef_c3_control_authority_restore_receipt_v1"
RESTORE_STATUS = "PASS_ISOLATED_CONTROL_AUTHORITY_RESTORE_TEST"
AGGREGATE_TYPE = "lvef_c3_control_authority_backup_recovery_summary_v1"
AGGREGATE_STATUS = "PASS_BACKUP_RECOVERY_WITNESS"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ATTEMPT_RE = re.compile(
    r"^lvef_multitask_phase1ef_[a-z0-9][a-z0-9_-]{5,95}$"
)
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
SAFE_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PRIVATE_DIRECTORY_MODES = {0o700, 0o2700}
BUFFER_BYTES = 1024 * 1024
TEXT_PATTERN_OVERLAP = 8192


class BackupRecoveryError(RuntimeError):
    """Fail-closed error with an aggregate-safe code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BackupRecoveryError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_file_nofollow(path: Path) -> tuple[int, str, int]:
    _require_regular_file(path, "HASH_INPUT")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(descriptor, BUFFER_BYTES)
            if not block:
                break
            size += len(block)
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or size != after.st_size
    ):
        raise BackupRecoveryError("FILE_CHANGED_DURING_HASHING")
    return size, digest.hexdigest(), stat.S_IMODE(after.st_mode)


def sha256_file(path: Path) -> str:
    return _sha256_file_nofollow(path)[1]


def _safe_relative_path(value: str, code: str = "UNSAFE_RELATIVE_PATH") -> str:
    if not value or value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise BackupRecoveryError(code)
    candidate = PurePosixPath(value)
    if (
        not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or any(not SAFE_PART_RE.fullmatch(part) for part in candidate.parts)
    ):
        raise BackupRecoveryError(code)
    return candidate.as_posix()


def _require_no_symlink_ancestors(path: Path, code: str) -> None:
    if not path.is_absolute():
        raise BackupRecoveryError(f"{code}_NOT_ABSOLUTE")
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except FileNotFoundError:
            raise BackupRecoveryError(f"{code}_ANCESTOR_MISSING")
        if stat.S_ISLNK(metadata.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise BackupRecoveryError(f"{code}_SYMLINK_ANCESTOR")
        if not stat.S_ISDIR(metadata.st_mode):
            raise BackupRecoveryError(f"{code}_ANCESTOR_NOT_DIRECTORY")


def _require_regular_file(path: Path, code: str) -> os.stat_result:
    _require_no_symlink_ancestors(path, code)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise BackupRecoveryError(f"{code}_MISSING")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BackupRecoveryError(f"{code}_NOT_REGULAR_NOFOLLOW")
    return metadata


def _require_private_file(path: Path, code: str) -> os.stat_result:
    metadata = _require_regular_file(path, code)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise BackupRecoveryError(f"{code}_NOT_OWNER_PRIVATE_0600")
    return metadata


def _require_source_authority_file(path: Path, code: str) -> os.stat_result:
    """Require a stable owner-controlled source without rewriting its mode.

    Historical immutable authorities may be 0400, 0600, 0640, or 0644.  Their
    source mode is acceptable when the effective owner owns the regular file,
    the owner can read it, and neither group nor other has a write bit.  Every
    created backup/receipt remains mode 0600.
    """
    metadata = _require_regular_file(path, code)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        metadata.st_uid != os.geteuid()
        or not mode & stat.S_IRUSR
        or mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise BackupRecoveryError(f"{code}_NOT_OWNER_CONTROLLED_READABLE")
    return metadata


def _require_private_directory(path: Path, code: str) -> os.stat_result:
    _require_no_symlink_ancestors(path / ".authority_leaf", code)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise BackupRecoveryError(f"{code}_MISSING")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) not in PRIVATE_DIRECTORY_MODES
    ):
        raise BackupRecoveryError(f"{code}_NOT_OWNER_PRIVATE_DIRECTORY")
    return metadata


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_under_any_root(path: Path, roots: Sequence[Path], code: str) -> None:
    lexical = path.absolute()
    if not any(_is_relative_to(lexical, root.absolute()) for root in roots):
        raise BackupRecoveryError(code)


def _create_private_root(path: Path, *, approved_prefix: Path, code: str) -> None:
    if path.exists() or path.is_symlink():
        raise BackupRecoveryError(f"{code}_COLLISION")
    _require_under_any_root(path, [approved_prefix], f"{code}_OUTSIDE_APPROVED_PREFIX")
    _require_private_directory(path.parent, f"{code}_PARENT")
    os.mkdir(path, 0o700)
    if stat.S_IMODE(os.lstat(path).st_mode) not in PRIVATE_DIRECTORY_MODES:
        raise BackupRecoveryError(f"{code}_MODE_INVALID")


def _ensure_private_relative_directories(root: Path, relative_parent: PurePosixPath) -> Path:
    current = root
    for part in relative_parent.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.exists() or current.is_symlink():
            _require_private_directory(current, "DESTINATION_DIRECTORY")
            continue
        os.mkdir(current, 0o700)
        _require_private_directory(current, "DESTINATION_DIRECTORY")
    return current


def _write_new_private(path: Path, payload: bytes, *, maximum_bytes: int) -> None:
    if len(payload) > maximum_bytes:
        raise BackupRecoveryError("OUTPUT_EXCEEDS_POLICY_BOUND")
    if path.exists() or path.is_symlink():
        raise BackupRecoveryError("OUTPUT_COLLISION")
    _require_private_directory(path.parent, "OUTPUT_PARENT")
    name = f".{path.name}.partial.{os.getpid()}.{secrets.token_hex(8)}"
    temporary = path.parent / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        if sha256_file(path) != hashlib.sha256(payload).hexdigest():
            raise BackupRecoveryError("OUTPUT_POSTWRITE_HASH_MISMATCH")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _git_environment() -> dict[str, str]:
    """Return an offline Git environment insulated from ambient hooks/config."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_VALUE_1": "false",
    }


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if completed.returncode != 0:
        raise BackupRecoveryError("GIT_COMMAND_FAILED_SANITIZED")
    return completed.stdout.strip()


def _git_dir(git_dir: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if completed.returncode != 0:
        raise BackupRecoveryError("GIT_COMMAND_FAILED_SANITIZED")
    return completed.stdout.strip()


def _git_dir_bytes(git_dir: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_git_environment(), check=False,
    )
    if completed.returncode != 0:
        raise BackupRecoveryError("GIT_COMMAND_FAILED_SANITIZED")
    return completed.stdout


def _scan_payload_matches(
    payload: bytes, patterns: Sequence[re.Pattern[str]],
    allowed_synthetic: Sequence[re.Pattern[str]],
) -> int:
    text = payload.decode("utf-8", errors="ignore")
    allowed_count = 0
    for pattern in patterns:
        for match in pattern.finditer(text):
            matched = match.group(0)
            if any(item.search(matched) for item in allowed_synthetic):
                allowed_count += 1
                continue
            raise BackupRecoveryError("GIT_BUNDLE_CREDENTIAL_OR_PRIVATE_VALUE_DETECTED")
    return allowed_count


def _scan_full_reachable_bundle(
    *, git_dir: Path, branch: str, name_patterns: Sequence[re.Pattern[str]],
    content_patterns: Sequence[re.Pattern[str]],
    allowed_synthetic: Sequence[re.Pattern[str]], policy: Mapping[str, Any],
) -> Mapping[str, int | bool]:
    """Scan every commit, historical path, and unique blob reachable by branch."""
    scan_policy = policy["bundle_scan"]
    forbidden_suffixes = {
        str(item).lower() for item in policy["forbidden"]["suffixes"]
    }
    commits = [
        line for line in _git_dir(git_dir, "rev-list", f"refs/heads/{branch}").splitlines()
        if line
    ]
    if (
        not commits
        or len(commits) > int(scan_policy["maximum_commits"])
        or any(not COMMIT_RE.fullmatch(item) for item in commits)
    ):
        raise BackupRecoveryError("GIT_BUNDLE_COMMIT_SET_INVALID")
    blobs: set[str] = set()
    historical_path_count = 0
    synthetic_match_count = 0
    for commit in commits:
        commit_payload = _git_dir_bytes(git_dir, "cat-file", "commit", commit)
        synthetic_match_count += _scan_payload_matches(
            commit_payload, content_patterns, allowed_synthetic
        )
        tree = _git_dir_bytes(
            git_dir, "ls-tree", "-r", "-z", "--full-tree", commit
        )
        for raw_record in tree.split(b"\0"):
            if not raw_record:
                continue
            try:
                metadata, raw_path = raw_record.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
                path = raw_path.decode("utf-8", errors="strict")
            except (ValueError, UnicodeDecodeError) as exc:
                raise BackupRecoveryError("GIT_BUNDLE_TREE_RECORD_INVALID") from exc
            candidate = PurePosixPath(path)
            if (
                not path or path.startswith(("/", "~")) or "\\" in path
                or any(part in {"", ".", ".."} for part in candidate.parts)
                or any(pattern.fullmatch(candidate.name) for pattern in name_patterns)
                or candidate.suffix.lower() in forbidden_suffixes
            ):
                raise BackupRecoveryError("GIT_BUNDLE_HISTORICAL_PATH_FORBIDDEN")
            if not re.fullmatch(r"[0-7]{6}", mode) or not GIT_OBJECT_RE.fullmatch(object_id):
                raise BackupRecoveryError("GIT_BUNDLE_TREE_RECORD_INVALID")
            historical_path_count += 1
            if kind == "blob":
                blobs.add(object_id)
            elif kind != "commit":
                raise BackupRecoveryError("GIT_BUNDLE_TREE_OBJECT_TYPE_INVALID")
    if len(blobs) > int(scan_policy["maximum_unique_blobs"]):
        raise BackupRecoveryError("GIT_BUNDLE_BLOB_COUNT_BOUND_EXCEEDED")
    total_blob_bytes = 0
    for object_id in sorted(blobs):
        size_text = _git_dir(git_dir, "cat-file", "-s", object_id)
        if not size_text.isdigit():
            raise BackupRecoveryError("GIT_BUNDLE_BLOB_SIZE_INVALID")
        blob_size = int(size_text)
        total_blob_bytes += blob_size
        if total_blob_bytes > int(scan_policy["maximum_total_blob_bytes"]):
            raise BackupRecoveryError("GIT_BUNDLE_BLOB_BYTES_BOUND_EXCEEDED")
        payload = _git_dir_bytes(git_dir, "cat-file", "blob", object_id)
        if len(payload) != blob_size:
            raise BackupRecoveryError("GIT_BUNDLE_BLOB_SIZE_MISMATCH")
        synthetic_match_count += _scan_payload_matches(
            payload, content_patterns, allowed_synthetic
        )
    return {
        "reachable_commit_count": len(commits),
        "historical_path_record_count": historical_path_count,
        "reachable_unique_blob_count": len(blobs),
        "reachable_blob_bytes": total_blob_bytes,
        "allowed_synthetic_fixture_match_count": synthetic_match_count,
        "full_reachable_credential_scan_passed": True,
        "full_reachable_private_cloud_value_scan_passed": True,
        "historical_path_scan_passed": True,
    }


def _git_snapshot(checkout: Path, *, branch: str, commit: str, origin: str,
                  approved_origin: re.Pattern[str]) -> Mapping[str, str]:
    _require_no_symlink_ancestors(checkout / ".authority_leaf", "CHECKOUT")
    if checkout.is_symlink() or not checkout.is_dir():
        raise BackupRecoveryError("CHECKOUT_NOT_REGULAR_DIRECTORY")
    root = Path(_git(checkout, "rev-parse", "--show-toplevel"))
    if root != checkout.resolve(strict=True):
        raise BackupRecoveryError("CHECKOUT_ROOT_MISMATCH")
    observed_branch = _git(checkout, "branch", "--show-current")
    observed_commit = _git(checkout, "rev-parse", "HEAD")
    tracked_status = _git(checkout, "status", "--porcelain", "--untracked-files=no")
    origin_ref = _git(checkout, "rev-parse", f"refs/remotes/{origin}/{branch}")
    origin_url = _git(checkout, "remote", "get-url", origin)
    if (
        observed_branch != branch
        or observed_commit != commit
        or origin_ref != commit
        or tracked_status
        or not approved_origin.fullmatch(origin_url)
    ):
        raise BackupRecoveryError("CHECKOUT_ORIGIN_AUTHORITY_MISMATCH")
    return {
        "branch": observed_branch,
        "commit": observed_commit,
        "tracked_status_sha256": hashlib.sha256(tracked_status.encode()).hexdigest(),
        "origin_ref_commit": origin_ref,
        "origin_url_sha256": hashlib.sha256(origin_url.encode()).hexdigest(),
    }


def _load_policy(path: Path) -> tuple[Mapping[str, Any], str]:
    metadata = _require_regular_file(path, "POLICY")
    if metadata.st_size > 1024 * 1024:
        raise BackupRecoveryError("POLICY_TOO_LARGE")
    payload = path.read_bytes()
    try:
        value = yaml.safe_load(payload.decode("utf-8"))
    except Exception as exc:
        raise BackupRecoveryError("POLICY_INVALID_YAML") from exc
    if not isinstance(value, Mapping):
        raise BackupRecoveryError("POLICY_NOT_MAPPING")
    required_top = {
        "schema_version", "policy_id", "status", "git", "bundle_scan", "roots", "bounds",
        "classifications", "required_declarations", "artifact_roles",
        "required_artifact_roles", "forbidden", "execution_boundary",
    }
    if set(value) != required_top:
        raise BackupRecoveryError("POLICY_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != 1
        or value.get("policy_id") != "lvef_c3_control_authority_backup_recovery_v1"
        or value.get("status") != "IMPLEMENTED_OFFLINE_OWNER_GATED"
    ):
        raise BackupRecoveryError("POLICY_IDENTITY_INVALID")
    categories = value["classifications"].get("allowed")
    if not isinstance(categories, list) or len(categories) != 8 or len(set(categories)) != 8:
        raise BackupRecoveryError("POLICY_CLASSIFICATION_SET_INVALID")
    required_roles = value.get("required_artifact_roles")
    artifact_roles = value.get("artifact_roles")
    declarations = value.get("required_declarations")
    if (
        not isinstance(required_roles, list)
        or len(required_roles) != len(set(required_roles))
        or not isinstance(artifact_roles, Mapping)
        or not set(required_roles).issubset(artifact_roles)
        or not isinstance(declarations, Mapping)
    ):
        raise BackupRecoveryError("POLICY_ROLE_SET_INVALID")
    expected_role_keys = {
        "classification", "backup_relative_path", "permitted_suffixes",
        "binary_payload_permitted", "schema_kind", "schema_parameters",
        "fixed_expected_sha256",
        "fixed_expected_size_bytes",
    }
    for role, role_policy in artifact_roles.items():
        if not isinstance(role_policy, Mapping) or set(role_policy) != expected_role_keys:
            raise BackupRecoveryError("POLICY_ARTIFACT_ROLE_SCHEMA_NOT_CLOSED")
        schema_kind = role_policy.get("schema_kind")
        fixed_sha = role_policy.get("fixed_expected_sha256")
        fixed_size = role_policy.get("fixed_expected_size_bytes")
        if (
            not isinstance(schema_kind, str)
            or not ROLE_RE.fullmatch(schema_kind)
            or not isinstance(role_policy.get("schema_parameters"), Mapping)
            or (fixed_sha is not None and not SHA256_RE.fullmatch(str(fixed_sha)))
            or (
                fixed_size is not None
                and (
                    not isinstance(fixed_size, int)
                    or isinstance(fixed_size, bool)
                    or fixed_size <= 0
                )
            )
        ):
            raise BackupRecoveryError("POLICY_ARTIFACT_ROLE_AUTHORITY_INVALID")
        parameter_keys = {
            "echoprime_checkpoint_v1": set(),
            "selected_study_manifest_v1": {"expected_rows"},
            "selected_source_manifest_v1": {"expected_rows"},
            "selected_source_metadata_receipt_v1": {
                "expected_rows", "expected_source_bytes"
            },
            "subject_split_map_v1": {"expected_rows"},
            "strict_json_mapping_v1": set(),
            "strict_json_or_csv_v1": set(),
            "strict_jsonl_mapping_v1": set(),
        }
        parameters = role_policy["schema_parameters"]
        if (
            schema_kind not in parameter_keys
            or set(parameters) != parameter_keys[schema_kind]
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item <= 0
                for item in parameters.values()
            )
        ):
            raise BackupRecoveryError("POLICY_ARTIFACT_SCHEMA_PARAMETERS_INVALID")
    bundle_scan = value.get("bundle_scan")
    if (
        not isinstance(bundle_scan, Mapping)
        or set(bundle_scan) != {
            "maximum_commits", "maximum_unique_blobs", "maximum_total_blob_bytes",
            "allowed_synthetic_match_patterns",
        }
        or any(
            not isinstance(bundle_scan.get(key), int)
            or isinstance(bundle_scan.get(key), bool)
            or bundle_scan[key] <= 0
            for key in ("maximum_commits", "maximum_unique_blobs", "maximum_total_blob_bytes")
        )
        or not isinstance(bundle_scan.get("allowed_synthetic_match_patterns"), list)
    ):
        raise BackupRecoveryError("POLICY_BUNDLE_SCAN_INVALID")
    return value, hashlib.sha256(payload).hexdigest()


def _parse_declarations(values: Sequence[str], policy: Mapping[str, Any]) -> Mapping[str, str]:
    observed: dict[str, str] = {}
    for raw in values:
        fields = raw.split("=", 1)
        if len(fields) != 2 or not ROLE_RE.fullmatch(fields[0]):
            raise BackupRecoveryError("DECLARATION_ARGUMENT_INVALID")
        role, category = fields
        if role in observed:
            raise BackupRecoveryError("DUPLICATE_DECLARATION_ROLE")
        observed[role] = category
    expected = {str(key): str(value) for key, value in policy["required_declarations"].items()}
    if observed != expected:
        raise BackupRecoveryError("DECLARATION_SET_OR_CLASSIFICATION_MISMATCH")
    return observed


def _parse_artifacts(
    values: Sequence[str], policy: Mapping[str, Any]
) -> Mapping[str, tuple[str, str, int, str, Path]]:
    observed: dict[str, tuple[str, str, int, str, Path]] = {}
    for raw in values:
        fields = raw.split("=", 5)
        if (
            len(fields) != 6
            or not ROLE_RE.fullmatch(fields[0])
            or not fields[5]
            or not SHA256_RE.fullmatch(fields[2])
            or not fields[3].isdigit()
            or int(fields[3]) <= 0
            or not ROLE_RE.fullmatch(fields[4])
        ):
            raise BackupRecoveryError("ARTIFACT_ARGUMENT_INVALID")
        role, category, expected_sha, expected_size_raw, schema_kind, source = fields
        if role in observed:
            raise BackupRecoveryError("DUPLICATE_ARTIFACT_ROLE")
        observed[role] = (
            category, expected_sha, int(expected_size_raw), schema_kind, Path(source)
        )
    required = set(str(item) for item in policy["required_artifact_roles"])
    if set(observed) != required:
        raise BackupRecoveryError("ARTIFACT_ROLE_SET_NOT_EXACT")
    for role, (category, expected_sha, expected_size, schema_kind, _) in observed.items():
        role_policy = policy["artifact_roles"][role]
        expected = str(role_policy["classification"])
        fixed_sha = role_policy.get("fixed_expected_sha256")
        fixed_size = role_policy.get("fixed_expected_size_bytes")
        if (
            category != expected
            or schema_kind != role_policy.get("schema_kind")
            or (fixed_sha is not None and expected_sha != fixed_sha)
            or (fixed_size is not None and expected_size != fixed_size)
        ):
            raise BackupRecoveryError("ARTIFACT_CLASSIFICATION_MISMATCH")
    return observed


def _compiled_forbidden(
    policy: Mapping[str, Any]
) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]], list[re.Pattern[str]]]:
    try:
        names = [re.compile(str(item)) for item in policy["forbidden"]["basename_patterns"]]
        content = [re.compile(str(item)) for item in policy["forbidden"]["credential_value_patterns"]]
        synthetic = [
            re.compile(str(item))
            for item in policy["bundle_scan"]["allowed_synthetic_match_patterns"]
        ]
    except (re.error, KeyError, TypeError) as exc:
        raise BackupRecoveryError("POLICY_FORBIDDEN_PATTERN_INVALID") from exc
    return names, content, synthetic


def _scan_text_file(path: Path, patterns: Sequence[re.Pattern[str]]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    tail = ""
    try:
        before = os.fstat(descriptor)
        while True:
            block = os.read(descriptor, BUFFER_BYTES)
            if not block:
                break
            if b"\x00" in block:
                raise BackupRecoveryError("TEXT_AUTHORITY_CONTAINS_NUL")
            text = tail + block.decode("utf-8", errors="strict")
            if any(pattern.search(text) for pattern in patterns):
                raise BackupRecoveryError("CREDENTIAL_OR_PRIVATE_CLOUD_VALUE_DETECTED")
            tail = text[-TEXT_PATTERN_OVERLAP:]
        after = os.fstat(descriptor)
    except UnicodeDecodeError as exc:
        raise BackupRecoveryError("TEXT_AUTHORITY_NOT_UTF8") from exc
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise BackupRecoveryError("FILE_CHANGED_DURING_CREDENTIAL_SCAN")


def _strict_json_from_file(path: Path) -> Mapping[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = -1
            value = json.load(handle, object_pairs_hook=_pairs)
            after = os.fstat(handle.fileno())
    except BackupRecoveryError:
        raise
    except Exception as exc:
        raise BackupRecoveryError("AUTHORITY_JSON_SCHEMA_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or not isinstance(value, Mapping)
        or not value
    ):
        raise BackupRecoveryError("AUTHORITY_JSON_SCHEMA_INVALID")
    return value


def _validate_csv_schema(
    path: Path, schema_kind: str, parameters: Mapping[str, Any]
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    subject_ids: set[str] = set()
    study_ids: set[str] = set()
    physical_paths: set[str] = set()
    physical_keys: set[str] = set()
    row_count = 0
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = -1
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise BackupRecoveryError("AUTHORITY_CSV_EMPTY") from exc
            if (
                not header
                or any(not item for item in header)
                or len(header) != len(set(header))
            ):
                raise BackupRecoveryError("AUTHORITY_CSV_HEADER_INVALID")
            index = {name: position for position, name in enumerate(header)}
            required: set[str] = set()
            if schema_kind == "selected_study_manifest_v1":
                required = {"subject_id", "study_id"}
            elif schema_kind == "subject_split_map_v1":
                required = {"subject_id", "split"}
            elif schema_kind == "selected_source_manifest_v1":
                required = {
                    "subject_id", "study_id", "source_relative_path",
                    "source_object_key",
                }
            if not required.issubset(index):
                raise BackupRecoveryError("AUTHORITY_CSV_REQUIRED_COLUMN_MISSING")
            for row in reader:
                if len(row) != len(header):
                    raise BackupRecoveryError("AUTHORITY_CSV_ROW_WIDTH_INVALID")
                row_count += 1
                if schema_kind == "selected_study_manifest_v1":
                    subject = row[index["subject_id"]]
                    study = row[index["study_id"]]
                    if not subject or not study or subject in subject_ids or study in study_ids:
                        raise BackupRecoveryError("SELECTED_STUDY_SCHEMA_INVALID")
                    subject_ids.add(subject)
                    study_ids.add(study)
                elif schema_kind == "subject_split_map_v1":
                    subject = row[index["subject_id"]]
                    split = row[index["split"]]
                    if not subject or subject in subject_ids or split not in {"train", "val", "test"}:
                        raise BackupRecoveryError("SPLIT_MAP_SCHEMA_INVALID")
                    subject_ids.add(subject)
                elif schema_kind == "selected_source_manifest_v1":
                    subject = row[index["subject_id"]]
                    study = row[index["study_id"]]
                    locator = row[index["source_relative_path"]]
                    key = row[index["source_object_key"]]
                    if (
                        not subject or not study or not locator or not key
                        or locator in physical_paths or key in physical_keys
                    ):
                        raise BackupRecoveryError("SELECTED_SOURCE_SCHEMA_INVALID")
                    physical_paths.add(locator)
                    physical_keys.add(key)
            after = os.fstat(handle.fileno())
    except BackupRecoveryError:
        raise
    except (UnicodeDecodeError, csv.Error, OSError) as exc:
        raise BackupRecoveryError("AUTHORITY_CSV_SCHEMA_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or row_count <= 0
    ):
        raise BackupRecoveryError("AUTHORITY_CSV_SCHEMA_INVALID")
    if "expected_rows" in parameters and row_count != parameters["expected_rows"]:
        raise BackupRecoveryError("AUTHORITY_CSV_EXPECTED_ROW_COUNT_MISMATCH")


def _validate_source_metadata_jsonl(
    path: Path, parameters: Mapping[str, Any]
) -> None:
    required = {
        "release_id", "component", "subject_id", "study_id", "split",
        "source_relative_path", "gcs_uri", "source_object_key", "production_batch",
        "remote_size_bytes", "remote_md5_base64", "remote_crc32c_base64",
        "remote_generation", "remote_storage_class", "remote_updated",
        "preflight_status", "discrepancy_reasons",
    }
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    paths: set[str] = set()
    keys: set[str] = set()
    rows = 0
    source_bytes = 0
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = -1
            for line in handle:
                try:
                    value = json.loads(line, object_pairs_hook=_pairs)
                except BackupRecoveryError:
                    raise
                except Exception as exc:
                    raise BackupRecoveryError("SOURCE_METADATA_JSONL_INVALID") from exc
                if not isinstance(value, Mapping) or set(value) != required:
                    raise BackupRecoveryError("SOURCE_METADATA_JSONL_SCHEMA_NOT_CLOSED")
                locator = value["source_relative_path"]
                key = value["source_object_key"]
                size = value["remote_size_bytes"]
                if (
                    not isinstance(locator, str) or not locator
                    or not isinstance(key, str) or not SHA256_RE.fullmatch(key)
                    or locator in paths or key in keys
                    or not isinstance(size, int) or isinstance(size, bool) or size <= 0
                    or value["preflight_status"] != "PASS"
                    or value["discrepancy_reasons"] != []
                ):
                    raise BackupRecoveryError("SOURCE_METADATA_JSONL_SEMANTICS_INVALID")
                paths.add(locator)
                keys.add(key)
                rows += 1
                source_bytes += size
            after = os.fstat(handle.fileno())
    except BackupRecoveryError:
        raise
    except (UnicodeDecodeError, OSError) as exc:
        raise BackupRecoveryError("SOURCE_METADATA_JSONL_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or rows != parameters["expected_rows"]
        or source_bytes != parameters["expected_source_bytes"]
    ):
        raise BackupRecoveryError("SOURCE_METADATA_JSONL_AUTHORITY_MISMATCH")


def _validate_strict_jsonl_mapping(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    rows = 0
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = -1
            for line in handle:
                try:
                    value = json.loads(line, object_pairs_hook=_pairs)
                except BackupRecoveryError:
                    raise
                except Exception as exc:
                    raise BackupRecoveryError("AUTHORITY_JSONL_SCHEMA_INVALID") from exc
                if not isinstance(value, Mapping) or not value:
                    raise BackupRecoveryError("AUTHORITY_JSONL_SCHEMA_INVALID")
                rows += 1
            after = os.fstat(handle.fileno())
    except BackupRecoveryError:
        raise
    except (UnicodeDecodeError, OSError) as exc:
        raise BackupRecoveryError("AUTHORITY_JSONL_SCHEMA_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        rows <= 0 or before.st_dev != after.st_dev or before.st_ino != after.st_ino
        or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise BackupRecoveryError("AUTHORITY_JSONL_SCHEMA_INVALID")


def _validate_artifact_schema(path: Path, role_policy: Mapping[str, Any]) -> None:
    schema_kind = str(role_policy["schema_kind"])
    parameters = role_policy["schema_parameters"]
    if schema_kind == "echoprime_checkpoint_v1":
        return
    if schema_kind in {
        "selected_study_manifest_v1", "selected_source_manifest_v1",
        "subject_split_map_v1",
    }:
        _validate_csv_schema(path, schema_kind, parameters)
        return
    if schema_kind == "selected_source_metadata_receipt_v1":
        _validate_source_metadata_jsonl(path, parameters)
        return
    if schema_kind == "strict_json_mapping_v1":
        _strict_json_from_file(path)
        return
    if schema_kind == "strict_jsonl_mapping_v1":
        _validate_strict_jsonl_mapping(path)
        return
    if schema_kind == "strict_json_or_csv_v1":
        if path.suffix.lower() == ".json":
            _strict_json_from_file(path)
        elif path.suffix.lower() == ".csv":
            _validate_csv_schema(path, schema_kind, parameters)
        else:
            raise BackupRecoveryError("AUTHORITY_SCHEMA_SUFFIX_INVALID")
        return
    raise BackupRecoveryError("AUTHORITY_SCHEMA_KIND_UNSUPPORTED")


def _copy_regular_atomic(source: Path, destination: Path) -> tuple[int, str, int]:
    _require_source_authority_file(source, "SOURCE_AUTHORITY")
    _require_private_directory(destination.parent, "DESTINATION_PARENT")
    if destination.exists() or destination.is_symlink():
        raise BackupRecoveryError("DESTINATION_COLLISION")
    temporary = destination.parent / (
        f".{destination.name}.partial.{os.getpid()}.{secrets.token_hex(8)}"
    )
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        before = os.fstat(source_fd)
        digest = hashlib.sha256()
        copied = 0
        while True:
            block = os.read(source_fd, BUFFER_BYTES)
            if not block:
                break
            digest.update(block)
            copied += len(block)
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        output_metadata = os.fstat(destination_fd)
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    try:
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or copied != before.st_size
            or output_metadata.st_size != copied
            or stat.S_IMODE(output_metadata.st_mode) != 0o600
        ):
            raise BackupRecoveryError("COPY_SOURCE_OR_DESTINATION_CHANGED")
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    observed_size, observed_sha, observed_mode = _sha256_file_nofollow(destination)
    if observed_size != copied or observed_sha != digest.hexdigest() or observed_mode != 0o600:
        raise BackupRecoveryError("COPIED_FILE_VERIFICATION_FAILED")
    return copied, observed_sha, observed_mode


def _walk_no_symlinks(root: Path) -> Iterator[Path]:
    root_device = os.lstat(root).st_dev
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        current_metadata = os.lstat(current_path)
        if (
            stat.S_ISLNK(current_metadata.st_mode)
            or not stat.S_ISDIR(current_metadata.st_mode)
            or current_metadata.st_dev != root_device
        ):
            raise BackupRecoveryError("RESTORE_TREE_DIRECTORY_INVALID")
        for name in sorted(directories):
            candidate = current_path / name
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise BackupRecoveryError("RESTORE_TREE_SYMLINK_OR_SPECIAL")
        for name in sorted(files):
            candidate = current_path / name
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise BackupRecoveryError("RESTORE_TREE_SYMLINK_OR_SPECIAL")
            yield candidate


def _verify_private_tree(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        metadata = os.lstat(current_path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise BackupRecoveryError("RESTORE_TREE_DIRECTORY_PERMISSION_INVALID")
        for name in [*directories, *files]:
            candidate = current_path / name
            item = os.lstat(candidate)
            if (
                stat.S_ISLNK(item.st_mode)
                or (not stat.S_ISDIR(item.st_mode) and not stat.S_ISREG(item.st_mode))
                or item.st_uid != os.geteuid()
                or stat.S_IMODE(item.st_mode) & 0o077
            ):
                raise BackupRecoveryError("RESTORE_TREE_ITEM_PERMISSION_INVALID")


def _normalize_isolated_restore_permissions(root: Path) -> None:
    """Remove group/other access inside a newly created restore fixture only."""
    paths = list(_walk_no_symlinks(root))
    directories: list[Path] = []
    for current, names, _ in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        for name in names:
            candidate = current_path / name
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise BackupRecoveryError("RESTORE_TREE_SYMLINK_OR_SPECIAL")
    for path in paths:
        metadata = os.lstat(path)
        mode = 0o700 if stat.S_IMODE(metadata.st_mode) & 0o100 else 0o600
        os.chmod(path, mode, follow_symlinks=False)
    for path in reversed(directories):
        existing = stat.S_IMODE(os.lstat(path).st_mode)
        target = 0o2700 if existing & stat.S_ISGID else 0o700
        os.chmod(path, target, follow_symlinks=False)


def _tracked_tree_authority(checkout: Path) -> tuple[int, str]:
    names = [item for item in _git(checkout, "ls-files", "-z").split("\x00") if item]
    rows: list[tuple[str, int, str]] = []
    for name in names:
        relative = _safe_relative_path(name, "TRACKED_PATH_UNSAFE")
        path = checkout / relative
        metadata = _require_regular_file(path, "TRACKED_FILE")
        size, digest, _ = _sha256_file_nofollow(path)
        rows.append((relative, size, digest))
        if stat.S_ISLNK(metadata.st_mode):
            raise BackupRecoveryError("TRACKED_SYMLINK_FORBIDDEN")
    return len(rows), canonical_json_sha256(rows)


def _create_and_restore_git_bundle(
    *, checkout: Path, backup_root: Path, restore_root: Path,
    branch: str, commit: str, bundle_relative: str, maximum_bytes: int,
    name_patterns: Sequence[re.Pattern[str]],
    content_patterns: Sequence[re.Pattern[str]],
    allowed_synthetic: Sequence[re.Pattern[str]],
    policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    bundle_relative = _safe_relative_path(bundle_relative)
    bundle_path = backup_root / bundle_relative
    _ensure_private_relative_directories(backup_root, PurePosixPath(bundle_relative).parent)
    if bundle_path.exists() or bundle_path.is_symlink():
        raise BackupRecoveryError("GIT_BUNDLE_COLLISION")
    with _private_umask():
        _git(checkout, "bundle", "create", str(bundle_path), f"refs/heads/{branch}")
    os.chmod(bundle_path, 0o600, follow_symlinks=False)
    bundle_size, bundle_sha, bundle_mode = _sha256_file_nofollow(bundle_path)
    if bundle_size <= 0 or bundle_size > maximum_bytes or bundle_mode != 0o600:
        raise BackupRecoveryError("GIT_BUNDLE_BOUND_OR_MODE_INVALID")
    _git(checkout, "bundle", "verify", str(bundle_path))

    git_root = restore_root / "git"
    os.mkdir(git_root, 0o700)
    common = git_root / "common.git"
    worktree = git_root / "worktree"
    environment = _git_environment()
    template = git_root / "empty_template"
    os.mkdir(template, 0o700)
    with _private_umask():
        cloned = subprocess.run(
            [
                "git", "clone", "--bare", f"--template={template}",
                str(bundle_path), str(common),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=environment, check=False,
        )
        if cloned.returncode != 0:
            raise BackupRecoveryError("GIT_BUNDLE_BARE_RESTORE_FAILED")
        _git_dir(common, "worktree", "add", str(worktree), branch)
    if (
        _git(worktree, "rev-parse", "HEAD") != commit
        or _git(worktree, "branch", "--show-current") != branch
        or _git(worktree, "status", "--porcelain", "--untracked-files=no")
    ):
        raise BackupRecoveryError("RESTORED_WORKTREE_AUTHORITY_MISMATCH")
    _git_dir(common, "fsck", "--full", "--strict")
    tracked_count, tracked_set_sha = _tracked_tree_authority(worktree)
    bundle_scan = _scan_full_reachable_bundle(
        git_dir=common, branch=branch, name_patterns=name_patterns,
        content_patterns=content_patterns, allowed_synthetic=allowed_synthetic,
        policy=policy,
    )
    _normalize_isolated_restore_permissions(git_root)
    with _private_umask():
        _git_dir(common, "fsck", "--full", "--strict")
        if (
            _git(worktree, "rev-parse", "HEAD") != commit
            or _git(worktree, "branch", "--show-current") != branch
            or _git(worktree, "status", "--porcelain", "--untracked-files=no")
        ):
            raise BackupRecoveryError("RESTORED_WORKTREE_POST_NORMALIZATION_MISMATCH")
        post_count, post_set_sha = _tracked_tree_authority(worktree)
    _normalize_isolated_restore_permissions(git_root)
    _verify_private_tree(git_root)
    if post_count != tracked_count or post_set_sha != tracked_set_sha:
        raise BackupRecoveryError("RESTORED_TRACKED_TREE_POST_NORMALIZATION_MISMATCH")
    return {
        "bundle_relative_path": bundle_relative,
        "bundle_size_bytes": bundle_size,
        "bundle_sha256": bundle_sha,
        "git_fsck_passed": True,
        "linked_worktree_recreated": True,
        "exact_git_commit_restored": True,
        "tracked_file_count": tracked_count,
        "tracked_file_set_sha256": tracked_set_sha,
        "post_normalization_git_fsck_passed": True,
        "post_normalization_tracked_file_set_sha256": post_set_sha,
        **bundle_scan,
    }


MANIFEST_KEYS = {
    "schema_version", "artifact_type", "status", "attempt_id", "created_at_utc",
    "governing_commit", "policy_sha256", "selection_sha256",
    "recovery_documentation_sha256", "classification_counts", "declarations",
    "artifacts", "git_bundle", "declared_item_count", "copied_file_count",
    "copied_bytes", "checksum_only_file_count", "unresolved_item_count",
    "credential_material_included", "private_project_or_billing_material_included",
    "approved_restricted_control_authorities_only",
    "unapproved_bulk_scientific_payload_included", "symlinks_included", "special_files_included",
    "live_git_mutated", "full_c3_authorized",
}
MANIFEST_ARTIFACT_KEYS = {
    "role", "classification", "copied", "backup_relative_path", "size_bytes",
    "sha256", "expected_size_bytes", "expected_sha256", "schema_kind",
    "source_locator_sha256", "source_mode", "source_authority_safe_mode",
    "authority_identity_verified", "schema_verified", "credential_scan_passed",
    "approved_restricted_control_authority",
    "unapproved_bulk_scientific_payload_absent",
}
GIT_BUNDLE_KEYS = {
    "backup_relative_path", "size_bytes", "sha256", "reachable_commit_count",
    "historical_path_record_count", "reachable_unique_blob_count",
    "reachable_blob_bytes", "allowed_synthetic_fixture_match_count",
    "full_reachable_credential_scan_passed",
    "full_reachable_private_cloud_value_scan_passed",
    "historical_path_scan_passed", "post_normalization_git_fsck_passed",
    "post_normalization_tracked_file_set_sha256",
}
RESTORE_KEYS = {
    "schema_version", "artifact_type", "status", "attempt_id", "created_at_utc",
    "governing_commit", "policy_sha256", "backup_manifest_sha256",
    "git_bundle_sha256", "exact_git_commit_restored", "git_fsck_passed",
    "linked_worktree_recreated", "tracked_file_count", "tracked_file_set_sha256",
    "restored_file_count", "restored_bytes", "manifest_restore_checksum_equality",
    "owner_private_permissions_passed", "no_symlinks", "no_special_files",
    "credential_material_absent", "private_project_or_billing_material_absent",
    "approved_restricted_control_authorities_only",
    "unapproved_bulk_scientific_payload_absent",
    "git_bundle_full_reachable_scan_passed", "historical_path_scan_passed",
    "post_normalization_git_fsck_passed",
    "post_normalization_tracked_file_set_sha256",
    "live_git_unchanged", "restore_test_passed",
    "full_c3_authorized",
}
AGGREGATE_KEYS = {
    "schema_version", "artifact_type", "status", "attempt_id", "governing_commit",
    "policy_sha256", "selection_sha256", "backup_manifest_sha256",
    "restore_receipt_sha256", "recovery_documentation_sha256",
    "classification_counts", "declared_item_count", "copied_file_count",
    "copied_bytes", "checksum_only_file_count", "git_bundle_bytes",
    "git_bundle_sha256", "exact_git_commit_restored", "git_fsck_passed",
    "linked_worktree_recreated", "tracked_file_count", "tracked_file_set_sha256",
    "restored_file_count", "restored_bytes", "manifest_restore_checksum_equality",
    "owner_private_permissions_passed", "no_symlinks", "no_special_files",
    "credential_material_absent", "private_project_or_billing_material_absent",
    "approved_restricted_control_authorities_only",
    "unapproved_bulk_scientific_payload_absent", "excluded_credential_item_count",
    "git_bundle_reachable_commit_count", "git_bundle_historical_path_record_count",
    "git_bundle_reachable_unique_blob_count", "git_bundle_reachable_blob_bytes",
    "git_bundle_allowed_synthetic_fixture_match_count",
    "git_bundle_full_reachable_scan_passed", "historical_path_scan_passed",
    "post_normalization_git_fsck_passed",
    "post_normalization_tracked_file_set_sha256",
    "unresolved_item_count", "live_git_unchanged", "backup_verified",
    "restore_test_passed", "cloud_requests", "object_listing_repeated",
    "scheduler_jobs_submitted", "dicom_bodies_downloaded", "real_dicom_extraction",
    "echoprime_inference", "model_fitting", "confirmatory_performance_accessed",
    "full_c3_authorized",
}


def _validate_sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise BackupRecoveryError(code)
    return value


def validate_backup_manifest(value: Mapping[str, Any]) -> None:
    if set(value) != MANIFEST_KEYS:
        raise BackupRecoveryError("BACKUP_MANIFEST_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type") != MANIFEST_TYPE
        or value.get("status") != MANIFEST_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
    ):
        raise BackupRecoveryError("BACKUP_MANIFEST_IDENTITY_INVALID")
    for key in ("policy_sha256", "selection_sha256", "recovery_documentation_sha256"):
        _validate_sha(value.get(key), "BACKUP_MANIFEST_SHA_INVALID")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise BackupRecoveryError("BACKUP_MANIFEST_ARTIFACTS_INVALID")
    if any(not isinstance(row, Mapping) or set(row) != MANIFEST_ARTIFACT_KEYS for row in artifacts):
        raise BackupRecoveryError("BACKUP_MANIFEST_ARTIFACT_SCHEMA_NOT_CLOSED")
    if len({row["role"] for row in artifacts}) != len(artifacts):
        raise BackupRecoveryError("BACKUP_MANIFEST_DUPLICATE_ROLE")
    for row in artifacts:
        if (
            row.get("expected_sha256") != row.get("sha256")
            or row.get("expected_size_bytes") != row.get("size_bytes")
            or not ROLE_RE.fullmatch(str(row.get("schema_kind", "")))
            or any(
                row.get(key) is not True for key in (
                    "source_authority_safe_mode", "authority_identity_verified",
                    "schema_verified", "credential_scan_passed",
                    "approved_restricted_control_authority",
                    "unapproved_bulk_scientific_payload_absent",
                )
            )
        ):
            raise BackupRecoveryError("BACKUP_MANIFEST_ARTIFACT_AUTHORITY_INVALID")
    bundle = value.get("git_bundle")
    if (
        not isinstance(bundle, Mapping) or set(bundle) != GIT_BUNDLE_KEYS
        or not SHA256_RE.fullmatch(str(bundle.get("sha256", "")))
        or not SHA256_RE.fullmatch(
            str(bundle.get("post_normalization_tracked_file_set_sha256", ""))
        )
        or any(
            not isinstance(bundle.get(key), int) or isinstance(bundle.get(key), bool)
            or bundle[key] < 0
            for key in (
                "size_bytes", "reachable_commit_count", "historical_path_record_count",
                "reachable_unique_blob_count", "reachable_blob_bytes",
                "allowed_synthetic_fixture_match_count",
            )
        )
        or bundle["size_bytes"] <= 0 or bundle["reachable_commit_count"] <= 0
        or any(
            bundle.get(key) is not True for key in (
                "full_reachable_credential_scan_passed",
                "full_reachable_private_cloud_value_scan_passed",
                "historical_path_scan_passed", "post_normalization_git_fsck_passed",
            )
        )
    ):
        raise BackupRecoveryError("BACKUP_MANIFEST_GIT_BUNDLE_AUTHORITY_INVALID")
    if any(value.get(key) is not False for key in (
        "credential_material_included", "private_project_or_billing_material_included",
        "unapproved_bulk_scientific_payload_included", "symlinks_included", "special_files_included",
        "live_git_mutated", "full_c3_authorized",
    )) or value.get("approved_restricted_control_authorities_only") is not True:
        raise BackupRecoveryError("BACKUP_MANIFEST_SAFETY_ATTESTATION_INVALID")


def validate_restore_receipt(value: Mapping[str, Any]) -> None:
    if set(value) != RESTORE_KEYS:
        raise BackupRecoveryError("RESTORE_RECEIPT_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type") != RESTORE_TYPE
        or value.get("status") != RESTORE_STATUS
        or value.get("restore_test_passed") is not True
        or value.get("full_c3_authorized") is not False
    ):
        raise BackupRecoveryError("RESTORE_RECEIPT_SEMANTICS_INVALID")
    for key in (
        "policy_sha256", "backup_manifest_sha256", "git_bundle_sha256",
        "tracked_file_set_sha256", "post_normalization_tracked_file_set_sha256",
    ):
        _validate_sha(value.get(key), "RESTORE_RECEIPT_SHA_INVALID")
    required_true = {
        "exact_git_commit_restored", "git_fsck_passed", "linked_worktree_recreated",
        "manifest_restore_checksum_equality", "owner_private_permissions_passed",
        "no_symlinks", "no_special_files", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "approved_restricted_control_authorities_only",
        "unapproved_bulk_scientific_payload_absent",
        "git_bundle_full_reachable_scan_passed", "historical_path_scan_passed",
        "post_normalization_git_fsck_passed",
        "live_git_unchanged", "restore_test_passed",
    }
    if any(value.get(key) is not True for key in required_true):
        raise BackupRecoveryError("RESTORE_RECEIPT_GATE_NOT_PASS")


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    """Validate the closed Git-safe Phase 1E-F backup/recovery aggregate."""
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        raise BackupRecoveryError("BACKUP_AGGREGATE_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type") != AGGREGATE_TYPE
        or value.get("status") != AGGREGATE_STATUS
        or not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
    ):
        raise BackupRecoveryError("BACKUP_AGGREGATE_IDENTITY_INVALID")
    for key in (
        "policy_sha256", "selection_sha256", "backup_manifest_sha256",
        "restore_receipt_sha256", "recovery_documentation_sha256",
        "git_bundle_sha256", "tracked_file_set_sha256",
        "post_normalization_tracked_file_set_sha256",
    ):
        _validate_sha(value.get(key), "BACKUP_AGGREGATE_SHA_INVALID")
    counts = value.get("classification_counts")
    expected_categories = {
        "GIT_ORIGIN_PROTECTED", "COMMITTED_RECONSTRUCTABLE",
        "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE", "OWNER_RECREATABLE",
        "CHECKSUM_ONLY_NO_COPY_REQUIRED", "IRREPLACEABLE_BACKUP_REQUIRED",
        "EXCLUDED_CREDENTIAL_MATERIAL", "UNRESOLVED",
    }
    if (
        not isinstance(counts, Mapping)
        or set(counts) != expected_categories
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in counts.values())
        or sum(counts.values()) != value.get("declared_item_count")
        or counts["UNRESOLVED"] != 0
    ):
        raise BackupRecoveryError("BACKUP_AGGREGATE_CLASSIFICATION_COUNTS_INVALID")
    true_keys = {
        "exact_git_commit_restored", "git_fsck_passed", "linked_worktree_recreated",
        "manifest_restore_checksum_equality", "owner_private_permissions_passed",
        "no_symlinks", "no_special_files", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "approved_restricted_control_authorities_only",
        "unapproved_bulk_scientific_payload_absent",
        "git_bundle_full_reachable_scan_passed", "historical_path_scan_passed",
        "post_normalization_git_fsck_passed",
        "live_git_unchanged", "backup_verified", "restore_test_passed",
    }
    if any(value.get(key) is not True for key in true_keys):
        raise BackupRecoveryError("BACKUP_AGGREGATE_GATE_NOT_PASS")
    for key in (
        "git_bundle_reachable_commit_count", "git_bundle_historical_path_record_count",
        "git_bundle_reachable_unique_blob_count", "git_bundle_reachable_blob_bytes",
        "git_bundle_allowed_synthetic_fixture_match_count",
    ):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value[key] < 0:
            raise BackupRecoveryError("BACKUP_AGGREGATE_BUNDLE_COUNT_INVALID")
    if value["git_bundle_reachable_commit_count"] <= 0:
        raise BackupRecoveryError("BACKUP_AGGREGATE_BUNDLE_COUNT_INVALID")
    expected_boundary = {
        "cloud_requests": 0, "object_listing_repeated": False,
        "scheduler_jobs_submitted": 0, "dicom_bodies_downloaded": 0,
        "real_dicom_extraction": False, "echoprime_inference": False,
        "model_fitting": False, "confirmatory_performance_accessed": False,
        "full_c3_authorized": False, "unresolved_item_count": 0,
    }
    if any(value.get(key) != expected for key, expected in expected_boundary.items()):
        raise BackupRecoveryError("BACKUP_AGGREGATE_EXECUTION_BOUNDARY_INVALID")


def validate_live_backup_root(
    backup_root: Path, *, attempt_id: str, governing_commit: str,
    expected_manifest_binding: Mapping[str, Any],
    expected_restore_binding: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Revalidate the exact backed primary pack immediately before launch.

    This intentionally performs no restore, checkout, mutation, or network
    operation.  It proves that the manifest, receipt, Git bundle, and every
    copied control authority still exist with the original byte identities,
    owner-private modes, and exact no-symlink file set.
    """
    _require_private_directory(backup_root, "LIVE_BACKUP_ROOT")
    manifest_path = backup_root / "backup_manifest.restricted.json"
    restore_path = backup_root / "restore_receipt.restricted.json"
    manifest_size, manifest_sha, manifest_mode = _sha256_file_nofollow(manifest_path)
    restore_size, restore_sha, restore_mode = _sha256_file_nofollow(restore_path)
    if (
        manifest_mode != 0o600 or restore_mode != 0o600
        or expected_manifest_binding
        != {"size_bytes": manifest_size, "sha256": manifest_sha}
        or expected_restore_binding
        != {"size_bytes": restore_size, "sha256": restore_sha}
    ):
        raise BackupRecoveryError("LIVE_BACKUP_PRIMARY_BINDING_MISMATCH")
    manifest = _strict_json_from_file(manifest_path)
    restore = _strict_json_from_file(restore_path)
    validate_backup_manifest(manifest)
    validate_restore_receipt(restore)
    if (
        manifest.get("attempt_id") != attempt_id
        or restore.get("attempt_id") != attempt_id
        or manifest.get("governing_commit") != governing_commit
        or restore.get("governing_commit") != governing_commit
        or restore.get("backup_manifest_sha256") != manifest_sha
        or restore.get("git_bundle_sha256") != manifest["git_bundle"]["sha256"]
    ):
        raise BackupRecoveryError("LIVE_BACKUP_PRIMARY_IDENTITY_MISMATCH")
    expected_files = {
        "backup_manifest.restricted.json",
        "restore_receipt.restricted.json",
        str(manifest["git_bundle"]["backup_relative_path"]),
    }
    artifact_by_relative = {}
    for row in manifest["artifacts"]:
        if row.get("copied") is not True:
            continue
        relative = str(row["backup_relative_path"])
        expected_files.add(relative)
        artifact_by_relative[relative] = row
    observed_files = {
        path.relative_to(backup_root).as_posix()
        for path in _walk_no_symlinks(backup_root)
    }
    if observed_files != expected_files:
        raise BackupRecoveryError("LIVE_BACKUP_PRIMARY_FILE_SET_MISMATCH")
    for relative, row in artifact_by_relative.items():
        size, digest, mode = _sha256_file_nofollow(backup_root / relative)
        if size != row["size_bytes"] or digest != row["sha256"] or mode != 0o600:
            raise BackupRecoveryError("LIVE_BACKUP_PRIMARY_ARTIFACT_MISMATCH")
    bundle = manifest["git_bundle"]
    bundle_size, bundle_sha, bundle_mode = _sha256_file_nofollow(
        backup_root / str(bundle["backup_relative_path"])
    )
    if (
        bundle_size != bundle["size_bytes"] or bundle_sha != bundle["sha256"]
        or bundle_mode != 0o600
    ):
        raise BackupRecoveryError("LIVE_BACKUP_PRIMARY_BUNDLE_MISMATCH")
    _verify_private_tree(backup_root)
    return {
        "manifest_sha256": manifest_sha,
        "restore_receipt_sha256": restore_sha,
        "copied_file_count": len(artifact_by_relative),
        "exact_file_set_verified": True,
    }


def _artifact_safety(
    *, role: str, source: Path, role_policy: Mapping[str, Any], policy: Mapping[str, Any],
    name_patterns: Sequence[re.Pattern[str]], content_patterns: Sequence[re.Pattern[str]],
) -> None:
    # Two frozen, already reviewed aggregate authorities contain the word
    # ``preflight`` in their source basenames.  They are safe only through the
    # exact prior_safe role allowlist plus strict JSON/CSV and content scans;
    # the broad credential/preflight basename rule still blocks every other
    # role (including any environment, database, or credential candidate).
    reviewed_prior_aggregate_name = (
        role.startswith("prior_safe_aggregate_")
        and role_policy.get("schema_kind") == "strict_json_or_csv_v1"
    )
    if (
        any(pattern.fullmatch(source.name) for pattern in name_patterns)
        and not reviewed_prior_aggregate_name
    ):
        raise BackupRecoveryError("FORBIDDEN_AUTHORITY_BASENAME")
    suffix = source.suffix.lower()
    permitted = {str(item).lower() for item in role_policy["permitted_suffixes"]}
    if suffix not in permitted:
        raise BackupRecoveryError("AUTHORITY_SUFFIX_NOT_ALLOWLISTED_FOR_ROLE")
    forbidden_suffixes = {str(item).lower() for item in policy["forbidden"]["suffixes"]}
    if suffix in forbidden_suffixes:
        raise BackupRecoveryError("SCIENTIFIC_DATA_OR_LOG_SUFFIX_FORBIDDEN")
    if suffix in {".pt", ".pth", ".ckpt"} and role != "checkpoint":
        raise BackupRecoveryError("MODEL_BINARY_ROLE_FORBIDDEN")
    if role_policy.get("binary_payload_permitted") is not True:
        _scan_text_file(source, content_patterns)


def _verify_manifest_file_set(
    root: Path, artifacts: Sequence[Mapping[str, Any]], *, scan_patterns: Sequence[re.Pattern[str]],
) -> tuple[int, int]:
    expected = {
        str(row["backup_relative_path"]): row
        for row in artifacts if row.get("copied") is True
    }
    observed: set[str] = set()
    total = 0
    files_root = root / "files"
    for path in _walk_no_symlinks(files_root):
        relative = path.relative_to(root).as_posix()
        row = expected.get(relative)
        if row is None:
            raise BackupRecoveryError("RESTORE_CONTAINS_UNLISTED_FILE")
        size, digest, mode = _sha256_file_nofollow(path)
        if size != row["size_bytes"] or digest != row["sha256"] or mode != 0o600:
            raise BackupRecoveryError("RESTORE_FILE_MANIFEST_MISMATCH")
        if row["role"] != "checkpoint":
            _scan_text_file(path, scan_patterns)
        observed.add(relative)
        total += size
    if observed != set(expected):
        raise BackupRecoveryError("RESTORE_FILE_SET_MISMATCH")
    return len(observed), total


def execute(
    *, policy_path: Path, attempt_id: str, governing_commit: str, checkout: Path,
    declarations_raw: Sequence[str], artifacts_raw: Sequence[str], backup_root: Path,
    restore_root: Path, aggregate_output: Path,
) -> Mapping[str, Any]:
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise BackupRecoveryError("ATTEMPT_ID_INVALID")
    if not COMMIT_RE.fullmatch(governing_commit):
        raise BackupRecoveryError("GOVERNING_COMMIT_INVALID")
    policy, policy_sha = _load_policy(policy_path)
    declarations = _parse_declarations(declarations_raw, policy)
    artifacts = _parse_artifacts(artifacts_raw, policy)
    if len(declarations) + len(artifacts) > int(policy["bounds"]["maximum_declared_items"]):
        raise BackupRecoveryError("DECLARED_ITEM_BOUND_EXCEEDED")
    categories = [*declarations.values(), *(item[0] for item in artifacts.values())]
    if policy["classifications"]["unresolved_classification"] in categories:
        raise BackupRecoveryError("UNRESOLVED_CONTROL_AUTHORITY_BLOCKS_BACKUP")
    classification_counts = Counter(categories)
    for category in policy["classifications"]["allowed"]:
        classification_counts.setdefault(str(category), 0)

    branch = str(policy["git"]["required_branch"])
    origin = str(policy["git"]["required_origin_remote"])
    approved_origin = re.compile(str(policy["git"]["approved_origin_url_regex"]))
    before_git = _git_snapshot(
        checkout, branch=branch, commit=governing_commit,
        origin=origin, approved_origin=approved_origin,
    )
    source_roots = [Path(str(item)) for item in policy["roots"]["approved_source_roots"]]
    backup_prefix = Path(str(policy["roots"]["approved_backup_root_prefix"]))
    restore_prefix = Path(str(policy["roots"]["approved_restore_root_prefix"]))
    if _is_relative_to(backup_root.absolute(), restore_root.absolute()) or _is_relative_to(
        restore_root.absolute(), backup_root.absolute()
    ):
        raise BackupRecoveryError("BACKUP_AND_RESTORE_ROOTS_OVERLAP")
    if _is_relative_to(restore_root.absolute(), checkout.absolute()) or _is_relative_to(
        checkout.absolute(), restore_root.absolute()
    ):
        raise BackupRecoveryError("RESTORE_AND_LIVE_CHECKOUT_OVERLAP")
    name_patterns, content_patterns, allowed_synthetic = _compiled_forbidden(policy)
    validated_sources: dict[str, tuple[int, str, int]] = {}
    for role, (_, expected_sha, expected_size, _, source) in sorted(artifacts.items()):
        role_policy = policy["artifact_roles"][role]
        _require_under_any_root(source, source_roots, "SOURCE_OUTSIDE_APPROVED_ROOTS")
        source_metadata = _require_source_authority_file(source, "SOURCE_AUTHORITY")
        if source_metadata.st_size > int(policy["bounds"]["maximum_single_file_bytes"]):
            raise BackupRecoveryError("SOURCE_FILE_BOUND_EXCEEDED")
        observed_size, observed_sha, observed_mode = _sha256_file_nofollow(source)
        if observed_size != expected_size or observed_sha != expected_sha:
            raise BackupRecoveryError("SOURCE_EXPECTED_AUTHORITY_MISMATCH")
        _artifact_safety(
            role=role, source=source, role_policy=role_policy, policy=policy,
            name_patterns=name_patterns, content_patterns=content_patterns,
        )
        _validate_artifact_schema(source, role_policy)
        after_size, after_sha, after_mode = _sha256_file_nofollow(source)
        if (
            (after_size, after_sha, after_mode)
            != (observed_size, observed_sha, observed_mode)
        ):
            raise BackupRecoveryError("SOURCE_CHANGED_DURING_PREFLIGHT")
        validated_sources[role] = (observed_size, observed_sha, observed_mode)

    _create_private_root(backup_root, approved_prefix=backup_prefix, code="BACKUP_ROOT")
    _create_private_root(restore_root, approved_prefix=restore_prefix, code="RESTORE_ROOT")

    copied_count = 0
    copied_bytes = 0
    checksum_only_count = 0
    records: list[dict[str, Any]] = []
    selection_rows: list[Mapping[str, Any]] = [
        {"role": role, "classification": category, "source_locator_sha256": None}
        for role, category in sorted(declarations.items())
    ]
    for role, (category, expected_sha, expected_size, schema_kind, source) in sorted(artifacts.items()):
        role_policy = policy["artifact_roles"][role]
        expected_observed = validated_sources[role]
        copied = category == policy["classifications"]["copied_classification"]
        relative_value = role_policy.get("backup_relative_path")
        if copied:
            if not isinstance(relative_value, str):
                raise BackupRecoveryError("COPIED_ROLE_BACKUP_PATH_MISSING")
            relative = _safe_relative_path(relative_value)
            destination = backup_root / relative
            _ensure_private_relative_directories(backup_root, PurePosixPath(relative).parent)
            size, digest, _ = _copy_regular_atomic(source, destination)
            if (size, digest) != expected_observed[:2]:
                raise BackupRecoveryError("SOURCE_CHANGED_AFTER_PREFLIGHT")
            mode = expected_observed[2]
            copied_count += 1
            copied_bytes += size
        else:
            if category != "CHECKSUM_ONLY_NO_COPY_REQUIRED" or relative_value is not None:
                raise BackupRecoveryError("NONCOPIED_ARTIFACT_CLASSIFICATION_INVALID")
            size, digest, mode = _sha256_file_nofollow(source)
            if (size, digest, mode) != expected_observed:
                raise BackupRecoveryError("SOURCE_CHANGED_AFTER_PREFLIGHT")
            relative = None
            checksum_only_count += 1
        records.append(
            {
                "role": role,
                "classification": category,
                "copied": copied,
                "backup_relative_path": relative,
                "size_bytes": size,
                "sha256": digest,
                "expected_size_bytes": expected_size,
                "expected_sha256": expected_sha,
                "schema_kind": schema_kind,
                "source_locator_sha256": hashlib.sha256(str(source.absolute()).encode()).hexdigest(),
                "source_mode": format(mode, "04o"),
                "source_authority_safe_mode": True,
                "authority_identity_verified": True,
                "schema_verified": True,
                "credential_scan_passed": True,
                "approved_restricted_control_authority": True,
                "unapproved_bulk_scientific_payload_absent": True,
            }
        )
        selection_rows.append(
            {
                "role": role,
                "classification": category,
                "expected_size_bytes": expected_size,
                "expected_sha256": expected_sha,
                "schema_kind": schema_kind,
                "source_locator_sha256": hashlib.sha256(str(source.absolute()).encode()).hexdigest(),
            }
        )
    if copied_count > int(policy["bounds"]["maximum_copied_files"]):
        raise BackupRecoveryError("COPIED_FILE_COUNT_BOUND_EXCEEDED")
    if copied_bytes > int(policy["bounds"]["maximum_total_copied_bytes"]):
        raise BackupRecoveryError("COPIED_BYTE_BOUND_EXCEEDED")

    documentation = checkout / str(policy["git"]["recovery_documentation_relative_path"])
    documentation_sha = sha256_file(documentation)
    current_tracked_count, current_tracked_sha = _tracked_tree_authority(checkout)
    git_result = _create_and_restore_git_bundle(
        checkout=checkout, backup_root=backup_root, restore_root=restore_root,
        branch=branch, commit=governing_commit,
        bundle_relative=str(policy["git"]["bundle_relative_path"]),
        maximum_bytes=int(policy["git"]["maximum_bundle_bytes"]),
        name_patterns=name_patterns, content_patterns=content_patterns,
        allowed_synthetic=allowed_synthetic, policy=policy,
    )
    if (
        git_result["tracked_file_count"] != current_tracked_count
        or git_result["tracked_file_set_sha256"] != current_tracked_sha
    ):
        raise BackupRecoveryError("RESTORED_TRACKED_TREE_MISMATCH")

    selection_sha = canonical_json_sha256(sorted(selection_rows, key=lambda row: row["role"]))
    manifest: Mapping[str, Any] = {
        "schema_version": 1,
        "artifact_type": MANIFEST_TYPE,
        "status": MANIFEST_STATUS,
        "attempt_id": attempt_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "governing_commit": governing_commit,
        "policy_sha256": policy_sha,
        "selection_sha256": selection_sha,
        "recovery_documentation_sha256": documentation_sha,
        "classification_counts": dict(sorted(classification_counts.items())),
        "declarations": [
            {"role": role, "classification": category}
            for role, category in sorted(declarations.items())
        ],
        "artifacts": records,
        "git_bundle": {
            "backup_relative_path": git_result["bundle_relative_path"],
            "size_bytes": git_result["bundle_size_bytes"],
            "sha256": git_result["bundle_sha256"],
            "reachable_commit_count": git_result["reachable_commit_count"],
            "historical_path_record_count": git_result["historical_path_record_count"],
            "reachable_unique_blob_count": git_result["reachable_unique_blob_count"],
            "reachable_blob_bytes": git_result["reachable_blob_bytes"],
            "allowed_synthetic_fixture_match_count": git_result[
                "allowed_synthetic_fixture_match_count"
            ],
            "full_reachable_credential_scan_passed": git_result[
                "full_reachable_credential_scan_passed"
            ],
            "full_reachable_private_cloud_value_scan_passed": git_result[
                "full_reachable_private_cloud_value_scan_passed"
            ],
            "historical_path_scan_passed": git_result["historical_path_scan_passed"],
            "post_normalization_git_fsck_passed": git_result[
                "post_normalization_git_fsck_passed"
            ],
            "post_normalization_tracked_file_set_sha256": git_result[
                "post_normalization_tracked_file_set_sha256"
            ],
        },
        "declared_item_count": len(categories),
        "copied_file_count": copied_count,
        "copied_bytes": copied_bytes,
        "checksum_only_file_count": checksum_only_count,
        "unresolved_item_count": classification_counts["UNRESOLVED"],
        "credential_material_included": False,
        "private_project_or_billing_material_included": False,
        "approved_restricted_control_authorities_only": True,
        "unapproved_bulk_scientific_payload_included": False,
        "symlinks_included": False,
        "special_files_included": False,
        "live_git_mutated": False,
        "full_c3_authorized": False,
    }
    validate_backup_manifest(manifest)
    manifest_path = backup_root / "backup_manifest.restricted.json"
    _write_new_private(
        manifest_path, canonical_json_bytes(manifest),
        maximum_bytes=int(policy["bounds"]["maximum_manifest_bytes"]),
    )
    manifest_sha = sha256_file(manifest_path)

    restored_files_root = restore_root / "files"
    os.mkdir(restored_files_root, 0o700)
    for row in records:
        if row["copied"] is not True:
            continue
        relative = str(row["backup_relative_path"])
        source = backup_root / relative
        destination = restore_root / relative
        _ensure_private_relative_directories(restore_root, PurePosixPath(relative).parent)
        size, digest, _ = _copy_regular_atomic(source, destination)
        if size != row["size_bytes"] or digest != row["sha256"]:
            raise BackupRecoveryError("RESTORE_COPY_HASH_MISMATCH")
    restored_count, restored_bytes = _verify_manifest_file_set(
        restore_root, records, scan_patterns=content_patterns
    )
    _verify_private_tree(restore_root)
    after_git = _git_snapshot(
        checkout, branch=branch, commit=governing_commit,
        origin=origin, approved_origin=approved_origin,
    )
    live_git_unchanged = before_git == after_git
    if not live_git_unchanged:
        raise BackupRecoveryError("LIVE_GIT_AUTHORITY_CHANGED")

    restore_receipt: Mapping[str, Any] = {
        "schema_version": 1,
        "artifact_type": RESTORE_TYPE,
        "status": RESTORE_STATUS,
        "attempt_id": attempt_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "governing_commit": governing_commit,
        "policy_sha256": policy_sha,
        "backup_manifest_sha256": manifest_sha,
        "git_bundle_sha256": git_result["bundle_sha256"],
        "exact_git_commit_restored": True,
        "git_fsck_passed": True,
        "linked_worktree_recreated": True,
        "tracked_file_count": git_result["tracked_file_count"],
        "tracked_file_set_sha256": git_result["tracked_file_set_sha256"],
        "post_normalization_tracked_file_set_sha256": git_result[
            "post_normalization_tracked_file_set_sha256"
        ],
        "post_normalization_git_fsck_passed": True,
        "git_bundle_full_reachable_scan_passed": True,
        "historical_path_scan_passed": True,
        "restored_file_count": restored_count,
        "restored_bytes": restored_bytes,
        "manifest_restore_checksum_equality": True,
        "owner_private_permissions_passed": True,
        "no_symlinks": True,
        "no_special_files": True,
        "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "approved_restricted_control_authorities_only": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "live_git_unchanged": True,
        "restore_test_passed": True,
        "full_c3_authorized": False,
    }
    validate_restore_receipt(restore_receipt)
    restore_receipt_path = backup_root / "restore_receipt.restricted.json"
    _write_new_private(
        restore_receipt_path, canonical_json_bytes(restore_receipt),
        maximum_bytes=int(policy["bounds"]["maximum_restore_receipt_bytes"]),
    )
    restore_receipt_sha = sha256_file(restore_receipt_path)

    aggregate: Mapping[str, Any] = {
        "schema_version": 1,
        "artifact_type": AGGREGATE_TYPE,
        "status": AGGREGATE_STATUS,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "policy_sha256": policy_sha,
        "selection_sha256": selection_sha,
        "backup_manifest_sha256": manifest_sha,
        "restore_receipt_sha256": restore_receipt_sha,
        "recovery_documentation_sha256": documentation_sha,
        "classification_counts": dict(sorted(classification_counts.items())),
        "declared_item_count": len(categories),
        "copied_file_count": copied_count,
        "copied_bytes": copied_bytes,
        "checksum_only_file_count": checksum_only_count,
        "git_bundle_bytes": git_result["bundle_size_bytes"],
        "git_bundle_sha256": git_result["bundle_sha256"],
        "git_bundle_reachable_commit_count": git_result["reachable_commit_count"],
        "git_bundle_historical_path_record_count": git_result[
            "historical_path_record_count"
        ],
        "git_bundle_reachable_unique_blob_count": git_result[
            "reachable_unique_blob_count"
        ],
        "git_bundle_reachable_blob_bytes": git_result["reachable_blob_bytes"],
        "git_bundle_allowed_synthetic_fixture_match_count": git_result[
            "allowed_synthetic_fixture_match_count"
        ],
        "git_bundle_full_reachable_scan_passed": True,
        "historical_path_scan_passed": True,
        "post_normalization_git_fsck_passed": True,
        "post_normalization_tracked_file_set_sha256": git_result[
            "post_normalization_tracked_file_set_sha256"
        ],
        "exact_git_commit_restored": True,
        "git_fsck_passed": True,
        "linked_worktree_recreated": True,
        "tracked_file_count": git_result["tracked_file_count"],
        "tracked_file_set_sha256": git_result["tracked_file_set_sha256"],
        "restored_file_count": restored_count,
        "restored_bytes": restored_bytes,
        "manifest_restore_checksum_equality": True,
        "owner_private_permissions_passed": True,
        "no_symlinks": True,
        "no_special_files": True,
        "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "approved_restricted_control_authorities_only": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "excluded_credential_item_count": classification_counts["EXCLUDED_CREDENTIAL_MATERIAL"],
        "unresolved_item_count": 0,
        "live_git_unchanged": True,
        "backup_verified": True,
        "restore_test_passed": True,
        "cloud_requests": 0,
        "object_listing_repeated": False,
        "scheduler_jobs_submitted": 0,
        "dicom_bodies_downloaded": 0,
        "real_dicom_extraction": False,
        "echoprime_inference": False,
        "model_fitting": False,
        "confirmatory_performance_accessed": False,
        "full_c3_authorized": False,
    }
    validate_aggregate_output(aggregate)
    _write_new_private(
        aggregate_output, canonical_json_bytes(aggregate),
        maximum_bytes=int(policy["bounds"]["maximum_aggregate_bytes"]),
    )
    return aggregate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--declaration", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--restore-root", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        aggregate = execute(
            policy_path=args.policy,
            attempt_id=args.attempt_id,
            governing_commit=args.governing_commit,
            checkout=args.checkout,
            declarations_raw=args.declaration,
            artifacts_raw=args.artifact,
            backup_root=args.backup_root,
            restore_root=args.restore_root,
            aggregate_output=args.aggregate_output,
        )
    except (BackupRecoveryError, OSError) as exc:
        code = exc.code if isinstance(exc, BackupRecoveryError) else "FILESYSTEM_IO_ERROR"
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "BACKUP_RECOVERY_FAILED_SANITIZED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": aggregate["status"],
                "backup_verified": aggregate["backup_verified"],
                "restore_test_passed": aggregate["restore_test_passed"],
                "credential_material_absent": aggregate["credential_material_absent"],
                "cloud_requests": 0,
                "scheduler_jobs_submitted": 0,
                "full_c3_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
