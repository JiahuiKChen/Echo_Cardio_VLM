#!/usr/bin/env python3
"""Fail-closed production wrappers for the C3 imaging stages.

The dependency-light validation surface is safe to exercise offline.  Real
DICOM decoding and EchoPrime inference are reachable only through explicit
subcommands that require a restricted, stage-specific owner-authorization
receipt.  Heavy dependencies are imported only after every authority and path
gate has passed.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CHECKPOINT_FILENAME = "echo_prime_encoder.pt"
CHECKPOINT_BYTES = 138_642_379
CHECKPOINT_SHA256 = (
    "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
)
EXPECTED_EMBEDDING_DIMENSION = 512
EXPECTED_EXTRACTED_SHAPE = (32, 224, 224, 3)
ORDINARY_TEMPORAL_SAMPLING_POLICY = (
    "historical_compatible_linspace_or_tail_repeat_v1"
)
FALLBACK_TEMPORAL_SAMPLING_POLICY = "stride2_signal_coverage_pair_repeat_v1"
ORDINARY_PREPROCESSING_PATH = (
    "ORDINARY_CENTER_CROP_RESIZE_HISTORICAL_TEMPORAL_V1"
)
SPATIAL_FALLBACK_PREPROCESSING_PATH = (
    "SECTOR_BOUND_SQUARE_PAD_SPATIAL_FALLBACK_V1"
)
TEMPORAL_FALLBACK_PREPROCESSING_PATH = (
    "STRIDE2_SIGNAL_COVERAGE_TEMPORAL_FALLBACK_V1"
)
SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH = (
    "SECTOR_BOUND_SQUARE_PAD_AND_STRIDE2_SIGNAL_COVERAGE_FALLBACK_V1"
)
ALLOWED_PREPROCESSING_PATHS = {
    ORDINARY_PREPROCESSING_PATH,
    SPATIAL_FALLBACK_PREPROCESSING_PATH,
    TEMPORAL_FALLBACK_PREPROCESSING_PATH,
    SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
}
FALLBACK_NOT_ATTEMPTED = "NOT_ATTEMPTED"
FALLBACK_PATH_PASS = "FALLBACK_PATH_PASS"
FALLBACK_PATH_FAILED = "FALLBACK_PATH_FAILED"
ALLOWED_FAILURE_SUBSTAGES = {
    "DECODE_OR_COLOR_CONVERSION_FAILURE",
    "SOURCE_SIGNAL_QUALITY_FAILURE",
    "SPATIAL_CROP_RESIZE_FAILURE",
    "POST_CROP_SIGNAL_QUALITY_FAILURE",
    "TEMPORAL_SAMPLING_FAILURE",
    "SAMPLED_NONZERO_SIGNAL_FAILURE",
    "SAMPLED_TEMPORAL_VARIATION_FAILURE",
    "OUTPUT_WRITE_FAILURE",
}
OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION = (
    "source_signal_object_technical_disposition_v2"
)
LEGACY_OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION = (
    "source_signal_object_technical_disposition_v1"
)
OBJECT_TECHNICAL_DISPOSITION_ABSOLUTE_LIMIT = 10
OBJECT_TECHNICAL_DISPOSITION_RATE_DENOMINATOR = 1000
OBJECT_TECHNICAL_DISPOSITION = (
    "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR"
)
CLIP_KEY_NAMESPACE = "mimic-iv-echo-1.0:prospective-cine-v1"
SUCCESSFUL_EXTRACTION = "SUCCESSFUL_EXTRACTION"
APPROVED_OBJECT_TECHNICAL_DISPOSITION = (
    "APPROVED_OBJECT_TECHNICAL_DISPOSITION"
)
BLOCKING_FAILURE = "BLOCKING_FAILURE"
TECHNICAL_DISPOSITION_MANIFEST_HEADER = (
    "subject_id",
    "study_id",
    "clip_key",
    "physical_source_key",
    "failure_substage",
    "technical_disposition",
    "selected_source_membership_passed",
    "batch_plan_membership_passed",
    "download_integrity_authority_passed",
    "dicom_header_readable",
    "pixel_decode_ok",
    "decode_color_status",
    "canonical_color_space",
    "raw_dicom_retained",
    "npz_absent",
    "embedding_absent",
    "object_substitution",
    "study_retains_valid_cine_coverage",
    "technical_disposition_policy_version",
)
DICOM_EXTRACTION_SUMMARY_KEYS_V2 = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "n_objects",
        "n_studies",
        "n_readable",
        "n_unreadable",
        "n_multiframe_candidates",
        "n_single_frame",
        "n_pixel_decode_failures",
        "physical_source_keys_unique",
        "n_extracted_clips",
        "n_studies_with_extracted_clips",
        "clip_keys_unique",
        "all_shapes_and_dtypes_valid",
        "all_pixel_decodes_passed",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "n_fallback_path_pass",
        "n_fallback_path_failed",
        "all_fallback_encoder_visible_signal_gates_passed",
        "n_requested_cines",
        "n_successfully_extracted_cines",
        "n_successfully_extracted_clips",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_studies_affected_by_technical_disposition",
        "n_new_no_cine_studies",
        "technical_disposition_counts_by_class",
        "all_extraction_rows_resolved",
        "all_successful_extractions_embeddable",
        "all_technical_dispositions_retained",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        "all_failure_substages_none",
        "all_source_signal_gates_passed",
        "all_post_crop_signal_gates_passed",
        "all_sampled_signal_gates_passed",
        "successful_extraction_gate_scope",
        "requested_cine_resolution_scope",
        "technical_disposition_policy_version",
        "technical_disposition_manifest_sha256",
        "identifiers_emitted",
        "paths_emitted",
    }
)
ECHOPRIME_SUMMARY_KEYS_V2 = frozenset(
    {
        "schema_version", "artifact_type", "status", "n_clip_embeddings",
        "n_pooled_studies", "n_no_cine_studies", "n_new_no_cine_studies",
        "prespecified_no_cine_study_set_sha256",
        "actual_no_cine_study_set_sha256",
        "all_no_cine_studies_prespecified",
        "n_object_technical_dispositions",
        "n_studies_affected_by_technical_disposition",
        "technical_disposition_counts_by_class",
        "technical_disposition_policy_version",
        "technical_disposition_manifest_sha256",
        "all_extraction_rows_resolved",
        "all_successful_extractions_embedded",
        "all_technical_dispositions_retained", "object_substitution_count",
        "unaccounted_multiframe_objects", "embedding_dimension",
        "embedding_dtype", "all_finite", "encoder_only",
        "view_classifier_used", "pooling", "checkpoint_sha256",
        "identifiers_emitted", "paths_emitted",
    }
)
SOURCE_SIGNAL_COUNT_GATE_PAIRS = (
    ("source_sector_pixel_count", "source_sector_nonempty_gate_passed"),
    (
        "source_nonzero_retained_pixel_count",
        "source_nonzero_retained_pixel_gate_passed",
    ),
    (
        "source_temporal_variation_pixel_count",
        "source_temporal_variation_gate_passed",
    ),
)
DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS = (
    (
        "ordinary_post_crop_nonzero_retained_pixel_count",
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed",
    ),
    (
        "ordinary_post_crop_temporal_variation_pixel_count",
        "ordinary_post_crop_temporal_variation_gate_passed",
    ),
    (
        "post_crop_nonzero_retained_pixel_count",
        "post_crop_nonzero_retained_pixel_gate_passed",
    ),
    (
        "post_crop_temporal_variation_pixel_count",
        "post_crop_temporal_variation_gate_passed",
    ),
    (
        "ordinary_sampled_nonzero_retained_pixel_count",
        "ordinary_sampled_nonzero_retained_pixel_gate_passed",
    ),
    (
        "ordinary_sampled_temporal_variation_pixel_count",
        "ordinary_sampled_temporal_variation_gate_passed",
    ),
    (
        "sampled_nonzero_retained_pixel_count",
        "sampled_nonzero_retained_pixel_gate_passed",
    ),
    (
        "sampled_temporal_variation_pixel_count",
        "sampled_temporal_variation_gate_passed",
    ),
    (
        "encoder_visible_nonzero_retained_pixel_count",
        "encoder_visible_nonzero_retained_pixel_gate_passed",
    ),
    (
        "encoder_visible_temporal_variation_pixel_count",
        "encoder_visible_temporal_variation_gate_passed",
    ),
)
EXTRACTION_PRODUCER_FIELDS = frozenset(
    {
        "subject_id", "study_id", "smoke_role", "source_relative_path",
        "source_sha256", "clip_key", "output_relative_path", "write_ok",
        "mask_status", "photometric_interpretation", "transfer_syntax_uid",
        "decoder_backend", "decoder_color_behavior", "color_transform",
        "canonical_color_space", "selected_preprocessing_path",
        "fallback_status", "failure_substage", "decode_color_status",
        "temporal_sampling_policy", "frames_shape", "frames_dtype",
        "frames_sha256", "sampled_indices_sha256", "source_num_frames_sha256",
        "npz_sha256", "source_num_frames", "error_code",
        "physical_source_key", "pixel_decode_ok",
        *(count for count, _gate in SOURCE_SIGNAL_COUNT_GATE_PAIRS),
        *(gate for _count, gate in SOURCE_SIGNAL_COUNT_GATE_PAIRS),
        *(count for count, _gate in DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS),
        *(gate for _count, gate in DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS),
    }
)
PROJECTNB_PREFIX = Path("/restricted/projectnb")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BATCH_RE = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
CANONICAL_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PINNED_CRC32C_PYTHON = Path(
    "/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/"
    "google-cloud-sdk/platform/bundledpythonunix/bin/python3.14"
)

AUTHORIZATION_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "authorization_scope",
    "stage",
    "batch_id",
    "attempt_id",
    "governing_commit",
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "launch_authority_sha256",
    "owner_authorized",
    "owner_authorization_date",
}
WRAPPER_STAGES = {"DICOM_EXTRACTION", "ECHOPRIME_EMBEDDING"}
SCIENTIFIC_AUTHORIZATION_STAGES = {
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "PRESERVATION_FINALIZATION",
}
STAGE_AUTHORIZATION_SCOPES = {
    "DICOM_EXTRACTION": "EXTRACTION_AUTHORIZATION",
    "ECHOPRIME_EMBEDDING": "ECHOPRIME_INFERENCE_AUTHORIZATION",
    "BATCH_PRESERVATION": "BATCH_PRESERVATION_AUTHORIZATION",
    "PRESERVATION_FINALIZATION": "PRESERVATION_FINALIZATION_AUTHORIZATION",
}
ENVIRONMENT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "governing_commit",
        "captured_at_utc",
        "source_environment_receipt_sha256",
        "python_executable_sha256",
        "python_version",
        "torch_version",
        "torchvision_version",
        "cuda_version",
        "cudnn_version",
        "crc32c_runtime_source",
        "crc32c_python_executable_sha256",
        "crc32c_python_version",
        "crc32c_worker_sha256",
        "crc32c_worker_protocol_version",
        "google_crc32c_version",
        "google_crc32c_implementation",
        "google_crc32c_distribution_sha256",
        "google_crc32c_distribution_file_count",
        "google_crc32c_known_vector_base64",
        "package_inventory_sha256",
        "package_count",
        "package_inventory",
        "operating_system",
        "gpu_execution_performed",
        "cloud_request_performed",
        "dicom_body_read",
        "model_fitted",
        "prediction_generated",
        "confirmatory_performance_accessed",
    }
)


class ProductionStageError(RuntimeError):
    """Fail-closed stage validation error with a stable, non-sensitive code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExtractionDispositionContext:
    """Authority derived from plan, download, DICOM, and filesystem evidence."""

    approved_source_keys: frozenset[str]
    raw_retained_source_keys: frozenset[str]
    npz_absent_clip_keys: frozenset[str]
    embedding_absent_clip_keys: frozenset[str]
    study_success_counts: tuple[tuple[str, int], ...]
    source_num_frames_by_key: tuple[tuple[str, int], ...]
    object_substitution_count: int
    policy_version: str = OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProductionStageError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def load_json_object(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError(f"{code}_NOT_REGULAR")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except ProductionStageError:
        raise
    except Exception as exc:
        raise ProductionStageError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ProductionStageError(f"{code}_NOT_OBJECT")
    return value


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("HASH_INPUT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_nonsymlink_existing_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_PATH_TOPOLOGY_INVALID"
            )


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _stable_nofollow_sha256_authority(
    path: Path,
) -> tuple[str, tuple[int, ...]]:
    _require_nonsymlink_existing_ancestors(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID"
            )
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_RAW_IDENTITY_CHANGED"
            )
        return digest.hexdigest(), _file_identity(after)
    finally:
        os.close(descriptor)


def _stable_nofollow_identity(path: Path) -> tuple[int, ...]:
    _require_nonsymlink_existing_ancestors(path)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID"
            )
        return _file_identity(metadata)
    finally:
        os.close(descriptor)


def _read_canonical_dicom_audit_authority(
    *, extraction_manifest: Path, dicom_audit: Path | None
) -> bytes:
    """Read only the completed extraction stage's canonical DICOM audit."""

    canonical = extraction_manifest.parent / "dicom_audit.restricted.csv"
    supplied = canonical if dicom_audit is None else Path(dicom_audit)
    if Path(os.path.abspath(supplied)) != Path(os.path.abspath(canonical)):
        raise ProductionStageError("ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH")
    _require_nonsymlink_existing_ancestors(canonical)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(canonical.parent, parent_flags)
    except OSError as exc:
        raise ProductionStageError(
            "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
        ) from exc

    def parent_identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    try:
        parent_before = os.fstat(parent_descriptor)
        visible_parent_before = canonical.parent.lstat()
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or parent_identity(parent_before)
            != parent_identity(visible_parent_before)
        ):
            raise ProductionStageError(
                "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
            )

        def read_once() -> tuple[tuple[int, ...], bytes]:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(
                    canonical.name, flags, dir_fd=parent_descriptor
                )
            except OSError as exc:
                raise ProductionStageError(
                    "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
                ) from exc
            try:
                before = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.getuid()
                    or before.st_nlink != 1
                    or before.st_size < 1
                    or before.st_size > 64_000_000
                ):
                    raise ProductionStageError(
                        "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    block = os.read(
                        descriptor, min(1024 * 1024, 64_000_001 - total)
                    )
                    if not block:
                        break
                    chunks.append(block)
                    total += len(block)
                    if total > 64_000_000:
                        raise ProductionStageError(
                            "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
                        )
                after = os.fstat(descriptor)
                before_identity = _file_identity(before)
                if before_identity != _file_identity(after):
                    raise ProductionStageError(
                        "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
                    )
                return before_identity, b"".join(chunks)
            finally:
                os.close(descriptor)

        first_identity, payload = read_once()
        second_identity, second_payload = read_once()
        parent_after = os.fstat(parent_descriptor)
        visible_parent_after = canonical.parent.lstat()
        if (
            first_identity != second_identity
            or payload != second_payload
            or parent_identity(parent_before) != parent_identity(parent_after)
            or parent_identity(parent_before)
            != parent_identity(visible_parent_after)
        ):
            raise ProductionStageError(
                "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
            )
        return payload
    except ProductionStageError:
        raise
    except OSError as exc:
        raise ProductionStageError(
            "ECHOPRIME_DICOM_AUDIT_AUTHORITY_MISMATCH"
        ) from exc
    finally:
        os.close(parent_descriptor)


