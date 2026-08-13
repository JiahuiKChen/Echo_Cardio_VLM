#!/usr/bin/env python3
"""Closed, owner-private manifest contract for the exact-five C3 canary.

This module contains control-plane validation and deterministic selection only.
It has no cloud, scheduler, DICOM, GPU, modeling, prediction, or performance
access path.  A future authorized live selection may use the frozen selector on
restricted source-manifest summaries, then bind the complete declared object
membership with :func:`build_sealed_manifest`.

The embedded ``manifest_sha256`` is the SHA-256 of the canonical JSON envelope
without that field.  This avoids a self-referential digest while binding every
other manifest byte semantically.  A detached SHA-256 may additionally bind the
exact private file serialization at load time.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Final


SCHEMA_NAME: Final = "lvef_c3_exact_five_canary_manifest"
SCHEMA_VERSION: Final = 1
SELECTION_METHOD_VERSION: Final = (
    "source_object_count_quintile_representatives_numeric_id_tiebreak_v1"
)
SOURCE_RELEASE: Final = "mimic-iv-echo/1.0"
EXACT_STUDIES: Final = 5
EXACT_SUBJECTS: Final = 5
MAXIMUM_OBJECTS: Final = 750
MAXIMUM_EXPECTED_BYTES: Final = 5_000_000_000
MANIFEST_SEAL_PLACEHOLDER: Final = "0" * 64

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
ID_RE: Final = re.compile(r"^[1-9][0-9]*$")
CONFIG_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
SOURCE_PATH_RE: Final = re.compile(
    r"^files/p(?P<prefix>[0-9]{2})/p(?P<subject>[0-9]+)/"
    r"s(?P<study>[0-9]+)/(?P<filename>[A-Za-z0-9._-]+[.]dcm)$"
)

SELECTION_STRATA: Final = (
    "relatively_low_object_count",
    "lower_middle_object_count",
    "near_median_object_count",
    "upper_middle_object_count",
    "relatively_high_object_count",
)
CANDIDATE_COLUMNS: Final = (
    "study_id",
    "subject_id",
    "split",
    "expected_object_count",
    "expected_byte_total",
    "known_no_cine",
    "prior_reconstruction_smoke",
)
SOURCE_OBJECT_COLUMNS: Final = (
    "subject_id",
    "study_id",
    "split",
    "source_object_key",
    "source_relative_path",
    "size_bytes",
    "generation",
    "md5_base64",
    "crc32c_base64",
)

ENVELOPE_KEYS: Final = frozenset(
    {"schema_name", "schema_version", "manifest", "manifest_sha256"}
)
MANIFEST_KEYS: Final = frozenset(
    {
        "selection_method_version",
        "source_release",
        "source_authority_commit",
        "source_manifest_sha256",
        "source_configuration_hashes",
        "study_count",
        "subject_count",
        "split",
        "complete_object_membership",
        "expected_object_count",
        "expected_byte_total",
        "studies",
    }
)
CONFIG_HASH_KEYS: Final = frozenset({"logical_name", "sha256"})
STUDY_KEYS: Final = frozenset(
    {
        "study_id",
        "subject_id",
        "split",
        "selection_stratum",
        "known_no_cine",
        "prior_reconstruction_smoke",
        "complete_object_membership",
        "expected_object_count",
        "expected_byte_total",
        "objects",
    }
)
OBJECT_KEYS: Final = frozenset(
    {
        "source_object_key",
        "source_relative_path",
        "size_bytes",
        "generation",
        "md5_base64",
        "crc32c_base64",
    }
)

# Closed schemas already exclude unknown fields.  This explicit denylist makes
# outcome-bearing additions fail with a stable, unmistakable error before a
# generic schema error.  Matching is on normalized field-name tokens only;
# identifier or locator values are never searched or interpreted clinically.
PROHIBITED_FIELD_TOKENS: Final = frozenset(
    {
        "target",
        "label",
        "endpoint",
        "prediction",
        "predicted",
        "performance",
        "metric",
        "diagnosis",
        "outcome",
        "lvef",
        "auc",
        "auroc",
        "accuracy",
        "sensitivity",
        "specificity",
    }
)
PROHIBITED_FIELD_PHRASES: Final = frozenset(
    {"ejection_fraction", "ground_truth", "f1_score"}
)


class CanaryManifestError(ValueError):
    """A fail-closed validation error exposing only a fixed safe code."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_MANIFEST_INVALID"
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise CanaryManifestError(code)


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _plain_positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(code)
    return value


