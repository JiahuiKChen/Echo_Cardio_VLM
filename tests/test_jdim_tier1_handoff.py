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

from build_jdim_cohort_lineage_metadata import parse_name_class, parse_overlap_pairs  # noqa: E402
from build_jdim_provenance_spec import parse_arguments, parse_file_specs  # noqa: E402
from jdim_tier1.safety import sanitize_for_safe_manifest  # noqa: E402


class Tier1HandoffTests(unittest.TestCase):
    def test_lineage_metadata_cli_hashes_the_existing_split(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            split = root / "subject_split_map_v1.csv"
            output = root / "lineage.json"
            pd.DataFrame(
                [
                    {"subject_id": 1, "split": "train"},
                    {"subject_id": 2, "split": "test"},
                ]
            ).to_csv(split, index=False)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_jdim_cohort_lineage_metadata.py"),
                    "--mimic-iv-echo-release",
                    "synthetic-1.0",
                    "--source-denominator-definition",
                    "synthetic source",
                    "--imaging-lineage",
                    "synthetic imaging",
                    "--label-lineage",
                    "synthetic labels",
                    "--split-map-csv",
                    str(split),
                    "--split-version",
                    "subject_split_map_v1",
                    "--split-generator",
                    "synthetic generator",
                    "--batch-source",
                    "legacy=legacy",
                    "--batch-source",
                    "batch_000=fullscale",
                    "--output-json",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(completed.stdout)["status"], "ok")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["batch_sources"]["legacy"]["source_class"], "legacy")
            self.assertEqual(len(payload["split_map"]["expected_sha256"]), 64)

    def test_lineage_batch_and_overlap_syntax_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "path-like"):
            parse_name_class(["/restricted/batch=legacy"])
        batches = set(parse_name_class(["legacy=legacy", "batch_000=fullscale"]))
        with self.assertRaisesRegex(ValueError, "Invalid declared overlap"):
            parse_overlap_pairs(["legacy|legacy"], batches)
        with self.assertRaisesRegex(ValueError, "Invalid declared overlap"):
            parse_overlap_pairs(["legacy|unknown"], batches)

    def test_provenance_spec_parsers_require_safe_roles_and_absolute_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "synthetic.csv"
            path.write_text("value\n1\n", encoding="utf-8")
            parsed = parse_file_specs([f"synthetic_input=restricted={path}"])
            self.assertEqual(parsed["synthetic_input"]["classification"], "restricted")
            with self.assertRaisesRegex(ValueError, "path-free"):
                parse_file_specs([f"/restricted/role=restricted={path}"])
            with self.assertRaisesRegex(ValueError, "must be absolute"):
                parse_file_specs(["relative=restricted=relative.csv"])
        self.assertEqual(parse_arguments(["bootstrap_n=2000", "model_refit=false"]), {
            "bootstrap_n": 2000,
            "model_refit": False,
        })

    def test_safe_manifest_sanitizer_redacts_embedded_absolute_paths(self) -> None:
        payload = sanitize_for_safe_manifest(
            {"command": "tool --input=/restricted/project/example.csv --bootstrap-n=2000"}
        )
        self.assertEqual(payload["command"], "[REDACTED_PATH]")

    def test_scc_wrapper_has_valid_shell_syntax_and_help(self) -> None:
        wrapper = ROOT / "scripts" / "scc_run_jdim_tier1.sh"
        subprocess.run(["bash", "-n", str(wrapper)], cwd=ROOT, check=True)
        completed = subprocess.run(
            ["bash", str(wrapper), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("reviewer-metrics", completed.stdout)
        self.assertIn("audit-pilot", completed.stdout)


if __name__ == "__main__":
    unittest.main()
