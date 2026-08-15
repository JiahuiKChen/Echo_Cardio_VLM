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
import csv
from dataclasses import dataclass
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


EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
ALLOWED_DIAGNOSTIC_PREFIX = PRODUCTION_ROOT / "owner_private"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
DIAGNOSTIC_NAME_RE = re.compile(
    r"^lvef_c3_r3a_one_object_replay_[a-z0-9][a-z0-9_-]{7,63}$"
)
MAXIMUM_MANIFEST_BYTES = 128 * 1024 * 1024
MAXIMUM_REPAIRED_NPZ_BYTES = 32 * 1024 * 1024


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
)


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
    def __init__(self, code: str, *, dicom_body_reads: int = 0):
        super().__init__(code)
        self.code = code
        self.dicom_body_reads = dicom_body_reads


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
        ):
            _fail("ONE_OBJECT_REPLAY_PRIVATE_FILE_CHANGED_DURING_READ")
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def _read_json(path: Path, *, maximum_bytes: int = 1_048_576) -> dict[str, Any]:
    payload = _read_owner_private_file(path, maximum_bytes=maximum_bytes)
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


def _read_csv_exact(path: Path, header: Sequence[str]) -> list[dict[str, str]]:
    payload = _read_owner_private_file(path, maximum_bytes=MAXIMUM_MANIFEST_BYTES)
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
        ):
            _fail(invalid_code, dicom_body_reads=dicom_body_reads)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_owner_private_regular(
        path,
        invalid_code="ONE_OBJECT_REPLAY_SOURCE_DICOM_INVALID",
        dicom_body_reads=1,
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


def _attempt_metadata_inventory(attempt_root: Path) -> dict[str, Any]:
    """Hash a dev/inode/ctime-complete stat snapshot, not the opaque D4 hash."""

    _require_no_symlink_components(attempt_root)
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_INVALID")
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for current_text, directories, filenames in os.walk(
        attempt_root, topdown=True, followlinks=False
    ):
        current = Path(current_text)
        directories.sort()
        filenames.sort()
        children = [current / name for name in filenames]
        if current == attempt_root:
            children.insert(0, current)
        children.extend(current / name for name in directories)
        for child in children:
            try:
                item = os.lstat(child)
            except OSError as exc:
                raise ReplayError("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_INVALID") from exc
            if stat.S_ISLNK(item.st_mode) or item.st_uid != os.geteuid():
                _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_NOT_OWNER_PRIVATE")
            mode = stat.S_IMODE(item.st_mode)
            if mode & 0o077:
                _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_NOT_OWNER_PRIVATE")
            if stat.S_ISDIR(item.st_mode):
                kind = "directory"
            elif stat.S_ISREG(item.st_mode):
                kind = "file"
                file_count += 1
                total_bytes += item.st_size
            else:
                _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_NONREGULAR_ENTRY")
            relative = "." if child == attempt_root else child.relative_to(
                attempt_root
            ).as_posix()
            record = {
                "relative": relative,
                "kind": kind,
                "mode": mode,
                "uid": item.st_uid,
                "gid": item.st_gid,
                "st_dev": item.st_dev,
                "st_ino": item.st_ino,
                "size": item.st_size,
                "mtime_ns": item.st_mtime_ns,
                "st_ctime_ns": item.st_ctime_ns,
            }
            digest.update(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
                + b"\n"
            )
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "runtime_metadata_stat_snapshot_sha256": digest.hexdigest(),
    }


def _validate_original_inventory(
    observed: Mapping[str, Any], authority: OriginalAttemptAuthority
) -> None:
    snapshot_sha256 = observed.get("runtime_metadata_stat_snapshot_sha256")
    if (
        observed.get("file_count") != authority.file_count
        or observed.get("total_bytes") != authority.total_bytes
        or not isinstance(snapshot_sha256, str)
        or SHA256_RE.fullmatch(snapshot_sha256) is None
    ):
        _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_INVENTORY_MISMATCH")


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
) -> dict[str, str]:
    if len(rows) != authority.download_rows:
        _fail("ONE_OBJECT_REPLAY_DOWNLOAD_MANIFEST_COUNT_MISMATCH")
    if any(not _strict_bool(row["download_ok"]) for row in rows):
        _fail("ONE_OBJECT_REPLAY_DOWNLOAD_MANIFEST_NOT_VERIFIED")
    physical_key = failed["physical_source_key"]
    matches = [row for row in rows if row["physical_source_key"] == physical_key]
    if len(matches) != 1:
        _fail("ONE_OBJECT_REPLAY_DOWNLOAD_BINDING_NOT_UNIQUE")
    matched = dict(matches[0])
    _safe_relative(matched["source_relative_path"])
    if (
        matched["subject_id"] != failed["subject_id"]
        or matched["study_id"] != failed["study_id"]
        or matched["observed_sha256"] != failed["source_sha256"]
        or SHA256_RE.fullmatch(matched["observed_sha256"]) is None
    ):
        _fail("ONE_OBJECT_REPLAY_DOWNLOAD_BINDING_MISMATCH")
    return matched


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _prepare_diagnostic_root(
    path: Path, *, allowed_prefix: Path, original_attempt_root: Path
) -> Path:
    if (
        not path.is_absolute()
        or Path(os.path.abspath(path)) != path
        or DIAGNOSTIC_NAME_RE.fullmatch(path.name) is None
        or os.path.lexists(path)
    ):
        _fail("ONE_OBJECT_REPLAY_DIAGNOSTIC_ROOT_INVALID")
    _require_no_symlink_components(path.parent)
    _require_no_symlink_components(allowed_prefix)
    allowed = allowed_prefix.resolve(strict=True)
    parent = path.parent.resolve(strict=True)
    candidate = parent / path.name
    if (
        not parent.is_dir()
        or parent != allowed
        or _inside(candidate, original_attempt_root)
        or _inside(candidate, REPOSITORY_ROOT)
    ):
        _fail("ONE_OBJECT_REPLAY_DIAGNOSTIC_ROOT_INVALID")
    try:
        candidate.mkdir(mode=0o700)
    except OSError as exc:
        raise ReplayError("ONE_OBJECT_REPLAY_DIAGNOSTIC_ROOT_CREATE_FAILED") from exc
    item = candidate.stat(follow_symlinks=False)
    if item.st_uid != os.geteuid() or stat.S_IMODE(item.st_mode) != 0o700:
        _fail("ONE_OBJECT_REPLAY_DIAGNOSTIC_ROOT_NOT_OWNER_PRIVATE")
    return candidate


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


