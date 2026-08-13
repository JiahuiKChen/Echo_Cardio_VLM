from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

try:
    import pytest
except ModuleNotFoundError:
    @contextmanager
    def _dependency_light_raises(
        expected: type[BaseException], *, match: str | None = None
    ):
        try:
            yield
        except expected as exc:
            if match is not None and re.search(match, str(exc)) is None:
                raise AssertionError(
                    f"exception {exc!r} did not match {match!r}"
                ) from exc
        else:
            raise AssertionError(f"expected {expected.__name__}")

    class _DependencyLightMark:
        @staticmethod
        def parametrize(*_args: object, **_kwargs: object):
            return lambda function: function

    class _DependencyLightPytest:
        mark = _DependencyLightMark()
        raises = staticmethod(_dependency_light_raises)

    pytest = _DependencyLightPytest()


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import lvef_c3_minimal_canary_preservation_recovery as recovery


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    return path


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_fixed_authorities_and_no_caller_paths() -> None:
    assert recovery.ORIGINAL_SCIENTIFIC_COMMIT == "e5ca24c4899a16952eedb04f4d94c4d87453587c"
    assert recovery.ORIGINAL_JOB_ID == "7168202"
    assert recovery.MANIFEST_FILE_SHA256.startswith("5907a1ac53b05036")
    assert recovery.RUN_ID == "lvef_c3_minimal_5907a1ac53b05036_e5ca24c4"
    for argv in (
        ["--validate-installation"],
        ["--preflight-only"],
        ["--submit"],
        ["--run-recovery-worker"],
    ):
        recovery.parse_args(argv)
    with pytest.raises(SystemExit):
        recovery.parse_args(["--submit", "/caller/path"])


def test_shell_exposes_three_modes_and_hides_gpu() -> None:
    text = (SCRIPTS / "scc_recover_lvef_c3_minimal_canary_preservation.sh").read_text()
    assert "--validate-installation:1|--preflight-only:1|--submit:1" in text
    assert "--run-recovery-worker" not in text
    assert "export CUDA_VISIBLE_DEVICES=''" in text
    assert "qsub" not in text
    assert "source " not in text and "eval " not in text


def test_qsub_is_one_cpu_nonarray_job() -> None:
    command = recovery.qsub_command()
    assert command.count(str(recovery.QSUB_PATH)) == 1
    assert command[0] == str(recovery.QSUB_PATH)
    assert "-terse" in command and ["-r", "n"] == command[2:4]
    assert "h_rt=2:00:00" in command
    assert "omp" in command and "4" in command
    assert "mem_per_core=8G" in command
    joined = " ".join(command)
    assert "gpu" not in joined.casefold()
    assert " -t " not in f" {joined} " and "hold_jid" not in joined
    assert command[command.index("-N") + 1] == "lvef_c3_presrec_5907a1_e5ca"
    assert command[command.index("-o") + 1] == str(recovery.RECOVERY_ROOT)
    assert command[-1] == "--run-recovery-worker"


def test_cpu_mask_is_set_before_deferred_scientific_imports() -> None:
    source = (
        SCRIPTS / "lvef_c3_minimal_canary_preservation_recovery.py"
    ).read_text()
    mask = 'os.environ["CUDA_VISIBLE_DEVICES"] = ""'
    assert source.index(mask) < source.index("SCRIPT_ROOT: Final")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ""


def test_private_writer_accepts_0700_and_inherited_2700() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = Path(directory) / "private"
        _private_dir(root)
        target = root / "receipt.json"
        recovery.write_json_no_clobber(target, {"status": "PASS"})
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        with pytest.raises(recovery.RecoveryError, match="RECOVERY_OUTPUT_COLLISION"):
            recovery.write_json_no_clobber(target, {"status": "PASS"})
        root.chmod(0o2700)
        recovery.validate_private_directory(root)


