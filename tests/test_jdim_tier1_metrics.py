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

from jdim_tier1.metrics import (  # noqa: E402
    PAIRED_NONIMAGE_UNAVAILABLE,
    calibration_parameters,
    compute_fixed_prediction_metrics,
    derive_training_tertiles,
    paired_delta_mae,
    strict_pair_nonimage,
    subject_bootstrap,
    validate_fixed_prediction_request,
    validate_prediction_file_schemas,
)


def imaging_frame(target: str = "lvot_vti") -> pd.DataFrame:
    rows = []
    train_values = [10, 12, 14, 16, 18, 20, 22, 24, 26]
    for index, value in enumerate(train_values):
        rows.append(
            {
                "target": target,
                "subject_id": 100 + index,
                "study_id": 1000 + index,
                "split": "train",
                "target_value": value,
                "pred_ridge": value + (-1 if index % 2 else 1),
                "pred_null_median": 18.0,
            }
        )
    for index, value in enumerate([13, 19, 25]):
        rows.append(
            {
                "target": target,
                "subject_id": 200 + index,
                "study_id": 2000 + index,
                "split": "val",
                "target_value": value,
                "pred_ridge": value + 0.5,
                "pred_null_median": 18.0,
            }
        )
    test_rows = [
        (300, 3000, 11.0, 12.0),
        (300, 3001, 18.0, 17.0),
        (301, 3002, 21.0, 20.0),
        (302, 3003, 27.0, 25.0),
    ]
    for subject, study, observed, predicted in test_rows:
        rows.append(
            {
                "target": target,
                "subject_id": subject,
                "study_id": study,
                "split": "test",
                "target_value": observed,
                "pred_ridge": predicted,
                "pred_null_median": 18.0,
            }
        )
    return pd.DataFrame(rows)


