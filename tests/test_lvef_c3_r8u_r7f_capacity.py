from __future__ import annotations

"""Focused proofs for the production-derived R7F Tasks-17--19 gate."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as frozen_capacity
import lvef_c3_r8u_r7d_capacity as capacity


R7F_RUNTIME_COMMIT = "7" * 40


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _distribute(total: int, count: int) -> list[int]:
    base, remainder = divmod(total, count)
    return [base + int(index < remainder) for index in range(count)]


def _production_scalar_plan() -> dict[str, Any]:
    objects = [
        *_distribute(277_700, 15),
        18_677,
        18_606,
        18_658,
        2_343,
    ]
    source_bytes = [
        *_distribute(1_003_366_146_696, 15),
        68_000_000_000,
        66_807_894_336,
        68_754_613_138,
        9_640_479_152,
    ]
    return {
        "schema_version": 3,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v3",
        "authority": {"git_commit": capacity.R8U_ORIGINAL_SCIENTIFIC_COMMIT},
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


@contextmanager
def _plan_authority(plan: dict[str, Any]) -> Iterator[None]:
    digest = _canonical_sha256(plan)
    with (
        mock.patch.object(frozen_capacity, "R8U_ORIGINAL_PLAN_SHA256", digest),
        mock.patch.object(capacity, "R8U_ORIGINAL_PLAN_SHA256", digest),
    ):
        yield


def _native_payload() -> bytes:
    return (
        "rproject_mimicecho root FILESET 0 52428800 0 0 none | "
        "0 1638400 0 0 none\n"
        "rprojectnb_mimicecho root FILESET 0 2044723200 0 0 none | "
        "0 33554432 0 0 none\n"
    ).encode("ascii")


def _authority(root: Path) -> Any:
    tools = root / "tools"
    tools.mkdir(mode=0o700)
    research = root / "research"
    backed = root / "backed"
    research.mkdir(mode=0o700)
    backed.mkdir(mode=0o700)
    for name in ("pquota", "findmnt", "df"):
        path = tools / name
        path.write_bytes(b"#!/bin/sh\nexit 97\n")
        path.chmod(0o700)
    native = tools / "project.quota"
    native.write_bytes(_native_payload())
    native.chmod(0o600)
    return frozen_capacity.CurrentCanaryHeadroomAuthority(
        native_quota_path=native,
        pquota_path=tools / "pquota",
        findmnt_path=tools / "findmnt",
        df_path=tools / "df",
        research_path=research,
        backed_path=backed,
    )


def _runner(
    authority: Any,
    calls: list[str],
    *,
    research_available: int = 2_500_000_000_000,
    fail_role: str | None = None,
) -> Any:
    role_counts = {"findmnt": 0, "df": 0}

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = Path(argv[0]).name
        if command == "pquota":
            role = "pquota"
        else:
            role_counts[command] += 1
            role = (
                ("research_" if role_counts[command] == 1 else "backed_")
                + command
            )
        calls.append(role)
        if role == fail_role:
            return subprocess.CompletedProcess(argv, 17, b"failed", b"")
        if command == "pquota":
            stdout = (
                "/rproject/mimicecho 50 1638400 0 0\n"
                "/rprojectnb/mimicecho 1950 33554432 0 0\n"
            ).encode("ascii")
        else:
            target = Path(argv[-1] if command == "df" else argv[3])
            target_role = "research" if target == authority.research_path else "backed"
            source = f"synthetic:/{target_role}"
            if command == "findmnt":
                stdout = json.dumps(
                    {
                        "filesystems": [
                            {
                                "source": source,
                                "target": str(target),
                                "fstype": "syntheticfs",
                                "options": "rw",
                                "fsroot": "/",
                            }
                        ]
                    }
                ).encode("utf-8")
            else:
                available = (
                    research_available
                    if target_role == "research"
                    else 2_500_000_000_000
                )
                stdout = (
                    "Filesystem 1B-blocks Used Avail Mounted on\n"
                    f"{source} {available + 1} 1 {available} {target}\n"
                ).encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    return run


def test_r7f_projection_is_exact_and_preserves_consumed_r7e_diagnosis() -> None:
    plan = _production_scalar_plan()
    with _plan_authority(plan):
        value = capacity.build_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
        assert capacity.validate_fixed_r8u_r7f_tasks17_19_plan_projection(
            plan, value
        ) == value
        assert capacity.require_fixed_r8u_r7f_tasks17_19_plan_projection(plan) == value

    assert value["status"] == capacity.R8U_R7F_STATIC_PLAN_PROJECTION_STATUS_PASS
    assert value["mismatching_fields"] == []
    assert value["mismatch_codes"] == []
    assert value["task_projection"] == list(
        capacity.R8U_R7F_TASKS17_19_SCALAR_PROJECTION
    )
    current = {item["field_name"]: item for item in value["comparators"]}
    assert current["remaining_source_byte_count"][
        "immutable_plan_derived_observed_value"
    ] == 145_202_986_626
    assert current["largest_remaining_batch_object_count"][
        "immutable_plan_derived_observed_value"
    ] == 18_658
    assert current["largest_remaining_batch_source_byte_count"][
        "immutable_plan_derived_observed_value"
    ] == 68_754_613_138
    assert current["active_extraction_cache_demand_bytes"][
        "immutable_plan_derived_observed_value"
    ] == 18_658 * 4_816_896
    assert current["incremental_demand_bytes"][
        "immutable_plan_derived_observed_value"
    ] == 380_995_646_484
    assert current["required_file_slots"][
        "immutable_plan_derived_observed_value"
    ] == 158_265

    historical_scalar_mismatches = {
        item["field_name"]
        for item in value["historical_r7e_comparators"]
        if item["comparison"] == "MISMATCH"
        and not item["field_name"].startswith("task_")
    }
    assert historical_scalar_mismatches == {
        "remaining_source_byte_count",
        "largest_remaining_batch_object_count",
        "largest_remaining_batch_source_byte_count",
        "active_extraction_cache_demand_bytes",
        "incremental_demand_bytes",
        "required_file_slots",
    }
    assert {
        field
        for field in value["historical_r7e_mismatching_fields"]
        if field.startswith("task_")
    } == {
        f"task_{task}.{field}"
        for task in (17, 18, 19)
        for field in ("n_objects", "source_bytes")
    }


def test_r7f_static_mismatches_are_field_specific_before_live_commands() -> None:
    plan = _production_scalar_plan()
    plan["batches"][18]["source_bytes"] += 1
    plan["batches"][0]["source_bytes"] -= 1
    calls: list[str] = []
    with tempfile.TemporaryDirectory() as temporary_name:
        raw_root = Path(temporary_name).resolve(strict=True) / "raw"
        with _plan_authority(plan):
            try:
                capacity.capture_fixed_r8u_r7f_tasks17_19_capacity(
                    plan,
                    r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                    raw_capture_root=raw_root,
                    process_runner=lambda *_args, **_kwargs: calls.append("called"),
                )
            except capacity.R8UR7FPlanProjectionError as exc:
                assert exc.code == "REMAINING_SOURCE_BYTES_MISMATCH"
                assert "INCREMENTAL_DEMAND_MISMATCH" in exc.projection[
                    "mismatch_codes"
                ]
            else:
                raise AssertionError("expected a field-specific plan mismatch")
        assert calls == []
        assert not raw_root.exists()

    plan = _production_scalar_plan()
    plan["batches"][17]["n_objects"] += 1
    plan["batches"][16]["n_objects"] -= 1
    with _plan_authority(plan):
        try:
            capacity.require_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
        except capacity.R8UR7FPlanProjectionError as exc:
            assert exc.code == "LARGEST_BATCH_OBJECTS_MISMATCH"
            assert {
                "ACTIVE_EXTRACTION_CACHE_DEMAND_MISMATCH",
                "INCREMENTAL_DEMAND_MISMATCH",
                "REQUIRED_FILE_SLOTS_MISMATCH",
            }.issubset(set(exc.projection["mismatch_codes"]))
        else:
            raise AssertionError("expected largest-object mismatch")

    plan = _production_scalar_plan()
    plan["batches"][17]["source_bytes"] += 1
    plan["batches"][16]["source_bytes"] -= 1
    with _plan_authority(plan):
        try:
            capacity.require_fixed_r8u_r7f_tasks17_19_plan_projection(plan)
        except capacity.R8UR7FPlanProjectionError as exc:
            assert exc.code == "LARGEST_BATCH_SOURCE_BYTES_MISMATCH"
            assert "INCREMENTAL_DEMAND_MISMATCH" in exc.projection[
                "mismatch_codes"
            ]
        else:
            raise AssertionError("expected largest-source mismatch")


def test_r7f_one_observation_uses_production_arithmetic_and_is_no_clobber() -> None:
    plan = _production_scalar_plan()
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        output = temporary / "r7f"
        output.mkdir(mode=0o700)
        authority = _authority(temporary)
        calls: list[str] = []
        receipt = output / "capacity.restricted.json"
        raw_root = output / "raw_captures"
        with _plan_authority(plan):
            value = capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
                plan,
                r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                receipt_path=receipt,
                raw_capture_root=raw_root,
                authority=authority,
                process_runner=_runner(authority, calls),
            )
            assert capacity.validate_fixed_r8u_r7f_tasks17_19_capacity(
                plan,
                value,
                r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                raw_capture_root=raw_root,
            ) == value
        assert value["artifact_type"] == capacity.R8U_R7F_CAPACITY_ARTIFACT_TYPE
        assert value["status"] == capacity.R8U_R7F_CAPACITY_STATUS_PASS
        assert value["r7f_runtime_commit"] == R7F_RUNTIME_COMMIT
        assert value["capacity_observation_count"] == 1
        assert value["pquota_command_captures"] == 1
        assert value["findmnt_command_captures"] == 2
        assert value["df_command_captures"] == 2
        assert value["du_command_captures"] == 0
        assert calls == [
            "pquota",
            "research_findmnt",
            "backed_findmnt",
            "research_df",
            "backed_df",
        ]
        projection = value["capacity_projection"]
        assert projection["r7f_increment_bytes"] == 380_995_646_484
        assert projection["required_file_slots"] == 158_265
        assert projection["required_quota_reserve_bytes"] == 200_000_000_000
        assert projection["required_physical_reserve_bytes"] == 200_000_000_000
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
        assert len(list(raw_root.iterdir())) == 10

        collision_calls: list[str] = []
        with _plan_authority(plan):
            try:
                capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
                    plan,
                    r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                    receipt_path=receipt,
                    raw_capture_root=output / "second_raw",
                    authority=authority,
                    process_runner=lambda *_args, **_kwargs: collision_calls.append(
                        "called"
                    ),
                )
            except capacity.PostReallocationCapacityError as exc:
                assert exc.code == "R8U_R7F_CAPACITY_RECEIPT_COLLISION"
            else:
                raise AssertionError("expected receipt collision")
        assert collision_calls == []
        assert not (output / "second_raw").exists()


def test_r7f_deficit_and_command_failure_are_not_relabelled_as_each_other() -> None:
    plan = _production_scalar_plan()
    required = (
        capacity.R8U_R7F_INCREMENT_BYTES
        + capacity.R8U_R7D_REQUIRED_PHYSICAL_RESERVE_BYTES
    )
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        output = temporary / "r7f"
        output.mkdir(mode=0o700)
        authority = _authority(temporary)
        with _plan_authority(plan):
            value = capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
                plan,
                r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                receipt_path=output / "capacity.restricted.json",
                raw_capture_root=output / "raw",
                authority=authority,
                process_runner=_runner(
                    authority, [], research_available=required - 123
                ),
            )
        assert value["status"] == capacity.R8U_R7F_CAPACITY_STATUS_DEFICIT
        assert value["valid_numerical_deficit_calculated"] is True
        assert value["capacity_projection"][
            "physical_reserve_deficit_bytes"
        ] == 123

    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        output = temporary / "r7f"
        output.mkdir(mode=0o700)
        authority = _authority(temporary)
        receipt = output / "capacity.restricted.json"
        try:
            with _plan_authority(plan):
                capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity(
                    plan,
                    r7f_runtime_commit=R7F_RUNTIME_COMMIT,
                    receipt_path=receipt,
                    raw_capture_root=output / "raw",
                    authority=authority,
                    process_runner=_runner(authority, [], fail_role="pquota"),
                )
        except capacity.R8UR7FCapacityObservationError as exc:
            assert exc.code == (
                capacity.R8U_R7F_CAPACITY_STATUS_OBSERVATION_PREFIX
                + "PQUOTA_COMMAND_EXIT_NONZERO"
            )
            assert exc.receipt_sha256 == hashlib.sha256(
                receipt.read_bytes()
            ).hexdigest()
            assert exc.receipt is not None
            assert exc.receipt["valid_numerical_deficit_calculated"] is False
        else:
            raise AssertionError("expected a sealed command failure")


def _run_dependency_light() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


if __name__ == "__main__":
    _run_dependency_light()
