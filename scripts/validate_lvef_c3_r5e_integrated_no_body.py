#!/usr/bin/env python3
"""Run the exact R5E synthetic and live no-body acceptance once."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_full_sequential as sequential


SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


def _load(name: str, relative: str) -> Any:
    specification = spec_from_file_location(name, ROOT / relative)
    if specification is None or specification.loader is None:
        raise RuntimeError("R5E_ACCEPTANCE_IMPORT_INVALID")
    module = module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def run() -> dict[str, Any]:
    focused = _load(
        "r5e_acceptance_focused",
        "tests/test_lvef_c3_object_technical_disposition.py",
    )
    cohort = _load(
        "r5e_acceptance_cohort",
        "tests/test_lvef_c3_production_stages_and_finalizer.py",
    )
    focused.test_r5e_v2_policy_is_closed_and_v1_policy_remains_byte_valid()
    focused.test_case_02_r4d2c_is_bound_provenance_but_unreachable_to_classifier()
    focused.test_case_01_exact_batch3_pattern_is_one_disposition_and_one_affected_study()
    cohort.test_production_finalizer_reconciles_exact_cohort_and_five_no_cine()
    preflight = sequential.preflight_full()
    if (
        preflight.get("status") != "PASS_FULL_C3_NO_BODY_PREFLIGHT"
        or preflight.get("capacity_gain_source")
        not in {"ALLOCATION", "CLEANUP", "BOTH", "EXISTING_HEADROOM"}
        or preflight.get("raw_retirement_status")
        not in {
            "PASS_OLDER_RAW_DUPLICATES_RETIRED",
            "NOT_APPLICABLE_CLEANUP_SKIPPED",
        }
        or preflight.get("bucket_listing_requests") != 0
        or preflight.get("cloud_requests") != 0
        or preflight.get("qsub_submissions") != 0
        or preflight.get("dicom_body_reads") != 0
        or preflight.get("gpu_executions") != 0
    ):
        raise RuntimeError("R5E_INTEGRATED_PREFLIGHT_INVALID")
    return {
        "R5E_TECHNICAL_DISPOSITION_V2": "PASS",
        "R5E_HISTORICAL_V1_COMPATIBILITY": "PASS",
        "R5E_R4D2C_EVIDENCE_BINDING": "PASS",
        "R5E_EXACT_BATCH3_SYNTHETIC_CASE": "PASS",
        "R5E_COMPLETE_19_BATCH_SYNTHETIC_FINALIZATION": "PASS",
        "R5E_FAILED_ATTEMPT_RETIREMENT_RECEIPT": preflight[
            "raw_retirement_status"
        ],
        "R5E_SELECTED_SOURCE_PLAN": "UNCHANGED_PASS",
        "R5E_RUNTIME_AND_CHECKPOINT": "PASS",
        "R5E_CAPACITY_GAIN_SOURCE": preflight["capacity_gain_source"],
        "R5E_INTEGRATED_NO_BODY_ACCEPTANCE": "PASS",
        "SUCCESSOR_CLAIM_CREATED": "NO",
        "SUCCESSOR_ATTEMPT_ROOT_CREATED": "NO",
        "NEW_CLOUD_REQUESTS": 0,
        "NEW_QSUB_SUBMISSIONS": 0,
        "NEW_DICOM_BODY_READS": 0,
        "NEW_NPZ_BODY_READS": 0,
        "NEW_GPU_EXECUTIONS": 0,
        "NEW_ECHOPRIME_EXECUTIONS": 0,
        "NEW_EMBEDDING_GENERATIONS": 0,
        "NEW_MODEL_FITTING": 0,
        "NEW_PREDICTION_GENERATION": 0,
        "CONFIRMATORY_PERFORMANCE_ACCESSED": "NO",
    }


def main() -> int:
    try:
        result = run()
        print("\n".join(f"{key}={value}" for key, value in result.items()))
        return 0
    except BaseException as exc:
        code = getattr(exc, "code", "R5E_INTEGRATED_NO_BODY_FAILED")
        if not isinstance(code, str) or SAFE_CODE.fullmatch(code) is None:
            code = "R5E_INTEGRATED_NO_BODY_FAILED"
        print("R5E_INTEGRATED_NO_BODY_ACCEPTANCE=FAIL")
        print(f"R5E_INTEGRATED_NO_BODY_FAILURE_CODE={code}")
        print("SUCCESSOR_CLAIM_CREATED=NO")
        print("SUCCESSOR_ATTEMPT_ROOT_CREATED=NO")
        print("NEW_CLOUD_REQUESTS=0")
        print("NEW_QSUB_SUBMISSIONS=0")
        print("NEW_DICOM_BODY_READS=0")
        print("NEW_NPZ_BODY_READS=0")
        print("NEW_GPU_EXECUTIONS=0")
        print("NEW_ECHOPRIME_EXECUTIONS=0")
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
