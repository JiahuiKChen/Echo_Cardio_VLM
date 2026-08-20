#!/usr/bin/env python3
"""Retire exactly the superseded Batch-1/2 raw DICOM leaf directories.

The authority path is metadata-only: immutable plans, verified-download
manifests, resume ledgers, per-object verification receipts, and current
``lstat`` identities are compared between the older failed attempt and the
fully retained R4 attempt.  DICOM and NPZ bodies are never opened or hashed.

This is deliberately not a general garbage collector.  Production paths,
attempt identities, batch identities, counts, and basenames are fixed below.
The sole destructive CLI mode accepts no caller-supplied target.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import datetime
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_orchestration_core as core
import lvef_multitask_analysis_modes as analysis_modes
import capture_lvef_c3_post_reallocation_capacity as capacity
import replay_lvef_c3_batch3_premask_one_object as r4d2
import replay_lvef_c3_failed_extraction_one_object as r3e


PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
OWNER_PRIVATE_ROOT = PRODUCTION_ROOT / "owner_private"
EVIDENCE_ROOT = OWNER_PRIVATE_ROOT / "r5e_older_raw_retirement"
OLDER_ATTEMPT_ID = "lvef_c3_full_d574f21c760a5679_b805fd1a"
R4_ATTEMPT_ID = "lvef_c3_full_38750555923b547c_c11e1313"
OLDER_EXECUTION_COMMIT = "b805fd1a403b3ff0503d09bb79d35b01805dd765"
R4_EXECUTION_COMMIT = "c11e1313999880881eb67f0269820361638fc8dc"
TARGET_BATCHES = ("c3_batch_000", "c3_batch_001")
OLDER_ARRAY_JOB_ID = "7183952"
OLDER_FINALIZER_JOB_ID = "7183953"
QUIESCENCE_STABILITY_INTERVAL_SECONDS = 0.10
RAW_OBJECT_BASENAME_RE = re.compile(r"^[0-9a-f]{64}\.dcm$")
PLANNED_DOWNLOAD_PARTIAL_RE = re.compile(
    rf"^(?P<source_key>[0-9a-f]{{64}})\.{re.escape(OLDER_ATTEMPT_ID)}\.partial$"
)
R5E_R2_PRE_ACTION_GOVERNING_COMMIT = (
    "f201e22cce760402814be76e25899c0ba10bdfbd"
)
EXPECTED_BATCH_COUNTS = {"c3_batch_000": 18_872, "c3_batch_001": 18_196}
EXPECTED_BATCH_BYTES = {
    "c3_batch_000": 68_847_811_224,
    "c3_batch_001": 64_974_548_122,
}
EXPECTED_PRE_FILES = 158_288
EXPECTED_PRE_BYTES = 151_954_116_217
EXPECTED_DELETE_FILES = 37_068
EXPECTED_DELETE_BYTES = 133_822_359_346
EXPECTED_RETAINED_FILES = 121_220
EXPECTED_RETAINED_BYTES = 18_131_756_871
EXPECTED_EXTRACTED_NPZ_FILES = 9_936
EXPECTED_EXTRACTED_NPZ_BYTES = 17_846_025_657
EXPECTED_R4_FILES = 233_257
EXPECTED_R4_DIRECTORIES = 300
EXPECTED_R4_BYTES = 221_586_476_241
EXPECTED_R4_METADATA_SHA256 = (
    "c820806c26ba9644061e7d1c92e79de48d69b7b91fa3d0d09763d028284fc4c6"
)
EXPECTED_SELECTED_SOURCE_SHA256 = core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256
MANIFEST_BASENAME = "older_raw_retirement_manifest.restricted.json"
RECEIPT_BASENAME = "older_raw_retirement_receipt.restricted.json"
SUMMARY_BASENAME = "older_raw_retirement_summary.aggregate_safe.json"
MANIFEST_PATH = EVIDENCE_ROOT / MANIFEST_BASENAME
RECEIPT_PATH = EVIDENCE_ROOT / RECEIPT_BASENAME
SUMMARY_PATH = EVIDENCE_ROOT / SUMMARY_BASENAME
RECEIPT_KEYS = frozenset({
    "schema_version", "artifact_type", "status", "governing_commit",
    "manifest_basename", "manifest_bytes", "manifest_sha256",
    "planned_delete_files", "planned_delete_bytes", "actual_deleted_files",
    "actual_deleted_bytes", "residual_target_files", "residual_target_bytes",
    "retained_files", "retained_bytes", "retained_file_metadata_sha256",
    "retained_directory_topology_sha256", "retained_role_inventory_sha256",
    "retained_control_content_sha256", "diagnostic_evidence_authority_sha256",
    "r4_metadata_stat_sha256",
    "older_npz_cache_retained", "canonical_source_deleted",
    "r4_attempt_immutable", "dicom_body_reads", "npz_body_reads",
    "cloud_requests", "qsub_submissions", "gpu_executions",
    "embedding_generations", "model_fitting", "prediction_generation",
    "confirmatory_performance_accesses", "identifiers_emitted",
    "paths_emitted",
})
SUMMARY_KEYS = frozenset({
    "schema_version", "artifact_type", "status", "governing_commit",
    "manifest_basename", "manifest_bytes", "manifest_sha256",
    "planned_delete_files", "planned_delete_bytes", "actual_deleted_files",
    "actual_deleted_bytes", "residual_target_files", "residual_target_bytes",
    "retained_files", "retained_bytes", "r4_metadata_stat_sha256",
    "older_npz_cache_retained", "canonical_source_deleted",
    "r4_attempt_immutable", "dicom_body_reads", "npz_body_reads",
    "cloud_requests", "qsub_submissions", "gpu_executions",
    "embedding_generations", "model_fitting", "prediction_generation",
    "confirmatory_performance_accesses", "identifiers_emitted",
    "paths_emitted", "receipt_basename", "receipt_bytes", "receipt_sha256",
})
TARGET_KEYS = frozenset({
    "batch_id", "relative_path", "source_object_key", "subject_id",
    "study_id", "split", "source_relative_path", "size_bytes",
    "generation", "md5_base64", "crc32c_base64", "local_sha256",
    "verification_receipt_sha256", "file_inode", "file_mtime_ns",
    "r4_relative_path", "r4_verification_receipt_sha256",
    "r4_file_inode", "r4_file_mtime_ns",
})
TARGET_PROJECTION_KEYS = (
    "batch_id", "relative_path", "source_object_key", "subject_id",
    "study_id", "split", "source_relative_path", "size_bytes",
    "generation", "md5_base64", "crc32c_base64", "local_sha256",
    "verification_receipt_sha256", "r4_relative_path",
    "r4_verification_receipt_sha256",
)
BATCH_AUTHORITY_KEYS = frozenset({
    "batch_id", "n_objects", "source_bytes", "study_membership_sha256",
    "source_membership_sha256", "older_plan_sha256", "r4_plan_sha256",
    "older_download_manifest_sha256", "r4_download_manifest_sha256",
    "older_download_ledger_sha256", "r4_download_ledger_sha256",
    "older_download_manifest_role", "r4_download_manifest_role",
    "download_manifest_canonical_projection",
})
RECEIPT_TREE_AUTHORITY_KEYS = frozenset({
    "required_receipt_files",
    "auxiliary_receipt_files",
    "auxiliary_receipt_bytes",
    "auxiliary_metadata_projection_sha256",
    "auxiliary_files_retained",
})
RETAINED_ROLE_NAMES = (
    "RAW_DICOM_PAYLOAD",
    "PRESERVED_DOWNLOAD_PARTIALS",
    "EXTRACTED_NPZ_CACHE",
    "CLIP_EMBEDDINGS",
    "STUDY_EMBEDDINGS",
    "DOWNLOAD_VERIFICATION_RECEIPTS",
    "SOURCE_AND_BATCH_MANIFESTS",
    "DICOM_AUDIT_AND_EXTRACTION_MANIFESTS",
    "POOLING_PRESERVATION_RETIREMENT_FINALIZATION_RECEIPTS",
    "CLAIM_PLAN_SUBMISSION_AND_ENVIRONMENT_AUTHORITIES",
    "SCHEDULER_LOGS",
    "R3E_DIAGNOSTIC_EVIDENCE",
    "OTHER_CONTROL_EVIDENCE",
    "UNCLASSIFIED",
)
RETAINED_ROLE_ROW_KEYS = frozenset({"role", "file_count", "total_bytes"})
DIAGNOSTIC_AUTHORITY_KEYS = frozenset({
    "status",
    "diagnostic_root_count",
    "diagnostic_file_count",
    "diagnostic_directory_count",
    "diagnostic_total_bytes",
    "diagnostic_file_metadata_projection_sha256",
    "diagnostic_directory_topology_sha256",
    "diagnostics_outside_deletion_targets",
    "diagnostics_retained",
    "body_reads",
})
MAXIMUM_CONTROL_BYTES = 512 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
QSTAT = Path("/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qstat")
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
SAFE_EXPORT_POLICY_PATH = (
    REPOSITORY_ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
)
SAFE_EXPORT_PROFILE = "lvef_c3_older_raw_retirement_summary_json"


class OlderRawRetirementError(RuntimeError):
    """Closed aggregate-safe failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class OlderRawDownloadManifestError(OlderRawRetirementError):
    """Closed manifest failure that carries only an aggregate-safe role."""

    def __init__(self, code: str, role: str):
        super().__init__(code)
        self.role = role


def _fail(code: str) -> None:
    raise OlderRawRetirementError(code)


def _manifest_fail(role: str, code: str) -> None:
    raise OlderRawDownloadManifestError(code, role)


def _mountinfo_mountpoints() -> frozenset[Path]:
    """Read kernel mount topology and decode its fixed octal path escapes."""

    if sys.platform == "darwin":
        return frozenset()
    if not sys.platform.startswith("linux"):
        _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(MOUNTINFO_PATH, flags)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 16 * 1024 * 1024:
                    _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
                chunks.append(block)
        finally:
            os.close(fd)
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except OlderRawRetirementError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID"
        ) from exc

    escapes = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
    mountpoints: set[Path] = set()
    try:
        for line in text.splitlines():
            left, separator, _right = line.partition(" - ")
            fields = left.split(" ")
            if not separator or len(fields) < 6:
                _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
            encoded = fields[4]

            def replace_escape(match: re.Match[str]) -> str:
                value = escapes.get(match.group(1))
                if value is None:
                    _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
                return value

            decoded = re.sub(r"\\([0-9]{3})", replace_escape, encoded)
            if not decoded.startswith("/"):
                _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
            mountpoints.add(Path(os.path.abspath(decoded)))
    except OlderRawRetirementError:
        raise
    except (IndexError, ValueError) as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID"
        ) from exc
    return frozenset(mountpoints)


def _pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("OLDER_RAW_CONTROL_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _constant(_value: str) -> Any:
    _fail("OLDER_RAW_CONTROL_JSON_INVALID")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_private(path: Path, *, maximum: int = MAXIMUM_CONTROL_BYTES) -> bytes:
    try:
        payload = r3e._read_owner_private_file(path, maximum_bytes=maximum)
        info = os.lstat(path)
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_CONTROL_READ_INVALID") from exc
    if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
        _fail("OLDER_RAW_CONTROL_READ_INVALID")
    return payload


def _read_json(path: Path, *, maximum: int = MAXIMUM_CONTROL_BYTES) -> tuple[dict[str, Any], bytes]:
    payload = _read_private(path, maximum=maximum)
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except OlderRawRetirementError:
        raise
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_CONTROL_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("OLDER_RAW_CONTROL_JSON_INVALID")
    return value, payload


def _write_new(path: Path, payload: bytes) -> None:
    parent = path.parent
    if not parent.is_absolute():
        _fail("OLDER_RAW_EVIDENCE_PATH_INVALID")
    if not os.path.lexists(parent):
        parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    info = os.lstat(parent)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) not in {0o700, 0o2700}
    ):
        _fail("OLDER_RAW_EVIDENCE_PATH_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
        parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except FileExistsError as exc:
        raise OlderRawRetirementError("OLDER_RAW_EVIDENCE_COLLISION") from exc
    except OSError as exc:
        raise OlderRawRetirementError("OLDER_RAW_EVIDENCE_WRITE_FAILED") from exc
    if _read_private(path) != payload:
        _fail("OLDER_RAW_EVIDENCE_REOPEN_MISMATCH")


def _current_commit() -> str:
    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", *arguments], cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, check=False,
            env={
                "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
            },
        )
        if result.returncode:
            _fail("OLDER_RAW_GIT_AUTHORITY_INVALID")
        return result.stdout.strip()

    head = git("rev-parse", "HEAD")
    origin = git(
        "rev-parse", "refs/remotes/origin/codex/lvef-multitask-revalidation"
    )
    if (
        git("branch", "--show-current") != EXPECTED_BRANCH
        or head != origin
        or COMMIT_RE.fullmatch(head) is None
        or git("status", "--porcelain", "--untracked-files=no")
    ):
        _fail("OLDER_RAW_GIT_AUTHORITY_INVALID")
    return head


def _metadata_rows(
    root: Path, *, excluded_roots: Sequence[Path] = ()
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    root = Path(os.path.abspath(root))
    excluded = tuple(Path(os.path.abspath(path)) for path in excluded_roots)
    try:
        root_info = os.lstat(root)
    except OSError as exc:
        raise OlderRawRetirementError("OLDER_RAW_INVENTORY_INVALID") from exc
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        _fail("OLDER_RAW_INVENTORY_INVALID")
    file_rows: list[tuple[Any, ...]] = []
    directory_rows: list[tuple[Any, ...]] = []
    seen_inodes: set[tuple[int, int]] = set()

    def excluded_path(path: Path) -> bool:
        return any(path == item or item in path.parents for item in excluded)

    for current, names, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names[:] = sorted(
            name
            for name in names
            if not excluded_path(current_path / name)
        )
        for name in names:
            path = current_path / name
            info = os.lstat(path)
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
            ):
                _fail("OLDER_RAW_INVENTORY_INVALID")
            directory_rows.append(
                (
                    path.relative_to(root).as_posix(),
                    stat.S_IMODE(info.st_mode),
                    info.st_uid,
                    info.st_gid,
                    info.st_ino,
                )
            )
        for name in sorted(files):
            path = current_path / name
            if excluded_path(path):
                continue
            info = os.lstat(path)
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or identity in seen_inodes
            ):
                _fail("OLDER_RAW_INVENTORY_INVALID")
            seen_inodes.add(identity)
            file_rows.append(
                (
                    path.relative_to(root).as_posix(),
                    stat.S_IMODE(info.st_mode),
                    info.st_uid,
                    info.st_gid,
                    info.st_nlink,
                    info.st_size,
                    info.st_ino,
                    info.st_mtime_ns,
                )
            )
    return sorted(file_rows), sorted(directory_rows)


