from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no SCC, cloud, scheduler, or scientific data.

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Mapping, Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture_lvef_c3_production_environment as environment_capture
import finalize_lvef_phase1ef_d3 as recovery
import lvef_c3_execution_state as execution_state
import lvef_c3_phase1ef_authority_manifest as authority_manifest


TARGET_COMMIT = "e" * 40
CANONICAL_BYTES = 5786
CANONICAL_SHA256 = (
    "873faf00b11658d6b11f5778088cfc028b663ec579c244f5a470168cb5086695"
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _completed(
    argv: Sequence[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def _write(path: Path, payload: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _valid_receipt(
    commit: str,
    *,
    python_sha256: str,
    crc32c_sha256: str,
    worker_sha256: str,
    prior_sha256: str,
) -> dict[str, object]:
    runtime = {
        "python_version": "3.10.12",
        "torch_version": "2.11.0+cu130",
        "torchvision_version": "0.26.0+cu130",
        "cuda_version": "13.0",
        "cudnn_version": "91002",
    }
    return dict(
        environment_capture.build_receipt(
            prior=runtime,
            prior_sha256=prior_sha256,
            governing_commit=commit,
            python_executable_sha256=python_sha256,
            python_version=runtime["python_version"],
            torch_version=runtime["torch_version"],
            torchvision_version=runtime["torchvision_version"],
            cuda_version=runtime["cuda_version"],
            cudnn_version=runtime["cudnn_version"],
            crc32c_python_executable_sha256=crc32c_sha256,
            crc32c_worker_sha256=worker_sha256,
            crc32c_probe={
                "protocol_version": 1,
                "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
                "python_version": "3.14.0",
                "google_crc32c_version": "1.8.0",
                "google_crc32c_implementation": "c",
                "google_crc32c_distribution_sha256": "b" * 64,
                "google_crc32c_distribution_file_count": 1,
                "known_vector_crc32c_base64": "4waSgw==",
                "cloud_requests": 0,
            },
            packages=[{"name": "torch", "version": "2.11.0+cu130"}],
            captured_at_utc="2026-08-12T12:00:00+00:00",
        )
    )


class SyntheticRunner:
    def __init__(
        self,
        fixture: "D3Fixture",
        *,
        current: str,
        branch: str = recovery.BRANCH,
        status: str = "",
        remote: str = TARGET_COMMIT,
        ancestry_ok: bool = True,
        merge_range: str = "",
        capture_status: int = 0,
    ) -> None:
        self.fixture = fixture
        self.current = current
        self.branch = branch
        self.status = status
        self.remote = remote
        self.ancestry_ok = ancestry_ok
        self.merge_range = merge_range
        self.capture_status = capture_status
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.capture_calls = 0

    def __call__(
        self,
        command: Sequence[str],
        cwd: Path | None,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        argv = tuple(str(item) for item in command)
        self.commands.append(argv)
        self.environments.append(dict(environment))
        if argv[0] == "/usr/bin/git":
            args = argv[1:]
            if args == ("branch", "--show-current"):
                return _completed(argv, stdout=self.branch + "\n")
            if args == ("rev-parse", "HEAD"):
                return _completed(argv, stdout=self.current + "\n")
            if args == (
                "rev-parse",
                f"refs/remotes/origin/{recovery.BRANCH}",
            ):
                return _completed(argv, stdout=self.remote + "\n")
            if args == ("status", "--porcelain=v1", "--untracked-files=all"):
                return _completed(argv, stdout=self.status)
            if args[:2] == ("merge-base", "--is-ancestor"):
                return _completed(argv, returncode=0 if self.ancestry_ok else 1)
            if args == (
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{recovery.BRANCH}:refs/remotes/origin/{recovery.BRANCH}",
            ):
                return _completed(argv)
            if args[:2] == ("rev-list", "--merges"):
                return _completed(argv, stdout=self.merge_range)
            if args[:2] == ("merge", "--ff-only"):
                self.current = self.remote
                return _completed(argv)
            return _completed(argv, returncode=99, stderr="unexpected git role")

        if argv[0] == str(self.fixture.lexical_python):
            self.capture_calls += 1
            if self.capture_status:
                return _completed(argv, returncode=self.capture_status)
            output = Path(argv[argv.index("--output") + 1])
            self.fixture.write_receipt(output, TARGET_COMMIT)
            return _completed(argv, stdout='{"status":"PASS"}\n')
        return _completed(argv, returncode=99, stderr="unexpected command role")


class D3Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.audit_root = self.root / "audits"
        self.audit_root.mkdir(mode=0o700)
        self.worktree = self.root / "worktree"
        (self.worktree / "scripts").mkdir(parents=True)
        self.production_root = self.root / "production"
        self.production_root.mkdir(mode=0o700)
        (self.production_root / "attempts").mkdir(mode=0o700)
        self.attempt_roots = tuple(
            self.audit_root
            / f"{recovery._TRACKED_STATE.execution_attempt_namespace}_{i:03d}"
            for i in range(1, 6)
        )
        for attempt in self.attempt_roots[:4]:
            attempt.mkdir(mode=0o700)
        self.production_attempt_006 = (
            self.production_root
            / "attempts"
            / recovery._TRACKED_STATE.next_unused_production_attempt_id
        )

        self.diagnostic_root = (
            self.audit_root / "lvef_multitask_phase1ef_d3_environment_diagnostic_ABC12345"
        )
        self.diagnostic_root.mkdir(mode=0o700)
        self.preparation_root = self.audit_root / recovery.PREPARATION_DIRECTORY
        self.preparation_root.mkdir(mode=0o700)
        self.lexical_python = self.root / "echoprime" / "bin" / "python"

        self.prior_payload = b'{"synthetic":"prior-runtime-authority"}\n'
        self.prior = _write(self.root / "private" / "prior.json", self.prior_payload)
        self.crc_payload = b"#!/bin/sh\nexit 0\n"
        self.crc32c_python = _write(
            self.root / "private" / "crc32c-python", self.crc_payload, 0o700
        )
        for index, (role, (relative_path, mode)) in enumerate(
            authority_manifest.ROLE_SPECS.items(), start=1
        ):
            _write(
                self.worktree / relative_path,
                f"# synthetic manifest authority {index}: {role}\n".encode("utf-8"),
                mode,
            )
        self.manifest = self.preparation_root / "phase1ef_authority.json"
        logical_execution_attempt_id = (
            recovery._TRACKED_STATE.logical_execution_attempt_id
        )
        with mock.patch.object(
            authority_manifest,
            "AUTHORIZED_NEXT_EXECUTION_ATTEMPT_ID",
            logical_execution_attempt_id,
        ):
            manifest_value = authority_manifest.build_manifest_payload(
                worktree=self.worktree,
                attempt_id=logical_execution_attempt_id,
                git_branch=recovery.BRANCH,
                git_commit=recovery.LEGACY_D3_COMMIT,
                historical_base_commit=recovery.HISTORICAL_BASE,
                created_utc="2026-08-12T12:00:00Z",
            )
        manifest_payload = (json.dumps(manifest_value, sort_keys=True) + "\n").encode()
        _write(self.manifest, manifest_payload)
        manifest_sha = _sha(manifest_payload)
        environment_text = (
            f"WORKTREE={self.worktree}\n"
            f"EXPECTED_COMMIT={recovery.LEGACY_D3_COMMIT}\n"
            f"ATTEMPT_ID={logical_execution_attempt_id}\n"
            f"PHASE1EF_ATTEMPT_ID={logical_execution_attempt_id}\n"
            "PHASE1EF_EXECUTION_SCOPES_GRANTED=0\n"
            f"PYTHON={self.lexical_python}\n"
            f"PRIOR_ENVIRONMENT_RECEIPT={self.prior}\n"
            f"CRC32C_PYTHON={self.crc32c_python}\n"
            f"PRIOR_ENVIRONMENT_EXPECTED_SIZE={len(self.prior_payload)}\n"
            f"PRIOR_ENVIRONMENT_EXPECTED_SHA={_sha(self.prior_payload)}\n"
            f"PHASE1EF_AUTHORITY_MANIFEST={self.manifest}\n"
            f"PHASE1EF_AUTHORITY_MANIFEST_SHA256={manifest_sha}\n"
        )
        self.preparation_environment = _write(
            self.preparation_root / recovery.PREPARATION_ENVIRONMENT,
            environment_text.encode("utf-8"),
        )

        state_value = json.loads(
            (ROOT / "configs/lvef_c3_execution_state_v1.yaml").read_text(
                encoding="utf-8"
            )
        )
        state_value["preparation_environment_bytes"] = (
            self.preparation_environment.stat().st_size
        )
        self.state_path = _write(
            self.worktree / "configs/lvef_c3_execution_state_v1.yaml",
            (json.dumps(state_value, indent=2) + "\n").encode("utf-8"),
            0o644,
        )

        self.restricted_payload = b"synthetic frozen restricted capacity\n"
        self.aggregate_payload = b"synthetic frozen aggregate capacity\n"
        self.frozen_restricted = _write(
            self.attempt_roots[3]
            / "restricted"
            / "capacity"
            / "post_reallocation_capacity.restricted.json",
            self.restricted_payload,
        )
        self.frozen_aggregate = _write(
            self.attempt_roots[3]
            / "aggregate"
            / "lvef_c3_post_reallocation_capacity.summary.json",
            self.aggregate_payload,
            0o600,
        )

        self.python_target_payload = b"#!/bin/sh\nexit 0\n"
        self.python_target = _write(
            self.root / "echoprime" / "python-target",
            self.python_target_payload,
            0o700,
        )
        self.lexical_python.parent.mkdir(mode=0o700)
        self.lexical_python.symlink_to(self.python_target)

        self.worker_payload = b"# synthetic crc worker\n"
        self.worker = _write(
            self.worktree / "scripts" / "lvef_c3_crc32c_worker.py",
            self.worker_payload,
            0o644,
        )
        self.capture_payload = b"# synthetic capture entrypoint\n"
        _write(
            self.worktree / "scripts" / "capture_lvef_c3_production_environment.py",
            self.capture_payload,
            0o644,
        )
        self.python_sha = _sha(self.python_target_payload)
        self.crc_sha = _sha(self.crc_payload)
        self.worker_sha = _sha(self.worker_payload)

        self.config = recovery.RecoveryConfig(
            worktree=self.worktree,
            audit_root=self.audit_root,
            production_root=self.production_root,
            lexical_python=self.lexical_python,
            state_path=self.state_path,
        )

    def patches(self) -> ExitStack:
        stack = ExitStack()
        replacements = {
            "LEXICAL_ECHOPRIME_PYTHON": self.lexical_python,
            "ECHOPRIME_PYTHON_SHA256": self.python_sha,
            "CRC32C_PYTHON_SHA256": self.crc_sha,
            "CRC32C_WORKER_SHA256": self.worker_sha,
            "CAPTURE_SCRIPT_SHA256": _sha(self.capture_payload),
            "FROZEN_RESTRICTED_CAPACITY_BYTES": len(self.restricted_payload),
            "FROZEN_RESTRICTED_CAPACITY_SHA256": _sha(self.restricted_payload),
            "FROZEN_AGGREGATE_CAPACITY_BYTES": len(self.aggregate_payload),
            "FROZEN_AGGREGATE_CAPACITY_SHA256": _sha(self.aggregate_payload),
        }
        for name, value in replacements.items():
            stack.enter_context(mock.patch.object(recovery, name, value))
        stack.enter_context(
            mock.patch.object(
                environment_capture, "EXPECTED_PYTHON_SHA256", self.python_sha
            )
        )
        return stack

    @property
    def receipt(self) -> Path:
        return self.diagnostic_root / f"current_environment_{TARGET_COMMIT}.restricted.json"

    def write_receipt(self, path: Path | None = None, commit: str = TARGET_COMMIT) -> bytes:
        destination = path or self.receipt
        value = _valid_receipt(
            commit,
            python_sha256=self.python_sha,
            crc32c_sha256=self.crc_sha,
            worker_sha256=self.worker_sha,
            prior_sha256=_sha(self.prior_payload),
        )
        payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
        _write(destination, payload, 0o600)
        return payload


class EndToEndRunner(SyntheticRunner):
    """Run the real receipt producer; allow only exact control-plane roles."""

    def __call__(
        self,
        command: Sequence[str],
        cwd: Path | None,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        argv = tuple(str(item) for item in command)
        if argv[0] != str(self.fixture.lexical_python):
            return super().__call__(command, cwd, environment)

        self.commands.append(argv)
        self.environments.append(dict(environment))
        self.capture_calls += 1
        expected_prefix = (
            str(self.fixture.lexical_python),
            "-E",
            "-s",
            "-B",
            str(
                self.fixture.worktree
                / "scripts/capture_lvef_c3_production_environment.py"
            ),
        )
        assert argv[:5] == expected_prefix
        assert cwd == self.fixture.worktree
        assert environment.get("CUDA_VISIBLE_DEVICES") == ""
        capture_args = environment_capture.parse_args(argv[5:])

        runtime = {
            "python_version": platform.python_version(),
            "torch_version": "2.11.0+cu130",
            "torchvision_version": "0.26.0+cu130",
            "cuda_version": "13.0",
            "cudnn_version": "91002",
        }
        torch = SimpleNamespace(
            __version__=runtime["torch_version"],
            version=SimpleNamespace(cuda=runtime["cuda_version"]),
            backends=SimpleNamespace(
                cudnn=SimpleNamespace(version=lambda: int(runtime["cudnn_version"]))
            ),
        )
        torchvision = SimpleNamespace(__version__=runtime["torchvision_version"])

        def import_module(name: str):
            assert name in {"torch", "torchvision"}
            return torch if name == "torch" else torchvision

        def hash_file(path: Path) -> str:
            if path == self.fixture.prior:
                return _sha(self.fixture.prior_payload)
            if path == self.fixture.worker:
                return self.fixture.worker_sha
            raise AssertionError("unexpected hash role")

        def crc_probe(python: Path, worker: Path, *, expected_python_sha256: str):
            assert python == self.fixture.crc32c_python
            assert worker == self.fixture.worker
            assert expected_python_sha256 == self.fixture.crc_sha
            return {
                "protocol_version": 1,
                "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
                "python_version": "3.14.0",
                "google_crc32c_version": "1.8.0",
                "google_crc32c_implementation": "c",
                "google_crc32c_distribution_sha256": "b" * 64,
                "google_crc32c_distribution_file_count": 1,
                "known_vector_crc32c_base64": "4waSgw==",
                "cloud_requests": 0,
            }

        def write_temp(path: Path, value: Mapping[str, object]) -> Path:
            return environment_capture.write_receipt_temp(
                path, value, allowed_root=self.fixture.diagnostic_root
            )

        def promote(temporary: Path, destination: Path) -> None:
            environment_capture.promote_receipt_atomic(
                temporary,
                destination,
                allowed_root=self.fixture.diagnostic_root,
            )

        hooks = environment_capture.CaptureHooks(
            import_module=import_module,
            find_optional_package=lambda name: None,
            interpreter_authority=lambda: (
                self.fixture.python_target,
                self.fixture.python_sha,
            ),
            validate_checkout=lambda checkout, commit: (
                None
                if checkout == self.fixture.worktree and commit == TARGET_COMMIT
                else (_ for _ in ()).throw(AssertionError("checkout mismatch"))
            ),
            load_prior=lambda path: runtime,
            hash_file=hash_file,
            inventory=lambda: [
                {"name": "torch", "version": runtime["torch_version"]},
                {"name": "torchvision", "version": runtime["torchvision_version"]},
            ],
            crc_probe=crc_probe,
            write_temp=write_temp,
            promote=promote,
        )
        environment_capture.capture_environment(capture_args, hooks=hooks)
        return _completed(argv, stdout='{"status":"PASS"}\n')


def _error_code(operation) -> str:
    try:
        operation()
    except recovery.D3RecoveryError as exc:
        return exc.code
    raise AssertionError("D3 operation unexpectedly passed")


def _assert_no_mutating_command(runner: SyntheticRunner) -> None:
    assert runner.capture_calls == 0
    assert not any(
        command[0] == "/usr/bin/git"
        and command[1:2] in {("fetch",), ("merge",)}
        for command in runner.commands
    )


def test_d3_default_capacity_paths_match_frozen_attempt004_authority() -> None:
    assert recovery.FROZEN_RESTRICTED_CAPACITY == (
        recovery.ATTEMPT_ROOTS[3]
        / "restricted"
        / "capacity"
        / "post_reallocation_capacity.restricted.json"
    )
    assert recovery.FROZEN_AGGREGATE_CAPACITY == (
        recovery.ATTEMPT_ROOTS[3]
        / "aggregate"
        / "lvef_c3_post_reallocation_capacity.summary.json"
    )


def test_d3_wrapper_uses_approved_lexical_launcher_not_resolved_base() -> None:
    source = (SCRIPTS / "scc_finalize_lvef_phase1ef_d3.sh").read_text(
        encoding="utf-8"
    )
    assert source.startswith("#!/bin/bash -p\n")
    assert str(recovery.LEXICAL_ECHOPRIME_PYTHON) in source
    assert 'exec "$ECHOPRIME_PYTHON"' in source
    assert "SYSTEM_PYTHON=" not in source


def test_d3_canonical_reference_bytes_remain_exact_and_uninvoked() -> None:
    path = SCRIPTS / "scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh"
    assert path.stat().st_size == CANONICAL_BYTES
    assert _sha(path.read_bytes()) == CANONICAL_SHA256
    wrapper = (SCRIPTS / "scc_finalize_lvef_phase1ef_d3.sh").read_text()
    assert path.name not in wrapper


def test_d3_live_capture_entrypoint_hash_matches_tracked_bytes() -> None:
    path = SCRIPTS / "capture_lvef_c3_production_environment.py"
    assert _sha(path.read_bytes()) == recovery.CAPTURE_SCRIPT_SHA256


def test_d3_canonical_operation_mapping_is_closed_and_scope_never_broadens() -> None:
    path = ROOT / "docs" / "lvef_multitask" / "phase1ef_d3_canonical_operation_mapping.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert set(value) == {
        "schema_name", "schema_version", "canonical_artifact", "operative_entrypoint",
        "operative_implementation", "scope_broadened", "operation_groups",
        "prohibited_capabilities",
    }
    assert value["canonical_artifact"]["bytes"] == CANONICAL_BYTES
    assert value["canonical_artifact"]["sha256"] == CANONICAL_SHA256
    assert value["scope_broadened"] is False
    assert len(value["operation_groups"]) == 9
    assert set(value["prohibited_capabilities"].values()) == {False}


def test_d3_private_manifest_uses_exact_ten_key_seven_role_contract() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        value = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        assert set(value) == authority_manifest.TOP_LEVEL_KEYS
        assert value["created_utc"] == "2026-08-12T12:00:00Z"
        assert value["execution_scopes_granted"] == 0
        assert value["execution_scope_flags"] == authority_manifest.EXECUTION_SCOPE_FLAGS
        assert isinstance(value["authorities"], list)
        assert len(value["authorities"]) == 7
        assert {
            entry["logical_role"] for entry in value["authorities"]
        } == authority_manifest.AUTHORITY_ROLES
        assert all(
            set(entry) == authority_manifest.AUTHORITY_KEYS
            for entry in value["authorities"]
        )


def test_d3_starting_authority_requires_separate_fast_forward() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(
            fixture, current=recovery._TRACKED_STATE.starting_authority_commit
        )
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment",
                    config=fixture.config,
                    runner=runner,
                )
            ) == "STARTING_AUTHORITY_REQUIRES_FAST_FORWARD"
        assert runner.capture_calls == 0
        assert not fixture.receipt.exists()


def test_d3_current_commit_captures_without_git_mutation() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery("--capture-current-environment", config=fixture.config, runner=runner)
        assert result["SCC_COMMIT_EQUALITY"] == "PASS"
        assert runner.capture_calls == 1
        assert not any(
            command[1:2] in {("fetch",), ("merge",)}
            for command in runner.commands
        )


def test_phase1eg_true_end_to_end_preserved_attempt004_environment_capture() -> None:
    """One tracked invocation traverses the complete sanitized live topology."""
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = EndToEndRunner(fixture, current=TARGET_COMMIT)
        state = execution_state.load_execution_state(fixture.state_path)
        preparation_values = recovery.parse_literal_environment(
            fixture.preparation_environment,
            expected_size=state.preparation_environment_bytes,
        )
        manifest_value = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        assert state.logical_execution_attempt == 4
        assert state.next_unused_execution_attempt == 5
        assert state.preparation_sequence_id.endswith("_attempt_005")
        assert preparation_values["ATTEMPT_ID"] == state.logical_execution_attempt_id
        assert manifest_value["attempt_id"] == state.logical_execution_attempt_id

        before_files = {
            path.relative_to(fixture.root)
            for path in fixture.root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        restricted_before = (
            fixture.frozen_restricted.read_bytes(), fixture.frozen_restricted.stat().st_ino
        )
        aggregate_before = (
            fixture.frozen_aggregate.read_bytes(), fixture.frozen_aggregate.stat().st_ino
        )
        assert not os.path.lexists(fixture.attempt_roots[4])
        assert not os.path.lexists(fixture.production_attempt_006)

        stream = io.StringIO()
        with fixture.patches(), redirect_stdout(stream):
            status = recovery.main(
                ["--capture-current-environment"],
                config=fixture.config,
                runner=runner,
            )

        assert status == 0, stream.getvalue()
        assert "PREPARATION_BINDING_VALIDATION=PASS_LOGICAL_EXECUTION_ATTEMPT" in (
            stream.getvalue()
        )
        assert runner.capture_calls == 1
        receipt = json.loads(fixture.receipt.read_text(encoding="utf-8"))
        assert receipt["governing_commit"] == TARGET_COMMIT
        assert receipt["status"] == "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        assert all(
            receipt[key] is False
            for key in (
                "gpu_execution_performed",
                "cloud_request_performed",
                "dicom_body_read",
                "model_fitted",
                "prediction_generated",
                "confirmatory_performance_accessed",
            )
        )
        assert (
            fixture.frozen_restricted.read_bytes(), fixture.frozen_restricted.stat().st_ino
        ) == restricted_before
        assert (
            fixture.frozen_aggregate.read_bytes(), fixture.frozen_aggregate.stat().st_ino
        ) == aggregate_before
        assert not os.path.lexists(fixture.attempt_roots[4])
        assert not os.path.lexists(fixture.production_attempt_006)

        after_files = {
            path.relative_to(fixture.root)
            for path in fixture.root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        assert after_files - before_files == {fixture.receipt.relative_to(fixture.root)}
        assert all(
            command[0] in {"/usr/bin/git", str(fixture.lexical_python)}
            for command in runner.commands
        )
        joined = "\n".join(" ".join(command) for command in runner.commands).casefold()
        for forbidden in (
            "gcloud", "gsutil", "qsub", "objects.list", "alt=media",
            "dicom", "inference", "embedding", "model_fitting", "prediction",
            "confirmatory",
        ):
            assert forbidden not in joined


def test_d3_existing_valid_receipt_is_validated_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            before = fixture.write_receipt()
            inode = fixture.receipt.stat().st_ino
            result = recovery.run_recovery("--capture-current-environment", config=fixture.config, runner=runner)
        assert result["CURRENT_ENVIRONMENT_POSTCOMMIT_VALIDATION"] == "PASS"
        assert runner.capture_calls == 0
        assert fixture.receipt.read_bytes() == before
        assert fixture.receipt.stat().st_ino == inode


def test_d3_valid_receipt_binds_crc32c_executable_schema_key() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        with fixture.patches():
            fixture.write_receipt()
            size, digest = recovery.validate_current_receipt(
                fixture.receipt, TARGET_COMMIT
            )
        assert size > 0 and digest == _sha(fixture.receipt.read_bytes())


def test_d3_existing_invalid_receipt_is_preserved_and_capture_stops() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        invalid = b'{"status":"invalid"}\n'
        _write(fixture.receipt, invalid)
        inode = fixture.receipt.stat().st_ino
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "RECEIPT_SCHEMA_INVALID"
        assert runner.capture_calls == 0
        assert fixture.receipt.read_bytes() == invalid
        assert fixture.receipt.stat().st_ino == inode


def test_d3_missing_private_authority_stops_before_git_mutation() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.prior.unlink()
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "AUTHORITY_MISSING"
        _assert_no_mutating_command(runner)


def test_d3_ambiguous_diagnostic_authority_stops_before_git_mutation() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        (fixture.audit_root / "lvef_multitask_phase1ef_d3_environment_diagnostic_ZYX98765").mkdir(
            mode=0o700
        )
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "DIAGNOSTIC_AUTHORITY_AMBIGUOUS"
        _assert_no_mutating_command(runner)


def test_d3_wrong_capacity_size_or_hash_stops_before_git_mutation() -> None:
    for mutation in (b"short", b"synthetic frozen restricted capacitx\n"):
        with tempfile.TemporaryDirectory() as raw:
            fixture = D3Fixture(Path(raw))
            fixture.frozen_restricted.write_bytes(mutation)
            fixture.frozen_restricted.chmod(0o600)
            runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
            with fixture.patches():
                code = _error_code(
                    lambda: recovery.run_recovery(
                        "--capture-current-environment", config=fixture.config, runner=runner
                    )
                )
            assert code in {"AUTHORITY_SIZE_MISMATCH", "AUTHORITY_HASH_MISMATCH"}
            _assert_no_mutating_command(runner)


def test_d3_dirty_checkout_stops_before_fetch_or_capture() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT, status=" M tracked.py\n")
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "TRACKED_WORKTREE_DIRTY"
        assert not any(command[1:2] == ("fetch",) for command in runner.commands)
        assert runner.capture_calls == 0


def test_d3_cleanliness_allows_only_two_preserved_ds_store_files() -> None:
    recovery.validate_clean_status("?? .DS_Store\n?? docs/.DS_Store\n")
    assert _error_code(lambda: recovery.validate_clean_status("?? other.tmp\n")) == (
        "TRACKED_WORKTREE_DIRTY"
    )


def test_d3_wrong_branch_or_origin_commit_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT, branch="other")
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "BRANCH_AUTHORITY_MISMATCH"
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current="d" * 40)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "ORIGIN_COMMIT_MISMATCH"


def test_d3_wrong_ancestry_is_rejected_and_preflight_never_mutates_git() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(
            fixture, current=TARGET_COMMIT, ancestry_ok=False
        )
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "GIT_AUTHORITY_FAILURE"
        assert runner.capture_calls == 0
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery(
                "--preflight-only", config=fixture.config, runner=runner
            )
        assert result["CURRENT_ENVIRONMENT_CAPTURE"] == (
            "NOT_PERFORMED_PREFLIGHT_ONLY"
        )
        _assert_no_mutating_command(runner)


def test_d3_requires_starting_authority_as_current_head_ancestor() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))

        class StartingAuthorityRejectingRunner(SyntheticRunner):
            def __call__(self, command, cwd, environment):
                argv = tuple(str(item) for item in command)
                if argv[0] == "/usr/bin/git" and argv[1:] == (
                    "merge-base",
                    "--is-ancestor",
                    recovery._TRACKED_STATE.starting_authority_commit,
                    TARGET_COMMIT,
                ):
                    self.commands.append(argv)
                    self.environments.append(dict(environment))
                    return _completed(argv, returncode=1)
                return super().__call__(command, cwd, environment)

        runner = StartingAuthorityRejectingRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--preflight-only", config=fixture.config, runner=runner
                )
            ) == "GIT_AUTHORITY_FAILURE"
        _assert_no_mutating_command(runner)


