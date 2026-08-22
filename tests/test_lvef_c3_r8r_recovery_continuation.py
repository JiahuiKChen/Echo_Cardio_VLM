from __future__ import annotations

"""Dependency-light contracts for the fixed Phase 1I-R8R controller.

Every test is zero-argument and uses only synthetic mappings, temporary files,
and mocks.  Nothing in this file contacts SCC, qsub/qstat, GCS, DICOM readers,
EchoPrime, a GPU runtime, or the production evidence tree.
"""

from contextlib import ExitStack, contextmanager
import hashlib
import inspect
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
from unittest import mock

try:
    import pytest
except ModuleNotFoundError:
    class _Raises:
        def __init__(self, expected: type[BaseException]):
            self.expected = expected
            self.value: BaseException | None = None

        def __enter__(self) -> _Raises:
            return self

        def __exit__(self, kind: Any, value: Any, _traceback: Any) -> bool:
            if kind is None:
                raise AssertionError(f"expected {self.expected.__name__}")
            if not issubclass(kind, self.expected):
                return False
            self.value = value
            return True

    class _DependencyLightPytest:
        raises = staticmethod(lambda expected: _Raises(expected))

    pytest = _DependencyLightPytest()


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_full_sequential as sequential
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import lvef_c3_r8r_recovery_continuation as r8r


IMPLEMENTATION_COMMIT = "b" * 40
QSUB_ENVIRONMENT_SHA256 = "9" * 64
RECOVERY_SHA256 = "8" * 64
CAPACITY_SHA256 = "7" * 64
PREFIX_SHA256 = tuple(item[2] for item in r8r.PREFIX_RECEIPT_AUTHORITIES)
RECOVERY_ACCOUNTING = {
    "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
    "job_id": "101",
    "task_id": "undefined",
    "failed": 0,
    "exit_status": 0,
    "start_time": "Sat Aug 22 12:00:00 2026",
    "end_time": "Sat Aug 22 12:10:00 2026",
    "ru_wallclock_seconds": "600.0",
}


def _assert_code(caught: Any, expected: str) -> None:
    assert getattr(caught.value, "code", None) == expected


def _fixed_run() -> SimpleNamespace:
    authority = SimpleNamespace(
        governing_commit=r8r.ORIGINAL_SCIENTIFIC_COMMIT,
        environment_receipt=Path("/synthetic/environment.json"),
        checkpoint=Path("/synthetic/checkpoint.pt"),
        billing_variable="R8R_TEST_BILLING_PROJECT",
        billing_project="synthetic-billing-project",
    )
    batches = [
        {
            "ordinal": index,
            "batch_id": f"c3_batch_{index:03d}",
            "objects": [],
        }
        for index in range(19)
    ]
    return SimpleNamespace(
        authority=authority,
        plan={"batches": batches},
        requirements=SimpleNamespace(batch_count=19),
        contract={},
        contract_path=Path("/synthetic/contract.yaml"),
        plan_sha256=r8r.ORIGINAL_PLAN_SHA256,
        runtime_authority={
            "environment_receipt_sha256": "e" * 64,
            "checkpoint_sha256": "c" * 64,
        },
        attempt_id=r8r.ORIGINAL_ATTEMPT_ID,
        production_root=Path("/synthetic/production"),
        attempt_root=Path("/synthetic/production/attempts")
        / r8r.ORIGINAL_ATTEMPT_ID,
        plan_path=Path("/synthetic/full_batch_plan.restricted.json"),
        launch_authority={"expected_no_cine_studies": 5},
        launch_authority_sha256="6" * 64,
        scheduler_job_identity="123",
    )


def _capacity_observation() -> dict[str, Any]:
    value: dict[str, Any] = {
        key: 0 for key in r8r.capacity.R8R_CONTINUATION_CAPACITY_KEYS
    }
    value.update(
        {
            "schema_version": 1,
            "artifact_type": r8r.capacity.R8R_CONTINUATION_ARTIFACT_TYPE,
            "status": r8r.CONTINUATION_CAPACITY_STATUS,
            "blocking_reason_codes": [],
            "original_attempt_id": r8r.ORIGINAL_ATTEMPT_ID,
            "original_plan_sha256": r8r.ORIGINAL_PLAN_SHA256,
            "original_scientific_governing_commit": (
                r8r.ORIGINAL_SCIENTIFIC_COMMIT
            ),
            "continuation_first_task": 4,
            "continuation_last_task": 19,
            "continuation_task_count": 16,
            "remaining_batch_count": 16,
            "remaining_studies": 3_780,
            "remaining_objects": 280_263,
            "remaining_source_bytes": 1_014_021_066_806,
            "quota_reserve_gate_passed": True,
            "physical_reserve_gate_passed": True,
            "file_slot_gate_passed": True,
            "native_capacity_snapshot_captures": 1,
            "native_quota_file_captures": 1,
            "capacity_command_captures": 5,
            "native_quota_authority_read_only": True,
            "pquota_display_crosscheck": "PASS",
        }
    )
    for key in r8r.capacity.R8R_CONTINUATION_ZERO_EFFECT_KEYS:
        value[key] = 0
    assert set(value) == r8r.capacity.R8R_CONTINUATION_CAPACITY_KEYS
    return value


