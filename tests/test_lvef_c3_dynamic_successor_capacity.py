from __future__ import annotations

"""Dependency-light R5B-R1 static-versus-dynamic capacity release gates.

Every test is deliberately zero-argument so the maintained SCC runner can
execute this file without pytest.  Fixtures are synthetic and no test invokes
the production SCC paths, cloud clients, schedulers, or scientific workers.
"""

import copy
from contextlib import contextmanager
from dataclasses import replace as dataclass_replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as capacity
import lvef_c3_full_sequential as sequential
import lvef_c3_orchestration_core as core
import replay_lvef_c3_failed_extraction_one_object as replay
import retire_lvef_c3_older_raw_duplicates as raw_retirement


HISTORICAL_RECEIPT_KEY_SET_SHA256 = (
    "864847c3e7ea34fc1c7fe2a40b631661aeacd310501dcddbf7f14bbcb7df0579"
)
HISTORICAL_AGGREGATE_KEY_SET_SHA256 = (
    "5f0ccccad15877354d8f1bccec0b48850f60ee304dfc84a0871820ad07e1def4"
)
HISTORICAL_FULL_HEADROOM_KEY_SET_SHA256 = (
    "a9f34b5c9654762f75bb3c703ebb811ade14f896dee62e12b3286fc13cff0d8a"
)
HISTORICAL_NATIVE_FIXTURE_BYTES = 175
HISTORICAL_NATIVE_FIXTURE_SHA256 = (
    "735a8e2ca95183d87e9e6e2f290268639bda9c71089e42ad95a423186f8ff742"
)
GOVERNING_COMMIT = "7" * 40
HISTORICAL_RESEARCH_QUOTA_BYTES = 2_093_796_556_800
BACKED_QUOTA_BYTES = 53_687_091_200
LIVE_USAGE_BYTES = 527_008_808_960
REPRESENTATIVE_INCREMENT_BYTES = 1_000_000_000_000
PASS_PHYSICAL_AVAILABLE_BYTES = 1_800_000_000_000
CAPTURED_AT = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
SAFE_EXPORT_POLICY = ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"


