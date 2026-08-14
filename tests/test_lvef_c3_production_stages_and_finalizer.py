from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import json
import os
import stat
import sys
from argparse import Namespace
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stages = _load("lvef_c3_production_stages_test", "lvef_c3_production_stages.py")
finalizer = _load("finalize_lvef_c3_production_test", "finalize_lvef_c3_production.py")
dispatch_auth = _load(
    "validate_lvef_c3_dispatch_authorization_test",
    "validate_lvef_c3_dispatch_authorization.py",
)
retirement = _load(
    "retire_lvef_c3_extracted_cache_v2_test",
    "retire_lvef_c3_extracted_cache_v2.py",
)
owner_authorization = _load(
    "build_lvef_c3_owner_authorization_receipt_test",
    "build_lvef_c3_owner_authorization_receipt.py",
)
prior_batch_gate = _load(
    "validate_lvef_c3_prior_batch_finalization_test",
    "validate_lvef_c3_prior_batch_finalization.py",
)
analysis_modes = _load(
    "lvef_multitask_analysis_modes_test", "lvef_multitask_analysis_modes.py"
)


def _synthetic_dispatch_launch(root: Path) -> tuple[Path, dict[str, object]]:
    path = root / "launch_authority.restricted.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o600)
    value: dict[str, object] = {
        "post_expansion_capacity_summary": {"sha256": "d" * 64},
        "production_authority_packet": {"sha256": "e" * 64},
    }
    return path, value


def _dispatch_payload(
    *, root: Path, contract: Path, plan: Path, environment: Path,
    stage: str, task_scope: str,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    launch_path, launch_value = _synthetic_dispatch_launch(root)
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_dispatch_authorization_v1",
        "status": "AUTHORIZED",
        "stage": stage,
        "authorized_array_range": task_scope,
        "governing_commit": "a" * 40,
        "attempt_id": "lvef_c3_phase1ee_synthetic_001",
        "orchestration_contract_sha256": dispatch_auth.sha256_file(contract),
        "batch_plan_sha256": dispatch_auth.sha256_file(plan),
        "execution_environment_sha256": dispatch_auth.sha256_file(environment),
        "launch_authority_sha256": dispatch_auth.sha256_file(launch_path),
        "post_expansion_capacity_summary_sha256": "d" * 64,
        "production_authority_packet_sha256": "e" * 64,
        "owner_authorized": True,
        "owner_authorization_date": "2026-08-10",
    }
    return launch_path, launch_value, payload


def _validate_dispatch_with_stub(
    receipt: Path, *, launch_path: Path, launch_value: dict[str, object], **kwargs
) -> None:
    with mock.patch.object(
        dispatch_auth.launch, "_load_json", return_value=launch_value
    ), mock.patch.object(dispatch_auth.launch, "validate", return_value=launch_value):
        dispatch_auth.validate(
            receipt,
            launch_authority=launch_path,
            attempt_id="lvef_c3_phase1ee_synthetic_001",
            **kwargs,
        )


def expect_code(code: str, function) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", None) == code
    else:
        raise AssertionError(f"Expected {code}")


def test_production_dicom_validator_separates_single_multiframe_and_decode_failure() -> None:
    rows = [
        {
            "subject_id": 1,
            "study_id": 10,
            "source_relative_path": "files/a.dcm",
            "read_ok": True,
            "is_multiframe": True,
            "pixel_decode_ok": True,
        },
        {
            "subject_id": 1,
            "study_id": 10,
            "source_relative_path": "files/b.dcm",
            "read_ok": True,
            "is_multiframe": False,
            "pixel_decode_ok": False,
        },
        {
            "subject_id": 2,
            "study_id": 20,
            "source_relative_path": "files/c.dcm",
            "read_ok": True,
            "is_multiframe": True,
            "pixel_decode_ok": False,
        },
    ]
    value = stages.validate_production_dicom_rows(
        rows, expected_objects=3, expected_studies=2
    )
    assert value["n_multiframe_candidates"] == 2
    assert value["n_single_frame"] == 1
    assert value["n_pixel_decode_failures"] == 1


def test_production_dicom_validator_rejects_contradiction_and_duplicate_source() -> None:
    row = {
        "subject_id": 1,
        "study_id": 10,
        "source_relative_path": "files/a.dcm",
        "read_ok": False,
        "is_multiframe": True,
        "pixel_decode_ok": False,
    }
    expect_code(
        "DICOM_AUDIT_STATE_CONTRADICTION",
        lambda: stages.validate_production_dicom_rows(
            [row], expected_objects=1, expected_studies=1
        ),
    )
    row["read_ok"] = True
    row["pixel_decode_ok"] = True
    expect_code(
        "DUPLICATE_PHYSICAL_SOURCE",
        lambda: stages.validate_production_dicom_rows(
            [row, dict(row)], expected_objects=2, expected_studies=1
        ),
    )


def _extraction_row(seed: str = "a") -> dict[str, object]:
    return {
        "study_id": 1,
        "clip_key": hashlib.sha256(f"clip-{seed}".encode()).hexdigest(),
        "physical_source_key": hashlib.sha256(f"source-{seed}".encode()).hexdigest(),
        "write_ok": True,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "mask_status": "APPLIED",
        "temporal_sampling_policy": "historical_compatible_linspace_or_tail_repeat_v1",
        "pixel_decode_ok": True,
        "npz_sha256": hashlib.sha256(f"npz-{seed}".encode()).hexdigest(),
    }


def test_production_extraction_validator_rejects_shape_and_duplicate_clip_key() -> None:
    good = _extraction_row()
    assert stages.validate_production_extraction_rows(
        [good], expected_cines=1
    )["all_shapes_and_dtypes_valid"]
    malformed = dict(good, frames_shape="31x224x224x3")
    expect_code(
        "EXTRACTION_SHAPE_OR_DTYPE_MISMATCH",
        lambda: stages.validate_production_extraction_rows(
            [malformed], expected_cines=1
        ),
    )
    duplicate = _extraction_row("b")
    duplicate["clip_key"] = good["clip_key"]
    expect_code(
        "DUPLICATE_CLIP_KEY",
        lambda: stages.validate_production_extraction_rows(
            [good, duplicate], expected_cines=2
        ),
    )


def test_embedding_validator_rejects_wrong_dimension_nonfinite_and_boolean() -> None:
    good = [[0.25] * 512]
    assert stages.validate_embedding_values(good, expected_rows=1)["all_finite"]
    expect_code(
        "EMBEDDING_DIMENSION_MISMATCH",
        lambda: stages.validate_embedding_values([[0.25] * 511], expected_rows=1),
    )
    nonfinite = [[0.25] * 512]
    nonfinite[0][8] = float("nan")
    expect_code(
        "EMBEDDING_VALUE_NONFINITE",
        lambda: stages.validate_embedding_values(nonfinite, expected_rows=1),
    )
    boolean = [[0.25] * 512]
    boolean[0][8] = True
    expect_code(
        "EMBEDDING_VALUE_NOT_NUMERIC",
        lambda: stages.validate_embedding_values(boolean, expected_rows=1),
    )


def test_wrapper_authority_accepts_exact_five_scope_and_rejects_drift() -> None:
    import lvef_c3_orchestration_core as core

    governing_commit = "a" * 40
    attempt_id = "lvef_c3_canary_regression_001"
    batch_id = "c3_batch_000"
    release = "mimic-iv-echo/1.0"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        authority_worktree = root / "authority"
        authority_worktree.mkdir()
        contract_path = root / "contract.yaml"
        contract_path.write_text("synthetic exact-five contract\n", encoding="utf-8")
        plan_path = root / "plan.json"
        plan_path.write_text("{}\n", encoding="utf-8")
        environment_path = root / "environment.json"
        environment_path.write_text("{}\n", encoding="utf-8")
        output_root = root / "output"
        output_root.mkdir()

        plan_authority = {
            key: hashlib.sha256(f"canary-{key}".encode()).hexdigest()
            for key in core.PLAN_AUTHORITY_KEYS
        }
        plan_authority.update(
            {
                "git_commit": governing_commit,
                "orchestration_contract_sha256": stages.sha256_file(contract_path),
                "checkpoint_sha256": stages.CHECKPOINT_SHA256,
                "environment_receipt_sha256": stages.sha256_file(environment_path),
            }
        )
        studies = [
            {
                "subject_id": str(800_000 + index),
                "study_id": str(900_000 + index),
                "split": "train",
            }
            for index in range(1, 6)
        ]
        objects = []
        for index, study in enumerate(studies, start=1):
            relative = (
                f"files/p00/p{study['subject_id']}/s{study['study_id']}/"
                f"synthetic_{index:03d}.dcm"
            )
            objects.append(
                {
                    **study,
                    "source_object_key": hashlib.sha256(
                        f"{release}\0{relative}".encode()
                    ).hexdigest(),
                    "source_relative_path": relative,
                    "size_bytes": 100,
                    "generation": str(index),
                    "md5_base64": "AAAAAAAAAAAAAAAAAAAAAA==",
                    "crc32c_base64": "AAAAAA==",
                }
            )
        batch = {
            "batch_id": batch_id,
            "ordinal": 0,
            "n_studies": 5,
            "n_subjects": 5,
            "n_objects": 5,
            "source_bytes": 500,
            "study_membership_sha256": core.canonical_json_sha256(studies),
            "source_membership_sha256": core.canonical_json_sha256(objects),
            "studies": studies,
            "objects": objects,
        }
        plan = {
            "schema_version": 2,
            "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v2",
            "contract_id": "lvef_multitask_c3_exact_five_canary_v1",
            "algorithm": "numeric_subject_then_numeric_study_contiguous_v1",
            "authority": plan_authority,
            "cohort": {
                "release": release,
                "selected_studies": 5,
                "selected_subjects": 5,
                "normalized_source_objects": 5,
                "selected_source_bytes": 500,
            },
            "largest_batch": {
                "batch_id": batch_id,
                "n_objects": 5,
                "source_bytes": 500,
            },
            "batches": [batch],
        }
        requirements = core.PlanRequirements(
            release=release,
            selected_studies=5,
            selected_subjects=5,
            normalized_source_objects=5,
            selected_source_bytes=500,
            batch_count=1,
            studies_per_full_batch=5,
            final_batch_studies=5,
            contract_id="lvef_multitask_c3_exact_five_canary_v1",
        )
        plan_sha256 = core.validate_batch_plan(plan, requirements=requirements)
        runtime_authority = {
            **plan_authority,
            "batch_plan_sha256": plan_sha256,
        }
        contract = {
            "authority": {
                "state_machine_schema_sha256": plan_authority[
                    "state_machine_schema_sha256"
                ],
                "resume_ledger_schema_sha256": plan_authority[
                    "resume_ledger_schema_sha256"
                ],
            }
        }
        current = {"plan": plan, "contract": contract}

        def invoke(
            *,
            supplied_requirements=requirements,
            supplied_runtime_authority=runtime_authority,
        ):
            return stages.validate_wrapper_authority(
                stage="ECHOPRIME_EMBEDDING",
                batch_id=batch_id,
                attempt_id=attempt_id,
                governing_commit=governing_commit,
                authority_worktree=authority_worktree,
                orchestration_contract=contract_path,
                batch_plan=plan_path,
                environment_receipt=environment_path,
                output_root=output_root,
                requirements=supplied_requirements,
                expected_runtime_authority=supplied_runtime_authority,
            )

        with mock.patch.object(
            stages, "require_authority_worktree"
        ), mock.patch.object(
            stages, "validate_environment_receipt_against_current_runtime"
        ), mock.patch.object(
            stages, "require_projectnb_path", return_value=output_root
        ), mock.patch.object(
            core,
            "load_orchestration_contract",
            side_effect=lambda _path: current["contract"],
        ), mock.patch.object(
            core, "load_strict_json", side_effect=lambda _path: current["plan"]
        ), mock.patch.object(
            core, "production_requirements"
        ) as production_requirements, mock.patch.object(
            core, "derive_expected_runtime_authority"
        ) as derive_runtime:
            accepted = invoke()
            assert accepted["batch_plan_sha256"] == plan_sha256
            assert accepted["runtime_authority"] == dict(
                sorted(runtime_authority.items())
            )
            assert accepted["expected_object_keys"] == {
                row["source_object_key"] for row in objects
            }
            assert accepted["real_execution_performed"] is False
            production_requirements.assert_not_called()
            derive_runtime.assert_not_called()

            expect_code(
                "SCOPED_RUNTIME_AUTHORITY_ARGUMENTS_INCOMPLETE",
                lambda: invoke(supplied_runtime_authority=None),
            )

            runtime_drift = dict(runtime_authority)
            runtime_drift["batch_plan_sha256"] = "0" * 64
            expect_code(
                "SCOPED_RUNTIME_AUTHORITY_MISMATCH",
                lambda: invoke(supplied_runtime_authority=runtime_drift),
            )

            current["plan"] = copy.deepcopy(plan)
            current["plan"]["authority"]["source_metadata_sha256"] = "1" * 64
            expect_code(
                "SCOPED_RUNTIME_AUTHORITY_MISMATCH",
                invoke,
            )
            current["plan"] = plan

            current["contract"] = copy.deepcopy(contract)
            current["contract"]["authority"][
                "state_machine_schema_sha256"
            ] = "2" * 64
            expect_code(
                "SCOPED_RUNTIME_AUTHORITY_MISMATCH",
                invoke,
            )
            current["contract"] = contract

            requirements_drift = replace(requirements, selected_studies=6)
            try:
                invoke(supplied_requirements=requirements_drift)
            except core.OrchestrationError as exc:
                assert str(exc) == "BATCH_PLAN_COHORT_CONSTANT_MISMATCH"
            else:
                raise AssertionError("six-study requirements drift was accepted")
            production_requirements.assert_not_called()
            derive_runtime.assert_not_called()


