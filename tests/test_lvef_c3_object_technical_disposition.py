from __future__ import annotations

"""Focused, dependency-light R5A technical-disposition release gates.

Every test is deliberately zero-argument so ``run_phase1a_tests.py`` executes
the same contracts on the SCC login node without pytest fixtures.
"""

import base64
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import traceback
from typing import Any, Callable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_full_sequential as sequential
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import lvef_multitask_analysis_modes as analysis_modes
import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement


DISPOSITION = "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR"
ZERO_DISPOSITION_MAP = {DISPOSITION: 0}


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def _expect_code(expected: str, function: Callable[[], Any]) -> None:
    try:
        function()
    except Exception as exc:
        assert _code(exc) == expected, (_code(exc), expected)
    else:
        raise AssertionError(f"expected {expected}")


def _expect_failure(function: Callable[[], Any]) -> BaseException:
    try:
        function()
    except Exception as exc:
        return exc
    raise AssertionError("expected a fail-closed exception")


def _digest(label: object) -> str:
    return hashlib.sha256(str(label).encode("utf-8")).hexdigest()


def _source_relative(seed: object) -> str:
    return f"synthetic_authority/cines/cine_{seed}.dcm"


def _clip_key(relative: str) -> str:
    return hashlib.sha256(
        f"{stages.CLIP_KEY_NAMESPACE}\0{relative}".encode("utf-8")
    ).hexdigest()


def _successful_row(
    seed: object,
    *,
    subject_id: str = "100001",
    study_id: str = "200001",
) -> dict[str, Any]:
    physical_source_key = _digest(f"physical-{seed}")
    relative = f"{physical_source_key}.dcm"
    clip = _clip_key(relative)
    row: dict[str, Any] = {
        "subject_id": subject_id,
        "study_id": study_id,
        "smoke_role": "production_selected",
        "source_relative_path": relative,
        "source_sha256": _digest(f"source-body-{seed}"),
        "clip_key": clip,
        "output_relative_path": f"clips/{clip[:2]}/{clip}.npz",
        "write_ok": True,
        "mask_status": "APPLIED",
        "photometric_interpretation": "MONOCHROME2",
        "transfer_syntax_uid": "1.2.840.10008.1.2.1",
        "decoder_backend": "pydicom_pixels_raw:3.0.1",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "MONOCHROME2_REPLICATE_TO_RGB",
        "canonical_color_space": "RGB",
        "selected_preprocessing_path": stages.ORDINARY_PREPROCESSING_PATH,
        "fallback_status": stages.FALLBACK_NOT_ATTEMPTED,
        "failure_substage": "NONE",
        "decode_color_status": "PASS",
        "temporal_sampling_policy": stages.ORDINARY_TEMPORAL_SAMPLING_POLICY,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "frames_sha256": _digest(f"frames-{seed}"),
        "sampled_indices_sha256": _digest(f"indices-{seed}"),
        "source_num_frames_sha256": _digest(f"frame-count-{seed}"),
        "npz_sha256": _digest(f"npz-{seed}"),
        "source_num_frames": 40,
        "error_code": "",
        "physical_source_key": physical_source_key,
        "pixel_decode_ok": True,
    }
    for count_field, gate_field in (
        *stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS,
        *stages.DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS,
    ):
        row[count_field] = 10
        row[gate_field] = True
    assert set(row) == stages.EXTRACTION_PRODUCER_FIELDS
    return row


def _disposed_row(
    seed: object = "disposed",
    *,
    subject_id: str = "100001",
    study_id: str = "200001",
) -> dict[str, Any]:
    row = _successful_row(seed, subject_id=subject_id, study_id=study_id)
    row.update(
        {
            "write_ok": False,
            "mask_status": "FAILED",
            "selected_preprocessing_path": "NOT_SELECTED",
            "fallback_status": stages.FALLBACK_NOT_ATTEMPTED,
            "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
            "frames_shape": None,
            "frames_dtype": None,
            "frames_sha256": None,
            "sampled_indices_sha256": None,
            "source_num_frames_sha256": None,
            "npz_sha256": None,
            "error_code": "SourceSignalQualityFailure",
        }
    )
    for ordinal, (count_field, gate_field) in enumerate(
        stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS
    ):
        row[count_field] = 0 if ordinal == 2 else 10
        row[gate_field] = row[count_field] > 0
    for count_field, gate_field in stages.DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS:
        row[count_field] = None
        row[gate_field] = False
    assert set(row) == stages.EXTRACTION_PRODUCER_FIELDS
    return row


def _context(rows: list[Mapping[str, Any]]) -> stages.ExtractionDispositionContext:
    failures = [row for row in rows if row.get("write_ok") is not True]
    successes: dict[str, int] = {}
    for row in rows:
        if row.get("write_ok") is True:
            study = str(row["study_id"])
            successes[study] = successes.get(study, 0) + 1
    return stages.ExtractionDispositionContext(
        approved_source_keys=frozenset(
            str(row["physical_source_key"]) for row in failures
        ),
        raw_retained_source_keys=frozenset(
            str(row["physical_source_key"]) for row in failures
        ),
        npz_absent_clip_keys=frozenset(str(row["clip_key"]) for row in failures),
        embedding_absent_clip_keys=frozenset(
            str(row["clip_key"]) for row in failures
        ),
        study_success_counts=tuple(sorted(successes.items())),
        source_num_frames_by_key=tuple(
            sorted(
                (str(row["physical_source_key"]), int(row["source_num_frames"]))
                for row in failures
            )
        ),
        object_substitution_count=0,
    )


def _validate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return stages.validate_production_extraction_rows(
        rows,
        expected_cines=len(rows),
        disposition_context=_context(rows),
    )


def _manifest_fixture() -> tuple[
    list[dict[str, Any]],
    stages.ExtractionDispositionContext,
    list[dict[str, Any]],
]:
    rows = [_successful_row("manifest-success"), _disposed_row("manifest-failure")]
    context = _context(rows)
    manifest = stages.technical_disposition_manifest_rows(rows, context=context)
    return rows, context, manifest


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def _plan_fixture(
    *, expected_no_cine: tuple[int, ...] = ()
) -> tuple[dict[str, Any], core.PlanRequirements]:
    selected = [
        {"subject_id": str(100 + index), "study_id": str(200 + index)}
        for index in range(1, 5)
    ]
    split = [{"subject_id": row["subject_id"], "split": "train"} for row in selected]
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    for ordinal, row in enumerate(selected):
        relative = (
            f"files/p00/p{row['subject_id']}/s{row['study_id']}/"
            f"synthetic_cine_{ordinal + 1:03d}.dcm"
        )
        payload = f"r5a-synthetic-{ordinal}".encode("ascii")
        total_bytes += len(payload)
        sources.append(
            {
                **row,
                "split": "train",
                "production_batch": f"c3_batch_{ordinal // 2:03d}",
                "source_relative_path": relative,
                "source_object_key": hashlib.sha256(
                    f"mimic-iv-echo/1.0\0{relative}".encode("utf-8")
                ).hexdigest(),
                "size_bytes": len(payload),
                "generation": str(1000 + ordinal),
                "md5_base64": base64.b64encode(
                    hashlib.md5(payload, usedforsecurity=False).digest()
                ).decode("ascii"),
                "crc32c_base64": base64.b64encode(
                    (ordinal + 1).to_bytes(4, "big")
                ).decode("ascii"),
            }
        )
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=4,
        selected_subjects=4,
        normalized_source_objects=4,
        selected_source_bytes=total_bytes,
        batch_count=2,
        studies_per_full_batch=2,
        final_batch_studies=2,
        contract_id="r5a_synthetic_two_batch_v1",
    )
    authority = {
        key: ("a" * 40 if key == "git_commit" else _digest(key))
        for key in core.PLAN_AUTHORITY_KEYS
    }
    no_cine = [selected[index] for index in expected_no_cine]
    plan = core.build_immutable_batch_plan(
        selected,
        sources,
        split,
        requirements=requirements,
        authority=authority,
        prespecified_no_cine_studies=no_cine,
    )
    return plan, requirements


