#!/usr/bin/env python3
"""Dependency-light sequencing tests for the fixed R7C adjudicator."""
from __future__ import annotations

from contextlib import ExitStack
import inspect
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7c_accounting as accounting
import lvef_c3_r8u_r7c_evidence as evidence
import lvef_c3_r8u_r7c_terminal_adjudicator as adjudicator


ADJUDICATION_COMMIT = "a" * 40
REPOSITORY_STATE = {
    "branch": adjudicator.BRANCH,
    "local_head": ADJUDICATION_COMMIT,
    "origin_head": ADJUDICATION_COMMIT,
    "scc_head": ADJUDICATION_COMMIT,
    "local_tracked_clean": True,
    "scc_tracked_clean": True,
}


def _accounting_result(*, classification: str) -> accounting.AccountingReceiptResult:
    receipt = {
        "failed": 0,
        "exit_status": 0 if classification == "PASS" else 78,
        "wall_seconds": 11,
        "terminal_classification": classification,
        "observed_qacct_job_name": accounting.ARRAY_JOB_NAME,
    }
    return accounting.AccountingReceiptResult(
        receipt=receipt,
        receipt_sha256="b" * 64,
        created=True,
        qacct_query_count=1,
    )


def _common_patches() -> tuple[mock._patch, ...]:
    return (
        mock.patch.object(
            adjudicator,
            "_repository_authority",
            return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE)),
        ),
        mock.patch.object(
            accounting, "validate_fixed_continuation_receipt", return_value="c" * 64
        ),
        mock.patch.object(
            accounting, "ensure_fixed_accounting_directory", return_value=None
        ),
        mock.patch.object(
            accounting,
            "validate_fixed_scheduler_accounting_environment",
            return_value={"USER": accounting.OWNER},
        ),
    )


def test_task17_nonzero_is_independent_failure_and_stops_before_batch_read() -> None:
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], mock.patch.object(
        accounting,
        "reuse_or_query_fixed_accounting",
        return_value=_accounting_result(classification="FAIL"),
    ) as query, mock.patch.object(
        adjudicator.terminal_logs,
        "inspect_fixed_terminal_logs",
        return_value={
            "status": "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED",
            "failure_scope": "CONTROL_PLANE_ONLY",
            "first_failure_marker": (
                "R8U_R7_STATUS=BLOCKED_SCHEDULER_JOB_ROLE_MISMATCH"
            ),
            "first_failed_stage": "NOT_REPORTED",
        },
    ), mock.patch.object(evidence, "load_fixed_plan") as plan_loader:
        try:
            adjudicator.adjudicate_fixed_existing_jobs()
        except adjudicator.R7CTerminalStop as exc:
            assert exc.status == adjudicator.TRUE_FAILURE_STOP
            assert exc.report["first_failed_task"] == 17
            assert exc.report["accounting"]["task_17"]["exit_status"] == 78
        else:
            raise AssertionError("nonzero Task-17 accounting did not stop")
    assert query.call_count == 1
    plan_loader.assert_not_called()


def test_batch17_contradiction_stops_before_task18_query() -> None:
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], mock.patch.object(
        accounting,
        "reuse_or_query_fixed_accounting",
        return_value=_accounting_result(classification="PASS"),
    ) as query, mock.patch.object(
        adjudicator.terminal_logs,
        "inspect_fixed_terminal_logs",
        return_value={
            "status": "FIXED_PASS_TERMINAL_LOG_VALIDATED",
            "failure_scope": "NOT_APPLICABLE",
            "first_failed_stage": "NOT_APPLICABLE",
        },
    ), mock.patch.object(
        evidence, "load_fixed_plan", return_value={}
    ), mock.patch.object(
        evidence,
        "load_batch_metadata",
        side_effect=evidence.R7CEvidenceError("R8U_R7C_BATCH_RECEIPT_INVALID"),
    ):
        try:
            adjudicator.adjudicate_fixed_existing_jobs()
        except adjudicator.R7CTerminalStop as exc:
            assert exc.status == adjudicator.BATCH_STOP
            assert exc.report["first_failed_batch"] == "c3_batch_016"
        else:
            raise AssertionError("missing Batch-17 authority did not stop")
    assert query.call_count == 1


def test_closed_main_rejects_every_nonfixed_argument_without_adjudicating() -> None:
    for arguments in ([], ["--help"], [adjudicator.ENTRYPOINT_FLAG, "extra"]):
        with mock.patch.object(adjudicator, "adjudicate_fixed_existing_jobs") as run:
            assert adjudicator.guarded_main(arguments) == 64
            run.assert_not_called()


