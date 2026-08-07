#!/usr/bin/env python3
"""Metadata-only GCS preflight for the prospective selected-cohort C3 source.

The live provider uses the Cloud Storage JSON API ``objects.list`` endpoint
with a fields projection.  It never calls a media endpoint and never reads an
object body.  A requester-pays project is taken only from an environment
variable.  Restricted row-level metadata and discrepancies stay outside Git;
the aggregate outputs contain counts, byte totals, deterministic batch totals,
and a rate-explicit planning estimate.
"""
from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)
from audit_lvef_c3_gcp_authority import (
    AuthorityError as GCPAuthorityError,
    acquire_access_token_for_preflight,
    validate_restricted_receipt,
)


BUCKET = "mimic-iv-echo-1.0.physionet.org"
RELEASE = "mimic-iv-echo/1.0"
EXPECTED_SELECTED_STUDIES = 4530
EXPECTED_SELECTED_SUBJECTS = 4530
EXPECTED_RAW_REQUEST_ROWS = 336016
EXPECTED_NORMALIZED_REQUESTS = 335984
EXPECTED_COLLAPSED_ROWS = 32
EXPECTED_SPLIT_COUNTS = {"train": 3171, "val": 679, "test": 680}
EXPECTED_SELECTED_SHA256 = (
    "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1"
)
DEFAULT_PAGE_SIZE = 1000
MAX_GCS_JSON_BYTES = 16 * 1024 * 1024
JSON_API_FIELDS = (
    "nextPageToken,items(name,size,md5Hash,crc32c,generation,storageClass,updated)"
)
JSON_API_BUCKET_FIELDS = (
    "name,location,locationType,storageClass,billing(requesterPays),"
    "autoclass(enabled,toggleTime,terminalStorageClass,"
    "terminalStorageClassUpdateTime),metageneration,updated"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[0-9]+$")
PATH_RE = re.compile(
    r"^files/p(?P<prefix>[0-9]{2})/p(?P<subject>[0-9]+)/s(?P<study>[0-9]+)/"
    r"(?P<filename>[A-Za-z0-9._-]+\.dcm)$"
)
SAFE_BATCH_RE = re.compile(r"^c3_batch_[0-9]{3}$")
SAFE_PAGE_JOURNAL_RE = re.compile(r"^page_[0-9]{6}_[0-9a-f]{16}\.jsonl$")
FORBIDDEN_SOURCE_COLUMN_TOKENS = (
    "lvef",
    "label",
    "target",
    "prediction",
    "performance",
    "outcome",
    "y_true",
    "y_pred",
)
ALLOWED_SOURCE_COLUMNS = {
    "release_id",
    "source_bucket",
    "component",
    "subject_id",
    "study_id",
    "split",
    "source_relative_path",
    "gcs_uri",
    "expected_size_bytes",
    "expected_sha256",
    "source_object_key",
    "expected_md5_base64",
    "expected_crc32c_base64",
    "expected_generation",
}


class PreflightError(RuntimeError):
    """Fail-closed error whose message is safe for aggregate output."""


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def _urlopen_no_redirect(request: Request, *, timeout: int):
    return build_opener(_RejectRedirectHandler()).open(request, timeout=timeout)


def _read_gcs_json_response(
    request: Request, *, timeout_seconds: int, purpose: str
) -> Mapping[str, Any]:
    requested = urlparse(request.full_url)
    if (
        requested.scheme != "https"
        or requested.hostname != "storage.googleapis.com"
        or "/download/storage/" in request.full_url
        or "alt=media" in request.full_url
    ):
        raise PreflightError("MEDIA_ENDPOINT_PROHIBITED")
    try:
        with _urlopen_no_redirect(request, timeout=timeout_seconds) as response:
            if response.geturl() != request.full_url:
                raise PreflightError(f"{purpose}_REDIRECT_PROHIBITED")
            headers = getattr(response, "headers", None)
            content_type = (
                headers.get_content_type()
                if headers is not None and hasattr(headers, "get_content_type")
                else ""
            )
            if content_type != "application/json":
                raise PreflightError(f"{purpose}_CONTENT_TYPE_INVALID")
            body = response.read(MAX_GCS_JSON_BYTES + 1)
    except HTTPError as exc:
        raise PreflightError(f"{purpose}_HTTP_{exc.code}") from None
    except (URLError, TimeoutError):
        raise PreflightError(f"{purpose}_NETWORK_FAILURE") from None
    if len(body) > MAX_GCS_JSON_BYTES:
        raise PreflightError(f"{purpose}_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise PreflightError(f"{purpose}_JSON_INVALID") from None
    if not isinstance(payload, Mapping):
        raise PreflightError(f"{purpose}_JSON_NOT_OBJECT")
    return payload


@dataclass(frozen=True)
class SelectedStudy:
    subject_id: str
    study_id: str
    n_dicoms: int
    batch_id: str


@dataclass(frozen=True)
class SourceRequest:
    release_id: str
    component: str
    subject_id: str
    study_id: str
    split: str
    relative_path: str
    gcs_uri: str
    expected_size_bytes: int | None
    expected_md5_base64: str | None
    expected_crc32c_base64: str | None
    expected_generation: str | None
    source_object_key: str
    batch_id: str


@dataclass(frozen=True)
class RemoteMetadata:
    relative_path: str
    size_bytes: int
    md5_base64: str
    crc32c_base64: str
    generation: str
    storage_class: str
    updated: str


@dataclass(frozen=True)
class ProviderStats:
    provider: str
    pages: int
    objects_scanned: int
    selected_objects_matched: int
    operation_class: str
    media_requests: int
    body_bytes_read: int


@dataclass(frozen=True)
class BucketMetadata:
    name: str
    location: str
    location_type: str
    default_storage_class: str
    requester_pays: bool
    autoclass_metadata_present: bool
    autoclass_enabled: bool
    autoclass_toggle_time: str | None
    autoclass_terminal_storage_class: str | None
    autoclass_terminal_storage_class_update_time: str | None
    metageneration: str
    updated: str
    provider: str
    metadata_requests: int
    media_requests: int
    body_bytes_read: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_id(value: Any) -> str:
    text = str(value)
    if text != text.strip() or not ID_RE.fullmatch(text) or str(int(text)) != text:
        raise PreflightError("NONCANONICAL_IDENTIFIER")
    return text


def _safe_relative_path(value: Any, subject_id: str | None = None, study_id: str | None = None) -> str:
    text = str(value).strip()
    prefix = f"gs://{BUCKET}/"
    if text.startswith("gs://"):
        if not text.startswith(prefix):
            raise PreflightError("SOURCE_BUCKET_NOT_LOCKED")
        text = text[len(prefix) :]
    pure = PurePosixPath(text)
    if (
        not text
        or text.startswith(("/", "~"))
        or "\\" in text
        or pure.as_posix() != text
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PreflightError("UNSAFE_SOURCE_PATH")
    match = PATH_RE.fullmatch(text)
    if match is None:
        raise PreflightError("SOURCE_PATH_OUTSIDE_LOCKED_DICOM_SCOPE")
    if subject_id is not None and study_id is not None:
        if match.group("subject") != subject_id or match.group("study") != study_id:
            raise PreflightError("SOURCE_PATH_OWNERSHIP_CONFLICT")
        if match.group("prefix") != f"{int(subject_id) // 1_000_000:02d}":
            raise PreflightError("SOURCE_PATH_PREFIX_CONFLICT")
    return text


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not text.isdigit():
        raise PreflightError("INVALID_OPTIONAL_INTEGER")
    return int(text)


def _base64_digest(value: Any, decoded_bytes: int, code: str) -> str:
    text = str(value).strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise PreflightError(code) from None
    if len(decoded) != decoded_bytes or base64.b64encode(decoded).decode("ascii") != text:
        raise PreflightError(code)
    return text


def _optional_digest(value: Any, decoded_bytes: int, code: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _base64_digest(value, decoded_bytes, code)


def _read_csv_header(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise PreflightError("INPUT_NOT_REGULAR_FILE")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        try:
            header = [str(value).strip() for value in next(csv.reader(handle))]
        except StopIteration:
            raise PreflightError("EMPTY_INPUT_CSV") from None
    if not header or len(header) != len(set(header)):
        raise PreflightError("INVALID_INPUT_HEADER")
    return header


def load_selected_studies(
    path: Path,
    *,
    batch_size: int,
    expected_sha256: str = EXPECTED_SELECTED_SHA256,
    expected_studies: int = EXPECTED_SELECTED_STUDIES,
) -> list[SelectedStudy]:
    if sha256_file(path) != expected_sha256:
        raise PreflightError("SELECTED_STUDY_AUTHORITY_SHA256_MISMATCH")
    header = _read_csv_header(path)
    if not {"subject_id", "study_id", "n_dicoms"}.issubset(header):
        raise PreflightError("SELECTED_STUDY_AUTHORITY_SCHEMA_MISMATCH")
    raw: list[tuple[str, str, int]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            subject = _canonical_id(row["subject_id"])
            study = _canonical_id(row["study_id"])
            n_dicoms = _optional_nonnegative_int(row["n_dicoms"])
            if n_dicoms is None or n_dicoms <= 0:
                raise PreflightError("SELECTED_STUDY_NONPOSITIVE_DICOM_COUNT")
            raw.append((subject, study, n_dicoms))
    if len(raw) != expected_studies:
        raise PreflightError("SELECTED_STUDY_COUNT_MISMATCH")
    if len({row[0] for row in raw}) != len(raw) or len({row[1] for row in raw}) != len(raw):
        raise PreflightError("SELECTED_STUDY_AUTHORITY_NOT_BIJECTIVE")
    ordered = sorted(raw, key=lambda row: (int(row[0]), int(row[1])))
    return [
        SelectedStudy(
            subject_id=subject,
            study_id=study,
            n_dicoms=n_dicoms,
            batch_id=f"c3_batch_{index // batch_size:03d}",
        )
        for index, (subject, study, n_dicoms) in enumerate(ordered)
    ]


def load_split_map(
    path: Path,
    selected: Sequence[SelectedStudy],
    *,
    expected_sha256: str,
    expected_counts: Mapping[str, int] = EXPECTED_SPLIT_COUNTS,
) -> dict[str, str]:
    expected_sha256 = expected_sha256.lower()
    if not SHA256_RE.fullmatch(expected_sha256) or sha256_file(path) != expected_sha256:
        raise PreflightError("SPLIT_MAP_SHA256_MISMATCH")
    header = _read_csv_header(path)
    if not {"subject_id", "split"}.issubset(header):
        raise PreflightError("SPLIT_MAP_SCHEMA_MISMATCH")
    selected_subjects = {row.subject_id for row in selected}
    result: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            subject = _canonical_id(row["subject_id"])
            split = str(row["split"]).strip()
            if subject in result:
                raise PreflightError("SPLIT_MAP_DUPLICATE_SUBJECT")
            if split not in expected_counts:
                raise PreflightError("SPLIT_MAP_INVALID_ASSIGNMENT")
            result[subject] = split
    if set(result) != selected_subjects:
        raise PreflightError("SPLIT_MAP_SELECTED_SUBJECT_SET_MISMATCH")
    observed = {name: sum(value == name for value in result.values()) for name in expected_counts}
    if observed != dict(expected_counts):
        raise PreflightError("SPLIT_MAP_COUNT_MISMATCH")
    return result


def load_source_requests(
    path: Path,
    selected: Sequence[SelectedStudy],
    *,
    split_by_subject: Mapping[str, str],
    expected_sha256: str,
    expected_requests: int = EXPECTED_NORMALIZED_REQUESTS,
    expected_raw_rows: int = EXPECTED_RAW_REQUEST_ROWS,
    expected_collapsed_rows: int = EXPECTED_COLLAPSED_ROWS,
) -> tuple[list[SourceRequest], dict[str, int]]:
    expected_sha256 = expected_sha256.lower()
    if not SHA256_RE.fullmatch(expected_sha256) or sha256_file(path) != expected_sha256:
        raise PreflightError("SOURCE_MANIFEST_SHA256_MISMATCH")
    header = _read_csv_header(path)
    unknown = set(header) - ALLOWED_SOURCE_COLUMNS
    forbidden = {
        name for name in header if any(token in name.lower() for token in FORBIDDEN_SOURCE_COLUMN_TOKENS)
    }
    if unknown or forbidden:
        raise PreflightError("SOURCE_MANIFEST_NONTECHNICAL_OR_UNKNOWN_COLUMN")
    required = {
        "release_id",
        "source_bucket",
        "component",
        "subject_id",
        "study_id",
        "split",
        "source_relative_path",
        "gcs_uri",
        "source_object_key",
    }
    if not required.issubset(header):
        raise PreflightError("SOURCE_MANIFEST_SCHEMA_MISMATCH")
    selected_by_study = {row.study_id: row for row in selected}
    selected_by_subject = {row.subject_id: row.study_id for row in selected}
    requests: list[SourceRequest] = []
    duplicate_groups = 0
    ownership_conflicts = 0
    seen: dict[str, SourceRequest] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            subject = _canonical_id(row["subject_id"])
            study = _canonical_id(row["study_id"])
            selected_row = selected_by_study.get(study)
            if selected_row is None or selected_row.subject_id != subject or selected_by_subject.get(subject) != study:
                ownership_conflicts += 1
                continue
            if row["release_id"] != RELEASE or row["source_bucket"] != BUCKET:
                raise PreflightError("SOURCE_RELEASE_OR_BUCKET_MISMATCH")
            split = str(row["split"]).strip()
            if split_by_subject.get(subject) != split:
                raise PreflightError("SOURCE_SPLIT_AUTHORITY_MISMATCH")
            relative = _safe_relative_path(row["source_relative_path"], subject, study)
            uri_relative = _safe_relative_path(row["gcs_uri"], subject, study)
            if uri_relative != relative:
                raise PreflightError("SOURCE_LOCATOR_COLUMNS_DISAGREE")
            expected_key = hashlib.sha256(f"{RELEASE}\0{relative}".encode("utf-8")).hexdigest()
            if str(row["source_object_key"]).lower() != expected_key:
                raise PreflightError("SOURCE_OBJECT_KEY_MISMATCH")
            request = SourceRequest(
                release_id=RELEASE,
                component=str(row["component"]),
                subject_id=subject,
                study_id=study,
                split=split,
                relative_path=relative,
                gcs_uri=f"gs://{BUCKET}/{relative}",
                expected_size_bytes=_optional_nonnegative_int(row.get("expected_size_bytes")),
                expected_md5_base64=_optional_digest(row.get("expected_md5_base64"), 16, "EXPECTED_MD5_INVALID"),
                expected_crc32c_base64=_optional_digest(row.get("expected_crc32c_base64"), 4, "EXPECTED_CRC32C_INVALID"),
                expected_generation=(str(row.get("expected_generation", "")).strip() or None),
                source_object_key=expected_key,
                batch_id=selected_row.batch_id,
            )
            prior = seen.get(relative)
            if prior is not None:
                duplicate_groups += 1
                if prior != request:
                    ownership_conflicts += 1
                continue
            seen[relative] = request
            requests.append(request)
    if ownership_conflicts:
        raise PreflightError("SOURCE_OWNERSHIP_OR_DUPLICATE_CONFLICT")
    if duplicate_groups:
        raise PreflightError("FROZEN_SOURCE_MANIFEST_CONTAINS_REPEATED_LOCATORS")
    if len(requests) != expected_requests:
        raise PreflightError("NORMALIZED_SOURCE_REQUEST_COUNT_MISMATCH")
    represented = {row.study_id for row in requests}
    zero_record_studies = len(set(selected_by_study) - represented)
    if zero_record_studies:
        raise PreflightError("SELECTED_STUDY_SOURCE_DEFICIT")
    observed_counts: dict[str, int] = {}
    for request in requests:
        observed_counts[request.study_id] = observed_counts.get(request.study_id, 0) + 1
    deficits = {
        study: selected_by_study[study].n_dicoms - count
        for study, count in observed_counts.items()
    }
    if any(value < 0 for value in deficits.values()):
        raise PreflightError("NORMALIZED_SOURCE_COUNT_EXCEEDS_RAW_AUTHORITY")
    raw_rows = sum(row.n_dicoms for row in selected)
    collapsed = raw_rows - len(requests)
    if raw_rows != expected_raw_rows or collapsed != expected_collapsed_rows:
        raise PreflightError("RAW_TO_NORMALIZED_RECONCILIATION_MISMATCH")
    stats = {
        "repeated_locator_groups": duplicate_groups,
        "ownership_conflicts": ownership_conflicts,
        "zero_record_studies": zero_record_studies,
        "raw_request_rows": raw_rows,
        "normalized_requests": len(requests),
        "collapsed_rows": collapsed,
        "studies_with_collapsed_rows": sum(value > 0 for value in deficits.values()),
    }
    return sorted(requests, key=lambda row: row.relative_path), stats


def normalize_remote_metadata(record: Mapping[str, Any]) -> RemoteMetadata:
    name = record.get("name") or record.get("relative_path")
    if name is None and record.get("gcs_uri"):
        name = record["gcs_uri"]
    relative = _safe_relative_path(name)
    size_text = str(record.get("size") if record.get("size") is not None else record.get("size_bytes", ""))
    if not size_text.isdigit():
        raise PreflightError("REMOTE_SIZE_MISSING_OR_INVALID")
    md5 = record.get("md5Hash") if record.get("md5Hash") is not None else record.get("md5_base64")
    crc = record.get("crc32c") if record.get("crc32c") is not None else record.get("crc32c_base64")
    generation = str(record.get("generation", ""))
    storage_class = str(record.get("storageClass") or record.get("storage_class") or "").strip()
    updated = str(record.get("updated") or "").strip()
    if not generation.isdigit():
        raise PreflightError("REMOTE_GENERATION_MISSING_OR_INVALID")
    if not storage_class:
        raise PreflightError("REMOTE_STORAGE_CLASS_MISSING")
    if not updated:
        raise PreflightError("REMOTE_UPDATED_TIMESTAMP_MISSING")
    return RemoteMetadata(
        relative_path=relative,
        size_bytes=int(size_text),
        md5_base64=_base64_digest(md5, 16, "REMOTE_MD5_MISSING_OR_INVALID"),
        crc32c_base64=_base64_digest(crc, 4, "REMOTE_CRC32C_MISSING_OR_INVALID"),
        generation=generation,
        storage_class=storage_class,
        updated=updated,
    )


def _iter_json_records(path: Path) -> Iterable[Mapping[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise PreflightError("METADATA_INPUT_NOT_REGULAR_FILE")
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, Mapping):
                        raise PreflightError("METADATA_JSONL_ROW_NOT_MAPPING")
                    yield value
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    if not isinstance(payload, list):
        raise PreflightError("METADATA_JSON_ROOT_NOT_LIST")
    for value in payload:
        if not isinstance(value, Mapping):
            raise PreflightError("METADATA_JSON_ROW_NOT_MAPPING")
        yield value


def load_offline_listing(
    path: Path, expected_paths: set[str], *, page_size: int = DEFAULT_PAGE_SIZE
) -> tuple[dict[str, RemoteMetadata], ProviderStats]:
    selected_owners = {
        (match.group("subject"), match.group("study"))
        for value in expected_paths
        if (match := PATH_RE.fullmatch(value)) is not None
    }
    matched: dict[str, RemoteMetadata] = {}
    scanned = 0
    for record in _iter_json_records(path):
        scanned += 1
        raw_name = record.get("name") or record.get("relative_path") or record.get("gcs_uri")
        if raw_name is None:
            raise PreflightError("REMOTE_OBJECT_NAME_MISSING")
        raw_text = str(raw_name)
        bucket_prefix = f"gs://{BUCKET}/"
        if raw_text.startswith(bucket_prefix):
            raw_text = raw_text[len(bucket_prefix) :]
        match = PATH_RE.fullmatch(raw_text)
        owner = (match.group("subject"), match.group("study")) if match else None
        if raw_text not in expected_paths and owner not in selected_owners:
            continue
        metadata = normalize_remote_metadata(record)
        if metadata.relative_path in matched:
            raise PreflightError("REMOTE_LISTING_DUPLICATE_SELECTED_OBJECT")
        matched[metadata.relative_path] = metadata
    pages = math.ceil(scanned / page_size) if scanned else 0
    return matched, ProviderStats(
        provider="OFFLINE_GCS_JSON_LISTING_REPLAY",
        pages=pages,
        objects_scanned=scanned,
        selected_objects_matched=len(matched),
        operation_class="SIMULATED_CLASS_A_OBJECTS_LIST",
        media_requests=0,
        body_bytes_read=0,
    )


def _access_token(*, token_env: str, gcloud_bin: str) -> str:
    """Legacy unit-test seam; live execution uses the authority-gated source.

    Production access-token environment overrides are prohibited.  A custom
    ``TEST_*`` variable remains available to dependency-light HTTP unit tests.
    """
    token = os.environ.get(token_env, "").strip()
    if token:
        if not token_env.startswith("TEST_"):
            raise PreflightError("AMBIENT_ACCESS_TOKEN_OVERRIDE_PROHIBITED")
        return token
    executable = Path(gcloud_bin)
    if not executable.is_absolute() or not executable.exists() or not os.access(executable, os.X_OK):
        raise PreflightError("GCLOUD_NOT_AVAILABLE_FOR_ACCESS_TOKEN")
    executable = executable.resolve(strict=True)
    completed = subprocess.run(
        [str(executable), "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise PreflightError("GCLOUD_ACCESS_TOKEN_FAILED")
    return completed.stdout.strip()


def get_bucket_metadata(
    *,
    billing_project: str,
    token_env: str,
    gcloud_bin: str,
    timeout_seconds: int,
    access_token: str | None = None,
) -> BucketMetadata:
    """Read requester-pays bucket metadata without listing or reading objects."""
    if not billing_project or any(character.isspace() for character in billing_project):
        raise PreflightError("REQUESTER_PAYS_PROJECT_MISSING_OR_INVALID")
    token = access_token or _access_token(token_env=token_env, gcloud_bin=gcloud_bin)
    base_url = f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}"
    url = f"{base_url}?{urlencode({'fields': JSON_API_BUCKET_FIELDS, 'userProject': billing_project})}"
    if "/download/storage/" in url or "alt=media" in url:
        raise PreflightError("MEDIA_ENDPOINT_PROHIBITED")
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    payload = _read_gcs_json_response(
        request,
        timeout_seconds=timeout_seconds,
        purpose="GCS_BUCKET_JSON_API",
    )
    if not isinstance(payload, Mapping) or payload.get("name") != BUCKET:
        raise PreflightError("GCS_BUCKET_METADATA_SCHEMA_INVALID")
    billing = payload.get("billing")
    if not isinstance(billing, Mapping) or not isinstance(billing.get("requesterPays"), bool):
        raise PreflightError("GCS_BUCKET_REQUESTER_PAYS_METADATA_MISSING")
    fields = {
        "location": str(payload.get("location", "")).strip().upper(),
        "location_type": str(payload.get("locationType", "")).strip().lower(),
        "default_storage_class": str(payload.get("storageClass", "")).strip().upper(),
        "metageneration": str(payload.get("metageneration", "")).strip(),
        "updated": str(payload.get("updated", "")).strip(),
    }
    if not all(fields.values()) or not fields["metageneration"].isdigit():
        raise PreflightError("GCS_BUCKET_METADATA_INCOMPLETE")
    autoclass = payload.get("autoclass")
    autoclass_present = isinstance(autoclass, Mapping)
    if autoclass_present and not isinstance(autoclass.get("enabled"), bool):
        raise PreflightError("GCS_BUCKET_AUTOCLASS_METADATA_INVALID")
    autoclass_enabled = bool(autoclass.get("enabled")) if autoclass_present else False
    autoclass_toggle_time = (
        str(autoclass.get("toggleTime", "")).strip() or None
        if autoclass_present
        else None
    )
    autoclass_terminal_class = (
        str(autoclass.get("terminalStorageClass", "")).strip().upper() or None
        if autoclass_present
        else None
    )
    autoclass_terminal_update = (
        str(autoclass.get("terminalStorageClassUpdateTime", "")).strip() or None
        if autoclass_present
        else None
    )
    return BucketMetadata(
        name=BUCKET,
        requester_pays=bool(billing["requesterPays"]),
        autoclass_metadata_present=autoclass_present,
        autoclass_enabled=autoclass_enabled,
        autoclass_toggle_time=autoclass_toggle_time,
        autoclass_terminal_storage_class=autoclass_terminal_class,
        autoclass_terminal_storage_class_update_time=autoclass_terminal_update,
        provider="GCS_JSON_API_BUCKETS_GET",
        metadata_requests=1,
        media_requests=0,
        body_bytes_read=0,
        **fields,
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _jsonl_page_bytes(rows: Sequence[RemoteMetadata]) -> bytes:
    return b"".join(
        (json.dumps(row.__dict__, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )


def _commit_page_journal(path: Path, payload: bytes) -> None:
    """Atomically create one replayable listing-page journal.

    An existing byte-identical page is an expected crash-recovery case.  Any
    different content for the same request-token/page identity blocks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise PreflightError("PAGE_JOURNAL_CONTENT_MISMATCH")
        return
    temporary = path.with_name(f".{path.name}.partial")
    if temporary.is_symlink() or (temporary.exists() and not temporary.is_file()):
        raise PreflightError("PAGE_JOURNAL_PARTIAL_INVALID")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            temporary.unlink(missing_ok=True)
            raise PreflightError("PAGE_JOURNAL_CONTENT_MISMATCH")
        temporary.unlink(missing_ok=True)
        return
    os.replace(temporary, path)


def list_json_api_metadata(
    expected_paths: set[str],
    *,
    billing_project: str,
    token_env: str,
    gcloud_bin: str,
    page_size: int,
    restricted_work_dir: Path,
    resume: bool,
    timeout_seconds: int,
    access_token: str | None = None,
    authority_hashes: Mapping[str, str] | None = None,
) -> tuple[dict[str, RemoteMetadata], ProviderStats]:
    if not billing_project or any(character.isspace() for character in billing_project):
        raise PreflightError("REQUESTER_PAYS_PROJECT_MISSING_OR_INVALID")
    if page_size < 1 or page_size > 1000:
        raise PreflightError("JSON_API_PAGE_SIZE_OUT_OF_RANGE")
    token = access_token or _access_token(token_env=token_env, gcloud_bin=gcloud_bin)
    selected_owners = {
        (match.group("subject"), match.group("study"))
        for value in expected_paths
        if (match := PATH_RE.fullmatch(value)) is not None
    }
    expected_paths_sha256 = hashlib.sha256(
        ("\n".join(sorted(expected_paths)) + "\n").encode("utf-8")
    ).hexdigest()
    normalized_authority_hashes = dict(sorted((authority_hashes or {}).items()))
    if any(
        not isinstance(name, str)
        or not isinstance(value, str)
        or not SHA256_RE.fullmatch(value)
        for name, value in normalized_authority_hashes.items()
    ):
        raise PreflightError("RESUME_AUTHORITY_HASH_SET_INVALID")
    authority_hash_set_sha256 = hashlib.sha256(
        json.dumps(
            normalized_authority_hashes,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    legacy_cache_path = restricted_work_dir / "c3_gcs_selected_metadata.partial.jsonl"
    page_dir = restricted_work_dir / "c3_gcs_selected_metadata_pages.restricted"
    state_path = restricted_work_dir / "c3_gcs_listing_state.restricted.json"
    matched: dict[str, RemoteMetadata] = {}
    next_page_token: str | None = None
    pages = 0
    scanned = 0
    committed_page_files: list[str] = []
    if legacy_cache_path.exists():
        raise PreflightError("LEGACY_NONTRANSACTIONAL_RESUME_CACHE_PRESENT")
    if resume and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            state.get("schema_version") != 5
            or state.get("bucket") != BUCKET
            or state.get("prefix") != "files/"
            or state.get("fields") != JSON_API_FIELDS
            or state.get("expected_paths_sha256") != expected_paths_sha256
            or state.get("authority_hash_set_sha256") != authority_hash_set_sha256
        ):
            raise PreflightError("RESUME_STATE_AUTHORITY_MISMATCH")
        next_page_token = state.get("next_page_token")
        pages = int(state.get("pages", 0))
        scanned = int(state.get("objects_scanned", 0))
        raw_page_files = state.get("committed_page_files")
        if (
            not isinstance(raw_page_files, list)
            or len(raw_page_files) != pages
            or any(not isinstance(value, str) or not SAFE_PAGE_JOURNAL_RE.fullmatch(value) for value in raw_page_files)
            or len(set(raw_page_files)) != len(raw_page_files)
        ):
            raise PreflightError("RESUME_PAGE_JOURNAL_AUTHORITY_INVALID")
        committed_page_files = list(raw_page_files)
        for filename in committed_page_files:
            journal = page_dir / filename
            for record in _iter_json_records(journal):
                metadata = normalize_remote_metadata(record)
                if metadata.relative_path in matched:
                    raise PreflightError("RESUME_CACHE_DUPLICATE_OBJECT")
                matched[metadata.relative_path] = metadata
        if len(matched) != int(state.get("selected_objects_matched", -1)):
            raise PreflightError("RESUME_STATE_SELECTED_COUNT_MISMATCH")
        if state.get("complete") is True:
            observed_pages = {
                path.name for path in page_dir.iterdir()
                if path.is_file() and not path.is_symlink()
            } if page_dir.is_dir() else set()
            if observed_pages != set(committed_page_files):
                raise PreflightError("COMPLETE_RESUME_HAS_UNCOMMITTED_PAGE")
            return matched, ProviderStats(
                provider="GCS_JSON_API_OBJECTS_LIST",
                pages=pages,
                objects_scanned=scanned,
                selected_objects_matched=len(matched),
                operation_class="CLASS_A_OBJECTS_LIST",
                media_requests=0,
                body_bytes_read=0,
            )
    elif not resume and (state_path.exists() or page_dir.exists()):
        raise PreflightError("PARTIAL_METADATA_CACHE_EXISTS_WITHOUT_RESUME")
    elif resume and page_dir.exists():
        if page_dir.is_symlink() or not page_dir.is_dir():
            raise PreflightError("PAGE_JOURNAL_DIRECTORY_INVALID")
        orphan_pages = [path for path in page_dir.iterdir()]
        orphan_names = {path.name for path in orphan_pages}
        base_names = {
            name[1:-len(".partial")] if name.startswith(".") and name.endswith(".partial") else name
            for name in orphan_names
        }
        if (
            len(base_names) > 1
            or any(
                path.is_symlink()
                or not path.is_file()
                or not SAFE_PAGE_JOURNAL_RE.fullmatch(
                    path.name[1:-len(".partial")]
                    if path.name.startswith(".") and path.name.endswith(".partial")
                    else path.name
                )
                for path in orphan_pages
            )
        ):
            raise PreflightError("UNCOMMITTED_PAGE_JOURNAL_SET_INVALID")

    base_url = f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}/o"
    first_request = pages == 0 and next_page_token is None
    while first_request or next_page_token is not None:
        first_request = False
        params = {
            "prefix": "files/",
            "maxResults": str(page_size),
            "projection": "noAcl",
            "fields": JSON_API_FIELDS,
            "userProject": billing_project,
        }
        if next_page_token:
            params["pageToken"] = next_page_token
        url = f"{base_url}?{urlencode(params)}"
        if "/download/storage/" in url or "alt=media" in url:
            raise PreflightError("MEDIA_ENDPOINT_PROHIBITED")
        request = Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        payload = _read_gcs_json_response(
            request,
            timeout_seconds=timeout_seconds,
            purpose="GCS_JSON_API",
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("items", []), list):
            raise PreflightError("GCS_JSON_API_RESPONSE_SCHEMA_INVALID")
        request_page_token = next_page_token or "__FIRST_PAGE__"
        new_selected: list[RemoteMetadata] = []
        new_paths: set[str] = set()
        for item in payload.get("items", []):
            if not isinstance(item, Mapping):
                raise PreflightError("GCS_JSON_API_ITEM_SCHEMA_INVALID")
            scanned += 1
            name = str(item.get("name", ""))
            match = PATH_RE.fullmatch(name)
            owner = (match.group("subject"), match.group("study")) if match else None
            if name not in expected_paths and owner not in selected_owners:
                continue
            metadata = normalize_remote_metadata(item)
            if metadata.relative_path in matched or metadata.relative_path in new_paths:
                raise PreflightError("REMOTE_LISTING_DUPLICATE_SELECTED_OBJECT")
            new_paths.add(metadata.relative_path)
            new_selected.append(metadata)
        journal_name = (
            f"page_{pages:06d}_"
            f"{hashlib.sha256(request_page_token.encode('utf-8')).hexdigest()[:16]}.jsonl"
        )
        if not SAFE_PAGE_JOURNAL_RE.fullmatch(journal_name):
            raise PreflightError("PAGE_JOURNAL_NAME_INVALID")
        existing_uncommitted = {
            path.name for path in page_dir.iterdir()
            if path.name not in set(committed_page_files)
        } if page_dir.is_dir() else set()
        permitted_uncommitted = {
            journal_name,
            f".{journal_name}.partial",
        }
        if not existing_uncommitted.issubset(permitted_uncommitted):
            raise PreflightError("UNCOMMITTED_PAGE_JOURNAL_SET_INVALID")
        _commit_page_journal(page_dir / journal_name, _jsonl_page_bytes(new_selected))
        response_next_page_token = payload.get("nextPageToken")
        if response_next_page_token is not None and not isinstance(response_next_page_token, str):
            raise PreflightError("GCS_JSON_API_PAGE_TOKEN_INVALID")
        for metadata in new_selected:
            matched[metadata.relative_path] = metadata
        pages += 1
        next_page_token = response_next_page_token
        committed_page_files.append(journal_name)
        _atomic_json(
            state_path,
            {
                "schema_version": 5,
                "bucket": BUCKET,
                "prefix": "files/",
                "fields": JSON_API_FIELDS,
                "expected_paths_sha256": expected_paths_sha256,
                "authority_hash_set_sha256": authority_hash_set_sha256,
                "pages": pages,
                "objects_scanned": scanned,
                "selected_objects_matched": len(matched),
                "committed_page_files": committed_page_files,
                "next_page_token": next_page_token,
                "complete": next_page_token is None,
                "media_requests": 0,
                "body_bytes_read": 0,
            },
        )
    return matched, ProviderStats(
        provider="GCS_JSON_API_OBJECTS_LIST",
        pages=pages,
        objects_scanned=scanned,
        selected_objects_matched=len(matched),
        operation_class="CLASS_A_OBJECTS_LIST",
        media_requests=0,
        body_bytes_read=0,
    )


def _quantiles(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {name: 0 for name in ("minimum", "q1", "median", "q3", "maximum")}
    def nearest(fraction: float) -> int:
        return ordered[round((len(ordered) - 1) * fraction)]
    return {
        "minimum": ordered[0],
        "q1": nearest(0.25),
        "median": nearest(0.5),
        "q3": nearest(0.75),
        "maximum": ordered[-1],
    }


def reconcile(
    selected: Sequence[SelectedStudy],
    requests: Sequence[SourceRequest],
    metadata: Mapping[str, RemoteMetadata],
    provider: ProviderStats,
    source_stats: Mapping[str, int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    metadata_by_request = metadata
    discrepancies: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, Any]] = []
    batch_rows: dict[str, dict[str, Any]] = {}
    storage_classes: dict[str, int] = {}
    storage_class_bytes: dict[str, int] = {}
    comparable_counts = {name: 0 for name in ("size", "md5", "crc32c", "generation")}
    mismatch_counts = {name: 0 for name in ("size", "md5", "crc32c", "generation")}
    changed = 0
    verified = 0
    total_bytes = 0
    for request in requests:
        batch = batch_rows.setdefault(
            request.batch_id,
            {
                "production_batch": request.batch_id,
                "n_studies": 0,
                "n_subjects": 0,
                "n_requested_objects": 0,
                "n_verified_objects": 0,
                "n_unexpected_selected_objects": 0,
                "total_source_bytes": 0,
                "status": "PASS",
            },
        )
        batch["n_requested_objects"] += 1
        remote = metadata_by_request.get(request.relative_path)
        reasons: list[str] = []
        if remote is None:
            reasons.append("MISSING_REMOTE_OBJECT")
            batch["status"] = "FAIL"
        else:
            comparisons = (
                ("size", request.expected_size_bytes, remote.size_bytes, "HISTORICAL_SIZE_CHANGED"),
                ("md5", request.expected_md5_base64, remote.md5_base64, "HISTORICAL_MD5_CHANGED"),
                ("crc32c", request.expected_crc32c_base64, remote.crc32c_base64, "HISTORICAL_CRC32C_CHANGED"),
                ("generation", request.expected_generation, remote.generation, "HISTORICAL_GENERATION_CHANGED"),
            )
            for comparison_name, expected_value, remote_value, reason in comparisons:
                if expected_value is None:
                    continue
                comparable_counts[comparison_name] += 1
                if expected_value != remote_value:
                    mismatch_counts[comparison_name] += 1
                    reasons.append(reason)
            if reasons:
                changed += 1
                batch["status"] = "FAIL"
            else:
                verified += 1
                batch["n_verified_objects"] += 1
            total_bytes += remote.size_bytes
            batch["total_source_bytes"] += remote.size_bytes
            storage_classes[remote.storage_class] = storage_classes.get(remote.storage_class, 0) + 1
            storage_class_bytes[remote.storage_class] = (
                storage_class_bytes.get(remote.storage_class, 0) + remote.size_bytes
            )
        if reasons:
            discrepancies.append(
                {
                    "subject_id": request.subject_id,
                    "study_id": request.study_id,
                    "source_relative_path": request.relative_path,
                    "production_batch": request.batch_id,
                    "reasons": reasons,
                }
            )
        restricted_rows.append(
            {
                "release_id": request.release_id,
                "component": request.component,
                "subject_id": request.subject_id,
                "study_id": request.study_id,
                "split": request.split,
                "source_relative_path": request.relative_path,
                "gcs_uri": request.gcs_uri,
                "source_object_key": request.source_object_key,
                "production_batch": request.batch_id,
                "remote_size_bytes": remote.size_bytes if remote else None,
                "remote_md5_base64": remote.md5_base64 if remote else None,
                "remote_crc32c_base64": remote.crc32c_base64 if remote else None,
                "remote_generation": remote.generation if remote else None,
                "remote_storage_class": remote.storage_class if remote else None,
                "remote_updated": remote.updated if remote else None,
                "preflight_status": "PASS" if not reasons else "FAIL",
                "discrepancy_reasons": reasons,
            }
        )
    studies_by_batch: dict[str, set[tuple[str, str]]] = {}
    for row in selected:
        studies_by_batch.setdefault(row.batch_id, set()).add((row.subject_id, row.study_id))
    for batch_id, ownership in studies_by_batch.items():
        batch_rows.setdefault(
            batch_id,
            {
                "production_batch": batch_id,
                "n_studies": 0,
                "n_subjects": 0,
                "n_requested_objects": 0,
                "n_verified_objects": 0,
                "n_unexpected_selected_objects": 0,
                "total_source_bytes": 0,
                "status": "FAIL",
            },
        )
        batch_rows[batch_id]["n_studies"] = len({study for _, study in ownership})
        batch_rows[batch_id]["n_subjects"] = len({subject for subject, _ in ownership})

    expected_paths = {row.relative_path for row in requests}
    selected_by_pair = {(row.subject_id, row.study_id): row for row in selected}
    unexpected_paths = sorted(set(metadata) - expected_paths)
    unexpected_bytes = 0
    for relative_path in unexpected_paths:
        remote = metadata[relative_path]
        match = PATH_RE.fullmatch(relative_path)
        if match is None:
            raise PreflightError("UNEXPECTED_SELECTED_OBJECT_PATH_INVALID")
        subject_id = match.group("subject")
        study_id = match.group("study")
        selected_row = selected_by_pair.get((subject_id, study_id))
        if selected_row is None:
            raise PreflightError("UNEXPECTED_OBJECT_OUTSIDE_SELECTED_OWNERSHIP")
        unexpected_bytes += remote.size_bytes
        batch_rows[selected_row.batch_id]["n_unexpected_selected_objects"] += 1
        batch_rows[selected_row.batch_id]["status"] = "FAIL"
        discrepancies.append(
            {
                "subject_id": subject_id,
                "study_id": study_id,
                "source_relative_path": relative_path,
                "production_batch": selected_row.batch_id,
                "reasons": ["UNEXPECTED_SELECTED_PREFIX_OBJECT"],
            }
        )
        restricted_rows.append(
            {
                "release_id": RELEASE,
                "component": None,
                "subject_id": subject_id,
                "study_id": study_id,
                "split": None,
                "source_relative_path": relative_path,
                "gcs_uri": f"gs://{BUCKET}/{relative_path}",
                "source_object_key": hashlib.sha256(
                    f"{RELEASE}\0{relative_path}".encode("utf-8")
                ).hexdigest(),
                "production_batch": selected_row.batch_id,
                "remote_size_bytes": remote.size_bytes,
                "remote_md5_base64": remote.md5_base64,
                "remote_crc32c_base64": remote.crc32c_base64,
                "remote_generation": remote.generation,
                "remote_storage_class": remote.storage_class,
                "remote_updated": remote.updated,
                "preflight_status": "FAIL",
                "discrepancy_reasons": ["UNEXPECTED_SELECTED_PREFIX_OBJECT"],
            }
        )
    batch_output = [batch_rows[key] for key in sorted(batch_rows)]
    if not all(SAFE_BATCH_RE.fullmatch(row["production_batch"]) for row in batch_output):
        raise PreflightError("UNSAFE_PRODUCTION_BATCH_NAME")
    counts_by_study: dict[str, int] = {}
    for request in requests:
        counts_by_study[request.study_id] = counts_by_study.get(request.study_id, 0) + 1
    status = (
        "PASS_METADATA_ONLY"
        if verified == len(requests) and not discrepancies and provider.media_requests == 0 and provider.body_bytes_read == 0
        else "FAIL_SOURCE_PREFLIGHT"
    )
    summary = {
        "schema_version": 1,
        "status": status,
        "authority_scope": "FULL_SELECTED_SOURCE_METADATA_ONLY",
        "source_release": RELEASE,
        "selected_studies": len({row.study_id for row in selected}),
        "selected_subjects": len({row.subject_id for row in selected}),
        "raw_source_request_rows": source_stats["raw_request_rows"],
        "requested_objects": len(requests),
        "verified_objects": verified,
        "missing_objects": sum("MISSING_REMOTE_OBJECT" in row["reasons"] for row in discrepancies),
        "unexpected_selected_objects": len(unexpected_paths),
        "unexpected_selected_source_bytes": unexpected_bytes,
        "changed_objects_relative_to_historical_metadata": changed,
        "historical_metadata_comparable_counts": comparable_counts,
        "historical_metadata_mismatch_counts": mismatch_counts,
        "repeated_locator_groups_in_frozen_manifest": source_stats["repeated_locator_groups"],
        "historical_identical_rows_collapsed_before_frozen_manifest": source_stats["collapsed_rows"],
        "ownership_conflicts": source_stats["ownership_conflicts"],
        "zero_record_studies": source_stats["zero_record_studies"],
        "source_records_per_study": _quantiles(list(counts_by_study.values())),
        "exact_source_bytes": total_bytes,
        "exact_source_gib": round(total_bytes / 1024**3, 6),
        "exact_source_decimal_tb": round(total_bytes / 10**12, 9),
        "production_batches": len(batch_output),
        "storage_class_counts": dict(sorted(storage_classes.items())),
        "storage_class_bytes": dict(sorted(storage_class_bytes.items())),
        "metadata_provider": provider.provider,
        "metadata_listing_pages": provider.pages,
        "metadata_objects_scanned": provider.objects_scanned,
        "metadata_selected_objects_matched": provider.selected_objects_matched,
        "metadata_operation_class": provider.operation_class,
        "media_requests": provider.media_requests,
        "object_body_bytes_read": provider.body_bytes_read,
        "expected_metadata_list_operations": provider.pages,
        "expected_future_body_get_operations": len(requests),
        "dicom_bodies_downloaded": False,
        "models_fitted": False,
        "predictions_generated": False,
        "confirmatory_performance_accessed": False,
    }
    return summary, batch_output, restricted_rows, discrepancies


def calculate_cost(
    summary: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    rates = policy["gcs_cost"]
    gib_bytes = Decimal(str(policy["units"]["gib_bytes"]))
    total_bytes = Decimal(str(summary["exact_source_bytes"]))
    pages = Decimal(str(summary["metadata_listing_pages"]))
    bucket_metadata_operations = Decimal(str(summary.get("bucket_metadata_operations", 0)))
    objects = Decimal(str(summary["requested_objects"]))
    egress_rate = Decimal(str(rates["internet_egress_usd_per_gib"]))
    class_a_rate = Decimal(str(rates["class_a_usd_per_1000_operations"]))
    class_b_rate = Decimal(str(rates["class_b_usd_per_10000_operations"]))
    body_ops_per_object = Decimal(str(rates["body_get_operations_per_object"]))
    retry_fraction = Decimal(str(rates["retry_transfer_fraction"]))
    contingency_fraction = Decimal(str(rates["contingency_fraction"]))
    source_gib = total_bytes / gib_bytes
    metadata_cost = (pages + bucket_metadata_operations) / Decimal(1000) * class_a_rate
    egress_cost = source_gib * egress_rate
    body_operation_cost = objects * body_ops_per_object / Decimal(10000) * class_b_rate
    retrieval_rates = rates["retrieval_usd_per_gib_by_storage_class"]
    storage_class_bytes = summary.get("storage_class_bytes")
    storage_class_counts = summary.get("storage_class_counts")
    if not isinstance(storage_class_bytes, Mapping):
        raise PreflightError("STORAGE_CLASS_BYTE_TOTALS_MISSING")
    if not isinstance(storage_class_counts, Mapping):
        raise PreflightError("STORAGE_CLASS_OBJECT_COUNTS_MISSING")
    if rates.get("require_all_selected_objects_standard_for_operation_rate_authority") is not True:
        raise PreflightError("NONSTANDARD_OPERATION_RATE_MODEL_NOT_IMPLEMENTED")
    all_selected_standard = (
        set(storage_class_bytes) == {"STANDARD"}
        and set(storage_class_counts) == {"STANDARD"}
        and int(storage_class_counts["STANDARD"]) == int(objects)
        and int(storage_class_bytes["STANDARD"]) == int(total_bytes)
    )
    autoclass_metadata_present = summary.get("bucket_autoclass_metadata_present") is True
    autoclass_enabled = summary.get("bucket_autoclass_enabled") is True
    operation_pricing_authoritative = (
        all_selected_standard
        and (
            autoclass_metadata_present
            if rates["require_bucket_autoclass_metadata"]
            else True
        )
        and (
            not autoclass_enabled
            if not rates["permit_autoclass_for_cost_authority"]
            else True
        )
    )
    retrieval_cost = Decimal(0)
    for storage_class, class_bytes in storage_class_bytes.items():
        if storage_class not in retrieval_rates:
            raise PreflightError("UNPRICED_STORAGE_CLASS")
        retrieval_cost += (
            Decimal(str(class_bytes)) / gib_bytes * Decimal(str(retrieval_rates[storage_class]))
        )
    retry_cost = (egress_cost + body_operation_cost + retrieval_cost) * retry_fraction
    base = metadata_cost + egress_cost + body_operation_cost + retrieval_cost + retry_cost
    contingency = base * contingency_fraction
    total = base + contingency
    money = lambda value: format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")
    return {
        "schema_version": 1,
        "status": (
            "PASS_RATE_EXPLICIT_PLANNING_ESTIMATE"
            if summary.get("authoritative_for_full_c3") is True
            and summary.get("bucket_location_rate_match") is True
            and operation_pricing_authoritative
            else "NONAUTHORITATIVE_OR_INCOMPLETE_PLANNING_ESTIMATE"
        ),
        "currency": rates["currency"],
        "rate_authority": rates["rate_authority"],
        "pricing_verified_date": rates["pricing_verified_date"],
        "bucket_location_assumption": rates["bucket_location_assumption"],
        "bucket_location_verified": summary.get("bucket_location_rate_match") is True,
        "bucket_location": summary.get("bucket_location", "UNVERIFIED"),
        "bucket_requester_pays_enabled": summary.get("bucket_requester_pays_enabled") is True,
        "bucket_autoclass_metadata_present": autoclass_metadata_present,
        "bucket_autoclass_enabled": autoclass_enabled,
        "selected_storage_classes_all_standard": all_selected_standard,
        "operation_pricing_authoritative": operation_pricing_authoritative,
        "operation_pricing_scope": rates["operation_pricing_scope"],
        "exact_source_bytes": int(total_bytes),
        "exact_source_gib": format(source_gib.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f"),
        "metadata_list_class_a_operations": int(pages),
        "bucket_metadata_class_a_operations": int(bucket_metadata_operations),
        "total_metadata_class_a_operations": int(pages + bucket_metadata_operations),
        "future_body_get_class_b_operations": int(objects * body_ops_per_object),
        "internet_egress_usd_per_gib": str(egress_rate),
        "class_a_usd_per_1000_operations": str(class_a_rate),
        "class_b_usd_per_10000_operations": str(class_b_rate),
        "metadata_preflight_operation_cost_usd": money(metadata_cost),
        "one_pass_source_egress_cost_usd": money(egress_cost),
        "one_pass_body_get_operation_cost_usd": money(body_operation_cost),
        "storage_class_bytes": dict(sorted(storage_class_bytes.items())),
        "storage_class_counts": dict(sorted(storage_class_counts.items())),
        "one_pass_retrieval_cost_usd": money(retrieval_cost),
        "retry_fraction": str(retry_fraction),
        "retry_contingency_cost_usd": money(retry_cost),
        "subtotal_before_general_contingency_usd": money(base),
        "general_contingency_fraction": str(contingency_fraction),
        "general_contingency_cost_usd": money(contingency),
        "projected_requester_pays_total_usd": money(total),
        "invoice_guarantee": False,
    }


def _reject_restricted_output_in_repository(path: Path) -> Path:
    repository = Path(__file__).resolve().parents[1]
    resolved = path.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(repository)
    except ValueError:
        pass
    else:
        raise PreflightError("RESTRICTED_OUTPUT_INSIDE_REPOSITORY")
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise PreflightError("RESTRICTED_OUTPUT_ROOT_TOO_BROAD")
    return resolved


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_exclusive(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_csv_exclusive(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise PreflightError("EMPTY_BATCH_OUTPUT")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
    source_manifest = bind_approved_restricted_path(
        args.source_manifest,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    selected_studies_path = bind_approved_restricted_path(
        args.selected_studies,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    split_map_path = bind_approved_restricted_path(
        args.split_map,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    policy = yaml.safe_load(args.resource_policy.read_text(encoding="utf-8"))
    if not isinstance(policy, Mapping) or policy.get("schema_version") != 1:
        raise PreflightError("RESOURCE_POLICY_INVALID")
    batch_size = int(policy["cohort"]["deterministic_batch_size_studies"])
    selected = load_selected_studies(
        selected_studies_path,
        batch_size=batch_size,
        expected_sha256=args.expected_selected_manifest_sha256,
        expected_studies=args.expected_selected_studies,
    )
    split_by_subject = load_split_map(
        split_map_path,
        selected,
        expected_sha256=args.expected_split_manifest_sha256,
        expected_counts=args.expected_split_counts,
    )
    requests, source_stats = load_source_requests(
        source_manifest,
        selected,
        split_by_subject=split_by_subject,
        expected_sha256=args.expected_source_manifest_sha256,
        expected_requests=args.expected_source_requests,
        expected_raw_rows=args.expected_raw_request_rows,
        expected_collapsed_rows=args.expected_collapsed_rows,
    )
    restricted_dir = bind_approved_restricted_path(
        args.restricted_output_dir,
        policy=safe_policy,
        must_exist=False,
        expect="directory",
        root_kind="direct",
        create=True,
    )
    aggregate_dir = bind_approved_restricted_path(
        args.aggregate_output_dir,
        policy=safe_policy,
        must_exist=False,
        expect="directory",
        root_kind="staging",
        create=True,
    )
    expected_paths = {row.relative_path for row in requests}
    if args.metadata_json is not None:
        metadata_json = bind_approved_restricted_path(
            args.metadata_json,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        metadata, provider = load_offline_listing(metadata_json, expected_paths)
        bucket_metadata = BucketMetadata(
            name=BUCKET,
            location="UNVERIFIED_OFFLINE_REPLAY",
            location_type="unverified",
            default_storage_class="UNVERIFIED",
            requester_pays=False,
            autoclass_metadata_present=False,
            autoclass_enabled=False,
            autoclass_toggle_time=None,
            autoclass_terminal_storage_class=None,
            autoclass_terminal_storage_class_update_time=None,
            metageneration="0",
            updated="UNVERIFIED",
            provider="OFFLINE_BUCKET_METADATA_UNAVAILABLE",
            metadata_requests=0,
            media_requests=0,
            body_bytes_read=0,
        )
    else:
        billing_project = os.environ.get(args.billing_project_env, "")
        authority_receipt = bind_approved_restricted_path(
            args.gcp_authority_receipt,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        validate_restricted_receipt(
            authority_receipt,
            expected_sha256=args.expected_gcp_authority_receipt_sha256,
            billing_project=billing_project,
        )
        access_token = acquire_access_token_for_preflight(
            gcloud_bin=args.gcloud_bin,
            timeout_seconds=args.timeout_seconds,
        )
        bucket_metadata = get_bucket_metadata(
            billing_project=billing_project,
            token_env=args.access_token_env,
            gcloud_bin=args.gcloud_bin,
            timeout_seconds=args.timeout_seconds,
            access_token=access_token,
        )
        metadata, provider = list_json_api_metadata(
            expected_paths,
            billing_project=billing_project,
            token_env=args.access_token_env,
            gcloud_bin=args.gcloud_bin,
            page_size=args.page_size,
            restricted_work_dir=restricted_dir,
            resume=args.resume,
            timeout_seconds=args.timeout_seconds,
            access_token=access_token,
            authority_hashes={
                "selected_source_manifest_sha256": sha256_file(source_manifest),
                "selected_studies_sha256": sha256_file(selected_studies_path),
                "split_map_sha256": sha256_file(split_map_path),
                "gcp_authority_receipt_sha256": sha256_file(authority_receipt),
            },
        )
    summary, batches, restricted_rows, discrepancies = reconcile(
        selected, requests, metadata, provider, source_stats
    )
    if provider.provider == "OFFLINE_GCS_JSON_LISTING_REPLAY":
        if summary["status"] == "PASS_METADATA_ONLY":
            summary["status"] = "PASS_OFFLINE_REPLAY_NONAUTHORITATIVE"
        summary["authoritative_for_full_c3"] = False
    else:
        expected_locations = {
            str(value).strip().upper()
            for value in policy["gcs_cost"]["expected_bucket_locations"]
        }
        location_passed = bucket_metadata.location in expected_locations
        requester_pays_passed = (
            bucket_metadata.requester_pays
            if policy["gcs_cost"]["require_requester_pays_enabled"]
            else True
        )
        if not location_passed or not requester_pays_passed:
            summary["status"] = "FAIL_BUCKET_METADATA_PREFLIGHT"
        summary["authoritative_for_full_c3"] = (
            summary["status"] == "PASS_METADATA_ONLY"
            and location_passed
            and requester_pays_passed
        )
    summary.update(
        {
            "bucket_metadata_provider": bucket_metadata.provider,
            "bucket_location": bucket_metadata.location,
            "bucket_location_type": bucket_metadata.location_type,
            "bucket_default_storage_class": bucket_metadata.default_storage_class,
            "bucket_requester_pays_enabled": bucket_metadata.requester_pays,
            "bucket_autoclass_metadata_present": bucket_metadata.autoclass_metadata_present,
            "bucket_autoclass_enabled": bucket_metadata.autoclass_enabled,
            "bucket_autoclass_toggle_time": bucket_metadata.autoclass_toggle_time,
            "bucket_autoclass_terminal_storage_class": (
                bucket_metadata.autoclass_terminal_storage_class
            ),
            "bucket_autoclass_terminal_storage_class_update_time": (
                bucket_metadata.autoclass_terminal_storage_class_update_time
            ),
            "bucket_metageneration": bucket_metadata.metageneration,
            "bucket_updated": bucket_metadata.updated,
            "bucket_metadata_operations": bucket_metadata.metadata_requests,
            "bucket_metadata_media_requests": bucket_metadata.media_requests,
            "bucket_metadata_body_bytes_read": bucket_metadata.body_bytes_read,
            "bucket_location_rate_match": bucket_metadata.location
            in {
                str(value).strip().upper()
                for value in policy["gcs_cost"]["expected_bucket_locations"]
            },
        }
    )
    summary["selected_manifest_sha256"] = sha256_file(selected_studies_path)
    summary["split_manifest_sha256"] = sha256_file(split_map_path)
    summary["split_counts"] = {
        name: sum(value == name for value in split_by_subject.values())
        for name in sorted(args.expected_split_counts)
    }
    summary["selected_source_manifest_sha256"] = sha256_file(source_manifest)
    cost = calculate_cost(summary, policy)
    safety_passed = (
        provider.media_requests == 0
        and provider.body_bytes_read == 0
        and bucket_metadata.media_requests == 0
        and bucket_metadata.body_bytes_read == 0
    )
    safety = {
        "schema_version": 1,
        "status": "PASS" if safety_passed else "FAIL",
        "safety_gate_passed": safety_passed,
        "issues": [] if safety_passed else ["OBJECT_BODY_ACCESS_DETECTED"],
        "metadata_only_provider": True,
        "cloud_storage_json_api_objects_list_only": provider.provider == "GCS_JSON_API_OBJECTS_LIST",
        "offline_listing_replay": provider.provider == "OFFLINE_GCS_JSON_LISTING_REPLAY",
        "media_requests": provider.media_requests,
        "object_body_bytes_read": provider.body_bytes_read,
        "dicom_bodies_downloaded": False,
        "restricted_detail_outside_repository": True,
        "aggregate_contains_subject_or_study_identifiers": False,
        "aggregate_contains_object_locators": False,
        "aggregate_contains_object_hashes": False,
        "models_fitted": False,
        "predictions_generated": False,
        "confirmatory_performance_accessed": False,
    }
    targets = {
        "summary": aggregate_dir / "c3_full_source_preflight.summary.json",
        "batch": aggregate_dir / "c3_full_source_preflight_by_batch.csv",
        "cost": aggregate_dir / "c3_full_source_cost_estimate.json",
        "safety": aggregate_dir / "c3_full_source_preflight_safety_gate.json",
        "detail": restricted_dir / "c3_full_source_object_metadata.restricted.jsonl",
        "discrepancy": restricted_dir / "c3_full_source_discrepancies.restricted.jsonl",
    }
    if any(path.exists() for path in targets.values()):
        raise PreflightError("FINAL_OUTPUT_ALREADY_EXISTS")
    _write_jsonl_exclusive(targets["detail"], restricted_rows)
    _write_jsonl_exclusive(targets["discrepancy"], discrepancies)
    _write_json_exclusive(targets["summary"], summary)
    _write_csv_exclusive(targets["batch"], batches)
    _write_json_exclusive(targets["cost"], cost)
    _write_json_exclusive(targets["safety"], safety)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--split-map", type=Path, required=True)
    parser.add_argument("--expected-source-manifest-sha256", required=True)
    parser.add_argument("--expected-selected-manifest-sha256", default=EXPECTED_SELECTED_SHA256)
    parser.add_argument("--expected-split-manifest-sha256", required=True)
    parser.add_argument("--resource-policy", type=Path, required=True)
    parser.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    parser.add_argument("--metadata-json", type=Path)
    parser.add_argument("--billing-project-env", default="LVEF_C3_GCP_BILLING_PROJECT")
    parser.add_argument("--access-token-env", default="GOOGLE_OAUTH_ACCESS_TOKEN")
    parser.add_argument("--gcloud-bin", default="")
    parser.add_argument("--gcp-authority-receipt", type=Path)
    parser.add_argument("--expected-gcp-authority-receipt-sha256")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-selected-studies", type=int, default=EXPECTED_SELECTED_STUDIES, help=argparse.SUPPRESS)
    parser.set_defaults(expected_split_counts=EXPECTED_SPLIT_COUNTS)
    parser.add_argument("--expected-source-requests", type=int, default=EXPECTED_NORMALIZED_REQUESTS, help=argparse.SUPPRESS)
    parser.add_argument("--expected-raw-request-rows", type=int, default=EXPECTED_RAW_REQUEST_ROWS, help=argparse.SUPPRESS)
    parser.add_argument("--expected-collapsed-rows", type=int, default=EXPECTED_COLLAPSED_ROWS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.metadata_json is not None and args.resume:
        parser.error("--resume is only valid for the live JSON API provider")
    if args.metadata_json is None and (
        args.gcp_authority_receipt is None
        or not args.expected_gcp_authority_receipt_sha256
    ):
        parser.error(
            "live provider requires --gcp-authority-receipt and "
            "--expected-gcp-authority-receipt-sha256"
        )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = execute(args)
    except (
        PreflightError,
        GCPAuthorityError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": summary["status"],
                "selected_studies": summary["selected_studies"],
                "requested_objects": summary["requested_objects"],
                "verified_objects": summary["verified_objects"],
                "missing_objects": summary["missing_objects"],
                "exact_source_bytes": summary["exact_source_bytes"],
                "metadata_listing_pages": summary["metadata_listing_pages"],
                "object_body_bytes_read": summary["object_body_bytes_read"],
            },
            sort_keys=True,
        )
    )
    return 0 if summary["status"] in {
        "PASS_METADATA_ONLY",
        "PASS_OFFLINE_REPLAY_NONAUTHORITATIVE",
    } else 4


if __name__ == "__main__":
    sys.exit(main())