def test_private_writer_rejects_group_bits_and_symlink() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        base = Path(directory)
        root = _private_dir(base / "private")
        root.chmod(0o770)
        with pytest.raises(recovery.RecoveryError, match="RECOVERY_PRIVATE_DIRECTORY_INVALID"):
            recovery.validate_private_directory(root)
        root.chmod(0o700)
        link = base / "link"
        link.symlink_to(root, target_is_directory=True)
        with pytest.raises(recovery.RecoveryError, match="RECOVERY_PATH_SYMLINK"):
            recovery.validate_private_directory(link)


@pytest.mark.parametrize("mode", [0o000, 0o500, 0o600, 0o701, 0o770, 0o1700])
def test_private_directory_requires_owner_0700_and_no_disallowed_bits(mode: int) -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "private")
        root.chmod(mode)
        with pytest.raises(recovery.RecoveryError, match="RECOVERY_PRIVATE_DIRECTORY_INVALID"):
            recovery.validate_private_directory(root)


@pytest.mark.parametrize("mode", [0o400, 0o500, 0o640, 0o700])
def test_private_file_requires_exact_0600(mode: int) -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        path = _write(Path(directory) / "private.json", b"{}\n")
        path.chmod(mode)
        with pytest.raises(recovery.RecoveryError, match="RECOVERY_PRIVATE_INPUT_INVALID"):
            recovery.read_regular(path, private=True)
        path.chmod(0o600)
        assert recovery.read_regular(path, private=True) == b"{}\n"


def test_private_file_rejects_special_bits_even_when_host_chmod_strips_them() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        path = _write(Path(directory) / "private.json", b"{}\n")
        observed = path.stat()
        synthetic = SimpleNamespace(
            st_mode=stat.S_IFREG | stat.S_ISGID | 0o600,
            st_uid=os.geteuid(),
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
        )
        with mock.patch.object(recovery.os, "fstat", return_value=synthetic):
            with pytest.raises(
                recovery.RecoveryError, match="RECOVERY_PRIVATE_INPUT_INVALID"
            ):
                recovery.read_regular(path, private=True)


def test_untracked_script_import_gate_includes_ignored_candidates() -> None:
    recovery.validate_script_import_tree("")
    recovery.validate_script_import_tree("scripts/__pycache__/safe.pyc\n")
    for candidate in (
        "scripts/ignored.py\n",
        "scripts/native.so\n",
        "scripts/__pycache__/nested/unsafe.pyc\n",
    ):
        with pytest.raises(
            recovery.RecoveryError,
            match="RECOVERY_UNTRACKED_SCRIPT_IMPORT_CANDIDATE",
        ):
            recovery.validate_script_import_tree(candidate)
    source = (
        SCRIPTS / "lvef_c3_minimal_canary_preservation_recovery.py"
    ).read_text()
    assert 'git("ls-files", "--others", "--", "scripts")' in source


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ENVIRONMENT_AUTHORITY_COMMIT_EQUAL"),
        ("environment_authority_relation", "EQUAL"),
        ("environment_authority_commit", "f" * 40),
        ("scientific_governing_commit", "f" * 40),
        ("environment_receipt_sha256", "f" * 64),
        ("unexpected", False),
    ],
)
def test_recovery_environment_relation_requires_exact_shared_api(
    field: str, value: object
) -> None:
    import lvef_c3_production_stages as stages

    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        path = _write(Path(directory) / "environment.json", b"{}\n")
        result = {
            "status": "ENVIRONMENT_AUTHORITY_COMMIT_ANCESTOR",
            "environment_receipt": {},
            "environment_receipt_sha256": recovery.ENVIRONMENT_RECEIPT_SHA256,
            "environment_authority_commit": recovery.ENVIRONMENT_AUTHORITY_COMMIT,
            "scientific_governing_commit": recovery.ORIGINAL_SCIENTIFIC_COMMIT,
            "environment_authority_relation": "ANCESTOR",
        }
        with mock.patch.object(
            stages,
            "validate_environment_authority_for_scientific_commit",
            return_value=result,
        ):
            assert recovery._validate_environment(path) == "ANCESTOR"
        result[field] = value
        with mock.patch.object(
            stages,
            "validate_environment_authority_for_scientific_commit",
            return_value=result,
        ):
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ENVIRONMENT_AUTHORITY_RELATION_INVALID",
            ):
                recovery._validate_environment(path)