def test_d3_attempt005_presence_blocks_before_git() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.attempt_roots[4].mkdir(mode=0o700)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "PROHIBITED_ATTEMPT_ROOT_PRESENT"
        _assert_no_mutating_command(runner)


def test_d3_production_attempt006_presence_blocks_before_git() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.production_attempt_006.mkdir(parents=True, mode=0o700)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            )
        assert code == "PROHIBITED_ATTEMPT_ROOT_PRESENT"
        _assert_no_mutating_command(runner)


def test_d3_attempt004_is_read_only_and_has_no_rerun_command_role() -> None:
    source = (SCRIPTS / "finalize_lvef_phase1ef_d3.py").read_text(encoding="utf-8")
    assert "scc_execute_lvef_c3_phase1ef_attempt.sh" not in source
    assert "scc_capture_lvef_c3_post_reallocation_capacity.sh" not in source
    assert '"LOGICAL_EXECUTION_ATTEMPT_RERUN": "NO"' in source


def test_d3_alternate_or_resolved_launcher_substitution_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        alternate = fixture.root / "alternate-python"
        alternate.symlink_to(fixture.python_target)
        with fixture.patches():
            assert _error_code(lambda: recovery.require_lexical_launcher(alternate)) == (
                "LEXICAL_LAUNCHER_SUBSTITUTION"
            )
            assert _error_code(
                lambda: recovery.require_lexical_launcher(fixture.python_target)
            ) == "LEXICAL_LAUNCHER_SUBSTITUTION"


