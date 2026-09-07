#!/usr/bin/env python3
"""Dependency-light contracts for the isolated R7H continuation controller."""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import traceback
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_full_scheduler as scheduler
import lvef_c3_orchestration_core as core
import lvef_c3_r8u_r7d_capacity as capacity
import lvef_c3_r8u_r7h_continuation as r7h


IMPLEMENTATION_COMMIT = "f" * 40
SHA = "a" * 64


def _raises(code: str, function: Callable[[], Any]) -> r7h.R7HContinuationError:
    try:
        function()
    except r7h.R7HContinuationError as exc:
        assert exc.code == code
        assert "BLOCKED_BLOCKED" not in exc.code
        return exc
    raise AssertionError(f"expected {code}")


def _account() -> dict[str, Any]:
    return {
        "qsub_environment_sha256": SHA,
        "expected_scheduler_username": "owner",
        "expected_effective_uid": os.geteuid(),
        "canonical_home": "/home/owner",
        "sealed_qsub_environment": {},
        "script_authority": {"runner_sha256": SHA},
        "python_sha256": SHA,
    }


def _qstat_projection(expected_job_count: int) -> dict[str, Any]:
    return {
        "status": "PASS_R7H_RELEVANT_QSTAT_SNAPSHOT",
        "expected_job_count": expected_job_count,
        "target_rows_visible": expected_job_count,
        "competing_relevant_jobs": 0,
        "job_id_matches": True,
        "job_name_matches": True,
        "owner_matches": True,
        "qstat_snapshot_count": 1,
        "qstat_argv_sha256": "1" * 64,
        "qstat_stdout_sha256": "2" * 64,
        "truncated_display_name_used": False,
    }


def _process_projection() -> dict[str, Any]:
    return {
        "status": "PASS_ZERO_R7H_RELEVANT_PROCESSES",
        "matching_processes": 0,
        "process_snapshot_count": 1,
        "ps_argv_sha256": "3" * 64,
        "ps_stdout_sha256": "4" * 64,
    }


def _references(expected_job_count: int) -> dict[str, Any]:
    return {
        "status": "PASS_R7H_ZERO_COMPETING_LIVE_REFERENCES",
        "active_job_references": 0,
        "active_process_references": 0,
        "qstat_projection": _qstat_projection(expected_job_count),
        "process_projection": _process_projection(),
    }


def _worker_context() -> scheduler.WorkerSchedulerContext:
    values: dict[str, Any] = {}
    for field in scheduler.WorkerSchedulerDiagnostics._fields:
        if field == "passwd_lookup_status":
            values[field] = "PASS"
        elif field == "classifications":
            values[field] = ("PASS",)
        else:
            values[field] = True
    return scheduler.WorkerSchedulerContext(
        environment={}, diagnostics=scheduler.WorkerSchedulerDiagnostics(**values)
    )


def _qstat_diagnostic() -> scheduler.R8UR7DQstatDiagnostic:
    observation = scheduler.R8UR7DQstatObservation(
        observation_ordinal=1,
        record_present=False,
        unique=True,
        job_id_match=None,
        task_id_match=None,
        owner_match=None,
        full_job_name_match=None,
        state_token=None,
    )
    return scheduler.R8UR7DQstatDiagnostic(
        classification="PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE",
        observation_count=1,
        row_ever_visible=False,
        job_id_equality=True,
        task_id_equality=True,
        owner_equality=True,
        full_job_name_equality=True,
        observed_scheduler_state_category="NOT_OBSERVED",
        observations=(observation,),
    )


def test_exact_qsub_topology_and_isolated_roots() -> None:
    probe = r7h._probe_qsub_command(IMPLEMENTATION_COMMIT)
    array = r7h._array_qsub_command(IMPLEMENTATION_COMMIT)
    finalizer = r7h._finalizer_qsub_command(IMPLEMENTATION_COMMIT, "123")
    assert probe[probe.index("-t") + 1] == "17"
    assert probe[probe.index("-tc") + 1] == "1"
    assert "gpus=1" not in probe
    assert str(r7h.PROBE_SCHEDULER_ROOT) in probe
    assert array[array.index("-t") + 1] == "17-19"
    assert array[array.index("-tc") + 1] == "1"
    assert "gpus=1" in array and "gpu_c=8.0" in array
    assert "gpu_memory=48G" in array
    assert str(r7h.CONTINUATION_SCHEDULER_ROOT) in array
    assert finalizer[finalizer.index("-hold_jid") + 1] == "123"
    assert "gpus=1" not in finalizer
    assert str(r7h.PROBE_SCHEDULER_ROOT) not in array + finalizer


