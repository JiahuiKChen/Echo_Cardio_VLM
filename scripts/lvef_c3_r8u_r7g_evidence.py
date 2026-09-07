#!/usr/bin/env python3
"""Body-free evidence readers for the fixed R8U-R7G adjudication.

Only bounded JSON/CSV/TSV control metadata, already-sealed hashes, and file
metadata are opened.  DICOM, extracted NPZ, embedding, model, prediction, and
performance-result bodies are never read.  Historical R7C helpers are reused
only where their fixed scientific/attempt authority is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Final, Mapping, Sequence

import finalize_lvef_c3_production as finalizer
import lvef_c3_orchestration_core as core
import lvef_c3_r8u_r7c_evidence as r7c
import lvef_c3_r8u_r7g_metadata as metadata


PRODUCTION_ROOT: Final = Path(
    "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2"
)
ATTEMPT_ROOT: Final = PRODUCTION_ROOT / "attempts" / metadata.ATTEMPT_ID
PLAN_PATH: Final = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
ORIGINAL_COHORT_RECEIPT_PATH: Final = (
    ATTEMPT_ROOT
    / "cohort_finalization/full_c3_finalization.aggregate_safe.json"
)
FAILED_PARTIAL_SEAL_PATH: Final = r7c.FAILED_PARTIAL_SEAL_PATH
FAILED_PARTIAL_ROOT: Final = r7c.FAILED_PARTIAL_ROOT
R7_TERMINAL_RECEIPT_PATH: Final = r7c.R7_TERMINAL_RECEIPT_PATH
PREFIX_FINAL_RECEIPT_SHA256: Final = r7c.PREFIX_FINAL_RECEIPT_SHA256
MAX_PLAN_BYTES: Final = r7c.MAX_PLAN_BYTES
MAX_METADATA_BYTES: Final = r7c.MAX_METADATA_BYTES
MAX_MANIFEST_BYTES: Final = r7c.MAX_MANIFEST_BYTES
MAX_LEDGER_BYTES: Final = r7c.MAX_LEDGER_BYTES


class R7GEvidenceError(RuntimeError):
    """One stable, sanitized R7G evidence error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7GEvidenceError(code)


def _translate_r7c(exc: r7c.R7CEvidenceError) -> R7GEvidenceError:
    return R7GEvidenceError(exc.code.replace("R8U_R7C", "R8U_R7G"))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_tail_batch_producer_serialization(
    value: Mapping[str, Any], payload: bytes
) -> None:
    """Enforce the compact serializer declared by the Batch-17--19 writer."""

    if payload != core.canonical_json_bytes(value):
        _fail("R8U_R7G_BATCH_RECEIPT_INVALID")


def _require_original_cohort_producer_serialization(
    value: Mapping[str, Any], payload: bytes
) -> None:
    """Enforce the indented serializer declared by the historical finalizer."""

    expected = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if payload != expected:
        _fail("R8U_R7G_ORIGINAL_COHORT_RECEIPT_INVALID")


def _read_json(
    path: Path,
    code: str,
    *,
    maximum_bytes: int = MAX_METADATA_BYTES,
) -> tuple[dict[str, Any], bytes]:
    try:
        return r7c._read_json(path, code, maximum_bytes=maximum_bytes)
    except r7c.R7CEvidenceError as exc:
        raise _translate_r7c(exc) from exc


def load_fixed_plan() -> dict[str, Any]:
    """Load the one immutable plan through an owner-private no-follow read."""

    plan, payload = _read_json(
        PLAN_PATH, "R8U_R7G_PLAN", maximum_bytes=MAX_PLAN_BYTES
    )
    if (
        _sha256(payload) != metadata.PLAN_SHA256
        or metadata.canonical_json_sha256(plan) != metadata.PLAN_SHA256
        or plan.get("schema_version") != 3
        or plan.get("artifact_type")
        != "lvef_c3_restricted_immutable_batch_plan_v3"
        or not isinstance(plan.get("batches"), list)
        or len(plan["batches"]) != 19
    ):
        _fail("R8U_R7G_PLAN_AUTHORITY_INVALID")
    return plan