def _canonical_id(value: Any, code: str) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_exact_keys(value: Any, expected: frozenset[str], code: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(code)


def _field_tokens(name: str) -> tuple[set[str], str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return set(part for part in normalized.split("_") if part), normalized


def reject_prohibited_fields(value: Any) -> None:
    """Reject outcome-, prediction-, or performance-bearing field names."""

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("MANIFEST_FIELD_NAME_INVALID")
            tokens, normalized = _field_tokens(key)
            if tokens & PROHIBITED_FIELD_TOKENS or any(
                phrase in normalized for phrase in PROHIBITED_FIELD_PHRASES
            ):
                _fail("PROHIBITED_FIELD_PRESENT")
            reject_prohibited_fields(item)
    elif isinstance(value, list):
        for item in value:
            reject_prohibited_fields(item)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def calculate_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    unsigned = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "manifest": manifest,
    }
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _base64_digest(value: Any, decoded_bytes: int, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _fail(code)
    if len(decoded) != decoded_bytes or base64.b64encode(decoded).decode("ascii") != value:
        _fail(code)
    return value


def _validate_source_object(
    value: Any, *, subject_id: str, study_id: str, source_release: str
) -> dict[str, Any]:
    _require_exact_keys(value, OBJECT_KEYS, "CANARY_OBJECT_SCHEMA_NOT_CLOSED")
    source_path = value.get("source_relative_path")
    if not isinstance(source_path, str):
        _fail("CANARY_OBJECT_LOCATOR_INVALID")
    match = SOURCE_PATH_RE.fullmatch(source_path)
    if (
        match is None
        or match.group("subject") != subject_id
        or match.group("study") != study_id
        or match.group("prefix") != f"{int(subject_id) // 1_000_000:02d}"
    ):
        _fail("CANARY_OBJECT_LOCATOR_INVALID")
    source_key = _sha256(value.get("source_object_key"), "CANARY_OBJECT_KEY_INVALID")
    derived = hashlib.sha256(
        f"{source_release}\0{source_path}".encode("utf-8")
    ).hexdigest()
    if source_key != derived:
        _fail("CANARY_OBJECT_KEY_DERIVATION_MISMATCH")
    size_bytes = _plain_positive_int(
        value.get("size_bytes"), "CANARY_OBJECT_SIZE_INVALID"
    )
    generation = value.get("generation")
    if (
        not isinstance(generation, str)
        or not generation.isdigit()
        or str(int(generation)) != generation
        or int(generation) < 1
    ):
        _fail("CANARY_OBJECT_GENERATION_INVALID")
    md5 = _base64_digest(value.get("md5_base64"), 16, "CANARY_OBJECT_MD5_INVALID")
    crc32c = _base64_digest(
        value.get("crc32c_base64"), 4, "CANARY_OBJECT_CRC32C_INVALID"
    )
    return {
        "source_object_key": source_key,
        "source_relative_path": source_path,
        "size_bytes": size_bytes,
        "generation": generation,
        "md5_base64": md5,
        "crc32c_base64": crc32c,
    }


def _validate_configuration_hashes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        _fail("CANARY_CONFIGURATION_HASHES_INVALID")
    normalized: list[dict[str, str]] = []
    names: set[str] = set()
    for item in value:
        _require_exact_keys(
            item, CONFIG_HASH_KEYS, "CANARY_CONFIGURATION_HASH_SCHEMA_NOT_CLOSED"
        )
        name = item.get("logical_name")
        if not isinstance(name, str) or CONFIG_NAME_RE.fullmatch(name) is None:
            _fail("CANARY_CONFIGURATION_HASH_NAME_INVALID")
        if name in names:
            _fail("CANARY_CONFIGURATION_HASH_NAME_DUPLICATE")
        names.add(name)
        normalized.append(
            {
                "logical_name": name,
                "sha256": _sha256(
                    item.get("sha256"), "CANARY_CONFIGURATION_SHA256_INVALID"
                ),
            }
        )
    expected = sorted(normalized, key=lambda item: item["logical_name"])
    if normalized != expected:
        _fail("CANARY_CONFIGURATION_HASH_ORDER_INVALID")
    return normalized


def validate_manifest_body(value: Any) -> dict[str, Any]:
    reject_prohibited_fields(value)
    _require_exact_keys(value, MANIFEST_KEYS, "CANARY_MANIFEST_BODY_SCHEMA_NOT_CLOSED")
    if value.get("selection_method_version") != SELECTION_METHOD_VERSION:
        _fail("CANARY_SELECTION_METHOD_INVALID")
    if value.get("source_release") != SOURCE_RELEASE:
        _fail("CANARY_SOURCE_RELEASE_INVALID")
    commit = value.get("source_authority_commit")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        _fail("CANARY_SOURCE_AUTHORITY_COMMIT_INVALID")
    source_manifest_sha = _sha256(
        value.get("source_manifest_sha256"), "CANARY_SOURCE_MANIFEST_SHA256_INVALID"
    )
    configuration_hashes = _validate_configuration_hashes(
        value.get("source_configuration_hashes")
    )
    if (
        value.get("study_count") != EXACT_STUDIES
        or isinstance(value.get("study_count"), bool)
        or value.get("subject_count") != EXACT_SUBJECTS
        or isinstance(value.get("subject_count"), bool)
    ):
        _fail("CANARY_EXACT_FIVE_COUNT_INVALID")
    if value.get("split") != "train":
        _fail("CANARY_SPLIT_NOT_TRAIN")
    if value.get("complete_object_membership") is not True:
        _fail("CANARY_OBJECT_MEMBERSHIP_NOT_COMPLETE")

    studies = value.get("studies")
    if not isinstance(studies, list) or len(studies) != EXACT_STUDIES:
        _fail("CANARY_EXACT_FIVE_COUNT_INVALID")
    normalized_studies: list[dict[str, Any]] = []
    seen_studies: set[str] = set()
    seen_subjects: set[str] = set()
    seen_object_keys: set[str] = set()
    seen_object_paths: set[str] = set()
    total_objects = 0
    total_bytes = 0
    for index, study in enumerate(studies):
        _require_exact_keys(study, STUDY_KEYS, "CANARY_STUDY_SCHEMA_NOT_CLOSED")
        study_id = _canonical_id(study.get("study_id"), "CANARY_STUDY_ID_INVALID")
        subject_id = _canonical_id(
            study.get("subject_id"), "CANARY_SUBJECT_ID_INVALID"
        )
        if study_id in seen_studies:
            _fail("CANARY_STUDY_DUPLICATE")
        if subject_id in seen_subjects:
            _fail("CANARY_SUBJECT_DUPLICATE")
        seen_studies.add(study_id)
        seen_subjects.add(subject_id)
        if study.get("split") != "train":
            _fail("CANARY_SPLIT_NOT_TRAIN")
        if study.get("selection_stratum") != SELECTION_STRATA[index]:
            _fail("CANARY_SELECTION_STRATA_INVALID")
        if study.get("known_no_cine") is not False:
            _fail("CANARY_KNOWN_NO_CINE_FORBIDDEN")
        if study.get("prior_reconstruction_smoke") is not False:
            _fail("CANARY_PRIOR_SMOKE_STUDY_FORBIDDEN")
        if study.get("complete_object_membership") is not True:
            _fail("CANARY_OBJECT_MEMBERSHIP_NOT_COMPLETE")
        expected_objects = _plain_positive_int(
            study.get("expected_object_count"), "CANARY_STUDY_OBJECT_COUNT_INVALID"
        )
        expected_bytes = _plain_positive_int(
            study.get("expected_byte_total"), "CANARY_STUDY_BYTE_TOTAL_INVALID"
        )
        objects = study.get("objects")
        if not isinstance(objects, list) or not objects:
            _fail("CANARY_STUDY_OBJECTS_INVALID")
        normalized_objects: list[dict[str, Any]] = []
        for object_value in objects:
            normalized_object = _validate_source_object(
                object_value,
                subject_id=subject_id,
                study_id=study_id,
                source_release=SOURCE_RELEASE,
            )
            object_key = normalized_object["source_object_key"]
            object_path = normalized_object["source_relative_path"]
            if object_key in seen_object_keys:
                _fail("CANARY_OBJECT_KEY_DUPLICATE")
            if object_path in seen_object_paths:
                _fail("CANARY_OBJECT_LOCATOR_DUPLICATE")
            seen_object_keys.add(object_key)
            seen_object_paths.add(object_path)
            normalized_objects.append(normalized_object)
        if normalized_objects != sorted(
            normalized_objects, key=lambda item: item["source_relative_path"]
        ):
            _fail("CANARY_OBJECT_ORDER_INVALID")
        observed_bytes = sum(item["size_bytes"] for item in normalized_objects)
        if len(normalized_objects) != expected_objects:
            _fail("CANARY_STUDY_OBJECT_COUNT_MISMATCH")
        if observed_bytes != expected_bytes:
            _fail("CANARY_STUDY_BYTE_TOTAL_MISMATCH")
        total_objects += expected_objects
        total_bytes += expected_bytes
        normalized_studies.append(
            {
                "study_id": study_id,
                "subject_id": subject_id,
                "split": "train",
                "selection_stratum": SELECTION_STRATA[index],
                "known_no_cine": False,
                "prior_reconstruction_smoke": False,
                "complete_object_membership": True,
                "expected_object_count": expected_objects,
                "expected_byte_total": expected_bytes,
                "objects": normalized_objects,
            }
        )
    if len(seen_studies) != EXACT_STUDIES or len(seen_subjects) != EXACT_SUBJECTS:
        _fail("CANARY_EXACT_FIVE_COUNT_INVALID")
    if total_objects > MAXIMUM_OBJECTS:
        _fail("CANARY_OBJECT_CEILING_EXCEEDED")
    if total_bytes > MAXIMUM_EXPECTED_BYTES:
        _fail("CANARY_BYTE_CEILING_EXCEEDED")
    if (
        value.get("expected_object_count") != total_objects
        or isinstance(value.get("expected_object_count"), bool)
    ):
        _fail("CANARY_OBJECT_TOTAL_MISMATCH")
    if (
        value.get("expected_byte_total") != total_bytes
        or isinstance(value.get("expected_byte_total"), bool)
    ):
        _fail("CANARY_BYTE_TOTAL_MISMATCH")
    return {
        "selection_method_version": SELECTION_METHOD_VERSION,
        "source_release": SOURCE_RELEASE,
        "source_authority_commit": commit,
        "source_manifest_sha256": source_manifest_sha,
        "source_configuration_hashes": configuration_hashes,
        "study_count": EXACT_STUDIES,
        "subject_count": EXACT_SUBJECTS,
        "split": "train",
        "complete_object_membership": True,
        "expected_object_count": total_objects,
        "expected_byte_total": total_bytes,
        "studies": normalized_studies,
    }


def seal_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_manifest_body(dict(manifest))
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "manifest": normalized,
        "manifest_sha256": calculate_manifest_sha256(normalized),
    }


def validate_manifest(
    value: Any,
    *,
    expected_manifest_sha256: str | None = None,
    expected_source_authority_commit: str | None = None,
) -> dict[str, Any]:
    reject_prohibited_fields(value)
    _require_exact_keys(value, ENVELOPE_KEYS, "CANARY_MANIFEST_SCHEMA_NOT_CLOSED")
    if value.get("schema_name") != SCHEMA_NAME:
        _fail("CANARY_MANIFEST_SCHEMA_NAME_INVALID")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or isinstance(value.get("schema_version"), bool)
    ):
        _fail("CANARY_MANIFEST_SCHEMA_VERSION_INVALID")
    normalized_manifest = validate_manifest_body(value.get("manifest"))
    observed_seal = _sha256(
        value.get("manifest_sha256"), "CANARY_MANIFEST_SHA256_INVALID"
    )
    calculated = calculate_manifest_sha256(normalized_manifest)
    if observed_seal != calculated:
        _fail("CANARY_MANIFEST_SEAL_MISMATCH")
    if expected_manifest_sha256 is not None:
        expected = _sha256(
            expected_manifest_sha256, "CANARY_EXPECTED_MANIFEST_SHA256_INVALID"
        )
        if observed_seal != expected:
            _fail("CANARY_MANIFEST_EXPECTED_SHA256_MISMATCH")
    if expected_source_authority_commit is not None:
        if (
            not isinstance(expected_source_authority_commit, str)
            or COMMIT_RE.fullmatch(expected_source_authority_commit) is None
        ):
            _fail("CANARY_EXPECTED_SOURCE_COMMIT_INVALID")
        if (
            normalized_manifest["source_authority_commit"]
            != expected_source_authority_commit
        ):
            _fail("CANARY_SOURCE_AUTHORITY_COMMIT_MISMATCH")
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "manifest": normalized_manifest,
        "manifest_sha256": observed_seal,
    }


