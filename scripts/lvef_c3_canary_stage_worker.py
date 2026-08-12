#!/usr/bin/env python3
"""Run exactly one owner-authorized exact-five canary stage.

This is a thin scope envelope around the tracked production implementations.
It does not submit scheduler jobs and it never infers authorization from the
presence of an artifact.  The CLI consumes one separately validated, closed
execution-authority packet and one of the five frozen stage identifiers.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import finalize_lvef_c3_production as finalizer
import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler_contract
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation


STAGE_IDS = scheduler_contract.ORDERED_STAGE_IDS
BATCH_ID = "c3_batch_000"
CONTRACT_ID = "lvef_multitask_c3_exact_five_canary_v1"
PRODUCTION_ROOT = Path("/restricted/projectnb/mimicecho/lvef_multitask_c3_v2")
OWNER_PRIVATE_ROOT = PRODUCTION_ROOT / "owner_private" / "exact_five_canary"
CANARY_RUN_ROOT = OWNER_PRIVATE_ROOT / "canary_runs"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^lvef_c3_exact_five_canary_[a-z0-9]{8}$")


class CanaryStageWorkerError(RuntimeError):
    """Fail-closed worker error carrying only an aggregate-safe code."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_STAGE_WORKER_INVALID"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CanaryStageContext:
    authorization_sha256: str
    authorization_file_sha256: str
    governing_commit: str
    run_id: str
    attempt_id: str
    output_root: Path
    manifest_path: Path
    manifest_file_sha256: str
    manifest_sha256: str
    batch_plan_path: Path
    batch_plan_file_sha256: str
    batch_plan_sha256: str
    scheduler_plan_path: Path
    scheduler_plan_file_sha256: str
    scheduler_plan_sha256: str
    contract_path: Path
    contract_sha256: str
    environment_receipt: Path
    environment_receipt_sha256: str
    checkpoint: Path
    checkpoint_sha256: str
    runtime_authority: Mapping[str, str]
    stage_authorization_path: Path
    stage_authorization_file_sha256: str
    body_transfer_authorization_path: Path
    body_transfer_authorization_file_sha256: str
    launch_authority_sha256: str
    gcloud_binary: Path
    gcloud_resolution_receipt: Path
    crc32c_python: Path
    crc32c_worker: Path
    cloudsdk_config: Path
    billing_environment_variable: str
    billing_project: str


@dataclass(frozen=True)
class CanaryStageDependencies:
    """Explicit external-effect adapters; production defaults are live-only."""

    download: Callable[..., Mapping[str, Any]] = core.execute_exact_batch_download
    dicom: Callable[..., Mapping[str, Any]] = stages.run_production_dicom_extraction
    echoprime: Callable[..., Mapping[str, Any]] = stages.run_production_echoprime
    preserve: Callable[..., Mapping[str, Any]] = preservation.preserve_batch
    finalize: Callable[..., Mapping[str, Any]] = finalizer.finalize_canary_preservation_receipt
    token_provider_factory: Callable[..., Any] = core.GcloudADCTokenProvider
    transport_factory: Callable[[], Any] = core.GCSExactObjectBodyTransport
    digest_worker_factory: Callable[..., Any] = core.ExternalCRC32CDigestWorker


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanaryStageWorkerError(code)
    return value


def _keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise CanaryStageWorkerError(code)


def _sha(value: Any, code: str) -> str:
    text = str(value)
    if SHA256_RE.fullmatch(text) is None:
        raise CanaryStageWorkerError(code)
    return text


def _binding(
    value: Any, *, extras: frozenset[str] = frozenset()
) -> Mapping[str, Any]:
    item = _mapping(value, "CANARY_EXECUTION_ARTIFACT_BINDING_INVALID")
    _keys(item, {"path", "file_sha256", *extras}, "CANARY_EXECUTION_ARTIFACT_BINDING_INVALID")
    if not isinstance(item.get("path"), (str, Path)):
        raise CanaryStageWorkerError("CANARY_EXECUTION_ARTIFACT_BINDING_INVALID")
    _sha(item.get("file_sha256"), "CANARY_EXECUTION_ARTIFACT_HASH_INVALID")
    for key in extras:
        _sha(item.get(key), "CANARY_EXECUTION_ARTIFACT_HASH_INVALID")
    return item


