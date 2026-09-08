from __future__ import annotations

"""Synthetic-only publication and held-release recovery contracts."""

import contextlib
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "scripts", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import lvef_c3_orchestration_core as core
import lvef_c3_r8u_r7h_auth_publication_successor as publication
import test_lvef_c3_r8u_r7h_auth_successor as previous_tests
import test_lvef_c3_r8u_r7h_continuation as runtime_tests


@contextlib.contextmanager
def _publication_failure_fixture():
    """Real 19-batch plan, original terminal journal, and fixed v1 seal replay."""
    tests = previous_tests.core_tests
    selected_base, splits_base, source_base, metadata_base, content_base = tests._fixture_rows()
    payload = next(iter(content_base.values()))
    selected, splits, source, metadata, content = [], [], [], [], {}
    for index in range(37):
        subject, study = str(10000001 + index), str(20000001 + index)
        relative = f"files/p10/p{subject}/s{study}/synthetic.dcm"
        key = hashlib.sha256(f"mimic-iv-echo/1.0\0{relative}".encode()).hexdigest()
        selected.append({"subject_id": subject, "study_id": study})
        splits.append({"subject_id": subject, "split": "train"})
        row = {**source_base[0], "subject_id": subject, "study_id": study, "split": "train", "source_relative_path": relative, "source_object_key": key}
        source.append(row)
        metadata.append({**metadata_base[0], **row, "production_batch": f"c3_batch_{index // 2:03d}"})
        content[key] = payload
    requirements = replace(tests._requirements(), selected_studies=37, selected_subjects=37, normalized_source_objects=37, selected_source_bytes=37 * len(payload), batch_count=19)
    enriched = core.reconcile_selected_source_metadata(source, metadata, release="mimic-iv-echo/1.0")
    plan = core.build_immutable_batch_plan(selected, enriched, splits, requirements=requirements, authority=tests._authority())
    attempt = "lvef_c3_synthetic_publication_attempt"
    runtime = {**tests._authority(), "batch_plan_sha256": core.canonical_json_sha256(plan)}
    initial = core.initialize_resume_ledger(plan, requirements=requirements, attempt_id=attempt, authority=runtime, batch_ids=["c3_batch_016"])
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = {**tests._body_authorization(plan, initial, now=now), "scope": "REMAINING_BATCHES", "batch_ids": ["c3_batch_016"]}
    with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
        root = Path(directory).resolve()
        output = root / "attempts" / attempt / "raw"
        output.mkdir(mode=0o700, parents=True)
        contract = copy.deepcopy(core.load_orchestration_contract(tests.CONTRACT_PATH))
        contract["storage"]["raw_root"] = str(output)
        for name, value in {"AUTHENTICATION_SUCCESSOR_ATTEMPT_ID": attempt, "AUTHENTICATION_SUCCESSOR_PLAN_SHA256": core.canonical_json_sha256(plan), "AUTHENTICATION_SUCCESSOR_SCIENTIFIC_COMMIT": runtime["git_commit"]}.items():
            stack.enter_context(mock.patch.object(core, name, value))
        stack.enter_context(mock.patch.dict("os.environ", {"JOB_ID": "8123400", "SGE_TASK_ID": "17", "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"}))
        arguments = dict(plan=plan, requirements=requirements, ledger=initial, contract=contract, batch_id="c3_batch_016", expected_runtime_authority=runtime, authorization_receipt=authorization, output_root=output, launch_authority_sha256="f" * 64, argv=(), now=now, sleeper=lambda _seconds: None)
        provider = mock.Mock(side_effect=core.DownloadTransportError("ADC_TOKEN_ACQUISITION_FAILED", "AUTHENTICATION"))
        previous_tests._assert_code("DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED", lambda: core.execute_exact_batch_download(**arguments, token_provider=provider, transport=mock.Mock()))
        consumed = core.validate_consumed_authentication_failure(plan=plan, requirements=requirements, authority=runtime, attempt_id=attempt, output_root=output)
        v1_root = output.parent / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID
        v1_root.mkdir(mode=0o700)

        def write(relative, value):
            path = v1_root / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            return core.atomic_write_json_no_clobber(path, value, attempt_id=attempt)

        control_hashes = {role: write(relative, {"synthetic_control_role": role}) for role, relative in core.AUTHENTICATION_SUCCESSOR_V1_CONTROL_PATHS.items()}
        worker_hashes = {role: write(relative, {"synthetic_worker_role": role}) for role, relative in core.AUTHENTICATION_SUCCESSOR_V1_WORKER_PATHS.items()}
        evidence = {field: write(relative, {"synthetic_scheduler_evidence": field}) for field, relative in core.AUTHENTICATION_SUCCESSOR_V1_EVIDENCE_PATHS.items()}
        prefix = []
        for index in range(16):
            path = output.parent / "batches" / f"c3_batch_{index:03d}" / "preservation" / "batch_finalization_receipt.restricted.json"
            path.parent.mkdir(mode=0o700, parents=True)
            prefix.append(core.atomic_write_json_no_clobber(path, {"synthetic_finalized_prefix": index}, attempt_id=attempt))
        manifest = {
            "schema_version": 1, "artifact_type": "lvef_c3_r7ha_v1_publication_failure_v1", "status": "PASS_R7HA_V1_QUIESCENT_ZERO_PAYLOAD_PUBLICATION_FAILURE",
            "execution_id": core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID, "attempt_id": attempt, "batch_plan_sha256": core.canonical_json_sha256(plan), "scientific_commit": runtime["git_commit"],
            "implementation_commit": core.AUTHENTICATION_SUCCESSOR_V1_IMPLEMENTATION_COMMIT,
            "array_job_id": "7491124", "finalizer_job_id": "7491257", "probe_job_id": "7491004",
            "task17_failure_code": "R7HA_SUBMISSION_RECEIPT_TIMEOUT", "task17_exit_status": 78, "task17_failed": 0,
            "active_execution_jobs": 0, "active_execution_processes": 0, "tail_payload_files": 0, "v1_nested_journal_roots": 0,
            "task17_worker_receipt_present": False, "task17_predownload_credential_receipt_present": False, "v1_predownload_credential_receipts": 0,
            "scientific_stage_replayed": False, "identifiers_emitted": False, "restricted_paths_emitted": False,
            "observed_utc": now.isoformat(), "original_terminal_journal_sha256": consumed["terminal_journal_sha256"],
            "v1_control_sha256": control_hashes, "v1_worker_control_sha256": worker_hashes, "prefix_final_receipt_sha256": prefix, **evidence,
        }
        digest = write("publication_failure.restricted.json", manifest)
        stack.enter_context(mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_V1_PUBLICATION_FAILURE_SHA256", digest))
        yield SimpleNamespace(root=root, output=output, plan=plan, content=content, requirements=requirements, initial_ledger=initial, arguments=arguments, batch_id="c3_batch_016", v1_root=v1_root, manifest=manifest, manifest_sha256=digest, consumed=consumed)


def _validate_failure(fixture, *, pristine=True):
    return core.validate_consumed_publication_failure(plan=fixture.plan, requirements=fixture.requirements, authority=fixture.initial_ledger["authority"], attempt_id=fixture.initial_ledger["attempt_id"], output_root=fixture.output, require_pristine_tail=pristine)


def _v2_binding(fixture):
    # Reuse the existing private credential/claim fixture, then publish the
    # distinct v2 schema before exercising any production download call.
    with mock.patch.object(core, "AUTHENTICATION_SUCCESSOR_EXECUTION_ID", core.AUTHENTICATION_SUCCESSOR_V2_EXECUTION_ID):
        prototype = previous_tests._binding(fixture, fixture.consumed["terminal_journal_sha256"])
    binding = core.AuthenticationSuccessorDownloadAuthorityV2(**vars(prototype), consumed_publication_failure_sha256=fixture.manifest_sha256)
    root = fixture.output.parent / core.AUTHENTICATION_SUCCESSOR_V2_EXECUTION_ID
    probe = root / "credential_probe" / "result.restricted.json"
    value = core.load_strict_json(probe)
    value["status"] = "PASS_R7H_AUTH_PUBLICATION_SUCCESSOR_CREDENTIAL_PROBE"
    probe.write_bytes(core.canonical_json_bytes(value))
    receipt = root / "download_authority.restricted.json"
    receipt.write_bytes(core.canonical_json_bytes(core.authentication_successor_download_receipt(binding)))
    return replace(binding, authority_receipt_sha256=core.sha256_file(receipt))


def test_v2_cold_download_binds_real_sealed_v1_failure_and_preserves_all_history() -> None:
    with _publication_failure_fixture() as fixture:
        assert _validate_failure(fixture) == fixture.manifest
        before = previous_tests._snapshot(fixture.root)
        binding = _v2_binding(fixture)
        result = core.execute_exact_batch_download(**fixture.arguments, authentication_successor_authority=binding, token_provider=lambda: "synthetic-token", transport=previous_tests.core_tests._synthetic_transport(fixture.content))
        assert result["batches"][fixture.batch_id]["state"] == "DOWNLOAD_VERIFIED"
        for relative, payload in before.items():
            assert (fixture.root / relative).read_bytes() == payload
        assert not (fixture.output / fixture.batch_id / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID).exists()
        assert (fixture.output / fixture.batch_id / binding.execution_id / "ledger").is_dir()
        assert _validate_failure(fixture, pristine=False) == fixture.manifest


def test_v2_rejects_changed_consumed_manifest_claim_job_and_v1_execution() -> None:
    for mutation in ("manifest", "manifest_binding", "claim", "worker", "evidence", "job", "v1_journal", "payload"):
        with _publication_failure_fixture() as fixture:
            binding = _v2_binding(fixture)
            if mutation == "manifest":
                path = fixture.v1_root / "publication_failure.restricted.json"
                path.write_bytes(path.read_bytes() + b"\n")
                expected = "AUTH_SUCCESSOR_V1_PUBLICATION_FAILURE_CHANGED"
            elif mutation == "manifest_binding":
                binding = replace(binding, consumed_publication_failure_sha256="a" * 64)
                receipt = fixture.output.parent / binding.execution_id / "download_authority.restricted.json"
                receipt.write_bytes(core.canonical_json_bytes(core.authentication_successor_download_receipt(binding)))
                binding = replace(binding, authority_receipt_sha256=core.sha256_file(receipt))
                expected = "AUTH_SUCCESSOR_V1_PUBLICATION_FAILURE_CHANGED"
            elif mutation == "claim":
                (fixture.v1_root / core.AUTHENTICATION_SUCCESSOR_V1_CONTROL_PATHS["claim"]).write_bytes(b"{}")
                expected = "AUTH_SUCCESSOR_V1_CONTROLS_CHANGED"
            elif mutation == "worker":
                (fixture.v1_root / core.AUTHENTICATION_SUCCESSOR_V1_WORKER_PATHS["array_task_18"]).write_bytes(b"{}")
                expected = "AUTH_SUCCESSOR_V1_CONTROLS_CHANGED"
            elif mutation == "evidence":
                (fixture.v1_root / core.AUTHENTICATION_SUCCESSOR_V1_EVIDENCE_PATHS["task17_qacct_sha256"]).write_bytes(b"{}")
                expected = "AUTH_SUCCESSOR_V1_SCHEDULER_EVIDENCE_CHANGED"
            elif mutation == "job":
                binding = replace(binding, successor_array_job_id="7491257")
                expected = "AUTH_SUCCESSOR_DOWNLOAD_AUTHORITY_INVALID"
            elif mutation == "v1_journal":
                (fixture.output / fixture.batch_id / core.AUTHENTICATION_SUCCESSOR_EXECUTION_ID).mkdir(mode=0o700)
                expected = "AUTH_SUCCESSOR_V1_EXECUTION_APPEARED"
            else:
                (fixture.output / fixture.batch_id / "objects" / "unbound.dcm").write_bytes(b"synthetic")
                expected = "AUTH_SUCCESSOR_RETAINED_PAYLOAD_PRESENT"
            before = previous_tests._snapshot(fixture.root)
            provider, transport = mock.Mock(), mock.Mock()
            previous_tests._assert_code(expected, lambda: core.execute_exact_batch_download(**fixture.arguments, authentication_successor_authority=binding, token_provider=provider, transport=transport))
            provider.assert_not_called()
            transport.fetch.assert_not_called()
            assert previous_tests._snapshot(fixture.root) == before


def _raises(code, operation):
    try:
        operation()
    except publication.AuthenticationSuccessorError as error:
        assert error.code == code, error.code
    else:
        raise AssertionError(f"Expected {code}")


@contextlib.contextmanager
def _held_submission_fixture(*, with_probe=True):
    # The real consumed-source graph has its own 19-batch fixture above. Reuse
    # the existing scheduler fixture for independent publication/release tests.
    with mock.patch.object(previous_tests, "successor", publication), previous_tests._submission_fixture() as fixture, contextlib.ExitStack() as stack:
        root = fixture.successor_root
        probe = root / "credential_probe"
        for name, path in {
            "RELEASE_CLAIM_PATH": root / "release_claim.restricted.json",
            "RELEASE_PATH": root / "release.restricted.json",
            "RELEASE_CAPTURE_ROOT": root / "release_capture",
            "PROBE_ROOT": probe, "PROBE_SCHEDULER_ROOT": probe / "scheduler",
            "PROBE_AUTHORITY_PATH": probe / "authority.restricted.json",
            "PROBE_SUBMISSION_PATH": probe / "submission.restricted.json",
            "PROBE_RELEASE_CLAIM_PATH": probe / "release_claim.restricted.json",
            "PROBE_RELEASE_PATH": probe / "release.restricted.json",
            "PROBE_RELEASE_CAPTURE_ROOT": probe / "release_capture",
        }.items():
            stack.enter_context(mock.patch.object(publication, name, path))
        environment = {"USER": "synthetic"}
        stack.enter_context(mock.patch.object(publication, "_account", return_value={"sealed_qsub_environment": environment, "qsub_environment_sha256": publication.scheduler.qsub_environment_sha256(environment)}))
        stack.enter_context(mock.patch.object(publication, "load_consumed_authentication_failure", return_value={}))
        stack.enter_context(mock.patch.object(publication, "_replay_credential"))
        stack.enter_context(mock.patch.object(publication, "_release_tool_authority", return_value={"invoked_path": str(publication.QRLS_PATH), "link_target": "qalter", "resolved_path": str(publication.QRLS_TARGET_PATH), "resolved_sha256": "c" * 64}))
        if with_probe:
            publication.submit_auth_successor_probe(
                qsub_runner=mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"8123500.17-17:1\n", b"")),
                qstat_runner=mock.Mock(return_value=subprocess.CompletedProcess([], 0, _held_xml(role="probe", job_id="8123500"), b"")),
                qrls_runner=mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"", b"")),
            )
        yield fixture


