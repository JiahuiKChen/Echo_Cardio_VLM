#!/usr/bin/env python3
"""Focused dependency-light tests for the R8U-R7G quiescence observer."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterator
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7g_accounting as accounting
import lvef_c3_r8u_r7g_quiescence as quiescence


UID = 17001
ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": "/restricted/home/pkarim",
    "LC_ALL": "C",
    "LOGNAME": "pkarim",
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "SGE_ROOT": "/usr/local/ogs-ge2011.11.p1/sge_root",
    "SHELL": "/bin/bash",
    "USER": "pkarim",
}


def _authority() -> dict[str, Any]:
    return {
        "expected_owner": "pkarim",
        "array_job_id": "7480830",
        "array_job_name": "lvef_c3_r8u_r7d_seq_2223d976",
        "array_role": "R8U_R7D_CONTINUATION_ARRAY",
        "array_task_ids": [17, 18, 19],
        "array_task_range": "17-19",
        "array_task_count": 3,
        "array_max_concurrency": 1,
        "finalizer_job_id": "7480831",
        "finalizer_job_name": "lvef_c3_r8u_r7d_fin_2223d976",
        "finalizer_role": "R8U_R7D_COHORT_FINALIZER",
    }


def _specs() -> tuple[accounting.FixedAccountingSpec, ...]:
    root = Path("/fixed/r7g/accounting")
    log_root = Path("/fixed/r7d/scheduler")
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


@contextmanager
def _bound() -> Iterator[dict[str, Any]]:
    authority = _authority()
    with mock.patch.object(
        quiescence.accounting,
        "fixed_accounting_specs",
        return_value=_specs(),
    ):
        yield authority


def _scheduler_xml(*jobs: dict[str, str]) -> bytes:
    rows: list[str] = []
    for job in jobs:
        tasks = f"<tasks>{job['tasks']}</tasks>" if job.get("tasks") else ""
        rows.append(
            "<job_list state=\"{category}\">"
            "<JB_job_number>{job_id}</JB_job_number>"
            "<JB_name>{name}</JB_name>"
            "<JB_owner>{owner}</JB_owner>"
            "<state>{state}</state>{tasks}</job_list>".format(
                category=job.get("category", "running"),
                job_id=job["job_id"],
                name=job["name"],
                owner=job.get("owner", "pkarim"),
                state=job.get("state", "r"),
                tasks=tasks,
            )
        )
    return (
        "<job_info><queue_info>"
        + "".join(rows)
        + "</queue_info><job_info/></job_info>"
    ).encode("ascii")


def _completed(
    argv: list[str], stdout: bytes
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")


def _expect_code(function: Any, code: str) -> None:
    try:
        function()
    except quiescence.R7GQuiescenceError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def test_unrelated_owner_job_is_ignored_without_output_projection() -> None:
    payload = _scheduler_xml(
        {
            "job_id": "8000001",
            "name": "unrelated_owner_job",
            "state": "qw",
            "category": "pending",
        }
    )
    with _bound() as authority:
        value = quiescence.project_fixed_scheduler_snapshot(
            payload, terminal_authority=authority
        )
    assert value["matching_active_jobs"] == 0
    assert value["matching_job_records"] == []
    assert value["parsed_job_records"] == 1
    assert "unrelated_owner_job" not in repr(value)


def test_exact_array_finalizer_and_replacement_are_contradictions() -> None:
    jobs = (
        (quiescence.ARRAY_JOB_ID, quiescence.ARRAY_JOB_NAME, "17-19:1"),
        (quiescence.FINALIZER_JOB_ID, quiescence.FINALIZER_JOB_NAME, ""),
        ("9000001", "lvef_c3_r8u_r7d_seq_deadbeef", "17-19:1"),
        ("9000002", "lvef_c3_r8u_r7g_fin_deadbeef", ""),
    )
    with _bound() as authority:
        for job_id, name, tasks in jobs:
            scheduler = quiescence.project_fixed_scheduler_snapshot(
                _scheduler_xml(
                    {"job_id": job_id, "name": name, "tasks": tasks}
                ),
                terminal_authority=authority,
            )
            assert scheduler["matching_active_jobs"] == 1
            process = quiescence.project_fixed_process_snapshot(
                f"1 {UID} /sbin/init\n".encode("ascii"),
                effective_uid=UID,
            )
            _expect_code(
                lambda: quiescence.build_fixed_scheduler_state(
                    scheduler,
                    process,
                    terminal_authority=authority,
                ),
                "R8U_R7G_ACTIVE_EXECUTION_CONTRADICTION",
            )


def test_truncated_or_wrong_fixed_scheduler_identity_is_rejected() -> None:
    with _bound() as authority:
        for name in (
            "lvef_c3_r8",
            "lvef_c3_r8...",
            "lvef_c3_r8u_r7d_seq",
            "lvef_c3_r8u_r7d_seq_2223d97",
        ):
            _expect_code(
                lambda name=name: quiescence.project_fixed_scheduler_snapshot(
                    _scheduler_xml({"job_id": "9000003", "name": name}),
                    terminal_authority=authority,
                ),
                "R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS",
            )
        _expect_code(
            lambda: quiescence.project_fixed_scheduler_snapshot(
                _scheduler_xml(
                    {
                        "job_id": quiescence.ARRAY_JOB_ID,
                        "name": "unrelated",
                    }
                ),
                terminal_authority=authority,
            ),
            "R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS",
        )


def test_full_xml_rejects_dtd_duplicate_fields_and_wrong_relevant_owner() -> None:
    duplicate = (
        b"<job_info><job_list><JB_job_number>3</JB_job_number>"
        b"<JB_name>a</JB_name><JB_name>b</JB_name>"
        b"<JB_owner>pkarim</JB_owner><state>r</state>"
        b"</job_list></job_info>"
    )
    with _bound() as authority:
        for payload, code in (
            (
                b"<!DOCTYPE x><job_info/>",
                "R8U_R7G_SCHEDULER_SNAPSHOT_INVALID",
            ),
            (duplicate, "R8U_R7G_SCHEDULER_SNAPSHOT_INVALID"),
        ):
            _expect_code(
                lambda payload=payload: quiescence.project_fixed_scheduler_snapshot(
                    payload, terminal_authority=authority
                ),
                code,
            )
        _expect_code(
            lambda: quiescence.project_fixed_scheduler_snapshot(
                _scheduler_xml(
                    {
                        "job_id": quiescence.ARRAY_JOB_ID,
                        "name": quiescence.ARRAY_JOB_NAME,
                        "owner": "somebody_else",
                    }
                ),
                terminal_authority=authority,
            ),
            "R8U_R7G_SCHEDULER_IDENTITY_AMBIGUOUS",
        )


def test_process_projection_detects_workers_accounting_cohort_lock_and_qacct() -> None:
    commands = (
        "python controller.py --run-r8u-r7d-continuation-17-19-array-task",
        "python scripts/lvef_c3_r8u_r7g_accounting.py --fixed",
        "python worker.py --finalize-r8u-r7g-cohort-metadata-only",
        "python worker.py --review-r8u-r7g-post-reconstruction-lock",
        "/usr/local/sge/bin/qacct -j 7480830 -t 17",
    )
    payload = "".join(
        f"{100 + index} {UID} {command}\n"
        for index, command in enumerate(commands)
    ).encode("ascii")
    value = quiescence.project_fixed_process_snapshot(
        payload, effective_uid=UID
    )
    assert value["matching_active_processes"] == len(commands)
    assert len(value["matching_process_records"]) == len(commands)
    assert all(
        set(record) == {"command_sha256", "pid", "uid"}
        for record in value["matching_process_records"]
    )
    assert all(command not in repr(value) for command in commands)


def test_other_uid_relevant_process_and_unrelated_owner_process_are_ignored() -> None:
    payload = (
        f"10 {UID + 1} python controller.py "
        "--run-r8u-r7d-continuation-finalizer\n"
        f"11 {UID} /usr/bin/python unrelated.py\n"
    ).encode("ascii")
    value = quiescence.project_fixed_process_snapshot(
        payload, effective_uid=UID
    )
    assert value["parsed_process_records"] == 2
    assert value["owner_process_records"] == 1
    assert value["matching_active_processes"] == 0


def test_only_exact_current_observer_process_is_excluded() -> None:
    current_pid = os.getpid()
    observer = (
        f"python scripts/{quiescence.OBSERVER_SCRIPT_BASENAME} "
        f"{quiescence.OBSERVER_ENTRYPOINT_FLAG}"
    )
    payload = (
        f"{current_pid} {UID} {observer}\n"
        f"{current_pid + 1} {UID} {observer}\n"
        f"{current_pid + 2} {UID} python scripts/"
        f"{quiescence.OBSERVER_SCRIPT_BASENAME} --wrong-flag\n"
    ).encode("ascii")
    value = quiescence.project_fixed_process_snapshot(
        payload,
        effective_uid=UID,
        observer_pid=current_pid,
    )
    assert value["matching_active_processes"] == 2
    assert [item["pid"] for item in value["matching_process_records"]] == [
        current_pid + 1,
        current_pid + 2,
    ]
    _expect_code(
        lambda: quiescence.project_fixed_process_snapshot(
            payload,
            effective_uid=UID,
            observer_pid=current_pid + 1,
        ),
        "R8U_R7G_PROCESS_SNAPSHOT_INVALID",
    )


def test_capture_is_exactly_two_snapshots_and_excludes_its_observer() -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    current_pid = os.getpid()

    def scheduler_runner(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs["env"]))
        return _completed(argv, _scheduler_xml())

    def process_runner(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs["env"]))
        payload = (
            f"{current_pid} {UID} python scripts/"
            f"{quiescence.OBSERVER_SCRIPT_BASENAME} "
            f"{quiescence.OBSERVER_ENTRYPOINT_FLAG}\n"
            f"31 {UID} /sbin/init\n"
        ).encode("ascii")
        return _completed(argv, payload)

    with _bound() as authority, mock.patch.object(
        quiescence, "_default_effective_uid", return_value=UID
    ):
        evidence = quiescence.capture_fixed_quiescence(
            terminal_authority=authority,
            environment=ENVIRONMENT,
            scheduler_runner=scheduler_runner,
            process_runner=process_runner,
            tool_validator=lambda: None,
        )
    assert len(calls) == 2
    assert calls[0][0] == list(quiescence.SCHEDULER_COMMAND)
    assert calls[1][0] == list(quiescence.PROCESS_COMMAND)
    assert all(
        not quiescence.FORBIDDEN_WORKER_ENVIRONMENT_NAMES & set(environment)
        for _argv, environment in calls
    )
    assert evidence.scheduler_state == {
        "matching_active_jobs": 0,
        "matching_active_processes": 0,
        "qstat_projection_sha256": quiescence._canonical_json_sha256(
            evidence.scheduler_projection
        ),
        "process_projection_sha256": quiescence._canonical_json_sha256(
            evidence.process_projection
        ),
    }


def test_capture_rejects_worker_environment_and_query_stderr() -> None:
    def failed(
        argv: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=b"<job_info/>", stderr=b"unexpected"
        )

    with _bound() as authority, mock.patch.object(
        quiescence, "_default_effective_uid", return_value=UID
    ):
        _expect_code(
            lambda: quiescence.capture_fixed_quiescence(
                terminal_authority=authority,
                environment={**ENVIRONMENT, "JOB_ID": "7480830"},
                tool_validator=lambda: None,
            ),
            "R8U_R7G_QUIESCENCE_ENVIRONMENT_INVALID",
        )
        _expect_code(
            lambda: quiescence.capture_fixed_quiescence(
                terminal_authority=authority,
                environment=ENVIRONMENT,
                scheduler_runner=failed,
                tool_validator=lambda: None,
            ),
            "R8U_R7G_SCHEDULER_QUERY_FAILED",
        )


def test_source_has_no_scientific_execution_or_worker_environment_path() -> None:
    source = inspect.getsource(quiescence)
    assert "np.load" not in source
    assert "pydicom" not in source
    assert "run_production_dicom_extraction(" not in source
    assert "build_worker_scheduler_context" not in source
    assert 'SGE_TASK_ID"]' not in source
    signature = inspect.signature(quiescence.capture_fixed_quiescence)
    assert "job_id" not in signature.parameters
    assert "job_name" not in signature.parameters
    assert "effective_uid" not in signature.parameters


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        try:
            function()
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"SUMMARY passed={passed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
