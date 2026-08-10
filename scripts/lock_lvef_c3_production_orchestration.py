#!/usr/bin/env python3
"""Build an aggregate-safe, offline-only C3 production pre-transfer lock.

This module is deliberately not an execution runner.  It validates the
already-produced aggregate batch authority and the committed execution
contract, freezes the future production topology, and emits a plan-only JSON
summary.  It has no cloud, scheduler, DICOM, extraction, embedding, deletion,
or model execution path.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Mapping, Sequence

import yaml

from validate_lvef_c3_execution_contract import (
    ContractError as ExecutionContractError,
    load_contract,
    validate_structure,
)


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "lvef_c3_production_pretransfer_lock_v1"
LOCK_STATUS = "PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED"
EXPECTED_SELECTED_STUDIES = 4_530
EXPECTED_SELECTED_SUBJECTS = 4_530
EXPECTED_NORMALIZED_SOURCE_REQUESTS = 335_984
EXPECTED_SOURCE_BYTES = 1_216_569_133_322
EXPECTED_BATCH_COUNT = 19
EXPECTED_FULL_BATCH_COUNT = 18
EXPECTED_STUDIES_PER_FULL_BATCH = 250
EXPECTED_FINAL_BATCH_STUDIES = 30
EXPECTED_BATCH_IDS = tuple(f"c3_batch_{index:03d}" for index in range(19))
EXPECTED_BATCH_STUDY_COUNTS = (250,) * 18 + (30,)
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
EXPECTED_RELEASE = "mimic-iv-echo/1.0"
EXPECTED_BILLING_ENV = "LVEF_C3_GCP_BILLING_PROJECT"
EXPECTED_CHECKPOINT_SHA256 = (
    "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_BATCH_HEADER = (
    "production_batch",
    "n_studies",
    "n_subjects",
    "n_requested_objects",
    "n_verified_objects",
    "n_unexpected_selected_objects",
    "total_source_bytes",
    "status",
)

# These are aggregate-only, already-approved authorities.  The validator
# re-hashes them but never prints or reads row-level restricted artifacts.
ORIGINAL_AGGREGATE_AUTHORITIES: Mapping[str, tuple[int, str]] = {
    "scc_storage_inventory.summary.json": (
        2_566,
        "3e27c71285558402d546bd7e15290cbd08fbb5b1eea445d2d04bc5cc9d8df3d6",
    ),
    "c3_full_source_preflight.summary.json": (
        2_866,
        "8aaac6cbd62245184db05d47a98cd69ca5787a8caaec620e9a410ad99d0694b6",
    ),
    "c3_full_source_preflight_by_batch.csv": (
        1_136,
        "6c17d2bccf992d79023023e011431fe86da35cbab68451f7fb3ea85520abbfa1",
    ),
    "c3_full_source_cost_estimate.json": (
        1_659,
        "bad47492ca5980b91b9560c5cd385afd87a4f54b48e1a6a3fe149ae04be03285",
    ),
    "c3_full_source_preflight_safety_gate.json": (
        609,
        "d846b8d6210b50e20533bac3361c42ff5bde2bd24360ac18f6178c4c0b671c54",
    ),
    "c3_full_resource_plan.json": (
        5_261,
        "5610bd3ec3a2cf3fd4ab905946ab6f3ab25824e38d30ced1b8061f349ba3b357",
    ),
}
SUPPLEMENTAL_AGGREGATE_AUTHORITIES: Mapping[str, tuple[int, str]] = {
    "c3_autoclass_adjudication.summary.json": (
        1_604,
        "ec480c69e3a2412b18c958b43bc361db55fd9faf16dcf3ed8b25256e5ba21f1a",
    ),
    "c3_source_inventory_authority_adjudication.summary.json": (
        1_588,
        "3cce1ef791ee20b9528f38c001d4fbb5405354c20053d083502138de9647c5dd",
    ),
    "c3_cost_authority_adjudication.summary.json": (
        3_456,
        "3ac53f0aa7afbd3cd1195c78b467bb11041c87f9dc89d8ba0d64fd311c25dc56",
    ),
    "c3_autoclass_adjudication_provenance_manifest.json": (
        6_295,
        "4ab11f7c255efd192cebc612704e11ae5b855d8f3fb8710b2261f768d522b11b",
    ),
    "c3_autoclass_adjudication_safety_gate.json": (
        1_151,
        "7718e5f7aeb718f1b68973bae1bd27c3a7645d39e04414d218c616c64528b363",
    ),
    "c3_autoclass_combined_validation.summary.json": (
        1_173,
        "ec64010819b001f25147c74b8e73af9673b1525acb91ef4355414a34f3f6e73d",
    ),
}
SUPPLEMENTAL_RECEIPT_FILENAME = "c3_autoclass_combined_validation.summary.json"
SUPPLEMENTAL_RECEIPT_KEYS = {
    "schema_version",
    "status",
    "attempt_id",
    "governing_commit",
    "original_output_hash_gate_passed",
    "original_output_schema_gate_passed",
    "restricted_receipt_gate_passed",
    "autoclass_adjudication_gate_passed",
    "source_inventory_gate_passed",
    "historical_identity_limitation_preserved",
    "cost_authority_gate_passed",
    "new_output_schema_gate_passed",
    "aggregate_provenance_gate_passed",
    "aggregate_safety_gate_passed",
    "autoclass_summary_sha256",
    "source_authority_sha256",
    "cost_authority_sha256",
    "provenance_manifest_sha256",
    "safety_gate_sha256",
    "full_c3_authorized",
    "section5_run",
}
SUPPLEMENTAL_RECEIPT_TRUE_GATES = {
    "original_output_hash_gate_passed",
    "original_output_schema_gate_passed",
    "restricted_receipt_gate_passed",
    "autoclass_adjudication_gate_passed",
    "source_inventory_gate_passed",
    "historical_identity_limitation_preserved",
    "cost_authority_gate_passed",
    "new_output_schema_gate_passed",
    "aggregate_provenance_gate_passed",
    "aggregate_safety_gate_passed",
}
SUPPLEMENTAL_RECEIPT_HASH_BINDINGS = {
    "autoclass_summary_sha256": "c3_autoclass_adjudication.summary.json",
    "source_authority_sha256": (
        "c3_source_inventory_authority_adjudication.summary.json"
    ),
    "cost_authority_sha256": "c3_cost_authority_adjudication.summary.json",
    "provenance_manifest_sha256": (
        "c3_autoclass_adjudication_provenance_manifest.json"
    ),
    "safety_gate_sha256": "c3_autoclass_adjudication_safety_gate.json",
}
IMMUTABLE_AGGREGATE_AUTHORITIES: Mapping[str, tuple[int, str]] = {
    **ORIGINAL_AGGREGATE_AUTHORITIES,
    **SUPPLEMENTAL_AGGREGATE_AUTHORITIES,
}

STAGE_ORDER = (
    "source_authority",
    "exact_object_download",
    "dicom_header_audit",
    "cine_extraction",
    "clip_embedding",
    "study_pooling",
    "batch_preservation",
    "aggregate_safety",
)

FORBIDDEN_ACTIONS = (
    "cloud_object_listing",
    "cloud_object_body_download",
    "scheduler_submission",
    "dicom_pixel_decode",
    "cine_extraction",
    "echoprime_inference",
    "embedding_generation",
    "model_fitting",
    "prediction_generation",
    "confirmatory_performance_access",
    "raw_dicom_deletion",
    "extracted_cache_deletion",
)

LOCK_TOP_LEVEL_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "execution_mode",
    "execution_authorized",
    "submission_authorized",
    "source_body_download_authorized",
    "scientific_execution_authorized",
    "governing_commit",
    "authority_verification",
    "authority_hashes",
    "immutable_aggregate_authorities",
    "supplemental_validation_receipt",
    "cohort",
    "batch_topology",
    "downloader_contract",
    "stage_receipt_and_resume_contract",
    "storage_contract",
    "preservation_and_finalization_contract",
    "execution_stubs",
    "forbidden_actions",
    "remaining_execution_blockers",
    "full_c3_status",
}
FORBIDDEN_AGGREGATE_KEYS = {
    "subject_id",
    "study_id",
    "dicom_id",
    "object_name",
    "source_locator",
    "normalized_source_locator",
    "gcs_uri",
    "local_path",
    "credential_path",
    "access_token",
    "refresh_token",
    "account_email",
    "billing_account_id",
}
FORBIDDEN_AGGREGATE_STRING_FRAGMENTS = (
    "/restricted/",
    "gs://",
    "storage.googleapis.com/",
    "bearer ",
    "ya29.",
)


class PretransferLockError(ValueError):
    """A non-row-level, safe pre-transfer lock validation failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_regular_file(path: Path, code: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise PretransferLockError(code)
    return sha256_file(path)


def _require_sha256(value: str, code: str) -> str:
    normalized = str(value).strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise PretransferLockError(code)
    return normalized


def _require_git_commit(value: str) -> str:
    normalized = str(value).strip().lower()
    if not GIT_SHA1_RE.fullmatch(normalized):
        raise PretransferLockError("GOVERNING_COMMIT_INVALID")
    return normalized


def _read_regular_text(path: Path, code: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise PretransferLockError(code)
    return path.read_text(encoding="utf-8").strip()


def _resolve_git_ref(git_dir: Path, ref_name: str) -> str:
    """Resolve a worktree ref from loose or packed Git metadata, without Git."""
    common_dir = git_dir
    commondir_path = git_dir / "commondir"
    if commondir_path.exists():
        relative = _read_regular_text(
            commondir_path, "CHECKOUT_COMMONDIR_NOT_REGULAR_FILE"
        )
        common_dir = (git_dir / relative).resolve(strict=True)
        if common_dir.is_symlink() or not common_dir.is_dir():
            raise PretransferLockError("CHECKOUT_COMMONDIR_NOT_DIRECTORY")
    loose_ref = common_dir / ref_name
    if loose_ref.exists():
        return _require_git_commit(
            _read_regular_text(loose_ref, "CHECKOUT_REF_NOT_REGULAR_FILE")
        )
    packed_refs = common_dir / "packed-refs"
    if packed_refs.exists():
        text = _read_regular_text(packed_refs, "CHECKOUT_PACKED_REFS_NOT_REGULAR_FILE")
        for line in text.splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            fields = line.split(" ", 1)
            if len(fields) == 2 and fields[1] == ref_name:
                return _require_git_commit(fields[0])
    raise PretransferLockError("CHECKOUT_BRANCH_REF_NOT_RESOLVED")


def verify_checkout_authority(checkout_root: Path, expected_commit: str) -> str:
    """Verify the exact branch and commit from checkout metadata without subprocesses."""
    expected = _require_git_commit(expected_commit)
    if checkout_root.is_symlink() or not checkout_root.is_dir():
        raise PretransferLockError("CHECKOUT_ROOT_NOT_REGULAR_DIRECTORY")
    dot_git = checkout_root / ".git"
    if dot_git.is_symlink():
        raise PretransferLockError("CHECKOUT_GIT_METADATA_SYMLINKED")
    if dot_git.is_dir():
        git_dir = dot_git.resolve(strict=True)
    elif dot_git.is_file():
        descriptor = _read_regular_text(
            dot_git, "CHECKOUT_GIT_DESCRIPTOR_NOT_REGULAR_FILE"
        )
        prefix = "gitdir: "
        if not descriptor.startswith(prefix) or "\n" in descriptor:
            raise PretransferLockError("CHECKOUT_GIT_DESCRIPTOR_INVALID")
        candidate = Path(descriptor[len(prefix):])
        if not candidate.is_absolute():
            candidate = dot_git.parent / candidate
        git_dir = candidate.resolve(strict=True)
    else:
        raise PretransferLockError("CHECKOUT_GIT_METADATA_MISSING")
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise PretransferLockError("CHECKOUT_GITDIR_NOT_DIRECTORY")
    head = _read_regular_text(git_dir / "HEAD", "CHECKOUT_HEAD_NOT_REGULAR_FILE")
    expected_ref = f"refs/heads/{EXPECTED_BRANCH}"
    if head != f"ref: {expected_ref}":
        raise PretransferLockError("CHECKOUT_BRANCH_MISMATCH")
    observed = _resolve_git_ref(git_dir, expected_ref)
    if observed != expected:
        raise PretransferLockError("CHECKOUT_COMMIT_MISMATCH")
    return observed


def _resolve_file_hash_authority(
    *,
    provided_sha256: str,
    artifact_path: Path | None,
    invalid_hash_code: str,
    missing_file_code: str,
    mismatch_code: str,
) -> tuple[str, str]:
    """Return a hash and its verification class; never accept a path symlink."""
    expected = _require_sha256(provided_sha256, invalid_hash_code)
    if artifact_path is None:
        return expected, "PROVIDED_UNVERIFIED"
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise PretransferLockError(missing_file_code)
    observed = sha256_file(artifact_path)
    if observed != expected:
        raise PretransferLockError(mismatch_code)
    return observed, "HASH_VERIFIED_FROM_ACTUAL_FILE"


def _validate_one_aggregate_authority_root(
    aggregate_root: Path,
    *,
    authorities: Mapping[str, tuple[int, str]],
) -> list[str]:
    if aggregate_root.is_symlink() or not aggregate_root.is_dir():
        raise PretransferLockError("AGGREGATE_ROOT_NOT_REGULAR_DIRECTORY")
    validated_names: list[str] = []
    for name, (expected_bytes, expected_sha256) in sorted(authorities.items()):
        if Path(name).name != name:
            raise PretransferLockError("AGGREGATE_AUTHORITY_NAME_NOT_BASENAME")
        _require_sha256(expected_sha256, "AGGREGATE_EXPECTED_SHA256_INVALID")
        path = aggregate_root / name
        if path.is_symlink() or not path.is_file():
            raise PretransferLockError("IMMUTABLE_AGGREGATE_MISSING_OR_NOT_REGULAR")
        if path.stat().st_size != expected_bytes:
            raise PretransferLockError("IMMUTABLE_AGGREGATE_BYTE_SIZE_MISMATCH")
        if sha256_file(path) != expected_sha256:
            raise PretransferLockError("IMMUTABLE_AGGREGATE_SHA256_MISMATCH")
        validated_names.append(name)
    return validated_names


def validate_immutable_aggregate_authorities(
    original_aggregate_root: Path,
    supplemental_aggregate_root: Path,
    *,
    original_authorities: Mapping[str, tuple[int, str]] = (
        ORIGINAL_AGGREGATE_AUTHORITIES
    ),
    supplemental_authorities: Mapping[str, tuple[int, str]] = (
        SUPPLEMENTAL_AGGREGATE_AUTHORITIES
    ),
) -> dict[str, Any]:
    """Validate the two immutable evidence roots by exact size and SHA-256."""
    original_names = _validate_one_aggregate_authority_root(
        original_aggregate_root, authorities=original_authorities
    )
    supplemental_names = _validate_one_aggregate_authority_root(
        supplemental_aggregate_root, authorities=supplemental_authorities
    )
    return {
        "expected_file_count": len(original_authorities) + len(supplemental_authorities),
        "validated_file_count": len(original_names) + len(supplemental_names),
        "original_expected_file_count": len(original_authorities),
        "original_validated_file_count": len(original_names),
        "supplemental_expected_file_count": len(supplemental_authorities),
        "supplemental_validated_file_count": len(supplemental_names),
        "all_size_and_sha256_checks_passed": True,
        "original_validated_filenames": original_names,
        "supplemental_validated_filenames": supplemental_names,
    }


def validate_supplemental_validation_receipt(
    supplemental_aggregate_root: Path,
    *,
    supplemental_authorities: Mapping[str, tuple[int, str]],
) -> dict[str, Any]:
    """Validate the prior closed-schema/DAG result as a subordinate receipt.

    This does not rerun the separate safe-export or DAG validator.  It verifies
    the immutable combined receipt's exact closed schema, PASS gates, and hash
    bindings to its five immutable predecessors.  The output labels that
    evidence role explicitly so this specification lock cannot claim to have
    independently repeated the earlier validation.
    """
    receipt_path = supplemental_aggregate_root / SUPPLEMENTAL_RECEIPT_FILENAME
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_MISSING")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_INVALID_JSON") from exc
    if not isinstance(payload, dict) or set(payload) != SUPPLEMENTAL_RECEIPT_KEYS:
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_SCHEMA_NOT_EXACT")
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_NOT_PASS")
    if payload.get("status") != "PASS" or not isinstance(payload.get("attempt_id"), str):
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_NOT_PASS")
    if any(payload.get(key) is not True for key in SUPPLEMENTAL_RECEIPT_TRUE_GATES):
        raise PretransferLockError("SUPPLEMENTAL_VALIDATION_RECEIPT_GATE_NOT_PASS")
    if payload.get("full_c3_authorized") is not False:
        raise PretransferLockError("SUPPLEMENTAL_RECEIPT_AUTHORIZES_FULL_C3")
    if payload.get("section5_run") is not False:
        raise PretransferLockError("SUPPLEMENTAL_RECEIPT_SECTION5_RAN")
    _require_git_commit(
        str(payload.get("governing_commit")),
    )
    for receipt_key, filename in SUPPLEMENTAL_RECEIPT_HASH_BINDINGS.items():
        if filename not in supplemental_authorities:
            raise PretransferLockError("SUPPLEMENTAL_RECEIPT_PREDECESSOR_UNDECLARED")
        if payload.get(receipt_key) != supplemental_authorities[filename][1]:
            raise PretransferLockError("SUPPLEMENTAL_RECEIPT_HASH_BINDING_MISMATCH")
    return {
        "evidence_role": "SUBORDINATE_HASH_BOUND_RECEIPT",
        "receipt_filename": SUPPLEMENTAL_RECEIPT_FILENAME,
        "receipt_sha256": supplemental_authorities[SUPPLEMENTAL_RECEIPT_FILENAME][1],
        "closed_schema_and_dag_pass_attested_by_receipt": True,
        "closed_schema_and_dag_validator_reexecuted_by_this_lock": False,
        "predecessor_hash_bindings_verified": True,
        "full_c3_authorized": False,
        "section5_run": False,
    }


def validate_batch_topology(batch_csv: Path) -> list[dict[str, int | str]]:
    """Validate the aggregate 19-batch production partition exactly."""
    if batch_csv.is_symlink() or not batch_csv.is_file():
        raise PretransferLockError("BATCH_SUMMARY_NOT_REGULAR_FILE")
    with batch_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise PretransferLockError("BATCH_SUMMARY_EMPTY") from exc
        if header != EXPECTED_BATCH_HEADER:
            raise PretransferLockError("BATCH_SUMMARY_HEADER_NOT_EXACT")
        raw_rows = list(reader)
    if len(raw_rows) != EXPECTED_BATCH_COUNT:
        raise PretransferLockError("BATCH_COUNT_NOT_19")
    if any(len(row) != len(EXPECTED_BATCH_HEADER) for row in raw_rows):
        raise PretransferLockError("BATCH_SUMMARY_ROW_WIDTH_INVALID")

    safe_rows: list[dict[str, int | str]] = []
    for index, raw in enumerate(raw_rows):
        row = dict(zip(EXPECTED_BATCH_HEADER, raw))
        try:
            studies = int(row["n_studies"])
            subjects = int(row["n_subjects"])
            requested = int(row["n_requested_objects"])
            verified = int(row["n_verified_objects"])
            unexpected = int(row["n_unexpected_selected_objects"])
            source_bytes = int(row["total_source_bytes"])
        except (TypeError, ValueError) as exc:
            raise PretransferLockError("BATCH_SUMMARY_NONINTEGER_COUNT") from exc
        if row["production_batch"] != EXPECTED_BATCH_IDS[index]:
            raise PretransferLockError("BATCH_ID_OR_ORDER_CHANGED")
        if studies != EXPECTED_BATCH_STUDY_COUNTS[index] or subjects != studies:
            raise PretransferLockError("BATCH_STUDY_PARTITION_CHANGED")
        if requested < 1 or verified != requested:
            raise PretransferLockError("BATCH_OBJECT_DENOMINATOR_NOT_RECONCILED")
        if unexpected != 0 or source_bytes < 1 or row["status"] != "PASS":
            raise PretransferLockError("BATCH_SOURCE_GATE_NOT_PASS")
        safe_rows.append(
            {
                "scheduler_task": index + 1,
                "production_batch": EXPECTED_BATCH_IDS[index],
                "n_studies": studies,
                "n_requested_objects": requested,
                "total_source_bytes": source_bytes,
            }
        )
    if sum(int(row["n_studies"]) for row in safe_rows) != EXPECTED_SELECTED_STUDIES:
        raise PretransferLockError("BATCH_STUDY_TOTAL_CHANGED")
    if (
        sum(int(row["n_requested_objects"]) for row in safe_rows)
        != EXPECTED_NORMALIZED_SOURCE_REQUESTS
    ):
        raise PretransferLockError("BATCH_OBJECT_TOTAL_CHANGED")
    if sum(int(row["total_source_bytes"]) for row in safe_rows) != EXPECTED_SOURCE_BYTES:
        raise PretransferLockError("BATCH_SOURCE_BYTE_TOTAL_CHANGED")
    return safe_rows


def _validate_plan_only_contract(contract: Mapping[str, Any]) -> None:
    try:
        validate_structure(contract)
    except ExecutionContractError as exc:
        raise PretransferLockError(str(exc)) from exc
    if contract.get("status") != "UNAUTHORIZED_PHASE_1E_BC":
        raise PretransferLockError("CONTRACT_NOT_PLAN_ONLY")
    authority = contract["authority"]
    source = contract["source"]
    storage = contract["storage"]
    batching = contract["batching"]
    scheduler = contract["scheduler"]
    download = contract["download"]
    authorization = contract["authorization"]
    requester_pays = contract["requester_pays"]
    preservation = contract["preservation"]
    preauthorization = contract["preauthorization_gates"]

    if authority.get("branch") != EXPECTED_BRANCH:
        raise PretransferLockError("CONTRACT_BRANCH_CHANGED")
    if source.get("release") != EXPECTED_RELEASE:
        raise PretransferLockError("SOURCE_RELEASE_CHANGED")
    if source.get("selected_studies") != EXPECTED_SELECTED_STUDIES:
        raise PretransferLockError("SELECTED_STUDY_COUNT_CHANGED")
    if source.get("selected_subjects") != EXPECTED_SELECTED_SUBJECTS:
        raise PretransferLockError("SELECTED_SUBJECT_COUNT_CHANGED")
    if source.get("normalized_source_requests") != EXPECTED_NORMALIZED_SOURCE_REQUESTS:
        raise PretransferLockError("SOURCE_REQUEST_COUNT_CHANGED")
    if source.get("source_body_download_authorized") is not False:
        raise PretransferLockError("SOURCE_BODY_DOWNLOAD_PREAUTHORIZED")
    if source.get("outside_selected_studies_permitted") is not False:
        raise PretransferLockError("OUTSIDE_SELECTED_STUDIES_ALLOWED")
    if source.get("historical_embedding_reuse_permitted") is not False:
        raise PretransferLockError("HISTORICAL_EMBEDDING_REUSE_ALLOWED")
    if storage.get("raw_dicoms_retained_through_active_analysis") is not True:
        raise PretransferLockError("RAW_DICOM_RETENTION_NOT_REQUIRED")
    if storage.get("raw_dicom_deletion_permitted_by_this_contract") is not False:
        raise PretransferLockError("RAW_DICOM_DELETION_ALLOWED")
    if storage.get("maximum_concurrent_extracted_batches") != 1:
        raise PretransferLockError("EXTRACTED_CACHE_CONCURRENCY_NOT_ONE")
    if batching.get("studies_per_batch") != EXPECTED_STUDIES_PER_FULL_BATCH:
        raise PretransferLockError("BATCH_SIZE_CHANGED")
    if batching.get("maximum_active_extracted_batches") != 1:
        raise PretransferLockError("ACTIVE_CACHE_BATCH_COUNT_NOT_ONE")
    if scheduler.get("array_tasks") != EXPECTED_BATCH_COUNT:
        raise PretransferLockError("SCHEDULER_ARRAY_COUNT_CHANGED")
    if scheduler.get("maximum_concurrent_array_tasks") != 1:
        raise PretransferLockError("SCHEDULER_CONCURRENCY_NOT_ONE")
    if scheduler.get("ambient_environment_export_permitted") is not False:
        raise PretransferLockError("AMBIENT_ENVIRONMENT_EXPORT_ALLOWED")
    if download.get("exact_object_only") is not True:
        raise PretransferLockError("DOWNLOADER_NOT_EXACT_OBJECT_ONLY")
    if download.get("prefix_body_copy_permitted") is not False:
        raise PretransferLockError("PREFIX_BODY_COPY_ALLOWED")
    if requester_pays.get("billing_project_environment_variable") != EXPECTED_BILLING_ENV:
        raise PretransferLockError("BILLING_ENVIRONMENT_AUTHORITY_CHANGED")
    if preauthorization.get("requester_pays_planning_estimate_owner_accepted") is not True:
        raise PretransferLockError("REQUESTER_PAYS_PLANNING_ESTIMATE_NOT_OWNER_ACCEPTED")
    if preauthorization.get("actual_dicom_transfer_authorized") is not False:
        raise PretransferLockError("ACTUAL_DICOM_TRANSFER_PREAUTHORIZED")
    if any(value is not False for key, value in authorization.items() if key in {
        "full_c3_execution",
        "source_body_download",
        "full_extraction",
        "full_embedding",
        "cache_retirement",
        "predictive_modeling",
        "confirmatory_performance_access",
    }):
        raise PretransferLockError("SCIENTIFIC_OR_EXECUTION_ACTION_PREAUTHORIZED")
    if not all(value is True for value in preservation.values()):
        raise PretransferLockError("PRESERVATION_REQUIREMENT_DISABLED")


def validate_aggregate_safe_summary(payload: Mapping[str, Any]) -> None:
    """Enforce the lock's closed aggregate-only export surface."""
    if set(payload) != LOCK_TOP_LEVEL_KEYS:
        raise PretransferLockError("LOCK_SUMMARY_TOP_LEVEL_SCHEMA_NOT_EXACT")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("artifact_type") != ARTIFACT_TYPE
        or payload.get("status") != LOCK_STATUS
        or payload.get("execution_mode") != "SPECIFICATION_ONLY"
        or payload.get("execution_authorized") is not False
        or payload.get("submission_authorized") is not False
        or payload.get("source_body_download_authorized") is not False
        or payload.get("scientific_execution_authorized") is not False
        or payload.get("full_c3_status") != "NO_GO"
    ):
        raise PretransferLockError("LOCK_SUMMARY_AUTHORITY_FIELDS_INVALID")

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized_key = str(key).strip().casefold()
                if normalized_key in FORBIDDEN_AGGREGATE_KEYS:
                    raise PretransferLockError("LOCK_SUMMARY_RESTRICTED_KEY_PRESENT")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
        elif isinstance(value, str):
            normalized_value = value.casefold()
            if any(
                fragment in normalized_value
                for fragment in FORBIDDEN_AGGREGATE_STRING_FRAGMENTS
            ):
                raise PretransferLockError("LOCK_SUMMARY_RESTRICTED_VALUE_PRESENT")
            if "@" in value:
                raise PretransferLockError("LOCK_SUMMARY_IDENTITY_VALUE_PRESENT")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise PretransferLockError("LOCK_SUMMARY_VALUE_TYPE_INVALID")

    inspect(payload)


