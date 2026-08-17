from __future__ import annotations

import copy
import csv
import hashlib
from pathlib import Path
import sys
import tempfile
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preserve_lvef_c3_production_batch as preservation


def expect_code(code: str, function) -> None:
    try:
        function()
    except preservation.BatchPreservationError as exc:
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