def _synthetic_state(root: Path) -> recovery.PreservedState:
    return recovery.PreservedState(
        implementation_commit="f" * 40,
        manifest={"manifest_sha256": recovery.MANIFEST_SEMANTIC_SHA256},
        manifest_file_sha256=recovery.MANIFEST_FILE_SHA256,
        manifest_semantic_sha256=recovery.MANIFEST_SEMANTIC_SHA256,
        plan={"batches": [{"n_studies": 5}]},
        plan_sha256="1" * 64,
        requirements=object(),
        runtime_authority={"git_commit": recovery.ORIGINAL_SCIENTIFIC_COMMIT},
        environment_receipt=_write(root / "environment.json", b"{}\n"),
        environment_relation="ANCESTOR",
        preservation_manifest_sha256="2" * 64,
        preservation_manifest_bytes=100,
        scheduler_binding_sha256="3" * 64,
        original_stage_ledger_sha256="4" * 64,
        original_pooling_ledger_sha256="5" * 64,
        original_observation_sha256="6" * 64,
    )


def test_submit_captures_exact_numeric_terse_output_once() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(root)
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(list(command))
            assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == ""
            return subprocess.CompletedProcess(command, 0, b"8123456\n", b"notice\n")

        patches = (
            mock.patch.object(recovery, "RECOVERY_ROOT", root / "recovery"),
            mock.patch.object(recovery, "RECOVERY_AUTHORITY_PATH", root / "recovery/authority.json"),
            mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", root / "recovery/claim.json"),
            mock.patch.object(recovery, "RECOVERY_SUBMISSION_PATH", root / "recovery/submission.json"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", root / "recovery/stdout"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STDERR_PATH", root / "recovery/stderr"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", root / "recovery/status"),
            mock.patch.object(recovery, "validate_preserved_state", return_value=state),
            mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            result = recovery.submit_recovery(runner=runner)
        assert len(calls) == 1
        assert calls[0][calls[0].index("-o") + 1] == str(root / "recovery")
        assert calls[0][calls[0].index("-N") + 1] == recovery.RECOVERY_JOB_NAME
        assert result["recovery_job_id"] == "8123456"
        assert result["scheduler_submissions"] == 1
        assert (root / "recovery/stdout").read_bytes() == b"8123456\n"
        assert (root / "recovery/stderr").read_bytes() == b"notice\n"
        receipt = json.loads((root / "recovery/submission.json").read_text())
        assert receipt["status"] == "PASS_NUMERIC_QSUB_ID_CAPTURED"
        assert receipt["scheduler_submission_count"] == 1


@pytest.mark.parametrize(
    "stdout",
    [b"", b"0\n", b"123\n456\n", b"123\n\n", b"123\r\n", b"job 123\n", b"123.1\n"],
)
def test_submit_never_retries_ambiguous_qsub_output(stdout: bytes) -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(root)
        count = 0

        def runner(command, **kwargs):
            nonlocal count
            count += 1
            return subprocess.CompletedProcess(command, 0, stdout, b"")

        with mock.patch.object(recovery, "RECOVERY_ROOT", root / "recovery"), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", root / "recovery/authority.json"
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", root / "recovery/claim.json"), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", root / "recovery/submission.json"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", root / "recovery/stdout"), mock.patch.object(
            recovery, "RECOVERY_QSUB_STDERR_PATH", root / "recovery/stderr"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", root / "recovery/status"), mock.patch.object(
            recovery, "validate_preserved_state", return_value=state
        ), mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}):
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_QSUB_OUTPUT_IDENTITY_AMBIGUOUS"):
                recovery.submit_recovery(runner=runner)
        assert count == 1
        assert (root / "recovery/stdout").read_bytes() == stdout