def _require_nofollow_absent(path: Path, *, code: str) -> None:
    _require_nonsymlink_existing_ancestors(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ProductionStageError(code)


def write_technical_disposition_manifest_no_clobber(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> None:
    """Publish the restricted CSV once, without rename-overwrite semantics."""

    _require_nonsymlink_existing_ancestors(path)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(TECHNICAL_DISPOSITION_MANIFEST_HEADER),
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        if set(row) != set(TECHNICAL_DISPOSITION_MANIFEST_HEADER):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_SCHEMA_MISMATCH"
            )
        writer.writerow(row)
    payload = buffer.getvalue().encode("utf-8")
    temporary_name = f".{path.name}.tmp.{os.getpid()}.{os.urandom(12).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
        ) from exc
    descriptor = -1
    published = False
    completed: os.stat_result | None = None
    try:
        parent_before = os.fstat(parent_descriptor)
        try:
            visible_parent = path.parent.lstat()
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
            ) from exc
        parent_identity = (
            parent_before.st_dev,
            parent_before.st_ino,
            parent_before.st_mode,
            parent_before.st_uid,
            parent_before.st_gid,
        )
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or parent_identity
            != (
                visible_parent.st_dev,
                visible_parent.st_ino,
                visible_parent.st_mode,
                visible_parent.st_uid,
                visible_parent.st_gid,
            )
        ):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
            )
        try:
            descriptor = os.open(
                temporary_name, flags, 0o600, dir_fd=parent_descriptor
            )
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_NO_CLOBBER_FAILED"
            ) from exc
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_uid != os.getuid()
            or created.st_nlink != 1
            or stat.S_IMODE(created.st_mode) != 0o600
        ):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
            )
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            (created.st_dev, created.st_ino)
            != (completed.st_dev, completed.st_ino)
            or completed.st_size != len(payload)
            or completed.st_uid != os.getuid()
            or completed.st_nlink != 1
            or stat.S_IMODE(completed.st_mode) != 0o600
        ):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
            )
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_NO_CLOBBER_FAILED"
            ) from exc
        published = True
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        try:
            metadata = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            visible_parent_after = path.parent.lstat()
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
            ) from exc
        parent_after = os.fstat(parent_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (completed.st_dev, completed.st_ino)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or parent_identity
            != (
                parent_after.st_dev,
                parent_after.st_ino,
                parent_after.st_mode,
                parent_after.st_uid,
                parent_after.st_gid,
            )
            or parent_identity
            != (
                visible_parent_after.st_dev,
                visible_parent_after.st_ino,
                visible_parent_after.st_mode,
                visible_parent_after.st_uid,
                visible_parent_after.st_gid,
            )
        ):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
            )
    except BaseException:
        if published and completed is not None:
            try:
                visible = os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (visible.st_dev, visible.st_ino) == (
                    completed.st_dev,
                    completed.st_ino,
                ):
                    os.unlink(path.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except OSError:
            pass
        os.close(parent_descriptor)


def read_technical_disposition_manifest_authority(
    path: Path,
) -> tuple[list[dict[str, str]], str]:
    _require_nonsymlink_existing_ancestors(path)

    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
        ) from exc
    try:
        parent_before = os.fstat(parent_descriptor)
        visible_parent = path.parent.lstat()
    except OSError as exc:
        os.close(parent_descriptor)
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
        ) from exc
    parent_identity = (
        parent_before.st_dev, parent_before.st_ino, parent_before.st_mode,
        parent_before.st_uid, parent_before.st_gid, parent_before.st_nlink,
        parent_before.st_size, parent_before.st_mtime_ns, parent_before.st_ctime_ns,
    )
    if (
        not stat.S_ISDIR(parent_before.st_mode)
        or parent_identity
        != (
            visible_parent.st_dev, visible_parent.st_ino, visible_parent.st_mode,
            visible_parent.st_uid, visible_parent.st_gid, visible_parent.st_nlink,
            visible_parent.st_size, visible_parent.st_mtime_ns,
            visible_parent.st_ctime_ns,
        )
    ):
        os.close(parent_descriptor)
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_PARENT_INVALID"
        )

    def read_once() -> tuple[tuple[int, ...], bytes]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_NOT_REGULAR"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size < 1
                or before.st_size > 16_000_000
            ):
                raise ProductionStageError(
                    "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                block = os.read(descriptor, min(1024 * 1024, 16_000_001 - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if total > 16_000_000:
                    raise ProductionStageError(
                        "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID"
                    )
            after = os.fstat(descriptor)
            identity = (
                before.st_dev, before.st_ino, before.st_mode, before.st_uid,
                before.st_nlink, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns,
            )
            if identity != (
                after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                after.st_nlink, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ProductionStageError(
                    "TECHNICAL_DISPOSITION_MANIFEST_IDENTITY_CHANGED"
                )
            return identity, b"".join(chunks)
        finally:
            os.close(descriptor)

    try:
        first_identity, payload = read_once()
        second_identity, second_payload = read_once()
        parent_after = os.fstat(parent_descriptor)
        try:
            visible_parent_after = path.parent.lstat()
        except OSError as exc:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_PARENT_CHANGED"
            ) from exc
    finally:
        os.close(parent_descriptor)
    if (
        first_identity != second_identity
        or payload != second_payload
        or parent_identity
        != (
            parent_after.st_dev, parent_after.st_ino, parent_after.st_mode,
            parent_after.st_uid, parent_after.st_gid, parent_after.st_nlink,
            parent_after.st_size, parent_after.st_mtime_ns, parent_after.st_ctime_ns,
        )
        or parent_identity
        != (
            visible_parent_after.st_dev, visible_parent_after.st_ino,
            visible_parent_after.st_mode, visible_parent_after.st_uid,
            visible_parent_after.st_gid, visible_parent_after.st_nlink,
            visible_parent_after.st_size, visible_parent_after.st_mtime_ns,
            visible_parent_after.st_ctime_ns,
        )
    ):
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_IDENTITY_CHANGED"
        )
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8"), newline=""))
        header = next(reader)
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_SCHEMA_MISMATCH"
        ) from exc
    if header != list(TECHNICAL_DISPOSITION_MANIFEST_HEADER):
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_SCHEMA_MISMATCH"
        )
    rows: list[dict[str, str]] = []
    try:
        for values in reader:
            if len(values) != len(header):
                raise ProductionStageError(
                    "TECHNICAL_DISPOSITION_MANIFEST_ROW_WIDTH_MISMATCH"
                )
            rows.append(dict(zip(header, values)))
    except csv.Error as exc:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_SCHEMA_MISMATCH"
        ) from exc
    return rows, hashlib.sha256(payload).hexdigest()


def read_technical_disposition_manifest(path: Path) -> list[dict[str, str]]:
    return read_technical_disposition_manifest_authority(path)[0]


def technical_disposition_manifest_sha256(path: Path) -> str:
    return read_technical_disposition_manifest_authority(path)[1]


def resolved_python_executable_sha256(path: Path) -> str:
    """Hash the regular target of an expected virtual-environment symlink."""
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProductionStageError("PYTHON_EXECUTABLE_RESOLUTION_FAILED") from exc
    return sha256_file(resolved)


def _validate_hash(value: Any, code: str) -> str:
    text = str(value)
    if not SHA256_RE.fullmatch(text):
        raise ProductionStageError(code)
    return text


def safe_relative_path(value: Any) -> str:
    text = str(value)
    if (
        not text
        or text != text.strip()
        or text.startswith(("/", "~"))
        or "\\" in text
        or any(ord(character) < 32 for character in text)
    ):
        raise ProductionStageError("UNSAFE_RELATIVE_PATH")
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != text:
        raise ProductionStageError("UNSAFE_RELATIVE_PATH")
    return text


def require_projectnb_path(path: Path, *, must_exist: bool = False) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ProductionStageError("OUTPUT_ROOT_NOT_ABSOLUTE_REGULAR_PROJECTNB_PATH")
    try:
        relative = path.relative_to(PROJECTNB_PREFIX)
    except ValueError as exc:
        raise ProductionStageError("OUTPUT_ROOT_OUTSIDE_PROJECTNB") from exc
    cursor = PROJECTNB_PREFIX
    for part in relative.parts[:-1] if relative.parts else ():
        cursor /= part
        if cursor.exists() or cursor.is_symlink():
            try:
                metadata = os.lstat(cursor)
            except OSError as exc:
                raise ProductionStageError("OUTPUT_ROOT_ANCESTOR_INVALID") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ProductionStageError("OUTPUT_ROOT_SYMLINK_ANCESTOR")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ProductionStageError("OUTPUT_ROOT_ANCESTOR_INVALID")
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(PROJECTNB_PREFIX)
    except ValueError as exc:
        raise ProductionStageError("OUTPUT_ROOT_OUTSIDE_PROJECTNB") from exc
    if must_exist and not resolved.is_dir():
        raise ProductionStageError("OUTPUT_ROOT_NOT_DIRECTORY")
    return resolved


def require_authority_worktree(path: Path, governing_commit: str) -> Path:
    if not COMMIT_RE.fullmatch(governing_commit):
        raise ProductionStageError("INVALID_GOVERNING_COMMIT")
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ProductionStageError("AUTHORITY_WORKTREE_INVALID")
    required = (
        path / "scripts" / "lvef_c3_orchestration_core.py",
        path / "scripts" / "lvef_c3_production_stages.py",
        path / "scripts" / "lvef_reconstruction_smoke.py",
    )
    if any(item.is_symlink() or not item.is_file() for item in required):
        raise ProductionStageError("AUTHORITY_WORKTREE_HELPER_MISSING")
    def git_value(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments], capture_output=True, text=True,
            check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode != 0:
            raise ProductionStageError("AUTHORITY_WORKTREE_GIT_CHECK_FAILED")
        return result.stdout.strip()
    if git_value("branch", "--show-current") != "codex/lvef-multitask-revalidation":
        raise ProductionStageError("AUTHORITY_WORKTREE_BRANCH_MISMATCH")
    if git_value("rev-parse", "HEAD") != governing_commit:
        raise ProductionStageError("AUTHORITY_WORKTREE_COMMIT_MISMATCH")
    if git_value("status", "--porcelain", "--untracked-files=no"):
        raise ProductionStageError("AUTHORITY_WORKTREE_TRACKED_DIRTY")
    return path.resolve()


def validate_stage_authorization(
    receipt_path: Path,
    *,
    stage: str,
    batch_id: str,
    attempt_id: str,
    governing_commit: str,
    orchestration_contract_sha256: str,
    batch_plan_sha256: str,
    launch_authority_sha256: str,
) -> dict[str, Any]:
    receipt = load_json_object(receipt_path, "STAGE_AUTHORIZATION_RECEIPT")
    return validate_stage_authorization_value(
        receipt,
        stage=stage,
        batch_id=batch_id,
        attempt_id=attempt_id,
        governing_commit=governing_commit,
        orchestration_contract_sha256=orchestration_contract_sha256,
        batch_plan_sha256=batch_plan_sha256,
        launch_authority_sha256=launch_authority_sha256,
    )


def validate_stage_authorization_value(
    receipt: Mapping[str, Any], *, stage: str, batch_id: str, attempt_id: str,
    governing_commit: str, orchestration_contract_sha256: str,
    batch_plan_sha256: str, launch_authority_sha256: str,
) -> dict[str, Any]:
    if stage not in SCIENTIFIC_AUTHORIZATION_STAGES:
        raise ProductionStageError("UNKNOWN_PRODUCTION_STAGE")
    if (
        stage == "PRESERVATION_FINALIZATION" and batch_id != "all_batches"
    ) or (
        stage != "PRESERVATION_FINALIZATION" and not BATCH_RE.fullmatch(batch_id)
    ):
        raise ProductionStageError("STAGE_AUTHORIZATION_BATCH_SCOPE_INVALID")
    if not ATTEMPT_RE.fullmatch(attempt_id) or not COMMIT_RE.fullmatch(governing_commit):
        raise ProductionStageError("STAGE_AUTHORIZATION_IDENTITY_INVALID")
    if set(receipt) != AUTHORIZATION_KEYS:
        raise ProductionStageError("STAGE_AUTHORIZATION_SCHEMA_MISMATCH")
    expected = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
        "status": "AUTHORIZED",
        "authorization_scope": STAGE_AUTHORIZATION_SCOPES[stage],
        "stage": stage,
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "orchestration_contract_sha256": orchestration_contract_sha256,
        "batch_plan_sha256": batch_plan_sha256,
        "launch_authority_sha256": _validate_hash(
            launch_authority_sha256, "LAUNCH_AUTHORITY_HASH_INVALID"
        ),
        "owner_authorized": True,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ProductionStageError("STAGE_AUTHORIZATION_AUTHORITY_MISMATCH")
    date = receipt.get("owner_authorization_date")
    if not isinstance(date, str) or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", date):
        raise ProductionStageError("STAGE_AUTHORIZATION_DATE_INVALID")
    return receipt


