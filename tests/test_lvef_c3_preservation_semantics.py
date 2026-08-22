from __future__ import annotations

import copy
import csv
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement


def expect_code(code: str, function) -> None:
    try:
        function()
    except preservation.BatchPreservationError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected {code}")


def capture_preservation_error(function) -> preservation.BatchPreservationError:
    try:
        function()
    except preservation.BatchPreservationError as exc:
        return exc
    raise AssertionError("Expected a preservation failure")


def expect_retirement_code(code: str, function) -> None:
    try:
        function()
    except retirement.CacheRetirementError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected {code}")


def _valid_inputs():
    planned = [
        {"subject_id": "1", "study_id": "10"},
        {"subject_id": "2", "study_id": "20"},
    ]
    clip_hashes = ["a" * 64, "b" * 64, "c" * 64]
    study_hashes = ["d" * 64, "e" * 64]
    clips = [
        {
            "embedding_idx": 0, "subject_id": "1", "study_id": "10",
            "embedding_sha256": clip_hashes[0],
        },
        {
            "embedding_idx": 1, "subject_id": "1", "study_id": "10",
            "embedding_sha256": clip_hashes[1],
        },
        {
            "embedding_idx": 2, "subject_id": "2", "study_id": "20",
            "embedding_sha256": clip_hashes[2],
        },
    ]
    studies = [
        {
            "study_idx": 0, "subject_id": "1", "study_id": "10",
            "n_clips": 2, "embedding_sha256": study_hashes[0],
        },
        {
            "study_idx": 1, "subject_id": "2", "study_id": "20",
            "n_clips": 1, "embedding_sha256": study_hashes[1],
        },
    ]
    dispositions = [
        {"subject_id": "1", "study_id": "10", "disposition": "IMAGING_ELIGIBLE"},
        {"subject_id": "2", "study_id": "20", "disposition": "IMAGING_ELIGIBLE"},
    ]
    return planned, clips, studies, dispositions, clip_hashes, study_hashes


def _validate(values):
    planned, clips, studies, dispositions, clip_hashes, study_hashes = values
    return preservation.validate_study_pooling_records(
        clip_rows=clips,
        study_rows=studies,
        disposition_rows=dispositions,
        planned_studies=planned,
        clip_vector_hashes=clip_hashes,
        study_vector_hashes=study_hashes,
    )


def test_exact_one_vector_per_eligible_study_passes() -> None:
    result = _validate(_valid_inputs())
    assert result == {
        "eligible_studies": 2,
        "no_cine_studies": 0,
        "one_vector_per_eligible_study": True,
    }


def test_duplicate_or_missing_study_vector_is_blocked() -> None:
    duplicated = copy.deepcopy(_valid_inputs())
    duplicated[2][1] = copy.deepcopy(duplicated[2][0])
    expect_code(
        "STUDY_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH",
        lambda: _validate(duplicated),
    )
    missing = copy.deepcopy(_valid_inputs())
    missing[2].pop()
    missing[5].pop()
    expect_code("STUDY_MANIFEST_ELIGIBLE_SET_MISMATCH", lambda: _validate(missing))


def test_outside_or_wrong_ownership_is_blocked() -> None:
    outside = copy.deepcopy(_valid_inputs())
    outside[3][1]["study_id"] = "999"
    expect_code("STUDY_DISPOSITION_INVALID", lambda: _validate(outside))
    wrong_owner = copy.deepcopy(_valid_inputs())
    wrong_owner[1][0]["subject_id"] = "2"
    expect_code(
        "CLIP_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH",
        lambda: _validate(wrong_owner),
    )


def test_clip_count_and_vector_hash_mismatch_are_blocked() -> None:
    count = copy.deepcopy(_valid_inputs())
    count[2][0]["n_clips"] = 1
    expect_code(
        "STUDY_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH",
        lambda: _validate(count),
    )
    vector = copy.deepcopy(_valid_inputs())
    vector[2][0]["embedding_sha256"] = "f" * 64
    expect_code(
        "STUDY_MANIFEST_VECTOR_OR_OWNERSHIP_MISMATCH",
        lambda: _validate(vector),
    )


def test_preservation_callsite_recomputes_float64_mean_and_binds_summary() -> None:
    source = (ROOT / "scripts" / "preserve_lvef_c3_production_batch.py").read_text()
    assert "validate_study_pooling_records(" in source
    assert "expected_study_array = mean_pool_study_embeddings(" in source
    assert "mean(axis=0, dtype=np.float64).astype(" in source
    assert "np.array_equal(expected_study_array, study_array)" in source
    assert 'pooling_semantics["eligible_studies"]' in source
    assert '"study_pooling_semantics_gate_passed": True' in source


def test_mean_pool_study_embeddings_is_float64_mean_with_float32_output() -> None:
    clips = np.zeros((4, 512), dtype=np.float32)
    clips[:3, 0] = np.asarray([16_777_216, 1, -16_777_216], dtype=np.float32)
    clips[3, 0] = np.float32(7)
    pooled = preservation.mean_pool_study_embeddings(
        clip_embeddings=clips,
        clip_rows=[
            {"embedding_idx": 0, "study_id": "10"},
            {"embedding_idx": 1, "study_id": "10"},
            {"embedding_idx": 2, "study_id": "10"},
            {"embedding_idx": 3, "study_id": "20"},
        ],
        study_rows=[
            {"study_idx": 1, "study_id": "20", "n_clips": 1},
            {"study_idx": 0, "study_id": "10", "n_clips": 3},
        ],
    )
    assert pooled.shape == (2, 512)
    assert pooled.dtype == np.float32
    assert pooled[0, 0] == np.float32(1 / 3)
    assert pooled[1, 0] == np.float32(7)


def test_mean_pool_study_embeddings_rejects_nonfinite_shape_and_membership() -> None:
    clips = np.zeros((1, 512), dtype=np.float32)
    clip_rows = [{"embedding_idx": 0, "study_id": "10"}]
    study_rows = [{"study_idx": 0, "study_id": "10", "n_clips": 1}]
    nonfinite = clips.copy()
    nonfinite[0, 0] = np.nan
    expect_code(
        "STUDY_POOLING_NONFINITE",
        lambda: preservation.mean_pool_study_embeddings(
            clip_embeddings=nonfinite, clip_rows=clip_rows, study_rows=study_rows
        ),
    )
    expect_code(
        "STUDY_POOLING_CLIP_ARRAY_INVALID",
        lambda: preservation.mean_pool_study_embeddings(
            clip_embeddings=clips[:, :511],
            clip_rows=clip_rows,
            study_rows=study_rows,
        ),
    )
    expect_code(
        "STUDY_POOLING_STUDY_ROW_INVALID",
        lambda: preservation.mean_pool_study_embeddings(
            clip_embeddings=clips,
            clip_rows=clip_rows,
            study_rows=[{"study_idx": 0, "study_id": "20", "n_clips": 1}],
        ),
    )


