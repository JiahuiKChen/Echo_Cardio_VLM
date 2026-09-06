from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7c_accounting as accounting
import lvef_c3_r8u_r7c_terminal_logs as terminal_logs


ADJUDICATION_COMMIT = "a" * 40
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _record(
    spec: accounting.FixedAccountingSpec,
    *,
    failed: str = "0",
    exit_status: str = "78",
) -> dict[str, str]:
    return {
        "jobnumber": spec.job_id,
        "taskid": "NONE" if spec.task_id is None else str(spec.task_id),
        "jobname": spec.expected_job_name,
        "owner": accounting.OWNER,
        "qsub_time": "Sat Sep  5 10:00:00 2026",
        "start_time": "Sat Sep  5 10:01:00 2026",
        "end_time": "Sat Sep  5 10:02:00 2026",
        "failed": failed,
        "exit_status": exit_status,
        "ru_wallclock": "60.0",
        "qname": "gpu.q",
        "hostname": "node.example",
    }


def _receipt(
    spec: accounting.FixedAccountingSpec,
    *,
    failed: str = "0",
    exit_status: str = "78",
) -> dict[str, object]:
    return accounting.build_accounting_receipt(
        spec,
        _record(spec, failed=failed, exit_status=exit_status),
        adjudication_implementation_commit=ADJUDICATION_COMMIT,
        accounting_query_timestamp=NOW,
        creation_timestamp=NOW,
    )


def _write_log(root: Path, spec: accounting.FixedAccountingSpec, data: bytes) -> Path:
    root.mkdir(mode=0o700)
    with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
        path = terminal_logs.fixed_scheduler_log_path(spec)
    path.write_bytes(data)
    path.chmod(0o644)
    return path


def _assert_code(code: str, callback: Callable[[], object]) -> None:
    try:
        callback()
    except terminal_logs.R7CTerminalLogError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def test_task17_role_mismatch_is_control_plane_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        spec = accounting.TASK_ACCOUNTING_SPECS[17]
        payload = (
            b"R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n"
            b"R8U_BATCH16_CLOUD_REQUESTS=0\n"
        )
        _write_log(root, spec, payload)
        with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
            result = terminal_logs.inspect_fixed_terminal_logs(
                spec, _receipt(spec)
            )

        assert result["failure_scope"] == "CONTROL_PLANE_ONLY"
        assert result["application_failure_supported"] is False
        assert result["first_failure_marker"] == (
            "R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH"
        )
        assert result["first_failed_stage"] == (
            "CONTINUATION_WORKER_SUBMISSION_VALIDATION"
        )
        assert result["scheduler_log_basename"] == (
            "lvef_c3_r8u_r7_seq_1be99c64.o7478863.17"
        )
        assert result["scheduler_log_bytes"] == len(payload)


def test_application_marker_and_reported_stage_are_projected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        spec = accounting.TASK_ACCOUNTING_SPECS[18]
        _write_log(
            root,
            spec,
            b"R8U_R7_STATUS=BLOCKED_R8U_R7_CONTINUATION_BATCH_NOT_FINALIZED\n"
            b"R8U_R7_FAILED_STAGE=PRESERVATION\n",
        )
        with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
            result = terminal_logs.inspect_fixed_terminal_logs(
                spec, _receipt(spec)
            )

        assert result["failure_scope"] == "SCIENTIFIC_OR_APPLICATION"
        assert result["application_failure_supported"] is True
        assert result["first_failed_stage"] == "PRESERVATION"


def test_nonzero_grid_engine_failed_field_is_scheduler_scope() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        spec = accounting.FINALIZER_ACCOUNTING_SPEC
        _write_log(root, spec, b"")
        with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
            result = terminal_logs.inspect_fixed_terminal_logs(
                spec, _receipt(spec, failed="100", exit_status="0")
            )

        assert result["failure_scope"] == "SCHEDULER"
        assert result["first_failure_marker"] == "QACCT_FAILED_NONZERO"
        assert result["scheduler_failure_supported"] is True


