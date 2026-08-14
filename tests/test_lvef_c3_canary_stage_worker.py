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
import subprocess


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


def _authority_projection_value(root: Path) -> dict[str, object]:
    """Build only the closed outer shape needed for a schema regression."""
    context = _context(root)
    binding = {"path": str(root / "artifact"), "file_sha256": "1" * 64}
    canonical = {**binding, "canonical_sha256": "2" * 64}
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_exact_five_canary_execution_authorization_v1",
        "status": "OWNER_AUTHORIZED_EXACT_FIVE_CANARY",
        "created_at_utc": "2026-08-12T12:00:00+00:00",
        "authorization_path": str(worker.OWNER_PRIVATE_ROOT / "execution_authorization_v1.json"),
        "authorization_sha256": context.authorization_sha256,
        "authorization_file_sha256": context.authorization_file_sha256,
        "scheduler_tool_identities": {
            "qsub": {
                "path": str(root / "qsub"),
                "file_sha256": "1" * 64,
                "size_bytes": 1,
                "device_id": 1,
                "inode": 1,
            },
            "qstat": {
                "path": str(root / "qstat"),
                "file_sha256": "2" * 64,
                "size_bytes": 1,
                "device_id": 1,
                "inode": 2,
            },
        },
        "branch": "codex/lvef-multitask-revalidation",
        "governing_commit": context.governing_commit,
        "run_id": context.run_id,
        "attempt_id": context.attempt_id,
        "output_root": str(worker.CANARY_RUN_ROOT / context.run_id),
        "preselection_authority": {
            **binding,
            "semantic_sha256": "4" * 64,
        },
        "manifest": {**binding, "embedded_sha256": "3" * 64},
        "batch_plan": canonical,
        "scheduler_plan": canonical,
        "production_contract": binding,
        "environment_receipt": binding,
        "checkpoint": binding,
        "runtime_authority": {},
        "hard_scope": {},
        "scheduler": {},
        "stage_authorizations": {},
        "body_transfer_authorization": binding,
        "launch_authority_sha256": "4" * 64,
        "gcloud": {},
        "crc32c": {},
        "owner_authorized": True,
        "authorization_scopes": {},
        "qsub": {"path": str(root / "qsub"), "file_sha256": "1" * 64},
        "stage_worker": {},
        "stage_launcher": {},
        "requester_pays": {},
    }


def test_worker_outer_authority_schema_requires_created_timestamp() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = _authority_projection_value(Path(directory))
        # A complete current packet progresses beyond the closed outer schema.
        try:
            worker._project_execution_authority(value, stage_id="DOWNLOAD")
        except worker.CanaryStageWorkerError as exc:
            assert exc.code != "CANARY_EXECUTION_AUTHORITY_SCHEMA_MISMATCH"
        else:
            raise AssertionError("intentionally incomplete packet unexpectedly passed")

        missing = dict(value)
        del missing["created_at_utc"]
        try:
            worker._project_execution_authority(missing, stage_id="DOWNLOAD")
        except worker.CanaryStageWorkerError as exc:
            assert exc.code == "CANARY_EXECUTION_AUTHORITY_SCHEMA_MISMATCH"
        else:
            raise AssertionError("packet missing created_at_utc passed")


