from __future__ import annotations

"""Exact two-batch synthetic acceptance for the controlling full C3 adapter.

Only the requester-pays transport and encoder compute boundary are replaced.
The fixture is sanitized Part-10 data under a private temporary directory.
"""

import base64
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Mapping, Sequence

import numpy as np
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

    class _Mark:
        @staticmethod
        def parametrize(*_args: Any, **_kwargs: Any) -> Any:
            return lambda function: function

    class _DependencyLightPytest:
        mark = _Mark()
        raises = staticmethod(lambda expected: _Raises(expected))

    pytest = _DependencyLightPytest()


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_full_sequential as sequential
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


def _fixture_module(filename: str, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, ROOT / "tests" / filename)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _two_batch_plan() -> tuple[dict[str, Any], core.PlanRequirements]:
    unit = _fixture_module(
        "test_lvef_c3_full_sequential.py", "full_sequential_unit_fixture"
    )
    return unit.two_batch_plan()


def _part10_fixture(frame_count: int, pixel_offset: int) -> bytes:
    """Build an explicit-VR little-endian RGB Part-10 cine or single frame."""

    canary = _fixture_module(
        "test_lvef_c3_canary_integration.py", "full_sequential_part10_fixture"
    )
    sop_class = "1.2.840.10008.5.1.4.1.1.3.1"
    sop_instance = f"1.2.826.0.1.3680043.10.543.{pixel_offset + 10}"
    transfer_syntax = "1.2.840.10008.1.2.1"
    implementation = "1.2.826.0.1.3680043.10.543.2"
    meta_tail = b"".join(
        (
            canary._dicom_element(0x0002, 0x0001, "OB", b"\0\1"),
            canary._dicom_element(
                0x0002, 0x0002, "UI", canary._dicom_text(sop_class, null_pad=True)
            ),
            canary._dicom_element(
                0x0002, 0x0003, "UI", canary._dicom_text(sop_instance, null_pad=True)
            ),
            canary._dicom_element(
                0x0002,
                0x0010,
                "UI",
                canary._dicom_text(transfer_syntax, null_pad=True),
            ),
            canary._dicom_element(
                0x0002,
                0x0012,
                "UI",
                canary._dicom_text(implementation, null_pad=True),
            ),
        )
    )
    meta = canary._dicom_element(
        0x0002, 0x0000, "UL", struct.pack("<I", len(meta_tail))
    ) + meta_tail
    frames = np.zeros((frame_count, 224, 224, 3), dtype=np.uint8)
    for frame_index in range(frame_count):
        value = np.uint8(35 + pixel_offset * 4 + frame_index * 15)
        frames[frame_index, 30:194, 30:194, :] = value
    dataset = b"".join(
        (
            canary._dicom_element(
                0x0008, 0x0016, "UI", canary._dicom_text(sop_class, null_pad=True)
            ),
            canary._dicom_element(
                0x0008, 0x0018, "UI", canary._dicom_text(sop_instance, null_pad=True)
            ),
            canary._dicom_element(0x0028, 0x0002, "US", struct.pack("<H", 3)),
            canary._dicom_element(0x0028, 0x0004, "CS", canary._dicom_text("RGB")),
            canary._dicom_element(0x0028, 0x0006, "US", struct.pack("<H", 0)),
            canary._dicom_element(
                0x0028, 0x0008, "IS", canary._dicom_text(str(frame_count))
            ),
            canary._dicom_element(0x0028, 0x0010, "US", struct.pack("<H", 224)),
            canary._dicom_element(0x0028, 0x0011, "US", struct.pack("<H", 224)),
            canary._dicom_element(0x0028, 0x0100, "US", struct.pack("<H", 8)),
            canary._dicom_element(0x0028, 0x0101, "US", struct.pack("<H", 8)),
            canary._dicom_element(0x0028, 0x0102, "US", struct.pack("<H", 7)),
            canary._dicom_element(0x0028, 0x0103, "US", struct.pack("<H", 0)),
            canary._dicom_element(0x7FE0, 0x0010, "OB", frames.tobytes(order="C")),
        )
    )
    return b"\0" * 128 + b"DICM" + meta + dataset


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _write_csv(
    path: Path, header: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)