def build_lock_summary(
    *,
    contract_path: Path,
    original_aggregate_root: Path,
    supplemental_aggregate_root: Path,
    governing_commit: str,
    selected_source_manifest_sha256: str,
    environment_receipt_sha256: str,
    command_config_manifest_sha256: str,
    checkout_root: Path | None = None,
    selected_cohort_manifest_path: Path | None = None,
    split_manifest_path: Path | None = None,
    selected_source_manifest_path: Path | None = None,
    environment_receipt_path: Path | None = None,
    command_config_manifest_path: Path | None = None,
    checkpoint_path: Path | None = None,
    original_authorities: Mapping[str, tuple[int, str]] = (
        ORIGINAL_AGGREGATE_AUTHORITIES
    ),
    supplemental_authorities: Mapping[str, tuple[int, str]] = (
        SUPPLEMENTAL_AGGREGATE_AUTHORITIES
    ),
) -> dict[str, Any]:
    """Return a closed specification-only lock; never authorize execution.

    Supplying a hash without its artifact is deliberately recorded as
    ``PROVIDED_UNVERIFIED`` and retained as a blocker.  The CLI requires the
    actual checkout and authority-file paths; optional paths exist here only
    to keep the pure builder useful for synthetic tests and offline review.
    """
    try:
        contract = load_contract(contract_path)
    except ExecutionContractError as exc:
        raise PretransferLockError(str(exc)) from exc
    _validate_plan_only_contract(contract)
    commit = _require_git_commit(governing_commit)
    checkout_status = "PROVIDED_UNVERIFIED"
    if checkout_root is not None:
        verify_checkout_authority(checkout_root, commit)
        checkout_status = "COMMIT_AND_BRANCH_VERIFIED_FROM_GIT_METADATA"
    source_manifest_sha, source_manifest_status = _resolve_file_hash_authority(
        provided_sha256=selected_source_manifest_sha256,
        artifact_path=selected_source_manifest_path,
        invalid_hash_code="SELECTED_SOURCE_MANIFEST_SHA256_INVALID",
        missing_file_code="SELECTED_SOURCE_MANIFEST_NOT_REGULAR_FILE",
        mismatch_code="SELECTED_SOURCE_MANIFEST_SHA256_MISMATCH",
    )
    environment_sha, environment_status = _resolve_file_hash_authority(
        provided_sha256=environment_receipt_sha256,
        artifact_path=environment_receipt_path,
        invalid_hash_code="ENVIRONMENT_RECEIPT_SHA256_INVALID",
        missing_file_code="ENVIRONMENT_RECEIPT_NOT_REGULAR_FILE",
        mismatch_code="ENVIRONMENT_RECEIPT_SHA256_MISMATCH",
    )
    command_config_sha, command_config_status = _resolve_file_hash_authority(
        provided_sha256=command_config_manifest_sha256,
        artifact_path=command_config_manifest_path,
        invalid_hash_code="COMMAND_CONFIG_MANIFEST_SHA256_INVALID",
        missing_file_code="COMMAND_CONFIG_MANIFEST_NOT_REGULAR_FILE",
        mismatch_code="COMMAND_CONFIG_MANIFEST_SHA256_MISMATCH",
    )
    immutable = validate_immutable_aggregate_authorities(
        original_aggregate_root,
        supplemental_aggregate_root,
        original_authorities=original_authorities,
        supplemental_authorities=supplemental_authorities,
    )
    batch_path = original_aggregate_root / "c3_full_source_preflight_by_batch.csv"
    topology_rows = validate_batch_topology(batch_path)
    supplemental_receipt = validate_supplemental_validation_receipt(
        supplemental_aggregate_root,
        supplemental_authorities=supplemental_authorities,
    )

    authority = contract["authority"]
    selected_cohort_sha, selected_cohort_status = _resolve_file_hash_authority(
        provided_sha256=authority["selected_manifest_sha256"],
        artifact_path=selected_cohort_manifest_path,
        invalid_hash_code="SELECTED_COHORT_MANIFEST_SHA256_INVALID",
        missing_file_code="SELECTED_COHORT_MANIFEST_NOT_REGULAR_FILE",
        mismatch_code="SELECTED_COHORT_MANIFEST_SHA256_MISMATCH",
    )
    split_sha, split_status = _resolve_file_hash_authority(
        provided_sha256=authority["split_manifest_sha256"],
        artifact_path=split_manifest_path,
        invalid_hash_code="SPLIT_MANIFEST_SHA256_INVALID",
        missing_file_code="SPLIT_MANIFEST_NOT_REGULAR_FILE",
        mismatch_code="SPLIT_MANIFEST_SHA256_MISMATCH",
    )
    checkpoint_sha, checkpoint_status = _resolve_file_hash_authority(
        provided_sha256=contract["embedding"]["checkpoint_sha256"],
        artifact_path=checkpoint_path,
        invalid_hash_code="CHECKPOINT_SHA256_INVALID",
        missing_file_code="CHECKPOINT_NOT_REGULAR_FILE",
        mismatch_code="CHECKPOINT_SHA256_MISMATCH",
    )
    if checkpoint_sha != EXPECTED_CHECKPOINT_SHA256:
        raise PretransferLockError("CHECKPOINT_SHA256_CHANGED")

    verification_statuses = {
        "checkout_commit_and_branch": checkout_status,
        "contract": "SCHEMA_AND_PLAN_GATES_VALIDATED_FROM_ACTUAL_FILE",
        "selected_cohort_manifest": selected_cohort_status,
        "split_manifest": split_status,
        "selected_source_manifest": source_manifest_status,
        "environment_receipt": environment_status,
        "command_config_manifest": command_config_status,
        "checkpoint": checkpoint_status,
    }
    unverified = sorted(
        name
        for name, status in verification_statuses.items()
        if status == "PROVIDED_UNVERIFIED"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "status": LOCK_STATUS,
        "execution_mode": "SPECIFICATION_ONLY",
        "execution_authorized": False,
        "submission_authorized": False,
        "source_body_download_authorized": False,
        "scientific_execution_authorized": False,
        "governing_commit": commit,
        "authority_verification": {
            "statuses": verification_statuses,
            "all_authority_file_hashes_verified_from_actual_paths": not unverified,
            "provided_unverified_authorities": unverified,
            "hash_verification_does_not_establish_semantic_authority": True,
            "production_semantic_or_source_receipt_validation_not_implemented_for": [
                "selected_cohort_manifest",
                "split_manifest",
                "selected_source_manifest",
                "environment_receipt",
                "command_config_manifest",
                "checkpoint",
            ],
        },
        "authority_hashes": {
            "contract_sha256": sha256_file(contract_path),
            "selected_cohort_manifest_sha256": selected_cohort_sha,
            "split_manifest_sha256": split_sha,
            "selected_source_manifest_sha256": source_manifest_sha,
            "source_preflight_summary_sha256": original_authorities[
                "c3_full_source_preflight.summary.json"
            ][1],
            "resource_plan_sha256": original_authorities[
                "c3_full_resource_plan.json"
            ][1],
            "checkpoint_sha256": checkpoint_sha,
            "environment_receipt_sha256": environment_sha,
            "command_config_manifest_sha256": command_config_sha,
        },
        "immutable_aggregate_authorities": immutable,
        "supplemental_validation_receipt": supplemental_receipt,
        "cohort": {
            "selected_studies": EXPECTED_SELECTED_STUDIES,
            "selected_subjects": EXPECTED_SELECTED_SUBJECTS,
            "normalized_source_requests": EXPECTED_NORMALIZED_SOURCE_REQUESTS,
            "exact_source_bytes": EXPECTED_SOURCE_BYTES,
            "outside_selected_studies_permitted": False,
            "historical_embedding_reuse_permitted": False,
        },
        "batch_topology": {
            "method": "sorted_selected_study_authority_contiguous_chunks",
            "total_batches": EXPECTED_BATCH_COUNT,
            "full_batches": EXPECTED_FULL_BATCH_COUNT,
            "studies_per_full_batch": EXPECTED_STUDIES_PER_FULL_BATCH,
            "final_batch_studies": EXPECTED_FINAL_BATCH_STUDIES,
            "maximum_concurrent_batches": 1,
            "scheduler_task_range": [1, 19],
            "scheduler_task_to_batch": topology_rows,
        },
        "downloader_contract": {
            "implementation_present": False,
            "generation_pinned_exact_objects_required": True,
            "selected_manifest_objects_only": True,
            "object_listing_permitted": False,
            "prefix_copy_permitted": False,
            "requester_pays_environment_variable": EXPECTED_BILLING_ENV,
            "billing_project_argv_permitted": False,
            "billing_project_log_export_permitted": False,
            "partial_suffix": ".partial",
            "verify_remote_generation_size_md5_crc32c_before_finalization": True,
            "local_sha256_required": True,
            "atomic_no_clobber_finalization_required": True,
        },
        "stage_receipt_and_resume_contract": {
            "stage_order": list(STAGE_ORDER),
            "one_owner_private_receipt_per_stage": True,
            "receipt_mode": "0600",
            "unique_attempt_root_required": True,
            "prior_attempt_overwrite_permitted": False,
            "canonical_output_overwrite_permitted": False,
            "failed_and_partial_evidence_preserved": True,
            "resume_requires_exact_input_authority_hashes": True,
            "resume_requires_pass_receipt": True,
            "resume_requires_output_size_and_sha256_match": True,
            "resume_reuses_failed_receipt": False,
            "receipt_requires_scheduler_job_identity": True,
            "receipt_requires_command_config_and_environment_hashes": True,
        },
        "storage_contract": {
            "raw_dicoms_retained_through_active_analysis": True,
            "raw_dicom_deletion_permitted": False,
            "maximum_active_extracted_cache_batches": 1,
            "cache_retirement_authorized": False,
            "cache_retirement_requires_separate_owner_authorization": True,
            "cache_retirement_requires_all_batch_gates": True,
        },
        "preservation_and_finalization_contract": {
            "batch_preservation_manifest_required": True,
            "safe_relative_paths_sizes_and_sha256_required": True,
            "source_commit_command_config_checkpoint_environment_required": True,
            "scheduler_identity_and_timestamps_required": True,
            "aggregate_safety_gate_required": True,
            "finalizer_implementation_present": False,
            "finalizer_requires_all_19_pass_receipts": True,
            "finalizer_requires_exact_cohort_object_and_byte_totals": True,
            "finalizer_requires_unique_physical_clip_keys": True,
            "finalizer_requires_one_vector_per_imaging_eligible_study": True,
            "finalizer_requires_raw_dicom_retention": True,
            "finalizer_no_clobber_required": True,
        },
        "execution_stubs": {
            "must_remain_fail_closed": True,
            "expected_refusal_exit_status": 78,
            "batch_runner_implemented": False,
            "finalizer_implemented": False,
            "submit_mode_enabled": False,
        },
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "remaining_execution_blockers": [
            *(
                ["AUTHORITY_FILES_PROVIDED_UNVERIFIED"]
                if unverified
                else []
            ),
            "PRODUCTION_AUTHORITY_SEMANTIC_AND_SOURCE_RECEIPT_VALIDATION_NOT_IMPLEMENTED",
            "LIVE_QUOTA_AND_HEADROOM_GATE_NOT_BOUND_TO_LOCK",
            "BACKUP_MIGRATION_AUTHORITY_NOT_BOUND_TO_LOCK",
            "PRODUCTION_DOWNLOADER_NOT_IMPLEMENTED",
            "PRODUCTION_BATCH_RUNNER_NOT_IMPLEMENTED",
            "PRODUCTION_FINALIZER_NOT_IMPLEMENTED",
            "SEPARATE_OWNER_TRANSFER_AUTHORIZATION_ABSENT",
        ],
        "full_c3_status": "NO_GO",
    }
    validate_aggregate_safe_summary(summary)
    return summary