def _five_study_requirements():
    return preservation.core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=5,
        selected_subjects=5,
        normalized_source_objects=5,
        selected_source_bytes=5_000,
        batch_count=1,
        studies_per_full_batch=5,
        final_batch_studies=5,
        contract_id="lvef-c3-canary-synthetic",
    )


def _runtime_authority(*, plan_sha: str, checkpoint_sha: str, environment_sha: str):
    authority = {
        key: ("a" * 40 if key == "git_commit" else "b" * 64)
        for key in preservation.core.PLAN_AUTHORITY_KEYS
    }
    authority["checkpoint_sha256"] = checkpoint_sha
    authority["environment_receipt_sha256"] = environment_sha
    return {**authority, "batch_plan_sha256": plan_sha}


def test_preservation_authority_defaults_to_production_requirements() -> None:
    requirements = _five_study_requirements()
    runtime = _runtime_authority(
        plan_sha="c" * 64, checkpoint_sha="d" * 64, environment_sha="e" * 64
    )
    with mock.patch.object(
        preservation.core, "production_requirements", return_value=requirements
    ) as production_requirements, mock.patch.object(
        preservation.core, "validate_current_batch_plan_v3", return_value="c" * 64
    ), mock.patch.object(
        preservation.core, "derive_expected_runtime_authority", return_value=runtime
    ):
        observed = preservation.resolve_preservation_runtime_authority(
            plan={"authority": {key: runtime[key] for key in preservation.core.PLAN_AUTHORITY_KEYS}},
            contract={},
            contract_path=Path("contract.yaml"),
            governing_commit="a" * 40,
            environment_receipt_sha256="e" * 64,
            checkpoint_sha256="d" * 64,
        )
    production_requirements.assert_called_once_with({})
    assert observed == (requirements, "c" * 64, runtime, False)


def test_preservation_authority_accepts_explicit_exact_five_scope() -> None:
    requirements = _five_study_requirements()
    runtime = _runtime_authority(
        plan_sha="c" * 64, checkpoint_sha="d" * 64, environment_sha="e" * 64
    )
    plan = {
        "authority": {
            key: runtime[key] for key in preservation.core.PLAN_AUTHORITY_KEYS
        }
    }
    with mock.patch.object(
        preservation.core, "validate_current_batch_plan_v3", return_value="c" * 64
    ) as validate_plan, mock.patch.object(
        preservation.core, "production_requirements"
    ) as production_requirements, mock.patch.object(
        preservation.core, "derive_expected_runtime_authority"
    ) as derive_runtime, mock.patch.object(
        preservation, "sha256_file", return_value="b" * 64
    ):
        observed = preservation.resolve_preservation_runtime_authority(
            plan=plan,
            contract={
                "authority": {
                    "state_machine_schema_sha256": "b" * 64,
                    "resume_ledger_schema_sha256": "b" * 64,
                }
            },
            contract_path=Path("contract.yaml"),
            governing_commit="a" * 40,
            environment_receipt_sha256="e" * 64,
            checkpoint_sha256="d" * 64,
            requirements=requirements,
            expected_runtime_authority=runtime,
        )
    assert validate_plan.call_args.kwargs["requirements"] is requirements
    production_requirements.assert_not_called()
    derive_runtime.assert_not_called()
    assert observed == (requirements, "c" * 64, runtime, True)


def test_scoped_preservation_authority_rejects_every_runtime_binding_drift() -> None:
    requirements = _five_study_requirements()
    runtime = _runtime_authority(
        plan_sha="c" * 64, checkpoint_sha="d" * 64, environment_sha="e" * 64
    )

    def resolve(
        authority: dict[str, str], *, plan_authority=None, governing_commit="a" * 40,
        checkpoint_sha="d" * 64, environment_sha="e" * 64,
    ):
        plan = {
            "authority": plan_authority
            if plan_authority is not None
            else {key: authority[key] for key in preservation.core.PLAN_AUTHORITY_KEYS}
        }
        with mock.patch.object(
            preservation.core, "validate_current_batch_plan_v3", return_value="c" * 64
        ), mock.patch.object(preservation, "sha256_file", return_value="b" * 64):
            return preservation.resolve_preservation_runtime_authority(
                plan=plan,
                contract={
                    "authority": {
                        "state_machine_schema_sha256": "b" * 64,
                        "resume_ledger_schema_sha256": "b" * 64,
                    }
                },
                contract_path=Path("contract.yaml"),
                governing_commit=governing_commit,
                environment_receipt_sha256=environment_sha,
                checkpoint_sha256=checkpoint_sha,
                requirements=requirements,
                expected_runtime_authority=authority,
            )

    wrong_plan_hash = dict(runtime)
    wrong_plan_hash["batch_plan_sha256"] = "f" * 64
    expect_code(
        "SCOPED_RUNTIME_BATCH_PLAN_MISMATCH", lambda: resolve(wrong_plan_hash)
    )

    wrong_plan_authority = {
        key: runtime[key] for key in preservation.core.PLAN_AUTHORITY_KEYS
    }
    wrong_plan_authority["selected_manifest_sha256"] = "f" * 64
    expect_code(
        "SCOPED_RUNTIME_PLAN_AUTHORITY_MISMATCH",
        lambda: resolve(runtime, plan_authority=wrong_plan_authority),
    )

    wrong_commit = dict(runtime)
    wrong_commit["git_commit"] = "f" * 40
    expect_code(
        "SCOPED_RUNTIME_GOVERNING_COMMIT_MISMATCH",
        lambda: resolve(wrong_commit),
    )
    expect_code(
        "RUNTIME_CHECKPOINT_AUTHORITY_MISMATCH",
        lambda: resolve(runtime, checkpoint_sha="f" * 64),
    )
    expect_code(
        "RUNTIME_ENVIRONMENT_AUTHORITY_MISMATCH",
        lambda: resolve(runtime, environment_sha="f" * 64),
    )
    expect_code(
        "SCOPED_RUNTIME_AUTHORITY_ARGUMENTS_INCOMPLETE",
        lambda: preservation.resolve_preservation_runtime_authority(
            plan={"authority": {}},
            contract={},
            contract_path=Path("contract.yaml"),
            governing_commit="a" * 40,
            environment_receipt_sha256="e" * 64,
            checkpoint_sha256="d" * 64,
            requirements=requirements,
        ),
    )


