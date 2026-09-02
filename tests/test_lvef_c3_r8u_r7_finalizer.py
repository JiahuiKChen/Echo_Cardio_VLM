#!/usr/bin/env python3
"""Dependency-light R8U-R7 cohort-finalizer compatibility checks."""
from __future__ import annotations

from dataclasses import fields
import ast
import inspect
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_r8r_recovery_continuation as controller


def test_r7_finalizer_api_is_additive() -> None:
    parameters = inspect.signature(finalizer.finalize_receipts).parameters
    assert "r8u_r6_implementation_authority" in parameters
    assert "r8u_r7_implementation_authority" in parameters
    assert hasattr(finalizer, "R8UR6ImplementationAuthority")
    assert hasattr(finalizer, "R8UR7ImplementationAuthority")
    assert callable(finalizer._validate_r8u_r6_mixed_implementation_epochs)
    assert callable(finalizer._validate_r8u_r7_mixed_implementation_epochs)


def test_r7_finalizer_mirrors_every_controller_schema() -> None:
    names = (
        "R8U_R7_ACCOUNT_AUTHORITY_KEYS",
        "R8U_R7_R6_FAILURE_EVIDENCE_KEYS",
        "R8U_R7_CAPACITY_KEYS",
        "R8U_R7_AUTHORITY_KEYS",
        "R8U_R7_CLAIM_KEYS",
        "R8U_R7_SUBMISSION_KEYS",
        "R8U_R7_ACCOUNTING_KEYS",
        "R8U_R7_TERMINAL_KEYS",
        "R8U_R7_CONTINUATION_LINK_KEYS",
        "R8U_R7_CONTINUATION_CLAIM_KEYS",
        "R8U_R7_CONTINUATION_SUBMISSION_KEYS",
        "R8U_R7_CONTINUATION_WORKER_RECEIPT_KEYS",
    )
    for name in names:
        assert getattr(finalizer, name) == getattr(controller, name), name


def test_r7_finalizer_authority_covers_the_closed_chain_once() -> None:
    authority_fields = [
        item.name for item in fields(finalizer.R8UR7ImplementationAuthority)
    ]
    assert len(authority_fields) == len(set(authority_fields))
    chain_fields = [item[0] for item in finalizer.R8U_R7_CHAIN_ARTIFACT_SPECS]
    assert len(chain_fields) == len(set(chain_fields))
    assert set(chain_fields) == set(finalizer.R8U_R7_CHAIN_ARTIFACT_KEYS)
    assert set(chain_fields) < set(authority_fields)
    assert "preservation_recovery_claim_sha256" in authority_fields
    assert "r8u_r6_scheduler_account_authority_sha256" in authority_fields
    assert "scheduler_account_authority_sha256" in authority_fields


def test_r7_controller_constructs_the_exact_finalizer_authority() -> None:
    source = inspect.getsource(controller.run_r8u_r7_continuation_finalizer)
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "R8UR7ImplementationAuthority"
    ]
    assert len(calls) == 1
    supplied = {keyword.arg for keyword in calls[0].keywords}
    expected = {
        item.name for item in fields(finalizer.R8UR7ImplementationAuthority)
    }
    assert supplied == expected
    assert "r8u_r7_implementation_authority=authority" in source


def test_r7_recovery_qsub_reconstruction_matches_controller() -> None:
    implementation_commit = "7" * 40
    controller_command = controller._r8u_r7_recovery_qsub_command(
        implementation_commit
    )
    finalizer_command = finalizer._r8u_r7_expected_recovery_qsub_command(
        attempt_root=controller.ATTEMPT_ROOT,
        implementation_commit=implementation_commit,
    )
    assert finalizer_command == controller_command


def test_r7_finalizer_uses_r7_batch16_then_exact_tasks17_19() -> None:
    validator = inspect.getsource(finalizer._validate_r8u_r7_recovery_successor)
    mixed = inspect.getsource(finalizer._validate_r8u_r7_mixed_implementation_epochs)
    assert '"npz_files_expected": 10_187' in validator
    assert '"extracted_npz_body_reads": 0' in validator
    assert '"preservation_status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"' in validator
    assert '"cache_retirement_status": "PASS_RETIRED"' in validator
    assert '"batch_finalization_status": "PASS_BATCH_FINALIZED"' in validator
    assert '"array_task_range": "17-19"' in validator
    assert '"array_max_concurrency": 1' in validator
    assert '"total_new_qsub_submissions": 3' in validator
    assert '"fourth_submission_reachable": False' in validator
    assert "receipts[:2]" in mixed
    assert "receipts[2:15]" in mixed
    assert "receipts[15:]" in mixed


def test_r7_finalizer_preserves_the_r6_failure_as_history() -> None:
    source = inspect.getsource(finalizer._validate_r8u_r7_chain_artifacts)
    assert 'r6_failure.get("scheduler_failed") != 0' in source
    assert 'r6_failure.get("application_exit_status") != 78' in source
    assert '!= "R8U_R3_EXTRACTION_NPZ_METADATA_INVALID"' in source
    assert (
        '!= "PASS_R8U_R6_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER"'
        in source
    )


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
