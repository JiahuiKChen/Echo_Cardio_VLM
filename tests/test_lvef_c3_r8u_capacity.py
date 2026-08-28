from __future__ import annotations

"""Focused dependency-light tests for the fixed Phase 1I-R8U capacity gate."""

import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import traceback
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as capacity


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


def _fixed_r8u_plan() -> dict[str, Any]:
    """Synthetic exact-aggregate plan with a fixed 18,677-object Batch 16."""

    prefix_objects = _distribute(277_700, 15)
    prefix_bytes = _distribute(1_004_679_096_576, 15)
    tail_objects = (18_677, 15_000, 15_000, 9_607)
    tail_bytes = (
        68_000_000_000,
        60_000_000_000,
        55_000_000_000,
        28_890_036_746,
    )
    objects = [*prefix_objects, *tail_objects]
    source_bytes = [*prefix_bytes, *tail_bytes]
    batches = [
        {
            "ordinal": ordinal,
            "batch_id": f"c3_batch_{ordinal:03d}",
            "n_studies": 30 if ordinal == 18 else 250,
            "n_objects": objects[ordinal],
            "source_bytes": source_bytes[ordinal],
        }
        for ordinal in range(19)
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
        "batches": batches,
    }


def _snapshot(
    *,
    quota_kib: int = 3_000_000_000,
    usage_kib: int = 500_000_000,
    file_quota: int = 33_554_432,
    files_used: int = 1_000_000,
    physical_available: int = 2_500_000_000_000,
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
                "total": 3_000_000_000_000,
                "used": 500_000_000_000,
                "available": physical_available,
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


def _probe(
    plan: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[dict[str, Any], mock.Mock]:
    capture = mock.Mock(return_value=snapshot)
    with (
        mock.patch.object(
            capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
        ),
        mock.patch.object(
            capacity,
            "_capture_current_capacity_snapshot",
            capture,
        ),
    ):
        result = capacity.probe_fixed_r8u_batch16_recovery_capacity(plan)
    return result, capture


def _expect_code(code: str, action: Any) -> None:
    try:
        action()
    except capacity.PostReallocationCapacityError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_r8u_exact_arithmetic_reuses_baseline_without_double_counting() -> None:
    plan = _fixed_r8u_plan()
    result, capture = _probe(plan, _snapshot())

    capture.assert_called_once_with(
        capacity.DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=None,
    )
    assert set(result) == capacity.R8U_CAPACITY_KEYS
    assert result["artifact_type"] == (
        "lvef_c3_r8u_batch16_recovery_capacity_v1"
    )
    assert result["status"] == (
        "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE"
    )
    assert result["blocking_reason_codes"] == []
    assert result["recovery_task"] == 16
    assert result["recovery_batch_id"] == "c3_batch_015"
    assert result["continuation_first_task"] == 17
    assert result["continuation_last_task"] == 19
    assert result["continuation_task_count"] == 3
    assert result["fresh_pipeline_batch_count"] == 4
    assert result["fresh_pipeline_studies"] == 780
    assert result["fresh_pipeline_objects"] == 58_284
    assert result["continuation_studies"] == 530
    assert result["continuation_objects"] == 39_607
    assert result["continuation_source_bytes"] == 143_890_036_746

    assert result["batch16_raw_reused"] is True
    assert result["batch16_raw_object_files_baseline"] == 18_677
    assert result["batch16_raw_source_bytes_baseline"] == 68_000_000_000
    assert result["failed_partial_files_baseline"] == 4_757
    assert result["failed_partial_bytes_baseline"] == 8_583_119_701
    for key in (
        "batch16_redownload_demand_bytes",
        "baseline_batch16_raw_bytes_added_to_increment",
        "baseline_failed_partial_bytes_added_to_increment",
        "baseline_batch16_raw_files_added_to_demand",
        "baseline_failed_partial_files_added_to_demand",
    ):
        assert result[key] == 0

    assert result["continuation_raw_source_demand_bytes"] == 143_890_036_746
    assert result["largest_continuation_transfer_retry_demand_bytes"] == (
        60_000_000_000
    )
    assert result["largest_fresh_pipeline_batch_objects"] == 18_677
    assert result["largest_rolling_fresh_extracted_cache_demand_bytes"] == (
        18_677 * 4_816_896
    )
    assert result["fresh_clip_embedding_upper_bound_bytes"] == (
        58_284 * 4_096
    )
    assert result["fresh_study_embedding_upper_bound_bytes"] == 780 * 4_096
    expected_increment = sum(
        (
            143_890_036_746,
            60_000_000_000,
            18_677 * 4_816_896,
            58_284 * 4_096,
            780 * 4_096,
            2_000_000_000,
            5_000_000_000,
            10_000_000_000,
            10_000_000_000,
            50_000_000_000,
        )
    )
    assert expected_increment == 371_097_129_482
    assert result["r8u_increment_bytes"] == expected_increment
    assert result["r8u_increment_bytes"] != (
        expected_increment + 68_000_000_000 + 8_583_119_701
    )
    assert result["required_file_slots"] == 39_607 + 18_677 + 100_000
    assert result["required_file_slots"] == 158_284
    assert result["required_quota_reserve_bytes"] == 200_000_000_000
    assert result["required_physical_reserve_bytes"] == 200_000_000_000
    assert result["quota_reserve_gate_passed"] is True
    assert result["physical_reserve_gate_passed"] is True
    assert result["file_slot_gate_passed"] is True
    assert result["native_capacity_snapshot_captures"] == 1
    assert result["native_quota_file_captures"] == 1
    assert result["capacity_command_captures"] == 5
    assert result["pquota_command_captures"] == 1
    assert result["findmnt_command_captures"] == 2
    assert result["df_command_captures"] == 2
    for key in capacity.R8U_CAPACITY_ZERO_EFFECT_KEYS:
        assert result[key] == 0

    with mock.patch.object(
        capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        assert capacity.validate_fixed_r8u_batch16_recovery_capacity(
            plan, result
        ) == result


def test_r8u_pure_validator_rejects_double_counting_and_tamper() -> None:
    plan = _fixed_r8u_plan()
    result, _ = _probe(plan, _snapshot())
    with mock.patch.object(
        capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        for key, replacement in (
            ("batch16_redownload_demand_bytes", 1),
            ("baseline_batch16_raw_bytes_added_to_increment", 1),
            ("baseline_failed_partial_bytes_added_to_increment", 1),
            ("baseline_batch16_raw_files_added_to_demand", 18_677),
            ("baseline_failed_partial_files_added_to_demand", 4_757),
            ("failed_partial_bytes_baseline", 8_583_119_702),
            ("r8u_increment_bytes", result["r8u_increment_bytes"] + 1),
            ("required_quota_reserve_bytes", 199_999_999_999),
            ("required_physical_reserve_bytes", 200_000_000_001),
            ("required_file_slots", result["required_file_slots"] + 1),
        ):
            altered = dict(result)
            altered[key] = replacement
            _expect_code(
                "R8U_CAPACITY_ARITHMETIC_INVALID",
                lambda altered=altered: (
                    capacity.validate_fixed_r8u_batch16_recovery_capacity(
                        plan, altered
                    )
                ),
            )

        wrong_type = dict(result)
        wrong_type["recovery_task"] = 16.0
        _expect_code(
            "R8U_CAPACITY_SCHEMA_INVALID",
            lambda: capacity.validate_fixed_r8u_batch16_recovery_capacity(
                plan, wrong_type
            ),
        )

    signature = inspect.signature(
        capacity.probe_fixed_r8u_batch16_recovery_capacity
    )
    assert tuple(signature.parameters) == ("plan", "process_runner")
    assert signature.parameters["process_runner"].kind == (
        inspect.Parameter.KEYWORD_ONLY
    )
    poison = mock.Mock(
        side_effect=AssertionError("invalid plan reached live capture")
    )
    changed = copy.deepcopy(plan)
    changed["batches"][15]["source_bytes"] += 1
    with mock.patch.object(
        capacity, "_capture_current_capacity_snapshot", poison
    ):
        _expect_code(
            "R8U_FIXED_PLAN_SHA256_MISMATCH",
            lambda: capacity.probe_fixed_r8u_batch16_recovery_capacity(
                changed
            ),
        )
    poison.assert_not_called()


def test_r8u_blocks_each_exact_quota_physical_and_file_margin() -> None:
    plan = _fixed_r8u_plan()
    increment = 371_097_129_482
    reserve = 200_000_000_000
    quota_kib = 3_000_000_000
    quota_bytes = quota_kib * 1_024
    usage_boundary = quota_bytes - increment - reserve
    quota_blocked, quota_capture = _probe(
        plan,
        _snapshot(
            quota_kib=quota_kib,
            usage_kib=usage_boundary // 1_024 + 1,
        ),
    )
    quota_capture.assert_called_once()
    assert quota_blocked["status"] == "BLOCKED"
    assert quota_blocked["blocking_reason_codes"] == [
        "R8U_QUOTA_RESERVE_INSUFFICIENT"
    ]
    assert quota_blocked["quota_margin_beyond_reserve_bytes"] < 0

    physical_blocked, physical_capture = _probe(
        plan,
        _snapshot(physical_available=increment + reserve - 1),
    )
    physical_capture.assert_called_once()
    assert physical_blocked["status"] == "BLOCKED"
    assert physical_blocked["blocking_reason_codes"] == [
        "R8U_PHYSICAL_RESERVE_INSUFFICIENT"
    ]
    assert physical_blocked["physical_margin_beyond_reserve_bytes"] == -1

    file_quota = capacity.EXPECTED_RESEARCH_FILE_QUOTA
    required_files = 158_284
    file_blocked, file_capture = _probe(
        plan,
        _snapshot(
            file_quota=file_quota,
            files_used=file_quota - required_files + 1,
        ),
    )
    file_capture.assert_called_once()
    assert file_blocked["status"] == "BLOCKED"
    assert file_blocked["blocking_reason_codes"] == [
        "R8U_FILE_SLOTS_INSUFFICIENT"
    ]
    assert file_blocked["file_slot_margin_after_demand"] == -1


def test_every_r8u_capacity_test_is_zero_argument() -> None:
    tests = {
        name: value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    }
    assert tests
    assert all(not inspect.signature(value).parameters for value in tests.values())


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    skipped = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        if inspect.signature(function).parameters:
            print(f"SKIP {name}: requires a fixture")
            skipped += 1
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
    print(f"SUMMARY passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
