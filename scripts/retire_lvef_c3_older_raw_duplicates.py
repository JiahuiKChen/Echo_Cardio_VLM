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
RETAINED_ROLE_NAMES = (
    "RAW_DICOM_PAYLOAD",
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
EXPECTED_DIAGNOSTIC_FILES = 4
EXPECTED_DIAGNOSTIC_DIRECTORIES = 4
EXPECTED_DIAGNOSTIC_BYTES = 25_175
DIAGNOSTIC_OBSERVATION_BASENAME = "technical_replay_observation.restricted.json"
DIAGNOSTIC_OBSERVATION_BYTES = 2_788
DIAGNOSTIC_OBSERVATION_SHA256 = (
    "1e4ba03360aa4f4e846a98fd3587969ab12e355ab51156b1debda2e0577fb8e4"
)
DIAGNOSTIC_COMPARISON_BASENAME = "technical_comparison.restricted.json"
DIAGNOSTIC_COMPARISON_BYTES = 4_147
DIAGNOSTIC_COMPARISON_SHA256 = (
    "cc60dd119e9193961ae29debd12e1d30c0db1b34ab52ab68b03278b58fe87a97"
)
DIAGNOSTIC_AGGREGATE_BASENAME = "one_object_replay.aggregate_safe.json"
DIAGNOSTIC_AGGREGATE_BYTES = 2_619
DIAGNOSTIC_AGGREGATE_SHA256 = (
    "0161b36848ccac7be294652aaecbf9bc2e830b86736739e0c0c37019cc24f04b"
)
DIAGNOSTIC_NPZ_BYTES = 15_621
DIAGNOSTIC_NPZ_SHA256 = (
    "1a6fb43fe9f666f659f5b88541fffee46cf50fc3e3c823234c28713e581e6a17"
)
DIAGNOSTIC_AUTHORITY_KEYS = frozenset({
    "status", "file_count", "directory_count", "total_bytes",
    "file_metadata_sha256", "directory_topology_sha256",
    "observation_basename", "observation_bytes", "observation_sha256",
    "comparison_basename", "comparison_bytes", "comparison_sha256",
    "aggregate_basename", "aggregate_bytes", "aggregate_sha256",
    "npz_relative_path", "npz_bytes", "npz_sealed_sha256",
    "source_object_key", "source_local_sha256",
})
MAXIMUM_CONTROL_BYTES = 512 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
QSTAT = Path("/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qstat")
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


def _retained_role(relative_path: str) -> str:
    """Classify one retained file by closed topology/name rules, never size."""

    path = PurePosixPath(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    if name.endswith(".dcm"):
        return "RAW_DICOM_PAYLOAD"
    if name.endswith(".npz"):
        if "clip_embeddings" in name:
            return "CLIP_EMBEDDINGS"
        if "study_embeddings" in name:
            return "STUDY_EMBEDDINGS"
        if "extracted_cache" in parts or "clips" in parts:
            return "EXTRACTED_NPZ_CACHE"
        return "UNCLASSIFIED"
    if (
        len(parts) >= 3
        and parts[0] == "raw"
        and "receipts" in parts
        and name.endswith(".verification.json")
    ):
        return "DOWNLOAD_VERIFICATION_RECEIPTS"
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
    root: Path, *, excluded_roots: Sequence[Path] = ()
) -> dict[str, Any]:
    """Seal every retained role plus exact control content and metadata."""

    first = _metadata_rows(root, excluded_roots=excluded_roots)
    files, directories = first
    counts = {
        role: {"role": role, "file_count": 0, "total_bytes": 0}
        for role in RETAINED_ROLE_NAMES
    }
    control_rows: list[tuple[str, str, int, str]] = []
    body_roles = {
        "RAW_DICOM_PAYLOAD", "EXTRACTED_NPZ_CACHE",
        "CLIP_EMBEDDINGS", "STUDY_EMBEDDINGS",
    }
    for row in files:
        relative = str(row[0])
        role = _retained_role(relative)
        counts[role]["file_count"] += 1
        counts[role]["total_bytes"] += int(row[5])
        if role not in body_roles:
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


def _validate_attempt_batch(
    attempt_root: Path, bundle: PlanBundle, batch_id: str
) -> tuple[dict[str, dict[str, Any]], str, str, str]:
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
    expected_object_names = {f"{key}.dcm" for key in expected}
    expected_receipt_names = {f"{key}.verification.json" for key in expected}
    try:
        object_names = {entry.name for entry in os.scandir(objects_root)}
        receipt_names = {entry.name for entry in os.scandir(receipts_root)}
        partial_names = {entry.name for entry in os.scandir(partials_root)} if partials_root.is_dir() else set()
    except OSError as exc:
        raise OlderRawRetirementError("OLDER_RAW_LEAF_INVENTORY_INVALID") from exc
    if object_names != expected_object_names or receipt_names != expected_receipt_names or partial_names:
        _fail("OLDER_RAW_LEAF_INVENTORY_INVALID")
    try:
        mount_authority = r3e.validate_current_mount_authority(
            attempt_root, objects_root
        )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_CURRENT_MOUNT_AUTHORITY_INVALID"
        ) from exc
    root_device = os.lstat(attempt_root).st_dev
    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(expected):
        row = expected[key]
        path = objects_root / f"{key}.dcm"
        receipt_path = receipts_root / f"{key}.verification.json"
        info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_dev != root_device
            or info.st_size != int(row["size_bytes"])
        ):
            _fail("OLDER_RAW_OBJECT_IDENTITY_INVALID")
        receipt, receipt_payload = _read_json(receipt_path, maximum=r3e.MAXIMUM_RECEIPT_BYTES)
        receipt_sha = _sha(receipt_payload)
        if receipt_map.get(key) != receipt_sha:
            _fail("OLDER_RAW_DOWNLOAD_RECEIPT_INVALID")
        local_sha = _receipt_authority(
            receipt,
            row=row,
            info=info,
            mount_authority=mount_authority,
            authority=bundle.authority,
        )
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
    return entries, manifest_sha, _sha(ledger_payload), schema.role