def _hash_regular_nofollow(path: Path, expected: str, code: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryStageWorkerError(code) from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CanaryStageWorkerError(code)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CanaryStageWorkerError(code) from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise CanaryStageWorkerError(code)
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        final = os.fstat(descriptor)
        if (final.st_size, final.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise CanaryStageWorkerError(code)
    finally:
        os.close(descriptor)
    if digest.hexdigest() != expected:
        raise CanaryStageWorkerError(code)


def _project_execution_authority(
    value: Mapping[str, Any], *, stage_id: str
) -> CanaryStageContext:
    """Project an already validated owner packet into a stage-only context."""
    expected = {
        "schema_version", "artifact_type", "status",
        "authorization_path", "authorization_sha256", "authorization_file_sha256", "branch",
        "governing_commit", "run_id", "attempt_id", "output_root", "manifest",
        "batch_plan", "scheduler_plan", "production_contract",
        "environment_receipt", "checkpoint", "runtime_authority", "hard_scope",
        "scheduler", "stage_authorizations", "body_transfer_authorization",
        "launch_authority_sha256", "gcloud", "crc32c", "owner_authorized",
        "authorization_scopes", "qsub", "stage_worker", "stage_launcher",
        "requester_pays",
    }
    _keys(value, expected, "CANARY_EXECUTION_AUTHORITY_SCHEMA_MISMATCH")
    governing_commit = str(value.get("governing_commit"))
    run_id = str(value.get("run_id"))
    attempt_id = str(value.get("attempt_id"))
    output_root = Path(str(value.get("output_root")))
    if (
        value.get("owner_authorized") is not True
        or value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_exact_five_canary_execution_authorization_v1"
        or value.get("status") != "OWNER_AUTHORIZED_EXACT_FIVE_CANARY"
        or Path(str(value.get("authorization_path")))
        != OWNER_PRIVATE_ROOT / "execution_authorization_v1.json"
        or str(value.get("branch")) != "codex/lvef-multitask-revalidation"
        or COMMIT_RE.fullmatch(governing_commit) is None
        or RUN_ID_RE.fullmatch(run_id) is None
        or core.ATTEMPT_RE.fullmatch(attempt_id) is None
        or output_root != CANARY_RUN_ROOT / run_id
    ):
        raise CanaryStageWorkerError("CANARY_EXECUTION_IDENTITY_INVALID")
    manifest = _binding(
        value.get("manifest"), extras=frozenset({"embedded_sha256"})
    )
    plan = _binding(
        value.get("batch_plan"), extras=frozenset({"canonical_sha256"})
    )
    scheduler = _binding(
        value.get("scheduler_plan"), extras=frozenset({"canonical_sha256"})
    )
    contract = _binding(value.get("production_contract"))
    environment = _binding(value.get("environment_receipt"))
    checkpoint = _binding(value.get("checkpoint"))
    body = _binding(value.get("body_transfer_authorization"))
    hard_scope = _mapping(value.get("hard_scope"), "CANARY_HARD_SCOPE_INVALID")
    _keys(hard_scope, {"studies", "subjects", "split", "max_objects", "max_bytes", "batch_id"}, "CANARY_HARD_SCOPE_INVALID")
    if dict(hard_scope) != {
        "studies": 5, "subjects": 5, "split": "train", "max_objects": 750,
        "max_bytes": 5_000_000_000, "batch_id": BATCH_ID,
    }:
        raise CanaryStageWorkerError("CANARY_HARD_SCOPE_INVALID")
    frozen = _mapping(value.get("scheduler"), "CANARY_SCHEDULER_SCOPE_INVALID")
    _keys(frozen, {"ordered_stage_ids", "scheduler_submission_count", "maximum_scheduler_submission_count", "gpu_stage_count", "stage_retry_count", "array_expansion_permitted", "automatic_resubmission_permitted", "production_continuation"}, "CANARY_SCHEDULER_SCOPE_INVALID")
    if (
        tuple(frozen.get("ordered_stage_ids", ())) != STAGE_IDS
        or frozen.get("scheduler_submission_count") != 5
        or frozen.get("maximum_scheduler_submission_count") != 5
        or frozen.get("gpu_stage_count") != 1
        or frozen.get("stage_retry_count") != 0
        or any(frozen.get(key) is not False for key in ("array_expansion_permitted", "automatic_resubmission_permitted", "production_continuation"))
    ):
        raise CanaryStageWorkerError("CANARY_SCHEDULER_SCOPE_INVALID")
    grants = _mapping(value.get("stage_authorizations"), "CANARY_STAGE_GRANTS_INVALID")
    _keys(grants, set(STAGE_IDS), "CANARY_STAGE_GRANTS_INVALID")
    grant = _mapping(grants[stage_id], "CANARY_STAGE_GRANT_INVALID")
    _keys(grant, {"stage_id", "path", "file_sha256", "authorized"}, "CANARY_STAGE_GRANT_INVALID")
    if grant.get("authorized") is not True or grant.get("stage_id") != stage_id:
        raise CanaryStageWorkerError("CANARY_STAGE_NOT_AUTHORIZED")
    gcloud = _mapping(value.get("gcloud"), "CANARY_GCLOUD_BINDING_INVALID")
    crc32c = _mapping(value.get("crc32c"), "CANARY_CRC32C_BINDING_INVALID")
    requester_pays = _mapping(
        value.get("requester_pays"), "CANARY_REQUESTER_PAYS_BINDING_INVALID"
    )
    for executable_name in ("qsub", "stage_worker", "stage_launcher"):
        executable = _binding(value.get(executable_name))
        path = Path(str(executable["path"]))
        _hash_regular_nofollow(
            path,
            str(executable["file_sha256"]),
            "CANARY_EXECUTION_EXECUTABLE_BINDING_CHANGED",
        )
    _keys(gcloud, {"binary_path", "resolution_receipt_path", "cloudsdk_config_path"}, "CANARY_GCLOUD_BINDING_INVALID")
    _keys(crc32c, {"python_path", "worker_path"}, "CANARY_CRC32C_BINDING_INVALID")
    _keys(
        requester_pays,
        {"billing_environment_variable", "billing_project"},
        "CANARY_REQUESTER_PAYS_BINDING_INVALID",
    )
    billing_environment_variable = str(
        requester_pays.get("billing_environment_variable")
    )
    billing_project = str(requester_pays.get("billing_project"))
    if (
        billing_environment_variable != "LVEF_C3_GCP_BILLING_PROJECT"
        or not billing_project
        or billing_project != billing_project.strip()
        or any(character.isspace() for character in billing_project)
    ):
        raise CanaryStageWorkerError("CANARY_REQUESTER_PAYS_BINDING_INVALID")
    runtime = core.validate_runtime_authority(
        _mapping(value.get("runtime_authority"), "CANARY_RUNTIME_AUTHORITY_INVALID")
    )
    return CanaryStageContext(
        authorization_sha256=_sha(value.get("authorization_sha256"), "CANARY_EXECUTION_AUTHORITY_HASH_INVALID"),
        authorization_file_sha256=_sha(value.get("authorization_file_sha256"), "CANARY_EXECUTION_AUTHORITY_HASH_INVALID"),
        governing_commit=governing_commit, run_id=run_id, attempt_id=attempt_id,
        output_root=output_root,
        manifest_path=Path(str(manifest["path"])),
        manifest_file_sha256=str(manifest["file_sha256"]),
        manifest_sha256=str(manifest["embedded_sha256"]),
        batch_plan_path=Path(str(plan["path"])),
        batch_plan_file_sha256=str(plan["file_sha256"]),
        batch_plan_sha256=str(plan["canonical_sha256"]),
        scheduler_plan_path=Path(str(scheduler["path"])),
        scheduler_plan_file_sha256=str(scheduler["file_sha256"]),
        scheduler_plan_sha256=str(scheduler["canonical_sha256"]),
        contract_path=Path(str(contract["path"])), contract_sha256=str(contract["file_sha256"]),
        environment_receipt=Path(str(environment["path"])), environment_receipt_sha256=str(environment["file_sha256"]),
        checkpoint=Path(str(checkpoint["path"])), checkpoint_sha256=str(checkpoint["file_sha256"]),
        runtime_authority=runtime,
        stage_authorization_path=Path(str(grant["path"])), stage_authorization_file_sha256=_sha(grant.get("file_sha256"), "CANARY_STAGE_GRANT_INVALID"),
        body_transfer_authorization_path=Path(str(body["path"])), body_transfer_authorization_file_sha256=str(body["file_sha256"]),
        launch_authority_sha256=_sha(value.get("launch_authority_sha256"), "CANARY_LAUNCH_AUTHORITY_INVALID"),
        gcloud_binary=Path(str(gcloud["binary_path"])), gcloud_resolution_receipt=Path(str(gcloud["resolution_receipt_path"])),
        crc32c_python=Path(str(crc32c["python_path"])), crc32c_worker=Path(str(crc32c["worker_path"])),
        cloudsdk_config=Path(str(gcloud["cloudsdk_config_path"])),
        billing_environment_variable=billing_environment_variable,
        billing_project=billing_project,
    )


def _validate_context(
    context: CanaryStageContext, *, stage_id: str
) -> tuple[dict[str, Any], Mapping[str, Any], core.PlanRequirements, Mapping[str, Any]]:
    if (
        stage_id not in STAGE_IDS
        or COMMIT_RE.fullmatch(context.governing_commit) is None
        or RUN_ID_RE.fullmatch(context.run_id) is None
        or context.attempt_id != context.run_id
        or core.ATTEMPT_RE.fullmatch(context.attempt_id) is None
        or context.output_root != CANARY_RUN_ROOT / context.run_id
        or SHA256_RE.fullmatch(context.authorization_sha256) is None
    ):
        raise CanaryStageWorkerError("CANARY_STAGE_CONTEXT_IDENTITY_INVALID")
    manifest = manifest_contract.load_and_validate_manifest(
        context.manifest_path,
        expected_file_sha256=context.manifest_file_sha256,
        expected_manifest_sha256=context.manifest_sha256,
        expected_source_authority_commit=context.governing_commit,
    )
    body = manifest["manifest"]
    requirements = core.PlanRequirements(
        release=str(body["source_release"]), selected_studies=5,
        selected_subjects=5, normalized_source_objects=int(body["expected_object_count"]),
        selected_source_bytes=int(body["expected_byte_total"]), batch_count=1,
        studies_per_full_batch=5, final_batch_studies=5, contract_id=CONTRACT_ID,
    )
    for path, digest, code in (
        (context.batch_plan_path, context.batch_plan_file_sha256, "CANARY_BATCH_PLAN_FILE_CHANGED"),
        (context.scheduler_plan_path, context.scheduler_plan_file_sha256, "CANARY_SCHEDULER_PLAN_FILE_CHANGED"),
        (context.contract_path, context.contract_sha256, "CANARY_CONTRACT_FILE_CHANGED"),
        (context.environment_receipt, context.environment_receipt_sha256, "CANARY_ENVIRONMENT_FILE_CHANGED"),
        (context.checkpoint, context.checkpoint_sha256, "CANARY_CHECKPOINT_FILE_CHANGED"),
        (context.stage_authorization_path, context.stage_authorization_file_sha256, "CANARY_STAGE_AUTHORIZATION_FILE_CHANGED"),
    ):
        _hash_regular_nofollow(path, digest, code)
    plan = core.load_strict_json(context.batch_plan_path)
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    if (
        plan_sha != context.batch_plan_sha256
        or context.runtime_authority["batch_plan_sha256"] != plan_sha
        or context.runtime_authority["git_commit"] != context.governing_commit
        or any(context.runtime_authority[key] != str(plan["authority"][key]) for key in core.PLAN_AUTHORITY_KEYS)
        or context.runtime_authority["environment_receipt_sha256"] != context.environment_receipt_sha256
        or context.runtime_authority["checkpoint_sha256"] != context.checkpoint_sha256
    ):
        raise CanaryStageWorkerError("CANARY_RUNTIME_PLAN_AUTHORITY_MISMATCH")
    scheduler = scheduler_contract.load_scheduler_plan(context.scheduler_plan_path)
    observed_scheduler_sha = scheduler_contract.validate_scheduler_plan(
        scheduler, repository_root=SCRIPT_ROOT.parent, require_bound_manifest=True
    )
    if (
        observed_scheduler_sha != context.scheduler_plan_sha256
        or scheduler["canary_manifest_sha256"] != context.manifest_sha256
    ):
        raise CanaryStageWorkerError("CANARY_MANIFEST_SCHEDULER_BINDING_MISMATCH")
    contract = core.load_orchestration_contract(context.contract_path)
    stage_batch = "all_batches" if stage_id == "CANARY_FINALIZATION" else BATCH_ID
    authorization_stage = (
        "PRESERVATION_FINALIZATION" if stage_id == "CANARY_FINALIZATION" else stage_id
    )
    if authorization_stage == "DOWNLOAD":
        _hash_regular_nofollow(context.body_transfer_authorization_path, context.body_transfer_authorization_file_sha256, "CANARY_BODY_AUTHORIZATION_FILE_CHANGED")
    else:
        stages.validate_stage_authorization(
            context.stage_authorization_path, stage=authorization_stage,
            batch_id=stage_batch, attempt_id=context.attempt_id,
            governing_commit=context.governing_commit,
            orchestration_contract_sha256=context.contract_sha256,
            batch_plan_sha256=plan_sha,
            launch_authority_sha256=context.launch_authority_sha256,
        )
    return manifest, plan, requirements, contract


def _load_latest_dispatch_ledger(
    context: CanaryStageContext, *, stage_id: str
) -> Mapping[str, Any]:
    import lvef_c3_canary_dispatch as dispatcher

    root = context.output_root / "scheduler_claims"
    if root.is_symlink() or not root.is_dir():
        raise CanaryStageWorkerError("CANARY_DISPATCH_CLAIMS_ROOT_INVALID")
    candidates: list[tuple[int, Path]] = []
    for path in root.iterdir():
        match = re.fullmatch(r"dispatch_ledger_([0-9]{2})[.]restricted[.]json", path.name)
        if match is None:
            raise CanaryStageWorkerError("CANARY_DISPATCH_CLAIMS_ENTRY_UNDECLARED")
        candidates.append((int(match.group(1)), path))
    if not candidates or len({sequence for sequence, _ in candidates}) != len(candidates):
        raise CanaryStageWorkerError("CANARY_DISPATCH_LATEST_SNAPSHOT_INVALID")
    _, latest = max(candidates)
    metadata = os.lstat(latest)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise CanaryStageWorkerError("CANARY_DISPATCH_LATEST_SNAPSHOT_INVALID")
    ledger = core.load_strict_json(latest)
    dispatcher.validate_dispatch_ledger(ledger)
    if (
        ledger.get("authorization_sha256") != context.authorization_sha256
        or ledger.get("authorization_file_sha256")
        != context.authorization_file_sha256
        or ledger.get("run_id") != context.run_id
        or ledger.get("attempt_id") != context.attempt_id
        or ledger.get("output_root") != str(context.output_root)
        or ledger.get("scheduler_plan_sha256") != context.scheduler_plan_sha256
        or ledger.get("manifest_file_sha256") != context.manifest_file_sha256
        or ledger.get("manifest_sha256") != context.manifest_sha256
    ):
        raise CanaryStageWorkerError("CANARY_DISPATCH_SNAPSHOT_AUTHORITY_MISMATCH")
    record = next(
        (row for row in ledger["stages"] if row["stage_id"] == stage_id), None
    )
    if record is None or record.get("status") != "SUBMITTED":
        raise CanaryStageWorkerError("CANARY_STAGE_NOT_SUBMITTED")
    return ledger


def _load_predecessor_stage_result(
    context: CanaryStageContext, *, stage_id: str
) -> tuple[str | None, Mapping[str, Any] | None]:
    index = STAGE_IDS.index(stage_id)
    if index == 0:
        return None, None
    predecessor = STAGE_IDS[index - 1]
    path = context.output_root / "stage_results" / f"{predecessor}.result.restricted.json"
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_RESULT_MISSING") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_RESULT_INVALID")
    value = core.load_strict_json(path)
    expected_keys = {
        "schema_version", "artifact_type", "status", "stage_id", "ordinal",
        "run_id", "attempt_id", "authorization_sha256", "manifest_sha256",
        "authorization_file_sha256",
        "batch_plan_sha256", "scheduler_plan_sha256", "claim_sha256",
        "predecessor_result_sha256", "stage_output_sha256",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or value.get("schema_version") != 1
        or value.get("artifact_type") != "lvef_c3_canary_stage_result_v1"
        or value.get("status") != "PASS"
        or value.get("stage_id") != predecessor
        or value.get("ordinal") != index
        or value.get("run_id") != context.run_id
        or value.get("attempt_id") != context.attempt_id
        or value.get("authorization_sha256") != context.authorization_sha256
        or value.get("authorization_file_sha256")
        != context.authorization_file_sha256
        or value.get("manifest_sha256") != context.manifest_sha256
        or value.get("batch_plan_sha256") != context.batch_plan_sha256
        or value.get("scheduler_plan_sha256") != context.scheduler_plan_sha256
        or any(
            item is not None and SHA256_RE.fullmatch(str(item)) is None
            for item in (
                value.get("claim_sha256"), value.get("predecessor_result_sha256"),
                value.get("stage_output_sha256"),
            )
        )
    ):
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_RESULT_INVALID")
    claim_path = (
        context.output_root
        / "stage_execution_claims"
        / f"{predecessor}.claim.restricted.json"
    )
    try:
        claim_metadata = os.lstat(claim_path)
    except OSError as exc:
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_CLAIM_MISSING") from exc
    if (
        not stat.S_ISREG(claim_metadata.st_mode)
        or stat.S_ISLNK(claim_metadata.st_mode)
        or stat.S_IMODE(claim_metadata.st_mode) != 0o600
        or core.sha256_file(claim_path) != value.get("claim_sha256")
    ):
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_CLAIM_INVALID")
    claim = core.load_strict_json(claim_path)
    expected_claim_keys = {
        "schema_version", "artifact_type", "status", "stage_id", "ordinal",
        "run_id", "attempt_id", "authorization_sha256", "manifest_sha256",
        "authorization_file_sha256",
        "batch_plan_sha256", "scheduler_plan_sha256",
        "predecessor_result_sha256", "claimed_at_utc",
    }
    try:
        claimed_at = datetime.fromisoformat(str(claim.get("claimed_at_utc")))
    except (AttributeError, ValueError) as exc:
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_CLAIM_INVALID") from exc
    if (
        not isinstance(claim, Mapping)
        or set(claim) != expected_claim_keys
        or claim.get("schema_version") != 1
        or claim.get("artifact_type")
        != "lvef_c3_canary_stage_execution_claim_v1"
        or claim.get("status") != "CLAIMED_NO_RETRY"
        or claim.get("stage_id") != predecessor
        or claim.get("ordinal") != index
        or claim.get("run_id") != context.run_id
        or claim.get("attempt_id") != context.attempt_id
        or claim.get("authorization_sha256") != context.authorization_sha256
        or claim.get("authorization_file_sha256")
        != context.authorization_file_sha256
        or claim.get("manifest_sha256") != context.manifest_sha256
        or claim.get("batch_plan_sha256") != context.batch_plan_sha256
        or claim.get("scheduler_plan_sha256") != context.scheduler_plan_sha256
        or claim.get("predecessor_result_sha256")
        != value.get("predecessor_result_sha256")
        or claimed_at.tzinfo is None
        or claimed_at.utcoffset() != timezone.utc.utcoffset(claimed_at)
    ):
        raise CanaryStageWorkerError("CANARY_PREDECESSOR_CLAIM_INVALID")
    return core.sha256_file(path), value


def _claim_stage_execution(
    context: CanaryStageContext, *, stage_id: str,
    predecessor_result_sha256: str | None,
) -> tuple[Path, str]:
    root = context.output_root / "stage_execution_claims"
    if root.is_symlink() or not root.is_dir():
        raise CanaryStageWorkerError("CANARY_STAGE_CLAIM_ROOT_INVALID")
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_stage_execution_claim_v1",
        "status": "CLAIMED_NO_RETRY",
        "stage_id": stage_id,
        "ordinal": STAGE_IDS.index(stage_id) + 1,
        "run_id": context.run_id,
        "attempt_id": context.attempt_id,
        "authorization_sha256": context.authorization_sha256,
        "authorization_file_sha256": context.authorization_file_sha256,
        "manifest_sha256": context.manifest_sha256,
        "batch_plan_sha256": context.batch_plan_sha256,
        "scheduler_plan_sha256": context.scheduler_plan_sha256,
        "predecessor_result_sha256": predecessor_result_sha256,
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path = root / f"{stage_id}.claim.restricted.json"
    if os.path.lexists(path):
        raise CanaryStageWorkerError("CANARY_STAGE_ALREADY_CLAIMED_NO_RETRY")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                raise CanaryStageWorkerError("CANARY_STAGE_CLAIM_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CanaryStageWorkerError("CANARY_STAGE_CLAIM_MODE_INVALID")
    finally:
        os.close(descriptor)
    return path, hashlib.sha256(payload).hexdigest()


def _write_stage_result(
    context: CanaryStageContext, *, stage_id: str, result: Mapping[str, Any],
    claim_sha256: str, predecessor_result_sha256: str | None,
) -> Mapping[str, Any]:
    root = context.output_root / "stage_results"
    if root.is_symlink() or not root.is_dir():
        raise CanaryStageWorkerError("CANARY_STAGE_RESULT_ROOT_INVALID")
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_stage_result_v1",
        "status": "PASS",
        "stage_id": stage_id,
        "ordinal": STAGE_IDS.index(stage_id) + 1,
        "run_id": context.run_id,
        "attempt_id": context.attempt_id,
        "authorization_sha256": context.authorization_sha256,
        "authorization_file_sha256": context.authorization_file_sha256,
        "manifest_sha256": context.manifest_sha256,
        "batch_plan_sha256": context.batch_plan_sha256,
        "scheduler_plan_sha256": context.scheduler_plan_sha256,
        "claim_sha256": claim_sha256,
        "predecessor_result_sha256": predecessor_result_sha256,
        "stage_output_sha256": core.canonical_json_sha256(result),
    }
    path = root / f"{stage_id}.result.restricted.json"
    core.atomic_write_json_no_clobber(path, value, attempt_id=context.attempt_id)
    return value


def _create_scoped_download_root(context: CanaryStageContext) -> Path:
    cursor = context.output_root
    for component in ("attempts", context.attempt_id, "raw"):
        cursor /= component
        if os.path.lexists(cursor):
            raise CanaryStageWorkerError("CANARY_DOWNLOAD_ROOT_ALREADY_EXISTS")
        cursor.mkdir(mode=0o700)
        metadata = os.lstat(cursor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise CanaryStageWorkerError("CANARY_DOWNLOAD_ROOT_INVALID")
    return cursor


def run_canary_stage(
    stage_id: str, context: CanaryStageContext, *,
    dependencies: CanaryStageDependencies | None = None,
    argv: Sequence[str] = (), scheduler_job_identity: str = "canary-stage",
) -> Mapping[str, Any]:
    """Validate all sealed inputs, then invoke exactly one production stage."""
    dependency = dependencies or CanaryStageDependencies()
    manifest, plan, requirements, contract = _validate_context(context, stage_id=stage_id)
    _load_latest_dispatch_ledger(context, stage_id=stage_id)
    predecessor_sha, _ = _load_predecessor_stage_result(context, stage_id=stage_id)
    _, claim_sha = _claim_stage_execution(
        context, stage_id=stage_id, predecessor_result_sha256=predecessor_sha
    )
    batch_root = context.output_root / "attempts" / context.attempt_id / "batches" / BATCH_ID
    raw_root = context.output_root / "attempts" / context.attempt_id / "raw"
    cache_root = context.output_root / "attempts" / context.attempt_id / "extracted_cache" / BATCH_ID
    expected_keys = {row["source_object_key"] for row in plan["batches"][0]["objects"]}

    if stage_id == "DOWNLOAD":
        if _create_scoped_download_root(context) != raw_root:
            raise CanaryStageWorkerError("CANARY_DOWNLOAD_ROOT_INVALID")
        ledger = core.initialize_resume_ledger(
            plan, requirements=requirements, attempt_id=context.attempt_id,
            authority=context.runtime_authority, batch_ids=[BATCH_ID],
        )
        body_receipt = core.load_strict_json(context.body_transfer_authorization_path)
        provider = dependency.token_provider_factory(
            context.gcloud_binary,
            cloudsdk_config=context.cloudsdk_config,
            authority_receipt=context.gcloud_resolution_receipt,
            authority_receipt_sha256=context.runtime_authority["gcloud_resolution_receipt_sha256"],
        )
        core.validate_gcloud_runtime_authority(
            provider.validate_authority(), expected_runtime_authority=context.runtime_authority
        )
        prior_billing = os.environ.get(context.billing_environment_variable)
        os.environ[context.billing_environment_variable] = context.billing_project
        try:
            with dependency.digest_worker_factory(
                python_executable=context.crc32c_python,
                worker_script=context.crc32c_worker,
                expected_python_sha256=context.runtime_authority["crc32c_python_executable_sha256"],
                expected_worker_sha256=context.runtime_authority["crc32c_worker_sha256"],
                expected_distribution_sha256=context.runtime_authority["crc32c_distribution_sha256"],
            ) as digests:
                updated = dependency.download(
                    plan=plan, requirements=requirements, ledger=ledger, contract=contract,
                    batch_id=BATCH_ID, expected_runtime_authority=context.runtime_authority,
                    authorization_receipt=body_receipt, output_root=raw_root,
                    scoped_production_root=context.output_root,
                    launch_authority_sha256=context.launch_authority_sha256,
                    argv=argv, token_provider=provider, transport=dependency.transport_factory(),
                    digest_provider=digests.digest,
                )
        finally:
            if prior_billing is None:
                os.environ.pop(context.billing_environment_variable, None)
            else:
                os.environ[context.billing_environment_variable] = prior_billing
        ledger_output = batch_root / "download_resume_ledger.restricted.json"
        ledger_output.parent.mkdir(parents=True, exist_ok=True)
        if ledger_output.exists() or ledger_output.is_symlink():
            if core.load_strict_json(ledger_output) != updated:
                raise CanaryStageWorkerError("CANARY_DOWNLOAD_LEDGER_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                ledger_output, updated, attempt_id=context.attempt_id
            )
        _write_stage_result(
            context, stage_id=stage_id, result=updated, claim_sha256=claim_sha,
            predecessor_result_sha256=predecessor_sha,
        )
        return updated
    if stage_id == "DICOM_EXTRACTION":
        verified = raw_root / BATCH_ID / "verified_download_manifest.restricted.csv"
        input_ledger = batch_root / "download_resume_ledger.restricted.json"
        stages.validate_stage_predecessor(
            input_ledger=input_ledger, batch_id=BATCH_ID,
            expected_state="DOWNLOAD_VERIFIED",
            expected_authority=context.runtime_authority,
            expected_attempt_id=context.attempt_id,
            expected_object_keys=expected_keys, bound_manifest=verified,
        )
        stages.validate_download_manifest_plan_membership(verified, plan["batches"][0])
        summary = dependency.dicom(
            verified_download_manifest=verified, download_root=raw_root / BATCH_ID / "objects",
            batch_output_root=cache_root, workers=4, batch_id=BATCH_ID,
            attempt_id=context.attempt_id, runtime_authority=context.runtime_authority,
        )
        stages.advance_stage_ledger(
            input_ledger=input_ledger,
            output_ledger=batch_root / "extraction_resume_ledger.restricted.json",
            receipt_root=cache_root / "dicom_extraction" / "transition_receipts",
            batch_id=BATCH_ID,
            transitions=(
                ("DICOM_AUDIT_COMPLETE", stages.sha256_file(cache_root / "dicom_extraction" / "dicom_audit.restricted.csv")),
                ("EXTRACTION_COMPLETE", stages.sha256_file(cache_root / "dicom_extraction" / "extraction_manifest.restricted.csv")),
            ),
            expected_authority=context.runtime_authority,
            expected_attempt_id=context.attempt_id,
            expected_object_keys=expected_keys,
        )
        _write_stage_result(
            context, stage_id=stage_id, result=summary, claim_sha256=claim_sha,
            predecessor_result_sha256=predecessor_sha,
        )
        return summary
    if stage_id == "ECHOPRIME_EMBEDDING":
        extraction = cache_root / "dicom_extraction" / "extraction_manifest.restricted.csv"
        input_ledger = batch_root / "extraction_resume_ledger.restricted.json"
        predecessor = cache_root / "dicom_extraction" / "transition_receipts" / "extraction_complete.restricted.json"
        stages.validate_stage_predecessor(
            input_ledger=input_ledger, batch_id=BATCH_ID,
            expected_state="EXTRACTION_COMPLETE",
            expected_authority=context.runtime_authority,
            expected_attempt_id=context.attempt_id,
            expected_object_keys=expected_keys, bound_manifest=extraction,
            predecessor_transition_receipt=predecessor,
        )
        stages.validate_extraction_manifest_plan_membership(
            extraction, plan["batches"][0]
        )
        summary = dependency.echoprime(
            extraction_manifest=extraction, extraction_root=cache_root / "dicom_extraction" / "clips",
            selected_batch_manifest=raw_root / BATCH_ID / "selected_batch_manifest.restricted.csv",
            checkpoint=context.checkpoint, environment_receipt=context.environment_receipt,
            orchestration_contract=context.contract_path, batch_plan=context.batch_plan_path,
            batch_id=BATCH_ID, batch_output_root=batch_root, batch_size=8, seed=20260803,
            attempt_id=context.attempt_id, runtime_authority=context.runtime_authority,
            requirements=requirements,
        )
        stages.advance_stage_ledger(
            input_ledger=input_ledger,
            output_ledger=batch_root / "pooling_resume_ledger.restricted.json",
            receipt_root=batch_root / "echoprime" / "transition_receipts",
            batch_id=BATCH_ID,
            transitions=(
                ("EMBEDDING_COMPLETE", stages.sha256_file(batch_root / "echoprime" / "clip_manifest.restricted.csv")),
                ("STUDY_POOLING_COMPLETE", stages.sha256_file(batch_root / "echoprime" / "study_manifest.restricted.csv")),
            ),
            expected_authority=context.runtime_authority,
            expected_attempt_id=context.attempt_id,
            expected_object_keys=expected_keys,
        )
        _write_stage_result(
            context, stage_id=stage_id, result=summary, claim_sha256=claim_sha,
            predecessor_result_sha256=predecessor_sha,
        )
        return summary
    if stage_id == "BATCH_PRESERVATION":
        result = dependency.preserve(
            contract_path=context.contract_path, plan_path=context.batch_plan_path,
            batch_id=BATCH_ID, attempt_id=context.attempt_id,
            governing_commit=context.governing_commit, production_root=context.output_root,
            output_root=batch_root / "preservation", environment_receipt=context.environment_receipt,
            checkpoint=context.checkpoint, scheduler_job_identity=scheduler_job_identity,
            input_ledger=batch_root / "pooling_resume_ledger.restricted.json",
            requirements=requirements, expected_runtime_authority=context.runtime_authority,
            scheduler_runner_path=SCRIPT_ROOT / "scc_run_lvef_c3_canary.sh",
        )
        _write_stage_result(
            context, stage_id=stage_id, result=result, claim_sha256=claim_sha,
            predecessor_result_sha256=predecessor_sha,
        )
        return result
    receipt = preservation.load_json(
        batch_root / "preservation" / "batch_preservation_receipt.restricted.json",
        "CANARY_PRESERVATION_RECEIPT",
    )
    summary = dependency.finalize(
        receipt, expected_governing_commit=context.governing_commit,
        expected_attempt_id=context.attempt_id,
        expected_canary_manifest_sha256=context.manifest_sha256,
        expected_batch_plan_sha256=context.batch_plan_sha256,
        expected_scheduler_plan_sha256=context.scheduler_plan_sha256,
        expected_object_count=requirements.normalized_source_objects,
        expected_source_bytes=requirements.selected_source_bytes,
    )
    finalizer.write_json_atomic(
        context.output_root
        / "attempts"
        / context.attempt_id
        / "canary_finalization_receipt.aggregate_safe.json",
        summary,
    )
    _write_stage_result(
        context, stage_id=stage_id, result=summary, claim_sha256=claim_sha,
        predecessor_result_sha256=predecessor_sha,
    )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope-authority", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGE_IDS, required=True)
    parser.add_argument("--scheduler-job-identity", default="canary-stage")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.scope_authority != OWNER_PRIVATE_ROOT / "execution_authorization_v1.json":
        raise CanaryStageWorkerError("CANARY_EXECUTION_AUTHORITY_PATH_INVALID")
    import lvef_c3_canary_execution_authority as execution_authority

    authority = execution_authority.load_and_validate_execution_authority(
        args.scope_authority, require_output_absent=False
    )
    context = _project_execution_authority(authority, stage_id=args.stage)
    result = run_canary_stage(
        args.stage, context, argv=sys.argv,
        scheduler_job_identity=args.scheduler_job_identity,
    )
    print(json.dumps({
        "status": str(result.get("status", "PASS")), "stage_id": args.stage,
        "identifiers_emitted": False, "paths_emitted": False,
        "production_continuation": False,
    }, sort_keys=True))
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except SystemExit:
        raise
    except (CanaryStageWorkerError, core.OrchestrationError,
            stages.ProductionStageError, preservation.BatchPreservationError,
            finalizer.ProductionFinalizationError) as exc:
        code = getattr(exc, "code", str(exc))
        print(json.dumps({"status": "BLOCKED", "error_code": code}, sort_keys=True))
        return 78
    except Exception:
        print(json.dumps({"status": "BLOCKED", "error_code": "CANARY_STAGE_UNEXPECTED_SANITIZED"}, sort_keys=True))
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
