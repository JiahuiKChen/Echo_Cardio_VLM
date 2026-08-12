from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no SCC, cloud, scheduler, or scientific data.

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture_lvef_c3_production_environment as environment_capture
import finalize_lvef_phase1ef_d3 as recovery
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
            self.audit_root / f"lvef_multitask_phase1ef_post_reallocation_lock_attempt_{i:03d}"
            for i in range(1, 6)
        )
        for attempt in self.attempt_roots[:4]:
            attempt.mkdir(mode=0o700)
        self.production_attempt_006 = (
            self.production_root / "attempts" / "lvef_c3_phase1ee_production_lock_006"
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
        manifest_value = authority_manifest.build_manifest_payload(
            worktree=self.worktree,
            attempt_id=authority_manifest.AUTHORIZED_ATTEMPT_ID,
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
            "ATTEMPT_ID=lvef_multitask_phase1ef_post_reallocation_lock_attempt_005\n"
            "PHASE1EF_ATTEMPT_ID=lvef_multitask_phase1ef_post_reallocation_lock_attempt_005\n"
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
            production_attempt_006=self.production_attempt_006,
            attempt_roots=self.attempt_roots,
            lexical_python=self.lexical_python,
            frozen_restricted_capacity=self.frozen_restricted,
            frozen_aggregate_capacity=self.frozen_aggregate,
        )

    def patches(self) -> ExitStack:
        stack = ExitStack()
        replacements = {
            "LEXICAL_ECHOPRIME_PYTHON": self.lexical_python,
            "ECHOPRIME_PYTHON_SHA256": self.python_sha,
            "CRC32C_PYTHON_SHA256": self.crc_sha,
            "CRC32C_WORKER_SHA256": self.worker_sha,
            "CAPTURE_SCRIPT_SHA256": _sha(self.capture_payload),
            "PREPARATION_ENVIRONMENT_BYTES": self.preparation_environment.stat().st_size,
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


def _error_code(operation) -> str:
    try:
        operation()
    except recovery.D3RecoveryError as exc:
        return exc.code
    raise AssertionError("D3 operation unexpectedly passed")


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


def test_d3_old_commit_fast_forwards_once_then_captures_receipt() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=recovery.LEGACY_D3_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery(TARGET_COMMIT, config=fixture.config, runner=runner)
        assert result["SCC_D3_FAST_FORWARD"] == "PASS"
        assert runner.capture_calls == 1
        assert sum(command[1:3] == ("merge", "--ff-only") for command in runner.commands) == 1
        assert fixture.receipt.is_file()


def test_d3_current_commit_captures_without_repeated_migration() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery(TARGET_COMMIT, config=fixture.config, runner=runner)
        assert result["SCC_D3_FAST_FORWARD"] == "ALREADY_COMPLETE"
        assert runner.capture_calls == 1
        assert not any(command[1:3] == ("merge", "--ff-only") for command in runner.commands)


def test_d3_existing_valid_receipt_is_validated_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            before = fixture.write_receipt()
            inode = fixture.receipt.stat().st_ino
            result = recovery.run_recovery(TARGET_COMMIT, config=fixture.config, runner=runner)
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
                    TARGET_COMMIT, config=fixture.config, runner=runner
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
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            )
        assert code == "AUTHORITY_MISSING"
        assert runner.commands == []


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
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            )
        assert code == "DIAGNOSTIC_AUTHORITY_AMBIGUOUS"
        assert runner.commands == []


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
                        TARGET_COMMIT, config=fixture.config, runner=runner
                    )
                )
            assert code in {"AUTHORITY_SIZE_MISMATCH", "AUTHORITY_HASH_MISMATCH"}
            assert runner.commands == []


def test_d3_dirty_checkout_stops_before_fetch_or_capture() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT, status=" M tracked.py\n")
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
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


def test_d3_wrong_branch_or_starting_commit_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT, branch="other")
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "BRANCH_AUTHORITY_MISMATCH"
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current="d" * 40)
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "STARTING_COMMIT_UNAUTHORIZED"


