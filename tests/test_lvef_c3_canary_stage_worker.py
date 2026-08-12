from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


worker = _load("lvef_c3_canary_stage_worker_test", "lvef_c3_canary_stage_worker.py")


def _context(root: Path) -> worker.CanaryStageContext:
    run_id = "lvef_c3_exact_five_canary_ab12cd34"
    return worker.CanaryStageContext(
        authorization_sha256="a" * 64,
        authorization_file_sha256="0" * 64,
        governing_commit="b" * 40,
        run_id=run_id,
        attempt_id=run_id,
        output_root=root,
        manifest_path=root / "manifest.json",
        manifest_file_sha256="c" * 64,
        manifest_sha256="d" * 64,
        batch_plan_path=root / "plan.json",
        batch_plan_file_sha256="e" * 64,
        batch_plan_sha256="f" * 64,
        scheduler_plan_path=root / "scheduler.json",
        scheduler_plan_file_sha256="1" * 64,
        scheduler_plan_sha256="2" * 64,
        contract_path=root / "contract.yaml",
        contract_sha256="3" * 64,
        environment_receipt=root / "environment.json",
        environment_receipt_sha256="4" * 64,
        checkpoint=root / "checkpoint.pt",
        checkpoint_sha256="5" * 64,
        runtime_authority={key: "6" * 64 for key in worker.core.RUNTIME_AUTHORITY_KEYS},
        stage_authorization_path=root / "stage-auth.json",
        stage_authorization_file_sha256="7" * 64,
        body_transfer_authorization_path=root / "body-auth.json",
        body_transfer_authorization_file_sha256="8" * 64,
        launch_authority_sha256="9" * 64,
        gcloud_binary=root / "gcloud",
        gcloud_resolution_receipt=root / "gcloud-receipt.json",
        crc32c_python=root / "python",
        crc32c_worker=root / "crc.py",
        cloudsdk_config=root / "cloudsdk",
        billing_environment_variable="LVEF_C3_GCP_BILLING_PROJECT",
        billing_project="synthetic-billing-project",
    )


def _dispatch_ledger(context: worker.CanaryStageContext) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_durable_dispatch_ledger_v1",
        "status": "DISPATCHED_FROZEN_DAG",
        "authorization_sha256": context.authorization_sha256,
        "authorization_file_sha256": context.authorization_file_sha256,
        "run_id": context.run_id,
        "attempt_id": context.attempt_id,
        "output_root": str(context.output_root),
        "scheduler_plan_sha256": context.scheduler_plan_sha256,
        "manifest_file_sha256": context.manifest_file_sha256,
        "manifest_sha256": context.manifest_sha256,
        "declared_submission_count": 5,
        "submission_count": 5,
        "active_claim": None,
        "failed_stage_id": None,
        "stages": [
            {
                "stage_id": stage_id,
                "ordinal": index + 1,
                "status": "SUBMITTED",
                "predecessor_stage_id": None if index == 0 else worker.STAGE_IDS[index - 1],
                "predecessor_job_id": None if index == 0 else str(100 + index - 1),
                "job_id": str(100 + index),
                "command_sha256": hashlib.sha256(stage_id.encode()).hexdigest(),
                "failure_code": None,
            }
            for index, stage_id in enumerate(worker.STAGE_IDS)
        ],
        "production_continuation_triggered": False,
    }


def test_stage_worker_claims_once_and_requires_submitted_dispatch_and_predecessor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        context = _context(root)
        (root / "stage_execution_claims").mkdir(mode=0o700)
        (root / "stage_results").mkdir(mode=0o700)
        claims = root / "scheduler_claims"
        claims.mkdir(mode=0o700)
        ledger = _dispatch_ledger(context)
        latest = claims / "dispatch_ledger_10.restricted.json"
        latest.write_text(json.dumps(ledger), encoding="utf-8")
        latest.chmod(0o600)
        with mock.patch.object(
            worker, "CANARY_RUN_ROOT", root.parent
        ), mock.patch.object(
            worker.core, "load_strict_json", side_effect=lambda path: json.loads(Path(path).read_text())
        ), mock.patch.object(
            worker, "_validate_context", return_value=(
                {}, {"batches": [{"objects": []}]}, mock.sentinel.requirements, {}
            )
        ), mock.patch.object(
            worker, "_load_predecessor_stage_result", return_value=(None, None)
        ), mock.patch.object(
            worker, "_write_stage_result"
        ), mock.patch.object(
            worker, "_load_latest_dispatch_ledger", wraps=worker._load_latest_dispatch_ledger
        ), mock.patch(
            "lvef_c3_canary_dispatch.validate_dispatch_ledger"
        ):
            dependencies = worker.CanaryStageDependencies(
                download=mock.Mock(return_value={"status": "PASS"}),
                token_provider_factory=mock.Mock(),
                digest_worker_factory=mock.Mock(),
            )
            # Stop after the no-clobber claim, before any external adapter.
            with mock.patch.object(
                worker.core, "initialize_resume_ledger", side_effect=RuntimeError("STOP_AFTER_CLAIM")
            ):
                try:
                    worker.run_canary_stage("DOWNLOAD", context, dependencies=dependencies)
                except RuntimeError as exc:
                    assert str(exc) == "STOP_AFTER_CLAIM"
                else:
                    raise AssertionError("stage unexpectedly continued")
            claim = root / "stage_execution_claims" / "DOWNLOAD.claim.restricted.json"
            assert claim.is_file()
            assert oct(claim.stat().st_mode & 0o777) == "0o600"
            try:
                worker._claim_stage_execution(
                    context, stage_id="DOWNLOAD", predecessor_result_sha256=None
                )
            except worker.CanaryStageWorkerError as exc:
                assert exc.code == "CANARY_STAGE_ALREADY_CLAIMED_NO_RETRY"
            else:
                raise AssertionError("second stage execution claim was accepted")

        unsubmitted = copy.deepcopy(ledger)
        unsubmitted["stages"][2]["status"] = "PENDING"
        latest.write_text(json.dumps(unsubmitted), encoding="utf-8")
        latest.chmod(0o600)
        with mock.patch("lvef_c3_canary_dispatch.validate_dispatch_ledger"):
            try:
                worker._load_latest_dispatch_ledger(
                    context, stage_id="ECHOPRIME_EMBEDDING"
                )
            except worker.CanaryStageWorkerError as exc:
                assert exc.code == "CANARY_STAGE_NOT_SUBMITTED"
            else:
                raise AssertionError("unsubmitted stage was accepted")

        try:
            worker._load_predecessor_stage_result(
                context, stage_id="DICOM_EXTRACTION"
            )
        except worker.CanaryStageWorkerError as exc:
            assert exc.code == "CANARY_PREDECESSOR_RESULT_MISSING"
        else:
            raise AssertionError("successor ran without predecessor result")


