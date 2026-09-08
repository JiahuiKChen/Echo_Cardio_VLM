from __future__ import annotations

"""Behavioral proofs for the fixed zero-payload authentication successor.

SYNTHETIC_TEST_DATA_ONLY: all content, identities, paths, and receipts below
are generated test fixtures. No credential or network operation is executed.
"""

import contextlib
import copy
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "scripts", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import lvef_c3_orchestration_core as core
import lvef_c3_full_sequential as sequential
import lvef_c3_r8u_r7h_continuation as r7h
import lvef_c3_r8u_r7h_auth_successor as successor
import test_lvef_c3_orchestration_core as core_tests


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def _assert_code(expected: str, operation) -> None:
    try:
        operation()
    except core.OrchestrationError as error:
        assert str(error) == expected, str(error)
    else:
        raise AssertionError(f"Expected {expected}")


@contextlib.contextmanager
def _terminal_authentication_fixture():
    """Keep journal construction and terminal replay real, changing only pins."""
    plan, content = core_tests._plan()
    ledger = core_tests._batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = core_tests._body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(core_tests.CONTRACT_PATH))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        output = root / "attempts" / ledger["attempt_id"] / "raw"
        output.mkdir(mode=0o700, parents=True)
        contract["storage"]["raw_root"] = str(output)
        with (
            mock.patch.dict("os.environ", {
                "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project",
                "JOB_ID": "8123400",
                "SGE_TASK_ID": "17",
            }),
            mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_ATTEMPT_ID", ledger["attempt_id"]),
            mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_PLAN_SHA256", core.canonical_json_sha256(plan)),
            mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_SCIENTIFIC_COMMIT", plan["authority"]["git_commit"]),
            mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_BATCH_IDS", tuple(row["batch_id"] for row in plan["batches"])),
        ):
            def run(*, token_provider, transport):
                return core_tests._run_download_for_crash_test(
                    plan=plan, ledger=ledger, authorization=authorization,
                    contract=contract, output=output, transport=transport,
                    token_provider=token_provider, now=now,
                )

            token_provider = mock.Mock(side_effect=core.DownloadTransportError(
                "ADC_TOKEN_ACQUISITION_FAILED", "AUTHENTICATION"
            ))
            transport = mock.Mock()
            _assert_code("DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED", lambda: run(
                token_provider=token_provider, transport=transport,
            ))
            transport.fetch.assert_not_called()
            token_provider.assert_called_once_with()
            yield SimpleNamespace(
                root=root, output=output, plan=plan, content=content,
                initial_ledger=ledger, authorization=authorization, now=now,
                contract=contract, run_original=run, original=_snapshot(output),
                batch_id=plan["batches"][0]["batch_id"],
            )


def _binding(fixture, journal_sha256: str):
    root = fixture.output.parent / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID
    root.mkdir(mode=0o700)
    (root / "credential_probe").mkdir(mode=0o700)
    (root / "scheduler").mkdir(mode=0o700)
    credential = {
        "status": "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS",
        "attempt_id": fixture.initial_ledger["attempt_id"],
        "batch_plan_sha256": core.canonical_json_sha256(fixture.plan),
        "scientific_commit": fixture.plan["authority"]["git_commit"],
        "adc_identity_matches_expected": True,
        "adc_quota_project_matches_expected": True, "source_metadata_matches_plan": True,
    }
    credential_sha = core.canonical_json_sha256(credential)
    claim_sha = core.atomic_write_json_no_clobber(
        root / "continuation_claim.restricted.json", {"synthetic_claim": True},
        attempt_id=fixture.initial_ledger["attempt_id"],
    )
    core.atomic_write_json_no_clobber(
        root / "credential_probe" / "result.restricted.json", {
            "status": "PASS_R7H_AUTH_SUCCESSOR_CREDENTIAL_PROBE",
            "implementation_commit": "f" * 40, "credential_check": credential,
            "credential_readiness_sha256": credential_sha,
        }, attempt_id=fixture.initial_ledger["attempt_id"],
    )
    core.atomic_write_json_no_clobber(
        root / "scheduler" / "array_submission.restricted.json",
        {"job_id": "8123400", "implementation_commit": "f" * 40},
        attempt_id=fixture.initial_ledger["attempt_id"],
    )
    binding = core.AuthenticationSuccessorDownloadAuthority(
        execution_id=core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID,
        implementation_commit="f" * 40,
        attempt_id=fixture.initial_ledger["attempt_id"],
        plan_sha256=core.canonical_json_sha256(fixture.plan),
        authority_receipt_sha256="1" * 64,
        consumed_terminal_journal_sha256=journal_sha256,
        consumed_reconciliation_sha256="2" * 64,
        consumed_array_submission_sha256="3" * 64,
        consumed_finalizer_submission_sha256="4" * 64,
        credential_readiness_sha256=credential_sha,
        successor_claim_sha256=claim_sha,
        successor_array_job_id="8123400",
    )
    digest = core.atomic_write_json_no_clobber(
        root / "download_authority.restricted.json",
        core.authentication_successor_download_receipt(binding),
        attempt_id=fixture.initial_ledger["attempt_id"],
    )
    return replace(binding, authority_receipt_sha256=digest)


