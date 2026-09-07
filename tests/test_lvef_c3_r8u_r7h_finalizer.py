#!/usr/bin/env python3
"""Focused, dependency-light R7H finalizer authority contracts."""
from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer


IMPLEMENTATION_COMMIT = "f" * 40
CURRENT_EPOCH = tuple(str(index) * 64 for index in range(5))
ORIGINAL_EPOCH = tuple(chr(ord("a") + index) * 64 for index in range(5))


def _authority() -> finalizer.R8UR7HImplementationAuthority:
    return finalizer.R8UR7HImplementationAuthority(
        implementation_commit=IMPLEMENTATION_COMMIT,
        r7f_runtime_commit=finalizer.R8U_R7F_RUNTIME_IMPLEMENTATION_COMMIT,
        r7g_adjudication_commit=(
            finalizer.R8U_R7G_R1_ADJUDICATION_IMPLEMENTATION_COMMIT
        ),
        finalized_prefix_receipt_sha256=(
            finalizer.R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256
        ),
        historical_failed_partial_seal_sha256="d" * 64,
        consumed_r7f_continuation_receipt_sha256=(
            finalizer.R8U_R7H_CONSUMED_R7F_CONTINUATION_RECEIPT_SHA256
        ),
        consumed_task17_accounting_receipt_sha256=(
            finalizer.R8U_R7H_CONSUMED_TASK17_ACCOUNTING_RECEIPT_SHA256
        ),
        consumed_task18_accounting_receipt_sha256="1" * 64,
        consumed_task19_accounting_receipt_sha256="2" * 64,
        consumed_finalizer_accounting_receipt_sha256="3" * 64,
        consumed_r7f_evidence_sha256="4" * 64,
        topology_authority_sha256="5" * 64,
        capacity_receipt_sha256="6" * 64,
        scheduler_account_authority_sha256="7" * 64,
        probe_terminal_receipt_sha256="8" * 64,
        continuation_claim_sha256="9" * 64,
        array_submission_receipt_sha256="a" * 64,
        finalizer_submission_receipt_sha256="b" * 64,
        continuation_submission_receipt_sha256="c" * 64,
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
                "governing_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
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
    authority: finalizer.R8UR7HImplementationAuthority | None = None,
    receipt_hashes: dict[str, str] | None = None,
) -> str:
    runtime = _runtime(receipts)
    with (
        mock.patch.object(
            finalizer.core, "validate_runtime_authority", return_value=runtime
        ),
        mock.patch.object(
            finalizer,
            "_current_r8r_implementation_epoch",
            return_value=CURRENT_EPOCH,
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_r7h_repository_authority",
            return_value=None,
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_r7h_tail_receipt_serialization",
            return_value=None,
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_r7d_mixed_implementation_epochs",
            side_effect=AssertionError("consumed R7D validator reached"),
        ),
    ):
        return finalizer._validate_r8u_r7h_mixed_implementation_epochs(
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


def test_r7h_finalizer_api_is_strictly_additive() -> None:
    parameters = inspect.signature(finalizer.finalize_receipts).parameters
    assert "r8u_r7d_implementation_authority" in parameters
    assert "r8u_r7h_implementation_authority" in parameters
    assert {item.name for item in fields(finalizer.R8UR7HImplementationAuthority)} == {
        "implementation_commit",
        "r7f_runtime_commit",
        "r7g_adjudication_commit",
        "finalized_prefix_receipt_sha256",
        "historical_failed_partial_seal_sha256",
        "consumed_r7f_continuation_receipt_sha256",
        "consumed_task17_accounting_receipt_sha256",
        "consumed_task18_accounting_receipt_sha256",
        "consumed_task19_accounting_receipt_sha256",
        "consumed_finalizer_accounting_receipt_sha256",
        "consumed_r7f_evidence_sha256",
        "topology_authority_sha256",
        "capacity_receipt_sha256",
        "scheduler_account_authority_sha256",
        "probe_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "array_submission_receipt_sha256",
        "finalizer_submission_receipt_sha256",
        "continuation_submission_receipt_sha256",
    }


def test_r7h_repository_authority_requires_direct_child_of_r7g_r1() -> None:
    fixed_r7f = finalizer.R8U_R7F_RUNTIME_IMPLEMENTATION_COMMIT
    fixed_r7g = finalizer.R8U_R7G_R1_ADJUDICATION_IMPLEMENTATION_COMMIT
    exact = {
        ("rev-parse", "HEAD"): f"{IMPLEMENTATION_COMMIT}\n".encode(),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{IMPLEMENTATION_COMMIT}\n".encode(),
        ("branch", "--show-current"): (
            b"codex/lvef-multitask-revalidation\n"
        ),
        ("rev-list", "--parents", "-n", "1", IMPLEMENTATION_COMMIT): (
            f"{IMPLEMENTATION_COMMIT} {fixed_r7g}\n".encode()
        ),
        (
            "rev-list", "--count", f"{fixed_r7g}..{IMPLEMENTATION_COMMIT}"
        ): b"1\n",
    }
    observed: set[tuple[str, ...]] = set()

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        arguments = tuple(argv[3:])
        observed.add(arguments)
        if arguments in exact:
            return subprocess.CompletedProcess(argv, 0, exact[arguments], b"")
        if arguments[:2] in {
            ("cat-file", "-e"),
            ("merge-base", "--is-ancestor"),
        }:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if arguments == ("status", "--porcelain", "--untracked-files=no"):
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError((fixed_r7f, arguments))

    with mock.patch.object(finalizer.subprocess, "run", side_effect=run):
        finalizer._validate_r8u_r7h_repository_authority(
            IMPLEMENTATION_COMMIT
        )
    assert set(exact) <= observed


def test_r7h_repository_authority_rejects_nonchild() -> None:
    fixed_r7g = finalizer.R8U_R7G_R1_ADJUDICATION_IMPLEMENTATION_COMMIT
    initial = {
        ("rev-parse", "HEAD"): f"{IMPLEMENTATION_COMMIT}\n".encode(),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{IMPLEMENTATION_COMMIT}\n".encode(),
        ("branch", "--show-current"): (
            b"codex/lvef-multitask-revalidation\n"
        ),
        ("rev-list", "--parents", "-n", "1", IMPLEMENTATION_COMMIT): (
            f"{IMPLEMENTATION_COMMIT} {'e' * 40}\n".encode()
        ),
    }

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        arguments = tuple(argv[3:])
        if arguments in initial:
            return subprocess.CompletedProcess(argv, 0, initial[arguments], b"")
        raise AssertionError((fixed_r7g, arguments))

    with mock.patch.object(finalizer.subprocess, "run", side_effect=run):
        _raises(
            "R8U_R7H_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH",
            lambda: finalizer._validate_r8u_r7h_repository_authority(
                IMPLEMENTATION_COMMIT
            ),
        )


def test_r7h_accepts_only_fixed_prefix_plus_fresh_three_batch_tail() -> None:
    result = _validate(_receipts())
    assert finalizer.SHA256_RE.fullmatch(result) is not None
    staged = finalizer._stage_authority_receipts(
        list(range(19)),
        r8r_mode=False,
        r8u_mode=False,
        r8u_r7h_mode=True,
    )
    assert staged == [16, 17, 18]


def test_r7h_tail_receipts_require_exact_canonical_producer_bytes() -> None:
    receipts = _receipts()
    with tempfile.TemporaryDirectory() as directory:
        attempt_root = (
            Path(directory).resolve()
            / "attempts"
            / finalizer.R8R_ATTEMPT_ID
        )
        paths = {
            batch_id: (
                attempt_root
                / "batches"
                / batch_id
                / "preservation"
                / "batch_finalization_receipt.restricted.json"
            )
            for batch_id in finalizer.EXPECTED_BATCH_IDS
        }
        hashes = _receipt_hashes()
        sizes = {batch_id: 1 for batch_id in finalizer.EXPECTED_BATCH_IDS}
        for index in range(16, 19):
            batch_id = finalizer.EXPECTED_BATCH_IDS[index]
            path = paths[batch_id]
            path.parent.mkdir(parents=True, mode=0o700)
            payload = finalizer.core.canonical_json_bytes(receipts[index])
            path.write_bytes(payload)
            path.chmod(0o600)
            hashes[batch_id] = hashlib.sha256(payload).hexdigest()
            sizes[batch_id] = len(payload)
        finalizer._validate_r8u_r7h_tail_receipt_serialization(
            receipts,
            receipt_hashes_by_batch=hashes,
            receipt_sizes_by_batch=sizes,
            receipt_paths_by_batch=paths,
        )

        changed_batch = finalizer.EXPECTED_BATCH_IDS[16]
        arbitrary = (
            json.dumps(receipts[16], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        paths[changed_batch].write_bytes(arbitrary)
        paths[changed_batch].chmod(0o600)
        hashes[changed_batch] = hashlib.sha256(arbitrary).hexdigest()
        sizes[changed_batch] = len(arbitrary)
        _raises(
            "R8U_R7H_FINALIZER_TAIL_RECEIPT_SERIALIZATION_INVALID",
            lambda: finalizer._validate_r8u_r7h_tail_receipt_serialization(
                receipts,
                receipt_hashes_by_batch=hashes,
                receipt_sizes_by_batch=sizes,
                receipt_paths_by_batch=paths,
            ),
        )


def test_r7h_rejects_any_prefix16_substitution() -> None:
    hashes = _receipt_hashes()
    hashes["c3_batch_015"] = "e" * 64
    _raises(
        "R8U_R7H_FINALIZER_PREFIX_RECEIPT_MISMATCH",
        lambda: _validate(_receipts(), receipt_hashes=hashes),
    )


def test_r7h_requires_all_three_tail_epochs_to_be_current() -> None:
    for index in range(16, 19):
        receipts = _receipts()
        receipts[index].update(
            dict(
                zip(
                    finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS,
                    finalizer.R8U_R7_BATCH16_IMPLEMENTATION_EPOCH,
                )
            )
        )
        _raises(
            "R8U_R7H_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH",
            lambda receipts=receipts: _validate(receipts),
        )


def test_r7h_rejects_consumed_failure_hash_as_tail_success() -> None:
    hashes = _receipt_hashes()
    hashes["c3_batch_016"] = (
        finalizer.R8U_R7H_CONSUMED_TASK17_ACCOUNTING_RECEIPT_SHA256
    )
    _raises(
        "R8U_R7H_FINALIZER_CONSUMED_FAILURE_RECEIPT_REJECTED",
        lambda: _validate(_receipts(), receipt_hashes=hashes),
    )


def test_r7h_rejects_consumed_submission_as_new_submission() -> None:
    authority = replace(
        _authority(),
        continuation_submission_receipt_sha256=(
            finalizer.R8U_R7H_CONSUMED_R7F_CONTINUATION_RECEIPT_SHA256
        ),
    )
    _raises(
        "R8U_R7H_FINALIZER_AUTHORITY_INVALID",
        lambda: _validate(_receipts(), authority=authority),
    )


def test_r7h_requires_exact_consumed_r7f_fixed_hashes() -> None:
    for field_name in (
        "consumed_r7f_continuation_receipt_sha256",
        "consumed_task17_accounting_receipt_sha256",
    ):
        authority = replace(_authority(), **{field_name: "e" * 64})
        _raises(
            "R8U_R7H_FINALIZER_AUTHORITY_INVALID",
            lambda authority=authority: _validate(
                _receipts(), authority=authority
            ),
        )


def test_r7h_authority_serializer_binds_dynamic_evidence() -> None:
    first = _validate(_receipts())
    second = _validate(
        _receipts(),
        authority=replace(_authority(), topology_authority_sha256="e" * 64),
    )
    assert first != second


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
