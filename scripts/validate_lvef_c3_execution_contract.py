#!/usr/bin/env python3
"""Validate the selected-cohort C3 execution contract without running C3.

The validator is intentionally usable by scheduler wrappers as a fail-closed
authorization gate.  A structurally valid contract is not the same as an
authorized contract.  Owner authorization must be a separate restricted JSON
record bound to the exact contract and source-manifest SHA-256 values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

import yaml


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_CHECKPOINT_SHA256 = (
    "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
)
EXPECTED_SELECTED_MANIFEST_SHA256 = (
    "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1"
)
EXPECTED_SPLIT_MANIFEST_SHA256 = (
    "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517"
)
EXPECTED_PYTHON_EXECUTABLE_SHA256 = (
    "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
)
EXPECTED_BRANCH = "codex/lvef-multitask-revalidation"
EXPECTED_RELEASE = "mimic-iv-echo/1.0"
EXPECTED_BUCKET = "mimic-iv-echo-1.0.physionet.org"
REQUIRED_CACHE_GATES = {
    "source",
    "download",
    "dicom_header",
    "extraction",
    "embedding",
    "pooling",
    "checksum",
    "preservation",
    "safety",
}
REQUIRED_PREAUTHORIZATION_GATES = {
    "prospective_gcp_tooling_resolved",
    "prospective_gcp_identity_verified",
    "prospective_gcp_project_verified",
    "prospective_gcp_billing_link_verified",
    "prospective_requester_pays_metadata_access_verified",
    "prospective_bigquery_billing_access_verified",
    "free_trial_status_owner_verified_or_separately_budgeted",
    "exact_source_metadata_complete",
    "exact_source_bytes_known",
    "no_selected_source_deficit",
    "storage_migration_plan_complete",
    "backed_up_authority_plan_complete",
    "backed_up_authority_copy_verified",
    "quota_headroom_gate_passed",
    "requester_pays_budget_approved",
    "requester_pays_planning_estimate_owner_accepted",
    "actual_dicom_transfer_authorized",
    "selected_source_manifest_frozen",
    "checkpoint_and_environment_contract_frozen",
    "production_runner_implemented_and_validated",
    "technical_metadata_review_complete",
    "clinician_packet_ready",
    "direct_restricted_agent_mode_documented",
    "export_safety_mode_documented",
    "command_config_checksums_frozen",
}
AUTHORIZATION_ACTIONS = (
    "full_c3_execution",
    "source_body_download",
    "full_extraction",
    "full_embedding",
    "cache_retirement",
)


class ContractError(ValueError):
    """A safe, non-row-level contract validation error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label}_MUST_BE_MAPPING")
    return value


def _exact(value: Any, expected: Any, code: str) -> None:
    if value != expected:
        raise ContractError(code)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ContractError(f"{label}_SCHEMA_NOT_EXACT")


def _sha(value: Any, code: str) -> str:
    text = str(value).lower()
    if not SHA256_RE.fullmatch(text):
        raise ContractError(code)
    return text