def _validate_private_output_parent(path: Path, restricted_root: Path) -> Path:
    """Return a private parent inside a caller-declared restricted root."""
    if not path.is_absolute() or not restricted_root.is_absolute():
        raise PretransferLockError("OUTPUT_AND_RESTRICTED_ROOT_MUST_BE_ABSOLUTE")
    if restricted_root.is_symlink() or not restricted_root.is_dir():
        raise PretransferLockError("RESTRICTED_OUTPUT_ROOT_NOT_REGULAR_DIRECTORY")
    root = restricted_root.resolve(strict=True)
    parent = path.parent.resolve(strict=True)
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise PretransferLockError("OUTPUT_OUTSIDE_RESTRICTED_ROOT") from exc
    current = root
    relative_parts = parent.relative_to(root).parts
    for part in (".", *relative_parts):
        if part != ".":
            current = current / part
        metadata = current.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or current.is_symlink():
            raise PretransferLockError("OUTPUT_PARENT_COMPONENT_NOT_DIRECTORY")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise PretransferLockError("OUTPUT_PARENT_NOT_OWNER_PRIVATE")
    if path.is_symlink() or path.exists():
        raise PretransferLockError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER")
    return parent


def write_json_no_clobber(
    path: Path,
    payload: Mapping[str, Any],
    *,
    restricted_root: Path,
) -> None:
    """Atomically publish a complete mode-0600 JSON file without clobbering.

    A same-directory temporary inode is created as 0600, fully written and
    fsynced, then hard-linked to the final basename.  The link is atomic and
    fails if the destination already exists; no partially written final path
    is ever visible.
    """
    validate_aggregate_safe_summary(payload)
    parent = _validate_private_output_parent(path, restricted_root)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(parent, directory_flags)
    temp_name = f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(16)}"
    temp_fd: int | None = None
    try:
        parent_metadata = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or parent_metadata.st_mode & 0o077
        ):
            raise PretransferLockError("OUTPUT_PARENT_NOT_OWNER_PRIVATE")
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        create_flags |= getattr(os, "O_NOFOLLOW", 0)
        temp_fd = os.open(temp_name, create_flags, 0o600, dir_fd=parent_fd)
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "wb") as handle:
            temp_fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                temp_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise PretransferLockError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER") from exc
        os.fsync(parent_fd)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--original-aggregate-root", type=Path, required=True)
    parser.add_argument("--supplemental-aggregate-root", type=Path, required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--selected-cohort-manifest", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--selected-source-manifest", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--command-config-manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--selected-source-manifest-sha256")
    parser.add_argument("--environment-receipt-sha256")
    parser.add_argument("--command-config-manifest-sha256")
    parser.add_argument("--restricted-output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source_manifest_sha = args.selected_source_manifest_sha256 or sha256_regular_file(
            args.selected_source_manifest, "SELECTED_SOURCE_MANIFEST_NOT_REGULAR_FILE"
        )
        environment_sha = args.environment_receipt_sha256 or sha256_regular_file(
            args.environment_receipt, "ENVIRONMENT_RECEIPT_NOT_REGULAR_FILE"
        )
        command_config_sha = args.command_config_manifest_sha256 or sha256_regular_file(
            args.command_config_manifest,
            "COMMAND_CONFIG_MANIFEST_NOT_REGULAR_FILE",
        )
        summary = build_lock_summary(
            contract_path=args.contract,
            original_aggregate_root=args.original_aggregate_root,
            supplemental_aggregate_root=args.supplemental_aggregate_root,
            governing_commit=args.governing_commit,
            selected_source_manifest_sha256=source_manifest_sha,
            environment_receipt_sha256=environment_sha,
            command_config_manifest_sha256=command_config_sha,
            checkout_root=args.checkout_root,
            selected_cohort_manifest_path=args.selected_cohort_manifest,
            split_manifest_path=args.split_manifest,
            selected_source_manifest_path=args.selected_source_manifest,
            environment_receipt_path=args.environment_receipt,
            command_config_manifest_path=args.command_config_manifest,
            checkpoint_path=args.checkpoint,
        )
        write_json_no_clobber(
            args.output,
            summary,
            restricted_root=args.restricted_output_root,
        )
    except (
        PretransferLockError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "PRETRANSFER_LOCK_VALIDATION_FAILED"
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "FAIL_PRETRANSFER_LOCK",
                    "execution_authorized": False,
                    "error_code": code,
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": LOCK_STATUS,
                "execution_authorized": False,
                "submission_authorized": False,
                "object_listing_requests": 0,
                "object_body_requests": 0,
                "scheduler_submissions": 0,
                "scientific_actions_executed": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
