from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
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
import lvef_c3_orchestration_core as orchestration_core


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
    assert command[command.index("-N") + 1] == "lvef_c3_presrec2_5907a1_e5ca"
    assert command[command.index("-o") + 1] == str(recovery.RECOVERY_WORKER_LOG_PATH)
    assert command[command.index("-b") + 1] == "y"
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
        attempt_001_evidence={"evidence": {"bytes": 1, "sha256": "7" * 64}},
        attempt_001_evidence_set_sha256="8" * 64,
    )


def _scheduler_environment() -> dict[str, str]:
    account = pwd.getpwuid(os.geteuid())
    return {
        "SGE_ROOT": str(recovery.CANONICAL_SGE_ROOT),
        "SGE_CELL": "default",
        "SGE_QMASTER_PORT": "6444",
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": account.pw_shell,
        "CLOUDSDK_CONFIG": "/must/not/forward",
        "GOOGLE_APPLICATION_CREDENTIALS": "/must/not/forward",
        "PYTHONPATH": "/must/not/forward",
        "ACCESS_TOKEN": "must-not-forward",
    }


def _qstat_clear(command, **kwargs):
    assert command == [
        str(recovery.QSTAT_PATH), "-xml", "-u", kwargs["env"]["USER"]
    ]
    return subprocess.CompletedProcess(
        command, 0, b"<job_info><queue_info/><job_info/></job_info>", b""
    )


def test_attempt_001_stderr_and_branch_a_classification_are_exact() -> None:
    payload = recovery.ATTEMPT_001_QSUB_STDERR
    assert len(payload) == 107
    assert hashlib.sha256(payload).hexdigest() == recovery.ATTEMPT_001_QSUB_STDERR_SHA256
    assert payload == (
        b"\nUnable to initialize environment because of error: "
        b"Please set the environment variable SGE_ROOT.\nExiting.\n"
    )
    assert recovery.QSUB_REJECTION_CLASSIFICATION == "QSUB_SCHEDULER_CONTEXT_MISSING"
    assert recovery.qsub_command()[recovery.qsub_command().index("-b") + 1] == "y"
    assert recovery.ATTEMPT_001_FROZEN_EVIDENCE == {
        "preservation_recovery_authority.restricted.json": {
            "bytes": 1792,
            "sha256": "43a9a18910ac2305d5fda806f8cc48e34ae1bd4838fe309e51b896d39af844c3",
        },
        "preservation_recovery_submission_claim.restricted.json": {
            "bytes": 1303,
            "sha256": "ccaab8a3d7dc787221e3b144344951c160810ef2397debc467a568a43960a938",
        },
        "qsub.stdout.restricted": {
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
        "qsub.stderr.restricted": {
            "bytes": 107,
            "sha256": recovery.ATTEMPT_001_QSUB_STDERR_SHA256,
        },
        "qsub.exit_status.restricted": {
            "bytes": 2,
            "sha256": hashlib.sha256(b"1\n").hexdigest(),
        },
    }


def test_qsub_environment_is_closed_and_preserves_required_scheduler_context() -> None:
    source = _scheduler_environment()
    observed = recovery.build_qsub_environment(source)
    assert observed["SGE_ROOT"] == str(recovery.CANONICAL_SGE_ROOT)
    assert all(
        name in observed
        for name in ("SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL")
    )
    assert observed["SGE_CELL"] == "default"
    assert observed["SGE_QMASTER_PORT"] == "6444"
    assert not {
        "CLOUDSDK_CONFIG", "GOOGLE_APPLICATION_CREDENTIALS", "PYTHONPATH",
        "ACCESS_TOKEN",
    } & set(observed)
    assert set(observed) <= set(recovery.CONTROLLED_QSUB_ENVIRONMENT) | set(
        recovery.SCHEDULER_CONTEXT_NAMES
    )


def test_sge_root_accepts_only_canonical_path_with_optional_trailing_slash() -> None:
    source = _scheduler_environment()
    source["SGE_ROOT"] = f"{recovery.CANONICAL_SGE_ROOT}/"
    assert recovery.build_qsub_environment(source)["SGE_ROOT"] == str(
        recovery.CANONICAL_SGE_ROOT
    )
    source["SGE_ROOT"] = f"{recovery.CANONICAL_SGE_ROOT}/../sge_root"
    with pytest.raises(
        recovery.RecoveryError, match="RECOVERY_SGE_ROOT_AUTHORITY_INVALID"
    ):
        recovery.build_qsub_environment(source)


@pytest.mark.parametrize("missing", ["SGE_ROOT", "HOME", "USER", "LOGNAME", "SHELL"])
def test_missing_required_scheduler_context_fails_before_claim(missing: str) -> None:
    source = _scheduler_environment()
    source.pop(missing)
    with pytest.raises(
        recovery.RecoveryError,
        match="RECOVERY_(SGE_ROOT_AUTHORITY_INVALID|SCHEDULER_CONTEXT_MISSING)",
    ):
        recovery.build_qsub_environment(source)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SGE_ROOT", "/wrong/sge"),
        ("SGE_CELL", "../unsafe"),
        ("SGE_QMASTER_PORT", "70000"),
        ("HOME", "relative/home"),
        ("USER", "unsafe user"),
        ("LOGNAME", "different"),
    ],
)
def test_malformed_or_conflicting_scheduler_context_is_rejected(
    name: str, value: str
) -> None:
    source = _scheduler_environment()
    source[name] = value
    with pytest.raises(recovery.RecoveryError):
        recovery.build_qsub_environment(source)