def test_submit_preserves_nonzero_qsub_evidence_without_retry() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(root)
        count = 0

        def runner(command, **kwargs):
            nonlocal count
            count += 1
            return subprocess.CompletedProcess(command, 17, b"", b"safe restricted detail")

        with mock.patch.object(recovery, "RECOVERY_ROOT", root / "recovery"), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", root / "recovery/authority.json"
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", root / "recovery/claim.json"), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", root / "recovery/submission.json"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", root / "recovery/stdout"), mock.patch.object(
            recovery, "RECOVERY_QSUB_STDERR_PATH", root / "recovery/stderr"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", root / "recovery/status"), mock.patch.object(
            recovery, "validate_preserved_state", return_value=state
        ), mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}):
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_QSUB_PROCESS_FAILED"):
                recovery.submit_recovery(runner=runner)
        assert count == 1
        assert (root / "recovery/status").read_text() == "17\n"
        assert (root / "recovery/stderr").read_bytes() == b"safe restricted detail"


def test_recovery_authority_and_claim_are_closed_and_exact() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        owner = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(owner)
        recovery_root = owner / "recovery"
        authority_path = recovery_root / "authority.json"
        claim_path = recovery_root / "claim.json"
        with mock.patch.object(recovery, "RECOVERY_ROOT", recovery_root), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", authority_path
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", claim_path):
            recovery.create_submission_claim(state)
            recovery._validate_worker_claim(state)
            authority = json.loads(authority_path.read_text())
            claim = json.loads(claim_path.read_text())
            assert set(authority) == recovery.RECOVERY_AUTHORITY_KEYS
            assert set(claim) == recovery.RECOVERY_CLAIM_KEYS
            assert authority["cpu_only"] is claim["cpu_only"] is True
            assert authority["environment_receipt_sha256"] == recovery.ENVIRONMENT_RECEIPT_SHA256
            assert claim["original_scheduler_job_id"] == recovery.ORIGINAL_JOB_ID
            assert claim["original_terminal_receipt_sha256"] == recovery.ORIGINAL_TERMINAL_SHA256
            claim["cloud_requests"] = 1
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_SUBMISSION_CLAIM_INVALID"):
                recovery._validate_worker_claim(state)
            claim["cloud_requests"] = 0
            claim["unexpected"] = False
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_SUBMISSION_CLAIM_INVALID"):
                recovery._validate_worker_claim(state)


def _valid_summary() -> dict[str, object]:
    import lvef_c3_orchestration_core as core

    receipt_sha = "7" * 64
    batch_sha = "8" * 64
    scheduler_sha = "9" * 64
    result: dict[str, object] = {
        "status": "PASS_CANARY_PRESERVATION_FINALIZED_RETAINED_CACHE",
        "successful_train_studies": 5,
        "selected_subjects": 5,
        "verified_source_objects": 380,
        "selected_source_bytes": recovery.DECLARED_EXPECTED_BYTES,
        "dicom_readable_objects": 380,
        "dicom_unreadable_objects": 0,
        "multiframe_cines": 230,
        "single_frame_objects": 150,
        "extracted_clips": 230,
        "unique_clip_keys": 230,
        "clip_embeddings": 230,
        "pooled_studies": 5,
        "no_cine_studies": 0,
        "failed_studies": 0,
        "preservation_receipt_sha256": receipt_sha,
        "canary_manifest_sha256": recovery.MANIFEST_SEMANTIC_SHA256,
        "batch_plan_sha256": batch_sha,
        "scheduler_plan_sha256": scheduler_sha,
        "all_studies_successful": True,
        "all_studies_train": True,
        "manifest_plan_scheduler_binding_passed": True,
        "all_preservation_gates_passed": True,
        "raw_dicoms_retained": True,
        "extracted_cache_retained": True,
        "aggregate_safe": True,
        "production_continuation_authorized": False,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
    }
    result["authority_binding_sha256"] = core.canonical_json_sha256(
        {
            "preservation_receipt_sha256": receipt_sha,
            "canary_manifest_sha256": recovery.MANIFEST_SEMANTIC_SHA256,
            "batch_plan_sha256": batch_sha,
            "scheduler_plan_sha256": scheduler_sha,
        }
    )
    return result