def _write_csv(path: Path, header, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)


def _stage_csv_fixture(root: Path) -> tuple[dict[str, Path], dict[str, object]]:
    root = root.resolve()
    source_key = "1" * 64
    source_relative_path = f"{source_key}.dcm"
    clip_key = hashlib.sha256(
        (
            f"{preservation.production_stages.CLIP_KEY_NAMESPACE}"
            f"\0{source_relative_path}"
        ).encode("utf-8")
    ).hexdigest()
    output_relative_path = f"clips/{clip_key[:2]}/{clip_key}.npz"
    clips_root = root / "extracted"
    npz_path = clips_root / output_relative_path
    npz_payload = b"synthetic canonical extraction artifact\n"
    npz_path.parent.mkdir(parents=True)
    npz_path.write_bytes(npz_payload)
    npz_path.chmod(0o600)
    dicom = dict.fromkeys(preservation.DICOM_AUDIT_HEADER, "")
    dicom.update(
        {
            "subject_id": "1",
            "study_id": "10",
            "smoke_role": "production_selected",
            "source_relative_path": source_relative_path,
            "download_sha256": "3" * 64,
            "read_ok": "True",
            "is_multiframe": "True",
            "number_of_frames": "64",
            "pixel_decode_ok": "True",
        }
    )
    extraction = dict.fromkeys(preservation.EXTRACTION_MANIFEST_HEADER, "")
    extraction.update(
        {
            "subject_id": "1",
            "study_id": "10",
            "smoke_role": "production_selected",
            "source_relative_path": source_relative_path,
            "source_sha256": "3" * 64,
            "clip_key": clip_key,
            "output_relative_path": output_relative_path,
            "write_ok": "True",
            "mask_status": "APPLIED",
            "photometric_interpretation": "RGB",
            "transfer_syntax_uid": "1.2.840.10008.1.2.1",
            "decoder_backend": "pydicom_pixels_raw:native",
            "decoder_color_behavior": "STORED_COLOR_RAW",
            "color_transform": "NONE_RGB",
            "canonical_color_space": "RGB",
            "decode_color_status": "PASS",
            "source_sector_pixel_count": "100",
            "source_sector_nonempty_gate_passed": "True",
            "source_nonzero_retained_pixel_count": "50",
            "source_nonzero_retained_pixel_gate_passed": "True",
            "source_temporal_variation_pixel_count": "10",
            "source_temporal_variation_gate_passed": "True",
            "ordinary_post_crop_nonzero_retained_pixel_count": "50",
            "ordinary_post_crop_nonzero_retained_pixel_gate_passed": "True",
            "ordinary_post_crop_temporal_variation_pixel_count": "10",
            "ordinary_post_crop_temporal_variation_gate_passed": "True",
            "post_crop_nonzero_retained_pixel_count": "50",
            "post_crop_nonzero_retained_pixel_gate_passed": "True",
            "post_crop_temporal_variation_pixel_count": "10",
            "post_crop_temporal_variation_gate_passed": "True",
            "ordinary_sampled_nonzero_retained_pixel_count": "50",
            "ordinary_sampled_nonzero_retained_pixel_gate_passed": "True",
            "ordinary_sampled_temporal_variation_pixel_count": "10",
            "ordinary_sampled_temporal_variation_gate_passed": "True",
            "sampled_nonzero_retained_pixel_count": "50",
            "sampled_nonzero_retained_pixel_gate_passed": "True",
            "sampled_temporal_variation_pixel_count": "10",
            "sampled_temporal_variation_gate_passed": "True",
            "encoder_visible_nonzero_retained_pixel_count": "25",
            "encoder_visible_nonzero_retained_pixel_gate_passed": "True",
            "encoder_visible_temporal_variation_pixel_count": "5",
            "encoder_visible_temporal_variation_gate_passed": "True",
            "selected_preprocessing_path": (
                "ORDINARY_CENTER_CROP_RESIZE_HISTORICAL_TEMPORAL_V1"
            ),
            "fallback_status": "NOT_ATTEMPTED",
            "failure_substage": "NONE",
            "temporal_sampling_policy": (
                "historical_compatible_linspace_or_tail_repeat_v1"
            ),
            "frames_shape": "32x224x224x3",
            "frames_dtype": "uint8",
            "npz_sha256": hashlib.sha256(npz_payload).hexdigest(),
            "physical_source_key": source_key,
            "pixel_decode_ok": "True",
        }
    )
    clip = dict.fromkeys(preservation.CLIP_MANIFEST_HEADER, "")
    clip.update(
        {
            "embedding_idx": "0",
            "subject_id": "1",
            "study_id": "10",
            "clip_key": clip_key,
            "physical_source_key": source_key,
            "embedding_l2_norm": "1.0",
            "embedding_sha256": "5" * 64,
            "write_ok": "True",
        }
    )
    study = dict.fromkeys(preservation.STUDY_MANIFEST_HEADER, "")
    study.update(
        {
            "study_idx": "0",
            "subject_id": "1",
            "study_id": "10",
            "n_clips": "1",
            "embedding_sha256": "6" * 64,
        }
    )
    disposition = {
        "subject_id": "1",
        "study_id": "10",
        "disposition": "IMAGING_ELIGIBLE",
    }
    definitions = {
        "dicom_audit_path": (
            root / "dicom.csv",
            preservation.DICOM_AUDIT_HEADER,
            [dicom],
        ),
        "extraction_manifest_path": (
            root / "extraction.csv",
            preservation.EXTRACTION_MANIFEST_HEADER,
            [extraction],
        ),
        "clip_manifest_path": (
            root / "clip.csv",
            preservation.CLIP_MANIFEST_HEADER,
            [clip],
        ),
        "study_manifest_path": (
            root / "study.csv",
            preservation.STUDY_MANIFEST_HEADER,
            [study],
        ),
        "disposition_path": (
            root / "disposition.csv",
            preservation.DISPOSITION_HEADER,
            [disposition],
        ),
        "technical_disposition_path": (
            root / "technical_disposition.csv",
            preservation.TECHNICAL_DISPOSITION_HEADER,
            [],
        ),
    }
    paths: dict[str, Path] = {}
    for name, (path, header, rows) in definitions.items():
        _write_csv(path, header, rows)
        if name == "technical_disposition_path":
            path.chmod(0o600)
        paths[name] = path
    paths["clips_root"] = clips_root
    return paths, {
        "definitions": definitions,
        "source_key": source_key,
        "clip_key": clip_key,
    }