def parse_manifest_bytes(
    payload: bytes,
    *,
    expected_manifest_sha256: str | None = None,
    expected_source_authority_commit: str | None = None,
) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_strict_pairs)
    except UnicodeDecodeError:
        _fail("CANARY_MANIFEST_UTF8_INVALID")
    except json.JSONDecodeError:
        _fail("CANARY_MANIFEST_JSON_INVALID")
    return validate_manifest(
        value,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_source_authority_commit=expected_source_authority_commit,
    )


def _read_private_regular_file(path: Path) -> bytes:
    if not path.is_absolute():
        _fail("CANARY_MANIFEST_PATH_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    for component in path.parts[1:-1]:
        cursor /= component
        try:
            ancestor = os.lstat(cursor)
        except OSError:
            _fail("CANARY_MANIFEST_ANCESTOR_INVALID")
        if stat.S_ISLNK(ancestor.st_mode) or not stat.S_ISDIR(ancestor.st_mode):
            _fail("CANARY_MANIFEST_ANCESTOR_INVALID")
    try:
        path_metadata = os.lstat(path)
    except OSError:
        _fail("CANARY_MANIFEST_FILE_MISSING")
    if stat.S_ISLNK(path_metadata.st_mode):
        _fail("CANARY_MANIFEST_SYMLINK_FORBIDDEN")
    if not stat.S_ISREG(path_metadata.st_mode):
        _fail("CANARY_MANIFEST_NOT_REGULAR_FILE")
    if path_metadata.st_uid != os.geteuid():
        _fail("CANARY_MANIFEST_OWNER_INVALID")
    if stat.S_IMODE(path_metadata.st_mode) != 0o600:
        _fail("CANARY_MANIFEST_MODE_INVALID")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("CANARY_MANIFEST_OPEN_FAILED")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != path_metadata.st_dev
            or opened.st_ino != path_metadata.st_ino
            or opened.st_uid != path_metadata.st_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            _fail("CANARY_MANIFEST_CHANGED_DURING_OPEN")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            final.st_size != len(payload)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
        ):
            _fail("CANARY_MANIFEST_CHANGED_DURING_READ")
        return payload
    finally:
        os.close(descriptor)


