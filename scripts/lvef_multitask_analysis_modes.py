#!/usr/bin/env python3
"""Fail-closed controls for restricted analysis and reviewed safe export.

This module deliberately does not replace the aggregate writers in
``lvef_multitask_audit_utils``.  Direct restricted work and an export from that
work are separate authorities:

* ``APPROVED_DIRECT_RESTRICTED_AGENT`` permits row-level inspection only below
  an approved SCC root and writes a restricted execution receipt.
* ``MANUSCRIPT_OR_GIT_EXPORT`` validates an allowlisted aggregate schema,
  binds the candidate to the policy by SHA-256, requires a separately completed
  approval manifest, and revalidates the bytes before release.

The path helpers resolve symlinks and reject an escape from an approved root.
They also reject symlinked files and symlinked descendants inside an approved
root so a lexical path cannot silently redirect an output.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import yaml

from lvef_multitask_audit_utils import (
    FORBIDDEN_AGGREGATE_COLUMNS,
    assert_aggregate_safe_json,
    repository_root,
)


DIRECT_MODE = "APPROVED_DIRECT_RESTRICTED_AGENT"
EXPORT_MODE = "MANUSCRIPT_OR_GIT_EXPORT"
AUTHORIZATION_STATUS = "INSTITUTIONALLY_AUTHORIZED"
REQUEST_STATUS = "AWAITING_HUMAN_APPROVAL"
APPROVAL_DECISION = "APPROVED"
DEFAULT_POLICY = repository_root() / "configs" / "lvef_multitask_safe_export_policy.yaml"
STORAGE_CONTEXTUAL_FORBIDDEN_KEY_EXCEPTION = {
    "path": "roots.*.label",
    "allowed_string_values": ["disaster_recovery", "research"],
}


class SafetyPolicyError(ValueError):
    """Raised when a path, policy, schema, or approval fails closed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SafetyPolicyError(f"{label} must be a mapping")
    return value


def _require_nonempty_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise SafetyPolicyError(f"{label} must be a nonempty list of strings")
    return list(value)