def _validate_stage_csvs(paths: dict[str, Path]):
    return preservation.validate_stage_csv_authority(
        **paths, expected_objects=1, expected_studies=1
    )


def _v2_disposition_stage_csv_fixture(
    root: Path,
) -> dict[str, Path]:
    paths, fixture = _stage_csv_fixture(root)
    definitions = fixture["definitions"]
    base_dicom = definitions["dicom_audit_path"][2][0]
    base_extraction = definitions["extraction_manifest_path"][2][0]
    base_clip = definitions["clip_manifest_path"][2][0]
    npz_payload = (
        paths["clips_root"] / str(base_extraction["output_relative_path"])
    ).read_bytes()
    successful_rows = 999
    dicom_rows = []
    extraction_rows = []
    clip_rows = []
    integer_count_fields = (
        tuple(
            field
            for field, _ in
            preservation.production_stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS
        )
        + tuple(
            field
            for field, _ in
            preservation.production_stages.DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS
        )
    )

    def identity(index: int) -> tuple[str, str, str, str]:
        source_key = hashlib.sha256(f"r8r-csv-{index}".encode()).hexdigest()
        source_relative = f"{source_key}.dcm"
        clip_key = hashlib.sha256(
            (
                f"{preservation.production_stages.CLIP_KEY_NAMESPACE}"
                f"\0{source_relative}"
            ).encode()
        ).hexdigest()
        return (
            source_key,
            source_relative,
            clip_key,
            f"clips/{clip_key[:2]}/{clip_key}.npz",
        )

    for index in range(successful_rows + 1):
        source_key, source_relative, clip_key, output_relative = identity(index)
        dicom = dict(base_dicom)
        dicom.update(
            {
                "source_relative_path": source_relative,
                "number_of_frames": "64.0",
                "photometric_interpretation": base_extraction[
                    "photometric_interpretation"
                ],
                "transfer_syntax_uid": base_extraction[
                    "transfer_syntax_uid"
                ],
            }
        )
        extraction = dict(base_extraction)
        extraction.update(
            {
                "source_relative_path": source_relative,
                "clip_key": clip_key,
                "output_relative_path": output_relative,
                "physical_source_key": source_key,
                "source_num_frames": "64.0",
            }
        )
        for field in integer_count_fields:
            extraction[field] = f"{extraction[field]}.0"
        dicom_rows.append(dicom)
        extraction_rows.append(extraction)
        if index < successful_rows:
            npz_path = paths["clips_root"] / output_relative
            npz_path.parent.mkdir(parents=True, exist_ok=True)
            npz_path.write_bytes(npz_payload)
            npz_path.chmod(0o600)
            clip = dict(base_clip)
            clip.update(
                {
                    "embedding_idx": str(index),
                    "clip_key": clip_key,
                    "physical_source_key": source_key,
                }
            )
            clip_rows.append(clip)

    disposed = extraction_rows[-1]
    disposed.update(
        {
            "write_ok": "False",
            "mask_status": "FAILED",
            "selected_preprocessing_path": "NOT_SELECTED",
            "fallback_status": (
                preservation.production_stages.FALLBACK_NOT_ATTEMPTED
            ),
            "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
            "frames_shape": "",
            "frames_dtype": "",
            "frames_sha256": "",
            "sampled_indices_sha256": "",
            "source_num_frames_sha256": "",
            "npz_sha256": "",
            "error_code": "SourceSignalQualityFailure",
        }
    )
    source_pairs = preservation.production_stages.SOURCE_SIGNAL_COUNT_GATE_PAIRS
    for ordinal, (count_field, gate_field) in enumerate(source_pairs):
        disposed[count_field] = "0.0" if ordinal == 2 else "10.0"
        disposed[gate_field] = "False" if ordinal == 2 else "True"
    for count_field, gate_field in (
        preservation.production_stages.DOWNSTREAM_SIGNAL_COUNT_GATE_PAIRS
    ):
        disposed[count_field] = ""
        disposed[gate_field] = "False"

    disposed_source, _relative, disposed_clip, _output = identity(successful_rows)
    technical_row = {
        "subject_id": disposed["subject_id"],
        "study_id": disposed["study_id"],
        "clip_key": disposed_clip,
        "physical_source_key": disposed_source,
        "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
        "technical_disposition": (
            preservation.production_stages.OBJECT_TECHNICAL_DISPOSITION
        ),
        "selected_source_membership_passed": "True",
        "batch_plan_membership_passed": "True",
        "download_integrity_authority_passed": "True",
        "dicom_header_readable": "True",
        "pixel_decode_ok": "True",
        "decode_color_status": "PASS",
        "canonical_color_space": "RGB",
        "raw_dicom_retained": "True",
        "npz_absent": "True",
        "embedding_absent": "True",
        "object_substitution": "False",
        "study_retains_valid_cine_coverage": "True",
        "technical_disposition_policy_version": (
            preservation.production_stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        ),
    }
    study_rows = [
        {
            **definitions["study_manifest_path"][2][0],
            "n_clips": str(successful_rows),
        }
    ]
    rows_by_name = {
        "dicom_audit_path": dicom_rows,
        "extraction_manifest_path": extraction_rows,
        "clip_manifest_path": clip_rows,
        "study_manifest_path": study_rows,
        "disposition_path": definitions["disposition_path"][2],
        "technical_disposition_path": [technical_row],
    }
    for name, rows in rows_by_name.items():
        path, header, _old_rows = definitions[name]
        _write_csv(path, header, rows)
        if name == "technical_disposition_path":
            path.chmod(0o600)
    return paths


def test_r8r_v2_pandas_integer_csv_tokens_replay_losslessly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths = _v2_disposition_stage_csv_fixture(Path(directory))
        authority = preservation.validate_stage_csv_authority(
            **paths, expected_objects=1000, expected_studies=1
        )
    semantics = authority["extraction_semantics"]
    assert semantics["n_requested_cines"] == 1000
    assert semantics["n_successfully_extracted_cines"] == 999
    assert semantics["n_object_technical_dispositions"] == 1
    assert semantics["n_blocking_failures"] == 0
    assert authority["technical_disposition_semantics"][
        "n_object_technical_dispositions"
    ] == 1