def _held_xml(*, role="array", job_id="8123400", tasks=None, state="hqw", owner="synthetic", name=None):
    task_text = tasks or ("17" if role == "probe" else "17-19:1")
    return (f'<job_info><queue_info><job_list state="pending"><JB_job_number>{job_id}</JB_job_number>'
        f'<JB_name>{name or publication._job_name(role)}</JB_name><JB_owner>{owner}</JB_owner>'
        f'<state>{state}</state><tasks>{task_text}</tasks></job_list></queue_info><job_info/></job_info>').encode()


def _qstat_runner(argv, **kwargs):
    assert argv == [str(publication.scheduler.QSTAT_PATH), "-xml", "-u", "synthetic"]
    assert kwargs["env"] == {"USER": "synthetic"}
    return subprocess.CompletedProcess(argv, 0, _held_xml(), b"")


def _array_and_finalizer_runner(calls):
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["env"] == {"USER": "synthetic"}
        assert argv[argv.index("-r") + 1] == "n"
        if len(calls) == 1:
            assert publication.CLAIM_PATH.is_file()
            assert argv[argv.index("-clear") + 1] == "-h"
            assert argv[argv.index("-t") + 1] == "17-19"
            assert argv[argv.index("-tc") + 1] == "1"
            return subprocess.CompletedProcess(argv, 0, b"8123400.17-19:1\n", b"")
        assert len(calls) == 2
        assert publication.ARRAY_SUBMISSION_PATH.is_file()
        assert argv[argv.index("-hold_jid") + 1] == "8123400"
        assert "-h" not in argv
        return subprocess.CompletedProcess(argv, 0, b"8123401\n", b"")
    return run


