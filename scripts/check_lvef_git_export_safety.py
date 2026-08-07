#!/usr/bin/env python3
"""Fail a commit gate when staged bytes bypass the reviewed export contract."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

from lvef_multitask_analysis_modes import (
    APPROVAL_DECISION,
    DEFAULT_POLICY,
    EXPORT_MODE,
    SafetyPolicyError,
    _assert_no_forbidden_values,
    _forbidden_names,
    _iter_json_strings,
    _normalized_field_name,
    _strict_json_loads,
    load_policy,
    sha256_bytes,
    validate_candidate_bytes,
)
from lvef_multitask_audit_utils import assert_aggregate_safe_json, repository_root


def _git(repo: Path, arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SafetyPolicyError("Git index inspection failed")
    return result.stdout


def staged_paths(repo: Path) -> list[str]:
    payload = _git(repo, ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"])
    return sorted(item.decode("utf-8") for item in payload.split(b"\0") if item)


def _index_blob(repo: Path, relative_path: str) -> bytes:
    return _git(repo, ["show", f":{relative_path}"])


def _index_mode(repo: Path, relative_path: str) -> str:
    payload = _git(repo, ["ls-files", "--stage", "--", relative_path]).decode("utf-8").strip()
    rows = [line for line in payload.splitlines() if line]
    if len(rows) != 1:
        raise SafetyPolicyError("Staged path does not have one unambiguous index entry")
    return rows[0].split(maxsplit=1)[0]


def _index_contains(repo: Path, relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f":{relative_path}"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _is_safe_relative_git_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\x00" not in value


def _under_release_root(relative_path: str, policy: Mapping[str, Any]) -> bool:
    path = PurePosixPath(relative_path)
    for raw_root in policy["modes"][EXPORT_MODE]["repository_release_roots"]:
        root = PurePosixPath(str(raw_root))
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _assert_global_table_safety(payload: bytes, suffix: str, policy: Mapping[str, Any]) -> None:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SafetyPolicyError("Staged text table is not UTF-8") from exc
    delimiter = "\t" if suffix in {".tsv", ".tab"} else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise SafetyPolicyError("Staged text table is empty") from exc
    normalized_header = [_normalized_field_name(column) for column in header]
    if len(normalized_header) != len(set(normalized_header)):
        raise SafetyPolicyError("Staged text table has duplicate columns")
    forbidden = _forbidden_names(policy)
    if any(column in forbidden for column in normalized_header):
        raise SafetyPolicyError("Staged text table contains a restricted column")
    for row in reader:
        _assert_no_forbidden_values((str(value) for value in row), policy)


def _assert_global_json_safety(payload: bytes, policy: Mapping[str, Any]) -> None:
    value = _strict_json_loads(payload)
    assert_aggregate_safe_json(value)
    forbidden = _forbidden_names(policy)

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            if any(_normalized_field_name(key) in forbidden for key in node):
                raise SafetyPolicyError("Staged JSON contains a restricted key")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    _assert_no_forbidden_values(_iter_json_strings(value), policy)


PATIENT_DICOM_LOCATOR_RE = re.compile(
    r"(?i)(?:gs://[^\s]+/)?files/p[0-9]{2}/p[0-9]{6,}/s[0-9]{6,}/[^\s,|]+\.dcm"
)
RESTRICTED_DICOM_PATH_RE = re.compile(
    r"(?i)/restricted/(?:project|projectnb)/[^\n\r]*"
    r"(?:/p[0-9]{6,}/s[0-9]{6,}/|\.dcm(?:$|[\s,|]))"
)
IDENTIFIER_HEADER_RE = re.compile(
    r"(?i)(?:subject_id|patient_id|person_id).*(?:study_id|dicom_id|measurement_id)"
)
IDENTIFIER_ROW_RE = re.compile(r"(?m)^\s*[|]?\s*[0-9]{6,}\s*[,|\t]\s*[0-9]{6,}(?:\s*[,|\t]|\s*$)")
SINGLE_IDENTIFIER_HEADER_RE = re.compile(
    r"(?im)^\s*[|]?\s*(?:subject_id|patient_id|person_id|study_id|dicom_id|measurement_id)"
    r"\s*(?:[,|\t]|\s*$)"
)
SINGLE_IDENTIFIER_ROW_RE = re.compile(
    r"(?m)^\s*[|]?\s*[0-9]{6,}\s*(?:[,|\t]|\s*$)"
)
RESTRICTED_ARTIFACT_BASENAME_RE = re.compile(
    r"(?i)(?:^|/)[^/]*(?:_restricted|\.restricted)\.(?:csv|tsv|tab|json|jsonl|ndjson|md|txt)$"
)
CLOUD_CREDENTIAL_BASENAME_RE = re.compile(
    r"(?i)(?:^|/)(?:"
    r"application_default_credentials\.json|access_tokens\.db|credentials\.db|\.boto|"
    r"[^/]*service[_-]?account[_-]?key[^/]*\.json|"
    r"[^/]*gcp[_-]?credentials?[^/]*\.json|"
    r"[^/]*requester[_-]?pays[^/]*\.env|"
    r"lvef_multitask_phase1ebc_(?:session|preflight)\.env|"
    r"lvef_c3_preflight\.env"
    r")$"
)
CLOUD_CREDENTIAL_DIRECTORY_RE = re.compile(
    r"(?i)(?:^|/)(?:\.config/gcloud|\.gcloud|\.gsutil|gcloud_config)(?:/|$)"
)
HIGH_CONFIDENCE_CLOUD_SECRET_RES = (
    re.compile(r"(?i)\bya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}\b", re.IGNORECASE),
)
CLOUD_CREDENTIAL_JSON_KEY_RE = re.compile(
    r"(?i)[\"'](?:refresh_token|access_token|id_token|client_secret|private_key|private_key_id)"
    r"[\"']\s*[:=]"
)
HARD_BLOCKED_TEXT_SUFFIXES = (".jsonl", ".ndjson", ".log", ".out", ".err")


def _assert_high_confidence_text_safety(
    payload: bytes, relative_path: str
) -> None:
    """Reject row/locator-shaped restricted text in every staged text format.

    Test fixtures must construct restricted-looking values from fragments;
    there is deliberately no whole-file or directory bypass.
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SafetyPolicyError("Staged text is not UTF-8") from exc
    if PATIENT_DICOM_LOCATOR_RE.search(text) or RESTRICTED_DICOM_PATH_RE.search(text):
        raise SafetyPolicyError("Staged text contains a patient/study DICOM locator")
    if IDENTIFIER_HEADER_RE.search(text) and IDENTIFIER_ROW_RE.search(text):
        raise SafetyPolicyError("Staged text contains an identifier-shaped row table")
    if SINGLE_IDENTIFIER_HEADER_RE.search(text) and SINGLE_IDENTIFIER_ROW_RE.search(text):
        raise SafetyPolicyError("Staged text contains an identifier-shaped row table")
    if any(pattern.search(text) for pattern in HIGH_CONFIDENCE_CLOUD_SECRET_RES):
        raise SafetyPolicyError("Staged text contains high-confidence cloud credential material")
    if PurePosixPath(relative_path).suffix.lower() == ".json" and CLOUD_CREDENTIAL_JSON_KEY_RE.search(text):
        raise SafetyPolicyError("Staged JSON contains cloud credential state")