def _stable_metadata_authority(
    root: Path, *, excluded_roots: Sequence[Path] = ()
) -> dict[str, Any]:
    first = _metadata_rows(root, excluded_roots=excluded_roots)
    second = _metadata_rows(root, excluded_roots=excluded_roots)
    if first != second:
        _fail("OLDER_RAW_INVENTORY_UNSTABLE")
    files, directories = first
    return {
        "file_count": len(files),
        "directory_count": len(directories) + 1,
        "total_bytes": sum(int(row[5]) for row in files),
        "file_metadata_sha256": core.canonical_json_sha256(files),
        "directory_topology_sha256": core.canonical_json_sha256(directories),
    }


def _is_direct_target_receipt(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    parts = path.parts
    return (
        len(parts) == 4
        and parts[0] == "raw"
        and parts[1] in TARGET_BATCHES
        and parts[2] == "receipts"
    )


def _required_receipt_relative_paths(
    targets: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    return frozenset(
        "raw/{batch}/receipts/{source}.verification.json".format(
            batch=item["batch_id"], source=item["source_object_key"]
        )
        for item in targets
    )


def _is_opaque_auxiliary_receipt(
    relative_path: str,
    *,
    required_receipt_relative_paths: frozenset[str],
) -> bool:
    return (
        _is_direct_target_receipt(relative_path)
        and relative_path not in required_receipt_relative_paths
    )


def _retained_role(
    relative_path: str,
    *,
    required_receipt_relative_paths: frozenset[str] = frozenset(),
) -> str:
    """Classify one retained file by closed topology/name rules, never size."""

    path = PurePosixPath(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    if _is_direct_target_receipt(relative_path):
        if relative_path in required_receipt_relative_paths:
            return "DOWNLOAD_VERIFICATION_RECEIPTS"
        return "OTHER_CONTROL_EVIDENCE"
    if name.endswith(".dcm"):
        return "RAW_DICOM_PAYLOAD"
    if (
        len(parts) >= 4
        and parts[0] == "raw"
        and parts[1] in TARGET_BATCHES
        and parts[2] == "partials"
        and PLANNED_DOWNLOAD_PARTIAL_RE.fullmatch(name) is not None
    ):
        return "PRESERVED_DOWNLOAD_PARTIALS"
    if name.endswith(".npz"):
        if "clip_embeddings" in name:
            return "CLIP_EMBEDDINGS"
        if "study_embeddings" in name:
            return "STUDY_EMBEDDINGS"
        if "extracted_cache" in parts or "clips" in parts:
            return "EXTRACTED_NPZ_CACHE"
        return "UNCLASSIFIED"
    if any(
        token in name
        for token in (
            "dicom_audit", "extraction_manifest",
        )
    ):
        return "DICOM_AUDIT_AND_EXTRACTION_MANIFESTS"
    if any(
        token in name
        for token in (
            "verified_download_manifest", "source_manifest", "study_manifest",
            "clip_manifest", "disposition_manifest", "batch_manifest",
        )
    ):
        return "SOURCE_AND_BATCH_MANIFESTS"
    if any(
        token in name
        for token in (
            "resume_ledger", "transition", "pooling", "preservation",
            "retirement", "final_receipt", "finalization",
        )
    ):
        return "POOLING_PRESERVATION_RETIREMENT_FINALIZATION_RECEIPTS"
    if any(
        token in name
        for token in (
            "claim", "batch_plan", "launch_authority", "submission",
            "environment", "runtime_authority", "execution_authority",
        )
    ):
        return "CLAIM_PLAN_SUBMISSION_AND_ENVIRONMENT_AUTHORITIES"
    if (
        name.endswith((".log", ".stdout", ".stderr", ".out", ".err"))
        or re.search(r"\.[oe]\d+(?:\.\d+)?$", name) is not None
        or any(token in name for token in ("scheduler", "qsub", "job_exit"))
    ):
        return "SCHEDULER_LOGS"
    if name.endswith((".json", ".csv", ".tsv", ".txt", ".yaml", ".yml")):
        return "OTHER_CONTROL_EVIDENCE"
    return "UNCLASSIFIED"


def _read_retained_control(path: Path) -> str:
    """Hash a control artifact stably; DICOM and NPZ roles never reach here."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
            ):
                _fail("OLDER_RAW_RETAINED_CONTROL_INVALID")
            digest = hashlib.sha256()
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
            after = os.fstat(fd)
            visible = os.stat(path, follow_symlinks=False)
        finally:
            os.close(fd)
    except OlderRawRetirementError:
        raise
    except OSError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_RETAINED_CONTROL_INVALID"
        ) from exc
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )
    if identity(before) != identity(after) or identity(after) != identity(visible):
        _fail("OLDER_RAW_RETAINED_CONTROL_INVALID")
    return digest.hexdigest()


def _retained_evidence_authority(
    root: Path,
    *,
    required_receipt_relative_paths: frozenset[str],
    excluded_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Seal every retained role plus exact control content and metadata."""

    if (
        len(required_receipt_relative_paths) != EXPECTED_DELETE_FILES
        or any(
            not _is_direct_target_receipt(relative)
            for relative in required_receipt_relative_paths
        )
    ):
        _fail("OLDER_RAW_RETAINED_ROLE_AUTHORITY_INVALID")
    first = _metadata_rows(root, excluded_roots=excluded_roots)
    files, directories = first
    counts = {
        role: {"role": role, "file_count": 0, "total_bytes": 0}
        for role in RETAINED_ROLE_NAMES
    }
    control_rows: list[tuple[str, str, int, str]] = []
    body_roles = {
        "RAW_DICOM_PAYLOAD", "PRESERVED_DOWNLOAD_PARTIALS",
        "EXTRACTED_NPZ_CACHE",
        "CLIP_EMBEDDINGS", "STUDY_EMBEDDINGS",
    }
    for row in files:
        relative = str(row[0])
        role = _retained_role(
            relative,
            required_receipt_relative_paths=required_receipt_relative_paths,
        )
        counts[role]["file_count"] += 1
        counts[role]["total_bytes"] += int(row[5])
        opaque_auxiliary = _is_opaque_auxiliary_receipt(
            relative,
            required_receipt_relative_paths=required_receipt_relative_paths,
        )
        if role not in body_roles and not opaque_auxiliary:
            control_rows.append(
                (
                    relative,
                    role,
                    int(row[5]),
                    _read_retained_control(root / relative),
                )
            )
    second = _metadata_rows(root, excluded_roots=excluded_roots)
    if first != second:
        _fail("OLDER_RAW_INVENTORY_UNSTABLE")
    inventory = [counts[role] for role in RETAINED_ROLE_NAMES]
    by_role = {row["role"]: row for row in inventory}
    required_positive = (
        "CLIP_EMBEDDINGS", "STUDY_EMBEDDINGS",
        "SOURCE_AND_BATCH_MANIFESTS",
        "DICOM_AUDIT_AND_EXTRACTION_MANIFESTS",
        "POOLING_PRESERVATION_RETIREMENT_FINALIZATION_RECEIPTS",
        "CLAIM_PLAN_SUBMISSION_AND_ENVIRONMENT_AUTHORITIES",
        "SCHEDULER_LOGS", "OTHER_CONTROL_EVIDENCE",
    )
    if (
        sum(row["file_count"] for row in inventory) != len(files)
        or sum(row["total_bytes"] for row in inventory)
        != sum(int(row[5]) for row in files)
        or by_role["RAW_DICOM_PAYLOAD"] != {
            "role": "RAW_DICOM_PAYLOAD", "file_count": 0, "total_bytes": 0
        }
        or by_role["R3E_DIAGNOSTIC_EVIDENCE"] != {
            "role": "R3E_DIAGNOSTIC_EVIDENCE", "file_count": 0,
            "total_bytes": 0,
        }
        or by_role["UNCLASSIFIED"] != {
            "role": "UNCLASSIFIED", "file_count": 0, "total_bytes": 0
        }
        or by_role["EXTRACTED_NPZ_CACHE"]["file_count"]
        != EXPECTED_EXTRACTED_NPZ_FILES
        or by_role["EXTRACTED_NPZ_CACHE"]["total_bytes"]
        != EXPECTED_EXTRACTED_NPZ_BYTES
        or by_role["DOWNLOAD_VERIFICATION_RECEIPTS"]["file_count"]
        != EXPECTED_DELETE_FILES
        or any(by_role[role]["file_count"] < 1 for role in required_positive)
    ):
        _fail("OLDER_RAW_RETAINED_ROLE_AUTHORITY_INVALID")
    return {
        "file_count": len(files),
        "directory_count": len(directories) + 1,
        "total_bytes": sum(int(row[5]) for row in files),
        "file_metadata_sha256": core.canonical_json_sha256(files),
        "directory_topology_sha256": core.canonical_json_sha256(directories),
        "role_inventory": inventory,
        "role_inventory_sha256": core.canonical_json_sha256(inventory),
        "control_content_sha256": core.canonical_json_sha256(control_rows),
    }


def _validate_r4() -> Mapping[str, Any]:
    root = PRODUCTION_ROOT / "attempts" / R4_ATTEMPT_ID
    try:
        observed = r4d2.r4_metadata_inventory(root)
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_R4_AUTHORITY_INVALID") from exc
    if (
        observed.file_count != EXPECTED_R4_FILES
        or observed.directory_count != EXPECTED_R4_DIRECTORIES
        or observed.total_bytes != EXPECTED_R4_BYTES
        or observed.metadata_stat_sha256 != EXPECTED_R4_METADATA_SHA256
        or observed.symlink_count != 0
        or observed.nonregular_count != 0
        or observed.owner_mismatch_count != 0
        or observed.regular_nlink_anomaly_count != 0
        or observed.duplicate_inode_count != 0
    ):
        _fail("OLDER_RAW_R4_AUTHORITY_INVALID")
    return {
        "file_count": observed.file_count,
        "directory_count": observed.directory_count,
        "total_bytes": observed.total_bytes,
        "metadata_stat_sha256": observed.metadata_stat_sha256,
    }


@dataclass(frozen=True)
class PlanBundle:
    plan: Mapping[str, Any]
    plan_sha256: str
    requirements: core.PlanRequirements
    launch: Mapping[str, Any]
    authority: r3e.OriginalAttemptAuthority


@dataclass(frozen=True)
class VerifiedDownloadManifestRole:
    """Exact producer-era schema for one immutable manifest artifact."""

    role: str
    attempt_id: str
    batch_id: str
    producer_schema: str
    ordered_header: tuple[str, ...]
    allowed_download_ok_token: str
    source_relative_path_convention: str
    expected_row_count: int
    manifest_bytes: int
    manifest_sha256: str


VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1 = (
    "subject_id",
    "study_id",
    "source_relative_path",
    "download_ok",
    "observed_sha256",
    "physical_source_key",
)
VERIFIED_DOWNLOAD_CANONICAL_PROJECTION_V1 = (
    "PLAN_SUBJECT_STUDY_PHYSICAL_KEY_PLANNED_SOURCE_LOCATOR_"
    "SUCCESS_OBSERVED_SHA256_V1"
)
VERIFIED_DOWNLOAD_MANIFEST_ROLES = {
    "OLDER_BATCH_1": VerifiedDownloadManifestRole(
        role="OLDER_BATCH_1",
        attempt_id=OLDER_ATTEMPT_ID,
        batch_id="c3_batch_000",
        producer_schema="B805_CORE_VERIFIED_DOWNLOAD_MANIFEST_V1",
        ordered_header=VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1,
        allowed_download_ok_token="true",
        source_relative_path_convention="PLANNED_SOURCE_AUTHORITY_LOCATOR_V1",
        expected_row_count=18_872,
        manifest_bytes=3_793_361,
        manifest_sha256=(
            "80aa064d5da8c28b9193410497a721923f47605a0a86124e6c89988bea1bfb2e"
        ),
    ),
    "OLDER_BATCH_2": VerifiedDownloadManifestRole(
        role="OLDER_BATCH_2",
        attempt_id=OLDER_ATTEMPT_ID,
        batch_id="c3_batch_001",
        producer_schema="B805_CORE_VERIFIED_DOWNLOAD_MANIFEST_V1",
        ordered_header=VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1,
        allowed_download_ok_token="true",
        source_relative_path_convention="PLANNED_SOURCE_AUTHORITY_LOCATOR_V1",
        expected_row_count=18_196,
        manifest_bytes=3_657_485,
        manifest_sha256=(
            "01162bbe350af5c53e8c8e644127eea06bdcd47c6f874b3f765d006129218ffb"
        ),
    ),
    "R4_BATCH_1": VerifiedDownloadManifestRole(
        role="R4_BATCH_1",
        attempt_id=R4_ATTEMPT_ID,
        batch_id="c3_batch_000",
        producer_schema="C11E_CORE_VERIFIED_DOWNLOAD_MANIFEST_V1",
        ordered_header=VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1,
        allowed_download_ok_token="true",
        source_relative_path_convention="PLANNED_SOURCE_AUTHORITY_LOCATOR_V1",
        expected_row_count=18_872,
        manifest_bytes=3_793_361,
        manifest_sha256=(
            "80aa064d5da8c28b9193410497a721923f47605a0a86124e6c89988bea1bfb2e"
        ),
    ),
    "R4_BATCH_2": VerifiedDownloadManifestRole(
        role="R4_BATCH_2",
        attempt_id=R4_ATTEMPT_ID,
        batch_id="c3_batch_001",
        producer_schema="C11E_CORE_VERIFIED_DOWNLOAD_MANIFEST_V1",
        ordered_header=VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1,
        allowed_download_ok_token="true",
        source_relative_path_convention="PLANNED_SOURCE_AUTHORITY_LOCATOR_V1",
        expected_row_count=18_196,
        manifest_bytes=3_657_485,
        manifest_sha256=(
            "01162bbe350af5c53e8c8e644127eea06bdcd47c6f874b3f765d006129218ffb"
        ),
    ),
}


def _verified_download_manifest_role(
    *, attempt_id: str, batch_id: str
) -> VerifiedDownloadManifestRole:
    observed = [
        item
        for item in VERIFIED_DOWNLOAD_MANIFEST_ROLES.values()
        if item.attempt_id == attempt_id and item.batch_id == batch_id
    ]
    if len(observed) != 1:
        _fail("OLDER_RAW_DOWNLOAD_MANIFEST_ROLE_SCHEMA_INVALID")
    return observed[0]


def _validate_verified_download_manifest_payload(
    payload: bytes,
    *,
    schema: VerifiedDownloadManifestRole,
    expected_attempt_id: str,
    expected_batch_id: str,
    expected: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, str]], str]:
    """Validate and project one exact fixed manifest without body access."""

    role = schema.role
    registered = VERIFIED_DOWNLOAD_MANIFEST_ROLES.get(role)
    if (
        registered is None
        or schema != registered
        or schema.attempt_id != expected_attempt_id
        or schema.batch_id != expected_batch_id
        or schema.ordered_header != VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1
        or schema.allowed_download_ok_token != "true"
        or schema.source_relative_path_convention
        != "PLANNED_SOURCE_AUTHORITY_LOCATOR_V1"
        or schema.expected_row_count != len(expected)
        or schema.manifest_bytes != len(payload)
        or SHA_RE.fullmatch(schema.manifest_sha256) is None
        or _sha(payload) != schema.manifest_sha256
    ):
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_ROLE_SCHEMA_INVALID")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OlderRawDownloadManifestError(
            "OLDER_RAW_DOWNLOAD_MANIFEST_ENCODING_INVALID", role
        ) from exc
    if text.startswith("\ufeff"):
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_ENCODING_INVALID")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise OlderRawDownloadManifestError(
            "OLDER_RAW_DOWNLOAD_MANIFEST_ROW_WIDTH_INVALID", role
        ) from exc
    if not rows:
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_HEADER_INVALID")
    header = tuple(rows[0])
    if len(header) != len(set(header)) or header != schema.ordered_header:
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_HEADER_INVALID")
    body = rows[1:]
    if any(
        len(row) != len(header) or not row or all(value == "" for value in row)
        for row in body
    ):
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_ROW_WIDTH_INVALID")
    if len(body) != schema.expected_row_count:
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_ROW_COUNT_INVALID")
    projected = [dict(zip(header, row, strict=True)) for row in body]
    keys = [item["physical_source_key"] for item in projected]
    if len(keys) != len(set(keys)):
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_DUPLICATE_KEY")
    manifest: dict[str, Mapping[str, str]] = {}
    for item in projected:
        key = item["physical_source_key"]
        if item["download_ok"] != schema.allowed_download_ok_token:
            _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_STATUS_INVALID")
        plan_row = expected.get(key)
        if plan_row is None:
            _manifest_fail(
                role, "OLDER_RAW_DOWNLOAD_MANIFEST_PLAN_MEMBERSHIP_INVALID"
            )
        if (
            item["subject_id"] != str(plan_row["subject_id"])
            or item["study_id"] != str(plan_row["study_id"])
        ):
            _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_OWNERSHIP_INVALID")
        if item["source_relative_path"] != str(
            plan_row["source_relative_path"]
        ):
            _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_SOURCE_PATH_INVALID")
        if SHA_RE.fullmatch(item["observed_sha256"]) is None:
            _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_SHA_INVALID")
        manifest[key] = item
    if set(manifest) != set(expected):
        _manifest_fail(role, "OLDER_RAW_DOWNLOAD_MANIFEST_OBJECT_SET_INVALID")
    return manifest, _sha(payload)


