#!/usr/bin/env python3
"""Focused dependency-light R8U-R5 cohort-finalizer authority proofs."""
from __future__ import annotations

import copy
import errno
import inspect
from pathlib import Path
import sys
import traceback
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_r8r_recovery_continuation as controller


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def _expect_code(action: Callable[[], Any], expected: str) -> None:
    try:
        action()
    except Exception as exc:
        assert _code(exc) == expected, (_code(exc), expected)
    else:
        raise AssertionError(f"expected {expected}")


def _account_authority() -> dict[str, Any]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        "SGE_ROOT": "/usr/local/ogs-ge2011.11.p1/sge_root",
        "SGE_CELL": "default",
        "SGE_QMASTER_PORT": "6444",
        "HOME": "/restricted/home/tester",
        "USER": "tester",
        "LOGNAME": "tester",
        "SHELL": "/bin/bash",
    }
    return {
        "expected_effective_uid": 12345,
        "expected_scheduler_username": "tester",
        "canonical_home": "/restricted/home/tester",
        "submitter_passwd_lookup_available": True,
        "runner_sha256": "a" * 64,
        "python_sha256": "b" * 64,
        "qsub_environment_sha256": (
            finalizer._r8u_r5_qsub_environment_sha256(environment)
        ),
        "sealed_qsub_environment": environment,
        "authorized_worker_roles": [
            "R8U_R5_WORKER_CONTEXT_PROBE",
            "R8U_R5_BATCH16_PUBLICATION_RESUME",
            "R8U_R5_CONTINUATION_ARRAY",
            "R8U_R5_COHORT_FINALIZER",
        ],
    }


def test_r5_finalizer_api_is_additive_and_r4_is_preserved() -> None:
    parameters = inspect.signature(finalizer.finalize_receipts).parameters
    assert "r8u_r4_implementation_authority" in parameters
    assert "r8u_r5_implementation_authority" in parameters
    assert hasattr(finalizer, "R8UR4ImplementationAuthority")
    assert hasattr(finalizer, "R8UR5ImplementationAuthority")
    assert callable(finalizer._validate_r8u_r4_mixed_implementation_epochs)
    assert callable(finalizer._validate_r8u_r5_mixed_implementation_epochs)
    assert finalizer.R8U_R4_PORTABILITY_REPAIR_IMPLEMENTATION_COMMIT == (
        "6eb5c9a4337ca4569ecd0d3157084fb4b76adfac"
    )


def test_r5_finalizer_mirrors_every_materialized_controller_schema() -> None:
    pairs = (
        ("R8U_R5_ACCOUNT_AUTHORITY_KEYS", "R8U_R5_ACCOUNT_AUTHORITY_KEYS"),
        ("R8U_R5_R4_FAILURE_EVIDENCE_KEYS", "R8U_R5_R4_FAILURE_EVIDENCE_KEYS"),
        ("R8U_R5_PROBE_AUTHORITY_KEYS", "R8U_R5_PROBE_AUTHORITY_KEYS"),
        ("R8U_R5_PROBE_SUBMISSION_KEYS", "R8U_R5_PROBE_SUBMISSION_KEYS"),
        ("R8U_R5_WORKER_DIAGNOSTIC_KEYS", "R8U_R5_WORKER_DIAGNOSTIC_KEYS"),
        ("R8U_R5_PROBE_RECEIPT_KEYS", "R8U_R5_PROBE_RECEIPT_KEYS"),
        ("R8U_R5_PROBE_ACCOUNTING_KEYS", "R8U_R5_PROBE_ACCOUNTING_KEYS"),
        ("R8U_R5_CAPACITY_KEYS", "R8U_R5_CAPACITY_KEYS"),
        ("R8U_R5_AUTHORITY_KEYS", "R8U_R5_RESUME_AUTHORITY_KEYS"),
        ("R8U_R5_SUBMISSION_KEYS", "R8U_R5_RESUME_SUBMISSION_KEYS"),
        ("R8U_R5_LOCALITY_KEYS", "R8U_R5_LOCALITY_KEYS"),
        ("R8U_R5_PUBLICATION_CLAIM_KEYS", "R8U_R5_PUBLICATION_CLAIM_KEYS"),
        ("R8U_R5_PROBE_KEYS", "R8U_R5_PRIMITIVE_PROBE_KEYS"),
        ("R8U_R5_PUBLICATION_KEYS", "R8U_R5_PUBLICATION_KEYS"),
        ("R8U_R5_ACCOUNTING_KEYS", "R8U_R5_RESUME_ACCOUNTING_KEYS"),
        ("R8U_R5_TERMINAL_KEYS", "R8U_R5_TERMINAL_KEYS"),
        ("R8U_R5_CONTINUATION_LINK_KEYS", "R8U_R5_CONTINUATION_LINK_KEYS"),
        ("R8U_R5_CONTINUATION_CLAIM_KEYS", "R8U_R5_CONTINUATION_CLAIM_KEYS"),
        (
            "R8U_R5_CONTINUATION_SUBMISSION_KEYS",
            "R8U_R5_CONTINUATION_SUBMISSION_KEYS",
        ),
        (
            "R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS",
            "R8U_R5_CONTINUATION_WORKER_RECEIPT_KEYS",
        ),
    )
    for finalizer_name, controller_name in pairs:
        assert getattr(finalizer, finalizer_name) == getattr(
            controller, controller_name
        )