def test_qsub_parsers_accept_only_fixed_shapes() -> None:
    assert r7h._parse_probe_qsub_stdout(b"123.17-17:1\n") == "123"
    assert r7h._parse_array_qsub_stdout(b"456.17-19:1\n") == "456"
    _raises(
        "R7H_PROBE_QSUB_OUTPUT_INVALID",
        lambda: r7h._parse_probe_qsub_stdout(b"123.18\n"),
    )
    _raises(
        "R7H_ARRAY_QSUB_OUTPUT_INVALID",
        lambda: r7h._parse_array_qsub_stdout(b"456.1-19:1\n"),
    )


def test_capacity_deficit_is_quantified_and_typed() -> None:
    producer = {
        "status": capacity.R8U_R7F_CAPACITY_STATUS_DEFICIT,
        "capacity_projection": {
            "quota_reserve_deficit_bytes": 11,
            "physical_reserve_deficit_bytes": 22,
            "file_slot_deficit": 33,
        },
    }
    exc = _raises(
        "BLOCKED_R7H_QUANTIFIED_CAPACITY_DEFICIT",
        lambda: r7h._raise_for_capacity_producer_status(producer),
    )
    assert exc.capacity_deficits == {
        "quota_deficit_bytes": 11,
        "physical_deficit_bytes": 22,
        "file_slot_deficit": 33,
    }
    bad = {
        **producer,
        "capacity_projection": {
            **producer["capacity_projection"],
            "file_slot_deficit": True,
        },
    }
    _raises(
        "R7H_CAPACITY_OBSERVATION_PRODUCER_INVALID",
        lambda: r7h._raise_for_capacity_producer_status(bad),
    )


def test_capacity_observation_code_is_not_double_blocked() -> None:
    producer = {
        "status": "BLOCKED_R8U_R7F_CAPACITY_OBSERVATION_DF_PARSE_INVALID"
    }
    _raises(
        "BLOCKED_R7H_CAPACITY_OBSERVATION_DF_PARSE_INVALID",
        lambda: r7h._raise_for_capacity_producer_status(producer),
    )


