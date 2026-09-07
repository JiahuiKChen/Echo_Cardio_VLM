#!/usr/bin/env python3
"""Fail-closed cross-batch finalizer for prospective selected-cohort C3.

The finalizer consumes restricted, checksummed batch-preservation receipts,
independently replays study pooling, and emits one canonical restricted study
store plus a closed-schema aggregate summary.  It never repairs, downloads,
decodes, embeds, deletes, or follows symlinks.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core
import capture_lvef_c3_post_reallocation_capacity as r8r_capacity
import lvef_c3_production_stages as production_stages
import preserve_lvef_c3_production_batch as preservation


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
    "extraction_ledger",
    "pooling_ledger",
}
CLIP_MANIFEST_HEADER = [
    "embedding_idx", "subject_id", "study_id", "clip_key",
    "physical_source_key", "embedding_l2_norm", "embedding_sha256", "write_ok",
]
STUDY_MANIFEST_HEADER = [
    "study_idx", "subject_id", "study_id", "n_clips", "embedding_sha256",
]
DISPOSITION_HEADER = ["subject_id", "study_id", "disposition"]
CANONICAL_STUDY_MANIFEST_HEADER = [
    "study_idx", "subject_id", "study_id", "batch_id", "n_clips",
    "embedding_sha256",
]
CANONICAL_STUDY_EMBEDDINGS_NAME = "canonical_study_embeddings.restricted.npz"
CANONICAL_STUDY_MANIFEST_NAME = "canonical_study_manifest.restricted.csv"
CANONICAL_STUDY_RECEIPT_NAME = "canonical_study_store.restricted.json"
CANONICAL_CLIP_INDEX_NAME = "canonical_clip_index.restricted.csv"
COHORT_PRESERVATION_RECEIPT_NAME = "cohort_preservation_receipt.restricted.json"
CANONICAL_CLIP_INDEX_HEADER = [
    "clip_idx", "batch_id", "batch_embedding_idx", "subject_id", "study_id",
    "clip_key", "physical_source_key", "embedding_sha256",
    "batch_clip_manifest_sha256", "batch_clip_embeddings_sha256",
]
CANONICAL_STUDY_RECEIPT_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "governing_commit",
    "attempt_id",
    "batch_plan_sha256",
    "batch_receipt_set_sha256",
    "study_embeddings",
    "embedding_dimension",
    "embedding_dtype",
    "study_embeddings_sha256",
    "study_embeddings_size_bytes",
    "study_manifest_sha256",
    "study_manifest_size_bytes",
    "pooling",
    "exact_pooling_replay_passed",
    "stable_plan_order",
    "duplicate_study_keys",
    "no_cine_studies",
    "identifiers_emitted",
    "restricted_paths_emitted",
}
COHORT_ARTIFACT_KEYS = {"role", "relative_path", "size_bytes", "sha256"}
COHORT_PRESERVATION_RECEIPT_KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "governing_commit",
    "attempt_id",
    "batch_plan_sha256",
    "batch_receipt_set_sha256",
    "production_batches",
    "clip_embeddings",
    "study_embeddings",
    "no_cine_studies",
    "successfully_extracted_cines",
    "object_technical_dispositions",
    "blocking_failures",
    "studies_affected_by_technical_disposition",
    "new_no_cine_studies",
    "technical_disposition_counts_by_class",
    "technical_disposition_policy_version",
    "technical_disposition_manifest_set_sha256",
    "prespecified_no_cine_study_set_sha256",
    "all_no_cine_studies_prespecified",
    "all_extraction_rows_resolved",
    "all_successful_extractions_embedded",
    "all_technical_dispositions_retained",
    "object_substitution_count",
    "unaccounted_multiframe_objects",
    "artifacts",
    "second_pass_replay_passed",
    "raw_dicoms_retained",
    "extracted_cache_retired",
    "identifiers_emitted",
    "restricted_paths_emitted",
}

# Phase 1I-R8R is a fixed repair of one already materialized scientific
# attempt.  These constants deliberately do not generalize to another
# attempt, plan, prefix length, or implementation split.
R8R_SCIENTIFIC_GOVERNING_COMMIT = (
    "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
)
R8R_ATTEMPT_ID = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
R8R_BATCH_PLAN_SHA256 = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
R8R_HISTORICAL_PREFIX_RECEIPT_AUTHORITIES = {
    "c3_batch_000": (
        4_730,
        "e8f1b505422af64fc98c44f1cb85da52528f014c904e1cbfe319ca0109f31277",
    ),
    "c3_batch_001": (
        4_725,
        "54c536f5c2faa712bc3a97d18c6c6fde804941d096dc307dbaf50e6c33e32c98",
    ),
}
R8R_SCHEDULER_RUNNER_BASENAME = (
    "scc_run_lvef_c3_r8r_recovery_continuation.sh"
)

# Phase 1I-R8U-R2 is a destination-specific implementation repair over the
# same immutable scientific attempt.  Batches 1--2 belong to the original
# implementation, Batches 3--15 to the fixed R8R commit below, and Batches
# 16--19 to the clean current R8U-R2 commit.  The fixed R8U base and projection
# commits remain distinct authority epochs; no arbitrary-batch or cross-attempt
# recovery interface is reachable.
R8U_PRIOR_IMPLEMENTATION_COMMIT = (
    "fe3b6c40162d16d5021558bc686ba93c05ab03f5"
)
R8U_BASE_IMPLEMENTATION_COMMIT = (
    "cbd54ec67a24bc26e538be0423df38cee8a9eb6f"
)
R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT = (
    "f3df5cd969ff70c87378657767c5bf2b92d4e074"
)
R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = frozenset(
    {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
    }
)

# Phase 1I-R8U-R3 is additive to the immutable R8U-R2 failure evidence.  Its
# Batch-16 receipt and the future Tasks 17--19 receipts are produced by one
# direct child of the publication-resume repair, while every R3 control
# artifact binds the complete seven-commit chain below.
R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT = (
    "4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff"
)
R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT = (
    "ce3326a23f149dd864c5aa534225b959d7b5abbe"
)
R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = frozenset(
    {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
        "r8u_scheduler_log_repair_commit",
        "r8u_publication_resume_repair_commit",
        "r8u_candidate_authority_repair_commit",
    }
)
R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT = (
    "a6e80b606a76dde2512a8eedf4eb8fa4f87211ef"
)
R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256 = (
    "cfb0b19044db53742c1fd6121f8d33b567f6a62c2661050cf4df2cc4e6f2cbf2"
)
R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = (
    R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    | frozenset({"r8u_portability_repair_commit"})
)
R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT = (
    "6eb5c9a4337ca4569ecd0d3157084fb4b76adfac"
)
R8U_R5_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = (
    R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    | frozenset({"r8u_worker_context_repair_commit"})
)
R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT = (
    "7b7c3657e110b53a6e6112567410243377437db4"
)
R8U_R6_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = (
    R8U_R5_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    | frozenset({"r8u_locality_ordering_repair_commit"})
)
R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT = (
    "17b147397ff4d1d445e648f04789ac3df0dc32b0"
)
R8U_R7_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS = (
    R8U_R6_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    | frozenset({"r8u_npz_metadata_repair_commit"})
)
# R7D is a new tail-only execution epoch.  Batch 16 remains the immutable R7
# result, R7C remains historical terminal-accounting evidence, and only
# Batches 17--19 may carry the current R7D implementation epoch.
R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT = (
    "1be99c6436293a7cad576e9855ba4cd58a71e156"
)
R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT = (
    "8fa3f93cabfb4065e012dca806051b97bbb2ca38"
)
R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT = (
    "85b5e847691335105f237479c4bf1b4889385e8d"
)
R8U_R7D_BATCH16_FINAL_RECEIPT_SHA256 = (
    "63b002947814e92c616d0eb7f74ca334cba4e77cdc17f7ce2b55cfc51e090439"
)
R8U_R7D_CONSUMED_CONTINUATION_RECEIPT_SHA256 = (
    "9ccc876aa4de905ba6a6a69129a21fc1cdb5f6a2d83cc91f7f49fefd8c33a000"
)
R8U_R7D_CONSUMED_TASK17_ACCOUNTING_RECEIPT_SHA256 = (
    "2f5238300da9119f3de28deabbda999e822a0303708cdce450a235318c66143b"
)
R8U_R7D_CONSUMED_TASK18_ACCOUNTING_RECEIPT_SHA256 = (
    "92603c68e2dffc43c54914e3586bf2c6edc97161b2bc0069f13d4ba4c9642013"
)
R8U_R7D_CONSUMED_TASK19_ACCOUNTING_RECEIPT_SHA256 = (
    "30173264e794ecde2539c95fb3d8fbfd0e7571b41dd84442ea5f285d2b7d1832"
)
R8U_R7D_CONSUMED_FINALIZER_ACCOUNTING_RECEIPT_SHA256 = (
    "5adc57d7282cdf73fc04fef58e50430d4be2c9dbad6e660c361ee6c3d41a6003"
)
R8U_FAILED_PARTIAL_METADATA_SHA256 = (
    "1dcc53e52a468773128348225943125c926bcab942ac7c69c37344684249f83e"
)
R8U_BATCH16_RAW_FILES = 18_677
R8U_BATCH16_RAW_BYTES = 66_687_050_120
R8U_BATCH16_DOWNLOAD_LEDGER_SHA256 = (
    "675fa5d0f41b1886ed8278244d47a0cafd82c253906790bcc4e373df27863cc5"
)
R8U_BATCH16_VERIFIED_MANIFEST_SHA256 = (
    "662511208b4658b6c656bebcf7a0b8ee7b4726bf7f5a69277a7f692cb67cf5b7"
)
R8U_BATCH16_SELECTED_MANIFEST_SHA256 = (
    "fe968a6cb2fc8ee0425705267b5f735b4eb8c7d8a1a34c2f99600c9afc2bc90a"
)
R8U_FAILED_R1_RECOVERY_JOB_ID = "7352656"
R8U_FAILED_R1_RECOVERY_JOB_NAME = "lvef_c3_r8u_rec_f3df5cd9"
R8U_FAILED_R1_RECOVERY_LOG_SHA256 = (
    "ce34ae86faad07306c8ffd0850ffdf174c748be982e1406460a10f782e37e805"
)
R8U_FAILED_R1_NAMESPACE_INVENTORY_SHA256 = (
    "c2b87c9d4d0d347a60afd84ce3916c06fb057b350eca61fbf420cfaf45307381"
)
R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES = {
    "recovery_authority_sha256": (
        "9cd0c609ecfd587fc529f9465031973d6d0148e138656816cdc5b307dcbb0b64"
    ),
    "recovery_terminal_receipt_sha256": (
        "0072ef8c12a7a4b1c1ae204d0b7d269fe3d2f9970697cf2832fbcd045f30713a"
    ),
    "continuation_capacity_receipt_sha256": (
        "a5fddc7764a004b4d79c33aff969e240acbc84ca5aab7978d14c95bc32c53115"
    ),
    "continuation_claim_sha256": (
        "418ad72fb488b12d6a5a7bbc6c92bc77cb15cd1484bebd6f59677663d7937305"
    ),
    "continuation_submission_receipt_sha256": (
        "3d093415e50fd98b553dc4fd935f8f3be81080d659afd39cabcebe3e400177ab"
    ),
}
R8U_PREFIX_RECEIPT_AUTHORITIES = {
    "c3_batch_000": R8R_HISTORICAL_PREFIX_RECEIPT_AUTHORITIES["c3_batch_000"],
    "c3_batch_001": R8R_HISTORICAL_PREFIX_RECEIPT_AUTHORITIES["c3_batch_001"],
    "c3_batch_002": (
        4_761,
        "23ef029943578f56ca41212ea8d98c05eddaeb2356c288f370c27b0759f06b22",
    ),
    "c3_batch_003": (
        4_756,
        "7acbb2325b4bafa4e57dca284161ade600dd319da4ecbea6fd04ebb73493bb1d",
    ),
    "c3_batch_004": (
        4_728,
        "0b14603822c4d869c605000f766f6b78aa73c19358721403c0f66dc3ed4ee7c6",
    ),
    "c3_batch_005": (
        4_728,
        "447d9e55bc1684135736bd0cfdf63ccee29e50f5a6d25c4ac53144e8caf22f31",
    ),
    "c3_batch_006": (
        4_723,
        "43c531839aef2c7c84eca87dfd88225435f9b9df6930010c336948b3c97515c2",
    ),
    "c3_batch_007": (
        4_723,
        "19e4ec6bdf60932987879b20b00ad0744a2e153e24cd508d85b2dfc7a76da8e6",
    ),
    "c3_batch_008": (
        4_761,
        "3d121bf49f64905fd00bbe93f25f7c8833a6a621f5c44d70079e9b625cf14bb5",
    ),
    "c3_batch_009": (
        4_761,
        "5f3cbb6aca0790393d5cbb9f12f97a760f3e91ab21971f8952ad852aaaa28e08",
    ),
    "c3_batch_010": (
        4_728,
        "5abfb7b45e7e79659fe47c288fe27f96ab1723c2c0d580536a71483c4d9b77cf",
    ),
    "c3_batch_011": (
        4_728,
        "fff43167e0cb0f7feb92cacbfc17ae84aa3a26728d5d143cce3ccecb161cdcfb",
    ),
    "c3_batch_012": (
        4_728,
        "ad6e88dc36d991a4a873bf776f48dacfe0a3eba7c5611a4b354c9e910fbb2297",
    ),
    "c3_batch_013": (
        4_728,
        "d0a7e48072f075539775d826be1e5fc1c28cdb55e2f67169303b78933eb850e0",
    ),
    "c3_batch_014": (
        4_728,
        "9b140c5a053474152c3c1276c532048b4e824476369bb000a7d349e9098717af",
    ),
}
R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256 = tuple(
    R8U_PREFIX_RECEIPT_AUTHORITIES[f"c3_batch_{index:03d}"][1]
    for index in range(15)
) + (R8U_R7D_BATCH16_FINAL_RECEIPT_SHA256,)
R8U_FE3_GIT_TREE_SHA256 = {
    "controller_sha256": (
        "dda78b939391c778f276e9640a90a811987bf569ada9cf68e7d8c7ff37de2ae2"
    ),
    "finalizer_sha256": (
        "116c9077d054cd90b1a625fb31396228d07456172777b4c464a28d1fd9efcfd7"
    ),
    "full_sequential_sha256": (
        "e157f0f802d9f415f11ce18d338befc73cb986338ebd1a73fef6d712717a3605"
    ),
    "preservation_sha256": (
        "c9360f7dbfb792e33e1c783d8a80ebd45083ad361262d1197deb12a665df16fe"
    ),
    "production_stages_sha256": (
        "caf71e1ebdd5def78105a23363368ef64ee2775027b1c2a2bf05d78292ebe0ac"
    ),
    "retirement_sha256": (
        "15251dd3ba2726875e2df55f76c19d4d07dee03cc5111dc4e48a9f95823871df"
    ),
    "runner_sha256": (
        "f6732d6d42b6994c05301b29fe51b371de2f9ea47a46786d6cb9e956705e8017"
    ),
}
R8U_FE3_GIT_TREE_PATHS = {
    "controller_sha256": "scripts/lvef_c3_r8r_recovery_continuation.py",
    "finalizer_sha256": "scripts/finalize_lvef_c3_production.py",
    "full_sequential_sha256": "scripts/lvef_c3_full_sequential.py",
    "preservation_sha256": "scripts/preserve_lvef_c3_production_batch.py",
    "production_stages_sha256": "scripts/lvef_c3_production_stages.py",
    "retirement_sha256": "scripts/retire_lvef_c3_extracted_cache_v2.py",
    "runner_sha256": "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh",
}
R8U_FE3_IMPLEMENTATION_EPOCH = (
    R8U_FE3_GIT_TREE_SHA256["production_stages_sha256"],
    R8U_FE3_GIT_TREE_SHA256["preservation_sha256"],
    R8U_FE3_GIT_TREE_SHA256["runner_sha256"],
    "882affb1ffd5d764d82d0ddd8449df46e2fd067e4152b79276838154ea7a1326",
    R8U_FE3_GIT_TREE_SHA256["retirement_sha256"],
)
R8U_R7_BATCH16_IMPLEMENTATION_EPOCH = (
    "caf71e1ebdd5def78105a23363368ef64ee2775027b1c2a2bf05d78292ebe0ac",
    "7cdbea83f56037bfcdc5cdbe82da6f937fa675724e713c732d291d38f7f37878",
    "6878b3ca63d190d3aeaf98e851671a477fefc163fa24fefa004a5c38e69bcd9f",
    "55e4137de8997b6cbf0718c1a82b1072de5997e561bd6af12543fa7b63569843",
    "a299ae7d4d15586ee1639d7927a8bcc63361eec7f85f86bda9181c4cbe0f2a24",
)
R8U_CHAIN_ARTIFACT_SPECS = (
    (
        "failed_partial_seal_sha256",
        "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json",
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
    ),
    (
        "recovery_capacity_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_capacity.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1",
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
    ),
    (
        "recovery_authority_sha256",
        "r8u_r2_batch16_recovery/recovery_authority.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_RECOVERY",
    ),
    (
        "recovery_submission_receipt_sha256",
        "r8u_r2_batch16_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
    ),
    (
        "recovery_accounting_sha256",
        "r8u_r2_batch16_recovery/recovery_accounting.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_accounting_v1",
        "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
    ),
    (
        "recovery_terminal_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r2_batch16_recovery_terminal_v1",
        "PASS_BATCH16_RECOVERY_FINALIZED",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r2_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r2_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r2_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
    ),
)
R8U_COMMON_CHAIN_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "implementation_authority_epochs",
        "attempt_id",
        "batch_plan_sha256",
    }
)
R8U_FAILED_PARTIAL_OBSERVATION_KEYS = frozenset(
    {
        "file_count",
        "directory_count",
        "total_bytes",
        "symlink_count",
        "nonregular_count",
        "metadata_projection_sha256",
        "npz_body_reads",
    }
)
R8U_FAILED_PARTIAL_SEAL_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "batch_id",
        "original_task_id",
        "failed_array_job_id",
        "failure_class",
        "observation",
        "partial_outputs_adopted",
        "partial_outputs_modified",
        "partial_outputs_deleted",
        "partial_outputs_renamed",
        "npz_body_reads",
    }
)
R8U_RETAINED_RAW_AUTHORITY_KEYS = frozenset(
    {
        "raw_dicom_files",
        "raw_dicom_bytes",
        "raw_metadata_projection_sha256",
        "download_control_projection_sha256",
        "download_ledger_sha256",
        "verified_download_manifest_sha256",
        "selected_batch_manifest_sha256",
        "historical_st_dev_required",
        "raw_dicom_body_reads",
        "cloud_requests",
    }
)
R8U_RECOVERY_AUTHORITY_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "prior_implementation_commit",
        "batch_id",
        "original_task_id",
        "continuation_task_range",
        "prefix_final_receipt_sha256",
        "historical_r8r_chain_authority",
        "failed_r8u_recovery_epoch_authority",
        "failed_r8u_recovery_epoch_authority_sha256",
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "retained_raw_authority",
        "runtime_authority_sha256",
        "qsub_environment_sha256",
        "script_authority",
        "runtime_validation_context",
        "fresh_extraction_relative_root",
        "cloud_requests_authorized",
        "download_reruns_authorized",
        "dicom_extraction_reruns_authorized",
        "echoprime_reruns_authorized",
        "gpu_executions_authorized",
        "failed_partial_adoption_authorized",
        "failed_partial_mutation_authorized",
        "raw_dicom_deletion_authorized",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_qsub_submissions",
    }
)
R8U_FAILED_R1_SCHEDULER_EVIDENCE_KEYS = frozenset(
    {
        "artifact_type",
        "status",
        "evidence_class",
        "role",
        "basename",
        "job_id",
        "task_id",
        "mode",
        "size_bytes",
        "sha256",
        "owner_uid",
        "terminal_state",
    }
)
R8U_FAILED_R1_EPOCH_AUTHORITY_KEYS = frozenset(
    {
        "artifact_type",
        "status",
        "implementation_commit",
        "job_id",
        "job_name",
        "qsub_exit",
        "qacct_failed",
        "qacct_exit_status",
        "qacct_task_id",
        "terminal_code",
        "scheduler_evidence",
        "namespace_file_count",
        "namespace_inventory_sha256",
        "dicom_body_reads",
        "cloud_requests",
        "download_reruns",
        "extraction_reruns",
        "echoprime_reruns",
        "embedding_generations",
    }
)
R8U_RECOVERY_SUBMISSION_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "batch_id",
        "original_task_id",
        "recovery_job_name",
        "recovery_job_id",
        "recovery_qsub_argv_sha256",
        "qsub_environment_sha256",
        "recovery_authority_sha256",
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "recovery_qsub_evidence",
        "scheduler_submission_count",
        "recovery_is_array",
        "gpu_requested",
        "automatic_retry_authorized",
        "cloud_requests",
        "download_reruns",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8U_RECOVERY_ACCOUNTING_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "batch_id",
        "original_task_id",
        "recovery_job_id",
        "failed",
        "exit_status",
        "accounting_projection",
    }
)
R8U_RECOVERY_TERMINAL_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "batch_id",
        "original_task_id",
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "fresh_extraction_publication_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "batch_finalization_receipt_sha256",
        "n_selected_studies",
        "n_expected_objects",
        "expected_source_bytes",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_clip_embeddings",
        "n_pooled_studies",
        "n_no_cine_studies",
        "n_new_no_cine_studies",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        "raw_dicoms_retained",
        "fresh_recovery_cache_retired",
        "failed_partial_cache_retained",
        "batch16_raw_reused",
        "failed_partial_files",
        "failed_partial_bytes",
        "cloud_requests",
        "download_reruns",
        "dicom_extraction_reruns",
        "echoprime_reruns",
        "embedding_generations",
        "gpu_executions",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8U_CONTINUATION_CLAIM_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "prior_implementation_commit",
        "prefix_final_receipt_sha256",
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "recovery_accounting_sha256",
        "recovery_terminal_receipt_sha256",
        "runtime_authority_sha256",
        "qsub_environment_sha256",
        "script_authority",
        "continuation_task_range",
        "continuation_task_count",
        "continuation_max_concurrency",
        "held_finalizer_count",
        "total_new_qsub_maximum",
        "automatic_retry_authorized",
        "whole_stage_retry_authorized",
        "fourth_submission_reachable",
        "cloud_requests_by_submitter",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "gpu_executions_by_submitter",
        "embedding_generations_by_submitter",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
    }
)
R8U_CONTINUATION_SUBMISSION_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "recovery_job_id",
        "array_job_name",
        "finalizer_job_name",
        "array_job_id",
        "finalizer_job_id",
        "array_qsub_argv_sha256",
        "finalizer_qsub_argv_sha256",
        "qsub_environment_sha256",
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "recovery_authority_sha256",
        "recovery_accounting_sha256",
        "recovery_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "array_qsub_evidence",
        "finalizer_qsub_evidence",
        "scheduler_submission_count",
        "total_new_qsub_submissions",
        "scheduler_submission_maximum",
        "array_task_range",
        "array_task_count",
        "array_max_concurrency",
        "finalizer_held_on_array",
        "whole_stage_retry_authorized",
        "fourth_submission_reachable",
        "cloud_requests",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "gpu_executions_by_submitter",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8U_RAW_CONTENT_AUTHORITY_KEYS = R8U_RETAINED_RAW_AUTHORITY_KEYS | {
    "status"
}
R8U_FRESH_PUBLICATION_KEYS = R8U_COMMON_CHAIN_KEYS | frozenset(
    {
        "batch_id",
        "failed_partial_seal_sha256",
        "raw_content_authority",
        "stage_completion_receipt_sha256",
        "fresh_stage_promoted_to_canonical",
        "canonical_target_absent_before_promotion",
        "failed_partial_modified",
        "partial_npz_adopted",
        "cloud_requests",
        "download_reruns",
        "dicom_extraction_reruns",
    }
)
R8U_CHAIN_ARTIFACT_KEYS = {
    "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_KEYS,
    "recovery_capacity_receipt_sha256": r8r_capacity.R8U_CAPACITY_KEYS,
    "recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_KEYS,
    "recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_KEYS,
    "recovery_accounting_sha256": R8U_RECOVERY_ACCOUNTING_KEYS,
    "recovery_terminal_receipt_sha256": R8U_RECOVERY_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_CONTINUATION_SUBMISSION_KEYS
    ),
}

# R8U-R3 deliberately carries the consumed R2 submission as immutable
# historical evidence, then binds the completed extraction candidate through
# one publication-resume job and the fixed Tasks-17--19 successor.  Keeping
# these schemas local to the finalizer prevents a permissive import-time
# dependency on controller implementation details.
R8U_R2_COMPLETED_EXTRACTION_JOB_ID = "7354951"
R8U_R2_COMPLETED_EXTRACTION_LOG_SHA256 = (
    "f4f634690a16c92681b248fffabdee8393624c591791aee0ce6f173071271b65"
)
R8U_R2_SCHEDULER_LOG_REPAIR_SCRIPT_AUTHORITY = {
    "controller_sha256": (
        "3999512325b6605ad99b388446db735955610d604479dcfdbbea2009caeee4e3"
    ),
    "finalizer_sha256": (
        "6656f128605f16319bc98b54ea402a75552c9b14ff6f5f5d273f3a50ab7ebe54"
    ),
    "full_sequential_sha256": (
        "18f37041718d4cedb3a27c9cef30b988e00c56653a372d31f0a2a3b45b395621"
    ),
    "preservation_sha256": (
        "c9360f7dbfb792e33e1c783d8a80ebd45083ad361262d1197deb12a665df16fe"
    ),
    "production_stages_sha256": (
        "caf71e1ebdd5def78105a23363368ef64ee2775027b1c2a2bf05d78292ebe0ac"
    ),
    "retirement_sha256": (
        "15251dd3ba2726875e2df55f76c19d4d07dee03cc5111dc4e48a9f95823871df"
    ),
    "runner_sha256": (
        "a71d97e19ba9d5bb82fd1d793dbc3f5db186fdaf796f0066b29af16e13d6b747"
    ),
}
R8U_R3_COMMON_CHAIN_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "implementation_authority_epochs",
        "attempt_id",
        "batch_plan_sha256",
        "batch_id",
    }
)
R8U_R3_CANDIDATE_SEAL_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "r2_recovery_job_id",
        "r2_recovery_capacity_receipt_sha256",
        "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256",
        "failed_partial_seal_sha256",
        "stage_completion_receipt_sha256",
        "extraction_manifest_sha256",
        "dicom_audit_sha256",
        "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
        "candidate_regular_files",
        "candidate_directories",
        "candidate_total_bytes",
        "candidate_npz_files",
        "candidate_npz_bytes",
        "candidate_relative_file_projection_sha256",
        "candidate_relative_directory_projection_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_root_identity_sha256",
        "source_parent_identity_sha256",
        "source_mount_identity_sha256",
        "target_parent_identity_sha256",
        "target_mount_identity_sha256",
        "source_target_same_mounted_filesystem",
        "target_absent",
        "symlink_count",
        "nonregular_count",
        "n_selected_studies",
        "n_source_objects",
        "source_bytes",
        "n_readable",
        "n_unreadable",
        "n_multiframe_candidates",
        "n_single_frame",
        "n_pixel_decode_failures",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "object_substitution_count",
        "extraction_status",
        "npz_body_reads",
        "dicom_body_reads",
        "dicom_extraction_executions",
        "cloud_requests",
        "downloads",
    }
)
R8U_R3_PROBE_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "primary_primitive",
        "primary_result",
        "primary_errno_number",
        "primary_errno",
        "primary_returned_success",
        "real_source_parent_identity_sha256",
        "real_target_parent_identity_sha256",
        "real_source_mount_identity_sha256",
        "real_target_mount_identity_sha256",
        "real_parents_same_mounted_filesystem",
        "probe_mount_identity_sha256",
        "probe_mount_matches_real_parents",
        "probe_source_present_after",
        "probe_target_present_after",
        "probe_target_exact_after",
        "probe_cleanup_passed",
        "probe_directories_created",
        "probe_directories_removed",
        "scientific_file_body_reads",
        "npz_body_reads",
        "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R3_PROCEEDABLE_PROBE_RESULTS = frozenset(
    {
        "RENAME_NOREPLACE_SUPPORTED",
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }
)
R8U_R3_PUBLICATION_CLAIM_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "resume_job_id",
        "r2_recovery_job_id",
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256",
        "resume_authority_sha256",
        "resume_submission_receipt_sha256",
        "failed_partial_seal_sha256",
        "candidate_relative_file_projection_sha256",
        "target_role",
        "publication_primitive_selected",
        "primary_result",
        "target_absent",
        "source_target_same_mounted_filesystem",
        "source_parent_identity_sha256",
        "target_parent_identity_sha256",
        "source_mount_identity_sha256",
        "target_mount_identity_sha256",
        "pre_qsub_process_projection_sha256",
        "initial_qstat_projection_sha256",
        "worker_process_projection",
        "competing_active_jobs",
        "competing_active_processes",
        "cloud_requests",
        "downloads",
        "dicom_body_reads",
        "dicom_extraction_executions",
        "npz_body_reads",
    }
)
R8U_R3_PUBLICATION_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256",
        "publication_claim_sha256",
        "primitive_attempted",
        "primary_result",
        "primary_errno",
        "fallback_used",
        "fallback_primitive",
        "rename_returned_success",
        "real_rename_returned_success",
        "real_rename_errno_number",
        "real_rename_errno",
        "real_rename_errno_classification",
        "publication_ruling",
        "prepublication_candidate_sha256",
        "postpublication_target_sha256",
        "source_absent",
        "target_exact",
        "candidate_npz_files",
        "candidate_total_bytes",
        "files_moved",
        "files_copied",
        "files_deleted_independently",
        "dicom_body_reads",
        "dicom_extraction_executions",
        "npz_body_reads",
        "cloud_requests",
        "downloads",
    }
)
R8U_R3_AUTHORITY_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "prior_implementation_commit",
        "original_task_id",
        "continuation_task_range",
        "prefix_final_receipt_sha256",
        "historical_r8r_chain_authority",
        "failed_r8u_recovery_epoch_authority_sha256",
        "r2_recovery_job_id",
        "r2_recovery_log_sha256",
        "r2_recovery_capacity_receipt_sha256",
        "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256",
        "failed_partial_seal_sha256",
        "extraction_candidate_seal_sha256",
        "resume_capacity_sha256",
        "runtime_authority_sha256",
        "qsub_environment_sha256",
        "script_authority",
        "runtime_validation_context",
        "target_role",
        "pre_qsub_process_projection",
        "cloud_requests_authorized",
        "downloads_authorized",
        "dicom_body_reads_authorized",
        "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized",
        "gpu_executions_authorized",
        "failed_partial_adoption_authorized",
        "failed_partial_mutation_authorized",
        "raw_dicom_deletion_authorized",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_qsub_submissions",
    }
)
R8U_R3_SUBMISSION_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id",
        "resume_job_name",
        "resume_job_id",
        "resume_qsub_argv_sha256",
        "qsub_environment_sha256",
        "resume_authority_sha256",
        "extraction_candidate_seal_sha256",
        "resume_capacity_sha256",
        "resume_qsub_evidence",
        "pre_qsub_process_projection",
        "initial_qstat_projection",
        "scheduler_submission_count",
        "resume_is_array",
        "gpu_requested",
        "automatic_retry_authorized",
        "cloud_requests",
        "downloads",
        "dicom_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter",
        "npz_body_reads_by_submitter",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8U_R3_PROCESS_PROJECTION_KEYS = frozenset(
    {
        "status",
        "matching_processes",
        "process_snapshot_count",
        "ps_argv_sha256",
        "ps_stdout_sha256",
    }
)
R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS = frozenset(
    {
        "status",
        "resume_job_id",
        "resume_job_name",
        "state",
        "category",
        "target_matches",
        "competing_matching_jobs",
        "qstat_snapshot_count",
        "qstat_projection_sha256",
    }
)
R8U_R3_ACCOUNTING_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id",
        "resume_job_id",
        "failed",
        "exit_status",
        "accounting_projection",
    }
)
R8U_R3_TERMINAL_KEYS = R8U_R3_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id",
        "failed_partial_seal_sha256",
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256",
        "publication_claim_sha256",
        "publication_receipt_sha256",
        "resume_capacity_sha256",
        "resume_authority_sha256",
        "resume_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "batch_finalization_receipt_sha256",
        "n_selected_studies",
        "n_expected_objects",
        "expected_source_bytes",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_clip_embeddings",
        "n_pooled_studies",
        "n_no_cine_studies",
        "n_new_no_cine_studies",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        "raw_dicoms_retained",
        "canonical_extraction_cache_retired",
        "failed_partial_cache_retained",
        "source_candidate_npz_files",
        "cloud_requests",
        "downloads",
        "dicom_body_reads",
        "dicom_extraction_executions",
        "echoprime_executions",
        "embedding_generations",
        "gpu_executions",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8U_R3_CONTINUATION_LINK_KEYS = frozenset(
    {
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256",
        "publication_claim_sha256",
        "publication_receipt_sha256",
        "resume_capacity_sha256",
        "resume_authority_sha256",
        "resume_submission_receipt_sha256",
        "resume_accounting_sha256",
        "resume_terminal_receipt_sha256",
    }
)
R8U_R3_CONTINUATION_CLAIM_KEYS = (
    R8U_R3_COMMON_CHAIN_KEYS
    | R8U_R3_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prior_implementation_commit",
            "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256",
            "runtime_authority_sha256",
            "qsub_environment_sha256",
            "script_authority",
            "continuation_task_range",
            "continuation_task_count",
            "continuation_max_concurrency",
            "held_finalizer_count",
            "total_new_qsub_maximum",
            "automatic_retry_authorized",
            "whole_stage_retry_authorized",
            "fourth_submission_reachable",
            "cloud_requests_by_submitter",
            "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter",
            "embedding_generations_by_submitter",
            "model_fitting_authorized",
            "prediction_authorized",
            "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R3_CONTINUATION_SUBMISSION_KEYS = (
    R8U_R3_COMMON_CHAIN_KEYS
    | R8U_R3_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "resume_job_id",
            "array_job_name",
            "finalizer_job_name",
            "array_job_id",
            "finalizer_job_id",
            "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256",
            "qsub_environment_sha256",
            "failed_partial_seal_sha256",
            "continuation_claim_sha256",
            "array_qsub_evidence",
            "finalizer_qsub_evidence",
            "scheduler_submission_count",
            "total_new_qsub_submissions",
            "scheduler_submission_maximum",
            "array_task_range",
            "array_task_count",
            "array_max_concurrency",
            "finalizer_held_on_array",
            "whole_stage_retry_authorized",
            "fourth_submission_reachable",
            "cloud_requests",
            "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter",
            "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        }
    )
)

# R8U-R4 mirrors the controller schemas locally so the cohort finalizer never
# acquires a permissive import-time dependency on a live recovery controller.
R8U_R4_COMMON_CHAIN_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "original_scientific_commit", "implementation_commit",
        "implementation_authority_epochs", "attempt_id",
        "batch_plan_sha256", "batch_id",
    }
)
R8U_R4_PORTABLE_METADATA_FIELDS = frozenset(
    {
        "candidate_regular_files", "candidate_directories",
        "candidate_total_bytes", "candidate_npz_files", "candidate_npz_bytes",
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_relative_file_portable_projection_sha256",
        "candidate_relative_directory_path_set_sha256",
        "candidate_relative_directory_portable_projection_sha256",
        "candidate_root_portable_identity_sha256", "symlink_count",
        "nonregular_count",
    }
)
R8U_R4_CONTROL_HASH_FIELDS = frozenset(
    {
        "stage_completion_receipt_sha256", "extraction_manifest_sha256",
        "dicom_audit_sha256", "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
    }
)
R8U_R4_SEMANTIC_FIELDS = frozenset(
    {
        "canonical_stage_event_authority_sha256", "input_manifest_sha256",
        "candidate_npz_manifest_projection_sha256", "n_selected_studies",
        "n_source_objects", "source_bytes", "n_readable", "n_unreadable",
        "n_multiframe_candidates", "n_single_frame",
        "n_pixel_decode_failures", "n_successfully_extracted_cines",
        "n_object_technical_dispositions", "n_blocking_failures",
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
        "object_substitution_count", "extraction_status",
    }
)
R8U_R4_PORTABLE_CONTENT_FIELDS = (
    R8U_R4_PORTABLE_METADATA_FIELDS
    | R8U_R4_CONTROL_HASH_FIELDS
    | R8U_R4_SEMANTIC_FIELDS
)
R8U_R4_DIAGNOSIS_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "job_id",
        "candidate_seal_sha256", "first_differing_field",
        "differing_fields", "portable_differing_fields",
        "node_local_differing_fields", "portable_candidate_fields_equal",
        "node_local_only_differences", "candidate_path_set_equal",
        "candidate_count_and_bytes_equal", "zero_body_reads",
        "dicom_body_reads", "npz_body_reads", "final_classification",
    }
)
R8U_R4_PORTABLE_AUTHORITY_KEYS = (
    R8U_R4_COMMON_CHAIN_KEYS
    | R8U_R4_PORTABLE_CONTENT_FIELDS
    | frozenset(
        {
            "failed_r8u_r3_job_id", "r8u_r3_candidate_seal_sha256",
            "candidate_replay_diagnosis_sha256", "portable_projection_status",
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
        }
    )
)
R8U_R4_LOCALITY_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "publication_claim_sha256",
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call",
        "source_identity_sha256", "source_parent_identity_sha256",
        "target_parent_identity_sha256", "source_mount_identity_sha256",
        "target_mount_identity_sha256", "competing_active_jobs",
        "competing_active_processes",
    }
)
R8U_R4_PUBLICATION_CLAIM_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "resume_job_id", "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256", "resume_authority_sha256",
        "resume_submission_receipt_sha256", "worker_process_projection_sha256",
        "worker_qstat_projection_sha256", "target_role", "target_absent",
        "competing_active_jobs", "competing_active_processes",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads",
    }
)
R8U_R4_PROBE_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "live_publication_locality_sha256", "publication_claim_sha256",
        "primary_primitive", "primary_result", "primary_errno",
        "primary_errno_number", "primary_returned_success",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R4_PUBLICATION_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "fallback_used",
        "rename_returned_success", "real_rename_errno",
        "real_rename_errno_number", "real_rename_errno_classification",
        "publication_ruling", "prepublication_candidate_sha256",
        "postpublication_root_identity_sha256", "source_absent",
        "target_exact", "candidate_npz_files", "candidate_total_bytes",
        "files_moved", "files_copied", "files_deleted_independently",
        "dicom_body_reads", "dicom_extraction_executions", "npz_body_reads",
        "cloud_requests", "downloads",
    }
)
R8U_R4_AUTHORITY_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "prior_implementation_commit", "original_task_id",
        "continuation_task_range", "prefix_final_receipt_sha256",
        "historical_r8r_chain_authority", "failed_partial_seal_sha256",
        "r2_recovery_capacity_receipt_sha256", "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256", "failed_r8u_r3_job_id",
        "r8u_r3_candidate_seal_sha256", "r8u_r3_scheduler_log_sha256",
        "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256", "resume_capacity_sha256",
        "runtime_authority_sha256", "qsub_environment_sha256",
        "script_authority", "runtime_validation_context", "target_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "failed_partial_adoption_authorized", "failed_partial_mutation_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_qsub_submissions", "pre_qsub_process_projection",
    }
)
R8U_R4_SUBMISSION_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id", "resume_job_name", "resume_job_id",
        "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_authority_sha256", "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256", "resume_capacity_sha256",
        "resume_qsub_evidence", "scheduler_submission_count",
        "resume_is_array", "gpu_requested", "automatic_retry_authorized",
        "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "npz_body_reads_by_submitter",
        "model_fitting_count", "prediction_generation_count",
        "confirmatory_performance_access_count", "pre_qsub_process_projection",
        "initial_qstat_projection",
    }
)
R8U_R4_ACCOUNTING_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id", "resume_job_id", "failed", "exit_status",
        "accounting_projection",
    }
)
R8U_R4_TERMINAL_KEYS = R8U_R4_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id", "failed_partial_seal_sha256",
        "r8u_r3_candidate_seal_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "n_selected_studies",
        "n_expected_objects", "expected_source_bytes",
        "n_successfully_extracted_cines", "n_object_technical_dispositions",
        "n_blocking_failures", "n_clip_embeddings", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
        "object_substitution_count", "unaccounted_multiframe_objects",
        "raw_dicoms_retained", "canonical_extraction_cache_retired",
        "failed_partial_cache_retained", "source_candidate_npz_files",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R4_CONTINUATION_LINK_KEYS = frozenset(
    {
        "r8u_r3_candidate_seal_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R4_CONTINUATION_CLAIM_KEYS = (
    R8U_R4_COMMON_CHAIN_KEYS
    | R8U_R4_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fourth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized",
            "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R4_CONTINUATION_SUBMISSION_KEYS = (
    R8U_R4_COMMON_CHAIN_KEYS
    | R8U_R4_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fourth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        }
    )
)

# R8U-R5 keeps the R4 science and portable-candidate authority immutable while
# replacing only the compute-worker scheduler-context boundary.  These closed
# schemas intentionally mirror the controller without importing it into the
# finalizer process.
R8U_R5_COMMON_CHAIN_KEYS = R8U_R4_COMMON_CHAIN_KEYS
R8U_R5_ACCOUNT_AUTHORITY_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "expected_effective_uid", "expected_scheduler_username",
        "canonical_home", "submitter_passwd_lookup_available",
        "runner_sha256", "python_sha256", "qsub_environment_sha256",
        "sealed_qsub_environment", "authorized_worker_roles",
    }
)
R8U_R5_R4_FAILURE_EVIDENCE_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "failed_job_id", "scheduler_failed", "application_exit_status",
        "wall_seconds", "first_failed_stage", "exact_failure_code",
        "scheduler_log_basename", "scheduler_log_bytes", "scheduler_log_mode",
        "scheduler_log_sha256", "candidate_replay_diagnosis_sha256",
        "portable_candidate_authority_sha256", "r8u_r4_capacity_sha256",
        "r8u_r4_resume_authority_sha256", "r8u_r4_submission_sha256",
        "portable_candidate_pass", "publication_locality_ran",
        "publication_ran", "echoprime_ran",
    }
)
R8U_R5_PROBE_AUTHORITY_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "qsub_environment_sha256",
        "r8u_r4_failure_evidence_sha256",
        "portable_candidate_authority_sha256",
        "script_authority", "worker_role", "wall_seconds_maximum",
        "cpu_slots", "gpu_requested", "array_requested",
        "candidate_scan_authorized", "cloud_requests_authorized",
        "dicom_body_reads_authorized", "npz_body_reads_authorized",
        "publication_authorized", "extraction_authorized",
        "embedding_generation_authorized", "preservation_authorized",
        "scientific_attempt_mutation_authorized",
    }
)
R8U_R5_PROBE_SUBMISSION_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "r8u_r4_failure_evidence_sha256",
        "portable_candidate_authority_sha256",
        "probe_job_name", "probe_job_id", "probe_qsub_argv_sha256",
        "qsub_environment_sha256", "probe_qsub_evidence",
        "scheduler_submission_count", "cpu_slots", "gpu_requested",
        "probe_is_array", "automatic_retry_authorized",
    }
)
R8U_R5_WORKER_DIAGNOSTIC_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status",
        "user_present", "logname_present", "home_present", "shell_present",
        "observed_user_match", "observed_logname_match",
        "observed_home_match", "observed_shell_match",
        "passwd_lookup_available", "passwd_name_match",
        "passwd_home_match", "passwd_shell_match", "passwd_lookup_status",
        "effective_uid_match", "job_id_match", "task_context_match",
        "job_role_match",
        "runner_sha256_match", "python_sha256_match",
        "implementation_commit_match", "qsub_environment_sha256_match",
        "classifications", "canonical_worker_environment_status",
    }
)
R8U_R5_PROBE_RECEIPT_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256", "probe_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "probe_job_id", "worker_role", "effective_uid_match",
        "job_id_match", "task_context_match", "job_role_match",
        "canonical_worker_environment_pass",
        "candidate_scans", "cloud_requests", "dicom_body_reads",
        "npz_body_reads", "publication_executions", "extraction_executions",
        "embedding_generations", "preservation_executions",
        "scientific_attempt_mutations",
    }
)
R8U_R5_PROBE_ACCOUNTING_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256",
        "probe_job_id", "failed", "exit_status", "accounting_projection",
        "probe_receipt_sha256", "probe_scheduler_log_sha256",
    }
)
R8U_R5_CAPACITY_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "worker_context_probe_accounting_sha256",
        "r8u_r4_capacity_authority_sha256", "fresh_capacity_observation",
        "fresh_capacity_observation_sha256", "candidate_seal_sha256",
        "candidate_total_bytes", "quota_reserve_bytes",
        "physical_reserve_bytes", "file_slot_reserve_pass",
    }
)
R8U_R5_AUTHORITY_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256",
        "worker_context_probe_authority_sha256",
        "portable_candidate_authority_sha256", "resume_capacity_sha256",
        "prefix_final_receipt_sha256", "runtime_authority_sha256",
        "qsub_environment_sha256", "script_authority", "worker_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_gpu_resume_qsubs",
    }
)
R8U_R5_SUBMISSION_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_authority_sha256",
        "worker_context_probe_receipt_sha256", "resume_capacity_sha256",
        "worker_context_probe_authority_sha256",
        "worker_context_probe_accounting_sha256",
        "portable_candidate_authority_sha256", "resume_job_name",
        "resume_job_id", "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_qsub_evidence", "initial_qstat_projection",
        "scheduler_submission_count", "resume_is_array", "gpu_requested",
        "automatic_retry_authorized", "cloud_requests", "downloads",
        "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R5_LOCALITY_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "schema_version", "artifact_type", "status",
        "scheduler_account_authority_sha256", "worker_context_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call", "source_identity_sha256",
        "source_parent_identity_sha256", "target_parent_identity_sha256",
        "source_mount_identity_sha256", "target_mount_identity_sha256",
        "competing_active_jobs", "competing_active_processes",
    }
)
R8U_R5_PUBLICATION_CLAIM_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_job_id",
        "portable_candidate_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "live_publication_locality_sha256", "worker_context_diagnostic_sha256",
        "worker_process_projection_sha256", "worker_qstat_projection_sha256",
        "target_role", "target_absent", "competing_active_jobs",
        "competing_active_processes", "cloud_requests", "downloads",
        "dicom_body_reads", "dicom_extraction_executions", "npz_body_reads",
    }
)
R8U_R5_PROBE_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "live_publication_locality_sha256",
        "publication_claim_sha256", "primary_primitive", "primary_result",
        "primary_errno", "primary_errno_number", "primary_returned_success",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "probe_directories_created", "probe_directories_removed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R5_PUBLICATION_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "portable_candidate_authority_sha256",
        "r8u_r4_failure_evidence_sha256", "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "fallback_used",
        "rename_returned_success", "real_rename_errno",
        "real_rename_errno_number", "real_rename_errno_classification",
        "publication_ruling", "prepublication_candidate_sha256",
        "postpublication_root_identity_sha256", "source_absent", "target_exact",
        "candidate_npz_files", "candidate_total_bytes", "files_moved",
        "files_copied", "files_deleted_independently", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads", "cloud_requests",
        "downloads",
    }
)
R8U_R5_ACCOUNTING_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "original_task_id", "resume_job_id", "failed", "exit_status",
        "accounting_projection",
    }
)
R8U_R5_TERMINAL_KEYS = R8U_R5_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256", "live_publication_locality_sha256",
        "worker_context_probe_accounting_sha256",
        "worker_context_diagnostic_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "preservation_receipt_sha256", "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "n_selected_studies",
        "n_expected_objects", "expected_source_bytes",
        "n_successfully_extracted_cines", "n_object_technical_dispositions",
        "n_blocking_failures", "n_clip_embeddings", "n_pooled_studies",
        "n_no_cine_studies", "n_new_no_cine_studies",
        "object_substitution_count", "unaccounted_multiframe_objects",
        "raw_dicoms_retained", "canonical_extraction_cache_retired",
        "failed_partial_cache_retained", "source_candidate_npz_files",
        "cloud_requests", "downloads", "dicom_body_reads",
        "dicom_extraction_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R5_CONTINUATION_LINK_KEYS = frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256",
        "portable_candidate_authority_sha256",
        "live_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R5_CONTINUATION_CLAIM_KEYS = (
    R8U_R5_COMMON_CHAIN_KEYS
    | R8U_R5_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fifth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized", "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R5_CONTINUATION_SUBMISSION_KEYS = (
    R8U_R5_COMMON_CHAIN_KEYS
    | R8U_R5_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fifth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        }
    )
)
R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS = (
    R8U_R5_COMMON_CHAIN_KEYS
    | frozenset(
        {
            "scheduler_account_authority_sha256",
            "continuation_claim_sha256", "continuation_submission_sha256",
            "worker_diagnostic_sha256", "worker_qstat_projection_sha256",
            "worker_process_projection_sha256", "job_id", "worker_role",
            "task_id", "effective_uid_match", "job_id_match",
            "task_context_match", "job_role_match",
            "canonical_worker_environment_pass",
        }
    )
)

# R8U-R6 preserves the R5 scheduler-account repair while moving the locality
# authority used by the real publication until after the primitive probe.  As
# with R5, these schemas are duplicated deliberately: the cohort finalizer is
# an independent consumer of the closed control chain, not an importer of the
# recovery controller.
R8U_R6_STABLE_IDENTITY_FIELDS = (
    "device", "inode", "type", "mode", "uid", "gid",
)
R8U_R6_VOLATILE_IDENTITY_FIELDS = (
    "nlink", "size", "mtime_ns", "ctime_ns",
)
R8U_R6_COMMON_CHAIN_KEYS = R8U_R5_COMMON_CHAIN_KEYS
R8U_R6_ACCOUNT_AUTHORITY_KEYS = R8U_R5_ACCOUNT_AUTHORITY_KEYS
R8U_R6_R5_FAILURE_EVIDENCE_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "failed_job_id", "scheduler_failed", "application_exit_status",
        "wall_seconds", "first_failed_stage", "exact_failure_code",
        "scheduler_log_basename", "scheduler_log_bytes", "scheduler_log_mode",
        "scheduler_log_sha256", "scheduler_account_authority_sha256",
        "worker_context_probe_receipt_sha256",
        "worker_context_probe_accounting_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "live_publication_locality_sha256", "publication_claim_sha256",
        "publication_primitive_probe_sha256",
        "portable_candidate_authority_sha256", "worker_context_pass",
        "portable_candidate_pass", "initial_locality_pass",
        "publication_claim_pass", "primitive_probe_pass",
        "primary_primitive_result", "final_locality_revalidation_pass",
        "publication_ran", "echoprime_ran",
    }
)
R8U_R6_PROBE_AUTHORITY_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "qsub_environment_sha256",
        "r8u_r5_failure_evidence_sha256",
        "portable_candidate_authority_sha256", "script_authority",
        "worker_role", "wall_seconds_maximum", "cpu_slots",
        "gpu_requested", "array_requested", "candidate_scan_authorized",
        "cloud_requests_authorized", "dicom_body_reads_authorized",
        "npz_body_reads_authorized", "publication_claim_authorized",
        "real_rename_authorized", "extraction_authorized",
        "embedding_generation_authorized", "preservation_authorized",
        "scientific_attempt_mutation_authorized",
    }
)
R8U_R6_PROBE_SUBMISSION_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "r8u_r5_failure_evidence_sha256",
        "portable_candidate_authority_sha256", "probe_job_name",
        "probe_job_id", "probe_qsub_argv_sha256", "qsub_environment_sha256",
        "probe_qsub_evidence", "scheduler_submission_count", "cpu_slots",
        "gpu_requested", "probe_is_array", "automatic_retry_authorized",
    }
)
R8U_R6_PROBE_DIAGNOSTIC_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256", "worker_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "probe_job_id", "worker_role", "stable_identity_fields",
        "volatile_identity_fields", "source_stable_fields_present",
        "source_parent_stable_fields_present",
        "target_parent_stable_fields_present", "source_stable_fields_equal",
        "source_parent_stable_fields_equal",
        "target_parent_stable_fields_equal", "source_volatile_fields_present",
        "source_parent_volatile_fields_present",
        "target_parent_volatile_fields_present", "source_volatile_fields_equal",
        "source_parent_volatile_fields_equal",
        "target_parent_volatile_fields_equal", "volatile_changed_fields",
        "volatile_metadata_classification", "target_absent_before_probe",
        "target_absent_after_probe", "same_mounted_filesystem_before_probe",
        "same_mounted_filesystem_after_probe", "mount_identity_equal",
        "primary_primitive", "primary_result", "primary_errno",
        "probe_cleanup_passed", "probe_directories_created",
        "probe_directories_removed", "candidate_scans", "cloud_requests",
        "dicom_body_reads", "npz_body_reads", "publication_claims",
        "real_renames", "extraction_executions", "embedding_generations",
        "preservation_executions", "scientific_attempt_mutations",
    }
)
R8U_R6_PROBE_RECEIPT_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256", "probe_diagnostic_sha256",
        "probe_job_id", "worker_role", "worker_scheduler_context_pass",
        "stable_fields_equal", "volatile_changed_fields", "target_absent",
        "same_mounted_filesystem", "primitive_probe_pass",
        "probe_cleanup_passed", "candidate_scans", "cloud_requests",
        "dicom_body_reads", "npz_body_reads", "publication_claims",
        "real_renames", "scientific_attempt_mutations",
    }
)
R8U_R6_PROBE_ACCOUNTING_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "probe_authority_sha256",
        "probe_submission_receipt_sha256", "probe_job_id", "failed",
        "exit_status", "accounting_projection", "probe_receipt_sha256",
        "probe_scheduler_log_sha256",
    }
)
R8U_R6_CAPACITY_KEYS = (
    R8U_R5_CAPACITY_KEYS
    - frozenset({"worker_context_probe_accounting_sha256"})
    | frozenset({"locality_sequence_probe_accounting_sha256"})
)
R8U_R6_AUTHORITY_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r5_failure_evidence_sha256",
        "locality_sequence_probe_receipt_sha256",
        "locality_sequence_probe_accounting_sha256",
        "portable_candidate_authority_sha256", "resume_capacity_sha256",
        "prefix_final_receipt_sha256", "runtime_authority_sha256",
        "qsub_environment_sha256", "script_authority", "worker_role",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized", "dicom_extraction_executions_authorized",
        "echoprime_executions_authorized", "gpu_executions_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_gpu_resume_qsubs",
    }
)
R8U_R6_SUBMISSION_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_authority_sha256",
        "locality_sequence_probe_receipt_sha256",
        "locality_sequence_probe_accounting_sha256", "resume_capacity_sha256",
        "portable_candidate_authority_sha256", "resume_job_name",
        "resume_job_id", "resume_qsub_argv_sha256", "qsub_environment_sha256",
        "resume_qsub_evidence", "initial_qstat_projection",
        "scheduler_submission_count", "resume_is_array", "gpu_requested",
        "automatic_retry_authorized", "cloud_requests", "downloads",
        "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R6_PUBLICATION_CLAIM_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "resume_job_id",
        "portable_candidate_authority_sha256", "r8u_r5_failure_evidence_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "worker_context_diagnostic_sha256", "worker_process_projection_sha256",
        "worker_qstat_projection_sha256", "target_role", "target_absent",
        "competing_active_jobs", "competing_active_processes", "cloud_requests",
        "downloads", "dicom_body_reads", "dicom_extraction_executions",
        "npz_body_reads",
    }
)
R8U_R6_PROBE_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "publication_claim_sha256",
        "primary_primitive", "primary_result", "primary_errno",
        "primary_errno_number", "primary_returned_success",
        "probe_source_present_after", "probe_target_present_after",
        "probe_target_exact_after", "probe_cleanup_passed",
        "probe_directories_created", "probe_directories_removed",
        "scientific_file_body_reads", "npz_body_reads", "dicom_body_reads",
        "dicom_extraction_executions",
    }
)
R8U_R6_FINAL_LOCALITY_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "worker_context_diagnostic_sha256",
        "worker_qstat_projection_sha256", "worker_process_projection_sha256",
        "publication_claim_sha256", "publication_primitive_probe_sha256",
        "stable_identity_fields", "volatile_identity_fields",
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_stable_identity_equal",
        "source_parent_stable_identity_equal",
        "target_parent_stable_identity_equal", "source_volatile_fields_equal",
        "source_parent_volatile_fields_equal",
        "target_parent_volatile_fields_equal", "volatile_changed_fields",
        "volatile_metadata_classification", "source_stable_identity_sha256",
        "source_parent_stable_identity_sha256",
        "target_parent_stable_identity_sha256",
        "source_mount_identity_sha256", "target_mount_identity_sha256",
        "competing_active_jobs", "competing_active_processes",
        "captured_after_primitive_probe", "probe_cleanup_validated",
    }
)
R8U_R6_PUBLICATION_KEYS = R8U_R6_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256", "portable_candidate_authority_sha256",
        "r8u_r5_failure_evidence_sha256", "final_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "primitive_attempted", "primary_result", "fallback_used",
        "rename_returned_success", "real_rename_errno",
        "real_rename_errno_number", "real_rename_errno_classification",
        "publication_ruling", "prepublication_candidate_sha256",
        "postpublication_root_stable_identity_sha256", "source_absent",
        "target_exact", "candidate_npz_files", "candidate_total_bytes",
        "files_moved", "files_copied", "files_deleted_independently",
        "publication_attempts", "dicom_body_reads",
        "dicom_extraction_executions", "npz_body_reads", "cloud_requests",
        "downloads",
    }
)
R8U_R6_ACCOUNTING_KEYS = R8U_R5_ACCOUNTING_KEYS
R8U_R6_TERMINAL_KEYS = (
    R8U_R5_TERMINAL_KEYS
    - frozenset(
        {
            "r8u_r4_failure_evidence_sha256",
            "worker_context_probe_receipt_sha256",
            "worker_context_probe_accounting_sha256",
            "live_publication_locality_sha256",
        }
    )
    | frozenset(
        {
            "r8u_r5_failure_evidence_sha256",
            "locality_sequence_probe_receipt_sha256",
            "locality_sequence_probe_accounting_sha256",
            "final_publication_locality_sha256",
        }
    )
)
R8U_R6_CONTINUATION_LINK_KEYS = frozenset(
    {
        "scheduler_account_authority_sha256", "r8u_r5_failure_evidence_sha256",
        "locality_sequence_probe_receipt_sha256",
        "locality_sequence_probe_accounting_sha256",
        "portable_candidate_authority_sha256",
        "final_publication_locality_sha256",
        "publication_primitive_probe_sha256", "publication_claim_sha256",
        "publication_receipt_sha256", "resume_capacity_sha256",
        "resume_authority_sha256", "resume_submission_receipt_sha256",
        "resume_accounting_sha256", "resume_terminal_receipt_sha256",
    }
)
R8U_R6_CONTINUATION_CLAIM_KEYS = (
    R8U_R6_COMMON_CHAIN_KEYS
    | R8U_R6_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prior_implementation_commit", "prefix_final_receipt_sha256",
            "failed_partial_seal_sha256", "runtime_authority_sha256",
            "qsub_environment_sha256", "script_authority",
            "continuation_task_range", "continuation_task_count",
            "continuation_max_concurrency", "held_finalizer_count",
            "total_new_qsub_maximum", "automatic_retry_authorized",
            "whole_stage_retry_authorized", "fifth_submission_reachable",
            "cloud_requests_by_submitter", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter", "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized", "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R6_CONTINUATION_SUBMISSION_KEYS = (
    R8U_R6_COMMON_CHAIN_KEYS
    | R8U_R6_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "resume_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fifth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        }
    )
)
R8U_R6_CONTINUATION_WORKER_RECEIPT_KEYS = (
    R8U_R6_COMMON_CHAIN_KEYS
    | frozenset(
        {
            "scheduler_account_authority_sha256",
            "continuation_claim_sha256", "continuation_submission_sha256",
            "worker_diagnostic_sha256", "worker_qstat_projection_sha256",
            "worker_process_projection_sha256", "job_id", "worker_role",
            "task_id", "effective_uid_match", "job_id_match",
            "task_context_match", "job_role_match",
            "canonical_worker_environment_pass",
        }
    )
)

# R8U-R7 consumes the completed R6 publication epoch and repairs only the
# body-free preservation boundary.  These closed schemas mirror the fixed
# controller without importing it into the cohort finalizer.
R8U_R7_COMMON_CHAIN_KEYS = R8U_R6_COMMON_CHAIN_KEYS
R8U_R7_ACCOUNT_AUTHORITY_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "expected_effective_uid", "expected_scheduler_username",
        "canonical_home", "submitter_passwd_lookup_available",
        "runner_sha256", "python_sha256", "qsub_environment_sha256",
        "sealed_qsub_environment", "authorized_worker_roles",
    }
)
R8U_R7_R6_FAILURE_EVIDENCE_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "failed_job_id", "scheduler_failed", "application_exit_status",
        "wall_seconds", "first_failed_stage", "exact_failure_code",
        "scheduler_log_basename", "scheduler_log_bytes",
        "scheduler_log_mode", "scheduler_log_sha256",
        "accounting_projection", "scheduler_account_authority_sha256",
        "locality_sequence_probe_receipt_sha256",
        "locality_sequence_probe_accounting_sha256",
        "resume_capacity_sha256", "resume_authority_sha256",
        "resume_submission_receipt_sha256",
        "worker_context_diagnostic_sha256", "publication_claim_sha256",
        "publication_primitive_probe_sha256",
        "final_publication_locality_sha256", "publication_receipt_sha256",
        "extraction_ledger_sha256", "pooling_ledger_sha256",
        "clip_embeddings_sha256", "clip_manifest_sha256",
        "study_embeddings_sha256", "study_manifest_sha256",
        "embedding_summary_sha256", "failed_partial_seal_sha256",
        "publication_status", "publication_ruling",
        "worker_scheduler_context_status", "echoprime_status",
        "published_npz_files", "clip_embeddings", "study_embeddings",
        "technical_dispositions", "blocking_failures",
        "dicom_extraction_reruns", "dicom_body_reads", "cloud_requests",
        "echoprime_executions", "embedding_generations",
    }
)
R8U_R7_CAPACITY_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "required_control_bytes", "available_bytes",
        "required_control_file_slots", "available_file_slots",
        "byte_envelope_passed", "file_slot_envelope_passed",
        "storage_neutral_or_reducing", "full_run_reserve_charged",
        "raw_data_charged", "extraction_charged", "publication_charged",
        "echoprime_charged", "embedding_generation_charged",
        "prefix_batches_charged", "continuation_charged",
    }
)
R8U_R7_AUTHORITY_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256",
        "prefix_final_receipt_sha256", "runtime_authority_sha256",
        "qsub_environment_sha256", "script_authority",
        "failed_partial_seal_sha256", "publication_receipt_sha256",
        "extraction_ledger_sha256", "pooling_ledger_sha256",
        "clip_embeddings_sha256", "clip_manifest_sha256",
        "study_embeddings_sha256", "study_manifest_sha256",
        "embedding_summary_sha256", "worker_role", "original_task_id",
        "human_batch_number", "published_npz_files",
        "cloud_requests_authorized", "downloads_authorized",
        "dicom_body_reads_authorized",
        "dicom_extraction_executions_authorized",
        "publication_executions_authorized", "echoprime_executions_authorized",
        "embedding_generations_authorized", "gpu_executions_authorized",
        "preservation_executions_authorized",
        "cache_retirement_executions_authorized",
        "batch_finalization_executions_authorized",
        "model_fitting_authorized", "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_recovery_qsubs",
    }
)
R8U_R7_CLAIM_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256",
        "preservation_recovery_authority_sha256",
        "qsub_environment_sha256", "qstat_projection_sha256",
        "process_projection_sha256", "competing_active_jobs",
        "competing_active_processes", "target_role",
        "preservation_receipt_absent", "retirement_authorization_absent",
        "final_receipt_absent",
    }
)
R8U_R7_SUBMISSION_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256",
        "preservation_recovery_authority_sha256",
        "preservation_recovery_claim_sha256", "recovery_job_name",
        "recovery_job_id", "recovery_qsub_argv_sha256",
        "qsub_environment_sha256", "recovery_qsub_evidence",
        "initial_qstat_projection", "scheduler_submission_count",
        "cpu_slots", "wall_seconds_maximum", "recovery_is_array",
        "gpu_requested", "automatic_retry_authorized", "cloud_requests",
        "downloads", "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter",
        "echoprime_executions_by_submitter",
        "embedding_generations_by_submitter", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R7_ACCOUNTING_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "preservation_recovery_submission_receipt_sha256",
        "preservation_recovery_terminal_receipt_sha256",
        "recovery_job_id", "failed", "exit_status",
        "accounting_projection", "scheduler_log_sha256",
    }
)
R8U_R7_TERMINAL_KEYS = R8U_R7_COMMON_CHAIN_KEYS | frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256",
        "preservation_recovery_authority_sha256",
        "preservation_recovery_claim_sha256",
        "preservation_recovery_submission_receipt_sha256",
        "preservation_worker_context_diagnostic_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256", "final_ledger_sha256",
        "batch_finalization_receipt_sha256", "npz_files_expected",
        "npz_files_observed", "npz_files_missing", "npz_files_additional",
        "npz_atime_only_differences", "npz_stable_metadata_differences",
        "extracted_npz_body_reads", "n_selected_studies",
        "n_successfully_extracted_cines", "n_clip_embeddings",
        "n_pooled_studies", "n_object_technical_dispositions",
        "n_blocking_failures", "preservation_status",
        "cache_retirement_status", "final_ledger_status",
        "batch_finalization_status", "raw_dicoms_retained",
        "failed_partial_cache_retained", "publication_reused",
        "echoprime_reused", "cloud_requests", "downloads",
        "dicom_body_reads", "dicom_extraction_executions",
        "publication_executions", "echoprime_executions",
        "embedding_generations", "gpu_executions", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    }
)
R8U_R7_CONTINUATION_LINK_KEYS = frozenset(
    {
        "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256",
        "preservation_recovery_authority_sha256",
        "preservation_recovery_submission_receipt_sha256",
        "preservation_worker_context_diagnostic_sha256",
        "preservation_recovery_accounting_sha256",
        "preservation_recovery_terminal_receipt_sha256",
    }
)
R8U_R7_CONTINUATION_CLAIM_KEYS = (
    R8U_R7_COMMON_CHAIN_KEYS
    | R8U_R7_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "prefix_final_receipt_sha256", "failed_partial_seal_sha256",
            "runtime_authority_sha256", "qsub_environment_sha256",
            "script_authority", "continuation_task_range",
            "continuation_task_count", "continuation_max_concurrency",
            "held_finalizer_count", "total_new_qsub_maximum",
            "automatic_retry_authorized", "whole_stage_retry_authorized",
            "fourth_submission_reachable", "cloud_requests_by_submitter",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter",
            "embedding_generations_by_submitter", "model_fitting_authorized",
            "prediction_authorized", "confirmatory_performance_access_authorized",
        }
    )
)
R8U_R7_CONTINUATION_SUBMISSION_KEYS = (
    R8U_R7_COMMON_CHAIN_KEYS
    | R8U_R7_CONTINUATION_LINK_KEYS
    | frozenset(
        {
            "recovery_job_id", "array_job_name", "finalizer_job_name",
            "array_job_id", "finalizer_job_id", "array_qsub_argv_sha256",
            "finalizer_qsub_argv_sha256", "qsub_environment_sha256",
            "failed_partial_seal_sha256", "continuation_claim_sha256",
            "array_qsub_evidence", "finalizer_qsub_evidence",
            "scheduler_submission_count", "total_new_qsub_submissions",
            "scheduler_submission_maximum", "array_task_range",
            "array_task_count", "array_max_concurrency",
            "finalizer_held_on_array", "whole_stage_retry_authorized",
            "fourth_submission_reachable", "cloud_requests",
            "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        }
    )
)
R8U_R7_CONTINUATION_WORKER_RECEIPT_KEYS = (
    R8U_R7_COMMON_CHAIN_KEYS
    | frozenset(
        {
            "scheduler_account_authority_sha256", "continuation_claim_sha256",
            "continuation_submission_sha256", "worker_diagnostic_sha256",
            "worker_qstat_projection_sha256", "worker_process_projection_sha256",
            "job_id", "worker_role", "task_id", "effective_uid_match",
            "job_id_match", "task_context_match", "job_role_match",
            "canonical_worker_environment_pass",
        }
    )
)
R8U_R3_CHAIN_ARTIFACT_SPECS = (
    (
        "failed_partial_seal_sha256",
        "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json",
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
        "r2",
    ),
    (
        "r2_recovery_capacity_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_capacity.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1",
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
        "r2",
    ),
    (
        "r2_recovery_authority_sha256",
        "r8u_r2_batch16_recovery/recovery_authority.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_RECOVERY",
        "r2",
    ),
    (
        "r2_recovery_submission_receipt_sha256",
        "r8u_r2_batch16_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
        "r2",
    ),
    (
        "extraction_candidate_seal_sha256",
        "r8u_r3_batch16_publication_resume/extraction_candidate_seal.restricted.json",
        "lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
        "PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
        "r3",
    ),
    (
        "publication_primitive_probe_sha256",
        "r8u_r3_batch16_publication_resume/publication_primitive_probe.restricted.json",
        "lvef_c3_r8u_r3_publication_primitive_probe_v1",
        "PASS_PUBLICATION_PRIMITIVE_PROBE",
        "r3",
    ),
    (
        "publication_claim_sha256",
        "extracted_cache/c3_batch_015/.r8u_r3_publication_claim/claim.restricted.json",
        "lvef_c3_r8u_r3_publication_claim_v1",
        "AUTHORIZED_EXCLUSIVE_BATCH16_PUBLICATION",
        "r3",
    ),
    (
        "publication_receipt_sha256",
        "r8u_r3_batch16_publication_resume/publication_receipt.restricted.json",
        "lvef_c3_r8u_r3_batch16_publication_v1",
        "PASS_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "r3",
    ),
    (
        "resume_capacity_receipt_sha256",
        "r8u_r3_batch16_publication_resume/resume_capacity.restricted.json",
        "lvef_c3_r8u_r3_batch16_publication_resume_capacity_v1",
        "PASS_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE",
        "r3_capacity",
    ),
    (
        "resume_authority_sha256",
        "r8u_r3_batch16_publication_resume/resume_authority.restricted.json",
        "lvef_c3_r8u_r3_batch16_publication_resume_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_PUBLICATION_RESUME",
        "r3",
    ),
    (
        "resume_submission_receipt_sha256",
        "r8u_r3_batch16_publication_resume/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r3_batch16_publication_resume_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
        "r3",
    ),
    (
        "resume_accounting_sha256",
        "r8u_r3_batch16_publication_resume/resume_accounting.restricted.json",
        "lvef_c3_r8u_r3_batch16_publication_resume_accounting_v1",
        "PASS_RESUME_QACCT_FAILED_0_EXIT_0",
        "r3",
    ),
    (
        "resume_terminal_receipt_sha256",
        "r8u_r3_batch16_publication_resume/resume_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r3_batch16_publication_resume_terminal_v1",
        "PASS_BATCH16_PUBLICATION_RESUME_FINALIZED",
        "r3",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r3_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r3_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "r3",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r3_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r3_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "r3",
    ),
)
R8U_R3_CHAIN_ARTIFACT_KEYS = {
    "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_KEYS,
    "r2_recovery_capacity_receipt_sha256": r8r_capacity.R8U_CAPACITY_KEYS,
    "r2_recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_KEYS,
    "r2_recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_KEYS,
    "extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_KEYS,
    "publication_primitive_probe_sha256": R8U_R3_PROBE_KEYS,
    "publication_claim_sha256": R8U_R3_PUBLICATION_CLAIM_KEYS,
    "publication_receipt_sha256": R8U_R3_PUBLICATION_KEYS,
    "resume_capacity_receipt_sha256": r8r_capacity.R8U_R3_CAPACITY_KEYS,
    "resume_authority_sha256": R8U_R3_AUTHORITY_KEYS,
    "resume_submission_receipt_sha256": R8U_R3_SUBMISSION_KEYS,
    "resume_accounting_sha256": R8U_R3_ACCOUNTING_KEYS,
    "resume_terminal_receipt_sha256": R8U_R3_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_R3_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_R3_CONTINUATION_SUBMISSION_KEYS
    ),
}
R8U_R4_CHAIN_ARTIFACT_SPECS = (
    (
        "failed_partial_seal_sha256",
        "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json",
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
        "r2",
    ),
    (
        "r2_recovery_capacity_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_capacity.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1",
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
        "r2",
    ),
    (
        "r2_recovery_authority_sha256",
        "r8u_r2_batch16_recovery/recovery_authority.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_RECOVERY",
        "r2",
    ),
    (
        "r2_recovery_submission_receipt_sha256",
        "r8u_r2_batch16_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
        "r2",
    ),
    (
        "r3_extraction_candidate_seal_sha256",
        "r8u_r3_batch16_publication_resume/extraction_candidate_seal.restricted.json",
        "lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
        "PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
        "r3_fixed",
    ),
    (
        "replay_diagnosis_sha256",
        "r8u_r4_batch16_publication_resume/candidate_replay_diagnosis.restricted.json",
        "lvef_c3_r8u_r4_candidate_replay_diagnosis_v1",
        frozenset(
            {
                "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE",
                "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE",
            }
        ),
        "r4_plain",
    ),
    (
        "portable_candidate_authority_sha256",
        "r8u_r4_batch16_publication_resume/portable_candidate_authority.restricted.json",
        "lvef_c3_r8u_r4_portable_candidate_authority_v1",
        "PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY",
        "r4",
    ),
    (
        "resume_capacity_receipt_sha256",
        "r8u_r4_batch16_publication_resume/resume_capacity.restricted.json",
        "lvef_c3_r8u_r4_batch16_publication_resume_capacity_v1",
        "PASS_R8U_R4_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE",
        "r4_capacity",
    ),
    (
        "resume_authority_sha256",
        "r8u_r4_batch16_publication_resume/resume_authority.restricted.json",
        "lvef_c3_r8u_r4_batch16_publication_resume_authority_v1",
        "AUTHORIZED_FIXED_R8U_R4_BATCH16_PUBLICATION_RESUME",
        "r4",
    ),
    (
        "resume_submission_receipt_sha256",
        "r8u_r4_batch16_publication_resume/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r4_batch16_publication_resume_submission_v1",
        "PASS_EXACT_ONE_R8U_R4_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
        "r4",
    ),
    (
        "publication_claim_sha256",
        "extracted_cache/c3_batch_015/.r8u_r4_publication_claim/claim.restricted.json",
        "lvef_c3_r8u_r4_publication_claim_v1",
        "AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION",
        "r4",
    ),
    (
        "live_publication_locality_sha256",
        "r8u_r4_batch16_publication_resume/live_publication_locality.restricted.json",
        "lvef_c3_r8u_r4_live_publication_locality_v1",
        "PASS_WORKER_LOCAL_PUBLICATION_LOCALITY",
        "r4_plain",
    ),
    (
        "publication_primitive_probe_sha256",
        "r8u_r4_batch16_publication_resume/publication_primitive_probe.restricted.json",
        "lvef_c3_r8u_r4_publication_primitive_probe_v1",
        "PASS_R8U_R4_PUBLICATION_PRIMITIVE_PROBE",
        "r4",
    ),
    (
        "publication_receipt_sha256",
        "r8u_r4_batch16_publication_resume/publication_receipt.restricted.json",
        "lvef_c3_r8u_r4_batch16_publication_v1",
        "PASS_R8U_R4_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "r4",
    ),
    (
        "resume_accounting_sha256",
        "r8u_r4_batch16_publication_resume/resume_accounting.restricted.json",
        "lvef_c3_r8u_r4_batch16_publication_resume_accounting_v1",
        "PASS_R8U_R4_RESUME_QACCT_FAILED_0_EXIT_0",
        "r4",
    ),
    (
        "resume_terminal_receipt_sha256",
        "r8u_r4_batch16_publication_resume/resume_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r4_batch16_publication_resume_terminal_v1",
        "PASS_BATCH16_R8U_R4_PUBLICATION_RESUME_FINALIZED",
        "r4",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r4_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r4_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "r4",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r4_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r4_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "r4",
    ),
)
R8U_R4_CHAIN_ARTIFACT_KEYS = {
    "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_KEYS,
    "r2_recovery_capacity_receipt_sha256": r8r_capacity.R8U_CAPACITY_KEYS,
    "r2_recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_KEYS,
    "r2_recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_KEYS,
    "r3_extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_KEYS,
    "replay_diagnosis_sha256": R8U_R4_DIAGNOSIS_KEYS,
    "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_KEYS,
    "resume_capacity_receipt_sha256": r8r_capacity.R8U_R4_CAPACITY_KEYS,
    "resume_authority_sha256": R8U_R4_AUTHORITY_KEYS,
    "resume_submission_receipt_sha256": R8U_R4_SUBMISSION_KEYS,
    "publication_claim_sha256": R8U_R4_PUBLICATION_CLAIM_KEYS,
    "live_publication_locality_sha256": R8U_R4_LOCALITY_KEYS,
    "publication_primitive_probe_sha256": R8U_R4_PROBE_KEYS,
    "publication_receipt_sha256": R8U_R4_PUBLICATION_KEYS,
    "resume_accounting_sha256": R8U_R4_ACCOUNTING_KEYS,
    "resume_terminal_receipt_sha256": R8U_R4_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_R4_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_R4_CONTINUATION_SUBMISSION_KEYS
    ),
}
R8U_R5_CHAIN_ARTIFACT_SPECS = (
    (
        "failed_partial_seal_sha256",
        "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json",
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
        "r2",
    ),
    (
        "r2_recovery_capacity_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_capacity.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1",
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
        "r2",
    ),
    (
        "r2_recovery_authority_sha256",
        "r8u_r2_batch16_recovery/recovery_authority.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_RECOVERY",
        "r2",
    ),
    (
        "r2_recovery_submission_receipt_sha256",
        "r8u_r2_batch16_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
        "r2",
    ),
    (
        "r3_extraction_candidate_seal_sha256",
        "r8u_r3_batch16_publication_resume/extraction_candidate_seal.restricted.json",
        "lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
        "PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
        "r3_fixed",
    ),
    (
        "replay_diagnosis_sha256",
        "r8u_r4_batch16_publication_resume/candidate_replay_diagnosis.restricted.json",
        "lvef_c3_r8u_r4_candidate_replay_diagnosis_v1",
        frozenset(
            {
                "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE",
                "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE",
            }
        ),
        "r4_plain",
    ),
    (
        "portable_candidate_authority_sha256",
        "r8u_r4_batch16_publication_resume/portable_candidate_authority.restricted.json",
        "lvef_c3_r8u_r4_portable_candidate_authority_v1",
        "PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY",
        "r4_fixed",
    ),
    (
        "scheduler_account_authority_sha256",
        "r8u_r5_batch16_publication_resume/scheduler_account_authority.restricted.json",
        "lvef_c3_r8u_r5_scheduler_account_authority_v1",
        "AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
        "r5",
    ),
    (
        "r8u_r4_failure_evidence_sha256",
        "r8u_r5_batch16_publication_resume/r8u_r4_failure_evidence.restricted.json",
        "lvef_c3_r8u_r5_r4_scheduler_identity_failure_v1",
        "PASS_IMMUTABLE_R8U_R4_SCHEDULER_IDENTITY_FAILURE",
        "r5",
    ),
    (
        "worker_context_probe_receipt_sha256",
        "r8u_r5_batch16_publication_resume/worker_context_probe_receipt.restricted.json",
        "lvef_c3_r8u_r5_worker_context_probe_receipt_v1",
        "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        "r5",
    ),
    (
        "worker_context_probe_accounting_sha256",
        "r8u_r5_batch16_publication_resume/worker_context_probe_accounting.restricted.json",
        "lvef_c3_r8u_r5_worker_context_probe_accounting_v1",
        "PASS_R8U_R5_PROBE_QACCT_FAILED_0_EXIT_0",
        "r5",
    ),
    (
        "resume_capacity_receipt_sha256",
        "r8u_r5_batch16_publication_resume/resume_capacity.restricted.json",
        "lvef_c3_r8u_r5_batch16_resume_capacity_v1",
        "PASS_R8U_R5_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE",
        "r5",
    ),
    (
        "resume_authority_sha256",
        "r8u_r5_batch16_publication_resume/resume_authority.restricted.json",
        "lvef_c3_r8u_r5_batch16_publication_resume_authority_v1",
        "AUTHORIZED_FIXED_R8U_R5_BATCH16_PUBLICATION_RESUME",
        "r5",
    ),
    (
        "resume_submission_receipt_sha256",
        "r8u_r5_batch16_publication_resume/scheduler/resume_submission_receipt.restricted.json",
        "lvef_c3_r8u_r5_batch16_publication_resume_submission_v1",
        "PASS_EXACT_ONE_R8U_R5_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
        "r5",
    ),
    (
        "live_publication_locality_sha256",
        "r8u_r5_batch16_publication_resume/live_publication_locality.restricted.json",
        "lvef_c3_r8u_r5_live_publication_locality_v1",
        "PASS_R8U_R5_WORKER_LOCAL_PUBLICATION_LOCALITY",
        "r5",
    ),
    (
        "publication_claim_sha256",
        "extracted_cache/c3_batch_015/.r8u_r5_publication_claim/claim.restricted.json",
        "lvef_c3_r8u_r5_publication_claim_v1",
        "AUTHORIZED_EXCLUSIVE_R8U_R5_BATCH16_PUBLICATION",
        "r5",
    ),
    (
        "publication_primitive_probe_sha256",
        "r8u_r5_batch16_publication_resume/publication_primitive_probe.restricted.json",
        "lvef_c3_r8u_r5_publication_primitive_probe_v1",
        "PASS_R8U_R5_PUBLICATION_PRIMITIVE_PROBE",
        "r5",
    ),
    (
        "publication_receipt_sha256",
        "r8u_r5_batch16_publication_resume/publication_receipt.restricted.json",
        "lvef_c3_r8u_r5_batch16_publication_v1",
        "PASS_R8U_R5_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "r5",
    ),
    (
        "resume_accounting_sha256",
        "r8u_r5_batch16_publication_resume/resume_accounting.restricted.json",
        "lvef_c3_r8u_r5_batch16_publication_resume_accounting_v1",
        "PASS_R8U_R5_RESUME_QACCT_FAILED_0_EXIT_0",
        "r5",
    ),
    (
        "resume_terminal_receipt_sha256",
        "r8u_r5_batch16_publication_resume/resume_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r5_batch16_publication_resume_terminal_v1",
        "PASS_BATCH16_R8U_R5_PUBLICATION_RESUME_FINALIZED",
        "r5",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r5_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r5_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "r5",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r5_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r5_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "r5",
    ),
)
R8U_R5_CHAIN_ARTIFACT_KEYS = {
    "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_KEYS,
    "r2_recovery_capacity_receipt_sha256": r8r_capacity.R8U_CAPACITY_KEYS,
    "r2_recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_KEYS,
    "r2_recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_KEYS,
    "r3_extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_KEYS,
    "replay_diagnosis_sha256": R8U_R4_DIAGNOSIS_KEYS,
    "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_KEYS,
    "scheduler_account_authority_sha256": R8U_R5_ACCOUNT_AUTHORITY_KEYS,
    "r8u_r4_failure_evidence_sha256": R8U_R5_R4_FAILURE_EVIDENCE_KEYS,
    "worker_context_probe_receipt_sha256": R8U_R5_PROBE_RECEIPT_KEYS,
    "worker_context_probe_accounting_sha256": R8U_R5_PROBE_ACCOUNTING_KEYS,
    "resume_capacity_receipt_sha256": R8U_R5_CAPACITY_KEYS,
    "resume_authority_sha256": R8U_R5_AUTHORITY_KEYS,
    "resume_submission_receipt_sha256": R8U_R5_SUBMISSION_KEYS,
    "live_publication_locality_sha256": R8U_R5_LOCALITY_KEYS,
    "publication_claim_sha256": R8U_R5_PUBLICATION_CLAIM_KEYS,
    "publication_primitive_probe_sha256": R8U_R5_PROBE_KEYS,
    "publication_receipt_sha256": R8U_R5_PUBLICATION_KEYS,
    "resume_accounting_sha256": R8U_R5_ACCOUNTING_KEYS,
    "resume_terminal_receipt_sha256": R8U_R5_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_R5_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_R5_CONTINUATION_SUBMISSION_KEYS
    ),
}
R8U_R6_CHAIN_ARTIFACT_SPECS = (
    (
        "failed_partial_seal_sha256",
        "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json",
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE",
        "r2",
    ),
    (
        "r2_recovery_capacity_receipt_sha256",
        "r8u_r2_batch16_recovery/recovery_capacity.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1",
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
        "r2",
    ),
    (
        "r2_recovery_authority_sha256",
        "r8u_r2_batch16_recovery/recovery_authority.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "AUTHORIZED_FIXED_BATCH16_RECOVERY",
        "r2",
    ),
    (
        "r2_recovery_submission_receipt_sha256",
        "r8u_r2_batch16_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "PASS_EXACT_ONE_GPU_BATCH16_RECOVERY_QSUB",
        "r2",
    ),
    (
        "r3_extraction_candidate_seal_sha256",
        "r8u_r3_batch16_publication_resume/extraction_candidate_seal.restricted.json",
        "lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1",
        "PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED",
        "r3_fixed",
    ),
    (
        "replay_diagnosis_sha256",
        "r8u_r4_batch16_publication_resume/candidate_replay_diagnosis.restricted.json",
        "lvef_c3_r8u_r4_candidate_replay_diagnosis_v1",
        frozenset(
            {
                "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE",
                "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE",
            }
        ),
        "r4_plain",
    ),
    (
        "portable_candidate_authority_sha256",
        "r8u_r4_batch16_publication_resume/portable_candidate_authority.restricted.json",
        "lvef_c3_r8u_r4_portable_candidate_authority_v1",
        "PASS_PORTABLE_BATCH16_CANDIDATE_AUTHORITY",
        "r4_fixed",
    ),
    (
        "scheduler_account_authority_sha256",
        "r8u_r6_batch16_publication_resume/scheduler_account_authority.restricted.json",
        "lvef_c3_r8u_r6_scheduler_account_authority_v1",
        "AUTHORIZED_R8U_R6_SCHEDULER_ACCOUNT",
        "r6",
    ),
    (
        "r8u_r5_failure_evidence_sha256",
        "r8u_r6_batch16_publication_resume/r8u_r5_failure_evidence.restricted.json",
        "lvef_c3_r8u_r6_r5_publication_locality_failure_v1",
        "PASS_IMMUTABLE_R8U_R5_PUBLICATION_LOCALITY_FAILURE",
        "r6",
    ),
    (
        "locality_sequence_probe_receipt_sha256",
        "r8u_r6_batch16_publication_resume/locality_sequence_probe_receipt.restricted.json",
        "lvef_c3_r8u_r6_locality_sequence_probe_receipt_v1",
        "PASS_R8U_R6_STABLE_PUBLICATION_LOCALITY",
        "r6",
    ),
    (
        "locality_sequence_probe_accounting_sha256",
        "r8u_r6_batch16_publication_resume/locality_sequence_probe_accounting.restricted.json",
        "lvef_c3_r8u_r6_locality_sequence_probe_accounting_v1",
        "PASS_R8U_R6_LOCALITY_PROBE_QACCT_FAILED_0_EXIT_0",
        "r6",
    ),
    (
        "resume_capacity_receipt_sha256",
        "r8u_r6_batch16_publication_resume/resume_capacity.restricted.json",
        "lvef_c3_r8u_r6_batch16_resume_capacity_v1",
        "PASS_R8U_R6_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE",
        "r6",
    ),
    (
        "resume_authority_sha256",
        "r8u_r6_batch16_publication_resume/resume_authority.restricted.json",
        "lvef_c3_r8u_r6_batch16_publication_resume_authority_v1",
        "AUTHORIZED_FIXED_R8U_R6_BATCH16_PUBLICATION_RESUME",
        "r6",
    ),
    (
        "resume_submission_receipt_sha256",
        "r8u_r6_batch16_publication_resume/scheduler/resume_submission_receipt.restricted.json",
        "lvef_c3_r8u_r6_batch16_publication_resume_submission_v1",
        "PASS_EXACT_ONE_R8U_R6_GPU_BATCH16_PUBLICATION_RESUME_QSUB",
        "r6",
    ),
    (
        "gpu_worker_context_diagnostic_sha256",
        "r8u_r6_batch16_publication_resume/gpu_worker_context_diagnostic.restricted.json",
        "lvef_c3_r8u_r6_worker_scheduler_context_v1",
        "PASS_R8U_R6_WORKER_SCHEDULER_CONTEXT",
        "r6_plain",
    ),
    (
        "publication_claim_sha256",
        "extracted_cache/c3_batch_015/.r8u_r6_publication_claim/claim.restricted.json",
        "lvef_c3_r8u_r6_publication_claim_v1",
        "AUTHORIZED_EXCLUSIVE_R8U_R6_BATCH16_PUBLICATION",
        "r6",
    ),
    (
        "publication_primitive_probe_sha256",
        "r8u_r6_batch16_publication_resume/publication_primitive_probe.restricted.json",
        "lvef_c3_r8u_r6_publication_primitive_probe_v1",
        "PASS_R8U_R6_PUBLICATION_PRIMITIVE_PROBE",
        "r6",
    ),
    (
        "final_publication_locality_sha256",
        "r8u_r6_batch16_publication_resume/final_publication_locality.restricted.json",
        "lvef_c3_r8u_r6_final_publication_locality_v1",
        "PASS_R8U_R6_FINAL_PUBLICATION_LOCALITY",
        "r6",
    ),
    (
        "publication_receipt_sha256",
        "r8u_r6_batch16_publication_resume/publication_receipt.restricted.json",
        "lvef_c3_r8u_r6_batch16_publication_v1",
        "PASS_R8U_R6_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "r6",
    ),
    (
        "resume_accounting_sha256",
        "r8u_r6_batch16_publication_resume/resume_accounting.restricted.json",
        "lvef_c3_r8u_r6_batch16_publication_resume_accounting_v1",
        "PASS_R8U_R6_RESUME_QACCT_FAILED_0_EXIT_0",
        "r6",
    ),
    (
        "resume_terminal_receipt_sha256",
        "r8u_r6_batch16_publication_resume/resume_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r6_batch16_publication_resume_terminal_v1",
        "PASS_BATCH16_R8U_R6_PUBLICATION_RESUME_FINALIZED",
        "r6",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r6_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r6_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "r6",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r6_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r6_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "r6",
    ),
)
R8U_R6_CHAIN_ARTIFACT_KEYS = {
    "failed_partial_seal_sha256": R8U_FAILED_PARTIAL_SEAL_KEYS,
    "r2_recovery_capacity_receipt_sha256": r8r_capacity.R8U_CAPACITY_KEYS,
    "r2_recovery_authority_sha256": R8U_RECOVERY_AUTHORITY_KEYS,
    "r2_recovery_submission_receipt_sha256": R8U_RECOVERY_SUBMISSION_KEYS,
    "r3_extraction_candidate_seal_sha256": R8U_R3_CANDIDATE_SEAL_KEYS,
    "replay_diagnosis_sha256": R8U_R4_DIAGNOSIS_KEYS,
    "portable_candidate_authority_sha256": R8U_R4_PORTABLE_AUTHORITY_KEYS,
    "scheduler_account_authority_sha256": R8U_R6_ACCOUNT_AUTHORITY_KEYS,
    "r8u_r5_failure_evidence_sha256": R8U_R6_R5_FAILURE_EVIDENCE_KEYS,
    "locality_sequence_probe_receipt_sha256": R8U_R6_PROBE_RECEIPT_KEYS,
    "locality_sequence_probe_accounting_sha256": R8U_R6_PROBE_ACCOUNTING_KEYS,
    "resume_capacity_receipt_sha256": R8U_R6_CAPACITY_KEYS,
    "resume_authority_sha256": R8U_R6_AUTHORITY_KEYS,
    "resume_submission_receipt_sha256": R8U_R6_SUBMISSION_KEYS,
    "gpu_worker_context_diagnostic_sha256": R8U_R5_WORKER_DIAGNOSTIC_KEYS,
    "publication_claim_sha256": R8U_R6_PUBLICATION_CLAIM_KEYS,
    "publication_primitive_probe_sha256": R8U_R6_PROBE_KEYS,
    "final_publication_locality_sha256": R8U_R6_FINAL_LOCALITY_KEYS,
    "publication_receipt_sha256": R8U_R6_PUBLICATION_KEYS,
    "resume_accounting_sha256": R8U_R6_ACCOUNTING_KEYS,
    "resume_terminal_receipt_sha256": R8U_R6_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_R6_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_R6_CONTINUATION_SUBMISSION_KEYS
    ),
}
R8U_R7_R6_PUBLICATION_ARTIFACT_SPECS = tuple(
    (
        "r8u_r6_scheduler_account_authority_sha256"
        if field == "scheduler_account_authority_sha256"
        else field,
        relative_path,
        artifact_type,
        status,
        epoch_kind,
    )
    for field, relative_path, artifact_type, status, epoch_kind
    in R8U_R6_CHAIN_ARTIFACT_SPECS
    if field not in {
        "resume_accounting_sha256",
        "resume_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "continuation_submission_receipt_sha256",
    }
)
R8U_R7_CHAIN_ARTIFACT_SPECS = (
    *R8U_R7_R6_PUBLICATION_ARTIFACT_SPECS,
    (
        "r8u_r6_failure_evidence_sha256",
        "r8u_r7_batch16_preservation_recovery/r8u_r6_failure_evidence.restricted.json",
        "lvef_c3_r8u_r7_r6_preservation_failure_v1",
        "PASS_IMMUTABLE_R8U_R6_SCIENTIFIC_OUTPUTS_AND_PRESERVATION_FAILURE",
        "r7",
    ),
    (
        "scheduler_account_authority_sha256",
        "r8u_r7_batch16_preservation_recovery/scheduler_account_authority.restricted.json",
        "lvef_c3_r8u_r7_scheduler_account_authority_v1",
        "AUTHORIZED_R8U_R7_SCHEDULER_ACCOUNT",
        "r7",
    ),
    (
        "preservation_recovery_capacity_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_recovery_capacity.restricted.json",
        "lvef_c3_r8u_r7_preservation_recovery_capacity_v1",
        "PASS_R8U_R7_BOUNDED_PRESERVATION_RECOVERY_ENVELOPE",
        "r7",
    ),
    (
        "preservation_recovery_claim_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_recovery_claim.restricted.json",
        "lvef_c3_r8u_r7_preservation_recovery_claim_v1",
        "AUTHORIZED_EXCLUSIVE_R8U_R7_BATCH16_PRESERVATION_RECOVERY",
        "r7",
    ),
    (
        "preservation_recovery_authority_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_recovery_authority.restricted.json",
        "lvef_c3_r8u_r7_preservation_recovery_authority_v1",
        "AUTHORIZED_FIXED_R8U_R7_BATCH16_PRESERVATION_RECOVERY",
        "r7",
    ),
    (
        "preservation_recovery_submission_receipt_sha256",
        "r8u_r7_batch16_preservation_recovery/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r7_preservation_recovery_submission_v1",
        "PASS_EXACT_ONE_R8U_R7_CPU_PRESERVATION_RECOVERY_QSUB",
        "r7",
    ),
    (
        "preservation_worker_context_diagnostic_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_worker_context_diagnostic.restricted.json",
        "lvef_c3_r8u_r7_worker_scheduler_context_v1",
        "PASS_R8U_R7_WORKER_SCHEDULER_CONTEXT",
        "r7_plain",
    ),
    (
        "preservation_recovery_accounting_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_recovery_accounting.restricted.json",
        "lvef_c3_r8u_r7_preservation_recovery_accounting_v1",
        "PASS_R8U_R7_PRESERVATION_RECOVERY_QACCT_FAILED_0_EXIT_0",
        "r7",
    ),
    (
        "preservation_recovery_terminal_receipt_sha256",
        "r8u_r7_batch16_preservation_recovery/preservation_recovery_terminal.aggregate_safe.json",
        "lvef_c3_r8u_r7_preservation_recovery_terminal_v1",
        "PASS_R8U_R7_BATCH16_PRESERVATION_RECOVERY_FINALIZED",
        "r7",
    ),
    (
        "continuation_claim_sha256",
        "r8u_r7_continuation_17_19/continuation_claim.restricted.json",
        "lvef_c3_r8u_r7_fixed_continuation_claim_v1",
        "AUTHORIZED_FIXED_CONTINUATION_17_19",
        "r7",
    ),
    (
        "continuation_submission_receipt_sha256",
        "r8u_r7_continuation_17_19/scheduler/submission_receipt.restricted.json",
        "lvef_c3_r8u_r7_fixed_continuation_submission_v1",
        "PASS_EXACT_ARRAY_17_19_AND_HELD_FINALIZER",
        "r7",
    ),
)
R8U_R7_CHAIN_ARTIFACT_KEYS = {
    **{
        (
            "r8u_r6_scheduler_account_authority_sha256"
            if field == "scheduler_account_authority_sha256"
            else field
        ): keys
        for field, keys in R8U_R6_CHAIN_ARTIFACT_KEYS.items()
        if field not in {
            "resume_accounting_sha256",
            "resume_terminal_receipt_sha256",
            "continuation_claim_sha256",
            "continuation_submission_receipt_sha256",
        }
    },
    "r8u_r6_failure_evidence_sha256": R8U_R7_R6_FAILURE_EVIDENCE_KEYS,
    "scheduler_account_authority_sha256": R8U_R7_ACCOUNT_AUTHORITY_KEYS,
    "preservation_recovery_capacity_sha256": R8U_R7_CAPACITY_KEYS,
    "preservation_recovery_claim_sha256": R8U_R7_CLAIM_KEYS,
    "preservation_recovery_authority_sha256": R8U_R7_AUTHORITY_KEYS,
    "preservation_recovery_submission_receipt_sha256": R8U_R7_SUBMISSION_KEYS,
    "preservation_worker_context_diagnostic_sha256": (
        R8U_R5_WORKER_DIAGNOSTIC_KEYS
    ),
    "preservation_recovery_accounting_sha256": R8U_R7_ACCOUNTING_KEYS,
    "preservation_recovery_terminal_receipt_sha256": R8U_R7_TERMINAL_KEYS,
    "continuation_claim_sha256": R8U_R7_CONTINUATION_CLAIM_KEYS,
    "continuation_submission_receipt_sha256": (
        R8U_R7_CONTINUATION_SUBMISSION_KEYS
    ),
}
R8R_QSUB_EVIDENCE_KEYS = frozenset(
    {
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "exit_status",
    }
)
R8R_RECOVERY_ACCOUNTING_KEYS = frozenset(
    {
        "status",
        "job_id",
        "task_id",
        "failed",
        "exit_status",
        "start_time",
        "end_time",
        "ru_wallclock_seconds",
    }
)
R8R_RECOVERY_AUTHORITY_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "batch_id",
        "original_control_authority",
        "prefix_final_receipt_sha256",
        "batch3_retained_authority",
        "script_authority",
        "runtime_validation_context",
        "qsub_environment_sha256",
        "fixed_recovery_task",
        "cloud_requests_authorized",
        "dicom_body_reads_authorized",
        "dicom_extraction_reruns_authorized",
        "echoprime_reruns_authorized",
        "embedding_generations_authorized",
        "gpu_executions_authorized",
        "raw_dicom_deletion_authorized",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
    }
)
R8R_RECOVERY_SUBMISSION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "batch_id",
        "recovery_job_name",
        "recovery_job_id",
        "recovery_qsub_argv_sha256",
        "qsub_environment_sha256",
        "recovery_qsub_evidence",
        "scheduler_submission_count",
        "recovery_is_array",
        "gpu_requested",
        "automatic_retry_authorized",
        "cloud_requests",
        "dicom_body_reads_by_submitter",
        "gpu_executions_by_submitter",
    }
)
R8R_RECOVERY_TERMINAL_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "batch_id",
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "batch_finalization_receipt_sha256",
        "raw_retention_authority",
        "n_selected_studies",
        "n_expected_objects",
        "expected_source_bytes",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_clip_embeddings",
        "n_pooled_studies",
        "n_no_cine_studies",
        "n_new_no_cine_studies",
        "raw_dicoms_retained",
        "extracted_cache_retired",
        "download_reruns",
        "dicom_extraction_reruns",
        "echoprime_reruns",
        "embedding_generations",
        "cloud_requests",
        "dicom_body_reads",
        "gpu_executions",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8R_CONTINUATION_CAPACITY_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "captured_at_utc",
        "continuation_task_range",
        "continuation_task_count",
        "continuation_max_concurrency",
        "prefix_final_receipt_sha256",
        "recovery_terminal_receipt_sha256",
        "recovery_scheduler_accounting",
        "capacity_observation",
        "active_extraction_caches",
        "continuation_root_absent_at_capture",
        "continuation_claim_absent_at_capture",
        "continuation_submission_receipt_absent_at_capture",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "scheduler_submissions",
        "gpu_executions",
        "embedding_generations",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    }
)
R8R_CONTINUATION_CLAIM_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "original_claim_sha256",
        "original_submission_receipt_sha256",
        "prefix_final_receipt_sha256",
        "recovery_terminal_receipt_sha256",
        "continuation_capacity_receipt_sha256",
        "recovery_job_id",
        "recovery_scheduler_accounting_sha256",
        "runtime_authority_sha256",
        "qsub_environment_sha256",
        "script_authority",
        "continuation_task_range",
        "continuation_task_count",
        "continuation_max_concurrency",
        "held_finalizer_count",
        "automatic_retry_authorized",
        "whole_stage_retry_authorized",
        "third_continuation_submission_reachable",
        "cloud_requests_by_submitter",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "gpu_executions_by_submitter",
        "embedding_generations_by_submitter",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
    }
)
R8R_CONTINUATION_SUBMISSION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "original_scientific_commit",
        "implementation_commit",
        "attempt_id",
        "batch_plan_sha256",
        "array_job_name",
        "finalizer_job_name",
        "array_job_id",
        "finalizer_job_id",
        "array_qsub_argv_sha256",
        "finalizer_qsub_argv_sha256",
        "qsub_environment_sha256",
        "continuation_capacity_receipt_sha256",
        "continuation_claim_sha256",
        "array_qsub_evidence",
        "finalizer_qsub_evidence",
        "scheduler_submission_count",
        "scheduler_submission_maximum",
        "array_task_range",
        "array_task_count",
        "array_max_concurrency",
        "finalizer_held_on_array",
        "whole_stage_retry_authorized",
        "third_continuation_submission_reachable",
        "cloud_requests",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "gpu_executions_by_submitter",
    }
)


@dataclass(frozen=True)
class R8RImplementationAuthority:
    """Closed external binding for the one authorized mixed-epoch finalizer.

    The controller validates the referenced restricted artifacts and supplies
    their exact hashes.  The finalizer binds that immutable recovery and
    continuation chain to the current repair implementation before accepting
    the otherwise-prohibited two-epoch receipt set.
    """

    implementation_commit: str
    recovery_authority_sha256: str
    recovery_terminal_receipt_sha256: str
    continuation_capacity_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UImplementationAuthority:
    """Closed binding for the one fixed R8U-R2 recovery continuation.

    The five historical hashes bind the already completed R8R repair at the
    exact prior implementation commit.  The eight current hashes bind the
    R2 failed-partial seal, one capacity observation, the immutable failed-R1
    authority, the one-job Batch-16 recovery, its terminal accounting, and the
    exact Tasks-17--19 successor.  No path, attempt, batch, plan, or range is
    caller selectable.
    """

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    recovery_capacity_receipt_sha256: str
    recovery_authority_sha256: str
    recovery_submission_receipt_sha256: str
    recovery_accounting_sha256: str
    recovery_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR3ImplementationAuthority:
    """Closed binding for publication-resumed Batch 16 and Tasks 17--19.

    R8U-R2 remains immutable failed evidence.  These hashes bind the complete
    R3 resume chain, its later successful accounting, and the exact fixed
    continuation without accepting caller-selected paths, batches, or ranges.
    """

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    r2_recovery_capacity_receipt_sha256: str
    r2_recovery_authority_sha256: str
    r2_recovery_submission_receipt_sha256: str
    extraction_candidate_seal_sha256: str
    publication_primitive_probe_sha256: str
    publication_claim_sha256: str
    publication_receipt_sha256: str
    resume_capacity_receipt_sha256: str
    resume_authority_sha256: str
    resume_submission_receipt_sha256: str
    resume_accounting_sha256: str
    resume_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR4ImplementationAuthority:
    """Closed binding for portable R4 Batch 16 and its fixed continuation."""

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    r2_recovery_capacity_receipt_sha256: str
    r2_recovery_authority_sha256: str
    r2_recovery_submission_receipt_sha256: str
    r3_extraction_candidate_seal_sha256: str
    replay_diagnosis_sha256: str
    portable_candidate_authority_sha256: str
    live_publication_locality_sha256: str
    publication_primitive_probe_sha256: str
    publication_claim_sha256: str
    publication_receipt_sha256: str
    resume_capacity_receipt_sha256: str
    resume_authority_sha256: str
    resume_submission_receipt_sha256: str
    resume_accounting_sha256: str
    resume_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR5ImplementationAuthority:
    """Closed binding for the R5 worker-context repair and continuation.

    The controller validates effective-UID, job-ID, role, qstat, process, and
    sealed runtime authority before invoking this finalizer.  This value then
    closes the materialized R5 evidence chain around the same scheduler-account
    authority, without reconstructing a qsub submitter environment here.
    """

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    r2_recovery_capacity_receipt_sha256: str
    r2_recovery_authority_sha256: str
    r2_recovery_submission_receipt_sha256: str
    r3_extraction_candidate_seal_sha256: str
    replay_diagnosis_sha256: str
    scheduler_account_authority_sha256: str
    r8u_r4_failure_evidence_sha256: str
    worker_context_probe_receipt_sha256: str
    worker_context_probe_accounting_sha256: str
    portable_candidate_authority_sha256: str
    live_publication_locality_sha256: str
    publication_primitive_probe_sha256: str
    publication_claim_sha256: str
    publication_receipt_sha256: str
    resume_capacity_receipt_sha256: str
    resume_authority_sha256: str
    resume_submission_receipt_sha256: str
    resume_accounting_sha256: str
    resume_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR6ImplementationAuthority:
    """Closed binding for post-probe R6 publication and continuation."""

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    r2_recovery_capacity_receipt_sha256: str
    r2_recovery_authority_sha256: str
    r2_recovery_submission_receipt_sha256: str
    r3_extraction_candidate_seal_sha256: str
    replay_diagnosis_sha256: str
    portable_candidate_authority_sha256: str
    scheduler_account_authority_sha256: str
    r8u_r5_failure_evidence_sha256: str
    locality_sequence_probe_receipt_sha256: str
    locality_sequence_probe_accounting_sha256: str
    resume_capacity_receipt_sha256: str
    resume_authority_sha256: str
    resume_submission_receipt_sha256: str
    gpu_worker_context_diagnostic_sha256: str
    publication_claim_sha256: str
    publication_primitive_probe_sha256: str
    final_publication_locality_sha256: str
    publication_receipt_sha256: str
    resume_accounting_sha256: str
    resume_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR7ImplementationAuthority:
    """Closed binding for the R7 preservation repair and fixed continuation."""

    implementation_commit: str
    historical_r8r_recovery_authority_sha256: str
    historical_r8r_recovery_terminal_receipt_sha256: str
    historical_r8r_continuation_capacity_receipt_sha256: str
    historical_r8r_continuation_claim_sha256: str
    historical_r8r_continuation_submission_receipt_sha256: str
    failed_partial_seal_sha256: str
    r2_recovery_capacity_receipt_sha256: str
    r2_recovery_authority_sha256: str
    r2_recovery_submission_receipt_sha256: str
    r3_extraction_candidate_seal_sha256: str
    replay_diagnosis_sha256: str
    portable_candidate_authority_sha256: str
    r8u_r6_scheduler_account_authority_sha256: str
    r8u_r5_failure_evidence_sha256: str
    locality_sequence_probe_receipt_sha256: str
    locality_sequence_probe_accounting_sha256: str
    resume_capacity_receipt_sha256: str
    resume_authority_sha256: str
    resume_submission_receipt_sha256: str
    gpu_worker_context_diagnostic_sha256: str
    publication_claim_sha256: str
    publication_primitive_probe_sha256: str
    final_publication_locality_sha256: str
    publication_receipt_sha256: str
    r8u_r6_failure_evidence_sha256: str
    scheduler_account_authority_sha256: str
    preservation_recovery_capacity_sha256: str
    preservation_recovery_claim_sha256: str
    preservation_recovery_authority_sha256: str
    preservation_recovery_submission_receipt_sha256: str
    preservation_worker_context_diagnostic_sha256: str
    preservation_recovery_accounting_sha256: str
    preservation_recovery_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


@dataclass(frozen=True)
class R8UR7DImplementationAuthority:
    """Closed binding for the fresh R7D Tasks-17--19 execution epoch.

    The first sixteen receipt hashes are fixed as an ordered tuple.  The old
    R7A scheduler failure and R7C accounting records are bound only as
    historical control-plane evidence; none is accepted as a batch receipt.
    The controller validates each referenced restricted artifact before
    constructing this value.
    """

    implementation_commit: str
    prior_r7_runtime_commit: str
    r7c_adjudication_commit: str
    finalized_prefix_receipt_sha256: tuple[str, ...]
    consumed_r7a_continuation_receipt_sha256: str
    consumed_task17_accounting_receipt_sha256: str
    consumed_task18_accounting_receipt_sha256: str
    consumed_task19_accounting_receipt_sha256: str
    consumed_finalizer_accounting_receipt_sha256: str
    capacity_receipt_sha256: str
    scheduler_account_authority_sha256: str
    probe_terminal_receipt_sha256: str
    continuation_claim_sha256: str
    continuation_submission_receipt_sha256: str


R8R_IMPLEMENTATION_EPOCH_KEYS = (
    "production_stage_wrapper_sha256",
    "batch_preservation_script_sha256",
    "scheduler_runner_sha256",
    "command_checksum",
    "cache_retirement_script_sha256",
)
CROSS_BATCH_AUTHORITY_KEYS = (
    "governing_commit",
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "checkpoint_sha256",
    "environment_receipt_sha256",
    *R8R_IMPLEMENTATION_EPOCH_KEYS[:-1],
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
    R8R_IMPLEMENTATION_EPOCH_KEYS[-1],
)
R8R_SCIENTIFIC_AUTHORITY_KEYS = tuple(
    key
    for key in CROSS_BATCH_AUTHORITY_KEYS
    if key not in R8R_IMPLEMENTATION_EPOCH_KEYS
)

LEGACY_BATCH_RECEIPT_KEYS_V2 = {
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
CURRENT_RECEIPT_ADDITIONAL_KEYS = {
    "n_successfully_extracted_cines",
    "n_object_technical_dispositions",
    "n_blocking_failures",
    "n_studies_affected_by_technical_disposition",
    "n_new_no_cine_studies",
    "technical_disposition_counts_by_class",
    "technical_disposition_policy_version",
    "technical_disposition_manifest_sha256",
    "prespecified_no_cine_study_set_sha256",
    "all_no_cine_studies_prespecified",
    "all_extraction_rows_resolved",
    "all_successful_extractions_embedded",
    "all_technical_dispositions_retained",
    "object_substitution_count",
    "unaccounted_multiframe_objects",
}
BATCH_RECEIPT_KEYS = (
    LEGACY_BATCH_RECEIPT_KEYS_V2 | CURRENT_RECEIPT_ADDITIONAL_KEYS
)
RETIREMENT_RECEIPT_KEYS = {
    "cache_retirement_authorization_sha256",
    "cache_tree_sha256",
    "cache_atomically_staged_receipt_sha256",
    "cache_retirement_script_sha256",
}
LEGACY_PRESERVATION_ELIGIBILITY_RECEIPT_KEYS_V2 = (
    LEGACY_BATCH_RECEIPT_KEYS_V2 - RETIREMENT_RECEIPT_KEYS
)
PRESERVATION_ELIGIBILITY_RECEIPT_KEYS = (
    BATCH_RECEIPT_KEYS - RETIREMENT_RECEIPT_KEYS
)
LEGACY_TRUE_GATE_KEYS_V2 = {
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
TRUE_GATE_KEYS = LEGACY_TRUE_GATE_KEYS_V2 | {
    "all_extraction_rows_resolved",
    "all_successful_extractions_embedded",
    "all_technical_dispositions_retained",
    "all_no_cine_studies_prespecified",
}
LEGACY_ZERO_KEYS_V2 = {
    "n_outside_selected_studies",
    "n_missing_selected_studies",
    "n_duplicate_physical_sources",
    "n_duplicate_clip_keys",
    "n_nonfinite_embeddings",
    "n_wrong_dimension_embeddings",
}
ZERO_KEYS = LEGACY_ZERO_KEYS_V2 | {
    "n_blocking_failures",
    "n_new_no_cine_studies",
    "object_substitution_count",
    "unaccounted_multiframe_objects",
}
LEGACY_HASH_KEYS_V2 = {
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
HASH_KEYS = LEGACY_HASH_KEYS_V2 | {
    "technical_disposition_manifest_sha256",
    "prespecified_no_cine_study_set_sha256",
}
LEGACY_COUNT_KEYS_V2 = {
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
    *LEGACY_ZERO_KEYS_V2,
}
COUNT_KEYS = LEGACY_COUNT_KEYS_V2 | {
    "n_successfully_extracted_cines",
    "n_object_technical_dispositions",
    "n_blocking_failures",
    "n_studies_affected_by_technical_disposition",
    "n_new_no_cine_studies",
    "object_substitution_count",
    "unaccounted_multiframe_objects",
}
BASE_FINAL_KEYS = {
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
    "successfully_extracted_cines",
    "object_technical_dispositions",
    "blocking_failures",
    "studies_affected_by_technical_disposition",
    "new_no_cine_studies",
    "technical_disposition_counts_by_class",
    "technical_disposition_policy_version",
    "technical_disposition_manifest_set_sha256",
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
    "all_extraction_rows_resolved",
    "all_successful_extractions_embedded",
    "all_technical_dispositions_retained",
    "all_no_cine_studies_prespecified",
    "object_substitution_count",
    "unaccounted_multiframe_objects",
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
    "model_fitting_count",
    "endpoint_prediction_count",
    "confirmatory_performance_access_count",
}
FINAL_BINDING_KEYS = {
    "canonical_clip_index_sha256",
    "canonical_clip_index_size_bytes",
    "canonical_clip_index_rows",
    "canonical_study_embeddings_sha256",
    "canonical_study_embeddings_size_bytes",
    "canonical_study_manifest_sha256",
    "canonical_study_manifest_size_bytes",
    "canonical_study_store_receipt_sha256",
    "canonical_study_store_receipt_size_bytes",
    "cohort_preservation_receipt_sha256",
    "cohort_preservation_receipt_size_bytes",
    "cohort_preserved_artifacts",
    "cohort_preservation_second_pass_replay_passed",
    "cohort_preservation_passed",
}
FINAL_KEYS = BASE_FINAL_KEYS | FINAL_BINDING_KEYS
R8R_FINAL_SUMMARY_KEYS = {
    "all_scientific_authority_bindings_identical",
    "implementation_authority_epoch_count",
    "r8r_implementation_commit",
    "r8r_recovery_continuation_authority_sha256",
}
R8U_FINAL_SUMMARY_KEYS = {
    "all_scientific_authority_bindings_identical",
    "implementation_authority_epoch_count",
    "r8u_implementation_commit",
    "r8u_recovery_continuation_authority_sha256",
}
R8U_R7D_FINAL_SUMMARY_KEYS = {
    "all_scientific_authority_bindings_identical",
    "implementation_authority_epoch_count",
    "r8u_r7d_implementation_commit",
    "r8u_r7d_continuation_authority_sha256",
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
    exact_ledgers = {
        "download_ledger": (
            f"{prefix}/batches/{batch_id}/download_resume_ledger.restricted.json"
        ),
        "extraction_ledger": (
            f"{prefix}/batches/{batch_id}/extraction_resume_ledger.restricted.json"
        ),
        "pooling_ledger": (
            f"{prefix}/batches/{batch_id}/pooling_resume_ledger.restricted.json"
        ),
    }
    observed_ledger_roles: set[str] = set()
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
        if role in exact_ledgers:
            if relative != exact_ledgers[role] or role in observed_ledger_roles:
                raise ProductionFinalizationError("PRESERVATION_ROLE_PATH_MISMATCH")
            observed_ledger_roles.add(role)
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
        try:
            size_bytes, digest = (
                preservation.stable_manifest_artifact_authority(path)
            )
        except preservation.BatchPreservationError as exc:
            raise ProductionFinalizationError(
                "RETAINED_ARTIFACT_AUTHORITY_INVALID"
            ) from exc
        if size_bytes != int(row["size_bytes"]):
            raise ProductionFinalizationError("RETAINED_ARTIFACT_SIZE_MISMATCH")
        if digest != row["sha256"]:
            raise ProductionFinalizationError("RETAINED_ARTIFACT_HASH_MISMATCH")
        listed_retained.add(relative)
        retained += 1
    if not cache_records:
        raise ProductionFinalizationError("RETIRED_CACHE_INVENTORY_EMPTY")
    if observed_ledger_roles != set(exact_ledgers):
        raise ProductionFinalizationError("PRESERVATION_LEDGER_SET_MISMATCH")
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
    for relative in exact_ledgers.values():
        ledger_path = production_root / relative
        if ledger_path.is_symlink() or not ledger_path.is_file():
            raise ProductionFinalizationError("RETAINED_ARTIFACT_MISSING")
        actual_retained.add(relative)
    if actual_retained != listed_retained:
        raise ProductionFinalizationError("UNLISTED_OR_MISSING_RETAINED_ARTIFACT")
    return {"retained_artifacts_reverified": retained, "retired_cache_artifacts": retired}


def accumulate_global_clip_authority(
    manifest_path: Path, *, planned_batch: Mapping[str, Any],
    expected_rows: int, global_clip_keys: set[str],
    global_physical_source_keys: set[str],
    batch_clip_embeddings_sha256: str | None = None,
) -> list[dict[str, Any]]:
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
    records: list[dict[str, Any]] = []
    manifest_sha256 = sha256_file(manifest_path)
    if (
        batch_clip_embeddings_sha256 is not None
        and SHA256_RE.fullmatch(batch_clip_embeddings_sha256) is None
    ):
        raise ProductionFinalizationError("CLIP_EMBEDDINGS_HASH_INVALID")
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
        if (
            not SHA256_RE.fullmatch(clip_key)
            or not SHA256_RE.fullmatch(source_key)
            or not SHA256_RE.fullmatch(row["embedding_sha256"])
        ):
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
        records.append(
            {
                "batch_id": str(planned_batch["batch_id"]),
                "batch_embedding_idx": index,
                "subject_id": row["subject_id"],
                "study_id": row["study_id"],
                "clip_key": clip_key,
                "physical_source_key": source_key,
                "embedding_sha256": row["embedding_sha256"],
                "batch_clip_manifest_sha256": manifest_sha256,
                "batch_clip_embeddings_sha256": batch_clip_embeddings_sha256,
            }
        )
    if indices != set(range(expected_rows)):
        raise ProductionFinalizationError("CLIP_MANIFEST_INDEX_INVALID")
    global_clip_keys.update(local_clips)
    global_physical_source_keys.update(local_sources)
    records.sort(key=lambda row: int(row["batch_embedding_idx"]))
    return records


def _read_closed_csv(
    path: Path, *, expected_header: Sequence[str], code: str
) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise ProductionFinalizationError(f"{code}_NOT_REGULAR")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if (
                header != list(expected_header)
                or len(header) != len(set(header))
            ):
                raise ProductionFinalizationError(f"{code}_SCHEMA_MISMATCH")
            rows = []
            for cells in reader:
                if len(cells) != len(header):
                    raise ProductionFinalizationError(f"{code}_ROW_WIDTH_MISMATCH")
                rows.append(dict(zip(header, cells)))
    except ProductionFinalizationError:
        raise
    except (OSError, UnicodeError, csv.Error, StopIteration) as exc:
        raise ProductionFinalizationError(f"{code}_INVALID") from exc
    return rows


def _stable_nofollow_bytes(
    path: Path, *, code: str, max_bytes: int = 256_000_000
) -> bytes:
    """Capture one retained metadata file through stable bound descriptors."""

    absolute = Path(os.path.abspath(path))
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    def directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def open_parent() -> tuple[int, tuple[int, ...]]:
        descriptor = -1
        try:
            descriptor = os.open(absolute.anchor, directory_flags)
            for component in absolute.parts[1:-1]:
                child = os.open(component, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor, directory_identity(os.fstat(descriptor))
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ProductionFinalizationError(f"{code}_AUTHORITY_INVALID") from exc

    parent_descriptor, parent_before = open_parent()

    def read_once() -> tuple[tuple[int, ...], bytes]:
        descriptor = -1
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(
                absolute.name, flags, dir_fd=parent_descriptor
            )
            before = os.fstat(descriptor)
            visible_before = os.stat(
                absolute.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size < 1
                or before.st_size > max_bytes
                or file_identity(before) != file_identity(visible_before)
            ):
                raise ProductionFinalizationError(
                    f"{code}_AUTHORITY_INVALID"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                block = os.read(
                    descriptor, min(1024 * 1024, max_bytes + 1 - total)
                )
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if total > max_bytes:
                    raise ProductionFinalizationError(
                        f"{code}_AUTHORITY_INVALID"
                    )
            after = os.fstat(descriptor)
            visible_after = os.stat(
                absolute.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            identity = file_identity(before)
            if (
                identity != file_identity(after)
                or identity != file_identity(visible_after)
            ):
                raise ProductionFinalizationError(
                    f"{code}_AUTHORITY_INVALID"
                )
            return identity, b"".join(chunks)
        except ProductionFinalizationError:
            raise
        except OSError as exc:
            raise ProductionFinalizationError(
                f"{code}_AUTHORITY_INVALID"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    try:
        first_identity, payload = read_once()
        second_identity, second_payload = read_once()
        if (
            first_identity != second_identity
            or payload != second_payload
            or parent_before
            != directory_identity(os.fstat(parent_descriptor))
        ):
            raise ProductionFinalizationError(f"{code}_AUTHORITY_INVALID")
        rebound_descriptor, rebound_parent = open_parent()
        os.close(rebound_descriptor)
        if rebound_parent != parent_before:
            raise ProductionFinalizationError(f"{code}_AUTHORITY_INVALID")
        return payload
    finally:
        os.close(parent_descriptor)


def _read_closed_csv_bytes(
    payload: bytes, *, expected_header: Sequence[str], code: str
) -> list[dict[str, str]]:
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8"), newline=""))
        header = next(reader)
        if header != list(expected_header) or len(header) != len(set(header)):
            raise ProductionFinalizationError(f"{code}_SCHEMA_MISMATCH")
        rows = []
        for cells in reader:
            if len(cells) != len(header):
                raise ProductionFinalizationError(f"{code}_ROW_WIDTH_MISMATCH")
            rows.append(dict(zip(header, cells)))
        return rows
    except ProductionFinalizationError:
        raise
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ProductionFinalizationError(f"{code}_INVALID") from exc


def _load_embedding_array(path: Path, *, code: str) -> Any:
    import numpy as np

    if path.is_symlink() or not path.is_file():
        raise ProductionFinalizationError(f"{code}_NOT_REGULAR")
    try:
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != {"embeddings"}:
                raise ProductionFinalizationError(f"{code}_SCHEMA_MISMATCH")
            values = archive["embeddings"].copy()
    except ProductionFinalizationError:
        raise
    except Exception as exc:
        raise ProductionFinalizationError(f"{code}_INVALID") from exc
    if (
        values.dtype != np.dtype("float32")
        or values.ndim != 2
        or values.shape[1] != 512
        or not np.isfinite(values).all()
    ):
        raise ProductionFinalizationError(f"{code}_ARRAY_INVALID")
    return values


def replay_batch_study_embeddings(
    *, clip_manifest_path: Path, clip_embeddings_path: Path,
    study_manifest_path: Path, study_embeddings_path: Path,
    disposition_path: Path, planned_batch: Mapping[str, Any],
    expected_clip_embeddings: int, expected_study_embeddings: int,
    expected_no_cine_studies: int,
) -> list[dict[str, Any]]:
    """Independently replay one retained batch's pooling and dispositions."""
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    clip_rows = _read_closed_csv(
        clip_manifest_path,
        expected_header=CLIP_MANIFEST_HEADER,
        code="FINALIZER_CLIP_MANIFEST",
    )
    study_rows = _read_closed_csv(
        study_manifest_path,
        expected_header=STUDY_MANIFEST_HEADER,
        code="FINALIZER_STUDY_MANIFEST",
    )
    disposition_rows = _read_closed_csv(
        disposition_path,
        expected_header=DISPOSITION_HEADER,
        code="FINALIZER_STUDY_DISPOSITION",
    )
    clip_array = _load_embedding_array(
        clip_embeddings_path, code="FINALIZER_CLIP_EMBEDDINGS"
    )
    study_array = _load_embedding_array(
        study_embeddings_path, code="FINALIZER_STUDY_EMBEDDINGS"
    )
    if (
        len(clip_rows) != expected_clip_embeddings
        or len(clip_array) != expected_clip_embeddings
        or len(study_rows) != expected_study_embeddings
        or len(study_array) != expected_study_embeddings
        or len(disposition_rows) != int(planned_batch.get("n_studies", -1))
    ):
        raise ProductionFinalizationError(
            "FINALIZER_BATCH_EMBEDDING_COUNT_MISMATCH"
        )
    clip_hashes = [smoke.array_content_sha256(row) for row in clip_array]
    study_hashes = [smoke.array_content_sha256(row) for row in study_array]
    try:
        preservation.validate_embedding_array_authority(
            clip_array=clip_array,
            study_array=study_array,
            clip_rows=clip_rows,
            study_rows=study_rows,
            embedding_summary={
                "n_clip_embeddings": expected_clip_embeddings,
                "n_pooled_studies": expected_study_embeddings,
                "embedding_dimension": 512,
                "embedding_dtype": "float32",
                "all_finite": True,
            },
        )
        pooling = preservation.validate_study_pooling_records(
            clip_rows=clip_rows,
            study_rows=study_rows,
            disposition_rows=disposition_rows,
            planned_studies=planned_batch["studies"],
            clip_vector_hashes=clip_hashes,
            study_vector_hashes=study_hashes,
        )
        replayed = preservation.mean_pool_study_embeddings(
            clip_embeddings=clip_array,
            clip_rows=clip_rows,
            study_rows=study_rows,
        )
    except preservation.BatchPreservationError as exc:
        if exc.code.startswith("STUDY_DISPOSITION_"):
            raise ProductionFinalizationError(
                "FINALIZER_NO_CINE_DISPOSITION_MISMATCH"
            ) from exc
        raise ProductionFinalizationError(
            "FINALIZER_BATCH_EMBEDDING_AUTHORITY_INVALID"
        ) from exc
    except (KeyError, TypeError) as exc:
        raise ProductionFinalizationError(
            "FINALIZER_BATCH_EMBEDDING_AUTHORITY_INVALID"
        ) from exc
    if not np.array_equal(replayed, study_array):
        raise ProductionFinalizationError(
            "FINALIZER_STUDY_POOLING_RECOMPUTATION_MISMATCH"
        )
    if (
        pooling["eligible_studies"] != expected_study_embeddings
        or pooling["no_cine_studies"] != expected_no_cine_studies
        or expected_study_embeddings + expected_no_cine_studies
        != int(planned_batch["n_studies"])
    ):
        raise ProductionFinalizationError(
            "FINALIZER_NO_CINE_DISPOSITION_MISMATCH"
        )
    expected_no_cine_keys = list(
        planned_batch.get("prespecified_no_cine_study_keys", ())
    )
    expected_no_cine = {
        (str(row.get("subject_id")), str(row.get("study_id")))
        for row in expected_no_cine_keys
        if isinstance(row, Mapping)
    }
    actual_no_cine = {
        (str(row.get("subject_id")), str(row.get("study_id")))
        for row in disposition_rows
        if row.get("disposition")
        == "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    }
    if (
        expected_no_cine_studies
        != planned_batch.get("expected_no_cine_studies")
        or len(expected_no_cine) != expected_no_cine_studies
        or actual_no_cine != expected_no_cine
        or core.canonical_json_sha256(expected_no_cine_keys)
        != planned_batch.get("prespecified_no_cine_study_set_sha256")
    ):
        raise ProductionFinalizationError(
            "FINALIZER_NO_CINE_IDENTITY_MISMATCH"
        )
    records: list[dict[str, Any]] = []
    for row in study_rows:
        index = int(row["study_idx"])
        records.append(
            {
                "subject_id": str(row["subject_id"]),
                "study_id": str(row["study_id"]),
                "n_clips": int(row["n_clips"]),
                "embedding_sha256": study_hashes[index],
                "embedding": study_array[index].copy(),
            }
        )
    return records


def _require_private_output_root(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionFinalizationError(
            "CANONICAL_STUDY_OUTPUT_ROOT_INVALID"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or not core.owner_private_directory_mode_ok(metadata.st_mode)
    ):
        raise ProductionFinalizationError("CANONICAL_STUDY_OUTPUT_ROOT_INVALID")


def build_plan_ordered_canonical_study_store(
    *, plan: Mapping[str, Any], studies_by_id: Mapping[str, Mapping[str, Any]],
    expected_study_count: int | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Order replayed study vectors by the immutable batch/study plan order."""
    import numpy as np

    expected_count = (
        EXPECTED_IMAGING_ELIGIBLE_STUDIES
        if expected_study_count is None
        else expected_study_count
    )
    ordered_records: list[dict[str, Any]] = []
    ordered_vectors: list[Any] = []
    try:
        for batch in plan["batches"]:
            for planned_study in batch["studies"]:
                study = str(planned_study["study_id"])
                replayed = studies_by_id.get(study)
                if replayed is None:
                    continue
                if (
                    str(replayed["subject_id"])
                    != str(planned_study["subject_id"])
                    or str(replayed["batch_id"]) != str(batch["batch_id"])
                ):
                    raise ProductionFinalizationError(
                        "CANONICAL_STUDY_PLAN_OWNERSHIP_MISMATCH"
                    )
                ordered_records.append(
                    {
                        "study_idx": len(ordered_records),
                        "subject_id": str(replayed["subject_id"]),
                        "study_id": study,
                        "batch_id": str(replayed["batch_id"]),
                        "n_clips": replayed["n_clips"],
                        "embedding_sha256": replayed["embedding_sha256"],
                    }
                )
                ordered_vectors.append(replayed["embedding"])
    except (KeyError, TypeError) as exc:
        raise ProductionFinalizationError(
            "CANONICAL_STUDY_PLAN_AUTHORITY_INVALID"
        ) from exc
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 1
        or len(ordered_records) != expected_count
        or len(ordered_records) != len(studies_by_id)
    ):
        raise ProductionFinalizationError(
            "CANONICAL_STUDY_MEMBERSHIP_COUNT_MISMATCH"
        )
    try:
        array = np.stack(ordered_vectors).astype(np.float32, copy=False)
    except (TypeError, ValueError) as exc:
        raise ProductionFinalizationError("CANONICAL_STUDY_ARRAY_INVALID") from exc
    if (
        array.shape != (expected_count, 512)
        or not np.isfinite(array).all()
    ):
        raise ProductionFinalizationError("CANONICAL_STUDY_ARRAY_INVALID")
    return ordered_records, array


def build_plan_ordered_canonical_clip_index(
    *, plan: Mapping[str, Any], records_by_batch: Mapping[str, Sequence[Mapping[str, Any]]],
    expected_clip_count: int,
) -> list[dict[str, Any]]:
    """Return one plan-ordered index over the retained per-batch clip stores."""

    if (
        isinstance(expected_clip_count, bool)
        or not isinstance(expected_clip_count, int)
        or expected_clip_count < 1
    ):
        raise ProductionFinalizationError("CANONICAL_CLIP_COUNT_INVALID")
    ordered: list[dict[str, Any]] = []
    try:
        planned_batches = list(plan["batches"])
        planned_ids = [str(batch["batch_id"]) for batch in planned_batches]
    except (KeyError, TypeError) as exc:
        raise ProductionFinalizationError(
            "CANONICAL_CLIP_PLAN_AUTHORITY_INVALID"
        ) from exc
    if (
        not planned_ids
        or len(planned_ids) != len(set(planned_ids))
        or any(
            re.fullmatch(r"c3_batch_[0-9]{3}", batch_id) is None
            for batch_id in planned_ids
        )
        or set(records_by_batch) != set(planned_ids)
    ):
        raise ProductionFinalizationError("CANONICAL_CLIP_BATCH_SET_MISMATCH")
    seen_clips: set[str] = set()
    seen_sources: set[str] = set()
    record_keys = set(CANONICAL_CLIP_INDEX_HEADER) - {"clip_idx"}
    for batch, batch_id in zip(planned_batches, planned_ids, strict=True):
        try:
            expected_ownership = {
                str(item["source_object_key"]): (
                    str(item["subject_id"]), str(item["study_id"])
                )
                for item in batch["objects"]
            }
            rows = sorted(
                records_by_batch[batch_id],
                key=lambda row: row["batch_embedding_idx"],
            )
        except (KeyError, TypeError) as exc:
            raise ProductionFinalizationError(
                "CANONICAL_CLIP_PLAN_AUTHORITY_INVALID"
            ) from exc
        if any(
            isinstance(row.get("batch_embedding_idx"), bool)
            or not isinstance(row.get("batch_embedding_idx"), int)
            for row in rows
        ) or [row["batch_embedding_idx"] for row in rows] != list(range(len(rows))):
            raise ProductionFinalizationError("CANONICAL_CLIP_INDEX_INVALID")
        for row in rows:
            subject = str(row.get("subject_id"))
            study = str(row.get("study_id"))
            clip_key = str(row.get("clip_key"))
            source_key = str(row.get("physical_source_key"))
            if clip_key in seen_clips:
                raise ProductionFinalizationError("GLOBAL_CLIP_KEY_COLLISION")
            if source_key in seen_sources:
                raise ProductionFinalizationError("GLOBAL_PHYSICAL_SOURCE_COLLISION")
            if (
                set(row) != record_keys
                or row.get("batch_id") != batch_id
                or expected_ownership.get(source_key) != (subject, study)
                or re.fullmatch(r"[1-9][0-9]*", subject) is None
                or re.fullmatch(r"[1-9][0-9]*", study) is None
                or any(
                    SHA256_RE.fullmatch(str(row.get(key))) is None
                    for key in (
                        "clip_key",
                        "physical_source_key",
                        "embedding_sha256",
                        "batch_clip_manifest_sha256",
                        "batch_clip_embeddings_sha256",
                    )
                )
            ):
                raise ProductionFinalizationError(
                    "CANONICAL_CLIP_PLAN_AUTHORITY_INVALID"
                )
            seen_clips.add(clip_key)
            seen_sources.add(source_key)
            ordered.append({"clip_idx": len(ordered), **dict(row)})
    if len(ordered) != expected_clip_count:
        raise ProductionFinalizationError("CANONICAL_CLIP_COUNT_INVALID")
    return ordered


def _canonical_clip_index_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=CANONICAL_CLIP_INDEX_HEADER, lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    seen_clips: set[str] = set()
    seen_sources: set[str] = set()
    next_batch_index: dict[str, int] = {}
    prior_batch = ""
    for index, row in enumerate(rows):
        batch_id = str(row.get("batch_id"))
        batch_embedding_idx = row.get("batch_embedding_idx")
        subject = str(row.get("subject_id"))
        study = str(row.get("study_id"))
        clip_key = str(row.get("clip_key"))
        source_key = str(row.get("physical_source_key"))
        if (
            set(row) != set(CANONICAL_CLIP_INDEX_HEADER)
            or isinstance(row.get("clip_idx"), bool)
            or row.get("clip_idx") != index
            or re.fullmatch(r"c3_batch_[0-9]{3}", batch_id) is None
            or (prior_batch and batch_id < prior_batch)
            or isinstance(batch_embedding_idx, bool)
            or not isinstance(batch_embedding_idx, int)
            or batch_embedding_idx != next_batch_index.get(batch_id, 0)
            or re.fullmatch(r"[1-9][0-9]*", subject) is None
            or re.fullmatch(r"[1-9][0-9]*", study) is None
            or clip_key in seen_clips
            or source_key in seen_sources
            or any(
                SHA256_RE.fullmatch(str(row.get(key))) is None
                for key in (
                    "clip_key", "physical_source_key", "embedding_sha256",
                    "batch_clip_manifest_sha256", "batch_clip_embeddings_sha256",
                )
            )
        ):
            raise ProductionFinalizationError(
                "CANONICAL_CLIP_INDEX_AUTHORITY_INVALID"
            )
        prior_batch = batch_id
        next_batch_index[batch_id] = batch_embedding_idx + 1
        seen_clips.add(clip_key)
        seen_sources.add(source_key)
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _cohort_artifact_path(artifact_root: Path, relative_path: str) -> Path:
    """Resolve one inventory member without following any symlink component."""

    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ProductionFinalizationError("COHORT_ARTIFACT_AUTHORITY_INVALID")
    try:
        root_metadata = artifact_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionFinalizationError("COHORT_ARTIFACT_ROOT_INVALID") from exc
    if artifact_root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
        raise ProductionFinalizationError("COHORT_ARTIFACT_ROOT_INVALID")
    current = artifact_root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionFinalizationError(
                "COHORT_ARTIFACT_NOT_REGULAR"
            ) from exc
        if current.is_symlink() or (
            index < len(relative.parts) - 1
            and not stat.S_ISDIR(metadata.st_mode)
        ) or (
            index == len(relative.parts) - 1
            and not stat.S_ISREG(metadata.st_mode)
        ):
            raise ProductionFinalizationError("COHORT_ARTIFACT_NOT_REGULAR")
    return current


def _expected_cohort_artifacts(
    *, attempt_id: str, production_batches: int, output_relative: PurePosixPath,
) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    for index in range(production_batches):
        batch_id = f"c3_batch_{index:03d}"
        batch_root = PurePosixPath("attempts") / attempt_id / "batches" / batch_id
        expected.add(
            (
                "batch_final_receipt",
                (batch_root / "preservation" / "batch_finalization_receipt.restricted.json").as_posix(),
            )
        )
        expected.add(
            (
                "batch_clip_embeddings",
                (batch_root / "echoprime" / "clip_embeddings.restricted.npz").as_posix(),
            )
        )
    expected.update(
        {
            (
                "canonical_clip_index",
                (output_relative / CANONICAL_CLIP_INDEX_NAME).as_posix(),
            ),
            (
                "canonical_study_embeddings",
                (output_relative / CANONICAL_STUDY_EMBEDDINGS_NAME).as_posix(),
            ),
            (
                "canonical_study_manifest",
                (output_relative / CANONICAL_STUDY_MANIFEST_NAME).as_posix(),
            ),
            (
                "canonical_study_store",
                (output_relative / CANONICAL_STUDY_RECEIPT_NAME).as_posix(),
            ),
        }
    )
    return expected


def _normalize_cohort_artifacts(
    artifacts: Sequence[Mapping[str, Any]], *, attempt_id: str,
    production_batches: int, output_relative: PurePosixPath,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for item in artifacts:
        if not isinstance(item, Mapping) or set(item) != COHORT_ARTIFACT_KEYS:
            raise ProductionFinalizationError("COHORT_ARTIFACT_SCHEMA_INVALID")
        role = str(item["role"])
        relative = str(item["relative_path"])
        parts = PurePosixPath(relative).parts
        size = item["size_bytes"]
        digest = str(item["sha256"])
        if (
            not parts
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or relative in seen_paths
            or (role, relative) in seen_pairs
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise ProductionFinalizationError("COHORT_ARTIFACT_AUTHORITY_INVALID")
        seen_paths.add(relative)
        seen_pairs.add((role, relative))
        normalized.append(
            {
                "role": role,
                "relative_path": relative,
                "size_bytes": size,
                "sha256": digest,
            }
        )
    if seen_pairs != _expected_cohort_artifacts(
        attempt_id=attempt_id,
        production_batches=production_batches,
        output_relative=output_relative,
    ):
        raise ProductionFinalizationError("COHORT_ARTIFACT_SET_MISMATCH")
    normalized.sort(key=lambda item: (item["role"], item["relative_path"]))
    return normalized


def _validate_cohort_receipt_fields(
    receipt: Mapping[str, Any], *, output_relative: PurePosixPath,
) -> list[dict[str, Any]]:
    if set(receipt) != COHORT_PRESERVATION_RECEIPT_KEYS:
        raise ProductionFinalizationError("COHORT_RECEIPT_SCHEMA_MISMATCH")
    production_batches = receipt.get("production_batches")
    clip_embeddings = receipt.get("clip_embeddings")
    study_embeddings = receipt.get("study_embeddings")
    no_cine_studies = receipt.get("no_cine_studies")
    successful_cines = receipt.get("successfully_extracted_cines")
    dispositions = receipt.get("object_technical_dispositions")
    if (
        receipt.get("schema_version") != 2
        or receipt.get("artifact_type")
        != "lvef_c3_cohort_preservation_receipt_v2"
        or receipt.get("status") != "PASS_COHORT_PRESERVATION"
        or COMMIT_RE.fullmatch(str(receipt.get("governing_commit"))) is None
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
            str(receipt.get("attempt_id")),
        )
        is None
        or SHA256_RE.fullmatch(str(receipt.get("batch_plan_sha256"))) is None
        or SHA256_RE.fullmatch(str(receipt.get("batch_receipt_set_sha256"))) is None
        or isinstance(production_batches, bool)
        or not isinstance(production_batches, int)
        or production_batches < 1
        or production_batches > len(EXPECTED_BATCH_IDS)
        or isinstance(clip_embeddings, bool)
        or not isinstance(clip_embeddings, int)
        or clip_embeddings < 1
        or isinstance(study_embeddings, bool)
        or not isinstance(study_embeddings, int)
        or study_embeddings < 1
        or isinstance(no_cine_studies, bool)
        or not isinstance(no_cine_studies, int)
        or no_cine_studies < 0
        or isinstance(successful_cines, bool)
        or not isinstance(successful_cines, int)
        or successful_cines < 1
        or isinstance(dispositions, bool)
        or not isinstance(dispositions, int)
        or dispositions < 0
        or isinstance(
            receipt.get("studies_affected_by_technical_disposition"), bool
        )
        or not isinstance(
            receipt.get("studies_affected_by_technical_disposition"), int
        )
        or receipt["studies_affected_by_technical_disposition"] < 0
        or receipt["studies_affected_by_technical_disposition"] > dispositions
        or (
            receipt["studies_affected_by_technical_disposition"] == 0
        ) is not (dispositions == 0)
        or receipt.get("clip_embeddings") != successful_cines
        or receipt.get("blocking_failures") != 0
        or receipt.get("new_no_cine_studies") != 0
        or receipt.get("object_substitution_count") != 0
        or receipt.get("unaccounted_multiframe_objects") != 0
        or receipt.get("technical_disposition_policy_version")
        != production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        or SHA256_RE.fullmatch(
            str(receipt.get("technical_disposition_manifest_set_sha256"))
        )
        is None
        or SHA256_RE.fullmatch(
            str(receipt.get("prespecified_no_cine_study_set_sha256"))
        )
        is None
        or receipt.get("technical_disposition_counts_by_class")
        != {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": dispositions
        }
        or receipt.get("all_extraction_rows_resolved") is not True
        or receipt.get("all_successful_extractions_embedded") is not True
        or receipt.get("all_technical_dispositions_retained") is not True
        or receipt.get("all_no_cine_studies_prespecified") is not True
        or receipt.get("second_pass_replay_passed") is not True
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
        or receipt.get("identifiers_emitted") is not False
        or receipt.get("restricted_paths_emitted") is not False
        or not isinstance(receipt.get("artifacts"), list)
    ):
        raise ProductionFinalizationError("COHORT_RECEIPT_AUTHORITY_INVALID")
    expected_output = (
        PurePosixPath("attempts")
        / str(receipt["attempt_id"])
        / "cohort_finalization"
    )
    if output_relative != expected_output:
        raise ProductionFinalizationError("COHORT_OUTPUT_ROOT_MISMATCH")
    return _normalize_cohort_artifacts(
        receipt["artifacts"],
        attempt_id=str(receipt["attempt_id"]),
        production_batches=production_batches,
        output_relative=output_relative,
    )


def _replay_batch_extraction_partition(
    *,
    artifact_root: Path,
    attempt_id: str,
    batch_id: str,
    planned_batch: Mapping[str, Any],
    batch_receipt: Mapping[str, Any],
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    """Rebuild the exact success/disposition partition from retained metadata."""

    extraction_prefix = (
        PurePosixPath("attempts")
        / attempt_id
        / "extracted_cache"
        / batch_id
        / "dicom_extraction"
    )
    try:
        extraction_path = _cohort_artifact_path(
            artifact_root,
            (
                extraction_prefix / "extraction_manifest.restricted.csv"
            ).as_posix(),
        )
        technical_path = _cohort_artifact_path(
            artifact_root,
            (
                extraction_prefix
                / "technical_disposition_manifest.restricted.csv"
            ).as_posix(),
        )
        extraction_payload = _stable_nofollow_bytes(
            extraction_path, code="COHORT_EXTRACTION_MANIFEST"
        )
        if hashlib.sha256(extraction_payload).hexdigest() != str(
            batch_receipt["extraction_manifest_sha256"]
        ):
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
        extraction_rows = _read_closed_csv_bytes(
            extraction_payload,
            expected_header=preservation.EXTRACTION_MANIFEST_HEADER,
            code="COHORT_EXTRACTION_MANIFEST",
        )
        technical_rows, technical_sha = (
            production_stages.read_technical_disposition_manifest_authority(
                technical_path
            )
        )
        if technical_sha != str(
            batch_receipt["technical_disposition_manifest_sha256"]
        ):
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
    except (ProductionFinalizationError, production_stages.ProductionStageError) as exc:
        if (
            isinstance(exc, ProductionFinalizationError)
            and exc.code == "COHORT_EXTRACTION_PARTITION_MISMATCH"
        ):
            raise
        raise ProductionFinalizationError(
            "COHORT_EXTRACTION_PARTITION_MISMATCH"
        ) from exc

    expected_sources = {
        str(row["source_object_key"]): (
            str(row["subject_id"]), str(row["study_id"])
        )
        for row in planned_batch["objects"]
    }
    successful: set[tuple[str, str, str, str]] = set()
    disposed: set[tuple[str, str, str, str]] = set()
    seen_sources: set[str] = set()
    seen_clips: set[str] = set()
    for row in extraction_rows:
        if None in row.values():
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
        source = str(row["physical_source_key"])
        clip = str(row["clip_key"])
        identity = (
            str(row["subject_id"]),
            str(row["study_id"]),
            clip,
            source,
        )
        if (
            source in seen_sources
            or clip in seen_clips
            or expected_sources.get(source) != identity[:2]
            or SHA256_RE.fullmatch(source) is None
            or SHA256_RE.fullmatch(clip) is None
        ):
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
        seen_sources.add(source)
        seen_clips.add(clip)
        if row["write_ok"] == "True":
            if SHA256_RE.fullmatch(str(row["npz_sha256"])) is None:
                raise ProductionFinalizationError(
                    "COHORT_EXTRACTION_PARTITION_MISMATCH"
                )
            successful.add(identity)
        elif (
            row["write_ok"] == "False"
            and row["failure_substage"] == "SOURCE_SIGNAL_QUALITY_FAILURE"
            and row["npz_sha256"] == ""
        ):
            disposed.add(identity)
        else:
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
    technical_identities: set[tuple[str, str, str, str]] = set()
    technical_true_fields = (
        "selected_source_membership_passed",
        "batch_plan_membership_passed",
        "download_integrity_authority_passed",
        "dicom_header_readable",
        "pixel_decode_ok",
        "raw_dicom_retained",
        "npz_absent",
        "embedding_absent",
        "study_retains_valid_cine_coverage",
    )
    for row in technical_rows:
        identity = (
            str(row["subject_id"]),
            str(row["study_id"]),
            str(row["clip_key"]),
            str(row["physical_source_key"]),
        )
        if (
            identity in technical_identities
            or any(row[field] != "True" for field in technical_true_fields)
            or row["object_substitution"] != "False"
            or row["failure_substage"]
            != "SOURCE_SIGNAL_QUALITY_FAILURE"
            or row["technical_disposition"]
            != "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR"
            or row["decode_color_status"] != "PASS"
            or row["canonical_color_space"] != "RGB"
            or row["technical_disposition_policy_version"]
            not in {
                production_stages.LEGACY_OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION,
                production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION,
            }
        ):
            raise ProductionFinalizationError(
                "COHORT_EXTRACTION_PARTITION_MISMATCH"
            )
        technical_identities.add(identity)
    if (
        len(extraction_rows) != int(batch_receipt["n_multiframe_cines"])
        or not seen_sources.issubset(set(expected_sources))
        or successful.intersection(disposed)
        or technical_identities != disposed
        or len(technical_rows) != len(technical_identities)
        or len(successful)
        != int(batch_receipt["n_successfully_extracted_cines"])
        or len(disposed)
        != int(batch_receipt["n_object_technical_dispositions"])
    ):
        raise ProductionFinalizationError(
            "COHORT_EXTRACTION_PARTITION_MISMATCH"
        )
    return successful, disposed


def _replay_cohort_artifact_inventory(
    receipt: Mapping[str, Any], *, artifact_root: Path, output_root: Path,
) -> dict[str, Any]:
    """Rehash the exact inventory and replay all clip/store/study bindings."""

    import lvef_reconstruction_smoke as smoke

    try:
        output_relative = PurePosixPath(output_root.relative_to(artifact_root).as_posix())
    except ValueError as exc:
        raise ProductionFinalizationError("COHORT_OUTPUT_ROOT_MISMATCH") from exc
    artifacts = _validate_cohort_receipt_fields(
        receipt, output_relative=output_relative
    )
    by_role_and_path = {
        (item["role"], item["relative_path"]): item for item in artifacts
    }
    for item in artifacts:
        artifact = _cohort_artifact_path(artifact_root, item["relative_path"])
        metadata = artifact.stat(follow_symlinks=False)
        if metadata.st_size != item["size_bytes"] or sha256_file(artifact) != item["sha256"]:
            raise ProductionFinalizationError("COHORT_ARTIFACT_SECOND_PASS_MISMATCH")

    attempt_id = str(receipt["attempt_id"])
    production_batches = int(receipt["production_batches"])
    output_prefix = PurePosixPath(output_relative)
    plan_path = (
        artifact_root / "attempts" / attempt_id
        / "full_batch_plan.restricted.json"
    )
    if plan_path.is_symlink() or not plan_path.is_file():
        raise ProductionFinalizationError("COHORT_BATCH_PLAN_NOT_REGULAR")
    plan = load_json(plan_path, "COHORT_BATCH_PLAN")
    cohort = plan.get("cohort")
    batches = plan.get("batches")
    if not isinstance(cohort, Mapping) or not isinstance(batches, list):
        raise ProductionFinalizationError("COHORT_BATCH_PLAN_INVALID")
    try:
        plan_requirements = core.PlanRequirements(
            release=str(cohort["release"]),
            selected_studies=int(cohort["selected_studies"]),
            selected_subjects=int(cohort["selected_subjects"]),
            normalized_source_objects=int(cohort["normalized_source_objects"]),
            selected_source_bytes=int(cohort["selected_source_bytes"]),
            batch_count=len(batches),
            studies_per_full_batch=int(batches[0]["n_studies"]),
            final_batch_studies=int(batches[-1]["n_studies"]),
            contract_id=str(plan["contract_id"]),
        )
        observed_plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=plan_requirements
        )
    except (KeyError, TypeError, ValueError, core.OrchestrationError) as exc:
        raise ProductionFinalizationError("COHORT_BATCH_PLAN_INVALID") from exc
    if (
        production_batches != len(batches)
        or observed_plan_sha != receipt["batch_plan_sha256"]
        or sha256_file(plan_path) != receipt["batch_plan_sha256"]
        or cohort.get("prespecified_no_cine_study_set_sha256")
        != receipt.get("prespecified_no_cine_study_set_sha256")
        or cohort.get("expected_no_cine_studies")
        != receipt.get("no_cine_studies")
    ):
        raise ProductionFinalizationError("COHORT_NO_CINE_PLAN_BINDING_MISMATCH")
    planned_by_batch = {str(item["batch_id"]): item for item in batches}
    batch_receipts: dict[str, Mapping[str, Any]] = {}
    receipt_hashes: list[str] = []
    for index in range(production_batches):
        batch_id = f"c3_batch_{index:03d}"
        batch_prefix = PurePosixPath("attempts") / attempt_id / "batches" / batch_id
        receipt_relative = (
            batch_prefix / "preservation" / "batch_finalization_receipt.restricted.json"
        ).as_posix()
        item = by_role_and_path[("batch_final_receipt", receipt_relative)]
        batch_receipt = load_json(
            _cohort_artifact_path(artifact_root, receipt_relative),
            "COHORT_BATCH_RECEIPT",
        )
        _validate_current_receipt_v3(batch_receipt)
        if (
            batch_receipt.get("batch_id") != batch_id
            or batch_receipt.get("attempt_id") != attempt_id
            or batch_receipt.get("governing_commit") != receipt["governing_commit"]
            or batch_receipt.get("batch_plan_sha256") != receipt["batch_plan_sha256"]
            or batch_receipt.get("prespecified_no_cine_study_set_sha256")
            != planned_by_batch[batch_id][
                "prespecified_no_cine_study_set_sha256"
            ]
            or batch_receipt.get("n_no_cine_studies")
            != planned_by_batch[batch_id]["expected_no_cine_studies"]
        ):
            raise ProductionFinalizationError("COHORT_BATCH_RECEIPT_BINDING_MISMATCH")
        batch_receipts[batch_id] = batch_receipt
        receipt_hashes.append(str(item["sha256"]))
    receipt_set_hash = hashlib.sha256(
        "\n".join(sorted(receipt_hashes)).encode("ascii") + b"\n"
    ).hexdigest()
    if receipt_set_hash != receipt["batch_receipt_set_sha256"]:
        raise ProductionFinalizationError("COHORT_BATCH_RECEIPT_SET_MISMATCH")
    def batch_total(key: str) -> int:
        return sum(int(item[key]) for item in batch_receipts.values())

    technical_set_hash = hashlib.sha256(
        (
            "\n".join(
                sorted(
                    str(item["technical_disposition_manifest_sha256"])
                    for item in batch_receipts.values()
                )
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()
    disposition_total = batch_total("n_object_technical_dispositions")
    if (
        receipt.get("clip_embeddings") != batch_total("n_clip_embeddings")
        or receipt.get("successfully_extracted_cines")
        != batch_total("n_successfully_extracted_cines")
        or receipt.get("object_technical_dispositions") != disposition_total
        or receipt.get("studies_affected_by_technical_disposition")
        != batch_total("n_studies_affected_by_technical_disposition")
        or receipt.get("new_no_cine_studies") != batch_total("n_new_no_cine_studies")
        or receipt.get("object_substitution_count")
        != batch_total("object_substitution_count")
        or receipt.get("unaccounted_multiframe_objects")
        != batch_total("unaccounted_multiframe_objects")
        or receipt.get("technical_disposition_counts_by_class")
        != {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": disposition_total
        }
        or receipt.get("technical_disposition_manifest_set_sha256")
        != technical_set_hash
    ):
        raise ProductionFinalizationError(
            "COHORT_TECHNICAL_DISPOSITION_RECONCILIATION_MISMATCH"
        )
    if (
        sum(int(item["n_clip_embeddings"]) for item in batch_receipts.values())
        != receipt["clip_embeddings"]
        or sum(int(item["n_pooled_studies"]) for item in batch_receipts.values())
        != receipt["study_embeddings"]
        or sum(int(item["n_no_cine_studies"]) for item in batch_receipts.values())
        != receipt["no_cine_studies"]
    ):
        raise ProductionFinalizationError("COHORT_BATCH_COUNT_BINDING_MISMATCH")

    clip_relative = (output_prefix / CANONICAL_CLIP_INDEX_NAME).as_posix()
    clip_path = _cohort_artifact_path(artifact_root, clip_relative)
    clip_rows_raw = _read_closed_csv(
        clip_path,
        expected_header=CANONICAL_CLIP_INDEX_HEADER,
        code="COHORT_CLIP_INDEX",
    )
    try:
        clip_rows = [
            {
                **row,
                "clip_idx": int(row["clip_idx"]),
                "batch_embedding_idx": int(row["batch_embedding_idx"]),
            }
            for row in clip_rows_raw
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ProductionFinalizationError("CANONICAL_CLIP_INDEX_AUTHORITY_INVALID") from exc
    if (
        len(clip_rows) != receipt["clip_embeddings"]
        or _canonical_clip_index_bytes(clip_rows) != clip_path.read_bytes()
    ):
        raise ProductionFinalizationError("CANONICAL_CLIP_INDEX_AUTHORITY_INVALID")

    rows_by_batch: dict[str, list[Mapping[str, Any]]] = {
        f"c3_batch_{index:03d}": [] for index in range(production_batches)
    }
    for row in clip_rows:
        batch_id = str(row["batch_id"])
        if batch_id not in rows_by_batch:
            raise ProductionFinalizationError("CANONICAL_CLIP_BATCH_SET_MISMATCH")
        rows_by_batch[batch_id].append(row)
    replayed_clip_keys: set[str] = set()
    replayed_source_keys: set[str] = set()
    for batch_id, rows in rows_by_batch.items():
        batch_prefix = PurePosixPath("attempts") / attempt_id / "batches" / batch_id
        store_relative = (
            batch_prefix / "echoprime" / "clip_embeddings.restricted.npz"
        ).as_posix()
        store_item = by_role_and_path[("batch_clip_embeddings", store_relative)]
        batch_receipt = batch_receipts[batch_id]
        batch_clip_manifest = _cohort_artifact_path(
            artifact_root,
            (batch_prefix / "echoprime" / "clip_manifest.restricted.csv").as_posix(),
        )
        expected_rows = accumulate_global_clip_authority(
            batch_clip_manifest,
            planned_batch=planned_by_batch[batch_id],
            expected_rows=int(batch_receipt["n_clip_embeddings"]),
            global_clip_keys=replayed_clip_keys,
            global_physical_source_keys=replayed_source_keys,
            batch_clip_embeddings_sha256=str(store_item["sha256"]),
        )
        successful_extractions, disposed_extractions = (
            _replay_batch_extraction_partition(
                artifact_root=artifact_root,
                attempt_id=attempt_id,
                batch_id=batch_id,
                planned_batch=planned_by_batch[batch_id],
                batch_receipt=batch_receipt,
            )
        )
        embedded_identities = {
            (
                str(row["subject_id"]),
                str(row["study_id"]),
                str(row["clip_key"]),
                str(row["physical_source_key"]),
            )
            for row in expected_rows
        }
        canonical_identity = [
            (
                str(row["batch_id"]),
                int(row["batch_embedding_idx"]),
                str(row["subject_id"]),
                str(row["study_id"]),
                str(row["clip_key"]),
                str(row["physical_source_key"]),
                str(row["embedding_sha256"]),
                str(row["batch_clip_manifest_sha256"]),
                str(row["batch_clip_embeddings_sha256"]),
            )
            for row in rows
        ]
        replayed_identity = [
            (
                str(row["batch_id"]),
                int(row["batch_embedding_idx"]),
                str(row["subject_id"]),
                str(row["study_id"]),
                str(row["clip_key"]),
                str(row["physical_source_key"]),
                str(row["embedding_sha256"]),
                str(row["batch_clip_manifest_sha256"]),
                str(row["batch_clip_embeddings_sha256"]),
            )
            for row in expected_rows
        ]
        if (
            len(rows) != batch_receipt["n_clip_embeddings"]
            or len(rows) != batch_receipt["n_unique_clip_keys"]
            or store_item["sha256"] != batch_receipt["clip_embeddings_sha256"]
            or sha256_file(batch_clip_manifest)
            != batch_receipt["clip_manifest_sha256"]
            or embedded_identities != successful_extractions
            or embedded_identities.intersection(disposed_extractions)
            or canonical_identity != replayed_identity
            or any(
                row["batch_clip_embeddings_sha256"] != store_item["sha256"]
                or row["batch_clip_manifest_sha256"]
                != batch_receipt["clip_manifest_sha256"]
                for row in rows
            )
        ):
            raise ProductionFinalizationError("COHORT_CLIP_STORE_BINDING_MISMATCH")
        clip_array = _load_embedding_array(
            _cohort_artifact_path(artifact_root, store_relative),
            code="COHORT_BATCH_CLIP_EMBEDDINGS",
        )
        if len(clip_array) != len(rows) or any(
            row["batch_embedding_idx"] != index
            or row["embedding_sha256"] != smoke.array_content_sha256(clip_array[index])
            for index, row in enumerate(rows)
        ):
            raise ProductionFinalizationError("COHORT_CLIP_VECTOR_BINDING_MISMATCH")

    study_store_relative = (output_prefix / CANONICAL_STUDY_RECEIPT_NAME).as_posix()
    study_embeddings_relative = (
        output_prefix / CANONICAL_STUDY_EMBEDDINGS_NAME
    ).as_posix()
    study_manifest_relative = (output_prefix / CANONICAL_STUDY_MANIFEST_NAME).as_posix()
    study_store = load_json(
        _cohort_artifact_path(artifact_root, study_store_relative),
        "COHORT_CANONICAL_STUDY_RECEIPT",
    )
    if (
        set(study_store) != CANONICAL_STUDY_RECEIPT_KEYS
        or study_store.get("schema_version") != 1
        or study_store.get("artifact_type")
        != "lvef_c3_canonical_study_embedding_store_receipt_v1"
        or study_store.get("status") != "PASS_CANONICAL_STUDY_EMBEDDING_STORE"
        or study_store.get("governing_commit") != receipt["governing_commit"]
        or study_store.get("attempt_id") != attempt_id
        or study_store.get("batch_plan_sha256") != receipt["batch_plan_sha256"]
        or study_store.get("batch_receipt_set_sha256")
        != receipt["batch_receipt_set_sha256"]
        or study_store.get("study_embeddings") != receipt["study_embeddings"]
        or study_store.get("no_cine_studies") != receipt["no_cine_studies"]
        or study_store.get("exact_pooling_replay_passed") is not True
        or study_store.get("stable_plan_order") is not True
        or study_store.get("duplicate_study_keys") != 0
        or study_store.get("identifiers_emitted") is not False
        or study_store.get("restricted_paths_emitted") is not False
    ):
        raise ProductionFinalizationError("COHORT_STUDY_STORE_BINDING_MISMATCH")
    study_embeddings_item = by_role_and_path[
        ("canonical_study_embeddings", study_embeddings_relative)
    ]
    study_manifest_item = by_role_and_path[
        ("canonical_study_manifest", study_manifest_relative)
    ]
    if (
        study_store.get("study_embeddings_sha256") != study_embeddings_item["sha256"]
        or study_store.get("study_embeddings_size_bytes")
        != study_embeddings_item["size_bytes"]
        or study_store.get("study_manifest_sha256") != study_manifest_item["sha256"]
        or study_store.get("study_manifest_size_bytes")
        != study_manifest_item["size_bytes"]
    ):
        raise ProductionFinalizationError("COHORT_STUDY_STORE_BINDING_MISMATCH")
    study_array = _load_embedding_array(
        _cohort_artifact_path(artifact_root, study_embeddings_relative),
        code="COHORT_CANONICAL_STUDY_EMBEDDINGS",
    )
    study_rows = _read_closed_csv(
        _cohort_artifact_path(artifact_root, study_manifest_relative),
        expected_header=CANONICAL_STUDY_MANIFEST_HEADER,
        code="COHORT_CANONICAL_STUDY_MANIFEST",
    )
    planned_no_cine_keys = sorted(
        [
            {
                "subject_id": str(row["subject_id"]),
                "study_id": str(row["study_id"]),
            }
            for batch in batches
            for row in batch["prespecified_no_cine_study_keys"]
        ],
        key=lambda row: (int(row["subject_id"]), int(row["study_id"])),
    )
    planned_no_cine_pairs = {
        (row["subject_id"], row["study_id"]) for row in planned_no_cine_keys
    }
    expected_study_membership = [
        (
            str(index),
            str(study["subject_id"]),
            str(study["study_id"]),
            str(batch["batch_id"]),
        )
        for index, (batch, study) in enumerate(
            (
                (batch, study)
                for batch in batches
                for study in batch["studies"]
                if (str(study["subject_id"]), str(study["study_id"]))
                not in planned_no_cine_pairs
            )
        )
    ]
    actual_study_membership = [
        (
            str(row.get("study_idx")),
            str(row.get("subject_id")),
            str(row.get("study_id")),
            str(row.get("batch_id")),
        )
        for row in study_rows
    ]
    if (
        len(study_array) != receipt["study_embeddings"]
        or len(study_rows) != receipt["study_embeddings"]
        or actual_study_membership != expected_study_membership
        or len(planned_no_cine_pairs) != receipt["no_cine_studies"]
        or len(expected_study_membership) != receipt["study_embeddings"]
        or core.canonical_json_sha256(planned_no_cine_keys)
        != receipt["prespecified_no_cine_study_set_sha256"]
        or any(
            row.get("study_idx") != str(index)
            or not str(row.get("n_clips", "")).isdigit()
            or int(str(row["n_clips"])) < 1
            or row.get("embedding_sha256") != smoke.array_content_sha256(study_array[index])
            for index, row in enumerate(study_rows)
        )
    ):
        raise ProductionFinalizationError("COHORT_STUDY_VECTOR_BINDING_MISMATCH")
    return {
        "artifacts": artifacts,
        "clip_index_rows": len(clip_rows),
        "batch_receipt_set_sha256": receipt_set_hash,
    }


def replay_cohort_preservation_receipt(
    receipt_path: Path, *, artifact_root: Path,
) -> Mapping[str, Any]:
    """Independently replay one already-published cohort receipt."""

    receipt = load_json(receipt_path, "COHORT_PRESERVATION_RECEIPT")
    if receipt_path.name != COHORT_PRESERVATION_RECEIPT_NAME:
        raise ProductionFinalizationError("COHORT_RECEIPT_PATH_INVALID")
    _replay_cohort_artifact_inventory(
        receipt, artifact_root=artifact_root, output_root=receipt_path.parent
    )
    return receipt


def write_cohort_preservation_outputs(
    *, output_root: Path, artifact_root: Path,
    clip_index_rows: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]], governing_commit: str,
    attempt_id: str, batch_plan_sha256: str, batch_receipt_set_sha256: str,
    production_batches: int, study_embeddings: int, no_cine_studies: int,
    successfully_extracted_cines: int,
    object_technical_dispositions: int,
    studies_affected_by_technical_disposition: int,
    technical_disposition_manifest_set_sha256: str,
    prespecified_no_cine_study_set_sha256: str,
) -> dict[str, Any]:
    """Publish the cohort clip index, replay it, then publish one receipt last."""

    _require_private_output_root(output_root)
    _require_private_output_root(artifact_root)
    clip_path = output_root / CANONICAL_CLIP_INDEX_NAME
    receipt_path = output_root / COHORT_PRESERVATION_RECEIPT_NAME
    if any(os.path.lexists(path) for path in (clip_path, receipt_path)):
        raise ProductionFinalizationError("COHORT_PRESERVATION_OUTPUT_EXISTS")
    try:
        output_relative = PurePosixPath(output_root.relative_to(artifact_root).as_posix())
    except ValueError as exc:
        raise ProductionFinalizationError("COHORT_OUTPUT_ROOT_MISMATCH") from exc
    clip_body = _canonical_clip_index_bytes(clip_index_rows)
    clip_artifact = {
        "role": "canonical_clip_index",
        "relative_path": (output_relative / CANONICAL_CLIP_INDEX_NAME).as_posix(),
        "size_bytes": len(clip_body),
        "sha256": hashlib.sha256(clip_body).hexdigest(),
    }
    normalized = _normalize_cohort_artifacts(
        [*artifacts, clip_artifact],
        attempt_id=attempt_id,
        production_batches=production_batches,
        output_relative=output_relative,
    )
    receipt = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_cohort_preservation_receipt_v2",
        "status": "PASS_COHORT_PRESERVATION",
        "governing_commit": governing_commit,
        "attempt_id": attempt_id,
        "batch_plan_sha256": batch_plan_sha256,
        "batch_receipt_set_sha256": batch_receipt_set_sha256,
        "production_batches": production_batches,
        "clip_embeddings": len(clip_index_rows),
        "study_embeddings": study_embeddings,
        "no_cine_studies": no_cine_studies,
        "successfully_extracted_cines": successfully_extracted_cines,
        "object_technical_dispositions": object_technical_dispositions,
        "blocking_failures": 0,
        "studies_affected_by_technical_disposition": (
            studies_affected_by_technical_disposition
        ),
        "new_no_cine_studies": 0,
        "technical_disposition_counts_by_class": {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": (
                object_technical_dispositions
            )
        },
        "technical_disposition_policy_version": (
            production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "technical_disposition_manifest_set_sha256": (
            technical_disposition_manifest_set_sha256
        ),
        "prespecified_no_cine_study_set_sha256": (
            prespecified_no_cine_study_set_sha256
        ),
        "all_no_cine_studies_prespecified": True,
        "all_extraction_rows_resolved": True,
        "all_successful_extractions_embedded": True,
        "all_technical_dispositions_retained": True,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "artifacts": normalized,
        "second_pass_replay_passed": True,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    _validate_cohort_receipt_fields(receipt, output_relative=output_relative)
    for item in normalized:
        if item["role"] == "canonical_clip_index":
            continue
        artifact = _cohort_artifact_path(artifact_root, item["relative_path"])
        if (
            artifact.stat(follow_symlinks=False).st_size != item["size_bytes"]
            or sha256_file(artifact) != item["sha256"]
        ):
            raise ProductionFinalizationError("COHORT_ARTIFACT_SECOND_PASS_MISMATCH")

    _write_bytes_atomic_no_clobber(
        clip_path, clip_body, exists_code="COHORT_PRESERVATION_OUTPUT_EXISTS"
    )
    # The terminal receipt is deliberately absent until every retained artifact,
    # including the newly published index and every referenced vector, replays.
    _replay_cohort_artifact_inventory(
        receipt, artifact_root=artifact_root, output_root=output_root
    )
    receipt_body = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes_atomic_no_clobber(
        receipt_path,
        receipt_body,
        exists_code="COHORT_PRESERVATION_OUTPUT_EXISTS",
    )
    if replay_cohort_preservation_receipt(
        receipt_path, artifact_root=artifact_root
    ) != receipt:
        raise ProductionFinalizationError("COHORT_ARTIFACT_SECOND_PASS_MISMATCH")
    return receipt


def _write_bytes_atomic_no_clobber(
    path: Path, body: bytes, *,
    exists_code: str = "CANONICAL_STUDY_OUTPUT_ALREADY_EXISTS",
) -> None:
    _require_private_output_root(path.parent)
    if path.exists() or path.is_symlink():
        raise ProductionFinalizationError(exists_code)
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created_temporary = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        created_temporary = True
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as exc:
        if created_temporary and temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise ProductionFinalizationError(exists_code) from exc
    except Exception:
        if created_temporary and temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise


def write_canonical_study_store(
    *, output_root: Path, records: Sequence[Mapping[str, Any]],
    embeddings: Any, governing_commit: str, attempt_id: str,
    batch_plan_sha256: str, batch_receipt_set_sha256: str,
    no_cine_studies: int, expected_study_count: int | None = None,
) -> dict[str, Any]:
    """Write one no-clobber plan-ordered canonical store and binding receipt."""
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    expected_count = (
        EXPECTED_IMAGING_ELIGIBLE_STUDIES
        if expected_study_count is None
        else expected_study_count
    )
    _require_private_output_root(output_root)
    targets = (
        output_root / CANONICAL_STUDY_EMBEDDINGS_NAME,
        output_root / CANONICAL_STUDY_MANIFEST_NAME,
        output_root / CANONICAL_STUDY_RECEIPT_NAME,
    )
    if any(path.exists() or path.is_symlink() for path in targets):
        raise ProductionFinalizationError("CANONICAL_STUDY_OUTPUT_ALREADY_EXISTS")
    if (
        not COMMIT_RE.fullmatch(governing_commit)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", attempt_id)
        or not SHA256_RE.fullmatch(batch_plan_sha256)
        or not SHA256_RE.fullmatch(batch_receipt_set_sha256)
        or isinstance(no_cine_studies, bool)
        or not isinstance(no_cine_studies, int)
        or no_cine_studies < 0
        or isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 1
    ):
        raise ProductionFinalizationError(
            "CANONICAL_STUDY_RECEIPT_AUTHORITY_INVALID"
        )
    if (
        not isinstance(embeddings, np.ndarray)
        or embeddings.dtype != np.dtype("float32")
        or embeddings.shape != (len(records), 512)
        or not np.isfinite(embeddings).all()
        or len(records) != expected_count
    ):
        raise ProductionFinalizationError("CANONICAL_STUDY_ARRAY_INVALID")
    seen_studies: set[str] = set()
    seen_subjects: set[str] = set()
    manifest_rows: list[list[Any]] = []
    for index, (record, vector) in enumerate(zip(records, embeddings, strict=True)):
        study = str(record.get("study_id"))
        subject = str(record.get("subject_id"))
        if (
            study in seen_studies
            or subject in seen_subjects
            or not re.fullmatch(r"[1-9][0-9]*", study)
            or not re.fullmatch(r"[1-9][0-9]*", subject)
            or str(int(study)) != study
            or str(int(subject)) != subject
            or str(record.get("batch_id")) not in EXPECTED_BATCH_IDS
            or record.get("study_idx") != index
            or not str(record.get("n_clips", "")).isdigit()
            or int(record["n_clips"]) < 1
            or not SHA256_RE.fullmatch(str(record.get("embedding_sha256")))
            or record["embedding_sha256"]
            != smoke.array_content_sha256(vector)
        ):
            raise ProductionFinalizationError(
                "CANONICAL_STUDY_MANIFEST_AUTHORITY_INVALID"
            )
        seen_studies.add(study)
        seen_subjects.add(subject)
        manifest_rows.append(
            [
                index, subject, study, str(record.get("batch_id")),
                int(record["n_clips"]), record["embedding_sha256"],
            ]
        )
    npz_buffer = io.BytesIO()
    np.savez_compressed(npz_buffer, embeddings=embeddings)
    manifest_buffer = io.StringIO(newline="")
    writer = csv.writer(manifest_buffer, lineterminator="\n")
    writer.writerow(CANONICAL_STUDY_MANIFEST_HEADER)
    writer.writerows(manifest_rows)
    embedding_body = npz_buffer.getvalue()
    manifest_body = manifest_buffer.getvalue().encode("utf-8")
    embedding_sha = hashlib.sha256(embedding_body).hexdigest()
    manifest_sha = hashlib.sha256(manifest_body).hexdigest()
    receipt = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canonical_study_embedding_store_receipt_v1",
        "status": "PASS_CANONICAL_STUDY_EMBEDDING_STORE",
        "governing_commit": governing_commit,
        "attempt_id": attempt_id,
        "batch_plan_sha256": batch_plan_sha256,
        "batch_receipt_set_sha256": batch_receipt_set_sha256,
        "study_embeddings": len(records),
        "embedding_dimension": 512,
        "embedding_dtype": "float32",
        "study_embeddings_sha256": embedding_sha,
        "study_embeddings_size_bytes": len(embedding_body),
        "study_manifest_sha256": manifest_sha,
        "study_manifest_size_bytes": len(manifest_body),
        "pooling": "stable_clip_key_order_float64_mean_then_float32",
        "exact_pooling_replay_passed": True,
        "stable_plan_order": True,
        "duplicate_study_keys": 0,
        "no_cine_studies": no_cine_studies,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    receipt_body = (
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic_no_clobber(targets[0], embedding_body)
    _write_bytes_atomic_no_clobber(targets[1], manifest_body)
    _write_bytes_atomic_no_clobber(targets[2], receipt_body)
    for target, expected_body in zip(
        targets, (embedding_body, manifest_body, receipt_body), strict=True
    ):
        metadata = target.stat(follow_symlinks=False)
        if (
            target.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or target.read_bytes() != expected_body
        ):
            raise ProductionFinalizationError(
                "CANONICAL_STUDY_SECOND_PASS_MISMATCH"
            )
    with np.load(targets[0], allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"} or not np.array_equal(
            archive["embeddings"], embeddings
        ):
            raise ProductionFinalizationError(
                "CANONICAL_STUDY_SECOND_PASS_MISMATCH"
            )
    if load_json(targets[2], "CANONICAL_STUDY_RECEIPT") != receipt:
        raise ProductionFinalizationError("CANONICAL_STUDY_SECOND_PASS_MISMATCH")
    return receipt


def _validate_legacy_receipt_v2(value: Mapping[str, Any]) -> None:
    if set(value) != LEGACY_BATCH_RECEIPT_KEYS_V2:
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
    for key in LEGACY_HASH_KEYS_V2:
        if not SHA256_RE.fullmatch(str(value.get(key))):
            raise ProductionFinalizationError("BATCH_HASH_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))):
        raise ProductionFinalizationError("SCHEDULER_IDENTITY_INVALID")
    for key in LEGACY_COUNT_KEYS_V2:
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
            raise ProductionFinalizationError("BATCH_COUNT_NOT_INTEGER")
        if value[key] < 0:
            raise ProductionFinalizationError("BATCH_COUNT_NEGATIVE")
    for key in LEGACY_TRUE_GATE_KEYS_V2:
        if value.get(key) is not True:
            raise ProductionFinalizationError("BATCH_GATE_FAILED")
    for key in LEGACY_ZERO_KEYS_V2:
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


def _validate_current_receipt_v3(value: Mapping[str, Any]) -> None:
    if set(value) != BATCH_RECEIPT_KEYS:
        raise ProductionFinalizationError("BATCH_RECEIPT_SCHEMA_MISMATCH")
    if (
        value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_batch_finalization_receipt_v3"
    ):
        raise ProductionFinalizationError("BATCH_RECEIPT_VERSION_MISMATCH")
    if value.get("status") != "PASS_BATCH_FINALIZED":
        raise ProductionFinalizationError("BATCH_NOT_FINALIZED")
    if value.get("batch_id") not in EXPECTED_BATCH_IDS:
        raise ProductionFinalizationError("BATCH_ID_INVALID")
    if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
        raise ProductionFinalizationError("ATTEMPT_ID_INVALID")
    if (
        not COMMIT_RE.fullmatch(str(value.get("governing_commit")))
        or value.get("source_commit") != value.get("governing_commit")
        or not TIMESTAMP_RE.fullmatch(str(value.get("run_timestamp_utc")))
    ):
        raise ProductionFinalizationError("BATCH_PROVENANCE_VALUE_INVALID")
    if (
        value.get("cohort_version") != "mimic-iv-echo/1.0"
        or not re.fullmatch(
            r"split_map_sha256:[0-9a-f]{64}", str(value.get("split_version"))
        )
        or value.get("execution_contract_version") != 2
        or value.get("aggregate_safety_gate_result") != "PASS"
        or value.get("checkpoint_checksum") != value.get("checkpoint_sha256")
        or value.get("technical_disposition_policy_version")
        != production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
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
    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))
    ):
        raise ProductionFinalizationError("SCHEDULER_IDENTITY_INVALID")
    for key in COUNT_KEYS:
        if (
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            or value[key] < 0
        ):
            raise ProductionFinalizationError("BATCH_COUNT_INVALID")
    for key in TRUE_GATE_KEYS:
        if value.get(key) is not True:
            raise ProductionFinalizationError("BATCH_GATE_FAILED")
    for key in ZERO_KEYS:
        if value[key] != 0:
            raise ProductionFinalizationError("SCIENTIFIC_INCONSISTENCY")
    expected_counts = {
        "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": value[
            "n_object_technical_dispositions"
        ]
    }
    if value.get("technical_disposition_counts_by_class") != expected_counts:
        raise ProductionFinalizationError(
            "TECHNICAL_DISPOSITION_COUNT_MAP_INVALID"
        )
    dispositions = value["n_object_technical_dispositions"]
    affected_studies = value["n_studies_affected_by_technical_disposition"]
    if (
        affected_studies > dispositions
        or (affected_studies == 0) is not (dispositions == 0)
    ):
        raise ProductionFinalizationError(
            "TECHNICAL_DISPOSITION_STUDY_COUNT_INVALID"
        )
    if value.get("extracted_cache_retired") is not True:
        raise ProductionFinalizationError("CACHE_RETIREMENT_NOT_COMPLETE")
    if value["n_download_verified"] != value["n_expected_objects"]:
        raise ProductionFinalizationError("DOWNLOAD_COUNT_MISMATCH")
    if (
        value["n_dicom_readable"] + value["n_dicom_unreadable"]
        != value["n_expected_objects"]
        or value["n_multiframe_cines"] + value["n_single_frame_objects"]
        != value["n_dicom_readable"]
    ):
        raise ProductionFinalizationError("DICOM_COUNT_MISMATCH")
    if (
        value["n_multiframe_cines"]
        != value["n_successfully_extracted_cines"]
        + value["n_object_technical_dispositions"]
        or value["n_successfully_extracted_cines"]
        != value["n_extracted_clips"]
        or value["n_successfully_extracted_cines"]
        != value["n_unique_clip_keys"]
        or value["n_successfully_extracted_cines"]
        != value["n_clip_embeddings"]
    ):
        raise ProductionFinalizationError("CLIP_ACCOUNTING_MISMATCH")
    if (
        value["n_pooled_studies"] + value["n_no_cine_studies"]
        != value["n_selected_studies"]
    ):
        raise ProductionFinalizationError("STUDY_POOLING_ACCOUNTING_MISMATCH")
    if value.get("no_cine_disposition") != (
        "NONE"
        if value["n_no_cine_studies"] == 0
        else "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    ):
        raise ProductionFinalizationError("NO_CINE_DISPOSITION_MISMATCH")


def _validate_receipt(value: Mapping[str, Any]) -> None:
    identity = (value.get("schema_version"), value.get("artifact_type"))
    if identity == (1, "lvef_c3_batch_finalization_receipt_v2"):
        _validate_legacy_receipt_v2(value)
    elif identity == (2, "lvef_c3_batch_finalization_receipt_v3"):
        _validate_current_receipt_v3(value)
    else:
        raise ProductionFinalizationError("BATCH_RECEIPT_VERSION_MISMATCH")


def _validate_legacy_canary_eligibility_receipt_v2(
    value: Mapping[str, Any]
) -> None:
    """Validate the retained-cache receipt subset used by a bounded canary."""
    if set(value) != LEGACY_PRESERVATION_ELIGIBILITY_RECEIPT_KEYS_V2:
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
    for key in LEGACY_HASH_KEYS_V2 - RETIREMENT_RECEIPT_KEYS:
        if not SHA256_RE.fullmatch(str(value.get(key))):
            raise ProductionFinalizationError("CANARY_HASH_INVALID")
    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))
    ):
        raise ProductionFinalizationError("CANARY_SCHEDULER_IDENTITY_INVALID")
    for key in LEGACY_COUNT_KEYS_V2:
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
            raise ProductionFinalizationError("CANARY_COUNT_NOT_INTEGER")
        if value[key] < 0:
            raise ProductionFinalizationError("CANARY_COUNT_NEGATIVE")
    for key in LEGACY_TRUE_GATE_KEYS_V2:
        if value.get(key) is not True:
            raise ProductionFinalizationError("CANARY_GATE_FAILED")
    for key in LEGACY_ZERO_KEYS_V2:
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


def _validate_current_canary_eligibility_receipt_v3(
    value: Mapping[str, Any]
) -> None:
    if set(value) != PRESERVATION_ELIGIBILITY_RECEIPT_KEYS:
        raise ProductionFinalizationError("CANARY_RECEIPT_SCHEMA_MISMATCH")
    if (
        value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_batch_preservation_eligibility_receipt_v3"
        or value.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
    ):
        raise ProductionFinalizationError("CANARY_RECEIPT_VERSION_MISMATCH")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", str(value.get("batch_id"))
    ):
        raise ProductionFinalizationError("CANARY_BATCH_ID_INVALID")
    if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
        raise ProductionFinalizationError("CANARY_ATTEMPT_ID_INVALID")
    if (
        not COMMIT_RE.fullmatch(str(value.get("governing_commit")))
        or value.get("source_commit") != value.get("governing_commit")
        or not TIMESTAMP_RE.fullmatch(str(value.get("run_timestamp_utc")))
        or value.get("cohort_version") != "mimic-iv-echo/1.0"
        or not re.fullmatch(
            r"split_map_sha256:[0-9a-f]{64}", str(value.get("split_version"))
        )
        or value.get("execution_contract_version") != 2
        or value.get("aggregate_safety_gate_result") != "PASS"
        or value.get("checkpoint_checksum") != value.get("checkpoint_sha256")
        or value.get("technical_disposition_policy_version")
        != production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
    ):
        raise ProductionFinalizationError("CANARY_PROVENANCE_VALUE_INVALID")
    for key in HASH_KEYS - RETIREMENT_RECEIPT_KEYS:
        if not SHA256_RE.fullmatch(str(value.get(key))):
            raise ProductionFinalizationError("CANARY_HASH_INVALID")
    for key in (
        "python_version", "pytorch_version", "torchvision_version",
        "cuda_version", "cudnn_version",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductionFinalizationError("CANARY_RUNTIME_VERSION_INVALID")
    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", str(value.get("scheduler_job_identity"))
    ):
        raise ProductionFinalizationError("CANARY_SCHEDULER_IDENTITY_INVALID")
    for key in COUNT_KEYS:
        if (
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            or value[key] < 0
        ):
            raise ProductionFinalizationError("CANARY_COUNT_INVALID")
    for key in TRUE_GATE_KEYS:
        if value.get(key) is not True:
            raise ProductionFinalizationError("CANARY_GATE_FAILED")
    for key in ZERO_KEYS:
        if value[key] != 0:
            raise ProductionFinalizationError("CANARY_SCIENTIFIC_INCONSISTENCY")
    if value.get("extracted_cache_retired") is not False:
        raise ProductionFinalizationError("CANARY_CACHE_NOT_RETAINED")
    if (
        value.get("technical_disposition_counts_by_class")
        != {"SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": 0}
        or value.get("prespecified_no_cine_study_set_sha256")
        != core.canonical_json_sha256([])
        or value.get("all_no_cine_studies_prespecified") is not True
        or value.get("n_object_technical_dispositions") != 0
        or value.get("n_studies_affected_by_technical_disposition") != 0
        or value["n_selected_studies"] != 5
        or value["n_selected_subjects"] != 5
        or value["n_pooled_studies"] != 5
        or value["n_no_cine_studies"] != 0
        or value.get("no_cine_disposition") != "NONE"
        or value["n_expected_objects"] < 5
        or value["expected_source_bytes"] < 1
        or value["n_download_verified"] != value["n_expected_objects"]
        or value["n_dicom_unreadable"] != 0
        or value["n_dicom_readable"] != value["n_expected_objects"]
        or value["n_multiframe_cines"] + value["n_single_frame_objects"]
        != value["n_dicom_readable"]
        or value["n_multiframe_cines"]
        != value["n_successfully_extracted_cines"]
        or value["n_successfully_extracted_cines"] != value["n_extracted_clips"]
        or value["n_successfully_extracted_cines"] != value["n_unique_clip_keys"]
        or value["n_successfully_extracted_cines"] != value["n_clip_embeddings"]
        or value["n_multiframe_cines"] < 5
    ):
        raise ProductionFinalizationError("CANARY_CLIP_ACCOUNTING_MISMATCH")


def _validate_canary_eligibility_receipt(value: Mapping[str, Any]) -> None:
    identity = (value.get("schema_version"), value.get("artifact_type"))
    if identity == (1, "lvef_c3_batch_preservation_eligibility_receipt_v2"):
        _validate_legacy_canary_eligibility_receipt_v2(value)
    elif identity == (2, "lvef_c3_batch_preservation_eligibility_receipt_v3"):
        _validate_current_canary_eligibility_receipt_v3(value)
    else:
        raise ProductionFinalizationError("CANARY_RECEIPT_VERSION_MISMATCH")


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
    r8r_mode = set(value) == FINAL_KEYS | R8R_FINAL_SUMMARY_KEYS
    r8u_mode = set(value) == FINAL_KEYS | R8U_FINAL_SUMMARY_KEYS
    r8u_r7d_mode = set(value) == FINAL_KEYS | R8U_R7D_FINAL_SUMMARY_KEYS
    mixed_epoch_mode = r8r_mode or r8u_mode or r8u_r7d_mode
    if set(value) != FINAL_KEYS and not mixed_epoch_mode:
        raise ProductionFinalizationError("FINAL_SUMMARY_SCHEMA_MISMATCH")
    scientific_scope_keys = {
        "model_fitting_count",
        "endpoint_prediction_count",
        "confirmatory_performance_access_count",
    }
    if any(type(value.get(key)) is not int or value[key] != 0 for key in scientific_scope_keys):
        raise ProductionFinalizationError("FINAL_SUMMARY_SCIENTIFIC_SCOPE_INVALID")
    integer_keys = {
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
        "successfully_extracted_cines",
        "object_technical_dispositions",
        "blocking_failures",
        "studies_affected_by_technical_disposition",
        "new_no_cine_studies",
        "unique_clip_keys",
        "clip_embeddings",
        "pooled_imaging_eligible_studies",
        "no_cine_studies",
        "outside_selected_studies",
        "missing_selected_studies",
        "duplicate_physical_sources",
        "duplicate_clip_keys",
        "nonfinite_embeddings",
        "wrong_dimension_embeddings",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        *scientific_scope_keys,
        "canonical_clip_index_size_bytes",
        "canonical_clip_index_rows",
        "canonical_study_embeddings_size_bytes",
        "canonical_study_manifest_size_bytes",
        "canonical_study_store_receipt_size_bytes",
        "cohort_preservation_receipt_size_bytes",
        "cohort_preserved_artifacts",
    }
    positive_keys = {
        "production_batches",
        "selected_studies",
        "selected_subjects",
        "verified_source_objects",
        "selected_source_bytes",
        "dicom_readable_objects",
        "multiframe_cines",
        "extracted_clips",
        "successfully_extracted_cines",
        "unique_clip_keys",
        "clip_embeddings",
        "pooled_imaging_eligible_studies",
    }
    zero_keys = {
        "blocking_failures",
        "new_no_cine_studies",
        "outside_selected_studies",
        "missing_selected_studies",
        "duplicate_physical_sources",
        "duplicate_clip_keys",
        "nonfinite_embeddings",
        "wrong_dimension_embeddings",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        "model_fitting_count",
        "endpoint_prediction_count",
        "confirmatory_performance_access_count",
    }
    true_gate_keys = {
        "all_batches_finalized",
        "all_source_receipts_passed",
        "all_dicom_audits_passed",
        "all_extraction_rows_resolved",
        "all_successful_extractions_embedded",
        "all_technical_dispositions_retained",
        "all_no_cine_studies_prespecified",
        "all_embeddings_passed",
        "all_pooling_passed",
        "all_preservation_manifests_passed",
        "all_aggregate_safety_gates_passed",
        "raw_dicoms_retained",
        "extracted_cache_retired",
    }
    false_gate_keys = {
        "outside_selected_studies_permitted",
        "scientific_inconsistency_repair_performed",
        "identifiers_emitted",
        "restricted_paths_emitted",
    }
    if (
        value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_production_finalization_summary_v2"
        or SHA256_RE.fullmatch(str(value.get("batch_receipt_set_sha256"))) is None
        or SHA256_RE.fullmatch(
            str(value.get("technical_disposition_manifest_set_sha256"))
        )
        is None
        or value.get("technical_disposition_policy_version")
        != production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        or any(
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            or value[key] < 0
            for key in integer_keys
        )
        or any(value[key] < 1 for key in positive_keys)
        or any(value[key] != 0 for key in zero_keys)
        or any(value.get(key) is not True for key in true_gate_keys)
        or value.get("all_authority_bindings_identical")
        is not (not mixed_epoch_mode)
        or (
            r8r_mode
            and (
                value.get("all_scientific_authority_bindings_identical")
                is not True
                or type(value.get("implementation_authority_epoch_count"))
                is not int
                or value.get("implementation_authority_epoch_count") != 2
                or COMMIT_RE.fullmatch(
                    str(value.get("r8r_implementation_commit"))
                )
                is None
                or SHA256_RE.fullmatch(
                    str(
                        value.get(
                            "r8r_recovery_continuation_authority_sha256"
                        )
                    )
                )
                is None
            )
        )
        or (
            r8u_mode
            and (
                value.get("all_scientific_authority_bindings_identical")
                is not True
                or type(value.get("implementation_authority_epoch_count"))
                is not int
                or value.get("implementation_authority_epoch_count") != 3
                or COMMIT_RE.fullmatch(
                    str(value.get("r8u_implementation_commit"))
                )
                is None
                or SHA256_RE.fullmatch(
                    str(
                        value.get(
                            "r8u_recovery_continuation_authority_sha256"
                        )
                    )
                )
                is None
            )
        )
        or (
            r8u_r7d_mode
            and (
                value.get("all_scientific_authority_bindings_identical")
                is not True
                or type(value.get("implementation_authority_epoch_count"))
                is not int
                or value.get("implementation_authority_epoch_count") != 4
                or COMMIT_RE.fullmatch(
                    str(value.get("r8u_r7d_implementation_commit"))
                )
                is None
                or SHA256_RE.fullmatch(
                    str(
                        value.get(
                            "r8u_r7d_continuation_authority_sha256"
                        )
                    )
                )
                is None
            )
        )
        or any(value.get(key) is not False for key in false_gate_keys)
        or value.get("technical_disposition_counts_by_class")
        != {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": value.get(
                "object_technical_dispositions"
            )
        }
        or value.get("selected_subjects") != value.get("selected_studies")
        or value.get("verified_source_objects")
        != value.get("dicom_readable_objects")
        + value.get("dicom_unreadable_objects")
        or value.get("dicom_readable_objects")
        != value.get("multiframe_cines") + value.get("single_frame_objects")
        or value.get("multiframe_cines")
        != value.get("successfully_extracted_cines")
        + value.get("object_technical_dispositions")
        or value.get("successfully_extracted_cines")
        != value.get("clip_embeddings")
        or value.get("successfully_extracted_cines")
        != value.get("extracted_clips")
        or value.get("successfully_extracted_cines")
        != value.get("unique_clip_keys")
        or value.get("pooled_imaging_eligible_studies")
        + value.get("no_cine_studies")
        != value.get("selected_studies")
        or value.get("no_cine_disposition")
        != (
            "NONE"
            if value.get("no_cine_studies") == 0
            else "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
        )
        or value.get("studies_affected_by_technical_disposition")
        > value.get("object_technical_dispositions")
        or (
            value.get("studies_affected_by_technical_disposition") == 0
        ) is not (value.get("object_technical_dispositions") == 0)
    ):
        raise ProductionFinalizationError("FINAL_SUMMARY_BINDING_INVALID")
    if value.get("status") == "PASS_PRODUCTION_C3_FINALIZED":
        hash_keys = {
            "canonical_clip_index_sha256",
            "canonical_study_embeddings_sha256",
            "canonical_study_manifest_sha256",
            "canonical_study_store_receipt_sha256",
            "cohort_preservation_receipt_sha256",
        }
        size_keys = {
            "canonical_clip_index_size_bytes",
            "canonical_study_embeddings_size_bytes",
            "canonical_study_manifest_size_bytes",
            "canonical_study_store_receipt_size_bytes",
            "cohort_preservation_receipt_size_bytes",
        }
        if (
            any(SHA256_RE.fullmatch(str(value.get(key))) is None for key in hash_keys)
            or any(
                isinstance(value.get(key), bool)
                or not isinstance(value.get(key), int)
                or value[key] < 1
                for key in size_keys
            )
            or value.get("canonical_clip_index_rows") != value.get("clip_embeddings")
            or value.get("cohort_preserved_artifacts")
            != 2 * int(value.get("production_batches", -1)) + 4
            or value.get("cohort_preservation_second_pass_replay_passed") is not True
            or value.get("cohort_preservation_passed") is not True
        ):
            raise ProductionFinalizationError("FINAL_SUMMARY_BINDING_INVALID")
    elif value.get("status") == "PASS_PRODUCTION_C3_BATCH_RECEIPTS_RECONCILED":
        if any(
            value.get(key) is not None
            for key in FINAL_BINDING_KEYS
            if key.endswith("_sha256")
        ) or any(
            value.get(key) != 0
            for key in FINAL_BINDING_KEYS
            if key.endswith("_size_bytes")
            or key in {"canonical_clip_index_rows", "cohort_preserved_artifacts"}
        ) or value.get("cohort_preservation_second_pass_replay_passed") is not False \
            or value.get("cohort_preservation_passed") is not False:
            raise ProductionFinalizationError("FINAL_SUMMARY_BINDING_INVALID")
    else:
        raise ProductionFinalizationError("FINAL_SUMMARY_STATUS_INVALID")


def _receipt_implementation_epoch(
    value: Mapping[str, Any],
) -> tuple[str, ...]:
    return tuple(str(value[key]) for key in R8R_IMPLEMENTATION_EPOCH_KEYS)


def _validate_r8r_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind the repair epoch to the clean current HEAD and strict ancestry."""

    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit == R8R_SCIENTIFIC_GOVERNING_COMMIT
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8R_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8R_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    head = run_git("rev-parse", "HEAD")
    branch = run_git("branch", "--show-current")
    tracked_status = run_git(
        "status", "--porcelain", "--untracked-files=no"
    )
    for commit in (R8R_SCIENTIFIC_GOVERNING_COMMIT, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8R_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    ancestry = run_git(
        "merge-base",
        "--is-ancestor",
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        implementation_commit,
    )
    distance = run_git(
        "rev-list",
        "--count",
        f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
    )
    if (
        head.returncode != 0
        or head.stderr
        or head.stdout != f"{implementation_commit}\n".encode("ascii")
        or branch.returncode != 0
        or branch.stderr
        or branch.stdout != b"codex/lvef-multitask-revalidation\n"
        or tracked_status.returncode != 0
        or tracked_status.stderr
        or tracked_status.stdout
        or ancestry.returncode != 0
        or ancestry.stdout
        or ancestry.stderr
        or distance.returncode != 0
        or distance.stderr
        or distance.stdout != b"1\n"
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )


def _validate_r8u_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R2 to the exact five-epoch implementation chain."""

    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit
        in {
            R8R_SCIENTIFIC_GOVERNING_COMMIT,
            R8U_PRIOR_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        }
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): (
            b"codex/lvef-multitask-revalidation\n"
        ),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_BASE_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_BASE_IMPLEMENTATION_COMMIT} "
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_PRIOR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT} "
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--count",
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}..{implementation_commit}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}..{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}..{R8U_BASE_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{R8U_PRIOR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{R8U_BASE_IMPLEMENTATION_COMMIT}",
        ): b"2\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"4\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        implementation_commit,
    ):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in (
        (R8R_SCIENTIFIC_GOVERNING_COMMIT, R8U_PRIOR_IMPLEMENTATION_COMMIT),
        (R8U_PRIOR_IMPLEMENTATION_COMMIT, R8U_BASE_IMPLEMENTATION_COMMIT),
        (
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT, implementation_commit),
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git("show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}")
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r3_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R3 to one exact child of the publication-resume repair."""

    fixed_commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in set(fixed_commits)
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode(
            "ascii"
        ),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): (
            b"codex/lvef-multitask-revalidation\n"
        ),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_BASE_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_BASE_IMPLEMENTATION_COMMIT} "
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_PRIOR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT} "
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--count",
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}.."
            f"{R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}.."
            f"{R8U_BASE_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}.."
            f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}.."
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}",
        ): b"5\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"6\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (*fixed_commits, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in (
        (R8R_SCIENTIFIC_GOVERNING_COMMIT, R8U_PRIOR_IMPLEMENTATION_COMMIT),
        (R8U_PRIOR_IMPLEMENTATION_COMMIT, R8U_BASE_IMPLEMENTATION_COMMIT),
        (
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            implementation_commit,
        ),
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git(
            "show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}"
        )
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r4_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R4 to the sole direct child of the immutable R3 repair."""

    fixed_commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in set(fixed_commits)
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list",
            "--count",
            f"{R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"1\n",
        (
            "rev-list",
            "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"7\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (*fixed_commits, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in (
        (R8R_SCIENTIFIC_GOVERNING_COMMIT, R8U_PRIOR_IMPLEMENTATION_COMMIT),
        (R8U_PRIOR_IMPLEMENTATION_COMMIT, R8U_BASE_IMPLEMENTATION_COMMIT),
        (
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        ),
        (
            R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
            implementation_commit,
        ),
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git(
            "show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}"
        )
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r5_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R5 to the sole direct child of the immutable R4 repair."""

    fixed_commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in set(fixed_commits)
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list", "--parents", "-n", "1", implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--count",
            f"{R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"1\n",
        (
            "rev-list", "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"8\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (*fixed_commits, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in zip(
        fixed_commits, (*fixed_commits[1:], implementation_commit), strict=True
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git(
            "show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}"
        )
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r6_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R6 to the sole direct child of the immutable R5 repair."""

    fixed_commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in set(fixed_commits)
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list", "--parents", "-n", "1", implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--count",
            f"{R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"1\n",
        (
            "rev-list", "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"9\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (*fixed_commits, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in zip(
        fixed_commits, (*fixed_commits[1:], implementation_commit), strict=True
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git(
            "show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}"
        )
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r7_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind R8U-R7 to the sole direct child of the immutable R6 repair."""

    fixed_commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT,
    )
    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit in set(fixed_commits)
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list", "--parents", "-n", "1", implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--count",
            f"{R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"1\n",
        (
            "rev-list", "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"10\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    for commit in (*fixed_commits, implementation_commit):
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in zip(
        fixed_commits, (*fixed_commits[1:], implementation_commit), strict=True
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for field, relative_path in sorted(R8U_FE3_GIT_TREE_PATHS.items()):
        blob = run_git(
            "show", f"{R8U_PRIOR_IMPLEMENTATION_COMMIT}:{relative_path}"
        )
        if (
            blob.returncode != 0
            or blob.stderr
            or hashlib.sha256(blob.stdout).hexdigest()
            != R8U_FE3_GIT_TREE_SHA256[field]
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_HISTORICAL_GIT_TREE_MISMATCH"
            )


def _validate_r8u_r7d_repository_authority(
    implementation_commit: str,
) -> None:
    """Bind the R7E runtime to the sole child of immutable R7D evidence."""

    if (
        not isinstance(implementation_commit, str)
        or COMMIT_RE.fullmatch(implementation_commit) is None
        or implementation_commit
        in {
            R8R_SCIENTIFIC_GOVERNING_COMMIT,
            R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT,
            R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT,
            R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT,
        }
    ):
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    repository = Path(__file__).resolve().parent.parent
    if repository.is_symlink() or not repository.is_dir():
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }

    def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            ) from exc

    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list", "--parents", "-n", "1", implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT} "
            f"{R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT,
        ): (
            f"{R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT} "
            f"{R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--count",
            f"{R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT}.."
            f"{implementation_commit}",
        ): b"2\n",
        (
            "rev-list", "--count",
            f"{R8R_SCIENTIFIC_GOVERNING_COMMIT}..{implementation_commit}",
        ): b"13\n",
    }
    for arguments, expected_stdout in exact_outputs.items():
        result = run_git(*arguments)
        if (
            result.returncode != 0
            or result.stderr
            or result.stdout != expected_stdout
        ):
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    status = run_git("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
        )
    commits = (
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT,
        R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT,
        R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT,
        implementation_commit,
    )
    for commit in commits:
        exists = run_git("cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0 or exists.stdout or exists.stderr:
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )
    for ancestor, descendant in zip(
        commits[:-1], commits[1:], strict=True
    ):
        ancestry = run_git("merge-base", "--is-ancestor", ancestor, descendant)
        if ancestry.returncode != 0 or ancestry.stdout or ancestry.stderr:
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH"
            )


def _r8u_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the one closed five-commit authority bound into R8U-R2 receipts."""

    return {
        "scientific_commit": R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "r8r_implementation_commit": R8U_PRIOR_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": implementation_commit,
    }


def _r8u_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    """Reject missing, open, mistyped, or cross-artifact epoch bindings."""

    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _r8u_r3_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the exact seven-commit authority for R8U-R3 artifacts."""

    return {
        "scientific_commit": R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "r8r_implementation_commit": R8U_PRIOR_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_publication_resume_repair_commit": (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_candidate_authority_repair_commit": implementation_commit,
    }


def _r8u_r3_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    """Reject missing, open, mistyped, or mixed R2/R3 epoch bindings."""

    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_r3_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _r8u_r4_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the exact eight-commit authority for R8U-R4 artifacts."""

    return {
        "scientific_commit": R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "r8r_implementation_commit": R8U_PRIOR_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_publication_resume_repair_commit": (
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_candidate_authority_repair_commit": (
            R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_portability_repair_commit": implementation_commit,
    }


def _r8u_r4_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R4_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_r4_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _r8u_r5_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the exact nine-commit authority for R8U-R5 artifacts."""

    return {
        **_r8u_r4_expected_implementation_authority_epochs(
            R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_worker_context_repair_commit": implementation_commit,
    }


def _r8u_r5_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R5_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_r5_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _r8u_r6_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the exact ten-commit authority for R8U-R6 artifacts."""

    return {
        **_r8u_r5_expected_implementation_authority_epochs(
            R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_locality_ordering_repair_commit": implementation_commit,
    }


def _r8u_r6_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R6_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_r6_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _r8u_r7_expected_implementation_authority_epochs(
    implementation_commit: str,
) -> dict[str, str]:
    """Return the exact eleven-commit authority for R8U-R7 artifacts."""

    return {
        **_r8u_r6_expected_implementation_authority_epochs(
            R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_npz_metadata_repair_commit": implementation_commit,
    }


def _r8u_r7_validate_implementation_authority_epochs(
    value: object,
    *,
    implementation_commit: str,
) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R7_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
        or any(type(value.get(key)) is not str for key in value)
        or dict(value)
        != _r8u_r7_expected_implementation_authority_epochs(
            implementation_commit
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID"
        )


def _current_r8r_implementation_epoch() -> tuple[str, ...]:
    script_root = Path(__file__).resolve().parent
    stage_sha256 = sha256_file(
        script_root / "lvef_c3_production_stages.py"
    )
    preservation_sha256 = sha256_file(
        script_root / "preserve_lvef_c3_production_batch.py"
    )
    runner_sha256 = sha256_file(
        script_root / R8R_SCHEDULER_RUNNER_BASENAME
    )
    retirement_sha256 = sha256_file(
        script_root / "retire_lvef_c3_extracted_cache_v2.py"
    )
    command_checksum = core.canonical_json_sha256(
        {
            "production_stage_wrapper_sha256": stage_sha256,
            "batch_preservation_script_sha256": preservation_sha256,
            "scheduler_runner_sha256": runner_sha256,
        }
    )
    return (
        stage_sha256,
        preservation_sha256,
        runner_sha256,
        command_checksum,
        retirement_sha256,
    )


def _validate_r8r_qsub_evidence(
    value: object, *, accepted_stdout: Sequence[bytes] | None = None
) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8R_QSUB_EVIDENCE_KEYS
        or type(value.get("stdout_bytes")) is not int
        or int(value["stdout_bytes"]) < 2
        or SHA256_RE.fullmatch(str(value.get("stdout_sha256"))) is None
        or value.get("stderr_bytes") != 0
        or value.get("stderr_sha256") != hashlib.sha256(b"").hexdigest()
        or value.get("exit_status") != 0
        or (
            accepted_stdout is not None
            and not any(
                value.get("stdout_bytes") == len(payload)
                and value.get("stdout_sha256")
                == hashlib.sha256(payload).hexdigest()
                for payload in accepted_stdout
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )


def _validate_r8r_recovery_accounting(
    value: object, *, expected_job_id: str
) -> str:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8R_RECOVERY_ACCOUNTING_KEYS
        or value.get("status") != "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0"
        or value.get("job_id") != expected_job_id
        or value.get("task_id") not in {"NONE", "undefined"}
        or type(value.get("failed")) is not int
        or value.get("failed") != 0
        or type(value.get("exit_status")) is not int
        or value.get("exit_status") != 0
        or not isinstance(value.get("start_time"), str)
        or not isinstance(value.get("end_time"), str)
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:[.][0-9]+)?",
            str(value.get("ru_wallclock_seconds")),
        )
        is None
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        )
    try:
        start = datetime.strptime(
            str(value["start_time"]), "%a %b %d %H:%M:%S %Y"
        )
        end = datetime.strptime(
            str(value["end_time"]), "%a %b %d %H:%M:%S %Y"
        )
    except ValueError as exc:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        ) from exc
    if end < start:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        )
    return core.canonical_json_sha256(value)


def _validate_r8r_capacity_observation(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )
    largest_objects = 17_517
    largest_source_bytes = 63_376_316_676
    remaining_objects = 280_263
    remaining_studies = 3_780
    remaining_source_bytes = 1_014_021_066_806
    rolling_bytes = (
        largest_objects * r8r_capacity.R8R_EXTRACTED_BYTES_PER_OBJECT
    )
    clip_bytes = (
        remaining_objects * r8r_capacity.R8R_CLIP_EMBEDDING_BYTES_PER_OBJECT
    )
    study_bytes = (
        remaining_studies * r8r_capacity.R8R_STUDY_EMBEDDING_BYTES_PER_STUDY
    )
    increment = sum(
        (
            remaining_source_bytes,
            largest_source_bytes,
            rolling_bytes,
            clip_bytes,
            study_bytes,
            r8r_capacity.R8R_RETAINED_EXTRACTED_AUDIT_BYTES,
            r8r_capacity.R8R_MANIFEST_AND_METADATA_BYTES,
            r8r_capacity.R8R_LOG_BYTES,
            r8r_capacity.R8R_PRESERVATION_AND_FINALIZATION_BYTES,
            r8r_capacity.R8R_SAFETY_BYTES,
        )
    )
    required_files = (
        remaining_objects
        + largest_objects
        + r8r_capacity.R8R_FIXED_CONTROL_FILE_DEMAND
    )
    exact = {
        "schema_version": 1,
        "artifact_type": r8r_capacity.R8R_CONTINUATION_ARTIFACT_TYPE,
        "status": r8r_capacity.R8R_CONTINUATION_STATUS_PASS,
        "blocking_reason_codes": [],
        "original_attempt_id": R8R_ATTEMPT_ID,
        "original_plan_sha256": R8R_BATCH_PLAN_SHA256,
        "original_scientific_governing_commit": (
            R8R_SCIENTIFIC_GOVERNING_COMMIT
        ),
        "continuation_first_task": 4,
        "continuation_last_task": 19,
        "continuation_task_count": 16,
        "remaining_batch_count": 16,
        "remaining_studies": remaining_studies,
        "remaining_objects": remaining_objects,
        "remaining_source_bytes": remaining_source_bytes,
        "largest_remaining_batch_objects": largest_objects,
        "largest_remaining_batch_source_bytes": largest_source_bytes,
        "remaining_raw_source_demand_bytes": remaining_source_bytes,
        "largest_remaining_transfer_retry_demand_bytes": largest_source_bytes,
        "largest_rolling_extracted_cache_demand_bytes": rolling_bytes,
        "remaining_clip_embedding_upper_bound_bytes": clip_bytes,
        "remaining_study_embedding_upper_bound_bytes": study_bytes,
        "retained_extracted_audit_demand_bytes": (
            r8r_capacity.R8R_RETAINED_EXTRACTED_AUDIT_BYTES
        ),
        "manifest_and_metadata_demand_bytes": (
            r8r_capacity.R8R_MANIFEST_AND_METADATA_BYTES
        ),
        "log_demand_bytes": r8r_capacity.R8R_LOG_BYTES,
        "preservation_and_finalization_demand_bytes": (
            r8r_capacity.R8R_PRESERVATION_AND_FINALIZATION_BYTES
        ),
        "safety_demand_bytes": r8r_capacity.R8R_SAFETY_BYTES,
        "continuation_increment_bytes": increment,
        "remaining_raw_object_file_demand": remaining_objects,
        "largest_rolling_object_file_demand": largest_objects,
        "fixed_control_file_demand": (
            r8r_capacity.R8R_FIXED_CONTROL_FILE_DEMAND
        ),
        "required_file_slots": required_files,
        "required_quota_reserve_bytes": r8r_capacity.R8R_QUOTA_RESERVE_BYTES,
        "required_physical_reserve_bytes": (
            r8r_capacity.R8R_PHYSICAL_RESERVE_BYTES
        ),
        "quota_reserve_gate_passed": True,
        "physical_reserve_gate_passed": True,
        "file_slot_gate_passed": True,
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "native_quota_authority_read_only": True,
    }
    integer_fields = (
        "research_quota_bytes",
        "research_usage_bytes",
        "research_quota_remaining_bytes",
        "research_file_quota",
        "research_files_used",
        "research_file_slots_remaining",
        "research_filesystem_total_bytes",
        "research_filesystem_used_bytes",
        "research_filesystem_available_bytes",
        "projected_research_usage_bytes",
        "quota_slack_after_continuation_bytes",
        "physical_slack_after_continuation_bytes",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
    )
    if (
        set(value) != r8r_capacity.R8R_CONTINUATION_CAPACITY_KEYS
        or any(value.get(key) != expected for key, expected in exact.items())
        or any(
            type(value.get(key)) is not type(expected)
            for key, expected in exact.items()
            if type(expected) in {int, bool}
        )
        or any(
            type(value.get(key)) is not int
            for key in integer_fields
        )
        or min(
            value["research_quota_bytes"],
            value["research_usage_bytes"],
            value["research_file_quota"],
            value["research_files_used"],
            value["research_filesystem_total_bytes"],
            value["research_filesystem_used_bytes"],
            value["research_filesystem_available_bytes"],
        )
        < 0
        or value["research_quota_bytes"] == 0
        or value["research_file_quota"] == 0
        or value["research_filesystem_total_bytes"] == 0
        or value["research_quota_bytes"] % 1024 != 0
        or value["research_usage_bytes"] % 1024 != 0
        or value["research_quota_bytes"]
        < r8r_capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
        or value["research_file_quota"]
        < r8r_capacity.EXPECTED_RESEARCH_FILE_QUOTA
        or value["research_usage_bytes"]
        > value["research_quota_bytes"]
        or value["research_files_used"]
        > value["research_file_quota"]
        or value["research_filesystem_used_bytes"]
        + value["research_filesystem_available_bytes"]
        > value["research_filesystem_total_bytes"]
        or value.get("pquota_display_crosscheck")
        not in {
            r8r_capacity.DISPLAY_CROSSCHECK_PASS,
            r8r_capacity.DISPLAY_CROSSCHECK_UNAVAILABLE,
        }
        or any(
            type(value.get(key)) is not int or value.get(key) != 0
            for key in r8r_capacity.R8R_CONTINUATION_ZERO_EFFECT_KEYS
        )
        or value["research_quota_remaining_bytes"]
        != value["research_quota_bytes"] - value["research_usage_bytes"]
        or value["research_file_slots_remaining"]
        != value["research_file_quota"] - value["research_files_used"]
        or value["projected_research_usage_bytes"]
        != value["research_usage_bytes"] + increment
        or value["quota_slack_after_continuation_bytes"]
        != value["research_quota_bytes"]
        - value["projected_research_usage_bytes"]
        or value["physical_slack_after_continuation_bytes"]
        != value["research_filesystem_available_bytes"] - increment
        or value["quota_margin_beyond_reserve_bytes"]
        != value["quota_slack_after_continuation_bytes"]
        - r8r_capacity.R8R_QUOTA_RESERVE_BYTES
        or value["physical_margin_beyond_reserve_bytes"]
        != value["physical_slack_after_continuation_bytes"]
        - r8r_capacity.R8R_PHYSICAL_RESERVE_BYTES
        or value["file_slot_margin_after_demand"]
        != value["research_file_slots_remaining"] - required_files
        or min(
            value["quota_margin_beyond_reserve_bytes"],
            value["physical_margin_beyond_reserve_bytes"],
            value["file_slot_margin_after_demand"],
        )
        < 0
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )


def _r8r_controller_json_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(core.canonical_json_bytes(value) + b"\n").hexdigest()


def _r8u_r3_expected_resume_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    """Return the sole fixed Batch-16 publication-resume qsub command."""

    return [
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub",
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        f"lvef_c3_r8u_r3_res_{implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(attempt_root / "r8u_r3_batch16_publication_resume/scheduler"),
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME),
    ]


def _r8u_r4_expected_resume_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    """Return the sole fixed R4 Batch-16 publication-resume qsub command."""

    command = _r8u_r3_expected_resume_qsub_command(
        attempt_root=attempt_root,
        implementation_commit=implementation_commit,
    )
    command[command.index(f"lvef_c3_r8u_r3_res_{implementation_commit[:8]}")] = (
        f"lvef_c3_r8u_r4_res_{implementation_commit[:8]}"
    )
    output_index = command.index(
        str(attempt_root / "r8u_r3_batch16_publication_resume/scheduler")
    )
    command[output_index] = str(
        attempt_root / "r8u_r4_batch16_publication_resume/scheduler"
    )
    return command


def _r8u_r5_expected_resume_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    """Return the sole fixed R5 Batch-16 publication-resume qsub command."""

    command = _r8u_r4_expected_resume_qsub_command(
        attempt_root=attempt_root,
        implementation_commit=implementation_commit,
    )
    command[command.index(f"lvef_c3_r8u_r4_res_{implementation_commit[:8]}")] = (
        f"lvef_c3_r8u_r5_res_{implementation_commit[:8]}"
    )
    output_index = command.index(
        str(attempt_root / "r8u_r4_batch16_publication_resume/scheduler")
    )
    command[output_index] = str(
        attempt_root / "r8u_r5_batch16_publication_resume/scheduler"
    )
    return command


def _r8u_r5_expected_probe_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    return [
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub",
        "-clear", "-terse", "-r", "n", "-P", "mimicecho",
        "-N", f"lvef_c3_r8u_r5_ctx_{implementation_commit[:8]}",
        "-j", "y", "-o",
        str(attempt_root / "r8u_r5_batch16_publication_resume/scheduler"),
        "-l", "h_rt=00:10:00", "-pe", "omp", "1",
        "-l", "mem_per_core=1G",
        str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME),
    ]


def _r8u_r6_expected_resume_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    """Return the sole fixed R6 Batch-16 publication-resume qsub command."""

    command = _r8u_r5_expected_resume_qsub_command(
        attempt_root=attempt_root,
        implementation_commit=implementation_commit,
    )
    command[command.index(f"lvef_c3_r8u_r5_res_{implementation_commit[:8]}")] = (
        f"lvef_c3_r8u_r6_res_{implementation_commit[:8]}"
    )
    output_index = command.index(
        str(attempt_root / "r8u_r5_batch16_publication_resume/scheduler")
    )
    command[output_index] = str(
        attempt_root / "r8u_r6_batch16_publication_resume/scheduler"
    )
    return command


def _r8u_r6_expected_probe_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    return [
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub",
        "-clear", "-terse", "-r", "n", "-P", "mimicecho",
        "-N", f"lvef_c3_r8u_r6_loc_{implementation_commit[:8]}",
        "-j", "y", "-o",
        str(attempt_root / "r8u_r6_batch16_publication_resume/scheduler"),
        "-l", "h_rt=00:10:00", "-pe", "omp", "1",
        "-l", "mem_per_core=1G",
        str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME),
    ]


def _r8u_r7_expected_recovery_qsub_command(
    *, attempt_root: Path, implementation_commit: str
) -> list[str]:
    """Return the sole fixed CPU-only R7 preservation-recovery qsub."""

    return [
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub",
        "-clear", "-terse", "-r", "n", "-P", "mimicecho",
        "-N", f"lvef_c3_r8u_r7_rec_{implementation_commit[:8]}",
        "-j", "y", "-o",
        str(attempt_root / "r8u_r7_batch16_preservation_recovery/scheduler"),
        "-l", "h_rt=02:00:00", "-pe", "omp", "4",
        "-l", "mem_per_core=16G",
        str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME),
    ]


def _r8r_expected_qsub_commands(
    *, attempt_root: Path, implementation_commit: str, array_job_id: str
) -> tuple[list[str], list[str], list[str]]:
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(
        Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
    )
    recovery_root = attempt_root / "r8r_batch3_recovery/scheduler"
    continuation_root = attempt_root / "r8r_continuation/scheduler"
    common = [qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho"]
    recovery = [
        *common,
        "-N",
        f"lvef_c3_r8r_rec_{implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(recovery_root),
        "-l",
        "h_rt=02:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    array = [
        *common,
        "-N",
        f"lvef_c3_r8r_seq_{implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_root),
        "-t",
        "4-19",
        "-tc",
        "1",
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    finalizer = [
        *common,
        "-N",
        f"lvef_c3_r8r_fin_{implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_root),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        runner,
    ]
    return recovery, array, finalizer


def _r8r_current_script_authority() -> Mapping[str, str]:
    script_root = Path(__file__).resolve().parent
    paths = {
        "controller_sha256": script_root
        / "lvef_c3_r8r_recovery_continuation.py",
        "full_sequential_sha256": script_root / "lvef_c3_full_sequential.py",
        "production_stages_sha256": script_root / "lvef_c3_production_stages.py",
        "preservation_sha256": script_root
        / "preserve_lvef_c3_production_batch.py",
        "retirement_sha256": script_root
        / "retire_lvef_c3_extracted_cache_v2.py",
        "finalizer_sha256": Path(__file__).resolve(),
        "runner_sha256": script_root / R8R_SCHEDULER_RUNNER_BASENAME,
    }
    try:
        return {
            key: sha256_file(path) for key, path in sorted(paths.items())
        }
    except OSError as exc:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc


def _validate_r8r_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    authority: R8RImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    historical_script_authority: Mapping[str, str] | None = None,
) -> str:
    """Validate and seal the exact fixed recovery/continuation chain."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or any(
            receipt_paths_by_batch.get(f"c3_batch_{index:03d}")
            != attempt_root
            / "batches"
            / f"c3_batch_{index:03d}"
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
            for index in range(19)
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    specifications = (
        (
            "recovery_authority_sha256",
            attempt_root
            / "r8r_batch3_recovery/recovery_authority.restricted.json",
            "lvef_c3_r8r_batch3_recovery_authority_v1",
            "AUTHORIZED_FIXED_BATCH3_PRESERVATION_RECOVERY",
        ),
        (
            "recovery_terminal_receipt_sha256",
            attempt_root
            / "r8r_batch3_recovery/recovery_terminal.aggregate_safe.json",
            "lvef_c3_r8r_batch3_recovery_terminal_v1",
            "PASS_BATCH3_PRESERVATION_RECOVERY",
        ),
        (
            "continuation_capacity_receipt_sha256",
            attempt_root
            / "r8r_continuation/continuation_capacity.restricted.json",
            "lvef_c3_r8r_fixed_continuation_capacity_v1",
            "PASS_FIXED_CONTINUATION_4_19_WITH_200GB_RESERVE",
        ),
        (
            "continuation_claim_sha256",
            attempt_root
            / "r8r_continuation/continuation_claim.restricted.json",
            "lvef_c3_r8r_fixed_continuation_claim_v1",
            "AUTHORIZED_FIXED_CONTINUATION_4_19",
        ),
        (
            "continuation_submission_receipt_sha256",
            attempt_root
            / "r8r_continuation/scheduler/submission_receipt.restricted.json",
            "lvef_c3_r8r_fixed_continuation_submission_v1",
            "PASS_EXACT_ARRAY_4_19_AND_HELD_FINALIZER",
        ),
    )
    observed: dict[str, str] = {}
    values: dict[str, Mapping[str, Any]] = {}
    for field, path, artifact_type, status in specifications:
        try:
            payload = _stable_nofollow_bytes(
                path,
                code="R8R_FINALIZER_CHAIN_ARTIFACT",
                max_bytes=128 * 1024 * 1024,
            )
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ProductionFinalizationError(
                        "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
                    )
                ),
            )
        except ProductionFinalizationError:
            raise
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ProductionFinalizationError(
                "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
            ) from exc
        if (
            not isinstance(value, Mapping)
            or payload != core.canonical_json_bytes(value)
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or value.get("artifact_type") != artifact_type
            or value.get("status") != status
            or value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != getattr(authority, field):
            raise ProductionFinalizationError(
                "R8R_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
            )
        observed[field] = digest
        values[field] = value

    recovery_submission_path = (
        attempt_root
        / "r8r_batch3_recovery/scheduler/submission_receipt.restricted.json"
    )
    try:
        recovery_submission_payload = _stable_nofollow_bytes(
            recovery_submission_path,
            code="R8R_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        recovery_submission = json.loads(
            recovery_submission_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    if (
        not isinstance(recovery_submission, Mapping)
        or recovery_submission_payload
        != core.canonical_json_bytes(recovery_submission)
        or set(recovery_submission) != R8R_RECOVERY_SUBMISSION_KEYS
        or type(recovery_submission.get("schema_version")) is not int
        or recovery_submission.get("schema_version") != 1
        or recovery_submission.get("artifact_type")
        != "lvef_c3_r8r_batch3_recovery_submission_v1"
        or recovery_submission.get("status")
        != "PASS_EXACT_ONE_CPU_RECOVERY_QSUB"
        or recovery_submission.get("original_scientific_commit")
        != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or recovery_submission.get("implementation_commit")
        != authority.implementation_commit
        or recovery_submission.get("attempt_id") != R8R_ATTEMPT_ID
        or recovery_submission.get("batch_plan_sha256")
        != R8R_BATCH_PLAN_SHA256
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    recovery_submission_sha256 = hashlib.sha256(
        recovery_submission_payload
    ).hexdigest()
    observed["recovery_submission_receipt_sha256"] = (
        recovery_submission_sha256
    )
    values["recovery_submission_receipt_sha256"] = recovery_submission

    recovery = values["recovery_authority_sha256"]
    terminal = values["recovery_terminal_receipt_sha256"]
    capacity_value = values["continuation_capacity_receipt_sha256"]
    claim = values["continuation_claim_sha256"]
    submission = values["continuation_submission_receipt_sha256"]
    schema_keys = {
        "recovery_authority_sha256": R8R_RECOVERY_AUTHORITY_KEYS,
        "recovery_terminal_receipt_sha256": R8R_RECOVERY_TERMINAL_KEYS,
        "continuation_capacity_receipt_sha256": (
            R8R_CONTINUATION_CAPACITY_RECEIPT_KEYS
        ),
        "continuation_claim_sha256": R8R_CONTINUATION_CLAIM_KEYS,
        "continuation_submission_receipt_sha256": (
            R8R_CONTINUATION_SUBMISSION_KEYS
        ),
    }
    if any(
        set(values[field]) != expected_keys
        for field, expected_keys in schema_keys.items()
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    expected_recovery_name = (
        f"lvef_c3_r8r_rec_{authority.implementation_commit[:8]}"
    )
    if (
        recovery_submission.get("batch_id") != "c3_batch_002"
        or re.fullmatch(r"[1-9][0-9]{0,19}", recovery_job_id) is None
        or recovery_submission.get("recovery_job_name")
        != expected_recovery_name
        or SHA256_RE.fullmatch(
            str(recovery_submission.get("qsub_environment_sha256"))
        )
        is None
        or type(recovery_submission.get("scheduler_submission_count")) is not int
        or recovery_submission.get("scheduler_submission_count") != 1
        or recovery_submission.get("recovery_is_array") is not False
        or recovery_submission.get("gpu_requested") is not False
        or recovery_submission.get("automatic_retry_authorized") is not False
        or any(
            type(recovery_submission.get(key)) is not int
            or recovery_submission.get(key) != 0
            for key in (
                "cloud_requests",
                "dicom_body_reads_by_submitter",
                "gpu_executions_by_submitter",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    prefix_sha256 = [
        R8R_HISTORICAL_PREFIX_RECEIPT_AUTHORITIES[
            f"c3_batch_{index:03d}"
        ][1]
        for index in range(2)
    ]
    batch3_payload = _stable_nofollow_bytes(
        receipt_paths_by_batch["c3_batch_002"],
        code="R8R_FINALIZER_CHAIN_ARTIFACT",
        max_bytes=128 * 1024 * 1024,
    )
    prefix_sha256.append(hashlib.sha256(batch3_payload).hexdigest())
    recovery_zero_keys = (
        "cloud_requests_authorized",
        "dicom_body_reads_authorized",
        "dicom_extraction_reruns_authorized",
        "echoprime_reruns_authorized",
        "embedding_generations_authorized",
        "gpu_executions_authorized",
    )
    current_script_authority = (
        _r8r_current_script_authority()
        if historical_script_authority is None
        else dict(sorted(historical_script_authority.items()))
    )
    if (
        set(current_script_authority) != set(R8U_FE3_GIT_TREE_SHA256)
        or any(
            SHA256_RE.fullmatch(str(value)) is None
            for value in current_script_authority.values()
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        )
    if (
        recovery.get("batch_id") != "c3_batch_002"
        or type(recovery.get("fixed_recovery_task")) is not int
        or recovery.get("fixed_recovery_task") != 3
        or recovery.get("prefix_final_receipt_sha256") != prefix_sha256[:2]
        or recovery.get("runtime_validation_context")
        != "SEALED_SCHEDULER_RUNTIME_REPLAY"
        or recovery.get("qsub_environment_sha256")
        != recovery_submission.get("qsub_environment_sha256")
        or not isinstance(recovery.get("original_control_authority"), Mapping)
        or not isinstance(recovery.get("batch3_retained_authority"), Mapping)
        or recovery.get("script_authority") != current_script_authority
        or any(
            type(recovery.get(key)) is not int or recovery.get(key) != 0
            for key in recovery_zero_keys
        )
        or any(
            recovery.get(key) is not False
            for key in (
                "raw_dicom_deletion_authorized",
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    terminal_hash_keys = (
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "batch_finalization_receipt_sha256",
    )
    terminal_exact = {
        "batch_id": "c3_batch_002",
        "n_selected_studies": 250,
        "n_expected_objects": 18_653,
        "expected_source_bytes": 68_725_707_170,
        "n_successfully_extracted_cines": 10_256,
        "n_object_technical_dispositions": 1,
        "n_blocking_failures": 0,
        "n_clip_embeddings": 10_256,
        "n_pooled_studies": 249,
        "n_no_cine_studies": 1,
        "n_new_no_cine_studies": 0,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
    }
    terminal_zero_keys = (
        "download_reruns",
        "dicom_extraction_reruns",
        "echoprime_reruns",
        "embedding_generations",
        "cloud_requests",
        "dicom_body_reads",
        "gpu_executions",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    )
    raw_retention = terminal.get("raw_retention_authority")
    if (
        any(
            SHA256_RE.fullmatch(str(terminal.get(key))) is None
            for key in terminal_hash_keys
        )
        or any(
            terminal.get(key) != expected
            for key, expected in terminal_exact.items()
        )
        or any(
            type(terminal.get(key)) is not type(expected)
            for key, expected in terminal_exact.items()
            if type(expected) in {int, bool}
        )
        or any(
            type(terminal.get(key)) is not int or terminal.get(key) != 0
            for key in terminal_zero_keys
        )
        or not isinstance(raw_retention, Mapping)
        or raw_retention.get("raw_dicom_files") != 18_653
        or raw_retention.get("raw_dicom_bytes") != 68_725_707_170
        or raw_retention.get("raw_dicom_body_reads") != 0
        or SHA256_RE.fullmatch(
            str(raw_retention.get("raw_metadata_projection_sha256"))
        )
        is None
        or terminal.get("recovery_submission_receipt_sha256")
        != recovery_submission_sha256
        or terminal.get("batch_finalization_receipt_sha256")
        != prefix_sha256[2]
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    accounting = capacity_value.get("recovery_scheduler_accounting")
    accounting_sha256 = _validate_r8r_recovery_accounting(
        accounting, expected_job_id=recovery_job_id
    )
    _validate_r8r_capacity_observation(
        capacity_value.get("capacity_observation")
    )
    capacity_zero_keys = (
        "active_extraction_caches",
        "cloud_requests",
        "dicom_body_reads",
        "npz_body_reads",
        "scheduler_submissions",
        "gpu_executions",
        "embedding_generations",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    )
    if (
        TIMESTAMP_RE.fullmatch(str(capacity_value.get("captured_at_utc")))
        is None
        or capacity_value.get("continuation_task_range") != "4-19"
        or type(capacity_value.get("continuation_task_count")) is not int
        or capacity_value.get("continuation_task_count") != 16
        or type(capacity_value.get("continuation_max_concurrency")) is not int
        or capacity_value.get("continuation_max_concurrency") != 1
        or capacity_value.get("prefix_final_receipt_sha256") != prefix_sha256
        or any(
            type(capacity_value.get(key)) is not int
            or capacity_value.get(key) != 0
            for key in capacity_zero_keys
        )
        or any(
            capacity_value.get(key) is not True
            for key in (
                "continuation_root_absent_at_capture",
                "continuation_claim_absent_at_capture",
                "continuation_submission_receipt_absent_at_capture",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )

    claim_zero_keys = (
        "cloud_requests_by_submitter",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "gpu_executions_by_submitter",
        "embedding_generations_by_submitter",
    )
    if (
        claim.get("original_claim_sha256")
        != "482b084b7e6407349b4c330a8029bae19a88c06d2a390a9fe2f385d5c7349b99"
        or claim.get("original_submission_receipt_sha256")
        != "5a8b942cef716b3cc90411638024146a5189781f6f4c70654bbfe0efc41e9c46"
        or claim.get("prefix_final_receipt_sha256") != prefix_sha256
        or claim.get("recovery_job_id") != recovery_job_id
        or claim.get("recovery_scheduler_accounting_sha256")
        != accounting_sha256
        or claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or SHA256_RE.fullmatch(str(claim.get("qsub_environment_sha256")))
        is None
        or claim.get("script_authority") != current_script_authority
        or claim.get("continuation_task_range") != "4-19"
        or type(claim.get("continuation_task_count")) is not int
        or claim.get("continuation_task_count") != 16
        or type(claim.get("continuation_max_concurrency")) is not int
        or claim.get("continuation_max_concurrency") != 1
        or type(claim.get("held_finalizer_count")) is not int
        or claim.get("held_finalizer_count") != 1
        or any(
            type(claim.get(key)) is not int or claim.get(key) != 0
            for key in claim_zero_keys
        )
        or any(
            claim.get(key) is not False
            for key in (
                "automatic_retry_authorized",
                "whole_stage_retry_authorized",
                "third_continuation_submission_reachable",
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_BINDING_MISMATCH"
        )

    array_job_id = str(submission.get("array_job_id", ""))
    finalizer_job_id = str(submission.get("finalizer_job_id", ""))
    recovery_command, array_command, finalizer_command = (
        _r8r_expected_qsub_commands(
            attempt_root=attempt_root,
            implementation_commit=authority.implementation_commit,
            array_job_id=array_job_id,
        )
    )
    _validate_r8r_qsub_evidence(
        recovery_submission.get("recovery_qsub_evidence"),
        accepted_stdout=(
            recovery_job_id.encode("ascii"),
            f"{recovery_job_id}\n".encode("ascii"),
        ),
    )
    _validate_r8r_qsub_evidence(
        submission.get("array_qsub_evidence"),
        accepted_stdout=tuple(
            value.encode("ascii")
            for value in (
                array_job_id,
                f"{array_job_id}\n",
                f"{array_job_id}.4-19:1",
                f"{array_job_id}.4-19:1\n",
            )
        ),
    )
    _validate_r8r_qsub_evidence(
        submission.get("finalizer_qsub_evidence"),
        accepted_stdout=(
            finalizer_job_id.encode("ascii"),
            f"{finalizer_job_id}\n".encode("ascii"),
        ),
    )
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or submission.get("array_job_name")
        != f"lvef_c3_r8r_seq_{authority.implementation_commit[:8]}"
        or submission.get("finalizer_job_name")
        != f"lvef_c3_r8r_fin_{authority.implementation_commit[:8]}"
        or recovery_submission.get("recovery_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": recovery_command})
        or submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or submission.get("qsub_environment_sha256")
        != claim.get("qsub_environment_sha256")
        or type(submission.get("scheduler_submission_count")) is not int
        or submission.get("scheduler_submission_count") != 2
        or type(submission.get("scheduler_submission_maximum")) is not int
        or submission.get("scheduler_submission_maximum") != 2
        or submission.get("array_task_range") != "4-19"
        or type(submission.get("array_task_count")) is not int
        or submission.get("array_task_count") != 16
        or type(submission.get("array_max_concurrency")) is not int
        or submission.get("array_max_concurrency") != 1
        or submission.get("finalizer_held_on_array") is not True
        or submission.get("whole_stage_retry_authorized") is not False
        or submission.get("third_continuation_submission_reachable") is not False
        or any(
            type(submission.get(key)) is not int
            or submission.get(key) != 0
            for key in (
                "cloud_requests",
                "dicom_body_reads_by_submitter",
                "npz_body_reads_by_submitter",
                "gpu_executions_by_submitter",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_BINDING_MISMATCH"
        )

    if (
        terminal.get("recovery_authority_sha256")
        != observed["recovery_authority_sha256"]
        or capacity_value.get("recovery_terminal_receipt_sha256")
        != observed["recovery_terminal_receipt_sha256"]
        or claim.get("recovery_terminal_receipt_sha256")
        != observed["recovery_terminal_receipt_sha256"]
        or claim.get("continuation_capacity_receipt_sha256")
        != observed["continuation_capacity_receipt_sha256"]
        or submission.get("continuation_capacity_receipt_sha256")
        != observed["continuation_capacity_receipt_sha256"]
        or submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_CHAIN_BINDING_MISMATCH"
        )
    return core.canonical_json_sha256(
        {
            "implementation_commit": authority.implementation_commit,
            **dict(sorted(observed.items())),
        }
    )


def _validate_r8r_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8RImplementationAuthority,
) -> str:
    """Accept only the fixed 2+17 historical/repair implementation split."""

    if type(authority) is not R8RImplementationAuthority:
        raise ProductionFinalizationError("R8R_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = (
        authority.recovery_authority_sha256,
        authority.recovery_terminal_receipt_sha256,
        authority.continuation_capacity_receipt_sha256,
        authority.continuation_claim_sha256,
        authority.continuation_submission_receipt_sha256,
    )
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
    ):
        raise ProductionFinalizationError("R8R_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(
            expected_runtime_authority
        )
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )

    for batch_id, expected in R8R_HISTORICAL_PREFIX_RECEIPT_AUTHORITIES.items():
        expected_bytes, expected_sha256 = expected
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8R_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )
    historical_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    repair_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:]
    }
    if (
        len(historical_epochs) != 1
        or len(repair_epochs) != 1
        or historical_epochs == repair_epochs
    ):
        raise ProductionFinalizationError(
            "R8R_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8r_repository_authority(authority.implementation_commit)
    try:
        expected_repair_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if repair_epochs != {expected_repair_epoch}:
        raise ProductionFinalizationError(
            "R8R_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    return _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
    )


def _load_r8u_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str,
    authority: R8UImplementationAuthority,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_CHAIN_ARTIFACT_KEYS[field]
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") != status
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    _r8u_validate_implementation_authority_epochs(
        value.get("implementation_authority_epochs"),
        implementation_commit=authority.implementation_commit,
    )
    if field == "recovery_capacity_receipt_sha256":
        try:
            r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                plan,
                value,
                r8u_scheduler_log_repair_commit=(
                    authority.implementation_commit
                ),
            )
        except r8r_capacity.PostReallocationCapacityError as exc:
            raise ProductionFinalizationError(
                "R8U_FINALIZER_CAPACITY_AUTHORITY_INVALID"
            ) from exc
    elif (
        value.get("original_scientific_commit")
        != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or value.get("implementation_commit")
        != authority.implementation_commit
        or value.get("attempt_id") != R8R_ATTEMPT_ID
        or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _load_r8u_r3_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str,
    epoch_kind: str,
    authority: R8UR3ImplementationAuthority,
    plan: Mapping[str, Any],
    candidate_total_bytes: int | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Load one exact R2-history or R3 successor artifact fail-closed."""

    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_R3_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_R3_CHAIN_ARTIFACT_KEYS.get(field)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") != status
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    if epoch_kind == "r2":
        _r8u_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if field == "r2_recovery_capacity_receipt_sha256":
            try:
                r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                    plan,
                    value,
                    r8u_scheduler_log_repair_commit=(
                        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R3_FINALIZER_R2_CAPACITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind in {"r3", "r3_capacity"}:
        _r8u_r3_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=authority.implementation_commit,
        )
        if epoch_kind == "r3_capacity":
            if type(candidate_total_bytes) is not int or candidate_total_bytes < 1:
                raise ProductionFinalizationError(
                    "R8U_R3_FINALIZER_CAPACITY_AUTHORITY_INVALID"
                )
            try:
                r8r_capacity.validate_fixed_r8u_r3_batch16_publication_resume_capacity(
                    plan,
                    value,
                    completed_extraction_candidate_seal_sha256=(
                        authority.extraction_candidate_seal_sha256
                    ),
                    completed_extraction_candidate_bytes=(
                        candidate_total_bytes
                    ),
                    r8u_candidate_authority_repair_commit=(
                        authority.implementation_commit
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R3_FINALIZER_CAPACITY_AUTHORITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    else:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _load_r8u_r4_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str | frozenset[str],
    epoch_kind: str,
    authority: R8UR4ImplementationAuthority,
    plan: Mapping[str, Any],
    candidate_total_bytes: int | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Load one exact historical or R4 artifact without importing controller."""

    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_R4_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    accepted_statuses = status if isinstance(status, frozenset) else frozenset({status})
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_R4_CHAIN_ARTIFACT_KEYS.get(field)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") not in accepted_statuses
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    if epoch_kind == "r2":
        _r8u_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if field == "r2_recovery_capacity_receipt_sha256":
            try:
                r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                    plan,
                    value,
                    r8u_scheduler_log_repair_commit=(
                        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R4_FINALIZER_R2_CAPACITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r3_fixed":
        _r8u_r3_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
        _validate_r8u_r3_candidate_static(value)
    elif epoch_kind == "r4_capacity":
        if type(candidate_total_bytes) is not int or candidate_total_bytes < 1:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CAPACITY_AUTHORITY_INVALID"
            )
        try:
            r8r_capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
                plan,
                value,
                completed_extraction_candidate_seal_sha256=(
                    authority.r3_extraction_candidate_seal_sha256
                ),
                completed_extraction_candidate_bytes=candidate_total_bytes,
                r8u_portability_repair_commit=authority.implementation_commit,
            )
        except r8r_capacity.PostReallocationCapacityError as exc:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CAPACITY_AUTHORITY_INVALID"
            ) from exc
    elif epoch_kind == "r4":
        _r8u_r4_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=authority.implementation_commit,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind != "r4_plain":
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    if (
        field == "r3_extraction_candidate_seal_sha256"
        and digest != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _load_r8u_r5_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str | frozenset[str],
    epoch_kind: str,
    authority: R8UR5ImplementationAuthority,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    """Load one exact historical or R5 artifact without controller imports."""

    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_R5_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    accepted_statuses = (
        status if isinstance(status, frozenset) else frozenset({status})
    )
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_R5_CHAIN_ARTIFACT_KEYS.get(field)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") not in accepted_statuses
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    if epoch_kind == "r2":
        _r8u_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if field == "r2_recovery_capacity_receipt_sha256":
            try:
                r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                    plan,
                    value,
                    r8u_scheduler_log_repair_commit=(
                        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R5_FINALIZER_R2_CAPACITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r3_fixed":
        _r8u_r3_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
        _validate_r8u_r3_candidate_static(value)
    elif epoch_kind == "r4_fixed":
        _r8u_r4_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r5":
        _r8u_r5_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=authority.implementation_commit,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind not in {"r4_plain", "r5_plain"}:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    if (
        field == "r3_extraction_candidate_seal_sha256"
        and digest != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _load_r8u_r6_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str | frozenset[str],
    epoch_kind: str,
    authority: R8UR6ImplementationAuthority,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    """Load one exact historical or R6 artifact without controller imports."""

    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_R6_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    accepted_statuses = (
        status if isinstance(status, frozenset) else frozenset({status})
    )
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_R6_CHAIN_ARTIFACT_KEYS.get(field)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") not in accepted_statuses
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    if epoch_kind == "r2":
        _r8u_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if field == "r2_recovery_capacity_receipt_sha256":
            try:
                r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                    plan,
                    value,
                    r8u_scheduler_log_repair_commit=(
                        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R6_FINALIZER_R2_CAPACITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r3_fixed":
        _r8u_r3_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
        _validate_r8u_r3_candidate_static(value)
    elif epoch_kind == "r4_fixed":
        _r8u_r4_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r6":
        _r8u_r6_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=authority.implementation_commit,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind not in {"r4_plain", "r6_plain"}:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    if (
        field == "r3_extraction_candidate_seal_sha256"
        and digest != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _load_r8u_r7_chain_artifact(
    *,
    path: Path,
    field: str,
    artifact_type: str,
    status: str | frozenset[str],
    epoch_kind: str,
    authority: R8UR7ImplementationAuthority,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    """Load one exact historical or R7 artifact without controller imports."""

    try:
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_R7_FINALIZER_CHAIN_ARTIFACT",
            max_bytes=128 * 1024 * 1024,
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
        ) from exc
    accepted_statuses = (
        status if isinstance(status, frozenset) else frozenset({status})
    )
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != R8U_R7_CHAIN_ARTIFACT_KEYS.get(field)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") not in accepted_statuses
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    if epoch_kind == "r2":
        _r8u_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if field == "r2_recovery_capacity_receipt_sha256":
            try:
                r8r_capacity.validate_fixed_r8u_batch16_recovery_capacity(
                    plan,
                    value,
                    r8u_scheduler_log_repair_commit=(
                        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            except r8r_capacity.PostReallocationCapacityError as exc:
                raise ProductionFinalizationError(
                    "R8U_R7_FINALIZER_R2_CAPACITY_INVALID"
                ) from exc
        elif (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r3_fixed":
        _r8u_r3_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
        _validate_r8u_r3_candidate_static(value)
    elif epoch_kind == "r4_fixed":
        _r8u_r4_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r6":
        _r8u_r6_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=(
                R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT
            ),
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit")
            != R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind == "r7":
        _r8u_r7_validate_implementation_authority_epochs(
            value.get("implementation_authority_epochs"),
            implementation_commit=authority.implementation_commit,
        )
        if (
            value.get("original_scientific_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or value.get("implementation_commit") != authority.implementation_commit
            or value.get("attempt_id") != R8R_ATTEMPT_ID
            or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            or value.get("batch_id") != "c3_batch_015"
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
            )
    elif epoch_kind not in {"r4_plain", "r6_plain", "r7_plain"}:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != getattr(authority, field):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    if (
        field == "r3_extraction_candidate_seal_sha256"
        and digest != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_HASH_MISMATCH"
        )
    return value, digest


def _r8u_exact_zero(value: Mapping[str, Any], *keys: str) -> bool:
    return all(type(value.get(key)) is int and value.get(key) == 0 for key in keys)


def _r8u_r6_exact_values(value: object, expected: Mapping[str, Any]) -> bool:
    """Require exact value and type matches for closed R6 counters."""

    return isinstance(value, Mapping) and all(
        type(value.get(key)) is type(expected_value)
        and value.get(key) == expected_value
        for key, expected_value in expected.items()
    )


def _r8u_valid_hashes(value: Mapping[str, Any], *keys: str) -> bool:
    return all(
        isinstance(value.get(key), str)
        and SHA256_RE.fullmatch(str(value[key])) is not None
        for key in keys
    )


def _r8u_r5_qsub_environment_sha256(value: object) -> str:
    """Hash one already sealed qsub environment without rebuilding it."""

    if (
        not isinstance(value, Mapping)
        or not value
        or any(
            not isinstance(name, str)
            or not isinstance(item, str)
            or not name
            or any(character in name + item for character in ("\x00", "\n", "\r"))
            for name, item in value.items()
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    return hashlib.sha256(
        core.canonical_json_bytes(
            {"environment": dict(sorted(value.items()))}
        )
    ).hexdigest()


def _validate_r8u_r5_scheduler_account(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    environment = value.get("sealed_qsub_environment")
    controlled = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
    }
    required = set(controlled) | {
        "SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL"
    }
    allowed = required | {"SGE_CELL", "SGE_QMASTER_PORT"}
    username = value.get("expected_scheduler_username")
    canonical_home = value.get("canonical_home")
    if (
        isinstance(value.get("expected_effective_uid"), bool)
        or not isinstance(value.get("expected_effective_uid"), int)
        or value.get("expected_effective_uid", -1) < 0
        or not isinstance(username, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username) is None
        or not isinstance(canonical_home, str)
        or not Path(canonical_home).is_absolute()
        or os.path.normpath(canonical_home) != canonical_home
        or value.get("submitter_passwd_lookup_available") is not True
        or not _r8u_valid_hashes(value, "runner_sha256", "python_sha256")
        or not isinstance(environment, Mapping)
        or not required <= set(environment)
        or set(environment) - allowed
        or any(environment.get(key) != item for key, item in controlled.items())
        or environment.get("SGE_ROOT")
        != "/usr/local/ogs-ge2011.11.p1/sge_root"
        or environment.get("USER") != username
        or environment.get("LOGNAME") != username
        or environment.get("HOME") != canonical_home
        or not isinstance(environment.get("SHELL"), str)
        or not Path(str(environment.get("SHELL"))).is_absolute()
        or value.get("qsub_environment_sha256")
        != _r8u_r5_qsub_environment_sha256(environment)
        or value.get("authorized_worker_roles")
        != [
            "R8U_R5_WORKER_CONTEXT_PROBE",
            "R8U_R5_BATCH16_PUBLICATION_RESUME",
            "R8U_R5_CONTINUATION_ARRAY",
            "R8U_R5_COHORT_FINALIZER",
        ]
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    cell = environment.get("SGE_CELL")
    port = environment.get("SGE_QMASTER_PORT")
    if (
        (cell is not None and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(cell)
        ) is None)
        or (
            port is not None
            and (
                re.fullmatch(r"[1-9][0-9]{0,4}", str(port)) is None
                or int(str(port)) > 65_535
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    return core.canonical_json_sha256(value)


def _r8u_r6_qsub_environment_sha256(value: object) -> str:
    """Hash the closed R6 qsub environment without ambient reconstruction."""

    try:
        return _r8u_r5_qsub_environment_sha256(value)
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        ) from exc


def _validate_r8u_r6_scheduler_account(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    environment = value.get("sealed_qsub_environment")
    controlled = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
    }
    required = set(controlled) | {
        "SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL"
    }
    allowed = required | {"SGE_CELL", "SGE_QMASTER_PORT"}
    username = value.get("expected_scheduler_username")
    canonical_home = value.get("canonical_home")
    if (
        isinstance(value.get("expected_effective_uid"), bool)
        or not isinstance(value.get("expected_effective_uid"), int)
        or value.get("expected_effective_uid", -1) < 0
        or not isinstance(username, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username) is None
        or not isinstance(canonical_home, str)
        or not Path(canonical_home).is_absolute()
        or os.path.normpath(canonical_home) != canonical_home
        or value.get("submitter_passwd_lookup_available") is not True
        or not _r8u_valid_hashes(value, "runner_sha256", "python_sha256")
        or not isinstance(environment, Mapping)
        or not required <= set(environment)
        or set(environment) - allowed
        or any(environment.get(key) != item for key, item in controlled.items())
        or environment.get("SGE_ROOT")
        != "/usr/local/ogs-ge2011.11.p1/sge_root"
        or environment.get("USER") != username
        or environment.get("LOGNAME") != username
        or environment.get("HOME") != canonical_home
        or not isinstance(environment.get("SHELL"), str)
        or not Path(str(environment.get("SHELL"))).is_absolute()
        or value.get("qsub_environment_sha256")
        != _r8u_r6_qsub_environment_sha256(environment)
        or value.get("authorized_worker_roles")
        != [
            "R8U_R6_LOCALITY_SEQUENCE_PROBE",
            "R8U_R6_BATCH16_PUBLICATION_RESUME",
            "R8U_R6_CONTINUATION_ARRAY",
            "R8U_R6_COHORT_FINALIZER",
        ]
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    cell = environment.get("SGE_CELL")
    port = environment.get("SGE_QMASTER_PORT")
    if (
        (
            cell is not None
            and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(cell)
            )
            is None
        )
        or (
            port is not None
            and (
                re.fullmatch(r"[1-9][0-9]{0,4}", str(port)) is None
                or int(str(port)) > 65_535
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    return core.canonical_json_sha256(value)


def _validate_r8u_r7_scheduler_account(value: object) -> str:
    """Validate the closed R7 submitter account without ambient reconstruction."""

    if not isinstance(value, Mapping):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    environment = value.get("sealed_qsub_environment")
    controlled = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
    }
    required = set(controlled) | {
        "SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL"
    }
    allowed = required | {"SGE_CELL", "SGE_QMASTER_PORT"}
    username = value.get("expected_scheduler_username")
    canonical_home = value.get("canonical_home")
    if (
        isinstance(value.get("expected_effective_uid"), bool)
        or not isinstance(value.get("expected_effective_uid"), int)
        or value.get("expected_effective_uid", -1) < 0
        or not isinstance(username, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username) is None
        or not isinstance(canonical_home, str)
        or not Path(canonical_home).is_absolute()
        or os.path.normpath(canonical_home) != canonical_home
        or value.get("submitter_passwd_lookup_available") is not True
        or not _r8u_valid_hashes(value, "runner_sha256", "python_sha256")
        or not isinstance(environment, Mapping)
        or not required <= set(environment)
        or set(environment) - allowed
        or any(environment.get(key) != item for key, item in controlled.items())
        or environment.get("SGE_ROOT")
        != "/usr/local/ogs-ge2011.11.p1/sge_root"
        or environment.get("USER") != username
        or environment.get("LOGNAME") != username
        or environment.get("HOME") != canonical_home
        or not isinstance(environment.get("SHELL"), str)
        or not Path(str(environment.get("SHELL"))).is_absolute()
        or value.get("qsub_environment_sha256")
        != _r8u_r5_qsub_environment_sha256(environment)
        or value.get("authorized_worker_roles")
        != [
            "R8U_R7_BATCH16_PRESERVATION_RECOVERY",
            "R8U_R7_CONTINUATION_ARRAY",
            "R8U_R7_COHORT_FINALIZER",
        ]
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    cell = environment.get("SGE_CELL")
    port = environment.get("SGE_QMASTER_PORT")
    if (
        (
            cell is not None
            and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(cell)
            )
            is None
        )
        or (
            port is not None
            and (
                re.fullmatch(r"[1-9][0-9]{0,4}", str(port)) is None
                or int(str(port)) > 65_535
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )
    return core.canonical_json_sha256(value)


def _load_r8u_r5_auxiliary_artifact(
    path: Path,
    *,
    keys: frozenset[str],
    artifact_type: str,
    status: str,
    code: str,
) -> tuple[Mapping[str, Any], str]:
    try:
        payload = _stable_nofollow_bytes(
            path, code=code, max_bytes=4 * 1024 * 1024
        )
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(code)
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(code) from exc
    if (
        not isinstance(value, Mapping)
        or payload != core.canonical_json_bytes(value)
        or set(value) != keys
        or value.get("schema_version") != 1
        or value.get("artifact_type") != artifact_type
        or value.get("status") != status
    ):
        raise ProductionFinalizationError(code)
    return value, hashlib.sha256(payload).hexdigest()


def _validate_r8u_r5_qstat_projection(
    value: object,
    *,
    job_id: str,
    job_name: str,
    expected_status: str,
) -> str:
    state_to_category = {
        "r": "running", "t": "running", "Rr": "running", "qw": "pending"
    }
    body = (
        {key: item for key, item in value.items() if key != "qstat_projection_sha256"}
        if isinstance(value, Mapping)
        else {}
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS
        or value.get("status") != expected_status
        or value.get("resume_job_id") != job_id
        or value.get("resume_job_name") != job_name
        or value.get("state") not in state_to_category
        or value.get("category") != state_to_category.get(value.get("state"))
        or value.get("target_matches") != 1
        or value.get("competing_matching_jobs") != 0
        or value.get("qstat_snapshot_count") != 1
        or value.get("qstat_projection_sha256")
        != core.canonical_json_sha256(body)
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_QSTAT_PROJECTION_INVALID"
        )
    return core.canonical_json_sha256(value)


def _validate_r8u_r6_qstat_projection(
    value: object,
    *,
    job_id: str,
    job_name: str,
    expected_status: str,
) -> str:
    try:
        return _validate_r8u_r5_qstat_projection(
            value,
            job_id=job_id,
            job_name=job_name,
            expected_status=expected_status,
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_QSTAT_PROJECTION_INVALID"
        ) from exc


def _validate_r8u_r3_candidate_static(value: object) -> None:
    """Replay every fixed candidate fact without reading scientific bodies."""

    hash_fields = (
        "stage_completion_receipt_sha256",
        "extraction_manifest_sha256",
        "dicom_audit_sha256",
        "extraction_summary_sha256",
        "technical_disposition_manifest_sha256",
        "candidate_relative_file_projection_sha256",
        "candidate_relative_directory_projection_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_root_identity_sha256",
        "source_parent_identity_sha256",
        "source_mount_identity_sha256",
        "target_parent_identity_sha256",
        "target_mount_identity_sha256",
    )
    expected = {
        "candidate_regular_files": 10_192,
        "candidate_npz_files": 10_187,
        "source_target_same_mounted_filesystem": True,
        "target_absent": True,
        "symlink_count": 0,
        "nonregular_count": 0,
        "n_selected_studies": 250,
        "n_source_objects": R8U_BATCH16_RAW_FILES,
        "source_bytes": R8U_BATCH16_RAW_BYTES,
        "n_readable": R8U_BATCH16_RAW_FILES,
        "n_unreadable": 0,
        "n_multiframe_candidates": 10_187,
        "n_single_frame": 8_490,
        "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": 10_187,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": 10_187,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
        "extraction_status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
        "cloud_requests": 0,
        "downloads": 0,
    }
    if (
        not isinstance(value, Mapping)
        or not _r8u_valid_hashes(value, *hash_fields)
        or any(
            type(value.get(key)) is not type(expected_value)
            or value.get(key) != expected_value
            for key, expected_value in expected.items()
        )
        or type(value.get("candidate_directories")) is not int
        or value.get("candidate_directories") < 1
        or type(value.get("candidate_total_bytes")) is not int
        or value.get("candidate_total_bytes") < 1
        or type(value.get("candidate_npz_bytes")) is not int
        or not 0 < value.get("candidate_npz_bytes") <= value.get(
            "candidate_total_bytes"
        )
        or value.get("source_mount_identity_sha256")
        != value.get("target_mount_identity_sha256")
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_EXTRACTION_CANDIDATE_INVALID"
        )


def _r8u_r3_expected_proceedable_probe_outcome(
    primary_result: object,
) -> tuple[int, str, bool] | None:
    errno_number_by_result = {
        "RENAME_NOREPLACE_SUPPORTED": 0,
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL": errno.EINVAL,
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS": errno.ENOSYS,
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP": getattr(
            errno, "EOPNOTSUPP", errno.ENOTSUP
        ),
    }
    if primary_result not in R8U_R3_PROCEEDABLE_PROBE_RESULTS:
        return None
    errno_number = errno_number_by_result.get(primary_result)
    if type(errno_number) is not int:
        return None
    return (
        errno_number,
        (
            "NONE"
            if errno_number == 0
            else errno.errorcode.get(errno_number, "UNKNOWN")
        ),
        primary_result == "RENAME_NOREPLACE_SUPPORTED",
    )


def _validate_r8u_r3_process_projection(
    value: object, *, expected_status: str
) -> str:
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R3_PROCESS_PROJECTION_KEYS
        or value.get("status") != expected_status
        or type(value.get("matching_processes")) is not int
        or value.get("matching_processes") != 0
        or type(value.get("process_snapshot_count")) is not int
        or value.get("process_snapshot_count") != 1
        or not _r8u_valid_hashes(
            value, "ps_argv_sha256", "ps_stdout_sha256"
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_PROCESS_PROJECTION_INVALID"
        )
    return core.canonical_json_sha256(value)


def _validate_r8u_r3_initial_qstat_projection(
    value: object, *, resume_job_id: str, resume_job_name: str
) -> str:
    state_to_category = {
        "r": "running",
        "t": "running",
        "Rr": "running",
        "qw": "pending",
    }
    projection_body = (
        {
            key: observed
            for key, observed in value.items()
            if key != "qstat_projection_sha256"
        }
        if isinstance(value, Mapping)
        else {}
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS
        or value.get("status")
        != "PASS_EXACT_ONE_R8U_R3_RESUME_JOB_ZERO_COMPETITORS"
        or value.get("resume_job_id") != resume_job_id
        or value.get("resume_job_name") != resume_job_name
        or value.get("state") not in state_to_category
        or value.get("category") != state_to_category.get(value.get("state"))
        or type(value.get("target_matches")) is not int
        or value.get("target_matches") != 1
        or type(value.get("competing_matching_jobs")) is not int
        or value.get("competing_matching_jobs") != 0
        or type(value.get("qstat_snapshot_count")) is not int
        or value.get("qstat_snapshot_count") != 1
        or value.get("qstat_projection_sha256")
        != core.canonical_json_sha256(projection_body)
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_QSTAT_PROJECTION_INVALID"
        )
    return core.canonical_json_sha256(value)


def _r8u_r3_real_rename_classification(errno_number: int) -> str:
    if errno_number == 0:
        return "RENAME_RETURNED_SUCCESS"
    if errno_number in {
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }:
        return "RENAME_ERROR_UNSUPPORTED"
    if errno_number == errno.EXDEV:
        return "RENAME_ERROR_CROSS_MOUNT"
    if errno_number in {errno.EACCES, errno.EPERM}:
        return "RENAME_ERROR_PERMISSION"
    if errno_number in {errno.EEXIST, errno.ENOTEMPTY}:
        return "RENAME_ERROR_COLLISION"
    if errno_number == errno.ENOENT:
        return "RENAME_ERROR_SOURCE_MISSING"
    if errno_number == errno.EIO:
        return "RENAME_ERROR_IO"
    return "RENAME_ERROR_OTHER"


def _validate_r8u_r3_real_rename_outcome(
    *,
    returned_success: object,
    errno_number: object,
    errno_name: object,
    errno_classification: object,
    publication_ruling: object,
) -> None:
    expected_name = (
        "NONE"
        if errno_number == 0
        else (
            errno.errorcode.get(errno_number, "UNKNOWN")
            if type(errno_number) is int
            else ""
        )
    )
    if (
        type(returned_success) is not bool
        or type(errno_number) is not int
        or errno_number < 0
        or not isinstance(errno_name, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", errno_name) is None
        or errno_name != expected_name
        or errno_classification
        != _r8u_r3_real_rename_classification(errno_number)
        or (returned_success and errno_number != 0)
        or (not returned_success and errno_number <= 0)
        or publication_ruling
        != (
            "PUBLICATION_PASS"
            if returned_success
            else "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_PUBLICATION_RECEIPT_INVALID"
        )


def _validate_r8u_r3_resume_accounting(
    value: Mapping[str, Any], *, resume_job_id: str
) -> None:
    """Require the later R3 scheduler accounting to close at failed=0/exit=0."""

    accounting_projection = value.get("accounting_projection")
    try:
        _validate_r8r_recovery_accounting(
            accounting_projection, expected_job_id=resume_job_id
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_ACCOUNTING_INVALID"
        ) from exc
    if (
        type(value.get("original_task_id")) is not int
        or value.get("original_task_id") != 16
        or value.get("resume_job_id") != resume_job_id
        or type(value.get("failed")) is not int
        or value.get("failed") != 0
        or type(value.get("exit_status")) is not int
        or value.get("exit_status") != 0
        or not isinstance(accounting_projection, Mapping)
        or value.get("failed") != accounting_projection.get("failed")
        or value.get("exit_status")
        != accounting_projection.get("exit_status")
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_ACCOUNTING_INVALID"
        )


def _r8u_failed_r1_epoch_authority_sha256(value: object) -> str:
    """Validate and hash the exact immutable failed job-7352656 authority."""

    if not isinstance(value, Mapping):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_FAILED_RECOVERY_EPOCH_AUTHORITY_INVALID"
        )
    scheduler_evidence = value.get("scheduler_evidence")
    expected_scheduler_evidence = {
        "artifact_type": "lvef_c3_r8u_r2_scheduler_log_evidence_v1",
        "status": (
            "PASS_ROLE_BOUND_GRID_ENGINE_MERGED_STDOUT_STDERR_LOG"
        ),
        "evidence_class": "GRID_ENGINE_MERGED_SCHEDULER_EVIDENCE",
        "role": "FAILED_R8U_BATCH16_RECOVERY",
        "basename": (
            f"{R8U_FAILED_R1_RECOVERY_JOB_NAME}.o"
            f"{R8U_FAILED_R1_RECOVERY_JOB_ID}"
        ),
        "job_id": R8U_FAILED_R1_RECOVERY_JOB_ID,
        "task_id": "NONE",
        "mode": "0644",
        "size_bytes": 116,
        "sha256": R8U_FAILED_R1_RECOVERY_LOG_SHA256,
        "owner_uid": os.geteuid(),
        "terminal_state": "TERMINAL_FAILED_APPLICATION_EXIT_78",
    }
    expected = {
        "artifact_type": (
            "lvef_c3_r8u_r1_failed_recovery_epoch_authority_v1"
        ),
        "status": "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78",
        "implementation_commit": R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        "job_id": R8U_FAILED_R1_RECOVERY_JOB_ID,
        "job_name": R8U_FAILED_R1_RECOVERY_JOB_NAME,
        "qsub_exit": 0,
        "qacct_failed": 0,
        "qacct_exit_status": 78,
        "qacct_task_id": "NONE",
        "terminal_code": "BLOCKED_R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
        "scheduler_evidence": expected_scheduler_evidence,
        "namespace_file_count": 8,
        "namespace_inventory_sha256": (
            R8U_FAILED_R1_NAMESPACE_INVENTORY_SHA256
        ),
        "dicom_body_reads": 0,
        "cloud_requests": 0,
        "download_reruns": 0,
        "extraction_reruns": 0,
        "echoprime_reruns": 0,
        "embedding_generations": 0,
    }
    if (
        set(value) != R8U_FAILED_R1_EPOCH_AUTHORITY_KEYS
        or not isinstance(scheduler_evidence, Mapping)
        or set(scheduler_evidence) != R8U_FAILED_R1_SCHEDULER_EVIDENCE_KEYS
        or not all(
            type(value.get(key)) is type(expected_value)
            and value.get(key) == expected_value
            for key, expected_value in expected.items()
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_FAILED_RECOVERY_EPOCH_AUTHORITY_INVALID"
        )
    return core.canonical_json_sha256(value)


def _validate_r8u_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate the historical R8R chain and richer exact R8U successor."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root
            / "batches"
            / batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    failed_partial_path = (
        attempt_root
        / "r8u_r2_batch16_recovery"
        / "failed_partial_seal.restricted.json"
    )
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or failed_partial_path in receipt_paths_by_batch.values()
        or authority.failed_partial_seal_sha256
        in set(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    for field, relative_path, artifact_type, status in R8U_CHAIN_ARTIFACT_SPECS:
        value, digest = _load_r8u_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            authority=authority,
            plan=plan,
        )
        values[field] = value
        observed[field] = digest

    seal = values["failed_partial_seal_sha256"]
    recovery = values["recovery_authority_sha256"]
    recovery_submission = values["recovery_submission_receipt_sha256"]
    accounting = values["recovery_accounting_sha256"]
    terminal = values["recovery_terminal_receipt_sha256"]
    claim = values["continuation_claim_sha256"]
    continuation_submission = values[
        "continuation_submission_receipt_sha256"
    ]

    def exact_values(
        value: object, expected: Mapping[str, Any]
    ) -> bool:
        return isinstance(value, Mapping) and all(
            type(value.get(key)) is type(expected_value)
            and value.get(key) == expected_value
            for key, expected_value in expected.items()
        )

    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )
    observation = seal.get("observation")
    if (
        seal.get("batch_id") != "c3_batch_015"
        or type(seal.get("original_task_id")) is not int
        or seal.get("original_task_id") != 16
        or seal.get("failed_array_job_id") != "7292691"
        or seal.get("failure_class")
        != "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS"
        or not isinstance(observation, Mapping)
        or set(observation) != R8U_FAILED_PARTIAL_OBSERVATION_KEYS
        or not exact_values(
            observation,
            {
                "file_count": 4_757,
                "directory_count": 259,
                "total_bytes": 8_583_119_701,
                "symlink_count": 0,
                "nonregular_count": 0,
                "metadata_projection_sha256": (
                    R8U_FAILED_PARTIAL_METADATA_SHA256
                ),
                "npz_body_reads": 0,
            },
        )
        or seal.get("partial_outputs_adopted") is not False
        or seal.get("partial_outputs_modified") is not False
        or seal.get("partial_outputs_deleted") is not False
        or seal.get("partial_outputs_renamed") is not False
        or type(seal.get("npz_body_reads")) is not int
        or seal.get("npz_body_reads") != 0
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_FAILED_PARTIAL_SEAL_INVALID"
        )

    prefix15 = [
        R8U_PREFIX_RECEIPT_AUTHORITIES[f"c3_batch_{index:03d}"][1]
        for index in range(15)
    ]
    prefix16 = [
        *prefix15,
        receipt_hashes_by_batch["c3_batch_015"],
    ]
    retained_raw = recovery.get("retained_raw_authority")
    try:
        current_script_authority = _r8r_current_script_authority()
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    failed_r1_epoch_sha256 = _r8u_failed_r1_epoch_authority_sha256(
        recovery.get("failed_r8u_recovery_epoch_authority")
    )
    if (
        recovery.get("prior_implementation_commit")
        != R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        or recovery.get("batch_id") != "c3_batch_015"
        or type(recovery.get("original_task_id")) is not int
        or recovery.get("original_task_id") != 16
        or recovery.get("continuation_task_range") != "17-19"
        or recovery.get("prefix_final_receipt_sha256") != prefix15
        or recovery.get("historical_r8r_chain_authority")
        != historical_hashes
        or recovery.get("failed_r8u_recovery_epoch_authority_sha256")
        != failed_r1_epoch_sha256
        or recovery.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or recovery.get("recovery_capacity_sha256")
        != observed["recovery_capacity_receipt_sha256"]
        or not isinstance(retained_raw, Mapping)
        or set(retained_raw) != R8U_RETAINED_RAW_AUTHORITY_KEYS
        or not exact_values(
            retained_raw,
            {
                "raw_dicom_files": R8U_BATCH16_RAW_FILES,
                "raw_dicom_bytes": R8U_BATCH16_RAW_BYTES,
                "download_ledger_sha256": (
                    R8U_BATCH16_DOWNLOAD_LEDGER_SHA256
                ),
                "verified_download_manifest_sha256": (
                    R8U_BATCH16_VERIFIED_MANIFEST_SHA256
                ),
                "selected_batch_manifest_sha256": (
                    R8U_BATCH16_SELECTED_MANIFEST_SHA256
                ),
                "historical_st_dev_required": False,
                "raw_dicom_body_reads": 0,
                "cloud_requests": 0,
            },
        )
        or not _r8u_valid_hashes(
            retained_raw,
            "raw_metadata_projection_sha256",
            "download_control_projection_sha256",
        )
        or recovery.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or SHA256_RE.fullmatch(
            str(recovery.get("qsub_environment_sha256"))
        )
        is None
        or recovery.get("script_authority") != current_script_authority
        or recovery.get("runtime_validation_context")
        != "SEALED_SCHEDULER_RUNTIME_REPLAY"
        or recovery.get("fresh_extraction_relative_root")
        != "r8u_r2_batch16_recovery/fresh_extracted_cache/c3_batch_015"
        or not _r8u_exact_zero(
            recovery,
            "cloud_requests_authorized",
            "download_reruns_authorized",
        )
        or any(
            type(recovery.get(key)) is not int
            or recovery.get(key) != 1
            for key in (
                "dicom_extraction_reruns_authorized",
                "echoprime_reruns_authorized",
                "gpu_executions_authorized",
            )
        )
        or any(
            recovery.get(key) is not False
            for key in (
                "failed_partial_adoption_authorized",
                "failed_partial_mutation_authorized",
                "raw_dicom_deletion_authorized",
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
        or type(recovery.get("maximum_new_qsub_submissions")) is not int
        or recovery.get("maximum_new_qsub_submissions") != 3
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_AUTHORITY_INVALID"
        )

    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(
        Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
    )
    recovery_scheduler_root = (
        attempt_root / "r8u_r2_batch16_recovery" / "scheduler"
    )
    continuation_scheduler_root = (
        attempt_root / "r8u_r2_continuation_17_19" / "scheduler"
    )
    common_qsub = [
        qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho",
    ]
    recovery_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_rec_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(recovery_scheduler_root),
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    zero_scientific_keys = (
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    )
    if (
        recovery_submission.get("batch_id") != "c3_batch_015"
        or type(recovery_submission.get("original_task_id")) is not int
        or recovery_submission.get("original_task_id") != 16
        or recovery_submission.get("recovery_job_name")
        != f"lvef_c3_r8u_rec_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", recovery_job_id) is None
        or recovery_submission.get("recovery_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": recovery_command})
        or recovery_submission.get("qsub_environment_sha256")
        != recovery.get("qsub_environment_sha256")
        or recovery_submission.get("recovery_authority_sha256")
        != observed["recovery_authority_sha256"]
        or recovery_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or recovery_submission.get("recovery_capacity_sha256")
        != observed["recovery_capacity_receipt_sha256"]
        or type(recovery_submission.get("scheduler_submission_count")) is not int
        or recovery_submission.get("scheduler_submission_count") != 1
        or recovery_submission.get("recovery_is_array") is not False
        or recovery_submission.get("gpu_requested") is not True
        or recovery_submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            recovery_submission,
            "cloud_requests",
            "download_reruns",
            *zero_scientific_keys,
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            recovery_submission.get("recovery_qsub_evidence"),
            accepted_stdout=[
                f"{recovery_job_id}\n".encode("ascii"),
                recovery_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_SUBMISSION_INVALID"
        ) from exc

    accounting_projection = accounting.get("accounting_projection")
    try:
        _validate_r8r_recovery_accounting(
            accounting_projection, expected_job_id=recovery_job_id
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        ) from exc
    if (
        accounting.get("batch_id") != "c3_batch_015"
        or type(accounting.get("original_task_id")) is not int
        or accounting.get("original_task_id") != 16
        or accounting.get("recovery_job_id") != recovery_job_id
        or type(accounting.get("failed")) is not int
        or accounting.get("failed") != 0
        or type(accounting.get("exit_status")) is not int
        or accounting.get("exit_status") != 0
        or not isinstance(accounting_projection, Mapping)
        or accounting.get("failed") != accounting_projection.get("failed")
        or accounting.get("exit_status")
        != accounting_projection.get("exit_status")
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        )

    fresh_path = (
        attempt_root
        / "r8u_r2_batch16_recovery"
        / "fresh_extraction_publication.restricted.json"
    )
    try:
        fresh_payload = _stable_nofollow_bytes(
            fresh_path,
            code="R8U_FINALIZER_FRESH_PUBLICATION",
            max_bytes=128 * 1024 * 1024,
        )
        fresh = json.loads(
            fresh_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProductionFinalizationError(
                    "R8U_FINALIZER_FRESH_PUBLICATION_INVALID"
                )
            ),
        )
    except ProductionFinalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_FRESH_PUBLICATION_INVALID"
        ) from exc
    fresh_sha256 = hashlib.sha256(fresh_payload).hexdigest()
    fresh_stage_payload = _stable_nofollow_bytes(
        attempt_root
        / "extracted_cache"
        / "c3_batch_015"
        / "dicom_extraction"
        / "stage_completion_receipt.restricted.json",
        code="R8U_FINALIZER_FRESH_STAGE_COMPLETION",
        max_bytes=128 * 1024 * 1024,
    )
    fresh_stage_sha256 = hashlib.sha256(fresh_stage_payload).hexdigest()
    raw_content = (
        fresh.get("raw_content_authority")
        if isinstance(fresh, Mapping)
        else None
    )
    if (
        not isinstance(fresh, Mapping)
        or fresh_payload != core.canonical_json_bytes(fresh)
        or set(fresh) != R8U_FRESH_PUBLICATION_KEYS
        or type(fresh.get("schema_version")) is not int
        or fresh.get("schema_version") != 1
        or fresh.get("artifact_type")
        != "lvef_c3_r8u_r2_fresh_batch16_extraction_publication_v1"
        or fresh.get("status")
        != "PASS_FRESH_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER"
        or fresh.get("original_scientific_commit")
        != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or fresh.get("implementation_commit")
        != authority.implementation_commit
        or fresh.get("implementation_authority_epochs")
        != _r8u_expected_implementation_authority_epochs(
            authority.implementation_commit
        )
        or fresh.get("attempt_id") != R8R_ATTEMPT_ID
        or fresh.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or fresh.get("batch_id") != "c3_batch_015"
        or fresh.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or not isinstance(raw_content, Mapping)
        or set(raw_content) != R8U_RAW_CONTENT_AUTHORITY_KEYS
        or not exact_values(
            raw_content,
            {
                "raw_dicom_files": R8U_BATCH16_RAW_FILES,
                "raw_dicom_bytes": R8U_BATCH16_RAW_BYTES,
                "download_ledger_sha256": (
                    R8U_BATCH16_DOWNLOAD_LEDGER_SHA256
                ),
                "verified_download_manifest_sha256": (
                    R8U_BATCH16_VERIFIED_MANIFEST_SHA256
                ),
                "selected_batch_manifest_sha256": (
                    R8U_BATCH16_SELECTED_MANIFEST_SHA256
                ),
                "historical_st_dev_required": False,
                "raw_dicom_body_reads": R8U_BATCH16_RAW_FILES,
                "cloud_requests": 0,
                "status": "PASS_BATCH16_RETAINED_RAW_CONTENT_AUTHORITY",
            },
        )
        or not _r8u_valid_hashes(
            raw_content,
            "raw_metadata_projection_sha256",
            "download_control_projection_sha256",
        )
        or raw_content.get("raw_metadata_projection_sha256")
        != retained_raw.get("raw_metadata_projection_sha256")
        or raw_content.get("download_control_projection_sha256")
        != retained_raw.get("download_control_projection_sha256")
        or SHA256_RE.fullmatch(
            str(fresh.get("stage_completion_receipt_sha256"))
        )
        is None
        or fresh.get("stage_completion_receipt_sha256")
        != fresh_stage_sha256
        or fresh.get("fresh_stage_promoted_to_canonical") is not True
        or fresh.get("canonical_target_absent_before_promotion") is not True
        or fresh.get("failed_partial_modified") is not False
        or not _r8u_exact_zero(
            fresh,
            "partial_npz_adopted",
            "cloud_requests",
            "download_reruns",
        )
        or type(fresh.get("dicom_extraction_reruns")) is not int
        or fresh.get("dicom_extraction_reruns") != 1
        or terminal.get("fresh_extraction_publication_sha256")
        != fresh_sha256
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_FRESH_PUBLICATION_INVALID"
        )
    observed["fresh_extraction_publication_sha256"] = fresh_sha256

    batch16_receipt_payload = _stable_nofollow_bytes(
        receipt_paths_by_batch["c3_batch_015"],
        code="R8U_FINALIZER_BATCH16_RECEIPT",
        max_bytes=128 * 1024 * 1024,
    )
    try:
        batch16_receipt = json.loads(
            batch16_receipt_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_BATCH16_RECEIPT_INVALID"
        ) from exc
    terminal_hash_keys = (
        "failed_partial_seal_sha256",
        "recovery_capacity_sha256",
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "fresh_extraction_publication_sha256",
        "preservation_receipt_sha256",
        "cache_retirement_authorization_sha256",
        "cache_retirement_transition_sha256",
        "final_ledger_sha256",
        "batch_finalization_receipt_sha256",
    )
    terminal_receipt_fields = (
        "n_selected_studies",
        "n_expected_objects",
        "expected_source_bytes",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_clip_embeddings",
        "n_pooled_studies",
        "n_no_cine_studies",
        "n_new_no_cine_studies",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
    )
    batch16_root = attempt_root / "batches" / "c3_batch_015"
    terminal_support_paths = {
        "preservation_receipt_sha256": (
            batch16_root
            / "preservation"
            / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root
            / "cache_retirement_authorizations"
            / "c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            batch16_root
            / "preservation"
            / "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            batch16_root / "final_resume_ledger.restricted.json"
        ),
    }
    terminal_support_hashes: dict[str, str] = {}
    for field, path in terminal_support_paths.items():
        payload = _stable_nofollow_bytes(
            path,
            code="R8U_FINALIZER_TERMINAL_SUPPORT",
            max_bytes=128 * 1024 * 1024,
        )
        terminal_support_hashes[field] = hashlib.sha256(payload).hexdigest()
    if (
        not isinstance(batch16_receipt, Mapping)
        or batch16_receipt_payload != core.canonical_json_bytes(batch16_receipt)
        or hashlib.sha256(batch16_receipt_payload).hexdigest()
        != receipt_hashes_by_batch["c3_batch_015"]
        or terminal.get("batch_id") != "c3_batch_015"
        or type(terminal.get("original_task_id")) is not int
        or terminal.get("original_task_id") != 16
        or not _r8u_valid_hashes(terminal, *terminal_hash_keys)
        or terminal.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or terminal.get("recovery_capacity_sha256")
        != observed["recovery_capacity_receipt_sha256"]
        or terminal.get("recovery_authority_sha256")
        != observed["recovery_authority_sha256"]
        or terminal.get("recovery_submission_receipt_sha256")
        != observed["recovery_submission_receipt_sha256"]
        or terminal.get("fresh_extraction_publication_sha256")
        != fresh_sha256
        or terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
        or any(
            terminal.get(field) != digest
            for field, digest in terminal_support_hashes.items()
        )
        or terminal.get("cache_retirement_authorization_sha256")
        != batch16_receipt.get("cache_retirement_authorization_sha256")
        or any(
            type(terminal.get(key)) is not type(batch16_receipt.get(key))
            or terminal.get(key) != batch16_receipt.get(key)
            for key in terminal_receipt_fields
        )
        or terminal.get("raw_dicoms_retained") is not True
        or terminal.get("raw_dicoms_retained")
        is not batch16_receipt.get("raw_dicoms_retained")
        or terminal.get("fresh_recovery_cache_retired") is not True
        or terminal.get("fresh_recovery_cache_retired")
        is not batch16_receipt.get("extracted_cache_retired")
        or terminal.get("failed_partial_cache_retained") is not True
        or terminal.get("batch16_raw_reused") is not True
        or type(terminal.get("failed_partial_files")) is not int
        or terminal.get("failed_partial_files") != 4_757
        or type(terminal.get("failed_partial_bytes")) is not int
        or terminal.get("failed_partial_bytes") != 8_583_119_701
        or not _r8u_exact_zero(
            terminal,
            "cloud_requests",
            "download_reruns",
            "n_blocking_failures",
            "n_new_no_cine_studies",
            "object_substitution_count",
            "unaccounted_multiframe_objects",
            *zero_scientific_keys,
        )
        or any(
            type(terminal.get(key)) is not int
            or terminal.get(key) != 1
            for key in (
                "dicom_extraction_reruns",
                "echoprime_reruns",
                "embedding_generations",
                "gpu_executions",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_RECOVERY_TERMINAL_INVALID"
        )

    if (
        claim.get("prior_implementation_commit")
        != R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        or claim.get("prefix_final_receipt_sha256") != prefix16
        or claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or claim.get("recovery_capacity_sha256")
        != observed["recovery_capacity_receipt_sha256"]
        or claim.get("recovery_authority_sha256")
        != observed["recovery_authority_sha256"]
        or claim.get("recovery_submission_receipt_sha256")
        != observed["recovery_submission_receipt_sha256"]
        or claim.get("recovery_accounting_sha256")
        != observed["recovery_accounting_sha256"]
        or claim.get("recovery_terminal_receipt_sha256")
        != observed["recovery_terminal_receipt_sha256"]
        or claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or claim.get("qsub_environment_sha256")
        != recovery_submission.get("qsub_environment_sha256")
        or claim.get("script_authority") != current_script_authority
        or claim.get("continuation_task_range") != "17-19"
        or type(claim.get("continuation_task_count")) is not int
        or claim.get("continuation_task_count") != 3
        or type(claim.get("continuation_max_concurrency")) is not int
        or claim.get("continuation_max_concurrency") != 1
        or type(claim.get("held_finalizer_count")) is not int
        or claim.get("held_finalizer_count") != 1
        or type(claim.get("total_new_qsub_maximum")) is not int
        or claim.get("total_new_qsub_maximum") != 3
        or claim.get("automatic_retry_authorized") is not False
        or claim.get("whole_stage_retry_authorized") is not False
        or claim.get("fourth_submission_reachable") is not False
        or not _r8u_exact_zero(
            claim,
            "cloud_requests_by_submitter",
            "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter",
            "embedding_generations_by_submitter",
        )
        or any(
            claim.get(key) is not False
            for key in (
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(
        continuation_submission.get("finalizer_job_id", "")
    )
    array_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_seq_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-t",
        "17-19",
        "-tc",
        "1",
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_fin_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        runner,
    ]
    if (
        continuation_submission.get("recovery_job_id") != recovery_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != claim.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or continuation_submission.get("recovery_capacity_sha256")
        != observed["recovery_capacity_receipt_sha256"]
        or continuation_submission.get("recovery_authority_sha256")
        != observed["recovery_authority_sha256"]
        or continuation_submission.get("recovery_accounting_sha256")
        != observed["recovery_accounting_sha256"]
        or continuation_submission.get("recovery_terminal_receipt_sha256")
        != observed["recovery_terminal_receipt_sha256"]
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or type(continuation_submission.get("scheduler_submission_count"))
        is not int
        or continuation_submission.get("scheduler_submission_count") != 2
        or type(continuation_submission.get("total_new_qsub_submissions"))
        is not int
        or continuation_submission.get("total_new_qsub_submissions") != 3
        or type(continuation_submission.get("scheduler_submission_maximum"))
        is not int
        or continuation_submission.get("scheduler_submission_maximum") != 3
        or continuation_submission.get("array_task_range") != "17-19"
        or type(continuation_submission.get("array_task_count")) is not int
        or continuation_submission.get("array_task_count") != 3
        or type(continuation_submission.get("array_max_concurrency")) is not int
        or continuation_submission.get("array_max_concurrency") != 1
        or continuation_submission.get("finalizer_held_on_array") is not True
        or continuation_submission.get("whole_stage_retry_authorized")
        is not False
        or continuation_submission.get("fourth_submission_reachable")
        is not False
        or not _r8u_exact_zero(
            continuation_submission,
            "cloud_requests",
            "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "gpu_executions_by_submitter",
            *zero_scientific_keys,
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode("ascii"),
                f"{array_job_id}.17-19:1".encode("ascii"),
                f"{array_job_id}\n".encode("ascii"),
                array_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode("ascii"),
                finalizer_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": (
                historical_chain_sha256
            ),
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _validate_r8u_r3_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR3ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate the closed R2 failure and R3 publication-resume successor."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root
            / "batches"
            / batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    chain_paths = {
        field: attempt_root / relative_path
        for field, relative_path, _artifact_type, _status, _epoch_kind
        in R8U_R3_CHAIN_ARTIFACT_SPECS
    }
    current_hashes = {
        getattr(authority, field) for field in chain_paths
    }
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or len(chain_paths) != len(R8U_R3_CHAIN_ARTIFACT_SPECS)
        or len(set(chain_paths.values())) != len(chain_paths)
        or any(path in receipt_paths_by_batch.values() for path in chain_paths.values())
        or not current_hashes.isdisjoint(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )
    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    candidate_total_bytes: int | None = None
    for field, relative_path, artifact_type, status, epoch_kind in (
        R8U_R3_CHAIN_ARTIFACT_SPECS
    ):
        value, digest = _load_r8u_r3_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            epoch_kind=epoch_kind,
            authority=authority,
            plan=plan,
            candidate_total_bytes=candidate_total_bytes,
        )
        values[field] = value
        observed[field] = digest
        if field == "extraction_candidate_seal_sha256":
            raw_candidate_bytes = value.get("candidate_total_bytes")
            if type(raw_candidate_bytes) is int:
                candidate_total_bytes = raw_candidate_bytes

    seal = values["failed_partial_seal_sha256"]
    r2_capacity = values["r2_recovery_capacity_receipt_sha256"]
    r2_authority = values["r2_recovery_authority_sha256"]
    r2_submission = values["r2_recovery_submission_receipt_sha256"]
    candidate = values["extraction_candidate_seal_sha256"]
    probe = values["publication_primitive_probe_sha256"]
    publication_claim = values["publication_claim_sha256"]
    publication = values["publication_receipt_sha256"]
    resume_capacity = values["resume_capacity_receipt_sha256"]
    resume_authority = values["resume_authority_sha256"]
    resume_submission = values["resume_submission_receipt_sha256"]
    resume_accounting = values["resume_accounting_sha256"]
    terminal = values["resume_terminal_receipt_sha256"]
    continuation_claim = values["continuation_claim_sha256"]
    continuation_submission = values[
        "continuation_submission_receipt_sha256"
    ]

    def exact_values(
        value: object, expected: Mapping[str, Any]
    ) -> bool:
        return isinstance(value, Mapping) and all(
            type(value.get(key)) is type(expected_value)
            and value.get(key) == expected_value
            for key, expected_value in expected.items()
        )

    observation = seal.get("observation")
    if (
        seal.get("batch_id") != "c3_batch_015"
        or type(seal.get("original_task_id")) is not int
        or seal.get("original_task_id") != 16
        or seal.get("failed_array_job_id") != "7292691"
        or seal.get("failure_class")
        != "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS"
        or not isinstance(observation, Mapping)
        or set(observation) != R8U_FAILED_PARTIAL_OBSERVATION_KEYS
        or not exact_values(
            observation,
            {
                "file_count": 4_757,
                "directory_count": 259,
                "total_bytes": 8_583_119_701,
                "symlink_count": 0,
                "nonregular_count": 0,
                "metadata_projection_sha256": (
                    R8U_FAILED_PARTIAL_METADATA_SHA256
                ),
                "npz_body_reads": 0,
            },
        )
        or seal.get("partial_outputs_adopted") is not False
        or seal.get("partial_outputs_modified") is not False
        or seal.get("partial_outputs_deleted") is not False
        or seal.get("partial_outputs_renamed") is not False
        or not _r8u_exact_zero(seal, "npz_body_reads")
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_FAILED_PARTIAL_SEAL_INVALID"
        )

    prefix15 = [
        R8U_PREFIX_RECEIPT_AUTHORITIES[f"c3_batch_{index:03d}"][1]
        for index in range(15)
    ]
    prefix16 = [*prefix15, receipt_hashes_by_batch["c3_batch_015"]]
    retained_raw = r2_authority.get("retained_raw_authority")
    failed_r1_epoch_sha256 = _r8u_failed_r1_epoch_authority_sha256(
        r2_authority.get("failed_r8u_recovery_epoch_authority")
    )
    if (
        r2_authority.get("prior_implementation_commit")
        != R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        or r2_authority.get("batch_id") != "c3_batch_015"
        or type(r2_authority.get("original_task_id")) is not int
        or r2_authority.get("original_task_id") != 16
        or r2_authority.get("continuation_task_range") != "17-19"
        or r2_authority.get("prefix_final_receipt_sha256") != prefix15
        or r2_authority.get("historical_r8r_chain_authority")
        != historical_hashes
        or r2_authority.get("failed_r8u_recovery_epoch_authority_sha256")
        != failed_r1_epoch_sha256
        or r2_authority.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or r2_authority.get("recovery_capacity_sha256")
        != observed["r2_recovery_capacity_receipt_sha256"]
        or not isinstance(retained_raw, Mapping)
        or set(retained_raw) != R8U_RETAINED_RAW_AUTHORITY_KEYS
        or not exact_values(
            retained_raw,
            {
                "raw_dicom_files": R8U_BATCH16_RAW_FILES,
                "raw_dicom_bytes": R8U_BATCH16_RAW_BYTES,
                "download_ledger_sha256": (
                    R8U_BATCH16_DOWNLOAD_LEDGER_SHA256
                ),
                "verified_download_manifest_sha256": (
                    R8U_BATCH16_VERIFIED_MANIFEST_SHA256
                ),
                "selected_batch_manifest_sha256": (
                    R8U_BATCH16_SELECTED_MANIFEST_SHA256
                ),
                "historical_st_dev_required": False,
                "raw_dicom_body_reads": 0,
                "cloud_requests": 0,
            },
        )
        or not _r8u_valid_hashes(
            retained_raw,
            "raw_metadata_projection_sha256",
            "download_control_projection_sha256",
        )
        or r2_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or r2_authority.get("script_authority")
        != R8U_R2_SCHEDULER_LOG_REPAIR_SCRIPT_AUTHORITY
        or r2_authority.get("runtime_validation_context")
        != "SEALED_SCHEDULER_RUNTIME_REPLAY"
        or r2_authority.get("fresh_extraction_relative_root")
        != "r8u_r2_batch16_recovery/fresh_extracted_cache/c3_batch_015"
        or not _r8u_exact_zero(
            r2_authority,
            "cloud_requests_authorized",
            "download_reruns_authorized",
        )
        or any(
            type(r2_authority.get(key)) is not int
            or r2_authority.get(key) != 1
            for key in (
                "dicom_extraction_reruns_authorized",
                "echoprime_reruns_authorized",
                "gpu_executions_authorized",
            )
        )
        or any(
            r2_authority.get(key) is not False
            for key in (
                "failed_partial_adoption_authorized",
                "failed_partial_mutation_authorized",
                "raw_dicom_deletion_authorized",
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
        or type(r2_authority.get("maximum_new_qsub_submissions")) is not int
        or r2_authority.get("maximum_new_qsub_submissions") != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_R2_RECOVERY_AUTHORITY_INVALID"
        )

    r2_job_id = str(r2_submission.get("recovery_job_id", ""))
    if (
        r2_job_id != R8U_R2_COMPLETED_EXTRACTION_JOB_ID
        or r2_submission.get("batch_id") != "c3_batch_015"
        or type(r2_submission.get("original_task_id")) is not int
        or r2_submission.get("original_task_id") != 16
        or r2_submission.get("recovery_job_name")
        != "lvef_c3_r8u_rec_4fd8f4bf"
        or not _r8u_valid_hashes(
            r2_submission,
            "recovery_qsub_argv_sha256",
            "qsub_environment_sha256",
        )
        or r2_submission.get("qsub_environment_sha256")
        != r2_authority.get("qsub_environment_sha256")
        or r2_submission.get("recovery_authority_sha256")
        != observed["r2_recovery_authority_sha256"]
        or r2_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or r2_submission.get("recovery_capacity_sha256")
        != observed["r2_recovery_capacity_receipt_sha256"]
        or type(r2_submission.get("scheduler_submission_count")) is not int
        or r2_submission.get("scheduler_submission_count") != 1
        or r2_submission.get("recovery_is_array") is not False
        or r2_submission.get("gpu_requested") is not True
        or r2_submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            r2_submission,
            "cloud_requests",
            "download_reruns",
            "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_R2_RECOVERY_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            r2_submission.get("recovery_qsub_evidence"),
            accepted_stdout=[b"7354951\n", b"7354951"],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_R2_RECOVERY_SUBMISSION_INVALID"
        ) from exc
    if r2_capacity.get("batch_id") != "c3_batch_015":
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_R2_CAPACITY_INVALID"
        )

    _validate_r8u_r3_candidate_static(candidate)
    if (
        candidate.get("r2_recovery_job_id") != r2_job_id
        or candidate.get("r2_recovery_capacity_receipt_sha256")
        != observed["r2_recovery_capacity_receipt_sha256"]
        or candidate.get("r2_recovery_authority_sha256")
        != observed["r2_recovery_authority_sha256"]
        or candidate.get("r2_recovery_submission_receipt_sha256")
        != observed["r2_recovery_submission_receipt_sha256"]
        or candidate.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_EXTRACTION_CANDIDATE_INVALID"
        )

    primary_result = str(probe.get("primary_result", ""))
    expected_probe_outcome = _r8u_r3_expected_proceedable_probe_outcome(
        primary_result
    )
    expected_probe_errno_number, expected_errno, primary_succeeded = (
        expected_probe_outcome if expected_probe_outcome is not None
        else (-1, "INVALID", False)
    )
    if (
        expected_probe_outcome is None
        or type(expected_probe_errno_number) is not int
        or expected_probe_errno_number < 0
        or probe.get("primary_primitive")
        != "RENAMEAT2_RENAME_NOREPLACE"
        or type(probe.get("primary_errno_number")) is not int
        or probe.get("primary_errno_number")
        != expected_probe_errno_number
        or probe.get("primary_errno") != expected_errno
        or probe.get("primary_returned_success") is not primary_succeeded
        or probe.get("real_source_parent_identity_sha256")
        != candidate.get("source_parent_identity_sha256")
        or probe.get("real_target_parent_identity_sha256")
        != candidate.get("target_parent_identity_sha256")
        or probe.get("real_source_mount_identity_sha256")
        != candidate.get("source_mount_identity_sha256")
        or probe.get("real_target_mount_identity_sha256")
        != candidate.get("target_mount_identity_sha256")
        or probe.get("real_parents_same_mounted_filesystem") is not True
        or not _r8u_valid_hashes(probe, "probe_mount_identity_sha256")
        or probe.get("probe_mount_identity_sha256")
        != candidate.get("source_mount_identity_sha256")
        or probe.get("probe_mount_identity_sha256")
        != candidate.get("target_mount_identity_sha256")
        or probe.get("probe_mount_matches_real_parents") is not True
        or probe.get("probe_source_present_after") is primary_succeeded
        or probe.get("probe_target_present_after") is not primary_succeeded
        or probe.get("probe_target_exact_after") is not primary_succeeded
        or probe.get("probe_cleanup_passed") is not True
        or not exact_values(
            probe,
            {
                "probe_directories_created": 2,
                "probe_directories_removed": 2,
                "scientific_file_body_reads": 0,
                "npz_body_reads": 0,
                "dicom_body_reads": 0,
                "dicom_extraction_executions": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_PUBLICATION_PROBE_INVALID"
        )

    resume_job_id = str(resume_submission.get("resume_job_id", ""))
    resume_job_name = (
        f"lvef_c3_r8u_r3_res_{authority.implementation_commit[:8]}"
    )
    authority_process_projection = resume_authority.get(
        "pre_qsub_process_projection"
    )
    submission_process_projection = resume_submission.get(
        "pre_qsub_process_projection"
    )
    qstat_projection = resume_submission.get("initial_qstat_projection")
    worker_process_projection = publication_claim.get(
        "worker_process_projection"
    )
    authority_process_sha256 = _validate_r8u_r3_process_projection(
        authority_process_projection,
        expected_status="PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
    )
    submission_process_sha256 = _validate_r8u_r3_process_projection(
        submission_process_projection,
        expected_status="PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
    )
    qstat_projection_sha256 = _validate_r8u_r3_initial_qstat_projection(
        qstat_projection,
        resume_job_id=resume_job_id,
        resume_job_name=resume_job_name,
    )
    _validate_r8u_r3_process_projection(
        worker_process_projection,
        expected_status="PASS_ZERO_COMPETING_R8U_R3_WORKER_PROCESSES",
    )
    identity_links = {
        "source_parent_identity_sha256": "source_parent_identity_sha256",
        "target_parent_identity_sha256": "target_parent_identity_sha256",
        "source_mount_identity_sha256": "source_mount_identity_sha256",
        "target_mount_identity_sha256": "target_mount_identity_sha256",
    }
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", resume_job_id) is None
        or publication_claim.get("resume_job_id") != resume_job_id
        or publication_claim.get("r2_recovery_job_id") != r2_job_id
        or publication_claim.get("extraction_candidate_seal_sha256")
        != observed["extraction_candidate_seal_sha256"]
        or publication_claim.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or publication_claim.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or publication_claim.get("resume_submission_receipt_sha256")
        != observed["resume_submission_receipt_sha256"]
        or publication_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or publication_claim.get("candidate_relative_file_projection_sha256")
        != candidate.get("candidate_relative_file_projection_sha256")
        or publication_claim.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or publication_claim.get("publication_primitive_selected")
        != (
            "RENAMEAT2_RENAME_NOREPLACE"
            if primary_succeeded
            else "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
        )
        or publication_claim.get("primary_result") != primary_result
        or publication_claim.get("target_absent") is not True
        or publication_claim.get("source_target_same_mounted_filesystem")
        is not True
        or any(
            publication_claim.get(claim_key) != candidate.get(candidate_key)
            for claim_key, candidate_key in identity_links.items()
        )
        or authority_process_projection != submission_process_projection
        or authority_process_sha256 != submission_process_sha256
        or publication_claim.get("pre_qsub_process_projection_sha256")
        != submission_process_sha256
        or publication_claim.get("initial_qstat_projection_sha256")
        != qstat_projection_sha256
        or publication_claim.get("competing_active_jobs")
        != qstat_projection.get("competing_matching_jobs")
        or publication_claim.get("competing_active_processes")
        != worker_process_projection.get("matching_processes")
        or not _r8u_exact_zero(
            publication_claim,
            "competing_active_jobs",
            "competing_active_processes",
            "cloud_requests",
            "downloads",
            "dicom_body_reads",
            "dicom_extraction_executions",
            "npz_body_reads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_PUBLICATION_CLAIM_INVALID"
        )

    expected_fallback = not primary_succeeded
    expected_fallback_primitive = (
        "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
        if expected_fallback
        else "NONE"
    )
    publication_ruling = publication.get("publication_ruling")
    real_rename_returned_success = publication.get(
        "real_rename_returned_success"
    )
    real_rename_errno_number = publication.get("real_rename_errno_number")
    real_rename_errno = str(publication.get("real_rename_errno", ""))
    real_rename_errno_classification = publication.get(
        "real_rename_errno_classification"
    )
    _validate_r8u_r3_real_rename_outcome(
        returned_success=real_rename_returned_success,
        errno_number=real_rename_errno_number,
        errno_name=real_rename_errno,
        errno_classification=real_rename_errno_classification,
        publication_ruling=publication_ruling,
    )
    if (
        publication.get("extraction_candidate_seal_sha256")
        != observed["extraction_candidate_seal_sha256"]
        or publication.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or publication.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or publication.get("primitive_attempted")
        != (
            "RENAMEAT2_RENAME_NOREPLACE"
            if primary_succeeded
            else "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
        )
        or publication.get("primary_result") != primary_result
        or publication.get("primary_errno") != expected_errno
        or publication.get("fallback_used") is not expected_fallback
        or publication.get("fallback_primitive")
        != expected_fallback_primitive
        or type(publication.get("rename_returned_success")) is not bool
        or publication.get("rename_returned_success")
        is not real_rename_returned_success
        or not _r8u_valid_hashes(
            publication,
            "prepublication_candidate_sha256",
            "postpublication_target_sha256",
        )
        or publication.get("prepublication_candidate_sha256")
        != publication.get("postpublication_target_sha256")
        or publication.get("prepublication_candidate_sha256")
        != candidate.get("candidate_relative_file_projection_sha256")
        or not exact_values(
            publication,
            {
                "source_absent": True,
                "target_exact": True,
                "candidate_npz_files": 10_187,
                "candidate_total_bytes": candidate["candidate_total_bytes"],
                "files_moved": 10_187,
                "files_copied": 0,
                "files_deleted_independently": 0,
                "dicom_body_reads": 0,
                "dicom_extraction_executions": 0,
                "npz_body_reads": 0,
                "cloud_requests": 0,
                "downloads": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_PUBLICATION_RECEIPT_INVALID"
        )

    try:
        current_script_authority = _r8r_current_script_authority()
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        resume_authority.get("prior_implementation_commit")
        != R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        or type(resume_authority.get("original_task_id")) is not int
        or resume_authority.get("original_task_id") != 16
        or resume_authority.get("continuation_task_range") != "17-19"
        or resume_authority.get("prefix_final_receipt_sha256") != prefix15
        or resume_authority.get("historical_r8r_chain_authority")
        != historical_hashes
        or resume_authority.get(
            "failed_r8u_recovery_epoch_authority_sha256"
        )
        != failed_r1_epoch_sha256
        or resume_authority.get("r2_recovery_job_id") != r2_job_id
        or resume_authority.get("r2_recovery_log_sha256")
        != R8U_R2_COMPLETED_EXTRACTION_LOG_SHA256
        or resume_authority.get("r2_recovery_capacity_receipt_sha256")
        != observed["r2_recovery_capacity_receipt_sha256"]
        or resume_authority.get("r2_recovery_authority_sha256")
        != observed["r2_recovery_authority_sha256"]
        or resume_authority.get("r2_recovery_submission_receipt_sha256")
        != observed["r2_recovery_submission_receipt_sha256"]
        or resume_authority.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or resume_authority.get("extraction_candidate_seal_sha256")
        != observed["extraction_candidate_seal_sha256"]
        or resume_authority.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or resume_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or not _r8u_valid_hashes(
            resume_authority, "qsub_environment_sha256"
        )
        or resume_authority.get("script_authority")
        != current_script_authority
        or resume_authority.get("runtime_validation_context")
        != "SEALED_SCHEDULER_RUNTIME_REPLAY"
        or resume_authority.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or not _r8u_exact_zero(
            resume_authority,
            "cloud_requests_authorized",
            "downloads_authorized",
            "dicom_body_reads_authorized",
            "dicom_extraction_executions_authorized",
        )
        or any(
            type(resume_authority.get(key)) is not int
            or resume_authority.get(key) != 1
            for key in (
                "echoprime_executions_authorized",
                "gpu_executions_authorized",
            )
        )
        or any(
            resume_authority.get(key) is not False
            for key in (
                "failed_partial_adoption_authorized",
                "failed_partial_mutation_authorized",
                "raw_dicom_deletion_authorized",
                "model_fitting_authorized",
                "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
        or type(resume_authority.get("maximum_new_qsub_submissions"))
        is not int
        or resume_authority.get("maximum_new_qsub_submissions") != 1
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_AUTHORITY_INVALID"
        )

    if (
        type(resume_submission.get("original_task_id")) is not int
        or resume_submission.get("original_task_id") != 16
        or resume_submission.get("resume_job_name")
        != f"lvef_c3_r8u_r3_res_{authority.implementation_commit[:8]}"
        or not _r8u_valid_hashes(
            resume_submission,
            "resume_qsub_argv_sha256",
            "qsub_environment_sha256",
        )
        or resume_submission.get("resume_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {
                "argv": _r8u_r3_expected_resume_qsub_command(
                    attempt_root=attempt_root,
                    implementation_commit=authority.implementation_commit,
                )
            }
        )
        or resume_submission.get("qsub_environment_sha256")
        != resume_authority.get("qsub_environment_sha256")
        or resume_submission.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or resume_submission.get("extraction_candidate_seal_sha256")
        != observed["extraction_candidate_seal_sha256"]
        or resume_submission.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or resume_submission.get("scheduler_submission_count") != 1
        or type(resume_submission.get("scheduler_submission_count")) is not int
        or resume_submission.get("resume_is_array") is not False
        or resume_submission.get("gpu_requested") is not True
        or resume_submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            resume_submission,
            "cloud_requests",
            "downloads",
            "dicom_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter",
            "npz_body_reads_by_submitter",
            "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            resume_submission.get("resume_qsub_evidence"),
            accepted_stdout=[
                f"{resume_job_id}\n".encode("ascii"),
                resume_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_SUBMISSION_INVALID"
        ) from exc

    _validate_r8u_r3_resume_accounting(
        resume_accounting, resume_job_id=resume_job_id
    )

    batch16_receipt_payload = _stable_nofollow_bytes(
        receipt_paths_by_batch["c3_batch_015"],
        code="R8U_R3_FINALIZER_BATCH16_RECEIPT",
        max_bytes=128 * 1024 * 1024,
    )
    try:
        batch16_receipt = json.loads(
            batch16_receipt_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_BATCH16_RECEIPT_INVALID"
        ) from exc
    terminal_support_paths = {
        "preservation_receipt_sha256": (
            attempt_root
            / "batches/c3_batch_015/preservation"
            / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root
            / "cache_retirement_authorizations/c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            attempt_root
            / "batches/c3_batch_015/preservation"
            / "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            attempt_root
            / "batches/c3_batch_015/final_resume_ledger.restricted.json"
        ),
    }
    terminal_support_hashes = {
        field: hashlib.sha256(
            _stable_nofollow_bytes(
                path,
                code="R8U_R3_FINALIZER_TERMINAL_SUPPORT",
                max_bytes=128 * 1024 * 1024,
            )
        ).hexdigest()
        for field, path in terminal_support_paths.items()
    }
    terminal_receipt_fields = (
        "n_selected_studies",
        "n_expected_objects",
        "expected_source_bytes",
        "n_successfully_extracted_cines",
        "n_object_technical_dispositions",
        "n_blocking_failures",
        "n_clip_embeddings",
        "n_pooled_studies",
        "n_no_cine_studies",
        "n_new_no_cine_studies",
        "object_substitution_count",
        "unaccounted_multiframe_objects",
    )
    terminal_links = {
        "failed_partial_seal_sha256": "failed_partial_seal_sha256",
        "extraction_candidate_seal_sha256": (
            "extraction_candidate_seal_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
    }
    if (
        not isinstance(batch16_receipt, Mapping)
        or batch16_receipt_payload != core.canonical_json_bytes(batch16_receipt)
        or hashlib.sha256(batch16_receipt_payload).hexdigest()
        != receipt_hashes_by_batch["c3_batch_015"]
        or type(terminal.get("original_task_id")) is not int
        or terminal.get("original_task_id") != 16
        or any(
            terminal.get(terminal_key) != observed[observed_key]
            for terminal_key, observed_key in terminal_links.items()
        )
        or terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
        or any(
            terminal.get(field) != digest
            for field, digest in terminal_support_hashes.items()
        )
        or terminal.get("cache_retirement_authorization_sha256")
        != batch16_receipt.get("cache_retirement_authorization_sha256")
        or any(
            type(terminal.get(key)) is not type(batch16_receipt.get(key))
            or terminal.get(key) != batch16_receipt.get(key)
            for key in terminal_receipt_fields
        )
        or not exact_values(
            terminal,
            {
                "n_selected_studies": 250,
                "n_expected_objects": R8U_BATCH16_RAW_FILES,
                "expected_source_bytes": R8U_BATCH16_RAW_BYTES,
                "n_successfully_extracted_cines": 10_187,
                "n_object_technical_dispositions": 0,
                "n_blocking_failures": 0,
                "n_clip_embeddings": 10_187,
                "n_pooled_studies": 250,
                "n_no_cine_studies": 0,
                "n_new_no_cine_studies": 0,
                "object_substitution_count": 0,
                "unaccounted_multiframe_objects": 0,
                "raw_dicoms_retained": True,
                "canonical_extraction_cache_retired": True,
                "failed_partial_cache_retained": True,
                "source_candidate_npz_files": 10_187,
                "cloud_requests": 0,
                "downloads": 0,
                "dicom_body_reads": 0,
                "dicom_extraction_executions": 0,
                "echoprime_executions": 1,
                "embedding_generations": 1,
                "gpu_executions": 1,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
        or terminal.get("raw_dicoms_retained")
        is not batch16_receipt.get("raw_dicoms_retained")
        or terminal.get("canonical_extraction_cache_retired")
        is not batch16_receipt.get("extracted_cache_retired")
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_RESUME_TERMINAL_INVALID"
        )

    continuation_links = {
        "extraction_candidate_seal_sha256": (
            "extraction_candidate_seal_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
        "resume_accounting_sha256": "resume_accounting_sha256",
        "resume_terminal_receipt_sha256": "resume_terminal_receipt_sha256",
    }
    if (
        continuation_claim.get("prior_implementation_commit")
        != R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        or continuation_claim.get("prefix_final_receipt_sha256") != prefix16
        or continuation_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_claim.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or continuation_claim.get("qsub_environment_sha256")
        != resume_submission.get("qsub_environment_sha256")
        or continuation_claim.get("script_authority")
        != current_script_authority
        or continuation_claim.get("continuation_task_range") != "17-19"
        or not exact_values(
            continuation_claim,
            {
                "continuation_task_count": 3,
                "continuation_max_concurrency": 1,
                "held_finalizer_count": 1,
                "total_new_qsub_maximum": 3,
                "automatic_retry_authorized": False,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests_by_submitter": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(
        continuation_submission.get("finalizer_job_id", "")
    )
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(
        Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
    )
    continuation_scheduler_root = (
        attempt_root / "r8u_r3_continuation_17_19" / "scheduler"
    )
    common_qsub = [
        qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho",
    ]
    array_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_r3_seq_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-t",
        "17-19",
        "-tc",
        "1",
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_r3_fin_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        runner,
    ]
    if (
        continuation_submission.get("resume_job_id") != resume_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_r3_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_r3_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != continuation_claim.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_submission.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or not exact_values(
            continuation_submission,
            {
                "scheduler_submission_count": 2,
                "total_new_qsub_submissions": 3,
                "scheduler_submission_maximum": 3,
                "array_task_range": "17-19",
                "array_task_count": 3,
                "array_max_concurrency": 1,
                "finalizer_held_on_array": True,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode("ascii"),
                f"{array_job_id}.17-19:1".encode("ascii"),
                f"{array_job_id}\n".encode("ascii"),
                array_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode("ascii"),
                finalizer_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": (
                historical_chain_sha256
            ),
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _validate_r8u_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U 2+13+4 epoch split."""

    if type(authority) is not R8UImplementationAuthority:
        raise ProductionFinalizationError("R8U_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = (
        authority.historical_r8r_recovery_authority_sha256,
        authority.historical_r8r_recovery_terminal_receipt_sha256,
        authority.historical_r8r_continuation_capacity_receipt_sha256,
        authority.historical_r8r_continuation_claim_sha256,
        authority.historical_r8r_continuation_submission_receipt_sha256,
        authority.failed_partial_seal_sha256,
        authority.recovery_capacity_receipt_sha256,
        authority.recovery_authority_sha256,
        authority.recovery_submission_receipt_sha256,
        authority.recovery_accounting_sha256,
        authority.recovery_terminal_receipt_sha256,
        authority.continuation_claim_sha256,
        authority.continuation_submission_receipt_sha256,
    )
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit
        in {R8R_SCIENTIFIC_GOVERNING_COMMIT, R8U_PRIOR_IMPLEMENTATION_COMMIT}
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
    ):
        raise ProductionFinalizationError("R8U_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )
    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 3
    ):
        raise ProductionFinalizationError(
            "R8U_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_repository_authority(authority.implementation_commit)
    return _validate_r8u_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r4_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR4ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate immutable R2/R3 evidence and the closed R4 successor chain."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root
            / "batches"
            / batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    chain_paths = {
        field: attempt_root / relative_path
        for field, relative_path, _artifact_type, _status, _epoch_kind
        in R8U_R4_CHAIN_ARTIFACT_SPECS
    }
    current_hashes = {getattr(authority, field) for field in chain_paths}
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or len(chain_paths) != len(R8U_R4_CHAIN_ARTIFACT_SPECS)
        or len(set(chain_paths.values())) != len(chain_paths)
        or any(path in receipt_paths_by_batch.values() for path in chain_paths.values())
        or not current_hashes.isdisjoint(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )
    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    candidate_total_bytes: int | None = None
    for field, relative_path, artifact_type, status, epoch_kind in (
        R8U_R4_CHAIN_ARTIFACT_SPECS
    ):
        value, digest = _load_r8u_r4_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            epoch_kind=epoch_kind,
            authority=authority,
            plan=plan,
            candidate_total_bytes=candidate_total_bytes,
        )
        values[field] = value
        observed[field] = digest
        if field == "portable_candidate_authority_sha256":
            raw_candidate_bytes = value.get("candidate_total_bytes")
            if type(raw_candidate_bytes) is not int or raw_candidate_bytes < 1:
                raise ProductionFinalizationError(
                    "R8U_R4_FINALIZER_PORTABLE_AUTHORITY_INVALID"
                )
            candidate_total_bytes = raw_candidate_bytes

    diagnosis = values["replay_diagnosis_sha256"]
    portable = values["portable_candidate_authority_sha256"]
    capacity_value = values["resume_capacity_receipt_sha256"]
    resume_authority = values["resume_authority_sha256"]
    submission = values["resume_submission_receipt_sha256"]
    claim = values["publication_claim_sha256"]
    locality = values["live_publication_locality_sha256"]
    probe = values["publication_primitive_probe_sha256"]
    publication = values["publication_receipt_sha256"]
    accounting = values["resume_accounting_sha256"]
    terminal = values["resume_terminal_receipt_sha256"]
    continuation_claim = values["continuation_claim_sha256"]
    continuation_submission = values[
        "continuation_submission_receipt_sha256"
    ]

    if (
        diagnosis.get("job_id") != "7364184"
        or diagnosis.get("candidate_seal_sha256")
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
        or diagnosis.get("final_classification") != diagnosis.get("status")
        or diagnosis.get("portable_candidate_fields_equal") is not True
        or diagnosis.get("node_local_only_differences") is not True
        or diagnosis.get("candidate_path_set_equal") is not True
        or diagnosis.get("candidate_count_and_bytes_equal") is not True
        or diagnosis.get("zero_body_reads") is not True
        or diagnosis.get("portable_differing_fields") != []
        or not _r8u_exact_zero(diagnosis, "dicom_body_reads", "npz_body_reads")
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_REPLAY_DIAGNOSIS_INVALID"
        )

    portable_hash_fields = tuple(R8U_R4_CONTROL_HASH_FIELDS) + (
        "canonical_stage_event_authority_sha256",
        "input_manifest_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_relative_file_portable_projection_sha256",
        "candidate_relative_directory_path_set_sha256",
        "candidate_relative_directory_portable_projection_sha256",
        "candidate_root_portable_identity_sha256",
    )
    if (
        portable.get("failed_r8u_r3_job_id") != "7364184"
        or portable.get("r8u_r3_candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or portable.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or portable.get("portable_projection_status")
        != "PASS_EXACT_PORTABLE_CONTENT"
        or not _r8u_valid_hashes(portable, *portable_hash_fields)
        or portable.get("input_manifest_sha256")
        != R8U_BATCH16_VERIFIED_MANIFEST_SHA256
        or portable.get("candidate_regular_files") != 10_192
        or portable.get("candidate_npz_files") != 10_187
        or portable.get("candidate_total_bytes") != candidate_total_bytes
        or type(portable.get("candidate_npz_bytes")) is not int
        or portable.get("candidate_npz_bytes") <= 0
        or portable.get("candidate_npz_bytes") > candidate_total_bytes
        or portable.get("symlink_count") != 0
        or portable.get("nonregular_count") != 0
        or portable.get("n_selected_studies") != 250
        or portable.get("n_source_objects") != R8U_BATCH16_RAW_FILES
        or portable.get("source_bytes") != R8U_BATCH16_RAW_BYTES
        or portable.get("n_readable") != R8U_BATCH16_RAW_FILES
        or portable.get("n_unreadable") != 0
        or portable.get("n_multiframe_candidates") != 10_187
        or portable.get("n_single_frame") != 8_490
        or portable.get("n_successfully_extracted_cines") != 10_187
        or portable.get("n_ordinary_preprocessing_path") != 10_187
        or portable.get("extraction_status")
        != "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
        or not _r8u_exact_zero(
            portable,
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
            "n_pixel_decode_failures", "n_object_technical_dispositions",
            "n_blocking_failures", "n_spatial_fallback_preprocessing_path",
            "n_temporal_fallback_preprocessing_path",
            "n_spatial_temporal_fallback_preprocessing_path",
            "object_substitution_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_PORTABLE_AUTHORITY_INVALID"
        )

    expected_script_authority = {
        "controller_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "lvef_c3_r8r_recovery_continuation.py"
        ),
        "full_sequential_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_full_sequential.py"
        ),
        "production_stages_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_production_stages.py"
        ),
        "preservation_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "preserve_lvef_c3_production_batch.py"
        ),
        "retirement_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "retire_lvef_c3_extracted_cache_v2.py"
        ),
        "finalizer_sha256": sha256_file(Path(__file__).resolve()),
        "runner_sha256": sha256_file(
            Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
        ),
    }
    expected_prefix = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(15)
    ]
    if (
        resume_authority.get("prior_implementation_commit")
        != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        or resume_authority.get("original_task_id") != 16
        or resume_authority.get("continuation_task_range") != "17-19"
        or resume_authority.get("prefix_final_receipt_sha256")
        != expected_prefix
        or resume_authority.get("historical_r8r_chain_authority")
        != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES
        or resume_authority.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or resume_authority.get("r2_recovery_capacity_receipt_sha256")
        != observed["r2_recovery_capacity_receipt_sha256"]
        or resume_authority.get("r2_recovery_authority_sha256")
        != observed["r2_recovery_authority_sha256"]
        or resume_authority.get("r2_recovery_submission_receipt_sha256")
        != observed["r2_recovery_submission_receipt_sha256"]
        or resume_authority.get("failed_r8u_r3_job_id") != "7364184"
        or resume_authority.get("r8u_r3_candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or resume_authority.get("r8u_r3_scheduler_log_sha256")
        != "bc2feef6398e3f9be408a77c80fe6eba38dad98506671d96b893371a5b59f824"
        or resume_authority.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or resume_authority.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or resume_authority.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or resume_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or resume_authority.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or resume_authority.get("runtime_validation_context")
        != "SEALED_SCHEDULER_RUNTIME_REPLAY"
        or resume_authority.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or resume_authority.get("maximum_new_qsub_submissions") != 1
        or not _r8u_exact_zero(
            resume_authority,
            "cloud_requests_authorized", "downloads_authorized",
            "dicom_body_reads_authorized",
            "dicom_extraction_executions_authorized",
        )
        or resume_authority.get("echoprime_executions_authorized") != 1
        or resume_authority.get("gpu_executions_authorized") != 1
        or any(
            resume_authority.get(key) is not False
            for key in (
                "failed_partial_adoption_authorized",
                "failed_partial_mutation_authorized",
                "model_fitting_authorized", "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_RESUME_AUTHORITY_INVALID"
        )

    resume_job_id = str(submission.get("resume_job_id", ""))
    expected_resume_command = _r8u_r4_expected_resume_qsub_command(
        attempt_root=attempt_root,
        implementation_commit=authority.implementation_commit,
    )
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", resume_job_id) is None
        or submission.get("original_task_id") != 16
        or submission.get("resume_job_name")
        != f"lvef_c3_r8u_r4_res_{authority.implementation_commit[:8]}"
        or submission.get("resume_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": expected_resume_command})
        or submission.get("qsub_environment_sha256")
        != resume_authority.get("qsub_environment_sha256")
        or submission.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or submission.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or submission.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or submission.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or submission.get("scheduler_submission_count") != 1
        or submission.get("resume_is_array") is not False
        or submission.get("gpu_requested") is not True
        or submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            submission,
            "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter",
            "npz_body_reads_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_RESUME_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            submission.get("resume_qsub_evidence"),
            accepted_stdout=[
                f"{resume_job_id}\n".encode("ascii"),
                resume_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_RESUME_SUBMISSION_INVALID"
        ) from exc

    if (
        claim.get("resume_job_id") != resume_job_id
        or claim.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or claim.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or claim.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or claim.get("resume_submission_receipt_sha256")
        != observed["resume_submission_receipt_sha256"]
        or claim.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or claim.get("target_absent") is not True
        or claim.get("competing_active_jobs") != 0
        or claim.get("competing_active_processes") != 0
        or not _r8u_valid_hashes(
            claim,
            "worker_process_projection_sha256",
            "worker_qstat_projection_sha256",
        )
        or not _r8u_exact_zero(
            claim, "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "npz_body_reads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_PUBLICATION_CLAIM_INVALID"
        )

    locality_true_fields = (
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call",
    )
    if (
        locality.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or any(locality.get(key) is not True for key in locality_true_fields)
        or locality.get("competing_active_jobs") != 0
        or locality.get("competing_active_processes") != 0
        or not _r8u_valid_hashes(
            locality,
            "source_identity_sha256", "source_parent_identity_sha256",
            "target_parent_identity_sha256", "source_mount_identity_sha256",
            "target_mount_identity_sha256",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_LIVE_LOCALITY_INVALID"
        )
    if (
        probe.get("live_publication_locality_sha256")
        != observed["live_publication_locality_sha256"]
        or probe.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or probe.get("primary_result") not in R8U_R3_PROCEEDABLE_PROBE_RESULTS
        or probe.get("probe_cleanup_passed") is not True
        or not _r8u_exact_zero(
            probe, "scientific_file_body_reads", "npz_body_reads",
            "dicom_body_reads", "dicom_extraction_executions",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_PUBLICATION_PROBE_INVALID"
        )
    if (
        publication.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or publication.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or publication.get("live_publication_locality_sha256")
        != observed["live_publication_locality_sha256"]
        or publication.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or publication.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or publication.get("source_absent") is not True
        or publication.get("target_exact") is not True
        or publication.get("candidate_npz_files") != 10_187
        or publication.get("candidate_total_bytes") != candidate_total_bytes
        or publication.get("files_moved") != 10_187
        or publication.get("files_copied") != 0
        or publication.get("files_deleted_independently") != 0
        or not _r8u_exact_zero(
            publication, "dicom_body_reads", "dicom_extraction_executions",
            "npz_body_reads", "cloud_requests", "downloads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_PUBLICATION_RECEIPT_INVALID"
        )
    try:
        _validate_r8u_r3_real_rename_outcome(
            returned_success=publication.get("rename_returned_success"),
            errno_number=publication.get("real_rename_errno_number"),
            errno_name=publication.get("real_rename_errno"),
            errno_classification=publication.get(
                "real_rename_errno_classification"
            ),
            publication_ruling=publication.get("publication_ruling"),
        )
        _validate_r8u_r3_resume_accounting(
            accounting,
            resume_job_id=resume_job_id,
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_TERMINAL_ACCOUNTING_INVALID"
        ) from exc

    batch16_receipt_path = receipt_paths_by_batch["c3_batch_015"]
    batch16_receipt = load_json(batch16_receipt_path, "R8U_R4_BATCH16_RECEIPT")
    batch16_paths = {
        "preservation_receipt_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root / "cache_retirement_authorizations/"
            "c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            attempt_root / "batches/c3_batch_015/final_resume_ledger.restricted.json"
        ),
        "batch_finalization_receipt_sha256": batch16_receipt_path,
    }
    if any(
        terminal.get(key) != sha256_file(path)
        for key, path in batch16_paths.items()
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_TERMINAL_RECEIPT_INVALID"
        )
    terminal_links = {
        "failed_partial_seal_sha256": "failed_partial_seal_sha256",
        "r8u_r3_candidate_seal_sha256": (
            "r3_extraction_candidate_seal_sha256"
        ),
        "candidate_replay_diagnosis_sha256": "replay_diagnosis_sha256",
        "portable_candidate_authority_sha256": (
            "portable_candidate_authority_sha256"
        ),
        "live_publication_locality_sha256": (
            "live_publication_locality_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
    }
    terminal_count_links = {
        "n_selected_studies": "n_selected_studies",
        "n_expected_objects": "n_expected_objects",
        "expected_source_bytes": "expected_source_bytes",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_clip_embeddings": "n_clip_embeddings",
        "n_pooled_studies": "n_pooled_studies",
        "n_no_cine_studies": "n_no_cine_studies",
        "n_new_no_cine_studies": "n_new_no_cine_studies",
        "object_substitution_count": "object_substitution_count",
        "unaccounted_multiframe_objects": "unaccounted_multiframe_objects",
    }
    if (
        terminal.get("original_task_id") != 16
        or any(
            terminal.get(terminal_key) != observed[observed_key]
            for terminal_key, observed_key in terminal_links.items()
        )
        or any(
            terminal.get(terminal_key) != batch16_receipt.get(receipt_key)
            for terminal_key, receipt_key in terminal_count_links.items()
        )
        or terminal.get("raw_dicoms_retained") is not True
        or terminal.get("canonical_extraction_cache_retired") is not True
        or terminal.get("failed_partial_cache_retained") is not True
        or terminal.get("source_candidate_npz_files") != 10_187
        or terminal.get("echoprime_executions") != 1
        or terminal.get("embedding_generations") != 1
        or terminal.get("gpu_executions") != 1
        or not _r8u_exact_zero(
            terminal, "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
        or terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
        or capacity_value.get("completed_extraction_candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_TERMINAL_RECEIPT_INVALID"
        )

    continuation_links = {
        "r8u_r3_candidate_seal_sha256": (
            "r3_extraction_candidate_seal_sha256"
        ),
        "candidate_replay_diagnosis_sha256": "replay_diagnosis_sha256",
        "portable_candidate_authority_sha256": (
            "portable_candidate_authority_sha256"
        ),
        "live_publication_locality_sha256": (
            "live_publication_locality_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
        "resume_accounting_sha256": "resume_accounting_sha256",
        "resume_terminal_receipt_sha256": "resume_terminal_receipt_sha256",
    }
    prefix16 = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(16)
    ]
    if (
        continuation_claim.get("prior_implementation_commit")
        != R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        or continuation_claim.get("prefix_final_receipt_sha256") != prefix16
        or continuation_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_claim.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or continuation_claim.get("qsub_environment_sha256")
        != submission.get("qsub_environment_sha256")
        or continuation_claim.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or continuation_claim.get("continuation_task_range") != "17-19"
        or not exact_values(
            continuation_claim,
            {
                "continuation_task_count": 3,
                "continuation_max_concurrency": 1,
                "held_finalizer_count": 1,
                "total_new_qsub_maximum": 3,
                "automatic_retry_authorized": False,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests_by_submitter": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(
        continuation_submission.get("finalizer_job_id", "")
    )
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(
        Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
    )
    continuation_scheduler_root = (
        attempt_root / "r8u_r4_continuation_17_19" / "scheduler"
    )
    common_qsub = [
        qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho",
    ]
    array_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_r4_seq_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-t",
        "17-19",
        "-tc",
        "1",
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N",
        f"lvef_c3_r8u_r4_fin_{authority.implementation_commit[:8]}",
        "-j",
        "y",
        "-o",
        str(continuation_scheduler_root),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        runner,
    ]
    if (
        continuation_submission.get("resume_job_id") != resume_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_r4_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_r4_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != continuation_claim.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_submission.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or not exact_values(
            continuation_submission,
            {
                "scheduler_submission_count": 2,
                "total_new_qsub_submissions": 3,
                "scheduler_submission_maximum": 3,
                "array_task_range": "17-19",
                "array_task_count": 3,
                "array_max_concurrency": 1,
                "finalizer_held_on_array": True,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode("ascii"),
                f"{array_job_id}.17-19:1".encode("ascii"),
                f"{array_job_id}\n".encode("ascii"),
                array_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode("ascii"),
                finalizer_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": historical_chain_sha256,
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _validate_r8u_r5_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR5ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate the immutable science and closed R5 worker-context chain."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root / "batches" / batch_id / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    chain_paths = {
        field: attempt_root / relative_path
        for field, relative_path, _artifact_type, _status, _epoch_kind
        in R8U_R5_CHAIN_ARTIFACT_SPECS
    }
    current_hashes = {getattr(authority, field) for field in chain_paths}
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or len(chain_paths) != len(R8U_R5_CHAIN_ARTIFACT_SPECS)
        or len(set(chain_paths.values())) != len(chain_paths)
        or any(path in receipt_paths_by_batch.values() for path in chain_paths.values())
        or not current_hashes.isdisjoint(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )
    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    for field, relative_path, artifact_type, status, epoch_kind in (
        R8U_R5_CHAIN_ARTIFACT_SPECS
    ):
        value, digest = _load_r8u_r5_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            epoch_kind=epoch_kind,
            authority=authority,
            plan=plan,
        )
        values[field] = value
        observed[field] = digest

    diagnosis = values["replay_diagnosis_sha256"]
    portable = values["portable_candidate_authority_sha256"]
    account = values["scheduler_account_authority_sha256"]
    r4_failure = values["r8u_r4_failure_evidence_sha256"]
    probe_receipt = values["worker_context_probe_receipt_sha256"]
    probe_accounting = values["worker_context_probe_accounting_sha256"]
    capacity_value = values["resume_capacity_receipt_sha256"]
    resume_authority = values["resume_authority_sha256"]
    submission = values["resume_submission_receipt_sha256"]
    locality = values["live_publication_locality_sha256"]
    claim = values["publication_claim_sha256"]
    probe = values["publication_primitive_probe_sha256"]
    publication = values["publication_receipt_sha256"]
    accounting = values["resume_accounting_sha256"]
    terminal = values["resume_terminal_receipt_sha256"]
    continuation_claim = values["continuation_claim_sha256"]
    continuation_submission = values[
        "continuation_submission_receipt_sha256"
    ]

    if (
        diagnosis.get("job_id") != "7364184"
        or diagnosis.get("candidate_seal_sha256")
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
        or diagnosis.get("final_classification") != diagnosis.get("status")
        or diagnosis.get("portable_candidate_fields_equal") is not True
        or diagnosis.get("node_local_only_differences") is not True
        or diagnosis.get("candidate_path_set_equal") is not True
        or diagnosis.get("candidate_count_and_bytes_equal") is not True
        or diagnosis.get("zero_body_reads") is not True
        or diagnosis.get("portable_differing_fields") != []
        or not _r8u_exact_zero(diagnosis, "dicom_body_reads", "npz_body_reads")
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_REPLAY_DIAGNOSIS_INVALID"
        )
    candidate_total_bytes = portable.get("candidate_total_bytes")
    portable_hash_fields = tuple(R8U_R4_CONTROL_HASH_FIELDS) + (
        "canonical_stage_event_authority_sha256",
        "input_manifest_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_relative_file_portable_projection_sha256",
        "candidate_relative_directory_path_set_sha256",
        "candidate_relative_directory_portable_projection_sha256",
        "candidate_root_portable_identity_sha256",
    )
    if (
        portable.get("failed_r8u_r3_job_id") != "7364184"
        or portable.get("r8u_r3_candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or portable.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or portable.get("portable_projection_status")
        != "PASS_EXACT_PORTABLE_CONTENT"
        or not _r8u_valid_hashes(portable, *portable_hash_fields)
        or portable.get("input_manifest_sha256")
        != R8U_BATCH16_VERIFIED_MANIFEST_SHA256
        or portable.get("candidate_regular_files") != 10_192
        or portable.get("candidate_npz_files") != 10_187
        or type(candidate_total_bytes) is not int
        or candidate_total_bytes < 1
        or portable.get("n_selected_studies") != 250
        or portable.get("n_source_objects") != R8U_BATCH16_RAW_FILES
        or portable.get("source_bytes") != R8U_BATCH16_RAW_BYTES
        or portable.get("n_successfully_extracted_cines") != 10_187
        or portable.get("extraction_status")
        != "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
        or not _r8u_exact_zero(
            portable,
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
            "n_pixel_decode_failures", "n_object_technical_dispositions",
            "n_blocking_failures", "object_substitution_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_PORTABLE_AUTHORITY_INVALID"
        )
    _validate_r8u_r5_scheduler_account(account)

    r4_root = attempt_root / "r8u_r4_batch16_publication_resume"
    if (
        r4_failure.get("failed_job_id") != "7375270"
        or r4_failure.get("scheduler_failed") != 0
        or r4_failure.get("application_exit_status") != 78
        or r4_failure.get("wall_seconds") != 275
        or r4_failure.get("first_failed_stage")
        != "PRE_BODY_WORKER_SCHEDULER_IDENTITY_VALIDATION"
        or r4_failure.get("exact_failure_code") != "SCHEDULER_IDENTITY_INVALID"
        or r4_failure.get("scheduler_log_basename")
        != "lvef_c3_r8u_r4_res_6eb5c9a4.o7375270"
        or r4_failure.get("scheduler_log_bytes") != 108
        or r4_failure.get("scheduler_log_mode") != "0644"
        or r4_failure.get("scheduler_log_sha256")
        != "c27f6cca757a3ffc26ca1f7211f44a691137fb736f62a5db13825e9b2ae632e8"
        or r4_failure.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or r4_failure.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or r4_failure.get("r8u_r4_capacity_sha256")
        != sha256_file(r4_root / "resume_capacity.restricted.json")
        or r4_failure.get("r8u_r4_resume_authority_sha256")
        != sha256_file(r4_root / "resume_authority.restricted.json")
        or r4_failure.get("r8u_r4_submission_sha256")
        != sha256_file(r4_root / "scheduler/submission_receipt.restricted.json")
        or r4_failure.get("portable_candidate_pass") is not True
        or r4_failure.get("publication_locality_ran") is not False
        or r4_failure.get("publication_ran") is not False
        or r4_failure.get("echoprime_ran") is not False
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_R4_FAILURE_EVIDENCE_INVALID"
        )

    probe_root = attempt_root / "r8u_r5_batch16_publication_resume"
    probe_authority, probe_authority_sha = _load_r8u_r5_auxiliary_artifact(
        probe_root / "worker_context_probe_authority.restricted.json",
        keys=R8U_R5_PROBE_AUTHORITY_KEYS,
        artifact_type="lvef_c3_r8u_r5_worker_context_probe_authority_v1",
        status="AUTHORIZED_R8U_R5_WORKER_CONTEXT_PROBE",
        code="R8U_R5_FINALIZER_PROBE_AUTHORITY_INVALID",
    )
    probe_submission, probe_submission_sha = _load_r8u_r5_auxiliary_artifact(
        probe_root / "scheduler/probe_submission_receipt.restricted.json",
        keys=R8U_R5_PROBE_SUBMISSION_KEYS,
        artifact_type="lvef_c3_r8u_r5_worker_context_probe_submission_v1",
        status="PASS_EXACT_ONE_R8U_R5_CPU_WORKER_CONTEXT_PROBE_QSUB",
        code="R8U_R5_FINALIZER_PROBE_SUBMISSION_INVALID",
    )
    diagnostic, diagnostic_sha = _load_r8u_r5_auxiliary_artifact(
        probe_root / "worker_context_probe_diagnostic.restricted.json",
        keys=R8U_R5_WORKER_DIAGNOSTIC_KEYS,
        artifact_type="lvef_c3_r8u_r5_worker_scheduler_context_v1",
        status="PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        code="R8U_R5_FINALIZER_WORKER_CONTEXT_DIAGNOSTIC_INVALID",
    )
    probe_job_id = str(probe_receipt.get("probe_job_id", ""))
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", probe_job_id) is None
        or probe_authority.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_authority.get("r8u_r4_failure_evidence_sha256")
        != observed["r8u_r4_failure_evidence_sha256"]
        or probe_authority.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or probe_authority.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or probe_authority.get("worker_role")
        != "R8U_R5_WORKER_CONTEXT_PROBE"
        or probe_authority.get("wall_seconds_maximum") != 600
        or probe_authority.get("cpu_slots") != 1
        or probe_authority.get("gpu_requested") is not False
        or probe_authority.get("array_requested") is not False
        or not _r8u_exact_zero(
            probe_authority,
            "cloud_requests_authorized", "dicom_body_reads_authorized",
            "npz_body_reads_authorized",
        )
        or any(
            probe_authority.get(key) is not False
            for key in (
                "candidate_scan_authorized",
                "publication_authorized", "extraction_authorized",
                "embedding_generation_authorized", "preservation_authorized",
                "scientific_attempt_mutation_authorized",
            )
        )
        or probe_submission.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_submission.get("r8u_r4_failure_evidence_sha256")
        != observed["r8u_r4_failure_evidence_sha256"]
        or probe_submission.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or probe_submission.get("probe_authority_sha256") != probe_authority_sha
        or probe_submission.get("probe_job_id") != probe_job_id
        or probe_submission.get("probe_job_name")
        != f"lvef_c3_r8u_r5_ctx_{authority.implementation_commit[:8]}"
        or probe_submission.get("probe_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {
                "argv": _r8u_r5_expected_probe_qsub_command(
                    attempt_root=attempt_root,
                    implementation_commit=authority.implementation_commit,
                )
            }
        )
        or probe_submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or probe_submission.get("scheduler_submission_count") != 1
        or probe_submission.get("cpu_slots") != 1
        or probe_submission.get("gpu_requested") is not False
        or probe_submission.get("probe_is_array") is not False
        or probe_submission.get("automatic_retry_authorized") is not False
        or any(
            diagnostic.get(key) is not True
            for key in (
                "effective_uid_match", "job_id_match", "task_context_match",
                "job_role_match", "runner_sha256_match", "python_sha256_match",
                "implementation_commit_match", "qsub_environment_sha256_match",
            )
        )
        or diagnostic.get("canonical_worker_environment_status") != "PASS"
        or probe_receipt.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_receipt.get("probe_authority_sha256") != probe_authority_sha
        or probe_receipt.get("probe_submission_receipt_sha256")
        != probe_submission_sha
        or probe_receipt.get("probe_diagnostic_sha256") != diagnostic_sha
        or probe_receipt.get("worker_role")
        != "R8U_R5_WORKER_CONTEXT_PROBE"
        or any(
            probe_receipt.get(key) is not True
            for key in (
                "effective_uid_match", "job_id_match", "task_context_match",
                "job_role_match",
                "canonical_worker_environment_pass",
            )
        )
        or not _r8u_valid_hashes(
            probe_receipt,
            "worker_qstat_projection_sha256",
            "worker_process_projection_sha256",
        )
        or not _r8u_exact_zero(
            probe_receipt,
            "candidate_scans", "cloud_requests", "dicom_body_reads",
            "npz_body_reads", "publication_executions", "extraction_executions",
            "embedding_generations", "preservation_executions",
            "scientific_attempt_mutations",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_WORKER_CONTEXT_PROBE_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            probe_submission.get("probe_qsub_evidence"),
            accepted_stdout=[
                f"{probe_job_id}\n".encode("ascii"),
                probe_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_recovery_accounting(
            probe_accounting.get("accounting_projection"),
            expected_job_id=probe_job_id,
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_WORKER_CONTEXT_PROBE_INVALID"
        ) from exc
    if (
        probe_accounting.get("probe_job_id") != probe_job_id
        or probe_accounting.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_accounting.get("probe_authority_sha256") != probe_authority_sha
        or probe_accounting.get("probe_submission_receipt_sha256")
        != probe_submission_sha
        or probe_accounting.get("failed") != 0
        or probe_accounting.get("exit_status") != 0
        or probe_accounting.get("probe_receipt_sha256")
        != observed["worker_context_probe_receipt_sha256"]
        or not _r8u_valid_hashes(probe_accounting, "probe_scheduler_log_sha256")
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_WORKER_CONTEXT_PROBE_INVALID"
        )

    # The capacity wrapper binds the immutable R4 capacity plus one fresh
    # observation without reinterpreting the candidate or scientific bodies.
    fresh_capacity = capacity_value.get("fresh_capacity_observation")
    if (
        capacity_value.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or capacity_value.get("worker_context_probe_accounting_sha256")
        != observed["worker_context_probe_accounting_sha256"]
        or capacity_value.get("r8u_r4_capacity_authority_sha256")
        != sha256_file(r4_root / "resume_capacity.restricted.json")
        or not isinstance(fresh_capacity, Mapping)
        or capacity_value.get("fresh_capacity_observation_sha256")
        != core.canonical_json_sha256(fresh_capacity)
        or capacity_value.get("candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or capacity_value.get("candidate_total_bytes") != candidate_total_bytes
        or capacity_value.get("quota_reserve_bytes") != 200_000_000_000
        or capacity_value.get("physical_reserve_bytes") != 200_000_000_000
        or capacity_value.get("file_slot_reserve_pass") is not True
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )

    expected_script_authority = {
        "controller_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "lvef_c3_r8r_recovery_continuation.py"
        ),
        "full_sequential_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_full_sequential.py"
        ),
        "production_stages_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_production_stages.py"
        ),
        "preservation_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "preserve_lvef_c3_production_batch.py"
        ),
        "retirement_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "retire_lvef_c3_extracted_cache_v2.py"
        ),
        "finalizer_sha256": sha256_file(Path(__file__).resolve()),
        "runner_sha256": sha256_file(
            Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
        ),
    }
    expected_prefix = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(15)
    ]
    if (
        resume_authority.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or resume_authority.get("r8u_r4_failure_evidence_sha256")
        != observed["r8u_r4_failure_evidence_sha256"]
        or resume_authority.get("worker_context_probe_receipt_sha256")
        != observed["worker_context_probe_receipt_sha256"]
        or resume_authority.get("worker_context_probe_authority_sha256")
        != probe_authority_sha
        or resume_authority.get("worker_context_probe_accounting_sha256")
        != observed["worker_context_probe_accounting_sha256"]
        or resume_authority.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or resume_authority.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or resume_authority.get("prefix_final_receipt_sha256") != expected_prefix
        or resume_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or resume_authority.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or resume_authority.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or resume_authority.get("worker_role")
        != "R8U_R5_BATCH16_PUBLICATION_RESUME"
        or not _r8u_exact_zero(
            resume_authority,
            "cloud_requests_authorized", "downloads_authorized",
            "dicom_body_reads_authorized",
            "dicom_extraction_executions_authorized",
        )
        or resume_authority.get("echoprime_executions_authorized") != 1
        or resume_authority.get("gpu_executions_authorized") != 1
        or resume_authority.get("maximum_new_gpu_resume_qsubs") != 1
        or any(
            resume_authority.get(key) is not False
            for key in (
                "model_fitting_authorized", "prediction_authorized",
                "confirmatory_performance_access_authorized",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_RESUME_AUTHORITY_INVALID"
        )

    resume_job_id = str(submission.get("resume_job_id", ""))
    resume_job_name = f"lvef_c3_r8u_r5_res_{authority.implementation_commit[:8]}"
    expected_resume_command = _r8u_r5_expected_resume_qsub_command(
        attempt_root=attempt_root,
        implementation_commit=authority.implementation_commit,
    )
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", resume_job_id) is None
        or resume_job_id == probe_job_id
        or submission.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or submission.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or submission.get("worker_context_probe_receipt_sha256")
        != observed["worker_context_probe_receipt_sha256"]
        or submission.get("worker_context_probe_authority_sha256")
        != probe_authority_sha
        or submission.get("worker_context_probe_accounting_sha256")
        != observed["worker_context_probe_accounting_sha256"]
        or submission.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or submission.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or submission.get("resume_job_name") != resume_job_name
        or submission.get("resume_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": expected_resume_command})
        or submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or submission.get("scheduler_submission_count") != 1
        or submission.get("resume_is_array") is not False
        or submission.get("gpu_requested") is not True
        or submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            submission,
            "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_RESUME_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            submission.get("resume_qsub_evidence"),
            accepted_stdout=[
                f"{resume_job_id}\n".encode("ascii"),
                resume_job_id.encode("ascii"),
            ],
        )
        _validate_r8u_r5_qstat_projection(
            submission.get("initial_qstat_projection"),
            job_id=resume_job_id,
            job_name=resume_job_name,
            expected_status=(
                "PASS_EXACT_ONE_R8U_R5_SUBMITTED_JOB_ZERO_COMPETITORS"
            ),
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_RESUME_SUBMISSION_INVALID"
        ) from exc

    return _validate_r8u_r5_successor_artifacts(
        attempt_root=attempt_root,
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=expected_runtime_authority,
        expected_script_authority=expected_script_authority,
        observed=observed,
        account=account,
        candidate_total_bytes=candidate_total_bytes,
        resume_job_id=resume_job_id,
        values={
            "locality": locality,
            "claim": claim,
            "probe": probe,
            "publication": publication,
            "accounting": accounting,
            "terminal": terminal,
            "continuation_claim": continuation_claim,
            "continuation_submission": continuation_submission,
        },
        historical_chain_sha256=historical_chain_sha256,
        historical_hashes=historical_hashes,
    )


def _validate_r8u_r5_successor_artifacts(
    *,
    attempt_root: Path,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR5ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    expected_script_authority: Mapping[str, str],
    observed: Mapping[str, str],
    account: Mapping[str, Any],
    candidate_total_bytes: int,
    resume_job_id: str,
    values: Mapping[str, Mapping[str, Any]],
    historical_chain_sha256: str,
    historical_hashes: Mapping[str, str],
) -> str:
    """Validate R5 publication, terminal, and future finalizer bindings."""

    locality = values["locality"]
    claim = values["claim"]
    probe = values["probe"]
    publication = values["publication"]
    accounting = values["accounting"]
    terminal = values["terminal"]
    continuation_claim = values["continuation_claim"]
    continuation_submission = values["continuation_submission"]
    r5_root = attempt_root / "r8u_r5_batch16_publication_resume"
    worker_diagnostic, worker_diagnostic_sha = _load_r8u_r5_auxiliary_artifact(
        r5_root / "gpu_worker_context_diagnostic.restricted.json",
        keys=R8U_R5_WORKER_DIAGNOSTIC_KEYS,
        artifact_type="lvef_c3_r8u_r5_worker_scheduler_context_v1",
        status="PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        code="R8U_R5_FINALIZER_WORKER_CONTEXT_DIAGNOSTIC_INVALID",
    )
    if (
        any(
            worker_diagnostic.get(field) is not True
            for field in (
                "effective_uid_match", "job_id_match", "task_context_match",
                "job_role_match", "runner_sha256_match", "python_sha256_match",
                "implementation_commit_match", "qsub_environment_sha256_match",
            )
        )
        or worker_diagnostic.get("canonical_worker_environment_status") != "PASS"
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_WORKER_CONTEXT_DIAGNOSTIC_INVALID"
        )

    locality_true_fields = (
        "source_exists_safe_directory", "target_absent",
        "source_target_same_mounted_filesystem", "parents_nonsymlinked",
        "owner_mode_valid", "source_identity_stable_same_call",
        "source_parent_identity_stable_same_call",
        "target_parent_identity_stable_same_call",
    )
    if (
        locality.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or locality.get("worker_context_diagnostic_sha256")
        != worker_diagnostic_sha
        or any(locality.get(key) is not True for key in locality_true_fields)
        or locality.get("competing_active_jobs") != 0
        or locality.get("competing_active_processes") != 0
        or not _r8u_valid_hashes(
            locality,
            "worker_qstat_projection_sha256", "worker_process_projection_sha256",
            "source_identity_sha256", "source_parent_identity_sha256",
            "target_parent_identity_sha256", "source_mount_identity_sha256",
            "target_mount_identity_sha256",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_LIVE_LOCALITY_INVALID"
        )
    if (
        claim.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or claim.get("resume_job_id") != resume_job_id
        or claim.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or claim.get("r8u_r4_failure_evidence_sha256")
        != observed["r8u_r4_failure_evidence_sha256"]
        or claim.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or claim.get("resume_submission_receipt_sha256")
        != observed["resume_submission_receipt_sha256"]
        or claim.get("live_publication_locality_sha256")
        != observed["live_publication_locality_sha256"]
        or claim.get("worker_context_diagnostic_sha256")
        != worker_diagnostic_sha
        or claim.get("worker_process_projection_sha256")
        != locality.get("worker_process_projection_sha256")
        or claim.get("worker_qstat_projection_sha256")
        != locality.get("worker_qstat_projection_sha256")
        or claim.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or claim.get("target_absent") is not True
        or claim.get("competing_active_jobs") != 0
        or claim.get("competing_active_processes") != 0
        or not _r8u_exact_zero(
            claim, "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "npz_body_reads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_PUBLICATION_CLAIM_INVALID"
        )
    if (
        probe.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe.get("live_publication_locality_sha256")
        != observed["live_publication_locality_sha256"]
        or probe.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or probe.get("primary_result") not in R8U_R3_PROCEEDABLE_PROBE_RESULTS
        or probe.get("probe_cleanup_passed") is not True
        or probe.get("probe_directories_created") != 2
        or probe.get("probe_directories_removed") != 2
        or not _r8u_exact_zero(
            probe, "scientific_file_body_reads", "npz_body_reads",
            "dicom_body_reads", "dicom_extraction_executions",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_PUBLICATION_PROBE_INVALID"
        )
    if (
        publication.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or publication.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or publication.get("r8u_r4_failure_evidence_sha256")
        != observed["r8u_r4_failure_evidence_sha256"]
        or publication.get("live_publication_locality_sha256")
        != observed["live_publication_locality_sha256"]
        or publication.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or publication.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or publication.get("source_absent") is not True
        or publication.get("target_exact") is not True
        or publication.get("candidate_npz_files") != 10_187
        or publication.get("candidate_total_bytes") != candidate_total_bytes
        or publication.get("files_moved") != 10_187
        or publication.get("files_copied") != 0
        or publication.get("files_deleted_independently") != 0
        or not _r8u_exact_zero(
            publication, "dicom_body_reads", "dicom_extraction_executions",
            "npz_body_reads", "cloud_requests", "downloads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_PUBLICATION_RECEIPT_INVALID"
        )
    try:
        _validate_r8u_r3_real_rename_outcome(
            returned_success=publication.get("rename_returned_success"),
            errno_number=publication.get("real_rename_errno_number"),
            errno_name=publication.get("real_rename_errno"),
            errno_classification=publication.get(
                "real_rename_errno_classification"
            ),
            publication_ruling=publication.get("publication_ruling"),
        )
        _validate_r8u_r3_resume_accounting(
            accounting, resume_job_id=resume_job_id
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_TERMINAL_ACCOUNTING_INVALID"
        ) from exc

    batch16_receipt_path = receipt_paths_by_batch["c3_batch_015"]
    batch16_receipt = load_json(batch16_receipt_path, "R8U_R5_BATCH16_RECEIPT")
    batch16_paths = {
        "preservation_receipt_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root / "cache_retirement_authorizations/"
            "c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            attempt_root / "batches/c3_batch_015/final_resume_ledger.restricted.json"
        ),
        "batch_finalization_receipt_sha256": batch16_receipt_path,
    }
    terminal_links = {
        "scheduler_account_authority_sha256": (
            "scheduler_account_authority_sha256"
        ),
        "r8u_r4_failure_evidence_sha256": "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256": (
            "worker_context_probe_receipt_sha256"
        ),
        "worker_context_probe_accounting_sha256": (
            "worker_context_probe_accounting_sha256"
        ),
        "live_publication_locality_sha256": "live_publication_locality_sha256",
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": "resume_submission_receipt_sha256",
    }
    terminal_count_links = {
        "n_selected_studies": "n_selected_studies",
        "n_expected_objects": "n_expected_objects",
        "expected_source_bytes": "expected_source_bytes",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_clip_embeddings": "n_clip_embeddings",
        "n_pooled_studies": "n_pooled_studies",
        "n_no_cine_studies": "n_no_cine_studies",
        "n_new_no_cine_studies": "n_new_no_cine_studies",
        "object_substitution_count": "object_substitution_count",
        "unaccounted_multiframe_objects": "unaccounted_multiframe_objects",
    }
    if (
        any(terminal.get(key) != sha256_file(path) for key, path in batch16_paths.items())
        or any(
            terminal.get(terminal_key) != observed[observed_key]
            for terminal_key, observed_key in terminal_links.items()
        )
        or terminal.get("worker_context_diagnostic_sha256")
        != worker_diagnostic_sha
        or any(
            terminal.get(terminal_key) != batch16_receipt.get(receipt_key)
            for terminal_key, receipt_key in terminal_count_links.items()
        )
        or terminal.get("raw_dicoms_retained") is not True
        or terminal.get("canonical_extraction_cache_retired") is not True
        or terminal.get("failed_partial_cache_retained") is not True
        or terminal.get("source_candidate_npz_files") != 10_187
        or terminal.get("echoprime_executions") != 1
        or terminal.get("embedding_generations") != 1
        or terminal.get("gpu_executions") != 1
        or not _r8u_exact_zero(
            terminal, "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        )
        or terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_TERMINAL_RECEIPT_INVALID"
        )

    continuation_links = {
        "scheduler_account_authority_sha256": (
            "scheduler_account_authority_sha256"
        ),
        "r8u_r4_failure_evidence_sha256": "r8u_r4_failure_evidence_sha256",
        "worker_context_probe_receipt_sha256": (
            "worker_context_probe_receipt_sha256"
        ),
        "worker_context_probe_accounting_sha256": (
            "worker_context_probe_accounting_sha256"
        ),
        "portable_candidate_authority_sha256": (
            "portable_candidate_authority_sha256"
        ),
        "live_publication_locality_sha256": "live_publication_locality_sha256",
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": "resume_submission_receipt_sha256",
        "resume_accounting_sha256": "resume_accounting_sha256",
        "resume_terminal_receipt_sha256": "resume_terminal_receipt_sha256",
    }
    prefix16 = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(16)
    ]
    if (
        continuation_claim.get("prior_implementation_commit")
        != R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT
        or continuation_claim.get("prefix_final_receipt_sha256") != prefix16
        or continuation_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_claim.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or continuation_claim.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or continuation_claim.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or continuation_claim.get("continuation_task_range") != "17-19"
        or not exact_values(
            continuation_claim,
            {
                "continuation_task_count": 3,
                "continuation_max_concurrency": 1,
                "held_finalizer_count": 1,
                "total_new_qsub_maximum": 4,
                "automatic_retry_authorized": False,
                "whole_stage_retry_authorized": False,
                "fifth_submission_reachable": False,
                "cloud_requests_by_submitter": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(continuation_submission.get("finalizer_job_id", ""))
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME)
    scheduler_root = attempt_root / "r8u_r5_continuation_17_19/scheduler"
    common_qsub = [qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho"]
    array_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r5_seq_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-t", "17-19", "-tc", "1",
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r5_fin_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-hold_jid", array_job_id,
        "-l", "h_rt=12:00:00", "-pe", "omp", "4",
        "-l", "mem_per_core=8G", runner,
    ]
    if (
        continuation_submission.get("resume_job_id") != resume_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_r5_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_r5_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_submission.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or not exact_values(
            continuation_submission,
            {
                "scheduler_submission_count": 2,
                "total_new_qsub_submissions": 4,
                "scheduler_submission_maximum": 4,
                "array_task_range": "17-19",
                "array_task_count": 3,
                "array_max_concurrency": 1,
                "finalizer_held_on_array": True,
                "whole_stage_retry_authorized": False,
                "fifth_submission_reachable": False,
                "cloud_requests": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode("ascii"),
                f"{array_job_id}.17-19:1".encode("ascii"),
                f"{array_job_id}\n".encode("ascii"),
                array_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode("ascii"),
                finalizer_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": historical_chain_sha256,
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _r8u_r6_validate_current_common(
    value: Mapping[str, Any], *, authority: R8UR6ImplementationAuthority
) -> None:
    _r8u_r6_validate_implementation_authority_epochs(
        value.get("implementation_authority_epochs"),
        implementation_commit=authority.implementation_commit,
    )
    if (
        value.get("original_scientific_commit")
        != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or value.get("implementation_commit") != authority.implementation_commit
        or value.get("attempt_id") != R8R_ATTEMPT_ID
        or value.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or value.get("batch_id") != "c3_batch_015"
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )


def _r8u_r6_validate_portable_history(
    *, values: Mapping[str, Mapping[str, Any]], observed: Mapping[str, str]
) -> int:
    diagnosis = values["replay_diagnosis_sha256"]
    portable = values["portable_candidate_authority_sha256"]
    if (
        diagnosis.get("job_id") != "7364184"
        or diagnosis.get("candidate_seal_sha256")
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
        or diagnosis.get("final_classification") != diagnosis.get("status")
        or diagnosis.get("portable_candidate_fields_equal") is not True
        or diagnosis.get("node_local_only_differences") is not True
        or diagnosis.get("candidate_path_set_equal") is not True
        or diagnosis.get("candidate_count_and_bytes_equal") is not True
        or diagnosis.get("zero_body_reads") is not True
        or diagnosis.get("portable_differing_fields") != []
        or not _r8u_exact_zero(diagnosis, "dicom_body_reads", "npz_body_reads")
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_REPLAY_DIAGNOSIS_INVALID"
        )
    candidate_total_bytes = portable.get("candidate_total_bytes")
    portable_hash_fields = tuple(R8U_R4_CONTROL_HASH_FIELDS) + (
        "canonical_stage_event_authority_sha256",
        "input_manifest_sha256",
        "candidate_npz_manifest_projection_sha256",
        "candidate_relative_file_path_set_sha256",
        "candidate_relative_npz_path_set_sha256",
        "candidate_relative_file_portable_projection_sha256",
        "candidate_relative_directory_path_set_sha256",
        "candidate_relative_directory_portable_projection_sha256",
        "candidate_root_portable_identity_sha256",
    )
    if (
        portable.get("failed_r8u_r3_job_id") != "7364184"
        or portable.get("r8u_r3_candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or portable.get("candidate_replay_diagnosis_sha256")
        != observed["replay_diagnosis_sha256"]
        or portable.get("portable_projection_status")
        != "PASS_EXACT_PORTABLE_CONTENT"
        or not _r8u_valid_hashes(portable, *portable_hash_fields)
        or portable.get("input_manifest_sha256")
        != R8U_BATCH16_VERIFIED_MANIFEST_SHA256
        or portable.get("candidate_regular_files") != 10_192
        or portable.get("candidate_npz_files") != 10_187
        or type(candidate_total_bytes) is not int
        or candidate_total_bytes < 1
        or portable.get("n_selected_studies") != 250
        or portable.get("n_source_objects") != R8U_BATCH16_RAW_FILES
        or portable.get("source_bytes") != R8U_BATCH16_RAW_BYTES
        or portable.get("n_successfully_extracted_cines") != 10_187
        or portable.get("extraction_status")
        != "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
        or not _r8u_exact_zero(
            portable,
            "missing_npz_files", "additional_npz_files",
            "substituted_npz_files", "npz_body_reads", "dicom_body_reads",
            "n_pixel_decode_failures", "n_object_technical_dispositions",
            "n_blocking_failures", "object_substitution_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_PORTABLE_AUTHORITY_INVALID"
        )
    return candidate_total_bytes


def _validate_r8u_r6_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR6ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate the closed R6 post-probe publication and successor chain."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root / "batches" / batch_id / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    chain_paths = {
        field: attempt_root / relative_path
        for field, relative_path, _artifact_type, _status, _epoch_kind
        in R8U_R6_CHAIN_ARTIFACT_SPECS
    }
    current_hashes = {getattr(authority, field) for field in chain_paths}
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or len(chain_paths) != len(R8U_R6_CHAIN_ARTIFACT_SPECS)
        or len(set(chain_paths.values())) != len(chain_paths)
        or any(path in receipt_paths_by_batch.values() for path in chain_paths.values())
        or not current_hashes.isdisjoint(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )
    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    for field, relative_path, artifact_type, status, epoch_kind in (
        R8U_R6_CHAIN_ARTIFACT_SPECS
    ):
        value, digest = _load_r8u_r6_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            epoch_kind=epoch_kind,
            authority=authority,
            plan=plan,
        )
        values[field] = value
        observed[field] = digest

    candidate_total_bytes = _r8u_r6_validate_portable_history(
        values=values, observed=observed
    )
    _validate_r8u_r6_scheduler_account(
        values["scheduler_account_authority_sha256"]
    )
    return _validate_r8u_r6_current_chain(
        attempt_root=attempt_root,
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=expected_runtime_authority,
        values=values,
        observed=observed,
        candidate_total_bytes=candidate_total_bytes,
        historical_chain_sha256=historical_chain_sha256,
        historical_hashes=historical_hashes,
    )


def _validate_r8u_r6_current_chain(
    *,
    attempt_root: Path,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR6ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    values: Mapping[str, Mapping[str, Any]],
    observed: Mapping[str, str],
    candidate_total_bytes: int,
    historical_chain_sha256: str,
    historical_hashes: Mapping[str, str],
) -> str:
    account = values["scheduler_account_authority_sha256"]
    r5_failure = values["r8u_r5_failure_evidence_sha256"]
    probe_receipt = values["locality_sequence_probe_receipt_sha256"]
    probe_accounting = values["locality_sequence_probe_accounting_sha256"]
    capacity_value = values["resume_capacity_receipt_sha256"]
    resume_authority = values["resume_authority_sha256"]
    submission = values["resume_submission_receipt_sha256"]
    gpu_diagnostic = values["gpu_worker_context_diagnostic_sha256"]
    claim = values["publication_claim_sha256"]
    primitive = values["publication_primitive_probe_sha256"]
    final_locality = values["final_publication_locality_sha256"]
    publication = values["publication_receipt_sha256"]
    accounting = values["resume_accounting_sha256"]
    terminal = values["resume_terminal_receipt_sha256"]
    continuation_claim = values["continuation_claim_sha256"]
    continuation_submission = values[
        "continuation_submission_receipt_sha256"
    ]

    r5_root = attempt_root / "r8u_r5_batch16_publication_resume"
    r5_paths = {
        "scheduler_account_authority_sha256": (
            r5_root / "scheduler_account_authority.restricted.json"
        ),
        "worker_context_probe_receipt_sha256": (
            r5_root / "worker_context_probe_receipt.restricted.json"
        ),
        "worker_context_probe_accounting_sha256": (
            r5_root / "worker_context_probe_accounting.restricted.json"
        ),
        "resume_capacity_sha256": r5_root / "resume_capacity.restricted.json",
        "resume_authority_sha256": r5_root / "resume_authority.restricted.json",
        "resume_submission_receipt_sha256": (
            r5_root / "scheduler/resume_submission_receipt.restricted.json"
        ),
        "live_publication_locality_sha256": (
            r5_root / "live_publication_locality.restricted.json"
        ),
        "publication_claim_sha256": (
            attempt_root
            / "extracted_cache/c3_batch_015/.r8u_r5_publication_claim/"
            "claim.restricted.json"
        ),
        "publication_primitive_probe_sha256": (
            r5_root / "publication_primitive_probe.restricted.json"
        ),
        "portable_candidate_authority_sha256": (
            attempt_root
            / "r8u_r4_batch16_publication_resume/"
            "portable_candidate_authority.restricted.json"
        ),
    }
    r5_log = r5_root / "scheduler/lvef_c3_r8u_r5_res_7b7c3657.o7388079"
    try:
        r5_log_info = os.lstat(r5_log)
    except OSError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_R5_FAILURE_EVIDENCE_INVALID"
        ) from exc
    if (
        r5_failure.get("failed_job_id") != "7388079"
        or r5_failure.get("scheduler_failed") != 0
        or r5_failure.get("application_exit_status") != 78
        or r5_failure.get("wall_seconds") != 274
        or r5_failure.get("first_failed_stage")
        != "FINAL_PRE_RENAME_LOCALITY_REVALIDATION"
        or r5_failure.get("exact_failure_code")
        != "R8U_LIVE_PUBLICATION_PARENT_CHANGED"
        or r5_failure.get("scheduler_log_basename")
        != "lvef_c3_r8u_r5_res_7b7c3657.o7388079"
        or r5_failure.get("scheduler_log_bytes") != 117
        or r5_failure.get("scheduler_log_mode") != "0644"
        or r5_failure.get("scheduler_log_sha256")
        != "55cf0f86199f3b1152909a2a6fe814aa9f598b9e7041879a37e33e55557a080e"
        or r5_log_info.st_size != 117
        or stat.S_IMODE(r5_log_info.st_mode) != 0o644
        or sha256_file(r5_log) != r5_failure.get("scheduler_log_sha256")
        or any(
            r5_failure.get(field) != sha256_file(path)
            for field, path in r5_paths.items()
        )
        or r5_failure.get("worker_context_pass") is not True
        or r5_failure.get("portable_candidate_pass") is not True
        or r5_failure.get("initial_locality_pass") is not True
        or r5_failure.get("publication_claim_pass") is not True
        or r5_failure.get("primitive_probe_pass") is not True
        or r5_failure.get("primary_primitive_result")
        != "RENAME_NOREPLACE_UNSUPPORTED_EINVAL"
        or r5_failure.get("final_locality_revalidation_pass") is not False
        or r5_failure.get("publication_ran") is not False
        or r5_failure.get("echoprime_ran") is not False
        or os.path.lexists(r5_root / "publication_receipt.restricted.json")
        or os.path.lexists(r5_root / "resume_accounting.restricted.json")
        or os.path.lexists(r5_root / "resume_terminal.aggregate_safe.json")
        or os.path.lexists(attempt_root / "r8u_r5_continuation_17_19")
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_R5_FAILURE_EVIDENCE_INVALID"
        )

    r6_root = attempt_root / "r8u_r6_batch16_publication_resume"
    probe_authority, probe_authority_sha = _load_r8u_r5_auxiliary_artifact(
        r6_root / "locality_sequence_probe_authority.restricted.json",
        keys=R8U_R6_PROBE_AUTHORITY_KEYS,
        artifact_type="lvef_c3_r8u_r6_locality_sequence_probe_authority_v1",
        status="AUTHORIZED_R8U_R6_LOCALITY_SEQUENCE_PROBE",
        code="R8U_R6_FINALIZER_PROBE_AUTHORITY_INVALID",
    )
    probe_submission, probe_submission_sha = _load_r8u_r5_auxiliary_artifact(
        r6_root / "scheduler/probe_submission_receipt.restricted.json",
        keys=R8U_R6_PROBE_SUBMISSION_KEYS,
        artifact_type="lvef_c3_r8u_r6_locality_sequence_probe_submission_v1",
        status="PASS_EXACT_ONE_R8U_R6_CPU_LOCALITY_SEQUENCE_PROBE_QSUB",
        code="R8U_R6_FINALIZER_PROBE_SUBMISSION_INVALID",
    )
    probe_diagnostic, probe_diagnostic_sha = _load_r8u_r5_auxiliary_artifact(
        r6_root / "locality_sequence_probe_diagnostic.restricted.json",
        keys=R8U_R6_PROBE_DIAGNOSTIC_KEYS,
        artifact_type="lvef_c3_r8u_r6_locality_sequence_diagnostic_v1",
        status="PASS_R8U_R6_STABLE_PUBLICATION_LOCALITY",
        code="R8U_R6_FINALIZER_PROBE_DIAGNOSTIC_INVALID",
    )
    for item in (probe_authority, probe_submission, probe_diagnostic):
        _r8u_r6_validate_current_common(item, authority=authority)

    probe_job_id = str(probe_receipt.get("probe_job_id", ""))
    probe_diagnostic_result = probe_diagnostic.get("primary_result")
    probe_diagnostic_supported = (
        probe_diagnostic_result == "RENAME_NOREPLACE_SUPPORTED"
    )
    volatile_changed = probe_receipt.get("volatile_changed_fields")
    stable_maps = (
        "source_stable_fields_present", "source_parent_stable_fields_present",
        "target_parent_stable_fields_present", "source_stable_fields_equal",
        "source_parent_stable_fields_equal", "target_parent_stable_fields_equal",
    )
    volatile_maps = (
        "source_volatile_fields_present",
        "source_parent_volatile_fields_present",
        "target_parent_volatile_fields_present",
        "source_volatile_fields_equal",
        "source_parent_volatile_fields_equal",
        "target_parent_volatile_fields_equal",
    )
    allowed_volatile_changes = {
        f"{role}.{field}"
        for role in ("source", "source_parent", "target_parent")
        for field in R8U_R6_VOLATILE_IDENTITY_FIELDS
    }
    diagnostic_changed_from_maps = sorted(
        f"{role}.{field}"
        for role, map_name in (
            ("source", "source_volatile_fields_equal"),
            ("source_parent", "source_parent_volatile_fields_equal"),
            ("target_parent", "target_parent_volatile_fields_equal"),
        )
        if isinstance(probe_diagnostic.get(map_name), Mapping)
        for field, equal in probe_diagnostic[map_name].items()
        if equal is False
    )
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", probe_job_id) is None
        or probe_authority.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_authority.get("r8u_r5_failure_evidence_sha256")
        != observed["r8u_r5_failure_evidence_sha256"]
        or probe_authority.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or probe_authority.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or probe_authority.get("worker_role")
        != "R8U_R6_LOCALITY_SEQUENCE_PROBE"
        or probe_authority.get("wall_seconds_maximum") != 600
        or probe_authority.get("cpu_slots") != 1
        or probe_authority.get("gpu_requested") is not False
        or probe_authority.get("array_requested") is not False
        or not _r8u_exact_zero(
            probe_authority,
            "cloud_requests_authorized", "dicom_body_reads_authorized",
            "npz_body_reads_authorized",
        )
        or any(
            probe_authority.get(key) is not False
            for key in (
                "candidate_scan_authorized", "publication_claim_authorized",
                "real_rename_authorized", "extraction_authorized",
                "embedding_generation_authorized", "preservation_authorized",
                "scientific_attempt_mutation_authorized",
            )
        )
        or probe_submission.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_submission.get("probe_authority_sha256") != probe_authority_sha
        or probe_submission.get("r8u_r5_failure_evidence_sha256")
        != observed["r8u_r5_failure_evidence_sha256"]
        or probe_submission.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or probe_submission.get("probe_job_id") != probe_job_id
        or probe_submission.get("probe_job_name")
        != f"lvef_c3_r8u_r6_loc_{authority.implementation_commit[:8]}"
        or probe_submission.get("probe_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {
                "argv": _r8u_r6_expected_probe_qsub_command(
                    attempt_root=attempt_root,
                    implementation_commit=authority.implementation_commit,
                )
            }
        )
        or probe_submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or probe_submission.get("scheduler_submission_count") != 1
        or probe_submission.get("cpu_slots") != 1
        or probe_submission.get("gpu_requested") is not False
        or probe_submission.get("probe_is_array") is not False
        or probe_submission.get("automatic_retry_authorized") is not False
        or probe_diagnostic.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_diagnostic.get("probe_authority_sha256") != probe_authority_sha
        or probe_diagnostic.get("probe_submission_receipt_sha256")
        != probe_submission_sha
        or probe_diagnostic.get("probe_job_id") != probe_job_id
        or probe_diagnostic.get("worker_role")
        != "R8U_R6_LOCALITY_SEQUENCE_PROBE"
        or probe_diagnostic.get("stable_identity_fields")
        != list(R8U_R6_STABLE_IDENTITY_FIELDS)
        or probe_diagnostic.get("volatile_identity_fields")
        != list(R8U_R6_VOLATILE_IDENTITY_FIELDS)
        or any(
            not isinstance(probe_diagnostic.get(field), Mapping)
            or set(probe_diagnostic[field])
            != set(R8U_R6_STABLE_IDENTITY_FIELDS)
            or any(item is not True for item in probe_diagnostic[field].values())
            for field in stable_maps
        )
        or any(
            not isinstance(probe_diagnostic.get(field), Mapping)
            or set(probe_diagnostic[field])
            != set(R8U_R6_VOLATILE_IDENTITY_FIELDS)
            for field in volatile_maps
        )
        or any(
            item is not True
            for field in volatile_maps[:3]
            for item in probe_diagnostic[field].values()
        )
        or any(
            type(item) is not bool
            for field in volatile_maps[3:]
            for item in probe_diagnostic[field].values()
        )
        or not isinstance(probe_diagnostic.get("volatile_changed_fields"), list)
        or probe_diagnostic.get("volatile_changed_fields")
        != sorted(set(probe_diagnostic["volatile_changed_fields"]))
        or not set(probe_diagnostic["volatile_changed_fields"])
        <= allowed_volatile_changes
        or probe_diagnostic["volatile_changed_fields"]
        != diagnostic_changed_from_maps
        or probe_diagnostic.get("volatile_metadata_classification")
        != (
            "R8U_PUBLICATION_VOLATILE_METADATA_ONLY_CHANGED"
            if probe_diagnostic["volatile_changed_fields"] else "NONE"
        )
        or any(
            probe_diagnostic.get(key) is not True
            for key in (
                "target_absent_before_probe", "target_absent_after_probe",
                "same_mounted_filesystem_before_probe",
                "same_mounted_filesystem_after_probe", "mount_identity_equal",
                "probe_cleanup_passed",
            )
        )
        or probe_diagnostic.get("primary_primitive")
        != "RENAMEAT2_RENAME_NOREPLACE"
        or probe_diagnostic_result
        not in {
            "RENAME_NOREPLACE_SUPPORTED",
            "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        }
        or probe_diagnostic.get("primary_errno")
        != ("NONE" if probe_diagnostic_supported else "EINVAL")
        or probe_diagnostic.get("probe_directories_created") != 2
        or probe_diagnostic.get("probe_directories_removed") != 2
        or not _r8u_valid_hashes(
            probe_diagnostic,
            "worker_diagnostic_sha256", "worker_qstat_projection_sha256",
            "worker_process_projection_sha256",
        )
        or not _r8u_exact_zero(
            probe_diagnostic,
            "candidate_scans", "cloud_requests", "dicom_body_reads",
            "npz_body_reads", "publication_claims", "real_renames",
            "extraction_executions", "embedding_generations",
            "preservation_executions", "scientific_attempt_mutations",
        )
        or probe_receipt.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_receipt.get("probe_authority_sha256") != probe_authority_sha
        or probe_receipt.get("probe_submission_receipt_sha256")
        != probe_submission_sha
        or probe_receipt.get("probe_diagnostic_sha256") != probe_diagnostic_sha
        or probe_receipt.get("worker_role")
        != "R8U_R6_LOCALITY_SEQUENCE_PROBE"
        or probe_receipt.get("worker_scheduler_context_pass") is not True
        or probe_receipt.get("stable_fields_equal") is not True
        or not isinstance(volatile_changed, list)
        or volatile_changed != probe_diagnostic.get("volatile_changed_fields")
        or probe_receipt.get("target_absent") is not True
        or probe_receipt.get("same_mounted_filesystem") is not True
        or probe_receipt.get("primitive_probe_pass") is not True
        or probe_receipt.get("probe_cleanup_passed") is not True
        or not _r8u_exact_zero(
            probe_receipt,
            "candidate_scans", "cloud_requests", "dicom_body_reads",
            "npz_body_reads", "publication_claims", "real_renames",
            "scientific_attempt_mutations",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_LOCALITY_SEQUENCE_PROBE_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            probe_submission.get("probe_qsub_evidence"),
            accepted_stdout=[
                f"{probe_job_id}\n".encode("ascii"),
                probe_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_recovery_accounting(
            probe_accounting.get("accounting_projection"),
            expected_job_id=probe_job_id,
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_LOCALITY_SEQUENCE_PROBE_INVALID"
        ) from exc
    if (
        probe_accounting.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or probe_accounting.get("probe_authority_sha256") != probe_authority_sha
        or probe_accounting.get("probe_submission_receipt_sha256")
        != probe_submission_sha
        or probe_accounting.get("probe_job_id") != probe_job_id
        or probe_accounting.get("failed") != 0
        or probe_accounting.get("exit_status") != 0
        or probe_accounting.get("probe_receipt_sha256")
        != observed["locality_sequence_probe_receipt_sha256"]
        or not _r8u_valid_hashes(
            probe_accounting, "probe_scheduler_log_sha256"
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_LOCALITY_SEQUENCE_PROBE_INVALID"
        )

    r4_capacity_path = (
        attempt_root
        / "r8u_r4_batch16_publication_resume/resume_capacity.restricted.json"
    )
    fresh_capacity = capacity_value.get("fresh_capacity_observation")
    if (
        capacity_value.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or capacity_value.get("locality_sequence_probe_accounting_sha256")
        != observed["locality_sequence_probe_accounting_sha256"]
        or capacity_value.get("r8u_r4_capacity_authority_sha256")
        != sha256_file(r4_capacity_path)
        or not isinstance(fresh_capacity, Mapping)
        or capacity_value.get("fresh_capacity_observation_sha256")
        != core.canonical_json_sha256(fresh_capacity)
        or capacity_value.get("candidate_seal_sha256")
        != observed["r3_extraction_candidate_seal_sha256"]
        or capacity_value.get("candidate_total_bytes") != candidate_total_bytes
        or capacity_value.get("quota_reserve_bytes") != 200_000_000_000
        or capacity_value.get("physical_reserve_bytes") != 200_000_000_000
        or capacity_value.get("file_slot_reserve_pass") is not True
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )

    expected_script_authority = {
        "controller_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "lvef_c3_r8r_recovery_continuation.py"
        ),
        "full_sequential_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_full_sequential.py"
        ),
        "production_stages_sha256": sha256_file(
            Path(__file__).resolve().parent / "lvef_c3_production_stages.py"
        ),
        "preservation_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "preserve_lvef_c3_production_batch.py"
        ),
        "retirement_sha256": sha256_file(
            Path(__file__).resolve().parent
            / "retire_lvef_c3_extracted_cache_v2.py"
        ),
        "finalizer_sha256": sha256_file(Path(__file__).resolve()),
        "runner_sha256": sha256_file(
            Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME
        ),
    }
    expected_prefix = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(15)
    ]
    resume_job_id = str(submission.get("resume_job_id", ""))
    resume_job_name = f"lvef_c3_r8u_r6_res_{authority.implementation_commit[:8]}"
    if probe_authority.get("script_authority") != dict(
        sorted(expected_script_authority.items())
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_PROBE_AUTHORITY_INVALID"
        )
    if (
        resume_authority.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or resume_authority.get("r8u_r5_failure_evidence_sha256")
        != observed["r8u_r5_failure_evidence_sha256"]
        or resume_authority.get("locality_sequence_probe_receipt_sha256")
        != observed["locality_sequence_probe_receipt_sha256"]
        or resume_authority.get("locality_sequence_probe_accounting_sha256")
        != observed["locality_sequence_probe_accounting_sha256"]
        or resume_authority.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or resume_authority.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or resume_authority.get("prefix_final_receipt_sha256") != expected_prefix
        or resume_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or resume_authority.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or resume_authority.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or resume_authority.get("worker_role")
        != "R8U_R6_BATCH16_PUBLICATION_RESUME"
        or not _r8u_r6_exact_values(
            resume_authority,
            {
                "cloud_requests_authorized": 0,
                "downloads_authorized": 0,
                "dicom_body_reads_authorized": 0,
                "dicom_extraction_executions_authorized": 0,
                "echoprime_executions_authorized": 1,
                "gpu_executions_authorized": 1,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
                "maximum_new_gpu_resume_qsubs": 1,
            },
        )
        or re.fullmatch(r"[1-9][0-9]{0,19}", resume_job_id) is None
        or resume_job_id == probe_job_id
        or submission.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or submission.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or submission.get("locality_sequence_probe_receipt_sha256")
        != observed["locality_sequence_probe_receipt_sha256"]
        or submission.get("locality_sequence_probe_accounting_sha256")
        != observed["locality_sequence_probe_accounting_sha256"]
        or submission.get("resume_capacity_sha256")
        != observed["resume_capacity_receipt_sha256"]
        or submission.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or submission.get("resume_job_name") != resume_job_name
        or submission.get("resume_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {
                "argv": _r8u_r6_expected_resume_qsub_command(
                    attempt_root=attempt_root,
                    implementation_commit=authority.implementation_commit,
                )
            }
        )
        or submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or submission.get("scheduler_submission_count") != 1
        or submission.get("resume_is_array") is not False
        or submission.get("gpu_requested") is not True
        or submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            submission,
            "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_RESUME_AUTHORITY_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            submission.get("resume_qsub_evidence"),
            accepted_stdout=[
                f"{resume_job_id}\n".encode("ascii"),
                resume_job_id.encode("ascii"),
            ],
        )
        _validate_r8u_r6_qstat_projection(
            submission.get("initial_qstat_projection"),
            job_id=resume_job_id,
            job_name=resume_job_name,
            expected_status=(
                "PASS_EXACT_ONE_R8U_R6_SUBMITTED_JOB_ZERO_COMPETITORS"
            ),
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_RESUME_SUBMISSION_INVALID"
        ) from exc

    return _validate_r8u_r6_successor_chain(
        attempt_root=attempt_root,
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=expected_runtime_authority,
        values=values,
        observed=observed,
        candidate_total_bytes=candidate_total_bytes,
        expected_script_authority=expected_script_authority,
        resume_job_id=resume_job_id,
        account=account,
        gpu_diagnostic=gpu_diagnostic,
        claim=claim,
        primitive=primitive,
        final_locality=final_locality,
        publication=publication,
        accounting=accounting,
        terminal=terminal,
        continuation_claim=continuation_claim,
        continuation_submission=continuation_submission,
        historical_chain_sha256=historical_chain_sha256,
        historical_hashes=historical_hashes,
    )


def _validate_r8u_r6_successor_chain(
    *,
    attempt_root: Path,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR6ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    values: Mapping[str, Mapping[str, Any]],
    observed: Mapping[str, str],
    candidate_total_bytes: int,
    expected_script_authority: Mapping[str, str],
    resume_job_id: str,
    account: Mapping[str, Any],
    gpu_diagnostic: Mapping[str, Any],
    claim: Mapping[str, Any],
    primitive: Mapping[str, Any],
    final_locality: Mapping[str, Any],
    publication: Mapping[str, Any],
    accounting: Mapping[str, Any],
    terminal: Mapping[str, Any],
    continuation_claim: Mapping[str, Any],
    continuation_submission: Mapping[str, Any],
    historical_chain_sha256: str,
    historical_hashes: Mapping[str, str],
) -> str:
    if (
        any(
            gpu_diagnostic.get(key) is not True
            for key in (
                "effective_uid_match", "job_id_match", "task_context_match",
                "job_role_match", "runner_sha256_match", "python_sha256_match",
                "implementation_commit_match", "qsub_environment_sha256_match",
            )
        )
        or gpu_diagnostic.get("canonical_worker_environment_status") != "PASS"
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_WORKER_CONTEXT_DIAGNOSTIC_INVALID"
        )

    claim_process_sha = claim.get("worker_process_projection_sha256")
    claim_qstat_sha = claim.get("worker_qstat_projection_sha256")
    if (
        claim.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or claim.get("resume_job_id") != resume_job_id
        or claim.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or claim.get("r8u_r5_failure_evidence_sha256")
        != observed["r8u_r5_failure_evidence_sha256"]
        or claim.get("resume_authority_sha256")
        != observed["resume_authority_sha256"]
        or claim.get("resume_submission_receipt_sha256")
        != observed["resume_submission_receipt_sha256"]
        or claim.get("worker_context_diagnostic_sha256")
        != observed["gpu_worker_context_diagnostic_sha256"]
        or not _r8u_valid_hashes(
            claim,
            "worker_process_projection_sha256",
            "worker_qstat_projection_sha256",
        )
        or claim.get("target_role")
        != "extracted_cache/c3_batch_015/dicom_extraction"
        or claim.get("target_absent") is not True
        or claim.get("competing_active_jobs") != 0
        or claim.get("competing_active_processes") != 0
        or not _r8u_exact_zero(
            claim,
            "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "npz_body_reads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_PUBLICATION_CLAIM_INVALID"
        )

    primitive_result = primitive.get("primary_result")
    primitive_supported = primitive_result == "RENAME_NOREPLACE_SUPPORTED"
    expected_probe = _r8u_r3_expected_proceedable_probe_outcome(primitive_result)
    if (
        primitive.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or primitive.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or primitive.get("primary_primitive") != "RENAMEAT2_RENAME_NOREPLACE"
        or primitive_result
        not in {
            "RENAME_NOREPLACE_SUPPORTED",
            "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        }
        or expected_probe is None
        or (
            primitive.get("primary_errno_number"),
            primitive.get("primary_errno"),
            primitive.get("primary_returned_success"),
        )
        != expected_probe
        or primitive.get("probe_source_present_after")
        is not (not primitive_supported)
        or primitive.get("probe_target_present_after") is not primitive_supported
        or primitive.get("probe_target_exact_after") is not primitive_supported
        or primitive.get("probe_cleanup_passed") is not True
        or primitive.get("probe_directories_created") != 2
        or primitive.get("probe_directories_removed") != 2
        or not _r8u_exact_zero(
            primitive,
            "scientific_file_body_reads", "npz_body_reads",
            "dicom_body_reads", "dicom_extraction_executions",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_PUBLICATION_PROBE_INVALID"
        )

    volatile_changed = final_locality.get("volatile_changed_fields")
    allowed_changed = {
        f"{role}.{field}"
        for role in ("source", "source_parent", "target_parent")
        for field in R8U_R6_VOLATILE_IDENTITY_FIELDS
    }
    volatile_equality_maps = (
        "source_volatile_fields_equal",
        "source_parent_volatile_fields_equal",
        "target_parent_volatile_fields_equal",
    )
    locality_changed_from_maps = sorted(
        f"{role}.{field}"
        for role, map_name in (
            ("source", "source_volatile_fields_equal"),
            ("source_parent", "source_parent_volatile_fields_equal"),
            ("target_parent", "target_parent_volatile_fields_equal"),
        )
        if isinstance(final_locality.get(map_name), Mapping)
        for field, equal in final_locality[map_name].items()
        if equal is False
    )
    if (
        final_locality.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or final_locality.get("worker_context_diagnostic_sha256")
        != observed["gpu_worker_context_diagnostic_sha256"]
        or final_locality.get("worker_process_projection_sha256")
        != claim_process_sha
        or final_locality.get("worker_qstat_projection_sha256")
        != claim_qstat_sha
        or final_locality.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or final_locality.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or final_locality.get("stable_identity_fields")
        != list(R8U_R6_STABLE_IDENTITY_FIELDS)
        or final_locality.get("volatile_identity_fields")
        != list(R8U_R6_VOLATILE_IDENTITY_FIELDS)
        or any(
            final_locality.get(key) is not True
            for key in (
                "source_exists_safe_directory", "target_absent",
                "source_target_same_mounted_filesystem", "parents_nonsymlinked",
                "owner_mode_valid", "source_stable_identity_equal",
                "source_parent_stable_identity_equal",
                "target_parent_stable_identity_equal",
                "captured_after_primitive_probe", "probe_cleanup_validated",
            )
        )
        or any(
            not isinstance(final_locality.get(field), Mapping)
            or set(final_locality[field])
            != set(R8U_R6_VOLATILE_IDENTITY_FIELDS)
            or any(type(item) is not bool for item in final_locality[field].values())
            for field in volatile_equality_maps
        )
        or not isinstance(volatile_changed, list)
        or volatile_changed != sorted(set(volatile_changed))
        or not set(volatile_changed) <= allowed_changed
        or volatile_changed != locality_changed_from_maps
        or final_locality.get("volatile_metadata_classification")
        != (
            "R8U_PUBLICATION_VOLATILE_METADATA_ONLY_CHANGED"
            if volatile_changed else "NONE"
        )
        or not _r8u_valid_hashes(
            final_locality,
            "source_stable_identity_sha256",
            "source_parent_stable_identity_sha256",
            "target_parent_stable_identity_sha256",
            "source_mount_identity_sha256", "target_mount_identity_sha256",
        )
        or final_locality.get("source_mount_identity_sha256")
        != final_locality.get("target_mount_identity_sha256")
        or final_locality.get("competing_active_jobs") != 0
        or final_locality.get("competing_active_processes") != 0
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_FINAL_PUBLICATION_LOCALITY_INVALID"
        )

    if (
        publication.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or publication.get("portable_candidate_authority_sha256")
        != observed["portable_candidate_authority_sha256"]
        or publication.get("r8u_r5_failure_evidence_sha256")
        != observed["r8u_r5_failure_evidence_sha256"]
        or publication.get("final_publication_locality_sha256")
        != observed["final_publication_locality_sha256"]
        or publication.get("publication_primitive_probe_sha256")
        != observed["publication_primitive_probe_sha256"]
        or publication.get("publication_claim_sha256")
        != observed["publication_claim_sha256"]
        or publication.get("primitive_attempted")
        != (
            "RENAMEAT2_RENAME_NOREPLACE"
            if primitive_supported
            else "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
        )
        or publication.get("primary_result") != primitive_result
        or publication.get("fallback_used") is not (not primitive_supported)
        or publication.get("source_absent") is not True
        or publication.get("target_exact") is not True
        or publication.get("candidate_npz_files") != 10_187
        or publication.get("candidate_total_bytes") != candidate_total_bytes
        or publication.get("files_moved") != 10_187
        or publication.get("files_copied") != 0
        or publication.get("files_deleted_independently") != 0
        or publication.get("publication_attempts") != 1
        or not _r8u_valid_hashes(
            publication,
            "prepublication_candidate_sha256",
            "postpublication_root_stable_identity_sha256",
        )
        or publication.get("prepublication_candidate_sha256")
        != values["portable_candidate_authority_sha256"].get(
            "candidate_relative_file_portable_projection_sha256"
        )
        or publication.get("postpublication_root_stable_identity_sha256")
        != final_locality.get("source_stable_identity_sha256")
        or (
            publication.get("rename_returned_success") is False
            and publication.get("real_rename_errno_number")
            not in {errno.ENOENT, getattr(errno, "ESTALE", errno.ENOENT)}
        )
        or not _r8u_exact_zero(
            publication,
            "dicom_body_reads", "dicom_extraction_executions",
            "npz_body_reads", "cloud_requests", "downloads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_PUBLICATION_RECEIPT_INVALID"
        )
    try:
        _validate_r8u_r3_real_rename_outcome(
            returned_success=publication.get("rename_returned_success"),
            errno_number=publication.get("real_rename_errno_number"),
            errno_name=publication.get("real_rename_errno"),
            errno_classification=publication.get(
                "real_rename_errno_classification"
            ),
            publication_ruling=publication.get("publication_ruling"),
        )
        _validate_r8u_r3_resume_accounting(
            accounting, resume_job_id=resume_job_id
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_TERMINAL_ACCOUNTING_INVALID"
        ) from exc

    batch16_receipt_path = receipt_paths_by_batch["c3_batch_015"]
    batch16_receipt = load_json(batch16_receipt_path, "R8U_R6_BATCH16_RECEIPT")
    batch16_paths = {
        "preservation_receipt_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root / "cache_retirement_authorizations/"
            "c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            attempt_root / "batches/c3_batch_015/final_resume_ledger.restricted.json"
        ),
        "batch_finalization_receipt_sha256": batch16_receipt_path,
    }
    terminal_links = {
        "scheduler_account_authority_sha256": (
            "scheduler_account_authority_sha256"
        ),
        "r8u_r5_failure_evidence_sha256": "r8u_r5_failure_evidence_sha256",
        "locality_sequence_probe_receipt_sha256": (
            "locality_sequence_probe_receipt_sha256"
        ),
        "locality_sequence_probe_accounting_sha256": (
            "locality_sequence_probe_accounting_sha256"
        ),
        "final_publication_locality_sha256": (
            "final_publication_locality_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
    }
    terminal_count_links = {
        "n_selected_studies": "n_selected_studies",
        "n_expected_objects": "n_expected_objects",
        "expected_source_bytes": "expected_source_bytes",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_clip_embeddings": "n_clip_embeddings",
        "n_pooled_studies": "n_pooled_studies",
        "n_no_cine_studies": "n_no_cine_studies",
        "n_new_no_cine_studies": "n_new_no_cine_studies",
        "object_substitution_count": "object_substitution_count",
        "unaccounted_multiframe_objects": "unaccounted_multiframe_objects",
    }
    if (
        any(terminal.get(key) != sha256_file(path) for key, path in batch16_paths.items())
        or any(
            terminal.get(terminal_key) != observed[observed_key]
            for terminal_key, observed_key in terminal_links.items()
        )
        or terminal.get("worker_context_diagnostic_sha256")
        != observed["gpu_worker_context_diagnostic_sha256"]
        or any(
            terminal.get(terminal_key) != batch16_receipt.get(receipt_key)
            for terminal_key, receipt_key in terminal_count_links.items()
        )
        or terminal.get("raw_dicoms_retained") is not True
        or terminal.get("canonical_extraction_cache_retired") is not True
        or terminal.get("failed_partial_cache_retained") is not True
        or terminal.get("source_candidate_npz_files") != 10_187
        or terminal.get("echoprime_executions") != 1
        or terminal.get("embedding_generations") != 1
        or terminal.get("gpu_executions") != 1
        or not _r8u_exact_zero(
            terminal,
            "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "model_fitting_count",
            "prediction_generation_count",
            "confirmatory_performance_access_count",
        )
        or terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_TERMINAL_RECEIPT_INVALID"
        )

    continuation_links = {
        "scheduler_account_authority_sha256": (
            "scheduler_account_authority_sha256"
        ),
        "r8u_r5_failure_evidence_sha256": "r8u_r5_failure_evidence_sha256",
        "locality_sequence_probe_receipt_sha256": (
            "locality_sequence_probe_receipt_sha256"
        ),
        "locality_sequence_probe_accounting_sha256": (
            "locality_sequence_probe_accounting_sha256"
        ),
        "portable_candidate_authority_sha256": (
            "portable_candidate_authority_sha256"
        ),
        "final_publication_locality_sha256": (
            "final_publication_locality_sha256"
        ),
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_receipt_sha256": "publication_receipt_sha256",
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
        "resume_accounting_sha256": "resume_accounting_sha256",
        "resume_terminal_receipt_sha256": "resume_terminal_receipt_sha256",
    }
    prefix16 = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(16)
    ]
    if (
        continuation_claim.get("prior_implementation_commit")
        != R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT
        or continuation_claim.get("prefix_final_receipt_sha256") != prefix16
        or continuation_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_claim.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or continuation_claim.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or continuation_claim.get("script_authority")
        != dict(sorted(expected_script_authority.items()))
        or continuation_claim.get("continuation_task_range") != "17-19"
        or not _r8u_r6_exact_values(
            continuation_claim,
            {
                "continuation_task_count": 3,
                "continuation_max_concurrency": 1,
                "held_finalizer_count": 1,
                "total_new_qsub_maximum": 4,
                "automatic_retry_authorized": False,
                "whole_stage_retry_authorized": False,
                "fifth_submission_reachable": False,
                "cloud_requests_by_submitter": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(continuation_submission.get("finalizer_job_id", ""))
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME)
    scheduler_root = attempt_root / "r8u_r6_continuation_17_19/scheduler"
    common_qsub = [qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho"]
    array_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r6_seq_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-t", "17-19", "-tc", "1",
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r6_fin_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-hold_jid", array_job_id,
        "-l", "h_rt=12:00:00", "-pe", "omp", "4",
        "-l", "mem_per_core=8G", runner,
    ]
    if (
        continuation_submission.get("resume_job_id") != resume_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_r6_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_r6_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({resume_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != account.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_submission.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or not _r8u_r6_exact_values(
            continuation_submission,
            {
                "scheduler_submission_count": 2,
                "total_new_qsub_submissions": 4,
                "scheduler_submission_maximum": 4,
                "array_task_range": "17-19",
                "array_task_count": 3,
                "array_max_concurrency": 1,
                "finalizer_held_on_array": True,
                "whole_stage_retry_authorized": False,
                "fifth_submission_reachable": False,
                "cloud_requests": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode("ascii"),
                f"{array_job_id}.17-19:1".encode("ascii"),
                f"{array_job_id}\n".encode("ascii"),
                array_job_id.encode("ascii"),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode("ascii"),
                finalizer_job_id.encode("ascii"),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": historical_chain_sha256,
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _r8u_r7_current_script_authority() -> dict[str, str]:
    script_root = Path(__file__).resolve().parent
    return {
        "controller_sha256": sha256_file(
            script_root / "lvef_c3_r8r_recovery_continuation.py"
        ),
        "full_sequential_sha256": sha256_file(
            script_root / "lvef_c3_full_sequential.py"
        ),
        "production_stages_sha256": sha256_file(
            script_root / "lvef_c3_production_stages.py"
        ),
        "preservation_sha256": sha256_file(
            script_root / "preserve_lvef_c3_production_batch.py"
        ),
        "retirement_sha256": sha256_file(
            script_root / "retire_lvef_c3_extracted_cache_v2.py"
        ),
        "finalizer_sha256": sha256_file(Path(__file__).resolve()),
        "runner_sha256": sha256_file(
            script_root / R8R_SCHEDULER_RUNNER_BASENAME
        ),
    }


def _r8u_r7_historical_r6_script_authority() -> dict[str, str]:
    """Hash the exact R6 script blobs without treating them as current files."""

    repository = Path(__file__).resolve().parent.parent
    relative_paths = {
        "controller_sha256": "scripts/lvef_c3_r8r_recovery_continuation.py",
        "full_sequential_sha256": "scripts/lvef_c3_full_sequential.py",
        "production_stages_sha256": "scripts/lvef_c3_production_stages.py",
        "preservation_sha256": "scripts/preserve_lvef_c3_production_batch.py",
        "retirement_sha256": "scripts/retire_lvef_c3_extracted_cache_v2.py",
        "finalizer_sha256": "scripts/finalize_lvef_c3_production.py",
        "runner_sha256": "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh",
    }
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    result: dict[str, str] = {}
    for field, relative_path in sorted(relative_paths.items()):
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/git", "-C", str(repository), "show",
                    f"{R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT}:"
                    f"{relative_path}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_HISTORICAL_R6_SCRIPT_AUTHORITY_INVALID"
            ) from exc
        if completed.returncode != 0 or completed.stderr:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_HISTORICAL_R6_SCRIPT_AUTHORITY_INVALID"
            )
        result[field] = hashlib.sha256(completed.stdout).hexdigest()
    return result


def _r8u_r7_validate_worker_diagnostic(value: Mapping[str, Any]) -> None:
    if (
        value.get("canonical_worker_environment_status") != "PASS"
        or not isinstance(value.get("classifications"), list)
        or any(
            value.get(field) is not True
            for field in (
                "effective_uid_match", "job_id_match", "task_context_match",
                "job_role_match", "runner_sha256_match", "python_sha256_match",
                "implementation_commit_match", "qsub_environment_sha256_match",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_WORKER_CONTEXT_DIAGNOSTIC_INVALID"
        )


def _r8u_r7_scheduler_log_authority(
    path: Path, *, code: str,
) -> tuple[bytes, str, str]:
    """Read one fixed Grid Engine log through a stable no-follow descriptor."""

    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        visible = os.lstat(path)

        def identity(value: os.stat_result) -> tuple[int, ...]:
            return (
                value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                value.st_gid, value.st_nlink, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns,
            )

        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o600, 0o644}
            or before.st_size < 1
            or before.st_size > 1024 * 1024
            or identity(before) != identity(visible)
        ):
            raise ProductionFinalizationError(code)
        payload = b""
        while len(payload) <= 1024 * 1024:
            block = os.read(descriptor, min(64 * 1024, 1024 * 1024 + 1 - len(payload)))
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        visible_after = os.lstat(path)
        if (
            len(payload) != before.st_size
            or identity(before) != identity(after)
            or identity(before) != identity(visible_after)
        ):
            raise ProductionFinalizationError(code)
        return (
            payload,
            f"{stat.S_IMODE(before.st_mode):04o}",
            hashlib.sha256(payload).hexdigest(),
        )
    except ProductionFinalizationError:
        raise
    except OSError as exc:
        raise ProductionFinalizationError(code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_r8u_r7_chain_artifacts(
    *,
    receipt_paths_by_batch: Mapping[str, Path],
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR7ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    """Validate the immutable R6 publication and the R7 recovery successor."""

    first_path = receipt_paths_by_batch.get("c3_batch_000")
    if not isinstance(first_path, Path) or len(first_path.parents) < 4:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )
    attempt_root = first_path.parents[3]
    canonical_receipt_paths = {
        batch_id: (
            attempt_root / "batches" / batch_id / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in EXPECTED_BATCH_IDS
    }
    chain_paths = {
        field: attempt_root / relative_path
        for field, relative_path, _artifact_type, _status, _epoch_kind
        in R8U_R7_CHAIN_ARTIFACT_SPECS
    }
    chain_hashes = {getattr(authority, field) for field in chain_paths}
    if (
        attempt_root.name != R8R_ATTEMPT_ID
        or attempt_root.parent.name != "attempts"
        or dict(receipt_paths_by_batch) != canonical_receipt_paths
        or len(chain_paths) != len(R8U_R7_CHAIN_ARTIFACT_SPECS)
        or len(set(chain_paths.values())) != len(chain_paths)
        or any(path in receipt_paths_by_batch.values() for path in chain_paths.values())
        or not chain_hashes.isdisjoint(receipt_hashes_by_batch.values())
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CHAIN_ARTIFACT_INVALID"
        )

    historical_authority = R8RImplementationAuthority(
        implementation_commit=R8U_PRIOR_IMPLEMENTATION_COMMIT,
        recovery_authority_sha256=(
            authority.historical_r8r_recovery_authority_sha256
        ),
        recovery_terminal_receipt_sha256=(
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        continuation_capacity_receipt_sha256=(
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        continuation_claim_sha256=(
            authority.historical_r8r_continuation_claim_sha256
        ),
        continuation_submission_receipt_sha256=(
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    )
    historical_chain_sha256 = _validate_r8r_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        authority=historical_authority,
        expected_runtime_authority=expected_runtime_authority,
        historical_script_authority=R8U_FE3_GIT_TREE_SHA256,
    )
    historical_hashes = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    if historical_hashes != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH"
        )

    values: dict[str, Mapping[str, Any]] = {}
    observed: dict[str, str] = {}
    for field, relative_path, artifact_type, status, epoch_kind in (
        R8U_R7_CHAIN_ARTIFACT_SPECS
    ):
        value, digest = _load_r8u_r7_chain_artifact(
            path=attempt_root / relative_path,
            field=field,
            artifact_type=artifact_type,
            status=status,
            epoch_kind=epoch_kind,
            authority=authority,
            plan=plan,
        )
        values[field] = value
        observed[field] = digest

    candidate_total_bytes = _r8u_r6_validate_portable_history(
        values=values, observed=observed
    )
    r6_account = values["r8u_r6_scheduler_account_authority_sha256"]
    r7_account = values["scheduler_account_authority_sha256"]
    _validate_r8u_r6_scheduler_account(r6_account)
    _validate_r8u_r7_scheduler_account(r7_account)
    historical_script_authority = _r8u_r7_historical_r6_script_authority()
    current_script_authority = _r8u_r7_current_script_authority()
    if (
        r6_account.get("runner_sha256")
        != historical_script_authority["runner_sha256"]
        or r7_account.get("runner_sha256")
        != current_script_authority["runner_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCHEDULER_ACCOUNT_INVALID"
        )

    r6_submission = values["resume_submission_receipt_sha256"]
    r6_job_id = str(r6_submission.get("resume_job_id", ""))
    r6_job_name = (
        "lvef_c3_r8u_r6_res_"
        f"{R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT[:8]}"
    )
    r6_failure = values["r8u_r6_failure_evidence_sha256"]
    r6_log = (
        attempt_root / "r8u_r6_batch16_publication_resume/scheduler"
        / f"{r6_job_name}.o{r6_job_id}"
    )
    log_payload, log_mode, log_sha256 = _r8u_r7_scheduler_log_authority(
        r6_log,
        code="R8U_R7_FINALIZER_R6_FAILURE_EVIDENCE_INVALID",
    )
    log_bytes = len(log_payload)
    accounting_projection = r6_failure.get("accounting_projection")
    if not isinstance(accounting_projection, Mapping):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_FAILURE_EVIDENCE_INVALID"
        )
    try:
        start_time = datetime.strptime(
            str(accounting_projection.get("start_time", "")),
            "%a %b %d %H:%M:%S %Y",
        )
        end_time = datetime.strptime(
            str(accounting_projection.get("end_time", "")),
            "%a %b %d %H:%M:%S %Y",
        )
        accounted_wall_seconds = float(
            str(accounting_projection.get("ru_wallclock_seconds", ""))
        )
    except (TypeError, ValueError) as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_FAILURE_EVIDENCE_INVALID"
        ) from exc
    if (
        set(accounting_projection) != R8R_RECOVERY_ACCOUNTING_KEYS
        or accounting_projection.get("status")
        != "PASS_FIXED_R8U_R6_QACCT_FAILED_0_EXIT_78"
        or accounting_projection.get("job_id") != "7407005"
        or accounting_projection.get("task_id") not in {"NONE", "undefined"}
        or accounting_projection.get("failed") != 0
        or accounting_projection.get("exit_status") != 78
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:[.][0-9]+)?",
            str(accounting_projection.get("ru_wallclock_seconds")),
        )
        is None
        or int(accounted_wall_seconds) != 1_714
        or end_time < start_time
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_FAILURE_EVIDENCE_INVALID"
        )

    batch16_root = attempt_root / "batches/c3_batch_015"
    scientific_paths = {
        "extraction_ledger_sha256": (
            batch16_root / "extraction_resume_ledger.restricted.json"
        ),
        "pooling_ledger_sha256": (
            batch16_root / "pooling_resume_ledger.restricted.json"
        ),
        "clip_embeddings_sha256": (
            batch16_root / "echoprime/clip_embeddings.restricted.npz"
        ),
        "clip_manifest_sha256": (
            batch16_root / "echoprime/clip_manifest.restricted.csv"
        ),
        "study_embeddings_sha256": (
            batch16_root / "echoprime/study_embeddings.restricted.npz"
        ),
        "study_manifest_sha256": (
            batch16_root / "echoprime/study_manifest.restricted.csv"
        ),
        "embedding_summary_sha256": (
            batch16_root / "echoprime/echoprime_pooling.summary.json"
        ),
        "failed_partial_seal_sha256": (
            attempt_root / "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json"
        ),
    }
    r6_failure_links = {
        "scheduler_account_authority_sha256": (
            "r8u_r6_scheduler_account_authority_sha256"
        ),
        "locality_sequence_probe_receipt_sha256": (
            "locality_sequence_probe_receipt_sha256"
        ),
        "locality_sequence_probe_accounting_sha256": (
            "locality_sequence_probe_accounting_sha256"
        ),
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
        "worker_context_diagnostic_sha256": (
            "gpu_worker_context_diagnostic_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "final_publication_locality_sha256": (
            "final_publication_locality_sha256"
        ),
        "publication_receipt_sha256": "publication_receipt_sha256",
    }
    if (
        r6_job_id != "7407005"
        or r6_submission.get("resume_job_name") != r6_job_name
        or r6_failure.get("failed_job_id") != "7407005"
        or r6_failure.get("scheduler_failed") != 0
        or r6_failure.get("application_exit_status") != 78
        or r6_failure.get("wall_seconds") != 1714
        or r6_failure.get("first_failed_stage")
        != "PRESERVATION_RETIREMENT_FINALIZATION"
        or r6_failure.get("exact_failure_code")
        != "R8U_R3_EXTRACTION_NPZ_METADATA_INVALID"
        or r6_failure.get("scheduler_log_basename") != r6_log.name
        or r6_failure.get("scheduler_log_bytes") != log_bytes
        or r6_failure.get("scheduler_log_mode") != log_mode
        or r6_failure.get("scheduler_log_sha256") != log_sha256
        or log_bytes != 177
        or log_mode != "0644"
        or log_sha256
        != "a15108e203035b435611e7a61a0c5baf8a713450a7a251b3f585da0e094ad8c3"
        or any(
            r6_failure.get(link_key) != observed[observed_key]
            for link_key, observed_key in r6_failure_links.items()
        )
        or any(
            r6_failure.get(field) != sha256_file(path)
            for field, path in scientific_paths.items()
        )
        or r6_failure.get("publication_status")
        != "PASS_R8U_R6_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER"
        or r6_failure.get("publication_ruling") != "PUBLICATION_PASS"
        or r6_failure.get("worker_scheduler_context_status") != "PASS"
        or r6_failure.get("echoprime_status") != "PASS"
        or not _r8u_r6_exact_values(
            r6_failure,
            {
                "published_npz_files": 10_187,
                "clip_embeddings": 10_187,
                "study_embeddings": 250,
                "technical_dispositions": 0,
                "blocking_failures": 0,
                "dicom_extraction_reruns": 0,
                "dicom_body_reads": 0,
                "cloud_requests": 0,
                "echoprime_executions": 1,
                "embedding_generations": 1,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_FAILURE_EVIDENCE_INVALID"
        )

    return _validate_r8u_r7_recovery_successor(
        attempt_root=attempt_root,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=expected_runtime_authority,
        values=values,
        observed=observed,
        candidate_total_bytes=candidate_total_bytes,
        historical_chain_sha256=historical_chain_sha256,
        historical_hashes=historical_hashes,
        historical_script_authority=historical_script_authority,
        current_script_authority=current_script_authority,
    )


def _validate_r8u_r7_recovery_successor(
    *,
    attempt_root: Path,
    receipt_hashes_by_batch: Mapping[str, str],
    authority: R8UR7ImplementationAuthority,
    expected_runtime_authority: Mapping[str, Any],
    values: Mapping[str, Mapping[str, Any]],
    observed: Mapping[str, str],
    candidate_total_bytes: int,
    historical_chain_sha256: str,
    historical_hashes: Mapping[str, str],
    historical_script_authority: Mapping[str, str],
    current_script_authority: Mapping[str, str],
) -> str:
    """Close R6 publication reuse, R7 finalization, and Tasks 17--19."""

    r6_account = values["r8u_r6_scheduler_account_authority_sha256"]
    r6_resume_authority = values["resume_authority_sha256"]
    r6_submission = values["resume_submission_receipt_sha256"]
    r6_diagnostic = values["gpu_worker_context_diagnostic_sha256"]
    r6_claim = values["publication_claim_sha256"]
    r6_primitive = values["publication_primitive_probe_sha256"]
    r6_locality = values["final_publication_locality_sha256"]
    r6_publication = values["publication_receipt_sha256"]
    r6_job_id = str(r6_submission.get("resume_job_id", ""))
    r6_job_name = "lvef_c3_r8u_r6_res_17b14739"
    prefix15 = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(15)
    ]
    r6_links = {
        "scheduler_account_authority_sha256": (
            "r8u_r6_scheduler_account_authority_sha256"
        ),
        "r8u_r5_failure_evidence_sha256": "r8u_r5_failure_evidence_sha256",
        "locality_sequence_probe_receipt_sha256": (
            "locality_sequence_probe_receipt_sha256"
        ),
        "locality_sequence_probe_accounting_sha256": (
            "locality_sequence_probe_accounting_sha256"
        ),
        "portable_candidate_authority_sha256": (
            "portable_candidate_authority_sha256"
        ),
        "resume_capacity_sha256": "resume_capacity_receipt_sha256",
        "resume_authority_sha256": "resume_authority_sha256",
        "resume_submission_receipt_sha256": (
            "resume_submission_receipt_sha256"
        ),
        "worker_context_diagnostic_sha256": (
            "gpu_worker_context_diagnostic_sha256"
        ),
        "publication_claim_sha256": "publication_claim_sha256",
        "publication_primitive_probe_sha256": (
            "publication_primitive_probe_sha256"
        ),
        "final_publication_locality_sha256": (
            "final_publication_locality_sha256"
        ),
    }
    if (
        r6_resume_authority.get("prefix_final_receipt_sha256") != prefix15
        or r6_resume_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or r6_resume_authority.get("qsub_environment_sha256")
        != r6_account.get("qsub_environment_sha256")
        or r6_resume_authority.get("script_authority")
        != dict(sorted(historical_script_authority.items()))
        or r6_resume_authority.get("worker_role")
        != "R8U_R6_BATCH16_PUBLICATION_RESUME"
        or not _r8u_r6_exact_values(
            r6_resume_authority,
            {
                "cloud_requests_authorized": 0,
                "downloads_authorized": 0,
                "dicom_body_reads_authorized": 0,
                "dicom_extraction_executions_authorized": 0,
                "echoprime_executions_authorized": 1,
                "gpu_executions_authorized": 1,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
                "maximum_new_gpu_resume_qsubs": 1,
            },
        )
        or r6_submission.get("resume_job_name") != r6_job_name
        or r6_submission.get("resume_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {
                "argv": _r8u_r6_expected_resume_qsub_command(
                    attempt_root=attempt_root,
                    implementation_commit=(
                        R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                )
            }
        )
        or r6_submission.get("qsub_environment_sha256")
        != r6_account.get("qsub_environment_sha256")
        or r6_submission.get("scheduler_submission_count") != 1
        or r6_submission.get("resume_is_array") is not False
        or r6_submission.get("gpu_requested") is not True
        or r6_submission.get("automatic_retry_authorized") is not False
        or not _r8u_exact_zero(
            r6_submission,
            "cloud_requests", "downloads", "dicom_body_reads_by_submitter",
            "npz_body_reads_by_submitter",
            "dicom_extraction_executions_by_submitter", "model_fitting_count",
            "prediction_generation_count", "confirmatory_performance_access_count",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_PUBLICATION_AUTHORITY_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            r6_submission.get("resume_qsub_evidence"),
            accepted_stdout=[f"{r6_job_id}\n".encode(), r6_job_id.encode()],
        )
        _validate_r8u_r6_qstat_projection(
            r6_submission.get("initial_qstat_projection"),
            job_id=r6_job_id,
            job_name=r6_job_name,
            expected_status=(
                "PASS_EXACT_ONE_R8U_R6_SUBMITTED_JOB_ZERO_COMPETITORS"
            ),
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_PUBLICATION_AUTHORITY_INVALID"
        ) from exc
    _r8u_r7_validate_worker_diagnostic(r6_diagnostic)
    primitive_result = r6_primitive.get("primary_result")
    primitive_supported = primitive_result == "RENAME_NOREPLACE_SUPPORTED"
    if (
        r6_claim.get("resume_job_id") != r6_job_id
        or r6_claim.get("target_absent") is not True
        or r6_claim.get("competing_active_jobs") != 0
        or r6_claim.get("competing_active_processes") != 0
        or not _r8u_exact_zero(
            r6_claim, "cloud_requests", "downloads", "dicom_body_reads",
            "dicom_extraction_executions", "npz_body_reads",
        )
        or r6_primitive.get("primary_primitive")
        != "RENAMEAT2_RENAME_NOREPLACE"
        or primitive_result
        not in {"RENAME_NOREPLACE_SUPPORTED", "RENAME_NOREPLACE_UNSUPPORTED_EINVAL"}
        or r6_primitive.get("probe_cleanup_passed") is not True
        or r6_primitive.get("probe_directories_created") != 2
        or r6_primitive.get("probe_directories_removed") != 2
        or not _r8u_exact_zero(
            r6_primitive, "scientific_file_body_reads", "npz_body_reads",
            "dicom_body_reads", "dicom_extraction_executions",
        )
        or any(
            r6_locality.get(field) is not True
            for field in (
                "source_exists_safe_directory", "target_absent",
                "source_target_same_mounted_filesystem", "parents_nonsymlinked",
                "owner_mode_valid", "source_stable_identity_equal",
                "source_parent_stable_identity_equal",
                "target_parent_stable_identity_equal",
                "captured_after_primitive_probe", "probe_cleanup_validated",
            )
        )
        or r6_locality.get("competing_active_jobs") != 0
        or r6_locality.get("competing_active_processes") != 0
        or any(
            r6_publication.get(link_key) != observed[observed_key]
            for link_key, observed_key in r6_links.items()
            if link_key in r6_publication
        )
        or r6_publication.get("primary_result") != primitive_result
        or r6_publication.get("fallback_used") is not (not primitive_supported)
        or r6_publication.get("publication_ruling") != "PUBLICATION_PASS"
        or r6_publication.get("source_absent") is not True
        or r6_publication.get("target_exact") is not True
        or r6_publication.get("candidate_npz_files") != 10_187
        or r6_publication.get("candidate_total_bytes") != candidate_total_bytes
        or r6_publication.get("files_moved") != 10_187
        or r6_publication.get("files_copied") != 0
        or r6_publication.get("files_deleted_independently") != 0
        or r6_publication.get("publication_attempts") != 1
        or not _r8u_exact_zero(
            r6_publication, "dicom_body_reads", "dicom_extraction_executions",
            "npz_body_reads", "cloud_requests", "downloads",
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_R6_PUBLICATION_AUTHORITY_INVALID"
        )

    r7_account = values["scheduler_account_authority_sha256"]
    r7_capacity = values["preservation_recovery_capacity_sha256"]
    r7_claim = values["preservation_recovery_claim_sha256"]
    r7_authority = values["preservation_recovery_authority_sha256"]
    r7_submission = values["preservation_recovery_submission_receipt_sha256"]
    r7_diagnostic = values["preservation_worker_context_diagnostic_sha256"]
    r7_accounting = values["preservation_recovery_accounting_sha256"]
    r7_terminal = values["preservation_recovery_terminal_receipt_sha256"]
    continuation_claim = values["continuation_claim_sha256"]
    continuation_submission = values["continuation_submission_receipt_sha256"]
    recovery_job_id = str(r7_submission.get("recovery_job_id", ""))
    recovery_job_name = f"lvef_c3_r8u_r7_rec_{authority.implementation_commit[:8]}"
    if (
        type(r7_capacity.get("required_control_bytes")) is not int
        or r7_capacity.get("required_control_bytes") != 64 * 1024 * 1024
        or type(r7_capacity.get("available_bytes")) is not int
        or r7_capacity.get("available_bytes", -1)
        < r7_capacity.get("required_control_bytes", 0)
        or type(r7_capacity.get("required_control_file_slots")) is not int
        or r7_capacity.get("required_control_file_slots") != 128
        or type(r7_capacity.get("available_file_slots")) is not int
        or r7_capacity.get("available_file_slots", -1)
        < r7_capacity.get("required_control_file_slots", 0)
        or any(
            r7_capacity.get(field) is not True
            for field in (
                "byte_envelope_passed", "file_slot_envelope_passed",
                "storage_neutral_or_reducing",
            )
        )
        or any(
            r7_capacity.get(field) is not False
            for field in (
                "full_run_reserve_charged", "raw_data_charged",
                "extraction_charged", "publication_charged",
                "echoprime_charged", "embedding_generation_charged",
                "prefix_batches_charged", "continuation_charged",
            )
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CAPACITY_AUTHORITY_INVALID"
        )

    expected_scientific_hashes = {
        key: values["r8u_r6_failure_evidence_sha256"].get(key)
        for key in (
            "failed_partial_seal_sha256", "publication_receipt_sha256",
            "extraction_ledger_sha256", "pooling_ledger_sha256",
            "clip_embeddings_sha256", "clip_manifest_sha256",
            "study_embeddings_sha256", "study_manifest_sha256",
            "embedding_summary_sha256",
        )
    }
    if (
        r7_authority.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or r7_authority.get("r8u_r6_failure_evidence_sha256")
        != observed["r8u_r6_failure_evidence_sha256"]
        or r7_authority.get("preservation_recovery_capacity_sha256")
        != observed["preservation_recovery_capacity_sha256"]
        or r7_authority.get("prefix_final_receipt_sha256") != prefix15
        or r7_authority.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or r7_authority.get("qsub_environment_sha256")
        != r7_account.get("qsub_environment_sha256")
        or r7_authority.get("script_authority")
        != dict(sorted(current_script_authority.items()))
        or any(
            r7_authority.get(key) != expected
            for key, expected in expected_scientific_hashes.items()
        )
        or r7_authority.get("worker_role")
        != "R8U_R7_BATCH16_PRESERVATION_RECOVERY"
        or not _r8u_r6_exact_values(
            r7_authority,
            {
                "original_task_id": 16,
                "human_batch_number": 16,
                "published_npz_files": 10_187,
                "cloud_requests_authorized": 0,
                "downloads_authorized": 0,
                "dicom_body_reads_authorized": 0,
                "dicom_extraction_executions_authorized": 0,
                "publication_executions_authorized": 0,
                "echoprime_executions_authorized": 0,
                "embedding_generations_authorized": 0,
                "gpu_executions_authorized": 0,
                "preservation_executions_authorized": 1,
                "cache_retirement_executions_authorized": 1,
                "batch_finalization_executions_authorized": 1,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
                "maximum_new_recovery_qsubs": 1,
            },
        )
        or r7_claim.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or r7_claim.get("r8u_r6_failure_evidence_sha256")
        != observed["r8u_r6_failure_evidence_sha256"]
        or r7_claim.get("preservation_recovery_capacity_sha256")
        != observed["preservation_recovery_capacity_sha256"]
        or r7_claim.get("preservation_recovery_authority_sha256")
        != observed["preservation_recovery_authority_sha256"]
        or r7_claim.get("qsub_environment_sha256")
        != r7_account.get("qsub_environment_sha256")
        or r7_claim.get("target_role")
        != "BATCH16_PRESERVATION_RETIREMENT_FINALIZATION"
        or r7_claim.get("competing_active_jobs") != 0
        or r7_claim.get("competing_active_processes") != 0
        or r7_claim.get("preservation_receipt_absent") is not True
        or r7_claim.get("retirement_authorization_absent") is not True
        or r7_claim.get("final_receipt_absent") is not True
        or not _r8u_valid_hashes(
            r7_claim, "qstat_projection_sha256", "process_projection_sha256"
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_AUTHORITY_INVALID"
        )

    if (
        r7_submission.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or r7_submission.get("r8u_r6_failure_evidence_sha256")
        != observed["r8u_r6_failure_evidence_sha256"]
        or r7_submission.get("preservation_recovery_capacity_sha256")
        != observed["preservation_recovery_capacity_sha256"]
        or r7_submission.get("preservation_recovery_authority_sha256")
        != observed["preservation_recovery_authority_sha256"]
        or r7_submission.get("preservation_recovery_claim_sha256")
        != observed["preservation_recovery_claim_sha256"]
        or r7_submission.get("recovery_job_name") != recovery_job_name
        or re.fullmatch(r"[1-9][0-9]{0,19}", recovery_job_id) is None
        or r7_submission.get("recovery_qsub_argv_sha256")
        != _r8r_controller_json_sha256(
            {"argv": _r8u_r7_expected_recovery_qsub_command(
                attempt_root=attempt_root,
                implementation_commit=authority.implementation_commit,
            )}
        )
        or r7_submission.get("qsub_environment_sha256")
        != r7_account.get("qsub_environment_sha256")
        or not _r8u_r6_exact_values(
            r7_submission,
            {
                "scheduler_submission_count": 1,
                "cpu_slots": 4,
                "wall_seconds_maximum": 7200,
                "recovery_is_array": False,
                "gpu_requested": False,
                "automatic_retry_authorized": False,
                "cloud_requests": 0,
                "downloads": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "dicom_extraction_executions_by_submitter": 0,
                "echoprime_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            r7_submission.get("recovery_qsub_evidence"),
            accepted_stdout=[
                f"{recovery_job_id}\n".encode(), recovery_job_id.encode()
            ],
        )
        _validate_r8u_r6_qstat_projection(
            r7_submission.get("initial_qstat_projection"),
            job_id=recovery_job_id,
            job_name=recovery_job_name,
            expected_status=(
                "PASS_EXACT_ONE_R8U_R7_SUBMITTED_JOB_ZERO_COMPETITORS"
            ),
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_SUBMISSION_INVALID"
        ) from exc
    _r8u_r7_validate_worker_diagnostic(r7_diagnostic)

    try:
        _validate_r8r_recovery_accounting(
            r7_accounting.get("accounting_projection"),
            expected_job_id=recovery_job_id,
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        ) from exc
    if (
        r7_accounting.get("scheduler_account_authority_sha256")
        != observed["scheduler_account_authority_sha256"]
        or r7_accounting.get("preservation_recovery_submission_receipt_sha256")
        != observed["preservation_recovery_submission_receipt_sha256"]
        or r7_accounting.get("preservation_recovery_terminal_receipt_sha256")
        != observed["preservation_recovery_terminal_receipt_sha256"]
        or r7_accounting.get("recovery_job_id") != recovery_job_id
        or not _r8u_r6_exact_values(
            r7_accounting, {"failed": 0, "exit_status": 0}
        )
        or not _r8u_valid_hashes(r7_accounting, "scheduler_log_sha256")
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        )
    recovery_log_path = (
        attempt_root / "r8u_r7_batch16_preservation_recovery/scheduler"
        / f"{recovery_job_name}.o{recovery_job_id}"
    )
    recovery_log, _recovery_log_mode, recovery_log_sha256 = (
        _r8u_r7_scheduler_log_authority(
            recovery_log_path,
            code="R8U_R7_FINALIZER_RECOVERY_ACCOUNTING_INVALID",
        )
    )
    recovery_terminal_marker = (
        "R8U_R7_STATUS="
        "PASS_R8U_R7_BATCH16_PRESERVATION_RECOVERY_FINALIZED\n"
    ).encode("ascii")
    if (
        r7_accounting.get("scheduler_log_sha256") != recovery_log_sha256
        or recovery_log.count(recovery_terminal_marker) != 1
        or b"BLOCKED_" in recovery_log
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_RECOVERY_ACCOUNTING_INVALID"
        )

    batch16_receipt_path = (
        attempt_root / "batches/c3_batch_015/preservation/"
        "batch_finalization_receipt.restricted.json"
    )
    batch16_receipt = load_json(batch16_receipt_path, "R8U_R7_BATCH16_RECEIPT")
    terminal_artifact_paths = {
        "preservation_receipt_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": (
            attempt_root / "cache_retirement_authorizations/"
            "c3_batch_015.authorization.json"
        ),
        "cache_retirement_transition_sha256": (
            attempt_root / "batches/c3_batch_015/preservation/"
            "cache_retirement_finalized.restricted.json"
        ),
        "final_ledger_sha256": (
            attempt_root / "batches/c3_batch_015/final_resume_ledger.restricted.json"
        ),
        "batch_finalization_receipt_sha256": batch16_receipt_path,
    }
    terminal_control_links = {
        "scheduler_account_authority_sha256": "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256": "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256": (
            "preservation_recovery_capacity_sha256"
        ),
        "preservation_recovery_authority_sha256": (
            "preservation_recovery_authority_sha256"
        ),
        "preservation_recovery_claim_sha256": (
            "preservation_recovery_claim_sha256"
        ),
        "preservation_recovery_submission_receipt_sha256": (
            "preservation_recovery_submission_receipt_sha256"
        ),
        "preservation_worker_context_diagnostic_sha256": (
            "preservation_worker_context_diagnostic_sha256"
        ),
    }
    if (
        any(
            r7_terminal.get(key) != observed[observed_key]
            for key, observed_key in terminal_control_links.items()
        )
        or any(
            r7_terminal.get(key) != sha256_file(path)
            for key, path in terminal_artifact_paths.items()
        )
        or not _r8u_r6_exact_values(
            r7_terminal,
            {
                "npz_files_expected": 10_187,
                "npz_files_observed": 10_187,
                "npz_files_missing": 0,
                "npz_files_additional": 0,
                "npz_stable_metadata_differences": 0,
                "extracted_npz_body_reads": 0,
                "n_selected_studies": 250,
                "n_successfully_extracted_cines": 10_187,
                "n_clip_embeddings": 10_187,
                "n_pooled_studies": 250,
                "n_object_technical_dispositions": 0,
                "n_blocking_failures": 0,
                "preservation_status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
                "cache_retirement_status": "PASS_RETIRED",
                "final_ledger_status": "FINALIZED",
                "batch_finalization_status": "PASS_BATCH_FINALIZED",
                "raw_dicoms_retained": True,
                "failed_partial_cache_retained": True,
                "publication_reused": True,
                "echoprime_reused": True,
                "cloud_requests": 0,
                "downloads": 0,
                "dicom_body_reads": 0,
                "dicom_extraction_executions": 0,
                "publication_executions": 0,
                "echoprime_executions": 0,
                "embedding_generations": 0,
                "gpu_executions": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
        or type(r7_terminal.get("npz_atime_only_differences")) is not int
        or r7_terminal.get("npz_atime_only_differences", -1) < 0
        or r7_terminal.get("batch_finalization_receipt_sha256")
        != receipt_hashes_by_batch["c3_batch_015"]
        or batch16_receipt.get("preservation_status")
        != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or batch16_receipt.get("raw_dicoms_retained") is not True
        or batch16_receipt.get("extracted_cache_retired") is not True
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_TERMINAL_RECEIPT_INVALID"
        )

    continuation_links = {
        "scheduler_account_authority_sha256": "scheduler_account_authority_sha256",
        "r8u_r6_failure_evidence_sha256": "r8u_r6_failure_evidence_sha256",
        "preservation_recovery_capacity_sha256": (
            "preservation_recovery_capacity_sha256"
        ),
        "preservation_recovery_authority_sha256": (
            "preservation_recovery_authority_sha256"
        ),
        "preservation_recovery_submission_receipt_sha256": (
            "preservation_recovery_submission_receipt_sha256"
        ),
        "preservation_worker_context_diagnostic_sha256": (
            "preservation_worker_context_diagnostic_sha256"
        ),
        "preservation_recovery_accounting_sha256": (
            "preservation_recovery_accounting_sha256"
        ),
        "preservation_recovery_terminal_receipt_sha256": (
            "preservation_recovery_terminal_receipt_sha256"
        ),
    }
    prefix16 = [
        receipt_hashes_by_batch[f"c3_batch_{index:03d}"]
        for index in range(16)
    ]
    if (
        continuation_claim.get("prefix_final_receipt_sha256") != prefix16
        or continuation_claim.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_claim.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_claim.get("runtime_authority_sha256")
        != core.canonical_json_sha256(expected_runtime_authority)
        or continuation_claim.get("qsub_environment_sha256")
        != r7_account.get("qsub_environment_sha256")
        or continuation_claim.get("script_authority")
        != dict(sorted(current_script_authority.items()))
        or continuation_claim.get("continuation_task_range") != "17-19"
        or not _r8u_r6_exact_values(
            continuation_claim,
            {
                "continuation_task_count": 3,
                "continuation_max_concurrency": 1,
                "held_finalizer_count": 1,
                "total_new_qsub_maximum": 3,
                "automatic_retry_authorized": False,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests_by_submitter": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "embedding_generations_by_submitter": 0,
                "model_fitting_authorized": False,
                "prediction_authorized": False,
                "confirmatory_performance_access_authorized": False,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CONTINUATION_CLAIM_INVALID"
        )

    array_job_id = str(continuation_submission.get("array_job_id", ""))
    finalizer_job_id = str(continuation_submission.get("finalizer_job_id", ""))
    qsub = "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub"
    runner = str(Path(__file__).resolve().parent / R8R_SCHEDULER_RUNNER_BASENAME)
    scheduler_root = attempt_root / "r8u_r7_continuation_17_19/scheduler"
    common_qsub = [qsub, "-clear", "-terse", "-r", "n", "-P", "mimicecho"]
    array_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r7_seq_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-t", "17-19", "-tc", "1",
        "-l", "h_rt=48:00:00", "-l", "gpus=1", "-l", "gpu_c=8.0",
        "-l", "gpu_memory=48G", "-pe", "omp", "4",
        "-l", "mem_per_core=16G", runner,
    ]
    finalizer_command = [
        *common_qsub,
        "-N", f"lvef_c3_r8u_r7_fin_{authority.implementation_commit[:8]}",
        "-j", "y", "-o", str(scheduler_root), "-hold_jid", array_job_id,
        "-l", "h_rt=12:00:00", "-pe", "omp", "4",
        "-l", "mem_per_core=8G", runner,
    ]
    if (
        continuation_submission.get("recovery_job_id") != recovery_job_id
        or continuation_submission.get("array_job_name")
        != f"lvef_c3_r8u_r7_seq_{authority.implementation_commit[:8]}"
        or continuation_submission.get("finalizer_job_name")
        != f"lvef_c3_r8u_r7_fin_{authority.implementation_commit[:8]}"
        or re.fullmatch(r"[1-9][0-9]{0,19}", array_job_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", finalizer_job_id) is None
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or continuation_submission.get("array_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": array_command})
        or continuation_submission.get("finalizer_qsub_argv_sha256")
        != _r8r_controller_json_sha256({"argv": finalizer_command})
        or continuation_submission.get("qsub_environment_sha256")
        != r7_account.get("qsub_environment_sha256")
        or continuation_submission.get("failed_partial_seal_sha256")
        != observed["failed_partial_seal_sha256"]
        or any(
            continuation_submission.get(link_key) != observed[observed_key]
            for link_key, observed_key in continuation_links.items()
        )
        or continuation_submission.get("continuation_claim_sha256")
        != observed["continuation_claim_sha256"]
        or not _r8u_r6_exact_values(
            continuation_submission,
            {
                "scheduler_submission_count": 2,
                "total_new_qsub_submissions": 3,
                "scheduler_submission_maximum": 3,
                "array_task_range": "17-19",
                "array_task_count": 3,
                "array_max_concurrency": 1,
                "finalizer_held_on_array": True,
                "whole_stage_retry_authorized": False,
                "fourth_submission_reachable": False,
                "cloud_requests": 0,
                "dicom_body_reads_by_submitter": 0,
                "npz_body_reads_by_submitter": 0,
                "gpu_executions_by_submitter": 0,
                "model_fitting_count": 0,
                "prediction_generation_count": 0,
                "confirmatory_performance_access_count": 0,
            },
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        )
    try:
        _validate_r8r_qsub_evidence(
            continuation_submission.get("array_qsub_evidence"),
            accepted_stdout=[
                f"{array_job_id}.17-19:1\n".encode(),
                f"{array_job_id}.17-19:1".encode(),
                f"{array_job_id}\n".encode(), array_job_id.encode(),
            ],
        )
        _validate_r8r_qsub_evidence(
            continuation_submission.get("finalizer_qsub_evidence"),
            accepted_stdout=[
                f"{finalizer_job_id}\n".encode(), finalizer_job_id.encode(),
            ],
        )
    except ProductionFinalizationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_CONTINUATION_SUBMISSION_INVALID"
        ) from exc

    return core.canonical_json_sha256(
        {
            "historical_r8r_chain_authority_sha256": historical_chain_sha256,
            "implementation_commit": authority.implementation_commit,
            **{
                f"historical_r8r_{key}": value
                for key, value in sorted(historical_hashes.items())
            },
            **dict(sorted(observed.items())),
        }
    )


def _validate_r8u_r3_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR3ImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U-R3 2+13+4 split."""

    if type(authority) is not R8UR3ImplementationAuthority:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_AUTHORITY_INVALID"
        )
    authority_hashes = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field != "implementation_commit"
    )
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit
        in {
            R8R_SCIENTIFIC_GOVERNING_COMMIT,
            R8U_PRIOR_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        }
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_AUTHORITY_INVALID"
        )
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_r3_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_r3_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R3_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r3_repository_authority(authority.implementation_commit)
    return _validate_r8u_r3_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r4_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR4ImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U-R4 2+13+4 split."""

    if type(authority) is not R8UR4ImplementationAuthority:
        raise ProductionFinalizationError("R8U_R4_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field != "implementation_commit"
    )
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit
        in {
            R8R_SCIENTIFIC_GOVERNING_COMMIT,
            R8U_PRIOR_IMPLEMENTATION_COMMIT,
            R8U_BASE_IMPLEMENTATION_COMMIT,
            R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        }
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
        or authority.r3_extraction_candidate_seal_sha256
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError("R8U_R4_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_r4_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_r4_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R4_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r4_repository_authority(authority.implementation_commit)
    return _validate_r8u_r4_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r5_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR5ImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U-R5 2+13+4 split."""

    if type(authority) is not R8UR5ImplementationAuthority:
        raise ProductionFinalizationError("R8U_R5_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field != "implementation_commit"
    )
    fixed_commits = {
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
    }
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit in fixed_commits
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
        or authority.r3_extraction_candidate_seal_sha256
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError("R8U_R5_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_r5_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_r5_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        ) != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R5_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r5_repository_authority(authority.implementation_commit)
    return _validate_r8u_r5_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r6_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR6ImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U-R6 2+13+4 split."""

    if type(authority) is not R8UR6ImplementationAuthority:
        raise ProductionFinalizationError("R8U_R6_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field != "implementation_commit"
    )
    fixed_commits = {
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT,
    }
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit in fixed_commits
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
        or authority.r3_extraction_candidate_seal_sha256
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError("R8U_R6_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_r6_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_r6_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R6_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r6_repository_authority(authority.implementation_commit)
    return _validate_r8u_r6_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r7_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR7ImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only the fixed original/R8R/R8U-R7 2+13+4 split."""

    if type(authority) is not R8UR7ImplementationAuthority:
        raise ProductionFinalizationError("R8U_R7_FINALIZER_AUTHORITY_INVALID")
    authority_hashes = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field != "implementation_commit"
    )
    fixed_commits = {
        R8R_SCIENTIFIC_GOVERNING_COMMIT,
        R8U_PRIOR_IMPLEMENTATION_COMMIT,
        R8U_BASE_IMPLEMENTATION_COMMIT,
        R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT,
        R8U_R6_LOCALITY_ORDERING_REPAIR_IMPLEMENTATION_COMMIT,
    }
    if (
        not isinstance(authority.implementation_commit, str)
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit in fixed_commits
        or (
            authority.historical_r8r_recovery_authority_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_authority_sha256"
            ]
        )
        or (
            authority.historical_r8r_recovery_terminal_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "recovery_terminal_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_capacity_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_capacity_receipt_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_claim_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_claim_sha256"
            ]
        )
        or (
            authority.historical_r8r_continuation_submission_receipt_sha256
            != R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                "continuation_submission_receipt_sha256"
            ]
        )
        or any(
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
            for value in authority_hashes
        )
        or len(set(authority_hashes)) != len(authority_hashes)
        or authority.r3_extraction_candidate_seal_sha256
        != R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    ):
        raise ProductionFinalizationError("R8U_R7_FINALIZER_AUTHORITY_INVALID")
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    for batch_id, (expected_bytes, expected_sha256) in (
        R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        if (
            receipt_sizes_by_batch.get(batch_id) != expected_bytes
            or receipt_hashes_by_batch.get(batch_id) != expected_sha256
        ):
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_PREFIX_RECEIPT_MISMATCH"
            )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    current_r8u_r7_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or current_r8u_r7_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 3
    ):
        raise ProductionFinalizationError(
            "R8U_R7_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r7_repository_authority(authority.implementation_commit)
    return _validate_r8u_r7_chain_artifacts(
        receipt_paths_by_batch=receipt_paths_by_batch,
        receipt_hashes_by_batch=receipt_hashes_by_batch,
        authority=authority,
        expected_runtime_authority=runtime,
        plan=plan,
    )


def _validate_r8u_r7d_mixed_implementation_epochs(
    receipts: Sequence[Mapping[str, Any]],
    *,
    receipt_hashes_by_batch: Mapping[str, str],
    receipt_sizes_by_batch: Mapping[str, int],
    receipt_paths_by_batch: Mapping[str, Path],
    expected_governing_commit: str,
    expected_attempt_id: str | None,
    expected_runtime_authority: Mapping[str, Any] | None,
    authority: R8UR7DImplementationAuthority,
    plan: Mapping[str, Any] | None,
) -> str:
    """Accept only immutable Batches 1--16 plus fresh R7D Batches 17--19."""

    del receipt_sizes_by_batch, receipt_paths_by_batch
    if type(authority) is not R8UR7DImplementationAuthority:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_AUTHORITY_INVALID"
        )
    hash_fields = tuple(
        getattr(authority, field)
        for field in authority.__dataclass_fields__
        if field
        not in {
            "implementation_commit",
            "prior_r7_runtime_commit",
            "r7c_adjudication_commit",
            "finalized_prefix_receipt_sha256",
        }
    )
    if (
        type(authority.implementation_commit) is not str
        or COMMIT_RE.fullmatch(authority.implementation_commit) is None
        or authority.implementation_commit
        in {
            R8R_SCIENTIFIC_GOVERNING_COMMIT,
            R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT,
            R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT,
        }
        or authority.prior_r7_runtime_commit
        != R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT
        or authority.r7c_adjudication_commit
        != R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT
        or type(authority.finalized_prefix_receipt_sha256) is not tuple
        or authority.finalized_prefix_receipt_sha256
        != R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256
        or authority.consumed_r7a_continuation_receipt_sha256
        != R8U_R7D_CONSUMED_CONTINUATION_RECEIPT_SHA256
        or authority.consumed_task17_accounting_receipt_sha256
        != R8U_R7D_CONSUMED_TASK17_ACCOUNTING_RECEIPT_SHA256
        or authority.consumed_task18_accounting_receipt_sha256
        != R8U_R7D_CONSUMED_TASK18_ACCOUNTING_RECEIPT_SHA256
        or authority.consumed_task19_accounting_receipt_sha256
        != R8U_R7D_CONSUMED_TASK19_ACCOUNTING_RECEIPT_SHA256
        or authority.consumed_finalizer_accounting_receipt_sha256
        != R8U_R7D_CONSUMED_FINALIZER_ACCOUNTING_RECEIPT_SHA256
        or any(
            type(value) is not str or SHA256_RE.fullmatch(value) is None
            for value in hash_fields
        )
        or len(set(hash_fields)) != len(hash_fields)
        or authority.continuation_submission_receipt_sha256
        == authority.consumed_r7a_continuation_receipt_sha256
    ):
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_AUTHORITY_INVALID"
        )
    if (
        expected_governing_commit != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or expected_attempt_id != R8R_ATTEMPT_ID
        or plan is None
        or len(receipts) != len(EXPECTED_BATCH_IDS)
        or tuple(str(item.get("batch_id")) for item in receipts)
        != EXPECTED_BATCH_IDS
        or any(
            item.get("governing_commit")
            != R8R_SCIENTIFIC_GOVERNING_COMMIT
            or item.get("attempt_id") != R8R_ATTEMPT_ID
            or item.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
            for item in receipts
        )
        or any(
            len({item[key] for item in receipts}) != 1
            for key in R8R_SCIENTIFIC_AUTHORITY_KEYS
        )
    ):
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    if expected_runtime_authority is None:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )
    try:
        runtime = core.validate_runtime_authority(expected_runtime_authority)
    except core.OrchestrationError as exc:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        ) from exc
    first = receipts[0]
    if (
        runtime.get("git_commit") != R8R_SCIENTIFIC_GOVERNING_COMMIT
        or runtime.get("batch_plan_sha256") != R8R_BATCH_PLAN_SHA256
        or runtime.get("orchestration_contract_sha256")
        != first["orchestration_contract_sha256"]
        or runtime.get("checkpoint_sha256") != first["checkpoint_sha256"]
        or runtime.get("environment_receipt_sha256")
        != first["environment_receipt_sha256"]
    ):
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_SCIENTIFIC_AUTHORITY_MISMATCH"
        )

    observed_prefix = tuple(
        receipt_hashes_by_batch.get(f"c3_batch_{index:03d}", "")
        for index in range(16)
    )
    if observed_prefix != R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_PREFIX_RECEIPT_MISMATCH"
        )

    original_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[:2]
    }
    historical_r8r_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[2:15]
    }
    r7_batch16_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[15:16]
    }
    current_r7d_epochs = {
        _receipt_implementation_epoch(item) for item in receipts[16:]
    }
    try:
        expected_current_epoch = _current_r8r_implementation_epoch()
    except (OSError, ProductionFinalizationError) as exc:
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH"
        ) from exc
    if (
        len(original_epochs) != 1
        or historical_r8r_epochs != {R8U_FE3_IMPLEMENTATION_EPOCH}
        or r7_batch16_epochs != {R8U_R7_BATCH16_IMPLEMENTATION_EPOCH}
        or current_r7d_epochs != {expected_current_epoch}
        or len(
            {
                next(iter(original_epochs), ()),
                R8U_FE3_IMPLEMENTATION_EPOCH,
                R8U_R7_BATCH16_IMPLEMENTATION_EPOCH,
                expected_current_epoch,
            }
        )
        != 4
    ):
        raise ProductionFinalizationError(
            "R8U_R7D_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH"
        )
    _validate_r8u_r7d_repository_authority(authority.implementation_commit)
    authority_payload = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r7d_finalizer_authority_v1",
        **{
            field: getattr(authority, field)
            for field in authority.__dataclass_fields__
        },
    }
    return core.canonical_json_sha256(authority_payload)


def _stage_authority_receipts(
    receipts: Sequence[Mapping[str, Any]],
    *,
    r8r_mode: bool,
    r8u_mode: bool,
    r8u_r3_mode: bool = False,
    r8u_r4_mode: bool = False,
    r8u_r5_mode: bool = False,
    r8u_r6_mode: bool = False,
    r8u_r7_mode: bool = False,
    r8u_r7d_mode: bool = False,
) -> Sequence[Mapping[str, Any]]:
    if sum(
        (
            r8r_mode, r8u_mode, r8u_r3_mode, r8u_r4_mode,
            r8u_r5_mode, r8u_r6_mode, r8u_r7_mode, r8u_r7d_mode,
        )
    ) > 1:
        raise ProductionFinalizationError(
            "FINALIZER_IMPLEMENTATION_AUTHORITY_AMBIGUOUS"
        )
    if (
        r8u_mode or r8u_r3_mode or r8u_r4_mode
        or r8u_r5_mode or r8u_r6_mode or r8u_r7_mode
    ):
        return receipts[15:]
    if r8u_r7d_mode:
        return receipts[16:]
    if r8r_mode:
        return receipts[2:]
    return receipts


def finalize_receipts(
    receipt_paths: Sequence[Path], *, expected_governing_commit: str,
    expected_attempt_id: str | None = None, plan: Mapping[str, Any] | None = None,
    requirements: Any | None = None, production_root: Path | None = None,
    contract: Mapping[str, Any] | None = None, contract_path: Path | None = None,
    environment_receipt: Path | None = None,
    cache_retirement_authorization_root: Path | None = None,
    canonical_output_root: Path | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
    expected_no_cine_studies: int | None = None,
    r8r_implementation_authority: R8RImplementationAuthority | None = None,
    r8u_implementation_authority: R8UImplementationAuthority | None = None,
    r8u_r3_implementation_authority: R8UR3ImplementationAuthority | None = None,
    r8u_r4_implementation_authority: R8UR4ImplementationAuthority | None = None,
    r8u_r5_implementation_authority: R8UR5ImplementationAuthority | None = None,
    r8u_r6_implementation_authority: R8UR6ImplementationAuthority | None = None,
    r8u_r7_implementation_authority: R8UR7ImplementationAuthority | None = None,
    r8u_r7d_implementation_authority: R8UR7DImplementationAuthority | None = None,
) -> dict[str, Any]:
    authority_mode_count = sum(
        value is not None
        for value in (
            r8r_implementation_authority,
            r8u_implementation_authority,
            r8u_r3_implementation_authority,
            r8u_r4_implementation_authority,
            r8u_r5_implementation_authority,
            r8u_r6_implementation_authority,
            r8u_r7_implementation_authority,
            r8u_r7d_implementation_authority,
        )
    )
    if authority_mode_count > 1:
        raise ProductionFinalizationError(
            "FINALIZER_IMPLEMENTATION_AUTHORITY_AMBIGUOUS"
        )
    mixed_implementation_authority = (
        authority_mode_count == 1
    )
    expected_batch_ids = (
        tuple(f"c3_batch_{index:03d}" for index in range(requirements.batch_count))
        if requirements is not None
        else EXPECTED_BATCH_IDS
    )
    if len(receipt_paths) != len(expected_batch_ids):
        raise ProductionFinalizationError("FINAL_BATCH_SET_INCOMPLETE")
    receipts: list[dict[str, Any]] = []
    receipt_hashes: list[str] = []
    receipt_hashes_by_batch: dict[str, str] = {}
    receipt_sizes_by_batch: dict[str, int] = {}
    receipt_paths_by_batch: dict[str, Path] = {}
    r8r_chain_authority_sha256: str | None = None
    r8u_chain_authority_sha256: str | None = None
    r8u_r3_chain_authority_sha256: str | None = None
    r8u_r4_chain_authority_sha256: str | None = None
    r8u_r5_chain_authority_sha256: str | None = None
    r8u_r6_chain_authority_sha256: str | None = None
    r8u_r7_chain_authority_sha256: str | None = None
    r8u_r7d_chain_authority_sha256: str | None = None
    for path in receipt_paths:
        receipt = load_json(path, "BATCH_RECEIPT")
        _validate_current_receipt_v3(receipt)
        receipt_hash = sha256_file(path)
        receipts.append(receipt)
        receipt_hashes.append(receipt_hash)
        if receipt["batch_id"] in receipt_paths_by_batch:
            raise ProductionFinalizationError("DUPLICATE_BATCH_RECEIPT")
        receipt_paths_by_batch[receipt["batch_id"]] = path
        if mixed_implementation_authority:
            receipt_hashes_by_batch[receipt["batch_id"]] = receipt_hash
            receipt_sizes_by_batch[receipt["batch_id"]] = path.stat(
                follow_symlinks=False
            ).st_size
    receipts.sort(key=lambda item: str(item["batch_id"]))
    if tuple(item["batch_id"] for item in receipts) != expected_batch_ids:
        raise ProductionFinalizationError("FINAL_BATCH_SET_MISMATCH")
    if any(item["governing_commit"] != expected_governing_commit for item in receipts):
        raise ProductionFinalizationError("GOVERNING_COMMIT_MISMATCH")
    attempt_ids = {item["attempt_id"] for item in receipts}
    if len(attempt_ids) != 1 or (expected_attempt_id is not None and attempt_ids != {expected_attempt_id}):
        raise ProductionFinalizationError("CROSS_BATCH_ATTEMPT_MISMATCH")
    if not mixed_implementation_authority:
        if any(
            len({item[key] for item in receipts}) != 1
            for key in CROSS_BATCH_AUTHORITY_KEYS
        ):
            raise ProductionFinalizationError("CROSS_BATCH_AUTHORITY_MISMATCH")
    elif r8r_implementation_authority is not None:
        r8r_chain_authority_sha256 = _validate_r8r_mixed_implementation_epochs(
            receipts,
            receipt_hashes_by_batch=receipt_hashes_by_batch,
            receipt_sizes_by_batch=receipt_sizes_by_batch,
            receipt_paths_by_batch=receipt_paths_by_batch,
            expected_governing_commit=expected_governing_commit,
            expected_attempt_id=expected_attempt_id,
            expected_runtime_authority=expected_runtime_authority,
            authority=r8r_implementation_authority,
        )
    elif r8u_implementation_authority is not None:
        r8u_chain_authority_sha256 = _validate_r8u_mixed_implementation_epochs(
            receipts,
            receipt_hashes_by_batch=receipt_hashes_by_batch,
            receipt_sizes_by_batch=receipt_sizes_by_batch,
            receipt_paths_by_batch=receipt_paths_by_batch,
            expected_governing_commit=expected_governing_commit,
            expected_attempt_id=expected_attempt_id,
            expected_runtime_authority=expected_runtime_authority,
            authority=r8u_implementation_authority,
            plan=plan,
        )
    elif r8u_r3_implementation_authority is not None:
        r8u_r3_chain_authority_sha256 = (
            _validate_r8u_r3_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r3_implementation_authority,
                plan=plan,
            )
        )
    elif r8u_r4_implementation_authority is not None:
        r8u_r4_chain_authority_sha256 = (
            _validate_r8u_r4_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r4_implementation_authority,
                plan=plan,
            )
        )
    elif r8u_r5_implementation_authority is not None:
        r8u_r5_chain_authority_sha256 = (
            _validate_r8u_r5_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r5_implementation_authority,
                plan=plan,
            )
        )
    elif r8u_r6_implementation_authority is not None:
        r8u_r6_chain_authority_sha256 = (
            _validate_r8u_r6_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r6_implementation_authority,
                plan=plan,
            )
        )
    elif r8u_r7_implementation_authority is not None:
        r8u_r7_chain_authority_sha256 = (
            _validate_r8u_r7_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r7_implementation_authority,
                plan=plan,
            )
        )
    else:
        if r8u_r7d_implementation_authority is None:
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_AUTHORITY_INVALID"
            )
        r8u_r7d_chain_authority_sha256 = (
            _validate_r8u_r7d_mixed_implementation_epochs(
                receipts,
                receipt_hashes_by_batch=receipt_hashes_by_batch,
                receipt_sizes_by_batch=receipt_sizes_by_batch,
                receipt_paths_by_batch=receipt_paths_by_batch,
                expected_governing_commit=expected_governing_commit,
                expected_attempt_id=expected_attempt_id,
                expected_runtime_authority=expected_runtime_authority,
                authority=r8u_r7d_implementation_authority,
                plan=plan,
            )
        )
    receipt_set_hash = hashlib.sha256(
        "\n".join(sorted(receipt_hashes)).encode("ascii") + b"\n"
    ).hexdigest()
    canonical_store: tuple[
        list[dict[str, Any]],
        Any,
        list[dict[str, Any]],
        list[dict[str, Any]],
        str,
    ] | None = None
    if plan is not None:
        if (
            requirements is None
            or production_root is None
            or contract is None
            or contract_path is None
            or environment_receipt is None
            or cache_retirement_authorization_root is None
            or canonical_output_root is None
        ):
            raise ProductionFinalizationError("FINALIZER_AUTHORITY_ARGUMENTS_INCOMPLETE")
        if (
            cache_retirement_authorization_root.is_symlink()
            or not cache_retirement_authorization_root.is_dir()
        ):
            raise ProductionFinalizationError("CACHE_AUTHORIZATION_ROOT_INVALID")
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=requirements
        )
        runtime_authority = (
            core.validate_runtime_authority(expected_runtime_authority)
            if expected_runtime_authority is not None
            else core.derive_expected_runtime_authority(
                plan,
                requirements=requirements,
                contract=contract,
                contract_path=contract_path,
                governing_commit=expected_governing_commit,
                environment_receipt_sha256=sha256_file(environment_receipt),
            )
        )
        if any(item["batch_plan_sha256"] != plan_sha for item in receipts):
            raise ProductionFinalizationError("FINALIZER_PLAN_HASH_MISMATCH")
        stage_authority_receipts = _stage_authority_receipts(
            receipts,
            r8r_mode=r8r_implementation_authority is not None,
            r8u_mode=r8u_implementation_authority is not None,
            r8u_r3_mode=r8u_r3_implementation_authority is not None,
            r8u_r4_mode=r8u_r4_implementation_authority is not None,
            r8u_r5_mode=r8u_r5_implementation_authority is not None,
            r8u_r6_mode=r8u_r6_implementation_authority is not None,
            r8u_r7_mode=r8u_r7_implementation_authority is not None,
            r8u_r7d_mode=r8u_r7d_implementation_authority is not None,
        )
        if any(
            item["batch_preservation_script_sha256"]
            != sha256_file(Path(__file__).resolve().parent / "preserve_lvef_c3_production_batch.py")
            or item["cache_retirement_script_sha256"]
            != sha256_file(Path(__file__).resolve().parent / "retire_lvef_c3_extracted_cache_v2.py")
            for item in stage_authority_receipts
        ):
            raise ProductionFinalizationError("FINALIZER_STAGE_SCRIPT_AUTHORITY_MISMATCH")
        planned = {item["batch_id"]: item for item in plan["batches"]}
        attempt_id = next(iter(attempt_ids))
        global_clip_keys: set[str] = set()
        global_physical_source_keys: set[str] = set()
        clip_records_by_batch: dict[str, list[dict[str, Any]]] = {}
        cohort_artifacts: list[dict[str, Any]] = []
        canonical_by_study: dict[str, dict[str, Any]] = {}
        canonical_subjects: set[str] = set()
        for receipt in receipts:
            batch = planned[receipt["batch_id"]]
            if (
                receipt["n_selected_studies"] != batch["n_studies"]
                or receipt["n_selected_subjects"] != batch["n_subjects"]
                or receipt["n_expected_objects"] != batch["n_objects"]
                or receipt["expected_source_bytes"] != batch["source_bytes"]
                or receipt.get("prespecified_no_cine_study_set_sha256")
                != batch["prespecified_no_cine_study_set_sha256"]
                or receipt.get("n_no_cine_studies")
                != batch["expected_no_cine_studies"]
                or receipt.get("all_no_cine_studies_prespecified") is not True
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
                "technical_disposition_manifest_sha256": (
                    production_root / "attempts" / attempt_id / "extracted_cache"
                    / receipt["batch_id"] / "dicom_extraction"
                    / "technical_disposition_manifest.restricted.csv"
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
                observed_hash = (
                    production_stages.technical_disposition_manifest_sha256(path)
                    if key == "technical_disposition_manifest_sha256"
                    else sha256_file(path)
                )
                if observed_hash != receipt[key]:
                    raise ProductionFinalizationError("FINALIZER_REFERENCED_ARTIFACT_HASH_MISMATCH")
            replay_batch_preservation_manifest(
                expected_artifacts["preservation_manifest_sha256"],
                production_root=production_root,
                attempt_id=attempt_id,
                batch_id=receipt["batch_id"],
                expected_retired_cache_tree_sha256=receipt["cache_tree_sha256"],
            )
            clip_records_by_batch[receipt["batch_id"]] = accumulate_global_clip_authority(
                expected_artifacts["clip_manifest_sha256"],
                planned_batch=batch,
                expected_rows=receipt["n_clip_embeddings"],
                global_clip_keys=global_clip_keys,
                global_physical_source_keys=global_physical_source_keys,
                batch_clip_embeddings_sha256=receipt["clip_embeddings_sha256"],
            )
            for role, artifact in (
                ("batch_final_receipt", receipt_paths_by_batch[receipt["batch_id"]]),
                ("batch_clip_embeddings", expected_artifacts["clip_embeddings_sha256"]),
            ):
                cohort_artifacts.append(
                    {
                        "role": role,
                        "relative_path": artifact.relative_to(
                            production_root
                        ).as_posix(),
                        "size_bytes": artifact.stat(follow_symlinks=False).st_size,
                        "sha256": sha256_file(artifact),
                    }
                )
            replayed_studies = replay_batch_study_embeddings(
                clip_manifest_path=expected_artifacts["clip_manifest_sha256"],
                clip_embeddings_path=expected_artifacts["clip_embeddings_sha256"],
                study_manifest_path=expected_artifacts["study_manifest_sha256"],
                study_embeddings_path=expected_artifacts["study_embeddings_sha256"],
                disposition_path=(
                    batch_root / "echoprime" / "study_disposition.restricted.csv"
                ),
                planned_batch=batch,
                expected_clip_embeddings=receipt["n_clip_embeddings"],
                expected_study_embeddings=receipt["n_pooled_studies"],
                expected_no_cine_studies=receipt["n_no_cine_studies"],
            )
            for replayed in replayed_studies:
                study = str(replayed["study_id"])
                subject = str(replayed["subject_id"])
                if study in canonical_by_study or subject in canonical_subjects:
                    raise ProductionFinalizationError(
                        "GLOBAL_STUDY_OR_SUBJECT_COLLISION"
                    )
                canonical_subjects.add(subject)
                canonical_by_study[study] = {
                    **replayed,
                    "batch_id": receipt["batch_id"],
                }
            final_ledger = core.load_strict_json(batch_root / "final_resume_ledger.restricted.json")
            if expected_runtime_authority is None:
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
                expected_authority=runtime_authority,
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
        scoped_no_cine = (
            EXPECTED_NO_CINE_STUDIES
            if expected_no_cine_studies is None
            else expected_no_cine_studies
        )
        if (
            isinstance(scoped_no_cine, bool)
            or not isinstance(scoped_no_cine, int)
            or scoped_no_cine < 0
            or scoped_no_cine >= requirements.selected_studies
        ):
            raise ProductionFinalizationError("EXPECTED_NO_CINE_COUNT_INVALID")
        ordered_records, canonical_array = build_plan_ordered_canonical_study_store(
            plan=plan,
            studies_by_id=canonical_by_study,
            expected_study_count=requirements.selected_studies - scoped_no_cine,
        )
        canonical_clip_index = build_plan_ordered_canonical_clip_index(
            plan=plan,
            records_by_batch=clip_records_by_batch,
            expected_clip_count=sum(item["n_clip_embeddings"] for item in receipts),
        )
        canonical_store = (
            ordered_records, canonical_array, canonical_clip_index,
            cohort_artifacts, plan_sha,
        )

    def total(key: str) -> int:
        return sum(int(item[key]) for item in receipts)

    expected_selected_studies = (
        requirements.selected_studies
        if requirements is not None
        else EXPECTED_SELECTED_STUDIES
    )
    expected_selected_subjects = (
        requirements.selected_subjects
        if requirements is not None
        else EXPECTED_SELECTED_SUBJECTS
    )
    expected_objects = (
        requirements.normalized_source_objects
        if requirements is not None
        else EXPECTED_SOURCE_OBJECTS
    )
    expected_bytes = (
        requirements.selected_source_bytes
        if requirements is not None
        else EXPECTED_SOURCE_BYTES
    )
    expected_no_cine = (
        EXPECTED_NO_CINE_STUDIES
        if expected_no_cine_studies is None
        else expected_no_cine_studies
    )
    if (
        isinstance(expected_no_cine, bool)
        or not isinstance(expected_no_cine, int)
        or expected_no_cine < 0
        or expected_no_cine >= expected_selected_studies
    ):
        raise ProductionFinalizationError("EXPECTED_NO_CINE_COUNT_INVALID")
    expected_totals = {
        "n_selected_studies": expected_selected_studies,
        "n_selected_subjects": expected_selected_subjects,
        "n_expected_objects": expected_objects,
        "expected_source_bytes": expected_bytes,
        "n_download_verified": expected_objects,
        "n_pooled_studies": expected_selected_studies - expected_no_cine,
        "n_no_cine_studies": expected_no_cine,
    }
    for key, expected in expected_totals.items():
        if total(key) != expected:
            raise ProductionFinalizationError("FINAL_COHORT_ACCOUNTING_MISMATCH")
    disposition_total = total("n_object_technical_dispositions")
    disposition_manifest_set_sha256 = hashlib.sha256(
        (
            "\n".join(
                sorted(
                    str(item["technical_disposition_manifest_sha256"])
                    for item in receipts
                )
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()
    result = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_production_finalization_summary_v2",
        "status": "PASS_PRODUCTION_C3_BATCH_RECEIPTS_RECONCILED",
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
        "successfully_extracted_cines": total(
            "n_successfully_extracted_cines"
        ),
        "object_technical_dispositions": disposition_total,
        "blocking_failures": total("n_blocking_failures"),
        "studies_affected_by_technical_disposition": total(
            "n_studies_affected_by_technical_disposition"
        ),
        "new_no_cine_studies": total("n_new_no_cine_studies"),
        "technical_disposition_counts_by_class": {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": (
                disposition_total
            )
        },
        "technical_disposition_policy_version": (
            production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
        "technical_disposition_manifest_set_sha256": (
            disposition_manifest_set_sha256
        ),
        "unique_clip_keys": total("n_unique_clip_keys"),
        "clip_embeddings": total("n_clip_embeddings"),
        "pooled_imaging_eligible_studies": total("n_pooled_studies"),
        "no_cine_studies": total("n_no_cine_studies"),
        "no_cine_disposition": (
            "NONE"
            if total("n_no_cine_studies") == 0
            else "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
        ),
        "outside_selected_studies": total("n_outside_selected_studies"),
        "missing_selected_studies": total("n_missing_selected_studies"),
        "duplicate_physical_sources": total("n_duplicate_physical_sources"),
        "duplicate_clip_keys": total("n_duplicate_clip_keys"),
        "nonfinite_embeddings": total("n_nonfinite_embeddings"),
        "wrong_dimension_embeddings": total("n_wrong_dimension_embeddings"),
        "batch_receipt_set_sha256": receipt_set_hash,
        "all_batches_finalized": True,
        "all_authority_bindings_identical": (
            not mixed_implementation_authority
        ),
        "all_source_receipts_passed": True,
        "all_dicom_audits_passed": True,
        "all_extraction_rows_resolved": True,
        "all_successful_extractions_embedded": True,
        "all_technical_dispositions_retained": True,
        "all_no_cine_studies_prespecified": True,
        "object_substitution_count": total("object_substitution_count"),
        "unaccounted_multiframe_objects": total(
            "unaccounted_multiframe_objects"
        ),
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
        "model_fitting_count": 0,
        "endpoint_prediction_count": 0,
        "confirmatory_performance_access_count": 0,
        "canonical_clip_index_sha256": None,
        "canonical_clip_index_size_bytes": 0,
        "canonical_clip_index_rows": 0,
        "canonical_study_embeddings_sha256": None,
        "canonical_study_embeddings_size_bytes": 0,
        "canonical_study_manifest_sha256": None,
        "canonical_study_manifest_size_bytes": 0,
        "canonical_study_store_receipt_sha256": None,
        "canonical_study_store_receipt_size_bytes": 0,
        "cohort_preservation_receipt_sha256": None,
        "cohort_preservation_receipt_size_bytes": 0,
        "cohort_preserved_artifacts": 0,
        "cohort_preservation_second_pass_replay_passed": False,
        "cohort_preservation_passed": False,
    }
    if r8r_implementation_authority is not None:
        if r8r_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8R_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 2,
                "r8r_implementation_commit": (
                    r8r_implementation_authority.implementation_commit
                ),
                "r8r_recovery_continuation_authority_sha256": (
                    r8r_chain_authority_sha256
                ),
            }
        )
    elif r8u_r7d_implementation_authority is not None:
        if r8u_r7d_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R7D_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 4,
                "r8u_r7d_implementation_commit": (
                    r8u_r7d_implementation_authority.implementation_commit
                ),
                "r8u_r7d_continuation_authority_sha256": (
                    r8u_r7d_chain_authority_sha256
                ),
            }
        )
    elif r8u_r7_implementation_authority is not None:
        if r8u_r7_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R7_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_r7_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_r7_chain_authority_sha256
                ),
            }
        )
    elif r8u_r6_implementation_authority is not None:
        if r8u_r6_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R6_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_r6_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_r6_chain_authority_sha256
                ),
            }
        )
    elif r8u_r5_implementation_authority is not None:
        if r8u_r5_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R5_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_r5_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_r5_chain_authority_sha256
                ),
            }
        )
    elif r8u_r4_implementation_authority is not None:
        if r8u_r4_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R4_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_r4_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_r4_chain_authority_sha256
                ),
            }
        )
    elif r8u_implementation_authority is not None:
        if r8u_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_chain_authority_sha256
                ),
            }
        )
    elif r8u_r3_implementation_authority is not None:
        if r8u_r3_chain_authority_sha256 is None:
            raise ProductionFinalizationError(
                "R8U_R3_FINALIZER_CHAIN_BINDING_MISMATCH"
            )
        result.update(
            {
                "all_scientific_authority_bindings_identical": True,
                "implementation_authority_epoch_count": 3,
                "r8u_implementation_commit": (
                    r8u_r3_implementation_authority.implementation_commit
                ),
                "r8u_recovery_continuation_authority_sha256": (
                    r8u_r3_chain_authority_sha256
                ),
            }
        )
    if canonical_store is not None:
        if canonical_output_root is None:
            raise ProductionFinalizationError(
                "FINALIZER_AUTHORITY_ARGUMENTS_INCOMPLETE"
            )
        records, embeddings, clip_index, cohort_artifacts, plan_sha = canonical_store
        study_receipt = write_canonical_study_store(
            output_root=canonical_output_root,
            records=records,
            embeddings=embeddings,
            governing_commit=expected_governing_commit,
            attempt_id=next(iter(attempt_ids)),
            batch_plan_sha256=plan_sha,
            batch_receipt_set_sha256=receipt_set_hash,
            no_cine_studies=total("n_no_cine_studies"),
            expected_study_count=expected_selected_studies - expected_no_cine,
        )
        for role, name in (
            ("canonical_study_embeddings", CANONICAL_STUDY_EMBEDDINGS_NAME),
            ("canonical_study_manifest", CANONICAL_STUDY_MANIFEST_NAME),
            ("canonical_study_store", CANONICAL_STUDY_RECEIPT_NAME),
        ):
            artifact = canonical_output_root / name
            cohort_artifacts.append(
                {
                    "role": role,
                    "relative_path": artifact.relative_to(
                        production_root
                    ).as_posix(),
                    "size_bytes": artifact.stat(follow_symlinks=False).st_size,
                    "sha256": sha256_file(artifact),
                }
            )
        cohort_receipt = write_cohort_preservation_outputs(
            output_root=canonical_output_root,
            artifact_root=production_root,
            clip_index_rows=clip_index,
            artifacts=cohort_artifacts,
            governing_commit=expected_governing_commit,
            attempt_id=next(iter(attempt_ids)),
            batch_plan_sha256=plan_sha,
            batch_receipt_set_sha256=receipt_set_hash,
            production_batches=len(receipts),
            study_embeddings=study_receipt["study_embeddings"],
            no_cine_studies=total("n_no_cine_studies"),
            successfully_extracted_cines=total(
                "n_successfully_extracted_cines"
            ),
            object_technical_dispositions=disposition_total,
            studies_affected_by_technical_disposition=total(
                "n_studies_affected_by_technical_disposition"
            ),
            technical_disposition_manifest_set_sha256=(
                disposition_manifest_set_sha256
            ),
            prespecified_no_cine_study_set_sha256=plan["cohort"][
                "prespecified_no_cine_study_set_sha256"
            ],
        )
        clip_index_path = canonical_output_root / CANONICAL_CLIP_INDEX_NAME
        study_embeddings_path = canonical_output_root / CANONICAL_STUDY_EMBEDDINGS_NAME
        study_manifest_path = canonical_output_root / CANONICAL_STUDY_MANIFEST_NAME
        study_receipt_path = canonical_output_root / CANONICAL_STUDY_RECEIPT_NAME
        cohort_receipt_path = canonical_output_root / COHORT_PRESERVATION_RECEIPT_NAME
        result.update(
            {
                "status": "PASS_PRODUCTION_C3_FINALIZED",
                "canonical_clip_index_sha256": sha256_file(clip_index_path),
                "canonical_clip_index_size_bytes": clip_index_path.stat(
                    follow_symlinks=False
                ).st_size,
                "canonical_clip_index_rows": len(clip_index),
                "canonical_study_embeddings_sha256": sha256_file(
                    study_embeddings_path
                ),
                "canonical_study_embeddings_size_bytes": study_embeddings_path.stat(
                    follow_symlinks=False
                ).st_size,
                "canonical_study_manifest_sha256": sha256_file(study_manifest_path),
                "canonical_study_manifest_size_bytes": study_manifest_path.stat(
                    follow_symlinks=False
                ).st_size,
                "canonical_study_store_receipt_sha256": sha256_file(
                    study_receipt_path
                ),
                "canonical_study_store_receipt_size_bytes": study_receipt_path.stat(
                    follow_symlinks=False
                ).st_size,
                "cohort_preservation_receipt_sha256": sha256_file(
                    cohort_receipt_path
                ),
                "cohort_preservation_receipt_size_bytes": cohort_receipt_path.stat(
                    follow_symlinks=False
                ).st_size,
                "cohort_preserved_artifacts": len(cohort_receipt["artifacts"]),
                "cohort_preservation_second_pass_replay_passed": cohort_receipt[
                    "second_pass_replay_passed"
                ],
                "cohort_preservation_passed": cohort_receipt["status"]
                == "PASS_COHORT_PRESERVATION",
            }
        )
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
        canonical_output_root=args.output.parent,
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
