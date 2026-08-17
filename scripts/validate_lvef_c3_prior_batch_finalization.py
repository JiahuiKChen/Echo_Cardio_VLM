#!/usr/bin/env python3
"""Fail closed unless the immediately prior C3 batch is fully finalized."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finalize_lvef_c3_production as finalizer
import lvef_c3_orchestration_core as core
import retire_lvef_c3_extracted_cache_v2 as retirement


BATCH_RE = re.compile(r"^c3_batch_(00[1-9]|01[0-8])$")


class PriorBatchGateError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def prior_batch_id(current_batch_id: str) -> str:
    if not BATCH_RE.fullmatch(current_batch_id):
        raise PriorBatchGateError("CURRENT_BATCH_NOT_REMAINING_BATCH")
    return f"c3_batch_{int(current_batch_id.rsplit('_', 1)[1]) - 1:03d}"


def validate_terminal_transition(
    transition: Mapping[str, Any], *, ledger: Mapping[str, Any],
    expected_authority: Mapping[str, Any], attempt_id: str, batch_id: str,
    final_receipt_sha256: str,
) -> None:
    """Validate the exact terminal receipt and its one-link predecessor chain."""

    batch = ledger["batches"][batch_id]
    events = batch["events"]
    if (
        set(transition) != set(core.RECEIPT_KEYS)
        or transition.get("schema_version") != 2
        or transition.get("receipt_type") != "lvef_c3_state_transition_v2"
        or transition.get("status") != "PASS"
        or transition.get("attempt_id") != attempt_id
        or transition.get("batch_id") != batch_id
        or transition.get("from_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or transition.get("to_state") != "FINALIZED"
        or core.validate_runtime_authority(transition.get("authority"))
        != core.validate_runtime_authority(expected_authority)
        or transition.get("output_manifest_sha256") != final_receipt_sha256
        or not isinstance(events, list)
        or len(events) < 2
        or events[-2].get("from_state") != "PRESERVATION_COMPLETE"
        or events[-2].get("to_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or transition.get("input_receipt_sha256")
        != [events[-2].get("receipt_sha256")]
        or events[-1].get("from_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or events[-1].get("to_state") != "FINALIZED"
        or events[-1].get("receipt_sha256")
        != core.canonical_json_sha256(transition)
    ):
        raise PriorBatchGateError("PRIOR_FINALIZATION_TRANSITION_INVALID")


def validate_prior_batch(
    *, current_batch_id: str, attempt_id: str, governing_commit: str,
    contract_path: Path, plan_path: Path, environment_receipt: Path,
    final_receipt_path: Path, final_ledger_path: Path, transition_path: Path,
    requirements: core.PlanRequirements | None = None,
    expected_runtime_authority: Mapping[str, Any] | None = None,
    _synthetic_test_capability: object | None = None,
) -> None:
    previous = prior_batch_id(current_batch_id)
    attempt_root = plan_path.parent
    prior_batch_root = attempt_root / "batches" / previous
    if (
        plan_path.name != "full_batch_plan.restricted.json"
        or final_receipt_path
        != prior_batch_root
        / "preservation"
        / "batch_finalization_receipt.restricted.json"
        or final_ledger_path != prior_batch_root / "final_resume_ledger.restricted.json"
        or transition_path
        != prior_batch_root
        / "preservation"
        / "cache_retirement_finalized.restricted.json"
    ):
        raise PriorBatchGateError("PRIOR_TERMINAL_AUTHORITY_PATH_MISMATCH")
    contract = core.load_orchestration_contract(contract_path)
    plan = retirement.load_json_and_sha256(
        plan_path, "PRIOR_BATCH_PLAN", max_bytes=512 * 1024 * 1024
    )[0]
    effective_requirements = requirements or core.production_requirements(contract)
    plan_sha = core.validate_current_batch_plan_v3(
        plan, requirements=effective_requirements
    )
    planned = {row["batch_id"]: row for row in plan["batches"]}
    if previous not in planned or current_batch_id not in planned:
        raise PriorBatchGateError("BATCH_SEQUENCE_NOT_PLANNED")
    receipt, final_receipt_sha = retirement.load_json_and_sha256(
        final_receipt_path, "PRIOR_FINAL_RECEIPT"
    )
    try:
        finalizer._validate_current_receipt_v3(receipt)
    except finalizer.ProductionFinalizationError as exc:
        raise PriorBatchGateError("PRIOR_FINAL_RECEIPT_INVALID") from exc
    if (
        receipt.get("batch_id") != previous
        or receipt.get("attempt_id") != attempt_id
        or receipt.get("governing_commit") != governing_commit
        or receipt.get("batch_plan_sha256") != plan_sha
        or receipt.get("prespecified_no_cine_study_set_sha256")
        != planned[previous]["prespecified_no_cine_study_set_sha256"]
        or receipt.get("n_no_cine_studies")
        != planned[previous]["expected_no_cine_studies"]
        or receipt.get("all_no_cine_studies_prespecified") is not True
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
    ):
        raise PriorBatchGateError("PRIOR_FINAL_RECEIPT_AUTHORITY_MISMATCH")
    ledger = retirement.load_json_and_sha256(
        final_ledger_path,
        "PRIOR_FINAL_LEDGER",
        max_bytes=128 * 1024 * 1024,
    )[0]
    try:
        expected_authority = retirement.derive_current_runtime_authority(
            plan=plan,
            effective_requirements=effective_requirements,
            contract=contract,
            contract_path=contract_path,
            governing_commit=governing_commit,
            environment_receipt=environment_receipt,
            synthetic_test_capability=_synthetic_test_capability,
        )
        if expected_runtime_authority is not None and (
            core.validate_runtime_authority(expected_runtime_authority)
            != expected_authority
        ):
            raise PriorBatchGateError("SUPPLIED_RUNTIME_AUTHORITY_MISMATCH")
        core.validate_resume_authority(
            ledger,
            expected_authority=expected_authority,
            attempt_id=attempt_id,
            expected_object_keys={
                previous: {
                    str(row["source_object_key"])
                    for row in planned[previous]["objects"]
                }
            },
        )
    except PriorBatchGateError:
        raise
    except core.OrchestrationError as exc:
        raise PriorBatchGateError("PRIOR_RUNTIME_OR_LEDGER_AUTHORITY_INVALID") from exc
    batch = ledger["batches"][previous]
    if ledger.get("status") != "COMPLETE" or batch.get("state") != "FINALIZED":
        raise PriorBatchGateError("PRIOR_BATCH_NOT_FINALIZED")
    transition = retirement.load_json_and_sha256(
        transition_path, "PRIOR_FINALIZATION_TRANSITION"
    )[0]
    try:
        validate_terminal_transition(
            transition,
            ledger=ledger,
            expected_authority=expected_authority,
            attempt_id=attempt_id,
            batch_id=previous,
            final_receipt_sha256=final_receipt_sha,
        )
    except core.OrchestrationError as exc:
        raise PriorBatchGateError("PRIOR_FINALIZATION_TRANSITION_INVALID") from exc


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
