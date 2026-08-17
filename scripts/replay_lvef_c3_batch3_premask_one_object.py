#!/usr/bin/env python3
"""Run the bounded R4D2 one-object premask diagnostic.

This entrypoint is deliberately diagnostic-only.  It has no cloud, qsub,
EchoPrime, GPU, embedding, model, prediction, or performance interface.  The
preflight reads metadata authorities but no retained DICOM body.  Execution
reads one retained DICOM once, decodes its pixels once, mirrors the frozen
strict mask, and publishes two aggregate-only JSON receipts in a fresh private
root.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

import numpy as np


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

import finalize_lvef_c3_production as finalizer
import lvef_c3_full_scheduler as scheduler
import lvef_c3_full_sequential as full_sequential
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as production_stages
import lvef_reconstruction_smoke as reconstruction
import preserve_lvef_c3_production_batch as preservation
import replay_lvef_c3_failed_extraction_one_object as r3e


STARTING_COMMIT = "c11e1313999880881eb67f0269820361638fc8dc"
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
ATTEMPT_ID = "lvef_c3_full_38750555923b547c_c11e1313"
BATCH_ID = "c3_batch_002"
ARRAY_JOB_ID = "7191243"
FINALIZER_JOB_ID = "7191244"
PLAN_SHA256 = "38750555923b547c4d5d42b701ced8d5611b1adedc745f2eaeb629a28f2ba0fb"
PLAN_SHA256_PREFIX = PLAN_SHA256[:16]
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
ALLOWED_DIAGNOSTIC_PREFIX = PRODUCTION_ROOT / "owner_private"
CONTRACT_PATH = REPOSITORY_ROOT / "configs/lvef_c3_orchestration_v2.yaml"

FROZEN_FILE_COUNT = 233_257
FROZEN_DIRECTORY_COUNT = 300
FROZEN_TOTAL_BYTES = 221_586_476_241
FROZEN_METADATA_STAT_SHA256 = (
    "c820806c26ba9644061e7d1c92e79de48d69b7b91fa3d0d09763d028284fc4c6"
)
FROZEN_KIND_MODE_HISTOGRAM = (
    ("directory", 0o2700, 300),
    ("file", 0o600, 233_237),
    ("file", 0o644, 20),
)
FROZEN_EXCEPTION_HISTOGRAM = (("file", 0o644, "scheduler_log", 20),)

BATCH_1_RECEIPT = (
    "c3_batch_000",
    3_926,
    "4fbbb0fc516caaae0de7b641aeef6b21ba6879fb5f292021fb24055625c04cd9",
    "7191243.1",
)
BATCH_2_RECEIPT = (
    "c3_batch_001",
    3_922,
    "a92584984ab4141e49c4f2ab42ae67a9d4179a6f7ccfe1264085ac62db70036b",
    "7191243.2",
)
FROZEN_BATCH3_ARTIFACTS = {
    "failure.summary.json": (
        5_153,
        "b20258c834e1e05ba17225ccbac2866ca3e74a6e3742947832b64aabdf1e4f23",
    ),
    "dicom_audit.restricted.csv": (
        4_524_264,
        "c256d5ec028b6c1c303f121d335c95525b4263472bebfb5661f9e19361761b12",
    ),
    "extraction_manifest.restricted.csv": (
        11_307_655,
        "b49411963a1bc468223bcce026ce2164b2aea8d2514f9db59afa764fbc19e1d8",
    ),
}

EXPECTED_DOWNLOAD_ROWS = 18_653
EXPECTED_EXTRACTION_ROWS = 10_257
EXPECTED_SUCCESSFUL_EXTRACTIONS = 10_256
EXPECTED_AFFECTED_STUDY_ORDINARY_CINES = 38
EXPECTED_SOURCE_FRAMES = 9
MAXIMUM_AUTHORITY_BYTES = 512 * 1024 * 1024
MAXIMUM_DICOM_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_JSON_BYTES = 128 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
SAFE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
DIAGNOSTIC_NAME_RE = re.compile(
    r"^lvef_c3_r4d2_premask_[0-9a-f]{16}$"
)

PREMASK_CLASSES = frozenset(
    {
        "PREMASK_BLANK",
        "PREMASK_NONBLANK_STATIC",
        "DYNAMIC_DECODE_MASK_COLLAPSE",
        "PERSISTED_SOURCE_FAILURE_CONTRADICTED",
        "MASK_CONSTRUCTION_EXCEPTION",
        "REPLAY_EVIDENCE_CONTRADICTORY",
    }
)
MASK_MECHANISMS = frozenset(
    {
        "PERSISTENT_OCCUPANCY_ERODED_TO_ZERO",
        "FIRST_LAST_DIFFERENCE_ZERO",
        "OVERLAP_EMPTY",
        "FLOODFILL_OR_CONTOUR_TOPOLOGY_EMPTY",
        "POSTMASK_ZERO_SIGNAL",
        "POSTMASK_ZERO_VARIATION",
        "MULTIPLE_MASK_COLLAPSE_MECHANISMS",
        "MECHANISM_NOT_RESOLVED",
        "NOT_APPLICABLE",
    }
)
NEXT_ACTIONS = frozenset(
    {
        "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION",
        "MASK_IMPLEMENTATION_REPAIR_REVIEW",
        "READ_ONLY_AUTHORITY_RECONCILIATION",
    }
)

SOURCE_GATE_FIELDS = (
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
DOWNSTREAM_GATE_FIELDS = (
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

CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT = "dicom_audit"
CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST = "extraction_manifest"
CSV_BOOLEAN_TWO_STATE = "TWO_STATE"
CSV_BOOLEAN_TRI_STATE = "TRI_STATE"
CSV_BOOLEAN_STATE_TRUE = "TRUE"
CSV_BOOLEAN_STATE_FALSE = "FALSE"
CSV_BOOLEAN_STATE_NOT_EVALUATED = "NOT_EVALUATED"
FROZEN_CSV_BOOLEAN_DIALECT = "TITLECASE_TRUE_FALSE_UNQUOTED_V1"
FROZEN_CSV_BOOLEAN_DIALECT_CLASS = "BOOLEAN_FIELD_ROUTING_DEFECT"
_CSV_BOOLEAN_COLUMN_MISSING = object()

OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "execution_commit",
        "source_frame_count",
        "decode_color_status",
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
        "pre_mask_nonzero_pixel_count",
        "pre_mask_adjacent_temporal_variation_pixel_count",
        "first_last_difference_pixel_count",
        "persistent_occupancy_pixel_count",
        "overlap_before_floodfill_pixel_count",
        "generated_sector_pixel_count",
        "post_mask_nonzero_pixel_count",
        "post_mask_adjacent_temporal_variation_pixel_count",
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "mask_mirror_equals_frozen_helper",
        "production_mask_exception_class",
        "contradiction_kind",
        "premask_replay_class",
        "mask_collapse_mechanism",
        "selected_next_action",
        "identifiers_emitted",
        "locators_emitted",
        "paths_emitted",
    }
)
AGGREGATE_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "starting_commit",
        "execution_commit",
        "r4_attempt_inventory_before_sha256",
        "r4_attempt_inventory_after_sha256",
        "r4_attempt_preserved_immutable",
        "batch_1_terminal_receipt_unchanged",
        "batch_2_terminal_receipt_unchanged",
        "batch_3_authorities_unchanged",
        "replay_input_authority",
        "historical_file_device_authority",
        "current_mount_authority",
        "unique_dicom_objects_accessed",
        "local_dicom_read_passes",
        "pydicom_decode_invocations",
        "source_frame_count",
        "decode_color_status",
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
        "pre_mask_nonzero_pixel_count",
        "pre_mask_adjacent_temporal_variation_pixel_count",
        "first_last_difference_pixel_count",
        "persistent_occupancy_pixel_count",
        "overlap_before_floodfill_pixel_count",
        "generated_sector_pixel_count",
        "post_mask_nonzero_pixel_count",
        "post_mask_adjacent_temporal_variation_pixel_count",
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "mask_mirror_equals_frozen_helper",
        "production_mask_exception_class",
        "contradiction_kind",
        "premask_replay_class",
        "mask_collapse_mechanism",
        "affected_study_valid_cines",
        "affected_study_imaging_coverage",
        "source_object_substitution_required",
        "whole_batch_zero_failure_policy",
        "selected_next_action",
        "observation_receipt_basename",
        "observation_receipt_bytes",
        "observation_receipt_sha256",
        "diagnostic_npz_created",
        "cloud_requests",
        "object_downloads",
        "qsub_submissions",
        "gpu_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accessed",
        "identifiers_emitted",
        "locators_emitted",
        "paths_emitted",
    }
)


class PremaskReplayError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        unique_objects: int = 0,
        local_read_passes: int = 0,
        decode_invocations: int = 0,
        diagnostic_root_created: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.unique_objects = unique_objects
        self.local_read_passes = local_read_passes
        self.decode_invocations = decode_invocations
        self.diagnostic_root_created = diagnostic_root_created


def _fail(code: str) -> None:
    if SAFE_CODE_RE.fullmatch(code) is None:
        code = "R4D2_INTERNAL_SAFE_CODE_INVALID"
    raise PremaskReplayError(code)


@dataclass(frozen=True)
class ClosedCsvBooleanField:
    artifact_role: str
    field_name: str
    semantic_type: str
    frozen_dialect: str
    raw_token_histogram: tuple[tuple[str, int], ...]


FROZEN_CSV_BOOLEAN_FIELDS = (
    ClosedCsvBooleanField(
        CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
        "read_ok",
        CSV_BOOLEAN_TWO_STATE,
        FROZEN_CSV_BOOLEAN_DIALECT,
        (("True", 18_653),),
    ),
    ClosedCsvBooleanField(
        CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
        "is_multiframe",
        CSV_BOOLEAN_TWO_STATE,
        FROZEN_CSV_BOOLEAN_DIALECT,
        (("False", 8_396), ("True", 10_257)),
    ),
    ClosedCsvBooleanField(
        CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
        "pixel_decode_ok",
        CSV_BOOLEAN_TWO_STATE,
        FROZEN_CSV_BOOLEAN_DIALECT,
        (("False", 8_396), ("True", 10_257)),
    ),
    ClosedCsvBooleanField(
        CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
        "write_ok",
        CSV_BOOLEAN_TWO_STATE,
        FROZEN_CSV_BOOLEAN_DIALECT,
        (("False", 1), ("True", 10_256)),
    ),
    *(
        ClosedCsvBooleanField(
            CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
            gate_field,
            CSV_BOOLEAN_TWO_STATE,
            FROZEN_CSV_BOOLEAN_DIALECT,
            (("False", 1), ("True", 10_256)),
        )
        for _, gate_field in (*SOURCE_GATE_FIELDS, *DOWNSTREAM_GATE_FIELDS)
    ),
    ClosedCsvBooleanField(
        CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
        "pixel_decode_ok",
        CSV_BOOLEAN_TWO_STATE,
        FROZEN_CSV_BOOLEAN_DIALECT,
        (("True", 10_257),),
    ),
)


@dataclass(frozen=True)
class R4AttemptAuthority:
    attempt_id: str = ATTEMPT_ID
    execution_commit: str = STARTING_COMMIT
    batch_id: str = BATCH_ID
    file_count: int = FROZEN_FILE_COUNT
    directory_count: int = FROZEN_DIRECTORY_COUNT
    total_bytes: int = FROZEN_TOTAL_BYTES
    metadata_stat_sha256: str = FROZEN_METADATA_STAT_SHA256
    extraction_rows: int = EXPECTED_EXTRACTION_ROWS
    successful_extraction_rows: int = EXPECTED_SUCCESSFUL_EXTRACTIONS
    download_rows: int = EXPECTED_DOWNLOAD_ROWS
    batch_plan_sha256_prefix: str = PLAN_SHA256_PREFIX
    root_mode: int = 0o2700


R4_AUTHORITY = R4AttemptAuthority()


@dataclass(frozen=True)
class R4Inventory:
    file_count: int
    directory_count: int
    total_bytes: int
    symlink_count: int
    nonregular_count: int
    owner_mismatch_count: int
    cross_device_count: int
    identity_instability_count: int
    group_other_write_count: int
    special_bit_anomaly_count: int
    regular_nlink_anomaly_count: int
    duplicate_inode_count: int
    sensitive_exception_count: int
    kind_mode_histogram: tuple[tuple[str, int, int], ...]
    exceptional_role_histogram: tuple[tuple[str, int, str, int], ...]
    metadata_stat_sha256: str
    root_identity: r3e.LegacyRootIdentity


@dataclass(frozen=True)
class PremaskReplayPreflight:
    execution_commit: str
    attempt_root: Path
    inventory: R4Inventory
    failed_row: Mapping[str, str]
    planned_object: Mapping[str, Any]
    source_path: Path
    source_identity: r3e.SourceFileIdentity
    root_identity: r3e.LegacyRootIdentity
    mount_authority: r3e.CurrentMountAuthority
    local_sha256: str
    diagnostic_root: Path
    diagnostic_parent: r3e.PrivateDirectoryIdentity
    artifact_digests: Mapping[str, tuple[int, str]]


@dataclass
class AccessCounters:
    allowed_source: Path
    accessed: set[Path] = field(default_factory=set)
    local_read_passes: int = 0
    decode_invocations: int = 0

    def register_read(self, path: Path) -> None:
        observed = Path(os.path.abspath(path))
        if observed != self.allowed_source or self.local_read_passes >= 1:
            _fail("R4D2_ONE_OBJECT_READ_SCOPE_EXCEEDED")
        self.accessed.add(observed)
        self.local_read_passes += 1

    def register_decode(self, path: Path) -> None:
        observed = Path(os.path.abspath(path))
        if observed != self.allowed_source or self.decode_invocations >= 1:
            _fail("R4D2_ONE_OBJECT_DECODE_SCOPE_EXCEEDED")
        self.accessed.add(observed)
        self.decode_invocations += 1

    @property
    def unique_objects(self) -> int:
        return len(self.accessed)


def _translate(error: BaseException, fallback: str) -> PremaskReplayError:
    if isinstance(error, PremaskReplayError):
        return error
    code = getattr(error, "code", fallback)
    if not isinstance(code, str) or SAFE_CODE_RE.fullmatch(code) is None:
        code = fallback
    return PremaskReplayError(code)


def _closed_csv_boolean_field(
    artifact_role: str, field_name: str
) -> ClosedCsvBooleanField:
    matches = tuple(
        item
        for item in FROZEN_CSV_BOOLEAN_FIELDS
        if item.artifact_role == artifact_role and item.field_name == field_name
    )
    if len(matches) != 1:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    return matches[0]


def parse_closed_csv_boolean(
    raw_value: object,
    *,
    artifact_role: str,
    field_name: str,
    semantic_type: str,
    frozen_dialect: str,
) -> str:
    """Parse one frozen CSV Boolean without normalization or coercion."""

    specification = _closed_csv_boolean_field(artifact_role, field_name)
    if (
        semantic_type != specification.semantic_type
        or frozen_dialect != specification.frozen_dialect
    ):
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    if raw_value is _CSV_BOOLEAN_COLUMN_MISSING:
        _fail("R4D2_CSV_BOOLEAN_COLUMN_MISSING")
    if type(raw_value) is not str:
        _fail("R4D2_CSV_BOOLEAN_TOKEN_INVALID")
    if raw_value == "":
        if semantic_type == CSV_BOOLEAN_TWO_STATE:
            _fail("R4D2_CSV_BOOLEAN_REQUIRED_EMPTY")
        return CSV_BOOLEAN_STATE_NOT_EVALUATED
    if raw_value[:1].isspace() or raw_value[-1:].isspace():
        _fail("R4D2_CSV_BOOLEAN_WHITESPACE_INVALID")
    if raw_value == "True":
        return CSV_BOOLEAN_STATE_TRUE
    if raw_value == "False":
        return CSV_BOOLEAN_STATE_FALSE
    if raw_value == "NOT_EVALUATED":
        if semantic_type != CSV_BOOLEAN_TRI_STATE:
            _fail("R4D2_CSV_BOOLEAN_NOT_EVALUATED_INVALID_FOR_FIELD")
        return CSV_BOOLEAN_STATE_NOT_EVALUATED
    if raw_value in ("true", "false"):
        _fail("R4D2_CSV_BOOLEAN_DIALECT_MISMATCH")
    _fail("R4D2_CSV_BOOLEAN_TOKEN_INVALID")


def _parse_csv_boolean_field(
    row: Mapping[str, str], *, artifact_role: str, field_name: str
) -> str:
    specification = _closed_csv_boolean_field(artifact_role, field_name)
    raw_value: object = (
        row[field_name]
        if field_name in row and row[field_name] is not None
        else _CSV_BOOLEAN_COLUMN_MISSING
    )
    return parse_closed_csv_boolean(
        raw_value,
        artifact_role=artifact_role,
        field_name=field_name,
        semantic_type=specification.semantic_type,
        frozen_dialect=specification.frozen_dialect,
    )


def _validate_frozen_csv_boolean_aggregate(
    rows: Sequence[Mapping[str, str]], *, artifact_role: str
) -> tuple[
    tuple[str, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]], ...
]:
    fields = tuple(
        item
        for item in FROZEN_CSV_BOOLEAN_FIELDS
        if item.artifact_role == artifact_role
    )
    if not fields:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    raw_histograms = {item: Counter() for item in fields}
    state_histograms = {item: Counter() for item in fields}
    for row in rows:
        for specification in fields:
            raw_value: object = (
                row[specification.field_name]
                if specification.field_name in row
                and row[specification.field_name] is not None
                else _CSV_BOOLEAN_COLUMN_MISSING
            )
            state = parse_closed_csv_boolean(
                raw_value,
                artifact_role=artifact_role,
                field_name=specification.field_name,
                semantic_type=specification.semantic_type,
                frozen_dialect=specification.frozen_dialect,
            )
            raw_histograms[specification][raw_value] += 1
            state_histograms[specification][state] += 1
    observed = []
    for specification in fields:
        raw_histogram = raw_histograms[specification]
        state_histogram = state_histograms[specification]
        if raw_histogram != Counter(dict(specification.raw_token_histogram)):
            _fail("R4D2_CSV_BOOLEAN_DIALECT_MISMATCH")
        observed.append(
            (
                specification.field_name,
                tuple(sorted(raw_histogram.items())),
                tuple(sorted(state_histogram.items())),
            )
        )
    return tuple(observed)


def _csv_nonnegative_integer(value: object) -> int | None:
    text = str(value).strip()
    if text == "":
        return None
    match = re.fullmatch(r"(?:0|[1-9][0-9]*)(?:[.]0)?", text)
    if match is None:
        _fail("R4D2_CSV_INTEGER_INVALID")
    return int(text.split(".", 1)[0])


def _safe_relative(value: object) -> str:
    try:
        return r3e._safe_relative(value)
    except Exception as exc:
        raise _translate(exc, "R4D2_RELATIVE_AUTHORITY_INVALID") from exc


def _stable_private_payload(
    path: Path, *, expected_size: int | None = None, expected_sha256: str | None = None
) -> tuple[bytes, str]:
    try:
        payload = r3e._read_owner_private_file(
            path, maximum_bytes=MAXIMUM_AUTHORITY_BYTES
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_PRIVATE_AUTHORITY_INVALID") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if expected_size is not None and len(payload) != expected_size:
        _fail("R4D2_FROZEN_ARTIFACT_SIZE_MISMATCH")
    if expected_sha256 is not None and digest != expected_sha256:
        _fail("R4D2_FROZEN_ARTIFACT_SHA256_MISMATCH")
    return payload, digest


def _strict_json(payload: bytes) -> dict[str, Any]:
    try:
        return r3e._decode_json_object(payload)
    except Exception as exc:
        raise _translate(exc, "R4D2_JSON_AUTHORITY_INVALID") from exc


def _strict_csv(
    payload: bytes, header: Sequence[str], *, artifact_role: str
) -> list[dict[str, str]]:
    if artifact_role == CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT:
        governed_header = tuple(preservation.DICOM_AUDIT_HEADER)
    elif artifact_role == CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST:
        governed_header = tuple(preservation.EXTRACTION_MANIFEST_HEADER)
    else:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    if tuple(header) != governed_header:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    try:
        decoded = payload.decode("utf-8")
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        observed_header = tuple(next(reader))
    except Exception as exc:
        raise _translate(exc, "R4D2_CSV_AUTHORITY_INVALID") from exc
    header_histogram = Counter(observed_header)
    if any(count != 1 for count in header_histogram.values()):
        _fail("R4D2_CSV_BOOLEAN_COLUMN_DUPLICATED")
    boolean_fields = {
        item.field_name
        for item in FROZEN_CSV_BOOLEAN_FIELDS
        if item.artifact_role == artifact_role
    }
    unexpected_boolean_fields = {
        field_name
        for field_name in observed_header
        if field_name not in boolean_fields
        and (
            field_name.endswith("_gate_passed")
            or field_name.endswith("_ok")
            or field_name == "is_multiframe"
        )
    } - set(governed_header)
    if unexpected_boolean_fields:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    if any(field_name not in observed_header for field_name in boolean_fields):
        _fail("R4D2_CSV_BOOLEAN_COLUMN_MISSING")
    if observed_header != governed_header:
        _fail("R4D2_CSV_BOOLEAN_FIELD_SCHEMA_INVALID")
    try:
        return r3e._decode_csv_exact(payload, header)
    except Exception as exc:
        raise _translate(exc, "R4D2_CSV_AUTHORITY_INVALID") from exc


def _scheduler_log_role(relative: str, kind: str) -> str:
    path = PurePosixPath(relative)
    name = path.name.lower()
    parts = tuple(part.lower() for part in path.parts)
    if kind == "file" and (
        "receipt" in name
        or "manifest" in name
        or "ledger" in name
        or re.search(
            r"credential|token|requester[-_]?pays|oauth|service[-_]?account|"
            r"application[-_]?default|secret|access[-_]?key",
            relative,
            re.IGNORECASE,
        )
    ):
        return "sensitive_authority"
    if (
        kind == "file"
        and len(parts) == 2
        and parts[0] == "scheduler"
        and re.fullmatch(r".+[.]o[0-9]+(?:[.][0-9]+)?", name)
    ):
        return "scheduler_log"
    return "other"


def r4_metadata_inventory(attempt_root: Path) -> R4Inventory:
    """Reproduce the exact owner-frozen R4T tuple/list metadata preimage."""

    try:
        r3e._require_no_symlink_components(attempt_root)
        root_before = os.lstat(attempt_root)
    except Exception as exc:
        raise _translate(exc, "R4D2_ATTEMPT_ROOT_INVALID") from exc
    digest = hashlib.sha256()
    files = 0
    directories_seen = 0
    total_bytes = 0
    symlinks = 0
    nonregular = 0
    owners = 0
    cross_device = 0
    unstable = 0
    group_other_write = 0
    special = 0
    nlink_bad = 0
    duplicate = 0
    sensitive = 0
    histogram: Counter[tuple[str, int]] = Counter()
    exceptions: Counter[tuple[str, int, str]] = Counter()
    seen: set[tuple[int, int]] = set()

    def identity(item: os.stat_result) -> tuple[int, ...]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_uid,
            item.st_gid,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    def observe(path: Path, relative: str) -> None:
        nonlocal files, directories_seen, total_bytes, symlinks, nonregular
        nonlocal owners, cross_device, unstable, group_other_write, special
        nonlocal nlink_bad, duplicate, sensitive
        try:
            first = os.lstat(path)
            second = os.lstat(path)
        except OSError as exc:
            raise PremaskReplayError("R4D2_ATTEMPT_METADATA_SCAN_INVALID") from exc
        if identity(first) != identity(second):
            unstable += 1
        mode = stat.S_IMODE(first.st_mode)
        if stat.S_ISDIR(first.st_mode):
            kind = "directory"
            directories_seen += 1
        elif stat.S_ISREG(first.st_mode):
            kind = "file"
            files += 1
            total_bytes += int(first.st_size)
        elif stat.S_ISLNK(first.st_mode):
            kind = "symlink"
            symlinks += 1
        else:
            kind = "nonregular"
            nonregular += 1
        if first.st_uid != os.geteuid():
            owners += 1
        if first.st_dev != root_before.st_dev:
            cross_device += 1
        if kind in {"directory", "file"}:
            inode = (int(first.st_dev), int(first.st_ino))
            if inode in seen:
                duplicate += 1
            seen.add(inode)
        if kind == "file" and first.st_nlink != 1:
            nlink_bad += 1
        if mode & 0o022:
            group_other_write += 1
        if mode & 0o5000 or (mode & 0o2000 and kind != "directory"):
            special += 1
        histogram[(kind, mode)] += 1
        if mode & 0o077:
            role = _scheduler_log_role(relative, kind)
            exceptions[(kind, mode, role)] += 1
            if role != "scheduler_log":
                sensitive += 1
        # This tuple order and JSON list encoding are the recovered R4T
        # authority.  Do not replace it with the R3E dict-shaped snapshot.
        record = (
            relative,
            kind,
            mode,
            first.st_uid,
            first.st_gid,
            first.st_dev,
            first.st_ino,
            first.st_nlink,
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        )
        digest.update(
            json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode()
            + b"\n"
        )

    observe(attempt_root, ".")

    def walk_error(_error: OSError) -> None:
        _fail("R4D2_ATTEMPT_METADATA_SCAN_INVALID")

    for current_text, child_directories, child_files in os.walk(
        attempt_root, topdown=True, followlinks=False, onerror=walk_error
    ):
        child_directories.sort()
        child_files.sort()
        current = Path(current_text)
        for name in child_directories + child_files:
            child = current / name
            observe(child, child.relative_to(attempt_root).as_posix())
    try:
        root_after = os.lstat(attempt_root)
    except OSError as exc:
        raise PremaskReplayError("R4D2_ATTEMPT_ROOT_INVALID") from exc
    if identity(root_before) != identity(root_after):
        unstable += 1
    root_identity = r3e.LegacyRootIdentity(
        path=attempt_root,
        device=int(root_before.st_dev),
        inode=int(root_before.st_ino),
        group=int(root_before.st_gid),
        mode=stat.S_IMODE(root_before.st_mode),
        nlink=int(root_before.st_nlink),
        size=int(root_before.st_size),
        mtime_ns=int(root_before.st_mtime_ns),
        ctime_ns=int(root_before.st_ctime_ns),
    )
    return R4Inventory(
        file_count=files,
        directory_count=directories_seen,
        total_bytes=total_bytes,
        symlink_count=symlinks,
        nonregular_count=nonregular,
        owner_mismatch_count=owners,
        cross_device_count=cross_device,
        identity_instability_count=unstable,
        group_other_write_count=group_other_write,
        special_bit_anomaly_count=special,
        regular_nlink_anomaly_count=nlink_bad,
        duplicate_inode_count=duplicate,
        sensitive_exception_count=sensitive,
        kind_mode_histogram=tuple(
            (kind, mode, count)
            for (kind, mode), count in sorted(histogram.items())
        ),
        exceptional_role_histogram=tuple(
            (kind, mode, role, count)
            for (kind, mode, role), count in sorted(exceptions.items())
        ),
        metadata_stat_sha256=digest.hexdigest(),
        root_identity=root_identity,
    )


def validate_r4_inventory(
    observed: R4Inventory, authority: R4AttemptAuthority = R4_AUTHORITY
) -> None:
    if (
        observed.file_count != authority.file_count
        or observed.directory_count != authority.directory_count
        or observed.total_bytes != authority.total_bytes
        or observed.metadata_stat_sha256 != authority.metadata_stat_sha256
        or observed.root_identity.mode != authority.root_mode
        or observed.kind_mode_histogram != FROZEN_KIND_MODE_HISTOGRAM
        or observed.exceptional_role_histogram != FROZEN_EXCEPTION_HISTOGRAM
    ):
        _fail("R4D2_FROZEN_ATTEMPT_INVENTORY_MISMATCH")
    anomaly_values = (
        observed.symlink_count,
        observed.nonregular_count,
        observed.owner_mismatch_count,
        observed.cross_device_count,
        observed.identity_instability_count,
        observed.group_other_write_count,
        observed.special_bit_anomaly_count,
        observed.regular_nlink_anomaly_count,
        observed.duplicate_inode_count,
        observed.sensitive_exception_count,
    )
    if any(anomaly_values):
        _fail("R4D2_FROZEN_ATTEMPT_TOPOLOGY_INVALID")


def _validate_git(repository: Path) -> str:
    try:
        head = r3e._git_value(repository, "rev-parse", "HEAD")
        r3e.validate_git_authority(
            repository, head, original_commit=STARTING_COMMIT
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_GIT_AUTHORITY_INVALID") from exc
    if COMMIT_RE.fullmatch(head) is None:
        _fail("R4D2_GIT_AUTHORITY_INVALID")
    return head


def _validate_no_active_jobs_or_processes(
    *,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ps_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    try:
        scheduler.validate_scheduler_tools()
        environment, _ = scheduler.build_qsub_environment()
        completed = qstat_runner(
            [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
        payload = bytes(completed.stdout)
        if (
            completed.returncode != 0
            or completed.stderr
            or len(payload) > 4 * 1024 * 1024
            or b"<!DOCTYPE" in payload.upper()
            or b"<!ENTITY" in payload.upper()
        ):
            raise ValueError
        root = ET.fromstring(payload)
        local = lambda element: element.tag.rsplit("}", 1)[-1]
        numbers = {
            element.text or ""
            for element in root.iter()
            if local(element) == "JB_job_number"
        }
        names = {
            element.text or ""
            for element in root.iter()
            if local(element) == "JB_name"
        }
        if {ARRAY_JOB_ID, FINALIZER_JOB_ID} & numbers or any(
            re.search(r"lvef.*(?:c3|replay|extract|echo)", name, re.IGNORECASE)
            for name in names
        ):
            _fail("R4D2_ACTIVE_SCHEDULER_JOB_PRESENT")
        process = ps_runner(
            ["/bin/ps", "-u", environment["USER"], "-o", "pid=,command="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        if process.returncode != 0 or process.stderr or len(process.stdout) > 4 * 1024 * 1024:
            raise ValueError
        pattern = re.compile(
            rb"(?:scc_(?:submit|run)_lvef|lvef_c3_full_|run_production_|"
            rb"replay_lvef_c3_|echoprime|cuda|gpu)",
            re.IGNORECASE,
        )
        for line in bytes(process.stdout).splitlines():
            match = re.match(rb"\s*([0-9]+)\s+(.*)", line)
            if (
                match
                and int(match.group(1)) != os.getpid()
                and pattern.search(match.group(2))
            ):
                _fail("R4D2_ACTIVE_MATCHING_PROCESS_PRESENT")
    except PremaskReplayError:
        raise
    except Exception as exc:
        raise PremaskReplayError("R4D2_EXECUTION_QUIESCENCE_GATE_INVALID") from exc


def _validate_batch_terminal_receipt(
    attempt_root: Path, frozen: tuple[str, int, str, str]
) -> tuple[int, str]:
    batch_id, expected_size, expected_sha256, scheduler_identity = frozen
    path = (
        attempt_root
        / "batches"
        / batch_id
        / "preservation"
        / "batch_finalization_receipt.restricted.json"
    )
    payload, digest = _stable_private_payload(
        path, expected_size=expected_size, expected_sha256=expected_sha256
    )
    value = _strict_json(payload)
    try:
        finalizer._validate_receipt(value)
    except Exception as exc:
        raise PremaskReplayError("R4D2_PRIOR_BATCH_RECEIPT_INVALID") from exc
    if (
        value.get("batch_id") != batch_id
        or value.get("attempt_id") != ATTEMPT_ID
        or value.get("governing_commit") != STARTING_COMMIT
        or value.get("batch_plan_sha256") != PLAN_SHA256
        or value.get("scheduler_job_identity") != scheduler_identity
    ):
        _fail("R4D2_PRIOR_BATCH_RECEIPT_INVALID")
    return len(payload), digest


def _validate_failed_row(
    extraction_rows: Sequence[Mapping[str, str]],
    audit_rows: Sequence[Mapping[str, str]],
    *,
    partial: Path,
) -> dict[str, str]:
    if len(extraction_rows) != EXPECTED_EXTRACTION_ROWS:
        _fail("R4D2_EXTRACTION_ROW_COUNT_INVALID")
    write_states = tuple(
        (
            row,
            _parse_csv_boolean_field(
                row,
                artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
                field_name="write_ok",
            ),
        )
        for row in extraction_rows
    )
    failed = [
        row for row, state in write_states if state == CSV_BOOLEAN_STATE_FALSE
    ]
    successful = [
        row for row, state in write_states if state == CSV_BOOLEAN_STATE_TRUE
    ]
    if len(failed) != 1 or len(successful) != EXPECTED_SUCCESSFUL_EXTRACTIONS:
        _fail("R4D2_FAILED_ROW_NOT_UNIQUE")
    row = dict(failed[0])
    exact = {
        "smoke_role": "production_selected",
        "mask_status": "FAILED",
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
        "decoder_backend": "pydicom_pixels_raw:pillow",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
        "canonical_color_space": "RGB",
        "selected_preprocessing_path": reconstruction.PREPROCESSING_PATH_NOT_SELECTED,
        "fallback_status": reconstruction.FALLBACK_NOT_ATTEMPTED,
        "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
        "decode_color_status": "PASS",
        "temporal_sampling_policy": reconstruction.TEMPORAL_SAMPLING_POLICY,
        "error_code": "ValueError",
    }
    if any(str(row.get(key, "")) != value for key, value in exact.items()):
        _fail("R4D2_FAILED_ROW_AUTHORITY_INVALID")
    if (
        _parse_csv_boolean_field(
            row,
            artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
            field_name="write_ok",
        )
        != CSV_BOOLEAN_STATE_FALSE
        or _parse_csv_boolean_field(
            row,
            artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
            field_name="pixel_decode_ok",
        )
        != CSV_BOOLEAN_STATE_TRUE
        or _csv_nonnegative_integer(row.get("source_num_frames", ""))
        != EXPECTED_SOURCE_FRAMES
    ):
        _fail("R4D2_FAILED_ROW_AUTHORITY_INVALID")
    for count_field, gate_field in SOURCE_GATE_FIELDS:
        if (
            _csv_nonnegative_integer(row.get(count_field, "")) != 0
            or _parse_csv_boolean_field(
                row,
                artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
                field_name=gate_field,
            )
            != CSV_BOOLEAN_STATE_FALSE
        ):
            _fail("R4D2_FROZEN_SOURCE_METRICS_INVALID")
    for count_field, gate_field in DOWNSTREAM_GATE_FIELDS:
        if (
            _csv_nonnegative_integer(row.get(count_field, "")) is not None
            or _parse_csv_boolean_field(
                row,
                artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
                field_name=gate_field,
            )
            != CSV_BOOLEAN_STATE_FALSE
        ):
            _fail("R4D2_DOWNSTREAM_STAGE_AUTHORITY_INVALID")
    for field_name in (
        "frames_shape",
        "frames_dtype",
        "frames_sha256",
        "sampled_indices_sha256",
        "source_num_frames_sha256",
        "npz_sha256",
    ):
        if str(row.get(field_name, "")) != "":
            _fail("R4D2_DIAGNOSTIC_NPZ_AUTHORITY_INVALID")
    physical_key = str(row.get("physical_source_key", ""))
    source_relative = _safe_relative(row.get("source_relative_path", ""))
    if (
        SHA256_RE.fullmatch(physical_key) is None
        or source_relative != f"{physical_key}.dcm"
        or SHA256_RE.fullmatch(str(row.get("source_sha256", ""))) is None
    ):
        _fail("R4D2_FAILED_ROW_BINDING_INVALID")
    expected_clip = reconstruction.stable_clip_key(source_relative)
    expected_output = f"clips/{expected_clip[:2]}/{expected_clip}.npz"
    if (
        row.get("clip_key") != expected_clip
        or row.get("output_relative_path") != expected_output
        or os.path.lexists(partial / expected_output)
    ):
        _fail("R4D2_DIAGNOSTIC_NPZ_AUTHORITY_INVALID")

    audit_matches = [
        audit
        for audit in audit_rows
        if audit.get("source_relative_path") == source_relative
        and audit.get("subject_id") == row.get("subject_id")
        and audit.get("study_id") == row.get("study_id")
    ]
    if len(audit_matches) != 1:
        _fail("R4D2_DICOM_AUDIT_JOIN_INVALID")
    audit = audit_matches[0]
    audit_exact = {
        "smoke_role": "production_selected",
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
    }
    if any(str(audit.get(key, "")) != value for key, value in audit_exact.items()):
        _fail("R4D2_DICOM_AUDIT_JOIN_INVALID")
    if (
        _parse_csv_boolean_field(
            audit,
            artifact_role=CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
            field_name="read_ok",
        )
        != CSV_BOOLEAN_STATE_TRUE
        or _parse_csv_boolean_field(
            audit,
            artifact_role=CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
            field_name="is_multiframe",
        )
        != CSV_BOOLEAN_STATE_TRUE
        or _parse_csv_boolean_field(
            audit,
            artifact_role=CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
            field_name="pixel_decode_ok",
        )
        != CSV_BOOLEAN_STATE_TRUE
        or _csv_nonnegative_integer(audit.get("number_of_frames", ""))
        != EXPECTED_SOURCE_FRAMES
        or _csv_nonnegative_integer(audit.get("samples_per_pixel", "")) != 3
        or _csv_nonnegative_integer(audit.get("bits_allocated", "")) != 8
        or _csv_nonnegative_integer(audit.get("bits_stored", "")) != 8
    ):
        _fail("R4D2_DICOM_AUDIT_JOIN_INVALID")
    if audit.get("download_sha256") != row.get("source_sha256"):
        _fail("R4D2_LOCAL_SHA256_AUTHORITY_INVALID")
    same_study_successes = [
        item for item in successful if item.get("study_id") == row.get("study_id")
    ]
    ordinary = [
        item
        for item in same_study_successes
        if item.get("selected_preprocessing_path")
        == "ORDINARY_CENTER_CROP_RESIZE_HISTORICAL_TEMPORAL_V1"
    ]
    if (
        len(same_study_successes) != EXPECTED_AFFECTED_STUDY_ORDINARY_CINES
        or len(ordinary) != EXPECTED_AFFECTED_STUDY_ORDINARY_CINES
    ):
        _fail("R4D2_AFFECTED_STUDY_COVERAGE_INVALID")
    return row


def _load_batch3_authority(
    attempt_root: Path,
) -> tuple[dict[str, str], dict[str, tuple[int, str]]]:
    partial = (
        attempt_root
        / "extracted_cache"
        / BATCH_ID
        / "dicom_extraction.partial"
    )
    if not full_sequential._closed_terminal_failure_summary(partial):
        _fail("R4D2_BATCH3_FAILURE_NOT_CLOSED")
    payloads: dict[str, bytes] = {}
    digests: dict[str, tuple[int, str]] = {}
    for basename, (expected_size, expected_sha256) in FROZEN_BATCH3_ARTIFACTS.items():
        payload, digest = _stable_private_payload(
            partial / basename,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        payloads[basename] = payload
        digests[basename] = (len(payload), digest)
    failure = _strict_json(payloads["failure.summary.json"])
    if (
        set(failure) != full_sequential.TERMINAL_EXTRACTION_FAILURE_SUMMARY_V2_KEYS
        or failure.get("schema_version") != 2
        or failure.get("artifact_type")
        != "lvef_c3_batch_extraction_failure_summary_v2"
        or failure.get("status") != "FAIL_EXTRACTION_GATE"
        or failure.get("error_code") != "EXTRACTION_SOURCE_SIGNAL_QUALITY_FAILURE"
        or not full_sequential._closed_extraction_provenance(
            failure.get("extraction_provenance")
        )
        or failure.get("identifiers_emitted") is not False
        or failure.get("paths_emitted") is not False
    ):
        _fail("R4D2_BATCH3_FAILURE_SUMMARY_INVALID")
    audit_rows = _strict_csv(
        payloads["dicom_audit.restricted.csv"],
        preservation.DICOM_AUDIT_HEADER,
        artifact_role=CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT,
    )
    extraction_rows = _strict_csv(
        payloads["extraction_manifest.restricted.csv"],
        preservation.EXTRACTION_MANIFEST_HEADER,
        artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
    )
    _validate_frozen_csv_boolean_aggregate(
        audit_rows, artifact_role=CSV_BOOLEAN_ARTIFACT_DICOM_AUDIT
    )
    _validate_frozen_csv_boolean_aggregate(
        extraction_rows,
        artifact_role=CSV_BOOLEAN_ARTIFACT_EXTRACTION_MANIFEST,
    )
    if len(audit_rows) != EXPECTED_DOWNLOAD_ROWS:
        _fail("R4D2_DICOM_AUDIT_ROW_COUNT_INVALID")
    row = _validate_failed_row(extraction_rows, audit_rows, partial=partial)
    if os.path.lexists(attempt_root / "batches" / BATCH_ID / "echoprime") or os.path.lexists(
        attempt_root / "batches" / BATCH_ID / "echoprime.partial"
    ):
        _fail("R4D2_ECHOPRIME_STAGE_WAS_REACHED")
    return row, digests


def _validate_diagnostic_candidate(
    diagnostic_root: Path,
    *,
    allowed_prefix: Path,
    attempt_root: Path,
) -> r3e.PrivateDirectoryIdentity:
    if (
        not diagnostic_root.is_absolute()
        or Path(os.path.abspath(diagnostic_root)) != diagnostic_root
        or DIAGNOSTIC_NAME_RE.fullmatch(diagnostic_root.name) is None
        or os.path.lexists(diagnostic_root)
    ):
        _fail("R4D2_DIAGNOSTIC_ROOT_NOT_FRESH")
    try:
        r3e._require_no_symlink_components(allowed_prefix)
        parent = diagnostic_root.parent.resolve(strict=True)
        approved = allowed_prefix.resolve(strict=True)
    except Exception as exc:
        raise _translate(exc, "R4D2_DIAGNOSTIC_PARENT_INVALID") from exc
    if parent != approved:
        _fail("R4D2_DIAGNOSTIC_PARENT_INVALID")
    try:
        diagnostic_root.relative_to(attempt_root)
    except ValueError:
        pass
    else:
        _fail("R4D2_DIAGNOSTIC_ROOT_INSIDE_ATTEMPT")
    try:
        return r3e._private_directory_identity(parent)
    except Exception as exc:
        raise _translate(exc, "R4D2_DIAGNOSTIC_PARENT_INVALID") from exc


def _strict_current_device_receipt(
    receipt: Mapping[str, Any],
    *,
    expectation: core.DownloadExpectation,
    source_identity: r3e.SourceFileIdentity,
    root_identity: r3e.LegacyRootIdentity,
    mount_authority: r3e.CurrentMountAuthority,
) -> str:
    if set(receipt) != r3e.DOWNLOAD_VERIFICATION_RECEIPT_KEYS:
        _fail("R4D2_DOWNLOAD_RECEIPT_SCHEMA_INVALID")
    exact: Mapping[str, Any] = {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": expectation.source_object_key,
        "size_bytes": expectation.size_bytes,
        "generation": expectation.generation,
        "md5_base64": expectation.md5_base64,
        "crc32c_base64": expectation.crc32c_base64,
        "file_inode": source_identity.inode,
        "file_mtime_ns": source_identity.mtime_ns,
        "file_device": source_identity.device,
        "digest_backend": "google_crc32c_c_external_worker_v1",
        "digest_chunk_size_bytes": 8_388_608,
    }
    for key, expected in exact.items():
        observed = receipt.get(key)
        if key == "file_device" and (
            type(observed) is not int or observed != expected
        ):
            _fail("R4D2_HISTORICAL_FILE_DEVICE_NAMESPACE_UNRESOLVED")
        if type(observed) is not type(expected) or observed != expected:
            _fail("R4D2_DOWNLOAD_RECEIPT_AUTHORITY_INVALID")
    local_sha256 = receipt.get("local_sha256")
    if type(local_sha256) is not str or SHA256_RE.fullmatch(local_sha256) is None:
        _fail("R4D2_DOWNLOAD_RECEIPT_AUTHORITY_INVALID")
    if source_identity.device != root_identity.device:
        _fail("R4D2_CURRENT_DEVICE_TOPOLOGY_INVALID")
    if mount_authority.status != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT":
        _fail("R4D2_CURRENT_MOUNT_AUTHORITY_INVALID")
    return local_sha256


def _load_plan_and_source(
    *,
    attempt_root: Path,
    root_identity: r3e.LegacyRootIdentity,
    failed_row: Mapping[str, str],
    authority: R4AttemptAuthority,
    requirements: core.PlanRequirements | None,
    row_authority: r3e.ReplayRowAuthority | None,
    row_authority_loader: Callable[[Path], r3e.ReplayRowAuthority],
    repository: Path,
    mount_validator: Callable[[Path, Path], r3e.CurrentMountAuthority],
) -> tuple[
    Mapping[str, Any],
    Path,
    r3e.SourceFileIdentity,
    r3e.CurrentMountAuthority,
    str,
]:
    for path in (
        attempt_root / "full_batch_plan.restricted.json",
        attempt_root / "full_submission_claim.restricted.json",
        attempt_root / "full_launch_authority.restricted.json",
    ):
        try:
            r3e._validate_replay_input_effective_privacy(
                path, attempt_root=attempt_root, root_identity=root_identity
            )
        except Exception as exc:
            raise _translate(exc, "R4D2_PLAN_AUTHORITY_INVALID") from exc
    try:
        plan, effective_requirements, planned_batch, launch, plan_sha256 = (
            r3e._load_bound_plan(
                attempt_root, authority=authority, requirements=requirements
            )
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_PLAN_AUTHORITY_INVALID") from exc
    if plan_sha256 != PLAN_SHA256:
        _fail("R4D2_PLAN_SHA256_MISMATCH")
    physical_key = str(failed_row["physical_source_key"])
    matches = [
        row
        for row in planned_batch["objects"]
        if row.get("source_object_key") == physical_key
    ]
    if len(matches) != 1:
        _fail("R4D2_PLAN_OBJECT_MEMBERSHIP_INVALID")
    planned_object = matches[0]
    if (
        str(planned_object["subject_id"]) != failed_row["subject_id"]
        or str(planned_object["study_id"]) != failed_row["study_id"]
    ):
        _fail("R4D2_PLAN_OBJECT_MEMBERSHIP_INVALID")
    selected = row_authority or row_authority_loader(repository)
    try:
        r3e._load_selected_source_object(
            selected,
            plan=plan,
            planned_object=planned_object,
            authority=authority,
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_SOURCE_MANIFEST_MEMBERSHIP_INVALID") from exc

    raw_batch = attempt_root / "raw" / BATCH_ID
    manifest_path = raw_batch / "verified_download_manifest.restricted.csv"
    try:
        r3e._validate_replay_input_effective_privacy(
            manifest_path, attempt_root=attempt_root, root_identity=root_identity
        )
        download_rows, manifest_sha256 = r3e._read_csv_exact_with_sha256(
            manifest_path, r3e.VERIFIED_DOWNLOAD_MANIFEST_HEADER
        )
        downloaded = r3e._validate_download_binding(
            download_rows,
            failed_row,
            authority,
            planned_batch=planned_batch,
        )
        production_stages.validate_download_manifest_plan_membership(
            manifest_path, planned_batch
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_DOWNLOAD_MANIFEST_AUTHORITY_INVALID") from exc
    expected_runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha256}
    )
    ledger_path = (
        attempt_root
        / "batches"
        / BATCH_ID
        / "download_resume_ledger.restricted.json"
    )
    try:
        r3e._validate_replay_input_effective_privacy(
            ledger_path, attempt_root=attempt_root, root_identity=root_identity
        )
        ledger = r3e._read_json(
            ledger_path, maximum_bytes=r3e.MAXIMUM_MANIFEST_BYTES
        )
        expected_keys = {
            str(item["source_object_key"]) for item in planned_batch["objects"]
        }
        core.validate_resume_authority(
            ledger,
            expected_authority=expected_runtime,
            attempt_id=ATTEMPT_ID,
            expected_object_keys={BATCH_ID: expected_keys},
        )
        canonical = production_stages.validate_stage_predecessor(
            input_ledger=ledger_path,
            batch_id=BATCH_ID,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=expected_runtime,
            expected_attempt_id=ATTEMPT_ID,
            expected_object_keys=expected_keys,
            bound_manifest=manifest_path,
        )
        if canonical != ledger:
            raise ValueError
        core.validate_direct_full_download_scope(
            launch_authority=launch,
            ledger=ledger,
            plan=plan,
            requirements=effective_requirements,
            batch_id=BATCH_ID,
            maximum_attempts_per_object=5,
            expected_launch_authority_sha256=core.canonical_json_sha256(launch),
            test_only_synthetic_full_scope=requirements is not None,
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_DOWNLOAD_LEDGER_AUTHORITY_INVALID") from exc
    ledger_batch = ledger.get("batches", {}).get(BATCH_ID, {})
    if (
        set(ledger.get("batches", {})) != {BATCH_ID}
        or ledger_batch.get("state") != "DOWNLOAD_VERIFIED"
        or ledger_batch.get("download_manifest_sha256") != manifest_sha256
    ):
        _fail("R4D2_DOWNLOAD_LEDGER_AUTHORITY_INVALID")
    expectation = core.expectation_from_plan_object(planned_object)
    objects_root = raw_batch / "objects"
    source_path = objects_root / core.planned_final_name(expectation)
    if source_path != objects_root / failed_row["source_relative_path"]:
        _fail("R4D2_LOCAL_SOURCE_BINDING_INVALID")
    try:
        source_identity = r3e._source_file_identity(
            source_path,
            expected_size=expectation.size_bytes,
            attempt_root=attempt_root,
            root_identity=root_identity,
        )
        mount_authority = mount_validator(attempt_root, source_path)
    except Exception as exc:
        raise _translate(exc, "R4D2_CURRENT_SOURCE_AUTHORITY_INVALID") from exc
    if (
        not isinstance(mount_authority, r3e.CurrentMountAuthority)
        or mount_authority.status != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT"
        or SHA256_RE.fullmatch(mount_authority.identity_sha256) is None
    ):
        _fail("R4D2_CURRENT_MOUNT_AUTHORITY_INVALID")
    receipt_path = (
        raw_batch / "receipts" / f"{expectation.source_object_key}.verification.json"
    )
    try:
        r3e._validate_replay_input_effective_privacy(
            receipt_path, attempt_root=attempt_root, root_identity=root_identity
        )
        receipt, receipt_sha256 = r3e._read_json_with_sha256(
            receipt_path, maximum_bytes=r3e.MAXIMUM_RECEIPT_BYTES
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_DOWNLOAD_RECEIPT_AUTHORITY_INVALID") from exc
    receipts = ledger_batch.get("download_verification_receipts", {})
    if set(receipts) != expected_keys or receipts.get(expectation.source_object_key) != receipt_sha256:
        _fail("R4D2_DOWNLOAD_RECEIPT_AUTHORITY_INVALID")
    local_sha256 = _strict_current_device_receipt(
        receipt,
        expectation=expectation,
        source_identity=source_identity,
        root_identity=root_identity,
        mount_authority=mount_authority,
    )
    if (
        downloaded.get("observed_sha256") != local_sha256
        or failed_row.get("source_sha256") != local_sha256
    ):
        _fail("R4D2_LOCAL_SHA256_AUTHORITY_INVALID")
    planned_partial = raw_batch / "partials" / core.planned_partial_name(
        expectation, ATTEMPT_ID
    )
    receipt_temporary = receipt_path.parent / (
        f".{receipt_path.name}.{ATTEMPT_ID}.partial"
    )
    if os.path.lexists(planned_partial) or os.path.lexists(receipt_temporary):
        _fail("R4D2_LOCAL_SOURCE_AUTHORITY_INVALID")
    return planned_object, source_path, source_identity, mount_authority, local_sha256


def run_preflight(
    *,
    production_root: Path = PRODUCTION_ROOT,
    allowed_diagnostic_prefix: Path = ALLOWED_DIAGNOSTIC_PREFIX,
    repository: Path = REPOSITORY_ROOT,
    authority: R4AttemptAuthority = R4_AUTHORITY,
    requirements: core.PlanRequirements | None = None,
    row_authority: r3e.ReplayRowAuthority | None = None,
    row_authority_loader: Callable[[Path], r3e.ReplayRowAuthority] = (
        r3e._discover_replay_row_authority
    ),
    git_validator: Callable[[Path], str] = _validate_git,
    quiescence_validator: Callable[[], None] = _validate_no_active_jobs_or_processes,
    inventory_loader: Callable[[Path], R4Inventory] = r4_metadata_inventory,
    mount_validator: Callable[[Path, Path], r3e.CurrentMountAuthority] = (
        r3e.validate_current_mount_authority
    ),
) -> PremaskReplayPreflight:
    """Validate all R4D2 authority without opening the retained DICOM body."""

    try:
        execution_commit = git_validator(repository)
        quiescence_validator()
        attempt_root = production_root / "attempts" / authority.attempt_id
        inventory = inventory_loader(attempt_root)
        validate_r4_inventory(inventory, authority)
        root_identity = inventory.root_identity
        batch1 = _validate_batch_terminal_receipt(attempt_root, BATCH_1_RECEIPT)
        batch2 = _validate_batch_terminal_receipt(attempt_root, BATCH_2_RECEIPT)
        failed_row, batch3 = _load_batch3_authority(attempt_root)
        planned, source, source_identity, mount, local_sha = _load_plan_and_source(
            attempt_root=attempt_root,
            root_identity=root_identity,
            failed_row=failed_row,
            authority=authority,
            requirements=requirements,
            row_authority=row_authority,
            row_authority_loader=row_authority_loader,
            repository=repository,
            mount_validator=mount_validator,
        )
        diagnostic_root = (
            allowed_diagnostic_prefix
            / f"lvef_c3_r4d2_premask_{execution_commit[:16]}"
        )
        diagnostic_parent = _validate_diagnostic_candidate(
            diagnostic_root,
            allowed_prefix=allowed_diagnostic_prefix,
            attempt_root=attempt_root,
        )
        current = r3e._source_file_identity(
            source,
            expected_size=int(planned["size_bytes"]),
            attempt_root=attempt_root,
            root_identity=root_identity,
        )
        if current != source_identity or mount_validator(attempt_root, source) != mount:
            _fail("R4D2_CURRENT_SOURCE_IDENTITY_CHANGED")
        artifacts = {
            "batch_1_terminal_receipt": batch1,
            "batch_2_terminal_receipt": batch2,
            **batch3,
        }
        return PremaskReplayPreflight(
            execution_commit=execution_commit,
            attempt_root=attempt_root,
            inventory=inventory,
            failed_row=failed_row,
            planned_object=planned,
            source_path=source,
            source_identity=source_identity,
            root_identity=root_identity,
            mount_authority=mount,
            local_sha256=local_sha,
            diagnostic_root=diagnostic_root,
            diagnostic_parent=diagnostic_parent,
            artifact_digests=artifacts,
        )
    except PremaskReplayError:
        raise
    except Exception as exc:
        raise _translate(exc, "R4D2_PREFLIGHT_UNEXPECTED_SANITIZED_FAILURE") from exc


def _read_source_once(
    path: Path,
    *,
    expected_identity: r3e.SourceFileIdentity,
    counters: AccessCounters,
) -> tuple[bytes, str]:
    counters.register_read(path)
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise PremaskReplayError("R4D2_SOURCE_READ_AUTHORITY_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_uid,
            opened.st_nlink,
            stat.S_IMODE(opened.st_mode),
        )
        expected = (
            expected_identity.device,
            expected_identity.inode,
            expected_identity.size,
            expected_identity.mtime_ns,
            expected_identity.ctime_ns,
            os.geteuid(),
            1,
            0o600,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or identity != expected
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size < 1
            or opened.st_size > MAXIMUM_DICOM_BYTES
        ):
            _fail("R4D2_SOURCE_READ_AUTHORITY_INVALID")
        remaining = int(opened.st_size)
        blocks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                _fail("R4D2_SOURCE_READ_AUTHORITY_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_uid,
            after.st_nlink,
            stat.S_IMODE(after.st_mode),
        ) != identity:
            _fail("R4D2_SOURCE_IDENTITY_CHANGED_DURING_READ")
        payload = b"".join(blocks)
        return payload, hashlib.sha256(payload).hexdigest()
    finally:
        os.close(descriptor)


def _mirror_strict_mask(
    frames: np.ndarray, cv2_module: Any
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    value = np.asarray(frames)
    if value.ndim != 4 or value.shape[-1] != 3 or value.dtype != np.uint8:
        raise ValueError("canonical frames must be uint8 T,H,W,3")
    source = np.copy(value)
    gray_source = reconstruction._rgb_luma_uint8(source)
    frame_sum = np.where(gray_source[0] > 0, 1, 0)
    for gray in gray_source:
        frame_sum = np.add(frame_sum, np.where(gray > 0, 1, 0))
    kernel = np.ones((3, 3), np.uint8)
    frame_sum = cv2_module.erode(
        np.where(frame_sum > 0, 1, 0).astype(np.uint8),
        kernel,
        iterations=10,
    )
    frame_sum = np.where(frame_sum > 0, 1, 0)
    persistent = int(np.count_nonzero(frame_sum))
    first = gray_source[0].astype(np.int16)
    last = gray_source[-1].astype(np.int16)
    difference = np.where(np.abs(first - last) > 0, 1, 0)
    difference[0:20, 0:20] = 0
    first_last = int(np.count_nonzero(difference))
    overlap = np.where(np.add(frame_sum, difference) > 1, 1, 0)
    overlap = cv2_module.dilate(
        np.uint8(overlap), kernel, iterations=10
    ).astype(np.uint8)
    overlap_before = int(np.count_nonzero(overlap))
    cv2_module.floodFill(overlap, None, (0, 0), 100)
    overlap = np.where(overlap != 100, 255, 0).astype(np.uint8)
    contours, _ = cv2_module.findContours(
        overlap, cv2_module.RETR_TREE, cv2_module.CHAIN_APPROX_SIMPLE
    )
    for contour in contours:
        hull = cv2_module.convexHull(contour)
        cv2_module.drawContours(overlap, [hull], -1, (255, 0, 0), 3)
    overlap = np.where(overlap > 0, 1, 0).astype(np.uint8)
    cv2_module.floodFill(overlap, None, (0, 0), 100)
    mask = np.asarray(np.where(overlap != 100, 1, 0), dtype=bool)
    output = np.where(mask[None, ..., None], source, np.uint8(0))
    return (
        np.ascontiguousarray(output, dtype=np.uint8),
        np.ascontiguousarray(mask),
        {
            "first_last_difference_pixel_count": first_last,
            "persistent_occupancy_pixel_count": persistent,
            "overlap_before_floodfill_pixel_count": overlap_before,
            "generated_sector_pixel_count": int(np.count_nonzero(mask)),
        },
    )


def _premask_counts(frames: np.ndarray) -> tuple[int, int]:
    value = np.asarray(frames)
    nonzero = int(np.count_nonzero(np.any(value != 0, axis=-1)))
    temporal = int(
        np.count_nonzero(np.any(value[1:] != value[:-1], axis=-1))
    )
    return nonzero, temporal


def classify_mask_collapse(value: Mapping[str, Any]) -> str:
    occupancy = value.get("persistent_occupancy_pixel_count")
    first_last = value.get("first_last_difference_pixel_count")
    overlap = value.get("overlap_before_floodfill_pixel_count")
    sector = value.get("generated_sector_pixel_count")
    post_nonzero = value.get("post_mask_nonzero_pixel_count")
    post_temporal = value.get("post_mask_adjacent_temporal_variation_pixel_count")
    if occupancy == 0 and first_last == 0:
        return "MULTIPLE_MASK_COLLAPSE_MECHANISMS"
    if occupancy == 0:
        return "PERSISTENT_OCCUPANCY_ERODED_TO_ZERO"
    if first_last == 0:
        return "FIRST_LAST_DIFFERENCE_ZERO"
    if overlap == 0:
        return "OVERLAP_EMPTY"
    if sector == 0:
        return "FLOODFILL_OR_CONTOUR_TOPOLOGY_EMPTY"
    if post_nonzero == 0 and post_temporal == 0:
        return "MULTIPLE_MASK_COLLAPSE_MECHANISMS"
    if post_nonzero == 0:
        return "POSTMASK_ZERO_SIGNAL"
    if post_temporal == 0:
        return "POSTMASK_ZERO_VARIATION"
    return "MECHANISM_NOT_RESOLVED"


def classify_premask_observation(value: Mapping[str, Any]) -> tuple[str, str, str]:
    contradiction = str(value.get("contradiction_kind", "NONE"))
    production_exception = value.get("production_mask_exception_class")
    if production_exception is not None:
        return (
            "MASK_CONSTRUCTION_EXCEPTION",
            "NOT_APPLICABLE",
            "MASK_IMPLEMENTATION_REPAIR_REVIEW",
        )
    mirror_equal = value.get("mask_mirror_equals_frozen_helper")
    if contradiction != "NONE" or mirror_equal is not True:
        action = (
            "MASK_IMPLEMENTATION_REPAIR_REVIEW"
            if mirror_equal is False
            or contradiction in {
                "MIRROR_IMPLEMENTATION_EXCEPTION",
                "MIRROR_MASK_MISMATCH",
                "HELPER_OUTPUT_INCONSISTENT",
                "SOURCE_METRIC_RECOMPUTATION_EXCEPTION",
            }
            else "READ_ONLY_AUTHORITY_RECONCILIATION"
        )
        return "REPLAY_EVIDENCE_CONTRADICTORY", "NOT_APPLICABLE", action
    frozen = (
        value.get("generated_sector_pixel_count") == 0
        and value.get("post_mask_nonzero_pixel_count") == 0
        and value.get("post_mask_adjacent_temporal_variation_pixel_count") == 0
        and value.get("source_sector_nonempty_gate_passed") is False
        and value.get("source_nonzero_retained_pixel_gate_passed") is False
        and value.get("source_temporal_variation_gate_passed") is False
    )
    if not frozen:
        return (
            "PERSISTED_SOURCE_FAILURE_CONTRADICTED",
            "NOT_APPLICABLE",
            "READ_ONLY_AUTHORITY_RECONCILIATION",
        )
    premask_nonzero = value.get("pre_mask_nonzero_pixel_count")
    premask_temporal = value.get("pre_mask_adjacent_temporal_variation_pixel_count")
    if type(premask_nonzero) is not int or type(premask_temporal) is not int:
        return (
            "REPLAY_EVIDENCE_CONTRADICTORY",
            "NOT_APPLICABLE",
            "READ_ONLY_AUTHORITY_RECONCILIATION",
        )
    if premask_nonzero == 0:
        return (
            "PREMASK_BLANK",
            "NOT_APPLICABLE",
            "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION",
        )
    if premask_temporal == 0:
        return (
            "PREMASK_NONBLANK_STATIC",
            "NOT_APPLICABLE",
            "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION",
        )
    if any(
        value.get(name) == 0
        for name in (
            "generated_sector_pixel_count",
            "post_mask_nonzero_pixel_count",
            "post_mask_adjacent_temporal_variation_pixel_count",
        )
    ):
        return (
            "DYNAMIC_DECODE_MASK_COLLAPSE",
            classify_mask_collapse(value),
            "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION",
        )
    return (
        "PERSISTED_SOURCE_FAILURE_CONTRADICTED",
        "NOT_APPLICABLE",
        "READ_ONLY_AUTHORITY_RECONCILIATION",
    )


def compute_premask_observation(
    frames: np.ndarray,
    *,
    cv2_module: Any,
    mask_helper: Callable[[np.ndarray, Any], tuple[np.ndarray, np.ndarray]] = (
        reconstruction._mask_ultrasound_strict
    ),
    mirror: Callable[
        [np.ndarray, Any], tuple[np.ndarray, np.ndarray, dict[str, int]]
    ] = _mirror_strict_mask,
) -> dict[str, Any]:
    value = np.ascontiguousarray(frames, dtype=np.uint8)
    premask_nonzero, premask_temporal = _premask_counts(value)
    result: dict[str, Any] = {
        "pre_mask_nonzero_pixel_count": premask_nonzero,
        "pre_mask_adjacent_temporal_variation_pixel_count": premask_temporal,
        "first_last_difference_pixel_count": None,
        "persistent_occupancy_pixel_count": None,
        "overlap_before_floodfill_pixel_count": None,
        "generated_sector_pixel_count": None,
        "post_mask_nonzero_pixel_count": None,
        "post_mask_adjacent_temporal_variation_pixel_count": None,
        "source_sector_nonempty_gate_passed": None,
        "source_nonzero_retained_pixel_gate_passed": None,
        "source_temporal_variation_gate_passed": None,
        "mask_mirror_equals_frozen_helper": None,
        "production_mask_exception_class": None,
        "contradiction_kind": "NONE",
    }
    mirror_output: np.ndarray | None = None
    mirror_mask: np.ndarray | None = None
    try:
        mirror_output, mirror_mask, intermediates = mirror(value, cv2_module)
        result.update(intermediates)
    except Exception:
        result["contradiction_kind"] = "MIRROR_IMPLEMENTATION_EXCEPTION"
    try:
        helper_output, helper_mask = mask_helper(value, cv2_module)
    except Exception as exc:
        exception_class = type(exc).__name__
        result["production_mask_exception_class"] = (
            exception_class if SAFE_EXCEPTION_RE.fullmatch(exception_class) else "Exception"
        )
        replay_class, mechanism, action = classify_premask_observation(result)
        result.update(
            {
                "premask_replay_class": replay_class,
                "mask_collapse_mechanism": mechanism,
                "selected_next_action": action,
            }
        )
        return result
    if mirror_output is None or mirror_mask is None:
        result["mask_mirror_equals_frozen_helper"] = False
    else:
        equal = bool(np.array_equal(mirror_mask, helper_mask))
        result["mask_mirror_equals_frozen_helper"] = equal
        if not equal:
            result["contradiction_kind"] = "MIRROR_MASK_MISMATCH"
        expected_output = np.where(
            np.asarray(helper_mask)[None, ..., None], value, np.uint8(0)
        )
        if not np.array_equal(helper_output, expected_output):
            result["contradiction_kind"] = "HELPER_OUTPUT_INCONSISTENT"
    try:
        source_quality = reconstruction._signal_quality_metrics(
            helper_output, helper_mask
        )
        result.update(
            {
                "generated_sector_pixel_count": int(
                    source_quality["sector_pixel_count"]
                ),
                "post_mask_nonzero_pixel_count": int(
                    source_quality["nonzero_retained_pixel_count"]
                ),
                "post_mask_adjacent_temporal_variation_pixel_count": int(
                    source_quality["temporal_variation_pixel_count"]
                ),
                "source_sector_nonempty_gate_passed": bool(
                    source_quality["sector_nonempty_gate_passed"]
                ),
                "source_nonzero_retained_pixel_gate_passed": bool(
                    source_quality["nonzero_retained_pixel_gate_passed"]
                ),
                "source_temporal_variation_gate_passed": bool(
                    source_quality["temporal_variation_gate_passed"]
                ),
            }
        )
    except Exception:
        result["contradiction_kind"] = "SOURCE_METRIC_RECOMPUTATION_EXCEPTION"
    replay_class, mechanism, action = classify_premask_observation(result)
    result.update(
        {
            "premask_replay_class": replay_class,
            "mask_collapse_mechanism": mechanism,
            "selected_next_action": action,
        }
    )
    return result


def _decode_once(
    payload: bytes,
    *,
    source_path: Path,
    counters: AccessCounters,
    dcmread: Callable[..., Any] | None = None,
    normalizer: Callable[[Any, Any], tuple[np.ndarray, dict[str, str]]] = (
        reconstruction._normalize_dicom_pixels
    ),
) -> tuple[np.ndarray, dict[str, str]]:
    import pydicom

    reader = pydicom.dcmread if dcmread is None else dcmread
    counters.register_decode(source_path)
    dataset = reader(io.BytesIO(payload), stop_before_pixels=False, force=False)
    frames, metadata = normalizer(dataset, pydicom)
    return np.ascontiguousarray(frames, dtype=np.uint8), dict(metadata)


def _validate_decode_authority(
    frames: np.ndarray,
    metadata: Mapping[str, str],
    failed_row: Mapping[str, str],
) -> None:
    expected_metadata_keys = {
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
    }
    if (
        set(metadata) != expected_metadata_keys
        or frames.ndim != 4
        or frames.dtype != np.uint8
        or frames.shape[-1] != 3
        or frames.shape[0] != EXPECTED_SOURCE_FRAMES
    ):
        _fail("R4D2_DECODED_FRAME_AUTHORITY_INVALID")
    for field in (
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
    ):
        if metadata.get(field) != failed_row.get(field):
            _fail("R4D2_DECODE_COLOR_AUTHORITY_MISMATCH")


def _revalidate_source(preflight: PremaskReplayPreflight) -> None:
    try:
        source = r3e._source_file_identity(
            preflight.source_path,
            expected_size=int(preflight.planned_object["size_bytes"]),
            attempt_root=preflight.attempt_root,
            root_identity=preflight.root_identity,
        )
        mount = r3e.validate_current_mount_authority(
            preflight.attempt_root, preflight.source_path
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_CURRENT_SOURCE_AUTHORITY_CHANGED") from exc
    if source != preflight.source_identity or mount != preflight.mount_authority:
        _fail("R4D2_CURRENT_SOURCE_AUTHORITY_CHANGED")


def _post_validate_original_authority(
    preflight: PremaskReplayPreflight,
    *,
    inventory_loader: Callable[[Path], R4Inventory] = r4_metadata_inventory,
) -> R4Inventory:
    after = inventory_loader(preflight.attempt_root)
    validate_r4_inventory(after)
    if after != preflight.inventory:
        _fail("R4D2_R4_ATTEMPT_CHANGED_DURING_REPLAY")
    batch1 = _validate_batch_terminal_receipt(preflight.attempt_root, BATCH_1_RECEIPT)
    batch2 = _validate_batch_terminal_receipt(preflight.attempt_root, BATCH_2_RECEIPT)
    _, batch3 = _load_batch3_authority(preflight.attempt_root)
    observed = {
        "batch_1_terminal_receipt": batch1,
        "batch_2_terminal_receipt": batch2,
        **batch3,
    }
    if observed != dict(preflight.artifact_digests):
        _fail("R4D2_FROZEN_ARTIFACT_CHANGED_DURING_REPLAY")
    _revalidate_source(preflight)
    return after


def _safe_document(value: Mapping[str, Any], expected_keys: frozenset[str]) -> None:
    if set(value) != expected_keys:
        _fail("R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID")
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    forbidden = (
        "subject_id",
        "study_id",
        "clip_key",
        "physical_source_key",
        "source_relative_path",
        "output_relative_path",
        "gcs://",
        "/restricted/",
        ".dcm",
        ".npz",
    )
    if any(token in serialized for token in forbidden):
        _fail("R4D2_DIAGNOSTIC_SAFE_EXPORT_INVALID")
    if value.get("identifiers_emitted") is not False or value.get("locators_emitted") is not False or value.get("paths_emitted") is not False:
        _fail("R4D2_DIAGNOSTIC_SAFE_EXPORT_INVALID")


def _nonnegative_integer_or_none(value: object) -> bool:
    return value is None or (
        type(value) is int and int(value) >= 0
    )


def _validate_technical_document(value: Mapping[str, Any]) -> None:
    exact_decode = {
        "source_frame_count": EXPECTED_SOURCE_FRAMES,
        "decode_color_status": "PASS",
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
        "decoder_backend": "pydicom_pixels_raw:pillow",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
        "canonical_color_space": "RGB",
    }
    if any(
        type(value.get(key)) is not type(expected)
        or value.get(key) != expected
        for key, expected in exact_decode.items()
    ):
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    for key in (
        "pre_mask_nonzero_pixel_count",
        "pre_mask_adjacent_temporal_variation_pixel_count",
    ):
        if type(value.get(key)) is not int or int(value[key]) < 0:
            _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    for key in (
        "first_last_difference_pixel_count",
        "persistent_occupancy_pixel_count",
        "overlap_before_floodfill_pixel_count",
        "generated_sector_pixel_count",
        "post_mask_nonzero_pixel_count",
        "post_mask_adjacent_temporal_variation_pixel_count",
    ):
        if not _nonnegative_integer_or_none(value.get(key)):
            _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    gate_pairs = (
        ("generated_sector_pixel_count", "source_sector_nonempty_gate_passed"),
        (
            "post_mask_nonzero_pixel_count",
            "source_nonzero_retained_pixel_gate_passed",
        ),
        (
            "post_mask_adjacent_temporal_variation_pixel_count",
            "source_temporal_variation_gate_passed",
        ),
    )
    gate_evaluation_unavailable = (
        value.get("production_mask_exception_class") is not None
        or value.get("contradiction_kind")
        == "SOURCE_METRIC_RECOMPUTATION_EXCEPTION"
    )
    if gate_evaluation_unavailable and (
        value.get("post_mask_nonzero_pixel_count") is not None
        or value.get("post_mask_adjacent_temporal_variation_pixel_count") is not None
        or any(value.get(gate_key) is not None for _, gate_key in gate_pairs)
    ):
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    for count_key, gate_key in gate_pairs:
        count = value.get(count_key)
        gate = value.get(gate_key)
        if gate_evaluation_unavailable and gate is None:
            continue
        if count is None:
            if gate is not None:
                _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
        elif type(gate) is not bool or gate is not (int(count) > 0):
            _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    mirror = value.get("mask_mirror_equals_frozen_helper")
    if mirror is not None and type(mirror) is not bool:
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    exception_class = value.get("production_mask_exception_class")
    if exception_class is not None and (
        type(exception_class) is not str
        or SAFE_EXCEPTION_RE.fullmatch(exception_class) is None
    ):
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    contradiction_kinds = {
        "NONE",
        "MIRROR_IMPLEMENTATION_EXCEPTION",
        "MIRROR_MASK_MISMATCH",
        "HELPER_OUTPUT_INCONSISTENT",
        "SOURCE_METRIC_RECOMPUTATION_EXCEPTION",
    }
    if value.get("contradiction_kind") not in contradiction_kinds:
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    if (
        (exception_class is not None and mirror is not None)
        or (exception_class is None and mirror is None)
        or (
            mirror is False
            and value.get("contradiction_kind")
            not in {"MIRROR_MASK_MISMATCH", "MIRROR_IMPLEMENTATION_EXCEPTION"}
        )
        or (
            value.get("contradiction_kind")
            in {"MIRROR_MASK_MISMATCH", "MIRROR_IMPLEMENTATION_EXCEPTION"}
            and mirror is not False
        )
    ):
        _fail("R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID")
    replay_class = value.get("premask_replay_class")
    mechanism = value.get("mask_collapse_mechanism")
    action = value.get("selected_next_action")
    if (
        replay_class not in PREMASK_CLASSES
        or mechanism not in MASK_MECHANISMS
        or action not in NEXT_ACTIONS
        or classify_premask_observation(value)
        != (replay_class, mechanism, action)
        or (
            replay_class == "DYNAMIC_DECODE_MASK_COLLAPSE"
            and mechanism == "NOT_APPLICABLE"
        )
        or (
            replay_class != "DYNAMIC_DECODE_MASK_COLLAPSE"
            and mechanism != "NOT_APPLICABLE"
        )
    ):
        _fail("R4D2_DIAGNOSTIC_CLASSIFICATION_SCHEMA_INVALID")


def validate_observation_document(value: Mapping[str, Any]) -> None:
    _safe_document(value, OBSERVATION_KEYS)
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_r4d2_premask_replay_observation_v1"
        or value.get("status")
        != "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED"
        or type(value.get("execution_commit")) is not str
        or COMMIT_RE.fullmatch(str(value["execution_commit"])) is None
    ):
        _fail("R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID")
    _validate_technical_document(value)


def validate_aggregate_document(value: Mapping[str, Any]) -> None:
    _safe_document(value, AGGREGATE_KEYS)
    exact = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r4d2_premask_replay_aggregate_safe_v1",
        "status": "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED",
        "starting_commit": STARTING_COMMIT,
        "r4_attempt_inventory_before_sha256": FROZEN_METADATA_STAT_SHA256,
        "r4_attempt_inventory_after_sha256": FROZEN_METADATA_STAT_SHA256,
        "r4_attempt_preserved_immutable": True,
        "batch_1_terminal_receipt_unchanged": True,
        "batch_2_terminal_receipt_unchanged": True,
        "batch_3_authorities_unchanged": True,
        "replay_input_authority": "PASS",
        "historical_file_device_authority": "PASS_EXACT_CURRENT_DEVICE",
        "current_mount_authority": "PASS",
        "unique_dicom_objects_accessed": 1,
        "local_dicom_read_passes": 1,
        "pydicom_decode_invocations": 1,
        "affected_study_valid_cines": EXPECTED_AFFECTED_STUDY_ORDINARY_CINES,
        "affected_study_imaging_coverage": "RETAINED",
        "source_object_substitution_required": False,
        "whole_batch_zero_failure_policy": "UNCHANGED_FAIL_CLOSED",
        "observation_receipt_basename": "premask_replay_observation.restricted.json",
        "diagnostic_npz_created": False,
        "cloud_requests": 0,
        "object_downloads": 0,
        "qsub_submissions": 0,
        "gpu_executions": 0,
        "echoprime_executions": 0,
        "embedding_generations": 0,
        "model_fitting": 0,
        "prediction_generation": 0,
        "confirmatory_performance_accessed": False,
    }
    if any(
        type(value.get(key)) is not type(expected)
        or value.get(key) != expected
        for key, expected in exact.items()
    ):
        _fail("R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID")
    if (
        type(value.get("execution_commit")) is not str
        or COMMIT_RE.fullmatch(str(value["execution_commit"])) is None
        or type(value.get("observation_receipt_bytes")) is not int
        or not 1 <= int(value["observation_receipt_bytes"]) <= MAXIMUM_JSON_BYTES
        or type(value.get("observation_receipt_sha256")) is not str
        or SHA256_RE.fullmatch(str(value["observation_receipt_sha256"])) is None
    ):
        _fail("R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID")
    _validate_technical_document(value)


def _create_root(
    preflight: PremaskReplayPreflight,
) -> tuple[Path, r3e.PrivateDirectoryIdentity]:
    try:
        return r3e._create_diagnostic_root(preflight)  # type: ignore[arg-type]
    except Exception as exc:
        translated = _translate(exc, "R4D2_DIAGNOSTIC_ROOT_CREATE_FAILED")
        translated.diagnostic_root_created = bool(
            getattr(exc, "diagnostic_root_created", False)
        )
        raise translated from exc


def _write_receipt(
    path: Path,
    value: Mapping[str, Any],
    *,
    parent_identity: r3e.PrivateDirectoryIdentity,
) -> tuple[int, str]:
    try:
        return r3e._write_json_no_clobber(
            path, value, expected_parent_identity=parent_identity
        )
    except Exception as exc:
        raise _translate(exc, "R4D2_DIAGNOSTIC_OUTPUT_WRITE_FAILED") from exc


def _reopen_json_receipt(
    path: Path,
    *,
    expected_keys: frozenset[str],
    expected_size: int,
    expected_sha256: str,
) -> dict[str, Any]:
    payload, digest = _stable_private_payload(
        path, expected_size=expected_size, expected_sha256=expected_sha256
    )
    value = _strict_json(payload)
    if expected_keys == OBSERVATION_KEYS:
        validate_observation_document(value)
    elif expected_keys == AGGREGATE_KEYS:
        validate_aggregate_document(value)
    else:
        _fail("R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID")
    if digest != expected_sha256:
        _fail("R4D2_DIAGNOSTIC_RECEIPT_BINDING_INVALID")
    return value


def _validate_output_topology(root: Path) -> None:
    files: list[Path] = []
    directories = 0
    for current_text, child_directories, child_files in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_text)
        directories += 1
        info = os.lstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or not core.owner_private_directory_mode_ok(info.st_mode)
        ):
            _fail("R4D2_DIAGNOSTIC_OUTPUT_TOPOLOGY_INVALID")
        for name in child_directories:
            if stat.S_ISLNK(os.lstat(current / name).st_mode):
                _fail("R4D2_DIAGNOSTIC_OUTPUT_TOPOLOGY_INVALID")
        for name in child_files:
            child = current / name
            item = os.lstat(child)
            if (
                not stat.S_ISREG(item.st_mode)
                or item.st_uid != os.geteuid()
                or item.st_nlink != 1
                or stat.S_IMODE(item.st_mode) != 0o600
            ):
                _fail("R4D2_DIAGNOSTIC_OUTPUT_TOPOLOGY_INVALID")
            files.append(child)
    if directories != 1 or {path.name for path in files} != {
        "premask_replay_observation.restricted.json",
        "premask_replay.aggregate_safe.json",
    }:
        _fail("R4D2_DIAGNOSTIC_OUTPUT_TOPOLOGY_INVALID")


def run_execute(
    *,
    preflight: PremaskReplayPreflight | None = None,
    preflight_loader: Callable[[], PremaskReplayPreflight] = run_preflight,
    source_reader: Callable[..., tuple[bytes, str]] = _read_source_once,
    decoder: Callable[..., tuple[np.ndarray, dict[str, str]]] = _decode_once,
    observation_computer: Callable[..., dict[str, Any]] = compute_premask_observation,
    inventory_loader: Callable[[Path], R4Inventory] = r4_metadata_inventory,
    cv2_module: Any | None = None,
) -> dict[str, Any]:
    effective = preflight if preflight is not None else preflight_loader()
    counters = AccessCounters(allowed_source=effective.source_path)
    root_created = False
    try:
        root, root_identity = _create_root(effective)
        root_created = True
        _revalidate_source(effective)
        payload, digest = source_reader(
            effective.source_path,
            expected_identity=effective.source_identity,
            counters=counters,
        )
        if digest != effective.local_sha256:
            _fail("R4D2_LOCAL_CONTENT_SHA256_MISMATCH")
        _revalidate_source(effective)
        frames, decode_metadata = decoder(
            payload,
            source_path=effective.source_path,
            counters=counters,
        )
        _validate_decode_authority(frames, decode_metadata, effective.failed_row)
        if (
            counters.unique_objects != 1
            or counters.local_read_passes != 1
            or counters.decode_invocations != 1
        ):
            _fail("R4D2_ONE_OBJECT_EXECUTION_COUNTERS_INVALID")
        if cv2_module is None:
            import cv2

            cv2.setNumThreads(1)
            active_cv2 = cv2
        else:
            active_cv2 = cv2_module
        computed = observation_computer(frames, cv2_module=active_cv2)
        replay_class = computed.get("premask_replay_class")
        mechanism = computed.get("mask_collapse_mechanism")
        action = computed.get("selected_next_action")
        if replay_class not in PREMASK_CLASSES or mechanism not in MASK_MECHANISMS or action not in NEXT_ACTIONS:
            _fail("R4D2_CLOSED_CLASSIFICATION_INVALID")
        observation = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_r4d2_premask_replay_observation_v1",
            "status": "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED",
            "execution_commit": effective.execution_commit,
            "source_frame_count": int(frames.shape[0]),
            "decode_color_status": "PASS",
            **decode_metadata,
            **computed,
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
        }
        validate_observation_document(observation)
        observation_size, observation_sha256 = _write_receipt(
            root / "premask_replay_observation.restricted.json",
            observation,
            parent_identity=root_identity,
        )
        _reopen_json_receipt(
            root / "premask_replay_observation.restricted.json",
            expected_keys=OBSERVATION_KEYS,
            expected_size=observation_size,
            expected_sha256=observation_sha256,
        )
        _revalidate_source(effective)
        after = _post_validate_original_authority(
            effective, inventory_loader=inventory_loader
        )
        aggregate = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_r4d2_premask_replay_aggregate_safe_v1",
            "status": "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED",
            "starting_commit": STARTING_COMMIT,
            "execution_commit": effective.execution_commit,
            "r4_attempt_inventory_before_sha256": effective.inventory.metadata_stat_sha256,
            "r4_attempt_inventory_after_sha256": after.metadata_stat_sha256,
            "r4_attempt_preserved_immutable": True,
            "batch_1_terminal_receipt_unchanged": True,
            "batch_2_terminal_receipt_unchanged": True,
            "batch_3_authorities_unchanged": True,
            "replay_input_authority": "PASS",
            "historical_file_device_authority": "PASS_EXACT_CURRENT_DEVICE",
            "current_mount_authority": "PASS",
            "unique_dicom_objects_accessed": counters.unique_objects,
            "local_dicom_read_passes": counters.local_read_passes,
            "pydicom_decode_invocations": counters.decode_invocations,
            "source_frame_count": int(frames.shape[0]),
            "decode_color_status": "PASS",
            **decode_metadata,
            **computed,
            "affected_study_valid_cines": EXPECTED_AFFECTED_STUDY_ORDINARY_CINES,
            "affected_study_imaging_coverage": "RETAINED",
            "source_object_substitution_required": False,
            "whole_batch_zero_failure_policy": "UNCHANGED_FAIL_CLOSED",
            "observation_receipt_basename": "premask_replay_observation.restricted.json",
            "observation_receipt_bytes": observation_size,
            "observation_receipt_sha256": observation_sha256,
            "diagnostic_npz_created": False,
            "cloud_requests": 0,
            "object_downloads": 0,
            "qsub_submissions": 0,
            "gpu_executions": 0,
            "echoprime_executions": 0,
            "embedding_generations": 0,
            "model_fitting": 0,
            "prediction_generation": 0,
            "confirmatory_performance_accessed": False,
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
        }
        validate_aggregate_document(aggregate)
        aggregate_size, aggregate_sha256 = _write_receipt(
            root / "premask_replay.aggregate_safe.json",
            aggregate,
            parent_identity=root_identity,
        )
        _reopen_json_receipt(
            root / "premask_replay.aggregate_safe.json",
            expected_keys=AGGREGATE_KEYS,
            expected_size=aggregate_size,
            expected_sha256=aggregate_sha256,
        )
        _validate_output_topology(root)
        final_after = _post_validate_original_authority(
            effective, inventory_loader=inventory_loader
        )
        if final_after != after:
            _fail("R4D2_FINAL_AUTHORITY_REVALIDATION_CHANGED")
        result = dict(aggregate)
        result["_observation_receipt"] = {
            "basename": "premask_replay_observation.restricted.json",
            "bytes": observation_size,
            "sha256": observation_sha256,
        }
        result["_aggregate_receipt"] = {
            "basename": "premask_replay.aggregate_safe.json",
            "bytes": aggregate_size,
            "sha256": aggregate_sha256,
        }
        return result
    except PremaskReplayError as exc:
        exc.unique_objects = counters.unique_objects
        exc.local_read_passes = counters.local_read_passes
        exc.decode_invocations = counters.decode_invocations
        exc.diagnostic_root_created = root_created or exc.diagnostic_root_created
        raise
    except Exception as exc:
        error = PremaskReplayError(
            "R4D2_EXECUTION_UNEXPECTED_SANITIZED_FAILURE",
            unique_objects=counters.unique_objects,
            local_read_passes=counters.local_read_passes,
            decode_invocations=counters.decode_invocations,
            diagnostic_root_created=root_created,
        )
        raise error from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight-only", action="store_true")
    group.add_argument("--execute", action="store_true")
    return parser


def _effect_boundary(
    *,
    unique_objects: int,
    local_read_passes: int,
    decode_invocations: int,
    root_created: bool,
) -> None:
    print(f"UNIQUE_DICOM_OBJECTS_ACCESSED={unique_objects}")
    print(f"LOCAL_DICOM_READ_PASSES={local_read_passes}")
    print(f"PYDICOM_DECODE_INVOCATIONS={decode_invocations}")
    print(f"DIAGNOSTIC_ROOT_CREATED={'YES' if root_created else 'NO'}")
    print("IDENTIFIERS_EMITTED=0")
    print("LOCATORS_EMITTED=0")
    print("NEW_CLOUD_REQUESTS=0")
    print("NEW_OBJECT_DOWNLOADS=0")
    print("NEW_QSUB_SUBMISSIONS=0")
    print("NEW_GPU_EXECUTIONS=0")
    print("NEW_ECHOPRIME_EXECUTIONS=0")
    print("EMBEDDING_GENERATIONS=0")
    print("MODEL_FITTING=0")
    print("PREDICTION_GENERATION=0")
    print("CONFIRMATORY_PERFORMANCE_ACCESSED=NO")


def _display_count(value: object) -> str:
    return str(value) if type(value) is int else "NOT_EVALUATED"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.preflight_only:
            preflight = run_preflight()
        else:
            result = run_execute()
    except PremaskReplayError as exc:
        prefix = "R4D2_PREFLIGHT" if arguments.preflight_only else "R4D2_REPLAY"
        print(f"{prefix}=BLOCKED_{exc.code}")
        _effect_boundary(
            unique_objects=exc.unique_objects,
            local_read_passes=exc.local_read_passes,
            decode_invocations=exc.decode_invocations,
            root_created=exc.diagnostic_root_created,
        )
        return 78
    if arguments.preflight_only:
        print("R4D2_PREFLIGHT=PASS_ZERO_BODY_NO_ROOT")
        print(f"R4D2_EXECUTION_COMMIT={preflight.execution_commit}")
        print("R4D2_FROZEN_R4_INVENTORY=PASS_EXACT")
        print("R4D2_BATCH1_BATCH2_RECEIPTS=PASS_UNCHANGED")
        print("R4D2_BATCH3_ARTIFACTS=PASS_UNCHANGED")
        print("R4D2_FROZEN_CSV_BOOLEAN_DIALECT=PASS")
        print("R4D2_UNIQUE_FAILED_ROW=PASS")
        print("R4D2_SOURCE_PLAN_MEMBERSHIP=PASS")
        print("R4D2_DOWNLOAD_AUTHORITY=PASS")
        print("R4D2_HISTORICAL_FILE_DEVICE_AUTHORITY=PASS_EXACT_CURRENT_DEVICE")
        print("R4D2_CURRENT_MOUNT_AUTHORITY=PASS")
        _effect_boundary(
            unique_objects=0,
            local_read_passes=0,
            decode_invocations=0,
            root_created=False,
        )
        return 0
    print("R4D2_REPLAY=PASS_CLASSIFIED")
    print(f"R4D2_STARTING_COMMIT={STARTING_COMMIT}")
    print(f"R4D2_EXECUTION_COMMIT={result['execution_commit']}")
    print(f"R4D2_ATTEMPT_ID={ATTEMPT_ID}")
    print("R4D2_FAILED_BATCH=3")
    print("R4D2_FAILED_CINES=1")
    print(f"SOURCE_FRAME_COUNT={result['source_frame_count']}")
    print(f"DECODE_COLOR_STATUS={result['decode_color_status']}")
    for field, label in (
        ("photometric_interpretation", "PHOTOMETRIC_INTERPRETATION"),
        ("transfer_syntax_uid", "TRANSFER_SYNTAX_UID"),
        ("decoder_backend", "DECODER_BACKEND"),
        ("decoder_color_behavior", "DECODER_COLOR_BEHAVIOR"),
        ("color_transform", "COLOR_TRANSFORM"),
        ("canonical_color_space", "CANONICAL_COLOR_SPACE"),
    ):
        print(f"{label}={result[field]}")
    for field, label in (
        ("pre_mask_nonzero_pixel_count", "PREMASK_NONZERO_PIXELS"),
        (
            "pre_mask_adjacent_temporal_variation_pixel_count",
            "PREMASK_ADJACENT_TEMPORAL_VARIATION_PIXELS",
        ),
        ("first_last_difference_pixel_count", "FIRST_LAST_DIFFERENCE_PIXELS"),
        ("persistent_occupancy_pixel_count", "PERSISTENT_OCCUPANCY_PIXELS"),
        (
            "overlap_before_floodfill_pixel_count",
            "OVERLAP_BEFORE_FLOODFILL_PIXELS",
        ),
        ("generated_sector_pixel_count", "GENERATED_SECTOR_PIXELS"),
        ("post_mask_nonzero_pixel_count", "POSTMASK_NONZERO_PIXELS"),
        (
            "post_mask_adjacent_temporal_variation_pixel_count",
            "POSTMASK_TEMPORAL_VARIATION_PIXELS",
        ),
    ):
        print(f"{label}={_display_count(result[field])}")
    mirror = result["mask_mirror_equals_frozen_helper"]
    print(
        "MASK_MIRROR_EQUALS_FROZEN_HELPER="
        + ("YES" if mirror is True else "NO" if mirror is False else "NOT_EVALUATED")
    )
    print(
        "SOURCE_SECTOR_NONEMPTY_GATE="
        + ("PASS" if result["source_sector_nonempty_gate_passed"] else "FAIL")
    )
    print(
        "SOURCE_NONZERO_RETAINED_GATE="
        + ("PASS" if result["source_nonzero_retained_pixel_gate_passed"] else "FAIL")
    )
    print(
        "SOURCE_TEMPORAL_VARIATION_GATE="
        + ("PASS" if result["source_temporal_variation_gate_passed"] else "FAIL")
    )
    print(f"PREMASK_REPLAY_CLASS={result['premask_replay_class']}")
    print(f"MASK_COLLAPSE_MECHANISM={result['mask_collapse_mechanism']}")
    print(f"SELECTED_NEXT_ACTION={result['selected_next_action']}")
    for label, key in (
        ("PREMASK_OBSERVATION_RECEIPT", "_observation_receipt"),
        ("PREMASK_AGGREGATE_SAFE_RECEIPT", "_aggregate_receipt"),
    ):
        receipt = result[key]
        print(f"{label}_BASENAME={receipt['basename']}")
        print(f"{label}_BYTES={receipt['bytes']}")
        print(f"{label}_SHA256={receipt['sha256']}")
    print("R4_ATTEMPT_PRESERVED_IMMUTABLE=YES")
    print("DIAGNOSTIC_NPZ_CREATED=NO")
    print(f"AFFECTED_STUDY_VALID_CINES={EXPECTED_AFFECTED_STUDY_ORDINARY_CINES}")
    print("AFFECTED_STUDY_IMAGING_COVERAGE=RETAINED")
    print("WHOLE_BATCH_ZERO_FAILURE_POLICY=UNCHANGED_FAIL_CLOSED")
    _effect_boundary(
        unique_objects=int(result["unique_dicom_objects_accessed"]),
        local_read_passes=int(result["local_dicom_read_passes"]),
        decode_invocations=int(result["pydicom_decode_invocations"]),
        root_created=True,
    )
    print("FULL_C3_STATUS=NO_GO_PENDING_SEPARATE_POSTREPLAY_POLICY_ACTION")
    print(f"EXACT_NEXT_ACTION={result['selected_next_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
