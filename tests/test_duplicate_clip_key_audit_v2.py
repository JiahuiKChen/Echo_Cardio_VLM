import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_duplicate_clip_keys_v2 import (  # noqa: E402
    ADJUDICATION_CATEGORIES,
    AGGREGATE_FILENAMES,
    classify_duplicate_group_v2,
    main as duplicate_v2_main,
    resolution_for_category,
)
from lvef_multitask_clip_provenance import EXPECTED_COMPONENTS  # noqa: E402


def _evidence(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "source_count": 2,
        "merged_count": 2,
        "source_full_rows_equal": True,
        "payload_equal_excluding_permitted_rewrites": True,
        "source_vectors_exact_equal": True,
        "source_vectors_numerically_equal": True,
        "source_locator_status": "COMPLETE_EQUAL",
        "physical_source_assessment": "SAME_LOCATOR_CONTENT_UNVERIFIED",
        "extraction_metadata_status": "COMPLETE_EQUAL",
        "extracted_content_assessment": "UNAVAILABLE",
        "source_artifact_purged": False,
        "stored_vs_recomputed_l2": "COMPLETE_EQUAL_1E_6",
        "merged_component_correspondence": "EXACT",
    }
    result.update(updates)
    return result


def test_v2_never_permits_vector_only_deduplication() -> None:
    category = classify_duplicate_group_v2(_evidence())
    assert category == "SAME_SOURCE_LOCATOR_IDENTICAL_VECTOR_FILE_HASH_UNAVAILABLE"
    resolution = resolution_for_category(category)
    assert resolution["deterministic_deduplication_permitted"] is False
    assert resolution["quarantine_required"] is True


def test_v2_physical_confirmation_and_collision_categories() -> None:
    exact = classify_duplicate_group_v2(
        _evidence(
            physical_source_assessment="CONFIRMED_SINGLE_PHYSICAL_SOURCE",
            extracted_content_assessment="CONFIRMED_EQUAL",
        )
    )
    assert exact == "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED"
    assert resolution_for_category(exact)["deterministic_deduplication_permitted"] is True

    collision = classify_duplicate_group_v2(
        _evidence(
            source_locator_status="COMPLETE_DIFFERENT",
            physical_source_assessment="CONFIRMED_MULTIPLE_PHYSICAL_SOURCES",
            source_vectors_exact_equal=False,
            source_vectors_numerically_equal=False,
        )
    )
    assert collision == "DIFFERENT_PHYSICAL_CLIPS_KEY_COLLISION"
    different_locator_same_vector = classify_duplicate_group_v2(
        _evidence(
            source_locator_status="COMPLETE_DIFFERENT",
            physical_source_assessment="DIFFERENT_LOCATORS_CONTENT_UNVERIFIED",
        )
    )
    assert different_locator_same_vector == "DIFFERENT_SOURCE_LOCATORS_IDENTICAL_VECTOR"
    assert set(ADJUDICATION_CATEGORIES) >= {
        exact,
        collision,
        different_locator_same_vector,
    }


def test_v2_requested_fail_closed_categories_have_deterministic_precedence() -> None:
    assert classify_duplicate_group_v2(
        _evidence(source_vectors_exact_equal=False, source_vectors_numerically_equal=False)
    ) == "SAME_SOURCE_LOCATOR_DIFFERENT_VECTOR"
    assert classify_duplicate_group_v2(
        _evidence(source_artifact_purged=True)
    ) == "SOURCE_ARTIFACT_PURGED"
    assert classify_duplicate_group_v2(
        _evidence(source_locator_status="MISSING", physical_source_assessment="MISSING_LOCATOR")
    ) == "MISSING_LOCATOR"
    assert classify_duplicate_group_v2(
        _evidence(
            source_count=1,
            merged_count=2,
            merged_component_correspondence="MERGE_CARDINALITY_OR_INDEX_REWRITE_ONLY",
        )
    ) == "MERGE_OR_INDEX_REWRITE_ONLY"
    assert classify_duplicate_group_v2(
        _evidence(
            source_full_rows_equal=False,
            physical_source_assessment="CONFIRMED_SINGLE_PHYSICAL_SOURCE",
            extracted_content_assessment="CONFIRMED_EQUAL",
        )
    ) == "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED"


def _empty_clip_manifest() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "embedding_idx",
            "subject_id",
            "study_id",
            "dicom_filepath",
            "npz_path",
            "view_id",
            "view_name",
            "embedding_l2_norm",
            "write_ok",
            "error",
        ]
    )


def _empty_extraction_manifest() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "subject_id",
            "study_id",
            "dicom_filepath",
            "output_path",
            "write_ok",
            "source_num_frames",
            "source_rows",
            "source_columns",
            "target_frames",
            "target_size",
        ]
    )


def _empty_dicom_audit() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "subject_id",
            "study_id",
            "dicom_filepath",
            "dicom_abs_path",
            "read_ok",
        ]
    )


