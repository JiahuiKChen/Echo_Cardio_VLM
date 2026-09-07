#!/usr/bin/env python3
"""Dependency-light tests for fixed R8U-R7C terminal accounting.

All qacct output is synthetic.  This module never contacts Grid Engine and
never opens a scientific artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import traceback
from typing import Any, Callable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_orchestration_core as core
import lvef_c3_r8u_r7c_accounting as accounting


ADJUDICATION_COMMIT = "a" * 40
QUERY_TIME = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
CREATION_TIME = datetime(2026, 9, 6, 12, 0, 1, tzinfo=timezone.utc)


def _record(
    spec: accounting.FixedAccountingSpec,
    **overrides: str,
) -> dict[str, str]:
    value = {
        "qname": "gpu.q",
        "hostname": "scc-gpu-01.scc.bu.edu",
        "group": "synthetic",
        "owner": accounting.OWNER,
        "project": "mimicecho",
        "jobname": spec.expected_job_name,
        "jobnumber": spec.job_id,
        "taskid": "undefined" if spec.task_id is None else str(spec.task_id),
        "qsub_time": "Sun Sep 06 08:00:00 2026",
        "start_time": "Sun Sep 06 08:05:00 2026",
        "end_time": "Sun Sep 06 08:15:00 2026",
        "failed": "0",
        "exit_status": "0",
        "ru_wallclock": "600.0",
        "ru_utime": "10.000",
    }
    value.update(overrides)
    return value


def _payload(*records: Mapping[str, str]) -> bytes:
    lines: list[str] = []
    for record in records:
        lines.append("==============================================================")
        lines.extend(f"{key:<14} {value}" for key, value in record.items())
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _completed(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], 0, payload, b"")


def _receipt(
    spec: accounting.FixedAccountingSpec,
    record: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return accounting.build_accounting_receipt(
        spec,
        _record(spec) if record is None else record,
        adjudication_implementation_commit=ADJUDICATION_COMMIT,
        accounting_query_timestamp=QUERY_TIME,
        creation_timestamp=CREATION_TIME,
    )


def _assert_code(code: str, callback: Callable[[], Any]) -> None:
    try:
        callback()
    except accounting.R7CAccountingError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def _clock() -> Callable[[], datetime]:
    values = iter((QUERY_TIME, CREATION_TIME))
    return lambda: next(values)


def test_valid_task17_accounting_record() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    projected = accounting.project_fixed_qacct_record(spec, _record(spec))
    assert projected["terminal_classification"] == "PASS"
    assert projected["wall_seconds"] == 600
    assert projected["qacct_task_number"] == "17"


def test_valid_task18_accounting_record() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[18]
    projected = accounting.project_fixed_qacct_record(spec, _record(spec))
    assert projected["qacct_job_number"] == accounting.ARRAY_JOB_ID
    assert projected["qacct_task_number"] == "18"


def test_valid_task19_accounting_record() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[19]
    projected = accounting.project_fixed_qacct_record(spec, _record(spec))
    assert projected["qacct_task_number"] == "19"
    assert spec.receipt_path.name == (
        "array_7478863_task_19_accounting.restricted.json"
    )


def test_valid_nonarray_finalizer_accounting_record() -> None:
    spec = accounting.FINALIZER_ACCOUNTING_SPEC
    projected = accounting.project_fixed_qacct_record(spec, _record(spec))
    assert projected["qacct_task_number"] == "undefined"
    assert projected["terminal_classification"] == "PASS"
    receipt = _receipt(spec)
    assert receipt["task_id"] is None
    assert receipt["finalizer_job_id"] == accounting.FINALIZER_JOB_ID


def test_wrong_array_job_id_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, jobnumber="7478862")
        ),
    )


def test_wrong_task_id_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, taskid="18")
        ),
    )


def test_task_outside_17_19_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[19]
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, taskid="20")
        ),
    )


def test_finalizer_array_task_identity_is_rejected() -> None:
    spec = accounting.FINALIZER_ACCOUNTING_SPEC
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, taskid="17")
        ),
    )


def test_duplicate_qacct_records_are_not_available() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    runner = mock.Mock(return_value=_completed(_payload(_record(spec), _record(spec))))
    _assert_code(
        "R8U_R7C_ACCOUNTING_NOT_AVAILABLE",
        lambda: accounting.query_fixed_qacct_record(
            spec,
            environment={"USER": "pkarim"},
            runner=runner,
            tool_validator=lambda: None,
        ),
    )
    assert runner.call_count == 1


def test_missing_qacct_record_is_not_available() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    runner = mock.Mock(return_value=_completed(b""))
    _assert_code(
        "R8U_R7C_ACCOUNTING_NOT_AVAILABLE",
        lambda: accounting.query_fixed_qacct_record(
            spec,
            environment={"USER": "pkarim"},
            runner=runner,
            tool_validator=lambda: None,
        ),
    )
    assert runner.call_count == 1


def test_nonzero_failed_is_captured_as_fail_not_parse_failure() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec, _record(spec, failed="37 : h_rt exceeded"))
    assert receipt["failed"] == 37
    assert receipt["exit_status"] == 0
    assert receipt["terminal_classification"] == "FAIL"
    assert receipt["normalized_qacct_record"]["failed"] == (
        "37 : h_rt exceeded"
    )


def test_nonzero_exit_status_is_captured_as_fail_not_parse_failure() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[18]
    receipt = _receipt(spec, _record(spec, exit_status="78"))
    assert receipt["failed"] == 0
    assert receipt["exit_status"] == 78
    assert receipt["terminal_classification"] == "FAIL"


def test_end_before_start_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_RECORD_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec,
            _record(
                spec,
                start_time="Sun Sep 06 08:15:00 2026",
                end_time="Sun Sep 06 08:14:59 2026",
            ),
        ),
    )


def test_submission_after_start_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_RECORD_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, qsub_time="Sun Sep 06 08:06:00 2026")
        ),
    )


def test_malformed_wall_time_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    for malformed in ("", "-1", "NaN", "1e3", "600 seconds"):
        _assert_code(
            "R8U_R7C_QACCT_RECORD_INVALID",
            lambda malformed=malformed: accounting.project_fixed_qacct_record(
                spec, _record(spec, ru_wallclock=malformed)
            ),
        )


def test_fractional_wall_time_is_sealed_and_reported_as_whole_seconds() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec, _record(spec, ru_wallclock="601.539"))
    assert receipt["wall_seconds"] == 601
    assert receipt["normalized_qacct_record"]["ru_wallclock"] == "601.539"


def test_wrong_owner_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, owner="someone_else")
        ),
    )


def test_wrong_continuation_receipt_hash_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec)
    receipt["continuation_submission_receipt_sha256"] = "b" * 64
    _assert_code(
        "R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID",
        lambda: accounting.validate_accounting_receipt(
            receipt,
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        ),
    )


def test_wrong_runtime_implementation_commit_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec)
    receipt["runtime_implementation_commit"] = "b" * 40
    _assert_code(
        "R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID",
        lambda: accounting.validate_accounting_receipt(
            receipt,
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        ),
    )


def test_runtime_and_adjudication_commits_are_separate() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec)
    assert receipt["runtime_implementation_commit"] == (
        accounting.RUNTIME_IMPLEMENTATION_COMMIT
    )
    assert receipt["adjudication_implementation_commit"] == ADJUDICATION_COMMIT
    assert receipt["runtime_implementation_commit"] != (
        receipt["adjudication_implementation_commit"]
    )
    _assert_code(
        "R8U_R7C_ADJUDICATION_COMMIT_INVALID",
        lambda: accounting.build_accounting_receipt(
            spec,
            _record(spec),
            adjudication_implementation_commit=(
                accounting.RUNTIME_IMPLEMENTATION_COMMIT
            ),
            accounting_query_timestamp=QUERY_TIME,
            creation_timestamp=CREATION_TIME,
        ),
    )


def test_observer_does_not_need_live_worker_sge_variables() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    runner = mock.Mock(return_value=_completed(_payload(_record(spec))))
    with mock.patch.dict(os.environ, {}, clear=True):
        observed = accounting.query_fixed_qacct_record(
            spec,
            environment={"USER": "pkarim", "SGE_ROOT": "/fixed"},
            runner=runner,
            tool_validator=lambda: None,
        )
    assert observed["taskid"] == "17"
    assert runner.call_args.kwargs["env"] == {
        "USER": "pkarim",
        "SGE_ROOT": "/fixed",
    }


def test_consumed_live_worker_authority_remains_historically_pinned() -> None:
    # Later additive epochs may extend the shared controller/runner.  R7C binds
    # the exact blobs actually executed by consumed R7 through their immutable
    # runtime commit rather than falsely requiring the current whole file to
    # retain the historical hash.
    for path, expected in (
        (
            "scripts/lvef_c3_r8r_recovery_continuation.py",
            accounting.RUNTIME_CONTROLLER_SHA256,
        ),
        (
            "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh",
            accounting.RUNTIME_RUNNER_SHA256,
        ),
    ):
        completed = subprocess.run(
            [
                "/usr/bin/git", "show",
                f"{accounting.RUNTIME_IMPLEMENTATION_COMMIT}:{path}",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 0 and completed.stderr == b""
        assert hashlib.sha256(completed.stdout).hexdigest() == expected
    source = inspect.getsource(accounting)
    assert "validate_r8u_r7_continuation_worker_submission(" not in source
    assert "build_worker_scheduler_context(" not in source
    assert "QSTAT_PATH" not in source


def test_qstat_display_name_truncation_is_not_exact_authority() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_QACCT_IDENTITY_INVALID",
        lambda: accounting.project_fixed_qacct_record(
            spec, _record(spec, jobname="lvef_c3_r8")
        ),
    )


def test_existing_valid_receipt_is_reused_without_query() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        expected = _receipt(spec)
        core.atomic_write_json_no_clobber(
            path, expected, attempt_id=accounting.ATTEMPT_ID
        )
        runner = mock.Mock(side_effect=AssertionError("qacct must not run"))
        result = accounting._reuse_or_query_accounting_at_path(
            spec,
            path,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
            environment={"USER": "pkarim"},
            runner=runner,
            tool_validator=lambda: None,
            clock=_clock(),
        )
        assert result.created is False
        assert result.qacct_query_count == 0
        assert result.receipt == expected
        runner.assert_not_called()


def test_no_clobber_race_preserves_competing_path() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        competing = b'{"status":"COMPETING"}'

        def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            path.write_bytes(competing)
            path.chmod(0o600)
            return _completed(_payload(_record(spec)))

        _assert_code(
            "R8U_R7C_ACCOUNTING_RECEIPT_COLLISION",
            lambda: accounting._reuse_or_query_accounting_at_path(
                spec,
                path,
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
                environment={"USER": "pkarim"},
                runner=runner,
                tool_validator=lambda: None,
                clock=_clock(),
            ),
        )
        assert path.read_bytes() == competing


def test_symlink_receipt_path_is_rejected_before_query() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        target = root / "target.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        path = root / "receipt.json"
        path.symlink_to(target)
        runner = mock.Mock(side_effect=AssertionError("qacct must not run"))
        _assert_code(
            "R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID",
            lambda: accounting._reuse_or_query_accounting_at_path(
                spec,
                path,
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
                environment={"USER": "pkarim"},
                runner=runner,
                tool_validator=lambda: None,
                clock=_clock(),
            ),
        )
        runner.assert_not_called()


def test_nonregular_receipt_path_is_rejected_before_query() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        path.mkdir(mode=0o700)
        runner = mock.Mock(side_effect=AssertionError("qacct must not run"))
        _assert_code(
            "R8U_R7C_ACCOUNTING_RECEIPT_FILE_INVALID",
            lambda: accounting._reuse_or_query_accounting_at_path(
                spec,
                path,
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
                environment={"USER": "pkarim"},
                runner=runner,
                tool_validator=lambda: None,
                clock=_clock(),
            ),
        )
        runner.assert_not_called()


def test_receipt_publication_mode_is_exactly_0600() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        accounting.publish_accounting_receipt(
            path,
            _receipt(spec),
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        )
        metadata = path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1


def test_receipt_publication_forces_0600_under_restrictive_umask() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        previous_umask = os.umask(0o777)
        try:
            value, digest = accounting.publish_accounting_receipt(
                path,
                _receipt(spec),
                spec,
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
            )
        finally:
            os.umask(previous_umask)
        observed = path.lstat()
        assert stat.S_ISREG(observed.st_mode)
        assert stat.S_IMODE(observed.st_mode) == 0o600
        assert observed.st_nlink == 1
        reopened, reopened_digest = accounting.load_accounting_receipt(
            path,
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        )
        assert reopened == value
        assert reopened_digest == digest
        partial = path.parent / (
            f".{path.name}.{accounting.ATTEMPT_ID}.partial"
        )
        assert not os.path.lexists(partial)


def test_atomic_publication_reopens_and_revalidates() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        path = root / "receipt.json"
        original = accounting.load_accounting_receipt
        with mock.patch.object(
            accounting, "load_accounting_receipt", wraps=original
        ) as reopen:
            value, digest = accounting.publish_accounting_receipt(
                path,
                _receipt(spec),
                spec,
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
            )
        assert reopen.call_count == 1
        assert value["terminal_classification"] == "PASS"
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        partial = root / f".{path.name}.{accounting.ATTEMPT_ID}.partial"
        assert not os.path.lexists(partial)


def test_fixed_task_query_argv_is_exact_and_called_once() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[18]
    runner = mock.Mock(return_value=_completed(_payload(_record(spec))))
    accounting.query_fixed_qacct_record(
        spec,
        environment={"USER": "pkarim"},
        runner=runner,
        tool_validator=lambda: None,
    )
    assert runner.call_count == 1
    assert runner.call_args.args[0] == [
        str(accounting.QACCT_PATH),
        "-j",
        accounting.ARRAY_JOB_ID,
        "-t",
        "18",
    ]


def test_fixed_finalizer_query_argv_is_exact_and_nonarray() -> None:
    spec = accounting.FINALIZER_ACCOUNTING_SPEC
    runner = mock.Mock(return_value=_completed(_payload(_record(spec))))
    accounting.query_fixed_qacct_record(
        spec,
        environment={"USER": "pkarim"},
        runner=runner,
        tool_validator=lambda: None,
    )
    assert runner.call_count == 1
    assert runner.call_args.args[0] == [
        str(accounting.QACCT_PATH),
        "-j",
        accounting.FINALIZER_JOB_ID,
    ]
    assert "-t" not in runner.call_args.args[0]


def test_normalized_raw_record_hash_ignores_order_and_horizontal_spacing() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    first = _record(spec)
    second = {
        key: f"  {value.replace(' ', '  ')}\t"
        for key, value in reversed(tuple(first.items()))
    }
    assert accounting.normalized_qacct_record_sha256(first) == (
        accounting.normalized_qacct_record_sha256(second)
    )


def test_duplicate_key_within_qacct_record_is_rejected() -> None:
    payload = (
        b"==============================================================\n"
        b"jobnumber 7478863\n"
        b"jobnumber 7478863\n"
    )
    _assert_code(
        "R8U_R7C_QACCT_OUTPUT_INVALID",
        lambda: accounting.parse_qacct_records(payload),
    )


def test_malformed_qacct_line_is_rejected() -> None:
    _assert_code(
        "R8U_R7C_QACCT_OUTPUT_INVALID",
        lambda: accounting.parse_qacct_records(b"not-a-key-value-line\n"),
    )


def test_receipt_schema_rejects_extra_field() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec)
    receipt["unexpected"] = True
    _assert_code(
        "R8U_R7C_ACCOUNTING_RECEIPT_SCHEMA_INVALID",
        lambda: accounting.validate_accounting_receipt(
            receipt,
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        ),
    )


def test_receipt_revalidates_normalized_record_digest() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    receipt = _receipt(spec)
    receipt["normalized_qacct_record"]["ru_utime"] = "11.000"
    _assert_code(
        "R8U_R7C_ACCOUNTING_RECEIPT_BINDING_INVALID",
        lambda: accounting.validate_accounting_receipt(
            receipt,
            spec,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
        ),
    )


def test_optional_queue_and_host_may_be_absent() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    record = _record(spec)
    del record["qname"]
    del record["hostname"]
    projected = accounting.project_fixed_qacct_record(spec, record)
    assert projected["queue_name"] is None
    assert projected["execution_host"] is None


def test_private_directory_validation_accepts_owner_only_setgid_mode() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "setgid-private"
        path.mkdir(mode=0o700)
        path.chmod(0o2700)
        accounting._validate_private_directory(path)


def test_one_missing_receipt_uses_one_query_and_publishes_once() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[19]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        runner = mock.Mock(return_value=_completed(_payload(_record(spec))))
        result = accounting._reuse_or_query_accounting_at_path(
            spec,
            path,
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
            environment={"USER": "pkarim"},
            runner=runner,
            tool_validator=lambda: None,
            clock=_clock(),
        )
        assert result.created is True
        assert result.qacct_query_count == 1
        assert result.receipt["task_id"] == 19
        assert path.is_file()
        assert runner.call_count == 1


def test_qacct_nonzero_process_result_is_not_available_without_retry() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    runner = mock.Mock(
        return_value=subprocess.CompletedProcess([], 1, b"", b"not found")
    )
    _assert_code(
        "R8U_R7C_ACCOUNTING_NOT_AVAILABLE",
        lambda: accounting.query_fixed_qacct_record(
            spec,
            environment={"USER": "pkarim"},
            runner=runner,
            tool_validator=lambda: None,
        ),
    )
    assert runner.call_count == 1


def test_creation_timestamp_cannot_precede_query_timestamp() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    _assert_code(
        "R8U_R7C_RECEIPT_TIMESTAMP_INVALID",
        lambda: accounting.build_accounting_receipt(
            spec,
            _record(spec),
            adjudication_implementation_commit=ADJUDICATION_COMMIT,
            accounting_query_timestamp=CREATION_TIME,
            creation_timestamp=QUERY_TIME,
        ),
    )


def test_strict_receipt_loader_rejects_duplicate_json_keys() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "receipt.json"
        path.write_text('{"schema_name":"one","schema_name":"two"}', encoding="utf-8")
        path.chmod(0o600)
        _assert_code(
            "R8U_R7C_ACCOUNTING_RECEIPT_JSON_INVALID",
            lambda: accounting.load_accounting_receipt(
                path,
                accounting.TASK_ACCOUNTING_SPECS[17],
                adjudication_implementation_commit=ADJUDICATION_COMMIT,
            ),
        )


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