def _consumed(fixture, *, require_pristine_tail=True):
    return core.validate_consumed_authentication_failure(
        plan=fixture.plan, requirements=core_tests._requirements(),
        authority=fixture.initial_ledger["authority"],
        attempt_id=fixture.initial_ledger["attempt_id"], output_root=fixture.output,
        require_pristine_tail=require_pristine_tail,
    )


def _run_successor(fixture, binding, *, transport=None, token_provider=None):
    return core.execute_exact_batch_download(
        plan=fixture.plan, requirements=core_tests._requirements(),
        ledger=fixture.initial_ledger, contract=fixture.contract,
        batch_id=fixture.batch_id,
        expected_runtime_authority=fixture.initial_ledger["authority"],
        authorization_receipt=fixture.authorization, output_root=fixture.output,
        launch_authority_sha256="f" * 64, argv=(),
        token_provider=token_provider or (lambda: "synthetic-token"),
        transport=transport or core_tests._synthetic_transport(fixture.content),
        now=fixture.now, sleeper=lambda _seconds: None,
        authentication_successor_authority=binding,
    )


@contextlib.contextmanager
def _controller_fixture():
    with _terminal_authentication_fixture() as fixture:
        consumed = _consumed(fixture)
        failure = next((fixture.output / fixture.batch_id / "receipts").glob("*.failure.restricted.json"))
        failure_sha = core.sha256_file(failure)
        journal_head = core.load_latest_ledger_snapshot(
            fixture.output / fixture.batch_id / "ledger", initial_ledger=fixture.initial_ledger,
        )["journal_head_sha256"]
        controls = fixture.root / "consumed_controls"
        controls.mkdir(mode=0o700)
        paths, hashes = {}, {}
        for role in successor.CONSUMED_CONTROL_SHA256:
            value = {"synthetic_control": role}
            if role == "array_submission": value["array_job_id"] = successor.CONSUMED_ARRAY_JOB_ID
            if role == "finalizer_submission": value["finalizer_job_id"] = successor.CONSUMED_FINALIZER_JOB_ID
            if role == "reconciliation": value.update({"task17_journal_sha256": journal_head, "task17_failure_receipt_sha256": failure_sha})
            path = controls / f"{role}.json"
            hashes[role] = core.atomic_write_json_no_clobber(path, value, attempt_id=fixture.initial_ledger["attempt_id"])
            paths[role] = path
        prefix_hashes = []
        for index in range(16):
            path = fixture.output.parent / "batches" / f"c3_batch_{index:03d}" / "preservation" / "batch_finalization_receipt.restricted.json"
            path.parent.mkdir(mode=0o700, parents=True)
            prefix_hashes.append(core.atomic_write_json_no_clobber(path, {"synthetic_prefix_batch": index}, attempt_id=fixture.initial_ledger["attempt_id"]))
        run = SimpleNamespace(
            plan=fixture.plan, requirements=core_tests._requirements(),
            runtime_authority=fixture.initial_ledger["authority"],
            attempt_id=fixture.initial_ledger["attempt_id"],
            attempt_root=fixture.output.parent,
        )
        with contextlib.ExitStack() as stack:
            for name, value in {
                "PRODUCTION_ROOT": fixture.root, "ATTEMPT_ROOT": fixture.output.parent,
                "ATTEMPT_ID": fixture.initial_ledger["attempt_id"],
                "PLAN_SHA256": core.canonical_json_sha256(fixture.plan),
                "SCIENTIFIC_COMMIT": fixture.plan["authority"]["git_commit"],
                "CONSUMED_PATHS": paths, "CONSUMED_CONTROL_SHA256": hashes,
                "TERMINAL_JOURNAL_SHA256": consumed["terminal_journal_sha256"],
                "TERMINAL_JOURNAL_HEAD_SHA256": journal_head,
                "TERMINAL_FAILURE_SHA256": failure_sha,
                "FINALIZER_AGGREGATE_PATH": fixture.output.parent / "cohort" / "aggregate.json",
            }.items():
                stack.enter_context(mock.patch.object(successor, name, value))
            stack.enter_context(mock.patch.object(r7h, "PREFIX_FINAL_RECEIPT_SHA256", tuple(prefix_hashes)))
            stack.enter_context(mock.patch.object(successor, "_load_run", return_value=run))
            fixture.run = run
            fixture.consumed_paths = paths
            fixture.consumed_hashes = hashes
            yield fixture


def _controller_error(code, operation):
    try:
        operation()
    except successor.AuthenticationSuccessorError as error:
        assert error.code == code
    else:
        raise AssertionError(f"Expected {code}")