def test_stage_worker_requires_executing_lifecycle_and_records_terminal_receipt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        context = _context(Path(directory) / "owner_private" / "canary_runs" / "run")
        executing = {
            "current_state": "CANARY_EXECUTING",
            "run_id": context.run_id,
            "governing_commit": context.governing_commit,
        }
        with mock.patch(
            "lvef_c3_canary_state.load_state", return_value=executing
        ) as load_state, mock.patch(
            "lvef_c3_canary_state.transition_state",
            return_value={"current_state": "CANARY_TERMINAL_PASS"},
        ) as transition_state:
            worker._require_executing_lifecycle(context)
            worker._transition_terminal_lifecycle(
                context,
                target_state="CANARY_TERMINAL_PASS",
                reason_code="CANARY_FINALIZATION_RECEIPT_VALIDATED",
                bindings={"aggregate_safe_finalization_sha256": "a" * 64},
            )
        expected_lifecycle_root = context.output_root.parent.parent / "lifecycle_state"
        expected_state_path = (
            context.contract_path.parent / "lvef_c3_execution_state_v1.yaml"
        )
        assert load_state.call_args.kwargs == {
            "root": expected_lifecycle_root,
            "execution_state_path": expected_state_path,
            "expected_governing_commit": context.governing_commit,
            "expected_run_id": context.run_id,
        }
        assert transition_state.call_args.kwargs == {
            "root": expected_lifecycle_root,
            "execution_state_path": expected_state_path,
            "expected_current": "CANARY_EXECUTING",
            "target_state": "CANARY_TERMINAL_PASS",
            "governing_commit": context.governing_commit,
            "run_id": context.run_id,
            "reason_code": "CANARY_FINALIZATION_RECEIPT_VALIDATED",
            "bindings": {"aggregate_safe_finalization_sha256": "a" * 64},
        }
        assert "_require_executing_lifecycle" in worker.run_canary_stage.__code__.co_names
        assert "_transition_terminal_lifecycle" in worker.main.__code__.co_names

        terminal_fail = {**executing, "current_state": "CANARY_TERMINAL_FAIL"}
        with mock.patch(
            "lvef_c3_canary_state.load_state", return_value=terminal_fail
        ), mock.patch("lvef_c3_canary_state.transition_state") as no_second_edge:
            worker._transition_terminal_lifecycle(
                context,
                target_state="CANARY_TERMINAL_FAIL",
                reason_code="CANARY_STAGE_FAILED_NO_RETRY",
            )
        no_second_edge.assert_not_called()

    launcher = (ROOT / "scripts/scc_run_lvef_c3_canary_stage.sh").read_text()
    assert '[[ $# -eq 6 ]]' in launcher
    assert '[[ "${JOB_ID:-}" =~ ^[0-9]+$ ]]' in launcher
    assert '--scheduler-job-identity "$JOB_ID"' in launcher
    assert '"$observed_launcher_sha" == "$EXPECTED_LAUNCHER_SHA256"' in launcher
    assert '"$observed_worker_sha" == "$EXPECTED_WORKER_SHA256"' in launcher
    assert 'diff-index --quiet "$EXPECTED_GOVERNING_COMMIT"' in launcher
    assert 'git -C "$WORKTREE" ls-files --others -- scripts' in launcher
    assert 'scripts/__pycache__/*' in launcher
    assert '-X pycache_prefix=/dev/null/lvef_c3_canary' in launcher
    assert 'trap terminalize_bootstrap_failure EXIT' in launcher
    assert '--target-state CANARY_TERMINAL_FAIL' in launcher
    assert '--reason-code CANARY_STAGE_BOOTSTRAP_FAILED_NO_RETRY' in launcher
    assert launcher.index("observed_worker_sha") < launcher.index('if "$ECHOPRIME_PYTHON"')
    assert "worker_status=$?" in launcher

    # Functional shell proof: a failed child preserves its status while the
    # armed EXIT trap runs; a successful child disarms the trap.
    with tempfile.TemporaryDirectory() as trap_directory:
        marker = Path(trap_directory) / "terminalized"
        failed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "marker=$1; terminalize(){ s=$?; trap - EXIT; printf '%s' \"$s\" > \"$marker\"; exit \"$s\"; }; trap terminalize EXIT; if /bin/sh -c 'exit 7'; then trap - EXIT; exit 0; else child=$?; exit \"$child\"; fi",
                "bash",
                str(marker),
            ],
            check=False,
        )
        assert failed.returncode == 7
        assert marker.read_text() == "7"

    context = _context(Path("/synthetic/run"))
    ledger = _dispatch_ledger(context)
    worker._require_bound_scheduler_job(
        ledger, stage_id="DOWNLOAD", scheduler_job_identity="100"
    )
    for altered in (
        {**ledger, "status": "READY", "submission_count": 4},
        ledger,
    ):
        try:
            worker._require_bound_scheduler_job(
                altered,
                stage_id="DOWNLOAD",
                scheduler_job_identity="999" if altered is ledger else "100",
            )
        except worker.CanaryStageWorkerError as exc:
            assert exc.code == "CANARY_STAGE_SCHEDULER_JOB_BINDING_INVALID"
        else:
            raise AssertionError("unbound scheduler job was accepted")


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
            worker, "_require_executing_lifecycle"
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
                    worker.run_canary_stage(
                        "DOWNLOAD", context, dependencies=dependencies,
                        scheduler_job_identity="100",
                    )
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


def test_worker_waits_for_submitted_snapshot_before_claim_or_effect() -> None:
    with tempfile.TemporaryDirectory() as directory:
        context = _context(Path(directory))
        worker_wait = worker._wait_for_submitted_dispatch_ledger
        events: list[str] = []
        outcomes: list[object] = [
            worker.CanaryStageWorkerError("CANARY_STAGE_NOT_SUBMITTED"),
            {"status": "SUBMITTED"},
        ]

        def load(*_args, **_kwargs):
            events.append("read_dispatch")
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(
            worker, "_validate_context",
            return_value=({}, {"batches": [{"objects": []}]}, mock.sentinel.requirements, {}),
        ), mock.patch.object(
            worker, "_wait_for_submitted_dispatch_ledger",
            side_effect=lambda *args, **kwargs: worker_wait(
                *args, **kwargs, ledger_loader=load,
                sleep=lambda _delay: events.append("sleep")
            ),
        ), mock.patch.object(
            worker, "_require_bound_scheduler_job",
            side_effect=lambda *_args, **_kwargs: events.append("job_binding"),
        ), mock.patch.object(
            worker, "_load_predecessor_stage_result",
            side_effect=lambda *_args, **_kwargs: (events.append("predecessor"), (None, None))[1],
        ), mock.patch.object(
            worker, "_require_executing_lifecycle",
            side_effect=lambda *_args, **_kwargs: events.append("lifecycle"),
        ), mock.patch.object(
            worker, "_claim_stage_execution",
            side_effect=RuntimeError("STOP_AFTER_CONFIRMED_SUBMITTED"),
        ):
            try:
                worker.run_canary_stage(
                    "DOWNLOAD", context, scheduler_job_identity="100"
                )
            except RuntimeError as exc:
                assert str(exc) == "STOP_AFTER_CONFIRMED_SUBMITTED"
            else:
                raise AssertionError("worker unexpectedly continued")
        assert events == [
            "read_dispatch", "sleep", "read_dispatch", "job_binding",
            "predecessor", "lifecycle"
        ]