def test_d3_symlinked_private_authority_file_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        real = fixture.prior.with_name("prior-real.json")
        fixture.prior.rename(real)
        fixture.prior.symlink_to(real)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "AUTHORITY_SYMLINK_COMPONENT"
        _assert_no_mutating_command(runner)


def test_d3_literal_private_environment_rejects_duplicate_and_shell_syntax() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        duplicate = (
            f"PRIOR_ENVIRONMENT_RECEIPT={fixture.prior}\n"
            f"PRIOR_ENVIRONMENT_RECEIPT={fixture.prior}\n"
            f"CRC32C_PYTHON={fixture.crc32c_python}\n"
        ).encode()
        fixture.preparation_environment.write_bytes(duplicate)
        fixture.preparation_environment.chmod(0o600)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.parse_literal_environment(
                    fixture.preparation_environment, expected_size=len(duplicate)
                )
            ) == "PRIVATE_ENVIRONMENT_SCHEMA_INVALID"
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        injected = b"PRIOR_ENVIRONMENT_RECEIPT=$(touch${IFS}marker)\nCRC32C_PYTHON=/bin/sh\n"
        fixture.preparation_environment.write_bytes(injected)
        fixture.preparation_environment.chmod(0o600)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.parse_literal_environment(
                    fixture.preparation_environment, expected_size=len(injected)
                )
            ) == "PRIVATE_ENVIRONMENT_VALUE_INVALID"