def _validate_repaired_npz(
    row: Mapping[str, Any],
    extraction_output: Path,
    *,
    expected_source_relative: str,
) -> None:
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
    observed_sha256 = _sha256_owner_private_regular(
        output_path,
        invalid_code="ONE_OBJECT_REPLAY_REPAIRED_NPZ_INVALID",
        dicom_body_reads=1,
        maximum_bytes=MAXIMUM_REPAIRED_NPZ_BYTES,
    )
    if observed_sha256 != row.get("npz_sha256"):
        _fail("ONE_OBJECT_REPLAY_REPAIRED_NPZ_HASH_MISMATCH", dicom_body_reads=1)


def _write_json_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
    if os.path.lexists(path):
        _fail("ONE_OBJECT_REPLAY_OUTPUT_COLLISION", dicom_body_reads=1)
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _fail("ONE_OBJECT_REPLAY_OUTPUT_COLLISION", dicom_body_reads=1)
        os.unlink(temporary)
    except OSError as exc:
        raise ReplayError(
            "ONE_OBJECT_REPLAY_OUTPUT_WRITE_FAILED", dicom_body_reads=1
        ) from exc
    finally:
        if os.path.lexists(temporary):
            try:
                item = os.lstat(temporary)
                if stat.S_ISREG(item.st_mode) and item.st_uid == os.geteuid():
                    os.unlink(temporary)
            except OSError:
                pass