def test_r8r_numeric_csv_coercion_is_closed_and_lossless() -> None:
    convert = preservation._strict_csv_nonnegative_integer_or_empty
    for token, expected in (
        ("", ""),
        ("0", 0),
        ("0.0", 0),
        ("17", 17),
        ("17.0", 17),
    ):
        assert convert(token, code="INTEGER_INVALID") == expected
    for token in (
        "-1",
        "+1",
        "01",
        "01.0",
        "1.00",
        "1.5",
        "1e0",
        "nan",
        "NaN",
        "inf",
        "Infinity",
        " 1",
        "1 ",
    ):
        expect_code(
            "INTEGER_INVALID",
            lambda token=token: convert(token, code="INTEGER_INVALID"),
        )
    with tempfile.TemporaryDirectory() as directory:
        paths, fixture = _stage_csv_fixture(Path(directory))
        definitions = fixture["definitions"]
        dicom_path, dicom_header, dicom_rows = definitions["dicom_audit_path"]
        invalid_dicom = copy.deepcopy(dicom_rows)
        invalid_dicom[0]["number_of_frames"] = "64.00"
        _write_csv(dicom_path, dicom_header, invalid_dicom)
        expect_code(
            "DICOM_AUDIT_INTEGER_INVALID",
            lambda: _validate_stage_csvs(paths),
        )
        _write_csv(dicom_path, dicom_header, dicom_rows)

        extraction_path, extraction_header, extraction_rows = definitions[
            "extraction_manifest_path"
        ]
        invalid_count = copy.deepcopy(extraction_rows)
        invalid_count[0]["post_crop_nonzero_retained_pixel_count"] = "5e1"
        _write_csv(extraction_path, extraction_header, invalid_count)
        expect_code(
            "EXTRACTION_MANIFEST_INTEGER_INVALID",
            lambda: _validate_stage_csvs(paths),
        )
        invalid_frames = copy.deepcopy(extraction_rows)
        invalid_frames[0]["source_num_frames"] = "+64"
        _write_csv(extraction_path, extraction_header, invalid_frames)
        expect_code(
            "EXTRACTION_MANIFEST_INTEGER_INVALID",
            lambda: _validate_stage_csvs(paths),
        )


def test_r8r_post_embedding_clip_partition_is_exact() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths = _v2_disposition_stage_csv_fixture(Path(directory))
        clip_path = paths["clip_manifest_path"]
        clip_rows = preservation.read_csv_exact(
            clip_path, preservation.CLIP_MANIFEST_HEADER
        )
        technical_rows = (
            preservation.production_stages.read_technical_disposition_manifest(
                paths["technical_disposition_path"]
            )
        )
        _write_csv(
            clip_path, preservation.CLIP_MANIFEST_HEADER, clip_rows[:-1]
        )
        expect_code(
            "EXTRACTION_CLIP_IDENTITY_SET_MISMATCH",
            lambda: preservation.validate_stage_csv_authority(
                **paths, expected_objects=1000, expected_studies=1
            ),
        )

        disposed = technical_rows[0]
        disposed_embedding = {
            **clip_rows[0],
            "embedding_idx": str(len(clip_rows)),
            "subject_id": disposed["subject_id"],
            "study_id": disposed["study_id"],
            "clip_key": disposed["clip_key"],
            "physical_source_key": disposed["physical_source_key"],
        }
        _write_csv(
            clip_path,
            preservation.CLIP_MANIFEST_HEADER,
            [*clip_rows, disposed_embedding],
        )
        error = capture_preservation_error(
            lambda: preservation.validate_stage_csv_authority(
                **paths, expected_objects=1000, expected_studies=1
            )
        )
        assert error.code == (
            "EXTRACTION_TECHNICAL_DISPOSITION_EMBEDDING_ABSENCE_INVALID"
        )
        assert error.validation_substage is (
            preservation.PreservationValidationSubstage.CONTEXT_RECONSTRUCTION
        )