def test_echoprime_execution_calls_shared_mean_pooling_helper() -> None:
    import numpy as np
    import pandas as pd
    import lvef_c3_orchestration_core as core
    import lvef_reconstruction_smoke as smoke
    import preserve_lvef_c3_production_batch as preservation

    class FakeBatch:
        def __init__(self, size: int):
            self.size = size

        def to(self, _device):
            return self

    class FakeModelOutput:
        def __init__(self, size: int):
            self.value = np.arange(size * 512, dtype=np.float32).reshape(size, 512)

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    class FakeModel:
        def __init__(self):
            self.head = [SimpleNamespace(in_features=4)]

        def load_state_dict(self, _state, *, strict: bool):
            assert strict is True

        def eval(self):
            return self

        def to(self, device):
            assert device == "cuda"
            return self

        def parameters(self):
            return []

        def __call__(self, batch: FakeBatch):
            return FakeModelOutput(batch.size)

    fake_torch = ModuleType("torch")
    fake_torch.__version__ = "synthetic-torch"
    fake_torch.version = SimpleNamespace(cuda="synthetic-cuda")
    fake_torch.backends = SimpleNamespace(
        cudnn=SimpleNamespace(version=lambda: 9000)
    )
    fake_torch.cuda = SimpleNamespace(is_available=lambda: True)
    fake_torch.device = lambda value: value
    fake_torch.nn = SimpleNamespace(
        Linear=lambda in_features, out_features: SimpleNamespace(
            in_features=in_features, out_features=out_features
        )
    )
    fake_torch.load = lambda *_args, **_kwargs: {}
    fake_torch.stack = lambda tensors, dim=0: FakeBatch(len(tensors))
    fake_torch.inference_mode = nullcontext

    fake_torchvision = ModuleType("torchvision")
    fake_torchvision.__version__ = "synthetic-torchvision"
    fake_torchvision.models = SimpleNamespace(
        video=SimpleNamespace(mvit_v2_s=lambda *, weights: FakeModel())
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        extraction_root = root / "extracted"
        extraction_root.mkdir()
        batch_output_root = root / "batch"
        batch_output_root.mkdir()
        extraction_manifest = root / "extraction.csv"
        selected_manifest = root / "selected.csv"
        extraction_rows = []
        selected_rows = []
        plan_studies = []
        for index in range(1, 6):
            subject_id = 800_000 + index
            study_id = 900_000 + index
            selected_rows.append(
                {"subject_id": subject_id, "study_id": study_id}
            )
            plan_studies.append(
                {"subject_id": subject_id, "study_id": study_id, "split": "train"}
            )
            extraction_rows.append(
                {
                    "subject_id": subject_id,
                    "study_id": study_id,
                    "clip_key": hashlib.sha256(f"clip-{index}".encode()).hexdigest(),
                    "physical_source_key": hashlib.sha256(
                        f"source-{index}".encode()
                    ).hexdigest(),
                    "write_ok": True,
                    "frames_shape": "32x224x224x3",
                    "frames_dtype": "uint8",
                    "mask_status": "APPLIED",
                    "temporal_sampling_policy": (
                        "historical_compatible_linspace_or_tail_repeat_v1"
                    ),
                    "pixel_decode_ok": True,
                    "npz_sha256": hashlib.sha256(f"npz-{index}".encode()).hexdigest(),
                }
            )
        pd.DataFrame(extraction_rows).to_csv(extraction_manifest, index=False)
        pd.DataFrame(selected_rows).to_csv(selected_manifest, index=False)
        environment = {
            "torch_version": fake_torch.__version__,
            "torchvision_version": fake_torchvision.__version__,
            "cuda_version": fake_torch.version.cuda,
            "cudnn_version": "9000",
        }
        plan = {
            "authority": {key: "a" * 64 for key in core.PLAN_AUTHORITY_KEYS},
            "batches": [
                {
                    "batch_id": "c3_batch_000",
                    "studies": plan_studies,
                }
            ]
        }
        requirements = core.PlanRequirements(
            release="mimic-iv-echo/1.0",
            selected_studies=5,
            selected_subjects=5,
            normalized_source_objects=5,
            selected_source_bytes=500,
            batch_count=1,
            studies_per_full_batch=5,
            final_batch_studies=5,
            contract_id="lvef_multitask_c3_exact_five_canary_v1",
        )
        runtime_authority = {
            **plan["authority"],
            "git_commit": "b" * 40,
            "batch_plan_sha256": "f" * 64,
            "checkpoint_sha256": "c" * 64,
            "environment_receipt_sha256": "e" * 64,
        }
        plan["authority"]["git_commit"] = "b" * 40
        plan["authority"]["checkpoint_sha256"] = "c" * 64
        plan["authority"]["environment_receipt_sha256"] = "e" * 64
        with mock.patch.dict(
            sys.modules,
            {"torch": fake_torch, "torchvision": fake_torchvision},
        ), mock.patch.object(
            stages, "validate_checkpoint_and_environment"
        ), mock.patch.object(
            stages,
            "require_projectnb_path",
            side_effect=lambda path, must_exist: Path(path),
        ), mock.patch.object(
            stages, "load_json_object", return_value=environment
        ), mock.patch.object(
            core, "load_orchestration_contract", return_value={}
        ), mock.patch.object(
            core, "load_strict_json", return_value=plan
        ), mock.patch.object(
            core, "validate_batch_plan", return_value="f" * 64
        ) as validate_plan, mock.patch.object(
            core, "validate_runtime_authority", return_value=runtime_authority
        ), mock.patch.object(
            stages,
            "sha256_file",
            side_effect=lambda path: (
                "c" * 64
                if Path(path).name == "synthetic-checkpoint"
                else "e" * 64
                if Path(path).name == "synthetic-environment"
                else hashlib.sha256(Path(path).read_bytes()).hexdigest()
            ),
        ), mock.patch.object(
            smoke, "configure_torch_determinism"
        ), mock.patch.object(
            smoke, "_load_extracted_frames", return_value=np.zeros((32, 1, 1, 3))
        ), mock.patch.object(
            smoke, "_prepare_encoder_input", return_value=object()
        ), mock.patch.object(
            preservation,
            "mean_pool_study_embeddings",
            wraps=preservation.mean_pool_study_embeddings,
        ) as shared_pool:
            summary = stages.run_production_echoprime(
                extraction_manifest=extraction_manifest,
                extraction_root=extraction_root,
                selected_batch_manifest=selected_manifest,
                checkpoint=root / "synthetic-checkpoint",
                environment_receipt=root / "synthetic-environment",
                orchestration_contract=root / "synthetic-contract",
                batch_plan=root / "synthetic-plan",
                batch_id="c3_batch_000",
                batch_output_root=batch_output_root,
                batch_size=2,
                seed=17,
                attempt_id="lvef_c3_canary_regression_001",
                runtime_authority=runtime_authority,
                requirements=requirements,
            )

        assert summary["status"] == "PASS_ECHOPRIME_AND_POOLING"
        assert summary["n_clip_embeddings"] == 5
        assert summary["n_pooled_studies"] == 5
        validate_plan.assert_called_once_with(plan, requirements=requirements)
        shared_pool.assert_called_once()
        assert set(shared_pool.call_args.kwargs) == {
            "clip_embeddings",
            "clip_rows",
            "study_rows",
        }
        assert shared_pool.call_args.kwargs["clip_embeddings"].shape == (5, 512)
        assert len(shared_pool.call_args.kwargs["clip_rows"]) == 5
        assert len(shared_pool.call_args.kwargs["study_rows"]) == 5
        with np.load(
            batch_output_root
            / "echoprime"
            / "study_embeddings.restricted.npz",
            allow_pickle=False,
        ) as archive:
            assert archive["embeddings"].shape == (5, 512)
            assert archive["embeddings"].dtype == np.float32


def test_stage_authorization_is_closed_and_stage_specific() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "authorization.json"
        payload = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
            "status": "AUTHORIZED",
            "authorization_scope": "EXTRACTION_AUTHORIZATION",
            "stage": "DICOM_EXTRACTION",
            "batch_id": "c3_batch_000",
            "attempt_id": "lvef_c3_attempt_001",
            "governing_commit": "a" * 40,
            "orchestration_contract_sha256": "b" * 64,
            "batch_plan_sha256": "c" * 64,
            "launch_authority_sha256": "d" * 64,
            "owner_authorized": True,
            "owner_authorization_date": "2026-08-10",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert stages.validate_stage_authorization(
            path,
            stage="DICOM_EXTRACTION",
            batch_id="c3_batch_000",
            attempt_id="lvef_c3_attempt_001",
            governing_commit="a" * 40,
            orchestration_contract_sha256="b" * 64,
            batch_plan_sha256="c" * 64,
            launch_authority_sha256="d" * 64,
        )["owner_authorized"]
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "STAGE_AUTHORIZATION_SCHEMA_MISMATCH",
            lambda: stages.validate_stage_authorization(
                path,
                stage="DICOM_EXTRACTION",
                batch_id="c3_batch_000",
                attempt_id="lvef_c3_attempt_001",
                governing_commit="a" * 40,
                orchestration_contract_sha256="b" * 64,
                batch_plan_sha256="c" * 64,
                launch_authority_sha256="d" * 64,
            ),
        )


def _batch_receipt(index: int) -> dict[str, object]:
    studies = 250 if index < 18 else 30
    objects_base, remainder = divmod(finalizer.EXPECTED_SOURCE_OBJECTS, 19)
    source_base, source_remainder = divmod(finalizer.EXPECTED_SOURCE_BYTES, 19)
    objects = objects_base + int(index < remainder)
    source_bytes = source_base + int(index < source_remainder)
    no_cine = 5 if index == 0 else 0
    multiframe = objects - 1
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_batch_finalization_receipt_v2",
        "status": "PASS_BATCH_FINALIZED",
        "batch_id": f"c3_batch_{index:03d}",
        "attempt_id": "lvef_c3_attempt_shared",
        "governing_commit": "a" * 40,
        "source_commit": "a" * 40,
        "run_timestamp_utc": "2026-08-10T12:00:00+00:00",
        "cohort_version": "mimic-iv-echo/1.0",
        "split_version": f"split_map_sha256:{'d' * 64}",
        "execution_contract_version": 2,
        "orchestration_contract_sha256": "b" * 64,
        "batch_plan_sha256": "c" * 64,
        "checkpoint_sha256": "6" * 64,
        "checkpoint_checksum": "6" * 64,
        "environment_receipt_sha256": "7" * 64,
        "python_version": "3.10.12",
        "pytorch_version": "2.11.0+cu130",
        "torchvision_version": "0.26.0+cu130",
        "cuda_version": "13.0",
        "cudnn_version": "91002",
        "package_inventory_sha256": "e" * 64,
        "production_stage_wrapper_sha256": "8" * 64,
        "batch_preservation_script_sha256": "8" * 64,
        "scheduler_runner_sha256": "9" * 64,
        "command_checksum": "0" * 64,
        "config_checksum": "1" * 64,
        "scheduler_job_identity": f"7100000.{index + 1}",
        "state_input_ledger_sha256": "0" * 64,
        "n_selected_studies": studies,
        "n_selected_subjects": studies,
        "n_expected_objects": objects,
        "expected_source_bytes": source_bytes,
        "n_download_verified": objects,
        "n_dicom_readable": objects,
        "n_dicom_unreadable": 0,
        "n_multiframe_cines": multiframe,
        "n_single_frame_objects": 1,
        "n_extracted_clips": multiframe,
        "n_unique_clip_keys": multiframe,
        "n_clip_embeddings": multiframe,
        "n_pooled_studies": studies - no_cine,
        "n_no_cine_studies": no_cine,
        "no_cine_disposition": (
            "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE" if no_cine else "NONE"
        ),
        "n_outside_selected_studies": 0,
        "n_missing_selected_studies": 0,
        "n_duplicate_physical_sources": 0,
        "n_duplicate_clip_keys": 0,
        "n_nonfinite_embeddings": 0,
        "n_wrong_dimension_embeddings": 0,
        "source_receipt_sha256": "d" * 64,
        "dicom_audit_sha256": "e" * 64,
        "extraction_manifest_sha256": "f" * 64,
        "clip_manifest_sha256": "1" * 64,
        "clip_embeddings_sha256": "2" * 64,
        "study_manifest_sha256": "3" * 64,
        "study_embeddings_sha256": "4" * 64,
        "preservation_manifest_sha256": "5" * 64,
        "cache_retirement_authorization_sha256": "a" * 64,
        "cache_tree_sha256": "b" * 64,
        "cache_atomically_staged_receipt_sha256": "c" * 64,
        "cache_retirement_script_sha256": "d" * 64,
        "source_gate_passed": True,
        "download_gate_passed": True,
        "dicom_audit_gate_passed": True,
        "extraction_gate_passed": True,
        "embedding_gate_passed": True,
        "pooling_gate_passed": True,
        "study_pooling_semantics_gate_passed": True,
        "preservation_gate_passed": True,
        "aggregate_safety_gate_passed": True,
        "aggregate_safety_gate_result": "PASS",
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
    }