def test_role_specific_json_serializers_reject_arbitrary_formatting() -> None:
    value = {"alpha": 1, "beta": [2]}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "receipt.json"
        path.write_bytes(core.canonical_json_bytes(value))
        path.chmod(0o600)
        assert r7h._read_private_json(path, code="R7H_TEST")[0] == value
        _raises(
            "R7H_TEST",
            lambda: r7h._read_indented_private_json(path, code="R7H_TEST"),
        )
        path.write_bytes(
            (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        )
        path.chmod(0o600)
        assert r7h._read_indented_private_json(
            path, code="R7H_TEST"
        )[0] == value
        _raises(
            "R7H_TEST",
            lambda: r7h._read_private_json(path, code="R7H_TEST"),
        )


def test_live_reference_projection_is_closed_and_nonzero_fails() -> None:
    assert r7h._validate_live_reference_projection(
        _references(1), expected_job_count=1
    )["active_job_references"] == 0
    for field in ("active_job_references", "active_process_references"):
        changed = _references(1)
        changed[field] = 1
        _raises(
            "R7H_LIVE_REFERENCE_PROJECTION_INVALID",
            lambda changed=changed: r7h._validate_live_reference_projection(
                changed, expected_job_count=1
            ),
        )
    changed = _references(1)
    changed["unexpected"] = 0
    _raises(
        "R7H_LIVE_REFERENCE_PROJECTION_INVALID",
        lambda: r7h._validate_live_reference_projection(
            changed, expected_job_count=1
        ),
    )


def test_worker_receipt_has_closed_schema_and_typed_identity() -> None:
    with mock.patch.object(
        r7h,
        "_current_r8u_r7h_implementation_commit",
        return_value=IMPLEMENTATION_COMMIT,
    ):
        receipt = r7h._worker_receipt(
            implementation_commit=IMPLEMENTATION_COMMIT,
            role="probe",
            logical_role=r7h.PROBE_ROLE,
            expected_job_id="123",
            expected_task_id="17",
            expected_job_name=r7h._probe_job_name(IMPLEMENTATION_COMMIT),
            account_sha256="1" * 64,
            authority_sha256="2" * 64,
            submission_sha256="3" * 64,
            context=_worker_context(),
            diagnostic=_qstat_diagnostic(),
        )
        r7h._validate_worker_receipt(
            receipt,
            role="probe",
            logical_role=r7h.PROBE_ROLE,
            expected_job_id="123",
            expected_task_id="17",
            expected_job_name=r7h._probe_job_name(IMPLEMENTATION_COMMIT),
            account_sha256="1" * 64,
            authority_sha256="2" * 64,
            submission_sha256="3" * 64,
        )
        changed = {**receipt, "task_id": True}
        _raises(
            "R7H_WORKER_CONTEXT_RECEIPT_INVALID",
            lambda: r7h._validate_worker_receipt(
                changed,
                role="probe",
                logical_role=r7h.PROBE_ROLE,
                expected_job_id="123",
                expected_task_id="17",
                expected_job_name=r7h._probe_job_name(IMPLEMENTATION_COMMIT),
                account_sha256="1" * 64,
                authority_sha256="2" * 64,
                submission_sha256="3" * 64,
            ),
        )


def test_probe_diagnostic_authorizes_no_science() -> None:
    topology = {"status": r7h.TOPOLOGY_PASS, "closed": True}
    with (
        mock.patch.object(
            r7h,
            "_current_r8u_r7h_implementation_commit",
            return_value=IMPLEMENTATION_COMMIT,
        ),
        mock.patch.object(r7h.core, "sha256_file", return_value=SHA),
    ):
        value = r7h._probe_topology_diagnostic(
            implementation_commit=IMPLEMENTATION_COMMIT,
            probe_job_id="123",
            topology=topology,
            live_references=_references(1),
        )
        assert value["status"] == r7h.PROBE_PASS
        for field in (
            "scientific_artifacts_created",
            "cloud_requests",
            "dicom_body_reads",
            "npz_body_reads",
            "gpu_executions",
            "extraction_executions",
            "echoprime_executions",
            "embedding_generations",
            "preservation_executions",
            "finalization_executions",
        ):
            assert type(value[field]) is int and value[field] == 0


def test_probe_entrypoint_cannot_reach_scientific_runner() -> None:
    source = inspect.getsource(r7h.run_r8u_r7h_continuation_context_probe)
    assert "run_batch_task" not in source
    assert "finalize_receipts" not in source
    assert "validate_r8u_r7h_extraction_cache_topology" in source
    assert "_live_reference_projection" in source


def test_array_receipt_is_published_before_finalizer_qsub() -> None:
    source = inspect.getsource(r7h.submit_r8u_r7h_continuation_17_19)
    array_publication = source.index(
        "array_sha = _write_private_json(\n        ARRAY_SUBMISSION_PATH"
    )
    finalizer_submission = source.index(
        "finalizer_job_id = scheduler._capture_qsub"
    )
    assert array_publication < finalizer_submission
    assert source.count("scheduler._capture_qsub") == 2


def test_consumed_evidence_reuse_recomputes_artifact_aggregates() -> None:
    source = inspect.getsource(r7h._validate_consumed_evidence)
    assert "_derive_consumed_r7f_evidence" in source
    assert "capture_missing=False" in source
    assert "not _exact(value, replay)" in source
    assert "replay_tail_artifacts" in source
    derive = inspect.getsource(r7h._derive_consumed_r7f_evidence)
    assert "_consumed_tail_artifact_snapshot" in derive
    assert '"preserved_scientific_artifact_files"' in derive
    assert '"preserved_scientific_artifact_bytes"' in derive


def test_finalizer_authority_payload_matches_shared_contract() -> None:
    expected_fields = {
        field.name
        for field in __import__("dataclasses").fields(
            r7h.finalizer.R8UR7HImplementationAuthority
        )
    }
    source = inspect.getsource(r7h._r7h_finalizer_authority)
    for field in expected_fields:
        assert f"{field}=" in source
    assert 'CONSUMED_R7F_AUTHORITY_SHA256["combined_submission"]' in source
    run_source = inspect.getsource(r7h.run_r8u_r7h_continuation_finalizer)
    assert "r8u_r7h_implementation_authority=authority" in run_source
    assert 'summary.get("implementation_authority_epoch_count") != 4' in run_source


def test_controller_isolated_but_aliases_live_recovery_identity() -> None:
    source = inspect.getsource(r7h._recovery_controller_module)
    assert 'sys.modules.get("__main__")' in source
    assert "import_module" in source
    module_source = inspect.getsource(r7h)
    assert "from lvef_c3_r8r_recovery_continuation import" not in module_source
    assert "R8U_R7D_PROBE_SCHEDULER_ROOT" not in inspect.getsource(
        r7h._probe_qsub_command
    )


def test_worker_bounded_waits_for_fast_submission_receipts() -> None:
    source = inspect.getsource(
        r7h.validate_r8u_r7h_continuation_worker_submission
    )
    assert "_wait_for_private_control" in source
    assert "PROBE_SUBMISSION_PATH" in source
    assert "CONTINUATION_SUBMISSION_PATH" in source
    ticks = iter((0.0, 61.0))
    _raises(
        "R7H_TEST_RECEIPT_TIMEOUT",
        lambda: r7h._wait_for_private_control(
            Path("/definitely/absent/r7h.receipt"),
            code="R7H_TEST_RECEIPT_TIMEOUT",
            timeout_seconds=60,
            sleeper=lambda _seconds: None,
            monotonic_clock=lambda: next(ticks),
        ),
    )


def test_scheduler_account_replay_never_calls_nss() -> None:
    environment = {"USER": "owner", "LOGNAME": "owner", "HOME": "/home/owner"}
    with (
        mock.patch.object(
            r7h,
            "_current_r8u_r7h_implementation_commit",
            return_value=IMPLEMENTATION_COMMIT,
        ),
        mock.patch.object(
            r7h.stages,
            "resolved_python_executable_sha256",
            return_value="1" * 64,
        ),
        mock.patch.object(
            r7h.scheduler,
            "qsub_environment_sha256",
            return_value="2" * 64,
        ),
        mock.patch.object(r7h, "_script_authority", return_value={"x": "3" * 64}),
        mock.patch.object(
            r7h.pwd,
            "getpwuid",
            side_effect=AssertionError("NSS reached during pure replay"),
        ),
    ):
        account = {
            **r7h._common(
                artifact_type="lvef_c3_r8u_r7h_scheduler_account_authority_v1",
                status="AUTHORIZED_R7H_SCHEDULER_ACCOUNT",
                implementation_commit=IMPLEMENTATION_COMMIT,
            ),
            "expected_effective_uid": os.geteuid(),
            "expected_scheduler_username": "owner",
            "canonical_home": "/home/owner",
            "python_sha256": "1" * 64,
            "qsub_environment_sha256": "2" * 64,
            "sealed_qsub_environment": environment,
            "authorized_worker_roles": list(r7h.WORKER_ROLES),
            "script_authority": {"x": "3" * 64},
        }
        assert r7h._validate_scheduler_account(account) == account


def test_qstat_split_array_rows_count_as_one_allowed_job() -> None:
    array_name = r7h._array_job_name(IMPLEMENTATION_COMMIT)
    finalizer_name = r7h._finalizer_job_name(IMPLEMENTATION_COMMIT)
    rows = (
        ("123", array_name, "r", "17"),
        ("123", array_name, "qw", "18-19:1"),
        ("124", finalizer_name, "hqw", None),
    )
    row_xml = "".join(
        "<job_list><JB_job_number>{}</JB_job_number><JB_name>{}</JB_name>"
        "<JB_owner>owner</JB_owner><state>{}</state>{}</job_list>".format(
            job_id,
            name,
            state,
            "" if tasks is None else f"<tasks>{tasks}</tasks>",
        )
        for job_id, name, state, tasks in rows
    )
    payload = (
        "<job_info><queue_info>"
        + row_xml
        + "</queue_info><job_info></job_info></job_info>"
    ).encode()
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=payload, stderr=b""
    )
    with mock.patch.object(
        r7h,
        "_current_r8u_r7h_implementation_commit",
        return_value=IMPLEMENTATION_COMMIT,
    ):
        projection = r7h._login_qstat_snapshot(
            environment={"USER": "owner"},
            runner=lambda *args, **kwargs: completed,
            expected={"array": "123", "finalizer": "124"},
        )
    assert projection["target_rows_visible"] == 2
    assert projection["competing_relevant_jobs"] == 0