def _key_set_sha256(value: object) -> str:
    payload = json.dumps(sorted(value), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _historical_native() -> bytes:
    return (
        "rproject_mimicecho root FILESET 10690224 52428800 0 0 none | "
        "47379 1638400 0 0 none\n"
        "rprojectnb_mimicecho root FILESET 147117696 2044723200 0 0 none | "
        "106407 33554432 0 0 none\n"
    ).encode("ascii")


def _dynamic_native(
    *,
    research_quota_bytes: int = (
        HISTORICAL_RESEARCH_QUOTA_BYTES + REPRESENTATIVE_INCREMENT_BYTES
    ),
    research_usage_bytes: int = LIVE_USAGE_BYTES,
    research_file_quota: int = 33_554_432,
    research_files_used: int = 501_481,
    backed_quota_bytes: int = BACKED_QUOTA_BYTES,
    backed_usage_bytes: int = 10_946_789_376,
    backed_file_quota: int = 1_638_400,
    backed_files_used: int = 47_379,
) -> bytes:
    for value in (
        research_quota_bytes,
        research_usage_bytes,
        backed_quota_bytes,
        backed_usage_bytes,
    ):
        assert value % 1024 == 0
    return (
        "rproject_mimicecho root FILESET "
        f"{backed_usage_bytes // 1024} {backed_quota_bytes // 1024} "
        f"0 0 none | {backed_files_used} {backed_file_quota} 0 0 none\n"
        "rprojectnb_mimicecho root FILESET "
        f"{research_usage_bytes // 1024} {research_quota_bytes // 1024} "
        f"0 0 none | {research_files_used} {research_file_quota} 0 0 none\n"
    ).encode("ascii")


def _probe_fixture(
    root: Path, *, native_payload: bytes | None = None
) -> capacity.CurrentCanaryHeadroomAuthority:
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
    native.write_bytes(native_payload or _dynamic_native())
    native.chmod(0o600)
    return capacity.CurrentCanaryHeadroomAuthority(
        native_quota_path=native,
        pquota_path=tools / "pquota",
        findmnt_path=tools / "findmnt",
        df_path=tools / "df",
        research_path=research,
        backed_path=backed,
    )


def _process_runner(
    authority: capacity.CurrentCanaryHeadroomAuthority,
    *,
    research_available: int = PASS_PHYSICAL_AVAILABLE_BYTES,
    pquota_returncode: int = 1,
    pquota_stdout: bytes = b"",
    pquota_stderr: bytes = b"",
    same_mount: bool = False,
    bind_research: bool = False,
    calls: list[tuple[str, ...]] | None = None,
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if calls is not None:
            calls.append(tuple(str(item) for item in argv))
        command = Path(argv[0]).name
        if command == "pquota":
            return subprocess.CompletedProcess(
                argv, pquota_returncode, pquota_stdout, pquota_stderr
            )
        target = Path(argv[-1] if command == "df" else argv[3])
        role = "research" if target == authority.research_path else "backed"
        source = "synthetic:/shared" if same_mount else f"synthetic:/{role}"
        if command == "findmnt":
            options = "rw,bind" if bind_research and role == "research" else "rw"
            stdout = json.dumps(
                {
                    "filesystems": [
                        {
                            "source": source,
                            "target": str(target),
                            "fstype": "syntheticfs",
                            "options": options,
                            "fsroot": "/",
                        }
                    ]
                }
            ).encode("utf-8")
        else:
            available = research_available if role == "research" else 20_000_000_000
            stdout = (
                "Filesystem 1B-blocks Used Avail Mounted on\n"
                f"{source} {available + 1} 1 {available} {target}\n"
            ).encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    return run


def _synthetic_path_identity(path: Path) -> Mapping[str, Any]:
    """Give the two synthetic mount roots distinct device identities."""

    resolved = path.resolve(strict=True)
    role = path.name
    assert role in {"research", "backed"}
    return {
        "path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
        "resolved_path_sha256": hashlib.sha256(
            str(resolved).encode()
        ).hexdigest(),
        "device": 101 if role == "research" else 202,
        "inode": 303 if role == "research" else 404,
        "is_symlink": False,
    }


def _capture(
    *,
    governing_commit: str = GOVERNING_COMMIT,
    native_payload: bytes | None = None,
    research_available: int = PASS_PHYSICAL_AVAILABLE_BYTES,
    active_caches: int = 0,
    preserved_failed_caches: int = 2,
    successor_root_absent: bool = True,
    successor_claim_absent: bool = True,
    pquota_returncode: int = 1,
    pquota_stdout: bytes = b"",
    pquota_stderr: bytes = b"",
    same_mount: bool = False,
    bind_research: bool = False,
    now_utc: datetime = CAPTURED_AT,
) -> Any:
    temporary = tempfile.TemporaryDirectory()
    try:
        authority = _probe_fixture(
            Path(temporary.name), native_payload=native_payload
        )
        with mock.patch.object(
            capacity, "_path_identity", side_effect=_synthetic_path_identity
        ):
            return capacity.probe_dynamic_successor_capacity_observation(
                governing_commit=governing_commit,
                active_extraction_caches=active_caches,
                preserved_terminal_failed_extraction_caches=(
                    preserved_failed_caches
                ),
                successor_attempt_root_absent=successor_root_absent,
                successor_claim_absent=successor_claim_absent,
                authority=authority,
                process_runner=_process_runner(
                    authority,
                    research_available=research_available,
                    pquota_returncode=pquota_returncode,
                    pquota_stdout=pquota_stdout,
                    pquota_stderr=pquota_stderr,
                    same_mount=same_mount,
                    bind_research=bind_research,
                ),
                now_utc=now_utc,
            )
    finally:
        temporary.cleanup()


def _error_code(error: BaseException) -> str:
    return str(getattr(error, "code", error))


def _expect_code(code: str, operation: Callable[[], Any]) -> None:
    try:
        operation()
    except Exception as error:
        assert _error_code(error) == code
    else:
        raise AssertionError(f"expected {code}")


def _expect_one_of_codes(
    codes: set[str], operation: Callable[[], Any]
) -> None:
    try:
        operation()
    except Exception as error:
        assert _error_code(error) in codes
    else:
        raise AssertionError(f"expected one of {sorted(codes)}")


def test_r5e_r2_append_only_pair_is_current_bound_and_preserves_a85_pair() -> None:
    capture = _capture(now_utc=CAPTURED_AT)
    with tempfile.TemporaryDirectory() as temporary:
        owner = Path(temporary).resolve() / "owner_private"
        owner.mkdir(mode=0o700)
        historical = (
            owner / capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            owner / capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        )
        for index, path in enumerate(historical):
            path.write_bytes(f"historical-{index}\n".encode())
            path.chmod(0o600)
        historical_before = tuple(path.read_bytes() for path in historical)
        restricted = (
            owner / capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        )
        summary = (
            owner / capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        )
        published = capacity.publish_dynamic_successor_capacity_capture(
            capture,
            restricted_receipt_path=restricted,
            aggregate_summary_path=summary,
            safe_export_policy_path=SAFE_EXPORT_POLICY,
            now_utc=CAPTURED_AT,
        )
        assert published["restricted_receipt_basename"] == restricted.name
        assert published["aggregate_summary_basename"] == summary.name
        assert tuple(path.read_bytes() for path in historical) == historical_before
        loaded = capacity.load_dynamic_successor_capacity_capture(
            restricted_receipt_path=restricted,
            aggregate_summary_path=summary,
            expected_governing_commit=GOVERNING_COMMIT,
            now_utc=CAPTURED_AT,
        )
        assert loaded.receipt_payload == capture.receipt_payload
        _expect_code(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
            lambda: capacity.load_dynamic_successor_capacity_capture(
                restricted_receipt_path=restricted,
                aggregate_summary_path=summary,
                expected_governing_commit="b" * 40,
                now_utc=CAPTURED_AT,
            ),
        )
        _expect_code(
            "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
            lambda: capacity.publish_dynamic_successor_capacity_capture(
                capture,
                restricted_receipt_path=restricted,
                aggregate_summary_path=summary,
                safe_export_policy_path=SAFE_EXPORT_POLICY,
                now_utc=CAPTURED_AT,
            ),
        )


def _rebind_dynamic_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    rebound = copy.deepcopy(receipt)
    command = rebound["commands"]["pquota"]
    rows = rebound["native_quota_authority"]["rows"]
    display = capacity._parse_pquota(
        command["stdout_text"],
        rows,
        command_available=(command["availability_status"] == "AVAILABLE"),
    )
    core = rebound["observation_authority"]
    core["command_authority_sha256"] = hashlib.sha256(
        capacity._canonical(rebound["commands"])
    ).hexdigest()
    core["pquota_display_crosscheck"] = display["status"]
    core["pquota_executable_authority_status"] = (
        "PASS_TRUSTED_ROOT_CONTROLLED"
        if command["availability_status"] == "AVAILABLE"
        else "UNAVAILABLE_NONBLOCKING"
    )
    rebound["observation_authority_sha256"] = hashlib.sha256(
        capacity._canonical(core)
    ).hexdigest()
    return rebound


def _synthetic_full_run(root: Path) -> SimpleNamespace:
    production_root = root / "production"
    owner_private = production_root / "owner_private"
    owner_private.mkdir(parents=True, mode=0o700)
    production_root.chmod(0o700)
    batches = [
        {
            "ordinal": ordinal,
            "batch_id": f"c3_batch_{ordinal:03d}",
            "n_studies": 250 if ordinal < 18 else 30,
            "n_objects": 1 if ordinal < 18 else 335_966,
            "source_bytes": 1 if ordinal < 18 else 1_216_569_133_304,
        }
        for ordinal in range(19)
    ]
    attempt_id = "lvef_c3_full_0123456789abcdef_01234567"
    attempt_root = production_root / "attempts" / attempt_id
    return SimpleNamespace(
        authority=SimpleNamespace(
            environment_receipt=root / "synthetic-environment.json",
            governing_commit=GOVERNING_COMMIT,
        ),
        runtime_authority={"environment_receipt_sha256": "8" * 64},
        production_root=production_root,
        attempt_root=attempt_root,
        attempt_id=attempt_id,
        plan={"batches": batches, "authority": {"git_commit": GOVERNING_COMMIT}},
        requirements=SimpleNamespace(batch_count=19),
        plan_sha256="4" * 64,
        launch_authority_sha256="5" * 64,
        plan_path=attempt_root / "full_batch_plan.restricted.json",
        launch_authority={"status": "SYNTHETIC_TEST_ONLY"},
    )


def _write_fixed_capture_pair(
    run: SimpleNamespace,
    capture: capacity.DynamicSuccessorCapacityCapture,
    *,
    restricted_basename: str = (
        capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
    ),
    summary_basename: str = (
        capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
    ),
) -> tuple[Path, Path]:
    owner_private = run.production_root / "owner_private"
    receipt_path = owner_private / restricted_basename
    summary_path = owner_private / summary_basename
    receipt_path.write_bytes(capture.receipt_payload)
    receipt_path.chmod(0o600)
    summary_path.write_bytes(capacity._canonical(capture.observation))
    summary_path.chmod(0o600)
    return receipt_path, summary_path


def _r5e_historical_failed_capture(
) -> capacity.DynamicSuccessorCapacityCapture:
    return _capture(
        governing_commit=(
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        ),
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=LIVE_USAGE_BYTES,
        ),
        now_utc=CAPTURED_AT,
    )


def _write_r5e_historical_pair(
    run: SimpleNamespace,
    captured: capacity.DynamicSuccessorCapacityCapture,
) -> tuple[Path, Path]:
    return _write_fixed_capture_pair(
        run,
        captured,
        restricted_basename=(
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        summary_basename=(
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
    )


def _write_r5e_post_cleanup_pair(
    run: SimpleNamespace,
    captured: capacity.DynamicSuccessorCapacityCapture,
) -> tuple[Path, Path]:
    return _write_fixed_capture_pair(
        run,
        captured,
        restricted_basename=(
            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
        ),
        summary_basename=(
            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
        ),
    )


def _historical_event(
    captured: capacity.DynamicSuccessorCapacityCapture,
) -> sequential.HistoricalCapacityEvent:
    summary_payload = capacity._canonical(captured.observation)
    return sequential.HistoricalCapacityEvent(
        capture=captured,
        authority={
            "status": captured.observation["status"],
            "receipt_basename": (
                capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
            ),
            "receipt_bytes": len(captured.receipt_payload),
            "receipt_sha256": hashlib.sha256(
                captured.receipt_payload
            ).hexdigest(),
            "summary_basename": (
                capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
            ),
            "summary_bytes": len(summary_payload),
            "summary_sha256": hashlib.sha256(summary_payload).hexdigest(),
        },
    )


def _production_role_run(root: Path) -> SimpleNamespace:
    run = _synthetic_full_run(root)
    run.requirements = SimpleNamespace(
        batch_count=19,
        contract_id=core.EXPECTED_FULL_CONTRACT_ID,
    )
    return run


def _completed_retirement_event(
    historical: sequential.HistoricalCapacityEvent,
) -> dict[str, Any]:
    return {
        "status": sequential.RETIREMENT_EVENT_STATUS,
        "receipt_sha256": sequential.RETIREMENT_EVENT_RECEIPT_SHA256,
        "pre_cleanup_capacity_authority": dict(historical.authority),
        "retained_state_authority": {
            "retained_files": 121_220,
            "retained_bytes": 18_131_756_871,
            "r4_attempt_immutable": True,
        },
    }


def _no_effect_dependencies(
    *,
    capacity_probe: Callable[[], Any] | None = None,
    test_only_synthetic_full_scope: bool = True,
) -> tuple[sequential.FullDependencies, dict[str, mock.Mock]]:
    effect_names = (
        "prior_batch_validator",
        "download",
        "dicom",
        "echoprime",
        "preserve",
        "retire",
        "finalize_batch",
        "cross_batch_finalize",
        "token_provider_factory",
        "transport_factory",
        "digest_provider_factory",
    )
    effects = {
        name: mock.Mock(side_effect=AssertionError(f"{name} effect reached"))
        for name in effect_names
    }
    effects["environment_validator"] = mock.Mock(
        return_value={"status": "PASS"}
    )
    dependencies = sequential.FullDependencies(
        **{name: effects[name] for name in effect_names},
        environment_validator=effects["environment_validator"],
        capacity_probe=capacity_probe,
        test_only_synthetic_full_scope=test_only_synthetic_full_scope,
    )
    return dependencies, effects


@contextmanager
def _synthetic_preflight_boundaries(run: SimpleNamespace) -> Any:
    aggregate = {
        "selected_studies": 4_530,
        "selected_subjects": 4_530,
        "normalized_source_objects": 335_984,
        "selected_source_bytes": 1_216_569_133_322,
        "batch_count": 19,
    }
    with (
        mock.patch.object(
            sequential,
            "validate_installation",
            return_value={"governing_commit": GOVERNING_COMMIT},
        ),
        mock.patch.object(sequential, "build_full_run", return_value=run),
        mock.patch.object(
            sequential,
            "_validate_completed_canary_evidence",
            return_value="6" * 64,
        ),
        mock.patch.object(
            sequential,
            "_extraction_cache_inventory",
            return_value=sequential.ExtractionCacheInventory(
                active=0, preserved_terminal_failed=2
            ),
        ),
        mock.patch.object(core, "aggregate_batch_plan", return_value=aggregate),
    ):
        yield


def test_historical_exact_allocation_and_schema_fingerprints_remain_frozen() -> None:
    payload = _historical_native()
    assert len(payload) == HISTORICAL_NATIVE_FIXTURE_BYTES
    assert hashlib.sha256(payload).hexdigest() == HISTORICAL_NATIVE_FIXTURE_SHA256
    rows = capacity._parse_native_quota(payload)
    assert rows["research"]["quota_kib"] == 2_044_723_200
    assert rows["backed"]["quota_kib"] == 52_428_800
    assert rows["research"]["file_quota"] == 33_554_432
    assert rows["backed"]["file_quota"] == 1_638_400
    assert capacity.SCHEMA_VERSION == 2
    assert capacity.RECEIPT_TYPE == "lvef_c3_post_reallocation_capacity_receipt_v2"
    assert capacity.AGGREGATE_TYPE == "lvef_c3_post_reallocation_capacity_summary_v2"
    assert _key_set_sha256(capacity.RECEIPT_KEYS) == (
        HISTORICAL_RECEIPT_KEY_SET_SHA256
    )
    assert _key_set_sha256(capacity.AGGREGATE_KEYS) == (
        HISTORICAL_AGGREGATE_KEY_SET_SHA256
    )
    assert _key_set_sha256(capacity.FULL_HEADROOM_KEYS) == (
        HISTORICAL_FULL_HEADROOM_KEY_SET_SHA256
    )


def test_historical_parser_still_rejects_every_allocation_change() -> None:
    mutations = (
        (b"2044723200", b"2044723201"),
        (b"52428800", b"52428801"),
        (b"33554432", b"33554433"),
        (b"1638400", b"1638401"),
    )
    for old, new in mutations:
        payload = _historical_native().replace(old, new, 1)
        _expect_code(
            "NATIVE_QUOTA_ALLOCATION_UNEXPECTED",
            lambda payload=payload: capacity._parse_native_quota(payload),
        )


def test_dynamic_api_is_additive_and_does_not_add_a_historical_escape_hatch() -> None:
    assert hasattr(capacity, "probe_dynamic_successor_capacity_observation")
    assert hasattr(capacity, "validate_dynamic_successor_capacity_observation")
    assert hasattr(capacity, "validate_dynamic_successor_capacity_receipt")
    assert hasattr(capacity, "publish_dynamic_successor_capacity_capture")
    source = inspect.getsource(capacity._parse_native_quota)
    assert "allow_any_quota" not in source
    assert "dynamic" not in inspect.signature(capacity._parse_native_quota).parameters


def test_legacy_r3e_claim_v1_still_passes_the_frozen_shared_key_contract() -> None:
    execution_commit = "6" * 40
    attempt_id = "lvef_c3_full_legacy_authority"
    plan = {"authority": {"git_commit": execution_commit}}
    plan_sha256 = "1" * 64
    runtime_authority = {"environment_receipt_sha256": "2" * 64}
    launch_authority_sha256 = "3" * 64
    authority = SimpleNamespace(
        execution_commit=execution_commit,
        attempt_id=attempt_id,
    )
    requirements = SimpleNamespace(batch_count=19)
    claim = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_submission_claim_v1",
        "status": "PREPARED_TWO_SUBMISSION_FULL_RECONSTRUCTION",
        "governing_commit": execution_commit,
        "attempt_id": attempt_id,
        "batch_plan_sha256": plan_sha256,
        "plan_authority_sha256": core.canonical_json_sha256(
            plan["authority"]
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            runtime_authority
        ),
        "launch_authority_sha256": launch_authority_sha256,
        "capacity_receipt_sha256": "4" * 64,
        "qsub_environment_sha256": "5" * 64,
        "maximum_qsub_submissions": 2,
        "array_tasks": 19,
        "array_max_concurrency": 1,
        "automatic_resubmission": False,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "raw_dicom_deletion_authorized": False,
        "bucket_listing_requests_before_claim": 0,
        "cloud_requests_before_claim": 0,
        "qsub_submissions_before_claim": 0,
        "dicom_body_reads_before_claim": 0,
        "gpu_executions_before_claim": 0,
        "model_fitting_before_claim": 0,
        "prediction_generation_before_claim": 0,
        "confirmatory_performance_access_before_claim": 0,
    }
    assert set(claim) == sequential.FULL_SUBMISSION_CLAIM_KEYS
    assert sequential.FRESH_FULL_SUBMISSION_CLAIM_V2_KEYS == frozenset(
        {*sequential.FULL_SUBMISSION_CLAIM_KEYS,
         "dynamic_capacity_receipt_sha256"}
    )
    replay._validate_submission_claim(
        claim,
        plan=plan,
        plan_sha256=plan_sha256,
        requirements=requirements,
        authority=authority,
        expected_runtime_authority=runtime_authority,
        launch_authority_sha256=launch_authority_sha256,
    )
    changed = {**claim, "dynamic_capacity_receipt_sha256": "7" * 64}
    _expect_code(
        "REPLAY_BATCH_MEMBERSHIP_INVALID",
        lambda: replay._validate_submission_claim(
            changed,
            plan=plan,
            plan_sha256=plan_sha256,
            requirements=requirements,
            authority=authority,
            expected_runtime_authority=runtime_authority,
            launch_authority_sha256=launch_authority_sha256,
        ),
    )


def test_dynamic_old_larger_and_representative_1000gb_statuses_are_exact() -> None:
    pending = _capture(native_payload=_historical_native())
    pending_value = capacity.validate_dynamic_successor_capacity_observation(
        pending.observation,
        expected_governing_commit=GOVERNING_COMMIT,
        expected_receipt_payload=pending.receipt_payload,
        now_utc=CAPTURED_AT,
    )
    assert pending_value["status"] == (
        "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
    )
    assert pending_value["storage_allocation_visible"] is False

    for regressed in (
        _dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES - 1024
        ),
        _dynamic_native(research_file_quota=33_554_431),
    ):
        _expect_code(
            "DYNAMIC_CAPACITY_ALLOCATION_REGRESSION",
            lambda regressed=regressed: _capture(native_payload=regressed),
        )

    arbitrary_increment = 200_000_000_000
    arbitrary_quota = HISTORICAL_RESEARCH_QUOTA_BYTES + arbitrary_increment
    accepted = _capture(
        native_payload=_dynamic_native(research_quota_bytes=arbitrary_quota)
    )
    assert accepted.observation["status"] == (
        "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
    )
    assert accepted.observation["live_research_quota_bytes"] == arbitrary_quota

    representative = _capture()
    value = capacity.validate_dynamic_successor_capacity_observation(
        representative.observation,
        expected_governing_commit=GOVERNING_COMMIT,
        expected_receipt_payload=representative.receipt_payload,
        now_utc=CAPTURED_AT,
    )
    expected_peak = LIVE_USAGE_BYTES + capacity.DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    expected_quota = (
        HISTORICAL_RESEARCH_QUOTA_BYTES + REPRESENTATIVE_INCREMENT_BYTES
    )
    assert value["status"] == "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
    assert value["storage_allocation_visible"] is True
    assert value["live_research_quota_bytes"] == expected_quota
    assert value["live_research_usage_bytes"] == LIVE_USAGE_BYTES
    assert value["projected_fresh_successor_peak_bytes"] == expected_peak
    assert value["quota_slack_after_peak_bytes"] == expected_quota - expected_peak
    assert value["physical_slack_after_peak_bytes"] == (
        PASS_PHYSICAL_AVAILABLE_BYTES
        - capacity.DYNAMIC_SUCCESSOR_INCREMENT_BYTES
    )
    assert value["quota_margin_beyond_reserve_bytes"] == (
        expected_quota - expected_peak - 200_000_000_000
    )
    assert value["physical_margin_beyond_reserve_bytes"] == (
        PASS_PHYSICAL_AVAILABLE_BYTES
        - capacity.DYNAMIC_SUCCESSOR_INCREMENT_BYTES
        - 200_000_000_000
    )
    assert value["remaining_file_slots"] == 33_554_432 - 501_481
    assert value["file_slot_margin_after_demand"] == (
        33_554_432 - 501_481 - 3_500_000
    )
    assert value["minimum_additional_quota_bytes"] == 0
    assert value["minimum_additional_physical_bytes"] == 0
    assert value["minimum_additional_file_slots"] == 0
    assert set(value) == capacity.DYNAMIC_SUCCESSOR_OBSERVATION_KEYS
    assert set(representative.receipt) == capacity.DYNAMIC_SUCCESSOR_RECEIPT_KEYS
    assert value["restricted_receipt_size_bytes"] == len(
        representative.receipt_payload
    )
    assert value["restricted_receipt_sha256"] == hashlib.sha256(
        representative.receipt_payload
    ).hexdigest()
    assert representative.receipt["observation_authority_sha256"] == value[
        "observation_authority_sha256"
    ]
    assert representative.receipt["observation_authority"] == {
        key: value[key]
        for key in capacity.DYNAMIC_SUCCESSOR_OBSERVATION_HASHED_KEYS
    }
    for key in (
        "cloud_requests",
        "qsub_submissions",
        "dicom_body_reads",
        "npz_body_reads",
        "gpu_executions",
        "echoprime_executions",
        "embedding_generations",
        "model_fitting",
        "prediction_generation",
        "confirmatory_performance_accesses",
        "files_moved",
        "files_deleted",
        "writes_performed",
    ):
        assert value[key] == 0


def test_dynamic_physical_and_file_shortfalls_are_blocked_with_exact_minima() -> None:
    physical_required = (
        capacity.DYNAMIC_SUCCESSOR_INCREMENT_BYTES
        + capacity.DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
    )
    physical = _capture(research_available=physical_required - 1).observation
    assert physical["status"] == "BLOCKED_ADDITIONAL_STORAGE_REQUIRED"
    assert physical["minimum_additional_physical_bytes"] == 1
    assert physical["physical_margin_beyond_reserve_bytes"] == -1
    assert physical["underlying_filesystem_expansion_appears_necessary"] is True

    file_quota = 33_554_432
    files_used = file_quota - capacity.DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS + 1
    files = _capture(
        native_payload=_dynamic_native(
            research_file_quota=file_quota,
            research_files_used=files_used,
        )
    ).observation
    assert files["status"] == "BLOCKED_ADDITIONAL_STORAGE_REQUIRED"
    assert files["file_slot_margin_after_demand"] == -1
    assert files["minimum_additional_file_slots"] == 1


def test_dynamic_native_display_mount_and_backed_authorities_fail_closed() -> None:
    invalid_native_payloads = (
        _dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES + 1024,
        ),
        _dynamic_native(research_file_quota=500_000, research_files_used=500_001),
        _dynamic_native() + _dynamic_native().splitlines()[1] + b"\n",
        _dynamic_native().replace(
            b"rprojectnb_mimicecho root FILESET",
            b"rprojectnb_mimicecho wrong FILESET",
        ),
        _dynamic_native().replace(
            b"rprojectnb_mimicecho root FILESET",
            b"wrong_project root FILESET",
        ),
    )
    for payload in invalid_native_payloads:
        _expect_code(
            "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID",
            lambda payload=payload: _capture(native_payload=payload),
        )

    for changed_backed in (
        _dynamic_native(backed_quota_bytes=BACKED_QUOTA_BYTES + 1024),
        _dynamic_native(backed_file_quota=1_638_401),
    ):
        _expect_code(
            "DYNAMIC_CAPACITY_BACKED_TIER_CHANGED",
            lambda changed_backed=changed_backed: _capture(
                native_payload=changed_backed
            ),
        )
    _expect_code(
        "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID",
        lambda: _capture(same_mount=True),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID",
        lambda: _capture(bind_research=True),
    )

    contradictory = (
        ROOT
        / "tests"
        / "fixtures"
        / "phase1ef"
        / "pquota_display_observed_sanitized.txt"
    ).read_text().replace("PROJECT_PLACEHOLDER", "mimicecho").encode("utf-8")
    _expect_code(
        "DYNAMIC_CAPACITY_DISPLAY_CONTRADICTION",
        lambda: _capture(pquota_returncode=0, pquota_stdout=contradictory),
    )
    unavailable = _capture(pquota_returncode=1, pquota_stdout=b"").observation
    assert unavailable["pquota_display_crosscheck"] == (
        capacity.DISPLAY_CROSSCHECK_UNAVAILABLE
    )
    assert unavailable["status"] == "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"