def _canary_eligibility_receipt() -> dict[str, object]:
    receipt = _batch_receipt(1)
    for key in finalizer.RETIREMENT_RECEIPT_KEYS:
        receipt.pop(key)
    receipt.update(
        {
            "artifact_type": "lvef_c3_batch_preservation_eligibility_receipt_v2",
            "status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
            "batch_id": "canary_batch_000",
            "attempt_id": "lvef_c3_canary_synthetic",
            "batch_plan_sha256": "c" * 64,
            "n_selected_studies": 5,
            "n_selected_subjects": 5,
            "n_expected_objects": 6,
            "expected_source_bytes": 60_000,
            "n_download_verified": 6,
            "n_dicom_readable": 6,
            "n_dicom_unreadable": 0,
            "n_multiframe_cines": 5,
            "n_single_frame_objects": 1,
            "n_extracted_clips": 5,
            "n_unique_clip_keys": 5,
            "n_clip_embeddings": 5,
            "n_pooled_studies": 5,
            "n_no_cine_studies": 0,
            "no_cine_disposition": "NONE",
            "extracted_cache_retired": False,
        }
    )
    return receipt


def _write_receipts(root: Path) -> list[Path]:
    paths = []
    for index in range(19):
        path = root / f"receipt_{index:03d}.json"
        path.write_text(json.dumps(_batch_receipt(index)), encoding="utf-8")
        paths.append(path)
    return paths


def test_production_finalizer_reconciles_exact_cohort_and_five_no_cine() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary = finalizer.finalize_receipts(
            _write_receipts(Path(directory)), expected_governing_commit="a" * 40
        )
        assert set(summary) == finalizer.FINAL_KEYS
        assert summary["selected_studies"] == 4530
        assert summary["verified_source_objects"] == 335984
        assert summary["selected_source_bytes"] == 1216569133322
        assert summary["pooled_imaging_eligible_studies"] == 4525
        assert summary["no_cine_studies"] == 5
        assert summary["raw_dicoms_retained"] is True
        assert summary["extracted_cache_retired"] is True
        assert summary["model_fitting_count"] == 0
        assert summary["endpoint_prediction_count"] == 0
        assert summary["confirmatory_performance_access_count"] == 0
        policy, _ = analysis_modes.load_policy(
            ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
        )
        result = analysis_modes.validate_candidate_bytes(
            (json.dumps(summary, sort_keys=True) + "\n").encode("utf-8"),
            filename="lvef_c3_production_finalization.summary.json",
            profile_name="lvef_c3_production_finalization_summary_json",
            policy=policy,
        )
        assert result["status"] == "PASS"

        changed = {**summary, "endpoint_prediction_count": 1}
        try:
            finalizer.validate_closed_final_summary(changed)
        except finalizer.ProductionFinalizationError as exc:
            assert exc.code == "FINAL_SUMMARY_SCIENTIFIC_SCOPE_INVALID"
        else:
            raise AssertionError("nonzero prediction count was accepted")


def test_production_finalizer_fails_on_missing_batch_or_scientific_inconsistency() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths = _write_receipts(Path(directory))
        expect_code(
            "FINAL_BATCH_SET_INCOMPLETE",
            lambda: finalizer.finalize_receipts(
                paths[:-1], expected_governing_commit="a" * 40
            ),
        )
        payload = json.loads(paths[0].read_text())
        payload["n_duplicate_clip_keys"] = 1
        paths[0].write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "SCIENTIFIC_INCONSISTENCY",
            lambda: finalizer.finalize_receipts(
                paths, expected_governing_commit="a" * 40
            ),
        )


