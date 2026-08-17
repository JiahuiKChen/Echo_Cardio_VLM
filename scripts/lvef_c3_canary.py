#!/usr/bin/env python3
"""DEPRECATED historical five-qsub C3 canary control plane.

This implementation is retained for evidence and comparison only. It is not
the controlling canary route after the historical pipeline recovery sprint;
the tracked single-job route is ``scc_submit_lvef_c3_minimal_canary.sh``.

The installation and strengthened preflight modes are dependency-light and
deliberately have no cloud client, real scheduler invocation, restricted-row
reader, DICOM body reader, GPU, model-fitting, prediction, or confirmatory-
performance path.  Live execution is gated by the tracked lifecycle policy,
one validated owner-private lifecycle snapshot, and one independently sealed
owner-private execution authority.

The synthetic integration surface below is an in-process proof harness.  Its
hooks substitute only external effects; every scientific contract check,
temporal transform, mean-pooling rule, and finalizer is the same tracked
callable used by the production path.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, MutableMapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import finalize_lvef_c3_production as finalizer
import finalize_lvef_phase1ef_d3 as phase1eg_authority
import build_lvef_c3_production_authority_packet as production_authority_packet
import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler
import lvef_c3_execution_state as execution_state
import lvef_c3_orchestration_core as orchestration_core
import lvef_c3_production_stages as production_stages
import lvef_reconstruction_smoke as reconstruction
import preserve_lvef_c3_production_batch as preservation


DEFAULT_EXECUTION_STATE = REPOSITORY_ROOT / "configs/lvef_c3_execution_state_v1.yaml"
DEFAULT_ORCHESTRATION_CONTRACT = REPOSITORY_ROOT / "configs/lvef_c3_orchestration_v2.yaml"
DEFAULT_SCHEDULER_PLAN = REPOSITORY_ROOT / "configs/lvef_c3_canary_scheduler_plan_v1.json"
REQUIRED_BRANCH = "codex/lvef-multitask-revalidation"
CANARY_EXECUTION_SCOPE = "execute_exact_five_canary"
PHASE1HR1_STARTING_AUTHORITY_COMMIT = (
    "0bfcba9973fa6592ca75aa23c6cf0e42d432c5cb"
)
PHASE1HR2_STARTING_AUTHORITY_COMMIT = (
    "34c8ad795a7059f1d76b53a9a8be543132b095f7"
)
PHASE1HR1_CURRENT_ENVIRONMENT_RECEIPT_BYTES = 6_026
PHASE1HR1_CURRENT_ENVIRONMENT_RECEIPT_SHA256 = (
    "182a03baa5b66b103fe80a3d4b4f3ab2941b7c5e6abf368d5f9b49781a8b8683"
)
PHASE1EG_PRODUCTION_PACKET_BYTES = 7_492
PHASE1EG_PRODUCTION_PACKET_SHA256 = (
    "2725570d1137640e0c00ae790f1ae3583d63b17c7f957e86e886892dd0e6ba07"
)
SYNTHETIC_ATTEMPT_ID = "lvef_c3_synthetic_canary"
SYNTHETIC_BATCH_ID = "c3_batch_000"
SYNTHETIC_CHECKPOINT_BYTES = (
    b"synthetic exact-five EchoPrime checkpoint authority\n"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LIVE_PLAN_CONFIGURATION_MAP = {
    "source_metadata": "source_metadata_sha256",
    "split_map": "split_map_sha256",
    "checkpoint": "checkpoint_sha256",
    "environment_receipt": "environment_receipt_sha256",
    "state_machine_schema": "state_machine_schema_sha256",
    "resume_ledger_schema": "resume_ledger_schema_sha256",
    "gcloud_resolution_receipt": "gcloud_resolution_receipt_sha256",
    "gcloud_executable": "gcloud_executable_sha256",
    "crc32c_python_executable": "crc32c_python_executable_sha256",
    "crc32c_worker": "crc32c_worker_sha256",
    "crc32c_distribution": "crc32c_distribution_sha256",
}
TRACKED_CANARY_CONTROL_FILES = (
    REPOSITORY_ROOT / "configs/lvef_c3_execution_state_v1.yaml",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_manifest_schema_v1.json",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_scheduler_plan_v1.json",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_execution_authority_schema_v1.json",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_preselection_authority_schema_v1.json",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_state_snapshot_schema_v1.json",
    REPOSITORY_ROOT / "configs/lvef_c3_canary_live_dependencies_v1.json",
    SCRIPT_ROOT / "lvef_c3_execution_state.py",
    SCRIPT_ROOT / "lvef_c3_canary.py",
    SCRIPT_ROOT / "lvef_c3_canary_manifest.py",
    SCRIPT_ROOT / "lvef_c3_canary_scheduler_plan.py",
    SCRIPT_ROOT / "lvef_c3_canary_execution_authority.py",
    SCRIPT_ROOT / "lvef_c3_canary_state.py",
    SCRIPT_ROOT / "lvef_c3_canary_authority_materializer.py",
    SCRIPT_ROOT / "capture_lvef_c3_post_reallocation_capacity.py",
    SCRIPT_ROOT / "lvef_c3_canary_dispatch.py",
    SCRIPT_ROOT / "lvef_c3_canary_stage_worker.py",
    SCRIPT_ROOT / "scc_run_lvef_c3_canary.sh",
    SCRIPT_ROOT / "scc_run_lvef_c3_canary_stage.sh",
)
TRACKED_CONTROL_RELATIVE_FILES = (
    "configs/lvef_c3_execution_state_v1.yaml",
    "configs/lvef_c3_canary_manifest_schema_v1.json",
    "configs/lvef_c3_canary_scheduler_plan_v1.json",
    "configs/lvef_c3_canary_execution_authority_schema_v1.json",
    "configs/lvef_c3_canary_preselection_authority_schema_v1.json",
    "configs/lvef_c3_canary_state_snapshot_schema_v1.json",
    "configs/lvef_c3_canary_live_dependencies_v1.json",
    "scripts/lvef_c3_execution_state.py",
    "scripts/lvef_c3_canary.py",
    "scripts/lvef_c3_canary_manifest.py",
    "scripts/lvef_c3_canary_scheduler_plan.py",
    "scripts/lvef_c3_canary_execution_authority.py",
    "scripts/lvef_c3_canary_state.py",
    "scripts/lvef_c3_canary_authority_materializer.py",
    "scripts/capture_lvef_c3_post_reallocation_capacity.py",
    "scripts/lvef_c3_canary_dispatch.py",
    "scripts/lvef_c3_canary_stage_worker.py",
    "scripts/scc_run_lvef_c3_canary.sh",
    "scripts/scc_run_lvef_c3_canary_stage.sh",
)


PRODUCTION_FUNCTIONS = {
    "source_transfer": orchestration_core.execute_exact_batch_download,
    "download_integrity": orchestration_core.verify_downloaded_partial,
    "dicom_stage": production_stages.run_production_dicom_extraction,
    "dicom_rows": production_stages.validate_production_dicom_rows,
    "extraction_rows": production_stages.validate_production_extraction_rows,
    "echoprime_stage": production_stages.run_production_echoprime,
    "embedding_values": production_stages.validate_embedding_values,
    "temporal_sampling": reconstruction.temporal_sample,
    "encoder_input": reconstruction._prepare_encoder_input,
    "study_mean_pooling": preservation.mean_pool_study_embeddings,
    "pooling_records": preservation.validate_study_pooling_records,
    "preservation": preservation.preserve_batch,
    "canary_finalization": finalizer.finalize_canary_preservation_receipt,
}


class CanaryIntegrationError(RuntimeError):
    """A fixed-code synthetic integration failure with its terminal ledger."""

    def __init__(self, code: str, *, claim_ledger: Mapping[str, Any] | None = None):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_INTEGRATION_INVALID"
        super().__init__(code)
        self.code = code
        self.claim_ledger = dict(claim_ledger or {})


class CanaryControlError(RuntimeError):
    """A safe aggregate-only control-plane failure."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_CONTROL_INVALID"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CanaryPrivateAuthorityConfig:
    """Read-only SCC private-authority roots and frozen receipt identity."""

    recovery: phase1eg_authority.RecoveryConfig = field(
        default_factory=phase1eg_authority.RecoveryConfig
    )
    current_environment_commit: str = PHASE1HR1_STARTING_AUTHORITY_COMMIT
    current_environment_bytes: int = PHASE1HR1_CURRENT_ENVIRONMENT_RECEIPT_BYTES
    current_environment_sha256: str = PHASE1HR1_CURRENT_ENVIRONMENT_RECEIPT_SHA256


