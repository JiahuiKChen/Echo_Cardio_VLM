#!/usr/bin/env python3
"""Fail-closed control plane for prospective selected-cohort C3.

This module implements deterministic planning, state and resume authority,
authorization-gated exact-object transport, download-artifact verification,
and cache-retirement decisions.  Live transport cannot run without a separate,
attempt-bound owner receipt; planning and synthetic validation remain entirely
offline.  The module has no bucket-listing, scheduler, DICOM, extraction,
embedding, deletion, or model execution path.  Row-level plans and ledgers are
restricted artifacts and must remain on SCC; only separately reviewed
aggregates may be exported.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

import yaml


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
BATCH_RE = re.compile(r"^c3_batch_[0-9]{3}$")
CANONICAL_ID_RE = re.compile(r"^[1-9][0-9]*$")
SOURCE_PATH_RE = re.compile(
    r"^files/p(?P<prefix>[0-9]{2})/p(?P<subject>[0-9]+)/"
    r"s(?P<study>[0-9]+)/(?P<filename>[A-Za-z0-9._-]+[.]dcm)$"
)

EXPECTED_PRODUCTION = {
    "selected_studies": 4_530,
    "selected_subjects": 4_530,
    "normalized_source_objects": 335_984,
    "selected_source_bytes": 1_216_569_133_322,
    "batch_count": 19,
    "studies_per_full_batch": 250,
    "final_batch_studies": 30,
}
EXPECTED_SELECTED_MANIFEST_SHA256 = (
    "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1"
)
EXPECTED_SPLIT_MAP_SHA256 = (
    "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517"
)
EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256 = (
    "35071385477515e40e6cc1503561b7c7aa434127435ce91aa3ae8b0a009188ff"
)
EXPECTED_CHECKPOINT_SHA256 = (
    "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
)
EXPECTED_FULL_CONTRACT_ID = "lvef_multitask_c3_production_orchestration_v2"
EXPECTED_FULL_PRODUCTION_ROOT = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
EXPECTED_FULL_RAW_ROOT_TEMPLATE = (
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2/"
    "attempts/{attempt_id}/raw"
)
TEST_ONLY_FULL_CONTRACT_ID = "synthetic_two_batch_full_sequential_v1"

STATES = (
    "PLANNED",
    "DOWNLOAD_IN_PROGRESS",
    "DOWNLOAD_VERIFIED",
    "DICOM_AUDIT_COMPLETE",
    "EXTRACTION_COMPLETE",
    "EMBEDDING_COMPLETE",
    "STUDY_POOLING_COMPLETE",
    "PRESERVATION_COMPLETE",
    "CACHE_RETIREMENT_ELIGIBLE",
    "FINALIZED",
    "FAILED_RETRYABLE",
    "FAILED_NONRETRYABLE",
    "QUARANTINED",
)
STATE_SEQUENCE = STATES[:10]
TERMINAL_STATES = frozenset({"FINALIZED", "FAILED_NONRETRYABLE", "QUARANTINED"})
RETRYABLE_FAILURE_CLASSES = frozenset(
    {"TRANSIENT_NETWORK", "TIMEOUT", "HTTP_408", "HTTP_429", "HTTP_5XX"}
)
NONRETRYABLE_FAILURE_CLASSES = frozenset(
    {
        "AUTHENTICATION",
        "AUTHORIZATION",
        "BILLING",
        "MANIFEST",
        "OWNERSHIP_CONFLICT",
        "GENERATION_MISMATCH",
        "SIZE_MISMATCH",
        "CHECKSUM_MISMATCH",
        "ZERO_BYTE",
        "HTTP_OTHER_4XX",
    }
)
PLAN_AUTHORITY_KEYS = frozenset(
    {
        "git_commit",
        "orchestration_contract_sha256",
        "selected_manifest_sha256",
        "selected_source_manifest_sha256",
        "source_metadata_sha256",
        "split_map_sha256",
        "checkpoint_sha256",
        "environment_receipt_sha256",
        "state_machine_schema_sha256",
        "resume_ledger_schema_sha256",
        "gcloud_resolution_receipt_sha256",
        "gcloud_executable_sha256",
        "crc32c_python_executable_sha256",
        "crc32c_worker_sha256",
        "crc32c_distribution_sha256",
    }
)
RUNTIME_AUTHORITY_KEYS = frozenset({*PLAN_AUTHORITY_KEYS, "batch_plan_sha256"})
SOURCE_OBJECT_KEYS = frozenset(
    {
        "source_object_key",
        "source_relative_path",
        "size_bytes",
        "generation",
        "md5_base64",
        "crc32c_base64",
    }
)
PLAN_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "contract_id",
        "algorithm",
        "authority",
        "cohort",
        "largest_batch",
        "batches",
    }
)
BATCH_KEYS = frozenset(
    {
        "batch_id",
        "ordinal",
        "n_studies",
        "n_subjects",
        "n_objects",
        "source_bytes",
        "study_membership_sha256",
        "source_membership_sha256",
        "studies",
        "objects",
    }
)
LARGEST_BATCH_KEYS = frozenset({"batch_id", "n_objects", "source_bytes"})
STUDY_ENTRY_KEYS = frozenset({"subject_id", "study_id", "split"})
LEDGER_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "attempt_id",
        "status",
        "authority",
        "journal_sequence",
        "journal_head_sha256",
        "batches",
    }
)
LEDGER_BATCH_KEYS = frozenset(
    {
        "state",
        "resume_state",
        "completed_states",
        "events",
        "download_attempts",
        "download_verification_receipts",
        "download_recovery_receipts",
        "download_manifest_sha256",
        "selected_batch_manifest_sha256",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "receipt_type",
        "attempt_id",
        "batch_id",
        "from_state",
        "to_state",
        "status",
        "authority",
        "input_receipt_sha256",
        "output_manifest_sha256",
    }
)
BODY_AUTHORIZATION_KEYS = frozenset(
    {
        "schema_version",
        "receipt_type",
        "status",
        "attempt_id",
        "scope",
        "batch_ids",
        "authority_sha256",
        "batch_plan_sha256",
        "launch_authority_sha256",
        "maximum_requests",
        "issued_at_utc",
        "expires_at_utc",
        "owner_authorization_recorded",
        "body_download_only",
        "scientific_actions_authorized",
    }
)
DIRECT_FULL_LAUNCH_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "governing_commit",
        "batch_plan_sha256",
        "selected_manifest_sha256",
        "selected_source_manifest_sha256",
        "split_map_sha256",
        "checkpoint_sha256",
        "selected_studies",
        "selected_subjects",
        "normalized_source_objects",
        "selected_source_bytes",
        "batch_count",
        "expected_no_cine_studies",
        "maximum_scheduler_submissions",
        "array_task_range",
        "array_max_concurrency",
        "raw_dicom_deletion_authorized",
        "extracted_cache_retirement_authorized_after_preservation",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
    }
)
RECOVERY_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "batch_id",
        "source_object_key",
        "recovery_class",
        "authority_sha256",
        "expected_identity_sha256",
        "final_sha256",
        "verification_receipt_sha256",
        "partial_artifact_present",
        "partial_sha256",
    }
)


class OrchestrationError(ValueError):
    """Fail-closed error with a stable, aggregate-safe code."""


class DownloadTransportError(OrchestrationError):
    """Safe transport failure carrying a closed retry-class value."""

    def __init__(self, code: str, failure_class: str):
        super().__init__(code)
        self.failure_class = failure_class


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> MutableMapping[Any, Any]:
    result: MutableMapping[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise OrchestrationError("YAML_DUPLICATE_KEY")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True)
class PlanRequirements:
    release: str
    selected_studies: int
    selected_subjects: int
    normalized_source_objects: int
    selected_source_bytes: int
    batch_count: int
    studies_per_full_batch: int
    final_batch_studies: int
    contract_id: str


@dataclass(frozen=True)
class DownloadExpectation:
    source_object_key: str
    source_relative_path: str
    size_bytes: int
    generation: str
    md5_base64: str
    crc32c_base64: str


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OrchestrationError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _open_regular_nofollow(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OrchestrationError("INPUT_NOT_REGULAR_NOFOLLOW_FILE") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise OrchestrationError("INPUT_NOT_REGULAR_NOFOLLOW_FILE")
    return descriptor, metadata


def read_regular_bytes(path: Path) -> bytes:
    descriptor, _ = _open_regular_nofollow(path)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def sha256_file(path: Path) -> str:
    descriptor, _ = _open_regular_nofollow(path)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def stream_regular_file_digests(
    path: Path, *, chunk_size: int = 8 * 1024 * 1024
) -> dict[str, Any]:
    """One bounded-memory pass using the pinned C-backed CRC32C implementation."""
    if (
        not isinstance(chunk_size, int)
        or isinstance(chunk_size, bool)
        or chunk_size < 1024
        or chunk_size > 64 * 1024 * 1024
    ):
        raise OrchestrationError("STREAM_DIGEST_CHUNK_SIZE_INVALID")
    try:
        import google_crc32c
    except ImportError as exc:
        raise OrchestrationError("GOOGLE_CRC32C_C_BACKEND_UNAVAILABLE") from exc
    if getattr(google_crc32c, "implementation", None) != "c":
        raise OrchestrationError("GOOGLE_CRC32C_C_BACKEND_UNAVAILABLE")
    descriptor, before = _open_regular_nofollow(path)
    sha256 = hashlib.sha256()
    try:
        md5 = hashlib.md5(usedforsecurity=False)
    except TypeError:  # pragma: no cover - compatibility with older Python.
        md5 = hashlib.md5()
    crc32c = google_crc32c.Checksum()
    observed_size = 0
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                observed_size += len(block)
                sha256.update(block)
                md5.update(block)
                crc32c.update(block)
            after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in stable_fields):
        raise OrchestrationError("FILE_CHANGED_DURING_STREAM_DIGEST")
    if observed_size != after.st_size:
        raise OrchestrationError("STREAM_DIGEST_SIZE_ACCOUNTING_MISMATCH")
    return {
        "size_bytes": observed_size,
        "sha256": sha256.hexdigest(),
        "md5_base64": base64.b64encode(md5.digest()).decode("ascii"),
        "crc32c_base64": base64.b64encode(crc32c.digest()).decode("ascii"),
        "file_device": int(after.st_dev),
        "file_inode": int(after.st_ino),
        "file_mtime_ns": int(after.st_mtime_ns),
        "chunk_size_bytes": chunk_size,
        "backend": "google_crc32c_c",
    }


def _inprocess_digest_provider(path: Path, request_id: str) -> Mapping[str, Any]:
    """Dependency-light test provider; production injects the external worker."""
    _require_sha256(request_id, "DIGEST_REQUEST_ID_INVALID")
    return stream_regular_file_digests(path)


class ExternalCRC32CDigestWorker:
    """Persistent, isolated C-backed digest helper for one production batch."""

    READY_KEYS = frozenset(
        {
            "protocol_version",
            "status",
            "google_crc32c_version",
            "google_crc32c_implementation",
            "google_crc32c_distribution_sha256",
            "google_crc32c_distribution_file_count",
            "known_vector_crc32c_base64",
        }
    )
    RESPONSE_KEYS = frozenset(
        {
            "protocol_version",
            "status",
            "request_id",
            "size_bytes",
            "sha256",
            "md5_base64",
            "crc32c_base64",
            "file_device",
            "file_inode",
            "file_mtime_ns",
            "chunk_size_bytes",
            "backend",
        }
    )

    def __init__(
        self,
        *,
        python_executable: Path,
        worker_script: Path,
        expected_python_sha256: str,
        expected_worker_sha256: str,
        expected_distribution_sha256: str,
        allowed_root: Path = Path("/restricted/projectnb"),
    ) -> None:
        for value, code in (
            (expected_python_sha256, "CRC32C_PYTHON_HASH_INVALID"),
            (expected_worker_sha256, "CRC32C_WORKER_HASH_INVALID"),
            (expected_distribution_sha256, "CRC32C_DISTRIBUTION_HASH_INVALID"),
        ):
            _require_sha256(value, code)
        if (
            python_executable.is_symlink()
            or not python_executable.is_file()
            or not os.access(python_executable, os.X_OK)
            or sha256_file(python_executable) != expected_python_sha256
        ):
            raise OrchestrationError("CRC32C_PYTHON_AUTHORITY_MISMATCH")
        if (
            worker_script.is_symlink()
            or not worker_script.is_file()
            or sha256_file(worker_script) != expected_worker_sha256
        ):
            raise OrchestrationError("CRC32C_WORKER_AUTHORITY_MISMATCH")
        if (
            not allowed_root.is_absolute()
            or allowed_root.is_symlink()
            or not allowed_root.is_dir()
        ):
            raise OrchestrationError("CRC32C_ALLOWED_ROOT_INVALID")
        self._expected_distribution_sha256 = expected_distribution_sha256
        self._process: subprocess.Popen[str] | None = subprocess.Popen(
            [
                str(python_executable),
                "-I",
                "-u",
                str(worker_script),
                "--serve",
                "--allowed-root",
                str(allowed_root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd="/",
            env={
                "LC_ALL": "C",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        try:
            ready = self._read_response("CRC32C_WORKER_STARTUP_FAILED")
            if (
                set(ready) != self.READY_KEYS
                or ready.get("protocol_version") != 1
                or ready.get("status") != "READY"
                or ready.get("google_crc32c_implementation") != "c"
                or not isinstance(ready.get("google_crc32c_version"), str)
                or not ready["google_crc32c_version"]
                or ready.get("known_vector_crc32c_base64") != "4waSgw=="
                or ready.get("google_crc32c_distribution_sha256")
                != expected_distribution_sha256
                or not isinstance(
                    ready.get("google_crc32c_distribution_file_count"), int
                )
                or isinstance(
                    ready.get("google_crc32c_distribution_file_count"), bool
                )
                or ready["google_crc32c_distribution_file_count"] < 1
            ):
                raise OrchestrationError("CRC32C_WORKER_STARTUP_AUTHORITY_MISMATCH")
        except Exception:
            self._terminate()
            raise

    def _read_response(self, code: str) -> Mapping[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise OrchestrationError(code)
        line = process.stdout.readline()
        if not line or len(line.encode("utf-8")) > 65_536:
            raise OrchestrationError(code)
        try:
            value = json.loads(line, object_pairs_hook=_strict_pairs)
        except (json.JSONDecodeError, OrchestrationError) as exc:
            raise OrchestrationError(code) from exc
        if not isinstance(value, Mapping):
            raise OrchestrationError(code)
        return value

    def digest(
        self, path: Path, request_id: str, *, chunk_size: int = 8 * 1024 * 1024
    ) -> dict[str, Any]:
        if SHA256_RE.fullmatch(request_id) is None:
            raise OrchestrationError("CRC32C_WORKER_REQUEST_ID_INVALID")
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise OrchestrationError("CRC32C_WORKER_NOT_RUNNING")
        request = {
            "protocol_version": 1,
            "command": "DIGEST",
            "request_id": request_id,
            "path": str(path),
            "chunk_size": chunk_size,
        }
        try:
            process.stdin.write(json.dumps(request, sort_keys=True) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise OrchestrationError("CRC32C_WORKER_REQUEST_FAILED") from exc
        response = self._read_response("CRC32C_WORKER_RESPONSE_INVALID")
        if response.get("status") == "FAIL":
            error_code = response.get("error_code")
            self._terminate()
            if isinstance(error_code, str) and re.fullmatch(r"[A-Z0-9_]+", error_code):
                raise OrchestrationError(f"CRC32C_WORKER_{error_code}")
            raise OrchestrationError("CRC32C_WORKER_DIGEST_FAILED")
        try:
            if (
                set(response) != self.RESPONSE_KEYS
                or response.get("protocol_version") != 1
                or response.get("status") != "PASS"
                or response.get("request_id") != request_id
                or response.get("backend") != "google_crc32c_c_external_worker_v1"
            ):
                raise OrchestrationError("CRC32C_WORKER_RESPONSE_AUTHORITY_MISMATCH")
            _require_sha256(response.get("sha256"), "CRC32C_WORKER_DIGEST_INVALID")
            _base64_digest(
                response.get("md5_base64"), 16, "CRC32C_WORKER_MD5_INVALID"
            )
            _base64_digest(
                response.get("crc32c_base64"), 4, "CRC32C_WORKER_CRC_INVALID"
            )
            for key in (
                "size_bytes",
                "file_device",
                "file_inode",
                "file_mtime_ns",
                "chunk_size_bytes",
            ):
                value = response.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise OrchestrationError("CRC32C_WORKER_FILE_METADATA_INVALID")
            if response["chunk_size_bytes"] != chunk_size:
                raise OrchestrationError("CRC32C_WORKER_CHUNK_SIZE_MISMATCH")
        except OrchestrationError:
            self._terminate()
            raise
        return dict(response)

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._terminate()
        if process.returncode != 0:
            raise OrchestrationError("CRC32C_WORKER_EXIT_INVALID")

    def __enter__(self) -> "ExternalCRC32CDigestWorker":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is None:
            self.close()
        else:
            self._terminate()
        return False


def load_strict_json(path: Path) -> Any:
    try:
        return json.loads(
            read_regular_bytes(path).decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except UnicodeDecodeError as exc:
        raise OrchestrationError("JSON_NOT_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise OrchestrationError("JSON_INVALID") from exc


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise OrchestrationError(code)


def _require_sha256(value: Any, code: str) -> str:
    text = str(value)
    if not SHA256_RE.fullmatch(text):
        raise OrchestrationError(code)
    return text


def _require_commit(value: Any) -> str:
    text = str(value)
    if not GIT_COMMIT_RE.fullmatch(text):
        raise OrchestrationError("GIT_COMMIT_INVALID")
    return text


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise OrchestrationError(code)
    try:
        integer = int(value)
    except (TypeError, ValueError):
        raise OrchestrationError(code) from None
    if integer <= 0 or str(value).strip() != str(integer):
        raise OrchestrationError(code)
    return integer


def _canonical_id(value: Any, code: str) -> str:
    text = str(value)
    if not CANONICAL_ID_RE.fullmatch(text) or str(int(text)) != text:
        raise OrchestrationError(code)
    return text


def _base64_digest(value: Any, decoded_size: int, code: str) -> str:
    text = str(value)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise OrchestrationError(code) from None
    if len(decoded) != decoded_size or base64.b64encode(decoded).decode("ascii") != text:
        raise OrchestrationError(code)
    return text


def _safe_source_path(value: Any, subject_id: str, study_id: str) -> str:
    text = str(value)
    pure = PurePosixPath(text)
    if (
        not text
        or text != pure.as_posix()
        or pure.is_absolute()
        or "\\" in text
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise OrchestrationError("SOURCE_PATH_UNSAFE")
    match = SOURCE_PATH_RE.fullmatch(text)
    if match is None:
        raise OrchestrationError("SOURCE_PATH_OUTSIDE_LOCKED_RELEASE")
    if match.group("subject") != subject_id or match.group("study") != study_id:
        raise OrchestrationError("SOURCE_PATH_OWNERSHIP_CONFLICT")
    if match.group("prefix") != f"{int(subject_id) // 1_000_000:02d}":
        raise OrchestrationError("SOURCE_PATH_PREFIX_CONFLICT")
    return text


def _load_yaml_unique(path: Path) -> Mapping[str, Any]:
    try:
        payload = yaml.load(read_regular_bytes(path), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise OrchestrationError("ORCHESTRATION_CONTRACT_YAML_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise OrchestrationError("ORCHESTRATION_CONTRACT_NOT_MAPPING")
    return payload


def expected_transition_graph() -> dict[str, list[str]]:
    failure_targets = ["FAILED_RETRYABLE", "FAILED_NONRETRYABLE", "QUARANTINED"]
    graph = {
        state: [STATE_SEQUENCE[index + 1], *failure_targets]
        for index, state in enumerate(STATE_SEQUENCE[:-1])
    }
    graph["FAILED_RETRYABLE"] = list(STATE_SEQUENCE[:-1])
    return dict(sorted(graph.items()))


def validate_state_machine_schema(schema: Mapping[str, Any]) -> None:
    expected_keys = {
        "additionalProperties",
        "artifact_type",
        "authorization_state",
        "initial_state",
        "schema_version",
        "states",
        "transitions",
    }
    if not isinstance(schema, Mapping) or set(schema) != expected_keys:
        raise OrchestrationError("STATE_MACHINE_SCHEMA_NOT_CLOSED")
    if (
        schema.get("schema_version") != 2
        or schema.get("artifact_type") != "lvef_c3_state_machine_schema_v2"
        or schema.get("authorization_state") != "UNAUTHORIZED"
        or schema.get("additionalProperties") is not False
        or schema.get("initial_state") != "PLANNED"
        or tuple(schema.get("states", ())) != STATES
        or schema.get("transitions") != expected_transition_graph()
    ):
        raise OrchestrationError("STATE_MACHINE_SCHEMA_SEMANTICS_INVALID")


def validate_resume_ledger_schema(schema: Mapping[str, Any]) -> None:
    expected_keys = {
        "additionalProperties",
        "artifact_type",
        "authority_fields",
        "authorization_state",
        "ledger_fields",
        "batch_fields",
        "batch_scoped_ledgers_required",
        "shared_array_task_ledger_mutation_permitted",
        "download_start_transition_receipt_required",
        "download_transaction_recovery_receipt_required",
        "journal_artifact_type",
        "journal_operation_set",
        "full_ledger_snapshot_per_object_permitted",
        "no_clobber",
        "schema_version",
        "stale_or_partial_artifact_may_promote_state",
    }
    if not isinstance(schema, Mapping) or set(schema) != expected_keys:
        raise OrchestrationError("RESUME_LEDGER_SCHEMA_NOT_CLOSED")
    if (
        schema.get("schema_version") != 2
        or schema.get("artifact_type") != "lvef_c3_resume_ledger_schema_v2"
        or schema.get("authorization_state") != "UNAUTHORIZED"
        or schema.get("additionalProperties") is not False
        or set(schema.get("ledger_fields", ())) != set(LEDGER_KEYS)
        or set(schema.get("authority_fields", ())) != set(RUNTIME_AUTHORITY_KEYS)
        or set(schema.get("batch_fields", ())) != set(LEDGER_BATCH_KEYS)
        or schema.get("batch_scoped_ledgers_required") is not True
        or schema.get("shared_array_task_ledger_mutation_permitted") is not False
        or schema.get("download_start_transition_receipt_required") is not True
        or schema.get("download_transaction_recovery_receipt_required") is not True
        or schema.get("journal_artifact_type")
        != "lvef_c3_resume_journal_delta_v2"
        or tuple(schema.get("journal_operation_set", ()))
        != (
            "TRANSITION",
            "SELECTED_BATCH_MANIFEST",
            "DOWNLOAD_ATTEMPT",
            "DOWNLOAD_VERIFIED",
            "DOWNLOAD_MANIFEST",
        )
        or schema.get("full_ledger_snapshot_per_object_permitted") is not False
        or schema.get("no_clobber") is not True
        or schema.get("stale_or_partial_artifact_may_promote_state") is not False
    ):
        raise OrchestrationError("RESUME_LEDGER_SCHEMA_SEMANTICS_INVALID")


def _validate_bound_control_schemas(
    contract: Mapping[str, Any], *, contract_path: Path
) -> None:
    authority = contract.get("authority")
    expected_authority_keys = {
        "branch",
        "execution_contract_version",
        "state_machine_schema_filename",
        "state_machine_schema_sha256",
        "resume_ledger_schema_filename",
        "resume_ledger_schema_sha256",
        "runtime_bindings_required",
        "changed_authority_requires_new_attempt",
    }
    if not isinstance(authority, Mapping) or set(authority) != expected_authority_keys:
        raise OrchestrationError("CONTRACT_AUTHORITY_SCHEMA_INVALID")
    if (
        authority.get("branch") != "codex/lvef-multitask-revalidation"
        or authority.get("execution_contract_version") != 2
        or authority.get("changed_authority_requires_new_attempt") is not True
        or set(authority.get("runtime_bindings_required", ()))
        != set(RUNTIME_AUTHORITY_KEYS)
    ):
        raise OrchestrationError("CONTRACT_RUNTIME_BINDINGS_INVALID")
    schema_bindings = (
        (
            "state_machine_schema_filename",
            "state_machine_schema_sha256",
            "lvef_c3_state_machine_v2.json",
            validate_state_machine_schema,
        ),
        (
            "resume_ledger_schema_filename",
            "resume_ledger_schema_sha256",
            "lvef_c3_resume_ledger_v2.json",
            validate_resume_ledger_schema,
        ),
    )
    for filename_key, digest_key, expected_filename, validator in schema_bindings:
        if authority.get(filename_key) != expected_filename:
            raise OrchestrationError("CONTROL_SCHEMA_FILENAME_CHANGED")
        expected_digest = _require_sha256(
            authority.get(digest_key), "CONTROL_SCHEMA_HASH_INVALID"
        )
        schema_path = contract_path.parent / expected_filename
        if sha256_file(schema_path) != expected_digest:
            raise OrchestrationError("CONTROL_SCHEMA_HASH_MISMATCH")
        schema = load_strict_json(schema_path)
        if not isinstance(schema, Mapping):
            raise OrchestrationError("CONTROL_SCHEMA_NOT_MAPPING")
        validator(schema)


def load_orchestration_contract(path: Path) -> Mapping[str, Any]:
    contract = _load_yaml_unique(path)
    expected_top = {
        "schema_version",
        "contract_id",
        "status",
        "mode",
        "cohort",
        "batching",
        "authority",
        "storage",
        "downloader",
        "state_machine",
        "cache_retirement",
        "embedding",
        "authorization",
    }
    if set(contract) != expected_top:
        raise OrchestrationError("ORCHESTRATION_CONTRACT_TOP_LEVEL_SCHEMA_INVALID")
    if (
        contract.get("schema_version") != 2
        or contract.get("status") != "IMPLEMENTED_OFFLINE_LOCK_UNAUTHORIZED"
        or contract.get("mode")
        != "PRODUCTION_IMPLEMENTED_OWNER_GATED_UNAUTHORIZED"
    ):
        raise OrchestrationError("ORCHESTRATION_CONTRACT_MODE_INVALID")
    cohort = contract["cohort"]
    batching = contract["batching"]
    expected_nested_keys = {
        "cohort": {
            "release",
            "selected_studies",
            "selected_subjects",
            "normalized_source_objects",
            "selected_source_bytes",
            "selected_manifest_sha256",
            "split_map_sha256",
            "selected_source_manifest_sha256",
            "outside_selected_studies_permitted",
            "implicit_cohort_expansion_permitted",
            "object_listing_permitted",
        },
        "batching": {
            "algorithm",
            "batch_prefix",
            "batch_count",
            "studies_per_full_batch",
            "final_batch_studies",
            "default_active_batches",
            "maximum_active_batches_after_capacity_proof",
            "study_may_span_batches",
        },
        "storage": {
            "production_root",
            "raw_root",
            "extracted_cache_root",
            "state_root",
            "partial_suffix",
            "largest_active_extraction_cache_bytes",
            "required_free_reserve_bytes",
            "one_batch_default_enforced",
            "two_batch_overlap_authorized",
            "raw_deletion_enabled",
            "extracted_cache_retirement_implemented",
            "extracted_cache_retirement_authorized",
            "owner_cache_retirement_authorization_required",
        },
        "downloader": {
            "implementation_mode",
            "live_transport_present",
            "live_requests_authorized",
            "exact_manifest_objects_only",
            "billing_project_environment_variable",
            "cloudsdk_config_environment_variable",
            "cloudsdk_config_receipt_environment_variable",
            "cloudsdk_config_receipt_sha256_environment_variable",
            "billing_project_in_argv_permitted",
            "billing_project_in_logs_permitted",
            "maximum_attempts_per_object",
            "retry_backoff_initial_seconds",
            "retry_backoff_max_seconds",
            "token_refresh_interval_seconds",
            "streaming_digest_backend",
            "crc32c_runtime_source",
            "crc32c_worker_protocol_version",
            "streaming_digest_chunk_bytes",
            "retryable_failure_classes",
            "nonretryable_failure_classes",
            "verification_required",
            "atomic_no_clobber_finalization",
        },
        "state_machine": {
            "initial_state",
            "states",
            "terminal_states",
            "file_existence_may_promote_state",
            "checksummed_receipt_required_for_every_transition",
            "prior_attempt_overwrite_permitted",
        },
        "embedding": {
            "checkpoint_filename",
            "checkpoint_sha256",
            "clip_dimension",
            "clip_dtype",
            "encoder_only",
            "view_classifier_used",
        },
        "authorization": {
            "source_body_download",
            "scheduler_submission",
            "real_dicom_decode",
            "cine_extraction",
            "echoprime_inference",
            "embedding_generation",
            "cache_retirement",
            "model_fitting",
            "prediction_generation",
            "confirmatory_performance_access",
        },
    }
    for section, expected_keys in expected_nested_keys.items():
        value = contract.get(section)
        if not isinstance(value, Mapping) or set(value) != expected_keys:
            raise OrchestrationError(
                f"ORCHESTRATION_CONTRACT_{section.upper()}_SCHEMA_INVALID"
            )
    for key in (
        "selected_studies",
        "selected_subjects",
        "normalized_source_objects",
        "selected_source_bytes",
    ):
        if cohort.get(key) != EXPECTED_PRODUCTION[key]:
            raise OrchestrationError("PRODUCTION_COHORT_CONSTANT_CHANGED")
    for key in ("batch_count", "studies_per_full_batch", "final_batch_studies"):
        if batching.get(key) != EXPECTED_PRODUCTION[key]:
            raise OrchestrationError("PRODUCTION_BATCH_CONSTANT_CHANGED")
    if (
        cohort.get("release") != "mimic-iv-echo/1.0"
        or cohort.get("outside_selected_studies_permitted") is not False
        or cohort.get("implicit_cohort_expansion_permitted") is not False
        or batching.get("algorithm")
        != "numeric_subject_then_numeric_study_contiguous_v1"
        or batching.get("batch_prefix") != "c3_batch_"
        or batching.get("maximum_active_batches_after_capacity_proof") != 2
        or batching.get("study_may_span_batches") is not False
    ):
        raise OrchestrationError("PRODUCTION_COHORT_OR_BATCH_SEMANTICS_CHANGED")
    if (
        cohort.get("selected_manifest_sha256") != EXPECTED_SELECTED_MANIFEST_SHA256
        or cohort.get("split_map_sha256") != EXPECTED_SPLIT_MAP_SHA256
        or cohort.get("selected_source_manifest_sha256")
        != EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        or contract["embedding"].get("checkpoint_sha256")
        != EXPECTED_CHECKPOINT_SHA256
    ):
        raise OrchestrationError("PRODUCTION_HASH_AUTHORITY_CHANGED")
    if cohort.get("object_listing_permitted") is not False:
        raise OrchestrationError("OBJECT_LISTING_PREAUTHORIZED")
    downloader = contract["downloader"]
    if (
        downloader.get("implementation_mode")
        != "AUTHORIZATION_GATED_EXACT_OBJECT_TRANSPORT"
        or downloader.get("live_transport_present") is not True
        or downloader.get("live_requests_authorized") is not False
    ):
        raise OrchestrationError("LIVE_DOWNLOADER_PREAUTHORIZED")
    if (
        downloader.get("exact_manifest_objects_only") is not True
        or downloader.get("billing_project_in_argv_permitted") is not False
        or downloader.get("billing_project_in_logs_permitted") is not False
        or downloader.get("maximum_attempts_per_object") != 5
        or tuple(downloader.get("retryable_failure_classes", ()))
        != ("TRANSIENT_NETWORK", "TIMEOUT", "HTTP_408", "HTTP_429", "HTTP_5XX")
        or tuple(downloader.get("nonretryable_failure_classes", ()))
        != (
            "AUTHENTICATION",
            "AUTHORIZATION",
            "BILLING",
            "MANIFEST",
            "OWNERSHIP_CONFLICT",
            "GENERATION_MISMATCH",
            "SIZE_MISMATCH",
            "CHECKSUM_MISMATCH",
            "ZERO_BYTE",
            "HTTP_OTHER_4XX",
        )
        or tuple(downloader.get("verification_required", ()))
        != ("size_bytes", "generation", "md5_base64", "crc32c_base64", "local_sha256")
        or downloader.get("atomic_no_clobber_finalization") is not True
    ):
        raise OrchestrationError("DOWNLOADER_CONTRACT_SEMANTICS_CHANGED")
    if (
        downloader.get("streaming_digest_backend")
        != "google_crc32c_c_external_worker_v1"
        or downloader.get("crc32c_runtime_source")
        != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or downloader.get("crc32c_worker_protocol_version") != 1
        or downloader.get("streaming_digest_chunk_bytes") != 8 * 1024 * 1024
        or downloader.get("token_refresh_interval_seconds") != 2_400
        or downloader.get("retry_backoff_initial_seconds") != 2
        or downloader.get("retry_backoff_max_seconds") != 30
    ):
        raise OrchestrationError("DOWNLOADER_RUNTIME_PERFORMANCE_AUTHORITY_CHANGED")
    expected_private_environment = {
        "billing_project_environment_variable": "LVEF_C3_GCP_BILLING_PROJECT",
        "cloudsdk_config_environment_variable": "LVEF_C3_CLOUDSDK_CONFIG",
        "cloudsdk_config_receipt_environment_variable": (
            "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT"
        ),
        "cloudsdk_config_receipt_sha256_environment_variable": (
            "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256"
        ),
    }
    if any(
        downloader.get(key) != value
        for key, value in expected_private_environment.items()
    ):
        raise OrchestrationError("DOWNLOADER_PRIVATE_ENVIRONMENT_AUTHORITY_CHANGED")
    if (
        contract["state_machine"].get("initial_state") != "PLANNED"
        or tuple(contract["state_machine"].get("states", ())) != STATES
        or tuple(contract["state_machine"].get("terminal_states", ()))
        != ("FINALIZED", "FAILED_NONRETRYABLE", "QUARANTINED")
        or contract["state_machine"].get("file_existence_may_promote_state") is not False
        or contract["state_machine"].get("checksummed_receipt_required_for_every_transition")
        is not True
        or contract["state_machine"].get("prior_attempt_overwrite_permitted") is not False
    ):
        raise OrchestrationError("STATE_MACHINE_CLOSED_SET_CHANGED")
    _validate_bound_control_schemas(contract, contract_path=path)
    authorization = contract["authorization"]
    if any(value is not False for value in authorization.values()):
        raise OrchestrationError("PRODUCTION_ACTION_PREAUTHORIZED")
    embedding = contract["embedding"]
    if (
        embedding.get("checkpoint_filename") != "echo_prime_encoder.pt"
        or embedding.get("clip_dimension") != 512
        or embedding.get("clip_dtype") != "float32"
        or embedding.get("encoder_only") is not True
        or embedding.get("view_classifier_used") is not False
    ):
        raise OrchestrationError("EMBEDDING_CONTRACT_SEMANTICS_CHANGED")
    if contract["storage"].get("raw_deletion_enabled") is not False:
        raise OrchestrationError("RAW_DELETION_ENABLED")
    if (
        contract["storage"].get("extracted_cache_retirement_implemented") is not True
        or contract["storage"].get("extracted_cache_retirement_authorized") is not False
    ):
        raise OrchestrationError("CACHE_RETIREMENT_IMPLEMENTATION_AUTHORITY_INVALID")
    cache_retirement = contract["cache_retirement"]
    expected_cache_retirement_keys = {
        "raw_dicom_deletion_default_authorized",
        "raw_dicom_deletion_owner_authorizable",
        "extracted_cache_retirement_default_authorized",
        "extracted_cache_retirement_owner_authorizable",
        "required_state",
        "required_completed_states",
        "explicit_owner_authorization_required",
        "preservation_receipt_required",
    }
    if (
        not isinstance(cache_retirement, Mapping)
        or set(cache_retirement) != expected_cache_retirement_keys
        or cache_retirement.get("raw_dicom_deletion_default_authorized") is not False
        or cache_retirement.get("raw_dicom_deletion_owner_authorizable") is not False
        or cache_retirement.get("extracted_cache_retirement_default_authorized")
        is not False
        or cache_retirement.get("extracted_cache_retirement_owner_authorizable")
        is not True
        or cache_retirement.get("required_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or cache_retirement.get("explicit_owner_authorization_required") is not True
        or cache_retirement.get("preservation_receipt_required") is not True
        or tuple(cache_retirement.get("required_completed_states", ()))
        != (
            "DOWNLOAD_VERIFIED",
            "DICOM_AUDIT_COMPLETE",
            "EXTRACTION_COMPLETE",
            "EMBEDDING_COMPLETE",
            "STUDY_POOLING_COMPLETE",
            "PRESERVATION_COMPLETE",
        )
    ):
        raise OrchestrationError("CACHE_RETIREMENT_CONTRACT_INVALID")
    for key, suffix in (
        ("raw_root", "/attempts/{attempt_id}/raw"),
        ("extracted_cache_root", "/attempts/{attempt_id}/extracted_cache"),
        ("state_root", "/attempts/{attempt_id}/state"),
    ):
        template = str(contract["storage"].get(key, ""))
        if (
            template.count("{attempt_id}") != 1
            or not template.startswith("/restricted/projectnb/")
            or not template.endswith(suffix)
        ):
            raise OrchestrationError("ATTEMPT_SCOPED_STORAGE_TEMPLATE_INVALID")
    if (
        contract["storage"].get("largest_active_extraction_cache_bytes")
        != 92_286_910_464
        or contract["storage"].get("required_free_reserve_bytes")
        != 200_000_000_000
        or contract["storage"].get("one_batch_default_enforced") is not True
        or contract["storage"].get("two_batch_overlap_authorized") is not False
        or batching.get("default_active_batches") != 1
        or contract["storage"].get("partial_suffix") != ".partial"
        or contract["storage"].get("owner_cache_retirement_authorization_required")
        is not True
    ):
        raise OrchestrationError("ROLLING_CACHE_AUTHORITY_CHANGED")
    return contract


def production_requirements(contract: Mapping[str, Any]) -> PlanRequirements:
    cohort = contract["cohort"]
    batching = contract["batching"]
    return PlanRequirements(
        release=str(cohort["release"]),
        selected_studies=int(cohort["selected_studies"]),
        selected_subjects=int(cohort["selected_subjects"]),
        normalized_source_objects=int(cohort["normalized_source_objects"]),
        selected_source_bytes=int(cohort["selected_source_bytes"]),
        batch_count=int(batching["batch_count"]),
        studies_per_full_batch=int(batching["studies_per_full_batch"]),
        final_batch_studies=int(batching["final_batch_studies"]),
        contract_id=str(contract["contract_id"]),
    )


def validate_plan_authority_against_contract(
    authority: Mapping[str, Any], *, contract: Mapping[str, Any], contract_path: Path
) -> dict[str, str]:
    normalized = _validate_plan_authority(authority)
    expected = {
        "orchestration_contract_sha256": sha256_file(contract_path),
        "selected_manifest_sha256": str(contract["cohort"]["selected_manifest_sha256"]),
        "selected_source_manifest_sha256": str(
            contract["cohort"]["selected_source_manifest_sha256"]
        ),
        "split_map_sha256": str(contract["cohort"]["split_map_sha256"]),
        "checkpoint_sha256": str(contract["embedding"]["checkpoint_sha256"]),
        "state_machine_schema_sha256": str(
            contract["authority"]["state_machine_schema_sha256"]
        ),
        "resume_ledger_schema_sha256": str(
            contract["authority"]["resume_ledger_schema_sha256"]
        ),
    }
    if any(normalized[key] != value for key, value in expected.items()):
        raise OrchestrationError("PLAN_AUTHORITY_CONTRACT_MISMATCH")
    return normalized


def _validate_plan_authority(authority: Mapping[str, Any]) -> dict[str, str]:
    _require_exact_keys(authority, PLAN_AUTHORITY_KEYS, "PLAN_AUTHORITY_SCHEMA_INVALID")
    normalized = {key: str(authority[key]) for key in PLAN_AUTHORITY_KEYS}
    normalized["git_commit"] = _require_commit(normalized["git_commit"])
    for key in PLAN_AUTHORITY_KEYS - {"git_commit"}:
        normalized[key] = _require_sha256(
            normalized[key], f"PLAN_AUTHORITY_{key.upper()}_INVALID"
        )
    return dict(sorted(normalized.items()))


def _normalize_source_row(
    row: Mapping[str, Any], *, release: str, subject_id: str, study_id: str
) -> dict[str, Any]:
    relative = _safe_source_path(row.get("source_relative_path"), subject_id, study_id)
    source_key = _require_sha256(row.get("source_object_key"), "SOURCE_OBJECT_KEY_INVALID")
    derived = hashlib.sha256(f"{release}\0{relative}".encode("utf-8")).hexdigest()
    if source_key != derived:
        raise OrchestrationError("SOURCE_OBJECT_KEY_DERIVATION_MISMATCH")

    def one_of(names: Sequence[str], code: str) -> Any:
        present = [name for name in names if row.get(name) not in (None, "")]
        if len(present) != 1:
            raise OrchestrationError(code)
        return row[present[0]]

    size = _positive_int(
        one_of(
            ("expected_size_bytes", "remote_size_bytes", "size_bytes"),
            "SOURCE_SIZE_AMBIGUOUS",
        ),
        "SOURCE_SIZE_INVALID",
    )
    generation = str(
        one_of(
            ("expected_generation", "remote_generation", "generation"),
            "SOURCE_GENERATION_AMBIGUOUS",
        )
    )
    if not generation.isdigit() or str(int(generation)) != generation or int(generation) <= 0:
        raise OrchestrationError("SOURCE_GENERATION_INVALID")
    md5 = _base64_digest(
        one_of(
            ("expected_md5_base64", "remote_md5_base64", "md5_base64"),
            "SOURCE_MD5_AMBIGUOUS",
        ),
        16,
        "SOURCE_MD5_INVALID",
    )
    crc = _base64_digest(
        one_of(
            ("expected_crc32c_base64", "remote_crc32c_base64", "crc32c_base64"),
            "SOURCE_CRC32C_AMBIGUOUS",
        ),
        4,
        "SOURCE_CRC32C_INVALID",
    )
    if row.get("release_id", release) != release:
        raise OrchestrationError("SOURCE_RELEASE_MISMATCH")
    return {
        "source_object_key": source_key,
        "source_relative_path": relative,
        "size_bytes": size,
        "generation": generation,
        "md5_base64": md5,
        "crc32c_base64": crc,
    }


def reconcile_selected_source_metadata(
    selected_source_rows: Sequence[Mapping[str, Any]],
    metadata_rows: Sequence[Mapping[str, Any]],
    *,
    release: str,
) -> list[dict[str, Any]]:
    """Join the frozen locator authority to immutable metadata one-to-one."""
    source_by_key: dict[str, Mapping[str, Any]] = {}
    source_paths: set[str] = set()
    for row in selected_source_rows:
        subject = _canonical_id(row.get("subject_id"), "SOURCE_SUBJECT_ID_INVALID")
        study = _canonical_id(row.get("study_id"), "SOURCE_STUDY_ID_INVALID")
        relative = _safe_source_path(row.get("source_relative_path"), subject, study)
        key = _require_sha256(row.get("source_object_key"), "SOURCE_OBJECT_KEY_INVALID")
        derived = hashlib.sha256(f"{release}\0{relative}".encode("utf-8")).hexdigest()
        if key != derived:
            raise OrchestrationError("SOURCE_OBJECT_KEY_DERIVATION_MISMATCH")
        if key in source_by_key or relative in source_paths:
            raise OrchestrationError("DUPLICATE_SELECTED_SOURCE_AUTHORITY")
        if row.get("release_id", release) != release:
            raise OrchestrationError("SOURCE_RELEASE_MISMATCH")
        source_by_key[key] = row
        source_paths.add(relative)
    metadata_by_key: dict[str, Mapping[str, Any]] = {}
    metadata_paths: set[str] = set()
    for row in metadata_rows:
        subject = _canonical_id(row.get("subject_id"), "METADATA_SUBJECT_ID_INVALID")
        study = _canonical_id(row.get("study_id"), "METADATA_STUDY_ID_INVALID")
        relative = _safe_source_path(row.get("source_relative_path"), subject, study)
        key = _require_sha256(row.get("source_object_key"), "METADATA_OBJECT_KEY_INVALID")
        if key in metadata_by_key or relative in metadata_paths:
            raise OrchestrationError("DUPLICATE_SOURCE_METADATA_AUTHORITY")
        if (
            row.get("preflight_status") != "PASS"
            or row.get("discrepancy_reasons") not in ([], None)
        ):
            raise OrchestrationError("SOURCE_METADATA_PREFLIGHT_NOT_PASS")
        if not BATCH_RE.fullmatch(str(row.get("production_batch"))):
            raise OrchestrationError("SOURCE_METADATA_BATCH_ASSIGNMENT_MISSING")
        metadata_by_key[key] = row
        metadata_paths.add(relative)
    if set(source_by_key) != set(metadata_by_key) or source_paths != metadata_paths:
        raise OrchestrationError("SOURCE_METADATA_NOT_ONE_TO_ONE")
    merged: list[dict[str, Any]] = []
    for key in sorted(source_by_key):
        source = source_by_key[key]
        metadata = metadata_by_key[key]
        identity_fields = ("subject_id", "study_id", "split", "source_relative_path")
        if any(str(source.get(field)) != str(metadata.get(field)) for field in identity_fields):
            raise OrchestrationError("SOURCE_METADATA_IDENTITY_CONFLICT")
        merged.append(
            {
                "release_id": release,
                "subject_id": str(source["subject_id"]),
                "study_id": str(source["study_id"]),
                "split": str(source["split"]),
                "source_relative_path": str(source["source_relative_path"]),
                "source_object_key": key,
                "production_batch": metadata.get("production_batch"),
                "remote_size_bytes": metadata.get("remote_size_bytes"),
                "remote_md5_base64": metadata.get("remote_md5_base64"),
                "remote_crc32c_base64": metadata.get("remote_crc32c_base64"),
                "remote_generation": metadata.get("remote_generation"),
            }
        )
    return merged


def build_immutable_batch_plan(
    selected_rows: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    *,
    requirements: PlanRequirements,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a restricted deterministic plan from already-validated authorities."""
    normalized_authority = _validate_plan_authority(authority)
    expected_batches = (
        requirements.selected_studies + requirements.studies_per_full_batch - 1
    ) // requirements.studies_per_full_batch
    expected_final = (
        requirements.selected_studies
        - requirements.studies_per_full_batch * (requirements.batch_count - 1)
    )
    if (
        requirements.batch_count != expected_batches
        or requirements.final_batch_studies != expected_final
    ):
        raise OrchestrationError("BATCH_REQUIREMENTS_INTERNALLY_INCONSISTENT")
    selected: list[tuple[str, str]] = []
    subjects: set[str] = set()
    studies: set[str] = set()
    for row in selected_rows:
        subject = _canonical_id(row.get("subject_id"), "SELECTED_SUBJECT_ID_INVALID")
        study = _canonical_id(row.get("study_id"), "SELECTED_STUDY_ID_INVALID")
        if subject in subjects or study in studies:
            raise OrchestrationError("SELECTED_COHORT_NOT_ONE_STUDY_PER_SUBJECT")
        subjects.add(subject)
        studies.add(study)
        selected.append((subject, study))
    selected.sort(key=lambda value: (int(value[0]), int(value[1])))
    if len(selected) != requirements.selected_studies or len(subjects) != requirements.selected_subjects:
        raise OrchestrationError("SELECTED_COHORT_COUNT_MISMATCH")

    split_map: dict[str, str] = {}
    for row in split_rows:
        subject = _canonical_id(row.get("subject_id"), "SPLIT_SUBJECT_ID_INVALID")
        split = str(row.get("split"))
        if subject in split_map or split not in {"train", "val", "test"}:
            raise OrchestrationError("SPLIT_MAP_INVALID")
        split_map[subject] = split
    if set(split_map) != subjects:
        raise OrchestrationError("SPLIT_MAP_COHORT_MISMATCH")

    batch_by_study: dict[str, str] = {}
    selected_by_study: dict[str, str] = {}
    for index, (subject, study) in enumerate(selected):
        batch_id = f"c3_batch_{index // requirements.studies_per_full_batch:03d}"
        batch_by_study[study] = batch_id
        selected_by_study[study] = subject

    objects_by_batch: dict[str, list[dict[str, Any]]] = {
        f"c3_batch_{index:03d}": [] for index in range(requirements.batch_count)
    }
    seen_keys: set[str] = set()
    seen_paths: set[str] = set()
    covered_studies: set[str] = set()
    source_total = 0
    for row in source_rows:
        subject = _canonical_id(row.get("subject_id"), "SOURCE_SUBJECT_ID_INVALID")
        study = _canonical_id(row.get("study_id"), "SOURCE_STUDY_ID_INVALID")
        if selected_by_study.get(study) != subject:
            raise OrchestrationError("SOURCE_OUTSIDE_SELECTED_OR_OWNERSHIP_CONFLICT")
        split = str(row.get("split"))
        if split != split_map[subject]:
            raise OrchestrationError("SOURCE_SPLIT_CONFLICT")
        normalized = _normalize_source_row(
            row, release=requirements.release, subject_id=subject, study_id=study
        )
        if normalized["source_object_key"] in seen_keys:
            raise OrchestrationError("DUPLICATE_PHYSICAL_SOURCE_KEY")
        if normalized["source_relative_path"] in seen_paths:
            raise OrchestrationError("DUPLICATE_SOURCE_LOCATOR")
        seen_keys.add(normalized["source_object_key"])
        seen_paths.add(normalized["source_relative_path"])
        batch_id = batch_by_study[study]
        declared_batch = row.get("production_batch")
        if declared_batch in (None, ""):
            raise OrchestrationError("SOURCE_BATCH_ASSIGNMENT_REQUIRED")
        if declared_batch != batch_id:
            raise OrchestrationError("DECLARED_BATCH_MEMBERSHIP_CHANGED")
        objects_by_batch[batch_id].append(
            {**normalized, "subject_id": subject, "study_id": study, "split": split}
        )
        covered_studies.add(study)
        source_total += normalized["size_bytes"]
    if len(seen_keys) != requirements.normalized_source_objects:
        raise OrchestrationError("SOURCE_OBJECT_COUNT_MISMATCH")
    if source_total != requirements.selected_source_bytes:
        raise OrchestrationError("SOURCE_BYTE_TOTAL_MISMATCH")
    if covered_studies != studies:
        raise OrchestrationError("SELECTED_STUDY_SOURCE_COVERAGE_MISMATCH")

    batches: list[dict[str, Any]] = []
    for ordinal in range(requirements.batch_count):
        batch_id = f"c3_batch_{ordinal:03d}"
        batch_studies = [
            {"subject_id": subject, "study_id": study, "split": split_map[subject]}
            for index, (subject, study) in enumerate(selected)
            if index // requirements.studies_per_full_batch == ordinal
        ]
        batch_objects = sorted(
            objects_by_batch[batch_id],
            key=lambda row: (int(row["subject_id"]), int(row["study_id"]), row["source_relative_path"]),
        )
        if not batch_studies or not batch_objects:
            raise OrchestrationError("EMPTY_PRODUCTION_BATCH")
        batches.append(
            {
                "batch_id": batch_id,
                "ordinal": ordinal,
                "n_studies": len(batch_studies),
                "n_subjects": len({row["subject_id"] for row in batch_studies}),
                "n_objects": len(batch_objects),
                "source_bytes": sum(int(row["size_bytes"]) for row in batch_objects),
                "study_membership_sha256": canonical_json_sha256(batch_studies),
                "source_membership_sha256": canonical_json_sha256(batch_objects),
                "studies": batch_studies,
                "objects": batch_objects,
            }
        )
    largest_batch = max(
        batches,
        key=lambda row: (
            int(row["source_bytes"]), int(row["n_objects"]), row["batch_id"]
        ),
    )
    plan = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v2",
        "contract_id": requirements.contract_id,
        "algorithm": "numeric_subject_then_numeric_study_contiguous_v1",
        "authority": normalized_authority,
        "cohort": {
            "release": requirements.release,
            "selected_studies": len(selected),
            "selected_subjects": len(subjects),
            "normalized_source_objects": len(seen_keys),
            "selected_source_bytes": source_total,
        },
        "largest_batch": {
            "batch_id": largest_batch["batch_id"],
            "n_objects": largest_batch["n_objects"],
            "source_bytes": largest_batch["source_bytes"],
        },
        "batches": batches,
    }
    validate_batch_plan(plan, requirements=requirements)
    return plan