def test_worker_submission_wait_times_out_without_claim_or_effect() -> None:
    with tempfile.TemporaryDirectory() as directory:
        context = _context(Path(directory))
        worker_wait = worker._wait_for_submitted_dispatch_ledger
        clock = iter((0.0, 0.0, 0.5, 1.0))
        loader = mock.Mock(
            side_effect=worker.CanaryStageWorkerError("CANARY_STAGE_NOT_SUBMITTED")
        )
        claim = mock.Mock()
        predecessor = mock.Mock()
        download = mock.Mock()
        token_provider = mock.Mock()
        transport = mock.Mock()
        digest_worker = mock.Mock()
        dependencies = worker.CanaryStageDependencies(
            download=download,
            token_provider_factory=token_provider,
            transport_factory=transport,
            digest_worker_factory=digest_worker,
        )
        with mock.patch.object(
            worker, "_validate_context",
            return_value=({}, {"batches": [{"objects": []}]}, mock.sentinel.requirements, {}),
        ), mock.patch.object(
            worker, "_wait_for_submitted_dispatch_ledger",
            side_effect=lambda *args, **kwargs: worker_wait(
                *args,
                **kwargs,
                timeout_seconds=1.0,
                poll_seconds=0.5,
                monotonic=lambda: next(clock),
                sleep=lambda _delay: None,
                ledger_loader=loader,
            ),
        ), mock.patch.object(
            worker, "_load_predecessor_stage_result", side_effect=predecessor
        ), mock.patch.object(
            worker, "_claim_stage_execution", side_effect=claim
        ):
            try:
                worker.run_canary_stage(
                    "DOWNLOAD", context, dependencies=dependencies
                )
            except worker.CanaryStageWorkerError as exc:
                assert exc.code == "CANARY_STAGE_SUBMISSION_CONFIRMATION_TIMEOUT"
            else:
                raise AssertionError("unsubmitted stage did not time out")
        assert loader.call_count == 3
        predecessor.assert_not_called()
        claim.assert_not_called()
        download.assert_not_called()
        token_provider.assert_not_called()
        transport.assert_not_called()
        digest_worker.assert_not_called()


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
            worker, "_require_bound_scheduler_job"
        ), mock.patch.object(
            worker, "_require_executing_lifecycle"
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
                "DOWNLOAD", context, dependencies=dependencies, argv=("worker",),
                scheduler_job_identity="100",
            )
        assert result["status"] == "PASS"
        assert os.environ[context.billing_environment_variable] == prior
        del os.environ[context.billing_environment_variable]


def test_scoped_download_roots_are_closed_and_default_is_unchanged() -> None:
    import lvef_c3_orchestration_core as core

    source = (ROOT / "scripts" / "lvef_c3_orchestration_core.py").read_text()
    assert "scoped_production_root" in inspect.signature(
        core.execute_exact_batch_download
    ).parameters
    assert "direct_full_authority" in inspect.signature(
        core.execute_exact_batch_download
    ).parameters
    assert "test_only_synthetic_full_scope" in inspect.signature(
        core.execute_exact_batch_download
    ).parameters
    assert "SCOPED_DOWNLOAD_ROOT_AUTHORITY_INVALID" in source
    assert '== "lvef_multitask_c3_exact_five_canary_v1"' in source
    assert "5 <= requirements.normalized_source_objects <= 750" in source
    assert "1 <= requirements.selected_source_bytes <= 5_000_000_000" in source
    assert "DIRECT_FULL_PRODUCTION_ROOT_INVALID" in source
    assert "DIRECT_FULL_TEST_BOUNDARY_INVALID" in source


def test_live_worker_stage_paths_match_production_producers() -> None:
    source = (ROOT / "scripts" / "lvef_c3_canary_stage_worker.py").read_text()
    assert (
        'selected_batch_manifest=raw_root / BATCH_ID / "selected_batch.restricted.csv"'
        in source
    )
    assert "selected_batch_manifest.restricted.csv" not in source

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        context = _context(root / "canary_runs" / "run")
        attempt_root = context.output_root / "attempts" / context.attempt_id
        attempt_root.mkdir(parents=True, mode=0o700)
        observed = worker._create_scoped_cache_root(context)
        expected = attempt_root / "extracted_cache" / worker.BATCH_ID
        assert observed == expected
        for path in (expected.parent, expected):
            metadata = os.lstat(path)
            assert not path.is_symlink()
            assert oct(metadata.st_mode & 0o777) == "0o700"
        try:
            worker._create_scoped_cache_root(context)
        except worker.CanaryStageWorkerError as exc:
            assert exc.code == "CANARY_CACHE_ROOT_ALREADY_EXISTS"
        else:
            raise AssertionError("existing cache root was reused")