def test_required_mount_commands_and_native_read_use_distinct_failure_roles() -> None:
    for failed_command in ("findmnt", "df"):
        with tempfile.TemporaryDirectory() as temporary:
            authority = _probe_fixture(Path(temporary).resolve())
            normal_runner = _process_runner(authority)

            def failing_runner(
                argv: list[str], **kwargs: Any
            ) -> subprocess.CompletedProcess[bytes]:
                if Path(argv[0]).name == failed_command:
                    return subprocess.CompletedProcess(
                        argv, 1, b"", b"synthetic-required-command-failure"
                    )
                return normal_runner(argv, **kwargs)

            _expect_code(
                "DYNAMIC_CAPACITY_MOUNT_AUTHORITY_INVALID",
                lambda: capacity.probe_dynamic_successor_capacity_observation(
                    governing_commit=GOVERNING_COMMIT,
                    active_extraction_caches=0,
                    preserved_terminal_failed_extraction_caches=2,
                    successor_attempt_root_absent=True,
                    successor_claim_absent=True,
                    authority=authority,
                    process_runner=failing_runner,
                    now_utc=CAPTURED_AT,
                ),
            )

    with tempfile.TemporaryDirectory() as temporary:
        authority = _probe_fixture(Path(temporary).resolve())
        normal_read = capacity._read_regular

        def failing_native_read(path: Path, **kwargs: Any) -> bytes:
            if path == authority.native_quota_path:
                raise OSError("synthetic native authority read failure")
            return normal_read(path, **kwargs)

        with mock.patch.object(
            capacity, "_read_regular", side_effect=failing_native_read
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_NATIVE_QUOTA_INVALID",
                lambda: capacity.probe_dynamic_successor_capacity_observation(
                    governing_commit=GOVERNING_COMMIT,
                    active_extraction_caches=0,
                    preserved_terminal_failed_extraction_caches=2,
                    successor_attempt_root_absent=True,
                    successor_claim_absent=True,
                    authority=authority,
                    process_runner=_process_runner(authority),
                    now_utc=CAPTURED_AT,
                ),
            )


def test_dynamic_command_roles_share_exact_executable_authority() -> None:
    base = _capture().receipt
    mutations = (
        ("backed_findmnt", "executable_sha256", "0" * 64),
        (
            "backed_findmnt",
            "executable_size_bytes",
            base["commands"]["backed_findmnt"]["executable_size_bytes"] + 1,
        ),
        ("backed_df", "executable_sha256", "0" * 64),
        (
            "backed_df",
            "executable_size_bytes",
            base["commands"]["backed_df"]["executable_size_bytes"] + 1,
        ),
    )
    for role, field, changed_value in mutations:
        changed = copy.deepcopy(base)
        changed["commands"][role][field] = changed_value
        changed = _rebind_dynamic_receipt(changed)
        _expect_code(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
            lambda changed=changed: (
                capacity.validate_dynamic_successor_capacity_receipt(
                    changed,
                    expected_governing_commit=GOVERNING_COMMIT,
                    now_utc=CAPTURED_AT,
                )
            ),
        )


