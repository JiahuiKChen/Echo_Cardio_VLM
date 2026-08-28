from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase2f_finalize import (  # noqa: E402
    compare_nonimage_table,
    load_field_policy,
    normalized_json_leaf_paths,
    validate_alpha_selection,
    validate_confusion_matrices,
    validate_table_schema,
)


POLICY_PATH = ROOT / "configs/phase2f_finalizer_field_policy_v1.json"


def real_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def alpha_policy(input_status: str, old_alpha: float, new_alpha: float) -> dict:
    policy = real_policy()
    policy["selection_protocol"]["alpha_grid"] = [1.0, 10.0]
    policy["selection_protocol"]["expected_current_selected_alpha_changes"] = (
        []
        if old_alpha == new_alpha
        else [
            {
                "target": "tapse",
                "baseline_tier": "study_metadata",
                "model": "ridge",
                "historical_alpha": old_alpha,
                "corrected_alpha": new_alpha,
            }
        ]
    )
    policy["analysis_dependencies"]["study_metadata"]["input_status"] = input_status
    return policy


def alpha_frame(
    selected_alpha: float,
    mae_at_one: float,
    mae_at_ten: float,
    selected_override: list[bool] | None = None,
) -> pd.DataFrame:
    selected = selected_override or [selected_alpha == 1.0, selected_alpha == 10.0]
    return pd.DataFrame(
        {
            "target": ["tapse", "tapse"],
            "baseline_tier": ["study_metadata", "study_metadata"],
            "model": ["ridge", "ridge"],
            "alpha": [1.0, 10.0],
            "val_mae": [mae_at_one, mae_at_ten],
            "warning_count": [0, 0],
            "warning_messages": ["", ""],
            "selected": selected,
        }
    )


def binary_row(tp: int, fp: int, tn: int, fn: int, n: int = 2654) -> pd.DataFrame:
    positive = tp + fn
    return pd.DataFrame(
        {
            "target": ["lvot_vti"],
            "split": ["train"],
            "model": ["ridge"],
            "threshold_label": ["low_lt_20cm"],
            "threshold_value": [20.0],
            "n": [n],
            "prevalence": [positive / n],
            "predicted_positive_rate": [(tp + fp) / n],
            "accuracy": [(tp + tn) / n],
            "f1": [0.0],
            "sensitivity": [tp / positive],
            "specificity": [tn / (tn + fp)],
            "ppv": [tp / (tp + fp)],
            "npv": [tn / (tn + fn)],
            "auroc_continuous_score": [0.58],
            "average_precision_continuous_score": [0.39],
            "tp": [tp],
            "fp": [fp],
            "tn": [tn],
            "fn": [fn],
            "baseline_tier": ["demographics_plus_study_metadata"],
            "analysis_label": ["phase2_nonimage_baseline"],
        }
    )


