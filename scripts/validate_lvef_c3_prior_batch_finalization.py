#!/usr/bin/env python3
"""Fail closed unless the immediately prior C3 batch is fully finalized."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finalize_lvef_c3_production as finalizer
import lvef_c3_orchestration_core as core


BATCH_RE = re.compile(r"^c3_batch_(00[1-9]|01[0-8])$")


class PriorBatchGateError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def prior_batch_id(current_batch_id: str) -> str:
    if not BATCH_RE.fullmatch(current_batch_id):
        raise PriorBatchGateError("CURRENT_BATCH_NOT_REMAINING_BATCH")
    return f"c3_batch_{int(current_batch_id.rsplit('_', 1)[1]) - 1:03d}"


def validate_prior_batch(
    *, current_batch_id: str, attempt_id: str, governing_commit: str,
    contract_path: Path, plan_path: Path, environment_receipt: Path,
    final_receipt_path: Path, final_ledger_path: Path, transition_path: Path,
) -> None:
    previous = prior_batch_id(current_batch_id)
    contract = core.load_orchestration_contract(contract_path)
    plan = core.load_strict_json(plan_path)
    requirements = core.production_requirements(contract)
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    planned = {row["batch_id"]: row for row in plan["batches"]}
    if previous not in planned or current_batch_id not in planned:
        raise PriorBatchGateError("BATCH_SEQUENCE_NOT_PLANNED")
    receipt = finalizer.load_json(final_receipt_path, "PRIOR_FINAL_RECEIPT")
    try:
        finalizer._validate_receipt(receipt)
    except finalizer.ProductionFinalizationError as exc:
        raise PriorBatchGateError("PRIOR_FINAL_RECEIPT_INVALID") from exc
    if (
        receipt.get("batch_id") != previous
        or receipt.get("attempt_id") != attempt_id
        or receipt.get("governing_commit") != governing_commit
        or receipt.get("batch_plan_sha256") != plan_sha
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
    ):
        raise PriorBatchGateError("PRIOR_FINAL_RECEIPT_AUTHORITY_MISMATCH")
    ledger = core.load_strict_json(final_ledger_path)
    expected_authority = core.validate_ledger_against_current_runtime(
        ledger,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=contract_path,
        governing_commit=governing_commit,
        environment_receipt_sha256=finalizer.sha256_file(environment_receipt),
        batch_id=previous,
    )
    core.validate_resume_authority(
        ledger,
        expected_authority=expected_authority,
        attempt_id=attempt_id,
        expected_object_keys={
            previous: {row["source_object_key"] for row in planned[previous]["objects"]}
        },
    )
    batch = ledger["batches"][previous]
    if ledger.get("status") != "COMPLETE" or batch.get("state") != "FINALIZED":
        raise PriorBatchGateError("PRIOR_BATCH_NOT_FINALIZED")
    transition = core.load_strict_json(transition_path)
    if (
        transition.get("attempt_id") != attempt_id
        or transition.get("batch_id") != previous
        or transition.get("from_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or transition.get("to_state") != "FINALIZED"
        or transition.get("output_manifest_sha256")
        != finalizer.sha256_file(final_receipt_path)
        or not batch["events"]
        or batch["events"][-1].get("receipt_sha256")
        != core.canonical_json_sha256(transition)
    ):
        raise PriorBatchGateError("PRIOR_FINALIZATION_TRANSITION_INVALID")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-batch-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--prior-final-receipt", type=Path, required=True)
    parser.add_argument("--prior-final-ledger", type=Path, required=True)
    parser.add_argument("--prior-transition-receipt", type=Path, required=True)
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_prior_batch(
            current_batch_id=args.current_batch_id,
            attempt_id=args.attempt_id,
            governing_commit=args.governing_commit,
            contract_path=args.contract,
            plan_path=args.plan,
            environment_receipt=args.environment_receipt,
            final_receipt_path=args.prior_final_receipt,
            final_ledger_path=args.prior_final_ledger,
            transition_path=args.prior_transition_receipt,
        )
    except PriorBatchGateError as exc:
        print(f"C3_PRIOR_BATCH_FINALIZATION_GATE=BLOCKED_{exc.code}")
        return 78
    except Exception:
        print("C3_PRIOR_BATCH_FINALIZATION_GATE=BLOCKED_UNEXPECTED_SANITIZED_EXCEPTION")
        return 78
    print("C3_PRIOR_BATCH_FINALIZATION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(guarded_main())
