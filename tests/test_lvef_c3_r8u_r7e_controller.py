#!/usr/bin/env python3
"""Focused synthetic proofs for the additive R8U-R7E controller."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as controller


COMMIT = controller.R8U_R7E_CAPACITY_RECOVERY_IMPLEMENTATION_COMMIT
PASS_STATUS = "PASS_R8U_R7E_TASKS_17_19_REMAINING_CAPACITY"


def _evidence() -> dict[str, int]:
    return {
        "preserved_old_evidence_bytes": 12,
        "preserved_old_evidence_files": 3,
        "partial_scientific_artifact_bytes": 0,
        "partial_scientific_artifact_files": 0,
    }


def test_r7e_commit_is_the_sole_child_of_fixed_r7d() -> None:
    def git(*arguments: str) -> str:
        if arguments[:4] == ("rev-list", "--parents", "-n", "1"):
            return (
                f"{COMMIT} "
                f"{controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT}"
            )
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            return (
                "2"
                if arguments[-1].startswith(
                    controller.R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT
                )
                else "1"
            )
        raise AssertionError(arguments)

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r7e_implementation_commit() == COMMIT

    epochs = controller._r8u_r7d_implementation_authority_epochs(COMMIT)
    assert epochs["r8u_r7d_worker_identity_repair_commit"] == (
        controller.R8U_R7D_WORKER_IDENTITY_IMPLEMENTATION_COMMIT
    )
    assert epochs["r8u_r7e_capacity_recovery_commit"] == COMMIT
    assert "r8u_r7f_plan_scope_recovery_commit" not in epochs


def test_r7e_capacity_namespace_and_preprobe_claim_are_separate() -> None:
    assert controller.R8U_R7E_CAPACITY_ROOT != controller.R8U_R7D_ROOT
    assert not controller.R8U_R7E_CONTINUATION_CLAIM_PATH.is_relative_to(
        controller.R8U_R7E_CAPACITY_ROOT
    )
    diagnosis = controller._r8u_r7e_old_capacity_diagnosis(
        implementation_commit=COMMIT
    )
    expected_diagnostic_fields = {
        "old_r7d_capacity_observation_preserved": True,
        "old_r7d_outer_status": "BLOCKED_R8U_R7D_CAPACITY_INVALID",
        "old_r7d_inner_status": "NOT_PERSISTED",
        "old_r7d_failure_class": "DIAGNOSTIC_DETAIL_NOT_PERSISTED",
        "first_failed_predicate": "NOT_PERSISTED",
        "failed_command_type": "NOT_PERSISTED",
        "failed_command_ordinal": "NOT_PERSISTED",
        "command_exit_status": "NOT_PERSISTED",
        "capture_present": "NOT_PERSISTED",
        "parser_result": "NOT_PERSISTED",
        "expected_value_category": "NOT_PERSISTED",
        "observed_value_category": "NOT_PERSISTED",
        "validation_class": "NOT_PERSISTED",
        "exception_class": "NOT_PERSISTED",
        "raw_capture_sha256": "NOT_AVAILABLE",
        "failure_before_capacity_arithmetic": "NOT_PERSISTED",
        "valid_numerical_deficit_calculated": False,
        "implementation_inner_status_equivalent": (
            "R8U_R7D_CAPACITY_OBSERVATION_INVALID"
        ),
        "consumed_inner_status_equivalent_persisted": False,
        "outer_status_translation_path": {
            "capacity_entrypoint": (
                "capture_and_validate_fixed_r8u_r7d_tasks17_19_capacity"
            ),
            "source_exception_class": "NOT_PERSISTED",
            "source_exception_code": "NOT_PERSISTED",
            "controller_catch_exception_class": "Exception",
            "controller_raised_exception_class": "R8RControllerError",
            "controller_raised_code": "R8U_R7D_CAPACITY_INVALID",
            "cli_prefix_condition": "CODE_DOES_NOT_START_WITH_BLOCKED_",
            "cli_prefix_applied": "BLOCKED_",
            "cli_rendered_status": "BLOCKED_R8U_R7D_CAPACITY_INVALID",
        },
        "new_capacity_commands_executed": 0,
        "scientific_body_reads": 0,
    }
    expected_common = controller._r8u_r7e_common(
        artifact_type=(
            "lvef_c3_r8u_r7e_consumed_r7d_capacity_diagnosis_v1"
        ),
        status="DIAGNOSTIC_DETAIL_NOT_PERSISTED",
        implementation_commit=COMMIT,
    )
    assert diagnosis == {**expected_common, **expected_diagnostic_fields}

    def digest(path: Path) -> str:
        return {
            controller.R8U_R7E_CAPACITY_AUTHORITY_PATH: "a" * 64,
            controller.R8U_R7E_CAPACITY_PATH: "b" * 64,
            controller.R8U_R7E_OLD_DIAGNOSIS_PATH: "c" * 64,
            controller.R8U_R7D_ACCOUNT_AUTHORITY_PATH: "d" * 64,
        }[path]

    with (
        mock.patch.object(controller.core, "sha256_file", side_effect=digest),
        mock.patch.object(controller, "_script_authority", return_value={}),
    ):
        claim = controller._r8u_r7e_continuation_claim(
            run=SimpleNamespace(runtime_authority={}),
            implementation_commit=COMMIT,
            prefix_receipts=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
            qsub_environment_sha256="e" * 64,
        )
    assert claim["capacity_authority_sha256"] == "a" * 64
    assert claim["capacity_receipt_sha256"] == "b" * 64
    assert claim["consumed_r7d_diagnosis_sha256"] == "c" * 64
    assert claim["prefix_final_receipt_sha256"] == list(
        controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256
    )
    assert claim["owner_authorization"] == controller.R8U_R7E_OWNER_AUTHORIZATION
    assert "probe_terminal_receipt_sha256" not in claim


def test_r7e_probe_authority_and_submission_explicitly_bind_claim_and_capacity() -> None:
    claim_sha = "1" * 64
    capacity_sha = "2" * 64
    with (
        mock.patch.object(controller.core, "sha256_file", return_value="3" * 64),
        mock.patch.object(controller, "_script_authority", return_value={}),
        mock.patch.object(
            controller, "_qsub_evidence_authority", return_value={"sealed": True}
        ),
    ):
        authority = controller._r8u_r7d_probe_authority(
            run=SimpleNamespace(runtime_authority={}),
            implementation_commit=COMMIT,
            prefix_receipts=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
            qsub_environment_sha256="4" * 64,
            r7e_claim_sha256=claim_sha,
            r7e_capacity_sha256=capacity_sha,
        )
        submission = controller._r8u_r7d_probe_submission(
            implementation_commit=COMMIT,
            probe_job_id="8123456",
            qsub_environment_sha256="4" * 64,
            r7e_claim_sha256=claim_sha,
            r7e_capacity_sha256=capacity_sha,
        )
    for value in (authority, submission):
        assert value["r7e_continuation_claim_sha256"] == claim_sha
        assert value["r7e_capacity_receipt_sha256"] == capacity_sha
        assert "capacity_authority_sha256" not in value
    assert authority["scientific_artifact_writes_authorized"] == 0
    assert submission["scheduler_submission_maximum"] == 3


def test_r7e_resume_consumes_sealed_pass_without_recapture_and_claim_precedes_qsub() -> None:
    writes: dict[Path, object] = {}
    claim = {"status": "AUTHORIZED_FRESH_R7E_CONTINUATION_17_19"}
    probe_authority = {"status": "AUTHORIZED_PROBE"}
    probe_submission = {"status": "PROBE_SUBMITTED"}
    capacity_capture = mock.Mock(side_effect=AssertionError("capacity recaptured"))

    def lexists(path: Path) -> bool:
        if path == controller.R8U_R7D_ROOT:
            return False
        if path == controller.R8U_R7E_CAPACITY_ROOT:
            return True
        return path in writes

    def write(path: Path, value: object) -> str:
        writes[path] = value
        return {
            controller.R8U_R7E_CONTINUATION_CLAIM_PATH: "1" * 64,
            controller.R8U_R7D_PROBE_AUTHORITY_PATH: "2" * 64,
            controller.R8U_R7D_PROBE_SUBMISSION_PATH: "3" * 64,
        }.get(path, "4" * 64)

    def load(path: Path) -> tuple[object, bytes]:
        return writes[path], b"sealed"

    def qsub(*_args: object, **_kwargs: object) -> str:
        assert controller.R8U_R7E_CONTINUATION_CLAIM_PATH in writes
        assert controller.R8U_R7D_PROBE_AUTHORITY_PATH in writes
        return "8123456"

    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(controller, "_current_r8u_r7e_implementation_commit", return_value=COMMIT),
        mock.patch.object(controller.scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, {})),
        mock.patch.object(controller.scheduler, "qsub_environment_sha256", return_value="e" * 64),
        mock.patch.object(controller.os.path, "lexists", side_effect=lexists),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=SimpleNamespace(plan={}, runtime_authority={})),
        mock.patch.object(controller, "_validate_original_controls"),
        mock.patch.object(controller, "_r8u_r7d_bounded_prefix", return_value=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256),
        mock.patch.object(controller, "_r8u_r7d_validate_consumed_evidence", return_value=_evidence()),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller, "_r8u_r7d_login_qstat_snapshot", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_r8u_r7d_process_quiescence", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_r8u_r7e_validate_capacity_namespace", return_value={"status": PASS_STATUS}),
        mock.patch.object(controller.r7d_capacity, "capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity", capacity_capture),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(controller, "_r8u_r7d_account_authority", return_value={"status": "ACCOUNT"}),
        mock.patch.object(controller, "_r8u_r7e_continuation_claim", return_value=claim),
        mock.patch.object(controller, "_r8u_r7d_probe_authority", return_value=probe_authority),
        mock.patch.object(controller, "_r8u_r7d_probe_submission", return_value=probe_submission),
        mock.patch.object(controller, "_write_private_json", side_effect=write),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller.core, "sha256_file", return_value="9" * 64),
        mock.patch.object(controller.scheduler, "_capture_qsub", side_effect=qsub),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = controller.submit_r8u_r7e_continuation_context_probe(
            qsub_runner=mock.Mock(),
            qstat_runner=mock.Mock(),
            process_runner=mock.Mock(),
            capacity_runner=mock.Mock(),
        )

    capacity_capture.assert_not_called()
    assert result["capacity_observation_resumed"] is True
    assert result["probe_job_id"] == "8123456"
    assert result["continuation_claim_sha256"] == "1" * 64


def test_r7e_field_specific_capacity_failure_is_not_collapsed() -> None:
    code = "BLOCKED_R8U_R7E_CAPACITY_OBSERVATION_FINDMNT_PARSE_FAILURE"
    receipt = {"status": code}
    receipt_sha = "7" * 64
    writes: dict[Path, object] = {}

    class ObservationFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__(code)
            self.code = code
            self.receipt = receipt
            self.receipt_sha256 = receipt_sha

    def capture(*_args: object, **_kwargs: object) -> None:
        writes[controller.R8U_R7E_CAPACITY_PATH] = receipt
        raise ObservationFailure()

    def load(path: Path) -> tuple[object, bytes]:
        return writes[path], b"sealed"

    qsub = mock.Mock(side_effect=AssertionError("qsub reached"))
    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(controller, "_current_r8u_r7e_implementation_commit", return_value=COMMIT),
        mock.patch.object(controller.scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, {})),
        mock.patch.object(controller.scheduler, "qsub_environment_sha256", return_value="e" * 64),
        mock.patch.object(controller.os.path, "lexists", return_value=False),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=SimpleNamespace(plan={}, runtime_authority={})),
        mock.patch.object(controller, "_validate_original_controls"),
        mock.patch.object(controller, "_r8u_r7d_bounded_prefix", return_value=controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256),
        mock.patch.object(controller, "_r8u_r7d_validate_consumed_evidence", return_value=_evidence()),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller, "_r8u_r7d_login_qstat_snapshot", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_r8u_r7d_process_quiescence", return_value={"status": "PASS"}),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(controller, "_r8u_r7e_old_capacity_diagnosis", return_value={"status": "DIAGNOSIS"}),
        mock.patch.object(controller, "_r8u_r7e_capacity_authority", return_value={"status": "AUTHORITY"}),
        mock.patch.object(controller, "_write_private_json", side_effect=lambda path, value: writes.setdefault(path, value) and "6" * 64),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller.core, "sha256_file", return_value=receipt_sha),
        mock.patch.object(controller.r7d_capacity, "capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity", side_effect=capture),
        mock.patch.object(controller.scheduler, "_capture_qsub", qsub),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        try:
            controller.submit_r8u_r7e_continuation_context_probe(
                capacity_runner=mock.Mock()
            )
        except controller.R8RControllerError as exc:
            assert exc.code == code
        else:
            raise AssertionError("field-specific capacity failure was accepted")
    qsub.assert_not_called()

    output = io.StringIO()
    with (
        mock.patch.object(
            controller,
            "submit_r8u_r7e_continuation_context_probe",
            side_effect=controller.R8RControllerError(code),
        ),
        redirect_stdout(output),
    ):
        assert controller.guarded_main([
            "--submit-r8u-r7e-continuation-context-probe"
        ]) == 78
    assert f"R8U_R7E_STATUS={code}" in output.getvalue()
    assert "BLOCKED_BLOCKED" not in output.getvalue()


def test_r7e_capacity_replay_binds_the_exact_raw_capture_root() -> None:
    receipt = {"status": PASS_STATUS}
    validator = mock.Mock(return_value=receipt)
    run = SimpleNamespace(plan={"fixed": "plan"})
    with (
        mock.patch.object(
            controller, "_load_private_json", return_value=(receipt, b"sealed")
        ),
        mock.patch.object(
            controller.r7d_capacity,
            "validate_fixed_r8u_r7e_tasks17_19_capacity",
            validator,
        ),
    ):
        assert controller._r8u_r7e_validate_capacity_receipt(
            run=run,
            implementation_commit=COMMIT,
            evidence=_evidence(),
        ) == receipt

    assert validator.call_count == 1
    assert validator.call_args.kwargs["raw_capture_root"] == (
        controller.R8U_R7E_RAW_CAPTURE_ROOT
    )


def test_r7e_capacity_namespace_normalizes_only_generic_receipt_errors() -> None:
    receipt_invalid = (
        "BLOCKED_R8U_R7E_CAPACITY_OBSERVATION_RECEIPT_INVALID"
    )
    run = SimpleNamespace(plan={})
    arguments = {
        "run": run,
        "implementation_commit": COMMIT,
        "prefix_receipts": controller.R8U_R7D_PREFIX_FINAL_RECEIPT_SHA256,
        "evidence": _evidence(),
    }
    for generic in (
        controller.R8RControllerError("R8R_RETAINED_CONTROL_INVALID"),
        controller.R8RControllerError("R8R_CONTROL_JSON_INVALID"),
        ValueError("malformed private JSON"),
    ):
        with mock.patch.object(
            controller, "_load_private_json", side_effect=generic
        ):
            try:
                controller._r8u_r7e_validate_capacity_namespace(**arguments)
            except controller.R8RControllerError as exc:
                assert exc.code == receipt_invalid
            else:
                raise AssertionError("generic receipt failure was accepted")

    diagnosis = {"status": "DIAGNOSIS"}
    authority = {"status": "AUTHORITY"}
    preserved_codes = (
        "BLOCKED_R8U_R7E_CAPACITY_OBSERVATION_FINDMNT_PARSE_FAILURE",
        controller.r7d_capacity.R8U_R7E_CAPACITY_STATUS_DEFICIT,
    )
    for code in preserved_codes:
        with (
            mock.patch.object(
                controller,
                "_load_private_json",
                side_effect=((diagnosis, b"d"), (authority, b"a")),
            ),
            mock.patch.object(
                controller,
                "_r8u_r7e_old_capacity_diagnosis",
                return_value=diagnosis,
            ),
            mock.patch.object(
                controller,
                "_r8u_r7e_capacity_authority",
                return_value=authority,
            ),
            mock.patch.object(
                controller,
                "_r8u_r7e_validate_capacity_receipt",
                side_effect=controller.R8RControllerError(code),
            ),
        ):
            try:
                controller._r8u_r7e_validate_capacity_namespace(**arguments)
            except controller.R8RControllerError as exc:
                assert exc.code == code
            else:
                raise AssertionError(f"sealed status {code} was accepted")


def test_all_three_r7e_cli_success_paths_return_without_legacy_fallthrough() -> None:
    cases = (
        (
            "--submit-r8u-r7e-continuation-context-probe",
            "submit_r8u_r7e_continuation_context_probe",
            {
                "status": "R7E_CONTINUATION_CONTEXT_PROBE_SUBMITTED",
                "probe_job_id": "8123456",
                "capacity_status": PASS_STATUS,
                "capacity_receipt_sha256": "a" * 64,
                "continuation_claim_sha256": "b" * 64,
                "capacity_observation_resumed": False,
            },
        ),
        (
            "--adjudicate-r8u-r7e-continuation-context-probe",
            "adjudicate_r8u_r7d_continuation_context_probe",
            {
                "status": "PASS_R7E_CONTINUATION_WORKER_CONTEXT_PROBE",
                "qstat_classification": "PASS_QSTAT_TRANSITIONAL_STATE",
            },
        ),
        (
            "--submit-r8u-r7e-continuation-17-19",
            "submit_r8u_r7e_continuation_17_19",
            {
                "status": "FINAL_TASKS_17_19_AND_FINALIZER_RESUBMITTED",
                "array_job_id": "8234567",
                "finalizer_job_id": "8234568",
                "continuation_receipt_sha256": "c" * 64,
                "finalizer_submission_receipt_sha256": "d" * 64,
            },
        ),
    )
    for option, function_name, result in cases:
        output = io.StringIO()
        with (
            mock.patch.object(controller, function_name, return_value=result),
            mock.patch.object(
                controller,
                "_parser",
                side_effect=AssertionError("legacy parser reached"),
            ),
            redirect_stdout(output),
        ):
            assert controller.guarded_main([option]) == 0
        assert "R8U_R7E_STATUS=" in output.getvalue()


def test_legacy_r7d_submit_is_rejected_after_r7e_claim_before_qsub() -> None:
    qsub = mock.Mock(side_effect=AssertionError("legacy qsub reached"))
    with (
        mock.patch.object(
            controller.os.path,
            "lexists",
            side_effect=lambda path: (
                path == controller.R8U_R7E_CONTINUATION_CLAIM_PATH
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
            controller.submit_r8u_r7d_continuation_17_19()
        except controller.R8RControllerError as exc:
            assert exc.code == (
                "R8U_R7D_CONTINUATION_SUPERSEDED_BY_R7E_CLAIM"
            )
        else:
            raise AssertionError("legacy R7D submit accepted the R7E claim")
    qsub.assert_not_called()


def test_r7e_scientific_submit_is_array_then_held_finalizer_and_seals_both() -> None:
    writes: dict[Path, object] = {}
    qsub_commands: list[list[str]] = []
    claim = {"status": "CLAIM"}

    def load(path: Path) -> tuple[object, bytes]:
        if path == controller.R8U_R7E_CONTINUATION_CLAIM_PATH:
            return claim, b"claim"
        return writes[path], b"sealed"

    def write(path: Path, value: object) -> str:
        writes[path] = value
        return {
            controller.R8U_R7E_ARRAY_SUBMISSION_PATH: "a" * 64,
            controller.R8U_R7E_FINALIZER_SUBMISSION_PATH: "b" * 64,
            controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH: "c" * 64,
        }[path]

    def qsub(_role: str, command: list[str], **_kwargs: object) -> str:
        qsub_commands.append(command)
        return "8234567" if len(qsub_commands) == 1 else "8234568"

    patches = (
        mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
        mock.patch.object(controller, "_current_r8u_r7e_implementation_commit", return_value=COMMIT),
        mock.patch.object(controller.scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, {})),
        mock.patch.object(controller.scheduler, "qsub_environment_sha256", return_value="d" * 64),
        mock.patch.object(controller, "validate_r8u_r7d_scheduler_account_authority", return_value={"qsub_environment_sha256": "d" * 64}),
        mock.patch.object(controller, "_load_fixed_original_run", return_value=SimpleNamespace()),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_chain"),
        mock.patch.object(controller, "_r8u_r7d_validate_probe_terminal"),
        mock.patch.object(controller, "_r8u_r7d_require_tail_pristine"),
        mock.patch.object(controller, "_r8u_r7e_continuation_claim", return_value=claim),
        mock.patch.object(controller.os.path, "lexists", return_value=False),
        mock.patch.object(controller, "_r8u_r7d_login_qstat_snapshot", return_value={"status": "PASS_R8U_R7D_FULL_XML_QSTAT_SNAPSHOT", "qstat_snapshot_count": 1, "truncated_display_name_used": False}),
        mock.patch.object(controller, "_r8u_r7d_process_quiescence"),
        mock.patch.object(controller, "_create_private_directory_no_clobber"),
        mock.patch.object(controller.scheduler, "_capture_qsub", side_effect=qsub),
        mock.patch.object(controller.core, "sha256_file", return_value="e" * 64),
        mock.patch.object(controller, "_r8u_r7e_array_submission", return_value={"status": "ARRAY"}),
        mock.patch.object(controller, "_r8u_r7e_finalizer_submission", return_value={"status": "FINALIZER"}),
        mock.patch.object(controller, "_r8u_r7e_continuation_submission", return_value={"status": "COMBINED"}),
        mock.patch.object(controller, "_write_private_json", side_effect=write),
        mock.patch.object(controller, "_load_private_json", side_effect=load),
        mock.patch.object(controller, "_r8u_r7d_validate_continuation_chain"),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = controller.submit_r8u_r7e_continuation_17_19(
            qsub_runner=mock.Mock(),
            qstat_runner=mock.Mock(),
            process_runner=mock.Mock(),
        )

    assert len(qsub_commands) == 2
    array, finalizer = qsub_commands
    assert array[array.index("-t") + 1] == "17-19"
    assert array[array.index("-tc") + 1] == "1"
    assert finalizer[finalizer.index("-hold_jid") + 1] == "8234567"
    assert controller.R8U_R7E_ARRAY_SUBMISSION_PATH in writes
    assert controller.R8U_R7E_FINALIZER_SUBMISSION_PATH in writes
    assert controller.R8U_R7D_CONTINUATION_SUBMISSION_PATH in writes
    assert result["array_job_id"] == "8234567"
    assert result["finalizer_job_id"] == "8234568"
    assert result["finalizer_submission_receipt_sha256"] == "b" * 64
    assert result["total_new_qsub_submissions"] == 3