def load_contract(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ContractError("CONTRACT_NOT_REGULAR_FILE")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError("CONTRACT_ROOT_MUST_BE_MAPPING")
    return payload


def validate_structure(contract: Mapping[str, Any]) -> None:
    if set(contract) != {
        "schema_version", "contract_id", "status", "scientific_scope", "authority",
        "source", "requester_pays", "google_cloud_provenance", "storage", "batching", "download",
        "dicom_and_extraction", "embedding", "pooling", "environment", "scheduler",
        "cache_retirement", "preservation", "analysis_and_export", "authorization",
        "preauthorization_gates", "post_manuscript",
    }:
        raise ContractError("CONTRACT_TOP_LEVEL_SCHEMA_NOT_EXACT")
    _exact(contract.get("schema_version"), 1, "UNSUPPORTED_SCHEMA_VERSION")
    _exact(contract.get("contract_id"), "lvef_multitask_c3_selected_reconstruction_v1", "CONTRACT_ID_CHANGED")
    _exact(contract.get("scientific_scope"), "prospective_selected_cohort_only_reconstruction", "SCIENTIFIC_SCOPE_CHANGED")
    authority = _mapping(contract.get("authority"), "AUTHORITY")
    source = _mapping(contract.get("source"), "SOURCE")
    billing = _mapping(contract.get("requester_pays"), "REQUESTER_PAYS")
    cloud = _mapping(contract.get("google_cloud_provenance"), "GOOGLE_CLOUD_PROVENANCE")
    storage = _mapping(contract.get("storage"), "STORAGE")
    batching = _mapping(contract.get("batching"), "BATCHING")
    download = _mapping(contract.get("download"), "DOWNLOAD")
    extraction = _mapping(contract.get("dicom_and_extraction"), "DICOM_AND_EXTRACTION")
    embedding = _mapping(contract.get("embedding"), "EMBEDDING")
    pooling = _mapping(contract.get("pooling"), "POOLING")
    environment = _mapping(contract.get("environment"), "ENVIRONMENT")
    scheduler = _mapping(contract.get("scheduler"), "SCHEDULER")
    retirement = _mapping(contract.get("cache_retirement"), "CACHE_RETIREMENT")
    preservation = _mapping(contract.get("preservation"), "PRESERVATION")
    analysis_export = _mapping(contract.get("analysis_and_export"), "ANALYSIS_AND_EXPORT")
    authorization = _mapping(contract.get("authorization"), "AUTHORIZATION")
    preauthorization = _mapping(
        contract.get("preauthorization_gates"), "PREAUTHORIZATION_GATES"
    )
    post_manuscript = _mapping(contract.get("post_manuscript"), "POST_MANUSCRIPT")

    _exact_keys(authority, {
        "accepted_abstract_version", "accepted_abstract_immutable", "historical_base_commit",
        "branch", "selected_manifest_sha256", "split_manifest_sha256",
        "frozen_selected_source_manifest_sha256", "command_commit",
        "source_preflight_summary_sha256", "resource_plan_sha256",
        "command_config_manifest_sha256",
    }, "AUTHORITY")
    _exact_keys(source, {
        "release", "bucket", "selected_subjects", "selected_studies",
        "normalized_source_requests", "exact_source_bytes", "outside_selected_studies_permitted",
        "historical_embedding_reuse_permitted", "exact_object_metadata_required",
        "source_body_download_authorized",
    }, "SOURCE")
    _exact_keys(billing, {
        "enabled", "authority_scope", "billing_project_environment_variable",
        "billing_project_may_be_written_to_git", "authentication_and_billing_gate_required",
    }, "REQUESTER_PAYS")
    _exact_keys(cloud, {
        "historical_authority", "active_prospective_authority", "credential_state", "free_trial",
    }, "GOOGLE_CLOUD_PROVENANCE")
    cloud_historical = _mapping(cloud.get("historical_authority"), "GCP_HISTORICAL_AUTHORITY")
    cloud_active = _mapping(cloud.get("active_prospective_authority"), "GCP_ACTIVE_PROSPECTIVE_AUTHORITY")
    cloud_credentials = _mapping(cloud.get("credential_state"), "GCP_CREDENTIAL_STATE")
    cloud_trial = _mapping(cloud.get("free_trial"), "GCP_FREE_TRIAL")
    _exact_keys(cloud_historical, {
        "classification", "preserve_captured_account_and_project_association",
        "unknown_association_disposition", "prospective_project_backfill_permitted",
    }, "GCP_HISTORICAL_AUTHORITY")
    _exact_keys(cloud_active, {
        "classification", "exact_identity_storage", "exact_project_storage",
        "expected_identity_environment_variable", "expected_project_display_name_environment_variable",
        "expected_project_environment_variable",
        "requester_pays_project_environment_variable", "bigquery_billing_project_environment_variable",
        "exact_identifiers_may_be_written_to_git", "activation_requires_identity_match",
        "activation_requires_project_match", "activation_requires_active_billing_link",
        "activation_requires_requester_pays_metadata_access",
        "activation_requires_bigquery_billing_access",
    }, "GCP_ACTIVE_PROSPECTIVE_AUTHORITY")
    _exact_keys(cloud_credentials, {
        "classification", "credentials_remain_scc_only", "oauth_tokens_may_be_printed",
        "credential_files_may_be_written_to_git",
        "billing_account_or_payment_details_may_be_exported",
        "requester_pays_session_files_may_be_written_to_git",
    }, "GCP_CREDENTIAL_STATE")
    _exact_keys(cloud_trial, {
        "api_verification_status", "project_existence_is_trial_evidence",
        "billing_enabled_is_trial_evidence", "trial_credit_required_for_scientific_authority",
    }, "GCP_FREE_TRIAL")
    _exact_keys(storage, {
        "output_root", "raw_dicom_root", "extracted_cache_root",
        "raw_dicoms_retained_through_active_analysis", "raw_dicom_deletion_permitted_by_this_contract",
        "maximum_concurrent_extracted_batches", "extracted_cache_retirement_permitted_after_authorization",
        "partial_files",
    }, "STORAGE")
    _exact_keys(batching, {
        "method", "sort_keys", "studies_per_batch", "batch_prefix",
        "keep_study_objects_in_one_batch", "maximum_active_extracted_batches",
    }, "BATCHING")
    _exact_keys(download, {
        "exact_object_only", "prefix_body_copy_permitted", "retries", "retry_backoff_seconds",
        "resumable", "local_size_must_match_remote", "local_md5_must_match_remote",
        "local_sha256_required",
    }, "DOWNLOAD")
    _exact_keys(extraction, {
        "header_audit_before_pixel_decode", "cine_candidate_rule", "decoder_api",
        "decoder_plugin_priority", "native_transfer_syntax_uids",
        "supported_photometric_interpretations", "required_bits_allocated", "required_bits_stored",
        "canonical_color_space", "ybr_conversion", "monochrome1_rule", "monochrome2_rule",
        "masking_policy", "crop_resize_policy", "opencv_threads", "source_and_sampled_quality_gates",
        "target_frames", "target_height", "target_width", "output_dtype", "temporal_sampling",
        "one_canonical_row_per_physical_clip", "unique_physical_source_key_fields",
    }, "DICOM_AND_EXTRACTION")
    _exact_keys(embedding, {
        "encoder", "encoder_only", "view_classifier_used", "checkpoint_filename", "checkpoint_bytes",
        "checkpoint_sha256", "dimension", "dtype", "deterministic_torch_algorithms_required",
        "normalization_mean", "normalization_std", "tensor_layout", "temporal_stride",
        "encoder_input_frames", "autocast_used", "tf32_allowed", "cublas_workspace_config",
        "batch_size", "random_seed",
    }, "EMBEDDING")
    _exact_keys(pooling, {
        "method", "stable_clip_key_order_required", "accumulation_dtype", "output_dtype",
        "one_vector_per_imaging_eligible_study",
    }, "POOLING")
    _exact_keys(environment, {
        "python_executable", "python_version", "python_executable_sha256", "pytorch_version",
        "torchvision_version", "cuda_runtime", "cudnn_version", "full_package_inventory_required",
        "allocated_gpu_identity_required",
    }, "ENVIRONMENT")
    _exact_keys(scheduler, {
        "system", "array_tasks", "maximum_concurrent_array_tasks",
        "production_concurrency_batches", "extraction_threads",
        "gpu_slots_per_embedding_job", "dependent_finalizer_required",
        "ambient_environment_export_permitted", "job_identity_required",
        "stdout_stderr_restricted",
    }, "SCHEDULER")
    _exact_keys(retirement, {
        "enabled_only_after_owner_authorization", "raw_dicom_retirement_prohibited",
        "required_batch_gates", "require_marker_file", "require_batch_manifest_sha256_match",
        "require_preservation_manifest_sha256_match", "require_cache_inventory_sha256_match",
        "batch_cache_manifest_schema", "batch_preservation_manifest_schema",
        "require_contract_sha256_match", "execute_confirmation_environment_variable",
    }, "CACHE_RETIREMENT")
    _exact_keys(preservation, {
        "safe_relative_paths_only", "sizes_and_sha256_required", "source_commit_required",
        "command_and_config_sha256_required", "checkpoint_sha256_required",
        "environment_and_package_inventory_required", "scheduler_job_identity_required",
        "run_timestamp_required", "cohort_split_panel_model_versions_required",
        "aggregate_safety_gate_required", "denominator_manifest_required",
    }, "PRESERVATION")
    _exact_keys(analysis_export, {
        "restricted_analysis_mode", "export_mode", "detailed_outputs_remain_restricted",
        "export_safety_gate_required",
    }, "ANALYSIS_AND_EXPORT")
    _exact_keys(authorization, {
        "full_c3_execution", "source_body_download", "full_extraction", "full_embedding",
        "cache_retirement", "predictive_modeling", "confirmatory_performance_access",
        "owner_name_or_initials", "owner_authorization_date", "owner_authorized_commit",
        "owner_authorized_contract_sha256",
    }, "AUTHORIZATION")
    _exact_keys(post_manuscript, {"archival_or_purge_decision"}, "POST_MANUSCRIPT")
    if contract.get("status") not in {"UNAUTHORIZED_PHASE_1E_BC", "AUTHORIZED_FULL_C3"}:
        raise ContractError("CONTRACT_STATUS_INVALID")

    _exact(authority.get("accepted_abstract_version"), 10, "ABSTRACT_VERSION_CHANGED")
    _exact(authority.get("accepted_abstract_immutable"), True, "ABSTRACT_NOT_IMMUTABLE")
    _exact(authority.get("branch"), EXPECTED_BRANCH, "WRONG_BRANCH")
    _exact(
        authority.get("historical_base_commit"),
        "23c74ccfd145ab9a423b6942a431a1894a34ab67",
        "HISTORICAL_BASE_CHANGED",
    )
    _exact(
        authority.get("selected_manifest_sha256"),
        EXPECTED_SELECTED_MANIFEST_SHA256,
        "SELECTED_AUTHORITY_CHANGED",
    )
    _exact(
        authority.get("split_manifest_sha256"),
        EXPECTED_SPLIT_MANIFEST_SHA256,
        "SPLIT_AUTHORITY_CHANGED",
    )

    _exact(source.get("release"), EXPECTED_RELEASE, "WRONG_SOURCE_RELEASE")
    _exact(source.get("bucket"), EXPECTED_BUCKET, "WRONG_SOURCE_BUCKET")
    _exact(source.get("selected_subjects"), 4530, "WRONG_SELECTED_SUBJECT_COUNT")
    _exact(source.get("selected_studies"), 4530, "WRONG_SELECTED_STUDY_COUNT")
    _exact(source.get("normalized_source_requests"), 335984, "WRONG_SOURCE_REQUEST_COUNT")
    _exact(source.get("outside_selected_studies_permitted"), False, "OUTSIDE_SELECTED_ALLOWED")
    _exact(source.get("historical_embedding_reuse_permitted"), False, "HISTORICAL_REUSE_ALLOWED")
    if not isinstance(source.get("source_body_download_authorized"), bool):
        raise ContractError("SOURCE_DOWNLOAD_AUTHORITY_NOT_BOOLEAN")
    required_metadata = set(source.get("exact_object_metadata_required") or [])
    if required_metadata != {
        "size_bytes",
        "md5_base64",
        "crc32c_base64",
        "generation",
        "storage_class",
        "updated",
    }:
        raise ContractError("REMOTE_METADATA_SET_NOT_EXACT")

    _exact(billing.get("enabled"), True, "REQUESTER_PAYS_NOT_REQUIRED")
    _exact(billing.get("authority_scope"), "ACTIVE_PROSPECTIVE_AUTHORITY", "REQUESTER_PAYS_AUTHORITY_SCOPE_CHANGED")
    billing_env = str(billing.get("billing_project_environment_variable", ""))
    if billing_env != "LVEF_C3_GCP_BILLING_PROJECT":
        raise ContractError("BILLING_ENVIRONMENT_VARIABLE_CHANGED")
    _exact(billing.get("billing_project_may_be_written_to_git"), False, "BILLING_PROJECT_EXPORT_ALLOWED")
    _exact(billing.get("authentication_and_billing_gate_required"), True, "GCP_AUTHENTICATION_GATE_DISABLED")

    _exact(cloud_historical.get("classification"), "HISTORICAL_AUTHORITY", "GCP_HISTORICAL_CLASS_CHANGED")
    _exact(cloud_historical.get("preserve_captured_account_and_project_association"), True, "GCP_HISTORICAL_PROVENANCE_REWRITE_ALLOWED")
    _exact(cloud_historical.get("unknown_association_disposition"), "UNKNOWN_NOT_RETROACTIVELY_ASSIGNED", "GCP_UNKNOWN_HISTORY_DISPOSITION_CHANGED")
    _exact(cloud_historical.get("prospective_project_backfill_permitted"), False, "GCP_PROSPECTIVE_PROJECT_BACKFILL_ALLOWED")
    _exact(cloud_active.get("classification"), "ACTIVE_PROSPECTIVE_AUTHORITY", "GCP_ACTIVE_CLASS_CHANGED")
    _exact(cloud_active.get("exact_identity_storage"), "SCC_ONLY_MODE_600", "GCP_IDENTITY_STORAGE_NOT_RESTRICTED")
    _exact(cloud_active.get("exact_project_storage"), "SCC_ONLY_MODE_600", "GCP_PROJECT_STORAGE_NOT_RESTRICTED")
    _exact(cloud_active.get("expected_identity_environment_variable"), "LVEF_C3_EXPECTED_GCP_ACCOUNT", "GCP_IDENTITY_ENVIRONMENT_VARIABLE_CHANGED")
    _exact(cloud_active.get("expected_project_display_name_environment_variable"), "LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME", "GCP_PROJECT_DISPLAY_NAME_ENVIRONMENT_VARIABLE_CHANGED")
    for key in (
        "expected_project_environment_variable",
        "requester_pays_project_environment_variable",
        "bigquery_billing_project_environment_variable",
    ):
        _exact(cloud_active.get(key), "LVEF_C3_GCP_BILLING_PROJECT", f"GCP_{key.upper()}_CHANGED")
    for key in (
        "activation_requires_identity_match",
        "activation_requires_project_match",
        "activation_requires_active_billing_link",
        "activation_requires_requester_pays_metadata_access",
        "activation_requires_bigquery_billing_access",
    ):
        _exact(cloud_active.get(key), True, f"GCP_{key.upper()}_DISABLED")
    _exact(cloud_active.get("exact_identifiers_may_be_written_to_git"), False, "GCP_EXACT_IDENTIFIERS_EXPORT_ALLOWED")
    _exact(cloud_credentials.get("classification"), "CREDENTIAL_STATE", "GCP_CREDENTIAL_CLASS_CHANGED")
    _exact(cloud_credentials.get("credentials_remain_scc_only"), True, "GCP_CREDENTIALS_NOT_RESTRICTED")
    for key in (
        "oauth_tokens_may_be_printed",
        "credential_files_may_be_written_to_git",
        "billing_account_or_payment_details_may_be_exported",
        "requester_pays_session_files_may_be_written_to_git",
    ):
        _exact(cloud_credentials.get(key), False, f"GCP_{key.upper()}_ALLOWED")
    _exact(cloud_trial.get("api_verification_status"), "NOT_API_VERIFIABLE_REQUIRES_OWNER_CONSOLE_OR_BILLING_RECORD", "GCP_FREE_TRIAL_API_CLAIM_CHANGED")
    _exact(cloud_trial.get("project_existence_is_trial_evidence"), False, "GCP_PROJECT_EXISTENCE_MISCLASSIFIED_AS_TRIAL_EVIDENCE")
    _exact(cloud_trial.get("billing_enabled_is_trial_evidence"), False, "GCP_BILLING_LINK_MISCLASSIFIED_AS_TRIAL_EVIDENCE")
    _exact(cloud_trial.get("trial_credit_required_for_scientific_authority"), False, "GCP_TRIAL_CREDIT_MISCLASSIFIED_AS_SCIENTIFIC_AUTHORITY")

    output_root = Path(str(storage.get("output_root", "")))
    raw_root = Path(str(storage.get("raw_dicom_root", "")))
    cache_root = Path(str(storage.get("extracted_cache_root", "")))
    expected_root = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3")
    if (
        output_root != expected_root
        or raw_root != output_root / "raw_dicoms"
        or cache_root != output_root / "extracted_cache"
    ):
        raise ContractError("STORAGE_ROOTS_NOT_LOCKED")
    _exact(storage.get("raw_dicoms_retained_through_active_analysis"), True, "RAW_RETENTION_DISABLED")
    _exact(storage.get("raw_dicom_deletion_permitted_by_this_contract"), False, "RAW_DELETION_ALLOWED")
    maximum_cache = storage.get("maximum_concurrent_extracted_batches")
    if maximum_cache != 1:
        raise ContractError("EXTRACTED_CACHE_CONCURRENCY_OUTSIDE_LOCK")
    _exact(storage.get("extracted_cache_retirement_permitted_after_authorization"), True, "CACHE_RETIREMENT_DISABLED")
    partial = _mapping(storage.get("partial_files"), "PARTIAL_FILES")
    _exact_keys(partial, {"suffix", "atomic_rename_after_verified_size_and_md5", "stale_partial_files_may_be_retried"}, "PARTIAL_FILES")
    _exact(partial.get("suffix"), ".partial", "PARTIAL_SUFFIX_CHANGED")
    _exact(partial.get("atomic_rename_after_verified_size_and_md5"), True, "PARTIAL_ATOMICITY_DISABLED")
    _exact(partial.get("stale_partial_files_may_be_retried"), True, "PARTIAL_RETRY_DISABLED")

    _exact(batching.get("method"), "sorted_selected_study_authority_contiguous_chunks", "BATCHING_METHOD_CHANGED")
    _exact(batching.get("sort_keys"), ["subject_id_numeric", "study_id_numeric"], "BATCH_SORT_CHANGED")
    _exact(batching.get("studies_per_batch"), 250, "BATCH_SIZE_CHANGED")
    _exact(batching.get("batch_prefix"), "c3_batch_", "BATCH_PREFIX_CHANGED")
    _exact(batching.get("keep_study_objects_in_one_batch"), True, "STUDY_MAY_SPAN_BATCHES")
    _exact(batching.get("maximum_active_extracted_batches"), maximum_cache, "CACHE_CONCURRENCY_DISAGREES")

    _exact(download.get("exact_object_only"), True, "DOWNLOAD_NOT_EXACT_OBJECT_ONLY")
    _exact(download.get("prefix_body_copy_permitted"), False, "PREFIX_BODY_COPY_ALLOWED")
    _exact(download.get("retries"), 4, "DOWNLOAD_RETRIES_CHANGED")
    _exact(download.get("retry_backoff_seconds"), [5, 20, 60, 180], "DOWNLOAD_BACKOFF_CHANGED")
    _exact(download.get("resumable"), True, "DOWNLOAD_NOT_RESUMABLE")
    for key in ("local_size_must_match_remote", "local_md5_must_match_remote", "local_sha256_required"):
        _exact(download.get(key), True, f"DOWNLOAD_GATE_{key.upper()}_DISABLED")

    _exact(extraction.get("header_audit_before_pixel_decode"), True, "HEADER_AUDIT_DISABLED")
    _exact(extraction.get("cine_candidate_rule"), "number_of_frames_greater_than_one", "CINE_RULE_CHANGED")
    _exact(extraction.get("decoder_api"), "pydicom_3_pixels_pixel_array_raw_true", "DECODER_API_CHANGED")
    _exact(extraction.get("decoder_plugin_priority"), ["pylibjpeg", "gdcm", "pillow", "pyjpegls"], "DECODER_PRIORITY_CHANGED")
    _exact(extraction.get("native_transfer_syntax_uids"), [
        "1.2.840.10008.1.2", "1.2.840.10008.1.2.1",
        "1.2.840.10008.1.2.1.99", "1.2.840.10008.1.2.2",
    ], "NATIVE_TRANSFER_SYNTAX_SET_CHANGED")
    _exact(extraction.get("supported_photometric_interpretations"), [
        "MONOCHROME1", "MONOCHROME2", "RGB", "YBR_FULL", "YBR_FULL_422",
    ], "PHOTOMETRIC_SET_CHANGED")
    _exact(extraction.get("required_bits_allocated"), 8, "BITS_ALLOCATED_CHANGED")
    _exact(extraction.get("required_bits_stored"), 8, "BITS_STORED_CHANGED")
    _exact(extraction.get("canonical_color_space"), "RGB", "COLOR_SPACE_CHANGED")
    _exact(extraction.get("ybr_conversion"), "explicit_once_after_raw_decode", "YBR_CONVERSION_CHANGED")
    _exact(extraction.get("monochrome1_rule"), "invert_then_replicate_to_rgb", "MONOCHROME1_RULE_CHANGED")
    _exact(extraction.get("monochrome2_rule"), "replicate_to_rgb", "MONOCHROME2_RULE_CHANGED")
    _exact(extraction.get("masking_policy"), "strict_ultrasound_sector_v1", "MASK_POLICY_CHANGED")
    _exact(extraction.get("crop_resize_policy"), "aspect_center_crop_then_10_percent_zoom_then_cv2_inter_cubic", "CROP_RESIZE_CHANGED")
    _exact(extraction.get("opencv_threads"), 1, "OPENCV_THREAD_COUNT_CHANGED")
    _exact(extraction.get("source_and_sampled_quality_gates"), ["nonempty_sector", "retained_signal", "temporal_variation"], "QUALITY_GATES_CHANGED")
    _exact(extraction.get("target_frames"), 32, "TARGET_FRAMES_CHANGED")
    _exact(extraction.get("target_height"), 224, "TARGET_HEIGHT_CHANGED")
    _exact(extraction.get("target_width"), 224, "TARGET_WIDTH_CHANGED")
    _exact(extraction.get("temporal_sampling"), "historical_compatible_linspace_or_tail_repeat_v1", "TEMPORAL_SAMPLING_CHANGED")
    _exact(extraction.get("one_canonical_row_per_physical_clip"), True, "PHYSICAL_CLIP_UNIQUENESS_DISABLED")
    _exact(extraction.get("output_dtype"), "uint8", "EXTRACTION_DTYPE_CHANGED")
    _exact(extraction.get("unique_physical_source_key_fields"), [
        "release", "normalized_source_locator", "source_generation", "extracted_clip_index",
    ], "PHYSICAL_SOURCE_KEY_CHANGED")

    _exact(embedding.get("encoder"), "EchoPrime", "ENCODER_CHANGED")
    _exact(embedding.get("encoder_only"), True, "NONENCODER_COMPONENT_ALLOWED")
    _exact(embedding.get("view_classifier_used"), False, "VIEW_CLASSIFIER_ALLOWED")
    _exact(embedding.get("checkpoint_bytes"), 138642379, "CHECKPOINT_SIZE_CHANGED")
    _exact(embedding.get("checkpoint_filename"), "echo_prime_encoder.pt", "CHECKPOINT_FILENAME_CHANGED")
    _exact(embedding.get("checkpoint_sha256"), EXPECTED_CHECKPOINT_SHA256, "CHECKPOINT_SHA256_CHANGED")
    _exact(embedding.get("dimension"), 512, "EMBEDDING_DIMENSION_CHANGED")
    _exact(embedding.get("dtype"), "float32", "EMBEDDING_DTYPE_CHANGED")
    _exact(embedding.get("deterministic_torch_algorithms_required"), True, "DETERMINISM_DISABLED")
    _exact(embedding.get("normalization_mean"), [29.110628, 28.076836, 29.096405], "NORMALIZATION_MEAN_CHANGED")
    _exact(embedding.get("normalization_std"), [47.989223, 46.456997, 47.20083], "NORMALIZATION_STD_CHANGED")
    _exact(embedding.get("tensor_layout"), "C_T_H_W", "TENSOR_LAYOUT_CHANGED")
    _exact(embedding.get("temporal_stride"), 2, "EMBEDDING_TEMPORAL_STRIDE_CHANGED")
    _exact(embedding.get("encoder_input_frames"), 16, "ENCODER_FRAME_COUNT_CHANGED")
    _exact(embedding.get("autocast_used"), False, "AUTOCAST_ENABLED")
    _exact(embedding.get("tf32_allowed"), False, "TF32_ENABLED")
    _exact(embedding.get("cublas_workspace_config"), ":4096:8", "CUBLAS_WORKSPACE_CHANGED")
    _exact(embedding.get("batch_size"), 8, "EMBEDDING_BATCH_SIZE_CHANGED")
    _exact(embedding.get("random_seed"), 20260803, "RANDOM_SEED_CHANGED")

    _exact(pooling.get("method"), "arithmetic_mean", "POOLING_METHOD_CHANGED")
    _exact(pooling.get("stable_clip_key_order_required"), True, "POOLING_ORDER_NOT_STABLE")
    _exact(pooling.get("accumulation_dtype"), "float64", "POOLING_ACCUMULATION_CHANGED")
    _exact(pooling.get("output_dtype"), "float32", "POOLING_OUTPUT_CHANGED")
    _exact(pooling.get("one_vector_per_imaging_eligible_study"), True, "POOLING_DENOMINATOR_CHANGED")

    _exact(environment.get("python_executable"), "/restricted/projectnb/mimicecho/envs/echoprime-c3-py310/bin/python", "PYTHON_EXECUTABLE_PATH_CHANGED")
    _exact(environment.get("python_version"), "3.10.12", "PYTHON_VERSION_CHANGED")
    _exact(environment.get("pytorch_version"), "2.11.0+cu130", "PYTORCH_VERSION_CHANGED")
    _exact(environment.get("torchvision_version"), "0.26.0+cu130", "TORCHVISION_VERSION_CHANGED")
    _exact(environment.get("cuda_runtime"), "13.0", "CUDA_RUNTIME_CHANGED")
    _exact(environment.get("cudnn_version"), "CAPTURE_FROM_ALLOCATED_JOB", "CUDNN_AUTHORITY_CHANGED")
    _exact(environment.get("full_package_inventory_required"), True, "PACKAGE_INVENTORY_DISABLED")
    _exact(environment.get("allocated_gpu_identity_required"), True, "GPU_IDENTITY_DISABLED")
    _exact(environment.get("python_executable_sha256"), EXPECTED_PYTHON_EXECUTABLE_SHA256, "PYTHON_SHA256_CHANGED")
    _exact(scheduler.get("system"), "SGE", "SCHEDULER_CHANGED")
    _exact(scheduler.get("array_tasks"), 19, "SCHEDULER_ARRAY_SIZE_CHANGED")
    _exact(
        scheduler.get("maximum_concurrent_array_tasks"),
        maximum_cache,
        "SCHEDULER_ARRAY_CONCURRENCY_OUTSIDE_LOCK",
    )
    _exact(scheduler.get("production_concurrency_batches"), maximum_cache, "PRODUCTION_CONCURRENCY_OUTSIDE_LOCK")
    _exact(scheduler.get("extraction_threads"), 4, "EXTRACTION_THREAD_COUNT_CHANGED")
    _exact(scheduler.get("gpu_slots_per_embedding_job"), 1, "GPU_SLOT_COUNT_CHANGED")
    _exact(scheduler.get("dependent_finalizer_required"), True, "FINALIZER_NOT_REQUIRED")
    _exact(
        scheduler.get("ambient_environment_export_permitted"),
        False,
        "AMBIENT_ENVIRONMENT_EXPORT_ALLOWED",
    )
    _exact(scheduler.get("job_identity_required"), True, "JOB_IDENTITY_DISABLED")
    _exact(scheduler.get("stdout_stderr_restricted"), True, "SCHEDULER_LOG_EXPORT_ALLOWED")

    _exact(retirement.get("raw_dicom_retirement_prohibited"), True, "RAW_RETIREMENT_NOT_PROHIBITED")
    _exact(retirement.get("enabled_only_after_owner_authorization"), True, "CACHE_RETIREMENT_OWNER_GATE_DISABLED")
    if set(retirement.get("required_batch_gates") or []) != REQUIRED_CACHE_GATES:
        raise ContractError("CACHE_RETIREMENT_GATES_NOT_EXACT")
    _exact(retirement.get("require_marker_file"), ".c3_extracted_cache_marker.json", "CACHE_MARKER_CHANGED")
    _exact(retirement.get("batch_cache_manifest_schema"), "c3_extracted_cache_manifest_v1_json", "CACHE_MANIFEST_SCHEMA_CHANGED")
    _exact(retirement.get("batch_preservation_manifest_schema"), "c3_batch_preservation_manifest_v1_json", "PRESERVATION_MANIFEST_SCHEMA_CHANGED")
    _exact(retirement.get("execute_confirmation_environment_variable"), "LVEF_C3_CACHE_RETIRE_CONFIRMATION", "CACHE_CONFIRMATION_VARIABLE_CHANGED")
    for key in (
        "require_batch_manifest_sha256_match",
        "require_preservation_manifest_sha256_match",
        "require_cache_inventory_sha256_match",
        "require_contract_sha256_match",
    ):
        _exact(retirement.get(key), True, f"CACHE_{key.upper()}_DISABLED")

    for key in (
        "safe_relative_paths_only",
        "sizes_and_sha256_required",
        "source_commit_required",
        "command_and_config_sha256_required",
        "checkpoint_sha256_required",
        "environment_and_package_inventory_required",
        "scheduler_job_identity_required",
        "run_timestamp_required",
        "cohort_split_panel_model_versions_required",
        "aggregate_safety_gate_required",
        "denominator_manifest_required",
    ):
        _exact(preservation.get(key), True, f"PRESERVATION_GATE_{key.upper()}_DISABLED")

    _exact(analysis_export.get("restricted_analysis_mode"), "APPROVED_DIRECT_RESTRICTED_AGENT", "WRONG_RESTRICTED_ANALYSIS_MODE")
    _exact(analysis_export.get("export_mode"), "MANUSCRIPT_OR_GIT_EXPORT", "WRONG_EXPORT_MODE")
    _exact(analysis_export.get("detailed_outputs_remain_restricted"), True, "DETAIL_EXPORT_ALLOWED")
    _exact(analysis_export.get("export_safety_gate_required"), True, "EXPORT_SAFETY_GATE_DISABLED")
    _exact(post_manuscript.get("archival_or_purge_decision"), "PENDING_SEPARATE_OWNER_AND_INSTITUTIONAL_REVIEW", "POST_MANUSCRIPT_DISPOSITION_PREAUTHORIZED")

    for key in AUTHORIZATION_ACTIONS + ("predictive_modeling", "confirmatory_performance_access"):
        if not isinstance(authorization.get(key), bool):
            raise ContractError(f"AUTHORIZATION_{key.upper()}_NOT_BOOLEAN")
    _exact(authorization.get("predictive_modeling"), False, "MODELING_PREAUTHORIZED")
    _exact(authorization.get("confirmatory_performance_access"), False, "PERFORMANCE_ACCESS_PREAUTHORIZED")
    if set(preauthorization) != REQUIRED_PREAUTHORIZATION_GATES:
        raise ContractError("PREAUTHORIZATION_GATE_SET_NOT_EXACT")
    if any(not isinstance(value, bool) for value in preauthorization.values()):
        raise ContractError("PREAUTHORIZATION_GATE_NOT_BOOLEAN")


def authorization_reasons(
    contract: Mapping[str, Any],
    *,
    contract_sha256: str,
    owner_authorization: Mapping[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    authority = _mapping(contract["authority"], "AUTHORITY")
    source = _mapping(contract["source"], "SOURCE")
    authorization = _mapping(contract["authorization"], "AUTHORIZATION")
    preauthorization = _mapping(
        contract["preauthorization_gates"], "PREAUTHORIZATION_GATES"
    )

    if contract.get("status") != "AUTHORIZED_FULL_C3":
        reasons.append("CONTRACT_STATUS_NOT_AUTHORIZED")
    for key in AUTHORIZATION_ACTIONS:
        if authorization.get(key) is not True:
            reasons.append(f"{key.upper()}_NOT_AUTHORIZED")
    if source.get("source_body_download_authorized") is not True:
        reasons.append("SOURCE_CONTRACT_BODY_DOWNLOAD_NOT_AUTHORIZED")
    source_sha = authority.get("frozen_selected_source_manifest_sha256")
    if not isinstance(source_sha, str) or not SHA256_RE.fullmatch(source_sha):
        reasons.append("FROZEN_SELECTED_SOURCE_MANIFEST_SHA256_PENDING")
    if not isinstance(source.get("exact_source_bytes"), int) or source["exact_source_bytes"] <= 0:
        reasons.append("EXACT_SOURCE_BYTES_PENDING")
    for field in (
        "source_preflight_summary_sha256",
        "resource_plan_sha256",
        "command_config_manifest_sha256",
    ):
        value = authority.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            reasons.append(f"{field.upper()}_PENDING_OR_INVALID")
    command_commit = authority.get("command_commit")
    if not isinstance(command_commit, str) or not GIT_SHA1_RE.fullmatch(command_commit):
        reasons.append("COMMAND_COMMIT_PENDING_OR_INVALID")
    for gate, passed in preauthorization.items():
        if passed is not True:
            reasons.append(f"PREAUTHORIZATION_{gate.upper()}_NOT_PASSED")

    if owner_authorization is None:
        reasons.append("SEPARATE_OWNER_AUTHORIZATION_RECORD_ABSENT")
    else:
        if owner_authorization.get("status") != "AUTHORIZED_FULL_C3":
            reasons.append("OWNER_AUTHORIZATION_STATUS_INVALID")
        if owner_authorization.get("contract_sha256") != contract_sha256:
            reasons.append("OWNER_AUTHORIZATION_CONTRACT_SHA256_MISMATCH")
        if owner_authorization.get("selected_source_manifest_sha256") != source_sha:
            reasons.append("OWNER_AUTHORIZATION_SOURCE_SHA256_MISMATCH")
        if owner_authorization.get("authorized_commit") != command_commit:
            reasons.append("OWNER_AUTHORIZATION_COMMIT_MISMATCH")
        for key in ("owner_name_or_initials", "authorization_date", "authorized_commit"):
            if not owner_authorization.get(key):
                reasons.append(f"OWNER_AUTHORIZATION_{key.upper()}_MISSING")
    return sorted(set(reasons))


def validate_contract(
    path: Path, owner_authorization_path: Path | None = None
) -> dict[str, Any]:
    contract = load_contract(path)
    validate_structure(contract)
    contract_sha256 = sha256_file(path)
    owner_authorization: Mapping[str, Any] | None = None
    owner_sha256: str | None = None
    if owner_authorization_path is not None:
        if owner_authorization_path.is_symlink() or not owner_authorization_path.is_file():
            raise ContractError("OWNER_AUTHORIZATION_NOT_REGULAR_FILE")
        parsed = json.loads(owner_authorization_path.read_text(encoding="utf-8"))
        owner_authorization = _mapping(parsed, "OWNER_AUTHORIZATION")
        owner_sha256 = sha256_file(owner_authorization_path)
    reasons = authorization_reasons(
        contract,
        contract_sha256=contract_sha256,
        owner_authorization=owner_authorization,
    )
    return {
        "schema_version": 1,
        "status": "PASS_AUTHORIZED" if not reasons else "PASS_CONTRACT_NO_GO",
        "contract_valid": True,
        "execution_authorized": not reasons,
        "contract_sha256": contract_sha256,
        "owner_authorization_sha256": owner_sha256,
        "authorization_blockers": reasons,
        "raw_dicom_deletion_prohibited": True,
        "historical_embedding_reuse_permitted": False,
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "normalized_source_requests": 335984,
        "checkpoint_sha256": EXPECTED_CHECKPOINT_SHA256,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--owner-authorization", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-authorized", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_contract(args.contract, args.owner_authorization)
    except (ContractError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        result = {
            "schema_version": 1,
            "status": "FAIL_INVALID_CONTRACT",
            "contract_valid": False,
            "execution_authorized": False,
            "error_code": str(exc),
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if args.require_authorized and not result["execution_authorized"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