def test_r8r_scientific_scope_and_public_interfaces_are_fixed() -> None:
    assert r8r.ORIGINAL_SCIENTIFIC_COMMIT == (
        "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
    )
    assert r8r.ORIGINAL_ATTEMPT_ID == (
        "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
    )
    assert r8r.ORIGINAL_PLAN_SHA256 == (
        "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
    )
    assert r8r.FIXED_BATCH3_ID == "c3_batch_002"
    assert r8r.FIXED_RECOVERY_TASK_ID == 3
    assert r8r.FIXED_CONTINUATION_TASK_IDS == tuple(range(4, 20))
    assert r8r.FIXED_CONTINUATION_TASK_RANGE == "4-19"
    assert r8r.FIXED_CONTINUATION_MAX_CONCURRENCY == 1
    assert sequential.R8R_FIXED_CONTINUATION.value == "R8R_FIXED_CONTINUATION"

    assert tuple(inspect.signature(r8r.recover_batch3_preservation).parameters) == ()
    assert tuple(inspect.signature(r8r.validate_recovery_terminal).parameters) == ()
    assert tuple(inspect.signature(r8r.run_continuation_array_task).parameters) == ()
    assert tuple(inspect.signature(r8r.run_continuation_finalizer).parameters) == ()
    assert set(inspect.signature(r8r.submit_batch3_recovery).parameters) == {
        "qsub_runner",
        "qstat_runner",
    }
    assert set(inspect.signature(r8r.submit_continuation).parameters) == {
        "qsub_runner",
        "qstat_runner",
        "qacct_runner",
        "capacity_process_runner",
    }
    option_strings = {
        option
        for action in r8r._parser()._actions
        for option in action.option_strings
    }
    assert option_strings == {
        "-h",
        "--help",
        "--submit-batch3-recovery",
        "--recover-batch3-preservation",
        "--validate-batch3-recovery",
        "--submit-continuation",
        "--run-continuation-array-task",
        "--run-continuation-finalizer",
    }
    assert not option_strings.intersection(
        {
            "--attempt-id",
            "--batch-id",
            "--plan",
            "--production-root",
            "--task-range",
        }
    )

    with (
        mock.patch.object(
            r8r.sequential,
            "_current_commit",
            return_value=IMPLEMENTATION_COMMIT,
        ),
        mock.patch.object(
            r8r.sequential,
            "_git",
            side_effect=["", "1"],
        ) as git_call,
    ):
        assert r8r._current_implementation_commit() == IMPLEMENTATION_COMMIT
    assert git_call.call_args_list == [
        mock.call(
            "merge-base",
            "--is-ancestor",
            r8r.ORIGINAL_SCIENTIFIC_COMMIT,
            IMPLEMENTATION_COMMIT,
        ),
        mock.call(
            "rev-list",
            "--count",
            f"{r8r.ORIGINAL_SCIENTIFIC_COMMIT}..{IMPLEMENTATION_COMMIT}",
        ),
    ]
    with (
        mock.patch.object(
            r8r.sequential,
            "_current_commit",
            return_value=IMPLEMENTATION_COMMIT,
        ),
        mock.patch.object(
            r8r.sequential,
            "_git",
            side_effect=["", "2"],
        ),
        pytest.raises(r8r.R8RControllerError) as caught,
    ):
        r8r._current_implementation_commit()
    _assert_code(caught, "R8R_IMPLEMENTATION_ANCESTRY_INVALID")

    def synthetic_stat(*, device: int, inode: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=os.geteuid(),
            st_gid=os.getegid(),
            st_dev=device,
            st_ino=inode,
            st_nlink=1,
            st_size=123,
            st_mtime_ns=456,
            st_ctime_ns=789,
        )

    node_a = synthetic_stat(device=10, inode=20)
    node_b = synthetic_stat(device=30, inode=40)
    with mock.patch.object(r8r.os, "lstat", side_effect=[node_a, node_a]):
        projection_a = r8r._metadata_row(
            Path("/synthetic/file"), root=Path("/synthetic"), kind="F"
        )
    with mock.patch.object(r8r.os, "lstat", side_effect=[node_b, node_b]):
        projection_b = r8r._metadata_row(
            Path("/synthetic/file"), root=Path("/synthetic"), kind="F"
        )
    assert projection_a == projection_b
    assert projection_a == [
        "file",
        "F",
        0o600,
        os.geteuid(),
        os.getegid(),
        1,
        123,
        456,
        789,
    ]