def test_repository_authority_requires_exact_one_commit_and_local_attestation() -> None:
    head = ADJUDICATION_COMMIT

    def fixed_git(arguments: tuple[str, ...]) -> str:
        values = {
            ("branch", "--show-current"): adjudicator.BRANCH,
            ("rev-parse", "HEAD"): head,
            ("rev-parse", f"origin/{adjudicator.BRANCH}"): head,
            ("rev-list", "--parents", "-n", "1", head): (
                f"{head} {accounting.RUNTIME_IMPLEMENTATION_COMMIT}"
            ),
            (
                "rev-list",
                "--count",
                f"{accounting.RUNTIME_IMPLEMENTATION_COMMIT}..{head}",
            ): "1",
            ("status", "--porcelain=v1", "--untracked-files=no"): "",
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
    ), mock.patch.object(adjudicator, "_git", side_effect=fixed_git), mock.patch.dict(
        os.environ, environment, clear=False
    ):
        observed_head, state = adjudicator._repository_authority()
    assert observed_head == head
    assert state["local_head"] == head
    assert state["origin_head"] == head
    assert state["scc_head"] == head

    def two_commit_git(arguments: tuple[str, ...]) -> str:
        value = fixed_git(arguments)
        return "2" if arguments[:2] == ("rev-list", "--count") else value

    with mock.patch.object(
        adjudicator, "SCC_REPOSITORY_ROOT", adjudicator.REPOSITORY_ROOT
    ), mock.patch.object(adjudicator, "_git", side_effect=two_commit_git), mock.patch.dict(
        os.environ, environment, clear=False
    ):
        try:
            adjudicator._repository_authority()
        except RuntimeError as exc:
            assert str(exc) == "R8U_R7C_GIT_AUTHORITY_INVALID"
        else:
            raise AssertionError("two adjudication commits were accepted")


def test_proven_tail_ledger_failure_is_true_existing_task_failure() -> None:
    report = adjudicator._initial_report(ADJUDICATION_COMMIT)
    with mock.patch.object(
        evidence,
        "load_batch_metadata",
        side_effect=evidence.R7CEvidenceError(
            "R8U_R7C_FINAL_LEDGER_PROVEN_FAILURE"
        ),
    ):
        try:
            adjudicator._load_tail_batch(
                task_id=17,
                ordinal=16,
                expected_studies=250,
                plan={},
                report=report,
            )
        except adjudicator.R7CTerminalStop as exc:
            assert exc.status == adjudicator.TRUE_FAILURE_STOP
            assert exc.report["first_failed_task"] == 17
            assert exc.report["first_failed_stage"] == "FINAL_LEDGER"
        else:
            raise AssertionError("proven ledger failure was not task-specific")