def test_v2_cli_emits_exact_five_safe_outputs_and_restricted_groups() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        dicom = root / "source.dcm"
        dicom.write_bytes(b"synthetic-dicom")
        extracted = root / "clip.npz"
        np.savez_compressed(
            extracted,
            frames=np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4),
            source_num_frames=np.array([2], dtype=np.int32),
            source_rows=np.array([3], dtype=np.int32),
            source_columns=np.array([4], dtype=np.int32),
        )
        vector = np.repeat(
            np.arange(4, dtype=np.float32).reshape(1, 4), 2, axis=0
        )
        norm = float(np.linalg.norm(vector[0]))
        duplicate_manifest = pd.DataFrame(
            {
                "embedding_idx": [0, 1],
                "subject_id": ["SYN_SUBJECT", "SYN_SUBJECT"],
                "study_id": ["SYN_STUDY", "SYN_STUDY"],
                "dicom_filepath": [str(dicom), str(dicom)],
                "npz_path": [str(extracted), str(extracted)],
                "view_id": [-1, -1],
                "view_name": [None, None],
                "embedding_l2_norm": [norm, norm],
                "write_ok": [True, True],
                "error": [None, None],
            }
        )
        extraction = pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT"],
                "study_id": ["SYN_STUDY"],
                "dicom_filepath": [str(dicom)],
                "output_path": [str(extracted)],
                "write_ok": [True],
                "source_num_frames": [2],
                "source_rows": [3],
                "source_columns": [4],
                "target_frames": [2],
                "target_size": [4],
            }
        )

        component_manifest_args: list[str] = []
        component_embedding_args: list[str] = []
        component_extraction_args: list[str] = []
        component_dicom_args: list[str] = []
        for component in EXPECTED_COMPONENTS:
            manifest_path = root / f"{component}_clip.csv"
            embedding_path = root / f"{component}_embedding.npz"
            extraction_path = root / f"{component}_extraction.csv"
            dicom_audit_path = root / f"{component}_dicom_audit.csv"
            if component == "batch_000":
                duplicate_manifest.to_csv(manifest_path, index=False)
                np.savez_compressed(embedding_path, embeddings=vector)
                extraction.to_csv(extraction_path, index=False)
                pd.DataFrame(
                    {
                        "subject_id": ["SYN_SUBJECT"],
                        "study_id": ["SYN_STUDY"],
                        "dicom_filepath": [str(dicom)],
                        "dicom_abs_path": [str(dicom)],
                        "read_ok": [True],
                    }
                ).to_csv(dicom_audit_path, index=False)
            else:
                _empty_clip_manifest().to_csv(manifest_path, index=False)
                np.savez_compressed(
                    embedding_path, embeddings=np.zeros((0, 4), dtype=np.float32)
                )
                _empty_extraction_manifest().to_csv(extraction_path, index=False)
                _empty_dicom_audit().to_csv(dicom_audit_path, index=False)
            component_manifest_args.extend(
                ["--component-manifest", f"{component}={manifest_path}"]
            )
            component_embedding_args.extend(
                ["--component-embedding-npz", f"{component}={embedding_path}"]
            )
            component_extraction_args.extend(
                ["--component-extraction-manifest", f"{component}={extraction_path}"]
            )
            component_dicom_args.extend(
                ["--component-dicom-audit", f"{component}={dicom_audit_path}"]
            )

        merged_manifest = root / "merged.csv"
        merged_embedding = root / "merged.npz"
        selected = root / "selected.csv"
        duplicate_manifest.to_csv(merged_manifest, index=False)
        np.savez_compressed(merged_embedding, embeddings=vector)
        pd.DataFrame(
            {"subject_id": ["SYN_SUBJECT"], "study_id": ["SYN_STUDY"]}
        ).to_csv(selected, index=False)
        aggregate = root / "aggregate"
        restricted = root / "restricted"

        original = sys.argv[:]
        sys.argv = [
            "audit_duplicate_clip_keys_v2.py",
            *component_manifest_args,
            *component_embedding_args,
            *component_extraction_args,
            *component_dicom_args,
            "--merged-manifest",
            str(merged_manifest),
            "--merged-embedding-npz",
            str(merged_embedding),
            "--selected-studies",
            str(selected),
            "--expected-duplicate-keys",
            "1",
            "--expected-embedding-dim",
            "4",
            "--vector-rtol",
            "1e-6",
            "--vector-atol",
            "1e-6",
            "--aggregate-output-dir",
            str(aggregate),
            "--restricted-output-dir",
            str(restricted),
        ]
        try:
            status = duplicate_v2_main()
        finally:
            sys.argv = original
        assert status == 0
        assert {path.name for path in aggregate.iterdir()} == set(AGGREGATE_FILENAMES)
        summary = json.loads(
            (aggregate / "duplicate_clip_adjudication_v2.summary.json").read_text()
        )
        assert summary["category_counts"]["EXACT_REPEATED_MANIFEST_ROW_CONFIRMED"] == 1
        counts = pd.read_csv(
            aggregate / "duplicate_clip_adjudication_v2_reason_counts.csv"
        )
        assert counts.iloc[0]["deterministic_deduplication_permitted"]
        aggregate_text = "\n".join(
            path.read_text() for path in aggregate.iterdir() if path.is_file()
        )
        assert "SYN_SUBJECT" not in aggregate_text
        assert "SYN_STUDY" not in aggregate_text
        assert str(root) not in aggregate_text
        restricted_text = (
            restricted / "duplicate_clip_evidence_availability_v2_restricted.csv"
        ).read_text()
        assert "SYN_STUDY" in restricted_text
