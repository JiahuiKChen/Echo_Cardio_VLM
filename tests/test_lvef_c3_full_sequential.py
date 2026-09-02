from __future__ import annotations

"""Focused, dependency-light contracts for the full sequential C3 adapter."""

import base64
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock

try:
    import pytest
except ModuleNotFoundError:
    class _Raises:
        def __init__(self, expected: type[BaseException]):
            self.expected = expected
            self.value: BaseException | None = None

        def __enter__(self) -> _Raises:
            return self

        def __exit__(self, kind: Any, value: Any, _traceback: Any) -> bool:
            if kind is None:
                raise AssertionError(f"expected {self.expected.__name__}")
            if not issubclass(kind, self.expected):
                return False
            self.value = value
            return True

    class _DependencyLightPytest:
        raises = staticmethod(lambda expected: _Raises(expected))

    pytest = _DependencyLightPytest()


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
BOUND_QSUB_ENVIRONMENT_SHA256 = "9" * 64

import lvef_c3_full_sequential as sequential
import lvef_c3_full_scheduler as scheduler
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation

capacity = sequential.capacity


def test_r8u_compatibility_name_resolves_only_to_fresh_r2_context() -> None:
    assert sequential.R8U_FIXED_CONTINUATION is (
        sequential.R8U_R2_FIXED_CONTINUATION
    )
    assert sequential.R8U_R2_FIXED_CONTINUATION.value == (
        "R8U_R2_FIXED_CONTINUATION"
    )
    assert not hasattr(
        sequential.FullExecutionContext, "R8U_FIXED_CONTINUATION"
    )


def test_r8u_r5_continuation_context_is_fresh_and_closed() -> None:
    assert sequential.R8U_R5_FIXED_CONTINUATION is (
        sequential.FullExecutionContext.R8U_R5_FIXED_CONTINUATION
    )
    assert sequential.R8U_R5_FIXED_CONTINUATION.value == (
        "R8U_R5_FIXED_CONTINUATION"
    )
    assert sequential.R8U_R5_FIXED_CONTINUATION is not (
        sequential.R8U_R4_FIXED_CONTINUATION
    )
    source = inspect.getsource(sequential.run_batch_task)
    assert "FULL_SEQUENTIAL_R8U_R5_CONTINUATION_TASK_OUT_OF_SCOPE" in source
    assert "FULL_SEQUENTIAL_R8U_R5_EXTRACTION_CACHE_TOPOLOGY_INVALID" in source
    assert "validate_r8u_r5_frozen_partial_evidence()" in source
    assert "validate_r8u_r5_continuation_worker_submission(" in source


def test_r8u_r7_continuation_context_is_fixed_to_tasks17_19() -> None:
    assert sequential.R8U_R7_FIXED_CONTINUATION is (
        sequential.FullExecutionContext.R8U_R7_FIXED_CONTINUATION
    )
    assert sequential.R8U_R7_FIXED_CONTINUATION.value == (
        "R8U_R7_FIXED_CONTINUATION"
    )
    assert sequential.R8U_R7_FIXED_CONTINUATION is not (
        sequential.R8U_R6_FIXED_CONTINUATION
    )
    source = inspect.getsource(sequential.run_batch_task)
    assert "FULL_SEQUENTIAL_R8U_R7_CONTINUATION_TASK_OUT_OF_SCOPE" in source
    assert "FULL_SEQUENTIAL_R8U_R7_EXTRACTION_CACHE_TOPOLOGY_INVALID" in source
    assert "validate_r8u_r7_frozen_partial_evidence()" in source
    assert "validate_r8u_r7_continuation_worker_submission(" in source


def two_batch_plan() -> tuple[dict[str, Any], core.PlanRequirements]:
    """Return the scientific-path fixture, not a production-scale file test."""

    selected = [
        {"subject_id": str(100_000 + index), "study_id": str(200_000 + index)}
        for index in range(1, 5)
    ]
    split = [
        {"subject_id": row["subject_id"], "split": "train"}
        for row in selected
    ]
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    for ordinal, row in enumerate(selected):
        relative = (
            f"files/p00/p{row['subject_id']}/s{row['study_id']}/"
            f"cine_{ordinal + 1:03d}.dcm"
        )
        payload = f"synthetic-part10-{ordinal}".encode("ascii")
        total_bytes += len(payload)
        sources.append(
            {
                **row,
                "split": "train",
                "production_batch": f"c3_batch_{ordinal // 2:03d}",
                "source_relative_path": relative,
                "source_object_key": hashlib.sha256(
                    f"mimic-iv-echo/1.0\0{relative}".encode("utf-8")
                ).hexdigest(),
                "size_bytes": len(payload),
                "generation": str(1000 + ordinal),
                "md5_base64": base64.b64encode(
                    hashlib.md5(payload, usedforsecurity=False).digest()
                ).decode("ascii"),
                # Structurally valid CRC32C authority; body verification is
                # exercised by the integration test's external-worker seam.
                "crc32c_base64": base64.b64encode(
                    (ordinal + 1).to_bytes(4, "big")
                ).decode("ascii"),
            }
        )
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=4,
        selected_subjects=4,
        normalized_source_objects=4,
        selected_source_bytes=total_bytes,
        batch_count=2,
        studies_per_full_batch=2,
        final_batch_studies=2,
        contract_id="synthetic_two_batch_full_sequential_v1",
    )
    authority = {
        key: ("a" * 40 if key == "git_commit" else hashlib.sha256(key.encode()).hexdigest())
        for key in core.PLAN_AUTHORITY_KEYS
    }
    plan = core.build_immutable_batch_plan(
        selected,
        sources,
        split,
        requirements=requirements,
        authority=authority,
        prespecified_no_cine_studies=[selected[-1]],
    )
    return plan, requirements


def test_generic_production_builder_requires_restricted_no_cine_authority() -> None:
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=core.EXPECTED_PRODUCTION["selected_studies"],
        selected_subjects=core.EXPECTED_PRODUCTION["selected_subjects"],
        normalized_source_objects=core.EXPECTED_PRODUCTION[
            "normalized_source_objects"
        ],
        selected_source_bytes=core.EXPECTED_PRODUCTION[
            "selected_source_bytes"
        ],
        batch_count=core.EXPECTED_PRODUCTION["batch_count"],
        studies_per_full_batch=core.EXPECTED_PRODUCTION[
            "studies_per_full_batch"
        ],
        final_batch_studies=core.EXPECTED_PRODUCTION[
            "final_batch_studies"
        ],
        contract_id=core.EXPECTED_FULL_CONTRACT_ID,
    )
    selected = [
        {"subject_id": str(100_000 + index), "study_id": str(200_000 + index)}
        for index in range(requirements.selected_studies)
    ]
    authority = {
        key: (
            "a" * 40
            if key == "git_commit"
            else hashlib.sha256(f"offline-{key}".encode()).hexdigest()
        )
        for key in core.PLAN_AUTHORITY_KEYS
    }
    with pytest.raises(core.OrchestrationError) as caught:
        core.build_immutable_batch_plan(
            selected,
            [],
            [],
            requirements=requirements,
            authority=authority,
        )
    assert str(caught.value) == (
        "PRESPECIFIED_NO_CINE_PRODUCTION_AUTHORITY_REQUIRED"
    )


def _synthetic_full_launch(
    plan: Mapping[str, Any], requirements: core.PlanRequirements
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "artifact_type": "lvef_c3_full_selected_cohort_launch_authority_v2",
        "status": "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "governing_commit": plan["authority"]["git_commit"],
        "batch_plan_sha256": core.canonical_json_sha256(plan),
        "selected_manifest_sha256": plan["authority"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": plan["authority"][
            "selected_source_manifest_sha256"
        ],
        "split_map_sha256": plan["authority"]["split_map_sha256"],
        "checkpoint_sha256": plan["authority"]["checkpoint_sha256"],
        "selected_studies": requirements.selected_studies,
        "selected_subjects": requirements.selected_subjects,
        "normalized_source_objects": requirements.normalized_source_objects,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_count": requirements.batch_count,
        "expected_no_cine_studies": plan["cohort"][
            "expected_no_cine_studies"
        ],
        "prespecified_no_cine_study_set_sha256": plan["cohort"][
            "prespecified_no_cine_study_set_sha256"
        ],
        "maximum_scheduler_submissions": 2,
        "array_task_range": f"1-{requirements.batch_count}",
        "array_max_concurrency": 1,
        "raw_dicom_deletion_authorized": False,
        "extracted_cache_retirement_authorized_after_preservation": True,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _dynamic_capacity_capture(
    *,
    governing_commit: str = "a" * 40,
    captured_at: datetime | None = None,
    research_quota: int = 3_093_796_556_800,
    research_usage: int = 527_008_808_960,
) -> capacity.DynamicSuccessorCapacityCapture:
    """Return one closed synthetic dynamic receipt without production I/O."""

    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name).resolve()
    tools = root / "tools"
    research = root / "research"
    backed = root / "backed"
    tools.mkdir(mode=0o700)
    research.mkdir(mode=0o700)
    backed.mkdir(mode=0o700)
    for name in ("pquota", "findmnt", "df"):
        executable = tools / name
        executable.write_bytes(b"#!/bin/sh\nexit 97\n")
        executable.chmod(0o700)
    native = tools / "project.quota"
    backed_quota = 53_687_091_200
    backed_usage = 10_946_789_376
    native.write_bytes(
        (
            "rproject_mimicecho root FILESET "
            f"{backed_usage // 1024} {backed_quota // 1024} "
            "0 0 none | 47379 1638400 0 0 none\n"
            "rprojectnb_mimicecho root FILESET "
            f"{research_usage // 1024} {research_quota // 1024} "
            "0 0 none | 501481 33554432 0 0 none\n"
        ).encode("ascii")
    )
    native.chmod(0o600)
    authority = capacity.CurrentCanaryHeadroomAuthority(
        native_quota_path=native,
        pquota_path=tools / "pquota",
        findmnt_path=tools / "findmnt",
        df_path=tools / "df",
        research_path=research,
        backed_path=backed,
    )

    def runner(argv: list[str], **_kwargs: Any) -> Any:
        command = Path(argv[0]).name
        if command == "pquota":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        target = Path(argv[3] if command == "findmnt" else argv[-1])
        role = "research" if target == research else "backed"
        source = f"synthetic:/{role}"
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
            ).encode()
        else:
            available = 1_800_000_000_000 if role == "research" else 20_000_000_000
            stdout = (
                "Filesystem 1B-blocks Used Avail Mounted on\n"
                f"{source} {available + 1} 1 {available} {target}\n"
            ).encode()
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

    def path_identity(path: Path) -> Mapping[str, Any]:
        role = path.name
        return {
            "path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
            "resolved_path_sha256": hashlib.sha256(
                str(path.resolve(strict=True)).encode()
            ).hexdigest(),
            "device": 101 if role == "research" else 202,
            "inode": 303 if role == "research" else 404,
            "is_symlink": False,
        }

    try:
        with mock.patch.object(
            capacity, "_path_identity", side_effect=path_identity
        ):
            return capacity.probe_dynamic_successor_capacity_observation(
                governing_commit=governing_commit,
                active_extraction_caches=0,
                preserved_terminal_failed_extraction_caches=2,
                successor_attempt_root_absent=True,
                successor_claim_absent=True,
                authority=authority,
                process_runner=runner,
                now_utc=captured_at or datetime.now(timezone.utc),
            )
    finally:
        temporary.cleanup()


def _successor_capacity_authority(
    source_capacity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    increment = sequential.SUCCESSOR_INCREMENT_BYTES
    reserve = sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES
    live_usage = (
        int(source_capacity["live_research_usage_bytes"])
        if source_capacity is not None
        and "live_research_usage_bytes" in source_capacity
        else 123_456_789
    )
    projected = live_usage + increment
    research_quota = (
        int(source_capacity["live_research_quota_bytes"])
        if source_capacity is not None
        and "live_research_quota_bytes" in source_capacity
        else projected + reserve
    )
    physical_available = (
        int(source_capacity["live_research_filesystem_available_bytes"])
        if source_capacity is not None
        and "live_research_filesystem_available_bytes" in source_capacity
        else increment + reserve
    )
    file_slots = (
        int(source_capacity["remaining_file_slots"])
        if source_capacity is not None and "remaining_file_slots" in source_capacity
        else sequential.SUCCESSOR_REQUIRED_FILE_SLOTS
    )
    return {
        "schema_version": 2,
        "artifact_type": "lvef_c3_fresh_successor_capacity_authority_v2",
        "status": "PASS_FRESH_SUCCESSOR_WITH_200GB_RESERVE",
        "source_capacity_authority_sha256": (
            core.canonical_json_sha256(source_capacity)
            if source_capacity is not None
            else "a" * 64
        ),
        "source_dynamic_receipt_bytes": (
            int(source_capacity["restricted_receipt_size_bytes"])
            if source_capacity is not None
            and "restricted_receipt_size_bytes" in source_capacity
            else 1_024
        ),
        "source_dynamic_receipt_sha256": (
            str(source_capacity["restricted_receipt_sha256"])
            if source_capacity is not None
            and "restricted_receipt_sha256" in source_capacity
            else "b" * 64
        ),
        "frozen_projected_peak_bytes": (
            sequential.FROZEN_FULL_PLAN_PROJECTED_PEAK_BYTES
        ),
        "frozen_original_current_usage_bytes": (
            sequential.FROZEN_FULL_PLAN_ORIGINAL_CURRENT_USAGE_BYTES
        ),
        "successor_increment_bytes": increment,
        "live_research_usage_bytes": live_usage,
        "projected_total_research_usage_bytes": projected,
        "research_quota_bytes": research_quota,
        "quota_remaining_after_successor_bytes": research_quota - projected,
        "research_filesystem_available_bytes": physical_available,
        "physical_remaining_after_successor_bytes": physical_available - increment,
        "research_file_slots_remaining": file_slots,
        "required_reserve_bytes": reserve,
        "required_remaining_file_slots": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
        "active_extraction_caches": 0,
        "preserved_terminal_failed_extraction_caches": 2,
        "quota_reserve_gate_passed": True,
        "physical_reserve_gate_passed": True,
        "file_slot_gate_passed": True,
        "terminal_failure_cache_gate_passed": True,
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "dicom_body_reads": 0,
        "writes_performed": 0,
    }


def _schema_faithful_scale_plan(
    *, object_count: int = 52_000
) -> tuple[dict[str, Any], core.PlanRequirements]:
    """Build real plan object rows whose canonical form exceeds 16 MB."""

    subject_id = "100001"
    study_id = "200001"
    selected = [{"subject_id": subject_id, "study_id": study_id}]
    split = [{"subject_id": subject_id, "split": "train"}]
    sources: list[dict[str, Any]] = []
    for ordinal in range(object_count):
        relative = (
            f"files/p00/p{subject_id}/s{study_id}/"
            f"cine_{ordinal:06d}.dcm"
        )
        sources.append(
            {
                "subject_id": subject_id,
                "study_id": study_id,
                "split": "train",
                "production_batch": "c3_batch_000",
                "source_relative_path": relative,
                "source_object_key": hashlib.sha256(
                    f"mimic-iv-echo/1.0\0{relative}".encode("utf-8")
                ).hexdigest(),
                "size_bytes": 1,
                "generation": str(ordinal + 1),
                "md5_base64": "AAAAAAAAAAAAAAAAAAAAAA==",
                "crc32c_base64": "AAAAAA==",
            }
        )
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=1,
        selected_subjects=1,
        normalized_source_objects=object_count,
        selected_source_bytes=object_count,
        batch_count=1,
        studies_per_full_batch=1,
        final_batch_studies=1,
        contract_id="schema_faithful_full_plan_scale_v1",
    )
    authority = {
        key: (
            "a" * 40
            if key == "git_commit"
            else hashlib.sha256(key.encode("ascii")).hexdigest()
        )
        for key in core.PLAN_AUTHORITY_KEYS
    }
    plan = core.build_immutable_batch_plan(
        selected,
        sources,
        split,
        requirements=requirements,
        authority=authority,
    )
    assert core.validate_batch_plan(plan, requirements=requirements)
    return plan, requirements