def _fixture_plan() -> tuple[
    dict[str, Any], core.PlanRequirements, dict[str, bytes]
]:
    """Replace unit-plan placeholder sizes with deterministic Part-10 bodies."""

    original, _ = _two_batch_plan()
    payloads: dict[str, bytes] = {}
    selected = [study for batch in original["batches"] for study in batch["studies"]]
    sources: list[dict[str, Any]] = []
    total = 0
    for ordinal, prior in enumerate(
        source for batch in original["batches"] for source in batch["objects"]
    ):
        # The final selected study is the sole true no-multiframe disposition.
        payload = _part10_fixture(1 if ordinal == 3 else 4, ordinal + 1)
        payloads[prior["source_object_key"]] = payload
        total += len(payload)
        sources.append(
            {
                **prior,
                "production_batch": f"c3_batch_{ordinal // 2:03d}",
                "size_bytes": len(payload),
                "generation": str(9000 + ordinal),
                "md5_base64": base64.b64encode(
                    hashlib.md5(payload, usedforsecurity=False).digest()
                ).decode("ascii"),
                "crc32c_base64": _fixture_module(
                    "test_lvef_c3_canary_integration.py",
                    "full_sequential_crc_fixture",
                )._synthetic_crc32c_base64(payload),
            }
        )
    requirements = core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=4,
        selected_subjects=4,
        normalized_source_objects=4,
        selected_source_bytes=total,
        batch_count=2,
        studies_per_full_batch=2,
        final_batch_studies=2,
        contract_id="synthetic_two_batch_full_sequential_v1",
    )
    selected_source = [
        {
            "release_id": requirements.release,
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "split": row["split"],
            "source_relative_path": row["source_relative_path"],
            "source_object_key": row["source_object_key"],
        }
        for row in sources
    ]
    metadata = [
        {
            **row,
            "remote_size_bytes": source["size_bytes"],
            "remote_generation": source["generation"],
            "remote_md5_base64": source["md5_base64"],
            "remote_crc32c_base64": source["crc32c_base64"],
            "production_batch": source["production_batch"],
            "preflight_status": "PASS",
            "discrepancy_reasons": [],
        }
        for row, source in zip(selected_source, sources, strict=True)
    ]
    reconciled = core.reconcile_selected_source_metadata(
        selected_source, metadata, release=requirements.release
    )
    normalized_sources = [
        {
            "release_id": row["release_id"],
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "split": row["split"],
            "source_relative_path": row["source_relative_path"],
            "source_object_key": row["source_object_key"],
            "production_batch": row["production_batch"],
            "size_bytes": row["remote_size_bytes"],
            "generation": row["remote_generation"],
            "md5_base64": row["remote_md5_base64"],
            "crc32c_base64": row["remote_crc32c_base64"],
        }
        for row in reconciled
    ]
    plan = core.build_immutable_batch_plan(
        selected,
        normalized_sources,
        [
            {"subject_id": row["subject_id"], "split": "train"}
            for row in selected
        ],
        requirements=requirements,
        authority=original["authority"],
        prespecified_no_cine_studies=[
            {
                "subject_id": selected[-1]["subject_id"],
                "study_id": selected[-1]["study_id"],
            }
        ],
    )
    return plan, requirements, payloads


