#!/usr/bin/env python3
"""Fail-closed, owner-authorized retirement of one preserved C3 clip cache.

Validation is read-only.  Deletion is reachable only through ``--execute`` and
an exact restricted authorization receipt.  Raw DICOM roots are never accepted
as a target and are revalidated before and after cache retirement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core
import finalize_lvef_c3_production as finalizer
import lvef_c3_production_stages as production_stages


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BATCH_RE = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
PRODUCTION_ROOT_PREFIX = Path("/restricted/projectnb")
LIVE_PRODUCTION_ROOT = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
MAX_AUTHORITY_JSON_BYTES = 8 * 1024 * 1024
_SYNTHETIC_TEST_ROOT_CAPABILITY = object()
AUTH_KEYS = {
    "schema_version", "artifact_type", "status", "authorization_scope",
    "owner_authorized", "owner_authorization_date_utc",
    "batch_id", "attempt_id",
    "authority_sha256", "preservation_receipt_sha256",
    "cache_inventory_sha256", "launch_authority_sha256",
}
INTENT_KEYS = {
    "schema_version", "artifact_type", "status", "batch_id", "attempt_id",
    "governing_commit", "preservation_receipt_sha256",
    "authorization_receipt_sha256", "cache_tree_sha256",
    "raw_dicom_deletion_permitted",
}
STAGED_KEYS = {
    "schema_version", "artifact_type", "status", "batch_id", "attempt_id",
    "governing_commit", "intent_receipt_sha256", "cache_tree_sha256",
    "atomic_same_filesystem_rename_completed", "raw_dicom_deletion_permitted",
}


class CacheRetirementError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CacheRetirementError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _stable_regular_bytes(
    path: Path, code: str, *, max_bytes: int | None = None,
    owner_private: bool = False,
) -> bytes:
    """Read one stable regular-file inode without following its final link."""

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CacheRetirementError(f"{code}_NOT_REGULAR") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CacheRetirementError(f"{code}_NOT_REGULAR")
        if owner_private and (
            before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise CacheRetirementError(f"{code}_NOT_OWNER_PRIVATE")
        if max_bytes is not None and before.st_size > max_bytes:
            raise CacheRetirementError(f"{code}_TOO_LARGE")
        chunks: list[bytes] = []
        observed = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            observed += len(block)
            if max_bytes is not None and observed > max_bytes:
                raise CacheRetirementError(f"{code}_TOO_LARGE")
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CacheRetirementError(f"{code}_CHANGED_DURING_READ") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if path.is_symlink() or identity(before) != identity(after) or identity(after) != identity(current):
        raise CacheRetirementError(f"{code}_CHANGED_DURING_READ")
    return b"".join(chunks)


def load_json_and_sha256(
    path: Path, code: str, *, owner_private: bool = False,
    max_bytes: int = MAX_AUTHORITY_JSON_BYTES,
) -> tuple[dict[str, Any], str]:
    body = _stable_regular_bytes(
        path,
        code,
        max_bytes=max_bytes,
        owner_private=owner_private,
    )
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
    except CacheRetirementError:
        raise
    except Exception as exc:
        raise CacheRetirementError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise CacheRetirementError(f"{code}_NOT_OBJECT")
    return value, hashlib.sha256(body).hexdigest()


def load_json(path: Path, code: str) -> dict[str, Any]:
    return load_json_and_sha256(path, code)[0]


def require_owner_private(path: Path, code: str) -> None:
    _stable_regular_bytes(path, code, max_bytes=MAX_AUTHORITY_JSON_BYTES, owner_private=True)


def require_no_symlink_ancestors(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CacheRetirementError("RETIREMENT_PATH_OUTSIDE_ROOT") from exc
    cursor = root
    if cursor.is_symlink():
        raise CacheRetirementError("RETIREMENT_PATH_SYMLINK_ANCESTOR")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CacheRetirementError("RETIREMENT_PATH_SYMLINK_ANCESTOR")


def sha256_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CacheRetirementError("HASH_INPUT_NOT_REGULAR") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CacheRetirementError("HASH_INPUT_NOT_REGULAR")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CacheRetirementError("HASH_INPUT_CHANGED_DURING_READ") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if path.is_symlink() or identity(before) != identity(after) or identity(after) != identity(current):
        raise CacheRetirementError("HASH_INPUT_CHANGED_DURING_READ")
    return digest.hexdigest()


def cache_tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise CacheRetirementError("CACHE_ROOT_NOT_REGULAR")
    records: list[str] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in names:
            if (current / name).is_symlink():
                raise CacheRetirementError("CACHE_TREE_SYMLINK")
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise CacheRetirementError("CACHE_TREE_NONREGULAR")
            records.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t{sha256_file(path)}"
            )
    if not records:
        raise CacheRetirementError("CACHE_TREE_EMPTY")
    return hashlib.sha256(("\n".join(sorted(records)) + "\n").encode()).hexdigest()


def validate_preservation_coverage(
    manifest_path: Path, *, production_root: Path,
    required_roots: Sequence[tuple[Path, Path]],
) -> None:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CacheRetirementError("PRESERVATION_MANIFEST_NOT_REGULAR")
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected_header = ["relative_path", "size_bytes", "sha256", "role"]
        if reader.fieldnames != expected_header or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise CacheRetirementError("PRESERVATION_MANIFEST_SCHEMA_INVALID")
        rows = list(reader)
    by_relative: dict[str, Mapping[str, str]] = {}
    for row in rows:
        relative = str(row["relative_path"])
        if relative in by_relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise CacheRetirementError("PRESERVATION_MANIFEST_PATH_INVALID")
        by_relative[relative] = row
    for actual_root, logical_root in required_roots:
        if actual_root.is_symlink() or not actual_root.is_dir():
            raise CacheRetirementError("PRESERVED_TREE_INVALID")
        for directory, names, filenames in os.walk(actual_root, followlinks=False):
            current = Path(directory)
            if any((current / name).is_symlink() for name in names):
                raise CacheRetirementError("PRESERVED_TREE_SYMLINK")
            for name in filenames:
                path = current / name
                if path.is_symlink() or not path.is_file():
                    raise CacheRetirementError("PRESERVED_TREE_NONREGULAR")
                logical_path = logical_root / path.relative_to(actual_root)
                relative = logical_path.relative_to(production_root).as_posix()
                row = by_relative.get(relative)
                if (
                    row is None
                    or int(row["size_bytes"]) != path.stat().st_size
                    or row["sha256"] != sha256_file(path)
                ):
                    raise CacheRetirementError("PRESERVATION_TREE_COVERAGE_MISMATCH")


def _validate_production_root(
    production_root: Path, *, allowed_production_prefix: Path,
    synthetic_test_capability: object | None,
) -> None:
    """Enforce the fixed live root; a private capability permits test roots."""

    if production_root.is_symlink() or not production_root.is_dir():
        raise CacheRetirementError("PRODUCTION_ROOT_INVALID")
    resolved = production_root.resolve()
    if resolved == LIVE_PRODUCTION_ROOT:
        return
    if synthetic_test_capability is not _SYNTHETIC_TEST_ROOT_CAPABILITY:
        raise CacheRetirementError("PRODUCTION_ROOT_AUTHORITY_MISMATCH")
    try:
        resolved.relative_to(allowed_production_prefix.resolve())
    except ValueError as exc:
        raise CacheRetirementError("SYNTHETIC_PRODUCTION_ROOT_OUTSIDE_TEST_SCOPE") from exc


def derive_current_runtime_authority(
    *, plan: Mapping[str, Any],
    effective_requirements: core.PlanRequirements, contract: Mapping[str, Any],
    contract_path: Path, governing_commit: str, environment_receipt: Path,
    synthetic_test_capability: object | None = None,
) -> dict[str, str]:
    """Derive authority from current inputs, with one explicit miniature seam."""

    environment_sha = sha256_file(environment_receipt)
    if synthetic_test_capability is not _SYNTHETIC_TEST_ROOT_CAPABILITY:
        return core.derive_expected_runtime_authority(
            plan,
            requirements=effective_requirements,
            contract=contract,
            contract_path=contract_path,
            governing_commit=governing_commit,
            environment_receipt_sha256=environment_sha,
        )
    # The exact-two-batch test uses synthetic cohort hashes that deliberately do
    # not equal the frozen live contract.  Still derive every runtime field from
    # the current plan/files; never accept the caller-supplied authority as truth.
    plan_sha = core.validate_current_batch_plan_v3(
        plan, requirements=effective_requirements
    )
    plan_authority = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha}
    )
    try:
        contract_state_sha = str(contract["authority"]["state_machine_schema_sha256"])
        contract_resume_sha = str(contract["authority"]["resume_ledger_schema_sha256"])
    except (KeyError, TypeError) as exc:
        raise core.OrchestrationError("SYNTHETIC_CONTRACT_AUTHORITY_INVALID") from exc
    if (
        plan_authority["orchestration_contract_sha256"] != sha256_file(contract_path)
        or plan_authority["state_machine_schema_sha256"] != contract_state_sha
        or plan_authority["resume_ledger_schema_sha256"] != contract_resume_sha
        or plan_authority["git_commit"] != governing_commit
        or plan_authority["environment_receipt_sha256"] != environment_sha
    ):
        raise core.OrchestrationError("SYNTHETIC_CURRENT_RUNTIME_AUTHORITY_MISMATCH")
    return plan_authority


def _derive_and_validate_ledger_authority(
    *, ledger: Mapping[str, Any], plan: Mapping[str, Any],
    effective_requirements: core.PlanRequirements, contract: Mapping[str, Any],
    contract_path: Path, governing_commit: str, environment_receipt: Path,
    supplied_runtime_authority: Mapping[str, Any] | None, attempt_id: str,
    batch_id: str, expected_object_keys: set[str],
    synthetic_test_capability: object | None = None,
) -> dict[str, str]:
    """Never trust an injected runtime authority without independent derivation."""

    try:
        derived = derive_current_runtime_authority(
            plan=plan,
            effective_requirements=effective_requirements,
            contract=contract,
            contract_path=contract_path,
            governing_commit=governing_commit,
            environment_receipt=environment_receipt,
            synthetic_test_capability=synthetic_test_capability,
        )
        if supplied_runtime_authority is not None and (
            core.validate_runtime_authority(supplied_runtime_authority) != derived
        ):
            raise CacheRetirementError("SUPPLIED_RUNTIME_AUTHORITY_MISMATCH")
        core.validate_resume_authority(
            ledger,
            expected_authority=derived,
            attempt_id=attempt_id,
            expected_object_keys={batch_id: expected_object_keys},
        )
    except CacheRetirementError:
        raise
    except core.OrchestrationError as exc:
        raise CacheRetirementError("CURRENT_RUNTIME_OR_LEDGER_AUTHORITY_INVALID") from exc
    return derived


def _validate_raw_retention(
    raw_root: Path, *, planned_batch: Mapping[str, Any],
) -> None:
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise CacheRetirementError("RAW_RETENTION_ROOT_INVALID")
    try:
        expected = {
            f"{row['source_object_key']}.dcm": int(row["size_bytes"])
            for row in planned_batch["objects"]
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CacheRetirementError("RAW_RETENTION_PLAN_INVALID") from exc
    observed: dict[str, int] = {}
    for entry in os.scandir(raw_root):
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise CacheRetirementError("RAW_RETENTION_ENTRY_INVALID")
        metadata = entry.stat(follow_symlinks=False)
        observed[entry.name] = metadata.st_size
    if observed != expected:
        raise CacheRetirementError("RAW_RETENTION_MEMBERSHIP_MISMATCH")


def validate_preservation_eligibility_receipt(
    receipt: Mapping[str, Any], *, planned_batch: Mapping[str, Any],
    governing_commit: str, attempt_id: str, batch_id: str,
    plan_sha256: str, expected_authority: Mapping[str, str],
    contract: Mapping[str, Any], contract_path: Path,
    environment_receipt: Path, production_root: Path,
    preservation_manifest: Path,
) -> None:
    """Closed validation of every destructive eligibility assertion."""

    environment, environment_sha = load_json_and_sha256(
        environment_receipt, "PRESERVATION_ENVIRONMENT_RECEIPT"
    )
    expected_keys = finalizer.PRESERVATION_ELIGIBILITY_RECEIPT_KEYS
    if set(receipt) != expected_keys:
        raise CacheRetirementError("PRESERVATION_RECEIPT_SCHEMA_MISMATCH")
    if (
        receipt.get("schema_version") != 2
        or receipt.get("artifact_type")
        != "lvef_c3_batch_preservation_eligibility_receipt_v3"
        or receipt.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or receipt.get("attempt_id") != attempt_id
        or receipt.get("batch_id") != batch_id
        or receipt.get("governing_commit") != governing_commit
        or receipt.get("source_commit") != governing_commit
        or receipt.get("batch_plan_sha256") != plan_sha256
        or receipt.get("orchestration_contract_sha256") != sha256_file(contract_path)
        or receipt.get("environment_receipt_sha256")
        != environment_sha
        or receipt.get("package_inventory_sha256")
        != environment.get("package_inventory_sha256")
        or receipt.get("checkpoint_sha256") != expected_authority["checkpoint_sha256"]
        or receipt.get("checkpoint_checksum") != receipt.get("checkpoint_sha256")
        or receipt.get("cohort_version") != str(contract["cohort"]["release"])
        or receipt.get("split_version")
        != f"split_map_sha256:{contract['cohort']['split_map_sha256']}"
        or receipt.get("execution_contract_version")
        != int(contract["authority"]["execution_contract_version"])
        or finalizer.TIMESTAMP_RE.fullmatch(str(receipt.get("run_timestamp_utc")))
        is None
        or receipt.get("aggregate_safety_gate_result") != "PASS"
        or receipt.get("technical_disposition_policy_version")
        != production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        or receipt.get("extracted_cache_retired") is not False
    ):
        raise CacheRetirementError("PRESERVATION_AUTHORITY_INVALID")
    for key in (
        "python_version", "pytorch_version", "torchvision_version",
        "cuda_version", "cudnn_version",
    ):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise CacheRetirementError("PRESERVATION_RUNTIME_VERSION_INVALID")
    if re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", str(receipt.get("scheduler_job_identity"))
    ) is None:
        raise CacheRetirementError("PRESERVATION_SCHEDULER_IDENTITY_INVALID")
    for key in finalizer.HASH_KEYS - finalizer.RETIREMENT_RECEIPT_KEYS:
        if SHA_RE.fullmatch(str(receipt.get(key))) is None:
            raise CacheRetirementError("PRESERVATION_HASH_INVALID")
    for key in finalizer.COUNT_KEYS:
        value = receipt.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CacheRetirementError("PRESERVATION_COUNT_INVALID")
    for key in finalizer.TRUE_GATE_KEYS:
        if receipt.get(key) is not True:
            raise CacheRetirementError("PRESERVATION_GATE_FAILED")
    for key in finalizer.ZERO_KEYS:
        if receipt[key] != 0:
            raise CacheRetirementError("PRESERVATION_SCIENTIFIC_INCONSISTENCY")
    if (
        receipt["n_selected_studies"] != planned_batch["n_studies"]
        or receipt["n_selected_subjects"] != planned_batch["n_subjects"]
        or receipt["n_expected_objects"] != planned_batch["n_objects"]
        or receipt["expected_source_bytes"] != planned_batch["source_bytes"]
        or receipt.get("prespecified_no_cine_study_set_sha256")
        != planned_batch["prespecified_no_cine_study_set_sha256"]
        or receipt.get("n_no_cine_studies")
        != planned_batch["expected_no_cine_studies"]
        or receipt.get("all_no_cine_studies_prespecified") is not True
        or receipt["n_download_verified"] != receipt["n_expected_objects"]
        or receipt["n_dicom_readable"] + receipt["n_dicom_unreadable"]
        != receipt["n_expected_objects"]
        or receipt["n_multiframe_cines"] + receipt["n_single_frame_objects"]
        != receipt["n_dicom_readable"]
        or receipt["n_multiframe_cines"]
        != receipt["n_successfully_extracted_cines"]
        + receipt["n_object_technical_dispositions"]
        or receipt["n_object_technical_dispositions"]
        > production_stages.OBJECT_TECHNICAL_DISPOSITION_ABSOLUTE_LIMIT
        or receipt["n_object_technical_dispositions"]
        * production_stages.OBJECT_TECHNICAL_DISPOSITION_RATE_DENOMINATOR
        > receipt["n_multiframe_cines"]
        or receipt["n_successfully_extracted_cines"]
        != receipt["n_extracted_clips"]
        or receipt["n_successfully_extracted_cines"]
        != receipt["n_unique_clip_keys"]
        or receipt["n_successfully_extracted_cines"]
        != receipt["n_clip_embeddings"]
        or receipt.get("technical_disposition_counts_by_class")
        != {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": receipt[
                "n_object_technical_dispositions"
            ]
        }
        or receipt["n_studies_affected_by_technical_disposition"]
        > receipt["n_object_technical_dispositions"]
        or (
            receipt["n_studies_affected_by_technical_disposition"] == 0
        ) is not (receipt["n_object_technical_dispositions"] == 0)
        or receipt["n_pooled_studies"] + receipt["n_no_cine_studies"]
        != receipt["n_selected_studies"]
        or (
            receipt["n_no_cine_studies"] == 0
            and receipt.get("no_cine_disposition") != "NONE"
        )
        or (
            receipt["n_no_cine_studies"] > 0
            and receipt.get("no_cine_disposition")
            != "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
        )
    ):
        raise CacheRetirementError("PRESERVATION_COUNT_OR_DISPOSITION_MISMATCH")

    batch_root = production_root / "attempts" / attempt_id / "batches" / batch_id
    extraction_root = (
        production_root
        / "attempts"
        / attempt_id
        / "extracted_cache"
        / batch_id
        / "dicom_extraction"
    )
    artifact_paths = {
        "state_input_ledger_sha256": batch_root / "pooling_resume_ledger.restricted.json",
        "source_receipt_sha256": batch_root / "download_resume_ledger.restricted.json",
        "dicom_audit_sha256": extraction_root / "dicom_audit.restricted.csv",
        "extraction_manifest_sha256": extraction_root / "extraction_manifest.restricted.csv",
        "technical_disposition_manifest_sha256": extraction_root / "technical_disposition_manifest.restricted.csv",
        "clip_manifest_sha256": batch_root / "echoprime" / "clip_manifest.restricted.csv",
        "clip_embeddings_sha256": batch_root / "echoprime" / "clip_embeddings.restricted.npz",
        "study_manifest_sha256": batch_root / "echoprime" / "study_manifest.restricted.csv",
        "study_embeddings_sha256": batch_root / "echoprime" / "study_embeddings.restricted.npz",
        "preservation_manifest_sha256": preservation_manifest,
        "production_stage_wrapper_sha256": (
            Path(__file__).resolve().parent / "lvef_c3_production_stages.py"
        ),
        "batch_preservation_script_sha256": (
            Path(__file__).resolve().parent / "preserve_lvef_c3_production_batch.py"
        ),
        "scheduler_runner_sha256": (
            Path(__file__).resolve().parent / "scc_run_lvef_c3_full_sequential.sh"
        ),
    }
    for key, path in artifact_paths.items():
        observed_hash = (
            production_stages.technical_disposition_manifest_sha256(path)
            if key == "technical_disposition_manifest_sha256"
            else sha256_file(path)
        )
        if observed_hash != receipt[key]:
            raise CacheRetirementError("PRESERVATION_REFERENCED_HASH_MISMATCH")
    expected_command_checksum = core.canonical_json_sha256(
        {
            key: receipt[key]
            for key in (
                "production_stage_wrapper_sha256",
                "batch_preservation_script_sha256",
                "scheduler_runner_sha256",
            )
        }
    )
    expected_config_checksum = core.canonical_json_sha256(
        {
            "orchestration_contract_sha256": receipt[
                "orchestration_contract_sha256"
            ],
            "batch_plan_sha256": plan_sha256,
            "state_machine_schema_sha256": expected_authority[
                "state_machine_schema_sha256"
            ],
            "resume_ledger_schema_sha256": expected_authority[
                "resume_ledger_schema_sha256"
            ],
        }
    )
    if (
        receipt["command_checksum"] != expected_command_checksum
        or receipt["config_checksum"] != expected_config_checksum
    ):
        raise CacheRetirementError("PRESERVATION_DERIVED_HASH_MISMATCH")


def validate_gate(
    *, contract_path: Path, plan_path: Path, environment_receipt: Path,
    production_root: Path, attempt_id: str, batch_id: str,
    governing_commit: str, final_ledger_path: Path,
    preservation_receipt_path: Path, authorization_receipt_path: Path,
    launch_authority_sha256: str,
    require_authorization: bool,
    requirements: core.PlanRequirements | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
    allowed_production_prefix: Path = PRODUCTION_ROOT_PREFIX,
    _synthetic_test_capability: object | None = None,
) -> dict[str, Any]:
    if (
        not ATTEMPT_RE.fullmatch(attempt_id)
        or not BATCH_RE.fullmatch(batch_id)
        or not COMMIT_RE.fullmatch(governing_commit)
        or SHA_RE.fullmatch(launch_authority_sha256) is None
    ):
        raise CacheRetirementError("IDENTITY_ARGUMENT_INVALID")
    _validate_production_root(
        production_root,
        allowed_production_prefix=allowed_production_prefix,
        synthetic_test_capability=_synthetic_test_capability,
    )
    # Only extracted NPZ clip derivatives are owner-retirable.  DICOM audit,
    # extraction manifests, summaries, and transition receipts remain in the
    # parent directory as permanent provenance.
    cache_root = (
        production_root / "attempts" / attempt_id / "extracted_cache" /
        batch_id / "dicom_extraction" / "clips"
    )
    raw_root = production_root / "attempts" / attempt_id / "raw" / batch_id / "objects"
    batch_root = production_root / "attempts" / attempt_id / "batches" / batch_id
    expected_preservation_receipt = (
        batch_root / "preservation" / "batch_preservation_receipt.restricted.json"
    )
    expected_eligibility_ledger = (
        batch_root / "cache_retirement_eligible_resume_ledger.restricted.json"
    )
    expected_plan_path = (
        production_root / "attempts" / attempt_id / "full_batch_plan.restricted.json"
    )
    if (
        preservation_receipt_path != expected_preservation_receipt
        or final_ledger_path != expected_eligibility_ledger
        or plan_path != expected_plan_path
    ):
        raise CacheRetirementError("RETIREMENT_AUTHORITY_PATH_MISMATCH")
    require_no_symlink_ancestors(cache_root, production_root)
    require_no_symlink_ancestors(raw_root, production_root)
    require_no_symlink_ancestors(preservation_receipt_path, production_root)
    require_no_symlink_ancestors(final_ledger_path, production_root)
    require_no_symlink_ancestors(plan_path, production_root)
    if cache_root == raw_root or "raw" in cache_root.parts[-5:]:
        raise CacheRetirementError("RAW_TARGET_PROHIBITED")
    contract = core.load_orchestration_contract(contract_path)
    plan = load_json_and_sha256(
        plan_path, "BATCH_PLAN", max_bytes=512 * 1024 * 1024
    )[0]
    effective_requirements = requirements or core.production_requirements(contract)
    plan_sha = core.validate_current_batch_plan_v3(
        plan, requirements=effective_requirements
    )
    planned = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if planned is None:
        raise CacheRetirementError("BATCH_NOT_PLANNED")
    ledger = load_json_and_sha256(
        final_ledger_path, "ELIGIBILITY_LEDGER", max_bytes=128 * 1024 * 1024
    )[0]
    expected_object_keys = {
        str(row["source_object_key"]) for row in planned["objects"]
    }
    expected_authority = _derive_and_validate_ledger_authority(
        ledger=ledger,
        plan=plan,
        effective_requirements=effective_requirements,
        contract=contract,
        contract_path=contract_path,
        governing_commit=governing_commit,
        environment_receipt=environment_receipt,
        supplied_runtime_authority=expected_runtime_authority,
        attempt_id=attempt_id,
        batch_id=batch_id,
        expected_object_keys=expected_object_keys,
        synthetic_test_capability=_synthetic_test_capability,
    )
    if (
        ledger.get("attempt_id") != attempt_id
        or ledger["batches"][batch_id]["state"] != "CACHE_RETIREMENT_ELIGIBLE"
    ):
        raise CacheRetirementError("BATCH_NOT_CACHE_RETIREMENT_ELIGIBLE")
    preservation, preservation_sha = load_json_and_sha256(
        preservation_receipt_path, "PRESERVATION_RECEIPT"
    )
    preservation_manifest = preservation_receipt_path.parent / "batch_preservation_manifest.restricted.tsv"
    validate_preservation_eligibility_receipt(
        preservation,
        planned_batch=planned,
        governing_commit=governing_commit,
        attempt_id=attempt_id,
        batch_id=batch_id,
        plan_sha256=plan_sha,
        expected_authority=expected_authority,
        contract=contract,
        contract_path=contract_path,
        environment_receipt=environment_receipt,
        production_root=production_root,
        preservation_manifest=preservation_manifest,
    )
    _validate_raw_retention(raw_root, planned_batch=planned)
    authorization = None
    intent_path = preservation_receipt_path.parent / "cache_retirement_intent.restricted.json"
    staged_path = preservation_receipt_path.parent / "cache_atomically_staged.restricted.json"
    retirement_staging = None
    intent = None
    intent_sha: str | None = None
    if require_authorization:
        expected_authorization_path = (
            production_root
            / "attempts"
            / attempt_id
            / "cache_retirement_authorizations"
            / f"{batch_id}.authorization.json"
        )
        if authorization_receipt_path != expected_authorization_path:
            raise CacheRetirementError("CACHE_AUTHORIZATION_PATH_MISMATCH")
        require_no_symlink_ancestors(authorization_receipt_path, production_root)
        authorization, authorization_sha = load_json_and_sha256(
            authorization_receipt_path,
            "CACHE_AUTHORIZATION",
            owner_private=True,
        )
        retirement_staging = (
            production_root
            / "attempts"
            / attempt_id
            / "retired_cache_staging"
            / f"{batch_id}.{authorization_sha}.pending"
        )
        require_no_symlink_ancestors(retirement_staging, production_root)
        if intent_path.exists() or intent_path.is_symlink():
            intent, intent_sha = load_json_and_sha256(
                intent_path, "CACHE_RETIREMENT_INTENT"
            )
            if set(intent) != INTENT_KEYS:
                raise CacheRetirementError("CACHE_RETIREMENT_INTENT_SCHEMA_MISMATCH")
            tree_sha = str(intent.get("cache_tree_sha256"))
        else:
            tree_sha = cache_tree_sha256(cache_root)
    else:
        tree_sha = cache_tree_sha256(cache_root)
    if intent is None:
        validate_preservation_coverage(
            preservation_manifest,
            production_root=production_root,
            required_roots=((cache_root, cache_root), (raw_root, raw_root)),
        )
    else:
        validate_preservation_coverage(
            preservation_manifest,
            production_root=production_root,
            required_roots=((raw_root, raw_root),),
        )
    if require_authorization:
        expected = {
            "schema_version": 2,
            "artifact_type": "lvef_c3_cache_retirement_owner_authorization_v2",
            "status": "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT",
            "authorization_scope": "EXTRACTED_CACHE_RETIREMENT",
            "owner_authorized": True,
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
            "preservation_receipt_sha256": preservation_sha,
            "cache_inventory_sha256": tree_sha,
            "launch_authority_sha256": launch_authority_sha256,
        }
        if set(authorization) != AUTH_KEYS or any(
            authorization.get(key) != value for key, value in expected.items()
        ) or finalizer.TIMESTAMP_RE.fullmatch(
            str(authorization.get("owner_authorization_date_utc"))
        ) is None:
            raise CacheRetirementError("CACHE_AUTHORIZATION_MISMATCH")
        retirement_gate = core.evaluate_cache_retirement(
            ledger,
            batch_id=batch_id,
            target_kind="extracted_cache",
            contract=contract,
            owner_authorization=authorization,
            expected_launch_authority_sha256=launch_authority_sha256,
        )
        if retirement_gate.get("authorized") is not True:
            raise CacheRetirementError("CACHE_RETIREMENT_POLICY_GATE_BLOCKED")
        expected_intent = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_retirement_intent_v1",
            "status": "AUTHORIZED_INTENT_RECORDED_NOT_RETIRED",
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "governing_commit": governing_commit,
            "preservation_receipt_sha256": preservation_sha,
            "authorization_receipt_sha256": authorization_sha,
            "cache_tree_sha256": tree_sha,
            "raw_dicom_deletion_permitted": False,
        }
        if intent is not None and intent != expected_intent:
            raise CacheRetirementError("CACHE_RETIREMENT_INTENT_MISMATCH")
        expected_staged = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_atomically_staged_v1",
            "status": "CACHE_ATOMICALLY_STAGED",
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "governing_commit": governing_commit,
            "intent_receipt_sha256": intent_sha,
            "cache_tree_sha256": tree_sha,
            "atomic_same_filesystem_rename_completed": True,
            "raw_dicom_deletion_permitted": False,
        }
        staged = None
        staged_sha: str | None = None
        if staged_path.exists() or staged_path.is_symlink():
            staged, staged_sha = load_json_and_sha256(
                staged_path, "CACHE_ATOMICALLY_STAGED_RECEIPT"
            )
            if set(staged) != STAGED_KEYS or staged != expected_staged:
                raise CacheRetirementError("CACHE_ATOMICALLY_STAGED_RECEIPT_MISMATCH")
        cache_exists = cache_root.exists() or cache_root.is_symlink()
        staging_exists = retirement_staging.exists() or retirement_staging.is_symlink()
        if staged is not None and cache_exists:
            raise CacheRetirementError("STAGED_RECEIPT_WITH_ACTIVE_CACHE")
        if intent is not None and staged is None and not cache_exists and not staging_exists:
            raise CacheRetirementError("ATOMIC_STAGING_EVIDENCE_MISSING")
        if intent is not None and staged is None and staging_exists:
            if cache_tree_sha256(retirement_staging) != tree_sha:
                raise CacheRetirementError("UNPROVEN_PARTIAL_STAGING")
            validate_preservation_coverage(
                preservation_manifest,
                production_root=production_root,
                required_roots=((retirement_staging, cache_root),),
            )
    if expected_authority["checkpoint_sha256"] != preservation["checkpoint_sha256"]:
        raise CacheRetirementError("CHECKPOINT_AUTHORITY_MISMATCH")
    return {
        "cache_root": cache_root,
        "raw_root": raw_root,
        "cache_tree_sha256": tree_sha,
        "retirement_staging": retirement_staging,
        "intent_path": intent_path,
        "intent": intent,
        "intent_sha256": intent_sha,
        "expected_intent": expected_intent if require_authorization else None,
        "staged_path": staged_path,
        "staged": staged if require_authorization else None,
        "staged_sha256": staged_sha if require_authorization else None,
        "expected_staged": expected_staged if require_authorization else None,
        "ledger": ledger,
        "planned_batch": planned,
        "preservation": preservation,
        "preservation_receipt_sha256": preservation_sha,
        "authorization_receipt_sha256": (
            authorization_sha if require_authorization else None
        ),
        "authorization": authorization if require_authorization else None,
    }


def _delete_cache_tree(root: Path) -> None:
    """Delete only the already validated exact cache tree, never following links."""
    for entry in os.scandir(root):
        path = Path(entry.path)
        if entry.is_symlink():
            raise CacheRetirementError("CACHE_TREE_SYMLINK")
        if entry.is_dir(follow_symlinks=False):
            _delete_cache_tree(path)
        elif entry.is_file(follow_symlinks=False):
            path.unlink()
        else:
            raise CacheRetirementError("CACHE_TREE_NONREGULAR")
    root.rmdir()


def classify_retirement_state(
    *, cache_exists: bool, staging_exists: bool, intent_exists: bool,
    staged_receipt_exists: bool = False,
) -> str:
    if cache_exists and staging_exists:
        raise CacheRetirementError("CACHE_AND_RETIREMENT_STAGING_BOTH_EXIST")
    if not intent_exists and not cache_exists:
        raise CacheRetirementError("CACHE_MISSING_BEFORE_RETIREMENT_INTENT")
    if not intent_exists:
        return "READY_TO_RECORD_INTENT"
    if staged_receipt_exists and cache_exists:
        raise CacheRetirementError("STAGED_RECEIPT_WITH_ACTIVE_CACHE")
    if cache_exists:
        return "INTENT_RECORDED_READY_TO_RENAME"
    if staging_exists:
        return (
            "RENAMED_OR_PARTIALLY_CLEANED_RESUME"
            if staged_receipt_exists
            else "FULL_STAGING_REQUIRES_HASH_AND_STAGED_RECEIPT"
        )
    if not staged_receipt_exists:
        raise CacheRetirementError("ATOMIC_STAGING_EVIDENCE_MISSING")
    return "PHYSICAL_RETIREMENT_COMPLETE_READY_TO_FINALIZE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--final-ledger", type=Path, required=True)
    parser.add_argument("--preservation-receipt", type=Path, required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--launch-authority-sha256", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    requirements: core.PlanRequirements | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
    allowed_production_prefix: Path = PRODUCTION_ROOT_PREFIX,
    _synthetic_test_capability: object | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.validate_only == args.execute:
        raise CacheRetirementError("EXACTLY_ONE_MODE_REQUIRED")
    context = validate_gate(
        contract_path=args.contract,
        plan_path=args.plan,
        environment_receipt=args.environment_receipt,
        production_root=args.production_root,
        attempt_id=args.attempt_id,
        batch_id=args.batch_id,
        governing_commit=args.governing_commit,
        final_ledger_path=args.final_ledger,
        preservation_receipt_path=args.preservation_receipt,
        authorization_receipt_path=args.authorization_receipt,
        launch_authority_sha256=args.launch_authority_sha256,
        require_authorization=args.execute,
        requirements=requirements,
        expected_runtime_authority=expected_runtime_authority,
        allowed_production_prefix=allowed_production_prefix,
        _synthetic_test_capability=_synthetic_test_capability,
    )
    if args.execute:
        cache_root = context["cache_root"]
        raw_root = context["raw_root"]
        staging = context["retirement_staging"]
        intent_path = context["intent_path"]
        expected_intent = context["expected_intent"]
        if context["intent"] is None:
            core.atomic_write_json_no_clobber(
                intent_path, expected_intent, attempt_id=args.attempt_id
            )
        elif context["intent"] != expected_intent:
            raise CacheRetirementError("CACHE_RETIREMENT_INTENT_MISMATCH")
        observed_intent, intent_sha = load_json_and_sha256(
            intent_path, "CACHE_RETIREMENT_INTENT"
        )
        if observed_intent != expected_intent:
            raise CacheRetirementError("CACHE_RETIREMENT_INTENT_MISMATCH")
        cache_exists = cache_root.exists() or cache_root.is_symlink()
        staging_exists = staging.exists() or staging.is_symlink()
        classify_retirement_state(
            cache_exists=cache_exists,
            staging_exists=staging_exists,
            intent_exists=True,
            staged_receipt_exists=context["staged"] is not None,
        )
        if cache_exists:
            if cache_root.is_symlink():
                raise CacheRetirementError("CACHE_ROOT_SYMLINK")
            if staging.parent.is_symlink():
                raise CacheRetirementError("RETIREMENT_STAGING_PARENT_SYMLINK")
            staging.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.rename(cache_root, staging)
            staging_exists = True
        expected_staged = dict(context["expected_staged"])
        expected_staged["intent_receipt_sha256"] = intent_sha
        staged_path = context["staged_path"]
        if context["staged"] is None:
            if not staging_exists or cache_tree_sha256(staging) != context["cache_tree_sha256"]:
                raise CacheRetirementError("ATOMIC_STAGING_HASH_NOT_PROVEN")
            core.atomic_write_json_no_clobber(
                staged_path, expected_staged, attempt_id=args.attempt_id
            )
        elif context["staged"] != expected_staged:
            raise CacheRetirementError("CACHE_ATOMICALLY_STAGED_RECEIPT_MISMATCH")
        observed_staged, staged_sha = load_json_and_sha256(
            staged_path, "CACHE_ATOMICALLY_STAGED_RECEIPT"
        )
        if observed_staged != expected_staged:
            raise CacheRetirementError("CACHE_ATOMICALLY_STAGED_RECEIPT_MISMATCH")
        if staging_exists:
            if staging.is_symlink() or not staging.is_dir():
                raise CacheRetirementError("RETIREMENT_STAGING_INVALID")
            _delete_cache_tree(staging)
        if cache_root.exists() or cache_root.is_symlink() or staging.exists() or staging.is_symlink():
            raise CacheRetirementError("CACHE_RETIREMENT_POSTCONDITION_FAILED")
        try:
            _validate_raw_retention(
                raw_root, planned_batch=context["planned_batch"]
            )
            retained_extraction_root = cache_root.parent
            retained_metadata = {
                "dicom_audit_sha256": retained_extraction_root
                / "dicom_audit.restricted.csv",
                "extraction_manifest_sha256": retained_extraction_root
                / "extraction_manifest.restricted.csv",
                "technical_disposition_manifest_sha256": retained_extraction_root
                / "technical_disposition_manifest.restricted.csv",
            }
            preservation_authority = context["preservation"]
            for key, retained_path in retained_metadata.items():
                retained_hash = (
                    production_stages.technical_disposition_manifest_sha256(
                        retained_path
                    )
                    if key == "technical_disposition_manifest_sha256"
                    else sha256_file(retained_path)
                )
                if retained_hash != preservation_authority[key]:
                    raise CacheRetirementError(
                        "RETAINED_EXTRACTION_METADATA_CHANGED"
                    )
            validate_preservation_coverage(
                args.preservation_receipt.parent
                / "batch_preservation_manifest.restricted.tsv",
                production_root=args.production_root,
                required_roots=((raw_root, raw_root),),
            )
        except CacheRetirementError:
            raise
        except Exception as exc:
            raise CacheRetirementError("RAW_RETENTION_POSTCONDITION_FAILED") from exc
        preservation = dict(context["preservation"])
        final_receipt = {
            **preservation,
            "artifact_type": "lvef_c3_batch_finalization_receipt_v3",
            "status": "PASS_BATCH_FINALIZED",
            "extracted_cache_retired": True,
            "cache_retirement_authorization_sha256": context[
                "authorization_receipt_sha256"
            ],
            "cache_tree_sha256": context["cache_tree_sha256"],
            "cache_atomically_staged_receipt_sha256": staged_sha,
            "cache_retirement_script_sha256": sha256_file(Path(__file__).resolve()),
        }
        final_receipt_path = (
            args.production_root
            / "attempts"
            / args.attempt_id
            / "batches"
            / args.batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        if final_receipt_path.exists() or final_receipt_path.is_symlink():
            if core.load_strict_json(final_receipt_path) != final_receipt:
                raise CacheRetirementError("FINALIZATION_RECEIPT_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                final_receipt_path, final_receipt, attempt_id=args.attempt_id
            )
        ledger = context["ledger"]
        batch = ledger["batches"][args.batch_id]
        transition = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": args.attempt_id,
            "batch_id": args.batch_id,
            "from_state": "CACHE_RETIREMENT_ELIGIBLE",
            "to_state": "FINALIZED",
            "status": "PASS",
            "authority": ledger["authority"],
            "input_receipt_sha256": [batch["events"][-1]["receipt_sha256"]],
            "output_manifest_sha256": sha256_file(final_receipt_path),
        }
        updated = core.apply_transition(ledger, transition)
        transition_path = final_receipt_path.parent / "cache_retirement_finalized.restricted.json"
        if transition_path.exists() or transition_path.is_symlink():
            if core.load_strict_json(transition_path) != transition:
                raise CacheRetirementError("RETIREMENT_TRANSITION_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                transition_path, transition, attempt_id=args.attempt_id
            )
        final_ledger_path = final_receipt_path.parents[1] / "final_resume_ledger.restricted.json"
        if final_ledger_path.exists() or final_ledger_path.is_symlink():
            if core.load_strict_json(final_ledger_path) != updated:
                raise CacheRetirementError("FINAL_LEDGER_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                final_ledger_path, updated, attempt_id=args.attempt_id
            )
    print("C3_CACHE_RETIREMENT_GATE=PASS")
    print(f"CACHE_RETIREMENT_EXECUTED={'YES' if args.execute else 'NO'}")
    print("RAW_DICOM_DELETION=NO")
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except CacheRetirementError as exc:
        print(f"C3_CACHE_RETIREMENT_GATE=BLOCKED_{exc.code}")
        return 78
    except Exception:
        print("C3_CACHE_RETIREMENT_GATE=BLOCKED_UNEXPECTED_SANITIZED_EXCEPTION")
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
