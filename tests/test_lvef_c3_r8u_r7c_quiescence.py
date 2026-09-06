#!/usr/bin/env python3
"""Focused dependency-light tests for the R8U-R7C quiescence observer."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7c_metadata as metadata
import lvef_c3_r8u_r7c_quiescence as quiescence


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


def _scheduler_xml(*jobs: dict[str, str]) -> bytes:
    rows: list[str] = []
    for job in jobs:
        tasks = (
            f"<tasks>{job['tasks']}</tasks>" if job.get("tasks") else ""
        )
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
        "<job_info><queue_info>" + "".join(rows)
        + "</queue_info><job_info/></job_info>"
    ).encode("ascii")


def _completed(argv: list[str], stdout: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")


def _expect_code(function: Any, code: str) -> None:
    try:
        function()
    except quiescence.R7CQuiescenceError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_zero_matching_scheduler_projection_uses_full_xml_name() -> None:
    payload = _scheduler_xml(
        {
            "job_id": "8000001",
            "name": "unrelated_owner_job",
            "state": "qw",
            "category": "pending",
        }
    )
    value = quiescence.project_fixed_scheduler_snapshot(payload)
    assert value["matching_active_jobs"] == 0
    assert value["matching_job_records"] == []
    assert value["query_stdout_sha256"]
    assert value["parsed_job_records"] == 1


def test_exact_array_or_finalizer_job_is_active_contradiction() -> None:
    for job_id, name in (
        (quiescence.ARRAY_JOB_ID, quiescence.ARRAY_JOB_NAME),
        (quiescence.FINALIZER_JOB_ID, quiescence.FINALIZER_JOB_NAME),
    ):
        projected = quiescence.project_fixed_scheduler_snapshot(
            _scheduler_xml(
                {
                    "job_id": job_id,
                    "name": name,
                    "tasks": "17" if job_id == quiescence.ARRAY_JOB_ID else "",
                }
            )
        )
        assert projected["matching_active_jobs"] == 1
        process = quiescence.project_fixed_process_snapshot(
            f"1 {UID} /sbin/init\n".encode("ascii"), effective_uid=UID
        )
        _expect_code(
            lambda: quiescence.build_fixed_scheduler_state(projected, process),
            "R8U_R7C_ACTIVE_EXECUTION_CONTRADICTION",
        )


def test_later_replacement_job_family_is_also_active() -> None:
    value = quiescence.project_fixed_scheduler_snapshot(
        _scheduler_xml(
            {
                "job_id": "9000001",
                "name": "lvef_c3_r8u_r7_seq_deadbeef",
                "tasks": "17-19:1",
            }
        )
    )
    assert value["matching_active_jobs"] == 1
    assert value["target_array_job_present"] is False


def test_truncated_or_wrong_fixed_scheduler_identity_is_rejected() -> None:
    for name in (
        "lvef_c3_r8",
        "lvef_c3_r8...",
        "lvef_c3_r8u_r7_seq",
        "lvef_c3_r8u_r7_seq_1be99c6",
    ):
        _expect_code(
            lambda name=name: quiescence.project_fixed_scheduler_snapshot(
                _scheduler_xml({"job_id": "9000002", "name": name})
            ),
            "R8U_R7C_SCHEDULER_IDENTITY_AMBIGUOUS",
        )
    _expect_code(
        lambda: quiescence.project_fixed_scheduler_snapshot(
            _scheduler_xml(
                {"job_id": quiescence.ARRAY_JOB_ID, "name": "unrelated"}
            )
        ),
        "R8U_R7C_SCHEDULER_IDENTITY_AMBIGUOUS",
    )


def test_scheduler_snapshot_rejects_dtd_and_duplicate_name() -> None:
    _expect_code(
        lambda: quiescence.project_fixed_scheduler_snapshot(
            b"<!DOCTYPE x><job_info/>"
        ),
        "R8U_R7C_SCHEDULER_SNAPSHOT_INVALID",
    )
    duplicate = (
        b"<job_info><job_list><JB_job_number>3</JB_job_number>"
        b"<JB_name>a</JB_name><JB_name>b</JB_name><state>r</state>"
        b"</job_list></job_info>"
    )
    _expect_code(
        lambda: quiescence.project_fixed_scheduler_snapshot(duplicate),
        "R8U_R7C_SCHEDULER_SNAPSHOT_INVALID",
    )


def test_process_projection_filters_numeric_owner_and_hashes_commands() -> None:
    payload = (
        f"10 {UID + 1} python --run-r8u-r7-continuation-finalizer\n"
        f"11 {UID} /usr/bin/python unrelated.py\n"
        f"12 {UID} /usr/bin/python scripts/lvef_c3_r8u_r7c_terminal_adjudicator.py "
        "--adjudicate-fixed-r8u-r7-existing-jobs\n"
    ).encode("ascii")
    value = quiescence.project_fixed_process_snapshot(payload, effective_uid=UID)
    assert value["parsed_process_records"] == 3
    assert value["owner_process_records"] == 2
    assert value["matching_active_processes"] == 0
    assert value["matching_process_records"] == []


def test_matching_worker_process_is_active_contradiction() -> None:
    process = quiescence.project_fixed_process_snapshot(
        (
            f"21 {UID} /usr/bin/python controller.py "
            "--run-r8u-r7-continuation-17-19-array-task\n"
        ).encode("ascii"),
        effective_uid=UID,
    )
    assert process["matching_active_processes"] == 1
    assert set(process["matching_process_records"][0]) == {
        "command_sha256",
        "pid",
        "uid",
    }
    scheduler = quiescence.project_fixed_scheduler_snapshot(_scheduler_xml())
    _expect_code(
        lambda: quiescence.build_fixed_scheduler_state(scheduler, process),
        "R8U_R7C_ACTIVE_EXECUTION_CONTRADICTION",
    )


def test_capture_is_two_metadata_snapshots_and_needs_no_worker_environment() -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def scheduler_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs["env"]))
        return _completed(argv, _scheduler_xml())

    def process_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs["env"]))
        return _completed(argv, f"31 {UID} /sbin/init\n".encode("ascii"))

    evidence = quiescence.capture_fixed_quiescence(
        environment=ENVIRONMENT,
        scheduler_runner=scheduler_runner,
        process_runner=process_runner,
        tool_validator=lambda: None,
        effective_uid=UID,
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
        "qstat_projection_sha256": metadata.canonical_json_sha256(
            evidence.scheduler_projection
        ),
        "process_projection_sha256": metadata.canonical_json_sha256(
            evidence.process_projection
        ),
    }
    assert metadata._validate_scheduler_state(evidence.scheduler_state) == (
        evidence.scheduler_state
    )


def test_capture_rejects_worker_environment_and_query_failure() -> None:
    _expect_code(
        lambda: quiescence.capture_fixed_quiescence(
            environment={**ENVIRONMENT, "JOB_ID": "7478863"},
            tool_validator=lambda: None,
            effective_uid=UID,
        ),
        "R8U_R7C_QUIESCENCE_ENVIRONMENT_INVALID",
    )

    def failed(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"failed")

    _expect_code(
        lambda: quiescence.capture_fixed_quiescence(
            environment=ENVIRONMENT,
            scheduler_runner=failed,
            tool_validator=lambda: None,
            effective_uid=UID,
        ),
        "R8U_R7C_SCHEDULER_QUERY_FAILED",
    )


def test_source_has_no_scientific_body_or_worker_validator_path() -> None:
    source = inspect.getsource(quiescence)
    assert "np.load" not in source
    assert "pydicom" not in source
    assert "_r8u_r7_qstat_projection" not in source
    assert "build_worker_scheduler_context" not in source
    assert "SGE_TASK_ID\"]" not in source


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