def test_r8r_preserves_exact_nested_code_and_closed_substage() -> None:
    validation_substage = preservation.PreservationValidationSubstage
    cases = (
        (
            "context_from_completed_extraction_authority",
            "EXTRACTION_TECHNICAL_DISPOSITION_CONTEXT_INVALID",
            validation_substage.CONTEXT_RECONSTRUCTION,
        ),
        (
            "validate_production_extraction_rows",
            "EXTRACTION_PROVENANCE_COUNT_INVALID",
            validation_substage.EXTRACTION_ROW_VALIDATION,
        ),
        (
            "validate_technical_disposition_manifest_rows",
            "TECHNICAL_DISPOSITION_MANIFEST_AUTHORITY_INVALID",
            validation_substage.TECHNICAL_DISPOSITION_VALIDATION,
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        paths, _fixture = _stage_csv_fixture(Path(directory))
        for function_name, code, substage in cases:
            with mock.patch.object(
                preservation.production_stages,
                function_name,
                side_effect=preservation.production_stages.ProductionStageError(
                    code
                ),
            ):
                error = capture_preservation_error(
                    lambda: _validate_stage_csvs(paths)
                )
            assert error.code == code
            assert error.validation_substage is substage

        with mock.patch.object(
            preservation.production_stages,
            "context_from_completed_extraction_authority",
            side_effect=preservation.production_stages.ProductionStageError(
                "unsafe/path"
            ),
        ):
            sanitized = capture_preservation_error(
                lambda: _validate_stage_csvs(paths)
            )
        assert sanitized.code == "PRESERVATION_NESTED_VALIDATION_CODE_INVALID"
        assert sanitized.validation_substage is (
            preservation.PreservationValidationSubstage.CONTEXT_RECONSTRUCTION
        )
    try:
        preservation.BatchPreservationError(
            "SYNTHETIC", validation_substage="CONTEXT_RECONSTRUCTION"
        )
    except TypeError:
        pass
    else:
        raise AssertionError("A free-form validation substage was accepted")


def test_stage_csvs_require_exact_ordered_headers_before_dataframe_loading() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, fixture = _stage_csv_fixture(Path(directory))
        assert _validate_stage_csvs(paths)["extraction_semantics"][
            "n_extracted_clips"
        ] == 1
        for name in (
            "dicom_audit_path",
            "extraction_manifest_path",
            "clip_manifest_path",
            "study_manifest_path",
            "disposition_path",
        ):
            path, header, rows = fixture["definitions"][name]
            variants = (
                [*header, header[0]],
                [*header, "unexpected_column"],
                [header[1], header[0], *header[2:]],
            )
            for mutated_header in variants:
                mutated_rows = [
                    {column: row.get(column, "") for column in mutated_header}
                    for row in rows
                ]
                _write_csv(path, mutated_header, mutated_rows)
                expect_code("CSV_SCHEMA_MISMATCH", lambda: _validate_stage_csvs(paths))
                _write_csv(path, header, rows)


def test_extraction_and_clip_identity_require_exact_set_and_row_count() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, fixture = _stage_csv_fixture(Path(directory))
        clip_path, clip_header, clip_rows = fixture["definitions"][
            "clip_manifest_path"
        ]
        mismatched = copy.deepcopy(clip_rows)
        mismatched[0]["physical_source_key"] = "7" * 64
        _write_csv(clip_path, clip_header, mismatched)
        expect_code(
            "EXTRACTION_CLIP_IDENTITY_SET_MISMATCH",
            lambda: _validate_stage_csvs(paths),
        )
        extra = copy.deepcopy(clip_rows)
        extra.append(dict(extra[0]))
        _write_csv(clip_path, clip_header, extra)
        expect_code(
            "EXTRACTION_CLIP_IDENTITY_SET_MISMATCH",
            lambda: _validate_stage_csvs(paths),
        )


def test_dicom_candidate_and_extraction_require_exact_same_count_source_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, fixture = _stage_csv_fixture(Path(directory))
        dicom_path, dicom_header, dicom_rows = fixture["definitions"][
            "dicom_audit_path"
        ]
        substituted = copy.deepcopy(dicom_rows)
        substituted[0]["source_relative_path"] = f"{'8' * 64}.dcm"
        _write_csv(dicom_path, dicom_header, substituted)
        expect_code(
            "DICOM_EXTRACTION_CANDIDATE_SET_MISMATCH",
            lambda: _validate_stage_csvs(paths),
        )


def test_preservation_rejects_tampered_dicom_and_extraction_summary_counts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, _ = _stage_csv_fixture(Path(directory))
        authority = _validate_stage_csvs(paths)
        summary = {
            key: None
            for key in preservation.production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
        }
        summary.update(authority["dicom_semantics"])
        summary.update(authority["extraction_semantics"])
        summary.update(
            {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v2",
            }
        )
        preservation.validate_recomputed_stage_summaries(
            dicom_summary=summary,
            dicom_semantics=authority["dicom_semantics"],
            extraction_semantics=authority["extraction_semantics"],
        )
        tampered_dicom = dict(summary, n_readable=0)
        expect_code(
            "DICOM_SUMMARY_RECOMPUTATION_MISMATCH",
            lambda: preservation.validate_recomputed_stage_summaries(
                dicom_summary=tampered_dicom,
                dicom_semantics=authority["dicom_semantics"],
                extraction_semantics=authority["extraction_semantics"],
            ),
        )
        tampered_extraction = dict(summary, n_extracted_clips=0)
        expect_code(
            "EXTRACTION_SUMMARY_RECOMPUTATION_MISMATCH",
            lambda: preservation.validate_recomputed_stage_summaries(
                dicom_summary=tampered_extraction,
                dicom_semantics=authority["dicom_semantics"],
                extraction_semantics=authority["extraction_semantics"],
            ),
        )


def _embedding_authority_fixture():
    clip_array = np.zeros((1, 512), dtype=np.float32)
    clip_array[0, 0] = np.float32(3.0)
    study_array = clip_array.copy()
    clip_rows = [
        {
            "embedding_idx": "0",
            "embedding_l2_norm": "3.0",
            "write_ok": "True",
        }
    ]
    study_rows = [{"study_idx": "0"}]
    summary = {
        "n_clip_embeddings": 1,
        "n_pooled_studies": 1,
        "embedding_dimension": 512,
        "embedding_dtype": "float32",
        "all_finite": True,
    }
    return clip_array, study_array, clip_rows, study_rows, summary


def _validate_embedding(values):
    clip_array, study_array, clip_rows, study_rows, summary = values
    return preservation.validate_embedding_array_authority(
        clip_array=clip_array,
        study_array=study_array,
        clip_rows=clip_rows,
        study_rows=study_rows,
        embedding_summary=summary,
    )


def test_embedding_arrays_require_exact_float32_and_summary_reconciliation() -> None:
    valid = _embedding_authority_fixture()
    result = _validate_embedding(valid)
    assert result["embedding_dtype"] == "float32"
    for array_index in (0, 1):
        wrong_dtype = list(_embedding_authority_fixture())
        wrong_dtype[array_index] = wrong_dtype[array_index].astype(np.float64)
        expect_code(
            "EMBEDDING_ARRAY_DTYPE_MISMATCH",
            lambda values=wrong_dtype: _validate_embedding(values),
        )
    for key, value in (
        ("n_clip_embeddings", 2),
        ("n_pooled_studies", 2),
        ("embedding_dimension", 511),
        ("embedding_dtype", "float64"),
        ("all_finite", False),
        ("all_finite", 1),
    ):
        tampered = list(_embedding_authority_fixture())
        tampered[4] = dict(tampered[4], **{key: value})
        expect_code(
            "EMBEDDING_SUMMARY_ARRAY_MISMATCH",
            lambda values=tampered: _validate_embedding(values),
        )


def test_clip_manifest_write_status_and_l2_norm_are_array_bound() -> None:
    for write_ok in ("False", "malformed"):
        invalid = list(_embedding_authority_fixture())
        invalid[2] = [dict(invalid[2][0], write_ok=write_ok)]
        expect_code(
            "CLIP_MANIFEST_WRITE_STATUS_INVALID",
            lambda values=invalid: _validate_embedding(values),
        )
    for norm in ("not-a-number", "nan"):
        invalid = list(_embedding_authority_fixture())
        invalid[2] = [dict(invalid[2][0], embedding_l2_norm=norm)]
        expect_code(
            "CLIP_MANIFEST_L2_NORM_INVALID",
            lambda values=invalid: _validate_embedding(values),
        )
    mismatch = list(_embedding_authority_fixture())
    mismatch[2] = [dict(mismatch[2][0], embedding_l2_norm="3.000001")]
    expect_code(
        "CLIP_MANIFEST_L2_NORM_MISMATCH",
        lambda: _validate_embedding(mismatch),
    )


def _r8r_raw_authority_fixture(root: Path) -> dict[str, object]:
    production_root = (root / "production").resolve()
    raw_batch_root = (
        production_root
        / "attempts"
        / preservation.R8R_FIXED_ATTEMPT_ID
        / "raw"
        / preservation.R8R_FIXED_BATCH_ID
    )
    raw_objects_root = raw_batch_root / "objects"
    receipts_root = raw_batch_root / "receipts"
    raw_objects_root.mkdir(parents=True)
    receipts_root.mkdir()
    source_key = hashlib.sha256(b"r8r-fixed-raw-object").hexdigest()
    source_relative_path = "gcs_authority/cines/r8r_fixed_object.dcm"
    payload = b"fixed synthetic dicom bytes for body-free authority\n"
    raw_path = raw_objects_root / f"{source_key}.dcm"
    raw_path.write_bytes(payload)
    raw_path.chmod(0o600)
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    verified_download_manifest = (
        raw_batch_root / "verified_download_manifest.restricted.csv"
    )
    _write_csv(
        verified_download_manifest,
        preservation.VERIFIED_DOWNLOAD_MANIFEST_HEADER,
        [
            {
                "subject_id": "100001",
                "study_id": "200001",
                "source_relative_path": source_relative_path,
                "download_ok": "true",
                "observed_sha256": observed_sha256,
                "physical_source_key": source_key,
            }
        ],
    )
    verified_download_manifest.chmod(0o600)
    receipt_path = receipts_root / f"{source_key}.verification.json"
    receipt_path.write_text("{\"status\":\"PASS_DOWNLOAD_VERIFICATION\"}\n")
    receipt_path.chmod(0o600)
    planned_batch = {
        "batch_id": preservation.R8R_FIXED_BATCH_ID,
        "n_objects": 1,
        "source_bytes": len(payload),
        "objects": [
            {
                "source_object_key": source_key,
                "subject_id": "100001",
                "study_id": "200001",
                "source_relative_path": source_relative_path,
                "size_bytes": len(payload),
            }
        ],
    }
    preservation_manifest = (
        production_root / "batch_preservation_manifest.restricted.tsv"
    )

    def write_preservation_manifest(dicom_sha256: str) -> None:
        rows = []
        for path in (raw_path, verified_download_manifest, receipt_path):
            digest = (
                dicom_sha256
                if path == raw_path
                else hashlib.sha256(path.read_bytes()).hexdigest()
            )
            rows.append(
                {
                    "relative_path": path.relative_to(
                        production_root
                    ).as_posix(),
                    "size_bytes": str(path.stat().st_size),
                    "sha256": digest,
                    "role": "raw_dicom_and_download_authority",
                }
            )
        with preservation_manifest.open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=preservation.MANIFEST_HEADER,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(
                sorted(rows, key=lambda row: row["relative_path"])
            )
        preservation_manifest.chmod(0o600)

    write_preservation_manifest(observed_sha256)
    return {
        "production_root": production_root,
        "raw_batch_root": raw_batch_root,
        "raw_objects_root": raw_objects_root,
        "raw_path": raw_path,
        "verified_download_manifest": verified_download_manifest,
        "receipt_path": receipt_path,
        "planned_batch": planned_batch,
        "observed_sha256": observed_sha256,
        "preservation_manifest": preservation_manifest,
        "write_preservation_manifest": write_preservation_manifest,
    }


def test_r8r_recovery_never_opens_dicom_bodies_across_preservation_and_retirement() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _r8r_raw_authority_fixture(Path(directory))
        raw_path = fixture["raw_path"]
        verified_manifest = fixture["verified_download_manifest"]
        raw_batch_root = fixture["raw_batch_root"]
        production_root = fixture["production_root"]
        body_open_attempts: list[str] = []
        non_dicom_stable_hashes: list[Path] = []
        non_dicom_retirement_hashes: list[Path] = []
        real_path_open = Path.open
        real_os_open = os.open
        real_preservation_hash = preservation.sha256_file
        real_stable_hash = preservation.stable_manifest_artifact_authority
        real_retirement_hash = retirement.sha256_file

        def reject_dicom_path_open(path: Path, *args, **kwargs):
            if path.suffix.lower() == ".dcm":
                body_open_attempts.append(str(path))
                raise AssertionError("DICOM Path.open was reached")
            return real_path_open(path, *args, **kwargs)

        def reject_dicom_os_open(
            path, flags, mode=0o777, *, dir_fd=None
        ):
            token = os.fspath(path)
            if token.lower().endswith(".dcm"):
                body_open_attempts.append(token)
                raise AssertionError("DICOM os.open was reached")
            if dir_fd is None:
                return real_os_open(path, flags, mode)
            return real_os_open(path, flags, mode, dir_fd=dir_fd)

        def guarded_preservation_hash(path: Path) -> str:
            if Path(path).suffix.lower() == ".dcm":
                body_open_attempts.append(str(path))
                raise AssertionError("DICOM sha256_file was reached")
            return real_preservation_hash(Path(path))

        def guarded_stable_hash(path: Path) -> tuple[int, str]:
            if Path(path).suffix.lower() == ".dcm":
                body_open_attempts.append(str(path))
                raise AssertionError("DICOM stable hash was reached")
            non_dicom_stable_hashes.append(Path(path))
            return real_stable_hash(Path(path))

        def guarded_retirement_hash(path: Path) -> str:
            if Path(path).suffix.lower() == ".dcm":
                body_open_attempts.append(str(path))
                raise AssertionError("retirement DICOM hash was reached")
            non_dicom_retirement_hashes.append(Path(path))
            return real_retirement_hash(Path(path))

        with mock.patch.object(
            Path, "open", reject_dicom_path_open
        ), mock.patch.object(
            preservation.os, "open", reject_dicom_os_open
        ), mock.patch.object(
            preservation, "sha256_file", guarded_preservation_hash
        ), mock.patch.object(
            preservation,
            "stable_manifest_artifact_authority",
            guarded_stable_hash,
        ), mock.patch.object(
            retirement, "sha256_file", guarded_retirement_hash
        ):
            rows, authority = (
                preservation.validate_verified_raw_dicom_authority(
                    raw_objects_root=fixture["raw_objects_root"],
                    verified_download_manifest=verified_manifest,
                    planned_batch=fixture["planned_batch"],
                    artifact_validation_context=(
                        preservation.R8R_FIXED_BATCH3_NO_DICOM_BODY
                    ),
                )
            )
            assert len(rows) == 1
            sealed = authority[raw_path]
            assert sealed.observed_sha256 == fixture["observed_sha256"]
            assert preservation._sealed_raw_dicom_artifact_record(
                sealed,
                production_root,
                "raw_dicom_and_download_authority",
            )["sha256"] == fixture["observed_sha256"]
            preservation._artifact_record(
                verified_manifest,
                production_root,
                "raw_dicom_and_download_authority",
            )
            retirement_authority = retirement._validate_raw_retention(
                fixture["raw_objects_root"],
                planned_batch=fixture["planned_batch"],
                artifact_validation_context=(
                    retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
                ),
            )
            retirement._validate_raw_retention(
                fixture["raw_objects_root"],
                planned_batch=fixture["planned_batch"],
                artifact_validation_context=(
                    retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
                ),
                expected_authority=retirement_authority,
            )
            retirement.validate_preservation_coverage(
                fixture["preservation_manifest"],
                production_root=production_root,
                required_roots=((raw_batch_root, raw_batch_root),),
                artifact_validation_context=(
                    retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
                ),
                sealed_raw_dicom_authority=retirement_authority,
            )
            fixture["write_preservation_manifest"]("0" * 64)
            expect_retirement_code(
                "PRESERVATION_TREE_COVERAGE_MISMATCH",
                lambda: retirement.validate_preservation_coverage(
                    fixture["preservation_manifest"],
                    production_root=production_root,
                    required_roots=((raw_batch_root, raw_batch_root),),
                    artifact_validation_context=(
                        retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
                    ),
                    sealed_raw_dicom_authority=retirement_authority,
                ),
            )
            fixture["write_preservation_manifest"](
                fixture["observed_sha256"]
            )
            metadata = raw_path.stat(follow_symlinks=False)
            os.utime(
                raw_path,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1),
                follow_symlinks=False,
            )
            expect_code(
                "RAW_DICOM_METADATA_CHANGED",
                lambda: preservation.validate_sealed_raw_dicom_metadata(
                    sealed
                ),
            )
        assert body_open_attempts == []
        assert verified_manifest in non_dicom_stable_hashes
        assert verified_manifest in non_dicom_retirement_hashes


def test_ordinary_raw_authority_still_hashes_every_dicom_body() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _r8r_raw_authority_fixture(Path(directory))
        raw_path = fixture["raw_path"]
        preservation_hashes: list[Path] = []
        stable_hashes: list[Path] = []
        retirement_hashes: list[Path] = []
        real_preservation_hash = preservation.sha256_file
        real_stable_hash = preservation.stable_manifest_artifact_authority
        real_retirement_hash = retirement.sha256_file

        def observed_preservation_hash(path: Path) -> str:
            preservation_hashes.append(Path(path))
            return real_preservation_hash(Path(path))

        def observed_stable_hash(path: Path) -> tuple[int, str]:
            stable_hashes.append(Path(path))
            return real_stable_hash(Path(path))

        def observed_retirement_hash(path: Path) -> str:
            retirement_hashes.append(Path(path))
            return real_retirement_hash(Path(path))

        with mock.patch.object(
            preservation, "sha256_file", observed_preservation_hash
        ), mock.patch.object(
            preservation,
            "stable_manifest_artifact_authority",
            observed_stable_hash,
        ), mock.patch.object(
            retirement, "sha256_file", observed_retirement_hash
        ):
            preservation.validate_verified_raw_dicom_authority(
                raw_objects_root=fixture["raw_objects_root"],
                verified_download_manifest=fixture[
                    "verified_download_manifest"
                ],
                planned_batch=fixture["planned_batch"],
                artifact_validation_context=preservation.STRICT_CONTENT_HASH,
            )
            preservation._artifact_record(
                raw_path,
                fixture["production_root"],
                "raw_dicom_and_download_authority",
            )
            retirement._validate_raw_retention(
                fixture["raw_objects_root"],
                planned_batch=fixture["planned_batch"],
                artifact_validation_context=retirement.STRICT_CONTENT_HASH,
            )
            retirement.validate_preservation_coverage(
                fixture["preservation_manifest"],
                production_root=fixture["production_root"],
                required_roots=(
                    (
                        fixture["raw_batch_root"],
                        fixture["raw_batch_root"],
                    ),
                ),
                artifact_validation_context=retirement.STRICT_CONTENT_HASH,
            )
        assert raw_path in preservation_hashes
        assert raw_path in stable_hashes
        assert raw_path in retirement_hashes


def test_r8r_body_free_context_is_closed_to_exact_fixed_authority() -> None:
    exact = {
        "artifact_validation_context": (
            preservation.R8R_FIXED_BATCH3_NO_DICOM_BODY
        ),
        "production_root": preservation.R8R_FIXED_PRODUCTION_ROOT,
        "attempt_id": preservation.R8R_FIXED_ATTEMPT_ID,
        "batch_id": preservation.R8R_FIXED_BATCH_ID,
        "plan_sha256": preservation.R8R_FIXED_PLAN_SHA256,
        "governing_commit": preservation.R8R_FIXED_SCIENTIFIC_COMMIT,
        "scheduler_runner_path": (
            preservation.R8R_FIXED_SCHEDULER_RUNNER_PATH
        ),
    }
    preservation.validate_artifact_validation_scope(**exact)
    mutations = (
        ("attempt_id", "lvef_c3_full_wrong_authority"),
        ("batch_id", "c3_batch_001"),
        ("plan_sha256", "0" * 64),
        ("governing_commit", "0" * 40),
        ("production_root", Path("/restricted/projectnb/wrong")),
    )
    for key, value in mutations:
        candidate = dict(exact)
        candidate[key] = value
        expect_code(
            "R8R_RECOVERY_SCOPE_INVALID",
            lambda candidate=candidate: (
                preservation.validate_artifact_validation_scope(**candidate)
            ),
        )
    wrong_runner = dict(exact)
    wrong_runner["scheduler_runner_path"] = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "scc_run_lvef_c3_full_sequential.sh"
    )
    expect_code(
        "R8R_RECOVERY_SCHEDULER_RUNNER_INVALID",
        lambda: preservation.validate_artifact_validation_scope(
            **wrong_runner
        ),
    )
    for token in (
        "R8R_FIXED_BATCH3_NO_DICOM_BODY",
        True,
        None,
    ):
        invalid = dict(exact)
        invalid["artifact_validation_context"] = token
        expect_code(
            "ARTIFACT_VALIDATION_CONTEXT_INVALID",
            lambda invalid=invalid: (
                preservation.validate_artifact_validation_scope(**invalid)
            ),
        )
    expect_retirement_code(
        "ARTIFACT_VALIDATION_CONTEXT_INVALID",
        lambda: retirement.require_artifact_validation_context(
            "STRICT_CONTENT_HASH"
        ),
    )
    preservation.validate_artifact_validation_scope(
        **{
            **exact,
            "artifact_validation_context": preservation.STRICT_CONTENT_HASH,
            "production_root": Path("/ordinary/synthetic"),
            "attempt_id": "lvef_c3_ordinary_synthetic",
            "batch_id": "c3_batch_001",
            "plan_sha256": "1" * 64,
            "governing_commit": "2" * 40,
            "scheduler_runner_path": None,
        }
    )
