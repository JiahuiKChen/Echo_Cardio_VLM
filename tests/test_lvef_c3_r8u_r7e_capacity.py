from __future__ import annotations

"""Focused dependency-light proofs for the R7E capacity observation layer."""

import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as frozen_capacity
import lvef_c3_r8u_r7d_capacity as capacity


R7E_RUNTIME_COMMIT = "e" * 40


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


def _native_payload() -> bytes:
    return (
        "rproject_mimicecho root FILESET 0 52428800 0 0 none | "
        "0 1638400 0 0 none\n"
        "rprojectnb_mimicecho root FILESET 0 2044723200 0 0 none | "
        "0 33554432 0 0 none\n"
    ).encode("ascii")


def _authority(root: Path, *, native_payload: bytes | None = None) -> Any:
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
    native.write_bytes(
        _native_payload() if native_payload is None else native_payload
    )
    native.chmod(0o600)
    return frozen_capacity.CurrentCanaryHeadroomAuthority(
        native_quota_path=native,
        pquota_path=tools / "pquota",
        findmnt_path=tools / "findmnt",
        df_path=tools / "df",
        research_path=research,
        backed_path=backed,
    )


def _valid_pquota() -> bytes:
    return (
        "/rproject/mimicecho 50 1638400 0 0\n"
        "/rprojectnb/mimicecho 1950 33554432 0 0\n"
    ).encode("ascii")