def test_active_matching_job_gate_is_fail_closed() -> None:
    environment = recovery.build_qsub_environment(_scheduler_environment())

    def active(command, **kwargs):
        payload = (
            b"<job_info><queue_info><job_list><JB_name>"
            + recovery.RECOVERY_JOB_NAME.encode()
            + b"</JB_name></job_list></queue_info></job_info>"
        )
        return subprocess.CompletedProcess(command, 0, payload, b"")

    with pytest.raises(
        recovery.RecoveryError, match="RECOVERY_ACTIVE_MATCHING_JOB_EXISTS"
    ):
        recovery.validate_no_active_recovery_jobs(environment, runner=active)


def test_attempt_001_exact_rejection_evidence_is_required_and_immutable() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "recovery")
        paths = {
            "authority": _write(root / "authority.json", b"authority"),
            "claim": _write(root / "claim.json", b"claim"),
            "stdout": _write(root / "stdout", b""),
            "stderr": _write(root / "stderr", recovery.ATTEMPT_001_QSUB_STDERR),
            "status": _write(root / "status", b"1\n"),
        }
        authority = {"status": "fixed"}
        claim = {"status": "fixed"}
        patches = (
            mock.patch.object(recovery, "RECOVERY_ROOT", root),
            mock.patch.object(recovery, "ATTEMPT_001_AUTHORITY_PATH", paths["authority"]),
            mock.patch.object(recovery, "ATTEMPT_001_CLAIM_PATH", paths["claim"]),
            mock.patch.object(recovery, "ATTEMPT_001_QSUB_STDOUT_PATH", paths["stdout"]),
            mock.patch.object(recovery, "ATTEMPT_001_QSUB_STDERR_PATH", paths["stderr"]),
            mock.patch.object(recovery, "ATTEMPT_001_QSUB_STATUS_PATH", paths["status"]),
            mock.patch.object(recovery, "ATTEMPT_001_SUBMISSION_PATH", root / "submission"),
            mock.patch.object(recovery, "ATTEMPT_001_TERMINAL_PATH", root / "terminal"),
            mock.patch.object(recovery, "ATTEMPT_002_ROOT", root / "submission_attempt_002"),
            mock.patch.object(recovery, "ATTEMPT_001_EVIDENCE_FILENAMES", frozenset(path.name for path in paths.values())),
            mock.patch.object(
                recovery,
                "load_json",
                side_effect=lambda path, **kwargs: (
                    authority if Path(path) == paths["authority"] else claim
                ),
            ),
            mock.patch.object(recovery, "_validate_attempt_001_authority", return_value=None),
            mock.patch.object(
                recovery,
                "ATTEMPT_001_FROZEN_EVIDENCE",
                {
                    path.name: {
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in paths.values()
                },
            ),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            before, before_sha = recovery.validate_attempt_001_evidence(
                allow_attempt_002=False
            )
            assert before["stderr"]["bytes"] == 107
            for path in paths.values():
                original = path.read_bytes()
                path.write_bytes(original + b"x")
                with pytest.raises(
                    recovery.RecoveryError,
                    match=(
                        "RECOVERY_ATTEMPT_001_(?:REJECTION_EVIDENCE_INVALID|"
                        "FROZEN_HASH_MISMATCH)"
                    ),
                ):
                    recovery.validate_attempt_001_evidence(
                        allow_attempt_002=False
                    )
                path.write_bytes(original)
            after, after_sha = recovery.validate_attempt_001_evidence(
                allow_attempt_002=False
            )
        assert (before, before_sha) == (after, after_sha)


def test_attempt_002_claim_is_fixed_path_and_no_clobber() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        owner = _private_dir(Path(directory) / "owner")
        root = _private_dir(owner / "recovery")
        attempt = root / "submission_attempt_002"
        state = _synthetic_state(owner)
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", attempt / "authority.json"
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", attempt / "claim.json"):
            recovery.create_submission_claim(
                state,
                command=recovery.qsub_command(),
                environment=recovery.build_qsub_environment(
                    _scheduler_environment()
                ),
            )
            with pytest.raises(
                recovery.RecoveryError, match="RECOVERY_OUTPUT_COLLISION"
            ):
                recovery.create_submission_claim(
                    state,
                    command=recovery.qsub_command(),
                    environment=recovery.build_qsub_environment(
                        _scheduler_environment()
                    ),
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

        _private_dir(root / "recovery")
        attempt = root / "recovery/attempt2"
        patches = (
            mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt),
            mock.patch.object(recovery, "RECOVERY_AUTHORITY_PATH", attempt / "authority.json"),
            mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", attempt / "claim.json"),
            mock.patch.object(recovery, "RECOVERY_SUBMISSION_PATH", attempt / "submission.json"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", attempt / "stdout"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STDERR_PATH", attempt / "stderr"),
            mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", attempt / "status"),
            mock.patch.object(recovery, "RECOVERY_WORKER_LOG_PATH", attempt / "worker.log"),
            mock.patch.object(recovery, "validate_preserved_state", return_value=state),
            mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}),
            mock.patch.object(recovery, "_validate_scheduler_tools", return_value=None),
            mock.patch.object(recovery, "revalidate_attempt_001_snapshot", return_value=None),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = recovery.submit_recovery(
                environment=_scheduler_environment(),
                qstat_runner=_qstat_clear,
                runner=runner,
            )
        assert len(calls) == 1
        assert calls[0][calls[0].index("-o") + 1] == str(attempt / "worker.log")
        assert calls[0][calls[0].index("-N") + 1] == recovery.RECOVERY_JOB_NAME
        assert result["recovery_job_id"] == "8123456"
        assert result["scheduler_submissions"] == 1
        assert (attempt / "stdout").read_bytes() == b"8123456\n"
        assert (attempt / "stderr").read_bytes() == b"notice\n"
        receipt = json.loads((attempt / "submission.json").read_text())
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

        _private_dir(root / "recovery")
        attempt = root / "recovery/attempt2"
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", attempt / "authority.json"
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", attempt / "claim.json"), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", attempt / "submission.json"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", attempt / "stdout"), mock.patch.object(
            recovery, "RECOVERY_QSUB_STDERR_PATH", attempt / "stderr"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", attempt / "status"), mock.patch.object(
            recovery, "RECOVERY_WORKER_LOG_PATH", attempt / "worker.log"
        ), mock.patch.object(
            recovery, "validate_preserved_state", return_value=state
        ), mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}), mock.patch.object(
            recovery, "_validate_scheduler_tools", return_value=None
        ), mock.patch.object(recovery, "revalidate_attempt_001_snapshot", return_value=None):
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_QSUB_OUTPUT_IDENTITY_AMBIGUOUS"):
                recovery.submit_recovery(
                    environment=_scheduler_environment(),
                    qstat_runner=_qstat_clear,
                    runner=runner,
                )
        assert count == 1
        assert (attempt / "stdout").read_bytes() == stdout