@dataclass(frozen=True)
class CanaryControlRuntime:
    """Inject only filesystem roots and irreversible-effect adapters for tests.

    The public SCC wrapper supplies no overrides.  Synthetic acceptance uses a
    real clean Git sandbox and the same control components while substituting
    only qsub.  No field can weaken manifest, packet, lifecycle, hash, mode, or
    no-clobber validation.
    """

    repository_root: Path = REPOSITORY_ROOT
    execution_state_path: Path = DEFAULT_EXECUTION_STATE
    orchestration_contract_path: Path = DEFAULT_ORCHESTRATION_CONTRACT
    scheduler_plan_path: Path = DEFAULT_SCHEDULER_PLAN
    lifecycle_root: Path | None = None
    authority_path: Path | None = None
    authority_paths: Any | None = None
    materialization_authority_source: Any | None = None
    private_authority_config: CanaryPrivateAuthorityConfig = field(
        default_factory=CanaryPrivateAuthorityConfig
    )
    qsub_submitter: Callable[[Sequence[str]], str] | None = None
    synthetic_external_effects: bool = False


@dataclass
class CanaryIntegrationContext:
    manifest: Mapping[str, Any]
    scheduler_plan: Mapping[str, Any]
    artifacts: MutableMapping[str, Any] = field(default_factory=dict)
    call_trace: list[str] = field(default_factory=list)
    _declared_objects: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict, repr=False
    )

    def access_declared_object(self, source_object_key: str) -> Mapping[str, Any]:
        try:
            return self._declared_objects[source_object_key]
        except (KeyError, TypeError) as exc:
            raise CanaryIntegrationError("CANARY_UNDECLARED_OBJECT_ACCESS") from exc


IntegrationHook = Callable[[CanaryIntegrationContext], None]


@dataclass(frozen=True)
class CanaryIntegrationHooks:
    source_transfer: IntegrationHook
    integrity_verification: IntegrationHook
    dicom_audit_decode: IntegrationHook
    cine_extraction: IntegrationHook
    encoder_inference: IntegrationHook
    preservation: IntegrationHook


@dataclass(frozen=True)
class CanaryIntegrationResult:
    claim_ledger: Mapping[str, Any]
    completed_stages: tuple[str, ...]
    study_embeddings: Any
    aggregate_summary: Mapping[str, Any]
    production_continuation: bool
    reachable_roles: frozenset[str]
    call_trace: tuple[str, ...]


