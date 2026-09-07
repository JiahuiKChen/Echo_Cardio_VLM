#!/usr/bin/env python3
"""Focused, dependency-light R7D sequential/finalizer contracts."""
from __future__ import annotations

from dataclasses import fields, replace
import inspect
from pathlib import Path
import sys
import traceback
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_full_sequential as sequential


CURRENT_EPOCH = tuple(str(index) * 64 for index in range(5))
ORIGINAL_EPOCH = tuple(chr(ord("a") + index) * 64 for index in range(5))


def _authority() -> finalizer.R8UR7DImplementationAuthority:
    return finalizer.R8UR7DImplementationAuthority(
        implementation_commit="f" * 40,
        prior_r7_runtime_commit=(
            finalizer.R8U_R7_RUNTIME_IMPLEMENTATION_COMMIT
        ),
        r7c_adjudication_commit=(
            finalizer.R8U_R7C_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        finalized_prefix_receipt_sha256=(
            finalizer.R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256
        ),
        consumed_r7a_continuation_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_CONTINUATION_RECEIPT_SHA256
        ),
        consumed_task17_accounting_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_TASK17_ACCOUNTING_RECEIPT_SHA256
        ),
        consumed_task18_accounting_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_TASK18_ACCOUNTING_RECEIPT_SHA256
        ),
        consumed_task19_accounting_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_TASK19_ACCOUNTING_RECEIPT_SHA256
        ),
        consumed_finalizer_accounting_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_FINALIZER_ACCOUNTING_RECEIPT_SHA256
        ),
        capacity_receipt_sha256="4" * 64,
        scheduler_account_authority_sha256="5" * 64,
        probe_terminal_receipt_sha256="6" * 64,
        continuation_claim_sha256="7" * 64,
        continuation_submission_receipt_sha256="8" * 64,
    )


def _receipts() -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for index, batch_id in enumerate(finalizer.EXPECTED_BATCH_IDS):
        if index < 2:
            epoch = ORIGINAL_EPOCH
        elif index < 15:
            epoch = finalizer.R8U_FE3_IMPLEMENTATION_EPOCH
        elif index == 15:
            epoch = finalizer.R8U_R7_BATCH16_IMPLEMENTATION_EPOCH
        else:
            epoch = CURRENT_EPOCH
        receipt = {
            key: "shared-scientific-authority"
            for key in finalizer.R8R_SCIENTIFIC_AUTHORITY_KEYS
        }
        receipt.update(
            {
                "batch_id": batch_id,
                "governing_commit": (
                    finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
                ),
                "attempt_id": finalizer.R8R_ATTEMPT_ID,
                "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
                **dict(zip(finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS, epoch)),
            }
        )
        receipts.append(receipt)
    return receipts


def _receipt_hashes() -> dict[str, str]:
    return {
        batch_id: (
            finalizer.R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256[index]
            if index < 16
            else f"{index + 1:064x}"
        )
        for index, batch_id in enumerate(finalizer.EXPECTED_BATCH_IDS)
    }


def _runtime(receipts: list[dict[str, Any]]) -> dict[str, str]:
    return {
        "git_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
        "orchestration_contract_sha256": receipts[0][
            "orchestration_contract_sha256"
        ],
        "checkpoint_sha256": receipts[0]["checkpoint_sha256"],
        "environment_receipt_sha256": receipts[0][
            "environment_receipt_sha256"
        ],
    }


def _validate(
    receipts: list[dict[str, Any]],
    *,
    authority: finalizer.R8UR7DImplementationAuthority | None = None,
    receipt_hashes: dict[str, str] | None = None,
) -> str:
    runtime = _runtime(receipts)
    with (
        mock.patch.object(
            finalizer.core,
            "validate_runtime_authority",
            return_value=runtime,
        ),
        mock.patch.object(
            finalizer,
            "_current_r8r_implementation_epoch",
            return_value=CURRENT_EPOCH,
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_r7d_repository_authority",
            return_value=None,
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_r7_mixed_implementation_epochs",
            side_effect=AssertionError("old R7 finalizer validator reached"),
        ),
    ):
        return finalizer._validate_r8u_r7d_mixed_implementation_epochs(
            receipts,
            receipt_hashes_by_batch=receipt_hashes or _receipt_hashes(),
            receipt_sizes_by_batch={},
            receipt_paths_by_batch={},
            expected_governing_commit=(
                finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
            ),
            expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
            expected_runtime_authority=runtime,
            authority=authority or _authority(),
            plan={"closed_test_plan": True},
        )