def test_dynamic_pquota_state_machine_and_coordinated_relabel_fail_closed() -> None:
    produced = (
        (
            _capture(pquota_returncode=0).receipt,
            ("AVAILABLE", "AVAILABLE", 0, 0),
        ),
        (
            _capture(pquota_returncode=3).receipt,
            ("UNAVAILABLE_NONBLOCKING", "COMMAND_NONZERO_EXIT", 3, 0),
        ),
        (
            _capture(pquota_returncode=0, pquota_stderr=b"warning").receipt,
            ("UNAVAILABLE_NONBLOCKING", "COMMAND_STDERR_PRESENT", 0, 7),
        ),
        (
            _capture(pquota_returncode=0, pquota_stdout=b"x" * 2_000_001).receipt,
            ("UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_OVERSIZED", 0, 0),
        ),
        (
            _capture(pquota_returncode=0, pquota_stdout=b"\xff").receipt,
            ("UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_NOT_UTF8", 0, 0),
        ),
    )
    for receipt, expected in produced:
        command = receipt["commands"]["pquota"]
        assert (
            command["availability_status"],
            command["availability_reason"],
            command["exit_status"],
            command["stderr_bytes"],
        ) == expected
        capacity.validate_dynamic_successor_capacity_receipt(
            receipt,
            expected_governing_commit=GOVERNING_COMMIT,
            now_utc=CAPTURED_AT,
        )

    base = _capture().receipt
    invalid_states = (
        ("AVAILABLE", "AVAILABLE", 1, b""),
        ("AVAILABLE", "AVAILABLE", 0, b"warning"),
        ("AVAILABLE", "COMMAND_NONZERO_EXIT", 0, b""),
        ("UNAVAILABLE_NONBLOCKING", "AVAILABLE", 0, b""),
        ("UNAVAILABLE_NONBLOCKING", "ARBITRARY_REASON", 1, b""),
        ("UNAVAILABLE_NONBLOCKING", "COMMAND_NONZERO_EXIT", 0, b""),
        ("UNAVAILABLE_NONBLOCKING", "COMMAND_STDERR_PRESENT", 0, b""),
        ("UNAVAILABLE_NONBLOCKING", "COMMAND_STDERR_PRESENT", 1, b"warning"),
        ("UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_OVERSIZED", 0, b"warning"),
        ("UNAVAILABLE_NONBLOCKING", "COMMAND_OUTPUT_NOT_UTF8", 0, b"warning"),
    )
    for availability, reason, exit_status, stderr in invalid_states:
        changed = copy.deepcopy(base)
        command = changed["commands"]["pquota"]
        command["availability_status"] = availability
        command["availability_reason"] = reason
        command["exit_status"] = exit_status
        command["stderr_bytes"] = len(stderr)
        command["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
        changed = _rebind_dynamic_receipt(changed)
        _expect_code(
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
            lambda changed=changed: (
                capacity.validate_dynamic_successor_capacity_receipt(
                    changed,
                    expected_governing_commit=GOVERNING_COMMIT,
                    now_utc=CAPTURED_AT,
                )
            ),
        )

    contradictory_stdout = (
        ROOT
        / "tests"
        / "fixtures"
        / "phase1ef"
        / "pquota_display_observed_sanitized.txt"
    ).read_text().replace("PROJECT_PLACEHOLDER", "mimicecho")
    coordinated = copy.deepcopy(base)
    command = coordinated["commands"]["pquota"]
    command["availability_status"] = "UNAVAILABLE_NONBLOCKING"
    command["availability_reason"] = "COORDINATED_RELABEL"
    command["exit_status"] = 0
    command["stdout_text"] = contradictory_stdout
    command["stdout_bytes"] = len(contradictory_stdout.encode())
    command["stdout_sha256"] = hashlib.sha256(
        contradictory_stdout.encode()
    ).hexdigest()
    command["stderr_bytes"] = 0
    command["stderr_sha256"] = hashlib.sha256(b"").hexdigest()
    coordinated = _rebind_dynamic_receipt(coordinated)
    _expect_one_of_codes(
        {
            "DYNAMIC_CAPACITY_DISPLAY_CONTRADICTION",
            "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        },
        lambda: capacity.validate_dynamic_successor_capacity_receipt(
            coordinated,
            expected_governing_commit=GOVERNING_COMMIT,
            now_utc=CAPTURED_AT,
        ),
    )


def test_dynamic_cache_and_successor_collisions_fail_before_effects() -> None:
    for active, preserved in ((1, 2), (0, 1), (0, 3)):
        _expect_code(
            "DYNAMIC_CAPACITY_CACHE_INVENTORY_INVALID",
            lambda active=active, preserved=preserved: _capture(
                active_caches=active,
                preserved_failed_caches=preserved,
            ),
        )

    for root_absent, claim_absent in ((False, True), (True, False)):
        with tempfile.TemporaryDirectory() as temporary:
            authority = _probe_fixture(Path(temporary))
            calls: list[tuple[str, ...]] = []

            def forbidden(argv: list[str], **_kwargs: Any) -> Any:
                calls.append(tuple(str(item) for item in argv))
                raise AssertionError("capacity subprocess crossed collision gate")

            _expect_code(
                "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
                lambda root_absent=root_absent, claim_absent=claim_absent: (
                    capacity.probe_dynamic_successor_capacity_observation(
                        governing_commit=GOVERNING_COMMIT,
                        active_extraction_caches=0,
                        preserved_terminal_failed_extraction_caches=2,
                        successor_attempt_root_absent=root_absent,
                        successor_claim_absent=claim_absent,
                        authority=authority,
                        process_runner=forbidden,
                        now_utc=CAPTURED_AT,
                    )
                ),
            )
            assert calls == []


def test_dynamic_publisher_checks_both_fixed_targets_before_validation_or_write() -> None:
    capture = _capture()
    for occupied_role in ("restricted", "summary"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            restricted = (
                root / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
            )
            summary = (
                root / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
            )
            occupied = restricted if occupied_role == "restricted" else summary
            absent = summary if occupied_role == "restricted" else restricted
            occupied.write_bytes(b"preserved-collision-sentinel")
            before = occupied.read_bytes()

            with (
                mock.patch.object(
                    capacity,
                    "validate_dynamic_successor_capacity_receipt",
                    side_effect=AssertionError("validated after collision"),
                ) as receipt_validator,
                mock.patch.object(
                    capacity,
                    "validate_dynamic_successor_capacity_observation",
                    side_effect=AssertionError("validated after collision"),
                ) as observation_validator,
                mock.patch.object(
                    capacity,
                    "_write_dynamic_owner_private_pair",
                    side_effect=AssertionError("wrote after collision"),
                ) as writer,
            ):
                _expect_code(
                    "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
                    lambda: capacity.publish_dynamic_successor_capacity_capture(
                        capture,
                        restricted_receipt_path=restricted,
                        aggregate_summary_path=summary,
                        safe_export_policy_path=root / "unused-policy.yaml",
                        now_utc=CAPTURED_AT,
                    ),
                )
            receipt_validator.assert_not_called()
            observation_validator.assert_not_called()
            writer.assert_not_called()
            assert occupied.read_bytes() == before
            assert not os.path.lexists(absent)


def test_dynamic_publisher_nofollow_topology_and_reopen_tamper_are_closed() -> None:
    capture = _capture()

    for occupied_role in ("restricted", "summary"):
        for occupied_kind in ("broken_symlink", "fifo"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                restricted = (
                    root
                    / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
                )
                summary = (
                    root
                    / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
                )
                occupied = (
                    restricted if occupied_role == "restricted" else summary
                )
                absent = summary if occupied_role == "restricted" else restricted
                if occupied_kind == "broken_symlink":
                    occupied.symlink_to(root / "missing-target")
                else:
                    os.mkfifo(occupied, mode=0o600)
                _expect_code(
                    "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
                    lambda: capacity.publish_dynamic_successor_capacity_capture(
                        capture,
                        restricted_receipt_path=restricted,
                        aggregate_summary_path=summary,
                        safe_export_policy_path=SAFE_EXPORT_POLICY,
                        now_utc=CAPTURED_AT,
                    ),
                )
                assert os.path.lexists(occupied)
                assert not os.path.lexists(absent)

    for parent_kind in ("symlink", "fifo", "wrong_mode"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output_parent = root / "output-parent"
            if parent_kind == "symlink":
                real_parent = root / "real-parent"
                real_parent.mkdir(mode=0o700)
                output_parent.symlink_to(real_parent, target_is_directory=True)
            elif parent_kind == "fifo":
                os.mkfifo(output_parent, mode=0o600)
            else:
                output_parent.mkdir(mode=0o755)
            restricted = (
                output_parent
                / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
            )
            summary = (
                output_parent
                / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
            )
            _expect_one_of_codes(
                {
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                    "OUTPUT_SYMLINK_ANCESTOR",
                },
                lambda: capacity.publish_dynamic_successor_capacity_capture(
                    capture,
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    safe_export_policy_path=SAFE_EXPORT_POLICY,
                    now_utc=CAPTURED_AT,
                ),
            )
            assert not os.path.lexists(restricted)
            assert not os.path.lexists(summary)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        output_parent = root / "initially-absent-private-parent"
        restricted = (
            output_parent
            / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        )
        summary = (
            output_parent
            / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )
        result = capacity.publish_dynamic_successor_capacity_capture(
            capture,
            restricted_receipt_path=restricted,
            aggregate_summary_path=summary,
            safe_export_policy_path=SAFE_EXPORT_POLICY,
            now_utc=CAPTURED_AT,
        )
        assert stat.S_IMODE(output_parent.stat().st_mode) == 0o700
        assert restricted.read_bytes() == capture.receipt_payload
        assert summary.read_bytes() == capacity._canonical(capture.observation)
        assert result["evidence_files_written"] == 2

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        restricted = (
            root / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        )
        summary = (
            root / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )
        original_write = capacity._write_dynamic_owner_private_pair

        def tampering_write(
            receipt_path: Path,
            receipt_payload: bytes,
            summary_path: Path,
            summary_payload: bytes,
        ) -> tuple[bytes, bytes]:
            original_write(
                receipt_path,
                receipt_payload,
                summary_path,
                summary_payload,
            )
            prior = receipt_path.read_bytes()
            receipt_path.write_bytes(b"!" + prior[1:])
            return capacity._read_dynamic_owner_private_pair(
                receipt_path, summary_path
            )

        with mock.patch.object(
            capacity,
            "_write_dynamic_owner_private_pair",
            side_effect=tampering_write,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity.publish_dynamic_successor_capacity_capture(
                    capture,
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    safe_export_policy_path=SAFE_EXPORT_POLICY,
                    now_utc=CAPTURED_AT,
                ),
            )


def test_dynamic_publisher_o_excl_competitor_is_sanitized_and_preserved() -> None:
    capture = _capture()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        restricted = (
            root / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        )
        summary = (
            root / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )
        original_open = os.open
        sentinel = b"competing-owner-evidence"
        raced = False

        def competing_open(
            path: object, flags: int, mode: int = 0o777, **kwargs: Any
        ) -> int:
            nonlocal raced
            if (
                Path(path).name == restricted.name
                and flags & os.O_EXCL
                and not raced
            ):
                raced = True
                competitor = original_open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    **kwargs,
                )
                try:
                    os.write(competitor, sentinel)
                finally:
                    os.close(competitor)
            return original_open(path, flags, mode, **kwargs)

        with mock.patch.object(capacity.os, "open", side_effect=competing_open):
            _expect_code(
                "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
                lambda: capacity.publish_dynamic_successor_capacity_capture(
                    capture,
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    safe_export_policy_path=SAFE_EXPORT_POLICY,
                    now_utc=CAPTURED_AT,
                ),
            )
        assert raced is True
        assert restricted.read_bytes() == sentinel
        assert not summary.exists()


def test_dynamic_pair_rejects_hardlinks_fifos_and_parent_replacement() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))

    for target_role in ("restricted", "summary"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent = root / "owner_private"
            parent.mkdir(mode=0o700)
            run = SimpleNamespace(production_root=root)
            restricted, summary = _write_fixed_capture_pair(run, capture)
            target = restricted if target_role == "restricted" else summary
            os.link(target, parent / f"{target_role}.hardlink")
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent = root / "owner_private"
            parent.mkdir(mode=0o700)
            run = SimpleNamespace(production_root=root)
            restricted, summary = _write_fixed_capture_pair(run, capture)
            target = restricted if target_role == "restricted" else summary
            target.unlink()
            os.mkfifo(target, mode=0o600)
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "owner_private"
        parent.mkdir(mode=0o700)
        run = SimpleNamespace(production_root=root)
        restricted, summary = _write_fixed_capture_pair(run, capture)
        original_read = os.read
        raced = False

        def replace_summary_after_receipt_read(
            descriptor: int, maximum: int
        ) -> bytes:
            nonlocal raced
            block = original_read(descriptor, maximum)
            if not raced:
                prior = summary.read_bytes()
                summary.unlink()
                summary.write_bytes(prior)
                summary.chmod(0o600)
                raced = True
            return block

        with mock.patch.object(
            capacity.os,
            "read",
            side_effect=replace_summary_after_receipt_read,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )
        assert raced is True

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "owner_private"
        parent.mkdir(mode=0o700)
        run = SimpleNamespace(production_root=root)
        restricted, summary = _write_fixed_capture_pair(run, capture)
        original_pread = os.pread
        pread_calls = 0
        raced = False

        def overwrite_receipt_during_summary_second_read(
            descriptor: int, maximum: int, offset: int
        ) -> bytes:
            nonlocal pread_calls, raced
            block = original_pread(descriptor, maximum, offset)
            pread_calls += 1
            # Both synthetic artifacts fit in one block. The first pread is
            # the receipt's descriptor-bound second read and its per-leaf
            # final check completes before the summary's pread begins.
            if pread_calls == 2:
                prior = restricted.read_bytes()
                with restricted.open("r+b") as handle:
                    handle.write(b"!" + prior[1:])
                    handle.flush()
                    os.fsync(handle.fileno())
                raced = True
            return block

        with mock.patch.object(
            capacity.os,
            "pread",
            side_effect=overwrite_receipt_during_summary_second_read,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )
        assert pread_calls == 2
        assert raced is True

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "owner_private"
        parent.mkdir(mode=0o700)
        run = SimpleNamespace(production_root=root)
        restricted, summary = _write_fixed_capture_pair(run, capture)
        original_read = os.read
        raced = False

        def overwrite_receipt_after_first_read(
            descriptor: int, maximum: int
        ) -> bytes:
            nonlocal raced
            block = original_read(descriptor, maximum)
            if not raced:
                prior = restricted.read_bytes()
                with restricted.open("r+b") as handle:
                    handle.write(b"!" + prior[1:])
                    handle.flush()
                    os.fsync(handle.fileno())
                raced = True
            return block

        with mock.patch.object(
            capacity.os,
            "read",
            side_effect=overwrite_receipt_after_first_read,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )
        assert raced is True

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "owner_private"
        parent.mkdir(mode=0o700)
        displaced = root / "owner_private.displaced"
        restricted = (
            parent / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        )
        summary = (
            parent / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )
        original_write = capacity._write_dynamic_owner_private_new_at
        writes = 0

        def replace_parent_after_first_write(*args: Any, **kwargs: Any) -> Any:
            nonlocal writes
            identity = original_write(*args, **kwargs)
            writes += 1
            if writes == 1:
                parent.rename(displaced)
                parent.mkdir(mode=0o700)
            return identity

        with mock.patch.object(
            capacity,
            "_write_dynamic_owner_private_new_at",
            side_effect=replace_parent_after_first_write,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity._write_dynamic_owner_private_pair(
                    restricted,
                    capture.receipt_payload,
                    summary,
                    capacity._canonical(capture.observation),
                ),
            )
        assert writes == 1
        assert not os.path.lexists(summary)
        assert not os.path.lexists(
            displaced / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "owner_private"
        parent.mkdir(mode=0o700)
        run = SimpleNamespace(production_root=root)
        restricted, summary = _write_fixed_capture_pair(run, capture)
        displaced = root / "owner_private.displaced"
        original_read = os.read
        raced = False

        def replace_parent_after_first_read(
            descriptor: int, maximum: int
        ) -> bytes:
            nonlocal raced
            block = original_read(descriptor, maximum)
            if not raced:
                parent.rename(displaced)
                parent.mkdir(mode=0o700)
                raced = True
            return block

        with mock.patch.object(
            capacity.os,
            "read",
            side_effect=replace_parent_after_first_read,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
                lambda: capacity.load_dynamic_successor_capacity_capture(
                    restricted_receipt_path=restricted,
                    aggregate_summary_path=summary,
                    expected_governing_commit=GOVERNING_COMMIT,
                ),
            )
        assert raced is True


def test_dynamic_receipt_hash_freshness_and_role_are_closed() -> None:
    capture = _capture()
    capacity.validate_dynamic_successor_capacity_receipt(
        capture.receipt,
        expected_governing_commit=GOVERNING_COMMIT,
        now_utc=CAPTURED_AT,
    )
    capacity.validate_dynamic_successor_capacity_observation(
        capture.observation,
        expected_governing_commit=GOVERNING_COMMIT,
        expected_receipt_payload=capture.receipt_payload,
        now_utc=CAPTURED_AT,
    )

    missing = dict(capture.observation)
    missing.pop("observation_authority_sha256")
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_observation(
            missing, now_utc=CAPTURED_AT
        ),
    )
    changed = dict(capture.observation)
    changed["observation_authority_sha256"] = "0" * 64
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
        lambda: capacity.validate_dynamic_successor_capacity_observation(
            changed,
            expected_receipt_payload=capture.receipt_payload,
            now_utc=CAPTURED_AT,
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_STALE",
        lambda: capacity.validate_dynamic_successor_capacity_observation(
            capture.observation,
            expected_receipt_payload=capture.receipt_payload,
            now_utc=(
                CAPTURED_AT
                + timedelta(
                    seconds=capacity.DYNAMIC_SUCCESSOR_MAXIMUM_AGE_SECONDS + 1
                )
            ),
        ),
    )
    historical_role = {
        "schema_version": capacity.SCHEMA_VERSION,
        "artifact_type": capacity.RECEIPT_TYPE,
    }
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_observation(
            historical_role, now_utc=CAPTURED_AT
        ),
    )


def test_full_preflight_requires_the_exact_dynamic_receipt_payload() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    changed_payload = capture.receipt_payload + b"\n"
    changed_observation = dict(capture.observation)
    changed_observation["restricted_receipt_size_bytes"] = len(changed_payload)
    changed_observation["restricted_receipt_sha256"] = hashlib.sha256(
        changed_payload
    ).hexdigest()
    tampered = capacity.DynamicSuccessorCapacityCapture(
        receipt=capture.receipt,
        receipt_payload=changed_payload,
        observation=changed_observation,
    )
    capacity.validate_dynamic_successor_capacity_observation(
        changed_observation,
        expected_governing_commit=GOVERNING_COMMIT,
        now_utc=datetime.now(timezone.utc),
    )

    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        dependencies, effects = _no_effect_dependencies(
            capacity_probe=lambda: tampered
        )
        with (
            _synthetic_preflight_boundaries(run),
            mock.patch.object(
                sequential,
                "build_successor_capacity_authority",
                side_effect=AssertionError("derived from tampered receipt"),
            ) as derive,
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: sequential.preflight_full(dependencies=dependencies),
            )
        derive.assert_not_called()
        for name, effect in effects.items():
            if name != "environment_validator":
                effect.assert_not_called()
        assert not run.attempt_root.exists()
        assert not (
            run.attempt_root / "full_submission_claim.restricted.json"
        ).exists()


def test_public_dynamic_capture_injection_is_strictly_test_only() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    production_probe = mock.Mock(
        side_effect=AssertionError("unauthorized production probe invoked")
    )
    production_dependencies, production_effects = _no_effect_dependencies(
        capacity_probe=production_probe,
        test_only_synthetic_full_scope=False,
    )
    _expect_code(
        "FULL_SEQUENTIAL_SYNTHETIC_CAPACITY_NOT_AUTHORIZED",
        lambda: sequential.preflight_full(
            dependencies=production_dependencies
        ),
    )
    production_probe.assert_not_called()
    seal_dependencies, seal_effects = _no_effect_dependencies(
        test_only_synthetic_full_scope=False
    )
    _expect_code(
        "FULL_SEQUENTIAL_SYNTHETIC_CAPACITY_NOT_AUTHORIZED",
        lambda: sequential.run_dynamic_successor_capacity_seal(
            capture=capture, dependencies=seal_dependencies
        ),
    )
    for effect in (*production_effects.values(), *seal_effects.values()):
        effect.assert_not_called()

    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        forged_dependencies, forged_effects = _no_effect_dependencies(
            test_only_synthetic_full_scope=False
        )
        with _synthetic_preflight_boundaries(run):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: sequential.preflight_full(
                    dependencies=forged_dependencies,
                    _validated_dynamic_capture=capture,
                    _capture_reuse_token=(
                        sequential._DYNAMIC_CAPTURE_REUSE_TOKEN
                    ),
                ),
            )
        for name, effect in forged_effects.items():
            if name != "environment_validator":
                effect.assert_not_called()
        assert not run.attempt_root.exists()

    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        test_dependencies, test_effects = _no_effect_dependencies(
            capacity_probe=lambda: capture,
            test_only_synthetic_full_scope=True,
        )
        with _synthetic_preflight_boundaries(run):
            result = sequential.preflight_full(
                dependencies=test_dependencies
            )
        assert result["status"] == "PASS_FULL_C3_NO_BODY_PREFLIGHT"
        assert result["capacity"] == capture.observation
        for name, effect in test_effects.items():
            if name != "environment_validator":
                effect.assert_not_called()


def test_safe_export_profile_failure_precedes_probe_and_artifact_write() -> None:
    policy_text = SAFE_EXPORT_POLICY.read_text()
    anchor = "  lvef_c3_dynamic_successor_capacity_summary_json:\n"
    before_profile, profile_and_after = policy_text.split(anchor, 1)
    invalid_policies = (
        policy_text.replace(
            "lvef_c3_dynamic_successor_capacity_summary_json:",
            "lvef_c3_dynamic_successor_capacity_summary_json_mutated:",
            1,
        ),
        before_profile
        + anchor
        + profile_and_after.replace(
            "    max_bytes: 65536\n",
            '    max_bytes: "65536"\n',
            1,
        ),
        before_profile
        + anchor
        + profile_and_after.replace(
            "    kind: json\n",
            "    kind: json\n    unexpected_profile_key: true\n",
            1,
        ),
    )
    for bad_policy_text in invalid_policies:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            run = _synthetic_full_run(root)
            bad_policy = root / "bad-safe-export-policy.yaml"
            bad_policy.write_text(bad_policy_text)
            dependencies, effects = _no_effect_dependencies(
                test_only_synthetic_full_scope=False
            )
            forbidden_probe = mock.Mock(
                side_effect=AssertionError("capacity probe crossed policy gate")
            )
            forbidden_publish = mock.Mock(
                side_effect=AssertionError("publisher crossed policy gate")
            )
            with (
                _synthetic_preflight_boundaries(run),
                mock.patch.object(
                    sequential,
                    "DYNAMIC_CAPACITY_SAFE_EXPORT_POLICY_PATH",
                    bad_policy,
                ),
                mock.patch.object(
                    capacity,
                    "probe_dynamic_successor_capacity_observation",
                    forbidden_probe,
                ),
                mock.patch.object(
                    capacity,
                    "publish_dynamic_successor_capacity_capture",
                    forbidden_publish,
                ),
            ):
                _expect_code(
                    "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                    lambda: sequential.run_dynamic_successor_capacity_seal(
                        dependencies=dependencies
                    ),
                )
            forbidden_probe.assert_not_called()
            forbidden_publish.assert_not_called()
            for name, effect in effects.items():
                if name != "environment_validator":
                    effect.assert_not_called()
            evidence_root = run.production_root / "owner_private"
            assert not os.path.lexists(
                evidence_root
                / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
            )
            assert not os.path.lexists(
                evidence_root
                / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
            )


