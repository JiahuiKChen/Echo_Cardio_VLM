#!/usr/bin/env python3
"""Create and independently verify a scoped Phase 1E-A preservation pack.

The detailed manifest and metadata are restricted artifacts.  The aggregate
JSON contains counts, byte totals, hashes, and pass/fail flags only.  The
utility never deletes, follows symlinks, or broadens scope beyond the explicit
run root.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterator, Mapping, Sequence

import pandas as pd

import lvef_reconstruction_smoke as reconstruction


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MANIFEST_NAME = "preservation_manifest.tsv"
METADATA_NAME = "preservation_metadata.json"
VERIFICATION_NAME = "preservation_verification.json"
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
REQUIRED_ENVIRONMENT_KEYS = {
    "python_version",
    "python_executable",
    "python_executable_sha256",
    "numpy_version",
    "pandas_version",
    "pydicom_version",
    "opencv_version",
    "torch_version",
    "torchvision_version",
    "scikit_learn_version",
    "cuda_version",
    "cudnn_version",
    "gpu_name",
    "gpu_uuid",
    "operating_system",
    "package_inventory",
    "source_commit",
    "repository_head",
    "repository_branch",
    "repository_clean",
    "config_sha256",
    "checkpoint_sha256",
    "technical_smoke_source_manifest_sha256",
    "script_sha256",
    "scheduler",
    "scheduler_job_id",
}
REQUIRED_SCRIPT_IDENTITIES = {
    "audit_lvef_reconstruction_smoke_run.py",
    "build_lvef_reconstruction_source_manifest.py",
    "capture_lvef_reconstruction_environment.py",
    "download_lvef_reconstruction_smoke.py",
    "lvef_reconstruction_smoke.py",
    "preserve_lvef_reconstruction_smoke.py",
}
CHECKPOINT_FILENAME = "echo_prime_encoder.pt"
CHECKPOINT_BYTES = 138_642_379
CHECKPOINT_SHA256 = "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
AGGREGATE_ARTIFACT_RELATIVE_PATHS = {
    "source_manifest_summary": "aggregate/source/reconstruction_source_manifest.summary.json",
    "source_manifest_safety": "aggregate/source/reconstruction_source_manifest_safety_gate.json",
    "download": "aggregate/download.json",
    "download_audit": "aggregate/download_audit.json",
    "dicom_audit": "aggregate/dicom_audit.json",
    "run_a_extraction": "aggregate/run_a/extraction.json",
    "run_a_embedding": "aggregate/run_a/embedding.json",
    "run_a_pooling": "aggregate/run_a/pooling.json",
    "run_b_extraction": "aggregate/run_b/extraction.json",
    "run_b_embedding": "aggregate/run_b/embedding.json",
    "run_b_pooling": "aggregate/run_b/pooling.json",
    "reproducibility": "aggregate/reproducibility.json",
}


class PreservationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileRecord:
    relative_path: str
    size_bytes: int
    sha256: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_inside_repository(path: Path, code: str) -> None:
    repository = Path(__file__).resolve().parents[1]
    resolved = path.expanduser().resolve(strict=False)
    if resolved == repository or _is_relative_to(resolved, repository):
        raise PreservationError(code)


def validate_safe_relative_path(value: str) -> str:
    if not value or value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise PreservationError("UNSAFE_RELATIVE_PATH")
    path = PurePosixPath(value)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PreservationError("UNSAFE_RELATIVE_PATH")
    if any(not SAFE_PART_RE.fullmatch(part) for part in path.parts):
        raise PreservationError("UNSAFE_RELATIVE_PATH")
    return path.as_posix()


def validate_run_root(path: Path) -> Path:
    expanded = path.expanduser()
    _reject_inside_repository(expanded, "RUN_ROOT_INSIDE_REPOSITORY")
    if expanded.is_symlink() or not expanded.is_dir():
        raise PreservationError("RUN_ROOT_NOT_REGULAR_DIRECTORY")
    resolved = expanded.resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise PreservationError("RUN_ROOT_TOO_BROAD")
    return resolved


def _walk_regular_files(root: Path) -> Iterator[Path]:
    def visit(directory: Path) -> Iterator[Path]:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
        for entry in entries:
            candidate = Path(entry.path)
            if entry.is_symlink():
                raise PreservationError("SYMLINK_IN_RUN_SCOPE")
            if entry.is_dir(follow_symlinks=False):
                yield from visit(candidate)
            elif entry.is_file(follow_symlinks=False):
                yield candidate
            else:
                raise PreservationError("NONREGULAR_ENTRY_IN_RUN_SCOPE")

    yield from visit(root)


def build_file_records(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in _walk_regular_files(root):
        relative = validate_safe_relative_path(path.relative_to(root).as_posix())
        stat_before = path.stat(follow_symlinks=False)
        digest = sha256_file(path)
        stat_after = path.stat(follow_symlinks=False)
        if (
            stat_before.st_size != stat_after.st_size
            or stat_before.st_mtime_ns != stat_after.st_mtime_ns
            or stat_before.st_ino != stat_after.st_ino
        ):
            raise PreservationError("FILE_CHANGED_DURING_HASHING")
        records.append(
            FileRecord(
                relative_path=relative,
                size_bytes=int(stat_after.st_size),
                sha256=digest,
            )
        )
    records.sort(key=lambda item: item.relative_path)
    if not records:
        raise PreservationError("EMPTY_RUN_SCOPE")
    if len({item.relative_path for item in records}) != len(records):
        raise PreservationError("DUPLICATE_RELATIVE_PATH")
    return records


def write_manifest_exclusive(path: Path, records: Sequence[FileRecord]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["relative_path", "size_bytes", "sha256"])
        for item in records:
            writer.writerow([item.relative_path, item.size_bytes, item.sha256])


def read_manifest(path: Path) -> list[FileRecord]:
    if path.is_symlink() or not path.is_file():
        raise PreservationError("PRESERVATION_MANIFEST_NOT_REGULAR")
    records: list[FileRecord] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["relative_path", "size_bytes", "sha256"]:
            raise PreservationError("INVALID_PRESERVATION_MANIFEST_HEADER")
        for row in reader:
            relative = validate_safe_relative_path(str(row["relative_path"]))
            size_text = str(row["size_bytes"])
            digest = str(row["sha256"]).lower()
            if not size_text.isdigit() or not SHA256_RE.fullmatch(digest):
                raise PreservationError("INVALID_PRESERVATION_MANIFEST_ROW")
            records.append(FileRecord(relative, int(size_text), digest))
    if not records or len({item.relative_path for item in records}) != len(records):
        raise PreservationError("INVALID_PRESERVATION_MANIFEST_SET")
    if records != sorted(records, key=lambda item: item.relative_path):
        raise PreservationError("PRESERVATION_MANIFEST_NOT_SORTED")
    return records


def verify_manifest_independently(root: Path, manifest_path: Path) -> dict[str, Any]:
    declared = read_manifest(manifest_path)
    declared_map = {item.relative_path: item for item in declared}
    observed_paths: set[str] = set()
    total_bytes = 0
    for path in _walk_regular_files(root):
        relative = validate_safe_relative_path(path.relative_to(root).as_posix())
        observed_paths.add(relative)
        expected = declared_map.get(relative)
        if expected is None:
            raise PreservationError("UNLISTED_FILE_DURING_VERIFICATION")
        stat_before = path.stat(follow_symlinks=False)
        digest = sha256_file(path)
        stat_after = path.stat(follow_symlinks=False)
        if (
            stat_before.st_size != stat_after.st_size
            or stat_before.st_mtime_ns != stat_after.st_mtime_ns
            or stat_before.st_ino != stat_after.st_ino
        ):
            raise PreservationError("FILE_CHANGED_DURING_VERIFICATION")
        if stat_after.st_size != expected.size_bytes:
            raise PreservationError("SIZE_MISMATCH_DURING_VERIFICATION")
        if digest != expected.sha256:
            raise PreservationError("SHA256_MISMATCH_DURING_VERIFICATION")
        total_bytes += int(stat_after.st_size)
    if observed_paths != set(declared_map):
        raise PreservationError("MISSING_FILE_DURING_VERIFICATION")
    return {
        "status": "PASS",
        "n_files": len(declared),
        "total_bytes": total_bytes,
        "manifest_sha256": sha256_file(manifest_path),
        "exact_file_set": True,
        "all_sizes_match": True,
        "all_sha256_match": True,
        "no_symlinks": True,
    }


def _validate_regular_metadata_file(path: Path, code: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_file():
        raise PreservationError(code)
    return expanded.resolve()


def _artifact_identity(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size_bytes": int(path.stat().st_size),
        "sha256": sha256_file(path),
    }


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PreservationError(error_code) from exc
    if not isinstance(value, dict):
        raise PreservationError(error_code)
    return value


def _assert_frames_equal(
    observed: pd.DataFrame,
    expected: pd.DataFrame,
    *,
    error_code: str,
) -> None:
    observed_normalized = observed.reset_index(drop=True).astype(object)
    expected_normalized = expected.reset_index(drop=True).astype(object)
    observed_normalized = observed_normalized.where(observed_normalized.notna(), None)
    expected_normalized = expected_normalized.where(expected_normalized.notna(), None)
    try:
        pd.testing.assert_frame_equal(
            observed_normalized,
            expected_normalized,
            check_dtype=False,
            check_like=True,
        )
    except AssertionError as exc:
        raise PreservationError(error_code) from exc


def _verify_aggregate_artifact_hashes(
    run_root: Path, safety_gate: Mapping[str, Any]
) -> dict[str, str]:
    declared = safety_gate.get("artifact_sha256")
    if not isinstance(declared, dict) or set(declared) != set(
        AGGREGATE_ARTIFACT_RELATIVE_PATHS
    ):
        raise PreservationError("AGGREGATE_ARTIFACT_HASH_SET_INVALID")
    observed: dict[str, str] = {}
    for name, relative in sorted(AGGREGATE_ARTIFACT_RELATIVE_PATHS.items()):
        expected = str(declared[name]).lower()
        if not SHA256_RE.fullmatch(expected):
            raise PreservationError("AGGREGATE_ARTIFACT_HASH_INVALID")
        path = run_root / validate_safe_relative_path(relative)
        if path.is_symlink() or not path.is_file():
            raise PreservationError("AGGREGATE_ARTIFACT_MISSING")
        digest = sha256_file(path)
        if digest != expected:
            raise PreservationError("AGGREGATE_ARTIFACT_CHANGED_AFTER_SAFETY_GATE")
        observed[name] = digest
    return observed


def _recompute_reproducibility(run_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    restricted = run_root / "restricted"
    return reconstruction.compare_reproducibility(
        manifest_pairs=(
            (
                "clip_manifest",
                restricted / "run_a/clip_embedding_manifest.csv",
                restricted / "run_b/clip_embedding_manifest.csv",
            ),
            (
                "study_manifest",
                restricted / "run_a/study_embedding_manifest.csv",
                restricted / "run_b/study_embedding_manifest.csv",
            ),
        ),
        array_pairs=(
            (
                "clip_embeddings",
                restricted / "run_a/clip_embeddings_512.npz",
                restricted / "run_b/clip_embeddings_512.npz",
            ),
            (
                "study_embeddings",
                restricted / "run_a/study_embeddings_512.npz",
                restricted / "run_b/study_embeddings_512.npz",
            ),
        ),
        extraction_pairs=(
            (
                "extraction",
                restricted / "run_a/extraction_manifest.csv",
                restricted / "run_a/extracted",
                restricted / "run_b/extraction_manifest.csv",
                restricted / "run_b/extracted",
            ),
        ),
    )


def _restricted_snapshot(run_root: Path) -> dict[str, tuple[int, str]]:
    restricted_root = run_root / "restricted"
    if restricted_root.is_symlink() or not restricted_root.is_dir():
        raise PreservationError("RESTRICTED_RUN_SCOPE_MISSING")
    return {
        f"restricted/{record.relative_path}": (record.size_bytes, record.sha256)
        for record in build_file_records(restricted_root)
    }


def _verify_reproducibility_matches_saved(run_root: Path) -> None:
    saved_aggregate = _read_json_object(
        run_root / AGGREGATE_ARTIFACT_RELATIVE_PATHS["reproducibility"],
        "REPRODUCIBILITY_AGGREGATE_INVALID",
    )
    details_path = run_root / "restricted/reproducibility_details.json"
    bound_details_sha256 = saved_aggregate.get("restricted_details_sha256")
    if (
        not isinstance(bound_details_sha256, str)
        or not SHA256_RE.fullmatch(bound_details_sha256)
    ):
        raise PreservationError("REPRODUCIBILITY_DETAILS_IDENTITY_MISSING")
    if details_path.is_symlink() or not details_path.is_file():
        raise PreservationError("REPRODUCIBILITY_DETAILS_IDENTITY_MISMATCH")
    if sha256_file(details_path) != bound_details_sha256:
        raise PreservationError("REPRODUCIBILITY_DETAILS_IDENTITY_MISMATCH")
    saved_restricted = _read_json_object(
        details_path,
        "REPRODUCIBILITY_DETAILS_INVALID",
    )
    current_restricted, current_aggregate = _recompute_reproducibility(run_root)
    if current_restricted != saved_restricted:
        raise PreservationError("REPRODUCIBILITY_DETAILS_REVALIDATION_MISMATCH")
    comparable_saved_aggregate = dict(saved_aggregate)
    comparable_saved_aggregate.pop("restricted_details_sha256")
    if current_aggregate != comparable_saved_aggregate:
        raise PreservationError("REPRODUCIBILITY_AGGREGATE_REVALIDATION_MISMATCH")
    if current_aggregate.get("status") != "PASS":
        raise PreservationError("REPRODUCIBILITY_REVALIDATION_NOT_PASSED")


def _revalidate_download_authority(
    run_root: Path,
    smoke_manifest: Path,
    smoke_sha256: str,
    saved_download_summary: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Recompute local hashes against the sealed exact-object GCS authority."""

    if (
        saved_download_summary.get("source_manifest_sha256") != smoke_sha256
        or saved_download_summary.get("source_manifest_sha256_verified") is not True
    ):
        raise PreservationError("DOWNLOAD_SMOKE_MANIFEST_IDENTITY_MISMATCH")
    if (
        saved_download_summary.get("status") != "PASS"
        or saved_download_summary.get("remote_metadata_authority")
        != reconstruction.DOWNLOAD_REPORT_AUTHORITY
        or saved_download_summary.get("object_transport_integrity_status")
        != "VERIFIED_ALL_OBJECTS"
    ):
        raise PreservationError("DOWNLOAD_GCS_AUTHORITY_NOT_PASSED")
    source_frame = pd.read_csv(smoke_manifest, low_memory=False)
    download_report_path = run_root / "restricted/download_report.json"
    bound_report_sha256 = saved_download_summary.get("restricted_report_sha256")
    if (
        not isinstance(bound_report_sha256, str)
        or not SHA256_RE.fullmatch(bound_report_sha256)
    ):
        raise PreservationError("DOWNLOAD_REPORT_IDENTITY_MISSING")
    if download_report_path.is_symlink() or not download_report_path.is_file():
        raise PreservationError("DOWNLOAD_REPORT_IDENTITY_MISMATCH")
    if sha256_file(download_report_path) != bound_report_sha256:
        raise PreservationError("DOWNLOAD_REPORT_IDENTITY_MISMATCH")
    downloader_report = reconstruction.read_downloader_report(download_report_path)
    try:
        current_frame, current_summary = reconstruction.audit_downloaded_objects(
            source_frame,
            downloader_report,
            run_root / "restricted/downloads",
            source_manifest_sha256=smoke_sha256,
        )
    except (OSError, ValueError) as exc:
        raise PreservationError("DOWNLOAD_AUTHORITY_REVALIDATION_FAILED") from exc
    if current_summary.get("status") != "PASS":
        raise PreservationError("DOWNLOAD_AUTHORITY_REVALIDATION_FAILED")
    saved_frame = pd.read_csv(
        run_root / "restricted/download_audit.csv", low_memory=False
    )
    _assert_frames_equal(
        current_frame,
        saved_frame,
        error_code="DOWNLOAD_AUDIT_REVALIDATION_MISMATCH",
    )
    if current_summary != _read_json_object(
        run_root / AGGREGATE_ARTIFACT_RELATIVE_PATHS["download_audit"],
        "DOWNLOAD_AUDIT_AGGREGATE_INVALID",
    ):
        raise PreservationError("DOWNLOAD_AUDIT_AGGREGATE_REVALIDATION_MISMATCH")
    return current_frame, current_summary