def _launch_authority(
    plan: Mapping[str, Any], requirements: core.PlanRequirements
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "artifact_type": "lvef_c3_full_selected_cohort_launch_authority_v2",
        "status": "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "governing_commit": plan["authority"]["git_commit"],
        "batch_plan_sha256": core.canonical_json_sha256(plan),
        "selected_manifest_sha256": plan["authority"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": plan["authority"]["selected_source_manifest_sha256"],
        "split_map_sha256": plan["authority"]["split_map_sha256"],
        "checkpoint_sha256": plan["authority"]["checkpoint_sha256"],
        "selected_studies": requirements.selected_studies,
        "selected_subjects": requirements.selected_subjects,
        "normalized_source_objects": requirements.normalized_source_objects,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_count": requirements.batch_count,
        "expected_no_cine_studies": 1,
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


def _scoped_production_run(
    root: Path,
    *,
    fixture: ModuleType,
    minimal_fixture: ModuleType,
) -> tuple[
    sequential.FullRun,
    dict[str, bytes],
    list[dict[str, str]],
]:
    """Build a public FullRun directly; production builders stay frozen at 19."""

    authority, _, _, package_inventory = (
        minimal_fixture._synthetic_authority_and_manifest(fixture, root)
    )
    template, requirements, payloads = _fixture_plan()
    contract_path = ROOT / "configs/lvef_c3_orchestration_v2.yaml"
    contract = core.load_orchestration_contract(contract_path)
    plan_authority = {
        "git_commit": authority.governing_commit,
        "orchestration_contract_sha256": core.sha256_file(contract_path),
        "selected_manifest_sha256": template["authority"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": template["authority"]["selected_source_manifest_sha256"],
        "source_metadata_sha256": template["authority"]["source_metadata_sha256"],
        "split_map_sha256": template["authority"]["split_map_sha256"],
        "checkpoint_sha256": core.sha256_file(authority.checkpoint),
        "environment_receipt_sha256": core.sha256_file(authority.environment_receipt),
        "state_machine_schema_sha256": core.sha256_file(
            ROOT / "configs/lvef_c3_state_machine_v2.json"
        ),
        "resume_ledger_schema_sha256": core.sha256_file(
            ROOT / "configs/lvef_c3_resume_ledger_v2.json"
        ),
        "gcloud_resolution_receipt_sha256": core.sha256_file(
            authority.gcloud_receipt
        ),
        "gcloud_executable_sha256": core.sha256_file(authority.gcloud),
        "crc32c_python_executable_sha256": core.sha256_file(
            authority.crc32c_python
        ),
        "crc32c_worker_sha256": core.sha256_file(authority.crc32c_worker),
        "crc32c_distribution_sha256": authority.crc32c_distribution_sha256,
    }
    selected = [study for batch in template["batches"] for study in batch["studies"]]
    sources = [
        {**source, "production_batch": batch["batch_id"]}
        for batch in template["batches"]
        for source in batch["objects"]
    ]
    plan = core.build_immutable_batch_plan(
        selected,
        sources,
        [{"subject_id": row["subject_id"], "split": "train"} for row in selected],
        requirements=requirements,
        authority=plan_authority,
        prespecified_no_cine_studies=[
            key
            for batch in template["batches"]
            for key in batch["prespecified_no_cine_study_keys"]
        ],
    )
    plan_sha = core.canonical_json_sha256(plan)
    runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha}
    )
    attempt_id = f"lvef_c3_full_{plan_sha[:16]}_{authority.governing_commit[:8]}"
    production_root = root / "production"
    attempt_root = production_root / "attempts" / attempt_id
    attempt_root.mkdir(mode=0o700, parents=True)
    os.chmod(production_root, 0o700)
    os.chmod(production_root / "attempts", 0o700)
    os.chmod(attempt_root, 0o700)
    plan_path = attempt_root / "full_batch_plan.restricted.json"
    plan_path.write_bytes(core.canonical_json_bytes(plan))
    os.chmod(plan_path, 0o600)
    launch = _launch_authority(plan, requirements)
    run = sequential.FullRun(
        authority=authority,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=contract_path,
        plan_sha256=plan_sha,
        runtime_authority=runtime,
        attempt_id=attempt_id,
        production_root=production_root,
        attempt_root=attempt_root,
        plan_path=plan_path,
        launch_authority=launch,
        launch_authority_sha256=core.canonical_json_sha256(launch),
        scheduler_job_identity="synthetic.full.sequential",
    )
    return run, payloads, package_inventory


class _SyntheticDigestProvider:
    def __init__(self, fixture: ModuleType, calls: dict[str, int]):
        self.fixture = fixture
        self.calls = calls

    def __enter__(self) -> _SyntheticDigestProvider:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def digest(
        self, path: Path, request_id: str, *, chunk_size: int = 8 * 1024 * 1024
    ) -> dict[str, Any]:
        assert re.fullmatch(r"[0-9a-f]{64}", request_id)
        before = path.stat(follow_symlinks=False)
        body = path.read_bytes()
        after = path.stat(follow_symlinks=False)
        assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
        )
        self.calls["synthetic_external_crc32c_digests"] = self.calls.get(
            "synthetic_external_crc32c_digests", 0
        ) + 1
        return {
            "protocol_version": 1,
            "status": "PASS",
            "request_id": request_id,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "md5_base64": base64.b64encode(
                hashlib.md5(body, usedforsecurity=False).digest()
            ).decode("ascii"),
            "crc32c_base64": self.fixture._synthetic_crc32c_base64(body),
            "file_device": int(after.st_dev),
            "file_inode": int(after.st_ino),
            "file_mtime_ns": int(after.st_mtime_ns),
            "chunk_size_bytes": chunk_size,
            "backend": "google_crc32c_c_external_worker_v1",
        }