def validate_policy(policy: Mapping[str, Any]) -> None:
    allowed_top_level = {
        "schema_version",
        "policy_id",
        "authorization",
        "modes",
        "forbidden_content",
        "export_profiles",
    }
    unknown = set(policy) - allowed_top_level
    if unknown:
        raise SafetyPolicyError(f"Unknown policy keys: {sorted(unknown)}")
    if policy.get("schema_version") != 1:
        raise SafetyPolicyError("Safe-export policy schema_version must equal 1")
    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]:
        raise SafetyPolicyError("Safe-export policy_id is required")

    authorization = _require_mapping(policy.get("authorization"), "authorization")
    if authorization.get("status") != AUTHORIZATION_STATUS:
        raise SafetyPolicyError("Institutional authorization is not active")

    modes = _require_mapping(policy.get("modes"), "modes")
    if set(modes) != {DIRECT_MODE, EXPORT_MODE}:
        raise SafetyPolicyError("Policy must define exactly the two approved analysis modes")
    direct = _require_mapping(modes[DIRECT_MODE], DIRECT_MODE)
    export = _require_mapping(modes[EXPORT_MODE], EXPORT_MODE)
    if direct.get("enabled") is not True or export.get("enabled") is not True:
        raise SafetyPolicyError("Both mode controls must be explicitly enabled")
    _validate_root_specs(direct.get("approved_roots"), f"{DIRECT_MODE}.approved_roots")
    _validate_root_specs(direct.get("receipt_roots"), f"{DIRECT_MODE}.receipt_roots")
    _validate_root_specs(export.get("restricted_staging_roots"), f"{EXPORT_MODE}.restricted_staging_roots")
    release_roots = _require_nonempty_strings(
        export.get("repository_release_roots"),
        f"{EXPORT_MODE}.repository_release_roots",
    )
    for raw_root in release_roots:
        relative = Path(raw_root)
        if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
            raise SafetyPolicyError("Repository release roots must be safe nonempty relative paths")
    if export.get("release_receipt_suffix") != ".release_receipt.json":
        raise SafetyPolicyError("The release receipt suffix must be .release_receipt.json")
    if export.get("human_approval_required") is not True:
        raise SafetyPolicyError("Human approval must remain required")

    forbidden = _require_mapping(policy.get("forbidden_content"), "forbidden_content")
    _require_nonempty_strings(forbidden.get("column_or_key_names"), "forbidden_content.column_or_key_names")
    _require_nonempty_strings(forbidden.get("value_patterns"), "forbidden_content.value_patterns")
    _require_nonempty_strings(forbidden.get("git_blocked_suffixes"), "forbidden_content.git_blocked_suffixes")
    for pattern in forbidden["value_patterns"]:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SafetyPolicyError("Invalid forbidden value pattern") from exc

    profiles = _require_mapping(policy.get("export_profiles"), "export_profiles")
    if not profiles:
        raise SafetyPolicyError("At least one export profile is required")
    for name, profile_value in profiles.items():
        if not isinstance(name, str) or not name:
            raise SafetyPolicyError("Export profile names must be nonempty strings")
        profile = _require_mapping(profile_value, f"export_profiles.{name}")
        if profile.get("kind") not in {"json", "csv"}:
            raise SafetyPolicyError(f"Unsupported export profile kind for {name}")
        _require_nonempty_strings(profile.get("extensions"), f"export_profiles.{name}.extensions")
        if not isinstance(profile.get("max_bytes"), int) or profile["max_bytes"] <= 0:
            raise SafetyPolicyError(f"export_profiles.{name}.max_bytes must be positive")
        if profile["kind"] == "json":
            _require_nonempty_strings(
                profile.get("required_top_level_keys"),
                f"export_profiles.{name}.required_top_level_keys",
            )
            _require_nonempty_strings(
                profile.get("allowed_top_level_keys"),
                f"export_profiles.{name}.allowed_top_level_keys",
            )
            exceptions = profile.get("contextual_forbidden_key_exceptions", [])
            if not isinstance(exceptions, list):
                raise SafetyPolicyError(
                    f"export_profiles.{name}.contextual_forbidden_key_exceptions "
                    "must be a list"
                )
            seen_exception_paths: set[str] = set()
            forbidden_names = {
                _normalized_field_name(value)
                for value in forbidden["column_or_key_names"]
            }
            for exception in exceptions:
                spec = _require_mapping(
                    exception,
                    f"export_profiles.{name}.contextual_forbidden_key_exceptions",
                )
                if set(spec) != {"path", "allowed_string_values"}:
                    raise SafetyPolicyError(
                        "Contextual forbidden-key exceptions require exactly path "
                        "and allowed_string_values"
                    )
                path = spec.get("path")
                if (
                    not isinstance(path, str)
                    or not re.fullmatch(
                        r"[A-Za-z0-9_]+(?:\.(?:[A-Za-z0-9_]+|\*))*",
                        path,
                    )
                    or path in seen_exception_paths
                ):
                    raise SafetyPolicyError(
                        "Contextual forbidden-key exception paths must be unique "
                        "safe dotted paths"
                    )
                if _normalized_field_name(path.rsplit(".", 1)[-1]) not in forbidden_names:
                    raise SafetyPolicyError(
                        "Contextual exception must terminate in a globally forbidden key"
                    )
                _require_nonempty_strings(
                    spec.get("allowed_string_values"),
                    "contextual forbidden-key allowed_string_values",
                )
                seen_exception_paths.add(path)
            if exceptions and (
                name != "scc_storage_inventory_summary_json"
                or len(exceptions) != 1
                or exceptions[0].get("path")
                != STORAGE_CONTEXTUAL_FORBIDDEN_KEY_EXCEPTION["path"]
                or set(exceptions[0].get("allowed_string_values", []))
                != set(
                    STORAGE_CONTEXTUAL_FORBIDDEN_KEY_EXCEPTION[
                        "allowed_string_values"
                    ]
                )
            ):
                raise SafetyPolicyError(
                    "Only the fixed storage-root label exception is permitted"
                )
        else:
            _require_nonempty_strings(
                profile.get("required_columns"),
                f"export_profiles.{name}.required_columns",
            )
            _require_nonempty_strings(
                profile.get("allowed_columns"),
                f"export_profiles.{name}.allowed_columns",
            )
            if not isinstance(profile.get("max_rows"), int) or profile["max_rows"] <= 0:
                raise SafetyPolicyError(f"export_profiles.{name}.max_rows must be positive")


def _validate_root_specs(value: Any, label: str) -> list[Mapping[str, str]]:
    if not isinstance(value, list) or not value:
        raise SafetyPolicyError(f"{label} must be a nonempty list")
    result: list[Mapping[str, str]] = []
    seen_ids: set[str] = set()
    for item in value:
        spec = _require_mapping(item, label)
        if set(spec) != {"id", "path"}:
            raise SafetyPolicyError(f"Each {label} entry must contain exactly id and path")
        root_id = spec.get("id")
        raw_path = spec.get("path")
        if not isinstance(root_id, str) or not root_id or root_id in seen_ids:
            raise SafetyPolicyError(f"{label} root ids must be unique nonempty strings")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise SafetyPolicyError(f"{label} paths must be absolute")
        seen_ids.add(root_id)
        result.append({"id": root_id, "path": raw_path})
    return result


def load_policy(path: Path = DEFAULT_POLICY) -> tuple[dict[str, Any], str]:
    lexical_path = Path(os.path.abspath(os.path.expanduser(str(path))))
    if lexical_path.is_symlink():
        raise SafetyPolicyError("Policy must be a regular, non-symlink file")
    policy_path = lexical_path.resolve(strict=True)
    if not policy_path.is_file():
        raise SafetyPolicyError("Policy must be a regular, non-symlink file")
    raw = policy_path.read_bytes()
    loaded = yaml.safe_load(raw)
    policy = dict(_require_mapping(loaded, "policy"))
    validate_policy(policy)
    return policy, sha256_bytes(raw)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_dotdot(path: Path) -> None:
    if ".." in path.parts:
        raise SafetyPolicyError("Parent traversal is prohibited")


def _reject_symlink_descendants(root: Path, target: Path) -> None:
    relative = target.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if cursor.is_symlink():
                raise SafetyPolicyError("Symlinked paths are prohibited")
        else:
            break


