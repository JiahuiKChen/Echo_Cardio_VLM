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
CHECKSUM_COLUMNS = ("expected_sha256", "sha256", "release_sha256")
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


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def parse_release_checksums(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise SmokeDownloadError("RELEASE_CHECKSUM_FILE_NOT_REGULAR")
    checksums: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            match = re.fullmatch(r"([0-9A-Fa-f]{64})\s+[*]?(.+)", line)
            if not match:
                raise SmokeDownloadError("MALFORMED_RELEASE_CHECKSUM_LINE")
            relative_raw = match.group(2).strip()
            while relative_raw.startswith("./"):
                relative_raw = relative_raw[2:]
            try:
                relative = safe_relative_object_path(relative_raw)
            except SmokeDownloadError:
                # The official release file can contain non-DICOM metadata;
                # those entries are outside this exact-object smoke scope.
                continue
            checksum = match.group(1).lower()
            if relative in checksums and checksums[relative] != checksum:
                raise SmokeDownloadError("CONFLICTING_RELEASE_CHECKSUM")
            checksums[relative] = checksum
    return checksums


def apply_release_checksums(
    objects: Sequence[SourceObject], release_checksums: dict[str, str] | None
) -> list[SourceObject]:
    if release_checksums is None:
        return list(objects)
    out: list[SourceObject] = []
    for item in objects:
        release_sha = release_checksums.get(item.relative_path)
        if release_sha is None:
            raise SmokeDownloadError("SOURCE_OBJECT_MISSING_RELEASE_CHECKSUM")
        if item.expected_sha256 is not None and item.expected_sha256 != release_sha:
            raise SmokeDownloadError("MANIFEST_RELEASE_CHECKSUM_DISAGREEMENT")
        values = asdict(item)
        values["expected_sha256"] = release_sha
        out.append(SourceObject(**values))
    return out


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


def verify_remote_sizes(
    objects: Sequence[SourceObject], remote_sizes: dict[str, int]
) -> tuple[int, list[SourceObject]]:
    total = 0
    updated: list[SourceObject] = []
    for item in objects:
        remote_size = remote_sizes[item.gcs_uri]
        if item.expected_size_bytes is not None and item.expected_size_bytes != remote_size:
            raise SmokeDownloadError("MANIFEST_REMOTE_SIZE_DISAGREEMENT")
        total += remote_size
        values = asdict(item)
        values["expected_size_bytes"] = remote_size
        updated.append(SourceObject(**values))
    if total > MAX_TOTAL_BYTES:
        raise SmokeDownloadError("TOTAL_BYTE_CAP_EXCEEDED")
    return total, updated


def verify_local_file(path: Path, item: SourceObject) -> tuple[int, str, bool]:
    if path.is_symlink() or not path.is_file():
        raise SmokeDownloadError("DOWNLOADED_OBJECT_NOT_REGULAR")
    size = path.stat().st_size
    if item.expected_size_bytes is None or size != item.expected_size_bytes:
        raise SmokeDownloadError("DOWNLOADED_OBJECT_SIZE_MISMATCH")
    digest = sha256_file(path)
    checksum_verified = item.expected_sha256 is not None
    if checksum_verified and digest != item.expected_sha256:
        raise SmokeDownloadError("DOWNLOADED_OBJECT_CHECKSUM_MISMATCH")
    return size, digest, checksum_verified


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
        "schema_version": 1,
        "status": "FAIL",
        "preflight_only": False,
        "n_manifest_rows": 0,
        "n_studies": 0,
        "n_subjects": 0,
        "n_expected_objects": 0,
        "n_remote_objects": 0,
        "n_downloaded_objects": 0,
        "n_preexisting_verified_objects": 0,
        "n_checksum_verified_objects": 0,
        "total_remote_bytes": 0,
        "free_bytes_before": 0,
        "max_studies": MAX_STUDIES,
        "max_objects": MAX_OBJECTS,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "min_free_bytes": MIN_FREE_BYTES,
        "exact_remote_set": False,
        "all_sizes_verified": False,
        "checksum_authority_status": "NOT_EVALUATED",
        "no_symlinks": False,
        "no_extras": False,
        "error_code": "NOT_RUN",
        "source_manifest_sha256": None,
        "source_manifest_sha256_verified": False,
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
    release_checksums = (
        parse_release_checksums(args.release_checksums) if args.release_checksums else None
    )
    if not args.preflight_only and release_checksums is None:
        raise SmokeDownloadError("RELEASE_CHECKSUMS_REQUIRED_FOR_DOWNLOAD")
    objects = apply_release_checksums(objects, release_checksums)
    aggregate["checksum_authority_status"] = (
        "SUPPLIED_PENDING_LOCAL_VERIFICATION"
        if release_checksums is not None
        else "PENDING_PREFLIGHT_ONLY"
    )
    aggregate.update(
        {
            "n_manifest_rows": len(objects),
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
    total_bytes, objects = verify_remote_sizes(objects, remote_sizes)
    aggregate["total_remote_bytes"] = total_bytes
    aggregate["all_sizes_verified"] = True

    restricted_rows: list[dict[str, Any]] = []
    if args.preflight_only:
        for item in objects:
            restricted_rows.append(
                {
                    **asdict(item),
                    "remote_size_bytes": remote_sizes[item.gcs_uri],
                    "local_status": "NOT_DOWNLOADED_PREFLIGHT_ONLY",
                    "local_size_bytes": None,
                    "local_sha256": None,
                    "release_checksum_verified": False,
                }
            )
    else:
        for item in objects:
            destination = download_root / PurePosixPath(item.relative_path)
            ensure_no_symlink_components(download_root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_symlink_components(download_root, destination.parent)
            status: str
            if item.relative_path in existing:
                local_size, local_sha, checksum_verified = verify_local_file(destination, item)
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
                local_size, local_sha, checksum_verified = verify_local_file(temporary, item)
                if destination.exists() or destination.is_symlink():
                    raise SmokeDownloadError("DESTINATION_APPEARED_DURING_DOWNLOAD")
                os.replace(temporary, destination)
                status = "DOWNLOADED_VERIFIED"
                aggregate["n_downloaded_objects"] += 1
            if checksum_verified:
                aggregate["n_checksum_verified_objects"] += 1
            restricted_rows.append(
                {
                    **asdict(item),
                    "remote_size_bytes": remote_sizes[item.gcs_uri],
                    "local_status": status,
                    "local_size_bytes": local_size,
                    "local_sha256": local_sha,
                    "release_checksum_verified": checksum_verified,
                }
            )

        final_inventory = inventory_download_root(download_root, expected_paths)
        if set(final_inventory) != expected_paths:
            raise SmokeDownloadError("FINAL_LOCAL_OBJECT_SET_MISMATCH")
        if aggregate["n_checksum_verified_objects"] != aggregate["n_expected_objects"]:
            raise SmokeDownloadError("INCOMPLETE_RELEASE_CHECKSUM_VERIFICATION")
        aggregate["checksum_authority_status"] = "VERIFIED_ALL_OBJECTS"

    aggregate["status"] = "PASS_PREFLIGHT_ONLY" if args.preflight_only else "PASS"
    aggregate["error_code"] = "NONE"
    restricted = {
        "schema_version": 1,
        "status": aggregate["status"],
        "source_manifest_sha256": sha256_file(args.source_manifest),
        "release_checksums_supplied": release_checksums is not None,
        "objects": restricted_rows,
    }
    _write_json_exclusive(restricted_report, restricted)
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
    parser.add_argument("--release-checksums", type=Path, default=None)
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
                "schema_version": 1,
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
                "schema_version": 1,
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
