from __future__ import annotations

"""Focused synthetic proofs for the Tasks-17--19-only R7D capacity gate."""

import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as frozen_capacity
import lvef_c3_r8u_r7d_capacity as capacity


R7D_RUNTIME_COMMIT = "f" * 40
OLD_EVIDENCE_BYTES = 12_345
OLD_EVIDENCE_FILES = 7
PARTIAL_BYTES = 67_890
PARTIAL_FILES = 11


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


def _snapshot(
    *,
    quota_kib: int = 3_000_000_000,
    usage_kib: int = 500_000_000,
    file_quota: int = 33_554_432,
    files_used: int = 1_000_000,
    physical_available: int = 2_500_000_000_000,
    observation_count: int = 1,
) -> dict[str, Any]:
    return {
        "native": {
            "research": {
                "quota_kib": quota_kib,
                "usage_kib": usage_kib,
                "file_quota": file_quota,
                "files_used": files_used,
            }
        },
        "dfs": {
            "research": {
                "total": 3_500_000_000_000,
                "used": 500_000_000_000,
                "available": physical_available,
            }
        },
        "native_capacity_snapshot_captures": observation_count,
        "native_quota_file_captures": observation_count,
        "capacity_command_captures": 5,
        "pquota_command_captures": 1,
        "findmnt_command_captures": 2,
        "df_command_captures": 2,
        "pquota_display_crosscheck": capacity.DISPLAY_CROSSCHECK_PASS,
    }


