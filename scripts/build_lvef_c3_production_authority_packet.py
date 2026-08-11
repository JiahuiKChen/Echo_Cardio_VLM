#!/usr/bin/env python3
"""Freeze the offline C3 production authority set without exposing paths.

The input artifacts may be restricted.  This builder opens regular files with
``O_NOFOLLOW``, verifies an exact role allowlist, and writes only role, byte
count, and SHA-256 evidence.  It has no cloud, scheduler, DICOM, deletion, or
model execution path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import yaml

import build_lvef_c3_first_batch_command as first_batch_command
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


SCHEMA_VERSION = 2
ARTIFACT_TYPE = "lvef_c3_production_authority_packet_v2"
STATUS = "PASS_OFFLINE_IMPLEMENTATION_LOCK_UNAUTHORIZED"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_RE = re.compile(r"^lvef_c3_phase1ee_[a-z0-9][a-z0-9_-]{5,63}$")

REQUIRED_ROLES = frozenset(
    {
        "selected_study_manifest",
        "selected_source_manifest",
        "selected_source_metadata_receipt",
        "split_map",
        "batch_plan",
        "orchestration_contract",
        "downloader",
        "dicom_audit_and_extractor",
        "echoprime_wrapper",
        "checkpoint",
        "python_executable",
        "crc32c_python_executable",
        "crc32c_worker",
        "environment_receipt",
        "environment_receipt_capture",
        "execution_environment",
        "gcloud_executable",
        "gcloud_resolution_receipt",
        "cloudsdk_config_receipt",
        "state_machine_schema",
        "resume_ledger_schema",
        "preservation_policy",
        "cache_retirement_gate",
        "batch_preservation_producer",
        "finalizer",
        "aggregate_export_policy",
        "scheduler_common",
        "scheduler_dispatch_authorization_validator",
        "launch_authority_builder",
        "owner_authorization_builder",
        "prior_batch_finalization_validator",
        "scheduler_dispatcher",
        "scheduler_batch_runner",
        "scheduler_finalizer",
        "future_command_block",
        "post_expansion_capacity_summary",
        "authority_packet_builder",
        "control_plane_preparer",
    }
)

AUTHORIZATION_SCOPES = (
    "first_batch_dicom_body_transfer",
    "remaining_batch_dicom_body_transfer",
    "dicom_audit_and_extraction",
    "echoprime_inference",
    "preservation_and_cache_retirement",
    "model_fitting",
    "confirmatory_test_access",
)

EXECUTION_ATTESTATIONS = {
    "cloud_requests": 0,
    "object_listing_repeated": False,
    "storage_audit_repeated": False,
    "scheduler_jobs_submitted": 0,
    "dicom_bodies_downloaded": 0,
    "real_dicom_extraction": False,
    "echoprime_inference": False,
    "model_fitting": False,
    "confirmatory_performance_accessed": False,
}

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "attempt_id",
        "created_at_utc",
        "governing_commit",
        "authority",
        "semantic_validation",
        "authorization_scopes",
        "execution_attestations",
        "full_c3_status",
    }
)

SEMANTIC_VALIDATION_KEYS = frozenset(
    {
        "checkout_authority_verified",
        "tracked_role_bindings_verified",
        "contract_semantics_verified",
        "manifest_hashes_verified",
        "batch_plan_schema_verified",
        "batch_plan_exact_rederivation_verified",
        "checkpoint_and_environment_verified",
        "crc32c_external_runtime_verified",
        "execution_environment_verified",
        "gcloud_resolution_authority_verified",
        "state_and_resume_schemas_verified",
        "preservation_policy_verified",
        "capacity_summary_verified",
        "scheduler_scripts_syntax_verified",
        "scheduler_spool_portability_verified",
        "aggregate_export_policy_bound",
        "future_command_marked_unexecuted",
    }
)

TRACKED_ROLE_PATHS = {
    "orchestration_contract": "configs/lvef_c3_orchestration_v2.yaml",
    "downloader": "scripts/lvef_c3_orchestration_core.py",
    "dicom_audit_and_extractor": "scripts/lvef_c3_production_stages.py",
    "echoprime_wrapper": "scripts/lvef_c3_production_stages.py",
    "state_machine_schema": "configs/lvef_c3_state_machine_v2.json",
    "resume_ledger_schema": "configs/lvef_c3_resume_ledger_v2.json",
    "preservation_policy": "configs/lvef_c3_preservation_policy_v2.yaml",
    "cache_retirement_gate": "scripts/retire_lvef_c3_extracted_cache_v2.py",
    "batch_preservation_producer": "scripts/preserve_lvef_c3_production_batch.py",
    "finalizer": "scripts/finalize_lvef_c3_production.py",
    "aggregate_export_policy": "configs/lvef_multitask_safe_export_policy.yaml",
    "scheduler_common": "scripts/lvef_c3_production_scheduler_common.sh",
    "scheduler_dispatch_authorization_validator": "scripts/validate_lvef_c3_dispatch_authorization.py",
    "launch_authority_builder": "scripts/build_lvef_c3_production_launch_authority.py",
    "owner_authorization_builder": "scripts/build_lvef_c3_owner_authorization_receipt.py",
    "prior_batch_finalization_validator": "scripts/validate_lvef_c3_prior_batch_finalization.py",
    "scheduler_dispatcher": "scripts/scc_dispatch_lvef_c3_production_v2.sh",
    "scheduler_batch_runner": "scripts/scc_run_lvef_c3_production_batch_v2.sh",
    "scheduler_finalizer": "scripts/scc_finalize_lvef_c3_production_v2.sh",
    "authority_packet_builder": "scripts/build_lvef_c3_production_authority_packet.py",
    "control_plane_preparer": "scripts/prepare_lvef_c3_production_control_plane.py",
    "environment_receipt_capture": "scripts/capture_lvef_c3_production_environment.py",
    "crc32c_worker": "scripts/lvef_c3_crc32c_worker.py",
}

GCLOUD_RESOLUTION_KEYS = frozenset(
    {
        "audit",
        "credential_material_accessed",
        "expected_version",
        "executable_sha256",
        "module_name",
        "resolution_source",
        "retained_archive_sha256",
        "retained_tar_payload_sha256",
        "selected_executable",
        "status",
        "version",
    }
)
PINNED_GCLOUD_VERSION = "579.0.0"
PINNED_GCLOUD_ROOT = "/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0"
PINNED_GCLOUD_ARCHIVE_SHA256 = (
    "a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
)
PINNED_GCLOUD_TAR_PAYLOAD_SHA256 = (
    "f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
)

RUNTIME_ENVIRONMENT_KEYS = frozenset(
    {
        "LVEF_C3_GOVERNING_COMMIT",
        "LVEF_C3_ATTEMPT_ID",
        "LVEF_C3_ORCHESTRATION_CONTRACT",
        "LVEF_C3_ORCHESTRATION_CONTRACT_SHA256",
        "LVEF_C3_BATCH_PLAN",
        "LVEF_C3_BATCH_PLAN_SHA256",
        "LVEF_C3_PRODUCTION_ROOT",
        "LVEF_C3_PYTHON",
        "LVEF_C3_PYTHON_SHA256",
        "LVEF_C3_ENVIRONMENT_RECEIPT",
        "LVEF_C3_ENVIRONMENT_RECEIPT_SHA256",
        "LVEF_C3_CRC32C_PYTHON",
        "LVEF_C3_CRC32C_PYTHON_SHA256",
        "LVEF_C3_CRC32C_WORKER",
        "LVEF_C3_CRC32C_WORKER_SHA256",
        "LVEF_C3_CRC32C_DISTRIBUTION_SHA256",
        "LVEF_C3_CHECKPOINT",
        "LVEF_C3_CHECKPOINT_SHA256",
        "LVEF_C3_GCLOUD_BINARY",
        "LVEF_C3_GCLOUD_BINARY_SHA256",
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT",
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256",
        "LVEF_C3_CLOUDSDK_CONFIG",
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT",
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256",
        "LVEF_C3_GCP_BILLING_PROJECT",
        "LVEF_C3_BATCH_LEDGER_ROOT",
        "LVEF_C3_DISPATCH_AUTHORIZATION_ROOT",
        "LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT",
        "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT",
        "LVEF_C3_EXTRACTION_WORKERS",
        "LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT",
        "LVEF_C3_EMBEDDING_BATCH_SIZE",
        "LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT",
        "LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT",
        "LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT",
    }
)


class AuthorityPacketError(ValueError):
    """Fail-closed authority-packet error with an aggregate-safe code."""


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AuthorityPacketError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def read_regular_nofollow(path: Path) -> bytes:
    require_no_symlink_ancestors(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AuthorityPacketError("AUTHORITY_NOT_REGULAR_NOFOLLOW_FILE") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AuthorityPacketError("AUTHORITY_NOT_REGULAR_NOFOLLOW_FILE")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def require_no_symlink_ancestors(path: Path) -> None:
    """Reject path redirection through any non-system lexical ancestor."""
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise AuthorityPacketError("AUTHORITY_PATH_NOT_ABSOLUTE")
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise AuthorityPacketError("AUTHORITY_PATH_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(metadata.st_mode):
            # macOS exposes /var as a stable system alias to /private/var;
            # synthetic tests use tempfile there. SCC authority paths never
            # rely on this exception.
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise AuthorityPacketError("AUTHORITY_PATH_SYMLINK_ANCESTOR")
        if not stat.S_ISDIR(metadata.st_mode):
            raise AuthorityPacketError("AUTHORITY_PATH_ANCESTOR_NOT_DIRECTORY")


def _parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise AuthorityPacketError("ARTIFACT_ARGUMENT_INVALID")
    role, raw_path = value.split("=", 1)
    if role not in REQUIRED_ROLES or not raw_path:
        raise AuthorityPacketError("ARTIFACT_ROLE_INVALID")
    return role, Path(raw_path)


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AuthorityPacketError("CHECKOUT_GIT_INSPECTION_FAILED")
    return completed.stdout.strip()


def _validate_checkout(checkout: Path, governing_commit: str) -> Path:
    require_no_symlink_ancestors(checkout / ".authority_leaf")
    if checkout.is_symlink() or not checkout.is_dir():
        raise AuthorityPacketError("CHECKOUT_NOT_REGULAR_DIRECTORY")
    root = checkout.resolve(strict=True)
    if (
        _git(root, "rev-parse", "--show-toplevel") != str(root)
        or _git(root, "branch", "--show-current")
        != "codex/lvef-multitask-revalidation"
        or _git(root, "rev-parse", "HEAD") != governing_commit
        or _git(root, "status", "--porcelain", "--untracked-files=no")
    ):
        raise AuthorityPacketError("CHECKOUT_AUTHORITY_MISMATCH")
    return root


def _validate_tracked_role_bindings(
    checkout: Path, artifacts: Mapping[str, Path]
) -> None:
    for role, relative in TRACKED_ROLE_PATHS.items():
        expected = (checkout / relative).resolve(strict=True)
        observed = artifacts[role].resolve(strict=True)
        if observed != expected:
            raise AuthorityPacketError("TRACKED_ROLE_PATH_MISMATCH")
        if _git(checkout, "ls-files", "--error-unmatch", relative) != relative:
            raise AuthorityPacketError("TRACKED_ROLE_NOT_TRACKED")


def _strict_yaml(path: Path) -> Mapping[str, Any]:
    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False):
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise AuthorityPacketError("YAML_DUPLICATE_KEY")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct
    )
    value = yaml.load(read_regular_nofollow(path).decode("utf-8"), Loader=UniqueLoader)
    if not isinstance(value, Mapping):
        raise AuthorityPacketError("YAML_ROOT_NOT_MAPPING")
    return value


def _validate_preservation_policy(policy: Mapping[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "policy_id",
        "status",
        "source_authority",
        "retention",
        "cache_retirement_prerequisites",
        "finalization",
        "provenance_fields",
        "authorization",
    }
    if set(policy) != expected_top:
        raise AuthorityPacketError("PRESERVATION_POLICY_SCHEMA_NOT_CLOSED")
    expected_retention = {
        "raw_dicom_deletion_enabled",
        "raw_dicoms_retained_during_active_analysis",
        "extracted_cache_retirement_implemented",
        "extracted_cache_retirement_authorized_by_default",
        "extracted_cache_retirement_scope",
        "dicom_extraction_metadata_retained",
        "extracted_cache_owner_authorization_required",
        "failed_attempt_evidence_preserved",
        "overwrite_prior_attempt_permitted",
    }
    if set(policy.get("retention", {})) != expected_retention:
        raise AuthorityPacketError("PRESERVATION_RETENTION_SCHEMA_NOT_CLOSED")
    if (
        policy.get("schema_version") != 2
        or policy.get("policy_id") != "lvef_c3_prospective_preservation_v2"
        or policy.get("status") != "IMPLEMENTED_UNAUTHORIZED"
        or policy["retention"].get("raw_dicom_deletion_enabled") is not False
        or policy["retention"].get("raw_dicoms_retained_during_active_analysis") is not True
        or policy["retention"].get("extracted_cache_retirement_implemented") is not True
        or policy["retention"].get("extracted_cache_retirement_authorized_by_default") is not False
        or policy["retention"].get("extracted_cache_retirement_scope")
        != "EXTRACTED_NPZ_CLIP_SUBTREE_ONLY"
        or policy["retention"].get("dicom_extraction_metadata_retained") is not True
        or policy["retention"].get("extracted_cache_owner_authorization_required") is not True
        or policy["retention"].get("failed_attempt_evidence_preserved") is not True
        or policy["retention"].get("overwrite_prior_attempt_permitted") is not False
        or policy["finalization"].get("expected_batches") != 19
        or policy["finalization"].get("expected_selected_studies") != 4_530
        or policy["finalization"].get("expected_selected_subjects") != 4_530
        or policy["finalization"].get("expected_source_objects") != 335_984
        or policy["finalization"].get("expected_source_bytes") != 1_216_569_133_322
        or policy["finalization"].get("symlinks_allowed") is not False
        or any(value is not False for value in policy["authorization"].values())
    ):
        raise AuthorityPacketError("PRESERVATION_POLICY_SEMANTICS_INVALID")


def _validate_gcloud_resolution_authority(
    receipt_path: Path, executable_path: Path
) -> None:
    receipt = core.load_strict_json(receipt_path)
    if not isinstance(receipt, Mapping) or set(receipt) != GCLOUD_RESOLUTION_KEYS:
        raise AuthorityPacketError("GCLOUD_RESOLUTION_RECEIPT_SCHEMA_NOT_CLOSED")
    executable = executable_path.resolve(strict=True)
    pinned_root = Path(PINNED_GCLOUD_ROOT).resolve(strict=True)
    try:
        executable.relative_to(pinned_root)
    except ValueError as exc:
        raise AuthorityPacketError("GCLOUD_EXECUTABLE_NOT_PINNED_INSTALL") from exc
    if (
        executable_path.is_symlink()
        or not executable_path.is_file()
        or receipt.get("audit") != "lvef_scc_gcloud_resolution"
        or receipt.get("status") != "PASS"
        or receipt.get("credential_material_accessed") is not False
        or receipt.get("expected_version") != PINNED_GCLOUD_VERSION
        or receipt.get("version") != PINNED_GCLOUD_VERSION
        or receipt.get("selected_executable") != str(executable)
        or receipt.get("executable_sha256") != core.sha256_file(executable)
        or receipt.get("retained_archive_sha256")
        != PINNED_GCLOUD_ARCHIVE_SHA256
        or receipt.get("retained_tar_payload_sha256")
        != PINNED_GCLOUD_TAR_PAYLOAD_SHA256
        or receipt.get("module_name") is not None
        or receipt.get("resolution_source") != "COMMON_SELF_CONTAINED_INSTALL"
    ):
        raise AuthorityPacketError("GCLOUD_RESOLUTION_RECEIPT_NOT_AUTHORITATIVE")


def _require_owner_private(path: Path, code: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise AuthorityPacketError(f"{code}_NOT_REGULAR")
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise AuthorityPacketError(f"{code}_NOT_OWNER_PRIVATE")


def _parse_execution_environment(path: Path) -> Mapping[str, str]:
    _require_owner_private(path, "EXECUTION_ENVIRONMENT")
    result: dict[str, str] = {}
    for line in read_regular_nofollow(path).decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([A-Za-z0-9_@%+,./:=-]+)", line)
        if match is None:
            raise AuthorityPacketError("EXECUTION_ENVIRONMENT_ASSIGNMENT_NOT_LITERAL")
        key, value = match.groups()
        if key in result:
            raise AuthorityPacketError("EXECUTION_ENVIRONMENT_KEY_DUPLICATE")
        result[key] = value
    if set(result) != RUNTIME_ENVIRONMENT_KEYS:
        raise AuthorityPacketError("EXECUTION_ENVIRONMENT_SCHEMA_NOT_CLOSED")
    return result


def _validate_execution_environment(
    path: Path, *, governing_commit: str, attempt_id: str,
    artifacts: Mapping[str, Path], contract: Mapping[str, Any], plan_hash: str
) -> None:
    values = _parse_execution_environment(path)
    expected_files = {
        "LVEF_C3_ORCHESTRATION_CONTRACT": "orchestration_contract",
        "LVEF_C3_BATCH_PLAN": "batch_plan",
        "LVEF_C3_PYTHON": "python_executable",
        "LVEF_C3_ENVIRONMENT_RECEIPT": "environment_receipt",
        "LVEF_C3_CRC32C_PYTHON": "crc32c_python_executable",
        "LVEF_C3_CRC32C_WORKER": "crc32c_worker",
        "LVEF_C3_CHECKPOINT": "checkpoint",
        "LVEF_C3_GCLOUD_BINARY": "gcloud_executable",
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT": "gcloud_resolution_receipt",
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT": "cloudsdk_config_receipt",
    }
    expected_hashes = {
        "LVEF_C3_ORCHESTRATION_CONTRACT_SHA256": core.sha256_file(
            artifacts["orchestration_contract"]
        ),
        "LVEF_C3_BATCH_PLAN_SHA256": plan_hash,
        "LVEF_C3_PYTHON_SHA256": core.sha256_file(artifacts["python_executable"]),
        "LVEF_C3_ENVIRONMENT_RECEIPT_SHA256": core.sha256_file(
            artifacts["environment_receipt"]
        ),
        "LVEF_C3_CRC32C_PYTHON_SHA256": core.sha256_file(
            artifacts["crc32c_python_executable"]
        ),
        "LVEF_C3_CRC32C_WORKER_SHA256": core.sha256_file(
            artifacts["crc32c_worker"]
        ),
        "LVEF_C3_CHECKPOINT_SHA256": core.sha256_file(artifacts["checkpoint"]),
        "LVEF_C3_GCLOUD_BINARY_SHA256": core.sha256_file(
            artifacts["gcloud_executable"]
        ),
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256": core.sha256_file(
            artifacts["gcloud_resolution_receipt"]
        ),
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256": core.sha256_file(
            artifacts["cloudsdk_config_receipt"]
        ),
    }
    if (
        values["LVEF_C3_GOVERNING_COMMIT"] != governing_commit
        or values["LVEF_C3_ATTEMPT_ID"] != attempt_id
        or values["LVEF_C3_PRODUCTION_ROOT"] != contract["storage"]["production_root"]
        or any(
            Path(values[key]).resolve(strict=True) != artifact.resolve(strict=True)
            for key, role in expected_files.items()
            for artifact in (artifacts[role],)
        )
        or any(values[key] != expected for key, expected in expected_hashes.items())
        or values["LVEF_C3_CLOUDSDK_CONFIG_RECEIPT"]
        != values["LVEF_C3_GCLOUD_RESOLUTION_RECEIPT"]
        or values["LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256"]
        != values["LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256"]
        or values["LVEF_C3_CRC32C_DISTRIBUTION_SHA256"]
        != str(
            core.load_strict_json(artifacts["environment_receipt"])[
                "google_crc32c_distribution_sha256"
            ]
        )
        or not values["LVEF_C3_EXTRACTION_WORKERS"].isdigit()
        or int(values["LVEF_C3_EXTRACTION_WORKERS"]) < 1
        or not values["LVEF_C3_EMBEDDING_BATCH_SIZE"].isdigit()
        or int(values["LVEF_C3_EMBEDDING_BATCH_SIZE"]) < 1
    ):
        raise AuthorityPacketError("EXECUTION_ENVIRONMENT_AUTHORITY_MISMATCH")
    production_root = Path(values["LVEF_C3_PRODUCTION_ROOT"])
    for key in (
        "LVEF_C3_BATCH_LEDGER_ROOT",
        "LVEF_C3_DISPATCH_AUTHORIZATION_ROOT",
        "LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT",
        "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT",
        "LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT",
        "LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT",
        "LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT",
        "LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT",
    ):
        candidate = Path(values[key])
        try:
            candidate.relative_to(production_root)
        except ValueError as exc:
            raise AuthorityPacketError("EXECUTION_OUTPUT_PATH_OUTSIDE_PRODUCTION_ROOT") from exc
    cloudsdk = Path(values["LVEF_C3_CLOUDSDK_CONFIG"])
    if (
        cloudsdk.is_symlink()
        or not cloudsdk.is_dir()
        or cloudsdk.stat().st_uid != os.getuid()
        or not core.owner_private_directory_mode_ok(cloudsdk.stat().st_mode)
    ):
        raise AuthorityPacketError("EXECUTION_CLOUDSDK_CONFIG_NOT_PRIVATE")
    _require_owner_private(
        cloudsdk / "application_default_credentials.json", "EXECUTION_ADC"
    )


def _validate_pretransfer_command_binding(
    pretransfer_value: Mapping[str, Any], command_path: Path
) -> None:
    """Prove the packet's command is the command frozen by Phase 1E-F."""
    authority = pretransfer_value.get("authority")
    expected = authority.get("future_first_batch_command") if isinstance(
        authority, Mapping
    ) else None
    payload = read_regular_nofollow(command_path)
    observed = {
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if expected != observed:
        raise AuthorityPacketError("PRETRANSFER_COMMAND_BINDING_MISMATCH")


def validate_artifact_semantics(
    *, governing_commit: str, attempt_id: str, checkout_root: Path,
    artifacts: Mapping[str, Path]
) -> Mapping[str, bool]:
    """Validate the complete production authority, not just file digests."""

    checkout = _validate_checkout(checkout_root, governing_commit)
    _validate_tracked_role_bindings(checkout, artifacts)
    contract_path = artifacts["orchestration_contract"]
    contract = core.load_orchestration_contract(contract_path)
    requirements = core.production_requirements(contract)

    expected_external_hashes = {
        "selected_study_manifest": core.EXPECTED_SELECTED_MANIFEST_SHA256,
        "selected_source_manifest": core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256,
        "split_map": core.EXPECTED_SPLIT_MAP_SHA256,
        "checkpoint": core.EXPECTED_CHECKPOINT_SHA256,
    }
    for role, expected in expected_external_hashes.items():
        if core.sha256_file(artifacts[role]) != expected:
            raise AuthorityPacketError("FROZEN_EXTERNAL_AUTHORITY_HASH_MISMATCH")
    checkpoint = artifacts["checkpoint"]
    if checkpoint.name != "echo_prime_encoder.pt" or checkpoint.stat().st_size != 138_642_379:
        raise AuthorityPacketError("CHECKPOINT_IDENTITY_INVALID")

    plan = core.load_strict_json(artifacts["batch_plan"])
    if not isinstance(plan, Mapping):
        raise AuthorityPacketError("BATCH_PLAN_NOT_MAPPING")
    plan_hash = core.validate_batch_plan(plan, requirements=requirements)
    core.validate_plan_authority_against_contract(
        plan["authority"], contract=contract, contract_path=contract_path
    )
    plan_authority = plan["authority"]
    exact_plan_authorities = {
        "git_commit": governing_commit,
        "source_metadata_sha256": core.sha256_file(
            artifacts["selected_source_metadata_receipt"]
        ),
        "environment_receipt_sha256": core.sha256_file(
            artifacts["environment_receipt"]
        ),
        "crc32c_python_executable_sha256": core.sha256_file(
            artifacts["crc32c_python_executable"]
        ),
        "crc32c_worker_sha256": core.sha256_file(artifacts["crc32c_worker"]),
    }
    if any(plan_authority.get(key) != value for key, value in exact_plan_authorities.items()):
        raise AuthorityPacketError("BATCH_PLAN_RUNTIME_AUTHORITY_MISMATCH")

    selected_rows = core._read_csv_rows(artifacts["selected_study_manifest"])
    source_rows = core.reconcile_selected_source_metadata(
        core._read_csv_rows(artifacts["selected_source_manifest"]),
        core._read_jsonl_rows(artifacts["selected_source_metadata_receipt"]),
        release=str(contract["cohort"]["release"]),
    )
    rebuilt = core.build_immutable_batch_plan(
        selected_rows,
        source_rows,
        core._read_csv_rows(artifacts["split_map"]),
        requirements=requirements,
        authority=plan_authority,
    )
    if core.canonical_json_sha256(rebuilt) != plan_hash or rebuilt != plan:
        raise AuthorityPacketError("BATCH_PLAN_NOT_EXACT_REDERIVATION")

    stages.validate_checkpoint_and_environment(
        checkpoint,
        artifacts["environment_receipt"],
        crc32c_python=artifacts["crc32c_python_executable"],
        crc32c_worker=artifacts["crc32c_worker"],
    )
    environment = core.load_strict_json(artifacts["environment_receipt"])
    if not isinstance(environment, Mapping) or environment.get(
        "python_executable_sha256"
    ) != core.sha256_file(artifacts["python_executable"]) or environment.get(
        "governing_commit"
    ) != governing_commit:
        raise AuthorityPacketError("PYTHON_ENVIRONMENT_BINDING_MISMATCH")
    if (
        environment.get("crc32c_python_executable_sha256")
        != core.sha256_file(artifacts["crc32c_python_executable"])
        or environment.get("crc32c_worker_sha256")
        != core.sha256_file(artifacts["crc32c_worker"])
        or plan_authority.get("crc32c_distribution_sha256")
        != environment.get("google_crc32c_distribution_sha256")
    ):
        raise AuthorityPacketError("CRC32C_ENVIRONMENT_BINDING_MISMATCH")

    _validate_gcloud_resolution_authority(
        artifacts["gcloud_resolution_receipt"], artifacts["gcloud_executable"]
    )
    for role in (
        "batch_plan",
        "selected_source_manifest",
        "selected_source_metadata_receipt",
        "environment_receipt",
        "gcloud_resolution_receipt",
        "cloudsdk_config_receipt",
        "execution_environment",
        "future_command_block",
    ):
        _require_owner_private(artifacts[role], role.upper())
    _validate_execution_environment(
        artifacts["execution_environment"],
        governing_commit=governing_commit,
        attempt_id=attempt_id,
        artifacts=artifacts,
        contract=contract,
        plan_hash=plan_hash,
    )

    state_schema = core.load_strict_json(artifacts["state_machine_schema"])
    resume_schema = core.load_strict_json(artifacts["resume_ledger_schema"])
    core.validate_state_machine_schema(state_schema)
    core.validate_resume_ledger_schema(resume_schema)
    _validate_preservation_policy(_strict_yaml(artifacts["preservation_policy"]))

    capacity_summary = core.load_strict_json(artifacts["post_expansion_capacity_summary"])
    try:
        pretransfer.validate_aggregate_output(capacity_summary)
    except pretransfer.Phase1EFPretransferError as exc:
        raise AuthorityPacketError("CAPACITY_EVIDENCE_NOT_PASS") from exc
    if (
        capacity_summary.get("governing_commit") != governing_commit
        or not pretransfer.launch_ready(capacity_summary)
    ):
        raise AuthorityPacketError("CAPACITY_EVIDENCE_NOT_PASS")
    _validate_pretransfer_command_binding(
        capacity_summary, artifacts["future_command_block"]
    )

    scheduler_roles = (
        "scheduler_common",
        "scheduler_dispatcher",
        "scheduler_batch_runner",
        "scheduler_finalizer",
    )
    for role in scheduler_roles:
        completed = subprocess.run(
            ["bash", "-n", str(artifacts[role])], capture_output=True, check=False
        )
        if completed.returncode != 0:
            raise AuthorityPacketError("SCHEDULER_BASH_SYNTAX_INVALID")
        source = read_regular_nofollow(artifacts[role]).decode("utf-8")
        if "qsub -V" in source:
            raise AuthorityPacketError("SCHEDULER_INHERITED_ENVIRONMENT_FORBIDDEN")
    scheduler_common = read_regular_nofollow(artifacts["scheduler_common"]).decode("utf-8")
    if (
        "BASH_SOURCE[0]" in scheduler_common
        or "canonical_worktree='/restricted/project/mimicecho/code/"
        "Echo_Cardio_VLM_lvef_multitask'" not in scheduler_common
    ):
        raise AuthorityPacketError("SCHEDULER_SPOOL_PORTABILITY_NOT_BOUND")
    execution_values = _parse_execution_environment(artifacts["execution_environment"])
    attempt_root = artifacts["execution_environment"].parent.parent
    try:
        expected_future = first_batch_command.render(
            governing_commit=governing_commit,
            attempt_id=attempt_id,
            dispatcher=artifacts["scheduler_dispatcher"],
            execution_environment=artifacts["execution_environment"],
            launch_authority=(
                attempt_root
                / "authority"
                / "lvef_c3_production_launch_authority.restricted.json"
            ),
            dispatch_authorization=(
                Path(execution_values["LVEF_C3_DISPATCH_AUTHORIZATION_ROOT"])
                / "FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json"
            ),
            body_authorization=(
                Path(execution_values["LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT"])
                / "c3_batch_000.authorization.json"
            ),
        )
        first_batch_command.validate_exact(
            artifacts["future_command_block"], expected=expected_future
        )
    except first_batch_command.FirstBatchCommandError as exc:
        raise AuthorityPacketError("FUTURE_COMMAND_NOT_EXACT_UNEXECUTED") from exc

    return {key: True for key in SEMANTIC_VALIDATION_KEYS}


def build_packet(
    *,
    governing_commit: str,
    attempt_id: str,
    artifacts: Mapping[str, Path],
    semantic_validation: Mapping[str, bool],
    full_c3_status: str = "NO_GO_PENDING_OWNER_REVIEW",
) -> Mapping[str, Any]:
    if not COMMIT_RE.fullmatch(governing_commit):
        raise AuthorityPacketError("GOVERNING_COMMIT_INVALID")
    if not ATTEMPT_RE.fullmatch(attempt_id):
        raise AuthorityPacketError("ATTEMPT_ID_INVALID")
    if set(artifacts) != set(REQUIRED_ROLES):
        raise AuthorityPacketError("ARTIFACT_ROLE_SET_NOT_EXACT")
    if set(semantic_validation) != set(SEMANTIC_VALIDATION_KEYS) or any(
        value is not True for value in semantic_validation.values()
    ):
        raise AuthorityPacketError("SEMANTIC_VALIDATION_NOT_ALL_PASS")
    if full_c3_status not in {
        "NO_GO_PENDING_OWNER_REVIEW",
        "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }:
        raise AuthorityPacketError("FULL_C3_STATUS_INVALID")

    authority: dict[str, Mapping[str, Any]] = {}
    for role in sorted(artifacts):
        payload = read_regular_nofollow(artifacts[role])
        if not payload:
            raise AuthorityPacketError("AUTHORITY_FILE_EMPTY")
        authority[role] = {
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "status": STATUS,
        "attempt_id": attempt_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "governing_commit": governing_commit,
        "authority": authority,
        "semantic_validation": dict(sorted(semantic_validation.items())),
        "authorization_scopes": {
            scope: False for scope in AUTHORIZATION_SCOPES
        },
        "execution_attestations": dict(EXECUTION_ATTESTATIONS),
        "full_c3_status": full_c3_status,
    }


def validate_packet(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != set(TOP_LEVEL_KEYS):
        raise AuthorityPacketError("PACKET_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != ARTIFACT_TYPE
        or value.get("status") != STATUS
        or value.get("full_c3_status") not in {
            "NO_GO_PENDING_OWNER_REVIEW",
            "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
        }
    ):
        raise AuthorityPacketError("PACKET_AUTHORITY_FIELDS_INVALID")
    if not COMMIT_RE.fullmatch(str(value.get("governing_commit", ""))):
        raise AuthorityPacketError("PACKET_COMMIT_INVALID")
    if not ATTEMPT_RE.fullmatch(str(value.get("attempt_id", ""))):
        raise AuthorityPacketError("PACKET_ATTEMPT_INVALID")
    authority = value.get("authority")
    if not isinstance(authority, Mapping) or set(authority) != set(REQUIRED_ROLES):
        raise AuthorityPacketError("PACKET_AUTHORITY_ROLE_SET_INVALID")
    for item in authority.values():
        if (
            not isinstance(item, Mapping)
            or set(item) != {"size_bytes", "sha256"}
            or not isinstance(item["size_bytes"], int)
            or isinstance(item["size_bytes"], bool)
            or item["size_bytes"] <= 0
            or not SHA256_RE.fullmatch(str(item["sha256"]))
        ):
            raise AuthorityPacketError("PACKET_AUTHORITY_ITEM_INVALID")
    semantic = value.get("semantic_validation")
    if (
        not isinstance(semantic, Mapping)
        or set(semantic) != set(SEMANTIC_VALIDATION_KEYS)
        or any(item is not True for item in semantic.values())
    ):
        raise AuthorityPacketError("PACKET_SEMANTIC_VALIDATION_INVALID")
    scopes = value.get("authorization_scopes")
    if (
        not isinstance(scopes, Mapping)
        or set(scopes) != set(AUTHORIZATION_SCOPES)
        or any(item is not False for item in scopes.values())
    ):
        raise AuthorityPacketError("PACKET_AUTHORIZATION_SCOPE_INVALID")
    attestations = value.get("execution_attestations")
    if attestations != EXECUTION_ATTESTATIONS:
        raise AuthorityPacketError("PACKET_EXECUTION_ATTESTATION_INVALID")


def write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.is_symlink() or path.exists():
        raise AuthorityPacketError("OUTPUT_ALREADY_EXISTS")
    require_no_symlink_ancestors(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise AuthorityPacketError("OUTPUT_PARENT_INVALID")
    parent_metadata = path.parent.stat(follow_symlinks=False)
    if parent_metadata.st_uid != os.getuid() or stat.S_IMODE(parent_metadata.st_mode) & 0o077:
        raise AuthorityPacketError("OUTPUT_PARENT_NOT_OWNER_PRIVATE")
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        pairs = [_parse_named_path(value) for value in args.artifact]
        roles = [role for role, _ in pairs]
        if len(roles) != len(set(roles)):
            raise AuthorityPacketError("ARTIFACT_ROLE_DUPLICATE")
        semantic = validate_artifact_semantics(
            governing_commit=args.governing_commit,
            attempt_id=args.attempt_id,
            checkout_root=args.checkout_root,
            artifacts=dict(pairs),
        )
        capacity_summary = core.load_strict_json(
            dict(pairs)["post_expansion_capacity_summary"]
        )
        launch_ready = pretransfer.launch_ready(capacity_summary)
        packet = build_packet(
            governing_commit=args.governing_commit,
            attempt_id=args.attempt_id,
            artifacts=dict(pairs),
            semantic_validation=semantic,
            full_c3_status=(
                "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION"
                if launch_ready
                else "NO_GO_PENDING_OWNER_REVIEW"
            ),
        )
        validate_packet(packet)
        write_exclusive_json(args.output, packet)
    except (AuthorityPacketError, core.OrchestrationError, stages.ProductionStageError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "PRODUCTION_AUTHORITY_SEMANTIC_VALIDATION_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_code": "PRODUCTION_AUTHORITY_VALIDATION_FAILED",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": STATUS,
                "artifact_roles_frozen": len(REQUIRED_ROLES),
                "authorization_scopes_granted": 0,
                "semantic_validations_passed": len(SEMANTIC_VALIDATION_KEYS),
                "cloud_requests": 0,
                "scheduler_jobs_submitted": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