def test_d3_strict_receipt_parser_rejects_duplicate_json_keys() -> None:
    with tempfile.TemporaryDirectory() as raw:
        path = _write(Path(raw).resolve() / "receipt.json", b'{"a":1,"a":2}\n')
        assert _error_code(lambda: recovery.load_strict_json(path)) == (
            "RECEIPT_DUPLICATE_KEY"
        )


def test_d3_capture_uses_lexical_launcher_and_no_prohibited_command_roles() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            recovery.run_recovery("--capture-current-environment", config=fixture.config, runner=runner)
        non_git = [command for command in runner.commands if command[0] != "/usr/bin/git"]
        assert len(non_git) == 1 and non_git[0][0] == str(fixture.lexical_python)
        assert non_git[0][1:4] == ("-E", "-s", "-B")
        joined = "\n".join(" ".join(command) for command in runner.commands).casefold()
        for forbidden in (
            "gcloud", "gsutil", "qsub", "objects.list", "alt=media",
            "echoprime", "embedding", "prediction", "confirmatory",
        ):
            # The synthetic lexical directory is deliberately named `echoprime`;
            # command-role review, rather than substring review, governs it.
            if forbidden == "echoprime":
                continue
            assert forbidden not in joined


def test_d3_capture_failure_is_closed_and_does_not_create_attempt_roots() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT, capture_status=2)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "CURRENT_ENVIRONMENT_CAPTURE_FAILED"
        assert not os.path.lexists(fixture.attempt_roots[4])
        assert not os.path.lexists(fixture.production_attempt_006)