class SelectionPolicyTests(unittest.TestCase):
    def test_selected_alpha_change_fails_for_reused_input(self) -> None:
        policy = alpha_policy("INPUT_UNCHANGED", 1.0, 10.0)
        with self.assertRaisesRegex(ValueError, "unchanged analysis"):
            validate_alpha_selection(
                alpha_frame(1.0, 1.0, 2.0),
                alpha_frame(10.0, 2.0, 1.0),
                policy,
            )

    def test_selected_alpha_change_passes_for_corrected_validation_optimum(self) -> None:
        policy = alpha_policy("INPUT_CHANGED", 1.0, 10.0)
        records = validate_alpha_selection(
            alpha_frame(1.0, 1.0, 2.0),
            alpha_frame(10.0, 2.0, 1.0),
            policy,
        )
        self.assertEqual(records[0]["corrected_selected_alpha"], 10.0)
        self.assertTrue(records[0]["corrected_alpha_is_deterministic_validation_optimum"])

    def test_alpha_grid_change_fails(self) -> None:
        policy = alpha_policy("INPUT_CHANGED", 1.0, 10.0)
        corrected = alpha_frame(10.0, 2.0, 1.0)
        corrected.loc[1, "alpha"] = 30.0
        with self.assertRaisesRegex(ValueError, "alpha grid"):
            validate_alpha_selection(alpha_frame(1.0, 1.0, 2.0), corrected, policy)

    def _assert_manifest_selection_change_fails(self, field: str, value: object) -> None:
        payload = real_policy()
        payload["selection_protocol"][field] = value
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "selection protocol changed"):
                load_field_policy(ROOT, path)

    def test_validation_split_change_fails(self) -> None:
        self._assert_manifest_selection_change_fails("selection_split", "test")

    def test_selection_metric_change_fails(self) -> None:
        self._assert_manifest_selection_change_fails("selection_metric", "r2")

    def test_test_driven_selection_fails(self) -> None:
        self._assert_manifest_selection_change_fails("test_data_used_for_selection", True)

    def test_more_than_one_selected_flag_fails(self) -> None:
        policy = alpha_policy("INPUT_CHANGED", 1.0, 10.0)
        corrected = alpha_frame(10.0, 2.0, 1.0, [True, True])
        with self.assertRaisesRegex(ValueError, "exactly one selected"):
            validate_alpha_selection(alpha_frame(1.0, 1.0, 2.0), corrected, policy)

    def test_no_selected_flag_fails(self) -> None:
        policy = alpha_policy("INPUT_CHANGED", 1.0, 10.0)
        corrected = alpha_frame(10.0, 2.0, 1.0, [False, False])
        with self.assertRaisesRegex(ValueError, "exactly one selected"):
            validate_alpha_selection(alpha_frame(1.0, 1.0, 2.0), corrected, policy)

    def test_selected_flag_must_match_validation_optimum(self) -> None:
        policy = alpha_policy("INPUT_CHANGED", 1.0, 10.0)
        corrected = alpha_frame(1.0, 2.0, 1.0)
        with self.assertRaisesRegex(ValueError, "validation optimum"):
            validate_alpha_selection(alpha_frame(1.0, 1.0, 2.0), corrected, policy)


class DerivedCountPolicyTests(unittest.TestCase):
    def test_confusion_cells_may_change_with_fixed_observed_counts(self) -> None:
        policy = real_policy()
        changes = validate_confusion_matrices(
            binary_row(3, 8, 1753, 890),
            binary_row(2, 7, 1754, 891),
            policy,
        )
        self.assertEqual(changes[0]["historical_observed_positive"], 893)
        self.assertEqual(changes[0]["corrected_observed_negative"], 1761)

    def test_changed_observed_positive_count_fails(self) -> None:
        policy = copy.deepcopy(real_policy())
        policy["expected_current_confusion_changes"] = []
        with self.assertRaisesRegex(ValueError, "observed-positive"):
            validate_confusion_matrices(
                binary_row(3, 8, 1753, 890),
                binary_row(2, 7, 1754, 890),
                policy,
            )

    def test_changed_cohort_n_fails(self) -> None:
        policy = copy.deepcopy(real_policy())
        policy["expected_current_confusion_changes"] = []
        with self.assertRaisesRegex(ValueError, "cohort N"):
            validate_confusion_matrices(
                binary_row(3, 8, 1753, 890),
                binary_row(2, 7, 1754, 891, n=2655),
                policy,
            )

    def test_unknown_integer_field_fails_closed(self) -> None:
        policy = real_policy()
        historical = binary_row(3, 8, 1753, 890)
        corrected = binary_row(2, 7, 1754, 891)
        historical["unknown_count"] = 1
        corrected["unknown_count"] = 2
        with self.assertRaisesRegex(ValueError, "unclassified fields"):
            validate_table_schema("binary_metrics", historical, corrected, policy)

    def test_allowlisted_derived_integer_is_reported(self) -> None:
        policy = real_policy()
        table = policy["nonimage_tables"]["binary_metrics"]
        comparison = compare_nonimage_table(
            self._write_temp_pair(
                binary_row(3, 8, 1753, 890),
                binary_row(2, 7, 1754, 891),
            )[0],
            self._paths[1],
            "binary_metrics",
            tuple(table["row_identity_fields"]),
            policy=policy,
            semantic_checks_complete=True,
        )
        tp = comparison.loc[comparison["metric"].eq("tp")].iloc[0]
        self.assertEqual(tp["policy_category"], "derived_integer_output")
        self.assertEqual(tp["status"], "ACCEPTED_DERIVED_CHANGE")
        self.assertTrue(bool(tp["difference_allowed"]))

    def _write_temp_pair(
        self, historical: pd.DataFrame, corrected: pd.DataFrame
    ) -> tuple[Path, Path]:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        self._paths = (root / "old.csv", root / "new.csv")
        historical.to_csv(self._paths[0], index=False)
        corrected.to_csv(self._paths[1], index=False)
        return self._paths


