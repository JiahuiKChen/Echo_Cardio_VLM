#!/usr/bin/env python3
"""Focused synthetic proofs for the additive R8U-R7F controller."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as controller


COMMIT = "f" * 40
PASS_STATUS = controller.r7d_capacity.R8U_R7F_CAPACITY_STATUS_PASS


def _evidence() -> dict[str, int]:
    return {
        "preserved_old_evidence_bytes": 12,
        "preserved_old_evidence_files": 3,
        "partial_scientific_artifact_bytes": 0,
        "partial_scientific_artifact_files": 0,
    }


def test_r7f_commit_is_the_direct_child_of_fixed_r7e_and_epochs_are_exact() -> None:
    def git(*arguments: str) -> str:
        if arguments[:4] == ("rev-list", "--parents", "-n", "1"):
            commit = arguments[-1]
            parents = {
                COMMIT: controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT,
                controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT: (
                    controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT
                ),
                controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT: (
                    controller.R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT
                ),
            }
            return f"{commit} {parents[commit]}"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            start = arguments[-1].split("..", 1)[0]
            return {
                controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT: "1",
                controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT: "2",
                controller.R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT: "3",
                controller.ORIGINAL_SCIENTIFIC_COMMIT: "14",
            }[start]
        raise AssertionError(arguments)

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r7f_implementation_commit() == COMMIT

    r7e_epochs = controller._r8u_r7d_implementation_authority_epochs(
        controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert r7e_epochs["r8u_r7e_capacity_recovery_commit"] == (
        controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert "r8u_r7f_plan_scope_recovery_commit" not in r7e_epochs
    r7f_epochs = controller._r8u_r7d_implementation_authority_epochs(COMMIT)
    assert r7f_epochs["r8u_r7e_capacity_recovery_commit"] == (
        controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert r7f_epochs["r8u_r7f_plan_scope_recovery_commit"] == COMMIT


def test_r7f_selector_is_tristate_and_rejects_two_successor_claims() -> None:
    def epoch_for(paths: set[Path]) -> str:
        with mock.patch.object(
            controller.os.path, "lexists", side_effect=lambda path: path in paths
        ):
            return controller._r8u_r7d_execution_epoch()

    assert epoch_for(set()) == "R7D"
    assert epoch_for({controller.R8U_R7E_CONTINUATION_CLAIM_PATH}) == "R7E"
    assert epoch_for({controller.R8U_R7F_CONTINUATION_CLAIM_PATH}) == "R7F"
    with mock.patch.object(controller.os.path, "lexists", return_value=True):
        try:
            controller._r8u_r7d_execution_epoch()
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7_EXECUTION_CLAIM_CONTRADICTION"
        else:
            raise AssertionError("two successor claims were accepted")


def test_r7f_namespace_claim_and_probe_bindings_are_additive() -> None:
    assert controller.R8U_R7F_CAPACITY_ROOT != controller.R8U_R7E_CAPACITY_ROOT
    assert not controller.R8U_R7F_CONTINUATION_CLAIM_PATH.is_relative_to(
        controller.R8U_R7F_CAPACITY_ROOT
    )

    digests = {
        controller.R8U_R7F_STATIC_PLAN_PROJECTION_PATH: "1" * 64,
        controller.R8U_R7F_CAPACITY_AUTHORITY_PATH: "2" * 64,
        controller.R8U_R7F_CAPACITY_PATH: "3" * 64,
        controller.R8U_R7D_ACCOUNT_AUTHORITY_PATH: "4" * 64,
        controller.R8U_R7D_PROBE_AUTHORITY_PATH: "5" * 64,
    }
    with (
        mock.patch.object(
            controller.core,
            "sha256_file",
            side_effect=lambda path: digests.get(path, "6" * 64),
        ),
        mock.patch.object(controller, "_script_authority", return_value={}),
        mock.patch.object(
            controller, "_qsub_evidence_authority", return_value={"sealed": True}
        ),
    ):
        claim = controller._r8u_r7f_continuation_claim(
            run=SimpleNamespace(runtime_authority={}),
            implementation_commit=COMMIT,
            prefix_receipts=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
            qsub_environment_sha256="7" * 64,
        )
        authority = controller._r8u_r7d_probe_authority(
            run=SimpleNamespace(runtime_authority={}),
            implementation_commit=COMMIT,
            prefix_receipts=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
            qsub_environment_sha256="7" * 64,
            r7f_claim_sha256="8" * 64,
            r7f_capacity_sha256="9" * 64,
        )
        submission = controller._r8u_r7d_probe_submission(
            implementation_commit=COMMIT,
            probe_job_id="8123456",
            qsub_environment_sha256="7" * 64,
            r7f_claim_sha256="8" * 64,
            r7f_capacity_sha256="9" * 64,
        )

    assert claim["r7d_worker_identity_commit"] == (
        controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT
    )
    assert claim["r7e_capacity_recovery_commit"] == (
        controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert claim["consumed_r7e_failure_receipt_sha256"] == (
        controller.R8U_R7E_FAILURE_RECEIPT_SHA256
    )
    assert claim["static_plan_projection_sha256"] == "1" * 64
    assert claim["total_new_qsub_maximum"] == 3
    for value in (authority, submission):
        assert value["r7f_continuation_claim_sha256"] == "8" * 64
        assert value["r7f_capacity_receipt_sha256"] == "9" * 64
        assert "r7e_continuation_claim_sha256" not in value
    assert submission["scheduler_submission_maximum"] == 3


def test_r7f_consumes_only_the_exact_zero_observation_r7e_failure() -> None:
    receipt = {
        "artifact_type": controller.r7d_capacity.R8U_R7E_CAPACITY_ARTIFACT_TYPE,
        "status": "BLOCKED_R8U_R7E_CAPACITY_OBSERVATION_PLAN_SCOPE_MISMATCH",
        "original_attempt_id": controller.ORIGINAL_ATTEMPT_ID,
        "original_plan_sha256": controller.ORIGINAL_PLAN_SHA256,
        "original_scientific_governing_commit": controller.ORIGINAL_SCIENTIFIC_COMMIT,
        "r7e_runtime_commit": controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT,
        "command_diagnostics": [],
        "raw_capture_root_owner_private": False,
        "arithmetic_evaluated": False,
        "valid_numerical_deficit_calculated": False,
        "capacity_projection": None,
        "failure_diagnostic": {
            "failure_stage": "STATIC",
            "failure_field": "plan.tasks17_19_scope",
            "failure_predicate": "PLAN_SCOPE_MISMATCH",
            "failure_before_capacity_arithmetic": True,
        },
    }
    for field in (
        "capacity_observation_count",
        "native_quota_file_captures",
        "pquota_command_captures",
        "findmnt_command_captures",
        "df_command_captures",
        "du_command_captures",
        "pquota_command_invocation_attempts",
        "findmnt_command_invocation_attempts",
        "df_command_invocation_attempts",
        "du_command_invocation_attempts",
        "raw_capture_file_count",
    ):
        receipt[field] = 0
    with (
        mock.patch.object(
            controller, "_load_private_json", return_value=(receipt, b"sealed")
        ),
        mock.patch.object(
            controller.core,
            "sha256_file",
            return_value=controller.R8U_R7E_FAILURE_RECEIPT_SHA256,
        ),
    ):
        assert controller._r8u_r7f_validate_consumed_r7e_failure() == receipt
        receipt["capacity_observation_count"] = 1
        try:
            controller._r8u_r7f_validate_consumed_r7e_failure()
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7F_CONSUMED_R7E_FAILURE_RECEIPT_INVALID"
        else:
            raise AssertionError("mutated R7E failure was accepted")


def test_r7f_probe_seals_projection_and_claim_before_its_only_qsub() -> None:
    writes: dict[Path, object] = {}
    projection = {"status": controller.r7d_capacity.R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_PASS}
    claim = {"status": "AUTHORIZED_FRESH_R7F_CONTINUATION_17_19"}
    capture = mock.Mock()

    def lexists(path: Path) -> bool:
        return path in writes

    def write(path: Path, value: object) -> str:
        writes[path] = value
        return "a" * 64

    def load(path: Path) -> tuple[object, bytes]:
        return writes[path], b"sealed"

    def write_projection(path: Path, value: object) -> str:
        assert path == controller.R8U_R7F_STATIC_PLAN_PROJECTION_PATH
        writes[path] = value
        return "a" * 64

    def qsub(*_args: object, **_kwargs: object) -> str:
        assert controller.R8U_R7F_STATIC_PLAN_PROJECTION_PATH in writes
        assert controller.R8U_R7F_CAPACITY_AUTHORITY_PATH in writes
        assert controller.R8U_R7F_CONTINUATION_CLAIM_PATH in writes
        return "8123456"

    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(controller, "_current_r8u_r7f_implementation_commit", return_value=COMMIT),
        mock.patch.object(controller.scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, {})),
        mock.patch.object(controller.scheduler, "qsub_environment_sha256", return_value="e" * 64),
        mock.patch.object(controller.os.path, "lexists", side_effect=lexists),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=SimpleNamespace(plan={}, runtime_authority={})),
        mock.patch.object(controller, "_validate_original_controls"),
        mock.patch.object(controller, "_r8u_r7d_bounded_prefix", return_value=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256),
        mock.patch.object(controller, "_r8u_r7d_validate_consumed_evidence", return_value=_evidence()),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller, "_r8u_r7f_validate_consumed_r7e_failure"),
        mock.patch.object(controller, "_r8u_r7d_login_qstat_snapshot", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_r8u_r7d_process_quiescence", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(controller.r7d_capacity, "require_fixed_r8u_r7f_tasks17_19_plan_projection", return_value=projection),
        mock.patch.object(controller.r7d_capacity, "write_r8u_r7f_static_plan_projection_no_clobber", side_effect=write_projection),
        mock.patch.object(controller, "_r8u_r7f_capacity_authority", return_value={"status": "AUTHORITY"}),
        mock.patch.object(controller.r7d_capacity, "capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity", capture),
        mock.patch.object(controller, "_r8u_r7f_validate_capacity_namespace", return_value={"status": PASS_STATUS}),
        mock.patch.object(controller, "_r8u_r7d_account_authority", return_value={"status": "ACCOUNT"}),
        mock.patch.object(controller, "_r8u_r7f_continuation_claim", return_value=claim),
        mock.patch.object(controller, "_r8u_r7d_probe_authority", return_value={"status": "PROBE_AUTHORITY"}),
        mock.patch.object(controller, "_r8u_r7d_probe_submission", return_value={"status": "PROBE_SUBMISSION"}),
        mock.patch.object(controller, "_write_private_json", side_effect=write),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller.core, "sha256_file", return_value="a" * 64),
        mock.patch.object(controller.scheduler, "_capture_qsub", side_effect=qsub),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = controller.submit_r8u_r7f_continuation_context_probe(
            capacity_runner=mock.Mock()
        )

    capture.assert_called_once()
    assert result["capacity_observation_count"] == 1
    assert result["new_qsub_submissions"] == 1


def test_r7f_capacity_namespace_collision_stops_before_observation_or_qsub() -> None:
    capture = mock.Mock(side_effect=AssertionError("capacity observation reached"))
    qsub = mock.Mock(side_effect=AssertionError("qsub reached"))

    def lexists(path: Path) -> bool:
        return path == controller.R8U_R7F_CAPACITY_ROOT

    with (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(
            controller, "_current_r8u_r7f_implementation_commit", return_value=COMMIT
        ),
        mock.patch.object(
            controller.scheduler,
            "build_qsub_environment",
            return_value=({"USER": "owner"}, {}),
        ),
        mock.patch.object(
            controller.scheduler, "qsub_environment_sha256", return_value="e" * 64
        ),
        mock.patch.object(controller.os.path, "lexists", side_effect=lexists),
        mock.patch.object(
            controller.r7d_capacity,
            "capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity",
            capture,
        ),
        mock.patch.object(controller.scheduler, "_capture_qsub", qsub),
    ):
        try:
            controller.submit_r8u_r7f_continuation_context_probe()
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7F_CAPACITY_OUTPUT_COLLISION"
        else:
            raise AssertionError("R7F capacity namespace was clobbered")
    capture.assert_not_called()
    qsub.assert_not_called()


def test_r7f_probe_terminal_requires_the_exact_r7f_pass_authority() -> None:
    digest = "a" * 64
    expected_common = controller._r8u_r7f_common(
        artifact_type="lvef_c3_r8u_r7f_context_probe_terminal_v1",
        status="PASS_R7F_CONTINUATION_WORKER_CONTEXT_PROBE",
        implementation_commit=COMMIT,
    )
    terminal = {
        **expected_common,
        "probe_submission_receipt_sha256": digest,
        "worker_context_receipt_sha256": digest,
        "accounting_receipt_sha256": digest,
        "qstat_classification": next(
            iter(controller.scheduler.R8U_R7D_QSTAT_PASS_CLASSIFICATIONS)
        ),
        "controlling_worker_identity": "PASS",
        "failed": 0,
        "exit_status": 0,
        "task_id": 17,
        "scientific_artifacts_created": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
    }
    with (
        mock.patch.object(controller, "_r8u_r7d_execution_epoch", return_value="R7F"),
        mock.patch.object(
            controller,
            "_current_r8u_r7d_execution_implementation_commit",
            return_value=COMMIT,
        ),
        mock.patch.object(
            controller, "_load_private_json", return_value=(terminal, b"sealed")
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=digest),
    ):
        assert controller._r8u_r7d_validate_probe_terminal() == terminal


def test_r7f_continuation_chain_selects_all_three_r7f_receipts() -> None:
    initial = {
        "status": "PASS_R8U_R7D_FULL_XML_QSTAT_SNAPSHOT",
        "qstat_snapshot_count": 1,
        "truncated_display_name_used": False,
    }
    claim = {"status": "R7F_CLAIM"}
    array = {"status": "R7F_ARRAY"}
    finalizer = {"status": "R7F_FINALIZER"}
    submission = {
        "status": "R7F_COMBINED",
        "array_job_id": "8234567",
        "finalizer_job_id": "8234568",
        "initial_full_xml_qstat_projection": initial,
    }
    observed_paths: list[Path] = []

    def load(path: Path) -> tuple[object, bytes]:
        observed_paths.append(path)
        return {
            controller.R8U_R7F_CONTINUATION_CLAIM_PATH: claim,
            controller.R8U_R7F_ARRAY_SUBMISSION_PATH: array,
            controller.R8U_R7F_FINALIZER_SUBMISSION_PATH: finalizer,
            controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH: submission,
        }[path], b"sealed"

    with (
        mock.patch.object(controller, "_r8u_r7d_execution_epoch", return_value="R7F"),
        mock.patch.object(
            controller,
            "_current_r8u_r7d_execution_implementation_commit",
            return_value=COMMIT,
        ),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_chain"),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_terminal"),
        mock.patch.object(controller, "_wait_for_r8u_r5_control"),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller, "_r8u_r7f_continuation_claim", return_value=claim),
        mock.patch.object(controller, "_r8u_r7f_array_submission", return_value=array),
        mock.patch.object(
            controller, "_r8u_r7f_finalizer_submission", return_value=finalizer
        ),
        mock.patch.object(
            controller, "_r8u_r7f_continuation_submission", return_value=submission
        ),
        mock.patch.object(controller.core, "sha256_file", return_value="a" * 64),
    ):
        assert controller._r8u_r7d_validate_continuation_chain(
            run=SimpleNamespace(), account={"qsub_environment_sha256": "b" * 64}
        ) == submission

    assert controller.R8U_R7F_CONTINUATION_CLAIM_PATH in observed_paths
    assert controller.R8U_R7F_ARRAY_SUBMISSION_PATH in observed_paths
    assert controller.R8U_R7F_FINALIZER_SUBMISSION_PATH in observed_paths


def test_r7f_scientific_submit_is_array_then_held_finalizer_and_seals_both() -> None:
    writes: dict[Path, object] = {}
    qsub_commands: list[list[str]] = []
    claim = {"status": "CLAIM"}

    def load(path: Path) -> tuple[object, bytes]:
        if path == controller.R8U_R7F_CONTINUATION_CLAIM_PATH:
            return claim, b"claim"
        return writes[path], b"sealed"

    def write(path: Path, value: object) -> str:
        writes[path] = value
        return {
            controller.R8U_R7F_ARRAY_SUBMISSION_PATH: "a" * 64,
            controller.R8U_R7F_FINALIZER_SUBMISSION_PATH: "b" * 64,
            controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH: "c" * 64,
        }[path]

    def qsub(_role: str, command: list[str], **_kwargs: object) -> str:
        qsub_commands.append(command)
        return "8234567" if len(qsub_commands) == 1 else "8234568"

    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(controller, "_current_r8u_r7f_implementation_commit", return_value=COMMIT),
        mock.patch.object(controller.scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, {})),
        mock.patch.object(controller.scheduler, "qsub_environment_sha256", return_value="d" * 64),
        mock.patch.object(controller, "validate_r8u_r7d_scheduler_account_authority", return_value={"qsub_environment_sha256": "d" * 64}),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=SimpleNamespace()),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_chain"),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_terminal"),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller, "_r8u_r7f_continuation_claim", return_value=claim),
        mock.patch.object(controller.os.path, "lexists", return_value=False),
        mock.patch.object(controller, "_r8u_r7d_login_qstat_snapshot", return_value={"status": "PASS_R8U_R7D_FULL_XML_QSTAT_SNAPSHOT", "qstat_snapshot_count": 1, "truncated_display_name_used": False}),
        mock.patch.object(controller, "_r8u_r7d_process_quiescence"),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(controller.scheduler, "_capture_qsub", side_effect=qsub),
        mock.patch.object(controller.core, "sha256_file", return_value="e" * 64),
        mock.patch.object(controller, "_r8u_r7f_array_submission", return_value={"status": "ARRAY"}),
        mock.patch.object(controller, "_r8u_r7f_finalizer_submission", return_value={"status": "FINALIZER"}),
        mock.patch.object(controller, "_r8u_r7f_continuation_submission", return_value={"status": "COMBINED"}),
        mock.patch.object(controller, "_write_private_json", side_effect=write),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller, "_r8u_r7d_validate_continuation_chain"),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = controller.submit_r8u_r7f_continuation_17_19()

    assert len(qsub_commands) == 2
    array, finalizer = qsub_commands
    assert array[array.index("-t") + 1] == "17-19"
    assert array[array.index("-tc") + 1] == "1"
    assert finalizer[finalizer.index("-hold_jid") + 1] == "8234567"
    assert controller.R8U_R7F_ARRAY_SUBMISSION_PATH in writes
    assert controller.R8U_R7F_FINALIZER_SUBMISSION_PATH in writes
    assert controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH in writes
    assert result["new_qsub_submissions"] == 2
    assert result["total_new_qsub_submissions"] == 3


def test_all_three_r7f_cli_paths_return_without_legacy_fallthrough() -> None:
    cases = (
        (
            "--submit-r8u-r7f-continuation-context-probe",
            "submit_r8u_r7f_continuation_context_probe",
            {
                "status": "R7F_CONTINUATION_CONTEXT_PROBE_SUBMITTED",
                "probe_job_id": "8123456",
                "capacity_status": PASS_STATUS,
                "static_plan_projection_sha256": "a" * 64,
                "capacity_receipt_sha256": "b" * 64,
                "continuation_claim_sha256": "c" * 64,
                "capacity_observation_count": 1,
            },
        ),
        (
            "--adjudicate-r8u-r7f-continuation-context-probe",
            "adjudicate_r8u_r7d_continuation_context_probe",
            {
                "status": "PASS_R7F_CONTINUATION_WORKER_CONTEXT_PROBE",
                "qstat_classification": "PASS_QSTAT_TRANSITIONAL_STATE",
            },
        ),
        (
            "--submit-r8u-r7f-continuation-17-19",
            "submit_r8u_r7f_continuation_17_19",
            {
                "status": "FINAL_TASKS_17_19_AND_FINALIZER_RESUBMITTED",
                "array_job_id": "8234567",
                "finalizer_job_id": "8234568",
                "array_submission_receipt_sha256": "d" * 64,
                "finalizer_submission_receipt_sha256": "e" * 64,
                "continuation_receipt_sha256": "f" * 64,
            },
        ),
    )
    for option, function_name, result in cases:
        output = io.StringIO()
        with (
            mock.patch.object(controller, function_name, return_value=result),
            mock.patch.object(
                controller, "_parser", side_effect=AssertionError("legacy reached")
            ),
            redirect_stdout(output),
        ):
            assert controller.guarded_main([option]) == 0
        assert "R8U_R7F_STATUS=" in output.getvalue()


def test_legacy_r7d_and_r7e_submits_stop_before_qsub_after_r7f_claim() -> None:
    qsub = mock.Mock(side_effect=AssertionError("legacy qsub reached"))
    for submit, code in (
        (
            controller.submit_r8u_r7d_continuation_17_19,
            "R8U_R7D_CONTINUATION_SUPERSEDED_BY_R7F_CLAIM",
        ),
        (
            controller.submit_r8u_r7e_continuation_17_19,
            "R8U_R7E_CONTINUATION_SUPERSEDED_BY_R7F_CLAIM",
        ),
    ):
        with (
            mock.patch.object(
                controller.os.path,
                "lexists",
                side_effect=lambda path: (
                    path == controller.R8U_R7F_CONTINUATION_CLAIM_PATH
                ),
            ),
            mock.patch.object(
                controller.scheduler,
                "validate_scheduler_tools",
                side_effect=AssertionError("legacy validation reached"),
            ),
            mock.patch.object(controller.scheduler, "_capture_qsub", qsub),
        ):
            try:
                submit()
            except controller.R8RControllerError as exc:
                assert exc.code == code
            else:
                raise AssertionError("legacy submit accepted the R7F claim")
    qsub.assert_not_called()