def test_private_read_and_directory_reject_symlink_ancestor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        real = root / "real"
        real.mkdir(mode=0o700)
        control = real / "control.json"
        control.write_bytes(core.canonical_json_bytes({"ok": True}))
        control.chmod(0o600)
        link = root / "link"
        link.symlink_to(real, target_is_directory=True)
        _raises(
            "R7H_TEST_SYMLINK",
            lambda: r7h._read_private_bytes(
                link / "control.json", code="R7H_TEST_SYMLINK"
            ),
        )
        _raises(
            "R7H_TEST_SYMLINK",
            lambda: r7h._validate_private_directory(
                link, code="R7H_TEST_SYMLINK"
            ),
        )
    assert "_require_nonsymlink_components" in inspect.getsource(
        r7h._read_probe_log
    )


def test_private_write_rejects_symlink_ancestor_before_creation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        real = root / "real"
        real.mkdir(mode=0o700)
        link = root / "link"
        link.symlink_to(real, target_is_directory=True)
        target = link / "control.json"
        _raises(
            "R7H_TEST_WRITE_SYMLINK",
            lambda: r7h._write_private_json(
                target, {"ok": True}, code="R7H_TEST_WRITE_SYMLINK"
            ),
        )
        assert not os.path.lexists(real / "control.json")