def _load_plan_bundle(
    attempt_root: Path, *, authority: r3e.OriginalAttemptAuthority
) -> PlanBundle:
    plan, _ = _read_json(attempt_root / "full_batch_plan.restricted.json")
    claim, _ = _read_json(attempt_root / "full_submission_claim.restricted.json")
    launch, _ = _read_json(attempt_root / "full_launch_authority.restricted.json")
    try:
        contract = core.load_orchestration_contract(r3e.CONTRACT_PATH)
        requirements = core.production_requirements(contract)
        plan_sha = core.validate_batch_plan(plan, requirements=requirements)
        runtime = core.validate_runtime_authority(
            {**plan["authority"], "batch_plan_sha256": plan_sha}
        )
        r3e._validate_submission_claim(
            claim,
            plan=plan,
            plan_sha256=plan_sha,
            requirements=requirements,
            authority=authority,
            expected_runtime_authority=runtime,
            launch_authority_sha256=core.canonical_json_sha256(launch),
        )
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_PLAN_AUTHORITY_INVALID") from exc
    if (
        plan.get("authority", {}).get("git_commit") != authority.execution_commit
        or plan.get("authority", {}).get("selected_source_manifest_sha256")
        != EXPECTED_SELECTED_SOURCE_SHA256
        or not plan_sha.startswith(authority.batch_plan_sha256_prefix)
        or authority.attempt_id
        != f"lvef_c3_full_{authority.batch_plan_sha256_prefix}_{authority.execution_commit[:8]}"
    ):
        _fail("OLDER_RAW_PLAN_AUTHORITY_INVALID")
    return PlanBundle(plan, plan_sha, requirements, launch, authority)


def _batch(plan: Mapping[str, Any], batch_id: str) -> Mapping[str, Any]:
    rows = [item for item in plan["batches"] if item.get("batch_id") == batch_id]
    if len(rows) != 1:
        _fail("OLDER_RAW_BATCH_AUTHORITY_INVALID")
    return rows[0]


def _receipt_authority(
    receipt: Mapping[str, Any],
    *,
    row: Mapping[str, Any],
    info: os.stat_result,
    mount_authority: r3e.CurrentMountAuthority,
    authority: r3e.OriginalAttemptAuthority,
) -> str:
    expected = {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": row["source_object_key"],
        "size_bytes": row["size_bytes"],
        "generation": row["generation"],
        "md5_base64": row["md5_base64"],
        "crc32c_base64": row["crc32c_base64"],
        "file_inode": info.st_ino,
        "file_mtime_ns": info.st_mtime_ns,
        "digest_backend": "google_crc32c_c_external_worker_v1",
        "digest_chunk_size_bytes": 8_388_608,
    }
    if set(receipt) != r3e.DOWNLOAD_VERIFICATION_RECEIPT_KEYS:
        _fail("OLDER_RAW_DOWNLOAD_RECEIPT_INVALID")
    if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value for key, value in expected.items()):
        _fail("OLDER_RAW_DOWNLOAD_RECEIPT_INVALID")
    historical_device = receipt.get("file_device")
    if (
        mount_authority.status
        != "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT"
        or type(historical_device) is not int
        or historical_device < 0
        or ((historical_device != info.st_dev)
            is not authority.historical_device_must_differ)
    ):
        _fail("OLDER_RAW_DOWNLOAD_RECEIPT_INVALID")
    local_sha = receipt.get("local_sha256")
    if not isinstance(local_sha, str) or SHA_RE.fullmatch(local_sha) is None:
        _fail("OLDER_RAW_DOWNLOAD_RECEIPT_INVALID")
    return local_sha