def test_controller_replays_sealed_controls_and_prefix_without_republication() -> None:
    with _controller_fixture() as fixture:
        before = _snapshot(fixture.root)
        with mock.patch.object(successor, "_write") as publish:
            value = successor.load_consumed_authentication_failure(run=fixture.run)
        publish.assert_not_called()
        assert value["consumed_control_sha256"] == fixture.consumed_hashes
        assert len(value["prefix_final_receipt_sha256"]) == 16
        assert value["raw_failure_projection"]["verified_objects"] == 0
        assert _snapshot(fixture.root) == before


def test_controller_rejects_changed_consumed_controls_and_finalized_prefix() -> None:
    for role in (*successor.CONSUMED_CONTROL_SHA256, "prefix", "journal_binding"):
        with _controller_fixture() as fixture:
            if role == "journal_binding":
                with mock.patch.object(successor, "TERMINAL_JOURNAL_SHA256", "9" * 64):
                    _controller_error("R7HA_TERMINAL_DOWNLOAD_BINDING_MISMATCH", lambda: successor.load_consumed_authentication_failure(run=fixture.run))
                continue
            path = fixture.consumed_paths[role] if role != "prefix" else fixture.output.parent / "batches" / "c3_batch_000" / "preservation" / "batch_finalization_receipt.restricted.json"
            path.write_bytes(path.read_bytes() + b"\n")
            before = _snapshot(fixture.root)
            _controller_error("R7HA_CONSUMED_EVIDENCE_HASH_MISMATCH", lambda: successor.load_consumed_authentication_failure(run=fixture.run))
            assert _snapshot(fixture.root) == before


def test_controller_consumed_job_identity_is_independently_required() -> None:
    for role, field in (("array_submission", "array_job_id"), ("finalizer_submission", "finalizer_job_id")):
        with _controller_fixture() as fixture:
            path = fixture.consumed_paths[role]
            value = core.load_strict_json(path)
            value[field] = "8123999"
            path.write_bytes(core.canonical_json_bytes(value))
            changed_hashes = {**fixture.consumed_hashes, role: core.sha256_file(path)}
            with mock.patch.object(successor, "CONSUMED_CONTROL_SHA256", changed_hashes):
                _controller_error("R7HA_CONSUMED_JOB_MISMATCH", lambda: successor.load_consumed_authentication_failure(run=fixture.run))


def test_successor_capacity_observes_resources_and_preserves_consumed_receipt() -> None:
    import test_lvef_c3_r8u_r7f_capacity as capacity_tests

    plan = capacity_tests._production_scalar_plan()
    capture = successor.capacity.capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        previous = root / "original_capacity.restricted.json"
        previous.write_bytes(b'{"synthetic_historical_capacity":true}')
        previous.chmod(0o600)
        consumed_evidence = root / "consumed_evidence.restricted.json"
        consumed_evidence.write_bytes(core.canonical_json_bytes({"synthetic_consumed_evidence": True}))
        consumed_evidence.chmod(0o600)
        authority = capacity_tests._authority(root)
        new_root = root / "auth_successor"
        capacity_root = new_root / "capacity"
        calls = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(capacity_tests._plan_authority(plan))
            for name, path in {
                "SUCCESSOR_ROOT": new_root, "CAPACITY_ROOT": capacity_root,
                "CAPACITY_RECEIPT_PATH": capacity_root / "capacity.restricted.json",
                "CAPACITY_PRODUCER_PATH": capacity_root / "producer.restricted.json",
                "CAPACITY_RAW_ROOT": capacity_root / "raw_captures",
                "STATIC_PLAN_PATH": capacity_root / "static_plan.restricted.json",
                "CONSUMED_RECEIPT_PATH": new_root / "consumed.restricted.json",
                "ACCOUNT_PATH": new_root / "account.restricted.json",
            }.items():
                stack.enter_context(mock.patch.object(successor, name, path))
            stack.enter_context(mock.patch.object(r7h, "CAPACITY_RECEIPT_PATH", previous))
            stack.enter_context(mock.patch.object(r7h, "CONSUMED_EVIDENCE_PATH", consumed_evidence))
            stack.enter_context(mock.patch.object(r7h, "PREDECESSOR_CONSUMED_EVIDENCE_SHA256", core.sha256_file(consumed_evidence)))
            stack.enter_context(mock.patch.object(successor, "current_successor_commit", return_value="f" * 40))
            stack.enter_context(mock.patch.object(successor, "_load_run", return_value=SimpleNamespace(plan=plan)))
            stack.enter_context(mock.patch.object(successor, "load_consumed_authentication_failure", return_value={"sealed_zero_payload": True}))
            stack.enter_context(mock.patch.object(successor.scheduler, "build_qsub_environment", return_value=({}, "synthetic")))
            stack.enter_context(mock.patch.object(successor, "_references", return_value={"active_job_references": 0, "active_process_references": 0}))
            stack.enter_context(mock.patch.object(successor, "_topology", return_value={"status": "PASS"}))
            stack.enter_context(mock.patch.object(successor, "_build_account", return_value={"synthetic_account": True}))
            stack.enter_context(mock.patch.object(r7h, "_capacity_baselines", return_value={}))
            stack.enter_context(mock.patch.object(successor.capacity, "capture_validate_and_seal_fixed_r8u_r7f_tasks17_19_capacity", side_effect=lambda observed_plan, **kwargs: capture(observed_plan, authority=authority, **kwargs)))
            value = successor.capture_auth_successor_capacity(capacity_process_runner=capacity_tests._runner(authority, calls))
            assert calls == ["pquota", "research_findmnt", "backed_findmnt", "research_df", "backed_df"]
            assert value["status"] == "PASS_R7HA_FRESH_REMAINING_CAPACITY"
            assert value["resource_observation_count"] == 1
            assert value["pquota_commands"] == 1
            assert value["findmnt_commands"] == value["df_commands"] == 2
            assert previous.read_bytes() == b'{"synthetic_historical_capacity":true}'
            assert successor.CAPACITY_RECEIPT_PATH.is_file()
            before = _snapshot(root)
            _controller_error("R7HA_CAPACITY_OBSERVATION_ALREADY_CONSUMED", lambda: successor.capture_auth_successor_capacity(capacity_process_runner=capacity_tests._runner(authority, calls)))
            assert len(calls) == 5
            assert _snapshot(root) == before


