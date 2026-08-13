#!/usr/bin/env python3
"""CPU-only preservation recovery for the completed exact-five canary.

This module has one fixed scientific input.  It never accepts a manifest,
run-root, study, object, DICOM, extraction, or embedding path from a caller.
The original failed scientific run remains immutable; recovery provenance and
the recovery terminal result are written under a separate owner-private root.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Final, Mapping, Sequence

# This process is preservation-only. Mask accelerators before any deferred
# import of NumPy, Torch, or an existing scientific/preservation module.
os.environ["CUDA_VISIBLE_DEVICES"] = ""


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

ORIGINAL_SCIENTIFIC_COMMIT: Final = (
    "e5ca24c4899a16952eedb04f4d94c4d87453587c"
)
ORIGINAL_JOB_ID: Final = "7168202"
MANIFEST_FILE_SHA256: Final = (
    "5907a1ac53b050362eee15c0f0ede095e3c50bb0231246738a597eeca8ada8b8"
)
MANIFEST_SEMANTIC_SHA256: Final = (
    "cff8311f12621cdea91a183cba847ad6c0a3d12de820264c85f4f5d167686276"
)
MANIFEST_STUDIES: Final = 5
MANIFEST_SUBJECTS: Final = 5
DECLARED_OBJECTS: Final = 380
DECLARED_EXPECTED_BYTES: Final = 1_772_100_274
ORIGINAL_OBSERVATION_BYTES: Final = 740
ORIGINAL_OBSERVATION_SHA256: Final = (
    "03da0676a6f0ec60614a5664b38b935934291ee5f5259a27b6997cbcf2b85ee7"
)
ORIGINAL_TERMINAL_BYTES: Final = 1_033
ORIGINAL_TERMINAL_SHA256: Final = (
    "57e9a0c25fbb1e56758678077adfdbb08ee1991f7287afcefde0317ef7f7dc14"
)
ENVIRONMENT_AUTHORITY_COMMIT: Final = (
    "0bfcba9973fa6592ca75aa23c6cf0e42d432c5cb"
)
ENVIRONMENT_RECEIPT_SHA256: Final = (
    "182a03baa5b66b103fe80a3d4b4f3ab2941b7c5e6abf368d5f9b49781a8b8683"
)
ENVIRONMENT_RECEIPT_BYTES: Final = 6_026

PRODUCTION_ROOT: Final = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
OWNER_PRIVATE_ROOT: Final = PRODUCTION_ROOT / "owner_private"
MANIFEST_PATH: Final = OWNER_PRIVATE_ROOT / "exact_five_manifest.restricted.json"
RUN_ID: Final = "lvef_c3_minimal_5907a1ac53b05036_e5ca24c4"
RUN_ROOT: Final = PRODUCTION_ROOT / "minimal_canary_runs" / RUN_ID
RECOVERY_ROOT: Final = OWNER_PRIVATE_ROOT / f"preservation_recovery_{RUN_ID}"
RECOVERY_AUTHORITY_PATH: Final = (
    RECOVERY_ROOT / "preservation_recovery_authority.restricted.json"
)
RECOVERY_CLAIM_PATH: Final = (
    RECOVERY_ROOT / "preservation_recovery_submission_claim.restricted.json"
)
RECOVERY_SUBMISSION_PATH: Final = (
    RECOVERY_ROOT / "preservation_recovery_submission.aggregate_safe.json"
)
RECOVERY_TERMINAL_PATH: Final = (
    RECOVERY_ROOT / "preservation_recovery_terminal.aggregate_safe.json"
)
RECOVERY_QSUB_STDOUT_PATH: Final = RECOVERY_ROOT / "qsub.stdout.restricted"
RECOVERY_QSUB_STDERR_PATH: Final = RECOVERY_ROOT / "qsub.stderr.restricted"
RECOVERY_QSUB_STATUS_PATH: Final = RECOVERY_ROOT / "qsub.exit_status.restricted"

STAGE_LEDGER_PATH: Final = RUN_ROOT / "minimal_canary_stage_ledger.restricted.json"
ORIGINAL_TERMINAL_PATH: Final = (
    RUN_ROOT / "minimal_canary_terminal_receipt.aggregate_safe.json"
)
PLAN_PATH: Final = RUN_ROOT / "minimal_batch_plan.restricted.json"
ATTEMPT_ROOT: Final = RUN_ROOT / "attempts" / RUN_ID
BATCH_ID: Final = "c3_batch_000"
BATCH_ROOT: Final = ATTEMPT_ROOT / "batches" / BATCH_ID
RAW_BATCH_ROOT: Final = ATTEMPT_ROOT / "raw" / BATCH_ID
EXTRACTION_ROOT: Final = (
    ATTEMPT_ROOT / "extracted_cache" / BATCH_ID / "dicom_extraction"
)
ECHOPRIME_ROOT: Final = BATCH_ROOT / "echoprime"
PRESERVATION_ROOT: Final = BATCH_ROOT / "preservation"
PRESERVATION_MANIFEST_PATH: Final = (
    PRESERVATION_ROOT / "batch_preservation_manifest.restricted.tsv"
)
PRESERVATION_RECEIPT_PATH: Final = (
    PRESERVATION_ROOT / "batch_preservation_receipt.restricted.json"
)
POOLING_LEDGER_PATH: Final = BATCH_ROOT / "pooling_resume_ledger.restricted.json"
FINALIZATION_PATH: Final = (
    RUN_ROOT / "minimal_canary_finalization_receipt.aggregate_safe.json"
)

RUNNER_PATH: Final = SCRIPT_ROOT / "scc_recover_lvef_c3_minimal_canary_preservation.sh"
ORIGINAL_SCIENTIFIC_RUNNER_PATH: Final = (
    SCRIPT_ROOT / "scc_run_lvef_c3_minimal_canary.sh"
)
PRESERVATION_SCRIPT_PATH: Final = SCRIPT_ROOT / "preserve_lvef_c3_production_batch.py"
FINALIZER_SCRIPT_PATH: Final = SCRIPT_ROOT / "finalize_lvef_c3_production.py"
RECOVERY_JOB_NAME: Final = "lvef_c3_presrec_5907a1_e5ca"
QSUB_PATH: Final = Path(
    "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
)
ECHOPRIME_PYTHON: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
)

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
JOB_ID_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_CODE_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
ALLOWED_UNTRACKED: Final = frozenset({".DS_Store", "docs/.DS_Store"})
RECOVERY_AUTHORITY_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "original_scientific_governing_commit", "recovery_implementation_commit",
        "environment_authority_commit", "environment_receipt_sha256",
        "environment_to_scientific_commit_relation", "manifest_file_sha256",
        "manifest_semantic_sha256", "original_scheduler_job_id",
        "original_terminal_receipt_sha256", "preservation_script_sha256",
        "finalizer_script_sha256", "recovery_worker_sha256",
        "recovery_runner_sha256", "original_scientific_runner_sha256",
        "scheduler_submission_count", "scientific_stage_reruns", "cpu_only",
        "created_at_utc", "cloud_requests", "dicom_reads",
        "extraction_operations", "gpu_execution", "embedding_generation",
        "model_fitting", "prediction_generation",
        "confirmatory_performance_access", "production_continuation",
    }
)
RECOVERY_CLAIM_KEYS: Final = frozenset(
    {
        "schema_version", "artifact_type", "status", "recovery_authority_sha256",
        "original_scientific_governing_commit", "recovery_implementation_commit",
        "environment_authority_commit", "environment_receipt_sha256",
        "environment_to_scientific_commit_relation", "manifest_file_sha256",
        "manifest_semantic_sha256", "original_scheduler_job_id",
        "original_terminal_receipt_sha256", "scheduler_submission_count",
        "scientific_stage_reruns", "cpu_only", "cloud_requests", "dicom_reads",
        "extraction_operations", "gpu_execution", "embedding_generation",
        "model_fitting", "prediction_generation",
        "confirmatory_performance_access", "production_continuation",
    }
)


class RecoveryError(RuntimeError):
    """Fail-closed recovery error carrying only an aggregate-safe code."""

    def __init__(self, code: str):
        safe = code if SAFE_CODE_RE.fullmatch(code) else "RECOVERY_INVALID"
        super().__init__(safe)
        self.code = safe


def _fail(code: str) -> None:
    raise RecoveryError(code)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    payload = read_regular(path)
    return sha256_bytes(payload)


def _require_nonsymlink_components(path: Path) -> None:
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _fail("RECOVERY_PATH_NOT_CANONICAL")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if os.path.lexists(cursor) and stat.S_ISLNK(os.lstat(cursor).st_mode):
            _fail("RECOVERY_PATH_SYMLINK")


def read_regular(path: Path, *, private: bool = False) -> bytes:
    _require_nonsymlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecoveryError("RECOVERY_INPUT_NOT_REGULAR") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("RECOVERY_INPUT_NOT_REGULAR")
        if private and (
            before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            _fail("RECOVERY_PRIVATE_INPUT_INVALID")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _fail("RECOVERY_INPUT_CHANGED_DURING_READ")
        payload = b"".join(chunks)
        if len(payload) != after.st_size:
            _fail("RECOVERY_INPUT_CHANGED_DURING_READ")
        return payload
    finally:
        os.close(descriptor)


def load_json(path: Path, *, private: bool = False) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail("RECOVERY_JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(
            read_regular(path, private=private).decode("utf-8"),
            object_pairs_hook=pairs,
        )
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError("RECOVERY_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("RECOVERY_JSON_NOT_OBJECT")
    return value


def canonical_payload(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def validate_private_directory(path: Path) -> None:
    _require_nonsymlink_components(path)
    try:
        info = os.lstat(path)
        parent = os.lstat(path.parent)
    except OSError as exc:
        raise RecoveryError("RECOVERY_PRIVATE_DIRECTORY_INVALID") from exc
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != parent.st_gid
        or mode & 0o700 != 0o700
        or mode & 0o077
        or mode & (stat.S_ISUID | stat.S_ISVTX)
        or mode & ~(0o700 | stat.S_ISGID)
    ):
        _fail("RECOVERY_PRIVATE_DIRECTORY_INVALID")


def mkdir_private_no_clobber(path: Path) -> None:
    if os.path.lexists(path):
        _fail("RECOVERY_OUTPUT_COLLISION")
    path.mkdir(mode=0o700)
    validate_private_directory(path)


def write_bytes_no_clobber(path: Path, payload: bytes) -> str:
    validate_private_directory(path.parent)
    if os.path.lexists(path):
        _fail("RECOVERY_OUTPUT_COLLISION")
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return sha256_bytes(payload)


def write_json_no_clobber(path: Path, value: Mapping[str, Any]) -> str:
    return write_bytes_no_clobber(path, canonical_payload(value))


def git(*arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        },
    )
    if completed.returncode:
        _fail("RECOVERY_GIT_AUTHORITY_INVALID")
    return completed.stdout.strip()


def validate_repository_authority() -> str:
    head = git("rev-parse", "HEAD")
    origin = git("rev-parse", "refs/remotes/origin/codex/lvef-multitask-revalidation")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=all")
    entries = {line[3:] for line in status.splitlines() if len(line) >= 4}
    if (
        head != origin
        or branch != "codex/lvef-multitask-revalidation"
        or not COMMIT_RE.fullmatch(head)
        or entries - ALLOWED_UNTRACKED
    ):
        _fail("RECOVERY_REPOSITORY_AUTHORITY_INVALID")
    validate_script_import_tree(git("ls-files", "--others", "--", "scripts"))
    ancestry = subprocess.run(
        [
            "/usr/bin/git", "merge-base", "--is-ancestor",
            ORIGINAL_SCIENTIFIC_COMMIT, head,
        ],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
        },
    )
    if ancestry.returncode:
        _fail("RECOVERY_SCIENTIFIC_COMMIT_NOT_ANCESTOR")
    return head


def validate_script_import_tree(git_output: str) -> None:
    for path in git_output.splitlines():
        if (
            not path.startswith("scripts/__pycache__/")
            or path.count("/") != 2
            or path.endswith("/")
        ):
            _fail("RECOVERY_UNTRACKED_SCRIPT_IMPORT_CANDIDATE")


def find_original_observation() -> Path:
    matches: list[Path] = []
    validate_private_directory(OWNER_PRIVATE_ROOT)
    for directory, names, filenames in os.walk(OWNER_PRIVATE_ROOT, followlinks=False):
        current = Path(directory)
        if any((current / name).is_symlink() for name in names):
            _fail("RECOVERY_PRIVATE_TREE_SYMLINK")
        for filename in filenames:
            path = current / filename
            if path == MANIFEST_PATH or path.is_symlink() or not path.is_file():
                continue
            info = path.stat(follow_symlinks=False)
            if info.st_size == ORIGINAL_OBSERVATION_BYTES and sha256_file(path) == ORIGINAL_OBSERVATION_SHA256:
                matches.append(path)
    if len(matches) != 1:
        _fail("RECOVERY_ORIGINAL_OBSERVATION_IDENTITY_INVALID")
    return matches[0]


@dataclass(frozen=True)
class PreservedState:
    implementation_commit: str
    manifest: Mapping[str, Any]
    manifest_file_sha256: str
    manifest_semantic_sha256: str
    plan: Mapping[str, Any]
    plan_sha256: str
    requirements: Any
    runtime_authority: Mapping[str, str]
    environment_receipt: Path
    environment_relation: str
    preservation_manifest_sha256: str
    preservation_manifest_bytes: int
    scheduler_binding_sha256: str
    original_stage_ledger_sha256: str
    original_pooling_ledger_sha256: str
    original_observation_sha256: str


def _environment_receipt() -> Path:
    import lvef_c3_minimal_canary as minimal

    path = minimal._discover_current_environment_receipt(
        repository=REPOSITORY_ROOT
    )
    payload = read_regular(path, private=True)
    if len(payload) != ENVIRONMENT_RECEIPT_BYTES or sha256_bytes(payload) != ENVIRONMENT_RECEIPT_SHA256:
        _fail("RECOVERY_ENVIRONMENT_RECEIPT_IDENTITY_MISMATCH")
    return path


def _validate_environment(path: Path) -> str:
    import lvef_c3_production_stages as stages

    try:
        result = stages.validate_environment_authority_for_scientific_commit(
            path,
            expected_environment_receipt_sha256=ENVIRONMENT_RECEIPT_SHA256,
            scientific_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        )
    except Exception as exc:
        raise RecoveryError(
            getattr(exc, "code", "RECOVERY_ENVIRONMENT_AUTHORITY_INVALID")
        ) from exc
    if (
        set(result)
        != {
            "status",
            "environment_receipt",
            "environment_receipt_sha256",
            "environment_authority_commit",
            "scientific_governing_commit",
            "environment_authority_relation",
        }
        or result.get("status") != "ENVIRONMENT_AUTHORITY_COMMIT_ANCESTOR"
        or result.get("environment_receipt") != load_json(path, private=True)
        or result.get("environment_receipt_sha256")
        != ENVIRONMENT_RECEIPT_SHA256
        or result.get("environment_authority_commit")
        != ENVIRONMENT_AUTHORITY_COMMIT
        or result.get("scientific_governing_commit")
        != ORIGINAL_SCIENTIFIC_COMMIT
        or result.get("environment_authority_relation") != "ANCESTOR"
    ):
        _fail("RECOVERY_ENVIRONMENT_AUTHORITY_RELATION_INVALID")
    return "ANCESTOR"


def _validate_manifest_and_plan() -> tuple[Mapping[str, Any], str, Mapping[str, Any], str, Any, Mapping[str, str]]:
    import lvef_c3_minimal_canary as minimal
    import lvef_c3_orchestration_core as core

    manifest, payload, file_sha = minimal._load_manifest(MANIFEST_PATH)
    body = manifest["manifest"]
    if (
        file_sha != MANIFEST_FILE_SHA256
        or sha256_bytes(payload) != MANIFEST_FILE_SHA256
        or manifest.get("manifest_sha256") != MANIFEST_SEMANTIC_SHA256
        or body.get("study_count") != MANIFEST_STUDIES
        or body.get("subject_count") != MANIFEST_SUBJECTS
        or body.get("expected_object_count") != DECLARED_OBJECTS
        or body.get("expected_byte_total") != DECLARED_EXPECTED_BYTES
    ):
        _fail("RECOVERY_MANIFEST_AUTHORITY_MISMATCH")
    plan_payload = read_regular(PLAN_PATH, private=True)
    plan = load_json(PLAN_PATH, private=True)
    # Reconstruct the authority exactly as it existed for the scientific job.
    # The checkout may now be a descendant repair commit, but neither the plan
    # nor any original run artifact may be rebound to it.
    discovered = minimal.discover_live_authority(repository=REPOSITORY_ROOT)
    scientific_authority = replace(
        discovered, governing_commit=ORIGINAL_SCIENTIFIC_COMMIT
    )
    try:
        rebuilt, requirements, runtime = minimal._build_direct_manifest_plan(
            manifest, authority=scientific_authority
        )
        plan_sha = core.validate_batch_plan(rebuilt, requirements=requirements)
    except Exception as exc:
        raise RecoveryError("RECOVERY_PLAN_AUTHORITY_INVALID") from exc
    if (
        plan != rebuilt
        or plan_payload != core.canonical_json_bytes(rebuilt)
        or plan["authority"].get("git_commit") != ORIGINAL_SCIENTIFIC_COMMIT
        or plan["authority"].get("selected_manifest_sha256") != MANIFEST_SEMANTIC_SHA256
        or plan["authority"].get("environment_receipt_sha256") != ENVIRONMENT_RECEIPT_SHA256
        or plan["batches"][0].get("n_objects") != DECLARED_OBJECTS
        or plan["batches"][0].get("source_bytes") != DECLARED_EXPECTED_BYTES
    ):
        _fail("RECOVERY_PLAN_AUTHORITY_MISMATCH")
    return manifest, file_sha, plan, plan_sha, requirements, runtime


def _validate_original_failure() -> tuple[str, str]:
    terminal_payload = read_regular(ORIGINAL_TERMINAL_PATH, private=True)
    if len(terminal_payload) != ORIGINAL_TERMINAL_BYTES or sha256_bytes(terminal_payload) != ORIGINAL_TERMINAL_SHA256:
        _fail("RECOVERY_ORIGINAL_TERMINAL_IDENTITY_MISMATCH")
    terminal = load_json(ORIGINAL_TERMINAL_PATH, private=True)
    ledger = load_json(STAGE_LEDGER_PATH, private=True)
    completed = ["DOWNLOAD", "DICOM_EXTRACTION", "ECHOPRIME_EMBEDDING"]
    if (
        terminal.get("status") != "FAIL"
        or terminal.get("failed_stage") != "BATCH_PRESERVATION"
        or terminal.get("completed_stages") != completed
        or terminal.get("scheduler_job_identity") != ORIGINAL_JOB_ID
        or terminal.get("governing_commit") != ORIGINAL_SCIENTIFIC_COMMIT
        or terminal.get("manifest_file_sha256") != MANIFEST_FILE_SHA256
        or terminal.get("manifest_sha256") != MANIFEST_SEMANTIC_SHA256
        or terminal.get("stage_ledger_sha256") != sha256_file(STAGE_LEDGER_PATH)
        or ledger.get("status") != "FAIL"
        or ledger.get("failed_stage") != "BATCH_PRESERVATION"
        or ledger.get("completed_stages") != completed
        or ledger.get("active_stage") is not None
        or ledger.get("scheduler_submission_count") != 1
        or ledger.get("production_continuation") is not False
    ):
        _fail("RECOVERY_ORIGINAL_FAILURE_STATE_INVALID")
    return sha256_file(STAGE_LEDGER_PATH), sha256_file(POOLING_LEDGER_PATH)


def _walk_regular(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        _fail("RECOVERY_ARTIFACT_ROOT_INVALID")
    result: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        if any((current / name).is_symlink() for name in names):
            _fail("RECOVERY_ARTIFACT_TREE_SYMLINK")
        for filename in filenames:
            path = current / filename
            if path.is_symlink() or not path.is_file():
                _fail("RECOVERY_ARTIFACT_NOT_REGULAR")
            result.append(path)
    return sorted(result)


def _artifact_record(path: Path, role: str) -> dict[str, Any]:
    try:
        relative = path.relative_to(RUN_ROOT).as_posix()
    except ValueError as exc:
        raise RecoveryError("RECOVERY_ARTIFACT_OUTSIDE_RUN") from exc
    return {
        "relative_path": relative,
        "size_bytes": path.stat(follow_symlinks=False).st_size,
        "sha256": sha256_file(path),
        "role": role,
    }


def validate_preservation_output_topology(
    *, expect_completion_outputs: bool
) -> None:
    if PRESERVATION_ROOT.is_symlink() or not PRESERVATION_ROOT.is_dir():
        _fail("RECOVERY_PRESERVATION_TOPOLOGY_INVALID")
    observed = {entry.name for entry in os.scandir(PRESERVATION_ROOT)}
    eligibility = BATCH_ROOT / "cache_retirement_eligible_resume_ledger.restricted.json"
    transition_root = PRESERVATION_ROOT / "transition_receipts"
    if not expect_completion_outputs:
        if (
            observed != {PRESERVATION_MANIFEST_PATH.name}
            or os.path.lexists(eligibility)
            or os.path.lexists(transition_root)
            or os.path.lexists(FINALIZATION_PATH)
        ):
            _fail("RECOVERY_INCOMPLETE_STATE_NOT_EXACT")
        return
    if observed != {
        PRESERVATION_MANIFEST_PATH.name,
        PRESERVATION_RECEIPT_PATH.name,
        transition_root.name,
    }:
        _fail("RECOVERY_COMPLETION_TOPOLOGY_INVALID")
    if transition_root.is_symlink() or not transition_root.is_dir():
        _fail("RECOVERY_COMPLETION_TOPOLOGY_INVALID")
    transition_names = {entry.name for entry in os.scandir(transition_root)}
    if transition_names != {
        "preservation_complete.restricted.json",
        "cache_retirement_eligible.restricted.json",
    }:
        _fail("RECOVERY_COMPLETION_TOPOLOGY_INVALID")
    for path in (
        PRESERVATION_RECEIPT_PATH,
        FINALIZATION_PATH,
        eligibility,
        transition_root / "preservation_complete.restricted.json",
        transition_root / "cache_retirement_eligible.restricted.json",
    ):
        if path.is_symlink() or not path.is_file():
            _fail("RECOVERY_COMPLETION_OUTPUT_MISSING_OR_INVALID")


def validate_preservation_manifest_replay(
    *, expect_completion_outputs: bool,
) -> tuple[int, str]:
    import preserve_lvef_c3_production_batch as preservation

    validate_preservation_output_topology(
        expect_completion_outputs=expect_completion_outputs
    )
    records: list[dict[str, Any]] = []
    records.extend(
        _artifact_record(path, "raw_dicom_and_download_authority")
        for path in _walk_regular(RAW_BATCH_ROOT)
    )
    clip_cache_root = EXTRACTION_ROOT / "clips"
    for path in _walk_regular(EXTRACTION_ROOT):
        role = (
            "extracted_npz_cache_owner_retirable"
            if path.is_relative_to(clip_cache_root)
            else "dicom_extraction_metadata_retained"
        )
        records.append(_artifact_record(path, role))
    records.extend(
        _artifact_record(path, "embedding_and_pooling_retained")
        for path in _walk_regular(ECHOPRIME_ROOT)
    )
    records.append(
        _artifact_record(
            BATCH_ROOT / "download_resume_ledger.restricted.json",
            "download_ledger",
        )
    )
    lines = ["\t".join(preservation.MANIFEST_HEADER)]
    for row in sorted(records, key=lambda item: item["relative_path"]):
        lines.append(
            f"{row['relative_path']}\t{row['size_bytes']}\t{row['sha256']}\t{row['role']}"
        )
    expected = ("\n".join(lines) + "\n").encode("utf-8")
    observed = read_regular(PRESERVATION_MANIFEST_PATH, private=True)
    if observed != expected:
        _fail("RECOVERY_PRESERVATION_MANIFEST_DRIFT")
    # Independent replay of every manifest row without emitting its path.
    rows = preservation.read_csv_exact(
        PRESERVATION_MANIFEST_PATH,
        preservation.MANIFEST_HEADER,
        delimiter="\t",
    )
    if len(rows) != len(records):
        _fail("RECOVERY_PRESERVATION_MANIFEST_COUNT_MISMATCH")
    expected_by_path = {row["relative_path"]: row for row in records}
    if len(expected_by_path) != len(records):
        _fail("RECOVERY_PRESERVATION_MANIFEST_DUPLICATE_PATH")
    for row in rows:
        expected_row = expected_by_path.get(row["relative_path"])
        if expected_row is None or row["role"] != expected_row["role"]:
            _fail("RECOVERY_PRESERVATION_ROLE_OR_PATH_MISMATCH")
        artifact = RUN_ROOT / row["relative_path"]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or artifact.stat(follow_symlinks=False).st_size != int(row["size_bytes"])
            or sha256_file(artifact) != row["sha256"]
        ):
            _fail("RECOVERY_PRESERVATION_SECOND_PASS_MISMATCH")
    return len(observed), sha256_bytes(observed)


def validate_pooling_ledger_state(
    plan: Mapping[str, Any], runtime_authority: Mapping[str, Any]
) -> None:
    import lvef_c3_orchestration_core as core

    pooling_ledger = load_json(POOLING_LEDGER_PATH, private=True)
    expected_object_keys = {
        str(row["source_object_key"])
        for row in plan["batches"][0]["objects"]
    }
    try:
        core.validate_resume_authority(
            pooling_ledger,
            expected_authority=runtime_authority,
            attempt_id=RUN_ID,
            expected_object_keys={BATCH_ID: expected_object_keys},
        )
    except Exception as exc:
        raise RecoveryError("RECOVERY_POOLING_LEDGER_AUTHORITY_INVALID") from exc
    batch_ledger = pooling_ledger["batches"].get(BATCH_ID)
    expected_states = [
        "PLANNED",
        "DOWNLOAD_VERIFIED",
        "DICOM_AUDIT_COMPLETE",
        "EXTRACTION_COMPLETE",
        "EMBEDDING_COMPLETE",
        "STUDY_POOLING_COMPLETE",
    ]
    if (
        pooling_ledger.get("status") != "ACTIVE"
        or not isinstance(batch_ledger, Mapping)
        or batch_ledger.get("state") != "STUDY_POOLING_COMPLETE"
        or batch_ledger.get("resume_state") is not None
        or batch_ledger.get("completed_states") != expected_states
        or [event.get("to_state") for event in batch_ledger.get("events", [])]
        != expected_states[1:]
        or batch_ledger.get("download_manifest_sha256")
        != sha256_file(RAW_BATCH_ROOT / "verified_download_manifest.restricted.csv")
        or batch_ledger.get("events", [])[-1].get("output_manifest_sha256")
        != sha256_file(ECHOPRIME_ROOT / "study_manifest.restricted.csv")
    ):
        _fail("RECOVERY_POOLING_LEDGER_STATE_INVALID")


def validate_scientific_aggregates(
    plan: Mapping[str, Any], runtime_authority: Mapping[str, Any]
) -> None:
    import numpy as np
    import preserve_lvef_c3_production_batch as preservation

    validate_pooling_ledger_state(plan, runtime_authority)
    raw_objects = _walk_regular(RAW_BATCH_ROOT / "objects")
    clips = _walk_regular(EXTRACTION_ROOT / "clips")
    if (
        len(raw_objects) != DECLARED_OBJECTS
        or sum(path.stat(follow_symlinks=False).st_size for path in raw_objects)
        != DECLARED_EXPECTED_BYTES
        or len(clips) != 230
    ):
        _fail("RECOVERY_RETAINED_ARTIFACT_COUNT_MISMATCH")
    dicom = load_json(EXTRACTION_ROOT / "dicom_extraction.summary.json", private=True)
    embedding = load_json(ECHOPRIME_ROOT / "echoprime_pooling.summary.json", private=True)
    if (
        dicom.get("status") != "PASS_DICOM_EXTRACTION"
        or dicom.get("n_objects") != 380
        or dicom.get("n_readable") != 380
        or dicom.get("n_unreadable") != 0
        or dicom.get("n_multiframe_candidates") != 230
        or dicom.get("n_single_frame") != 150
        or dicom.get("n_pixel_decode_failures") != 0
        or dicom.get("n_extracted_clips") != 230
        or embedding.get("status") != "PASS_ECHOPRIME_AND_POOLING"
        or embedding.get("n_clip_embeddings") != 230
        or embedding.get("n_pooled_studies") != 5
        or embedding.get("n_no_cine_studies") != 0
        or embedding.get("embedding_dimension") != 512
        or embedding.get("embedding_dtype") != "float32"
        or embedding.get("all_finite") is not True
        or embedding.get("encoder_only") is not True
        or embedding.get("view_classifier_used") is not False
    ):
        _fail("RECOVERY_SCIENTIFIC_SUMMARY_MISMATCH")
    with np.load(ECHOPRIME_ROOT / "clip_embeddings.restricted.npz", allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"}:
            _fail("RECOVERY_CLIP_STORE_SCHEMA_MISMATCH")
        clip_array = archive["embeddings"]
    with np.load(ECHOPRIME_ROOT / "study_embeddings.restricted.npz", allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"}:
            _fail("RECOVERY_STUDY_STORE_SCHEMA_MISMATCH")
        study_array = archive["embeddings"]
    if (
        clip_array.shape != (230, 512)
        or clip_array.dtype != np.float32
        or not np.isfinite(clip_array).all()
        or study_array.shape != (5, 512)
        or study_array.dtype != np.float32
        or not np.isfinite(study_array).all()
    ):
        _fail("RECOVERY_EMBEDDING_STORE_INVALID")
    clip_rows = preservation.read_csv_exact(
        ECHOPRIME_ROOT / "clip_manifest.restricted.csv",
        preservation.CLIP_MANIFEST_HEADER,
    )
    study_rows = preservation.read_csv_exact(
        ECHOPRIME_ROOT / "study_manifest.restricted.csv",
        preservation.STUDY_MANIFEST_HEADER,
    )
    recomputed = preservation.mean_pool_study_embeddings(
        clip_embeddings=clip_array,
        clip_rows=clip_rows,
        study_rows=study_rows,
    )
    if not np.array_equal(recomputed, study_array):
        _fail("RECOVERY_STUDY_POOLING_RECOMPUTATION_MISMATCH")
    if plan["batches"][0].get("n_studies") != 5:
        _fail("RECOVERY_PLAN_STUDY_COUNT_MISMATCH")


def scheduler_binding_sha256(plan_sha: str) -> str:
    import lvef_c3_orchestration_core as core

    return core.canonical_json_sha256(
        {
            "schema_version": 1,
            "job_model": "ONE_SEQUENTIAL_SCC_JOB",
            "scheduler_submission_count": 1,
            "ordered_stages": [
                "DOWNLOAD", "DICOM_EXTRACTION", "ECHOPRIME_EMBEDDING",
                "BATCH_PRESERVATION", "CANARY_FINALIZATION",
            ],
            "manifest_sha256": MANIFEST_SEMANTIC_SHA256,
            "manifest_file_sha256": MANIFEST_FILE_SHA256,
            "batch_plan_sha256": plan_sha,
            "automatic_resubmission": False,
            "array_expansion": False,
            "production_continuation": False,
        }
    )


def validate_preserved_state(*, require_recovery_absent: bool) -> PreservedState:
    implementation = validate_repository_authority()
    observation = find_original_observation()
    manifest, file_sha, plan, plan_sha, requirements, runtime = _validate_manifest_and_plan()
    stage_ledger_sha, pooling_ledger_sha = _validate_original_failure()
    environment = _environment_receipt()
    relation = _validate_environment(environment)
    manifest_bytes, manifest_sha = validate_preservation_manifest_replay(
        expect_completion_outputs=False
    )
    validate_scientific_aggregates(plan, runtime)
    if require_recovery_absent and os.path.lexists(RECOVERY_ROOT):
        _fail("RECOVERY_ALREADY_CLAIMED")
    return PreservedState(
        implementation_commit=implementation,
        manifest=manifest,
        manifest_file_sha256=file_sha,
        manifest_semantic_sha256=MANIFEST_SEMANTIC_SHA256,
        plan=plan,
        plan_sha256=plan_sha,
        requirements=requirements,
        runtime_authority=runtime,
        environment_receipt=environment,
        environment_relation=relation,
        preservation_manifest_sha256=manifest_sha,
        preservation_manifest_bytes=manifest_bytes,
        scheduler_binding_sha256=scheduler_binding_sha256(plan_sha),
        original_stage_ledger_sha256=stage_ledger_sha,
        original_pooling_ledger_sha256=pooling_ledger_sha,
        original_observation_sha256=sha256_file(observation),
    )


def effect_zeros() -> dict[str, Any]:
    return {
        "cloud_requests": 0,
        "dicom_reads": 0,
        "extraction_operations": 0,
        "gpu_execution": 0,
        "embedding_generation": 0,
        "model_fitting": 0,
        "prediction_generation": 0,
        "confirmatory_performance_access": 0,
    }


def validate_installation() -> dict[str, Any]:
    state = validate_preserved_state(require_recovery_absent=True)
    if QSUB_PATH.is_symlink() or not QSUB_PATH.is_file() or not os.access(QSUB_PATH, os.X_OK):
        _fail("RECOVERY_QSUB_AUTHORITY_INVALID")
    try:
        resolved_python = ECHOPRIME_PYTHON.resolve(strict=True)
    except OSError as exc:
        raise RecoveryError("RECOVERY_PYTHON_AUTHORITY_INVALID") from exc
    if not resolved_python.is_file() or resolved_python.is_symlink():
        _fail("RECOVERY_PYTHON_AUTHORITY_INVALID")
    return {
        "status": "PASS_PRESERVATION_RECOVERY_INSTALLATION",
        "implementation_commit": state.implementation_commit,
        "environment_relation": state.environment_relation,
        **effect_zeros(),
    }


def preflight() -> dict[str, Any]:
    state = validate_preserved_state(require_recovery_absent=True)
    return {
        "status": "PASS_PRESERVATION_RECOVERY_PREFLIGHT",
        "implementation_commit": state.implementation_commit,
        "environment_relation": state.environment_relation,
        "scientific_stages_complete": 3,
        "preservation_content_audit": "PASS",
        "preservation_manifest_sha256": state.preservation_manifest_sha256,
        "preservation_manifest_bytes": state.preservation_manifest_bytes,
        **effect_zeros(),
    }


def recovery_authority(state: PreservedState) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_minimal_canary_preservation_recovery_authority_v1",
        "status": "PASS_PRESERVATION_RECOVERY_AUTHORITY",
        "original_scientific_governing_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "recovery_implementation_commit": state.implementation_commit,
        "environment_authority_commit": ENVIRONMENT_AUTHORITY_COMMIT,
        "environment_receipt_sha256": ENVIRONMENT_RECEIPT_SHA256,
        "environment_to_scientific_commit_relation": state.environment_relation,
        "manifest_file_sha256": MANIFEST_FILE_SHA256,
        "manifest_semantic_sha256": MANIFEST_SEMANTIC_SHA256,
        "original_scheduler_job_id": ORIGINAL_JOB_ID,
        "original_terminal_receipt_sha256": ORIGINAL_TERMINAL_SHA256,
        "preservation_script_sha256": sha256_file(PRESERVATION_SCRIPT_PATH),
        "finalizer_script_sha256": sha256_file(FINALIZER_SCRIPT_PATH),
        "recovery_worker_sha256": sha256_file(Path(__file__).resolve()),
        "recovery_runner_sha256": sha256_file(RUNNER_PATH),
        "original_scientific_runner_sha256": sha256_file(
            ORIGINAL_SCIENTIFIC_RUNNER_PATH
        ),
        "scheduler_submission_count": 1,
        "scientific_stage_reruns": 0,
        "cpu_only": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        **effect_zeros(),
        "production_continuation": False,
    }


def create_submission_claim(state: PreservedState) -> None:
    mkdir_private_no_clobber(RECOVERY_ROOT)
    authority_sha = write_json_no_clobber(
        RECOVERY_AUTHORITY_PATH, recovery_authority(state)
    )
    write_json_no_clobber(
        RECOVERY_CLAIM_PATH,
        {
            "schema_version": 1,
            "artifact_type": "lvef_c3_preservation_recovery_submission_claim_v1",
            "status": "PREPARED",
            "recovery_authority_sha256": authority_sha,
            "original_scientific_governing_commit": ORIGINAL_SCIENTIFIC_COMMIT,
            "recovery_implementation_commit": state.implementation_commit,
            "environment_authority_commit": ENVIRONMENT_AUTHORITY_COMMIT,
            "environment_receipt_sha256": ENVIRONMENT_RECEIPT_SHA256,
            "environment_to_scientific_commit_relation": state.environment_relation,
            "manifest_file_sha256": MANIFEST_FILE_SHA256,
            "manifest_semantic_sha256": MANIFEST_SEMANTIC_SHA256,
            "original_scheduler_job_id": ORIGINAL_JOB_ID,
            "original_terminal_receipt_sha256": ORIGINAL_TERMINAL_SHA256,
            "scheduler_submission_count": 1,
            "scientific_stage_reruns": 0,
            "cpu_only": True,
            "production_continuation": False,
            **effect_zeros(),
        },
    )


def qsub_command() -> list[str]:
    return [
        str(QSUB_PATH), "-terse", "-r", "n", "-P", "mimicecho",
        "-N", RECOVERY_JOB_NAME, "-j", "y",
        "-o", str(RECOVERY_ROOT), "-l", "h_rt=2:00:00",
        "-pe", "omp", "4", "-l", "mem_per_core=8G", "-b", "y",
        str(ECHOPRIME_PYTHON), "-I", "-B", "-X", "pycache_prefix=/dev/null/lvef_c3_recovery",
        str(Path(__file__).resolve()), "--run-recovery-worker",
    ]


def submit_recovery(
    *, runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    state = validate_preserved_state(require_recovery_absent=True)
    create_submission_claim(state)
    completed = runner(
        qsub_command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "CUDA_VISIBLE_DEVICES": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "LC_ALL": "C",
        },
    )
    stdout = bytes(completed.stdout)
    stderr = bytes(completed.stderr)
    write_bytes_no_clobber(RECOVERY_QSUB_STDOUT_PATH, stdout)
    write_bytes_no_clobber(RECOVERY_QSUB_STDERR_PATH, stderr)
    write_bytes_no_clobber(
        RECOVERY_QSUB_STATUS_PATH, f"{completed.returncode}\n".encode("ascii")
    )
    if completed.returncode != 0:
        _fail("RECOVERY_QSUB_PROCESS_FAILED")
    if re.fullmatch(rb"[1-9][0-9]{0,19}(?:\n)?", stdout) is None:
        _fail("RECOVERY_QSUB_OUTPUT_IDENTITY_AMBIGUOUS")
    job_id = stdout.rstrip(b"\n").decode("ascii")
    submission = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_preservation_recovery_submission_v1",
        "status": "PASS_NUMERIC_QSUB_ID_CAPTURED",
        "recovery_authority_sha256": sha256_file(RECOVERY_AUTHORITY_PATH),
        "qsub_exit_status": 0,
        "qsub_stdout_bytes": len(stdout),
        "qsub_stdout_sha256": sha256_bytes(stdout),
        "qsub_stderr_bytes": len(stderr),
        "qsub_stderr_sha256": sha256_bytes(stderr),
        "scheduler_job_id": job_id,
        "scheduler_submission_count": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        **effect_zeros(),
    }
    write_json_no_clobber(RECOVERY_SUBMISSION_PATH, submission)
    return {
        "status": "PASS_PRESERVATION_RECOVERY_SUBMITTED",
        "recovery_job_id": job_id,
        "scheduler_submissions": 1,
        **effect_zeros(),
    }


@dataclass(frozen=True)
class RecoveryDependencies:
    preserve: Callable[..., Mapping[str, Any]]
    finalize: Callable[..., Mapping[str, Any]]
    validate_finalization: Callable[[Mapping[str, Any]], None]
    write_finalization: Callable[[Path, Mapping[str, Any]], None]


def production_dependencies() -> RecoveryDependencies:
    import finalize_lvef_c3_production as finalizer
    import preserve_lvef_c3_production_batch as preservation

    return RecoveryDependencies(
        preserve=preservation.preserve_batch,
        finalize=finalizer.finalize_canary_preservation_receipt,
        validate_finalization=finalizer.validate_closed_canary_summary,
        write_finalization=finalizer.write_json_atomic,
    )


def _validate_worker_claim(state: PreservedState) -> None:
    authority = load_json(RECOVERY_AUTHORITY_PATH, private=True)
    claim = load_json(RECOVERY_CLAIM_PATH, private=True)
    expected_hashes = {
        "preservation_script_sha256": sha256_file(PRESERVATION_SCRIPT_PATH),
        "finalizer_script_sha256": sha256_file(FINALIZER_SCRIPT_PATH),
        "recovery_worker_sha256": sha256_file(Path(__file__).resolve()),
        "recovery_runner_sha256": sha256_file(RUNNER_PATH),
        "original_scientific_runner_sha256": sha256_file(
            ORIGINAL_SCIENTIFIC_RUNNER_PATH
        ),
    }
    fixed = {
        "original_scientific_governing_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "recovery_implementation_commit": state.implementation_commit,
        "environment_authority_commit": ENVIRONMENT_AUTHORITY_COMMIT,
        "environment_receipt_sha256": ENVIRONMENT_RECEIPT_SHA256,
        "environment_to_scientific_commit_relation": "ANCESTOR",
        "manifest_file_sha256": MANIFEST_FILE_SHA256,
        "manifest_semantic_sha256": MANIFEST_SEMANTIC_SHA256,
        "original_scheduler_job_id": ORIGINAL_JOB_ID,
        "original_terminal_receipt_sha256": ORIGINAL_TERMINAL_SHA256,
        "scheduler_submission_count": 1,
        "scientific_stage_reruns": 0,
        "cpu_only": True,
        **effect_zeros(),
        "production_continuation": False,
    }
    try:
        created = datetime.fromisoformat(str(authority.get("created_at_utc")))
    except ValueError as exc:
        raise RecoveryError("RECOVERY_SUBMISSION_CLAIM_INVALID") from exc
    if (
        set(authority) != RECOVERY_AUTHORITY_KEYS
        or set(claim) != RECOVERY_CLAIM_KEYS
        or authority.get("schema_version") != 1
        or authority.get("artifact_type")
        != "lvef_c3_minimal_canary_preservation_recovery_authority_v1"
        or authority.get("status") != "PASS_PRESERVATION_RECOVERY_AUTHORITY"
        or created.tzinfo is None
        or created.utcoffset() != timezone.utc.utcoffset(created)
        or any(authority.get(key) != value for key, value in fixed.items())
        or any(authority.get(key) != value for key, value in expected_hashes.items())
        or claim.get("schema_version") != 1
        or claim.get("artifact_type")
        != "lvef_c3_preservation_recovery_submission_claim_v1"
        or claim.get("status") != "PREPARED"
        or claim.get("recovery_authority_sha256") != sha256_file(RECOVERY_AUTHORITY_PATH)
        or any(claim.get(key) != value for key, value in fixed.items())
    ):
        _fail("RECOVERY_SUBMISSION_CLAIM_INVALID")


def validate_finalization_summary(
    summary: Mapping[str, Any],
    *,
    expected_preservation_receipt_sha256: str,
    expected_batch_plan_sha256: str,
    expected_scheduler_plan_sha256: str,
    closed_schema_validator: Callable[[Mapping[str, Any]], None],
) -> None:
    import lvef_c3_orchestration_core as core

    authority_binding_sha256 = core.canonical_json_sha256(
        {
            "preservation_receipt_sha256": expected_preservation_receipt_sha256,
            "canary_manifest_sha256": MANIFEST_SEMANTIC_SHA256,
            "batch_plan_sha256": expected_batch_plan_sha256,
            "scheduler_plan_sha256": expected_scheduler_plan_sha256,
        }
    )
    if (
        summary.get("status")
        != "PASS_CANARY_PRESERVATION_FINALIZED_RETAINED_CACHE"
        or summary.get("successful_train_studies") != 5
        or summary.get("selected_subjects") != 5
        or summary.get("verified_source_objects") != DECLARED_OBJECTS
        or summary.get("selected_source_bytes") != DECLARED_EXPECTED_BYTES
        or summary.get("dicom_readable_objects") != DECLARED_OBJECTS
        or summary.get("dicom_unreadable_objects") != 0
        or summary.get("multiframe_cines") != 230
        or summary.get("single_frame_objects") != 150
        or summary.get("extracted_clips") != 230
        or summary.get("unique_clip_keys") != 230
        or summary.get("clip_embeddings") != 230
        or summary.get("pooled_studies") != 5
        or summary.get("no_cine_studies") != 0
        or summary.get("failed_studies") != 0
        or summary.get("preservation_receipt_sha256")
        != expected_preservation_receipt_sha256
        or summary.get("canary_manifest_sha256")
        != MANIFEST_SEMANTIC_SHA256
        or summary.get("batch_plan_sha256") != expected_batch_plan_sha256
        or summary.get("scheduler_plan_sha256")
        != expected_scheduler_plan_sha256
        or summary.get("authority_binding_sha256")
        != authority_binding_sha256
        or summary.get("all_studies_successful") is not True
        or summary.get("all_studies_train") is not True
        or summary.get("manifest_plan_scheduler_binding_passed") is not True
        or summary.get("all_preservation_gates_passed") is not True
        or summary.get("raw_dicoms_retained") is not True
        or summary.get("extracted_cache_retained") is not True
        or summary.get("aggregate_safe") is not True
        or summary.get("production_continuation_authorized") is not False
        or summary.get("identifiers_emitted") is not False
        or summary.get("restricted_paths_emitted") is not False
    ):
        _fail("RECOVERY_FINALIZATION_SUMMARY_INVALID")
    try:
        closed_schema_validator(summary)
    except Exception as exc:
        raise RecoveryError("RECOVERY_FINALIZATION_SUMMARY_INVALID") from exc


def load_bound_preservation_receipt(
    returned_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    import lvef_c3_orchestration_core as core

    receipt = load_json(PRESERVATION_RECEIPT_PATH, private=True)
    if not isinstance(returned_receipt, Mapping) or dict(returned_receipt) != receipt:
        _fail("RECOVERY_PRESERVATION_RECEIPT_RETURN_MISMATCH")
    return receipt, core.canonical_json_sha256(receipt)


def validate_postwrite_state(
    state: PreservedState,
    summary: Mapping[str, Any],
    *,
    closed_schema_validator: Callable[[Mapping[str, Any]], None],
) -> None:
    manifest_bytes, manifest_sha = validate_preservation_manifest_replay(
        expect_completion_outputs=True
    )
    if (
        manifest_bytes != state.preservation_manifest_bytes
        or manifest_sha != state.preservation_manifest_sha256
        or sha256_file(MANIFEST_PATH) != state.manifest_file_sha256
        or sha256_file(ORIGINAL_TERMINAL_PATH) != ORIGINAL_TERMINAL_SHA256
        or sha256_file(STAGE_LEDGER_PATH) != state.original_stage_ledger_sha256
        or sha256_file(POOLING_LEDGER_PATH) != state.original_pooling_ledger_sha256
        or sha256_file(find_original_observation())
        != state.original_observation_sha256
    ):
        _fail("RECOVERY_POSTWRITE_IMMUTABILITY_MISMATCH")
    manifest, file_sha, plan, plan_sha, _, _ = _validate_manifest_and_plan()
    if (
        manifest != state.manifest
        or file_sha != state.manifest_file_sha256
        or plan != state.plan
        or plan_sha != state.plan_sha256
    ):
        _fail("RECOVERY_POSTWRITE_AUTHORITY_MISMATCH")
    _validate_original_failure()
    validate_scientific_aggregates(plan, state.runtime_authority)
    written_summary = load_json(FINALIZATION_PATH)
    if written_summary != summary:
        _fail("RECOVERY_FINALIZATION_WRITE_MISMATCH")
    try:
        closed_schema_validator(written_summary)
    except Exception as exc:
        raise RecoveryError("RECOVERY_FINALIZATION_WRITE_MISMATCH") from exc


def execute_recovery(
    *,
    scheduler_job_id: str,
    dependencies: RecoveryDependencies | None = None,
) -> dict[str, Any]:
    if JOB_ID_RE.fullmatch(scheduler_job_id) is None:
        _fail("RECOVERY_SCHEDULER_JOB_ID_INVALID")
    if not RECOVERY_ROOT.exists():
        _fail("RECOVERY_SUBMISSION_CLAIM_MISSING")
    validate_private_directory(RECOVERY_ROOT)
    if os.path.lexists(RECOVERY_TERMINAL_PATH):
        _fail("RECOVERY_ALREADY_TERMINAL")
    state = validate_preserved_state(require_recovery_absent=False)
    _validate_worker_claim(state)
    source = dependencies or production_dependencies()
    returned_receipt = source.preserve(
        contract_path=SCRIPT_ROOT.parent / "configs/lvef_c3_orchestration_v2.yaml",
        plan_path=PLAN_PATH,
        batch_id=BATCH_ID,
        attempt_id=RUN_ID,
        governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        production_root=RUN_ROOT,
        output_root=PRESERVATION_ROOT,
        environment_receipt=state.environment_receipt,
        checkpoint=Path(
            "/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt"
        ),
        scheduler_job_identity=ORIGINAL_JOB_ID,
        input_ledger=POOLING_LEDGER_PATH,
        requirements=state.requirements,
        expected_runtime_authority=state.runtime_authority,
        scheduler_runner_path=ORIGINAL_SCIENTIFIC_RUNNER_PATH,
    )
    receipt, receipt_semantic_sha = load_bound_preservation_receipt(
        returned_receipt
    )
    summary = source.finalize(
        receipt,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=RUN_ID,
        expected_canary_manifest_sha256=MANIFEST_SEMANTIC_SHA256,
        expected_batch_plan_sha256=state.plan_sha256,
        expected_scheduler_plan_sha256=state.scheduler_binding_sha256,
        expected_object_count=DECLARED_OBJECTS,
        expected_source_bytes=DECLARED_EXPECTED_BYTES,
    )
    validate_finalization_summary(
        summary,
        expected_preservation_receipt_sha256=receipt_semantic_sha,
        expected_batch_plan_sha256=state.plan_sha256,
        expected_scheduler_plan_sha256=state.scheduler_binding_sha256,
        closed_schema_validator=source.validate_finalization,
    )
    source.write_finalization(FINALIZATION_PATH, summary)
    validate_postwrite_state(
        state, summary, closed_schema_validator=source.validate_finalization
    )
    terminal = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_minimal_canary_preservation_recovery_terminal_v1",
        "status": "PASS_WITH_POSTJOB_PRESERVATION_RECOVERY",
        "original_scientific_governing_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "recovery_implementation_commit": state.implementation_commit,
        "original_scheduler_job_id": ORIGINAL_JOB_ID,
        "recovery_scheduler_job_id": scheduler_job_id,
        "recovery_authority_sha256": sha256_file(RECOVERY_AUTHORITY_PATH),
        "original_terminal_receipt_sha256": ORIGINAL_TERMINAL_SHA256,
        "preservation_manifest_sha256": sha256_file(PRESERVATION_MANIFEST_PATH),
        "preservation_receipt_sha256": sha256_file(PRESERVATION_RECEIPT_PATH),
        "finalization_summary_sha256": sha256_file(FINALIZATION_PATH),
        "scientific_stage_reruns": 0,
        "raw_dicoms_retained": True,
        "extracted_clips_retained": True,
        **effect_zeros(),
        "production_continuation": False,
    }
    write_json_no_clobber(RECOVERY_TERMINAL_PATH, terminal)
    return terminal


def write_failure_terminal(code: str, job_id: str) -> None:
    if not RECOVERY_ROOT.exists() or os.path.lexists(RECOVERY_TERMINAL_PATH):
        return
    try:
        write_json_no_clobber(
            RECOVERY_TERMINAL_PATH,
            {
                "schema_version": 1,
                "artifact_type": "lvef_c3_minimal_canary_preservation_recovery_terminal_v1",
                "status": "FAIL",
                "safe_failure_code": code,
                "original_scientific_governing_commit": ORIGINAL_SCIENTIFIC_COMMIT,
                "original_scheduler_job_id": ORIGINAL_JOB_ID,
                "recovery_scheduler_job_id": job_id if JOB_ID_RE.fullmatch(job_id) else "NOT_AVAILABLE",
                "original_terminal_receipt_sha256": ORIGINAL_TERMINAL_SHA256,
                "scientific_stage_reruns": 0,
                **effect_zeros(),
                "production_continuation": False,
            },
        )
    except Exception:
        return


def print_result(result: Mapping[str, Any]) -> None:
    print(f"STATUS={result['status']}")
    print(f"ORIGINAL_SCIENTIFIC_COMMIT={ORIGINAL_SCIENTIFIC_COMMIT}")
    if "environment_relation" in result:
        print(f"ENVIRONMENT_AUTHORITY_RELATION={result['environment_relation']}")
    if result.get("scientific_stages_complete") == 3:
        print("SCIENTIFIC_STAGES_COMPLETE=3")
    if result.get("preservation_content_audit") == "PASS":
        print("PRESERVATION_CONTENT_AUDIT=PASS")
        print(f"PRESERVATION_MANIFEST_BYTES={result['preservation_manifest_bytes']}")
        print(f"PRESERVATION_MANIFEST_SHA256={result['preservation_manifest_sha256']}")
    if result["status"] == "PASS_PRESERVATION_RECOVERY_PREFLIGHT":
        print("PRESERVATION_RECOVERY_PREFLIGHT=PASS")
    if "recovery_job_id" in result:
        print(f"RECOVERY_JOB_ID={result['recovery_job_id']}")
        print(f"RECOVERY_SCHEDULER_SUBMISSIONS={result['scheduler_submissions']}")
    print(f"CLOUD_REQUESTS={result['cloud_requests']}")
    print(f"DICOM_READS={result['dicom_reads']}")
    print(f"GPU_EXECUTION={result['gpu_execution']}")
    print(f"EMBEDDING_GENERATION={result['embedding_generation']}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-installation", action="store_true")
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--submit", action="store_true")
    modes.add_argument("--run-recovery-worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.run_recovery_worker:
        job_id = os.environ.get("JOB_ID", "")
        try:
            terminal = execute_recovery(scheduler_job_id=job_id)
        except Exception as exc:
            code = getattr(exc, "code", "RECOVERY_UNEXPECTED_FAILURE")
            if not SAFE_CODE_RE.fullmatch(str(code)):
                code = "RECOVERY_UNEXPECTED_FAILURE"
            write_failure_terminal(str(code), job_id)
            print(f"STATUS=BLOCKED_{code}")
            return 78
        print(f"STATUS={terminal['status']}")
        print("SCIENTIFIC_STAGE_RERUNS=0")
        print("PRODUCTION_CONTINUATION=NO")
        return 0
    if args.validate_installation:
        result = validate_installation()
    elif args.preflight_only:
        result = preflight()
    else:
        result = submit_recovery()
    print_result(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as exc:
        print(f"STATUS=BLOCKED_{exc.code}")
        print("RECOVERY_CLOUD_REQUESTS=0")
        print("RECOVERY_DICOM_READS=0")
        print("RECOVERY_GPU_EXECUTION=0")
        print("RECOVERY_EMBEDDING_GENERATION=0")
        raise SystemExit(78)
    except Exception:
        # Never emit a traceback, path, identifier, or private row from an
        # unexpected validation/import failure in an operator-facing mode.
        print("STATUS=BLOCKED_RECOVERY_UNEXPECTED_FAILURE")
        print("RECOVERY_CLOUD_REQUESTS=0")
        print("RECOVERY_DICOM_READS=0")
        print("RECOVERY_GPU_EXECUTION=0")
        print("RECOVERY_EMBEDDING_GENERATION=0")
        raise SystemExit(78)
