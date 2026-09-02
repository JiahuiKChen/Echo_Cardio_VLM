#!/usr/bin/env python3
"""Create and second-pass verify one restricted C3 batch preservation receipt."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, MutableMapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as production_stages


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
BATCH_RE = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
MANIFEST_HEADER = ["relative_path", "size_bytes", "sha256", "role"]
DICOM_AUDIT_HEADER = (
    "subject_id",
    "study_id",
    "smoke_role",
    "source_relative_path",
    "download_sha256",
    "read_ok",
    "is_multiframe",
    "number_of_frames",
    "rows",
    "columns",
    "samples_per_pixel",
    "bits_allocated",
    "bits_stored",
    "photometric_interpretation",
    "transfer_syntax_uid",
    "error_code",
    "pixel_decode_ok",
)
EXTRACTION_MANIFEST_HEADER = (
    "subject_id",
    "study_id",
    "smoke_role",
    "source_relative_path",
    "source_sha256",
    "clip_key",
    "output_relative_path",
    "write_ok",
    "mask_status",
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
    "decode_color_status",
    "temporal_sampling_policy",
    "frames_shape",
    "frames_dtype",
    "frames_sha256",
    "sampled_indices_sha256",
    "source_num_frames_sha256",
    "npz_sha256",
    "source_num_frames",
    "error_code",
    "physical_source_key",
    "pixel_decode_ok",
)
CLIP_MANIFEST_HEADER = (
    "embedding_idx",
    "subject_id",
    "study_id",
    "clip_key",
    "physical_source_key",
    "embedding_l2_norm",
    "embedding_sha256",
    "write_ok",
)
STUDY_MANIFEST_HEADER = (
    "study_idx",
    "subject_id",
    "study_id",
    "n_clips",
    "embedding_sha256",
)
DISPOSITION_HEADER = ("subject_id", "study_id", "disposition")
TECHNICAL_DISPOSITION_HEADER = (
    production_stages.TECHNICAL_DISPOSITION_MANIFEST_HEADER
)
DICOM_RECOMPUTED_SUMMARY_KEYS = (
    "n_objects",
    "n_studies",
    "n_readable",
    "n_unreadable",
    "n_multiframe_candidates",
    "n_single_frame",
    "n_pixel_decode_failures",
    "physical_source_keys_unique",
)
EXTRACTION_RECOMPUTED_SUMMARY_KEYS = (
    "status",
    "n_requested_cines",
    "n_extracted_clips",
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
    "n_studies_with_extracted_clips",
    "clip_keys_unique",
    "physical_source_keys_unique",
    "all_shapes_and_dtypes_valid",
    "all_pixel_decodes_passed",
    "n_ordinary_preprocessing_path",
    "n_spatial_fallback_preprocessing_path",
    "n_temporal_fallback_preprocessing_path",
    "n_spatial_temporal_fallback_preprocessing_path",
    "n_fallback_path_pass",
    "n_fallback_path_failed",
    "all_source_signal_gates_passed",
    "all_post_crop_signal_gates_passed",
    "all_sampled_signal_gates_passed",
    "all_fallback_encoder_visible_signal_gates_passed",
    "all_failure_substages_none",
)
CLIP_L2_NORM_REL_TOL = 1e-12
CLIP_L2_NORM_ABS_TOL = 1e-12
TIMESTAMP_RE = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|\+00:00)$"
)
VERIFIED_DOWNLOAD_MANIFEST_HEADER = (
    "subject_id",
    "study_id",
    "source_relative_path",
    "download_ok",
    "observed_sha256",
    "physical_source_key",
)
RAW_DICOM_BASENAME_RE = re.compile(r"^([0-9a-f]{64})[.]dcm$")

R8R_FIXED_ATTEMPT_ID = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
R8R_FIXED_BATCH_ID = "c3_batch_002"
R8R_FIXED_PLAN_SHA256 = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
R8R_FIXED_SCIENTIFIC_COMMIT = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
R8R_FIXED_PRODUCTION_ROOT = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
R8R_FIXED_SCHEDULER_RUNNER_PATH = (
    Path(__file__).resolve().parent
    / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
)
R8U_R3_FIXED_BATCH_ID = "c3_batch_015"
R8U_R3_FIXED_EXTRACTION_NPZ_FILES = 10_187
NPZ_STABLE_METADATA_FIELDS = (
    "device", "inode", "mode_including_type", "uid", "gid", "nlink",
    "size", "mtime_ns", "ctime_ns",
)
NPZ_METADATA_DIAGNOSTIC_KEYS = frozenset(
    {
        "files_evaluated", "files_passing", "first_failed_predicate",
        "atime_only_differences", "stable_metadata_differences",
        "missing_paths", "additional_paths",
    }
)
R8U_NPZ_ATIME_ONLY_DIFFERENCE = "R8U_NPZ_ATIME_ONLY_DIFFERENCE"

SAFE_NESTED_VALIDATION_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
CSV_NONNEGATIVE_INTEGER_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.0)?$")


class PreservationValidationSubstage(str, Enum):
    CONTEXT_RECONSTRUCTION = "CONTEXT_RECONSTRUCTION"
    EXTRACTION_ROW_VALIDATION = "EXTRACTION_ROW_VALIDATION"
    TECHNICAL_DISPOSITION_VALIDATION = "TECHNICAL_DISPOSITION_VALIDATION"
    SUMMARY_RECONCILIATION = "SUMMARY_RECONCILIATION"
    ARRAY_EMBEDDING_VALIDATION = "ARRAY_EMBEDDING_VALIDATION"


class ArtifactValidationContext(Enum):
    """Closed content-authority modes; never construct from CLI text."""

    STRICT_CONTENT_HASH = "STRICT_CONTENT_HASH"
    R8R_FIXED_BATCH3_NO_DICOM_BODY = "R8R_FIXED_BATCH3_NO_DICOM_BODY"
    R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY = (
        "R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY"
    )


STRICT_CONTENT_HASH = ArtifactValidationContext.STRICT_CONTENT_HASH
R8R_FIXED_BATCH3_NO_DICOM_BODY = (
    ArtifactValidationContext.R8R_FIXED_BATCH3_NO_DICOM_BODY
)
R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY = (
    ArtifactValidationContext.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
)
BODY_FREE_RAW_CONTEXTS = frozenset(
    {
        R8R_FIXED_BATCH3_NO_DICOM_BODY,
        R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY,
    }
)


@dataclass(frozen=True)
class SealedRawDicomAuthority:
    path: Path
    source_object_key: str
    subject_id: str
    study_id: str
    source_relative_path: str
    size_bytes: int
    observed_sha256: str
    metadata_projection: tuple[int, ...]


@dataclass(frozen=True)
class SealedExtractedNpzAuthority:
    path: Path
    output_relative_path: str
    size_bytes: int
    observed_sha256: str
    metadata_projection: tuple[int, ...]
    diagnostic_atime_ns: int


@dataclass(frozen=True)
class ExtractedNpzMetadataObservation:
    """One body-free NPZ observation with access time kept diagnostic-only."""

    stable_projection: tuple[int, ...]
    diagnostic_atime_ns: int
    atime_only_difference: bool


class BatchPreservationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        validation_substage: PreservationValidationSubstage | None = None,
    ):
        if validation_substage is not None and not isinstance(
            validation_substage, PreservationValidationSubstage
        ):
            raise TypeError("validation_substage must be a closed enum value")
        super().__init__(code)
        self.code = code
        self.validation_substage = validation_substage


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchPreservationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def load_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BatchPreservationError(f"{code}_NOT_REGULAR")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except BatchPreservationError:
        raise
    except Exception as exc:
        raise BatchPreservationError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise BatchPreservationError(f"{code}_NOT_OBJECT")
    return value


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise BatchPreservationError("ARTIFACT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_manifest_artifact_authority(path: Path) -> tuple[int, str]:
    """Hash one manifest member through bound, no-follow parent/file fds."""

    absolute = Path(os.path.abspath(path))
    if not absolute.name:
        raise BatchPreservationError(
            "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
        )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    def directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def open_parent() -> tuple[int, tuple[int, ...]]:
        try:
            descriptor = os.open(absolute.anchor, directory_flags)
            for component in absolute.parts[1:-1]:
                child = os.open(
                    component, directory_flags, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            metadata = os.fstat(descriptor)
        except OSError as exc:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise BatchPreservationError(
                "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
            ) from exc
        return descriptor, directory_identity(metadata)

    def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    parent_descriptor, parent_before = open_parent()
    file_descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_descriptor = os.open(
            absolute.name, flags, dir_fd=parent_descriptor
        )
        before = os.fstat(file_descriptor)
        visible_before = os.stat(
            absolute.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or file_identity(before) != file_identity(visible_before)
        ):
            raise BatchPreservationError(
                "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
            )
        digest = hashlib.sha256()
        while True:
            block = os.read(file_descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(file_descriptor)
        visible_after = os.stat(
            absolute.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            file_identity(before) != file_identity(after)
            or file_identity(before) != file_identity(visible_after)
            or parent_before
            != directory_identity(os.fstat(parent_descriptor))
        ):
            raise BatchPreservationError(
                "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
            )
        # The bound parent must still be the parent reachable through the
        # original no-follow component walk after the read.
        rebound_descriptor, rebound_parent = open_parent()
        os.close(rebound_descriptor)
        if rebound_parent != parent_before:
            raise BatchPreservationError(
                "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
            )
        return before.st_size, digest.hexdigest()
    except BatchPreservationError:
        raise
    except OSError as exc:
        raise BatchPreservationError(
            "PRESERVATION_ARTIFACT_AUTHORITY_INVALID"
        ) from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        os.close(parent_descriptor)


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise BatchPreservationError("ARTIFACT_OUTSIDE_PRODUCTION_ROOT") from exc
    value = PurePosixPath(relative)
    if not relative or any(part in {"", ".", ".."} for part in value.parts):
        raise BatchPreservationError("ARTIFACT_RELATIVE_PATH_INVALID")
    return relative


def read_csv_exact(
    path: Path, header: Sequence[str], *, delimiter: str = ","
) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise BatchPreservationError("CSV_NOT_REGULAR")
    if delimiter not in {",", "\t"}:
        raise BatchPreservationError("CSV_DELIMITER_INVALID")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            observed = next(reader)
        except StopIteration:
            raise BatchPreservationError("CSV_EMPTY") from None
        if observed != list(header) or len(observed) != len(set(observed)):
            raise BatchPreservationError("CSV_SCHEMA_MISMATCH")
        rows = []
        for values in reader:
            if len(values) != len(observed):
                raise BatchPreservationError("CSV_ROW_WIDTH_MISMATCH")
            rows.append(dict(zip(observed, values)))
    return rows


def require_artifact_validation_context(
    value: ArtifactValidationContext,
) -> ArtifactValidationContext:
    if type(value) is not ArtifactValidationContext:
        raise BatchPreservationError("ARTIFACT_VALIDATION_CONTEXT_INVALID")
    return value


def validate_artifact_validation_scope(
    *,
    artifact_validation_context: ArtifactValidationContext,
    production_root: Path,
    attempt_id: str,
    batch_id: str,
    plan_sha256: str,
    governing_commit: str,
    scheduler_runner_path: Path | None,
) -> None:
    """Close the body-free capability to the one fixed R8R continuation."""

    context = require_artifact_validation_context(
        artifact_validation_context
    )
    if context is STRICT_CONTENT_HASH:
        return
    fixed_batch_id = (
        R8R_FIXED_BATCH_ID
        if context is R8R_FIXED_BATCH3_NO_DICOM_BODY
        else R8U_R3_FIXED_BATCH_ID
    )
    scope_code = (
        "R8R_RECOVERY_SCOPE_INVALID"
        if context is R8R_FIXED_BATCH3_NO_DICOM_BODY
        else "R8U_R3_RECOVERY_SCOPE_INVALID"
    )
    runner_code = (
        "R8R_RECOVERY_SCHEDULER_RUNNER_INVALID"
        if context is R8R_FIXED_BATCH3_NO_DICOM_BODY
        else "R8U_R3_RECOVERY_SCHEDULER_RUNNER_INVALID"
    )
    if (
        context not in BODY_FREE_RAW_CONTEXTS
        or production_root != R8R_FIXED_PRODUCTION_ROOT
        or attempt_id != R8R_FIXED_ATTEMPT_ID
        or batch_id != fixed_batch_id
        or plan_sha256 != R8R_FIXED_PLAN_SHA256
        or governing_commit != R8R_FIXED_SCIENTIFIC_COMMIT
    ):
        raise BatchPreservationError(scope_code)
    if scheduler_runner_path != R8R_FIXED_SCHEDULER_RUNNER_PATH:
        raise BatchPreservationError(runner_code)


def _raw_dicom_metadata_projection(
    path: Path, *, expected_size_bytes: int
) -> tuple[int, ...]:
    """Project stable no-follow metadata without opening the DICOM leaf."""

    absolute = Path(os.path.abspath(path))
    if RAW_DICOM_BASENAME_RE.fullmatch(absolute.name) is None:
        raise BatchPreservationError("RAW_DICOM_PATH_INVALID")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    def directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def open_parent() -> tuple[int, tuple[int, ...]]:
        descriptor = -1
        try:
            descriptor = os.open(absolute.anchor, directory_flags)
            for component in absolute.parts[1:-1]:
                child = os.open(
                    component, directory_flags, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise OSError("parent is not a directory")
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise BatchPreservationError(
                "RAW_DICOM_METADATA_AUTHORITY_INVALID"
            ) from exc
        return descriptor, directory_identity(metadata)

    parent_descriptor, parent_before = open_parent()
    try:
        before = os.stat(
            absolute.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        after = os.stat(
            absolute.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        file_projection = file_identity(before)
        if (
            file_projection != file_identity(after)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or type(expected_size_bytes) is not int
            or expected_size_bytes <= 0
            or before.st_size != expected_size_bytes
            or parent_before
            != directory_identity(os.fstat(parent_descriptor))
        ):
            raise BatchPreservationError(
                "RAW_DICOM_METADATA_AUTHORITY_INVALID"
            )
    except BatchPreservationError:
        raise
    except OSError as exc:
        raise BatchPreservationError(
            "RAW_DICOM_METADATA_AUTHORITY_INVALID"
        ) from exc
    finally:
        os.close(parent_descriptor)
    rebound_descriptor, rebound_parent = open_parent()
    os.close(rebound_descriptor)
    if rebound_parent != parent_before:
        raise BatchPreservationError(
            "RAW_DICOM_METADATA_AUTHORITY_INVALID"
        )
    return (*parent_before, *file_projection)


def validate_verified_raw_dicom_authority(
    *,
    raw_objects_root: Path,
    verified_download_manifest: Path,
    planned_batch: Mapping[str, Any],
    artifact_validation_context: ArtifactValidationContext,
) -> tuple[list[dict[str, str]], dict[Path, SealedRawDicomAuthority]]:
    """Bind raw leaves to the plan and sealed download-manifest authority."""

    context = require_artifact_validation_context(
        artifact_validation_context
    )
    rows = read_csv_exact(
        verified_download_manifest, VERIFIED_DOWNLOAD_MANIFEST_HEADER
    )
    try:
        planned_objects = planned_batch["objects"]
        planned_n_objects = planned_batch["n_objects"]
        planned_source_bytes = planned_batch["source_bytes"]
    except (KeyError, TypeError) as exc:
        raise BatchPreservationError("RAW_DICOM_PLAN_INVALID") from exc
    if (
        not isinstance(planned_objects, list)
        or type(planned_n_objects) is not int
        or type(planned_source_bytes) is not int
        or planned_n_objects <= 0
        or len(planned_objects) != planned_n_objects
    ):
        raise BatchPreservationError("RAW_DICOM_PLAN_INVALID")
    expected: dict[str, Mapping[str, Any]] = {}
    expected_source_bytes = 0
    for item in planned_objects:
        if not isinstance(item, Mapping):
            raise BatchPreservationError("RAW_DICOM_PLAN_INVALID")
        key = item.get("source_object_key")
        size_bytes = item.get("size_bytes")
        source_relative_path = item.get("source_relative_path")
        if (
            not isinstance(key, str)
            or SHA_RE.fullmatch(key) is None
            or key in expected
            or type(size_bytes) is not int
            or size_bytes <= 0
            or not isinstance(source_relative_path, str)
            or not source_relative_path
        ):
            raise BatchPreservationError("RAW_DICOM_PLAN_INVALID")
        expected[key] = item
        expected_source_bytes += size_bytes
    if expected_source_bytes != planned_source_bytes:
        raise BatchPreservationError("RAW_DICOM_PLAN_INVALID")
    if len(rows) != planned_n_objects:
        raise BatchPreservationError(
            "DOWNLOAD_MANIFEST_COUNT_OR_STATUS_MISMATCH"
        )
    manifest_by_key: dict[str, Mapping[str, str]] = {}
    for row in rows:
        key = row["physical_source_key"]
        if key in manifest_by_key:
            raise BatchPreservationError(
                "DOWNLOAD_MANIFEST_MEMBERSHIP_MISMATCH"
            )
        expected_row = expected.get(key)
        if expected_row is None:
            raise BatchPreservationError(
                "DOWNLOAD_MANIFEST_MEMBERSHIP_MISMATCH"
            )
        if row["download_ok"] != "true":
            raise BatchPreservationError(
                "DOWNLOAD_MANIFEST_COUNT_OR_STATUS_MISMATCH"
            )
        if (
            row["subject_id"] != str(expected_row.get("subject_id"))
            or row["study_id"] != str(expected_row.get("study_id"))
        ):
            raise BatchPreservationError(
                "DOWNLOAD_MANIFEST_OWNERSHIP_MISMATCH"
            )
        if row["source_relative_path"] != expected_row["source_relative_path"]:
            raise BatchPreservationError(
                "DOWNLOAD_MANIFEST_SOURCE_PATH_MISMATCH"
            )
        if SHA_RE.fullmatch(row["observed_sha256"]) is None:
            raise BatchPreservationError("DOWNLOAD_MANIFEST_SHA256_INVALID")
        manifest_by_key[key] = row
    if set(manifest_by_key) != set(expected):
        raise BatchPreservationError(
            "DOWNLOAD_MANIFEST_MEMBERSHIP_MISMATCH"
        )
    if raw_objects_root.is_symlink() or not raw_objects_root.is_dir():
        raise BatchPreservationError("RAW_DICOM_RETENTION_GATE_FAILED")
    observed_names: set[str] = set()
    for entry in os.scandir(raw_objects_root):
        match = RAW_DICOM_BASENAME_RE.fullmatch(entry.name)
        if (
            match is None
            or entry.is_symlink()
            or not entry.is_file(follow_symlinks=False)
            or entry.name in observed_names
        ):
            raise BatchPreservationError("RAW_DICOM_RETENTION_GATE_FAILED")
        observed_names.add(entry.name)
    if observed_names != {f"{key}.dcm" for key in expected}:
        raise BatchPreservationError("RAW_DICOM_RETENTION_GATE_FAILED")

    authority: dict[Path, SealedRawDicomAuthority] = {}
    for key in sorted(expected):
        expected_row = expected[key]
        manifest_row = manifest_by_key[key]
        path = raw_objects_root / f"{key}.dcm"
        size_bytes = expected_row["size_bytes"]
        metadata_projection = _raw_dicom_metadata_projection(
            path, expected_size_bytes=size_bytes
        )
        observed_sha256 = manifest_row["observed_sha256"]
        if (
            context is STRICT_CONTENT_HASH
            and sha256_file(path) != observed_sha256
        ):
            raise BatchPreservationError("RAW_DICOM_HASH_MISMATCH")
        authority[path] = SealedRawDicomAuthority(
            path=path,
            source_object_key=key,
            subject_id=str(expected_row.get("subject_id")),
            study_id=str(expected_row.get("study_id")),
            source_relative_path=str(expected_row["source_relative_path"]),
            size_bytes=size_bytes,
            observed_sha256=observed_sha256,
            metadata_projection=metadata_projection,
        )
    return rows, authority


def validate_sealed_raw_dicom_metadata(
    authority: SealedRawDicomAuthority,
) -> None:
    if type(authority) is not SealedRawDicomAuthority:
        raise BatchPreservationError("RAW_DICOM_SEALED_AUTHORITY_INVALID")
    observed = _raw_dicom_metadata_projection(
        authority.path, expected_size_bytes=authority.size_bytes
    )
    if observed != authority.metadata_projection:
        raise BatchPreservationError("RAW_DICOM_METADATA_CHANGED")


def _sealed_raw_dicom_artifact_record(
    authority: SealedRawDicomAuthority, root: Path, role: str
) -> dict[str, Any]:
    validate_sealed_raw_dicom_metadata(authority)
    return {
        "relative_path": safe_relative(authority.path, root),
        "size_bytes": authority.size_bytes,
        "sha256": authority.observed_sha256,
        "role": role,
    }


def new_npz_metadata_diagnostics() -> dict[str, int | str]:
    """Return the closed aggregate-safe diagnostic for one NPZ seal."""

    return {
        "files_evaluated": 0,
        "files_passing": 0,
        "first_failed_predicate": "NONE",
        "atime_only_differences": 0,
        "stable_metadata_differences": 0,
        "missing_paths": 0,
        "additional_paths": 0,
    }


def _require_npz_metadata_diagnostics(
    value: MutableMapping[str, int | str] | None,
) -> MutableMapping[str, int | str] | None:
    if value is None:
        return None
    if not value:
        value.update(new_npz_metadata_diagnostics())
    if (
        set(value) != NPZ_METADATA_DIAGNOSTIC_KEYS
        or value.get("first_failed_predicate") == ""
        or any(
            isinstance(value.get(field), bool)
            or not isinstance(value.get(field), int)
            or int(value.get(field, -1)) < 0
            for field in NPZ_METADATA_DIAGNOSTIC_KEYS
            if field != "first_failed_predicate"
        )
    ):
        raise BatchPreservationError("R8U_NPZ_STABLE_METADATA_CHANGED")
    return value


def _record_npz_metadata_failure(
    diagnostics: MutableMapping[str, int | str] | None,
    code: str,
    *, stable_difference: bool = False,
) -> None:
    observed = _require_npz_metadata_diagnostics(diagnostics)
    if observed is None:
        return
    if observed["first_failed_predicate"] == "NONE":
        observed["first_failed_predicate"] = code
    if stable_difference:
        observed["stable_metadata_differences"] = (
            int(observed["stable_metadata_differences"]) + 1
        )


def _raise_npz_metadata_error(
    code: str,
    diagnostics: MutableMapping[str, int | str] | None,
    *, stable_difference: bool = False,
    cause: BaseException | None = None,
) -> None:
    _record_npz_metadata_failure(
        diagnostics, code, stable_difference=stable_difference
    )
    error = BatchPreservationError(code)
    if cause is None:
        raise error
    raise error from cause


def _npz_stable_metadata_projection(
    value: os.stat_result,
) -> tuple[int, ...]:
    """Project exactly the NPZ identity fields sealed across body-free reads."""

    return (
        int(value.st_dev), int(value.st_ino), int(value.st_mode),
        int(value.st_uid), int(value.st_gid), int(value.st_nlink),
        int(value.st_size), int(value.st_mtime_ns), int(value.st_ctime_ns),
    )


def _extracted_npz_metadata_observation(
    path: Path,
    *,
    approved_device: int | None = None,
    diagnostics: MutableMapping[str, int | str] | None = None,
    count_evaluation: bool = False,
) -> ExtractedNpzMetadataObservation:
    """Bind one NPZ leaf without opening its scientific payload."""

    observed = _require_npz_metadata_diagnostics(diagnostics)
    if observed is not None and count_evaluation:
        observed["files_evaluated"] = int(observed["files_evaluated"]) + 1
    absolute = Path(os.path.abspath(path))
    try:
        before = os.lstat(absolute)
        after = os.lstat(absolute)
    except OSError as exc:
        _raise_npz_metadata_error(
            "R8U_NPZ_LSTAT_FAILED", observed, cause=exc
        )
    if stat.S_ISLNK(before.st_mode):
        _raise_npz_metadata_error("R8U_NPZ_SYMLINK_INVALID", observed)
    if not stat.S_ISREG(before.st_mode):
        _raise_npz_metadata_error("R8U_NPZ_NOT_REGULAR", observed)
    if before.st_uid != os.geteuid():
        _raise_npz_metadata_error("R8U_NPZ_OWNER_MISMATCH", observed)
    if stat.S_IMODE(before.st_mode) != 0o600:
        _raise_npz_metadata_error("R8U_NPZ_MODE_INVALID", observed)
    if before.st_nlink != 1:
        _raise_npz_metadata_error(
            "R8U_NPZ_LINK_COUNT_INVALID", observed
        )
    if before.st_size <= 0:
        _raise_npz_metadata_error("R8U_NPZ_SIZE_INVALID", observed)
    if absolute.suffix.lower() != ".npz":
        _raise_npz_metadata_error("R8U_NPZ_SUFFIX_INVALID", observed)
    if approved_device is not None and int(before.st_dev) != approved_device:
        _raise_npz_metadata_error(
            "R8U_NPZ_DEVICE_TOPOLOGY_INVALID", observed
        )
    before_projection = _npz_stable_metadata_projection(before)
    after_projection = _npz_stable_metadata_projection(after)
    if before_projection != after_projection:
        _raise_npz_metadata_error(
            "R8U_NPZ_STABLE_METADATA_CHANGED",
            observed,
            stable_difference=True,
        )
    # Access time is deliberately diagnostic-only.  Naming the classification
    # here keeps it auditable without adding a path-bearing or per-file field
    # to the closed aggregate diagnostic.
    atime_only_difference = int(before.st_atime_ns) != int(after.st_atime_ns)
    atime_classification = (
        R8U_NPZ_ATIME_ONLY_DIFFERENCE if atime_only_difference else "NONE"
    )
    if observed is not None:
        if atime_classification == R8U_NPZ_ATIME_ONLY_DIFFERENCE:
            observed["atime_only_differences"] = (
                int(observed["atime_only_differences"]) + 1
            )
        if count_evaluation:
            observed["files_passing"] = int(observed["files_passing"]) + 1
    return ExtractedNpzMetadataObservation(
        stable_projection=before_projection,
        diagnostic_atime_ns=int(after.st_atime_ns),
        atime_only_difference=atime_only_difference,
    )


def _extracted_npz_metadata_projection(
    path: Path,
    *,
    approved_device: int | None = None,
    diagnostics: MutableMapping[str, int | str] | None = None,
    count_evaluation: bool = False,
) -> tuple[int, ...]:
    """Compatibility wrapper returning only the explicit stable projection."""

    return _extracted_npz_metadata_observation(
        path,
        approved_device=approved_device,
        diagnostics=diagnostics,
        count_evaluation=count_evaluation,
    ).stable_projection


def _r8u_r3_private_npz_directory(
    info: os.stat_result, *, approved_device: int
) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    return bool(
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.geteuid()
        and mode & 0o700 == 0o700
        and not mode & 0o077
        and mode & 0o7000 in {0, stat.S_ISGID}
        and int(info.st_dev) == approved_device
    )


def seal_r8u_r3_extracted_npz_authority(
    *,
    extraction_manifest: Path,
    extraction_root: Path,
    diagnostics: MutableMapping[str, int | str] | None = None,
) -> dict[Path, SealedExtractedNpzAuthority]:
    """Reproject the fixed Batch-16 cache from its manifest, metadata only."""

    observed_diagnostics = _require_npz_metadata_diagnostics(diagnostics)
    rows = read_csv_exact(extraction_manifest, EXTRACTION_MANIFEST_HEADER)
    if (
        len(rows) != R8U_R3_FIXED_EXTRACTION_NPZ_FILES
        or any(row.get("write_ok") != "True" for row in rows)
    ):
        raise BatchPreservationError(
            "R8U_R3_EXTRACTION_NPZ_MANIFEST_INVALID"
        )
    clips_root = extraction_root / "clips"
    try:
        clips_info = os.lstat(clips_root)
    except OSError as exc:
        raise BatchPreservationError(
            "R8U_R3_EXTRACTION_NPZ_TOPOLOGY_INVALID"
        ) from exc
    approved_device = int(clips_info.st_dev)
    if not _r8u_r3_private_npz_directory(
        clips_info, approved_device=approved_device
    ):
        raise BatchPreservationError(
            "R8U_R3_EXTRACTION_NPZ_TOPOLOGY_INVALID"
        )
    manifest_authority: dict[Path, tuple[Path, str]] = {}
    for row in rows:
        clip_key = str(row.get("clip_key", ""))
        relative_text = str(row.get("output_relative_path", ""))
        digest = str(row.get("npz_sha256", ""))
        expected_relative = f"clips/{clip_key[:2]}/{clip_key}.npz"
        if SHA_RE.fullmatch(clip_key) is None or SHA_RE.fullmatch(digest) is None:
            raise BatchPreservationError(
                "R8U_R3_EXTRACTION_NPZ_MANIFEST_INVALID"
            )
        if relative_text != expected_relative:
            _raise_npz_metadata_error(
                "R8U_NPZ_PATH_SET_MISMATCH", observed_diagnostics
            )
        relative = production_stages.safe_relative_path(relative_text)
        # The extraction producer receives <stage>/clips as its output root,
        # while the manifest locator itself begins with clips/.  Mirror the
        # same authority used by stage and EchoPrime validation: the physical
        # stage-relative locator is therefore clips/<manifest locator>.
        path = clips_root / relative
        if path in manifest_authority:
            raise BatchPreservationError(
                "R8U_R3_EXTRACTION_NPZ_MANIFEST_INVALID"
            )
        manifest_authority[path] = (relative, digest)
    observed: set[Path] = set()
    for directory, names, filenames in os.walk(clips_root, followlinks=False):
        current = Path(directory)
        info = os.lstat(current)
        if not _r8u_r3_private_npz_directory(
            info, approved_device=approved_device
        ):
            raise BatchPreservationError(
                "R8U_R3_EXTRACTION_NPZ_TOPOLOGY_INVALID"
            )
        for name in names:
            child = current / name
            child_info = os.lstat(child)
            if not _r8u_r3_private_npz_directory(
                child_info, approved_device=approved_device
            ):
                raise BatchPreservationError(
                    "R8U_R3_EXTRACTION_NPZ_TOPOLOGY_INVALID"
                )
        for name in filenames:
            observed.add(current / name)
    missing = set(manifest_authority) - observed
    additional = observed - set(manifest_authority)
    if observed_diagnostics is not None:
        observed_diagnostics["missing_paths"] = len(missing)
        observed_diagnostics["additional_paths"] = len(additional)
    if missing or additional:
        _raise_npz_metadata_error(
            "R8U_NPZ_PATH_SET_MISMATCH", observed_diagnostics
        )
    expected: dict[Path, SealedExtractedNpzAuthority] = {}
    for path, (relative, digest) in manifest_authority.items():
        observation = _extracted_npz_metadata_observation(
            path,
            approved_device=approved_device,
            diagnostics=observed_diagnostics,
            count_evaluation=True,
        )
        expected[path] = SealedExtractedNpzAuthority(
            path=path,
            output_relative_path=relative,
            size_bytes=observation.stable_projection[6],
            observed_sha256=digest,
            metadata_projection=observation.stable_projection,
            diagnostic_atime_ns=observation.diagnostic_atime_ns,
        )
    return expected


def validate_sealed_extracted_npz_metadata(
    authority: SealedExtractedNpzAuthority,
    *,
    diagnostics: MutableMapping[str, int | str] | None = None,
) -> None:
    observed_diagnostics = _require_npz_metadata_diagnostics(diagnostics)
    if type(authority) is not SealedExtractedNpzAuthority:
        _raise_npz_metadata_error(
            "R8U_NPZ_SEALED_METADATA_CHANGED",
            observed_diagnostics,
            stable_difference=True,
        )
    observation = _extracted_npz_metadata_observation(
        authority.path,
        approved_device=int(authority.metadata_projection[0]),
        diagnostics=observed_diagnostics,
    )
    if observation.stable_projection != authority.metadata_projection:
        _raise_npz_metadata_error(
            "R8U_NPZ_SEALED_METADATA_CHANGED",
            observed_diagnostics,
            stable_difference=True,
        )
    if (
        observed_diagnostics is not None
        and observation.diagnostic_atime_ns != authority.diagnostic_atime_ns
        and not observation.atime_only_difference
    ):
        observed_diagnostics["atime_only_differences"] = (
            int(observed_diagnostics["atime_only_differences"]) + 1
        )


def _sealed_extracted_npz_artifact_record(
    authority: SealedExtractedNpzAuthority, root: Path, role: str
) -> dict[str, Any]:
    return {
        "relative_path": safe_relative(authority.path, root),
        "size_bytes": authority.size_bytes,
        "sha256": authority.observed_sha256,
        "role": role,
    }


def _strict_csv_bool(value: str, *, code: str) -> bool:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    raise BatchPreservationError(code)


def _strict_csv_nonnegative_integer_or_empty(
    value: str, *, code: str
) -> int | str:
    """Restore producer integers after a lossless pandas CSV round trip."""

    if value == "":
        return ""
    match = CSV_NONNEGATIVE_INTEGER_RE.fullmatch(value)
    if match is None:
        raise BatchPreservationError(code)
    return int(match.group(1))


def _nested_validation_error(
    error: production_stages.ProductionStageError,
    *,
    substage: PreservationValidationSubstage,
) -> BatchPreservationError:
    code = error.code
    if (
        not isinstance(code, str)
        or SAFE_NESTED_VALIDATION_CODE_RE.fullmatch(code) is None
    ):
        code = "PRESERVATION_NESTED_VALIDATION_CODE_INVALID"
    return BatchPreservationError(code, validation_substage=substage)


def _preservation_substage_error(
    error: Exception,
    *,
    substage: PreservationValidationSubstage,
) -> BatchPreservationError:
    code = getattr(error, "code", None)
    if (
        not isinstance(code, str)
        or SAFE_NESTED_VALIDATION_CODE_RE.fullmatch(code) is None
    ):
        code = "PRESERVATION_NESTED_VALIDATION_CODE_INVALID"
    return BatchPreservationError(code, validation_substage=substage)


def validate_stage_csv_authority(
    *,
    dicom_audit_path: Path,
    extraction_manifest_path: Path,
    clip_manifest_path: Path,
    study_manifest_path: Path,
    disposition_path: Path,
    technical_disposition_path: Path,
    clips_root: Path,
    expected_objects: int,
    expected_studies: int,
    validate_npz_bodies: bool = True,
) -> dict[str, Any]:
    """Raw-parse exact schemas and independently recompute stage semantics."""
    dicom_rows = read_csv_exact(dicom_audit_path, DICOM_AUDIT_HEADER)
    extraction_rows = read_csv_exact(
        extraction_manifest_path, EXTRACTION_MANIFEST_HEADER
    )
    clip_rows = read_csv_exact(clip_manifest_path, CLIP_MANIFEST_HEADER)
    study_rows = read_csv_exact(study_manifest_path, STUDY_MANIFEST_HEADER)
    disposition_rows = read_csv_exact(disposition_path, DISPOSITION_HEADER)
    technical_disposition_rows = (
        production_stages.read_technical_disposition_manifest(
            technical_disposition_path
        )
    )

    typed_dicom_rows: list[dict[str, Any]] = []
    for row in dicom_rows:
        typed = dict(row)
        for field in ("read_ok", "is_multiframe", "pixel_decode_ok"):
            typed[field] = _strict_csv_bool(
                row[field], code="DICOM_AUDIT_BOOLEAN_INVALID"
            )
        typed["number_of_frames"] = _strict_csv_nonnegative_integer_or_empty(
            row["number_of_frames"], code="DICOM_AUDIT_INTEGER_INVALID"
        )
        typed_dicom_rows.append(typed)
    try:
        dicom_semantics = production_stages.validate_production_dicom_rows(
            typed_dicom_rows,
            expected_objects=expected_objects,
            expected_studies=expected_studies,
        )
    except production_stages.ProductionStageError as exc:
        raise BatchPreservationError("DICOM_AUDIT_RECOMPUTATION_FAILED") from exc
    if (
        dicom_semantics["n_unreadable"] != 0
        or dicom_semantics["n_pixel_decode_failures"] != 0
    ):
        raise BatchPreservationError("DICOM_AUDIT_RECOMPUTED_FAILURES_PRESENT")

    typed_extraction_rows: list[dict[str, Any]] = []
    integer_fields = (
        tuple(
            count_field
            for count_field, _ in production_stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS
        )
        + tuple(
            count_field
            for count_field, _ in production_stages.DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS
        )
        + ("source_num_frames",)
    )
    for row in extraction_rows:
        typed = dict(row)
        for field in (
            "write_ok",
            "pixel_decode_ok",
            "source_sector_nonempty_gate_passed",
            "source_nonzero_retained_pixel_gate_passed",
            "source_temporal_variation_gate_passed",
            "ordinary_post_crop_nonzero_retained_pixel_gate_passed",
            "ordinary_post_crop_temporal_variation_gate_passed",
            "post_crop_nonzero_retained_pixel_gate_passed",
            "post_crop_temporal_variation_gate_passed",
            "ordinary_sampled_nonzero_retained_pixel_gate_passed",
            "ordinary_sampled_temporal_variation_gate_passed",
            "sampled_nonzero_retained_pixel_gate_passed",
            "sampled_temporal_variation_gate_passed",
            "encoder_visible_nonzero_retained_pixel_gate_passed",
            "encoder_visible_temporal_variation_gate_passed",
        ):
            typed[field] = _strict_csv_bool(
                row[field], code="EXTRACTION_MANIFEST_BOOLEAN_INVALID"
            )
        for field in integer_fields:
            typed[field] = _strict_csv_nonnegative_integer_or_empty(
                row[field], code="EXTRACTION_MANIFEST_INTEGER_INVALID"
            )
        typed_extraction_rows.append(typed)
    try:
        disposition_context = (
            production_stages.context_from_completed_extraction_authority(
                extraction_rows=typed_extraction_rows,
                dicom_rows=typed_dicom_rows,
                manifest_rows=technical_disposition_rows,
                clips_root=clips_root,
                existing_embedding_clip_keys=[
                    str(row["clip_key"]) for row in clip_rows
                ],
            )
        )
    except production_stages.ProductionStageError as exc:
        raise _nested_validation_error(
            exc,
            substage=PreservationValidationSubstage.CONTEXT_RECONSTRUCTION,
        ) from exc
    try:
        extraction_semantics = production_stages.validate_production_extraction_rows(
            typed_extraction_rows,
            expected_cines=dicom_semantics["n_multiframe_candidates"],
            disposition_context=disposition_context,
            clips_root=clips_root if validate_npz_bodies else None,
        )
    except production_stages.ProductionStageError as exc:
        raise _nested_validation_error(
            exc,
            substage=PreservationValidationSubstage.EXTRACTION_ROW_VALIDATION,
        ) from exc
    try:
        technical_disposition_semantics = (
            production_stages.validate_technical_disposition_manifest_rows(
                typed_extraction_rows,
                technical_disposition_rows,
                context=disposition_context,
            )
        )
    except production_stages.ProductionStageError as exc:
        raise _nested_validation_error(
            exc,
            substage=(
                PreservationValidationSubstage.TECHNICAL_DISPOSITION_VALIDATION
            ),
        ) from exc

    dicom_candidate_identity = {
        (
            str(raw["subject_id"]),
            str(raw["study_id"]),
            str(raw["source_relative_path"]),
        )
        for raw, typed in zip(dicom_rows, typed_dicom_rows)
        if typed["read_ok"] is True and typed["is_multiframe"] is True
    }
    extraction_source_identity = {
        (
            str(row["subject_id"]),
            str(row["study_id"]),
            str(row["source_relative_path"]),
        )
        for row in extraction_rows
    }
    if (
        len(dicom_candidate_identity) != len(extraction_rows)
        or len(extraction_source_identity) != len(extraction_rows)
        or dicom_candidate_identity != extraction_source_identity
    ):
        raise BatchPreservationError("DICOM_EXTRACTION_CANDIDATE_SET_MISMATCH")

    identity_fields = (
        "subject_id",
        "study_id",
        "clip_key",
        "physical_source_key",
    )
    extraction_identity = {
        tuple(str(raw[field]) for field in identity_fields)
        for raw, typed in zip(extraction_rows, typed_extraction_rows)
        if typed["write_ok"] is True
    }
    clip_identity = {
        tuple(str(row[field]) for field in identity_fields) for row in clip_rows
    }
    if len(extraction_identity) != len(clip_rows) or extraction_identity != clip_identity:
        raise BatchPreservationError("EXTRACTION_CLIP_IDENTITY_SET_MISMATCH")

    return {
        "dicom_rows": dicom_rows,
        "extraction_rows": extraction_rows,
        "clip_rows": clip_rows,
        "study_rows": study_rows,
        "disposition_rows": disposition_rows,
        "technical_disposition_rows": technical_disposition_rows,
        "dicom_semantics": dicom_semantics,
        "extraction_semantics": extraction_semantics,
        "technical_disposition_semantics": technical_disposition_semantics,
    }


def validate_recomputed_stage_summaries(
    *,
    dicom_summary: Mapping[str, Any],
    dicom_semantics: Mapping[str, Any],
    extraction_semantics: Mapping[str, Any],
) -> None:
    if set(dicom_summary) != set(
        production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
    ):
        raise BatchPreservationError("DICOM_SUMMARY_SCHEMA_MISMATCH")
    if any(
        dicom_summary.get(key) != dicom_semantics.get(key)
        for key in DICOM_RECOMPUTED_SUMMARY_KEYS
    ):
        raise BatchPreservationError("DICOM_SUMMARY_RECOMPUTATION_MISMATCH")
    if any(
        dicom_summary.get(key) != extraction_semantics.get(key)
        for key in EXTRACTION_RECOMPUTED_SUMMARY_KEYS
    ):
        raise BatchPreservationError("EXTRACTION_SUMMARY_RECOMPUTATION_MISMATCH")


def validate_technical_disposition_summary_bindings(
    *,
    dicom_summary: Mapping[str, Any],
    embedding_summary: Mapping[str, Any],
    extraction_semantics: Mapping[str, Any],
    technical_semantics: Mapping[str, Any],
    technical_manifest_path: Path,
) -> None:
    digest = production_stages.technical_disposition_manifest_sha256(
        technical_manifest_path
    )
    common = {
        "n_object_technical_dispositions": technical_semantics[
            "n_object_technical_dispositions"
        ],
        "n_studies_affected_by_technical_disposition": technical_semantics[
            "n_studies_affected_by_technical_disposition"
        ],
        "technical_disposition_counts_by_class": technical_semantics[
            "technical_disposition_counts_by_class"
        ],
        "technical_disposition_policy_version": (
            production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "technical_disposition_manifest_sha256": digest,
        "all_extraction_rows_resolved": True,
        "all_technical_dispositions_retained": True,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "n_new_no_cine_studies": 0,
    }
    dicom_static = {
        "successful_extraction_gate_scope": "SUCCESSFUL_EXTRACTIONS_ONLY",
        "requested_cine_resolution_scope": (
            "SUCCESSFUL_EXTRACTION_OR_APPROVED_OBJECT_TECHNICAL_DISPOSITION"
        ),
        "identifiers_emitted": False,
        "paths_emitted": False,
    }
    embedding_static = {
        "encoder_only": True,
        "view_classifier_used": False,
        "pooling": "stable_clip_key_order_float64_mean_then_float32",
        "checkpoint_sha256": production_stages.CHECKPOINT_SHA256,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }
    if (
        set(dicom_summary)
        != set(production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2)
        or dicom_summary.get("schema_version") != 2
        or dicom_summary.get("artifact_type")
        != "lvef_c3_batch_dicom_extraction_summary_v2"
        or any(dicom_summary.get(key) != value for key, value in common.items())
        or any(
            dicom_summary.get(key) != value
            for key, value in dicom_static.items()
        )
        or dicom_summary.get("n_successfully_extracted_cines")
        != extraction_semantics["n_successfully_extracted_cines"]
        or set(embedding_summary)
        != set(production_stages.ECHOPRIME_SUMMARY_KEYS_V2)
        or embedding_summary.get("schema_version") != 2
        or embedding_summary.get("artifact_type")
        != "lvef_c3_batch_echoprime_pooling_summary_v2"
        or any(embedding_summary.get(key) != value for key, value in common.items())
        or any(
            embedding_summary.get(key) != value
            for key, value in embedding_static.items()
        )
        or embedding_summary.get("all_successful_extractions_embedded") is not True
        or embedding_summary.get("n_clip_embeddings")
        != extraction_semantics["n_successfully_extracted_cines"]
    ):
        raise BatchPreservationError(
            "TECHNICAL_DISPOSITION_STAGE_SUMMARY_BINDING_MISMATCH"
        )


def validate_prespecified_no_cine_authority(
    *,
    disposition_rows: Sequence[Mapping[str, Any]],
    planned_batch: Mapping[str, Any],
    embedding_summary: Mapping[str, Any],
) -> dict[str, Any]:
    expected_keys = list(planned_batch.get("prespecified_no_cine_study_keys", ()))
    expected = {
        (str(row.get("subject_id")), str(row.get("study_id")))
        for row in expected_keys
        if isinstance(row, Mapping)
    }
    actual = {
        (str(row.get("subject_id")), str(row.get("study_id")))
        for row in disposition_rows
        if row.get("disposition")
        == "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    }
    try:
        actual_keys = [
            {"subject_id": subject, "study_id": study}
            for subject, study in sorted(
                actual, key=lambda pair: (int(pair[0]), int(pair[1]))
            )
        ]
    except (TypeError, ValueError) as exc:
        raise BatchPreservationError(
            "PRESPECIFIED_NO_CINE_IDENTITY_MISMATCH"
        ) from exc
    expected_sha = planned_batch.get(
        "prespecified_no_cine_study_set_sha256"
    )
    actual_sha = core.canonical_json_sha256(actual_keys)
    if (
        len(expected) != planned_batch.get("expected_no_cine_studies")
        or core.canonical_json_sha256(expected_keys) != expected_sha
        or actual != expected
        or embedding_summary.get("n_no_cine_studies") != len(actual)
        or embedding_summary.get("n_new_no_cine_studies")
        != len(actual - expected)
        or embedding_summary.get("prespecified_no_cine_study_set_sha256")
        != expected_sha
        or embedding_summary.get("actual_no_cine_study_set_sha256")
        != actual_sha
        or embedding_summary.get("all_no_cine_studies_prespecified") is not True
    ):
        raise BatchPreservationError("PRESPECIFIED_NO_CINE_IDENTITY_MISMATCH")
    return {
        "n_no_cine_studies": len(actual),
        "n_new_no_cine_studies": len(actual - expected),
        "prespecified_no_cine_study_set_sha256": expected_sha,
        "all_no_cine_studies_prespecified": True,
    }


def validate_preservation_disposition_accounting(
    receipt: Mapping[str, Any]
) -> None:
    dispositions = receipt.get("n_object_technical_dispositions")
    affected = receipt.get("n_studies_affected_by_technical_disposition")
    successful = receipt.get("n_successfully_extracted_cines")
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (dispositions, affected, successful)
        )
        or affected > dispositions
        or (affected == 0) is not (dispositions == 0)
        or receipt.get("technical_disposition_counts_by_class")
        != {
            production_stages.OBJECT_TECHNICAL_DISPOSITION: dispositions
        }
        or receipt.get("n_multiframe_cines") != successful + dispositions
        or receipt.get("n_extracted_clips") != successful
        or receipt.get("n_unique_clip_keys") != successful
        or receipt.get("n_clip_embeddings") != successful
    ):
        raise BatchPreservationError(
            "PRESERVATION_TECHNICAL_DISPOSITION_ACCOUNTING_INVALID"
        )


def validate_embedding_array_authority(
    *,
    clip_array: Any,
    study_array: Any,
    clip_rows: Sequence[Mapping[str, Any]],
    study_rows: Sequence[Mapping[str, Any]],
    embedding_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind exact float32 arrays and clip-manifest numeric claims to summary."""
    import numpy as np

    if clip_array.dtype != np.dtype("float32") or study_array.dtype != np.dtype(
        "float32"
    ):
        raise BatchPreservationError("EMBEDDING_ARRAY_DTYPE_MISMATCH")
    if (
        clip_array.ndim != 2
        or study_array.ndim != 2
        or clip_array.shape[1] != 512
        or study_array.shape[1] != 512
        or len(clip_array) != len(clip_rows)
        or len(study_array) != len(study_rows)
    ):
        raise BatchPreservationError("EMBEDDING_ARRAY_SHAPE_OR_ROW_MISMATCH")
    if not np.isfinite(clip_array).all() or not np.isfinite(study_array).all():
        raise BatchPreservationError("EMBEDDING_ARRAY_NONFINITE")

    for row in clip_rows:
        try:
            index = int(str(row["embedding_idx"]))
        except (TypeError, ValueError) as exc:
            raise BatchPreservationError("CLIP_INDEX_INVALID") from exc
        if (
            str(index) != str(row["embedding_idx"])
            or index < 0
            or index >= len(clip_array)
        ):
            raise BatchPreservationError("CLIP_INDEX_INVALID")
        if not _strict_csv_bool(
            str(row["write_ok"]), code="CLIP_MANIFEST_WRITE_STATUS_INVALID"
        ):
            raise BatchPreservationError("CLIP_MANIFEST_WRITE_STATUS_INVALID")
        norm_text = str(row["embedding_l2_norm"])
        try:
            reported_norm = float(norm_text)
        except ValueError as exc:
            raise BatchPreservationError("CLIP_MANIFEST_L2_NORM_INVALID") from exc
        if norm_text != norm_text.strip() or not math.isfinite(reported_norm):
            raise BatchPreservationError("CLIP_MANIFEST_L2_NORM_INVALID")
        expected_norm = float(
            np.linalg.norm(clip_array[index].astype(np.float64, copy=False))
        )
        if not math.isclose(
            reported_norm,
            expected_norm,
            rel_tol=CLIP_L2_NORM_REL_TOL,
            abs_tol=CLIP_L2_NORM_ABS_TOL,
        ):
            raise BatchPreservationError("CLIP_MANIFEST_L2_NORM_MISMATCH")

    if (
        isinstance(embedding_summary.get("n_clip_embeddings"), bool)
        or embedding_summary.get("n_clip_embeddings") != len(clip_array)
        or isinstance(embedding_summary.get("n_pooled_studies"), bool)
        or embedding_summary.get("n_pooled_studies") != len(study_array)
        or embedding_summary.get("embedding_dimension") != 512
        or embedding_summary.get("embedding_dtype") != "float32"
        or embedding_summary.get("all_finite") is not True
    ):
        raise BatchPreservationError("EMBEDDING_SUMMARY_ARRAY_MISMATCH")
    return {
        "n_clip_embeddings": len(clip_array),
        "n_study_embeddings": len(study_array),
        "embedding_dimension": 512,
        "embedding_dtype": "float32",
        "all_finite": True,
        "clip_l2_norm_tolerance_rel": CLIP_L2_NORM_REL_TOL,
        "clip_l2_norm_tolerance_abs": CLIP_L2_NORM_ABS_TOL,
    }