def _raw_object_leaf_authority(
    attempt_root: Path,
    leaf: Path,
    *,
    batch_id: str,
    expected_sizes: Mapping[str, int] | None,
    expected_relative_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Close one raw-object leaf recursively without opening an object body."""

    if batch_id not in TARGET_BATCHES:
        _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
    attempt_root = Path(os.path.abspath(attempt_root))
    leaf = Path(os.path.abspath(leaf))
    try:
        relative_leaf = leaf.relative_to(attempt_root)
        if any(
            mountpoint == attempt_root or attempt_root in mountpoint.parents
            for mountpoint in _mountinfo_mountpoints()
        ):
            _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
        attempt_info = os.lstat(attempt_root)
        if (
            stat.S_ISLNK(attempt_info.st_mode)
            or not stat.S_ISDIR(attempt_info.st_mode)
            or attempt_info.st_uid != os.geteuid()
        ):
            _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
        current = attempt_root
        for component in relative_leaf.parts:
            current = current / component
            info = os.lstat(current)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_dev != attempt_info.st_dev
                or os.path.ismount(current)
            ):
                _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
        leaf_info = os.lstat(leaf)
        if stat.S_IMODE(leaf_info.st_mode) not in {0o700, 0o2700}:
            _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")

        entries: dict[str, dict[str, Any]] = {}
        metadata: list[dict[str, int | str]] = []
        directory_rows: list[tuple[str, int, int, int, int]] = []
        seen_inodes: set[tuple[int, int]] = set()
        for current_name, names, files in os.walk(
            leaf, topdown=True, followlinks=False
        ):
            current_path = Path(current_name)
            current_info = os.lstat(current_path)
            if (
                stat.S_ISLNK(current_info.st_mode)
                or not stat.S_ISDIR(current_info.st_mode)
                or current_info.st_uid != os.geteuid()
                or current_info.st_dev != leaf_info.st_dev
                or stat.S_IMODE(current_info.st_mode) not in {0o700, 0o2700}
                or (current_path != leaf and os.path.ismount(current_path))
            ):
                _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
            if current_path != leaf:
                directory_rows.append(
                    (
                        current_path.relative_to(leaf).as_posix(),
                        stat.S_IMODE(current_info.st_mode),
                        current_info.st_uid,
                        current_info.st_gid,
                        current_info.st_ino,
                    )
                )
            names[:] = sorted(names)
            for name in names:
                directory = current_path / name
                directory_info = os.lstat(directory)
                if name.startswith(".nfs") or ".partial" in name:
                    _fail("OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY")
                if stat.S_ISLNK(directory_info.st_mode):
                    _fail("OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY")
                if (
                    not stat.S_ISDIR(directory_info.st_mode)
                    or directory_info.st_uid != os.geteuid()
                    or directory_info.st_dev != leaf_info.st_dev
                    or stat.S_IMODE(directory_info.st_mode)
                    not in {0o700, 0o2700}
                    or os.path.ismount(directory)
                ):
                    _fail("OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID")
            for name in sorted(files):
                path = current_path / name
                info = os.lstat(path)
                identity = (info.st_dev, info.st_ino)
                if name.startswith(".nfs") or ".partial" in name:
                    _fail("OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY")
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_dev != leaf_info.st_dev
                    or identity in seen_inodes
                ):
                    _fail("OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY")
                match = RAW_OBJECT_BASENAME_RE.fullmatch(name)
                if match is None:
                    _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
                source_key = name[:-4]
                if source_key in entries:
                    _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
                seen_inodes.add(identity)
                relative_path = path.relative_to(attempt_root).as_posix()
                leaf_relative_path = path.relative_to(leaf).as_posix()
                entries[source_key] = {
                    "path": path,
                    "relative_path": relative_path,
                    "leaf_relative_path": leaf_relative_path,
                    "size_bytes": info.st_size,
                    "info": info,
                }
                metadata.append(
                    {
                        "relative_path": leaf_relative_path,
                        "device": info.st_dev,
                        "inode": info.st_ino,
                        "mode": stat.S_IMODE(info.st_mode),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                        "nlink": info.st_nlink,
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "ctime_ns": info.st_ctime_ns,
                    }
                )
    except OlderRawRetirementError:
        raise
    except (OSError, ValueError) as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID"
        ) from exc

    if expected_sizes is not None:
        expected_keys = set(expected_sizes)
        if set(entries) != expected_keys:
            _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
        if any(
            type(expected_sizes[key]) is not int
            or entries[key]["size_bytes"] != expected_sizes[key]
            for key in expected_keys
        ):
            _fail("OLDER_RAW_OBJECT_LEAF_BYTES_INVALID")
    else:
        if len(entries) != EXPECTED_BATCH_COUNTS[batch_id]:
            _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
        if sum(item["size_bytes"] for item in entries.values()) != (
            EXPECTED_BATCH_BYTES[batch_id]
        ):
            _fail("OLDER_RAW_OBJECT_LEAF_BYTES_INVALID")
    if expected_relative_paths is not None and any(
        entries.get(key, {}).get("relative_path") != relative
        for key, relative in expected_relative_paths.items()
    ):
        _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")

    ordered_paths = sorted(
        item["leaf_relative_path"] for item in entries.values()
    )
    metadata.sort(key=lambda row: str(row["relative_path"]))
    directory_rows.sort()
    return {
        "batch_id": batch_id,
        "entries": entries,
        "file_count": len(entries),
        "total_bytes": sum(item["size_bytes"] for item in entries.values()),
        "directory_count": len(directory_rows) + 1,
        "relative_path_projection_sha256": core.canonical_json_sha256(
            ordered_paths
        ),
        "metadata_projection_sha256": core.canonical_json_sha256(metadata),
        "directory_projection_sha256": core.canonical_json_sha256(
            directory_rows
        ),
        "leaf_device": leaf_info.st_dev,
        "leaf_inode": leaf_info.st_ino,
        "leaf_mode": stat.S_IMODE(leaf_info.st_mode),
        "leaf_uid": leaf_info.st_uid,
        "leaf_gid": leaf_info.st_gid,
        "leaf_nlink": leaf_info.st_nlink,
        "leaf_size": leaf_info.st_size,
        "leaf_mtime_ns": leaf_info.st_mtime_ns,
        "leaf_ctime_ns": leaf_info.st_ctime_ns,
    }


def _retained_receipt_tree_authority(
    attempt_root: Path,
    receipts_root: Path,
    *,
    batch_id: str,
    expected_receipt_names: set[str],
) -> dict[str, Any]:
    """Validate required receipts and retain safe auxiliary files opaquely."""

    try:
        root_info = os.lstat(receipts_root)
        attempt_info = os.lstat(attempt_root)
        expected_root = attempt_root / "raw" / batch_id / "receipts"
        deletion_leaves = _target_leaves(attempt_root)
        if (
            batch_id not in TARGET_BATCHES
            or receipts_root != expected_root
            or stat.S_ISLNK(attempt_info.st_mode)
            or not stat.S_ISDIR(attempt_info.st_mode)
            or attempt_info.st_uid != os.geteuid()
            or stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or root_info.st_dev != attempt_info.st_dev
            or os.path.ismount(receipts_root)
        ):
            _fail("OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID")
        current = attempt_root
        for component in receipts_root.relative_to(attempt_root).parts:
            current = current / component
            current_info = os.lstat(current)
            if (
                stat.S_ISLNK(current_info.st_mode)
                or not stat.S_ISDIR(current_info.st_mode)
                or current_info.st_uid != os.geteuid()
                or current_info.st_dev != attempt_info.st_dev
                or os.path.ismount(current)
            ):
                _fail("OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID")
        observed: dict[str, tuple[Path, os.stat_result]] = {}
        for entry in os.scandir(receipts_root):
            required = entry.name in expected_receipt_names
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                code = (
                    "OLDER_RAW_REQUIRED_RECEIPT_INVALID"
                    if required
                    else "OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID"
                )
                raise OlderRawRetirementError(code) from exc
            path = Path(entry.path)
            if (
                path.parent != receipts_root
                or any(
                    path == leaf or leaf in path.parents
                    for leaf in deletion_leaves
                )
                or entry.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_dev != root_info.st_dev
            ):
                code = (
                    "OLDER_RAW_REQUIRED_RECEIPT_INVALID"
                    if required
                    else "OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID"
                )
                _fail(code)
            observed[entry.name] = (path, info)
    except OlderRawRetirementError:
        raise
    except (OSError, ValueError) as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID"
        ) from exc

    observed_names = set(observed)
    missing = expected_receipt_names - observed_names
    if missing:
        _fail("OLDER_RAW_REQUIRED_RECEIPT_MISSING")
    auxiliary_names = observed_names - expected_receipt_names
    auxiliary_metadata = [
        (
            name,
            stat.S_IMODE(observed[name][1].st_mode),
            observed[name][1].st_uid,
            observed[name][1].st_gid,
            observed[name][1].st_nlink,
            observed[name][1].st_size,
            observed[name][1].st_dev,
            observed[name][1].st_ino,
            observed[name][1].st_mtime_ns,
            observed[name][1].st_ctime_ns,
        )
        for name in sorted(auxiliary_names)
    ]
    return {
        "required_receipt_files": len(expected_receipt_names),
        "auxiliary_receipt_files": len(auxiliary_names),
        "auxiliary_receipt_bytes": sum(
            observed[name][1].st_size for name in auxiliary_names
        ),
        "auxiliary_metadata_projection_sha256": core.canonical_json_sha256(
            auxiliary_metadata
        ),
        "auxiliary_files_retained": True,
    }


def _aggregate_receipt_tree_authority(
    rows: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    if [batch for batch, _authority in rows] != list(TARGET_BATCHES):
        _fail("OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID")
    if any(
        set(authority) != RECEIPT_TREE_AUTHORITY_KEYS
        or type(authority.get("required_receipt_files")) is not int
        or authority["required_receipt_files"] < 0
        or type(authority.get("auxiliary_receipt_files")) is not int
        or authority["auxiliary_receipt_files"] < 0
        or type(authority.get("auxiliary_receipt_bytes")) is not int
        or authority["auxiliary_receipt_bytes"] < 0
        or not isinstance(
            authority.get("auxiliary_metadata_projection_sha256"), str
        )
        or SHA_RE.fullmatch(
            authority["auxiliary_metadata_projection_sha256"]
        ) is None
        or authority.get("auxiliary_files_retained") is not True
        for _batch, authority in rows
    ):
        _fail("OLDER_RAW_AUXILIARY_RECEIPT_FILE_AUTHORITY_INVALID")
    return {
        "required_receipt_files": sum(
            authority["required_receipt_files"]
            for _batch, authority in rows
        ),
        "auxiliary_receipt_files": sum(
            authority["auxiliary_receipt_files"]
            for _batch, authority in rows
        ),
        "auxiliary_receipt_bytes": sum(
            authority["auxiliary_receipt_bytes"]
            for _batch, authority in rows
        ),
        "auxiliary_metadata_projection_sha256": core.canonical_json_sha256(
            [
                {
                    "batch_id": batch,
                    "metadata_projection_sha256": authority[
                        "auxiliary_metadata_projection_sha256"
                    ],
                }
                for batch, authority in rows
            ]
        ),
        "auxiliary_files_retained": True,
    }


def _current_older_receipt_tree_authority(
    older_root: Path, targets: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    rows: list[tuple[str, Mapping[str, Any]]] = []
    for batch_id in TARGET_BATCHES:
        expected_names = {
            f"{item['source_object_key']}.verification.json"
            for item in targets
            if item.get("batch_id") == batch_id
        }
        rows.append(
            (
                batch_id,
                _retained_receipt_tree_authority(
                    older_root,
                    older_root / "raw" / batch_id / "receipts",
                    batch_id=batch_id,
                    expected_receipt_names=expected_names,
                ),
            )
        )
    authority = _aggregate_receipt_tree_authority(rows)
    if authority["required_receipt_files"] != EXPECTED_DELETE_FILES:
        _fail("OLDER_RAW_REQUIRED_RECEIPT_MISSING")
    return authority


def _partial_tree_authority(
    attempt_root: Path,
    partials_root: Path,
    *,
    batch_id: str,
    attempt_id: str,
    expected_source_keys: set[str],
) -> dict[str, Any]:
    """Validate a preserved sibling partial tree without opening any file."""

    if not os.path.lexists(partials_root):
        return {"file_count": 0, "total_bytes": 0, "directory_count": 0}
    planned = re.compile(
        rf"^(?P<source_key>[0-9a-f]{{64}})\.{re.escape(attempt_id)}\.partial$"
    )
    try:
        root_info = os.lstat(partials_root)
        attempt_info = os.lstat(attempt_root)
        expected_root = attempt_root / "raw" / batch_id / "partials"
        try:
            mountpoints = _mountinfo_mountpoints()
        except OlderRawRetirementError as exc:
            raise OlderRawRetirementError(
                "OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID"
            ) from exc
        if any(
            mountpoint == attempt_root or attempt_root in mountpoint.parents
            for mountpoint in mountpoints
        ):
            _fail("OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID")
        if (
            partials_root != expected_root
            or stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or root_info.st_dev != attempt_info.st_dev
            or stat.S_IMODE(root_info.st_mode) not in {0o700, 0o2700}
            or os.path.ismount(partials_root)
        ):
            _fail("OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID")
        file_count = 0
        total_bytes = 0
        directory_count = 1
        seen_inodes: set[tuple[int, int]] = set()
        seen_keys: set[str] = set()
        for current_name, names, files in os.walk(
            partials_root, topdown=True, followlinks=False
        ):
            current = Path(current_name)
            info = os.lstat(current)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_dev != root_info.st_dev
                or stat.S_IMODE(info.st_mode) not in {0o700, 0o2700}
                or (current != partials_root and os.path.ismount(current))
            ):
                _fail("OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID")
            names[:] = sorted(names)
            for name in names:
                directory = current / name
                directory_info = os.lstat(directory)
                if (
                    name.startswith(".nfs")
                    or ".partial" in name
                    or stat.S_ISLNK(directory_info.st_mode)
                    or not stat.S_ISDIR(directory_info.st_mode)
                    or directory_info.st_uid != os.geteuid()
                    or directory_info.st_dev != root_info.st_dev
                    or stat.S_IMODE(directory_info.st_mode)
                    not in {0o700, 0o2700}
                    or os.path.ismount(directory)
                ):
                    _fail("OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID")
                directory_count += 1
            for name in sorted(files):
                path = current / name
                file_info = os.lstat(path)
                identity = (file_info.st_dev, file_info.st_ino)
                match = planned.fullmatch(name)
                if (
                    name.startswith(".nfs")
                    or match is None
                    or match.group("source_key") not in expected_source_keys
                    or match.group("source_key") in seen_keys
                    or stat.S_ISLNK(file_info.st_mode)
                    or not stat.S_ISREG(file_info.st_mode)
                    or file_info.st_uid != os.geteuid()
                    or file_info.st_nlink != 1
                    or stat.S_IMODE(file_info.st_mode) != 0o600
                    or file_info.st_dev != root_info.st_dev
                    or identity in seen_inodes
                ):
                    _fail("OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID")
                seen_keys.add(match.group("source_key"))
                seen_inodes.add(identity)
                file_count += 1
                total_bytes += file_info.st_size
    except OlderRawRetirementError:
        raise
    except OSError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID"
        ) from exc
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "directory_count": directory_count,
    }


def _validate_attempt_batch(
    attempt_root: Path, bundle: PlanBundle, batch_id: str
) -> tuple[dict[str, dict[str, Any]], str, str, str, dict[str, Any]]:
    planned = _batch(bundle.plan, batch_id)
    objects = list(planned["objects"])
    expected = {str(item["source_object_key"]): item for item in objects}
    if len(expected) != len(objects):
        _fail("OLDER_RAW_BATCH_AUTHORITY_INVALID")
    raw = attempt_root / "raw" / batch_id
    manifest_path = raw / "verified_download_manifest.restricted.csv"
    schema = _verified_download_manifest_role(
        attempt_id=bundle.authority.attempt_id, batch_id=batch_id
    )
    try:
        manifest_payload = _read_private(manifest_path)
    except Exception as exc:
        raise OlderRawDownloadManifestError(
            "OLDER_RAW_DOWNLOAD_MANIFEST_FILE_INVALID", schema.role
        ) from exc
    manifest, manifest_sha = _validate_verified_download_manifest_payload(
        manifest_payload,
        schema=schema,
        expected_attempt_id=bundle.authority.attempt_id,
        expected_batch_id=batch_id,
        expected=expected,
    )
    ledger_path = attempt_root / "batches" / batch_id / "download_resume_ledger.restricted.json"
    ledger, ledger_payload = _read_json(ledger_path)
    runtime = core.validate_runtime_authority(
        {**bundle.plan["authority"], "batch_plan_sha256": bundle.plan_sha256}
    )
    try:
        core.validate_resume_authority(
            ledger,
            expected_authority=runtime,
            attempt_id=bundle.authority.attempt_id,
            expected_object_keys={batch_id: set(expected)},
        )
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_DOWNLOAD_LEDGER_INVALID") from exc
    if set(ledger.get("batches", {})) != {batch_id}:
        _fail("OLDER_RAW_DOWNLOAD_LEDGER_INVALID")
    ledger_batch = ledger["batches"][batch_id]
    receipt_map = ledger_batch.get("download_verification_receipts")
    if (
        ledger_batch.get("state") != "DOWNLOAD_VERIFIED"
        or ledger_batch.get("download_manifest_sha256") != manifest_sha
        or not isinstance(receipt_map, Mapping)
        or set(receipt_map) != set(expected)
    ):
        _fail("OLDER_RAW_DOWNLOAD_LEDGER_INVALID")
    objects_root = raw / "objects"
    receipts_root = raw / "receipts"
    partials_root = raw / "partials"
    expected_receipt_names = {f"{key}.verification.json" for key in expected}
    leaf_authority = _raw_object_leaf_authority(
        attempt_root,
        objects_root,
        batch_id=batch_id,
        expected_sizes={key: int(row["size_bytes"]) for key, row in expected.items()},
    )
    receipt_tree_authority = _retained_receipt_tree_authority(
        attempt_root,
        receipts_root,
        batch_id=batch_id,
        expected_receipt_names=expected_receipt_names,
    )
    _partial_tree_authority(
        attempt_root,
        partials_root,
        batch_id=batch_id,
        attempt_id=bundle.authority.attempt_id,
        expected_source_keys=set(expected),
    )
    try:
        mount_authority = r3e.validate_current_mount_authority(
            attempt_root, objects_root
        )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_CURRENT_MOUNT_AUTHORITY_INVALID"
        ) from exc
    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(expected):
        row = expected[key]
        observed = leaf_authority["entries"][key]
        path = observed["path"]
        receipt_path = receipts_root / f"{key}.verification.json"
        info = observed["info"]
        try:
            receipt, receipt_payload = _read_json(
                receipt_path, maximum=r3e.MAXIMUM_RECEIPT_BYTES
            )
            receipt_sha = _sha(receipt_payload)
            if receipt_map.get(key) != receipt_sha:
                _fail("OLDER_RAW_REQUIRED_RECEIPT_INVALID")
            local_sha = _receipt_authority(
                receipt,
                row=row,
                info=info,
                mount_authority=mount_authority,
                authority=bundle.authority,
            )
        except OlderRawRetirementError as exc:
            if exc.code == "OLDER_RAW_REQUIRED_RECEIPT_INVALID":
                raise
            raise OlderRawRetirementError(
                "OLDER_RAW_REQUIRED_RECEIPT_INVALID"
            ) from exc
        if manifest[key]["observed_sha256"] != local_sha:
            _fail("OLDER_RAW_LOCAL_SHA_MISMATCH")
        entries[key] = {
            "batch_id": batch_id,
            "relative_path": path.relative_to(attempt_root).as_posix(),
            "source_object_key": key,
            "subject_id": str(row["subject_id"]),
            "study_id": str(row["study_id"]),
            "split": str(row["split"]),
            "source_relative_path": str(row["source_relative_path"]),
            "size_bytes": int(row["size_bytes"]),
            "generation": str(row["generation"]),
            "md5_base64": str(row["md5_base64"]),
            "crc32c_base64": str(row["crc32c_base64"]),
            "local_sha256": local_sha,
            "verification_receipt_sha256": receipt_sha,
            "file_inode": info.st_ino,
            "file_mtime_ns": info.st_mtime_ns,
        }
    if (
        len(entries) != int(planned["n_objects"])
        or sum(item["size_bytes"] for item in entries.values())
        != int(planned["source_bytes"])
    ):
        _fail("OLDER_RAW_BATCH_AUTHORITY_INVALID")
    return (
        entries,
        manifest_sha,
        _sha(ledger_payload),
        schema.role,
        receipt_tree_authority,
    )


def _opaque_diagnostic_metadata_rows(
) -> tuple[int, list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Inventory every retained R3E diagnostic root without opening a file."""

    owner_private = Path(os.path.abspath(OWNER_PRIVATE_ROOT))
    production_root = Path(os.path.abspath(PRODUCTION_ROOT))
    deletion_targets = tuple(
        Path(os.path.abspath(path))
        for path in _target_leaves(
            production_root / "attempts" / OLDER_ATTEMPT_ID
        )
    )

    def overlaps_deletion_target(path: Path) -> bool:
        return any(
            path == target
            or path in target.parents
            or target in path.parents
            for target in deletion_targets
        )

    def contained(root: Path, path: Path) -> bool:
        return path == root or root in path.parents

    try:
        production_info = os.lstat(production_root)
        owner_private_info = os.lstat(owner_private)
        if (
            stat.S_ISLNK(production_info.st_mode)
            or not stat.S_ISDIR(production_info.st_mode)
            or stat.S_ISLNK(owner_private_info.st_mode)
            or not stat.S_ISDIR(owner_private_info.st_mode)
            or owner_private_info.st_uid != os.geteuid()
            or owner_private_info.st_dev != production_info.st_dev
        ):
            _fail("OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID")
        try:
            mountpoints = _mountinfo_mountpoints()
        except OlderRawRetirementError as exc:
            raise OlderRawRetirementError(
                "OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID"
            ) from exc

        roots: list[tuple[Path, os.stat_result]] = []
        with os.scandir(owner_private) as entries:
            for entry in entries:
                if r3e.DIAGNOSTIC_NAME_RE.fullmatch(entry.name) is None:
                    continue
                root = Path(os.path.abspath(entry.path))
                info = os.lstat(root)
                if (
                    root.parent != owner_private
                    or root.name != entry.name
                    or stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_dev != owner_private_info.st_dev
                    or overlaps_deletion_target(root)
                    or os.path.ismount(root)
                    or any(
                        mountpoint == root or root in mountpoint.parents
                        for mountpoint in mountpoints
                    )
                ):
                    _fail("OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID")
                roots.append((root, info))
        roots.sort(key=lambda item: item[0].name)
        if not roots:
            _fail("OLDER_RAW_DIAGNOSTIC_ROOT_MISSING")

        file_rows: list[tuple[Any, ...]] = []
        directory_rows: list[tuple[Any, ...]] = []

        def walk_error(error: OSError) -> None:
            raise error

        for root, root_info in roots:
            for current, names, files in os.walk(
                root,
                topdown=True,
                onerror=walk_error,
                followlinks=False,
            ):
                current_path = Path(os.path.abspath(current))
                current_info = os.lstat(current_path)
                if (
                    not contained(root, current_path)
                    or stat.S_ISLNK(current_info.st_mode)
                    or not stat.S_ISDIR(current_info.st_mode)
                    or current_info.st_uid != os.geteuid()
                    or current_info.st_dev != root_info.st_dev
                    or overlaps_deletion_target(current_path)
                    or os.path.ismount(current_path)
                    or (
                        current_path != root
                        and current_path in mountpoints
                    )
                ):
                    _fail("OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID")
                relative_directory = (
                    "."
                    if current_path == root
                    else current_path.relative_to(root).as_posix()
                )
                directory_rows.append(
                    (
                        root.name,
                        relative_directory,
                        stat.S_IMODE(current_info.st_mode),
                        current_info.st_uid,
                        current_info.st_gid,
                        current_info.st_dev,
                        current_info.st_ino,
                        current_info.st_nlink,
                        current_info.st_size,
                        current_info.st_mtime_ns,
                        current_info.st_ctime_ns,
                    )
                )
                names[:] = sorted(names)
                for name in names:
                    directory = Path(os.path.abspath(current_path / name))
                    directory_info = os.lstat(directory)
                    if (
                        directory.parent != current_path
                        or not contained(root, directory)
                        or stat.S_ISLNK(directory_info.st_mode)
                        or not stat.S_ISDIR(directory_info.st_mode)
                        or directory_info.st_uid != os.geteuid()
                        or directory_info.st_dev != root_info.st_dev
                        or overlaps_deletion_target(directory)
                        or os.path.ismount(directory)
                        or directory in mountpoints
                    ):
                        _fail("OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID")
                for name in sorted(files):
                    path = Path(os.path.abspath(current_path / name))
                    info = os.lstat(path)
                    if (
                        path.parent != current_path
                        or not contained(root, path)
                        or stat.S_ISLNK(info.st_mode)
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or info.st_dev != root_info.st_dev
                        or overlaps_deletion_target(path)
                        or os.path.ismount(path)
                        or path in mountpoints
                    ):
                        _fail("OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID")
                    file_rows.append(
                        (
                            root.name,
                            path.relative_to(root).as_posix(),
                            stat.S_IMODE(info.st_mode),
                            info.st_uid,
                            info.st_gid,
                            info.st_dev,
                            info.st_ino,
                            info.st_nlink,
                            info.st_size,
                            info.st_mtime_ns,
                            info.st_ctime_ns,
                        )
                    )
    except OlderRawRetirementError:
        raise
    except (OSError, ValueError) as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_DIAGNOSTIC_TOPOLOGY_INVALID"
        ) from exc
    return len(roots), sorted(file_rows), sorted(directory_rows)