def test_stage_worker_production_function_identity_and_exact_stage_set() -> None:
    assert worker.STAGE_IDS == (
        "DOWNLOAD", "DICOM_EXTRACTION", "ECHOPRIME_EMBEDDING",
        "BATCH_PRESERVATION", "CANARY_FINALIZATION",
    )
    dependencies = worker.CanaryStageDependencies()
    assert dependencies.download is worker.core.execute_exact_batch_download
    assert dependencies.dicom is worker.stages.run_production_dicom_extraction
    assert dependencies.echoprime is worker.stages.run_production_echoprime
    assert dependencies.preserve is worker.preservation.preserve_batch
    assert dependencies.finalize is worker.finalizer.finalize_canary_preservation_receipt


def test_download_uses_only_packet_billing_binding_and_restores_environment() -> None:
    class Provider:
        def validate_authority(self):
            return {}

    class Digests:
        digest = staticmethod(lambda *_args: {})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with tempfile.TemporaryDirectory() as directory:
        context = _context(Path(directory))
        prior = "ambient-value-must-be-restored"
        os.environ[context.billing_environment_variable] = prior

        def download(**kwargs):
            assert os.environ[context.billing_environment_variable] == context.billing_project
            assert kwargs["scoped_production_root"] == context.output_root
            assert kwargs["requirements"] is mock.sentinel.requirements
            return {"status": "PASS", "batches": {}}

        dependencies = worker.CanaryStageDependencies(
            download=download,
            token_provider_factory=lambda *_args, **_kwargs: Provider(),
            transport_factory=lambda: object(),
            digest_worker_factory=lambda **_kwargs: Digests(),
        )
        plan = {"batches": [{"objects": []}]}
        with mock.patch.object(
            worker, "_validate_context",
            return_value=({}, plan, mock.sentinel.requirements, {}),
        ), mock.patch.object(
            worker, "_load_latest_dispatch_ledger", return_value={}
        ), mock.patch.object(
            worker, "_load_predecessor_stage_result", return_value=(None, None)
        ), mock.patch.object(
            worker, "_claim_stage_execution", return_value=(Path("claim"), "a" * 64)
        ), mock.patch.object(
            worker, "_write_stage_result"
        ), mock.patch.object(
            worker.core, "initialize_resume_ledger", return_value={}
        ), mock.patch.object(
            worker.core, "load_strict_json", return_value={}
        ), mock.patch.object(
            worker.core, "validate_gcloud_runtime_authority"
        ), mock.patch.object(
            worker.core, "atomic_write_json_no_clobber"
        ):
            result = worker.run_canary_stage(
                "DOWNLOAD", context, dependencies=dependencies, argv=("worker",)
            )
        assert result["status"] == "PASS"
        assert os.environ[context.billing_environment_variable] == prior
        del os.environ[context.billing_environment_variable]


def test_scoped_download_root_is_exact_five_only_and_default_is_unchanged() -> None:
    import lvef_c3_orchestration_core as core

    source = (ROOT / "scripts" / "lvef_c3_orchestration_core.py").read_text()
    assert "scoped_production_root" in inspect.signature(
        core.execute_exact_batch_download
    ).parameters
    assert "SCOPED_DOWNLOAD_ROOT_AUTHORITY_INVALID" in source
    assert '!= "lvef_multitask_c3_exact_five_canary_v1"' in source
    assert "requirements.normalized_source_objects > 750" in source
    assert "requirements.selected_source_bytes > 5_000_000_000" in source