def _runner(
    authority: Any,
    *,
    fail_role: str | None = None,
    malformed_role: str | None = None,
    missing_role: str | None = None,
    ambiguous_role: str | None = None,
    filesystem_invalid_role: str | None = None,
    research_available: int = 2_500_000_000_000,
    calls: list[str] | None = None,
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
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
        if calls is not None:
            calls.append(role)
        if role == missing_role:
            return SimpleNamespace(returncode=0, stdout=None, stderr=b"")
        if role == fail_role:
            return subprocess.CompletedProcess(argv, 17, b"failed", b"")
        if role == malformed_role:
            return subprocess.CompletedProcess(argv, 0, b"not valid", b"")
        if role == ambiguous_role:
            return subprocess.CompletedProcess(
                argv,
                0,
                b'{"filesystems":[]}',
                b"",
            )
        if command == "pquota":
            stdout = _valid_pquota()
        else:
            target = Path(argv[-1] if command == "df" else argv[3])
            target_role = (
                "research" if target == authority.research_path else "backed"
            )
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
                if role == filesystem_invalid_role:
                    stdout = (
                        "Filesystem 1B-blocks Used Avail Mounted on\n"
                        f"{source} 10 8 3 {target}\n"
                    ).encode("utf-8")
                else:
                    stdout = (
                        "Filesystem 1B-blocks Used Avail Mounted on\n"
                        f"{source} {available + 1} 1 {available} {target}\n"
                    ).encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    return run


def _plan_authority(plan: dict[str, Any]) -> mock._patch[Any]:
    plan_sha = _canonical_sha256(plan)
    patches = mock.patch.multiple(
        frozen_capacity,
        R8U_ORIGINAL_PLAN_SHA256=plan_sha,
    )
    return patches


def _capture(
    temporary: Path,
    *,
    fail_role: str | None = None,
    malformed_role: str | None = None,
    research_available: int = 2_500_000_000_000,
    precreate_raw_root: bool = False,
) -> tuple[dict[str, Any], Path, Path, list[str], dict[str, Any]]:
    plan = _fixed_plan()
    authority = _authority(temporary)
    output_root = temporary / "r7e"
    output_root.mkdir(mode=0o700)
    raw_root = output_root / "raw_captures"
    if precreate_raw_root:
        raw_root.mkdir(mode=0o700)
    receipt_path = output_root / "capacity.restricted.json"
    calls: list[str] = []
    kwargs = {
        "r7e_runtime_commit": R7E_RUNTIME_COMMIT,
        "receipt_path": receipt_path,
        "raw_capture_root": raw_root,
        "authority": authority,
        "process_runner": _runner(
            authority,
            fail_role=fail_role,
            malformed_role=malformed_role,
            research_available=research_available,
            calls=calls,
        ),
    }
    with (
        _plan_authority(plan),
        mock.patch.object(
            capacity,
            "R8U_ORIGINAL_PLAN_SHA256",
            _canonical_sha256(plan),
        ),
    ):
        value = capacity.capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity(
            plan,
            **kwargs,
        )
    return value, receipt_path, raw_root, calls, kwargs


def _expect_observation_failure(
    temporary: Path,
    *,
    fail_role: str | None = None,
    malformed_role: str | None = None,
    missing_role: str | None = None,
    ambiguous_role: str | None = None,
    filesystem_invalid_role: str | None = None,
    native_payload: bytes | None = None,
    nonregular_role: str | None = None,
    nonregular_stream: tuple[str, str] | None = None,
) -> tuple[capacity.R8UR7ECapacityObservationError, dict[str, Any]]:
    plan = _fixed_plan()
    authority = _authority(temporary, native_payload=native_payload)
    output_root = temporary / "r7e"
    output_root.mkdir(mode=0o700)
    receipt_path = output_root / "capacity.restricted.json"
    real_private_write = capacity._r8u_r7e_write_new_private_bytes

    def conditional_private_write(path: Path, payload: bytes) -> str:
        role_failure = (
            nonregular_role is not None
            and f"_{nonregular_role}." in path.name
        )
        stream_failure = (
            nonregular_stream is not None
            and f"_{nonregular_stream[0]}.{nonregular_stream[1]}.bin"
            in path.name
        )
        if role_failure or stream_failure:
            raise capacity.PostReallocationCapacityError(
                "R8U_R7E_PRIVATE_OUTPUT_WRITE_FAILED"
            )
        return real_private_write(path, payload)

    try:
        with ExitStack() as stack:
            stack.enter_context(_plan_authority(plan))
            stack.enter_context(mock.patch.object(
                capacity,
                "R8U_ORIGINAL_PLAN_SHA256",
                _canonical_sha256(plan),
            ))
            if nonregular_role is not None or nonregular_stream is not None:
                stack.enter_context(mock.patch.object(
                    capacity,
                    "_r8u_r7e_write_new_private_bytes",
                    side_effect=conditional_private_write,
                ))
            capacity.capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity(
                plan,
                r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                receipt_path=receipt_path,
                raw_capture_root=output_root / "raw_captures",
                authority=authority,
                process_runner=_runner(
                    authority,
                    fail_role=fail_role,
                    malformed_role=malformed_role,
                    missing_role=missing_role,
                    ambiguous_role=ambiguous_role,
                    filesystem_invalid_role=filesystem_invalid_role,
                ),
            )
    except capacity.R8UR7ECapacityObservationError as exc:
        persisted = json.loads(receipt_path.read_text())
        assert exc.receipt == persisted
        assert exc.receipt_sha256 == hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest()
        assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
        return exc, persisted
    raise AssertionError("expected a sealed R7E observation failure")


def test_r7e_pass_is_exactly_one_observation_and_sealed_private() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        value, receipt_path, raw_root, calls, _ = _capture(
            temporary,
            precreate_raw_root=True,
        )

        assert value["status"] == capacity.R8U_R7E_CAPACITY_STATUS_PASS
        assert value["capacity_observation_count"] == 1
        assert value["native_quota_file_captures"] == 1
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
        assert value["raw_capture_file_count"] == 10
        assert len(list(raw_root.iterdir())) == 10
        assert all(
            item.is_file() and stat.S_IMODE(item.stat().st_mode) == 0o600
            for item in raw_root.iterdir()
        )
        assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
        serialized = receipt_path.read_text()
        assert "synthetic:/research" not in serialized
        assert str(temporary) not in serialized


def test_r7e_quantifies_deficit_without_relabeling_observation_failure() -> None:
    required = (
        capacity.R8U_R7D_INCREMENT_BYTES
        + capacity.R8U_R7D_REQUIRED_PHYSICAL_RESERVE_BYTES
    )
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        value, _, _, _, _ = _capture(
            temporary,
            research_available=required - 123,
        )
        assert value["status"] == capacity.R8U_R7E_CAPACITY_STATUS_DEFICIT
        assert value["valid_numerical_deficit_calculated"] is True
        assert value["capacity_projection"][
            "physical_reserve_deficit_bytes"
        ] == 123


def test_r7e_every_nonzero_command_exit_is_field_specific_and_sealed() -> None:
    expected = {
        "pquota": "PQUOTA_COMMAND_EXIT_NONZERO",
        "research_findmnt": "RESEARCH_FINDMNT_COMMAND_EXIT_NONZERO",
        "backed_findmnt": "BACKED_FINDMNT_COMMAND_EXIT_NONZERO",
        "research_df": "RESEARCH_DF_COMMAND_EXIT_NONZERO",
        "backed_df": "BACKED_DF_COMMAND_EXIT_NONZERO",
    }
    for role, suffix in expected.items():
        with tempfile.TemporaryDirectory() as temporary_name:
            exc, receipt = _expect_observation_failure(
                Path(temporary_name).resolve(strict=True),
                fail_role=role,
            )
            assert exc.code == (
                capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX + suffix
            )
            diagnostic = receipt["failure_diagnostic"]
            assert diagnostic["failure_field"] == f"{role}.capture"
            assert diagnostic["failure_predicate"] == "COMMAND_EXIT_NONZERO"
            assert diagnostic["command_exit_status"] == 17
            assert diagnostic["command_exit_class"] == "NONZERO"
            assert diagnostic["capture_present"] is True
            assert diagnostic["capture_regular_file"] is True
            assert receipt["capacity_observation_count"] == 1
            assert receipt["du_command_captures"] == 0


def test_r7e_every_parser_failure_is_field_specific_and_sealed() -> None:
    expected = {
        "pquota": "PQUOTA_PARSE_FAILURE",
        "research_findmnt": "RESEARCH_FINDMNT_PARSE_FAILURE",
        "backed_findmnt": "BACKED_FINDMNT_PARSE_FAILURE",
        "research_df": "RESEARCH_DF_PARSE_FAILURE",
        "backed_df": "BACKED_DF_PARSE_FAILURE",
    }
    for role, suffix in expected.items():
        with tempfile.TemporaryDirectory() as temporary_name:
            exc, receipt = _expect_observation_failure(
                Path(temporary_name).resolve(strict=True),
                malformed_role=role,
            )
            assert exc.code == (
                capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX + suffix
            )
            diagnostic = receipt["failure_diagnostic"]
            assert diagnostic["failure_predicate"].endswith("PARSE_FAILURE")
            assert diagnostic["parser_result"] == "FAIL"
            assert diagnostic["raw_capture_sha256"] is not None


def test_r7e_missing_and_nonregular_captures_are_distinct_and_sealed() -> None:
    cases = (
        (
            {"missing_role": "research_df"},
            "RESEARCH_DF_COMMAND_CAPTURE_MISSING",
            "COMMAND_CAPTURE_MISSING",
            False,
        ),
        (
            {"nonregular_role": "pquota"},
            "PQUOTA_COMMAND_CAPTURE_NOT_REGULAR",
            "COMMAND_CAPTURE_NOT_REGULAR",
            True,
        ),
    )
    for arguments, suffix, predicate, capture_present in cases:
        with tempfile.TemporaryDirectory() as temporary_name:
            exc, receipt = _expect_observation_failure(
                Path(temporary_name).resolve(strict=True),
                **arguments,
            )
            assert exc.code == (
                capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX + suffix
            )
            diagnostic = receipt["failure_diagnostic"]
            assert diagnostic["failure_predicate"] == predicate
            assert diagnostic["capture_present"] is capture_present
            assert diagnostic["capture_regular_file"] is False
            if predicate == "COMMAND_CAPTURE_MISSING":
                assert receipt["df_command_invocation_attempts"] == 2
                assert receipt["df_command_captures"] == 1

    with tempfile.TemporaryDirectory() as temporary_name:
        _, receipt = _expect_observation_failure(
            Path(temporary_name).resolve(strict=True),
            nonregular_stream=("pquota", "stdout"),
        )
        first = receipt["command_diagnostics"][0]
        assert first["stdout_capture_regular_file"] is False
        assert first["stderr_capture_regular_file"] is True
        assert receipt["raw_capture_file_count"] == 9


def test_r7e_ambiguous_mount_resolution_is_field_specific() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        exc, receipt = _expect_observation_failure(
            Path(temporary_name).resolve(strict=True),
            ambiguous_role="research_findmnt",
        )
        assert exc.code == (
            capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
            + "RESEARCH_FINDMNT_MOUNT_RESOLUTION_AMBIGUOUS"
        )
        diagnostic = receipt["failure_diagnostic"]
        assert diagnostic["failure_field"] == (
            "research_findmnt.parsed_output"
        )
        assert diagnostic["failure_predicate"] == (
            "MOUNT_RESOLUTION_AMBIGUOUS"
        )
        assert diagnostic["source_error_code"] == "FINDMNT_ROW_COUNT_INVALID"


def test_r7e_quota_authority_and_numeric_failures_remain_distinct() -> None:
    cases = (
        (
            _native_payload().replace(b"2044723200", b"2044723199"),
            "QUOTA_AUTHORITY_MISMATCH",
            "native_quota_file.allocation_authority",
        ),
        (
            _native_payload().replace(
                b"rprojectnb_mimicecho root FILESET 0 ",
                b"rprojectnb_mimicecho root FILESET -1 ",
            ),
            "NUMERIC_VALUE_INVALID",
            "native_quota_file.numeric_values",
        ),
        (
            _native_payload().replace(
                b"rprojectnb_mimicecho root FILESET 0 ",
                b"rprojectnb_mimicecho root FILESET not_an_integer ",
            ),
            "NUMERIC_VALUE_INVALID",
            "native_quota_file.numeric_values",
        ),
    )
    for payload, suffix, field in cases:
        with tempfile.TemporaryDirectory() as temporary_name:
            exc, receipt = _expect_observation_failure(
                Path(temporary_name).resolve(strict=True),
                native_payload=payload,
            )
            assert exc.code == (
                capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX + suffix
            )
            assert receipt["failure_diagnostic"]["failure_field"] == field
            assert receipt["native_quota_file_captures"] == 1
            assert receipt["native_quota_sha256"] == hashlib.sha256(
                payload
            ).hexdigest()


def test_r7e_filesystem_used_plus_available_invariant_is_specific() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        exc, receipt = _expect_observation_failure(
            Path(temporary_name).resolve(strict=True),
            filesystem_invalid_role="research_df",
        )
        assert exc.code == (
            capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
            + "RESEARCH_DF_FILESYSTEM_ARITHMETIC_INVALID"
        )
        diagnostic = receipt["failure_diagnostic"]
        assert diagnostic["failure_predicate"] == (
            "FILESYSTEM_ARITHMETIC_INVALID"
        )
        assert diagnostic["source_error_code"] == (
            "DF_BYTES_DO_NOT_RECONCILE"
        )
        assert receipt["arithmetic_evaluated"] is False
        assert receipt["valid_numerical_deficit_calculated"] is False


def test_r7e_static_failures_seal_without_live_commands() -> None:
    cases = (
        ("plan", "PLAN_SCOPE_MISMATCH", "plan.tasks17_19_scope"),
        (
            "baseline",
            "BASELINE_AUTHORITY_INVALID",
            "preserved_old_control_evidence_bytes_baseline",
        ),
    )
    for kind, suffix, failure_field in cases:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name).resolve(strict=True)
            plan = _fixed_plan()
            if kind == "plan":
                plan["batches"] = plan["batches"][:-1]
            authority = _authority(temporary)
            output_root = temporary / "r7e"
            output_root.mkdir(mode=0o700)
            receipt_path = output_root / "capacity.restricted.json"
            calls: list[str] = []
            extra = (
                {"preserved_old_evidence_bytes": -1}
                if kind == "baseline"
                else {}
            )
            try:
                with (
                    _plan_authority(plan),
                    mock.patch.object(
                        capacity,
                        "R8U_ORIGINAL_PLAN_SHA256",
                        _canonical_sha256(plan),
                    ),
                ):
                    capacity.capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity(
                        plan,
                        r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                        receipt_path=receipt_path,
                        raw_capture_root=output_root / "raw_captures",
                        authority=authority,
                        process_runner=_runner(authority, calls=calls),
                        **extra,
                    )
            except capacity.R8UR7ECapacityObservationError as exc:
                receipt = json.loads(receipt_path.read_text())
                assert exc.code == (
                    capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
                    + suffix
                )
                assert receipt["failure_diagnostic"]["failure_field"] == (
                    failure_field
                )
                assert receipt["failure_diagnostic"]["failure_stage"] == (
                    "STATIC"
                )
                assert receipt["capacity_observation_count"] == 0
                assert receipt["command_diagnostics"] == []
                assert calls == []
                assert not (output_root / "raw_captures").exists()
            else:
                raise AssertionError("static authority failure was accepted")