def test_successor_authority_and_future_claim_bind_exact_dynamic_receipt() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    successor = sequential.build_successor_capacity_authority(
        capture,
        sequential.ExtractionCacheInventory(
            active=0, preserved_terminal_failed=2
        ),
    )
    assert successor["source_capacity_authority_sha256"] == (
        core.canonical_json_sha256(capture.observation)
    )
    assert successor["source_dynamic_receipt_bytes"] == len(
        capture.receipt_payload
    )
    assert successor["source_dynamic_receipt_sha256"] == hashlib.sha256(
        capture.receipt_payload
    ).hexdigest()

    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        successor_payload = capacity._canonical(successor)
        future_claim = sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256=hashlib.sha256(
                successor_payload
            ).hexdigest(),
            dynamic_capacity_receipt_sha256=hashlib.sha256(
                capture.receipt_payload
            ).hexdigest(),
            capacity_evidence_role="R5B_HISTORICAL",
            capacity_gain_source="ALLOCATION",
            raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CLEANUP_SKIPPED"
            ),
            qsub_environment_sha256="9" * 64,
        )
        assert future_claim["dynamic_capacity_receipt_sha256"] == (
            successor["source_dynamic_receipt_sha256"]
        )
        assert not run.attempt_root.exists()


def test_claim_rejects_missing_mutated_stale_and_nonpass_fixed_pair_before_root() -> None:
    scenarios = (
        ("missing", "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"),
        ("mutated", "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID"),
        ("stale", "DYNAMIC_CAPACITY_RECEIPT_STALE"),
        ("nonpass", "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_NOT_PASS"),
    )
    for scenario, expected_code in scenarios:
        with tempfile.TemporaryDirectory() as temporary:
            run = _synthetic_full_run(Path(temporary).resolve())
            dependencies, _ = _no_effect_dependencies(
                test_only_synthetic_full_scope=False
            )
            if scenario == "mutated":
                capture = _capture(now_utc=datetime.now(timezone.utc))
                _, summary_path = _write_fixed_capture_pair(run, capture)
                payload = summary_path.read_bytes()
                summary_path.write_bytes(b"!" + payload[1:])
            elif scenario == "stale":
                stale_at = datetime.now(timezone.utc) - timedelta(
                    seconds=capacity.DYNAMIC_SUCCESSOR_MAXIMUM_AGE_SECONDS + 60
                )
                _write_fixed_capture_pair(
                    run, _capture(now_utc=stale_at)
                )
            elif scenario == "nonpass":
                _write_fixed_capture_pair(
                    run,
                    _capture(
                        native_payload=_dynamic_native(
                            research_quota_bytes=(
                                HISTORICAL_RESEARCH_QUOTA_BYTES
                            ),
                            research_usage_bytes=LIVE_USAGE_BYTES,
                        ),
                        now_utc=datetime.now(timezone.utc),
                    ),
                )

            forbidden_preflight = mock.Mock(
                side_effect=AssertionError("invalid sealed pair reached preflight")
            )
            forbidden_root = mock.Mock(
                side_effect=AssertionError("invalid sealed pair created root")
            )
            with (
                _synthetic_preflight_boundaries(run),
                mock.patch.object(
                    sequential, "resolve_dependencies", return_value=dependencies
                ),
                mock.patch.object(
                    sequential, "preflight_full", forbidden_preflight
                ),
                mock.patch.object(
                    sequential, "_ensure_private_directory", forbidden_root
                ),
                mock.patch.object(
                    sequential, "_write_private_json", forbidden_root
                ),
            ):
                _expect_code(
                    expected_code,
                    lambda: sequential.claim_submission(
                        qsub_environment_sha256="9" * 64
                    ),
                )
            forbidden_preflight.assert_not_called()
            forbidden_root.assert_not_called()
            assert not run.attempt_root.exists()
            assert not (
                run.attempt_root / "full_submission_claim.restricted.json"
            ).exists()


def test_fixed_loader_rejects_synthetic_authority_in_production_role() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        _write_fixed_capture_pair(run, capture)
        forbidden_probe = mock.Mock(
            side_effect=AssertionError("fixed loader repeated capacity probe")
        )
        with (
            mock.patch.object(
                capacity,
                "probe_dynamic_successor_capacity_observation",
                forbidden_probe,
            ),
        ):
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: sequential._load_fixed_dynamic_capacity_capture(run),
            )
        forbidden_probe.assert_not_called()
        assert not run.attempt_root.exists()


def test_r5e_r8_fixed_pair_is_append_only_and_allowed() -> None:
    expected_pair = (
        "r5e_r8_post_cleanup_dynamic_successor_capacity.restricted.json",
        "r5e_r8_post_cleanup_dynamic_successor_capacity.aggregate_safe.json",
    )
    assert capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME == (
        expected_pair[0]
    )
    assert capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME == (
        expected_pair[1]
    )
    assert expected_pair in capacity.DYNAMIC_SUCCESSOR_ALLOWED_EVIDENCE_BASENAME_PAIRS
    assert capacity.SEALED_EXECUTION_REPLAY is (
        capacity.DynamicSuccessorCapacityValidationContext.SEALED_EXECUTION_REPLAY
    )
    assert capacity.LIVE_PRECLAIM_ADMISSION is (
        capacity.DynamicSuccessorCapacityValidationContext.LIVE_PRECLAIM_ADMISSION
    )
    assert set(capacity.DynamicSuccessorCapacityValidationContext) == {
        capacity.LIVE_PRECLAIM_ADMISSION,
        capacity.SEALED_EXECUTION_REPLAY,
    }

    capture = _capture(now_utc=CAPTURED_AT)
    with tempfile.TemporaryDirectory() as temporary:
        owner = Path(temporary).resolve() / "owner_private"
        owner.mkdir(mode=0o700)
        prior_pair = (
            owner / capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            owner / capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        )
        for ordinal, path in enumerate(prior_pair):
            path.write_bytes(f"sealed-r5e-post-{ordinal}\n".encode())
            path.chmod(0o600)
        prior_payloads = tuple(path.read_bytes() for path in prior_pair)
        restricted = owner / expected_pair[0]
        summary = owner / expected_pair[1]
        result = capacity.publish_dynamic_successor_capacity_capture(
            capture,
            restricted_receipt_path=restricted,
            aggregate_summary_path=summary,
            safe_export_policy_path=SAFE_EXPORT_POLICY,
            now_utc=CAPTURED_AT,
        )
        assert result["restricted_receipt_basename"] == expected_pair[0]
        assert result["aggregate_summary_basename"] == expected_pair[1]
        assert tuple(path.read_bytes() for path in prior_pair) == prior_payloads


def test_r5e_r8_sealed_execution_replay_has_no_live_node_dependencies() -> None:
    historical = _r5e_historical_failed_capture()
    blocked_live_dependency = AssertionError(
        "sealed replay consulted current execution-node state"
    )
    with (
        mock.patch.object(
            capacity.os, "geteuid", side_effect=blocked_live_dependency
        ) as effective_uid,
        mock.patch.object(
            capacity.pwd, "getpwuid", side_effect=blocked_live_dependency
        ) as username,
        mock.patch.object(
            capacity.socket, "gethostname", side_effect=blocked_live_dependency
        ) as hostname,
        mock.patch.object(
            capacity,
            "_validate_current_canary_headroom_authority",
            side_effect=blocked_live_dependency,
        ) as live_authority,
        mock.patch.object(
            capacity, "_read_regular", side_effect=blocked_live_dependency
        ) as executable_read,
        mock.patch.object(
            capacity, "_path_identity", side_effect=blocked_live_dependency
        ) as path_identity,
        mock.patch.object(
            capacity,
            "_validate_pquota_restricted_mount_reconciliation",
            side_effect=blocked_live_dependency,
        ) as mount_reconciliation,
        mock.patch.object(
            capacity.subprocess, "run", side_effect=blocked_live_dependency
        ) as process,
    ):
        replayed = (
            capacity.validate_dynamic_successor_capacity_capture(
                historical,
                validation_context=capacity.SEALED_EXECUTION_REPLAY,
                expected_governing_commit=(
                    sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
                ),
                expected_captured_at_utc=historical.receipt[
                    "captured_at_utc"
                ],
            )
        )
    assert replayed.receipt_payload == historical.receipt_payload
    assert replayed.receipt == historical.receipt
    assert replayed.observation == historical.observation
    assert replayed.observation["status"] == (
        capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
    )
    for dependency in (
        effective_uid,
        username,
        hostname,
        live_authority,
        executable_read,
        path_identity,
        mount_reconciliation,
        process,
    ):
        dependency.assert_not_called()

    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: capacity.validate_production_dynamic_successor_capacity_capture(
            historical,
            expected_governing_commit=(
                sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
            ),
            now_utc=CAPTURED_AT,
        ),
    )


def test_r5e_r8_sealed_execution_replay_fails_on_bytes_commit_and_time() -> None:
    historical = _r5e_historical_failed_capture()
    captured_at = historical.receipt["captured_at_utc"]

    def validator(
        value: capacity.DynamicSuccessorCapacityCapture,
        *,
        expected_commit: str = (
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        ),
        expected_time: str = captured_at,
    ) -> capacity.DynamicSuccessorCapacityCapture:
        return capacity.validate_dynamic_successor_capacity_capture(
            value,
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            expected_governing_commit=expected_commit,
            expected_captured_at_utc=expected_time,
        )

    changed_receipt_bytes = dataclass_replace(
        historical,
        receipt_payload=historical.receipt_payload + b" ",
    )
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: validator(changed_receipt_bytes),
    )

    changed_summary = copy.deepcopy(historical.observation)
    changed_summary["observation_authority_sha256"] = "0" * 64
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_HASH_MISMATCH",
        lambda: validator(
            dataclass_replace(historical, observation=changed_summary)
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: validator(historical, expected_commit="e" * 40),
    )
    wrong_time = (
        CAPTURED_AT + timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    _expect_code(
        "DYNAMIC_CAPACITY_CAPTURE_TIME_MISMATCH",
        lambda: validator(historical, expected_time=wrong_time),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_CAPTURE_TIME_MISMATCH",
        lambda: validator(
            changed_receipt_bytes,
            expected_time=wrong_time,
        ),
    )


def test_r5e_r8_validation_context_dispatch_is_closed() -> None:
    historical = _r5e_historical_failed_capture()
    captured_at = historical.receipt["captured_at_utc"]
    base = {
        "expected_governing_commit": (
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        )
    }
    for invalid_context in ("SEALED_EXECUTION_REPLAY", True, None):
        _expect_code(
            "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
            lambda invalid_context=invalid_context: (
                capacity.validate_dynamic_successor_capacity_capture(
                    historical,
                    validation_context=invalid_context,
                    expected_captured_at_utc=captured_at,
                    **base,
                )
            ),
        )
    _expect_code(
        "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            **base,
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            expected_captured_at_utc=captured_at,
            now_utc=CAPTURED_AT,
            **base,
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.SEALED_EXECUTION_REPLAY,
            expected_captured_at_utc="not-an-event-time",
            **base,
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            expected_captured_at_utc=captured_at,
            **base,
        ),
    )
    _expect_code(
        "DYNAMIC_CAPACITY_VALIDATION_CONTEXT_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            now_utc=CAPTURED_AT,
            **base,
        ),
    )

    blocked = _capture(
        governing_commit=GOVERNING_COMMIT,
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=LIVE_USAGE_BYTES,
        ),
        now_utc=datetime.now(timezone.utc),
    )
    assert blocked.observation["status"] != (
        capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    )
    _expect_code(
        "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
        lambda: capacity.validate_dynamic_successor_capacity_capture(
            blocked,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            expected_governing_commit=GOVERNING_COMMIT,
        ),
    )

    live = mock.Mock(return_value=historical)
    with mock.patch.object(
        capacity,
        "_validate_live_preclaim_admission_dynamic_successor_capacity_capture",
        live,
    ):
        observed = capacity.validate_dynamic_successor_capacity_capture(
            historical,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            **base,
        )
    assert observed is historical
    live.assert_called_once_with(
        historical,
        expected_governing_commit=(
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        ),
    )


def test_production_capture_validation_rechecks_tools_without_native_content_read() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        authority = _probe_fixture(Path(temporary).resolve())
        with mock.patch.object(
            capacity, "_path_identity", side_effect=_synthetic_path_identity
        ):
            capture = capacity.probe_dynamic_successor_capacity_observation(
                governing_commit=GOVERNING_COMMIT,
                active_extraction_caches=0,
                preserved_terminal_failed_extraction_caches=2,
                successor_attempt_root_absent=True,
                successor_claim_absent=True,
                authority=authority,
                process_runner=_process_runner(authority),
                now_utc=CAPTURED_AT,
            )

        synthetic_registry = {
            role: dataclass_replace(
                specification,
                argv_tail=tuple(capture.receipt["commands"][role]["argv"][1:]),
            )
            for role, specification in capacity.CAPACITY_COMMAND_REGISTRY.items()
        }
        original_read = capacity._read_regular
        executable_reads: list[Path] = []

        def no_native_content_read(path: Path, **kwargs: Any) -> bytes:
            if path == authority.native_quota_path:
                raise AssertionError("consumer reopened live native quota content")
            if path in {
                authority.pquota_path,
                authority.findmnt_path,
                authority.df_path,
            }:
                executable_reads.append(path)
            return original_read(path, **kwargs)

        pquota_payload = authority.pquota_path.read_bytes()
        reconciliation = mock.Mock(return_value=None)
        with (
            mock.patch.object(
                capacity,
                "DEFAULT_CURRENT_CANARY_HEADROOM_AUTHORITY",
                authority,
            ),
            mock.patch.object(
                capacity,
                "CAPACITY_COMMAND_REGISTRY",
                synthetic_registry,
            ),
            mock.patch.object(
                capacity,
                "EXPECTED_PQUOTA_SIZE_BYTES",
                len(pquota_payload),
            ),
            mock.patch.object(
                capacity,
                "EXPECTED_PQUOTA_SHA256",
                hashlib.sha256(pquota_payload).hexdigest(),
            ),
            mock.patch.object(
                capacity,
                "_validate_current_canary_headroom_authority",
                return_value=True,
            ),
            mock.patch.object(
                capacity, "_path_identity", side_effect=_synthetic_path_identity
            ),
            mock.patch.object(
                capacity,
                "_validate_pquota_restricted_mount_reconciliation",
                reconciliation,
            ),
            mock.patch.object(
                capacity, "_read_regular", side_effect=no_native_content_read
            ),
        ):
            validated = (
                capacity.validate_production_dynamic_successor_capacity_capture(
                    capture,
                    expected_governing_commit=GOVERNING_COMMIT,
                    now_utc=CAPTURED_AT,
                )
            )

        assert validated.receipt_payload == capture.receipt_payload
        assert executable_reads.count(authority.pquota_path) == 1
        assert executable_reads.count(authority.findmnt_path) == 2
        assert executable_reads.count(authority.df_path) == 2
        reconciliation.assert_called_once_with(
            native=capture.receipt["native_quota_authority"]["rows"],
            paths=capture.receipt["paths"]["identities"],
            mounts=capture.receipt["paths"]["mounts"],
        )