def validate_wrapper_authority(
    *,
    stage: str,
    batch_id: str,
    attempt_id: str,
    governing_commit: str,
    authority_worktree: Path,
    orchestration_contract: Path,
    batch_plan: Path,
    environment_receipt: Path,
    output_root: Path,
    requirements: Any | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if stage not in WRAPPER_STAGES:
        raise ProductionStageError("UNKNOWN_PRODUCTION_STAGE")
    if not BATCH_RE.fullmatch(batch_id):
        raise ProductionStageError("INVALID_BATCH_ID")
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise ProductionStageError("INVALID_ATTEMPT_ID")
    require_authority_worktree(authority_worktree, governing_commit)
    import lvef_c3_orchestration_core as core

    contract_hash = sha256_file(orchestration_contract)
    contract = core.load_orchestration_contract(orchestration_contract)
    plan = core.load_strict_json(batch_plan)
    scoped = requirements is not None or expected_runtime_authority is not None
    if scoped and (requirements is None or expected_runtime_authority is None):
        raise ProductionStageError("SCOPED_RUNTIME_AUTHORITY_ARGUMENTS_INCOMPLETE")
    effective_requirements = (
        requirements if requirements is not None else core.production_requirements(contract)
    )
    plan_hash = core.validate_current_batch_plan_v3(
        plan, requirements=effective_requirements
    )
    if contract_hash != plan["authority"]["orchestration_contract_sha256"]:
        raise ProductionStageError("PLAN_CONTRACT_AUTHORITY_MISMATCH")
    if governing_commit != plan["authority"]["git_commit"]:
        raise ProductionStageError("PLAN_GOVERNING_COMMIT_MISMATCH")
    planned_batch = next(
        (item for item in plan["batches"] if item["batch_id"] == batch_id), None
    )
    if planned_batch is None:
        raise ProductionStageError("BATCH_NOT_PRESENT_IN_PLAN")
    environment_receipt_sha256 = sha256_file(environment_receipt)
    validate_environment_receipt_against_current_runtime(environment_receipt)
    if scoped:
        runtime_authority = core.validate_runtime_authority(expected_runtime_authority)
        if (
            runtime_authority["batch_plan_sha256"] != plan_hash
            or any(
                runtime_authority[key] != str(plan["authority"][key])
                for key in core.PLAN_AUTHORITY_KEYS
            )
            or runtime_authority["git_commit"] != governing_commit
            or runtime_authority["environment_receipt_sha256"]
            != environment_receipt_sha256
            or runtime_authority["checkpoint_sha256"] != CHECKPOINT_SHA256
            or plan["authority"]["state_machine_schema_sha256"]
            != str(contract["authority"]["state_machine_schema_sha256"])
            or plan["authority"]["resume_ledger_schema_sha256"]
            != str(contract["authority"]["resume_ledger_schema_sha256"])
        ):
            raise ProductionStageError("SCOPED_RUNTIME_AUTHORITY_MISMATCH")
    else:
        runtime_authority = core.derive_expected_runtime_authority(
            plan,
            requirements=effective_requirements,
            contract=contract,
            contract_path=orchestration_contract,
            governing_commit=governing_commit,
            environment_receipt_sha256=environment_receipt_sha256,
        )
    require_projectnb_path(output_root, must_exist=False)
    return {
        "stage": stage,
        "batch_id_valid": True,
        "attempt_id_valid": True,
        "governing_commit_valid": bool(COMMIT_RE.fullmatch(governing_commit)),
        "orchestration_contract_sha256": contract_hash,
        "batch_plan_sha256": plan_hash,
        "runtime_authority": runtime_authority,
        "expected_object_keys": {
            row["source_object_key"] for row in planned_batch["objects"]
        },
        "planned_batch": planned_batch,
        "output_root_projectnb_bound": True,
        "real_execution_performed": False,
    }


def validate_production_dicom_rows(
    rows: Sequence[Mapping[str, Any]], *, expected_objects: int, expected_studies: int
) -> dict[str, Any]:
    required = {
        "subject_id",
        "study_id",
        "source_relative_path",
        "read_ok",
        "is_multiframe",
        "pixel_decode_ok",
    }
    if len(rows) != expected_objects or expected_objects < 1:
        raise ProductionStageError("DICOM_OBJECT_COUNT_MISMATCH")
    locators: set[str] = set()
    studies: set[Any] = set()
    readable = multiframe = single_frame = decode_failures = 0
    for row in rows:
        if not required.issubset(row):
            raise ProductionStageError("DICOM_AUDIT_ROW_SCHEMA_MISMATCH")
        locator = safe_relative_path(row["source_relative_path"])
        if locator in locators:
            raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
        locators.add(locator)
        studies.add(row["study_id"])
        read_ok = row["read_ok"] is True
        is_multiframe = row["is_multiframe"] is True
        pixel_decode_ok = row["pixel_decode_ok"] is True
        readable += int(read_ok)
        multiframe += int(read_ok and is_multiframe)
        single_frame += int(read_ok and not is_multiframe)
        decode_failures += int(read_ok and is_multiframe and not pixel_decode_ok)
        if not read_ok and (is_multiframe or pixel_decode_ok):
            raise ProductionStageError("DICOM_AUDIT_STATE_CONTRADICTION")
        if pixel_decode_ok and not is_multiframe:
            raise ProductionStageError("PIXEL_DECODE_RECORDED_FOR_NONCINE")
    if len(studies) != expected_studies:
        raise ProductionStageError("DICOM_STUDY_COUNT_MISMATCH")
    return {
        "n_objects": len(rows),
        "n_studies": len(studies),
        "n_readable": readable,
        "n_unreadable": len(rows) - readable,
        "n_multiframe_candidates": multiframe,
        "n_single_frame": single_frame,
        "n_pixel_decode_failures": decode_failures,
        "physical_source_keys_unique": True,
    }


def _validate_successful_extraction_rows(
    rows: Sequence[Mapping[str, Any]], *, expected_cines: int
) -> dict[str, Any]:
    required = {
        "study_id",
        "clip_key",
        "physical_source_key",
        "write_ok",
        "frames_shape",
        "frames_dtype",
        "mask_status",
        "temporal_sampling_policy",
        "pixel_decode_ok",
        "decode_color_status",
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
        "source_sector_pixel_count",
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_count",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_pixel_count",
        "source_temporal_variation_gate_passed",
        "ordinary_post_crop_nonzero_retained_pixel_count",
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed",
        "ordinary_post_crop_temporal_variation_pixel_count",
        "ordinary_post_crop_temporal_variation_gate_passed",
        "post_crop_nonzero_retained_pixel_count",
        "post_crop_nonzero_retained_pixel_gate_passed",
        "post_crop_temporal_variation_pixel_count",
        "post_crop_temporal_variation_gate_passed",
        "ordinary_sampled_nonzero_retained_pixel_count",
        "ordinary_sampled_nonzero_retained_pixel_gate_passed",
        "ordinary_sampled_temporal_variation_pixel_count",
        "ordinary_sampled_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_count",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_pixel_count",
        "sampled_temporal_variation_gate_passed",
        "encoder_visible_nonzero_retained_pixel_count",
        "encoder_visible_nonzero_retained_pixel_gate_passed",
        "encoder_visible_temporal_variation_pixel_count",
        "encoder_visible_temporal_variation_gate_passed",
        "selected_preprocessing_path",
        "fallback_status",
        "failure_substage",
        "npz_sha256",
    }
    if len(rows) != expected_cines:
        raise ProductionStageError("EXTRACTION_CINE_COUNT_MISMATCH")
    clip_keys: set[str] = set()
    source_keys: set[str] = set()
    studies: set[Any] = set()
    path_counts = {value: 0 for value in ALLOWED_PREPROCESSING_PATHS}
    fallback_passes = 0
    count_fields = (
        "source_sector_pixel_count",
        "source_nonzero_retained_pixel_count",
        "source_temporal_variation_pixel_count",
        "ordinary_post_crop_nonzero_retained_pixel_count",
        "ordinary_post_crop_temporal_variation_pixel_count",
        "post_crop_nonzero_retained_pixel_count",
        "post_crop_temporal_variation_pixel_count",
        "ordinary_sampled_nonzero_retained_pixel_count",
        "ordinary_sampled_temporal_variation_pixel_count",
        "sampled_nonzero_retained_pixel_count",
        "sampled_temporal_variation_pixel_count",
        "encoder_visible_nonzero_retained_pixel_count",
        "encoder_visible_temporal_variation_pixel_count",
    )
    count_gate_pairs = (
        ("source_sector_pixel_count", "source_sector_nonempty_gate_passed"),
        ("source_nonzero_retained_pixel_count", "source_nonzero_retained_pixel_gate_passed"),
        ("source_temporal_variation_pixel_count", "source_temporal_variation_gate_passed"),
        ("ordinary_post_crop_nonzero_retained_pixel_count", "ordinary_post_crop_nonzero_retained_pixel_gate_passed"),
        ("ordinary_post_crop_temporal_variation_pixel_count", "ordinary_post_crop_temporal_variation_gate_passed"),
        ("post_crop_nonzero_retained_pixel_count", "post_crop_nonzero_retained_pixel_gate_passed"),
        ("post_crop_temporal_variation_pixel_count", "post_crop_temporal_variation_gate_passed"),
        ("ordinary_sampled_nonzero_retained_pixel_count", "ordinary_sampled_nonzero_retained_pixel_gate_passed"),
        ("ordinary_sampled_temporal_variation_pixel_count", "ordinary_sampled_temporal_variation_gate_passed"),
        ("sampled_nonzero_retained_pixel_count", "sampled_nonzero_retained_pixel_gate_passed"),
        ("sampled_temporal_variation_pixel_count", "sampled_temporal_variation_gate_passed"),
        ("encoder_visible_nonzero_retained_pixel_count", "encoder_visible_nonzero_retained_pixel_gate_passed"),
        ("encoder_visible_temporal_variation_pixel_count", "encoder_visible_temporal_variation_gate_passed"),
    )
    source_gate_fields = (
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
    )
    selected_gate_fields = (
        "post_crop_nonzero_retained_pixel_gate_passed",
        "post_crop_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_gate_passed",
    )
    encoder_visible_gate_fields = (
        "encoder_visible_nonzero_retained_pixel_gate_passed",
        "encoder_visible_temporal_variation_gate_passed",
    )
    ordinary_post_crop_gate_fields = (
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed",
        "ordinary_post_crop_temporal_variation_gate_passed",
    )
    ordinary_sampled_gate_fields = (
        "ordinary_sampled_nonzero_retained_pixel_gate_passed",
        "ordinary_sampled_temporal_variation_gate_passed",
    )
    expected_color_transforms = {
        "MONOCHROME1": "MONOCHROME1_INVERT_REPLICATE_TO_RGB",
        "MONOCHROME2": "MONOCHROME2_REPLICATE_TO_RGB",
        "RGB": "NONE_RGB",
        "YBR_FULL": "EXPLICIT_YBR_FULL_TO_RGB",
        "YBR_FULL_422": "EXPLICIT_YBR_FULL_422_TO_RGB",
    }
    for row in rows:
        if not required.issubset(row):
            raise ProductionStageError("EXTRACTION_ROW_SCHEMA_MISMATCH")
        clip_key = _validate_hash(row["clip_key"], "INVALID_CLIP_KEY")
        source_key = _validate_hash(row["physical_source_key"], "INVALID_SOURCE_KEY")
        if clip_key in clip_keys:
            raise ProductionStageError("DUPLICATE_CLIP_KEY")
        if source_key in source_keys:
            raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
        clip_keys.add(clip_key)
        source_keys.add(source_key)
        studies.add(row["study_id"])
        if row["write_ok"] is not True:
            failure_substage = row["failure_substage"]
            if failure_substage not in ALLOWED_FAILURE_SUBSTAGES:
                raise ProductionStageError("EXTRACTION_FAILURE_SUBSTAGE_INVALID")
            if row["fallback_status"] not in {
                FALLBACK_NOT_ATTEMPTED,
                FALLBACK_PATH_FAILED,
            }:
                raise ProductionStageError("EXTRACTION_FALLBACK_STATUS_INVALID")
            raise ProductionStageError(f"EXTRACTION_{failure_substage}")
        if row["pixel_decode_ok"] is not True:
            raise ProductionStageError("EXTRACTION_DECODE_COLOR_AUTHORITY_INVALID")
        if row["decode_color_status"] != "PASS":
            raise ProductionStageError("EXTRACTION_DECODE_COLOR_AUTHORITY_INVALID")
        if (
            row["decoder_color_behavior"] != "STORED_COLOR_RAW"
            or row["canonical_color_space"] != "RGB"
            or row["photometric_interpretation"] not in expected_color_transforms
            or row["color_transform"]
            != expected_color_transforms[row["photometric_interpretation"]]
            or not isinstance(row["decoder_backend"], str)
            or re.fullmatch(
                r"pydicom_pixels_raw:[A-Za-z0-9_.+-]{1,80}",
                row["decoder_backend"],
            )
            is None
            or not isinstance(row["transfer_syntax_uid"], str)
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", row["transfer_syntax_uid"])
            is None
        ):
            raise ProductionStageError("EXTRACTION_DECODE_COLOR_AUTHORITY_INVALID")
        if row["frames_shape"] != "32x224x224x3" or row["frames_dtype"] != "uint8":
            raise ProductionStageError("EXTRACTION_SHAPE_OR_DTYPE_MISMATCH")
        if row["mask_status"] != "APPLIED":
            raise ProductionStageError("EXTRACTION_MASK_GATE_FAILED")
        path = row["selected_preprocessing_path"]
        if path not in ALLOWED_PREPROCESSING_PATHS:
            raise ProductionStageError("EXTRACTION_PREPROCESSING_PATH_INVALID")
        path_counts[str(path)] += 1
        expected_policy = (
            FALLBACK_TEMPORAL_SAMPLING_POLICY
            if path
            in {
                TEMPORAL_FALLBACK_PREPROCESSING_PATH,
                SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
            }
            else ORDINARY_TEMPORAL_SAMPLING_POLICY
        )
        if row["temporal_sampling_policy"] != expected_policy:
            raise ProductionStageError("EXTRACTION_SAMPLING_POLICY_MISMATCH")
        expected_fallback = (
            FALLBACK_NOT_ATTEMPTED
            if path == ORDINARY_PREPROCESSING_PATH
            else FALLBACK_PATH_PASS
        )
        if row["fallback_status"] != expected_fallback:
            raise ProductionStageError("EXTRACTION_FALLBACK_STATUS_INVALID")
        fallback_passes += int(expected_fallback == FALLBACK_PATH_PASS)
        if row["failure_substage"] != "NONE":
            raise ProductionStageError("EXTRACTION_FAILURE_SUBSTAGE_INVALID")
        numeric_counts: dict[str, int] = {}
        for field in count_fields:
            value = row[field]
            if isinstance(value, bool):
                raise ProductionStageError("EXTRACTION_PROVENANCE_COUNT_INVALID")
            if isinstance(value, float):
                if not math.isfinite(value) or value < 0 or not value.is_integer():
                    raise ProductionStageError(
                        "EXTRACTION_PROVENANCE_COUNT_INVALID"
                    )
                numeric_counts[field] = int(value)
            elif re.fullmatch(r"0|[1-9][0-9]*", str(value)) is not None:
                numeric_counts[field] = int(value)
            else:
                raise ProductionStageError("EXTRACTION_PROVENANCE_COUNT_INVALID")
        if any(
            row[gate_field] is not (numeric_counts[count_field] > 0)
            for count_field, gate_field in count_gate_pairs
        ):
            raise ProductionStageError("EXTRACTION_PROVENANCE_GATE_CONTRADICTION")
        if not all(row[field] is True for field in source_gate_fields):
            raise ProductionStageError("EXTRACTION_SOURCE_SIGNAL_GATE_FAILED")
        if not all(row[field] is True for field in selected_gate_fields):
            raise ProductionStageError("EXTRACTION_SELECTED_SIGNAL_GATE_FAILED")
        if path != ORDINARY_PREPROCESSING_PATH and not all(
            row[field] is True for field in encoder_visible_gate_fields
        ):
            raise ProductionStageError(
                "EXTRACTION_FALLBACK_ENCODER_VISIBLE_SIGNAL_GATE_FAILED"
            )
        if path == ORDINARY_PREPROCESSING_PATH and not all(
            row[field] is True
            for field in (
                *ordinary_post_crop_gate_fields,
                *ordinary_sampled_gate_fields,
            )
        ):
            raise ProductionStageError("EXTRACTION_ORDINARY_PATH_CONTRADICTION")
        if path != ORDINARY_PREPROCESSING_PATH and all(
            row[field] is True for field in ordinary_sampled_gate_fields
        ):
            raise ProductionStageError("EXTRACTION_FALLBACK_TRIGGER_INVALID")
        if path in {
            SPATIAL_FALLBACK_PREPROCESSING_PATH,
            SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
        } and all(row[field] is True for field in ordinary_post_crop_gate_fields):
            raise ProductionStageError("EXTRACTION_SPATIAL_FALLBACK_TRIGGER_INVALID")
        if path == TEMPORAL_FALLBACK_PREPROCESSING_PATH and not all(
            row[field] is True for field in ordinary_post_crop_gate_fields
        ):
            raise ProductionStageError("EXTRACTION_TEMPORAL_FALLBACK_TRIGGER_INVALID")
        _validate_hash(row["npz_sha256"], "INVALID_EXTRACTION_HASH")
    return {
        "n_extracted_clips": len(rows),
        "n_studies_with_extracted_clips": len(studies),
        "clip_keys_unique": True,
        "physical_source_keys_unique": True,
        "all_shapes_and_dtypes_valid": True,
        "all_pixel_decodes_passed": True,
        "n_ordinary_preprocessing_path": path_counts[
            ORDINARY_PREPROCESSING_PATH
        ],
        "n_spatial_fallback_preprocessing_path": path_counts[
            SPATIAL_FALLBACK_PREPROCESSING_PATH
        ],
        "n_temporal_fallback_preprocessing_path": path_counts[
            TEMPORAL_FALLBACK_PREPROCESSING_PATH
        ],
        "n_spatial_temporal_fallback_preprocessing_path": path_counts[
            SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH
        ],
        "n_fallback_path_pass": fallback_passes,
        "n_fallback_path_failed": 0,
        "all_source_signal_gates_passed": True,
        "all_post_crop_signal_gates_passed": True,
        "all_sampled_signal_gates_passed": True,
        "all_fallback_encoder_visible_signal_gates_passed": True,
        "all_failure_substages_none": True,
    }


def _is_not_evaluated(value: Any) -> bool:
    return value is None or value == "" or (
        isinstance(value, float) and math.isnan(value)
    )


def _nonnegative_integral(value: Any) -> int | None:
    if isinstance(value, bool) or _is_not_evaluated(value):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 and value.is_integer() else None
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    return None


def _exact_boolean(value: Any) -> bool | None:
    if value is True or value == "True" or value == "true":
        return True
    if value is False or value == "False" or value == "false":
        return False
    return None


def _technical_manifest_boolean(value: Any) -> bool | None:
    if type(value) is bool:
        return value
    if value == "True":
        return True
    if value == "False":
        return False
    return None


def _decoder_authority_valid(row: Mapping[str, Any]) -> bool:
    expected_color_transforms = {
        "MONOCHROME1": "MONOCHROME1_INVERT_REPLICATE_TO_RGB",
        "MONOCHROME2": "MONOCHROME2_REPLICATE_TO_RGB",
        "RGB": "NONE_RGB",
        "YBR_FULL": "EXPLICIT_YBR_FULL_TO_RGB",
        "YBR_FULL_422": "EXPLICIT_YBR_FULL_422_TO_RGB",
    }
    photometric = row.get("photometric_interpretation")
    return bool(
        row.get("pixel_decode_ok") is True
        and row.get("decode_color_status") == "PASS"
        and row.get("decoder_color_behavior") == "STORED_COLOR_RAW"
        and row.get("canonical_color_space") == "RGB"
        and photometric in expected_color_transforms
        and row.get("color_transform") == expected_color_transforms[photometric]
        and isinstance(row.get("decoder_backend"), str)
        and re.fullmatch(
            r"pydicom_pixels_raw:[A-Za-z0-9_.+-]{1,80}",
            str(row.get("decoder_backend")),
        )
        is not None
        and isinstance(row.get("transfer_syntax_uid"), str)
        and re.fullmatch(
            r"[0-9]+(?:\.[0-9]+)+", str(row.get("transfer_syntax_uid"))
        )
        is not None
    )


def _validate_source_signal_quality_disposition_row(
    row: Mapping[str, Any], *, context: ExtractionDispositionContext
) -> None:
    if set(row) != EXTRACTION_PRODUCER_FIELDS:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_ROW_SCHEMA_INVALID"
        )
    source_key = _validate_hash(
        row.get("physical_source_key"), "INVALID_SOURCE_KEY"
    )
    clip_key = _validate_hash(row.get("clip_key"), "INVALID_CLIP_KEY")
    source_relative = row.get("source_relative_path")
    output_relative = row.get("output_relative_path")
    expected_output = f"clips/{clip_key[:2]}/{clip_key}.npz"
    expected_clip_key = (
        hashlib.sha256(
            f"{CLIP_KEY_NAMESPACE}\0{safe_relative_path(source_relative)}".encode(
                "utf-8"
            )
        ).hexdigest()
        if isinstance(source_relative, str)
        else ""
    )
    source_num_frames = _nonnegative_integral(row.get("source_num_frames"))
    context_frames = dict(context.source_num_frames_by_key).get(source_key)
    if (
        row.get("write_ok") is not False
        or row.get("smoke_role") != "production_selected"
        or row.get("failure_substage") != "SOURCE_SIGNAL_QUALITY_FAILURE"
        or row.get("mask_status") != "FAILED"
        or row.get("selected_preprocessing_path") != "NOT_SELECTED"
        or row.get("fallback_status") != FALLBACK_NOT_ATTEMPTED
        or row.get("temporal_sampling_policy")
        != ORDINARY_TEMPORAL_SAMPLING_POLICY
        or not isinstance(row.get("error_code"), str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", row["error_code"])
        is None
        or expected_clip_key != clip_key
        or output_relative != expected_output
        or source_num_frames is None
        or source_num_frames < 2
        or source_num_frames != context_frames
        or not _decoder_authority_valid(row)
    ):
        raise ProductionStageError("EXTRACTION_TECHNICAL_DISPOSITION_ROW_INVALID")
    for field in (
        "frames_shape",
        "frames_dtype",
        "frames_sha256",
        "sampled_indices_sha256",
        "source_num_frames_sha256",
        "npz_sha256",
    ):
        if not _is_not_evaluated(row.get(field)):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_OUTPUT_AUTHORITY_INVALID"
            )
    source_counts: list[int] = []
    for count, gate in SOURCE_SIGNAL_COUNT_GATE_PAIRS:
        parsed = _nonnegative_integral(row.get(count))
        if parsed is None or row.get(gate) is not (parsed > 0):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_SOURCE_METRICS_INVALID"
            )
        source_counts.append(parsed)
    if not any(value == 0 for value in source_counts):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_SOURCE_METRICS_INVALID"
        )
    if any(
        not _is_not_evaluated(row.get(count)) or row.get(gate) is not False
        for count, gate in DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS
    ):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_DOWNSTREAM_METRICS_INVALID"
        )
    studies = dict(context.study_success_counts)
    if (
        context.policy_version != OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        or context.object_substitution_count != 0
        or source_key not in context.approved_source_keys
        or source_key not in context.raw_retained_source_keys
        or clip_key not in context.npz_absent_clip_keys
        or clip_key not in context.embedding_absent_clip_keys
    ):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID"
        )
    if studies.get(str(row.get("study_id")), 0) < 1:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_STUDY_ELIGIBILITY_FAILED"
        )


