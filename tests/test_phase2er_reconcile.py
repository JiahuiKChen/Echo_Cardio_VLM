from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/phase2er_reconcile.py"
SPEC = importlib.util.spec_from_file_location("phase2er_reconcile", DRIVER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to import {DRIVER}")
phase2er = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase2er
SPEC.loader.exec_module(phase2er)


class PacketDefinitionTests(unittest.TestCase):
    def test_six_echoview_packets_are_explicit_and_complete(self) -> None:
        definitions = phase2er.validate_packet_definitions()
        echoview = {
            item.normalized_label: item
            for item in definitions
            if item.normalized_label in phase2er.ECHOVIEW_PACKET_LABELS
        }
        self.assertEqual(set(echoview), phase2er.ECHOVIEW_PACKET_LABELS)
        self.assertNotIn(
            "a5c_only_070", {item.label for item in phase2er.SPECS}
        )
        for item in echoview.values():
            self.assertTrue(item.normalized_label)
            self.assertTrue(item.raw_label)
            self.assertTrue(item.original_rel)
            self.assertTrue(item.corrected_rel)
            self.assertTrue(item.expected_cohort_identity)
            self.assertTrue(item.expected_protocol_identity)

    def test_normalized_labels_must_be_unique(self) -> None:
        definitions = list(phase2er.PACKET_DEFINITIONS)
        definitions[-1] = dataclasses.replace(
            definitions[-1], normalized_label=definitions[-2].normalized_label
        )
        with self.assertRaisesRegex(phase2er.Blocked, "must be unique"):
            phase2er.validate_packet_definitions(definitions)

    def test_raw_and_normalized_labels_cannot_be_interchanged(self) -> None:
        definitions = list(phase2er.PACKET_DEFINITIONS)
        source = definitions[-1]
        definitions[-1] = dataclasses.replace(
            source,
            raw_label=source.normalized_label,
            normalized_label=source.raw_label,
        )
        with self.assertRaisesRegex(phase2er.Blocked, "normalized label is malformed"):
            phase2er.validate_packet_definitions(definitions)

    def test_095_cannot_be_constructed_with_an_omitted_field(self) -> None:
        source = next(
            item
            for item in phase2er.PACKET_DEFINITIONS
            if item.label == "a5c_or_other_095"
        )
        kwargs = dataclasses.asdict(source)
        kwargs.pop("normalized_label")
        with self.assertRaises(TypeError):
            phase2er.Spec(**kwargs)

    def test_packet_order_does_not_change_field_assignment(self) -> None:
        forward = phase2er.validate_packet_definitions()
        reverse = phase2er.validate_packet_definitions(reversed(forward))
        forward_map = {
            item.label: dataclasses.asdict(item)
            for item in forward
        }
        reverse_map = {
            item.label: dataclasses.asdict(item)
            for item in reverse
        }
        self.assertEqual(forward_map, reverse_map)

    def test_malformed_packet_fails_before_scientific_output_access(self) -> None:
        definitions = list(phase2er.PACKET_DEFINITIONS)
        definitions[-1] = dataclasses.replace(definitions[-1], normalized_label="")
        with mock.patch.object(
            phase2er.pd, "read_csv", side_effect=AssertionError("scientific read")
        ), mock.patch.object(
            phase2er, "load_json", side_effect=AssertionError("scientific read")
        ):
            with self.assertRaisesRegex(phase2er.Blocked, "empty normalized_label"):
                phase2er.validate_packet_definitions(definitions)

    def test_valid_definition_validation_loads_no_scientific_output(self) -> None:
        with mock.patch.object(
            phase2er.pd, "read_csv", side_effect=AssertionError("scientific read")
        ), mock.patch.object(
            phase2er, "load_json", side_effect=AssertionError("scientific read")
        ):
            validated = phase2er.validate_packet_definitions()
        self.assertEqual(len(validated), 10)


class ReconciliationPolicyTests(unittest.TestCase):
    def test_aggregate_safe_summary_contract_is_complete(self) -> None:
        required = {
            "policy",
            "n_test",
            "original_mae",
            "corrected_mae",
            "mae_difference_corrected_minus_original",
            "original_r2",
            "corrected_r2",
            "r2_difference_corrected_minus_original",
            "original_calibration_intercept",
            "reconstructed_historical_calibration_intercept",
            "corrected_calibration_intercept",
            "maximum_approved_scalar_reconstruction_discrepancy",
            "number_predictions_changed",
            "mean_absolute_prediction_change",
            "maximum_absolute_prediction_change",
            "original_corrected_prediction_correlation",
            "comparison_status",
        }
        self.assertTrue(
            required.issubset(set(phase2er.PREDICTION_COMPARISON_COLUMNS))
        )

    def test_fixed_tolerance_is_not_selected_from_observed_discrepancy(self) -> None:
        self.assertEqual(phase2er.approved_scalar_tolerance(0.0), 2e-6)
        self.assertEqual(
            phase2er.approved_scalar_tolerance(1.5318417361243064e-6),
            2e-6,
        )

    def test_discrepancy_above_fixed_tolerance_fails_closed(self) -> None:
        with self.assertRaises(phase2er.Blocked) as context:
            phase2er.approved_scalar_tolerance(2.0000001e-6)
        self.assertEqual(
            context.exception.status,
            "BLOCKED_RECONCILIATION_TOLERANCE_EXCEEDED",
        )


if __name__ == "__main__":
    unittest.main()