def _transport(payloads: Mapping[str, bytes], calls: dict[str, int]) -> Any:
    class Transport:
        def fetch(
            self,
            expectation: Any,
            *,
            partial_path: Path,
            billing_project: str,
            access_token: str,
        ) -> dict[str, Any]:
            assert billing_project == "synthetic-private-project"
            assert access_token == "synthetic-token"
            body = payloads[expectation.source_object_key]
            partial_path.write_bytes(body)
            calls["mock_requester_pays_transfer"] = calls.get(
                "mock_requester_pays_transfer", 0
            ) + 1
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": len(body),
                "resume_offset_bytes": 0,
                "response_body_bytes_read": len(body),
                "final_partial_size_bytes": len(body),
                "content_range_validated": False,
            }

    return Transport()


def _token_provider(authority: Any, calls: dict[str, int]) -> Any:
    class Provider:
        @staticmethod
        def validate_authority() -> dict[str, str]:
            return {
                "gcloud_resolution_receipt_sha256": core.sha256_file(
                    authority.gcloud_receipt
                ),
                "gcloud_executable_sha256": core.sha256_file(authority.gcloud),
            }

        @staticmethod
        def __call__() -> str:
            calls["mock_token_acquisition"] = calls.get(
                "mock_token_acquisition", 0
            ) + 1
            return "synthetic-token"

    return Provider()


def test_exact_two_batch_fixture_is_real_part10_with_one_no_cine(
    tmp_path: Path,
) -> None:
    plan, requirements, payloads = _fixture_plan()
    assert core.aggregate_batch_plan(plan, requirements=requirements)["batch_count"] == 2
    assert [row["n_studies"] for row in plan["batches"]] == [2, 2]
    assert len(payloads) == 4
    assert all(body[128:132] == b"DICM" for body in payloads.values())

    canary = _fixture_module(
        "test_lvef_c3_canary_integration.py", "full_sequential_reader_fixture"
    )
    observed_frames = []
    for index, body in enumerate(payloads.values()):
        path = tmp_path / f"fixture_{index}.dcm"
        path.write_bytes(body)
        observed_frames.append(
            canary._read_explicit_vr_part10(
                str(path), stop_before_pixels=True, force=False
            ).NumberOfFrames
        )
    observed_frames.sort()
    assert observed_frames == [1, 4, 4, 4]