def _write_private_payload(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def _error_code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def test_task_to_batch_is_exact_one_based_plan_mapping() -> None:
    plan, requirements = two_batch_plan()
    assert core.validate_batch_plan(plan, requirements=requirements)
    assert sequential.task_to_batch(1, plan) == "c3_batch_000"
    assert sequential.task_to_batch(2, plan) == "c3_batch_001"

    for invalid in (0, 3, -1, True, "1", 1.0, None):
        with pytest.raises(Exception) as caught:
            sequential.task_to_batch(invalid, plan)  # type: ignore[arg-type]
        assert _error_code(caught.value) == "FULL_SEQUENTIAL_TASK_ID_INVALID"


def test_task_mapping_rejects_plan_order_or_identity_drift() -> None:
    plan, _ = two_batch_plan()
    reversed_plan = {**plan, "batches": list(reversed(plan["batches"]))}
    with pytest.raises(Exception) as caught:
        sequential.task_to_batch(1, reversed_plan)
    assert _error_code(caught.value) == "FULL_SEQUENTIAL_PLAN_ORDER_INVALID"

    duplicate = {
        **plan,
        "batches": [plan["batches"][0], plan["batches"][0]],
    }
    with pytest.raises(Exception) as caught:
        sequential.task_to_batch(2, duplicate)
    assert _error_code(caught.value) == "FULL_SEQUENTIAL_PLAN_ORDER_INVALID"


def test_default_science_dependencies_are_the_shared_production_functions() -> None:
    """The adapter may coordinate stages, but may not copy their science."""

    dependencies = sequential.resolve_dependencies(None)
    assert dependencies.download is core.execute_exact_batch_download
    assert dependencies.dicom is stages.run_production_dicom_extraction
    assert dependencies.echoprime is stages.run_production_echoprime
    assert dependencies.preserve is preservation.preserve_batch

    source = inspect.getsource(sequential)
    assert "mean_pool_study_embeddings" not in source
    assert "mvit_v2_s" not in source
    assert "pixel_array(" not in source
    assert '"model_fitting_authorized": False' in source
    assert '"prediction_authorized": False' in source
    assert '"confirmatory_performance_access_authorized": False' in source


def _scoped_run(
    root: Path,
    plan: Mapping[str, Any],
    requirements: core.PlanRequirements,
) -> sequential.FullRun:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    attempt_id = "lvef_c3_full_" + "1" * 16 + "_" + "a" * 8
    attempt_root = root / "attempts" / attempt_id
    attempt_root.mkdir(mode=0o700, parents=True)
    plan_path = attempt_root / "full_batch_plan.restricted.json"
    core.atomic_write_json_no_clobber(plan_path, plan, attempt_id=attempt_id)
    plan_sha = core.canonical_json_sha256(plan)
    runtime = {
        **plan["authority"],
        "batch_plan_sha256": plan_sha,
        "environment_receipt_sha256": "e" * 64,
        "gcloud_resolution_receipt_sha256": "f" * 64,
        "gcloud_executable_sha256": "1" * 64,
        "crc32c_python_executable_sha256": "2" * 64,
        "crc32c_worker_sha256": "3" * 64,
        "crc32c_distribution_sha256": "4" * 64,
    }
    launch = _synthetic_full_launch(plan, requirements)
    return sequential.FullRun(
        authority=SimpleNamespace(governing_commit="a" * 40),
        plan=plan,
        requirements=requirements,
        contract={},
        contract_path=root / "contract.yaml",
        plan_sha256=plan_sha,
        runtime_authority=runtime,
        attempt_id=attempt_id,
        production_root=root,
        attempt_root=attempt_root,
        plan_path=plan_path,
        launch_authority=launch,
        launch_authority_sha256=core.canonical_json_sha256(launch),
        scheduler_job_identity="synthetic-task",
    )


def test_prior_batch_gate_precedes_every_effect_boundary(
    tmp_path: Path,
) -> None:
    """Task 2 cannot acquire a token, construct transport, or enter science."""

    plan, requirements = two_batch_plan()
    calls: list[str] = []

    def blocked_prior(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        calls.append("prior")
        raise sequential.FullSequentialError("PRIOR_BATCH_NOT_FINALIZED")

    def forbidden(name: str):
        def operation(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
            calls.append(name)
            raise AssertionError(f"{name} crossed before the prior-batch gate")

        return operation

    dependencies = sequential.FullDependencies(
        prior_batch_validator=blocked_prior,
        download=forbidden("download"),
        dicom=forbidden("dicom"),
        echoprime=forbidden("echoprime"),
        preserve=forbidden("preserve"),
        retire=forbidden("retire"),
        finalize_batch=forbidden("finalize_batch"),
        token_provider_factory=forbidden("token"),
        transport_factory=forbidden("transport"),
        digest_provider_factory=forbidden("digest"),
    )
    run = _scoped_run(tmp_path, plan, requirements)
    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential.run_batch_task(task_id=2, run=run, dependencies=dependencies)
    assert caught.value.code == "PRIOR_BATCH_NOT_FINALIZED"
    assert calls == ["prior"]


def test_active_cache_gate_precedes_token_and_body_boundaries(tmp_path: Path) -> None:
    plan, requirements = two_batch_plan()
    run = _scoped_run(tmp_path, plan, requirements)
    calls: list[str] = []

    def forbidden(name: str):
        def operation(*_args: Any, **_kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} crossed the active-cache gate")

        return operation

    dependencies = sequential.FullDependencies(
        environment_validator=forbidden("environment"),
        token_provider_factory=forbidden("token"),
        transport_factory=forbidden("transport"),
        download=forbidden("download"),
    )
    with (
        mock.patch.object(
            sequential,
            "_extraction_cache_inventory",
            return_value=sequential.ExtractionCacheInventory(
                active=1, preserved_terminal_failed=0
            ),
        ),
        pytest.raises(sequential.FullSequentialError) as caught,
    ):
        sequential.run_batch_task(task_id=1, run=run, dependencies=dependencies)
    assert caught.value.code == "FULL_SEQUENTIAL_ACTIVE_EXTRACTION_CACHE_PRESENT"
    assert calls == []


def _write_terminal_dicom_failure(partial: Path) -> None:
    partial.mkdir(mode=0o700, parents=True)
    summary = partial / "failure.summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "FAIL_DICOM_OR_PIXEL_DECODE_GATE",
                "n_objects": 10,
                "n_studies": 2,
                "n_readable": 10,
                "n_unreadable": 0,
                "n_multiframe_candidates": 6,
                "n_single_frame": 4,
                "n_pixel_decode_failures": 1,
                "physical_source_keys_unique": True,
                "identifiers_emitted": False,
                "paths_emitted": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(summary, 0o600)


def test_foreign_closed_failure_cache_is_preserved_but_never_current_or_final() -> None:
    current = "lvef_c3_full_1111111111111111_aaaaaaaa"
    prior = "lvef_c3_full_2222222222222222_bbbbbbbb"
    with tempfile.TemporaryDirectory() as directory:
        production = Path(directory).resolve()
        prior_batch = (
            production / "attempts" / prior / "extracted_cache" / "c3_batch_001"
        )
        partial = prior_batch / "dicom_extraction.partial"
        _write_terminal_dicom_failure(partial)
        inventory = sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        )
        assert inventory == sequential.ExtractionCacheInventory(
            active=0, preserved_terminal_failed=1
        )

        # The same exact tree is active when it belongs to the current attempt.
        current_production = production / "current_case"
        current_partial = (
            current_production
            / "attempts"
            / current
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(current_partial)
        current_inventory = sequential._extraction_cache_inventory(
            current_production, current_attempt_id=current
        )
        assert current_inventory == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        # A foreign finalized clip cache remains a live/blocking cache even if
        # a closed partial failure summary also exists.
        clips = prior_batch / "dicom_extraction" / "clips"
        clips.mkdir(mode=0o700, parents=True)
        final_inventory = sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        )
        assert final_inventory == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )


def test_malformed_or_nonprivate_foreign_failure_summary_remains_active() -> None:
    current = "lvef_c3_full_1111111111111111_aaaaaaaa"
    prior = "lvef_c3_full_2222222222222222_bbbbbbbb"
    with tempfile.TemporaryDirectory() as directory:
        production = Path(directory).resolve()
        partial = (
            production
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(partial)
        summary = partial / "failure.summary.json"
        value = json.loads(summary.read_text(encoding="utf-8"))
        value["unexpected"] = 1
        summary.write_text(json.dumps(value) + "\n", encoding="utf-8")
        os.chmod(summary, 0o600)
        assert sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        malformed_attempt_root = production / "malformed_attempt_case"
        malformed_partial = (
            malformed_attempt_root
            / "attempts"
            / "foreign_attempt_name"
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(malformed_partial)
        assert sequential._extraction_cache_inventory(
            malformed_attempt_root, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        symlink_root = production / "unsafe_nested_symlink_case"
        symlink_partial = (
            symlink_root
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(symlink_partial)
        nested = symlink_partial / "clips"
        nested.mkdir(mode=0o700)
        (nested / "unsafe_alias").symlink_to(
            symlink_partial / "failure.summary.json"
        )
        assert sequential._extraction_cache_inventory(
            symlink_root, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        nonprivate_root = production / "unsafe_nested_mode_case"
        nonprivate_partial = (
            nonprivate_root
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(nonprivate_partial)
        unsafe_file = nonprivate_partial / "unexpected.restricted.json"
        unsafe_file.write_text("{}\n", encoding="utf-8")
        os.chmod(unsafe_file, 0o640)
        assert sequential._extraction_cache_inventory(
            nonprivate_root, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        hardlink_root = production / "unsafe_hardlink_case"
        hardlink_partial = (
            hardlink_root
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_terminal_dicom_failure(hardlink_partial)
        hardlink_summary = hardlink_partial / "failure.summary.json"
        os.link(hardlink_summary, hardlink_partial / "hardlinked-evidence.json")
        assert sequential._extraction_cache_inventory(
            hardlink_root, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )

        value.pop("unexpected")
        summary.write_text(json.dumps(value) + "\n", encoding="utf-8")
        os.chmod(summary, 0o644)
        assert sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )


def _closed_v2_extraction_provenance() -> dict[str, Any]:
    value: dict[str, Any] = {
        "audit": "prospective_cine_extraction",
        "status": "FAIL",
        "temporal_sampling_policy": (
            "historical_compatible_linspace_or_tail_repeat_v1"
        ),
        "temporal_fallback_policy": "stride2_signal_coverage_pair_repeat_v1",
        "temporal_sampling_long_cine_rule": (
            "endpoint_inclusive_integer_linspace"
        ),
        "temporal_sampling_short_cine_rule": (
            "ordered_source_frames_then_repeat_final_frame"
        ),
    }
    value.update({key: 0 for key in sequential.EXTRACTION_PROVENANCE_INTEGER_KEYS})
    value.update({key: False for key in sequential.EXTRACTION_PROVENANCE_BOOLEAN_KEYS})
    value.update({key: {} for key in sequential.EXTRACTION_PROVENANCE_COUNT_MAP_KEYS})
    value["preprocessing_gate_state_counts"] = {
        gate: {"PASS": 0, "FAIL": 0, "NOT_EVALUATED": 1, "INVALID": 0}
        for gate in sequential.EXTRACTION_PROVENANCE_GATE_STATE_KEYS
    }
    value.update(
        {
            "n_requested_cines": 1,
            "n_failed_cines": 1,
            "fallback_status_counts": {"FALLBACK_PATH_FAILED": 1},
            "failure_substage_counts": {
                "SAMPLED_NONZERO_SIGNAL_FAILURE": 1
            },
            "decode_color_status_counts": {"PASS": 1},
        }
    )
    return value


def test_foreign_closed_v2_failure_summaries_are_preserved() -> None:
    current = "lvef_c3_full_1111111111111111_aaaaaaaa"
    prior = "lvef_c3_full_2222222222222222_bbbbbbbb"
    with tempfile.TemporaryDirectory() as directory:
        production = Path(directory).resolve()
        partial = (
            production
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        partial.mkdir(mode=0o700, parents=True)
        summary_path = partial / "failure.summary.json"
        extraction_summary = {
            "schema_version": 2,
            "artifact_type": "lvef_c3_batch_extraction_failure_summary_v2",
            "status": "FAIL_EXTRACTION_GATE",
            "error_code": "EXTRACTION_SAMPLED_NONZERO_SIGNAL_FAILURE",
            "extraction_provenance": _closed_v2_extraction_provenance(),
            "identifiers_emitted": False,
            "paths_emitted": False,
        }
        summary_path.write_text(
            json.dumps(extraction_summary, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(summary_path, 0o600)
        assert sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=0, preserved_terminal_failed=1
        )

        dicom_summary = {
            "schema_version": 2,
            "artifact_type": (
                "lvef_c3_batch_dicom_or_extraction_failure_summary_v2"
            ),
            "status": "FAIL_DICOM_OR_PIXEL_DECODE_GATE",
            "n_objects": 10,
            "n_studies": 2,
            "n_readable": 10,
            "n_unreadable": 0,
            "n_multiframe_candidates": 6,
            "n_single_frame": 4,
            "n_pixel_decode_failures": 1,
            "physical_source_keys_unique": True,
            "extraction_provenance": _closed_v2_extraction_provenance(),
            "identifiers_emitted": False,
            "paths_emitted": False,
        }
        summary_path.write_text(
            json.dumps(dicom_summary, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(summary_path, 0o600)
        assert sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=0, preserved_terminal_failed=1
        )


def test_v2_failure_summary_with_unclosed_nested_provenance_remains_active() -> None:
    current = "lvef_c3_full_1111111111111111_aaaaaaaa"
    prior = "lvef_c3_full_2222222222222222_bbbbbbbb"
    with tempfile.TemporaryDirectory() as directory:
        production = Path(directory).resolve()
        partial = (
            production
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        partial.mkdir(mode=0o700, parents=True)
        provenance = _closed_v2_extraction_provenance()
        provenance["decoder_backend_counts"] = {"123456": 0}
        summary = partial / "failure.summary.json"
        summary.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "artifact_type": (
                        "lvef_c3_batch_extraction_failure_summary_v2"
                    ),
                    "status": "FAIL_EXTRACTION_GATE",
                    "error_code": "EXTRACTION_SAMPLED_NONZERO_SIGNAL_FAILURE",
                    "extraction_provenance": provenance,
                    "identifiers_emitted": False,
                    "paths_emitted": False,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(summary, 0o600)
        assert sequential._extraction_cache_inventory(
            production, current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=1, preserved_terminal_failed=0
        )


def test_installation_tracks_replay_entrypoint_without_effects() -> None:
    replay = sequential.SCRIPT_ROOT / (
        "replay_lvef_c3_failed_extraction_one_object.py"
    )
    assert replay.is_file() and not replay.is_symlink()
    with (
        mock.patch.object(sequential, "_current_commit", return_value="a" * 40),
        mock.patch.object(
            sequential.minimal, "_validate_two_runtime_installation"
        ) as runtime,
    ):
        result = sequential.validate_installation()
    runtime.assert_called_once_with(repository=sequential.REPOSITORY_ROOT)
    assert result["status"] == "PASS_FULL_C3_INSTALLATION"
    assert result["cloud_requests"] == 0
    assert result["qsub_submissions"] == 0
    assert result["dicom_body_reads"] == 0
    assert result["gpu_executions"] == 0


def test_plan_object_and_byte_mutations_fail_closed() -> None:
    plan, requirements = two_batch_plan()
    changed_bytes = {
        **plan,
        "cohort": {**plan["cohort"], "selected_source_bytes": plan["cohort"]["selected_source_bytes"] + 1},
    }
    with pytest.raises(core.OrchestrationError) as caught:
        core.validate_batch_plan(changed_bytes, requirements=requirements)
    assert _error_code(caught.value) == "BATCH_PLAN_COHORT_CONSTANT_MISMATCH"

    missing = {
        **plan,
        "batches": [
            {**plan["batches"][0], "objects": plan["batches"][0]["objects"][:-1]},
            plan["batches"][1],
        ],
    }
    with pytest.raises(core.OrchestrationError):
        core.validate_batch_plan(missing, requirements=requirements)


def test_direct_full_launch_scope_is_plan_exact_and_scientifically_closed() -> None:
    plan, requirements = two_batch_plan()
    plan_sha = core.canonical_json_sha256(plan)
    ledger = core.initialize_resume_ledger(
        plan,
        requirements=requirements,
        attempt_id="lvef_c3_full_" + "2" * 16 + "_" + "a" * 8,
        authority={**plan["authority"], "batch_plan_sha256": plan_sha},
        batch_ids=["c3_batch_000"],
    )
    launch = _synthetic_full_launch(plan, requirements)
    launch_sha = core.canonical_json_sha256(launch)
    with pytest.raises(core.OrchestrationError) as caught:
        core.validate_direct_full_download_scope(
            launch_authority=launch,
            ledger=ledger,
            plan=plan,
            requirements=requirements,
            batch_id="c3_batch_000",
            maximum_attempts_per_object=5,
            expected_launch_authority_sha256=launch_sha,
        )
    assert _error_code(caught.value) == "DIRECT_FULL_DOWNLOAD_SCOPE_INVALID"
    core.validate_direct_full_download_scope(
        launch_authority=launch,
        ledger=ledger,
        plan=plan,
        requirements=requirements,
        batch_id="c3_batch_000",
        maximum_attempts_per_object=5,
        expected_launch_authority_sha256=launch_sha,
        test_only_synthetic_full_scope=True,
    )
    for key in (
        "raw_dicom_deletion_authorized",
        "model_fitting_authorized",
        "prediction_authorized",
        "confirmatory_performance_access_authorized",
    ):
        changed = {**launch, key: True}
        with pytest.raises(core.OrchestrationError) as caught:
            core.validate_direct_full_download_scope(
                launch_authority=changed,
                ledger=ledger,
                plan=plan,
                requirements=requirements,
                batch_id="c3_batch_000",
                maximum_attempts_per_object=5,
                expected_launch_authority_sha256=core.canonical_json_sha256(changed),
                test_only_synthetic_full_scope=True,
            )
        assert _error_code(caught.value) == "DIRECT_FULL_DOWNLOAD_SCOPE_INVALID"


def _claimed_run_fixture(
    root: Path,
    *,
    qsub_environment_sha256: str = BOUND_QSUB_ENVIRONMENT_SHA256,
) -> tuple[
    sequential.FullRun, dict[str, Any], Path
]:
    plan, requirements = two_batch_plan()
    run = _scoped_run(root, plan, requirements)
    dynamic_capture = _dynamic_capacity_capture(
        governing_commit=run.authority.governing_commit
    )
    capacity_value = _successor_capacity_authority(
        dynamic_capture.observation
    )
    capacity_path = run.attempt_root / "full_capacity_receipt.restricted.json"
    dynamic_path = (
        run.attempt_root / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
    )
    sequential._write_private_json(
        run.attempt_root / "full_launch_authority.restricted.json",
        run.launch_authority,
        attempt_id=run.attempt_id,
    )
    sequential._write_private_json(
        capacity_path, capacity_value, attempt_id=run.attempt_id
    )
    capacity.write_dynamic_successor_capacity_receipt_payload(
        dynamic_path,
        dynamic_capture.receipt_payload,
        expected_governing_commit=run.authority.governing_commit,
    )
    claim = sequential._expected_submission_claim(
        run,
        capacity_receipt_sha256=core.sha256_file(capacity_path),
        dynamic_capacity_receipt_sha256=hashlib.sha256(
            dynamic_capture.receipt_payload
        ).hexdigest(),
        capacity_evidence_role="R5B_HISTORICAL",
        capacity_gain_source="ALLOCATION",
        raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
        raw_retirement_receipt_sha256=(
            "NOT_APPLICABLE_CLEANUP_SKIPPED"
        ),
        qsub_environment_sha256=qsub_environment_sha256,
    )
    claim_path = run.attempt_root / "full_submission_claim.restricted.json"
    sequential._write_private_json(
        claim_path, claim, attempt_id=run.attempt_id
    )
    return run, capacity_value, claim_path


def _r5e_r8_claimed_run_fixture(
    root: Path,
    *,
    research_usage: int = 527_008_808_960,
) -> tuple[
    sequential.FullRun,
    capacity.DynamicSuccessorCapacityCapture,
    Path,
]:
    plan, requirements = two_batch_plan()
    run = _scoped_run(root, plan, requirements)
    run = replace(
        run,
        requirements=replace(
            requirements, contract_id=core.EXPECTED_FULL_CONTRACT_ID
        ),
    )
    dynamic_capture = _dynamic_capacity_capture(
        governing_commit=run.authority.governing_commit,
        research_usage=research_usage,
    )
    capacity_value = _successor_capacity_authority(
        dynamic_capture.observation
    )
    launch_path = (
        run.attempt_root / "full_launch_authority.restricted.json"
    )
    capacity_path = (
        run.attempt_root / "full_capacity_receipt.restricted.json"
    )
    dynamic_path = (
        run.attempt_root / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
    )
    sequential._write_private_json(
        launch_path, run.launch_authority, attempt_id=run.attempt_id
    )
    sequential._write_private_json(
        capacity_path, capacity_value, attempt_id=run.attempt_id
    )
    capacity.write_dynamic_successor_capacity_receipt_payload(
        dynamic_path,
        dynamic_capture.receipt_payload,
        expected_governing_commit=run.authority.governing_commit,
    )
    claim = sequential._expected_submission_claim(
        run,
        capacity_receipt_sha256=core.sha256_file(capacity_path),
        dynamic_capacity_receipt_sha256=hashlib.sha256(
            dynamic_capture.receipt_payload
        ).hexdigest(),
        capacity_evidence_role="R5E_R8_POST_CLEANUP",
        capacity_gain_source=sequential._sealed_r5e_r8_gain_source(
            dynamic_capture.observation
        ),
        raw_retirement_status=sequential.RETIREMENT_EVENT_STATUS,
        raw_retirement_receipt_sha256=(
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        ),
        qsub_environment_sha256=BOUND_QSUB_ENVIRONMENT_SHA256,
    )
    claim_path = run.attempt_root / "full_submission_claim.restricted.json"
    sequential._write_private_json(
        claim_path, claim, attempt_id=run.attempt_id
    )
    return run, dynamic_capture, claim_path


def test_r5e_r8_attempt_local_replay_is_shared_by_readback_array_and_finalizer(
) -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        run, current_post, claim_path = _r5e_r8_claimed_run_fixture(
            Path(raw_root).resolve()
        )

        def materialized_run(*, scheduler_job_identity: str = "NO_BODY"):
            return replace(
                run, scheduler_job_identity=scheduler_job_identity
            )

        with (
            mock.patch.object(
                sequential,
                "_load_fixed_capacity_admission",
                side_effect=AssertionError("attempt replay reopened admission"),
            ) as fixed_loader,
            mock.patch.object(
                sequential,
                "_load_historical_r5e_r2_pre_action_capacity",
                side_effect=AssertionError("attempt replay reopened f201"),
            ) as historical_pre,
            mock.patch.object(
                sequential,
                "_load_historical_r5e_post_cleanup_capacity",
                side_effect=AssertionError("attempt replay reopened 1fc"),
            ) as historical_post,
            mock.patch.object(
                sequential,
                "_load_current_r5e_r8_post_cleanup_capacity",
                side_effect=AssertionError("attempt replay reopened live R8"),
            ) as current_r8,
            mock.patch.object(
                sequential,
                "_load_completed_retirement_event",
                side_effect=AssertionError("attempt replay reopened retirement"),
            ) as retirement,
            mock.patch.object(
                sequential, "build_full_run", side_effect=materialized_run
            ),
            mock.patch.object(
                sequential,
                "_validate_completed_canary_evidence",
                return_value={"status": "PASS"},
            ),
            mock.patch.object(sequential, "_validate_full_run"),
            mock.patch.object(
                sequential, "_load_full_batch_plan", return_value=run.plan
            ),
            mock.patch.object(
                capacity,
                "probe_dynamic_successor_capacity_observation",
                side_effect=AssertionError("attempt replay ran live capacity"),
            ) as live_capacity,
            mock.patch.object(
                capacity,
                "_path_identity",
                side_effect=AssertionError("attempt replay compared path identity"),
            ) as path_identity,
            mock.patch.object(
                capacity,
                "_validate_current_canary_headroom_authority",
                side_effect=AssertionError("attempt replay checked live paths"),
            ) as live_paths,
            mock.patch.object(
                capacity,
                "_validate_pquota_restricted_mount_reconciliation",
                side_effect=AssertionError("attempt replay checked live mounts"),
            ) as live_mounts,
            mock.patch.object(stages, "run_production_dicom_extraction") as dicom,
            mock.patch.object(stages, "run_production_echoprime") as gpu,
            mock.patch.object(core, "execute_exact_batch_download") as cloud,
        ):
            assert sequential._load_bound_submission_environment_sha256(
                run
            ) == BOUND_QSUB_ENVIRONMENT_SHA256
            array_run = sequential._adopt_claimed_run(
                scheduler_job_identity="8123456"
            )
            finalizer_run = sequential._adopt_claimed_run(
                scheduler_job_identity="8123457"
            )

        assert array_run.scheduler_job_identity == "8123456"
        assert finalizer_run.scheduler_job_identity == "8123457"
        for forbidden in (
            fixed_loader,
            historical_pre,
            historical_post,
            current_r8,
            retirement,
            live_capacity,
            path_identity,
            live_paths,
            live_mounts,
            dicom,
            gpu,
            cloud,
        ):
            forbidden.assert_not_called()
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        current_sha256 = hashlib.sha256(
            current_post.receipt_payload
        ).hexdigest()
        assert claim["capacity_evidence_role"] == "R5E_R8_POST_CLEANUP"
        assert claim["dynamic_capacity_receipt_sha256"] == current_sha256
        assert claim["dynamic_capacity_receipt_sha256"] != (
            sequential.HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256
        )
        assert claim["raw_retirement_receipt_sha256"] == (
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        )
        _replace_private_json(
            claim_path,
            {
                **claim,
                "dynamic_capacity_receipt_sha256": (
                    sequential.HISTORICAL_R5E_R2_PRE_ACTION_RECEIPT_SHA256
                ),
            },
        )
        with pytest.raises(sequential.FullSequentialError) as caught:
            sequential._load_bound_submission_environment_sha256(run)
        assert caught.value.code == (
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
        )


def test_r5e_r8_attempt_local_dynamic_claim_retirement_and_qsub_tamper_codes(
) -> None:
    cases = (
        (
            "dynamic",
            "FULL_SEQUENTIAL_ATTEMPT_LOCAL_CAPACITY_MISMATCH",
        ),
        (
            "claim_dynamic_hash",
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH",
        ),
        (
            "retirement_hash",
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH",
        ),
        (
            "qsub_environment_hash",
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH",
        ),
    )
    for role, expected in cases:
        with tempfile.TemporaryDirectory() as raw_root:
            run, _capture, claim_path = _r5e_r8_claimed_run_fixture(
                Path(raw_root).resolve()
            )
            if role == "dynamic":
                dynamic_path = (
                    run.attempt_root
                    / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
                )
                _write_private_payload(
                    dynamic_path, dynamic_path.read_bytes() + b" "
                )
            else:
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
                key = {
                    "claim_dynamic_hash": "dynamic_capacity_receipt_sha256",
                    "retirement_hash": "raw_retirement_receipt_sha256",
                    "qsub_environment_hash": "qsub_environment_sha256",
                }[role]
                _replace_private_json(
                    claim_path,
                    {
                        **claim,
                        key: (
                            "8" * 64
                            if role == "qsub_environment_hash"
                            else "0" * 64
                        ),
                    },
                )
            with (
                mock.patch.object(
                    sequential,
                    "_load_fixed_capacity_admission",
                    side_effect=AssertionError("replay reopened global capacity"),
                ) as global_capacity,
                mock.patch.object(
                    capacity,
                    "probe_dynamic_successor_capacity_observation",
                    side_effect=AssertionError("replay ran live capacity"),
                ) as live_capacity,
                pytest.raises(sequential.FullSequentialError) as caught,
            ):
                sequential._load_bound_submission_environment_sha256(
                    run,
                    expected_qsub_environment_sha256=(
                        BOUND_QSUB_ENVIRONMENT_SHA256
                    ),
                )
            assert caught.value.code == expected
            global_capacity.assert_not_called()
            live_capacity.assert_not_called()


def test_r5e_r8_claim_gain_source_substitution_fails_against_local_receipt(
) -> None:
    cases = (
        (300_000_000_000, "CLEANUP", "BOTH"),
        (527_008_808_960, "BOTH", "CLEANUP"),
    )
    for research_usage, original_gain, substituted_gain in cases:
        with tempfile.TemporaryDirectory() as raw_root:
            run, capture_value, claim_path = _r5e_r8_claimed_run_fixture(
                Path(raw_root).resolve(), research_usage=research_usage
            )
            dynamic_path = (
                run.attempt_root
                / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
            )
            capacity_path = (
                run.attempt_root
                / "full_capacity_receipt.restricted.json"
            )
            sealed_before = (
                dynamic_path.read_bytes(), capacity_path.read_bytes()
            )
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            assert sequential._sealed_r5e_r8_gain_source(
                capture_value.observation
            ) == original_gain
            assert claim["capacity_gain_source"] == original_gain
            _replace_private_json(
                claim_path,
                {**claim, "capacity_gain_source": substituted_gain},
            )
            substituted = json.loads(
                claim_path.read_text(encoding="utf-8")
            )
            assert {
                key for key in claim if claim[key] != substituted[key]
            } == {"capacity_gain_source"}

            with (
                mock.patch.object(
                    sequential,
                    "_load_fixed_capacity_admission",
                    side_effect=AssertionError(
                        "claim replay reopened global capacity"
                    ),
                ) as global_capacity,
                mock.patch.object(
                    capacity,
                    "probe_dynamic_successor_capacity_observation",
                    side_effect=AssertionError(
                        "claim replay ran live capacity"
                    ),
                ) as live_capacity,
                pytest.raises(sequential.FullSequentialError) as caught,
            ):
                sequential._load_bound_submission_environment_sha256(
                    run,
                    expected_qsub_environment_sha256=(
                        BOUND_QSUB_ENVIRONMENT_SHA256
                    ),
                )
            assert caught.value.code == (
                "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
            )
            global_capacity.assert_not_called()
            live_capacity.assert_not_called()
            assert (
                dynamic_path.read_bytes(), capacity_path.read_bytes()
            ) == sealed_before


def _write_historical_capacity_pair(
    owner_private: Path,
    capture_value: capacity.DynamicSuccessorCapacityCapture,
    *,
    receipt_basename: str,
    summary_basename: str,
) -> tuple[bytes, bytes]:
    receipt_payload = capture_value.receipt_payload
    summary_payload = capacity._canonical(capture_value.observation)
    _write_private_payload(owner_private / receipt_basename, receipt_payload)
    _write_private_payload(owner_private / summary_basename, summary_payload)
    return receipt_payload, summary_payload


def _historical_constant_overrides(
    prefix: str, receipt_payload: bytes, summary_payload: bytes
) -> dict[str, object]:
    return {
        f"{prefix}_RECEIPT_BYTES": len(receipt_payload),
        f"{prefix}_RECEIPT_SHA256": hashlib.sha256(
            receipt_payload
        ).hexdigest(),
        f"{prefix}_SUMMARY_BYTES": len(summary_payload),
        f"{prefix}_SUMMARY_SHA256": hashlib.sha256(
            summary_payload
        ).hexdigest(),
    }


def test_r5e_r8_fixed_f201_and_1fc_loaders_replay_only_sealed_event_state(
) -> None:
    f201_time = datetime.fromisoformat(
        sequential.HISTORICAL_R5E_R2_PRE_ACTION_CAPTURED_AT_UTC.replace(
            "Z", "+00:00"
        )
    )
    post_time = datetime.fromisoformat(
        sequential.HISTORICAL_R5E_POST_CLEANUP_CAPTURED_AT_UTC.replace(
            "Z", "+00:00"
        )
    )
    f201 = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT,
        captured_at=f201_time,
        research_quota=2_100_000_000_000,
    )
    post = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_POST_CLEANUP_COMMIT,
        captured_at=post_time,
    )
    assert f201.observation["status"] in {
        capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        capacity.DYNAMIC_SUCCESSOR_STATUS_BLOCKED,
    }
    assert post.observation["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    with tempfile.TemporaryDirectory() as raw_root:
        production = Path(raw_root).resolve()
        owner_private = production / "owner_private"
        owner_private.mkdir(mode=0o700)
        f201_receipt, f201_summary = _write_historical_capacity_pair(
            owner_private,
            f201,
            receipt_basename=(
                capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        post_receipt, post_summary = _write_historical_capacity_pair(
            owner_private,
            post,
            receipt_basename=(
                capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        constants = {
            **_historical_constant_overrides(
                "HISTORICAL_R5E_R2_PRE_ACTION",
                f201_receipt,
                f201_summary,
            ),
            **_historical_constant_overrides(
                "HISTORICAL_R5E_POST_CLEANUP",
                post_receipt,
                post_summary,
            ),
        }
        with (
            mock.patch.object(sequential, "PRODUCTION_ROOT", production),
            mock.patch.multiple(sequential, **constants),
            mock.patch.object(
                capacity,
                "validate_production_dynamic_successor_capacity_capture",
                side_effect=AssertionError("sealed replay called LIVE wrapper"),
            ) as legacy_live,
            mock.patch.object(
                capacity,
                "_path_identity",
                side_effect=AssertionError("sealed replay compared path identity"),
            ) as path_identity,
            mock.patch.object(
                capacity,
                "_read_regular",
                side_effect=AssertionError("sealed replay read executable bytes"),
            ) as executable_read,
            mock.patch.object(
                capacity,
                "_validate_pquota_restricted_mount_reconciliation",
                side_effect=AssertionError("sealed replay checked live mounts"),
            ) as mounts,
        ):
            loaded_f201 = (
                sequential._load_historical_r5e_r2_pre_action_capacity()
            )
            loaded_post = (
                sequential._load_historical_r5e_post_cleanup_capacity()
            )
        assert loaded_f201.capture.receipt_payload == f201_receipt
        assert loaded_f201.capture.observation == f201.observation
        assert loaded_post.capture.receipt_payload == post_receipt
        assert loaded_post.capture.observation == post.observation
        assert inspect.signature(
            sequential._load_historical_r5e_r2_pre_action_capacity
        ).parameters == {}
        assert inspect.signature(
            sequential._load_historical_r5e_post_cleanup_capacity
        ).parameters == {}
        for forbidden in (
            legacy_live, path_identity, executable_read, mounts
        ):
            forbidden.assert_not_called()


def test_r5e_r8_historical_loader_file_hash_commit_time_and_schema_errors(
) -> None:
    fixed_time_text = sequential.HISTORICAL_R5E_POST_CLEANUP_CAPTURED_AT_UTC
    fixed_time = datetime.fromisoformat(
        fixed_time_text.replace("Z", "+00:00")
    )

    def run_case(
        label: str,
        capture_value: capacity.DynamicSuccessorCapacityCapture | None,
        expected: str,
        *,
        captured_at_override: str | None = None,
        hash_override: str | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            production = Path(raw_root).resolve()
            owner_private = production / "owner_private"
            owner_private.mkdir(mode=0o700)
            constants: dict[str, object] = {
                "HISTORICAL_R5E_POST_CLEANUP_COMMIT": (
                    sequential.HISTORICAL_R5E_POST_CLEANUP_COMMIT
                )
            }
            if capture_value is not None:
                receipt_payload, summary_payload = (
                    _write_historical_capacity_pair(
                        owner_private,
                        capture_value,
                        receipt_basename=(
                            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
                        ),
                        summary_basename=(
                            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
                        ),
                    )
                )
                constants.update(
                    _historical_constant_overrides(
                        "HISTORICAL_R5E_POST_CLEANUP",
                        receipt_payload,
                        summary_payload,
                    )
                )
            if captured_at_override is not None:
                constants[
                    "HISTORICAL_R5E_POST_CLEANUP_CAPTURED_AT_UTC"
                ] = captured_at_override
            if hash_override is not None:
                constants["HISTORICAL_R5E_POST_CLEANUP_RECEIPT_SHA256"] = (
                    hash_override
                )
            with (
                mock.patch.object(sequential, "PRODUCTION_ROOT", production),
                mock.patch.multiple(sequential, **constants),
                pytest.raises(sequential.FullSequentialError) as caught,
            ):
                sequential._load_historical_r5e_post_cleanup_capacity()
            assert caught.value.code == expected, label

    run_case(
        "missing",
        None,
        "FULL_SEQUENTIAL_HISTORICAL_EVENT_FILE_INVALID",
    )
    valid = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_POST_CLEANUP_COMMIT,
        captured_at=fixed_time,
    )
    run_case(
        "hash",
        valid,
        "FULL_SEQUENTIAL_HISTORICAL_EVENT_HASH_MISMATCH",
        hash_override="0" * 64,
    )
    wrong_commit = _dynamic_capacity_capture(
        governing_commit="b" * 40,
        captured_at=fixed_time,
    )
    run_case(
        "commit",
        wrong_commit,
        "FULL_SEQUENTIAL_HISTORICAL_EVENT_COMMIT_INVALID",
    )
    run_case(
        "time",
        valid,
        "FULL_SEQUENTIAL_HISTORICAL_EVENT_COMMIT_INVALID",
        captured_at_override="2026-08-20T18:20:00Z",
    )
    blocked = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_POST_CLEANUP_COMMIT,
        captured_at=fixed_time,
        research_quota=2_100_000_000_000,
    )
    run_case(
        "schema-status",
        blocked,
        "FULL_SEQUENTIAL_HISTORICAL_EVENT_SCHEMA_INVALID",
    )


def test_r5e_r8_current_loader_is_fixed_live_fresh_and_role_strict() -> None:
    governing_commit = "a" * 40
    current = _dynamic_capacity_capture(governing_commit=governing_commit)
    blocked = _dynamic_capacity_capture(
        governing_commit=governing_commit,
        research_quota=2_100_000_000_000,
    )
    with tempfile.TemporaryDirectory() as raw_root:
        production = Path(raw_root).resolve()
        owner_private = production / "owner_private"
        owner_private.mkdir(mode=0o700)
        _write_historical_capacity_pair(
            owner_private,
            current,
            receipt_basename=(
                capacity.R5E_R8_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME
            ),
            summary_basename=(
                capacity.R5E_R8_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME
            ),
        )
        run = SimpleNamespace(
            production_root=production,
            authority=SimpleNamespace(governing_commit=governing_commit),
        )

        def strict_context(
            capture_value: capacity.DynamicSuccessorCapacityCapture,
            **kwargs: object,
        ) -> capacity.DynamicSuccessorCapacityCapture:
            assert capture_value.receipt_payload == current.receipt_payload
            assert kwargs == {
                "validation_context": capacity.LIVE_PRECLAIM_ADMISSION,
                "expected_governing_commit": governing_commit,
            }
            return capture_value

        with mock.patch.object(
            capacity,
            "validate_dynamic_successor_capacity_capture",
            side_effect=strict_context,
        ) as validator:
            loaded = sequential._load_current_r5e_r8_post_cleanup_capacity(
                run
            )
        assert loaded.receipt_payload == current.receipt_payload
        validator.assert_called_once()

        with (
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                side_effect=capacity.PostReallocationCapacityError(
                    "DYNAMIC_CAPACITY_RECEIPT_STALE"
                ),
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            sequential._load_current_r5e_r8_post_cleanup_capacity(run)
        assert caught.value.code == "FULL_SEQUENTIAL_CURRENT_CAPACITY_STALE"

        with (
            mock.patch.object(
                capacity,
                "load_dynamic_successor_capacity_capture",
                return_value=blocked,
            ),
            mock.patch.object(
                capacity,
                "validate_dynamic_successor_capacity_capture",
                return_value=blocked,
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            sequential._load_current_r5e_r8_post_cleanup_capacity(run)
        assert caught.value.code == (
            "FULL_SEQUENTIAL_CURRENT_CAPACITY_NOT_PASS"
        )


def test_r5e_r8_old_1fc_pair_cannot_bind_current_production_admission() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        production = Path(raw_root).resolve()
        plan, requirements = two_batch_plan()
        run = _scoped_run(production, plan, requirements)
        run = replace(
            run,
            authority=SimpleNamespace(
                governing_commit=run.authority.governing_commit,
                environment_receipt=production / "environment.restricted.json",
            ),
            requirements=replace(
                requirements, contract_id=core.EXPECTED_FULL_CONTRACT_ID
            ),
        )
        owner_private = production / "owner_private"
        owner_private.mkdir(mode=0o700)
        for basename in (
            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ):
            _write_private_payload(owner_private / basename, b"sealed-1fc\n")
        with (
            mock.patch.object(sequential, "PRODUCTION_ROOT", production),
            mock.patch.object(
                sequential,
                "_load_historical_r5e_r2_pre_action_capacity",
                return_value=object(),
            ) as f201,
            mock.patch.object(
                sequential,
                "_load_historical_r5e_post_cleanup_capacity",
                return_value=object(),
            ) as old_post,
            mock.patch.object(
                sequential, "_load_current_r5e_r8_post_cleanup_capacity"
            ) as current_r8,
            mock.patch.object(
                sequential, "_load_completed_retirement_event"
            ) as retirement,
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            sequential._load_fixed_capacity_admission(run)
        assert caught.value.code == "FULL_SEQUENTIAL_CURRENT_CAPACITY_FILE_INVALID"
        f201.assert_called_once_with()
        old_post.assert_called_once_with()
        current_r8.assert_not_called()
        retirement.assert_not_called()
        assert not any(
            path.name.startswith("r5e_r8_post_cleanup")
            for path in owner_private.iterdir()
        )

        with pytest.raises(sequential.FullSequentialError) as caught:
            sequential._expected_submission_claim(
                run,
                capacity_receipt_sha256="1" * 64,
                dynamic_capacity_receipt_sha256="2" * 64,
                capacity_evidence_role="R5E_POST_CLEANUP",
                capacity_gain_source="CLEANUP",
                raw_retirement_status=sequential.RETIREMENT_EVENT_STATUS,
                raw_retirement_receipt_sha256=(
                    sequential.RETIREMENT_EVENT_RECEIPT_SHA256
                ),
                qsub_environment_sha256=BOUND_QSUB_ENVIRONMENT_SHA256,
            )
        assert caught.value.code == "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID"


def test_r5e_r8_claim_is_strict_live_admission_without_second_preflight() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        production = Path(raw_root).resolve()
        plan, requirements = two_batch_plan()
        run = _scoped_run(production, plan, requirements)
        run = replace(
            run,
            authority=SimpleNamespace(
                governing_commit=run.authority.governing_commit,
                environment_receipt=production / "environment.restricted.json",
            ),
            requirements=replace(
                requirements, contract_id=core.EXPECTED_FULL_CONTRACT_ID
            ),
        )
        shutil.rmtree(run.attempt_root)
        os.chmod(run.production_root, 0o700)
        os.chmod(run.production_root / "attempts", 0o700)
        dynamic_capture = _dynamic_capacity_capture(
            governing_commit=run.authority.governing_commit
        )
        admission = sequential.CapacityAdmission(
            capture=dynamic_capture,
            evidence_role="R5E_R8_POST_CLEANUP",
            capacity_gain_source="CLEANUP",
            raw_retirement_status=sequential.RETIREMENT_EVENT_STATUS,
            raw_retirement_receipt_sha256=(
                sequential.RETIREMENT_EVENT_RECEIPT_SHA256
            ),
        )
        environment_validator = mock.Mock()
        dependencies = SimpleNamespace(
            environment_validator=environment_validator
        )
        with (
            mock.patch.object(sequential, "build_full_run", return_value=run),
            mock.patch.object(
                sequential,
                "validate_installation",
                return_value={"governing_commit": run.authority.governing_commit},
            ) as installation,
            mock.patch.object(
                sequential,
                "_validate_completed_canary_evidence",
                return_value="c" * 64,
            ) as canary,
            mock.patch.object(
                sequential, "resolve_dependencies", return_value=dependencies
            ),
            mock.patch.object(
                sequential,
                "_extraction_cache_inventory",
                return_value=sequential.ExtractionCacheInventory(
                    active=0,
                    preserved_terminal_failed=(
                        sequential.SUCCESSOR_REQUIRED_TERMINAL_FAILED_CACHES
                    ),
                ),
            ) as cache_inventory,
            mock.patch.object(
                sequential,
                "_load_fixed_capacity_admission",
                return_value=admission,
            ) as strict_admission,
            mock.patch.object(
                sequential,
                "preflight_full",
                side_effect=AssertionError("claim ran a second preflight"),
            ) as preflight,
        ):
            result = sequential.claim_submission(
                qsub_environment_sha256=BOUND_QSUB_ENVIRONMENT_SHA256
            )
        assert result["status"] == "READY"
        assert result["cloud_requests"] == 0
        assert result["qsub_submissions"] == 0
        installation.assert_called_once_with()
        canary.assert_called_once_with()
        cache_inventory.assert_called_once_with(
            run.production_root, current_attempt_id=run.attempt_id
        )
        strict_admission.assert_called_once_with(run)
        preflight.assert_not_called()
        environment_validator.assert_called_once_with(
            run.authority.environment_receipt,
            expected_environment_receipt_sha256=(
                run.runtime_authority["environment_receipt_sha256"]
            ),
            scientific_governing_commit=run.authority.governing_commit,
        )
        claim = json.loads(
            (
                run.attempt_root
                / "full_submission_claim.restricted.json"
            ).read_text(encoding="utf-8")
        )
        assert claim["capacity_evidence_role"] == "R5E_R8_POST_CLEANUP"
        assert claim["raw_retirement_receipt_sha256"] == (
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        )
        assert claim["qsub_environment_sha256"] == (
            BOUND_QSUB_ENVIRONMENT_SHA256
        )


def test_r5e_r8_preflight_cli_runs_exact_integrated_no_body_gate_once() -> None:
    check_names = (
        "test_r5e_v2_policy_is_closed_and_v1_policy_remains_byte_valid",
        "test_case_02_r4d2c_is_bound_provenance_but_unreachable_to_classifier",
        "test_case_01_exact_batch3_pattern_is_one_disposition_and_one_affected_study",
        "test_production_finalizer_reconciles_exact_cohort_and_five_no_cine",
    )
    events: list[str] = []

    def synthetic_check(name: str) -> mock.Mock:
        return mock.Mock(side_effect=lambda: events.append(name))

    checks = {name: synthetic_check(name) for name in check_names}
    focused = SimpleNamespace(
        **{name: checks[name] for name in check_names[:3]}
    )
    cohort = SimpleNamespace(**{check_names[3]: checks[check_names[3]]})
    preflight_result = {
        "status": "PASS_FULL_C3_NO_BODY_PREFLIGHT",
        "capacity_evidence_role": "R5E_R8_POST_CLEANUP",
        "capacity_gain_source": "BOTH",
        "raw_retirement_status": sequential.RETIREMENT_EVENT_STATUS,
        "raw_retirement_receipt_sha256": (
            sequential.RETIREMENT_EVENT_RECEIPT_SHA256
        ),
        "bucket_listing_requests": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "writes_performed": 0,
    }

    def one_live_preflight() -> Mapping[str, Any]:
        events.append("preflight_full")
        return preflight_result

    def clean_git_authority() -> str:
        events.append("git_authority")
        return "a" * 40

    with tempfile.TemporaryDirectory() as raw_root:
        absent_attempt = Path(raw_root).resolve() / "absent-attempt"
        forbidden_effects = (
            mock.patch.object(sequential, "claim_submission"),
            mock.patch.object(sequential, "_write_private_json"),
            mock.patch.object(
                capacity, "write_dynamic_successor_capacity_receipt_payload"
            ),
            mock.patch.object(sequential.subprocess, "run"),
            mock.patch.object(scheduler, "submit"),
            mock.patch.object(core, "execute_exact_batch_download"),
            mock.patch.object(stages, "run_production_dicom_extraction"),
            mock.patch.object(stages, "run_production_echoprime"),
            mock.patch.object(sequential, "run_batch_task"),
            mock.patch.object(sequential, "run_cross_batch_finalizer"),
            mock.patch.object(scheduler, "_capture_qsub"),
        )
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                sequential,
                "_load_r5e_r8_integrated_module",
                side_effect=(focused, cohort),
            ) as loader,
            mock.patch.object(
                sequential,
                "_current_commit",
                side_effect=clean_git_authority,
            ) as git_authority,
            mock.patch.object(
                sequential,
                "preflight_full",
                side_effect=one_live_preflight,
            ) as preflight,
            mock.patch.object(
                sequential,
                "build_full_run",
                return_value=SimpleNamespace(attempt_root=absent_attempt),
            ) as builder,
            forbidden_effects[0] as claim,
            forbidden_effects[1] as private_writer,
            forbidden_effects[2] as capacity_writer,
            forbidden_effects[3] as process,
            forbidden_effects[4] as qsub,
            forbidden_effects[5] as cloud,
            forbidden_effects[6] as dicom,
            forbidden_effects[7] as gpu,
            forbidden_effects[8] as array_body,
            forbidden_effects[9] as finalizer_body,
            forbidden_effects[10] as qsub_capture,
            mock.patch("builtins.print") as printer,
        ):
            assert sequential.guarded_main(["--preflight-only"]) == 0

    assert events == ["git_authority", *check_names, "preflight_full"]
    git_authority.assert_called_once_with()
    loader.assert_has_calls(
        [
            mock.call(
                "lvef_c3_r8_integrated_technical",
                "tests/test_lvef_c3_object_technical_disposition.py",
            ),
            mock.call(
                "lvef_c3_r8_integrated_cohort",
                "tests/test_lvef_c3_production_stages_and_finalizer.py",
            ),
        ]
    )
    assert loader.call_count == 2
    for name in check_names:
        checks[name].assert_called_once_with()
    preflight.assert_called_once_with()
    builder.assert_called_once_with()
    assert printer.call_args_list == [
        mock.call("FULL_C3_NO_BODY_PREFLIGHT=PASS")
    ]
    assert scheduler.SCIENCE_MARKERS["--preflight-only"] == (
        "FULL_C3_NO_BODY_PREFLIGHT=PASS"
    )
    for effect in (
        claim,
        private_writer,
        capacity_writer,
        process,
        qsub,
        cloud,
        dicom,
        gpu,
        array_body,
        finalizer_body,
        qsub_capture,
    ):
        effect.assert_not_called()


def test_r5e_r8_execution_node_cli_and_runner_are_cpu_only_and_no_body() -> None:
    runner = sequential.EXECUTION_NODE_PROBE_RUNNER_PATH
    source = runner.read_text(encoding="utf-8")
    assert runner.is_file() and not runner.is_symlink()
    assert runner.stat(follow_symlinks=False).st_mode & 0o111
    assert "qsub" not in source
    assert "CUDA_VISIBLE_DEVICES=\"\"" in source
    assert '[[ "${NSLOTS:-1}" == "1" ]]' in source
    assert '[[ "${SGE_TASK_ID:-undefined}" == "undefined" ]]' in source
    assert "--validate-execution-node-sealed-authorities" in source
    assert "--run-array-task" not in source
    assert "--run-cohort-finalizer" not in source
    assert "mkdir" not in source
    assert "attempts" not in source

    result = {
        "status": "PASS_SEALED_EXECUTION_NODE_AUTHORITY_REPLAY",
        "governing_commit": "a" * 40,
        "historical_pre_cleanup": "PASS",
        "retirement_event": "PASS",
        "historical_post_cleanup": "PASS",
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
        "successor_claims_created": 0,
        "successor_attempt_roots_created": 0,
    }
    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch.object(
            sequential,
            "validate_execution_node_sealed_authorities",
            return_value=result,
        ) as replay,
        mock.patch.object(sequential, "_write_private_json") as writer,
        mock.patch.object(sequential, "_provider_and_transport") as provider,
        mock.patch.object(sequential, "run_batch_task") as array,
        mock.patch.object(sequential, "run_cross_batch_finalizer") as finalizer,
        mock.patch.object(core, "execute_exact_batch_download") as cloud,
        mock.patch.object(stages, "run_production_dicom_extraction") as dicom,
        mock.patch.object(stages, "run_production_echoprime") as gpu,
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(
            ["--validate-execution-node-sealed-authorities"]
        ) == 0
    replay.assert_called_once_with()
    for forbidden in (
        writer, provider, array, finalizer, cloud, dicom, gpu
    ):
        forbidden.assert_not_called()
    assert [call.args[0] for call in printer.call_args_list] == [
        "FULL_C3_EXECUTION_NODE_SEALED_AUTHORITY_REPLAY=PASS",
        "CLOUD_REQUESTS=0",
        "DICOM_BODY_READS=0",
        "NPZ_BODY_READS=0",
        "GPU_EXECUTIONS=0",
        "SUCCESSOR_CLAIMS_CREATED=0",
        "SUCCESSOR_ATTEMPT_ROOTS_CREATED=0",
    ]


def test_r5e_r8_execution_node_replay_calls_only_fixed_sealed_loaders() -> None:
    f201_capture = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_R2_PRE_ACTION_COMMIT,
        research_quota=2_100_000_000_000,
    )
    post_capture = _dynamic_capacity_capture(
        governing_commit=sequential.HISTORICAL_R5E_POST_CLEANUP_COMMIT
    )
    historical_authority = {
        "status": f201_capture.observation["status"],
        "receipt_basename": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        "receipt_bytes": len(f201_capture.receipt_payload),
        "receipt_sha256": hashlib.sha256(
            f201_capture.receipt_payload
        ).hexdigest(),
        "summary_basename": (
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
        "summary_bytes": len(capacity._canonical(f201_capture.observation)),
        "summary_sha256": hashlib.sha256(
            capacity._canonical(f201_capture.observation)
        ).hexdigest(),
    }
    historical = sequential.HistoricalCapacityEvent(
        capture=f201_capture, authority=historical_authority
    )
    historical_post = sequential.HistoricalCapacityEvent(
        capture=post_capture,
        authority={"status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS},
    )
    retired = {
        "status": sequential.RETIREMENT_EVENT_STATUS,
        "receipt_sha256": sequential.RETIREMENT_EVENT_RECEIPT_SHA256,
        "pre_cleanup_capacity_authority": historical_authority,
    }
    with (
        mock.patch.object(
            sequential,
            "validate_installation",
            return_value={"governing_commit": "a" * 40},
        ) as installation,
        mock.patch.object(
            sequential,
            "_load_historical_r5e_r2_pre_action_capacity",
            return_value=historical,
        ) as f201,
        mock.patch.object(
            sequential,
            "_load_completed_retirement_receipt_event",
            return_value=retired,
        ) as retirement,
        mock.patch.object(
            sequential,
            "_load_historical_r5e_post_cleanup_capacity",
            return_value=historical_post,
        ) as old_post,
        mock.patch.object(
            sequential,
            "_capacity_gain_source",
            return_value=sequential.HISTORICAL_R5E_POST_CLEANUP_GAIN_SOURCE,
        ) as gain,
        mock.patch.object(
            sequential,
            "_load_current_r5e_r8_post_cleanup_capacity",
            side_effect=AssertionError("probe opened current unsealed R8"),
        ) as current,
        mock.patch.object(
            capacity,
            "probe_dynamic_successor_capacity_observation",
            side_effect=AssertionError("probe ran live capacity"),
        ) as live_capacity,
        mock.patch.object(sequential, "_write_private_json") as writer,
        mock.patch.object(core, "execute_exact_batch_download") as cloud,
        mock.patch.object(stages, "run_production_dicom_extraction") as dicom,
        mock.patch.object(stages, "run_production_echoprime") as gpu,
    ):
        result = sequential.validate_execution_node_sealed_authorities()
    assert result["status"] == "PASS_SEALED_EXECUTION_NODE_AUTHORITY_REPLAY"
    assert result["cloud_requests"] == 0
    assert result["dicom_body_reads"] == 0
    assert result["npz_body_reads"] == 0
    assert result["gpu_executions"] == 0
    assert result["successor_claims_created"] == 0
    assert result["successor_attempt_roots_created"] == 0
    installation.assert_called_once_with()
    f201.assert_called_once_with()
    retirement.assert_called_once_with()
    old_post.assert_called_once_with()
    gain.assert_called_once_with(
        post_capture.observation,
        evidence_role="R5E_POST_CLEANUP",
        pre_cleanup_observation=f201_capture.observation,
    )
    for forbidden in (
        current, live_capacity, writer, cloud, dicom, gpu
    ):
        forbidden.assert_not_called()


def test_r5e_r8_download_boundary_is_reachable_only_after_adoption() -> None:
    run = object()
    adoption_failure = sequential.FullSequentialError(
        "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
    )
    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "1"},
            clear=True,
        ),
        mock.patch.object(
            sequential, "_adopt_claimed_run", side_effect=adoption_failure
        ) as adopt,
        mock.patch.object(sequential, "run_batch_task") as worker,
        mock.patch("builtins.print"),
    ):
        assert sequential.guarded_main(["--run-array-task"]) == 78
    adopt.assert_called_once_with(scheduler_job_identity="8123456.1")
    worker.assert_not_called()

    download_boundary = sequential.FullSequentialError(
        "R5E_R8_SYNTHETIC_DOWNLOAD_BOUNDARY", stage="DOWNLOAD"
    )
    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "1"},
            clear=True,
        ),
        mock.patch.object(
            sequential, "_adopt_claimed_run", return_value=run
        ) as adopt,
        mock.patch.object(
            sequential, "run_batch_task", side_effect=download_boundary
        ) as worker,
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(["--run-array-task"]) == 78
    adopt.assert_called_once_with(scheduler_job_identity="8123456.1")
    worker.assert_called_once_with(task_id=1, run=run)
    assert [call.args[0] for call in printer.call_args_list] == [
        "FULL_C3_STATUS=BLOCKED_R5E_R8_SYNTHETIC_DOWNLOAD_BOUNDARY",
        "FULL_C3_FAILED_STAGE=DOWNLOAD",
        "FULL_C3_FAILED_BATCH=1",
    ]
    worker_source = inspect.getsource(sequential.run_batch_task)
    assert worker_source.index('_stage_boundary("SUBMISSION_AUTHORITY")') < (
        worker_source.index('_stage_boundary("DOWNLOAD")')
    )


def _replace_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(core.canonical_json_bytes(value))
    os.chmod(path, 0o600)


def _submission_receipt_fixture(
    root: Path,
    *,
    qsub_environment_sha256: str = BOUND_QSUB_ENVIRONMENT_SHA256,
) -> tuple[
    sequential.FullRun,
    scheduler.SchedulerTopology,
    Path,
    dict[str, Any],
]:
    run, _, _ = _claimed_run_fixture(
        root, qsub_environment_sha256=qsub_environment_sha256
    )
    scheduler_root = run.attempt_root / "scheduler"
    scheduler_root.mkdir(mode=0o700)
    os.chmod(scheduler_root, 0o700)
    topology = scheduler.SchedulerTopology(
        head=run.authority.governing_commit,
        attempt_id=run.attempt_id,
        scheduler_root=scheduler_root,
        array_job_name=(
            f"lvef_c3_full_seq_{run.authority.governing_commit[:8]}"
        ),
        finalizer_job_name=(
            f"lvef_c3_full_fin_{run.authority.governing_commit[:8]}"
        ),
    )
    evidence = {
        "array_stdout": b"8123456.1-19:1\n",
        "array_stderr": b"",
        "array_exit_status": b"0\n",
        "finalizer_stdout": b"8123457\n",
        "finalizer_stderr": b"",
        "finalizer_exit_status": b"0\n",
    }
    for label in ("array", "finalizer"):
        for kind in ("stdout", "stderr", "exit_status"):
            _write_private_payload(
                scheduler_root / f"{label}.qsub.{kind}.restricted",
                evidence[f"{label}_{kind}"],
            )
    array_command = topology.array_command()
    finalizer_command = topology.finalizer_command("8123456")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_two_submission_receipt_v1",
        "status": "PASS_EXACT_TWO_QSUB_SUBMISSIONS",
        "attempt_id": run.attempt_id,
        "governing_commit": run.authority.governing_commit,
        "array_job_name": topology.array_job_name,
        "finalizer_job_name": topology.finalizer_job_name,
        "array_job_id": "8123456",
        "finalizer_job_id": "8123457",
        "array_qsub_argv_sha256": hashlib.sha256(
            scheduler._canonical_json({"argv": array_command})
        ).hexdigest(),
        "finalizer_qsub_argv_sha256": hashlib.sha256(
            scheduler._canonical_json({"argv": finalizer_command})
        ).hexdigest(),
        "qsub_environment_sha256": qsub_environment_sha256,
        "array_qsub_stdout_bytes": len(evidence["array_stdout"]),
        "array_qsub_stdout_sha256": hashlib.sha256(
            evidence["array_stdout"]
        ).hexdigest(),
        "array_qsub_stderr_bytes": len(evidence["array_stderr"]),
        "array_qsub_stderr_sha256": hashlib.sha256(
            evidence["array_stderr"]
        ).hexdigest(),
        "array_qsub_exit_status": 0,
        "finalizer_qsub_stdout_bytes": len(evidence["finalizer_stdout"]),
        "finalizer_qsub_stdout_sha256": hashlib.sha256(
            evidence["finalizer_stdout"]
        ).hexdigest(),
        "finalizer_qsub_stderr_bytes": len(evidence["finalizer_stderr"]),
        "finalizer_qsub_stderr_sha256": hashlib.sha256(
            evidence["finalizer_stderr"]
        ).hexdigest(),
        "finalizer_qsub_exit_status": 0,
        "scheduler_submission_count": 2,
        "scheduler_submission_maximum": 2,
        "array_task_range": "1-19",
        "array_max_concurrency": 1,
        "finalizer_held_on_array": True,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }
    receipt_path = scheduler_root / "submission_receipt.restricted.json"
    _write_private_payload(receipt_path, scheduler._canonical_json(receipt))
    return run, topology, receipt_path, receipt


def _validate_submission_receipt_fixture(
    run: sequential.FullRun,
    topology: scheduler.SchedulerTopology,
    *,
    role: str = "array",
    current_job_id: str = "8123456",
) -> Mapping[str, Any]:
    with (
        mock.patch.object(
            scheduler, "build_topology", return_value=topology
        ),
        mock.patch.object(
            sequential.capacity,
            "validate_current_full_headroom",
            return_value={},
        ),
    ):
        return sequential._wait_for_submission_receipt(
            run,
            current_job_id=current_job_id,
            role=role,
            monotonic_clock=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )


def test_adopted_claim_is_closed_and_binds_capacity_plan_and_runtime(
    tmp_path: Path,
) -> None:
    run, capacity_value, _ = _claimed_run_fixture(tmp_path)
    with (
        mock.patch.object(sequential, "build_full_run", return_value=run),
        mock.patch.object(
            sequential,
            "_validate_completed_canary_evidence",
            return_value={"status": "PASS"},
        ),
        mock.patch.object(
            sequential.capacity,
            "validate_current_full_headroom",
            side_effect=lambda value: dict(value),
        ) as capacity_gate,
    ):
        assert sequential._adopt_claimed_run(
            scheduler_job_identity="8123456.1"
        ) is run
    capacity_gate.assert_not_called()


def test_claim_tampering_fails_before_array_worker_effects(tmp_path: Path) -> None:
    mutations = {
        "extra_key": lambda value: {**value, "unexpected": 1},
        "capacity_hash": lambda value: {
            **value, "capacity_receipt_sha256": "0" * 64
        },
        "runtime_hash": lambda value: {
            **value, "runtime_authority_sha256": "1" * 64
        },
        "governing_commit": lambda value: {
            **value, "governing_commit": "b" * 40
        },
        "three_qsubs": lambda value: {
            **value, "maximum_qsub_submissions": 3
        },
        "retry": lambda value: {
            **value, "whole_batch_retry_authorized": True
        },
        "third_submission": lambda value: {
            **value, "third_scheduler_submission_reachable": True
        },
        "nonzero_effect": lambda value: {
            **value, "cloud_requests_before_claim": 1
        },
    }
    for label, mutate in mutations.items():
        run, _, claim_path = _claimed_run_fixture(tmp_path / label)
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        _replace_private_json(claim_path, mutate(claim))
        with (
            mock.patch.object(sequential, "build_full_run", return_value=run),
            mock.patch.object(
                sequential,
                "_validate_completed_canary_evidence",
                return_value={"status": "PASS"},
            ),
            mock.patch.object(
                sequential.capacity,
                "validate_current_full_headroom",
                return_value={},
            ),
            mock.patch.object(sequential, "run_batch_task") as worker,
            mock.patch.dict(
                os.environ,
                {"JOB_ID": "8123456", "SGE_TASK_ID": "1"},
                clear=False,
            ),
        ):
            assert sequential.guarded_main(["--run-array-task"]) == 78
        worker.assert_not_called()


def test_claim_bound_environment_digest_ignores_execution_environment() -> None:
    submission_environment = {
        "SGE_ROOT": "/validated/submission/root",
        "SGE_CELL": "submission-cell",
    }
    binding = scheduler.qsub_environment_sha256(submission_environment)
    with tempfile.TemporaryDirectory() as raw_root:
        run, topology, _, receipt = _submission_receipt_fixture(
            Path(raw_root).resolve(), qsub_environment_sha256=binding
        )
        assert receipt["qsub_environment_sha256"] == binding
        forbidden = (
            mock.patch.object(
                scheduler,
                "build_qsub_environment",
                side_effect=AssertionError(
                    "execution environment must not rebuild client authority"
                ),
            ),
            mock.patch.object(core, "execute_exact_batch_download"),
            mock.patch.object(stages, "run_production_dicom_extraction"),
            mock.patch.object(stages, "run_production_echoprime"),
        )
        with (
            mock.patch.dict(
                os.environ,
                {"SGE_CELL": "different-execution-cell"},
                clear=True,
            ),
            forbidden[0] as environment_rebuilder,
            forbidden[1] as cloud,
            forbidden[2] as dicom,
            forbidden[3] as gpu,
        ):
            assert _validate_submission_receipt_fixture(
                run, topology
            )["status"] == "PASS_EXACT_TWO_QSUB_SUBMISSIONS"
        for boundary in (environment_rebuilder, cloud, dicom, gpu):
            boundary.assert_not_called()

        with (
            mock.patch.dict(
                os.environ,
                {"SGE_QMASTER_PORT": "6543"},
                clear=True,
            ),
            mock.patch.object(
                scheduler,
                "build_qsub_environment",
                side_effect=AssertionError("must remain unreachable"),
            ) as environment_rebuilder,
        ):
            assert _validate_submission_receipt_fixture(
                run,
                topology,
                role="finalizer",
                current_job_id="8123457",
            )["status"] == "PASS_EXACT_TWO_QSUB_SUBMISSIONS"
        environment_rebuilder.assert_not_called()

    wait_source = inspect.getsource(sequential._wait_for_submission_receipt)
    assert "build_qsub_environment" not in wait_source
    assert "os.environ" not in wait_source


def test_claim_binding_missing_malformed_or_changed_fails_closed() -> None:
    mutations = {
        "missing": lambda value: {
            key: item
            for key, item in value.items()
            if key != "qsub_environment_sha256"
        },
        "malformed": lambda value: {
            **value, "qsub_environment_sha256": "A" * 64
        },
        "changed": lambda value: {
            **value, "qsub_environment_sha256": "8" * 64
        },
    }
    for label, mutate in mutations.items():
        with tempfile.TemporaryDirectory() as raw_root:
            run, _, claim_path = _claimed_run_fixture(
                Path(raw_root).resolve()
            )
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            _replace_private_json(claim_path, mutate(claim))
            with (
                mock.patch.object(
                    sequential, "build_full_run", return_value=run
                ),
                mock.patch.object(
                    sequential,
                    "_validate_completed_canary_evidence",
                    return_value={"status": "PASS"},
                ),
                mock.patch.object(
                    sequential.capacity,
                    "validate_current_full_headroom",
                    return_value={},
                ),
                pytest.raises(sequential.FullSequentialError) as caught,
            ):
                sequential._adopt_claimed_run(
                    scheduler_job_identity="MATERIALIZED_CLAIM_READBACK",
                    expected_qsub_environment_sha256=(
                        BOUND_QSUB_ENVIRONMENT_SHA256
                    ),
                )
            assert caught.value.code == (
                "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
            )
            assert label in mutations

    with tempfile.TemporaryDirectory() as raw_root:
        run, _, claim_path = _claimed_run_fixture(Path(raw_root).resolve())
        payload = claim_path.read_bytes()
        duplicate = (
            b'{"qsub_environment_sha256":"'
            + BOUND_QSUB_ENVIRONMENT_SHA256.encode("ascii")
            + b'",'
            + payload[1:]
        )
        _write_private_payload(claim_path, duplicate)
        with (
            mock.patch.object(
                sequential, "build_full_run", return_value=run
            ),
            mock.patch.object(
                sequential,
                "_validate_completed_canary_evidence",
                return_value={"status": "PASS"},
            ),
            mock.patch.object(
                sequential.capacity,
                "validate_current_full_headroom",
                return_value={},
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            sequential._adopt_claimed_run(
                scheduler_job_identity="MATERIALIZED_CLAIM_READBACK",
                expected_qsub_environment_sha256=(
                    BOUND_QSUB_ENVIRONMENT_SHA256
                ),
            )
        assert caught.value.code == (
            "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"
        )


def test_control_environment_binding_is_required_and_scope_closed() -> None:
    for invalid in (None, "", "9" * 63, "A" * 64, 9):
        with pytest.raises(sequential.FullSequentialError) as caught:
            sequential._require_qsub_environment_sha256(invalid)
        assert (
            caught.value.code
            == "FULL_SEQUENTIAL_QSUB_ENVIRONMENT_BINDING_INVALID"
        )

    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch.object(sequential, "preflight_full") as preflight,
        mock.patch.object(sequential, "build_full_run") as builder,
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(["--claim-submission"]) == 78
    preflight.assert_not_called()
    builder.assert_not_called()
    assert printer.call_args_list[0].args[0] == (
        "FULL_C3_STATUS=BLOCKED_"
        "FULL_SEQUENTIAL_QSUB_ENVIRONMENT_BINDING_INVALID"
    )

    with (
        mock.patch.dict(
            os.environ,
            {
                sequential.QSUB_ENVIRONMENT_SHA256_NAME:
                BOUND_QSUB_ENVIRONMENT_SHA256,
            },
            clear=True,
        ),
        mock.patch.object(sequential, "preflight_full") as preflight,
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(["--preflight-only"]) == 78
    preflight.assert_not_called()
    assert printer.call_args_list[0].args[0] == (
        "FULL_C3_STATUS=BLOCKED_"
        "FULL_SEQUENTIAL_QSUB_ENVIRONMENT_BINDING_INVALID"
    )


def test_submission_receipt_role_codes_are_exact_and_closed() -> None:
    cases = (
        (
            "schema",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path, {**receipt, "unexpected": 1}
            ),
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_SCHEMA_INVALID",
            "array",
            "8123456",
        ),
        (
            "authority",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path,
                {**receipt, "array_qsub_argv_sha256": "0" * 64},
            ),
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID",
            "array",
            "8123456",
        ),
        (
            "environment",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path,
                {**receipt, "qsub_environment_sha256": "0" * 64},
            ),
            (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_"
                "ENVIRONMENT_BINDING_MISMATCH"
            ),
            "array",
            "8123456",
        ),
        (
            "malformed_environment",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path,
                {**receipt, "qsub_environment_sha256": "invalid"},
            ),
            (
                "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_"
                "ENVIRONMENT_BINDING_MISMATCH"
            ),
            "array",
            "8123456",
        ),
        (
            "missing_environment",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path,
                {
                    key: item
                    for key, item in receipt.items()
                    if key != "qsub_environment_sha256"
                },
            ),
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_SCHEMA_INVALID",
            "array",
            "8123456",
        ),
        (
            "array_job",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path, {**receipt, "array_job_id": "8123466"}
            ),
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH",
            "array",
            "8123456",
        ),
        (
            "finalizer_job",
            lambda _root, receipt_path, receipt: _replace_private_json(
                receipt_path, {**receipt, "finalizer_job_id": "8123467"}
            ),
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JOB_ID_MISMATCH",
            "finalizer",
            "8123457",
        ),
    )
    for label, mutate, expected, role, job_id in cases:
        with tempfile.TemporaryDirectory() as raw_root:
            run, topology, receipt_path, receipt = (
                _submission_receipt_fixture(Path(raw_root).resolve())
            )
            mutate(topology.scheduler_root, receipt_path, receipt)
            with pytest.raises(sequential.FullSequentialError) as caught:
                _validate_submission_receipt_fixture(
                    run,
                    topology,
                    role=role,
                    current_job_id=job_id,
                )
            assert caught.value.code == expected, label

    with tempfile.TemporaryDirectory() as raw_root:
        run, topology, _, _ = _submission_receipt_fixture(
            Path(raw_root).resolve()
        )
        with pytest.raises(sequential.FullSequentialError) as caught:
            _validate_submission_receipt_fixture(
                run, topology, role="unknown", current_job_id="8123456"
            )
        assert (
            caught.value.code
            == "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_AUTHORITY_INVALID"
        )


def test_submission_receipt_evidence_drift_maps_exactly() -> None:
    mutations = (
        ("array", "stdout", b"9123456.1-19:1\n"),
        ("array", "stderr", b"scheduler failure\n"),
        ("array", "exit_status", b"1\n"),
        ("finalizer", "stdout", b"9123457\n"),
        ("finalizer", "stderr", b"scheduler failure\n"),
        ("finalizer", "exit_status", b"1\n"),
    )
    for label, kind, payload in mutations:
        with tempfile.TemporaryDirectory() as raw_root:
            run, topology, _, _ = _submission_receipt_fixture(
                Path(raw_root).resolve()
            )
            evidence_path = (
                topology.scheduler_root
                / f"{label}.qsub.{kind}.restricted"
            )
            _write_private_payload(evidence_path, payload)
            with pytest.raises(sequential.FullSequentialError) as caught:
                _validate_submission_receipt_fixture(run, topology)
            assert (
                caught.value.code
                == "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_EVIDENCE_INVALID"
            )


def test_submission_receipt_file_and_json_failures_are_role_specific() -> None:
    json_payloads = (
        b"{",
        b'{"value":NaN}\n',
        b'{"value":1,"value":2}\n',
        b"[]\n",
        b"\xff",
    )
    for payload in json_payloads:
        with tempfile.TemporaryDirectory() as raw_root:
            _, _, receipt_path, _ = _submission_receipt_fixture(
                Path(raw_root).resolve()
            )
            _write_private_payload(receipt_path, payload)
            with pytest.raises(sequential.FullSequentialError) as caught:
                sequential._load_submission_receipt(receipt_path)
            assert (
                caught.value.code
                == "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_JSON_INVALID"
            )

    for failure in ("mode", "symlink", "owner"):
        with tempfile.TemporaryDirectory() as raw_root:
            _, _, receipt_path, _ = _submission_receipt_fixture(
                Path(raw_root).resolve()
            )
            context = mock.patch.object(
                sequential.os,
                "geteuid",
                return_value=os.geteuid() + 1,
            )
            if failure == "mode":
                os.chmod(receipt_path, 0o640)
                context = mock.patch.object(
                    sequential.os, "geteuid", wraps=os.geteuid
                )
            elif failure == "symlink":
                target = receipt_path.with_name("receipt-target.json")
                target.write_bytes(receipt_path.read_bytes())
                os.chmod(target, 0o600)
                receipt_path.unlink()
                receipt_path.symlink_to(target)
                context = mock.patch.object(
                    sequential.os, "geteuid", wraps=os.geteuid
                )
            with (
                context,
                pytest.raises(sequential.FullSequentialError) as caught,
            ):
                sequential._load_submission_receipt(receipt_path)
            assert (
                caught.value.code
                == "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_FILE_INVALID"
            ), failure


def test_changed_claim_hash_is_detected_against_unchanged_receipt() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        run, topology, _, _ = _submission_receipt_fixture(
            Path(raw_root).resolve()
        )
        claim_path = (
            run.attempt_root / "full_submission_claim.restricted.json"
        )
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        _replace_private_json(
            claim_path, {**claim, "qsub_environment_sha256": "8" * 64}
        )
        with pytest.raises(sequential.FullSequentialError) as caught:
            _validate_submission_receipt_fixture(run, topology)
        assert caught.value.code == (
            "FULL_SEQUENTIAL_SUBMISSION_RECEIPT_"
            "ENVIRONMENT_BINDING_MISMATCH"
        )


def test_claim_and_receipt_no_clobber_writers_preserve_existing_bytes() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        run, _, receipt_path, _ = _submission_receipt_fixture(
            Path(raw_root).resolve()
        )
        claim_path = (
            run.attempt_root / "full_submission_claim.restricted.json"
        )
        claim_before = claim_path.read_bytes()
        receipt_before = receipt_path.read_bytes()
        with pytest.raises(core.OrchestrationError) as claim_error:
            sequential._write_private_json(
                claim_path,
                {"qsub_environment_sha256": "8" * 64},
                attempt_id=run.attempt_id,
            )
        assert _error_code(claim_error.value) == (
            "OUTPUT_ALREADY_EXISTS_NO_CLOBBER"
        )
        with pytest.raises(scheduler.FullSchedulerError) as caught:
            scheduler._write_new(receipt_path, b'{"altered":true}\n')
        assert caught.value.code == "SCHEDULER_EVIDENCE_NO_CLOBBER"
        assert claim_path.read_bytes() == claim_before
        assert receipt_path.read_bytes() == receipt_before


def _synthetic_completed_canary_evidence(
    root: Path,
) -> tuple[tuple[tuple[str, ...], int, str], ...]:
    root.mkdir(mode=0o700)
    for relative in sequential.COMPLETED_CANARY_PRIVATE_TOPOLOGY:
        path = root.joinpath(*relative)
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    authorities = []
    for ordinal, (relative, _, _) in enumerate(
        sequential.COMPLETED_CANARY_EVIDENCE_AUTHORITIES, start=1
    ):
        payload = (f"fixed-canary-evidence-{ordinal}\n").encode("ascii")
        path = root.joinpath(*relative)
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        authorities.append(
            (relative, len(payload), hashlib.sha256(payload).hexdigest())
        )
    return tuple(authorities)


def test_completed_canary_evidence_is_fixed_read_only_and_tamper_evident(
    tmp_path: Path,
) -> None:
    production_root = tmp_path / "production"
    authorities = _synthetic_completed_canary_evidence(production_root)
    evidence_paths = [production_root.joinpath(*row[0]) for row in authorities]
    before = [
        (
            path.stat(follow_symlinks=False).st_ino,
            path.stat(follow_symlinks=False).st_size,
            path.stat(follow_symlinks=False).st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in evidence_paths
    ]
    with (
        mock.patch.object(sequential, "PRODUCTION_ROOT", production_root),
        mock.patch.object(
            sequential, "COMPLETED_CANARY_EVIDENCE_AUTHORITIES", authorities
        ),
    ):
        observed = sequential._validate_completed_canary_evidence()
        assert len(observed) == 3
    after = [
        (
            path.stat(follow_symlinks=False).st_ino,
            path.stat(follow_symlinks=False).st_size,
            path.stat(follow_symlinks=False).st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in evidence_paths
    ]
    assert after == before

    evidence_paths[1].write_bytes(evidence_paths[1].read_bytes() + b"tamper")
    with (
        mock.patch.object(sequential, "PRODUCTION_ROOT", production_root),
        mock.patch.object(
            sequential, "COMPLETED_CANARY_EVIDENCE_AUTHORITIES", authorities
        ),
        pytest.raises(sequential.FullSequentialError) as caught,
    ):
        sequential._validate_completed_canary_evidence()
    assert caught.value.code == "FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT"


def test_private_authority_reader_rejects_leaf_and_ancestor_symlinks(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    authority = private / "authority.json"
    authority.write_bytes(b"{}\n")
    authority.chmod(0o600)
    assert sequential._read_owner_private_regular(authority) == b"{}\n"

    leaf = private / "leaf-link.json"
    leaf.symlink_to(authority)
    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential._read_owner_private_regular(leaf)
    assert caught.value.code == "FULL_SEQUENTIAL_PATH_SYMLINK"

    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential._read_owner_private_regular(alias / authority.name)
    assert caught.value.code == "FULL_SEQUENTIAL_PATH_SYMLINK"


def test_full_batch_plan_scale_contract_and_role_failures() -> None:
    """Exercise the dynamic bound with actual plan objects above 16 MB."""

    assert 335_984 * 64 == 21_502_976
    assert 21_502_976 > sequential.GENERIC_PRIVATE_FILE_MAXIMUM_BYTES
    plan, requirements = _schema_faithful_scale_plan()
    assert requirements.normalized_source_objects == 52_000
    payload = core.canonical_json_bytes(plan)
    assert len(payload) > sequential.GENERIC_PRIVATE_FILE_MAXIMUM_BYTES
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root).resolve()
        path = root / "full_batch_plan.restricted.json"
        _write_private_payload(path, payload)
        run = SimpleNamespace(
            plan=plan,
            plan_path=path,
            plan_sha256=expected_sha256,
        )
        observed = sequential._load_full_batch_plan(run)
        assert observed["cohort"]["normalized_source_objects"] == 52_000
        del observed

        for changed in (payload + b"x", payload[:-1]):
            _write_private_payload(path, changed)
            with pytest.raises(sequential.FullSequentialError) as caught:
                sequential._load_full_batch_plan(run)
            assert (
                caught.value.code
                == "FULL_SEQUENTIAL_BATCH_PLAN_SIZE_MISMATCH"
            )

        # The altered first byte is also invalid JSON. Digest authority has
        # deliberate precedence for any exact-size content mismatch.
        changed = b"!" + payload[1:]
        _write_private_payload(path, changed)
        with pytest.raises(sequential.FullSequentialError) as caught:
            sequential._load_full_batch_plan(run)
        assert (
            caught.value.code
            == "FULL_SEQUENTIAL_BATCH_PLAN_SHA256_MISMATCH"
        )


def test_full_batch_plan_json_semantics_and_file_authority_failures() -> None:
    """Map strict JSON and every stable owner-private file predicate."""

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root).resolve()
        for ordinal, payload in enumerate(
            (b"\xff", b"{", b'{"duplicate":1,"duplicate":2}', b"[]")
        ):
            path = root / f"invalid-{ordinal}.json"
            _write_private_payload(path, payload)
            with pytest.raises(sequential.FullSequentialError) as caught:
                sequential._load_full_batch_plan_payload(
                    path,
                    expected_bytes=len(payload),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
            assert (
                caught.value.code
                == "FULL_SEQUENTIAL_BATCH_PLAN_JSON_INVALID"
            )

        valid = core.canonical_json_bytes({"valid": True})

        def read(path: Path) -> Mapping[str, Any]:
            return sequential._load_full_batch_plan_payload(
                path,
                expected_bytes=len(valid),
                expected_sha256=hashlib.sha256(valid).hexdigest(),
            )

        target = root / "target.json"
        _write_private_payload(target, valid)
        symlink = root / "symlink.json"
        symlink.symlink_to(target)
        with pytest.raises(sequential.FullSequentialError) as caught:
            read(symlink)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        wrong_mode = root / "wrong-mode.json"
        _write_private_payload(wrong_mode, valid)
        os.chmod(wrong_mode, 0o640)
        with pytest.raises(sequential.FullSequentialError) as caught:
            read(wrong_mode)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        nonregular = root / "directory.json"
        nonregular.mkdir(mode=0o700)
        with pytest.raises(sequential.FullSequentialError) as caught:
            read(nonregular)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        wrong_owner = root / "wrong-owner.json"
        _write_private_payload(wrong_owner, valid)
        with (
            mock.patch.object(
                sequential.os, "geteuid", return_value=os.geteuid() + 1
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            read(wrong_owner)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        unstable = root / "unstable.json"
        _write_private_payload(unstable, valid)
        observed = os.stat(unstable, follow_symlinks=False)
        wrong_identity = SimpleNamespace(
            st_mode=observed.st_mode,
            st_uid=observed.st_uid,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino + 1,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
        )
        with (
            mock.patch.object(
                sequential.os, "fstat", return_value=wrong_identity
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            read(unstable)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        truncated = root / "truncated.json"
        _write_private_payload(truncated, valid)
        with (
            mock.patch.object(sequential.os, "read", return_value=b""),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            read(truncated)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        mutated = root / "post-read-mutation.json"
        _write_private_payload(mutated, valid)
        before = os.stat(mutated, follow_symlinks=False)
        after = SimpleNamespace(
            st_mode=before.st_mode,
            st_uid=before.st_uid,
            st_dev=before.st_dev,
            st_ino=before.st_ino,
            st_size=before.st_size,
            st_mtime_ns=before.st_mtime_ns + 1,
        )
        with (
            mock.patch.object(
                sequential.os, "fstat", side_effect=(before, after)
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            read(mutated)
        assert caught.value.code == "FULL_SEQUENTIAL_BATCH_PLAN_FILE_INVALID"

        plan, _ = two_batch_plan()
        plan_payload = core.canonical_json_bytes(plan)
        altered = json.loads(plan_payload.decode("ascii"))
        altered["contract_id"] = "semantic_drift_same_reader_contract"
        altered_payload = core.canonical_json_bytes(altered)
        semantic_path = root / "semantic-drift.json"
        _write_private_payload(semantic_path, altered_payload)
        run = SimpleNamespace(
            plan=plan,
            plan_path=semantic_path,
            plan_sha256=core.canonical_json_sha256(plan),
        )
        with (
            mock.patch.object(
                sequential,
                "_derive_full_batch_plan_read_contract",
                return_value=(
                    len(altered_payload),
                    hashlib.sha256(altered_payload).hexdigest(),
                ),
            ),
            pytest.raises(sequential.FullSequentialError) as caught,
        ):
            sequential._load_full_batch_plan(run)
        assert (
            caught.value.code
            == "FULL_SEQUENTIAL_PREPARED_AUTHORITY_MISMATCH"
        )


def test_small_private_authorities_retain_generic_ceiling() -> None:
    assert sequential.GENERIC_PRIVATE_FILE_MAXIMUM_BYTES == 16_000_000
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root).resolve()
        for role in ("launch", "capacity", "claim"):
            path = root / f"{role}.restricted.json"
            with path.open("wb") as handle:
                handle.truncate(
                    sequential.GENERIC_PRIVATE_FILE_MAXIMUM_BYTES + 1
                )
            os.chmod(path, 0o600)
            with pytest.raises(sequential.FullSequentialError) as caught:
                sequential._load_owner_private_json(path)
            assert caught.value.code == "FULL_SEQUENTIAL_PRIVATE_FILE_INVALID"


def test_materialized_claim_readback_cli_is_same_path_and_zero_effect() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root).resolve()
        run, _, _ = _claimed_run_fixture(root)
        files = tuple(
            run.attempt_root / name
            for name in (
                "full_batch_plan.restricted.json",
                "full_launch_authority.restricted.json",
                sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME,
                "full_capacity_receipt.restricted.json",
                "full_submission_claim.restricted.json",
            )
        )

        def snapshot(path: Path) -> tuple[int, int, int, str]:
            item = path.stat(follow_symlinks=False)
            return (
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

        before = tuple(snapshot(path) for path in files)
        forbidden = (
            "_write_private_json",
            "_provider_and_transport",
            "run_batch_task",
            "run_cross_batch_finalizer",
        )
        patches = [mock.patch.object(sequential, name) for name in forbidden]
        with (
            mock.patch.object(sequential, "build_full_run", return_value=run),
            mock.patch.object(
                sequential,
                "_validate_completed_canary_evidence",
                return_value={"status": "PASS"},
            ),
            mock.patch.object(
                sequential.capacity,
                "validate_current_full_headroom",
                side_effect=lambda value: dict(value),
            ) as capacity_gate,
            mock.patch.object(sequential.subprocess, "run") as process,
            mock.patch.object(stages, "run_production_dicom_extraction") as dicom,
            mock.patch.object(stages, "run_production_echoprime") as gpu,
            mock.patch.object(core, "execute_exact_batch_download") as cloud,
            mock.patch("builtins.print") as printer,
            mock.patch.dict(
                os.environ,
                {
                    sequential.QSUB_ENVIRONMENT_SHA256_NAME:
                    BOUND_QSUB_ENVIRONMENT_SHA256,
                },
                clear=False,
            ),
            patches[0] as writer,
            patches[1] as provider,
            patches[2] as science,
            patches[3] as cohort,
        ):
            assert sequential.guarded_main(
                ["--validate-claimed-submission"]
            ) == 0
        assert [call.args[0] for call in printer.call_args_list] == [
            "FULL_C3_MATERIALIZED_CLAIM_READBACK=PASS"
        ]
        capacity_gate.assert_not_called()
        for boundary in (
            process, dicom, gpu, cloud, writer, provider, science, cohort
        ):
            boundary.assert_not_called()
        assert tuple(snapshot(path) for path in files) == before


def test_each_materialized_claim_file_tamper_fails_before_effects() -> None:
    for role in ("plan", "launch", "dynamic", "capacity", "claim"):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            run, _, claim_path = _claimed_run_fixture(root)
            if role == "plan":
                payload = run.plan_path.read_bytes()
                _write_private_payload(run.plan_path, b"!" + payload[1:])
                expected = "FULL_SEQUENTIAL_BATCH_PLAN_SHA256_MISMATCH"
            elif role == "launch":
                launch_path = (
                    run.attempt_root
                    / "full_launch_authority.restricted.json"
                )
                _replace_private_json(launch_path, {"status": "ALTERED"})
                expected = "FULL_SEQUENTIAL_PREPARED_AUTHORITY_MISMATCH"
            elif role == "dynamic":
                dynamic_path = (
                    run.attempt_root
                    / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME
                )
                payload = dynamic_path.read_bytes()
                _write_private_payload(dynamic_path, b"!" + payload[1:])
                expected = "FULL_SEQUENTIAL_ATTEMPT_LOCAL_CAPACITY_MISMATCH"
            elif role == "capacity":
                capacity_path = (
                    run.attempt_root
                    / "full_capacity_receipt.restricted.json"
                )
                _replace_private_json(capacity_path, {"status": "ALTERED"})
                expected = "FULL_SEQUENTIAL_ATTEMPT_LOCAL_CAPACITY_MISMATCH"
            else:
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
                _replace_private_json(claim_path, {**claim, "unexpected": 1})
                expected = "FULL_SEQUENTIAL_CLAIM_CAPACITY_BINDING_MISMATCH"

            with (
                mock.patch.object(
                    sequential, "build_full_run", return_value=run
                ),
                mock.patch.object(
                    sequential,
                    "_validate_completed_canary_evidence",
                    return_value={"status": "PASS"},
                ),
                mock.patch.object(
                    sequential.capacity,
                    "validate_current_full_headroom",
                    return_value={},
                ),
                mock.patch.object(sequential, "_provider_and_transport") as provider,
                mock.patch.object(sequential, "run_batch_task") as science,
                mock.patch.object(sequential.subprocess, "run") as process,
                mock.patch("builtins.print") as printer,
                mock.patch.dict(
                    os.environ,
                    {
                        sequential.QSUB_ENVIRONMENT_SHA256_NAME:
                        BOUND_QSUB_ENVIRONMENT_SHA256,
                    },
                    clear=False,
                ),
            ):
                assert sequential.guarded_main(
                    ["--validate-claimed-submission"]
                ) == 78
            assert printer.call_args_list[0].args[0] == (
                f"FULL_C3_STATUS=BLOCKED_{expected}"
            )
            provider.assert_not_called()
            science.assert_not_called()
            process.assert_not_called()

def test_canary_evidence_drift_stops_preflight_before_runtime_or_capacity(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def forbidden(name: str):
        def operation(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
            calls.append(name)
            raise AssertionError(f"{name} crossed the canary-evidence gate")

        return operation

    run = SimpleNamespace(attempt_root=tmp_path / "absent")
    dependencies = sequential.FullDependencies(
        environment_validator=forbidden("environment"),
        capacity_probe=forbidden("capacity"),
        test_only_synthetic_full_scope=True,
    )
    with (
        mock.patch.object(
            sequential,
            "validate_installation",
            return_value={"governing_commit": "a" * 40},
        ),
        mock.patch.object(sequential, "build_full_run", return_value=run),
        mock.patch.object(
            sequential,
            "_validate_completed_canary_evidence",
            side_effect=sequential.FullSequentialError(
                "FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT"
            ),
        ),
        pytest.raises(sequential.FullSequentialError) as caught,
    ):
        sequential.preflight_full(dependencies=dependencies)
    assert caught.value.code == "FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT"
    assert calls == []


def _aggregate_safe_preflight_report_fixture() -> dict[str, Any]:
    mappings = []
    for ordinal in range(1, 20):
        mappings.append(
            {
                "task_id": ordinal,
                "batch_id": f"c3_batch_{ordinal - 1:03d}",
                "n_studies": 250 if ordinal < 19 else 30,
                "n_objects": 1 if ordinal < 19 else 335_966,
                "source_bytes": 1 if ordinal < 19 else 1_216_569_133_304,
            }
        )
    capacity_value = _dynamic_capacity_capture().observation
    return {
        "status": "PASS_FULL_C3_NO_BODY_PREFLIGHT",
        "governing_commit": "a" * 40,
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "normalized_source_objects": 335984,
        "selected_source_bytes": 1_216_569_133_322,
        "batch_count": 19,
        "expected_study_embeddings": 4525,
        "expected_no_cine_studies": 5,
        "active_extraction_caches": 0,
        "preserved_terminal_failed_extraction_caches": 2,
        "storage_reserve": "PASS",
        "echoprime_runtime": "PASS",
        "crc32c_external_runtime": "PASS",
        "completed_canary_evidence": "PASS",
        "task_mappings": mappings,
        "capacity": capacity_value,
        "successor_capacity": _successor_capacity_authority(
            capacity_value
        ),
        "bucket_listing_requests": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "writes_performed": 0,
    }


def test_preflight_report_is_complete_aggregate_safe_and_zero_effect() -> None:
    value = _aggregate_safe_preflight_report_fixture()
    with mock.patch.object(
        sequential.capacity,
        "validate_current_full_headroom",
        side_effect=lambda observed: dict(observed),
    ):
        lines = sequential.format_preflight_report(value)
    markers = dict(line.split("=", 1) for line in lines)
    assert markers["FULL_C3_NO_BODY_PREFLIGHT"] == "PASS"
    assert markers["FULL_C3_SELECTED_STUDIES"] == "4530"
    assert markers["FULL_C3_SELECTED_SUBJECTS"] == "4530"
    assert markers["FULL_C3_DECLARED_OBJECTS"] == "335984"
    assert markers["FULL_C3_DECLARED_BYTES"] == "1216569133322"
    assert markers["FULL_C3_BATCHES"] == "19"
    assert markers["FULL_C3_EXPECTED_STUDY_EMBEDDINGS"] == "4525"
    assert markers["FULL_C3_EXPECTED_NO_CINE_STUDIES"] == "5"
    assert markers[
        "FULL_C3_PRESERVED_TERMINAL_FAILED_EXTRACTION_CACHES"
    ] == "2"
    assert markers["ECHOPRIME_RUNTIME"] == "PASS"
    assert markers["CRC32C_EXTERNAL_RUNTIME"] == "PASS"
    assert markers["COMPLETED_CANARY_EVIDENCE"] == "PASS"
    assert markers["FULL_C3_SOURCE_PLAN_VALIDATION"] == "PASS"
    assert markers["FULL_C3_TASK_MAPPING_VALIDATION"] == "PASS"
    assert markers["FULL_C3_TASK_MAPPINGS"] == "19"
    assert markers["FULL_C3_TASK_01_BATCH"] == "c3_batch_000"
    assert markers["FULL_C3_TASK_19_BATCH"] == "c3_batch_018"
    assert markers["CLOUD_REQUESTS"] == "0"
    assert markers["BUCKET_LISTING_REQUESTS"] == "0"
    assert markers["QSUB_SUBMISSIONS"] == "0"
    assert markers["DICOM_BODY_READS"] == "0"
    assert markers["GPU_EXECUTIONS"] == "0"
    assert markers["WRITES_PERFORMED"] == "0"
    assert len(markers) == len(lines)
    report = "\n".join(lines)
    assert "/restricted/" not in report
    assert "subject_id" not in report
    assert "study_id" not in report
    assert "source_object_key" not in report

    changed = {**value, "selected_source_bytes": 1}
    with (
        mock.patch.object(
            sequential.capacity,
            "validate_current_full_headroom",
            side_effect=lambda observed: dict(observed),
        ),
        pytest.raises(sequential.FullSequentialError) as caught,
    ):
        sequential.format_preflight_report(changed)
    assert caught.value.code == "FULL_SEQUENTIAL_PREFLIGHT_REPORT_INVALID"


def test_preflight_report_cli_is_fixed_no_body_and_prints_only_report() -> None:
    value = _aggregate_safe_preflight_report_fixture()
    with (
        mock.patch.object(sequential, "preflight_full", return_value=value) as gate,
        mock.patch.object(
            sequential.capacity,
            "validate_current_full_headroom",
            side_effect=lambda observed: dict(observed),
        ),
        mock.patch("builtins.print") as printer,
        mock.patch.object(sequential.subprocess, "run") as process,
    ):
        assert sequential.guarded_main(["--preflight-report"]) == 0
    gate.assert_called_once_with()
    process.assert_not_called()
    printer.assert_called_once()
    payload = printer.call_args.args[0]
    assert payload.startswith("FULL_C3_NO_BODY_PREFLIGHT=PASS\n")
    assert payload.endswith("WRITES_PERFORMED=0")


def test_live_stage_failure_preserves_safe_code_stage_and_batch() -> None:
    failure = sequential.FullSequentialError(
        "DOWNLOAD_GENERATION_MISMATCH", stage="DOWNLOAD"
    )
    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "2"},
            clear=False,
        ),
        mock.patch.object(sequential, "_adopt_claimed_run", return_value=object()),
        mock.patch.object(sequential, "run_batch_task", side_effect=failure),
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(["--run-array-task"]) == 78
    lines = [call.args[0] for call in printer.call_args_list]
    assert lines == [
        "FULL_C3_STATUS=BLOCKED_DOWNLOAD_GENERATION_MISMATCH",
        "FULL_C3_FAILED_STAGE=DOWNLOAD",
        "FULL_C3_FAILED_BATCH=2",
    ]


def test_live_finalizer_failure_preserves_safe_code_and_stage() -> None:
    failure = sequential.FullSequentialError("COHORT_RECEIPT_MISMATCH")
    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "8123457", "SGE_TASK_ID": "undefined"},
            clear=False,
        ),
        mock.patch.object(sequential, "_adopt_claimed_run", return_value=object()),
        mock.patch.object(
            sequential, "run_cross_batch_finalizer", side_effect=failure
        ),
        mock.patch("builtins.print") as printer,
    ):
        assert sequential.guarded_main(["--run-cohort-finalizer"]) == 78
    assert [call.args[0] for call in printer.call_args_list] == [
        "FULL_C3_STATUS=BLOCKED_COHORT_RECEIPT_MISMATCH",
        "FULL_C3_FAILED_STAGE=CROSS_BATCH_FINALIZATION",
    ]


def test_stage_boundary_maps_only_closed_safe_codes() -> None:
    class TypedFailure(RuntimeError):
        code = "OBJECT_GENERATION_MISMATCH"

    with pytest.raises(sequential.FullSequentialError) as caught:
        with sequential._stage_boundary("DOWNLOAD"):
            raise TypedFailure("private path must not escape")
    assert caught.value.code == "OBJECT_GENERATION_MISMATCH"
    assert caught.value.stage == "DOWNLOAD"

    with pytest.raises(sequential.FullSequentialError) as caught:
        with sequential._stage_boundary("DICOM_EXTRACTION"):
            raise RuntimeError("/restricted/private/subject-id")
    assert caught.value.code == "UNEXPECTED_SANITIZED_STAGE_FAILURE"
    assert caught.value.stage == "DICOM_EXTRACTION"
    assert "/restricted/" not in str(caught.value)


def test_full_run_requires_plan_at_the_fixed_attempt_path(tmp_path: Path) -> None:
    run, _, _ = _claimed_run_fixture(tmp_path)
    wrong = replace(
        run,
        plan_path=run.production_root / "full_batch_plan.restricted.json",
    )
    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential._validate_full_run(wrong)
    assert caught.value.code == "FULL_SEQUENTIAL_RUN_AUTHORITY_INVALID"


def test_adoption_rechecks_canary_evidence_before_array_worker(
    tmp_path: Path,
) -> None:
    run, _, _ = _claimed_run_fixture(tmp_path)
    with (
        mock.patch.object(sequential, "build_full_run", return_value=run),
        mock.patch.object(
            sequential,
            "_validate_completed_canary_evidence",
            side_effect=sequential.FullSequentialError(
                "FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT"
            ),
        ),
        mock.patch.object(
            sequential.capacity, "validate_current_full_headroom"
        ) as capacity_gate,
        mock.patch.object(sequential, "run_batch_task") as worker,
        mock.patch("builtins.print") as printer,
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "8123456", "SGE_TASK_ID": "1"},
            clear=False,
        ),
    ):
        assert sequential.guarded_main(["--run-array-task"]) == 78
    capacity_gate.assert_not_called()
    worker.assert_not_called()
    assert [call.args[0] for call in printer.call_args_list] == [
        "FULL_C3_STATUS=BLOCKED_FULL_SEQUENTIAL_CANARY_EVIDENCE_DRIFT",
        "FULL_C3_FAILED_STAGE=SUBMISSION_AUTHORITY",
        "FULL_C3_FAILED_BATCH=1",
    ]
