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

from jdim_tier1.audit import (  # noqa: E402
    STUDY_OUTCOMES,
    aggregate_audit_annotations,
    agreement_statistics,
    build_adjudication_queue,
    build_audit_sample,
    largest_remainder_allocation,
    load_audit_config,
    validate_allocation_override,
    write_audit_sample,
)
from jdim_tier1.safety import (  # noqa: E402
    Tier1BlockedError,
    assert_export_safe_columns,
    require_restricted_destination,
)


CONFIG_PATH = ROOT / "configs" / "jdim_input_content_audit_v1.yaml"


def make_cohorts() -> dict[str, pd.DataFrame]:
    lvot_rows = []
    for study in range(1, 13):
        split = "train" if study <= 7 else "val" if study <= 9 else "test"
        lvot_rows.append({"study_id": study, "subject_id": 100 + study, "split": split})
    tapse_rows = []
    for study in range(8, 18):
        split = "train" if study <= 13 else "val" if study <= 15 else "test"
        tapse_rows.append({"study_id": study, "subject_id": 100 + study, "split": split})
    return {"lvot_vti": pd.DataFrame(lvot_rows), "tapse": pd.DataFrame(tapse_rows)}


class AuditSamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config, self.config_hash = load_audit_config(CONFIG_PATH)
        self.cohorts = make_cohorts()

    def test_proportional_largest_remainder(self) -> None:
        allocation = largest_remainder_allocation({"train": 70, "val": 15, "test": 15}, 60)
        self.assertEqual(allocation, {"train": 42, "val": 9, "test": 9})

    def test_overlap_is_deduplicated_for_physical_review(self) -> None:
        result = build_audit_sample(
            self.cohorts,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=20,
        )
        self.assertEqual(len(result.linkage), 17)
        self.assertEqual(result.safe_summary["cross_target_overlap_studies"], 5)
        self.assertEqual(list(result.reader_manifest.columns), ["audit_id", "review_order"])
        self.assertNotIn("study_id", result.reader_manifest.columns)
        self.assertNotIn("split", result.reader_manifest.columns)

    def test_sampling_is_deterministic(self) -> None:
        first = build_audit_sample(
            self.cohorts,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=6,
        )
        second = build_audit_sample(
            self.cohorts,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=6,
        )
        pd.testing.assert_frame_equal(first.linkage, second.linkage)

    def test_author_override_requires_approval_and_respects_counts(self) -> None:
        counts = {
            target: frame["split"].value_counts().to_dict()
            for target, frame in self.cohorts.items()
        }
        with self.assertRaisesRegex(ValueError, "author_approved"):
            validate_allocation_override(
                {"author_approved": False, "allocations": {}},
                ["lvot_vti", "tapse"],
                counts,
            )
        override = {
            "author_approved": True,
            "allocations": {
                "lvot_vti": {"train": 1, "val": 1, "test": 1},
                "tapse": {"train": 2, "val": 1, "test": 1},
            },
        }
        result = build_audit_sample(
            self.cohorts,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            allocation_override=override,
        )
        design = result.sampling_design.set_index(["target", "split"])
        self.assertEqual(float(design.loc[("lvot_vti", "train"), "design_weight"]), 7.0)
        self.assertEqual(result.safe_summary["allocation_method"], "author_approved_override")

    def test_technical_pilot_has_no_clinical_content_fields(self) -> None:
        result = build_audit_sample(
            self.cohorts,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            technical_pilot=True,
            pilot_n_per_target=2,
        )
        self.assertTrue(result.technical_pilot)
        self.assertEqual(
            set(result.study_template.columns),
            {"audit_id", "review_order", "reconstruction_success", "review_minutes"},
        )
        self.assertTrue(set(STUDY_OUTCOMES).isdisjoint(result.study_template.columns))
        self.assertTrue(result.safe_summary["excluded_from_prevalence_estimates"])

    def test_unsafe_restricted_output_path_is_rejected(self) -> None:
        with self.assertRaises(Tier1BlockedError):
            require_restricted_destination(ROOT / "restricted_rows.csv", worktree=ROOT)

    def test_export_safe_schema_rejects_identifiers_and_paths(self) -> None:
        with self.assertRaises(Tier1BlockedError):
            assert_export_safe_columns(["target", "n_studies", "study_id"], "bad table")
        with self.assertRaises(Tier1BlockedError):
            assert_export_safe_columns(["logical_role", "restricted_path"], "bad manifest")


class AuditAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config, self.config_hash = load_audit_config(CONFIG_PATH)
        self.linkage = pd.DataFrame(
            [
                {
                    "audit_id": "A1",
                    "target_membership": "lvot_vti",
                    "target_strata": "lvot_vti:train",
                },
                {
                    "audit_id": "A2",
                    "target_membership": "lvot_vti",
                    "target_strata": "lvot_vti:test",
                },
            ]
        )
        self.design = pd.DataFrame(
            [
                {"target": "lvot_vti", "split": "train", "source_n": 4, "sample_n": 1, "design_weight": 4.0},
                {"target": "lvot_vti", "split": "test", "source_n": 1, "sample_n": 1, "design_weight": 1.0},
            ]
        )
        base_values = {outcome: "no" for outcome in STUDY_OUTCOMES}
        self.study = pd.DataFrame(
            [
                {"audit_id": "A1", "reader_id": "R1", "reader_role": "primary", **base_values, "spectral_doppler_present": "yes"},
                {"audit_id": "A1", "reader_id": "R2", "reader_role": "secondary", **base_values, "spectral_doppler_present": "yes"},
                {"audit_id": "A2", "reader_id": "R1", "reader_role": "primary", **base_values},
                {"audit_id": "A2", "reader_id": "R2", "reader_role": "secondary", **base_values},
            ]
        )
        clip_rows = []
        for index in range(10):
            clip_rows.append(
                {
                    "audit_id": "A1",
                    "clip_audit_id": f"A1C{index}",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    "waveform_or_tracing": "yes",
                    "candidate_target_value": 18.0,
                }
            )
        clip_rows.append(
            {
                "audit_id": "A2",
                "clip_audit_id": "A2C1",
                "reader_id": "R1",
                "reader_role": "primary",
                "waveform_or_tracing": "no",
                "candidate_target_value": 20.0,
            }
        )
        self.clips = pd.DataFrame(clip_rows)

    def test_design_weighted_study_estimate(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            n_bootstrap=50,
        )
        row = result.study_summary.query(
            "target == 'lvot_vti' and stratum == 'design_weighted_overall' and outcome == 'spectral_doppler_present'"
        ).iloc[0]
        self.assertAlmostEqual(float(row["proportion"]), 0.8)
        self.assertEqual(row["ci_method"], "design_weighted_wilson_effective_n")

    def test_sparse_agreement_reports_gwet_ac1(self) -> None:
        stats = agreement_statistics(["no", "no", "no"], ["no", "no", "no"])
        self.assertEqual(stats["raw_agreement"], 1.0)
        self.assertIsNone(stats["cohen_kappa"])
        self.assertEqual(stats["gwet_ac1"], 1.0)

    def test_clip_summary_uses_study_clusters(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            n_bootstrap=50,
        )
        row = result.clip_summary.query("target == 'lvot_vti' and outcome == 'waveform_or_tracing'").iloc[0]
        self.assertEqual(int(row["n_clips"]), 11)
        self.assertEqual(int(row["n_studies"]), 2)
        self.assertAlmostEqual(float(row["proportion"]), 10 / 11)
        self.assertEqual(row["ci_method"], "study_cluster_percentile_bootstrap")

    def test_candidate_values_never_enter_safe_outputs(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            n_bootstrap=20,
        )
        combined_columns = set(result.study_summary.columns) | set(result.clip_summary.columns) | set(result.agreement.columns)
        self.assertNotIn("candidate_target_value", combined_columns)
        self.assertFalse(result.summary["candidate_values_exported"])

    def test_invalid_annotation_category_is_rejected(self) -> None:
        bad = self.study.copy()
        bad.loc[0, "m_mode_present"] = "probably"
        with self.assertRaisesRegex(ValueError, "Invalid values"):
            aggregate_audit_annotations(
                bad,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                n_bootstrap=10,
            )

    def test_positive_uncertain_and_discordant_findings_enter_adjudication_queue(self) -> None:
        study = self.study.copy()
        study.loc[(study["audit_id"] == "A2") & (study["reader_id"] == "R2"), "m_mode_present"] = "uncertain"
        queue = build_adjudication_queue(study, self.clips)
        self.assertTrue(((queue["audit_id"] == "A1") & (queue["outcome"] == "spectral_doppler_present")).any())
        row = queue[(queue["audit_id"] == "A2") & (queue["outcome"] == "m_mode_present")].iloc[0]
        self.assertIn("positive_or_uncertain", row["adjudication_reason"])
        self.assertIn("reader_disagreement", row["adjudication_reason"])


if __name__ == "__main__":
    unittest.main()