def test_submit_preserves_nonzero_qsub_evidence_without_retry() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(root)
        count = 0

        def runner(command, **kwargs):
            nonlocal count
            count += 1
            return subprocess.CompletedProcess(command, 17, b"", b"safe restricted detail")

        _private_dir(root / "recovery")
        attempt = root / "recovery/attempt2"
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", attempt / "authority.json"
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", attempt / "claim.json"), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", attempt / "submission.json"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", attempt / "stdout"), mock.patch.object(
            recovery, "RECOVERY_QSUB_STDERR_PATH", attempt / "stderr"
        ), mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", attempt / "status"), mock.patch.object(
            recovery, "RECOVERY_WORKER_LOG_PATH", attempt / "worker.log"
        ), mock.patch.object(
            recovery, "validate_preserved_state", return_value=state
        ), mock.patch.object(recovery, "recovery_authority", return_value={"status": "PASS"}), mock.patch.object(
            recovery, "_validate_scheduler_tools", return_value=None
        ), mock.patch.object(recovery, "revalidate_attempt_001_snapshot", return_value=None):
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_QSUB_PROCESS_FAILED"):
                recovery.submit_recovery(
                    environment=_scheduler_environment(),
                    qstat_runner=_qstat_clear,
                    runner=runner,
                )
        assert count == 1
        assert (attempt / "status").read_text() == "17\n"
        assert (attempt / "stderr").read_bytes() == b"safe restricted detail"