def test_r7e_native_read_failure_seals_before_native_capture() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        exc, receipt = _expect_observation_failure(
            Path(temporary_name).resolve(strict=True),
            native_payload=b"",
        )
        assert exc.code == (
            capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
            + "QUOTA_AUTHORITY_MISMATCH"
        )
        assert receipt["failure_diagnostic"]["failure_stage"] == (
            "NATIVE_QUOTA_READ"
        )
        assert receipt["native_quota_file_captures"] == 0
        assert receipt["native_quota_size_bytes"] is None
        assert receipt["native_quota_sha256"] is None


def test_r7e_receipt_collision_is_preflighted_before_observation() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        plan = _fixed_plan()
        authority = _authority(temporary)
        output_root = temporary / "r7e"
        output_root.mkdir(mode=0o700)
        receipt_path = output_root / "capacity.restricted.json"
        receipt_path.write_bytes(b"preexisting\n")
        receipt_path.chmod(0o600)
        calls: list[str] = []
        try:
            with (
                _plan_authority(plan),
                mock.patch.object(
                    capacity,
                    "R8U_ORIGINAL_PLAN_SHA256",
                    _canonical_sha256(plan),
                ),
            ):
                capacity.capture_validate_and_seal_fixed_r8u_r7e_tasks17_19_capacity(
                    plan,
                    r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                    receipt_path=receipt_path,
                    raw_capture_root=output_root / "raw_captures",
                    authority=authority,
                    process_runner=_runner(authority, calls=calls),
                )
        except capacity.PostReallocationCapacityError as exc:
            assert exc.code == "R8U_R7E_CAPACITY_RECEIPT_COLLISION"
        else:
            raise AssertionError("preexisting receipt was overwritten")
        assert calls == []
        assert receipt_path.read_bytes() == b"preexisting\n"
        assert not (output_root / "raw_captures").exists()


