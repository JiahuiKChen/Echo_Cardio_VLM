#!/usr/bin/env python3
"""Fixed, metadata-only adjudication of the completed R8U-R7 jobs.

This is a retrospective control-plane role.  It cannot select a job, task,
attempt, plan, or output location and it never calls the live continuation
worker.  The four accounting identities and the three tail batches are fixed
below and are evaluated in their required fail-closed order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Final, Mapping


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_r8u_r7c_accounting as accounting
import lvef_c3_r8u_r7c_evidence as evidence
import lvef_c3_r8u_r7c_metadata as metadata
import lvef_c3_r8u_r7c_quiescence as quiescence
import lvef_c3_r8u_r7c_terminal_logs as terminal_logs


BRANCH: Final = "codex/lvef-multitask-revalidation"
SCC_REPOSITORY_ROOT: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
)
GIT_PATH: Final = Path("/usr/bin/git")
COORDINATOR_LOCAL_HEAD_ENV: Final = "R8U_R7C_VERIFIED_LOCAL_HEAD"
COORDINATOR_LOCAL_CLEAN_ENV: Final = "R8U_R7C_VERIFIED_LOCAL_TRACKED_CLEAN"
BATCH16_FINAL_RECEIPT_SHA256: Final = (
    "63b002947814e92c616d0eb7f74ca334cba4e77cdc17f7ce2b55cfc51e090439"
)
COHORT_RECEIPT_PATH: Final = (
    accounting.R7C_ROOT / "cohort_finalization_receipt.restricted.json"
)
LOCK_RECEIPT_PATH: Final = (
    accounting.R7C_ROOT / "post_reconstruction_lock_receipt.restricted.json"
)
ENTRYPOINT_FLAG: Final = "--adjudicate-fixed-r8u-r7-existing-jobs"
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")

TAIL_TASK_BATCHES: Final = ((17, 16, 250), (18, 17, 250), (19, 18, 30))
PREFIX_EXPECTED: Final = {
    "finalized_batches": 16,
    "n_selected_studies": 4_000,
    "n_selected_subjects": 4_000,
    "n_source_objects": 296_377,
    "source_bytes": 1_071_366_146_696,
    "n_clip_embeddings": 162_764,
    "n_study_embeddings": 3_995,
    "n_prespecified_no_cine_studies": 5,
}

SUCCESS = "R7C_EXISTING_JOBS_ADJUDICATED_FULL_COHORT_LOCKED"
STARTING_AUTHORITY_STOP = "STOPPED_ON_STARTING_AUTHORITY_DIVERGENCE"
ROLE_AUTHORITY_STOP = "STOPPED_ON_ROLE_AUTHORITY_CONTRADICTION"
ACCOUNTING_STOP = "STOPPED_ON_ACCOUNTING_NOT_AVAILABLE"
TRUE_FAILURE_STOP = "STOPPED_ON_TRUE_EXISTING_JOB_FAILURE"
BATCH_STOP = "STOPPED_ON_BATCH_RECEIPT_CONTRADICTION"
COHORT_STOP = "STOPPED_ON_COHORT_FINALIZATION_CONTRADICTION"
LOCK_STOP = "STOPPED_ON_POST_RECONSTRUCTION_LOCK_CONTRADICTION"

# These errors are emitted only after a canonical metadata object has been
# read successfully and its immutable scientific/transition reconciliation
# has independently failed.  Missing, nonregular, malformed, or schema-invalid
# paths remain receipt contradictions instead of unsupported task failures.
PROVEN_TASK_EVIDENCE_ERRORS: Final = frozenset(
    {
        "R8U_R7C_BATCH_FINALIZATION_PROVEN_FAILURE",
        "R8U_R7C_BATCH_RECEIPT_PLAN_MISMATCH",
        "R8U_R7C_CACHE_AUTHORIZATION_INVALID",
        "R8U_R7C_CACHE_INTENT_INVALID",
        "R8U_R7C_CACHE_RETIREMENT_PROVEN_FAILURE",
        "R8U_R7C_CACHE_STAGED_INVALID",
        "R8U_R7C_CACHE_TRANSITION_INVALID",
        "R8U_R7C_ECHOPRIME_STAGE_RECEIPT_INVALID",
        "R8U_R7C_EMBEDDING_METADATA_AUTHORITY_INVALID",
        "R8U_R7C_EXTRACTION_STAGE_HASH_MISMATCH",
        "R8U_R7C_EXTRACTION_SUMMARY_HASH_MISMATCH",
        "R8U_R7C_EXTRACTION_SUMMARY_RECONCILIATION_INVALID",
        "R8U_R7C_FINALIZED_EXTRACTION_CACHE_PRESENT",
        "R8U_R7C_FINAL_LEDGER_INVALID",
        "R8U_R7C_FINAL_LEDGER_PROVEN_FAILURE",
        "R8U_R7C_PRESERVATION_COVERAGE_INVALID",
        "R8U_R7C_PRESERVATION_MANIFEST_HASH_MISMATCH",
        "R8U_R7C_PRESERVATION_MANIFEST_PLAN_INVALID",
        "R8U_R7C_PRESERVATION_PROVEN_FAILURE",
        "R8U_R7C_PRESERVATION_RECEIPT_RECONCILIATION_INVALID",
        "R8U_R7C_PRESERVATION_ROLE_INVALID",
        "R8U_R7C_PRESERVATION_ROLE_PATH_INVALID",
        "R8U_R7C_RAW_SOURCE_AUTHORITY_INVALID",
        "R8U_R7C_RETIRED_CACHE_MANIFEST_INVALID",
        "R8U_R7C_RETIRED_CACHE_TREE_MISMATCH",
        "R8U_R7C_STAGE_RECEIPT_PROVEN_FAILURE",
        "R8U_R7C_STAGE_RECEIPT_RECONCILIATION_INVALID",
        "R8U_R7C_TAIL_BATCH_SELECTED_STUDIES_INVALID",
    }
)


class R7CTerminalStop(RuntimeError):
    """A sanitized expected terminal state with its accumulated report."""

    def __init__(self, status: str, code: str, report: Mapping[str, Any]):
        self.status = status
        self.code = code
        self.report = dict(report)
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_git_tool() -> None:
    try:
        observed = os.lstat(GIT_PATH)
    except OSError as exc:
        raise RuntimeError("R8U_R7C_GIT_TOOL_INVALID") from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or observed.st_uid != 0
        or observed.st_nlink != 1
        or stat.S_IMODE(observed.st_mode) & 0o022
        or stat.S_IMODE(observed.st_mode) & 0o111 == 0
    ):
        raise RuntimeError("R8U_R7C_GIT_TOOL_INVALID")


def _git(arguments: tuple[str, ...]) -> str:
    _validate_git_tool()
    git_environment = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    try:
        completed = subprocess.run(
            [str(GIT_PATH), "-C", str(REPOSITORY_ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            text=True,
            env=git_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_INVALID")
    if completed.stderr:
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_INVALID")
    return completed.stdout.strip()


def _repository_authority() -> tuple[str, dict[str, Any]]:
    if REPOSITORY_ROOT != SCC_REPOSITORY_ROOT:
        raise RuntimeError("R8U_R7C_SCC_CHECKOUT_INVALID")
    branch = _git(("branch", "--show-current"))
    head = _git(("rev-parse", "HEAD"))
    origin = _git(("rev-parse", f"origin/{BRANCH}"))
    local_head = os.environ.get(COORDINATOR_LOCAL_HEAD_ENV, "")
    local_clean = os.environ.get(COORDINATOR_LOCAL_CLEAN_ENV, "")
    parent_projection = _git(("rev-list", "--parents", "-n", "1", head)).split()
    descendant_count = _git(
        ("rev-list", "--count", f"{accounting.RUNTIME_IMPLEMENTATION_COMMIT}..{head}")
    )
    tracked_status = _git(("status", "--porcelain=v1", "--untracked-files=no"))
    if (
        branch != BRANCH
        or COMMIT_RE.fullmatch(head) is None
        or origin != head
        or local_head != head
        or local_clean != "YES"
        or parent_projection != [head, accounting.RUNTIME_IMPLEMENTATION_COMMIT]
        or descendant_count != "1"
        or tracked_status
    ):
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_INVALID")
    try:
        _git(
            (
                "merge-base",
                "--is-ancestor",
                accounting.SCIENTIFIC_COMMIT,
                head,
            )
        )
    except RuntimeError as exc:
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_INVALID") from exc
    if head == accounting.RUNTIME_IMPLEMENTATION_COMMIT:
        raise RuntimeError("R8U_R7C_GIT_AUTHORITY_INVALID")
    return head, {
        "branch": BRANCH,
        "local_head": local_head,
        "origin_head": origin,
        "scc_head": head,
        "local_tracked_clean": True,
        "scc_tracked_clean": True,
    }


def _initial_report(adjudication_commit: str) -> dict[str, Any]:
    return {
        "status": "IN_PROGRESS",
        "error_code": "NONE",
        "runtime_implementation_commit": accounting.RUNTIME_IMPLEMENTATION_COMMIT,
        "adjudication_implementation_commit": adjudication_commit,
        "continuation_receipt_sha256": accounting.CONTINUATION_RECEIPT_SHA256,
        "accounting": {},
        "batches": {},
        "finalized_batches": 16,
        "prefix_totals": dict(PREFIX_EXPECTED),
        "cohort_finalization_mode": "NOT_RUN",
        "cohort_finalization_receipt_sha256": None,
        "post_reconstruction_lock_receipt_sha256": None,
        "prohibited_actions": metadata.zero_scientific_actions(),
        "control_plane_qsub_submissions": 0,
    }


def _stop(
    status: str, code: str, report: dict[str, Any], **details: Any
) -> None:
    report.update(details)
    report["status"] = status
    report["error_code"] = code
    raise R7CTerminalStop(status, code, report)


def _accounting_projection(
    result: accounting.AccountingReceiptResult,
) -> dict[str, Any]:
    value = result.receipt
    return {
        "qacct_query_count": result.qacct_query_count,
        "failed": value["failed"],
        "exit_status": value["exit_status"],
        "wall_seconds": value["wall_seconds"],
        "runtime_classification": value["terminal_classification"],
        "receipt_created": result.created,
        "receipt_sha256": result.receipt_sha256,
        "observed_qacct_job_name": value["observed_qacct_job_name"],
        "failure_evidence": None,
    }


def _record_terminal_log(
    spec: accounting.FixedAccountingSpec,
    result: accounting.AccountingReceiptResult,
    projection: dict[str, Any],
) -> None:
    try:
        projection["failure_evidence"] = dict(
            terminal_logs.inspect_fixed_terminal_logs(spec, result.receipt)
        )
    except terminal_logs.R7CTerminalLogError as exc:
        projection["failure_evidence"] = {
            "failure_scope": "NOT_DETERMINED",
            "first_failure_marker": "FIXED_TERMINAL_LOG_UNAVAILABLE",
            "first_failed_stage": "TERMINAL_LOG_VALIDATION",
            "inspection_error": exc.code,
        }


def _obtain_accounting(
    spec: accounting.FixedAccountingSpec,
    *,
    key: str,
    adjudication_commit: str,
    environment: Mapping[str, str],
    report: dict[str, Any],
) -> accounting.AccountingReceiptResult:
    try:
        result = accounting.reuse_or_query_fixed_accounting(
            spec,
            adjudication_implementation_commit=adjudication_commit,
            environment=environment,
        )
    except accounting.R7CAccountingError as exc:
        destination = (
            ACCOUNTING_STOP
            if exc.code
            in {
                "R8U_R7C_ACCOUNTING_NOT_AVAILABLE",
                "R8U_R7C_QACCT_TOOL_INVALID",
                "R8U_R7C_QACCT_ENVIRONMENT_INVALID",
            }
            else ROLE_AUTHORITY_STOP
        )
        _stop(destination, exc.code, report, first_failed_record=key)
    projection = _accounting_projection(result)
    report["accounting"][key] = projection
    _record_terminal_log(spec, result, projection)
    return result


def _task_evidence_stage(code: str) -> str:
    if "FINAL_LEDGER" in code:
        return "FINAL_LEDGER"
    if "CACHE" in code or "RETIRED" in code:
        return "CACHE_RETIREMENT"
    if "PRESERVATION" in code or "RAW_SOURCE" in code:
        return "PRESERVATION"
    if "ECHOPRIME" in code or "EMBEDDING" in code:
        return "ECHOPRIME_EMBEDDING"
    if "EXTRACTION" in code:
        return "DICOM_EXTRACTION"
    return "BATCH_FINALIZATION"


def _load_tail_batch(
    *, task_id: int, ordinal: int, expected_studies: int, plan: Mapping[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = evidence.load_batch_metadata(ordinal, plan=plan)
    except evidence.R7CEvidenceError as exc:
        if exc.code in PROVEN_TASK_EVIDENCE_ERRORS:
            _stop(
                TRUE_FAILURE_STOP,
                exc.code,
                report,
                first_failed_task=task_id,
                first_failed_stage=_task_evidence_stage(exc.code),
                first_failed_batch=f"c3_batch_{ordinal:03d}",
            )
        _stop(
            BATCH_STOP,
            exc.code,
            report,
            first_failed_batch=f"c3_batch_{ordinal:03d}",
        )
    if value["n_selected_studies"] != expected_studies:
        _stop(
            TRUE_FAILURE_STOP,
            "R8U_R7C_TAIL_BATCH_SELECTED_STUDIES_INVALID",
            report,
            first_failed_task=task_id,
            first_failed_stage="BATCH_FINALIZATION",
            first_failed_batch=f"c3_batch_{ordinal:03d}",
        )
    report["batches"][f"batch_{ordinal + 1}"] = {
        "selected_studies": value["n_selected_studies"],
        "finalization": "PASS",
        "final_receipt_sha256": value["batch_finalization_receipt_sha256"],
    }
    report["finalized_batches"] = ordinal + 1
    return value


def _validate_prefix(batch_metadata: list[Mapping[str, Any]]) -> None:
    observed = {
        "finalized_batches": len(batch_metadata),
        **{
            key: sum(int(item[key]) for item in batch_metadata)
            for key in PREFIX_EXPECTED
            if key != "finalized_batches"
        },
    }
    if (
        observed != PREFIX_EXPECTED
        or batch_metadata[15]["batch_finalization_receipt_sha256"]
        != BATCH16_FINAL_RECEIPT_SHA256
    ):
        raise RuntimeError("R8U_R7C_FROZEN_PREFIX_CONTRADICTION")


def _receipt_timestamp(path: Path) -> str:
    if not os.path.lexists(path):
        return _utc_now()
    value, _payload = metadata.read_private_json(path)
    timestamp = value.get("created_at_utc")
    if not isinstance(timestamp, str):
        raise metadata.R7CMetadataError("EXISTING_RECEIPT_CONTRADICTS_EXPECTED")
    return timestamp


def adjudicate_fixed_existing_jobs() -> dict[str, Any]:
    """Adjudicate the four immutable completed records, in fixed order."""

    try:
        adjudication_commit, _starting_repository_state = _repository_authority()
    except RuntimeError as exc:
        report = _initial_report("NOT_VALIDATED")
        _stop(STARTING_AUTHORITY_STOP, str(exc), report)
    report = _initial_report(adjudication_commit)
    try:
        accounting.validate_fixed_continuation_receipt()
        # Receipt paths are validated only when their fixed record becomes
        # eligible.  A malformed Task-18 path must not preempt independent
        # Task-17 adjudication.
        accounting.ensure_fixed_accounting_directory()
        environment = accounting.validate_fixed_scheduler_accounting_environment()
    except accounting.R7CAccountingError as exc:
        destination = (
            ACCOUNTING_STOP
            if exc.code
            in {
                "R8U_R7C_ACCOUNTING_NOT_AVAILABLE",
                "R8U_R7C_QACCT_TOOL_INVALID",
                "R8U_R7C_QACCT_ENVIRONMENT_INVALID",
            }
            else ROLE_AUTHORITY_STOP
        )
        _stop(destination, exc.code, report)

    plan: Mapping[str, Any] | None = None
    tail_metadata: list[dict[str, Any]] = []
    for task_id, ordinal, expected_studies in TAIL_TASK_BATCHES:
        spec = accounting.TASK_ACCOUNTING_SPECS[task_id]
        key = f"task_{task_id}"
        result = _obtain_accounting(
            spec,
            key=key,
            adjudication_commit=adjudication_commit,
            environment=environment,
            report=report,
        )
        log_evidence = report["accounting"][key]["failure_evidence"]
        if result.receipt["terminal_classification"] == "FAIL":
            failed_stage = log_evidence.get("first_failed_stage")
            if failed_stage in {None, "NOT_REPORTED", "NOT_APPLICABLE"}:
                failed_stage = "TERMINAL_ACCOUNTING"
            _stop(
                TRUE_FAILURE_STOP,
                "R8U_R7C_EXISTING_TASK_ACCOUNTING_NONZERO",
                report,
                first_failed_task=task_id,
                first_failed_stage=failed_stage,
            )
        if (
            log_evidence.get("status") != "FIXED_PASS_TERMINAL_LOG_VALIDATED"
            or log_evidence.get("failure_scope") != "NOT_APPLICABLE"
        ):
            if log_evidence.get("failure_scope") == "SCIENTIFIC_OR_APPLICATION":
                _stop(
                    TRUE_FAILURE_STOP,
                    "R8U_R7C_EXISTING_TASK_APPLICATION_LOG_FAILURE",
                    report,
                    first_failed_task=task_id,
                    first_failed_stage=log_evidence.get(
                        "first_failed_stage", "APPLICATION_LOG"
                    ),
                )
            _stop(
                BATCH_STOP,
                "R8U_R7C_TASK_PASS_LOG_CONTRADICTION",
                report,
                first_failed_task=task_id,
                first_failed_stage="TERMINAL_LOG_VALIDATION",
            )
        if plan is None:
            try:
                plan = evidence.load_fixed_plan()
            except evidence.R7CEvidenceError as exc:
                _stop(BATCH_STOP, exc.code, report, first_failed_batch="PLAN")
        tail_metadata.append(
            _load_tail_batch(
                task_id=task_id,
                ordinal=ordinal,
                expected_studies=expected_studies,
                plan=plan,
                report=report,
            )
        )

    finalizer_result = _obtain_accounting(
        accounting.FINALIZER_ACCOUNTING_SPEC,
        key="finalizer",
        adjudication_commit=adjudication_commit,
        environment=environment,
        report=report,
    )
    finalizer_evidence = report["accounting"]["finalizer"]["failure_evidence"]
    if finalizer_result.receipt["terminal_classification"] == "FAIL":
        if finalizer_evidence["failure_scope"] != "CONTROL_PLANE_ONLY":
            _stop(
                COHORT_STOP,
                "R8U_R7C_FINALIZER_SUBSTANTIVE_FAILURE",
                report,
                first_failed_stage=finalizer_evidence["first_failed_stage"],
            )
    elif (
        finalizer_evidence.get("status")
        != "FIXED_PASS_TERMINAL_LOG_VALIDATED"
        or finalizer_evidence.get("failure_scope") != "NOT_APPLICABLE"
    ):
        _stop(
            COHORT_STOP,
            "R8U_R7C_FINALIZER_PASS_LOG_CONTRADICTION",
            report,
            first_failed_stage="TERMINAL_LOG_VALIDATION",
        )

    assert plan is not None
    try:
        prefix_metadata = [
            evidence.load_batch_metadata(ordinal, plan=plan)
            for ordinal in range(16)
        ]
        _validate_prefix(prefix_metadata)
    except evidence.R7CEvidenceError as exc:
        _stop(BATCH_STOP, exc.code, report, first_failed_batch="PREFIX")
    except RuntimeError as exc:
        _stop(COHORT_STOP, str(exc), report, first_failed_batch="PREFIX")
    all_batch_metadata = [*prefix_metadata, *tail_metadata]
    accounting_hashes = {
        key: report["accounting"][key]["receipt_sha256"]
        for key in ("task_17", "task_18", "task_19", "finalizer")
    }
    try:
        original_cohort = evidence.load_original_cohort_finalization_receipt(
            all_batch_metadata
        )
        cache_topology = evidence.fixed_cache_topology()
        zero_actions = metadata.zero_scientific_actions()
        cohort_timestamp = (
            _utc_now()
            if original_cohort is not None
            else _receipt_timestamp(COHORT_RECEIPT_PATH)
        )
        cohort_kwargs = {
            "attempt_id": accounting.ATTEMPT_ID,
            "plan_sha256": accounting.PLAN_SHA256,
            "scientific_commit": accounting.SCIENTIFIC_COMMIT,
            "runtime_implementation_commit": accounting.RUNTIME_IMPLEMENTATION_COMMIT,
            "adjudication_implementation_commit": adjudication_commit,
            "batch16_final_receipt_sha256": BATCH16_FINAL_RECEIPT_SHA256,
            "continuation_receipt_sha256": accounting.CONTINUATION_RECEIPT_SHA256,
            "accounting_receipt_sha256": accounting_hashes,
            "cache_topology": cache_topology,
            "prohibited_actions": zero_actions,
            "created_at_utc": cohort_timestamp,
        }
        cohort = metadata.build_cohort_finalization_receipt(
            plan, all_batch_metadata, **cohort_kwargs
        )
        if original_cohort is not None:
            _original_value, cohort_sha = original_cohort
            cohort_mode = "REUSED_EXISTING"
        else:
            cohort_sha, cohort_created = (
                metadata.publish_cohort_finalization_receipt(
                    COHORT_RECEIPT_PATH,
                    cohort,
                    validation_kwargs={
                        "plan": plan,
                        "batch_metadata": all_batch_metadata,
                        **cohort_kwargs,
                    },
                )
            )
            cohort_mode = (
                "R7C_METADATA_ONLY_PUBLICATION"
                if cohort_created
                else "REUSED_EXISTING"
            )
    except (evidence.R7CEvidenceError, metadata.R7CMetadataError) as exc:
        _stop(COHORT_STOP, exc.code, report)
    report["cohort_finalization_mode"] = cohort_mode
    report["cohort_finalization_receipt_sha256"] = cohort_sha
    report["full_cohort_aggregates"] = dict(cohort["aggregate_totals"])
    report["full_cohort_study_partition"] = dict(cohort["study_partition"])

    try:
        observed_quiescence = quiescence.capture_fixed_quiescence(
            environment=environment
        )
        scheduler_state = dict(observed_quiescence.scheduler_state)
        lock_commit, repository_state = _repository_authority()
        if lock_commit != adjudication_commit:
            raise RuntimeError("R8U_R7C_LOCK_REPOSITORY_COMMIT_CHANGED")
        lock_timestamp = _receipt_timestamp(LOCK_RECEIPT_PATH)
        lock_kwargs = {
            "cohort_receipt_sha256": cohort_sha,
            "cohort_finalization_mode": cohort_mode,
            "repository_state": repository_state,
            "scheduler_state": scheduler_state,
            "expected_branch": BRANCH,
            "created_at_utc": lock_timestamp,
        }
        lock = metadata.build_post_reconstruction_lock_receipt(
            cohort, **lock_kwargs
        )
        lock_sha, _lock_created = metadata.publish_post_reconstruction_lock_receipt(
            LOCK_RECEIPT_PATH,
            lock,
            validation_kwargs={"cohort_receipt": cohort, **lock_kwargs},
        )
    except (quiescence.R7CQuiescenceError, metadata.R7CMetadataError) as exc:
        _stop(LOCK_STOP, exc.code, report)
    except RuntimeError as exc:
        _stop(LOCK_STOP, str(exc), report)
    report["post_reconstruction_lock_receipt_sha256"] = lock_sha
    report["status"] = SUCCESS
    report["error_code"] = "NONE"
    report["finalized_batches"] = 19
    return report


def guarded_main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    if selected != [ENTRYPOINT_FLAG]:
        print("R8U_R7C_STATUS=STOPPED_ON_IMPLEMENTATION_OR_TEST_FAILURE")
        print("R8U_R7C_ERROR_CODE=R8U_R7C_CLOSED_ARGUMENTS_INVALID")
        return 64
    try:
        result = adjudicate_fixed_existing_jobs()
    except R7CTerminalStop as exc:
        print(f"R8U_R7C_STATUS={exc.status}")
        print(f"R8U_R7C_ERROR_CODE={exc.code}")
        print(
            "R8U_R7C_REPORT_JSON="
            + json.dumps(exc.report, sort_keys=True, separators=(",", ":"))
        )
        return 78
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R7C_UNEXPECTED_SANITIZED_FAILURE")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "R8U_R7C_UNEXPECTED_SANITIZED_FAILURE"
        print("R8U_R7C_STATUS=STOPPED_ON_IMPLEMENTATION_OR_TEST_FAILURE")
        print(f"R8U_R7C_ERROR_CODE={code}")
        return 78
    print(f"R8U_R7C_STATUS={result['status']}")
    print(
        "R8U_R7C_REPORT_JSON="
        + json.dumps(result, sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(guarded_main())