def test_cross_batch_finalizer_rejects_pooling_and_no_cine_mutations(
    tmp_path: Path,
) -> None:
    """Exercise independent finalizer replay over exact retained vectors."""

    plan, _, _ = _fixture_plan()
    clip_rows = [
        {
            "embedding_idx": index,
            "subject_id": study["subject_id"],
            "study_id": study["study_id"],
            "clip_key": hashlib.sha256(f"clip:{index}".encode()).hexdigest(),
            "physical_source_key": plan["batches"][0]["objects"][index]["source_object_key"],
            "embedding_l2_norm": "0",
            "embedding_sha256": "0" * 64,
            "write_ok": "true",
        }
        for index, study in enumerate(plan["batches"][0]["studies"])
    ]
    clips = np.stack(
        [np.arange(512, dtype=np.float32) / 1000 + index for index in range(2)]
    )
    smoke = _fixture_module(
        "test_lvef_c3_canary_integration.py", "full_sequential_hash_fixture"
    ).reconstruction
    for row, vector in zip(clip_rows, clips, strict=True):
        row["embedding_l2_norm"] = str(float(np.linalg.norm(vector.astype(np.float64))))
        row["embedding_sha256"] = smoke.array_content_sha256(vector)
    study_rows = [
        {
            "study_idx": index,
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "n_clips": 1,
            "embedding_sha256": smoke.array_content_sha256(clips[index]),
        }
        for index, row in enumerate(plan["batches"][0]["studies"])
    ]
    disposition = [
        {
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "disposition": "IMAGING_ELIGIBLE",
        }
        for row in plan["batches"][0]["studies"]
    ]
    _write_csv(tmp_path / "clip.csv", finalizer.CLIP_MANIFEST_HEADER, clip_rows)
    _write_csv(tmp_path / "study.csv", finalizer.STUDY_MANIFEST_HEADER, study_rows)
    _write_csv(tmp_path / "disposition.csv", finalizer.DISPOSITION_HEADER, disposition)
    np.savez(tmp_path / "clips.npz", embeddings=clips)
    np.savez(tmp_path / "studies.npz", embeddings=clips)

    records = finalizer.replay_batch_study_embeddings(
        clip_manifest_path=tmp_path / "clip.csv",
        clip_embeddings_path=tmp_path / "clips.npz",
        study_manifest_path=tmp_path / "study.csv",
        study_embeddings_path=tmp_path / "studies.npz",
        disposition_path=tmp_path / "disposition.csv",
        planned_batch=plan["batches"][0],
        expected_clip_embeddings=2,
        expected_study_embeddings=2,
        expected_no_cine_studies=0,
    )
    assert len(records) == 2

    mutated = clips.copy()
    mutated[0, 0] += np.float32(1)
    np.savez(tmp_path / "studies.npz", embeddings=mutated)
    with pytest.raises(finalizer.ProductionFinalizationError) as caught:
        finalizer.replay_batch_study_embeddings(
            clip_manifest_path=tmp_path / "clip.csv",
            clip_embeddings_path=tmp_path / "clips.npz",
            study_manifest_path=tmp_path / "study.csv",
            study_embeddings_path=tmp_path / "studies.npz",
            disposition_path=tmp_path / "disposition.csv",
            planned_batch=plan["batches"][0],
            expected_clip_embeddings=2,
            expected_study_embeddings=2,
            expected_no_cine_studies=0,
        )
    assert caught.value.code in {
        "FINALIZER_BATCH_EMBEDDING_AUTHORITY_INVALID",
        "FINALIZER_STUDY_POOLING_RECOMPUTATION_MISMATCH",
    }


