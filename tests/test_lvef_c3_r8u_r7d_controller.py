#!/usr/bin/env python3
"""Dependency-light proofs for the fixed R8U-R7D controller topology."""
from __future__ import annotations

from contextlib import ExitStack
import inspect
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_full_scheduler as scheduler
import lvef_c3_r8r_recovery_continuation as controller


COMMIT = "f" * 40


def test_probe_is_one_cpu_array_task_with_no_gpu_request() -> None:
    command = controller._r8u_r7d_probe_command(COMMIT)
    assert command[command.index("-t") + 1] == "17"
    assert command[command.index("-tc") + 1] == "1"
    assert command[command.index("-pe") + 1 :] == [
        "omp", "1", "-l", "mem_per_core=1G", str(controller.RUNNER_PATH)
    ]
    assert "gpus=1" not in command
    assert "gpu_c=8.0" not in command
    assert "gpu_memory=48G" not in command
    assert "h_rt=00:10:00" in command


def test_probe_qsub_parser_accepts_only_fixed_task17_shapes() -> None:
    for payload in (b"12345\n", b"12345.17\n", b"12345.17-17:1\n"):
        assert controller._parse_r8u_r7d_probe_qsub_stdout(payload) == "12345"
    for payload in (b"12345.18\n", b"12345.17-19:1\n", b"job 12345\n"):
        try:
            controller._parse_r8u_r7d_probe_qsub_stdout(payload)
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7D_PROBE_QSUB_OUTPUT_INVALID"
        else:
            raise AssertionError("invalid probe qsub output accepted")


def test_scientific_array_is_exact_tasks17_19_tc1_gpu() -> None:
    command = controller._r8u_r7d_array_command(COMMIT)
    assert command[command.index("-t") + 1] == "17-19"
    assert command[command.index("-tc") + 1] == "1"
    assert "gpus=1" in command
    assert "gpu_c=8.0" in command
    assert "gpu_memory=48G" in command
    assert "h_rt=48:00:00" in command


def test_finalizer_is_cpu_only_and_exactly_held_on_new_array() -> None:
    command = controller._r8u_r7d_finalizer_command(COMMIT, "8123456")
    assert command[command.index("-hold_jid") + 1] == "8123456"
    assert "-t" not in command
    assert "gpus=1" not in command
    assert "gpu_c=8.0" not in command
    assert "gpu_memory=48G" not in command


def test_r7d_namespace_is_fresh_and_does_not_alias_consumed_r7a() -> None:
    assert controller.R8U_R7D_ROOT != controller.R8U_R7_CONTINUATION_ROOT
    assert not controller.R8U_R7D_ROOT.is_relative_to(
        controller.R8U_R7_CONTINUATION_ROOT
    )
    assert controller.R8U_R7D_OLD_CONTINUATION_RECEIPT_SHA256 == (
        "9ccc876aa4de905ba6a6a69129a21fc1cdb5f6a2d83cc91f7f49fefd8c33a000"
    )


def test_worker_receipts_are_per_fixed_role_and_task() -> None:
    paths = {
        controller._r8u_r7d_worker_receipt_path(
            expected_role=controller.R8U_R7D_PROBE_ROLE,
            expected_task_id="17",
        ),
        *(
            controller._r8u_r7d_worker_receipt_path(
                expected_role=controller.R8U_R7D_ARRAY_ROLE,
                expected_task_id=str(task),
            )
            for task in (17, 18, 19)
        ),
        controller._r8u_r7d_worker_receipt_path(
            expected_role=controller.R8U_R7D_FINALIZER_ROLE,
            expected_task_id=None,
        ),
    }
    assert len(paths) == 5
    assert all(path.is_relative_to(controller.R8U_R7D_ROOT) for path in paths)


def test_live_validator_builds_controlling_context_before_qstat() -> None:
    source = inspect.getsource(
        controller.validate_r8u_r7d_continuation_worker_submission
    )
    assert source.index("scheduler.build_worker_scheduler_context(") < source.index(
        "scheduler.diagnose_r8u_r7d_qstat_self("
    )
    assert "observed_job_role=observed_role" in source
    assert "expected_job_role=expected_role" in source
    assert "observed_job_role=expected_role" not in source


def test_qstat_is_diagnostic_and_never_requires_literal_r() -> None:
    source = inspect.getsource(
        controller.validate_r8u_r7d_continuation_worker_submission
    )
    assert "diagnose_r8u_r7d_qstat_self" in source
    assert "state == \"r\"" not in source
    qstat_tail = source.split("scheduler.diagnose_r8u_r7d_qstat_self(", 1)[1]
    assert "SCHEDULER_JOB_ROLE_MISMATCH" not in qstat_tail
    assert scheduler.R8U_R7D_QSTAT_MAXIMUM_OBSERVATIONS == 3
    assert scheduler.R8U_R7D_QSTAT_MAXIMUM_ELAPSED_SECONDS == 15.0