def test_d3_success_output_schema_is_closed_ordered_and_path_free() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery("--capture-current-environment", config=fixture.config, runner=runner)
        assert tuple(result) == recovery.SAFE_CAPTURE_KEYS
        serialized = json.dumps(result, sort_keys=True)
        assert str(fixture.root) not in serialized
        assert set(result.values()).isdisjoint(
            {str(fixture.prior), str(fixture.crc32c_python), str(fixture.diagnostic_root)}
        )


def test_d3_failure_output_is_fixed_and_does_not_disclose_private_values() -> None:
    private_value = "/private/synthetic/do-not-print"
    stream = io.StringIO()
    with mock.patch.object(
        recovery,
        "run_recovery",
        side_effect=recovery.D3RecoveryError("PRIVATE_AUTHORITY_ROLE_MISSING"),
    ), redirect_stdout(stream):
        status = recovery.main(["--preflight-only"])
    output = stream.getvalue()
    assert status == 65
    assert output.splitlines() == [
        "D3_TRACKED_RECOVERY=FAILED",
        "D3_TRACKED_RECOVERY_ERROR=PRIVATE_AUTHORITY_ROLE_MISSING",
    ]
    assert private_value not in output and "/restricted/" not in output


def test_d3_clean_subprocess_environment_excludes_cloud_and_credential_overrides() -> None:
    hostile = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/credential",
        "CLOUDSDK_CONFIG": "/private/cloudsdk",
        "LVEF_C3_GCP_BILLING_PROJECT": "private-project",
        "GOOGLE_OAUTH_ACCESS_TOKEN": "secret",
        "GIT_DIR": "/hostile/git",
        "PYTHONPATH": "/hostile/python",
        "HOME": "/synthetic/home",
    }
    with mock.patch.dict(os.environ, hostile, clear=True):
        result = recovery.clean_environment()
    assert not set(hostile) & set(result)
    assert result["PATH"] == "/usr/bin:/bin"


