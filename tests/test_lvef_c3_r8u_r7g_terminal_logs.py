#!/usr/bin/env python3
"""Focused dependency-light tests for R7G fixed R7F terminal logs."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import tempfile
import traceback
from typing import Any, Callable, Iterator, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7g_accounting as accounting
import lvef_c3_r8u_r7g_terminal_logs as terminal_logs


def _specs(root: Path) -> tuple[accounting.FixedAccountingSpec, ...]:
    log_root = root
    array = tuple(
        accounting.FixedAccountingSpec(
            job_kind="ARRAY_TASK",
            job_id="7480830",
            task_id=task_id,
            expected_job_role="R8U_R7D_CONTINUATION_ARRAY",
            expected_job_name="lvef_c3_r8u_r7d_seq_2223d976",
            expected_owner="pkarim",
            receipt_path=root / f"task_{task_id}.restricted.json",
            scheduler_log_path=(
                log_root
                / f"lvef_c3_r8u_r7d_seq_2223d976.o7480830.{task_id}"
            ),
            scheduler_log_basename=(
                f"lvef_c3_r8u_r7d_seq_2223d976.o7480830.{task_id}"
            ),
        )
        for task_id in (17, 18, 19)
    )
    finalizer = accounting.FixedAccountingSpec(
        job_kind="NON_ARRAY_FINALIZER",
        job_id="7480831",
        task_id=None,
        expected_job_role="R8U_R7D_COHORT_FINALIZER",
        expected_job_name="lvef_c3_r8u_r7d_fin_2223d976",
        expected_owner="pkarim",
        receipt_path=root / "finalizer.restricted.json",
        scheduler_log_path=(
            log_root / "lvef_c3_r8u_r7d_fin_2223d976.o7480831"
        ),
        scheduler_log_basename="lvef_c3_r8u_r7d_fin_2223d976.o7480831",
    )
    return (*array, finalizer)


def _authority(root: Path) -> dict[str, Any]:
    return {
        "expected_owner": "pkarim",
        "array_job_id": "7480830",
        "array_job_name": "lvef_c3_r8u_r7d_seq_2223d976",
        "array_role": "R8U_R7D_CONTINUATION_ARRAY",
        "finalizer_job_id": "7480831",
        "finalizer_job_name": "lvef_c3_r8u_r7d_fin_2223d976",
        "finalizer_role": "R8U_R7D_COHORT_FINALIZER",
        "scheduler_log_root": str(root),
        "scheduler_log_basenames": {
            "array_task_17": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.17",
            "array_task_18": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.18",
            "array_task_19": "lvef_c3_r8u_r7d_seq_2223d976.o7480830.19",
            "finalizer": "lvef_c3_r8u_r7d_fin_2223d976.o7480831",
        },
    }


def _receipt(*, failed: int = 0, exit_status: int = 78) -> dict[str, Any]:
    return {
        "failed": failed,
        "exit_status": exit_status,
        "terminal_classification": (
            "PASS" if failed == 0 and exit_status == 0 else "FAIL"
        ),
    }


@contextmanager
def _bound(
    specs: tuple[accounting.FixedAccountingSpec, ...],
) -> Iterator[None]:
    with (
        mock.patch.object(
            terminal_logs.accounting,
            "fixed_accounting_specs",
            return_value=specs,
        ),
        mock.patch.object(
            terminal_logs.accounting,
            "validate_accounting_receipt",
            side_effect=lambda value, _spec, **_kwargs: dict(value),
        ),
        mock.patch.object(
            terminal_logs,
            "_expected_owner_uid",
            return_value=os.geteuid(),
        ),
    ):
        yield


def _write_log(
    authority: Mapping[str, Any],
    spec: accounting.FixedAccountingSpec,
    payload: bytes,
    *,
    mode: int = 0o644,
) -> Path:
    root = Path(str(authority["scheduler_log_root"]))
    root.mkdir(mode=0o700, exist_ok=True)
    path = terminal_logs.fixed_scheduler_log_path(
        spec, terminal_authority=authority
    )
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _expect_code(code: str, callback: Callable[[], object]) -> None:
    try:
        callback()
    except terminal_logs.R7GTerminalLogError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def test_paths_are_exactly_bound_by_terminal_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = (Path(temporary).resolve() / "scheduler")
        authority = _authority(root)
        specs = _specs(root)
        with _bound(specs):
            assert terminal_logs.fixed_scheduler_log_path(
                specs[0], terminal_authority=authority
            ) == root / "lvef_c3_r8u_r7d_seq_2223d976.o7480830.17"
            assert terminal_logs.fixed_scheduler_log_path(
                specs[-1], terminal_authority=authority
            ) == root / "lvef_c3_r8u_r7d_fin_2223d976.o7480831"

            substituted = _authority(root)
            substituted["scheduler_log_basenames"]["array_task_17"] = (
                "lvef_c3_r8u_r7d_seq_2223d976.o9999999.17"
            )
            _expect_code(
                "R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID",
                lambda: terminal_logs.fixed_scheduler_log_path(
                    specs[0], terminal_authority=substituted
                ),
            )


def test_caller_cannot_select_old_or_invented_accounting_spec() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        authority = _authority(root)
        specs = _specs(root)
        invented = accounting.FixedAccountingSpec(
            job_kind="ARRAY_TASK",
            job_id="7478863",
            task_id=17,
            expected_job_role="R8U_R7_CONTINUATION_ARRAY",
            expected_job_name="lvef_c3_r8u_r7_seq_1be99c64",
            expected_owner="pkarim",
            receipt_path=root / "old.restricted.json",
            scheduler_log_path=(
                root / "lvef_c3_r8u_r7_seq_1be99c64.o7478863.17"
            ),
            scheduler_log_basename=(
                "lvef_c3_r8u_r7_seq_1be99c64.o7478863.17"
            ),
        )
        with _bound(specs):
            _expect_code(
                "R8U_R7G_TERMINAL_LOG_SCOPE_INVALID",
                lambda: terminal_logs.fixed_scheduler_log_path(
                    invented, terminal_authority=authority
                ),
            )


def test_mode_0644_task_and_finalizer_pass_markers_are_accepted() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        authority = _authority(root)
        specs = _specs(root)
        cases = (
            (specs[2], terminal_logs.ARRAY_PASS_MARKER + b"\n"),
            (specs[-1], terminal_logs.FINALIZER_PASS_MARKER + b"\n"),
        )
        with _bound(specs):
            for spec, payload in cases:
                _write_log(authority, spec, payload, mode=0o644)
                result = terminal_logs.inspect_fixed_terminal_logs(
                    spec,
                    _receipt(exit_status=0),
                    terminal_authority=authority,
                )
                assert result["status"] == "FIXED_PASS_TERMINAL_LOG_VALIDATED"
                assert result["scheduler_log_mode"] == "0644"
                assert result["scheduler_log_bytes"] == len(payload)
                assert result["scheduler_log_sha256"]
                assert result["expected_job_role"] == spec.expected_job_role


def test_only_r7d_job_kind_pass_marker_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        authority = _authority(root)
        specs = _specs(root)
        with _bound(specs):
            for payload, code in (
                (
                    b"R8U_R7_CONTINUATION_BATCH_STATUS=PASS_BATCH_FINALIZED\n",
                    "R8U_R7G_TERMINAL_LOG_PASS_MARKER_MISSING",
                ),
                (
                    terminal_logs.FINALIZER_PASS_MARKER + b"\n",
                    "R8U_R7G_TERMINAL_LOG_PASS_MARKER_WRONG_JOB_KIND",
                ),
                (
                    b"R8U_R7D_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n",
                    "R8U_R7G_TERMINAL_LOG_PASS_CONTAINS_BLOCKED_MARKER",
                ),
            ):
                path = _write_log(authority, specs[0], payload)
                _expect_code(
                    code,
                    lambda: terminal_logs.inspect_fixed_terminal_logs(
                        specs[0],
                        _receipt(exit_status=0),
                        terminal_authority=authority,
                    ),
                )
                path.unlink()


def test_control_application_and_scheduler_failures_remain_distinct() -> None:
    cases = (
        (
            b"R8U_R7D_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH\n",
            _receipt(),
            "CONTROL_PLANE_ONLY",
        ),
        (
            b"R8U_R7D_STATUS=BLOCKED_R8U_R7D_CONTINUATION_BATCH_NOT_FINALIZED\n"
            b"R8U_R7D_FAILED_STAGE=PRESERVATION\n",
            _receipt(),
            "SCIENTIFIC_OR_APPLICATION",
        ),
        (b"", _receipt(failed=100, exit_status=0), "SCHEDULER"),
        (b"", _receipt(failed=0, exit_status=72), "SCIENTIFIC_OR_APPLICATION"),
    )
    for payload, receipt, expected_scope in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "scheduler"
            authority = _authority(root)
            specs = _specs(root)
            with _bound(specs):
                _write_log(authority, specs[1], payload)
                result = terminal_logs.inspect_fixed_terminal_logs(
                    specs[1], receipt, terminal_authority=authority
                )
            assert result["failure_scope"] == expected_scope
            assert result["qacct_failed"] == receipt["failed"]
            assert result["qacct_exit_status"] == receipt["exit_status"]


def test_unknown_failure_marker_is_not_downgraded_to_control_plane() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "scheduler"
        authority = _authority(root)
        specs = _specs(root)
        with _bound(specs):
            _write_log(
                authority,
                specs[-1],
                b"R8U_R7D_STATUS=BLOCKED_UNKNOWN_FINALIZER_PROBLEM\n",
            )
            _expect_code(
                "R8U_R7G_TERMINAL_LOG_FAILURE_MARKER_UNCLASSIFIED",
                lambda: terminal_logs.inspect_fixed_terminal_logs(
                    specs[-1], _receipt(), terminal_authority=authority
                ),
            )


def test_symlink_hardlink_and_unsafe_modes_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        root = base / "scheduler"
        authority = _authority(root)
        specs = _specs(root)
        with _bound(specs):
            for mode in (0o664, 0o755):
                path = _write_log(authority, specs[0], b"x\n", mode=mode)
                _expect_code(
                    "R8U_R7G_TERMINAL_LOG_FILE_INVALID",
                    lambda: terminal_logs.inspect_fixed_terminal_logs(
                        specs[0], _receipt(), terminal_authority=authority
                    ),
                )
                path.unlink()

            path = _write_log(authority, specs[0], b"x\n")
            outside = base / "outside"
            outside.write_bytes(b"x\n")
            outside.chmod(0o644)
            path.unlink()
            path.symlink_to(outside)
            _expect_code(
                "R8U_R7G_TERMINAL_LOG_PATH_INVALID",
                lambda: terminal_logs.inspect_fixed_terminal_logs(
                    specs[0], _receipt(), terminal_authority=authority
                ),
            )
            path.unlink()

            _write_log(authority, specs[0], b"x\n")
            hardlink = base / "second-link"
            os.link(
                terminal_logs.fixed_scheduler_log_path(
                    specs[0], terminal_authority=authority
                ),
                hardlink,
            )
            _expect_code(
                "R8U_R7G_TERMINAL_LOG_FILE_INVALID",
                lambda: terminal_logs.inspect_fixed_terminal_logs(
                    specs[0], _receipt(), terminal_authority=authority
                ),
            )


def test_public_api_has_no_caller_log_path_parameter() -> None:
    for function in (
        terminal_logs.fixed_scheduler_log_path,
        terminal_logs.inspect_fixed_terminal_logs,
    ):
        parameters = inspect.signature(function).parameters
        assert "path" not in parameters
        assert "basename" not in parameters
        assert "job_id" not in parameters
        assert "owner" not in parameters


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