def _capacity_authority() -> dict[str, Any]:
    increment = sequential.SUCCESSOR_INCREMENT_BYTES
    reserve = sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES
    live = 123_456_789
    projected = live + increment
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_fresh_successor_capacity_authority_v1",
        "status": "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE",
        "source_capacity_authority_sha256": "a" * 64,
        "frozen_projected_peak_bytes": sequential.FROZEN_FULL_PLAN_PROJECTED_PEAK_BYTES,
        "frozen_original_current_usage_bytes": (
            sequential.FROZEN_FULL_PLAN_ORIGINAL_CURRENT_USAGE_BYTES
        ),
        "successor_increment_bytes": increment,
        "live_research_usage_bytes": live,
        "projected_total_research_usage_bytes": projected,
        "research_quota_bytes": projected + reserve,
        "quota_remaining_after_successor_bytes": reserve,
        "research_filesystem_available_bytes": increment + reserve,
        "physical_remaining_after_successor_bytes": reserve,
        "research_file_slots_remaining": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
        "required_reserve_bytes": reserve,
        "required_remaining_file_slots": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
        "active_extraction_caches": 0,
        "preserved_terminal_failed_extraction_caches": 2,
        "quota_reserve_gate_passed": True,
        "physical_reserve_gate_passed": True,
        "file_slot_gate_passed": True,
        "terminal_failure_cache_gate_passed": True,
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "dicom_body_reads": 0,
        "writes_performed": 0,
    }


def _receipt(*, current: bool, canary: bool = False) -> dict[str, Any]:
    keys = (
        finalizer.BATCH_RECEIPT_KEYS
        if current
        else finalizer.LEGACY_BATCH_RECEIPT_KEYS_V2
    )
    value: dict[str, Any] = {key: 0 for key in keys}
    value.update(
        {
            "schema_version": 2 if current else 1,
            "artifact_type": (
                "lvef_c3_batch_finalization_receipt_v3"
                if current
                else "lvef_c3_batch_finalization_receipt_v2"
            ),
            "status": "PASS_BATCH_FINALIZED",
            "batch_id": "c3_batch_000",
            "attempt_id": "lvef_c3_r5a_synthetic",
            "governing_commit": "a" * 40,
            "source_commit": "a" * 40,
            "run_timestamp_utc": "2026-08-17T12:00:00Z",
            "cohort_version": "mimic-iv-echo/1.0",
            "split_version": "split_map_sha256:" + "b" * 64,
            "execution_contract_version": 2,
            "aggregate_safety_gate_result": "PASS",
            "python_version": "3.11",
            "pytorch_version": "2.7",
            "torchvision_version": "0.22",
            "cuda_version": "12.8",
            "cudnn_version": "9",
            "scheduler_job_identity": "synthetic.1",
            "n_selected_studies": 1,
            "n_selected_subjects": 1,
            "n_expected_objects": 1,
            "expected_source_bytes": 1,
            "n_download_verified": 1,
            "n_dicom_readable": 1,
            "n_dicom_unreadable": 0,
            "n_multiframe_cines": 1,
            "n_single_frame_objects": 0,
            "n_extracted_clips": 1,
            "n_unique_clip_keys": 1,
            "n_clip_embeddings": 1,
            "n_pooled_studies": 1,
            "n_no_cine_studies": 0,
            "no_cine_disposition": "NONE",
            "raw_dicoms_retained": True,
            "extracted_cache_retired": True,
        }
    )
    for key in finalizer.LEGACY_HASH_KEYS_V2:
        value[key] = "c" * 64
    value["checkpoint_checksum"] = value["checkpoint_sha256"]
    for key in finalizer.LEGACY_TRUE_GATE_KEYS_V2:
        value[key] = True
    for key in finalizer.LEGACY_ZERO_KEYS_V2:
        value[key] = 0
    if current:
        value.update(
            {
                "n_successfully_extracted_cines": 1,
                "n_object_technical_dispositions": 0,
                "n_blocking_failures": 0,
                "n_studies_affected_by_technical_disposition": 0,
                "n_new_no_cine_studies": 0,
                "technical_disposition_counts_by_class": ZERO_DISPOSITION_MAP,
                "technical_disposition_policy_version": (
                    stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
                ),
                "technical_disposition_manifest_sha256": "d" * 64,
                "prespecified_no_cine_study_set_sha256": (
                    core.canonical_json_sha256([])
                ),
                "all_no_cine_studies_prespecified": True,
                "all_extraction_rows_resolved": True,
                "all_successful_extractions_embedded": True,
                "all_technical_dispositions_retained": True,
                "object_substitution_count": 0,
                "unaccounted_multiframe_objects": 0,
            }
        )
    if canary:
        for key in finalizer.RETIREMENT_RECEIPT_KEYS:
            value.pop(key)
        value.update(
            {
                "artifact_type": (
                    "lvef_c3_batch_preservation_eligibility_receipt_v3"
                    if current
                    else "lvef_c3_batch_preservation_eligibility_receipt_v2"
                ),
                "status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
                "batch_id": "canary_batch_000",
                "n_selected_studies": 5,
                "n_selected_subjects": 5,
                "n_expected_objects": 6,
                "expected_source_bytes": 60_000,
                "n_download_verified": 6,
                "n_dicom_readable": 6,
                "n_multiframe_cines": 5,
                "n_single_frame_objects": 1,
                "n_extracted_clips": 5,
                "n_unique_clip_keys": 5,
                "n_clip_embeddings": 5,
                "n_pooled_studies": 5,
                "extracted_cache_retired": False,
            }
        )
        if current:
            value["n_successfully_extracted_cines"] = 5
    assert set(value) == (
        finalizer.PRESERVATION_ELIGIBILITY_RECEIPT_KEYS
        if current and canary
        else finalizer.LEGACY_PRESERVATION_ELIGIBILITY_RECEIPT_KEYS_V2
        if canary
        else keys
    )
    return value