def _opaque_diagnostic_evidence_authority() -> dict[str, Any]:
    """Seal all retained R3E diagnostics as opaque, metadata-only evidence."""

    first = _opaque_diagnostic_metadata_rows()
    second = _opaque_diagnostic_metadata_rows()
    if first != second:
        _fail("OLDER_RAW_DIAGNOSTIC_METADATA_AUTHORITY_MISMATCH")
    root_count, files, directories = first
    return {
        "status": "PASS_OPAQUE_R3E_DIAGNOSTIC_EVIDENCE_RETAINED",
        "diagnostic_root_count": root_count,
        "diagnostic_file_count": len(files),
        "diagnostic_directory_count": len(directories),
        "diagnostic_total_bytes": sum(int(row[8]) for row in files),
        "diagnostic_file_metadata_projection_sha256": (
            core.canonical_json_sha256(files)
        ),
        "diagnostic_directory_topology_sha256": (
            core.canonical_json_sha256(directories)
        ),
        "diagnostics_outside_deletion_targets": True,
        "diagnostics_retained": True,
        "body_reads": 0,
    }


def _require_sealed_diagnostic_authority(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    sealed = manifest.get("diagnostic_evidence_authority")
    if not isinstance(sealed, Mapping):
        _fail("OLDER_RAW_DIAGNOSTIC_METADATA_AUTHORITY_MISMATCH")
    observed = _opaque_diagnostic_evidence_authority()
    if observed != sealed:
        _fail("OLDER_RAW_DIAGNOSTIC_METADATA_AUTHORITY_MISMATCH")
    return observed


def _validate_opaque_diagnostic_authority_schema(value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != DIAGNOSTIC_AUTHORITY_KEYS
        or value.get("status")
        != "PASS_OPAQUE_R3E_DIAGNOSTIC_EVIDENCE_RETAINED"
        or type(value.get("diagnostic_root_count")) is not int
        or value["diagnostic_root_count"] < 1
        or type(value.get("diagnostic_file_count")) is not int
        or value["diagnostic_file_count"] < 0
        or type(value.get("diagnostic_directory_count")) is not int
        or value["diagnostic_directory_count"]
        < value["diagnostic_root_count"]
        or type(value.get("diagnostic_total_bytes")) is not int
        or value["diagnostic_total_bytes"] < 0
        or value.get("diagnostics_outside_deletion_targets") is not True
        or value.get("diagnostics_retained") is not True
        or type(value.get("body_reads")) is not int
        or value["body_reads"] != 0
        or any(
            not isinstance(value.get(key), str)
            or SHA_RE.fullmatch(value[key]) is None
            for key in (
                "diagnostic_file_metadata_projection_sha256",
                "diagnostic_directory_topology_sha256",
            )
        )
    ):
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")


def _target_leaves(attempt_root: Path) -> tuple[Path, ...]:
    return tuple(attempt_root / "raw" / batch / "objects" for batch in TARGET_BATCHES)


def _partial_trees(attempt_root: Path) -> tuple[Path, ...]:
    return tuple(attempt_root / "raw" / batch / "partials" for batch in TARGET_BATCHES)


def _pre_cleanup_capacity_authority() -> dict[str, Any]:
    """Load the frozen f201e22 observation as historical cleanup evidence."""

    try:
        receipt_path = (
            OWNER_PRIVATE_ROOT
            / capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        )
        sealed_receipt, _sealed_payload = _read_json(receipt_path)
        captured_at = sealed_receipt.get("captured_at_utc")
        if (
            sealed_receipt.get("governing_commit")
            != R5E_R2_PRE_ACTION_GOVERNING_COMMIT
            or not isinstance(captured_at, str)
        ):
            _fail("OLDER_RAW_PRE_CLEANUP_CAPACITY_AUTHORITY_INVALID")
        historical_now = datetime.fromisoformat(
            captured_at.replace("Z", "+00:00")
        )
        captured = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=receipt_path,
            aggregate_summary_path=(
                OWNER_PRIVATE_ROOT
                / capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
            ),
            expected_governing_commit=R5E_R2_PRE_ACTION_GOVERNING_COMMIT,
            now_utc=historical_now,
        )
        captured = capacity.validate_production_dynamic_successor_capacity_capture(
            captured,
            expected_governing_commit=R5E_R2_PRE_ACTION_GOVERNING_COMMIT,
            now_utc=historical_now,
        )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_PRE_CLEANUP_CAPACITY_AUTHORITY_INVALID"
        ) from exc
    status = captured.observation.get("status")
    if status not in {
        capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        capacity.DYNAMIC_SUCCESSOR_STATUS_BLOCKED,
    }:
        _fail("OLDER_RAW_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS")
    return {
        "status": status,
        "receipt_basename": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        "receipt_bytes": len(captured.receipt_payload),
        "receipt_sha256": _sha(captured.receipt_payload),
        "summary_basename": (
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
        "summary_bytes": len(capacity._canonical(captured.observation)),
        "summary_sha256": _sha(capacity._canonical(captured.observation)),
    }


def _derive_manifest(governing_commit: str) -> dict[str, Any]:
    if COMMIT_RE.fullmatch(governing_commit) is None:
        _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    r4_root = PRODUCTION_ROOT / "attempts" / R4_ATTEMPT_ID
    pre_cleanup_capacity = _pre_cleanup_capacity_authority()
    try:
        older_inventory, _older_identity = (
            r3e.validate_immutable_legacy_attempt_effective_privacy(
                older_root, r3e.ORIGINAL_AUTHORITY
            )
        )
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_PREINVENTORY_INVALID") from exc
    if (
        older_inventory.get("file_count") != EXPECTED_PRE_FILES
        or older_inventory.get("total_bytes") != EXPECTED_PRE_BYTES
    ):
        _fail("OLDER_RAW_PREINVENTORY_INVALID")
    r4_authority = _validate_r4()
    older_bundle = _load_plan_bundle(older_root, authority=r3e.ORIGINAL_AUTHORITY)
    r4_authority_adapter = r4d2._r3e_device_authority(r4d2.R4_AUTHORITY)
    r4_bundle = _load_plan_bundle(r4_root, authority=r4_authority_adapter)
    targets: list[dict[str, Any]] = []
    batch_authorities: list[dict[str, Any]] = []
    older_receipt_rows: list[tuple[str, Mapping[str, Any]]] = []
    for batch_id in TARGET_BATCHES:
        older_batch = _batch(older_bundle.plan, batch_id)
        r4_batch = _batch(r4_bundle.plan, batch_id)
        projection_fields = (
            "batch_id",
            "ordinal",
            "n_objects",
            "source_bytes",
            "study_membership_sha256",
            "source_membership_sha256",
        )
        if any(older_batch[field] != r4_batch[field] for field in projection_fields):
            _fail("OLDER_RAW_CROSS_ATTEMPT_BATCH_MISMATCH")
        (
            older_entries,
            older_manifest_sha,
            older_ledger_sha,
            older_manifest_role,
            older_receipt_authority,
        ) = _validate_attempt_batch(older_root, older_bundle, batch_id)
        (
            r4_entries,
            r4_manifest_sha,
            r4_ledger_sha,
            r4_manifest_role,
            _r4_receipt_authority,
        ) = _validate_attempt_batch(r4_root, r4_bundle, batch_id)
        older_receipt_rows.append((batch_id, older_receipt_authority))
        if set(older_entries) != set(r4_entries):
            _fail("OLDER_RAW_CROSS_ATTEMPT_OBJECT_MISMATCH")
        for key in sorted(older_entries):
            left = older_entries[key]
            right = r4_entries[key]
            science_fields = (
                "batch_id", "source_object_key", "subject_id", "study_id",
                "split", "source_relative_path", "size_bytes", "generation",
                "md5_base64", "crc32c_base64", "local_sha256",
            )
            if any(left[field] != right[field] for field in science_fields):
                _fail("OLDER_RAW_CROSS_ATTEMPT_OBJECT_MISMATCH")
            targets.append(
                {
                    **left,
                    "r4_relative_path": right["relative_path"],
                    "r4_verification_receipt_sha256": right[
                        "verification_receipt_sha256"
                    ],
                    "r4_file_inode": right["file_inode"],
                    "r4_file_mtime_ns": right["file_mtime_ns"],
                }
            )
        batch_authorities.append(
            {
                "batch_id": batch_id,
                "n_objects": len(older_entries),
                "source_bytes": sum(item["size_bytes"] for item in older_entries.values()),
                "study_membership_sha256": older_batch["study_membership_sha256"],
                "source_membership_sha256": older_batch["source_membership_sha256"],
                "older_plan_sha256": older_bundle.plan_sha256,
                "r4_plan_sha256": r4_bundle.plan_sha256,
                "older_download_manifest_sha256": older_manifest_sha,
                "r4_download_manifest_sha256": r4_manifest_sha,
                "older_download_ledger_sha256": older_ledger_sha,
                "r4_download_ledger_sha256": r4_ledger_sha,
                "older_download_manifest_role": older_manifest_role,
                "r4_download_manifest_role": r4_manifest_role,
                "download_manifest_canonical_projection": (
                    VERIFIED_DOWNLOAD_CANONICAL_PROJECTION_V1
                ),
            }
        )
    if (
        len(targets) != EXPECTED_DELETE_FILES
        or sum(item["size_bytes"] for item in targets) != EXPECTED_DELETE_BYTES
        or any(len([item for item in targets if item["batch_id"] == batch]) != EXPECTED_BATCH_COUNTS[batch] for batch in TARGET_BATCHES)
        or any(sum(item["size_bytes"] for item in targets if item["batch_id"] == batch) != EXPECTED_BATCH_BYTES[batch] for batch in TARGET_BATCHES)
    ):
        _fail("OLDER_RAW_TARGET_AGGREGATE_MISMATCH")
    diagnostic_authority = _opaque_diagnostic_evidence_authority()
    leaves = _target_leaves(older_root)
    older_receipt_tree_authority = _aggregate_receipt_tree_authority(
        older_receipt_rows
    )
    retained = _retained_evidence_authority(
        older_root,
        required_receipt_relative_paths=_required_receipt_relative_paths(
            targets
        ),
        excluded_roots=leaves,
    )
    if (
        retained["file_count"] != EXPECTED_RETAINED_FILES
        or retained["total_bytes"] != EXPECTED_RETAINED_BYTES
    ):
        _fail("OLDER_RAW_RETAINED_AUTHORITY_INVALID")
    target_projection = [
        {key: item[key] for key in TARGET_PROJECTION_KEYS}
        for item in targets
    ]
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r5e_older_raw_retirement_manifest_v1",
        "status": "PASS_EXACT_RAW_DUPLICATE_AUTHORITY",
        "governing_commit": governing_commit,
        "older_attempt_id": OLDER_ATTEMPT_ID,
        "r4_attempt_id": R4_ATTEMPT_ID,
        "older_execution_commit": OLDER_EXECUTION_COMMIT,
        "r4_execution_commit": R4_EXECUTION_COMMIT,
        "pre_cleanup_capacity_authority": pre_cleanup_capacity,
        "older_pre_files": EXPECTED_PRE_FILES,
        "older_pre_bytes": EXPECTED_PRE_BYTES,
        "planned_delete_files": EXPECTED_DELETE_FILES,
        "planned_delete_bytes": EXPECTED_DELETE_BYTES,
        "expected_retained_files": EXPECTED_RETAINED_FILES,
        "expected_retained_bytes": EXPECTED_RETAINED_BYTES,
        "target_leaf_relative_paths": [
            path.relative_to(older_root).as_posix() for path in leaves
        ],
        "target_set_sha256": core.canonical_json_sha256(target_projection),
        "retained_file_metadata_sha256": retained["file_metadata_sha256"],
        "retained_directory_topology_sha256": retained[
            "directory_topology_sha256"
        ],
        "retained_role_inventory": retained["role_inventory"],
        "retained_role_inventory_sha256": retained[
            "role_inventory_sha256"
        ],
        "retained_control_content_sha256": retained[
            "control_content_sha256"
        ],
        "older_receipt_tree_authority": older_receipt_tree_authority,
        "batch_authorities": batch_authorities,
        "compared_raw_objects": EXPECTED_DELETE_FILES,
        "exact_r4_matches": EXPECTED_DELETE_FILES,
        "mismatches": 0,
        "missing_in_older": 0,
        "missing_in_r4": 0,
        "diagnostic_source_hold": 0,
        "diagnostic_source_retention": "BYTE_IDENTICAL_COPY_PRESERVED_IN_R4",
        "diagnostic_evidence_authority": diagnostic_authority,
        "diagnostic_evidence_authority_sha256": core.canonical_json_sha256(
            diagnostic_authority
        ),
        "r4_authority": r4_authority,
        "targets": targets,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "model_fitting": 0,
        "prediction_generation": 0,
        "confirmatory_performance_accesses": 0,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }


def _target_relative_path_is_safe(
    value: Any, *, batch_id: str, source_key: str
) -> bool:
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    prefix = ("raw", batch_id, "objects")
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) >= 4
        and tuple(path.parts[:3]) == prefix
        and path.name == f"{source_key}.dcm"
    )