def test_login_qstat_requires_the_full_xml_envelope() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"<garbage/>", stderr=b""
    )
    try:
        controller._r8u_r7d_login_qstat_snapshot(
            environment={"USER": "owner"},
            runner=lambda *args, **kwargs: completed,
        )
    except controller.R8RControllerError as exc:
        assert exc.code == "R8U_R7D_QSTAT_SNAPSHOT_INVALID"
    else:
        raise AssertionError("non-qstat XML envelope was accepted")


def test_login_process_projection_excludes_only_the_submitter_itself() -> None:
    payload = (
        f"{os.getpid()} {os.geteuid()} python "
        f"{Path(controller.__file__).name} "
        "--submit-r8u-r7d-continuation-context-probe\n"
    ).encode("utf-8")
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=payload, stderr=b""
    )
    value = controller._r8u_r7d_process_quiescence(
        environment={}, runner=lambda *args, **kwargs: completed
    )
    assert value["matching_processes"] == 0


def test_login_process_projection_blocks_every_other_r7d_process() -> None:
    r7d_modes = (
        "--submit-r8u-r7d-continuation-context-probe",
        "--run-r8u-r7d-continuation-context-probe",
        "--adjudicate-r8u-r7d-continuation-context-probe",
        "--submit-r8u-r7d-continuation-17-19",
        "--run-r8u-r7d-continuation-17-19-array-task",
        "--run-r8u-r7d-continuation-finalizer",
    )
    for offset, mode in enumerate(r7d_modes, start=1):
        payload = (
            f"{os.getpid()} {os.geteuid()} python "
            f"{Path(controller.__file__).name} "
            "--submit-r8u-r7d-continuation-context-probe\n"
            f"{os.getpid() + offset} {os.geteuid()} python "
            f"{Path(controller.__file__).name} {mode}\n"
        ).encode("utf-8")
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=payload, stderr=b""
        )
        try:
            controller._r8u_r7d_process_quiescence(
                environment={}, runner=lambda *args, **kwargs: completed
            )
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R5_WORKER_PROCESS_PROJECTION_INVALID"
        else:
            raise AssertionError(f"competing R7D process was accepted: {mode}")


def test_capacity_failure_surfaces_every_exact_deficit() -> None:
    try:
        controller._r8u_r7d_fail_capacity_insufficient({
            "quota_reserve_deficit_bytes": 11,
            "physical_reserve_deficit_bytes": 22,
            "file_slot_deficit": 33,
        })
    except controller.R8RControllerError as exc:
        assert exc.code == "R8U_R7D_CAPACITY_INSUFFICIENT"
        assert exc.capacity_deficits == {
            "quota_deficit_bytes": 11,
            "physical_deficit_bytes": 22,
            "file_slot_deficit": 33,
        }
    else:
        raise AssertionError("insufficient capacity was accepted")


def test_probe_entrypoint_has_no_scientific_execution_call() -> None:
    source = inspect.getsource(controller.run_r8u_r7d_continuation_context_probe)
    forbidden = (
        "run_batch_task", "execute_exact_batch_download",
        "run_production_dicom_extraction", "run_production_echoprime",
        "preserve_batch", "finalize_receipts", "torch", "google",
    )
    assert all(token not in source for token in forbidden)
    assert "validate_r8u_r7d_continuation_worker_submission" in source


def test_array_entrypoint_is_closed_to_tasks17_19() -> None:
    source = inspect.getsource(controller.run_r8u_r7d_continuation_array_task)
    assert 'task_text not in {"17", "18", "19"}' in source
    assert "R8U_R7D_FIXED_CONTINUATION" in source


def test_finalizer_consumes_new_r7d_authority_and_all_19_receipts() -> None:
    source = inspect.getsource(controller.run_r8u_r7d_continuation_finalizer)
    assert "R8UR7DImplementationAuthority" in source
    assert "R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256" in source
    assert "r8u_r7d_implementation_authority=authority" in source
    assert "range(run.requirements.batch_count)" in source
    assert source.index("_r8u_validate_pristine_exclusion_paths(") < source.index(
        "finalizer.finalize_receipts("
    )


def test_tail_pristine_gate_uses_same_device_nonmount_compatibility_rule() -> None:
    source = inspect.getsource(controller._r8u_r7d_require_tail_pristine)
    assert "_r8u_validate_pristine_exclusion_paths(" in source
    assert 'PurePosixPath("cohort_finalization")' in source


