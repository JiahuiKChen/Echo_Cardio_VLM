from __future__ import annotations

"""Focused dependency-light proofs for the fixed R8U-R3 capacity gate."""

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


R8U_R3_IMPLEMENTATION_COMMIT = "a" * 40
CANDIDATE_SEAL_SHA256 = "b" * 64
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


def _fixed_r8u_plan() -> dict[str, Any]:
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
        result = (
            capacity.probe_fixed_r8u_r3_batch16_publication_resume_capacity(
                plan,
                completed_extraction_candidate_seal_sha256=(
                    CANDIDATE_SEAL_SHA256
                ),
                completed_extraction_candidate_bytes=CANDIDATE_BYTES,
                r8u_publication_resume_repair_commit=(
                    R8U_R3_IMPLEMENTATION_COMMIT
                ),
            )
        )
    return result, capture


def _validate(plan: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    with mock.patch.object(
        capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
    ):
        return capacity.validate_fixed_r8u_r3_batch16_publication_resume_capacity(
            plan,
            value,
            completed_extraction_candidate_seal_sha256=(
                CANDIDATE_SEAL_SHA256
            ),
            completed_extraction_candidate_bytes=CANDIDATE_BYTES,
            r8u_publication_resume_repair_commit=(
                R8U_R3_IMPLEMENTATION_COMMIT
            ),
        )


def _expect_code(code: str, action: Any) -> None:
    try:
        action()
    except capacity.PostReallocationCapacityError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_r8u_r3_exact_remaining_demand_excludes_completed_inputs() -> None:
    plan = _fixed_r8u_plan()
    result, capture = _probe(plan, _snapshot())

    capture.assert_called_once_with(
        capacity.DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY,
        process_runner=None,
    )
    assert set(result) == capacity.R8U_R3_CAPACITY_KEYS
    assert result["artifact_type"] == (
        "lvef_c3_r8u_r3_batch16_publication_resume_capacity_v1"
    )
    assert result["status"] == (
        "PASS_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE"
    )
    assert result["blocking_reason_codes"] == []
    assert result["implementation_authority_epochs"] == {
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
            R8U_R3_IMPLEMENTATION_COMMIT
        ),
    }
    assert result["resume_task"] == 16
    assert result["resume_batch_id"] == "c3_batch_015"
    assert result["continuation_first_task"] == 17
    assert result["continuation_last_task"] == 19
    assert result["continuation_task_count"] == 3
    assert result["remaining_scope_batch_count"] == 4
    assert result["remaining_scope_studies"] == 780
    assert result["remaining_scope_source_objects"] == 58_284
    assert result["remaining_scope_source_bytes"] == 211_890_036_746
    assert result["continuation_studies"] == 530
    assert result["continuation_objects"] == 39_607
    assert result["continuation_source_bytes"] == 143_890_036_746
    assert result["largest_continuation_batch_objects"] == 15_000
    assert result["largest_continuation_batch_source_bytes"] == 60_000_000_000

    assert result["batch16_raw_reused"] is True
    assert result["batch16_raw_object_files_baseline"] == 18_677
    assert result["batch16_raw_source_bytes_baseline"] == 68_000_000_000
    assert result["failed_partial_files_baseline"] == 4_757
    assert result["failed_partial_bytes_baseline"] == 8_583_119_701
    assert result["completed_extraction_candidate_reused"] is True
    assert result["completed_extraction_candidate_files_baseline"] == 10_187
    assert result["completed_extraction_candidate_bytes_baseline"] == (
        CANDIDATE_BYTES
    )
    assert result["completed_extraction_candidate_seal_sha256"] == (
        CANDIDATE_SEAL_SHA256
    )
    for key in (
        "batch16_redownload_demand_bytes",
        "batch16_dicom_extraction_demand_bytes",
        "batch16_dicom_extraction_file_demand",
        "baseline_batch16_raw_bytes_added_to_increment",
        "baseline_batch16_raw_files_added_to_demand",
        "baseline_failed_partial_bytes_added_to_increment",
        "baseline_failed_partial_files_added_to_demand",
        "baseline_completed_candidate_bytes_added_to_increment",
        "baseline_completed_candidate_files_added_to_demand",
    ):
        assert result[key] == 0

    assert result[
        "largest_rolling_continuation_extracted_cache_demand_bytes"
    ] == 15_000 * 4_816_896
    assert result["batch16_clip_embedding_file_upper_bound"] == 10_187
    assert result["continuation_clip_embedding_file_upper_bound"] == 39_607
    assert result["remaining_clip_embedding_file_upper_bound"] == 49_794
    assert result["remaining_clip_embedding_upper_bound_bytes"] == (
        49_794 * 4_096
    )
    assert result["remaining_study_embedding_upper_bound_bytes"] == (
        780 * 4_096
    )
    expected_increment = sum(
        (
            143_890_036_746,
            60_000_000_000,
            15_000 * 4_816_896,
            49_794 * 4_096,
            780 * 4_096,
            2_000_000_000,
            5_000_000_000,
            10_000_000_000,
            10_000_000_000,
            50_000_000_000,
        )
    )
    assert expected_increment == 353_350_627_850
    assert result["r8u_r3_increment_bytes"] == expected_increment
    assert result["r8u_r3_increment_bytes"] != (
        expected_increment + CANDIDATE_BYTES + 68_000_000_000
    )
    assert result["required_file_slots"] == 39_607 + 15_000 + 100_000
    assert result["required_file_slots"] == 154_607
    assert result["required_quota_reserve_bytes"] == 200_000_000_000
    assert result["required_physical_reserve_bytes"] == 200_000_000_000
    for key in capacity.R8U_R3_CAPACITY_ZERO_EFFECT_KEYS:
        assert result[key] == 0
    assert _validate(plan, result) == result