class SchemaPresencePolicyTests(unittest.TestCase):
    def test_declared_historical_only_field_is_classified_but_not_compared(self) -> None:
        policy = {
            "analysis_dependencies": {
                "study_metadata": {"input_status": "INPUT_CHANGED"}
            },
            "nonimage_tables": {
                "metrics": {
                    "row_identity_fields": [
                        "target",
                        "baseline_tier",
                        "split",
                        "model",
                    ],
                    "historical_only_fields": ["legacy_delta"],
                    "fields": {
                        "target": "identity_invariant",
                        "baseline_tier": "identity_invariant",
                        "split": "identity_invariant",
                        "model": "protocol_invariant",
                        "mae": "derived_float_output",
                        "legacy_delta": "derived_float_output",
                    },
                }
            },
        }
        keys = {
            "target": ["tapse"],
            "baseline_tier": ["study_metadata"],
            "split": ["test"],
            "model": ["ridge"],
        }
        historical = pd.DataFrame({**keys, "mae": [3.2], "legacy_delta": [0.4]})
        corrected = pd.DataFrame({**keys, "mae": [3.1]})
        validate_table_schema("metrics", historical, corrected, policy)
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            historical.to_csv(root / "old.csv", index=False)
            corrected.to_csv(root / "new.csv", index=False)
            compared = compare_nonimage_table(
                root / "old.csv",
                root / "new.csv",
                "metrics",
                tuple(policy["nonimage_tables"]["metrics"]["row_identity_fields"]),
                policy=policy,
                semantic_checks_complete=True,
            )
        self.assertEqual(compared["metric"].tolist(), ["mae"])
        self.assertNotIn("legacy_delta", set(compared["metric"]))
        with self.assertRaisesRegex(ValueError, "missing=\\['mae'\\]"):
            validate_table_schema(
                "metrics", historical, corrected.drop(columns=["mae"]), policy
            )
        with self.assertRaisesRegex(ValueError, "unknown=\\['new_metric'\\]"):
            validate_table_schema(
                "metrics", historical, corrected.assign(new_metric=1.0), policy
            )

        locked = real_policy()
        locked["nonimage_tables"]["metrics"]["historical_only_fields"].append(
            "mae"
        )
        with tempfile.TemporaryDirectory() as tempdir:
            policy_path = Path(tempdir) / "field_policy.json"
            policy_path.write_text(json.dumps(locked), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "locked allowlist"):
                load_field_policy(ROOT, policy_path)


class PacketSchemaTests(unittest.TestCase):
    def test_every_current_packet_field_is_classified(self) -> None:
        policy = real_policy()
        file_record = {
            "logical_role": "metrics",
            "path": "/restricted/example.csv",
            "row_count": 1,
            "sha256": "a" * 64,
            "size_bytes": 100,
        }
        packet = {
            "bootstrap_n": 2000,
            "bootstrap_seed": 1337,
            "correlations_computed_directly_from_saved_test_predictions": True,
            "historical_outputs_modified": False,
            "input_files": [file_record],
            "model_refit": False,
            "output_files": [file_record],
            "prediction_regeneration": False,
            "prediction_rows_modified": False,
            "schema_version": "fixture",
            "status": "HISTORICAL_AGGREGATES_COPIED_AND_CORRELATIONS_RECOMPUTED",
        }
        self.assertEqual(
            normalized_json_leaf_paths(packet),
            set(policy["comparison_packet_fields"]),
        )


if __name__ == "__main__":
    unittest.main()