def test_d3_expected_commit_and_single_short_wrapper_interface_are_closed() -> None:
    assert _error_code(lambda: recovery.run_recovery("not-a-mode")) == (
        "DISPATCH_MODE_INVALID"
    )
    wrapper = (SCRIPTS / "scc_finalize_lvef_phase1ef_d3.sh").read_text(
        encoding="utf-8"
    )
    assert "[[ $# -ne 1" in wrapper
    assert "finalize_lvef_phase1ef_d3.py" in wrapper
    assert "heredoc" not in wrapper.casefold()
    assert "base64" not in wrapper.casefold()
    assert "scp " not in wrapper.casefold()
    assert "sftp" not in wrapper.casefold()
    assert "rsync" not in wrapper.casefold()


def test_d3_repository_sources_contain_no_operational_cloud_or_scientific_entrypoints() -> None:
    python_source = (SCRIPTS / "finalize_lvef_phase1ef_d3.py").read_text(
        encoding="utf-8"
    )
    shell_source = (SCRIPTS / "scc_finalize_lvef_phase1ef_d3.sh").read_text(
        encoding="utf-8"
    )
    for executable_token in (
        "gcloud ", "gsutil ", "qsub ", "objects.list", "alt=media",
        "scc_run_lvef_c3", "scc_dispatch_lvef_c3", "echoprime_smoke_test.py",
        "run_multitask", "run_multimodal_fusion",
    ):
        assert executable_token not in python_source
        assert executable_token not in shell_source