@contextmanager
def _attempt_002_submission_fixture(root: Path):
    attempt = _private_dir(root / "submission_attempt_002")
    paths = {
        "authority": _write(attempt / "authority.json", b"{}\n"),
        "claim": _write(attempt / "claim.json", b"{}\n"),
        "stdout": _write(attempt / "stdout", b"8123456\n"),
        "stderr": _write(attempt / "stderr", b"notice\n"),
        "status": _write(attempt / "status", b"0\n"),
        "submission": attempt / "submission.json",
        "terminal": attempt / "terminal.json",
        "worker_log": attempt / "worker.log",
    }
    evidence_set_sha = "8" * 64
    submission = {
        "schema_version": 1,
        "artifact_type": (
            "lvef_c3_preservation_recovery_attempt_002_submission_v1"
        ),
        "status": "PASS_NUMERIC_QSUB_ID_CAPTURED",
        "submission_attempt": 2,
        "recovery_authority_sha256": recovery.sha256_file(paths["authority"]),
        "attempt_001_evidence_set_sha256": evidence_set_sha,
        "qsub_rejection_classification": recovery.QSUB_REJECTION_CLASSIFICATION,
        "qsub_exit_status": 0,
        "qsub_stdout_bytes": paths["stdout"].stat().st_size,
        "qsub_stdout_sha256": recovery.sha256_file(paths["stdout"]),
        "qsub_stderr_bytes": paths["stderr"].stat().st_size,
        "qsub_stderr_sha256": recovery.sha256_file(paths["stderr"]),
        "scheduler_job_id": "8123456",
        "scheduler_submission_count": 1,
        "captured_at_utc": "2026-08-13T12:00:00+00:00",
        "scientific_stage_reruns": 0,
        "cpu_only": True,
        **recovery.effect_zeros(),
        "production_continuation": False,
    }
    _write(paths["submission"], recovery.canonical_payload(submission))
    patches = (
        mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt),
        mock.patch.object(recovery, "RECOVERY_AUTHORITY_PATH", paths["authority"]),
        mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", paths["claim"]),
        mock.patch.object(recovery, "RECOVERY_QSUB_STDOUT_PATH", paths["stdout"]),
        mock.patch.object(recovery, "RECOVERY_QSUB_STDERR_PATH", paths["stderr"]),
        mock.patch.object(recovery, "RECOVERY_QSUB_STATUS_PATH", paths["status"]),
        mock.patch.object(recovery, "RECOVERY_SUBMISSION_PATH", paths["submission"]),
        mock.patch.object(recovery, "RECOVERY_TERMINAL_PATH", paths["terminal"]),
        mock.patch.object(recovery, "RECOVERY_WORKER_LOG_PATH", paths["worker_log"]),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield paths, submission, evidence_set_sha