@pytest.mark.parametrize("mutation", ["environment", "checkpoint"])
def test_changed_runtime_authority_fails_before_transport(
    tmp_path: Path, mutation: str
) -> None:
    canary = _fixture_module(
        "test_lvef_c3_canary_integration.py",
        f"full_sequential_prebody_canary_{mutation}",
    )
    minimal = _fixture_module(
        "test_lvef_c3_minimal_canary_integration.py",
        f"full_sequential_prebody_minimal_{mutation}",
    )
    run, _, _ = _scoped_production_run(
        tmp_path, fixture=canary, minimal_fixture=minimal
    )
    calls: list[str] = []

    def forbidden(name: str):
        def operation(*_args: Any, **_kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} crossed the runtime authority gate")

        return operation

    environment_validator: Any = None
    expected_code = "FULL_SEQUENTIAL_PREBODY_ENVIRONMENT_AUTHORITY_FAILED"
    if mutation == "environment":
        run.authority.environment_receipt.write_bytes(
            run.authority.environment_receipt.read_bytes() + b" "
        )
    else:
        run.authority.checkpoint.write_bytes(run.authority.checkpoint.read_bytes() + b"x")
        environment_validator = lambda *_args, **_kwargs: {"status": "PASS"}
        expected_code = "FULL_SEQUENTIAL_PREBODY_CHECKPOINT_AUTHORITY_FAILED"
    dependencies = sequential.FullDependencies(
        environment_validator=environment_validator,
        token_provider_factory=forbidden("token"),
        transport_factory=forbidden("transport"),
        digest_provider_factory=forbidden("digest"),
        download=forbidden("download"),
    )
    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential.run_batch_task(task_id=1, run=run, dependencies=dependencies)
    assert caught.value.code == expected_code
    assert calls == []