def _diagnostic_root() -> Path:
    try:
        candidates = []
        for entry in os.scandir(OWNER_PRIVATE_ROOT):
            if r3e.DIAGNOSTIC_NAME_RE.fullmatch(entry.name) is None:
                continue
            info = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or not stat.S_ISDIR(info.st_mode):
                _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
            candidates.append(Path(entry.path))
    except OSError as exc:
        raise OlderRawRetirementError("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED") from exc
    if len(candidates) != 1:
        _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
    return candidates[0]


def _diagnostic_evidence_authority(
    diagnostic_root: Path, preflight: Any
) -> dict[str, Any]:
    """Bind the four preserved R3E artifacts without opening the NPZ body."""

    metadata = _stable_metadata_authority(diagnostic_root)
    if (
        metadata["file_count"] != EXPECTED_DIAGNOSTIC_FILES
        or metadata["directory_count"] != EXPECTED_DIAGNOSTIC_DIRECTORIES
        or metadata["total_bytes"] != EXPECTED_DIAGNOSTIC_BYTES
    ):
        _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
    json_specs = (
        (
            DIAGNOSTIC_OBSERVATION_BASENAME,
            DIAGNOSTIC_OBSERVATION_BYTES,
            DIAGNOSTIC_OBSERVATION_SHA256,
        ),
        (
            DIAGNOSTIC_COMPARISON_BASENAME,
            DIAGNOSTIC_COMPARISON_BYTES,
            DIAGNOSTIC_COMPARISON_SHA256,
        ),
        (
            DIAGNOSTIC_AGGREGATE_BASENAME,
            DIAGNOSTIC_AGGREGATE_BYTES,
            DIAGNOSTIC_AGGREGATE_SHA256,
        ),
    )
    documents: dict[str, tuple[dict[str, Any], bytes]] = {}
    for basename, expected_bytes, expected_sha in json_specs:
        document, payload = _read_json(
            diagnostic_root / basename, maximum=r3e.MAXIMUM_RECEIPT_BYTES
        )
        if len(payload) != expected_bytes or _sha(payload) != expected_sha:
            _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
        documents[basename] = (document, payload)
    aggregate = documents[DIAGNOSTIC_AGGREGATE_BASENAME][0]
    npz_candidates: list[tuple[Path, os.stat_result]] = []
    try:
        for current, names, files in os.walk(
            diagnostic_root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            names[:] = sorted(names)
            for name in sorted(files):
                path = current_path / name
                if path.suffix != ".npz":
                    continue
                npz_candidates.append((path, os.lstat(path)))
    except OSError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED"
        ) from exc
    if len(npz_candidates) != 1:
        _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
    npz_path, npz_info = npz_candidates[0]
    relative = npz_path.relative_to(diagnostic_root)
    match = re.fullmatch(
        r"repaired_extraction/clips/([0-9a-f]{2})/([0-9a-f]{64})\.npz",
        relative.as_posix(),
    )
    source_key = str(preflight.planned_object.get("source_object_key"))
    local_sha = str(preflight.local_sha256)
    if (
        match is None
        or match.group(1) != match.group(2)[:2]
        or not stat.S_ISREG(npz_info.st_mode)
        or stat.S_ISLNK(npz_info.st_mode)
        or npz_info.st_uid != os.geteuid()
        or stat.S_IMODE(npz_info.st_mode) != 0o600
        or npz_info.st_nlink != 1
        or npz_info.st_size != DIAGNOSTIC_NPZ_BYTES
        or aggregate.get("diagnostic_npz_bytes") != DIAGNOSTIC_NPZ_BYTES
        or aggregate.get("diagnostic_npz_sha256") != DIAGNOSTIC_NPZ_SHA256
        or SHA_RE.fullmatch(source_key) is None
        or SHA_RE.fullmatch(local_sha) is None
    ):
        _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
    return {
        "status": "PASS_R3E_DIAGNOSTIC_EVIDENCE_RETAINED",
        "file_count": metadata["file_count"],
        "directory_count": metadata["directory_count"],
        "total_bytes": metadata["total_bytes"],
        "file_metadata_sha256": metadata["file_metadata_sha256"],
        "directory_topology_sha256": metadata["directory_topology_sha256"],
        "observation_basename": DIAGNOSTIC_OBSERVATION_BASENAME,
        "observation_bytes": DIAGNOSTIC_OBSERVATION_BYTES,
        "observation_sha256": DIAGNOSTIC_OBSERVATION_SHA256,
        "comparison_basename": DIAGNOSTIC_COMPARISON_BASENAME,
        "comparison_bytes": DIAGNOSTIC_COMPARISON_BYTES,
        "comparison_sha256": DIAGNOSTIC_COMPARISON_SHA256,
        "aggregate_basename": DIAGNOSTIC_AGGREGATE_BASENAME,
        "aggregate_bytes": DIAGNOSTIC_AGGREGATE_BYTES,
        "aggregate_sha256": DIAGNOSTIC_AGGREGATE_SHA256,
        "npz_relative_path": relative.as_posix(),
        "npz_bytes": DIAGNOSTIC_NPZ_BYTES,
        "npz_sealed_sha256": DIAGNOSTIC_NPZ_SHA256,
        "source_object_key": source_key,
        "source_local_sha256": local_sha,
    }


