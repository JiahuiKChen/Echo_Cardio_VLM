#!/usr/bin/env python3
"""Bounded, outcome-blind downloader for the Phase 1E-A reconstruction smoke.

The input and detailed report are restricted artifacts.  Standard output and
the aggregate JSON intentionally contain counts/status only.  This utility
never authenticates, selects a cohort, reads labels, or downloads a prefix: it
uses an already-authenticated ``gsutil`` process to list and copy the exact
objects declared by a four-study train-only source manifest.
"""
from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Any, Sequence
from uuid import uuid4


MIMIC_ECHO_BUCKET = "mimic-iv-echo-1.0.physionet.org"
MAX_STUDIES = 4
MAX_OBJECTS = 1000
MAX_TOTAL_BYTES = 5 * 1024**3
MIN_FREE_BYTES = 20 * 1024**3
GSUTIL_LIST_CHUNK = 100
EXPECTED_SMOKE_ROLES = (
    "batch_000_duplicate_affected_or_prespecified_fallback",
    "batch_001_008_historical_cine_positive",
    "stage_d_historical_cine_positive_with_surviving_npz",
    "historical_no_cine_negative_control",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")

SUBJECT_COLUMNS = ("subject_id",)
STUDY_COLUMNS = ("study_id",)
SPLIT_COLUMNS = ("split",)
LOCATOR_COLUMNS = (
    "gcs_uri",
    "source_uri",
    "object_uri",
    "relative_path",
    "gcs_object_path",
    "dicom_filepath",
)
SIZE_COLUMNS = ("expected_size_bytes", "size_bytes")
CHECKSUM_COLUMNS = ("expected_sha256", "sha256")
ROLE_COLUMNS = ("smoke_role", "technical_stratum")
SOURCE_RELATIVE_COLUMNS = ("source_relative_path",)
TECHNICAL_METADATA_COLUMNS = (
    "release_id",
    "source_bucket",
    "component",
    "source_object_key",
    "selection_basis",
)
ALLOWED_COLUMNS = set(
    SUBJECT_COLUMNS
    + STUDY_COLUMNS
    + SPLIT_COLUMNS
    + LOCATOR_COLUMNS
    + SIZE_COLUMNS
    + CHECKSUM_COLUMNS
    + ROLE_COLUMNS
    + SOURCE_RELATIVE_COLUMNS
    + TECHNICAL_METADATA_COLUMNS
)
FORBIDDEN_COLUMN_TOKENS = (
    "label",
    "lvef",
    "measurement",
    "outcome",
    "prediction",
    "performance",
    "metric",
    "target",
    "y_true",
    "y_pred",
)


class SmokeDownloadError(RuntimeError):
    """Expected fail-closed error carrying a non-sensitive code."""

    def __init__(self, code: str, *, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class SourceObject:
    subject_id: str
    study_id: str
    split: str
    relative_path: str
    gcs_uri: str
    expected_size_bytes: int | None
    expected_sha256: str | None
    smoke_role: str | None


@dataclass(frozen=True)
class GCSObjectMetadata:
    """Restricted object metadata returned by ``gsutil stat``."""

    gcs_uri: str
    size_bytes: int
    md5_base64: str
    crc32c_base64: str
    generation: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_digests(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, str]:
    """Return local MD5 in GCS base64 form plus SHA-256 hex in one pass."""

    md5_digest = hashlib.md5(usedforsecurity=False)
    sha256_digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            md5_digest.update(chunk)
            sha256_digest.update(chunk)
    return (
        base64.b64encode(md5_digest.digest()).decode("ascii"),
        sha256_digest.hexdigest(),
    )


def _one_column(fieldnames: Sequence[str], candidates: Sequence[str], *, required: bool) -> str | None:
    found = [name for name in candidates if name in fieldnames]
    if len(found) > 1:
        raise SmokeDownloadError("AMBIGUOUS_MANIFEST_COLUMNS")
    if required and not found:
        raise SmokeDownloadError("MISSING_REQUIRED_MANIFEST_COLUMN")
    return found[0] if found else None


def safe_relative_object_path(value: str) -> str:
    raw = str(value).strip()
    if not raw or raw.startswith(("/", "~")) or "\\" in raw or "\x00" in raw:
        raise SmokeDownloadError("UNSAFE_OBJECT_PATH")
    path = PurePosixPath(raw)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SmokeDownloadError("UNSAFE_OBJECT_PATH")
    if any(not SAFE_PART_RE.fullmatch(part) for part in parts):
        raise SmokeDownloadError("UNSAFE_OBJECT_PATH")
    if parts[0] != "files" or path.suffix.lower() != ".dcm":
        raise SmokeDownloadError("OBJECT_OUTSIDE_MIMIC_ECHO_DICOM_SCOPE")
    return path.as_posix()


def canonicalize_locator(value: str, bucket: str = MIMIC_ECHO_BUCKET) -> tuple[str, str]:
    raw = str(value).strip()
    if raw.startswith("gs://"):
        prefix = f"gs://{bucket}/"
        if not raw.startswith(prefix):
            raise SmokeDownloadError("REMOTE_BUCKET_NOT_ALLOWED")
        relative = raw[len(prefix) :]
    else:
        relative = raw
    relative = safe_relative_object_path(relative)
    return relative, f"gs://{bucket}/{relative}"


def _parse_optional_size(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not text.isdigit():
        raise SmokeDownloadError("INVALID_EXPECTED_SIZE")
    parsed = int(text)
    if parsed < 0:
        raise SmokeDownloadError("INVALID_EXPECTED_SIZE")
    return parsed


def _parse_optional_sha256(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    parsed = str(value).strip().lower()
    if not SHA256_RE.fullmatch(parsed):
        raise SmokeDownloadError("INVALID_EXPECTED_SHA256")
    return parsed


def load_source_manifest(path: Path, bucket: str = MIMIC_ECHO_BUCKET) -> list[SourceObject]:
    if path.is_symlink() or not path.is_file():
        raise SmokeDownloadError("SOURCE_MANIFEST_NOT_REGULAR_FILE")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [str(name).strip() for name in (reader.fieldnames or [])]
        if not fieldnames or len(fieldnames) != len(set(fieldnames)):
            raise SmokeDownloadError("INVALID_MANIFEST_HEADER")
        forbidden = {
            name
            for name in fieldnames
            if any(token in name.lower() for token in FORBIDDEN_COLUMN_TOKENS)
        }
        unknown = set(fieldnames) - ALLOWED_COLUMNS
        if forbidden or unknown:
            raise SmokeDownloadError("NONTECHNICAL_OR_UNKNOWN_MANIFEST_COLUMN")

        subject_col = _one_column(fieldnames, SUBJECT_COLUMNS, required=True)
        study_col = _one_column(fieldnames, STUDY_COLUMNS, required=True)
        split_col = _one_column(fieldnames, SPLIT_COLUMNS, required=True)
        locator_col = _one_column(fieldnames, LOCATOR_COLUMNS, required=True)
        size_col = _one_column(fieldnames, SIZE_COLUMNS, required=False)
        checksum_col = _one_column(fieldnames, CHECKSUM_COLUMNS, required=False)
        role_col = _one_column(fieldnames, ROLE_COLUMNS, required=False)
        source_relative_col = _one_column(
            fieldnames, SOURCE_RELATIVE_COLUMNS, required=False
        )
        assert subject_col and study_col and split_col and locator_col

        objects: list[SourceObject] = []
        for row in reader:
            subject_id = str(row.get(subject_col, "")).strip()
            study_id = str(row.get(study_col, "")).strip()
            split = str(row.get(split_col, "")).strip().lower()
            if not subject_id or not study_id:
                raise SmokeDownloadError("MISSING_OWNERSHIP_VALUE")
            if split != "train":
                raise SmokeDownloadError("NONTRAIN_SMOKE_ROW")
            relative, uri = canonicalize_locator(str(row.get(locator_col, "")), bucket=bucket)
            if source_relative_col:
                declared_relative = safe_relative_object_path(
                    str(row.get(source_relative_col, ""))
                )
                if declared_relative != relative:
                    raise SmokeDownloadError("SOURCE_LOCATOR_RELATIVE_PATH_MISMATCH")
            objects.append(
                SourceObject(
                    subject_id=subject_id,
                    study_id=study_id,
                    split=split,
                    relative_path=relative,
                    gcs_uri=uri,
                    expected_size_bytes=_parse_optional_size(row.get(size_col) if size_col else None),
                    expected_sha256=_parse_optional_sha256(
                        row.get(checksum_col) if checksum_col else None
                    ),
                    smoke_role=(str(row.get(role_col, "")).strip() or None) if role_col else None,
                )
            )

    validate_source_objects(objects)
    return objects


def validate_source_objects(objects: Sequence[SourceObject]) -> None:
    if not objects:
        raise SmokeDownloadError("EMPTY_SOURCE_MANIFEST")
    if len(objects) > MAX_OBJECTS:
        raise SmokeDownloadError("OBJECT_COUNT_CAP_EXCEEDED")
    studies = {item.study_id for item in objects}
    subjects = {item.subject_id for item in objects}
    if len(studies) != MAX_STUDIES or len(subjects) != MAX_STUDIES:
        raise SmokeDownloadError("SMOKE_REQUIRES_EXACTLY_FOUR_STUDIES_AND_SUBJECTS")
    study_to_subject: dict[str, str] = {}
    subject_to_study: dict[str, str] = {}
    study_to_role: dict[str, str] = {}
    role_to_study: dict[str, str] = {}
    for item in objects:
        if not item.subject_id.isdigit() or not item.study_id.isdigit():
            raise SmokeDownloadError("NONCANONICAL_OWNERSHIP_VALUE")
        path_parts = PurePosixPath(item.relative_path).parts
        if (
            len(path_parts) != 5
            or path_parts[1] != f"p{item.subject_id[:2]}"
            or path_parts[2] != f"p{item.subject_id}"
            or path_parts[3] != f"s{item.study_id}"
        ):
            raise SmokeDownloadError("SOURCE_PATH_OWNERSHIP_MISMATCH")
        prior_subject = study_to_subject.setdefault(item.study_id, item.subject_id)
        prior_study = subject_to_study.setdefault(item.subject_id, item.study_id)
        if prior_subject != item.subject_id or prior_study != item.study_id:
            raise SmokeDownloadError("NONBIJECTIVE_STUDY_SUBJECT_OWNERSHIP")
        if item.smoke_role is None:
            raise SmokeDownloadError("MISSING_SMOKE_ROLE")
        prior_role = study_to_role.setdefault(item.study_id, item.smoke_role)
        prior_role_study = role_to_study.setdefault(item.smoke_role, item.study_id)
        if prior_role != item.smoke_role or prior_role_study != item.study_id:
            raise SmokeDownloadError("NONBIJECTIVE_STUDY_SMOKE_ROLE")
    if len(study_to_role) != MAX_STUDIES or len(role_to_study) != MAX_STUDIES:
        raise SmokeDownloadError("SMOKE_REQUIRES_FOUR_DISTINCT_ROLES")
    if set(role_to_study) != set(EXPECTED_SMOKE_ROLES):
        raise SmokeDownloadError("SMOKE_ROLE_SET_NOT_AUTHORIZED")
    paths = [item.relative_path for item in objects]
    uris = [item.gcs_uri for item in objects]
    if len(paths) != len(set(paths)) or len(uris) != len(set(uris)):
        raise SmokeDownloadError("DUPLICATE_SOURCE_OBJECT")
    sizes_present = [item.expected_size_bytes is not None for item in objects]
    checksums_present = [item.expected_sha256 is not None for item in objects]
    if any(sizes_present) and not all(sizes_present):
        raise SmokeDownloadError("PARTIAL_EXPECTED_SIZE_COVERAGE")
    if any(checksums_present) and not all(checksums_present):
        raise SmokeDownloadError("PARTIAL_EXPECTED_CHECKSUM_COVERAGE")


def parse_gsutil_ls_long(stdout: str, bucket: str = MIMIC_ECHO_BUCKET) -> dict[str, int]:
    remote: dict[str, int] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("TOTAL:"):
            continue
        match = re.match(r"^(\d+)\s+\S+\s+(gs://\S+)\s*$", line)
        if not match:
            continue
        size = int(match.group(1))
        relative, uri = canonicalize_locator(match.group(2), bucket=bucket)
        del relative
        if uri in remote and remote[uri] != size:
            raise SmokeDownloadError("REMOTE_LISTING_DUPLICATE_CONFLICT")
        remote[uri] = size
    return remote


def _parse_gcs_base64_hash(value: str, *, decoded_bytes: int, code: str) -> str:
    text = str(value).strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise SmokeDownloadError(code) from None
    if len(decoded) != decoded_bytes or base64.b64encode(decoded).decode("ascii") != text:
        raise SmokeDownloadError(code)
    return text


def parse_gsutil_stat(
    stdout: str, bucket: str = MIMIC_ECHO_BUCKET
) -> dict[str, GCSObjectMetadata]:
    """Parse exact-object ``gsutil stat`` output without emitting identifiers."""

    parsed: dict[str, GCSObjectMetadata] = {}
    current_uri: str | None = None
    current: dict[str, str] = {}

    def finish() -> None:
        nonlocal current_uri, current
        if current_uri is None:
            return
        if current_uri in parsed:
            raise SmokeDownloadError("REMOTE_STAT_DUPLICATE_OBJECT")
        size_text = current.get("content-length")
        if size_text is None or not size_text.isdigit():
            raise SmokeDownloadError("REMOTE_STAT_SIZE_MISSING_OR_INVALID")
        md5_text = current.get("hash-md5")
        if md5_text is None:
            raise SmokeDownloadError("REMOTE_STAT_MD5_MISSING")
        md5_base64 = _parse_gcs_base64_hash(
            md5_text, decoded_bytes=16, code="REMOTE_STAT_MD5_INVALID"
        )
        crc32c_text = current.get("hash-crc32c")
        if crc32c_text is None:
            raise SmokeDownloadError("REMOTE_STAT_CRC32C_MISSING")
        crc32c_base64 = _parse_gcs_base64_hash(
            crc32c_text,
            decoded_bytes=4,
            code="REMOTE_STAT_CRC32C_INVALID",
        )
        generation = current.get("generation")
        if generation is None:
            raise SmokeDownloadError("REMOTE_STAT_GENERATION_MISSING")
        if not generation.isdigit():
            raise SmokeDownloadError("REMOTE_STAT_GENERATION_INVALID")
        parsed[current_uri] = GCSObjectMetadata(
            gcs_uri=current_uri,
            size_bytes=int(size_text),
            md5_base64=md5_base64,
            crc32c_base64=crc32c_base64,
            generation=generation,
        )
        current_uri = None
        current = {}

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("gs://"):
            if not stripped.endswith(":"):
                raise SmokeDownloadError("REMOTE_STAT_HEADER_INVALID")
            finish()
            _, current_uri = canonicalize_locator(stripped[:-1], bucket=bucket)
            continue
        if current_uri is None:
            raise SmokeDownloadError("REMOTE_STAT_OUTPUT_BEFORE_HEADER")
        match = re.fullmatch(r"([^:]+):\s*(.*)", stripped)
        if not match:
            raise SmokeDownloadError("REMOTE_STAT_LINE_INVALID")
        label = match.group(1).strip().lower()
        value = match.group(2).strip()
        normalized: str | None = None
        if label == "content-length":
            normalized = "content-length"
        elif label == "hash (md5)":
            normalized = "hash-md5"
        elif label == "hash (crc32c)":
            normalized = "hash-crc32c"
        elif label == "generation":
            normalized = "generation"
        if normalized is not None:
            if normalized in current and current[normalized] != value:
                raise SmokeDownloadError("REMOTE_STAT_FIELD_CONFLICT")
            current[normalized] = value
    finish()
    return parsed


def _safe_subprocess_failure(code: str, completed: subprocess.CompletedProcess[str]) -> SmokeDownloadError:
    stderr = completed.stderr or ""
    stdout = completed.stdout or ""
    return SmokeDownloadError(
        code,
        detail={
            "returncode": int(completed.returncode),
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "stderr_sha256": hashlib.sha256(stderr.encode("utf-8", errors="replace")).hexdigest(),
            "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8", errors="replace")).hexdigest(),
        },
    )


def list_remote_objects(
    objects: Sequence[SourceObject], *, gsutil_bin: str, billing_project: str
) -> dict[str, int]:
    study_patterns: list[str] = []
    study_to_parent: dict[str, str] = {}
    for item in objects:
        parent = PurePosixPath(item.relative_path).parent.as_posix()
        prior = study_to_parent.setdefault(item.study_id, parent)
        if prior != parent:
            raise SmokeDownloadError("STUDY_HAS_MULTIPLE_REMOTE_PREFIXES")
    for parent in sorted(set(study_to_parent.values())):
        study_patterns.append(f"gs://{MIMIC_ECHO_BUCKET}/{parent}/*.dcm")
    if len(study_patterns) != MAX_STUDIES:
        raise SmokeDownloadError("REMOTE_PREFIX_COUNT_MISMATCH")

    remote: dict[str, int] = {}
    for start in range(0, len(study_patterns), GSUTIL_LIST_CHUNK):
        chunk = study_patterns[start : start + GSUTIL_LIST_CHUNK]
        command = [gsutil_bin, "-u", billing_project, "ls", "-l"] + chunk
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise _safe_subprocess_failure("REMOTE_LISTING_FAILED", completed)
        parsed = parse_gsutil_ls_long(completed.stdout)
        overlap = set(remote) & set(parsed)
        if overlap:
            raise SmokeDownloadError("REMOTE_LISTING_DUPLICATE_OBJECT")
        remote.update(parsed)
    expected = {item.gcs_uri for item in objects}
    if set(remote) != expected:
        raise SmokeDownloadError("REMOTE_OBJECT_SET_MISMATCH")
    return remote


def stat_remote_objects(
    objects: Sequence[SourceObject], *, gsutil_bin: str, billing_project: str
) -> dict[str, GCSObjectMetadata]:
    """Fetch integrity metadata for the exact requested URIs."""

    expected = {item.gcs_uri for item in objects}
    remote: dict[str, GCSObjectMetadata] = {}
    uris = sorted(expected)
    for start in range(0, len(uris), GSUTIL_LIST_CHUNK):
        chunk = uris[start : start + GSUTIL_LIST_CHUNK]
        completed = subprocess.run(
            [gsutil_bin, "-u", billing_project, "stat", *chunk],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise _safe_subprocess_failure("REMOTE_STAT_FAILED", completed)
        parsed = parse_gsutil_stat(completed.stdout)
        if set(parsed) != set(chunk):
            raise SmokeDownloadError("REMOTE_STAT_SET_MISMATCH")
        if set(remote) & set(parsed):
            raise SmokeDownloadError("REMOTE_STAT_DUPLICATE_OBJECT")
        remote.update(parsed)
    if set(remote) != expected:
        raise SmokeDownloadError("REMOTE_STAT_SET_MISMATCH")
    return remote


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def reject_inside_repository(path: Path, code: str) -> None:
    repository = Path(__file__).resolve().parents[1]
    resolved = path.expanduser().resolve(strict=False)
    if resolved == repository or _is_relative_to(resolved, repository):
        raise SmokeDownloadError(code)


def validate_scoped_root(root: Path) -> Path:
    expanded = root.expanduser()
    reject_inside_repository(expanded, "DOWNLOAD_ROOT_INSIDE_REPOSITORY")
    if expanded.is_symlink():
        raise SmokeDownloadError("DOWNLOAD_ROOT_IS_SYMLINK")
    expanded.mkdir(parents=True, exist_ok=True)
    resolved = expanded.resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise SmokeDownloadError("DOWNLOAD_ROOT_TOO_BROAD")
    if resolved.is_symlink() or not resolved.is_dir():
        raise SmokeDownloadError("DOWNLOAD_ROOT_INVALID")
    return resolved


def ensure_no_symlink_components(root: Path, destination: Path) -> None:
    if not _is_relative_to(destination, root):
        raise SmokeDownloadError("DESTINATION_ESCAPES_DOWNLOAD_ROOT")
    current = root
    for part in destination.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise SmokeDownloadError("SYMLINK_IN_DOWNLOAD_SCOPE")


def inventory_download_root(root: Path, expected_paths: set[str]) -> dict[str, Path]:
    actual: dict[str, Path] = {}
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            candidate = current / name
            if candidate.is_symlink():
                raise SmokeDownloadError("SYMLINK_IN_DOWNLOAD_SCOPE")
        for name in file_names:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                raise SmokeDownloadError("NONREGULAR_FILE_IN_DOWNLOAD_SCOPE")
            relative = candidate.relative_to(root).as_posix()
            safe_relative_object_path(relative)
            actual[relative] = candidate
    extras = set(actual) - expected_paths
    if extras:
        raise SmokeDownloadError("EXTRA_FILE_IN_DOWNLOAD_SCOPE")
    return actual


def reconcile_remote_metadata(
    objects: Sequence[SourceObject],
    remote_sizes: dict[str, int],
    remote_metadata: dict[str, GCSObjectMetadata],
) -> tuple[int, list[SourceObject]]:
    total = 0
    updated: list[SourceObject] = []
    for item in objects:
        remote_size = remote_sizes[item.gcs_uri]
        stat_metadata = remote_metadata[item.gcs_uri]
        if remote_size != stat_metadata.size_bytes:
            raise SmokeDownloadError("REMOTE_LISTING_STAT_SIZE_DISAGREEMENT")
        if item.expected_size_bytes is not None and item.expected_size_bytes != remote_size:
            raise SmokeDownloadError("MANIFEST_REMOTE_SIZE_DISAGREEMENT")
        total += remote_size
        values = asdict(item)
        values["expected_size_bytes"] = remote_size
        updated.append(SourceObject(**values))
    if total > MAX_TOTAL_BYTES:
        raise SmokeDownloadError("TOTAL_BYTE_CAP_EXCEEDED")
    return total, updated


def verify_local_file(
    path: Path, item: SourceObject, remote_metadata: GCSObjectMetadata
) -> tuple[int, str, str]:
    if path.is_symlink() or not path.is_file():
        raise SmokeDownloadError("DOWNLOADED_OBJECT_NOT_REGULAR")
    size = path.stat().st_size
    if (
        item.expected_size_bytes is None
        or size != item.expected_size_bytes
        or size != remote_metadata.size_bytes
    ):
        raise SmokeDownloadError("DOWNLOADED_OBJECT_SIZE_MISMATCH")
    local_md5_base64, local_sha256 = file_digests(path)
    if local_md5_base64 != remote_metadata.md5_base64:
        raise SmokeDownloadError("DOWNLOADED_OBJECT_MD5_MISMATCH")
    if item.expected_sha256 is not None and local_sha256 != item.expected_sha256:
        raise SmokeDownloadError("DOWNLOADED_OBJECT_CHECKSUM_MISMATCH")
    return size, local_md5_base64, local_sha256


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _validate_output_path(path: Path, *, outside_root: Path) -> Path:
    reject_inside_repository(path, "OUTPUT_INSIDE_REPOSITORY")
    if path.exists() or path.is_symlink():
        raise SmokeDownloadError("OUTPUT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise SmokeDownloadError("OUTPUT_PARENT_IS_SYMLINK")
    resolved = path.resolve()
    if _is_relative_to(resolved, outside_root):
        raise SmokeDownloadError("REPORT_INSIDE_DOWNLOAD_SCOPE")
    return resolved


def aggregate_template() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "status": "FAIL",
        "preflight_only": False,
        "n_manifest_rows": 0,
        "n_requested_objects": 0,
        "n_studies": 0,
        "n_subjects": 0,
        "n_expected_objects": 0,
        "n_remote_objects": 0,
        "n_remote_stat_objects": 0,
        "n_remote_metadata_complete": 0,
        "n_remote_md5_present": 0,
        "n_remote_crc32c_present": 0,
        "n_remote_generation_present": 0,
        "n_remote_md5_verified_objects": 0,
        "n_remote_metadata_mismatches": 0,
        "n_downloaded_objects": 0,
        "n_preexisting_verified_objects": 0,
        "n_local_sha256_computed": 0,
        "total_remote_bytes": 0,
        "total_downloaded_bytes": 0,
        "free_bytes_before": 0,
        "max_studies": MAX_STUDIES,
        "max_objects": MAX_OBJECTS,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "min_free_bytes": MIN_FREE_BYTES,
        "exact_remote_set": False,
        "exact_stat_set": False,
        "listing_stat_sizes_match": False,
        "all_remote_md5_present": False,
        "all_local_md5_match": False,
        "all_local_sha256_computed": False,
        "all_sizes_verified": False,
        "remote_metadata_authority": "GCS_EXACT_OBJECT_STAT",
        "object_transport_integrity_status": "NOT_EVALUATED",
        "no_symlinks": False,
        "no_extras": False,
        "error_code": "NOT_RUN",
        "source_manifest_sha256": None,
        "source_manifest_sha256_verified": False,
        "restricted_report_sha256": None,
    }


def verify_source_manifest_identity(path: Path, expected_sha256: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise SmokeDownloadError("SOURCE_MANIFEST_NOT_REGULAR_FILE")
    expected = str(expected_sha256).lower()
    if not SHA256_RE.fullmatch(expected):
        raise SmokeDownloadError("INVALID_EXPECTED_SOURCE_MANIFEST_SHA256")
    observed = sha256_file(path)
    if observed != expected:
        raise SmokeDownloadError("SOURCE_MANIFEST_SHA256_MISMATCH")
    return observed


def execute(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    aggregate = aggregate_template()
    aggregate["preflight_only"] = bool(args.preflight_only)
    if args.bucket != MIMIC_ECHO_BUCKET:
        raise SmokeDownloadError("REMOTE_BUCKET_NOT_ALLOWED")
    if not SAFE_PROJECT_RE.fullmatch(args.billing_project):
        raise SmokeDownloadError("INVALID_BILLING_PROJECT")
    gsutil_bin = shutil.which(args.gsutil_bin)
    if gsutil_bin is None:
        raise SmokeDownloadError("GSUTIL_NOT_FOUND")

    reject_inside_repository(args.source_manifest, "SOURCE_MANIFEST_INSIDE_REPOSITORY")
    source_manifest_sha256 = verify_source_manifest_identity(
        args.source_manifest, args.expected_source_manifest_sha256
    )
    aggregate["source_manifest_sha256"] = source_manifest_sha256
    aggregate["source_manifest_sha256_verified"] = True
    download_root = validate_scoped_root(args.download_root)
    restricted_report = _validate_output_path(args.restricted_report, outside_root=download_root)
    aggregate_output = _validate_output_path(args.aggregate_output, outside_root=download_root)
    if restricted_report == aggregate_output:
        raise SmokeDownloadError("OUTPUT_PATH_COLLISION")

    objects = load_source_manifest(args.source_manifest, bucket=args.bucket)
    aggregate.update(
        {
            "n_manifest_rows": len(objects),
            "n_requested_objects": len(objects),
            "n_studies": len({item.study_id for item in objects}),
            "n_subjects": len({item.subject_id for item in objects}),
            "n_expected_objects": len(objects),
        }
    )

    expected_paths = {item.relative_path for item in objects}
    existing = inventory_download_root(download_root, expected_paths)
    aggregate["no_symlinks"] = True
    aggregate["no_extras"] = True
    free_bytes = shutil.disk_usage(download_root).free
    aggregate["free_bytes_before"] = int(free_bytes)
    if free_bytes < MIN_FREE_BYTES:
        raise SmokeDownloadError("INSUFFICIENT_FREE_SPACE")

    remote_sizes = list_remote_objects(
        objects, gsutil_bin=gsutil_bin, billing_project=args.billing_project
    )
    aggregate["n_remote_objects"] = len(remote_sizes)
    aggregate["exact_remote_set"] = True
    remote_metadata = stat_remote_objects(
        objects, gsutil_bin=gsutil_bin, billing_project=args.billing_project
    )
    aggregate["n_remote_stat_objects"] = len(remote_metadata)
    aggregate["exact_stat_set"] = True
    aggregate["n_remote_metadata_complete"] = sum(
        item.size_bytes >= 0
        and bool(item.md5_base64)
        and bool(item.crc32c_base64)
        and bool(item.generation)
        for item in remote_metadata.values()
    )
    aggregate["n_remote_md5_present"] = sum(
        bool(item.md5_base64) for item in remote_metadata.values()
    )
    aggregate["n_remote_crc32c_present"] = sum(
        bool(item.crc32c_base64) for item in remote_metadata.values()
    )
    aggregate["n_remote_generation_present"] = sum(
        bool(item.generation) for item in remote_metadata.values()
    )
    required_remote_count = len(objects)
    if not (
        aggregate["n_remote_metadata_complete"]
        == aggregate["n_remote_md5_present"]
        == aggregate["n_remote_crc32c_present"]
        == aggregate["n_remote_generation_present"]
        == required_remote_count
    ):
        raise SmokeDownloadError("INCOMPLETE_REMOTE_METADATA_AUTHORITY")
    aggregate["all_remote_md5_present"] = (
        aggregate["n_remote_md5_present"] == len(objects)
    )
    total_bytes, objects = reconcile_remote_metadata(
        objects, remote_sizes, remote_metadata
    )
    aggregate["total_remote_bytes"] = total_bytes
    aggregate["listing_stat_sizes_match"] = True
    aggregate["all_sizes_verified"] = True
    aggregate["object_transport_integrity_status"] = "REMOTE_MD5_PRESENT"

    restricted_rows: list[dict[str, Any]] = []
    if args.preflight_only:
        for item in objects:
            metadata = remote_metadata[item.gcs_uri]
            restricted_rows.append(
                {
                    "subject_id": item.subject_id,
                    "study_id": item.study_id,
                    "split": item.split,
                    "source_relative_path": item.relative_path,
                    "gcs_uri": item.gcs_uri,
                    "expected_size_bytes": item.expected_size_bytes,
                    "expected_sha256": item.expected_sha256,
                    "smoke_role": item.smoke_role,
                    "remote_size_bytes": remote_sizes[item.gcs_uri],
                    "remote_stat_size_bytes": metadata.size_bytes,
                    "remote_md5_base64": metadata.md5_base64,
                    "remote_crc32c_base64": metadata.crc32c_base64,
                    "remote_generation": metadata.generation,
                    "local_status": "NOT_DOWNLOADED_PREFLIGHT_ONLY",
                    "local_size_bytes": None,
                    "local_md5_base64": None,
                    "local_sha256": None,
                    "remote_md5_verified": False,
                }
            )
    else:
        for item in objects:
            metadata = remote_metadata[item.gcs_uri]
            destination = download_root / PurePosixPath(item.relative_path)
            ensure_no_symlink_components(download_root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_symlink_components(download_root, destination.parent)
            status: str
            if item.relative_path in existing:
                local_size, local_md5, local_sha = verify_local_file(
                    destination, item, metadata
                )
                status = "PREEXISTING_VERIFIED"
                aggregate["n_preexisting_verified_objects"] += 1
            else:
                temporary = destination.with_name(
                    f".{destination.name}.partial.{os.getpid()}.{uuid4().hex}"
                )
                if temporary.exists() or temporary.is_symlink():
                    raise SmokeDownloadError("TEMPORARY_DOWNLOAD_COLLISION")
                completed = subprocess.run(
                    [gsutil_bin, "-u", args.billing_project, "cp", item.gcs_uri, str(temporary)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    raise _safe_subprocess_failure("OBJECT_DOWNLOAD_FAILED", completed)
                local_size, local_md5, local_sha = verify_local_file(
                    temporary, item, metadata
                )
                if destination.exists() or destination.is_symlink():
                    raise SmokeDownloadError("DESTINATION_APPEARED_DURING_DOWNLOAD")
                os.replace(temporary, destination)
                status = "DOWNLOADED_VERIFIED"
                aggregate["n_downloaded_objects"] += 1
            aggregate["n_remote_md5_verified_objects"] += 1
            aggregate["n_local_sha256_computed"] += 1
            aggregate["total_downloaded_bytes"] += local_size
            restricted_rows.append(
                {
                    "subject_id": item.subject_id,
                    "study_id": item.study_id,
                    "split": item.split,
                    "source_relative_path": item.relative_path,
                    "gcs_uri": item.gcs_uri,
                    "expected_size_bytes": item.expected_size_bytes,
                    "expected_sha256": item.expected_sha256,
                    "smoke_role": item.smoke_role,
                    "remote_size_bytes": remote_sizes[item.gcs_uri],
                    "remote_stat_size_bytes": metadata.size_bytes,
                    "remote_md5_base64": metadata.md5_base64,
                    "remote_crc32c_base64": metadata.crc32c_base64,
                    "remote_generation": metadata.generation,
                    "local_status": status,
                    "local_size_bytes": local_size,
                    "local_md5_base64": local_md5,
                    "local_sha256": local_sha,
                    "remote_md5_verified": True,
                }
            )

        final_inventory = inventory_download_root(download_root, expected_paths)
        if set(final_inventory) != expected_paths:
            raise SmokeDownloadError("FINAL_LOCAL_OBJECT_SET_MISMATCH")
        if aggregate["n_remote_md5_verified_objects"] != aggregate["n_expected_objects"]:
            raise SmokeDownloadError("INCOMPLETE_GCS_MD5_VERIFICATION")
        if aggregate["n_local_sha256_computed"] != aggregate["n_expected_objects"]:
            raise SmokeDownloadError("INCOMPLETE_LOCAL_SHA256_COMPUTATION")
        aggregate["all_local_md5_match"] = True
        aggregate["all_local_sha256_computed"] = True
        aggregate["object_transport_integrity_status"] = "VERIFIED_ALL_OBJECTS"

    aggregate["status"] = "PASS_PREFLIGHT_ONLY" if args.preflight_only else "PASS"
    aggregate["error_code"] = "NONE"
    restricted = {
        "schema_version": 2,
        "status": aggregate["status"],
        "source_manifest_sha256": sha256_file(args.source_manifest),
        "authority": aggregate["remote_metadata_authority"],
        "object_transport_integrity_status": aggregate[
            "object_transport_integrity_status"
        ],
        "n_requested_objects": aggregate["n_requested_objects"],
        "n_downloaded_objects": aggregate["n_downloaded_objects"],
        "n_remote_metadata_complete": aggregate["n_remote_metadata_complete"],
        "n_remote_md5_verified_objects": aggregate[
            "n_remote_md5_verified_objects"
        ],
        "n_remote_metadata_mismatches": aggregate["n_remote_metadata_mismatches"],
        "n_local_sha256_computed": aggregate["n_local_sha256_computed"],
        "total_downloaded_bytes": aggregate["total_downloaded_bytes"],
        "objects": restricted_rows,
    }
    _write_json_exclusive(restricted_report, restricted)
    aggregate["restricted_report_sha256"] = sha256_file(restricted_report)
    _write_json_exclusive(aggregate_output, aggregate)
    return aggregate, restricted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight and download the exact four-study Phase 1E-A smoke object set."
    )
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--expected-source-manifest-sha256", required=True)
    parser.add_argument("--download-root", type=Path, required=True)
    parser.add_argument("--restricted-report", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--billing-project", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--gsutil-bin", default="gsutil")
    parser.add_argument("--bucket", default=MIMIC_ECHO_BUCKET)
    return parser.parse_args(argv)


def _safe_failure_write(
    path: Path, payload: dict[str, Any], *, forbidden_root: Path | None = None
) -> None:
    try:
        if forbidden_root is not None:
            root = forbidden_root.expanduser().resolve()
            candidate = path.expanduser().resolve()
            if candidate == root or _is_relative_to(candidate, root):
                return
        if not path.exists() and not path.is_symlink():
            _write_json_exclusive(path, payload)
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    aggregate = aggregate_template()
    aggregate["preflight_only"] = bool(args.preflight_only)
    try:
        aggregate, _ = execute(args)
    except SmokeDownloadError as exc:
        aggregate["status"] = "FAIL"
        aggregate["error_code"] = exc.code
        _safe_failure_write(
            args.aggregate_output, aggregate, forbidden_root=args.download_root
        )
        _safe_failure_write(
            args.restricted_report,
            {
                "schema_version": 2,
                "status": "FAIL",
                "error_code": exc.code,
                "sanitized_detail": exc.detail,
            },
            forbidden_root=args.download_root,
        )
        print(json.dumps(aggregate, sort_keys=True))
        return 2
    except Exception:
        aggregate["status"] = "FAIL"
        aggregate["error_code"] = "UNEXPECTED_INTERNAL_ERROR"
        _safe_failure_write(
            args.aggregate_output, aggregate, forbidden_root=args.download_root
        )
        _safe_failure_write(
            args.restricted_report,
            {
                "schema_version": 2,
                "status": "FAIL",
                "error_code": "UNEXPECTED_INTERNAL_ERROR",
            },
            forbidden_root=args.download_root,
        )
        print(json.dumps(aggregate, sort_keys=True))
        return 2
    print(json.dumps(aggregate, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