def test_full_wrapper_two_batch_science_acceptance(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Run the two-batch scientific path; this is not the scale-authority test."""

    canary = _fixture_module(
        "test_lvef_c3_canary_integration.py", "full_sequential_science_fixture"
    )
    minimal = _fixture_module(
        "test_lvef_c3_minimal_canary_integration.py",
        "full_sequential_compute_fixture",
    )
    calls: dict[str, int] = {}
    run, payloads, package_inventory = _scoped_production_run(
        tmp_path,
        fixture=canary,
        minimal_fixture=minimal,
    )

    def restricted_temp_path(path: Path, *, must_exist: bool = False) -> Path:
        candidate = Path(path)
        if (
            not candidate.is_absolute()
            or candidate.is_symlink()
            or not candidate.is_relative_to(tmp_path)
            or (must_exist and not candidate.exists())
        ):
            raise stages.ProductionStageError("SYNTHETIC_PATH_OUTSIDE_TEST_ROOT")
        return candidate

    checkpoint_bytes = run.authority.checkpoint.read_bytes()
    monkeypatch.setattr(stages, "require_projectnb_path", restricted_temp_path)
    monkeypatch.setattr(stages, "CANONICAL_REPOSITORY_ROOT", ROOT)
    monkeypatch.setattr(
        stages.importlib.metadata,
        "distributions",
        lambda: [
            SimpleNamespace(metadata={"Name": row["name"]}, version=row["version"])
            for row in package_inventory
        ],
    )
    monkeypatch.setattr(stages, "CHECKPOINT_FILENAME", run.authority.checkpoint.name)
    monkeypatch.setattr(stages, "CHECKPOINT_BYTES", len(checkpoint_bytes))
    monkeypatch.setattr(
        stages, "CHECKPOINT_SHA256", hashlib.sha256(checkpoint_bytes).hexdigest()
    )
    monkeypatch.setattr(
        stages,
        "validate_crc32c_external_authority",
        lambda environment_receipt, *_args: stages.load_json_object(
            environment_receipt, "SYNTHETIC_ENVIRONMENT_RECEIPT"
        ),
    )
    dependencies = sequential.FullDependencies(
        token_provider_factory=lambda active: _token_provider(active, calls),
        transport_factory=lambda: _transport(payloads, calls),
        digest_provider_factory=lambda _active: _SyntheticDigestProvider(canary, calls),
        test_only_synthetic_full_scope=True,
        extraction_workers=1,
        echoprime_batch_size=8,
        monotonic_clock=lambda: 0.0,
        sleeper=lambda _seconds: (_ for _ in ()).throw(
            AssertionError("synthetic transfer must not retry")
        ),
    )

    with pytest.raises(sequential.FullSequentialError) as caught:
        sequential.run_batch_task(task_id=2, run=run, dependencies=dependencies)
    assert caught.value.code == "PRIOR_FINAL_RECEIPT_NOT_REGULAR"
    assert calls.get("mock_requester_pays_transfer", 0) == 0

    modules = {
        "pydicom": canary._synthetic_pydicom_module(),
        "cv2": canary._synthetic_cv2_module(),
        **minimal._mocked_torch_modules(canary, calls),
    }
    with canary._installed_dependency_modules(modules):
        first = sequential.run_batch_task(
            task_id=1, run=run, dependencies=dependencies
        )
        assert first["status"] == "PASS_BATCH_FINALIZED"
        assert first["extracted_cache_retired"] is True
        assert first["raw_dicoms_retained"] is True

        second = sequential.run_batch_task(
            task_id=2, run=run, dependencies=dependencies
        )
        assert second["status"] == "PASS_BATCH_FINALIZED"
        assert second["n_no_cine_studies"] == 1
        assert second["extracted_cache_retired"] is True
        assert second["raw_dicoms_retained"] is True
    disposition_class = stages.OBJECT_TECHNICAL_DISPOSITION
    disposition_hashes: list[str] = []
    for batch_id, receipt in (
        ("c3_batch_000", first),
        ("c3_batch_001", second),
    ):
        assert receipt["schema_version"] == 2
        assert receipt["artifact_type"] == (
            "lvef_c3_batch_finalization_receipt_v3"
        )
        assert receipt["n_object_technical_dispositions"] == 0
        assert receipt["n_studies_affected_by_technical_disposition"] == 0
        assert receipt["n_new_no_cine_studies"] == 0
        assert receipt["technical_disposition_counts_by_class"] == {
            disposition_class: 0
        }
        assert receipt["technical_disposition_policy_version"] == (
            stages.OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
        )
        assert receipt["object_substitution_count"] == 0
        assert receipt["unaccounted_multiframe_objects"] == 0
        paths = sequential._batch_paths(run, batch_id)
        technical_path = (
            paths["extraction"]
            / "technical_disposition_manifest.restricted.csv"
        )
        assert technical_path.read_text(encoding="utf-8") == (
            ",".join(stages.TECHNICAL_DISPOSITION_MANIFEST_HEADER) + "\n"
        )
        technical_sha = stages.technical_disposition_manifest_sha256(
            technical_path
        )
        disposition_hashes.append(technical_sha)
        dicom_summary = core.load_strict_json(
            paths["extraction"] / "dicom_extraction.summary.json"
        )
        echoprime_summary = core.load_strict_json(
            paths["echoprime"] / "echoprime_pooling.summary.json"
        )
        for value in (dicom_summary, echoprime_summary, receipt):
            assert value["technical_disposition_manifest_sha256"] == (
                technical_sha
            )
            assert value["technical_disposition_counts_by_class"] == {
                disposition_class: 0
            }
            assert value["n_object_technical_dispositions"] == 0
            assert value["n_studies_affected_by_technical_disposition"] == 0
            assert value["n_new_no_cine_studies"] == 0
            assert value["object_substitution_count"] == 0
            assert value["unaccounted_multiframe_objects"] == 0
        assert not os.path.lexists(paths["batch_root"] / "echoprime.partial")
    assert calls["mock_requester_pays_transfer"] == 4
    assert calls["mock_encoder_compute"] == 2

    result = sequential.run_cross_batch_finalizer(run=run)
    assert result["status"] == "PASS_FULL_SELECTED_COHORT_RECONSTRUCTION"
    assert result["selected_studies"] == 4
    assert result["n_pooled_studies"] == 3
    assert result["n_no_cine_studies"] == 1
    assert result["exact_pooling_replay_passed"] is True
    assert result["raw_dicoms_retained"] is True
    assert result["object_technical_dispositions"] == 0
    assert result["studies_affected_by_technical_disposition"] == 0
    assert result["new_no_cine_studies"] == 0
    assert result["technical_disposition_counts_by_class"] == {
        disposition_class: 0
    }
    assert result["object_substitution_count"] == 0
    assert result["unaccounted_multiframe_objects"] == 0
    assert result["technical_disposition_manifest_set_sha256"] == (
        hashlib.sha256(
            ("\n".join(sorted(disposition_hashes)) + "\n").encode("ascii")
        ).hexdigest()
    )
    for gate in (
        "all_extraction_rows_resolved",
        "all_successful_extractions_embedded",
        "all_technical_dispositions_retained",
        "all_embeddings_passed",
        "all_pooling_passed",
        "all_preservation_manifests_passed",
    ):
        assert result[gate] is True
    assert result["model_fitting_count"] == 0
    assert result["endpoint_prediction_count"] == 0
    assert result["confirmatory_performance_access_count"] == 0
    output_root = run.attempt_root / "cohort_finalization"
    clip_index_path = output_root / finalizer.CANONICAL_CLIP_INDEX_NAME
    study_embeddings_path = output_root / finalizer.CANONICAL_STUDY_EMBEDDINGS_NAME
    study_manifest_path = output_root / finalizer.CANONICAL_STUDY_MANIFEST_NAME
    study_receipt_path = output_root / finalizer.CANONICAL_STUDY_RECEIPT_NAME
    cohort_receipt_path = output_root / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
    with clip_index_path.open(newline="", encoding="utf-8") as handle:
        clip_index = list(csv.DictReader(handle))
    assert [row["batch_id"] for row in clip_index] == [
        "c3_batch_000",
        "c3_batch_000",
        "c3_batch_001",
    ]
    assert [int(row["batch_embedding_idx"]) for row in clip_index] == [0, 1, 0]
    assert len({row["clip_key"] for row in clip_index}) == 3
    assert len({row["physical_source_key"] for row in clip_index}) == 3
    cohort_receipt = finalizer.replay_cohort_preservation_receipt(
        cohort_receipt_path, artifact_root=run.production_root
    )
    assert cohort_receipt["batch_receipt_set_sha256"] == result[
        "batch_receipt_set_sha256"
    ]
    assert len(cohort_receipt["artifacts"]) == 8
    assert result["canonical_clip_index_rows"] == 3
    assert result["canonical_clip_index_sha256"] == core.sha256_file(
        clip_index_path
    )
    assert result["canonical_study_embeddings_sha256"] == core.sha256_file(
        study_embeddings_path
    )
    assert result["canonical_study_manifest_sha256"] == core.sha256_file(
        study_manifest_path
    )
    assert result["canonical_study_store_receipt_sha256"] == core.sha256_file(
        study_receipt_path
    )
    assert result["cohort_preservation_receipt_sha256"] == core.sha256_file(
        cohort_receipt_path
    )
    assert result["cohort_preserved_artifacts"] == 8
    assert result["cohort_preservation_second_pass_replay_passed"] is True
    assert result["cohort_preservation_passed"] is True
    terminal = core.load_strict_json(
        output_root / "full_c3_finalization.aggregate_safe.json"
    )
    finalizer.validate_closed_final_summary(terminal)
    assert set(terminal) == finalizer.FINAL_KEYS
    assert terminal["model_fitting_count"] == 0
    assert terminal["endpoint_prediction_count"] == 0
    assert terminal["confirmatory_performance_access_count"] == 0
    for key in finalizer.FINAL_BINDING_KEYS:
        assert terminal[key] == result[key]