def _validate_manifest(value: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema_version", "artifact_type", "status", "governing_commit",
        "older_attempt_id", "r4_attempt_id", "older_execution_commit",
        "r4_execution_commit", "pre_cleanup_capacity_authority",
        "older_pre_files", "older_pre_bytes",
        "planned_delete_files", "planned_delete_bytes", "expected_retained_files",
        "expected_retained_bytes", "target_leaf_relative_paths",
        "target_set_sha256", "retained_file_metadata_sha256",
        "retained_directory_topology_sha256", "retained_role_inventory",
        "retained_role_inventory_sha256", "retained_control_content_sha256",
        "older_receipt_tree_authority", "batch_authorities",
        "compared_raw_objects", "exact_r4_matches", "mismatches",
        "missing_in_older", "missing_in_r4", "diagnostic_source_hold",
        "diagnostic_source_retention", "diagnostic_evidence_authority",
        "diagnostic_evidence_authority_sha256", "r4_authority", "targets",
        "dicom_body_reads", "npz_body_reads", "cloud_requests",
        "qsub_submissions", "model_fitting", "prediction_generation",
        "confirmatory_performance_accesses", "identifiers_emitted", "paths_emitted",
    }
    if set(value) != expected_keys:
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
    targets = value.get("targets")
    batch_authorities = value.get("batch_authorities")
    r4_authority = value.get("r4_authority")
    pre_cleanup_capacity = value.get("pre_cleanup_capacity_authority")
    role_inventory = value.get("retained_role_inventory")
    diagnostic_authority = value.get("diagnostic_evidence_authority")
    receipt_tree_authority = value.get("older_receipt_tree_authority")
    _validate_opaque_diagnostic_authority_schema(diagnostic_authority)
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_r5e_older_raw_retirement_manifest_v1"
        or value.get("status") != "PASS_EXACT_RAW_DUPLICATE_AUTHORITY"
        or not isinstance(value.get("governing_commit"), str)
        or COMMIT_RE.fullmatch(value["governing_commit"]) is None
        or value.get("older_attempt_id") != OLDER_ATTEMPT_ID
        or value.get("r4_attempt_id") != R4_ATTEMPT_ID
        or value.get("older_execution_commit") != OLDER_EXECUTION_COMMIT
        or value.get("r4_execution_commit") != R4_EXECUTION_COMMIT
        or not isinstance(pre_cleanup_capacity, Mapping)
        or set(pre_cleanup_capacity)
        != {
            "status", "receipt_basename", "receipt_bytes",
            "receipt_sha256", "summary_basename", "summary_bytes",
            "summary_sha256",
        }
        or pre_cleanup_capacity.get("status") not in {
            capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
            capacity.DYNAMIC_SUCCESSOR_STATUS_BLOCKED,
        }
        or pre_cleanup_capacity.get("receipt_basename")
        != capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        or type(pre_cleanup_capacity.get("receipt_bytes")) is not int
        or pre_cleanup_capacity["receipt_bytes"] < 1
        or pre_cleanup_capacity.get("summary_basename")
        != capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        or type(pre_cleanup_capacity.get("summary_bytes")) is not int
        or pre_cleanup_capacity["summary_bytes"] < 1
        or any(
            not isinstance(pre_cleanup_capacity.get(key), str)
            or SHA_RE.fullmatch(pre_cleanup_capacity[key]) is None
            for key in ("receipt_sha256", "summary_sha256")
        )
        or value.get("older_pre_files") != EXPECTED_PRE_FILES
        or value.get("older_pre_bytes") != EXPECTED_PRE_BYTES
        or value.get("planned_delete_files") != EXPECTED_DELETE_FILES
        or value.get("planned_delete_bytes") != EXPECTED_DELETE_BYTES
        or value.get("expected_retained_files") != EXPECTED_RETAINED_FILES
        or value.get("expected_retained_bytes") != EXPECTED_RETAINED_BYTES
        or value.get("compared_raw_objects") != EXPECTED_DELETE_FILES
        or value.get("exact_r4_matches") != EXPECTED_DELETE_FILES
        or any(value.get(key) != 0 for key in (
            "mismatches", "missing_in_older", "missing_in_r4",
            "diagnostic_source_hold", "dicom_body_reads", "npz_body_reads",
            "cloud_requests", "qsub_submissions", "model_fitting",
            "prediction_generation", "confirmatory_performance_accesses",
        ))
        or value.get("diagnostic_source_retention")
        != "BYTE_IDENTICAL_COPY_PRESERVED_IN_R4"
        or value.get("target_leaf_relative_paths")
        != [f"raw/{batch}/objects" for batch in TARGET_BATCHES]
        or not isinstance(value.get("retained_file_metadata_sha256"), str)
        or SHA_RE.fullmatch(value["retained_file_metadata_sha256"]) is None
        or not isinstance(value.get("retained_directory_topology_sha256"), str)
        or SHA_RE.fullmatch(value["retained_directory_topology_sha256"]) is None
        or not isinstance(role_inventory, list)
        or len(role_inventory) != len(RETAINED_ROLE_NAMES)
        or [row.get("role") for row in role_inventory if isinstance(row, Mapping)]
        != list(RETAINED_ROLE_NAMES)
        or any(
            not isinstance(row, Mapping)
            or set(row) != RETAINED_ROLE_ROW_KEYS
            or type(row.get("file_count")) is not int
            or row["file_count"] < 0
            or type(row.get("total_bytes")) is not int
            or row["total_bytes"] < 0
            for row in role_inventory
        )
        or sum(row["file_count"] for row in role_inventory)
        != EXPECTED_RETAINED_FILES
        or sum(row["total_bytes"] for row in role_inventory)
        != EXPECTED_RETAINED_BYTES
        or value.get("retained_role_inventory_sha256")
        != core.canonical_json_sha256(role_inventory)
        or not isinstance(value.get("retained_control_content_sha256"), str)
        or SHA_RE.fullmatch(value["retained_control_content_sha256"]) is None
        or not isinstance(receipt_tree_authority, Mapping)
        or set(receipt_tree_authority) != RECEIPT_TREE_AUTHORITY_KEYS
        or receipt_tree_authority.get("required_receipt_files")
        != EXPECTED_DELETE_FILES
        or type(receipt_tree_authority.get("auxiliary_receipt_files")) is not int
        or receipt_tree_authority["auxiliary_receipt_files"] < 0
        or type(receipt_tree_authority.get("auxiliary_receipt_bytes")) is not int
        or receipt_tree_authority["auxiliary_receipt_bytes"] < 0
        or not isinstance(
            receipt_tree_authority.get(
                "auxiliary_metadata_projection_sha256"
            ),
            str,
        )
        or SHA_RE.fullmatch(
            receipt_tree_authority[
                "auxiliary_metadata_projection_sha256"
            ]
        ) is None
        or receipt_tree_authority.get("auxiliary_files_retained") is not True
        or value.get("diagnostic_evidence_authority_sha256")
        != core.canonical_json_sha256(diagnostic_authority)
        or value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
        or not isinstance(targets, list)
        or len(targets) != EXPECTED_DELETE_FILES
        or not isinstance(batch_authorities, list)
        or len(batch_authorities) != len(TARGET_BATCHES)
        or r4_authority
        != {
            "file_count": EXPECTED_R4_FILES,
            "directory_count": EXPECTED_R4_DIRECTORIES,
            "total_bytes": EXPECTED_R4_BYTES,
            "metadata_stat_sha256": EXPECTED_R4_METADATA_SHA256,
        }
    ):
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
    by_role = {row["role"]: row for row in role_inventory}
    if (
        by_role["EXTRACTED_NPZ_CACHE"]["file_count"]
        != EXPECTED_EXTRACTED_NPZ_FILES
        or by_role["EXTRACTED_NPZ_CACHE"]["total_bytes"]
        != EXPECTED_EXTRACTED_NPZ_BYTES
        or by_role["DOWNLOAD_VERIFICATION_RECEIPTS"]["file_count"]
        != EXPECTED_DELETE_FILES
        or any(
            by_role[role]["file_count"] != 0
            or by_role[role]["total_bytes"] != 0
            for role in (
                "RAW_DICOM_PAYLOAD", "R3E_DIAGNOSTIC_EVIDENCE", "UNCLASSIFIED"
            )
        )
    ):
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
    expected_order: list[tuple[str, str]] = []
    observed_keys: set[str] = set()
    for item in targets:
        if not isinstance(item, Mapping) or set(item) != TARGET_KEYS:
            _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
        batch_id = item.get("batch_id")
        source_key = item.get("source_object_key")
        subject_id = item.get("subject_id")
        study_id = item.get("study_id")
        source_relative = item.get("source_relative_path")
        size = item.get("size_bytes")
        generation = item.get("generation")
        relative = item.get("relative_path")
        r4_relative = item.get("r4_relative_path")
        try:
            safe_source_relative = core._safe_source_path(
                source_relative, str(subject_id), str(study_id)
            )
            canonical_md5 = core._base64_digest(
                item.get("md5_base64"), 16, "SOURCE_MD5_INVALID"
            )
            canonical_crc = core._base64_digest(
                item.get("crc32c_base64"), 4, "SOURCE_CRC32C_INVALID"
            )
        except Exception as exc:
            raise OlderRawRetirementError(
                "OLDER_RAW_MANIFEST_SCHEMA_INVALID"
            ) from exc
        if (
            batch_id not in TARGET_BATCHES
            or not isinstance(source_key, str)
            or SHA_RE.fullmatch(source_key) is None
            or source_key in observed_keys
            or not isinstance(subject_id, str)
            or not subject_id.isdigit()
            or str(int(subject_id)) != subject_id
            or not isinstance(study_id, str)
            or not study_id.isdigit()
            or str(int(study_id)) != study_id
            or item.get("split") not in {"train", "val", "test"}
            or not isinstance(source_relative, str)
            or safe_source_relative != source_relative
            or hashlib.sha256(
                f"mimic-iv-echo/1.0\0{source_relative}".encode("utf-8")
            ).hexdigest() != source_key
            or type(size) is not int
            or size <= 0
            or not isinstance(generation, str)
            or not generation.isdigit()
            or str(int(generation)) != generation
            or int(generation) <= 0
            or not _target_relative_path_is_safe(
                relative, batch_id=str(batch_id), source_key=source_key
            )
            or not _target_relative_path_is_safe(
                r4_relative, batch_id=str(batch_id), source_key=source_key
            )
            or item.get("md5_base64") != canonical_md5
            or item.get("crc32c_base64") != canonical_crc
            or any(
                not isinstance(item.get(key), str)
                or SHA_RE.fullmatch(item[key]) is None
                for key in (
                    "local_sha256", "verification_receipt_sha256",
                    "r4_verification_receipt_sha256",
                )
            )
            or any(
                type(item.get(key)) is not int or item[key] <= 0
                for key in (
                    "file_inode", "file_mtime_ns", "r4_file_inode",
                    "r4_file_mtime_ns",
                )
            )
        ):
            _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
        observed_keys.add(source_key)
        expected_order.append((batch_id, source_key))
    if expected_order != sorted(expected_order):
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
    if value.get("target_set_sha256") != core.canonical_json_sha256(
        [
            {key: item[key] for key in TARGET_PROJECTION_KEYS}
            for item in targets
        ]
    ):
        _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")
    for ordinal, batch_id in enumerate(TARGET_BATCHES):
        authority = batch_authorities[ordinal]
        batch_targets = [item for item in targets if item["batch_id"] == batch_id]
        sha_fields = BATCH_AUTHORITY_KEYS - {
            "batch_id",
            "n_objects",
            "source_bytes",
            "older_download_manifest_role",
            "r4_download_manifest_role",
            "download_manifest_canonical_projection",
        }
        if (
            not isinstance(authority, Mapping)
            or set(authority) != BATCH_AUTHORITY_KEYS
            or authority.get("batch_id") != batch_id
            or authority.get("n_objects") != EXPECTED_BATCH_COUNTS[batch_id]
            or authority.get("source_bytes") != EXPECTED_BATCH_BYTES[batch_id]
            or len(batch_targets) != EXPECTED_BATCH_COUNTS[batch_id]
            or sum(item["size_bytes"] for item in batch_targets)
            != EXPECTED_BATCH_BYTES[batch_id]
            or authority.get("older_download_manifest_role")
            != f"OLDER_BATCH_{ordinal + 1}"
            or authority.get("r4_download_manifest_role")
            != f"R4_BATCH_{ordinal + 1}"
            or authority.get("download_manifest_canonical_projection")
            != VERIFIED_DOWNLOAD_CANONICAL_PROJECTION_V1
            or any(
                not isinstance(authority.get(key), str)
                or SHA_RE.fullmatch(authority[key]) is None
                for key in sha_fields
            )
        ):
            _fail("OLDER_RAW_MANIFEST_SCHEMA_INVALID")


def prepare_retirement_manifest(*, governing_commit: str) -> Mapping[str, Any]:
    if governing_commit != _current_commit():
        _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
    _validate_safe_export()
    if any(os.path.lexists(path) for path in (MANIFEST_PATH, RECEIPT_PATH, SUMMARY_PATH)):
        _fail("OLDER_RAW_EVIDENCE_COLLISION")
    quiescence = _quiescent()
    manifest = _derive_manifest(governing_commit)
    _validate_manifest(manifest)
    receipt_tree = manifest["older_receipt_tree_authority"]
    diagnostics = manifest["diagnostic_evidence_authority"]
    payload = _canonical(manifest)
    _write_new(MANIFEST_PATH, payload)
    return {
        "status": "PASS_RETIREMENT_MANIFEST_SEALED",
        "manifest_basename": MANIFEST_BASENAME,
        "manifest_bytes": len(payload),
        "manifest_sha256": _sha(payload),
        "planned_delete_files": EXPECTED_DELETE_FILES,
        "planned_delete_bytes": EXPECTED_DELETE_BYTES,
        "expected_retained_files": EXPECTED_RETAINED_FILES,
        "expected_retained_bytes": EXPECTED_RETAINED_BYTES,
        "required_receipt_files": receipt_tree["required_receipt_files"],
        "auxiliary_receipt_files": receipt_tree["auxiliary_receipt_files"],
        "auxiliary_receipt_bytes": receipt_tree["auxiliary_receipt_bytes"],
        "diagnostic_root_count": diagnostics["diagnostic_root_count"],
        "diagnostic_file_count": diagnostics["diagnostic_file_count"],
        "diagnostic_directory_count": diagnostics[
            "diagnostic_directory_count"
        ],
        "diagnostic_total_bytes": diagnostics["diagnostic_total_bytes"],
        "diagnostics_retained": diagnostics["diagnostics_retained"],
        "diagnostic_body_reads": diagnostics["body_reads"],
        "four_manifest_authority": "PASS",
        "r4_copy_authority": "PASS",
        "r4_metadata_sha256": EXPECTED_R4_METADATA_SHA256,
        "diagnostic_source_hold": 0,
        "target_leaves": len(TARGET_BATCHES),
        "stable_target_leaves": quiescence["stable_target_leaves"],
        "same_user_processes_observed": quiescence[
            "same_user_processes_observed"
        ],
        "candidate_processes": quiescence["candidate_processes"],
        "vanished_processes": quiescence["vanished_processes"],
        "unrelated_inaccessible_processes": quiescence[
            "unrelated_inaccessible_processes"
        ],
        "candidate_inaccessible_processes": quiescence[
            "candidate_inaccessible_processes"
        ],
        "confirmed_target_references": quiescence[
            "confirmed_target_references"
        ],
        "matching_scheduler_jobs": quiescence[
            "matching_scheduler_jobs"
        ],
        "active_references": quiescence["confirmed_target_references"],
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "destructive_operations": 0,
    }


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _candidate_tokens(target_leaves: Sequence[Path]) -> tuple[str, ...]:
    return tuple(
        value.lower()
        for value in (
            OLDER_ATTEMPT_ID,
            *TARGET_BATCHES,
            *(str(path) for path in target_leaves),
            Path(__file__).name,
            "lvef_c3_full_sequential.py",
            "lvef_c3_production_stages.py",
        )
    )