def batch_final_receipt_path(ordinal: int) -> Path:
    if type(ordinal) is not int or ordinal not in range(19):
        _fail("R8U_R7G_BATCH_ORDINAL_INVALID")
    return (
        ATTEMPT_ROOT
        / "batches"
        / f"c3_batch_{ordinal:03d}"
        / "preservation/batch_finalization_receipt.restricted.json"
    )


def _validate_extraction_summary(
    value: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any],
    planned: Mapping[str, Any],
) -> None:
    """Validate the sealed v2 summary with disposition-aware gate semantics.

    The historical R7C reader required every signal/failure aggregate flag to
    be true.  That is not the v2 meaning when a candidate has an approved
    technical disposition: the candidate-level signal flags may be false even
    though every row is resolved, all successful clips are embeddable, and no
    blocking failure remains.
    """

    path_count_keys = (
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
    )
    technical = receipt.get("n_object_technical_dispositions")
    successful = receipt.get("n_successfully_extracted_cines")
    multiframe = receipt.get("n_multiframe_cines")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in (technical, successful, multiframe)
    ):
        _fail("R8U_R7G_EXTRACTION_SUMMARY_RECONCILIATION_INVALID")
    expected_status = (
        "PASS_EXTRACTION_WITH_OBJECT_TECHNICAL_DISPOSITIONS"
        if technical
        else "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
    )
    expected_pairs = {
        "n_objects": "n_expected_objects",
        "n_readable": "n_dicom_readable",
        "n_unreadable": "n_dicom_unreadable",
        "n_multiframe_candidates": "n_multiframe_cines",
        "n_single_frame": "n_single_frame_objects",
        "n_extracted_clips": "n_extracted_clips",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_successfully_extracted_clips": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_studies_affected_by_technical_disposition": (
            "n_studies_affected_by_technical_disposition"
        ),
        "n_new_no_cine_studies": "n_new_no_cine_studies",
        "object_substitution_count": "object_substitution_count",
        "unaccounted_multiframe_objects": "unaccounted_multiframe_objects",
    }
    disposition_sensitive_flags = (
        "all_failure_substages_none",
        "all_source_signal_gates_passed",
        "all_post_crop_signal_gates_passed",
        "all_sampled_signal_gates_passed",
    )
    class_counts = value.get("technical_disposition_counts_by_class")
    class_counts_valid = (
        isinstance(class_counts, Mapping)
        and all(
            isinstance(key, str)
            and key
            and type(count) is int
            and count >= 0
            for key, count in class_counts.items()
        )
    )
    if (
        set(value) != finalizer.production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
        or value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_batch_dicom_extraction_summary_v2"
        or value.get("status") != expected_status
        or value.get("n_studies") != planned.get("n_studies")
        or any(
            value.get(summary_key) != receipt.get(receipt_key)
            for summary_key, receipt_key in expected_pairs.items()
        )
        or successful + technical != multiframe
        or receipt.get("n_blocking_failures") != 0
        or any(
            type(value.get(key)) is not int or value[key] < 0
            for key in path_count_keys
        )
        or sum(int(value[key]) for key in path_count_keys) != successful
        or value.get("n_pixel_decode_failures") != 0
        or value.get("n_fallback_path_failed") != 0
        or value.get("technical_disposition_counts_by_class")
        != receipt.get("technical_disposition_counts_by_class")
        or not class_counts_valid
        or sum(class_counts.values()) != technical
        or value.get("technical_disposition_policy_version")
        != receipt.get("technical_disposition_policy_version")
        or value.get("technical_disposition_manifest_sha256")
        != receipt.get("technical_disposition_manifest_sha256")
        or any(
            value.get(key) is not True
            for key in (
                "physical_source_keys_unique",
                "clip_keys_unique",
                "all_shapes_and_dtypes_valid",
                "all_pixel_decodes_passed",
                "all_fallback_encoder_visible_signal_gates_passed",
                "all_extraction_rows_resolved",
                "all_successful_extractions_embeddable",
                "all_technical_dispositions_retained",
            )
        )
        or any(type(value.get(key)) is not bool for key in disposition_sensitive_flags)
        or (
            technical == 0
            and any(value.get(key) is not True for key in disposition_sensitive_flags)
        )
        or value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
    ):
        _fail("R8U_R7G_EXTRACTION_SUMMARY_RECONCILIATION_INVALID")