def test_passing_task_and_finalizer_require_their_exact_pass_markers() -> None:
    cases = (
        (
            accounting.TASK_ACCOUNTING_SPECS[19],
            terminal_logs.ARRAY_PASS_MARKER + b"\n",
        ),
        (
            accounting.FINALIZER_ACCOUNTING_SPEC,
            terminal_logs.FINALIZER_PASS_MARKER + b"\n",
        ),
    )
    for spec, payload in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "scheduler"
            _write_log(root, spec, payload)
            with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
                result = terminal_logs.inspect_fixed_terminal_logs(
                    spec, _receipt(spec, exit_status="0")
                )
        assert result["status"] == "FIXED_PASS_TERMINAL_LOG_VALIDATED"
        assert result["qacct_terminal_classification"] == "PASS"
        assert result["terminal_marker"] == payload.strip().decode("ascii")
        assert result["first_failure_marker"] == "NOT_AVAILABLE"
        assert result["failure_scope"] == "NOT_APPLICABLE"


def test_passing_log_missing_wrong_or_blocked_marker_is_rejected() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[19]
    cases = (
        (b"ordinary output\n", "R8U_R7C_TERMINAL_LOG_PASS_MARKER_MISSING"),
        (
            terminal_logs.FINALIZER_PASS_MARKER + b"\n",
            "R8U_R7C_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND",
        ),
        (
            b"R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n",
            "R8U_R7C_TERMINAL_LOG_PASS_CONTAINS_BLOCKED_MARKER",
        ),
    )
    for payload, expected_code in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "scheduler"
            _write_log(root, spec, payload)
            with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
                _assert_code(
                    expected_code,
                    lambda: terminal_logs.inspect_fixed_terminal_logs(
                        spec, _receipt(spec, exit_status="0")
                    ),
                )


def test_bare_or_unknown_application_exit_is_never_control_plane() -> None:
    spec = accounting.FINALIZER_ACCOUNTING_SPEC
    cases = (
        (b"", "R8U_R7C_TERMINAL_LOG_FAILURE_MARKER_MISSING"),
        (
            b"R8U_R7_STATUS=BLOCKED_FINAL_COHORT_ACCOUNTING_MISMATCH\n",
            "R8U_R7C_TERMINAL_LOG_FAILURE_MARKER_UNCLASSIFIED",
        ),
    )
    for payload, expected_code in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "scheduler"
            _write_log(root, spec, payload)
            with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
                _assert_code(
                    expected_code,
                    lambda: terminal_logs.inspect_fixed_terminal_logs(
                        spec, _receipt(spec)
                    ),
                )


def test_affirmative_control_marker_overrides_nonzero_failed_field() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        spec = accounting.FINALIZER_ACCOUNTING_SPEC
        _write_log(
            root,
            spec,
            b"R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n",
        )
        with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", root):
            result = terminal_logs.inspect_fixed_terminal_logs(
                spec, _receipt(spec, failed="100", exit_status="0")
            )
    assert result["failure_scope"] == "CONTROL_PLANE_ONLY"
    assert result["first_failed_stage"] == (
        "CONTINUATION_WORKER_SUBMISSION_VALIDATION"
    )


def test_symlinked_fixed_log_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary).resolve()
        spec = accounting.TASK_ACCOUNTING_SPECS[19]
        scheduler_root = temporary_root / "scheduler"
        path = _write_log(scheduler_root, spec, b"outside\n")

        with mock.patch.object(terminal_logs, "SCHEDULER_ROOT", scheduler_root):
            path.unlink()
            outside = temporary_root / "outside"
            outside.write_bytes(
                b"R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n"
            )
            path.symlink_to(outside)
            _assert_code(
                "R8U_R7C_TERMINAL_LOG_PATH_INVALID",
                lambda: terminal_logs.inspect_fixed_terminal_logs(
                    spec, _receipt(spec)
                ),
            )


def test_caller_cannot_select_an_arbitrary_log_identity() -> None:
    original = accounting.TASK_ACCOUNTING_SPECS[17]
    invented = accounting.FixedAccountingSpec(
        job_kind=original.job_kind,
        job_id="9999999",
        task_id=original.task_id,
        expected_job_role=original.expected_job_role,
        expected_job_name=original.expected_job_name,
        receipt_path=original.receipt_path,
    )
    _assert_code(
        "R8U_R7C_TERMINAL_LOG_SCOPE_INVALID",
        lambda: terminal_logs.fixed_scheduler_log_path(invented),
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
