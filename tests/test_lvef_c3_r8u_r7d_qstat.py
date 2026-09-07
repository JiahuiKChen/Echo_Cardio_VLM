#!/usr/bin/env python3
"""Focused, offline tests for the R8U-R7D qstat contradiction diagnostic."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_full_scheduler as scheduler


JOB_ID = "8123456"
OWNER = "sealed-owner"
JOB_NAME = "lvef_c3_r8u_r7d_seq_1234abcd"
ENVIRONMENT = {"USER": OWNER, "PATH": "/usr/bin:/bin"}


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _job(
    *,
    job_id: str = JOB_ID,
    name: str = JOB_NAME,
    owner: str = OWNER,
    state: str = "r",
    tasks: str | None = "17",
) -> str:
    task_xml = "" if tasks is None else f"<tasks>{tasks}</tasks>"
    return (
        '<job_list state="running">'
        f"<JB_job_number>{job_id}</JB_job_number>"
        f"<JB_name>{name}</JB_name>"
        f"<JB_owner>{owner}</JB_owner>"
        f"<state>{state}</state>{task_xml}</job_list>"
    )


def _xml(*jobs: str) -> bytes:
    return (
        "<job_info><queue_info>"
        + "".join(jobs)
        + "</queue_info><job_info/></job_info>"
    ).encode("ascii")


def _runner(
    *payloads: bytes,
) -> tuple[Callable[..., subprocess.CompletedProcess[bytes]], list[dict[str, Any]]]:
    pending = iter(payloads)
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, next(pending), b"")

    return run, calls


def _diagnose(*payloads: bytes, **overrides: Any) -> tuple[Any, FakeTime, list[Any]]:
    fake_time = FakeTime()
    runner, calls = _runner(*payloads)
    arguments: dict[str, Any] = {
        "environment": ENVIRONMENT,
        "expected_job_id": JOB_ID,
        "expected_task_id": "17",
        "expected_owner": OWNER,
        "expected_full_job_name": JOB_NAME,
        "runner": runner,
        "clock": fake_time.clock,
        "sleeper": fake_time.sleep,
    }
    arguments.update(overrides)
    return scheduler.diagnose_r8u_r7d_qstat_self(**arguments), fake_time, calls


def _assert_error(code: str, callback: Callable[[], Any]) -> None:
    try:
        callback()
    except scheduler.FullSchedulerError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_exact_full_xml_self_record_passes_immediately_and_is_aggregate_safe() -> None:
    diagnostic, fake_time, calls = _diagnose(_xml(_job()))
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_MATCHED"
    assert diagnostic.observation_count == 1
    assert diagnostic.row_ever_visible is True
    assert diagnostic.job_id_equality is True
    assert diagnostic.task_id_equality is True
    assert diagnostic.owner_equality is True
    assert diagnostic.full_job_name_equality is True
    assert diagnostic.observed_scheduler_state_category == "RUNNING_R"
    assert diagnostic.observations[0].state_token == "r"
    assert fake_time.sleeps == []
    assert calls[0]["command"] == [
        str(scheduler.QSTAT_PATH),
        "-xml",
        "-u",
        OWNER,
    ]
    public = repr(diagnostic._asdict())
    assert JOB_ID not in public and OWNER not in public and JOB_NAME not in public


def test_absent_first_then_exact_record_passes_after_one_bounded_retry() -> None:
    diagnostic, fake_time, calls = _diagnose(_xml(), _xml(_job()))
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_MATCHED"
    assert diagnostic.observation_count == 2
    assert [item.record_present for item in diagnostic.observations] == [False, True]
    assert fake_time.sleeps == [scheduler.R8U_R7D_QSTAT_RETRY_INTERVAL_SECONDS]
    assert len(calls) == 2


def test_absent_throughout_is_nonblocking_and_strictly_bounded() -> None:
    diagnostic, fake_time, calls = _diagnose(_xml(), _xml(), _xml())
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE"
    assert diagnostic.observation_count == 3
    assert diagnostic.row_ever_visible is False
    assert diagnostic.observed_scheduler_state_category == "NOT_OBSERVED"
    assert all(item.job_id_match is None for item in diagnostic.observations)
    assert len(calls) == scheduler.R8U_R7D_QSTAT_MAXIMUM_OBSERVATIONS == 3
    assert sum(fake_time.sleeps) < scheduler.R8U_R7D_QSTAT_MAXIMUM_ELAPSED_SECONDS
    assert all(
        call["timeout"] <= scheduler.R8U_R7D_QSTAT_COMMAND_TIMEOUT_SECONDS
        for call in calls
    )


def test_exact_record_in_non_r_state_is_nonblocking_transitional() -> None:
    diagnostic, _fake_time, _calls = _diagnose(_xml(_job(state="qw")))
    assert diagnostic.classification == "PASS_QSTAT_TRANSITIONAL_STATE"
    assert diagnostic.observed_scheduler_state_category == "TRANSITIONAL_NON_R"
    assert diagnostic.observations[0].state_token == "qw"


def test_each_concrete_field_contradiction_returns_its_closed_code() -> None:
    cases = (
        (
            _job(job_id="8123457"),
            "BLOCKED_QSTAT_SELF_JOB_ID_CONTRADICTION",
            "job_id_equality",
        ),
        (
            _job(tasks="18"),
            "BLOCKED_QSTAT_SELF_TASK_ID_CONTRADICTION",
            "task_id_equality",
        ),
        (
            _job(owner="other-owner"),
            "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION",
            "owner_equality",
        ),
        (
            _job(name="lvef_c3_r8u_r7d_seq_deadbeef"),
            "BLOCKED_QSTAT_SELF_JOB_NAME_CONTRADICTION",
            "full_job_name_equality",
        ),
    )
    for job, code, equality_field in cases:
        diagnostic, _fake_time, _calls = _diagnose(_xml(job))
        assert diagnostic.classification == code
        assert getattr(diagnostic, equality_field) is False
        assert diagnostic.row_ever_visible is True
        assert diagnostic.observation_count == 1


def test_job_and_name_contradictions_survive_nonmatching_task_filter() -> None:
    cases = (
        (
            _job(job_id="8123457", state="qw", tasks="18-19:1"),
            "BLOCKED_QSTAT_SELF_JOB_ID_CONTRADICTION",
            "job_id_equality",
        ),
        (
            _job(
                name="lvef_c3_r8u_r7d_seq_deadbeef",
                state="qw",
                tasks="18-19:1",
            ),
            "BLOCKED_QSTAT_SELF_JOB_NAME_CONTRADICTION",
            "full_job_name_equality",
        ),
    )
    for job, code, equality_field in cases:
        diagnostic, _fake_time, _calls = _diagnose(_xml(job))
        assert diagnostic.classification == code
        assert getattr(diagnostic, equality_field) is False
        assert diagnostic.observations[0].task_id_match is False
        assert diagnostic.row_ever_visible is True


def test_duplicate_records_for_current_task_are_blocking() -> None:
    diagnostic, _fake_time, _calls = _diagnose(_xml(_job(), _job(state="qw")))
    assert diagnostic.classification == "BLOCKED_QSTAT_SELF_MULTIPLE_RECORDS"
    assert diagnostic.observations[0].record_present is True
    assert diagnostic.observations[0].unique is False
    assert diagnostic.observed_scheduler_state_category == "MULTIPLE_RECORDS"


def test_array_sibling_partitions_are_not_duplicates() -> None:
    diagnostic, _fake_time, _calls = _diagnose(
        _xml(_job(tasks="17"), _job(state="qw", tasks="18-19:1"))
    )
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_MATCHED"
    assert diagnostic.observations[0].unique is True
    assert diagnostic.observation_count == 1


def test_pending_sibling_without_current_row_is_temporary_absence_not_task_error() -> None:
    pending_sibling = _xml(_job(state="qw", tasks="18-19:1"))
    diagnostic, _fake_time, _calls = _diagnose(
        pending_sibling, pending_sibling, pending_sibling
    )
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE"
    assert diagnostic.task_id_equality is True


def test_nonarray_finalizer_record_uses_same_diagnostic_path() -> None:
    diagnostic, _fake_time, _calls = _diagnose(
        _xml(_job(name="lvef_c3_r8u_r7d_fin_1234abcd", tasks=None)),
        expected_task_id=None,
        expected_full_job_name="lvef_c3_r8u_r7d_fin_1234abcd",
    )
    assert diagnostic.classification == "PASS_QSTAT_SELF_RECORD_MATCHED"
    assert diagnostic.task_id_equality is True
    wrong_task, _fake_time, _calls = _diagnose(
        _xml(_job(name="lvef_c3_r8u_r7d_fin_1234abcd", tasks="17")),
        expected_task_id=None,
        expected_full_job_name="lvef_c3_r8u_r7d_fin_1234abcd",
    )
    assert wrong_task.classification == "BLOCKED_QSTAT_SELF_TASK_ID_CONTRADICTION"
    assert wrong_task.task_id_equality is False


def test_plain_width_truncated_qstat_table_is_never_accepted_as_name_authority() -> None:
    runner, _calls = _runner(b"8123456 0.55500 lvef_c3_r8 sealed-owner r\n")
    fake_time = FakeTime()
    _assert_error(
        "BLOCKED_QSTAT_SELF_XML_INVALID",
        lambda: scheduler.diagnose_r8u_r7d_qstat_self(
            environment=ENVIRONMENT,
            expected_job_id=JOB_ID,
            expected_task_id="17",
            expected_owner=OWNER,
            expected_full_job_name=JOB_NAME,
            runner=runner,
            clock=fake_time.clock,
            sleeper=fake_time.sleep,
        ),
    )


def test_failed_qstat_and_invalid_arguments_raise_safe_closed_codes() -> None:
    fake_time = FakeTime()

    def failed(
        command: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, b"", b"private failure")

    arguments = {
        "environment": ENVIRONMENT,
        "expected_job_id": JOB_ID,
        "expected_task_id": "17",
        "expected_owner": OWNER,
        "expected_full_job_name": JOB_NAME,
        "runner": failed,
        "clock": fake_time.clock,
        "sleeper": fake_time.sleep,
    }
    _assert_error(
        "BLOCKED_QSTAT_SELF_OBSERVATION_FAILED",
        lambda: scheduler.diagnose_r8u_r7d_qstat_self(**arguments),
    )
    _assert_error(
        "BLOCKED_QSTAT_SELF_OBSERVATION_FAILED",
        lambda: scheduler.diagnose_r8u_r7d_qstat_self(
            **{**arguments, "expected_task_id": "16"}
        ),
    )