def test_r8u_r3_validator_rejects_double_charging_and_authority_drift() -> None:
    plan = _fixed_r8u_plan()
    result, _ = _probe(plan, _snapshot())
    for key, replacement in (
        ("batch16_redownload_demand_bytes", 1),
        ("batch16_dicom_extraction_demand_bytes", 1),
        ("batch16_dicom_extraction_file_demand", 1),
        ("baseline_batch16_raw_bytes_added_to_increment", 1),
        ("baseline_failed_partial_bytes_added_to_increment", 1),
        ("baseline_completed_candidate_bytes_added_to_increment", 1),
        ("baseline_completed_candidate_files_added_to_demand", 10_187),
        (
            "largest_rolling_continuation_extracted_cache_demand_bytes",
            18_677 * 4_816_896,
        ),
        ("r8u_r3_increment_bytes", result["r8u_r3_increment_bytes"] + 1),
        ("required_file_slots", result["required_file_slots"] + 1),
        ("completed_extraction_candidate_bytes_baseline", CANDIDATE_BYTES + 1),
        ("completed_extraction_candidate_seal_sha256", "c" * 64),
    ):
        altered = copy.deepcopy(result)
        altered[key] = replacement
        _expect_code(
            "R8U_R3_CAPACITY_ARITHMETIC_INVALID",
            lambda altered=altered: _validate(plan, altered),
        )

    wrong_type = copy.deepcopy(result)
    wrong_type["resume_task"] = 16.0
    _expect_code(
        "R8U_R3_CAPACITY_SCHEMA_INVALID",
        lambda: _validate(plan, wrong_type),
    )
    extra = copy.deepcopy(result)
    extra["unexpected"] = 0
    _expect_code(
        "R8U_R3_CAPACITY_SCHEMA_INVALID",
        lambda: _validate(plan, extra),
    )

    for epoch in capacity.R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS:
        altered = copy.deepcopy(result)
        altered["implementation_authority_epochs"][epoch] = "c" * 40
        _expect_code(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID",
            lambda altered=altered: _validate(plan, altered),
        )