@contextlib.contextmanager
def _submission_fixture():
    with _controller_fixture() as fixture, contextlib.ExitStack() as stack:
        root = fixture.output.parent / successor.EXECUTION_ID
        root.mkdir(mode=0o700)
        for name, path in {
            "SUCCESSOR_ROOT": root, "SCHEDULER_ROOT": root / "scheduler",
            "CLAIM_PATH": root / "continuation_claim.restricted.json",
            "ARRAY_SUBMISSION_PATH": root / "scheduler" / "array_submission.restricted.json",
            "FINALIZER_SUBMISSION_PATH": root / "scheduler" / "finalizer_submission.restricted.json",
            "SUBMISSION_PATH": root / "scheduler" / "submission.restricted.json",
            "DOWNLOAD_AUTHORITY_PATH": root / "download_authority.restricted.json",
            "WORKER_ROOT": root / "workers", "FINALIZER_AUTHORITY_PATH": root / "finalizer_authority.restricted.json",
            "FINALIZER_BINDING_PATH": root / "finalizer_binding.restricted.json",
            "CONSUMED_RECEIPT_PATH": root / "consumed.restricted.json",
            "CAPACITY_RECEIPT_PATH": root / "capacity.restricted.json",
            "PROBE_TERMINAL_PATH": root / "probe_terminal.restricted.json",
            "ACCOUNT_PATH": root / "account.restricted.json",
        }.items():
            stack.enter_context(mock.patch.object(successor, name, path))
        for path in (successor.CONSUMED_RECEIPT_PATH, successor.CAPACITY_RECEIPT_PATH, successor.PROBE_TERMINAL_PATH, successor.ACCOUNT_PATH):
            core.atomic_write_json_no_clobber(path, {"synthetic_closed_predecessor": True}, attempt_id=fixture.run.attempt_id)
        stack.enter_context(mock.patch.object(successor, "current_successor_commit", return_value="f" * 40))
        stack.enter_context(mock.patch.object(successor.scheduler, "validate_scheduler_tools"))
        environment = {"USER": "synthetic"}
        stack.enter_context(mock.patch.object(successor.scheduler, "build_qsub_environment", return_value=(environment, "synthetic")))
        stack.enter_context(mock.patch.object(successor, "_account", return_value={"qsub_environment_sha256": successor.scheduler.qsub_environment_sha256(environment)}))
        stack.enter_context(mock.patch.object(successor, "_capacity", return_value={}))
        stack.enter_context(mock.patch.object(successor, "_probe_terminal", return_value={"credential_readiness_sha256": "a" * 64}))
        stack.enter_context(mock.patch.object(successor, "_credential", return_value={"status": successor.ADC_PASS}))
        stack.enter_context(mock.patch.object(successor, "_references", return_value={"active_job_references": 0, "active_process_references": 0}))
        stack.enter_context(mock.patch.object(successor, "_topology", return_value={}))
        stack.enter_context(mock.patch.object(successor, "_script_authority", return_value={"synthetic": "a" * 64}))
        fixture.successor_root = root
        yield fixture