def test_runner_dispatch_is_exact_and_probe_clears_cuda() -> None:
    source = (ROOT / "scripts/scc_run_lvef_c3_r8r_recovery_continuation.sh").read_text()
    assert "r8u_r7d_(ctx|seq|fin)" in source
    assert "--run-r8u-r7d-continuation-context-probe" in source
    assert "--run-r8u-r7d-continuation-17-19-array-task" in source
    assert "--run-r8u-r7d-continuation-finalizer" in source
    probe_case = source.split("lvef_c3_r8u_r7d_ctx_*)", 1)[1].split(";;", 1)[0]
    assert 'SGE_TASK_ID:-}" == "17"' in probe_case
    assert 'NSLOTS:-}" == "1"' in probe_case
    assert "export CUDA_VISIBLE_DEVICES=''" in probe_case


def test_cli_exposes_only_six_fixed_r7d_modes() -> None:
    parser = controller._r8u_r7d_parser()
    modes = (
        "--submit-r8u-r7d-continuation-context-probe",
        "--run-r8u-r7d-continuation-context-probe",
        "--adjudicate-r8u-r7d-continuation-context-probe",
        "--submit-r8u-r7d-continuation-17-19",
        "--run-r8u-r7d-continuation-17-19-array-task",
        "--run-r8u-r7d-continuation-finalizer",
    )
    for mode in modes:
        parsed = parser.parse_args([mode])
        assert sum(value is True for value in vars(parsed).values()) == 1


