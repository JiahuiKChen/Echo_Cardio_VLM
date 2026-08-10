from __future__ import annotations

import copy
import csv
from pathlib import Path
import sys
import tempfile

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
    assert "mean(axis=0, dtype=np.float64).astype(" in source
    assert "np.array_equal(expected_vector" in source
    assert 'pooling_semantics["eligible_studies"]' in source
    assert '"study_pooling_semantics_gate_passed": True' in source


def _write_csv(path: Path, header, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)


def _stage_csv_fixture(root: Path) -> tuple[dict[str, Path], dict[str, object]]:
    source_key = "1" * 64
    clip_key = "2" * 64
    dicom = dict.fromkeys(preservation.DICOM_AUDIT_HEADER, "")
    dicom.update(
        {
            "subject_id": "1",
            "study_id": "10",
            "smoke_role": "production_selected",
            "source_relative_path": f"{source_key}.dcm",
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
            "source_relative_path": f"{source_key}.dcm",
            "source_sha256": "3" * 64,
            "clip_key": clip_key,
            "output_relative_path": f"clips/{clip_key[:2]}/{clip_key}.npz",
            "write_ok": "True",
            "mask_status": "APPLIED",
            "temporal_sampling_policy": (
                "historical_compatible_linspace_or_tail_repeat_v1"
            ),
            "frames_shape": "32x224x224x3",
            "frames_dtype": "uint8",
            "npz_sha256": "4" * 64,
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
    }
    paths: dict[str, Path] = {}
    for name, (path, header, rows) in definitions.items():
        _write_csv(path, header, rows)
        paths[name] = path
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
            "EXTRACTION_CLIP_ROW_COUNT_MISMATCH",
            lambda: _validate_stage_csvs(paths),
        )


def test_dicom_candidate_and_extraction_require_exact_same_count_source_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, fixture = _stage_csv_fixture(Path(directory))
        extraction_path, extraction_header, extraction_rows = fixture["definitions"][
            "extraction_manifest_path"
        ]
        substituted = copy.deepcopy(extraction_rows)
        substituted[0]["source_relative_path"] = f"{'8' * 64}.dcm"
        _write_csv(extraction_path, extraction_header, substituted)
        expect_code(
            "DICOM_EXTRACTION_CANDIDATE_SET_MISMATCH",
            lambda: _validate_stage_csvs(paths),
        )


def test_preservation_rejects_tampered_dicom_and_extraction_summary_counts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, _ = _stage_csv_fixture(Path(directory))
        authority = _validate_stage_csvs(paths)
        summary = {
            **authority["dicom_semantics"],
            **authority["extraction_semantics"],
        }
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
