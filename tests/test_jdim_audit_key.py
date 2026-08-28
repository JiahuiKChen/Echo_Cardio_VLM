from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.audit import build_audit_sample, load_audit_config  # noqa: E402
from jdim_tier1.audit_key import (  # noqa: E402
    AuditKeySafetyError,
    create_or_resume_audit_key,
)


CONFIG_PATH = ROOT / "configs" / "jdim_input_content_audit_v1.yaml"
SECRET = b"K" * 32


def paths(tempdir: str) -> tuple[Path, Path]:
    output = Path(tempdir) / "audit_run"
    return output, output / "restricted" / "input_content_audit" / "opaque_id_key.bin"


def fixture_roster(key: bytes):
    config, config_hash = load_audit_config(CONFIG_PATH)
    cohorts = {
        "lvot_vti": pd.DataFrame(
            [
                {"study_id": i, "subject_id": 100 + i, "split": split, "target_value": 10 + i}
                for i, split in enumerate(["train", "train", "val", "test"], start=1)
            ]
        ),
        "tapse": pd.DataFrame(
            [
                {"study_id": i, "subject_id": 100 + i, "split": split, "target_value": 12 + i}
                for i, split in zip([3, 5, 6, 7], ["train", "train", "val", "test"])
            ]
        ),
    }
    pairs = pd.concat([frame[["study_id", "subject_id"]] for frame in cohorts.values()]).drop_duplicates()
    clips = pd.DataFrame(
        [
            {
                "study_id": row.study_id,
                "subject_id": row.subject_id,
                "canonical_clip_id": f"clip-{row.study_id}",
                "embedding_idx": index,
                "write_ok": True,
            }
            for index, row in enumerate(pairs.itertuples(index=False))
        ]
    )
    return build_audit_sample(
        cohorts,
        clips,
        config,
        config_hash,
        key,
        sample_size_per_target=4,
    )


class AuditKeyTests(unittest.TestCase):
    def test_absent_parent_is_created_securely(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            self.assertTrue(key.is_file())
            self.assertEqual(stat.S_IMODE(key.parent.stat().st_mode), 0o700)

    def test_existing_valid_parent_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            key.parent.mkdir(parents=True, mode=0o700)
            result = create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            self.assertTrue(result.created)

    def test_key_and_marker_permissions_are_restrictive(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key.with_name(key.name + ".run.json").stat().st_mode), 0o600)

    def test_existing_key_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            before = key.read_bytes()
            with self.assertRaisesRegex(AuditKeySafetyError, "not be overwritten"):
                create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: b"Z" * 32)
            self.assertEqual(key.read_bytes(), before)

    def test_nonrestricted_or_escaping_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "audit_run"
            unsafe = output / "aggregate_safe" / "opaque_id_key.bin"
            with self.assertRaisesRegex(AuditKeySafetyError, "restricted"):
                create_or_resume_audit_key(output, unsafe, "run-1", secret_factory=lambda _: SECRET)
            with self.assertRaisesRegex(AuditKeySafetyError, "absolute"):
                create_or_resume_audit_key(
                    Path("relative-output"),
                    Path("relative-output/restricted/key.bin"),
                    "run-1",
                    secret_factory=lambda _: SECRET,
                )
            outside = Path(tempdir) / "outside"
            outside.mkdir()
            output.mkdir()
            (output / "restricted").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(AuditKeySafetyError, "outside"):
                create_or_resume_audit_key(
                    output,
                    output / "restricted" / "key.bin",
                    "run-1",
                    secret_factory=lambda _: SECRET,
                )

    def test_key_contents_do_not_enter_aggregate_safe_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            safe = output / "aggregate_safe"
            safe.mkdir()
            (safe / "status.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
            aggregate_bytes = b"".join(path.read_bytes() for path in safe.rglob("*") if path.is_file())
            self.assertNotIn(SECRET, aggregate_bytes)

    def test_key_contents_do_not_enter_cli_logs_or_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_jdim_audit_key.py"),
                    "--output-root",
                    str(output),
                    "--key-file",
                    str(key),
                    "--run-id",
                    "run-1",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            secret = key.read_bytes()
            self.assertNotIn(secret.hex(), completed.stdout + completed.stderr)
            self.assertNotIn(str(secret), completed.stdout + completed.stderr)

    def test_failed_creation_leaves_no_partial_key(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            marker = key.with_name(key.name + ".run.json")
            with self.assertRaisesRegex(AuditKeySafetyError, "invalid secret"):
                create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: b"short")
            self.assertFalse(key.exists())
            self.assertFalse(marker.exists())

    def test_repeated_invocation_reuses_only_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            result = create_or_resume_audit_key(output, key, "run-1", resume_existing=True)
            self.assertEqual(result.status, "AUDIT_KEY_REUSED")
            with self.assertRaisesRegex(AuditKeySafetyError, "different immutable run"):
                create_or_resume_audit_key(output, key, "run-2", resume_existing=True)
            self.assertEqual(key.read_bytes(), SECRET)

    def test_locked_fixture_roster_succeeds_after_parent_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            result = fixture_roster(key.read_bytes())
            self.assertEqual(result.safe_summary["target_sample_counts"], {"lvot_vti": 4, "tapse": 4})
            self.assertGreater(result.safe_summary["unique_physical_studies_selected"], 0)

    def test_marker_failure_removes_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output, key = paths(tempdir)
            from jdim_tier1 import audit_key

            original = audit_key._exclusive_write
            calls = 0

            def fail_marker(path: Path, payload: bytes, mode: int = 0o600) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic marker failure")
                original(path, payload, mode)

            with mock.patch.object(audit_key, "_exclusive_write", side_effect=fail_marker):
                with self.assertRaisesRegex(OSError, "synthetic marker failure"):
                    create_or_resume_audit_key(output, key, "run-1", secret_factory=lambda _: SECRET)
            self.assertFalse(key.exists())
            self.assertFalse(key.with_name(key.name + ".run.json").exists())


class Phase2HWrapperTests(unittest.TestCase):
    def test_phase2h_wrapper_is_audit_only(self) -> None:
        text = (ROOT / "scripts" / "scc_run_jdim_phase2h.sh").read_text(encoding="utf-8")
        self.assertNotIn("cohort-flow", text)
        self.assertNotIn("reconstruct_jdim_cohort_flow", text)
        self.assertIn("AUDIT_ROSTER_LOCKED", text)
        self.assertIn("jdim_audit_roster_pilot_v1", text)


if __name__ == "__main__":
    unittest.main()