def write_bytes_no_clobber(path: Path, body: bytes) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise BatchPreservationError("PRESERVATION_OUTPUT_COLLISION")
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
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


def _artifact_record(path: Path, root: Path, role: str) -> dict[str, Any]:
    size_bytes, digest = stable_manifest_artifact_authority(path)
    return {
        "relative_path": safe_relative(path, root),
        "size_bytes": size_bytes,
        "sha256": digest,
        "role": role,
    }


def _walk_regular(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise BatchPreservationError("ARTIFACT_DIRECTORY_NOT_REGULAR")
    paths: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in names:
            if (current / name).is_symlink():
                raise BatchPreservationError("SYMLINK_IN_ARTIFACT_TREE")
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise BatchPreservationError("NONREGULAR_IN_ARTIFACT_TREE")
            paths.append(path)
    return sorted(paths)


def validate_study_pooling_records(
    *, clip_rows: Sequence[Mapping[str, Any]],
    study_rows: Sequence[Mapping[str, Any]],
    disposition_rows: Sequence[Mapping[str, Any]],
    planned_studies: Sequence[Mapping[str, Any]],
    clip_vector_hashes: Sequence[str], study_vector_hashes: Sequence[str],
) -> dict[str, Any]:
    """Prove exact selected ownership and one vector per cine-eligible study."""
    planned: dict[str, str] = {}
    planned_subjects: set[str] = set()
    for row in planned_studies:
        study = str(row["study_id"])
        subject = str(row["subject_id"])
        if study in planned or subject in planned_subjects:
            raise BatchPreservationError("PLANNED_STUDY_OWNERSHIP_NOT_ONE_TO_ONE")
        planned[study] = subject
        planned_subjects.add(subject)
    clip_counts: dict[str, int] = {}
    clip_indices: set[int] = set()
    for row in clip_rows:
        study = str(row.get("study_id"))
        subject = str(row.get("subject_id"))
        try:
            index = int(row.get("embedding_idx"))
        except (TypeError, ValueError) as exc:
            raise BatchPreservationError("CLIP_INDEX_INVALID") from exc
        if (
            str(index) != str(row.get("embedding_idx"))
            or index < 0
            or index >= len(clip_vector_hashes)
            or index in clip_indices
            or planned.get(study) != subject
            or row.get("embedding_sha256") != clip_vector_hashes[index]
        ):
            raise BatchPreservationError("CLIP_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH")
        clip_indices.add(index)
        clip_counts[study] = clip_counts.get(study, 0) + 1
    if clip_indices != set(range(len(clip_vector_hashes))) or len(clip_rows) != len(
        clip_vector_hashes
    ):
        raise BatchPreservationError("CLIP_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH")
    dispositions: dict[str, str] = {}
    disposition_subjects: set[str] = set()
    allowed = {"IMAGING_ELIGIBLE", "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"}
    for row in disposition_rows:
        study = str(row.get("study_id"))
        subject = str(row.get("subject_id"))
        disposition = str(row.get("disposition"))
        if (
            study in dispositions
            or subject in disposition_subjects
            or planned.get(study) != subject
            or disposition not in allowed
        ):
            raise BatchPreservationError("STUDY_DISPOSITION_INVALID")
        dispositions[study] = disposition
        disposition_subjects.add(subject)
    if set(dispositions) != set(planned):
        raise BatchPreservationError("STUDY_DISPOSITION_COHORT_MISMATCH")
    eligible = set(clip_counts)
    if {
        study for study, disposition in dispositions.items()
        if disposition == "IMAGING_ELIGIBLE"
    } != eligible:
        raise BatchPreservationError("STUDY_DISPOSITION_ELIGIBILITY_MISMATCH")
    observed_studies: set[str] = set()
    observed_subjects: set[str] = set()
    study_indices: set[int] = set()
    for row in study_rows:
        study = str(row.get("study_id"))
        subject = str(row.get("subject_id"))
        try:
            index = int(row.get("study_idx"))
            n_clips = int(row.get("n_clips"))
        except (TypeError, ValueError) as exc:
            raise BatchPreservationError("STUDY_MANIFEST_INDEX_OR_COUNT_INVALID") from exc
        if (
            str(index) != str(row.get("study_idx"))
            or str(n_clips) != str(row.get("n_clips"))
            or index < 0
            or index >= len(study_vector_hashes)
            or index in study_indices
            or study in observed_studies
            or subject in observed_subjects
            or planned.get(study) != subject
            or clip_counts.get(study) != n_clips
            or row.get("embedding_sha256") != study_vector_hashes[index]
        ):
            raise BatchPreservationError("STUDY_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH")
        study_indices.add(index)
        observed_studies.add(study)
        observed_subjects.add(subject)
    if (
        observed_studies != eligible
        or study_indices != set(range(len(study_vector_hashes)))
        or len(study_rows) != len(study_vector_hashes)
    ):
        raise BatchPreservationError("STUDY_MANIFEST_ELIGIBLE_SET_MISMATCH")
    return {
        "eligible_studies": len(eligible),
        "no_cine_studies": len(planned) - len(eligible),
        "one_vector_per_eligible_study": True,
    }


def mean_pool_study_embeddings(
    *, clip_embeddings: Any, clip_rows: Sequence[Mapping[str, Any]],
    study_rows: Sequence[Mapping[str, Any]],
) -> Any:
    """Pool canonical clip rows by study using float64 means and float32 output."""
    import numpy as np

    if (
        not isinstance(clip_rows, Sequence)
        or isinstance(clip_rows, (str, bytes))
        or not isinstance(study_rows, Sequence)
        or isinstance(study_rows, (str, bytes))
    ):
        raise BatchPreservationError("STUDY_POOLING_ROWS_INVALID")
    if (
        not isinstance(clip_embeddings, np.ndarray)
        or clip_embeddings.dtype != np.float32
        or clip_embeddings.ndim != 2
        or clip_embeddings.shape[0] != len(clip_rows)
        or clip_embeddings.shape[1] != 512
        or clip_embeddings.shape[0] < 1
    ):
        raise BatchPreservationError("STUDY_POOLING_CLIP_ARRAY_INVALID")
    if not np.isfinite(clip_embeddings).all():
        raise BatchPreservationError("STUDY_POOLING_NONFINITE")

    clip_indices: set[int] = set()
    clips_by_study: dict[str, list[int]] = {}
    for row in clip_rows:
        if not isinstance(row, Mapping):
            raise BatchPreservationError("STUDY_POOLING_CLIP_ROW_INVALID")
        raw_study_id = row.get("study_id")
        raw_index = row.get("embedding_idx")
        if raw_study_id is None or not str(raw_study_id):
            raise BatchPreservationError("STUDY_POOLING_CLIP_ROW_INVALID")
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise BatchPreservationError("STUDY_POOLING_CLIP_ROW_INVALID") from exc
        if (
            str(index) != str(raw_index)
            or index < 0
            or index >= len(clip_embeddings)
            or index in clip_indices
        ):
            raise BatchPreservationError("STUDY_POOLING_CLIP_ROW_INVALID")
        clip_indices.add(index)
        clips_by_study.setdefault(str(raw_study_id), []).append(index)
    if clip_indices != set(range(len(clip_embeddings))):
        raise BatchPreservationError("STUDY_POOLING_CLIP_ROW_INVALID")

    pooled = np.empty((len(study_rows), clip_embeddings.shape[1]), dtype=np.float32)
    study_indices: set[int] = set()
    study_ids: set[str] = set()
    for row in study_rows:
        if not isinstance(row, Mapping):
            raise BatchPreservationError("STUDY_POOLING_STUDY_ROW_INVALID")
        raw_study_id = row.get("study_id")
        raw_index = row.get("study_idx")
        raw_n_clips = row.get("n_clips")
        if raw_study_id is None or not str(raw_study_id):
            raise BatchPreservationError("STUDY_POOLING_STUDY_ROW_INVALID")
        try:
            index = int(raw_index)
            n_clips = int(raw_n_clips)
        except (TypeError, ValueError) as exc:
            raise BatchPreservationError("STUDY_POOLING_STUDY_ROW_INVALID") from exc
        study_id = str(raw_study_id)
        indices = clips_by_study.get(study_id, [])
        if (
            str(index) != str(raw_index)
            or str(n_clips) != str(raw_n_clips)
            or index < 0
            or index >= len(study_rows)
            or index in study_indices
            or study_id in study_ids
            or n_clips < 1
            or len(indices) != n_clips
        ):
            raise BatchPreservationError("STUDY_POOLING_STUDY_ROW_INVALID")
        study_indices.add(index)
        study_ids.add(study_id)
        pooled[index] = clip_embeddings[
            np.asarray(indices, dtype=np.int64)
        ].mean(axis=0, dtype=np.float64).astype(np.float32)
    if (
        study_indices != set(range(len(study_rows)))
        or study_ids != set(clips_by_study)
    ):
        raise BatchPreservationError("STUDY_POOLING_MEMBERSHIP_MISMATCH")
    if not np.isfinite(pooled).all():
        raise BatchPreservationError("STUDY_POOLING_NONFINITE")
    return pooled


def resolve_preservation_runtime_authority(
    *, plan: Mapping[str, Any], contract: Mapping[str, Any], contract_path: Path,
    governing_commit: str, environment_receipt_sha256: str,
    checkpoint_sha256: str, requirements: core.PlanRequirements | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
) -> tuple[core.PlanRequirements, str, dict[str, str], bool]:
    """Resolve default production or explicitly scoped preservation authority."""
    scoped = requirements is not None or expected_runtime_authority is not None
    if requirements is None and expected_runtime_authority is None:
        effective_requirements = core.production_requirements(contract)
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=effective_requirements
        )
        runtime_authority = core.derive_expected_runtime_authority(
            plan,
            requirements=effective_requirements,
            contract=contract,
            contract_path=contract_path,
            governing_commit=governing_commit,
            environment_receipt_sha256=environment_receipt_sha256,
        )
    elif requirements is None or expected_runtime_authority is None:
        raise BatchPreservationError("SCOPED_RUNTIME_AUTHORITY_ARGUMENTS_INCOMPLETE")
    else:
        effective_requirements = requirements
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=effective_requirements
        )
        runtime_authority = core.validate_runtime_authority(expected_runtime_authority)
        if runtime_authority["batch_plan_sha256"] != plan_sha:
            raise BatchPreservationError("SCOPED_RUNTIME_BATCH_PLAN_MISMATCH")
        if any(
            runtime_authority[key] != str(plan["authority"][key])
            for key in core.PLAN_AUTHORITY_KEYS
        ):
            raise BatchPreservationError("SCOPED_RUNTIME_PLAN_AUTHORITY_MISMATCH")
        if (
            runtime_authority["git_commit"] != governing_commit
            or plan["authority"]["orchestration_contract_sha256"]
            != sha256_file(contract_path)
            or plan["authority"]["state_machine_schema_sha256"]
            != str(contract["authority"]["state_machine_schema_sha256"])
            or plan["authority"]["resume_ledger_schema_sha256"]
            != str(contract["authority"]["resume_ledger_schema_sha256"])
        ):
            raise BatchPreservationError("SCOPED_RUNTIME_GOVERNING_COMMIT_MISMATCH")
    if runtime_authority["checkpoint_sha256"] != checkpoint_sha256:
        raise BatchPreservationError("RUNTIME_CHECKPOINT_AUTHORITY_MISMATCH")
    if runtime_authority["environment_receipt_sha256"] != environment_receipt_sha256:
        raise BatchPreservationError("RUNTIME_ENVIRONMENT_AUTHORITY_MISMATCH")
    return effective_requirements, plan_sha, runtime_authority, scoped