def test_blocking_worker_qstat_is_rejected_before_pass_publication() -> None:
    source = inspect.getsource(
        r7h.validate_r8u_r7h_continuation_worker_submission
    )
    blocking_check = source.index(
        "if diagnostic.classification in "
        "scheduler.R8U_R7D_QSTAT_BLOCKING_CLASSIFICATIONS"
    )
    receipt_construction = source.index("receipt = _worker_receipt(")
    receipt_publication = source.index("_write_private_json(\n        path, receipt")
    assert blocking_check < receipt_construction < receipt_publication


def test_consumed_tail_tree_scan_is_bounded_mode_safe_and_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve() / "tree"
        root.mkdir(mode=0o700)
        artifact = root / "artifact.bin"
        artifact.write_bytes(b"metadata-size-only")
        artifact.chmod(0o600)
        assert r7h._tree_metadata_counts(root) == {
            "files": 1,
            "directories": 1,
            "bytes": len(b"metadata-size-only"),
            "anomalies": 0,
        }
        artifact.chmod(0o644)
        assert r7h._tree_metadata_counts(root)["anomalies"] == 1
        with mock.patch.object(r7h, "MAX_TOPOLOGY_ENTRIES", 1):
            _raises(
                "R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID",
                lambda: r7h._tree_metadata_counts(root),
            )
        with mock.patch.object(
            r7h.os, "scandir", side_effect=OSError("unreadable tree")
        ):
            _raises(
                "R7H_CONSUMED_TAIL_ARTIFACT_METADATA_INVALID",
                lambda: r7h._tree_metadata_counts(root),
            )


def test_pre_array_collision_set_covers_worker_and_finalizer_outputs() -> None:
    assert set(r7h.PRE_ARRAY_COLLISION_PATHS) == {
        r7h.CONTINUATION_CLAIM_PATH,
        r7h.CONTINUATION_SCHEDULER_ROOT,
        r7h.WORKER_CONTEXT_ROOT,
        r7h.FINALIZER_AUTHORITY_PATH,
        r7h.FINALIZER_AGGREGATE_PATH,
    }
    source = inspect.getsource(r7h.submit_r8u_r7h_continuation_17_19)
    assert "for path in PRE_ARRAY_COLLISION_PATHS" in source
    assert set(r7h.PRE_PROBE_COLLISION_PATHS) == {
        r7h.PROBE_ROOT,
        *r7h.PRE_ARRAY_COLLISION_PATHS,
    }
    probe_source = inspect.getsource(
        r7h.submit_r8u_r7h_continuation_topology_probe
    )
    assert "for path in PRE_PROBE_COLLISION_PATHS" in probe_source


