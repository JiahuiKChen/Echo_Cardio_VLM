from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.corrected_analysis import (  # noqa: E402
    BLOCKED_CORRECTED_ANALYSIS,
    CorrectedAggregationResult,
    Tier1BlockedError,
    _array_sha256,
    _continuous_metrics,
    build_corrected_aggregation,
    compare_original_corrected,
    write_corrected_aggregation,
)
from build_jdim_corrected_study_embeddings import validate_forensic_evidence_hash  # noqa: E402
from jdim_tier1.safety import sha256_file  # noqa: E402


class CorrectedAnalysisTests(unittest.TestCase):
    def test_corrected_aggregation_evidence_must_match_forensic_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence = root / "duplicate_forensics_rows.csv"
            provenance = root / "duplicate_forensics_summary.json"
            evidence.write_text("group_token\ng1\n", encoding="utf-8")
            provenance.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "restricted_artifact_sha256": {
                            "duplicate_forensics_rows.csv": sha256_file(evidence)
                        },
                    }
                ),
                encoding="utf-8",
            )
            validate_forensic_evidence_hash(evidence, provenance)
            evidence.write_text("group_token\ng2\n", encoding="utf-8")
            with self.assertRaisesRegex(Tier1BlockedError, "does not match"):
                validate_forensic_evidence_hash(evidence, provenance)

    def _clip_fixture(
        self,
        root: Path,
        manifest_order: list[int] | None = None,
    ) -> tuple[Path, Path, Path, Path, Path]:
        embeddings = np.asarray(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
                [2.0, 2.0],
                [3.0, 1.0],
            ],
            dtype=np.float32,
        )
        rows = pd.DataFrame(
            [
                {"embedding_idx": 0, "study_id": 100, "subject_id": 10, "npz_path": "/r/a.npz", "write_ok": True},
                {"embedding_idx": 1, "study_id": 100, "subject_id": 10, "npz_path": "/r/b.npz", "write_ok": True},
                {"embedding_idx": 2, "study_id": 100, "subject_id": 10, "npz_path": "/r/b.npz", "write_ok": True},
                {"embedding_idx": 3, "study_id": 101, "subject_id": 10, "npz_path": "/r/c.npz", "write_ok": True},
                {"embedding_idx": 4, "study_id": 200, "subject_id": 20, "npz_path": "/r/d.npz", "write_ok": True},
            ]
        )
        if manifest_order is not None:
            rows = rows.iloc[manifest_order].reset_index(drop=True)
        manifest = root / "clips.csv"
        npz = root / "clips.npz"
        decisions = root / "duplicate_forensics_rows.csv"
        rows.to_csv(manifest, index=False)
        np.savez_compressed(npz, embeddings=embeddings)
        frozen_manifest = root / "frozen_studies.csv"
        frozen_npz = root / "frozen_studies.npz"
        study_rows = []
        study_vectors = []
        indexed = rows.sort_values("embedding_idx", kind="mergesort")
        for study_idx, study_id in enumerate(sorted(indexed["study_id"].unique())):
            group = indexed[indexed["study_id"] == study_id]
            indices = group["embedding_idx"].to_numpy(dtype=int)
            vector = embeddings[indices].mean(axis=0).astype(np.float32)
            study_vectors.append(vector)
            study_rows.append(
                {
                    "study_idx": study_idx,
                    "study_id": study_id,
                    "subject_id": int(group["subject_id"].iloc[0]),
                    "n_clips": len(group),
                    "embedding_l2_norm": float(np.linalg.norm(vector)),
                }
            )
        pd.DataFrame(study_rows).to_csv(frozen_manifest, index=False)
        np.savez_compressed(frozen_npz, embeddings=np.stack(study_vectors))
        vector_hash = _array_sha256(embeddings[1])
        pd.DataFrame(
            [
                {
                    "group_token": "group-b",
                    "classification": "TRUE_DUPLICATE_EMBEDDING_ROWS",
                    "_batch": "batch_000",
                    "_manifest_row": 1,
                    "study_id": 100,
                    "subject_id": 10,
                    "npz_path": "/r/b.npz",
                    "embedding_idx": 1,
                    "embedding_vector_sha256": vector_hash,
                    "processed_array_sha256": "b" * 64,
                    "processed_array_selector": "frames",
                    "explicit_window_spec_json": "{}",
                    "dedup_keep_candidate": True,
                },
                {
                    "group_token": "group-b",
                    "classification": "TRUE_DUPLICATE_EMBEDDING_ROWS",
                    "_batch": "batch_000",
                    "_manifest_row": 2,
                    "study_id": 100,
                    "subject_id": 10,
                    "npz_path": "/r/b.npz",
                    "embedding_idx": 2,
                    "embedding_vector_sha256": vector_hash,
                    "processed_array_sha256": "b" * 64,
                    "processed_array_selector": "frames",
                    "explicit_window_spec_json": "{}",
                    "dedup_keep_candidate": False,
                },
            ]
        ).to_csv(decisions, index=False)
        return decisions, manifest, npz, frozen_manifest, frozen_npz

    def _build(self, root: Path, order: list[int] | None = None) -> CorrectedAggregationResult:
        decisions, manifest, npz, frozen_manifest, frozen_npz = self._clip_fixture(root, order)
        return build_corrected_aggregation(
            decisions, manifest, npz, frozen_manifest, frozen_npz
        )

    def test_deduplication_is_stable_under_manifest_row_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = self._build(Path(first_dir))
            second = self._build(Path(second_dir), [4, 2, 0, 3, 1])
            np.testing.assert_array_equal(
                first.corrected_clip_embeddings,
                second.corrected_clip_embeddings,
            )
            np.testing.assert_array_equal(
                first.corrected_study_embeddings,
                second.corrected_study_embeddings,
            )
            pd.testing.assert_frame_equal(
                first.corrected_clip_manifest,
                second.corrected_clip_manifest,
                check_dtype=False,
            )
            self.assertEqual(
                first.aggregate_change_counts.iloc[0]["n_removed_duplicate_rows"],
                1,
            )

    def test_multiple_studies_per_subject_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            result = self._build(Path(tempdir))
            subject_ten = result.corrected_study_manifest.query("subject_id == 10")
            self.assertEqual(set(subject_ten["study_id"]), {100, 101})
            self.assertEqual(len(result.corrected_study_manifest), 3)
            self.assertEqual(
                result.aggregate_change_counts.iloc[0]["n_embedding_changed_studies"],
                1,
            )

    def test_correction_requires_exact_replay_of_frozen_study_store(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decisions, manifest, npz, frozen_manifest, frozen_npz = self._clip_fixture(root)
            with np.load(frozen_npz) as archive:
                altered = np.asarray(archive["embeddings"]).copy()
            altered[0, 0] += np.float32(0.01)
            np.savez_compressed(frozen_npz, embeddings=altered)
            with self.assertRaisesRegex(Tier1BlockedError, "does not exactly reproduce") as caught:
                build_corrected_aggregation(
                    decisions, manifest, npz, frozen_manifest, frozen_npz
                )
            self.assertEqual(caught.exception.status, BLOCKED_CORRECTED_ANALYSIS)

    def test_existing_output_root_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            result = self._build(root)
            output = root / "corrected_v1"
            output.mkdir()
            marker = output / "original.txt"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                write_corrected_aggregation(result, output)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(list(output.iterdir()), [marker])

    @staticmethod
    def _prediction_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        original = pd.DataFrame(
            [
                ("lvot_vti", 1, 10, "train", 10.0, 11.0, 20.0),
                ("lvot_vti", 1, 11, "train", 12.0, 11.5, 20.0),
                ("lvot_vti", 2, 20, "val", 16.0, 15.0, 20.0),
                ("lvot_vti", 3, 30, "val", 18.0, 19.0, 20.0),
                ("lvot_vti", 4, 40, "test", 22.0, 21.0, 20.0),
                ("lvot_vti", 4, 41, "test", 24.0, 22.0, 20.0),
            ],
            columns=[
                "target",
                "subject_id",
                "study_id",
                "split",
                "target_value",
                "pred_ridge",
                "pred_null_median",
            ],
        )
        corrected = original.copy()
        corrected["pred_ridge"] = corrected["pred_ridge"] + np.asarray(
            [0.1, -0.1, 0.2, -0.2, 0.3, -0.3]
        )
        split_map = pd.DataFrame(
            {
                "subject_id": [1, 2, 3, 4],
                "split": ["train", "val", "val", "test"],
            }
        )
        return original, corrected, split_map

    @staticmethod
    def _write_metric_table(path: Path, predictions: pd.DataFrame) -> None:
        rows = []
        for split, group in predictions.groupby("split", sort=False):
            values = _continuous_metrics(
                group["target_value"].to_numpy(dtype=float),
                group["pred_ridge"].to_numpy(dtype=float),
            )
            rows.append({"target": "lvot_vti", "split": split, "model": "ridge", **values})
        pd.DataFrame(rows).to_csv(path / "imaging_baseline_metrics.csv", index=False)

    def _comparison_inputs(
        self,
        root: Path,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Path], dict[str, Path]]:
        original, corrected, split_map = self._prediction_frames()
        original_dir = root / "original"
        corrected_dir = root / "corrected"
        original_dir.mkdir()
        corrected_dir.mkdir()
        self._write_metric_table(original_dir, original)
        self._write_metric_table(corrected_dir, corrected)
        return (
            original,
            corrected,
            split_map,
            {"lvot_vti": original_dir},
            {"lvot_vti": corrected_dir},
        )

    @staticmethod
    def _run_summary(target: str = "lvot_vti") -> dict:
        target_summary = {
            "target": target,
            "clinical_unit": "cm",
            "numeric_rows_before_exclusions": 6,
            "hard_invalid_or_extreme_before_exclusions": 0,
            "numeric_rows": 6,
            "numeric_studies": 6,
            "numeric_subjects": 4,
            "studies_with_multiple_numeric_values": 0,
            "outside_primary_range_studies": 0,
            "hard_invalid_or_extreme_studies": 0,
            "hard_invalid_or_extreme_excluded": 0,
            "aggregation": "median per study",
            "analysis_label": "all_clips_study_embeddings_stable_v2",
            "joined_target_embedding_studies": 6,
            "joined_target_embedding_subjects": 4,
            "split_counts": {"train": 2, "val": 2, "test": 2},
            "status": "ok",
            "skip_reason": "",
            "ridge_solver": "svd",
            "features_standardized": True,
            "ridge_alpha_grid": [0.1, 1.0],
            "random_seed": 1337,
            "hard_extremes_excluded": False,
            "train_target_iqr": 1.0,
        }
        return {
            "target": target,
            "targets": [target],
            "clinical_units": {target: "cm"},
            "analysis_label": "all_clips_study_embeddings_stable_v2",
            "ridge_solver": "svd",
            "features_standardized": True,
            "ridge_alphas": [0.1, 1.0],
            "n_bootstrap": 2000,
            "bootstrap_unit": "subject",
            "random_seed": 1337,
            "legacy_seed_arg": 1337,
            "hard_extremes_excluded": False,
            "sklearn_version": "synthetic",
            "target_summaries": [target_summary],
        }

    def test_comparison_proves_unchanged_frozen_split_and_row_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            result = compare_original_corrected(
                {"lvot_vti": original},
                {"lvot_vti": corrected},
                split_map,
                old_dirs,
                new_dirs,
                original_run_summaries={"lvot_vti": self._run_summary()},
                corrected_run_summaries={"lvot_vti": self._run_summary()},
            )
            self.assertTrue(result.safe_provenance["row_identity_verified"])
            self.assertTrue(result.safe_provenance["frozen_split_verified"])
            self.assertTrue(result.safe_provenance["run_protocol_verified"])
            overall = result.prediction_changes.query("target == 'lvot_vti' and split == 'all'").iloc[0]
            self.assertEqual(overall["n_studies"], 6)
            self.assertEqual(overall["n_subjects"], 4)
            self.assertEqual(overall["n_predictions_changed_exact"], 6)
            self.assertNotIn("study_id", result.prediction_changes.columns)
            self.assertNotIn("subject_id", result.prediction_metrics.columns)

    def test_comparison_fails_closed_when_corrected_protocol_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            old_summary = self._run_summary()
            new_summary = self._run_summary()
            new_summary["ridge_alphas"] = [0.1, 1.0, 10.0]
            with self.assertRaisesRegex(Tier1BlockedError, "changed protocol fields") as caught:
                compare_original_corrected(
                    {"lvot_vti": original},
                    {"lvot_vti": corrected},
                    split_map,
                    old_dirs,
                    new_dirs,
                    original_run_summaries={"lvot_vti": old_summary},
                    corrected_run_summaries={"lvot_vti": new_summary},
                )
            self.assertEqual(caught.exception.status, BLOCKED_CORRECTED_ANALYSIS)

    def test_comparison_fails_closed_on_prediction_row_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            with self.assertRaisesRegex(Tier1BlockedError, "row identity differs") as caught:
                compare_original_corrected(
                    {"lvot_vti": original},
                    {"lvot_vti": corrected.iloc[:-1].copy()},
                    split_map,
                    old_dirs,
                    new_dirs,
                )
            self.assertEqual(caught.exception.status, BLOCKED_CORRECTED_ANALYSIS)

    def test_comparison_fails_closed_on_frozen_split_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            corrected = corrected.copy()
            corrected.loc[corrected["subject_id"] == 1, "split"] = "val"
            with self.assertRaisesRegex(Tier1BlockedError, "frozen split map") as caught:
                compare_original_corrected(
                    {"lvot_vti": original},
                    {"lvot_vti": corrected},
                    split_map,
                    old_dirs,
                    new_dirs,
                )
            self.assertEqual(caught.exception.status, BLOCKED_CORRECTED_ANALYSIS)

    def test_comparison_requires_finite_null_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            corrected = corrected.copy()
            corrected.loc[0, "pred_null_median"] = np.inf
            with self.assertRaisesRegex(Tier1BlockedError, "invalid null predictions"):
                compare_original_corrected(
                    {"lvot_vti": original},
                    {"lvot_vti": corrected},
                    split_map,
                    old_dirs,
                    new_dirs,
                )

    def test_comparison_requires_null_prediction_column(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original, corrected, split_map, old_dirs, new_dirs = self._comparison_inputs(
                Path(tempdir)
            )
            with self.assertRaisesRegex(Tier1BlockedError, "pred_null_median"):
                compare_original_corrected(
                    {"lvot_vti": original.drop(columns=["pred_null_median"])},
                    {"lvot_vti": corrected},
                    split_map,
                    old_dirs,
                    new_dirs,
                )


if __name__ == "__main__":
    unittest.main()