def validate_production_extraction_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_cines: int,
    disposition_context: ExtractionDispositionContext | None = None,
    clips_root: Path | None = None,
) -> dict[str, Any]:
    """Classify every requested cine without converting a failed row to success."""

    if len(rows) != expected_cines or expected_cines < 1:
        raise ProductionStageError("EXTRACTION_CINE_COUNT_MISMATCH")
    if any(
        not isinstance(row, Mapping) or set(row) != EXTRACTION_PRODUCER_FIELDS
        for row in rows
    ):
        raise ProductionStageError("EXTRACTION_ROW_SCHEMA_MISMATCH")
    successful = [row for row in rows if row.get("write_ok") is True]
    failed = [row for row in rows if row.get("write_ok") is not True]
    if len(successful) + len(failed) != len(rows):
        raise ProductionStageError("EXTRACTION_CLASSIFICATION_INTERNAL_INVALID")
    source_keys = [str(row.get("physical_source_key")) for row in rows]
    clip_keys = [str(row.get("clip_key")) for row in rows]
    if len(set(source_keys)) != len(source_keys):
        raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
    if len(set(clip_keys)) != len(clip_keys):
        raise ProductionStageError("DUPLICATE_CLIP_KEY")
    for row in rows:
        _validate_extraction_row_identity(row)
    for row in failed:
        if not isinstance(row, Mapping) or set(row) != EXTRACTION_PRODUCER_FIELDS:
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_ROW_SCHEMA_INVALID"
            )
        failure_substage = row["failure_substage"]
        if failure_substage not in ALLOWED_FAILURE_SUBSTAGES:
            raise ProductionStageError("EXTRACTION_FAILURE_SUBSTAGE_INVALID")
        if failure_substage != "SOURCE_SIGNAL_QUALITY_FAILURE":
            raise ProductionStageError(f"EXTRACTION_{failure_substage}")
    if len(failed) > OBJECT_TECHNICAL_DISPOSITION_ABSOLUTE_LIMIT:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_ABSOLUTE_LIMIT_EXCEEDED"
        )
    if (
        len(failed) * OBJECT_TECHNICAL_DISPOSITION_RATE_DENOMINATOR
        > expected_cines
    ):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_RATE_LIMIT_EXCEEDED"
        )
    if failed and not isinstance(disposition_context, ExtractionDispositionContext):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_REQUIRED"
        )
    successful_summary = _validate_successful_extraction_rows(
        successful, expected_cines=len(successful)
    )
    for row in failed:
        _validate_source_signal_quality_disposition_row(
            row, context=disposition_context  # type: ignore[arg-type]
        )
    if clips_root is not None:
        validate_extraction_artifact_partition(rows, clips_root=clips_root)
    affected_studies = {str(row.get("study_id")) for row in failed}
    status = (
        "PASS_EXTRACTION_WITH_OBJECT_TECHNICAL_DISPOSITIONS"
        if failed
        else "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
    )
    return {
        **successful_summary,
        "status": status,
        "n_requested_cines": len(rows),
        "n_extracted_clips": len(successful),
        "n_successfully_extracted_cines": len(successful),
        "n_successfully_extracted_clips": len(successful),
        "n_object_technical_dispositions": len(failed),
        "n_blocking_failures": 0,
        "n_studies_affected_by_technical_disposition": len(affected_studies),
        "n_new_no_cine_studies": 0,
        "technical_disposition_counts_by_class": (
            {OBJECT_TECHNICAL_DISPOSITION: len(failed)}
        ),
        "all_extraction_rows_resolved": True,
        "all_successful_extractions_embeddable": True,
        "all_technical_dispositions_retained": True,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "all_failure_substages_none": not failed,
        "all_source_signal_gates_passed": not failed,
        "all_post_crop_signal_gates_passed": not failed,
        "all_sampled_signal_gates_passed": not failed,
        "successful_extraction_gate_scope": "SUCCESSFUL_EXTRACTIONS_ONLY",
        "requested_cine_resolution_scope": (
            "SUCCESSFUL_EXTRACTION_OR_APPROVED_OBJECT_TECHNICAL_DISPOSITION"
        ),
    }


def validate_extraction_artifact_partition(
    rows: Sequence[Mapping[str, Any]], *, clips_root: Path
) -> None:
    """Bind canonical source/clip/output identity and current NPZ topology."""

    for row in rows:
        if set(row) != EXTRACTION_PRODUCER_FIELDS:
            raise ProductionStageError("EXTRACTION_ROW_SCHEMA_MISMATCH")
        expected_clip, expected_output = _validate_extraction_row_identity(row)
        output = clips_root / expected_output
        if row.get("write_ok") is True:
            observed_sha256, _identity = _stable_nofollow_sha256_authority(output)
            if observed_sha256 != row.get("npz_sha256"):
                raise ProductionStageError("EXTRACTION_NPZ_HASH_MISMATCH")
        else:
            _require_nofollow_absent(
                output, code="EXTRACTION_TECHNICAL_DISPOSITION_NPZ_PRESENT"
            )


def _validate_extraction_row_identity(
    row: Mapping[str, Any],
) -> tuple[str, str]:
    """Bind source locator, clip key, and canonical NPZ locator for every row."""

    source_relative = row.get("source_relative_path")
    source_key = row.get("physical_source_key")
    if not isinstance(source_relative, str):
        raise ProductionStageError("EXTRACTION_SOURCE_PATH_INVALID")
    try:
        relative = safe_relative_path(source_relative)
    except ProductionStageError as exc:
        raise ProductionStageError("EXTRACTION_SOURCE_PATH_INVALID") from exc
    if (
        SHA256_RE.fullmatch(str(source_key)) is None
        or relative != f"{source_key}.dcm"
    ):
        raise ProductionStageError("EXTRACTION_SOURCE_PATH_BINDING_MISMATCH")
    expected_clip = hashlib.sha256(
        f"{CLIP_KEY_NAMESPACE}\0{relative}".encode("utf-8")
    ).hexdigest()
    if row.get("clip_key") != expected_clip:
        raise ProductionStageError("EXTRACTION_CLIP_KEY_DERIVATION_MISMATCH")
    expected_output = f"clips/{expected_clip[:2]}/{expected_clip}.npz"
    if row.get("output_relative_path") != expected_output:
        raise ProductionStageError("EXTRACTION_OUTPUT_PATH_MISMATCH")
    return expected_clip, expected_output


def build_extraction_disposition_context(
    *,
    extraction_rows: Sequence[Mapping[str, Any]],
    planned_batch: Mapping[str, Any],
    verified_download_rows: Sequence[Mapping[str, Any]],
    dicom_rows: Sequence[Mapping[str, Any]],
    download_root: Path,
    clips_root: Path,
    raw_authority_by_source: Mapping[
        str, tuple[str, tuple[int, ...]]
    ] | None = None,
    embedding_output_root: Path | None = None,
    existing_embedding_clip_keys: Sequence[str] = (),
) -> ExtractionDispositionContext:
    """Derive approval solely from current technical and physical authority."""

    expected = {
        str(row["source_object_key"]): (
            str(row["subject_id"]),
            str(row["study_id"]),
            safe_relative_path(str(row["source_relative_path"])),
        )
        for row in planned_batch.get("objects", ())
    }
    downloads = {
        str(row.get("physical_source_key")): row for row in verified_download_rows
    }
    if (
        len(expected) != int(planned_batch.get("n_objects", -1))
        or len(downloads) != len(verified_download_rows)
        or set(downloads) != set(expected)
    ):
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_PLAN_DOWNLOAD_AUTHORITY_INVALID"
        )
    dicom_candidates = {
        (
            str(row.get("subject_id")),
            str(row.get("study_id")),
            str(row.get("source_relative_path")),
        ): row
        for row in dicom_rows
        if row.get("read_ok") is True and row.get("is_multiframe") is True
    }
    extraction_identity = {
        (
            str(row.get("subject_id")),
            str(row.get("study_id")),
            str(row.get("source_relative_path")),
        )
        for row in extraction_rows
    }
    if len(dicom_candidates) != len(extraction_rows) or set(dicom_candidates) != extraction_identity:
        raise ProductionStageError(
            "EXTRACTION_TECHNICAL_DISPOSITION_OBJECT_SUBSTITUTION_DETECTED"
        )
    success_counts: dict[str, int] = {}
    for row in extraction_rows:
        if row.get("write_ok") is True:
            study = str(row.get("study_id"))
            success_counts[study] = success_counts.get(study, 0) + 1
    embedded = set(existing_embedding_clip_keys)
    has_failed_rows = any(row.get("write_ok") is not True for row in extraction_rows)
    if has_failed_rows:
        if embedding_output_root is None:
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID"
            )
        _require_nofollow_absent(
            embedding_output_root,
            code="EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID",
        )
        _require_nofollow_absent(
            embedding_output_root.with_name(f"{embedding_output_root.name}.partial"),
            code="EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID",
        )
    approved: set[str] = set()
    raw_retained: set[str] = set()
    npz_absent: set[str] = set()
    embedding_absent: set[str] = set()
    frames_by_key: dict[str, int] = {}
    for row in extraction_rows:
        if row.get("write_ok") is True:
            continue
        source_key = _validate_hash(
            row.get("physical_source_key"), "INVALID_SOURCE_KEY"
        )
        clip_key = _validate_hash(row.get("clip_key"), "INVALID_CLIP_KEY")
        authority = expected.get(source_key)
        download = downloads.get(source_key)
        identity = (
            str(row.get("subject_id")),
            str(row.get("study_id")),
            str(row.get("source_relative_path")),
        )
        dicom = dicom_candidates.get(identity)
        dicom_frames = (
            _nonnegative_integral(dicom.get("number_of_frames"))
            if dicom is not None
            else None
        )
        if (
            authority is None
            or authority[:2] != identity[:2]
            or download is None
            or _exact_boolean(download.get("download_ok")) is not True
            or str(download.get("subject_id")) != identity[0]
            or str(download.get("study_id")) != identity[1]
            or str(download.get("physical_source_key")) != source_key
            or str(download.get("source_relative_path")) != identity[2]
            or identity[2] != f"{source_key}.dcm"
            or str(download.get("source_authority_relative_path")) != authority[2]
            or SHA256_RE.fullmatch(str(download.get("observed_sha256"))) is None
            or dicom is None
            or dicom.get("pixel_decode_ok") is not True
            or dicom_frames is None
            or dicom_frames < 2
            or _nonnegative_integral(row.get("source_num_frames")) != dicom_frames
            or row.get("source_sha256") != download.get("observed_sha256")
            or row.get("photometric_interpretation")
            != dicom.get("photometric_interpretation")
            or row.get("transfer_syntax_uid") != dicom.get("transfer_syntax_uid")
        ):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID"
            )
        raw_path = download_root / safe_relative_path(
            str(download.get("source_relative_path"))
        )
        retained_authority = (
            raw_authority_by_source.get(source_key)
            if raw_authority_by_source is not None
            else None
        )
        if (
            retained_authority is None
            or retained_authority[0] != download.get("observed_sha256")
            or _stable_nofollow_identity(raw_path) != retained_authority[1]
        ):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID"
            )
        output_relative = row.get("output_relative_path")
        if not isinstance(output_relative, str):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_OUTPUT_PATH_INVALID"
            )
        output = clips_root / safe_relative_path(output_relative)
        _require_nofollow_absent(
            output, code="EXTRACTION_TECHNICAL_DISPOSITION_NPZ_PRESENT"
        )
        if clip_key in embedded:
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_PRESENT"
            )
        if success_counts.get(identity[1], 0) < 1:
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_STUDY_ELIGIBILITY_FAILED"
            )
        approved.add(source_key)
        raw_retained.add(source_key)
        npz_absent.add(clip_key)
        embedding_absent.add(clip_key)
        frames_by_key[source_key] = dicom_frames
    return ExtractionDispositionContext(
        approved_source_keys=frozenset(approved),
        raw_retained_source_keys=frozenset(raw_retained),
        npz_absent_clip_keys=frozenset(npz_absent),
        embedding_absent_clip_keys=frozenset(embedding_absent),
        study_success_counts=tuple(sorted(success_counts.items())),
        source_num_frames_by_key=tuple(sorted(frames_by_key.items())),
        object_substitution_count=0,
    )


def technical_disposition_manifest_rows(
    extraction_rows: Sequence[Mapping[str, Any]],
    *,
    context: ExtractionDispositionContext,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    studies = dict(context.study_success_counts)
    for source in extraction_rows:
        if source.get("write_ok") is True:
            continue
        _validate_source_signal_quality_disposition_row(source, context=context)
        rows.append(
            {
                "subject_id": source["subject_id"],
                "study_id": source["study_id"],
                "clip_key": source["clip_key"],
                "physical_source_key": source["physical_source_key"],
                "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
                "technical_disposition": OBJECT_TECHNICAL_DISPOSITION,
                "selected_source_membership_passed": True,
                "batch_plan_membership_passed": True,
                "download_integrity_authority_passed": True,
                "dicom_header_readable": True,
                "pixel_decode_ok": True,
                "decode_color_status": "PASS",
                "canonical_color_space": "RGB",
                "raw_dicom_retained": True,
                "npz_absent": True,
                "embedding_absent": True,
                "object_substitution": False,
                "study_retains_valid_cine_coverage": (
                    studies.get(str(source["study_id"]), 0) > 0
                ),
                "technical_disposition_policy_version": (
                    OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
                ),
            }
        )
    return sorted(rows, key=lambda row: str(row["physical_source_key"]))


def context_from_completed_extraction_authority(
    *,
    extraction_rows: Sequence[Mapping[str, Any]],
    dicom_rows: Sequence[Mapping[str, Any]],
    manifest_rows: Sequence[Mapping[str, Any]],
    clips_root: Path,
    embedding_output_root: Path | None = None,
    existing_embedding_clip_keys: Sequence[str] = (),
) -> ExtractionDispositionContext:
    """Rebuild structural authority without rereading any retained DICOM body."""

    failed = [row for row in extraction_rows if row.get("write_ok") is not True]
    manifest_identity = {
        (
            str(row.get("subject_id")), str(row.get("study_id")),
            str(row.get("clip_key")), str(row.get("physical_source_key")),
        )
        for row in manifest_rows
    }
    failed_identity = {
        (
            str(row.get("subject_id")), str(row.get("study_id")),
            str(row.get("clip_key")), str(row.get("physical_source_key")),
        )
        for row in failed
    }
    if len(manifest_identity) != len(manifest_rows) or manifest_identity != failed_identity:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_RECONCILIATION_MISMATCH"
        )
    success_counts: dict[str, int] = {}
    for row in extraction_rows:
        if row.get("write_ok") is True:
            study = str(row.get("study_id"))
            success_counts[study] = success_counts.get(study, 0) + 1
    dicom_by_identity = {
        (
            str(row.get("subject_id")), str(row.get("study_id")),
            str(row.get("source_relative_path")),
        ): row
        for row in dicom_rows
    }
    frames_by_key: dict[str, int] = {}
    for row in failed:
        clip_key = _validate_hash(row.get("clip_key"), "INVALID_CLIP_KEY")
        source_key = _validate_hash(row.get("physical_source_key"), "INVALID_SOURCE_KEY")
        output_relative = row.get("output_relative_path")
        if not isinstance(output_relative, str):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_OUTPUT_PATH_INVALID"
            )
        _require_nofollow_absent(
            clips_root / safe_relative_path(output_relative),
            code="EXTRACTION_TECHNICAL_DISPOSITION_NPZ_PRESENT",
        )
        dicom = dicom_by_identity.get(
            (
                str(row.get("subject_id")), str(row.get("study_id")),
                str(row.get("source_relative_path")),
            )
        )
        frames = (
            _nonnegative_integral(dicom.get("number_of_frames"))
            if dicom is not None
            else None
        )
        if (
            dicom is None
            or dicom.get("read_ok") is not True
            or dicom.get("is_multiframe") is not True
            or dicom.get("pixel_decode_ok") is not True
            or frames is None
            or frames < 2
            or row.get("photometric_interpretation")
            != dicom.get("photometric_interpretation")
            or row.get("transfer_syntax_uid") != dicom.get("transfer_syntax_uid")
        ):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_DICOM_AUTHORITY_INVALID"
            )
        frames_by_key[source_key] = frames
    if failed and embedding_output_root is not None:
        _require_nofollow_absent(
            embedding_output_root,
            code="EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID",
        )
        _require_nofollow_absent(
            embedding_output_root.with_name(f"{embedding_output_root.name}.partial"),
            code="EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID",
        )
    elif failed:
        embedded = set(existing_embedding_clip_keys)
        if not embedded or any(identity[2] in embedded for identity in failed_identity):
            raise ProductionStageError(
                "EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID"
            )
    return ExtractionDispositionContext(
        approved_source_keys=frozenset(identity[3] for identity in failed_identity),
        raw_retained_source_keys=frozenset(identity[3] for identity in failed_identity),
        npz_absent_clip_keys=frozenset(identity[2] for identity in failed_identity),
        embedding_absent_clip_keys=frozenset(identity[2] for identity in failed_identity),
        study_success_counts=tuple(sorted(success_counts.items())),
        source_num_frames_by_key=tuple(sorted(frames_by_key.items())),
        object_substitution_count=0,
    )