def _write_embedding_replay_fixture(root: Path) -> tuple[dict[str, Path], dict[str, object]]:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    root.mkdir(parents=True, exist_ok=True)
    planned: dict[str, object] = {
        "batch_id": "c3_batch_000",
        "n_studies": 3,
        "studies": [
            {"subject_id": "101", "study_id": "1001", "split": "train"},
            {"subject_id": "102", "study_id": "1002", "split": "train"},
            {"subject_id": "103", "study_id": "1003", "split": "train"},
        ],
    }
    clip_array = np.stack(
        [
            np.linspace(0.0, 1.0, 512, dtype=np.float32),
            np.linspace(1.0, 2.0, 512, dtype=np.float32),
            np.linspace(2.0, 3.0, 512, dtype=np.float32),
        ]
    )
    clip_rows: list[dict[str, object]] = []
    for index, (subject, study) in enumerate(
        (("101", "1001"), ("101", "1001"), ("102", "1002"))
    ):
        vector = clip_array[index]
        clip_rows.append(
            {
                "embedding_idx": index,
                "subject_id": subject,
                "study_id": study,
                "clip_key": hashlib.sha256(f"clip-{index}".encode()).hexdigest(),
                "physical_source_key": hashlib.sha256(
                    f"source-{index}".encode()
                ).hexdigest(),
                "embedding_l2_norm": float(
                    np.linalg.norm(vector.astype(np.float64))
                ),
                "embedding_sha256": smoke.array_content_sha256(vector),
                "write_ok": True,
            }
        )
    study_rows: list[dict[str, object]] = [
        {"study_idx": 0, "subject_id": "101", "study_id": "1001", "n_clips": 2},
        {"study_idx": 1, "subject_id": "102", "study_id": "1002", "n_clips": 1},
    ]
    study_array = finalizer.preservation.mean_pool_study_embeddings(
        clip_embeddings=clip_array,
        clip_rows=clip_rows,
        study_rows=study_rows,
    )
    for row, vector in zip(study_rows, study_array, strict=True):
        row["embedding_sha256"] = smoke.array_content_sha256(vector)
    dispositions = [
        {"subject_id": "101", "study_id": "1001", "disposition": "IMAGING_ELIGIBLE"},
        {"subject_id": "102", "study_id": "1002", "disposition": "IMAGING_ELIGIBLE"},
        {
            "subject_id": "103",
            "study_id": "1003",
            "disposition": "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE",
        },
    ]
    paths = {
        "clip_manifest": root / "clip_manifest.restricted.csv",
        "clip_embeddings": root / "clip_embeddings.restricted.npz",
        "study_manifest": root / "study_manifest.restricted.csv",
        "study_embeddings": root / "study_embeddings.restricted.npz",
        "disposition": root / "study_disposition.restricted.csv",
    }

    def write_rows(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    write_rows(paths["clip_manifest"], finalizer.CLIP_MANIFEST_HEADER, clip_rows)
    write_rows(paths["study_manifest"], finalizer.STUDY_MANIFEST_HEADER, study_rows)
    write_rows(paths["disposition"], finalizer.DISPOSITION_HEADER, dispositions)
    np.savez_compressed(paths["clip_embeddings"], embeddings=clip_array)
    np.savez_compressed(paths["study_embeddings"], embeddings=study_array)
    return paths, planned


def _replay_fixture(paths: dict[str, Path], planned: dict[str, object]):
    return finalizer.replay_batch_study_embeddings(
        clip_manifest_path=paths["clip_manifest"],
        clip_embeddings_path=paths["clip_embeddings"],
        study_manifest_path=paths["study_manifest"],
        study_embeddings_path=paths["study_embeddings"],
        disposition_path=paths["disposition"],
        planned_batch=planned,
        expected_clip_embeddings=3,
        expected_study_embeddings=2,
        expected_no_cine_studies=1,
    )


def test_finalizer_independently_replays_exact_batch_pooling_and_dispositions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths, planned = _write_embedding_replay_fixture(Path(directory))
        with mock.patch.object(
            finalizer.preservation,
            "mean_pool_study_embeddings",
            wraps=finalizer.preservation.mean_pool_study_embeddings,
        ) as replay:
            records = _replay_fixture(paths, planned)
        assert replay.call_count == 1
        assert [(row["subject_id"], row["study_id"]) for row in records] == [
            ("101", "1001"),
            ("102", "1002"),
        ]
        assert all(row["embedding"].dtype.name == "float32" for row in records)


def test_finalizer_rejects_pooling_mutation_and_no_cine_mismatch() -> None:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    with tempfile.TemporaryDirectory() as directory:
        paths, planned = _write_embedding_replay_fixture(Path(directory))
        with np.load(paths["study_embeddings"], allow_pickle=False) as archive:
            mutated = archive["embeddings"].copy()
        mutated[0, 0] = np.nextafter(mutated[0, 0], np.float32("inf"))
        np.savez_compressed(paths["study_embeddings"], embeddings=mutated)
        with paths["study_manifest"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["embedding_sha256"] = smoke.array_content_sha256(mutated[0])
        with paths["study_manifest"].open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=finalizer.STUDY_MANIFEST_HEADER, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        expect_code(
            "FINALIZER_STUDY_POOLING_RECOMPUTATION_MISMATCH",
            lambda: _replay_fixture(paths, planned),
        )

        paths, planned = _write_embedding_replay_fixture(Path(directory) / "disposition")
        with paths["disposition"].open(newline="", encoding="utf-8") as handle:
            dispositions = list(csv.DictReader(handle))
        dispositions[-1]["disposition"] = "IMAGING_ELIGIBLE"
        with paths["disposition"].open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=finalizer.DISPOSITION_HEADER, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(dispositions)
        expect_code(
            "FINALIZER_NO_CINE_DISPOSITION_MISMATCH",
            lambda: _replay_fixture(paths, planned),
        )


def test_finalizer_writes_plan_ordered_canonical_store_no_clobber() -> None:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        embeddings = np.stack(
            [np.full(512, 2.0, dtype=np.float32), np.full(512, 1.0, dtype=np.float32)]
        )
        with mock.patch.object(finalizer, "EXPECTED_IMAGING_ELIGIBLE_STUDIES", 2):
            records, embeddings = finalizer.build_plan_ordered_canonical_study_store(
                plan={
                    "batches": [
                        {
                            "batch_id": "c3_batch_000",
                            "studies": [{"subject_id": "20", "study_id": "200"}],
                        },
                        {
                            "batch_id": "c3_batch_001",
                            "studies": [{"subject_id": "10", "study_id": "100"}],
                        },
                    ]
                },
                studies_by_id={
                    "100": {
                        "subject_id": "10", "batch_id": "c3_batch_001",
                        "n_clips": 1,
                        "embedding_sha256": smoke.array_content_sha256(embeddings[1]),
                        "embedding": embeddings[1],
                    },
                    "200": {
                        "subject_id": "20", "batch_id": "c3_batch_000",
                        "n_clips": 1,
                        "embedding_sha256": smoke.array_content_sha256(embeddings[0]),
                        "embedding": embeddings[0],
                    },
                },
            )
            assert [row["study_id"] for row in records] == ["200", "100"]
            receipt = finalizer.write_canonical_study_store(
                output_root=root,
                records=records,
                embeddings=embeddings,
                governing_commit="a" * 40,
                attempt_id="lvef_c3_attempt_shared",
                batch_plan_sha256="b" * 64,
                batch_receipt_set_sha256="c" * 64,
                no_cine_studies=1,
            )
            assert receipt["status"] == "PASS_CANONICAL_STUDY_EMBEDDING_STORE"
            with np.load(
                root / finalizer.CANONICAL_STUDY_EMBEDDINGS_NAME,
                allow_pickle=False,
            ) as archive:
                assert np.array_equal(archive["embeddings"], embeddings)
            with (root / finalizer.CANONICAL_STUDY_MANIFEST_NAME).open(
                newline="", encoding="utf-8"
            ) as handle:
                manifest = list(csv.DictReader(handle))
            assert [row["study_id"] for row in manifest] == ["200", "100"]
            expect_code(
                "CANONICAL_STUDY_OUTPUT_ALREADY_EXISTS",
                lambda: finalizer.write_canonical_study_store(
                    output_root=root,
                    records=records,
                    embeddings=embeddings,
                    governing_commit="a" * 40,
                    attempt_id="lvef_c3_attempt_shared",
                    batch_plan_sha256="b" * 64,
                    batch_receipt_set_sha256="c" * 64,
                    no_cine_studies=1,
                ),
            )


def test_finalizer_rejects_duplicate_canonical_study_keys() -> None:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        embeddings = np.stack(
            [np.zeros(512, dtype=np.float32), np.ones(512, dtype=np.float32)]
        )
        records = [
            {
                "study_idx": index,
                "subject_id": str(10 + index),
                "study_id": "100",
                "batch_id": f"c3_batch_{index:03d}",
                "n_clips": 1,
                "embedding_sha256": smoke.array_content_sha256(embeddings[index]),
            }
            for index in range(2)
        ]
        with mock.patch.object(finalizer, "EXPECTED_IMAGING_ELIGIBLE_STUDIES", 2):
            expect_code(
                "CANONICAL_STUDY_MANIFEST_AUTHORITY_INVALID",
                lambda: finalizer.write_canonical_study_store(
                    output_root=root,
                    records=records,
                    embeddings=embeddings,
                    governing_commit="a" * 40,
                    attempt_id="lvef_c3_attempt_shared",
                    batch_plan_sha256="b" * 64,
                    batch_receipt_set_sha256="c" * 64,
                    no_cine_studies=1,
                ),
            )
        assert list(root.iterdir()) == []


def _cohort_preservation_fixture(root: Path) -> dict[str, object]:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    production_root = root / "production"
    attempt_id = "lvef_c3_full_synthetic_001"
    output_root = (
        production_root / "attempts" / attempt_id / "cohort_finalization"
    )
    output_root.mkdir(parents=True, mode=0o700)
    production_root.chmod(0o700)
    output_root.chmod(0o700)
    governing_commit = "a" * 40
    plan_sha = "b" * 64
    vectors = [
        np.full(512, 1.0, dtype=np.float32),
        np.full(512, 2.0, dtype=np.float32),
    ]
    plan_batches: list[dict[str, object]] = []
    records_by_batch: dict[str, list[dict[str, object]]] = {}
    receipt_paths: list[Path] = []
    store_paths: list[Path] = []
    for index, vector in enumerate(vectors):
        batch_id = f"c3_batch_{index:03d}"
        subject_id = str(101 + index)
        study_id = str(1001 + index)
        source_key = hashlib.sha256(f"source-{index}".encode()).hexdigest()
        batch_root = (
            production_root / "attempts" / attempt_id / "batches" / batch_id
        )
        store_path = batch_root / "echoprime" / "clip_embeddings.restricted.npz"
        store_path.parent.mkdir(parents=True)
        np.savez_compressed(store_path, embeddings=np.stack([vector]))
        store_path.chmod(0o600)
        store_sha = finalizer.sha256_file(store_path)
        manifest_sha = hashlib.sha256(f"manifest-{index}".encode()).hexdigest()
        receipt = _batch_receipt(index)
        receipt.update(
            {
                "batch_id": batch_id,
                "attempt_id": attempt_id,
                "governing_commit": governing_commit,
                "source_commit": governing_commit,
                "batch_plan_sha256": plan_sha,
                "n_selected_studies": 1,
                "n_selected_subjects": 1,
                "n_expected_objects": 1,
                "expected_source_bytes": 100 + index,
                "n_download_verified": 1,
                "n_dicom_readable": 1,
                "n_dicom_unreadable": 0,
                "n_multiframe_cines": 1,
                "n_single_frame_objects": 0,
                "n_extracted_clips": 1,
                "n_unique_clip_keys": 1,
                "n_clip_embeddings": 1,
                "n_pooled_studies": 1,
                "n_no_cine_studies": 0,
                "no_cine_disposition": "NONE",
                "clip_manifest_sha256": manifest_sha,
                "clip_embeddings_sha256": store_sha,
            }
        )
        receipt_path = (
            batch_root
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        receipt_path.chmod(0o600)
        receipt_paths.append(receipt_path)
        store_paths.append(store_path)
        plan_batches.append(
            {
                "batch_id": batch_id,
                "objects": [
                    {
                        "subject_id": subject_id,
                        "study_id": study_id,
                        "source_object_key": source_key,
                    }
                ],
            }
        )
        records_by_batch[batch_id] = [
            {
                "batch_id": batch_id,
                "batch_embedding_idx": 0,
                "subject_id": subject_id,
                "study_id": study_id,
                "clip_key": hashlib.sha256(f"clip-{index}".encode()).hexdigest(),
                "physical_source_key": source_key,
                "embedding_sha256": smoke.array_content_sha256(vector),
                "batch_clip_manifest_sha256": manifest_sha,
                "batch_clip_embeddings_sha256": store_sha,
            }
        ]
    receipt_set_sha = hashlib.sha256(
        "\n".join(sorted(finalizer.sha256_file(path) for path in receipt_paths)).encode(
            "ascii"
        )
        + b"\n"
    ).hexdigest()
    study_records = [
        {
            "study_idx": index,
            "subject_id": str(101 + index),
            "study_id": str(1001 + index),
            "batch_id": f"c3_batch_{index:03d}",
            "n_clips": 1,
            "embedding_sha256": smoke.array_content_sha256(vector),
        }
        for index, vector in enumerate(vectors)
    ]
    finalizer.write_canonical_study_store(
        output_root=output_root,
        records=study_records,
        embeddings=np.stack(vectors),
        governing_commit=governing_commit,
        attempt_id=attempt_id,
        batch_plan_sha256=plan_sha,
        batch_receipt_set_sha256=receipt_set_sha,
        no_cine_studies=0,
        expected_study_count=2,
    )
    artifacts: list[dict[str, object]] = []
    for role, paths in (
        ("batch_final_receipt", receipt_paths),
        ("batch_clip_embeddings", store_paths),
        (
            "canonical_study_embeddings",
            [output_root / finalizer.CANONICAL_STUDY_EMBEDDINGS_NAME],
        ),
        (
            "canonical_study_manifest",
            [output_root / finalizer.CANONICAL_STUDY_MANIFEST_NAME],
        ),
        (
            "canonical_study_store",
            [output_root / finalizer.CANONICAL_STUDY_RECEIPT_NAME],
        ),
    ):
        for path in paths:
            artifacts.append(
                {
                    "role": role,
                    "relative_path": path.relative_to(production_root).as_posix(),
                    "size_bytes": path.stat(follow_symlinks=False).st_size,
                    "sha256": finalizer.sha256_file(path),
                }
            )
    plan = {"batches": plan_batches}
    clip_rows = finalizer.build_plan_ordered_canonical_clip_index(
        plan=plan,
        records_by_batch={
            "c3_batch_001": records_by_batch["c3_batch_001"],
            "c3_batch_000": records_by_batch["c3_batch_000"],
        },
        expected_clip_count=2,
    )
    return {
        "production_root": production_root,
        "output_root": output_root,
        "attempt_id": attempt_id,
        "governing_commit": governing_commit,
        "plan_sha": plan_sha,
        "receipt_set_sha": receipt_set_sha,
        "plan": plan,
        "records_by_batch": records_by_batch,
        "clip_rows": clip_rows,
        "artifacts": artifacts,
        "receipt_paths": receipt_paths,
        "store_paths": store_paths,
    }


def _write_cohort_fixture(fixture: Mapping[str, object]) -> Mapping[str, object]:
    return finalizer.write_cohort_preservation_outputs(
        output_root=fixture["output_root"],
        artifact_root=fixture["production_root"],
        clip_index_rows=fixture["clip_rows"],
        artifacts=fixture["artifacts"],
        governing_commit=fixture["governing_commit"],
        attempt_id=fixture["attempt_id"],
        batch_plan_sha256=fixture["plan_sha"],
        batch_receipt_set_sha256=fixture["receipt_set_sha"],
        production_batches=2,
        study_embeddings=fixture.get("study_embeddings", 2),
        no_cine_studies=fixture.get("no_cine_studies", 0),
    )


def test_cohort_clip_index_is_plan_ordered_and_binds_exact_batch_stores() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        assert [row["batch_id"] for row in fixture["clip_rows"]] == [
            "c3_batch_000",
            "c3_batch_001",
        ]
        receipt = _write_cohort_fixture(fixture)
        assert receipt["status"] == "PASS_COHORT_PRESERVATION"
        assert len(receipt["artifacts"]) == 8
        clip_path = fixture["output_root"] / finalizer.CANONICAL_CLIP_INDEX_NAME
        with clip_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert [int(row["clip_idx"]) for row in rows] == [0, 1]
        assert [int(row["batch_embedding_idx"]) for row in rows] == [0, 0]
        assert [row["batch_id"] for row in rows] == [
            "c3_batch_000",
            "c3_batch_001",
        ]
        store_hashes = {
            finalizer.sha256_file(path) for path in fixture["store_paths"]
        }
        assert {row["batch_clip_embeddings_sha256"] for row in rows} == store_hashes
        receipt_path = (
            fixture["output_root"] / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        )
        assert finalizer.replay_cohort_preservation_receipt(
            receipt_path, artifact_root=fixture["production_root"]
        ) == receipt


def test_cohort_clip_index_rejects_duplicate_clip_or_physical_source() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        records = copy.deepcopy(fixture["records_by_batch"])
        records["c3_batch_001"][0]["clip_key"] = records["c3_batch_000"][0][
            "clip_key"
        ]
        expect_code(
            "GLOBAL_CLIP_KEY_COLLISION",
            lambda: finalizer.build_plan_ordered_canonical_clip_index(
                plan=fixture["plan"],
                records_by_batch=records,
                expected_clip_count=2,
            ),
        )
        records = copy.deepcopy(fixture["records_by_batch"])
        duplicate_source = records["c3_batch_000"][0]["physical_source_key"]
        records["c3_batch_001"][0]["physical_source_key"] = duplicate_source
        fixture["plan"]["batches"][1]["objects"][0][
            "source_object_key"
        ] = duplicate_source
        expect_code(
            "GLOBAL_PHYSICAL_SOURCE_COLLISION",
            lambda: finalizer.build_plan_ordered_canonical_clip_index(
                plan=fixture["plan"],
                records_by_batch=records,
                expected_clip_count=2,
            ),
        )


def test_cohort_preservation_requires_exact_inventory_and_receipt_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        incomplete = {**fixture, "artifacts": fixture["artifacts"][:-1]}
        expect_code("COHORT_ARTIFACT_SET_MISMATCH", lambda: _write_cohort_fixture(incomplete))
        assert not (
            fixture["output_root"] / finalizer.CANONICAL_CLIP_INDEX_NAME
        ).exists()

    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        wrong_set = {**fixture, "receipt_set_sha": "f" * 64}
        expect_code("COHORT_BATCH_RECEIPT_SET_MISMATCH", lambda: _write_cohort_fixture(wrong_set))
        assert not (
            fixture["output_root"] / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        ).exists()

    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        wrong_count = {**fixture, "study_embeddings": 1}
        expect_code(
            "COHORT_BATCH_COUNT_BINDING_MISMATCH",
            lambda: _write_cohort_fixture(wrong_count),
        )


def test_cohort_preservation_rejects_vector_mutation_before_terminal_receipt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        fixture["clip_rows"][0]["embedding_sha256"] = "f" * 64
        expect_code("COHORT_CLIP_VECTOR_BINDING_MISMATCH", lambda: _write_cohort_fixture(fixture))
        assert not (
            fixture["output_root"] / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        ).exists()


def test_cohort_preservation_rejects_store_and_index_binding_mutations() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        fixture["clip_rows"][0]["batch_clip_embeddings_sha256"] = "f" * 64
        expect_code("COHORT_CLIP_STORE_BINDING_MISMATCH", lambda: _write_cohort_fixture(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        fixture["clip_rows"][1]["batch_embedding_idx"] = 1
        expect_code(
            "CANONICAL_CLIP_INDEX_AUTHORITY_INVALID",
            lambda: _write_cohort_fixture(fixture),
        )


def test_cohort_preservation_receipt_is_published_only_after_replay() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        receipt_path = (
            fixture["output_root"] / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        )

        def reject_replay(*_args, **_kwargs):
            assert not receipt_path.exists()
            raise finalizer.ProductionFinalizationError("SYNTHETIC_REPLAY_FAILURE")

        with mock.patch.object(
            finalizer, "_replay_cohort_artifact_inventory", reject_replay
        ):
            expect_code("SYNTHETIC_REPLAY_FAILURE", lambda: _write_cohort_fixture(fixture))
        assert not receipt_path.exists()


def test_cohort_preservation_second_pass_and_no_clobber_are_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        _write_cohort_fixture(fixture)
        output_root = fixture["output_root"]
        clip_path = output_root / finalizer.CANONICAL_CLIP_INDEX_NAME
        receipt_path = output_root / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        original_clip = clip_path.read_bytes()
        original_receipt = receipt_path.read_bytes()
        expect_code("COHORT_PRESERVATION_OUTPUT_EXISTS", lambda: _write_cohort_fixture(fixture))
        assert clip_path.read_bytes() == original_clip
        assert receipt_path.read_bytes() == original_receipt

        manifest = output_root / finalizer.CANONICAL_STUDY_MANIFEST_NAME
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        expect_code(
            "COHORT_ARTIFACT_SECOND_PASS_MISMATCH",
            lambda: finalizer.replay_cohort_preservation_receipt(
                receipt_path, artifact_root=fixture["production_root"]
            ),
        )


def test_cohort_preservation_rejects_symlinked_inventory_member() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _cohort_preservation_fixture(Path(directory))
        store = fixture["store_paths"][0]
        outside = Path(directory) / "outside.npz"
        outside.write_bytes(store.read_bytes())
        store.unlink()
        store.symlink_to(outside)
        expect_code("COHORT_ARTIFACT_NOT_REGULAR", lambda: _write_cohort_fixture(fixture))
        assert outside.exists()
        assert not (
            fixture["output_root"] / finalizer.COHORT_PRESERVATION_RECEIPT_NAME
        ).exists()


def test_canary_finalizer_binds_exact_five_retained_cache_receipt() -> None:
    receipt = _canary_eligibility_receipt()
    summary = finalizer.finalize_canary_preservation_receipt(
        receipt,
        expected_governing_commit="a" * 40,
        expected_attempt_id="lvef_c3_canary_synthetic",
        expected_canary_manifest_sha256="a" * 64,
        expected_batch_plan_sha256="c" * 64,
        expected_scheduler_plan_sha256="b" * 64,
        expected_object_count=6,
        expected_source_bytes=60_000,
    )
    assert set(summary) == finalizer.CANARY_FINAL_KEYS
    assert summary["successful_train_studies"] == 5
    assert summary["failed_studies"] == 0
    assert summary["no_cine_studies"] == 0
    assert summary["extracted_cache_retained"] is True
    assert summary["production_continuation_authorized"] is False
    assert summary["preservation_receipt_sha256"] == (
        finalizer.core.canonical_json_sha256(receipt)
    )
    assert summary["authority_binding_sha256"] == finalizer.core.canonical_json_sha256(
        {
            "preservation_receipt_sha256": summary["preservation_receipt_sha256"],
            "canary_manifest_sha256": "a" * 64,
            "batch_plan_sha256": "c" * 64,
            "scheduler_plan_sha256": "b" * 64,
        }
    )
    serialized = json.dumps(summary, sort_keys=True)
    for prohibited in (
        "study_id", "subject_id", "source_relative_path", "object_locator",
        "label", "performance",
    ):
        assert prohibited not in serialized


def test_canary_finalizer_fails_closed_on_scope_failure_or_binding_drift() -> None:
    def finalize(receipt: dict[str, object], **overrides) -> dict[str, object]:
        arguments = {
            "expected_governing_commit": "a" * 40,
            "expected_attempt_id": "lvef_c3_canary_synthetic",
            "expected_canary_manifest_sha256": "a" * 64,
            "expected_batch_plan_sha256": "c" * 64,
            "expected_scheduler_plan_sha256": "b" * 64,
            "expected_object_count": 6,
            "expected_source_bytes": 60_000,
        }
        arguments.update(overrides)
        return finalizer.finalize_canary_preservation_receipt(receipt, **arguments)

    retired = _canary_eligibility_receipt()
    retired["extracted_cache_retired"] = True
    expect_code("CANARY_CACHE_NOT_RETAINED", lambda: finalize(retired))

    incomplete = _canary_eligibility_receipt()
    incomplete["n_pooled_studies"] = 4
    incomplete["n_no_cine_studies"] = 1
    incomplete["no_cine_disposition"] = "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE"
    expect_code(
        "CANARY_EXACT_FIVE_SUCCESSFUL_STUDIES_REQUIRED",
        lambda: finalize(incomplete),
    )

    failed = _canary_eligibility_receipt()
    failed["n_dicom_readable"] = 5
    failed["n_dicom_unreadable"] = 1
    expect_code("CANARY_DICOM_FAILURE", lambda: finalize(failed))

    expect_code(
        "CANARY_ATTEMPT_MISMATCH",
        lambda: finalize(
            _canary_eligibility_receipt(), expected_attempt_id="synthetic-canary-two"
        ),
    )
    expect_code(
        "CANARY_BATCH_PLAN_BINDING_MISMATCH",
        lambda: finalize(
            _canary_eligibility_receipt(), expected_batch_plan_sha256="d" * 64
        ),
    )

    over_object_ceiling = _canary_eligibility_receipt()
    over_object_ceiling["n_expected_objects"] = 751
    over_object_ceiling["n_download_verified"] = 751
    over_object_ceiling["n_dicom_readable"] = 751
    over_object_ceiling["n_multiframe_cines"] = 751
    over_object_ceiling["n_extracted_clips"] = 751
    over_object_ceiling["n_unique_clip_keys"] = 751
    over_object_ceiling["n_clip_embeddings"] = 751
    expect_code(
        "EXPECTED_CANARY_SOURCE_SCOPE_INVALID",
        lambda: finalize(over_object_ceiling, expected_object_count=751),
    )
    over_byte_ceiling = _canary_eligibility_receipt()
    over_byte_ceiling["expected_source_bytes"] = 5_000_000_001
    expect_code(
        "EXPECTED_CANARY_SOURCE_SCOPE_INVALID",
        lambda: finalize(over_byte_ceiling, expected_source_bytes=5_000_000_001),
    )

    expanded = _canary_eligibility_receipt()
    expanded["unexpected"] = True
    expect_code("CANARY_RECEIPT_SCHEMA_MISMATCH", lambda: finalize(expanded))


def test_finalizer_rejects_cross_batch_clip_key_collision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        global_clips: set[str] = set()
        global_sources: set[str] = set()

        def write_manifest(name: str, clip: str, source: str, subject: str, study: str) -> Path:
            path = root / name
            path.write_text(
                ",".join(finalizer.CLIP_MANIFEST_HEADER)
                + "\n"
                + f"0,{subject},{study},{clip},{source},1.0,{'e' * 64},True\n",
                encoding="utf-8",
            )
            return path

        first_source = "1" * 64
        second_source = "2" * 64
        clip = "a" * 64
        finalizer.accumulate_global_clip_authority(
            write_manifest("first.csv", clip, first_source, "1", "10"),
            planned_batch={
                "batch_id": "c3_batch_000",
                "objects": [{"source_object_key": first_source, "subject_id": "1", "study_id": "10"}]
            },
            expected_rows=1,
            global_clip_keys=global_clips,
            global_physical_source_keys=global_sources,
        )
        expect_code(
            "GLOBAL_CLIP_KEY_COLLISION",
            lambda: finalizer.accumulate_global_clip_authority(
                write_manifest("second.csv", clip, second_source, "2", "20"),
                planned_batch={
                    "batch_id": "c3_batch_001",
                    "objects": [{"source_object_key": second_source, "subject_id": "2", "study_id": "20"}]
                },
                expected_rows=1,
                global_clip_keys=global_clips,
                global_physical_source_keys=global_sources,
            ),
        )


def test_finalizer_replays_retained_raw_and_allows_only_retired_npz_cache() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        attempt = "lvef_c3_synthetic_001"
        batch = "c3_batch_000"
        prefix = Path("attempts") / attempt
        raw = root / prefix / "raw" / batch / "objects" / "source.dcm"
        metadata = (
            root / prefix / "extracted_cache" / batch / "dicom_extraction"
            / "dicom_audit.restricted.csv"
        )
        embedding = root / prefix / "batches" / batch / "echoprime" / "clip_manifest.restricted.csv"
        ledger = root / prefix / "batches" / batch / "download_resume_ledger.restricted.json"
        for path, body in (
            (raw, b"raw"), (metadata, b"metadata"),
            (embedding, b"embedding"), (ledger, b"ledger"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        retired_relative = (
            prefix / "extracted_cache" / batch / "dicom_extraction" / "clips" / "clip.npz"
        ).as_posix()
        retired_body = b"retired-npz"
        records = [
            (raw, "raw_dicom_and_download_authority"),
            (metadata, "dicom_extraction_metadata_retained"),
            (embedding, "embedding_and_pooling_retained"),
            (ledger, "download_ledger"),
        ]
        rows = ["\t".join(finalizer.PRESERVATION_MANIFEST_HEADER)]
        for path, role in records:
            rows.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t"
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}\t{role}"
            )
        retired_sha = hashlib.sha256(retired_body).hexdigest()
        rows.append(
            f"{retired_relative}\t{len(retired_body)}\t{retired_sha}\t"
            "extracted_npz_cache_owner_retirable"
        )
        manifest = root / "preservation.tsv"
        manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
        cache_tree_sha = hashlib.sha256(
            f"clip.npz\t{len(retired_body)}\t{retired_sha}\n".encode("utf-8")
        ).hexdigest()
        finalizer.replay_batch_preservation_manifest(
            manifest,
            production_root=root,
            attempt_id=attempt,
            batch_id=batch,
            expected_retired_cache_tree_sha256=cache_tree_sha,
        )
        unlisted = raw.parent / "unlisted.dcm"
        unlisted.write_bytes(b"unlisted")
        expect_code(
            "UNLISTED_OR_MISSING_RETAINED_ARTIFACT",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )
        unlisted.unlink()
        raw.unlink()
        expect_code(
            "RETAINED_ARTIFACT_MISSING",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )
        raw.write_bytes(b"bad")
        expect_code(
            "RETAINED_ARTIFACT_HASH_MISMATCH",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )


def test_finalizer_rejects_duplicate_json_keys_symlink_and_output_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        duplicate = root / "duplicate.json"
        duplicate.write_text('{"status":"PASS","status":"FAIL"}\n', encoding="utf-8")
        expect_code(
            "DUPLICATE_JSON_KEY",
            lambda: finalizer.load_json(duplicate, "BATCH_RECEIPT"),
        )
        target = root / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        link = root / "link.json"
        link.symlink_to(target)
        expect_code(
            "BATCH_RECEIPT_NOT_REGULAR",
            lambda: finalizer.load_json(link, "BATCH_RECEIPT"),
        )
        expect_code(
            "FINAL_OUTPUT_ALREADY_EXISTS",
            lambda: finalizer.write_json_atomic(target, {"status": "PASS"}),
        )


def test_dispatch_authorization_is_stage_specific_hash_bound_and_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=not-exported\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="FIRST_BATCH_DOWNLOAD",
            task_scope="1",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="FIRST_BATCH_DOWNLOAD",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
        )
        expect_code(
            "DISPATCH_RECEIPT_AUTHORITY_MISMATCH",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="REMAINING_BATCH_DOWNLOAD",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
                authorized_array_range="2",
            ),
        )


def test_dispatch_authorization_requires_exact_single_rolling_batch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=literal\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="DICOM_EXTRACTION",
            task_scope="7",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="DICOM_EXTRACTION",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
            authorized_array_range="7",
        )
        expect_code(
            "DISPATCH_ARRAY_SCOPE_INVALID",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="DICOM_EXTRACTION",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
                authorized_array_range="1-19",
            ),
        )


def test_remaining_download_authorization_is_exactly_one_batch_2_to_19() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=literal\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="REMAINING_BATCH_DOWNLOAD",
            task_scope="2",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="REMAINING_BATCH_DOWNLOAD",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
            authorized_array_range="2",
        )
        for invalid in ("1", "2-19", "20"):
            expect_code(
                "DISPATCH_ARRAY_SCOPE_INVALID",
                lambda invalid=invalid: _validate_dispatch_with_stub(
                    receipt,
                    launch_path=launch_path,
                    launch_value=launch_value,
                    stage="REMAINING_BATCH_DOWNLOAD",
                    governing_commit="a" * 40,
                    orchestration_contract=contract,
                    batch_plan=plan,
                    execution_environment=environment,
                    authorized_array_range=invalid,
                ),
            )
        payload["unexpected"] = True
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "DISPATCH_RECEIPT_SCHEMA_MISMATCH",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="FIRST_BATCH_DOWNLOAD",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
            ),
        )


