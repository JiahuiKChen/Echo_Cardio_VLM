from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))
import lvef_c3_canary_scheduler_plan as scheduler


MANIFEST_SHA = "a" * 64
RECEIPT_SHA = "b" * 64


def _assert_error(code: str, operation) -> None:
    try:
        operation()
    except scheduler.CanarySchedulerPlanError as exc:
        assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError(f"expected CanarySchedulerPlanError {code}")


def _template() -> dict:
    return scheduler.load_scheduler_plan(
        ROOT / "configs/lvef_c3_canary_scheduler_plan_v1.json"
    )


def _bound() -> dict:
    return scheduler.bind_scheduler_plan(
        _template(), MANIFEST_SHA, repository_root=ROOT
    )


def test_frozen_scheduler_plan_is_closed_exact_five_and_hash_bound() -> None:
    template = _template()
    scheduler.validate_scheduler_plan(template, repository_root=ROOT)
    bound = scheduler.bind_scheduler_plan(
        template, MANIFEST_SHA, repository_root=ROOT
    )
    assert bound["ordered_stage_ids"] == list(scheduler.ORDERED_STAGE_IDS)
    assert bound["scheduler_submission_count"] == 5
    assert bound["maximum_scheduler_submission_count"] == 5
    assert bound["gpu_stage_count"] == 1
    assert bound["stage_retry_count"] == 0
    assert bound["automatic_resubmission_permitted"] is False
    assert bound["array_expansion_permitted"] is False
    assert bound["production_continuation"] is False
    assert bound["canary_manifest_sha256"] == MANIFEST_SHA
    assert [stage["array_task_count"] for stage in bound["stages"]] == [1] * 5
    assert [stage["retry_count"] for stage in bound["stages"]] == [0] * 5


def test_plan_rejects_unknown_and_duplicate_json_keys() -> None:
    value = _template()
    value["unknown"] = False
    _assert_error(
        "CANARY_SCHEDULER_PLAN_SCHEMA_NOT_CLOSED",
        lambda: scheduler.validate_scheduler_plan(value),
    )
    raw = (ROOT / "configs/lvef_c3_canary_scheduler_plan_v1.json").read_text()
    duplicate = raw.replace(
        '"schema_version": 1,', '"schema_version": 1,\n  "schema_version": 1,', 1
    )
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "duplicate.json"
        path.write_text(duplicate, encoding="utf-8")
        _assert_error(
            "CANARY_SCHEDULER_DUPLICATE_JSON_KEY",
            lambda: scheduler.load_scheduler_plan(path),
        )


def test_entrypoint_hash_and_topology_mutations_fail_closed() -> None:
    value = _template()
    value["stages"][0]["entrypoint"]["sha256"] = "0" * 64
    _assert_error(
        "CANARY_STAGE_ENTRYPOINT_HASH_MISMATCH",
        lambda: scheduler.validate_scheduler_plan(value, repository_root=ROOT),
    )
    value = _template()
    value["stages"].append(copy.deepcopy(value["stages"][-1]))
    value["scheduler_submission_count"] = 6
    try:
        scheduler.validate_scheduler_plan(value)
    except scheduler.CanarySchedulerPlanError:
        pass
    else:
        raise AssertionError("expected scheduler topology failure")


def test_exact_sequence_completes_once_without_continuation() -> None:
    plan = _bound()
    ledger = scheduler.initialize_claim_ledger(plan)
    predecessor: list[str] = []
    for stage_id in scheduler.ORDERED_STAGE_IDS:
        ledger = scheduler.claim_stage_submission(
            ledger,
            plan,
            stage_id,
            predecessor_receipt_sha256s=predecessor,
        )
        assert ledger["active_stage_id"] == stage_id
        ledger = scheduler.record_stage_result(
            ledger,
            plan,
            stage_id,
            passed=True,
            result_receipt_sha256=RECEIPT_SHA,
        )
        predecessor = [RECEIPT_SHA]
    assert ledger["status"] == "COMPLETE"
    assert ledger["submission_count"] == 5
    assert ledger["production_continuation_triggered"] is False
    _assert_error(
        "CANARY_SCHEDULER_SUBMISSION_LIMIT_EXCEEDED",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "CANARY_FINALIZATION",
            predecessor_receipt_sha256s=[RECEIPT_SHA],
        ),
    )