def test_worker_requires_exact_successful_attempt_002_submission_and_job_id() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        with _attempt_002_submission_fixture(root) as (
            paths, submission, evidence_set_sha,
        ):
            observed = recovery.validate_attempt_002_submission_for_worker(
                "8123456",
                expected_attempt_001_evidence_set_sha256=evidence_set_sha,
                wait_cycles=1,
            )
            assert observed == submission
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ATTEMPT_002_SUBMISSION_INVALID",
            ):
                recovery.validate_attempt_002_submission_for_worker(
                    "8123457", wait_cycles=1
                )
            paths["status"].write_bytes(b"1\n")
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ATTEMPT_002_SUBMISSION_INVALID",
            ):
                recovery.validate_attempt_002_submission_for_worker(
                    "8123456", wait_cycles=1
                )


def test_worker_rejects_claim_only_and_unexpected_attempt_002_topology() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        with _attempt_002_submission_fixture(root) as (paths, _, _):
            paths["submission"].unlink()
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ATTEMPT_002_SUBMISSION_EVIDENCE_MISSING",
            ):
                recovery.validate_attempt_002_submission_for_worker(
                    "8123456", wait_cycles=1
                )
            _write(paths["submission"], b"{}\n")
            _write(paths["authority"].parent / "unexpected", b"x")
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ATTEMPT_002_TOPOLOGY_INVALID",
            ):
                recovery.validate_attempt_002_submission_for_worker(
                    "8123456", wait_cycles=1
                )


def test_worker_bounded_wait_accepts_only_exact_writer_partial_race() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "owner")
        with _attempt_002_submission_fixture(root) as (
            paths, submission, evidence_set_sha,
        ):
            partial = paths["submission"].with_name(
                f".{paths['submission'].name}.partial.4242"
            )
            _write(partial, paths["submission"].read_bytes())
            sleeps: list[float] = []

            def complete_atomic_promotion(seconds: float) -> None:
                sleeps.append(seconds)
                partial.unlink()

            observed = recovery.validate_attempt_002_submission_for_worker(
                "8123456",
                expected_attempt_001_evidence_set_sha256=evidence_set_sha,
                wait_cycles=2,
                sleeper=complete_atomic_promotion,
            )
            assert observed == submission
            assert sleeps == [0.1]

            _write(partial, paths["submission"].read_bytes())
            real_scandir = os.scandir
            scans: list[int] = []

            def unlink_after_scandir(path: Path):
                entries = list(real_scandir(path))
                scans.append(len(entries))
                if partial.exists():
                    partial.unlink()
                return entries

            race_sleeps: list[float] = []
            with mock.patch.object(
                recovery.os, "scandir", side_effect=unlink_after_scandir
            ):
                observed = recovery.validate_attempt_002_submission_for_worker(
                    "8123456",
                    expected_attempt_001_evidence_set_sha256=evidence_set_sha,
                    wait_cycles=2,
                    sleeper=race_sleeps.append,
                )
            assert observed == submission
            assert len(scans) == 2
            assert race_sleeps == [0.1]

            malformed = paths["submission"].with_name(
                f".{paths['submission'].name}.partial.not-a-pid"
            )
            _write(malformed, b"x")
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_ATTEMPT_002_TOPOLOGY_INVALID",
            ):
                recovery.validate_attempt_002_submission_for_worker(
                    "8123456",
                    wait_cycles=2,
                    sleeper=lambda _seconds: (_ for _ in ()).throw(
                        AssertionError("unexpected wait for malformed partial")
                    ),
                )