def test_r8r_qsub_commands_are_exact_and_closed() -> None:
    recovery = r8r._recovery_qsub_command(IMPLEMENTATION_COMMIT)
    assert recovery == [
        str(r8r.scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        f"lvef_c3_r8r_rec_{IMPLEMENTATION_COMMIT[:8]}",
        "-j",
        "y",
        "-o",
        str(r8r.RECOVERY_SCHEDULER_ROOT),
        "-l",
        "h_rt=02:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(r8r.RUNNER_PATH),
    ]
    assert "-t" not in recovery
    assert not any("gpu" in item.lower() for item in recovery)

    array = r8r._continuation_array_command(IMPLEMENTATION_COMMIT)
    assert array[array.index("-t") + 1] == "4-19"
    assert array[array.index("-tc") + 1] == "1"
    assert "gpus=1" in array
    assert "gpu_c=8.0" in array
    assert "gpu_memory=48G" in array
    assert array[-1] == str(r8r.RUNNER_PATH)

    finalizer = r8r._continuation_finalizer_command(
        IMPLEMENTATION_COMMIT, "7259999"
    )
    assert finalizer[finalizer.index("-hold_jid") + 1] == "7259999"
    assert "-t" not in finalizer
    assert not any("gpu" in item.lower() for item in finalizer)
    assert finalizer[-1] == str(r8r.RUNNER_PATH)
    assert r8r._parse_r8r_array_qsub_stdout(b"7259998.4-19:1\n") == "7259998"
    with pytest.raises(r8r.R8RControllerError) as caught:
        r8r._parse_r8r_array_qsub_stdout(b"7259998.1-19:1\n")
    _assert_code(caught, "R8R_ARRAY_QSUB_OUTPUT_INVALID")

    empty_qstat = subprocess.CompletedProcess(
        [],
        0,
        (
            b"<job_info><queue_info></queue_info>"
            b"<job_info></job_info></job_info>"
        ),
        b"",
    )
    r8r._validate_no_active_jobs(
        {"USER": "synthetic"}, runner=mock.Mock(return_value=empty_qstat)
    )
    for payload in (
        b"<job_info></job_info>",
        (
            b"<job_info><queue_info><job_list>"
            b"<JB_job_number>7253130</JB_job_number>"
            b"<JB_name>unrelated</JB_name></job_list></queue_info>"
            b"<job_info></job_info></job_info>"
        ),
        (
            b"<job_info><queue_info><job_list>"
            b"<JB_job_number>9000000</JB_job_number>"
            b"<JB_name>c3_pre_0123456789ab</JB_name></job_list>"
            b"</queue_info><job_info></job_info></job_info>"
        ),
    ):
        with pytest.raises(r8r.R8RControllerError) as caught:
            r8r._validate_no_active_jobs(
                {"USER": "synthetic"},
                runner=mock.Mock(
                    return_value=subprocess.CompletedProcess(
                        [], 0, payload, b""
                    )
                ),
            )
        assert caught.value.code in {
            "R8R_ACTIVE_JOB_CHECK_FAILED",
            "R8R_ACTIVE_MATCHING_JOB_EXISTS",
        }


def test_r8r_recovery_accounting_is_exact_and_mandatory() -> None:
    def result(*, failed: int = 0, exit_status: int = 0) -> subprocess.CompletedProcess[bytes]:
        payload = (
            "==============================================================\n"
            "qname all.q\n"
            "hostname suppressed\n"
            "jobnumber 101\n"
            "taskid undefined\n"
            f"failed {failed}\n"
            f"exit_status {exit_status}\n"
            "start_time Sat Aug 22 12:00:00 2026\n"
            "end_time Sat Aug 22 12:10:00 2026\n"
            "ru_wallclock 600.0\n"
        ).encode("utf-8")
        return subprocess.CompletedProcess([], 0, payload, b"")

    runner = mock.Mock(return_value=result())
    with mock.patch.object(r8r, "_validate_qacct_tool") as tool_gate:
        observed = r8r._query_recovery_accounting(
            recovery_job_id="101",
            environment={"USER": "synthetic"},
            runner=runner,
        )
    tool_gate.assert_called_once_with()
    assert observed == RECOVERY_ACCOUNTING
    assert runner.call_args.args[0] == [str(r8r.QACCT_PATH), "-j", "101"]

    for failed, exit_status in ((1, 0), (0, 78)):
        with (
            mock.patch.object(r8r, "_validate_qacct_tool"),
            pytest.raises(r8r.R8RControllerError) as caught,
        ):
            r8r._query_recovery_accounting(
                recovery_job_id="101",
                environment={"USER": "synthetic"},
                runner=mock.Mock(
                    return_value=result(
                        failed=failed, exit_status=exit_status
                    )
                ),
            )
        _assert_code(caught, "R8R_RECOVERY_ACCOUNTING_INVALID")

    extra_record = result().stdout + (
        "==============================================================\n"
        "jobnumber 202\n"
        "taskid undefined\n"
        "failed 0\n"
        "exit_status 0\n"
        "start_time Sat Aug 22 12:00:00 2026\n"
        "end_time Sat Aug 22 12:10:00 2026\n"
        "ru_wallclock 600.0\n"
    ).encode("utf-8")
    with (
        mock.patch.object(r8r, "_validate_qacct_tool"),
        pytest.raises(r8r.R8RControllerError) as caught,
    ):
        r8r._query_recovery_accounting(
            recovery_job_id="101",
            environment={"USER": "synthetic"},
            runner=mock.Mock(
                return_value=subprocess.CompletedProcess(
                    [], 0, extra_record, b""
                )
            ),
        )
    _assert_code(caught, "R8R_RECOVERY_ACCOUNTING_UNAVAILABLE")


def test_r8r_tasks_one_through_three_stop_before_every_effect_boundary() -> None:
    run = _fixed_run()
    forbidden = mock.Mock(side_effect=AssertionError("effect boundary crossed"))
    dependencies = sequential.FullDependencies(
        prior_batch_validator=forbidden,
        download=forbidden,
        dicom=forbidden,
        echoprime=forbidden,
        preserve=forbidden,
        retire=forbidden,
        finalize_batch=forbidden,
        token_provider_factory=forbidden,
        transport_factory=forbidden,
        digest_provider_factory=forbidden,
        environment_validator=forbidden,
        execution_context=sequential.R8R_FIXED_CONTINUATION,
    )
    with mock.patch.dict(os.environ, {}, clear=True):
        for task_id in (1, 2, 3):
            with pytest.raises(sequential.FullSequentialError) as caught:
                sequential.run_batch_task(
                    task_id=task_id, run=run, dependencies=dependencies
                )
            _assert_code(
                caught, "FULL_SEQUENTIAL_R8R_CONTINUATION_TASK_OUT_OF_SCOPE"
            )
    forbidden.assert_not_called()

    loader = mock.Mock(side_effect=AssertionError("run authority was opened"))
    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "123", "SGE_TASK_ID": "3"},
            clear=True,
        ),
        mock.patch.object(r8r, "_load_fixed_original_run", loader),
        pytest.raises(r8r.R8RControllerError) as caught,
    ):
        r8r.run_continuation_array_task()
    _assert_code(caught, "R8R_CONTINUATION_ARRAY_CONTEXT_INVALID")
    loader.assert_not_called()


