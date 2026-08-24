from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.provenance import build_provenance_manifests  # noqa: E402
from jdim_tier1.safety import Tier1BlockedError  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class ProvenanceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init")

        self.paths = {
            "split_map": self.repo / "split.csv",
            "selected_study_universe": self.repo / "selected.csv",
            "structured_measurements": self.repo / "measurements.csv",
            "embedding_manifest": self.repo / "embeddings.csv",
            "video_encoder_checkpoint": self.repo / "checkpoint.bin",
            "audit_configuration": self.repo / "audit_config.yaml",
        }
        pd.DataFrame(
            [
                {"subject_id": 1, "split": "train"},
                {"subject_id": 2, "split": "val"},
                {"subject_id": 3, "split": "test"},
            ]
        ).to_csv(self.paths["split_map"], index=False)
        pd.DataFrame([{"subject_id": 1, "study_id": 11}]).to_csv(
            self.paths["selected_study_universe"], index=False
        )
        pd.DataFrame(
            [{"subject_id": 1, "study_id": 11, "measurement": "lvot_vti", "result": 20}]
        ).to_csv(self.paths["structured_measurements"], index=False)
        pd.DataFrame([{"subject_id": 1, "study_id": 11, "study_idx": 0}]).to_csv(
            self.paths["embedding_manifest"], index=False
        )
        self.paths["video_encoder_checkpoint"].write_bytes(b"synthetic checkpoint")
        self.paths["audit_configuration"].write_text('{"protocol_version":"synthetic"}', encoding="utf-8")
        git(self.repo, "add", ".")
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Codex Test",
                "-c",
                "user.email=codex-test@example.invalid",
                "commit",
                "-m",
                "synthetic fixture",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        self.spec = {
            "manifest_version": "jdim-provenance-v1",
            "mimic_iv_echo_release": "synthetic-1.0",
            "echoprime_code_release": "synthetic-commit",
            "files": {
                role: {"path": str(path.resolve()), "classification": "restricted"}
                for role, path in self.paths.items()
            },
            "split_map_role": "split_map",
            "selected_study_universe_role": "selected_study_universe",
            "structured_measurement_role": "structured_measurements",
            "embedding_manifest_role": "embedding_manifest",
            "video_encoder_checkpoint_role": "video_encoder_checkpoint",
            "audit_configuration_role": "audit_configuration",
            "script_arguments": {
                "input_path": str(self.paths["structured_measurements"].resolve()),
                "bootstrap_n": 2000,
            },
            "codex_assisted_artifacts": ["scripts/jdim_tier1/"],
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_clean_manifest_has_restricted_and_path_free_safe_versions(self) -> None:
        restricted, safe = build_provenance_manifests(self.spec, self.repo)
        self.assertFalse(restricted["repository"]["dirty"])
        self.assertEqual(safe["split_map"]["subject_overlap_count"], 0)
        self.assertEqual(safe["split_map"]["subjects_per_split"], {"train": 1, "val": 1, "test": 1})
        self.assertEqual(safe["analysis_universe_split_counts"]["per_split"]["train"]["n_studies"], 1)
        serialized_safe = json.dumps(safe, sort_keys=True)
        for path in self.paths.values():
            self.assertNotIn(str(path.resolve()), serialized_safe)
        self.assertNotIn("subject_id", serialized_safe)
        self.assertIn(str(self.paths["split_map"].resolve()), json.dumps(restricted))

    def test_dirty_repository_is_rejected(self) -> None:
        (self.repo / "dirty.txt").write_text("dirty")
        with self.assertRaisesRegex(Tier1BlockedError, "repository is dirty"):
            build_provenance_manifests(self.spec, self.repo)

    def test_dirty_repository_allowed_only_for_synthetic_tests(self) -> None:
        (self.repo / "dirty.txt").write_text("dirty")
        _, safe = build_provenance_manifests(
            self.spec,
            self.repo,
            allow_dirty_for_synthetic_tests=True,
        )
        self.assertTrue(safe["repository"]["dirty"])
        self.assertTrue(safe["repository"]["dirty_state_prohibited"])

    def test_missing_release_is_rejected(self) -> None:
        self.spec["mimic_iv_echo_release"] = ""
        with self.assertRaisesRegex(ValueError, "must be explicit"):
            build_provenance_manifests(self.spec, self.repo)

    def test_split_overlap_is_rejected(self) -> None:
        split = pd.read_csv(self.paths["split_map"])
        split = pd.concat([split, pd.DataFrame([{"subject_id": 1, "split": "test"}])], ignore_index=True)
        split.to_csv(self.paths["split_map"], index=False)
        git(self.repo, "add", ".")
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Codex Test",
                "-c",
                "user.email=codex-test@example.invalid",
                "commit",
                "-m",
                "overlap fixture",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        with self.assertRaisesRegex(Tier1BlockedError, "subject_overlap_count=1"):
            build_provenance_manifests(self.spec, self.repo)


if __name__ == "__main__":
    unittest.main()