def test_held_array_survives_own_readback_failure_and_releases_once_after_complete_graph() -> None:
    with _held_submission_fixture() as fixture:
        preserved = {role: path.read_bytes() for role, path in fixture.consumed_paths.items()}
        qsubs, releases, failed_readback = [], [], []
        read = publication._read

        def fail_first_array_readback(path):
            if path == publication.ARRAY_SUBMISSION_PATH and not failed_readback:
                failed_readback.append(path)
                raise OSError("synthetic post-publication readback failure")
            return read(path)

        def release(argv, **kwargs):
            releases.append(argv)
            assert len(qsubs) == 2
            assert argv == [str(publication.QRLS_PATH), "-h", "u", "8123400"]
            assert kwargs["env"] == {"USER": "synthetic"}
            assert publication.RELEASE_CLAIM_PATH.is_file()
            assert publication.RELEASE_CAPTURE_ROOT.is_dir()
            assert not publication.RELEASE_PATH.exists()
            assert publication._validate_continuation(fixture.run)["array_job_id"] == "8123400"
            assert publication._validate_release_claim("array")["complete_graph_sha256"] == core.sha256_file(publication.SUBMISSION_PATH)
            return subprocess.CompletedProcess(argv, 0, b"synthetic release accepted\n", b"")

        with mock.patch.object(publication, "_read", side_effect=fail_first_array_readback):
            result = publication.submit_auth_successor(qsub_runner=_array_and_finalizer_runner(qsubs), qstat_runner=_qstat_runner, qrls_runner=release)
        assert result["array_job_id"] == "8123400" and result["finalizer_job_id"] == "8123401"
        assert len(qsubs) == 2 and len(releases) == 1 and len(failed_readback) == 1
        assert publication.validate_release_authority()["qrls_invocations"] == 1
        assert all(path.read_bytes() == preserved[role] for role, path in fixture.consumed_paths.items())
        before = previous_tests._snapshot(fixture.root)
        _raises("R7HB_SUBMISSION_ALREADY_CLAIMED", lambda: publication.submit_auth_successor(qsub_runner=mock.Mock(), qrls_runner=mock.Mock()))
        _raises("R7HB_RELEASE_ALREADY_CLAIMED", lambda: publication._release_user_hold("array", run=fixture.run, qrls_runner=mock.Mock()))
        assert previous_tests._snapshot(fixture.root) == before