def _reconciled_final_summary() -> dict[str, Any]:
    value: dict[str, Any] = {key: 0 for key in finalizer.FINAL_KEYS}
    value.update(
        {
            "schema_version": 2,
            "artifact_type": "lvef_c3_production_finalization_summary_v2",
            "status": "PASS_PRODUCTION_C3_BATCH_RECEIPTS_RECONCILED",
            "production_batches": 1,
            "selected_studies": 1,
            "selected_subjects": 1,
            "verified_source_objects": 1,
            "selected_source_bytes": 1,
            "dicom_readable_objects": 1,
            "dicom_unreadable_objects": 0,
            "multiframe_cines": 1,
            "single_frame_objects": 0,
            "extracted_clips": 1,
            "successfully_extracted_cines": 1,
            "object_technical_dispositions": 0,
            "blocking_failures": 0,
            "studies_affected_by_technical_disposition": 0,
            "new_no_cine_studies": 0,
            "technical_disposition_counts_by_class": ZERO_DISPOSITION_MAP,
            "technical_disposition_policy_version": (
                stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_set_sha256": "d" * 64,
            "unique_clip_keys": 1,
            "clip_embeddings": 1,
            "pooled_imaging_eligible_studies": 1,
            "no_cine_studies": 0,
            "no_cine_disposition": "NONE",
            "batch_receipt_set_sha256": "e" * 64,
            "all_batches_finalized": True,
            "all_authority_bindings_identical": True,
            "all_source_receipts_passed": True,
            "all_dicom_audits_passed": True,
            "all_extraction_rows_resolved": True,
            "all_successful_extractions_embedded": True,
            "all_technical_dispositions_retained": True,
            "all_no_cine_studies_prespecified": True,
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
            "cohort_preservation_second_pass_replay_passed": False,
            "cohort_preservation_passed": False,
        }
    )
    for key in finalizer.FINAL_BINDING_KEYS:
        if key.endswith("_sha256"):
            value[key] = None
        elif key.endswith("_size_bytes") or key in {
            "canonical_clip_index_rows",
            "cohort_preserved_artifacts",
        }:
            value[key] = 0
    assert set(value) == finalizer.FINAL_KEYS
    return value


def test_case_01_exact_batch3_pattern_is_one_disposition_and_one_affected_study() -> None:
    """Separately runnable exact 10,257-object Batch-3 release fixture."""

    successes = [
        _successful_row(
            index,
            study_id="200038" if index < 38 else "200039",
        )
        for index in range(10_256)
    ]
    rows = [*successes, _disposed_row("exact-batch3", study_id="200038")]
    summary = _validate(rows)
    assert summary["n_requested_cines"] == 10_257
    assert summary["n_successfully_extracted_cines"] == 10_256
    assert summary["n_object_technical_dispositions"] == 1
    assert summary["n_studies_affected_by_technical_disposition"] == 1
    assert summary["technical_disposition_counts_by_class"] == {DISPOSITION: 1}
    assert summary["n_blocking_failures"] == 0
    assert summary["n_new_no_cine_studies"] == 0
    manifest = stages.technical_disposition_manifest_rows(rows, context=_context(rows))
    assert len(manifest) == 1
    assert manifest[0]["study_retains_valid_cine_coverage"] is True
    preservation.validate_preservation_disposition_accounting(
        {
            "n_multiframe_cines": 10_257,
            "n_successfully_extracted_cines": 10_256,
            "n_object_technical_dispositions": 1,
            "n_studies_affected_by_technical_disposition": 1,
            "technical_disposition_counts_by_class": {DISPOSITION: 1},
            "n_extracted_clips": 10_256,
            "n_unique_clip_keys": 10_256,
            "n_clip_embeddings": 10_256,
        }
    )


def test_case_02_zero_disposition_batches_emit_exact_one_key_zero_map() -> None:
    summary = _validate([_successful_row("zero")])
    assert summary["technical_disposition_counts_by_class"] == ZERO_DISPOSITION_MAP
    assert summary["n_object_technical_dispositions"] == 0
    assert stages.validate_technical_disposition_manifest_rows(
        [_successful_row("zero-manifest")], [], context=None
    )["technical_disposition_counts_by_class"] == ZERO_DISPOSITION_MAP


def test_case_02_r4d2c_is_bound_provenance_but_unreachable_to_classifier() -> None:
    policy = core.load_strict_json(
        ROOT / "configs" / "lvef_c3_source_signal_object_technical_disposition_v1.json"
    )
    assert policy["r4d2c_provenance"] == {
        "aggregate_sha256": (
            "8251c5f1eeaa89a0a09534be4dc3b4025617e8d65d5cbf6b8045f644e4e9112e"
        ),
        "binding": "PROVENANCE_ONLY_NOT_CLASSIFIER_INPUT",
        "observation_sha256": (
            "ce4354611ba16688137db57bdd5bc1e49ffa1b5ee2512560fd0db83d841963a0"
        ),
    }
    classifier = inspect.getsource(
        stages._validate_source_signal_quality_disposition_row
    ).casefold()
    for forbidden in (
        "dynamic_decode_mask_collapse",
        "overlap_empty",
        "r4d2c",
        policy["r4d2c_provenance"]["aggregate_sha256"],
        policy["r4d2c_provenance"]["observation_sha256"],
    ):
        assert str(forbidden).casefold() not in classifier


def test_cases_03_to_05_study_coverage_is_current_and_per_affected_study() -> None:
    same_study = [_successful_row("same-success"), _disposed_row("same-failure")]
    assert _validate(same_study)["n_studies_affected_by_technical_disposition"] == 1

    two_studies = [
        _successful_row("a-success", study_id="200001"),
        _disposed_row("a-failure", study_id="200001"),
        _successful_row("b-success", study_id="200002"),
        _disposed_row("b-failure", study_id="200002"),
    ]
    assert _validate(two_studies)["n_studies_affected_by_technical_disposition"] == 2

    no_coverage = [_successful_row("other", study_id="200002"), _disposed_row("lost")]
    _expect_code(
        "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
        lambda: _validate(no_coverage),
    )


def test_cases_06_to_08_decode_color_and_output_failures_remain_blocking() -> None:
    for substage in (
        "DECODE_OR_COLOR_CONVERSION_FAILURE",
        "OUTPUT_WRITE_FAILURE",
        "SPATIAL_CROP_RESIZE_FAILURE",
        "TEMPORAL_SAMPLING_FAILURE",
    ):
        rows = [_successful_row(f"success-{substage}"), _disposed_row(substage)]
        rows[1]["failure_substage"] = substage
        _expect_code(f"EXTRACTION_{substage}", lambda rows=rows: _validate(rows))


def test_case_09_source_and_downstream_metric_authority_is_exact() -> None:
    valid = [_successful_row("metric-success"), _disposed_row("metric-failure")]
    assert _validate(valid)["n_object_technical_dispositions"] == 1

    partial = copy.deepcopy(valid)
    partial[1]["source_sector_pixel_count"] = None
    partial[1]["source_sector_nonempty_gate_passed"] = False
    _expect_code(
        "EXTRACTION_TECHNICAL_DISPOSITION_SOURCE_METRICS_INVALID",
        lambda: _validate(partial),
    )

    downstream = copy.deepcopy(valid)
    downstream[1]["post_crop_nonzero_retained_pixel_count"] = 0
    _expect_code(
        "EXTRACTION_TECHNICAL_DISPOSITION_DOWNSTREAM_METRICS_INVALID",
        lambda: _validate(downstream),
    )

    all_not_evaluated = copy.deepcopy(valid)
    for count_field, gate_field in stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS:
        all_not_evaluated[1][count_field] = None
        all_not_evaluated[1][gate_field] = False
    _expect_code(
        "EXTRACTION_TECHNICAL_DISPOSITION_SOURCE_METRICS_INVALID",
        lambda: _validate(all_not_evaluated),
    )


def test_cases_10_and_12_artifact_partition_requires_success_npz_and_disposed_absence() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        success = _successful_row("partition-success")
        disposed = _disposed_row("partition-disposed")
        success_path = root / str(success["output_relative_path"])
        success_path.parent.mkdir(parents=True)
        payload = b"canonical-success-npz"
        _write_private(success_path, payload)
        success["npz_sha256"] = hashlib.sha256(payload).hexdigest()
        stages.validate_extraction_artifact_partition([success, disposed], clips_root=root)

        success_path.unlink()
        _expect_code(
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID",
            lambda: stages.validate_extraction_artifact_partition(
                [success, disposed], clips_root=root
            ),
        )
        _write_private(success_path, payload)
        disposed_path = root / str(disposed["output_relative_path"])
        disposed_path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(disposed_path, b"must-not-exist")
        _expect_code(
            "EXTRACTION_TECHNICAL_DISPOSITION_NPZ_PRESENT",
            lambda: stages.validate_extraction_artifact_partition(
                [success, disposed], clips_root=root
            ),
        )


def test_case_11_both_embedding_final_and_partial_outputs_must_be_absent() -> None:
    rows = [_successful_row("embedding-success"), _disposed_row("embedding-failure")]
    disposed = rows[1]
    dicom_rows = [
        {
            "subject_id": disposed["subject_id"],
            "study_id": disposed["study_id"],
            "source_relative_path": disposed["source_relative_path"],
            "read_ok": True,
            "is_multiframe": True,
            "pixel_decode_ok": True,
            "number_of_frames": 40,
            "photometric_interpretation": disposed["photometric_interpretation"],
            "transfer_syntax_uid": disposed["transfer_syntax_uid"],
        }
    ]
    manifest = stages.technical_disposition_manifest_rows(rows, context=_context(rows))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        clips = root / "cache"
        clips.mkdir()
        final = root / "echoprime"
        for present in (final, final.with_name("echoprime.partial")):
            present.mkdir()
            _expect_code(
                "EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID",
                lambda: stages.context_from_completed_extraction_authority(
                    extraction_rows=rows,
                    dicom_rows=dicom_rows,
                    manifest_rows=manifest,
                    clips_root=clips,
                    embedding_output_root=final,
                ),
            )
            present.rmdir()
        context = stages.context_from_completed_extraction_authority(
            extraction_rows=rows,
            dicom_rows=dicom_rows,
            manifest_rows=manifest,
            clips_root=clips,
            embedding_output_root=final,
        )
        assert context.embedding_absent_clip_keys == frozenset(
            {str(disposed["clip_key"])}
        )


def test_cases_13_to_15_no_silent_removal_substitution_or_duplicate_identity() -> None:
    row = _successful_row("identity")
    _expect_code(
        "EXTRACTION_CINE_COUNT_MISMATCH",
        lambda: stages.validate_production_extraction_rows([row], expected_cines=2),
    )

    wrong_clip = copy.deepcopy(row)
    wrong_clip["clip_key"] = "f" * 64
    _expect_code(
        "EXTRACTION_CLIP_KEY_DERIVATION_MISMATCH",
        lambda: stages.validate_extraction_artifact_partition(
            [wrong_clip], clips_root=Path("unused")
        ),
    )

    duplicate = copy.deepcopy(row)
    _expect_code(
        "DUPLICATE_PHYSICAL_SOURCE",
        lambda: stages.validate_production_extraction_rows(
            [row, duplicate], expected_cines=2
        ),
    )


def test_contextual_authority_mutations_fail_closed_before_disposition() -> None:
    def fixture(root: Path) -> dict[str, Any]:
        success = _successful_row("authority-success")
        disposed = _disposed_row("authority-disposed")
        raw_payload = b"disposed-raw-A"
        disposed["source_sha256"] = hashlib.sha256(raw_payload).hexdigest()
        rows = [success, disposed]
        authority_relative_paths = [
            _source_relative("authority-success"),
            _source_relative("authority-disposed"),
        ]
        planned_batch = {
            "n_objects": len(rows),
            "objects": [
                {
                    "subject_id": row["subject_id"],
                    "study_id": row["study_id"],
                    "source_relative_path": authority_relative,
                    "source_object_key": row["physical_source_key"],
                }
                for row, authority_relative in zip(
                    rows, authority_relative_paths, strict=True
                )
            ],
        }
        downloads = [
            {
                "subject_id": row["subject_id"],
                "study_id": row["study_id"],
                "physical_source_key": row["physical_source_key"],
                "source_relative_path": row["source_relative_path"],
                "source_authority_relative_path": authority_relative,
                "observed_sha256": row["source_sha256"],
                "download_ok": True,
            }
            for row, authority_relative in zip(
                rows, authority_relative_paths, strict=True
            )
        ]
        dicom_rows = [
            {
                "subject_id": row["subject_id"],
                "study_id": row["study_id"],
                "source_relative_path": row["source_relative_path"],
                "read_ok": True,
                "is_multiframe": True,
                "pixel_decode_ok": True,
                "number_of_frames": row["source_num_frames"],
                "photometric_interpretation": row[
                    "photometric_interpretation"
                ],
                "transfer_syntax_uid": row["transfer_syntax_uid"],
            }
            for row in rows
        ]
        download_root = root / "raw"
        clips_root = root / "clips"
        clips_root.mkdir(parents=True)
        raw_path = download_root / str(disposed["source_relative_path"])
        raw_path.parent.mkdir(parents=True)
        _write_private(raw_path, raw_payload)
        raw_sha, raw_identity = stages._stable_nofollow_sha256_authority(
            raw_path
        )
        source_key = str(disposed["physical_source_key"])
        return {
            "rows": rows,
            "planned_batch": planned_batch,
            "downloads": downloads,
            "dicom_rows": dicom_rows,
            "download_root": download_root,
            "clips_root": clips_root,
            "embedding_root": root / "echoprime",
            "raw_path": raw_path,
            "raw_source_key": source_key,
            "raw_authority": {source_key: (raw_sha, raw_identity)},
        }

    def validate(value: Mapping[str, Any]) -> dict[str, Any]:
        context = stages.build_extraction_disposition_context(
            extraction_rows=value["rows"],
            planned_batch=value["planned_batch"],
            verified_download_rows=value["downloads"],
            dicom_rows=value["dicom_rows"],
            download_root=value["download_root"],
            clips_root=value["clips_root"],
            raw_authority_by_source=value["raw_authority"],
            embedding_output_root=value["embedding_root"],
        )
        return stages.validate_production_extraction_rows(
            value["rows"],
            expected_cines=len(value["rows"]),
            disposition_context=context,
        )

    def alter_raw_identity(value: dict[str, Any]) -> None:
        source_key = value["raw_source_key"]
        digest, identity = value["raw_authority"][source_key]
        value["raw_authority"][source_key] = (
            digest,
            (*identity[:-1], identity[-1] + 1),
        )

    def rewrite_raw_content(value: dict[str, Any]) -> None:
        raw_path = value["raw_path"]
        raw_path.write_bytes(b"disposed-raw-B")
        metadata = raw_path.stat(follow_symlinks=False)
        os.utime(
            raw_path,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
        )

    def alter_local_locator(value: dict[str, Any]) -> None:
        noncanonical = "noncanonical-local-name.dcm"
        value["rows"][1]["source_relative_path"] = noncanonical
        value["downloads"][1]["source_relative_path"] = noncanonical
        value["dicom_rows"][1]["source_relative_path"] = noncanonical

    cases: tuple[
        tuple[str, str, Callable[[dict[str, Any]], None]], ...
    ] = (
        (
            "planned_membership",
            "EXTRACTION_TECHNICAL_DISPOSITION_PLAN_DOWNLOAD_AUTHORITY_INVALID",
            lambda value: value["planned_batch"]["objects"][1].update(
                source_object_key="f" * 64
            ),
        ),
        (
            "source_membership",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["rows"][1].update(physical_source_key="e" * 64),
        ),
        (
            "noncanonical_local_locator",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            alter_local_locator,
        ),
        (
            "download_observed_sha",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["downloads"][1].update(observed_sha256="0" * 64),
        ),
        (
            "extraction_source_sha",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["rows"][1].update(source_sha256="1" * 64),
        ),
        (
            "photometric",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["dicom_rows"][1].update(
                photometric_interpretation="MONOCHROME1"
            ),
        ),
        (
            "transfer_syntax",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["dicom_rows"][1].update(
                transfer_syntax_uid="1.2.840.10008.1.2"
            ),
        ),
        (
            "extraction_source_num_frames",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["rows"][1].update(source_num_frames=41),
        ),
        (
            "dicom_number_of_frames",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["dicom_rows"][1].update(number_of_frames=41),
        ),
        (
            "dicom_frame_authority",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            lambda value: value["dicom_rows"][1].update(number_of_frames=1),
        ),
        (
            "raw_authority_digest",
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID",
            lambda value: value["raw_authority"].update(
                {
                    value["raw_source_key"]: (
                        "2" * 64,
                        value["raw_authority"][value["raw_source_key"]][1],
                    )
                }
            ),
        ),
        (
            "raw_stable_identity",
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID",
            alter_raw_identity,
        ),
        (
            "raw_same_size_content_rewrite",
            "EXTRACTION_TECHNICAL_DISPOSITION_RAW_RETENTION_INVALID",
            rewrite_raw_content,
        ),
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        baseline = fixture(root / "baseline")
        with mock.patch.object(
            stages,
            "_stable_nofollow_sha256_authority",
            side_effect=AssertionError("retained DICOM body was rehashed"),
        ):
            assert validate(baseline)["n_object_technical_dispositions"] == 1
        for label, expected, mutate in cases:
            value = fixture(root / label)
            mutate(value)
            _expect_code(expected, lambda value=value: validate(value))


def test_cases_16_and_17_partition_equations_and_manifest_partition_are_exact() -> None:
    rows, context, manifest = _manifest_fixture()
    summary = _validate(rows)
    manifest_summary = stages.validate_technical_disposition_manifest_rows(
        rows, manifest, context=context
    )
    assert summary["n_requested_cines"] == (
        summary["n_successfully_extracted_cines"]
        + summary["n_object_technical_dispositions"]
    )
    assert manifest_summary["n_object_technical_dispositions"] == 1

    overlap = copy.deepcopy(manifest)
    overlap[0]["clip_key"] = rows[0]["clip_key"]
    _expect_code(
        "TECHNICAL_DISPOSITION_MANIFEST_RECONCILIATION_MISMATCH",
        lambda: stages.validate_technical_disposition_manifest_rows(
            rows, overlap, context=context
        ),
    )
    receipt = _receipt(current=True)
    receipt["n_clip_embeddings"] = 0
    _expect_code(
        "CLIP_ACCOUNTING_MISMATCH",
        lambda: finalizer._validate_current_receipt_v3(receipt),
    )


def test_case_18_pooling_is_float64_mean_to_exact_float32_and_disposition_free() -> None:
    import numpy as np

    clips = np.empty((2, 512), dtype=np.float32)
    clips[0] = np.float32(0.1)
    clips[1] = np.float32(0.2)
    pooled = preservation.mean_pool_study_embeddings(
        clip_embeddings=clips,
        clip_rows=[
            {"study_id": "200001", "embedding_idx": "0"},
            {"study_id": "200001", "embedding_idx": "1"},
        ],
        study_rows=[{"study_id": "200001", "study_idx": "0", "n_clips": "2"}],
    )
    expected = clips.astype(np.float64).mean(axis=0).astype(np.float32)
    assert pooled.dtype == np.float32
    assert np.array_equal(pooled[0], expected)
    assert pooled.shape == (1, 512)


def test_case_19_technical_manifest_sha_is_bound_to_both_stage_summaries() -> None:
    rows, context, manifest = _manifest_fixture()
    extraction = _validate(rows)
    technical = stages.validate_technical_disposition_manifest_rows(
        rows, manifest, context=context
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "technical.csv"
        stages.write_technical_disposition_manifest_no_clobber(path, manifest)
        digest = stages.technical_disposition_manifest_sha256(path)
        common = {
            "n_object_technical_dispositions": 1,
            "n_studies_affected_by_technical_disposition": 1,
            "technical_disposition_counts_by_class": {DISPOSITION: 1},
            "technical_disposition_policy_version": (
                stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_sha256": digest,
            "all_extraction_rows_resolved": True,
            "all_technical_dispositions_retained": True,
            "object_substitution_count": 0,
            "unaccounted_multiframe_objects": 0,
            "n_new_no_cine_studies": 0,
        }
        dicom_summary = {key: None for key in stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2}
        dicom_summary.update(common)
        dicom_summary.update(
            {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v2",
                "n_successfully_extracted_cines": 1,
                "successful_extraction_gate_scope": (
                    "SUCCESSFUL_EXTRACTIONS_ONLY"
                ),
                "requested_cine_resolution_scope": (
                    "SUCCESSFUL_EXTRACTION_OR_APPROVED_OBJECT_TECHNICAL_DISPOSITION"
                ),
                "identifiers_emitted": False,
                "paths_emitted": False,
            }
        )
        for key in preservation.DICOM_RECOMPUTED_SUMMARY_KEYS:
            if key in extraction:
                dicom_summary[key] = extraction[key]
        embedding_summary = {key: None for key in stages.ECHOPRIME_SUMMARY_KEYS_V2}
        embedding_summary.update(common)
        embedding_summary.update(
            {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_echoprime_pooling_summary_v2",
                "all_successful_extractions_embedded": True,
                "n_clip_embeddings": 1,
                "encoder_only": True,
                "view_classifier_used": False,
                "pooling": "stable_clip_key_order_float64_mean_then_float32",
                "checkpoint_sha256": stages.CHECKPOINT_SHA256,
                "identifiers_emitted": False,
                "paths_emitted": False,
            }
        )
        preservation.validate_technical_disposition_summary_bindings(
            dicom_summary=dicom_summary,
            embedding_summary=embedding_summary,
            extraction_semantics=extraction,
            technical_semantics=technical,
            technical_manifest_path=path,
        )
        for target, key in (
            (dicom_summary, "technical_disposition_manifest_sha256"),
            (embedding_summary, "technical_disposition_manifest_sha256"),
            (embedding_summary, "technical_disposition_counts_by_class"),
        ):
            changed = copy.deepcopy(target)
            changed[key] = "0" * 64 if key.endswith("sha256") else ZERO_DISPOSITION_MAP
            kwargs = {
                "dicom_summary": changed if target is dicom_summary else dicom_summary,
                "embedding_summary": (
                    changed if target is embedding_summary else embedding_summary
                ),
                "extraction_semantics": extraction,
                "technical_semantics": technical,
                "technical_manifest_path": path,
            }
            _expect_code(
                "TECHNICAL_DISPOSITION_STAGE_SUMMARY_BINDING_MISMATCH",
                lambda kwargs=kwargs: preservation.validate_technical_disposition_summary_bindings(
                    **kwargs
                ),
            )


def test_cases_19_and_20_preservation_retains_metadata_and_retirement_only_deletes_clips() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        raw_root = root / "raw"
        extraction_root = root / "dicom_extraction"
        clips_root = extraction_root / "clips"
        raw_root.mkdir()
        clips_root.mkdir(parents=True)
        raw = raw_root / "object.dcm"
        clip = clips_root / "clip.npz"
        technical = extraction_root / "technical_disposition_manifest.restricted.csv"
        _write_private(raw, b"retained raw authority")
        _write_private(clip, b"retirable extracted cache")
        stages.write_technical_disposition_manifest_no_clobber(technical, manifest)
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        technical_sha = stages.technical_disposition_manifest_sha256(technical)

        walked = preservation._walk_regular(extraction_root)
        assert technical in walked and clip in walked
        assert not technical.is_relative_to(clips_root)
        preserve_source = inspect.getsource(preservation.preserve_batch)
        assert '"dicom_extraction_metadata_retained"' in preserve_source
        assert '"extracted_npz_cache_owner_retirable"' in preserve_source

        retirement._delete_cache_tree(clips_root)
        assert not clips_root.exists()
        assert raw.is_file() and hashlib.sha256(raw.read_bytes()).hexdigest() == raw_sha
        assert technical.is_file()
        assert stages.technical_disposition_manifest_sha256(technical) == technical_sha
        retirement_source = inspect.getsource(retirement.main)
        assert "_delete_cache_tree(staging)" in retirement_source
        assert "_delete_cache_tree(raw_root)" not in retirement_source
        assert "RETAINED_EXTRACTION_METADATA_CHANGED" in retirement_source


def test_case_21_finalizer_aggregates_counts_and_exact_manifest_set_hash() -> None:
    first = _receipt(current=True)
    second = _receipt(current=True)
    second.update(
        {
            "batch_id": "c3_batch_001",
            "n_expected_objects": 2,
            "expected_source_bytes": 2,
            "n_download_verified": 2,
            "n_dicom_readable": 2,
            "n_multiframe_cines": 2,
            "n_successfully_extracted_cines": 1,
            "n_object_technical_dispositions": 1,
            "n_studies_affected_by_technical_disposition": 1,
            "technical_disposition_counts_by_class": {DISPOSITION: 1},
            "technical_disposition_manifest_sha256": "e" * 64,
        }
    )
    finalizer._validate_current_receipt_v3(first)
    finalizer._validate_current_receipt_v3(second)
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=2,
        selected_subjects=2,
        normalized_source_objects=3,
        selected_source_bytes=3,
        batch_count=2,
        studies_per_full_batch=1,
        final_batch_studies=1,
        contract_id="r5a_two_receipt_aggregate_v1",
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        paths: list[Path] = []
        for ordinal, value in enumerate((first, second)):
            path = root / f"receipt_{ordinal}.json"
            _write_private(
                path,
                (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"),
            )
            paths.append(path)
        summary = finalizer.finalize_receipts(
            paths,
            expected_governing_commit="a" * 40,
            requirements=requirements,
            expected_no_cine_studies=0,
        )
    assert summary["production_batches"] == 2
    assert summary["verified_source_objects"] == 3
    assert summary["successfully_extracted_cines"] == 2
    assert summary["object_technical_dispositions"] == 1
    assert summary["studies_affected_by_technical_disposition"] == 1
    assert summary["technical_disposition_counts_by_class"] == {DISPOSITION: 1}
    expected_set_sha = hashlib.sha256(
        ("d" * 64 + "\n" + "e" * 64 + "\n").encode("ascii")
    ).hexdigest()
    assert summary["technical_disposition_manifest_set_sha256"] == expected_set_sha
    finalizer.validate_closed_final_summary(summary)


def test_case_21_closed_final_summary_enforces_v2_schema_equations_and_zero_map() -> None:
    summary = _reconciled_final_summary()
    finalizer.validate_closed_final_summary(summary)
    mutations = (
        ("all_extractions_passed", True),
        ("technical_disposition_counts_by_class", {}),
        ("selected_studies", True),
        ("selected_studies", -1),
        ("pooled_imaging_eligible_studies", 0),
        ("no_cine_disposition", "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"),
        ("successfully_extracted_cines", 0),
    )
    for key, replacement in mutations:
        changed = copy.deepcopy(summary)
        changed[key] = replacement
        _expect_failure(lambda changed=changed: finalizer.validate_closed_final_summary(changed))


def test_case_22_zero_disposition_never_adds_vectors_or_array_rows() -> None:
    import numpy as np

    clips = np.ones((1, 512), dtype=np.float32)
    pooled = preservation.mean_pool_study_embeddings(
        clip_embeddings=clips,
        clip_rows=[{"study_id": "200001", "embedding_idx": "0"}],
        study_rows=[{"study_id": "200001", "study_idx": "0", "n_clips": "1"}],
    )
    assert pooled.shape == (1, 512)
    _expect_code(
        "STUDY_POOLING_STUDY_ROW_INVALID",
        lambda: preservation.mean_pool_study_embeddings(
            clip_embeddings=clips,
            clip_rows=[{"study_id": "200001", "embedding_idx": "0"}],
            study_rows=[
                {"study_id": "200001", "study_idx": "0", "n_clips": "1"},
                {"study_id": "200002", "study_idx": "1", "n_clips": "1"},
            ],
        ),
    )


def test_case_23_safe_export_allows_only_closed_identifier_free_final_summary() -> None:
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
    )
    summary = _reconciled_final_summary()
    payload = (json.dumps(summary, sort_keys=True) + "\n").encode("utf-8")
    result = analysis_modes.validate_candidate_bytes(
        payload,
        filename="lvef_c3_production_finalization.summary.json",
        profile_name="lvef_c3_production_finalization_summary_json",
        policy=policy,
    )
    assert result["status"] == "PASS"
    assert "all_extractions_passed" not in summary
    for key, value in (
        ("study_id", "200001"),
        ("source_relative_path", "synthetic_authority/objects/x.dcm"),
    ):
        changed = {**summary, key: value}
        _expect_failure(
            lambda changed=changed: analysis_modes.validate_candidate_bytes(
                (json.dumps(changed, sort_keys=True) + "\n").encode("utf-8"),
                filename="lvef_c3_production_finalization.summary.json",
                profile_name="lvef_c3_production_finalization_summary_json",
                policy=policy,
            )
        )


def test_case_24_policy_and_classifier_inputs_are_closed_and_outcome_free() -> None:
    policy = core.load_strict_json(
        ROOT / "configs" / "lvef_c3_source_signal_object_technical_disposition_v1.json"
    )
    core.validate_object_technical_disposition_policy(policy)
    prohibited = {
        "endpoint_label",
        "outcome",
        "target_value",
        "split_role",
        "model_prediction",
        "performance",
        "r4d2c_replay_class",
        "r4d2c_mechanism",
    }
    lowered_fields = {field.casefold() for field in stages.EXTRACTION_PRODUCER_FIELDS}
    assert prohibited.isdisjoint(lowered_fields)
    assert tuple(policy["prohibited_decision_inputs"]) == (
        "R4D2C_REPLAY_CLASS",
        "R4D2C_MECHANISM",
        "ENDPOINT_LABELS",
        "OUTCOMES",
        "TARGET_VALUES",
        "SPLIT_ROLES",
        "MODEL_PREDICTIONS",
        "VALIDATION_TEST_PERFORMANCE",
    )
    changed = copy.deepcopy(policy)
    changed["eligible_failure_substages"].append("OUTPUT_WRITE_FAILURE")
    _expect_code(
        "OBJECT_TECHNICAL_DISPOSITION_POLICY_INVALID",
        lambda: core.validate_object_technical_disposition_policy(changed),
    )


def test_exact_extraction_and_stage_summary_schema_registries_reject_drift() -> None:
    row = _successful_row("schema")
    for changed in (
        {key: value for key, value in row.items() if key != "source_sha256"},
        {**row, "outcome": "forbidden"},
    ):
        _expect_code(
            "EXTRACTION_ROW_SCHEMA_MISMATCH",
            lambda changed=changed: stages.validate_production_extraction_rows(
                [changed], expected_cines=1
            ),
        )
    assert "outcome" not in stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
    assert "all_extractions_passed" not in stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
    assert "outcome" not in stages.ECHOPRIME_SUMMARY_KEYS_V2
    assert "all_extractions_passed" not in stages.ECHOPRIME_SUMMARY_KEYS_V2
    assert {
        "technical_disposition_manifest_sha256",
        "technical_disposition_counts_by_class",
        "n_object_technical_dispositions",
        "n_studies_affected_by_technical_disposition",
    }.issubset(stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2)
    assert {
        "technical_disposition_manifest_sha256",
        "technical_disposition_counts_by_class",
        "n_object_technical_dispositions",
        "n_studies_affected_by_technical_disposition",
        "all_successful_extractions_embedded",
    }.issubset(stages.ECHOPRIME_SUMMARY_KEYS_V2)


def test_blocking_classification_publishes_failure_summary_before_any_manifest() -> None:
    source = inspect.getsource(stages.run_production_dicom_extraction)
    classification_start = source.index("disposition_context =")
    classification_failure = source.index(
        '"artifact_type": "lvef_c3_batch_extraction_failure_summary_v2"',
        classification_start,
    )
    classification_raise = source.index("        raise\n", classification_failure)
    manifest_publish = source.index(
        "write_technical_disposition_manifest_no_clobber(", classification_raise
    )
    assert classification_start < classification_failure < classification_raise
    assert classification_raise < manifest_publish
    prepublication = source[classification_start:manifest_publish]
    assert 'partial / "failure.summary.json"' in prepublication
    assert "write_technical_disposition_manifest_no_clobber" not in prepublication


def test_manifest_header_only_zero_case_has_stable_sha_and_exact_header() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "technical.csv"
        stages.write_technical_disposition_manifest_no_clobber(path, [])
        rows, digest = stages.read_technical_disposition_manifest_authority(path)
        expected = (",".join(stages.TECHNICAL_DISPOSITION_MANIFEST_HEADER) + "\n").encode()
        assert rows == []
        assert path.read_bytes() == expected
        assert digest == hashlib.sha256(expected).hexdigest()
        metadata = path.stat(follow_symlinks=False)
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1


def test_manifest_round_trip_is_exact_stable_and_exclusive() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "technical.csv"
        stages.write_technical_disposition_manifest_no_clobber(path, manifest)
        first_rows, first_sha = stages.read_technical_disposition_manifest_authority(path)
        second_rows, second_sha = stages.read_technical_disposition_manifest_authority(path)
        expected_rows = [{key: str(row[key]) for key in stages.TECHNICAL_DISPOSITION_MANIFEST_HEADER} for row in manifest]
        assert first_rows == second_rows == expected_rows
        assert first_sha == second_sha == hashlib.sha256(path.read_bytes()).hexdigest()
        before = path.read_bytes()
        _expect_code(
            "TECHNICAL_DISPOSITION_MANIFEST_NO_CLOBBER_FAILED",
            lambda: stages.write_technical_disposition_manifest_no_clobber(path, manifest),
        )
        assert path.read_bytes() == before


def test_manifest_writer_rejects_symlink_broken_symlink_hardlink_and_fifo_collisions() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        target = root / "target"
        _write_private(target, b"unchanged")
        cases: list[Path] = []
        symlink = root / "symlink.csv"
        symlink.symlink_to(target)
        cases.append(symlink)
        broken = root / "broken.csv"
        broken.symlink_to(root / "absent")
        cases.append(broken)
        hardlink = root / "hardlink.csv"
        os.link(target, hardlink)
        cases.append(hardlink)
        fifo = root / "fifo.csv"
        os.mkfifo(fifo, 0o600)
        cases.append(fifo)
        for path in cases:
            _expect_code(
                "TECHNICAL_DISPOSITION_MANIFEST_NO_CLOBBER_FAILED",
                lambda path=path: stages.write_technical_disposition_manifest_no_clobber(
                    path, manifest
                ),
            )
        assert target.read_bytes() == b"unchanged"


def test_manifest_reader_rejects_symlink_hardlink_fifo_and_mode_drift() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        good = root / "good.csv"
        stages.write_technical_disposition_manifest_no_clobber(good, manifest)
        symlink = root / "symlink.csv"
        symlink.symlink_to(good)
        _expect_failure(lambda: stages.read_technical_disposition_manifest_authority(symlink))
        hardlink = root / "hardlink.csv"
        os.link(good, hardlink)
        _expect_code(
            "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID",
            lambda: stages.read_technical_disposition_manifest_authority(good),
        )
        hardlink.unlink()
        fifo = root / "fifo.csv"
        os.mkfifo(fifo, 0o600)
        _expect_code(
            "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID",
            lambda: stages.read_technical_disposition_manifest_authority(fifo),
        )
        os.chmod(good, 0o640)
        _expect_code(
            "TECHNICAL_DISPOSITION_MANIFEST_FILE_AUTHORITY_INVALID",
            lambda: stages.read_technical_disposition_manifest_authority(good),
        )


def test_manifest_writer_collision_race_preserves_competitor_and_removes_temp() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        path = root / "technical.csv"
        real_link = stages.os.link

        def competing_link(src: str, dst: str, **kwargs: Any) -> None:
            competitor = root / dst
            _write_private(competitor, b"competitor")
            real_link(src, dst, **kwargs)

        with mock.patch.object(stages.os, "link", side_effect=competing_link):
            _expect_code(
                "TECHNICAL_DISPOSITION_MANIFEST_NO_CLOBBER_FAILED",
                lambda: stages.write_technical_disposition_manifest_no_clobber(path, manifest),
            )
        assert path.read_bytes() == b"competitor"
        assert [item.name for item in root.iterdir()] == ["technical.csv"]


def test_manifest_reader_reopen_race_is_detected() -> None:
    _, _, manifest = _manifest_fixture()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        path = root / "technical.csv"
        stages.write_technical_disposition_manifest_no_clobber(path, manifest)
        original = path.read_bytes()
        real_open = stages.os.open
        leaf_opens = 0

        def racing_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            nonlocal leaf_opens
            if target == path.name and kwargs.get("dir_fd") is not None:
                leaf_opens += 1
                if leaf_opens == 2:
                    path.unlink()
                    _write_private(path, original)
            return real_open(target, flags, *args, **kwargs)

        with mock.patch.object(stages.os, "open", side_effect=racing_open):
            _expect_code(
                "TECHNICAL_DISPOSITION_MANIFEST_IDENTITY_CHANGED",
                lambda: stages.read_technical_disposition_manifest_authority(path),
            )


def test_capacity_overlay_exact_boundaries_fail_closed() -> None:
    authority = _capacity_authority()
    assert set(authority) == sequential.SUCCESSOR_CAPACITY_KEYS
    sequential.validate_successor_capacity_authority(authority)
    mutations: list[dict[str, Any]] = []
    for key, value in (
        ("status", "PASS"),
        ("successor_increment_bytes", sequential.SUCCESSOR_INCREMENT_BYTES - 1),
        ("quota_remaining_after_successor_bytes", sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES - 1),
        ("physical_remaining_after_successor_bytes", sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES - 1),
        ("research_file_slots_remaining", sequential.SUCCESSOR_REQUIRED_FILE_SLOTS - 1),
        ("active_extraction_caches", 1),
        ("preserved_terminal_failed_extraction_caches", 1),
        ("live_research_usage_bytes", True),
    ):
        changed = copy.deepcopy(authority)
        changed[key] = value
        mutations.append(changed)
    extra = {**authority, "unexpected": 0}
    _expect_code(
        "FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_SCHEMA_INVALID",
        lambda: sequential.validate_successor_capacity_authority(extra),
    )
    for changed in mutations:
        _expect_code(
            "FULL_SEQUENTIAL_SUCCESSOR_CAPACITY_INVALID",
            lambda changed=changed: sequential.validate_successor_capacity_authority(
                changed
            ),
        )


def test_plan_v3_no_cine_identity_is_exact_and_legacy_v2_is_historical_only() -> None:
    plan, requirements = _plan_fixture(expected_no_cine=(0,))
    assert plan["schema_version"] == 3
    assert plan["artifact_type"] == "lvef_c3_restricted_immutable_batch_plan_v3"
    core.validate_current_batch_plan_v3(plan, requirements=requirements)
    first_batch = plan["batches"][0]
    assert first_batch["expected_no_cine_studies"] == 1
    assert first_batch["prespecified_no_cine_study_set_sha256"] == (
        core.canonical_json_sha256(first_batch["prespecified_no_cine_study_keys"])
    )

    swapped = copy.deepcopy(plan)
    replacement = swapped["batches"][0]["studies"][1]
    swapped["batches"][0]["prespecified_no_cine_study_keys"] = [
        {
            "subject_id": replacement["subject_id"],
            "study_id": replacement["study_id"],
        }
    ]
    _expect_code(
        "BATCH_NO_CINE_AUTHORITY_INVALID",
        lambda: core.validate_current_batch_plan_v3(swapped, requirements=requirements),
    )

    legacy = copy.deepcopy(plan)
    legacy["schema_version"] = 2
    legacy["artifact_type"] = "lvef_c3_restricted_immutable_batch_plan_v2"
    legacy["cohort"].pop("expected_no_cine_studies")
    legacy["cohort"].pop("prespecified_no_cine_study_set_sha256")
    for batch in legacy["batches"]:
        batch.pop("expected_no_cine_studies")
        batch.pop("prespecified_no_cine_study_keys")
        batch.pop("prespecified_no_cine_study_set_sha256")
    core.validate_batch_plan(legacy, requirements=requirements)
    _expect_code(
        "BATCH_PLAN_CURRENT_VERSION_REQUIRED",
        lambda: core.validate_current_batch_plan_v3(legacy, requirements=requirements),
    )
    mixed = copy.deepcopy(legacy)
    mixed["cohort"]["expected_no_cine_studies"] = 0
    _expect_code(
        "BATCH_PLAN_COHORT_CONSTANT_MISMATCH",
        lambda: core.validate_batch_plan(mixed, requirements=requirements),
    )


def test_receipt_v2_v3_boundary_is_call_site_specific_and_schemas_never_mix() -> None:
    legacy = _receipt(current=False)
    current = _receipt(current=True)
    finalizer._validate_receipt(legacy)
    finalizer._validate_receipt(current)
    finalizer._validate_current_receipt_v3(current)
    _expect_code(
        "BATCH_RECEIPT_SCHEMA_MISMATCH",
        lambda: finalizer._validate_current_receipt_v3(legacy),
    )
    legacy_plus = {**legacy, "n_object_technical_dispositions": 0}
    _expect_code(
        "BATCH_RECEIPT_SCHEMA_MISMATCH",
        lambda: finalizer._validate_receipt(legacy_plus),
    )
    zero_map_missing = copy.deepcopy(current)
    zero_map_missing["technical_disposition_counts_by_class"] = {}
    _expect_code(
        "TECHNICAL_DISPOSITION_COUNT_MAP_INVALID",
        lambda: finalizer._validate_current_receipt_v3(zero_map_missing),
    )


def test_canary_accepts_exact_legacy_or_current_schema_and_rejects_union() -> None:
    legacy = _receipt(current=False, canary=True)
    current = _receipt(current=True, canary=True)
    finalizer._validate_canary_eligibility_receipt(legacy)
    finalizer._validate_canary_eligibility_receipt(current)
    mixed = {**legacy, "technical_disposition_counts_by_class": ZERO_DISPOSITION_MAP}
    _expect_code(
        "CANARY_RECEIPT_SCHEMA_MISMATCH",
        lambda: finalizer._validate_canary_eligibility_receipt(mixed),
    )
    changed = copy.deepcopy(current)
    changed["technical_disposition_counts_by_class"] = {}
    _expect_code(
        "CANARY_CLIP_ACCOUNTING_MISMATCH",
        lambda: finalizer._validate_canary_eligibility_receipt(changed),
    )


def test_no_cine_preservation_rejects_same_count_identity_swap() -> None:
    plan, _ = _plan_fixture(expected_no_cine=(0,))
    batch = plan["batches"][0]
    expected = batch["prespecified_no_cine_study_keys"][0]
    wrong = batch["studies"][1]
    embedding = {
        "n_no_cine_studies": 1,
        "n_new_no_cine_studies": 0,
        "prespecified_no_cine_study_set_sha256": batch[
            "prespecified_no_cine_study_set_sha256"
        ],
        "actual_no_cine_study_set_sha256": core.canonical_json_sha256([expected]),
        "all_no_cine_studies_prespecified": True,
    }
    valid_rows = [{**expected, "disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"}]
    result = preservation.validate_prespecified_no_cine_authority(
        disposition_rows=valid_rows,
        planned_batch=batch,
        embedding_summary=embedding,
    )
    assert result["n_no_cine_studies"] == 1
    swapped_rows = [{**wrong, "disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"}]
    _expect_code(
        "PRESPECIFIED_NO_CINE_IDENTITY_MISMATCH",
        lambda: preservation.validate_prespecified_no_cine_authority(
            disposition_rows=swapped_rows,
            planned_batch=batch,
            embedding_summary=embedding,
        ),
    )


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    skipped = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        if inspect.signature(function).parameters:
            print(f"SKIP {name}: requires a fixture")
            skipped += 1
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
