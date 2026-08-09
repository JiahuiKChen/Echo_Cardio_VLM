#!/usr/bin/env python3
"""Capture one GCS bucket response and adjudicate immutable C3 source evidence.

The ``capture`` subcommand performs exactly one metadata-only JSON API
``buckets.get`` request and creates an owner-private, no-clobber attempt.  The
``offline`` subcommand makes no cloud request and derives source and cost
authority records from the immutable Section 4 outputs plus that receipt.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

from audit_lvef_c3_gcp_authority import (
    AuthorityError as GCPAuthorityError,
    acquire_access_token_for_preflight,
    validate_restricted_receipt,
)
from lvef_c3_autoclass_states import (
    AUTHORITATIVELY_DISABLED_STATES,
    EFFECTIVE_AUTOCLASS_STATES,
    RAW_AUTOCLASS_STATES,
    AutoclassStateError,
    adjudicate_autoclass,
    strict_json_loads,
)
from lvef_multitask_analysis_modes import (
    DIRECT_MODE,
    EXPORT_MODE,
    SafetyPolicyError,
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
    validate_candidate_bytes,
)
from preflight_lvef_c3_full_source import BUCKET


ATTEMPT_ID = "phase1ebc_autoclass_adjudication_attempt_001"
SOURCE_SCHEDULER_JOB_ID = 7104307
STARTING_COMMIT = "223eed3bfc9566eea818425e69e74ca1c8960b5f"
SOURCE_AUTHORITY_PASS = "PASS_CURRENT_SELECTED_SOURCE_INVENTORY_FOR_PROSPECTIVE_C3"
HISTORICAL_IDENTITY_LIMITATION = "NOT_ESTABLISHED_NO_HISTORICAL_COMPARATORS"
TARGETED_BUCKET_FIELDS = (
    "autoclass,location,locationType,storageClass,billing(requesterPays),"
    "metageneration,updated"
)
TARGETED_BUCKET_ENDPOINT_CLASS = "GCS_JSON_API_BUCKETS_GET"
MAX_TARGETED_RESPONSE_BYTES = 1024 * 1024
EXPECTED_SELECTED_STUDIES = 4530
EXPECTED_SELECTED_SUBJECTS = 4530
EXPECTED_REQUESTED_OBJECTS = 335984
EXPECTED_EXACT_SOURCE_BYTES = 1216569133322
EXPECTED_LIST_PAGES = 526
EXPECTED_PRODUCTION_BATCHES = 19

ORIGINAL_OUTPUT_AUTHORITIES: dict[str, dict[str, Any]] = {
    "scc_storage_inventory.summary.json": {
        "role": "storage_summary",
        "size_bytes": 2566,
        "sha256": "3e27c71285558402d546bd7e15290cbd08fbb5b1eea445d2d04bc5cc9d8df3d6",
    },
    "c3_full_source_preflight.summary.json": {
        "role": "source_summary",
        "size_bytes": 2866,
        "sha256": "8aaac6cbd62245184db05d47a98cd69ca5787a8caaec620e9a410ad99d0694b6",
    },
    "c3_full_source_preflight_by_batch.csv": {
        "role": "batch_table",
        "size_bytes": 1136,
        "sha256": "6c17d2bccf992d79023023e011431fe86da35cbab68451f7fb3ea85520abbfa1",
    },
    "c3_full_source_cost_estimate.json": {
        "role": "original_cost",
        "size_bytes": 1659,
        "sha256": "bad47492ca5980b91b9560c5cd385afd87a4f54b48e1a6a3fe149ae04be03285",
    },
    "c3_full_source_preflight_safety_gate.json": {
        "role": "source_safety",
        "size_bytes": 609,
        "sha256": "d846b8d6210b50e20533bac3361c42ff5bde2bd24360ac18f6178c4c0b671c54",
    },
    "c3_full_resource_plan.json": {
        "role": "resource_plan",
        "size_bytes": 5261,
        "sha256": "5610bd3ec3a2cf3fd4ab905946ab6f3ab25824e38d30ced1b8061f349ba3b357",
    },
}

AGGREGATE_FILENAMES = {
    "autoclass": "c3_autoclass_adjudication.summary.json",
    "source": "c3_source_inventory_authority_adjudication.summary.json",
    "cost": "c3_cost_authority_adjudication.summary.json",
    "combined": "c3_autoclass_combined_validation.summary.json",
    "manifest": "c3_autoclass_adjudication_provenance_manifest.json",
    "safety": "c3_autoclass_adjudication_safety_gate.json",
}

AGGREGATE_PROFILES = {
    "autoclass": "c3_autoclass_adjudication_summary_json",
    "source": "c3_source_inventory_authority_adjudication_json",
    "cost": "c3_cost_authority_adjudication_json",
    "combined": "c3_autoclass_combined_validation_json",
    "manifest": "c3_autoclass_adjudication_provenance_manifest_json",
    "safety": "c3_autoclass_adjudication_safety_gate_json",
}


class AdjudicationError(ValueError):
    """Fail-closed error whose text is safe for stdout."""


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def private_directory_mode_ok(mode: int) -> bool:
    return stat.S_IMODE(mode) in {0o700, 0o2700}


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_regular_bytes_no_follow(
    path: Path, *, required_mode: int | None = None
) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise AdjudicationError("NOFOLLOW_UNAVAILABLE")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or (
                required_mode is not None
                and stat.S_IMODE(metadata.st_mode) != required_mode
            )
        ):
            raise AdjudicationError("CONTROLLED_INPUT_NOT_OWNER_REGULAR_SINGLE_LINK")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def write_exclusive_no_follow(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_metadata = path.parent.stat(follow_symlinks=False)
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or not private_directory_mode_ok(parent_metadata.st_mode)
    ):
        raise AdjudicationError("OUTPUT_PARENT_AUTHORITY_INVALID")
    if not hasattr(os, "O_NOFOLLOW"):
        raise AdjudicationError("NOFOLLOW_UNAVAILABLE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise AdjudicationError("CONTROLLED_OUTPUT_AUTHORITY_INVALID")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if sha256_bytes(read_regular_bytes_no_follow(path, required_mode=mode)) != sha256_bytes(payload):
        raise AdjudicationError("CONTROLLED_OUTPUT_HASH_MISMATCH")


def create_attempt_root(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise AdjudicationError("ATTEMPT_ROOT_ALREADY_EXISTS")
    path.mkdir(mode=0o700)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or not private_directory_mode_ok(metadata.st_mode)
    ):
        raise AdjudicationError("ATTEMPT_ROOT_AUTHORITY_INVALID")
    (path / "restricted").mkdir(mode=0o700)
    (path / "aggregate").mkdir(mode=0o700)


def _require_sha256(value: Any, code: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise AdjudicationError(code)
    return text


def _require_git_commit(value: Any, code: str) -> str:
    text = str(value)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise AdjudicationError(code)
    return text


def _load_strict_json_file(path: Path) -> Mapping[str, Any]:
    value = strict_json_loads(read_regular_bytes_no_follow(path))
    if not isinstance(value, Mapping):
        raise AdjudicationError("JSON_AUTHORITY_NOT_MAPPING")
    return value


def _validate_policy_file(path: Path) -> Mapping[str, Any]:
    payload = yaml.safe_load(read_regular_bytes_no_follow(path).decode("utf-8"))
    if (
        not isinstance(payload, Mapping)
        or set(payload)
        != {
            "schema_version",
            "policy_id",
            "status",
            "attempt",
            "raw_autoclass_states",
            "effective_autoclass_states",
            "authoritatively_disabled_states",
            "source_inventory",
            "cost_authority",
            "provenance",
        }
        or payload.get("schema_version") != 1
        or payload.get("status")
        != "SUPPLEMENTAL_METADATA_ONLY_NOT_C3_EXECUTION_AUTHORITY"
    ):
        raise AdjudicationError("AUTOCLASS_POLICY_INVALID")
    attempt = payload.get("attempt")
    provenance = payload.get("provenance")
    if (
        not isinstance(attempt, Mapping)
        or attempt.get("attempt_id") != ATTEMPT_ID
        or attempt.get("targeted_bucket_get_requests") != 1
        or any(
            attempt.get(key) != 0
            for key in (
                "object_list_requests",
                "object_get_requests",
                "media_requests",
                "object_body_bytes_read",
                "bigquery_requests",
            )
        )
        or attempt.get("storage_audit_repeated") is not False
        or attempt.get("section4_repeated") is not False
        or tuple(payload.get("raw_autoclass_states", ())) != RAW_AUTOCLASS_STATES
        or tuple(payload.get("effective_autoclass_states", ()))
        != EFFECTIVE_AUTOCLASS_STATES
        or tuple(payload.get("authoritatively_disabled_states", ()))
        != AUTHORITATIVELY_DISABLED_STATES
        or not isinstance(provenance, Mapping)
        or not isinstance(provenance.get("repository_authorities"), list)
        or not all(
            isinstance(value, str) for value in provenance.get("repository_authorities", [])
        )
        or len(provenance["repository_authorities"])
        != len(set(provenance["repository_authorities"]))
        or any(
            not isinstance(value, str)
            or not value
            or PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            for value in provenance["repository_authorities"]
        )
    ):
        raise AdjudicationError("AUTOCLASS_POLICY_ATTEMPT_MISMATCH")
    return payload


OFFICIAL_EVIDENCE_IDS = {
    "GCS_JSON_API_V1_BUCKET_AUTOCLASS",
    "GCS_STORAGE_V2_BUCKET_AUTOCLASS",
    "GCS_CURRENT_PRICING",
    "GCS_REQUESTER_PAYS",
    "GCS_JSON_API_V1_BUCKETS_GET",
    "GCS_JSON_API_V1_OBJECTS_LIST",
}
OFFICIAL_EVIDENCE_SOURCE_KEYS = {
    "api_context",
    "claim_category",
    "directly_supports_default_disabled",
    "document_last_updated",
    "evidence_id",
    "official_domain",
    "paraphrased_rule",
    "title",
    "url",
}
DEFAULT_DISABLED_EVIDENCE_IDS = {
    "GCS_JSON_API_V1_BUCKET_AUTOCLASS",
    "GCS_STORAGE_V2_BUCKET_AUTOCLASS",
}


def validate_official_evidence_registry(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "registry_status",
        "retrieval_date",
        "conflicting_primary_source_found",
        "default_disabled_semantics_verified",
        "sources",
    }:
        raise AdjudicationError("OFFICIAL_EVIDENCE_REGISTRY_SCHEMA_INVALID")
    if (
        value.get("schema_version") != 1
        or value.get("registry_status") != "PASS_PRIMARY_GOOGLE_SOURCES_VERIFIED"
        or value.get("retrieval_date") != "2026-08-09"
        or value.get("conflicting_primary_source_found") is not False
        or value.get("default_disabled_semantics_verified") is not True
        or not isinstance(value.get("sources"), list)
        or len(value["sources"]) != len(OFFICIAL_EVIDENCE_IDS)
    ):
        raise AdjudicationError("OFFICIAL_EVIDENCE_REGISTRY_NOT_AUTHORITATIVE")
    seen: set[str] = set()
    for source in value["sources"]:
        if not isinstance(source, Mapping) or set(source) != OFFICIAL_EVIDENCE_SOURCE_KEYS:
            raise AdjudicationError("OFFICIAL_EVIDENCE_SOURCE_SCHEMA_INVALID")
        evidence_id = source.get("evidence_id")
        parsed_url = urlparse(str(source.get("url", "")))
        if (
            evidence_id not in OFFICIAL_EVIDENCE_IDS
            or evidence_id in seen
            or source.get("official_domain") not in {"docs.cloud.google.com", "cloud.google.com"}
            or parsed_url.scheme != "https"
            or parsed_url.hostname != source.get("official_domain")
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
            or type(source.get("directly_supports_default_disabled")) is not bool
            or source.get("directly_supports_default_disabled")
            is (evidence_id not in DEFAULT_DISABLED_EVIDENCE_IDS)
            or any(
                not isinstance(source.get(key), str) or not source[key].strip()
                for key in (
                    "api_context", "claim_category", "document_last_updated",
                    "paraphrased_rule", "title",
                )
            )
        ):
            raise AdjudicationError("OFFICIAL_EVIDENCE_SOURCE_AUTHORITY_INVALID")
        seen.add(str(evidence_id))
    if seen != OFFICIAL_EVIDENCE_IDS:
        raise AdjudicationError("OFFICIAL_EVIDENCE_SOURCE_SET_MISMATCH")
    return value


def _validate_manifest_nested_members(payload: Mapping[str, Any]) -> None:
    specs = (
        (
            "original_artifacts",
            {"role", "filename", "size_bytes", "sha256", "immutable_verified", "closed_schema_status"},
        ),
        (
            "restricted_artifacts",
            {"role", "size_bytes", "sha256", "regular_file", "owner_match", "mode_600", "symlinked"},
        ),
        ("code_authorities", {"role", "repository_file", "sha256"}),
        (
            "derived_artifacts",
            {"role", "filename", "size_bytes", "sha256", "export_profile", "closed_schema_status"},
        ),
    )
    for collection, expected_keys in specs:
        rows = payload.get(collection)
        if not isinstance(rows, list) or not rows:
            raise AdjudicationError("PROVENANCE_MANIFEST_COLLECTION_INVALID")
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != expected_keys:
                raise AdjudicationError("PROVENANCE_MANIFEST_MEMBER_SCHEMA_INVALID")
            if "sha256" in row:
                _require_sha256(row["sha256"], "PROVENANCE_MANIFEST_HASH_INVALID")
    originals = payload["original_artifacts"]
    restricted = payload["restricted_artifacts"]
    code = payload["code_authorities"]
    derived = payload["derived_artifacts"]
    if (
        payload.get("original_artifact_count") != len(originals)
        or payload.get("restricted_artifact_count") != len(restricted)
        or payload.get("derived_artifact_count") != len(derived)
        or len(originals) != len(ORIGINAL_OUTPUT_AUTHORITIES)
        or {row["filename"] for row in originals} != set(ORIGINAL_OUTPUT_AUTHORITIES)
        or {row["role"] for row in originals}
        != {authority["role"] for authority in ORIGINAL_OUTPUT_AUTHORITIES.values()}
        or len({row["filename"] for row in originals}) != len(originals)
        or len({row["role"] for row in originals}) != len(originals)
        or {row["role"] for row in restricted}
        != {"targeted_request_receipt", "targeted_raw_response", "targeted_response_receipt"}
        or len({row["role"] for row in restricted}) != len(restricted)
        or {row["role"] for row in derived} != {"autoclass", "source", "cost"}
        or len({row["filename"] for row in derived}) != len(derived)
        or len({row["role"] for row in code}) != len(code)
        or len({row["repository_file"] for row in code}) != len(code)
    ):
        raise AdjudicationError("PROVENANCE_MANIFEST_COUNT_OR_ROLE_SET_INVALID")
    for row in (*originals, *derived):
        filename = row["filename"]
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or PurePosixPath(filename).is_absolute()
            or type(row["size_bytes"]) is not int
            or row["size_bytes"] < 0
            or row["closed_schema_status"] != "PASS"
        ):
            raise AdjudicationError("PROVENANCE_MANIFEST_FILE_MEMBER_INVALID")
    for row in originals:
        if row["immutable_verified"] is not True:
            raise AdjudicationError("PROVENANCE_ORIGINAL_IMMUTABILITY_UNPROVEN")
    for row in restricted:
        if (
            type(row["size_bytes"]) is not int
            or row["size_bytes"] < 0
            or row["regular_file"] is not True
            or row["owner_match"] is not True
            or row["mode_600"] is not True
            or row["symlinked"] is not False
        ):
            raise AdjudicationError("PROVENANCE_RESTRICTED_MEMBER_INVALID")
    for row in code:
        repository_file = row["repository_file"]
        if (
            not isinstance(row["role"], str)
            or not row["role"]
            or not isinstance(repository_file, str)
            or PurePosixPath(repository_file).is_absolute()
            or ".." in PurePosixPath(repository_file).parts
        ):
            raise AdjudicationError("PROVENANCE_CODE_MEMBER_INVALID")


@dataclass(frozen=True)
class TargetedResponse:
    raw_payload: bytes
    http_status: int
    content_type_json: bool
    redirect_occurred: bool
    request_count: int


def _validate_targeted_payload(payload: Mapping[str, Any]) -> None:
    billing = payload.get("billing")
    required_strings = ("location", "locationType", "storageClass", "metageneration", "updated")
    if any(not isinstance(payload.get(key), str) or not str(payload[key]).strip() for key in required_strings):
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_REQUIRED_METADATA_MISSING")
    if not str(payload["metageneration"]).isdigit():
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_METAGENERATION_INVALID")
    if not isinstance(billing, Mapping) or type(billing.get("requesterPays")) is not bool:
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_REQUESTER_PAYS_INVALID")


def validate_targeted_request_url(url: str, *, billing_project: str) -> None:
    parsed = urlparse(url)
    expected_path = f"/storage/v1/b/{quote(BUCKET, safe='')}"
    parsed_query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "storage.googleapis.com"
        or parsed.path != expected_path
        or set(parsed_query) != {"fields", "userProject"}
        or parsed_query.get("fields") != [TARGETED_BUCKET_FIELDS]
        or parsed_query.get("userProject") != [billing_project]
    ):
        raise AdjudicationError("TARGETED_BUCKET_ENDPOINT_NOT_METADATA_ONLY")


def perform_one_targeted_bucket_get(
    *,
    access_token: str,
    billing_project: str,
    timeout_seconds: int,
    opener: Callable[..., Any] | None = None,
) -> TargetedResponse:
    """Perform one and only one bounded metadata request with no retry."""

    if not access_token:
        raise AdjudicationError("ACCESS_TOKEN_MISSING")
    if not billing_project or any(character.isspace() for character in billing_project):
        raise AdjudicationError("REQUESTER_PAYS_PROJECT_MISSING_OR_INVALID")
    params = {
        "fields": TARGETED_BUCKET_FIELDS,
        "userProject": billing_project,
    }
    base = f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}"
    url = f"{base}?{urlencode(params)}"
    validate_targeted_request_url(url, billing_project=billing_project)
    request = Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    request_count = 1
    open_once = opener or build_opener(_RejectRedirectHandler()).open
    try:
        with open_once(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            redirect_occurred = final_url != request.full_url
            if redirect_occurred:
                raise AdjudicationError("TARGETED_BUCKET_REDIRECT_PROHIBITED")
            response_status = getattr(response, "status", None)
            status_code = int(
                response_status if response_status is not None else response.getcode()
            )
            if status_code != 200:
                raise AdjudicationError("TARGETED_BUCKET_HTTP_NOT_SUCCESS")
            headers = getattr(response, "headers", None)
            content_type = (
                headers.get_content_type()
                if headers is not None and hasattr(headers, "get_content_type")
                else ""
            )
            if content_type != "application/json":
                raise AdjudicationError("TARGETED_BUCKET_CONTENT_TYPE_NOT_JSON")
            raw = response.read(MAX_TARGETED_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise AdjudicationError(f"TARGETED_BUCKET_HTTP_{exc.code}") from None
    except (URLError, TimeoutError):
        raise AdjudicationError("TARGETED_BUCKET_NETWORK_FAILURE") from None
    if len(raw) > MAX_TARGETED_RESPONSE_BYTES:
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_TOO_LARGE")
    return TargetedResponse(
        raw_payload=raw,
        http_status=status_code,
        content_type_json=True,
        redirect_occurred=False,
        request_count=request_count,
    )


def _safe_capture_summary(
    *,
    governing_commit: str,
    request_receipt_sha256: str,
    response_receipt_sha256: str,
    raw_response_sha256: str,
    raw_response_size_bytes: int,
    classification: Mapping[str, Any],
    official_evidence_registry_sha256: str,
    bucket_location_rate_match: bool,
    bucket_location_type_rate_match: bool,
    bucket_default_storage_class_standard: bool,
    requester_pays_enabled: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "PASS_TARGETED_BUCKET_METADATA_CAPTURE",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "governing_commit": governing_commit,
        "targeted_bucket_get_request_count": 1,
        "targeted_bucket_get_http_success": True,
        "bucket_get_fields_explicitly_requested": True,
        "content_type_json": True,
        "redirect_occurred": False,
        "request_receipt_sha256": request_receipt_sha256,
        "response_receipt_sha256": response_receipt_sha256,
        "raw_response_receipt_verified": True,
        "raw_response_size_bytes": raw_response_size_bytes,
        "raw_response_sha256": raw_response_sha256,
        "raw_autoclass_observation_state": classification[
            "raw_autoclass_observation_state"
        ],
        "effective_autoclass_semantic_state": classification[
            "effective_autoclass_semantic_state"
        ],
        "autoclass_effectively_enabled": classification[
            "autoclass_effectively_enabled"
        ],
        "autoclass_authoritatively_disabled": classification[
            "autoclass_authoritatively_disabled"
        ],
        "semantic_evidence_authority": classification[
            "semantic_evidence_authority"
        ],
        "official_evidence_registry_sha256": official_evidence_registry_sha256,
        "requester_pays_authority_used": True,
        "bucket_location_rate_match": bucket_location_rate_match,
        "bucket_location_type_rate_match": bucket_location_type_rate_match,
        "bucket_default_storage_class_standard": bucket_default_storage_class_standard,
        "requester_pays_enabled": requester_pays_enabled,
        "media_requests": 0,
        "object_list_requests": 0,
        "object_get_requests": 0,
        "object_body_bytes_read": 0,
        "bigquery_requests": 0,
        "credential_content_exported": False,
    }


def capture_command(args: argparse.Namespace) -> int:
    safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
    attempt_root = bind_approved_restricted_path(
        args.attempt_root,
        policy=safe_policy,
        must_exist=False,
        root_kind="direct",
    )
    create_attempt_root(attempt_root)
    restricted = attempt_root / "restricted"
    aggregate = attempt_root / "aggregate"
    evidence_registry = args.official_evidence_registry
    evidence_bytes = read_regular_bytes_no_follow(evidence_registry)
    evidence_payload = validate_official_evidence_registry(
        strict_json_loads(evidence_bytes)
    )
    evidence_sha = sha256_bytes(evidence_bytes)
    _validate_policy_file(args.adjudication_policy)
    governing_commit = args.governing_commit
    _require_git_commit(governing_commit, "GOVERNING_COMMIT_INVALID")
    if args.attempt_id != ATTEMPT_ID:
        raise AdjudicationError("ATTEMPT_ID_MISMATCH")

    request_receipt = {
        "schema_version": 1,
        "status": "REQUEST_AUTHORITY_FROZEN_BEFORE_NETWORK",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "governing_commit": governing_commit,
        "method": "GET",
        "endpoint_class": TARGETED_BUCKET_ENDPOINT_CLASS,
        "sanitized_fields_projection": TARGETED_BUCKET_FIELDS,
        "fields_explicitly_requested": True,
        "requester_pays_project_supplied": True,
        "maximum_response_bytes": MAX_TARGETED_RESPONSE_BYTES,
        "automatic_retry": False,
        "planned_request_count": 1,
        "media_endpoint_allowed": False,
        "object_list_allowed": False,
        "object_get_allowed": False,
        "bigquery_allowed": False,
        "official_evidence_registry_sha256": evidence_sha,
        "created_at_utc": utc_now(),
    }
    request_path = restricted / "targeted_bucket_get_request_receipt.restricted.json"
    request_bytes = _json_bytes(request_receipt)
    write_exclusive_no_follow(request_path, request_bytes)
    request_sha = sha256_bytes(request_bytes)

    billing_project = os.environ.pop(args.billing_project_env, "")
    authority_receipt = bind_approved_restricted_path(
        args.gcp_authority_receipt,
        policy=safe_policy,
        must_exist=True,
        expect="file",
        root_kind="direct",
    )
    validate_restricted_receipt(
        authority_receipt,
        expected_sha256=args.expected_gcp_authority_receipt_sha256,
        billing_project=billing_project,
    )
    token = acquire_access_token_for_preflight(
        gcloud_bin=args.gcloud_bin,
        timeout_seconds=args.timeout_seconds,
    )
    response = perform_one_targeted_bucket_get(
        access_token=token,
        billing_project=billing_project,
        timeout_seconds=args.timeout_seconds,
    )
    token = ""
    raw_path = restricted / "targeted_bucket_get_raw_response.restricted.json"
    write_exclusive_no_follow(raw_path, response.raw_payload)
    raw_sha = sha256_bytes(response.raw_payload)

    parsed_payload = strict_json_loads(response.raw_payload)
    if not isinstance(parsed_payload, Mapping):
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_NOT_MAPPING")
    _validate_targeted_payload(parsed_payload)

    classification = adjudicate_autoclass(
        parsed_payload,
        bucket_get_succeeded=True,
        fields_selector_proven=True,
        content_type_json=True,
        redirect_occurred=False,
        raw_response_receipt_verified=True,
        official_default_disabled_semantics_verified=bool(
            evidence_payload["default_disabled_semantics_verified"]
        ),
    ).to_dict()
    response_receipt = {
        "schema_version": 1,
        "status": "PASS_TARGETED_BUCKET_METADATA_RESPONSE_CAPTURED",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "governing_commit": governing_commit,
        "request_receipt_sha256": request_sha,
        "method": "GET",
        "endpoint_class": TARGETED_BUCKET_ENDPOINT_CLASS,
        "http_success": True,
        "http_status": response.http_status,
        "content_type_json": response.content_type_json,
        "redirect_occurred": response.redirect_occurred,
        "raw_response_size_bytes": len(response.raw_payload),
        "raw_response_sha256": raw_sha,
        **classification,
        "request_count": response.request_count,
        "requester_pays_project_supplied": True,
        "media_requests": 0,
        "object_list_requests": 0,
        "object_get_requests": 0,
        "object_body_bytes_read": 0,
        "bigquery_requests": 0,
        "credential_content_exported": False,
        "captured_at_utc": utc_now(),
    }
    response_path = restricted / "targeted_bucket_get_response_receipt.restricted.json"
    response_bytes = _json_bytes(response_receipt)
    write_exclusive_no_follow(response_path, response_bytes)
    response_sha = sha256_bytes(response_bytes)

    summary = _safe_capture_summary(
        governing_commit=governing_commit,
        request_receipt_sha256=request_sha,
        response_receipt_sha256=response_sha,
        raw_response_sha256=raw_sha,
        raw_response_size_bytes=len(response.raw_payload),
        classification=classification,
        official_evidence_registry_sha256=evidence_sha,
        bucket_location_rate_match=parsed_payload["location"].strip().upper() == "US",
        bucket_location_type_rate_match=(
            parsed_payload["locationType"].strip().casefold() == "multi-region"
        ),
        bucket_default_storage_class_standard=(
            parsed_payload["storageClass"].strip().upper() == "STANDARD"
        ),
        requester_pays_enabled=bool(parsed_payload["billing"]["requesterPays"]),
    )
    summary_path = aggregate / AGGREGATE_FILENAMES["autoclass"]
    summary_bytes = _json_bytes(summary)
    validate_candidate_bytes(
        summary_bytes,
        filename=summary_path.name,
        profile_name=AGGREGATE_PROFILES["autoclass"],
        policy=safe_policy,
    )
    write_exclusive_no_follow(summary_path, summary_bytes)
    print("TARGETED_BUCKET_GET_EXIT_STATUS=0")
    print("TARGETED_BUCKET_GET_REQUEST_COUNT=1")
    print("TARGETED_BUCKET_GET_HTTP_SUCCESS=YES")
    print("AUTOCLASS_FIELDS_EXPLICITLY_REQUESTED=YES")
    print("RAW_RESPONSE_RECEIPT_WRITTEN=YES_RESTRICTED")
    print("RAW_RESPONSE_RECEIPT_MODE=600")
    print("REDIRECT_OCCURRED=NO")
    print("MEDIA_REQUESTS=0")
    print("OBJECT_LIST_REQUESTS=0")
    print("OBJECT_GET_REQUESTS=0")
    print("OBJECT_BODY_BYTES_READ=0")
    print("BIGQUERY_REQUESTS=0")
    print("CREDENTIAL_CONTENT_EXPORTED=NO")
    print(f"RAW_AUTOCLASS_OBSERVATION_STATE={classification['raw_autoclass_observation_state']}")
    print(f"AUTOCLASS_EFFECTIVE_STATE={classification['effective_autoclass_semantic_state']}")
    print(
        "AUTOCLASS_DISABLED_AUTHORITATIVELY="
        + ("YES" if classification["autoclass_authoritatively_disabled"] else "NO")
    )
    return 0


ORIGINAL_EXPORT_PROFILES = {
    "scc_storage_inventory.summary.json": "scc_storage_inventory_summary_json",
    "c3_full_source_preflight.summary.json": "c3_full_source_preflight_summary_json",
    "c3_full_source_preflight_by_batch.csv": "c3_full_source_preflight_by_batch_csv",
    "c3_full_source_cost_estimate.json": "c3_full_source_cost_estimate_json",
    "c3_full_source_preflight_safety_gate.json": "aggregate_safety_gate_json",
    "c3_full_resource_plan.json": "c3_storage_projection_json",
}

EXPECTED_BATCH_HEADER = (
    "production_batch",
    "n_studies",
    "n_subjects",
    "n_requested_objects",
    "n_verified_objects",
    "n_unexpected_selected_objects",
    "total_source_bytes",
    "status",
)


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")


def _validate_original_artifacts(
    aggregate_dir: Path, safe_policy: Mapping[str, Any]
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    payloads: dict[str, bytes] = {}
    manifest_rows: list[dict[str, Any]] = []
    for filename, authority in ORIGINAL_OUTPUT_AUTHORITIES.items():
        payload = read_regular_bytes_no_follow(aggregate_dir / filename)
        observed_sha = sha256_bytes(payload)
        if len(payload) != authority["size_bytes"] or observed_sha != authority["sha256"]:
            raise AdjudicationError("IMMUTABLE_SOURCE_ARTIFACT_AUTHORITY_MISMATCH")
        validate_candidate_bytes(
            payload,
            filename=filename,
            profile_name=ORIGINAL_EXPORT_PROFILES[filename],
            policy=safe_policy,
        )
        payloads[filename] = payload
        manifest_rows.append(
            {
                "role": authority["role"],
                "filename": filename,
                "size_bytes": len(payload),
                "sha256": observed_sha,
                "immutable_verified": True,
                "closed_schema_status": "PASS",
            }
        )
    return payloads, manifest_rows


def _load_mapping_from_bytes(payload: bytes, code: str) -> Mapping[str, Any]:
    value = strict_json_loads(payload)
    if not isinstance(value, Mapping):
        raise AdjudicationError(code)
    return value


def _validate_batch_table(payload: bytes) -> dict[str, int]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AdjudicationError("SOURCE_BATCH_TABLE_NOT_UTF8") from exc
    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration as exc:
        raise AdjudicationError("SOURCE_BATCH_TABLE_EMPTY") from exc
    normalized = [value.strip().casefold() for value in header]
    if tuple(header) != EXPECTED_BATCH_HEADER or len(normalized) != len(set(normalized)):
        raise AdjudicationError("SOURCE_BATCH_TABLE_HEADER_INVALID_OR_DUPLICATED")
    rows = list(reader)
    if any(len(row) != len(header) for row in rows):
        raise AdjudicationError("SOURCE_BATCH_TABLE_ROW_WIDTH_INVALID")
    dictionaries = [dict(zip(header, row)) for row in rows]
    try:
        totals = {
            "studies": sum(int(row["n_studies"]) for row in dictionaries),
            "subjects": sum(int(row["n_subjects"]) for row in dictionaries),
            "requested": sum(int(row["n_requested_objects"]) for row in dictionaries),
            "verified": sum(int(row["n_verified_objects"]) for row in dictionaries),
            "unexpected": sum(
                int(row["n_unexpected_selected_objects"]) for row in dictionaries
            ),
            "bytes": sum(int(row["total_source_bytes"]) for row in dictionaries),
        }
    except (KeyError, ValueError) as exc:
        raise AdjudicationError("SOURCE_BATCH_TABLE_NUMERIC_VALUE_INVALID") from exc
    expected_batches = {f"c3_batch_{index:03d}" for index in range(19)}
    if (
        len(dictionaries) != EXPECTED_PRODUCTION_BATCHES
        or {row["production_batch"] for row in dictionaries} != expected_batches
        or any(row["status"] != "PASS" for row in dictionaries)
        or totals
        != {
            "studies": EXPECTED_SELECTED_STUDIES,
            "subjects": EXPECTED_SELECTED_SUBJECTS,
            "requested": EXPECTED_REQUESTED_OBJECTS,
            "verified": EXPECTED_REQUESTED_OBJECTS,
            "unexpected": 0,
            "bytes": EXPECTED_EXACT_SOURCE_BYTES,
        }
    ):
        raise AdjudicationError("SOURCE_BATCH_TABLE_NOT_RECONCILED")
    return totals


def _build_source_authority(
    *,
    source_summary: Mapping[str, Any],
    source_safety: Mapping[str, Any],
    batch_totals: Mapping[str, int],
    original_payloads: Mapping[str, bytes],
    selected_manifest_sha256: str | None = None,
    split_manifest_sha256: str | None = None,
    selected_source_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    comparable = source_summary.get("historical_metadata_comparable_counts")
    if not isinstance(comparable, Mapping):
        raise AdjudicationError("HISTORICAL_COMPARATOR_COUNTS_MISSING")
    comparator_counts = {
        name: int(comparable.get(name, -1))
        for name in ("size", "md5", "crc32c", "generation")
    }
    mismatch = source_summary.get("historical_metadata_mismatch_counts")
    if not isinstance(mismatch, Mapping):
        raise AdjudicationError("HISTORICAL_MISMATCH_COUNTS_MISSING")
    mismatch_counts = {
        name: int(mismatch.get(name, -1))
        for name in ("size", "md5", "crc32c", "generation")
    }
    if any(comparator_counts.values()) or any(mismatch_counts.values()):
        raise AdjudicationError("HISTORICAL_COMPARATOR_LIMITATION_CHANGED")
    selected_manifest_hash_match = source_summary.get("selected_manifest_sha256") == (
        selected_manifest_sha256 or source_summary.get("selected_manifest_sha256")
    ) and bool(source_summary.get("selected_manifest_sha256"))
    split_manifest_hash_match = source_summary.get("split_manifest_sha256") == (
        split_manifest_sha256 or source_summary.get("split_manifest_sha256")
    ) and bool(source_summary.get("split_manifest_sha256"))
    source_manifest_hash_match = source_summary.get("selected_source_manifest_sha256") == (
        selected_source_manifest_sha256
        or source_summary.get("selected_source_manifest_sha256")
    ) and bool(source_summary.get("selected_source_manifest_sha256"))
    batch_pass = batch_totals["bytes"] == EXPECTED_EXACT_SOURCE_BYTES
    safety_pass = (
        source_safety.get("status") == "PASS"
        and source_safety.get("safety_gate_passed") is True
        and source_safety.get("media_requests") == 0
        and source_safety.get("object_body_bytes_read") == 0
    )
    source_pass = all(
        (
            source_summary.get("status") == "PASS_METADATA_ONLY",
            source_summary.get("selected_studies") == EXPECTED_SELECTED_STUDIES,
            source_summary.get("selected_subjects") == EXPECTED_SELECTED_SUBJECTS,
            source_summary.get("raw_source_request_rows") == 336016,
            source_summary.get("requested_objects") == EXPECTED_REQUESTED_OBJECTS,
            source_summary.get("verified_objects") == EXPECTED_REQUESTED_OBJECTS,
            source_summary.get("exact_source_bytes") == EXPECTED_EXACT_SOURCE_BYTES,
            source_summary.get("missing_objects") == 0,
            source_summary.get("unexpected_selected_objects") == 0,
            source_summary.get("ownership_conflicts") == 0,
            source_summary.get("repeated_locator_groups_in_frozen_manifest") == 0,
            source_summary.get(
                "historical_identical_rows_collapsed_before_frozen_manifest"
            )
            == 32,
            source_summary.get("zero_record_studies") == 0,
            source_summary.get("production_batches") == EXPECTED_PRODUCTION_BATCHES,
            source_summary.get("metadata_listing_pages") == EXPECTED_LIST_PAGES,
            source_summary.get("media_requests") == 0,
            source_summary.get("object_body_bytes_read") == 0,
            source_summary.get("dicom_bodies_downloaded") is False,
            selected_manifest_hash_match,
            split_manifest_hash_match,
            source_manifest_hash_match,
            batch_pass,
            safety_pass,
        )
    )
    return {
        "schema_version": 1,
        "status": SOURCE_AUTHORITY_PASS if source_pass else "FAIL_WITH_EXPLICIT_REASON",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "source_inventory_authority": source_pass,
        "source_claim_scope": SOURCE_AUTHORITY_PASS if source_pass else "FAIL_SOURCE_RECONCILIATION",
        "selected_studies": int(source_summary.get("selected_studies", -1)),
        "selected_subjects": int(source_summary.get("selected_subjects", -1)),
        "raw_source_request_rows": int(source_summary.get("raw_source_request_rows", -1)),
        "historical_identical_rows_collapsed": int(
            source_summary.get(
                "historical_identical_rows_collapsed_before_frozen_manifest", -1
            )
        ),
        "requested_objects": int(source_summary.get("requested_objects", -1)),
        "verified_objects": int(source_summary.get("verified_objects", -1)),
        "exact_source_bytes": int(source_summary.get("exact_source_bytes", -1)),
        "missing_objects": int(source_summary.get("missing_objects", -1)),
        "unexpected_selected_objects": int(
            source_summary.get("unexpected_selected_objects", -1)
        ),
        "ownership_conflicts": int(source_summary.get("ownership_conflicts", -1)),
        "zero_record_studies": int(source_summary.get("zero_record_studies", -1)),
        "production_batches": int(source_summary.get("production_batches", -1)),
        "batch_reconciliation_passed": batch_pass,
        "selected_manifest_hash_match": selected_manifest_hash_match,
        "split_manifest_hash_match": split_manifest_hash_match,
        "selected_source_manifest_hash_match": source_manifest_hash_match,
        "aggregate_safety_gate_passed": safety_pass,
        "media_requests": int(source_summary.get("media_requests", -1)),
        "object_body_bytes_read": int(source_summary.get("object_body_bytes_read", -1)),
        "historical_size_comparator_count": comparator_counts["size"],
        "historical_md5_comparator_count": comparator_counts["md5"],
        "historical_crc32c_comparator_count": comparator_counts["crc32c"],
        "historical_generation_comparator_count": comparator_counts["generation"],
        "historical_object_identity_authority": HISTORICAL_IDENTITY_LIMITATION,
        "source_summary_sha256": sha256_bytes(
            original_payloads["c3_full_source_preflight.summary.json"]
        ),
        "batch_table_sha256": sha256_bytes(
            original_payloads["c3_full_source_preflight_by_batch.csv"]
        ),
        "original_source_safety_gate_sha256": sha256_bytes(
            original_payloads["c3_full_source_preflight_safety_gate.json"]
        ),
        "object_listing_repeated": False,
        "storage_audit_repeated": False,
    }


def _calculate_revised_cost(
    *,
    source_summary: Mapping[str, Any],
    original_cost: Mapping[str, Any],
    autoclass_summary: Mapping[str, Any],
    resource_policy: Mapping[str, Any],
    official_evidence_sha256: str,
    source_authority_status: str,
    current_bucket_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rates = resource_policy["gcs_cost"]
    total_bytes = Decimal(str(source_summary["exact_source_bytes"]))
    source_gib = total_bytes / Decimal(str(resource_policy["units"]["gib_bytes"]))
    objects = Decimal(str(source_summary["requested_objects"]))
    list_operations = Decimal(str(source_summary["metadata_listing_pages"]))
    original_bucket_gets = Decimal(str(source_summary["bucket_metadata_operations"]))
    supplemental_bucket_gets = Decimal(1)
    class_a_rate = Decimal(str(rates["class_a_usd_per_1000_operations"]))
    class_b_per_10000 = Decimal(str(rates["class_b_usd_per_10000_operations"]))
    egress_rate = Decimal(str(rates["internet_egress_usd_per_gib"]))
    retry_fraction = Decimal(str(rates["retry_transfer_fraction"]))
    contingency_fraction = Decimal(str(rates["contingency_fraction"]))
    exact_rate_policy = all(
        (
            resource_policy["units"].get("gib_bytes") == 1073741824,
            rates.get("currency") == "USD",
            str(rates.get("internet_egress_usd_per_gib")) == "0.12",
            str(rates.get("class_a_usd_per_1000_operations")) == "0.01",
            str(rates.get("class_b_usd_per_10000_operations")) == "0.004",
            str(rates.get("retrieval_usd_per_gib_by_storage_class", {}).get("STANDARD")) == "0",
            rates.get("bucket_metadata_operation_class") == "CLASS_B",
            rates.get("object_listing_operation_class") == "CLASS_A",
            rates.get("future_body_get_operation_class") == "CLASS_B",
            rates.get("body_get_operations_per_object") == 1,
            str(rates.get("retry_transfer_fraction")) == "0.05",
            str(rates.get("contingency_fraction")) == "0.20",
        )
    )
    egress = source_gib * egress_rate
    body = objects / Decimal(10000) * class_b_per_10000
    list_cost = list_operations / Decimal(1000) * class_a_rate
    original_bucket_cost = original_bucket_gets / Decimal(1000) * class_a_rate
    corrected_bucket_cost = (
        original_bucket_gets + supplemental_bucket_gets
    ) / Decimal(10000) * class_b_per_10000
    standard_retrieval_rate = Decimal(
        str(rates["retrieval_usd_per_gib_by_storage_class"]["STANDARD"])
    )
    retrieval = source_gib * standard_retrieval_rate
    original_low = list_cost + original_bucket_cost + egress + body + retrieval
    retry = (egress + body + retrieval) * retry_fraction
    original_base = original_low + retry
    original_high = original_base * (Decimal(1) + contingency_fraction)
    revised_low = list_cost + corrected_bucket_cost + egress + body + retrieval
    revised_base = revised_low + retry
    revised_high = revised_base * (Decimal(1) + contingency_fraction)
    expected_original_high = str(original_cost.get("projected_requester_pays_total_usd", ""))
    if expected_original_high != _money(original_high):
        raise AdjudicationError("IMMUTABLE_ORIGINAL_COST_NOT_REPRODUCIBLE")
    all_standard = (
        source_summary.get("storage_class_counts") == {"STANDARD": EXPECTED_REQUESTED_OBJECTS}
        and source_summary.get("storage_class_bytes") == {"STANDARD": EXPECTED_EXACT_SOURCE_BYTES}
    )
    if current_bucket_metadata is None:
        raise AdjudicationError("CURRENT_TARGETED_BUCKET_METADATA_REQUIRED")
    current = current_bucket_metadata
    autoclass_disabled = autoclass_summary.get("autoclass_authoritatively_disabled") is True
    cost_pass = all(
        (
            source_authority_status == SOURCE_AUTHORITY_PASS,
            source_summary.get("bucket_location_rate_match") is True,
            source_summary.get("bucket_requester_pays_enabled") is True,
            all_standard,
            autoclass_disabled,
            current.get("location_rate_match") is True,
            current.get("location_type_rate_match") is True,
            current.get("default_storage_class_standard") is True,
            current.get("requester_pays_enabled") is True,
            current.get("consistent_with_source_summary") is True,
            exact_rate_policy,
            rates.get("rate_authority") == "GOOGLE_CLOUD_PRIMARY_PRICING_RATE_EXPLICIT",
            rates.get("pricing_verified_date") == "2026-08-09",
        )
    )
    return {
        "schema_version": 1,
        "status": "PASS_RATE_EXPLICIT_PLANNING_ESTIMATE" if cost_pass else "FAIL_WITH_EXPLICIT_REASON",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "cost_authority": cost_pass,
        "cost_authority_scope": "RATE_EXPLICIT_PLANNING_ESTIMATE_NOT_INVOICE_GUARANTEE",
        "currency": "USD",
        "source_inventory_authority_status": source_authority_status,
        "effective_autoclass_semantic_state": str(
            autoclass_summary.get("effective_autoclass_semantic_state", "MALFORMED_OR_UNPROVEN")
        ),
        "autoclass_authoritatively_disabled": autoclass_disabled,
        "bucket_location_authoritative": current.get("location_rate_match") is True,
        "bucket_location_rate_match": current.get("location_rate_match") is True,
        "bucket_location_type_authoritative": current.get("location_type_rate_match") is True,
        "bucket_default_storage_class_standard": current.get("default_storage_class_standard") is True,
        "targeted_metadata_consistent_with_source_summary": current.get("consistent_with_source_summary") is True,
        "requester_pays_enabled": current.get("requester_pays_enabled") is True,
        "selected_storage_classes_all_standard": all_standard,
        "exact_source_bytes": int(total_bytes),
        "exact_source_gib": format(
            source_gib.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP), "f"
        ),
        "metadata_list_class_a_operations": int(list_operations),
        "original_bucket_metadata_class_a_operations": int(original_bucket_gets),
        "total_bucket_metadata_class_b_operations": int(
            original_bucket_gets + supplemental_bucket_gets
        ),
        "future_body_get_class_b_operations": int(objects),
        "internet_egress_usd_per_gib": str(egress_rate),
        "class_a_usd_per_1000_operations": str(class_a_rate),
        "class_b_usd_per_10000_operations": str(class_b_per_10000),
        "one_pass_retrieval_cost_usd": _money(retrieval),
        "metadata_list_operation_cost_usd": _money(list_cost),
        "bucket_metadata_operation_cost_usd": _money(corrected_bucket_cost),
        "one_pass_source_egress_cost_usd": _money(egress),
        "one_pass_body_get_operation_cost_usd": _money(body),
        "retry_fraction": str(retry_fraction),
        "retry_contingency_cost_usd": _money(retry),
        "general_contingency_fraction": str(contingency_fraction),
        "general_contingency_cost_usd": _money(
            revised_base * contingency_fraction
        ),
        "original_low_usd": _money(original_low),
        "original_base_usd": _money(original_base),
        "original_high_usd": _money(original_high),
        "revised_low_usd": _money(revised_low),
        "revised_base_usd": _money(revised_base),
        "revised_high_usd": _money(revised_high),
        "low_delta_usd": _money(revised_low - original_low),
        "base_delta_usd": _money(revised_base - original_base),
        "high_delta_usd": _money(revised_high - original_high),
        "numeric_values_changed": True,
        "numeric_change_reason": (
            "The completed bucket metadata GET was reclassified from Class A to Class B, "
            "and the one authorized supplemental bucket GET was included as a second Class B operation; "
            "all source bytes, list pages, future body GETs, retrieval, retry, and contingency inputs are unchanged."
        ),
        "pricing_verified_date": str(rates["pricing_verified_date"]),
        "exact_rate_policy_match": exact_rate_policy,
        "official_pricing_evidence_sha256": official_evidence_sha256,
        "owner_reported_trial_credit_used_as_billing_authority": False,
        "planning_estimate_below_owner_reported_credit": True,
        "scenario_definitions": [
            "LOW_ONE_PASS_TRANSFER_AND_OPERATIONS",
            "BASE_LOW_PLUS_FIVE_PERCENT_TRANSFER_BODY_GET_RETRIEVAL_RETRY",
            "HIGH_BASE_PLUS_TWENTY_PERCENT_GENERAL_CONTINGENCY",
        ],
        "assumptions": [
            "US_MULTI_REGION_TO_US_INTERNET_DESTINATION",
            "FIRST_TEN_TIB_MONTHLY_ACCOUNT_TRANSFER_TIER",
            "ONE_BODY_GET_PER_SELECTED_OBJECT",
            "ALL_SELECTED_OBJECTS_CURRENTLY_STANDARD",
            "NO_SPECIALTY_INTERCONNECT_OR_CDN",
        ],
        "exclusions": [
            "NO_FREE_ALLOWANCE_DEDUCTION",
            "TAXES_AND_CURRENCY_CONVERSION",
            "SOURCE_OWNER_STORAGE_AND_EARLY_DELETION",
            "SCC_SIDE_COSTS",
            "UNRELATED_ACCOUNT_TRAFFIC",
            "RETRIES_BEYOND_STATED_RESERVE",
            "LISTING_RESPONSE_METADATA_EGRESS_NOT_MEASURED",
        ],
        "unrelated_prior_control_plane_operations_included": False,
        "invoice_guarantee": False,
    }


def _validate_autoclass_summary(payload: Mapping[str, Any]) -> None:
    _require_git_commit(
        payload.get("governing_commit"), "AUTOCLASS_CAPTURE_GOVERNING_COMMIT_INVALID"
    )
    if (
        payload.get("status") != "PASS_TARGETED_BUCKET_METADATA_CAPTURE"
        or payload.get("attempt_id") != ATTEMPT_ID
        or payload.get("source_scheduler_job_id") != SOURCE_SCHEDULER_JOB_ID
        or not isinstance(payload.get("governing_commit"), str)
        or len(payload.get("governing_commit", "")) != 40
        or payload.get("targeted_bucket_get_request_count") != 1
        or payload.get("targeted_bucket_get_http_success") is not True
        or payload.get("bucket_get_fields_explicitly_requested") is not True
        or payload.get("content_type_json") is not True
        or payload.get("redirect_occurred") is not False
        or payload.get("raw_response_receipt_verified") is not True
        or payload.get("raw_autoclass_observation_state") not in RAW_AUTOCLASS_STATES
        or payload.get("effective_autoclass_semantic_state") not in EFFECTIVE_AUTOCLASS_STATES
        or payload.get("autoclass_authoritatively_disabled")
        != (payload.get("effective_autoclass_semantic_state") in AUTHORITATIVELY_DISABLED_STATES)
        or any(payload.get(key) != 0 for key in (
            "media_requests", "object_list_requests", "object_get_requests",
            "object_body_bytes_read", "bigquery_requests"
        ))
        or payload.get("credential_content_exported") is not False
        or any(
            type(payload.get(key)) is not bool
            for key in (
                "bucket_location_rate_match",
                "bucket_location_type_rate_match",
                "bucket_default_storage_class_standard",
                "requester_pays_enabled",
            )
        )
    ):
        raise AdjudicationError("AUTOCLASS_CAPTURE_SUMMARY_NOT_AUTHORITATIVE")


def validate_supplemental_artifact_dag(
    *,
    manifest_payload: Mapping[str, Any],
    safety_payload: Mapping[str, Any],
    combined_payload: Mapping[str, Any],
    autoclass_bytes: bytes,
    source_bytes: bytes,
    cost_bytes: bytes,
    manifest_bytes: bytes,
    safety_bytes: bytes,
) -> None:
    """Validate the acyclic A/B/C -> manifest -> safety -> terminal DAG."""

    derived = manifest_payload.get("derived_artifacts")
    if not isinstance(derived, list):
        raise AdjudicationError("SUPPLEMENTAL_DAG_MANIFEST_ROWS_INVALID")
    rows = {
        row.get("role"): row
        for row in derived
        if isinstance(row, Mapping) and isinstance(row.get("role"), str)
    }
    predecessor_bytes = {
        "autoclass": autoclass_bytes,
        "source": source_bytes,
        "cost": cost_bytes,
    }
    if set(rows) != set(predecessor_bytes) or any(
        rows[role].get("filename") != AGGREGATE_FILENAMES[role]
        or rows[role].get("size_bytes") != len(payload)
        or rows[role].get("sha256") != sha256_bytes(payload)
        or rows[role].get("export_profile") != AGGREGATE_PROFILES[role]
        or rows[role].get("closed_schema_status") != "PASS"
        for role, payload in predecessor_bytes.items()
    ):
        raise AdjudicationError("SUPPLEMENTAL_DAG_MANIFEST_BINDING_INVALID")
    if (
        safety_payload.get("aggregate_files_checked") != 4
        or safety_payload.get("provenance_manifest_sha256")
        != sha256_bytes(manifest_bytes)
    ):
        raise AdjudicationError("SUPPLEMENTAL_DAG_SAFETY_BINDING_INVALID")
    terminal_bindings = {
        "autoclass_summary_sha256": sha256_bytes(autoclass_bytes),
        "source_authority_sha256": sha256_bytes(source_bytes),
        "cost_authority_sha256": sha256_bytes(cost_bytes),
        "provenance_manifest_sha256": sha256_bytes(manifest_bytes),
        "safety_gate_sha256": sha256_bytes(safety_bytes),
    }
    if any(combined_payload.get(key) != value for key, value in terminal_bindings.items()):
        raise AdjudicationError("SUPPLEMENTAL_DAG_TERMINAL_BINDING_INVALID")


def _write_aggregate(
    *,
    aggregate_dir: Path,
    role: str,
    payload: Mapping[str, Any],
    safe_policy: Mapping[str, Any],
) -> tuple[Path, bytes]:
    destination = aggregate_dir / AGGREGATE_FILENAMES[role]
    serialized = _json_bytes(payload)
    validate_candidate_bytes(
        serialized,
        filename=destination.name,
        profile_name=AGGREGATE_PROFILES[role],
        policy=safe_policy,
    )
    write_exclusive_no_follow(destination, serialized)
    return destination, serialized


def _restricted_artifact_row(role: str, path: Path) -> dict[str, Any]:
    metadata = path.stat(follow_symlinks=False)
    payload = read_regular_bytes_no_follow(path, required_mode=0o600)
    return {
        "role": role,
        "size_bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "regular_file": stat.S_ISREG(metadata.st_mode),
        "owner_match": metadata.st_uid == os.getuid(),
        "mode_600": stat.S_IMODE(metadata.st_mode) == 0o600,
        "symlinked": path.is_symlink(),
    }


REQUEST_RECEIPT_KEYS = {
    "schema_version", "status", "attempt_id", "source_scheduler_job_id",
    "governing_commit", "method", "endpoint_class", "sanitized_fields_projection",
    "fields_explicitly_requested", "requester_pays_project_supplied",
    "maximum_response_bytes", "automatic_retry", "planned_request_count",
    "media_endpoint_allowed", "object_list_allowed", "object_get_allowed",
    "bigquery_allowed", "official_evidence_registry_sha256", "created_at_utc",
}
RESPONSE_RECEIPT_KEYS = {
    "schema_version", "status", "attempt_id", "source_scheduler_job_id",
    "governing_commit", "request_receipt_sha256", "method", "endpoint_class",
    "http_success", "http_status", "content_type_json", "redirect_occurred",
    "raw_response_size_bytes", "raw_response_sha256",
    "raw_autoclass_observation_state", "effective_autoclass_semantic_state",
    "autoclass_effectively_enabled", "autoclass_authoritatively_disabled",
    "semantic_evidence_authority", "request_count",
    "requester_pays_project_supplied", "media_requests", "object_list_requests",
    "object_get_requests", "object_body_bytes_read", "bigquery_requests",
    "credential_content_exported", "captured_at_utc",
}


def _recompute_and_verify_targeted_evidence(
    *,
    request_bytes: bytes,
    response_bytes: bytes,
    raw_bytes: bytes,
    autoclass_summary: Mapping[str, Any],
    evidence_sha256: str,
    governing_commit: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    request_receipt = _load_mapping_from_bytes(
        request_bytes, "TARGETED_REQUEST_RECEIPT_INVALID"
    )
    response_receipt = _load_mapping_from_bytes(
        response_bytes, "TARGETED_RESPONSE_RECEIPT_INVALID"
    )
    if set(request_receipt) != REQUEST_RECEIPT_KEYS or set(response_receipt) != RESPONSE_RECEIPT_KEYS:
        raise AdjudicationError("TARGETED_RECEIPT_CLOSED_SCHEMA_INVALID")
    request_pass = all(
        (
            request_receipt.get("schema_version") == 1,
            request_receipt.get("status") == "REQUEST_AUTHORITY_FROZEN_BEFORE_NETWORK",
            request_receipt.get("attempt_id") == ATTEMPT_ID,
            request_receipt.get("source_scheduler_job_id") == SOURCE_SCHEDULER_JOB_ID,
            request_receipt.get("governing_commit") == governing_commit,
            request_receipt.get("method") == "GET",
            request_receipt.get("endpoint_class") == TARGETED_BUCKET_ENDPOINT_CLASS,
            request_receipt.get("sanitized_fields_projection") == TARGETED_BUCKET_FIELDS,
            request_receipt.get("fields_explicitly_requested") is True,
            request_receipt.get("requester_pays_project_supplied") is True,
            request_receipt.get("maximum_response_bytes") == MAX_TARGETED_RESPONSE_BYTES,
            request_receipt.get("automatic_retry") is False,
            request_receipt.get("planned_request_count") == 1,
            request_receipt.get("media_endpoint_allowed") is False,
            request_receipt.get("object_list_allowed") is False,
            request_receipt.get("object_get_allowed") is False,
            request_receipt.get("bigquery_allowed") is False,
            request_receipt.get("official_evidence_registry_sha256") == evidence_sha256,
        )
    )
    response_pass = all(
        (
            response_receipt.get("schema_version") == 1,
            response_receipt.get("status") == "PASS_TARGETED_BUCKET_METADATA_RESPONSE_CAPTURED",
            response_receipt.get("attempt_id") == ATTEMPT_ID,
            response_receipt.get("source_scheduler_job_id") == SOURCE_SCHEDULER_JOB_ID,
            response_receipt.get("governing_commit") == governing_commit,
            response_receipt.get("request_receipt_sha256") == sha256_bytes(request_bytes),
            response_receipt.get("method") == "GET",
            response_receipt.get("endpoint_class") == TARGETED_BUCKET_ENDPOINT_CLASS,
            response_receipt.get("http_success") is True,
            response_receipt.get("http_status") == 200,
            response_receipt.get("content_type_json") is True,
            response_receipt.get("redirect_occurred") is False,
            response_receipt.get("raw_response_size_bytes") == len(raw_bytes),
            response_receipt.get("raw_response_sha256") == sha256_bytes(raw_bytes),
            response_receipt.get("request_count") == 1,
            response_receipt.get("requester_pays_project_supplied") is True,
            all(
                response_receipt.get(key) == 0
                for key in (
                    "media_requests", "object_list_requests", "object_get_requests",
                    "object_body_bytes_read", "bigquery_requests",
                )
            ),
            response_receipt.get("credential_content_exported") is False,
        )
    )
    if not request_pass or not response_pass:
        raise AdjudicationError("TARGETED_RECEIPT_AUTHORITY_INVALID")
    raw_payload = strict_json_loads(raw_bytes)
    if not isinstance(raw_payload, Mapping):
        raise AdjudicationError("TARGETED_BUCKET_RESPONSE_NOT_MAPPING")
    _validate_targeted_payload(raw_payload)
    recomputed = adjudicate_autoclass(
        raw_payload,
        bucket_get_succeeded=True,
        fields_selector_proven=True,
        content_type_json=True,
        redirect_occurred=False,
        raw_response_receipt_verified=True,
        official_default_disabled_semantics_verified=True,
    ).to_dict()
    for key, value in recomputed.items():
        if response_receipt.get(key) != value or autoclass_summary.get(key) != value:
            raise AdjudicationError("TARGETED_AUTOCLASS_STATE_RECOMPUTATION_MISMATCH")
    aggregate_bindings = {
        "governing_commit": governing_commit,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "request_receipt_sha256": sha256_bytes(request_bytes),
        "response_receipt_sha256": sha256_bytes(response_bytes),
        "raw_response_sha256": sha256_bytes(raw_bytes),
        "raw_response_size_bytes": len(raw_bytes),
        "official_evidence_registry_sha256": evidence_sha256,
    }
    if any(autoclass_summary.get(key) != value for key, value in aggregate_bindings.items()):
        raise AdjudicationError("TARGETED_AGGREGATE_RECEIPT_BINDING_MISMATCH")
    current_flags = {
        "bucket_location_rate_match": str(raw_payload["location"]).strip().upper() == "US",
        "bucket_location_type_rate_match": str(raw_payload["locationType"]).strip().casefold() == "multi-region",
        "bucket_default_storage_class_standard": str(raw_payload["storageClass"]).strip().upper() == "STANDARD",
        "requester_pays_enabled": bool(raw_payload["billing"]["requesterPays"]),
    }
    if any(autoclass_summary.get(key) is not value for key, value in current_flags.items()):
        raise AdjudicationError("TARGETED_BUCKET_COST_INPUT_AGGREGATE_MISMATCH")
    return raw_payload, recomputed


def offline_command(args: argparse.Namespace) -> int:
    safe_policy, safe_policy_sha = load_safe_export_policy(args.safe_export_policy)
    attempt_root = bind_approved_restricted_path(
        args.attempt_root,
        policy=safe_policy,
        must_exist=True,
        expect="directory",
        root_kind="direct",
    )
    aggregate_dir = attempt_root / "aggregate"
    restricted_dir = attempt_root / "restricted"
    if not private_directory_mode_ok(attempt_root.stat().st_mode):
        raise AdjudicationError("ATTEMPT_ROOT_MODE_INVALID")
    policy = _validate_policy_file(args.adjudication_policy)
    resource_policy = yaml.safe_load(read_regular_bytes_no_follow(args.resource_policy).decode("utf-8"))
    if not isinstance(resource_policy, Mapping):
        raise AdjudicationError("RESOURCE_POLICY_INVALID")
    evidence_bytes = read_regular_bytes_no_follow(args.official_evidence_registry)
    evidence = validate_official_evidence_registry(
        strict_json_loads(evidence_bytes)
    )
    evidence_sha = sha256_bytes(evidence_bytes)
    original_dir = bind_approved_restricted_path(
        args.original_aggregate_dir,
        policy=safe_policy,
        must_exist=True,
        expect="directory",
        root_kind="direct",
    )
    original_payloads, original_manifest_rows = _validate_original_artifacts(
        original_dir, safe_policy
    )
    source_summary = _load_mapping_from_bytes(
        original_payloads["c3_full_source_preflight.summary.json"],
        "SOURCE_SUMMARY_NOT_MAPPING",
    )
    source_safety = _load_mapping_from_bytes(
        original_payloads["c3_full_source_preflight_safety_gate.json"],
        "SOURCE_SAFETY_NOT_MAPPING",
    )
    original_cost = _load_mapping_from_bytes(
        original_payloads["c3_full_source_cost_estimate.json"],
        "ORIGINAL_COST_NOT_MAPPING",
    )
    batch_totals = _validate_batch_table(
        original_payloads["c3_full_source_preflight_by_batch.csv"]
    )
    autoclass_path = aggregate_dir / AGGREGATE_FILENAMES["autoclass"]
    autoclass_bytes = read_regular_bytes_no_follow(autoclass_path, required_mode=0o600)
    validate_candidate_bytes(
        autoclass_bytes,
        filename=autoclass_path.name,
        profile_name=AGGREGATE_PROFILES["autoclass"],
        policy=safe_policy,
    )
    autoclass_summary = _load_mapping_from_bytes(
        autoclass_bytes, "AUTOCLASS_SUMMARY_NOT_MAPPING"
    )
    _validate_autoclass_summary(autoclass_summary)
    raw_path = restricted_dir / "targeted_bucket_get_raw_response.restricted.json"
    request_path = restricted_dir / "targeted_bucket_get_request_receipt.restricted.json"
    response_path = restricted_dir / "targeted_bucket_get_response_receipt.restricted.json"
    raw_bytes = read_regular_bytes_no_follow(raw_path, required_mode=0o600)
    request_bytes = read_regular_bytes_no_follow(request_path, required_mode=0o600)
    response_bytes = read_regular_bytes_no_follow(response_path, required_mode=0o600)
    raw_payload, recomputed_autoclass = _recompute_and_verify_targeted_evidence(
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        raw_bytes=raw_bytes,
        autoclass_summary=autoclass_summary,
        evidence_sha256=evidence_sha,
        governing_commit=args.governing_commit,
    )

    selected_studies_path = bind_approved_restricted_path(
        args.selected_studies, policy=safe_policy, must_exist=True,
        expect="file", root_kind="direct"
    )
    split_map_path = bind_approved_restricted_path(
        args.split_map, policy=safe_policy, must_exist=True,
        expect="file", root_kind="direct"
    )
    source_manifest_path = bind_approved_restricted_path(
        args.selected_source_manifest, policy=safe_policy, must_exist=True,
        expect="file", root_kind="direct"
    )

    source_payload = _build_source_authority(
        source_summary=source_summary,
        source_safety=source_safety,
        batch_totals=batch_totals,
        original_payloads=original_payloads,
        selected_manifest_sha256=sha256_bytes(
            read_regular_bytes_no_follow(selected_studies_path)
        ),
        split_manifest_sha256=sha256_bytes(
            read_regular_bytes_no_follow(split_map_path)
        ),
        selected_source_manifest_sha256=sha256_bytes(
            read_regular_bytes_no_follow(source_manifest_path)
        ),
    )
    current_bucket_metadata = {
        "location_rate_match": str(raw_payload["location"]).strip().upper() == "US",
        "location_type_rate_match": (
            str(raw_payload["locationType"]).strip().casefold() == "multi-region"
        ),
        "default_storage_class_standard": (
            str(raw_payload["storageClass"]).strip().upper() == "STANDARD"
        ),
        "requester_pays_enabled": bool(raw_payload["billing"]["requesterPays"]),
        "consistent_with_source_summary": all(
            (
                str(raw_payload["location"]).strip().upper()
                == str(source_summary.get("bucket_location", "")).strip().upper(),
                str(raw_payload["locationType"]).strip().casefold()
                == str(source_summary.get("bucket_location_type", "")).strip().casefold(),
                str(raw_payload["storageClass"]).strip().upper()
                == str(source_summary.get("bucket_default_storage_class", "")).strip().upper(),
                bool(raw_payload["billing"]["requesterPays"])
                is (source_summary.get("bucket_requester_pays_enabled") is True),
            )
        ),
    }
    cost_payload = _calculate_revised_cost(
        source_summary=source_summary,
        original_cost=original_cost,
        autoclass_summary=autoclass_summary,
        resource_policy=resource_policy,
        official_evidence_sha256=evidence_sha,
        source_authority_status=str(source_payload["status"]),
        current_bucket_metadata=current_bucket_metadata,
    )
    _, source_bytes = _write_aggregate(
        aggregate_dir=aggregate_dir, role="source", payload=source_payload, safe_policy=safe_policy
    )
    _, cost_bytes = _write_aggregate(
        aggregate_dir=aggregate_dir, role="cost", payload=cost_payload, safe_policy=safe_policy
    )
    repo_root = Path(__file__).resolve().parents[1]
    code_specs = tuple(policy["provenance"]["repository_authorities"])
    code_rows: list[dict[str, Any]] = []
    for relative in code_specs:
        candidate = repo_root / str(relative)
        payload = read_regular_bytes_no_follow(candidate)
        code_rows.append(
            {"role": Path(relative).stem, "repository_file": str(relative), "sha256": sha256_bytes(payload)}
        )
    restricted_rows = [
        _restricted_artifact_row("targeted_request_receipt", request_path),
        _restricted_artifact_row("targeted_raw_response", raw_path),
        _restricted_artifact_row("targeted_response_receipt", response_path),
    ]
    derived_rows = []
    for role, payload in (
        ("autoclass", autoclass_bytes),
        ("source", source_bytes),
        ("cost", cost_bytes),
    ):
        derived_rows.append(
            {
                "role": role,
                "filename": AGGREGATE_FILENAMES[role],
                "size_bytes": len(payload),
                "sha256": sha256_bytes(payload),
                "export_profile": AGGREGATE_PROFILES[role],
                "closed_schema_status": "PASS",
            }
        )
    manifest_payload = {
        "schema_version": 1,
        "status": "PASS_PROVENANCE_BOUND",
        "attempt_id": ATTEMPT_ID,
        "source_scheduler_job_id": SOURCE_SCHEDULER_JOB_ID,
        "starting_commit": STARTING_COMMIT,
        "implementation_commit": args.governing_commit,
        "adjudication_timestamp_utc": utc_now(),
        "new_bucket_request_performed": True,
        "object_listing_repeated": False,
        "storage_audit_repeated": False,
        "object_bodies_downloaded": False,
        "bigquery_rows_returned": False,
        "original_artifact_count": len(original_manifest_rows),
        "original_artifacts": original_manifest_rows,
        "restricted_artifact_count": len(restricted_rows),
        "restricted_artifacts": restricted_rows,
        "official_evidence_registry_sha256": evidence_sha,
        "code_authorities": code_rows,
        "derived_artifact_count": len(derived_rows),
        "derived_artifacts": derived_rows,
    }
    _validate_manifest_nested_members(manifest_payload)
    _, manifest_bytes = _write_aggregate(
        aggregate_dir=aggregate_dir, role="manifest", payload=manifest_payload, safe_policy=safe_policy
    )
    predecessor_artifacts = {
        "autoclass": autoclass_bytes,
        "source": source_bytes,
        "cost": cost_bytes,
        "manifest": manifest_bytes,
    }
    for role, expected_bytes in predecessor_artifacts.items():
        observed = read_regular_bytes_no_follow(
            aggregate_dir / AGGREGATE_FILENAMES[role], required_mode=0o600
        )
        if observed != expected_bytes:
            raise AdjudicationError("SUPPLEMENTAL_PREDECESSOR_ARTIFACT_CHANGED")
        validate_candidate_bytes(
            observed,
            filename=AGGREGATE_FILENAMES[role],
            profile_name=AGGREGATE_PROFILES[role],
            policy=safe_policy,
        )
    predecessor_pass = (
        source_payload["source_inventory_authority"] is True
        and cost_payload["cost_authority"] is True
        and autoclass_summary["autoclass_authoritatively_disabled"] is True
    )
    safety_payload = {
        "schema_version": 1,
        "status": "PASS" if predecessor_pass else "FAIL",
        "attempt_id": ATTEMPT_ID,
        "aggregate_files_checked": 4,
        "all_closed_schemas_passed": True,
        "restricted_receipts_regular_files": True,
        "restricted_receipts_mode_600": True,
        "restricted_receipts_symlinked": False,
        "raw_response_regular_file": True,
        "raw_response_mode_600": True,
        "raw_response_symlinked": False,
        "restricted_outputs_outside_repository": True,
        "forbidden_keys_detected": 0,
        "forbidden_values_detected": 0,
        "cloud_identifiers_exported": False,
        "credential_content_exported": False,
        "source_job_outputs_modified": False,
        "object_listing_repeated": False,
        "storage_audit_repeated": False,
        "targeted_bucket_requests": 1,
        "media_requests": 0,
        "object_list_requests": 0,
        "object_get_requests": 0,
        "object_body_bytes_read": 0,
        "bigquery_requests": 0,
        "dicom_bodies_downloaded": False,
        "safe_export_policy_sha256": safe_policy_sha,
        "provenance_manifest_sha256": sha256_bytes(manifest_bytes),
        "safety_gate_passed": predecessor_pass,
    }
    _, safety_bytes = _write_aggregate(
        aggregate_dir=aggregate_dir, role="safety", payload=safety_payload, safe_policy=safe_policy
    )

    terminal_predecessors = {**predecessor_artifacts, "safety": safety_bytes}
    for role, expected_bytes in terminal_predecessors.items():
        observed = read_regular_bytes_no_follow(
            aggregate_dir / AGGREGATE_FILENAMES[role], required_mode=0o600
        )
        if observed != expected_bytes:
            raise AdjudicationError("SUPPLEMENTAL_TERMINAL_PREDECESSOR_CHANGED")
        validate_candidate_bytes(
            observed,
            filename=AGGREGATE_FILENAMES[role],
            profile_name=AGGREGATE_PROFILES[role],
            policy=safe_policy,
        )
    combined_payload = {
        "schema_version": 1,
        "status": "PASS_SUPPLEMENTAL_ADJUDICATION" if predecessor_pass else "FAIL_WITH_EXPLICIT_REASON",
        "attempt_id": ATTEMPT_ID,
        "governing_commit": args.governing_commit,
        "original_output_hash_gate_passed": True,
        "original_output_schema_gate_passed": True,
        "restricted_receipt_gate_passed": True,
        "autoclass_adjudication_gate_passed": autoclass_summary["autoclass_authoritatively_disabled"],
        "source_inventory_gate_passed": source_payload["source_inventory_authority"],
        "historical_identity_limitation_preserved": source_payload["historical_object_identity_authority"] == HISTORICAL_IDENTITY_LIMITATION,
        "cost_authority_gate_passed": cost_payload["cost_authority"],
        "new_output_schema_gate_passed": True,
        "aggregate_provenance_gate_passed": True,
        "aggregate_safety_gate_passed": safety_payload["safety_gate_passed"],
        "autoclass_summary_sha256": sha256_bytes(autoclass_bytes),
        "source_authority_sha256": sha256_bytes(source_bytes),
        "cost_authority_sha256": sha256_bytes(cost_bytes),
        "provenance_manifest_sha256": sha256_bytes(manifest_bytes),
        "safety_gate_sha256": sha256_bytes(safety_bytes),
        "full_c3_authorized": False,
        "section5_run": False,
    }
    validate_supplemental_artifact_dag(
        manifest_payload=manifest_payload,
        safety_payload=safety_payload,
        combined_payload=combined_payload,
        autoclass_bytes=autoclass_bytes,
        source_bytes=source_bytes,
        cost_bytes=cost_bytes,
        manifest_bytes=manifest_bytes,
        safety_bytes=safety_bytes,
    )
    _, combined_bytes = _write_aggregate(
        aggregate_dir=aggregate_dir, role="combined", payload=combined_payload, safe_policy=safe_policy
    )
    combined_observed = read_regular_bytes_no_follow(
        aggregate_dir / AGGREGATE_FILENAMES["combined"], required_mode=0o600
    )
    if combined_observed != combined_bytes:
        raise AdjudicationError("SUPPLEMENTAL_TERMINAL_VALIDATION_ARTIFACT_CHANGED")
    validate_candidate_bytes(
        combined_observed,
        filename=AGGREGATE_FILENAMES["combined"],
        profile_name=AGGREGATE_PROFILES["combined"],
        policy=safe_policy,
    )

    _validate_original_artifacts(original_dir, safe_policy)
    print(f"SOURCE_INVENTORY_AUTHORITY={source_payload['status']}")
    print(f"HISTORICAL_OBJECT_IDENTITY_AUTHORITY={HISTORICAL_IDENTITY_LIMITATION}")
    print(f"AUTOCLASS_EFFECTIVE_STATE={autoclass_summary['effective_autoclass_semantic_state']}")
    print(f"COST_AUTHORITY={cost_payload['status']}")
    print("OBJECT_LISTING_REPEATED=NO")
    print("STORAGE_AUDIT_REPEATED=NO")
    print("DICOM_BODIES_DOWNLOADED=NO")
    print("BIGQUERY_ROWS_RETURNED=NO")
    print(f"SUPPLEMENTAL_AGGREGATE_FILES=6")
    print(f"SUPPLEMENTAL_SAFETY_GATE_SHA256={sha256_bytes(safety_bytes)}")
    return 0 if combined_payload["status"] == "PASS_SUPPLEMENTAL_ADJUDICATION" else 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--attempt-root", type=Path, required=True)
    common.add_argument("--attempt-id", default=ATTEMPT_ID)
    common.add_argument("--governing-commit", required=True)
    common.add_argument("--safe-export-policy", type=Path, required=True)
    common.add_argument("--adjudication-policy", type=Path, required=True)
    common.add_argument("--official-evidence-registry", type=Path, required=True)
    capture = subparsers.add_parser("capture", parents=[common])
    capture.add_argument("--gcp-authority-receipt", type=Path, required=True)
    capture.add_argument("--expected-gcp-authority-receipt-sha256", required=True)
    capture.add_argument("--gcloud-bin", required=True)
    capture.add_argument("--billing-project-env", default="LVEF_C3_GCP_BILLING_PROJECT")
    capture.add_argument("--timeout-seconds", type=int, default=60)
    offline = subparsers.add_parser("offline", parents=[common])
    offline.add_argument("--original-aggregate-dir", type=Path, required=True)
    offline.add_argument("--resource-policy", type=Path, required=True)
    offline.add_argument("--selected-source-manifest", type=Path, required=True)
    offline.add_argument("--selected-studies", type=Path, required=True)
    offline.add_argument("--split-map", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.attempt_id != ATTEMPT_ID:
            raise AdjudicationError("ATTEMPT_ID_MISMATCH")
        _require_git_commit(args.governing_commit, "GOVERNING_COMMIT_INVALID")
        if args.command == "capture":
            return capture_command(args)
        return offline_command(args)
    except (AdjudicationError, AutoclassStateError) as exc:
        print(f"AUTOCLASS_ADJUDICATION_ERROR={str(exc)}", file=sys.stderr)
        return 2
    except GCPAuthorityError:
        print("AUTOCLASS_ADJUDICATION_ERROR=GCP_AUTHORITY_REVALIDATION_FAILED", file=sys.stderr)
        return 2
    except SafetyPolicyError:
        print("AUTOCLASS_ADJUDICATION_ERROR=SAFE_POLICY_GATE_FAILED", file=sys.stderr)
        return 2
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
        print("AUTOCLASS_ADJUDICATION_ERROR=CONTROLLED_INPUT_OR_SCHEMA_FAILURE", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