def test_failed_graph_publication_keeps_array_held_and_never_releases_or_resubmits() -> None:
    with _held_submission_fixture() as fixture:
        qsubs, releases = [], mock.Mock()
        write = publication._write

        def failed_write(path, value):
            if path == publication.SUBMISSION_PATH:
                raise publication.AuthenticationSuccessorError("R7HB_OUTPUT_PUBLICATION_FAILED")
            return write(path, value)

        with mock.patch.object(publication, "_write", side_effect=failed_write):
            _raises("R7HB_OUTPUT_PUBLICATION_FAILED", lambda: publication.submit_auth_successor(qsub_runner=_array_and_finalizer_runner(qsubs), qrls_runner=releases))
        assert len(qsubs) == 2
        releases.assert_not_called()
        assert publication.ARRAY_SUBMISSION_PATH.is_file() and publication.FINALIZER_SUBMISSION_PATH.is_file()
        assert not publication.RELEASE_CLAIM_PATH.exists() and not publication.SUBMISSION_PATH.exists()
        before = previous_tests._snapshot(fixture.root)
        retry = mock.Mock()
        _raises("R7HB_SUBMISSION_ALREADY_CLAIMED", lambda: publication.submit_auth_successor(qsub_runner=retry, qrls_runner=releases))
        retry.assert_not_called()
        assert previous_tests._snapshot(fixture.root) == before