def test_scheduler_preflight_and_synthetic_claim_reuse_fixed_pair_without_probe() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        _write_fixed_capture_pair(run, capture)
        dependencies, effects = _no_effect_dependencies(
            test_only_synthetic_full_scope=False
        )
        forbidden_probe = mock.Mock(
            side_effect=AssertionError("scheduler path repeated capacity probe")
        )
        validated_payloads: list[bytes] = []

        def accept_isolated_production_role(
            value: capacity.DynamicSuccessorCapacityCapture,
            **_kwargs: Any,
        ) -> capacity.DynamicSuccessorCapacityCapture:
            # The fixed-pair loader has already checked schema, cross-hashes,
            # freshness, and governing commit. Only the production host path
            # identities cannot be reproduced in this dependency-light test.
            assert value.receipt_payload == capture.receipt_payload
            assert value.observation == capture.observation
            validated_payloads.append(value.receipt_payload)
            return value

        with (
            _synthetic_preflight_boundaries(run),
            mock.patch.object(
                sequential, "resolve_dependencies", return_value=dependencies
            ),
            mock.patch.object(
                capacity,
                "probe_dynamic_successor_capacity_observation",
                forbidden_probe,
            ),
            mock.patch.object(
                capacity,
                "validate_production_dynamic_successor_capacity_capture",
                side_effect=accept_isolated_production_role,
            ),
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                side_effect=accept_isolated_production_role,
            ),
            mock.patch.object(
                sequential.subprocess,
                "run",
                side_effect=AssertionError("scheduler path ran capacity command"),
            ) as process,
        ):
            preflight = sequential.preflight_full()
            assert preflight["capacity"] == capture.observation
            result = sequential.claim_submission(
                qsub_environment_sha256="9" * 64
            )

        forbidden_probe.assert_not_called()
        process.assert_not_called()
        assert len(validated_payloads) >= 2
        assert all(
            payload == capture.receipt_payload
            for payload in validated_payloads
        )
        for name, effect in effects.items():
            if name != "environment_validator":
                effect.assert_not_called()
        assert result["status"] == "READY"
        dynamic_source = (
            run.attempt_root / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
        )
        assert dynamic_source.read_bytes() == capture.receipt_payload
        successor_path = (
            run.attempt_root / "full_capacity_receipt.restricted.json"
        )
        successor = json.loads(successor_path.read_text())
        assert successor["source_dynamic_receipt_bytes"] == len(
            capture.receipt_payload
        )
        assert successor["source_dynamic_receipt_sha256"] == hashlib.sha256(
            capture.receipt_payload
        ).hexdigest()
        claim_path = (
            run.attempt_root / "full_submission_claim.restricted.json"
        )
        claim = json.loads(claim_path.read_text())
        assert claim["dynamic_capacity_receipt_sha256"] == hashlib.sha256(
            capture.receipt_payload
        ).hexdigest()


def test_real_dynamic_seal_reuses_one_capture_in_full_no_body_preflight() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        dependencies, effects = _no_effect_dependencies(
            test_only_synthetic_full_scope=False
        )
        forbidden_claim = mock.Mock(
            side_effect=AssertionError("claim became reachable from capacity seal")
        )
        forbidden_science = mock.Mock(
            side_effect=AssertionError("scientific execution became reachable")
        )
        production_validations: list[capacity.DynamicSuccessorCapacityCapture] = []

        def accept_isolated_production_role(
            value: capacity.DynamicSuccessorCapacityCapture,
            **_kwargs: Any,
        ) -> capacity.DynamicSuccessorCapacityCapture:
            # The live seal normally captures the fixed production roots. This
            # synthetic probe preserves every other receipt/schema/hash check.
            assert value is capture
            production_validations.append(value)
            return value

        real_preflight = sequential.preflight_full
        with (
            _synthetic_preflight_boundaries(run),
            mock.patch.object(
                capacity,
                "probe_dynamic_successor_capacity_observation",
                return_value=capture,
            ) as probe,
            mock.patch.object(
                capacity,
                "validate_production_dynamic_successor_capacity_capture",
                side_effect=accept_isolated_production_role,
            ),
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                side_effect=accept_isolated_production_role,
            ),
            mock.patch.object(
                sequential, "claim_submission", forbidden_claim
            ),
            mock.patch.object(
                sequential, "run_batch_task", forbidden_science
            ),
            mock.patch.object(
                sequential, "run_cross_batch_finalizer", forbidden_science
            ),
            mock.patch.object(
                sequential.subprocess,
                "run",
                side_effect=AssertionError("subprocess reached from sealed capture"),
            ) as process,
            mock.patch.object(
                sequential, "preflight_full", wraps=real_preflight
            ) as preflight,
        ):
            result = sequential.run_dynamic_successor_capacity_seal(
                dependencies=dependencies
            )

        probe.assert_called_once()
        preflight.assert_called_once()
        admission = preflight.call_args.kwargs[
            "_validated_capacity_admission"
        ]
        assert admission.capture is capture
        assert admission.evidence_role == "R5B_HISTORICAL"
        assert production_validations == [capture, capture]
        call = probe.call_args.kwargs
        assert call["successor_attempt_root_absent"] is True
        assert call["successor_claim_absent"] is True
        process.assert_not_called()
        forbidden_claim.assert_not_called()
        forbidden_science.assert_not_called()
        for name, effect in effects.items():
            if name != "environment_validator":
                effect.assert_not_called()

        assert result["status"] == "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE"
        assert result["integrated_no_body_preflight"] == "PASS"
        integrated = result["integrated_preflight"]
        assert integrated["status"] == "PASS_FULL_C3_NO_BODY_PREFLIGHT"
        assert integrated["capacity"] == capture.observation
        successor = integrated["successor_capacity"]
        assert successor["source_dynamic_receipt_bytes"] == len(
            capture.receipt_payload
        )
        assert successor["source_dynamic_receipt_sha256"] == hashlib.sha256(
            capture.receipt_payload
        ).hexdigest()
        assert successor["source_capacity_authority_sha256"] == (
            core.canonical_json_sha256(capture.observation)
        )

        evidence_root = run.production_root / "owner_private"
        receipt_path = (
            evidence_root
            / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME
        )
        summary_path = (
            evidence_root
            / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME
        )
        assert receipt_path.read_bytes() == capture.receipt_payload
        assert summary_path.read_bytes() == capacity._canonical(
            capture.observation
        )
        assert result["restricted_receipt_bytes"] == len(
            capture.receipt_payload
        )
        assert result["restricted_receipt_sha256"] == hashlib.sha256(
            capture.receipt_payload
        ).hexdigest()
        summary_payload = capacity._canonical(capture.observation)
        assert result["aggregate_summary_bytes"] == len(summary_payload)
        assert result["aggregate_summary_sha256"] == hashlib.sha256(
            summary_payload
        ).hexdigest()
        assert result["evidence_files_written"] == 2

        future_claim = sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256=hashlib.sha256(
                capacity._canonical(successor)
            ).hexdigest(),
            dynamic_capacity_receipt_sha256=result[
                "restricted_receipt_sha256"
            ],
            capacity_evidence_role="R5B_HISTORICAL",
            capacity_gain_source="ALLOCATION",
            raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CLEANUP_SKIPPED"
            ),
            qsub_environment_sha256="9" * 64,
        )
        assert future_claim["dynamic_capacity_receipt_sha256"] == (
            successor["source_dynamic_receipt_sha256"]
        )
        assert not run.attempt_root.exists()
        assert not (
            run.attempt_root / "full_submission_claim.restricted.json"
        ).exists()
        for key in capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS:
            assert capture.observation[key] == 0
            assert result[key] == 0

        report_lines = sequential.format_dynamic_successor_capacity_seal_report(
            result
        )
        markers = dict(line.split("=", 1) for line in report_lines)
        assert markers["R5B_R1_STARTING_COMMIT"] == (
            "738195d1fa4ae4e2a23bbd9482b5acd008317eb4"
        )
        assert markers["STATIC_CAPACITY_INCOMPATIBILITY"] == "CONFIRMED"
        assert markers["HISTORICAL_STATIC_CAPACITY_PATH_UNCHANGED"] == "YES"
        assert markers["DYNAMIC_SUCCESSOR_CAPACITY_AUTHORITY"] == "PASS"
        assert markers["HARD_CODED_ANTICIPATED_QUOTA_ADDED"] == "NO"
        assert markers["R5D_RETIREMENT_ROUTE"] == (
            "CLOSED_COMPLEXITY_DISPROPORTIONATE"
        )
        assert markers["PREFIX_ADOPTION_IMPLEMENTED"] == "NO"
        assert markers["CROSS_ATTEMPT_RECOVERY_IMPLEMENTED"] == "NO"
        assert markers["CURRENT_CAPACITY_STATUS"] == result["status"]
        assert markers["STORAGE_ALLOCATION_VISIBLE"] == "YES"
        assert markers["CURRENT_QUOTA_BYTES"] == str(
            capture.observation["live_research_quota_bytes"]
        )
        assert markers["CURRENT_USAGE_BYTES"] == str(
            capture.observation["live_research_usage_bytes"]
        )
        assert markers["CURRENT_PHYSICAL_AVAILABLE_BYTES"] == str(
            capture.observation["live_research_filesystem_available_bytes"]
        )
        assert markers["CURRENT_REMAINING_FILE_SLOTS"] == str(
            capture.observation["remaining_file_slots"]
        )
        assert markers["CAPACITY_RECEIPT_BYTES"] == str(
            len(capture.receipt_payload)
        )
        assert markers["CAPACITY_RECEIPT_SHA256"] == hashlib.sha256(
            capture.receipt_payload
        ).hexdigest()
        assert markers["CAPACITY_SUMMARY_BYTES"] == str(len(summary_payload))
        assert markers["CAPACITY_SUMMARY_SHA256"] == hashlib.sha256(
            summary_payload
        ).hexdigest()
        assert markers["SCC_INTEGRATED_NO_BODY_ACCEPTANCE"] == "PASS"
        assert markers["SUCCESSOR_ATTEMPT_ROOT_CREATED"] == "NO"
        assert markers["SUCCESSOR_CLAIM_CREATED"] == "NO"
        assert markers["CONFIRMATORY_PERFORMANCE_ACCESSED"] == "NO"
        assert markers["FULL_C3_STATUS"] == (
            "NO_GO_PENDING_DYNAMIC_CAPACITY_SEAL_AND_"
            "SEPARATE_SUCCESSOR_AUTHORIZATION"
        )
        assert markers["EXACT_NEXT_ACTION"] == (
            "REVIEW_SEPARATE_FRESH_SUCCESSOR_AUTHORIZATION"
        )
        for name in (
            "NEW_CLOUD_REQUESTS",
            "NEW_QSUB_SUBMISSIONS",
            "NEW_DICOM_BODY_READS",
            "NEW_NPZ_BODY_READS",
            "NEW_GPU_EXECUTIONS",
            "NEW_ECHOPRIME_EXECUTIONS",
            "NEW_EMBEDDING_GENERATIONS",
            "NEW_MODEL_FITTING",
            "NEW_PREDICTION_GENERATION",
            "NEW_CONFIRMATORY_PERFORMANCE_ACCESSES",
        ):
            assert markers[name] == "0"

        for field, changed in (
            ("aggregate_summary_bytes", len(summary_payload) + 1),
            ("aggregate_summary_bytes", len(summary_payload) - 1),
            ("aggregate_summary_sha256", "0" * 64),
            ("status", "ALLOCATION_NOT_YET_VISIBLE"),
        ):
            mutated = copy.deepcopy(result)
            mutated[field] = changed
            _expect_code(
                "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID",
                lambda mutated=mutated: (
                    sequential.format_dynamic_successor_capacity_seal_report(
                        mutated
                    )
                ),
            )
        missing_key = copy.deepcopy(result)
        del missing_key["minimum_additional_file_slots"]
        extra_key = {**copy.deepcopy(result), "unexpected_private_field": 0}
        for mutated_keys in (missing_key, extra_key):
            _expect_code(
                "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID",
                lambda mutated_keys=mutated_keys: (
                    sequential.format_dynamic_successor_capacity_seal_report(
                        mutated_keys
                    )
                ),
            )
        mutated_integrated = copy.deepcopy(result)
        mutated_integrated["integrated_preflight"]["capacity"] = {}
        _expect_code(
            "FULL_SEQUENTIAL_DYNAMIC_CAPACITY_REPORT_INVALID",
            lambda: sequential.format_dynamic_successor_capacity_seal_report(
                mutated_integrated
            ),
        )

        report = "\n".join(report_lines)
        assert str(run.production_root) not in report
        assert "subject_id" not in report
        assert "study_id" not in report
        assert "source_object" not in report