def test_recovery_authority_and_claim_are_closed_and_exact() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        owner = _private_dir(Path(directory) / "owner")
        state = _synthetic_state(owner)
        _private_dir(owner / "recovery")
        attempt_root = owner / "recovery/attempt2"
        authority_path = attempt_root / "authority.json"
        claim_path = attempt_root / "claim.json"
        environment = recovery.build_qsub_environment(_scheduler_environment())
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", attempt_root), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", authority_path
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", claim_path):
            recovery.create_submission_claim(
                state,
                command=recovery.qsub_command(),
                environment=environment,
            )
            recovery._validate_worker_claim(state)
            authority = json.loads(authority_path.read_text())
            claim = json.loads(claim_path.read_text())
            assert set(authority) == recovery.RECOVERY_AUTHORITY_KEYS
            assert set(claim) == recovery.RECOVERY_CLAIM_KEYS
            assert authority["cpu_only"] is claim["cpu_only"] is True
            assert authority["environment_receipt_sha256"] == recovery.ENVIRONMENT_RECEIPT_SHA256
            assert authority["attempt_001_status"] == "REJECTED_PRE_JOB"
            assert authority["qsub_rejection_classification"] == "QSUB_SCHEDULER_CONTEXT_MISSING"
            assert claim["recovery_base_implementation_commit"] == recovery.RECOVERY_BASE_IMPLEMENTATION_COMMIT
            expected_environment_sha = recovery._canonical_value_sha256(
                dict(sorted(environment.items()))
            )
            assert (
                authority["attempt_002_qsub_environment_sha256"]
                == claim["attempt_002_qsub_environment_sha256"]
                == expected_environment_sha
            )
            claim["cloud_requests"] = 1
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_SUBMISSION_CLAIM_INVALID"):
                recovery._validate_worker_claim(state)
            claim["cloud_requests"] = 0
            claim["unexpected"] = False
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with pytest.raises(recovery.RecoveryError, match="RECOVERY_SUBMISSION_CLAIM_INVALID"):
                recovery._validate_worker_claim(state)
            claim.pop("unexpected")
            names = authority["attempt_002_qsub_environment_variable_names"]
            names.remove("HOME")
            authority["attempt_002_qsub_environment_names_sha256"] = (
                recovery._canonical_value_sha256(names)
            )
            authority_path.write_text(
                json.dumps(authority, indent=2, sort_keys=True) + "\n"
            )
            claim["recovery_authority_sha256"] = recovery.sha256_file(
                authority_path
            )
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_SUBMISSION_CLAIM_INVALID",
            ):
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
        submission_receipt = _write(root / "submission.json", b"submission\n")
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
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", authority
        ), mock.patch.object(recovery, "RECOVERY_CLAIM_PATH", claim), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", submission_receipt
        ), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ), mock.patch.object(recovery, "PRESERVATION_MANIFEST_PATH", preservation_manifest), mock.patch.object(
            recovery, "PRESERVATION_RECEIPT_PATH", preservation_receipt
        ), mock.patch.object(recovery, "FINALIZATION_PATH", finalization), mock.patch.object(
            recovery, "ORIGINAL_TERMINAL_PATH", original_terminal
        ), mock.patch.object(recovery, "validate_preserved_state", return_value=state), mock.patch.object(
            recovery,
            "validate_attempt_002_submission_for_worker",
            return_value={
                "attempt_001_evidence_set_sha256": (
                    state.attempt_001_evidence_set_sha256
                )
            },
        ), mock.patch.object(
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
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", root), mock.patch.object(
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
        "mean_pool_study_embeddings(",
        "torch.cuda",
        "model.fit(",
        "predict(",
    )
    assert all(item not in source for item in forbidden)
    assert source.count("source.preserve(") == 1
    assert source.count("source.finalize(") == 1
    execute_source = source[source.index("def execute_recovery("):]
    assert execute_source.index(
        "submission = validate_attempt_002_submission_for_worker("
    ) < execute_source.index(
        "state = validate_preserved_state("
    ) < execute_source.index("source.preserve(")


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
    completed_states = list(
        orchestration_core.STATE_SEQUENCE[
            : orchestration_core.STATE_SEQUENCE.index("STUDY_POOLING_COMPLETE")
            + 1
        ]
    )
    events = [
        {
            "from_state": from_state,
            "to_state": to_state,
            "receipt_sha256": f"{index + 1:064x}",
        }
        for index, (from_state, to_state) in enumerate(
            zip(completed_states, completed_states[1:])
        )
    ]
    ledger = {
        "status": "ACTIVE",
        "batches": {
            recovery.BATCH_ID: {
                "state": "STUDY_POOLING_COMPLETE",
                "resume_state": None,
                "completed_states": completed_states,
                "events": events,
                "download_manifest_sha256": "d" * 64,
            }
        },
    }
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "root")
        download_manifest = _write(
            root / "verified_download_manifest.restricted.csv", b"download"
        )
        study_manifest = _write(root / "study_manifest.restricted.csv", b"study")
        ledger["batches"][recovery.BATCH_ID]["download_manifest_sha256"] = recovery.sha256_file(download_manifest)
        transition = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": recovery.RUN_ID,
            "batch_id": recovery.BATCH_ID,
            "from_state": "EMBEDDING_COMPLETE",
            "to_state": "STUDY_POOLING_COMPLETE",
            "status": "PASS",
            "authority": authority,
            "input_receipt_sha256": [events[-2]["receipt_sha256"]],
            "output_manifest_sha256": recovery.sha256_file(study_manifest),
        }
        events[-1]["receipt_sha256"] = orchestration_core.canonical_json_sha256(
            transition
        )
        transition_path = _write(
            root
            / "transition_receipts"
            / "study_pooling_complete.restricted.json",
            orchestration_core.canonical_json_bytes(transition),
        )
        ledger_path = _write(
            root / "pooling.json", (json.dumps(ledger) + "\n").encode()
        )
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
            ledger["batches"][recovery.BATCH_ID]["state"] = "STUDY_POOLING_COMPLETE"
            ledger_path.write_text(json.dumps(ledger) + "\n")
            transition["output_manifest_sha256"] = "f" * 64
            transition_path.write_bytes(
                orchestration_core.canonical_json_bytes(transition)
            )
            with pytest.raises(
                recovery.RecoveryError,
                match="RECOVERY_POOLING_LEDGER_STATE_INVALID",
            ):
                recovery.validate_pooling_ledger_state(plan, authority)