def test_uncertain_qrls_captures_once_and_cannot_be_retried() -> None:
    for outcome in (subprocess.TimeoutExpired(["synthetic-qrls"], 30), subprocess.CompletedProcess([], 1, b"", b"synthetic rejection")):
        with _held_submission_fixture() as fixture:
            qsubs = []
            releases = mock.Mock(side_effect=outcome) if isinstance(outcome, Exception) else mock.Mock(return_value=outcome)
            _raises("R7HB_RELEASE_RESULT_UNCERTAIN", lambda: publication.submit_auth_successor(qsub_runner=_array_and_finalizer_runner(qsubs), qstat_runner=_qstat_runner, qrls_runner=releases))
            releases.assert_called_once()
            assert publication.RELEASE_CLAIM_PATH.is_file() and not publication.RELEASE_PATH.exists()
            assert (publication.RELEASE_CAPTURE_ROOT / "qrls.exit_status.restricted").is_file()
            assert len(qsubs) == 2
            before = previous_tests._snapshot(fixture.root)
            _raises("R7HB_RELEASE_ALREADY_CLAIMED", lambda: publication._release_user_hold("array", run=fixture.run, qrls_runner=releases))
            releases.assert_called_once()
            assert previous_tests._snapshot(fixture.root) == before


def test_release_requires_exact_complete_held_task_partition_and_job_identity() -> None:
    for mutation in ({"state": "qw"}, {"state": "r"}, {"tasks": "17-18:1"}, {"tasks": "17-19:2"}, {"tasks": "17,17,18,19"}, {"job_id": "8123499"}, {"owner": "foreign"}, {"name": "foreign_job"}):
        with _held_submission_fixture():
            qsubs, release = [], mock.Mock()
            qstat = mock.Mock(return_value=subprocess.CompletedProcess([], 0, _held_xml(**mutation), b""))
            try:
                publication.submit_auth_successor(qsub_runner=_array_and_finalizer_runner(qsubs), qstat_runner=qstat, qrls_runner=release)
            except publication.AuthenticationSuccessorError as error:
                assert error.code in {"R7HB_INITIAL_USER_HOLD_MISMATCH", "R7HB_INITIAL_USER_HOLD_NOT_OBSERVED"}
            else:
                raise AssertionError("Invalid user hold was released")
            release.assert_not_called()
            assert not publication.RELEASE_CLAIM_PATH.exists()