def revalidate_authoritative_run_artifacts(
    run_root: Path, safety_gate: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, tuple[int, str]]]:
    aggregate_hashes = _verify_aggregate_artifact_hashes(run_root, safety_gate)
    source_summary = _read_json_object(
        run_root / AGGREGATE_ARTIFACT_RELATIVE_PATHS["source_manifest_summary"],
        "SOURCE_SUMMARY_INVALID",
    )
    smoke_manifest = (
        run_root / "restricted/source/technical_smoke_source_manifest_restricted.csv"
    )
    selected_manifest = (
        run_root / "restricted/source/selected_source_manifest_restricted.csv"
    )
    if smoke_manifest.is_symlink() or not smoke_manifest.is_file():
        raise PreservationError("SMOKE_SOURCE_MANIFEST_MISSING")
    if selected_manifest.is_symlink() or not selected_manifest.is_file():
        raise PreservationError("SELECTED_SOURCE_MANIFEST_MISSING")
    smoke_sha256 = sha256_file(smoke_manifest)
    if (
        smoke_sha256 != safety_gate.get("technical_smoke_source_manifest_sha256")
        or smoke_sha256 != source_summary.get("technical_smoke_source_manifest_sha256")
    ):
        raise PreservationError("SMOKE_SOURCE_MANIFEST_CHANGED")
    if sha256_file(selected_manifest) != source_summary.get(
        "selected_source_manifest_sha256"
    ):
        raise PreservationError("SELECTED_SOURCE_MANIFEST_CHANGED")

    saved_download_summary = _read_json_object(
        run_root / AGGREGATE_ARTIFACT_RELATIVE_PATHS["download"],
        "DOWNLOAD_AGGREGATE_INVALID",
    )
    current_download_frame, current_download_summary = _revalidate_download_authority(
        run_root, smoke_manifest, smoke_sha256, saved_download_summary
    )

    current_dicom_frame, current_dicom_summary = reconstruction.audit_dicom_headers(
        current_download_frame,
        run_root / "restricted/downloads",
        workers=1,
    )
    saved_dicom_frame = pd.read_csv(
        run_root / "restricted/dicom_audit.csv", low_memory=False
    )
    _assert_frames_equal(
        current_dicom_frame,
        saved_dicom_frame,
        error_code="DICOM_AUDIT_REVALIDATION_MISMATCH",
    )
    if current_dicom_summary != _read_json_object(
        run_root / AGGREGATE_ARTIFACT_RELATIVE_PATHS["dicom_audit"],
        "DICOM_AUDIT_AGGREGATE_INVALID",
    ):
        raise PreservationError("DICOM_AUDIT_AGGREGATE_REVALIDATION_MISMATCH")

    _verify_reproducibility_matches_saved(run_root)

    snapshot = _restricted_snapshot(run_root)
    return (
        {
            "aggregate_artifact_hashes_reconciled": len(aggregate_hashes),
            "download_audit_recomputed": True,
            "dicom_audit_recomputed": True,
            "reproducibility_recomputed": True,
            "restricted_files_snapshotted": len(snapshot),
        },
        snapshot,
    )


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def prepare_output_dir(path: Path, *, run_root: Path) -> Path:
    expanded = path.expanduser()
    _reject_inside_repository(expanded, "PRESERVATION_OUTPUT_INSIDE_REPOSITORY")
    if expanded.is_symlink():
        raise PreservationError("PRESERVATION_OUTPUT_IS_SYMLINK")
    resolved = expanded.resolve()
    if _is_relative_to(resolved, run_root) or resolved == run_root:
        raise PreservationError("PRESERVATION_OUTPUT_INSIDE_RUN_SCOPE")
    expanded.mkdir(parents=True, exist_ok=True)
    resolved = expanded.resolve()
    if any(resolved.iterdir()):
        raise PreservationError("PRESERVATION_OUTPUT_ALREADY_EXISTS")
    return resolved