def load_and_validate_manifest(
    manifest_path: Path,
    *,
    expected_file_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    expected_source_authority_commit: str | None = None,
) -> dict[str, Any]:
    payload = _read_private_regular_file(manifest_path)
    if expected_file_sha256 is not None:
        expected_file = _sha256(
            expected_file_sha256, "CANARY_EXPECTED_FILE_SHA256_INVALID"
        )
        if hashlib.sha256(payload).hexdigest() != expected_file:
            _fail("CANARY_MANIFEST_FILE_SHA256_MISMATCH")
    return parse_manifest_bytes(
        payload,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_source_authority_commit=expected_source_authority_commit,
    )


def serialize_manifest(value: Mapping[str, Any]) -> bytes:
    normalized = validate_manifest(dict(value))
    return (
        json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _validate_private_output_parent(parent: Path, *, code: str) -> os.stat_result:
    try:
        metadata = os.lstat(parent)
        resolved = parent.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != parent
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) not in {0o700, 0o2700}
    ):
        _fail(code)
    return metadata


def write_private_manifest_no_clobber(
    output_path: Path, value: Mapping[str, Any]
) -> tuple[str, str]:
    """Atomically create one mode-0600 private manifest without replacement.

    Returns ``(exact_file_sha256, embedded_manifest_sha256)``.  The destination
    parent must be a canonical, current-owner mode-0700 or setgid-only mode-2700
    directory.  A temporary inode is fully written and synced before a
    no-replace hard link publishes it; no existing destination of any file type
    is followed or overwritten.
    """

    if not output_path.is_absolute() or output_path.name in {"", ".", ".."}:
        _fail("CANARY_OUTPUT_PATH_INVALID")
    parent = output_path.parent
    parent_metadata = _validate_private_output_parent(
        parent, code="CANARY_OUTPUT_PARENT_INVALID"
    )
    parent_identity = (
        parent_metadata.st_dev,
        parent_metadata.st_ino,
        parent_metadata.st_uid,
        parent_metadata.st_gid,
        stat.S_IMODE(parent_metadata.st_mode),
    )
    if os.path.lexists(output_path):
        _fail("CANARY_OUTPUT_ALREADY_EXISTS")
    payload = serialize_manifest(value)
    temporary = parent / f".{output_path.name}.tmp.{secrets.token_hex(12)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                _fail("CANARY_OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
        ):
            _fail("CANARY_OUTPUT_POSTWRITE_INVALID")
        publication_parent = _validate_private_output_parent(
            parent, code="CANARY_OUTPUT_PARENT_CHANGED"
        )
        if (
            publication_parent.st_dev,
            publication_parent.st_ino,
            publication_parent.st_uid,
            publication_parent.st_gid,
            stat.S_IMODE(publication_parent.st_mode),
        ) != parent_identity:
            _fail("CANARY_OUTPUT_PARENT_CHANGED")
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, output_path, follow_symlinks=False)
        except FileExistsError:
            _fail("CANARY_OUTPUT_ALREADY_EXISTS")
        except OSError:
            _fail("CANARY_OUTPUT_PUBLISH_FAILED")
        published = True
        return (
            hashlib.sha256(payload).hexdigest(),
            str(value["manifest_sha256"]),
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        if not published and os.path.lexists(output_path):
            # Never remove an output we did not publish.  ``published`` becomes
            # true only after the no-replace link succeeds.
            pass


@dataclass(frozen=True)
class CandidateStudy:
    study_id: str
    subject_id: str
    split: str
    expected_object_count: int
    expected_byte_total: int
    known_no_cine: bool
    prior_reconstruction_smoke: bool


@dataclass(frozen=True)
class SelectedStudy:
    study_id: str
    subject_id: str
    split: str
    expected_object_count: int
    expected_byte_total: int
    known_no_cine: bool
    prior_reconstruction_smoke: bool
    selection_stratum: str


def _candidate_from_mapping(value: Mapping[str, Any]) -> CandidateStudy:
    reject_prohibited_fields(dict(value))
    _require_exact_keys(dict(value), frozenset(CANDIDATE_COLUMNS), "CANDIDATE_SCHEMA_NOT_CLOSED")
    split = value.get("split")
    if split not in {"train", "val", "test"}:
        _fail("CANDIDATE_SPLIT_INVALID")
    if type(value.get("known_no_cine")) is not bool:
        _fail("CANDIDATE_NO_CINE_FLAG_INVALID")
    if type(value.get("prior_reconstruction_smoke")) is not bool:
        _fail("CANDIDATE_PRIOR_SMOKE_FLAG_INVALID")
    return CandidateStudy(
        study_id=_canonical_id(value.get("study_id"), "CANDIDATE_STUDY_ID_INVALID"),
        subject_id=_canonical_id(
            value.get("subject_id"), "CANDIDATE_SUBJECT_ID_INVALID"
        ),
        split=str(split),
        expected_object_count=_plain_positive_int(
            value.get("expected_object_count"), "CANDIDATE_OBJECT_COUNT_INVALID"
        ),
        expected_byte_total=_plain_positive_int(
            value.get("expected_byte_total"), "CANDIDATE_BYTE_TOTAL_INVALID"
        ),
        known_no_cine=bool(value["known_no_cine"]),
        prior_reconstruction_smoke=bool(value["prior_reconstruction_smoke"]),
    )


def select_exact_five(candidates: Sequence[Mapping[str, Any]]) -> list[SelectedStudy]:
    """Select frozen source-only object-count strata without substitutions.

    Eligible rows are train-only, cine-positive, and absent from the known
    reconstruction smoke.  If a subject has multiple otherwise eligible
    studies, the numerically lowest study is its frozen representative.  The
    representatives are ordered by object count, expected bytes, numeric
    subject, then numeric study.  Ranks nearest 0, 25, 50, 75, and 100 percent
    use the explicitly frozen half-up formula ``(q * (n - 1) + 2) // 4``.
    The chosen five are never substituted when a hard ceiling is exceeded.
    """

    normalized = [_candidate_from_mapping(item) for item in candidates]
    seen_studies: set[str] = set()
    for candidate in normalized:
        if candidate.study_id in seen_studies:
            _fail("CANDIDATE_STUDY_DUPLICATE")
        seen_studies.add(candidate.study_id)
    eligible = [
        candidate
        for candidate in normalized
        if candidate.split == "train"
        and not candidate.known_no_cine
        and not candidate.prior_reconstruction_smoke
    ]
    representatives_by_subject: dict[str, CandidateStudy] = {}
    for candidate in sorted(
        eligible,
        key=lambda item: (
            int(item.subject_id),
            int(item.study_id),
            item.expected_object_count,
            item.expected_byte_total,
        ),
    ):
        representatives_by_subject.setdefault(candidate.subject_id, candidate)
    representatives = sorted(
        representatives_by_subject.values(),
        key=lambda item: (
            item.expected_object_count,
            item.expected_byte_total,
            int(item.subject_id),
            int(item.study_id),
        ),
    )
    if len(representatives) < EXACT_STUDIES:
        _fail("CANDIDATE_ELIGIBLE_UNIQUE_SUBJECTS_INSUFFICIENT")
    maximum_index = len(representatives) - 1
    indices = [(quartile * maximum_index + 2) // 4 for quartile in range(5)]
    if len(set(indices)) != EXACT_STUDIES:
        _fail("CANDIDATE_STRATUM_RANK_COLLISION")
    selected = [
        SelectedStudy(
            study_id=representatives[index].study_id,
            subject_id=representatives[index].subject_id,
            split="train",
            expected_object_count=representatives[index].expected_object_count,
            expected_byte_total=representatives[index].expected_byte_total,
            known_no_cine=False,
            prior_reconstruction_smoke=False,
            selection_stratum=SELECTION_STRATA[stratum_index],
        )
        for stratum_index, index in enumerate(indices)
    ]
    if sum(item.expected_object_count for item in selected) > MAXIMUM_OBJECTS:
        _fail("CANARY_OBJECT_CEILING_EXCEEDED")
    if sum(item.expected_byte_total for item in selected) > MAXIMUM_EXPECTED_BYTES:
        _fail("CANARY_BYTE_CEILING_EXCEEDED")
    return selected


def _parse_bool(text: str, code: str) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    _fail(code)


def _parse_decimal(text: str, code: str) -> int:
    if not text.isdigit() or str(int(text)) != text or int(text) < 1:
        _fail(code)
    return int(text)


def _closed_csv_rows(payload: bytes, expected_columns: tuple[str, ...]) -> list[list[str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        _fail("CSV_UTF8_INVALID")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
        if len(header) != len(set(header)):
            _fail("CSV_DUPLICATE_COLUMN")
        reject_prohibited_fields({column: None for column in header})
        if tuple(header) != expected_columns:
            _fail("CSV_SCHEMA_NOT_CLOSED")
        rows: list[list[str]] = []
        for row in reader:
            if len(row) != len(expected_columns):
                _fail("CSV_ROW_WIDTH_INVALID")
            rows.append(row)
        return rows
    except (csv.Error, StopIteration):
        _fail("CSV_PARSE_INVALID")


def parse_candidate_csv_bytes(payload: bytes) -> list[dict[str, Any]]:
    rows = _closed_csv_rows(payload, CANDIDATE_COLUMNS)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw = dict(zip(CANDIDATE_COLUMNS, row, strict=True))
        item = {
            "study_id": raw["study_id"],
            "subject_id": raw["subject_id"],
            "split": raw["split"],
            "expected_object_count": _parse_decimal(
                raw["expected_object_count"], "CANDIDATE_OBJECT_COUNT_INVALID"
            ),
            "expected_byte_total": _parse_decimal(
                raw["expected_byte_total"], "CANDIDATE_BYTE_TOTAL_INVALID"
            ),
            "known_no_cine": _parse_bool(
                raw["known_no_cine"], "CANDIDATE_NO_CINE_FLAG_INVALID"
            ),
            "prior_reconstruction_smoke": _parse_bool(
                raw["prior_reconstruction_smoke"],
                "CANDIDATE_PRIOR_SMOKE_FLAG_INVALID",
            ),
        }
        _candidate_from_mapping(item)
        normalized.append(item)
    return normalized


def parse_source_object_csv_bytes(payload: bytes) -> list[dict[str, Any]]:
    rows = _closed_csv_rows(payload, SOURCE_OBJECT_COLUMNS)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw = dict(zip(SOURCE_OBJECT_COLUMNS, row, strict=True))
        normalized.append(
            {
                "subject_id": raw["subject_id"],
                "study_id": raw["study_id"],
                "split": raw["split"],
                "source_object_key": raw["source_object_key"],
                "source_relative_path": raw["source_relative_path"],
                "size_bytes": _parse_decimal(
                    raw["size_bytes"], "CANARY_OBJECT_SIZE_INVALID"
                ),
                "generation": raw["generation"],
                "md5_base64": raw["md5_base64"],
                "crc32c_base64": raw["crc32c_base64"],
            }
        )
    return normalized


def build_sealed_manifest(
    *,
    selected_studies: Sequence[SelectedStudy],
    source_objects: Sequence[Mapping[str, Any]],
    source_authority_commit: str,
    source_manifest_sha256: str,
    source_configuration_hashes: Mapping[str, str],
) -> dict[str, Any]:
    if len(selected_studies) != EXACT_STUDIES:
        _fail("CANARY_EXACT_FIVE_COUNT_INVALID")
    expected_pairs = {
        (item.subject_id, item.study_id): item for item in selected_studies
    }
    if len(expected_pairs) != EXACT_STUDIES:
        _fail("CANARY_SELECTED_IDENTITY_DUPLICATE")
    if tuple(item.selection_stratum for item in selected_studies) != SELECTION_STRATA:
        _fail("CANARY_SELECTION_STRATA_INVALID")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {
        pair: [] for pair in expected_pairs
    }
    seen_keys: set[str] = set()
    seen_paths: set[str] = set()
    for row in source_objects:
        reject_prohibited_fields(dict(row))
        _require_exact_keys(
            dict(row), frozenset(SOURCE_OBJECT_COLUMNS), "SOURCE_OBJECT_ROW_SCHEMA_NOT_CLOSED"
        )
        subject_id = _canonical_id(row.get("subject_id"), "CANARY_SUBJECT_ID_INVALID")
        study_id = _canonical_id(row.get("study_id"), "CANARY_STUDY_ID_INVALID")
        pair = (subject_id, study_id)
        if pair not in expected_pairs:
            _fail("CANARY_UNDECLARED_STUDY_OBJECT")
        if row.get("split") != "train":
            _fail("CANARY_SPLIT_NOT_TRAIN")
        object_value = {key: row[key] for key in OBJECT_KEYS}
        normalized_object = _validate_source_object(
            object_value,
            subject_id=subject_id,
            study_id=study_id,
            source_release=SOURCE_RELEASE,
        )
        if normalized_object["source_object_key"] in seen_keys:
            _fail("CANARY_OBJECT_KEY_DUPLICATE")
        if normalized_object["source_relative_path"] in seen_paths:
            _fail("CANARY_OBJECT_LOCATOR_DUPLICATE")
        seen_keys.add(normalized_object["source_object_key"])
        seen_paths.add(normalized_object["source_relative_path"])
        grouped[pair].append(normalized_object)

    studies: list[dict[str, Any]] = []
    for selected in selected_studies:
        objects = sorted(
            grouped[(selected.subject_id, selected.study_id)],
            key=lambda item: item["source_relative_path"],
        )
        observed_bytes = sum(item["size_bytes"] for item in objects)
        if len(objects) != selected.expected_object_count:
            _fail("CANARY_STUDY_OBJECT_COUNT_MISMATCH")
        if observed_bytes != selected.expected_byte_total:
            _fail("CANARY_STUDY_BYTE_TOTAL_MISMATCH")
        studies.append(
            {
                "study_id": selected.study_id,
                "subject_id": selected.subject_id,
                "split": "train",
                "selection_stratum": selected.selection_stratum,
                "known_no_cine": False,
                "prior_reconstruction_smoke": False,
                "complete_object_membership": True,
                "expected_object_count": len(objects),
                "expected_byte_total": observed_bytes,
                "objects": objects,
            }
        )
    configuration_hashes = [
        {"logical_name": name, "sha256": digest}
        for name, digest in sorted(source_configuration_hashes.items())
    ]
    manifest = {
        "selection_method_version": SELECTION_METHOD_VERSION,
        "source_release": SOURCE_RELEASE,
        "source_authority_commit": source_authority_commit,
        "source_manifest_sha256": source_manifest_sha256,
        "source_configuration_hashes": configuration_hashes,
        "study_count": EXACT_STUDIES,
        "subject_count": EXACT_SUBJECTS,
        "split": "train",
        "complete_object_membership": True,
        "expected_object_count": sum(item.expected_object_count for item in selected_studies),
        "expected_byte_total": sum(item.expected_byte_total for item in selected_studies),
        "studies": studies,
    }
    return seal_manifest(manifest)