def test_r8u_r3_fixed_api_binds_six_epochs_before_live_capture() -> None:
    expected = {
        "scientific_commit": capacity.R8U_ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": capacity.R8U_R8R_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": (
            capacity.R8U_BASE_IMPLEMENTATION_COMMIT
        ),
        "r8u_projection_repair_commit": (
            capacity.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": (
            "4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff"
        ),
        "r8u_publication_resume_repair_commit": (
            R8U_R3_IMPLEMENTATION_COMMIT
        ),
    }
    assert capacity._fixed_r8u_r3_implementation_authority_epochs(
        R8U_R3_IMPLEMENTATION_COMMIT
    ) == expected
    assert set(expected) == capacity.R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    for reused in expected.values():
        if reused == R8U_R3_IMPLEMENTATION_COMMIT:
            continue
        _expect_code(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID",
            lambda reused=reused: (
                capacity._fixed_r8u_r3_implementation_authority_epochs(reused)
            ),
        )
    for malformed in ("", "A" * 40, "a" * 39, True):
        _expect_code(
            "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID",
            lambda malformed=malformed: (
                capacity._fixed_r8u_r3_implementation_authority_epochs(
                    malformed  # type: ignore[arg-type]
                )
            ),
        )

    probe_signature = inspect.signature(
        capacity.probe_fixed_r8u_r3_batch16_publication_resume_capacity
    )
    assert tuple(probe_signature.parameters) == (
        "plan",
        "completed_extraction_candidate_seal_sha256",
        "completed_extraction_candidate_bytes",
        "r8u_publication_resume_repair_commit",
        "process_runner",
    )
    validate_signature = inspect.signature(
        capacity.validate_fixed_r8u_r3_batch16_publication_resume_capacity
    )
    assert tuple(validate_signature.parameters) == (
        "plan",
        "value",
        "completed_extraction_candidate_seal_sha256",
        "completed_extraction_candidate_bytes",
        "r8u_publication_resume_repair_commit",
    )
    for signature, names in (
        (
            probe_signature,
            tuple(probe_signature.parameters)[1:],
        ),
        (
            validate_signature,
            tuple(validate_signature.parameters)[2:],
        ),
    ):
        for name in names:
            assert signature.parameters[name].kind == (
                inspect.Parameter.KEYWORD_ONLY
            )
        assert signature.parameters[
            "r8u_publication_resume_repair_commit"
        ].default is inspect.Parameter.empty

    plan = _fixed_r8u_plan()
    poison = mock.Mock(
        side_effect=AssertionError("invalid authority reached live capture")
    )
    with (
        mock.patch.object(
            capacity, "R8U_ORIGINAL_PLAN_SHA256", _canonical_sha256(plan)
        ),
        mock.patch.object(
            capacity, "_capture_current_capacity_snapshot", poison
        ),
    ):
        for seal, candidate_bytes, commit in (
            ("not-a-sha", CANDIDATE_BYTES, R8U_R3_IMPLEMENTATION_COMMIT),
            (CANDIDATE_SEAL_SHA256, 0, R8U_R3_IMPLEMENTATION_COMMIT),
            (
                CANDIDATE_SEAL_SHA256,
                CANDIDATE_BYTES,
                capacity.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            ),
        ):
            expected_code = (
                "R8U_R3_IMPLEMENTATION_AUTHORITY_INVALID"
                if commit
                == capacity.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                else "R8U_R3_COMPLETED_EXTRACTION_CANDIDATE_AUTHORITY_INVALID"
            )
            _expect_code(
                expected_code,
                lambda seal=seal, candidate_bytes=candidate_bytes, commit=commit: (
                    capacity.probe_fixed_r8u_r3_batch16_publication_resume_capacity(
                        plan,
                        completed_extraction_candidate_seal_sha256=seal,
                        completed_extraction_candidate_bytes=candidate_bytes,
                        r8u_publication_resume_repair_commit=commit,
                    )
                ),
            )
    poison.assert_not_called()

    assert callable(capacity.probe_fixed_r8u_batch16_recovery_capacity)
    assert callable(capacity.validate_fixed_r8u_batch16_recovery_capacity)
    assert capacity.R8U_CAPACITY_ARTIFACT_TYPE == (
        "lvef_c3_r8u_r2_batch16_recovery_capacity_v1"
    )


def test_r8u_r3_blocks_each_quota_physical_and_file_margin() -> None:
    plan = _fixed_r8u_plan()
    passing, _ = _probe(plan, _snapshot())
    increment = int(passing["r8u_r3_increment_bytes"])
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
        "R8U_R3_QUOTA_RESERVE_INSUFFICIENT"
    ]
    assert quota_blocked["quota_margin_beyond_reserve_bytes"] < 0

    physical_blocked, physical_capture = _probe(
        plan,
        _snapshot(physical_available=increment + reserve - 1),
    )
    physical_capture.assert_called_once()
    assert physical_blocked["status"] == "BLOCKED"
    assert physical_blocked["blocking_reason_codes"] == [
        "R8U_R3_PHYSICAL_RESERVE_INSUFFICIENT"
    ]
    assert physical_blocked["physical_margin_beyond_reserve_bytes"] == -1

    file_quota = capacity.EXPECTED_RESEARCH_FILE_QUOTA
    required_files = int(passing["required_file_slots"])
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
        "R8U_R3_FILE_SLOTS_INSUFFICIENT"
    ]
    assert file_blocked["file_slot_margin_after_demand"] == -1


def test_every_r8u_r3_capacity_test_is_zero_argument() -> None:
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