def _under_target(value: str, target_leaves: Sequence[Path]) -> bool:
    if value.endswith(" (deleted)"):
        value = value[:-10]
    if not value.startswith("/"):
        return False
    observed = Path(os.path.abspath(value))
    return any(observed == leaf or leaf in observed.parents for leaf in target_leaves)


def _inspect_proc_references(
    process_id: int,
    *,
    target_leaves: Sequence[Path],
    required: bool,
) -> str:
    """Classify one candidate/unknown process without exporting its metadata."""

    try:
        references = [os.readlink(f"/proc/{process_id}/cwd")]
        with os.scandir(f"/proc/{process_id}/fd") as descriptors:
            for descriptor in descriptors:
                try:
                    references.append(os.readlink(descriptor.path))
                except OSError as exc:
                    if exc.errno in {errno.ENOENT, errno.ESRCH}:
                        continue
                    if exc.errno in {errno.EACCES, errno.EPERM}:
                        if required:
                            return "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING"
                        return "UNRELATED_PROCESS_PROC_INACCESSIBLE_NONBLOCKING"
                    raise
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ESRCH}:
            return "PROCESS_EXITED_DURING_SCAN_NONBLOCKING"
        if exc.errno in {errno.EACCES, errno.EPERM}:
            return (
                "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING"
                if required
                else "UNRELATED_PROCESS_PROC_INACCESSIBLE_NONBLOCKING"
            )
        raise OlderRawRetirementError(
            "OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID"
        ) from exc
    if any(_under_target(value, target_leaves) for value in references):
        return "MATCHING_TARGET_REFERENCE_BLOCKING"
    return "UNRELATED_PROCESS_INSPECTED_NONBLOCKING"


def _target_leaf_snapshot(
    older_root: Path, leaf: Path, *, batch_id: str
) -> dict[str, Any]:
    """Return an exact metadata-only snapshot of one fixed raw leaf."""

    authority = _raw_object_leaf_authority(
        older_root,
        leaf,
        batch_id=batch_id,
        expected_sizes=None,
    )
    return {
        "batch_id": batch_id,
        "file_count": authority["file_count"],
        "total_bytes": authority["total_bytes"],
        "directory_count": authority["directory_count"],
        "relative_name_projection_sha256": authority[
            "relative_path_projection_sha256"
        ],
        "metadata_projection_sha256": authority[
            "metadata_projection_sha256"
        ],
        "leaf_device": authority["leaf_device"],
        "leaf_inode": authority["leaf_inode"],
        "leaf_mode": authority["leaf_mode"],
        "leaf_uid": authority["leaf_uid"],
        "leaf_gid": authority["leaf_gid"],
        "leaf_nlink": authority["leaf_nlink"],
        "leaf_size": authority["leaf_size"],
        "leaf_mtime_ns": authority["leaf_mtime_ns"],
        "leaf_ctime_ns": authority["leaf_ctime_ns"],
        "symlink_entries": 0,
        "nonregular_entries": 0,
        "partial_entries": 0,
        "nfs_entries": 0,
        "unexpected_entries": 0,
    }


def _stable_target_leaf_authority(
    *, sleeper: Any = time.sleep
) -> tuple[dict[str, Any], ...]:
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    leaves = _target_leaves(older_root)
    first = tuple(
        _target_leaf_snapshot(older_root, leaf, batch_id=batch)
        for batch, leaf in zip(TARGET_BATCHES, leaves, strict=True)
    )
    sleeper(QUIESCENCE_STABILITY_INTERVAL_SECONDS)
    second = tuple(
        _target_leaf_snapshot(older_root, leaf, batch_id=batch)
        for batch, leaf in zip(TARGET_BATCHES, leaves, strict=True)
    )
    if first != second:
        _fail("OLDER_RAW_TARGET_LEAF_UNSTABLE")
    return second


def _quiescent() -> Mapping[str, int]:
    """Prove only that no live authority is using either fixed raw leaf."""

    user = os.environ.get("USER")
    if not isinstance(user, str) or not user:
        _fail("OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID")
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    target_leaves = tuple(
        Path(os.path.abspath(path)) for path in _target_leaves(older_root)
    )
    protected_roots = (
        *target_leaves,
        *(Path(os.path.abspath(path)) for path in _partial_trees(older_root)),
    )
    tokens = _candidate_tokens(protected_roots)
    qstat = subprocess.run(
        [str(QSTAT), "-xml", "-u", user],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if qstat.returncode != 0 or qstat.stderr:
        _fail("OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID")
    matching_jobs = 0
    try:
        qstat_root = ET.fromstring(qstat.stdout)
        for job in qstat_root.iter():
            if _local_tag(job) != "job_list":
                continue
            fields = {
                _local_tag(child): child.text or ""
                for child in list(job)
            }
            job_number = fields.get("JB_job_number", "")
            job_name = fields.get("JB_name", "").lower()
            if (
                job_number in {OLDER_ARRAY_JOB_ID, OLDER_FINALIZER_JOB_ID}
                or any(token in job_name for token in tokens)
            ):
                matching_jobs += 1
    except ET.ParseError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID"
        ) from exc
    if matching_jobs:
        _fail("OLDER_RAW_ACTIVE_JOB_EXISTS")

    ps = subprocess.run(
        [
            "/bin/ps", "-u", user, "-o",
            "pid=,state=,lstart=,args=",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if ps.returncode != 0 or ps.stderr:
        _fail("OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID")
    counts = {
        "same_user_processes_observed": 0,
        "candidate_processes": 0,
        "vanished_processes": 0,
        "unrelated_inaccessible_processes": 0,
        "candidate_inaccessible_processes": 0,
        "confirmed_target_references": 0,
        "matching_scheduler_jobs": 0,
        "stable_target_leaves": 0,
    }
    try:
        process_lines = ps.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID"
        ) from exc
    for line in process_lines:
        fields = line.strip().split(None, 7)
        if not fields:
            continue
        try:
            process_id = int(fields[0])
        except ValueError:
            _fail("OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID")
        if process_id <= 0 or process_id == os.getpid():
            continue
        counts["same_user_processes_observed"] += 1
        state = fields[1] if len(fields) > 1 else ""
        if len(fields) < 7 or not state:
            _fail("OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID")
        command = fields[7] if len(fields) == 8 else ""
        command_lower = command.lower()
        candidate = bool(command) and any(token in command_lower for token in tokens)
        unknown = not command
        if candidate:
            counts["candidate_processes"] += 1
        if state.startswith("Z"):
            continue
        disposition = _inspect_proc_references(
            process_id,
            target_leaves=protected_roots,
            required=candidate or unknown,
        )
        if disposition == "PROCESS_EXITED_DURING_SCAN_NONBLOCKING":
            counts["vanished_processes"] += 1
        elif disposition == "UNRELATED_PROCESS_PROC_INACCESSIBLE_NONBLOCKING":
            counts["unrelated_inaccessible_processes"] += 1
        elif disposition == "MATCHING_TARGET_REFERENCE_BLOCKING":
            counts["confirmed_target_references"] += 1
            _fail("OLDER_RAW_ACTIVE_PROCESS_EXISTS")
        elif disposition == "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING":
            counts["candidate_inaccessible_processes"] += 1
            if unknown:
                _fail("OLDER_RAW_UNKNOWN_PROCESS_SCOPE")
            _fail("OLDER_RAW_CANDIDATE_PROC_AUTHORITY_INACCESSIBLE")
    snapshots = _stable_target_leaf_authority()
    counts["stable_target_leaves"] = len(snapshots)
    return counts


def _validate_leaf_from_manifest(
    older_root: Path, leaf: Path, entries: Sequence[Mapping[str, Any]]
) -> None:
    batch_ids = {str(item.get("batch_id")) for item in entries}
    if len(batch_ids) != 1:
        _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
    batch_id = next(iter(batch_ids))
    expected_sizes = {
        str(item["source_object_key"]): int(item["size_bytes"])
        for item in entries
    }
    expected_relative_paths = {
        str(item["source_object_key"]): str(item["relative_path"])
        for item in entries
    }
    if len(expected_sizes) != len(entries):
        _fail("OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID")
    _raw_object_leaf_authority(
        older_root,
        leaf,
        batch_id=batch_id,
        expected_sizes=expected_sizes,
        expected_relative_paths=expected_relative_paths,
    )


def _post_receipt(
    *, manifest_payload: bytes, manifest: Mapping[str, Any], deleted_files: int,
    deleted_bytes: int, retained: Mapping[str, Any], r4_authority: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r5e_older_raw_retirement_receipt_v1",
        "status": status,
        "governing_commit": manifest["governing_commit"],
        "manifest_basename": MANIFEST_BASENAME,
        "manifest_bytes": len(manifest_payload),
        "manifest_sha256": _sha(manifest_payload),
        "planned_delete_files": EXPECTED_DELETE_FILES,
        "planned_delete_bytes": EXPECTED_DELETE_BYTES,
        "actual_deleted_files": deleted_files,
        "actual_deleted_bytes": deleted_bytes,
        "residual_target_files": EXPECTED_DELETE_FILES - deleted_files,
        "residual_target_bytes": EXPECTED_DELETE_BYTES - deleted_bytes,
        "retained_files": retained["file_count"],
        "retained_bytes": retained["total_bytes"],
        "retained_file_metadata_sha256": retained["file_metadata_sha256"],
        "retained_directory_topology_sha256": retained[
            "directory_topology_sha256"
        ],
        "retained_role_inventory_sha256": retained[
            "role_inventory_sha256"
        ],
        "retained_control_content_sha256": retained[
            "control_content_sha256"
        ],
        "diagnostic_evidence_authority_sha256": manifest[
            "diagnostic_evidence_authority_sha256"
        ],
        "r4_metadata_stat_sha256": r4_authority["metadata_stat_sha256"],
        "older_npz_cache_retained": True,
        "canonical_source_deleted": False,
        "r4_attempt_immutable": True,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "gpu_executions": 0,
        "embedding_generations": 0,
        "model_fitting": 0,
        "prediction_generation": 0,
        "confirmatory_performance_accesses": 0,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }


def _validate_receipt(
    value: Mapping[str, Any], *, manifest_payload: bytes
) -> None:
    try:
        manifest = json.loads(
            manifest_payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except OlderRawRetirementError:
        raise
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_RETIREMENT_RECEIPT_INVALID"
        ) from exc
    if set(value) != RECEIPT_KEYS:
        _fail("OLDER_RAW_RETIREMENT_RECEIPT_INVALID")
    integer_keys = {
        "manifest_bytes", "planned_delete_files", "planned_delete_bytes",
        "actual_deleted_files", "actual_deleted_bytes", "residual_target_files",
        "residual_target_bytes", "retained_files", "retained_bytes",
        "dicom_body_reads", "npz_body_reads", "cloud_requests",
        "qsub_submissions", "gpu_executions", "embedding_generations",
        "model_fitting", "prediction_generation",
        "confirmatory_performance_accesses",
    }
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_r5e_older_raw_retirement_receipt_v1"
        or value.get("status") not in {
            "PASS_OLDER_RAW_DUPLICATES_RETIRED",
            "PARTIAL_OLDER_RAW_RETIREMENT_REQUIRES_OWNER_REVIEW",
        }
        or value.get("manifest_basename") != MANIFEST_BASENAME
        or value.get("manifest_bytes") != len(manifest_payload)
        or value.get("manifest_sha256") != _sha(manifest_payload)
        or not isinstance(manifest, Mapping)
        or value.get("retained_role_inventory_sha256")
        != manifest.get("retained_role_inventory_sha256")
        or value.get("retained_control_content_sha256")
        != manifest.get("retained_control_content_sha256")
        or value.get("diagnostic_evidence_authority_sha256")
        != manifest.get("diagnostic_evidence_authority_sha256")
        or any(type(value.get(key)) is not int for key in integer_keys)
        or value.get("planned_delete_files") != EXPECTED_DELETE_FILES
        or value.get("planned_delete_bytes") != EXPECTED_DELETE_BYTES
        or value.get("actual_deleted_files") < 0
        or value.get("actual_deleted_files") > EXPECTED_DELETE_FILES
        or value.get("actual_deleted_bytes") < 0
        or value.get("actual_deleted_bytes") > EXPECTED_DELETE_BYTES
        or value.get("residual_target_files")
        != EXPECTED_DELETE_FILES - value.get("actual_deleted_files")
        or value.get("residual_target_bytes")
        != EXPECTED_DELETE_BYTES - value.get("actual_deleted_bytes")
        or value.get("r4_metadata_stat_sha256")
        != EXPECTED_R4_METADATA_SHA256
        or value.get("older_npz_cache_retained") is not True
        or value.get("canonical_source_deleted") is not False
        or value.get("r4_attempt_immutable") is not True
        or any(value.get(key) != 0 for key in (
            "dicom_body_reads", "npz_body_reads", "cloud_requests",
            "qsub_submissions", "gpu_executions", "embedding_generations",
            "model_fitting", "prediction_generation",
            "confirmatory_performance_accesses",
        ))
        or value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
        or any(
            not isinstance(value.get(key), str)
            or SHA_RE.fullmatch(value[key]) is None
            for key in (
                "manifest_sha256", "retained_file_metadata_sha256",
                "retained_directory_topology_sha256",
                "retained_role_inventory_sha256",
                "retained_control_content_sha256",
                "diagnostic_evidence_authority_sha256",
                "r4_metadata_stat_sha256",
            )
        )
    ):
        _fail("OLDER_RAW_RETIREMENT_RECEIPT_INVALID")
    full = (
        value["actual_deleted_files"] == EXPECTED_DELETE_FILES
        and value["actual_deleted_bytes"] == EXPECTED_DELETE_BYTES
        and value["retained_files"] == EXPECTED_RETAINED_FILES
        and value["retained_bytes"] == EXPECTED_RETAINED_BYTES
    )
    if (value["status"] == "PASS_OLDER_RAW_DUPLICATES_RETIRED") is not full:
        _fail("OLDER_RAW_RETIREMENT_RECEIPT_INVALID")


