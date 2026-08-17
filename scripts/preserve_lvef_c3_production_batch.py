#!/usr/bin/env python3
"""Create and second-pass verify one restricted C3 batch preservation receipt."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, Sequence

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
class BatchPreservationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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


def _strict_csv_bool(value: str, *, code: str) -> bool:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    raise BatchPreservationError(code)


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
        extraction_semantics = production_stages.validate_production_extraction_rows(
            typed_extraction_rows,
            expected_cines=dicom_semantics["n_multiframe_candidates"],
            disposition_context=disposition_context,
            clips_root=clips_root,
        )
        technical_disposition_semantics = (
            production_stages.validate_technical_disposition_manifest_rows(
                typed_extraction_rows,
                technical_disposition_rows,
                context=disposition_context,
            )
        )
    except production_stages.ProductionStageError as exc:
        raise BatchPreservationError("EXTRACTION_RECOMPUTATION_FAILED") from exc

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
) -> dict[str, Any]:
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
    records: list[dict[str, Any]] = []
    records.extend(
        _artifact_record(path, production_root, "raw_dicom_and_download_authority")
        for path in _walk_regular(raw_root)
    )
    extraction_root = cache_batch_root / "dicom_extraction"
    clip_cache_root = extraction_root / "clips"
    for path in _walk_regular(extraction_root):
        role = (
            "extracted_npz_cache_owner_retirable"
            if path.is_relative_to(clip_cache_root)
            else "dicom_extraction_metadata_retained"
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
    download_rows = read_csv_exact(
        paths["download_manifest"],
        ["subject_id", "study_id", "source_relative_path", "download_ok", "observed_sha256", "physical_source_key"],
    )
    if len(download_rows) != batch["n_objects"] or any(row["download_ok"] != "true" for row in download_rows):
        raise BatchPreservationError("DOWNLOAD_MANIFEST_COUNT_OR_STATUS_MISMATCH")
    expected_objects = {row["source_object_key"]: row for row in batch["objects"]}
    if {row["physical_source_key"] for row in download_rows} != set(expected_objects):
        raise BatchPreservationError("DOWNLOAD_MANIFEST_MEMBERSHIP_MISMATCH")
    for row in download_rows:
        expected = expected_objects[row["physical_source_key"]]
        if row["subject_id"] != expected["subject_id"] or row["study_id"] != expected["study_id"]:
            raise BatchPreservationError("DOWNLOAD_MANIFEST_OWNERSHIP_MISMATCH")
        raw = raw_root / "objects" / f"{row['physical_source_key']}.dcm"
        if raw.is_symlink() or not raw.is_file() or raw.stat().st_size != expected["size_bytes"]:
            raise BatchPreservationError("RAW_DICOM_RETENTION_GATE_FAILED")
        if sha256_file(raw) != row["observed_sha256"]:
            raise BatchPreservationError("RAW_DICOM_HASH_MISMATCH")
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
    )
    dicom_semantics = stage_csvs["dicom_semantics"]
    extraction_semantics = stage_csvs["extraction_semantics"]
    technical_disposition_semantics = stage_csvs[
        "technical_disposition_semantics"
    ]
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
        technical_manifest_path=paths["technical_disposition_manifest"],
    )
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
    with np.load(paths["clip_embeddings"], allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"}:
            raise BatchPreservationError("CLIP_EMBEDDING_NPZ_SCHEMA_MISMATCH")
        clip_array = archive["embeddings"]
    with np.load(paths["study_embeddings"], allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"}:
            raise BatchPreservationError("STUDY_EMBEDDING_NPZ_SCHEMA_MISMATCH")
        study_array = archive["embeddings"]
    embedding_array_semantics = validate_embedding_array_authority(
        clip_array=clip_array,
        study_array=study_array,
        clip_rows=stage_csvs["clip_rows"],
        study_rows=stage_csvs["study_rows"],
        embedding_summary=embedding,
    )
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
        raise BatchPreservationError("ACTUAL_EMBEDDING_OR_COHORT_GATE_FAILED")
    import lvef_reconstruction_smoke as smoke
    clip_vector_hashes = [smoke.array_content_sha256(row) for row in clip_array]
    study_vector_hashes = [smoke.array_content_sha256(row) for row in study_array]
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
    if not np.array_equal(expected_study_array, study_array):
        raise BatchPreservationError("STUDY_POOLING_RECOMPUTATION_MISMATCH")
    if (
        pooling_semantics["eligible_studies"] != embedding["n_pooled_studies"]
        or pooling_semantics["no_cine_studies"] != embedding["n_no_cine_studies"]
    ):
        raise BatchPreservationError("POOLING_SUMMARY_SEMANTICS_MISMATCH")

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
    # Independent second pass: reload manifest and rehash every listed artifact.
    verified = read_csv_exact(manifest_path, MANIFEST_HEADER, delimiter="\t")
    for item in verified:
        artifact = production_root / item["relative_path"]
        try:
            size_bytes, digest = stable_manifest_artifact_authority(artifact)
        except BatchPreservationError as exc:
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