def test_pending_and_blocked_seals_never_reach_preflight_or_claim() -> None:
    file_quota = 33_554_432
    file_usage = (
        file_quota - capacity.DYNAMIC_SUCCESSOR_REQUIRED_FILE_SLOTS + 1
    )
    cases = (
        (
            "ALLOCATION_NOT_YET_VISIBLE",
            lambda now: _capture(
                native_payload=_dynamic_native(
                    research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
                    research_usage_bytes=LIVE_USAGE_BYTES,
                ),
                now_utc=now,
            ),
            (92_578_972_844, 0, 0),
        ),
        (
            "BLOCKED_ADDITIONAL_STORAGE_REQUIRED",
            lambda now: _capture(
                research_available=(
                    capacity.DYNAMIC_SUCCESSOR_INCREMENT_BYTES
                    + capacity.DYNAMIC_SUCCESSOR_PHYSICAL_RESERVE_BYTES
                    - 1
                ),
                now_utc=now,
            ),
            (0, 1, 0),
        ),
        (
            "BLOCKED_ADDITIONAL_STORAGE_REQUIRED",
            lambda now: _capture(
                native_payload=_dynamic_native(
                    research_file_quota=file_quota,
                    research_files_used=file_usage,
                ),
                now_utc=now,
            ),
            (0, 0, 1),
        ),
    )
    for expected_status, capture_factory, expected_minima in cases:
        capture = capture_factory(datetime.now(timezone.utc))
        with tempfile.TemporaryDirectory() as temporary:
            run = _synthetic_full_run(Path(temporary).resolve())
            dependencies, effects = _no_effect_dependencies(
                test_only_synthetic_full_scope=True
            )
            forbidden_preflight = mock.Mock(
                side_effect=AssertionError("non-pass capacity reached preflight")
            )
            forbidden_claim = mock.Mock(
                side_effect=AssertionError("non-pass capacity reached claim")
            )
            with (
                _synthetic_preflight_boundaries(run),
                mock.patch.object(
                    sequential, "preflight_full", forbidden_preflight
                ),
                mock.patch.object(
                    sequential, "claim_submission", forbidden_claim
                ),
            ):
                result = sequential.run_dynamic_successor_capacity_seal(
                    capture=capture, dependencies=dependencies
                )
            forbidden_preflight.assert_not_called()
            forbidden_claim.assert_not_called()
            for name, effect in effects.items():
                if name != "environment_validator":
                    effect.assert_not_called()
            assert result["status"] == expected_status
            assert result["integrated_no_body_preflight"] == "NOT_RUN"
            assert result["integrated_preflight"] is None
            assert (
                result["minimum_additional_quota_bytes"],
                result["minimum_additional_physical_bytes"],
                result["minimum_additional_file_slots"],
            ) == expected_minima
            assert result["evidence_files_written"] == 2
            assert not run.attempt_root.exists()
            assert not (
                run.attempt_root / "full_submission_claim.restricted.json"
            ).exists()
            for key in capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS:
                assert result[key] == 0


def test_full_preflight_rechecks_root_and_claim_after_observation() -> None:
    capture = _capture(now_utc=datetime.now(timezone.utc))
    for race_role in ("attempt_root", "claim"):
        with tempfile.TemporaryDirectory() as temporary:
            run = _synthetic_full_run(Path(temporary).resolve())
            claim_path = (
                run.attempt_root / "full_submission_claim.restricted.json"
            )
            dependencies, effects = _no_effect_dependencies(
                capacity_probe=lambda: capture
            )
            calls = {"attempt_root": 0, "claim": 0}

            def raced_lexists(value: object) -> bool:
                path = Path(value)
                if path == run.attempt_root:
                    calls["attempt_root"] += 1
                    return (
                        race_role == "attempt_root"
                        and calls["attempt_root"] >= 3
                    )
                if path == claim_path:
                    calls["claim"] += 1
                    return race_role == "claim" and calls["claim"] >= 2
                return False

            with (
                _synthetic_preflight_boundaries(run),
                mock.patch.object(
                    sequential.os.path,
                    "lexists",
                    side_effect=raced_lexists,
                ),
                mock.patch.object(
                    sequential,
                    "build_successor_capacity_authority",
                    side_effect=AssertionError("derived after successor race"),
                ) as derive,
            ):
                _expect_code(
                    "DYNAMIC_CAPACITY_SUCCESSOR_COLLISION",
                    lambda: sequential.preflight_full(
                        dependencies=dependencies
                    ),
                )
            derive.assert_not_called()
            for name, effect in effects.items():
                if name != "environment_validator":
                    effect.assert_not_called()
            assert not run.attempt_root.exists()
            assert not claim_path.exists()


def test_only_seal_live_probe_uses_observed_successor_absence_flags() -> None:
    preflight_source = inspect.getsource(sequential.preflight_full)
    assert "probe_dynamic_successor_capacity_observation" not in preflight_source
    assert "_load_fixed_capacity_admission" in preflight_source

    seal_source = inspect.getsource(
        sequential.run_dynamic_successor_capacity_seal
    )
    assert "successor_attempt_root_absent=True" not in seal_source
    assert "successor_claim_absent=True" not in seal_source
    assert "successor_attempt_root_absent=attempt_absent" in seal_source
    assert "successor_claim_absent=claim_absent" in seal_source


def test_r5e_r7_historical_pre_cleanup_is_closed_event_time_evidence() -> None:
    assert sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT == (
        "f201e22cce760402814be76e25899c0ba10bdfbd"
    )
    assert sequential.HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES == 12_267
    assert sequential.HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256 == (
        "b5a3340e9968633189f70d00a6593c39296d7b9dce20303bbe97671626b14b5e"
    )
    assert sequential.HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES == 3_084
    assert sequential.HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256 == (
        "72201eedc716aa5bf39da6bda76ea4614c34f3fe4edaa1db3a36454ed2b7ec5e"
    )
    captured = _r5e_historical_failed_capture()
    assert captured.observation["status"] == (
        capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
    )
    captured_at = captured.receipt["captured_at_utc"]
    assert not inspect.signature(
        sequential._load_historical_r5e_r2_pre_action_capacity
    ).parameters
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        receipt, summary = _write_r5e_historical_pair(run, captured)
        capture_validator = mock.Mock(
            wraps=capacity.validate_dynamic_successor_capacity_capture
        )
        with (
            mock.patch.object(
                sequential, "PRODUCTION_ROOT", run.production_root
            ),
            mock.patch.object(
                sequential,
                "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES",
                len(receipt.read_bytes()),
            ),
            mock.patch.object(
                sequential,
                "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256",
                hashlib.sha256(receipt.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                sequential,
                "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES",
                len(summary.read_bytes()),
            ),
            mock.patch.object(
                sequential,
                "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256",
                hashlib.sha256(summary.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                sequential,
                "HISTORICAL_R5E_R2_PRE_ACTION_CAPTURED_AT_UTC",
                captured_at,
            ),
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                capture_validator,
            ),
        ):
            historical = (
                sequential._load_historical_r5e_r2_pre_action_capacity()
            )
            _expect_code(
                "DYNAMIC_CAPACITY_RECEIPT_SCHEMA_INVALID",
                lambda: sequential._load_test_only_capacity_pair(
                    run,
                    restricted_basename=receipt.name,
                    summary_basename=summary.name,
                    require_pass=False,
                    now_utc=CAPTURED_AT,
                ),
            )
        assert isinstance(historical, sequential.HistoricalCapacityEvent)
        assert not isinstance(historical, sequential.CapacityAdmission)
        assert historical.capture.receipt_payload == captured.receipt_payload
        assert historical.authority == _historical_event(captured).authority
        capture_validator.assert_called_once()
        assert capture_validator.call_args.kwargs == {
            "validation_context": capacity.SEALED_EXECUTION_REPLAY,
            "expected_governing_commit": (
                sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
            ),
            "expected_captured_at_utc": captured_at,
        }
        assert run.authority.governing_commit != (
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        )


def test_r5e_r7_historical_pre_cleanup_failure_roles_are_exact() -> None:
    base = _r5e_historical_failed_capture()
    for changed_role in ("receipt", "summary"):
        with tempfile.TemporaryDirectory() as temporary:
            run = _synthetic_full_run(Path(temporary).resolve())
            receipt, summary = _write_r5e_historical_pair(run, base)
            sealed_receipt_sha = hashlib.sha256(
                receipt.read_bytes()
            ).hexdigest()
            sealed_summary_sha = hashlib.sha256(
                summary.read_bytes()
            ).hexdigest()
            changed = receipt if changed_role == "receipt" else summary
            payload = changed.read_bytes()
            changed.write_bytes(b"!" + payload[1:])
            with (
                mock.patch.object(
                    sequential, "PRODUCTION_ROOT", run.production_root
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES",
                    len(receipt.read_bytes()),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256",
                    sealed_receipt_sha,
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES",
                    len(summary.read_bytes()),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256",
                    sealed_summary_sha,
                ),
            ):
                _expect_code(
                    "FULL_SEQUENTIAL_HISTORICAL_EVENT_HASH_MISMATCH",
                    sequential._load_historical_r5e_r2_pre_action_capacity,
                )

    wrong_commit = _capture(
        governing_commit="e" * 40,
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=LIVE_USAGE_BYTES,
        ),
        now_utc=CAPTURED_AT,
    )
    passing = _capture(
        governing_commit=(
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT
        ),
        now_utc=CAPTURED_AT,
    )
    assert passing.observation["status"] == (
        capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    )
    for label, captured in (("wrong_commit", wrong_commit), ("pass", passing)):
        with tempfile.TemporaryDirectory() as temporary:
            run = _synthetic_full_run(Path(temporary).resolve())
            receipt, summary = _write_r5e_historical_pair(run, captured)
            with (
                mock.patch.object(
                    sequential, "PRODUCTION_ROOT", run.production_root
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_BYTES",
                    len(receipt.read_bytes()),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256",
                    hashlib.sha256(receipt.read_bytes()).hexdigest(),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_BYTES",
                    len(summary.read_bytes()),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_SUMMARY_SHA256",
                    hashlib.sha256(summary.read_bytes()).hexdigest(),
                ),
                mock.patch.object(
                    sequential,
                    "HISTORICAL_R5E_R2_PRE_ACTION_CAPTURED_AT_UTC",
                    captured.receipt["captured_at_utc"],
                ),
            ):
                _expect_code(
                    (
                        "FULL_SEQUENTIAL_HISTORICAL_EVENT_COMMIT_INVALID"
                        if label == "wrong_commit"
                        else "FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID"
                    ),
                    sequential._load_historical_r5e_r2_pre_action_capacity,
                )
            assert label in {"wrong_commit", "pass"}