def test_batch3_recovery_calls_only_preserve_authorize_retire_finalize() -> None:
    run = _fixed_run()
    paths = {
        "preservation": Path("/synthetic/preservation"),
        "pooling_ledger": Path("/synthetic/pooling-ledger.json"),
    }
    calls: list[str] = []
    captured: dict[str, Mapping[str, Any]] = {}

    def preserve(**kwargs: Any) -> Mapping[str, Any]:
        calls.append("preserve")
        captured["preserve"] = kwargs
        return {"status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"}

    def authorize(**_kwargs: Any) -> Path:
        calls.append("authorize")
        return Path("/synthetic/authorization.json")

    def retire(**kwargs: Any) -> Mapping[str, Any]:
        calls.append("retire")
        captured["retire"] = kwargs
        return {"status": "PASS"}

    def finalize(**_kwargs: Any) -> Mapping[str, Any]:
        calls.append("finalize")
        return {"status": "PASS_BATCH_FINALIZED"}

    def terminal(**_kwargs: Any) -> Mapping[str, Any]:
        calls.append("terminal")
        return {"status": r8r.RECOVERY_STATUS}

    def write(*_args: Any, **_kwargs: Any) -> str:
        calls.append("write")
        return "5" * 64

    forbidden = mock.Mock(side_effect=AssertionError("scientific replay crossed"))
    with (
        mock.patch.dict(
            os.environ,
            {
                "JOB_ID": "123",
                "SGE_TASK_ID": "undefined",
                "CUDA_VISIBLE_DEVICES": "",
            },
            clear=True,
        ),
        mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8r, "_validate_recovery_submission"),
        mock.patch.object(r8r, "_validate_original_controls"),
        mock.patch.object(r8r, "_validate_frozen_prefix"),
        mock.patch.object(r8r.os.path, "lexists", return_value=False),
        mock.patch.object(r8r.sequential, "_batch_paths", return_value=paths),
        mock.patch.object(r8r.preservation, "preserve_batch", side_effect=preserve),
        mock.patch.object(
            r8r.sequential,
            "_cache_retirement_authorization",
            side_effect=authorize,
        ),
        mock.patch.object(
            r8r.sequential, "_execute_cache_retirement", side_effect=retire
        ),
        mock.patch.object(
            r8r.sequential, "_validate_batch_finalization", side_effect=finalize
        ),
        mock.patch.object(r8r, "_recovery_terminal_receipt", side_effect=terminal),
        mock.patch.object(r8r, "_write_private_json", side_effect=write),
        mock.patch.object(r8r.core, "execute_exact_batch_download", forbidden),
        mock.patch.object(r8r.stages, "run_production_dicom_extraction", forbidden),
        mock.patch.object(r8r.stages, "run_production_echoprime", forbidden),
        mock.patch.object(r8r.sequential, "run_batch_task", forbidden),
    ):
        result = r8r.recover_batch3_preservation()

    assert result == {"status": r8r.RECOVERY_STATUS}
    assert calls == [
        "preserve",
        "authorize",
        "retire",
        "finalize",
        "terminal",
        "write",
    ]
    forbidden.assert_not_called()
    assert captured["preserve"]["runtime_validation_context"] is (
        stages.SEALED_SCHEDULER_RUNTIME_REPLAY
    )
    assert captured["preserve"]["artifact_validation_context"] is (
        r8r.preservation.R8R_FIXED_BATCH3_NO_DICOM_BODY
    )
    assert captured["preserve"]["scheduler_runner_path"] == r8r.RUNNER_PATH
    assert captured["retire"]["artifact_validation_context"] is (
        r8r.retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
    )
    assert captured["retire"]["scheduler_runner_path"] == r8r.RUNNER_PATH


def test_full_sequential_r8r_propagates_sealed_runtime_to_all_three_gates() -> None:
    run = _fixed_run()
    calls: dict[str, mock.Mock] = {
        name: mock.Mock() for name in ("environment", "echoprime", "preserve")
    }

    def environment(*_args: Any, **kwargs: Any) -> Mapping[str, Any]:
        calls["environment"](**kwargs)
        return {"status": "PASS"}

    def echoprime(**kwargs: Any) -> Mapping[str, Any]:
        calls["echoprime"](**kwargs)
        return {"status": "PASS"}

    def preserve(**kwargs: Any) -> Mapping[str, Any]:
        calls["preserve"](**kwargs)
        return {"status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"}

    dependencies = sequential.FullDependencies(
        prior_batch_validator=lambda **_kwargs: None,
        download=lambda **_kwargs: {},
        dicom=lambda **_kwargs: {"status": "PASS"},
        echoprime=echoprime,
        preserve=preserve,
        retire=lambda **_kwargs: {"status": "PASS"},
        finalize_batch=lambda **_kwargs: {
            "status": "PASS_BATCH_FINALIZED",
            "raw_dicoms_retained": True,
            "extracted_cache_retired": True,
        },
        environment_validator=environment,
        execution_context=sequential.R8R_FIXED_CONTINUATION,
    )

    @contextmanager
    def digest(*_args: Any, **_kwargs: Any) -> Iterator[object]:
        yield object()

    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch.object(sequential, "_validate_full_run"),
        mock.patch.object(
            sequential,
            "_extraction_cache_inventory",
            return_value=SimpleNamespace(active=0),
        ),
        mock.patch.object(sequential, "_ensure_private_directory"),
        mock.patch.object(sequential, "_write_private_json"),
        mock.patch.object(sequential, "_provider_and_transport", return_value=(object(), object())),
        mock.patch.object(sequential, "_digest_provider", side_effect=digest),
        mock.patch.object(sequential, "_cache_retirement_authorization", return_value=Path("/synthetic/authorization.json")),
        mock.patch.object(core, "initialize_resume_ledger", return_value={}),
        mock.patch.object(core, "sha256_file", return_value="c" * 64),
        mock.patch.object(stages, "validate_stage_predecessor"),
        mock.patch.object(stages, "validate_download_manifest_plan_membership"),
        mock.patch.object(stages, "validate_extraction_manifest_plan_membership"),
        mock.patch.object(stages, "advance_stage_ledger"),
    ):
        result = sequential.run_batch_task(
            task_id=4, run=run, dependencies=dependencies
        )

    assert result["status"] == "PASS_BATCH_FINALIZED"
    for name in ("environment", "echoprime", "preserve"):
        assert calls[name].call_count == 1
        assert calls[name].call_args.kwargs["runtime_validation_context"] is (
            stages.SEALED_SCHEDULER_RUNTIME_REPLAY
        )
    assert calls["preserve"].call_args.kwargs["scheduler_runner_path"] == (
        r8r.RUNNER_PATH
    )


