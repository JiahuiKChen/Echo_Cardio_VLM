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

from phase2f_finalize import (  # noqa: E402
    compare_nonimage_table,
    compare_scalar_values,
    load_legacy_finalizer,
    patch_legacy_finalizer,
)


def synthetic_policy() -> dict:
    return {
        "analysis_dependencies": {
            "study_metadata": {
                "input_status": "INPUT_CHANGED",
            }
        },
        "nonimage_tables": {
            "metrics": {
                "row_identity_fields": [
                    "target",
                    "baseline_tier",
                    "split",
                    "model",
                ],
                "fields": {
                    "target": "identity_invariant",
                    "baseline_tier": "identity_invariant",
                    "split": "identity_invariant",
                    "model": "protocol_invariant",
                    "input_changed": "protocol_boolean",
                    "mae": "derived_float_output",
                },
            }
        },
    }


class Phase2FFinalizerTests(unittest.TestCase):
    def test_equal_python_booleans_pass(self) -> None:
        result = compare_scalar_values(True, True)
        self.assertTrue(result["within_policy"])
        self.assertEqual(result["comparison_type"], "boolean_exact")
        self.assertIsNone(result["delta_corrected_minus_original"])

    def test_equal_numpy_booleans_pass(self) -> None:
        result = compare_scalar_values(np.bool_(False), np.bool_(False))
        self.assertTrue(result["within_policy"])
        self.assertIsNone(result["delta_corrected_minus_original"])

    def test_unequal_numpy_booleans_fail(self) -> None:
        result = compare_scalar_values(np.bool_(False), np.bool_(True))
        self.assertFalse(result["within_policy"])

    def test_no_boolean_subtraction_occurs(self) -> None:
        result = compare_scalar_values(np.bool_(True), np.bool_(False))
        self.assertIsNone(result["delta_corrected_minus_original"])
        self.assertIs(result["original_value"], True)
        self.assertIs(result["corrected_value"], False)

    def test_integer_counts_are_exact(self) -> None:
        self.assertTrue(compare_scalar_values(np.int64(7), 7)["within_policy"])
        self.assertFalse(compare_scalar_values(np.int64(7), 8)["within_policy"])

    def test_float_under_tolerance_passes(self) -> None:
        self.assertTrue(compare_scalar_values(1.0, 1.0 + 1.9e-6)["within_policy"])

    def test_float_above_tolerance_fails(self) -> None:
        self.assertFalse(compare_scalar_values(1.0, 1.0 + 2.1e-6)["within_policy"])

    def test_hashes_and_labels_are_exact(self) -> None:
        self.assertTrue(compare_scalar_values("abc", "abc")["within_policy"])
        self.assertFalse(compare_scalar_values("abc", "abd")["within_policy"])

    def test_missing_and_false_are_not_equal(self) -> None:
        result = compare_scalar_values(np.nan, np.bool_(False))
        self.assertFalse(result["within_policy"])

    def test_boolean_mismatch_blocks_nonimage_table(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            base = {
                "target": ["lvot_vti"],
                "baseline_tier": ["study_metadata"],
                "split": ["test"],
                "model": ["ridge"],
            }
            original = pd.DataFrame(
                {**base, "input_changed": [False], "mae": [3.64]}
            )
            corrected = pd.DataFrame(
                {**base, "input_changed": [True], "mae": [3.64]}
            )
            original.to_csv(root / "old.csv", index=False)
            corrected.to_csv(root / "new.csv", index=False)
            with self.assertRaisesRegex(ValueError, "exact invariant changed"):
                compare_nonimage_table(
                    root / "old.csv",
                    root / "new.csv",
                    "metrics",
                    ("target", "baseline_tier", "split", "model"),
                    policy=synthetic_policy(),
                    semantic_checks_complete=True,
                )

    def test_preserved_finalizer_fixture_generates_certificate(self) -> None:
        fixture = ROOT / "tests/fixtures/phase2e_finalizer_boolean_fixture.py"
        module = load_legacy_finalizer(fixture)
        patch_legacy_finalizer(module, synthetic_policy())
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            frame = pd.DataFrame(
                {
                    "target": ["lvot_vti"],
                    "baseline_tier": ["study_metadata"],
                    "split": ["test"],
                    "model": ["ridge"],
                    "input_changed": [False],
                    "mae": [3.64],
                }
            )
            frame.to_csv(root / "old.csv", index=False)
            frame.to_csv(root / "new.csv", index=False)
            certificate = root / "certificate.json"
            module.write_completion_certificate(
                root / "old.csv", root / "new.csv", certificate
            )
            payload = json.loads(certificate.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FIXTURE_CERTIFICATE_COMPLETE")
            self.assertTrue(payload["boolean_exact_match"])


if __name__ == "__main__":
    unittest.main()
