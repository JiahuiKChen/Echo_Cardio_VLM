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

from jdim_tier1.corrected_analysis import _array_sha256, _decisions_from_restricted_evidence  # noqa: E402
from jdim_tier1.duplicate_metadata import (  # noqa: E402
    DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE,
    TRUE_DUPLICATE_EXPECTED_ROWS,
)
from jdim_tier1.duplicate_resolution import (  # noqa: E402
    resolve_duplicate_decisions,
    resolve_duplicate_decisions_from_recovery,
    write_resolved_duplicate_decisions,
)
from jdim_tier1.duplicate_recovery import DUPLICATE_SEMANTICS_RESOLVED  # noqa: E402
from jdim_tier1.safety import Tier1BlockedError, sha256_file  # noqa: E402


class DuplicateResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.vector = np.asarray([1.0, 2.0, 3.0], dtype=np.float32)
        vector_hash = _array_sha256(self.vector)
        self.prior_rows = self.root / "prior_rows.csv"
        pd.DataFrame(
            [
                {
                    "group_token": "a" * 64,
                    "classification": "AMBIGUOUS_REQUIRES_AUTHOR_REVIEW",
                    "classification_reason": "processed NPZ missing",
                    "_batch": "batch_000",
                    "_manifest_row": index,
                    "source_manifest_row_fingerprint_sha256": str(index + 1) * 64,
                    "study_id": 101,
                    "subject_id": 10,
                    "dicom_filepath": "files/p10/p10/s101/clip.dcm",
                    "npz_path": "/restricted/historical/clip.npz",
                    "output_path": "/restricted/historical/clip.npz",
                    "embedding_idx": index,
                    "embedding_vector_sha256": vector_hash,
                    "processed_array_sha256": "",
                    "processed_array_selector": "",
                }
                for index in (0, 1)
            ]
        ).to_csv(self.prior_rows, index=False)
        self.prior_summary = self.root / "prior_summary.json"
        self.prior_summary.write_text(
            json.dumps(
                {
                    "status": "BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED",
                    "input_provenance": {
                        "split_map": {"sha256": "split"},
                        "batches": [],
                    },
                    "restricted_artifact_sha256": {
                        "duplicate_forensics_rows.csv": sha256_file(self.prior_rows)
                    },
                }
            )
        )
        self.metadata_groups = self.root / "metadata_groups.csv"
        pd.DataFrame(
            [
                {
                    "group_token": "a" * 64,
                    "classification": TRUE_DUPLICATE_EXPECTED_ROWS,
                    "first_duplicate_stage": "expected_records",
                    "semantic_identity_equal_all_stages": True,
                    "distinct_window_metadata_present": False,
                    "historical_vectors_exact_equal": True,
                    "one_source_path": True,
                    "one_processed_path": True,
                }
            ]
        ).to_csv(self.metadata_groups, index=False)
        self.metadata_summary = self.root / "metadata_summary.json"
        self.metadata_summary.write_text(
            json.dumps(
                {
                    "status": DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE,
                    "restricted_evidence_packet_sha256": {
                        "metadata_group_classification.csv": sha256_file(
                            self.metadata_groups
                        )
                    },
                }
            )
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _resolve(self):
        return resolve_duplicate_decisions(
            self.prior_rows,
            self.prior_summary,
            self.metadata_groups,
            self.metadata_summary,
        )

    def test_resolution_creates_one_deterministic_keeper(self) -> None:
        result = self._resolve()
        self.assertEqual(set(result.rows["classification"]), {TRUE_DUPLICATE_EXPECTED_ROWS})
        self.assertEqual(int(result.rows["dedup_keep_candidate"].sum()), 1)
        self.assertEqual(result.rows["semantic_clip_identity_sha256"].nunique(), 1)
        self.assertTrue(
            result.rows["semantic_clip_identity_sha256"]
            .str.fullmatch(r"[0-9a-f]{64}")
            .all()
        )

    def test_resolution_is_accepted_by_corrected_aggregation_identity_logic(self) -> None:
        result = self._resolve()
        evidence = self.root / "resolved.csv"
        result.rows.to_csv(evidence, index=False)
        manifest = pd.DataFrame(
            [
                {
                    "embedding_idx": index,
                    "study_id": 101,
                    "subject_id": 10,
                    "dicom_filepath": "files/p10/p10/s101/clip.dcm",
                    "npz_path": "/restricted/historical/clip.npz",
                }
                for index in (0, 1)
            ]
        )
        manifest["_study"] = "101"
        manifest["_subject"] = "10"
        decisions, summary = _decisions_from_restricted_evidence(
            evidence,
            manifest,
            np.stack([self.vector, self.vector]),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(summary["actionable_group_count"], 1)

    def test_tampered_metadata_group_packet_fails_closed(self) -> None:
        with self.metadata_groups.open("a") as stream:
            stream.write("tamper\n")
        with self.assertRaises(Tier1BlockedError):
            self._resolve()

    def test_deferred_recovery_packet_is_consumed_and_hash_bound(self) -> None:
        recovery_rows = self.root / "recovery_rows.csv"
        pd.DataFrame(
            [
                {
                    "group_token": "a" * 64,
                    "status": DUPLICATE_SEMANTICS_RESOLVED,
                    "processed_array_sha256": "f" * 64,
                }
            ]
        ).to_csv(recovery_rows, index=False)
        recovery_summary = self.root / "recovery_summary.json"
        recovery_summary.write_text(
            json.dumps(
                {
                    "restricted_evidence_packet_sha256": {
                        "duplicate_recovery_rows.csv": sha256_file(recovery_rows)
                    }
                }
            )
        )
        result = resolve_duplicate_decisions_from_recovery(
            self.prior_rows,
            self.prior_summary,
            self.metadata_groups,
            self.metadata_summary,
            recovery_rows,
            recovery_summary,
        )
        self.assertEqual(set(result.rows["processed_array_sha256"]), {"f" * 64})
        self.assertEqual(
            result.summary["recovery_resolution"]["status"],
            DUPLICATE_SEMANTICS_RESOLVED,
        )

    def test_output_is_nonoverwriting_and_hash_bound(self) -> None:
        result = self._resolve()
        output = self.root / "resolved_output"
        write_resolved_duplicate_decisions(result, output)
        summary = json.loads(
            (output / "aggregate_safe" / "duplicate_forensics_summary.json").read_text()
        )
        rows = output / "restricted" / "duplicate_forensics_rows.csv"
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(
            summary["restricted_artifact_sha256"]["duplicate_forensics_rows.csv"],
            sha256_file(rows),
        )
        with self.assertRaises(FileExistsError):
            write_resolved_duplicate_decisions(result, output)


if __name__ == "__main__":
    unittest.main()