def _target_leaves(attempt_root: Path) -> tuple[Path, ...]:
    return tuple(attempt_root / "raw" / batch / "objects" for batch in TARGET_BATCHES)


def _pre_cleanup_capacity_authority(
    governing_commit: str,
) -> dict[str, Any]:
    try:
        captured = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=(
                OWNER_PRIVATE_ROOT
                / capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
            ),
            aggregate_summary_path=(
                OWNER_PRIVATE_ROOT
                / capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
            ),
            expected_governing_commit=governing_commit,
        )
        captured = capacity.validate_production_dynamic_successor_capacity_capture(
            captured, expected_governing_commit=governing_commit
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
    pre_cleanup_capacity = _pre_cleanup_capacity_authority(governing_commit)
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
    all_older: dict[str, dict[str, Any]] = {}
    all_r4: dict[str, dict[str, Any]] = {}
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
        ) = _validate_attempt_batch(older_root, older_bundle, batch_id)
        (
            r4_entries,
            r4_manifest_sha,
            r4_ledger_sha,
            r4_manifest_role,
        ) = _validate_attempt_batch(r4_root, r4_bundle, batch_id)
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
        all_older.update(older_entries)
        all_r4.update(r4_entries)
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
    diagnostic = _diagnostic_root()
    try:
        preflight = r3e.run_preflight(
            governing_commit=governing_commit, diagnostic_root=diagnostic
        )
    except Exception as exc:
        raise OlderRawRetirementError("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED") from exc
    diagnostic_key = str(preflight.planned_object.get("source_object_key"))
    if (
        diagnostic_key not in all_older
        or diagnostic_key not in all_r4
        or preflight.local_sha256 != all_older[diagnostic_key]["local_sha256"]
        or preflight.local_sha256 != all_r4[diagnostic_key]["local_sha256"]
    ):
        _fail("OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED")
    diagnostic_authority = _diagnostic_evidence_authority(
        diagnostic, preflight
    )
    leaves = _target_leaves(older_root)
    retained = _retained_evidence_authority(
        older_root, excluded_roots=leaves
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
        "batch_authorities",
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
        or not isinstance(diagnostic_authority, Mapping)
        or set(diagnostic_authority) != DIAGNOSTIC_AUTHORITY_KEYS
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
        or diagnostic_authority.get("status")
        != "PASS_R3E_DIAGNOSTIC_EVIDENCE_RETAINED"
        or diagnostic_authority.get("file_count") != EXPECTED_DIAGNOSTIC_FILES
        or diagnostic_authority.get("directory_count")
        != EXPECTED_DIAGNOSTIC_DIRECTORIES
        or diagnostic_authority.get("total_bytes") != EXPECTED_DIAGNOSTIC_BYTES
        or diagnostic_authority.get("observation_basename")
        != DIAGNOSTIC_OBSERVATION_BASENAME
        or diagnostic_authority.get("observation_bytes")
        != DIAGNOSTIC_OBSERVATION_BYTES
        or diagnostic_authority.get("observation_sha256")
        != DIAGNOSTIC_OBSERVATION_SHA256
        or diagnostic_authority.get("comparison_basename")
        != DIAGNOSTIC_COMPARISON_BASENAME
        or diagnostic_authority.get("comparison_bytes")
        != DIAGNOSTIC_COMPARISON_BYTES
        or diagnostic_authority.get("comparison_sha256")
        != DIAGNOSTIC_COMPARISON_SHA256
        or diagnostic_authority.get("aggregate_basename")
        != DIAGNOSTIC_AGGREGATE_BASENAME
        or diagnostic_authority.get("aggregate_bytes")
        != DIAGNOSTIC_AGGREGATE_BYTES
        or diagnostic_authority.get("aggregate_sha256")
        != DIAGNOSTIC_AGGREGATE_SHA256
        or diagnostic_authority.get("npz_bytes") != DIAGNOSTIC_NPZ_BYTES
        or diagnostic_authority.get("npz_sealed_sha256")
        != DIAGNOSTIC_NPZ_SHA256
        or any(
            not isinstance(diagnostic_authority.get(key), str)
            or SHA_RE.fullmatch(diagnostic_authority[key]) is None
            for key in (
                "file_metadata_sha256", "directory_topology_sha256",
                "source_object_key", "source_local_sha256",
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
            or relative != f"raw/{batch_id}/objects/{source_key}.dcm"
            or r4_relative != relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
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

    try:
        older_info = os.lstat(older_root)
        if (
            stat.S_ISLNK(older_info.st_mode)
            or not stat.S_ISDIR(older_info.st_mode)
            or older_info.st_uid != os.geteuid()
        ):
            _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
        current = older_root
        for component in leaf.relative_to(older_root).parts:
            current = current / component
            component_info = os.lstat(current)
            if (
                stat.S_ISLNK(component_info.st_mode)
                or not stat.S_ISDIR(component_info.st_mode)
                or component_info.st_uid != os.geteuid()
                or component_info.st_dev != older_info.st_dev
                or os.path.ismount(current)
            ):
                _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
        leaf_info = os.lstat(leaf)
        if (
            stat.S_ISLNK(leaf_info.st_mode)
            or not stat.S_ISDIR(leaf_info.st_mode)
            or leaf_info.st_uid != os.geteuid()
            or leaf_info.st_dev != older_info.st_dev
            or stat.S_IMODE(leaf_info.st_mode) not in {0o700, 0o2700}
            or os.path.ismount(leaf)
        ):
            _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
        names: list[str] = []
        metadata: list[dict[str, int | str]] = []
        total_bytes = 0
        with os.scandir(leaf) as directory:
            for entry in directory:
                info = entry.stat(follow_symlinks=False)
                if (
                    entry.name.startswith(".nfs")
                    or ".partial" in entry.name
                    or RAW_OBJECT_BASENAME_RE.fullmatch(entry.name) is None
                    or entry.is_symlink()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_dev != leaf_info.st_dev
                ):
                    _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
                names.append(entry.name)
                total_bytes += info.st_size
                metadata.append({
                    "name": entry.name,
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "mode": stat.S_IMODE(info.st_mode),
                    "uid": info.st_uid,
                    "gid": info.st_gid,
                    "nlink": info.st_nlink,
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "ctime_ns": info.st_ctime_ns,
                })
    except OlderRawRetirementError:
        raise
    except OSError as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_LEAF_AUTHORITY_INVALID"
        ) from exc
    names.sort()
    metadata.sort(key=lambda row: str(row["name"]))
    if (
        len(names) != EXPECTED_BATCH_COUNTS[batch_id]
        or total_bytes != EXPECTED_BATCH_BYTES[batch_id]
    ):
        _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
    return {
        "batch_id": batch_id,
        "file_count": len(names),
        "total_bytes": total_bytes,
        "directory_count": 1,
        "relative_name_projection_sha256": core.canonical_json_sha256(names),
        "metadata_projection_sha256": core.canonical_json_sha256(metadata),
        "leaf_device": leaf_info.st_dev,
        "leaf_inode": leaf_info.st_ino,
        "leaf_mode": stat.S_IMODE(leaf_info.st_mode),
        "leaf_uid": leaf_info.st_uid,
        "leaf_gid": leaf_info.st_gid,
        "leaf_nlink": leaf_info.st_nlink,
        "leaf_size": leaf_info.st_size,
        "leaf_mtime_ns": leaf_info.st_mtime_ns,
        "leaf_ctime_ns": leaf_info.st_ctime_ns,
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
    target_leaves = tuple(Path(os.path.abspath(path)) for path in _target_leaves(older_root))
    tokens = _candidate_tokens(target_leaves)
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
        if not candidate and not unknown:
            # A clear nonmatching command snapshot is sufficient; optional
            # per-process procfs completeness is deliberately not required.
            continue
        disposition = _inspect_proc_references(
            process_id,
            target_leaves=target_leaves,
            required=True,
        )
        if disposition == "PROCESS_EXITED_DURING_SCAN_NONBLOCKING":
            counts["vanished_processes"] += 1
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
    expected = {
        Path(item["relative_path"]).name: int(item["size_bytes"])
        for item in entries
    }
    try:
        info = os.lstat(leaf)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_dev != os.lstat(older_root).st_dev
            or os.path.ismount(leaf)
        ):
            _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
        observed: dict[str, int] = {}
        for entry in os.scandir(leaf):
            child = Path(entry.path)
            child_info = entry.stat(follow_symlinks=False)
            if (
                entry.name.startswith(".nfs")
                or ".partial" in entry.name
                or entry.is_symlink()
                or not stat.S_ISREG(child_info.st_mode)
                or child_info.st_uid != os.geteuid()
                or child_info.st_nlink != 1
                or stat.S_IMODE(child_info.st_mode) != 0o600
                or child_info.st_dev != info.st_dev
            ):
                _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
            observed[entry.name] = child_info.st_size
    except OlderRawRetirementError:
        raise
    except OSError as exc:
        raise OlderRawRetirementError("OLDER_RAW_LEAF_AUTHORITY_INVALID") from exc
    if observed != expected:
        _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")


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
    _validate_safe_export()
    rederived = _derive_manifest(str(manifest["governing_commit"]))
    if _canonical(rederived) != manifest_payload:
        _fail("OLDER_RAW_MANIFEST_REDERIVATION_MISMATCH")
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    by_batch = {
        batch: [item for item in manifest["targets"] if item["batch_id"] == batch]
        for batch in TARGET_BATCHES
    }
    eligible: list[tuple[str, Path]] = []
    for batch, leaf in zip(TARGET_BATCHES, _target_leaves(older_root), strict=True):
        _validate_leaf_from_manifest(older_root, leaf, by_batch[batch])
        eligible.append((batch, leaf))
    if tuple(batch for batch, _leaf in eligible) != TARGET_BATCHES:
        _fail("OLDER_RAW_LEAF_AUTHORITY_INVALID")
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
    retained = _retained_evidence_authority(older_root)
    diagnostic_root = _diagnostic_root()
    try:
        diagnostic_preflight = r3e.run_preflight(
            governing_commit=str(manifest["governing_commit"]),
            diagnostic_root=diagnostic_root,
        )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED"
        ) from exc
    diagnostic_authority = _diagnostic_evidence_authority(
        diagnostic_root, diagnostic_preflight
    )
    r4_authority = _validate_r4()
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
        and diagnostic_authority == manifest["diagnostic_evidence_authority"]
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
    current_pre_cleanup_capacity = _pre_cleanup_capacity_authority(
        expected_governing_commit
    )
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
    return {
        "status": receipt["status"],
        "receipt_basename": RECEIPT_BASENAME,
        "receipt_bytes": len(receipt_payload),
        "receipt_sha256": _sha(receipt_payload),
        "actual_deleted_files": receipt["actual_deleted_files"],
        "actual_deleted_bytes": receipt["actual_deleted_bytes"],
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
        "pre_cleanup_capacity_authority": dict(
            manifest["pre_cleanup_capacity_authority"]
        ),
    }


def validate_retired_state(*, expected_governing_commit: str) -> Mapping[str, Any]:
    authority = validate_retirement_receipt_authority(
        expected_governing_commit=expected_governing_commit
    )
    older_root = PRODUCTION_ROOT / "attempts" / OLDER_ATTEMPT_ID
    retained = _retained_evidence_authority(older_root)
    diagnostic_root = _diagnostic_root()
    try:
        diagnostic_preflight = r3e.run_preflight(
            governing_commit=expected_governing_commit,
            diagnostic_root=diagnostic_root,
        )
    except Exception as exc:
        raise OlderRawRetirementError(
            "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED"
        ) from exc
    diagnostic_authority = _diagnostic_evidence_authority(
        diagnostic_root, diagnostic_preflight
    )
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
        or any(os.path.lexists(path) for path in _target_leaves(older_root))
    ):
        _fail("OLDER_RAW_RETAINED_AUTHORITY_INVALID")
    _validate_r4()
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
