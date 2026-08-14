from __future__ import annotations

"""Focused, dependency-light contracts for the full sequential C3 adapter."""

import base64
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
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

import lvef_c3_full_sequential as sequential
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation


def two_batch_plan() -> tuple[dict[str, Any], core.PlanRequirements]:
    """Return a valid, exact 2x2 miniature of the frozen production plan."""

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
    )
    return plan, requirements


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
        launch_authority={"status": "SYNTHETIC"},
        launch_authority_sha256="5" * 64,
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
            sequential, "_active_extraction_cache_count", return_value=1
        ),
        pytest.raises(sequential.FullSequentialError) as caught,
    ):
        sequential.run_batch_task(task_id=1, run=run, dependencies=dependencies)
    assert caught.value.code == "FULL_SEQUENTIAL_ACTIVE_EXTRACTION_CACHE_PRESENT"
    assert calls == []


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
    launch = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_selected_cohort_launch_authority_v1",
        "status": "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "governing_commit": plan["authority"]["git_commit"],
        "batch_plan_sha256": plan_sha,
        "selected_manifest_sha256": plan["authority"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": plan["authority"]["selected_source_manifest_sha256"],
        "split_map_sha256": plan["authority"]["split_map_sha256"],
        "checkpoint_sha256": plan["authority"]["checkpoint_sha256"],
        "selected_studies": 4,
        "selected_subjects": 4,
        "normalized_source_objects": 4,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_count": 2,
        "expected_no_cine_studies": 1,
        "maximum_scheduler_submissions": 2,
        "array_task_range": "1-2",
        "array_max_concurrency": 1,
        "raw_dicom_deletion_authorized": False,
        "extracted_cache_retirement_authorized_after_preservation": True,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
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


def _claimed_run_fixture(root: Path) -> tuple[
    sequential.FullRun, dict[str, Any], Path
]:
    plan, requirements = two_batch_plan()
    initial = _scoped_run(root, plan, requirements)
    launch = {"status": "SYNTHETIC_CLOSED_LAUNCH"}
    run = replace(
        initial,
        launch_authority=launch,
        launch_authority_sha256=core.canonical_json_sha256(launch),
    )
    capacity_value = {"status": "SYNTHETIC_CAPACITY_PASS"}
    capacity_path = run.attempt_root / "full_capacity_receipt.restricted.json"
    sequential._write_private_json(
        run.attempt_root / "full_launch_authority.restricted.json",
        run.launch_authority,
        attempt_id=run.attempt_id,
    )
    sequential._write_private_json(
        capacity_path, capacity_value, attempt_id=run.attempt_id
    )
    claim = sequential._expected_submission_claim(
        run, capacity_receipt_sha256=core.sha256_file(capacity_path)
    )
    claim_path = run.attempt_root / "full_submission_claim.restricted.json"
    sequential._write_private_json(
        claim_path, claim, attempt_id=run.attempt_id
    )
    return run, capacity_value, claim_path


def _replace_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(core.canonical_json_bytes(value))
    os.chmod(path, 0o600)


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
    capacity_gate.assert_called_once_with(capacity_value)


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
        "storage_reserve": "PASS",
        "echoprime_runtime": "PASS",
        "crc32c_external_runtime": "PASS",
        "completed_canary_evidence": "PASS",
        "task_mappings": mappings,
        "capacity": {
            "research_quota_remaining_bytes": 2_000_000_000_000,
            "research_filesystem_available_bytes": 1_800_000_000_000,
            "research_file_slots_remaining": 4_000_000,
            "research_margin_beyond_200gb_reserve_bytes": 300_000_000_000,
            "backed_quota_remaining_bytes": 20_000_000_000,
            "backed_file_slots_remaining": 200_000,
        },
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