def test_execute_validates_before_write_and_calls_only_recovery_functions_once() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "recovery")
        state = _synthetic_state(root)
        authority = _write(root / "authority.json", b"{}\n")
        claim = _write(root / "claim.json", b"{}\n")
        preservation_manifest = _write(root / "manifest.tsv", b"header\n")
        preservation_receipt = _write(root / "preservation.json", b"{}\n")
        finalization = root / "finalization.json"
        terminal = root / "terminal.json"
        original_stage = _write(root / "stage_ledger.json", b"stage immutable\n")
        sealed_manifest = _write(root / "sealed_manifest.json", b"manifest immutable\n")
        original_terminal_bytes = b"original immutable terminal\n"
        original_terminal = _write(root / "original_terminal.json", original_terminal_bytes)
        immutable_before = {
            path: path.read_bytes()
            for path in (original_terminal, original_stage, sealed_manifest, preservation_manifest)
        }
        calls = {
            "preserve": 0,
            "finalize": 0,
            "validate": 0,
            "write": 0,
            "postwrite": 0,
            "download": 0,
            "dicom": 0,
            "extract": 0,
            "echoprime": 0,
            "embedding": 0,
            "gpu": 0,
        }
        events: list[str] = []

        def preserve(**kwargs):
            calls["preserve"] += 1
            events.append("preserve")
            assert kwargs["scheduler_runner_path"] == recovery.ORIGINAL_SCIENTIFIC_RUNNER_PATH
            assert kwargs["scheduler_job_identity"] == recovery.ORIGINAL_JOB_ID
            return {}

        summary = _valid_summary()
        import lvef_c3_orchestration_core as core

        summary["preservation_receipt_sha256"] = core.canonical_json_sha256({})
        summary["batch_plan_sha256"] = state.plan_sha256
        summary["scheduler_plan_sha256"] = state.scheduler_binding_sha256
        summary["authority_binding_sha256"] = core.canonical_json_sha256(
            {
                "preservation_receipt_sha256": summary[
                    "preservation_receipt_sha256"
                ],
                "canary_manifest_sha256": recovery.MANIFEST_SEMANTIC_SHA256,
                "batch_plan_sha256": state.plan_sha256,
                "scheduler_plan_sha256": state.scheduler_binding_sha256,
            }
        )

        def finalize(receipt, **kwargs):
            calls["finalize"] += 1
            events.append("finalize")
            return summary

        def validate(value):
            calls["validate"] += 1
            events.append("validate")

        def write(path, value):
            calls["write"] += 1
            events.append("write")
            _write(path, (json.dumps(value) + "\n").encode())

        def postwrite(*args, **kwargs):
            calls["postwrite"] += 1
            events.append("postwrite")
            assert finalization.is_file()

        deps = recovery.RecoveryDependencies(
            preserve=preserve,
            finalize=finalize,
            validate_finalization=validate,
            write_finalization=write,
        )
        with mock.patch.object(recovery, "RECOVERY_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", authority
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", claim), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ), mock.patch.object(recovery, "PRESERVATION_MANIFEST_PATH", preservation_manifest), mock.patch.object(
            recovery, "PRESERVATION_RECEIPT_PATH", preservation_receipt
        ), mock.patch.object(recovery, "FINALIZATION_PATH", finalization), mock.patch.object(
            recovery, "ORIGINAL_TERMINAL_PATH", original_terminal
        ), mock.patch.object(recovery, "validate_preserved_state", return_value=state), mock.patch.object(
            recovery, "_validate_worker_claim", return_value=None
        ), mock.patch.object(
            recovery, "validate_postwrite_state", side_effect=postwrite
        ):
            result = recovery.execute_recovery(scheduler_job_id="8123456", dependencies=deps)
        assert events == ["preserve", "finalize", "validate", "write", "postwrite"]
        assert {key: calls[key] for key in ("preserve", "finalize", "validate", "write", "postwrite")} == {
            "preserve": 1,
            "finalize": 1,
            "validate": 1,
            "write": 1,
            "postwrite": 1,
        }
        assert all(
            calls[key] == 0
            for key in ("download", "dicom", "extract", "echoprime", "embedding", "gpu")
        )
        assert result["status"] == "PASS_WITH_POSTJOB_PRESERVATION_RECOVERY"
        assert result["scientific_stage_reruns"] == 0
        assert result["cloud_requests"] == result["dicom_reads"] == 0
        assert result["gpu_execution"] == result["embedding_generation"] == 0
        assert all(path.read_bytes() == payload for path, payload in immutable_before.items())
        with mock.patch.object(recovery, "RECOVERY_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ):
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_ALREADY_TERMINAL"):
                recovery.execute_recovery(scheduler_job_id="8123456", dependencies=deps)
        assert calls["preserve"] == calls["finalize"] == calls["write"] == 1


def test_recovery_source_has_no_scientific_stage_calls() -> None:
    source = (SCRIPTS / "lvef_c3_minimal_canary_preservation_recovery.py").read_text()
    forbidden = (
        "execute_exact_batch_download(",
        "run_production_dicom_extraction(",
        "run_production_echoprime(",
        "GcloudADCTokenProvider(",
        "GCSExactObjectBodyTransport(",
        "torch.cuda",
        "model.fit(",
        "predict(",
    )
    assert all(item not in source for item in forbidden)
    assert source.count("source.preserve(") == 1
    assert source.count("source.finalize(") == 1


def test_invalid_finalization_is_rejected_before_write() -> None:
    bad = _valid_summary()
    bad["clip_embeddings"] = 229
    calls = {"validate": 0, "write": 0}

    def validate(value):
        calls["validate"] += 1

    with pytest.raises(recovery.RecoveryError, match="RECOVERY_FINALIZATION_SUMMARY_INVALID"):
        recovery.validate_finalization_summary(
            bad,
            expected_preservation_receipt_sha256="7" * 64,
            expected_batch_plan_sha256="8" * 64,
            expected_scheduler_plan_sha256="9" * 64,
            closed_schema_validator=validate,
        )
    assert calls == {"validate": 0, "write": 0}


def test_returned_preservation_receipt_must_equal_on_disk_and_canonically_bind() -> None:
    import lvef_c3_orchestration_core as core

    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        path = _write(
            Path(directory) / "receipt.json",
            b'{"status":"PASS","value":1}\n',
        )
        expected = {"status": "PASS", "value": 1}
        with mock.patch.object(recovery, "PRESERVATION_RECEIPT_PATH", path):
            observed, digest = recovery.load_bound_preservation_receipt(expected)
            assert observed == expected
            assert digest == core.canonical_json_sha256(expected)
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_PRESERVATION_RECEIPT_RETURN_MISMATCH",
            ):
                recovery.load_bound_preservation_receipt(
                    {"status": "PASS", "value": 2}
                )