def test_scheduler_scripts_resolve_helpers_from_authority_worktree_and_bind_projectnb() -> None:
    common = (ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh").read_text()
    runner = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    final = (ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh").read_text()
    for source in (runner, final):
        assert "AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'" in source
        assert 'source "$COMMON"' in source
        assert "dirname" not in source
        assert "BASH_SOURCE" not in source
    for name in (
        "TMPDIR",
        "XDG_CACHE_HOME",
        "TORCH_HOME",
        "MPLCONFIGDIR",
        "NUMBA_CACHE_DIR",
        "PIP_CACHE_DIR",
        "JOBLIB_TEMP_FOLDER",
    ):
        assert f"export {name}=" in common
    assert "/restricted/projectnb/" in common
    assert "CONCURRENT_BATCH_OWNERSHIP" in common
    assert "mkdir \"$LVEF_C3_ACTIVE_LOCK\"" in common
    for path in (
        ROOT / "scripts" / "scc_dispatch_lvef_c3_production_v2.sh",
        ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh",
        ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh",
    ):
        assert stat.S_IMODE(path.stat().st_mode) & 0o111


def test_future_dispatcher_is_stage_separated_no_ambient_export_and_print_only_capable() -> None:
    source = (ROOT / "scripts" / "scc_dispatch_lvef_c3_production_v2.sh").read_text()
    assert "--print-only|--submit" in source
    assert "C3_FUTURE_DISPATCHER=UNEXECUTED" in source
    assert "QSUB_EXECUTED=NO" in source
    assert "FIRST_BATCH_DOWNLOAD" in source
    assert "REMAINING_BATCH_DOWNLOAD" in source
    assert "DICOM_EXTRACTION" in source
    assert "ECHOPRIME_EMBEDDING" in source
    assert "BATCH_PRESERVATION" in source
    assert "CACHE_RETIREMENT" in source
    assert "PRESERVATION_FINALIZATION" in source
    assert "qsub -terse" in source
    assert "qsub -V" not in source
    assert "-cwd" not in source
    assert 'QSUB_COMMAND+=(-t "$TASK_SCOPE" -tc 1)' in source
    assert "ROLLING_BATCH_SCOPE_INVALID" in source
    assert '[[ "$TASK_SCOPE" =~ ^([2-9]|1[0-9])$ ]]' in source
    commands = (ROOT / "docs" / "lvef_multitask" / "scc_phase1ee_production_commands.md").read_text()
    assert "PRIOR_BATCH_RETIREMENT_JOB" not in commands
    assert '"$CACHE_RETIREMENT_DISPATCH_AUTH" - "$N"' in commands
    assert '"$REMAINING_DOWNLOAD_DISPATCH_AUTH" \\\n  - "$N"' in commands
    assert "2-19)" not in commands
    assert "validate_lvef_c3_dispatch_authorization.py" in source


def test_future_cache_retirement_is_separate_from_preservation_submission() -> None:
    commands = (
        ROOT / "docs" / "lvef_multitask" / "scc_phase1ee_production_commands.md"
    ).read_text()
    bash_blocks = [
        chunk.split("```", 1)[0]
        for chunk in commands.split("```bash\n")[1:]
    ]
    preservation_blocks = [
        block for block in bash_blocks
        if "dispatch_one --submit BATCH_PRESERVATION" in block
    ]
    retirement_blocks = [
        block for block in bash_blocks
        if '"$AUTH_BUILDER" cache-retirement' in block
    ]
    submission_blocks = [
        block for block in bash_blocks if "dispatch_one --submit" in block
    ]
    assert len(submission_blocks) == 7
    assert all(block.count("dispatch_one --submit") == 1 for block in submission_blocks)
    assert len(preservation_blocks) == 1
    assert len(retirement_blocks) == 1
    assert all('"$AUTH_BUILDER" cache-retirement' not in block for block in preservation_blocks)
    assert all("dispatch_one --submit BATCH_PRESERVATION" not in block for block in retirement_blocks)
    assert "preservation job completes, its evidence independently passes" in commands
    assert "owner grants a new cache-retirement authorization" in commands
    assert "ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19" in commands
    for stage in (
        "DICOM_EXTRACTION",
        "ECHOPRIME_EMBEDDING",
        "BATCH_PRESERVATION",
        "CACHE_RETIREMENT",
    ):
        matching = [
            block for block in submission_blocks
            if f"dispatch_one --submit {stage}" in block
        ]
        assert len(matching) == 1
        assert ' - "$N")' in matching[0]
    finalization = [
        block for block in submission_blocks
        if "dispatch_one --submit PRESERVATION_FINALIZATION" in block
    ]
    assert len(finalization) == 1
    assert " - none)" in finalization[0]


def test_owner_authorization_builder_separates_dispatch_and_operation_receipts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        dispatch_root = root / "dispatch"
        extraction_root = root / "extraction"
        dispatch_root.mkdir(mode=0o700)
        extraction_root.mkdir(mode=0o700)
        contract = root / "contract.json"
        plan = root / "plan.json"
        environment = root / "environment"
        launch_authority_path = root / "launch"
        contract.write_text("{}\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("synthetic environment\n", encoding="utf-8")
        launch_authority_path.write_text("{}\n", encoding="utf-8")
        values = {
            "LVEF_C3_DISPATCH_AUTHORIZATION_ROOT": str(dispatch_root),
            "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT": str(extraction_root),
            "LVEF_C3_GOVERNING_COMMIT": "a" * 40,
            "LVEF_C3_ATTEMPT_ID": "lvef_c3_phase1ee_synthetic_001",
            "LVEF_C3_ORCHESTRATION_CONTRACT": str(contract),
            "LVEF_C3_BATCH_PLAN": str(plan),
        }
        launch_value = {
            "post_expansion_capacity_summary": {"sha256": "b" * 64},
            "production_authority_packet": {"sha256": "c" * 64},
        }
        runtime = (values, launch_value, "d" * 64)
        common = {
            "execution_environment": environment,
            "launch_authority": launch_authority_path,
            "owner_authorization_affirmed": "YES",
            "owner_authorization_date": "2026-08-10",
        }
        dispatch_output = dispatch_root / "DICOM_EXTRACTION.1.dispatch_authorization.json"
        dispatch_args = Namespace(
            **common,
            stage="DICOM_EXTRACTION",
            task_scope="1",
            output=dispatch_output,
        )
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime), mock.patch.object(
            owner_authorization.dispatch, "validate"
        ):
            dispatch_value = owner_authorization.build_dispatch(dispatch_args)

        operation_output = extraction_root / "c3_batch_000.authorization.json"
        operation_args = Namespace(
            **common,
            stage="DICOM_EXTRACTION",
            batch_id="c3_batch_000",
            output=operation_output,
        )
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime), mock.patch.object(
            owner_authorization.stages, "validate_stage_authorization_value"
        ):
            operation_value = owner_authorization.build_stage(operation_args)

        assert dispatch_output.parent == dispatch_root
        assert operation_output.parent == extraction_root
        assert dispatch_value["artifact_type"] == "lvef_c3_restricted_dispatch_authorization_v1"
        assert operation_value["artifact_type"] == "lvef_c3_restricted_stage_authorization_v1"
        assert dispatch_value["launch_authority_sha256"] == operation_value["launch_authority_sha256"]
        assert dispatch_value["stage"] == operation_value["stage"]

        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime):
            try:
                owner_authorization.build_dispatch(dispatch_args)
            except owner_authorization.OwnerAuthorizationError as exc:
                assert str(exc) == "LAUNCH_OUTPUT_COLLISION"
            else:
                raise AssertionError("owner authorization receipt was overwritten")

        wrong_output = extraction_root / dispatch_output.name
        wrong_args = Namespace(**{**vars(dispatch_args), "output": wrong_output})
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime):
            try:
                owner_authorization.build_dispatch(wrong_args)
            except owner_authorization.OwnerAuthorizationError as exc:
                assert str(exc) == "AUTHORIZATION_OUTPUT_PATH_MISMATCH"
            else:
                raise AssertionError("dispatch receipt was accepted in an operation root")