def _manifest_objects(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for study in manifest["manifest"]["studies"]:
        for item in study["objects"]:
            value = {
                **item,
                "subject_id": study["subject_id"],
                "study_id": study["study_id"],
                "split": study["split"],
            }
            key = str(item["source_object_key"])
            if key in declared:
                raise CanaryIntegrationError("CANARY_DECLARED_OBJECT_DUPLICATE")
            declared[key] = value
    return declared


def canary_requirements(manifest: Mapping[str, Any]) -> orchestration_core.PlanRequirements:
    """Project a validated exact-five manifest into the production plan type."""

    normalized = manifest_contract.validate_manifest(manifest)
    body = normalized["manifest"]
    return orchestration_core.PlanRequirements(
        release=str(body["source_release"]),
        selected_studies=int(body["study_count"]),
        selected_subjects=int(body["subject_count"]),
        normalized_source_objects=int(body["expected_object_count"]),
        selected_source_bytes=int(body["expected_byte_total"]),
        batch_count=1,
        studies_per_full_batch=5,
        final_batch_studies=5,
        contract_id="lvef_multitask_c3_exact_five_canary_v1",
    )


def synthetic_environment_receipt(governing_commit: str) -> dict[str, Any]:
    """Return the deterministic, offline-only runtime receipt used by tests."""

    package_inventory: list[dict[str, str]] = []
    return {
        "schema_version": 3,
        "artifact_type": "lvef_c3_production_environment_authority_v3",
        "status": "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION",
        "governing_commit": governing_commit,
        "captured_at_utc": "2026-08-12T16:00:00Z",
        "source_environment_receipt_sha256": "1" * 64,
        "python_executable_sha256": "2" * 64,
        "python_version": "synthetic-python",
        "torch_version": "synthetic-torch",
        "torchvision_version": "synthetic-torchvision",
        "cuda_version": "synthetic-cuda",
        "cudnn_version": "synthetic-cudnn",
        "crc32c_runtime_source": "PINNED_CLOUDSDK_BUNDLED_PYTHON",
        "crc32c_python_executable_sha256": "3" * 64,
        "crc32c_python_version": "synthetic-crc32c-python",
        "crc32c_worker_sha256": "4" * 64,
        "crc32c_worker_protocol_version": 1,
        "google_crc32c_version": "synthetic-google-crc32c",
        "google_crc32c_implementation": "c",
        "google_crc32c_distribution_sha256": "5" * 64,
        "google_crc32c_distribution_file_count": 1,
        "google_crc32c_known_vector_base64": "4waSgw==",
        "package_inventory": package_inventory,
        "package_inventory_sha256": orchestration_core.canonical_json_sha256(
            package_inventory
        ),
        "package_count": len(package_inventory),
        "operating_system": "synthetic-offline",
        "gpu_execution_performed": False,
        "cloud_request_performed": False,
        "dicom_body_read": False,
        "model_fitted": False,
        "prediction_generated": False,
        "confirmatory_performance_accessed": False,
    }


def synthetic_environment_receipt_bytes(governing_commit: str) -> bytes:
    return (
        json.dumps(
            synthetic_environment_receipt(governing_commit),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _synthetic_plan_authority(manifest: Mapping[str, Any]) -> dict[str, str]:
    body = manifest["manifest"]
    configuration = {
        str(item["logical_name"]): str(item["sha256"])
        for item in body["source_configuration_hashes"]
    }
    fallback = hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    authority = {
        key: (str(body["source_authority_commit"]) if key == "git_commit" else fallback)
        for key in orchestration_core.PLAN_AUTHORITY_KEYS
    }
    authority["selected_manifest_sha256"] = str(manifest["manifest_sha256"])
    authority["selected_source_manifest_sha256"] = str(body["source_manifest_sha256"])
    authority["source_metadata_sha256"] = str(body["source_manifest_sha256"])
    contract = orchestration_core.load_orchestration_contract(
        DEFAULT_ORCHESTRATION_CONTRACT
    )
    authority.update(
        {
            "orchestration_contract_sha256": hashlib.sha256(
                DEFAULT_ORCHESTRATION_CONTRACT.read_bytes()
            ).hexdigest(),
            "state_machine_schema_sha256": str(
                contract["authority"]["state_machine_schema_sha256"]
            ),
            "resume_ledger_schema_sha256": str(
                contract["authority"]["resume_ledger_schema_sha256"]
            ),
            "checkpoint_sha256": hashlib.sha256(
                SYNTHETIC_CHECKPOINT_BYTES
            ).hexdigest(),
            "environment_receipt_sha256": hashlib.sha256(
                synthetic_environment_receipt_bytes(
                    str(body["source_authority_commit"])
                )
            ).hexdigest(),
        }
    )
    return authority


def live_plan_authority_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, str]:
    """Build only an exact externally sealed production runtime authority."""

    normalized = manifest_contract.validate_manifest(manifest)
    body = normalized["manifest"]
    configuration = {
        str(item["logical_name"]): str(item["sha256"])
        for item in body["source_configuration_hashes"]
    }
    required = {
        "execution_state",
        "production_contract",
        *LIVE_PLAN_CONFIGURATION_MAP,
    }
    if not required.issubset(configuration):
        raise CanaryControlError("CANARY_LIVE_PLAN_AUTHORITY_INCOMPLETE")
    contract = orchestration_core.load_orchestration_contract(
        DEFAULT_ORCHESTRATION_CONTRACT
    )
    expected_fixed = {
        "execution_state": hashlib.sha256(DEFAULT_EXECUTION_STATE.read_bytes()).hexdigest(),
        "production_contract": hashlib.sha256(
            DEFAULT_ORCHESTRATION_CONTRACT.read_bytes()
        ).hexdigest(),
        "checkpoint": orchestration_core.EXPECTED_CHECKPOINT_SHA256,
        "split_map": orchestration_core.EXPECTED_SPLIT_MAP_SHA256,
        "state_machine_schema": str(
            contract["authority"]["state_machine_schema_sha256"]
        ),
        "resume_ledger_schema": str(
            contract["authority"]["resume_ledger_schema_sha256"]
        ),
    }
    if any(configuration[name] != digest for name, digest in expected_fixed.items()):
        raise CanaryControlError("CANARY_LIVE_PLAN_AUTHORITY_MISMATCH")
    authority = {
        "git_commit": str(body["source_authority_commit"]),
        "orchestration_contract_sha256": configuration["production_contract"],
        "selected_manifest_sha256": str(normalized["manifest_sha256"]),
        "selected_source_manifest_sha256": str(body["source_manifest_sha256"]),
    }
    authority.update(
        {
            authority_name: configuration[configuration_name]
            for configuration_name, authority_name in LIVE_PLAN_CONFIGURATION_MAP.items()
        }
    )
    return orchestration_core._validate_plan_authority(authority)


def build_canary_batch_plan(
    manifest: Mapping[str, Any], *, synthetic_authority: bool = False
) -> tuple[dict[str, Any], orchestration_core.PlanRequirements]:
    """Use the production immutable-plan builder for one exact-five batch."""

    normalized = manifest_contract.validate_manifest(manifest)
    requirements = canary_requirements(normalized)
    body = normalized["manifest"]
    selected_rows = [
        {"subject_id": row["subject_id"], "study_id": row["study_id"]}
        for row in body["studies"]
    ]
    split_rows = [
        {"subject_id": row["subject_id"], "split": "train"}
        for row in body["studies"]
    ]
    source_rows = []
    for study in body["studies"]:
        for item in study["objects"]:
            source_rows.append(
                {
                    "release_id": body["source_release"],
                    "subject_id": study["subject_id"],
                    "study_id": study["study_id"],
                    "split": "train",
                    "production_batch": SYNTHETIC_BATCH_ID,
                    **item,
                }
            )
    plan = orchestration_core.build_immutable_batch_plan(
        selected_rows,
        source_rows,
        split_rows,
        requirements=requirements,
        authority=(
            _synthetic_plan_authority(normalized)
            if synthetic_authority
            else live_plan_authority_from_manifest(normalized)
        ),
        prespecified_no_cine_studies=(),
    )
    if (
        len(plan["batches"]) != 1
        or plan["batches"][0]["batch_id"] != SYNTHETIC_BATCH_ID
        or any(row["split"] != "train" for row in plan["batches"][0]["studies"])
    ):
        raise CanaryIntegrationError("CANARY_PRODUCTION_PLAN_SCOPE_INVALID")
    return plan, requirements


def _array_hash(value: Any) -> str:
    return reconstruction.array_content_sha256(value)


def _complete_stage(
    ledger: Mapping[str, Any], plan: Mapping[str, Any], stage_id: str,
    *, predecessor_hash: str | None, receipt_value: Any,
) -> tuple[dict[str, Any], str]:
    predecessor = [] if predecessor_hash is None else [predecessor_hash]
    claimed = scheduler.claim_stage_submission(
        ledger, plan, stage_id, predecessor_receipt_sha256s=predecessor
    )
    receipt_hash = scheduler.canonical_json_sha256(
        {"stage_id": stage_id, "receipt": receipt_value}
    )
    completed = scheduler.record_stage_result(
        claimed, plan, stage_id, passed=True, result_receipt_sha256=receipt_hash
    )
    return completed, receipt_hash


def _fail_active_stage(
    ledger: Mapping[str, Any], plan: Mapping[str, Any], stage_id: str, code: str
) -> dict[str, Any]:
    failed_hash = scheduler.canonical_json_sha256(
        {"stage_id": stage_id, "status": "FAIL", "code": code}
    )
    return scheduler.record_stage_result(
        ledger, plan, stage_id, passed=False, result_receipt_sha256=failed_hash
    )


def run_synthetic_integration(
    *, execution_state_path: Path, manifest: Mapping[str, Any],
    scheduler_plan: Mapping[str, Any], hooks: CanaryIntegrationHooks,
) -> CanaryIntegrationResult:
    """Execute the exact control/scientific DAG once with external effects hooked."""

    state = execution_state.load_execution_state(execution_state_path)
    call_trace = ["validate_execution_state"]
    if (
        state.branch != REQUIRED_BRANCH
        or state.logical_execution_attempt != 4
        or state.attempt_004_execution_count != 1
        or state.attempt_005_exists
        or state.production_attempt_006_exists
    ):
        raise CanaryIntegrationError("CANARY_EXECUTION_STATE_INVALID")

    normalized_manifest = manifest_contract.validate_manifest(manifest)
    call_trace.append("validate_manifest")
    if scheduler_plan.get("canary_manifest_sha256") != normalized_manifest["manifest_sha256"]:
        raise CanaryIntegrationError("CANARY_MANIFEST_SCHEDULER_BINDING_MISMATCH")
    scheduler.validate_scheduler_plan(
        scheduler_plan,
        repository_root=REPOSITORY_ROOT,
        require_bound_manifest=True,
    )
    call_trace.append("validate_scheduler_plan")
    production_plan, requirements = build_canary_batch_plan(
        normalized_manifest, synthetic_authority=True
    )
    plan_sha = orchestration_core.validate_current_batch_plan_v3(
        production_plan, requirements=requirements
    )
    declared = _manifest_objects(normalized_manifest)
    context = CanaryIntegrationContext(
        manifest=normalized_manifest,
        scheduler_plan=scheduler_plan,
        artifacts={
            "batch_plan": production_plan,
            "batch_plan_sha256": plan_sha,
        },
        call_trace=call_trace,
        _declared_objects=declared,
    )
    ledger = scheduler.initialize_claim_ledger(scheduler_plan)
    predecessor_hash: str | None = None
    completed_stages: list[str] = []

    def run_stage(stage_id: str, operation: Callable[[], Any], receipt: Callable[[], Any]) -> None:
        nonlocal ledger, predecessor_hash
        predecessor = [] if predecessor_hash is None else [predecessor_hash]
        ledger = scheduler.claim_stage_submission(
            ledger, scheduler_plan, stage_id,
            predecessor_receipt_sha256s=predecessor,
        )
        try:
            operation()
            receipt_value = receipt()
        except CanaryIntegrationError as exc:
            ledger = _fail_active_stage(ledger, scheduler_plan, stage_id, exc.code)
            exc.claim_ledger = dict(ledger)
            raise
        except Exception as exc:
            ledger = _fail_active_stage(
                ledger, scheduler_plan, stage_id, "CANARY_STAGE_FAILED"
            )
            raise CanaryIntegrationError(
                "CANARY_STAGE_FAILED", claim_ledger=ledger
            ) from exc
        receipt_hash = scheduler.canonical_json_sha256(
            {"stage_id": stage_id, "receipt": receipt_value}
        )
        ledger = scheduler.record_stage_result(
            ledger,
            scheduler_plan,
            stage_id,
            passed=True,
            result_receipt_sha256=receipt_hash,
        )
        predecessor_hash = receipt_hash
        completed_stages.append(stage_id)

    run_stage(
        "DOWNLOAD",
        lambda: (hooks.source_transfer(context), hooks.integrity_verification(context)),
        lambda: {
            "declared_objects": len(declared),
            "integrity_verified": context.artifacts.get("integrity_verified") is True,
        },
    )
    run_stage(
        "DICOM_EXTRACTION",
        lambda: (hooks.dicom_audit_decode(context), hooks.cine_extraction(context)),
        lambda: {
            "dicom_rows": len(context.artifacts["dicom_rows"]),
            "extraction_rows": len(context.artifacts["extraction_rows"]),
        },
    )

    def embedding_and_pooling() -> None:
        hooks.encoder_inference(context)
        clip_array = context.artifacts["clip_embeddings"]
        clip_rows = context.artifacts["clip_rows"]
        production_stages.validate_embedding_values(
            clip_array.tolist(), expected_rows=len(clip_rows)
        )
        context.call_trace.append("validate_embedding_values")
        studies = normalized_manifest["manifest"]["studies"]
        clip_counts: dict[str, int] = {}
        for row in clip_rows:
            study_id = str(row["study_id"])
            clip_counts[study_id] = clip_counts.get(study_id, 0) + 1
        ordered = sorted(studies, key=lambda row: int(str(row["study_id"])))
        study_rows = [
            {
                "study_idx": str(index),
                "subject_id": row["subject_id"],
                "study_id": row["study_id"],
                "n_clips": str(clip_counts.get(str(row["study_id"]), 0)),
            }
            for index, row in enumerate(ordered)
        ]
        study_array = preservation.mean_pool_study_embeddings(
            clip_embeddings=clip_array,
            clip_rows=clip_rows,
            study_rows=study_rows,
        )
        context.call_trace.append("mean_pool_study_embeddings")
        clip_hashes = [_array_hash(vector) for vector in clip_array]
        study_hashes = [_array_hash(vector) for vector in study_array]
        clip_semantic_rows = [
            {**row, "embedding_sha256": clip_hashes[index]}
            for index, row in enumerate(clip_rows)
        ]
        study_semantic_rows = [
            {**row, "embedding_sha256": study_hashes[index]}
            for index, row in enumerate(study_rows)
        ]
        disposition_rows = [
            {
                "subject_id": row["subject_id"],
                "study_id": row["study_id"],
                "disposition": "IMAGING_ELIGIBLE",
            }
            for row in ordered
        ]
        pooling = preservation.validate_study_pooling_records(
            clip_rows=clip_semantic_rows,
            study_rows=study_semantic_rows,
            disposition_rows=disposition_rows,
            planned_studies=ordered,
            clip_vector_hashes=clip_hashes,
            study_vector_hashes=study_hashes,
        )
        context.call_trace.append("validate_study_pooling_records")
        context.artifacts.update(
            {
                "clip_rows": clip_semantic_rows,
                "study_embeddings": study_array,
                "study_rows": study_semantic_rows,
                "pooling_validation": pooling,
            }
        )

    run_stage(
        "ECHOPRIME_EMBEDDING",
        embedding_and_pooling,
        lambda: {
            "clip_embeddings": len(context.artifacts["clip_embeddings"]),
            "study_embeddings": len(context.artifacts["study_embeddings"]),
            "all_finite": True,
        },
    )
    run_stage(
        "BATCH_PRESERVATION",
        lambda: hooks.preservation(context),
        lambda: context.artifacts["preservation_receipt"],
    )

    def finalize_canary() -> None:
        context.artifacts["aggregate_summary"] = (
            finalizer.finalize_canary_preservation_receipt(
                context.artifacts["preservation_receipt"],
                expected_governing_commit=normalized_manifest["manifest"][
                    "source_authority_commit"
                ],
                expected_attempt_id=SYNTHETIC_ATTEMPT_ID,
                expected_canary_manifest_sha256=normalized_manifest[
                    "manifest_sha256"
                ],
                expected_batch_plan_sha256=plan_sha,
                expected_scheduler_plan_sha256=scheduler.canonical_json_sha256(
                    scheduler_plan
                ),
                expected_object_count=int(
                    normalized_manifest["manifest"]["expected_object_count"]
                ),
                expected_source_bytes=int(
                    normalized_manifest["manifest"]["expected_byte_total"]
                ),
            )
        )
        context.call_trace.append("finalize_canary_preservation_receipt")

    run_stage(
        "CANARY_FINALIZATION",
        finalize_canary,
        lambda: context.artifacts["aggregate_summary"],
    )
    if (
        ledger["status"] != "COMPLETE"
        or ledger["submission_count"] != 5
        or ledger["production_continuation_triggered"] is not False
    ):
        raise CanaryIntegrationError(
            "CANARY_SCHEDULER_TERMINAL_STATE_INVALID", claim_ledger=ledger
        )
    return CanaryIntegrationResult(
        claim_ledger=ledger,
        completed_stages=tuple(completed_stages),
        study_embeddings=context.artifacts["study_embeddings"],
        aggregate_summary=context.artifacts["aggregate_summary"],
        production_continuation=False,
        reachable_roles=frozenset(completed_stages),
        call_trace=tuple(context.call_trace),
    )


def _run_git(
    arguments: Sequence[str], *, repository_root: Path = REPOSITORY_ROOT
) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=repository_root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )
    if result.returncode != 0:
        raise CanaryControlError("CANARY_GIT_AUTHORITY_INVALID")
    return result.stdout.strip()


def _private_authority_roots_present(config: CanaryPrivateAuthorityConfig) -> bool:
    recovery = config.recovery
    return any(
        os.path.lexists(path)
        for path in (recovery.worktree, recovery.audit_root, recovery.production_root)
    )


def _private_literal(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise CanaryControlError("CANARY_PRIVATE_AUTHORITY_LITERAL_MISSING")
    return value


def _private_positive_int(values: Mapping[str, str], name: str) -> int:
    raw = _private_literal(values, name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise CanaryControlError("CANARY_PRIVATE_AUTHORITY_SIZE_INVALID") from exc
    if value < 1 or str(value) != raw:
        raise CanaryControlError("CANARY_PRIVATE_AUTHORITY_SIZE_INVALID")
    return value


def _private_sha256(values: Mapping[str, str], name: str) -> str:
    value = _private_literal(values, name)
    if SHA256_RE.fullmatch(value) is None:
        raise CanaryControlError("CANARY_PRIVATE_AUTHORITY_HASH_INVALID")
    return value


def _packet_binding(packet: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    authority = packet.get("authority")
    binding = authority.get(role) if isinstance(authority, Mapping) else None
    if (
        not isinstance(binding, Mapping)
        or set(binding) != {"size_bytes", "sha256"}
        or isinstance(binding.get("size_bytes"), bool)
        or not isinstance(binding.get("size_bytes"), int)
        or binding["size_bytes"] < 1
        or SHA256_RE.fullmatch(str(binding.get("sha256"))) is None
    ):
        raise CanaryControlError("CANARY_PRIVATE_PACKET_BINDING_INVALID")
    return binding


def _validate_row_authority_without_body_access(
    *, values: Mapping[str, str], packet: Mapping[str, Any], role: str,
    path_name: str, size_name: str, hash_name: str,
    frozen_sha256: str | None,
) -> None:
    """Validate row authority metadata and detached hash bindings, never bytes."""

    path = Path(_private_literal(values, path_name))
    expected_size = _private_positive_int(values, size_name)
    expected_sha256 = _private_sha256(values, hash_name)
    binding = _packet_binding(packet, role)
    if (
        binding["size_bytes"] != expected_size
        or binding["sha256"] != expected_sha256
        or (frozen_sha256 is not None and expected_sha256 != frozen_sha256)
    ):
        raise CanaryControlError("CANARY_PRIVATE_ROW_BINDING_MISMATCH")
    # Deliberately omit ``digest``. Opening or hashing these row-bearing files
    # is outside preflight authorization; the immutable packet carries their
    # already-sealed hashes while lstat proves current type/mode/size.
    phase1eg_authority.require_regular_file(
        path, owner_only=False, size=expected_size
    )


def _pass_private_authority_summary() -> dict[str, Any]:
    return {
        "status": "PASS_SCC_PRIVATE_AUTHORITY_READ_ONLY",
        "scc_private_authority_required": True,
        "attempt_capacity_seals_valid": True,
        "preserved_preparation_valid": True,
        "current_environment_receipt_valid": True,
        "checkpoint_binding_valid": True,
        "gcloud_binding_valid": True,
        "requester_binding_valid": True,
        "source_authority_stat_binding_valid": True,
        "restricted_row_bodies_read": 0,
        "cloud_requests": 0,
        "qsub_submissions": 0,
    }


def validate_scc_private_authority(
    config: CanaryPrivateAuthorityConfig = CanaryPrivateAuthorityConfig(),
) -> dict[str, Any]:
    """Validate owner-private SCC authority without reading cohort row bodies."""

    if not _private_authority_roots_present(config):
        return {
            "status": "UNAVAILABLE_LOCAL_NON_SCC",
            "scc_private_authority_required": False,
            "attempt_capacity_seals_valid": False,
            "preserved_preparation_valid": False,
            "current_environment_receipt_valid": False,
            "checkpoint_binding_valid": False,
            "gcloud_binding_valid": False,
            "requester_binding_valid": False,
            "source_authority_stat_binding_valid": False,
            "restricted_row_bodies_read": 0,
            "cloud_requests": 0,
            "qsub_submissions": 0,
        }
    if (
        re.fullmatch(r"[0-9a-f]{40}", config.current_environment_commit) is None
        or config.current_environment_bytes < 1
        or SHA256_RE.fullmatch(config.current_environment_sha256) is None
    ):
        raise CanaryControlError("CANARY_CURRENT_ENVIRONMENT_BINDING_INVALID")

    try:
        state = phase1eg_authority.load_canonical_state(config.recovery)
        if not state.permits("preflight_only"):
            raise CanaryControlError("CANARY_PREFLIGHT_SCOPE_NOT_PERMITTED")
        paths = phase1eg_authority.derive_execution_paths(config.recovery, state)
        phase1eg_authority.validate_attempts_and_capacity(state, paths)
        preparation_root, values = phase1eg_authority.discover_preparation(
            config.recovery, state
        )
        phase1eg_authority.validate_preparation_binding(
            config.recovery, state, preparation_root, values
        )
        private = phase1eg_authority.resolve_private_authorities(
            config.recovery,
            state,
            paths,
            config.current_environment_commit,
        )
        receipt_bytes, receipt_sha256 = phase1eg_authority.validate_current_receipt(
            private.output_receipt,
            config.current_environment_commit,
            prior_environment_sha256=private.prior_environment_sha256,
        )
        if (
            receipt_bytes != config.current_environment_bytes
            or receipt_sha256 != config.current_environment_sha256
        ):
            raise CanaryControlError("CANARY_CURRENT_ENVIRONMENT_BINDING_MISMATCH")

        packet_path = Path(_private_literal(values, "PRIOR_PRODUCTION_PACKET"))
        packet_size = _private_positive_int(
            values, "PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE"
        )
        packet_sha256 = _private_sha256(
            values, "PRIOR_PRODUCTION_PACKET_EXPECTED_SHA"
        )
        if (
            packet_size != PHASE1EG_PRODUCTION_PACKET_BYTES
            or packet_sha256 != PHASE1EG_PRODUCTION_PACKET_SHA256
        ):
            raise CanaryControlError("CANARY_PRIVATE_PACKET_IDENTITY_MISMATCH")
        phase1eg_authority.require_regular_file(
            packet_path,
            owner_only=True,
            size=packet_size,
            digest=packet_sha256,
        )
        packet = phase1eg_authority.load_strict_json(packet_path)
        production_authority_packet.validate_packet(packet)
        if packet.get("attempt_id") != state.prior_production_attempt_id:
            raise CanaryControlError("CANARY_PRIVATE_PACKET_ATTEMPT_MISMATCH")

        checkpoint = Path(_private_literal(values, "CHECKPOINT"))
        checkpoint_size = _private_positive_int(values, "CHECKPOINT_EXPECTED_SIZE")
        checkpoint_sha256 = _private_sha256(values, "CHECKPOINT_EXPECTED_SHA")
        checkpoint_binding = _packet_binding(packet, "checkpoint")
        if (
            checkpoint_binding["size_bytes"] != checkpoint_size
            or checkpoint_binding["sha256"] != checkpoint_sha256
            or checkpoint_sha256 != orchestration_core.EXPECTED_CHECKPOINT_SHA256
        ):
            raise CanaryControlError("CANARY_CHECKPOINT_BINDING_MISMATCH")
        phase1eg_authority.require_regular_file(
            checkpoint,
            owner_only=False,
            size=checkpoint_size,
            digest=checkpoint_sha256,
        )

        row_authorities = (
            (
                "selected_study_manifest", "SELECTED_STUDIES",
                "SELECTED_STUDIES_EXPECTED_SIZE", "SELECTED_STUDIES_EXPECTED_SHA",
                orchestration_core.EXPECTED_SELECTED_MANIFEST_SHA256,
            ),
            (
                "selected_source_manifest", "SELECTED_SOURCE",
                "SELECTED_SOURCE_EXPECTED_SIZE", "SELECTED_SOURCE_EXPECTED_SHA",
                orchestration_core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256,
            ),
            (
                "selected_source_metadata_receipt", "SOURCE_METADATA",
                "SOURCE_METADATA_EXPECTED_SIZE", "SOURCE_METADATA_EXPECTED_SHA", None,
            ),
            (
                "split_map", "SPLIT_MAP", "SPLIT_MAP_EXPECTED_SIZE",
                "SPLIT_MAP_EXPECTED_SHA", orchestration_core.EXPECTED_SPLIT_MAP_SHA256,
            ),
        )
        for role, path_name, size_name, hash_name, frozen_sha256 in row_authorities:
            _validate_row_authority_without_body_access(
                values=values,
                packet=packet,
                role=role,
                path_name=path_name,
                size_name=size_name,
                hash_name=hash_name,
                frozen_sha256=frozen_sha256,
            )

        gcloud = Path(_private_literal(values, "GCLOUD"))
        gcloud_receipt = Path(_private_literal(values, "GCLOUD_RECEIPT"))
        gcloud_binding = _packet_binding(packet, "gcloud_executable")
        receipt_binding = _packet_binding(packet, "gcloud_resolution_receipt")
        if receipt_binding != _packet_binding(packet, "cloudsdk_config_receipt"):
            raise CanaryControlError("CANARY_GCLOUD_RECEIPT_BINDING_MISMATCH")
        phase1eg_authority.require_regular_file(
            gcloud,
            owner_only=False,
            executable=True,
            size=int(gcloud_binding["size_bytes"]),
            digest=str(gcloud_binding["sha256"]),
            owner_required=False,
        )
        phase1eg_authority.require_regular_file(
            gcloud_receipt,
            owner_only=True,
            size=int(receipt_binding["size_bytes"]),
            digest=str(receipt_binding["sha256"]),
        )
        production_authority_packet._validate_gcloud_resolution_authority(
            gcloud_receipt, gcloud
        )
        provider = orchestration_core.GcloudADCTokenProvider(
            gcloud,
            cloudsdk_config=Path(_private_literal(values, "CLOUDSDK_CONFIG")),
            authority_receipt=gcloud_receipt,
            authority_receipt_sha256=str(receipt_binding["sha256"]),
        )
        observed_gcloud = provider.validate_authority()
        if observed_gcloud != {
            "gcloud_resolution_receipt_sha256": receipt_binding["sha256"],
            "gcloud_executable_sha256": gcloud_binding["sha256"],
        }:
            raise CanaryControlError("CANARY_GCLOUD_RUNTIME_BINDING_MISMATCH")

        billing_project = _private_literal(values, "LVEF_C3_GCP_BILLING_PROJECT")
        if re.fullmatch(r"[a-z][a-z0-9-]{4,62}[a-z0-9]", billing_project) is None:
            raise CanaryControlError("CANARY_PRIVATE_REQUESTER_BINDING_INVALID")
        requester = orchestration_core.validate_private_billing_environment(
            "LVEF_C3_GCP_BILLING_PROJECT",
            argv=(),
            environ={"LVEF_C3_GCP_BILLING_PROJECT": billing_project},
        )
        if requester.get("value_present") is not True or requester.get(
            "value_returned"
        ) is not False:
            raise CanaryControlError("CANARY_PRIVATE_REQUESTER_BINDING_INVALID")
    except CanaryControlError:
        raise
    except (
        phase1eg_authority.D3RecoveryError,
        production_authority_packet.AuthorityPacketError,
        orchestration_core.OrchestrationError,
        orchestration_core.DownloadTransportError,
        OSError,
        ValueError,
    ) as exc:
        raise CanaryControlError("CANARY_SCC_PRIVATE_AUTHORITY_INVALID") from exc
    return _pass_private_authority_summary()


def validate_installation(
    runtime: CanaryControlRuntime | None = None,
) -> dict[str, Any]:
    """Validate only tracked code/config/state and local Git authority."""

    control = runtime or CanaryControlRuntime()
    repository_root = Path(control.repository_root)
    state = execution_state.load_execution_state(control.execution_state_path)

    def git(arguments: Sequence[str]) -> str:
        # Preserve the one-argument seam used by older dependency-light tests.
        return (
            _run_git(arguments)
            if runtime is None
            else _run_git(arguments, repository_root=repository_root)
        )

    if state.branch != REQUIRED_BRANCH or git(("branch", "--show-current")) != state.branch:
        raise CanaryControlError("CANARY_BRANCH_AUTHORITY_INVALID")
    head = git(("rev-parse", "HEAD"))
    remote = git(("rev-parse", f"refs/remotes/origin/{state.branch}"))
    if head != remote or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise CanaryControlError("CANARY_LOCAL_ORIGIN_COMMIT_MISMATCH")
    git(("merge-base", "--is-ancestor", state.starting_authority_commit, head))
    git(
        (
            "merge-base",
            "--is-ancestor",
            PHASE1HR1_STARTING_AUTHORITY_COMMIT,
            head,
        )
    )
    git(
        (
            "merge-base",
            "--is-ancestor",
            PHASE1HR2_STARTING_AUTHORITY_COMMIT,
            head,
        )
    )
    status = git(("status", "--porcelain=v1", "--untracked-files=all"))
    allowed = {"?? .DS_Store", "?? docs/.DS_Store"}
    if any(line not in allowed for line in status.splitlines() if line):
        raise CanaryControlError("CANARY_TRACKED_WORKTREE_DIRTY")
    untracked_scripts = git(("ls-files", "--others", "--", "scripts"))
    if any(
        line and not line.startswith("scripts/__pycache__/")
        for line in untracked_scripts.splitlines()
    ):
        raise CanaryControlError("CANARY_UNTRACKED_IMPORT_PATH_PRESENT")
    tracked_files = (
        TRACKED_CANARY_CONTROL_FILES
        if runtime is None
        else tuple(repository_root / value for value in TRACKED_CONTROL_RELATIVE_FILES)
    )
    for path in tracked_files:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise CanaryControlError("CANARY_TRACKED_CONTROL_FILE_MISSING") from exc
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or metadata.st_mode & 0o022
            or (path.suffix == ".sh" and not metadata.st_mode & 0o100)
        ):
            raise CanaryControlError("CANARY_TRACKED_CONTROL_FILE_INVALID")
    for schema_path in (
        repository_root / "configs/lvef_c3_canary_manifest_schema_v1.json",
        repository_root
        / "configs/lvef_c3_canary_execution_authority_schema_v1.json",
        repository_root
        / "configs/lvef_c3_canary_preselection_authority_schema_v1.json",
        repository_root / "configs/lvef_c3_canary_state_snapshot_schema_v1.json",
    ):
        try:
            schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CanaryControlError("CANARY_TRACKED_SCHEMA_INVALID") from exc
        if (
            not isinstance(schema_value, Mapping)
            or schema_value.get("additionalProperties") is not False
        ):
            raise CanaryControlError("CANARY_TRACKED_SCHEMA_NOT_CLOSED")
    contract = orchestration_core.load_orchestration_contract(
        control.orchestration_contract_path
    )
    if contract["cohort"]["release"] != manifest_contract.SOURCE_RELEASE:
        raise CanaryControlError("CANARY_PRODUCTION_RELEASE_MISMATCH")
    scheduler.validate_scheduler_plan(
        scheduler.load_scheduler_plan(control.scheduler_plan_path),
        repository_root=repository_root,
    )
    required_callables = {
        scheduler.EXPECTED_ENTRYPOINTS[stage_id][1]
        for stage_id in scheduler.ORDERED_STAGE_IDS
    }
    if not required_callables.issubset(
        {function.__name__ for function in PRODUCTION_FUNCTIONS.values()}
    ):
        raise CanaryControlError("CANARY_PRODUCTION_CALLABLE_MISSING")
    return {
        "status": "PASS_INSTALLATION_VALIDATION_NO_LIVE_OPERATIONS",
        "governing_commit": head,
        "frozen_scheduler_submissions": 5,
        "gpu_stages": 1,
        "production_continuation": False,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "restricted_rows_accessed": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
        "tracked_control_envelope_valid": True,
    }


def _synthetic_preflight_manifest(
    governing_commit: str,
    *, execution_state_path: Path = DEFAULT_EXECUTION_STATE,
    orchestration_contract_path: Path = DEFAULT_ORCHESTRATION_CONTRACT,
) -> dict[str, Any]:
    candidates = [
        {
            "study_id": str(900000 + index),
            "subject_id": str(800000 + index),
            "split": "train",
            "expected_object_count": 1,
            "expected_byte_total": 1,
            "known_no_cine": False,
            "prior_reconstruction_smoke": False,
        }
        for index in range(1, 6)
    ]
    selected = manifest_contract.select_exact_five(candidates)
    objects = []
    for index, study in enumerate(selected, start=1):
        relative = (
            f"files/p{int(study.subject_id) // 1_000_000:02d}/p{study.subject_id}/"
            f"s{study.study_id}/synthetic_{index:03d}.dcm"
        )
        objects.append(
            {
                "subject_id": study.subject_id,
                "study_id": study.study_id,
                "split": "train",
                "source_object_key": hashlib.sha256(
                    f"{manifest_contract.SOURCE_RELEASE}\0{relative}".encode()
                ).hexdigest(),
                "source_relative_path": relative,
                "size_bytes": 1,
                "generation": str(index),
                "md5_base64": "AAAAAAAAAAAAAAAAAAAAAA==",
                "crc32c_base64": "AAAAAA==",
            }
        )
    return manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=objects,
        source_authority_commit=governing_commit,
        source_manifest_sha256="0" * 64,
        source_configuration_hashes={
            "execution_state": hashlib.sha256(execution_state_path.read_bytes()).hexdigest(),
            "production_contract": hashlib.sha256(
                orchestration_contract_path.read_bytes()
            ).hexdigest(),
        },
    )


def preflight_only(
    runtime: CanaryControlRuntime | None = None,
) -> dict[str, Any]:
    """Prove the complete future control path in a disposable synthetic root."""

    control = runtime or CanaryControlRuntime()
    state = execution_state.load_execution_state(control.execution_state_path)
    if not state.permits("preflight_only"):
        raise CanaryControlError("CANARY_PREFLIGHT_SCOPE_NOT_PERMITTED")
    installation = validate_installation(runtime)
    private_authority = (
        {
            "status": "PASS_SYNTHETIC_PRIVATE_AUTHORITY_NOT_REQUIRED",
            "scc_private_authority_required": False,
        }
        if control.synthetic_external_effects
        else validate_scc_private_authority(control.private_authority_config)
    )

    # Importing both producers here is itself part of the strengthened proof.
    import lvef_c3_canary_authority_materializer as materializer
    import lvef_c3_canary_state as canary_state
    import lvef_c3_canary_execution_authority as execution_authority
    import lvef_c3_canary_dispatch as canary_dispatch

    governing_commit = str(installation["governing_commit"])
    if control.materialization_authority_source is not None:
        authority_source = control.materialization_authority_source
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="lvef_c3_canary_preflight_")
        sandbox_root = Path(cleanup.name).resolve()
        authority_source = materializer.build_synthetic_materialization_authority_source(
            sandbox_root,
            repository=Path(control.repository_root),
            governing_commit=governing_commit,
        )
    try:
        materialization_config = materializer.discover_live_materialization_config(
            repository=Path(control.repository_root),
            execution_state_path=Path(control.execution_state_path),
            authority_source=authority_source,
        )
        prepared = materializer.prepare_live_authority(materialization_config)
        authority_paths = prepared.authority_paths
        authority = execution_authority.load_and_validate_execution_authority(
            prepared.authority_path,
            expected_governing_commit=governing_commit,
            require_output_absent=True,
            paths=authority_paths,
        )
        tracked = execution_state.load_execution_state(control.execution_state_path)
        private_state = canary_state.load_state(
            root=prepared.lifecycle_root,
            execution_state_path=control.execution_state_path,
            expected_governing_commit=governing_commit,
            expected_run_id=prepared.run_id,
        )
        canary_state.assert_execute_permitted(
            tracked,
            private_state,
            governing_commit=governing_commit,
            run_id=prepared.run_id,
        )
        job_counter = 0

        def synthetic_submitter(command: Sequence[str]) -> str:
            nonlocal job_counter
            if not command or Path(command[0]) != Path(authority["qsub"]["path"]):
                raise CanaryControlError("CANARY_SYNTHETIC_QSUB_BOUNDARY_INVALID")
            job_counter += 1
            return str(91_000 + job_counter)

        execute_result = execute_authorized_canary(
            CanaryControlRuntime(
                repository_root=Path(control.repository_root),
                execution_state_path=Path(control.execution_state_path),
                orchestration_contract_path=Path(
                    control.orchestration_contract_path
                ),
                scheduler_plan_path=Path(control.scheduler_plan_path),
                lifecycle_root=prepared.lifecycle_root,
                authority_path=prepared.authority_path,
                authority_paths=authority_paths,
                private_authority_config=control.private_authority_config,
                qsub_submitter=synthetic_submitter,
                synthetic_external_effects=True,
            )
        )
    finally:
        if cleanup is not None:
            cleanup.cleanup()

    if (
        prepared.lifecycle_state != "CANARY_MANIFEST_SEALED"
        or prepared.preselection_identifier_fields != 0
        or execute_result.get("status")
        != "PASS_OWNER_AUTHORIZED_FROZEN_DAG_DISPATCH"
        or execute_result.get("frozen_scheduler_submissions") != 5
        or execute_result.get("production_continuation") is not False
    ):
        raise CanaryControlError("CANARY_SYNTHETIC_LIVE_PATH_INVALID")

    manifest = _synthetic_preflight_manifest(
        governing_commit,
        execution_state_path=control.execution_state_path,
        orchestration_contract_path=control.orchestration_contract_path,
    )
    bound = scheduler.bind_scheduler_plan(
        scheduler.load_scheduler_plan(control.scheduler_plan_path),
        manifest["manifest_sha256"],
        repository_root=control.repository_root,
    )
    plan, requirements = build_canary_batch_plan(
        manifest, synthetic_authority=True
    )
    plan_sha = orchestration_core.validate_current_batch_plan_v3(
        plan, requirements=requirements
    )
    aggregate = orchestration_core.aggregate_batch_plan(plan, requirements=requirements)
    if (
        aggregate["selected_studies"] != 5
        or aggregate["normalized_source_objects"] != 5
        or aggregate["selected_source_bytes"] != 5
        or bound["scheduler_submission_count"] != 5
    ):
        raise CanaryControlError("CANARY_SYNTHETIC_PREFLIGHT_SCOPE_INVALID")
    return {
        "status": "PASS_SYNTHETIC_PREFLIGHT_NO_LIVE_OPERATIONS",
        "governing_commit": installation["governing_commit"],
        "synthetic_studies": 5,
        "synthetic_objects": 5,
        "synthetic_expected_bytes": 5,
        "batch_plan_sha256": plan_sha,
        "frozen_scheduler_submissions": 5,
        "gpu_stages": 1,
        "private_authority_validation": private_authority["status"],
        "scc_private_authority_required": private_authority[
            "scc_private_authority_required"
        ],
        "production_continuation": False,
        "live_canary_root_created": False,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "restricted_rows_accessed": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
        "tracked_state_transition_producer_valid": True,
        "tracked_materializer_valid": True,
        "synthetic_live_execute_path_accepted": True,
        "synthetic_qsub_adapter_calls": 5,
    }


def prepare_live_authority(
    runtime: CanaryControlRuntime | None = None,
) -> dict[str, Any]:
    """Materialize one sealed owner-private authority without live effects."""

    control = runtime or CanaryControlRuntime()
    tracked = execution_state.load_execution_state(control.execution_state_path)
    if not (
        tracked.permits("prepare_exact_five_canary_authority")
        and tracked.permits("seal_exact_five_canary_manifest")
    ):
        raise CanaryControlError("CANARY_AUTHORITY_PREPARATION_SCOPE_DENIED")
    installation = validate_installation(runtime)
    if not control.synthetic_external_effects:
        private = validate_scc_private_authority(control.private_authority_config)
        if private.get("status") != "PASS_SCC_PRIVATE_AUTHORITY_READ_ONLY":
            raise CanaryControlError("REAL_CANARY_SCC_PRIVATE_AUTHORITY_REQUIRED")
    import lvef_c3_canary_authority_materializer as materializer

    try:
        config = materializer.discover_live_materialization_config(
            repository=Path(control.repository_root),
            execution_state_path=Path(control.execution_state_path),
            private_authority_config=(
                control.private_authority_config
                if control.materialization_authority_source is None
                else None
            ),
            authority_source=control.materialization_authority_source,
        )
        result = materializer.prepare_live_authority(config)
    except materializer.CanaryAuthorityMaterializationError as exc:
        raise CanaryControlError(exc.code) from exc
    if (
        result.lifecycle_state != "CANARY_MANIFEST_SEALED"
        or result.preselection_identifier_fields != 0
    ):
        raise CanaryControlError("CANARY_AUTHORITY_MATERIALIZATION_INVALID")
    return {
        "status": "PASS_OWNER_PRIVATE_CANARY_AUTHORITY_MATERIALIZED",
        "governing_commit": installation["governing_commit"],
        "lifecycle_state": result.lifecycle_state,
        "preselection_identifier_fields": 0,
        "manifest_studies": 5,
        "frozen_scheduler_submissions": 5,
        "production_continuation": False,
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "restricted_identifiers_emitted": False,
        "private_paths_emitted": False,
    }


def execute_authorized_canary(
    runtime: CanaryControlRuntime | None = None,
) -> dict[str, Any]:
    """Dispatch the sealed five-stage DAG after lifecycle and packet gates."""

    control = runtime or CanaryControlRuntime()
    state = execution_state.load_execution_state(control.execution_state_path)
    installation = validate_installation(runtime)

    import lvef_c3_canary_state as canary_state
    import lvef_c3_canary_execution_authority as execution_authority
    import lvef_c3_canary_dispatch as canary_dispatch

    lifecycle_root = (
        Path(state.canary_lifecycle.private_state_root)
        if control.lifecycle_root is None
        else Path(control.lifecycle_root)
    )
    try:
        private_state = canary_state.load_state(
            root=lifecycle_root,
            execution_state_path=control.execution_state_path,
            expected_governing_commit=str(installation["governing_commit"]),
        )
        canary_state.assert_execute_permitted(
            state,
            private_state,
            governing_commit=str(installation["governing_commit"]),
            run_id=str(private_state["run_id"]),
        )
    except canary_state.CanaryStateError as exc:
        raise CanaryControlError(
            "REAL_CANARY_CANONICAL_STATE_AUTHORIZATION_REQUIRED"
        ) from exc
    if private_state.get("current_state") != "CANARY_MANIFEST_SEALED":
        raise CanaryControlError("REAL_CANARY_STATE_NOT_SEALED_FOR_DISPATCH")

    private_authority = (
        {
            "status": "PASS_SYNTHETIC_PRIVATE_AUTHORITY_NOT_REQUIRED",
            "scc_private_authority_required": False,
        }
        if control.synthetic_external_effects
        else validate_scc_private_authority(control.private_authority_config)
    )
    if (
        not control.synthetic_external_effects
        and (
            private_authority.get("status")
            != "PASS_SCC_PRIVATE_AUTHORITY_READ_ONLY"
            or private_authority.get("scc_private_authority_required") is not True
        )
    ):
        raise CanaryControlError("REAL_CANARY_SCC_PRIVATE_AUTHORITY_REQUIRED")
    authority_path = (
        execution_authority.FIXED_PATH
        if control.authority_path is None
        else Path(control.authority_path)
    )
    try:
        authority = execution_authority.load_and_validate_execution_authority(
            authority_path,
            expected_governing_commit=str(installation["governing_commit"]),
            require_output_absent=True,
            paths=control.authority_paths,
        )
        if authority.get("run_id") != private_state.get("run_id"):
            raise CanaryControlError("CANARY_STATE_PACKET_IDENTITY_MISMATCH")
        canary_state.transition_state(
            root=lifecycle_root,
            execution_state_path=control.execution_state_path,
            expected_current="CANARY_MANIFEST_SEALED",
            target_state="CANARY_EXECUTING",
            governing_commit=str(installation["governing_commit"]),
            run_id=str(authority["run_id"]),
            reason_code="OWNER_AUTHORIZED_FROZEN_DAG_DISPATCH",
            bindings={
                "authorization_sha256": str(authority["authorization_sha256"]),
                "manifest_sha256": str(authority["manifest"]["embedded_sha256"]),
                "scheduler_plan_sha256": str(
                    authority["scheduler_plan"]["canonical_sha256"]
                ),
            },
        )
        ledger = canary_dispatch.dispatch_authorized_canary(
            authority,
            **(
                {}
                if control.qsub_submitter is None
                else {"submitter": control.qsub_submitter}
            ),
        )
    except (
        execution_authority.CanaryExecutionAuthorityError,
        canary_dispatch.CanaryDispatchError,
        canary_state.CanaryStateError,
        OSError,
    ) as exc:
        try:
            latest = canary_state.load_state(
                root=lifecycle_root,
                execution_state_path=control.execution_state_path,
                expected_governing_commit=str(installation["governing_commit"]),
                expected_run_id=str(private_state["run_id"]),
            )
            if latest.get("current_state") == "CANARY_EXECUTING":
                canary_state.transition_state(
                    root=lifecycle_root,
                    execution_state_path=control.execution_state_path,
                    expected_current="CANARY_EXECUTING",
                    target_state="CANARY_TERMINAL_FAIL",
                    governing_commit=str(installation["governing_commit"]),
                    run_id=str(private_state["run_id"]),
                    reason_code="CANARY_DISPATCH_FAILED_NO_RETRY",
                )
        except canary_state.CanaryStateError:
            pass
        raise CanaryControlError(
            getattr(exc, "code", "CANARY_DISPATCH_FILESYSTEM_FAILED")
        ) from exc
    if (
        ledger.get("status") != "DISPATCHED_FROZEN_DAG"
        or ledger.get("submission_count") != 5
        or ledger.get("production_continuation_triggered") is not False
    ):
        raise CanaryControlError("CANARY_DISPATCH_TERMINAL_STATE_INVALID")
    return {
        "status": "PASS_OWNER_AUTHORIZED_FROZEN_DAG_DISPATCH",
        "governing_commit": installation["governing_commit"],
        "frozen_scheduler_submissions": 5,
        "gpu_stages": 1,
        "production_continuation": False,
        "qsub_submissions": 5,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-installation", action="store_true")
    modes.add_argument("--prepare-live-authority", action="store_true")
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *, runtime: CanaryControlRuntime | None = None,
) -> int:
    args = parse_args(argv)
    try:
        if args.execute:
            result = execute_authorized_canary(runtime)
        elif args.prepare_live_authority:
            result = prepare_live_authority(runtime)
        else:
            result = (
                validate_installation(runtime)
                if args.validate_installation
                else preflight_only(runtime)
            )
    except (
        CanaryControlError,
        execution_state.ExecutionStateError,
        manifest_contract.CanaryManifestError,
        scheduler.CanarySchedulerPlanError,
        orchestration_core.OrchestrationError,
    ) as exc:
        code = getattr(exc, "code", "CANARY_CONTROL_BLOCKED")
        print(f"LVEF_C3_CANARY_CONTROL=BLOCKED_{code}")
        before_dispatch = code in {
            "REAL_CANARY_CANONICAL_STATE_AUTHORIZATION_REQUIRED",
            "REAL_CANARY_SCC_PRIVATE_AUTHORITY_REQUIRED",
        }
        if not args.execute or before_dispatch:
            print("CLOUD_REQUESTS=0")
            print("QSUB_SUBMISSIONS=0")
        else:
            print("CLOUD_REQUESTS=NOT_ATTESTED")
            print("QSUB_SUBMISSIONS=NOT_ATTESTED")
        return 78
    except Exception:
        print("LVEF_C3_CANARY_CONTROL=BLOCKED_SANITIZED_UNEXPECTED_EXCEPTION")
        if args.execute:
            print("CLOUD_REQUESTS=NOT_ATTESTED")
            print("QSUB_SUBMISSIONS=NOT_ATTESTED")
        else:
            print("CLOUD_REQUESTS=0")
            print("QSUB_SUBMISSIONS=0")
        return 78
    print("LVEF_C3_CANARY_CONTROL=PASS")
    for key, value in result.items():
        if key == "governing_commit":
            print(f"GOVERNING_COMMIT={value}")
        elif isinstance(value, bool):
            print(f"{key.upper()}={'YES' if value else 'NO'}")
        else:
            print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
