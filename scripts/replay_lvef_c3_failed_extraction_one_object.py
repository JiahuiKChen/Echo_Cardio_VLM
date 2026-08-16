#!/usr/bin/env python3
"""Replay only the retained Phase 1I Batch-2 failed DICOM preprocessing row.

This owner-reviewed future entrypoint is deliberately local and narrow.  It
does not contain a cloud client, scheduler submission, EchoPrime invocation,
GPU path, model, prediction, or performance interface.  It reuses the shared
production ``_extract_one`` implementation and never writes into the original
attempt.  The live replay is not authorized merely because this file exists.
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


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_reconstruction_smoke as reconstruction
import lvef_c3_full_sequential as full_sequential
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as production_stages


EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
ALLOWED_DIAGNOSTIC_PREFIX = PRODUCTION_ROOT / "owner_private"
APPROVED_RESEARCH_MOUNT_TARGET = Path("/restricted/projectnb")
APPROVED_RESEARCH_FILESYSTEM_TYPE = "nfs"
APPROVED_RESEARCH_FILESYSTEM_ROOT = "/"
FINDMNT_PATH = Path("/usr/bin/findmnt")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
DIAGNOSTIC_NAME_RE = re.compile(
    r"^lvef_c3_r3e_one_object_replay_[a-z0-9][a-z0-9_-]{7,63}$"
)
MAXIMUM_MANIFEST_BYTES = 512 * 1024 * 1024
MAXIMUM_REPAIRED_NPZ_BYTES = 32 * 1024 * 1024
MAXIMUM_RECEIPT_BYTES = 64 * 1024
CONTRACT_PATH = REPOSITORY_ROOT / "configs/lvef_c3_orchestration_v2.yaml"


@dataclass(frozen=True)
class OriginalAttemptAuthority:
    attempt_id: str
    execution_commit: str
    batch_id: str
    file_count: int
    total_bytes: int
    opaque_d4_metadata_tree_sha256: str
    extraction_rows: int
    successful_extraction_rows: int
    download_rows: int
    batch_plan_sha256_prefix: str
    root_mode: int
    full_kind_mode_histogram: tuple[tuple[str, int, int], ...]
    exceptional_kind_mode_role_histogram: tuple[tuple[str, int, str, int], ...]
    historical_device_must_differ: bool


ORIGINAL_AUTHORITY = OriginalAttemptAuthority(
    attempt_id="lvef_c3_full_d574f21c760a5679_b805fd1a",
    execution_commit="b805fd1a403b3ff0503d09bb79d35b01805dd765",
    batch_id="c3_batch_001",
    file_count=158_288,
    total_bytes=151_954_116_217,
    # This is an opaque owner-frozen D4 authority.  This script intentionally
    # does not claim to know or reproduce the D4 preimage algorithm.
    opaque_d4_metadata_tree_sha256=(
        "800ff8fa66949a919d00bf3f66b6aec16c3c244ff48e68dc47d866c924684bb3"
    ),
    extraction_rows=9_937,
    successful_extraction_rows=9_936,
    download_rows=18_196,
    batch_plan_sha256_prefix="d574f21c760a5679",
    root_mode=0o2700,
    full_kind_mode_histogram=(
        ("directory", 0o2700, 287),
        ("file", 0o600, 158_268),
        ("file", 0o644, 20),
    ),
    exceptional_kind_mode_role_histogram=(
        ("file", 0o644, "scheduler_log", 20),
    ),
    historical_device_must_differ=True,
)


@dataclass(frozen=True)
class ReplayRowAuthority:
    selected_source: Path
    selected_source_sha256: str
    source_metadata: Path


@dataclass(frozen=True)
class PrivateDirectoryIdentity:
    path: Path
    device: int
    inode: int
    group: int
    mode: int


@dataclass(frozen=True)
class SourceFileIdentity:
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class LegacyRootIdentity:
    path: Path
    device: int
    inode: int
    group: int
    mode: int
    nlink: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class CurrentMountAuthority:
    status: str
    identity_sha256: str


@dataclass(frozen=True)
class HistoricalDeviceReconciliation:
    status: str
    historical_and_current_differ: bool


@dataclass(frozen=True)
class ReplayPreflight:
    attempt_root: Path
    original_inventory: Mapping[str, Any]
    failed_row: Mapping[str, str]
    download_row: Mapping[str, str]
    planned_object: Mapping[str, Any]
    source_path: Path
    source_identity: SourceFileIdentity
    attempt_root_identity: LegacyRootIdentity
    mount_authority: CurrentMountAuthority
    device_reconciliation: HistoricalDeviceReconciliation
    objects_root: Path
    local_sha256: str
    diagnostic_root: Path
    diagnostic_parent: PrivateDirectoryIdentity


@dataclass
class ReplayAccessCounters:
    allowed_source: Path
    accessed_objects: set[Path] = field(default_factory=set)
    local_content_hash_passes: int = 0
    pydicom_decode_invocations: int = 0

    def _register(self, path: Path) -> None:
        canonical = Path(os.path.abspath(path))
        if canonical != self.allowed_source:
            _fail("REPLAY_UNIQUE_OBJECT_SCOPE_EXCEEDED")
        self.accessed_objects.add(canonical)
        if len(self.accessed_objects) != 1:
            _fail("REPLAY_UNIQUE_OBJECT_SCOPE_EXCEEDED")

    def register_hash(self, path: Path) -> None:
        self._register(path)
        self.local_content_hash_passes += 1

    def register_decode(self, path: Path) -> None:
        self._register(path)
        self.pydicom_decode_invocations += 1

    @property
    def unique_dicom_objects_accessed(self) -> int:
        return len(self.accessed_objects)

    @property
    def dicom_body_read_calls(self) -> int:
        """Count body-reading passes without conflating them with objects."""

        return self.local_content_hash_passes + self.pydicom_decode_invocations


LEGACY_B805_EXTRACTION_MANIFEST_HEADER = (
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
    "sampled_nonzero_retained_pixel_count",
    "sampled_nonzero_retained_pixel_gate_passed",
    "sampled_temporal_variation_pixel_count",
    "sampled_temporal_variation_gate_passed",
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

VERIFIED_DOWNLOAD_MANIFEST_HEADER = (
    "subject_id",
    "study_id",
    "source_relative_path",
    "download_ok",
    "observed_sha256",
    "physical_source_key",
)

DOWNLOAD_VERIFICATION_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "source_object_key",
        "size_bytes",
        "generation",
        "md5_base64",
        "crc32c_base64",
        "local_sha256",
        "file_device",
        "file_inode",
        "file_mtime_ns",
        "digest_backend",
        "digest_chunk_size_bytes",
    }
)

LEGACY_FAILURE_SUMMARY_KEYS = frozenset(
    {
        "status",
        "n_objects",
        "n_studies",
        "n_readable",
        "n_unreadable",
        "n_multiframe_candidates",
        "n_single_frame",
        "n_pixel_decode_failures",
        "physical_source_keys_unique",
        "identifiers_emitted",
        "paths_emitted",
    }
)

SAFE_TECHNICAL_PROVENANCE_FIELDS = (
    "write_ok",
    "pixel_decode_ok",
    "mask_status",
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
    "ordinary_sampled_nonzero_retained_pixel_count",
    "ordinary_sampled_nonzero_retained_pixel_gate_passed",
    "ordinary_sampled_temporal_variation_pixel_count",
    "ordinary_sampled_temporal_variation_gate_passed",
    "post_crop_nonzero_retained_pixel_count",
    "post_crop_nonzero_retained_pixel_gate_passed",
    "post_crop_temporal_variation_pixel_count",
    "post_crop_temporal_variation_gate_passed",
    "sampled_nonzero_retained_pixel_count",
    "sampled_nonzero_retained_pixel_gate_passed",
    "sampled_temporal_variation_pixel_count",
    "sampled_temporal_variation_gate_passed",
    "encoder_visible_nonzero_retained_pixel_count",
    "encoder_visible_nonzero_retained_pixel_gate_passed",
    "encoder_visible_temporal_variation_pixel_count",
    "encoder_visible_temporal_variation_gate_passed",
    "selected_preprocessing_path",
    "temporal_sampling_policy",
    "fallback_status",
    "failure_substage",
    "frames_shape",
    "frames_dtype",
    "frames_sha256",
    "sampled_indices_sha256",
    "source_num_frames_sha256",
    "npz_sha256",
    "source_num_frames",
    "error_code",
)
DECODER_COLOR_AUTHORITY_FIELDS = (
    "photometric_interpretation",
    "transfer_syntax_uid",
    "decoder_backend",
    "decoder_color_behavior",
    "color_transform",
    "canonical_color_space",
)

REPAIRED_REQUIRED_FIELDS = frozenset(
    {
        "write_ok",
        "mask_status",
        "decode_color_status",
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
        "temporal_sampling_policy",
        "fallback_status",
        "failure_substage",
        "frames_shape",
        "frames_dtype",
        "frames_sha256",
        "sampled_indices_sha256",
        "source_num_frames_sha256",
        "npz_sha256",
        "source_num_frames",
        "error_code",
        "clip_key",
        "output_relative_path",
        *DECODER_COLOR_AUTHORITY_FIELDS,
    }
)
REPAIRED_COUNT_GATE_PAIRS = (
    ("source_sector_pixel_count", "source_sector_nonempty_gate_passed"),
    (
        "source_nonzero_retained_pixel_count",
        "source_nonzero_retained_pixel_gate_passed",
    ),
    ("source_temporal_variation_pixel_count", "source_temporal_variation_gate_passed"),
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
    ("post_crop_temporal_variation_pixel_count", "post_crop_temporal_variation_gate_passed"),
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
    ("sampled_temporal_variation_pixel_count", "sampled_temporal_variation_gate_passed"),
    (
        "encoder_visible_nonzero_retained_pixel_count",
        "encoder_visible_nonzero_retained_pixel_gate_passed",
    ),
    (
        "encoder_visible_temporal_variation_pixel_count",
        "encoder_visible_temporal_variation_gate_passed",
    ),
)


class ReplayError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        dicom_body_reads: int = 0,
        unique_dicom_objects_accessed: int = 0,
        local_content_hash_passes: int = 0,
        pydicom_decode_invocations: int = 0,
        diagnostic_root_created: bool = False,
    ):
        super().__init__(code)
        self.code = code
        self.dicom_body_reads = dicom_body_reads
        self.unique_dicom_objects_accessed = unique_dicom_objects_accessed
        self.local_content_hash_passes = local_content_hash_passes
        self.pydicom_decode_invocations = pydicom_decode_invocations
        self.diagnostic_root_created = diagnostic_root_created


def _attach_runtime_state(
    error: ReplayError,
    *,
    counters: ReplayAccessCounters,
    diagnostic_root_created: bool,
) -> ReplayError:
    error.unique_dicom_objects_accessed = counters.unique_dicom_objects_accessed
    error.local_content_hash_passes = counters.local_content_hash_passes
    error.pydicom_decode_invocations = counters.pydicom_decode_invocations
    error.dicom_body_reads = counters.dicom_body_read_calls
    error.diagnostic_root_created = (
        diagnostic_root_created or error.diagnostic_root_created
    )
    return error


def _fail(code: str, *, dicom_body_reads: int = 0) -> None:
    if SAFE_CODE_RE.fullmatch(code) is None:
        code = "ONE_OBJECT_REPLAY_INTERNAL_CODE_INVALID"
    raise ReplayError(code, dicom_body_reads=dicom_body_reads)


def _strict_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("ONE_OBJECT_REPLAY_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _safe_relative(value: object) -> str:
    text = str(value)
    path = PurePosixPath(text)
    if (
        not text
        or text != text.strip()
        or text.startswith(("/", "~"))
        or "\\" in text
        or any(ord(character) < 32 for character in text)
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        _fail("ONE_OBJECT_REPLAY_UNSAFE_RELATIVE_PATH")
    return text


def _require_no_symlink_components(path: Path) -> None:
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _fail("ONE_OBJECT_REPLAY_PATH_NOT_CANONICAL")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if os.path.lexists(cursor) and stat.S_ISLNK(os.lstat(cursor).st_mode):
            _fail("ONE_OBJECT_REPLAY_PATH_SYMLINK")


def _read_owner_private_file(path: Path, *, maximum_bytes: int) -> bytes:
    _require_no_symlink_components(path)
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ReplayError("ONE_OBJECT_REPLAY_PRIVATE_FILE_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_uid,
            stat.S_IMODE(before.st_mode),
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            _fail("ONE_OBJECT_REPLAY_PRIVATE_FILE_INVALID")
        remaining = before.st_size
        blocks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 1_048_576))
            if not block:
                _fail("ONE_OBJECT_REPLAY_PRIVATE_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_uid,
            stat.S_IMODE(after.st_mode),
        ):
            _fail("ONE_OBJECT_REPLAY_PRIVATE_FILE_CHANGED_DURING_READ")
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
    except ReplayError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayError("ONE_OBJECT_REPLAY_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("ONE_OBJECT_REPLAY_JSON_NOT_OBJECT")
    return value


def _read_json(path: Path, *, maximum_bytes: int = 1_048_576) -> dict[str, Any]:
    payload = _read_owner_private_file(path, maximum_bytes=maximum_bytes)
    return _decode_json_object(payload)


def _read_json_with_sha256(
    path: Path, *, maximum_bytes: int = 1_048_576
) -> tuple[dict[str, Any], str]:
    """Parse strict JSON and hash the exact bytes from the same stable inode."""

    payload = _read_owner_private_file(path, maximum_bytes=maximum_bytes)
    return _decode_json_object(payload), hashlib.sha256(payload).hexdigest()


def _decode_csv_exact(
    payload: bytes, header: Sequence[str]
) -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames != list(header) or len(set(reader.fieldnames)) != len(
            reader.fieldnames
        ):
            _fail("ONE_OBJECT_REPLAY_CSV_SCHEMA_MISMATCH")
        rows = list(reader)
    except ReplayError:
        raise
    except (UnicodeError, csv.Error) as exc:
        raise ReplayError("ONE_OBJECT_REPLAY_CSV_INVALID") from exc
    if not rows or any(None in row for row in rows):
        _fail("ONE_OBJECT_REPLAY_CSV_INVALID")
    return rows


def _read_csv_exact(path: Path, header: Sequence[str]) -> list[dict[str, str]]:
    payload = _read_owner_private_file(path, maximum_bytes=MAXIMUM_MANIFEST_BYTES)
    return _decode_csv_exact(payload, header)


def _read_csv_exact_with_sha256(
    path: Path, header: Sequence[str]
) -> tuple[list[dict[str, str]], str]:
    """Parse CSV and hash the exact bytes from the same stable inode."""

    payload = _read_owner_private_file(path, maximum_bytes=MAXIMUM_MANIFEST_BYTES)
    return _decode_csv_exact(payload, header), hashlib.sha256(payload).hexdigest()


def _strict_bool(value: object) -> bool:
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    _fail("ONE_OBJECT_REPLAY_BOOLEAN_INVALID")


def _sha256_owner_private_regular(
    path: Path,
    *,
    invalid_code: str,
    dicom_body_reads: int,
    maximum_bytes: int | None = None,
    expected_identity: SourceFileIdentity | None = None,
) -> str:
    _require_no_symlink_components(path)
    digest = hashlib.sha256()
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ReplayError(
            invalid_code, dicom_body_reads=dicom_body_reads
        ) from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_uid,
            stat.S_IMODE(before.st_mode),
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or before.st_size < 1
            or (maximum_bytes is not None and before.st_size > maximum_bytes)
            or (
                expected_identity is not None
                and (
                    before.st_dev != expected_identity.device
                    or before.st_ino != expected_identity.inode
                    or before.st_size != expected_identity.size
                    or before.st_mtime_ns != expected_identity.mtime_ns
                    or before.st_ctime_ns != expected_identity.ctime_ns
                )
            )
        ):
            _fail(invalid_code, dicom_body_reads=dicom_body_reads)
        for block in iter(lambda: os.read(descriptor, 1_048_576), b""):
            digest.update(block)
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_uid,
            stat.S_IMODE(after.st_mode),
        ):
            _fail(invalid_code, dicom_body_reads=dicom_body_reads)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _sha256_file(path: Path, *, expected_identity: SourceFileIdentity) -> str:
    return _sha256_owner_private_regular(
        path,
        invalid_code="REPLAY_CURRENT_FILE_IDENTITY_CHANGED",
        dicom_body_reads=1,
        expected_identity=expected_identity,
    )


def _git_value(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        },
    )
    if result.returncode != 0:
        _fail("ONE_OBJECT_REPLAY_GIT_AUTHORITY_UNAVAILABLE")
    return result.stdout.strip()


def validate_git_authority(
    repository: Path, governing_commit: str, *, original_commit: str
) -> None:
    if COMMIT_RE.fullmatch(governing_commit) is None:
        _fail("ONE_OBJECT_REPLAY_GOVERNING_COMMIT_INVALID")
    if repository.is_symlink() or not repository.is_dir():
        _fail("ONE_OBJECT_REPLAY_REPOSITORY_INVALID")
    head = _git_value(repository, "rev-parse", "HEAD")
    origin = _git_value(
        repository,
        "rev-parse",
        "refs/remotes/origin/codex/lvef-multitask-revalidation",
    )
    if (
        head != governing_commit
        or origin != governing_commit
        or _git_value(repository, "branch", "--show-current") != EXPECTED_BRANCH
        or _git_value(repository, "status", "--porcelain", "--untracked-files=no")
    ):
        _fail("ONE_OBJECT_REPLAY_GIT_AUTHORITY_MISMATCH")
    ancestor = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            original_commit,
            governing_commit,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    if ancestor.returncode != 0:
        _fail("ONE_OBJECT_REPLAY_ORIGINAL_COMMIT_NOT_ANCESTOR")


def _legacy_exception_role(relative: str, kind: str) -> str:
    path = PurePosixPath(relative)
    name = path.name.lower()
    parts = tuple(part.lower() for part in path.parts)
    if kind == "file" and "raw" in parts and name.endswith(".dcm"):
        return "raw_dicom"
    if kind == "file" and "extracted_cache" in parts and name.endswith(".npz"):
        return "extracted_npz"
    if kind == "file" and any("embedding" in part for part in parts):
        return "embedding_artifact"
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
        return "replay_authority_file"
    if (
        kind == "file"
        and len(parts) == 2
        and parts[0] == "scheduler"
        and re.fullmatch(r".+[.]o[0-9]+(?:[.][0-9]+)?", name)
    ):
        return "scheduler_log"
    if kind == "directory":
        return "stage_directory"
    return "other"


def _attempt_metadata_inventory(attempt_root: Path) -> dict[str, Any]:
    """Return an aggregate-only stable legacy metadata inventory without bodies."""

    try:
        _require_no_symlink_components(attempt_root)
        root_before = os.lstat(attempt_root)
    except (OSError, ReplayError) as exc:
        raise ReplayError("REPLAY_ORIGINAL_ATTEMPT_ROOT_INVALID") from exc
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    symlink_count = 0
    nonregular_count = 0
    owner_mismatch_count = 0
    path_escape_count = 0
    cross_device_count = 0
    identity_instability_count = 0
    group_other_write_count = 0
    unapproved_special_count = 0
    regular_nlink_anomaly_count = 0
    duplicate_inode_count = 0
    sensitive_exception_count = 0
    kind_mode: Counter[tuple[str, int]] = Counter()
    exception_role: Counter[tuple[str, int, str]] = Counter()
    seen_inodes: set[tuple[int, int]] = set()
    directory_authority: dict[str, tuple[int, int]] = {}

    def observe(child: Path, relative: str) -> None:
        nonlocal file_count, total_bytes, symlink_count, nonregular_count
        nonlocal owner_mismatch_count, path_escape_count, cross_device_count
        nonlocal identity_instability_count, group_other_write_count
        nonlocal unapproved_special_count, regular_nlink_anomaly_count
        nonlocal duplicate_inode_count, sensitive_exception_count
        try:
            before = os.lstat(child)
            after = os.lstat(child)
        except OSError as exc:
            raise ReplayError("REPLAY_ORIGINAL_ATTEMPT_PATH_ESCAPE") from exc
        identity = lambda item: (
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
        if identity(before) != identity(after):
            identity_instability_count += 1
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISDIR(before.st_mode):
            kind = "directory"
        elif stat.S_ISREG(before.st_mode):
            kind = "file"
            file_count += 1
            total_bytes += before.st_size
        elif stat.S_ISLNK(before.st_mode):
            kind = "symlink"
            symlink_count += 1
        else:
            kind = "nonregular"
            nonregular_count += 1
        if before.st_uid != os.geteuid():
            owner_mismatch_count += 1
        if before.st_dev != root_before.st_dev:
            cross_device_count += 1
        try:
            child.relative_to(attempt_root)
        except ValueError:
            path_escape_count += 1
        if kind in {"file", "directory"}:
            inode = (int(before.st_dev), int(before.st_ino))
            if inode in seen_inodes:
                duplicate_inode_count += 1
            seen_inodes.add(inode)
        if kind == "file" and before.st_nlink != 1:
            regular_nlink_anomaly_count += 1
        if mode & 0o022:
            group_other_write_count += 1
        if mode & 0o5000 or (mode & 0o2000 and kind != "directory"):
            unapproved_special_count += 1
        if kind == "directory":
            directory_authority[relative] = (mode, int(before.st_gid))
            if mode & 0o2000 and relative != ".":
                parent = PurePosixPath(relative).parent.as_posix() or "."
                parent_mode, parent_gid = directory_authority.get(parent, (0, -1))
                if not (parent_mode & 0o2000) or parent_gid != before.st_gid:
                    unapproved_special_count += 1
        kind_mode[(kind, mode)] += 1
        if mode & 0o077:
            role = _legacy_exception_role(relative, kind)
            exception_role[(kind, mode, role)] += 1
            if role in {
                "raw_dicom",
                "extracted_npz",
                "embedding_artifact",
                "replay_authority_file",
            } or re.search(
                r"credential|token|requester[-_]?pays|oauth|service[-_]?account|"
                r"application[-_]?default|secret|access[-_]?key",
                relative,
                re.IGNORECASE,
            ):
                sensitive_exception_count += 1
        record = {
            "relative": relative,
            "kind": kind,
            "mode": mode,
            "uid": before.st_uid,
            "gid": before.st_gid,
            "st_dev": before.st_dev,
            "st_ino": before.st_ino,
            "st_nlink": before.st_nlink,
            "size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "st_ctime_ns": before.st_ctime_ns,
        }
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )

    observe(attempt_root, ".")

    def walk_error(_error: OSError) -> None:
        _fail("REPLAY_ORIGINAL_ATTEMPT_PATH_ESCAPE")

    for current_text, directories, filenames in os.walk(
        attempt_root, topdown=True, followlinks=False, onerror=walk_error
    ):
        current = Path(current_text)
        directories.sort()
        filenames.sort()
        for name in directories + filenames:
            child = current / name
            observe(child, child.relative_to(attempt_root).as_posix())
    try:
        root_after = os.lstat(attempt_root)
    except OSError as exc:
        raise ReplayError("REPLAY_ORIGINAL_ATTEMPT_ROOT_INVALID") from exc
    root_fields = lambda item: (
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
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "root_mode": stat.S_IMODE(root_before.st_mode),
        "root_kind_valid": stat.S_ISDIR(root_before.st_mode),
        "root_owner_valid": root_before.st_uid == os.geteuid(),
        "root_identity_stable": root_fields(root_before) == root_fields(root_after),
        "symlink_count": symlink_count,
        "nonregular_count": nonregular_count,
        "owner_mismatch_count": owner_mismatch_count,
        "path_escape_count": path_escape_count,
        "cross_device_count": cross_device_count,
        "identity_instability_count": identity_instability_count,
        "group_other_write_count": group_other_write_count,
        "unapproved_special_count": unapproved_special_count,
        "regular_nlink_anomaly_count": regular_nlink_anomaly_count,
        "duplicate_inode_count": duplicate_inode_count,
        "sensitive_exception_count": sensitive_exception_count,
        "full_kind_mode_histogram": tuple(
            (kind, mode, count)
            for (kind, mode), count in sorted(kind_mode.items())
        ),
        "exceptional_kind_mode_role_histogram": tuple(
            (kind, mode, role, count)
            for (kind, mode, role), count in sorted(exception_role.items())
        ),
        "runtime_metadata_stat_snapshot_sha256": digest.hexdigest(),
    }


def _validate_original_inventory(
    observed: Mapping[str, Any], authority: OriginalAttemptAuthority
) -> None:
    snapshot_sha256 = observed.get("runtime_metadata_stat_snapshot_sha256")
    if observed.get("root_mode") != authority.root_mode or not observed.get(
        "root_kind_valid"
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_EFFECTIVE_PRIVACY_INVALID")
    if not observed.get("root_owner_valid") or observed.get(
        "owner_mismatch_count"
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_OWNER_MISMATCH")
    if observed.get("symlink_count") or observed.get("nonregular_count"):
        _fail("REPLAY_ORIGINAL_ATTEMPT_SYMLINK_OR_NONREGULAR")
    if observed.get("path_escape_count") or observed.get("cross_device_count"):
        _fail("REPLAY_ORIGINAL_ATTEMPT_PATH_ESCAPE")
    if observed.get("group_other_write_count"):
        _fail("REPLAY_ORIGINAL_ATTEMPT_GROUP_OTHER_WRITE_INVALID")
    if observed.get("unapproved_special_count"):
        _fail("REPLAY_ORIGINAL_ATTEMPT_SPECIAL_BITS_INVALID")
    if observed.get("regular_nlink_anomaly_count") or observed.get(
        "duplicate_inode_count"
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_TOPOLOGY_INVALID")
    if not observed.get("root_identity_stable") or observed.get(
        "identity_instability_count"
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_IDENTITY_MUTATION")
    if observed.get("full_kind_mode_histogram") != authority.full_kind_mode_histogram:
        _fail("REPLAY_ORIGINAL_ATTEMPT_MODE_HISTOGRAM_MISMATCH")
    if (
        observed.get("exceptional_kind_mode_role_histogram")
        != authority.exceptional_kind_mode_role_histogram
        or observed.get("sensitive_exception_count") != 0
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_EFFECTIVE_PRIVACY_INVALID")
    if observed.get("file_count") != authority.file_count or observed.get(
        "total_bytes"
    ) != authority.total_bytes:
        _fail("REPLAY_ORIGINAL_ATTEMPT_INVENTORY_MISMATCH")
    if not isinstance(snapshot_sha256, str) or SHA256_RE.fullmatch(
        snapshot_sha256
    ) is None:
        _fail("REPLAY_ORIGINAL_ATTEMPT_INVENTORY_MISMATCH")


def _legacy_root_identity(
    attempt_root: Path, authority: OriginalAttemptAuthority
) -> LegacyRootIdentity:
    try:
        _require_no_symlink_components(attempt_root)
        before = os.lstat(attempt_root)
        descriptor = os.open(
            attempt_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except (OSError, ReplayError) as exc:
        raise ReplayError("REPLAY_ORIGINAL_ATTEMPT_ROOT_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = lambda item: (
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
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != authority.root_mode
        or fields(before) != fields(opened)
        or fields(opened) != fields(after)
    ):
        _fail("REPLAY_ORIGINAL_ATTEMPT_ROOT_INVALID")
    return LegacyRootIdentity(
        path=attempt_root,
        device=int(opened.st_dev),
        inode=int(opened.st_ino),
        group=int(opened.st_gid),
        mode=stat.S_IMODE(opened.st_mode),
        nlink=int(opened.st_nlink),
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
    )


def validate_immutable_legacy_attempt_effective_privacy(
    attempt_root: Path, authority: OriginalAttemptAuthority
) -> tuple[dict[str, Any], LegacyRootIdentity]:
    root_before = _legacy_root_identity(attempt_root, authority)
    first = _attempt_metadata_inventory(attempt_root)
    _validate_original_inventory(first, authority)
    second = _attempt_metadata_inventory(attempt_root)
    _validate_original_inventory(second, authority)
    root_after = _legacy_root_identity(attempt_root, authority)
    if first != second or root_before != root_after:
        _fail("REPLAY_ORIGINAL_ATTEMPT_IDENTITY_MUTATION")
    return second, root_after


def _validate_failure_summary(value: Mapping[str, Any]) -> None:
    if (
        frozenset(value) != LEGACY_FAILURE_SUMMARY_KEYS
        or value.get("status") != "FAIL_DICOM_OR_PIXEL_DECODE_GATE"
        or value.get("n_objects") != 18_196
        or value.get("n_studies") != 250
        or value.get("n_readable") != 18_196
        or value.get("n_unreadable") != 0
        or value.get("n_multiframe_candidates") != 9_937
        or value.get("n_single_frame") != 8_259
        or value.get("n_pixel_decode_failures") != 1
        or value.get("physical_source_keys_unique") is not True
        or value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
    ):
        _fail("ONE_OBJECT_REPLAY_LEGACY_FAILURE_SUMMARY_MISMATCH")


def _validate_legacy_failed_row(
    rows: Sequence[Mapping[str, str]], authority: OriginalAttemptAuthority
) -> dict[str, str]:
    if len(rows) != authority.extraction_rows:
        _fail("ONE_OBJECT_REPLAY_LEGACY_EXTRACTION_COUNT_MISMATCH")
    failed = [row for row in rows if not _strict_bool(row["write_ok"])]
    if (
        len(failed) != 1
        or sum(_strict_bool(row["write_ok"]) for row in rows)
        != authority.successful_extraction_rows
    ):
        _fail("ONE_OBJECT_REPLAY_LEGACY_FAILED_ROW_NOT_UNIQUE")
    row = dict(failed[0])
    expected = {
        "smoke_role": "production_selected",
        "write_ok": "False",
        "mask_status": "FAILED",
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
        "decoder_backend": "pydicom_pixels_raw:pillow",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
        "canonical_color_space": "RGB",
        "source_sector_nonempty_gate_passed": "True",
        "source_nonzero_retained_pixel_gate_passed": "True",
        "source_temporal_variation_gate_passed": "True",
        "sampled_nonzero_retained_pixel_gate_passed": "False",
        "sampled_temporal_variation_gate_passed": "False",
        "error_code": "ValueError",
        "pixel_decode_ok": "False",
    }
    if any(row.get(key) != expected_value for key, expected_value in expected.items()):
        _fail("ONE_OBJECT_REPLAY_LEGACY_FAILED_ROW_STATE_MISMATCH")
    if not row.get("source_num_frames"):
        _fail("ONE_OBJECT_REPLAY_LEGACY_FAILED_ROW_STATE_MISMATCH")
    physical_key = str(row.get("physical_source_key", ""))
    source_relative = _safe_relative(row.get("source_relative_path", ""))
    if (
        SHA256_RE.fullmatch(physical_key) is None
        or source_relative != f"{physical_key}.dcm"
        or SHA256_RE.fullmatch(str(row.get("source_sha256", ""))) is None
    ):
        _fail("ONE_OBJECT_REPLAY_LEGACY_FAILED_ROW_BINDING_MISMATCH")
    return row


def _validate_download_binding(
    rows: Sequence[Mapping[str, str]],
    failed: Mapping[str, str],
    authority: OriginalAttemptAuthority,
    *,
    planned_batch: Mapping[str, Any],
) -> dict[str, str]:
    if (
        len(rows) != authority.download_rows
        or len(rows) != planned_batch.get("n_objects")
    ):
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
    if any(not _strict_bool(row["download_ok"]) for row in rows):
        _fail("REPLAY_DOWNLOAD_RECEIPT_INVALID")
    expected = {
        (
            str(row["subject_id"]),
            str(row["study_id"]),
            str(row["source_relative_path"]),
            str(row["source_object_key"]),
        )
        for row in planned_batch["objects"]
    }
    observed = {
        (
            str(row["subject_id"]),
            str(row["study_id"]),
            _safe_relative(row["source_relative_path"]),
            str(row["physical_source_key"]),
        )
        for row in rows
    }
    if len(observed) != len(rows) or observed != expected:
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
    physical_key = failed["physical_source_key"]
    matches = [row for row in rows if row["physical_source_key"] == physical_key]
    if len(matches) != 1:
        _fail("REPLAY_DOWNLOAD_RECEIPT_INVALID")
    matched = dict(matches[0])
    _safe_relative(matched["source_relative_path"])
    if (
        matched["subject_id"] != failed["subject_id"]
        or matched["study_id"] != failed["study_id"]
        or matched["observed_sha256"] != failed["source_sha256"]
        or SHA256_RE.fullmatch(matched["observed_sha256"]) is None
    ):
        _fail("REPLAY_LOCAL_SHA256_MISMATCH")
    return matched


def _discover_replay_row_authority(repository: Path) -> ReplayRowAuthority:
    try:
        legacy = minimal._project_legacy_session_environment(
            required_names=frozenset(
                {
                    "EXPECTED_COMMIT",
                    "SELECTED_SOURCE_MANIFEST",
                    "EXPECTED_SELECTED_SOURCE_SHA256",
                    "RUN_ROOT",
                }
            )
        )
    except Exception as exc:
        raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
    values = legacy.values
    observed = ReplayRowAuthority(
        selected_source=Path(values["SELECTED_SOURCE_MANIFEST"]),
        selected_source_sha256=values["EXPECTED_SELECTED_SOURCE_SHA256"],
        source_metadata=(
            Path(values["RUN_ROOT"])
            / "restricted/source_preflight/"
            "c3_full_source_object_metadata.restricted.jsonl"
        ),
    )
    try:
        head = _git_value(repository, "rev-parse", "HEAD")
        if (
            values["EXPECTED_SELECTED_SOURCE_SHA256"]
            != core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
            or not minimal._git_is_ancestor(
                values["EXPECTED_COMMIT"], head, repository=repository
            )
        ):
            _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")
        minimal._validate_row_authority_metadata(observed.selected_source)
        minimal._validate_row_authority_metadata(observed.source_metadata)
    except ReplayError as exc:
        raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
    except Exception as exc:
        raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
    return observed


def _validate_submission_claim(
    claim: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_sha256: str,
    requirements: core.PlanRequirements,
    authority: OriginalAttemptAuthority,
    expected_runtime_authority: Mapping[str, Any],
    launch_authority_sha256: str,
) -> None:
    if set(claim) != full_sequential.FULL_SUBMISSION_CLAIM_KEYS:
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
    exact = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_submission_claim_v1",
        "status": "PREPARED_TWO_SUBMISSION_FULL_RECONSTRUCTION",
        "governing_commit": authority.execution_commit,
        "attempt_id": authority.attempt_id,
        "batch_plan_sha256": plan_sha256,
        "plan_authority_sha256": core.canonical_json_sha256(plan["authority"]),
        "runtime_authority_sha256": core.canonical_json_sha256(
            expected_runtime_authority
        ),
        "launch_authority_sha256": launch_authority_sha256,
        "maximum_qsub_submissions": 2,
        "array_tasks": requirements.batch_count,
        "array_max_concurrency": 1,
        "automatic_resubmission": False,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "raw_dicom_deletion_authorized": False,
        "bucket_listing_requests_before_claim": 0,
        "cloud_requests_before_claim": 0,
        "qsub_submissions_before_claim": 0,
        "dicom_body_reads_before_claim": 0,
        "gpu_executions_before_claim": 0,
        "model_fitting_before_claim": 0,
        "prediction_generation_before_claim": 0,
        "confirmatory_performance_access_before_claim": 0,
    }
    digest_fields = {
        "capacity_receipt_sha256",
        "qsub_environment_sha256",
    }
    if any(claim.get(key) != value for key, value in exact.items()) or any(
        SHA256_RE.fullmatch(str(claim.get(key, ""))) is None
        for key in digest_fields
    ):
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")


def _load_bound_plan(
    attempt_root: Path,
    *,
    authority: OriginalAttemptAuthority,
    requirements: core.PlanRequirements | None,
) -> tuple[
    Mapping[str, Any],
    core.PlanRequirements,
    Mapping[str, Any],
    Mapping[str, Any],
    str,
]:
    try:
        plan = _read_json(
            attempt_root / "full_batch_plan.restricted.json",
            maximum_bytes=MAXIMUM_MANIFEST_BYTES,
        )
        effective_requirements = requirements
        if effective_requirements is None:
            contract = core.load_orchestration_contract(CONTRACT_PATH)
            effective_requirements = core.production_requirements(contract)
            core.validate_plan_authority_against_contract(
                plan["authority"], contract=contract, contract_path=CONTRACT_PATH
            )
        plan_sha256 = core.validate_batch_plan(
            plan, requirements=effective_requirements
        )
    except ReplayError as exc:
        raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc
    except Exception as exc:
        raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc
    expected_attempt_id = (
        f"lvef_c3_full_{authority.batch_plan_sha256_prefix}_"
        f"{authority.execution_commit[:8]}"
    )
    if (
        SHA256_RE.fullmatch(plan_sha256) is None
        or not plan_sha256.startswith(authority.batch_plan_sha256_prefix)
        or authority.attempt_id != expected_attempt_id
        or plan.get("authority", {}).get("git_commit") != authority.execution_commit
    ):
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
    expected_runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha256}
    )
    try:
        claim = _read_json(
            attempt_root / "full_submission_claim.restricted.json",
            maximum_bytes=MAXIMUM_RECEIPT_BYTES,
        )
    except ReplayError as exc:
        raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc
    try:
        launch_authority = _read_json(
            attempt_root / "full_launch_authority.restricted.json",
            maximum_bytes=MAXIMUM_RECEIPT_BYTES,
        )
        launch_authority_sha256 = core.canonical_json_sha256(launch_authority)
    except Exception as exc:
        raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc
    _validate_submission_claim(
        claim,
        plan=plan,
        plan_sha256=plan_sha256,
        requirements=effective_requirements,
        authority=authority,
        expected_runtime_authority=expected_runtime,
        launch_authority_sha256=launch_authority_sha256,
    )
    batches = [
        row for row in plan["batches"] if row.get("batch_id") == authority.batch_id
    ]
    if len(batches) != 1:
        _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
    planned_batch = batches[0]
    return (
        plan,
        effective_requirements,
        planned_batch,
        launch_authority,
        plan_sha256,
    )


def _load_selected_source_object(
    row_authority: ReplayRowAuthority,
    *,
    plan: Mapping[str, Any],
    planned_object: Mapping[str, Any],
    authority: OriginalAttemptAuthority,
) -> Mapping[str, Any]:
    try:
        selected_payload = _read_owner_private_file(
            row_authority.selected_source, maximum_bytes=MAXIMUM_MANIFEST_BYTES
        )
        metadata_payload = _read_owner_private_file(
            row_authority.source_metadata, maximum_bytes=MAXIMUM_MANIFEST_BYTES
        )
        if (
            row_authority.selected_source_sha256
            != core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
            or hashlib.sha256(selected_payload).hexdigest()
            != core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
            or hashlib.sha256(metadata_payload).hexdigest()
            != plan["authority"]["source_metadata_sha256"]
        ):
            _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")
        selected_rows = minimal._strict_csv_rows(selected_payload)
        metadata_rows = minimal._strict_jsonl_rows(metadata_payload)
        selected_keys = {
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
        }
        metadata_keys = {
            "release_id",
            "component",
            "subject_id",
            "study_id",
            "split",
            "source_relative_path",
            "gcs_uri",
            "source_object_key",
            "production_batch",
            "remote_size_bytes",
            "remote_md5_base64",
            "remote_crc32c_base64",
            "remote_generation",
            "remote_storage_class",
            "remote_updated",
            "preflight_status",
            "discrepancy_reasons",
        }
        if any(set(row) != selected_keys for row in selected_rows) or any(
            set(row) != metadata_keys for row in metadata_rows
        ):
            _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")
        normalized = core.reconcile_selected_source_metadata(
            selected_rows, metadata_rows, release="mimic-iv-echo/1.0"
        )
    except ReplayError as exc:
        raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
    except Exception as exc:
        raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
    key = str(planned_object["source_object_key"])
    matches = [row for row in normalized if row["source_object_key"] == key]
    if len(matches) != 1:
        _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")
    selected = matches[0]
    exact = {
        "subject_id": str(planned_object["subject_id"]),
        "study_id": str(planned_object["study_id"]),
        "split": str(planned_object["split"]),
        "source_relative_path": str(planned_object["source_relative_path"]),
        "source_object_key": key,
        "production_batch": authority.batch_id,
        "remote_size_bytes": planned_object["size_bytes"],
        "remote_generation": str(planned_object["generation"]),
        "remote_md5_base64": str(planned_object["md5_base64"]),
        "remote_crc32c_base64": str(planned_object["crc32c_base64"]),
    }
    if any(selected.get(field) != value for field, value in exact.items()):
        mapping = {
            "production_batch": "REPLAY_BATCH_MEMBERSHIP_INVALID",
            "remote_size_bytes": "REPLAY_OBJECT_SIZE_MISMATCH",
            "remote_generation": "REPLAY_OBJECT_GENERATION_MISMATCH",
            "remote_md5_base64": "REPLAY_OBJECT_MD5_MISMATCH",
            "remote_crc32c_base64": "REPLAY_OBJECT_CRC32C_MISMATCH",
        }
        mismatch = next(
            field for field, value in exact.items() if selected.get(field) != value
        )
        _fail(mapping.get(mismatch, "REPLAY_SOURCE_MEMBERSHIP_INVALID"))
    return selected


def _validate_replay_input_effective_privacy(
    path: Path,
    *,
    attempt_root: Path,
    root_identity: LegacyRootIdentity,
    expected_mode: int = 0o600,
) -> None:
    try:
        _require_no_symlink_components(path)
        relative = path.relative_to(attempt_root)
    except (ValueError, ReplayError) as exc:
        raise ReplayError("REPLAY_INPUT_PATH_CONTAINMENT_INVALID") from exc
    if not relative.parts:
        _fail("REPLAY_INPUT_PATH_CONTAINMENT_INVALID")
    try:
        root_observed = os.lstat(attempt_root)
    except OSError as exc:
        raise ReplayError("REPLAY_INPUT_PATH_CONTAINMENT_INVALID") from exc
    if (
        not stat.S_ISDIR(root_observed.st_mode)
        or root_observed.st_uid != os.geteuid()
        or root_observed.st_dev != root_identity.device
        or root_observed.st_ino != root_identity.inode
        or root_observed.st_gid != root_identity.group
        or stat.S_IMODE(root_observed.st_mode) != root_identity.mode
        or root_observed.st_nlink != root_identity.nlink
        or root_observed.st_size != root_identity.size
        or root_observed.st_mtime_ns != root_identity.mtime_ns
        or root_observed.st_ctime_ns != root_identity.ctime_ns
    ):
        _fail("REPLAY_CURRENT_FILE_IDENTITY_CHANGED")
    cursor = attempt_root
    for part in relative.parts[:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise ReplayError("REPLAY_INPUT_PATH_CONTAINMENT_INVALID") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_dev != root_identity.device
            or mode & 0o022
            or mode & 0o5000
        ):
            _fail("REPLAY_INPUT_EFFECTIVE_PRIVACY_INVALID")
    try:
        leaf = os.lstat(path)
    except OSError as exc:
        raise ReplayError("REPLAY_INPUT_PATH_CONTAINMENT_INVALID") from exc
    if (
        not stat.S_ISREG(leaf.st_mode)
        or leaf.st_uid != os.geteuid()
        or leaf.st_dev != root_identity.device
        or leaf.st_nlink != 1
        or stat.S_IMODE(leaf.st_mode) != expected_mode
    ):
        if leaf.st_dev != root_identity.device:
            _fail("REPLAY_CURRENT_NAMESPACE_DEVICE_TOPOLOGY_INVALID")
        _fail("REPLAY_INPUT_EFFECTIVE_PRIVACY_INVALID")


def _source_file_identity(
    path: Path,
    *,
    expected_size: int,
    attempt_root: Path | None = None,
    root_identity: LegacyRootIdentity | None = None,
) -> SourceFileIdentity:
    if (attempt_root is None) is not (root_identity is None):
        _fail("REPLAY_INPUT_PATH_CONTAINMENT_INVALID")
    if attempt_root is not None and root_identity is not None:
        _validate_replay_input_effective_privacy(
            path,
            attempt_root=attempt_root,
            root_identity=root_identity,
        )
    try:
        _require_no_symlink_components(path)
        first = os.lstat(path)
        second = os.lstat(path)
    except (OSError, ReplayError) as exc:
        raise ReplayError("REPLAY_LOCAL_FILE_AUTHORITY_INVALID") from exc
    identity = (
        first.st_dev,
        first.st_ino,
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(first.st_mode)
        or first.st_uid != os.geteuid()
        or stat.S_IMODE(first.st_mode) != 0o600
        or first.st_size != expected_size
        or identity
        != (
            second.st_dev,
            second.st_ino,
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        )
    ):
        if first.st_size != expected_size:
            _fail("REPLAY_OBJECT_SIZE_MISMATCH")
        if root_identity is not None and first.st_dev != root_identity.device:
            _fail("REPLAY_CURRENT_NAMESPACE_DEVICE_TOPOLOGY_INVALID")
        _fail("REPLAY_CURRENT_FILE_IDENTITY_CHANGED")
    return SourceFileIdentity(
        path=path,
        device=int(first.st_dev),
        inode=int(first.st_ino),
        size=int(first.st_size),
        mtime_ns=int(first.st_mtime_ns),
        ctime_ns=int(first.st_ctime_ns),
    )


def _findmnt_row(
    path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[str, str, str, tuple[str, ...], str]:
    try:
        completed = runner(
            [
                str(FINDMNT_PATH),
                "--json",
                "--target",
                str(path),
                "--output",
                "TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        if (
            completed.returncode != 0
            or completed.stderr
            or not isinstance(completed.stdout, bytes)
            or not completed.stdout
            or len(completed.stdout) > 65_536
        ):
            raise ValueError
    except Exception as exc:
        raise ReplayError("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID") from exc
    try:
        payload = json.loads(
            completed.stdout.decode("utf-8"), object_pairs_hook=_strict_pairs
        )
        if not isinstance(payload, Mapping) or set(payload) != {"filesystems"}:
            raise ValueError
        rows = payload["filesystems"]
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        row = rows[0]
        required = {"target", "source", "fstype", "options", "fsroot"}
        if (
            not isinstance(row, Mapping)
            or set(row) != required
            or any(type(row[key]) is not str for key in required)
        ):
            raise ValueError
        target, source, fstype, options, fsroot = (
            row[key] for key in ("target", "source", "fstype", "options", "fsroot")
        )
        option_tokens = options.split(",")
        option_set = tuple(sorted(set(option_tokens)))
        if (
            not source
            or target != str(APPROVED_RESEARCH_MOUNT_TARGET)
            or fstype != APPROVED_RESEARCH_FILESYSTEM_TYPE
            or fsroot != APPROVED_RESEARCH_FILESYSTEM_ROOT
            or not option_set
            or len(option_set) != len(option_tokens)
            or any(
                not value
                or value != value.strip()
                or value in {"bind", "rbind"}
                for value in option_set
            )
        ):
            raise ValueError
        path.relative_to(APPROVED_RESEARCH_MOUNT_TARGET)
    except (ReplayError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID")
    return target, source, fstype, option_set, fsroot


def validate_current_mount_authority(
    attempt_root: Path,
    source_path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> CurrentMountAuthority:
    root_row = _findmnt_row(attempt_root, runner=runner)
    source_row = _findmnt_row(source_path, runner=runner)
    if root_row != source_row:
        _fail("REPLAY_CURRENT_NAMESPACE_DEVICE_TOPOLOGY_INVALID")
    identity = hashlib.sha256(
        json.dumps(root_row, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CurrentMountAuthority(
        status="PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT",
        identity_sha256=identity,
    )


def validate_legacy_receipt_device_namespace(
    receipt: Mapping[str, Any],
    *,
    expectation: core.DownloadExpectation,
    source_identity: SourceFileIdentity,
    root_identity: LegacyRootIdentity,
    mount_authority: CurrentMountAuthority,
    authority: OriginalAttemptAuthority,
) -> tuple[str, HistoricalDeviceReconciliation]:
    if set(receipt) != DOWNLOAD_VERIFICATION_RECEIPT_KEYS:
        _fail("REPLAY_DOWNLOAD_RECEIPT_NONDEVICE_AUTHORITY_INVALID")
    exact = {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": expectation.source_object_key,
        "size_bytes": expectation.size_bytes,
        "generation": expectation.generation,
        "md5_base64": expectation.md5_base64,
        "crc32c_base64": expectation.crc32c_base64,
        "file_inode": source_identity.inode,
        "file_mtime_ns": source_identity.mtime_ns,
    }
    mismatch_codes = {
        "size_bytes": "REPLAY_OBJECT_SIZE_MISMATCH",
        "generation": "REPLAY_OBJECT_GENERATION_MISMATCH",
        "md5_base64": "REPLAY_OBJECT_MD5_MISMATCH",
        "crc32c_base64": "REPLAY_OBJECT_CRC32C_MISMATCH",
    }
    for field, expected in exact.items():
        observed = receipt.get(field)
        if type(observed) is not type(expected) or observed != expected:
            _fail(
                mismatch_codes.get(
                    field, "REPLAY_DOWNLOAD_RECEIPT_NONDEVICE_AUTHORITY_INVALID"
                )
            )
    local_sha256 = str(receipt.get("local_sha256", ""))
    chunk_size = receipt.get("digest_chunk_size_bytes")
    if (
        SHA256_RE.fullmatch(local_sha256) is None
        or receipt.get("digest_backend")
        != "google_crc32c_c_external_worker_v1"
        or type(chunk_size) is not int
        or chunk_size != 8_388_608
    ):
        _fail("REPLAY_DOWNLOAD_RECEIPT_NONDEVICE_AUTHORITY_INVALID")
    historical_device = receipt.get("file_device")
    if source_identity.device != root_identity.device:
        _fail("REPLAY_CURRENT_NAMESPACE_DEVICE_TOPOLOGY_INVALID")
    if mount_authority.status != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT":
        _fail("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID")
    if type(historical_device) is not int or historical_device < 0:
        _fail("REPLAY_HISTORICAL_DEVICE_NAMESPACE_RECONCILIATION_INVALID")
    differs = historical_device != source_identity.device
    if differs is not authority.historical_device_must_differ:
        _fail("REPLAY_HISTORICAL_DEVICE_NAMESPACE_RECONCILIATION_INVALID")
    return local_sha256, HistoricalDeviceReconciliation(
        status="CROSS_NODE_DEVICE_NAMESPACE_RECONCILED",
        historical_and_current_differ=differs,
    )


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _private_directory_identity(path: Path) -> PrivateDirectoryIdentity:
    try:
        _require_no_symlink_components(path)
        before = os.lstat(path)
        after = os.lstat(path)
    except (OSError, ReplayError) as exc:
        raise ReplayError("REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID") from exc
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.geteuid()
        or not core.owner_private_directory_mode_ok(before.st_mode)
        or (before.st_dev, before.st_ino, before.st_mode, before.st_uid)
        != (after.st_dev, after.st_ino, after.st_mode, after.st_uid)
    ):
        _fail("REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID")
    return PrivateDirectoryIdentity(
        path=path,
        device=int(before.st_dev),
        inode=int(before.st_ino),
        group=int(before.st_gid),
        mode=stat.S_IMODE(before.st_mode),
    )


def _directory_stat_matches_identity(
    metadata: os.stat_result, identity: PrivateDirectoryIdentity
) -> bool:
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and core.owner_private_directory_mode_ok(metadata.st_mode)
        and int(metadata.st_dev) == identity.device
        and int(metadata.st_ino) == identity.inode
        and int(metadata.st_gid) == identity.group
        and stat.S_IMODE(metadata.st_mode) == identity.mode
    )


def _validate_diagnostic_candidate(
    path: Path, *, allowed_prefix: Path, original_attempt_root: Path
) -> PrivateDirectoryIdentity:
    if (
        not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        or DIAGNOSTIC_NAME_RE.fullmatch(path.name) is None
        or os.path.lexists(path)
    ):
        _fail("REPLAY_DIAGNOSTIC_ROOT_INVALID")
    try:
        _require_no_symlink_components(path.parent)
        _require_no_symlink_components(allowed_prefix)
        allowed = allowed_prefix.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, ReplayError) as exc:
        raise ReplayError("REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID") from exc
    candidate = parent / path.name
    if (
        not parent.is_dir()
        or parent != allowed
        or _inside(candidate, original_attempt_root)
        or _inside(candidate, REPOSITORY_ROOT)
    ):
        _fail("REPLAY_DIAGNOSTIC_ROOT_INVALID")
    return _private_directory_identity(parent)


def _create_diagnostic_root(
    preflight: ReplayPreflight,
) -> tuple[Path, PrivateDirectoryIdentity]:
    """Create the fresh root relative to the preflight-pinned parent inode."""

    parent_descriptor = -1
    child_descriptor = -1
    root_created = False
    try:
        parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        parent_descriptor = os.open(
            preflight.diagnostic_root.parent, parent_flags
        )
        parent_before = os.fstat(parent_descriptor)
        if not _directory_stat_matches_identity(
            parent_before, preflight.diagnostic_parent
        ):
            _fail("REPLAY_DIAGNOSTIC_PARENT_IDENTITY_CHANGED")
        try:
            os.stat(
                preflight.diagnostic_root.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            _fail("REPLAY_DIAGNOSTIC_ROOT_INVALID")
        os.mkdir(
            preflight.diagnostic_root.name,
            mode=0o700,
            dir_fd=parent_descriptor,
        )
        root_created = True
        child_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        child_descriptor = os.open(
            preflight.diagnostic_root.name,
            child_flags,
            dir_fd=parent_descriptor,
        )
        child = os.fstat(child_descriptor)
        visible = os.lstat(preflight.diagnostic_root)
        parent_after = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(child.st_mode)
            or child.st_uid != os.geteuid()
            or not core.owner_private_directory_mode_ok(child.st_mode)
            or child.st_gid != parent_before.st_gid
            or (visible.st_dev, visible.st_ino) != (child.st_dev, child.st_ino)
            or not _directory_stat_matches_identity(
                parent_after, preflight.diagnostic_parent
            )
        ):
            raise ReplayError(
                "REPLAY_DIAGNOSTIC_ROOT_NOT_OWNER_PRIVATE",
                diagnostic_root_created=True,
            )
        child_identity = PrivateDirectoryIdentity(
            path=preflight.diagnostic_root,
            device=int(child.st_dev),
            inode=int(child.st_ino),
            group=int(child.st_gid),
            mode=stat.S_IMODE(child.st_mode),
        )
        return preflight.diagnostic_root, child_identity
    except ReplayError as exc:
        if root_created:
            exc.diagnostic_root_created = True
        raise
    except OSError as exc:
        raise ReplayError(
            "REPLAY_DIAGNOSTIC_ROOT_CREATE_FAILED",
            diagnostic_root_created=root_created,
        ) from exc
    finally:
        if child_descriptor >= 0:
            os.close(child_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def run_preflight(
    *,
    governing_commit: str,
    diagnostic_root: Path,
    production_root: Path = PRODUCTION_ROOT,
    allowed_diagnostic_prefix: Path = ALLOWED_DIAGNOSTIC_PREFIX,
    repository: Path = REPOSITORY_ROOT,
    authority: OriginalAttemptAuthority = ORIGINAL_AUTHORITY,
    requirements: core.PlanRequirements | None = None,
    row_authority: ReplayRowAuthority | None = None,
    row_authority_loader: Callable[[Path], ReplayRowAuthority] = (
        _discover_replay_row_authority
    ),
    git_validator: Callable[..., None] = validate_git_authority,
    mount_validator: Callable[[Path, Path], CurrentMountAuthority] = (
        validate_current_mount_authority
    ),
) -> ReplayPreflight:
    """Validate the exact replay authority without creating or reading a DICOM."""

    try:
        git_validator(
            repository, governing_commit, original_commit=authority.execution_commit
        )
        attempt_root = production_root / "attempts" / authority.attempt_id
        inventory, attempt_root_identity = (
            validate_immutable_legacy_attempt_effective_privacy(
                attempt_root, authority
            )
        )

        batch_cache = attempt_root / "extracted_cache" / authority.batch_id
        partial = batch_cache / "dicom_extraction.partial"
        failure_summary_path = partial / "failure.summary.json"
        extraction_manifest_path = (
            partial / "extraction_manifest.restricted.csv"
        )
        for replay_input in (failure_summary_path, extraction_manifest_path):
            _validate_replay_input_effective_privacy(
                replay_input,
                attempt_root=attempt_root,
                root_identity=attempt_root_identity,
            )
        try:
            _validate_failure_summary(_read_json(failure_summary_path))
            extraction_rows = _read_csv_exact(
                extraction_manifest_path,
                LEGACY_B805_EXTRACTION_MANIFEST_HEADER,
            )
        except ReplayError as exc:
            raise ReplayError("REPLAY_SOURCE_MEMBERSHIP_INVALID") from exc
        failed = _validate_legacy_failed_row(extraction_rows, authority)

        for replay_input in (
            attempt_root / "full_batch_plan.restricted.json",
            attempt_root / "full_submission_claim.restricted.json",
            attempt_root / "full_launch_authority.restricted.json",
        ):
            _validate_replay_input_effective_privacy(
                replay_input,
                attempt_root=attempt_root,
                root_identity=attempt_root_identity,
            )
        plan, effective_requirements, planned_batch, launch_authority, plan_sha256 = (
            _load_bound_plan(
                attempt_root, authority=authority, requirements=requirements
            )
        )
        physical_key = str(failed["physical_source_key"])
        planned_matches = [
            row
            for row in planned_batch["objects"]
            if row.get("source_object_key") == physical_key
        ]
        if len(planned_matches) != 1:
            _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
        planned_object = planned_matches[0]
        if (
            str(planned_object["subject_id"]) != failed["subject_id"]
            or str(planned_object["study_id"]) != failed["study_id"]
        ):
            _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")

        selected_authority = row_authority or row_authority_loader(repository)
        if not isinstance(selected_authority, ReplayRowAuthority):
            _fail("REPLAY_SOURCE_MEMBERSHIP_INVALID")
        _load_selected_source_object(
            selected_authority,
            plan=plan,
            planned_object=planned_object,
            authority=authority,
        )

        download_batch = attempt_root / "raw" / authority.batch_id
        download_manifest = (
            download_batch / "verified_download_manifest.restricted.csv"
        )
        _validate_replay_input_effective_privacy(
            download_manifest,
            attempt_root=attempt_root,
            root_identity=attempt_root_identity,
        )
        try:
            download_rows, download_manifest_sha256 = _read_csv_exact_with_sha256(
                download_manifest, VERIFIED_DOWNLOAD_MANIFEST_HEADER
            )
        except ReplayError as exc:
            raise ReplayError("REPLAY_DOWNLOAD_RECEIPT_INVALID") from exc
        downloaded = _validate_download_binding(
            download_rows,
            failed,
            authority,
            planned_batch=planned_batch,
        )
        # The replay's pass decision is made from ``download_rows`` and
        # ``download_manifest_sha256``, which come from the same stable,
        # no-follow inode read.  The production validator is retained as an
        # additional compatibility check, but is not used to supply the bound
        # digest.
        try:
            production_stages.validate_download_manifest_plan_membership(
                download_manifest, planned_batch
            )
        except Exception as exc:
            raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc

        expected_runtime = core.validate_runtime_authority(
            {**plan["authority"], "batch_plan_sha256": plan_sha256}
        )
        ledger_path = (
            attempt_root
            / "batches"
            / authority.batch_id
            / "download_resume_ledger.restricted.json"
        )
        _validate_replay_input_effective_privacy(
            ledger_path,
            attempt_root=attempt_root,
            root_identity=attempt_root_identity,
        )
        try:
            ledger = _read_json(
                ledger_path, maximum_bytes=MAXIMUM_MANIFEST_BYTES
            )
            expected_keys = {
                str(row["source_object_key"]) for row in planned_batch["objects"]
            }
            core.validate_resume_authority(
                ledger,
                expected_authority=expected_runtime,
                attempt_id=authority.attempt_id,
                expected_object_keys={authority.batch_id: expected_keys},
            )
            canonical_ledger = production_stages.validate_stage_predecessor(
                input_ledger=ledger_path,
                batch_id=authority.batch_id,
                expected_state="DOWNLOAD_VERIFIED",
                expected_authority=expected_runtime,
                expected_attempt_id=authority.attempt_id,
                expected_object_keys=expected_keys,
                bound_manifest=download_manifest,
            )
            if canonical_ledger != ledger:
                raise ValueError("ledger changed during canonical validation")
            core.validate_direct_full_download_scope(
                launch_authority=launch_authority,
                ledger=ledger,
                plan=plan,
                requirements=effective_requirements,
                batch_id=authority.batch_id,
                maximum_attempts_per_object=5,
                expected_launch_authority_sha256=core.canonical_json_sha256(
                    launch_authority
                ),
                test_only_synthetic_full_scope=requirements is not None,
            )
        except Exception as exc:
            raise ReplayError("REPLAY_BATCH_MEMBERSHIP_INVALID") from exc
        if set(ledger["batches"]) != {authority.batch_id}:
            _fail("REPLAY_BATCH_MEMBERSHIP_INVALID")
        ledger_batch = ledger["batches"][authority.batch_id]
        if (
            ledger_batch.get("state") != "DOWNLOAD_VERIFIED"
            or ledger_batch.get("download_manifest_sha256")
            != download_manifest_sha256
        ):
            _fail("REPLAY_DOWNLOAD_RECEIPT_INVALID")

        expectation = core.expectation_from_plan_object(planned_object)
        objects_root = download_batch / "objects"
        source_path = objects_root / core.planned_final_name(expectation)
        if source_path != objects_root / failed["source_relative_path"]:
            _fail("REPLAY_LOCAL_FILE_AUTHORITY_INVALID")
        source_identity = _source_file_identity(
            source_path,
            expected_size=expectation.size_bytes,
            attempt_root=attempt_root,
            root_identity=attempt_root_identity,
        )
        mount_authority = mount_validator(attempt_root, source_path)
        if (
            not isinstance(mount_authority, CurrentMountAuthority)
            or mount_authority.status
            != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT"
            or SHA256_RE.fullmatch(mount_authority.identity_sha256) is None
        ):
            _fail("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID")

        receipt_path = (
            download_batch
            / "receipts"
            / f"{expectation.source_object_key}.verification.json"
        )
        try:
            _validate_replay_input_effective_privacy(
                receipt_path,
                attempt_root=attempt_root,
                root_identity=attempt_root_identity,
            )
        except ReplayError as exc:
            raise ReplayError("REPLAY_DOWNLOAD_RECEIPT_INVALID") from exc
        try:
            receipt, receipt_sha256 = _read_json_with_sha256(
                receipt_path, maximum_bytes=MAXIMUM_RECEIPT_BYTES
            )
        except ReplayError as exc:
            raise ReplayError("REPLAY_DOWNLOAD_RECEIPT_INVALID") from exc
        receipts = ledger_batch["download_verification_receipts"]
        if (
            set(receipts) != expected_keys
            or receipts.get(expectation.source_object_key) != receipt_sha256
        ):
            _fail("REPLAY_DOWNLOAD_RECEIPT_INVALID")
        local_sha256, device_reconciliation = (
            validate_legacy_receipt_device_namespace(
                receipt,
                expectation=expectation,
                source_identity=source_identity,
                root_identity=attempt_root_identity,
                mount_authority=mount_authority,
                authority=authority,
            )
        )
        if downloaded["observed_sha256"] != local_sha256:
            _fail("REPLAY_LOCAL_SHA256_MISMATCH")
        if failed["source_sha256"] != local_sha256:
            _fail("REPLAY_LOCAL_SHA256_MISMATCH")

        planned_partial = (
            download_batch
            / "partials"
            / core.planned_partial_name(expectation, authority.attempt_id)
        )
        receipt_temporary = receipt_path.parent / (
            f".{receipt_path.name}.{authority.attempt_id}.partial"
        )
        if os.path.lexists(planned_partial) or os.path.lexists(receipt_temporary):
            _fail("REPLAY_LOCAL_FILE_AUTHORITY_INVALID")

        diagnostic_parent = _validate_diagnostic_candidate(
            diagnostic_root,
            allowed_prefix=allowed_diagnostic_prefix,
            original_attempt_root=attempt_root,
        )
        if _source_file_identity(
            source_path,
            expected_size=expectation.size_bytes,
            attempt_root=attempt_root,
            root_identity=attempt_root_identity,
        ) != source_identity:
            _fail("REPLAY_CURRENT_FILE_IDENTITY_CHANGED")
        if _legacy_root_identity(attempt_root, authority) != attempt_root_identity:
            _fail("REPLAY_CURRENT_FILE_IDENTITY_CHANGED")
        if mount_validator(attempt_root, source_path) != mount_authority:
            _fail("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID")
        return ReplayPreflight(
            attempt_root=attempt_root,
            original_inventory=inventory,
            failed_row=failed,
            download_row=downloaded,
            planned_object=planned_object,
            source_path=source_path,
            source_identity=source_identity,
            attempt_root_identity=attempt_root_identity,
            mount_authority=mount_authority,
            device_reconciliation=device_reconciliation,
            objects_root=objects_root,
            local_sha256=local_sha256,
            diagnostic_root=diagnostic_root,
            diagnostic_parent=diagnostic_parent,
        )
    except ReplayError:
        raise
    except Exception as exc:
        raise ReplayError("REPLAY_PREFLIGHT_UNEXPECTED_SANITIZED_FAILURE") from exc


def _plain_scalar(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        value = value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    _fail("ONE_OBJECT_REPLAY_TECHNICAL_PROVENANCE_INVALID", dicom_body_reads=1)


def _technical_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: _plain_scalar(row.get(field))
        for field in SAFE_TECHNICAL_PROVENANCE_FIELDS
        if field in row
    }


def _validate_repaired_row(
    row: Mapping[str, Any], *, legacy_failed_row: Mapping[str, Any]
) -> None:
    if not REPAIRED_REQUIRED_FIELDS.issubset(row):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_SCHEMA_MISMATCH", dicom_body_reads=1)
    if any(
        _plain_scalar(row.get(field)) != legacy_failed_row.get(field)
        for field in DECODER_COLOR_AUTHORITY_FIELDS
    ):
        _fail(
            "ONE_OBJECT_REPLAY_REPAIRED_DECODER_COLOR_AUTHORITY_MISMATCH",
            dicom_body_reads=1,
        )
    if (
        row.get("write_ok") is not True
        or row.get("error_code") is not None
        or row.get("mask_status") != "APPLIED"
        or row.get("decode_color_status") != "PASS"
        or row.get("failure_substage") != "NONE"
        or row.get("frames_shape") != "32x224x224x3"
        or row.get("frames_dtype") != "uint8"
    ):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_EXTRACTION_FAILED", dicom_body_reads=1)
    for count_field, gate_field in REPAIRED_COUNT_GATE_PAIRS:
        count = _plain_scalar(row.get(count_field))
        gate = _plain_scalar(row.get(gate_field))
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or not isinstance(gate, bool)
            or gate is not (count > 0)
        ):
            _fail(
                "ONE_OBJECT_REPLAY_REPAIRED_COUNT_GATE_CONTRADICTION",
                dicom_body_reads=1,
            )
    source_num_frames = _plain_scalar(row.get("source_num_frames"))
    if (
        not isinstance(source_num_frames, int)
        or isinstance(source_num_frames, bool)
        or source_num_frames < 2
    ):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_EXTRACTION_FAILED", dicom_body_reads=1)
    for gate_field in (
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "post_crop_nonzero_retained_pixel_gate_passed",
        "post_crop_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_gate_passed",
    ):
        if row.get(gate_field) is not True:
            _fail("ONE_OBJECT_REPLAY_REPAIRED_EXTRACTION_FAILED", dicom_body_reads=1)
    path = row.get("selected_preprocessing_path")
    # The frozen legacy row already proved that its unchanged ordinary path
    # fails the sampled signal gates.  A successful repair must therefore be
    # one of the bounded fallback paths, never an ordinary-path relabel.
    allowed_paths = {
        reconstruction.SPATIAL_FALLBACK_PREPROCESSING_PATH,
        reconstruction.TEMPORAL_FALLBACK_PREPROCESSING_PATH,
        reconstruction.SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
    }
    if path not in allowed_paths:
        _fail("ONE_OBJECT_REPLAY_REPAIRED_EXTRACTION_FAILED", dicom_body_reads=1)
    ordinary_post_crop_passed = bool(
        row.get("ordinary_post_crop_nonzero_retained_pixel_gate_passed") is True
        and row.get("ordinary_post_crop_temporal_variation_gate_passed") is True
    )
    ordinary_sampled_passed = bool(
        row.get("ordinary_sampled_nonzero_retained_pixel_gate_passed") is True
        and row.get("ordinary_sampled_temporal_variation_gate_passed") is True
    )
    if ordinary_sampled_passed:
        _fail(
            "ONE_OBJECT_REPLAY_REPAIRED_PATH_TRIGGER_CONTRADICTION",
            dicom_body_reads=1,
        )
    spatial_trigger_paths = {
        reconstruction.SPATIAL_FALLBACK_PREPROCESSING_PATH,
        reconstruction.SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
    }
    if (
        path in spatial_trigger_paths and ordinary_post_crop_passed
    ) or (
        path == reconstruction.TEMPORAL_FALLBACK_PREPROCESSING_PATH
        and not ordinary_post_crop_passed
    ):
        _fail(
            "ONE_OBJECT_REPLAY_REPAIRED_PATH_TRIGGER_CONTRADICTION",
            dicom_body_reads=1,
        )
    temporal_fallback_paths = {
        reconstruction.TEMPORAL_FALLBACK_PREPROCESSING_PATH,
        reconstruction.SPATIAL_TEMPORAL_FALLBACK_PREPROCESSING_PATH,
    }
    expected_policy = (
        reconstruction.TEMPORAL_FALLBACK_POLICY
        if path in temporal_fallback_paths
        else reconstruction.TEMPORAL_SAMPLING_POLICY
    )
    expected_fallback = "FALLBACK_PATH_PASS"
    if (
        row.get("temporal_sampling_policy") != expected_policy
        or row.get("fallback_status") != expected_fallback
        or (
            row.get("encoder_visible_nonzero_retained_pixel_gate_passed")
            is not True
            or row.get("encoder_visible_temporal_variation_gate_passed")
            is not True
        )
    ):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_EXTRACTION_FAILED", dicom_body_reads=1)
    for field in (
        "frames_sha256",
        "sampled_indices_sha256",
        "source_num_frames_sha256",
        "npz_sha256",
    ):
        if SHA256_RE.fullmatch(str(row.get(field, ""))) is None:
            _fail("ONE_OBJECT_REPLAY_REPAIRED_HASH_INVALID", dicom_body_reads=1)


def _validate_ordinary_reproduction(row: Mapping[str, Any]) -> None:
    source_gates = (
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
    )
    ordinary_nonzero = row.get(
        "ordinary_sampled_nonzero_retained_pixel_gate_passed"
    )
    ordinary_temporal = row.get(
        "ordinary_sampled_temporal_variation_gate_passed"
    )
    if (
        row.get("decode_color_status") != "PASS"
        or row.get("mask_status") not in {"APPLIED", "GENERATED_PENDING_SIGNAL_GATES"}
        or any(row.get(field) is not True for field in source_gates)
        or ordinary_nonzero is not False
        or ordinary_temporal is not False
        or row.get("failure_substage")
        == "REPLAY_ORDINARY_FAILURE_TOPOLOGY_MISMATCH"
    ):
        _fail("REPLAY_ORDINARY_FAILURE_TOPOLOGY_MISMATCH", dicom_body_reads=1)


def _validate_repaired_npz(
    row: Mapping[str, Any],
    extraction_output: Path,
    *,
    expected_source_relative: str,
    expected_source_sha256: str,
) -> tuple[int, str]:
    clip_key = str(row.get("clip_key", ""))
    output_relative = _safe_relative(row.get("output_relative_path", ""))
    if (
        SHA256_RE.fullmatch(clip_key) is None
        or clip_key != reconstruction.stable_clip_key(expected_source_relative)
        or output_relative != f"clips/{clip_key[:2]}/{clip_key}.npz"
    ):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_OUTPUT_BINDING_INVALID", dicom_body_reads=1)
    output_path = extraction_output.joinpath(
        *PurePosixPath(output_relative).parts
    )
    try:
        metadata = os.lstat(output_path)
        if metadata.st_size < 1 or metadata.st_size > MAXIMUM_REPAIRED_NPZ_BYTES:
            _fail("REPLAY_DIAGNOSTIC_NPZ_VALIDATION_FAILED", dicom_body_reads=1)
        reconstruction.validate_extracted_npz(
            row,
            extraction_output,
            require_owner_private=True,
            expected_source_sha256=expected_source_sha256,
        )
    except ReplayError:
        raise
    except Exception as exc:
        raise ReplayError(
            "REPLAY_DIAGNOSTIC_NPZ_VALIDATION_FAILED", dicom_body_reads=1
        ) from exc
    return int(metadata.st_size), str(row["npz_sha256"])


def _write_json_no_clobber(
    path: Path,
    value: Mapping[str, Any],
    *,
    expected_parent_identity: PrivateDirectoryIdentity | None = None,
) -> tuple[int, str]:
    if os.path.lexists(path):
        _fail("ONE_OBJECT_REPLAY_OUTPUT_COLLISION", dicom_body_reads=1)
    temporary_name = f".{path.name}.partial.{os.getpid()}"
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parent_descriptor = -1
    created_identity: tuple[int, int] | None = None
    try:
        parent_before = os.lstat(path.parent)
        parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        parent_descriptor = os.open(path.parent, parent_flags)
        parent_opened = os.fstat(parent_descriptor)
        actual_parent = PrivateDirectoryIdentity(
            path=path.parent,
            device=int(parent_opened.st_dev),
            inode=int(parent_opened.st_ino),
            group=int(parent_opened.st_gid),
            mode=stat.S_IMODE(parent_opened.st_mode),
        )
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or not _directory_stat_matches_identity(parent_opened, actual_parent)
            or (parent_before.st_dev, parent_before.st_ino)
            != (parent_opened.st_dev, parent_opened.st_ino)
            or (
                expected_parent_identity is not None
                and actual_parent != expected_parent_identity
            )
        ):
            _fail("REPLAY_DIAGNOSTIC_ROOT_IDENTITY_CHANGED", dicom_body_reads=1)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(
            temporary_name, flags, 0o600, dir_fd=parent_descriptor
        )
        opened = os.fstat(descriptor)
        created_identity = (int(opened.st_dev), int(opened.st_ino))
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
            after = os.fstat(handle.fileno())
            if (
                (after.st_dev, after.st_ino) != created_identity
                or after.st_uid != os.geteuid()
                or stat.S_IMODE(after.st_mode) != 0o600
                or after.st_size != len(payload)
            ):
                raise OSError("Temporary JSON authority changed during write.")
        temporary_metadata = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(temporary_metadata.st_mode) != 0o600
            or (temporary_metadata.st_dev, temporary_metadata.st_ino)
            != created_identity
            or temporary_metadata.st_size != len(payload)
        ):
            raise OSError("Temporary JSON authority changed before publication.")
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            _fail("ONE_OBJECT_REPLAY_OUTPUT_COLLISION", dicom_body_reads=1)
        published = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        parent_after = os.fstat(parent_descriptor)
        visible_parent = os.lstat(path.parent)
        if (
            not stat.S_ISREG(published.st_mode)
            or published.st_uid != os.geteuid()
            or stat.S_IMODE(published.st_mode) != 0o600
            or (published.st_dev, published.st_ino) != created_identity
            or published.st_size != len(payload)
            or not _directory_stat_matches_identity(parent_after, actual_parent)
            or (visible_parent.st_dev, visible_parent.st_ino)
            != (actual_parent.device, actual_parent.inode)
        ):
            raise OSError("Published JSON authority is invalid.")
        os.unlink(temporary_name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ReplayError(
            "ONE_OBJECT_REPLAY_OUTPUT_WRITE_FAILED", dicom_body_reads=1
        ) from exc
    finally:
        if created_identity is not None and parent_descriptor >= 0:
            try:
                item = os.stat(
                    temporary_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISREG(item.st_mode)
                    and item.st_uid == os.geteuid()
                    and (item.st_dev, item.st_ino) == created_identity
                ):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return len(payload), hashlib.sha256(payload).hexdigest()


def _revalidate_current_replay_authority(
    preflight: ReplayPreflight,
    *,
    authority: OriginalAttemptAuthority,
    mount_validator: Callable[[Path, Path], CurrentMountAuthority],
) -> None:
    try:
        root_identity = _legacy_root_identity(preflight.attempt_root, authority)
        source_identity = _source_file_identity(
            preflight.source_path,
            expected_size=int(preflight.planned_object["size_bytes"]),
            attempt_root=preflight.attempt_root,
            root_identity=preflight.attempt_root_identity,
        )
    except ReplayError as exc:
        if exc.code == "REPLAY_CURRENT_NAMESPACE_DEVICE_TOPOLOGY_INVALID":
            raise
        raise ReplayError(
            "REPLAY_CURRENT_FILE_IDENTITY_CHANGED", dicom_body_reads=1
        ) from exc
    if (
        root_identity != preflight.attempt_root_identity
        or source_identity != preflight.source_identity
    ):
        _fail("REPLAY_CURRENT_FILE_IDENTITY_CHANGED", dicom_body_reads=1)
    if (
        mount_validator(preflight.attempt_root, preflight.source_path)
        != preflight.mount_authority
    ):
        _fail("REPLAY_CURRENT_MOUNT_AUTHORITY_INVALID", dicom_body_reads=1)


def run_replay(
    *,
    governing_commit: str,
    diagnostic_root: Path,
    production_root: Path = PRODUCTION_ROOT,
    allowed_diagnostic_prefix: Path = ALLOWED_DIAGNOSTIC_PREFIX,
    repository: Path = REPOSITORY_ROOT,
    authority: OriginalAttemptAuthority = ORIGINAL_AUTHORITY,
    requirements: core.PlanRequirements | None = None,
    row_authority: ReplayRowAuthority | None = None,
    row_authority_loader: Callable[[Path], ReplayRowAuthority] = (
        _discover_replay_row_authority
    ),
    extractor: Callable[[Mapping[str, Any], str, str], Mapping[str, Any]] = (
        reconstruction._extract_one
    ),
    git_validator: Callable[..., None] = validate_git_authority,
    mount_validator: Callable[[Path, Path], CurrentMountAuthority] = (
        validate_current_mount_authority
    ),
) -> dict[str, Any]:
    """Run the exact one-object local preprocessing replay after all gates."""

    preflight = run_preflight(
        governing_commit=governing_commit,
        diagnostic_root=diagnostic_root,
        production_root=production_root,
        allowed_diagnostic_prefix=allowed_diagnostic_prefix,
        repository=repository,
        authority=authority,
        requirements=requirements,
        row_authority=row_authority,
        row_authority_loader=row_authority_loader,
        git_validator=git_validator,
        mount_validator=mount_validator,
    )
    counters = ReplayAccessCounters(allowed_source=preflight.source_path)
    root_created = False
    try:
        output, output_identity = _create_diagnostic_root(preflight)
        root_created = True
        source_relative = _safe_relative(preflight.failed_row["source_relative_path"])
        _revalidate_current_replay_authority(
            preflight, authority=authority, mount_validator=mount_validator
        )
        counters.register_hash(preflight.source_path)
        if _sha256_file(
            preflight.source_path, expected_identity=preflight.source_identity
        ) != preflight.local_sha256:
            _fail("REPLAY_LOCAL_SHA256_MISMATCH", dicom_body_reads=1)
        _revalidate_current_replay_authority(
            preflight, authority=authority, mount_validator=mount_validator
        )

        extraction_output = output / "repaired_extraction"
        os.mkdir(extraction_output, mode=0o700)
        _private_directory_identity(extraction_output)
        previous_umask = os.umask(0o077)
        try:
            _revalidate_current_replay_authority(
                preflight, authority=authority, mount_validator=mount_validator
            )
            counters.register_decode(preflight.source_path)
            repaired = dict(
                extractor(
                    {
                        "subject_id": preflight.failed_row["subject_id"],
                        "study_id": preflight.failed_row["study_id"],
                        "smoke_role": "production_selected",
                        "source_relative_path": source_relative,
                        "download_sha256": preflight.local_sha256,
                        reconstruction.REPLAY_ORDINARY_TOPOLOGY_RECORD_KEY: (
                            reconstruction.REPLAY_REQUIRED_ORDINARY_FAILURE_TOPOLOGY
                        ),
                    },
                    str(preflight.objects_root),
                    str(extraction_output),
                )
            )
        finally:
            os.umask(previous_umask)
        _revalidate_current_replay_authority(
            preflight, authority=authority, mount_validator=mount_validator
        )
        observation_bytes, observation_sha256 = _write_json_no_clobber(
            output / "technical_replay_observation.restricted.json",
            {
                "schema_version": 1,
                "artifact_type": "lvef_c3_r3e_replay_technical_observation_v1",
                "technical_provenance": _technical_projection(repaired),
                "identifiers_emitted": False,
                "locators_emitted": False,
                "paths_emitted": False,
            },
            expected_parent_identity=output_identity,
        )
        _validate_ordinary_reproduction(repaired)
        if repaired.get("write_ok") is not True:
            _fail("REPLAY_FALLBACK_FAILED", dicom_body_reads=1)
        _validate_repaired_row(
            repaired, legacy_failed_row=preflight.failed_row
        )
        diagnostic_npz_bytes, diagnostic_npz_sha256 = _validate_repaired_npz(
            repaired,
            extraction_output,
            expected_source_relative=source_relative,
            expected_source_sha256=preflight.local_sha256,
        )
        comparison = {
            "schema_version": 1,
            "artifact_type": (
                "lvef_c3_r3e_one_object_preprocessing_replay_comparison_v1"
            ),
            "status": "PASS_REPAIRED_EXTRACTION",
            "confirmed_original_failure_class": (
                "SAMPLED_SIGNAL_QUALITY_GATE_FAILURE"
            ),
            "legacy_b805": _technical_projection(preflight.failed_row),
            "repaired": _technical_projection(repaired),
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
        }
        comparison_bytes, comparison_sha256 = _write_json_no_clobber(
            output / "technical_comparison.restricted.json",
            comparison,
            expected_parent_identity=output_identity,
        )
        _revalidate_current_replay_authority(
            preflight, authority=authority, mount_validator=mount_validator
        )
        after, after_root_identity = (
            validate_immutable_legacy_attempt_effective_privacy(
                preflight.attempt_root, authority
            )
        )
        if (
            preflight.original_inventory != after
            or preflight.attempt_root_identity != after_root_identity
        ):
            _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_MUTATED", dicom_body_reads=1)
        summary = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_r3e_one_object_preprocessing_replay_summary_v1",
            "status": "PASS_ONE_OBJECT_PREPROCESSING_REPLAY",
            "replay_governing_commit": governing_commit,
            "original_execution_commit": authority.execution_commit,
            "original_attempt_file_count": preflight.original_inventory["file_count"],
            "original_attempt_total_bytes": preflight.original_inventory["total_bytes"],
            "opaque_d4_metadata_tree_authority_sha256": (
                authority.opaque_d4_metadata_tree_sha256
            ),
            "opaque_d4_hash_recomputed": False,
            "runtime_metadata_stat_snapshot_before_sha256": preflight.original_inventory[
                "runtime_metadata_stat_snapshot_sha256"
            ],
            "runtime_metadata_stat_snapshot_after_sha256": after[
                "runtime_metadata_stat_snapshot_sha256"
            ],
            "original_attempt_metadata_unchanged": True,
            "original_attempt_mode_histogram_unchanged": True,
            "legacy_mode_classification": "EFFECTIVELY_PRIVATE_COMPATIBLE",
            "legacy_mode_histogram": "PASS_FROZEN_EXACT",
            "legacy_effective_privacy": "PASS",
            "historical_device_namespace_reconciliation": (
                preflight.device_reconciliation.status
            ),
            "historical_current_device_values_differ": (
                preflight.device_reconciliation.historical_and_current_differ
            ),
            "current_mount_authority": "PASS",
            "current_namespace_topology": "PASS",
            "current_namespace_file_identity": "PASS",
            "nondevice_download_receipt_authority": "PASS",
            "replay_input_authority": "PASS",
            "source_dicom_objects_read": counters.unique_dicom_objects_accessed,
            "unique_dicom_objects_accessed": counters.unique_dicom_objects_accessed,
            "local_content_hash_passes": counters.local_content_hash_passes,
            "pydicom_decode_invocations": counters.pydicom_decode_invocations,
            "dicom_body_read_calls": counters.dicom_body_read_calls,
            "ordinary_path_reproduction": "PASS_BOTH_SAMPLED_GATES_FAILED",
            "ordinary_sampled_nonzero_gate": "FAIL",
            "ordinary_sampled_temporal_gate": "FAIL",
            "ordinary_path_failure_class": (
                "SAMPLED_SIGNAL_QUALITY_GATE_FAILURE"
            ),
            "repaired_write_ok": True,
            "selected_preprocessing_path": repaired[
                "selected_preprocessing_path"
            ],
            "temporal_sampling_policy": repaired["temporal_sampling_policy"],
            "fallback_status": repaired["fallback_status"],
            "full32_signal_gates": "PASS",
            "encoder_visible16_signal_gates": "PASS",
            "diagnostic_npz_validation": "PASS_DEEP_REOPEN",
            "diagnostic_npz_artifact_role": "DIAGNOSTIC_REPAIRED_OBJECT_NPZ",
            "diagnostic_npz_bytes": diagnostic_npz_bytes,
            "diagnostic_npz_sha256": diagnostic_npz_sha256,
            "cloud_requests": 0,
            "qsub_submissions": 0,
            "gpu_executions": 0,
            "echoprime_executions": 0,
            "model_fitting": 0,
            "prediction_generation": 0,
            "confirmatory_performance_accessed": False,
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
        }
        aggregate_bytes, aggregate_sha256 = _write_json_no_clobber(
            output / "one_object_replay.aggregate_safe.json",
            summary,
            expected_parent_identity=output_identity,
        )
        result = dict(summary)
        result["_technical_observation_receipt"] = {
            "basename": "technical_replay_observation.restricted.json",
            "bytes": observation_bytes,
            "sha256": observation_sha256,
        }
        result["_technical_comparison_receipt"] = {
            "basename": "technical_comparison.restricted.json",
            "bytes": comparison_bytes,
            "sha256": comparison_sha256,
        }
        result["_aggregate_safe_receipt"] = {
            "basename": "one_object_replay.aggregate_safe.json",
            "bytes": aggregate_bytes,
            "sha256": aggregate_sha256,
        }
        return result
    except ReplayError as exc:
        raise _attach_runtime_state(
            exc, counters=counters, diagnostic_root_created=root_created
        )
    except Exception as exc:
        sanitized = ReplayError("ONE_OBJECT_REPLAY_UNEXPECTED_SANITIZED_FAILURE")
        _attach_runtime_state(
            sanitized, counters=counters, diagnostic_root_created=root_created
        )
        raise sanitized from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def _print_effect_boundary(
    *,
    unique_objects: int,
    hash_passes: int,
    decode_invocations: int,
    diagnostic_root_created: bool,
) -> None:
    print(f"UNIQUE_DICOM_OBJECTS_ACCESSED={unique_objects}")
    print(f"LOCAL_CONTENT_HASH_PASSES={hash_passes}")
    print(f"PYDICOM_DECODE_INVOCATIONS={decode_invocations}")
    print(f"DICOM_BODY_READS={hash_passes + decode_invocations}")
    print(f"DICOM_BODY_READ_CALLS={hash_passes + decode_invocations}")
    print(
        "DIAGNOSTIC_ROOT_CREATED="
        f"{'YES' if diagnostic_root_created else 'NO'}"
    )
    print("IDENTIFIERS_EMITTED=0")
    print("LOCATORS_EMITTED=0")
    print("CLOUD_REQUESTS=0")
    print("OBJECT_DOWNLOADS=0")
    print("QSUB_SUBMISSIONS=0")
    print("GPU_EXECUTIONS=0")
    print("EMBEDDING_GENERATIONS=0")
    print("MODEL_FITTING=0")
    print("PREDICTION_GENERATION=0")
    print("CONFIRMATORY_PERFORMANCE_ACCESSED=NO")


def _print_r3e_authority_passes() -> None:
    print("LEGACY_MODE_CLASSIFICATION=EFFECTIVELY_PRIVATE_COMPATIBLE")
    print("LEGACY_MODE_HISTOGRAM=PASS")
    print("LEGACY_EFFECTIVE_PRIVACY=PASS")
    print(
        "HISTORICAL_DEVICE_NAMESPACE_RECONCILIATION="
        "CROSS_NODE_DEVICE_NAMESPACE_RECONCILED"
    )
    print("HISTORICAL_CURRENT_DEVICE_VALUES_DIFFER=YES")
    print("CURRENT_MOUNT_AUTHORITY=PASS")
    print("CURRENT_NAMESPACE_TOPOLOGY=PASS")
    print("CURRENT_NAMESPACE_FILE_IDENTITY=PASS")
    print("NONDEVICE_DOWNLOAD_RECEIPT_AUTHORITY=PASS")
    print("REPLAY_INPUT_AUTHORITY=PASS")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.execute is arguments.preflight_only:
        print("LVEF_C3_ONE_OBJECT_REPLAY=BLOCKED_EXECUTE_FLAG_REQUIRED")
        _print_effect_boundary(
            unique_objects=0,
            hash_passes=0,
            decode_invocations=0,
            diagnostic_root_created=False,
        )
        return 78
    try:
        if arguments.preflight_only:
            preflight = run_preflight(
                governing_commit=arguments.governing_commit,
                diagnostic_root=arguments.diagnostic_root,
            )
        else:
            summary = run_replay(
                governing_commit=arguments.governing_commit,
                diagnostic_root=arguments.diagnostic_root,
            )
    except ReplayError as exc:
        prefix = "R3E_REPLAY_PREFLIGHT" if arguments.preflight_only else (
            "LVEF_C3_ONE_OBJECT_REPLAY"
        )
        print(f"{prefix}=BLOCKED_{exc.code}")
        _print_effect_boundary(
            unique_objects=exc.unique_dicom_objects_accessed,
            hash_passes=exc.local_content_hash_passes,
            decode_invocations=exc.pydicom_decode_invocations,
            diagnostic_root_created=exc.diagnostic_root_created,
        )
        return 78
    if arguments.preflight_only:
        if (
            preflight.device_reconciliation.status
            != "CROSS_NODE_DEVICE_NAMESPACE_RECONCILED"
            or not preflight.device_reconciliation.historical_and_current_differ
            or preflight.mount_authority.status
            != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT"
        ):
            print("R3E_REPLAY_PREFLIGHT=BLOCKED_REPLAY_PREFLIGHT_STATUS_INVALID")
            _print_effect_boundary(
                unique_objects=0,
                hash_passes=0,
                decode_invocations=0,
                diagnostic_root_created=False,
            )
            return 78
        print("R3E_REPLAY_PREFLIGHT=PASS_ZERO_BODY_NO_ROOT")
        _print_r3e_authority_passes()
        _print_effect_boundary(
            unique_objects=0,
            hash_passes=0,
            decode_invocations=0,
            diagnostic_root_created=False,
        )
        return 0
    print("LVEF_C3_ONE_OBJECT_REPLAY=PASS")
    print("ORIGINAL_ATTEMPT_METADATA_UNCHANGED=YES")
    print("ORIGINAL_ATTEMPT_MODE_HISTOGRAM_AFTER_REPLAY=IDENTICAL")
    _print_r3e_authority_passes()
    print("ORDINARY_PATH_REPRODUCTION=PASS")
    print("ORDINARY_SAMPLED_NONZERO_GATE=FAIL")
    print("ORDINARY_SAMPLED_TEMPORAL_GATE=FAIL")
    print("ORDINARY_PATH_FAILURE_CLASS=SAMPLED_SIGNAL_QUALITY_GATE_FAILURE")
    print("FALLBACK_PATH=PASS")
    print("FULL32_SIGNAL_GATES=PASS")
    print("ENCODER_VISIBLE16_SIGNAL_GATES=PASS")
    print("R3E_DIAGNOSTIC_NPZ_VALIDATION=PASS_DEEP_REOPEN")
    print("DIAGNOSTIC_NPZ_ARTIFACT_ROLE=DIAGNOSTIC_REPAIRED_OBJECT_NPZ")
    print(f"DIAGNOSTIC_NPZ_BYTES={summary['diagnostic_npz_bytes']}")
    print(f"DIAGNOSTIC_NPZ_SHA256={summary['diagnostic_npz_sha256']}")
    for label, field in (
        ("TECHNICAL_OBSERVATION_RECEIPT", "_technical_observation_receipt"),
        ("TECHNICAL_COMPARISON_RECEIPT", "_technical_comparison_receipt"),
        ("AGGREGATE_SAFE_RECEIPT", "_aggregate_safe_receipt"),
    ):
        receipt = summary[field]
        print(f"{label}_BASENAME={receipt['basename']}")
        print(f"{label}_BYTES={receipt['bytes']}")
        print(f"{label}_SHA256={receipt['sha256']}")
    _print_effect_boundary(
        unique_objects=int(summary["unique_dicom_objects_accessed"]),
        hash_passes=int(summary["local_content_hash_passes"]),
        decode_invocations=int(summary["pydicom_decode_invocations"]),
        diagnostic_root_created=True,
    )
    print("ECHOPRIME_EXECUTIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