def test_failure_terminal_is_no_clobber_and_effect_free() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "recovery")
        terminal = root / "terminal.json"
        submission = _write(root / "submission.json", b"submission\n")
        authority = _write(root / "authority.json", b"authority\n")
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", submission
        ), mock.patch.object(
            recovery, "RECOVERY_AUTHORITY_PATH", authority
        ), mock.patch.object(
            recovery, "validate_attempt_002_submission_for_worker", return_value={}
        ), mock.patch.object(
            recovery,
            "validate_attempt_001_evidence",
            return_value=({}, "8" * 64),
        ):
            recovery.write_failure_terminal("RECOVERY_SYNTHETIC_FAILURE", "8123456")
            first = terminal.read_bytes()
            recovery.write_failure_terminal("RECOVERY_CHANGED", "8123456")
        assert terminal.read_bytes() == first
        value = json.loads(first)
        assert value["status"] == "FAIL"
        assert value["attempt_001_evidence_set_sha256"] == "8" * 64
        assert value["scientific_stage_reruns"] == 0
        assert value["cloud_requests"] == value["dicom_reads"] == 0
        assert value["gpu_execution"] == value["embedding_generation"] == 0


def test_claim_only_hidden_worker_cannot_create_failure_terminal() -> None:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = _private_dir(Path(directory) / "submission_attempt_002")
        terminal = root / "terminal.json"
        with mock.patch.object(recovery, "ATTEMPT_002_ROOT", root), mock.patch.object(
            recovery, "RECOVERY_TERMINAL_PATH", terminal
        ), mock.patch.object(
            recovery, "RECOVERY_SUBMISSION_PATH", root / "submission.json"
        ):
            recovery.write_failure_terminal(
                "RECOVERY_SUBMISSION_CLAIM_MISSING", "8123456"
            )
        assert not terminal.exists()
