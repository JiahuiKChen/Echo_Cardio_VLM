#!/usr/bin/env python3
"""Adjudicate only the completed R7F jobs and seal the full-cohort lock.

This closed entrypoint is retrospective and metadata-only.  It has no caller
surface for a job, task, attempt, plan, log, artifact, or receipt path.  The
completed scheduler event remains bound to the R7F runtime commit while the
new receipts bind both the base R7G adjudicator and its one direct-child R1
repair commit.
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

import lvef_c3_r8u_r7g_accounting as accounting
import lvef_c3_r8u_r7g_evidence as evidence
import lvef_c3_r8u_r7g_metadata as metadata
import lvef_c3_r8u_r7g_quiescence as quiescence
import lvef_c3_r8u_r7g_terminal_logs as terminal_logs


BRANCH: Final = "codex/lvef-multitask-revalidation"
SCC_REPOSITORY_ROOT: Final = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
)
GIT_PATH: Final = Path("/usr/bin/git")
COORDINATOR_LOCAL_HEAD_ENV: Final = "R8U_R7G_VERIFIED_LOCAL_HEAD"
COORDINATOR_LOCAL_CLEAN_ENV: Final = "R8U_R7G_VERIFIED_LOCAL_TRACKED_CLEAN"
COHORT_RECEIPT_PATH: Final = (
    accounting.R7G_ROOT / "cohort_finalization_receipt.restricted.json"
)
LOCK_RECEIPT_PATH: Final = (
    accounting.R7G_ROOT / "post_reconstruction_lock_receipt.restricted.json"
)
ENTRYPOINT_FLAG: Final = "--adjudicate-fixed-r8u-r7f-existing-jobs"
PREFLIGHT_FLAG: Final = "--preflight-fixed-r8u-r7f-authorities"
PREFLIGHT_PASS: Final = "PASS_R7G_R1_ALL_FIXED_AUTHORITY_INPUTS"
R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT: Final = (
    "4dc4b2327f91ffd3912c91a7113f16d41d0562a8"
)
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")

TAIL_TASKS: Final = (
    (17, 16, 250, 18_606, 66_807_894_336),
    (18, 17, 250, 18_658, 68_754_613_138),
    (19, 18, 30, 2_343, 9_640_479_152),
)
PREFIX_EXPECTED: Final = {
    "finalized_batches": 16,
    "n_selected_studies": 4_000,
    "n_source_objects": 296_377,
    "source_bytes": 1_071_366_146_696,
    "n_clip_embeddings": 162_764,
    "n_study_embeddings": 3_995,
    "n_prespecified_no_cine_studies": 5,
}

SUCCESS: Final = "FULL_SELECTED_COHORT_RECONSTRUCTION_FINALIZED_AND_LOCKED"
IMPLEMENTATION_STOP: Final = "STOPPED_ON_R7G_R1_IMPLEMENTATION_OR_SYNC_FAILURE"
HISTORICAL_STOP: Final = (
    "STOPPED_ON_HISTORICAL_AUTHORITY_HASH_OR_SCHEMA_CONTRADICTION"
)
ACCOUNTING_STOP: Final = "STOPPED_ON_ACCOUNTING_PENDING"
TASK_STOP: Final = "STOPPED_ON_FINAL_TAIL_TASK_FAILURE"
COHORT_STOP: Final = "STOPPED_ON_COHORT_FINALIZATION_CONTRADICTION"
LOCK_STOP: Final = "STOPPED_ON_POST_RECONSTRUCTION_LOCK_CONTRADICTION"

ACCOUNTING_PENDING_CODES: Final = frozenset(
    {
        "R8U_R7G_ACCOUNTING_NOT_AVAILABLE",
        "R8U_R7G_QACCT_OUTPUT_INVALID",
    }
)
STRUCTURAL_TERMINAL_LOG_CODES: Final = frozenset(
    {
        "R8U_R7G_TERMINAL_LOG_AUTHORITY_INVALID",
        "R8U_R7G_TERMINAL_LOG_SCOPE_INVALID",
        "R8U_R7G_TERMINAL_LOG_ACCOUNTING_INVALID",
    }
)


class R7GTerminalStop(RuntimeError):
    """One sanitized terminal classification and its aggregate-safe report."""

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
        raise RuntimeError("R8U_R7G_GIT_TOOL_INVALID") from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or observed.st_uid != 0
        or observed.st_nlink != 1
        or stat.S_IMODE(observed.st_mode) & 0o022
        or stat.S_IMODE(observed.st_mode) & 0o111 == 0
    ):
        raise RuntimeError("R8U_R7G_GIT_TOOL_INVALID")


def _git(arguments: tuple[str, ...]) -> str:
    _validate_git_tool()
    environment = {
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
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("R8U_R7G_GIT_AUTHORITY_UNAVAILABLE") from exc
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("R8U_R7G_GIT_AUTHORITY_INVALID")
    return completed.stdout.strip()


def _repository_authority() -> tuple[str, dict[str, Any]]:
    """Require the synchronized SCC checkout at exactly the R7G-R1 commit."""

    if REPOSITORY_ROOT != SCC_REPOSITORY_ROOT:
        raise RuntimeError("R8U_R7G_SCC_CHECKOUT_INVALID")
    branch = _git(("branch", "--show-current"))
    head = _git(("rev-parse", "HEAD"))
    origin = _git(("rev-parse", f"origin/{BRANCH}"))
    local_head = os.environ.get(COORDINATOR_LOCAL_HEAD_ENV, "")
    local_clean = os.environ.get(COORDINATOR_LOCAL_CLEAN_ENV, "")
    parent_projection = _git(("rev-list", "--parents", "-n", "1", head)).split()
    descendant_count = _git(
        (
            "rev-list",
            "--count",
            f"{R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT}..{head}",
        )
    )
    tracked_status = _git(("status", "--porcelain=v1", "--untracked-files=no"))
    if (
        branch != BRANCH
        or COMMIT_RE.fullmatch(head) is None
        or origin != head
        or local_head != head
        or local_clean != "YES"
        or parent_projection
        != [head, R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT]
        or descendant_count != "1"
        or tracked_status
    ):
        raise RuntimeError("R8U_R7G_GIT_AUTHORITY_INVALID")
    for ancestor in (
        accounting.RUNTIME_IMPLEMENTATION_COMMIT,
        R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
        accounting.SCIENTIFIC_COMMIT,
    ):
        try:
            _git(("merge-base", "--is-ancestor", ancestor, head))
        except RuntimeError as exc:
            raise RuntimeError("R8U_R7G_GIT_AUTHORITY_INVALID") from exc
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
        "preceding_result": (
            "R7G_HISTORICAL_PRODUCER_SERIALIZATION_COMPATIBILITY_DEFECT"
        ),
        "starting_commit": R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT,
        "runtime_implementation_commit": accounting.RUNTIME_IMPLEMENTATION_COMMIT,
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": adjudication_commit,
        "current_r7f_task_runtime_status": "UNADJUDICATED",
        "terminal_authority_created": False,
        "terminal_authority_sha256": None,
        "r7f_authority_receipt_sha256": dict(
            accounting.EXPECTED_R7F_RECEIPT_SHA256
        ),
        "r7f_probe_terminal_receipt_sha256": None,
        "accounting": {},
        "batches": {},
        "finalized_batches": 16,
        "prefix_totals": dict(PREFIX_EXPECTED),
        "cohort_finalization_mode": "NOT_RUN",
        "cohort_finalization_receipt_valid": False,
        "cohort_finalization_receipt_sha256": None,
        "post_reconstruction_lock_review": "NOT_RUN",
        "post_reconstruction_lock_receipt_sha256": None,
        "prohibited_actions": metadata.zero_scientific_actions(),
        "new_control_plane_qsub_submissions": 0,
    }


def _stop(
    status: str, code: str, report: dict[str, Any], **details: Any
) -> None:
    report.update(details)
    report["status"] = status
    report["error_code"] = code
    raise R7GTerminalStop(status, code, report)


def _implementation_stop(
    code: str,
    report: dict[str, Any],
    *,
    function: str,
    predicate: str,
    artifact: str,
) -> None:
    _stop(
        IMPLEMENTATION_STOP,
        code,
        report,
        structural_function=function,
        structural_predicate=predicate,
        structural_artifact=artifact,
    )


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
        "receipt_valid": True,
        "receipt_created": result.created,
        "receipt_sha256": result.receipt_sha256,
        "observed_qacct_job_name": value["observed_qacct_job_name"],
        "terminal_log": None,
    }


def _obtain_accounting(
    spec: accounting.FixedAccountingSpec,
    *,
    key: str,
    terminal_authority: Mapping[str, Any],
    environment: Mapping[str, str],
    report: dict[str, Any],
) -> accounting.AccountingReceiptResult:
    try:
        result = accounting.reuse_or_query_fixed_accounting(
            spec,
            terminal_authority=terminal_authority,
            environment=environment,
        )
    except accounting.R7GAccountingError as exc:
        if exc.code in ACCOUNTING_PENDING_CODES:
            _stop(
                ACCOUNTING_STOP,
                exc.code,
                report,
                accounting_pending_record=key,
            )
        _implementation_stop(
            exc.code,
            report,
            function="accounting.reuse_or_query_fixed_accounting",
            predicate=exc.code,
            artifact=str(spec.receipt_path),
        )
    projection = _accounting_projection(result)
    report["accounting"][key] = projection
    try:
        projection["terminal_log"] = dict(
            terminal_logs.inspect_fixed_terminal_logs(
                spec,
                result.receipt,
                terminal_authority=terminal_authority,
            )
        )
    except terminal_logs.R7GTerminalLogError as exc:
        if exc.code in STRUCTURAL_TERMINAL_LOG_CODES:
            _implementation_stop(
                exc.code,
                report,
                function="terminal_logs.inspect_fixed_terminal_logs",
                predicate=exc.code,
                artifact=str(spec.scheduler_log_path),
            )
        destination = TASK_STOP if spec.task_id is not None else COHORT_STOP
        _stop(
            destination,
            exc.code,
            report,
            first_failed_task=spec.task_id,
            first_failed_stage="TERMINAL_LOG_VALIDATION",
            first_failed_artifact=str(spec.scheduler_log_path),
        )
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
    if "PLAN" in code or "SOURCE" in code:
        return "SOURCE_PLAN_AUTHORITY"
    return "BATCH_FINALIZATION"


def _tail_failure(
    *, task_id: int, ordinal: int, stage: str, code: str,
    report: dict[str, Any],
) -> None:
    _stop(
        TASK_STOP,
        code,
        report,
        first_failed_task=task_id,
        first_failed_stage=stage,
        first_failed_batch=f"c3_batch_{ordinal:03d}",
        download_began="NOT_DETERMINED",
        extraction_began="NOT_DETERMINED",
        echoprime_began="NOT_DETERMINED",
        preservation_began="NOT_DETERMINED",
        retirement_occurred="NOT_DETERMINED",
        raw_outputs_remain="NOT_DETERMINED",
        partial_outputs_remain="NOT_DETERMINED",
    )


def _validate_tail_projection(
    value: Mapping[str, Any],
    *,
    task_id: int,
    ordinal: int,
    expected_studies: int,
    expected_objects: int,
    expected_bytes: int,
    report: dict[str, Any],
) -> dict[str, Any]:
    valid = (
        value.get("batch_id") == f"c3_batch_{ordinal:03d}"
        and value.get("ordinal") == ordinal
        and value.get("n_selected_studies") == expected_studies
        and value.get("n_source_objects") == expected_objects
        and value.get("source_bytes") == expected_bytes
        and value.get("n_downloaded_objects") == expected_objects
        and value.get("downloaded_bytes") == expected_bytes
        and value.get("n_multiframe_candidates")
        == value.get("n_successful_extractions", -1)
        + value.get("n_technical_dispositions", -1)
        + value.get("n_blocking_failures", -1)
        and value.get("n_blocking_failures") == 0
        and value.get("n_successful_extractions")
        == value.get("n_clip_embeddings")
        and value.get("n_study_embeddings", -1)
        + value.get("n_prespecified_no_cine_studies", -1)
        == expected_studies
        and value.get("n_new_no_cine_studies") == 0
        and value.get("n_missing_selected_studies") == 0
        and value.get("n_duplicate_selected_studies") == 0
        and value.get("n_source_substitutions") == 0
        and value.get("n_unaccounted_multiframe_candidates") == 0
        and value.get("n_outcome_informed_decisions") == 0
        and value.get("final_ledger_status") == "FINALIZED"
        and value.get("preservation_status")
        == "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        and value.get("cache_retirement_status") == "PASS_RETIRED"
        and value.get("batch_finalization_status") == "PASS_BATCH_FINALIZED"
        and value.get("raw_source_authority_retained") is True
        and value.get("extracted_cache_absent") is True
    )
    if not valid:
        _tail_failure(
            task_id=task_id,
            ordinal=ordinal,
            stage="BATCH_FINALIZATION",
            code="R8U_R7G_TAIL_BATCH_RECONCILIATION_INVALID",
            report=report,
        )
    projection = {
        "selected_studies": value["n_selected_studies"],
        "selected_subjects": value["n_selected_subjects"],
        "source_objects": value["n_source_objects"],
        "source_bytes": value["source_bytes"],
        "readable_dicoms": value["n_readable_objects"],
        "unreadable_dicoms": value["n_unreadable_objects"],
        "multiframe_candidates": value["n_multiframe_candidates"],
        "single_frame_objects": value["n_single_frame_objects"],
        "successful_extractions": value["n_successful_extractions"],
        "technical_dispositions": value["n_technical_dispositions"],
        "blocking_failures": value["n_blocking_failures"],
        "ordinary_processing": value["n_ordinary_preprocessing_path"],
        "spatial_processing": value[
            "n_spatial_fallback_preprocessing_path"
        ],
        "temporal_processing": value[
            "n_temporal_fallback_preprocessing_path"
        ],
        "combined_processing": value[
            "n_spatial_temporal_fallback_preprocessing_path"
        ],
        "clip_embeddings": value["n_clip_embeddings"],
        "study_embeddings": value["n_study_embeddings"],
        "prespecified_no_cine": value["n_prespecified_no_cine_studies"],
        "new_no_cine": value["n_new_no_cine_studies"],
        "retired_cache_bytes": value["retired_extracted_cache_bytes"],
        "preservation": "PASS",
        "cache_retirement": "PASS",
        "finalization": "PASS",
        "final_receipt_sha256": value[
            "batch_finalization_receipt_sha256"
        ],
    }
    report["batches"][f"batch_{ordinal + 1}"] = projection
    report["finalized_batches"] = ordinal + 1
    return dict(value)


def _load_tail_batch(
    *,
    task_id: int,
    ordinal: int,
    expected_studies: int,
    expected_objects: int,
    expected_bytes: int,
    plan: Mapping[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = evidence.load_batch_metadata(ordinal, plan=plan)
    except evidence.R7GEvidenceError as exc:
        _tail_failure(
            task_id=task_id,
            ordinal=ordinal,
            stage=_task_evidence_stage(exc.code),
            code=exc.code,
            report=report,
        )
    return _validate_tail_projection(
        value,
        task_id=task_id,
        ordinal=ordinal,
        expected_studies=expected_studies,
        expected_objects=expected_objects,
        expected_bytes=expected_bytes,
        report=report,
    )


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
        != metadata.BATCH16_FINAL_RECEIPT_SHA256
    ):
        raise RuntimeError("R8U_R7G_FROZEN_PREFIX_CONTRADICTION")


def _receipt_timestamp(path: Path) -> str:
    if not os.path.lexists(path):
        return _utc_now()
    value, _payload = metadata.read_private_json(path)
    timestamp = value.get("created_at_utc")
    if not isinstance(timestamp, str):
        raise metadata.R7GMetadataError(
            "EXISTING_RECEIPT_CONTRADICTS_EXPECTED"
        )
    return timestamp


def _r7f_authority_hashes(
    terminal_authority: Mapping[str, Any],
) -> dict[str, str]:
    fixed = terminal_authority["r7f_authority_receipt_sha256"]
    return {
        "r7f_capacity_receipt_sha256": fixed["capacity"],
        "r7f_probe_terminal_receipt_sha256": terminal_authority[
            "probe_terminal_receipt_sha256"
        ],
        "r7f_continuation_claim_sha256": fixed["continuation_claim"],
        "r7f_array_submission_receipt_sha256": fixed["array_submission"],
        "r7f_finalizer_submission_receipt_sha256": fixed[
            "finalizer_submission"
        ],
        "r7f_combined_submission_receipt_sha256": fixed[
            "combined_submission"
        ],
    }


def _validate_compact_output_machinery() -> None:
    """Exercise both current R7G compact serializers without touching disk."""

    specimen = {"z": [0, False, None], "a": "\u00e9"}
    expected = b'{"a":"\\u00e9","z":[0,false,null]}'
    try:
        accounting_bytes = accounting.core.canonical_json_bytes(specimen)
        metadata_bytes = metadata.canonical_json_bytes(specimen)
    except Exception as exc:
        raise RuntimeError(
            "R8U_R7G_CURRENT_OUTPUT_CANONICAL_BYTES_INVALID"
        ) from exc
    if accounting_bytes != expected or metadata_bytes != expected:
        raise RuntimeError("R8U_R7G_CURRENT_OUTPUT_CANONICAL_BYTES_INVALID")


def _preflight_fixed_authorities(
    *, adjudication_implementation_commit: str,
) -> Mapping[str, Any]:
    """Read and validate every fixed input without publishing or querying."""

    plan = evidence.load_fixed_plan()
    accounting.preflight_fixed_terminal_authority(
        plan=plan,
        adjudication_implementation_commit=adjudication_implementation_commit,
    )
    _validate_compact_output_machinery()
    return plan


def _is_historical_authority_error(code: str) -> bool:
    return (
        code.startswith("R8U_R7G_AUTHORITY_")
        or code.startswith("R8U_R7G_R7F_AUTHORITY_")
        or code
        in {
            "R8U_R7G_HISTORICAL_SERIALIZATION_ROLE_INVALID",
            "R8U_R7G_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
        }
    )


def adjudicate_fixed_r7f_existing_jobs() -> dict[str, Any]:
    """Adjudicate four fixed records, validate 19 batches, and seal the lock."""

    try:
        adjudication_commit, _state = _repository_authority()
    except RuntimeError as exc:
        report = _initial_report("NOT_VALIDATED")
        _stop(IMPLEMENTATION_STOP, str(exc), report)
    report = _initial_report(adjudication_commit)

    try:
        plan = _preflight_fixed_authorities(
            adjudication_implementation_commit=adjudication_commit
        )
    except evidence.R7GEvidenceError as exc:
        _stop(HISTORICAL_STOP, exc.code, report, first_failed_stage="PLAN_AUTHORITY")
    except accounting.R7GAccountingError as exc:
        destination = (
            HISTORICAL_STOP
            if _is_historical_authority_error(exc.code)
            else IMPLEMENTATION_STOP
        )
        _stop(destination, exc.code, report, first_failed_stage="AUTHORITY_PREFLIGHT")
    except RuntimeError as exc:
        _stop(IMPLEMENTATION_STOP, str(exc), report)

    try:
        authority_result = accounting.ensure_terminal_authority(
            plan=plan,
            adjudication_implementation_commit=adjudication_commit,
        )
        terminal_authority = authority_result.authority
        environment = accounting.validate_fixed_scheduler_accounting_environment(
            terminal_authority
        )
        specs = accounting.fixed_accounting_specs(terminal_authority)
    except accounting.R7GAccountingError as exc:
        if _is_historical_authority_error(exc.code):
            _stop(
                HISTORICAL_STOP,
                exc.code,
                report,
                first_failed_stage="TERMINAL_AUTHORITY_REVALIDATION",
            )
        _implementation_stop(
            exc.code,
            report,
            function="accounting.ensure_terminal_authority",
            predicate=exc.code,
            artifact=str(accounting.TERMINAL_AUTHORITY_PATH),
        )
    if len(specs) != 4 or [spec.task_id for spec in specs] != [17, 18, 19, None]:
        _implementation_stop(
            "R8U_R7G_ACCOUNTING_SCOPE_INVALID",
            report,
            function="accounting.fixed_accounting_specs",
            predicate="EXACT_FOUR_FIXED_R7F_RECORDS",
            artifact=str(accounting.ACCOUNTING_ROOT),
        )
    report["terminal_authority_created"] = authority_result.created
    report["terminal_authority_sha256"] = authority_result.authority_sha256
    report["r7f_probe_terminal_receipt_sha256"] = terminal_authority[
        "probe_terminal_receipt_sha256"
    ]

    tail_metadata: list[dict[str, Any]] = []
    for spec, fixed in zip(specs[:3], TAIL_TASKS, strict=True):
        task_id, ordinal, studies, objects, source_bytes = fixed
        key = f"task_{task_id}"
        result = _obtain_accounting(
            spec,
            key=key,
            terminal_authority=terminal_authority,
            environment=environment,
            report=report,
        )
        log = report["accounting"][key]["terminal_log"]
        if result.receipt["terminal_classification"] != "PASS":
            failed_stage = log.get("first_failed_stage")
            if failed_stage in {None, "NOT_REPORTED", "NOT_APPLICABLE"}:
                failed_stage = "TERMINAL_ACCOUNTING"
            _tail_failure(
                task_id=task_id,
                ordinal=ordinal,
                stage=failed_stage,
                code="R8U_R7G_CURRENT_TASK_ACCOUNTING_NONZERO",
                report=report,
            )
        if (
            log.get("status") != "FIXED_PASS_TERMINAL_LOG_VALIDATED"
            or log.get("failure_scope") != "NOT_APPLICABLE"
        ):
            _tail_failure(
                task_id=task_id,
                ordinal=ordinal,
                stage="TERMINAL_LOG_VALIDATION",
                code="R8U_R7G_CURRENT_TASK_PASS_LOG_CONTRADICTION",
                report=report,
            )
        tail_metadata.append(
            _load_tail_batch(
                task_id=task_id,
                ordinal=ordinal,
                expected_studies=studies,
                expected_objects=objects,
                expected_bytes=source_bytes,
                plan=plan,
                report=report,
            )
        )

    finalizer_spec = specs[3]
    finalizer_result = _obtain_accounting(
        finalizer_spec,
        key="finalizer",
        terminal_authority=terminal_authority,
        environment=environment,
        report=report,
    )
    finalizer_log = report["accounting"]["finalizer"]["terminal_log"]
    if finalizer_result.receipt["terminal_classification"] == "FAIL":
        if finalizer_log.get("failure_scope") != "CONTROL_PLANE_ONLY":
            _stop(
                COHORT_STOP,
                "R8U_R7G_FINALIZER_SUBSTANTIVE_FAILURE",
                report,
                first_failed_stage=finalizer_log.get(
                    "first_failed_stage", "FINALIZER"
                ),
            )
    elif (
        finalizer_log.get("status") != "FIXED_PASS_TERMINAL_LOG_VALIDATED"
        or finalizer_log.get("failure_scope") != "NOT_APPLICABLE"
    ):
        _stop(
            COHORT_STOP,
            "R8U_R7G_FINALIZER_PASS_LOG_CONTRADICTION",
            report,
            first_failed_stage="TERMINAL_LOG_VALIDATION",
        )

    try:
        prefix_metadata = [
            evidence.load_batch_metadata(ordinal, plan=plan)
            for ordinal in range(16)
        ]
        _validate_prefix(prefix_metadata)
        all_batch_metadata = [*prefix_metadata, *tail_metadata]
        reconciliation = metadata.validate_batch_metadata_partition(
            plan,
            all_batch_metadata,
            expected_plan_sha256=metadata.PLAN_SHA256,
            expected_attempt_id=metadata.ATTEMPT_ID,
            expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
        )
        original_cohort = evidence.load_original_cohort_finalization_receipt(
            all_batch_metadata
        )
        cache_topology = evidence.fixed_cache_topology()
    except evidence.R7GEvidenceError as exc:
        _stop(COHORT_STOP, exc.code, report)
    except metadata.R7GMetadataError as exc:
        _stop(COHORT_STOP, exc.code, report)
    except RuntimeError as exc:
        _stop(COHORT_STOP, str(exc), report)

    accounting_hashes = {
        key: report["accounting"][key]["receipt_sha256"]
        for key in ("task_17", "task_18", "task_19", "finalizer")
    }
    r7f_hashes = _r7f_authority_hashes(terminal_authority)
    zero_actions = metadata.zero_scientific_actions()
    try:
        cohort_timestamp = _receipt_timestamp(COHORT_RECEIPT_PATH)
    except metadata.R7GMetadataError as exc:
        _stop(COHORT_STOP, exc.code, report)
    cohort_kwargs = {
        "attempt_id": metadata.ATTEMPT_ID,
        "plan_sha256": metadata.PLAN_SHA256,
        "scientific_commit": metadata.SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": (
            accounting.RUNTIME_IMPLEMENTATION_COMMIT
        ),
        "base_adjudication_implementation_commit": (
            R7G_BASE_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": adjudication_commit,
        "terminal_authority_sha256": authority_result.authority_sha256,
        "r7f_authority_receipt_sha256": r7f_hashes,
        "accounting_receipt_sha256": accounting_hashes,
        "cache_topology": cache_topology,
        "prohibited_actions": zero_actions,
        "created_at_utc": cohort_timestamp,
    }
    try:
        cohort = metadata.build_cohort_finalization_receipt(
            plan, all_batch_metadata, **cohort_kwargs
        )
    except metadata.R7GMetadataError as exc:
        _implementation_stop(
            exc.code,
            report,
            function="metadata.build_cohort_finalization_receipt",
            predicate=exc.code,
            artifact=str(COHORT_RECEIPT_PATH),
        )
    try:
        # A valid historical finalizer receipt is reused as scientific source
        # evidence, but it predates and therefore cannot replace the canonical
        # R7G receipt that binds this commit, authority, and four accountings.
        cohort_sha, cohort_created = metadata.publish_cohort_finalization_receipt(
            COHORT_RECEIPT_PATH,
            cohort,
            validation_kwargs={
                "plan": plan,
                "batch_metadata": all_batch_metadata,
                **cohort_kwargs,
            },
        )
    except metadata.R7GMetadataError as exc:
        _stop(COHORT_STOP, exc.code, report)
    cohort_mode = (
        "R7G_METADATA_ONLY_PUBLICATION" if cohort_created else "REUSED_EXISTING"
    )
    report["original_cohort_finalization_receipt"] = (
        "VALID" if original_cohort is not None else "ABSENT"
    )
    report["cohort_finalization_mode"] = cohort_mode
    report["cohort_finalization_receipt_valid"] = True
    report["cohort_finalization_receipt_sha256"] = cohort_sha
    report["full_cohort_aggregates"] = dict(
        reconciliation["aggregate_totals"]
    )
    report["full_cohort_study_partition"] = dict(
        reconciliation["study_partition"]
    )
    report["cache_topology"] = dict(cache_topology)

    try:
        observed_quiescence = quiescence.capture_fixed_quiescence(
            terminal_authority=terminal_authority,
            environment=environment,
        )
        scheduler_state = dict(observed_quiescence.scheduler_state)
        lock_commit, repository_state = _repository_authority()
        if lock_commit != adjudication_commit:
            raise RuntimeError("R8U_R7G_LOCK_REPOSITORY_COMMIT_CHANGED")
        lock_timestamp = _receipt_timestamp(LOCK_RECEIPT_PATH)
    except quiescence.R7GQuiescenceError as exc:
        _stop(LOCK_STOP, exc.code, report)
    except metadata.R7GMetadataError as exc:
        _stop(LOCK_STOP, exc.code, report)
    except RuntimeError as exc:
        _stop(LOCK_STOP, str(exc), report)
    lock_kwargs = {
        "cohort_receipt_sha256": cohort_sha,
        "cohort_finalization_mode": cohort_mode,
        "repository_state": repository_state,
        "scheduler_state": scheduler_state,
        "expected_branch": BRANCH,
        "created_at_utc": lock_timestamp,
    }
    try:
        lock = metadata.build_post_reconstruction_lock_receipt(
            cohort, **lock_kwargs
        )
    except metadata.R7GMetadataError as exc:
        _implementation_stop(
            exc.code,
            report,
            function="metadata.build_post_reconstruction_lock_receipt",
            predicate=exc.code,
            artifact=str(LOCK_RECEIPT_PATH),
        )
    try:
        lock_sha, _lock_created = (
            metadata.publish_post_reconstruction_lock_receipt(
                LOCK_RECEIPT_PATH,
                lock,
                validation_kwargs={
                    "cohort_receipt": cohort,
                    **lock_kwargs,
                },
            )
        )
    except metadata.R7GMetadataError as exc:
        _stop(LOCK_STOP, exc.code, report)

    report["post_reconstruction_lock_review"] = "PASS"
    report["post_reconstruction_lock_receipt_sha256"] = lock_sha
    report["current_r7f_task_runtime_status"] = "ADJUDICATED"
    report["finalized_batches"] = 19
    report["status"] = SUCCESS
    report["error_code"] = "NONE"
    return report


def guarded_main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    if selected == [PREFLIGHT_FLAG]:
        try:
            adjudication_commit, _state = _repository_authority()
            _preflight_fixed_authorities(
                adjudication_implementation_commit=adjudication_commit
            )
        except Exception as exc:
            code = getattr(
                exc, "code", "R8U_R7G_R1_AUTHORITY_PREFLIGHT_FAILED"
            )
            if not isinstance(code, str) or re.fullmatch(
                r"[A-Z0-9_]+", code
            ) is None:
                code = "R8U_R7G_R1_AUTHORITY_PREFLIGHT_FAILED"
            print(f"FAIL_R7G_R1_ALL_FIXED_AUTHORITY_INPUTS={code}")
            return 78
        print(PREFLIGHT_PASS)
        return 0
    if selected != [ENTRYPOINT_FLAG]:
        print(f"R8U_R7G_STATUS={IMPLEMENTATION_STOP}")
        print("R8U_R7G_ERROR_CODE=R8U_R7G_CLOSED_ARGUMENTS_INVALID")
        return 64
    try:
        result = adjudicate_fixed_r7f_existing_jobs()
    except R7GTerminalStop as exc:
        print(f"R8U_R7G_STATUS={exc.status}")
        print(f"R8U_R7G_ERROR_CODE={exc.code}")
        print(
            "R8U_R7G_REPORT_JSON="
            + json.dumps(exc.report, sort_keys=True, separators=(",", ":"))
        )
        return 78
    except Exception as exc:
        code = getattr(exc, "code", "R8U_R7G_UNEXPECTED_SANITIZED_FAILURE")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "R8U_R7G_UNEXPECTED_SANITIZED_FAILURE"
        report = _initial_report("NOT_VALIDATED")
        report.update(
            {
                "status": IMPLEMENTATION_STOP,
                "error_code": code,
                "structural_function": "guarded_main",
                "structural_predicate": code,
                "structural_artifact": str(Path(__file__).resolve()),
            }
        )
        print(f"R8U_R7G_STATUS={IMPLEMENTATION_STOP}")
        print(f"R8U_R7G_ERROR_CODE={code}")
        print(
            "R8U_R7G_REPORT_JSON="
            + json.dumps(report, sort_keys=True, separators=(",", ":"))
        )
        return 78
    print(f"R8U_R7G_STATUS={result['status']}")
    print(
        "R8U_R7G_REPORT_JSON="
        + json.dumps(result, sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(guarded_main())