def bind_path_to_roots(
    path: Path,
    root_specs: Sequence[Mapping[str, str]],
    *,
    must_exist: bool,
    expect: str | None = None,
) -> dict[str, Any]:
    """Bind a path to an approved root after lexical and resolved checks.

    ``expect`` may be ``file`` or ``directory``.  A non-existing output may be
    checked with ``must_exist=False``; its closest existing ancestor must still
    resolve below the same approved root.
    """

    _reject_dotdot(path)
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    matches: list[dict[str, Any]] = []
    for spec in root_specs:
        lexical_root = Path(spec["path"]).expanduser()
        if lexical_root.is_symlink() or not lexical_root.is_dir():
            continue
        lexical_root = Path(os.path.abspath(str(lexical_root)))
        if not _is_relative_to(absolute, lexical_root):
            continue
        _reject_symlink_descendants(lexical_root, absolute)
        resolved_root = lexical_root.resolve(strict=True)
        if resolved_root.is_symlink() or not resolved_root.is_dir():
            continue
        if must_exist:
            try:
                resolved = absolute.resolve(strict=True)
            except FileNotFoundError as exc:
                raise SafetyPolicyError("Required restricted path does not exist") from exc
        else:
            ancestor = absolute
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            try:
                resolved_ancestor = ancestor.resolve(strict=True)
            except FileNotFoundError as exc:
                raise SafetyPolicyError("Restricted output has no existing approved ancestor") from exc
            if not _is_relative_to(resolved_ancestor, resolved_root):
                continue
            resolved = resolved_ancestor.joinpath(*absolute.relative_to(ancestor).parts)
        if not _is_relative_to(resolved, resolved_root):
            continue
        matches.append(
            {
                "root_id": str(spec["id"]),
                "root": resolved_root,
                "path": resolved,
                "relative_path": absolute.relative_to(lexical_root).as_posix(),
            }
        )
    if len(matches) != 1:
        raise SafetyPolicyError("Path must resolve under exactly one approved root")
    binding = matches[0]
    bound_path = Path(binding["path"])
    if must_exist:
        if bound_path.is_symlink():
            raise SafetyPolicyError("Symlinked paths are prohibited")
        mode = bound_path.stat().st_mode
        if expect == "file" and not stat.S_ISREG(mode):
            raise SafetyPolicyError("Expected a regular file")
        if expect == "directory" and not stat.S_ISDIR(mode):
            raise SafetyPolicyError("Expected a directory")
        if expect is None and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise SafetyPolicyError("Only regular files and directories are permitted")
    return binding