def test_dispatcher_stdout_contract_with_synthetic_qsub_stub() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree = root / "authority_worktree"
        scripts = worktree / "scripts"
        scripts.mkdir(parents=True)
        dispatcher = scripts / "scc_dispatch_lvef_c3_production_v2.sh"
        dispatcher.write_text(
            (ROOT / "scripts" / dispatcher.name).read_text().replace(
                "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask",
                str(worktree),
            ),
            encoding="utf-8",
        )
        dispatcher.chmod(0o700)
        production_root = root / "production"
        dispatch_root = production_root / "authorizations" / "dispatch"
        dispatch_root.mkdir(parents=True)
        common = scripts / "lvef_c3_production_scheduler_common.sh"
        common.write_text(
            "lvef_c3_load_runtime(){\n"
            "  LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n"
            f"  LVEF_C3_PRODUCTION_ROOT={production_root}\n"
            f"  LVEF_C3_PYTHON={scripts / 'fake_python'}\n"
            f"  LVEF_C3_GOVERNING_COMMIT={'a' * 40}\n"
            f"  LVEF_C3_ORCHESTRATION_CONTRACT={root / 'contract'}\n"
            f"  LVEF_C3_BATCH_PLAN={root / 'plan'}\n"
            f"  LVEF_C3_DISPATCH_AUTHORIZATION_ROOT={dispatch_root}\n"
            "}\n"
            "lvef_c3_require_projectnb_path(){ :; }\n"
            "lvef_c3_require_private_regular_file(){ :; }\n"
            "lvef_c3_require_private_projectnb_directory(){ :; }\n"
            "lvef_c3_validate_launch_authority(){ :; }\n"
            "lvef_c3_die(){ printf 'C3_DISPATCH_REFUSED=%s\\n' \"$1\" >&2; exit 78; }\n",
            encoding="utf-8",
        )
        fake_python = scripts / "fake_python"
        fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python.chmod(0o700)
        runner = scripts / "scc_run_lvef_c3_production_batch_v2.sh"
        runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        runner.chmod(0o700)
        env_file = root / "environment"
        launch_file = root / "launch_authority"
        auth_file = dispatch_root / "FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json"
        env_file.write_text("synthetic\n", encoding="utf-8")
        launch_file.write_text("synthetic\n", encoding="utf-8")
        auth_file.write_text("synthetic\n", encoding="utf-8")
        tool_bin = root / "bin"
        tool_bin.mkdir()
        qsub = tool_bin / "qsub"
        qsub.write_text("#!/usr/bin/env bash\nprintf '8123456\\n'\n", encoding="utf-8")
        qsub.chmod(0o700)
        environment = dict(os.environ)
        environment["PATH"] = f"{tool_bin}:{environment.get('PATH', '')}"
        result = subprocess.run(
            [
                str(dispatcher), "--submit", "FIRST_BATCH_DOWNLOAD",
                str(env_file), str(launch_file), str(auth_file), "-", "1",
            ],
            capture_output=True, text=True, check=False, env=environment,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert result.stdout == "8123456\n"
        assert "C3_DISPATCH_SUBMISSION=PASS" in result.stderr


def test_production_wrappers_are_authorization_gated_and_raw_deletion_absent() -> None:
    stages_source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    runner_source = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    final_source = (ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh").read_text()
    assert "validate_stage_authorization(" in stages_source
    assert "run-dicom-extraction" in stages_source
    assert "run-echoprime" in stages_source
    assert "view_classifier_used\": False" in stages_source
    assert "weights_only=True" in stages_source
    assert "raw_dicom" not in final_source.lower() or "RAW_DICOM_DELETION=NOT_PERFORMED" in final_source
    for source in (stages_source, runner_source, final_source):
        assert "rm -rf" not in source
        assert "gsutil" not in source


def test_scheduler_private_environment_is_literal_allowlisted_and_not_sourced() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh").read_text()
    assert "lvef_c3_parse_private_environment" in source
    assert "ENVIRONMENT_KEY_DUPLICATE" in source
    assert "ENVIRONMENT_ASSIGNMENT_NOT_LITERAL" in source
    assert 'source "$environment_file"' not in source
    assert "local -A" not in source
    assert "LVEF_C3_CLOUDSDK_CONFIG" in source
    assert "application_default_credentials.json" in source


def test_python_stage_output_gate_rejects_symlinked_projectnb_ancestor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        linked = root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        original = stages.PROJECTNB_PREFIX
        try:
            stages.PROJECTNB_PREFIX = root
            expect_code(
                "OUTPUT_ROOT_SYMLINK_ANCESTOR",
                lambda: stages.require_projectnb_path(linked / "batch"),
            )
        finally:
            stages.PROJECTNB_PREFIX = original


def test_scheduler_literal_environment_parser_rejects_duplicate_and_shell_syntax() -> None:
    common = ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "runtime.env"
        command = [
            "bash", "-c",
            'source "$1"; lvef_c3_parse_private_environment "$2"',
            "bash", str(common), str(env_file),
        ]
        env_file.write_text(
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n"
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n",
            encoding="utf-8",
        )
        duplicate = subprocess.run(command, capture_output=True, text=True, check=False)
        assert duplicate.returncode == 78
        assert "ENVIRONMENT_KEY_DUPLICATE" in duplicate.stderr
        env_file.write_text(
            "LVEF_C3_ATTEMPT_ID=$(touch_should_never_execute)\n",
            encoding="utf-8",
        )
        syntax = subprocess.run(command, capture_output=True, text=True, check=False)
        assert syntax.returncode == 78
        assert "ENVIRONMENT_ASSIGNMENT_NOT_LITERAL" in syntax.stderr


def test_single_active_cache_gate_rejects_symlink_and_nonregular_entries() -> None:
    common = ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache_root = root / "attempts" / "lvef_c3_synthetic_001" / "extracted_cache"
        cache_root.mkdir(parents=True)
        outside = root / "outside"
        outside.mkdir()
        (cache_root / "bad_link").symlink_to(outside)
        shell = (
            "set -euo pipefail; source \"$1\"; "
            "LVEF_C3_PRODUCTION_ROOT=\"$2\"; "
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001; "
            "lvef_c3_require_projectnb_path(){ :; }; "
            "lvef_c3_require_single_active_extraction_cache c3_batch_000"
        )
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(common), str(root)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 78
        assert "EXTRACTION_CACHE_ENTRY_INVALID" in result.stderr
        (cache_root / "bad_link").unlink()
        (cache_root / "regular_file").write_text("x", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(common), str(root)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 78
        assert "EXTRACTION_CACHE_ENTRY_INVALID" in result.stderr


def test_runner_binds_exact_downloader_authority_and_per_batch_stage_receipts() -> None:
    source = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    assert 'download-batch \\' in source
    assert '--governing-commit "$LVEF_C3_GOVERNING_COMMIT"' in source
    assert '--environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT"' in source
    assert '$LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert "lvef_c3_require_single_active_extraction_cache" in source
    assert "/extracted_cache/$BATCH_ID" in source
    assert "validate_lvef_c3_prior_batch_finalization.py" in source
    assert "cache_retirement_finalized.restricted.json" in source


def test_remaining_download_requires_immediately_prior_finalized_batch() -> None:
    assert prior_batch_gate.prior_batch_id("c3_batch_001") == "c3_batch_000"
    assert prior_batch_gate.prior_batch_id("c3_batch_018") == "c3_batch_017"
    for invalid in ("c3_batch_000", "c3_batch_019", "batch_001"):
        expect_code(
            "CURRENT_BATCH_NOT_REMAINING_BATCH",
            lambda invalid=invalid: prior_batch_gate.prior_batch_id(invalid),
        )


def test_echoprime_loader_is_streamed_with_bounded_residency() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    assert "mini_records = ordered_records[start : start + batch_size]" in source
    assert "frames = [" not in source
    assert "del batch, tensors, result" in source
    assert "ProcessPoolExecutor(max_workers=workers)" in source
    assert "packages != list(live_packages)" in source
    assert 'receipt.get("package_count") != len(packages)' in source


def test_stage_failures_are_new_attempt_only_not_false_retryable() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    function = source[source.index("def record_stage_failure(") : source.index("def _build_parser(")]
    assert '"to_state": "FAILED_NONRETRYABLE"' in function
    assert '"FAILED_RETRYABLE" if retryable' not in function
    assert "retryable=" not in source
    assert "requires a new attempt" in function


def test_preservation_stops_at_eligibility_and_retirement_is_explicit() -> None:
    preservation = (ROOT / "scripts" / "preserve_lvef_c3_production_batch.py").read_text()
    retirement = (ROOT / "scripts" / "retire_lvef_c3_extracted_cache_v2.py").read_text()
    assert '"CACHE_RETIREMENT_ELIGIBLE", receipt_body_sha' in preservation
    assert '("FINALIZED", receipt_body_sha)' not in preservation
    assert "--execute" in retirement
    assert "EXTRACTED_CACHE_RETIREMENT" in retirement
    assert "CACHE_ATOMICALLY_STAGED" in retirement
    assert "os.rename(cache_root, staging)" in retirement
    assert 'batch_id / "dicom_extraction" / "clips"' in retirement
    assert "extracted_npz_cache_owner_retirable" in preservation
    assert "dicom_extraction_metadata_retained" in preservation
    assert "embedding_and_pooling_retained" in preservation
    assert "RAW_TARGET_PROHIBITED" in retirement
    assert '"to_state": "FINALIZED"' in retirement
    policy = (ROOT / "configs" / "lvef_c3_preservation_policy_v2.yaml").read_text()
    assert "extracted_cache_retirement_implemented: true" in policy
    assert "extracted_cache_retirement_authorized_by_default: false" in policy
    assert "extracted_cache_retirement_scope: EXTRACTED_NPZ_CLIP_SUBTREE_ONLY" in policy
    assert "dicom_extraction_metadata_retained: true" in policy
    assert "extracted_cache_owner_authorization_required: true" in policy
    assert "extracted_cache_deletion_enabled" not in policy


def test_cache_retirement_uses_core_v2_owner_authorization_schema() -> None:
    assert retirement.AUTH_KEYS == {
        "schema_version", "artifact_type", "status", "authorization_scope",
        "owner_authorized", "owner_authorization_date_utc", "batch_id",
        "attempt_id", "authority_sha256", "preservation_receipt_sha256",
        "cache_inventory_sha256", "launch_authority_sha256",
    }
    source = (ROOT / "scripts" / "retire_lvef_c3_extracted_cache_v2.py").read_text()
    assert '"lvef_c3_cache_retirement_owner_authorization_v2"' in source
    assert "core.evaluate_cache_retirement(" in source
    assert "CACHE_RETIREMENT_POLICY_GATE_BLOCKED" in source


def test_cache_retirement_crash_states_are_closed_and_resumable() -> None:
    assert retirement.classify_retirement_state(
        cache_exists=True, staging_exists=False, intent_exists=False
    ) == "READY_TO_RECORD_INTENT"
    assert retirement.classify_retirement_state(
        cache_exists=True, staging_exists=False, intent_exists=True
    ) == "INTENT_RECORDED_READY_TO_RENAME"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=True, intent_exists=True
    ) == "FULL_STAGING_REQUIRES_HASH_AND_STAGED_RECEIPT"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=True, intent_exists=True,
        staged_receipt_exists=True,
    ) == "RENAMED_OR_PARTIALLY_CLEANED_RESUME"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=False, intent_exists=True,
        staged_receipt_exists=True,
    ) == "PHYSICAL_RETIREMENT_COMPLETE_READY_TO_FINALIZE"
    expect_code(
        "CACHE_AND_RETIREMENT_STAGING_BOTH_EXIST",
        lambda: retirement.classify_retirement_state(
            cache_exists=True, staging_exists=True, intent_exists=True
        ),
    )
    expect_code(
        "ATOMIC_STAGING_EVIDENCE_MISSING",
        lambda: retirement.classify_retirement_state(
            cache_exists=False, staging_exists=False, intent_exists=True
        ),
    )
    expect_code(
        "CACHE_MISSING_BEFORE_RETIREMENT_INTENT",
        lambda: retirement.classify_retirement_state(
            cache_exists=False, staging_exists=False, intent_exists=False
        ),
    )