def test_full_success_reuses_original_cohort_and_refreshes_lock_authority() -> None:
    repository = mock.Mock(
        return_value=(ADJUDICATION_COMMIT, dict(REPOSITORY_STATE))
    )
    queried_specs: list[accounting.FixedAccountingSpec] = []

    def passing_accounting(
        spec: accounting.FixedAccountingSpec, **_kwargs: object
    ) -> accounting.AccountingReceiptResult:
        queried_specs.append(spec)
        return _accounting_result(classification="PASS")

    def passing_log(
        _spec: accounting.FixedAccountingSpec, _receipt: object
    ) -> dict[str, str]:
        return {
            "status": "FIXED_PASS_TERMINAL_LOG_VALIDATED",
            "failure_scope": "NOT_APPLICABLE",
            "first_failed_stage": "NOT_APPLICABLE",
        }

    loaded_ordinals: list[int] = []

    def batch_projection(ordinal: int, **_kwargs: object) -> dict[str, object]:
        loaded_ordinals.append(ordinal)
        return {
            "n_selected_studies": 30 if ordinal == 18 else 250,
            "batch_finalization_receipt_sha256": f"{ordinal + 1:064x}",
        }

    cohort_projection = {
        "aggregate_totals": {"finalized_batches": 19},
        "study_partition": {"selected_studies": 4_530},
    }
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(adjudicator, "_repository_authority", repository))
        stack.enter_context(mock.patch.object(accounting, "validate_fixed_continuation_receipt", return_value="c" * 64))
        stack.enter_context(mock.patch.object(accounting, "ensure_fixed_accounting_directory"))
        stack.enter_context(mock.patch.object(accounting, "validate_fixed_scheduler_accounting_environment", return_value={"USER": accounting.OWNER}))
        stack.enter_context(mock.patch.object(accounting, "reuse_or_query_fixed_accounting", side_effect=passing_accounting))
        stack.enter_context(mock.patch.object(adjudicator.terminal_logs, "inspect_fixed_terminal_logs", side_effect=passing_log))
        stack.enter_context(mock.patch.object(evidence, "load_fixed_plan", return_value={}))
        stack.enter_context(mock.patch.object(evidence, "load_batch_metadata", side_effect=batch_projection))
        stack.enter_context(mock.patch.object(adjudicator, "_validate_prefix"))
        original = stack.enter_context(mock.patch.object(evidence, "load_original_cohort_finalization_receipt", return_value=({"status": "PASS_PRODUCTION_C3_FINALIZED"}, "d" * 64)))
        stack.enter_context(mock.patch.object(evidence, "fixed_cache_topology", return_value={}))
        stack.enter_context(mock.patch.object(adjudicator.metadata, "zero_scientific_actions", return_value={}))
        stack.enter_context(mock.patch.object(adjudicator.metadata, "build_cohort_finalization_receipt", return_value=cohort_projection))
        cohort_publish = stack.enter_context(mock.patch.object(adjudicator.metadata, "publish_cohort_finalization_receipt"))
        stack.enter_context(mock.patch.object(adjudicator.quiescence, "capture_fixed_quiescence", return_value=SimpleNamespace(scheduler_state={})))
        stack.enter_context(mock.patch.object(adjudicator, "_receipt_timestamp", return_value="2026-09-06T00:00:00.000000Z"))
        stack.enter_context(mock.patch.object(adjudicator.metadata, "build_post_reconstruction_lock_receipt", return_value={}))
        stack.enter_context(mock.patch.object(adjudicator.metadata, "publish_post_reconstruction_lock_receipt", return_value=("e" * 64, True)))
        result = adjudicator.adjudicate_fixed_existing_jobs()

    assert queried_specs == list(accounting.fixed_accounting_specs())
    assert loaded_ordinals == [16, 17, 18, *range(16)]
    assert repository.call_count == 2
    original.assert_called_once()
    cohort_publish.assert_not_called()
    assert result["status"] == adjudicator.SUCCESS
    assert result["cohort_finalization_mode"] == "REUSED_EXISTING"
    assert result["cohort_finalization_receipt_sha256"] == "d" * 64


def test_finalizer_case_c_requires_affirmative_control_plane_marker() -> None:
    accounting_results = [
        _accounting_result(classification="PASS"),
        _accounting_result(classification="PASS"),
        _accounting_result(classification="PASS"),
        _accounting_result(classification="FAIL"),
    ]
    log_results = [
        {
            "status": "FIXED_PASS_TERMINAL_LOG_VALIDATED",
            "failure_scope": "NOT_APPLICABLE",
            "first_failed_stage": "NOT_APPLICABLE",
        }
        for _ in range(3)
    ] + [
        {
            "status": "FIXED_NONZERO_TERMINAL_LOG_CLASSIFIED",
            "failure_scope": "NOT_DETERMINED",
            "first_failed_stage": "TERMINAL_LOG_VALIDATION",
        }
    ]

    def batch_projection(ordinal: int, **_kwargs: object) -> dict[str, object]:
        return {
            "n_selected_studies": 30 if ordinal == 18 else 250,
            "batch_finalization_receipt_sha256": f"{ordinal + 1:064x}",
        }

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], mock.patch.object(
        accounting,
        "reuse_or_query_fixed_accounting",
        side_effect=accounting_results,
    ), mock.patch.object(
        adjudicator.terminal_logs,
        "inspect_fixed_terminal_logs",
        side_effect=log_results,
    ), mock.patch.object(
        evidence, "load_fixed_plan", return_value={}
    ), mock.patch.object(
        evidence, "load_batch_metadata", side_effect=batch_projection
    ), mock.patch.object(adjudicator, "_validate_prefix"):
        try:
            adjudicator.adjudicate_fixed_existing_jobs()
        except adjudicator.R7CTerminalStop as exc:
            assert exc.status == adjudicator.COHORT_STOP
            assert exc.code == "R8U_R7C_FINALIZER_SUBSTANTIVE_FAILURE"
        else:
            raise AssertionError("unproven Case-C finalizer failure was accepted")


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