def test_r8r_private_controls_are_no_clobber_and_single_use() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "control.restricted.json"
        first = {"status": "FIRST"}
        r8r._write_private_json(path, first)
        original = path.read_bytes()
        with pytest.raises(r8r.R8RControllerError) as caught:
            r8r._write_private_json(path, {"status": "SECOND"})
        _assert_code(caught, "R8R_CONTROL_NO_CLOBBER_FAILED")
        assert path.read_bytes() == original

        control_root = Path(temporary).resolve() / "fixed-control-root"
        r8r._create_private_directory_no_clobber(control_root)
        assert control_root.is_dir()
        assert control_root.stat().st_mode & 0o777 == 0o700
        with pytest.raises(r8r.R8RControllerError) as caught:
            r8r._create_private_directory_no_clobber(control_root)
        _assert_code(caught, "R8R_CONTROL_ROOT_COLLISION")

    run = _fixed_run()
    preserve = mock.Mock(side_effect=AssertionError("recovery repeated"))
    with (
        mock.patch.dict(
            os.environ,
            {
                "JOB_ID": "123",
                "SGE_TASK_ID": "undefined",
                "CUDA_VISIBLE_DEVICES": "",
            },
            clear=True,
        ),
        mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8r, "_validate_recovery_submission"),
        mock.patch.object(r8r, "_validate_original_controls"),
        mock.patch.object(r8r, "_validate_frozen_prefix"),
        mock.patch.object(r8r.os.path, "lexists", return_value=True),
        mock.patch.object(r8r.preservation, "preserve_batch", preserve),
        pytest.raises(r8r.R8RControllerError) as caught,
    ):
        r8r.recover_batch3_preservation()
    _assert_code(caught, "R8R_RECOVERY_ALREADY_COMPLETE")
    preserve.assert_not_called()


def test_capacity_receipt_rejects_non_singleton_or_effectful_observation() -> None:
    run = _fixed_run()
    prefix = (*PREFIX_SHA256, "f" * 64)
    valid = _capacity_observation()

    def build(observation: Mapping[str, Any]) -> Mapping[str, Any]:
        with mock.patch.object(
            r8r.capacity,
            "validate_fixed_r8r_continuation_capacity",
            side_effect=lambda _plan, value: dict(value),
        ):
            return r8r._continuation_capacity_receipt(
                run=run,
                implementation_commit=IMPLEMENTATION_COMMIT,
                observation=observation,
                prefix_receipts=prefix,
                recovery_terminal_sha256=RECOVERY_SHA256,
                recovery_job_id="101",
                recovery_scheduler_accounting=RECOVERY_ACCOUNTING,
                captured_at_utc="2026-08-22T12:34:56Z",
            )

    receipt = build(valid)
    assert receipt["continuation_task_range"] == "4-19"
    assert receipt["continuation_max_concurrency"] == 1
    assert receipt["model_fitting_count"] == 0
    assert receipt["prediction_generation_count"] == 0
    assert receipt["confirmatory_performance_access_count"] == 0

    for key, replacement in (
        ("native_capacity_snapshot_captures", 2),
        ("dicom_body_reads", 1),
        ("continuation_first_task", 3),
    ):
        altered = dict(valid)
        altered[key] = replacement
        with pytest.raises(r8r.R8RControllerError) as caught:
            build(altered)
        _assert_code(caught, "R8R_CONTINUATION_CAPACITY_INVALID")