def test_d3_wrong_ancestry_or_merge_commit_range_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(
            fixture, current=recovery.LEGACY_D3_COMMIT, ancestry_ok=False
        )
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "GIT_AUTHORITY_FAILURE"
        assert runner.capture_calls == 0
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(
            fixture,
            current=recovery.LEGACY_D3_COMMIT,
            merge_range="a" * 40 + "\n",
        )
        with fixture.patches():
            assert _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "MERGE_COMMIT_IN_MIGRATION_RANGE"
        assert not any(command[1:3] == ("merge", "--ff-only") for command in runner.commands)


def test_d3_attempt005_presence_blocks_before_git() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.attempt_roots[4].mkdir(mode=0o700)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            )
        assert code == "PROHIBITED_ATTEMPT_ROOT_PRESENT"
        assert runner.commands == []


def test_d3_production_attempt006_presence_blocks_before_git() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        fixture.production_attempt_006.mkdir(parents=True, mode=0o700)
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            code = _error_code(
                lambda: recovery.run_recovery(
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            )
        assert code == "PROHIBITED_ATTEMPT_ROOT_PRESENT"
        assert runner.commands == []


def test_d3_attempt004_is_read_only_and_has_no_rerun_command_role() -> None:
    source = (SCRIPTS / "finalize_lvef_phase1ef_d3.py").read_text(encoding="utf-8")
    assert "scc_execute_lvef_c3_phase1ef_attempt.sh" not in source
    assert "scc_capture_lvef_c3_post_reallocation_capacity.sh" not in source
    assert '"ATTEMPT_004_RERUN": "NO"' in source


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
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "AUTHORITY_SYMLINK_COMPONENT"
        assert runner.commands == []


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
        with fixture.patches(), mock.patch.object(
            recovery, "PREPARATION_ENVIRONMENT_BYTES", len(duplicate)
        ):
            assert _error_code(
                lambda: recovery.parse_literal_environment(
                    fixture.preparation_environment
                )
            ) == "PRIVATE_ENVIRONMENT_SCHEMA_INVALID"
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        injected = b"PRIOR_ENVIRONMENT_RECEIPT=$(touch${IFS}marker)\nCRC32C_PYTHON=/bin/sh\n"
        fixture.preparation_environment.write_bytes(injected)
        fixture.preparation_environment.chmod(0o600)
        with fixture.patches(), mock.patch.object(
            recovery, "PREPARATION_ENVIRONMENT_BYTES", len(injected)
        ):
            assert _error_code(
                lambda: recovery.parse_literal_environment(
                    fixture.preparation_environment
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
            recovery.run_recovery(TARGET_COMMIT, config=fixture.config, runner=runner)
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
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "CURRENT_ENVIRONMENT_CAPTURE_FAILED"
        assert not os.path.lexists(fixture.attempt_roots[4])
        assert not os.path.lexists(fixture.production_attempt_006)


def test_d3_success_output_schema_is_closed_ordered_and_path_free() -> None:
    with tempfile.TemporaryDirectory() as raw:
        fixture = D3Fixture(Path(raw))
        runner = SyntheticRunner(fixture, current=TARGET_COMMIT)
        with fixture.patches():
            result = recovery.run_recovery(TARGET_COMMIT, config=fixture.config, runner=runner)
        assert tuple(result) == recovery.SAFE_SUCCESS_KEYS
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
        status = recovery.main([TARGET_COMMIT])
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
    assert _error_code(lambda: recovery.run_recovery("not-a-commit")) == (
        "EXPECTED_COMMIT_INVALID"
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
        status = recovery.main([TARGET_COMMIT])
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
                    TARGET_COMMIT, config=fixture.config, runner=runner
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
                    TARGET_COMMIT, config=fixture.config, runner=runner
                )
            ) == "AUTHORITY_MODE_INVALID"
        assert runner.commands == []