def validate_batch_plan(
    plan: Mapping[str, Any], *, requirements: PlanRequirements, expected_sha256: str | None = None
) -> str:
    _require_exact_keys(plan, PLAN_KEYS, "BATCH_PLAN_TOP_LEVEL_SCHEMA_INVALID")
    if (
        plan.get("schema_version") != 2
        or plan.get("artifact_type") != "lvef_c3_restricted_immutable_batch_plan_v2"
        or plan.get("contract_id") != requirements.contract_id
        or plan.get("algorithm") != "numeric_subject_then_numeric_study_contiguous_v1"
    ):
        raise OrchestrationError("BATCH_PLAN_IDENTITY_INVALID")
    _validate_plan_authority(plan["authority"])
    cohort = plan.get("cohort")
    if cohort != {
        "release": requirements.release,
        "selected_studies": requirements.selected_studies,
        "selected_subjects": requirements.selected_subjects,
        "normalized_source_objects": requirements.normalized_source_objects,
        "selected_source_bytes": requirements.selected_source_bytes,
    }:
        raise OrchestrationError("BATCH_PLAN_COHORT_CONSTANT_MISMATCH")
    batches = plan.get("batches")
    if not isinstance(batches, list) or len(batches) != requirements.batch_count:
        raise OrchestrationError("BATCH_PLAN_COUNT_MISMATCH")
    all_subjects: set[str] = set()
    all_studies: set[str] = set()
    all_source_keys: set[str] = set()
    all_paths: set[str] = set()
    total_objects = 0
    total_bytes = 0
    for ordinal, batch in enumerate(batches):
        _require_exact_keys(batch, BATCH_KEYS, "BATCH_PLAN_BATCH_SCHEMA_INVALID")
        expected_batch = f"c3_batch_{ordinal:03d}"
        if batch.get("batch_id") != expected_batch or batch.get("ordinal") != ordinal:
            raise OrchestrationError("BATCH_PLAN_ORDER_CHANGED")
        studies = batch.get("studies")
        objects = batch.get("objects")
        if not isinstance(studies, list) or not isinstance(objects, list):
            raise OrchestrationError("BATCH_PLAN_MEMBER_LIST_INVALID")
        expected_studies = (
            requirements.final_batch_studies
            if ordinal == requirements.batch_count - 1
            else requirements.studies_per_full_batch
        )
        if len(studies) != expected_studies:
            raise OrchestrationError("BATCH_STUDY_COUNT_CHANGED")
        batch_subjects: set[str] = set()
        batch_studies: set[str] = set()
        ownership: dict[str, tuple[str, str]] = {}
        for row in studies:
            _require_exact_keys(row, STUDY_ENTRY_KEYS, "BATCH_STUDY_SCHEMA_INVALID")
            subject = _canonical_id(row["subject_id"], "BATCH_SUBJECT_INVALID")
            study = _canonical_id(row["study_id"], "BATCH_STUDY_INVALID")
            if row["split"] not in {"train", "val", "test"}:
                raise OrchestrationError("BATCH_SPLIT_INVALID")
            if subject in all_subjects or study in all_studies:
                raise OrchestrationError("STUDY_OR_SUBJECT_SPANS_BATCHES")
            batch_subjects.add(subject)
            batch_studies.add(study)
            ownership[study] = (subject, str(row["split"]))
            all_subjects.add(subject)
            all_studies.add(study)
        if len(batch_subjects) != len(studies) or len(batch_studies) != len(studies):
            raise OrchestrationError("BATCH_NOT_ONE_STUDY_PER_SUBJECT")
        if batch.get("study_membership_sha256") != canonical_json_sha256(studies):
            raise OrchestrationError("BATCH_STUDY_MEMBERSHIP_HASH_MISMATCH")
        object_bytes = 0
        for row in objects:
            expected_keys = SOURCE_OBJECT_KEYS | {"subject_id", "study_id", "split"}
            _require_exact_keys(row, frozenset(expected_keys), "BATCH_OBJECT_SCHEMA_INVALID")
            if ownership.get(str(row["study_id"])) != (
                str(row["subject_id"]), str(row["split"])
            ):
                raise OrchestrationError("BATCH_OBJECT_OWNERSHIP_MISMATCH")
            normalized_object = _normalize_source_row(
                row,
                release=requirements.release,
                subject_id=str(row["subject_id"]),
                study_id=str(row["study_id"]),
            )
            if any(row[key] != normalized_object[key] for key in SOURCE_OBJECT_KEYS):
                raise OrchestrationError("BATCH_OBJECT_NORMALIZATION_MISMATCH")
            if row["source_object_key"] in all_source_keys:
                raise OrchestrationError("DUPLICATE_PHYSICAL_SOURCE_KEY")
            if row["source_relative_path"] in all_paths:
                raise OrchestrationError("DUPLICATE_SOURCE_LOCATOR")
            all_source_keys.add(row["source_object_key"])
            all_paths.add(row["source_relative_path"])
            object_bytes += _positive_int(row["size_bytes"], "BATCH_OBJECT_SIZE_INVALID")
        if batch.get("source_membership_sha256") != canonical_json_sha256(objects):
            raise OrchestrationError("BATCH_SOURCE_MEMBERSHIP_HASH_MISMATCH")
        if (
            batch.get("n_studies") != len(studies)
            or batch.get("n_subjects") != len(batch_subjects)
            or batch.get("n_objects") != len(objects)
            or batch.get("source_bytes") != object_bytes
        ):
            raise OrchestrationError("BATCH_AGGREGATE_MISMATCH")
        total_objects += len(objects)
        total_bytes += object_bytes
    if (
        len(all_studies) != requirements.selected_studies
        or len(all_subjects) != requirements.selected_subjects
        or total_objects != requirements.normalized_source_objects
        or total_bytes != requirements.selected_source_bytes
    ):
        raise OrchestrationError("BATCH_PLAN_TOTAL_MISMATCH")
    _require_exact_keys(
        plan.get("largest_batch"),
        LARGEST_BATCH_KEYS,
        "LARGEST_BATCH_SCHEMA_INVALID",
    )
    observed_largest = max(
        batches,
        key=lambda row: (
            int(row["source_bytes"]), int(row["n_objects"]), row["batch_id"]
        ),
    )
    if plan["largest_batch"] != {
        "batch_id": observed_largest["batch_id"],
        "n_objects": observed_largest["n_objects"],
        "source_bytes": observed_largest["source_bytes"],
    }:
        raise OrchestrationError("LARGEST_BATCH_AUTHORITY_MISMATCH")
    digest = canonical_json_sha256(plan)
    if expected_sha256 is not None and digest != _require_sha256(
        expected_sha256, "EXPECTED_BATCH_PLAN_SHA256_INVALID"
    ):
        raise OrchestrationError("BATCH_PLAN_SHA256_MISMATCH")
    return digest