def test_successor_submitter_captures_array_then_exact_held_finalizer_once() -> None:
    with _submission_fixture() as fixture:
        preserved = {role: path.read_bytes() for role, path in fixture.consumed_paths.items()}
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            assert kwargs["env"] == {"USER": "synthetic"}
            assert argv[argv.index("-r") + 1] == "n"
            if len(calls) == 1:
                assert successor.CLAIM_PATH.is_file()
                assert argv[argv.index("-t") + 1] == "17-19"
                assert argv[argv.index("-tc") + 1] == "1"
                assert "gpus=1" in argv
                return subprocess.CompletedProcess(argv, 0, b"8123400.17-19:1\n", b"")
            assert successor.ARRAY_SUBMISSION_PATH.is_file()
            assert argv[argv.index("-hold_jid") + 1] == "8123400"
            assert not any(value.startswith("gpus=") for value in argv)
            return subprocess.CompletedProcess(argv, 0, b"8123401\n", b"")

        result = successor.submit_auth_successor(qsub_runner=runner)
        assert len(calls) == 2
        assert result["array_job_id"] == result["held_on_array_job_id"] == "8123400"
        assert result["finalizer_job_id"] == "8123401"
        assert result["task_range"] == "17-19" and result["array_max_concurrency"] == 1
        for role, payload in preserved.items():
            assert fixture.consumed_paths[role].read_bytes() == payload
        assert not successor.FINALIZER_AUTHORITY_PATH.exists()
        before = _snapshot(fixture.root)
        _controller_error("R7HA_SUBMISSION_ALREADY_CLAIMED", lambda: successor.submit_auth_successor(qsub_runner=runner))
        assert len(calls) == 2 and _snapshot(fixture.root) == before


def test_ambiguous_successor_qsub_preserves_claim_and_cannot_be_retried() -> None:
    with _submission_fixture() as fixture:
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"ambiguous synthetic scheduler response\n", b""))
        try:
            successor.submit_auth_successor(qsub_runner=runner)
        except (successor.AuthenticationSuccessorError, r7h.R7HContinuationError):
            pass
        else:
            raise AssertionError("Ambiguous qsub response was accepted")
        runner.assert_called_once()
        assert successor.CLAIM_PATH.is_file()
        assert (successor.SCHEDULER_ROOT / "array.qsub.stdout.restricted").is_file()
        assert not successor.FINALIZER_SUBMISSION_PATH.exists()
        before = _snapshot(fixture.root)
        _controller_error("R7HA_SUBMISSION_ALREADY_CLAIMED", lambda: successor.submit_auth_successor(qsub_runner=runner))
        runner.assert_called_once()
        assert _snapshot(fixture.root) == before


def test_missing_tail_receipt_stops_finalizer_before_new_authority_publication() -> None:
    with _submission_fixture() as fixture:
        before = _snapshot(fixture.root)
        with (
            mock.patch.object(successor, "validate_successor_worker_submission"),
            mock.patch.object(successor, "_validate_continuation", return_value={"array_job_id": "8123400", "finalizer_job_id": "8123401"}),
            mock.patch.object(successor, "_account", return_value={"sealed_qsub_environment": {"USER": "synthetic"}}),
            mock.patch.object(successor.finalizer, "_validate_current_receipt_v3") as receipt_validation,
            mock.patch.object(successor, "_write") as publication,
            mock.patch.object(successor.finalizer, "finalize_receipts") as finalize,
            mock.patch.dict("os.environ", {"JOB_ID": "8123401"}),
        ):
            try:
                successor.run_auth_successor_finalizer()
            except successor.finalizer.ProductionFinalizationError as error:
                assert str(error).startswith("R7HA_BATCH_RECEIPT")
            else:
                raise AssertionError("Missing tail receipt reached finalization")
            assert receipt_validation.call_count == 16
            publication.assert_not_called()
            finalize.assert_not_called()
        assert _snapshot(fixture.root) == before
        assert not successor.FINALIZER_AUTHORITY_PATH.exists()
        assert not successor.FINALIZER_BINDING_PATH.exists()