class FixedMetricTests(unittest.TestCase):
    def test_calibration_orientation_observed_on_predicted(self) -> None:
        predicted = np.asarray([1.0, 2.0, 3.0, 4.0])
        observed = 1.0 + 2.0 * predicted
        intercept, slope = calibration_parameters(observed, predicted)
        self.assertAlmostEqual(intercept, 1.0)
        self.assertAlmostEqual(slope, 2.0)

    def test_test_defined_tertiles_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "training split only"):
            derive_training_tertiles(imaging_frame(), source_split="test")

    def test_training_tertiles_and_test_range_outputs(self) -> None:
        result = compute_fixed_prediction_metrics(
            {"lvot_vti": imaging_frame()},
            n_bootstrap=50,
            seed=20260824,
        )
        boundaries = result.tertile_boundaries["targets"]["lvot_vti"]
        self.assertEqual(boundaries["source_split"], "train")
        self.assertFalse(result.tertile_boundaries["test_labels_used_to_define_boundaries"])
        self.assertEqual(set(result.range_error["range_group"]), {
            "lower_training_distribution_tertile",
            "middle_training_distribution_tertile",
            "upper_training_distribution_tertile",
        })

    def test_delta_mae_sign_positive_favors_imaging(self) -> None:
        row = paired_delta_mae(
            imaging_frame(),
            "pred_null_median",
            "null_median",
            "lvot_vti",
            n_bootstrap=50,
            seed=20260824,
        )
        self.assertGreater(row["delta_mae_comparator_minus_imaging"], 0)
        self.assertEqual(row["positive_value_favors"], "imaging_ridge")

    def test_subject_bootstrap_retains_all_studies_per_sampled_subject(self) -> None:
        test = imaging_frame().query("split == 'test'").copy()
        original_counts = test.groupby("subject_id").size().to_dict()

        def integrity(sample: pd.DataFrame) -> tuple[float]:
            valid = all(
                count % original_counts[subject] == 0
                for subject, count in sample.groupby("subject_id").size().items()
            )
            return (1.0 if valid else 0.0,)

        boot = subject_bootstrap(test, integrity, n_bootstrap=100, seed=7)
        self.assertEqual(boot.shape, (100, 1))
        self.assertTrue(np.all(boot[:, 0] == 1.0))

    def test_refit_and_alpha_requests_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbids"):
            validate_fixed_prediction_request({"refit": True, "ridge_alpha": 1.0})

    def test_strict_nonimage_pairing_succeeds_only_one_to_one(self) -> None:
        imaging = imaging_frame()
        test = imaging.query("split == 'test'").copy()
        comparator = test[["target", "subject_id", "study_id", "split", "target_value"]].copy()
        comparator["y_pred"] = comparator["target_value"] + 4.0
        from jdim_tier1.metrics import normalize_comparator_predictions, normalize_imaging_predictions

        normalized = normalize_comparator_predictions(comparator, "demographics")
        paired, reason = strict_pair_nonimage(
            normalize_imaging_predictions(imaging, "lvot_vti"),
            normalized,
            "lvot_vti",
        )
        self.assertEqual(reason, "")
        self.assertIsNotNone(paired)
        self.assertEqual(len(paired), 4)

    def test_nonimage_row_mismatch_returns_unavailable(self) -> None:
        imaging = imaging_frame()
        comparator = imaging.query("split == 'test'").iloc[:-1].copy()
        comparator = comparator[["target", "subject_id", "study_id", "split", "target_value"]]
        comparator["y_pred"] = comparator["target_value"]
        from jdim_tier1.metrics import normalize_comparator_predictions

        normalized = normalize_comparator_predictions(comparator, "metadata")
        paired, reason = strict_pair_nonimage(imaging, normalized, "lvot_vti")
        self.assertIsNone(paired)
        self.assertIn("study_key_mismatch_count", reason)

    def test_nonimage_target_mismatch_returns_unavailable_status(self) -> None:
        imaging = imaging_frame()
        comparator = imaging.query("split == 'test'").copy()
        comparator = comparator[["target", "subject_id", "study_id", "split", "target_value"]]
        comparator.loc[comparator.index[0], "target_value"] += 1
        comparator["y_pred"] = comparator["target_value"] + 3
        result = compute_fixed_prediction_metrics(
            {"lvot_vti": imaging},
            nonimage_predictions={"demographics": comparator},
            n_bootstrap=20,
            seed=20260824,
        )
        row = result.paired_delta_mae.query("comparator == 'demographics'").iloc[0]
        self.assertEqual(row["status"], PAIRED_NONIMAGE_UNAVAILABLE)
        self.assertEqual(row["reason"], "target_value_mismatch")

    def test_output_contains_no_row_level_keys(self) -> None:
        result = compute_fixed_prediction_metrics(
            {"lvot_vti": imaging_frame()},
            n_bootstrap=20,
            seed=20260824,
        )
        for frame in (result.calibration, result.range_error, result.paired_delta_mae):
            self.assertNotIn("study_id", frame.columns)
            self.assertNotIn("subject_id", frame.columns)
            self.assertNotIn("target_value", frame.columns)

    def test_prediction_schema_only_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            lvot = root / "lvot.csv"
            tapse = root / "tapse.csv"
            imaging_frame("lvot_vti").to_csv(lvot, index=False)
            imaging_frame("tapse").to_csv(tapse, index=False)
            payload = validate_prediction_file_schemas({"lvot_vti": lvot, "tapse": tapse})
            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["row_level_metrics_computed"])

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "compute_jdim_fixed_prediction_metrics.py"),
                    "--imaging-predictions",
                    f"lvot_vti={lvot}",
                    "--imaging-predictions",
                    f"tapse={tapse}",
                    "--output-dir",
                    str(root / "unused"),
                    "--restricted-inputs-acknowledged",
                    "--schema-only",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            cli_payload = json.loads(completed.stdout)
            self.assertEqual(cli_payload["status"], "ok")
            self.assertFalse(cli_payload["row_level_metrics_computed"])


if __name__ == "__main__":
    unittest.main()