def aggregate_batch_plan(plan: Mapping[str, Any], *, requirements: PlanRequirements) -> dict[str, Any]:
    digest = validate_batch_plan(plan, requirements=requirements)
    return {
        "schema_version": 1,
        "status": "PASS_OFFLINE_IMMUTABLE_BATCH_PLAN",
        "selected_studies": requirements.selected_studies,
        "selected_subjects": requirements.selected_subjects,
        "normalized_source_objects": requirements.normalized_source_objects,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_count": requirements.batch_count,
        "batch_plan_sha256": digest,
        "largest_batch_source_objects": plan["largest_batch"]["n_objects"],
        "largest_batch_source_bytes": plan["largest_batch"]["source_bytes"],
        "batches": [
            {
                "production_batch": batch["batch_id"],
                "n_studies": batch["n_studies"],
                "n_subjects": batch["n_subjects"],
                "n_objects": batch["n_objects"],
                "source_bytes": batch["source_bytes"],
            }
            for batch in plan["batches"]
        ],
        "contains_identifiers": False,
        "contains_source_locators": False,
        "cloud_requests": 0,
        "object_bodies_downloaded": 0,
    }


def validate_runtime_authority(authority: Mapping[str, Any]) -> dict[str, str]:
    _require_exact_keys(authority, RUNTIME_AUTHORITY_KEYS, "RUNTIME_AUTHORITY_SCHEMA_INVALID")
    normalized = {key: str(authority[key]) for key in RUNTIME_AUTHORITY_KEYS}
    normalized["git_commit"] = _require_commit(normalized["git_commit"])
    for key in RUNTIME_AUTHORITY_KEYS - {"git_commit"}:
        normalized[key] = _require_sha256(
            normalized[key], f"RUNTIME_AUTHORITY_{key.upper()}_INVALID"
        )
    return dict(sorted(normalized.items()))


def validate_gcloud_runtime_authority(
    observed: Mapping[str, Any], *, expected_runtime_authority: Mapping[str, Any]
) -> dict[str, str]:
    """Bind the live token source to the exact resolver and binary in the plan."""
    keys = frozenset(
        {"gcloud_resolution_receipt_sha256", "gcloud_executable_sha256"}
    )
    _require_exact_keys(observed, keys, "GCLOUD_RUNTIME_AUTHORITY_SCHEMA_INVALID")
    normalized = {
        key: _require_sha256(
            observed[key], f"GCLOUD_RUNTIME_AUTHORITY_{key.upper()}_INVALID"
        )
        for key in keys
    }
    for key in keys:
        if normalized[key] != expected_runtime_authority.get(key):
            raise OrchestrationError("CURRENT_GCLOUD_AUTHORITY_MISMATCH")
    return dict(sorted(normalized.items()))


def derive_expected_runtime_authority(
    plan: Mapping[str, Any], *, requirements: PlanRequirements,
    contract: Mapping[str, Any], contract_path: Path, governing_commit: str,
    environment_receipt_sha256: str
) -> dict[str, str]:
    """Derive current authority from external files/arguments, never a ledger."""
    plan_sha = validate_batch_plan(plan, requirements=requirements)
    plan_authority = validate_plan_authority_against_contract(
        plan["authority"], contract=contract, contract_path=contract_path
    )
    commit = _require_commit(governing_commit)
    environment_sha = _require_sha256(
        environment_receipt_sha256, "CURRENT_ENVIRONMENT_RECEIPT_HASH_INVALID"
    )
    if plan_authority["git_commit"] != commit:
        raise OrchestrationError("CURRENT_GIT_AUTHORITY_PLAN_MISMATCH")
    if plan_authority["environment_receipt_sha256"] != environment_sha:
        raise OrchestrationError("CURRENT_ENVIRONMENT_AUTHORITY_PLAN_MISMATCH")
    return validate_runtime_authority(
        {**plan_authority, "batch_plan_sha256": plan_sha}
    )


def validate_ledger_against_current_runtime(
    ledger: Mapping[str, Any], *, plan: Mapping[str, Any],
    requirements: PlanRequirements, contract: Mapping[str, Any],
    contract_path: Path, governing_commit: str,
    environment_receipt_sha256: str, batch_id: str | None = None
) -> dict[str, str]:
    """Reject stale/foreign ledgers against independently supplied authority."""
    expected = derive_expected_runtime_authority(
        plan,
        requirements=requirements,
        contract=contract,
        contract_path=contract_path,
        governing_commit=governing_commit,
        environment_receipt_sha256=environment_receipt_sha256,
    )
    validate_resume_authority(ledger, expected_authority=expected)
    if batch_id is not None and set(ledger["batches"]) != {batch_id}:
        raise OrchestrationError("CURRENT_LEDGER_BATCH_SCOPE_MISMATCH")
    return expected


def initialize_resume_ledger(
    plan: Mapping[str, Any], *, requirements: PlanRequirements, attempt_id: str,
    authority: Mapping[str, Any], batch_ids: Sequence[str] | None = None
) -> dict[str, Any]:
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise OrchestrationError("ATTEMPT_ID_INVALID")
    plan_sha = validate_batch_plan(plan, requirements=requirements)
    runtime = validate_runtime_authority(authority)
    if runtime["batch_plan_sha256"] != plan_sha:
        raise OrchestrationError("LEDGER_BATCH_PLAN_AUTHORITY_MISMATCH")
    for key in PLAN_AUTHORITY_KEYS:
        if runtime[key] != plan["authority"][key]:
            raise OrchestrationError("LEDGER_PLAN_AUTHORITY_MISMATCH")
    planned_ids = [batch["batch_id"] for batch in plan["batches"]]
    selected_ids = planned_ids if batch_ids is None else list(batch_ids)
    if (
        not selected_ids
        or len(selected_ids) != len(set(selected_ids))
        or any(batch_id not in planned_ids for batch_id in selected_ids)
    ):
        raise OrchestrationError("LEDGER_BATCH_SCOPE_INVALID")
    return {
        "schema_version": 2,
        "artifact_type": "lvef_c3_resume_ledger_v2",
        "attempt_id": attempt_id,
        "status": "ACTIVE",
        "authority": runtime,
        "journal_sequence": 0,
        "journal_head_sha256": None,
        "batches": {
            batch["batch_id"]: {
                "state": "PLANNED",
                "resume_state": None,
                "completed_states": ["PLANNED"],
                "events": [],
                "download_attempts": {
                    row["source_object_key"]: 0 for row in batch["objects"]
                },
                "download_verification_receipts": {},
                "download_recovery_receipts": {},
                "download_manifest_sha256": None,
                "selected_batch_manifest_sha256": None,
            }
            for batch in plan["batches"]
            if batch["batch_id"] in selected_ids
        },
    }