def _load_batch_metadata_impl(
    ordinal: int, *, plan: Mapping[str, Any]
) -> dict[str, Any]:
    if type(ordinal) is not int or ordinal not in range(19):
        _fail("R8U_R7G_BATCH_ORDINAL_INVALID")
    batch_id = f"c3_batch_{ordinal:03d}"
    batches = plan.get("batches") if isinstance(plan, Mapping) else None
    if not isinstance(batches, list) or len(batches) != 19:
        _fail("R8U_R7G_PLAN_AUTHORITY_INVALID")
    planned = batches[ordinal]
    if not isinstance(planned, Mapping) or planned.get("batch_id") != batch_id:
        _fail("R8U_R7G_PLAN_AUTHORITY_INVALID")
    receipt_path = batch_final_receipt_path(ordinal)
    receipt, receipt_payload = _read_json(
        receipt_path, "R8U_R7G_BATCH_RECEIPT"
    )
    if ordinal >= 16:
        _require_tail_batch_producer_serialization(receipt, receipt_payload)
    try:
        finalizer._validate_current_receipt_v3(receipt)
    except Exception as exc:
        code = str(exc)
        destination = (
            "R8U_R7G_BATCH_RECEIPT_INVALID"
            if code
            in {
                "BATCH_RECEIPT_SCHEMA_MISMATCH",
                "BATCH_RECEIPT_VERSION_MISMATCH",
            }
            else "R8U_R7G_BATCH_FINALIZATION_PROVEN_FAILURE"
        )
        raise R7GEvidenceError(destination) from exc
    receipt_sha = _sha256(receipt_payload)
    if (
        (ordinal < 16 and receipt_sha != PREFIX_FINAL_RECEIPT_SHA256[ordinal])
        or receipt.get("attempt_id") != metadata.ATTEMPT_ID
        or receipt.get("batch_id") != batch_id
        or receipt.get("governing_commit") != metadata.SCIENTIFIC_COMMIT
        or receipt.get("batch_plan_sha256") != metadata.PLAN_SHA256
        or receipt.get("n_selected_studies") != planned.get("n_studies")
        or receipt.get("n_selected_subjects") != planned.get("n_subjects")
        or receipt.get("n_expected_objects") != planned.get("n_objects")
        or receipt.get("expected_source_bytes") != planned.get("source_bytes")
        or receipt.get("prespecified_no_cine_study_set_sha256")
        != planned.get("prespecified_no_cine_study_set_sha256")
        or receipt.get("n_no_cine_studies")
        != planned.get("expected_no_cine_studies")
        or receipt.get("n_blocking_failures") != 0
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
    ):
        _fail("R8U_R7G_BATCH_RECEIPT_PLAN_MISMATCH")

    batch_root = receipt_path.parents[1]
    preservation_root = receipt_path.parent
    extraction_root = (
        ATTEMPT_ROOT / "extracted_cache" / batch_id / "dicom_extraction"
    )
    preservation_path = (
        preservation_root / "batch_preservation_receipt.restricted.json"
    )
    preservation, preservation_payload = _read_json(
        preservation_path, "R8U_R7G_PRESERVATION_RECEIPT"
    )
    if (
        set(preservation)
        != set(finalizer.PRESERVATION_ELIGIBILITY_RECEIPT_KEYS)
        or preservation.get("artifact_type")
        != "lvef_c3_batch_preservation_eligibility_receipt_v3"
        or preservation.get("schema_version") != 2
    ):
        _fail("R8U_R7G_PRESERVATION_RECEIPT_INVALID")
    if preservation.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        _fail("R8U_R7G_PRESERVATION_PROVEN_FAILURE")
    if (
        preservation.get("batch_id") != batch_id
        or preservation.get("attempt_id") != metadata.ATTEMPT_ID
        or preservation.get("extracted_cache_retired") is not False
        or any(
            preservation.get(key) != receipt.get(key)
            for key in preservation
            if key not in {"artifact_type", "status", "extracted_cache_retired"}
        )
    ):
        _fail("R8U_R7G_PRESERVATION_RECEIPT_RECONCILIATION_INVALID")
    preservation_sha = _sha256(preservation_payload)

    manifest_path = (
        preservation_root / "batch_preservation_manifest.restricted.tsv"
    )
    rows = r7c._manifest_rows(
        manifest_path, str(receipt["preservation_manifest_sha256"])
    )
    r7c._validate_manifest_authority(rows, batch_id=batch_id, planned=planned)
    extraction_summary_path = (
        extraction_root / "dicom_extraction.summary.json"
    )
    extraction_summary, summary_payload = _read_json(
        extraction_summary_path, "R8U_R7G_EXTRACTION_SUMMARY"
    )
    summary_sha = _sha256(summary_payload)
    if r7c._require_metadata_row_matches_file(
        rows,
        extraction_summary_path,
        role="dicom_extraction_metadata_retained",
        content_hash=True,
    ) != summary_sha:
        _fail("R8U_R7G_EXTRACTION_SUMMARY_HASH_MISMATCH")

    extraction_stage_path = (
        extraction_root / "stage_completion_receipt.restricted.json"
    )
    extraction_stage, extraction_stage_payload = _read_json(
        extraction_stage_path, "R8U_R7G_EXTRACTION_STAGE_RECEIPT"
    )
    extraction_stage_sha = _sha256(extraction_stage_payload)
    verified_download_manifest_path = (
        ATTEMPT_ROOT
        / "raw"
        / batch_id
        / "verified_download_manifest.restricted.csv"
    )
    r7c._validate_stage_receipt(
        extraction_stage,
        batch_id=batch_id,
        stage="DICOM_EXTRACTION",
        required_artifacts={
            "dicom_extraction.summary.json": summary_sha,
            "dicom_audit.restricted.csv": str(receipt["dicom_audit_sha256"]),
            "extraction_manifest.restricted.csv": str(
                receipt["extraction_manifest_sha256"]
            ),
            "technical_disposition_manifest.restricted.csv": str(
                receipt["technical_disposition_manifest_sha256"]
            ),
        },
    )
    if extraction_stage.get("input_manifest_sha256") != r7c._manifest_digest_for_path(
        rows,
        verified_download_manifest_path,
        role="raw_dicom_and_download_authority",
    ):
        _fail("R8U_R7G_EXTRACTION_STAGE_HASH_MISMATCH")
    if r7c._require_metadata_row_matches_file(
        rows,
        extraction_stage_path,
        role="dicom_extraction_metadata_retained",
        content_hash=True,
    ) != extraction_stage_sha:
        _fail("R8U_R7G_EXTRACTION_STAGE_HASH_MISMATCH")

    echo_stage_path = (
        batch_root / "echoprime/stage_completion_receipt.restricted.json"
    )
    echo_stage, echo_stage_payload = _read_json(
        echo_stage_path, "R8U_R7G_ECHOPRIME_STAGE_RECEIPT"
    )
    echo_artifact_paths = {
        name: batch_root / "echoprime" / name
        for name in (
            "clip_embeddings.restricted.npz",
            "clip_manifest.restricted.csv",
            "study_embeddings.restricted.npz",
            "study_manifest.restricted.csv",
            "study_disposition.restricted.csv",
            "echoprime_pooling.summary.json",
        )
    }
    echo_artifacts = {
        name: r7c._manifest_digest_for_path(
            rows, path, role="embedding_and_pooling_retained"
        )
        for name, path in echo_artifact_paths.items()
    }
    if (
        echo_artifacts["clip_manifest.restricted.csv"]
        != receipt["clip_manifest_sha256"]
        or echo_artifacts["clip_embeddings.restricted.npz"]
        != receipt["clip_embeddings_sha256"]
        or echo_artifacts["study_manifest.restricted.csv"]
        != receipt["study_manifest_sha256"]
        or echo_artifacts["study_embeddings.restricted.npz"]
        != receipt["study_embeddings_sha256"]
    ):
        _fail("R8U_R7G_EMBEDDING_METADATA_AUTHORITY_INVALID")
    r7c._validate_stage_receipt(
        echo_stage,
        batch_id=batch_id,
        stage="ECHOPRIME_EMBEDDING",
        required_artifacts=echo_artifacts,
    )
    if (
        echo_stage.get("input_manifest_sha256")
        != receipt["extraction_manifest_sha256"]
        or echo_stage.get("runtime_authority")
        != extraction_stage.get("runtime_authority")
    ):
        _fail("R8U_R7G_ECHOPRIME_STAGE_RECEIPT_INVALID")
    echo_stage_sha = _sha256(echo_stage_payload)
    r7c._require_metadata_row_matches_file(
        rows,
        echo_stage_path,
        role="embedding_and_pooling_retained",
        content_hash=True,
    )
    if r7c._manifest_digest_for_path(
        rows, echo_stage_path, role="embedding_and_pooling_retained"
    ) != echo_stage_sha:
        _fail("R8U_R7G_ECHOPRIME_STAGE_RECEIPT_INVALID")

    # Scientific stores are checked only by their sealed manifest metadata.
    for basename, digest in (
        ("clip_embeddings.restricted.npz", receipt["clip_embeddings_sha256"]),
        ("study_embeddings.restricted.npz", receipt["study_embeddings_sha256"]),
    ):
        path = batch_root / "echoprime" / basename
        sealed_digest = r7c._manifest_digest_for_path(
            rows, path, role="embedding_and_pooling_retained"
        )
        if sealed_digest != digest or (
            ordinal >= 16
            and r7c._require_metadata_row_matches_file(
                rows,
                path,
                role="embedding_and_pooling_retained",
                content_hash=False,
            )
            != digest
        ):
            _fail("R8U_R7G_EMBEDDING_METADATA_AUTHORITY_INVALID")

    _validate_extraction_summary(
        extraction_summary, receipt=receipt, planned=planned
    )
    retired_bytes, retired_tree_sha = r7c._retired_cache_projection(
        rows,
        batch_id=batch_id,
        expected_tree_sha256=str(receipt["cache_tree_sha256"]),
        expected_files=int(receipt["n_successfully_extracted_cines"]),
        inspect_cache_root=ordinal >= 16,
    )

    authorization_path = (
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{batch_id}.authorization.json"
    )
    authorization, authorization_payload = _read_json(
        authorization_path, "R8U_R7G_CACHE_AUTHORIZATION"
    )
    authorization_sha = _sha256(authorization_payload)
    if authorization_sha != receipt["cache_retirement_authorization_sha256"]:
        _fail("R8U_R7G_CACHE_AUTHORIZATION_INVALID")
    intent, intent_payload = _read_json(
        preservation_root / "cache_retirement_intent.restricted.json",
        "R8U_R7G_CACHE_INTENT",
    )
    intent_sha = _sha256(intent_payload)
    staged, staged_payload = _read_json(
        preservation_root / "cache_atomically_staged.restricted.json",
        "R8U_R7G_CACHE_STAGED",
    )
    staged_sha = _sha256(staged_payload)
    transition, transition_payload = _read_json(
        preservation_root / "cache_retirement_finalized.restricted.json",
        "R8U_R7G_CACHE_TRANSITION",
    )
    transition_sha = _sha256(transition_payload)
    final_ledger, final_ledger_payload = _read_json(
        batch_root / "final_resume_ledger.restricted.json",
        "R8U_R7G_FINAL_LEDGER",
        maximum_bytes=MAX_LEDGER_BYTES,
    )
    runtime_authority = extraction_stage["runtime_authority"]
    r7c._validate_retirement_chain(
        batch_id=batch_id,
        receipt_sha256=receipt_sha,
        preservation_sha256=preservation_sha,
        retired_tree_sha256=retired_tree_sha,
        authorization=authorization,
        authorization_sha256=authorization_sha,
        intent=intent,
        intent_sha256=intent_sha,
        staged=staged,
        staged_sha256=staged_sha,
        expected_staged_sha256=str(
            receipt["cache_atomically_staged_receipt_sha256"]
        ),
        transition=transition,
        final_ledger=final_ledger,
        expected_runtime_authority=runtime_authority,
        expected_object_keys={
            str(item["source_object_key"]) for item in planned["objects"]
        },
    )

    source_objects = int(receipt["n_expected_objects"])
    readable = int(receipt["n_dicom_readable"])
    multiframe = int(receipt["n_multiframe_cines"])
    result = {
        "batch_id": batch_id,
        "ordinal": ordinal,
        "attempt_id": metadata.ATTEMPT_ID,
        "batch_plan_sha256": metadata.PLAN_SHA256,
        "scientific_commit": metadata.SCIENTIFIC_COMMIT,
        "batch_finalization_receipt_sha256": receipt_sha,
        "preservation_receipt_sha256": preservation_sha,
        "preservation_manifest_sha256": str(
            receipt["preservation_manifest_sha256"]
        ),
        "extraction_stage_completion_receipt_sha256": extraction_stage_sha,
        "extraction_summary_sha256": summary_sha,
        "cache_retirement_transition_sha256": transition_sha,
        "final_ledger_sha256": _sha256(final_ledger_payload),
        "retired_cache_tree_sha256": retired_tree_sha,
        "study_membership_sha256": str(planned["study_membership_sha256"]),
        "prespecified_no_cine_study_set_sha256": str(
            planned["prespecified_no_cine_study_set_sha256"]
        ),
        "n_selected_studies": int(receipt["n_selected_studies"]),
        "n_selected_subjects": int(receipt["n_selected_subjects"]),
        "n_source_objects": source_objects,
        "source_bytes": int(receipt["expected_source_bytes"]),
        "n_downloaded_objects": int(receipt["n_download_verified"]),
        "downloaded_bytes": int(receipt["expected_source_bytes"]),
        "n_readable_objects": readable,
        "n_unreadable_objects": source_objects - readable,
        "readable_bytes": int(receipt["expected_source_bytes"]),
        "n_multiframe_candidates": multiframe,
        "n_single_frame_objects": readable - multiframe,
        "n_successful_extractions": int(
            receipt["n_successfully_extracted_cines"]
        ),
        "n_clip_embeddings": int(receipt["n_clip_embeddings"]),
        "n_technical_dispositions": int(
            receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(receipt["n_blocking_failures"]),
        "n_ordinary_preprocessing_path": int(
            extraction_summary["n_ordinary_preprocessing_path"]
        ),
        "n_spatial_fallback_preprocessing_path": int(
            extraction_summary["n_spatial_fallback_preprocessing_path"]
        ),
        "n_temporal_fallback_preprocessing_path": int(
            extraction_summary["n_temporal_fallback_preprocessing_path"]
        ),
        "n_spatial_temporal_fallback_preprocessing_path": int(
            extraction_summary[
                "n_spatial_temporal_fallback_preprocessing_path"
            ]
        ),
        "n_study_embeddings": int(receipt["n_pooled_studies"]),
        "n_prespecified_no_cine_studies": int(receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(receipt["n_new_no_cine_studies"]),
        "retired_extracted_cache_bytes": retired_bytes,
        "n_missing_selected_studies": int(
            receipt["n_missing_selected_studies"]
        ),
        "n_duplicate_selected_studies": 0,
        "n_source_substitutions": int(receipt["object_substitution_count"]),
        "n_unaccounted_multiframe_candidates": int(
            receipt["unaccounted_multiframe_objects"]
        ),
        "n_outcome_informed_decisions": 0,
        "final_ledger_status": "FINALIZED",
        "preservation_status": str(preservation["status"]),
        "cache_retirement_status": "PASS_RETIRED",
        "batch_finalization_status": str(receipt["status"]),
        "raw_source_authority_retained": True,
        "extracted_cache_absent": True,
    }
    if set(result) != metadata.BATCH_METADATA_KEYS:
        _fail("R8U_R7G_BATCH_METADATA_SCHEMA_INVALID")
    return result


def load_batch_metadata(
    ordinal: int, *, plan: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        return _load_batch_metadata_impl(ordinal, plan=plan)
    except r7c.R7CEvidenceError as exc:
        raise _translate_r7c(exc) from exc


def load_all_batch_metadata(*, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [load_batch_metadata(ordinal, plan=plan) for ordinal in range(19)]


def _original_batch_totals(
    batch_metadata: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    if (
        not isinstance(batch_metadata, Sequence)
        or isinstance(batch_metadata, (str, bytes))
        or len(batch_metadata) != 19
    ):
        _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
    totals = {key: 0 for key in metadata.BATCH_COUNT_KEYS}
    hashes: list[str] = []
    seen: set[str] = set()
    for ordinal, item in enumerate(batch_metadata):
        expected_batch = f"c3_batch_{ordinal:03d}"
        expected_studies = 30 if ordinal == 18 else 250
        if (
            not isinstance(item, Mapping)
            or set(item) != metadata.BATCH_METADATA_KEYS
            or item.get("batch_id") != expected_batch
            or item.get("ordinal") != ordinal
            or item.get("attempt_id") != metadata.ATTEMPT_ID
            or item.get("batch_plan_sha256") != metadata.PLAN_SHA256
            or item.get("scientific_commit") != metadata.SCIENTIFIC_COMMIT
            or item.get("n_selected_studies") != expected_studies
        ):
            _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        digest = str(item.get("batch_finalization_receipt_sha256", ""))
        if metadata.SHA256_RE.fullmatch(digest) is None or digest in seen:
            _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        if ordinal < 16 and digest != PREFIX_FINAL_RECEIPT_SHA256[ordinal]:
            _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        seen.add(digest)
        hashes.append(digest)
        for key in totals:
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
            totals[key] += value
    if (
        totals["n_selected_studies"] != metadata.EXPECTED_SELECTED_STUDIES
        or totals["n_source_objects"]
        != metadata.EXPECTED_SELECTED_SOURCE_OBJECTS
        or totals["source_bytes"] != metadata.EXPECTED_SELECTED_SOURCE_BYTES
        or totals["n_blocking_failures"] != 0
    ):
        _fail("R8U_R7G_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
    return totals, hashes


def load_original_cohort_finalization_receipt(
    batch_metadata: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Load the existing R7F finalizer summary, or return ``None`` if absent."""

    try:
        visible = os.lstat(ORIGINAL_COHORT_RECEIPT_PATH)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise R7GEvidenceError(
            "R8U_R7G_ORIGINAL_COHORT_RECEIPT_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(visible.st_mode)
        or not stat.S_ISREG(visible.st_mode)
        or visible.st_uid != os.geteuid()
        or visible.st_nlink != 1
        or stat.S_IMODE(visible.st_mode) != 0o600
    ):
        _fail("R8U_R7G_ORIGINAL_COHORT_RECEIPT_INVALID")
    value, payload = _read_json(
        ORIGINAL_COHORT_RECEIPT_PATH,
        "R8U_R7G_ORIGINAL_COHORT_RECEIPT",
    )
    _require_original_cohort_producer_serialization(value, payload)
    try:
        finalizer.validate_closed_final_summary(value)
    except Exception as exc:
        raise R7GEvidenceError(
            "R8U_R7G_ORIGINAL_COHORT_RECEIPT_INVALID"
        ) from exc
    totals, receipt_hashes = _original_batch_totals(batch_metadata)
    expected_receipt_set_sha256 = _sha256(
        ("\n".join(sorted(receipt_hashes)) + "\n").encode("ascii")
    )
    expected_counts = {
        "production_batches": 19,
        "selected_studies": totals["n_selected_studies"],
        "selected_subjects": totals["n_selected_subjects"],
        "verified_source_objects": totals["n_downloaded_objects"],
        "selected_source_bytes": totals["source_bytes"],
        "dicom_readable_objects": totals["n_readable_objects"],
        "dicom_unreadable_objects": totals["n_unreadable_objects"],
        "multiframe_cines": totals["n_multiframe_candidates"],
        "single_frame_objects": totals["n_single_frame_objects"],
        "extracted_clips": totals["n_successful_extractions"],
        "successfully_extracted_cines": totals["n_successful_extractions"],
        "object_technical_dispositions": totals[
            "n_technical_dispositions"
        ],
        "blocking_failures": totals["n_blocking_failures"],
        "new_no_cine_studies": totals["n_new_no_cine_studies"],
        "unique_clip_keys": totals["n_clip_embeddings"],
        "clip_embeddings": totals["n_clip_embeddings"],
        "pooled_imaging_eligible_studies": totals["n_study_embeddings"],
        "no_cine_studies": totals["n_prespecified_no_cine_studies"],
        "missing_selected_studies": totals["n_missing_selected_studies"],
        "object_substitution_count": totals["n_source_substitutions"],
        "unaccounted_multiframe_objects": totals[
            "n_unaccounted_multiframe_candidates"
        ],
        "canonical_clip_index_rows": totals["n_clip_embeddings"],
        "cohort_preserved_artifacts": 2 * 19 + 4,
    }
    expected_class_counts = {
        finalizer.production_stages.OBJECT_TECHNICAL_DISPOSITION: totals[
            "n_technical_dispositions"
        ]
    }
    if (
        value.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or value.get("r8u_r7d_implementation_commit")
        != metadata.R7F_RUNTIME_IMPLEMENTATION_COMMIT
        or value.get("implementation_authority_epoch_count") != 4
        or value.get("all_scientific_authority_bindings_identical") is not True
        or value.get("batch_receipt_set_sha256")
        != expected_receipt_set_sha256
        or any(
            value.get(key) != expected for key, expected in expected_counts.items()
        )
        or value.get("technical_disposition_counts_by_class")
        != expected_class_counts
    ):
        _fail("R8U_R7G_ORIGINAL_COHORT_RECEIPT_CONTRADICTION")
    return value, _sha256(payload)


def fixed_cache_topology() -> dict[str, Any]:
    """Reopen the R7A-bound Batch-16 failed-partial evidence unchanged."""

    try:
        # This historical validator deliberately reopens the immutable R7A
        # continuation receipt and proves its binding to the failed-partial
        # seal and R7 preservation-recovery terminal receipt.
        value = r7c.fixed_cache_topology()
        return metadata.validate_cache_topology(value)
    except r7c.R7CEvidenceError as exc:
        raise _translate_r7c(exc) from exc


__all__ = [
    "ATTEMPT_ROOT",
    "FAILED_PARTIAL_ROOT",
    "FAILED_PARTIAL_SEAL_PATH",
    "ORIGINAL_COHORT_RECEIPT_PATH",
    "PLAN_PATH",
    "PREFIX_FINAL_RECEIPT_SHA256",
    "PRODUCTION_ROOT",
    "R7GEvidenceError",
    "batch_final_receipt_path",
    "fixed_cache_topology",
    "load_all_batch_metadata",
    "load_batch_metadata",
    "load_fixed_plan",
    "load_original_cohort_finalization_receipt",
]
