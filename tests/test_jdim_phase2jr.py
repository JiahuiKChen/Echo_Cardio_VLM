from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
    PHASE2JR2_EXPECTED_STATE,
    QACCT_ACCOUNTING_VERIFIED,
    RESTARTABLE_ZERO_PLACEHOLDER,
    RESTORATION_INCOMPLETE_RESUMABLE,
    RESTORATION_STATE_RESUMABLE,
    UNEXPECTED_OR_INVALID,
    _download_locked_file,
    _opaque_media_id,
    assess_restoration_state,
    parse_qacct_output,
    resume_audit_media,
    resume_locked_restoration,
    verify_interface_continuation_certificate,
    verify_phase2jr2_preflight_state,
    verify_qacct_accounting,
    verify_qacct_certificate_agreement,
    verify_restoration_certificate,
    verify_restoration_continuation_certificate,
    verify_scheduler_certificate_agreement,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file, sha256_text  # noqa: E402


def _validator(path: Path) -> tuple[bool, str]:
    valid = path.is_file() and path.read_bytes().startswith(b"DICOM")
    return valid, "validated" if valid else "invalid"


def _qacct_output(*, separator: str = "    ", overrides: dict[str, str] | None = None) -> str:
    fields = [
        ("qname", "long"),
        ("hostname", "scc-node.example.edu"),
        ("jobnumber", "7354017"),
        ("failed", "100 : assumedly after job"),
        ("exit_status", "137"),
        ("start_time", "Fri Aug 28 18:56:54 2026"),
        ("end_time", "Sat Aug 29 06:56:55 2026"),
        ("ru_wallclock", "43201"),
        ("maxvmem", "5.591G"),
    ]
    replacements = overrides or {}
    return "=" * 60 + "\n" + "\n".join(
        f"{name}{separator}{replacements.get(name, value)}" for name, value in fields
    )


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

    def test_expected_zero_partial_is_restartable_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_partial(0, b"")
            result = fixture.assess()
            self.assertEqual(
                result.restricted_rows.loc[0, "state"],
                RESTARTABLE_ZERO_PLACEHOLDER,
            )
            self.assertEqual(result.safe_summary["restartable_zero_placeholder_files"], 1)
            self.assertEqual(result.safe_summary["status"], RESTORATION_STATE_RESUMABLE)

    def test_zero_partial_with_final_present_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_complete(0)
            fixture.write_partial(0, b"")
            result = fixture.assess()
            self.assertEqual(result.restricted_rows.loc[0, "state"], UNEXPECTED_OR_INVALID)

    def test_zero_byte_final_dicom_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_complete(0, b"")
            result = fixture.assess()
            self.assertEqual(result.restricted_rows.loc[0, "state"], UNEXPECTED_OR_INVALID)
            self.assertEqual(result.safe_summary["status"], BLOCKED_RESTORATION_STATE_INCONSISTENT)

    def test_unexpected_zero_partial_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            unexpected = fixture.destination / "unexpected.dcm.part"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_bytes(b"")
            result = fixture.assess()
            self.assertEqual(result.safe_summary["unexpected_files"], 1)
            self.assertEqual(result.safe_summary["status"], BLOCKED_RESTORATION_STATE_INCONSISTENT)

    def test_symlink_placeholder_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            target = fixture.root / "zero-target"
            target.write_bytes(b"")
            partial = Path(str(fixture.destination_path(0)) + ".part")
            partial.parent.mkdir(parents=True)
            partial.symlink_to(target)
            result = fixture.assess()
            self.assertEqual(result.restricted_rows.loc[0, "state"], UNEXPECTED_OR_INVALID)

    def test_directory_placeholder_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            partial = Path(str(fixture.destination_path(0)) + ".part")
            partial.mkdir(parents=True)
            result = fixture.assess()
            self.assertEqual(result.restricted_rows.loc[0, "state"], UNEXPECTED_OR_INVALID)

    def test_destination_resolving_outside_restricted_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            outside = fixture.root / "outside"
            outside.mkdir()
            fixture.destination.mkdir()
            (fixture.destination / "files").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(Tier1BlockedError):
                fixture.assess()

    def test_ambiguous_locked_url_mapping_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.urls.write_text(
                "".join(
                    f"{OFFICIAL_SOURCE_BASE}{value}\n"
                    for value in [fixture.relative[0], fixture.relative[0], fixture.relative[2]]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(Tier1BlockedError):
                fixture.assess()

    def test_phase2jr2_exact_preflight_counts_reconcile_to_4808(self) -> None:
        summary = {
            "status": RESTORATION_STATE_RESUMABLE,
            "states_reconcile": True,
            **PHASE2JR2_EXPECTED_STATE,
        }
        verified = verify_phase2jr2_preflight_state(summary)
        self.assertEqual(verified["status"], RESTORATION_STATE_RESUMABLE)
        self.assertEqual(
            sum(
                summary[field]
                for field in (
                    "complete_verified_files",
                    "partial_resumable_files",
                    "restartable_zero_placeholder_files",
                    "missing_files",
                    "invalid_files",
                )
            ),
            4808,
        )

    def test_zero_placeholder_restarts_from_byte_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            partial = fixture.write_partial(0, b"")
            row = fixture.assess().restricted_rows.iloc[0].to_dict()

            def fake_run(command, **_kwargs):
                self.assertIn("--continue", command)
                self.assertEqual(partial.stat().st_size, 0)
                partial.write_bytes(b"DICOM-restored")
                return SimpleNamespace(returncode=0)

            with patch("jdim_tier1.phase2jr.shutil.which", return_value="/usr/bin/wget"), patch(
                "jdim_tier1.phase2jr.subprocess.run", side_effect=fake_run
            ):
                success, detail = _download_locked_file(
                    row,
                    netrc_path=fixture.netrc,
                    validator=_validator,
                )
            self.assertTrue(success)
            self.assertEqual(detail, "restored")
            self.assertTrue(fixture.destination_path(0).is_file())

    def test_download_helper_never_overwrites_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            complete = fixture.write_complete(0, b"DICOM-preserve")
            row = fixture.assess().restricted_rows.iloc[0].to_dict()
            with patch(
                "jdim_tier1.phase2jr.subprocess.run",
                side_effect=AssertionError("completed file was downloaded again"),
            ):
                success, detail = _download_locked_file(
                    row,
                    netrc_path=fixture.netrc,
                    validator=_validator,
                )
            self.assertTrue(success)
            self.assertEqual(detail, "already_available_verified")
            self.assertEqual(complete.read_bytes(), b"DICOM-preserve")

    def test_download_helper_never_truncates_nonzero_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            partial = fixture.write_partial(0, b"DICOM-prefix")
            row = fixture.assess().restricted_rows.iloc[0].to_dict()

            def fake_run(command, **_kwargs):
                self.assertIn("--continue", command)
                self.assertEqual(partial.read_bytes(), b"DICOM-prefix")
                with partial.open("ab") as stream:
                    stream.write(b"-continued")
                return SimpleNamespace(returncode=0)

            with patch("jdim_tier1.phase2jr.shutil.which", return_value="/usr/bin/wget"), patch(
                "jdim_tier1.phase2jr.subprocess.run", side_effect=fake_run
            ):
                success, _ = _download_locked_file(
                    row,
                    netrc_path=fixture.netrc,
                    validator=_validator,
                )
            self.assertTrue(success)
            self.assertEqual(
                fixture.destination_path(0).read_bytes(),
                b"DICOM-prefix-continued",
            )

    def test_pending_order_is_nonzero_then_zero_then_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fixture = RestorationFixture(Path(tempdir))
            fixture.write_partial(0, b"DICOM-partial")
            fixture.write_partial(1, b"")
            order: list[str] = []

            def downloader(row, **_kwargs):
                order.append(str(row["state"]))
                destination = Path(str(row["restricted_destination"]))
                partial = Path(str(row["restricted_partial"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(b"DICOM-restored")
                os.replace(partial, destination)
                return True, "restored"

            result = fixture.resume(downloader=downloader)
            self.assertEqual(result.status, LOCKED_ROSTER_SOURCE_RESTORED)
            self.assertEqual(
                order,
                [PARTIAL_RESUMABLE, RESTARTABLE_ZERO_PLACEHOLDER, MISSING],
            )

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
            fixture.write_partial(0, b"")
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
            self.assertNotIn("A1", safe_text)
            self.assertNotIn("p100", safe_text)

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


class Phase2JRQacctTests(unittest.TestCase):
    def test_normal_scc_spacing_parses_and_preserves_raw_hash(self) -> None:
        raw = _qacct_output(separator="        ")
        parsed = parse_qacct_output(raw)
        self.assertEqual(parsed["jobnumber"], 7354017)
        self.assertEqual(parsed["failed"], 100)
        self.assertEqual(parsed["exit_status"], 137)
        self.assertFalse(parsed["scheduler_success"])
        self.assertEqual(parsed["raw_accounting_sha256"], sha256_text(raw))

    def test_tab_separated_qacct_parses(self) -> None:
        parsed = parse_qacct_output(_qacct_output(separator="\t"))
        self.assertEqual(parsed["jobnumber"], 7354017)
        self.assertEqual(str(parsed["ru_wallclock"]), "43201")

    def test_missing_required_qacct_field_fails(self) -> None:
        raw = "\n".join(
            line for line in _qacct_output().splitlines() if not line.startswith("maxvmem")
        )
        with self.assertRaises(Tier1BlockedError):
            parse_qacct_output(raw)

    def test_duplicate_qacct_field_fails(self) -> None:
        raw = _qacct_output() + "\nexit_status    137\n"
        with self.assertRaises(Tier1BlockedError):
            parse_qacct_output(raw)

    def test_failed_zero_with_nonzero_exit_is_not_success(self) -> None:
        raw = _qacct_output(overrides={"failed": "0", "exit_status": "1"})
        self.assertFalse(parse_qacct_output(raw)["scheduler_success"])

    def test_expected_walltime_interruption_verifies_semantically(self) -> None:
        result = verify_qacct_accounting(
            _qacct_output(separator="\t"),
            expected_jobnumber=7354017,
            expected_failed=100,
            expected_exit_status=137,
            expected_ru_wallclock="43201",
        )
        self.assertEqual(result["status"], QACCT_ACCOUNTING_VERIFIED)

    def test_scheduler_and_certificate_disagreement_fails(self) -> None:
        parsed = parse_qacct_output(
            _qacct_output(overrides={"failed": "0", "exit_status": "0"})
        )
        with self.assertRaises(Tier1BlockedError):
            verify_scheduler_certificate_agreement(
                parsed,
                "BLOCKED_LOCKED_SOURCE_RESTORATION",
            )
        failed = parse_qacct_output(_qacct_output())
        with self.assertRaises(Tier1BlockedError):
            verify_scheduler_certificate_agreement(
                failed,
                LOCKED_ROSTER_SOURCE_RESTORED,
            )

    def test_qacct_certificate_gate_is_bound_to_upstream_job(self) -> None:
        raw = _qacct_output(overrides={"failed": "0", "exit_status": "0"})
        result = verify_qacct_certificate_agreement(
            raw,
            expected_jobnumber=7354017,
            certificate_status=RESTORATION_INCOMPLETE_RESUMABLE,
        )
        self.assertTrue(result["states_agree"])
        with self.assertRaises(Tier1BlockedError):
            verify_qacct_certificate_agreement(
                raw,
                expected_jobnumber=7354018,
                certificate_status=RESTORATION_INCOMPLETE_RESUMABLE,
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
        self.assertIn("229a3a88b040eb8728e7f1e73a43711a704c4d7f", common)
        self.assertIn("jdim_phase2j_restoration_v3b", common)
        self.assertIn("jdim_phase2j_restoration_v3c", common)
        self.assertIn("A2->A3->B1->B2", submit)
        self.assertIn("-hold_jid", submit)
        self.assertIn("JDIM_PHASE2JR_UPSTREAM_JOB_ID", submit)
        self.assertNotIn("-t ", submit)
        self.assertNotIn("qsub -V", submit)
        self.assertIn("--max-workers 2", restoration)
        self.assertIn("--soft-stop-seconds 38700", restoration)
        self.assertNotIn("cat \"${HOME}/.netrc\"", common)
        self.assertIn('if [[ ! -f "${A2_CERT}" || ! -f "${A2_RESULTS}" ]]', restoration)
        self.assertIn("phase2jr_verify_upstream_accounting", restoration)
        interface = (ROOT / "scripts/scc_run_jdim_phase2jr_interface.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('if [[ ! -f "${A3_CERT}" ]]', interface)
        self.assertIn("phase2jr_verify_upstream_accounting", interface)


if __name__ == "__main__":
    unittest.main()