def aggregate_template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "FAIL",
        "n_files": 0,
        "total_bytes": 0,
        "manifest_sha256": None,
        "metadata_sha256": None,
        "verification_sha256": None,
        "exact_file_set": False,
        "all_sizes_match": False,
        "all_sha256_match": False,
        "no_symlinks": False,
        "source_commit_recorded": False,
        "config_identity_recorded": False,
        "checkpoint_identity_recorded": False,
        "environment_recorded": False,
        "command_checksum_recorded": False,
        "aggregate_safety_gate_recorded": False,
        "aggregate_artifact_hashes_reconciled": False,
        "download_audit_recomputed": False,
        "dicom_audit_recomputed": False,
        "reproducibility_recomputed": False,
        "restricted_snapshot_reconciled": False,
        "job_metadata_recorded": False,
        "error_code": "NOT_RUN",
    }


def create_preservation_pack(args: argparse.Namespace) -> dict[str, Any]:
    run_root = validate_run_root(args.run_root)
    output_dir = prepare_output_dir(args.restricted_output_dir, run_root=run_root)
    aggregate_output = args.aggregate_output.expanduser()
    _reject_inside_repository(aggregate_output, "AGGREGATE_OUTPUT_INSIDE_REPOSITORY")
    if aggregate_output.exists() or aggregate_output.is_symlink():
        raise PreservationError("AGGREGATE_OUTPUT_ALREADY_EXISTS")
    aggregate_resolved = aggregate_output.resolve()
    if _is_relative_to(aggregate_resolved, run_root) or aggregate_resolved == run_root:
        raise PreservationError("AGGREGATE_OUTPUT_INSIDE_RUN_SCOPE")
    aggregate_output.parent.mkdir(parents=True, exist_ok=True)

    if not COMMIT_RE.fullmatch(args.source_commit.lower()):
        raise PreservationError("INVALID_SOURCE_COMMIT")
    config = _validate_regular_metadata_file(args.config, "CONFIG_NOT_REGULAR_FILE")
    checkpoint = _validate_regular_metadata_file(
        args.checkpoint, "CHECKPOINT_NOT_REGULAR_FILE"
    )
    environment_path = _validate_regular_metadata_file(
        args.environment_json, "ENVIRONMENT_NOT_REGULAR_FILE"
    )
    safety_gate_path = _validate_regular_metadata_file(
        args.aggregate_safety_gate_json, "SAFETY_GATE_NOT_REGULAR_FILE"
    )
    command_path = _validate_regular_metadata_file(
        args.command_file, "COMMAND_FILE_NOT_REGULAR_FILE"
    )
    for evidence_path in (environment_path, safety_gate_path, command_path):
        if not _is_relative_to(evidence_path, run_root):
            raise PreservationError("RUN_EVIDENCE_OUTSIDE_RUN_SCOPE")
    try:
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PreservationError("INVALID_ENVIRONMENT_JSON") from exc
    if not isinstance(environment, dict):
        raise PreservationError("INVALID_ENVIRONMENT_JSON")
    if not REQUIRED_ENVIRONMENT_KEYS.issubset(environment):
        raise PreservationError("INCOMPLETE_ENVIRONMENT_JSON")
    if any(
        environment[key] is None or str(environment[key]).strip() == ""
        for key in REQUIRED_ENVIRONMENT_KEYS - {"script_sha256", "package_inventory"}
    ):
        raise PreservationError("INCOMPLETE_ENVIRONMENT_JSON")
    package_inventory = environment["package_inventory"]
    if (
        not isinstance(package_inventory, list)
        or not package_inventory
        or any(
            not isinstance(item, dict)
            or set(item) != {"name", "version"}
            or not str(item["name"]).strip()
            or not str(item["version"]).strip()
            for item in package_inventory
        )
    ):
        raise PreservationError("INVALID_PACKAGE_INVENTORY")
    script_identities = environment["script_sha256"]
    if not isinstance(script_identities, dict) or set(script_identities) != REQUIRED_SCRIPT_IDENTITIES:
        raise PreservationError("INCOMPLETE_SCRIPT_IDENTITIES")
    if any(not SHA256_RE.fullmatch(str(value)) for value in script_identities.values()):
        raise PreservationError("INVALID_SCRIPT_IDENTITY")
    repository = Path(__file__).resolve().parents[1]
    for name, expected_digest in script_identities.items():
        script_path = repository / "scripts" / name
        if not script_path.is_file() or script_path.is_symlink():
            raise PreservationError("SCRIPT_IDENTITY_SOURCE_MISSING")
        if sha256_file(script_path) != expected_digest:
            raise PreservationError("SCRIPT_IDENTITY_MISMATCH")
    try:
        safety_gate = json.loads(safety_gate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PreservationError("INVALID_SAFETY_GATE_JSON") from exc
    if (
        not isinstance(safety_gate, dict)
        or safety_gate.get("status") != "PASS"
        or safety_gate.get("aggregate_safety_gate_passed") is not True
    ):
        raise PreservationError("AGGREGATE_SAFETY_GATE_NOT_PASSED")
    safety_smoke_sha256 = safety_gate.get("technical_smoke_source_manifest_sha256")
    if not isinstance(safety_smoke_sha256, str) or not SHA256_RE.fullmatch(
        safety_smoke_sha256
    ):
        raise PreservationError("SAFETY_GATE_SMOKE_MANIFEST_IDENTITY_MISSING")
    checkpoint_identity = _artifact_identity(checkpoint)
    config_identity = _artifact_identity(config)
    expected = args.expected_checkpoint_sha256.lower()
    if expected != CHECKPOINT_SHA256:
        raise PreservationError("INVALID_EXPECTED_CHECKPOINT_SHA256")
    if (
        checkpoint_identity["name"] != CHECKPOINT_FILENAME
        or checkpoint_identity["size_bytes"] != CHECKPOINT_BYTES
        or checkpoint_identity["sha256"] != expected
    ):
        raise PreservationError("CHECKPOINT_IDENTITY_MISMATCH")
    if not str(args.job_id).strip() or not str(args.scheduler).strip():
        raise PreservationError("MISSING_JOB_METADATA")
    if str(args.scheduler) != "SGE" or not str(args.job_id).isdigit():
        raise PreservationError("INVALID_JOB_METADATA")
    if not TIMESTAMP_RE.fullmatch(str(args.run_timestamp)):
        raise PreservationError("MISSING_RUN_TIMESTAMP")
    if environment["source_commit"] != args.source_commit.lower():
        raise PreservationError("ENVIRONMENT_SOURCE_COMMIT_MISMATCH")
    if environment["repository_head"] != args.source_commit.lower():
        raise PreservationError("ENVIRONMENT_REPOSITORY_HEAD_MISMATCH")
    if environment["repository_branch"] != "codex/lvef-multitask-revalidation":
        raise PreservationError("ENVIRONMENT_REPOSITORY_BRANCH_MISMATCH")
    if environment["repository_clean"] is not True:
        raise PreservationError("ENVIRONMENT_REPOSITORY_NOT_CLEAN")
    if environment["scheduler"] != str(args.scheduler):
        raise PreservationError("ENVIRONMENT_SCHEDULER_MISMATCH")
    if str(environment["scheduler_job_id"]) != str(args.job_id):
        raise PreservationError("ENVIRONMENT_JOB_ID_MISMATCH")
    if environment["config_sha256"] != config_identity["sha256"]:
        raise PreservationError("ENVIRONMENT_CONFIG_MISMATCH")
    if environment["checkpoint_sha256"] != checkpoint_identity["sha256"]:
        raise PreservationError("ENVIRONMENT_CHECKPOINT_MISMATCH")
    if (
        environment["technical_smoke_source_manifest_sha256"]
        != safety_smoke_sha256
    ):
        raise PreservationError("ENVIRONMENT_SMOKE_MANIFEST_IDENTITY_MISMATCH")
    if environment["python_executable_sha256"] is None or not SHA256_RE.fullmatch(
        str(environment["python_executable_sha256"])
    ):
        raise PreservationError("INVALID_PYTHON_EXECUTABLE_IDENTITY")
    python_executable = Path(str(environment["python_executable"])).expanduser().resolve()
    if not python_executable.is_file():
        raise PreservationError("PYTHON_EXECUTABLE_NOT_REGULAR_FILE")
    if sha256_file(python_executable) != environment["python_executable_sha256"]:
        raise PreservationError("PYTHON_EXECUTABLE_IDENTITY_MISMATCH")

    artifact_revalidation, restricted_snapshot = revalidate_authoritative_run_artifacts(
        run_root, safety_gate
    )
    records = build_file_records(run_root)
    record_map = {
        record.relative_path: (record.size_bytes, record.sha256) for record in records
    }
    current_restricted = {
        relative: identity
        for relative, identity in record_map.items()
        if relative.startswith("restricted/")
    }
    if current_restricted != restricted_snapshot:
        raise PreservationError("RESTRICTED_ARTIFACT_CHANGED_DURING_PRESERVATION")
    declared_aggregate_hashes = safety_gate["artifact_sha256"]
    for name, relative in AGGREGATE_ARTIFACT_RELATIVE_PATHS.items():
        identity = record_map.get(relative)
        if identity is None or identity[1] != declared_aggregate_hashes[name]:
            raise PreservationError("AGGREGATE_ARTIFACT_CHANGED_DURING_PRESERVATION")
    manifest_path = output_dir / MANIFEST_NAME
    metadata_path = output_dir / METADATA_NAME
    verification_path = output_dir / VERIFICATION_NAME
    write_manifest_exclusive(manifest_path, records)

    metadata = {
        "schema_version": 1,
        "source_commit": args.source_commit.lower(),
        "run_timestamp": str(args.run_timestamp),
        "scheduler": str(args.scheduler),
        "job_id": str(args.job_id),
        "config": config_identity,
        "checkpoint": checkpoint_identity,
        "environment_artifact": _artifact_identity(environment_path),
        "environment": environment,
        "command_artifact": _artifact_identity(command_path),
        "command_checksum": sha256_file(command_path),
        "aggregate_safety_gate_artifact": _artifact_identity(safety_gate_path),
        "aggregate_safety_gate": safety_gate,
        "authoritative_artifact_revalidation": artifact_revalidation,
        "run_scope": {
            "root_name": run_root.name,
            "n_files": len(records),
            "total_bytes": sum(item.size_bytes for item in records),
        },
    }
    _write_json_exclusive(metadata_path, metadata)

    verification = verify_manifest_independently(run_root, manifest_path)
    verification.update(
        {
            "metadata_sha256": sha256_file(metadata_path),
            "source_commit_matches_metadata": metadata["source_commit"]
            == args.source_commit.lower(),
            "checkpoint_sha256_matches_metadata": metadata["checkpoint"]["sha256"]
            == checkpoint_identity["sha256"],
        }
    )
    if not all(
        (
            verification["source_commit_matches_metadata"],
            verification["checkpoint_sha256_matches_metadata"],
        )
    ):
        raise PreservationError("METADATA_SECOND_PASS_MISMATCH")
    _write_json_exclusive(verification_path, verification)

    aggregate = aggregate_template()
    aggregate.update(
        {
            "status": "PASS",
            "n_files": verification["n_files"],
            "total_bytes": verification["total_bytes"],
            "manifest_sha256": verification["manifest_sha256"],
            "metadata_sha256": sha256_file(metadata_path),
            "verification_sha256": sha256_file(verification_path),
            "exact_file_set": True,
            "all_sizes_match": True,
            "all_sha256_match": True,
            "no_symlinks": True,
            "source_commit_recorded": True,
            "config_identity_recorded": True,
            "checkpoint_identity_recorded": True,
            "environment_recorded": True,
            "job_metadata_recorded": True,
            "command_checksum_recorded": True,
            "aggregate_safety_gate_recorded": True,
            "aggregate_artifact_hashes_reconciled": True,
            "download_audit_recomputed": True,
            "dicom_audit_recomputed": True,
            "reproducibility_recomputed": True,
            "restricted_snapshot_reconciled": True,
            "error_code": "NONE",
        }
    )
    _write_json_exclusive(aggregate_resolved, aggregate)
    return aggregate


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and independently verify a restricted reconstruction-smoke preservation pack."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha256", default=CHECKPOINT_SHA256)
    parser.add_argument("--environment-json", type=Path, required=True)
    parser.add_argument("--aggregate-safety-gate-json", type=Path, required=True)
    parser.add_argument("--command-file", type=Path, required=True)
    parser.add_argument("--scheduler", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-timestamp", required=True)
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
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_exclusive(path, payload)
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    aggregate = aggregate_template()
    try:
        aggregate = create_preservation_pack(args)
    except PreservationError as exc:
        aggregate["error_code"] = exc.code
        _safe_failure_write(
            args.aggregate_output, aggregate, forbidden_root=args.run_root
        )
        print(json.dumps(aggregate, sort_keys=True))
        return 2
    except Exception:
        aggregate["error_code"] = "UNEXPECTED_INTERNAL_ERROR"
        _safe_failure_write(
            args.aggregate_output, aggregate, forbidden_root=args.run_root
        )
        print(json.dumps(aggregate, sort_keys=True))
        return 2
    print(json.dumps(aggregate, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
