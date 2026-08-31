#!/usr/bin/env python3
"""Focused dependency-light R8U-R5 worker-context controller proofs."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as controller


R4_COMMIT = "6eb5c9a4337ca4569ecd0d3157084fb4b76adfac"
R5_COMMIT = "c" * 40
SHA = "a" * 64


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def _expect_code(action: Callable[[], Any], expected: str) -> None:
    try:
        action()
    except Exception as exc:
        assert _code(exc) == expected, (_code(exc), expected)
    else:
        raise AssertionError(f"expected {expected}")


def _source(function: Callable[..., Any]) -> str:
    return inspect.getsource(function)


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_names(function: Callable[..., Any]) -> tuple[str, ...]:
    tree = ast.parse(_source(function))
    return tuple(
        _dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def _has_while(function: Callable[..., Any]) -> bool:
    tree = ast.parse(_source(function))
    return any(isinstance(node, ast.While) for node in ast.walk(tree))


def _only_position(source: str, names: Iterable[str]) -> int:
    positions = [source.find(name) for name in names if source.find(name) >= 0]
    assert positions, tuple(names)
    return min(positions)


def test_r5_controller_api_is_closed_and_fresh() -> None:
    expected_functions = (
        "_current_r8u_r5_implementation_commit",
        "_r8u_r5_build_worker_context",
        "_r8u_r5_probe_qsub_command",
        "_r8u_r5_resume_qsub_command",
        "submit_r8u_r5_worker_context_probe",
        "run_r8u_r5_worker_context_probe",
        "adjudicate_r8u_r5_worker_context_probe",
        "submit_r8u_r5_batch16_publication_resume",
        "run_r8u_r5_batch16_publication_resume",
        "submit_r8u_r5_continuation_17_19",
        "validate_r8u_r5_continuation_worker_submission",
        "run_r8u_r5_continuation_array_task",
        "run_r8u_r5_continuation_finalizer",
    )
    for name in expected_functions:
        assert callable(getattr(controller, name)), name

    assert controller.R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT == R4_COMMIT
    assert controller.R8U_R4_FAILED_SCHEDULER_IDENTITY_JOB_ID == "7375270"
    assert controller.R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_BYTES == 108
    assert controller.R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_MODE == 0o644
    assert controller.R8U_R4_FAILED_SCHEDULER_IDENTITY_LOG_SHA256 == (
        "c27f6cca757a3ffc26ca1f7211f44a691137fb736f62a5db13825e9b2ae632e8"
    )
    assert controller.R8U_R5_ROOT != controller.R8U_R4_ROOT
    assert controller.R8U_R5_SCHEDULER_ROOT != controller.R8U_R4_SCHEDULER_ROOT
    assert controller.R8U_R5_PUBLICATION_CLAIM_ROOT != (
        controller.R8U_R4_PUBLICATION_CLAIM_ROOT
    )
    assert controller.R8U_R5_ROOT.name == "r8u_r5_batch16_publication_resume"


def test_r5_implementation_is_one_direct_child_of_consumed_r4() -> None:
    def git(*arguments: str) -> str:
        if arguments[:3] == ("rev-list", "--parents", "-n"):
            assert arguments[4] == R5_COMMIT
            return f"{R5_COMMIT} {R4_COMMIT}"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            assert arguments[2:] == (R4_COMMIT, R5_COMMIT)
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            assert arguments[2] == f"{R4_COMMIT}..{R5_COMMIT}"
            return "1"
        raise AssertionError(arguments)

    with (
        mock.patch.object(
            controller.sequential, "_current_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r5_implementation_commit() == R5_COMMIT

    with (
        mock.patch.object(
            controller.sequential, "_current_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(
            controller.sequential,
            "_git",
            side_effect=lambda *args: "2"
            if args[:2] == ("rev-list", "--count")
            else git(*args),
        ),
    ):
        _expect_code(
            controller._current_r8u_r5_implementation_commit,
            "R8U_R5_IMPLEMENTATION_ANCESTRY_INVALID",
        )


def test_scheduler_account_authority_binds_all_worker_consumers() -> None:
    required = {
        "expected_effective_uid",
        "expected_scheduler_username",
        "canonical_home",
        "runner_sha256",
        "python_sha256",
        "qsub_environment_sha256",
        "sealed_qsub_environment",
        "authorized_worker_roles",
    }
    assert required <= controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS
    assert tuple(controller.R8U_R5_WORKER_ROLES) == (
        controller.R8U_R5_PROBE_ROLE,
        controller.R8U_R5_RESUME_ROLE,
        controller.R8U_R5_ARRAY_ROLE,
        controller.R8U_R5_FINALIZER_ROLE,
    )
    for keys in (
        controller.R8U_R5_PROBE_AUTHORITY_KEYS,
        controller.R8U_R5_PROBE_SUBMISSION_KEYS,
        controller.R8U_R5_RESUME_AUTHORITY_KEYS,
        controller.R8U_R5_RESUME_SUBMISSION_KEYS,
        controller.R8U_R5_LOCALITY_KEYS,
        controller.R8U_R5_PUBLICATION_CLAIM_KEYS,
        controller.R8U_R5_PRIMITIVE_PROBE_KEYS,
        controller.R8U_R5_PUBLICATION_KEYS,
        controller.R8U_R5_TERMINAL_KEYS,
        controller.R8U_R5_CONTINUATION_CLAIM_KEYS,
        controller.R8U_R5_CONTINUATION_SUBMISSION_KEYS,
        controller.R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS,
    ):
        assert "scheduler_account_authority_sha256" in keys
    forbidden = {
        "password",
        "groups",
        "credentials",
        "token",
        "private_key",
    }
    assert controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS.isdisjoint(forbidden)
    authority_source = _source(controller._r8u_r5_scheduler_account_authority)
    worker_source = _source(controller._r8u_r5_build_worker_context)
    assert "scheduler.ECHOPRIME_PYTHON" in authority_source
    assert "resolved_python_executable_sha256" in authority_source
    assert "core.sha256_file(Path(sys.executable))" not in authority_source
    assert "resolved_python_executable_sha256" in worker_source
    validator_source = _source(
        controller.validate_r8u_r5_scheduler_account_authority
    )
    assert "_current_r8u_r5_implementation_commit" not in validator_source
    assert "core.sha256_file(RUNNER_PATH)" not in validator_source
    assert "Path(sys.executable)" not in validator_source


def test_worker_live_authority_failures_remain_field_specific() -> None:
    account = {
        "expected_effective_uid": 8123,
        "expected_scheduler_username": "sealed-owner",
        "canonical_home": "/restricted/sealed/home",
        "qsub_environment_sha256": SHA,
        "sealed_qsub_environment": {"sealed": "environment"},
        "implementation_commit": R5_COMMIT,
        "runner_sha256": "b" * 64,
        "python_sha256": "d" * 64,
    }
    common = {
        "account_authority": account,
        "expected_job_id": "8123456",
        "expected_role": controller.R8U_R5_RESUME_ROLE,
    }
    with (
        mock.patch.object(
            controller,
            "validate_r8u_r5_scheduler_account_authority",
            return_value=account,
        ),
        mock.patch.object(
            controller,
            "_current_r8u_r5_implementation_commit",
            side_effect=controller.R8RControllerError("wrong commit"),
        ),
    ):
        _expect_code(
            lambda: controller._r8u_r5_build_worker_context(**common),
            "SCHEDULER_IMPLEMENTATION_COMMIT_MISMATCH",
        )
    with (
        mock.patch.object(
            controller,
            "validate_r8u_r5_scheduler_account_authority",
            return_value=account,
        ),
        mock.patch.object(
            controller, "_current_r8u_r5_implementation_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(
            controller.core, "sha256_file", side_effect=OSError("runner")
        ),
    ):
        _expect_code(
            lambda: controller._r8u_r5_build_worker_context(**common),
            "SCHEDULER_RUNNER_AUTHORITY_MISMATCH",
        )
    with (
        mock.patch.object(
            controller,
            "validate_r8u_r5_scheduler_account_authority",
            return_value=account,
        ),
        mock.patch.object(
            controller, "_current_r8u_r5_implementation_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(controller.core, "sha256_file", return_value="b" * 64),
        mock.patch.object(controller.sys, "executable", "/wrong/python"),
    ):
        _expect_code(
            lambda: controller._r8u_r5_build_worker_context(**common),
            "SCHEDULER_PYTHON_AUTHORITY_MISMATCH",
        )


def test_account_file_owner_gate_precedes_private_receipt_reads() -> None:
    info = SimpleNamespace(
        st_uid=8124,
        st_mode=controller.stat.S_IFREG | 0o600,
        st_nlink=1,
    )
    with (
        mock.patch.object(controller.os, "lstat", return_value=info),
        mock.patch.object(controller.os, "geteuid", return_value=8123),
        mock.patch.object(controller, "_load_private_json") as private_read,
    ):
        _expect_code(
            lambda: controller._read_r8u_r5_probe_submission_minimal(
                current_job_id="8123456", wait=False
            ),
            "SCHEDULER_EFFECTIVE_UID_MISMATCH",
        )
    private_read.assert_not_called()


def test_aggregate_diagnostics_distinguish_absent_from_mismatch() -> None:
    diagnostic = {
        "user_present": False,
        "observed_user_match": False,
        "logname_present": True,
        "observed_logname_match": False,
        "home_present": True,
        "observed_home_match": True,
        "shell_present": False,
        "observed_shell_match": False,
    }
    assert controller._r8u_r5_aggregate_match_label(
        diagnostic, field="observed_user_match"
    ) == "NOT_AVAILABLE"
    assert controller._r8u_r5_aggregate_match_label(
        diagnostic, field="observed_logname_match"
    ) == "NO"
    assert controller._r8u_r5_aggregate_match_label(
        diagnostic, field="observed_home_match"
    ) == "YES"
    assert controller._r8u_r5_aggregate_match_label(
        diagnostic, field="observed_shell_match"
    ) == "NOT_AVAILABLE"
    assert controller._r8u_r5_aggregate_passwd_status("PASS") == "PASS"
    assert (
        controller._r8u_r5_aggregate_passwd_status("LOOKUP_UNAVAILABLE")
        == "UNAVAILABLE"
    )
    for value in ("NAME_MISMATCH", "HOME_MISMATCH", "SHELL_MISMATCH"):
        assert controller._r8u_r5_aggregate_passwd_status(value) == "MISMATCH"


def test_worker_diagnostic_is_equality_only_and_task_aware() -> None:
    required = {
        "user_present",
        "logname_present",
        "home_present",
        "shell_present",
        "observed_user_match",
        "observed_logname_match",
        "observed_home_match",
        "observed_shell_match",
        "passwd_lookup_available",
        "passwd_lookup_status",
        "effective_uid_match",
        "job_id_match",
        "task_context_match",
        "job_role_match",
        "runner_sha256_match",
        "python_sha256_match",
        "implementation_commit_match",
        "qsub_environment_sha256_match",
        "classifications",
    }
    assert required <= controller.R8U_R5_WORKER_DIAGNOSTIC_KEYS
    forbidden = {
        "observed_user",
        "observed_logname",
        "observed_home",
        "observed_shell",
        "effective_uid",
        "job_id",
        "runner_sha256",
        "python_sha256",
        "implementation_commit",
        "qsub_environment_sha256",
    }
    assert controller.R8U_R5_WORKER_DIAGNOSTIC_KEYS.isdisjoint(forbidden)


def test_controller_worker_context_forwards_exact_identity_and_task_bindings() -> None:
    signature = inspect.signature(controller._r8u_r5_build_worker_context)
    assert "expected_task_id" in signature.parameters
    account = {
        "expected_effective_uid": 8123,
        "expected_scheduler_username": "sealed-owner",
        "canonical_home": "/restricted/sealed/home",
        "qsub_environment_sha256": SHA,
        "sealed_qsub_environment": {"sealed": "environment"},
        "implementation_commit": R5_COMMIT,
        "runner_sha256": "b" * 64,
        "python_sha256": "d" * 64,
    }
    observed: dict[str, Any] = {}

    def build(**arguments: Any) -> str:
        observed.update(arguments)
        return "WORKER_CONTEXT"

    with (
        mock.patch.object(
            controller,
            "validate_r8u_r5_scheduler_account_authority",
            return_value=account,
        ),
        mock.patch.object(
            controller, "_current_r8u_r5_implementation_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(
            controller.sys, "executable", str(controller.scheduler.ECHOPRIME_PYTHON)
        ),
        mock.patch.object(controller.core, "sha256_file", return_value="b" * 64),
        mock.patch.object(
            controller.stages,
            "resolved_python_executable_sha256",
            return_value="d" * 64,
        ),
        mock.patch.object(
            controller.scheduler, "build_worker_scheduler_context", side_effect=build
        ),
        mock.patch.object(
            controller.scheduler,
            "build_qsub_environment",
            side_effect=AssertionError("worker reconstructed submitter environment"),
        ) as submitter_builder,
    ):
        value = controller._r8u_r5_build_worker_context(
            account_authority=account,
            expected_job_id="8123456",
            expected_role=controller.R8U_R5_ARRAY_ROLE,
            expected_task_id="17",
        )
    assert value == "WORKER_CONTEXT"
    submitter_builder.assert_not_called()
    assert observed["expected_effective_uid"] == 8123
    assert observed["expected_job_id"] == "8123456"
    assert observed["expected_job_role"] == controller.R8U_R5_ARRAY_ROLE
    assert observed["observed_job_role"] == controller.R8U_R5_ARRAY_ROLE
    assert observed["expected_task_id"] == "17"
    assert observed["expected_qsub_environment_sha256"] == SHA
    assert observed["sealed_qsub_environment"] == {"sealed": "environment"}


def test_worker_qstat_and_process_projections_bind_role_and_numeric_uid() -> None:
    job_id = "8123456"
    job_name = controller._r8u_r5_resume_job_name(R5_COMMIT)

    def qstat(name: str = job_name) -> SimpleNamespace:
        payload = (
            '<job_info><queue_info><job_list state="running">'
            f"<JB_job_number>{job_id}</JB_job_number>"
            f"<JB_name>{name}</JB_name><state>r</state>"
            "</job_list></queue_info></job_info>"
        ).encode("ascii")
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    environment = {"USER": "sealed-owner"}
    projected = controller._r8u_r5_qstat_projection(
        environment=environment,
        expected_job_id=job_id,
        expected_job_name=job_name,
        runner=lambda *_args, **_kwargs: qstat(),
        worker_local=True,
    )
    assert projected["target_matches"] == 1
    assert projected["competing_matching_jobs"] == 0
    assert projected["state"] == "r"
    _expect_code(
        lambda: controller._r8u_r5_qstat_projection(
            environment=environment,
            expected_job_id=job_id,
            expected_job_name=job_name,
            runner=lambda *_args, **_kwargs: qstat("wrong_role"),
            worker_local=True,
        ),
        "SCHEDULER_JOB_ROLE_MISMATCH",
    )

    marker = "--run-r8u-r5-batch16-publication-resume"
    ps = SimpleNamespace(
        returncode=0,
        stdout=f"123 8123 python {marker}\n124 9000 {marker}\n".encode("ascii"),
        stderr=b"",
    )
    with (
        mock.patch.object(controller.os, "geteuid", return_value=8123),
        mock.patch.object(controller.os, "getpid", return_value=123),
    ):
        projection = controller._r8u_r5_process_projection(
            environment=environment,
            runner=lambda *_args, **_kwargs: ps,
            worker_self_marker=marker,
        )
    assert projection["matching_processes"] == 0
    assert projection["process_snapshot_count"] == 1
    prior_epoch = SimpleNamespace(
        returncode=0,
        stdout=(
            f"123 8123 python {marker}\n"
            "124 8123 python --run-r8u-r3-batch16-publication-resume\n"
        ).encode("ascii"),
        stderr=b"",
    )
    with (
        mock.patch.object(controller.os, "geteuid", return_value=8123),
        mock.patch.object(controller.os, "getpid", return_value=123),
    ):
        _expect_code(
            lambda: controller._r8u_r5_process_projection(
                environment=environment,
                runner=lambda *_args, **_kwargs: prior_epoch,
                worker_self_marker=marker,
            ),
            "R8U_R5_WORKER_PROCESS_PROJECTION_INVALID",
        )


def test_array_qstat_accepts_task_records_and_exact_held_finalizer_only() -> None:
    array_job_id = "8123456"
    finalizer_job_id = "8123457"
    array_name = controller._r8u_r5_continuation_array_job_name(R5_COMMIT)
    finalizer_name = controller._r8u_r5_continuation_finalizer_job_name(R5_COMMIT)

    def qstat(*, include_finalizer: bool = True, competitor: bool = False) -> Any:
        jobs = (
            '<job_list state="running">'
            f"<JB_job_number>{array_job_id}</JB_job_number>"
            f"<JB_name>{array_name}</JB_name><state>r</state><tasks>17</tasks>"
            "</job_list>"
            '<job_list state="pending">'
            f"<JB_job_number>{array_job_id}</JB_job_number>"
            f"<JB_name>{array_name}</JB_name><state>qw</state>"
            "<tasks>18-19:1</tasks></job_list>"
        )
        if include_finalizer:
            jobs += (
                '<job_list state="pending">'
                f"<JB_job_number>{finalizer_job_id}</JB_job_number>"
                f"<JB_name>{finalizer_name}</JB_name><state>hqw</state>"
                "</job_list>"
            )
        if competitor:
            jobs += (
                '<job_list state="pending"><JB_job_number>8123458</JB_job_number>'
                "<JB_name>lvef_c3_r8u_r4_res_6eb5c9a4</JB_name>"
                "<state>qw</state></job_list>"
            )
        return SimpleNamespace(
            returncode=0,
            stdout=f"<job_info><queue_info>{jobs}</queue_info></job_info>".encode(),
            stderr=b"",
        )

    arguments = {
        "environment": {"USER": "sealed-owner"},
        "expected_job_id": array_job_id,
        "expected_job_name": array_name,
        "worker_local": True,
        "expected_task_id": "17",
        "allowed_companion_job_id": finalizer_job_id,
        "allowed_companion_job_name": finalizer_name,
    }
    projected = controller._r8u_r5_qstat_projection(
        **arguments,
        runner=lambda *_args, **_kwargs: qstat(),
    )
    assert projected["target_matches"] == 1
    assert projected["state"] == "r"
    assert projected["competing_matching_jobs"] == 0
    _expect_code(
        lambda: controller._r8u_r5_qstat_projection(
            **arguments,
            runner=lambda *_args, **_kwargs: qstat(include_finalizer=False),
        ),
        "SCHEDULER_JOB_ROLE_MISMATCH",
    )
    _expect_code(
        lambda: controller._r8u_r5_qstat_projection(
            **arguments,
            runner=lambda *_args, **_kwargs: qstat(competitor=True),
        ),
        "SCHEDULER_JOB_ROLE_MISMATCH",
    )


def test_continuation_worker_second_validation_is_readback_safe() -> None:
    array_job_id = "8123456"
    finalizer_job_id = "8123457"
    account_sha = "a" * 64
    claim_sha = "b" * 64
    submission_sha = "c" * 64
    diagnostic_sha = "d" * 64
    qstat_sha = "e" * 64
    process_sha = "f" * 64
    qsub_environment_sha = "9" * 64
    account = {
        "qsub_environment_sha256": qsub_environment_sha,
        "implementation_commit": R5_COMMIT,
    }
    claim = {
        "scheduler_account_authority_sha256": account_sha,
        "qsub_environment_sha256": qsub_environment_sha,
    }
    submission = {
        "scheduler_account_authority_sha256": account_sha,
        "qsub_environment_sha256": qsub_environment_sha,
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "resume_job_id": "8123000",
        "array_job_name": controller._r8u_r5_continuation_array_job_name(R5_COMMIT),
        "finalizer_job_name": controller._r8u_r5_continuation_finalizer_job_name(
            R5_COMMIT
        ),
        "array_task_range": controller.R8U_FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": 3,
        "array_max_concurrency": 1,
        "scheduler_submission_count": 2,
        "total_new_qsub_submissions": 4,
        "scheduler_submission_maximum": 4,
        "fifth_submission_reachable": False,
    }
    diagnostic = {"readback": True}
    worker_receipt = {
        "scheduler_account_authority_sha256": account_sha,
        "continuation_claim_sha256": claim_sha,
        "continuation_submission_sha256": submission_sha,
        "worker_diagnostic_sha256": diagnostic_sha,
        "worker_qstat_projection_sha256": qstat_sha,
        "worker_process_projection_sha256": process_sha,
        "job_id": array_job_id,
        "worker_role": controller.R8U_R5_ARRAY_ROLE,
        "task_id": "17",
        "effective_uid_match": True,
        "job_id_match": True,
        "task_context_match": True,
        "job_role_match": True,
        "canonical_worker_environment_pass": True,
    }
    diagnostic_path, receipt_path = controller._r8u_r5_continuation_worker_paths(
        expected_role=controller.R8U_R5_ARRAY_ROLE,
        expected_task_id="17",
    )

    def load(path: Path) -> tuple[Mapping[str, Any], bytes]:
        values = {
            controller.R8U_R5_ACCOUNT_AUTHORITY_PATH: account,
            controller.R8U_R5_CONTINUATION_CLAIM_PATH: claim,
            controller.R8U_R5_CONTINUATION_SUBMISSION_PATH: submission,
            diagnostic_path: diagnostic,
            receipt_path: worker_receipt,
        }
        return values[path], b"{}\n"

    def digest(path: Path) -> str:
        return {
            controller.R8U_R5_ACCOUNT_AUTHORITY_PATH: account_sha,
            controller.R8U_R5_CONTINUATION_CLAIM_PATH: claim_sha,
            controller.R8U_R5_CONTINUATION_SUBMISSION_PATH: submission_sha,
            diagnostic_path: diagnostic_sha,
        }[path]

    with (
        mock.patch.dict(
            controller.os.environ,
            {"JOB_ID": array_job_id, "SGE_TASK_ID": "17"},
            clear=True,
        ),
        mock.patch.multiple(
            controller,
            _r8u_r5_require_account_file_owner_matches_effective_uid=mock.DEFAULT,
            _wait_for_r8u_r5_control=mock.DEFAULT,
            validate_r8u_r5_scheduler_account_authority=mock.Mock(
                return_value=account
            ),
        ),
        mock.patch.object(
            controller, "_current_r8u_r5_implementation_commit", return_value=R5_COMMIT
        ),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller, "_r8u_r5_common", return_value={}),
        mock.patch.object(
            controller, "R8U_R5_CONTINUATION_CLAIM_KEYS", frozenset(claim)
        ),
        mock.patch.object(
            controller,
            "R8U_R5_CONTINUATION_SUBMISSION_KEYS",
            frozenset(submission),
        ),
        mock.patch.object(
            controller,
            "R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS",
            frozenset(worker_receipt),
        ),
        mock.patch.object(controller.core, "sha256_file", side_effect=digest),
        mock.patch.object(controller.os.path, "lexists", return_value=True),
        mock.patch.object(controller, "_validate_r8u_r5_worker_diagnostic"),
        mock.patch.object(controller, "validate_r8u_r5_resume_terminal"),
        mock.patch.object(controller, "_validate_r8u_r5_resume_accounting"),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=object()),
        mock.patch.object(controller, "_r8u_validate_frozen_prefix", return_value=[]),
        mock.patch.object(controller, "_r8u_r5_continuation_claim", return_value=claim),
        mock.patch.object(
            controller,
            "_r8u_r5_continuation_submission_receipt",
            return_value=submission,
        ),
        mock.patch.object(
            controller,
            "_r8u_r5_build_worker_context",
            side_effect=AssertionError("readback rebuilt worker context"),
        ) as context_builder,
        mock.patch.object(
            controller,
            "_r8u_r5_qstat_projection",
            side_effect=AssertionError("readback repeated qstat"),
        ) as qstat,
        mock.patch.object(
            controller,
            "_r8u_r5_process_projection",
            side_effect=AssertionError("readback repeated ps"),
        ) as process,
    ):
        assert (
            controller.validate_r8u_r5_continuation_worker_submission(
                current_job_id=array_job_id
            )
            == submission
        )
    context_builder.assert_not_called()
    qstat.assert_not_called()
    process.assert_not_called()


def test_r5_shell_dispatch_uses_numeric_uid_before_python() -> None:
    runner = (ROOT / "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh").read_text(
        encoding="utf-8"
    )
    numeric = runner.index("r8u_r5_require_private_projectnb_directory()")
    dispatcher = runner.index("if [[ \"$JOB_FAMILY\" == r8u_r5 ]]", numeric)
    assert "stat -c '%u'" in runner[numeric:dispatcher]
    assert '"$EUID"' in runner[numeric:dispatcher]
    assert "stat -c '%U'" not in runner[numeric:dispatcher]
    assert "id -un" not in runner[numeric:dispatcher]
    assert "r8u_r5_require_private_projectnb_directory \"$1\"" in runner[
        dispatcher:
    ]


def test_cpu_probe_qsub_is_single_slot_nonarray_nongpu_and_ten_minutes() -> None:
    command = controller._r8u_r5_probe_qsub_command(R5_COMMIT)
    assert command[0] == str(controller.scheduler.QSUB_PATH)
    assert command.count("-N") == 1
    assert command.count("-pe") == 1
    pe = command.index("-pe")
    assert command[pe + 1 : pe + 3] == ["omp", "1"]
    assert "h_rt=00:10:00" in command
    assert "-t" not in command
    assert "-hold_jid" not in command
    assert "-sync" not in command
    assert not any("gpu" in argument.casefold() for argument in command)
    assert command[-1] == str(controller.RUNNER_PATH)
    assert controller._r8u_r5_probe_job_name(R5_COMMIT) == (
        f"lvef_c3_r8u_r5_ctx_{R5_COMMIT[:8]}"
    )

    authority_flags = {
        "wall_seconds_maximum",
        "cpu_slots",
        "gpu_requested",
        "array_requested",
        "candidate_scan_authorized",
        "cloud_requests_authorized",
        "dicom_body_reads_authorized",
        "npz_body_reads_authorized",
        "publication_authorized",
        "extraction_authorized",
        "embedding_generation_authorized",
        "preservation_authorized",
        "scientific_attempt_mutation_authorized",
    }
    assert authority_flags <= controller.R8U_R5_PROBE_AUTHORITY_KEYS


def test_cpu_probe_worker_has_no_science_body_cloud_or_gpu_call() -> None:
    function = controller.run_r8u_r5_worker_context_probe
    calls = _call_names(function)
    joined = "\n".join(calls).casefold()
    required = (
        "_r8u_r5_build_worker_context",
        "_r8u_r5_qstat_projection",
        "_r8u_r5_process_projection",
    )
    for name in required:
        assert any(call.endswith(name) for call in calls), (name, calls)
    forbidden = (
        "echoprime",
        "download",
        "dicom",
        "npz",
        "extract",
        "publish",
        "preserv",
        "retire",
        "pool",
        "predict",
        "fit",
        "performance",
        "cuda",
        "gpu",
        "cloud",
    )
    assert not any(token in joined for token in forbidden), calls
    source = _source(function)
    assert "build_qsub_environment(" not in source
    assert "_r8u_r5_probe_receipt(" in source
    assert "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT" in _source(
        controller._r8u_r5_probe_receipt
    )


def test_all_compute_consumers_use_worker_context_not_submitter_builder() -> None:
    workers = (
        controller.run_r8u_r5_worker_context_probe,
        controller.run_r8u_r5_batch16_publication_resume,
        controller.run_r8u_r5_continuation_array_task,
        controller.run_r8u_r5_continuation_finalizer,
        controller.validate_r8u_r5_continuation_worker_submission,
    )
    combined = "\n".join(_source(function) for function in workers)
    assert "scheduler.build_qsub_environment(" not in combined
    assert "build_qsub_environment(" not in combined
    assert "_r8u_r5_build_worker_context(" in combined
    assert _source(controller.run_r8u_r5_batch16_publication_resume).count(
        "_r8u_r5_build_worker_context("
    ) == 1
    continuation = _source(controller.validate_r8u_r5_continuation_worker_submission)
    assert "_r8u_r5_build_worker_context(" in continuation
    assert "scheduler_account_authority_sha256" in continuation
    assert "expected_task_id" in continuation


def test_gpu_worker_gate_order_precedes_single_candidate_scan_and_science() -> None:
    source = _source(controller.run_r8u_r5_batch16_publication_resume)
    positions = (
        _only_position(source, ("JOB_ID",)),
        _only_position(
            source,
            (
                "validate_r8u_r5_resume_submission",
                "_r8u_r5_wait_resume_submission",
                "_read_r8u_r5_resume_submission_minimal",
                "R8U_R5_SUBMISSION_PATH",
            ),
        ),
        _only_position(source, ("_r8u_r5_build_worker_context(",)),
        _only_position(source, ("_r8u_r5_qstat_projection(",)),
        _only_position(source, ("_r8u_r5_process_projection(",)),
        _only_position(
            source,
            (
                "_validate_environment_receipt(",
                "validate_environment_receipt(",
                "_load_fixed_original_run(",
            ),
        ),
        _only_position(source, ("validate_r8u_r4_portable_candidate_authority(",)),
        _only_position(source, ("_r8u_r4_portable_candidate_projection(",)),
        _only_position(source, ("_r8u_r5_live_publication_locality(",)),
        _only_position(source, ("_r8u_r5_publication_claim(",)),
        _only_position(source, ("_r8u_r5_primitive_probe(",)),
        _only_position(source, ("_r8u_r5_publish_candidate(",)),
        _only_position(source, ("_r8u_r5_complete_batch16_after_publication(",)),
    )
    assert positions == tuple(sorted(positions)), positions
    assert source.count("_r8u_r4_portable_candidate_projection(") == 1
    completion = _source(controller._r8u_r5_complete_batch16_after_publication)
    assert "dependency.echoprime(" in completion
    combined = source + completion
    assert "dependency.dicom(" not in combined
    assert "dependency.download(" not in combined
    assert "run_production_dicom_extraction" not in combined
    assert "download_mimic_echo_subset" not in combined
    assert "predict(" not in combined.casefold()
    assert "fit(" not in combined.casefold()


def test_worker_context_failure_makes_candidate_scan_unreachable() -> None:
    submission = {
        "resume_job_name": controller._r8u_r5_resume_job_name(R5_COMMIT)
    }
    with (
        mock.patch.dict(
            controller.os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "undefined"},
            clear=True,
        ),
        mock.patch.object(
            controller,
            "_read_r8u_r5_resume_submission_minimal",
            return_value=({}, submission),
        ),
        mock.patch.object(
            controller,
            "_r8u_r5_build_worker_context",
            side_effect=controller.R8RControllerError(
                "SCHEDULER_EFFECTIVE_UID_MISMATCH"
            ),
        ),
        mock.patch.object(
            controller, "_r8u_r4_portable_candidate_projection"
        ) as candidate_scan,
    ):
        _expect_code(
            controller.run_r8u_r5_batch16_publication_resume,
            "SCHEDULER_EFFECTIVE_UID_MISMATCH",
        )
    candidate_scan.assert_not_called()


def test_probe_and_gpu_dispatch_use_field_specific_job_and_task_codes() -> None:
    for function in (
        controller.run_r8u_r5_worker_context_probe,
        controller.run_r8u_r5_batch16_publication_resume,
    ):
        with mock.patch.dict(
            controller.os.environ,
            {"JOB_ID": "not-numeric", "SGE_TASK_ID": "undefined"},
            clear=True,
        ):
            _expect_code(function, "SCHEDULER_JOB_ID_BINDING_MISMATCH")
        with mock.patch.dict(
            controller.os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "17"},
            clear=True,
        ):
            _expect_code(function, "SCHEDULER_TASK_ID_BINDING_MISMATCH")

    source = _source(controller._read_r8u_r5_resume_submission_minimal)
    assert source.index("SCHEDULER_JOB_ID_BINDING_MISMATCH") < source.index(
        "_validate_r8u_r5_qstat_projection("
    )
    probe_source = _source(controller._validate_r8u_r5_probe_submission)
    assert "SCHEDULER_JOB_ID_BINDING_MISMATCH" in probe_source


def test_locality_is_fresh_and_written_before_claim() -> None:
    source = _source(controller.run_r8u_r5_batch16_publication_resume)
    locality = source.index("_r8u_r5_live_publication_locality(")
    locality_write = source.index("R8U_R5_LOCALITY_PATH", locality)
    claim = source.index("_r8u_r5_publication_claim(")
    claim_write = source.index("_r8u_r5_create_publication_claim(", claim)
    primitive = source.index("_r8u_r5_primitive_probe(")
    publication = source.index("_r8u_r5_publish_candidate(")
    assert locality < locality_write < claim < claim_write < primitive < publication
    assert "R8U_R4_LOCALITY_PATH" not in source
    assert "R8U_R4_PUBLICATION_CLAIM_PATH" not in source


def test_probe_accounting_is_a_hard_gpu_submission_gate() -> None:
    adjudicator = _source(controller.adjudicate_r8u_r5_worker_context_probe)
    assert adjudicator.count("_wait_r8u_r5_probe_accounting(") == 1
    assert "scheduler.qsub_environment_sha256(environment)" in adjudicator
    assert adjudicator.index("scheduler.qsub_environment_sha256(environment)") < (
        adjudicator.index("_wait_r8u_r5_probe_accounting(")
    )
    accounting_wait = _source(controller._wait_r8u_r5_probe_accounting)
    assert accounting_wait.count("_query_recovery_accounting(") == 1
    assert "R8U_R5_PROBE_ACCOUNTING_PATH" in adjudicator
    assert "failed" in adjudicator
    assert "exit_status" in adjudicator
    validator = _source(controller._validate_r8u_r5_probe_accounting)
    assert "_r8u_r5_probe_scheduler_log(" in validator
    assert "current_log_sha" in validator

    source = _source(controller.submit_r8u_r5_batch16_publication_resume)
    accounting = _only_position(
        source,
        (
            "validate_r8u_r5_worker_context_probe_accounting(",
            "_validate_r8u_r5_probe_accounting(",
            "R8U_R5_PROBE_ACCOUNTING_PATH",
        ),
    )
    authority = source.index("R8U_R5_AUTHORITY_PATH")
    qsub = source.index("scheduler._capture_qsub(")
    assert accounting < authority < qsub


def test_phase_submitters_have_exact_two_qsubs_and_no_retry_or_polling() -> None:
    probe = _source(controller.submit_r8u_r5_worker_context_probe)
    resume = _source(controller.submit_r8u_r5_batch16_publication_resume)
    assert probe.count("scheduler._capture_qsub(") == 1
    assert resume.count("scheduler._capture_qsub(") == 1
    assert probe.count("scheduler.build_qsub_environment(") == 1
    assert resume.count("scheduler.build_qsub_environment(") == 1
    assert probe.count("_r8u_r5_probe_qsub_command(") == 1
    assert resume.count("_r8u_r5_resume_qsub_command(") == 1
    assert resume.count("_r8u_r5_qstat_projection(") == 1
    assert "_r8u_r4_portable_candidate_projection(" not in resume
    assert resume.index("scheduler._capture_qsub(") < resume.index(
        "_r8u_r5_qstat_projection("
    )
    calls = _call_names(controller.submit_r8u_r5_worker_context_probe) + _call_names(
        controller.submit_r8u_r5_batch16_publication_resume
    )
    assert not any(call.endswith("time.sleep") for call in calls)
    assert not _has_while(controller.submit_r8u_r5_worker_context_probe)
    assert not _has_while(controller.submit_r8u_r5_batch16_publication_resume)
    assert "submit_r8u_r5_continuation_17_19(" not in resume
    assert "run_r8u_r5_continuation_finalizer(" not in resume
    assert "automatic_retry_authorized" in controller.R8U_R5_PROBE_SUBMISSION_KEYS
    assert "automatic_retry_authorized" in controller.R8U_R5_RESUME_SUBMISSION_KEYS


def test_future_continuation_submitter_is_strict_but_unreachable_this_phase() -> None:
    future = _source(controller.submit_r8u_r5_continuation_17_19)
    resume = _source(controller.submit_r8u_r5_batch16_publication_resume)
    assert future.count("scheduler.build_qsub_environment(") == 1
    assert future.count("scheduler._capture_qsub(") == 2
    assert "validate_r8u_r5_resume_terminal(" in future
    assert "R8U_R5_ACCOUNTING_PATH" in future or (
        "_validate_r8u_r5_resume_accounting(" in future
    )
    assert "submit_r8u_r5_continuation_17_19(" not in resume


def test_r4_failure_evidence_is_fixed_without_login_candidate_scan() -> None:
    source = _source(controller._r8u_r5_r4_failure_evidence)
    assert "_r8u_r5_read_fixed_r4_scheduler_log(" in source
    assert "validate_r8u_r4_replay_diagnosis(" in source
    assert "validate_r8u_r4_portable_candidate_authority(" in source
    assert "_r8u_r4_portable_candidate_projection(" not in source
    assert "candidate_npz_files" in source
    assert "R8U_R3_CANDIDATE_NPZ_FILES" in source
    required = {
        "failed_job_id",
        "scheduler_failed",
        "application_exit_status",
        "first_failed_stage",
        "exact_failure_code",
        "scheduler_log_sha256",
        "portable_candidate_authority_sha256",
        "r8u_r4_capacity_sha256",
        "r8u_r4_resume_authority_sha256",
        "r8u_r4_submission_sha256",
        "portable_candidate_pass",
        "publication_locality_ran",
        "publication_ran",
        "echoprime_ran",
    }
    assert required <= controller.R8U_R5_R4_FAILURE_EVIDENCE_KEYS


def test_resume_contract_freezes_candidate_and_scientific_nonactions() -> None:
    authority_keys = controller.R8U_R5_RESUME_AUTHORITY_KEYS
    assert {
        "portable_candidate_authority_sha256",
        "cloud_requests_authorized",
        "downloads_authorized",
        "dicom_body_reads_authorized",
        "dicom_extraction_executions_authorized",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
        "maximum_new_gpu_resume_qsubs",
    } <= authority_keys
    submission_keys = controller.R8U_R5_RESUME_SUBMISSION_KEYS
    assert {
        "portable_candidate_authority_sha256",
        "scheduler_submission_count",
        "resume_is_array",
        "gpu_requested",
        "automatic_retry_authorized",
        "cloud_requests",
        "downloads",
        "dicom_body_reads_by_submitter",
        "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter",
        "model_fitting_count",
        "prediction_generation_count",
        "confirmatory_performance_access_count",
    } <= submission_keys
    assert controller.R8U_R3_CANDIDATE_NPZ_FILES == 10_187
    assert controller.R8U_FAILED_PARTIAL_FILES == 4_757


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