def _summary_from_receipt(
    receipt: Mapping[str, Any], *, receipt_payload: bytes
) -> dict[str, Any]:
    value = {
        key: receipt[key]
        for key in SUMMARY_KEYS
        if key not in {
            "artifact_type", "receipt_basename", "receipt_bytes",
            "receipt_sha256",
        }
    }
    value.update({
        "artifact_type": "lvef_c3_r5e_older_raw_retirement_summary_v1",
        "receipt_basename": RECEIPT_BASENAME,
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": _sha(receipt_payload),
    })
    if set(value) != SUMMARY_KEYS:
        _fail("OLDER_RAW_RETIREMENT_SUMMARY_INVALID")
    return value


def _validate_safe_export(payload: bytes | None = None) -> None:
    try:
        policy, _ = analysis_modes.load_policy(SAFE_EXPORT_POLICY_PATH)
        profile = policy["export_profiles"][SAFE_EXPORT_PROFILE]
        if (
            set(profile.get("required_top_level_keys", ())) != SUMMARY_KEYS
            or set(profile.get("allowed_top_level_keys", ())) != SUMMARY_KEYS
            or len(profile.get("required_top_level_keys", ())) != len(SUMMARY_KEYS)
            or len(profile.get("allowed_top_level_keys", ())) != len(SUMMARY_KEYS)
        ):
            raise ValueError("profile mismatch")
        if payload is not None:
            analysis_modes.validate_candidate_bytes(
                payload,
                filename=SUMMARY_BASENAME,
                profile_name=SAFE_EXPORT_PROFILE,
                policy=policy,
            )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_SAFE_EXPORT_POLICY_INVALID"
        ) from exc


def execute_exact_retirement(*, governing_commit: str) -> Mapping[str, Any]:
    """Execute the sole fixed raw-leaf retirement once."""

    if governing_commit != _current_commit():
        _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
    if os.path.lexists(RECEIPT_PATH) or os.path.lexists(SUMMARY_PATH):
        _fail("OLDER_RAW_EXECUTION_ALREADY_CONSUMED")
    manifest, manifest_payload = _read_json(MANIFEST_PATH)
    _validate_manifest(manifest)
    if manifest.get("governing_commit") != governing_commit:
        _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
    _require_sealed_diagnostic_authority(manifest)
    _validate_safe_export()
    rederived = _derive_manifest(str(manifest["governing_commit"]))
    if _canonical(rederived) != manifest_payload:
        _fail("OLDER_RAW_MANIFEST_REDERIVATION_MISMATCH")
    pre_delete_r4_authority = _validate_r4()
    _quiescent()
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    by_batch = {
        batch: [item for item in manifest["targets"] if item["batch_id"] == batch]
        for batch in TARGET_BATCHES
    }
    required_receipt_paths = _required_receipt_relative_paths(
        manifest["targets"]
    )
    target_leaves = _target_leaves(older_root)
    eligible: list[tuple[str, Path]] = []
    for batch, leaf in zip(TARGET_BATCHES, target_leaves, strict=True):
        _validate_leaf_from_manifest(older_root, leaf, by_batch[batch])
        eligible.append((batch, leaf))
    if tuple(batch for batch, _leaf in eligible) != TARGET_BATCHES:
        _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
    final_retained = _retained_evidence_authority(
        older_root,
        required_receipt_relative_paths=required_receipt_paths,
        excluded_roots=target_leaves,
    )
    if (
        final_retained["file_count"] != EXPECTED_RETAINED_FILES
        or final_retained["total_bytes"] != EXPECTED_RETAINED_BYTES
        or final_retained["file_metadata_sha256"]
        != manifest["retained_file_metadata_sha256"]
        or final_retained["directory_topology_sha256"]
        != manifest["retained_directory_topology_sha256"]
        or final_retained["role_inventory"] != manifest["retained_role_inventory"]
        or final_retained["role_inventory_sha256"]
        != manifest["retained_role_inventory_sha256"]
        or final_retained["control_content_sha256"]
        != manifest["retained_control_content_sha256"]
    ):
        _fail("OLDER_RAW_RETAINED_AUTHORITY_INVALID")
    _quiescent()
    for batch, leaf in eligible:
        _validate_leaf_from_manifest(older_root, leaf, by_batch[batch])
    _quiescent()
    if getattr(shutil.rmtree, "avoids_symlink_attacks", False) is not True:
        _fail("OLDER_RAW_DELETION_PRIMITIVE_UNSAFE")
    for _batch, leaf in eligible:
        try:
            shutil.rmtree(leaf)
        except OSError:
            break
    deleted_files = 0
    deleted_bytes = 0
    for item in manifest["targets"]:
        path = older_root / item["relative_path"]
        if not os.path.lexists(path):
            deleted_files += 1
            deleted_bytes += int(item["size_bytes"])
    retained = _retained_evidence_authority(
        older_root,
        required_receipt_relative_paths=required_receipt_paths,
    )
    receipt_tree_authority = _current_older_receipt_tree_authority(
        older_root, manifest["targets"]
    )
    r4_authority = _validate_r4()
    diagnostic_authority = _require_sealed_diagnostic_authority(manifest)
    full = (
        deleted_files == EXPECTED_DELETE_FILES
        and deleted_bytes == EXPECTED_DELETE_BYTES
        and retained["file_count"] == EXPECTED_RETAINED_FILES
        and retained["total_bytes"] == EXPECTED_RETAINED_BYTES
        and retained["file_metadata_sha256"]
        == manifest["retained_file_metadata_sha256"]
        and retained["directory_topology_sha256"]
        == manifest["retained_directory_topology_sha256"]
        and retained["role_inventory"] == manifest["retained_role_inventory"]
        and retained["role_inventory_sha256"]
        == manifest["retained_role_inventory_sha256"]
        and retained["control_content_sha256"]
        == manifest["retained_control_content_sha256"]
        and receipt_tree_authority
        == manifest["older_receipt_tree_authority"]
        and diagnostic_authority == manifest["diagnostic_evidence_authority"]
        and r4_authority == pre_delete_r4_authority
        and r4_authority == manifest["r4_authority"]
        and all(not os.path.lexists(path) for path in _target_leaves(older_root))
    )
    status = (
        "PASS_OLDER_RAW_DUPLICATES_RETIRED"
        if full
        else "PARTIAL_OLDER_RAW_RETIREMENT_REQUIRES_OWNER_REVIEW"
    )
    receipt = _post_receipt(
        manifest_payload=manifest_payload,
        manifest=manifest,
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        retained=retained,
        r4_authority=r4_authority,
        status=status,
    )
    receipt_payload = _canonical(receipt)
    _validate_receipt(receipt, manifest_payload=manifest_payload)
    summary = _summary_from_receipt(receipt, receipt_payload=receipt_payload)
    summary_payload = _canonical(summary)
    _validate_safe_export(summary_payload)
    _write_new(RECEIPT_PATH, receipt_payload)
    _write_new(SUMMARY_PATH, summary_payload)
    return {
        "status": status,
        "actual_deleted_files": deleted_files,
        "actual_deleted_bytes": deleted_bytes,
        "retained_files": retained["file_count"],
        "retained_bytes": retained["total_bytes"],
        "auxiliary_receipt_files": receipt_tree_authority[
            "auxiliary_receipt_files"
        ],
        "auxiliary_receipt_bytes": receipt_tree_authority[
            "auxiliary_receipt_bytes"
        ],
        "diagnostic_root_count": diagnostic_authority[
            "diagnostic_root_count"
        ],
        "diagnostic_file_count": diagnostic_authority[
            "diagnostic_file_count"
        ],
        "diagnostic_directory_count": diagnostic_authority[
            "diagnostic_directory_count"
        ],
        "diagnostic_total_bytes": diagnostic_authority[
            "diagnostic_total_bytes"
        ],
        "diagnostics_retained": diagnostic_authority[
            "diagnostics_retained"
        ],
        "diagnostic_body_reads": diagnostic_authority["body_reads"],
        "receipt_basename": RECEIPT_BASENAME,
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": _sha(receipt_payload),
        "summary_basename": SUMMARY_BASENAME,
        "summary_bytes": len(summary_payload),
        "summary_sha256": _sha(summary_payload),
    }


def validate_retirement_receipt_authority(
    *, expected_governing_commit: str
) -> Mapping[str, Any]:
    manifest, manifest_payload = _read_json(MANIFEST_PATH)
    _validate_manifest(manifest)
    receipt, receipt_payload = _read_json(RECEIPT_PATH)
    _validate_receipt(receipt, manifest_payload=manifest_payload)
    summary, summary_payload = _read_json(SUMMARY_PATH)
    expected_summary = _summary_from_receipt(
        receipt, receipt_payload=receipt_payload
    )
    current_pre_cleanup_capacity = _pre_cleanup_capacity_authority()
    if (
        receipt.get("status") != "PASS_OLDER_RAW_DUPLICATES_RETIRED"
        or receipt.get("governing_commit") != expected_governing_commit
        or manifest.get("governing_commit") != expected_governing_commit
        or summary != expected_summary
        or summary_payload != _canonical(expected_summary)
        or current_pre_cleanup_capacity
        != manifest.get("pre_cleanup_capacity_authority")
    ):
        _fail("OLDER_RAW_RETIREMENT_RECEIPT_INVALID")
    diagnostics = manifest["diagnostic_evidence_authority"]
    return {
        "status": receipt["status"],
        "receipt_basename": RECEIPT_BASENAME,
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": _sha(receipt_payload),
        "actual_deleted_files": receipt["actual_deleted_files"],
        "actual_deleted_bytes": receipt["actual_deleted_bytes"],
        "auxiliary_receipt_files": manifest[
            "older_receipt_tree_authority"
        ]["auxiliary_receipt_files"],
        "auxiliary_receipt_bytes": manifest[
            "older_receipt_tree_authority"
        ]["auxiliary_receipt_bytes"],
        "retained_file_metadata_sha256": receipt[
            "retained_file_metadata_sha256"
        ],
        "retained_directory_topology_sha256": receipt[
            "retained_directory_topology_sha256"
        ],
        "retained_role_inventory_sha256": receipt[
            "retained_role_inventory_sha256"
        ],
        "retained_control_content_sha256": receipt[
            "retained_control_content_sha256"
        ],
        "diagnostic_evidence_authority_sha256": receipt[
            "diagnostic_evidence_authority_sha256"
        ],
        "diagnostic_root_count": diagnostics["diagnostic_root_count"],
        "diagnostic_file_count": diagnostics["diagnostic_file_count"],
        "diagnostic_directory_count": diagnostics[
            "diagnostic_directory_count"
        ],
        "diagnostic_total_bytes": diagnostics["diagnostic_total_bytes"],
        "diagnostics_retained": diagnostics["diagnostics_retained"],
        "diagnostic_body_reads": diagnostics["body_reads"],
        "pre_cleanup_capacity_authority": dict(
            manifest["pre_cleanup_capacity_authority"]
        ),
    }


def validate_retired_state(*, expected_governing_commit: str) -> Mapping[str, Any]:
    authority = validate_retirement_receipt_authority(
        expected_governing_commit=expected_governing_commit
    )
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    manifest, _manifest_payload = _read_json(MANIFEST_PATH)
    _validate_manifest(manifest)
    retained = _retained_evidence_authority(
        older_root,
        required_receipt_relative_paths=_required_receipt_relative_paths(
            manifest["targets"]
        ),
    )
    receipt_tree_authority = _current_older_receipt_tree_authority(
        older_root, manifest["targets"]
    )
    _validate_r4()
    diagnostic_authority = _require_sealed_diagnostic_authority(manifest)
    if (
        retained["file_count"] != EXPECTED_RETAINED_FILES
        or retained["total_bytes"] != EXPECTED_RETAINED_BYTES
        or retained["file_metadata_sha256"]
        != authority["retained_file_metadata_sha256"]
        or retained["directory_topology_sha256"]
        != authority["retained_directory_topology_sha256"]
        or retained["role_inventory_sha256"]
        != authority["retained_role_inventory_sha256"]
        or retained["control_content_sha256"]
        != authority["retained_control_content_sha256"]
        or core.canonical_json_sha256(diagnostic_authority)
        != authority["diagnostic_evidence_authority_sha256"]
        or receipt_tree_authority
        != manifest["older_receipt_tree_authority"]
        or any(os.path.lexists(path) for path in _target_leaves(older_root))
    ):
        _fail("OLDER_RAW_RETAINED_AUTHORITY_INVALID")
    return authority


def dry_run() -> Mapping[str, Any]:
    if os.path.lexists(RECEIPT_PATH):
        receipt = validate_retired_state(
            expected_governing_commit=str(_read_json(RECEIPT_PATH)[0]["governing_commit"])
        )
        return {
            **receipt,
            "additional_proposed_delete_files": 0,
            "additional_proposed_delete_bytes": 0,
        }
    manifest, _ = _read_json(MANIFEST_PATH)
    _validate_manifest(manifest)
    return {
        "status": "DRY_RUN_EXACT_OLDER_RAW_RETIREMENT",
        "additional_proposed_delete_files": EXPECTED_DELETE_FILES,
        "additional_proposed_delete_bytes": EXPECTED_DELETE_BYTES,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-manifest", action="store_true")
    modes.add_argument("--execute-exact-older-raw-retirement", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    parser.add_argument("--governing-commit")
    args = parser.parse_args(argv)
    try:
        if args.prepare_manifest:
            if not args.governing_commit:
                _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
            result = prepare_retirement_manifest(
                governing_commit=args.governing_commit
            )
        elif args.execute_exact_older_raw_retirement:
            if not args.governing_commit:
                _fail("OLDER_RAW_GOVERNING_COMMIT_INVALID")
            result = execute_exact_retirement(
                governing_commit=args.governing_commit
            )
        else:
            if args.governing_commit is not None:
                _fail("OLDER_RAW_CALLER_OVERRIDE_PROHIBITED")
            result = dry_run()
        for key, value in result.items():
            if key in {"manifest_basename", "receipt_basename", "summary_basename"} or not isinstance(value, (dict, list)):
                print(f"{key.upper()}={value}")
        return 0
    except OlderRawRetirementError as exc:
        code = exc.code if SAFE_CODE_RE.fullmatch(exc.code) else "OLDER_RAW_UNCLASSIFIED_FAILURE"
        print("OLDER_RAW_RETIREMENT_STATUS=FAILED")
        if (
            isinstance(exc, OlderRawDownloadManifestError)
            and exc.role in VERIFIED_DOWNLOAD_MANIFEST_ROLES
        ):
            print(f"OLDER_RAW_RETIREMENT_FAILURE_ROLE={exc.role}")
        print(f"OLDER_RAW_RETIREMENT_FAILURE_CODE={code}")
        return 78
    except BaseException:
        print("OLDER_RAW_RETIREMENT_STATUS=FAILED")
        print("OLDER_RAW_RETIREMENT_FAILURE_CODE=OLDER_RAW_UNCLASSIFIED_FAILURE")
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