def test_r7e_replay_rejects_semantic_relabel_and_raw_tampering() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        mismatch = _native_payload().replace(b"2044723200", b"2044723199")
        _, receipt = _expect_observation_failure(
            temporary,
            native_payload=mismatch,
        )
        plan = _fixed_plan()
        relabeled = json.loads(json.dumps(receipt))
        relabeled["status"] = (
            capacity.R8U_R7E_CAPACITY_STATUS_OBSERVATION_PREFIX
            + "NUMERIC_VALUE_INVALID"
        )
        relabeled["failure_diagnostic"].update(
            {
                "failure_code": relabeled["status"],
                "failure_field": "native_quota_file.numeric_values",
                "failure_predicate": "NUMERIC_VALUE_INVALID",
                "expected_value_category": (
                    "NONNEGATIVE_BOUNDED_INTEGER_QUOTA_VALUES"
                ),
            }
        )
        with (
            _plan_authority(plan),
            mock.patch.object(
                capacity,
                "R8U_ORIGINAL_PLAN_SHA256",
                _canonical_sha256(plan),
            ),
        ):
            try:
                capacity.validate_fixed_r8u_r7e_tasks17_19_capacity(
                    plan,
                    relabeled,
                    r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                    raw_capture_root=temporary / "r7e" / "raw_captures",
                )
            except capacity.PostReallocationCapacityError:
                pass
            else:
                raise AssertionError("semantic failure relabel was accepted")

    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        value, _, raw_root, _, _ = _capture(temporary)
        target = raw_root / "01_pquota.stdout.bin"
        target.write_bytes(target.read_bytes() + b"tamper")
        plan = _fixed_plan()
        with (
            _plan_authority(plan),
            mock.patch.object(
                capacity,
                "R8U_ORIGINAL_PLAN_SHA256",
                _canonical_sha256(plan),
            ),
        ):
            try:
                capacity.validate_fixed_r8u_r7e_tasks17_19_capacity(
                    plan,
                    value,
                    r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                    raw_capture_root=raw_root,
                )
            except capacity.PostReallocationCapacityError:
                pass
            else:
                raise AssertionError("raw capture tamper was accepted")