def validate_technical_disposition_manifest_rows(
    extraction_rows: Sequence[Mapping[str, Any]],
    manifest_rows: Sequence[Mapping[str, Any]],
    *,
    context: ExtractionDispositionContext | None = None,
) -> dict[str, Any]:
    expected = {
        (
            str(row.get("subject_id")),
            str(row.get("study_id")),
            str(row.get("clip_key")),
            str(row.get("physical_source_key")),
        )
        for row in extraction_rows
        if row.get("write_ok") is not True
    }
    observed: set[tuple[str, str, str, str]] = set()
    failed_rows = [row for row in extraction_rows if row.get("write_ok") is not True]
    if failed_rows and not isinstance(context, ExtractionDispositionContext):
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_CONTEXT_REQUIRED"
        )
    for failed in failed_rows:
        _validate_source_signal_quality_disposition_row(
            failed, context=context  # type: ignore[arg-type]
        )
    for row in manifest_rows:
        if set(row) != set(TECHNICAL_DISPOSITION_MANIFEST_HEADER):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_SCHEMA_MISMATCH"
            )
        identity = tuple(
            str(row[field])
            for field in ("subject_id", "study_id", "clip_key", "physical_source_key")
        )
        if identity in observed:
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_DUPLICATE"
            )
        observed.add(identity)  # type: ignore[arg-type]
        true_fields = (
            "selected_source_membership_passed",
            "batch_plan_membership_passed",
            "download_integrity_authority_passed",
            "dicom_header_readable",
            "pixel_decode_ok",
            "raw_dicom_retained",
            "npz_absent",
            "embedding_absent",
            "study_retains_valid_cine_coverage",
        )
        if (
            any(
                _technical_manifest_boolean(row[field]) is not True
                for field in true_fields
            )
            or _technical_manifest_boolean(row["object_substitution"]) is not False
            or row["failure_substage"] != "SOURCE_SIGNAL_QUALITY_FAILURE"
            or row["technical_disposition"] != OBJECT_TECHNICAL_DISPOSITION
            or row["decode_color_status"] != "PASS"
            or row["canonical_color_space"] != "RGB"
            or row["technical_disposition_policy_version"]
            != OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ):
            raise ProductionStageError(
                "TECHNICAL_DISPOSITION_MANIFEST_AUTHORITY_INVALID"
            )
    if observed != expected:
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_MANIFEST_RECONCILIATION_MISMATCH"
        )
    successful_clips = {
        str(row.get("clip_key")) for row in extraction_rows if row.get("write_ok") is True
    }
    if successful_clips.intersection(identity[2] for identity in observed):
        raise ProductionStageError(
            "TECHNICAL_DISPOSITION_SUCCESS_MANIFEST_OVERLAP"
        )
    return {
        "n_object_technical_dispositions": len(observed),
        "n_studies_affected_by_technical_disposition": len(
            {identity[1] for identity in observed}
        ),
        "technical_disposition_counts_by_class": (
            {OBJECT_TECHNICAL_DISPOSITION: len(observed)}
        ),
        "object_substitution_count": 0,
        "all_technical_dispositions_retained": True,
    }


def validate_embedding_values(
    values: Sequence[Sequence[Any]], *, expected_rows: int
) -> dict[str, Any]:
    if len(values) != expected_rows or expected_rows < 1:
        raise ProductionStageError("EMBEDDING_ROW_COUNT_MISMATCH")
    for vector in values:
        if len(vector) != EXPECTED_EMBEDDING_DIMENSION:
            raise ProductionStageError("EMBEDDING_DIMENSION_MISMATCH")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
            raise ProductionStageError("EMBEDDING_VALUE_NOT_NUMERIC")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ProductionStageError("EMBEDDING_VALUE_NONFINITE")
    return {
        "n_embeddings": expected_rows,
        "embedding_dimension": EXPECTED_EMBEDDING_DIMENSION,
        "embedding_dtype": "float32",
        "all_finite": True,
    }