def _raises(code: str, function: Any) -> None:
    try:
        function()
    except finalizer.ProductionFinalizationError as exc:
        assert str(exc) == code
    else:
        raise AssertionError(f"expected {code}")


def test_r7d_sequential_context_is_fresh_and_tail_only() -> None:
    assert sequential.R8U_R7D_FIXED_CONTINUATION is (
        sequential.FullExecutionContext.R8U_R7D_FIXED_CONTINUATION
    )
    assert sequential.R8U_R7D_FIXED_CONTINUATION is not (
        sequential.R8U_R7_FIXED_CONTINUATION
    )
    source = inspect.getsource(sequential.run_batch_task)
    assert "FULL_SEQUENTIAL_R8U_R7D_CONTINUATION_TASK_OUT_OF_SCOPE" in source
    assert "FULL_SEQUENTIAL_R8U_R7D_EXTRACTION_CACHE_TOPOLOGY_INVALID" in source
    assert "_validate_r8u_r7d_worker_submission(" in source


def test_r7d_sequential_uses_injected_validator_without_old_r7_call() -> None:
    observed: list[tuple[str, str]] = []

    def validator(*, current_job_id: str, role: str) -> None:
        observed.append((current_job_id, role))

    dependency = sequential.resolve_dependencies(
        sequential.FullDependencies(
            execution_context=sequential.R8U_R7D_FIXED_CONTINUATION,
            r8u_r7d_worker_submission_validator=validator,
        )
    )
    sequential._validate_r8u_r7d_worker_submission(
        dependency,
        current_job_id="8123456",
        role="array",
    )
    assert observed == [("8123456", "array")]


def test_r7d_finalizer_api_and_authority_are_additive() -> None:
    parameters = inspect.signature(finalizer.finalize_receipts).parameters
    assert "r8u_r7_implementation_authority" in parameters
    assert "r8u_r7d_implementation_authority" in parameters
    names = {item.name for item in fields(finalizer.R8UR7DImplementationAuthority)}
    assert {
        "finalized_prefix_receipt_sha256",
        "consumed_r7a_continuation_receipt_sha256",
        "consumed_task17_accounting_receipt_sha256",
        "consumed_task18_accounting_receipt_sha256",
        "consumed_task19_accounting_receipt_sha256",
        "consumed_finalizer_accounting_receipt_sha256",
        "capacity_receipt_sha256",
        "scheduler_account_authority_sha256",
        "probe_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "continuation_submission_receipt_sha256",
    } < names


def test_r7d_finalizer_accepts_only_two_plus_thirteen_plus_one_plus_three() -> None:
    result = _validate(_receipts())
    assert len(result) == 64
    assert finalizer.SHA256_RE.fullmatch(result) is not None
    staged = finalizer._stage_authority_receipts(
        list(range(19)),
        r8r_mode=False,
        r8u_mode=False,
        r8u_r7d_mode=True,
    )
    assert staged == [16, 17, 18]


def test_r7d_finalizer_rejects_any_batch16_substitution() -> None:
    hashes = _receipt_hashes()
    hashes["c3_batch_015"] = "9" * 64
    _raises(
        "R8U_R7D_FINALIZER_PREFIX_RECEIPT_MISMATCH",
        lambda: _validate(_receipts(), receipt_hashes=hashes),
    )


def test_r7d_finalizer_rejects_old_r7_epoch_for_new_tail() -> None:
    receipts = _receipts()
    receipts[16].update(
        dict(
            zip(
                finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS,
                finalizer.R8U_R7_BATCH16_IMPLEMENTATION_EPOCH,
            )
        )
    )
    _raises(
        "R8U_R7D_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH",
        lambda: _validate(receipts),
    )


def test_r7d_finalizer_rejects_old_continuation_as_new_submission() -> None:
    authority = replace(
        _authority(),
        continuation_submission_receipt_sha256=(
            finalizer.R8U_R7D_CONSUMED_CONTINUATION_RECEIPT_SHA256
        ),
    )
    _raises(
        "R8U_R7D_FINALIZER_AUTHORITY_INVALID",
        lambda: _validate(_receipts(), authority=authority),
    )


def test_r7d_finalizer_rejects_prefix_authority_reordering() -> None:
    prefix = list(finalizer.R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256)
    prefix[0], prefix[1] = prefix[1], prefix[0]
    authority = replace(
        _authority(), finalized_prefix_receipt_sha256=tuple(prefix)
    )
    _raises(
        "R8U_R7D_FINALIZER_AUTHORITY_INVALID",
        lambda: _validate(_receipts(), authority=authority),
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