def test_duplicate_dynamic_and_out_of_order_claims_fail() -> None:
    plan = _bound()
    ledger = scheduler.initialize_claim_ledger(plan)
    ledger = scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD")
    _assert_error(
        "CANARY_SCHEDULER_STAGE_ALREADY_ACTIVE",
        lambda: scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD"),
    )
    _assert_error(
        "CANARY_SCHEDULER_UNDECLARED_STAGE",
        lambda: scheduler.claim_stage_submission(ledger, plan, "MODEL_FITTING"),
    )
    ledger = scheduler.record_stage_result(
        ledger, plan, "DOWNLOAD", passed=True, result_receipt_sha256=RECEIPT_SHA
    )
    _assert_error(
        "CANARY_SCHEDULER_DUPLICATE_STAGE_SUBMISSION",
        lambda: scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD"),
    )
    _assert_error(
        "CANARY_SCHEDULER_DYNAMIC_OR_OUT_OF_ORDER_STAGE",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "ECHOPRIME_EMBEDDING",
            predecessor_receipt_sha256s=[RECEIPT_SHA],
        ),
    )
    _assert_error(
        "CANARY_SCHEDULER_PREDECESSOR_RECEIPT_MISMATCH",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "DICOM_EXTRACTION",
            predecessor_receipt_sha256s=["c" * 64],
        ),
    )


def test_stage_failure_blocks_successors_and_resubmission() -> None:
    plan = _bound()
    ledger = scheduler.initialize_claim_ledger(plan)
    ledger = scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD")
    ledger = scheduler.record_stage_result(
        ledger, plan, "DOWNLOAD", passed=False, result_receipt_sha256=RECEIPT_SHA
    )
    assert ledger["status"] == "FAILED"
    _assert_error(
        "CANARY_SCHEDULER_SUCCESSOR_AFTER_FAILURE",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "DICOM_EXTRACTION",
            predecessor_receipt_sha256s=[RECEIPT_SHA],
        ),
    )
    _assert_error(
        "CANARY_SCHEDULER_SUCCESSOR_AFTER_FAILURE",
        lambda: scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD"),
    )


def test_claim_ledger_rejects_injected_claim_and_production_continuation() -> None:
    plan = _bound()
    ledger = scheduler.initialize_claim_ledger(plan)
    injected = copy.deepcopy(ledger)
    injected["claims"].append(
        {
            "stage_id": "MODEL_FITTING",
            "ordinal": 1,
            "status": "SUBMITTED",
            "predecessor_receipt_sha256s": [],
            "result_receipt_sha256": None,
        }
    )
    injected["submission_count"] = 1
    injected["active_stage_id"] = "MODEL_FITTING"
    injected["status"] = "STAGE_ACTIVE"
    _assert_error(
        "CANARY_SCHEDULER_CLAIM_ORDER_INVALID",
        lambda: scheduler.validate_claim_ledger(injected, plan),
    )
    continued = copy.deepcopy(ledger)
    continued["production_continuation_triggered"] = True
    _assert_error(
        "CANARY_SCHEDULER_CLAIM_AUTHORITY_MISMATCH",
        lambda: scheduler.validate_claim_ledger(continued, plan),
    )


def test_module_has_no_scheduler_or_scientific_execution_imports() -> None:
    source = (SCRIPTS / "lvef_c3_canary_scheduler_plan.py").read_text(
        encoding="utf-8"
    )
    assert "import subprocess" not in source
    assert "import torch" not in source
    assert "import pydicom" not in source
    assert "google.cloud" not in source