def _build(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    with mock.patch.object(
        frozen_capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        return capacity.build_fixed_r8u_r7d_tasks17_19_capacity(
            plan,
            snapshot,
            r7d_runtime_commit=R7D_RUNTIME_COMMIT,
            preserved_old_evidence_bytes=OLD_EVIDENCE_BYTES,
            preserved_old_evidence_files=OLD_EVIDENCE_FILES,
            confirmed_partial_artifact_bytes=PARTIAL_BYTES,
            confirmed_partial_artifact_files=PARTIAL_FILES,
        )


def _validate(plan: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    with mock.patch.object(
        frozen_capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        return capacity.validate_fixed_r8u_r7d_tasks17_19_capacity(
            plan,
            value,
            r7d_runtime_commit=R7D_RUNTIME_COMMIT,
            preserved_old_evidence_bytes=OLD_EVIDENCE_BYTES,
            preserved_old_evidence_files=OLD_EVIDENCE_FILES,
            confirmed_partial_artifact_bytes=PARTIAL_BYTES,
            confirmed_partial_artifact_files=PARTIAL_FILES,
        )


def _expect_code(code: str, action: Any) -> None:
    try:
        action()
    except capacity.PostReallocationCapacityError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_r7d_charges_exactly_tasks17_19_and_one_active_cache() -> None:
    plan = _fixed_plan()
    value = _build(plan, _snapshot())

    assert set(value) == capacity.R8U_R7D_CAPACITY_KEYS
    assert value["artifact_type"] == capacity.R8U_R7D_CAPACITY_ARTIFACT_TYPE
    assert value["status"] == capacity.R8U_R7D_CAPACITY_STATUS_PASS
    assert value["blocking_reason_codes"] == []
    assert value["capacity_observation_scope"] == (
        "TASKS_17_19_ONLY_METADATA_NO_SCIENTIFIC_BODY_ACCESS"
    )
    assert value["continuation_first_task"] == 17
    assert value["continuation_last_task"] == 19
    assert value["continuation_task_count"] == 3
    assert value["remaining_batch_count"] == 3
    assert value["finalized_prefix_batches"] == 16
    assert value["finalized_prefix_studies"] == 4_000
    assert value["remaining_studies"] == 530
    assert value["remaining_objects"] == 39_607
    assert value["remaining_source_bytes"] == 143_890_036_746
    assert value["maximum_simultaneous_active_extraction_caches"] == 1
    assert value["largest_remaining_batch_objects"] == 15_000
    assert value["continuation_raw_source_demand_bytes"] == 143_890_036_746
    assert value["largest_transfer_retry_demand_bytes"] == 60_000_000_000
    assert value["active_extraction_cache_object_demand"] == 15_000
    assert value["active_extraction_cache_demand_bytes"] == (
        15_000 * 4_816_896
    )
    assert value["continuation_clip_embedding_upper_bound_bytes"] == (
        39_607 * 4_096
    )
    assert value["continuation_study_embedding_upper_bound_bytes"] == (
        530 * 4_096
    )
    assert value[
        "final_cohort_aggregation_and_preservation_demand_bytes"
    ] == 10_000_000_000
    assert value["r7d_increment_bytes"] == sum(
        value[field]
        for field in (
            "continuation_raw_source_demand_bytes",
            "largest_transfer_retry_demand_bytes",
            "active_extraction_cache_demand_bytes",
            "continuation_clip_embedding_upper_bound_bytes",
            "continuation_study_embedding_upper_bound_bytes",
            "retained_extracted_audit_demand_bytes",
            "manifest_and_metadata_demand_bytes",
            "log_demand_bytes",
            "final_cohort_aggregation_and_preservation_demand_bytes",
            "safety_demand_bytes",
        )
    )
    assert value["r7d_increment_bytes"] == 353_307_877_898
    assert value["r7d_increment_bytes"] == capacity.R8U_R7D_INCREMENT_BYTES
    assert value["required_file_slots"] == 154_607
    assert value["required_file_slots"] == (
        capacity.R8U_R7D_REQUIRED_FILE_SLOTS
    )
    assert value["required_file_slots"] == (
        value["continuation_raw_object_file_demand"]
        + value["active_extraction_cache_file_demand"]
        + value["fixed_control_file_demand"]
    )

    for field in (
        "batches_1_16_bytes_added_to_increment",
        "batches_1_16_files_added_to_demand",
        "batch16_extraction_bytes_added_to_increment",
        "batch16_embedding_bytes_added_to_increment",
        "batch16_files_added_to_demand",
        "baseline_evidence_bytes_added_to_increment",
        "baseline_evidence_files_added_to_demand",
    ):
        assert value[field] == 0
    assert value["preserved_old_control_evidence_bytes_baseline"] == (
        OLD_EVIDENCE_BYTES
    )
    assert value["preserved_old_control_evidence_files_baseline"] == (
        OLD_EVIDENCE_FILES
    )
    assert value["confirmed_partial_artifact_bytes_baseline"] == PARTIAL_BYTES
    assert value["confirmed_partial_artifact_files_baseline"] == PARTIAL_FILES
    assert value["finalized_prefix_already_in_observed_usage"] is True
    assert value["batch16_already_in_observed_usage"] is True
    assert value["preserved_old_evidence_already_in_observed_usage"] is True
    assert value["confirmed_partials_already_in_observed_usage"] is True
    for field in capacity.R8U_R7D_ZERO_EFFECT_KEYS:
        assert value[field] == 0
    assert _validate(plan, value) == value


def test_r7d_reports_exact_quota_physical_and_file_deficits() -> None:
    plan = _fixed_plan()
    required_bytes = (
        capacity.R8U_R7D_INCREMENT_BYTES
        + capacity.R8U_R7D_REQUIRED_QUOTA_RESERVE_BYTES
    )
    quota_kib = 3_000_000_000
    remaining_kib = (required_bytes - 1) // 1024
    expected_quota_deficit = required_bytes - remaining_kib * 1024
    physical_deficit = 456
    file_deficit = 7
    snapshot = _snapshot(
        quota_kib=quota_kib,
        usage_kib=quota_kib - remaining_kib,
        file_quota=33_554_432,
        files_used=(
            33_554_432
            - capacity.R8U_R7D_REQUIRED_FILE_SLOTS
            + file_deficit
        ),
        physical_available=required_bytes - physical_deficit,
    )
    value = _build(plan, snapshot)

    assert value["status"] == capacity.R8U_R7D_CAPACITY_STATUS_BLOCKED
    assert value["blocking_reason_codes"] == [
        "R8U_R7D_QUOTA_RESERVE_INSUFFICIENT",
        "R8U_R7D_PHYSICAL_RESERVE_INSUFFICIENT",
        "R8U_R7D_FILE_SLOTS_INSUFFICIENT",
    ]
    assert value["quota_reserve_deficit_bytes"] == expected_quota_deficit
    assert value["physical_reserve_deficit_bytes"] == physical_deficit
    assert value["file_slot_deficit"] == file_deficit
    assert value["quota_margin_beyond_reserve_bytes"] == (
        -expected_quota_deficit
    )
    assert value["physical_margin_beyond_reserve_bytes"] == -physical_deficit
    assert value["file_slot_margin_after_demand"] == -file_deficit
    assert value["quota_reserve_gate_passed"] is False
    assert value["physical_reserve_gate_passed"] is False
    assert value["file_slot_gate_passed"] is False
    assert _validate(plan, value) == value


def test_r7d_live_entrypoint_captures_one_snapshot_and_replays() -> None:
    plan = _fixed_plan()
    snapshot = _snapshot()
    with (
        mock.patch.object(
            frozen_capacity,
            "R8U_ORIGINAL_PLAN_SHA256",
            _canonical_sha256(plan),
        ),
        mock.patch.object(
            capacity,
            "_capture_current_capacity_snapshot",
            return_value=snapshot,
        ) as capture,
    ):
        value = (
            capacity.capture_and_validate_fixed_r8u_r7d_tasks17_19_capacity(
                plan,
                r7d_runtime_commit=R7D_RUNTIME_COMMIT,
                preserved_old_evidence_bytes=OLD_EVIDENCE_BYTES,
                preserved_old_evidence_files=OLD_EVIDENCE_FILES,
                confirmed_partial_artifact_bytes=PARTIAL_BYTES,
                confirmed_partial_artifact_files=PARTIAL_FILES,
            )
        )

    capture.assert_called_once_with(
        capacity.DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=None,
    )
    assert value["native_capacity_snapshot_captures"] == 1
    assert value["native_quota_file_captures"] == 1
    assert value["capacity_command_captures"] == 5
    assert value["status"] == capacity.R8U_R7D_CAPACITY_STATUS_PASS


def test_r7d_rejects_multiple_observations_and_tampered_deficit() -> None:
    plan = _fixed_plan()
    _expect_code(
        "R8U_R7D_CAPACITY_OBSERVATION_INVALID",
        lambda: _build(plan, _snapshot(observation_count=2)),
    )

    value = _build(plan, _snapshot())
    value["quota_reserve_deficit_bytes"] = 1
    _expect_code(
        "R8U_R7D_CAPACITY_ARITHMETIC_INVALID",
        lambda: _validate(plan, value),
    )


def test_r7d_rejects_negative_or_noninteger_already_used_baselines() -> None:
    plan = _fixed_plan()
    snapshot = _snapshot()
    plan_sha = _canonical_sha256(plan)
    for bad_value in (-1, True):
        with mock.patch.object(
            frozen_capacity, "R8U_ORIGINAL_PLAN_SHA256", plan_sha
        ):
            _expect_code(
                "R8U_R7D_BASELINE_AUTHORITY_INVALID",
                lambda bad_value=bad_value: (
                    capacity.build_fixed_r8u_r7d_tasks17_19_capacity(
                        plan,
                        snapshot,
                        r7d_runtime_commit=R7D_RUNTIME_COMMIT,
                        preserved_old_evidence_bytes=bad_value,
                    )
                ),
            )