def test_r5e_r7_completed_retirement_event_is_fixed_and_descendant_safe() -> None:
    assert sequential.RETIREMENT_EVENT_COMMIT == (
        "ab5670f9db8d7ae06a43f3e5b10aa79e52edfb6b"
    )
    assert sequential.RETIREMENT_EVENT_RECEIPT_SHA256 == (
        "dd7d4328a5f5cc1507e9029d94a6960e051a4ad6413dc865d8f0b75a08ef1fe1"
    )
    assert sequential.RETIREMENT_EVENT_ARTIFACTS == (
        (
            "older_raw_retirement_manifest.restricted.json",
            37_139_326,
            "04f116b644fcf289a6b0e5927e34410d1c2d38cf03debb615038c077656e51bd",
        ),
        (
            "older_raw_retirement_receipt.restricted.json",
            1_557,
            "dd7d4328a5f5cc1507e9029d94a6960e051a4ad6413dc865d8f0b75a08ef1fe1",
        ),
        (
            "older_raw_retirement_summary.aggregate_safe.json",
            1_218,
            "0124213dd3b687165babf554dc7831b255225d051af52dd03351f646922b5b67",
        ),
    )
    historical = _historical_event(_r5e_historical_failed_capture())
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        artifact_root = (
            run.production_root
            / "owner_private"
            / "r5e_older_raw_retirement"
        )
        artifact_root.mkdir(mode=0o700)
        diagnostics = {
            "diagnostic_root_count": 1,
            "diagnostic_file_count": 2,
            "diagnostic_directory_count": 3,
            "diagnostic_total_bytes": 4,
            "diagnostics_retained": True,
            "body_reads": 0,
        }
        manifest = {
            "governing_commit": sequential.RETIREMENT_EVENT_COMMIT,
            "pre_cleanup_capacity_authority": dict(historical.authority),
            "diagnostic_evidence_authority": diagnostics,
            "older_receipt_tree_authority": {
                "auxiliary_receipt_files": 5,
                "auxiliary_receipt_bytes": 6,
            },
        }
        receipt_value = {
            "governing_commit": sequential.RETIREMENT_EVENT_COMMIT,
            "status": sequential.RETIREMENT_EVENT_STATUS,
            "actual_deleted_files": sequential.RETIREMENT_EVENT_DELETED_FILES,
            "actual_deleted_bytes": sequential.RETIREMENT_EVENT_DELETED_BYTES,
            "retained_file_metadata_sha256": "a" * 64,
            "retained_directory_topology_sha256": "b" * 64,
            "retained_role_inventory_sha256": "c" * 64,
            "retained_control_content_sha256": "d" * 64,
            "diagnostic_evidence_authority_sha256": "e" * 64,
        }
        summary_value = {"status": "PURE_SEALED_RETIREMENT_TEST"}
        payloads = {
            "older_raw_retirement_manifest.restricted.json": (
                raw_retirement._canonical(manifest)
            ),
            "older_raw_retirement_receipt.restricted.json": (
                raw_retirement._canonical(receipt_value)
            ),
            "older_raw_retirement_summary.aggregate_safe.json": (
                raw_retirement._canonical(summary_value)
            ),
        }
        artifacts = tuple(
            (
                basename,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
            for basename, payload in payloads.items()
        )
        for basename, payload in payloads.items():
            path = artifact_root / basename
            path.write_bytes(payload)
            path.chmod(0o600)
        manifest_validator = mock.Mock()
        receipt_validator = mock.Mock()
        summary_builder = mock.Mock(return_value=summary_value)
        live_validator = mock.Mock(
            side_effect=AssertionError("pure loader reached live replay")
        )
        with (
            mock.patch.object(
                sequential, "PRODUCTION_ROOT", run.production_root
            ),
            mock.patch.object(
                sequential, "RETIREMENT_EVENT_ARTIFACTS", artifacts
            ),
            mock.patch.object(
                sequential,
                "RETIREMENT_EVENT_RECEIPT_SHA256",
                artifacts[1][2],
            ),
            mock.patch.object(
                sequential,
                "_require_retirement_event_git_authority",
                return_value=GOVERNING_COMMIT,
            ),
            mock.patch.object(
                sequential,
                "_fixed_historical_pre_cleanup_capacity_authority",
                return_value=dict(historical.authority),
            ),
            mock.patch.object(
                raw_retirement, "_validate_manifest", manifest_validator
            ),
            mock.patch.object(
                raw_retirement, "_validate_receipt", receipt_validator
            ),
            mock.patch.object(
                raw_retirement, "_summary_from_receipt", summary_builder
            ),
            mock.patch.object(
                raw_retirement, "validate_retired_state", live_validator
            ),
        ):
            event = sequential._load_completed_retirement_event()
        assert event == {
            "status": sequential.RETIREMENT_EVENT_STATUS,
            "receipt_sha256": artifacts[1][2],
            "pre_cleanup_capacity_authority": dict(historical.authority),
            "retained_state_authority": {
                "actual_deleted_files": (
                    sequential.RETIREMENT_EVENT_DELETED_FILES
                ),
                "actual_deleted_bytes": (
                    sequential.RETIREMENT_EVENT_DELETED_BYTES
                ),
                "auxiliary_receipt_files": 5,
                "auxiliary_receipt_bytes": 6,
                "retained_file_metadata_sha256": "a" * 64,
                "retained_directory_topology_sha256": "b" * 64,
                "retained_role_inventory_sha256": "c" * 64,
                "retained_control_content_sha256": "d" * 64,
                "diagnostic_evidence_authority_sha256": "e" * 64,
                "diagnostic_root_count": 1,
                "diagnostic_file_count": 2,
                "diagnostic_directory_count": 3,
                "diagnostic_total_bytes": 4,
                "diagnostics_retained": True,
                "diagnostic_body_reads": 0,
            },
        }
        manifest_validator.assert_called_once_with(manifest)
        receipt_validator.assert_called_once_with(
            receipt_value,
            manifest_payload=payloads[artifacts[0][0]],
        )
        summary_builder.assert_called_once_with(
            receipt_value,
            receipt_payload=payloads[artifacts[1][0]],
        )
        live_validator.assert_not_called()

        for ordinal, (basename, size, digest) in enumerate(artifacts):
            changed = list(artifacts)
            changed[ordinal] = (basename, size, "0" * 64)
            with (
                mock.patch.object(
                    sequential, "PRODUCTION_ROOT", run.production_root
                ),
                mock.patch.object(
                    sequential,
                    "RETIREMENT_EVENT_ARTIFACTS",
                    tuple(changed),
                ),
            ):
                _expect_code(
                    "FULL_SEQUENTIAL_RETIREMENT_EVENT_ARTIFACT_INVALID",
                    sequential._retirement_event_artifact_payloads,
                )
            assert digest != "0" * 64

        with (
            mock.patch.object(sequential, "_current_commit", return_value="7" * 40),
            mock.patch.object(sequential, "_git", return_value="0" * 40),
        ):
            _expect_code(
                "FULL_SEQUENTIAL_RETIREMENT_EVENT_ANCESTRY_INVALID",
                sequential._require_retirement_event_git_authority,
            )
        with (
            mock.patch.object(
                sequential,
                "RETIREMENT_EVENT_COMMIT",
                "e" * 40,
            ),
            mock.patch.object(sequential, "_current_commit", return_value="7" * 40),
            mock.patch.object(
                sequential,
                "_git",
                return_value=(
                    "ab5670f9db8d7ae06a43f3e5b10aa79e52edfb6b"
                ),
            ),
        ):
            _expect_code(
                "FULL_SEQUENTIAL_RETIREMENT_EVENT_ANCESTRY_INVALID",
                sequential._require_retirement_event_git_authority,
            )

        for state_failure in ("manifest", "deleted_count"):
            state_payloads = payloads
            state_manifest_validator = mock.Mock()
            if state_failure == "manifest":
                state_manifest_validator.side_effect = RuntimeError(
                    "sealed manifest invalid"
                )
            else:
                bad_receipt = {**receipt_value, "actual_deleted_files": 0}
                state_payloads = {
                    **payloads,
                    artifacts[1][0]: raw_retirement._canonical(bad_receipt),
                }
            with (
                mock.patch.object(
                    sequential,
                    "_retirement_event_artifact_payloads",
                    return_value=state_payloads,
                ),
                mock.patch.object(
                    sequential,
                    "_require_retirement_event_git_authority",
                    return_value=GOVERNING_COMMIT,
                ),
                mock.patch.object(
                    sequential,
                    "_fixed_historical_pre_cleanup_capacity_authority",
                    return_value=dict(historical.authority),
                ),
                mock.patch.object(
                    raw_retirement,
                    "_validate_manifest",
                    state_manifest_validator,
                ),
                mock.patch.object(
                    raw_retirement, "_validate_receipt"
                ),
                mock.patch.object(
                    raw_retirement,
                    "_summary_from_receipt",
                    return_value=summary_value,
                ),
            ):
                _expect_code(
                    "FULL_SEQUENTIAL_RETIREMENT_STATE_INVALID",
                    sequential._load_completed_retirement_event,
                )


def test_r5e_r7_current_post_cleanup_is_fresh_and_claim_only() -> None:
    cleanup_usage = (393_000_000_000 // 1024) * 1024
    current = _capture(
        governing_commit=GOVERNING_COMMIT,
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=cleanup_usage,
        ),
        now_utc=datetime.now(timezone.utc),
    )
    assert current.observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        _write_fixed_capture_pair(
            run,
            current,
            restricted_basename=(
                capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        production_validator = mock.Mock(return_value=current)
        with (
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                production_validator,
            ),
        ):
            observed = sequential._load_current_r5e_r8_post_cleanup_capacity(
                run
            )
        assert observed.receipt_payload == current.receipt_payload
        production_validator.assert_called_once_with(
            mock.ANY,
            validation_context=capacity.LIVE_PRECLAIM_ADMISSION,
            expected_governing_commit=GOVERNING_COMMIT,
        )

    stale = _capture(
        governing_commit=GOVERNING_COMMIT,
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=cleanup_usage,
        ),
        now_utc=(
            datetime.now(timezone.utc)
            - timedelta(
                seconds=capacity.DYNAMIC_SUCCESSOR_MAXIMUM_AGE_SECONDS + 60
            )
        ),
    )
    with tempfile.TemporaryDirectory() as temporary:
        run = _synthetic_full_run(Path(temporary).resolve())
        _write_fixed_capture_pair(
            run,
            stale,
            restricted_basename=(
                capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        _expect_code(
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_STALE",
            lambda: sequential._load_current_r5e_r8_post_cleanup_capacity(run),
        )

    historical = _historical_event(_r5e_historical_failed_capture())
    combined = _capture(
        governing_commit=GOVERNING_COMMIT,
        native_payload=_dynamic_native(
            research_quota_bytes=(
                HISTORICAL_RESEARCH_QUOTA_BYTES + 30_000_000_000
            ),
            research_usage_bytes=450_000_000_000,
        ),
        now_utc=datetime.now(timezone.utc),
    )
    assert combined.observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    for expected_gain, post in (("CLEANUP", current), ("BOTH", combined)):
        with tempfile.TemporaryDirectory() as temporary:
            run = _production_role_run(Path(temporary).resolve())
            _write_fixed_capture_pair(
                run,
                post,
                restricted_basename=(
                    capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
                ),
                summary_basename=(
                    capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
                ),
            )
            retired = _completed_retirement_event(historical)
            with (
                mock.patch.object(
                    sequential, "PRODUCTION_ROOT", run.production_root
                ),
                mock.patch.object(
                    sequential,
                    "_load_historical_r5e_r2_pre_action_capacity",
                    return_value=historical,
                ),
                mock.patch.object(
                    sequential,
                    "_load_historical_r5e_post_cleanup_capacity",
                    return_value=_historical_event(post),
                ),
                mock.patch.object(
                    sequential,
                    "_load_current_r5e_r8_post_cleanup_capacity",
                    return_value=post,
                ),
                mock.patch.object(
                    sequential,
                    "_load_completed_retirement_event",
                    return_value=retired,
                ),
            ):
                admission = sequential._load_fixed_capacity_admission(run)
            assert admission.evidence_role == "R5E_R8_POST_CLEANUP"
            assert admission.capacity_gain_source == expected_gain
            assert admission.capture is post
            assert admission.raw_retirement_receipt_sha256 == (
                sequential.RETIREMENT_EVENT_RECEIPT_SHA256
            )

    with tempfile.TemporaryDirectory() as temporary:
        run = _production_role_run(Path(temporary).resolve())
        historical_loader = mock.Mock(return_value=historical)
        with (
            mock.patch.object(
                sequential, "PRODUCTION_ROOT", run.production_root
            ),
            mock.patch.object(
                sequential,
                "_load_historical_r5e_r2_pre_action_capacity",
                historical_loader,
            ),
            mock.patch.object(
                sequential,
                "_load_historical_r5e_post_cleanup_capacity",
                return_value=_historical_event(current),
            ),
            mock.patch.object(
                sequential, "_load_current_r5e_r8_post_cleanup_capacity"
            ) as current_loader,
            mock.patch.object(
                sequential, "_load_completed_retirement_event"
            ) as event_loader,
        ):
            _expect_code(
                "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID",
                lambda: sequential._load_fixed_capacity_admission(run),
            )
        historical_loader.assert_called_once_with()
        current_loader.assert_not_called()
        event_loader.assert_not_called()

        historical_sha = hashlib.sha256(
            historical.capture.receipt_payload
        ).hexdigest()
        _expect_code(
            "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID",
            lambda: sequential._expected_submission_claim(
                run,
                capacity_receipt_sha256="a" * 64,
                dynamic_capacity_receipt_sha256=historical_sha,
                capacity_evidence_role="R5E_R2_PRE_ACTION",
                capacity_gain_source="CLEANUP",
                raw_retirement_status=(
                    sequential.RETIREMENT_EVENT_STATUS
                ),
                raw_retirement_receipt_sha256=(
                    sequential.RETIREMENT_EVENT_RECEIPT_SHA256
                ),
                qsub_environment_sha256="9" * 64,
            ),
        )
        current_sha = hashlib.sha256(current.receipt_payload).hexdigest()
        claim = sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256="a" * 64,
            dynamic_capacity_receipt_sha256=current_sha,
            capacity_evidence_role="R5E_R8_POST_CLEANUP",
            capacity_gain_source="CLEANUP",
            raw_retirement_status=sequential.RETIREMENT_EVENT_STATUS,
            raw_retirement_receipt_sha256=(
                sequential.RETIREMENT_EVENT_RECEIPT_SHA256
            ),
            qsub_environment_sha256="9" * 64,
        )
        assert claim["dynamic_capacity_receipt_sha256"] == current_sha
        assert claim["dynamic_capacity_receipt_sha256"] != historical_sha
        assert claim["capacity_evidence_role"] == "R5E_R8_POST_CLEANUP"
        assert claim["raw_retirement_receipt_sha256"] == (
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        )


def test_r5e_r7_post_cleanup_seal_reuses_one_probe_and_has_no_effects() -> None:
    cleanup_usage = (393_000_000_000 // 1024) * 1024
    current = _capture(
        governing_commit=GOVERNING_COMMIT,
        native_payload=_dynamic_native(
            research_quota_bytes=HISTORICAL_RESEARCH_QUOTA_BYTES,
            research_usage_bytes=cleanup_usage,
        ),
        now_utc=datetime.now(timezone.utc),
    )
    historical = _historical_event(_r5e_historical_failed_capture())
    retired = _completed_retirement_event(historical)
    with tempfile.TemporaryDirectory() as temporary:
        run = _production_role_run(Path(temporary).resolve())
        dependencies, effects = _no_effect_dependencies(
            test_only_synthetic_full_scope=False
        )
        forbidden_claim = mock.Mock(
            side_effect=AssertionError("capacity seal reached claim")
        )
        forbidden_science = mock.Mock(
            side_effect=AssertionError("capacity seal reached science")
        )
        real_preflight = sequential.preflight_full

        def accept_current(
            value: capacity.DynamicSuccessorCapacityCapture,
            **kwargs: Any,
        ) -> capacity.DynamicSuccessorCapacityCapture:
            assert value is current
            assert kwargs == {
                "validation_context": capacity.LIVE_PRECLAIM_ADMISSION,
                "expected_governing_commit": GOVERNING_COMMIT,
            }
            return value

        with (
            _synthetic_preflight_boundaries(run),
            mock.patch.object(
                sequential,
                "_load_historical_r5e_r2_pre_action_capacity",
                return_value=historical,
            ),
            mock.patch.object(
                sequential,
                "_load_historical_r5e_post_cleanup_capacity",
                return_value=_historical_event(current),
            ),
            mock.patch.object(
                sequential,
                "_load_completed_retirement_event",
                return_value=retired,
            ),
            mock.patch.object(
                capacity,
                "probe_dynamic_successor_capacity_observation",
                return_value=current,
            ) as probe,
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                side_effect=accept_current,
            ),
            mock.patch.object(
                sequential, "claim_submission", forbidden_claim
            ),
            mock.patch.object(
                sequential, "run_batch_task", forbidden_science
            ),
            mock.patch.object(
                sequential, "run_cross_batch_finalizer", forbidden_science
            ),
            mock.patch.object(
                sequential.subprocess,
                "run",
                side_effect=AssertionError("capacity seal ran subprocess"),
            ) as process,
            mock.patch.object(
                sequential, "preflight_full", wraps=real_preflight
            ) as preflight,
        ):
            result = sequential.run_dynamic_successor_capacity_seal(
                evidence_role="R5E_R8_POST_CLEANUP",
                dependencies=dependencies,
            )
        probe.assert_called_once()
        preflight.assert_not_called()
        process.assert_not_called()
        forbidden_claim.assert_not_called()
        forbidden_science.assert_not_called()
        for name, effect in effects.items():
            if name != "environment_validator":
                effect.assert_not_called()
        assert result["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
        assert result["capacity_evidence_role"] == "R5E_R8_POST_CLEANUP"
        assert result["capacity_gain_source"] == "CLEANUP"
        assert result["raw_retirement_status"] == (
            sequential.RETIREMENT_EVENT_STATUS
        )
        assert result["raw_retirement_receipt_sha256"] == (
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        )
        assert result["integrated_no_body_preflight"] == "NOT_RUN"
        assert result["evidence_files_written"] == 2
        assert not run.attempt_root.exists()
        for key in capacity.DYNAMIC_SUCCESSOR_ZERO_EFFECT_KEYS:
            assert result[key] == 0


def test_every_r5b_test_function_is_zero_argument() -> None:
    tests = {
        name: value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    }
    assert tests
    assert all(not inspect.signature(value).parameters for value in tests.values())