def test_r7e_replay_rejects_counter_and_arithmetic_tampering() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        value, _, _, _, _ = _capture(temporary)
        plan = _fixed_plan()
        for field in ("findmnt_command_captures", "du_command_captures"):
            tampered = dict(value)
            tampered[field] += 1
            with (
                _plan_authority(plan),
                mock.patch.object(
                    capacity,
                    "R8U_ORIGINAL_PLAN_SHA256",
                    _canonical_sha256(plan),
                ),
            ):
                try:
                    capacity.validate_fixed_r8u_r7e_tasks17_19_capacity(
                        plan,
                        tampered,
                        r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                    )
                except capacity.PostReallocationCapacityError:
                    pass
                else:
                    raise AssertionError("tampered counter was accepted")

        tampered = dict(value)
        projection = dict(value["capacity_projection"])
        projection["quota_reserve_deficit_bytes"] += 1
        tampered["capacity_projection"] = projection
        with (
            _plan_authority(plan),
            mock.patch.object(
                capacity,
                "R8U_ORIGINAL_PLAN_SHA256",
                _canonical_sha256(plan),
            ),
        ):
            try:
                capacity.validate_fixed_r8u_r7e_tasks17_19_capacity(
                    plan,
                    tampered,
                    r7e_runtime_commit=R7E_RUNTIME_COMMIT,
                )
            except capacity.PostReallocationCapacityError as exc:
                assert exc.code == "R8U_R7D_CAPACITY_ARITHMETIC_INVALID"
            else:
                raise AssertionError("tampered arithmetic was accepted")


def test_r7e_receipt_publication_is_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name).resolve(strict=True)
        value, receipt_path, _, _, _ = _capture(temporary)
        original = receipt_path.read_bytes()
        try:
            capacity.write_r8u_r7e_capacity_receipt_no_clobber(
                receipt_path,
                value,
            )
        except capacity.PostReallocationCapacityError as exc:
            assert exc.code == "R8U_R7E_PRIVATE_OUTPUT_COLLISION"
        else:
            raise AssertionError("receipt collision was accepted")
        assert receipt_path.read_bytes() == original


def _run_dependency_light() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


if __name__ == "__main__":
    _run_dependency_light()