def test_cache_retirement_delete_is_exact_no_follow_and_resumable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "staging"
        nested = root / "nested"
        nested.mkdir(parents=True)
        (nested / "clip.npz").write_bytes(b"synthetic")
        retirement._delete_cache_tree(root)
        assert not root.exists()

        outside = Path(directory) / "outside"
        outside.mkdir()
        (outside / "keep").write_text("keep", encoding="utf-8")
        root.mkdir()
        (root / "bad").symlink_to(outside)
        expect_code("CACHE_TREE_SYMLINK", lambda: retirement._delete_cache_tree(root))
        assert (outside / "keep").read_text(encoding="utf-8") == "keep"


def _retirement_eligibility_fixture() -> tuple[
    dict[str, object], dict[str, object], dict[str, str], dict[str, object],
    dict[str, str],
]:
    receipt = _canary_eligibility_receipt()
    authority = {
        key: hashlib.sha256(f"retirement-{key}".encode()).hexdigest()
        for key in finalizer.core.RUNTIME_AUTHORITY_KEYS
    }
    authority["git_commit"] = "a" * 40
    authority.update(
        {
            "checkpoint_sha256": str(receipt["checkpoint_sha256"]),
            "state_machine_schema_sha256": "e" * 64,
            "resume_ledger_schema_sha256": "f" * 64,
        }
    )
    contract = {
        "cohort": {
            "release": "mimic-iv-echo/1.0",
            "split_map_sha256": "d" * 64,
        },
        "authority": {
            "execution_contract_version": 2,
            "state_machine_schema_sha256": authority[
                "state_machine_schema_sha256"
            ],
            "resume_ledger_schema_sha256": authority[
                "resume_ledger_schema_sha256"
            ],
        },
    }
    planned = {
        "n_studies": 5,
        "n_subjects": 5,
        "n_objects": 6,
        "source_bytes": 60_000,
        "objects": [],
    }
    receipt["command_checksum"] = finalizer.core.canonical_json_sha256(
        {
            key: receipt[key]
            for key in (
                "production_stage_wrapper_sha256",
                "batch_preservation_script_sha256",
                "scheduler_runner_sha256",
            )
        }
    )
    receipt["config_checksum"] = finalizer.core.canonical_json_sha256(
        {
            "orchestration_contract_sha256": receipt[
                "orchestration_contract_sha256"
            ],
            "batch_plan_sha256": receipt["batch_plan_sha256"],
            "state_machine_schema_sha256": authority[
                "state_machine_schema_sha256"
            ],
            "resume_ledger_schema_sha256": authority[
                "resume_ledger_schema_sha256"
            ],
        }
    )
    hash_by_name = {
        "contract.yaml": str(receipt["orchestration_contract_sha256"]),
        "environment.json": str(receipt["environment_receipt_sha256"]),
        "pooling_resume_ledger.restricted.json": str(
            receipt["state_input_ledger_sha256"]
        ),
        "download_resume_ledger.restricted.json": str(
            receipt["source_receipt_sha256"]
        ),
        "dicom_audit.restricted.csv": str(receipt["dicom_audit_sha256"]),
        "extraction_manifest.restricted.csv": str(
            receipt["extraction_manifest_sha256"]
        ),
        "clip_manifest.restricted.csv": str(receipt["clip_manifest_sha256"]),
        "clip_embeddings.restricted.npz": str(receipt["clip_embeddings_sha256"]),
        "study_manifest.restricted.csv": str(receipt["study_manifest_sha256"]),
        "study_embeddings.restricted.npz": str(receipt["study_embeddings_sha256"]),
        "batch_preservation_manifest.restricted.tsv": str(
            receipt["preservation_manifest_sha256"]
        ),
        "lvef_c3_production_stages.py": str(
            receipt["production_stage_wrapper_sha256"]
        ),
        "preserve_lvef_c3_production_batch.py": str(
            receipt["batch_preservation_script_sha256"]
        ),
        "scc_run_lvef_c3_full_sequential.sh": str(
            receipt["scheduler_runner_sha256"]
        ),
    }
    return receipt, planned, authority, contract, hash_by_name


