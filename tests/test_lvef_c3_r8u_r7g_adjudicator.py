#!/usr/bin/env python3
"""Dependency-light boundary and sequencing tests for the R7G adjudicator.

The tests are wholly synthetic.  They do not contact SCC or Grid Engine and
do not open any scientific artifact, DICOM body, embedding, or model output.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack, redirect_stdout
import inspect
import io
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from typing import Iterable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7g_accounting as accounting
import lvef_c3_r8u_r7g_evidence as evidence
import lvef_c3_r8u_r7g_terminal_adjudicator as adjudicator


ADJUDICATION_COMMIT = "a" * 40
AUTHORITY_SHA256 = "b" * 64
PROBE_SHA256 = "c" * 64
FIXED_PLAN = {"synthetic_fixed_plan": True}
REPOSITORY_STATE = {
    "branch": adjudicator.BRANCH,
    "local_head": ADJUDICATION_COMMIT,
    "origin_head": ADJUDICATION_COMMIT,
    "scc_head": ADJUDICATION_COMMIT,
    "local_tracked_clean": True,
    "scc_tracked_clean": True,
}

SCIENTIFIC_EXECUTION_CALLS = {
    "execute_exact_batch_download",
    "run_production_dicom_extraction",
    "run_production_echoprime",
    "run_batch_task",
    "run_cross_batch_finalizer",
    "run_r8u_r7_continuation_array_task",
    "run_r8u_r7d_continuation_array_task",
    "run_r8u_r7_continuation_finalizer",
    "run_r8u_r7d_continuation_finalizer",
    "preserve_lvef_c3_production_batch",
    "retire_lvef_c3_extracted_cache",
    "fit",
    "fit_model",
    "predict",
    "predict_proba",
}
LIVE_CONTROL_PLANE_CALLS = {
    "qsub",
    "_capture_qsub",
    "submit_r8u_r7f_continuation_17_19",
    "build_worker_scheduler_context",
    "validate_r8u_r7_continuation_worker_submission",
    "validate_r8u_r7d_continuation_worker_submission",
    "_r8u_r7_qstat_projection",
    "_r8u_r5_qstat_projection",
}
FORBIDDEN_DYNAMIC_CALLS = {"eval", "exec", "__import__"}
FORBIDDEN_DIRECT_IMPORTS = {
    "lvef_c3_full_sequential",
    "lvef_c3_full_scheduler",
    "lvef_c3_r8r_recovery_continuation",
    "run_production_dicom_extraction",
    "run_production_echoprime",
    "preserve_lvef_c3_production_batch",
    "retire_lvef_c3_extracted_cache",
    "finalize_lvef_c3_production",
}


def _terminal_authority() -> dict[str, object]:
    return {
        "r7f_authority_receipt_sha256": dict(
            accounting.EXPECTED_R7F_RECEIPT_SHA256
        ),
        "probe_terminal_receipt_sha256": PROBE_SHA256,
    }


def _specs() -> tuple[accounting.FixedAccountingSpec, ...]:
    result: list[accounting.FixedAccountingSpec] = []
    for task_id in (17, 18, 19):
        basename = f"array.o{accounting.ARRAY_JOB_ID}.{task_id}"
        result.append(
            accounting.FixedAccountingSpec(
                job_kind="array_task",
                job_id=accounting.ARRAY_JOB_ID,
                task_id=task_id,
                expected_job_role=accounting.ARRAY_ROLE,
                expected_job_name=f"array-{task_id}",
                expected_owner="fixed-owner",
                receipt_path=Path(f"/fixed/accounting/task_{task_id}.json"),
                scheduler_log_path=Path(f"/fixed/logs/{basename}"),
                scheduler_log_basename=basename,
            )
        )
    basename = f"finalizer.o{accounting.FINALIZER_JOB_ID}"
    result.append(
        accounting.FixedAccountingSpec(
            job_kind="finalizer",
            job_id=accounting.FINALIZER_JOB_ID,
            task_id=None,
            expected_job_role=accounting.FINALIZER_ROLE,
            expected_job_name="finalizer",
            expected_owner="fixed-owner",
            receipt_path=Path("/fixed/accounting/finalizer.json"),
            scheduler_log_path=Path(f"/fixed/logs/{basename}"),
            scheduler_log_basename=basename,
        )
    )
    return tuple(result)


def _accounting_result(
    spec: accounting.FixedAccountingSpec, *, classification: str = "PASS"
) -> accounting.AccountingReceiptResult:
    return accounting.AccountingReceiptResult(
        receipt={
            "failed": 0 if classification == "PASS" else 1,
            "exit_status": 0 if classification == "PASS" else 78,
            "wall_seconds": 11,
            "terminal_classification": classification,
            "observed_qacct_job_name": spec.expected_job_name,
        },
        receipt_sha256=f"{17 if spec.task_id is None else spec.task_id:064x}",
        created=True,
        qacct_query_count=1,
    )


def _pass_log() -> dict[str, str]:
    return {
        "status": "FIXED_PASS_TERMINAL_LOG_VALIDATED",
        "failure_scope": "NOT_APPLICABLE",
        "first_failed_stage": "NOT_APPLICABLE",
    }


def _install_fixed_start(
    stack: ExitStack,
    *,
    repository: mock.Mock | None = None,
) -> tuple[accounting.FixedAccountingSpec, ...]:
    fixed_specs = _specs()
    if repository is None:
        repository = mock.Mock(
            return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE))
        )
    stack.enter_context(
        mock.patch.object(adjudicator, "_repository_authority", repository)
    )
    stack.enter_context(
        mock.patch.object(
            evidence, "load_fixed_plan", return_value=FIXED_PLAN
        )
    )
    stack.enter_context(
        mock.patch.object(
            accounting,
            "preflight_fixed_terminal_authority",
            return_value=_terminal_authority(),
            create=True,
        )
    )
    stack.enter_context(
        mock.patch.object(
            accounting,
            "ensure_terminal_authority",
            return_value=accounting.TerminalAuthorityResult(
                authority=_terminal_authority(),
                authority_sha256=AUTHORITY_SHA256,
                created=True,
            ),
        )
    )
    stack.enter_context(
        mock.patch.object(
            accounting,
            "validate_fixed_scheduler_accounting_environment",
            return_value={"USER": "fixed-owner"},
        )
    )
    stack.enter_context(
        mock.patch.object(
            accounting, "fixed_accounting_specs", return_value=fixed_specs
        )
    )
    return fixed_specs


def _install_successful_reconciliation(
    stack: ExitStack,
    *,
    quiescence_result: object,
    finalizer_classification: str = "PASS",
    finalizer_scope: str = "NOT_APPLICABLE",
) -> tuple[mock.Mock, mock.Mock, mock.Mock, mock.Mock, mock.Mock]:
    """Install all synthetic operations after fixed repository authority."""

    accounting_events: list[int | None] = []

    def obtain(
        spec: accounting.FixedAccountingSpec, **_kwargs: object
    ) -> accounting.AccountingReceiptResult:
        accounting_events.append(spec.task_id)
        classification = (
            finalizer_classification if spec.task_id is None else "PASS"
        )
        return _accounting_result(spec, classification=classification)

    accounting_mock = stack.enter_context(
        mock.patch.object(
            accounting, "reuse_or_query_fixed_accounting", side_effect=obtain
        )
    )

    def inspect_log(
        spec: accounting.FixedAccountingSpec,
        _receipt: object,
        **_kwargs: object,
    ) -> dict[str, str]:
        if spec.task_id is None and finalizer_classification == "FAIL":
            return {
                "status": "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED",
                "failure_scope": finalizer_scope,
                "first_failed_stage": "FINALIZER_CONTROL_PLANE",
            }
        return _pass_log()

    log_mock = stack.enter_context(
        mock.patch.object(
            adjudicator.terminal_logs,
            "inspect_fixed_terminal_logs",
            side_effect=inspect_log,
        )
    )
    tail_mock = stack.enter_context(
        mock.patch.object(
            adjudicator,
            "_load_tail_batch",
            side_effect=lambda **values: {"ordinal": values["ordinal"]},
        )
    )
    prefix_mock = stack.enter_context(
        mock.patch.object(
            evidence,
            "load_batch_metadata",
            side_effect=lambda ordinal, **_kwargs: {
                "ordinal": ordinal,
                "batch_finalization_receipt_sha256": f"{ordinal + 1:064x}",
            },
        )
    )
    stack.enter_context(mock.patch.object(adjudicator, "_validate_prefix"))
    stack.enter_context(
        mock.patch.object(
            adjudicator.metadata,
            "validate_batch_metadata_partition",
            return_value={
                "aggregate_totals": {"finalized_batches": 19},
                "study_partition": {"selected_studies": 4_530},
            },
        )
    )
    stack.enter_context(
        mock.patch.object(
            evidence,
            "load_original_cohort_finalization_receipt",
            return_value=({"status": "PASS_PRODUCTION_C3_FINALIZED"}, "1" * 64),
        )
    )
    stack.enter_context(
        mock.patch.object(evidence, "fixed_cache_topology", return_value={})
    )
    cohort_build_mock = stack.enter_context(
        mock.patch.object(
            adjudicator.metadata,
            "build_cohort_finalization_receipt",
            return_value={"status": "PASS_FULL_COHORT_FINALIZED"},
        )
    )
    stack.enter_context(
        mock.patch.object(
            adjudicator.metadata,
            "publish_cohort_finalization_receipt",
            return_value=("d" * 64, True),
        )
    )
    quiescence_patch = {
        "side_effect": quiescence_result
    } if isinstance(quiescence_result, BaseException) else {
        "return_value": quiescence_result
    }
    stack.enter_context(
        mock.patch.object(
            adjudicator.quiescence,
            "capture_fixed_quiescence",
            **quiescence_patch,
        )
    )
    stack.enter_context(
        mock.patch.object(
            adjudicator, "_receipt_timestamp", return_value="2026-09-07T12:00:00Z"
        )
    )
    stack.enter_context(
        mock.patch.object(
            adjudicator.metadata,
            "build_post_reconstruction_lock_receipt",
            return_value={"status": "PASS_POST_RECONSTRUCTION_LOCKED"},
        )
    )
    stack.enter_context(
        mock.patch.object(
            adjudicator.metadata,
            "publish_post_reconstruction_lock_receipt",
            return_value=("e" * 64, True),
        )
    )
    accounting_mock.accounting_events = accounting_events
    return (
        accounting_mock,
        log_mock,
        tail_mock,
        prefix_mock,
        cohort_build_mock,
    )


def _expect_stop(status: str, code: str) -> adjudicator.R7GTerminalStop:
    try:
        adjudicator.adjudicate_fixed_r7f_existing_jobs()
    except adjudicator.R7GTerminalStop as exc:
        assert exc.status == status
        assert exc.code == code
        assert exc.report["status"] == status
        assert exc.report["error_code"] == code
        return exc
    raise AssertionError(f"expected terminal stop {status}: {code}")


def test_repository_authority_accepts_only_direct_child_of_r7g_base() -> None:
    head = ADJUDICATION_COMMIT

    def direct_child_git(arguments: tuple[str, ...]) -> str:
        values = {
            ("branch", "--show-current"): adjudicator.BRANCH,
            ("rev-parse", "HEAD"): head,
            ("rev-parse", f"origin/{adjudicator.BRANCH}"): head,
            ("rev-list", "--parents", "-n", "1", head): (
                f"{head} "
                f"{adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT}"
            ),
            (
                "rev-list",
                "--count",
                f"{adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT}"
                f"..{head}",
            ): "1",
            ("status", "--porcelain=v1", "--untracked-files=no"): "",
            (
                "merge-base",
                "--is-ancestor",
                accounting.RUNTIME_IMPLEMENTATION_COMMIT,
                head,
            ): "",
            (
                "merge-base",
                "--is-ancestor",
                adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
                head,
            ): "",
            (
                "merge-base",
                "--is-ancestor",
                accounting.SCIENTIFIC_COMMIT,
                head,
            ): "",
        }
        return values[arguments]

    environment = {
        adjudicator.COORDINATOR_LOCAL_HEAD_ENV: head,
        adjudicator.COORDINATOR_LOCAL_CLEAN_ENV: "YES",
    }
    with mock.patch.object(
        adjudicator, "SCC_REPOSITORY_ROOT", adjudicator.REPOSITORY_ROOT
    ), mock.patch.object(
        adjudicator, "_git", side_effect=direct_child_git
    ), mock.patch.dict(os.environ, environment, clear=False):
        observed_head, state = adjudicator._repository_authority()
    assert observed_head == head
    assert state == REPOSITORY_STATE

    unrelated_parent = "f" * 40

    def non_direct_descendant_git(arguments: tuple[str, ...]) -> str:
        if arguments == ("rev-list", "--parents", "-n", "1", head):
            return f"{head} {unrelated_parent}"
        if arguments == (
            "rev-list",
            "--count",
            f"{adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT}..{head}",
        ):
            return "2"
        return direct_child_git(arguments)

    with mock.patch.object(
        adjudicator, "SCC_REPOSITORY_ROOT", adjudicator.REPOSITORY_ROOT
    ), mock.patch.object(
        adjudicator, "_git", side_effect=non_direct_descendant_git
    ), mock.patch.dict(os.environ, environment, clear=False):
        try:
            adjudicator._repository_authority()
        except RuntimeError as exc:
            assert str(exc) == "R8U_R7G_GIT_AUTHORITY_INVALID"
        else:
            raise AssertionError("a non-direct descendant was accepted")


def test_closed_cli_accepts_only_the_two_exact_fixed_modes() -> None:
    invalid = (
        [],
        ["--help"],
        [adjudicator.ENTRYPOINT_FLAG, "extra"],
        ["--job-id", accounting.ARRAY_JOB_ID],
        ["--receipt-path", "/caller/selected.json"],
    )
    for arguments in invalid:
        with mock.patch.object(
            adjudicator, "adjudicate_fixed_r7f_existing_jobs"
        ) as run, redirect_stdout(io.StringIO()):
            assert adjudicator.guarded_main(list(arguments)) == 64
            run.assert_not_called()

    with mock.patch.object(
        adjudicator,
        "adjudicate_fixed_r7f_existing_jobs",
        return_value={"status": adjudicator.SUCCESS},
    ) as run, redirect_stdout(io.StringIO()):
        assert adjudicator.guarded_main([adjudicator.ENTRYPOINT_FLAG]) == 0
        run.assert_called_once_with()

    output = io.StringIO()
    with mock.patch.object(
        adjudicator,
        "_repository_authority",
        return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE)),
    ) as repository, mock.patch.object(
        adjudicator, "_preflight_fixed_authorities", return_value=FIXED_PLAN
    ) as preflight, redirect_stdout(output):
        assert adjudicator.guarded_main([adjudicator.PREFLIGHT_FLAG]) == 0
    assert output.getvalue() == adjudicator.PREFLIGHT_PASS + "\n"
    repository.assert_called_once_with()
    preflight.assert_called_once_with(
        adjudication_implementation_commit=ADJUDICATION_COMMIT
    )


def test_preflight_is_read_only_and_exercises_compact_output_policy() -> None:
    with mock.patch.object(
        evidence, "load_fixed_plan", return_value=FIXED_PLAN
    ) as load_plan, mock.patch.object(
        accounting,
        "preflight_fixed_terminal_authority",
        return_value=_terminal_authority(),
        create=True,
    ) as preflight, mock.patch.object(
        accounting, "ensure_terminal_authority"
    ) as ensure, mock.patch.object(
        accounting, "ensure_fixed_accounting_directory"
    ) as ensure_directory, mock.patch.object(
        accounting, "reuse_or_query_fixed_accounting"
    ) as query:
        observed = adjudicator._preflight_fixed_authorities(
            adjudication_implementation_commit=ADJUDICATION_COMMIT
        )

    assert observed is FIXED_PLAN
    load_plan.assert_called_once_with()
    preflight.assert_called_once_with(
        plan=FIXED_PLAN,
        adjudication_implementation_commit=ADJUDICATION_COMMIT,
    )
    ensure.assert_not_called()
    ensure_directory.assert_not_called()
    query.assert_not_called()

    with mock.patch.object(
        adjudicator.metadata, "canonical_json_bytes", return_value=b"{}"
    ):
        try:
            adjudicator._validate_compact_output_machinery()
        except RuntimeError as exc:
            assert str(exc) == (
                "R8U_R7G_CURRENT_OUTPUT_CANONICAL_BYTES_INVALID"
            )
        else:
            raise AssertionError("non-compact output machinery was accepted")


def test_initial_report_binds_runtime_base_and_r1_authorities() -> None:
    report = adjudicator._initial_report(ADJUDICATION_COMMIT)
    assert report["preceding_result"] == (
        "R7G_HISTORICAL_PRODUCER_SERIALIZATION_COMPATIBILITY_DEFECT"
    )
    assert report["starting_commit"] == (
        adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
    )
    assert report["runtime_implementation_commit"] == (
        accounting.RUNTIME_IMPLEMENTATION_COMMIT
    )
    assert report["base_adjudication_implementation_commit"] == (
        adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
    )
    assert report["adjudication_implementation_commit"] == ADJUDICATION_COMMIT


def test_terminal_statuses_are_exactly_the_r1_closed_set() -> None:
    assert {
        adjudicator.SUCCESS,
        adjudicator.IMPLEMENTATION_STOP,
        adjudicator.HISTORICAL_STOP,
        adjudicator.ACCOUNTING_STOP,
        adjudicator.TASK_STOP,
        adjudicator.COHORT_STOP,
        adjudicator.LOCK_STOP,
    } == {
        "FULL_SELECTED_COHORT_RECONSTRUCTION_FINALIZED_AND_LOCKED",
        "STOPPED_ON_R7G_R1_IMPLEMENTATION_OR_SYNC_FAILURE",
        "STOPPED_ON_HISTORICAL_AUTHORITY_HASH_OR_SCHEMA_CONTRADICTION",
        "STOPPED_ON_ACCOUNTING_PENDING",
        "STOPPED_ON_FINAL_TAIL_TASK_FAILURE",
        "STOPPED_ON_COHORT_FINALIZATION_CONTRADICTION",
        "STOPPED_ON_POST_RECONSTRUCTION_LOCK_CONTRADICTION",
    }


def test_implementation_and_accounting_failures_have_distinct_stops() -> None:
    with mock.patch.object(
        adjudicator,
        "_repository_authority",
        side_effect=RuntimeError("R8U_R7G_GIT_AUTHORITY_INVALID"),
    ), mock.patch.object(accounting, "ensure_terminal_authority") as authority:
        _expect_stop(
            adjudicator.IMPLEMENTATION_STOP,
            "R8U_R7G_GIT_AUTHORITY_INVALID",
        )
        authority.assert_not_called()

    with ExitStack() as stack:
        _install_fixed_start(stack)
        query = stack.enter_context(
            mock.patch.object(
                accounting,
                "reuse_or_query_fixed_accounting",
                side_effect=accounting.R7GAccountingError(
                    "R8U_R7G_ACCOUNTING_NOT_AVAILABLE"
                ),
            )
        )
        logs = stack.enter_context(
            mock.patch.object(
                adjudicator.terminal_logs, "inspect_fixed_terminal_logs"
            )
        )
        exc = _expect_stop(
            adjudicator.ACCOUNTING_STOP,
            "R8U_R7G_ACCOUNTING_NOT_AVAILABLE",
        )
        assert exc.report["accounting_pending_record"] == "task_17"
        assert query.call_count == 1
        logs.assert_not_called()


def test_unrecognized_accounting_defect_is_implementation_not_pending() -> None:
    with ExitStack() as stack:
        _install_fixed_start(stack)
        stack.enter_context(
            mock.patch.object(
                accounting,
                "reuse_or_query_fixed_accounting",
                side_effect=accounting.R7GAccountingError(
                    "R8U_R7G_ACCOUNTING_RECEIPT_SCHEMA_INVALID"
                ),
            )
        )
        exc = _expect_stop(
            adjudicator.IMPLEMENTATION_STOP,
            "R8U_R7G_ACCOUNTING_RECEIPT_SCHEMA_INVALID",
        )
        assert exc.report["structural_function"] == (
            "accounting.reuse_or_query_fixed_accounting"
        )


def test_historical_preflight_failure_precedes_publication_and_qacct() -> None:
    events: list[str] = []

    def load_plan() -> dict[str, bool]:
        events.append("plan")
        return FIXED_PLAN

    def fail_preflight(**_kwargs: object) -> None:
        events.append("preflight")
        raise accounting.R7GAccountingError(
            "R8U_R7G_AUTHORITY_HASH_MISMATCH"
        )

    with mock.patch.object(
        adjudicator,
        "_repository_authority",
        return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE)),
    ), mock.patch.object(
        evidence, "load_fixed_plan", side_effect=load_plan
    ), mock.patch.object(
        accounting,
        "preflight_fixed_terminal_authority",
        side_effect=fail_preflight,
        create=True,
    ) as preflight, mock.patch.object(
        accounting, "ensure_terminal_authority"
    ) as ensure, mock.patch.object(
        accounting, "reuse_or_query_fixed_accounting"
    ) as query:
        exc = _expect_stop(
            adjudicator.HISTORICAL_STOP,
            "R8U_R7G_AUTHORITY_HASH_MISMATCH",
        )

    assert events == ["plan", "preflight"]
    preflight.assert_called_once_with(
        plan=FIXED_PLAN,
        adjudication_implementation_commit=ADJUDICATION_COMMIT,
    )
    ensure.assert_not_called()
    query.assert_not_called()
    assert exc.report["first_failed_stage"] == "AUTHORITY_PREFLIGHT"


def test_tasks_are_adjudicated_independently_and_stop_in_fixed_order() -> None:
    events: list[str] = []

    def obtain(
        spec: accounting.FixedAccountingSpec, **_kwargs: object
    ) -> accounting.AccountingReceiptResult:
        events.append(f"accounting:{spec.task_id}")
        classification = "FAIL" if spec.task_id == 18 else "PASS"
        return _accounting_result(spec, classification=classification)

    def inspect_log(
        spec: accounting.FixedAccountingSpec,
        _receipt: object,
        **_kwargs: object,
    ) -> dict[str, str]:
        events.append(f"log:{spec.task_id}")
        if spec.task_id == 18:
            return {
                "status": "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED",
                "failure_scope": "SUBSTANTIVE",
                "first_failed_stage": "ECHOPRIME_EMBEDDING",
            }
        return _pass_log()

    def load_tail(**values: object) -> dict[str, object]:
        events.append(f"batch:{values['task_id']}")
        return {"ordinal": values["ordinal"]}

    with ExitStack() as stack:
        _install_fixed_start(stack)
        stack.enter_context(
            mock.patch.object(
                accounting,
                "reuse_or_query_fixed_accounting",
                side_effect=obtain,
            )
        )
        stack.enter_context(
            mock.patch.object(
                adjudicator.terminal_logs,
                "inspect_fixed_terminal_logs",
                side_effect=inspect_log,
            )
        )
        tail = stack.enter_context(
            mock.patch.object(
                adjudicator, "_load_tail_batch", side_effect=load_tail
            )
        )
        exc = _expect_stop(
            adjudicator.TASK_STOP,
            "R8U_R7G_CURRENT_TASK_ACCOUNTING_NONZERO",
        )
    assert events == [
        "accounting:17",
        "log:17",
        "batch:17",
        "accounting:18",
        "log:18",
    ]
    assert tail.call_count == 1
    assert exc.report["first_failed_task"] == 18
    assert exc.report["first_failed_batch"] == "c3_batch_017"
    assert exc.report["first_failed_stage"] == "ECHOPRIME_EMBEDDING"


def test_finalizer_substantive_failure_is_cohort_contradiction() -> None:
    def obtain(
        spec: accounting.FixedAccountingSpec, **_kwargs: object
    ) -> accounting.AccountingReceiptResult:
        classification = "FAIL" if spec.task_id is None else "PASS"
        return _accounting_result(spec, classification=classification)

    def inspect_log(
        spec: accounting.FixedAccountingSpec,
        _receipt: object,
        **_kwargs: object,
    ) -> dict[str, str]:
        if spec.task_id is None:
            return {
                "status": "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED",
                "failure_scope": "SUBSTANTIVE",
                "first_failed_stage": "COHORT_FINALIZER",
            }
        return _pass_log()

    with ExitStack() as stack:
        _install_fixed_start(stack)
        query = stack.enter_context(
            mock.patch.object(
                accounting,
                "reuse_or_query_fixed_accounting",
                side_effect=obtain,
            )
        )
        stack.enter_context(
            mock.patch.object(
                adjudicator.terminal_logs,
                "inspect_fixed_terminal_logs",
                side_effect=inspect_log,
            )
        )
        stack.enter_context(
            mock.patch.object(
                adjudicator,
                "_load_tail_batch",
                side_effect=lambda **values: {"ordinal": values["ordinal"]},
            )
        )
        prefix = stack.enter_context(
            mock.patch.object(evidence, "load_batch_metadata")
        )
        exc = _expect_stop(
            adjudicator.COHORT_STOP,
            "R8U_R7G_FINALIZER_SUBSTANTIVE_FAILURE",
        )
    assert query.call_count == 4
    prefix.assert_not_called()
    assert exc.report["first_failed_stage"] == "COHORT_FINALIZER"


def test_full_success_allows_affirmative_finalizer_control_plane_case() -> None:
    repository = mock.Mock(
        return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE))
    )
    with ExitStack() as stack:
        _install_fixed_start(stack, repository=repository)
        accounting_mock, log_mock, tail_mock, prefix_mock, cohort_build_mock = (
            _install_successful_reconciliation(
                stack,
                quiescence_result=SimpleNamespace(scheduler_state={}),
                finalizer_classification="FAIL",
                finalizer_scope="CONTROL_PLANE_ONLY",
            )
        )
        result = adjudicator.adjudicate_fixed_r7f_existing_jobs()

    assert accounting_mock.accounting_events == [17, 18, 19, None]
    assert accounting_mock.call_count == 4
    assert log_mock.call_count == 4
    assert [call.kwargs["task_id"] for call in tail_mock.call_args_list] == [
        17,
        18,
        19,
    ]
    assert [call.args[0] for call in prefix_mock.call_args_list] == list(
        range(16)
    )
    assert repository.call_count == 2
    assert cohort_build_mock.call_args.kwargs[
        "base_adjudication_implementation_commit"
    ] == adjudicator.R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
    assert result["status"] == adjudicator.SUCCESS
    assert result["current_r7f_task_runtime_status"] == "ADJUDICATED"
    assert result["finalized_batches"] == 19
    assert result["original_cohort_finalization_receipt"] == "VALID"
    assert result["cohort_finalization_mode"] == "R7G_METADATA_ONLY_PUBLICATION"
    assert result["cohort_finalization_receipt_sha256"] == "d" * 64
    assert result["post_reconstruction_lock_receipt_sha256"] == "e" * 64
    assert result["new_control_plane_qsub_submissions"] == 0


def test_quiescence_defect_is_post_reconstruction_lock_stop() -> None:
    with ExitStack() as stack:
        _install_fixed_start(stack)
        _install_successful_reconciliation(
            stack,
            quiescence_result=adjudicator.quiescence.R7GQuiescenceError(
                "R8U_R7G_FIXED_QUIESCENCE_INVALID"
            ),
        )
        exc = _expect_stop(
            adjudicator.LOCK_STOP,
            "R8U_R7G_FIXED_QUIESCENCE_INVALID",
        )
    assert exc.report["cohort_finalization_receipt_valid"] is True
    assert exc.report["post_reconstruction_lock_review"] == "NOT_RUN"


def _parse_entrypoint() -> tuple[str, ast.Module]:
    path = SCRIPTS / "lvef_c3_r8u_r7g_terminal_adjudicator.py"
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(path))


def _functions(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _dotted_name(node: ast.AST) -> str:
    pieces: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        pieces.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        pieces.append(current.id)
    return ".".join(reversed(pieces))


def _call_leaves(node: ast.AST) -> set[str]:
    return {
        name.rsplit(".", 1)[-1]
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        if (name := _dotted_name(candidate.func))
    }


def _reachable_local_functions(
    tree: ast.Module, roots: Iterable[str]
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    functions = _functions(tree)
    pending = list(roots)
    visited: set[str] = set()
    result: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        assert name in functions, f"missing local function: {name}"
        visited.add(name)
        function = functions[name]
        result.append(function)
        for called in _call_leaves(function):
            if called in functions and called not in visited:
                pending.append(called)
    return tuple(result)


def _argument_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    arguments = function.args
    return [
        item.arg
        for item in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ]


def test_entrypoint_has_no_caller_selected_job_task_or_path_surface() -> None:
    source, tree = _parse_entrypoint()
    functions = _functions(tree)
    adjudicate = functions["adjudicate_fixed_r7f_existing_jobs"]
    guarded = functions["guarded_main"]
    assert _argument_names(adjudicate) == []
    assert adjudicate.args.vararg is None and adjudicate.args.kwarg is None
    assert _argument_names(guarded) == ["arguments"]
    assert guarded.args.vararg is None and guarded.args.kwarg is None
    assert adjudicator.ENTRYPOINT_FLAG in source
    assert "argparse" not in source

    forbidden_option_literals = {
        "--job",
        "--job-id",
        "--task",
        "--task-id",
        "--attempt",
        "--plan",
        "--path",
        "--receipt-path",
        "--log-path",
    }
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not forbidden_option_literals & string_literals

    spec_calls = [
        node
        for node in ast.walk(adjudicate)
        if isinstance(node, ast.Call)
        and _dotted_name(node.func) == "accounting.fixed_accounting_specs"
    ]
    assert len(spec_calls) == 1
    assert len(spec_calls[0].args) == 1 and not spec_calls[0].keywords
    assert isinstance(spec_calls[0].args[0], ast.Name)
    assert spec_calls[0].args[0].id == "terminal_authority"

    calls = [node for node in ast.walk(adjudicate) if isinstance(node, ast.Call)]
    preflight_calls = [
        node
        for node in calls
        if _dotted_name(node.func) == "_preflight_fixed_authorities"
    ]
    ensure_calls = [
        node
        for node in calls
        if _dotted_name(node.func) == "accounting.ensure_terminal_authority"
    ]
    qacct_calls = [
        node
        for node in calls
        if _dotted_name(node.func) == "_obtain_accounting"
    ]
    assert len(preflight_calls) == len(ensure_calls) == 1
    assert len(qacct_calls) == 2
    assert preflight_calls[0].lineno < ensure_calls[0].lineno
    assert ensure_calls[0].lineno < min(node.lineno for node in qacct_calls)
    ensure_keywords = {item.arg: item.value for item in ensure_calls[0].keywords}
    assert set(ensure_keywords) == {
        "plan",
        "adjudication_implementation_commit",
    }
    assert isinstance(ensure_keywords["plan"], ast.Name)
    assert ensure_keywords["plan"].id == "plan"


def test_entrypoint_has_no_reachable_scientific_or_submission_path() -> None:
    _source, tree = _parse_entrypoint()
    reachable = _reachable_local_functions(
        tree, ("adjudicate_fixed_r7f_existing_jobs", "guarded_main")
    )
    calls = set().union(*(_call_leaves(function) for function in reachable))
    assert not calls & SCIENTIFIC_EXECUTION_CALLS
    assert not calls & LIVE_CONTROL_PLANE_CALLS
    assert not calls & FORBIDDEN_DYNAMIC_CALLS

    imported_modules: set[str] = set()
    star_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            if any(alias.name == "*" for alias in node.names):
                star_imports.append(node.module or "")
    assert not star_imports
    assert not imported_modules & FORBIDDEN_DIRECT_IMPORTS

    for function in reachable:
        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and _dotted_name(node.func) == "os.environ.get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                continue
            assert node.args[0].value in {
                adjudicator.COORDINATOR_LOCAL_HEAD_ENV,
                adjudicator.COORDINATOR_LOCAL_CLEAN_ENV,
            }


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