def validate_resume_authority(
    ledger: Mapping[str, Any], *, expected_authority: Mapping[str, Any],
    attempt_id: str | None = None,
    expected_object_keys: Mapping[str, set[str]] | None = None,
) -> None:
    _require_exact_keys(ledger, LEDGER_KEYS, "LEDGER_SCHEMA_INVALID")
    if (
        ledger.get("schema_version") != 2
        or ledger.get("artifact_type") != "lvef_c3_resume_ledger_v2"
        or ledger.get("status") not in {"ACTIVE", "COMPLETE", "FAILED"}
    ):
        raise OrchestrationError("LEDGER_IDENTITY_INVALID")
    if attempt_id is not None and ledger.get("attempt_id") != attempt_id:
        raise OrchestrationError("LEDGER_ATTEMPT_MISMATCH")
    sequence = ledger.get("journal_sequence")
    head = ledger.get("journal_head_sha256")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or (sequence == 0 and head is not None)
        or (sequence > 0 and not SHA256_RE.fullmatch(str(head)))
    ):
        raise OrchestrationError("LEDGER_JOURNAL_AUTHORITY_INVALID")
    if validate_runtime_authority(ledger["authority"]) != validate_runtime_authority(expected_authority):
        raise OrchestrationError("RESUME_AUTHORITY_MISMATCH_NEW_ATTEMPT_REQUIRED")
    batches = ledger.get("batches")
    if not isinstance(batches, Mapping) or not batches:
        raise OrchestrationError("LEDGER_BATCHES_INVALID")
    for batch_id, batch in batches.items():
        if not BATCH_RE.fullmatch(str(batch_id)):
            raise OrchestrationError("LEDGER_BATCH_ID_INVALID")
        _require_exact_keys(batch, LEDGER_BATCH_KEYS, "LEDGER_BATCH_SCHEMA_INVALID")
        if batch.get("state") not in STATES:
            raise OrchestrationError("LEDGER_STATE_INVALID")
        completed = batch.get("completed_states")
        events = batch.get("events")
        attempts = batch.get("download_attempts")
        verification_receipts = batch.get("download_verification_receipts")
        recovery_receipts = batch.get("download_recovery_receipts")
        download_manifest_sha256 = batch.get("download_manifest_sha256")
        selected_batch_manifest_sha256 = batch.get("selected_batch_manifest_sha256")
        if (
            not isinstance(completed, list)
            or not completed
            or completed[0] != "PLANNED"
            or any(state not in STATE_SEQUENCE for state in completed)
            or not isinstance(events, list)
            or not isinstance(attempts, Mapping)
            or not isinstance(verification_receipts, Mapping)
            or not isinstance(recovery_receipts, Mapping)
            or any(not SHA256_RE.fullmatch(str(key)) for key in attempts)
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in attempts.values()
            )
            or any(key not in attempts for key in verification_receipts)
            or any(key not in verification_receipts for key in recovery_receipts)
            or any(
                not SHA256_RE.fullmatch(str(value))
                for value in verification_receipts.values()
            )
            or any(
                not SHA256_RE.fullmatch(str(value))
                for value in recovery_receipts.values()
            )
            or (
                download_manifest_sha256 is not None
                and not SHA256_RE.fullmatch(str(download_manifest_sha256))
            )
            or (
                selected_batch_manifest_sha256 is not None
                and not SHA256_RE.fullmatch(str(selected_batch_manifest_sha256))
            )
        ):
            raise OrchestrationError("LEDGER_BATCH_CONTENT_INVALID")
        if expected_object_keys is not None:
            if batch_id not in expected_object_keys or set(attempts) != set(
                expected_object_keys[batch_id]
            ):
                raise OrchestrationError("LEDGER_OBJECT_SCOPE_MISMATCH")
        simulated_state = "PLANNED"
        simulated_resume: str | None = None
        simulated_completed = ["PLANNED"]
        for event in events:
            if (
                not isinstance(event, Mapping)
                or set(event) != {"from_state", "to_state", "receipt_sha256"}
                or event.get("from_state") != simulated_state
                or not SHA256_RE.fullmatch(str(event.get("receipt_sha256")))
            ):
                raise OrchestrationError("LEDGER_EVENT_CHAIN_INVALID")
            target_state = str(event.get("to_state"))
            if simulated_state in TERMINAL_STATES or target_state not in STATES:
                raise OrchestrationError("LEDGER_EVENT_TRANSITION_INVALID")
            if simulated_state == "FAILED_RETRYABLE":
                if target_state != simulated_resume:
                    raise OrchestrationError("LEDGER_RETRY_TRANSITION_INVALID")
                simulated_resume = None
            elif target_state == "FAILED_RETRYABLE":
                simulated_resume = simulated_state
            elif target_state in {"FAILED_NONRETRYABLE", "QUARANTINED"}:
                pass
            else:
                try:
                    expected_next = STATE_SEQUENCE[STATE_SEQUENCE.index(simulated_state) + 1]
                except (ValueError, IndexError):
                    raise OrchestrationError("LEDGER_EVENT_TRANSITION_INVALID") from None
                if target_state != expected_next:
                    raise OrchestrationError("LEDGER_EVENT_TRANSITION_INVALID")
            simulated_state = target_state
            if target_state in STATE_SEQUENCE and target_state not in simulated_completed:
                simulated_completed.append(target_state)
        if (
            simulated_state != batch["state"]
            or simulated_resume != batch["resume_state"]
            or simulated_completed != completed
        ):
            raise OrchestrationError("LEDGER_DERIVED_STATE_MISMATCH")
    states = {batch["state"] for batch in batches.values()}
    expected_status = (
        "COMPLETE"
        if states == {"FINALIZED"}
        else "FAILED"
        if states & {"FAILED_NONRETRYABLE", "QUARANTINED"}
        else "ACTIVE"
    )
    if ledger["status"] != expected_status:
        raise OrchestrationError("LEDGER_STATUS_MISMATCH")


def _receipt_sha(receipt: Mapping[str, Any]) -> str:
    return canonical_json_sha256(receipt)