def test_nonzero_probe_qacct_is_sealed_before_field_specific_failure() -> None:
    """A terminal nonzero qacct record is evidence, not a missing record."""

    cases = (
        (37, 78, "R8U_R7D_PROBE_QACCT_FAILED_NONZERO"),
        (0, 78, "R8U_R7D_PROBE_QACCT_EXIT_STATUS_NONZERO"),
    )
    for failed, exit_status, expected_code in cases:
        writes: dict[Path, dict[str, object]] = {}
        accounting_projection = {
            "fixed_qacct_argv": ["/usr/bin/qacct", "-j", "8123456", "-t", "17"],
            "fixed_qacct_argv_sha256": "1" * 64,
            "normalized_qacct_record": {"closed": "record"},
            "normalized_qacct_record_sha256": "2" * 64,
            "failed": failed,
            "exit_status": exit_status,
            "wall_seconds": 3,
            "chronology_valid": True,
            "job_id_match": True,
            "task_id_match": True,
            "job_name_match": True,
            "owner_match": True,
        }
        worker_receipt = {
            "final_closed_classification": "PASS_QSTAT_SELF_RECORD_MATCHED"
        }

        def write_receipt(path: Path, value: object) -> str:
            assert isinstance(value, dict)
            writes[path] = dict(value)
            return "a" * 64

        patches = (
            mock.patch.object(controller.os.path, "lexists", return_value=False),
            mock.patch.object(
                controller.scheduler,
                "build_qsub_environment",
                return_value=({"SEALED": "environment"}, {}),
            ),
            mock.patch.object(
                controller,
                "validate_r8u_r7d_scheduler_account_authority",
                return_value={"qsub_environment_sha256": "3" * 64},
            ),
            mock.patch.object(
                controller,
                "_load_fixed_original_run",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                controller,
                "_r8u_r7d_validate_probe_chain",
                return_value={
                    "probe_job_id": "8123456",
                    "probe_job_name": controller._r8u_r7d_probe_job_name(COMMIT),
                },
            ),
            mock.patch.object(
                controller.scheduler,
                "qsub_environment_sha256",
                return_value="3" * 64,
            ),
            mock.patch.object(
                controller,
                "_r8u_r7d_probe_qacct_once",
                return_value=accounting_projection,
            ),
            mock.patch.object(controller, "_wait_for_r8u_r5_control"),
            mock.patch.object(
                controller,
                "_load_private_json",
                return_value=(worker_receipt, b"worker"),
            ),
            mock.patch.object(
                controller,
                "_r8u_r7d_validate_existing_worker_receipt",
                return_value=worker_receipt,
            ),
            mock.patch.object(
                controller,
                "_r8u_r7d_read_probe_log",
                return_value=(b"sealed log", "4" * 64),
            ),
            mock.patch.object(
                controller,
                "_current_r8u_r7d_implementation_commit",
                return_value=COMMIT,
            ),
            mock.patch.object(
                controller.core, "sha256_file", return_value="5" * 64
            ),
            mock.patch.object(
                controller, "_write_private_json", side_effect=write_receipt
            ),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            try:
                controller.adjudicate_r8u_r7d_continuation_context_probe(
                    qacct_runner=mock.Mock(),
                    timeout_seconds=1.0,
                    sleeper=lambda _seconds: None,
                    monotonic_clock=lambda: 0.0,
                )
            except controller.R8RControllerError as exc:
                assert exc.code == expected_code
            else:
                raise AssertionError("nonzero probe accounting was accepted")

        assert controller.R8U_R7D_PROBE_ACCOUNTING_PATH in writes
        assert controller.R8U_R7D_PROBE_TERMINAL_PATH in writes
        accounting = writes[controller.R8U_R7D_PROBE_ACCOUNTING_PATH]
        terminal = writes[controller.R8U_R7D_PROBE_TERMINAL_PATH]
        assert "FAIL" in str(accounting["status"])
        assert "FAIL" in str(terminal["status"])
        assert accounting["failed"] == failed
        assert accounting["exit_status"] == exit_status
        assert terminal["failed"] == failed
        assert terminal["exit_status"] == exit_status
        assert accounting["failure_code"] == expected_code
        assert terminal["failure_code"] == expected_code
        assert accounting["accounting_projection"] == accounting_projection


def test_post_qsub_qstat_failure_still_seals_blocked_submission() -> None:
    """Both assigned job IDs must remain discoverable after qstat failure."""

    writes: dict[Path, dict[str, object]] = {}
    failure_code = "R8U_R7D_POST_SUBMISSION_QSTAT_UNAVAILABLE"
    pre_submission_qstat = {
        "status": "PASS_R8U_R7D_FULL_XML_QSTAT_SNAPSHOT",
        "qstat_snapshot_count": 1,
        "truncated_display_name_used": False,
    }

    def write_receipt(path: Path, value: object) -> str:
        assert isinstance(value, dict)
        writes[path] = dict(value)
        return "6" * 64

    def load_written_receipt(path: Path) -> tuple[dict[str, object], bytes]:
        return dict(writes[path]), b"sealed"

    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(
            controller,
            "_current_r8u_r7d_implementation_commit",
            return_value=COMMIT,
        ),
        mock.patch.object(
            controller.scheduler,
            "build_qsub_environment",
            return_value=({"SEALED": "environment"}, {}),
        ),
        mock.patch.object(
            controller,
            "validate_r8u_r7d_scheduler_account_authority",
            return_value={"qsub_environment_sha256": "7" * 64},
        ),
        mock.patch.object(
            controller.scheduler,
            "qsub_environment_sha256",
            return_value="7" * 64,
        ),
        mock.patch.object(
            controller,
            "_load_fixed_original_run",
            return_value=SimpleNamespace(),
        ),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_chain"),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_terminal"),
        mock.patch.object(
            controller, "_r8u_r7d_load_consumed_evidence", return_value={}
        ),
        mock.patch.object(controller, "_r8u_r7d_validate_capacity"),
        mock.patch.object(
            controller,
            "_r8u_r7d_bounded_prefix",
            return_value=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
        ),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller.os.path, "lexists", return_value=False),
        mock.patch.object(
            controller,
            "_r8u_r7d_login_qstat_snapshot",
            side_effect=(
                pre_submission_qstat,
                controller.R8RControllerError(failure_code),
            ),
        ),
        mock.patch.object(
            controller,
            "_r8u_r7d_process_quiescence",
            return_value={"status": "PASS"},
        ),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(
            controller,
            "_r8u_r7d_continuation_claim",
            return_value={"status": "AUTHORIZED"},
        ),
        mock.patch.object(
            controller, "_write_private_json", side_effect=write_receipt
        ),
        mock.patch.object(
            controller, "_load_private_json", side_effect=load_written_receipt
        ),
        mock.patch.object(
            controller.core, "sha256_file", return_value="8" * 64
        ),
        mock.patch.object(
            controller.scheduler,
            "_capture_qsub",
            side_effect=("8234567", "8234568"),
        ),
        mock.patch.object(
            controller,
            "_qsub_evidence_authority",
            return_value={"captured": True},
        ),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        try:
            controller.submit_r8u_r7d_continuation_17_19(
                qsub_runner=mock.Mock(),
                qstat_runner=mock.Mock(),
                process_runner=mock.Mock(),
            )
        except controller.R8RControllerError as exc:
            assert exc.code == failure_code
        else:
            raise AssertionError("post-qsub qstat failure was accepted")

    assert controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH in writes
    blocked = writes[controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH]
    assert blocked["status"] == "BLOCKED_POST_SUBMISSION_QSTAT_DIAGNOSTIC"
    assert blocked["initial_full_xml_qstat_projection"]["status"] == (
        "BLOCKED_R8U_R7D_POST_SUBMISSION_QSTAT"
    )
    assert blocked["failure_code"] == failure_code
    assert blocked["array_job_id"] == "8234567"
    assert blocked["finalizer_job_id"] == "8234568"