def validate_environment_receipt_payload(
    receipt: Mapping[str, Any], *, live_packages: Sequence[Mapping[str, str]],
    live_runtime: Mapping[str, str]
) -> None:
    """Validate the retained package preimage and exact live runtime authority."""
    if set(receipt) != ENVIRONMENT_RECEIPT_KEYS:
        raise ProductionStageError("ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH")
    if (
        receipt.get("schema_version") != 3
        or receipt.get("artifact_type")
        != "lvef_c3_production_environment_authority_v3"
        or receipt.get("status")
        != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        or not COMMIT_RE.fullmatch(str(receipt.get("governing_commit")))
    ):
        raise ProductionStageError("ENVIRONMENT_RECEIPT_IDENTITY_MISMATCH")
    _validate_hash(
        receipt.get("source_environment_receipt_sha256"),
        "SOURCE_ENVIRONMENT_HASH_INVALID",
    )
    _validate_hash(receipt.get("python_executable_sha256"), "PYTHON_HASH_INVALID")
    _validate_hash(
        receipt.get("crc32c_python_executable_sha256"),
        "CRC32C_PYTHON_HASH_INVALID",
    )
    _validate_hash(receipt.get("crc32c_worker_sha256"), "CRC32C_WORKER_HASH_INVALID")
    _validate_hash(
        receipt.get("google_crc32c_distribution_sha256"),
        "CRC32C_DISTRIBUTION_HASH_INVALID",
    )
    _validate_hash(receipt.get("package_inventory_sha256"), "PACKAGE_HASH_INVALID")
    if (
        receipt.get("crc32c_runtime_source")
        != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or receipt.get("crc32c_worker_protocol_version") != 1
        or receipt.get("google_crc32c_implementation") != "c"
        or receipt.get("google_crc32c_known_vector_base64") != "4waSgw=="
        or not isinstance(receipt.get("crc32c_python_version"), str)
        or not receipt["crc32c_python_version"]
        or not isinstance(receipt.get("google_crc32c_version"), str)
        or not receipt["google_crc32c_version"]
        or not isinstance(
            receipt.get("google_crc32c_distribution_file_count"), int
        )
        or isinstance(
            receipt.get("google_crc32c_distribution_file_count"), bool
        )
        or receipt["google_crc32c_distribution_file_count"] < 1
    ):
        raise ProductionStageError("CRC32C_AUXILIARY_AUTHORITY_INVALID")
    try:
        captured = datetime.fromisoformat(str(receipt.get("captured_at_utc")))
    except ValueError as exc:
        raise ProductionStageError("ENVIRONMENT_CAPTURE_TIMESTAMP_INVALID") from exc
    if captured.tzinfo is None or captured.utcoffset() != timezone.utc.utcoffset(captured):
        raise ProductionStageError("ENVIRONMENT_CAPTURE_TIMESTAMP_INVALID")
    for flag in (
        "gpu_execution_performed",
        "cloud_request_performed",
        "dicom_body_read",
        "model_fitted",
        "prediction_generated",
        "confirmatory_performance_accessed",
    ):
        if receipt.get(flag) is not False:
            raise ProductionStageError("ENVIRONMENT_RECEIPT_ACTIVITY_FLAG_INVALID")
    packages = receipt.get("package_inventory")
    if not isinstance(packages, list) or not packages:
        raise ProductionStageError("PACKAGE_INVENTORY_SCHEMA_INVALID")
    normalized_names: set[str] = set()
    prior_sort_key: tuple[str, str] | None = None
    for row in packages:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"name", "version"}
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
            or not isinstance(row.get("version"), str)
            or not row["version"].strip()
        ):
            raise ProductionStageError("PACKAGE_INVENTORY_SCHEMA_INVALID")
        normalized_name = re.sub(r"[-_.]+", "-", row["name"]).casefold()
        if normalized_name in normalized_names:
            raise ProductionStageError("PACKAGE_INVENTORY_DUPLICATE_NAME")
        normalized_names.add(normalized_name)
        sort_key = (row["name"].casefold(), row["version"])
        if prior_sort_key is not None and sort_key < prior_sort_key:
            raise ProductionStageError("PACKAGE_INVENTORY_NOT_SORTED")
        prior_sort_key = sort_key
    package_hash = hashlib.sha256(
        json.dumps(packages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        isinstance(receipt.get("package_count"), bool)
        or receipt.get("package_count") != len(packages)
        or package_hash != receipt.get("package_inventory_sha256")
        or packages != list(live_packages)
    ):
        raise ProductionStageError("RUNNING_PACKAGE_INVENTORY_MISMATCH")
    for key, value in live_runtime.items():
        if str(receipt.get(key)) != str(value):
            raise ProductionStageError("RUNNING_ENVIRONMENT_RUNTIME_MISMATCH")


def validate_environment_receipt_against_current_runtime(
    environment_receipt: Path,
) -> dict[str, Any]:
    receipt = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    try:
        import torch
        import torchvision
    except Exception as exc:
        raise ProductionStageError("ENVIRONMENT_RUNTIME_UNAVAILABLE") from exc
    cudnn_version = torch.backends.cudnn.version()
    if torch.version.cuda is None or cudnn_version is None:
        raise ProductionStageError("CUDA_CUDNN_RUNTIME_UNAVAILABLE")
    packages = sorted(
        (
            {"name": str(dist.metadata.get("Name")), "version": str(dist.version)}
            for dist in importlib.metadata.distributions()
            if dist.metadata.get("Name")
        ),
        key=lambda item: (item["name"].casefold(), item["version"]),
    )
    validate_environment_receipt_payload(
        receipt,
        live_packages=packages,
        live_runtime={
            "python_executable_sha256": resolved_python_executable_sha256(
                Path(sys.executable)
            ),
            "python_version": platform.python_version(),
            "torch_version": str(torch.__version__),
            "torchvision_version": str(torchvision.__version__),
            "cuda_version": str(torch.version.cuda),
            "cudnn_version": str(cudnn_version),
            "operating_system": platform.platform(),
        },
    )
    return receipt


def validate_crc32c_external_authority(
    environment_receipt: Path,
    crc32c_python: Path,
    crc32c_worker: Path,
) -> Mapping[str, Any]:
    import capture_lvef_c3_production_environment as capture

    receipt = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    if sha256_file(crc32c_python) != receipt.get(
        "crc32c_python_executable_sha256"
    ) or sha256_file(crc32c_worker) != receipt.get("crc32c_worker_sha256"):
        raise ProductionStageError("CRC32C_EXTERNAL_FILE_AUTHORITY_MISMATCH")
    try:
        probe = capture.probe_crc32c_runtime(
            crc32c_python,
            crc32c_worker,
            expected_python_sha256=str(
                receipt["crc32c_python_executable_sha256"]
            ),
        )
    except (capture.EnvironmentAuthorityError, OSError, subprocess.SubprocessError) as exc:
        raise ProductionStageError("CRC32C_EXTERNAL_RUNTIME_UNAVAILABLE") from exc
    expected = {
        "python_version": receipt.get("crc32c_python_version"),
        "google_crc32c_version": receipt.get("google_crc32c_version"),
        "google_crc32c_implementation": receipt.get(
            "google_crc32c_implementation"
        ),
        "google_crc32c_distribution_sha256": receipt.get(
            "google_crc32c_distribution_sha256"
        ),
        "google_crc32c_distribution_file_count": receipt.get(
            "google_crc32c_distribution_file_count"
        ),
        "known_vector_crc32c_base64": receipt.get(
            "google_crc32c_known_vector_base64"
        ),
    }
    if any(probe.get(key) != value for key, value in expected.items()):
        raise ProductionStageError("CRC32C_EXTERNAL_RUNTIME_AUTHORITY_MISMATCH")
    return receipt


def validate_environment_authority_for_scientific_commit(
    environment_receipt: Path,
    *,
    expected_environment_receipt_sha256: str,
    scientific_governing_commit: str,
) -> dict[str, Any]:
    """Bind one live runtime authority to an equal or ancestor code commit."""

    if not SHA256_RE.fullmatch(str(expected_environment_receipt_sha256)):
        raise ProductionStageError("ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH")
    expected_sha256 = str(expected_environment_receipt_sha256)
    if (
        not isinstance(scientific_governing_commit, str)
        or COMMIT_RE.fullmatch(scientific_governing_commit) is None
    ):
        raise ProductionStageError("ENVIRONMENT_AUTHORITY_COMMIT_INVALID")

    def bound_receipt_sha256() -> str:
        try:
            return sha256_file(environment_receipt)
        except ProductionStageError as exc:
            raise ProductionStageError(
                "ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH"
            ) from exc

    observed_sha256 = bound_receipt_sha256()
    if observed_sha256 != expected_sha256:
        raise ProductionStageError("ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH")

    try:
        receipt = validate_environment_receipt_against_current_runtime(
            environment_receipt
        )
    except ProductionStageError as exc:
        raise ProductionStageError(
            "ENVIRONMENT_RECEIPT_LIVE_RUNTIME_MISMATCH"
        ) from exc
    if bound_receipt_sha256() != expected_sha256:
        raise ProductionStageError("ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH")
    try:
        external_receipt = validate_crc32c_external_authority(
            environment_receipt,
            PINNED_CRC32C_PYTHON,
            CANONICAL_REPOSITORY_ROOT / "scripts/lvef_c3_crc32c_worker.py",
        )
    except ProductionStageError as exc:
        raise ProductionStageError(
            "ENVIRONMENT_RECEIPT_LIVE_RUNTIME_MISMATCH"
        ) from exc
    if (
        external_receipt != receipt
        or bound_receipt_sha256() != expected_sha256
    ):
        raise ProductionStageError("ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH")

    environment_commit = str(receipt["governing_commit"])
    try:
        repository = CANONICAL_REPOSITORY_ROOT.resolve(strict=True)
    except OSError as exc:
        raise ProductionStageError("CANONICAL_REPOSITORY_INVALID") from exc
    if repository != CANONICAL_REPOSITORY_ROOT or not repository.is_dir():
        raise ProductionStageError("CANONICAL_REPOSITORY_INVALID")
    git_environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def git_check(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(repository),
                    *arguments,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                env=git_environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionStageError(
                "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE"
            ) from exc

    for commit in (environment_commit, scientific_governing_commit):
        if git_check("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
            raise ProductionStageError(
                "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE"
            )

    if environment_commit == scientific_governing_commit:
        relation, status = "EQUAL", "ENVIRONMENT_AUTHORITY_COMMIT_EQUAL"
    else:
        ancestry = git_check(
            "merge-base",
            "--is-ancestor",
            environment_commit,
            scientific_governing_commit,
        )
        if ancestry.returncode == 1:
            raise ProductionStageError(
                "ENVIRONMENT_AUTHORITY_COMMIT_NOT_ANCESTOR"
            )
        if ancestry.returncode != 0:
            raise ProductionStageError(
                "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE"
            )
        relation, status = "ANCESTOR", "ENVIRONMENT_AUTHORITY_COMMIT_ANCESTOR"

    return {
        "status": status,
        "environment_receipt": receipt,
        "environment_receipt_sha256": observed_sha256,
        "environment_authority_commit": environment_commit,
        "scientific_governing_commit": scientific_governing_commit,
        "environment_authority_relation": relation,
    }


def validate_checkpoint_and_environment(
    checkpoint: Path,
    environment_receipt: Path,
    *,
    crc32c_python: Path | None = None,
    crc32c_worker: Path | None = None,
) -> dict[str, Any]:
    if checkpoint.name != CHECKPOINT_FILENAME:
        raise ProductionStageError("CHECKPOINT_FILENAME_MISMATCH")
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise ProductionStageError("CHECKPOINT_NOT_REGULAR")
    if checkpoint.stat(follow_symlinks=False).st_size != CHECKPOINT_BYTES:
        raise ProductionStageError("CHECKPOINT_SIZE_MISMATCH")
    if sha256_file(checkpoint) != CHECKPOINT_SHA256:
        raise ProductionStageError("CHECKPOINT_SHA256_MISMATCH")
    validate_environment_receipt_against_current_runtime(environment_receipt)
    if (crc32c_python is None) is not (crc32c_worker is None):
        raise ProductionStageError("CRC32C_EXTERNAL_AUTHORITY_PAIR_INCOMPLETE")
    if crc32c_python is not None and crc32c_worker is not None:
        validate_crc32c_external_authority(
            environment_receipt, crc32c_python, crc32c_worker
        )
    return {
        "checkpoint_identity_passed": True,
        "environment_receipt_complete": True,
        "encoder_only_required": True,
        "view_classifier_permitted": False,
    }


def _atomic_finalize_stage_directory(partial: Path, final: Path) -> None:
    if final.exists() or final.is_symlink():
        raise ProductionStageError("STAGE_FINAL_OUTPUT_ALREADY_EXISTS")
    if partial.is_symlink() or not partial.is_dir():
        raise ProductionStageError("STAGE_PARTIAL_OUTPUT_INVALID")
    try:
        os.rename(partial, final)
    except OSError as exc:
        raise ProductionStageError("STAGE_ATOMIC_DIRECTORY_RENAME_FAILED") from exc


def canonical_embedding_root_for_extraction_batch(
    extraction_batch_root: Path, *, batch_id: str
) -> Path:
    if (
        extraction_batch_root.name != batch_id
        or extraction_batch_root.parent.name != "extracted_cache"
    ):
        raise ProductionStageError("DICOM_EMBEDDING_TOPOLOGY_INVALID")
    return (
        extraction_batch_root.parent.parent
        / "batches"
        / batch_id
        / "echoprime"
    )


def _stage_completion_receipt(
    *, stage: str, stage_directory: Path, batch_id: str, attempt_id: str,
    runtime_authority: Mapping[str, Any], input_manifest: Path,
    artifact_names: Sequence[str],
) -> dict[str, Any]:
    artifacts = {
        name: (
            technical_disposition_manifest_sha256(stage_directory / name)
            if name == "technical_disposition_manifest.restricted.csv"
            else sha256_file(stage_directory / name)
        )
        for name in artifact_names
    }
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_stage_completion_receipt_v1",
        "status": "PASS_STAGE_OUTPUT_ATOMICALLY_FINALIZABLE",
        "stage": stage,
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "runtime_authority": dict(runtime_authority),
        "input_manifest_sha256": sha256_file(input_manifest),
        "artifacts": artifacts,
    }


def validate_completed_stage_for_recovery(
    *, stage_directory: Path, stage: str, batch_id: str, attempt_id: str,
    runtime_authority: Mapping[str, Any], input_manifest: Path,
    artifact_names: Sequence[str], summary_name: str,
) -> dict[str, Any]:
    """Validate a completed stage after a crash before its ledger promotion."""
    if stage_directory.is_symlink() or not stage_directory.is_dir():
        raise ProductionStageError("RECOVERY_STAGE_DIRECTORY_INVALID")
    receipt = load_json_object(
        stage_directory / "stage_completion_receipt.restricted.json",
        "STAGE_COMPLETION_RECEIPT",
    )
    expected = _stage_completion_receipt(
        stage=stage,
        stage_directory=stage_directory,
        batch_id=batch_id,
        attempt_id=attempt_id,
        runtime_authority=runtime_authority,
        input_manifest=input_manifest,
        artifact_names=artifact_names,
    )
    if receipt != expected:
        raise ProductionStageError("STAGE_COMPLETION_RECOVERY_AUTHORITY_MISMATCH")
    return load_json_object(stage_directory / summary_name, "RECOVERED_STAGE_SUMMARY")


def run_production_dicom_extraction(
    *,
    verified_download_manifest: Path,
    download_root: Path,
    batch_output_root: Path,
    workers: int,
    batch_id: str,
    attempt_id: str,
    runtime_authority: Mapping[str, Any],
    planned_batch: Mapping[str, Any],
    embedding_output_root: Path,
) -> dict[str, Any]:
    """Execute header audit and extraction after an external authorization gate.

    This function itself performs no authorization inference; callers must run
    :func:`validate_stage_authorization` first.  Any failure leaves the uniquely
    named partial stage directory intact as restricted evidence.
    """

    if workers < 1:
        raise ProductionStageError("EXTRACTION_WORKERS_INVALID")
    require_projectnb_path(download_root, must_exist=True)
    batch_root = require_projectnb_path(batch_output_root, must_exist=True)
    if verified_download_manifest.is_symlink() or not verified_download_manifest.is_file():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_REGULAR")
    import pandas as pd  # optional; execution path only
    import lvef_reconstruction_smoke as smoke

    frame = pd.read_csv(verified_download_manifest, low_memory=False)
    required = {
        "subject_id",
        "study_id",
        "source_relative_path",
        "download_ok",
        "observed_sha256",
        "physical_source_key",
    }
    if set(frame.columns) != required or frame.empty:
        raise ProductionStageError("DOWNLOAD_MANIFEST_SCHEMA_MISMATCH")
    if not frame["download_ok"].map(smoke.parse_bool).all():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_FULLY_VERIFIED")
    if frame["source_relative_path"].duplicated().any():
        raise ProductionStageError("DUPLICATE_PHYSICAL_SOURCE")
    records = frame.sort_values("source_relative_path", kind="mergesort").to_dict(
        orient="records"
    )
    for record in records:
        record["source_authority_relative_path"] = safe_relative_path(
            record["source_relative_path"]
        )
        record["smoke_role"] = "production_selected"
        _validate_hash(record["observed_sha256"], "DOWNLOAD_SHA256_INVALID")
        _validate_hash(record["physical_source_key"], "PHYSICAL_SOURCE_KEY_INVALID")
        record["download_sha256"] = record["observed_sha256"]
        record["source_relative_path"] = f"{record['physical_source_key']}.dcm"

    partial = batch_root / "dicom_extraction.partial"
    final = batch_root / "dicom_extraction"
    if partial.exists() or partial.is_symlink() or final.exists() or final.is_symlink():
        raise ProductionStageError("DICOM_EXTRACTION_ATTEMPT_ALREADY_EXISTS")
    partial.mkdir(mode=0o700)
    clips_root = partial / "clips"
    clips_root.mkdir(mode=0o700)
    raw_authority_by_source: dict[str, tuple[str, tuple[int, ...]]] = {}
    for record in records:
        downloaded = download_root / record["source_relative_path"]
        try:
            observed_digest, observed_identity = (
                _stable_nofollow_sha256_authority(downloaded)
            )
        except ProductionStageError:
            smoke.write_json_atomic(
                partial / "failure.summary.json",
                {"status": "FAIL_DOWNLOAD_FILE_NOT_REGULAR", "identifiers_emitted": False, "paths_emitted": False},
            )
            raise ProductionStageError("DOWNLOADED_DICOM_NOT_REGULAR")
        if observed_digest != record["observed_sha256"]:
            smoke.write_json_atomic(
                partial / "failure.summary.json",
                {"status": "FAIL_POSTDOWNLOAD_HASH_MISMATCH", "identifiers_emitted": False, "paths_emitted": False},
            )
            raise ProductionStageError("POSTDOWNLOAD_DICOM_HASH_MISMATCH")
        raw_authority_by_source[str(record["physical_source_key"])] = (
            observed_digest,
            observed_identity,
        )
    if workers == 1:
        header_rows = [
            smoke._dicom_header_row(record, str(download_root)) for record in records
        ]
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            header_rows = list(
                pool.map(
                    smoke._dicom_header_row,
                    records,
                    [str(download_root)] * len(records),
                )
            )
    header = pd.DataFrame(header_rows).sort_values(
        "source_relative_path", kind="mergesort"
    ).reset_index(drop=True)
    physical_by_path = {
        record["source_relative_path"]: record["physical_source_key"] for record in records
    }
    cine_records = [
        record
        for record in header.to_dict(orient="records")
        if record["read_ok"] is True and record["is_multiframe"] is True
    ]
    if workers == 1:
        extraction_rows = [
            smoke._extract_one(record, str(download_root), str(clips_root))
            for record in cine_records
        ]
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            extraction_rows = list(
                pool.map(
                    smoke._extract_one,
                    cine_records,
                    [str(download_root)] * len(cine_records),
                    [str(clips_root)] * len(cine_records),
                )
            )
    for extracted in extraction_rows:
        extracted["physical_source_key"] = physical_by_path[
            extracted["source_relative_path"]
        ]
        extracted["pixel_decode_ok"] = extracted.get("decode_color_status") == "PASS"
    extraction_rows.sort(key=lambda row: str(row["source_relative_path"]))
    decode_by_path = {
        row["source_relative_path"]: row["pixel_decode_ok"] for row in extraction_rows
    }
    header["pixel_decode_ok"] = header["source_relative_path"].map(
        lambda value: decode_by_path.get(value, False)
    )
    header_records = header.to_dict(orient="records")
    smoke.write_csv_atomic(partial / "dicom_audit.restricted.csv", header)
    extraction = pd.DataFrame(extraction_rows)
    smoke.write_csv_atomic(partial / "extraction_manifest.restricted.csv", extraction)
    extraction_provenance = (
        smoke.summarize_extraction(extraction) if extraction_rows else None
    )
    dicom_summary = validate_production_dicom_rows(
        header_records,
        expected_objects=len(records),
        expected_studies=int(frame["study_id"].nunique()),
    )
    if dicom_summary["n_unreadable"] or dicom_summary["n_pixel_decode_failures"]:
        failure_summary = {
            "status": "FAIL_DICOM_OR_PIXEL_DECODE_GATE",
            **dicom_summary,
            "identifiers_emitted": False,
            "paths_emitted": False,
        }
        if extraction_provenance is not None:
            failure_summary.update(
                {
                    "schema_version": 2,
                    "artifact_type": (
                        "lvef_c3_batch_dicom_or_extraction_failure_summary_v2"
                    ),
                    "extraction_provenance": extraction_provenance,
                }
            )
        smoke.write_json_atomic(
            partial / "failure.summary.json",
            failure_summary,
        )
        raise ProductionStageError("DICOM_OR_PIXEL_DECODE_GATE_FAILED")
    try:
        disposition_context = build_extraction_disposition_context(
            extraction_rows=extraction_rows,
            planned_batch=planned_batch,
            verified_download_rows=records,
            dicom_rows=header_records,
            download_root=download_root,
            clips_root=clips_root,
            raw_authority_by_source=raw_authority_by_source,
            embedding_output_root=embedding_output_root,
        )
        extraction_summary = validate_production_extraction_rows(
            extraction_rows,
            expected_cines=dicom_summary["n_multiframe_candidates"],
            disposition_context=disposition_context,
            clips_root=clips_root,
        )
        disposition_rows = technical_disposition_manifest_rows(
            extraction_rows, context=disposition_context
        )
        validate_technical_disposition_manifest_rows(
            extraction_rows,
            disposition_rows,
            context=disposition_context,
        )
    except ProductionStageError as exc:
        smoke.write_json_atomic(
            partial / "failure.summary.json",
            {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_extraction_failure_summary_v2",
                "status": "FAIL_EXTRACTION_GATE",
                "error_code": exc.code,
                "extraction_provenance": extraction_provenance,
                "identifiers_emitted": False,
                "paths_emitted": False,
            },
        )
        raise
    try:
        write_technical_disposition_manifest_no_clobber(
            partial / "technical_disposition_manifest.restricted.csv",
            disposition_rows,
        )
        summary = {
            "schema_version": 2,
            "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v2",
            "status": extraction_summary["status"],
            **dicom_summary,
            **extraction_summary,
            "technical_disposition_policy_version": (
                OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_sha256": (
                technical_disposition_manifest_sha256(
                    partial / "technical_disposition_manifest.restricted.csv"
                )
            ),
            "identifiers_emitted": False,
            "paths_emitted": False,
        }
        if set(summary) != DICOM_EXTRACTION_SUMMARY_KEYS_V2:
            raise ProductionStageError(
                "DICOM_EXTRACTION_SUMMARY_INTERNAL_SCHEMA_INVALID"
            )
        smoke.write_json_atomic(
            partial / "dicom_extraction.summary.json", summary
        )
        smoke.write_json_atomic(
            partial / "stage_completion_receipt.restricted.json",
            _stage_completion_receipt(
                stage="DICOM_EXTRACTION",
                stage_directory=partial,
                batch_id=batch_id,
                attempt_id=attempt_id,
                runtime_authority=runtime_authority,
                input_manifest=verified_download_manifest,
                artifact_names=(
                    "dicom_audit.restricted.csv",
                    "extraction_manifest.restricted.csv",
                    "technical_disposition_manifest.restricted.csv",
                    "dicom_extraction.summary.json",
                ),
            ),
        )
        _atomic_finalize_stage_directory(partial, final)
        return summary
    except Exception as exc:
        if partial.is_dir() and not partial.is_symlink():
            for name in (
                "dicom_extraction.summary.json",
                "stage_completion_receipt.restricted.json",
            ):
                artifact = partial / name
                try:
                    metadata = artifact.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid():
                    artifact.unlink()
            error_code = (
                exc.code
                if isinstance(exc, ProductionStageError)
                else "EXTRACTION_PUBLICATION_FAILED"
            )
            smoke.write_json_atomic(
                partial / "failure.summary.json",
                {
                    "schema_version": 2,
                    "artifact_type": (
                        "lvef_c3_batch_extraction_failure_summary_v2"
                    ),
                    "status": "FAIL_EXTRACTION_PUBLICATION",
                    "error_code": error_code,
                    "extraction_provenance": extraction_provenance,
                    "identifiers_emitted": False,
                    "paths_emitted": False,
                },
            )
        if isinstance(exc, ProductionStageError):
            raise
        raise ProductionStageError("EXTRACTION_PUBLICATION_FAILED") from exc


def run_production_echoprime(
    *,
    extraction_manifest: Path,
    extraction_root: Path,
    technical_disposition_manifest: Path | None = None,
    dicom_audit: Path | None = None,
    dicom_extraction_summary: Path | None = None,
    verified_download_manifest: Path | None = None,
    selected_batch_manifest: Path,
    checkpoint: Path,
    environment_receipt: Path,
    orchestration_contract: Path,
    batch_plan: Path,
    batch_id: str,
    batch_output_root: Path,
    batch_size: int,
    seed: int,
    attempt_id: str,
    runtime_authority: Mapping[str, Any],
    requirements: Any | None = None,
) -> dict[str, Any]:
    """Run encoder-only EchoPrime and deterministic study mean pooling.

    Heavy imports, checkpoint loading, and CUDA access occur only inside this
    explicitly called execution function, after the wrapper authorization gate.
    """

    if batch_size < 1:
        raise ProductionStageError("EMBEDDING_BATCH_SIZE_INVALID")
    validate_checkpoint_and_environment(checkpoint, environment_receipt)
    extracted_root = require_projectnb_path(extraction_root, must_exist=True)
    batch_root = require_projectnb_path(batch_output_root, must_exist=True)
    import numpy as np
    import pandas as pd
    import torch
    import torchvision
    import lvef_reconstruction_smoke as smoke
    import lvef_c3_orchestration_core as core
    import preserve_lvef_c3_production_batch as preservation

    environment = load_json_object(environment_receipt, "ENVIRONMENT_RECEIPT")
    if (
        str(torch.__version__) != str(environment["torch_version"])
        or str(torchvision.__version__) != str(environment["torchvision_version"])
        or str(torch.version.cuda) != str(environment["cuda_version"])
        or str(torch.backends.cudnn.version()) != str(environment["cudnn_version"])
    ):
        raise ProductionStageError("RUNNING_TORCH_CUDA_ENVIRONMENT_MISMATCH")

    extraction = pd.read_csv(extraction_manifest, low_memory=False)
    selected = pd.read_csv(selected_batch_manifest, low_memory=False)
    if set(selected.columns) != {"subject_id", "study_id"} or selected.empty:
        raise ProductionStageError("SELECTED_BATCH_MANIFEST_SCHEMA_MISMATCH")
    if selected["study_id"].duplicated().any() or selected["subject_id"].duplicated().any():
        raise ProductionStageError("SELECTED_BATCH_OWNERSHIP_NOT_ONE_TO_ONE")
    contract = core.load_orchestration_contract(orchestration_contract)
    plan = core.load_strict_json(batch_plan)
    plan_sha256 = core.validate_current_batch_plan_v3(
        plan,
        requirements=(
            requirements
            if requirements is not None
            else core.production_requirements(contract)
        ),
    )
    if requirements is not None:
        normalized_runtime = core.validate_runtime_authority(runtime_authority)
        if (
            normalized_runtime["batch_plan_sha256"] != plan_sha256
            or any(
                normalized_runtime[key] != str(plan["authority"][key])
                for key in core.PLAN_AUTHORITY_KEYS
            )
            or normalized_runtime["checkpoint_sha256"] != sha256_file(checkpoint)
            or normalized_runtime["environment_receipt_sha256"]
            != sha256_file(environment_receipt)
        ):
            raise ProductionStageError("SCOPED_ECHOPRIME_RUNTIME_AUTHORITY_MISMATCH")
    planned_batch = next((item for item in plan["batches"] if item["batch_id"] == batch_id), None)
    if planned_batch is None:
        raise ProductionStageError("SELECTED_BATCH_NOT_PLANNED")
    expected_selected = sorted(
        (str(item["subject_id"]), str(item["study_id"])) for item in planned_batch["studies"]
    )
    observed_selected = sorted(
        zip(selected["subject_id"].astype(str), selected["study_id"].astype(str))
    )
    if observed_selected != expected_selected:
        raise ProductionStageError("SELECTED_BATCH_PLAN_MEMBERSHIP_MISMATCH")
    extraction_records = extraction.to_dict(orient="records")
    expected_cines = len(extraction_records)
    technical_path = technical_disposition_manifest or (
        extraction_manifest.parent
        / "technical_disposition_manifest.restricted.csv"
    )
    summary_path = dicom_extraction_summary or (
        extraction_manifest.parent / "dicom_extraction.summary.json"
    )
    technical_rows, technical_manifest_sha256 = (
        read_technical_disposition_manifest_authority(technical_path)
    )
    if verified_download_manifest is None:
        extraction_stage = extraction_manifest.parent
        if (
            extraction_stage.name != "dicom_extraction"
            or extraction_stage.parent.name != batch_id
            or extraction_stage.parent.parent.name != "extracted_cache"
        ):
            raise ProductionStageError(
                "ECHOPRIME_COMPLETED_EXTRACTION_TOPOLOGY_INVALID"
            )
        verified_download_manifest = (
            extraction_stage.parent.parent.parent
            / "raw"
            / batch_id
            / "verified_download_manifest.restricted.csv"
        )
    recovered_summary = validate_completed_stage_for_recovery(
        stage_directory=extraction_manifest.parent,
        stage="DICOM_EXTRACTION",
        batch_id=batch_id,
        attempt_id=attempt_id,
        runtime_authority=runtime_authority,
        input_manifest=verified_download_manifest,
        artifact_names=(
            "dicom_audit.restricted.csv",
            "extraction_manifest.restricted.csv",
            "technical_disposition_manifest.restricted.csv",
            "dicom_extraction.summary.json",
        ),
        summary_name="dicom_extraction.summary.json",
    )
    failed_records = [
        row for row in extraction_records if row.get("write_ok") is not True
    ]
    if failed_records:
        audit_payload = _read_canonical_dicom_audit_authority(
            extraction_manifest=extraction_manifest,
            dicom_audit=dicom_audit,
        )
        audit_frame = pd.read_csv(io.BytesIO(audit_payload), low_memory=False)
        for field in ("read_ok", "is_multiframe", "pixel_decode_ok"):
            if field not in audit_frame:
                raise ProductionStageError("DICOM_AUDIT_SCHEMA_MISMATCH")
            audit_frame[field] = audit_frame[field].map(smoke.parse_bool)
        disposition_context = context_from_completed_extraction_authority(
            extraction_rows=extraction_records,
            dicom_rows=audit_frame.to_dict(orient="records"),
            manifest_rows=technical_rows,
            clips_root=extracted_root,
            embedding_output_root=batch_root / "echoprime",
        )
        extraction_summary = validate_production_extraction_rows(
            extraction_records,
            expected_cines=expected_cines,
            disposition_context=disposition_context,
            clips_root=extracted_root,
        )
        technical_summary = validate_technical_disposition_manifest_rows(
            extraction_records,
            technical_rows,
            context=disposition_context,
        )
    else:
        disposition_context = None
        extraction_summary = validate_production_extraction_rows(
            extraction_records,
            expected_cines=expected_cines,
            clips_root=extracted_root,
        )
        technical_summary = validate_technical_disposition_manifest_rows(
            extraction_records, technical_rows
        )
        if technical_summary["n_object_technical_dispositions"] != 0:
            raise ProductionStageError(
                "ECHOPRIME_ZERO_DISPOSITION_MANIFEST_NOT_EMPTY"
            )
    prior_summary = load_json_object(summary_path, "DICOM_EXTRACTION_SUMMARY")
    summary_binding_keys = (
        "n_requested_cines",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_studies_affected_by_technical_disposition",
        "n_new_no_cine_studies",
        "technical_disposition_counts_by_class",
        "all_extraction_rows_resolved",
        "all_technical_dispositions_retained",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
    )
    if (
        set(prior_summary) != DICOM_EXTRACTION_SUMMARY_KEYS_V2
        or prior_summary.get("schema_version") != 2
        or recovered_summary != prior_summary
        or prior_summary.get("artifact_type")
        != "lvef_c3_batch_dicom_extraction_summary_v2"
        or prior_summary.get("status") != extraction_summary["status"]
        or prior_summary.get("technical_disposition_policy_version")
        != OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        or prior_summary.get("technical_disposition_manifest_sha256")
        != technical_manifest_sha256
        or prior_summary.get("n_multiframe_candidates") != expected_cines
        or any(
            prior_summary.get(key) != extraction_summary[key]
            for key in summary_binding_keys
        )
        or any(
            prior_summary.get(key) != technical_summary[key]
            for key in technical_summary
        )
        or expected_cines
        != extraction_summary["n_successfully_extracted_cines"]
        + extraction_summary["n_object_technical_dispositions"]
        or extraction_summary["n_new_no_cine_studies"] != 0
        or extraction_summary["unaccounted_multiframe_objects"] != 0
    ):
        raise ProductionStageError(
            "ECHOPRIME_TECHNICAL_DISPOSITION_SUMMARY_MISMATCH"
        )
    successful_records = [
        row for row in extraction_records if row.get("write_ok") is True
    ]
    work = pd.DataFrame(successful_records, columns=extraction.columns).sort_values(
        ["study_id", "clip_key"], kind="mergesort"
    ).reset_index(drop=True)
    if not set(work["study_id"]).issubset(set(selected["study_id"])):
        raise ProductionStageError("OUTSIDE_SELECTED_STUDY_IN_EXTRACTION")
    if not work.groupby("study_id", dropna=False)["subject_id"].nunique(dropna=False).eq(1).all():
        raise ProductionStageError("EXTRACTION_STUDY_OWNERSHIP_CONFLICT")

    partial = batch_root / "echoprime.partial"
    final = batch_root / "echoprime"
    if partial.exists() or partial.is_symlink() or final.exists() or final.is_symlink():
        raise ProductionStageError("ECHOPRIME_ATTEMPT_ALREADY_EXISTS")
    partial.mkdir(mode=0o700)
    smoke.configure_torch_determinism(torch, seed)
    if not torch.cuda.is_available():
        raise ProductionStageError("CUDA_UNAVAILABLE")
    device = torch.device("cuda")
    model = torchvision.models.video.mvit_v2_s(weights=None)
    model.head[-1] = torch.nn.Linear(model.head[-1].in_features, 512)
    state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval().to(device)
    for parameter in model.parameters():
        parameter.requires_grad = False
    ordered_records = work.to_dict(orient="records")
    vectors: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, len(ordered_records), batch_size):
            mini_records = ordered_records[start : start + batch_size]
            tensors = [
                smoke._prepare_encoder_input(
                    smoke._load_extracted_frames(record, extracted_root), torch
                )
                for record in mini_records
            ]
            batch = torch.stack(tensors, dim=0).to(device)
            result = model(batch).detach().cpu().numpy().astype(np.float32, copy=False)
            if result.shape != (len(tensors), 512) or not np.isfinite(result).all():
                raise ProductionStageError("ENCODER_OUTPUT_GATE_FAILED")
            vectors.extend(result)
            del batch, tensors, result
    clip_array = np.stack(vectors).astype(np.float32, copy=False)
    validate_embedding_values(clip_array.tolist(), expected_rows=len(work))
    clip_rows: list[dict[str, Any]] = []
    for index, (record, vector) in enumerate(zip(work.to_dict(orient="records"), clip_array)):
        clip_rows.append(
            {
                "embedding_idx": index,
                "subject_id": record["subject_id"],
                "study_id": record["study_id"],
                "clip_key": record["clip_key"],
                "physical_source_key": record["physical_source_key"],
                "embedding_l2_norm": float(np.linalg.norm(vector.astype(np.float64))),
                "embedding_sha256": smoke.array_content_sha256(vector),
                "write_ok": True,
            }
        )
    clip_manifest = pd.DataFrame(clip_rows)
    if clip_manifest["clip_key"].duplicated().any() or clip_manifest["physical_source_key"].duplicated().any():
        raise ProductionStageError("DUPLICATE_EMBEDDED_CLIP_OR_SOURCE")
    disposed_clip_keys = {str(row["clip_key"]) for row in technical_rows}
    disposed_source_keys = {
        str(row["physical_source_key"]) for row in technical_rows
    }
    if (
        disposed_clip_keys.intersection(clip_manifest["clip_key"].astype(str))
        or disposed_source_keys.intersection(
            clip_manifest["physical_source_key"].astype(str)
        )
        or len(clip_manifest)
        != extraction_summary["n_successfully_extracted_cines"]
    ):
        raise ProductionStageError(
            "ECHOPRIME_DISPOSED_OBJECT_ENTERED_EMBEDDING_SET"
        )
    study_rows: list[dict[str, Any]] = []
    for study_id, group in clip_manifest.groupby("study_id", sort=True, dropna=False):
        study_rows.append(
            {
                "study_idx": len(study_rows),
                "subject_id": group["subject_id"].iloc[0],
                "study_id": study_id,
                "n_clips": len(group),
            }
        )
    try:
        study_array = preservation.mean_pool_study_embeddings(
            clip_embeddings=clip_array,
            clip_rows=clip_rows,
            study_rows=study_rows,
        )
    except preservation.BatchPreservationError as exc:
        raise ProductionStageError("POOLED_EMBEDDING_SEMANTICS_INVALID") from exc
    for row, vector in zip(study_rows, study_array, strict=True):
        row["embedding_sha256"] = smoke.array_content_sha256(vector)
    study_manifest = pd.DataFrame(study_rows)
    pooled = set(study_manifest["study_id"])
    disposition = selected.copy()
    disposition["disposition"] = disposition["study_id"].map(
        lambda value: "IMAGING_ELIGIBLE" if value in pooled else "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    )
    expected_no_cine_keys = list(
        planned_batch["prespecified_no_cine_study_keys"]
    )
    expected_no_cine = {
        (str(row["subject_id"]), str(row["study_id"]))
        for row in expected_no_cine_keys
    }
    actual_no_cine = {
        (str(row["subject_id"]), str(row["study_id"]))
        for row in disposition.to_dict(orient="records")
        if row["disposition"] == "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    }
    actual_no_cine_keys = [
        {"subject_id": subject, "study_id": study}
        for subject, study in sorted(
            actual_no_cine, key=lambda pair: (int(pair[0]), int(pair[1]))
        )
    ]
    new_no_cine_studies = len(actual_no_cine - expected_no_cine)
    if (
        actual_no_cine != expected_no_cine
        or len(actual_no_cine) != planned_batch["expected_no_cine_studies"]
        or core.canonical_json_sha256(expected_no_cine_keys)
        != planned_batch["prespecified_no_cine_study_set_sha256"]
    ):
        raise ProductionStageError("ECHOPRIME_NO_CINE_IDENTITY_MISMATCH")
    smoke.write_npz_atomic(partial / "clip_embeddings.restricted.npz", embeddings=clip_array)
    smoke.write_csv_atomic(partial / "clip_manifest.restricted.csv", clip_manifest)
    smoke.write_npz_atomic(partial / "study_embeddings.restricted.npz", embeddings=study_array)
    smoke.write_csv_atomic(partial / "study_manifest.restricted.csv", study_manifest)
    smoke.write_csv_atomic(partial / "study_disposition.restricted.csv", disposition)
    summary = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_batch_echoprime_pooling_summary_v2",
        "status": "PASS_ECHOPRIME_AND_POOLING",
        "n_clip_embeddings": int(len(clip_array)),
        "n_pooled_studies": int(len(study_array)),
        "n_no_cine_studies": len(actual_no_cine),
        "n_new_no_cine_studies": new_no_cine_studies,
        "prespecified_no_cine_study_set_sha256": planned_batch[
            "prespecified_no_cine_study_set_sha256"
        ],
        "actual_no_cine_study_set_sha256": core.canonical_json_sha256(
            actual_no_cine_keys
        ),
        "all_no_cine_studies_prespecified": True,
        "n_object_technical_dispositions": technical_summary[
            "n_object_technical_dispositions"
        ],
        "n_studies_affected_by_technical_disposition": technical_summary[
            "n_studies_affected_by_technical_disposition"
        ],
        "technical_disposition_counts_by_class": technical_summary[
            "technical_disposition_counts_by_class"
        ],
        "technical_disposition_policy_version": (
            OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "technical_disposition_manifest_sha256": technical_manifest_sha256,
        "all_extraction_rows_resolved": True,
        "all_successful_extractions_embedded": True,
        "all_technical_dispositions_retained": True,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "embedding_dimension": 512,
        "embedding_dtype": "float32",
        "all_finite": True,
        "encoder_only": True,
        "view_classifier_used": False,
        "pooling": "stable_clip_key_order_float64_mean_then_float32",
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }
    if set(summary) != ECHOPRIME_SUMMARY_KEYS_V2:
        raise ProductionStageError("ECHOPRIME_SUMMARY_INTERNAL_SCHEMA_INVALID")
    smoke.write_json_atomic(partial / "echoprime_pooling.summary.json", summary)
    smoke.write_json_atomic(
        partial / "stage_completion_receipt.restricted.json",
        _stage_completion_receipt(
            stage="ECHOPRIME_EMBEDDING",
            stage_directory=partial,
            batch_id=batch_id,
            attempt_id=attempt_id,
            runtime_authority=runtime_authority,
            input_manifest=extraction_manifest,
            artifact_names=(
                "clip_embeddings.restricted.npz",
                "clip_manifest.restricted.csv",
                "study_embeddings.restricted.npz",
                "study_manifest.restricted.csv",
                "study_disposition.restricted.csv",
                "echoprime_pooling.summary.json",
            ),
        ),
    )
    _atomic_finalize_stage_directory(partial, final)
    return summary


def advance_stage_ledger(
    *, input_ledger: Path, output_ledger: Path, receipt_root: Path,
    batch_id: str, transitions: Sequence[tuple[str, str]],
    expected_authority: Mapping[str, Any], expected_attempt_id: str,
    expected_object_keys: set[str],
) -> dict[str, Any]:
    """Apply an exact consecutive transition chain and preserve every receipt."""
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    if not isinstance(ledger, Mapping):
        raise ProductionStageError("INPUT_LEDGER_NOT_MAPPING")
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    if batch_id not in ledger["batches"]:
        raise ProductionStageError("LEDGER_BATCH_NOT_PLANNED")
    if receipt_root.is_symlink() or (receipt_root.exists() and not receipt_root.is_dir()):
        raise ProductionStageError("TRANSITION_RECEIPT_ROOT_COLLISION")
    receipt_root.mkdir(mode=0o700, exist_ok=True)
    updated = ledger
    for target_state, output_sha in transitions:
        _validate_hash(output_sha, "TRANSITION_OUTPUT_HASH_INVALID")
        batch = updated["batches"][batch_id]
        predecessor = (
            batch["events"][-1]["receipt_sha256"]
            if batch["events"]
            else updated["authority"]["batch_plan_sha256"]
        )
        receipt = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": updated["attempt_id"],
            "batch_id": batch_id,
            "from_state": batch["state"],
            "to_state": target_state,
            "status": "PASS",
            "authority": updated["authority"],
            "input_receipt_sha256": [predecessor],
            "output_manifest_sha256": output_sha,
        }
        updated = core.apply_transition(updated, receipt)
        receipt_path = receipt_root / f"{target_state.lower()}.restricted.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            if core.load_strict_json(receipt_path) != receipt:
                raise ProductionStageError("TRANSITION_RECOVERY_RECEIPT_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                receipt_path,
                receipt,
                attempt_id=str(updated["attempt_id"]),
            )
    if output_ledger.exists() or output_ledger.is_symlink():
        if core.load_strict_json(output_ledger) != updated:
            raise ProductionStageError("TRANSITION_RECOVERY_LEDGER_MISMATCH")
    else:
        core.atomic_write_json_no_clobber(
            output_ledger, updated, attempt_id=str(updated["attempt_id"])
        )
    return updated


def validate_stage_predecessor(
    *, input_ledger: Path, batch_id: str, expected_state: str,
    expected_authority: Mapping[str, Any], expected_attempt_id: str,
    expected_object_keys: set[str], bound_manifest: Path,
    predecessor_transition_receipt: Path | None = None,
) -> dict[str, Any]:
    """Bind a stage input to the current authority and predecessor receipt."""
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    batch = ledger["batches"].get(batch_id)
    if not isinstance(batch, Mapping) or batch.get("state") != expected_state:
        raise ProductionStageError("PREDECESSOR_LEDGER_STATE_MISMATCH")
    manifest_sha = sha256_file(bound_manifest)
    if expected_state == "DOWNLOAD_VERIFIED":
        if batch.get("download_manifest_sha256") != manifest_sha:
            raise ProductionStageError("DOWNLOAD_MANIFEST_LEDGER_HASH_MISMATCH")
    else:
        events = batch.get("events")
        if (
            not isinstance(events, list)
            or not events
            or events[-1].get("to_state") != expected_state
        ):
            raise ProductionStageError("STAGE_MANIFEST_PREDECESSOR_HASH_MISMATCH")
        if predecessor_transition_receipt is None:
            raise ProductionStageError("PREDECESSOR_TRANSITION_RECEIPT_REQUIRED")
        transition = core.load_strict_json(predecessor_transition_receipt)
        if (
            core.canonical_json_sha256(transition) != events[-1].get("receipt_sha256")
            or transition.get("attempt_id") != expected_attempt_id
            or transition.get("batch_id") != batch_id
            or transition.get("to_state") != expected_state
            or transition.get("authority") != expected_authority
            or transition.get("output_manifest_sha256") != manifest_sha
        ):
            raise ProductionStageError("STAGE_MANIFEST_PREDECESSOR_HASH_MISMATCH")
    return ledger


def validate_download_manifest_plan_membership(
    path: Path, planned_batch: Mapping[str, Any]
) -> None:
    expected_header = [
        "subject_id", "study_id", "source_relative_path", "download_ok",
        "observed_sha256", "physical_source_key",
    ]
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("DOWNLOAD_MANIFEST_NOT_REGULAR")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_header or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ProductionStageError("DOWNLOAD_MANIFEST_SCHEMA_MISMATCH")
        rows = list(reader)
    expected = {
        (
            str(row["subject_id"]), str(row["study_id"]),
            str(row["source_relative_path"]), str(row["source_object_key"]),
        )
        for row in planned_batch["objects"]
    }
    observed = {
        (
            str(row["subject_id"]), str(row["study_id"]),
            str(row["source_relative_path"]), str(row["physical_source_key"]),
        )
        for row in rows
    }
    if len(rows) != planned_batch["n_objects"] or observed != expected:
        raise ProductionStageError("DOWNLOAD_MANIFEST_PLAN_MEMBERSHIP_MISMATCH")
    if any(row["download_ok"] != "true" or not SHA256_RE.fullmatch(row["observed_sha256"]) for row in rows):
        raise ProductionStageError("DOWNLOAD_MANIFEST_VERIFICATION_INVALID")


def validate_extraction_manifest_plan_membership(
    path: Path,
    planned_batch: Mapping[str, Any],
    technical_disposition_manifest: Path | None = None,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProductionStageError("EXTRACTION_MANIFEST_NOT_REGULAR")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ProductionStageError("EXTRACTION_MANIFEST_SCHEMA_MISMATCH")
        required = {
            "subject_id", "study_id", "physical_source_key", "clip_key",
            "npz_sha256", "write_ok", "failure_substage",
        }
        if not required.issubset(reader.fieldnames):
            raise ProductionStageError("EXTRACTION_MANIFEST_SCHEMA_MISMATCH")
        rows = list(reader)
    expected_ownership = {
        str(row["source_object_key"]): (str(row["subject_id"]), str(row["study_id"]))
        for row in planned_batch["objects"]
    }
    seen: set[str] = set()
    disposed: set[tuple[str, str, str, str]] = set()
    if technical_disposition_manifest is not None:
        disposed = {
            (
                row["subject_id"], row["study_id"], row["clip_key"],
                row["physical_source_key"],
            )
            for row in read_technical_disposition_manifest(
                technical_disposition_manifest
            )
        }
    observed_disposed: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = str(row["physical_source_key"])
        if key in seen or expected_ownership.get(key) != (
            str(row["subject_id"]), str(row["study_id"])
        ):
            raise ProductionStageError("EXTRACTION_MANIFEST_PLAN_MEMBERSHIP_MISMATCH")
        seen.add(key)
        _validate_hash(row["clip_key"], "INVALID_CLIP_KEY")
        identity = (
            str(row["subject_id"]), str(row["study_id"]),
            str(row["clip_key"]), key,
        )
        write_ok = _technical_manifest_boolean(row["write_ok"])
        if write_ok is True:
            _validate_hash(row["npz_sha256"], "INVALID_EXTRACTION_HASH")
            if identity in disposed:
                raise ProductionStageError(
                    "EXTRACTION_DISPOSITION_SUCCESS_OVERLAP"
                )
        elif (
            write_ok is False
            and row["failure_substage"] == "SOURCE_SIGNAL_QUALITY_FAILURE"
            and row["npz_sha256"] == ""
            and identity in disposed
        ):
            observed_disposed.add(identity)
        else:
            raise ProductionStageError(
                "EXTRACTION_MANIFEST_UNRESOLVED_ROW"
            )
    if observed_disposed != disposed:
        raise ProductionStageError(
            "EXTRACTION_DISPOSITION_MANIFEST_MEMBERSHIP_MISMATCH"
        )


def record_stage_failure(
    *, input_ledger: Path, output_ledger: Path, receipt_root: Path,
    batch_id: str, expected_authority: Mapping[str, Any],
    expected_attempt_id: str, expected_object_keys: set[str],
    stage: str, error_code: str,
) -> None:
    """Persist a fail-closed stage failure without mutating prior evidence.

    DICOM/extraction and EchoPrime stage wrappers intentionally do not claim
    in-attempt retry support.  Their evidence paths and input ledgers are
    immutable, so every stage failure requires a new attempt with freshly
    bound authority.  Downloader retries remain governed separately by its
    per-object retry policy.
    """
    import lvef_c3_orchestration_core as core

    ledger = core.load_strict_json(input_ledger)
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=expected_attempt_id,
        expected_object_keys={batch_id: expected_object_keys},
    )
    batch = ledger["batches"][batch_id]
    predecessor = (
        batch["events"][-1]["receipt_sha256"]
        if batch["events"]
        else ledger["authority"]["batch_plan_sha256"]
    )
    receipt = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": expected_attempt_id,
        "batch_id": batch_id,
        "from_state": batch["state"],
        "to_state": "FAILED_NONRETRYABLE",
        "status": "PASS",
        "authority": ledger["authority"],
        "input_receipt_sha256": [predecessor],
        "output_manifest_sha256": hashlib.sha256(
            f"{stage}:{error_code}".encode("ascii", errors="replace")
        ).hexdigest(),
    }
    updated = core.apply_transition(ledger, receipt)
    if receipt_root.exists() or receipt_root.is_symlink():
        raise ProductionStageError("FAILURE_RECEIPT_ROOT_COLLISION")
    receipt_root.mkdir(mode=0o700)
    core.atomic_write_json_no_clobber(
        receipt_root / "stage_failure.restricted.json",
        receipt,
        attempt_id=expected_attempt_id,
    )
    core.atomic_write_json_no_clobber(
        output_ledger, updated, attempt_id=expected_attempt_id
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_authority_arguments(target: argparse.ArgumentParser, *, stage: str | None) -> None:
        if stage is None:
            target.add_argument("--stage", choices=sorted(WRAPPER_STAGES), required=True)
        else:
            target.set_defaults(stage=stage)
        target.add_argument("--batch-id", required=True)
        target.add_argument("--attempt-id", required=True)
        target.add_argument("--governing-commit", required=True)
        target.add_argument("--authority-worktree", type=Path, required=True)
        target.add_argument("--orchestration-contract", type=Path, required=True)
        target.add_argument("--batch-plan", type=Path, required=True)
        target.add_argument("--environment-receipt", type=Path, required=True)
        target.add_argument("--output-root", type=Path, required=True)

    validate = subparsers.add_parser("validate-wrapper")
    add_authority_arguments(validate, stage=None)
    validate_authorization = subparsers.add_parser("validate-stage-authorization")
    validate_authorization.add_argument(
        "--stage", choices=sorted(SCIENTIFIC_AUTHORIZATION_STAGES), required=True
    )
    validate_authorization.add_argument("--batch-id", required=True)
    validate_authorization.add_argument("--attempt-id", required=True)
    validate_authorization.add_argument("--governing-commit", required=True)
    validate_authorization.add_argument(
        "--orchestration-contract", type=Path, required=True
    )
    validate_authorization.add_argument("--batch-plan", type=Path, required=True)
    validate_authorization.add_argument(
        "--authorization-receipt", type=Path, required=True
    )
    validate_authorization.add_argument("--launch-authority-sha256", required=True)
    dicom = subparsers.add_parser("run-dicom-extraction")
    add_authority_arguments(dicom, stage="DICOM_EXTRACTION")
    dicom.add_argument("--authorization-receipt", type=Path, required=True)
    dicom.add_argument("--launch-authority-sha256", required=True)
    dicom.add_argument("--verified-download-manifest", type=Path, required=True)
    dicom.add_argument("--download-root", type=Path, required=True)
    dicom.add_argument("--workers", type=int, default=4)
    dicom.add_argument("--input-ledger", type=Path, required=True)
    dicom.add_argument("--output-ledger", type=Path, required=True)
    embed = subparsers.add_parser("run-echoprime")
    add_authority_arguments(embed, stage="ECHOPRIME_EMBEDDING")
    embed.add_argument("--authorization-receipt", type=Path, required=True)
    embed.add_argument("--launch-authority-sha256", required=True)
    embed.add_argument("--extraction-manifest", type=Path, required=True)
    embed.add_argument("--extraction-root", type=Path, required=True)
    embed.add_argument("--technical-disposition-manifest", type=Path)
    embed.add_argument("--dicom-audit", type=Path)
    embed.add_argument("--dicom-extraction-summary", type=Path)
    embed.add_argument("--verified-download-manifest", type=Path)
    embed.add_argument("--selected-batch-manifest", type=Path, required=True)
    embed.add_argument("--checkpoint", type=Path, required=True)
    embed.add_argument("--batch-size", type=int, default=8)
    embed.add_argument("--seed", type=int, default=20260803)
    embed.add_argument("--input-ledger", type=Path, required=True)
    embed.add_argument("--predecessor-transition-receipt", type=Path, required=True)
    embed.add_argument("--output-ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-stage-authorization":
        import lvef_c3_orchestration_core as core

        contract = core.load_orchestration_contract(args.orchestration_contract)
        plan = core.load_strict_json(args.batch_plan)
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=core.production_requirements(contract)
        )
        validate_stage_authorization(
            args.authorization_receipt,
            stage=args.stage,
            batch_id=args.batch_id,
            attempt_id=args.attempt_id,
            governing_commit=args.governing_commit,
            orchestration_contract_sha256=sha256_file(args.orchestration_contract),
            batch_plan_sha256=plan_sha,
            launch_authority_sha256=args.launch_authority_sha256,
        )
        print(
            json.dumps(
                {
                    "status": "PASS_STAGE_SCIENTIFIC_AUTHORIZATION",
                    "stage": args.stage,
                    "owner_authorized": True,
                },
                sort_keys=True,
            )
        )
        return 0
    value = validate_wrapper_authority(
        stage=args.stage,
        batch_id=args.batch_id,
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        authority_worktree=args.authority_worktree,
        orchestration_contract=args.orchestration_contract,
        batch_plan=args.batch_plan,
        environment_receipt=args.environment_receipt,
        output_root=args.output_root,
    )
    if args.command == "validate-wrapper":
        print(
            json.dumps(
                {
                    "status": "PASS_OFFLINE_WRAPPER_AUTHORITY",
                    "stage": value["stage"],
                    "real_execution_performed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    validate_stage_authorization(
        args.authorization_receipt,
        stage=args.stage,
        batch_id=args.batch_id,
        attempt_id=args.attempt_id,
        governing_commit=args.governing_commit,
        orchestration_contract_sha256=value["orchestration_contract_sha256"],
        batch_plan_sha256=value["batch_plan_sha256"],
        launch_authority_sha256=args.launch_authority_sha256,
    )
    if args.command == "run-dicom-extraction":
        validate_stage_predecessor(
            input_ledger=args.input_ledger,
            batch_id=args.batch_id,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
            bound_manifest=args.verified_download_manifest,
        )
        validate_download_manifest_plan_membership(
            args.verified_download_manifest, value["planned_batch"]
        )
        dicom_final = args.output_root / "dicom_extraction"
        if dicom_final.exists() or dicom_final.is_symlink():
            summary = validate_completed_stage_for_recovery(
                stage_directory=dicom_final,
                stage="DICOM_EXTRACTION",
                batch_id=args.batch_id,
                attempt_id=args.attempt_id,
                runtime_authority=value["runtime_authority"],
                input_manifest=args.verified_download_manifest,
                artifact_names=(
                    "dicom_audit.restricted.csv",
                    "extraction_manifest.restricted.csv",
                    "technical_disposition_manifest.restricted.csv",
                    "dicom_extraction.summary.json",
                ),
                summary_name="dicom_extraction.summary.json",
            )
        else:
            try:
                summary = run_production_dicom_extraction(
                    verified_download_manifest=args.verified_download_manifest,
                    download_root=args.download_root,
                    batch_output_root=args.output_root,
                    workers=args.workers,
                    batch_id=args.batch_id,
                    attempt_id=args.attempt_id,
                    runtime_authority=value["runtime_authority"],
                    planned_batch=value["planned_batch"],
                    embedding_output_root=(
                        canonical_embedding_root_for_extraction_batch(
                            args.output_root, batch_id=args.batch_id
                        )
                    ),
                )
            except ProductionStageError as exc:
                record_stage_failure(
                    input_ledger=args.input_ledger,
                    output_ledger=args.output_ledger,
                    receipt_root=args.output_root / "dicom_extraction_failure_receipt",
                    batch_id=args.batch_id,
                    expected_authority=value["runtime_authority"],
                    expected_attempt_id=args.attempt_id,
                    expected_object_keys=value["expected_object_keys"],
                    stage="DICOM_EXTRACTION",
                    error_code=exc.code,
                )
                raise
        advance_stage_ledger(
            input_ledger=args.input_ledger,
            output_ledger=args.output_ledger,
            receipt_root=args.output_root / "dicom_extraction" / "transition_receipts",
            batch_id=args.batch_id,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", sha256_file(args.output_root / "dicom_extraction" / "dicom_audit.restricted.csv")),
                ("EXTRACTION_COMPLETE", sha256_file(args.output_root / "dicom_extraction" / "extraction_manifest.restricted.csv")),
            ),
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
        )
    elif args.command == "run-echoprime":
        technical_disposition_manifest = (
            args.technical_disposition_manifest
            or args.extraction_manifest.parent
            / "technical_disposition_manifest.restricted.csv"
        )
        if sha256_file(args.checkpoint) != value["runtime_authority"]["checkpoint_sha256"]:
            raise ProductionStageError("RUNTIME_CHECKPOINT_AUTHORITY_MISMATCH")
        if sha256_file(args.environment_receipt) != value["runtime_authority"]["environment_receipt_sha256"]:
            raise ProductionStageError("RUNTIME_ENVIRONMENT_AUTHORITY_MISMATCH")
        validate_stage_predecessor(
            input_ledger=args.input_ledger,
            batch_id=args.batch_id,
            expected_state="EXTRACTION_COMPLETE",
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
            bound_manifest=args.extraction_manifest,
            predecessor_transition_receipt=args.predecessor_transition_receipt,
        )
        validate_extraction_manifest_plan_membership(
            args.extraction_manifest,
            value["planned_batch"],
            technical_disposition_manifest,
        )
        echoprime_final = args.output_root / "echoprime"
        if echoprime_final.exists() or echoprime_final.is_symlink():
            summary = validate_completed_stage_for_recovery(
                stage_directory=echoprime_final,
                stage="ECHOPRIME_EMBEDDING",
                batch_id=args.batch_id,
                attempt_id=args.attempt_id,
                runtime_authority=value["runtime_authority"],
                input_manifest=args.extraction_manifest,
                artifact_names=(
                    "clip_embeddings.restricted.npz",
                    "clip_manifest.restricted.csv",
                    "study_embeddings.restricted.npz",
                    "study_manifest.restricted.csv",
                    "study_disposition.restricted.csv",
                    "echoprime_pooling.summary.json",
                ),
                summary_name="echoprime_pooling.summary.json",
            )
        else:
            try:
                summary = run_production_echoprime(
                    extraction_manifest=args.extraction_manifest,
                    extraction_root=args.extraction_root,
                    technical_disposition_manifest=(
                        technical_disposition_manifest
                    ),
                    dicom_audit=args.dicom_audit,
                    dicom_extraction_summary=args.dicom_extraction_summary,
                    verified_download_manifest=args.verified_download_manifest,
                    selected_batch_manifest=args.selected_batch_manifest,
                    checkpoint=args.checkpoint,
                    environment_receipt=args.environment_receipt,
                    orchestration_contract=args.orchestration_contract,
                    batch_plan=args.batch_plan,
                    batch_id=args.batch_id,
                    batch_output_root=args.output_root,
                    batch_size=args.batch_size,
                    seed=args.seed,
                    attempt_id=args.attempt_id,
                    runtime_authority=value["runtime_authority"],
                )
            except ProductionStageError as exc:
                record_stage_failure(
                    input_ledger=args.input_ledger,
                    output_ledger=args.output_ledger,
                    receipt_root=args.output_root / "echoprime_failure_receipt",
                    batch_id=args.batch_id,
                    expected_authority=value["runtime_authority"],
                    expected_attempt_id=args.attempt_id,
                    expected_object_keys=value["expected_object_keys"],
                    stage="ECHOPRIME_EMBEDDING",
                    error_code=exc.code,
                )
                raise
        advance_stage_ledger(
            input_ledger=args.input_ledger,
            output_ledger=args.output_ledger,
            receipt_root=args.output_root / "echoprime" / "transition_receipts",
            batch_id=args.batch_id,
            transitions=(
                ("EMBEDDING_COMPLETE", sha256_file(args.output_root / "echoprime" / "clip_manifest.restricted.csv")),
                ("STUDY_POOLING_COMPLETE", sha256_file(args.output_root / "echoprime" / "study_manifest.restricted.csv")),
            ),
            expected_authority=value["runtime_authority"],
            expected_attempt_id=args.attempt_id,
            expected_object_keys=value["expected_object_keys"],
        )
    else:  # pragma: no cover
        raise ProductionStageError("UNKNOWN_COMMAND")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "stage": args.stage,
                "identifiers_emitted": False,
                "paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except SystemExit:
        raise
    except ProductionStageError as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": exc.code}, sort_keys=True))
        return 78
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_code": "UNEXPECTED_STAGE_EXCEPTION",
                    "error_type": type(exc).__name__,
                    "exception_message_emitted": False,
                    "identifiers_emitted": False,
                    "paths_emitted": False,
                },
                sort_keys=True,
            )
        )
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