def test_d3_unexpected_exception_is_sanitized_without_traceback() -> None:
    stream = io.StringIO()
    with mock.patch.object(
        recovery, "run_recovery", side_effect=RuntimeError("/private/do-not-emit")
    ), redirect_stdout(stream):
        status = recovery.main(["--preflight-only"])
    assert status == 70
    assert stream.getvalue().splitlines() == [
        "D3_TRACKED_RECOVERY=FAILED",
        "D3_TRACKED_RECOVERY_ERROR=UNEXPECTED_RUNTIME_FAILURE",
    ]


def test_d3_receipt_is_bound_to_prior_environment_and_worker() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        with fixture.patches():
            fixture.write_receipt()
            assert _error_code(
                lambda: recovery.validate_current_receipt(
                    fixture.receipt,
                    TARGET_COMMIT,
                    prior_environment_sha256="f" * 64,
                )
            ) == "RECEIPT_PRIOR_ENVIRONMENT_INVALID"
            value = json.loads(fixture.receipt.read_text())
            value["crc32c_worker_sha256"] = "f" * 64
            fixture.receipt.write_text(json.dumps(value, sort_keys=True) + "\n")
            fixture.receipt.chmod(0o600)
            assert _error_code(
                lambda: recovery.validate_current_receipt(
                    fixture.receipt,
                    TARGET_COMMIT,
                    prior_environment_sha256=_sha(fixture.prior_payload),
                )
            ) == "RECEIPT_CRC32C_WORKER_INVALID"