def _valid_worker_receipt() -> dict[str, object]:
    diagnostics = SimpleNamespace(
        job_id_match=True,
        task_context_match=True,
        effective_uid_match=True,
        job_role_match=True,
        runner_sha256_match=True,
        python_sha256_match=True,
        implementation_commit_match=True,
        qsub_environment_sha256_match=True,
    )
    context = SimpleNamespace(diagnostics=diagnostics)
    observation = scheduler.R8UR7DQstatObservation(
        observation_ordinal=1,
        record_present=True,
        unique=True,
        job_id_match=True,
        task_id_match=True,
        owner_match=True,
        full_job_name_match=True,
        state_token="r",
    )
    diagnostic = scheduler.R8UR7DQstatDiagnostic(
        classification="PASS_QSTAT_SELF_RECORD_MATCHED",
        observation_count=1,
        row_ever_visible=True,
        job_id_equality=True,
        task_id_equality=True,
        owner_equality=True,
        full_job_name_equality=True,
        observed_scheduler_state_category="RUNNING_R",
        observations=(observation,),
    )
    return dict(controller._r8u_r7d_worker_receipt(
        expected_role=controller.R8U_R7D_ARRAY_ROLE,
        context=context,
        diagnostic=diagnostic,
        account_sha256="a" * 64,
        authority_sha256="b" * 64,
        submission_sha256="c" * 64,
    ))


def _validate_worker_receipt(value: dict[str, object]) -> None:
    controller._r8u_r7d_validate_existing_worker_receipt(
        value,
        expected_role=controller.R8U_R7D_ARRAY_ROLE,
        account_sha256="a" * 64,
        authority_sha256="b" * 64,
        submission_sha256="c" * 64,
    )


def test_probe_adjudication_preserves_exact_blocked_qstat_code() -> None:
    value = _valid_worker_receipt()
    observations = value["qstat_observations"]
    assert isinstance(observations, list)
    observation = observations[0]
    assert isinstance(observation, dict)
    observation["owner_match"] = False
    value["qstat_owner_equality"] = False
    value["final_closed_classification"] = (
        "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION"
    )
    code = controller._r8u_r7d_blocked_worker_receipt_code(
        value,
        expected_role=controller.R8U_R7D_ARRAY_ROLE,
        account_sha256="a" * 64,
        authority_sha256="b" * 64,
        submission_sha256="c" * 64,
    )
    assert code == "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION"
    observations.insert(0, {
        "observation_ordinal": 1,
        "record_present": False,
        "unique": False,
        "job_id_match": None,
        "task_id_match": None,
        "owner_match": None,
        "full_job_name_match": None,
        "state_token": None,
    })
    observation["observation_ordinal"] = 2
    value["qstat_observation_count"] = 2
    delayed_code = controller._r8u_r7d_blocked_worker_receipt_code(
        value,
        expected_role=controller.R8U_R7D_ARRAY_ROLE,
        account_sha256="a" * 64,
        authority_sha256="b" * 64,
        submission_sha256="c" * 64,
    )
    assert delayed_code == "BLOCKED_QSTAT_SELF_OWNER_CONTRADICTION"
    source = inspect.getsource(
        controller.adjudicate_r8u_r7d_continuation_context_probe
    )
    assert source.index("if blocked_worker_code is not None:") < source.index(
        "if accounting_failure is not None:"
    )


def test_existing_worker_receipt_is_closed_and_qstat_consistent() -> None:
    value = _valid_worker_receipt()
    _validate_worker_receipt(value)
    corruptions = (
        {**value, "unexpected": True},
        {**value, "schema_version": True},
        {**value, "qstat_owner_equality": False},
        {
            **value,
            "qstat_row_ever_visible": False,
            "final_closed_classification": "PASS_QSTAT_SELF_RECORD_NOT_YET_VISIBLE",
        },
        {
            **value,
            "qstat_observations": [
                {**value["qstat_observations"][0], "observation_ordinal": 2}
            ],
        },
        {
            **value,
            "qstat_observations": [
                {**value["qstat_observations"][0], "observation_ordinal": True}
            ],
        },
    )
    for corrupt in corruptions:
        try:
            _validate_worker_receipt(corrupt)
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7D_WORKER_CONTEXT_RECEIPT_INVALID"
        else:
            raise AssertionError("invalid stored worker receipt accepted")
