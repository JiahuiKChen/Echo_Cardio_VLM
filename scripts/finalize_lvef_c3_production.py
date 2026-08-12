#!/usr/bin/env python3
"""Fail-closed cross-batch finalizer for prospective selected-cohort C3.

The finalizer consumes restricted, checksummed batch-preservation receipts and
emits one closed-schema aggregate summary.  It never repairs, downloads,
decodes, embeds, deletes, or follows symlinks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core


EXPECTED_BATCH_IDS = tuple(f"c3_batch_{index:03d}" for index in range(19))
EXPECTED_SELECTED_STUDIES = 4_530
EXPECTED_SELECTED_SUBJECTS = 4_530
EXPECTED_SOURCE_OBJECTS = 335_984
EXPECTED_SOURCE_BYTES = 1_216_569_133_322
EXPECTED_NO_CINE_STUDIES = 5
EXPECTED_IMAGING_ELIGIBLE_STUDIES = 4_525
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP_RE = re.compile(
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|\+00:00)$"
)
PRESERVATION_MANIFEST_HEADER = ["relative_path", "size_bytes", "sha256", "role"]
PRESERVATION_ROLES = {
    "raw_dicom_and_download_authority",
    "dicom_extraction_metadata_retained",
    "extracted_npz_cache_owner_retirable",
    "embedding_and_pooling_retained",
    "download_ledger",
}
CLIP_MANIFEST_HEADER = [
    "embedding_idx", "subject_id", "study_id", "clip_key",
    "physical_source_key", "embedding_l2_norm", "embedding_sha256", "write_ok",
]

BATCH_RECEIPT_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "batch_id",
    "attempt_id",
    "governing_commit",
    "source_commit",
    "run_timestamp_utc",
    "cohort_version",
    "split_version",
    "execution_contract_version",
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "checkpoint_sha256",
    "checkpoint_checksum",
    "environment_receipt_sha256",
    "python_version",
    "pytorch_version",
    "torchvision_version",
    "cuda_version",
    "cudnn_version",
    "package_inventory_sha256",
    "production_stage_wrapper_sha256",
    "batch_preservation_script_sha256",
    "scheduler_runner_sha256",
    "command_checksum",
    "config_checksum",
    "scheduler_job_identity",
    "state_input_ledger_sha256",
    "n_selected_studies",
    "n_selected_subjects",
    "n_expected_objects",
    "expected_source_bytes",
    "n_download_verified",
    "n_dicom_readable",
    "n_dicom_unreadable",
    "n_multiframe_cines",
    "n_single_frame_objects",
    "n_extracted_clips",
    "n_unique_clip_keys",
    "n_clip_embeddings",
    "n_pooled_studies",
    "n_no_cine_studies",
    "no_cine_disposition",
    "n_outside_selected_studies",
    "n_missing_selected_studies",
    "n_duplicate_physical_sources",
    "n_duplicate_clip_keys",
    "n_nonfinite_embeddings",
    "n_wrong_dimension_embeddings",
    "source_receipt_sha256",
    "dicom_audit_sha256",
    "extraction_manifest_sha256",
    "clip_manifest_sha256",
    "clip_embeddings_sha256",
    "study_manifest_sha256",
    "study_embeddings_sha256",
    "preservation_manifest_sha256",
    "cache_retirement_authorization_sha256",
    "cache_tree_sha256",
    "cache_atomically_staged_receipt_sha256",
    "cache_retirement_script_sha256",
    "source_gate_passed",
    "download_gate_passed",
    "dicom_audit_gate_passed",
    "extraction_gate_passed",
    "embedding_gate_passed",
    "pooling_gate_passed",
    "study_pooling_semantics_gate_passed",
    "preservation_gate_passed",
    "aggregate_safety_gate_passed",
    "aggregate_safety_gate_result",
    "raw_dicoms_retained",
    "extracted_cache_retired",
}
RETIREMENT_RECEIPT_KEYS = {
    "cache_retirement_authorization_sha256",
    "cache_tree_sha256",
    "cache_atomically_staged_receipt_sha256",
    "cache_retirement_script_sha256",
}
PRESERVATION_ELIGIBILITY_RECEIPT_KEYS = BATCH_RECEIPT_KEYS - RETIREMENT_RECEIPT_KEYS
TRUE_GATE_KEYS = {
    "source_gate_passed",
    "download_gate_passed",
    "dicom_audit_gate_passed",
    "extraction_gate_passed",
    "embedding_gate_passed",
    "pooling_gate_passed",
    "study_pooling_semantics_gate_passed",
    "preservation_gate_passed",
    "aggregate_safety_gate_passed",
    "raw_dicoms_retained",
}
ZERO_KEYS = {
    "n_outside_selected_studies",
    "n_missing_selected_studies",
    "n_duplicate_physical_sources",
    "n_duplicate_clip_keys",
    "n_nonfinite_embeddings",
    "n_wrong_dimension_embeddings",
}
HASH_KEYS = {
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "checkpoint_sha256",
    "checkpoint_checksum",
    "environment_receipt_sha256",
    "package_inventory_sha256",
    "production_stage_wrapper_sha256",
    "batch_preservation_script_sha256",
    "scheduler_runner_sha256",
    "command_checksum",
    "config_checksum",
    "state_input_ledger_sha256",
    "source_receipt_sha256",
    "dicom_audit_sha256",
    "extraction_manifest_sha256",
    "clip_manifest_sha256",
    "clip_embeddings_sha256",
    "study_manifest_sha256",
    "study_embeddings_sha256",
    "preservation_manifest_sha256",
    "cache_retirement_authorization_sha256",
    "cache_tree_sha256",
    "cache_atomically_staged_receipt_sha256",
    "cache_retirement_script_sha256",
}
COUNT_KEYS = {
    "n_selected_studies",
    "n_selected_subjects",
    "n_expected_objects",
    "expected_source_bytes",
    "n_download_verified",
    "n_dicom_readable",
    "n_dicom_unreadable",
    "n_multiframe_cines",
    "n_single_frame_objects",
    "n_extracted_clips",
    "n_unique_clip_keys",
    "n_clip_embeddings",
    "n_pooled_studies",
    "n_no_cine_studies",
    *ZERO_KEYS,
}
FINAL_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "production_batches",
    "selected_studies",
    "selected_subjects",
    "verified_source_objects",
    "selected_source_bytes",
    "dicom_readable_objects",
    "dicom_unreadable_objects",
    "multiframe_cines",
    "single_frame_objects",
    "extracted_clips",
    "unique_clip_keys",
    "clip_embeddings",
    "pooled_imaging_eligible_studies",
    "no_cine_studies",
    "no_cine_disposition",
    "outside_selected_studies",
    "missing_selected_studies",
    "duplicate_physical_sources",
    "duplicate_clip_keys",
    "nonfinite_embeddings",
    "wrong_dimension_embeddings",
    "batch_receipt_set_sha256",
    "all_batches_finalized",
    "all_authority_bindings_identical",
    "all_source_receipts_passed",
    "all_dicom_audits_passed",
    "all_extractions_passed",
    "all_embeddings_passed",
    "all_pooling_passed",
    "all_preservation_manifests_passed",
    "all_aggregate_safety_gates_passed",
    "raw_dicoms_retained",
    "extracted_cache_retired",
    "outside_selected_studies_permitted",
    "scientific_inconsistency_repair_performed",
    "identifiers_emitted",
    "restricted_paths_emitted",
}
CANARY_FINAL_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "successful_train_studies",
    "selected_subjects",
    "verified_source_objects",
    "selected_source_bytes",
    "dicom_readable_objects",
    "dicom_unreadable_objects",
    "multiframe_cines",
    "single_frame_objects",
    "extracted_clips",
    "unique_clip_keys",
    "clip_embeddings",
    "pooled_studies",
    "no_cine_studies",
    "failed_studies",
    "preservation_receipt_sha256",
    "canary_manifest_sha256",
    "batch_plan_sha256",
    "scheduler_plan_sha256",
    "authority_binding_sha256",
    "all_studies_successful",
    "all_studies_train",
    "manifest_plan_scheduler_binding_passed",
    "all_preservation_gates_passed",
    "raw_dicoms_retained",
    "extracted_cache_retained",
    "aggregate_safe",
    "production_continuation_authorized",
    "identifiers_emitted",
    "restricted_paths_emitted",
}


class ProductionFinalizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionFinalizationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def load_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProductionFinalizationError(f"{code}_NOT_REGULAR")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except ProductionFinalizationError:
        raise
    except Exception as exc:
        raise ProductionFinalizationError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ProductionFinalizationError(f"{code}_NOT_OBJECT")
    return value


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ProductionFinalizationError("HASH_INPUT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_manifest_path(production_root: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if (
        not relative
        or logical.is_absolute()
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        raise ProductionFinalizationError("PRESERVATION_PATH_INVALID")
    path = production_root.joinpath(*logical.parts)
    cursor = production_root
    if cursor.is_symlink():
        raise ProductionFinalizationError("PRESERVATION_PATH_SYMLINK")
    for part in logical.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProductionFinalizationError("PRESERVATION_PATH_SYMLINK")
    return path


def replay_batch_preservation_manifest(
    manifest_path: Path, *, production_root: Path, attempt_id: str,
    batch_id: str, expected_retired_cache_tree_sha256: str,
) -> dict[str, int]:
    """Re-hash retained artifacts and prove only NPZ cache rows were retired."""
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProductionFinalizationError("PRESERVATION_MANIFEST_NOT_REGULAR")
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            raise ProductionFinalizationError("PRESERVATION_MANIFEST_EMPTY") from None
        if header != PRESERVATION_MANIFEST_HEADER or len(header) != len(set(header)):
            raise ProductionFinalizationError("PRESERVATION_MANIFEST_SCHEMA_MISMATCH")
        values = list(reader)
    if not values:
        raise ProductionFinalizationError("PRESERVATION_MANIFEST_EMPTY")
    seen: set[str] = set()
    listed_retained: set[str] = set()
    cache_records: list[str] = []
    retained = retired = 0
    prefix = f"attempts/{attempt_id}"
    role_prefixes = {
        "raw_dicom_and_download_authority": f"{prefix}/raw/{batch_id}/",
        "dicom_extraction_metadata_retained": (
            f"{prefix}/extracted_cache/{batch_id}/dicom_extraction/"
        ),
        "extracted_npz_cache_owner_retirable": (
            f"{prefix}/extracted_cache/{batch_id}/dicom_extraction/clips/"
        ),
        "embedding_and_pooling_retained": f"{prefix}/batches/{batch_id}/echoprime/",
    }
    exact_ledger = f"{prefix}/batches/{batch_id}/download_resume_ledger.restricted.json"
    cache_prefix = role_prefixes["extracted_npz_cache_owner_retirable"]
    for cells in values:
        if len(cells) != len(PRESERVATION_MANIFEST_HEADER):
            raise ProductionFinalizationError("PRESERVATION_MANIFEST_ROW_WIDTH_MISMATCH")
        row = dict(zip(PRESERVATION_MANIFEST_HEADER, cells))
        relative = row["relative_path"]
        role = row["role"]
        if relative in seen:
            raise ProductionFinalizationError("PRESERVATION_MANIFEST_DUPLICATE_PATH")
        seen.add(relative)
        if role not in PRESERVATION_ROLES:
            raise ProductionFinalizationError("PRESERVATION_ROLE_INVALID")
        if not row["size_bytes"].isdigit() or not SHA256_RE.fullmatch(row["sha256"]):
            raise ProductionFinalizationError("PRESERVATION_METADATA_INVALID")
        if role == "download_ledger":
            if relative != exact_ledger:
                raise ProductionFinalizationError("PRESERVATION_ROLE_PATH_MISMATCH")
        elif not relative.startswith(role_prefixes[role]):
            raise ProductionFinalizationError("PRESERVATION_ROLE_PATH_MISMATCH")
        path = _safe_manifest_path(production_root, relative)
        if role == "extracted_npz_cache_owner_retirable":
            if path.exists() or path.is_symlink():
                raise ProductionFinalizationError("RETIRED_CACHE_ARTIFACT_STILL_PRESENT")
            cache_relative = relative[len(cache_prefix):]
            cache_records.append(
                f"{cache_relative}\t{row['size_bytes']}\t{row['sha256']}"
            )
            retired += 1
            continue
        if path.is_symlink() or not path.is_file():
            raise ProductionFinalizationError("RETAINED_ARTIFACT_MISSING")
        if path.stat(follow_symlinks=False).st_size != int(row["size_bytes"]):
            raise ProductionFinalizationError("RETAINED_ARTIFACT_SIZE_MISMATCH")
        if sha256_file(path) != row["sha256"]:
            raise ProductionFinalizationError("RETAINED_ARTIFACT_HASH_MISMATCH")
        listed_retained.add(relative)
        retained += 1
    if not cache_records:
        raise ProductionFinalizationError("RETIRED_CACHE_INVENTORY_EMPTY")
    cache_tree_sha = hashlib.sha256(
        ("\n".join(sorted(cache_records)) + "\n").encode("utf-8")
    ).hexdigest()
    if cache_tree_sha != expected_retired_cache_tree_sha256:
        raise ProductionFinalizationError("RETIRED_CACHE_INVENTORY_HASH_MISMATCH")
    actual_retained: set[str] = set()
    roots = (
        production_root / prefix / "raw" / batch_id,
        production_root / prefix / "extracted_cache" / batch_id / "dicom_extraction",
        production_root / prefix / "batches" / batch_id / "echoprime",
    )
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            raise ProductionFinalizationError("RETAINED_ARTIFACT_ROOT_INVALID")
        for directory, names, filenames in os.walk(root, followlinks=False):
            current = Path(directory)
            if any((current / name).is_symlink() for name in names):
                raise ProductionFinalizationError("RETAINED_ARTIFACT_SYMLINK")
            for name in filenames:
                path = current / name
                if path.is_symlink() or not path.is_file():
                    raise ProductionFinalizationError("RETAINED_ARTIFACT_NOT_REGULAR")
                actual_retained.add(path.relative_to(production_root).as_posix())
    ledger_path = production_root / exact_ledger
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ProductionFinalizationError("RETAINED_ARTIFACT_MISSING")
    actual_retained.add(exact_ledger)
    if actual_retained != listed_retained:
        raise ProductionFinalizationError("UNLISTED_OR_MISSING_RETAINED_ARTIFACT")
    return {"retained_artifacts_reverified": retained, "retired_cache_artifacts": retired}


def accumulate_global_clip_authority(
    manifest_path: Path, *, planned_batch: Mapping[str, Any],
    expected_rows: int, global_clip_keys: set[str],
    global_physical_source_keys: set[str],
) -> None:
    """Validate retained clip ownership and reject cross-batch collisions."""
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProductionFinalizationError("CLIP_MANIFEST_NOT_REGULAR")
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ProductionFinalizationError("CLIP_MANIFEST_EMPTY") from None
        if header != CLIP_MANIFEST_HEADER or len(header) != len(set(header)):
            raise ProductionFinalizationError("CLIP_MANIFEST_SCHEMA_MISMATCH")
        values = list(reader)
    if len(values) != expected_rows:
        raise ProductionFinalizationError("CLIP_MANIFEST_ROW_COUNT_MISMATCH")
    expected_ownership = {
        str(row["source_object_key"]): (str(row["subject_id"]), str(row["study_id"]))
        for row in planned_batch["objects"]
    }
    local_clips: set[str] = set()
    local_sources: set[str] = set()
    indices: set[int] = set()
    for cells in values:
        if len(cells) != len(CLIP_MANIFEST_HEADER):
            raise ProductionFinalizationError("CLIP_MANIFEST_ROW_WIDTH_MISMATCH")
        row = dict(zip(CLIP_MANIFEST_HEADER, cells))
        try:
            index = int(row["embedding_idx"])
        except ValueError as exc:
            raise ProductionFinalizationError("CLIP_MANIFEST_INDEX_INVALID") from exc
        if str(index) != row["embedding_idx"] or index < 0 or index in indices:
            raise ProductionFinalizationError("CLIP_MANIFEST_INDEX_INVALID")
        indices.add(index)
        clip_key = row["clip_key"]
        source_key = row["physical_source_key"]
        if not SHA256_RE.fullmatch(clip_key) or not SHA256_RE.fullmatch(source_key):
            raise ProductionFinalizationError("CLIP_OR_SOURCE_KEY_INVALID")
        if expected_ownership.get(source_key) != (row["subject_id"], row["study_id"]):
            raise ProductionFinalizationError("CLIP_MANIFEST_OWNERSHIP_MISMATCH")
        if row["write_ok"].casefold() != "true":
            raise ProductionFinalizationError("CLIP_MANIFEST_WRITE_STATUS_INVALID")
        if clip_key in local_clips or clip_key in global_clip_keys:
            raise ProductionFinalizationError("GLOBAL_CLIP_KEY_COLLISION")
        if source_key in local_sources or source_key in global_physical_source_keys:
            raise ProductionFinalizationError("GLOBAL_PHYSICAL_SOURCE_COLLISION")
        local_clips.add(clip_key)
        local_sources.add(source_key)
    if indices != set(range(expected_rows)):
        raise ProductionFinalizationError("CLIP_MANIFEST_INDEX_INVALID")
    global_clip_keys.update(local_clips)
    global_physical_source_keys.update(local_sources)


def _validate_receipt(value: Mapping[str, Any]) -> None:
    if set(value) != BATCH_RECEIPT_KEYS:
        raise ProductionFinalizationError("BATCH_RECEIPT_SCHEMA_MISMATCH")
    if value.get("schema_version") != 1:
        raise ProductionFinalizationError("BATCH_RECEIPT_VERSION_MISMATCH")
    if value.get("artifact_type") != "lvef_c3_batch_finalization_receipt_v2":
        raise ProductionFinalizationError("BATCH_RECEIPT_TYPE_MISMATCH")
    if value.get("status") != "PASS_BATCH_FINALIZED":
        raise ProductionFinalizationError("BATCH_NOT_FINALIZED")
    if value.get("batch_id") not in EXPECTED_BATCH_IDS:
        raise ProductionFinalizationError("BATCH_ID_INVALID")
    if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
        raise ProductionFinalizationError("ATTEMPT_ID_INVALID")
    if not COMMIT_RE.fullmatch(str(value.get("governing_commit"))):
        raise ProductionFinalizationError("GOVERNING_COMMIT_INVALID")
    if value.get("source_commit") != value.get("governing_commit"):
        raise ProductionFinalizationError("SOURCE_COMMIT_MISMATCH")
    if not TIMESTAMP_RE.fullmatch(str(value.get("run_timestamp_utc"))):
        raise ProductionFinalizationError("RUN_TIMESTAMP_INVALID")
    if (
        value.get("cohort_version") != "mimic-iv-echo/1.0"
        or not re.fullmatch(r"split_map_sha256:[0-9a-f]{64}", str(value.get("split_version")))
        or value.get("execution_contract_version") != 2
        or value.get("aggregate_safety_gate_result") != "PASS"
        or value.get("checkpoint_checksum") != value.get("checkpoint_sha256")
    ):
        raise ProductionFinalizationError("BATCH_PROVENANCE_VALUE_INVALID")
    for key in (
        "python_version", "pytorch_version", "torchvision_version",
        "cuda_version", "cudnn_version",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductionFinalizationError("BATCH_RUNTIME_VERSION_INVALID")
    for key in HASH_KEYS:
        if not SHA256_RE.fullmatch(str(value.get(key))):
            raise ProductionFinalizationError("BATCH_HASH_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))):
        raise ProductionFinalizationError("SCHEDULER_IDENTITY_INVALID")
    for key in COUNT_KEYS:
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
            raise ProductionFinalizationError("BATCH_COUNT_NOT_INTEGER")
        if value[key] < 0:
            raise ProductionFinalizationError("BATCH_COUNT_NEGATIVE")
    for key in TRUE_GATE_KEYS:
        if value.get(key) is not True:
            raise ProductionFinalizationError("BATCH_GATE_FAILED")
    for key in ZERO_KEYS:
        if value[key] != 0:
            raise ProductionFinalizationError("SCIENTIFIC_INCONSISTENCY")
    if value.get("extracted_cache_retired") is not True:
        raise ProductionFinalizationError("CACHE_RETIREMENT_NOT_COMPLETE")
    if value["n_download_verified"] != value["n_expected_objects"]:
        raise ProductionFinalizationError("DOWNLOAD_COUNT_MISMATCH")
    if value["n_dicom_readable"] + value["n_dicom_unreadable"] != value["n_expected_objects"]:
        raise ProductionFinalizationError("DICOM_COUNT_MISMATCH")
    if value["n_multiframe_cines"] + value["n_single_frame_objects"] != value["n_dicom_readable"]:
        raise ProductionFinalizationError("CINE_CLASSIFICATION_MISMATCH")
    if not (
        value["n_multiframe_cines"]
        == value["n_extracted_clips"]
        == value["n_unique_clip_keys"]
        == value["n_clip_embeddings"]
    ):
        raise ProductionFinalizationError("CLIP_ACCOUNTING_MISMATCH")
    if value["n_pooled_studies"] + value["n_no_cine_studies"] != value["n_selected_studies"]:
        raise ProductionFinalizationError("STUDY_POOLING_ACCOUNTING_MISMATCH")
    if value["n_no_cine_studies"] and value.get("no_cine_disposition") != (
        "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    ):
        raise ProductionFinalizationError("NO_CINE_DISPOSITION_MISMATCH")


def _validate_canary_eligibility_receipt(value: Mapping[str, Any]) -> None:
    """Validate the retained-cache receipt subset used by a bounded canary."""
    if set(value) != PRESERVATION_ELIGIBILITY_RECEIPT_KEYS:
        raise ProductionFinalizationError("CANARY_RECEIPT_SCHEMA_MISMATCH")
    if value.get("schema_version") != 1:
        raise ProductionFinalizationError("CANARY_RECEIPT_VERSION_MISMATCH")
    if value.get("artifact_type") != "lvef_c3_batch_preservation_eligibility_receipt_v2":
        raise ProductionFinalizationError("CANARY_RECEIPT_TYPE_MISMATCH")
    if value.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        raise ProductionFinalizationError("CANARY_PRESERVATION_NOT_ELIGIBLE")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", str(value.get("batch_id"))):
        raise ProductionFinalizationError("CANARY_BATCH_ID_INVALID")
    if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
        raise ProductionFinalizationError("CANARY_ATTEMPT_ID_INVALID")
    if not COMMIT_RE.fullmatch(str(value.get("governing_commit"))):
        raise ProductionFinalizationError("CANARY_GOVERNING_COMMIT_INVALID")
    if value.get("source_commit") != value.get("governing_commit"):
        raise ProductionFinalizationError("CANARY_SOURCE_COMMIT_MISMATCH")
    if not TIMESTAMP_RE.fullmatch(str(value.get("run_timestamp_utc"))):
        raise ProductionFinalizationError("CANARY_RUN_TIMESTAMP_INVALID")
    if (
        value.get("cohort_version") != "mimic-iv-echo/1.0"
        or not re.fullmatch(
            r"split_map_sha256:[0-9a-f]{64}", str(value.get("split_version"))
        )
        or value.get("execution_contract_version") != 2
        or value.get("aggregate_safety_gate_result") != "PASS"
        or value.get("checkpoint_checksum") != value.get("checkpoint_sha256")
    ):
        raise ProductionFinalizationError("CANARY_PROVENANCE_VALUE_INVALID")
    for key in (
        "python_version", "pytorch_version", "torchvision_version",
        "cuda_version", "cudnn_version",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductionFinalizationError("CANARY_RUNTIME_VERSION_INVALID")
    for key in HASH_KEYS - RETIREMENT_RECEIPT_KEYS:
        if not SHA256_RE.fullmatch(str(value.get(key))):
            raise ProductionFinalizationError("CANARY_HASH_INVALID")
    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))
    ):
        raise ProductionFinalizationError("CANARY_SCHEDULER_IDENTITY_INVALID")
    for key in COUNT_KEYS:
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
            raise ProductionFinalizationError("CANARY_COUNT_NOT_INTEGER")
        if value[key] < 0:
            raise ProductionFinalizationError("CANARY_COUNT_NEGATIVE")
    for key in TRUE_GATE_KEYS:
        if value.get(key) is not True:
            raise ProductionFinalizationError("CANARY_GATE_FAILED")
    for key in ZERO_KEYS:
        if value[key] != 0:
            raise ProductionFinalizationError("CANARY_SCIENTIFIC_INCONSISTENCY")
    if value.get("extracted_cache_retired") is not False:
        raise ProductionFinalizationError("CANARY_CACHE_NOT_RETAINED")
    if (
        value["n_selected_studies"] != 5
        or value["n_selected_subjects"] != 5
        or value["n_pooled_studies"] != 5
        or value["n_no_cine_studies"] != 0
        or value.get("no_cine_disposition") != "NONE"
    ):
        raise ProductionFinalizationError("CANARY_EXACT_FIVE_SUCCESSFUL_STUDIES_REQUIRED")
    if value["n_expected_objects"] < 5 or value["expected_source_bytes"] < 1:
        raise ProductionFinalizationError("CANARY_SOURCE_AGGREGATE_INVALID")
    if value["n_download_verified"] != value["n_expected_objects"]:
        raise ProductionFinalizationError("CANARY_DOWNLOAD_COUNT_MISMATCH")
    if (
        value["n_dicom_unreadable"] != 0
        or value["n_dicom_readable"] != value["n_expected_objects"]
    ):
        raise ProductionFinalizationError("CANARY_DICOM_FAILURE")
    if value["n_multiframe_cines"] + value["n_single_frame_objects"] != value["n_dicom_readable"]:
        raise ProductionFinalizationError("CANARY_CINE_CLASSIFICATION_MISMATCH")
    if not (
        value["n_multiframe_cines"]
        == value["n_extracted_clips"]
        == value["n_unique_clip_keys"]
        == value["n_clip_embeddings"]
    ) or value["n_multiframe_cines"] < 5:
        raise ProductionFinalizationError("CANARY_CLIP_ACCOUNTING_MISMATCH")


def validate_closed_canary_summary(value: Mapping[str, Any]) -> None:
    if set(value) != CANARY_FINAL_KEYS:
        raise ProductionFinalizationError("CANARY_SUMMARY_SCHEMA_MISMATCH")


def finalize_canary_preservation_receipt(
    receipt: Mapping[str, Any], *, expected_governing_commit: str,
    expected_attempt_id: str, expected_canary_manifest_sha256: str,
    expected_batch_plan_sha256: str, expected_scheduler_plan_sha256: str,
    expected_object_count: int, expected_source_bytes: int,
) -> dict[str, Any]:
    """Finalize one exact-five canary receipt without retiring its cache."""
    if not isinstance(receipt, Mapping):
        raise ProductionFinalizationError("CANARY_RECEIPT_NOT_MAPPING")
    if (
        not isinstance(expected_governing_commit, str)
        or not COMMIT_RE.fullmatch(expected_governing_commit)
    ):
        raise ProductionFinalizationError("EXPECTED_CANARY_COMMIT_INVALID")
    if (
        not isinstance(expected_attempt_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", expected_attempt_id)
    ):
        raise ProductionFinalizationError("EXPECTED_CANARY_ATTEMPT_ID_INVALID")
    if (
        isinstance(expected_object_count, bool)
        or not isinstance(expected_object_count, int)
        or expected_object_count < 5
        or expected_object_count > 750
        or isinstance(expected_source_bytes, bool)
        or not isinstance(expected_source_bytes, int)
        or expected_source_bytes < 1
        or expected_source_bytes > 5_000_000_000
    ):
        raise ProductionFinalizationError("EXPECTED_CANARY_SOURCE_SCOPE_INVALID")
    expected_hashes = {
        "canary_manifest_sha256": expected_canary_manifest_sha256,
        "batch_plan_sha256": expected_batch_plan_sha256,
        "scheduler_plan_sha256": expected_scheduler_plan_sha256,
    }
    if any(
        not isinstance(value, str) or not SHA256_RE.fullmatch(value)
        for value in expected_hashes.values()
    ):
        raise ProductionFinalizationError("EXPECTED_CANARY_AUTHORITY_HASH_INVALID")
    _validate_canary_eligibility_receipt(receipt)
    if receipt.get("governing_commit") != expected_governing_commit:
        raise ProductionFinalizationError("CANARY_GOVERNING_COMMIT_MISMATCH")
    if receipt.get("attempt_id") != expected_attempt_id:
        raise ProductionFinalizationError("CANARY_ATTEMPT_MISMATCH")
    if receipt.get("batch_plan_sha256") != expected_batch_plan_sha256:
        raise ProductionFinalizationError("CANARY_BATCH_PLAN_BINDING_MISMATCH")
    if (
        receipt.get("n_expected_objects") != expected_object_count
        or receipt.get("expected_source_bytes") != expected_source_bytes
    ):
        raise ProductionFinalizationError("CANARY_MANIFEST_SOURCE_SCOPE_MISMATCH")

    receipt_sha256 = core.canonical_json_sha256(receipt)
    authority_binding_sha256 = core.canonical_json_sha256(
        {
            "preservation_receipt_sha256": receipt_sha256,
            **expected_hashes,
        }
    )
    result = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_preservation_finalization_summary_v1",
        "status": "PASS_CANARY_PRESERVATION_FINALIZED_RETAINED_CACHE",
        "successful_train_studies": 5,
        "selected_subjects": 5,
        "verified_source_objects": receipt["n_download_verified"],
        "selected_source_bytes": receipt["expected_source_bytes"],
        "dicom_readable_objects": receipt["n_dicom_readable"],
        "dicom_unreadable_objects": 0,
        "multiframe_cines": receipt["n_multiframe_cines"],
        "single_frame_objects": receipt["n_single_frame_objects"],
        "extracted_clips": receipt["n_extracted_clips"],
        "unique_clip_keys": receipt["n_unique_clip_keys"],
        "clip_embeddings": receipt["n_clip_embeddings"],
        "pooled_studies": 5,
        "no_cine_studies": 0,
        "failed_studies": 0,
        "preservation_receipt_sha256": receipt_sha256,
        **expected_hashes,
        "authority_binding_sha256": authority_binding_sha256,
        "all_studies_successful": True,
        "all_studies_train": True,
        "manifest_plan_scheduler_binding_passed": True,
        "all_preservation_gates_passed": True,
        "raw_dicoms_retained": True,
        "extracted_cache_retained": True,
        "aggregate_safe": True,
        "production_continuation_authorized": False,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    validate_closed_canary_summary(result)
    return result


def validate_closed_final_summary(value: Mapping[str, Any]) -> None:
    if set(value) != FINAL_KEYS:
        raise ProductionFinalizationError("FINAL_SUMMARY_SCHEMA_MISMATCH")


def finalize_receipts(
    receipt_paths: Sequence[Path], *, expected_governing_commit: str,
    expected_attempt_id: str | None = None, plan: Mapping[str, Any] | None = None,
    requirements: Any | None = None, production_root: Path | None = None,
    contract: Mapping[str, Any] | None = None, contract_path: Path | None = None,
    environment_receipt: Path | None = None,
    cache_retirement_authorization_root: Path | None = None,
) -> dict[str, Any]:
    if len(receipt_paths) != len(EXPECTED_BATCH_IDS):
        raise ProductionFinalizationError("FINAL_BATCH_SET_INCOMPLETE")
    receipts: list[dict[str, Any]] = []
    receipt_hashes: list[str] = []
    receipt_paths_by_batch: dict[str, Path] = {}
    for path in receipt_paths:
        receipt = load_json(path, "BATCH_RECEIPT")
        _validate_receipt(receipt)
        receipts.append(receipt)
        receipt_hashes.append(sha256_file(path))
        if receipt["batch_id"] in receipt_paths_by_batch:
            raise ProductionFinalizationError("DUPLICATE_BATCH_RECEIPT")
        receipt_paths_by_batch[receipt["batch_id"]] = path
    receipts.sort(key=lambda item: str(item["batch_id"]))
    if tuple(item["batch_id"] for item in receipts) != EXPECTED_BATCH_IDS:
        raise ProductionFinalizationError("FINAL_BATCH_SET_MISMATCH")
    if any(item["governing_commit"] != expected_governing_commit for item in receipts):
        raise ProductionFinalizationError("GOVERNING_COMMIT_MISMATCH")
    attempt_ids = {item["attempt_id"] for item in receipts}
    if len(attempt_ids) != 1 or (expected_attempt_id is not None and attempt_ids != {expected_attempt_id}):
        raise ProductionFinalizationError("CROSS_BATCH_ATTEMPT_MISMATCH")
    authority_keys = (
        "governing_commit",
        "orchestration_contract_sha256",
        "batch_plan_sha256",
        "checkpoint_sha256",
        "environment_receipt_sha256",
        "production_stage_wrapper_sha256",
        "batch_preservation_script_sha256",
        "scheduler_runner_sha256",
        "command_checksum",
        "config_checksum",
        "package_inventory_sha256",
        "cohort_version",
        "split_version",
        "execution_contract_version",
        "python_version",
        "pytorch_version",
        "torchvision_version",
        "cuda_version",
        "cudnn_version",
        "cache_retirement_script_sha256",
    )
    if any(len({item[key] for item in receipts}) != 1 for key in authority_keys):
        raise ProductionFinalizationError("CROSS_BATCH_AUTHORITY_MISMATCH")
    if plan is not None:
        if (
            requirements is None
            or production_root is None
            or contract is None
            or contract_path is None
            or environment_receipt is None
            or cache_retirement_authorization_root is None
        ):
            raise ProductionFinalizationError("FINALIZER_AUTHORITY_ARGUMENTS_INCOMPLETE")
        if (
            cache_retirement_authorization_root.is_symlink()
            or not cache_retirement_authorization_root.is_dir()
        ):
            raise ProductionFinalizationError("CACHE_AUTHORIZATION_ROOT_INVALID")
        plan_sha = core.validate_batch_plan(plan, requirements=requirements)
        expected_runtime_authority = core.derive_expected_runtime_authority(
            plan,
            requirements=requirements,
            contract=contract,
            contract_path=contract_path,
            governing_commit=expected_governing_commit,
            environment_receipt_sha256=sha256_file(environment_receipt),
        )
        if any(item["batch_plan_sha256"] != plan_sha for item in receipts):
            raise ProductionFinalizationError("FINALIZER_PLAN_HASH_MISMATCH")
        if any(
            item["batch_preservation_script_sha256"]
            != sha256_file(Path(__file__).resolve().parent / "preserve_lvef_c3_production_batch.py")
            or item["cache_retirement_script_sha256"]
            != sha256_file(Path(__file__).resolve().parent / "retire_lvef_c3_extracted_cache_v2.py")
            for item in receipts
        ):
            raise ProductionFinalizationError("FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH")
        planned = {item["batch_id"]: item for item in plan["batches"]}
        attempt_id = next(iter(attempt_ids))
        global_clip_keys: set[str] = set()
        global_physical_source_keys: set[str] = set()
        for receipt in receipts:
            batch = planned[receipt["batch_id"]]
            if (
                receipt["n_selected_studies"] != batch["n_studies"]
                or receipt["n_selected_subjects"] != batch["n_subjects"]
                or receipt["n_expected_objects"] != batch["n_objects"]
                or receipt["expected_source_bytes"] != batch["source_bytes"]
            ):
                raise ProductionFinalizationError("FINALIZER_BATCH_PLAN_COUNT_MISMATCH")
            batch_root = production_root / "attempts" / attempt_id / "batches" / receipt["batch_id"]
            retired_cache_root = (
                production_root
                / "attempts"
                / attempt_id
                / "extracted_cache"
                / receipt["batch_id"]
                / "dicom_extraction"
                / "clips"
            )
            if retired_cache_root.exists() or retired_cache_root.is_symlink():
                raise ProductionFinalizationError("FINALIZER_CACHE_RETIREMENT_INCOMPLETE")
            expected_artifacts = {
                "source_receipt_sha256": batch_root / "download_resume_ledger.restricted.json",
                "dicom_audit_sha256": (
                    production_root / "attempts" / attempt_id / "extracted_cache"
                    / receipt["batch_id"] / "dicom_extraction"
                    / "dicom_audit.restricted.csv"
                ),
                "extraction_manifest_sha256": (
                    production_root / "attempts" / attempt_id / "extracted_cache"
                    / receipt["batch_id"] / "dicom_extraction"
                    / "extraction_manifest.restricted.csv"
                ),
                "clip_manifest_sha256": batch_root / "echoprime" / "clip_manifest.restricted.csv",
                "clip_embeddings_sha256": batch_root / "echoprime" / "clip_embeddings.restricted.npz",
                "study_manifest_sha256": batch_root / "echoprime" / "study_manifest.restricted.csv",
                "study_embeddings_sha256": batch_root / "echoprime" / "study_embeddings.restricted.npz",
                "preservation_manifest_sha256": batch_root / "preservation" / "batch_preservation_manifest.restricted.tsv",
                "cache_atomically_staged_receipt_sha256": batch_root / "preservation" / "cache_atomically_staged.restricted.json",
                "state_input_ledger_sha256": batch_root / "pooling_resume_ledger.restricted.json",
                "cache_retirement_authorization_sha256": (
                    cache_retirement_authorization_root
                    / f"{receipt['batch_id']}.authorization.json"
                ),
            }
            for key, path in expected_artifacts.items():
                if key == "cache_retirement_authorization_sha256":
                    metadata = path.stat(follow_symlinks=False) if path.exists() else None
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or metadata is None
                        or metadata.st_uid != os.getuid()
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                    ):
                        raise ProductionFinalizationError("CACHE_AUTHORIZATION_FILE_INVALID")
                if sha256_file(path) != receipt[key]:
                    raise ProductionFinalizationError("FINALIZER_REFERENCED_ARTIFACT_HASH_MISMATCH")
            replay_batch_preservation_manifest(
                expected_artifacts["preservation_manifest_sha256"],
                production_root=production_root,
                attempt_id=attempt_id,
                batch_id=receipt["batch_id"],
                expected_retired_cache_tree_sha256=receipt["cache_tree_sha256"],
            )
            accumulate_global_clip_authority(
                expected_artifacts["clip_manifest_sha256"],
                planned_batch=batch,
                expected_rows=receipt["n_clip_embeddings"],
                global_clip_keys=global_clip_keys,
                global_physical_source_keys=global_physical_source_keys,
            )
            final_ledger = core.load_strict_json(batch_root / "final_resume_ledger.restricted.json")
            core.validate_ledger_against_current_runtime(
                final_ledger,
                plan=plan,
                requirements=requirements,
                contract=contract,
                contract_path=contract_path,
                governing_commit=expected_governing_commit,
                environment_receipt_sha256=sha256_file(environment_receipt),
                batch_id=receipt["batch_id"],
            )
            core.validate_resume_authority(
                final_ledger,
                expected_authority=expected_runtime_authority,
                attempt_id=next(iter(attempt_ids)),
                expected_object_keys={
                    receipt["batch_id"]: {
                        item["source_object_key"] for item in batch["objects"]
                    }
                },
            )
            if final_ledger["status"] != "COMPLETE" or final_ledger["batches"][receipt["batch_id"]]["state"] != "FINALIZED":
                raise ProductionFinalizationError("FINALIZER_BATCH_LEDGER_NOT_FINALIZED")
            final_receipt_path = (
                batch_root / "preservation" / "batch_finalization_receipt.restricted.json"
            )
            if (
                sha256_file(final_receipt_path)
                != sha256_file(receipt_paths_by_batch[receipt["batch_id"]])
                or load_json(final_receipt_path, "FINAL_BATCH_RECEIPT") != receipt
            ):
                raise ProductionFinalizationError("FINAL_BATCH_RECEIPT_PATH_MISMATCH")
            transition = load_json(
                batch_root / "preservation" / "cache_retirement_finalized.restricted.json",
                "CACHE_RETIREMENT_TRANSITION",
            )
            final_batch = final_ledger["batches"][receipt["batch_id"]]
            if (
                transition.get("from_state") != "CACHE_RETIREMENT_ELIGIBLE"
                or transition.get("to_state") != "FINALIZED"
                or transition.get("output_manifest_sha256")
                != sha256_file(final_receipt_path)
                or not final_batch["events"]
                or final_batch["events"][-1].get("receipt_sha256")
                != core.canonical_json_sha256(transition)
            ):
                raise ProductionFinalizationError("CACHE_RETIREMENT_TRANSITION_INVALID")
        if (
            len(global_clip_keys) != sum(item["n_unique_clip_keys"] for item in receipts)
            or len(global_physical_source_keys)
            != sum(item["n_unique_clip_keys"] for item in receipts)
        ):
            raise ProductionFinalizationError("GLOBAL_CLIP_AUTHORITY_COUNT_MISMATCH")

    def total(key: str) -> int:
        return sum(int(item[key]) for item in receipts)

    expected_totals = {
        "n_selected_studies": EXPECTED_SELECTED_STUDIES,
        "n_selected_subjects": EXPECTED_SELECTED_SUBJECTS,
        "n_expected_objects": EXPECTED_SOURCE_OBJECTS,
        "expected_source_bytes": EXPECTED_SOURCE_BYTES,
        "n_download_verified": EXPECTED_SOURCE_OBJECTS,
        "n_pooled_studies": EXPECTED_IMAGING_ELIGIBLE_STUDIES,
        "n_no_cine_studies": EXPECTED_NO_CINE_STUDIES,
    }
    for key, expected in expected_totals.items():
        if total(key) != expected:
            raise ProductionFinalizationError("FINAL_COHORT_ACCOUNTING_MISMATCH")
    receipt_set_hash = hashlib.sha256(
        "\n".join(sorted(receipt_hashes)).encode("ascii") + b"\n"
    ).hexdigest()
    result = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_production_finalization_summary_v1",
        "status": "PASS_PRODUCTION_C3_FINALIZED",
        "production_batches": len(receipts),
        "selected_studies": total("n_selected_studies"),
        "selected_subjects": total("n_selected_subjects"),
        "verified_source_objects": total("n_download_verified"),
        "selected_source_bytes": total("expected_source_bytes"),
        "dicom_readable_objects": total("n_dicom_readable"),
        "dicom_unreadable_objects": total("n_dicom_unreadable"),
        "multiframe_cines": total("n_multiframe_cines"),
        "single_frame_objects": total("n_single_frame_objects"),
        "extracted_clips": total("n_extracted_clips"),
        "unique_clip_keys": total("n_unique_clip_keys"),
        "clip_embeddings": total("n_clip_embeddings"),
        "pooled_imaging_eligible_studies": total("n_pooled_studies"),
        "no_cine_studies": total("n_no_cine_studies"),
        "no_cine_disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE",
        "outside_selected_studies": total("n_outside_selected_studies"),
        "missing_selected_studies": total("n_missing_selected_studies"),
        "duplicate_physical_sources": total("n_duplicate_physical_sources"),
        "duplicate_clip_keys": total("n_duplicate_clip_keys"),
        "nonfinite_embeddings": total("n_nonfinite_embeddings"),
        "wrong_dimension_embeddings": total("n_wrong_dimension_embeddings"),
        "batch_receipt_set_sha256": receipt_set_hash,
        "all_batches_finalized": True,
        "all_authority_bindings_identical": True,
        "all_source_receipts_passed": True,
        "all_dicom_audits_passed": True,
        "all_extractions_passed": True,
        "all_embeddings_passed": True,
        "all_pooling_passed": True,
        "all_preservation_manifests_passed": True,
        "all_aggregate_safety_gates_passed": True,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
        "outside_selected_studies_permitted": False,
        "scientific_inconsistency_repair_performed": False,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    validate_closed_final_summary(result)
    return result


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ProductionFinalizationError("FINAL_OUTPUT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ProductionFinalizationError("FINAL_OUTPUT_PARENT_SYMLINK")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    try:
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as exc:
        raise ProductionFinalizationError("FINAL_OUTPUT_ALREADY_EXISTS") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-receipt", type=Path, action="append", required=True)
    parser.add_argument("--expected-governing-commit", required=True)
    parser.add_argument("--expected-attempt-id", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--batch-plan", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--cache-retirement-authorization-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not COMMIT_RE.fullmatch(args.expected_governing_commit):
        raise ProductionFinalizationError("EXPECTED_COMMIT_INVALID")
    contract = core.load_orchestration_contract(args.contract)
    plan = core.load_strict_json(args.batch_plan)
    summary = finalize_receipts(
        args.batch_receipt, expected_governing_commit=args.expected_governing_commit,
        expected_attempt_id=args.expected_attempt_id, plan=plan,
        requirements=core.production_requirements(contract), production_root=args.production_root,
        contract=contract, contract_path=args.contract,
        environment_receipt=args.environment_receipt,
        cache_retirement_authorization_root=args.cache_retirement_authorization_root,
    )
    write_json_atomic(args.output, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "production_batches": summary["production_batches"],
                "identifiers_emitted": False,
                "restricted_paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except ProductionFinalizationError as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": exc.code}, sort_keys=True))
        return 78
    except Exception:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_code": "UNEXPECTED_FINALIZER_EXCEPTION",
                    "exception_message_emitted": False,
                    "identifiers_emitted": False,
                    "restricted_paths_emitted": False,
                },
                sort_keys=True,
            )
        )
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
