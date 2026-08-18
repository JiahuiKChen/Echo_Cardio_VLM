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
    *, governing_commit: str = "a" * 40
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
    research_quota = 3_093_796_556_800
    research_usage = 527_008_808_960
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
                now_utc=datetime.now(timezone.utc),
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
            assert caught.value.code == "FULL_SEQUENTIAL_PREPARED_CLAIM_INVALID"
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
        assert caught.value.code == "FULL_SEQUENTIAL_PREPARED_CLAIM_INVALID"


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
        plan, requirements = two_batch_plan()
        run = _scoped_run(root, plan, requirements)
        # The producer's no-clobber precondition begins with no attempt. The
        # helper was used only to build the deterministic in-memory run.
        shutil.rmtree(run.attempt_root)
        os.chmod(run.production_root / "attempts", 0o700)
        dynamic_capture = _dynamic_capacity_capture(
            governing_commit=run.authority.governing_commit
        )
        capacity_value = _successor_capacity_authority(
            dynamic_capture.observation
        )
        owner_private = run.production_root / "owner_private"
        owner_private.mkdir(mode=0o700)
        _write_private_payload(
            owner_private
            / capacity.DYNAMIC_SUCCESSOR_RESTRICTED_RECEIPT_BASENAME,
            dynamic_capture.receipt_payload,
        )
        _write_private_payload(
            owner_private
            / capacity.DYNAMIC_SUCCESSOR_AGGREGATE_SUMMARY_BASENAME,
            capacity._canonical(dynamic_capture.observation),
        )
        with (
            mock.patch.object(sequential, "build_full_run", return_value=run),
            mock.patch.object(
                capacity,
                "validate_production_dynamic_successor_capacity_capture",
                return_value=dynamic_capture,
            ),
            mock.patch.object(
                sequential,
                "preflight_full",
                return_value={
                    "successor_capacity": capacity_value,
                    "capacity_evidence_role": "R5B_HISTORICAL",
                    "capacity_gain_source": "ALLOCATION",
                    "raw_retirement_status": (
                        "NOT_APPLICABLE_CLEANUP_SKIPPED"
                    ),
                    "raw_retirement_receipt_sha256": (
                        "NOT_APPLICABLE_CLEANUP_SKIPPED"
                    ),
                },
            ),
        ):
            produced = sequential.claim_submission(
                qsub_environment_sha256=BOUND_QSUB_ENVIRONMENT_SHA256
            )
        assert produced["status"] == "READY"
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
                expected = "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID"
            elif role == "capacity":
                capacity_path = (
                    run.attempt_root
                    / "full_capacity_receipt.restricted.json"
                )
                _replace_private_json(capacity_path, {"status": "ALTERED"})
                expected = "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID"
            else:
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
                _replace_private_json(claim_path, {**claim, "unexpected": 1})
                expected = "FULL_SEQUENTIAL_PREPARED_CLAIM_INVALID"

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