def _validate_release_pair(
    *,
    repo: Path,
    artifact_path: str,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> None:
    receipt_suffix = policy["modes"][EXPORT_MODE]["release_receipt_suffix"]
    receipt_path = artifact_path + receipt_suffix
    if not _index_contains(repo, receipt_path):
        raise SafetyPolicyError("Approved export is missing its staged/indexed release receipt")
    artifact = _index_blob(repo, artifact_path)
    receipt_payload = _index_blob(repo, receipt_path)
    receipt_value = _strict_json_loads(receipt_payload)
    if not isinstance(receipt_value, Mapping):
        raise SafetyPolicyError("Release receipt must be a JSON object")
    expected_keys = {
        "schema_version",
        "mode",
        "status",
        "request_id",
        "candidate_sha256",
        "candidate_size_bytes",
        "export_profile",
        "policy_id",
        "policy_sha256",
        "approval_decision",
        "approver_role",
        "approval_date",
        "destination_repository_relative_path",
        "schema_validation_status",
        "released_at_utc",
        "restricted_identifiers_exported",
    }
    if set(receipt_value) != expected_keys:
        raise SafetyPolicyError("Release receipt has an unexpected schema")
    if (
        receipt_value.get("schema_version") != 1
        or receipt_value.get("mode") != EXPORT_MODE
        or receipt_value.get("status") != "RELEASED_AFTER_REVIEW"
        or receipt_value.get("approval_decision") != APPROVAL_DECISION
        or receipt_value.get("schema_validation_status") != "PASS"
        or receipt_value.get("restricted_identifiers_exported") is not False
    ):
        raise SafetyPolicyError("Release receipt authority fields are invalid")
    if receipt_value.get("policy_id") != policy["policy_id"] or receipt_value.get("policy_sha256") != policy_sha256:
        raise SafetyPolicyError("Release receipt is not bound to the current policy")
    if receipt_value.get("destination_repository_relative_path") != artifact_path:
        raise SafetyPolicyError("Release receipt names a different repository artifact")
    if receipt_value.get("candidate_sha256") != sha256_bytes(artifact) or receipt_value.get("candidate_size_bytes") != len(artifact):
        raise SafetyPolicyError("Staged aggregate bytes differ from the approved release")
    validate_candidate_bytes(
        artifact,
        filename=PurePosixPath(artifact_path).name,
        profile_name=str(receipt_value.get("export_profile")),
        policy=policy,
    )


def scan_staged_git_safety(
    *,
    repo: Path,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    root = repo.resolve(strict=True)
    if not (root / ".git").exists():
        # Worktrees use a regular .git pointer file; either form is accepted.
        raise SafetyPolicyError("Git repository metadata is unavailable")
    paths = staged_paths(root)
    blocked_suffixes = tuple(
        str(item).lower() for item in policy["forbidden_content"]["git_blocked_suffixes"]
    )
    receipt_suffix = policy["modes"][EXPORT_MODE]["release_receipt_suffix"]
    release_artifacts: set[str] = set()
    release_receipts: set[str] = set()

    for relative_path in paths:
        if not _is_safe_relative_git_path(relative_path):
            raise SafetyPolicyError("Git index contains an unsafe path")
        index_mode = _index_mode(root, relative_path)
        if index_mode not in {"100644", "100755"}:
            raise SafetyPolicyError("Symlinks, submodules, and non-regular index entries are prohibited")
        if _under_release_root(relative_path, policy) and index_mode != "100644":
            raise SafetyPolicyError("Reviewed export artifacts must be regular non-executable files")
        lower = relative_path.lower()
        if lower.endswith(HARD_BLOCKED_TEXT_SUFFIXES):
            raise SafetyPolicyError("Git index contains a restricted row/log artifact suffix")
        if RESTRICTED_ARTIFACT_BASENAME_RE.search(relative_path):
            raise SafetyPolicyError("Git index contains a restricted analysis artifact filename")
        if (
            CLOUD_CREDENTIAL_BASENAME_RE.search(relative_path)
            or CLOUD_CREDENTIAL_DIRECTORY_RE.search(relative_path)
        ):
            raise SafetyPolicyError("Git index contains a cloud credential/session artifact filename")
        if lower.endswith(blocked_suffixes):
            raise SafetyPolicyError("Git index contains a prohibited restricted-artifact suffix")
        payload = _index_blob(root, relative_path)
        if b"\x00" in payload:
            raise SafetyPolicyError("Binary staged content is not allowlisted by this export policy")
        _assert_high_confidence_text_safety(payload, relative_path)
        suffix = PurePosixPath(relative_path).suffix.lower()
        if suffix in {".csv", ".tsv", ".tab"}:
            _assert_global_table_safety(payload, suffix, policy)
        elif suffix == ".json":
            _assert_global_json_safety(payload, policy)
        if _under_release_root(relative_path, policy):
            if relative_path.endswith(receipt_suffix):
                release_receipts.add(relative_path)
            else:
                release_artifacts.add(relative_path)

    for artifact_path in sorted(release_artifacts):
        _validate_release_pair(
            repo=root,
            artifact_path=artifact_path,
            policy=policy,
            policy_sha256=policy_sha256,
        )
    for receipt_path in sorted(release_receipts):
        artifact_path = receipt_path[: -len(receipt_suffix)]
        if not _index_contains(root, artifact_path):
            raise SafetyPolicyError("Release receipt is orphaned from its aggregate artifact")
        _validate_release_pair(
            repo=root,
            artifact_path=artifact_path,
            policy=policy,
            policy_sha256=policy_sha256,
        )

    return {
        "status": "PASS",
        "mode": EXPORT_MODE,
        "staged_files_checked": len(paths),
        "approved_export_artifacts_checked": len(release_artifacts | {
            item[: -len(receipt_suffix)] for item in release_receipts
        }),
        "restricted_artifacts_permitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--repo", type=Path, default=repository_root())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        policy, policy_sha = load_policy(args.policy)
        result = scan_staged_git_safety(
            repo=args.repo,
            policy=policy,
            policy_sha256=policy_sha,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_GIT_EXPORT_SAFETY",
                    "error_type": type(exc).__name__,
                    "sensitive_details_emitted": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