def _exercise_chain_tamper(role: str) -> str:
    type_equivalent = role.endswith("_type")
    if type_equivalent:
        role = role.removesuffix("_type")
    run = _fixed_run()
    capacity_value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "capacity",
        "captured_at_utc": "2026-08-22T12:34:56Z",
        "capacity_observation": {},
        "recovery_scheduler_accounting": RECOVERY_ACCOUNTING,
    }
    summary_value: dict[str, Any] = {"schema_version": 1, "kind": "summary"}
    claim_value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "claim",
        "qsub_environment_sha256": QSUB_ENVIRONMENT_SHA256,
    }
    submission_value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "submission",
        "array_job_id": "201",
        "finalizer_job_id": "202",
    }
    selected = {
        "capacity": capacity_value,
        "summary": summary_value,
        "claim": claim_value,
        "submission": submission_value,
    }[role]
    if type_equivalent:
        selected["schema_version"] = 1.0
    else:
        selected["tampered"] = True
    capacity_payload = r8r._canonical_bytes(capacity_value)
    claim_payload = r8r._canonical_bytes(claim_value)
    submission_payload = r8r._canonical_bytes(submission_value)
    loads = [
        (capacity_value, capacity_payload),
        (summary_value, r8r._canonical_bytes(summary_value)),
        (claim_value, claim_payload),
        (submission_value, submission_payload),
    ]

    def file_sha(path: Path) -> str:
        if path == r8r.RECOVERY_TERMINAL_PATH:
            return RECOVERY_SHA256
        if path == r8r.CONTINUATION_CLAIM_PATH:
            return hashlib.sha256(claim_payload).hexdigest()
        if path == r8r.CONTINUATION_SUBMISSION_PATH:
            return hashlib.sha256(submission_payload).hexdigest()
        raise AssertionError(f"unexpected hash path: {path}")

    expected_capacity = {
        "schema_version": 1,
        "kind": "capacity",
        "captured_at_utc": "2026-08-22T12:34:56Z",
        "capacity_observation": {},
        "recovery_scheduler_accounting": RECOVERY_ACCOUNTING,
    }
    expected_summary = {"schema_version": 1, "kind": "summary"}
    expected_claim = {
        "schema_version": 1,
        "kind": "claim",
        "qsub_environment_sha256": QSUB_ENVIRONMENT_SHA256,
    }
    expected_submission = {
        "schema_version": 1,
        "kind": "submission",
        "array_job_id": "201",
        "finalizer_job_id": "202",
    }
    with (
        mock.patch.object(
            r8r, "validate_recovery_terminal", return_value={"status": r8r.RECOVERY_STATUS}
        ),
        mock.patch.object(
            r8r,
            "_validate_recovery_submission",
            return_value=({}, {"recovery_job_id": "101"}),
        ),
        mock.patch.object(r8r, "_validate_frozen_prefix", return_value=(*PREFIX_SHA256, "f" * 64)),
        mock.patch.object(r8r, "_load_private_json", side_effect=loads),
        mock.patch.object(r8r, "_current_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8r.core, "sha256_file", side_effect=file_sha),
        mock.patch.object(r8r, "_continuation_capacity_receipt", return_value=expected_capacity),
        mock.patch.object(r8r, "_continuation_capacity_summary", return_value=expected_summary),
        mock.patch.object(r8r, "_continuation_claim", return_value=expected_claim),
        mock.patch.object(r8r, "_build_continuation_submission_receipt", return_value=expected_submission),
        pytest.raises(r8r.R8RControllerError) as caught,
    ):
        r8r._validate_continuation_chain(
            run, current_job_id=None, role="array", wait=False
        )
    return str(caught.value.code)


def test_capacity_claim_and_submission_tampering_fail_closed() -> None:
    assert _exercise_chain_tamper("capacity") == "R8R_CONTINUATION_CAPACITY_INVALID"
    assert _exercise_chain_tamper("summary") == "R8R_CONTINUATION_CAPACITY_INVALID"
    assert _exercise_chain_tamper("claim") == "R8R_CONTINUATION_CLAIM_INVALID"
    assert _exercise_chain_tamper("submission") == "R8R_CONTINUATION_SUBMISSION_INVALID"
    assert _exercise_chain_tamper("capacity_type") == "R8R_CONTINUATION_CAPACITY_INVALID"
    assert _exercise_chain_tamper("summary_type") == "R8R_CONTINUATION_CAPACITY_INVALID"
    assert _exercise_chain_tamper("claim_type") == "R8R_CONTINUATION_CLAIM_INVALID"
    assert _exercise_chain_tamper("submission_type") == "R8R_CONTINUATION_SUBMISSION_INVALID"


def test_recovery_authority_and_submission_tampering_fail_closed() -> None:
    run = _fixed_run()
    retained = {"retained": "authority"}
    evidence = {
        "stdout_bytes": 4,
        "stdout_sha256": hashlib.sha256(b"101\n").hexdigest(),
        "stderr_bytes": 0,
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "exit_status": 0,
    }
    with (
        mock.patch.object(r8r, "_current_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8r, "_validate_original_controls", return_value={"controls": "fixed"}),
        mock.patch.object(r8r, "_script_authority", return_value={"scripts": "fixed"}),
        mock.patch.object(r8r.scheduler, "_read_scheduler_evidence", return_value=b"101\n"),
        mock.patch.object(r8r, "_qsub_evidence_authority", return_value=evidence),
    ):
        authority = r8r._build_recovery_authority(
            run=run,
            implementation_commit=IMPLEMENTATION_COMMIT,
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
            retained_authority=retained,
        )
        altered_authority = dict(authority)
        altered_authority["dicom_body_reads_authorized"] = 1
        with pytest.raises(r8r.R8RControllerError) as caught:
            r8r._validate_recovery_authority(
                run, altered_authority, require_retained_current=False
            )
        _assert_code(caught, "R8R_RECOVERY_AUTHORITY_INVALID")

        type_changed_authority = dict(authority)
        type_changed_authority["schema_version"] = 1.0
        with pytest.raises(r8r.R8RControllerError) as caught:
            r8r._validate_recovery_authority(
                run, type_changed_authority, require_retained_current=False
            )
        _assert_code(caught, "R8R_RECOVERY_AUTHORITY_INVALID")

        receipt = r8r._build_recovery_submission_receipt(
            implementation_commit=IMPLEMENTATION_COMMIT,
            recovery_job_id="101",
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
        )
        altered_receipt = dict(receipt)
        altered_receipt["gpu_requested"] = True
        authority_payload = r8r._canonical_bytes(authority)
        with (
            mock.patch.object(
                r8r,
                "_load_private_json",
                side_effect=[
                    (authority, authority_payload),
                    (altered_receipt, r8r._canonical_bytes(altered_receipt)),
                ],
            ),
            mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
            mock.patch.object(
                r8r.core,
                "sha256_file",
                return_value=hashlib.sha256(authority_payload).hexdigest(),
            ),
            pytest.raises(r8r.R8RControllerError) as caught,
        ):
            r8r._validate_recovery_submission(require_retained_current=False)
        _assert_code(caught, "R8R_RECOVERY_SUBMISSION_RECEIPT_INVALID")

        type_changed_receipt = dict(receipt)
        type_changed_receipt["automatic_retry_authorized"] = 0
        with (
            mock.patch.object(
                r8r,
                "_load_private_json",
                side_effect=[
                    (authority, authority_payload),
                    (
                        type_changed_receipt,
                        r8r._canonical_bytes(type_changed_receipt),
                    ),
                ],
            ),
            mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
            mock.patch.object(
                r8r.core,
                "sha256_file",
                return_value=hashlib.sha256(authority_payload).hexdigest(),
            ),
            pytest.raises(r8r.R8RControllerError) as caught,
        ):
            r8r._validate_recovery_submission(require_retained_current=False)
        _assert_code(caught, "R8R_RECOVERY_SUBMISSION_RECEIPT_INVALID")


def test_submitters_issue_exactly_three_total_qsubs_and_no_retry_path() -> None:
    run = _fixed_run()
    capture = mock.Mock(side_effect=["101", "201", "202"])
    environment = {"USER": "synthetic"}

    recovery_patches = (
        mock.patch.object(r8r.scheduler, "validate_scheduler_tools"),
        mock.patch.object(r8r, "_current_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8r.scheduler, "build_qsub_environment", return_value=(environment, {})),
        mock.patch.object(r8r.scheduler, "qsub_environment_sha256", return_value=QSUB_ENVIRONMENT_SHA256),
        mock.patch.object(r8r, "_validate_no_active_jobs"),
        mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8r, "_validate_original_controls"),
        mock.patch.object(r8r, "_validate_frozen_prefix"),
        mock.patch.object(r8r, "_require_batch3_recovery_absent"),
        mock.patch.object(r8r, "_batch3_retained_authority", return_value={}),
        mock.patch.object(r8r.os.path, "lexists", return_value=False),
        mock.patch.object(r8r, "_create_private_directory_no_clobber"),
        mock.patch.object(r8r, "_build_recovery_authority", return_value={}),
        mock.patch.object(r8r, "_build_recovery_submission_receipt", return_value={}),
        mock.patch.object(r8r, "_write_private_json", return_value="5" * 64),
        mock.patch.object(r8r, "_validate_recovery_submission"),
        mock.patch.object(r8r.scheduler, "_capture_qsub", side_effect=capture),
    )
    with ExitStack() as stack:
        for patcher in recovery_patches:
            stack.enter_context(patcher)
        recovery = r8r.submit_batch3_recovery()
    assert recovery["new_qsub_submissions"] == 1

    continuation_patches = (
        mock.patch.object(r8r.scheduler, "validate_scheduler_tools"),
        mock.patch.object(r8r, "_current_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8r.scheduler, "build_qsub_environment", return_value=(environment, {})),
        mock.patch.object(r8r.scheduler, "qsub_environment_sha256", return_value=QSUB_ENVIRONMENT_SHA256),
        mock.patch.object(r8r, "_validate_no_active_jobs"),
        mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8r, "_validate_original_controls"),
        mock.patch.object(r8r, "validate_recovery_terminal", return_value={"status": r8r.RECOVERY_STATUS}),
        mock.patch.object(
            r8r,
            "_validate_recovery_submission",
            return_value=({}, {"recovery_job_id": "101"}),
        ),
        mock.patch.object(
            r8r,
            "_query_recovery_accounting",
            return_value=RECOVERY_ACCOUNTING,
        ),
        mock.patch.object(r8r, "_validate_frozen_prefix", return_value=(*PREFIX_SHA256, "f" * 64)),
        mock.patch.object(r8r.core, "sha256_file", return_value=RECOVERY_SHA256),
        mock.patch.object(r8r.sequential, "_extraction_cache_inventory", return_value=SimpleNamespace(active=0)),
        mock.patch.object(r8r.os.path, "lexists", return_value=False),
        mock.patch.object(r8r.capacity, "probe_fixed_r8r_continuation_capacity", return_value={"status": r8r.CONTINUATION_CAPACITY_STATUS}),
        mock.patch.object(r8r, "_continuation_capacity_receipt", return_value={}),
        mock.patch.object(r8r, "_continuation_capacity_summary", return_value={}),
        mock.patch.object(r8r, "_continuation_claim", return_value={}),
        mock.patch.object(r8r, "_build_continuation_submission_receipt", return_value={}),
        mock.patch.object(r8r, "_create_private_directory_no_clobber"),
        mock.patch.object(r8r, "_write_private_json", return_value="5" * 64),
        mock.patch.object(r8r, "_validate_continuation_chain"),
        mock.patch.object(r8r.scheduler, "_capture_qsub", side_effect=capture),
    )
    with ExitStack() as stack:
        for patcher in continuation_patches:
            stack.enter_context(patcher)
        continuation = r8r.submit_continuation()
    assert continuation["new_qsub_submissions"] == 2
    assert capture.call_count == 3
    assert [call.args[0] for call in capture.call_args_list] == [
        "recovery",
        "array",
        "finalizer",
    ]

    with (
        mock.patch.object(r8r.scheduler, "_read_scheduler_evidence", side_effect=[b"201.4-19:1\n", b"202\n"]),
        mock.patch.object(r8r, "_qsub_evidence_authority", return_value={}),
    ):
        submission = r8r._build_continuation_submission_receipt(
            implementation_commit=IMPLEMENTATION_COMMIT,
            recovery_job_id="101",
            array_job_id="201",
            finalizer_job_id="202",
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
            continuation_capacity_receipt_sha256=CAPACITY_SHA256,
            continuation_claim_sha256="6" * 64,
        )
    assert submission["scheduler_submission_count"] == 2
    assert submission["scheduler_submission_maximum"] == 2
    assert submission["third_continuation_submission_reachable"] is False

    with pytest.raises(r8r.R8RControllerError) as caught:
        r8r._build_continuation_submission_receipt(
            implementation_commit=IMPLEMENTATION_COMMIT,
            recovery_job_id="201",
            array_job_id="201",
            finalizer_job_id="202",
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
            continuation_capacity_receipt_sha256=CAPACITY_SHA256,
            continuation_claim_sha256="6" * 64,
        )
    _assert_code(caught, "R8R_CONTINUATION_SUBMISSION_INVALID")


def test_original_controls_are_read_only_exact_authorities() -> None:
    expected = {
        r8r.ATTEMPT_ROOT / "full_capacity_receipt.restricted.json": (
            r8r.ORIGINAL_CAPACITY_BYTES,
            r8r.ORIGINAL_CAPACITY_SHA256,
        ),
        r8r.ATTEMPT_ROOT / "full_submission_claim.restricted.json": (
            r8r.ORIGINAL_CLAIM_BYTES,
            r8r.ORIGINAL_CLAIM_SHA256,
        ),
        r8r.ATTEMPT_ROOT
        / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME: (
            r8r.ORIGINAL_DYNAMIC_CAPACITY_BYTES,
            r8r.ORIGINAL_DYNAMIC_CAPACITY_SHA256,
        ),
        r8r.ATTEMPT_ROOT / "full_launch_authority.restricted.json": (
            r8r.ORIGINAL_LAUNCH_BYTES,
            r8r.ORIGINAL_LAUNCH_SHA256,
        ),
        r8r.ATTEMPT_ROOT / "full_batch_plan.restricted.json": (
            r8r.ORIGINAL_PLAN_BYTES,
            r8r.ORIGINAL_PLAN_SHA256,
        ),
        r8r.ATTEMPT_ROOT
        / "scheduler/submission_receipt.restricted.json": (
            r8r.ORIGINAL_SUBMISSION_BYTES,
            r8r.ORIGINAL_SUBMISSION_SHA256,
        ),
    }
    observed: dict[Path, tuple[int, str]] = {}

    def read(path: Path, *, size: int, digest: str) -> bytes:
        observed[path] = (size, digest)
        return f"synthetic:{path.name}".encode("ascii")

    writer = mock.Mock(side_effect=AssertionError("original control write"))
    with (
        mock.patch.object(r8r, "_read_private_exact", side_effect=read),
        mock.patch.object(r8r, "_write_private_json", writer),
    ):
        value = r8r._validate_original_controls()
    assert observed == expected
    assert set(value) == {
        "original_capacity_sha256",
        "original_claim_sha256",
        "original_dynamic_capacity_sha256",
        "original_launch_sha256",
        "original_plan_sha256",
        "original_submission_sha256",
    }
    assert r8r.PREFIX_RECEIPT_AUTHORITIES == (
        (
            "c3_batch_000",
            4_730,
            "e8f1b505422af64fc98c44f1cb85da52528f014c904e1cbfe319ca0109f31277",
        ),
        (
            "c3_batch_001",
            4_725,
            "54c536f5c2faa712bc3a97d18c6c6fde804941d096dc307dbaf50e6c33e32c98",
        ),
    )
    writer.assert_not_called()


def test_finalizer_binds_all_five_r8r_chain_files_and_no_analysis_outputs() -> None:
    run = _fixed_run()
    chain_paths = (
        r8r.RECOVERY_AUTHORITY_PATH,
        r8r.RECOVERY_TERMINAL_PATH,
        r8r.CONTINUATION_CAPACITY_PATH,
        r8r.CONTINUATION_CLAIM_PATH,
        r8r.CONTINUATION_SUBMISSION_PATH,
    )
    chain_sha = {path: str(index + 1) * 64 for index, path in enumerate(chain_paths)}
    hashed: list[Path] = []

    def file_sha(path: Path) -> str:
        hashed.append(path)
        return chain_sha[path]

    finalized: dict[str, Any] = {}

    def finish(_receipts: Any, **kwargs: Any) -> Mapping[str, Any]:
        finalized.update(kwargs)
        return {
            "status": "PASS_PRODUCTION_C3_FINALIZED",
            "production_batches": 19,
            "selected_studies": 4_530,
            "pooled_imaging_eligible_studies": 4_525,
            "no_cine_studies": 5,
            "all_authority_bindings_identical": False,
            "all_scientific_authority_bindings_identical": True,
            "implementation_authority_epoch_count": 2,
            "r8r_implementation_commit": IMPLEMENTATION_COMMIT,
            "r8r_recovery_continuation_authority_sha256": "a" * 64,
            "model_fitting_count": 0,
            "endpoint_prediction_count": 0,
            "confirmatory_performance_access_count": 0,
        }

    with (
        mock.patch.dict(
            os.environ,
            {
                "JOB_ID": "202",
                "SGE_TASK_ID": "undefined",
                "CUDA_VISIBLE_DEVICES": "",
            },
            clear=True,
        ),
        mock.patch.object(r8r, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8r, "_validate_continuation_chain"),
        mock.patch.object(
            r8r.sequential,
            "_batch_paths",
            side_effect=lambda _run, batch_id: {
                "final_receipt": Path("/synthetic") / f"{batch_id}.json"
            },
        ),
        mock.patch.object(r8r, "_ensure_private_directory"),
        mock.patch.object(r8r, "_current_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8r.core, "sha256_file", side_effect=file_sha),
        mock.patch.object(r8r.finalizer, "finalize_receipts", side_effect=finish),
        mock.patch.object(r8r.finalizer, "write_json_atomic"),
    ):
        result = r8r.run_continuation_finalizer()

    assert hashed == list(chain_paths)
    authority = finalized["r8r_implementation_authority"]
    assert authority.implementation_commit == IMPLEMENTATION_COMMIT
    assert authority.recovery_authority_sha256 == chain_sha[chain_paths[0]]
    assert authority.recovery_terminal_receipt_sha256 == chain_sha[chain_paths[1]]
    assert authority.continuation_capacity_receipt_sha256 == chain_sha[chain_paths[2]]
    assert authority.continuation_claim_sha256 == chain_sha[chain_paths[3]]
    assert authority.continuation_submission_receipt_sha256 == chain_sha[chain_paths[4]]
    assert result["model_fitting_count"] == 0
    assert result["endpoint_prediction_count"] == 0
    assert result["confirmatory_performance_access_count"] == 0


def test_all_r8r_authorities_keep_model_prediction_and_confirmatory_work_unreachable() -> None:
    run = _fixed_run()
    with (
        mock.patch.object(r8r, "_validate_original_controls", return_value={}),
        mock.patch.object(r8r, "_script_authority", return_value={}),
    ):
        recovery = r8r._build_recovery_authority(
            run=run,
            implementation_commit=IMPLEMENTATION_COMMIT,
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
            retained_authority={},
        )
        claim = r8r._continuation_claim(
            run=run,
            implementation_commit=IMPLEMENTATION_COMMIT,
            qsub_environment_sha256=QSUB_ENVIRONMENT_SHA256,
            prefix_receipts=(*PREFIX_SHA256, "f" * 64),
            recovery_terminal_sha256=RECOVERY_SHA256,
            capacity_receipt_sha256=CAPACITY_SHA256,
            recovery_job_id="101",
            recovery_accounting_sha256=core.canonical_json_sha256(
                RECOVERY_ACCOUNTING
            ),
        )
    for authority in (recovery, claim):
        assert authority["model_fitting_authorized"] is False
        assert authority["prediction_authorized"] is False
        assert authority["confirmatory_performance_access_authorized"] is False
    assert recovery["embedding_generations_authorized"] == 0
    assert claim["embedding_generations_by_submitter"] == 0
    assert claim["third_continuation_submission_reachable"] is False


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"R8R_DEPENDENCY_LIGHT_TESTS=PASS ({len(tests)})")