def test_r5_finalizer_never_reconstructs_the_qsub_submitter_environment() -> None:
    source = inspect.getsource(finalizer)
    assert "build_qsub_environment" not in source
    assert "scheduler_account_authority_sha256" in source
    assert "worker_context_diagnostic_sha256" in source
    assert "worker_qstat_projection_sha256" in source
    assert "worker_process_projection_sha256" in source


def test_r5_sealed_scheduler_account_is_closed_and_hash_bound() -> None:
    authority = _account_authority()
    assert len(finalizer._validate_r8u_r5_scheduler_account(authority)) == 64

    drifted = copy.deepcopy(authority)
    drifted["sealed_qsub_environment"]["USER"] = "ambient-worker-name"
    drifted["qsub_environment_sha256"] = (
        finalizer._r8u_r5_qsub_environment_sha256(
            drifted["sealed_qsub_environment"]
        )
    )
    _expect_code(
        lambda: finalizer._validate_r8u_r5_scheduler_account(drifted),
        "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID",
    )

    forwarded = copy.deepcopy(authority)
    forwarded["sealed_qsub_environment"]["AWS_SECRET_ACCESS_KEY"] = "forbidden"
    forwarded["qsub_environment_sha256"] = (
        finalizer._r8u_r5_qsub_environment_sha256(
            forwarded["sealed_qsub_environment"]
        )
    )
    _expect_code(
        lambda: finalizer._validate_r8u_r5_scheduler_account(forwarded),
        "R8U_R5_FINALIZER_SCHEDULER_ACCOUNT_INVALID",
    )


def test_r5_qstat_projection_is_role_and_digest_bound() -> None:
    body = {
        "status": "PASS_EXACT_ONE_R8U_R5_WORKER_JOB_ZERO_COMPETITORS",
        "resume_job_id": "12345",
        "resume_job_name": "lvef_c3_r8u_r5_fin_deadbeef",
        "state": "r",
        "category": "running",
        "target_matches": 1,
        "competing_matching_jobs": 0,
        "qstat_snapshot_count": 1,
    }
    value = {
        **body,
        "qstat_projection_sha256": finalizer.core.canonical_json_sha256(body),
    }
    assert len(finalizer._validate_r8u_r5_qstat_projection(
        value,
        job_id="12345",
        job_name="lvef_c3_r8u_r5_fin_deadbeef",
        expected_status="PASS_EXACT_ONE_R8U_R5_WORKER_JOB_ZERO_COMPETITORS",
    )) == 64
    drifted = {**value, "resume_job_id": "54321"}
    _expect_code(
        lambda: finalizer._validate_r8u_r5_qstat_projection(
            drifted,
            job_id="12345",
            job_name="lvef_c3_r8u_r5_fin_deadbeef",
            expected_status=(
                "PASS_EXACT_ONE_R8U_R5_WORKER_JOB_ZERO_COMPETITORS"
            ),
        ),
        "R8U_R5_FINALIZER_QSTAT_PROJECTION_INVALID",
    )


def test_r4_publication_regression_calls_the_defined_rename_validator() -> None:
    source = inspect.getsource(finalizer._validate_r8u_r4_chain_artifacts)
    assert "_validate_r8u_r3_real_rename_outcome(" in source
    assert "_validate_r8u_r3_publication_outcome(" not in source
    finalizer._validate_r8u_r3_real_rename_outcome(
        returned_success=False,
        errno_number=errno.EIO,
        errno_name="EIO",
        errno_classification="RENAME_ERROR_IO",
        publication_ruling="PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN",
    )


def test_r5_future_continuation_counts_probe_and_gpu_before_finalizer() -> None:
    assert "scheduler_account_authority_sha256" in (
        finalizer.R8U_R5_CONTINUATION_LINK_KEYS
    )
    assert "fifth_submission_reachable" in (
        finalizer.R8U_R5_CONTINUATION_CLAIM_KEYS
    )
    assert "fifth_submission_reachable" in (
        finalizer.R8U_R5_CONTINUATION_SUBMISSION_KEYS
    )
    source = inspect.getsource(finalizer._validate_r8u_r5_successor_artifacts)
    assert '"total_new_qsub_maximum": 4' in source
    assert '"total_new_qsub_submissions": 4' in source
    assert '"fifth_submission_reachable": False' in source


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
