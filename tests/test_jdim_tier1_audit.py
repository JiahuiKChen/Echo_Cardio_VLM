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

from jdim_tier1.audit import (  # noqa: E402
    CLIP_PRESENCE_FIELDS,
    SAMPLE_TOKEN_COLUMN,
    STUDY_OUTCOMES,
    _sample_manifest_token,
    aggregate_audit_annotations,
    agreement_statistics,
    build_adjudication_queue,
    build_audit_sample,
    derive_post_unblinding_value_matches,
    largest_remainder_allocation,
    load_audit_config,
    load_manual_audit_completion,
    validate_allocation_override,
    write_audit_aggregates,
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
        lvot_rows.append(
            {"study_id": study, "subject_id": 100 + study, "split": split, "target_value": 10.0 + study}
        )
    tapse_rows = []
    for study in range(8, 18):
        split = "train" if study <= 13 else "val" if study <= 15 else "test"
        tapse_rows.append(
            {"study_id": study, "subject_id": 100 + study, "split": split, "target_value": 12.0 + study}
        )
    return {"lvot_vti": pd.DataFrame(lvot_rows), "tapse": pd.DataFrame(tapse_rows)}


def make_canonical_clips(cohorts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    studies = (
        pd.concat(
            [frame[["study_id", "subject_id"]] for frame in cohorts.values()],
            ignore_index=True,
        )
        .drop_duplicates("study_id")
        .sort_values("study_id")
    )
    rows: list[dict] = []
    embedding_idx = 0
    for study in studies.itertuples(index=False):
        for clip_index in range(2):
            rows.append(
                {
                    "study_id": study.study_id,
                    "subject_id": study.subject_id,
                    "embedding_idx": embedding_idx,
                    "canonical_clip_id": f"clip-{study.study_id}-{clip_index}",
                    "write_ok": True,
                }
            )
            embedding_idx += 1
    return pd.DataFrame(rows)


class AuditSamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config, self.config_hash = load_audit_config(CONFIG_PATH)
        self.cohorts = make_cohorts()
        self.canonical_clips = make_canonical_clips(self.cohorts)

    def test_proportional_largest_remainder(self) -> None:
        allocation = largest_remainder_allocation({"train": 70, "val": 15, "test": 15}, 60)
        self.assertEqual(allocation, {"train": 42, "val": 9, "test": 9})

    def test_config_validation_cli_integration(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "prepare_jdim_input_audit.py"),
                "validate-config",
                "--config",
                str(CONFIG_PATH),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["configuration_sha256"], self.config_hash)

    def test_overlap_is_deduplicated_for_physical_review(self) -> None:
        result = build_audit_sample(
            self.cohorts,
            self.canonical_clips,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=20,
        )
        self.assertEqual(len(result.linkage), 17)
        self.assertEqual(result.safe_summary["cross_target_overlap_studies"], 5)
        self.assertEqual(
            list(result.reader_manifest.columns),
            ["audit_id", "review_order", SAMPLE_TOKEN_COLUMN],
        )
        self.assertNotIn("study_id", result.reader_manifest.columns)
        self.assertNotIn("split", result.reader_manifest.columns)
        overlapping = result.linkage[result.linkage["target_membership"] == "lvot_vti;tapse"].iloc[0]
        self.assertIn("lvot_vti_target_value", result.linkage.columns)
        self.assertIn("tapse_target_value", result.linkage.columns)
        self.assertTrue(pd.notna(overlapping["lvot_vti_target_value"]))
        self.assertTrue(pd.notna(overlapping["tapse_target_value"]))
        self.assertNotIn("lvot_vti_target_value", result.reader_manifest.columns)
        self.assertEqual(
            result.safe_summary[SAMPLE_TOKEN_COLUMN],
            _sample_manifest_token(
                result.linkage,
                result.sampling_design,
                result.clip_roster,
            ),
        )
        with tempfile.TemporaryDirectory() as tempdir:
            linkage_path = Path(tempdir) / "linkage.csv"
            design_path = Path(tempdir) / "design.csv"
            roster_path = Path(tempdir) / "clip_roster.csv"
            result.linkage.to_csv(linkage_path, index=False)
            result.sampling_design.to_csv(design_path, index=False)
            result.clip_roster.to_csv(roster_path, index=False)
            self.assertEqual(
                result.safe_summary[SAMPLE_TOKEN_COLUMN],
                _sample_manifest_token(
                    pd.read_csv(linkage_path),
                    pd.read_csv(design_path),
                    pd.read_csv(roster_path),
                ),
            )
        self.assertEqual(
            result.safe_summary["primary_clip_reads_expected"],
            len(result.clip_roster),
        )

    def test_sampling_is_deterministic(self) -> None:
        first = build_audit_sample(
            self.cohorts,
            self.canonical_clips,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=6,
        )
        second = build_audit_sample(
            self.cohorts,
            self.canonical_clips,
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
            self.canonical_clips,
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
            self.canonical_clips,
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

    def test_audit_sample_outputs_are_immutable(self) -> None:
        result = build_audit_sample(
            self.cohorts,
            self.canonical_clips,
            self.config,
            self.config_hash,
            b"synthetic-secret-key-123",
            sample_size_per_target=4,
        )
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            restricted = root / "restricted" / "sample"
            safe = root / "aggregate_safe" / "sample"
            write_audit_sample(result, restricted, safe)
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                write_audit_sample(result, restricted, safe)

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
        self.clip_roster = pd.DataFrame(
            [
                {"audit_id": "A1", "clip_audit_id": f"A1C{index}"}
                for index in range(10)
            ]
            + [{"audit_id": "A2", "clip_audit_id": "A2C1"}]
        )
        self.sample_token = _sample_manifest_token(
            self.linkage,
            self.design,
            self.clip_roster,
        )
        self.second_readers = pd.DataFrame(
            [
                {"audit_id": "A1", SAMPLE_TOKEN_COLUMN: self.sample_token},
            ]
        )
        base_values = {outcome: "no" for outcome in STUDY_OUTCOMES}
        base_values["reader_confidence"] = "high"
        self.study = pd.DataFrame(
            [
                {
                    "audit_id": "A1",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    SAMPLE_TOKEN_COLUMN: self.sample_token,
                    **base_values,
                    "spectral_doppler_present": "yes",
                },
                {
                    "audit_id": "A1",
                    "reader_id": "R2",
                    "reader_role": "secondary",
                    SAMPLE_TOKEN_COLUMN: self.sample_token,
                    **base_values,
                    "spectral_doppler_present": "yes",
                },
                {
                    "audit_id": "A2",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    SAMPLE_TOKEN_COLUMN: self.sample_token,
                    **base_values,
                },
            ]
        )
        clip_rows = []
        clip_defaults = {field: "no" for field in CLIP_PRESENCE_FIELDS}
        clip_defaults["reconstruction_success"] = "yes"
        for index in range(10):
            clip_rows.append(
                {
                    "audit_id": "A1",
                    "clip_audit_id": f"A1C{index}",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    SAMPLE_TOKEN_COLUMN: self.sample_token,
                    "acquisition_content_type": "2d_b_mode",
                    "reader_confidence": "high",
                    **clip_defaults,
                    "waveform_or_tracing": "yes",
                    "candidate_target_value": 18.0,
                }
            )
            clip_rows.append(
                {
                    "audit_id": "A1",
                    "clip_audit_id": f"A1C{index}",
                    "reader_id": "R2",
                    "reader_role": "secondary",
                    SAMPLE_TOKEN_COLUMN: self.sample_token,
                    "acquisition_content_type": "2d_b_mode",
                    "reader_confidence": "high",
                    **clip_defaults,
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
                SAMPLE_TOKEN_COLUMN: self.sample_token,
                "acquisition_content_type": "m_mode",
                "reader_confidence": "high",
                **clip_defaults,
                "waveform_or_tracing": "no",
                "candidate_target_value": 20.0,
            }
        )
        self.clips = pd.DataFrame(clip_rows)
        self.adjudication = build_adjudication_queue(self.study, self.clips)
        self.adjudication["adjudicated_value"] = self.adjudication["reader_values"].map(
            lambda value: "yes" if "yes" in str(value).split(";") else "no"
        )
        self.adjudication["adjudicator_id"] = "ADJ1"

    def test_design_weighted_study_estimate(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            self.second_readers,
            self.clip_roster,
            self.adjudication,
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
            self.second_readers,
            self.clip_roster,
            self.adjudication,
            n_bootstrap=50,
        )
        row = result.clip_summary.query("target == 'lvot_vti' and outcome == 'waveform_or_tracing'").iloc[0]
        self.assertEqual(int(row["n_clips"]), 11)
        self.assertEqual(int(row["n_studies"]), 2)
        self.assertAlmostEqual(float(row["proportion"]), 10 / 11)
        self.assertEqual(row["estimand"], "unweighted_sampled_clip_composition")
        self.assertEqual(
            row["ci_method"],
            "unweighted_sampled_clip_study_cluster_percentile_bootstrap",
        )

    def test_acquisition_content_type_is_summarized(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            self.second_readers,
            self.clip_roster,
            self.adjudication,
            n_bootstrap=20,
        )
        row = result.clip_summary.query(
            "target == 'lvot_vti' and outcome == 'acquisition_content_type::m_mode'"
        ).iloc[0]
        self.assertEqual(int(row["n_clips"]), 11)
        self.assertAlmostEqual(float(row["proportion"]), 1 / 11)

    def test_manual_audit_completion_certifies_and_rechecks_outputs(self) -> None:
        input_hashes = {
            "study_annotations": "1" * 64,
            "clip_annotations": "2" * 64,
            "audit_linkage": "3" * 64,
            "sampling_design": "4" * 64,
            "second_reader_manifest": "5" * 64,
            "clip_roster": "6" * 64,
            "completed_adjudication": "7" * 64,
        }
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            self.second_readers,
            self.clip_roster,
            self.adjudication,
            input_hashes=input_hashes,
            n_bootstrap=20,
        )
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "aggregate_safe"
            write_audit_aggregates(result, output)
            certificate = output / "manual_audit_completion.json"
            payload = load_manual_audit_completion(certificate)
            self.assertEqual(payload["status"], "MANUAL_AUDIT_COMPLETE")
            with (output / "manual_audit_summary.json").open("a", encoding="utf-8") as stream:
                stream.write(" ")
            with self.assertRaisesRegex(Tier1BlockedError, "artifact changed"):
                load_manual_audit_completion(certificate)

    def test_candidate_values_never_enter_safe_outputs(self) -> None:
        result = aggregate_audit_annotations(
            self.study,
            self.clips,
            self.linkage,
            self.design,
            self.config,
            self.config_hash,
            self.second_readers,
            self.clip_roster,
            self.adjudication,
            n_bootstrap=20,
        )
        combined_columns = (
            set(result.study_summary.columns)
            | set(result.clip_summary.columns)
            | set(result.agreement.columns)
        )
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
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_sample_token_mismatch_fails_closed(self) -> None:
        bad = self.study.copy()
        bad.loc[0, SAMPLE_TOKEN_COLUMN] = "wrong-token"
        with self.assertRaisesRegex(Tier1BlockedError, "locked sample manifest token"):
            aggregate_audit_annotations(
                bad,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_blank_row_level_sample_token_fails_closed(self) -> None:
        bad = self.clips.copy()
        bad.loc[0, SAMPLE_TOKEN_COLUMN] = np.nan
        with self.assertRaisesRegex(Tier1BlockedError, "locked sample manifest token"):
            aggregate_audit_annotations(
                self.study,
                bad,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_missing_assigned_second_reader_fails_closed(self) -> None:
        bad = self.study[self.study["reader_role"] != "secondary"].copy()
        with self.assertRaisesRegex(Tier1BlockedError, "second-reader annotations"):
            aggregate_audit_annotations(
                bad,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_deleted_primary_secondary_clip_pair_fails_locked_roster(self) -> None:
        bad = self.clips[self.clips["clip_audit_id"] != "A1C0"].copy()
        with self.assertRaisesRegex(Tier1BlockedError, "locked canonical clip roster"):
            aggregate_audit_annotations(
                self.study,
                bad,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_incomplete_required_adjudication_fails_closed(self) -> None:
        incomplete = self.adjudication.copy()
        incomplete.loc[incomplete.index[0], "adjudicated_value"] = ""
        with self.assertRaisesRegex(Tier1BlockedError, "adjudications are incomplete"):
            aggregate_audit_annotations(
                self.study,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                incomplete,
                n_bootstrap=10,
            )

    def test_primary_and_secondary_reader_must_be_independent(self) -> None:
        bad = self.study.copy()
        bad.loc[bad["reader_role"] == "secondary", "reader_id"] = "R1"
        with self.assertRaisesRegex(Tier1BlockedError, "not independent"):
            aggregate_audit_annotations(
                bad,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_reader_independence_normalizes_surrounding_whitespace(self) -> None:
        bad = self.study.copy()
        bad.loc[bad["reader_role"] == "secondary", "reader_id"] = " R1 "
        with self.assertRaisesRegex(Tier1BlockedError, "not independent"):
            aggregate_audit_annotations(
                bad,
                self.clips,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_clip_primary_and_secondary_reader_must_be_independent(self) -> None:
        bad = self.clips.copy()
        bad.loc[bad["reader_role"] == "secondary", "reader_id"] = "R1"
        with self.assertRaisesRegex(Tier1BlockedError, "independent locked study-reader assignments"):
            aggregate_audit_annotations(
                self.study,
                bad,
                self.linkage,
                self.design,
                self.config,
                self.config_hash,
                self.second_readers,
                self.clip_roster,
                self.adjudication,
                n_bootstrap=10,
            )

    def test_positive_uncertain_and_discordant_findings_enter_adjudication_queue(self) -> None:
        study = self.study.copy()
        study.loc[(study["audit_id"] == "A1") & (study["reader_id"] == "R2"), "m_mode_present"] = "uncertain"
        queue = build_adjudication_queue(study, self.clips)
        self.assertTrue(((queue["audit_id"] == "A1") & (queue["outcome"] == "spectral_doppler_present")).any())
        row = queue[(queue["audit_id"] == "A1") & (queue["outcome"] == "m_mode_present")].iloc[0]
        self.assertIn("positive_or_uncertain", row["adjudication_reason"])
        self.assertIn("reader_disagreement", row["adjudication_reason"])


class PostUnblindingValueMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config, self.config_hash = load_audit_config(CONFIG_PATH)
        self.linkage = pd.DataFrame(
            [
                {
                    "audit_id": "A1",
                    "target_membership": "lvot_vti",
                    "target_strata": "lvot_vti:train",
                    "lvot_vti_target_value": 18.0,
                    "tapse_target_value": np.nan,
                },
                {
                    "audit_id": "A2",
                    "target_membership": "tapse",
                    "target_strata": "tapse:test",
                    "lvot_vti_target_value": np.nan,
                    "tapse_target_value": 20.0,
                },
            ]
        )
        self.design = pd.DataFrame(
            [
                {"target": "lvot_vti", "split": "train", "source_n": 10, "sample_n": 1, "design_weight": 10.0},
                {"target": "tapse", "split": "test", "source_n": 5, "sample_n": 1, "design_weight": 5.0},
            ]
        )
        self.clip_roster = pd.DataFrame(
            [
                {"audit_id": "A1", "clip_audit_id": "C1"},
                {"audit_id": "A2", "clip_audit_id": "C2"},
            ]
        )
        token = _sample_manifest_token(self.linkage, self.design, self.clip_roster)
        self.clips = pd.DataFrame(
            [
                {
                    "audit_id": "A1",
                    "clip_audit_id": "C1",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    SAMPLE_TOKEN_COLUMN: token,
                    "candidate_target_value_present": "yes",
                    "candidate_target_value": 180,
                    "visible_unit_text": "mm",
                    "display_precision": 0,
                    "lvot_vti_specific_label": "yes",
                    "tapse_specific_label": "no",
                },
                {
                    "audit_id": "A2",
                    "clip_audit_id": "C2",
                    "reader_id": "R1",
                    "reader_role": "primary",
                    SAMPLE_TOKEN_COLUMN: token,
                    "candidate_target_value_present": "yes",
                    "candidate_target_value": 2.0,
                    "visible_unit_text": "cm",
                    "display_precision": 1,
                    "lvot_vti_specific_label": "no",
                    "tapse_specific_label": "yes",
                },
            ]
        )
        self.adjudication = build_adjudication_queue(pd.DataFrame(), self.clips)
        self.adjudication["adjudicated_value"] = "yes"
        self.adjudication["adjudicator_id"] = "ADJ1"
        self.certified_hashes = {
            "study_annotations": "1" * 64,
            "clip_annotations": "2" * 64,
            "audit_linkage": "3" * 64,
            "sampling_design": "4" * 64,
            "second_reader_manifest": "5" * 64,
            "clip_roster": "6" * 64,
            "completed_adjudication": "7" * 64,
        }
        self.post_input_hashes = {
            role: self.certified_hashes[role]
            for role in (
                "clip_annotations",
                "audit_linkage",
                "sampling_design",
                "clip_roster",
                "completed_adjudication",
            )
        }

    def _completion(self, roster: pd.DataFrame) -> dict[str, object]:
        return {
            "schema_version": "jdim-manual-audit-completion-v1",
            "status": "MANUAL_AUDIT_COMPLETE",
            "configuration_sha256": self.config_hash,
            "sample_manifest_token": _sample_manifest_token(self.linkage, self.design, roster),
            "annotation_roster_validated": True,
            "independent_second_reads_validated": True,
            "completed_adjudication_validated": True,
            "input_file_hashes": self.certified_hashes,
        }

    def test_unit_conversion_and_display_precision_match_after_unblinding(self) -> None:
        result = derive_post_unblinding_value_matches(
            self.clips,
            self.linkage,
            self.design,
            self.clip_roster,
            self.adjudication,
            self.config,
            self.config_hash,
            self._completion(self.clip_roster),
            self.post_input_hashes,
        )
        statuses = result.restricted_study_matches.set_index("target")[
            "confirmed_target_value_match_present"
        ].to_dict()
        self.assertEqual(statuses, {"lvot_vti": "yes", "tapse": "yes"})
        self.assertTrue(result.provenance["comparison_performed_after_locked_blinded_transcription"])
        self.assertNotIn("audit_id", result.safe_summary.columns)

    def test_post_unblinding_requires_completed_audit_certificate(self) -> None:
        with self.assertRaisesRegex(Tier1BlockedError, "completion certificate"):
            derive_post_unblinding_value_matches(
                self.clips,
                self.linkage,
                self.design,
                self.clip_roster,
                self.adjudication,
                self.config,
                self.config_hash,
                {},
                self.post_input_hashes,
            )

    def test_post_unblinding_rejects_hash_mismatch(self) -> None:
        changed = dict(self.post_input_hashes)
        changed["clip_annotations"] = "f" * 64
        with self.assertRaisesRegex(Tier1BlockedError, "completed blinded audit"):
            derive_post_unblinding_value_matches(
                self.clips,
                self.linkage,
                self.design,
                self.clip_roster,
                self.adjudication,
                self.config,
                self.config_hash,
                self._completion(self.clip_roster),
                changed,
            )

    def test_post_unblinding_rejects_blank_row_token(self) -> None:
        clips = self.clips.copy()
        clips.loc[0, SAMPLE_TOKEN_COLUMN] = ""
        with self.assertRaisesRegex(Tier1BlockedError, "locked sample manifest token"):
            derive_post_unblinding_value_matches(
                clips,
                self.linkage,
                self.design,
                self.clip_roster,
                self.adjudication,
                self.config,
                self.config_hash,
                self._completion(self.clip_roster),
                self.post_input_hashes,
            )

    def test_ambiguous_distinct_candidate_values_require_restricted_adjudication(self) -> None:
        duplicate = self.clips.iloc[[0]].copy()
        duplicate["clip_audit_id"] = "C1B"
        duplicate["candidate_target_value"] = 190
        clips = pd.concat([self.clips, duplicate], ignore_index=True)
        roster = pd.concat(
            [
                self.clip_roster,
                pd.DataFrame([{"audit_id": "A1", "clip_audit_id": "C1B"}]),
            ],
            ignore_index=True,
        )
        clips[SAMPLE_TOKEN_COLUMN] = _sample_manifest_token(
            self.linkage,
            self.design,
            roster,
        )
        result = derive_post_unblinding_value_matches(
            clips,
            self.linkage,
            self.design,
            roster,
            build_adjudication_queue(pd.DataFrame(), clips).assign(
                adjudicated_value="yes",
                adjudicator_id="ADJ1",
            ),
            self.config,
            self.config_hash,
            self._completion(roster),
            self.post_input_hashes,
        )
        lvot = result.restricted_study_matches.query("target == 'lvot_vti'").iloc[0]
        self.assertEqual(lvot["confirmed_target_value_match_present"], "not_assessable")
        self.assertEqual(lvot["restricted_adjudication_required"], "yes")

    def test_nonfinite_visible_value_is_not_assessable(self) -> None:
        clips = self.clips.copy()
        clips.loc[clips["audit_id"] == "A1", "candidate_target_value"] = np.inf
        result = derive_post_unblinding_value_matches(
            clips,
            self.linkage,
            self.design,
            self.clip_roster,
            self.adjudication,
            self.config,
            self.config_hash,
            self._completion(self.clip_roster),
            self.post_input_hashes,
        )
        lvot = result.restricted_study_matches.query("target == 'lvot_vti'").iloc[0]
        self.assertEqual(lvot["confirmed_target_value_match_present"], "not_assessable")

    def test_uncertain_target_specificity_remains_not_assessable(self) -> None:
        clips = self.clips.copy()
        clips.loc[clips["audit_id"] == "A1", "lvot_vti_specific_label"] = "uncertain"
        adjudication = build_adjudication_queue(pd.DataFrame(), clips)
        adjudication["adjudicated_value"] = adjudication.apply(
            lambda row: "uncertain"
            if row["outcome"] == "lvot_vti_specific_label" and row["audit_id"] == "A1"
            else "yes",
            axis=1,
        )
        adjudication["adjudicator_id"] = "ADJ1"
        result = derive_post_unblinding_value_matches(
            clips,
            self.linkage,
            self.design,
            self.clip_roster,
            adjudication,
            self.config,
            self.config_hash,
            self._completion(self.clip_roster),
            self.post_input_hashes,
        )
        lvot = result.restricted_study_matches.query("target == 'lvot_vti'").iloc[0]
        self.assertEqual(lvot["confirmed_target_value_match_present"], "not_assessable")
        self.assertEqual(int(lvot["uncertain_candidate_clip_count"]), 1)

    def test_nonmatch_plus_unassessable_candidate_is_not_assessable(self) -> None:
        clips = self.clips.copy()
        clips.loc[clips["audit_id"] == "A1", "candidate_target_value"] = 170
        extra = clips[clips["audit_id"] == "A1"].copy()
        extra["clip_audit_id"] = "C1B"
        extra["candidate_target_value"] = 180
        extra["visible_unit_text"] = "unsupported"
        clips = pd.concat([clips, extra], ignore_index=True)
        roster = pd.concat(
            [
                self.clip_roster,
                pd.DataFrame([{"audit_id": "A1", "clip_audit_id": "C1B"}]),
            ],
            ignore_index=True,
        )
        clips[SAMPLE_TOKEN_COLUMN] = _sample_manifest_token(
            self.linkage,
            self.design,
            roster,
        )
        adjudication = build_adjudication_queue(pd.DataFrame(), clips)
        adjudication["adjudicated_value"] = "yes"
        adjudication["adjudicator_id"] = "ADJ1"
        result = derive_post_unblinding_value_matches(
            clips,
            self.linkage,
            self.design,
            roster,
            adjudication,
            self.config,
            self.config_hash,
            self._completion(roster),
            self.post_input_hashes,
        )
        lvot = result.restricted_study_matches.query("target == 'lvot_vti'").iloc[0]
        self.assertEqual(lvot["confirmed_target_value_match_present"], "not_assessable")
        self.assertEqual(int(lvot["assessable_candidate_clip_count"]), 1)
        self.assertEqual(int(lvot["not_assessable_candidate_clip_count"]), 1)


if __name__ == "__main__":
    unittest.main()