def test_retirement_closed_validates_every_eligibility_invariant() -> None:
    receipt, planned, authority, contract, hash_by_name = (
        _retirement_eligibility_fixture()
    )

    def validate(candidate: dict[str, object]) -> None:
        with mock.patch.object(
            retirement,
            "sha256_file",
            side_effect=lambda path: hash_by_name[Path(path).name],
        ), mock.patch.object(
            retirement,
            "load_json_and_sha256",
            return_value=(
                {"package_inventory_sha256": receipt["package_inventory_sha256"]},
                receipt["environment_receipt_sha256"],
            ),
        ):
            retirement.validate_preservation_eligibility_receipt(
                candidate,
                planned_batch=planned,
                governing_commit="a" * 40,
                attempt_id="lvef_c3_canary_synthetic",
                batch_id="canary_batch_000",
                plan_sha256="c" * 64,
                expected_authority=authority,
                contract=contract,
                contract_path=Path("contract.yaml"),
                environment_receipt=Path("environment.json"),
                production_root=Path("/synthetic/production"),
                preservation_manifest=Path(
                    "batch_preservation_manifest.restricted.tsv"
                ),
            )

    validate(receipt)
    mutations = (
        ("PRESERVATION_RECEIPT_SCHEMA_MISMATCH", lambda value: value.update(extra=True)),
        ("PRESERVATION_GATE_FAILED", lambda value: value.update(pooling_gate_passed=False)),
        (
            "PRESERVATION_SCIENTIFIC_INCONSISTENCY",
            lambda value: value.update(n_duplicate_physical_sources=1),
        ),
        (
            "PRESERVATION_COUNT_OR_DISPOSITION_MISMATCH",
            lambda value: value.update(n_download_verified=5),
        ),
        (
            "PRESERVATION_COUNT_OR_DISPOSITION_MISMATCH",
            lambda value: value.update(no_cine_disposition="INVALID"),
        ),
        (
            "PRESERVATION_AUTHORITY_INVALID",
            lambda value: value.update(package_inventory_sha256="0" * 64),
        ),
        ("PRESERVATION_HASH_INVALID", lambda value: value.update(clip_manifest_sha256="bad")),
    )
    for code, mutate in mutations:
        candidate = copy.deepcopy(receipt)
        mutate(candidate)
        expect_code(code, lambda candidate=candidate: validate(candidate))

    mismatched = copy.deepcopy(receipt)
    bad_hashes = dict(hash_by_name)
    bad_hashes["clip_embeddings.restricted.npz"] = "f" * 64
    with mock.patch.object(
        retirement,
        "sha256_file",
        side_effect=lambda path: bad_hashes[Path(path).name],
    ), mock.patch.object(
        retirement,
        "load_json_and_sha256",
        return_value=(
            {"package_inventory_sha256": receipt["package_inventory_sha256"]},
            receipt["environment_receipt_sha256"],
        ),
    ):
        expect_code(
            "PRESERVATION_REFERENCED_HASH_MISMATCH",
            lambda: retirement.validate_preservation_eligibility_receipt(
                mismatched,
                planned_batch=planned,
                governing_commit="a" * 40,
                attempt_id="lvef_c3_canary_synthetic",
                batch_id="canary_batch_000",
                plan_sha256="c" * 64,
                expected_authority=authority,
                contract=contract,
                contract_path=Path("contract.yaml"),
                environment_receipt=Path("environment.json"),
                production_root=Path("/synthetic/production"),
                preservation_manifest=Path(
                    "batch_preservation_manifest.restricted.tsv"
                ),
            ),
        )


def test_retirement_derives_before_comparing_and_validates_exact_object_scope() -> None:
    derived = {
        key: hashlib.sha256(f"runtime-{key}".encode()).hexdigest()
        for key in finalizer.core.RUNTIME_AUTHORITY_KEYS
    }
    derived["git_commit"] = "a" * 40
    ledger = {"authority": derived}
    calls: list[str] = []

    def derive(**_kwargs):
        calls.append("derive")
        return derived

    def validate_resume(value, **kwargs):
        calls.append("resume")
        assert value is ledger
        assert kwargs["expected_authority"] == derived
        assert kwargs["attempt_id"] == "lvef_c3_synthetic_001"
        assert kwargs["expected_object_keys"] == {
            "c3_batch_000": {"1" * 64, "2" * 64}
        }

    with mock.patch.object(
        retirement, "derive_current_runtime_authority", side_effect=derive
    ), mock.patch.object(
        retirement.core, "validate_resume_authority", side_effect=validate_resume
    ):
        observed = retirement._derive_and_validate_ledger_authority(
            ledger=ledger,
            plan={},
            effective_requirements=object(),
            contract={},
            contract_path=Path("contract"),
            governing_commit="a" * 40,
            environment_receipt=Path("environment"),
            supplied_runtime_authority=derived,
            attempt_id="lvef_c3_synthetic_001",
            batch_id="c3_batch_000",
            expected_object_keys={"1" * 64, "2" * 64},
        )
        assert observed == derived
        assert calls == ["derive", "resume"]

        drifted = dict(derived)
        drifted["batch_plan_sha256"] = "f" * 64
        expect_code(
            "SUPPLIED_RUNTIME_AUTHORITY_MISMATCH",
            lambda: retirement._derive_and_validate_ledger_authority(
                ledger=ledger,
                plan={},
                effective_requirements=object(),
                contract={},
                contract_path=Path("contract"),
                governing_commit="a" * 40,
                environment_receipt=Path("environment"),
                supplied_runtime_authority=drifted,
                attempt_id="lvef_c3_synthetic_001",
                batch_id="c3_batch_000",
                expected_object_keys={"1" * 64, "2" * 64},
            ),
        )
        assert calls == ["derive", "resume", "derive"]


def test_retirement_arbitrary_root_override_requires_private_test_capability() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        expect_code(
            "PRODUCTION_ROOT_AUTHORITY_MISMATCH",
            lambda: retirement._validate_production_root(
                root,
                allowed_production_prefix=root,
                synthetic_test_capability=None,
            ),
        )
        retirement._validate_production_root(
            root,
            allowed_production_prefix=root,
            synthetic_test_capability=retirement._SYNTHETIC_TEST_ROOT_CAPABILITY,
        )


def test_invalid_retirement_gate_cannot_reach_rename_or_delete() -> None:
    argv = [
        "--execute", "--contract", "contract", "--plan", "plan",
        "--environment-receipt", "environment", "--production-root", "production",
        "--attempt-id", "lvef_c3_synthetic_001", "--batch-id", "c3_batch_000",
        "--governing-commit", "a" * 40, "--final-ledger", "ledger",
        "--preservation-receipt", "preservation", "--authorization-receipt",
        "authorization", "--launch-authority-sha256", "b" * 64,
    ]
    with mock.patch.object(
        retirement,
        "validate_gate",
        side_effect=retirement.CacheRetirementError("PRESERVATION_GATE_FAILED"),
    ), mock.patch.object(retirement.os, "rename") as rename, mock.patch.object(
        retirement, "_delete_cache_tree"
    ) as delete:
        expect_code("PRESERVATION_GATE_FAILED", lambda: retirement.main(argv))
        rename.assert_not_called()
        delete.assert_not_called()


def _terminal_transition_fixture() -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    authority = {
        key: hashlib.sha256(f"terminal-{key}".encode()).hexdigest()
        for key in finalizer.core.RUNTIME_AUTHORITY_KEYS
    }
    authority["git_commit"] = "a" * 40
    predecessor = "1" * 64
    transition: dict[str, object] = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": "lvef_c3_synthetic_001",
        "batch_id": "c3_batch_000",
        "from_state": "CACHE_RETIREMENT_ELIGIBLE",
        "to_state": "FINALIZED",
        "status": "PASS",
        "authority": authority,
        "input_receipt_sha256": [predecessor],
        "output_manifest_sha256": "2" * 64,
    }
    ledger = {
        "batches": {
            "c3_batch_000": {
                "events": [
                    {
                        "from_state": "PRESERVATION_COMPLETE",
                        "to_state": "CACHE_RETIREMENT_ELIGIBLE",
                        "receipt_sha256": predecessor,
                    },
                    {
                        "from_state": "CACHE_RETIREMENT_ELIGIBLE",
                        "to_state": "FINALIZED",
                        "receipt_sha256": finalizer.core.canonical_json_sha256(
                            transition
                        ),
                    },
                ]
            }
        }
    }
    return transition, ledger, authority


def test_prior_terminal_transition_is_closed_and_exactly_predecessor_bound() -> None:
    transition, ledger, authority = _terminal_transition_fixture()

    def validate(candidate: dict[str, object]) -> None:
        candidate_ledger = copy.deepcopy(ledger)
        candidate_ledger["batches"]["c3_batch_000"]["events"][-1][
            "receipt_sha256"
        ] = finalizer.core.canonical_json_sha256(candidate)
        prior_batch_gate.validate_terminal_transition(
            candidate,
            ledger=candidate_ledger,
            expected_authority=authority,
            attempt_id="lvef_c3_synthetic_001",
            batch_id="c3_batch_000",
            final_receipt_sha256="2" * 64,
        )

    validate(transition)
    mutations = (
        lambda value: value.update(extra=True),
        lambda value: value.update(input_receipt_sha256=["1" * 64, "3" * 64]),
        lambda value: value.update(output_manifest_sha256="4" * 64),
        lambda value: value.update(status="FAIL"),
        lambda value: value["authority"].update(batch_plan_sha256="5" * 64),
    )
    for mutate in mutations:
        candidate = copy.deepcopy(transition)
        mutate(candidate)
        expect_code(
            "PRIOR_FINALIZATION_TRANSITION_INVALID",
            lambda candidate=candidate: validate(candidate),
        )


def test_prior_gate_derives_before_supplied_comparison_and_exact_scope() -> None:
    authority = {
        key: hashlib.sha256(f"prior-{key}".encode()).hexdigest()
        for key in finalizer.core.RUNTIME_AUTHORITY_KEYS
    }
    authority["git_commit"] = "a" * 40
    source = "1" * 64
    attempt = "lvef_c3_synthetic_001"
    root = Path("/synthetic/attempt")
    plan_path = root / "full_batch_plan.restricted.json"
    prior_root = root / "batches" / "c3_batch_000"
    receipt_path = (
        prior_root / "preservation" / "batch_finalization_receipt.restricted.json"
    )
    ledger_path = prior_root / "final_resume_ledger.restricted.json"
    transition_path = (
        prior_root / "preservation" / "cache_retirement_finalized.restricted.json"
    )
    plan = {
        "batches": [
            {
                "batch_id": "c3_batch_000",
                "objects": [{"source_object_key": source}],
            },
            {"batch_id": "c3_batch_001", "objects": []},
        ]
    }
    receipt = {
        "batch_id": "c3_batch_000",
        "attempt_id": attempt,
        "governing_commit": "a" * 40,
        "batch_plan_sha256": "b" * 64,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
    }
    ledger = {
        "status": "COMPLETE",
        "batches": {"c3_batch_000": {"state": "FINALIZED"}},
    }
    transition: dict[str, object] = {}
    calls: list[str] = []

    def load(_path: Path, code: str, **_kwargs):
        return {
            "PRIOR_BATCH_PLAN": (plan, "2" * 64),
            "PRIOR_FINAL_RECEIPT": (receipt, "3" * 64),
            "PRIOR_FINAL_LEDGER": (ledger, "4" * 64),
            "PRIOR_FINALIZATION_TRANSITION": (transition, "5" * 64),
        }[code]

    def derive(**_kwargs):
        calls.append("derive")
        return authority

    def resume(value, **kwargs):
        calls.append("resume")
        assert value is ledger
        assert kwargs["expected_authority"] == authority
        assert kwargs["attempt_id"] == attempt
        assert kwargs["expected_object_keys"] == {"c3_batch_000": {source}}

    def terminal(*_args, **_kwargs):
        calls.append("terminal")

    patches = (
        mock.patch.object(prior_batch_gate.core, "load_orchestration_contract", return_value={}),
        mock.patch.object(prior_batch_gate.retirement, "load_json_and_sha256", side_effect=load),
        mock.patch.object(prior_batch_gate.core, "validate_batch_plan", return_value="b" * 64),
        mock.patch.object(prior_batch_gate.finalizer, "_validate_receipt"),
        mock.patch.object(
            prior_batch_gate.retirement,
            "derive_current_runtime_authority",
            side_effect=derive,
        ),
        mock.patch.object(prior_batch_gate.core, "validate_resume_authority", side_effect=resume),
        mock.patch.object(prior_batch_gate, "validate_terminal_transition", side_effect=terminal),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        prior_batch_gate.validate_prior_batch(
            current_batch_id="c3_batch_001",
            attempt_id=attempt,
            governing_commit="a" * 40,
            contract_path=Path("contract.yaml"),
            plan_path=plan_path,
            environment_receipt=Path("environment.json"),
            final_receipt_path=receipt_path,
            final_ledger_path=ledger_path,
            transition_path=transition_path,
            requirements=object(),
            expected_runtime_authority=authority,
        )
    assert calls == ["derive", "resume", "terminal"]

    calls.clear()
    drifted = dict(authority)
    drifted["batch_plan_sha256"] = "f" * 64
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        expect_code(
            "SUPPLIED_RUNTIME_AUTHORITY_MISMATCH",
            lambda: prior_batch_gate.validate_prior_batch(
                current_batch_id="c3_batch_001",
                attempt_id=attempt,
                governing_commit="a" * 40,
                contract_path=Path("contract.yaml"),
                plan_path=plan_path,
                environment_receipt=Path("environment.json"),
                final_receipt_path=receipt_path,
                final_ledger_path=ledger_path,
                transition_path=transition_path,
                requirements=object(),
                expected_runtime_authority=drifted,
            ),
        )
    assert calls == ["derive"]