def test_cpu_probe_is_held_until_its_authority_graph_is_durable() -> None:
    with _held_submission_fixture(with_probe=False) as fixture:
        def qsub(argv, **kwargs):
            assert argv[argv.index("-clear") + 1] == "-h"
            assert argv[argv.index("-t") + 1] == "17"
            assert not any(value.startswith("gpus=") for value in argv)
            assert publication.PROBE_AUTHORITY_PATH.is_file()
            return subprocess.CompletedProcess(argv, 0, b"8123500.17-17:1\n", b"")

        def qrls(argv, **kwargs):
            assert argv == [str(publication.QRLS_PATH), "-h", "u", "8123500"]
            assert publication.PROBE_SUBMISSION_PATH.is_file()
            assert publication.PROBE_RELEASE_CLAIM_PATH.is_file()
            assert not publication.PROBE_RELEASE_PATH.exists()
            publication._validate_probe_authority(fixture.run)
            publication._validate_submission("probe")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        release = mock.Mock(side_effect=qrls)
        result = publication.submit_auth_successor_probe(qsub_runner=qsub, qstat_runner=mock.Mock(return_value=subprocess.CompletedProcess([], 0, _held_xml(role="probe", job_id="8123500"), b"")), qrls_runner=release)
        assert result["probe_job_id"] == "8123500"
        release.assert_called_once()
        assert publication.validate_release_authority("probe")["qrls_invocations"] == 1


def test_worker_can_start_after_release_claim_before_postrelease_receipt_and_rechecks_identity() -> None:
    with _held_submission_fixture(), contextlib.ExitStack() as stack:
        account = {**publication._account(), "expected_effective_uid": 1234, "expected_scheduler_username": "synthetic", "canonical_home": "/synthetic/home", "script_authority": {"runner": core.sha256_file(publication.RUNNER_PATH)}, "python_sha256": "b" * 64}
        stack.enter_context(mock.patch.object(publication, "_account", return_value=account))
        stack.enter_context(mock.patch.object(publication.scheduler, "build_worker_scheduler_context", return_value=runtime_tests._worker_context()))
        stack.enter_context(mock.patch.object(publication.stages, "resolved_python_executable_sha256", return_value="b" * 64))
        stack.enter_context(mock.patch.object(publication.scheduler, "diagnose_r8u_r7d_qstat_self", return_value=runtime_tests._qstat_diagnostic()))
        qsubs = []

        def start_worker_during_release(argv, **kwargs):
            assert not publication.RELEASE_PATH.exists()
            environment = {"JOB_ID": "8123400", "JOB_NAME": publication._job_name("array"), "SGE_TASK_ID": "17", "NSLOTS": "4", "CUDA_VISIBLE_DEVICES": "0"}
            with mock.patch.dict("os.environ", environment, clear=True):
                worker = publication.validate_successor_worker_submission(current_job_id="8123400", role="array")
                assert worker["release_claim_sha256"] == core.sha256_file(publication.RELEASE_CLAIM_PATH)
                assert publication._validate_recorded_worker("array", "17") == worker
                with mock.patch.dict("os.environ", {"JOB_NAME": "foreign_job"}):
                    _raises("R7HB_CURRENT_WORKER_IDENTITY_MISMATCH", lambda: publication.validate_successor_worker_submission(current_job_id="8123400", role="array"))
            assert not publication.RELEASE_PATH.exists()
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        publication.submit_auth_successor(qsub_runner=_array_and_finalizer_runner(qsubs), qstat_runner=_qstat_runner, qrls_runner=start_worker_during_release)
        assert publication.validate_release_authority()["job_id"] == "8123400"


