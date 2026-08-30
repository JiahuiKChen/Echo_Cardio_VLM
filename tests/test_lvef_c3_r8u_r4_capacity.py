from __future__ import annotations

"""Focused proofs for the closed R8U-R4 capacity wrapper."""

import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as capacity


CURRENT_R4_COMMIT = "d" * 40
CANDIDATE_BYTES = 48_765_432_100


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _distribute(total: int, count: int) -> list[int]:
    base, remainder = divmod(total, count)
    return [base + int(index < remainder) for index in range(count)]


def _fixed_plan() -> dict[str, Any]:
    objects = [
        *_distribute(277_700, 15),
        18_677,
        15_000,
        15_000,
        9_607,
    ]
    source_bytes = [
        *_distribute(1_004_679_096_576, 15),
        68_000_000_000,
        60_000_000_000,
        55_000_000_000,
        28_890_036_746,
    ]
    return {
        "schema_version": 3,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v3",
        "authority": {
            "git_commit": capacity.R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        },
        "cohort": {
            "selected_studies": 4_530,
            "normalized_source_objects": 335_984,
            "selected_source_bytes": 1_216_569_133_322,
        },
        "batches": [
            {
                "ordinal": ordinal,
                "batch_id": f"c3_batch_{ordinal:03d}",
                "n_studies": 30 if ordinal == 18 else 250,
                "n_objects": objects[ordinal],
                "source_bytes": source_bytes[ordinal],
            }
            for ordinal in range(19)
        ],
    }


def _snapshot() -> dict[str, Any]:
    return {
        "native": {
            "research": {
                "quota_kib": 3_000_000_000,
                "usage_kib": 500_000_000,
                "file_quota": 33_554_432,
                "files_used": 1_000_000,
            }
        },
        "dfs": {
            "research": {
                "total": 3_000_000_000_000,
                "used": 500_000_000_000,
                "available": 2_500_000_000_000,
            }
        },
        "native_capacity_snapshot_captures": 1,
        "native_quota_file_captures": 1,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "pquota_display_crosscheck": capacity.DISPLAY_CROSSCHECK_PASS,
    }


def _capture() -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _fixed_plan()
    with (
        mock.patch.object(
            capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
        ),
        mock.patch.object(
            capacity,
            "_capture_current_capacity_snapshot",
            return_value=_snapshot(),
        ) as capture,
    ):
        value = capacity.capture_r8u_r4_remaining_capacity(
            plan,
            completed_extraction_candidate_seal_sha256=(
                capacity.R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=CANDIDATE_BYTES,
            r8u_portability_repair_commit=CURRENT_R4_COMMIT,
        )
    capture.assert_called_once_with(
        capacity.DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=None,
    )
    return plan, value


def test_r4_wrapper_closes_r3_authority_and_preserves_reserves() -> None:
    plan, value = _capture()

    assert set(value) == capacity.R8U_R4_CAPACITY_KEYS
    assert value["artifact_type"] == capacity.R8U_R4_CAPACITY_ARTIFACT_TYPE
    assert value["status"] == capacity.R8U_R4_CAPACITY_STATUS_PASS
    assert value["completed_extraction_candidate_seal_sha256"] == (
        capacity.R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
    )
    assert value["completed_extraction_candidate_bytes_baseline"] == (
        CANDIDATE_BYTES
    )
    assert value["implementation_authority_epochs"] == {
        "scientific_commit": capacity.R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": capacity.R8U_R8R_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": (
            capacity.R8U_BASE_IMPLEMENTATION_COMMIT
        ),
        "r8u_projection_repair_commit": (
            capacity.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            capacity.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_publication_resume_repair_commit": (
            capacity.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_candidate_authority_repair_commit": (
            capacity.R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_portability_repair_commit": CURRENT_R4_COMMIT,
    }
    assert value["required_quota_reserve_bytes"] == 200_000_000_000
    assert value["required_physical_reserve_bytes"] == 200_000_000_000
    assert value["quota_reserve_gate_passed"] is True
    assert value["physical_reserve_gate_passed"] is True
    assert value["file_slot_gate_passed"] is True
    assert value["remaining_scope_batch_count"] == 4
    assert value["continuation_task_count"] == 3
    assert value["continuation_raw_object_file_demand"] == 39_607
    assert value["continuation_raw_source_demand_bytes"] == 143_890_036_746
    for field in capacity.R8U_R3_CAPACITY_ZERO_EFFECT_KEYS:
        assert value[field] == 0

    with mock.patch.object(
        capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        assert (
            capacity.validate_fixed_r8u_r4_batch16_publication_resume_capacity(
                plan,
                value,
                completed_extraction_candidate_seal_sha256=(
                    capacity.R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256
                ),
                completed_extraction_candidate_bytes=CANDIDATE_BYTES,
                r8u_portability_repair_commit=CURRENT_R4_COMMIT,
            )
            == value
        )


def test_r4_wrapper_rejects_mutable_r3_or_current_epoch_authority() -> None:
    plan = _fixed_plan()
    with mock.patch.object(
        capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        for seal, commit, code in (
            (
                "a" * 64,
                CURRENT_R4_COMMIT,
                "R8U_R4_COMPLETED_EXTRACTION_CANDIDATE_AUTHORITY_INVALID",
            ),
            (
                capacity.R8U_R3_IMMUTABLE_CANDIDATE_SEAL_SHA256,
                capacity.R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT,
                "R8U_R4_IMPLEMENTATION_AUTHORITY_INVALID",
            ),
        ):
            try:
                capacity.capture_r8u_r4_remaining_capacity(
                    plan,
                    completed_extraction_candidate_seal_sha256=seal,
                    completed_extraction_candidate_bytes=CANDIDATE_BYTES,
                    r8u_portability_repair_commit=commit,
                )
            except capacity.PostReallocationCapacityError as exc:
                assert exc.code == code
            else:
                raise AssertionError(f"expected {code}")