def test_finalizer_recorded_binding_replays_at_login_and_rejects_foreign_tail_jobs() -> None:
    import test_lvef_c3_r8u_r7h_finalizer as finalizer_tests

    authority = finalizer_tests._authority()
    value = {"array_job_id": "8123400", "finalizer_job_id": "8123401"}
    for foreign in (False, True):
        receipts = [{"batch_id": f"c3_batch_{index:03d}", "scheduler_job_identity": "8123400"} for index in range(19)]
        if foreign: receipts[17]["scheduler_job_identity"] = successor.CONSUMED_ARRAY_JOB_ID

        def read(path):
            if path == successor.FINALIZER_BINDING_PATH: return value, "a" * 64
            assert path == successor.FINALIZER_AUTHORITY_PATH
            return r7h._finalizer_authority_payload(authority), "b" * 64

        with (
            mock.patch.object(successor, "current_successor_commit", return_value="f" * 40),
            mock.patch.object(successor, "_load_run", return_value=object()),
            mock.patch.object(successor, "load_consumed_authentication_failure"),
            mock.patch.object(successor, "_read", side_effect=read),
            mock.patch.object(successor, "_finalizer_binding_payload", return_value=value),
            mock.patch.object(successor, "_finalizer_authority", return_value=authority),
            mock.patch.object(successor, "_validate_recorded_worker") as recorded,
            mock.patch.object(successor, "_write") as write,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            operation = lambda: successor.validate_finalizer_successor_binding(authority, binding_path=successor.FINALIZER_BINDING_PATH, receipts=receipts)
            if foreign:
                _controller_error("R7HA_TAIL_RECEIPT_JOB_SUBSTITUTION", operation)
                recorded.assert_called_once_with("finalizer", None)
            else:
                operation()
                assert recorded.call_args_list == [mock.call("finalizer", None), mock.call("array", "17"), mock.call("array", "18"), mock.call("array", "19")]
            write.assert_not_called()


def test_actual_runner_case_routes_successor_roles_to_their_cli_entrypoints() -> None:
    source = successor.RUNNER_PATH.read_text(encoding="utf-8")
    dispatch = "case \"$JOB_NAME\" in" + source.split('case "$JOB_NAME" in', 1)[1].split("\nesac", 1)[0] + "\nesac\n"
    script = "set -eu\n" + dispatch + '\nprintf "%s\\n%s\\n%s\\n" "$CONTROLLER" "$MODE" "$CUDA_VISIBLE_DEVICES"\n'
    for label, task, slots, expected_mode, expected_function in (
        ("ctx", "17", "1", "probe", "run_auth_successor_probe"),
        ("seq", "17", "4", "array", "run_auth_successor_array_task"),
        ("seq", "18", "4", "array", "run_auth_successor_array_task"),
        ("seq", "19", "4", "array", "run_auth_successor_array_task"),
        ("fin", "undefined", "4", "finalizer", "run_auth_successor_finalizer"),
    ):
        environment = {"WORKTREE": "/synthetic/worktree", "JOB_NAME": f"lvef_c3_r8u_r7ha_{label}_ffffffff", "SGE_TASK_ID": task, "NSLOTS": slots, "CUDA_VISIBLE_DEVICES": "0"}
        completed = subprocess.run(["/bin/bash", "-c", script], env=environment, capture_output=True, text=True, check=False)
        assert completed.returncode == 0 and completed.stderr == ""
        controller, mode, cuda = completed.stdout.splitlines()
        assert controller == "/synthetic/worktree/scripts/lvef_c3_r8u_r7h_auth_successor.py"
        assert mode == expected_mode
        assert cuda == ("0" if label == "seq" else "")
        with mock.patch.object(successor, expected_function, return_value={"status": "SYNTHETIC_DISPATCH_PASS"}) as entrypoint, mock.patch("builtins.print"):
            assert successor.main([mode]) == 0
        entrypoint.assert_called_once_with()
        if label == "seq":
            invalid = subprocess.run(["/bin/bash", "-c", script], env={**environment, "SGE_TASK_ID": "16"}, capture_output=True, text=True, check=False)
            assert invalid.returncode == 78 and invalid.stdout == ""


def test_consumed_authentication_replay_proves_exact_zero_payload_controls() -> None:
    with _terminal_authentication_fixture() as fixture:
        evidence = _consumed(fixture)
        assert evidence["status"] == "PASS_R7H_ZERO_PAYLOAD_AUTHENTICATION_FAILURE"
        assert evidence["attempts_used"] == 1
        assert evidence["verified_objects"] == evidence["retained_payload_files"] == 0
        assert evidence["pristine_tail_checked"] is True
        assert len(evidence["journal_file_sha256"]) == 4
        assert len(evidence["control_file_sha256"]) == 8
        assert _snapshot(fixture.output) == fixture.original


def test_consumed_authentication_replay_rejects_payload_and_changed_failure() -> None:
    for mutation in ("objects", "partials", "failure", "boolean_attempt", "extra_control", "journal"):
        with _terminal_authentication_fixture() as fixture:
            batch = fixture.output / fixture.batch_id
            if mutation in {"objects", "partials"}:
                (batch / mutation / "unexpected.dcm").write_bytes(b"synthetic payload")
                expected = "AUTH_SUCCESSOR_RETAINED_PAYLOAD_PRESENT"
            elif mutation in {"failure", "boolean_attempt"}:
                failure = next((batch / "receipts").glob("*.failure.restricted.json"))
                value = core.load_strict_json(failure)
                if mutation == "failure": value["failure_code"] = "AUTHORIZATION"
                else: value["attempts_used"] = True
                failure.write_bytes(core.canonical_json_bytes(value))
                expected = "AUTH_SUCCESSOR_TERMINAL_RECEIPT_INVALID"
            elif mutation == "journal":
                journal = next((batch / "ledger").iterdir())
                journal.write_bytes(journal.read_bytes() + b"\n")
                expected = "AUTH_SUCCESSOR_JOURNAL_INVALID"
            else:
                (batch / "unbound_control.json").write_bytes(b"{}")
                expected = "AUTH_SUCCESSOR_PRISTINE_TAIL_INVALID"
            before = _snapshot(fixture.output)
            _assert_code(expected, lambda: _consumed(fixture))
            assert _snapshot(fixture.output) == before


def test_bound_successor_download_uses_new_journal_and_preserves_original_terminal_bytes() -> None:
    with _terminal_authentication_fixture() as fixture:
        consumed = _consumed(fixture)
        binding = _binding(fixture, consumed["terminal_journal_sha256"])
        result = _run_successor(fixture, binding)
        assert result["batches"][fixture.batch_id]["state"] == "DOWNLOAD_VERIFIED"
        original_terminal = core.load_latest_ledger_snapshot(
            fixture.output / fixture.batch_id / "ledger",
            initial_ledger=fixture.initial_ledger,
        )
        assert original_terminal["batches"][fixture.batch_id]["state"] == "FAILED_NONRETRYABLE"
        for relative, payload in fixture.original.items():
            assert (fixture.output / relative).read_bytes() == payload
        root = fixture.output / fixture.batch_id
        execution = root / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID
        assert list((execution / "ledger").iterdir())
        assert len(list((execution / "receipts").glob("*.verification.json"))) == fixture.plan["batches"][0]["n_objects"]
        assert not list((root / "receipts").glob("*.verification.json"))
        assert (root / "verified_download_manifest.restricted.csv").is_file()
        replay = _consumed(fixture, require_pristine_tail=False)
        assert replay["terminal_journal_sha256"] == consumed["terminal_journal_sha256"]
        assert replay["terminal_control_sha256"] == consumed["terminal_control_sha256"]


def test_successor_download_rejects_substituted_binding_before_new_controls() -> None:
    changes = (
        {"plan_sha256": "9" * 64}, {"attempt_id": "foreign_attempt"},
        {"successor_array_job_id": "7489283"}, {"successor_array_job_id": "8123499"},
        {"consumed_terminal_journal_sha256": "9" * 64},
        {"authority_receipt_sha256": "9" * 64},
        {"credential_readiness_sha256": "9" * 64},
        {"successor_claim_sha256": "9" * 64},
        {"credential_readiness_sha256": ""}, {"execution_id": "unbound_namespace"},
    )
    for change in changes:
        with _terminal_authentication_fixture() as fixture:
            binding = _binding(fixture, _consumed(fixture)["terminal_journal_sha256"])
            provider = mock.Mock(return_value="synthetic-token")
            transport = mock.Mock()
            expected = (
                "AUTH_SUCCESSOR_DOWNLOAD_RECEIPT_MISMATCH" if set(change) & {
                    "consumed_terminal_journal_sha256", "authority_receipt_sha256",
                    "successor_claim_sha256",
                } or change.get("credential_readiness_sha256") == "9" * 64
                else "AUTH_SUCCESSOR_DOWNLOAD_AUTHORITY_INVALID"
            )
            _assert_code(expected, lambda: _run_successor(
                fixture, replace(binding, **change), token_provider=provider, transport=transport,
            ))
            provider.assert_not_called()
            transport.fetch.assert_not_called()
            assert _snapshot(fixture.output) == fixture.original


def test_successor_source_mutations_and_late_payload_cannot_create_download_journal() -> None:
    for mutation in ("claim", "credential", "array", "task", "objects", "partials"):
        with _terminal_authentication_fixture() as fixture:
            binding = _binding(fixture, _consumed(fixture)["terminal_journal_sha256"])
            controller = fixture.output.parent / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID
            if mutation == "claim":
                path = controller / "continuation_claim.restricted.json"
                path.write_bytes(core.canonical_json_bytes({"synthetic_claim": "changed"}))
                expected = "AUTH_SUCCESSOR_CLAIM_CHANGED"
            elif mutation == "credential":
                path = controller / "credential_probe" / "result.restricted.json"
                value = core.load_strict_json(path)
                value["credential_check"]["status"] = "ADC_REAUTH_REQUIRED"
                path.write_bytes(core.canonical_json_bytes(value))
                expected = "AUTH_SUCCESSOR_CREDENTIAL_SOURCE_INVALID"
            elif mutation == "array":
                path = controller / "scheduler" / "array_submission.restricted.json"
                value = core.load_strict_json(path)
                value["job_id"] = "8123499"
                path.write_bytes(core.canonical_json_bytes(value))
                expected = "AUTH_SUCCESSOR_ARRAY_SUBMISSION_MISMATCH"
            elif mutation in {"objects", "partials"}:
                (fixture.output / fixture.batch_id / mutation / "unbound.dcm").write_bytes(b"synthetic")
                expected = "AUTH_SUCCESSOR_RETAINED_PAYLOAD_PRESENT"
            else:
                expected = "AUTH_SUCCESSOR_DOWNLOAD_AUTHORITY_INVALID"
            before = _snapshot(fixture.root)
            provider, transport = mock.Mock(), mock.Mock()
            with mock.patch.dict("os.environ", {"SGE_TASK_ID": "18" if mutation == "task" else "17"}):
                _assert_code(expected, lambda: _run_successor(fixture, binding, token_provider=provider, transport=transport))
            provider.assert_not_called()
            transport.fetch.assert_not_called()
            assert _snapshot(fixture.root) == before


def test_new_authentication_failure_is_terminal_in_successor_without_retry_storm() -> None:
    with _terminal_authentication_fixture() as fixture:
        binding = _binding(fixture, _consumed(fixture)["terminal_journal_sha256"])
        provider = mock.Mock(side_effect=core.DownloadTransportError(
            "ADC_TOKEN_ACQUISITION_FAILED", "AUTHENTICATION"
        ))
        transport = mock.Mock()
        _assert_code("DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED", lambda: _run_successor(
            fixture, binding, token_provider=provider, transport=transport,
        ))
        provider.assert_called_once_with()
        transport.fetch.assert_not_called()
        journal = fixture.output / fixture.batch_id / binding.execution_id / "ledger"
        result = core.load_latest_ledger_snapshot(journal, initial_ledger=fixture.initial_ledger)
        batch = result["batches"][fixture.batch_id]
        assert batch["state"] == "FAILED_NONRETRYABLE"
        assert sum(batch["download_attempts"].values()) == 1
        assert batch["download_verification_receipts"] == {}
        for relative, payload in fixture.original.items():
            assert (fixture.output / relative).read_bytes() == payload
        before = _snapshot(fixture.output)
        _assert_code("DOWNLOAD_BATCH_NOT_RESUMABLE", lambda: _run_successor(fixture, binding))
        assert _snapshot(fixture.output) == before


def test_successor_rechecks_credentials_immediately_before_download_side_effects() -> None:
    class DownloadBoundaryReached(BaseException):
        pass

    for status in (None, "ADC_REAUTH_REQUIRED", "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = SimpleNamespace(
                governing_commit=r7h.SCIENTIFIC_COMMIT,
                checkpoint=root / "checkpoint", environment_receipt=root / "environment",
                billing_variable="SYNTHETIC_R7H_BILLING", billing_project="synthetic-project",
            )
            run = SimpleNamespace(
                plan={"batches": [{"ordinal": index, "batch_id": f"c3_batch_{index:03d}", "objects": []} for index in range(19)]},
                authority=authority, attempt_id=r7h.ATTEMPT_ID, plan_sha256=r7h.PLAN_SHA256,
                attempt_root=root / "attempt", production_root=root,
                runtime_authority={"environment_receipt_sha256": "a" * 64, "checkpoint_sha256": "a" * 64},
                requirements=SimpleNamespace(), contract={}, contract_path=root / "contract",
                plan_path=root / "plan", launch_authority={}, launch_authority_sha256="a" * 64,
                scheduler_job_identity="8123400", authentication_successor_download_authority=object(),
            )
            credential = None if status is None else mock.Mock(return_value={
                "status": status, "attempt_id": run.attempt_id,
                "batch_plan_sha256": run.plan_sha256,
                "scientific_commit": authority.governing_commit,
                "batch_id": "c3_batch_016", "job_id": run.scheduler_job_identity,
            })
            dependency = sequential.FullDependencies(
                prior_batch_validator=lambda **_kwargs: None,
                environment_validator=lambda *_args, **_kwargs: None,
                r8u_r7h_worker_submission_validator=lambda **_kwargs: None,
                execution_context=sequential.R8U_R7H_FIXED_CONTINUATION,
                pre_download_validator=credential,
            )
            with (
                mock.patch.object(sequential, "validate_r8u_r7h_extraction_cache_topology"),
                mock.patch.object(sequential, "_validate_full_run"),
                mock.patch.object(sequential, "_ensure_private_directory") as directory_creation,
                mock.patch.object(core, "sha256_file", return_value="a" * 64),
                mock.patch.object(core, "initialize_resume_ledger", side_effect=DownloadBoundaryReached()) as journal,
                mock.patch.object(sequential, "_provider_and_transport") as provider,
                mock.patch.dict("os.environ", {"JOB_ID": "8123400"}),
            ):
                try:
                    sequential.run_batch_task(task_id=17, run=run, dependencies=dependency)
                except DownloadBoundaryReached:
                    assert status == "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS"
                    assert credential.call_count == 1
                    journal.assert_called_once()
                except sequential.FullSequentialError as error:
                    assert status != "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS"
                    assert error.code == (
                        "AUTH_SUCCESSOR_FRESH_CREDENTIAL_CHECK_REQUIRED" if status is None
                        else "AUTH_SUCCESSOR_FRESH_CREDENTIAL_CHECK_INVALID"
                    )
                    assert error.stage == "DOWNLOAD"
                    directory_creation.assert_not_called()
                    journal.assert_not_called()
                else:
                    raise AssertionError("Expected credential gate or first download boundary")
                provider.assert_not_called()