def preserve_batch(
    *, contract_path: Path, plan_path: Path, batch_id: str, attempt_id: str,
    governing_commit: str, production_root: Path, output_root: Path,
    environment_receipt: Path, checkpoint: Path, scheduler_job_identity: str,
    input_ledger: Path, requirements: core.PlanRequirements | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
    scheduler_runner_path: Path | None = None,
    artifact_validation_context: ArtifactValidationContext = STRICT_CONTENT_HASH,
    runtime_validation_context: (
        production_stages.RuntimeAuthorityValidationContext
    ) = production_stages.LIVE_RUNTIME_CAPTURE,
    npz_metadata_diagnostics: (
        MutableMapping[str, int | str] | None
    ) = None,
) -> dict[str, Any]:
    artifact_validation_context = require_artifact_validation_context(
        artifact_validation_context
    )
    observed_npz_diagnostics = _require_npz_metadata_diagnostics(
        npz_metadata_diagnostics
    )
    if (
        observed_npz_diagnostics is not None
        and artifact_validation_context
        is not R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
    ):
        raise BatchPreservationError("ARTIFACT_VALIDATION_CONTEXT_INVALID")
    if not isinstance(
        runtime_validation_context,
        production_stages.RuntimeAuthorityValidationContext,
    ):
        raise BatchPreservationError("RUNTIME_VALIDATION_CONTEXT_INVALID")
    if not BATCH_RE.fullmatch(batch_id) or not ATTEMPT_RE.fullmatch(attempt_id):
        raise BatchPreservationError("BATCH_OR_ATTEMPT_INVALID")
    if not re.fullmatch(r"[0-9a-f]{40}", governing_commit):
        raise BatchPreservationError("GOVERNING_COMMIT_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", scheduler_job_identity):
        raise BatchPreservationError("SCHEDULER_IDENTITY_INVALID")
    if production_root.is_symlink() or not production_root.is_dir():
        raise BatchPreservationError("PRODUCTION_ROOT_INVALID")
    expected_output = production_root / "attempts" / attempt_id / "batches" / batch_id / "preservation"
    if (
        output_root != expected_output
        or output_root.is_symlink()
        or (output_root.exists() and not output_root.is_dir())
    ):
        raise BatchPreservationError("PRESERVATION_OUTPUT_ROOT_INVALID")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(mode=0o700, exist_ok=True)
    contract = core.load_orchestration_contract(contract_path)
    plan = core.load_strict_json(plan_path)
    if not isinstance(plan, Mapping):
        raise BatchPreservationError("BATCH_PLAN_NOT_MAPPING")
    (
        effective_requirements,
        plan_sha,
        runtime_authority,
        scoped_runtime_authority,
    ) = resolve_preservation_runtime_authority(
        plan=plan,
        contract=contract,
        contract_path=contract_path,
        governing_commit=governing_commit,
        environment_receipt_sha256=sha256_file(environment_receipt),
        checkpoint_sha256=sha256_file(checkpoint),
        requirements=requirements,
        expected_runtime_authority=expected_runtime_authority,
    )
    batch = next((item for item in plan["batches"] if item["batch_id"] == batch_id), None)
    if batch is None:
        raise BatchPreservationError("BATCH_NOT_PLANNED")
    raw_root = production_root / "attempts" / attempt_id / "raw" / batch_id
    cache_batch_root = (
        production_root / "attempts" / attempt_id / "extracted_cache" / batch_id
    )
    batch_root = production_root / "attempts" / attempt_id / "batches" / batch_id
    paths = {
        "download_ledger": batch_root / "download_resume_ledger.restricted.json",
        "extraction_ledger": batch_root / "extraction_resume_ledger.restricted.json",
        "pooling_ledger": batch_root / "pooling_resume_ledger.restricted.json",
        "state_input_ledger": input_ledger,
        "download_manifest": raw_root / "verified_download_manifest.restricted.csv",
        "dicom_summary": cache_batch_root / "dicom_extraction" / "dicom_extraction.summary.json",
        "dicom_audit": cache_batch_root / "dicom_extraction" / "dicom_audit.restricted.csv",
        "extraction_manifest": cache_batch_root / "dicom_extraction" / "extraction_manifest.restricted.csv",
        "technical_disposition_manifest": cache_batch_root / "dicom_extraction" / "technical_disposition_manifest.restricted.csv",
        "clip_embeddings": batch_root / "echoprime" / "clip_embeddings.restricted.npz",
        "clip_manifest": batch_root / "echoprime" / "clip_manifest.restricted.csv",
        "study_embeddings": batch_root / "echoprime" / "study_embeddings.restricted.npz",
        "study_manifest": batch_root / "echoprime" / "study_manifest.restricted.csv",
        "study_disposition": batch_root / "echoprime" / "study_disposition.restricted.csv",
        "embedding_summary": batch_root / "echoprime" / "echoprime_pooling.summary.json",
    }
    if input_ledger != paths["pooling_ledger"]:
        raise BatchPreservationError("PRESERVATION_INPUT_LEDGER_PATH_INVALID")
    effective_scheduler_runner = (
        scheduler_runner_path
        if scheduler_runner_path is not None
        else Path(__file__).resolve().parent
        / "scc_run_lvef_c3_production_batch_v2.sh"
    )
    validate_artifact_validation_scope(
        artifact_validation_context=artifact_validation_context,
        production_root=production_root,
        attempt_id=attempt_id,
        batch_id=batch_id,
        plan_sha256=plan_sha,
        governing_commit=governing_commit,
        scheduler_runner_path=effective_scheduler_runner,
    )
    external = {
        "environment_receipt": environment_receipt,
        "checkpoint": checkpoint,
        "orchestration_contract": contract_path,
        "batch_plan": plan_path,
        "stage_wrapper": Path(__file__).resolve().parent / "lvef_c3_production_stages.py",
        "batch_preservation_script": Path(__file__).resolve(),
        "scheduler_runner": effective_scheduler_runner,
    }
    for path in external.values():
        if path.is_symlink() or not path.is_file():
            raise BatchPreservationError("EXTERNAL_AUTHORITY_NOT_REGULAR")
    expected_objects = {
        row["source_object_key"]: row for row in batch["objects"]
    }
    download_rows, raw_dicom_authority = (
        validate_verified_raw_dicom_authority(
            raw_objects_root=raw_root / "objects",
            verified_download_manifest=paths["download_manifest"],
            planned_batch=batch,
            artifact_validation_context=artifact_validation_context,
        )
    )
    extraction_root = cache_batch_root / "dicom_extraction"
    sealed_npz_authority = (
        seal_r8u_r3_extracted_npz_authority(
            extraction_manifest=paths["extraction_manifest"],
            extraction_root=extraction_root,
            diagnostics=observed_npz_diagnostics,
        )
        if artifact_validation_context
        is R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
        else {}
    )
    records: list[dict[str, Any]] = []
    for path in _walk_regular(raw_root):
        sealed = raw_dicom_authority.get(path)
        if sealed is not None:
            record = (
                _sealed_raw_dicom_artifact_record(
                    sealed,
                    production_root,
                    "raw_dicom_and_download_authority",
                )
                if artifact_validation_context in BODY_FREE_RAW_CONTEXTS
                else _artifact_record(
                    path,
                    production_root,
                    "raw_dicom_and_download_authority",
                )
            )
        else:
            if (
                artifact_validation_context in BODY_FREE_RAW_CONTEXTS
                and path.suffix.lower() == ".dcm"
            ):
                raise BatchPreservationError(
                    "RECOVERY_RAW_DICOM_AUTHORITY_MISSING"
                )
            record = _artifact_record(
                path,
                production_root,
                "raw_dicom_and_download_authority",
            )
        records.append(record)
    clip_cache_root = extraction_root / "clips"
    for path in _walk_regular(extraction_root):
        role = (
            "extracted_npz_cache_owner_retirable"
            if path.is_relative_to(clip_cache_root)
            else "dicom_extraction_metadata_retained"
        )
        sealed_npz = sealed_npz_authority.get(path)
        if sealed_npz is not None:
            records.append(
                _sealed_extracted_npz_artifact_record(
                    sealed_npz, production_root, role
                )
            )
        else:
            if (
                artifact_validation_context
                is R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
                and path.suffix.lower() == ".npz"
            ):
                raise BatchPreservationError(
                    "R8U_R3_EXTRACTION_NPZ_AUTHORITY_MISSING"
                )
            records.append(_artifact_record(path, production_root, role))
    records.extend(
        _artifact_record(path, production_root, "embedding_and_pooling_retained")
        for path in _walk_regular(batch_root / "echoprime")
    )
    for role in ("download_ledger", "extraction_ledger", "pooling_ledger"):
        records.append(_artifact_record(paths[role], production_root, role))
    if len({item["relative_path"] for item in records}) != len(records):
        raise BatchPreservationError("DUPLICATE_PRESERVATION_PATH")
    dicom = load_json(paths["dicom_summary"], "DICOM_SUMMARY")
    embedding = load_json(paths["embedding_summary"], "EMBEDDING_SUMMARY")
    ledger = load_json(paths["state_input_ledger"], "STATE_INPUT_LEDGER")
    expected_keys = {batch_id: set(expected_objects)}
    try:
        if not scoped_runtime_authority:
            core.validate_ledger_against_current_runtime(
                ledger,
                plan=plan,
                requirements=effective_requirements,
                contract=contract,
                contract_path=contract_path,
                governing_commit=governing_commit,
                environment_receipt_sha256=sha256_file(environment_receipt),
                batch_id=batch_id,
            )
        core.validate_resume_authority(
            ledger,
            expected_authority=runtime_authority,
            attempt_id=attempt_id,
            expected_object_keys=expected_keys,
        )
    except core.OrchestrationError as exc:
        raise BatchPreservationError("STATE_LEDGER_AUTHORITY_INVALID") from exc
    if ledger["authority"]["batch_plan_sha256"] != plan_sha:
        raise BatchPreservationError("DOWNLOAD_LEDGER_PLAN_MISMATCH")
    batch_ledger = ledger["batches"].get(batch_id)
    if not isinstance(batch_ledger, Mapping) or batch_ledger.get("state") != "STUDY_POOLING_COMPLETE":
        raise BatchPreservationError("STATE_LEDGER_NOT_POOLING_COMPLETE")
    if batch_ledger.get("download_manifest_sha256") != sha256_file(paths["download_manifest"]):
        raise BatchPreservationError("DOWNLOAD_LEDGER_MANIFEST_HASH_MISMATCH")
    if dicom.get("status") not in {
        "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "PASS_EXTRACTION_WITH_OBJECT_TECHNICAL_DISPOSITIONS",
    } or embedding.get("status") != "PASS_ECHOPRIME_AND_POOLING":
        raise BatchPreservationError("STAGE_SUMMARY_NOT_PASS")
    if embedding.get("n_pooled_studies", -1) + embedding.get("n_no_cine_studies", -1) != batch["n_studies"]:
        raise BatchPreservationError("POOLING_SUMMARY_COUNT_MISMATCH")
    import numpy as np
    import pandas as pd
    stage_csvs = validate_stage_csv_authority(
        dicom_audit_path=paths["dicom_audit"],
        extraction_manifest_path=paths["extraction_manifest"],
        clip_manifest_path=paths["clip_manifest"],
        study_manifest_path=paths["study_manifest"],
        disposition_path=paths["study_disposition"],
        technical_disposition_path=paths["technical_disposition_manifest"],
        clips_root=clip_cache_root,
        expected_objects=batch["n_objects"],
        expected_studies=batch["n_studies"],
        validate_npz_bodies=(
            artifact_validation_context
            is not R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
        ),
    )
    dicom_semantics = stage_csvs["dicom_semantics"]
    extraction_semantics = stage_csvs["extraction_semantics"]
    technical_disposition_semantics = stage_csvs[
        "technical_disposition_semantics"
    ]
    try:
        no_cine_semantics = validate_prespecified_no_cine_authority(
            disposition_rows=stage_csvs["disposition_rows"],
            planned_batch=batch,
            embedding_summary=embedding,
        )
        validate_recomputed_stage_summaries(
            dicom_summary=dicom,
            dicom_semantics=dicom_semantics,
            extraction_semantics=extraction_semantics,
        )
        validate_technical_disposition_summary_bindings(
            dicom_summary=dicom,
            embedding_summary=embedding,
            extraction_semantics=extraction_semantics,
            technical_semantics=technical_disposition_semantics,
            technical_manifest_path=paths[
                "technical_disposition_manifest"
            ],
        )
    except (BatchPreservationError, production_stages.ProductionStageError) as exc:
        raise _preservation_substage_error(
            exc,
            substage=PreservationValidationSubstage.SUMMARY_RECONCILIATION,
        ) from exc
    clip_manifest = pd.DataFrame(
        stage_csvs["clip_rows"], columns=CLIP_MANIFEST_HEADER
    )
    study_manifest = pd.DataFrame(
        stage_csvs["study_rows"], columns=STUDY_MANIFEST_HEADER
    )
    disposition = pd.DataFrame(
        stage_csvs["disposition_rows"], columns=DISPOSITION_HEADER
    )
    extraction_manifest = pd.DataFrame(
        stage_csvs["extraction_rows"], columns=EXTRACTION_MANIFEST_HEADER
    )
    try:
        with np.load(paths["clip_embeddings"], allow_pickle=False) as archive:
            if set(archive.files) != {"embeddings"}:
                raise BatchPreservationError(
                    "CLIP_EMBEDDING_NPZ_SCHEMA_MISMATCH"
                )
            clip_array = archive["embeddings"]
        with np.load(paths["study_embeddings"], allow_pickle=False) as archive:
            if set(archive.files) != {"embeddings"}:
                raise BatchPreservationError(
                    "STUDY_EMBEDDING_NPZ_SCHEMA_MISMATCH"
                )
            study_array = archive["embeddings"]
        embedding_array_semantics = validate_embedding_array_authority(
            clip_array=clip_array,
            study_array=study_array,
            clip_rows=stage_csvs["clip_rows"],
            study_rows=stage_csvs["study_rows"],
            embedding_summary=embedding,
        )
    except (BatchPreservationError, production_stages.ProductionStageError) as exc:
        raise _preservation_substage_error(
            exc,
            substage=(
                PreservationValidationSubstage.ARRAY_EMBEDDING_VALIDATION
            ),
        ) from exc
    duplicate_source = int(extraction_manifest["physical_source_key"].duplicated().sum())
    duplicate_clip = int(clip_manifest["clip_key"].duplicated().sum())
    nonfinite = 0
    wrong_dimension = 0
    selected_studies = {str(item["study_id"]) for item in batch["studies"]}
    observed_dispositions = set(disposition["study_id"].astype(str))
    outside = len(observed_dispositions - selected_studies)
    missing = len(selected_studies - observed_dispositions)
    if (
        duplicate_source
        or duplicate_clip
        or nonfinite
        or wrong_dimension
        or outside
        or missing
    ):
        raise BatchPreservationError(
            "ACTUAL_EMBEDDING_OR_COHORT_GATE_FAILED",
            validation_substage=(
                PreservationValidationSubstage.ARRAY_EMBEDDING_VALIDATION
            ),
        )
    import lvef_reconstruction_smoke as smoke
    clip_vector_hashes = [smoke.array_content_sha256(row) for row in clip_array]
    study_vector_hashes = [smoke.array_content_sha256(row) for row in study_array]
    try:
        pooling_semantics = validate_study_pooling_records(
            clip_rows=clip_manifest.to_dict(orient="records"),
            study_rows=study_manifest.to_dict(orient="records"),
            disposition_rows=disposition.to_dict(orient="records"),
            planned_studies=batch["studies"],
            clip_vector_hashes=clip_vector_hashes,
            study_vector_hashes=study_vector_hashes,
        )
        expected_study_array = mean_pool_study_embeddings(
            clip_embeddings=clip_array,
            clip_rows=clip_manifest.to_dict(orient="records"),
            study_rows=study_manifest.to_dict(orient="records"),
        )
    except (BatchPreservationError, production_stages.ProductionStageError) as exc:
        raise _preservation_substage_error(
            exc,
            substage=(
                PreservationValidationSubstage.ARRAY_EMBEDDING_VALIDATION
            ),
        ) from exc
    if not np.array_equal(expected_study_array, study_array):
        raise BatchPreservationError(
            "STUDY_POOLING_RECOMPUTATION_MISMATCH",
            validation_substage=(
                PreservationValidationSubstage.ARRAY_EMBEDDING_VALIDATION
            ),
        )
    if (
        pooling_semantics["eligible_studies"] != embedding["n_pooled_studies"]
        or pooling_semantics["no_cine_studies"] != embedding["n_no_cine_studies"]
    ):
        raise BatchPreservationError(
            "POOLING_SUMMARY_SEMANTICS_MISMATCH",
            validation_substage=(
                PreservationValidationSubstage.ARRAY_EMBEDDING_VALIDATION
            ),
        )

    manifest_path = output_root / "batch_preservation_manifest.restricted.tsv"
    manifest_body = ["\t".join(MANIFEST_HEADER)]
    for item in sorted(records, key=lambda value: value["relative_path"]):
        manifest_body.append(
            f"{item['relative_path']}\t{item['size_bytes']}\t{item['sha256']}\t{item['role']}"
        )
    manifest_payload = ("\n".join(manifest_body) + "\n").encode("utf-8")
    if manifest_path.exists() or manifest_path.is_symlink():
        if manifest_path.is_symlink() or manifest_path.read_bytes() != manifest_payload:
            raise BatchPreservationError("PRESERVATION_RECOVERY_MANIFEST_MISMATCH")
    else:
        write_bytes_no_clobber(manifest_path, manifest_payload)
    # Independent second pass.  The fixed recovery reprojects sealed DICOM
    # metadata; every other artifact is opened and hashed normally.
    verified = read_csv_exact(manifest_path, MANIFEST_HEADER, delimiter="\t")
    for item in verified:
        artifact = production_root / item["relative_path"]
        sealed = raw_dicom_authority.get(artifact)
        sealed_npz = sealed_npz_authority.get(artifact)
        try:
            if (
                artifact_validation_context in BODY_FREE_RAW_CONTEXTS
                and sealed is not None
            ):
                validate_sealed_raw_dicom_metadata(sealed)
                size_bytes, digest = (
                    sealed.size_bytes,
                    sealed.observed_sha256,
                )
            elif sealed_npz is not None:
                if (
                    artifact_validation_context
                    is not R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
                ):
                    raise BatchPreservationError(
                        "R8U_R3_EXTRACTION_NPZ_AUTHORITY_INVALID"
                    )
                validate_sealed_extracted_npz_metadata(
                    sealed_npz, diagnostics=observed_npz_diagnostics
                )
                size_bytes, digest = (
                    sealed_npz.size_bytes,
                    sealed_npz.observed_sha256,
                )
            else:
                if (
                    artifact_validation_context in BODY_FREE_RAW_CONTEXTS
                    and artifact.suffix.lower() == ".dcm"
                ):
                    raise BatchPreservationError(
                        "RECOVERY_RAW_DICOM_AUTHORITY_MISSING"
                    )
                if (
                    artifact_validation_context
                    is R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
                    and artifact.suffix.lower() == ".npz"
                    and artifact.is_relative_to(clip_cache_root)
                ):
                    raise BatchPreservationError(
                        "R8U_R3_EXTRACTION_NPZ_AUTHORITY_MISSING"
                    )
                size_bytes, digest = stable_manifest_artifact_authority(
                    artifact
                )
        except BatchPreservationError as exc:
            if exc.code.startswith("R8U_NPZ_"):
                raise
            raise BatchPreservationError(
                "SECOND_PASS_ARTIFACT_NOT_REGULAR"
            ) from exc
        if size_bytes != int(item["size_bytes"]) or digest != item["sha256"]:
            raise BatchPreservationError("SECOND_PASS_ARTIFACT_HASH_MISMATCH")
    try:
        environment_authority = (
            production_stages.validate_environment_authority_for_scientific_commit(
                external["environment_receipt"],
                expected_environment_receipt_sha256=runtime_authority[
                    "environment_receipt_sha256"
                ],
                scientific_governing_commit=governing_commit,
                runtime_validation_context=runtime_validation_context,
            )
        )
    except production_stages.ProductionStageError as exc:
        raise BatchPreservationError(
            "ENVIRONMENT_PROVENANCE_AUTHORITY_INVALID"
        ) from exc
    environment = environment_authority["environment_receipt"]
    receipt_path = output_root / "batch_preservation_receipt.restricted.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        prior_receipt = load_json(receipt_path, "PRESERVATION_RECEIPT")
        run_timestamp_utc = str(prior_receipt.get("run_timestamp_utc"))
    else:
        run_timestamp_utc = datetime.now(timezone.utc).isoformat()
    if not TIMESTAMP_RE.fullmatch(run_timestamp_utc):
        raise BatchPreservationError("RUN_TIMESTAMP_INVALID")
    command_checksum = core.canonical_json_sha256(
        {
            "production_stage_wrapper_sha256": sha256_file(external["stage_wrapper"]),
            "batch_preservation_script_sha256": sha256_file(
                external["batch_preservation_script"]
            ),
            "scheduler_runner_sha256": sha256_file(external["scheduler_runner"]),
        }
    )
    config_checksum = core.canonical_json_sha256(
        {
            "orchestration_contract_sha256": sha256_file(contract_path),
            "batch_plan_sha256": plan_sha,
            "state_machine_schema_sha256": runtime_authority[
                "state_machine_schema_sha256"
            ],
            "resume_ledger_schema_sha256": runtime_authority[
                "resume_ledger_schema_sha256"
            ],
        }
    )
    receipt = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_batch_preservation_eligibility_receipt_v3",
        "status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "source_commit": governing_commit,
        "run_timestamp_utc": run_timestamp_utc,
        "cohort_version": str(contract["cohort"]["release"]),
        "split_version": f"split_map_sha256:{contract['cohort']['split_map_sha256']}",
        "execution_contract_version": int(
            contract["authority"]["execution_contract_version"]
        ),
        "orchestration_contract_sha256": sha256_file(contract_path),
        "batch_plan_sha256": plan_sha,
        "checkpoint_sha256": sha256_file(external["checkpoint"]),
        "checkpoint_checksum": sha256_file(external["checkpoint"]),
        "environment_receipt_sha256": sha256_file(external["environment_receipt"]),
        "python_version": str(environment["python_version"]),
        "pytorch_version": str(environment["torch_version"]),
        "torchvision_version": str(environment["torchvision_version"]),
        "cuda_version": str(environment["cuda_version"]),
        "cudnn_version": str(environment["cudnn_version"]),
        "package_inventory_sha256": str(environment["package_inventory_sha256"]),
        "production_stage_wrapper_sha256": sha256_file(external["stage_wrapper"]),
        "batch_preservation_script_sha256": sha256_file(
            external["batch_preservation_script"]
        ),
        "scheduler_runner_sha256": sha256_file(external["scheduler_runner"]),
        "command_checksum": command_checksum,
        "config_checksum": config_checksum,
        "scheduler_job_identity": scheduler_job_identity,
        "state_input_ledger_sha256": sha256_file(paths["state_input_ledger"]),
        "n_selected_studies": batch["n_studies"],
        "n_selected_subjects": batch["n_subjects"],
        "n_expected_objects": batch["n_objects"],
        "expected_source_bytes": batch["source_bytes"],
        "n_download_verified": len(download_rows),
        "n_dicom_readable": dicom_semantics["n_readable"],
        "n_dicom_unreadable": dicom_semantics["n_unreadable"],
        "n_multiframe_cines": dicom_semantics["n_multiframe_candidates"],
        "n_single_frame_objects": dicom_semantics["n_single_frame"],
        "n_extracted_clips": extraction_semantics["n_extracted_clips"],
        "n_successfully_extracted_cines": extraction_semantics[
            "n_successfully_extracted_cines"
        ],
        "n_object_technical_dispositions": extraction_semantics[
            "n_object_technical_dispositions"
        ],
        "n_blocking_failures": extraction_semantics["n_blocking_failures"],
        "n_studies_affected_by_technical_disposition": extraction_semantics[
            "n_studies_affected_by_technical_disposition"
        ],
        "n_new_no_cine_studies": no_cine_semantics[
            "n_new_no_cine_studies"
        ],
        "technical_disposition_counts_by_class": extraction_semantics[
            "technical_disposition_counts_by_class"
        ],
        "technical_disposition_policy_version": (
            production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "prespecified_no_cine_study_set_sha256": no_cine_semantics[
            "prespecified_no_cine_study_set_sha256"
        ],
        "all_no_cine_studies_prespecified": no_cine_semantics[
            "all_no_cine_studies_prespecified"
        ],
        "n_unique_clip_keys": extraction_semantics["n_extracted_clips"],
        "n_clip_embeddings": embedding_array_semantics["n_clip_embeddings"],
        "n_pooled_studies": embedding_array_semantics["n_study_embeddings"],
        "n_no_cine_studies": pooling_semantics["no_cine_studies"],
        "no_cine_disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE" if pooling_semantics["no_cine_studies"] else "NONE",
        "n_outside_selected_studies": outside,
        "n_missing_selected_studies": missing,
        "n_duplicate_physical_sources": duplicate_source,
        "n_duplicate_clip_keys": duplicate_clip,
        "n_nonfinite_embeddings": nonfinite,
        "n_wrong_dimension_embeddings": wrong_dimension,
        "source_receipt_sha256": sha256_file(paths["download_ledger"]),
        "dicom_audit_sha256": sha256_file(paths["dicom_audit"]),
        "extraction_manifest_sha256": sha256_file(paths["extraction_manifest"]),
        "technical_disposition_manifest_sha256": production_stages.technical_disposition_manifest_sha256(
            paths["technical_disposition_manifest"]
        ),
        "clip_manifest_sha256": sha256_file(paths["clip_manifest"]),
        "clip_embeddings_sha256": sha256_file(paths["clip_embeddings"]),
        "study_manifest_sha256": sha256_file(paths["study_manifest"]),
        "study_embeddings_sha256": sha256_file(paths["study_embeddings"]),
        "preservation_manifest_sha256": sha256_file(manifest_path),
        "source_gate_passed": True,
        "download_gate_passed": True,
        "dicom_audit_gate_passed": True,
        "extraction_gate_passed": True,
        "embedding_gate_passed": True,
        "pooling_gate_passed": True,
        "study_pooling_semantics_gate_passed": True,
        "preservation_gate_passed": True,
        "aggregate_safety_gate_passed": True,
        "aggregate_safety_gate_result": "PASS",
        "all_extraction_rows_resolved": extraction_semantics[
            "all_extraction_rows_resolved"
        ],
        "all_successful_extractions_embedded": (
            embedding_array_semantics["n_clip_embeddings"]
            == extraction_semantics["n_successfully_extracted_cines"]
        ),
        "all_technical_dispositions_retained": extraction_semantics[
            "all_technical_dispositions_retained"
        ],
        "object_substitution_count": extraction_semantics[
            "object_substitution_count"
        ],
        "unaccounted_multiframe_objects": extraction_semantics[
            "unaccounted_multiframe_objects"
        ],
        "raw_dicoms_retained": True,
        "extracted_cache_retired": False,
    }
    validate_preservation_disposition_accounting(receipt)
    receipt_body = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if receipt_path.exists() or receipt_path.is_symlink():
        existing_receipt = load_json(receipt_path, "PRESERVATION_RECEIPT")
        expected_recovery = dict(receipt)
        expected_recovery["scheduler_job_identity"] = existing_receipt.get(
            "scheduler_job_identity"
        )
        if existing_receipt != expected_recovery:
            raise BatchPreservationError("PRESERVATION_RECEIPT_RECOVERY_MISMATCH")
        receipt = existing_receipt
        validate_preservation_disposition_accounting(receipt)
        receipt_body = receipt_path.read_bytes()
    else:
        write_bytes_no_clobber(receipt_path, receipt_body)
    receipt_body_sha = hashlib.sha256(receipt_body).hexdigest()
    transition_root = output_root / "transition_receipts"
    if transition_root.is_symlink() or (transition_root.exists() and not transition_root.is_dir()):
        raise BatchPreservationError("PRESERVATION_TRANSITION_ROOT_INVALID")
    transition_root.mkdir(mode=0o700, exist_ok=True)
    updated = ledger
    for target, output_sha in (
        ("PRESERVATION_COMPLETE", receipt["preservation_manifest_sha256"]),
        ("CACHE_RETIREMENT_ELIGIBLE", receipt_body_sha),
    ):
        current = updated["batches"][batch_id]
        predecessor = current["events"][-1]["receipt_sha256"]
        transition = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": updated["attempt_id"],
            "batch_id": batch_id,
            "from_state": current["state"],
            "to_state": target,
            "status": "PASS",
            "authority": updated["authority"],
            "input_receipt_sha256": [predecessor],
            "output_manifest_sha256": output_sha,
        }
        updated = core.apply_transition(updated, transition)
        transition_path = transition_root / f"{target.lower()}.restricted.json"
        if transition_path.exists() or transition_path.is_symlink():
            if core.load_strict_json(transition_path) != transition:
                raise BatchPreservationError("PRESERVATION_TRANSITION_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                transition_path, transition, attempt_id=attempt_id
            )
    eligibility_ledger_path = batch_root / "cache_retirement_eligible_resume_ledger.restricted.json"
    if eligibility_ledger_path.exists() or eligibility_ledger_path.is_symlink():
        if core.load_strict_json(eligibility_ledger_path) != updated:
            raise BatchPreservationError("PRESERVATION_LEDGER_RECOVERY_MISMATCH")
    else:
        core.atomic_write_json_no_clobber(
            eligibility_ledger_path, updated, attempt_id=attempt_id
        )
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--scheduler-job-identity", required=True)
    parser.add_argument("--input-ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        preserve_batch(
            contract_path=args.contract, plan_path=args.plan, batch_id=args.batch_id,
            attempt_id=args.attempt_id, governing_commit=args.governing_commit,
            production_root=args.production_root, output_root=args.output_root,
            environment_receipt=args.environment_receipt, checkpoint=args.checkpoint,
            scheduler_job_identity=args.scheduler_job_identity,
            input_ledger=args.input_ledger,
        )
    except BatchPreservationError as exc:
        print(f"C3_BATCH_PRESERVATION=BLOCKED_{exc.code}")
        if exc.validation_substage is not None:
            print(
                "C3_BATCH_PRESERVATION_SUBSTAGE="
                f"{exc.validation_substage.value}"
            )
        return 78
    except Exception:
        print("C3_BATCH_PRESERVATION=BLOCKED_UNEXPECTED_SANITIZED_EXCEPTION")
        return 78
    print("C3_BATCH_PRESERVATION=PASS_CACHE_RETIREMENT_ELIGIBLE")
    print("RAW_DICOM_DELETION=NOT_PERFORMED")
    print("EXTRACTED_CACHE_RETIREMENT=NOT_PERFORMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