def run_replay(
    *,
    governing_commit: str,
    diagnostic_root: Path,
    production_root: Path = PRODUCTION_ROOT,
    allowed_diagnostic_prefix: Path = ALLOWED_DIAGNOSTIC_PREFIX,
    repository: Path = REPOSITORY_ROOT,
    authority: OriginalAttemptAuthority = ORIGINAL_AUTHORITY,
    extractor: Callable[[Mapping[str, Any], str, str], Mapping[str, Any]] = (
        reconstruction._extract_one
    ),
    git_validator: Callable[..., None] = validate_git_authority,
) -> dict[str, Any]:
    """Run the exact one-object local preprocessing replay after all gates."""

    body_reads = 0
    try:
        git_validator(
            repository, governing_commit, original_commit=authority.execution_commit
        )
        attempt_root = production_root / "attempts" / authority.attempt_id
        _require_no_symlink_components(attempt_root)
        before = _attempt_metadata_inventory(attempt_root)
        _validate_original_inventory(before, authority)
        batch_cache = attempt_root / "extracted_cache" / authority.batch_id
        partial = batch_cache / "dicom_extraction.partial"
        _validate_failure_summary(_read_json(partial / "failure.summary.json"))
        extraction_rows = _read_csv_exact(
            partial / "extraction_manifest.restricted.csv",
            LEGACY_B805_EXTRACTION_MANIFEST_HEADER,
        )
        failed = _validate_legacy_failed_row(extraction_rows, authority)
        download_batch = attempt_root / "raw" / authority.batch_id
        download_rows = _read_csv_exact(
            download_batch / "verified_download_manifest.restricted.csv",
            VERIFIED_DOWNLOAD_MANIFEST_HEADER,
        )
        downloaded = _validate_download_binding(download_rows, failed, authority)
        output = _prepare_diagnostic_root(
            diagnostic_root,
            allowed_prefix=allowed_diagnostic_prefix,
            original_attempt_root=attempt_root,
        )
        objects_root = download_batch / "objects"
        source_relative = _safe_relative(failed["source_relative_path"])
        source_path = objects_root.joinpath(*PurePosixPath(source_relative).parts)
        _require_no_symlink_components(source_path)
        body_reads = 1
        if _sha256_file(source_path) != downloaded["observed_sha256"]:
            _fail("ONE_OBJECT_REPLAY_SOURCE_DICOM_HASH_MISMATCH", dicom_body_reads=1)

        extraction_output = output / "repaired_extraction"
        extraction_output.mkdir(mode=0o700)
        previous_umask = os.umask(0o077)
        try:
            repaired = dict(
                extractor(
                    {
                        "subject_id": failed["subject_id"],
                        "study_id": failed["study_id"],
                        "smoke_role": "production_selected",
                        "source_relative_path": source_relative,
                        "download_sha256": downloaded["observed_sha256"],
                    },
                    str(objects_root),
                    str(extraction_output),
                )
            )
        finally:
            os.umask(previous_umask)
        _validate_repaired_row(repaired, legacy_failed_row=failed)
        _validate_repaired_npz(
            repaired,
            extraction_output,
            expected_source_relative=source_relative,
        )
        comparison = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_one_object_preprocessing_replay_comparison_v1",
            "status": "PASS_REPAIRED_EXTRACTION",
            "confirmed_original_failure_class": (
                "SAMPLED_SIGNAL_QUALITY_GATE_FAILURE"
            ),
            "legacy_b805": _technical_projection(failed),
            "repaired": _technical_projection(repaired),
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
        }
        _write_json_no_clobber(
            output / "technical_comparison.restricted.json", comparison
        )
        after = _attempt_metadata_inventory(attempt_root)
        _validate_original_inventory(after, authority)
        if before != after:
            _fail("ONE_OBJECT_REPLAY_ORIGINAL_ATTEMPT_MUTATED", dicom_body_reads=1)
        summary = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_one_object_preprocessing_replay_summary_v1",
            "status": "PASS_ONE_OBJECT_PREPROCESSING_REPLAY",
            "replay_governing_commit": governing_commit,
            "original_execution_commit": authority.execution_commit,
            "original_attempt_file_count": before["file_count"],
            "original_attempt_total_bytes": before["total_bytes"],
            "opaque_d4_metadata_tree_authority_sha256": (
                authority.opaque_d4_metadata_tree_sha256
            ),
            "opaque_d4_hash_recomputed": False,
            "runtime_metadata_stat_snapshot_before_sha256": before[
                "runtime_metadata_stat_snapshot_sha256"
            ],
            "runtime_metadata_stat_snapshot_after_sha256": after[
                "runtime_metadata_stat_snapshot_sha256"
            ],
            "original_attempt_metadata_unchanged": True,
            "source_dicom_objects_read": 1,
            "repaired_write_ok": True,
            "selected_preprocessing_path": repaired[
                "selected_preprocessing_path"
            ],
            "temporal_sampling_policy": repaired["temporal_sampling_policy"],
            "fallback_status": repaired["fallback_status"],
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
        _write_json_no_clobber(
            output / "one_object_replay.aggregate_safe.json", summary
        )
        return summary
    except ReplayError as exc:
        if body_reads and exc.dicom_body_reads == 0:
            exc.dicom_body_reads = 1
        raise
    except Exception as exc:
        raise ReplayError(
            "ONE_OBJECT_REPLAY_UNEXPECTED_SANITIZED_FAILURE",
            dicom_body_reads=body_reads,
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.execute:
        print("LVEF_C3_ONE_OBJECT_REPLAY=BLOCKED_EXECUTE_FLAG_REQUIRED")
        print("CLOUD_REQUESTS=0")
        print("QSUB_SUBMISSIONS=0")
        print("DICOM_BODY_READS=0")
        print("GPU_EXECUTIONS=0")
        print("MODEL_FITTING=0")
        print("CONFIRMATORY_PERFORMANCE_ACCESSED=NO")
        return 78
    try:
        run_replay(
            governing_commit=arguments.governing_commit,
            diagnostic_root=arguments.diagnostic_root,
        )
    except ReplayError as exc:
        print(f"LVEF_C3_ONE_OBJECT_REPLAY=BLOCKED_{exc.code}")
        print("IDENTIFIERS_EMITTED=0")
        print("LOCATORS_EMITTED=0")
        print("CLOUD_REQUESTS=0")
        print("QSUB_SUBMISSIONS=0")
        print(f"DICOM_BODY_READS={exc.dicom_body_reads}")
        print("GPU_EXECUTIONS=0")
        print("MODEL_FITTING=0")
        print("CONFIRMATORY_PERFORMANCE_ACCESSED=NO")
        return 78
    print("LVEF_C3_ONE_OBJECT_REPLAY=PASS")
    print("ORIGINAL_ATTEMPT_METADATA_UNCHANGED=YES")
    print("SOURCE_DICOM_OBJECTS_READ=1")
    print("IDENTIFIERS_EMITTED=0")
    print("LOCATORS_EMITTED=0")
    print("CLOUD_REQUESTS=0")
    print("QSUB_SUBMISSIONS=0")
    print("GPU_EXECUTIONS=0")
    print("ECHOPRIME_EXECUTIONS=0")
    print("MODEL_FITTING=0")
    print("PREDICTION_GENERATION=0")
    print("CONFIRMATORY_PERFORMANCE_ACCESSED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
