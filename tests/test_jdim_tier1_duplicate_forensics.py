from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.duplicate_forensics import (  # noqa: E402
    AMBIGUOUS_REQUIRES_AUTHOR_REVIEW,
    BLOCKED_DUPLICATE_SEMANTICS,
    KEY_GRANULARITY_TOO_COARSE,
    LEGITIMATE_DISTINCT_CLIPS,
    TRUE_DUPLICATE_EMBEDDING_ROWS,
    TRUE_DUPLICATE_MANIFEST_ROWS,
    BatchArtifacts,
    DuplicateForensicsInputs,
    analyze_duplicate_keys,
    write_duplicate_forensics_outputs,
)
from jdim_tier1.safety import (  # noqa: E402
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    sha256_file,
)


class DuplicateForensicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.input_root = self.root / "inputs"
        self.input_root.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_processed(
        self,
        name: str,
        frames: np.ndarray,
        sampled_indices: np.ndarray | None = None,
    ) -> Path:
        path = self.input_root / name
        payload = {
            "frames": np.asarray(frames, dtype=np.uint8),
            "source_num_frames": np.array([frames.shape[-4]], dtype=np.int32),
        }
        if sampled_indices is not None:
            payload["sampled_indices"] = np.asarray(sampled_indices, dtype=np.int32)
        np.savez_compressed(path, **payload)
        return path

    def _inputs(
        self,
        manifest_rows: list[dict],
        vectors: np.ndarray,
        extraction_rows: list[dict] | None = None,
        coarse_key_columns: tuple[str, ...] = ("study_id", "dicom_filepath"),
    ) -> DuplicateForensicsInputs:
        manifest_path = self.input_root / "embedding_manifest.csv"
        pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
        embedding_path = self.input_root / "embeddings.npz"
        np.savez_compressed(embedding_path, embeddings=np.asarray(vectors, dtype=np.float32))

        if extraction_rows is None:
            extraction_rows = []
            seen: set[tuple] = set()
            for row in manifest_rows:
                semantic = tuple(
                    row.get(column)
                    for column in (
                        "study_id",
                        "subject_id",
                        "dicom_filepath",
                        "npz_path",
                        "window_index",
                        "window_id",
                        "frame_start",
                        "frame_end",
                        "sop_instance_uid",
                        "dicom_id",
                    )
                )
                if semantic in seen:
                    continue
                seen.add(semantic)
                extraction_rows.append(
                    {
                        key: value
                        for key, value in {
                            **row,
                            "output_path": row.get("npz_path"),
                        }.items()
                        if key not in {"embedding_idx", "npz_path"}
                    }
                )
        extraction_path = self.input_root / "extraction_manifest.csv"
        pd.DataFrame(extraction_rows).to_csv(extraction_path, index=False)

        subjects = sorted({int(row["subject_id"]) for row in manifest_rows})
        split_path = self.input_root / "split.csv"
        pd.DataFrame(
            [
                {"subject_id": subject, "split": ("train", "val", "test")[index % 3]}
                for index, subject in enumerate(subjects)
            ]
        ).to_csv(split_path, index=False)

        studies = sorted({int(row["study_id"]) for row in manifest_rows})
        lvot_path = self.input_root / "lvot.csv"
        tapse_path = self.input_root / "tapse.csv"
        pd.DataFrame(
            [{"study_id": studies[0], "target_value": 999.0, "y_pred": -999.0}]
        ).to_csv(lvot_path, index=False)
        pd.DataFrame(
            [{"study_id": studies[-1], "target_value": -123.0, "error": 123.0}]
        ).to_csv(tapse_path, index=False)

        return DuplicateForensicsInputs(
            batches={
                "batch_000": BatchArtifacts(
                    extraction_manifest=extraction_path,
                    embedding_manifest=manifest_path,
                    embedding_npz=embedding_path,
                )
            },
            split_map=split_path,
            target_cohorts={"lvot_vti": lvot_path, "tapse": tapse_path},
            coarse_key_columns=coarse_key_columns,
        )

    def _classify(self, inputs: DuplicateForensicsInputs) -> tuple[str, object]:
        result = analyze_duplicate_keys(inputs)
        self.assertEqual(len(result.restricted_groups), 1)
        return str(result.restricted_groups.iloc[0]["classification"]), result

    def test_legitimate_multiple_clips_per_dicom_use_explicit_windows(self) -> None:
        first = self._write_processed("first.npz", np.zeros((4, 2, 2, 3), dtype=np.uint8))
        second = self._write_processed("second.npz", np.ones((4, 2, 2, 3), dtype=np.uint8))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(first),
                "window_index": 0,
                "embedding_idx": 0,
                "write_ok": True,
            },
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(second),
                "window_index": 1,
                "embedding_idx": 1,
                "write_ok": True,
            },
        ]
        classification, result = self._classify(self._inputs(rows, np.array([[1, 0], [0, 1]])))
        self.assertEqual(classification, LEGITIMATE_DISTINCT_CLIPS)
        self.assertEqual(result.status, "ok")
        self.assertFalse(bool(result.restricted_groups.iloc[0]["valid_dedup_rule"]))
        self.assertTrue(
            result.restricted_rows["source_manifest_row_fingerprint_sha256"]
            .astype(str)
            .str.fullmatch(r"[0-9a-f]{64}")
            .all()
        )

    def test_manifest_row_is_successful_embedding_ordinal(self) -> None:
        first = self._write_processed("ordinal_a.npz", np.zeros((4, 2, 2, 3)))
        second = self._write_processed("ordinal_b.npz", np.ones((4, 2, 2, 3)))
        rows = [
            {
                "study_id": 9,
                "subject_id": 90,
                "dicom_filepath": "failed.dcm",
                "npz_path": str(first),
                "embedding_idx": -1,
                "write_ok": False,
            },
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(first),
                "window_index": 0,
                "embedding_idx": 0,
                "write_ok": True,
            },
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(second),
                "window_index": 1,
                "embedding_idx": 1,
                "write_ok": True,
            },
        ]
        _, result = self._classify(self._inputs(rows, np.eye(2)))
        self.assertEqual(list(result.restricted_rows["_manifest_row"]), [0, 1])

    def test_shared_npz_with_distinct_explicit_windows_is_legitimate(self) -> None:
        windows = np.stack(
            [
                np.zeros((3, 2, 2, 3), dtype=np.uint8),
                np.full((3, 2, 2, 3), 7, dtype=np.uint8),
            ],
            axis=0,
        )
        shared = self._write_processed(
            "shared_windows.npz",
            windows,
            sampled_indices=np.array([[0, 1, 2], [3, 4, 5]]),
        )
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(shared),
                "window_index": index,
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        classification, result = self._classify(self._inputs(rows, np.array([[1, 2], [2, 1]])))
        group = result.restricted_groups.iloc[0]
        self.assertEqual(classification, LEGITIMATE_DISTINCT_CLIPS)
        self.assertEqual(int(group["unique_processed_input_hashes"]), 2)
        selectors = set(result.restricted_rows["processed_array_selector"])
        self.assertEqual(selectors, {"frames[0]", "frames[1]"})

    def test_true_manifest_duplicates_share_one_embedding_index(self) -> None:
        processed = self._write_processed("manifest_duplicate.npz", np.ones((3, 2, 2, 3)))
        row = {
            "study_id": 1,
            "subject_id": 10,
            "dicom_filepath": "same.dcm",
            "npz_path": str(processed),
            "embedding_idx": 0,
            "write_ok": True,
        }
        extraction = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "output_path": str(processed),
                "write_ok": True,
            }
        ]
        classification, result = self._classify(
            self._inputs([row, dict(row)], np.array([[1.0, 2.0]]), extraction)
        )
        self.assertEqual(classification, TRUE_DUPLICATE_MANIFEST_ROWS)
        self.assertTrue(bool(result.restricted_groups.iloc[0]["valid_dedup_rule"]))

    def test_true_embedding_duplicates_have_separate_indices_and_exact_vectors(self) -> None:
        duplicate = self._write_processed("duplicate.npz", np.ones((3, 2, 2, 3)))
        other = self._write_processed("other.npz", np.full((3, 2, 2, 3), 5))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(duplicate),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        rows.append(
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "other.dcm",
                "npz_path": str(other),
                "embedding_idx": 2,
                "write_ok": True,
            }
        )
        classification, result = self._classify(
            self._inputs(rows, np.array([[1.0, 1.0], [1.0, 1.0], [3.0, 3.0]]))
        )
        group = result.restricted_groups.iloc[0]
        self.assertEqual(classification, TRUE_DUPLICATE_EMBEDDING_ROWS)
        self.assertTrue(bool(group["all_vectors_exact_equal"]))
        self.assertEqual(int(result.summary["n_studies_evaluated_for_embedding_change"]), 1)
        self.assertEqual(int(result.summary["n_studies_whose_embedding_changes_under_valid_rule"]), 1)
        self.assertGreater(float(result.restricted_study_changes.iloc[0]["l2_distance"]), 0)

    def test_exact_vectors_do_not_override_distinct_window_provenance(self) -> None:
        first = self._write_processed("exact_a.npz", np.zeros((3, 2, 2, 3)))
        second = self._write_processed("exact_b.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(path),
                "window_id": f"window-{index}",
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, path in enumerate((first, second))
        ]
        classification, result = self._classify(
            self._inputs(rows, np.array([[0.5, 0.5], [0.5, 0.5]]))
        )
        self.assertEqual(classification, LEGITIMATE_DISTINCT_CLIPS)
        self.assertTrue(bool(result.restricted_groups.iloc[0]["any_exact_vector_pair"]))

    def test_near_equal_vectors_for_one_processed_input_are_embedding_duplicates(self) -> None:
        processed = self._write_processed("near.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(processed),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        vectors = np.array([[1.0, 2.0], [1.0 + 5e-7, 2.0 - 5e-7]], dtype=np.float64)
        classification, result = self._classify(self._inputs(rows, vectors))
        group = result.restricted_groups.iloc[0]
        self.assertEqual(classification, TRUE_DUPLICATE_EMBEDDING_ROWS)
        self.assertFalse(bool(group["all_vectors_exact_equal"]))
        self.assertTrue(bool(group["all_vectors_near_equal"]))
        self.assertTrue(bool(group["any_near_nonexact_vector_pair"]))

    def test_near_equal_keeper_is_stable_under_source_manifest_reordering(self) -> None:
        processed = self._write_processed("stable-near.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(processed),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        vectors = np.array([[1.0, 2.0], [1.0 + 5e-7, 2.0 - 5e-7]], dtype=np.float64)
        first = analyze_duplicate_keys(self._inputs(rows, vectors))
        first_keep = int(
            first.restricted_rows.loc[first.restricted_rows["dedup_keep_candidate"], "embedding_idx"].iloc[0]
        )
        second = analyze_duplicate_keys(self._inputs(list(reversed(rows)), vectors))
        second_keep = int(
            second.restricted_rows.loc[second.restricted_rows["dedup_keep_candidate"], "embedding_idx"].iloc[0]
        )
        self.assertEqual(first_keep, second_keep)

    def test_coarse_key_columns_use_explicit_lineage_allowlist(self) -> None:
        processed = self._write_processed("unsafe-key.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "outcome": "same",
                "dicom_filepath": "same.dcm",
                "npz_path": str(processed),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        with self.assertRaisesRegex(ValueError, "coarse key cannot use"):
            analyze_duplicate_keys(
                self._inputs(rows, np.ones((2, 2)), coarse_key_columns=("study_id", "outcome"))
            )

    def test_finer_dicom_identity_can_show_coarse_key_collision(self) -> None:
        first = self._write_processed("fine_a.npz", np.zeros((3, 2, 2, 3)))
        second = self._write_processed("fine_b.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_id": "coarse-name",
                "dicom_filepath": f"instance-{index}.dcm",
                "sop_instance_uid": f"1.2.3.{index}",
                "npz_path": str(path),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, path in enumerate((first, second))
        ]
        classification, result = self._classify(
            self._inputs(
                rows,
                np.array([[1.0, 0.0], [0.0, 1.0]]),
                coarse_key_columns=("study_id", "dicom_id"),
            )
        )
        self.assertEqual(classification, KEY_GRANULARITY_TOO_COARSE)
        self.assertEqual(result.restricted_groups.iloc[0]["fine_identity_column"], "sop_instance_uid")

    def test_missing_processed_provenance_blocks_and_writes_only_aggregate_safe_data(self) -> None:
        missing = self.input_root / "missing.npz"
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(missing),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        extraction = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "output_path": str(missing),
                "write_ok": True,
            }
        ]
        classification, result = self._classify(
            self._inputs(rows, np.array([[1.0, 2.0], [1.0, 2.0]]), extraction)
        )
        self.assertEqual(classification, AMBIGUOUS_REQUIRES_AUTHOR_REVIEW)
        self.assertEqual(result.status, BLOCKED_DUPLICATE_SEMANTICS)

        restricted = self.root / "restricted" / "forensics"
        safe = self.root / "aggregate_safe" / "forensics"
        with self.assertRaisesRegex(Tier1BlockedError, BLOCKED_DUPLICATE_SEMANTICS):
            write_duplicate_forensics_outputs(result, restricted, safe, worktree=ROOT)
        self.assertTrue((restricted / "duplicate_forensics_rows.csv").exists())
        self.assertTrue((safe / "duplicate_forensics_summary.json").exists())
        payload = json.loads((safe / "duplicate_forensics_summary.json").read_text())
        self.assertEqual(payload["status"], BLOCKED_DUPLICATE_SEMANTICS)
        self.assertEqual(
            payload["restricted_artifact_sha256"]["duplicate_forensics_rows.csv"],
            sha256_file(restricted / "duplicate_forensics_rows.csv"),
        )
        safe_text = "\n".join(path.read_text() for path in safe.iterdir())
        self.assertNotIn(str(self.root), safe_text)
        for forbidden in ("study_id", "subject_id", "dicom_filepath", "npz_path", "target_value", "y_pred"):
            self.assertNotIn(forbidden, safe_text)

    def test_restricted_output_inside_worktree_fails_before_writing(self) -> None:
        first = self._write_processed("safe_a.npz", np.zeros((3, 2, 2, 3)))
        second = self._write_processed("safe_b.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(path),
                "window_index": index,
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, path in enumerate((first, second))
        ]
        _, result = self._classify(self._inputs(rows, np.eye(2)))
        unsafe = ROOT / "restricted_forensics_should_not_exist"
        with self.assertRaisesRegex(Tier1BlockedError, BLOCKED_UNSAFE_OUTPUT):
            write_duplicate_forensics_outputs(
                result,
                restricted_output_dir=unsafe,
                safe_output_dir=self.root / "safe",
                worktree=ROOT,
            )
        self.assertFalse(unsafe.exists())

    def test_safe_output_inside_worktree_is_also_rejected(self) -> None:
        first = self._write_processed("safe_root_a.npz", np.zeros((3, 2, 2, 3)))
        second = self._write_processed("safe_root_b.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(path),
                "window_index": index,
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, path in enumerate((first, second))
        ]
        _, result = self._classify(self._inputs(rows, np.eye(2)))
        unsafe = ROOT / "safe_forensics_should_not_exist"
        with self.assertRaisesRegex(Tier1BlockedError, BLOCKED_UNSAFE_OUTPUT):
            write_duplicate_forensics_outputs(
                result,
                restricted_output_dir=self.root / "restricted_valid",
                safe_output_dir=unsafe,
                worktree=ROOT,
            )
        self.assertFalse(unsafe.exists())

    def test_existing_forensic_output_is_not_overwritten(self) -> None:
        first = self._write_processed("collision_a.npz", np.zeros((3, 2, 2, 3)))
        second = self._write_processed("collision_b.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(path),
                "window_index": index,
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, path in enumerate((first, second))
        ]
        _, result = self._classify(self._inputs(rows, np.eye(2)))
        restricted = self.root / "collision_restricted"
        safe = self.root / "collision_safe"
        safe.mkdir()
        existing = safe / "duplicate_forensics_summary.json"
        existing.write_text("preserve me\n")
        with self.assertRaisesRegex(Tier1BlockedError, "will not be overwritten"):
            write_duplicate_forensics_outputs(result, restricted, safe, worktree=ROOT)
        self.assertEqual(existing.read_text(), "preserve me\n")
        self.assertFalse(restricted.exists())

    def test_only_repeated_rows_trigger_processed_npz_inspection(self) -> None:
        repeated = self._write_processed("repeated_only.npz", np.ones((3, 2, 2, 3)))
        unique = self._write_processed("must_not_be_inspected.npz", np.full((3, 2, 2, 3), 9))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(repeated),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        rows.append(
            {
                "study_id": 2,
                "subject_id": 20,
                "dicom_filepath": "unique.dcm",
                "npz_path": str(unique),
                "embedding_idx": 2,
                "write_ok": True,
            }
        )
        _, result = self._classify(
            self._inputs(rows, np.array([[1.0, 1.0], [1.0, 1.0], [2.0, 2.0]]))
        )
        self.assertEqual(int(result.summary["n_processed_npz_inputs_inspected"]), 1)

    def test_target_or_prediction_column_cannot_enter_coarse_key(self) -> None:
        processed = self._write_processed("forbidden_key.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "target_value": 17.0,
                "npz_path": str(processed),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        inputs = self._inputs(
            rows,
            np.array([[1.0, 1.0], [1.0, 1.0]]),
            coarse_key_columns=("study_id", "target_value"),
        )
        with self.assertRaisesRegex(ValueError, "cannot use targets"):
            analyze_duplicate_keys(inputs)

    def test_no_repeated_keys_produces_empty_fixed_schema_outputs(self) -> None:
        processed = self._write_processed("unique_only.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "unique.dcm",
                "npz_path": str(processed),
                "embedding_idx": 0,
                "write_ok": True,
            }
        ]
        result = analyze_duplicate_keys(self._inputs(rows, np.array([[1.0, 2.0]])))
        self.assertEqual(result.status, "ok")
        self.assertEqual(int(result.summary["n_repeated_coarse_groups"]), 0)
        self.assertEqual(int(result.summary["n_processed_npz_inputs_inspected"]), 0)
        self.assertTrue(result.restricted_rows.empty)
        self.assertIn("embedding_changed", result.restricted_study_changes.columns)

    def test_cli_writes_restricted_and_safe_outputs(self) -> None:
        processed = self._write_processed("cli.npz", np.ones((3, 2, 2, 3)))
        rows = [
            {
                "study_id": 1,
                "subject_id": 10,
                "dicom_filepath": "same.dcm",
                "npz_path": str(processed),
                "embedding_idx": index,
                "write_ok": True,
            }
            for index in (0, 1)
        ]
        inputs = self._inputs(rows, np.array([[1.0, 2.0], [1.0, 2.0]]))
        artifacts = inputs.batches["batch_000"]
        restricted = self.root / "cli_restricted"
        safe = self.root / "cli_safe"
        command = [
            sys.executable,
            str(ROOT / "scripts" / "forensic_jdim_duplicate_keys.py"),
            "--extraction-manifest",
            f"batch_000={artifacts.extraction_manifest}",
            "--embedding-manifest",
            f"batch_000={artifacts.embedding_manifest}",
            "--embedding-npz",
            f"batch_000={artifacts.embedding_npz}",
            "--subject-split-map-csv",
            str(inputs.split_map),
            "--target-cohort",
            f"lvot_vti={inputs.target_cohorts['lvot_vti']}",
            "--target-cohort",
            f"tapse={inputs.target_cohorts['tapse']}",
            "--restricted-output-dir",
            str(restricted),
            "--safe-output-dir",
            str(safe),
        ]
        completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["classification_counts"][TRUE_DUPLICATE_EMBEDDING_ROWS], 1)
        self.assertTrue((restricted / "duplicate_forensics_groups.csv").exists())
        self.assertTrue((safe / "duplicate_class_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