def test_capacity_topology_authority_binds_observed_live_references() -> None:
    topology = {
        "status": r7h.TOPOLOGY_PASS,
        "active_job_references": 0,
        "active_process_references": 0,
    }
    references = _references(0)
    value = r7h._topology_authority(
        implementation_commit=IMPLEMENTATION_COMMIT,
        topology=topology,
        sealed_history={"batch16_failed_partial_seal_sha256": "1" * 64},
        consumed_evidence_sha256="2" * 64,
        live_references=references,
    )
    assert value["live_reference_projection"] == references
    assert value["active_job_references"] == references[
        "active_job_references"
    ]
    source = inspect.getsource(r7h.capture_r8u_r7h_capacity)
    assert "live_references = _live_reference_projection(" in source
    assert "active_job_references=live_references" in source
    assert "active_process_references=live_references" in source


def test_cli_pass_rendering_requires_typed_r7h_result() -> None:
    value = {
        "status": r7h.SUBMISSION_PASS,
        "task_range": "17-19",
        "array_max_concurrency": 1,
        "array_job_id": "123",
        "receipt_sha256": "1" * 64,
    }
    assert r7h.historical._validate_r8u_r7h_cli_result(
        value,
        expected_status=r7h.SUBMISSION_PASS,
        exact_fields={"task_range": "17-19", "array_max_concurrency": 1},
        job_id_fields=("array_job_id",),
        sha256_fields=("receipt_sha256",),
    ) is value
    changed = {**value, "array_max_concurrency": True}
    try:
        r7h.historical._validate_r8u_r7h_cli_result(
            changed,
            expected_status=r7h.SUBMISSION_PASS,
            exact_fields={"array_max_concurrency": 1},
        )
    except r7h.historical.R8RControllerError as exc:
        assert exc.code == "R8U_R7H_CLI_RESULT_INVALID"
    else:
        raise AssertionError("typed CLI validation accepted bool as integer")


def test_cli_failure_is_single_blocked_and_reports_zero_control_effects() -> None:
    stream = io.StringIO()
    with (
        mock.patch.object(
            r7h,
            "capture_r8u_r7h_capacity",
            return_value={"status": "UNPROVED"},
        ),
        contextlib.redirect_stdout(stream),
    ):
        exit_status = r7h.historical.guarded_main(
            ["--capture-r8u-r7h-capacity"]
        )
    output = stream.getvalue()
    assert exit_status == 78
    assert "R8U_R7H_STATUS=BLOCKED_R8U_R7H_CLI_RESULT_INVALID\n" in output
    assert "BLOCKED_BLOCKED" not in output
    for marker in (
        "R8U_R7H_NEW_CLOUD_REQUESTS=0",
        "R8U_R7H_NEW_DICOM_BODY_READS=0",
        "R8U_R7H_NEW_NPZ_BODY_READS=0",
        "R8U_R7H_NEW_GPU_EXECUTIONS=0",
        "R8U_R7H_NEW_MODEL_FITTING=0",
        "R8U_R7H_NEW_PREDICTION_GENERATION=0",
        "R8U_R7H_CONFIRMATORY_PERFORMANCE_ACCESSED=NO",
    ):
        assert output.count(marker) == 1
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        r7h.historical._emit_r8u_r7h_zero_effect_markers(
            ["--submit-r8u-r7h-continuation-17-19"]
        )
    assert stream.getvalue() == ""


def test_public_recovery_apis_are_callable() -> None:
    names = (
        "capture_r8u_r7h_capacity",
        "submit_r8u_r7h_continuation_topology_probe",
        "run_r8u_r7h_continuation_context_probe",
        "adjudicate_r8u_r7h_continuation_topology_probe",
        "submit_r8u_r7h_continuation_17_19",
        "run_r8u_r7h_continuation_array_task",
        "run_r8u_r7h_continuation_finalizer",
        "validate_r8u_r7h_continuation_worker_submission",
    )
    assert all(callable(getattr(r7h, name, None)) for name in names)


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