def _create_parent_and_rebind(
    path: Path,
    root_specs: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    binding = bind_path_to_roots(path, root_specs, must_exist=False)
    Path(binding["path"]).parent.mkdir(parents=True, exist_ok=True)
    return bind_path_to_roots(path, root_specs, must_exist=False)


def _reject_repository_local_restricted_path(path: Path) -> None:
    repo = repository_root().resolve(strict=True)
    resolved = path.resolve(strict=False)
    if resolved == repo or _is_relative_to(resolved, repo):
        raise SafetyPolicyError("Restricted analysis artifacts may not be written inside the repository")


def bind_approved_restricted_path(
    path: Path,
    *,
    policy: Mapping[str, Any],
    must_exist: bool,
    expect: str | None = None,
    root_kind: str = "direct",
    create: bool = False,
) -> Path:
    """Bind a restricted input/output to the approved SCC roots.

    ``root_kind=direct`` uses direct-analysis roots; ``staging`` uses the
    reviewed-export staging roots.  Repository-local paths are rejected even
    when the SCC repository itself sits below an otherwise approved root.
    """
    if root_kind == "direct":
        specs = policy["modes"][DIRECT_MODE]["approved_roots"]
    elif root_kind == "staging":
        specs = policy["modes"][EXPORT_MODE]["restricted_staging_roots"]
    else:
        raise SafetyPolicyError("Unknown approved-root kind")
    binding = bind_path_to_roots(path, specs, must_exist=must_exist, expect=expect)
    bound = Path(binding["path"])
    _reject_repository_local_restricted_path(bound)
    if create:
        if expect == "directory":
            bound.mkdir(parents=True, exist_ok=True)
        elif expect == "file":
            bound.parent.mkdir(parents=True, exist_ok=True)
        else:
            raise SafetyPolicyError("Created restricted output must declare file or directory")
        if expect == "directory":
            binding = bind_path_to_roots(path, specs, must_exist=True, expect="directory")
            bound = Path(binding["path"])
            _reject_repository_local_restricted_path(bound)
    return bound


def _exclusive_write_bytes(
    path: Path,
    payload: bytes,
    root_specs: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    binding = _create_parent_and_rebind(path, root_specs)
    destination = Path(binding["path"])
    _reject_repository_local_restricted_path(destination)
    try:
        with destination.open("xb") as handle:
            os.chmod(destination, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SafetyPolicyError("Refusing to overwrite an existing controlled artifact") from exc
    # Rebind the caller's lexical path.  On macOS, /var resolves to /private/var;
    # feeding the resolved destination back through a /var policy root would
    # otherwise fail the intentional lexical-containment check.
    final = bind_path_to_roots(path, root_specs, must_exist=True, expect="file")
    if sha256_file(Path(final["path"])) != sha256_bytes(payload):
        raise SafetyPolicyError("Controlled artifact hash changed during creation")
    return final


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _forbidden_names(policy: Mapping[str, Any]) -> set[str]:
    configured = policy["forbidden_content"]["column_or_key_names"]
    return {str(name).strip().casefold() for name in configured} | {
        str(name).strip().casefold() for name in FORBIDDEN_AGGREGATE_COLUMNS
    }


def _normalized_field_name(value: Any) -> str:
    """Normalize untrusted table/JSON field names before safety decisions."""
    return str(value).strip().casefold()


def _iter_json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_json_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_json_strings(nested)


def _assert_no_forbidden_values(values: Iterable[str], policy: Mapping[str, Any]) -> None:
    patterns = [re.compile(item) for item in policy["forbidden_content"]["value_patterns"]]
    for value in values:
        if any(pattern.search(value) for pattern in patterns):
            raise SafetyPolicyError("Candidate contains a forbidden restricted-value pattern")


def _strict_json_loads(payload: bytes) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SafetyPolicyError("JSON candidate must be UTF-8") from exc

    def reject_constant(_: str) -> None:
        raise SafetyPolicyError("NaN and infinite JSON numbers are prohibited")

    def reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject exact and safety-normalized duplicate keys at every depth."""
        result: dict[str, Any] = {}
        normalized: set[str] = set()
        for key, value in pairs:
            normalized_key = _normalized_field_name(key)
            if key in result or normalized_key in normalized:
                raise SafetyPolicyError(
                    "Duplicate or normalization-colliding JSON keys are prohibited"
                )
            result[key] = value
            normalized.add(normalized_key)
        return result

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_object,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SafetyPolicyError("Candidate is not valid strict JSON") from exc


def _assert_no_forbidden_json_keys(
    value: Any,
    forbidden_names: set[str],
    *,
    contextual_exceptions: Sequence[Mapping[str, Any]] = (),
    path: tuple[str | int, ...] = (),
) -> None:
    """Recursively apply safety normalization to every JSON object key."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            child_path = (*path, key)
            if (
                _normalized_field_name(key) in forbidden_names
                and not _contextual_forbidden_key_is_allowed(
                    child_path,
                    nested,
                    contextual_exceptions,
                )
            ):
                raise SafetyPolicyError("JSON export candidate contains a forbidden key")
            _assert_no_forbidden_json_keys(
                nested,
                forbidden_names,
                contextual_exceptions=contextual_exceptions,
                path=child_path,
            )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_forbidden_json_keys(
                nested,
                forbidden_names,
                contextual_exceptions=contextual_exceptions,
                path=(*path, index),
            )


def _contextual_forbidden_key_is_allowed(
    path: tuple[str | int, ...],
    value: Any,
    exceptions: Sequence[Mapping[str, Any]],
) -> bool:
    for exception in exceptions:
        expected_parts = str(exception["path"]).split(".")
        if len(expected_parts) != len(path):
            continue
        if all(
            expected == "*" and isinstance(observed, int)
            or expected == str(observed)
            for expected, observed in zip(expected_parts, path)
        ):
            return isinstance(value, str) and value in set(
                exception["allowed_string_values"]
            )
    return False


def _aggregate_safety_view(
    value: Any,
    *,
    forbidden_names: set[str],
    contextual_exceptions: Sequence[Mapping[str, Any]],
    path: tuple[str | int, ...] = (),
) -> Any:
    """Rename only explicitly allowed contextual keys for the legacy JSON gate."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            child_path = (*path, key)
            output_key = key
            if _normalized_field_name(key) in forbidden_names:
                if not _contextual_forbidden_key_is_allowed(
                    child_path,
                    nested,
                    contextual_exceptions,
                ):
                    output_key = key
                else:
                    output_key = f"contextual_safe_{key}"
            result[output_key] = _aggregate_safety_view(
                nested,
                forbidden_names=forbidden_names,
                contextual_exceptions=contextual_exceptions,
                path=child_path,
            )
        return result
    if isinstance(value, list):
        return [
            _aggregate_safety_view(
                nested,
                forbidden_names=forbidden_names,
                contextual_exceptions=contextual_exceptions,
                path=(*path, index),
            )
            for index, nested in enumerate(value)
        ]
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null_or_number":
        return value is None or _matches_type(value, "number")
    raise SafetyPolicyError(f"Unsupported configured field type: {expected}")


def validate_candidate_bytes(
    payload: bytes,
    *,
    filename: str,
    profile_name: str,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    profiles = policy["export_profiles"]
    if profile_name not in profiles:
        raise SafetyPolicyError("Export profile is not allowlisted")
    profile = profiles[profile_name]
    if len(payload) > int(profile["max_bytes"]):
        raise SafetyPolicyError("Candidate exceeds the profile byte limit")
    lower_name = filename.lower()
    if not any(lower_name.endswith(str(extension).lower()) for extension in profile["extensions"]):
        raise SafetyPolicyError("Candidate extension does not match its export profile")

    forbidden_names = _forbidden_names(policy)
    if profile["kind"] == "json":
        value = _strict_json_loads(payload)
        if not isinstance(value, Mapping):
            raise SafetyPolicyError("JSON export candidate must be a top-level object")
        contextual_exceptions = profile.get(
            "contextual_forbidden_key_exceptions",
            [],
        )
        assert_aggregate_safe_json(
            _aggregate_safety_view(
                value,
                forbidden_names=forbidden_names,
                contextual_exceptions=contextual_exceptions,
            )
        )
        keys = set(value)
        required = set(profile["required_top_level_keys"])
        allowed = set(profile["allowed_top_level_keys"])
        if not required.issubset(keys):
            raise SafetyPolicyError("JSON export candidate is missing required profile keys")
        if not keys.issubset(allowed):
            raise SafetyPolicyError("JSON export candidate contains unapproved top-level keys")
        _assert_no_forbidden_json_keys(
            value,
            forbidden_names,
            contextual_exceptions=contextual_exceptions,
        )
        for key, expected_type in profile.get("field_types", {}).items():
            if key in value and not _matches_type(value[key], str(expected_type)):
                raise SafetyPolicyError("JSON export candidate has an invalid field type")
        _assert_no_forbidden_values(_iter_json_strings(value), policy)
        return {
            "status": "PASS",
            "profile": profile_name,
            "kind": "json",
            "bytes": len(payload),
            "top_level_keys": len(keys),
        }

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SafetyPolicyError("CSV candidate must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames
    if not headers or any(header is None or not header for header in headers):
        raise SafetyPolicyError("CSV export candidate requires a nonempty header")
    normalized_headers = [_normalized_field_name(header) for header in headers]
    if len(normalized_headers) != len(set(normalized_headers)):
        raise SafetyPolicyError("CSV export candidate contains duplicate columns")
    header_set = set(headers)
    required_columns = set(profile["required_columns"])
    allowed_columns = set(profile["allowed_columns"])
    if not required_columns.issubset(header_set):
        raise SafetyPolicyError("CSV export candidate is missing required profile columns")
    if not header_set.issubset(allowed_columns):
        raise SafetyPolicyError("CSV export candidate contains unapproved columns")
    if any(_normalized_field_name(header) in forbidden_names for header in headers):
        raise SafetyPolicyError("CSV export candidate contains a forbidden column")
    row_count = 0
    values: list[str] = []
    for row in reader:
        if None in row:
            raise SafetyPolicyError("CSV export candidate has more values than headers")
        row_count += 1
        if row_count > int(profile["max_rows"]):
            raise SafetyPolicyError("CSV export candidate exceeds the profile row limit")
        values.extend(str(value) for value in row.values() if value is not None)
    _assert_no_forbidden_values(values, policy)
    return {
        "status": "PASS",
        "profile": profile_name,
        "kind": "csv",
        "bytes": len(payload),
        "rows": row_count,
        "columns": len(headers),
    }


def validate_candidate_file(
    candidate: Path,
    *,
    profile_name: str,
    policy: Mapping[str, Any],
    staging_roots: Sequence[Mapping[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    roots = staging_roots or policy["modes"][EXPORT_MODE]["restricted_staging_roots"]
    binding = bind_path_to_roots(candidate, roots, must_exist=True, expect="file")
    source = Path(binding["path"])
    _reject_repository_local_restricted_path(source)
    payload = source.read_bytes()
    validation = validate_candidate_bytes(
        payload,
        filename=source.name,
        profile_name=profile_name,
        policy=policy,
    )
    return binding, validation, payload


def create_direct_restricted_receipt(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    receipt_path: Path,
    purpose: str,
    source_commit: str,
    organization_class: str,
    input_paths: Sequence[Path] = (),
    output_paths: Sequence[Path] = (),
    command_sha256: str | None = None,
    hash_inputs: bool = False,
) -> dict[str, Any]:
    if not purpose.strip() or not source_commit.strip() or not organization_class.strip():
        raise SafetyPolicyError("Purpose, source commit, and organization class are required")
    direct = policy["modes"][DIRECT_MODE]
    records: list[dict[str, Any]] = []
    for path in input_paths:
        binding = bind_path_to_roots(path, direct["approved_roots"], must_exist=True)
        resolved = Path(binding["path"])
        record: dict[str, Any] = {
            "direction": "input",
            "root_id": binding["root_id"],
            "relative_path": binding["relative_path"],
            "kind": "file" if resolved.is_file() else "directory",
            "size_bytes": resolved.stat().st_size if resolved.is_file() else None,
        }
        if hash_inputs and resolved.is_file():
            record["sha256"] = sha256_file(resolved)
        records.append(record)
    for path in output_paths:
        binding = bind_path_to_roots(path, direct["approved_roots"], must_exist=False)
        _reject_repository_local_restricted_path(Path(binding["path"]))
        records.append(
            {
                "direction": "output",
                "root_id": binding["root_id"],
                "relative_path": binding["relative_path"],
            }
        )
    receipt = {
        "schema_version": 1,
        "mode": DIRECT_MODE,
        "authorization_status": AUTHORIZATION_STATUS,
        "organization_class": organization_class.strip(),
        "purpose": purpose.strip(),
        "source_commit": source_commit.strip(),
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "created_at_utc": utc_now(),
        "command_sha256": command_sha256,
        "path_records": records,
        "detailed_outputs_remain_restricted": True,
        "export_authority_granted": False,
        "confirmatory_performance_authority_granted": False,
    }
    _exclusive_write_bytes(receipt_path, _json_bytes(receipt), direct["receipt_roots"])
    return receipt


def validate_direct_restricted_receipt(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    receipt_path: Path,
    source_commit: str,
    purpose: str,
) -> dict[str, Any]:
    direct = policy["modes"][DIRECT_MODE]
    _, receipt = _read_bound_json(receipt_path, direct["receipt_roots"])
    expected_keys = {
        "schema_version",
        "mode",
        "authorization_status",
        "organization_class",
        "purpose",
        "source_commit",
        "policy_id",
        "policy_sha256",
        "created_at_utc",
        "command_sha256",
        "path_records",
        "detailed_outputs_remain_restricted",
        "export_authority_granted",
        "confirmatory_performance_authority_granted",
    }
    if set(receipt) != expected_keys:
        raise SafetyPolicyError("Restricted-analysis receipt has an unexpected schema")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("mode") != DIRECT_MODE
        or receipt.get("authorization_status") != AUTHORIZATION_STATUS
        or receipt.get("policy_id") != policy["policy_id"]
        or receipt.get("policy_sha256") != policy_sha256
        or receipt.get("source_commit") != source_commit
        or receipt.get("purpose") != purpose
        or receipt.get("detailed_outputs_remain_restricted") is not True
        or receipt.get("export_authority_granted") is not False
        or receipt.get("confirmatory_performance_authority_granted") is not False
    ):
        raise SafetyPolicyError("Restricted-analysis receipt authority fields are invalid")
    if not isinstance(receipt.get("organization_class"), str) or not receipt["organization_class"].strip():
        raise SafetyPolicyError("Restricted-analysis receipt lacks organization class")
    command_sha = receipt.get("command_sha256")
    if command_sha is not None and not re.fullmatch(r"[0-9a-f]{64}", str(command_sha)):
        raise SafetyPolicyError("Restricted-analysis receipt command checksum is invalid")
    records = receipt.get("path_records")
    if not isinstance(records, list) or not records:
        raise SafetyPolicyError("Restricted-analysis receipt lacks bound paths")
    allowed_root_ids = {str(item["id"]) for item in direct["approved_roots"]}
    for record in records:
        if not isinstance(record, Mapping):
            raise SafetyPolicyError("Restricted-analysis receipt path record is invalid")
        if record.get("direction") not in {"input", "output"}:
            raise SafetyPolicyError("Restricted-analysis receipt path direction is invalid")
        if record.get("root_id") not in allowed_root_ids:
            raise SafetyPolicyError("Restricted-analysis receipt root is invalid")
        relative = PurePosixPath(str(record.get("relative_path", "")))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise SafetyPolicyError("Restricted-analysis receipt relative path is invalid")
    return receipt


def _deterministic_request_id(
    *,
    candidate_sha256: str,
    policy_sha256: str,
    profile_name: str,
    root_id: str,
    relative_path: str,
) -> str:
    payload = "\n".join(
        [candidate_sha256, policy_sha256, profile_name, root_id, relative_path]
    ).encode("utf-8")
    return sha256_bytes(payload)


def prepare_export_request(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    candidate: Path,
    profile_name: str,
    request_path: Path,
    approval_template_path: Path,
    purpose: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not purpose.strip():
        raise SafetyPolicyError("A nonempty export purpose is required")
    export = policy["modes"][EXPORT_MODE]
    binding, validation, payload = validate_candidate_file(
        candidate,
        profile_name=profile_name,
        policy=policy,
    )
    candidate_sha = sha256_bytes(payload)
    request_id = _deterministic_request_id(
        candidate_sha256=candidate_sha,
        policy_sha256=policy_sha256,
        profile_name=profile_name,
        root_id=binding["root_id"],
        relative_path=binding["relative_path"],
    )
    request = {
        "schema_version": 1,
        "mode": EXPORT_MODE,
        "status": REQUEST_STATUS,
        "request_id": request_id,
        "purpose": purpose.strip(),
        "candidate_root_id": binding["root_id"],
        "candidate_relative_path": binding["relative_path"],
        "candidate_sha256": candidate_sha,
        "candidate_size_bytes": len(payload),
        "export_profile": profile_name,
        "profile_validation": validation,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "prepared_at_utc": utc_now(),
        "human_approval_required": True,
        "release_authority_granted": False,
    }
    approval_template = {
        "schema_version": 1,
        "mode": EXPORT_MODE,
        "decision": "PENDING",
        "request_id": request_id,
        "candidate_sha256": candidate_sha,
        "export_profile": profile_name,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "approved_by": "",
        "approver_role": "",
        "approved_at_utc": "",
        "rationale": "",
    }
    _exclusive_write_bytes(request_path, _json_bytes(request), export["restricted_staging_roots"])
    _exclusive_write_bytes(
        approval_template_path,
        _json_bytes(approval_template),
        export["restricted_staging_roots"],
    )
    return request, approval_template


def _read_bound_json(
    path: Path,
    roots: Sequence[Mapping[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    binding = bind_path_to_roots(path, roots, must_exist=True, expect="file")
    value = _strict_json_loads(Path(binding["path"]).read_bytes())
    if not isinstance(value, dict):
        raise SafetyPolicyError("Controlled manifest must be a JSON object")
    return binding, value


def _parse_approval_time(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise SafetyPolicyError("Approval timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SafetyPolicyError("Approval timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise SafetyPolicyError("Approval timestamp must include a timezone")


def validate_approval_manifest(
    approval: Mapping[str, Any],
    request: Mapping[str, Any],
) -> None:
    expected_keys = {
        "schema_version",
        "mode",
        "decision",
        "request_id",
        "candidate_sha256",
        "export_profile",
        "policy_id",
        "policy_sha256",
        "approved_by",
        "approver_role",
        "approved_at_utc",
        "rationale",
    }
    if set(approval) != expected_keys:
        raise SafetyPolicyError("Approval manifest has an unexpected schema")
    if approval.get("schema_version") != 1 or approval.get("mode") != EXPORT_MODE:
        raise SafetyPolicyError("Approval manifest mode or schema is invalid")
    if approval.get("decision") != APPROVAL_DECISION:
        raise SafetyPolicyError("Export has not been explicitly approved")
    for field in (
        "request_id",
        "candidate_sha256",
        "export_profile",
        "policy_id",
        "policy_sha256",
    ):
        if approval.get(field) != request.get(field):
            raise SafetyPolicyError("Approval is not hash-bound to this export request")
    for field in ("approved_by", "approver_role", "rationale"):
        if not isinstance(approval.get(field), str) or not str(approval[field]).strip():
            raise SafetyPolicyError("Approval identity, role, and rationale are required")
    _parse_approval_time(approval.get("approved_at_utc"))


def _validate_request(
    request: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> None:
    expected_keys = {
        "schema_version",
        "mode",
        "status",
        "request_id",
        "purpose",
        "candidate_root_id",
        "candidate_relative_path",
        "candidate_sha256",
        "candidate_size_bytes",
        "export_profile",
        "profile_validation",
        "policy_id",
        "policy_sha256",
        "prepared_at_utc",
        "human_approval_required",
        "release_authority_granted",
    }
    if set(request) != expected_keys:
        raise SafetyPolicyError("Export request has an unexpected schema")
    if (
        request.get("schema_version") != 1
        or request.get("mode") != EXPORT_MODE
        or request.get("status") != REQUEST_STATUS
        or request.get("human_approval_required") is not True
        or request.get("release_authority_granted") is not False
    ):
        raise SafetyPolicyError("Export request authority fields are invalid")
    if request.get("policy_id") != policy["policy_id"] or request.get("policy_sha256") != policy_sha256:
        raise SafetyPolicyError("Export request is not bound to the current policy")


def _safe_repository_destination(
    destination: Path,
    *,
    policy: Mapping[str, Any],
    repo_root: Path,
) -> tuple[Path, str]:
    lexical_repo_root = Path(os.path.abspath(os.path.expanduser(str(repo_root))))
    if lexical_repo_root.is_symlink():
        raise SafetyPolicyError("Repository root must not be a symlink")
    root = lexical_repo_root.resolve(strict=True)
    if not root.is_dir():
        raise SafetyPolicyError("Repository root must be a regular directory")
    _reject_dotdot(destination)
    absolute = destination if destination.is_absolute() else root / destination
    absolute = Path(os.path.abspath(str(absolute)))
    allowed = []
    for relative_root in policy["modes"][EXPORT_MODE]["repository_release_roots"]:
        candidate_root = root / relative_root
        if candidate_root.exists() and candidate_root.is_symlink():
            raise SafetyPolicyError("Repository release root may not be a symlink")
        if _is_relative_to(absolute, candidate_root):
            allowed.append(candidate_root)
    if len(allowed) != 1:
        raise SafetyPolicyError("Destination is outside the allowlisted repository release root")
    release_root = allowed[0]
    _reject_symlink_descendants(root, release_root)
    release_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_descendants(release_root, absolute)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_descendants(release_root, absolute)
    resolved_parent = absolute.parent.resolve(strict=True)
    resolved_release_root = release_root.resolve(strict=True)
    if not _is_relative_to(resolved_parent, resolved_release_root):
        raise SafetyPolicyError("Destination parent escapes the repository release root")
    if absolute.exists() or absolute.is_symlink():
        raise SafetyPolicyError("Refusing to overwrite an existing released artifact")
    return absolute, absolute.relative_to(root).as_posix()


def release_approved_export(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    candidate: Path,
    request_path: Path,
    approval_path: Path,
    destination: Path,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    export = policy["modes"][EXPORT_MODE]
    _, request = _read_bound_json(request_path, export["restricted_staging_roots"])
    _, approval = _read_bound_json(approval_path, export["restricted_staging_roots"])
    _validate_request(request, policy=policy, policy_sha256=policy_sha256)
    validate_approval_manifest(approval, request)

    binding, validation, payload = validate_candidate_file(
        candidate,
        profile_name=str(request["export_profile"]),
        policy=policy,
    )
    if binding["root_id"] != request["candidate_root_id"] or binding["relative_path"] != request["candidate_relative_path"]:
        raise SafetyPolicyError("Supplied candidate path differs from the reviewed candidate")
    candidate_sha = sha256_bytes(payload)
    if candidate_sha != request["candidate_sha256"] or len(payload) != request["candidate_size_bytes"]:
        raise SafetyPolicyError("Candidate changed after export preparation")
    expected_request_id = _deterministic_request_id(
        candidate_sha256=candidate_sha,
        policy_sha256=policy_sha256,
        profile_name=str(request["export_profile"]),
        root_id=binding["root_id"],
        relative_path=binding["relative_path"],
    )
    if request["request_id"] != expected_request_id:
        raise SafetyPolicyError("Export request identifier is not reproducible")

    destination_path, destination_relative = _safe_repository_destination(
        destination,
        policy=policy,
        repo_root=repo_root or repository_root(),
    )
    suffix = export["release_receipt_suffix"]
    receipt_path = Path(str(destination_path) + suffix)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise SafetyPolicyError("Refusing to overwrite an existing release receipt")
    release_receipt = {
        "schema_version": 1,
        "mode": EXPORT_MODE,
        "status": "RELEASED_AFTER_REVIEW",
        "request_id": request["request_id"],
        "candidate_sha256": candidate_sha,
        "candidate_size_bytes": len(payload),
        "export_profile": request["export_profile"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "approval_decision": APPROVAL_DECISION,
        "approver_role": str(approval["approver_role"]).strip(),
        "approval_date": str(approval["approved_at_utc"]),
        "destination_repository_relative_path": destination_relative,
        "schema_validation_status": validation["status"],
        "released_at_utc": utc_now(),
        "restricted_identifiers_exported": False,
    }
    try:
        with destination_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with receipt_path.open("xb") as handle:
            receipt_bytes = _json_bytes(release_receipt)
            handle.write(receipt_bytes)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        for created in (receipt_path, destination_path):
            if created.exists() and not created.is_symlink():
                created.unlink()
        raise
    if sha256_file(destination_path) != candidate_sha:
        raise SafetyPolicyError("Released artifact does not match the approved candidate")
    return release_receipt, destination_path, receipt_path


def _direct_cli(args: argparse.Namespace) -> int:
    policy, policy_sha = load_policy(args.policy)
    create_direct_restricted_receipt(
        policy=policy,
        policy_sha256=policy_sha,
        receipt_path=args.receipt,
        purpose=args.purpose,
        source_commit=args.source_commit,
        organization_class=args.organization_class,
        input_paths=args.input,
        output_paths=args.output,
        command_sha256=args.command_sha256,
        hash_inputs=args.hash_inputs,
    )
    print(
        json.dumps(
            {
                "status": "PASS_RESTRICTED_RECEIPT_WRITTEN",
                "mode": DIRECT_MODE,
                "export_authority_granted": False,
            },
            sort_keys=True,
        )
    )
    return 0


def _direct_validate_cli(args: argparse.Namespace) -> int:
    policy, policy_sha = load_policy(args.policy)
    validate_direct_restricted_receipt(
        policy=policy,
        policy_sha256=policy_sha,
        receipt_path=args.receipt,
        source_commit=args.source_commit,
        purpose=args.purpose,
    )
    print(
        json.dumps(
            {"status": "PASS_RESTRICTED_RECEIPT_VALID", "mode": DIRECT_MODE},
            sort_keys=True,
        )
    )
    return 0


def _validate_policy_cli(args: argparse.Namespace) -> int:
    policy, policy_sha = load_policy(args.policy)
    print(
        json.dumps(
            {
                "status": "PASS",
                "policy_id": policy["policy_id"],
                "policy_sha256": policy_sha,
                "modes": sorted(policy["modes"]),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-policy")
    validate_parser.set_defaults(handler=_validate_policy_cli)

    direct = subparsers.add_parser("direct-receipt")
    direct.add_argument("--receipt", type=Path, required=True)
    direct.add_argument("--purpose", required=True)
    direct.add_argument("--source-commit", required=True)
    direct.add_argument("--organization-class", required=True)
    direct.add_argument("--command-sha256")
    direct.add_argument("--hash-inputs", action="store_true")
    direct.add_argument("--input", type=Path, action="append", default=[])
    direct.add_argument("--output", type=Path, action="append", default=[])
    direct.set_defaults(handler=_direct_cli)

    direct_validate = subparsers.add_parser("direct-receipt-validate")
    direct_validate.add_argument("--receipt", type=Path, required=True)
    direct_validate.add_argument("--source-commit", required=True)
    direct_validate.add_argument("--purpose", required=True)
    direct_validate.set_defaults(handler=_direct_validate_cli)
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.handler(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_SAFETY_POLICY",
                    "error_type": type(exc).__name__,
                    "sensitive_details_emitted": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(guarded_main())