def apply_transition(
    ledger: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    _require_exact_keys(receipt, RECEIPT_KEYS, "TRANSITION_RECEIPT_SCHEMA_INVALID")
    if (
        receipt.get("schema_version") != 2
        or receipt.get("receipt_type") != "lvef_c3_state_transition_v2"
        or receipt.get("status") != "PASS"
        or receipt.get("attempt_id") != ledger["attempt_id"]
    ):
        raise OrchestrationError("TRANSITION_RECEIPT_IDENTITY_INVALID")
    if validate_runtime_authority(receipt["authority"]) != ledger["authority"]:
        raise OrchestrationError("TRANSITION_AUTHORITY_MISMATCH")
    batch_id = str(receipt.get("batch_id"))
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("TRANSITION_BATCH_NOT_PLANNED")
    from_state = str(receipt.get("from_state"))
    to_state = str(receipt.get("to_state"))
    if from_state not in STATES or to_state not in STATES:
        raise OrchestrationError("TRANSITION_STATE_UNKNOWN")
    batch = ledger["batches"][batch_id]
    if batch["state"] != from_state:
        raise OrchestrationError("TRANSITION_FROM_STATE_MISMATCH")
    if from_state in TERMINAL_STATES:
        raise OrchestrationError("TERMINAL_STATE_TRANSITION_PROHIBITED")
    if from_state == "FAILED_RETRYABLE":
        if to_state != batch["resume_state"]:
            raise OrchestrationError("RETRY_MUST_RETURN_TO_LAST_STABLE_STATE")
    elif to_state in {"FAILED_RETRYABLE", "FAILED_NONRETRYABLE", "QUARANTINED"}:
        pass
    else:
        try:
            expected_next = STATE_SEQUENCE[STATE_SEQUENCE.index(from_state) + 1]
        except (ValueError, IndexError):
            raise OrchestrationError("TRANSITION_SEQUENCE_INVALID") from None
        if to_state != expected_next:
            raise OrchestrationError("STATE_SKIP_OR_REGRESSION_PROHIBITED")
    inputs = receipt.get("input_receipt_sha256")
    if not isinstance(inputs, list) or not inputs or any(
        not SHA256_RE.fullmatch(str(value)) for value in inputs
    ):
        raise OrchestrationError("TRANSITION_INPUT_RECEIPTS_INVALID")
    _require_sha256(receipt.get("output_manifest_sha256"), "TRANSITION_OUTPUT_HASH_INVALID")
    digest = _receipt_sha(receipt)
    if any(event.get("receipt_sha256") == digest for event in batch["events"]):
        raise OrchestrationError("TRANSITION_RECEIPT_REPLAYED")
    required_predecessor = (
        batch["events"][-1]["receipt_sha256"]
        if batch["events"]
        else ledger["authority"]["batch_plan_sha256"]
    )
    if required_predecessor not in inputs:
        raise OrchestrationError("TRANSITION_RECEIPT_CHAIN_BROKEN")
    updated = copy.deepcopy(ledger)
    target = updated["batches"][batch_id]
    if to_state == "FAILED_RETRYABLE":
        target["resume_state"] = from_state
    elif from_state == "FAILED_RETRYABLE":
        target["resume_state"] = None
    target["state"] = to_state
    if to_state in STATE_SEQUENCE and to_state not in target["completed_states"]:
        target["completed_states"].append(to_state)
    target["events"].append(
        {"from_state": from_state, "to_state": to_state, "receipt_sha256": digest}
    )
    states = {row["state"] for row in updated["batches"].values()}
    if states == {"FINALIZED"}:
        updated["status"] = "COMPLETE"
    elif states & {"FAILED_NONRETRYABLE", "QUARANTINED"}:
        updated["status"] = "FAILED"
    return updated


def register_download_attempt(
    ledger: Mapping[str, Any], *, batch_id: str, source_object_key: str,
    maximum_attempts: int
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("DOWNLOAD_BATCH_NOT_PLANNED")
    batch = ledger["batches"][batch_id]
    if batch["state"] != "DOWNLOAD_IN_PROGRESS":
        raise OrchestrationError("DOWNLOAD_REQUEST_OUTSIDE_DOWNLOAD_STATE")
    if source_object_key not in batch["download_attempts"]:
        raise OrchestrationError("DOWNLOAD_OBJECT_NOT_IN_FROZEN_BATCH")
    if maximum_attempts <= 0:
        raise OrchestrationError("DOWNLOAD_MAXIMUM_ATTEMPTS_INVALID")
    observed = batch["download_attempts"][source_object_key]
    if observed >= maximum_attempts:
        raise OrchestrationError("DOWNLOAD_REQUEST_BUDGET_EXHAUSTED")
    updated = copy.deepcopy(ledger)
    updated["batches"][batch_id]["download_attempts"][source_object_key] += 1
    return updated


def mark_download_verified(
    ledger: Mapping[str, Any], *, batch_id: str, source_object_key: str,
    verification_receipt_sha256: str,
    recovery_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    receipt_sha = _require_sha256(
        verification_receipt_sha256, "DOWNLOAD_VERIFICATION_RECEIPT_HASH_INVALID"
    )
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("DOWNLOAD_BATCH_NOT_PLANNED")
    batch = ledger["batches"][batch_id]
    if batch["state"] != "DOWNLOAD_IN_PROGRESS":
        raise OrchestrationError("DOWNLOAD_VERIFICATION_OUTSIDE_DOWNLOAD_STATE")
    if source_object_key not in batch["download_attempts"]:
        raise OrchestrationError("DOWNLOAD_OBJECT_NOT_IN_FROZEN_BATCH")
    if batch["download_attempts"][source_object_key] <= 0:
        raise OrchestrationError("DOWNLOAD_VERIFICATION_WITHOUT_REQUEST")
    observed = batch["download_verification_receipts"].get(source_object_key)
    if observed is not None:
        if observed != receipt_sha:
            raise OrchestrationError("DOWNLOAD_VERIFICATION_RECEIPT_CHANGED")
        if recovery_receipt_sha256 is None:
            return copy.deepcopy(ledger)
        recovery_sha = _require_sha256(
            recovery_receipt_sha256, "DOWNLOAD_RECOVERY_RECEIPT_HASH_INVALID"
        )
        observed_recovery = batch["download_recovery_receipts"].get(source_object_key)
        if observed_recovery not in (None, recovery_sha):
            raise OrchestrationError("DOWNLOAD_RECOVERY_RECEIPT_CHANGED")
        updated = copy.deepcopy(ledger)
        updated["batches"][batch_id]["download_recovery_receipts"][source_object_key] = recovery_sha
        return updated
    updated = copy.deepcopy(ledger)
    updated["batches"][batch_id]["download_verification_receipts"][source_object_key] = receipt_sha
    if recovery_receipt_sha256 is not None:
        recovery_sha = _require_sha256(
            recovery_receipt_sha256, "DOWNLOAD_RECOVERY_RECEIPT_HASH_INVALID"
        )
        updated["batches"][batch_id]["download_recovery_receipts"][source_object_key] = recovery_sha
    return updated


def mark_download_manifest(
    ledger: Mapping[str, Any], *, batch_id: str, manifest_sha256: str
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    digest = _require_sha256(manifest_sha256, "DOWNLOAD_MANIFEST_HASH_INVALID")
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("DOWNLOAD_BATCH_NOT_PLANNED")
    batch = ledger["batches"][batch_id]
    if set(batch["download_verification_receipts"]) != set(batch["download_attempts"]):
        raise OrchestrationError("DOWNLOAD_MANIFEST_BEFORE_ALL_OBJECTS_VERIFIED")
    if batch["download_manifest_sha256"] not in (None, digest):
        raise OrchestrationError("DOWNLOAD_MANIFEST_CHANGED")
    updated = copy.deepcopy(ledger)
    updated["batches"][batch_id]["download_manifest_sha256"] = digest
    return updated


def mark_selected_batch_manifest(
    ledger: Mapping[str, Any], *, batch_id: str, manifest_sha256: str
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    digest = _require_sha256(
        manifest_sha256, "SELECTED_BATCH_MANIFEST_HASH_INVALID"
    )
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("SELECTED_BATCH_NOT_PLANNED")
    observed = ledger["batches"][batch_id]["selected_batch_manifest_sha256"]
    if observed not in (None, digest):
        raise OrchestrationError("SELECTED_BATCH_MANIFEST_CHANGED")
    updated = copy.deepcopy(ledger)
    updated["batches"][batch_id]["selected_batch_manifest_sha256"] = digest
    return updated


def classify_download_failure(
    failure_class: str, *, attempts_used: int, maximum_attempts: int
) -> str:
    if maximum_attempts <= 0 or attempts_used <= 0 or attempts_used > maximum_attempts:
        raise OrchestrationError("DOWNLOAD_ATTEMPT_COUNT_INVALID")
    if failure_class in NONRETRYABLE_FAILURE_CLASSES:
        return "FAILED_NONRETRYABLE"
    if failure_class not in RETRYABLE_FAILURE_CLASSES:
        raise OrchestrationError("DOWNLOAD_FAILURE_CLASS_UNKNOWN")
    return "FAILED_RETRYABLE" if attempts_used < maximum_attempts else "FAILED_NONRETRYABLE"


def retry_backoff_seconds(
    attempts_used: int, *, initial_seconds: int, maximum_seconds: int
) -> int:
    if (
        not isinstance(attempts_used, int)
        or isinstance(attempts_used, bool)
        or attempts_used <= 0
        or not isinstance(initial_seconds, int)
        or isinstance(initial_seconds, bool)
        or initial_seconds <= 0
        or not isinstance(maximum_seconds, int)
        or isinstance(maximum_seconds, bool)
        or maximum_seconds < initial_seconds
    ):
        raise OrchestrationError("DOWNLOAD_RETRY_BACKOFF_INVALID")
    return min(initial_seconds * (2 ** (attempts_used - 1)), maximum_seconds)


def _parse_utc(value: Any, code: str) -> datetime:
    text = str(value)
    if not text.endswith("Z"):
        raise OrchestrationError(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise OrchestrationError(code) from None
    if parsed.tzinfo != timezone.utc:
        raise OrchestrationError(code)
    return parsed


def validate_body_transfer_authorization(
    receipt: Mapping[str, Any], *, ledger: Mapping[str, Any],
    plan: Mapping[str, Any], batch_id: str, maximum_attempts_per_object: int,
    expected_launch_authority_sha256: str,
    now: datetime | None = None
) -> None:
    _require_exact_keys(
        receipt, BODY_AUTHORIZATION_KEYS, "BODY_TRANSFER_AUTHORIZATION_SCHEMA_INVALID"
    )
    if (
        receipt.get("schema_version") != 2
        or receipt.get("receipt_type") != "lvef_c3_body_transfer_authorization_v2"
        or receipt.get("status") != "AUTHORIZED_C3_DICOM_BODY_TRANSFER"
        or receipt.get("attempt_id") != ledger.get("attempt_id")
        or receipt.get("owner_authorization_recorded") is not True
        or receipt.get("body_download_only") is not True
        or receipt.get("scientific_actions_authorized") is not False
        or receipt.get("launch_authority_sha256")
        != _require_sha256(
            expected_launch_authority_sha256,
            "EXPECTED_LAUNCH_AUTHORITY_HASH_INVALID",
        )
    ):
        raise OrchestrationError("BODY_TRANSFER_AUTHORIZATION_IDENTITY_INVALID")
    batch_ids = receipt.get("batch_ids")
    scope = receipt.get("scope")
    planned_ids = [row["batch_id"] for row in plan["batches"]]
    if (
        not isinstance(batch_ids, list)
        or not batch_ids
        or len(batch_ids) != len(set(batch_ids))
        or any(value not in planned_ids for value in batch_ids)
        or batch_id not in batch_ids
    ):
        raise OrchestrationError("BODY_TRANSFER_AUTHORIZATION_BATCH_SCOPE_INVALID")
    if scope == "FIRST_BATCH_ONLY":
        if batch_ids != ["c3_batch_000"] or batch_id != "c3_batch_000":
            raise OrchestrationError("FIRST_BATCH_AUTHORIZATION_SCOPE_INVALID")
    elif scope == "REMAINING_BATCHES":
        if "c3_batch_000" in batch_ids:
            raise OrchestrationError("REMAINING_BATCH_AUTHORIZATION_INCLUDES_FIRST")
    else:
        raise OrchestrationError("BODY_TRANSFER_AUTHORIZATION_SCOPE_UNKNOWN")
    if receipt.get("authority_sha256") != canonical_json_sha256(ledger["authority"]):
        raise OrchestrationError("BODY_TRANSFER_AUTHORITY_HASH_MISMATCH")
    if receipt.get("batch_plan_sha256") != ledger["authority"]["batch_plan_sha256"]:
        raise OrchestrationError("BODY_TRANSFER_BATCH_PLAN_HASH_MISMATCH")
    authorized_objects = sum(
        int(row["n_objects"]) for row in plan["batches"] if row["batch_id"] in batch_ids
    )
    if receipt.get("maximum_requests") != authorized_objects * maximum_attempts_per_object:
        raise OrchestrationError("BODY_TRANSFER_REQUEST_BUDGET_INVALID")
    issued = _parse_utc(receipt.get("issued_at_utc"), "BODY_TRANSFER_ISSUED_TIME_INVALID")
    expires = _parse_utc(receipt.get("expires_at_utc"), "BODY_TRANSFER_EXPIRY_INVALID")
    current = now or datetime.now(timezone.utc)
    if issued >= expires or current < issued or current >= expires:
        raise OrchestrationError("BODY_TRANSFER_AUTHORIZATION_NOT_CURRENT")


def validate_direct_manifest_download_scope(
    *,
    manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    batch_id: str,
    maximum_attempts_per_object: int,
    expected_launch_authority_sha256: str,
) -> None:
    """Validate a direct-manifest exact-five download without a grant receipt.

    This compatibility entrypoint is intentionally narrow.  It lets the
    minimal one-job adapter reuse the production downloader while treating the
    canonical sealed manifest, its manifest-bound plan, and the manifest-bound
    production resume ledger as the only owner-authorized
    scope. It creates no packet or grant.
    """

    body = manifest.get("manifest")
    if not isinstance(body, Mapping):
        raise OrchestrationError("DIRECT_MANIFEST_DOWNLOAD_SCOPE_INVALID")
    objects = [
        (study, item)
        for study in body.get("studies", [])
        if isinstance(study, Mapping)
        for item in study.get("objects", [])
        if isinstance(item, Mapping)
    ]
    try:
        plan_authority = plan["authority"]
        plan_batches = plan["batches"]
    except (KeyError, TypeError) as exc:
        raise OrchestrationError("DIRECT_MANIFEST_DOWNLOAD_SCOPE_INVALID") from exc
    plan_sha = canonical_json_sha256(plan)
    try:
        from lvef_c3_canary_manifest import validate_manifest

        canonical_manifest = validate_manifest(dict(manifest))
    except Exception as exc:
        raise OrchestrationError("DIRECT_MANIFEST_DOWNLOAD_SCOPE_INVALID") from exc
    if canonical_manifest != dict(manifest):
        raise OrchestrationError("DIRECT_MANIFEST_DOWNLOAD_SCOPE_INVALID")
    manifest_objects = sorted(
        [
            (
                str(item.get("source_object_key")),
                str(item.get("source_relative_path")),
                int(item.get("size_bytes", 0)),
                str(item.get("generation")),
                str(item.get("md5_base64")),
                str(item.get("crc32c_base64")),
                str(study.get("subject_id")),
                str(study.get("study_id")),
                str(study.get("split")),
            )
            for study, item in objects
        ]
    )
    plan_objects = sorted(
        [
            (
                str(item.get("source_object_key")),
                str(item.get("source_relative_path")),
                int(item.get("size_bytes", 0)),
                str(item.get("generation")),
                str(item.get("md5_base64")),
                str(item.get("crc32c_base64")),
                str(item.get("subject_id")),
                str(item.get("study_id")),
                str(item.get("split")),
            )
            for batch in plan_batches
            for item in batch.get("objects", [])
            if isinstance(item, Mapping)
        ]
    )
    if (
        manifest.get("manifest_sha256")
        != plan_authority.get("selected_manifest_sha256")
        or ledger.get("authority", {}).get("batch_plan_sha256") != plan_sha
        or batch_id != "c3_batch_000"
        or [row.get("batch_id") for row in plan_batches]
        != ["c3_batch_000"]
        or body.get("study_count") != 5
        or body.get("subject_count") != 5
        or body.get("split") != "train"
        or body.get("complete_object_membership") is not True
        or not 5 <= len(objects) <= 750
        or len(objects) != body.get("expected_object_count")
        or manifest_objects != plan_objects
        or sum(int(row.get("size_bytes", 0)) for _, row in objects)
        != body.get("expected_byte_total")
        or not 1 <= int(body.get("expected_byte_total", 0)) <= 5_000_000_000
        or maximum_attempts_per_object < 1
        or maximum_attempts_per_object > 10
        or _require_sha256(
            expected_launch_authority_sha256,
            "EXPECTED_LAUNCH_AUTHORITY_HASH_INVALID",
        )
        != expected_launch_authority_sha256
    ):
        raise OrchestrationError("DIRECT_MANIFEST_DOWNLOAD_SCOPE_INVALID")


def validate_direct_full_download_scope(
    *,
    launch_authority: Mapping[str, Any],
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    requirements: PlanRequirements,
    batch_id: str,
    maximum_attempts_per_object: int,
    expected_launch_authority_sha256: str,
    test_only_synthetic_full_scope: bool = False,
) -> None:
    """Validate one batch against the single full-cohort launch authority."""

    _require_exact_keys(
        launch_authority,
        DIRECT_FULL_LAUNCH_KEYS,
        "DIRECT_FULL_LAUNCH_AUTHORITY_SCHEMA_INVALID",
    )
    plan_sha = validate_batch_plan(plan, requirements=requirements)
    planned_ids = [str(row["batch_id"]) for row in plan["batches"]]
    if not isinstance(test_only_synthetic_full_scope, bool):
        raise OrchestrationError("DIRECT_FULL_TEST_BOUNDARY_INVALID")
    production_scope = (
        requirements.release == "mimic-iv-echo/1.0"
        and requirements.selected_studies
        == EXPECTED_PRODUCTION["selected_studies"]
        and requirements.selected_subjects
        == EXPECTED_PRODUCTION["selected_subjects"]
        and requirements.normalized_source_objects
        == EXPECTED_PRODUCTION["normalized_source_objects"]
        and requirements.selected_source_bytes
        == EXPECTED_PRODUCTION["selected_source_bytes"]
        and requirements.batch_count == EXPECTED_PRODUCTION["batch_count"]
        and requirements.studies_per_full_batch
        == EXPECTED_PRODUCTION["studies_per_full_batch"]
        and requirements.final_batch_studies
        == EXPECTED_PRODUCTION["final_batch_studies"]
        and requirements.contract_id == EXPECTED_FULL_CONTRACT_ID
        and plan["authority"]["selected_manifest_sha256"]
        == EXPECTED_SELECTED_MANIFEST_SHA256
        and plan["authority"]["selected_source_manifest_sha256"]
        == EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
        and plan["authority"]["split_map_sha256"]
        == EXPECTED_SPLIT_MAP_SHA256
        and plan["authority"]["checkpoint_sha256"]
        == EXPECTED_CHECKPOINT_SHA256
    )
    # The miniature route is deliberately an explicit, exact test contract.
    # It is not a configurable smaller production launch authority.
    synthetic_scope = (
        test_only_synthetic_full_scope
        and requirements.release == "mimic-iv-echo/1.0"
        and requirements.selected_studies == 4
        and requirements.selected_subjects == 4
        and requirements.normalized_source_objects == 4
        and 1 <= requirements.selected_source_bytes <= 5_000_000_000
        and requirements.batch_count == 2
        and requirements.studies_per_full_batch == 2
        and requirements.final_batch_studies == 2
        and requirements.contract_id == TEST_ONLY_FULL_CONTRACT_ID
    )
    if (
        (test_only_synthetic_full_scope and not synthetic_scope)
        or (not test_only_synthetic_full_scope and not production_scope)
        or launch_authority.get("schema_version") != 1
        or launch_authority.get("artifact_type")
        != "lvef_c3_full_selected_cohort_launch_authority_v1"
        or launch_authority.get("status")
        != "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION"
        or launch_authority.get("governing_commit")
        != plan["authority"]["git_commit"]
        or launch_authority.get("batch_plan_sha256") != plan_sha
        or launch_authority.get("selected_manifest_sha256")
        != plan["authority"]["selected_manifest_sha256"]
        or launch_authority.get("selected_source_manifest_sha256")
        != plan["authority"]["selected_source_manifest_sha256"]
        or launch_authority.get("split_map_sha256")
        != plan["authority"]["split_map_sha256"]
        or launch_authority.get("checkpoint_sha256")
        != plan["authority"]["checkpoint_sha256"]
        or launch_authority.get("selected_studies")
        != requirements.selected_studies
        or launch_authority.get("selected_subjects")
        != requirements.selected_subjects
        or launch_authority.get("normalized_source_objects")
        != requirements.normalized_source_objects
        or launch_authority.get("selected_source_bytes")
        != requirements.selected_source_bytes
        or launch_authority.get("batch_count") != requirements.batch_count
        or not isinstance(launch_authority.get("expected_no_cine_studies"), int)
        or isinstance(launch_authority.get("expected_no_cine_studies"), bool)
        or launch_authority.get("expected_no_cine_studies")
        != (1 if test_only_synthetic_full_scope else 5)
        or launch_authority.get("maximum_scheduler_submissions") != 2
        or launch_authority.get("array_task_range")
        != f"1-{requirements.batch_count}"
        or launch_authority.get("array_max_concurrency") != 1
        or launch_authority.get("raw_dicom_deletion_authorized") is not False
        or launch_authority.get(
            "extracted_cache_retirement_authorized_after_preservation"
        )
        is not True
        or launch_authority.get("model_fitting_authorized") is not False
        or launch_authority.get("prediction_authorized") is not False
        or launch_authority.get("confirmatory_performance_access_authorized")
        is not False
        or canonical_json_sha256(launch_authority)
        != _require_sha256(
            expected_launch_authority_sha256,
            "EXPECTED_LAUNCH_AUTHORITY_HASH_INVALID",
        )
        or ledger.get("authority", {}).get("batch_plan_sha256") != plan_sha
        or batch_id not in planned_ids
        or batch_id not in ledger.get("batches", {})
        or maximum_attempts_per_object != 5
    ):
        raise OrchestrationError("DIRECT_FULL_DOWNLOAD_SCOPE_INVALID")


def validate_private_billing_environment(
    environment_variable: str, *, argv: Sequence[str], environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    source = os.environ if environ is None else environ
    value = source.get(environment_variable, "")
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise OrchestrationError("PRIVATE_BILLING_PROJECT_NOT_SET")
    if any(value in argument for argument in argv):
        raise OrchestrationError("PRIVATE_BILLING_PROJECT_EXPOSED_IN_ARGV")
    return {
        "status": "PASS_PRIVATE_ENVIRONMENT_ONLY",
        "environment_variable": environment_variable,
        "value_present": True,
        "value_returned": False,
        "value_in_argv": False,
    }


def expectation_from_plan_object(row: Mapping[str, Any]) -> DownloadExpectation:
    return DownloadExpectation(
        source_object_key=_require_sha256(row.get("source_object_key"), "DOWNLOAD_SOURCE_KEY_INVALID"),
        source_relative_path=str(row.get("source_relative_path")),
        size_bytes=_positive_int(row.get("size_bytes"), "DOWNLOAD_EXPECTED_SIZE_INVALID"),
        generation=str(row.get("generation")),
        md5_base64=_base64_digest(row.get("md5_base64"), 16, "DOWNLOAD_EXPECTED_MD5_INVALID"),
        crc32c_base64=_base64_digest(row.get("crc32c_base64"), 4, "DOWNLOAD_EXPECTED_CRC_INVALID"),
    )


def planned_partial_name(expectation: DownloadExpectation, attempt_id: str) -> str:
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise OrchestrationError("ATTEMPT_ID_INVALID")
    return f"{expectation.source_object_key}.{attempt_id}.partial"


def planned_final_name(expectation: DownloadExpectation) -> str:
    return f"{expectation.source_object_key}.dcm"


_CRC32C_TABLE: tuple[int, ...] | None = None


def _crc32c(data: bytes) -> int:
    global _CRC32C_TABLE
    if _CRC32C_TABLE is None:
        values: list[int] = []
        polynomial = 0x82F63B78
        for index in range(256):
            value = index
            for _ in range(8):
                value = (value >> 1) ^ polynomial if value & 1 else value >> 1
            values.append(value)
        _CRC32C_TABLE = tuple(values)
    checksum = 0xFFFFFFFF
    for byte in data:
        checksum = _CRC32C_TABLE[(checksum ^ byte) & 0xFF] ^ (checksum >> 8)
    return checksum ^ 0xFFFFFFFF


def _crc32c_base64(data: bytes) -> str:
    return base64.b64encode(_crc32c(data).to_bytes(4, "big")).decode("ascii")


def verify_downloaded_partial(
    expectation: DownloadExpectation, *, partial_path: Path,
    transfer_receipt: Mapping[str, Any], attempt_id: str,
    digest_provider: Callable[[Path, str], Mapping[str, Any]] = _inprocess_digest_provider,
) -> dict[str, Any]:
    expected_receipt_keys = {
        "schema_version",
        "status",
        "source_object_key",
        "size_bytes",
        "generation",
        "md5_base64",
        "crc32c_base64",
        "media_request_count",
        "object_body_bytes_read",
        "resume_offset_bytes",
        "response_body_bytes_read",
        "final_partial_size_bytes",
        "content_range_validated",
    }
    if set(transfer_receipt) != expected_receipt_keys:
        raise OrchestrationError("TRANSFER_RECEIPT_SCHEMA_INVALID")
    if (
        transfer_receipt.get("schema_version") != 2
        or transfer_receipt.get("status") != "BODY_TRANSFER_COMPLETE_UNVERIFIED"
        or transfer_receipt.get("source_object_key") != expectation.source_object_key
    ):
        raise OrchestrationError("TRANSFER_RECEIPT_IDENTITY_INVALID")
    if partial_path.name != planned_partial_name(expectation, attempt_id):
        raise OrchestrationError("STALE_OR_FOREIGN_DOWNLOAD_PARTIAL")
    digests = digest_provider(partial_path, expectation.source_object_key)
    if not digests["size_bytes"]:
        raise OrchestrationError("ZERO_BYTE_DOWNLOAD")
    observed_size = digests["size_bytes"]
    observed_md5 = digests["md5_base64"]
    observed_crc = digests["crc32c_base64"]
    receipt_identity = (
        transfer_receipt.get("size_bytes"),
        str(transfer_receipt.get("generation")),
        transfer_receipt.get("md5_base64"),
        transfer_receipt.get("crc32c_base64"),
    )
    expected_identity = (
        expectation.size_bytes,
        expectation.generation,
        expectation.md5_base64,
        expectation.crc32c_base64,
    )
    if receipt_identity != expected_identity:
        raise OrchestrationError("TRANSFER_REMOTE_IDENTITY_CHANGED")
    if transfer_receipt.get("media_request_count") != 1:
        raise OrchestrationError("TRANSFER_MEDIA_REQUEST_COUNT_INVALID")
    offset = transfer_receipt.get("resume_offset_bytes")
    response_bytes = transfer_receipt.get("response_body_bytes_read")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(response_bytes, int)
        or isinstance(response_bytes, bool)
        or response_bytes <= 0
        or transfer_receipt.get("object_body_bytes_read") != response_bytes
        or transfer_receipt.get("final_partial_size_bytes") != observed_size
        or offset + response_bytes != observed_size
        or transfer_receipt.get("content_range_validated") is not (offset > 0)
    ):
        raise OrchestrationError("TRANSFER_BODY_BYTE_ACCOUNTING_INVALID")
    if observed_size != expectation.size_bytes:
        raise OrchestrationError("DOWNLOAD_SIZE_MISMATCH")
    if observed_md5 != expectation.md5_base64:
        raise OrchestrationError("DOWNLOAD_MD5_MISMATCH")
    if observed_crc != expectation.crc32c_base64:
        raise OrchestrationError("DOWNLOAD_CRC32C_MISMATCH")
    return {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": expectation.source_object_key,
        "size_bytes": observed_size,
        "generation": expectation.generation,
        "md5_base64": observed_md5,
        "crc32c_base64": observed_crc,
        "local_sha256": digests["sha256"],
        "file_device": digests["file_device"],
        "file_inode": digests["file_inode"],
        "file_mtime_ns": digests["file_mtime_ns"],
        "digest_backend": digests["backend"],
        "digest_chunk_size_bytes": digests["chunk_size_bytes"],
    }


def atomic_finalize_verified_file(
    *, partial_path: Path, final_path: Path, verification: Mapping[str, Any]
) -> None:
    if verification.get("status") != "PASS_DOWNLOAD_VERIFICATION":
        raise OrchestrationError("DOWNLOAD_NOT_VERIFIED_FOR_FINALIZATION")
    if final_path.exists() or final_path.is_symlink():
        raise OrchestrationError("FINAL_DOWNLOAD_ALREADY_EXISTS_NO_CLOBBER")
    metadata = partial_path.lstat()
    if (
        stat.S_ISREG(metadata.st_mode) is not True
        or metadata.st_size != verification.get("size_bytes")
        or metadata.st_dev != verification.get("file_device")
        or metadata.st_ino != verification.get("file_inode")
        or metadata.st_mtime_ns != verification.get("file_mtime_ns")
        or verification.get("digest_backend")
        not in {"google_crc32c_c", "google_crc32c_c_external_worker_v1"}
    ):
        raise OrchestrationError("PARTIAL_CHANGED_AFTER_VERIFICATION")
    partial_parent = partial_path.parent.resolve(strict=True)
    final_parent = final_path.parent.resolve(strict=True)
    if partial_path.parent.is_symlink() or final_path.parent.is_symlink():
        raise OrchestrationError("ATOMIC_FINALIZATION_PARENT_SYMLINK_PROHIBITED")
    if partial_parent.stat().st_dev != final_parent.stat().st_dev:
        raise OrchestrationError("ATOMIC_FINALIZATION_REQUIRES_SAME_FILESYSTEM")
    try:
        os.link(partial_path, final_path, follow_symlinks=False)
    except FileExistsError as exc:
        raise OrchestrationError("FINAL_DOWNLOAD_ALREADY_EXISTS_NO_CLOBBER") from exc
    except OSError as exc:
        raise OrchestrationError("ATOMIC_FINALIZATION_LINK_FAILED") from exc
    try:
        partial_path.unlink()
    except OSError as exc:
        raise OrchestrationError("ATOMIC_FINALIZATION_PARTIAL_UNLINK_FAILED") from exc
    final_metadata = final_path.lstat()
    if (
        final_metadata.st_dev != metadata.st_dev
        or final_metadata.st_ino != metadata.st_ino
        or final_metadata.st_size != metadata.st_size
        or final_metadata.st_mtime_ns != metadata.st_mtime_ns
    ):
        raise OrchestrationError("ATOMIC_FINALIZATION_IDENTITY_CHANGED")


def _verification_from_exact_final(
    expectation: DownloadExpectation, *, final_path: Path,
    digest_provider: Callable[[Path, str], Mapping[str, Any]] = _inprocess_digest_provider,
) -> dict[str, Any]:
    digests = digest_provider(final_path, expectation.source_object_key)
    observed_size = digests["size_bytes"]
    observed_md5 = digests["md5_base64"]
    observed_crc = digests["crc32c_base64"]
    if not observed_size:
        raise OrchestrationError("RECOVERY_FINAL_ZERO_BYTE")
    if observed_size != expectation.size_bytes:
        raise OrchestrationError("RECOVERY_FINAL_SIZE_MISMATCH")
    if observed_md5 != expectation.md5_base64:
        raise OrchestrationError("RECOVERY_FINAL_MD5_MISMATCH")
    if observed_crc != expectation.crc32c_base64:
        raise OrchestrationError("RECOVERY_FINAL_CRC32C_MISMATCH")
    return {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": expectation.source_object_key,
        "size_bytes": observed_size,
        "generation": expectation.generation,
        "md5_base64": observed_md5,
        "crc32c_base64": observed_crc,
        "local_sha256": digests["sha256"],
        "file_device": digests["file_device"],
        "file_inode": digests["file_inode"],
        "file_mtime_ns": digests["file_mtime_ns"],
        "digest_backend": digests["backend"],
        "digest_chunk_size_bytes": digests["chunk_size_bytes"],
    }


def _validate_recovery_receipt(
    receipt: Mapping[str, Any], *, expected_common: Mapping[str, Any]
) -> None:
    _require_exact_keys(
        receipt, RECOVERY_RECEIPT_KEYS, "DOWNLOAD_RECOVERY_RECEIPT_SCHEMA_INVALID"
    )
    recovery_class = receipt.get("recovery_class")
    if recovery_class not in {
        "FINAL_WITHOUT_VERIFICATION_RECEIPT",
        "VERIFICATION_RECEIPT_WITHOUT_LEDGER_BINDING",
    }:
        raise OrchestrationError("DOWNLOAD_RECOVERY_CLASS_INVALID")
    for key, value in expected_common.items():
        if receipt.get(key) != value:
            raise OrchestrationError("DOWNLOAD_RECOVERY_RECEIPT_MISMATCH")


def recover_unbound_download_transaction(
    ledger: Mapping[str, Any], *, batch_id: str,
    expectation: DownloadExpectation, final_path: Path, partial_path: Path,
    receipt_path: Path, recovery_path: Path, ledger_root: Path,
    digest_provider: Callable[[Path, str], Mapping[str, Any]] = _inprocess_digest_provider,
) -> dict[str, Any]:
    """Adopt an interrupted verified final only through a bound recovery receipt."""
    if not final_path.exists() or final_path.is_symlink():
        raise OrchestrationError("UNBOUND_DOWNLOAD_FINAL_MISSING_OR_SYMLINK")
    batch = ledger["batches"][batch_id]
    key = expectation.source_object_key
    if batch["download_attempts"].get(key, 0) <= 0:
        raise OrchestrationError("UNBOUND_DOWNLOAD_WITHOUT_RECORDED_REQUEST")
    verification = _verification_from_exact_final(
        expectation, final_path=final_path, digest_provider=digest_provider
    )
    verification_sha = canonical_json_sha256(verification)
    receipt_preexisting = receipt_path.exists() or receipt_path.is_symlink()
    if receipt_path.is_symlink():
        raise OrchestrationError("UNBOUND_VERIFICATION_RECEIPT_SYMLINK")
    if receipt_preexisting:
        observed = load_strict_json(receipt_path)
        if not isinstance(observed, Mapping) or observed != verification:
            raise OrchestrationError("UNBOUND_VERIFICATION_RECEIPT_MISMATCH")
        if sha256_file(receipt_path) != verification_sha:
            raise OrchestrationError("UNBOUND_VERIFICATION_RECEIPT_HASH_MISMATCH")
    partial_present = partial_path.exists() or partial_path.is_symlink()
    partial_sha: str | None = None
    if partial_present:
        if partial_path.is_symlink():
            raise OrchestrationError("UNBOUND_PARTIAL_SYMLINK")
        partial_sha = sha256_file(partial_path)
        if (
            partial_sha != verification["local_sha256"]
            or partial_path.stat().st_size != expectation.size_bytes
        ):
            raise OrchestrationError("UNBOUND_PARTIAL_FINAL_MISMATCH")
    common = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_download_transaction_recovery_v2",
        "status": "PASS_RECOVERED_VERIFIED_DOWNLOAD_TRANSACTION",
        "attempt_id": ledger["attempt_id"],
        "batch_id": batch_id,
        "source_object_key": key,
        "authority_sha256": canonical_json_sha256(ledger["authority"]),
        "expected_identity_sha256": canonical_json_sha256(
            {
                "source_object_key": expectation.source_object_key,
                "source_relative_path": expectation.source_relative_path,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
            }
        ),
        "final_sha256": verification["local_sha256"],
        "verification_receipt_sha256": verification_sha,
        "partial_artifact_present": partial_present,
        "partial_sha256": partial_sha,
    }
    if recovery_path.exists() or recovery_path.is_symlink():
        if recovery_path.is_symlink():
            raise OrchestrationError("DOWNLOAD_RECOVERY_RECEIPT_SYMLINK")
        existing_recovery = load_strict_json(recovery_path)
        if not isinstance(existing_recovery, Mapping):
            raise OrchestrationError("DOWNLOAD_RECOVERY_RECEIPT_NOT_MAPPING")
        _validate_recovery_receipt(existing_recovery, expected_common=common)
        recovery_sha = sha256_file(recovery_path)
    else:
        recovery = {
            **common,
            "recovery_class": (
                "VERIFICATION_RECEIPT_WITHOUT_LEDGER_BINDING"
                if receipt_preexisting
                else "FINAL_WITHOUT_VERIFICATION_RECEIPT"
            ),
        }
        recovery_sha = atomic_write_json_no_clobber(
            recovery_path, recovery, attempt_id=str(ledger["attempt_id"])
        )
    if not receipt_preexisting:
        written_sha = atomic_write_json_no_clobber(
            receipt_path, verification, attempt_id=str(ledger["attempt_id"])
        )
        if written_sha != verification_sha:
            raise OrchestrationError("RECOVERED_VERIFICATION_RECEIPT_HASH_MISMATCH")
    if not isinstance(ledger, MutableMapping):
        raise OrchestrationError("LEDGER_NOT_MUTABLE_FOR_JOURNAL")
    append_ledger_delta_no_clobber(
        ledger_root,
        ledger,
        operation="DOWNLOAD_VERIFIED",
        payload={
            "batch_id": batch_id,
            "source_object_key": key,
            "verification_receipt_sha256": verification_sha,
            "recovery_receipt_sha256": recovery_sha,
        },
    )
    if partial_present:
        if partial_path.is_symlink() or sha256_file(partial_path) != partial_sha:
            raise OrchestrationError("RECOVERY_PARTIAL_CHANGED_BEFORE_RETIREMENT")
        try:
            partial_path.unlink()
        except OSError as exc:
            raise OrchestrationError("RECOVERY_REDUNDANT_PARTIAL_RETIREMENT_FAILED") from exc
    return ledger


def atomic_write_json_no_clobber(path: Path, payload: Mapping[str, Any], *, attempt_id: str) -> str:
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise OrchestrationError("ATTEMPT_ID_INVALID")
    if path.exists() or path.is_symlink():
        raise OrchestrationError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise OrchestrationError("OUTPUT_PARENT_NOT_REGULAR_DIRECTORY")
    body = canonical_json_bytes(payload)
    temporary = parent / f".{path.name}.{attempt_id}.partial"
    if temporary.exists() or temporary.is_symlink():
        raise OrchestrationError("OUTPUT_PARTIAL_ALREADY_EXISTS_NO_CLOBBER")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return hashlib.sha256(body).hexdigest()


def _verified_download_manifest_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    header = (
        "subject_id",
        "study_id",
        "source_relative_path",
        "download_ok",
        "observed_sha256",
        "physical_source_key",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if set(row) != set(header):
            raise OrchestrationError("VERIFIED_DOWNLOAD_MANIFEST_ROW_SCHEMA_INVALID")
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _write_or_validate_exact_bytes(
    target: Path, body: bytes, *, attempt_id: str, mismatch_code: str
) -> str:
    digest = hashlib.sha256(body).hexdigest()
    if target.exists() or target.is_symlink():
        if sha256_file(target) != digest or read_regular_bytes(target) != body:
            raise OrchestrationError(mismatch_code)
        return digest
    temporary = target.parent / f".{target.name}.{attempt_id}.partial"
    if temporary.exists() or temporary.is_symlink():
        raise OrchestrationError("MANIFEST_PARTIAL_ALREADY_EXISTS")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return digest


def materialize_selected_batch_manifest(
    *, plan: Mapping[str, Any], ledger: Mapping[str, Any], batch_id: str,
    batch_root: Path
) -> tuple[Path, str]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    batch_plan = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if batch_plan is None or batch_id not in ledger["batches"]:
        raise OrchestrationError("SELECTED_BATCH_NOT_PLANNED")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=["subject_id", "study_id"], lineterminator="\n"
    )
    writer.writeheader()
    for row in batch_plan["studies"]:
        writer.writerow({"subject_id": row["subject_id"], "study_id": row["study_id"]})
    body = output.getvalue().encode("utf-8")
    target = batch_root / "selected_batch.restricted.csv"
    digest = _write_or_validate_exact_bytes(
        target,
        body,
        attempt_id=str(ledger["attempt_id"]),
        mismatch_code="SELECTED_BATCH_MANIFEST_PREEXISTING_MISMATCH",
    )
    return target, digest


def materialize_verified_download_manifest(
    *, plan: Mapping[str, Any], ledger: Mapping[str, Any], batch_id: str,
    batch_root: Path
) -> tuple[Path, str]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    batch_plan = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if batch_plan is None or batch_id not in ledger["batches"]:
        raise OrchestrationError("DOWNLOAD_MANIFEST_BATCH_NOT_PLANNED")
    receipts = ledger["batches"][batch_id]["download_verification_receipts"]
    if len(receipts) != batch_plan["n_objects"]:
        raise OrchestrationError("DOWNLOAD_MANIFEST_BEFORE_ALL_OBJECTS_VERIFIED")
    rows: list[dict[str, Any]] = []
    for object_row in batch_plan["objects"]:
        key = str(object_row["source_object_key"])
        receipt_path = batch_root / "receipts" / f"{key}.verification.json"
        final_path = batch_root / "objects" / f"{key}.dcm"
        if sha256_file(receipt_path) != receipts.get(key):
            raise OrchestrationError("DOWNLOAD_MANIFEST_RECEIPT_HASH_MISMATCH")
        verification = load_strict_json(receipt_path)
        metadata = final_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OrchestrationError("DOWNLOAD_MANIFEST_FINAL_OBJECT_INVALID")
        observed_sha = sha256_file(final_path)
        if (
            verification.get("status") != "PASS_DOWNLOAD_VERIFICATION"
            or verification.get("local_sha256") != observed_sha
            or verification.get("size_bytes") != metadata.st_size
        ):
            raise OrchestrationError("DOWNLOAD_MANIFEST_FINAL_OBJECT_INVALID")
        rows.append(
            {
                "subject_id": object_row["subject_id"],
                "study_id": object_row["study_id"],
                "source_relative_path": object_row["source_relative_path"],
                "download_ok": "true",
                "observed_sha256": observed_sha,
                "physical_source_key": key,
            }
        )
    body = _verified_download_manifest_bytes(rows)
    target = batch_root / "verified_download_manifest.restricted.csv"
    digest = _write_or_validate_exact_bytes(
        target,
        body,
        attempt_id=str(ledger["attempt_id"]),
        mismatch_code="VERIFIED_DOWNLOAD_MANIFEST_PREEXISTING_MISMATCH",
    )
    return target, digest


JOURNAL_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "batch_id",
        "sequence",
        "operation",
        "payload",
        "prior_journal_sha256",
        "authority_sha256",
    }
)
JOURNAL_ENTRY_RE = re.compile(r"^journal_([0-9]{9})_([0-9a-f]{16})[.]json$")


def _validate_journal_operation(
    ledger: Mapping[str, Any], *, operation: str, payload: Mapping[str, Any]
) -> None:
    if operation == "TRANSITION":
        _require_exact_keys(payload, frozenset({"receipt"}), "JOURNAL_TRANSITION_SCHEMA_INVALID")
        receipt = payload["receipt"]
        if not isinstance(receipt, Mapping):
            raise OrchestrationError("JOURNAL_TRANSITION_RECEIPT_INVALID")
        apply_transition(ledger, receipt)
        return
    if operation == "SELECTED_BATCH_MANIFEST":
        expected = {"batch_id", "manifest_sha256"}
    elif operation == "DOWNLOAD_ATTEMPT":
        expected = {"batch_id", "source_object_key", "attempt_count"}
    elif operation == "DOWNLOAD_VERIFIED":
        expected = {
            "batch_id",
            "source_object_key",
            "verification_receipt_sha256",
            "recovery_receipt_sha256",
        }
    elif operation == "DOWNLOAD_MANIFEST":
        expected = {"batch_id", "manifest_sha256"}
    else:
        raise OrchestrationError("JOURNAL_OPERATION_UNKNOWN")
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise OrchestrationError("JOURNAL_OPERATION_PAYLOAD_SCHEMA_INVALID")
    batch_id = str(payload.get("batch_id"))
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("JOURNAL_BATCH_NOT_PLANNED")
    batch = ledger["batches"][batch_id]
    if operation == "SELECTED_BATCH_MANIFEST":
        digest = _require_sha256(
            payload.get("manifest_sha256"), "SELECTED_BATCH_MANIFEST_HASH_INVALID"
        )
        if batch["selected_batch_manifest_sha256"] not in (None, digest):
            raise OrchestrationError("SELECTED_BATCH_MANIFEST_CHANGED")
    elif operation == "DOWNLOAD_ATTEMPT":
        key = _require_sha256(payload.get("source_object_key"), "DOWNLOAD_OBJECT_KEY_INVALID")
        count = payload.get("attempt_count")
        if (
            batch["state"] != "DOWNLOAD_IN_PROGRESS"
            or key not in batch["download_attempts"]
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count != batch["download_attempts"][key] + 1
        ):
            raise OrchestrationError("JOURNAL_DOWNLOAD_ATTEMPT_INVALID")
    elif operation == "DOWNLOAD_VERIFIED":
        key = _require_sha256(payload.get("source_object_key"), "DOWNLOAD_OBJECT_KEY_INVALID")
        receipt_sha = _require_sha256(
            payload.get("verification_receipt_sha256"),
            "DOWNLOAD_VERIFICATION_RECEIPT_HASH_INVALID",
        )
        recovery_sha = payload.get("recovery_receipt_sha256")
        if recovery_sha is not None:
            _require_sha256(recovery_sha, "DOWNLOAD_RECOVERY_RECEIPT_HASH_INVALID")
        if (
            batch["state"] != "DOWNLOAD_IN_PROGRESS"
            or key not in batch["download_attempts"]
            or batch["download_attempts"][key] <= 0
            or key in batch["download_verification_receipts"]
        ):
            raise OrchestrationError("JOURNAL_DOWNLOAD_VERIFICATION_INVALID")
        del receipt_sha
    elif operation == "DOWNLOAD_MANIFEST":
        _require_sha256(payload.get("manifest_sha256"), "DOWNLOAD_MANIFEST_HASH_INVALID")
        if (
            set(batch["download_verification_receipts"])
            != set(batch["download_attempts"])
            or batch["download_manifest_sha256"] is not None
        ):
            raise OrchestrationError("JOURNAL_DOWNLOAD_MANIFEST_INVALID")


def _apply_journal_operation_inplace(
    ledger: MutableMapping[str, Any], *, operation: str, payload: Mapping[str, Any]
) -> None:
    _validate_journal_operation(ledger, operation=operation, payload=payload)
    if operation == "TRANSITION":
        updated = apply_transition(ledger, payload["receipt"])
        ledger.clear()
        ledger.update(updated)
        return
    batch = ledger["batches"][str(payload["batch_id"])]
    if operation == "SELECTED_BATCH_MANIFEST":
        batch["selected_batch_manifest_sha256"] = payload["manifest_sha256"]
    elif operation == "DOWNLOAD_ATTEMPT":
        batch["download_attempts"][payload["source_object_key"]] = payload["attempt_count"]
    elif operation == "DOWNLOAD_VERIFIED":
        key = payload["source_object_key"]
        batch["download_verification_receipts"][key] = payload[
            "verification_receipt_sha256"
        ]
        if payload["recovery_receipt_sha256"] is not None:
            batch["download_recovery_receipts"][key] = payload[
                "recovery_receipt_sha256"
            ]
    elif operation == "DOWNLOAD_MANIFEST":
        batch["download_manifest_sha256"] = payload["manifest_sha256"]


def append_ledger_delta_no_clobber(
    directory: Path, ledger: MutableMapping[str, Any], *, operation: str,
    payload: Mapping[str, Any]
) -> Path:
    """Durably append one constant-size mutation; never writes the full ledger."""
    if directory.is_symlink() or not directory.is_dir():
        raise OrchestrationError("LEDGER_JOURNAL_DIRECTORY_INVALID")
    _validate_journal_operation(ledger, operation=operation, payload=payload)
    sequence = int(ledger["journal_sequence"]) + 1
    batch_id = (
        str(payload["receipt"]["batch_id"])
        if operation == "TRANSITION"
        else str(payload["batch_id"])
    )
    entry = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_resume_journal_delta_v2",
        "status": "PASS_DURABLE_LEDGER_DELTA",
        "attempt_id": ledger["attempt_id"],
        "batch_id": batch_id,
        "sequence": sequence,
        "operation": operation,
        "payload": payload,
        "prior_journal_sha256": ledger["journal_head_sha256"],
        "authority_sha256": canonical_json_sha256(ledger["authority"]),
    }
    digest = canonical_json_sha256(entry)
    target = directory / f"journal_{sequence:09d}_{digest[:16]}.json"
    written = atomic_write_json_no_clobber(
        target, entry, attempt_id=str(ledger["attempt_id"])
    )
    if written != digest:
        raise OrchestrationError("LEDGER_JOURNAL_WRITE_HASH_MISMATCH")
    _apply_journal_operation_inplace(ledger, operation=operation, payload=payload)
    ledger["journal_sequence"] = sequence
    ledger["journal_head_sha256"] = digest
    return target


def load_latest_ledger_snapshot(
    directory: Path, *, initial_ledger: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replay a strict, append-only O(n) delta journal for crash resume."""
    validate_resume_authority(
        initial_ledger, expected_authority=initial_ledger["authority"]
    )
    if (
        initial_ledger["journal_sequence"] != 0
        or initial_ledger["journal_head_sha256"] is not None
    ):
        raise OrchestrationError("INITIAL_LEDGER_ALREADY_JOURNALED")
    if directory.is_symlink() or not directory.is_dir():
        raise OrchestrationError("LEDGER_JOURNAL_DIRECTORY_INVALID")
    entries: list[tuple[int, Path, str]] = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise OrchestrationError("LEDGER_JOURNAL_ENTRY_INVALID")
        match = JOURNAL_ENTRY_RE.fullmatch(path.name)
        if match is None:
            raise OrchestrationError("LEDGER_JOURNAL_NAME_INVALID")
        entries.append((int(match.group(1)), path, match.group(2)))
    entries.sort(key=lambda item: item[0])
    updated: MutableMapping[str, Any] = copy.deepcopy(initial_ledger)
    for expected_sequence, (sequence, path, name_digest) in enumerate(entries, start=1):
        if sequence != expected_sequence:
            raise OrchestrationError("LEDGER_JOURNAL_SEQUENCE_GAP_OR_DUPLICATE")
        entry = load_strict_json(path)
        if not isinstance(entry, Mapping):
            raise OrchestrationError("LEDGER_JOURNAL_NOT_MAPPING")
        _require_exact_keys(entry, JOURNAL_ENTRY_KEYS, "LEDGER_JOURNAL_SCHEMA_INVALID")
        digest = sha256_file(path)
        if (
            digest != canonical_json_sha256(entry)
            or name_digest != digest[:16]
            or entry.get("schema_version") != 2
            or entry.get("artifact_type") != "lvef_c3_resume_journal_delta_v2"
            or entry.get("status") != "PASS_DURABLE_LEDGER_DELTA"
            or entry.get("attempt_id") != initial_ledger["attempt_id"]
            or entry.get("sequence") != sequence
            or entry.get("prior_journal_sha256") != updated["journal_head_sha256"]
            or entry.get("authority_sha256")
            != canonical_json_sha256(initial_ledger["authority"])
        ):
            raise OrchestrationError("LEDGER_JOURNAL_AUTHORITY_OR_HASH_MISMATCH")
        operation = str(entry.get("operation"))
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            raise OrchestrationError("LEDGER_JOURNAL_PAYLOAD_NOT_MAPPING")
        _apply_journal_operation_inplace(updated, operation=operation, payload=payload)
        updated["journal_sequence"] = sequence
        updated["journal_head_sha256"] = digest
    validate_resume_authority(
        updated,
        expected_authority=initial_ledger["authority"],
        attempt_id=str(initial_ledger["attempt_id"]),
        expected_object_keys={
            batch_id: set(batch["download_attempts"])
            for batch_id, batch in initial_ledger["batches"].items()
        },
    )
    return updated


def _safe_transport_failure_from_http(status: int) -> str:
    if status == 401:
        return "AUTHENTICATION"
    if status in {402, 403}:
        return "AUTHORIZATION"
    if status == 408:
        return "HTTP_408"
    if status == 429:
        return "HTTP_429"
    if 500 <= status <= 599:
        return "HTTP_5XX"
    return "HTTP_OTHER_4XX"


CONTENT_RANGE_RE = re.compile(
    r"^bytes (?P<start>[0-9]+)-(?P<end>[0-9]+)/(?P<total>[0-9]+)$"
)


def owner_private_directory_mode_ok(mode: int) -> bool:
    """Accept private directories with SCC's inherited setgid bit only."""
    return stat.S_IMODE(mode) in {0o700, 0o2700}


def validate_resume_content_range(value: Any, *, offset: int, total_size: int) -> None:
    if offset <= 0 or total_size <= offset:
        raise OrchestrationError("CONTENT_RANGE_EXPECTATION_INVALID")
    match = CONTENT_RANGE_RE.fullmatch(str(value))
    if match is None:
        raise OrchestrationError("CONTENT_RANGE_HEADER_INVALID")
    start = int(match.group("start"))
    end = int(match.group("end"))
    total = int(match.group("total"))
    if start != offset or end != total_size - 1 or total != total_size:
        raise OrchestrationError("CONTENT_RANGE_IDENTITY_MISMATCH")


class GcloudADCTokenProvider:
    """Acquire one ADC access token without printing or persisting it."""

    def __init__(
        self, gcloud_binary: Path, *, cloudsdk_config: Path,
        authority_receipt: Path, authority_receipt_sha256: str
    ):
        self.gcloud_binary = gcloud_binary
        self.cloudsdk_config = cloudsdk_config
        self.authority_receipt = authority_receipt
        self.authority_receipt_sha256 = _require_sha256(
            authority_receipt_sha256, "CLOUDSDK_AUTHORITY_RECEIPT_HASH_INVALID"
        )

    def validate_authority(self) -> dict[str, str]:
        if (
            not self.gcloud_binary.is_absolute()
            or self.gcloud_binary.is_symlink()
            or not self.gcloud_binary.is_file()
        ):
            raise DownloadTransportError("GCLOUD_BINARY_INVALID", "AUTHENTICATION")
        if (
            not self.cloudsdk_config.is_absolute()
            or self.cloudsdk_config.is_symlink()
            or not self.cloudsdk_config.is_dir()
        ):
            raise DownloadTransportError("CLOUDSDK_CONFIG_INVALID", "AUTHENTICATION")
        config_stat = self.cloudsdk_config.stat()
        if (
            config_stat.st_uid != os.getuid()
            or not owner_private_directory_mode_ok(config_stat.st_mode)
        ):
            raise DownloadTransportError("CLOUDSDK_CONFIG_NOT_PRIVATE", "AUTHENTICATION")
        try:
            receipt_descriptor, receipt_stat = _open_regular_nofollow(
                self.authority_receipt
            )
        except OrchestrationError as exc:
            raise DownloadTransportError(
                "CLOUDSDK_AUTHORITY_RECEIPT_INVALID", "AUTHENTICATION"
            ) from exc
        os.close(receipt_descriptor)
        if (
            receipt_stat.st_uid != os.getuid()
            or stat.S_IMODE(receipt_stat.st_mode) != 0o600
            or sha256_file(self.authority_receipt) != self.authority_receipt_sha256
        ):
            raise DownloadTransportError(
                "CLOUDSDK_AUTHORITY_RECEIPT_INVALID", "AUTHENTICATION"
            )
        record = load_strict_json(self.authority_receipt)
        expected_record_keys = {
            "audit",
            "credential_material_accessed",
            "expected_version",
            "executable_sha256",
            "module_name",
            "resolution_source",
            "retained_archive_sha256",
            "retained_tar_payload_sha256",
            "selected_executable",
            "status",
            "version",
        }
        resolved_gcloud = self.gcloud_binary.resolve(strict=True)
        executable_sha = sha256_file(resolved_gcloud)
        if (
            not isinstance(record, Mapping)
            or set(record) != expected_record_keys
            or record.get("audit") != "lvef_scc_gcloud_resolution"
            or record.get("status") != "PASS"
            or record.get("credential_material_accessed") is not False
            or record.get("expected_version") != "579.0.0"
            or record.get("version") != "579.0.0"
            or record.get("resolution_source") != "COMMON_SELF_CONTAINED_INSTALL"
            or record.get("module_name") is not None
            or record.get("selected_executable") != str(resolved_gcloud)
            or record.get("executable_sha256") != executable_sha
            or record.get("retained_archive_sha256")
            != "a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
            or record.get("retained_tar_payload_sha256")
            != "f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
        ):
            raise DownloadTransportError(
                "CLOUDSDK_RESOLUTION_RECEIPT_NOT_AUTHORITATIVE",
                "AUTHENTICATION",
            )
        adc = self.cloudsdk_config / "application_default_credentials.json"
        try:
            adc_descriptor, adc_stat = _open_regular_nofollow(adc)
        except OrchestrationError as exc:
            raise DownloadTransportError("ADC_FILE_INVALID", "AUTHENTICATION") from exc
        os.close(adc_descriptor)
        if adc_stat.st_uid != os.getuid() or stat.S_IMODE(adc_stat.st_mode) != 0o600:
            raise DownloadTransportError("ADC_FILE_NOT_PRIVATE", "AUTHENTICATION")
        return {
            "gcloud_resolution_receipt_sha256": self.authority_receipt_sha256,
            "gcloud_executable_sha256": executable_sha,
        }

    def __call__(self) -> str:
        if self.gcloud_binary.is_symlink() or not self.gcloud_binary.is_file():
            raise DownloadTransportError("GCLOUD_BINARY_INVALID", "AUTHENTICATION")
        self.validate_authority()
        try:
            completed = subprocess.run(
                [
                    str(self.gcloud_binary),
                    "auth",
                    "application-default",
                    "print-access-token",
                    "--quiet",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=60,
                env={
                    "CLOUDSDK_CONFIG": str(self.cloudsdk_config),
                    "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
                    "PATH": "/usr/bin:/bin",
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DownloadTransportError("ADC_TOKEN_ACQUISITION_FAILED", "AUTHENTICATION") from exc
        token = completed.stdout.strip()
        if completed.returncode != 0 or not token or any(character.isspace() for character in token):
            raise DownloadTransportError("ADC_TOKEN_ACQUISITION_FAILED", "AUTHENTICATION")
        return token


class GCSExactObjectBodyTransport:
    """Exact-generation JSON API media transport; never performs listing."""

    bucket = "mimic-iv-echo-1.0.physionet.org"

    def fetch(
        self, expectation: DownloadExpectation, *, partial_path: Path,
        billing_project: str, access_token: str, timeout_seconds: int = 300
    ) -> Mapping[str, Any]:
        # Imports are local so offline planners and validators have no network side effect.
        from urllib.error import HTTPError, URLError
        from http.client import IncompleteRead
        from urllib.parse import quote, urlencode
        from urllib.request import HTTPRedirectHandler, Request, build_opener

        class RejectRedirect(HTTPRedirectHandler):
            def redirect_request(self, *_: Any, **__: Any) -> None:
                return None

        if not access_token or any(character.isspace() for character in access_token):
            raise DownloadTransportError("ACCESS_TOKEN_INVALID", "AUTHENTICATION")
        if partial_path.is_symlink():
            raise DownloadTransportError("DOWNLOAD_PARTIAL_IS_SYMLINK", "MANIFEST")
        offset = 0
        if partial_path.exists():
            if not partial_path.is_file():
                raise DownloadTransportError("DOWNLOAD_PARTIAL_NOT_REGULAR", "MANIFEST")
            offset = partial_path.stat().st_size
            if offset >= expectation.size_bytes:
                raise DownloadTransportError("STALE_PARTIAL_NOT_RESUMABLE", "MANIFEST")
        query = urlencode(
            {
                "alt": "media",
                "generation": expectation.generation,
                "ifGenerationMatch": expectation.generation,
                "userProject": billing_project,
            }
        )
        encoded_object = quote(expectation.source_relative_path, safe="")
        url = (
            f"https://storage.googleapis.com/download/storage/v1/b/{self.bucket}/"
            f"o/{encoded_object}?{query}"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, method="GET", headers=headers)
        flags = os.O_WRONLY | (os.O_APPEND if offset else os.O_CREAT | os.O_EXCL)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            with build_opener(RejectRedirect()).open(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", response.getcode()))
                if (offset and status_code != 206) or (not offset and status_code != 200):
                    raise DownloadTransportError("BODY_RESPONSE_STATUS_INVALID", "MANIFEST")
                if response.geturl() != url:
                    raise DownloadTransportError("BODY_REDIRECT_PROHIBITED", "MANIFEST")
                if offset:
                    try:
                        validate_resume_content_range(
                            response.headers.get("Content-Range"),
                            offset=offset,
                            total_size=expectation.size_bytes,
                        )
                    except OrchestrationError as exc:
                        raise DownloadTransportError(
                            "BODY_CONTENT_RANGE_INVALID", "MANIFEST"
                        ) from exc
                generation = str(response.headers.get("x-goog-generation", ""))
                hash_header = str(response.headers.get("x-goog-hash", ""))
                hash_parts = {
                    key.strip(): value.strip()
                    for part in hash_header.split(",")
                    if "=" in part
                    for key, value in [part.split("=", 1)]
                }
                if generation != expectation.generation:
                    raise DownloadTransportError("REMOTE_GENERATION_CHANGED", "GENERATION_MISMATCH")
                if (
                    "md5" in hash_parts
                    and hash_parts["md5"] != expectation.md5_base64
                ):
                    raise DownloadTransportError("REMOTE_MD5_CHANGED", "CHECKSUM_MISMATCH")
                if (
                    "crc32c" in hash_parts
                    and hash_parts["crc32c"] != expectation.crc32c_base64
                ):
                    raise DownloadTransportError("REMOTE_CRC_CHANGED", "CHECKSUM_MISMATCH")
                if not offset and not {"md5", "crc32c"}.issubset(hash_parts):
                    raise DownloadTransportError("REMOTE_HASH_HEADERS_MISSING", "MANIFEST")
                descriptor = os.open(partial_path, flags, 0o600)
                response_body_bytes = 0
                with os.fdopen(descriptor, "ab" if offset else "wb") as handle:
                    try:
                        while True:
                            block = response.read(8 * 1024 * 1024)
                            if not block:
                                break
                            response_body_bytes += len(block)
                            handle.write(block)
                    except (IncompleteRead, ConnectionResetError, TimeoutError, OSError) as exc:
                        handle.flush()
                        os.fsync(handle.fileno())
                        raise DownloadTransportError(
                            "GCS_BODY_RESPONSE_INTERRUPTED", "TRANSIENT_NETWORK"
                        ) from exc
                    handle.flush()
                    os.fsync(handle.fileno())
        except HTTPError as exc:
            raise DownloadTransportError(
                "GCS_BODY_REQUEST_HTTP_FAILURE", _safe_transport_failure_from_http(exc.code)
            ) from None
        except (URLError, TimeoutError):
            raise DownloadTransportError("GCS_BODY_REQUEST_NETWORK_FAILURE", "TRANSIENT_NETWORK") from None
        size = partial_path.stat().st_size
        expected_response_bytes = expectation.size_bytes - offset
        if response_body_bytes != expected_response_bytes or size != expectation.size_bytes:
            if response_body_bytes < expected_response_bytes and size < expectation.size_bytes:
                raise DownloadTransportError("GCS_BODY_RESPONSE_SHORT", "TRANSIENT_NETWORK")
            raise DownloadTransportError("GCS_BODY_RESPONSE_SIZE_INVALID", "SIZE_MISMATCH")
        return {
            "schema_version": 2,
            "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
            "source_object_key": expectation.source_object_key,
            "size_bytes": expectation.size_bytes,
            "generation": expectation.generation,
            "md5_base64": expectation.md5_base64,
            "crc32c_base64": expectation.crc32c_base64,
            "media_request_count": 1,
            "object_body_bytes_read": response_body_bytes,
            "resume_offset_bytes": offset,
            "response_body_bytes_read": response_body_bytes,
            "final_partial_size_bytes": size,
            "content_range_validated": offset > 0,
        }


def _record_terminal_download_failure(
    ledger: Mapping[str, Any], *, batch_id: str, source_object_key: str,
    failure_code: str, attempts_used: int, receipt_root: Path,
    ledger_root: Path
) -> dict[str, Any]:
    evidence = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_restricted_download_failure_v2",
        "status": "FAILED_NONRETRYABLE",
        "attempt_id": ledger["attempt_id"],
        "batch_id": batch_id,
        "source_object_key": source_object_key,
        "failure_code": failure_code,
        "attempts_used": attempts_used,
        "authority_sha256": canonical_json_sha256(ledger["authority"]),
    }
    evidence_path = receipt_root / f"{source_object_key}.failure.restricted.json"
    evidence_sha = atomic_write_json_no_clobber(
        evidence_path, evidence, attempt_id=str(ledger["attempt_id"])
    )
    last_event_sha = ledger["batches"][batch_id]["events"][-1]["receipt_sha256"]
    transition = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": ledger["attempt_id"],
        "batch_id": batch_id,
        "from_state": "DOWNLOAD_IN_PROGRESS",
        "to_state": "FAILED_NONRETRYABLE",
        "status": "PASS",
        "authority": ledger["authority"],
        "input_receipt_sha256": [last_event_sha],
        "output_manifest_sha256": evidence_sha,
    }
    transition_path = (
        receipt_root / f"{source_object_key}.failure_transition.restricted.json"
    )
    transition_sha = atomic_write_json_no_clobber(
        transition_path, transition, attempt_id=str(ledger["attempt_id"])
    )
    if transition_sha != canonical_json_sha256(transition):
        raise OrchestrationError("DOWNLOAD_FAILURE_TRANSITION_HASH_MISMATCH")
    if not isinstance(ledger, MutableMapping):
        raise OrchestrationError("LEDGER_NOT_MUTABLE_FOR_JOURNAL")
    append_ledger_delta_no_clobber(
        ledger_root,
        ledger,
        operation="TRANSITION",
        payload={"receipt": transition},
    )
    return ledger


def execute_exact_batch_download(
    *, plan: Mapping[str, Any], requirements: PlanRequirements,
    ledger: Mapping[str, Any], contract: Mapping[str, Any], batch_id: str,
    expected_runtime_authority: Mapping[str, Any],
    authorization_receipt: Mapping[str, Any] | None, output_root: Path,
    launch_authority_sha256: str,
    argv: Sequence[str], token_provider: Any, transport: Any,
    now: datetime | None = None, monotonic_clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
    digest_provider: Callable[[Path, str], Mapping[str, Any]] = _inprocess_digest_provider,
    scoped_production_root: Path | None = None,
    direct_manifest: Mapping[str, Any] | None = None,
    direct_full_authority: Mapping[str, Any] | None = None,
    test_only_synthetic_full_scope: bool = False,
) -> dict[str, Any]:
    """Execute one authorization-scoped exact batch; callers persist returned ledger."""
    plan_sha = validate_batch_plan(plan, requirements=requirements)
    validate_resume_authority(
        ledger, expected_authority=expected_runtime_authority
    )
    if plan_sha != ledger["authority"]["batch_plan_sha256"]:
        raise OrchestrationError("DOWNLOAD_PLAN_LEDGER_MISMATCH")
    maximum_attempts = int(contract["downloader"]["maximum_attempts_per_object"])
    backoff_initial = int(contract["downloader"]["retry_backoff_initial_seconds"])
    backoff_maximum = int(contract["downloader"]["retry_backoff_max_seconds"])
    direct_modes = int(direct_manifest is not None) + int(
        direct_full_authority is not None
    )
    if test_only_synthetic_full_scope and direct_full_authority is None:
        raise OrchestrationError("DIRECT_FULL_TEST_BOUNDARY_INVALID")
    if direct_modes > 1:
        raise OrchestrationError("MULTIPLE_DIRECT_DOWNLOAD_AUTHORITIES_SUPPLIED")
    if direct_modes == 0:
        if authorization_receipt is None:
            raise OrchestrationError("BODY_TRANSFER_AUTHORIZATION_MISSING")
        validate_body_transfer_authorization(
            authorization_receipt,
            ledger=ledger,
            plan=plan,
            batch_id=batch_id,
            maximum_attempts_per_object=maximum_attempts,
            expected_launch_authority_sha256=launch_authority_sha256,
            now=now,
        )
    elif direct_manifest is not None:
        if authorization_receipt is not None:
            raise OrchestrationError("DIRECT_MANIFEST_AND_GRANT_BOTH_SUPPLIED")
        validate_direct_manifest_download_scope(
            manifest=direct_manifest,
            ledger=ledger,
            plan=plan,
            batch_id=batch_id,
            maximum_attempts_per_object=maximum_attempts,
            expected_launch_authority_sha256=launch_authority_sha256,
        )
    else:
        if authorization_receipt is not None:
            raise OrchestrationError("DIRECT_FULL_AND_GRANT_BOTH_SUPPLIED")
        validate_direct_full_download_scope(
            launch_authority=direct_full_authority,
            ledger=ledger,
            plan=plan,
            requirements=requirements,
            batch_id=batch_id,
            maximum_attempts_per_object=maximum_attempts,
            expected_launch_authority_sha256=launch_authority_sha256,
            test_only_synthetic_full_scope=test_only_synthetic_full_scope,
        )
    billing_env = str(contract["downloader"]["billing_project_environment_variable"])
    validate_private_billing_environment(billing_env, argv=argv)
    billing_project = os.environ[billing_env]
    if direct_full_authority is not None and not test_only_synthetic_full_scope:
        if (
            contract.get("contract_id") != EXPECTED_FULL_CONTRACT_ID
            or contract.get("storage", {}).get("production_root")
            != str(EXPECTED_FULL_PRODUCTION_ROOT)
            or contract.get("storage", {}).get("raw_root")
            != EXPECTED_FULL_RAW_ROOT_TEMPLATE
            or scoped_production_root not in {None, EXPECTED_FULL_PRODUCTION_ROOT}
        ):
            raise OrchestrationError("DIRECT_FULL_PRODUCTION_ROOT_INVALID")
        expected_output_root = Path(
            EXPECTED_FULL_RAW_ROOT_TEMPLATE.format(
                attempt_id=str(ledger["attempt_id"])
            )
        )
    elif test_only_synthetic_full_scope:
        if (
            scoped_production_root is None
            or not scoped_production_root.is_absolute()
            or scoped_production_root.is_symlink()
            or isinstance(token_provider, GcloudADCTokenProvider)
            or isinstance(transport, GCSExactObjectBodyTransport)
        ):
            raise OrchestrationError("DIRECT_FULL_TEST_BOUNDARY_INVALID")
        try:
            scoped_production_root.resolve().relative_to(
                Path(tempfile.gettempdir()).resolve()
            )
        except (OSError, ValueError) as exc:
            raise OrchestrationError("DIRECT_FULL_TEST_BOUNDARY_INVALID") from exc
        expected_output_root = (
            scoped_production_root
            / "attempts"
            / str(ledger["attempt_id"])
            / "raw"
        )
    elif scoped_production_root is None:
        expected_output_root = Path(
            str(contract["storage"]["raw_root"]).format(
                attempt_id=str(ledger["attempt_id"])
            )
        )
    else:
        canary_scope = (
            direct_manifest is not None
            and requirements.contract_id
            == "lvef_multitask_c3_exact_five_canary_v1"
            and requirements.selected_studies == 5
            and requirements.selected_subjects == 5
            and requirements.batch_count == 1
            and requirements.studies_per_full_batch == 5
            and requirements.final_batch_studies == 5
            and 5 <= requirements.normalized_source_objects <= 750
            and 1 <= requirements.selected_source_bytes <= 5_000_000_000
        )
        if (
            not canary_scope
            or not scoped_production_root.is_absolute()
            or scoped_production_root.is_symlink()
        ):
            raise OrchestrationError("SCOPED_DOWNLOAD_ROOT_AUTHORITY_INVALID")
        expected_output_root = (
            scoped_production_root
            / "attempts"
            / str(ledger["attempt_id"])
            / "raw"
        )
    if output_root != expected_output_root:
        raise OrchestrationError("DOWNLOAD_OUTPUT_ROOT_NOT_CONTRACT_BOUND")
    if output_root.is_symlink() or not output_root.is_dir():
        raise OrchestrationError("DOWNLOAD_OUTPUT_ROOT_INVALID")
    batch_plan = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if batch_plan is None:
        raise OrchestrationError("DOWNLOAD_BATCH_NOT_PLANNED")
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("DOWNLOAD_BATCH_LEDGER_SCOPE_MISMATCH")
    expected_object_scope = {
        batch_id: {str(row["source_object_key"]) for row in batch_plan["objects"]}
    }
    validate_resume_authority(
        ledger,
        expected_authority=ledger["authority"],
        expected_object_keys=expected_object_scope,
    )
    batch_root = output_root / batch_id
    if batch_root.exists():
        if batch_root.is_symlink() or not batch_root.is_dir():
            raise OrchestrationError("DOWNLOAD_BATCH_ROOT_INVALID")
    else:
        batch_root.mkdir(mode=0o700)
    partial_root = batch_root / "partials"
    final_root = batch_root / "objects"
    receipt_root = batch_root / "receipts"
    ledger_root = batch_root / "ledger"
    for directory in (partial_root, final_root, receipt_root, ledger_root):
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise OrchestrationError("DOWNLOAD_SUBDIRECTORY_INVALID")
        else:
            directory.mkdir(mode=0o700)
    updated = load_latest_ledger_snapshot(ledger_root, initial_ledger=ledger)
    validate_resume_authority(
        updated,
        expected_authority=ledger["authority"],
        expected_object_keys=expected_object_scope,
    )
    batch_ledger = updated["batches"][batch_id]
    start_receipt: Mapping[str, Any] | None = None
    if batch_ledger["state"] == "PLANNED":
        start_receipt = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": updated["attempt_id"],
            "batch_id": batch_id,
            "from_state": "PLANNED",
            "to_state": "DOWNLOAD_IN_PROGRESS",
            "status": "PASS",
            "authority": updated["authority"],
            "input_receipt_sha256": [updated["authority"]["batch_plan_sha256"]],
            "output_manifest_sha256": canonical_json_sha256(
                direct_manifest
                if direct_manifest is not None
                else (
                    direct_full_authority
                    if direct_full_authority is not None
                    else authorization_receipt
                )
            ),
        }
    elif batch_ledger["state"] not in {"DOWNLOAD_IN_PROGRESS", "DOWNLOAD_VERIFIED"}:
        raise OrchestrationError("DOWNLOAD_BATCH_NOT_RESUMABLE")
    start_receipt_path = receipt_root / "download_start_transition.restricted.json"
    if start_receipt is not None:
        start_sha = atomic_write_json_no_clobber(
            start_receipt_path, start_receipt, attempt_id=updated["attempt_id"]
        )
        if start_sha != canonical_json_sha256(start_receipt):
            raise OrchestrationError("DOWNLOAD_START_TRANSITION_HASH_MISMATCH")
        append_ledger_delta_no_clobber(
            ledger_root,
            updated,
            operation="TRANSITION",
            payload={"receipt": start_receipt},
        )
    else:
        if (
            not updated["batches"][batch_id]["events"]
            or sha256_file(start_receipt_path)
            != updated["batches"][batch_id]["events"][0]["receipt_sha256"]
        ):
            raise OrchestrationError("DOWNLOAD_START_TRANSITION_RECEIPT_MISSING")
    selected_batch_already_bound = (
        updated["batches"][batch_id]["selected_batch_manifest_sha256"] is not None
    )
    _, selected_batch_sha = materialize_selected_batch_manifest(
        plan=plan, ledger=updated, batch_id=batch_id, batch_root=batch_root
    )
    if not selected_batch_already_bound:
        append_ledger_delta_no_clobber(
            ledger_root,
            updated,
            operation="SELECTED_BATCH_MANIFEST",
            payload={"batch_id": batch_id, "manifest_sha256": selected_batch_sha},
        )
    elif updated["batches"][batch_id]["selected_batch_manifest_sha256"] != selected_batch_sha:
        raise OrchestrationError("SELECTED_BATCH_MANIFEST_CHANGED")
    if updated["batches"][batch_id]["state"] == "DOWNLOAD_VERIFIED":
        _, observed_manifest_sha = materialize_verified_download_manifest(
            plan=plan, ledger=updated, batch_id=batch_id, batch_root=batch_root
        )
        if observed_manifest_sha != updated["batches"][batch_id]["download_manifest_sha256"]:
            raise OrchestrationError("RESUME_DOWNLOAD_MANIFEST_HASH_MISMATCH")
        verified_transition_path = (
            receipt_root / "download_verified_transition.restricted.json"
        )
        if (
            sha256_file(verified_transition_path)
            != updated["batches"][batch_id]["events"][-1]["receipt_sha256"]
        ):
            raise OrchestrationError("RESUME_VERIFIED_TRANSITION_RECEIPT_MISMATCH")
        return updated
    token: str | None = None
    token_acquired_at: float | None = None
    token_refresh_seconds = int(contract["downloader"]["token_refresh_interval_seconds"])
    for object_row in batch_plan["objects"]:
        expectation = expectation_from_plan_object(object_row)
        key = expectation.source_object_key
        final_path = final_root / planned_final_name(expectation)
        partial_path = partial_root / planned_partial_name(
            expectation, updated["attempt_id"]
        )
        receipt_path = receipt_root / f"{key}.verification.json"
        recovery_path = receipt_root / f"{key}.recovery.json"
        already = updated["batches"][batch_id]["download_verification_receipts"].get(key)
        if already is not None:
            if sha256_file(receipt_path) != already:
                raise OrchestrationError("RESUME_VERIFICATION_RECEIPT_MISMATCH")
            verification = load_strict_json(receipt_path)
            expected_verification = _verification_from_exact_final(
                expectation, final_path=final_path, digest_provider=digest_provider
            )
            if verification != expected_verification:
                raise OrchestrationError("RESUME_FINAL_DOWNLOAD_INVALID")
            bound_recovery_sha = updated["batches"][batch_id][
                "download_recovery_receipts"
            ].get(key)
            if bound_recovery_sha is not None:
                if sha256_file(recovery_path) != bound_recovery_sha:
                    raise OrchestrationError("RESUME_RECOVERY_RECEIPT_MISMATCH")
                recovery = load_strict_json(recovery_path)
                if not isinstance(recovery, Mapping):
                    raise OrchestrationError("RESUME_RECOVERY_RECEIPT_NOT_MAPPING")
                _validate_recovery_receipt(
                    recovery,
                    expected_common={
                        "schema_version": 2,
                        "artifact_type": "lvef_c3_download_transaction_recovery_v2",
                        "status": "PASS_RECOVERED_VERIFIED_DOWNLOAD_TRANSACTION",
                        "attempt_id": updated["attempt_id"],
                        "batch_id": batch_id,
                        "source_object_key": key,
                        "authority_sha256": canonical_json_sha256(updated["authority"]),
                        "expected_identity_sha256": canonical_json_sha256(
                            {
                                "source_object_key": expectation.source_object_key,
                                "source_relative_path": expectation.source_relative_path,
                                "size_bytes": expectation.size_bytes,
                                "generation": expectation.generation,
                                "md5_base64": expectation.md5_base64,
                                "crc32c_base64": expectation.crc32c_base64,
                            }
                        ),
                        "final_sha256": expected_verification["local_sha256"],
                        "verification_receipt_sha256": already,
                    },
                )
                if (
                    recovery.get("partial_artifact_present") is True
                    and recovery.get("partial_sha256")
                    != expected_verification["local_sha256"]
                ) or (
                    recovery.get("partial_artifact_present") is False
                    and recovery.get("partial_sha256") is not None
                ):
                    raise OrchestrationError("RESUME_RECOVERY_PARTIAL_EVIDENCE_INVALID")
            elif recovery_path.exists() or recovery_path.is_symlink():
                raise OrchestrationError("UNBOUND_RECOVERY_RECEIPT_PRESENT")
            if partial_path.exists() or partial_path.is_symlink():
                if bound_recovery_sha is None or partial_path.is_symlink():
                    raise OrchestrationError("UNBOUND_PARTIAL_AFTER_LEDGER_PROMOTION")
                if sha256_file(partial_path) != expected_verification["local_sha256"]:
                    raise OrchestrationError("RECOVERY_PARTIAL_CHANGED_BEFORE_RETIREMENT")
                partial_path.unlink()
            continue
        if (
            final_path.exists()
            or final_path.is_symlink()
            or receipt_path.exists()
            or receipt_path.is_symlink()
            or recovery_path.exists()
            or recovery_path.is_symlink()
        ):
            updated = recover_unbound_download_transaction(
                updated,
                batch_id=batch_id,
                expectation=expectation,
                final_path=final_path,
                partial_path=partial_path,
                receipt_path=receipt_path,
                recovery_path=recovery_path,
                ledger_root=ledger_root,
                digest_provider=digest_provider,
            )
            continue
        while True:
            attempts_used = updated["batches"][batch_id]["download_attempts"][key] + 1
            if attempts_used > maximum_attempts:
                raise OrchestrationError("DOWNLOAD_REQUEST_BUDGET_EXHAUSTED")
            append_ledger_delta_no_clobber(
                ledger_root,
                updated,
                operation="DOWNLOAD_ATTEMPT",
                payload={
                    "batch_id": batch_id,
                    "source_object_key": key,
                    "attempt_count": attempts_used,
                },
            )
            try:
                current_monotonic = float(monotonic_clock())
                if (
                    token is None
                    or token_acquired_at is None
                    or current_monotonic - token_acquired_at >= token_refresh_seconds
                ):
                    token = token_provider()
                    token_acquired_at = current_monotonic
                transfer_receipt = transport.fetch(
                    expectation,
                    partial_path=partial_path,
                    billing_project=billing_project,
                    access_token=token,
                )
                verification = verify_downloaded_partial(
                    expectation,
                    partial_path=partial_path,
                    transfer_receipt=transfer_receipt,
                    attempt_id=updated["attempt_id"],
                    digest_provider=digest_provider,
                )
            except DownloadTransportError as exc:
                disposition = classify_download_failure(
                    exc.failure_class,
                    attempts_used=attempts_used,
                    maximum_attempts=maximum_attempts,
                )
                if disposition == "FAILED_RETRYABLE":
                    sleeper(
                        retry_backoff_seconds(
                            attempts_used,
                            initial_seconds=backoff_initial,
                            maximum_seconds=backoff_maximum,
                        )
                    )
                    continue
                _record_terminal_download_failure(
                    updated,
                    batch_id=batch_id,
                    source_object_key=key,
                    failure_code=exc.failure_class,
                    attempts_used=attempts_used,
                    receipt_root=receipt_root,
                    ledger_root=ledger_root,
                )
                raise OrchestrationError("DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED") from exc
            except OrchestrationError as exc:
                _record_terminal_download_failure(
                    updated,
                    batch_id=batch_id,
                    source_object_key=key,
                    failure_code="DOWNLOAD_VERIFICATION_FAILURE",
                    attempts_used=attempts_used,
                    receipt_root=receipt_root,
                    ledger_root=ledger_root,
                )
                raise OrchestrationError("DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED") from exc
            except Exception:
                _record_terminal_download_failure(
                    updated,
                    batch_id=batch_id,
                    source_object_key=key,
                    failure_code="UNEXPECTED_TRANSPORT_RUNTIME_FAILURE",
                    attempts_used=attempts_used,
                    receipt_root=receipt_root,
                    ledger_root=ledger_root,
                )
                raise OrchestrationError("DOWNLOAD_FAILED_SANITIZED_UNEXPECTED_RUNTIME") from None
            atomic_finalize_verified_file(
                partial_path=partial_path, final_path=final_path, verification=verification
            )
            receipt_sha = atomic_write_json_no_clobber(
                receipt_path, verification, attempt_id=updated["attempt_id"]
            )
            append_ledger_delta_no_clobber(
                ledger_root,
                updated,
                operation="DOWNLOAD_VERIFIED",
                payload={
                    "batch_id": batch_id,
                    "source_object_key": key,
                    "verification_receipt_sha256": receipt_sha,
                    "recovery_receipt_sha256": None,
                },
            )
            break
    if len(updated["batches"][batch_id]["download_verification_receipts"]) != batch_plan["n_objects"]:
        raise OrchestrationError("BATCH_DOWNLOAD_INCOMPLETE")
    _, manifest_sha = materialize_verified_download_manifest(
        plan=plan, ledger=updated, batch_id=batch_id, batch_root=batch_root
    )
    append_ledger_delta_no_clobber(
        ledger_root,
        updated,
        operation="DOWNLOAD_MANIFEST",
        payload={"batch_id": batch_id, "manifest_sha256": manifest_sha},
    )
    last_event_sha = updated["batches"][batch_id]["events"][-1]["receipt_sha256"]
    verification_hashes = sorted(
        updated["batches"][batch_id]["download_verification_receipts"].values()
    )
    verified_transition = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": updated["attempt_id"],
        "batch_id": batch_id,
        "from_state": "DOWNLOAD_IN_PROGRESS",
        "to_state": "DOWNLOAD_VERIFIED",
        "status": "PASS",
        "authority": updated["authority"],
        "input_receipt_sha256": [last_event_sha, *verification_hashes],
        "output_manifest_sha256": manifest_sha,
    }
    verified_transition_path = (
        receipt_root / "download_verified_transition.restricted.json"
    )
    transition_sha = atomic_write_json_no_clobber(
        verified_transition_path,
        verified_transition,
        attempt_id=updated["attempt_id"],
    )
    if transition_sha != canonical_json_sha256(verified_transition):
        raise OrchestrationError("DOWNLOAD_VERIFIED_TRANSITION_HASH_MISMATCH")
    append_ledger_delta_no_clobber(
        ledger_root,
        updated,
        operation="TRANSITION",
        payload={"receipt": verified_transition},
    )
    return updated


def evaluate_cache_capacity(
    active_batch_bytes: Sequence[int], *, filesystem_available_bytes: int,
    required_reserve_bytes: int, maximum_active_batches: int
) -> dict[str, Any]:
    if not active_batch_bytes or len(active_batch_bytes) > maximum_active_batches:
        raise OrchestrationError("ACTIVE_CACHE_BATCH_COUNT_INVALID")
    if any(isinstance(value, bool) or value <= 0 for value in active_batch_bytes):
        raise OrchestrationError("ACTIVE_CACHE_BYTES_INVALID")
    required = sum(active_batch_bytes)
    remaining = filesystem_available_bytes - required
    passed = remaining >= required_reserve_bytes
    return {
        "active_batches": len(active_batch_bytes),
        "active_cache_bytes": required,
        "filesystem_available_bytes": filesystem_available_bytes,
        "required_reserve_bytes": required_reserve_bytes,
        "remaining_after_cache_bytes": remaining,
        "capacity_gate_passed": passed,
    }


def evaluate_contract_cache_overlap(
    contract: Mapping[str, Any], *, active_batches: int,
    quota_available_bytes: int, filesystem_available_bytes: int,
    two_batch_authorized: bool = False
) -> dict[str, Any]:
    if active_batches not in {1, 2}:
        raise OrchestrationError("CACHE_OVERLAP_BATCH_COUNT_INVALID")
    if active_batches == 2 and not two_batch_authorized:
        return {
            "active_batches": 2,
            "authorized": False,
            "quota_reserve_gate_passed": False,
            "physical_reserve_gate_passed": False,
            "reason": "TWO_BATCH_OVERLAP_NOT_AUTHORIZED",
        }
    cache_bound = int(contract["storage"]["largest_active_extraction_cache_bytes"])
    reserve = int(contract["storage"]["required_free_reserve_bytes"])
    required = active_batches * cache_bound
    quota_gate = quota_available_bytes - required >= reserve
    physical_gate = filesystem_available_bytes - required >= reserve
    return {
        "active_batches": active_batches,
        "authorized": quota_gate and physical_gate,
        "active_extraction_cache_bound_bytes": required,
        "required_reserve_bytes": reserve,
        "quota_available_after_cache_bytes": quota_available_bytes - required,
        "filesystem_available_after_cache_bytes": filesystem_available_bytes - required,
        "quota_reserve_gate_passed": quota_gate,
        "physical_reserve_gate_passed": physical_gate,
        "reason": "PASS" if quota_gate and physical_gate else "RESERVE_GATE_FAILED",
    }


def evaluate_cache_retirement(
    ledger: Mapping[str, Any], *, batch_id: str, target_kind: str,
    contract: Mapping[str, Any], owner_authorization: Mapping[str, Any] | None,
    expected_launch_authority_sha256: str,
) -> dict[str, Any]:
    validate_resume_authority(ledger, expected_authority=ledger["authority"])
    if target_kind == "raw_dicom":
        return {"authorized": False, "reason": "RAW_DICOM_DELETION_PROHIBITED"}
    if target_kind != "extracted_cache":
        raise OrchestrationError("CACHE_RETIREMENT_TARGET_UNKNOWN")
    if batch_id not in ledger["batches"]:
        raise OrchestrationError("CACHE_RETIREMENT_BATCH_NOT_PLANNED")
    batch = ledger["batches"][batch_id]
    reasons: list[str] = []
    if batch["state"] != "CACHE_RETIREMENT_ELIGIBLE":
        reasons.append("BATCH_STATE_NOT_CACHE_RETIREMENT_ELIGIBLE")
    required = set(contract["cache_retirement"]["required_completed_states"])
    if not required.issubset(set(batch["completed_states"])):
        reasons.append("REQUIRED_STAGE_RECEIPT_SEQUENCE_INCOMPLETE")
    expected_owner_keys = {
        "schema_version",
        "artifact_type",
        "status",
        "authorization_scope",
        "owner_authorized",
        "owner_authorization_date_utc",
        "attempt_id",
        "batch_id",
        "authority_sha256",
        "preservation_receipt_sha256",
        "cache_inventory_sha256",
        "launch_authority_sha256",
    }
    if owner_authorization is None or set(owner_authorization) != expected_owner_keys:
        reasons.append("OWNER_CACHE_RETIREMENT_AUTHORIZATION_ABSENT")
    else:
        if (
            owner_authorization.get("schema_version") != 2
            or owner_authorization.get("artifact_type")
            != "lvef_c3_cache_retirement_owner_authorization_v2"
            or owner_authorization.get("status") != "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT"
            or owner_authorization.get("authorization_scope")
            != "EXTRACTED_CACHE_RETIREMENT"
            or owner_authorization.get("owner_authorized") is not True
            or owner_authorization.get("attempt_id") != ledger["attempt_id"]
            or owner_authorization.get("batch_id") != batch_id
            or owner_authorization.get("authority_sha256")
            != canonical_json_sha256(ledger["authority"])
            or owner_authorization.get("launch_authority_sha256")
            != _require_sha256(
                expected_launch_authority_sha256,
                "EXPECTED_LAUNCH_AUTHORITY_HASH_INVALID",
            )
        ):
            reasons.append("OWNER_CACHE_RETIREMENT_AUTHORIZATION_MISMATCH")
        try:
            _parse_utc(
                owner_authorization.get("owner_authorization_date_utc"),
                "OWNER_CACHE_RETIREMENT_AUTHORIZATION_DATE_INVALID",
            )
        except OrchestrationError:
            reasons.append("OWNER_CACHE_RETIREMENT_AUTHORIZATION_DATE_INVALID")
        for key in ("preservation_receipt_sha256", "cache_inventory_sha256"):
            try:
                _require_sha256(owner_authorization.get(key), f"OWNER_{key.upper()}_INVALID")
            except OrchestrationError:
                reasons.append("OWNER_CACHE_RETIREMENT_HASH_INVALID")
                break
    return {
        "authorized": not reasons,
        "reason": "PASS" if not reasons else reasons[0],
        "all_reasons": reasons,
        "raw_dicom_deletion_permitted": False,
    }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    content = read_regular_bytes(path).decode("utf-8-sig")
    reader = csv.reader(content.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        raise OrchestrationError("CSV_EMPTY") from None
    if not header or any(not value for value in header) or len(header) != len(set(header)):
        raise OrchestrationError("CSV_HEADER_INVALID_OR_DUPLICATE")
    rows: list[dict[str, str]] = []
    for values in reader:
        if len(values) != len(header):
            raise OrchestrationError("CSV_ROW_WIDTH_MISMATCH")
        rows.append(dict(zip(header, values)))
    return rows


def _read_jsonl_rows(path: Path) -> list[Mapping[str, Any]]:
    content = read_regular_bytes(path).decode("utf-8")
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line:
            raise OrchestrationError("JSONL_BLANK_LINE")
        try:
            row = json.loads(line, object_pairs_hook=_strict_pairs)
        except json.JSONDecodeError as exc:
            raise OrchestrationError("JSONL_INVALID") from exc
        if not isinstance(row, Mapping):
            raise OrchestrationError("JSONL_ROW_NOT_MAPPING")
        rows.append(row)
    if not rows:
        raise OrchestrationError("JSONL_EMPTY")
    return rows


def _main_build_plan(args: argparse.Namespace) -> None:
    contract = load_orchestration_contract(args.contract)
    authority = load_strict_json(args.authority)
    if not isinstance(authority, Mapping):
        raise OrchestrationError("PLAN_AUTHORITY_NOT_MAPPING")
    validate_plan_authority_against_contract(
        authority, contract=contract, contract_path=args.contract
    )
    expected_hashes = {
        "selected": authority.get("selected_manifest_sha256"),
        "source": authority.get("selected_source_manifest_sha256"),
        "source_metadata": authority.get("source_metadata_sha256"),
        "split": authority.get("split_map_sha256"),
    }
    for label, path in (
        ("selected", args.selected),
        ("source", args.source),
        ("source_metadata", args.source_metadata),
        ("split", args.split),
    ):
        if sha256_file(path) != expected_hashes[label]:
            raise OrchestrationError(f"{label.upper()}_MANIFEST_HASH_MISMATCH")
    if (
        args.output.exists()
        or args.output.is_symlink()
        or args.aggregate_output.exists()
        or args.aggregate_output.is_symlink()
    ):
        raise OrchestrationError("OUTPUT_PAIR_ALREADY_EXISTS_NO_CLOBBER")
    source_rows = reconcile_selected_source_metadata(
        _read_csv_rows(args.source),
        _read_jsonl_rows(args.source_metadata),
        release=str(contract["cohort"]["release"]),
    )
    plan = build_immutable_batch_plan(
        _read_csv_rows(args.selected),
        source_rows,
        _read_csv_rows(args.split),
        requirements=production_requirements(contract),
        authority=authority,
    )
    digest = atomic_write_json_no_clobber(args.output, plan, attempt_id=args.attempt_id)
    aggregate = aggregate_batch_plan(plan, requirements=production_requirements(contract))
    aggregate_digest = atomic_write_json_no_clobber(
        args.aggregate_output, aggregate, attempt_id=args.attempt_id
    )
    print("C3_OFFLINE_BATCH_PLAN=PASS")
    print(f"BATCH_PLAN_SHA256={digest}")
    print(f"BATCH_PLAN_AGGREGATE_SHA256={aggregate_digest}")
    print("CLOUD_REQUESTS=0")


def _main_initialize_ledger(args: argparse.Namespace) -> None:
    contract = load_orchestration_contract(args.contract)
    plan = load_strict_json(args.plan)
    authority = load_strict_json(args.authority)
    if not isinstance(plan, Mapping) or not isinstance(authority, Mapping):
        raise OrchestrationError("LEDGER_INPUT_NOT_MAPPING")
    validate_plan_authority_against_contract(
        plan["authority"], contract=contract, contract_path=args.contract
    )
    ledger = initialize_resume_ledger(
        plan,
        requirements=production_requirements(contract),
        attempt_id=args.attempt_id,
        authority=authority,
        batch_ids=[args.batch_id] if args.batch_id is not None else None,
    )
    digest = atomic_write_json_no_clobber(
        args.output, ledger, attempt_id=args.attempt_id
    )
    print("C3_RESUME_LEDGER_INITIALIZED=YES")
    print(f"RESUME_LEDGER_SHA256={digest}")
    print("CLOUD_REQUESTS=0")


def _main_transition(args: argparse.Namespace) -> None:
    ledger = load_strict_json(args.ledger)
    receipt = load_strict_json(args.receipt)
    if not isinstance(ledger, Mapping) or not isinstance(receipt, Mapping):
        raise OrchestrationError("TRANSITION_INPUT_NOT_MAPPING")
    updated = apply_transition(ledger, receipt)
    digest = atomic_write_json_no_clobber(
        args.output, updated, attempt_id=str(updated["attempt_id"])
    )
    print("C3_STATE_TRANSITION=PASS")
    print(f"RESUME_LEDGER_SHA256={digest}")
    print("CLOUD_REQUESTS=0")


def _main_download_batch(args: argparse.Namespace, raw_argv: Sequence[str]) -> None:
    contract = load_orchestration_contract(args.contract)
    plan = load_strict_json(args.plan)
    ledger = load_strict_json(args.ledger)
    authorization = load_strict_json(args.authorization_receipt)
    if not all(isinstance(value, Mapping) for value in (plan, ledger, authorization)):
        raise OrchestrationError("DOWNLOAD_INPUT_NOT_MAPPING")
    validate_plan_authority_against_contract(
        plan["authority"], contract=contract, contract_path=args.contract
    )
    current_authority = validate_ledger_against_current_runtime(
        ledger,
        plan=plan,
        requirements=production_requirements(contract),
        contract=contract,
        contract_path=args.contract,
        governing_commit=args.governing_commit,
        environment_receipt_sha256=sha256_file(args.environment_receipt),
        batch_id=args.batch_id,
    )
    downloader = contract["downloader"]
    private_names = {
        key: str(downloader[key])
        for key in (
            "cloudsdk_config_environment_variable",
            "cloudsdk_config_receipt_environment_variable",
            "cloudsdk_config_receipt_sha256_environment_variable",
        )
    }
    if any(name not in os.environ or not os.environ[name] for name in private_names.values()):
        raise OrchestrationError("PRIVATE_CLOUDSDK_AUTHORITY_ENVIRONMENT_MISSING")
    token_provider = GcloudADCTokenProvider(
        args.gcloud_binary,
        cloudsdk_config=Path(
            os.environ[private_names["cloudsdk_config_environment_variable"]]
        ),
        authority_receipt=Path(
            os.environ[private_names["cloudsdk_config_receipt_environment_variable"]]
        ),
        authority_receipt_sha256=os.environ[
            private_names["cloudsdk_config_receipt_sha256_environment_variable"]
        ],
    )
    validate_gcloud_runtime_authority(
        token_provider.validate_authority(),
        expected_runtime_authority=current_authority,
    )
    canonical_worker = Path(__file__).resolve(strict=True).with_name(
        "lvef_c3_crc32c_worker.py"
    )
    if args.crc32c_worker.resolve(strict=True) != canonical_worker:
        raise OrchestrationError("CRC32C_WORKER_NOT_AUTHORITY_BOUND")
    with ExternalCRC32CDigestWorker(
        python_executable=args.crc32c_python,
        worker_script=args.crc32c_worker,
        expected_python_sha256=current_authority[
            "crc32c_python_executable_sha256"
        ],
        expected_worker_sha256=current_authority["crc32c_worker_sha256"],
        expected_distribution_sha256=current_authority[
            "crc32c_distribution_sha256"
        ],
    ) as digest_worker:
        updated = execute_exact_batch_download(
            plan=plan,
            requirements=production_requirements(contract),
            ledger=ledger,
            contract=contract,
            batch_id=args.batch_id,
            expected_runtime_authority=current_authority,
            authorization_receipt=authorization,
            output_root=args.output_root,
            launch_authority_sha256=args.launch_authority_sha256,
            argv=raw_argv,
            token_provider=token_provider,
            transport=GCSExactObjectBodyTransport(),
            digest_provider=digest_worker.digest,
        )
    digest = atomic_write_json_no_clobber(
        args.ledger_output, updated, attempt_id=str(updated["attempt_id"])
    )
    verified = len(
        updated["batches"][args.batch_id]["download_verification_receipts"]
    )
    print("C3_EXACT_BATCH_DOWNLOAD=PASS")
    print(f"BATCH_ID={args.batch_id}")
    print(f"VERIFIED_OBJECT_COUNT={verified}")
    print(
        "VERIFIED_DOWNLOAD_MANIFEST_SHA256="
        f"{updated['batches'][args.batch_id]['download_manifest_sha256']}"
    )
    print(f"RESUME_LEDGER_SHA256={digest}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-contract")
    validate.add_argument("--contract", type=Path, required=True)
    plan = subparsers.add_parser("build-plan")
    plan.add_argument("--contract", type=Path, required=True)
    plan.add_argument("--selected", type=Path, required=True)
    plan.add_argument("--source", type=Path, required=True)
    plan.add_argument("--source-metadata", type=Path, required=True)
    plan.add_argument("--split", type=Path, required=True)
    plan.add_argument("--authority", type=Path, required=True)
    plan.add_argument("--attempt-id", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--aggregate-output", type=Path, required=True)
    ledger = subparsers.add_parser("init-ledger")
    ledger.add_argument("--contract", type=Path, required=True)
    ledger.add_argument("--plan", type=Path, required=True)
    ledger.add_argument("--authority", type=Path, required=True)
    ledger.add_argument("--attempt-id", required=True)
    ledger.add_argument("--batch-id")
    ledger.add_argument("--output", type=Path, required=True)
    transition = subparsers.add_parser("transition")
    transition.add_argument("--ledger", type=Path, required=True)
    transition.add_argument("--receipt", type=Path, required=True)
    transition.add_argument("--output", type=Path, required=True)
    download = subparsers.add_parser("download-batch")
    download.add_argument("--contract", type=Path, required=True)
    download.add_argument("--plan", type=Path, required=True)
    download.add_argument("--ledger", type=Path, required=True)
    download.add_argument("--authorization-receipt", type=Path, required=True)
    download.add_argument("--launch-authority-sha256", required=True)
    download.add_argument("--batch-id", required=True)
    download.add_argument("--governing-commit", required=True)
    download.add_argument("--environment-receipt", type=Path, required=True)
    download.add_argument("--output-root", type=Path, required=True)
    download.add_argument("--gcloud-binary", type=Path, required=True)
    download.add_argument("--crc32c-python", type=Path, required=True)
    download.add_argument("--crc32c-worker", type=Path, required=True)
    download.add_argument("--ledger-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)
    try:
        if args.command == "validate-contract":
            load_orchestration_contract(args.contract)
            print("C3_ORCHESTRATION_CONTRACT_V2=PASS_OFFLINE_UNAUTHORIZED")
            print("CLOUD_REQUESTS=0")
        elif args.command == "build-plan":
            _main_build_plan(args)
        elif args.command == "init-ledger":
            _main_initialize_ledger(args)
        elif args.command == "transition":
            _main_transition(args)
        elif args.command == "download-batch":
            _main_download_batch(args, raw_argv)
        else:
            raise OrchestrationError("COMMAND_NOT_IMPLEMENTED")
    except OrchestrationError as exc:
        print(f"C3_ORCHESTRATION_REFUSED={exc}", file=sys.stderr)
        return 78
    except Exception:
        print(
            "C3_ORCHESTRATION_REFUSED=UNEXPECTED_RUNTIME_FAILURE",
            file=sys.stderr,
        )
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
