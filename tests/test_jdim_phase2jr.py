from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.phase2i import OFFICIAL_SOURCE_BASE, SOURCE_ACQUISITION_ONLY  # noqa: E402
from jdim_tier1.phase2jr import (  # noqa: E402
    BLOCKED_RESTORATION_STATE_INCONSISTENT,
    COMPLETE_VERIFIED,
    INTERFACE_INCOMPLETE_RESUMABLE,
    LOCKED_ROSTER_SOURCE_RESTORED,
    MISSING,
    PARTIAL_RESUMABLE,
    RESTORATION_INCOMPLETE_RESUMABLE,
    UNEXPECTED_OR_INVALID,
    _opaque_media_id,
    assess_restoration_state,
    resume_audit_media,
    resume_locked_restoration,
    verify_interface_continuation_certificate,
    verify_restoration_certificate,
    verify_restoration_continuation_certificate,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file  # noqa: E402


def _validator(path: Path) -> tuple[bool, str]:
    valid = path.is_file() and path.read_bytes().startswith(b"DICOM")
    return valid, "validated" if valid else "invalid"


class RestorationFixture:
    def __init__(self, root: Path):
        self.root = root
        self.destination = root / "restricted_destination"
        self.run_root = root / "run"
        self.netrc = root / ".netrc"
        self.netrc.write_text("credential content is intentionally not parsed\n", encoding="utf-8")
        self.netrc.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.relative = [
            "files/p10/p100/s1/c1.dcm",
            "files/p10/p100/s1/c2.dcm",
            "files/p20/p200/s2/c3.dcm",
        ]
        self.urls = root / "locked_urls.txt"
        self.urls.write_text(
            "".join(f"{OFFICIAL_SOURCE_BASE}{value}\n" for value in self.relative),
            encoding="utf-8",
        )
        self.manifest = root / "manifest.csv"
        pd.DataFrame(
            [
                {
                    "audit_id": "A1" if index < 2 else "A2",
                    "clip_audit_id": f"C{index + 1}",
                    "declared_source_dicom": value,
                    "unique_linkage": "true",
                }
                for index, value in enumerate(self.relative)
            ]
        ).to_csv(self.manifest, index=False)

    def destination_path(self, index: int) -> Path:
        return self.destination / self.relative[index]

    def write_complete(self, index: int, payload: bytes = b"DICOM-complete") -> Path:
        path = self.destination_path(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def write_partial(self, index: int, payload: bytes = b"partial") -> Path:
        path = Path(str(self.destination_path(index)) + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def assess(self):
        return assess_restoration_state(
            locked_url_list=self.urls,
            restoration_manifest_csv=self.manifest,
            source_destination_root=self.destination,
            restricted_output_csv=self.root / "assessment" / "restricted.csv",
            safe_output_json=self.root / "assessment" / "safe.json",
            source_commit="source-sha",
            expected_files=3,
            expected_studies=2,
            validator=_validator,
        )

    def resume(self, *, downloader, soft_stop_seconds: float = 60.0, run_root: Path | None = None):
        return resume_locked_restoration(
            locked_url_list=self.urls,
            restoration_manifest_csv=self.manifest,
            source_destination_root=self.destination,
            run_root=run_root or self.run_root,
            netrc_path=self.netrc,
            source_commit="source-sha",
            continuation_label="test",
            expected_files=3,
            expected_studies=2,
            max_workers=1,
            checkpoint_every=1,
            checkpoint_seconds=60.0,
            soft_stop_seconds=soft_stop_seconds,
            downloader=downloader,
            validator=_validator,
        )


class Phase2JRRestorationTests(unittest.TestCase):
    def test_state_assessment_classifies_complete_partial_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_complete(0)
            fixture.write_partial(1)
            result = fixture.assess()
            self.assertEqual(
                result.restricted_rows["state"].tolist(),
                [COMPLETE_VERIFIED, PARTIAL_RESUMABLE, MISSING],
            )
            self.assertEqual(result.safe_summary["complete_verified_files"], 1)
            self.assertEqual(result.safe_summary["partial_resumable_files"], 1)
            self.assertEqual(result.safe_summary["missing_files"], 1)
            self.assertEqual(result.safe_summary["remaining_files"], 2)

    def test_complete_and_partial_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_complete(0)
            fixture.write_partial(0)
            result = fixture.assess()
            self.assertEqual(result.restricted_rows.loc[0, "state"], UNEXPECTED_OR_INVALID)
            self.assertEqual(result.safe_summary["status"], BLOCKED_RESTORATION_STATE_INCONSISTENT)

    def test_unexpected_file_fails_closed_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            unexpected = fixture.destination / "unexpected.bin"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_bytes(b"keep")
            result = fixture.assess()
            self.assertEqual(result.safe_summary["unexpected_files"], 1)
            self.assertEqual(result.safe_summary["status"], BLOCKED_RESTORATION_STATE_INCONSISTENT)
            self.assertTrue(unexpected.is_file())

    def test_resume_skips_complete_and_processes_partial_before_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            complete = fixture.write_complete(0, b"DICOM-do-not-overwrite")
            before = complete.read_bytes()
            fixture.write_partial(1)
            order: list[str] = []

            def downloader(row, **_kwargs):
                order.append(str(row["official_relative_path"]))
                destination = Path(str(row["restricted_destination"]))
                partial = Path(str(row["restricted_partial"]))
                partial.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(b"DICOM-restored")
                os.replace(partial, destination)
                return True, "restored"

            result = fixture.resume(downloader=downloader)
            self.assertEqual(result.status, LOCKED_ROSTER_SOURCE_RESTORED)
            self.assertEqual(order, fixture.relative[1:])
            self.assertEqual(complete.read_bytes(), before)
            self.assertTrue((fixture.run_root / "aggregate_safe/restoration_checkpoint.json").is_file())

    def test_missing_file_starts_at_its_exact_locked_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            destinations: list[Path] = []

            def downloader(row, **_kwargs):
                destination = Path(str(row["restricted_destination"]))
                destinations.append(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"DICOM-restored")
                return True, "restored"

            result = fixture.resume(downloader=downloader)
            self.assertEqual(result.status, LOCKED_ROSTER_SOURCE_RESTORED)
            self.assertEqual(
                destinations,
                [fixture.destination_path(index).resolve() for index in range(3)],
            )

    def test_worker_bound_rejects_more_than_two_transfers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            with self.assertRaises(ValueError):
                resume_locked_restoration(
                    locked_url_list=fixture.urls,
                    restoration_manifest_csv=fixture.manifest,
                    source_destination_root=fixture.destination,
                    run_root=fixture.run_root,
                    netrc_path=fixture.netrc,
                    source_commit="source-sha",
                    continuation_label="test",
                    expected_files=3,
                    expected_studies=2,
                    max_workers=3,
                    validator=_validator,
                )

    def test_soft_stop_writes_resumable_certificate_and_no_download(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            calls: list[str] = []

            def downloader(row, **_kwargs):
                calls.append(str(row["official_relative_path"]))
                return False, "should_not_run"

            result = fixture.resume(downloader=downloader, soft_stop_seconds=0)
            self.assertEqual(result.status, RESTORATION_INCOMPLETE_RESUMABLE)
            self.assertEqual(calls, [])
            self.assertTrue(result.safe_summary["safe_continuation"])
            self.assertEqual(result.safe_summary["remaining_files"], 3)

    def test_second_run_resumes_deterministically_after_partial_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_partial(1)
            first = fixture.resume(downloader=lambda *_args, **_kwargs: (False, "unused"), soft_stop_seconds=0)
            self.assertEqual(first.status, RESTORATION_INCOMPLETE_RESUMABLE)
            order: list[str] = []

            def downloader(row, **_kwargs):
                order.append(str(row["official_relative_path"]))
                destination = Path(str(row["restricted_destination"]))
                partial = Path(str(row["restricted_partial"]))
                partial.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(b"DICOM-restored")
                os.replace(partial, destination)
                return True, "restored"

            second = fixture.resume(
                downloader=downloader,
                run_root=fixture.root / "run2",
            )
            self.assertEqual(second.status, LOCKED_ROSTER_SOURCE_RESTORED)
            self.assertEqual(order[0], fixture.relative[1])

    def test_final_certificate_passes_and_incomplete_certificate_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))

            def downloader(row, **_kwargs):
                destination = Path(str(row["restricted_destination"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"DICOM-restored")
                return True, "restored"

            result = fixture.resume(downloader=downloader)
            results = fixture.run_root / "restricted/restoration/source_restoration_results_restricted.csv"
            verified = verify_restoration_certificate(
                certificate_path=result.certificate_path,
                results_path=results,
                expected_source_commit="source-sha",
                expected_url_list_sha256=sha256_file(fixture.urls),
                expected_files=3,
                expected_studies=2,
            )
            self.assertEqual(verified["status"], LOCKED_ROSTER_SOURCE_RESTORED)
            payload = json.loads(result.certificate_path.read_text())
            payload["status"] = RESTORATION_INCOMPLETE_RESUMABLE
            result.certificate_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(Tier1BlockedError):
                verify_restoration_certificate(
                    certificate_path=result.certificate_path,
                    results_path=results,
                    expected_source_commit="source-sha",
                    expected_url_list_sha256=sha256_file(fixture.urls),
                    expected_files=3,
                    expected_studies=2,
                )

    def test_completion_verification_is_a_read_only_no_op_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))

            def downloader(row, **_kwargs):
                destination = Path(str(row["restricted_destination"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"DICOM-restored")
                return True, "restored"

            result = fixture.resume(downloader=downloader)
            results = fixture.run_root / "restricted/restoration/source_restoration_results_restricted.csv"
            before = [fixture.destination_path(index).stat().st_mtime_ns for index in range(3)]
            for _ in range(2):
                verify_restoration_certificate(
                    certificate_path=result.certificate_path,
                    results_path=results,
                    expected_source_commit="source-sha",
                    expected_url_list_sha256=sha256_file(fixture.urls),
                    expected_files=3,
                    expected_studies=2,
                )
            after = [fixture.destination_path(index).stat().st_mtime_ns for index in range(3)]
            self.assertEqual(before, after)

    def test_continuation_certificate_is_hash_and_destination_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            result = fixture.resume(
                downloader=lambda *_args, **_kwargs: (False, "unused"),
                soft_stop_seconds=0,
            )
            results = fixture.run_root / "restricted/restoration/source_restoration_results_restricted.csv"
            verified = verify_restoration_continuation_certificate(
                certificate_path=result.certificate_path,
                results_path=results,
                expected_source_commit="source-sha",
                expected_url_list_sha256=sha256_file(fixture.urls),
                expected_destination_root=fixture.destination,
                expected_files=3,
                expected_studies=2,
            )
            self.assertEqual(verified["status"], RESTORATION_INCOMPLETE_RESUMABLE)
            with self.assertRaises(Tier1BlockedError):
                verify_restoration_continuation_certificate(
                    certificate_path=result.certificate_path,
                    results_path=results,
                    expected_source_commit="different",
                    expected_url_list_sha256=sha256_file(fixture.urls),
                    expected_destination_root=fixture.destination,
                    expected_files=3,
                    expected_studies=2,
                )

    def test_aggregate_safe_outputs_contain_no_credentials_urls_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.resume(
                downloader=lambda *_args, **_kwargs: (False, "unused"),
                soft_stop_seconds=0,
            )
            safe_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (fixture.run_root / "aggregate_safe").glob("*.json")
            )
            self.assertNotIn("credential content", safe_text)
            self.assertNotIn("https://", safe_text)
            self.assertNotIn(str(fixture.root), safe_text)
            self.assertNotIn(".dcm", safe_text)

    def test_existing_terminal_certificate_makes_same_run_root_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.resume(
                downloader=lambda *_args, **_kwargs: (False, "unused"),
                soft_stop_seconds=0,
            )
            with self.assertRaises(Tier1BlockedError):
                fixture.resume(
                    downloader=lambda *_args, **_kwargs: (False, "unused"),
                    soft_stop_seconds=0,
                )


class Phase2JRInterfaceTests(unittest.TestCase):
    def _inventory(self, root: Path) -> Path:
        path = root / "technical.csv"
        pd.DataFrame(
            [
                {
                    "audit_id": "A1",
                    "clip_audit_id": "C1",
                    "evidence_tier": SOURCE_ACQUISITION_ONLY,
                    "source_viewable": True,
                    "source_path": "/restricted/source.dcm",
                    "processed_path": "",
                    "source_model_frame_indices_json": "[]",
                }
            ]
        ).to_csv(path, index=False)
        return path

    @staticmethod
    def _renderer(calls: list[str]):
        def render(row, media_root):
            calls.append(str(row["clip_audit_id"]))
            source_id = _opaque_media_id(str(row["audit_id"]), str(row["clip_audit_id"]), "source")
            (media_root / f"{source_id}.png").write_bytes(b"validated-test-png")
            return {
                "audit_id": str(row["audit_id"]),
                "clip_audit_id": str(row["clip_audit_id"]),
                "evidence_tier": SOURCE_ACQUISITION_ONLY,
                "source_media_id": source_id,
                "model_input_media_id": "",
                "model_input_verified": False,
                "source_only": True,
            }

        return render

    def test_media_soft_stop_then_resume_without_regenerating_completed_media(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            inventory = self._inventory(root)
            persistent = root / "persistent"
            calls: list[str] = []
            with patch("jdim_tier1.phase2jr._media_valid", side_effect=lambda path: path.is_file()):
                first = resume_audit_media(
                    technical_inventory_csv=inventory,
                    persistent_root=persistent,
                    run_root=root / "b1a",
                    source_commit="source-sha",
                    soft_stop_seconds=0,
                    renderer=self._renderer(calls),
                )
                self.assertEqual(first["status"], INTERFACE_INCOMPLETE_RESUMABLE)
                second = resume_audit_media(
                    technical_inventory_csv=inventory,
                    persistent_root=persistent,
                    run_root=root / "b1b",
                    source_commit="source-sha",
                    checkpoint_every=1,
                    soft_stop_seconds=60,
                    renderer=self._renderer(calls),
                )
                self.assertEqual(second["status"], "AUDIT_MEDIA_READY")
                third = resume_audit_media(
                    technical_inventory_csv=inventory,
                    persistent_root=persistent,
                    run_root=root / "b2",
                    source_commit="source-sha",
                    soft_stop_seconds=60,
                    renderer=lambda *_args, **_kwargs: self.fail("completed media was regenerated"),
                )
            self.assertEqual(third["status"], "AUDIT_MEDIA_READY")
            self.assertEqual(calls, ["C1"])

    def test_interface_continuation_gate_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            certificate = Path(tempdir) / "certificate.json"
            payload = {
                "status": INTERFACE_INCOMPLETE_RESUMABLE,
                "source_commit": "source-sha",
                "safe_continuation": True,
                "ocr_used": False,
                "clinical_annotations_generated": False,
            }
            certificate.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                verify_interface_continuation_certificate(
                    certificate_path=certificate,
                    expected_source_commit="source-sha",
                )["status"],
                INTERFACE_INCOMPLETE_RESUMABLE,
            )
            payload["ocr_used"] = True
            certificate.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(Tier1BlockedError):
                verify_interface_continuation_certificate(
                    certificate_path=certificate,
                    expected_source_commit="source-sha",
                )

    def test_scheduler_scripts_use_serial_nonarray_exact_parent_policy(self) -> None:
        common = (ROOT / "scripts/jdim_phase2jr_scc_common.sh").read_text(encoding="utf-8")
        submit = (ROOT / "scripts/scc_submit_jdim_phase2jr_chain.sh").read_text(encoding="utf-8")
        restoration = (ROOT / "scripts/scc_run_jdim_phase2jr_restoration.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("75453ebd1c16268870b6d588e4f54d8668e5e186", common)
        self.assertIn("A2->A3->B1->B2", submit)
        self.assertIn("-hold_jid", submit)
        self.assertNotIn("-t ", submit)
        self.assertNotIn("qsub -V", submit)
        self.assertIn("--max-workers 2", restoration)
        self.assertIn("--soft-stop-seconds 38700", restoration)
        self.assertNotIn("cat \"${HOME}/.netrc\"", common)


if __name__ == "__main__":
    unittest.main()
