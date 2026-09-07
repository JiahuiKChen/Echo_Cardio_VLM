#!/usr/bin/env python3
"""Focused dependency-light tests for fixed R7F R7G accounting."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import traceback
from typing import Any, Callable, Iterator, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_full_scheduler as scheduler
import lvef_c3_orchestration_core as core
import lvef_c3_r8r_recovery_continuation as r7
import lvef_c3_r8u_r7d_capacity as capacity
import lvef_c3_r8u_r7g_accounting as accounting


ADJUDICATION_COMMIT = "a" * 40
OWNER = "pkarim"
SHA = "b" * 64
QUERY_TIME = datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc)
CREATION_TIME = datetime(2026, 9, 7, 14, 0, 1, tzinfo=timezone.utc)


def _write_private_json(path: Path, value: Mapping[str, Any]) -> str:
    body = core.canonical_json_bytes(value)
    path.write_bytes(body)
    path.chmod(0o600)
    return hashlib.sha256(body).hexdigest()


def _common(artifact_type: str, status: str) -> dict[str, Any]:
    return dict(
        r7._r8u_r7f_common(
            artifact_type=artifact_type,
            status=status,
            implementation_commit=accounting.RUNTIME_IMPLEMENTATION_COMMIT,
        )
    )


def _qsub_evidence() -> dict[str, Any]:
    return {
        "stdout_bytes": 8,
        "stdout_sha256": hashlib.sha256(b"7480830\n").hexdigest(),
        "stderr_bytes": 0,
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "exit_status": 0,
    }


class _Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.attempt_root = self.root / "attempt"
        self.input_root = self.attempt_root / "r7f_inputs"
        self.r7g_root = self.attempt_root / "r8u_r7g_r7f_terminal_adjudication"
        self.paths = {
            "capacity": self.input_root / "capacity.restricted.json",
            "continuation_claim": self.input_root / "claim.restricted.json",
            "array_submission": self.input_root / "array.restricted.json",
            "finalizer_submission": self.input_root / "finalizer.restricted.json",
            "combined_submission": self.input_root / "combined.restricted.json",
            "scheduler_account": self.input_root / "account.restricted.json",
            "probe_submission": self.input_root / "probe_submission.restricted.json",
            "probe_terminal": self.input_root / "probe_terminal.restricted.json",
        }
        self.attempt_root.mkdir(mode=0o700)
        self.input_root.mkdir(mode=0o700)
        self.receipts: dict[str, dict[str, Any]] = {}
        self.hashes: dict[str, str] = {}
        self._build()

    def _seal(self, name: str, value: Mapping[str, Any]) -> str:
        self.receipts[name] = dict(value)
        digest = _write_private_json(self.paths[name], value)
        self.hashes[name] = digest
        return digest

    def _build(self) -> None:
        runtime = accounting.RUNTIME_IMPLEMENTATION_COMMIT
        environment = {
            "HOME": "/home/pkarim",
            "LOGNAME": OWNER,
            "PATH": "/usr/bin:/bin",
            "USER": OWNER,
        }
        environment_sha = scheduler.qsub_environment_sha256(environment)
        scripts = {
            "controller_sha256": "1" * 64,
            "full_sequential_sha256": "2" * 64,
            "production_stages_sha256": "3" * 64,
            "preservation_sha256": "4" * 64,
            "retirement_sha256": "5" * 64,
            "finalizer_sha256": "6" * 64,
            "runner_sha256": "7" * 64,
        }
        self.environment = environment
        self.environment_sha = environment_sha
        self.scripts = scripts

        capacity_receipt = {
            "artifact_type": capacity.R8U_R7F_CAPACITY_ARTIFACT_TYPE,
            "status": capacity.R8U_R7F_CAPACITY_STATUS_PASS,
            "original_attempt_id": accounting.ATTEMPT_ID,
            "original_plan_sha256": accounting.PLAN_SHA256,
            "original_scientific_governing_commit": accounting.SCIENTIFIC_COMMIT,
            "r7f_runtime_commit": runtime,
        }
        capacity_sha = self._seal("capacity", capacity_receipt)

        account = {
            **dict(
                r7._r8u_r7d_common(
                    artifact_type=(
                        "lvef_c3_r8u_r7d_scheduler_account_authority_v1"
                    ),
                    status="AUTHORIZED_R8U_R7D_SCHEDULER_ACCOUNT",
                    implementation_commit=(
                        r7.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT
                    ),
                )
            ),
            "expected_effective_uid": os.geteuid(),
            "expected_scheduler_username": OWNER,
            "canonical_home": "/home/pkarim",
            "runner_sha256": scripts["runner_sha256"],
            "python_sha256": "8" * 64,
            "qsub_environment_sha256": environment_sha,
            "sealed_qsub_environment": environment,
            "authorized_worker_roles": list(r7.R8U_R7D_WORKER_ROLES),
        }
        account_sha = self._seal("scheduler_account", account)

        claim = {
            **_common(
                "lvef_c3_r8u_r7f_fixed_continuation_claim_v1",
                "AUTHORIZED_FRESH_R7F_CONTINUATION_17_19",
            ),
            "capacity_receipt_sha256": capacity_sha,
            "scheduler_account_authority_sha256": account_sha,
            "qsub_environment_sha256": environment_sha,
            "script_authority": scripts,
            "continuation_task_range": "17-19",
            "continuation_task_ids": [17, 18, 19],
            "continuation_task_count": 3,
            "continuation_max_concurrency": 1,
            "probe_task_id": 17,
            "probe_submission_count": 1,
            "scientific_array_submission_count": 1,
            "held_finalizer_submission_count": 1,
            "total_new_qsub_maximum": 3,
            "automatic_retry_authorized": False,
            "whole_stage_retry_authorized": False,
            "fourth_submission_reachable": False,
        }
        claim_sha = self._seal("continuation_claim", claim)

        probe_submission = {
            **_common(
                "lvef_c3_r8u_r7f_context_probe_submission_v1",
                "PASS_EXACT_ONE_R7F_CPU_ARRAY_CONTEXT_PROBE_QSUB",
            ),
            "scheduler_account_authority_sha256": account_sha,
            "r7f_continuation_claim_sha256": claim_sha,
            "r7f_capacity_receipt_sha256": capacity_sha,
            "probe_job_id": "7480822",
            "probe_job_name": r7._r8u_r7d_probe_job_name(runtime),
            "worker_role": r7.R8U_R7D_PROBE_ROLE,
            "probe_qsub_argv_sha256": accounting._r7f_qsub_argv_sha256(
                r7._r8u_r7d_probe_command(runtime)
            ),
            "probe_qsub_evidence": _qsub_evidence(),
            "qsub_environment_sha256": environment_sha,
            "array_task_id": 17,
            "array_task_count": 1,
            "array_max_concurrency": 1,
            "scheduler_submission_count": 1,
            "scheduler_submission_maximum": 3,
            "scientific_execution_authorized": False,
        }
        probe_submission_sha = self._seal("probe_submission", probe_submission)

        probe_terminal = {
            **_common(
                "lvef_c3_r8u_r7f_context_probe_terminal_v1",
                "PASS_R7F_CONTINUATION_WORKER_CONTEXT_PROBE",
            ),
            "probe_submission_receipt_sha256": probe_submission_sha,
            "probe_job_id": "7480822",
            "qstat_classification": next(
                iter(scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS)
            ),
            "controlling_worker_identity": "PASS",
            "failed": 0,
            "exit_status": 0,
            "task_id": 17,
            "scientific_artifacts_created": 0,
            "cloud_requests": 0,
            "dicom_body_reads": 0,
            "npz_body_reads": 0,
            "gpu_executions": 0,
        }
        probe_terminal_sha = self._seal("probe_terminal", probe_terminal)

        array_submission = {
            **_common(
                "lvef_c3_r8u_r7f_array_submission_v1",
                "PASS_EXACT_R7F_ARRAY_17_19_QSUB",
            ),
            "capacity_receipt_sha256": capacity_sha,
            "continuation_claim_sha256": claim_sha,
            "probe_terminal_receipt_sha256": probe_terminal_sha,
            "scheduler_account_authority_sha256": account_sha,
            "array_job_id": "7480830",
            "array_job_name": r7._r8u_r7d_array_job_name(runtime),
            "array_worker_role": r7.R8U_R7D_ARRAY_ROLE,
            "array_qsub_argv_sha256": accounting._r7f_qsub_argv_sha256(
                r7._r8u_r7d_array_command(runtime)
            ),
            "array_qsub_evidence": _qsub_evidence(),
            "qsub_environment_sha256": environment_sha,
            "array_task_range": "17-19",
            "array_task_ids": [17, 18, 19],
            "array_task_count": 3,
            "array_max_concurrency": 1,
            "scheduler_submission_count": 1,
        }
        array_sha = self._seal("array_submission", array_submission)

        finalizer_submission = {
            **_common(
                "lvef_c3_r8u_r7f_finalizer_submission_v1",
                "PASS_EXACT_R7F_HELD_FINALIZER_QSUB",
            ),
            "capacity_receipt_sha256": capacity_sha,
            "continuation_claim_sha256": claim_sha,
            "probe_terminal_receipt_sha256": probe_terminal_sha,
            "scheduler_account_authority_sha256": account_sha,
            "array_submission_receipt_sha256": array_sha,
            "array_job_id": "7480830",
            "finalizer_job_id": "7480831",
            "finalizer_job_name": r7._r8u_r7d_finalizer_job_name(runtime),
            "finalizer_worker_role": r7.R8U_R7D_FINALIZER_ROLE,
            "finalizer_qsub_argv_sha256": accounting._r7f_qsub_argv_sha256(
                r7._r8u_r7d_finalizer_command(runtime, "7480830")
            ),
            "finalizer_qsub_evidence": _qsub_evidence(),
            "qsub_environment_sha256": environment_sha,
            "hold_jid": "7480830",
            "finalizer_held_on_array": True,
            "scheduler_submission_count": 1,
        }
        finalizer_sha = self._seal("finalizer_submission", finalizer_submission)

        combined = {
            **_common(
                "lvef_c3_r8u_r7f_fixed_continuation_submission_v1",
                "PASS_EXACT_R7F_ARRAY_17_19_AND_HELD_FINALIZER",
            ),
            "capacity_receipt_sha256": capacity_sha,
            "probe_terminal_receipt_sha256": probe_terminal_sha,
            "continuation_claim_sha256": claim_sha,
            "array_submission_receipt_sha256": array_sha,
            "finalizer_submission_receipt_sha256": finalizer_sha,
            "array_job_id": "7480830",
            "array_job_name": r7._r8u_r7d_array_job_name(runtime),
            "array_worker_role": r7.R8U_R7D_ARRAY_ROLE,
            "finalizer_job_id": "7480831",
            "finalizer_job_name": r7._r8u_r7d_finalizer_job_name(runtime),
            "finalizer_worker_role": r7.R8U_R7D_FINALIZER_ROLE,
            "qsub_environment_sha256": environment_sha,
            "array_task_range": "17-19",
            "array_task_ids": [17, 18, 19],
            "array_task_count": 3,
            "array_max_concurrency": 1,
            "finalizer_held_on_array": True,
            "scheduler_submission_count": 2,
            "total_new_qsub_submissions": 3,
            "scheduler_submission_maximum": 3,
            "whole_stage_retry_authorized": False,
            "fourth_submission_reachable": False,
        }
        self._seal("combined_submission", combined)

    @contextmanager
    def patched(self) -> Iterator[None]:
        terminal_path = self.r7g_root / "terminal_authority.restricted.json"
        accounting_root = self.r7g_root / "accounting"
        replacements = {
            "CAPACITY_RECEIPT_PATH": self.paths["capacity"],
            "CONTINUATION_CLAIM_PATH": self.paths["continuation_claim"],
            "ARRAY_SUBMISSION_PATH": self.paths["array_submission"],
            "FINALIZER_SUBMISSION_PATH": self.paths["finalizer_submission"],
            "COMBINED_SUBMISSION_PATH": self.paths["combined_submission"],
            "ACCOUNT_AUTHORITY_PATH": self.paths["scheduler_account"],
            "PROBE_SUBMISSION_PATH": self.paths["probe_submission"],
            "PROBE_TERMINAL_PATH": self.paths["probe_terminal"],
            "R7G_ROOT": self.r7g_root,
            "TERMINAL_AUTHORITY_PATH": terminal_path,
            "ACCOUNTING_ROOT": accounting_root,
        }
        with ExitStack() as stack:
            for name, value in replacements.items():
                stack.enter_context(mock.patch.object(accounting, name, value))
            stack.enter_context(
                mock.patch.dict(
                    accounting.EXPECTED_R7F_RECEIPT_SHA256,
                    {
                        name: self.hashes[name]
                        for name in accounting.R7F_AUTHORITY_NAMES
                    },
                    clear=True,
                )
            )
            yield


@contextmanager
def _fixture() -> Iterator[_Fixture]:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = _Fixture(Path(temporary))
        with fixture.patched():
            yield fixture


def _expect_code(code: str, callback: Callable[[], object]) -> None:
    try:
        callback()
    except accounting.R7GAccountingError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def _authority() -> accounting.TerminalAuthorityResult:
    return accounting.ensure_terminal_authority(
        adjudication_implementation_commit=ADJUDICATION_COMMIT
    )


def _record(
    spec: accounting.FixedAccountingSpec,
    *,
    failed: str = "0",
    exit_status: str = "0",
    owner: str | None = None,
    name: str | None = None,
    start: str = "Sun Sep 07 10:01:00 2026",
    end: str = "Sun Sep 07 10:02:00 2026",
) -> dict[str, str]:
    return {
        "jobnumber": spec.job_id,
        "taskid": "undefined" if spec.task_id is None else str(spec.task_id),
        "jobname": spec.expected_job_name if name is None else name,
        "owner": spec.expected_owner if owner is None else owner,
        "qsub_time": "Sun Sep 07 10:00:00 2026",
        "start_time": start,
        "end_time": end,
        "failed": failed,
        "exit_status": exit_status,
        "ru_wallclock": "60.75",
        "qname": "gpu.q",
        "hostname": "compute.example",
    }


def _qacct_bytes(record: Mapping[str, str]) -> bytes:
    return b"\n".join(
        f"{key:<13} {value}".encode("utf-8") for key, value in record.items()
    ) + b"\n"


def test_terminal_authority_is_derived_sealed_and_reused() -> None:
    with _fixture() as fixture:
        created = _authority()
        assert created.created is True
        authority = created.authority
        assert authority["runtime_implementation_commit"] == (
            "2223d9768a1cc23efbe95a3c5474ea747a383a10"
        )
        assert authority["probe_job_id"] == "7480822"
        assert authority["expected_owner"] == OWNER
        assert authority["array_job_name"] == "lvef_c3_r8u_r7d_seq_2223d976"
        assert authority["array_role"] == r7.R8U_R7D_ARRAY_ROLE
        assert authority["finalizer_role"] == r7.R8U_R7D_FINALIZER_ROLE
        assert authority["scheduler_log_basenames"] == {
            "array_task_17": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.17",
            "array_task_18": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.18",
            "array_task_19": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.19",
            "finalizer": "lvef_c3_r8u_r7d_fin_2223d976.o7480831",
        }
        assert stat.S_IMODE(accounting.TERMINAL_AUTHORITY_PATH.stat().st_mode) == 0o600
        reused = _authority()
        assert reused.created is False
        assert reused.authority_sha256 == created.authority_sha256
        assert reused.authority == created.authority
        assert (
            accounting.validate_fixed_scheduler_accounting_environment(authority)
            == fixture.environment
        )
        assert fixture.receipts["scheduler_account"]["implementation_commit"] != (
            accounting.RUNTIME_IMPLEMENTATION_COMMIT
        )


def test_exact_four_fixed_specs_and_no_caller_scope() -> None:
    with _fixture():
        authority = _authority().authority
        specs = accounting.fixed_accounting_specs(authority)
        assert [(item.job_id, item.task_id) for item in specs] == [
            ("7480830", 17),
            ("7480830", 18),
            ("7480830", 19),
            ("7480831", None),
        ]
        substitutions = (
            {"job_id": "7478863"},
            {"task_id": 20},
            {"job_kind": "NON_ARRAY_FINALIZER", "job_id": "9999999", "task_id": None},
        )
        for substitution in substitutions:
            fake = accounting.FixedAccountingSpec(
                **{**specs[0].__dict__, **substitution}
            )
            _expect_code(
                "R8U_R7G_ACCOUNTING_SCOPE_INVALID",
                lambda fake=fake: accounting.query_fixed_qacct_record(
                    fake,
                    terminal_authority=authority,
                    environment={"PATH": "/usr/bin"},
                    runner=lambda *_args, **_kwargs: None,
                    tool_validator=lambda: None,
                ),
            )
        forged_path = accounting.FixedAccountingSpec(
            **{
                **specs[0].__dict__,
                "receipt_path": specs[0].receipt_path.with_name("forged.json"),
            }
        )
        _expect_code(
            "R8U_R7G_ACCOUNTING_SCOPE_INVALID",
            lambda: accounting.build_accounting_receipt(
                forged_path,
                _record(specs[0]),
                terminal_authority=authority,
                accounting_query_timestamp=QUERY_TIME,
                creation_timestamp=CREATION_TIME,
            ),
        )
        forged_authority = dict(authority)
        forged_authority["expected_owner"] = "another_owner"
        _expect_code(
            "R8U_R7G_TERMINAL_AUTHORITY_BINDING_INVALID",
            lambda: accounting.fixed_accounting_specs(forged_authority),
        )


def test_hash_substitution_and_cross_binding_fail_closed() -> None:
    with _fixture() as fixture:
        replacement = dict(fixture.receipts["capacity"])
        replacement["extra"] = "substitution"
        _write_private_json(fixture.paths["capacity"], replacement)
        _expect_code("R8U_R7G_R7F_AUTHORITY_HASH_INVALID", _authority)

    with _fixture() as fixture:
        replacement = dict(fixture.receipts["combined_submission"])
        replacement["array_job_id"] = "9999999"
        replacement_sha = _write_private_json(
            fixture.paths["combined_submission"], replacement
        )
        accounting.EXPECTED_R7F_RECEIPT_SHA256["combined_submission"] = replacement_sha
        _expect_code("R8U_R7G_R7F_AUTHORITY_INVALID", _authority)


def test_valid_current_qacct_records_and_failure_projection() -> None:
    with _fixture():
        authority = _authority().authority
        specs = accounting.fixed_accounting_specs(authority)
        for spec in specs:
            projected = accounting.project_fixed_qacct_record(spec, _record(spec))
            assert projected["terminal_classification"] == "PASS"
            assert projected["failed"] == 0
            assert projected["exit_status"] == 0
            assert projected["wall_seconds"] == 60

        failed = accounting.project_fixed_qacct_record(
            specs[0], _record(specs[0], failed="37 : shepherd")
        )
        assert failed["failed"] == 37
        assert failed["terminal_classification"] == "FAIL"
        exited = accounting.project_fixed_qacct_record(
            specs[-1], _record(specs[-1], exit_status="78")
        )
        assert exited["exit_status"] == 78
        assert exited["terminal_classification"] == "FAIL"


def test_wrong_name_owner_and_chronology_fail() -> None:
    with _fixture():
        authority = _authority().authority
        spec = accounting.fixed_accounting_specs(authority)[0]
        for record, code in (
            (_record(spec, name="truncated-name"), "R8U_R7G_QACCT_IDENTITY_INVALID"),
            (_record(spec, owner="someone_else"), "R8U_R7G_QACCT_IDENTITY_INVALID"),
            (
                _record(
                    spec,
                    start="Sun Sep 07 10:03:00 2026",
                    end="Sun Sep 07 10:02:00 2026",
                ),
                "R8U_R7G_QACCT_RECORD_INVALID",
            ),
        ):
            _expect_code(
                code, lambda record=record: accounting.project_fixed_qacct_record(spec, record)
            )


def test_exactly_one_qacct_record_and_exact_argv() -> None:
    with _fixture():
        authority = _authority().authority
        spec = accounting.fixed_accounting_specs(authority)[1]
        calls: list[list[str]] = []

        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, _qacct_bytes(_record(spec)), b"")

        observed = accounting.query_fixed_qacct_record(
            spec,
            terminal_authority=authority,
            environment={"PATH": "/usr/bin"},
            runner=runner,
            tool_validator=lambda: None,
        )
        assert observed["taskid"] == "18"
        assert calls == [[str(accounting.QACCT_PATH), "-j", "7480830", "-t", "18"]]

        def query(payload: bytes) -> None:
            _expect_code(
                "R8U_R7G_ACCOUNTING_NOT_AVAILABLE",
                lambda: accounting.query_fixed_qacct_record(
                    spec,
                    terminal_authority=authority,
                    environment={"PATH": "/usr/bin"},
                    runner=lambda argv, **_kwargs: subprocess.CompletedProcess(
                        argv, 0, payload, b""
                    ),
                    tool_validator=lambda: None,
                ),
            )

        query(b"")
        one = _qacct_bytes(_record(spec))
        query(one + b"========\n" + one)


def test_receipt_reuse_collision_mode_and_symlink() -> None:
    with _fixture() as fixture:
        authority = _authority().authority
        spec = accounting.fixed_accounting_specs(authority)[2]
        calls = 0

        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(argv, 0, _qacct_bytes(_record(spec)), b"")

        clock_values = iter((QUERY_TIME, CREATION_TIME))
        first = accounting.reuse_or_query_fixed_accounting(
            spec,
            terminal_authority=authority,
            environment=fixture.environment,
            runner=runner,
            tool_validator=lambda: None,
            clock=lambda: next(clock_values),
        )
        assert first.created is True and first.qacct_query_count == 1
        assert calls == 1
        assert stat.S_IMODE(spec.receipt_path.stat().st_mode) == 0o600
        second = accounting.reuse_or_query_fixed_accounting(
            spec,
            terminal_authority=authority,
            environment=fixture.environment,
            runner=runner,
            tool_validator=lambda: None,
        )
        assert second.created is False and second.qacct_query_count == 0
        assert second.receipt_sha256 == first.receipt_sha256
        assert calls == 1

        different = accounting.build_accounting_receipt(
            spec,
            _record(spec, exit_status="78"),
            terminal_authority=authority,
            accounting_query_timestamp=QUERY_TIME,
            creation_timestamp=CREATION_TIME,
        )
        _expect_code(
            "R8U_R7G_ACCOUNTING_RECEIPT_COLLISION",
            lambda: accounting.publish_accounting_receipt(
                different, spec, terminal_authority=authority
            ),
        )

        spec.receipt_path.unlink()
        target = fixture.input_root / "untrusted.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        spec.receipt_path.symlink_to(target)
        _expect_code(
            "R8U_R7G_ACCOUNTING_RECEIPT_FILE_INVALID",
            lambda: accounting.load_accounting_receipt(
                spec, terminal_authority=authority
            ),
        )


def test_non_private_authority_and_receipt_modes_fail() -> None:
    with _fixture() as fixture:
        fixture.paths["capacity"].chmod(0o644)
        _expect_code("R8U_R7G_R7F_AUTHORITY_FILE_INVALID", _authority)

    with _fixture():
        authority = _authority().authority
        spec = accounting.fixed_accounting_specs(authority)[0]
        receipt = accounting.build_accounting_receipt(
            spec,
            _record(spec),
            terminal_authority=authority,
            accounting_query_timestamp=QUERY_TIME,
            creation_timestamp=CREATION_TIME,
        )
        _write_private_json(spec.receipt_path, receipt)
        spec.receipt_path.chmod(0o644)
        _expect_code(
            "R8U_R7G_ACCOUNTING_RECEIPT_FILE_INVALID",
            lambda: accounting.load_accounting_receipt(
                spec, terminal_authority=authority
            ),
        )


def test_parser_rejects_duplicate_fields_and_malformed_bytes() -> None:
    for payload in (
        b"jobnumber 7480830\njobnumber 7480830\n",
        b"not-a-field\n",
        b"jobnumber 7480830\x00\n",
        b"\xff",
    ):
        _expect_code(
            "R8U_R7G_QACCT_OUTPUT_INVALID",
            lambda payload=payload: accounting.parse_qacct_records(payload),
        )


TESTS = (
    test_terminal_authority_is_derived_sealed_and_reused,
    test_exact_four_fixed_specs_and_no_caller_scope,
    test_hash_substitution_and_cross_binding_fail_closed,
    test_valid_current_qacct_records_and_failure_projection,
    test_wrong_name_owner_and_chronology_fail,
    test_exactly_one_qacct_record_and_exact_argv,
    test_receipt_reuse_collision_mode_and_symlink,
    test_non_private_authority_and_receipt_modes_fail,
    test_parser_rejects_duplicate_fields_and_malformed_bytes,
)


def main() -> int:
    failures = 0
    for test in TESTS:
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}", file=sys.stderr)
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    print(f"SUMMARY passed={len(TESTS) - failures} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