def test_d3_capture_environment_masks_gpu_and_excludes_secret_overrides() -> None:
    hostile = {
        "CUDA_VISIBLE_DEVICES": "0,1",
        "LD_LIBRARY_PATH": "/synthetic/cuda",
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/credential",
        "CLOUDSDK_CONFIG": "/private/cloudsdk",
        "SSH_AUTH_SOCK": "/private/agent",
        "HOME": "/private/home",
    }
    with mock.patch.dict(os.environ, hostile, clear=True):
        value = recovery.capture_environment()
    assert value["CUDA_VISIBLE_DEVICES"] == ""
    assert value["LD_LIBRARY_PATH"] == "/synthetic/cuda"
    assert not ({"GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_CONFIG", "SSH_AUTH_SOCK", "HOME"} & set(value))


def test_d3_final_revalidation_detects_concurrent_attempt005_creation() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))

        class ConcurrentRunner(SyntheticRunner):
            def __call__(self, command, cwd, environment):
                result = super().__call__(command, cwd, environment)
                if command[0] == str(self.fixture.lexical_python) and result.returncode == 0:
                    self.fixture.attempt_roots[4].mkdir(mode=0o700)
                return result

        runner = ConcurrentRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "PROHIBITED_ATTEMPT_ROOT_PRESENT"


def test_d3_unsafe_authority_mode_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.frozen_aggregate.chmod(0o666)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    "--capture-current-environment", config=fixture.config, runner=runner
                )
            ) == "AUTHORITY_MODE_INVALID"
        _assert_no_mutating_command(runner)
