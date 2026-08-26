from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.duplicate_metadata import (  # noqa: E402
    DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE,
    LEGITIMATE_DISTINCT_CLIPS,
    MetadataInspectionInputs,
    MetadataStage,
    STAGE_ORDER,
    TRUE_DUPLICATE_EMBEDDING_ROWS,
    TRUE_DUPLICATE_EXPECTED_ROWS,
    TRUE_DUPLICATE_EXTRACTION_ROWS,
    TRUE_DUPLICATE_MERGE_ROWS,
    inspect_duplicate_metadata,
    write_metadata_inspection_outputs,
)


class DuplicateMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.input_root = self.root / "inputs"
        self.input_root.mkdir()
        self.token = "a" * 64
        self.study_id = 101
        self.subject_id = 10
        self.dicom = "files/p10/p10/s101/clip.dcm"
        self.npz = str(self.root / "historical" / "clip.npz")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _prior(self, *, vector_hashes: tuple[str, str] = ("b" * 64, "b" * 64)) -> Path:
        path = self.input_root / "prior.csv"
        pd.DataFrame(
            [
                {
                    "group_token": self.token,
                    "study_id": self.study_id,
                    "subject_id": self.subject_id,
                    "dicom_filepath": self.dicom,
                    "npz_path": self.npz,
                    "embedding_idx": index,
                    "embedding_vector_sha256": vector_hashes[index],
                }
                for index in (0, 1)
            ]
        ).to_csv(path, index=False)
        return path

    def _row(self, stage: str, index: int, *, distinct_window: bool = False) -> dict:
        row = {
            "subject_id": self.subject_id,
            "study_id": self.study_id,
            "dicom_filepath": self.dicom,
        }
        if stage == "expected_records":
            row["acquisition_datetime"] = "2020-01-01 00:00:00"
        elif stage in {"dicom_audit", "cine_candidates"}:
            row.update(
                {
                    "dicom_abs_path": str(self.root / "source" / self.dicom),
                    "read_ok": True,
                    "is_multiframe": True,
                    "number_of_frames": 40,
                    "rows": 600,
                    "columns": 800,
                }
            )
        elif stage == "extraction_manifest":
            row.update(
                {
                    "output_path": (
                        str(self.root / "historical" / f"window_{index}.npz")
                        if distinct_window
                        else self.npz
                    ),
                    "write_ok": True,
                    "status": "written" if index == 0 else "skipped_existing",
                    "source_num_frames": 40,
                    "source_rows": 600,
                    "source_columns": 800,
                    "target_frames": 32,
                    "target_size": 224,
                }
            )
            if distinct_window:
                row["window_index"] = index
        else:
            row.update(
                {
                    "npz_path": (
                        str(self.root / "historical" / f"window_{index}.npz")
                        if distinct_window
                        else self.npz
                    ),
                    "embedding_idx": index,
                    "write_ok": True,
                }
            )
            if distinct_window:
                row["window_index"] = index
        return row

    def _inputs(
        self,
        first_duplicate_stage: str,
        *,
        distinct_window: bool = False,
        create_npz: bool = False,
    ) -> MetadataInspectionInputs:
        stages: list[MetadataStage] = []
        duplicate_seen = False
        for stage in STAGE_ORDER:
            if stage == first_duplicate_stage:
                duplicate_seen = True
            count = 2 if duplicate_seen else 1
            rows = [
                self._row(stage, index, distinct_window=distinct_window)
                for index in range(count)
            ]
            path = self.input_root / f"{stage}.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            stages.append(MetadataStage(stage, path))
        if create_npz:
            path = Path(self.npz)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"metadata-test-placeholder")
        return MetadataInspectionInputs(
            prior_forensics_rows=self._prior(),
            stages=tuple(stages),
            output_root=self.root / "output",
            source_roots=(self.root / "source",),
            preprocessing_git_sha="1" * 40,
            preprocessing_script=ROOT / "scripts" / "extract_mimic_echo_cines.py",
            embedding_script=ROOT / "scripts" / "extract_echoprime_embeddings.py",
            runner_script=ROOT / "scripts" / "scc_run_fullscale_pipeline.sh",
            encoder_checkpoint_sha256="c" * 64,
            expected_group_count=1,
            expected_row_count=2,
        )

    def _classification(self, first_stage: str, **kwargs) -> tuple[str, object]:
        result = inspect_duplicate_metadata(self._inputs(first_stage, **kwargs))
        self.assertEqual(result.status, DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE)
        self.assertEqual(len(result.restricted_groups), 1)
        return str(result.restricted_groups.iloc[0]["classification"]), result

    def test_expected_list_duplication_is_resolved(self) -> None:
        classification, result = self._classification("expected_records")
        self.assertEqual(classification, TRUE_DUPLICATE_EXPECTED_ROWS)
        self.assertEqual(
            result.summary["first_duplicate_stage_counts"]["expected_records"], 1
        )

    def test_extraction_duplication_is_resolved(self) -> None:
        classification, _ = self._classification("extraction_manifest")
        self.assertEqual(classification, TRUE_DUPLICATE_EXTRACTION_ROWS)

    def test_embedding_duplication_is_resolved(self) -> None:
        classification, _ = self._classification("batch_embedding_manifest")
        self.assertEqual(classification, TRUE_DUPLICATE_EMBEDDING_ROWS)

    def test_merge_duplication_is_resolved(self) -> None:
        classification, _ = self._classification("merged_embedding_manifest")
        self.assertEqual(classification, TRUE_DUPLICATE_MERGE_ROWS)

    def test_positive_distinct_window_metadata_is_legitimate(self) -> None:
        classification, result = self._classification(
            "extraction_manifest", distinct_window=True
        )
        self.assertEqual(classification, LEGITIMATE_DISTINCT_CLIPS)
        self.assertTrue(
            bool(result.restricted_groups.iloc[0]["distinct_window_metadata_present"])
        )

    def test_recovery_inventory_uses_lstat_without_opening_npz(self) -> None:
        _, result = self._classification("expected_records", create_npz=True)
        npz_rows = result.restricted_recovery[
            result.restricted_recovery["candidate_type"] == "processed_npz"
        ]
        self.assertTrue(bool((npz_rows["exists"] & npz_rows["is_file"]).any()))

    def test_outputs_are_aggregate_safe_and_refuse_overwrite(self) -> None:
        inputs = self._inputs("expected_records")
        result = inspect_duplicate_metadata(inputs)
        write_metadata_inspection_outputs(result, inputs.output_root, worktree=ROOT)
        safe = inputs.output_root / "aggregate_safe" / "duplicate_metadata_summary.json"
        payload = json.loads(safe.read_text())
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.study_id), serialized)
        self.assertNotIn(self.dicom, serialized)
        self.assertNotIn(self.npz, serialized)
        with self.assertRaises(FileExistsError):
            write_metadata_inspection_outputs(result, inputs.output_root, worktree=ROOT)


if __name__ == "__main__":
    unittest.main()