def test_actual_runner_dispatch_routes_v2_probe_tail_and_finalizer() -> None:
    source = publication.RUNNER_PATH.read_text(encoding="utf-8")
    dispatch = 'case "$JOB_NAME" in' + source.split('case "$JOB_NAME" in', 1)[1].split("\nesac", 1)[0] + "\nesac\n"
    script = "set -eu\n" + dispatch + '\nprintf "%s\\n%s\\n%s\\n" "$CONTROLLER" "$MODE" "$CUDA_VISIBLE_DEVICES"\n'
    for label, task, slots, mode, function in (("ctx", "17", "1", "probe", "run_auth_successor_probe"), ("seq", "17", "4", "array", "run_auth_successor_array_task"), ("seq", "18", "4", "array", "run_auth_successor_array_task"), ("seq", "19", "4", "array", "run_auth_successor_array_task"), ("fin", "undefined", "4", "finalizer", "run_auth_successor_finalizer")):
        environment = {"WORKTREE": "/synthetic/worktree", "JOB_NAME": f"lvef_c3_r8u_r7hb_{label}_ffffffff", "SGE_TASK_ID": task, "NSLOTS": slots, "CUDA_VISIBLE_DEVICES": "0"}
        result = subprocess.run(["/bin/bash", "-c", script], env=environment, capture_output=True, text=True, check=False)
        assert result.returncode == 0 and result.stderr == ""
        controller, actual_mode, cuda = result.stdout.splitlines()
        assert controller == "/synthetic/worktree/scripts/lvef_c3_r8u_r7h_auth_publication_successor.py"
        assert actual_mode == mode and cuda == ("0" if label == "seq" else "")
        with mock.patch.object(publication, function, return_value={"status": "PASS_SYNTHETIC_DISPATCH"}) as entrypoint, mock.patch("builtins.print"):
            assert publication.main([mode]) == 0
        entrypoint.assert_called_once_with()
        if label == "seq":
            invalid = subprocess.run(["/bin/bash", "-c", script], env={**environment, "SGE_TASK_ID": "16"}, capture_output=True, text=True, check=False)
            assert invalid.returncode == 78 and invalid.stdout == ""


@contextlib.contextmanager
def _completed_probe_log_fixture():
    with _held_submission_fixture() as fixture:
        job_id = "8123500"
        record = {"jobnumber": job_id, "taskid": "17", "jobname": publication._job_name("probe"), "owner": "synthetic", "failed": "0", "exit_status": "0", "ru_wallclock": "10", "qsub_time": "Mon Sep 07 12:00:00 2026", "start_time": "Mon Sep 07 12:00:01 2026", "end_time": "Mon Sep 07 12:00:11 2026"}
        log = publication.PROBE_SCHEDULER_ROOT / f"{publication._job_name('probe')}.o{job_id}.17"
        payload = f"R7HB_STATUS={publication.PROBE_PASS}\n".encode()
        log.write_bytes(payload)
        log.chmod(0o644)
        yield SimpleNamespace(root=fixture.root, job_id=job_id, record=record, account={"expected_scheduler_username": "synthetic"}, path=log, payload=payload)


def _completed_probe_log(fixture, *, record=None, job_id=None):
    return publication._read_completed_probe_log(job_id=job_id or fixture.job_id, record=record or fixture.record, account=fixture.account)


def test_completed_probe_log_accepts_real_sge_644_without_changing_bytes_or_mode() -> None:
    for mode in (0o600, 0o644):
        with _completed_probe_log_fixture() as fixture:
            fixture.path.chmod(mode)
            before = fixture.path.stat()
            assert _completed_probe_log(fixture) == fixture.payload
            after = fixture.path.stat()
            for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"):
                assert getattr(before, field) == getattr(after, field)
            assert fixture.path.read_bytes() == fixture.payload


def test_completed_probe_log_refuses_unsafe_filesystem_shapes_and_owner() -> None:
    for mutation in ("group_writable", "world_writable", "unexpected_mode", "symlink", "hardlink", "empty", "oversize", "foreign_owner", "public_parent"):
        with _completed_probe_log_fixture() as fixture, contextlib.ExitStack() as stack:
            if mutation == "group_writable": fixture.path.chmod(0o664)
            elif mutation == "world_writable": fixture.path.chmod(0o666)
            elif mutation == "unexpected_mode": fixture.path.chmod(0o640)
            elif mutation == "symlink":
                target = fixture.path.with_name("synthetic-log-target")
                fixture.path.rename(target)
                fixture.path.symlink_to(target)
            elif mutation == "hardlink": os.link(fixture.path, fixture.path.with_name("synthetic-log-hardlink"))
            elif mutation == "empty": fixture.path.write_bytes(b"")
            elif mutation == "oversize": fixture.path.write_bytes(b"x" * (64 * 1024 + 1))
            elif mutation == "public_parent": fixture.path.parent.chmod(0o755)
            else:
                # Model a foreign-owned descriptor without requiring chown;
                # the path, open, bytes, all other stats, and readers stay real.
                fstat, inode = os.fstat, fixture.path.stat().st_ino
                def foreign_owner(descriptor):
                    value = fstat(descriptor)
                    if value.st_ino != inode: return value
                    fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
                    return SimpleNamespace(**{**fields, "st_uid": value.st_uid + 1})
                stack.enter_context(mock.patch.object(publication.os, "fstat", side_effect=foreign_owner))
            before = previous_tests._snapshot(fixture.root)
            try:
                _completed_probe_log(fixture)
            except (publication.AuthenticationSuccessorError, publication.original.R7HContinuationError) as error:
                assert error.code == "R7HB_PROBE_LOG_INVALID"
            else:
                raise AssertionError(f"Unsafe scheduler log accepted: {mutation}")
            assert previous_tests._snapshot(fixture.root) == before