@contextmanager
def _replay_fixture(root: Path):
    run_root = _private_dir(root / "run")
    raw_root = _private_dir(run_root / "raw")
    extraction_root = _private_dir(run_root / "extraction")
    clips_root = _private_dir(extraction_root / "clips")
    embedding_root = _private_dir(run_root / "echoprime")
    batch_root = _private_dir(run_root / "batch")
    preservation_root = _private_dir(run_root / "preservation")
    paths = {
        "raw": _write(raw_root / "object.dcm", b"raw-body"),
        "metadata": _write(extraction_root / "audit.csv", b"audit"),
        "clip": _write(clips_root / "clip.npz", b"clip-cache"),
        "clip_embedding": _write(
            embedding_root / "clip_embeddings.restricted.npz", b"clip-embedding"
        ),
        "study_embedding": _write(
            embedding_root / "study_embeddings.restricted.npz", b"study-embedding"
        ),
        "ledger": _write(batch_root / "download_resume_ledger.restricted.json", b"ledger"),
    }
    manifest_path = preservation_root / "batch_preservation_manifest.restricted.tsv"
    receipt_path = preservation_root / "batch_preservation_receipt.restricted.json"
    finalization_path = run_root / "finalization.json"
    patches = (
        mock.patch.object(recovery, "RUN_ROOT", run_root),
        mock.patch.object(recovery, "RAW_BATCH_ROOT", raw_root),
        mock.patch.object(recovery, "EXTRACTION_ROOT", extraction_root),
        mock.patch.object(recovery, "ECHOPRIME_ROOT", embedding_root),
        mock.patch.object(recovery, "BATCH_ROOT", batch_root),
        mock.patch.object(recovery, "PRESERVATION_ROOT", preservation_root),
        mock.patch.object(recovery, "PRESERVATION_MANIFEST_PATH", manifest_path),
        mock.patch.object(recovery, "PRESERVATION_RECEIPT_PATH", receipt_path),
        mock.patch.object(recovery, "FINALIZATION_PATH", finalization_path),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        import preserve_lvef_c3_production_batch as preservation

        records = [
            recovery._artifact_record(paths["raw"], "raw_dicom_and_download_authority"),
            recovery._artifact_record(paths["metadata"], "dicom_extraction_metadata_retained"),
            recovery._artifact_record(paths["clip"], "extracted_npz_cache_owner_retirable"),
            recovery._artifact_record(paths["clip_embedding"], "embedding_and_pooling_retained"),
            recovery._artifact_record(paths["study_embedding"], "embedding_and_pooling_retained"),
            recovery._artifact_record(paths["ledger"], "download_ledger"),
        ]
        body = ["\t".join(preservation.MANIFEST_HEADER)]
        body.extend(
            f"{row['relative_path']}\t{row['size_bytes']}\t{row['sha256']}\t{row['role']}"
            for row in sorted(records, key=lambda value: value["relative_path"])
        )
        _write(manifest_path, ("\n".join(body) + "\n").encode())
        yield paths, manifest_path, receipt_path, finalization_path


def test_retained_cache_replay_accepts_exact_pre_and_post_state() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        with _replay_fixture(Path(directory)) as (_, _, receipt, finalization):
            first = recovery.validate_preservation_manifest_replay(
                expect_completion_outputs=False
            )
            _write(receipt, b"{}\n")
            _write(finalization, b"{}\n")
            transition_root = _private_dir(recovery.PRESERVATION_ROOT / "transition_receipts")
            _write(transition_root / "preservation_complete.restricted.json", b"{}\n")
            _write(transition_root / "cache_retirement_eligible.restricted.json", b"{}\n")
            _write(
                recovery.BATCH_ROOT
                / "cache_retirement_eligible_resume_ledger.restricted.json",
                b"{}\n",
            )
            second = recovery.validate_preservation_manifest_replay(
                expect_completion_outputs=True
            )
        assert first == second


@pytest.mark.parametrize(
    "unexpected",
    ["partial", "transition", "receipt", "eligibility", "finalization"],
)
def test_preflight_requires_exact_incomplete_preservation_topology(
    unexpected: str,
) -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        with _replay_fixture(Path(directory)) as (_, _, receipt, finalization):
            if unexpected == "partial":
                _write(recovery.PRESERVATION_ROOT / ".unexpected.partial", b"x")
            elif unexpected == "transition":
                _private_dir(recovery.PRESERVATION_ROOT / "transition_receipts")
            elif unexpected == "receipt":
                _write(receipt, b"{}\n")
            elif unexpected == "eligibility":
                _write(
                    recovery.BATCH_ROOT
                    / "cache_retirement_eligible_resume_ledger.restricted.json",
                    b"{}\n",
                )
            else:
                _write(finalization, b"{}\n")
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_INCOMPLETE_STATE_NOT_EXACT",
            ):
                recovery.validate_preservation_manifest_replay(
                    expect_completion_outputs=False
                )