def test_completed_probe_log_requires_exact_successful_terminal_qacct_identity() -> None:
    for mutation in ({"jobnumber": "8123499"}, {"taskid": "18"}, {"owner": "foreign"}, {"jobname": "foreign_job"}, {"failed": "1"}, {"exit_status": "78"}, {"ru_wallclock": "601"}, {"end_time": "Mon Sep 07 11:59:00 2026"}, {"end_time": "not-yet-terminal"}):
        with _completed_probe_log_fixture() as fixture:
            try:
                _completed_probe_log(fixture, record={**fixture.record, **mutation})
            except publication.AuthenticationSuccessorError as error:
                assert error.code in {"R7HB_PROBE_TERMINAL_FAILURE", "R7HB_PROBE_ACCOUNTING_INVALID"}
            else:
                raise AssertionError("Nonterminal or foreign qacct record authorized log read")
            assert fixture.path.read_bytes() == fixture.payload
    with _completed_probe_log_fixture() as fixture:
        _raises("R7HB_PROBE_LOG_IDENTITY_INVALID", lambda: _completed_probe_log(fixture, job_id="8123499", record={**fixture.record, "jobnumber": "8123499"}))


def test_readback_failure_recovers_only_the_publication_completed_by_this_call() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        path = root / "array_submission.restricted.json"
        value = {"status": "PASS_SYNTHETIC_PUBLICATION", "job_id": "8123500"}
        expected = core.canonical_json_bytes(value)
        with (
            mock.patch.object(publication.core, "atomic_write_json_no_clobber", wraps=core.atomic_write_json_no_clobber) as publisher,
            mock.patch.object(publication, "_read", side_effect=RuntimeError("synthetic readback failure")) as readback,
        ):
            digest = publication._write(path, value)
        assert digest == hashlib.sha256(expected).hexdigest()
        assert path.read_bytes() == expected
        publisher.assert_called_once()
        readback.assert_called_once_with(path)
        with mock.patch.object(publication.core, "atomic_write_json_no_clobber") as republish:
            _raises("R7HB_OUTPUT_ALREADY_EXISTS_NO_CLOBBER", lambda: publication._write(path, value))
        republish.assert_not_called()
        assert path.read_bytes() == expected


def test_same_bytes_from_a_competing_publisher_are_never_adopted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "array_submission.restricted.json"
        value = {"status": "PASS_SYNTHETIC_PUBLICATION"}
        expected = core.canonical_json_bytes(value)

        def competing_writer(*_args, **_kwargs):
            path.write_bytes(expected)
            path.chmod(0o600)
            raise core.OrchestrationError("OUTPUT_ALREADY_EXISTS_NO_CLOBBER")

        with (
            mock.patch.object(publication.core, "atomic_write_json_no_clobber", side_effect=competing_writer),
            mock.patch.object(publication, "_read") as readback,
            mock.patch.object(publication.core, "_authentication_successor_control_bytes") as rescue,
        ):
            _raises("R7HB_OUTPUT_PUBLICATION_FAILED", lambda: publication._write(path, value))
        readback.assert_not_called()
        rescue.assert_not_called()
        assert path.read_bytes() == expected


def test_publication_readback_cannot_accept_changed_or_unreadable_durable_bytes() -> None:
    for mutation in ("changed", "unreadable"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "submission.restricted.json"
            value = {"status": "PASS_SYNTHETIC_PUBLICATION"}

            def failed_readback(_path):
                if mutation == "changed":
                    path.write_bytes(core.canonical_json_bytes({"status": "CHANGED"}))
                else:
                    path.chmod(0o644)
                raise RuntimeError("synthetic readback failure")

            with mock.patch.object(publication, "_read", side_effect=failed_readback):
                _raises(
                    "R7HB_OUTPUT_READBACK_MISMATCH" if mutation == "changed" else "R7HB_OUTPUT_READBACK_FAILED",
                    lambda: publication._write(path, value),
                )
            assert path.is_file()