@pytest.mark.parametrize(
    "target",
    ["manifest", "raw", "clip", "clip_embedding", "study_embedding", "ledger"],
)
def test_retained_cache_replay_rejects_every_material_mutation(target: str) -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        with _replay_fixture(Path(directory)) as (paths, manifest, _, _):
            path = manifest if target == "manifest" else paths[target]
            path.write_bytes(path.read_bytes() + b"altered")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_PRESERVATION"):
                recovery.validate_preservation_manifest_replay(
                    expect_completion_outputs=False
                )


def test_exact_post_pooling_state_required() -> None:
    plan = {"batches": [{"objects": [{"source_object_key": "object-1"}]}]}
    authority = {"batch_plan_sha256": "a" * 64}
    ledger = {
        "status": "ACTIVE",
        "batches": {
            recovery.BATCH_ID: {
                "state": "STUDY_POOLING_COMPLETE",
                "resume_state": None,
                "completed_states": [
                    "PLANNED", "DOWNLOAD_VERIFIED", "DICOM_AUDIT_COMPLETE",
                    "EXTRACTION_COMPLETE", "EMBEDDING_COMPLETE", "STUDY_POOLING_COMPLETE",
                ],
                "events": [
                    {"to_state": state, "output_manifest_sha256": "0" * 64}
                    for state in (
                        "DOWNLOAD_VERIFIED", "DICOM_AUDIT_COMPLETE", "EXTRACTION_COMPLETE",
                        "EMBEDDING_COMPLETE", "STUDY_POOLING_COMPLETE",
                    )
                ],
                "download_manifest_sha256": "d" * 64,
            }
        },
    }
    ledger["batches"][recovery.BATCH_ID]["events"][-1]["output_manifest_sha256"] = "s" * 64
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "root")
        ledger_path = _write(root / "pooling.json", (json.dumps(ledger) + "\n").encode())
        download_manifest = _write(
            root / "verified_download_manifest.restricted.csv", b"download"
        )
        study_manifest = _write(root / "study_manifest.restricted.csv", b"study")
        ledger["batches"][recovery.BATCH_ID]["download_manifest_sha256"] = recovery.sha256_file(download_manifest)
        ledger["batches"][recovery.BATCH_ID]["events"][-1]["output_manifest_sha256"] = recovery.sha256_file(study_manifest)
        ledger_path.write_text(json.dumps(ledger) + "\n")
        with mock.patch.object(recovery, "POOLING_LEDGER_PATH", ledger_path), mock.patch.object(
            recovery, "RAW_BATCH_ROOT", root
        ), mock.patch.object(recovery, "ECHOPRIME_ROOT", root), mock.patch(
            "lvef_c3_orchestration_core.validate_resume_authority", return_value=None
        ):
            recovery.validate_pooling_ledger_state(plan, authority)
            ledger["batches"][recovery.BATCH_ID]["state"] = "EMBEDDING_COMPLETE"
            ledger_path.write_text(json.dumps(ledger) + "\n")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_POOLING_LEDGER_STATE_INVALID"):
                recovery.validate_pooling_ledger_state(plan, authority)


def test_failure_terminal_is_no_clobber_and_effect_free() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "recovery")
        terminal = root / "terminal.json"
        with mock.patch.object(recovery, "RECOVERY_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ):
            recovery.write_failure_terminal("RECOVERY_SYNTHETIC_FAILURE", "8123456")
            first = terminal.read_bytes()
            recovery.write_failure_terminal("RECOVERY_CHANGED", "8123456")
        assert terminal.read_bytes() == first
        value = json.loads(first)
        assert value["status"] == "FAIL"
        assert value["scientific_stage_reruns"] == 0
        assert value["cloud_requests"] == value["dicom_reads"] == 0
        assert value["gpu_execution"] == value["embedding_generation"] == 0
