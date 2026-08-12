from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no SCC, cloud, scheduler, or scientific data.
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DISPATCHER = SCRIPTS / "scc_execute_lvef_c3_phase1ef_attempt.sh"
PREPARER = SCRIPTS / "scc_prepare_lvef_c3_phase1ef_environment.sh"
WRAPPER = SCRIPTS / "scc_capture_lvef_c3_post_reallocation_capacity.sh"
MANIFEST_TOOL = SCRIPTS / "lvef_c3_phase1ef_authority_manifest.py"
ATTEMPT = "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
BASE = "23c74ccfd145ab9a423b6942a431a1894a34ab67"
PINNED_PYTHON_SHA = (
    "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
)

sys.path.insert(0, str(SCRIPTS))
import lvef_c3_phase1ef_authority_manifest as authority
import archive_lvef_c3_phase1ef_environment as archive_tool


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **kwargs,
    )


def _copy_authority_worktree(
    root: Path, *, synthetic_python_sha256: str
) -> tuple[Path, str]:
    worktree = root / "worktree"
    git = "/usr/bin/git"
    result = _run(
        [
            git,
            "clone",
            "-q",
            "--no-local",
            "--branch",
            authority.AUTHORIZED_BRANCH,
            str(ROOT),
            str(worktree),
        ]
    )
    assert result.returncode == 0, result.stderr
    relative_files = {
        relative for relative, _ in authority.ROLE_SPECS.values()
    }
    relative_files.add("scripts/lvef_c3_phase1ef_authority_manifest.py")
    for relative in sorted(relative_files):
        source = ROOT / relative
        destination = worktree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    dispatcher = worktree / authority.ROLE_SPECS["tracked_attempt_dispatcher"][0]
    dispatcher_payload = dispatcher.read_text(encoding="utf-8")
    assert PINNED_PYTHON_SHA in dispatcher_payload
    dispatcher.write_text(
        dispatcher_payload.replace(PINNED_PYTHON_SHA, synthetic_python_sha256),
        encoding="utf-8",
    )
    dispatcher.chmod(0o755)
    for argv in (
        [git, "config", "user.email", "synthetic@example.invalid"],
        [git, "config", "user.name", "Synthetic Test"],
        [git, "add", "."],
        [git, "commit", "-q", "-m", "synthetic authority"],
    ):
        result = _run(argv, cwd=worktree)
        assert result.returncode == 0, result.stderr
    commit = _run([git, "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    result = _run(
        [
            git,
            "update-ref",
            f"refs/remotes/origin/{authority.AUTHORIZED_BRANCH}",
            commit,
        ],
        cwd=worktree,
    )
    assert result.returncode == 0, result.stderr
    return worktree.resolve(), commit


def _write_attack_shims(root: Path) -> Path:
    tools = root / "tools"
    tools.mkdir(mode=0o700)
    for name in (
        "git", "openssl", "sha256sum", "stat", "readlink", "gcloud", "qsub"
    ):
        (tools / name).write_text(
            "#!/bin/bash\n"
            f"touch {root / (name + '.called')!s}\n"
            "exit 99\n",
            encoding="utf-8",
        )
    for path in tools.iterdir():
        path.chmod(0o700)
    return tools


def _environment_names() -> list[str]:
    source = DISPATCHER.read_text(encoding="utf-8")
    match = re.search(
        r"phase1ef_allowed_environment_names=\(\n(?P<body>.*?)\n\)",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    names = match.group("body").split()
    assert names and len(names) == len(set(names))
    return names


def _synthetic_authority(root: Path) -> tuple[Path, Path, dict[str, str], dict[str, str]]:
    private = root / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    fake_python = private / "pinned_python"
    fake_python.write_text(
        f"#!/bin/bash\nexec {sys.executable!s} \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    fake_python_sha256 = hashlib.sha256(fake_python.read_bytes()).hexdigest()
    worktree, commit = _copy_authority_worktree(
        root, synthetic_python_sha256=fake_python_sha256
    )
    manifest = private / "authority.json"
    digest = authority.write_manifest_atomic(
        output_path=manifest,
        worktree=worktree,
        attempt_id=ATTEMPT,
        git_branch=authority.AUTHORIZED_BRANCH,
        git_commit=commit,
        historical_base_commit=BASE,
        created_utc="2026-08-11T21:00:00Z",
    )
    tools = _write_attack_shims(root)
    values = {name: "1" for name in _environment_names()}
    values.update(
        {
            "WORKTREE": str(worktree),
            "EXPECTED_COMMIT": commit,
            "PYTHON": str(fake_python),
            "PYTHON_AUTHORITY": str(fake_python),
            "GCLOUD": "/synthetic/never-executed-gcloud",
            "PRODUCTION_ROOT": "/restricted/projectnb/mimicecho/lvef_multitask_c3_v2",
            "ATTEMPT_ID": ATTEMPT,
            "PHASE1EF_ATTEMPT_ID": ATTEMPT,
            "PHASE1EF_ATTEMPT_ROOT": (
                "/restricted/projectnb/mimicecho/audits/" + ATTEMPT
            ),
            "PHASE1EF_AUTHORITY_MANIFEST": str(manifest),
            "PHASE1EF_AUTHORITY_MANIFEST_SHA256": digest,
            "PHASE1EF_EXECUTION_SCOPES_GRANTED": "0",
            "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-project",
        }
    )
    process_environment = dict(os.environ)
    process_environment["PATH"] = f"{tools}{os.pathsep}{process_environment['PATH']}"
    process_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return worktree, private, values, process_environment


def _write_environment(private: Path, values: dict[str, str]) -> Path:
    path = private / "phase1ef.env"
    path.write_text(
        "".join(
            f"{name}={values[name]}\n"
            for name in _environment_names()
            if name in values
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _dispatch(
    worktree: Path,
    environment_path: Path,
    process_environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    invocation_environment = dict(process_environment)
    invocation_environment["PHASE1EF_ENV"] = str(environment_path)
    return _run(
        [
            str(worktree / "scripts/scc_execute_lvef_c3_phase1ef_attempt.sh"),
            "--preflight-only",
        ],
        cwd=worktree,
        env=invocation_environment,
    )


def test_phase1ef_dispatcher_production_path_preflight_only_passes_without_side_effects() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        worktree, private, values, process_environment = _synthetic_authority(root)
        shadow = root / "shadow"
        shadow.mkdir(mode=0o700)
        shadow_marker = root / "python-shadow.called"
        (shadow / "json.py").write_text(
            f"from pathlib import Path\nPath({str(shadow_marker)!r}).touch()\n",
            encoding="utf-8",
        )
        process_environment["PYTHONPATH"] = str(shadow)
        environment_path = _write_environment(private, values)
        result = _dispatch(worktree, environment_path, process_environment)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "PHASE1EF_TRACKED_DISPATCHER_PREFLIGHT=PASS_ZERO_SCOPE_NO_ROOTS" in result.stdout
        assert "ATTEMPT_004_WORKFLOW_INVOKED=NO" in result.stdout
        assert "PHASE1EF_PRETRANSFER_LAST_STAGE=PREFLIGHT_ONLY_COMPLETED" in result.stdout
        for name in (
            "git", "openssl", "sha256sum", "stat", "readlink", "gcloud", "qsub"
        ):
            assert not (root / f"{name}.called").exists()
        assert not shadow_marker.exists()
        assert not list(root.rglob("__pycache__"))


def test_phase1ef_dispatcher_rejects_untracked_import_shadow_before_python() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        worktree, private, values, process_environment = _synthetic_authority(root)
        marker = root / "untracked-shadow.called"
        (worktree / "scripts/json.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        environment_path = _write_environment(private, values)
        result = _dispatch(worktree, environment_path, process_environment)
        assert result.returncode != 0
        assert "PREFLIGHT_ONLY_COMPLETED" not in result.stdout
        assert not marker.exists()


def test_phase1ef_dispatcher_rejects_ignored_bytecode_before_python() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        worktree, private, values, process_environment = _synthetic_authority(root)
        ignored = worktree / "scripts/__pycache__/json.cpython-310.pyc"
        ignored.parent.mkdir(mode=0o700)
        ignored.write_bytes(b"synthetic-ignored-bytecode-must-never-be-read")
        check = _run(["/usr/bin/git", "check-ignore", str(ignored)], cwd=worktree)
        assert check.returncode == 0
        environment_path = _write_environment(private, values)
        result = _dispatch(worktree, environment_path, process_environment)
        assert result.returncode != 0
        assert "PREFLIGHT_ONLY_COMPLETED" not in result.stdout
        assert "CANONICAL_AUTHORITY_MANIFEST" not in result.stdout


def test_phase1ef_dispatcher_suppresses_hostile_bash_env_and_git_overrides() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        worktree, private, values, process_environment = _synthetic_authority(root)
        environment_path = _write_environment(private, values)
        bash_env_marker = root / "bash-env.called"
        hostile_bash_env = root / "hostile-bash-env.sh"
        hostile_bash_env.write_text(
            f"touch {bash_env_marker}\nexit 99\n", encoding="utf-8"
        )
        hostile_bash_env.chmod(0o600)
        git_hook_marker = root / "git-config-parameters.called"
        hostile_git_hook = root / "hostile-git-hook.sh"
        hostile_git_hook.write_text(
            f"#!/bin/bash\ntouch {git_hook_marker}\nexit 0\n", encoding="utf-8"
        )
        hostile_git_hook.chmod(0o700)
        hostile_xdg = root / "hostile-xdg"
        (hostile_xdg / "git").mkdir(parents=True, mode=0o700)
        (hostile_xdg / "git/config").write_text(
            "[core]\n"
            f"  fsmonitor = {hostile_git_hook}\n",
            encoding="utf-8",
        )
        fake_git = root / "fake-git"
        fake_git.mkdir(mode=0o700)
        check = _run(["/usr/bin/git", "init", "-q"], cwd=fake_git)
        assert check.returncode == 0
        process_environment.update(
            {
                "BASH_ENV": str(hostile_bash_env),
                "GIT_DIR": str(fake_git / ".git"),
                "GIT_WORK_TREE": str(fake_git),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(root / "hostile-hooks"),
                "GIT_CONFIG_PARAMETERS": (
                    f"'core.fsmonitor={hostile_git_hook}'"
                ),
                "GIT_NAMESPACE": "hostile-namespace",
                "XDG_CONFIG_HOME": str(hostile_xdg),
            }
        )
        config_result = _run(
            ["/usr/bin/git", "config", "core.fsmonitor", str(hostile_git_hook)],
            cwd=worktree,
        )
        assert config_result.returncode == 0
        result = _dispatch(worktree, environment_path, process_environment)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "PHASE1EF_TRACKED_DISPATCHER_PREFLIGHT=PASS_ZERO_SCOPE_NO_ROOTS" in result.stdout
        assert not bash_env_marker.exists()
        assert not git_hook_marker.exists()


def test_phase1ef_dispatcher_rejects_old_injected_or_duplicate_environment_before_roots() -> None:
    cases = ("old", "injection", "duplicate")
    for case in cases:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            worktree, private, values, process_environment = _synthetic_authority(root)
            marker = root / "injection.executed"
            if case == "old":
                values.pop("PHASE1EF_AUTHORITY_MANIFEST")
            environment_path = _write_environment(private, values)
            if case == "injection":
                text = environment_path.read_text(encoding="utf-8")
                text = text.replace(
                    f"EXPECTED_COMMIT={values['EXPECTED_COMMIT']}",
                    f"EXPECTED_COMMIT=$(touch${{IFS}}{marker})",
                )
                environment_path.write_text(text, encoding="utf-8")
                environment_path.chmod(0o600)
            elif case == "duplicate":
                with environment_path.open("a", encoding="utf-8") as stream:
                    stream.write(f"EXPECTED_COMMIT={values['EXPECTED_COMMIT']}\n")
            result = _dispatch(worktree, environment_path, process_environment)
            assert result.returncode != 0
            assert not marker.exists()
            assert "PREFLIGHT_ONLY_COMPLETED" not in result.stdout
            assert not (root / "gcloud.called").exists()
            assert not (root / "qsub.called").exists()


def test_phase1ef_dispatcher_rejects_manifest_attempt_scope_python_and_authority_mutation() -> None:
    mutations = ("manifest_hash", "attempt", "scope", "python", "authority")
    for mutation in mutations:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            worktree, private, values, process_environment = _synthetic_authority(root)
            malicious_marker = root / "malicious-python.executed"
            if mutation == "manifest_hash":
                values["PHASE1EF_AUTHORITY_MANIFEST_SHA256"] = "0" * 64
            elif mutation == "attempt":
                values["ATTEMPT_ID"] = (
                    "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001"
                )
                values["PHASE1EF_ATTEMPT_ID"] = values["ATTEMPT_ID"]
            elif mutation == "scope":
                values["PHASE1EF_EXECUTION_SCOPES_GRANTED"] = "1"
            elif mutation == "python":
                malicious = private / "malicious_python"
                malicious.write_text(
                    f"#!/usr/bin/env bash\ntouch {malicious_marker}\nexit 0\n",
                    encoding="utf-8",
                )
                malicious.chmod(0o700)
                values["PYTHON"] = str(malicious)
                values["PYTHON_AUTHORITY"] = str(malicious)
            else:
                target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
                target.write_bytes(target.read_bytes() + b"# mutation\n")
                target.chmod(0o755)
            environment_path = _write_environment(private, values)
            result = _dispatch(worktree, environment_path, process_environment)
            assert result.returncode != 0
            assert not malicious_marker.exists()
            assert "PREFLIGHT_ONLY_COMPLETED" not in result.stdout
            assert not (root / "gcloud.called").exists()
            assert not (root / "qsub.called").exists()


def test_phase1ef_dispatcher_and_preparer_authority_sets_are_equal() -> None:
    dispatcher_names = set(_environment_names())
    preparer = PREPARER.read_text(encoding="utf-8")
    match = re.search(
        r"for variable in \\\n(?P<body>.*?)\n\s+PRIOR_SAFE_12_EXPECTED_SIZE PRIOR_SAFE_12_EXPECTED_SHA; do",
        preparer,
        flags=re.DOTALL,
    )
    assert match is not None
    preparer_names = {
        token for token in match.group("body").replace("\\", " ").split()
    }
    preparer_names.update(
        {"PRIOR_SAFE_12_EXPECTED_SIZE", "PRIOR_SAFE_12_EXPECTED_SHA"}
    )
    assert preparer_names == dispatcher_names
    assert len(dispatcher_names) == 90


def test_phase1ef_preexecution_path_has_no_shell_environment_evaluation_or_markdown_execution() -> None:
    dispatcher = DISPATCHER.read_text(encoding="utf-8")
    wrapper = WRAPPER.read_text(encoding="utf-8")
    runbook = (
        ROOT / "docs/lvef_multitask/scc_phase1ef_pretransfer_commands.md"
    ).read_text(encoding="utf-8")
    assert 'source "$PHASE1EF_ENV"' not in dispatcher
    assert 'source "$LVEF_ENV"' not in wrapper
    assert "eval " not in dispatcher
    assert dispatcher.startswith("#!/bin/bash -p\n")
    assert "PHASE1EF_PRIVILEGED_BASH_STARTUP=REQUIRED" in dispatcher
    assert "PATH=/usr/bin:/bin" in dispatcher
    assert "/usr/bin/git -C" in dispatcher
    assert "/usr/bin/openssl dgst -sha256 -r" in dispatcher
    assert "status --porcelain=v1 --untracked-files=all" in dispatcher
    assert "--ignored=matching" in dispatcher
    assert "PYTHONDONTWRITEBYTECODE=1" in dispatcher
    assert "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE" in dispatcher
    assert "GIT_CONFIG_PARAMETERS" in dispatcher
    assert "GIT_NAMESPACE GIT_SHALLOW_FILE GIT_QUARANTINE_PATH" in dispatcher
    assert "GIT_CONFIG_GLOBAL=/dev/null" in dispatcher
    assert "GIT_CONFIG_NOSYSTEM=1" in dispatcher
    assert "GIT_CONFIG_KEY_0=core.fsmonitor" in dispatcher
    assert "GIT_CONFIG_VALUE_0=false" in dispatcher
    assert '"$PYTHON" -I "$WORKTREE/scripts/lvef_c3_phase1ef_authority_manifest.py"' in dispatcher
    assert "bash <<'PHASE1EF_STRICT_CHILD'" not in runbook
    assert "awk" not in runbook
    assert dispatcher.index("CANONICAL_AUTHORITY_MANIFEST") < dispatcher.index(
        "ALL_OUTPUT_COLLISION_PREFLIGHT"
    ) < dispatcher.index('mkdir -m 700 -- "$PHASE1EF_ATTEMPT_ROOT"')
    assert dispatcher.index("PREFLIGHT_ONLY_COMPLETED") < dispatcher.index(
        'mkdir -m 700 -- "$PHASE1EF_ATTEMPT_ROOT"'
    )
    for forbidden in ("gcloud ", "gsutil", "qsub ", "objects.list", "alt=media"):
        assert forbidden not in dispatcher


def test_phase1ef_manifest_role_set_matches_all_adjacent_authority_names() -> None:
    assert set(authority.ROLE_SPECS) == {
        "capacity_parser",
        "capacity_wrapper",
        "environment_preparer",
        "phase1ef_runbook",
        "safe_export_policy",
        "backup_recovery_policy",
        "tracked_attempt_dispatcher",
    }
    dispatcher = DISPATCHER.read_text(encoding="utf-8")
    preparer = PREPARER.read_text(encoding="utf-8")
    runbook = (
        ROOT / "docs/lvef_multitask/scc_phase1ef_pretransfer_commands.md"
    ).read_text(encoding="utf-8")
    for role in authority.ROLE_SPECS:
        assert role in MANIFEST_TOOL.read_text(encoding="utf-8")
        assert role in runbook
    for name in (
        "PHASE1EF_ATTEMPT_ID",
        "EXPECTED_COMMIT",
        "PHASE1EF_AUTHORITY_MANIFEST",
        "PHASE1EF_AUTHORITY_MANIFEST_SHA256",
        "PHASE1EF_EXECUTION_SCOPES_GRANTED",
    ):
        assert name in dispatcher and name in preparer


def test_phase1ef_dispatcher_bytes_are_the_tested_production_entrypoint() -> None:
    payload = DISPATCHER.read_bytes()
    assert payload.startswith(b"#!/bin/bash -p\n")
    assert os.access(DISPATCHER, os.X_OK)
    assert hashlib.sha256(payload).hexdigest()
    assert "--preflight-only|--execute" in payload.decode("utf-8")


def test_phase1ef_old_environment_archive_is_byte_identical_closed_and_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        private = root / "private"
        private.mkdir(mode=0o700)
        source = private / "phase1ef_attempt004_authority.env"
        source_payload = b"SYNTHETIC_PRIVATE_VALUE=not-exported\n"
        source.write_bytes(source_payload)
        source.chmod(0o600)
        source_before = source.stat()
        history = private / "history_attempt_001"
        archive, receipt = archive_tool.archive_environment(
            source=source,
            history_root=history,
            governing_commit="8" * 40,
        )
        assert source.read_bytes() == source_payload
        assert archive.read_bytes() == source_payload
        assert source.stat().st_ino == source_before.st_ino
        assert source.stat().st_mode == source_before.st_mode
        assert archive.stat().st_ino != source.stat().st_ino
        assert (history.stat().st_mode & 0o7777) == 0o700
        assert (archive.stat().st_mode & 0o7777) == 0o600
        assert (receipt.stat().st_mode & 0o7777) == 0o600
        value = archive_tool.validate_receipt(
            archive_tool.json.loads(
                receipt.read_text(encoding="utf-8"),
                object_pairs_hook=archive_tool._strict_pairs,
            )
        )
        assert set(value) == archive_tool.RECEIPT_KEYS
        assert value["byte_identity_verified"] is True
        assert value["credential_contents_inspected"] is False
        assert "SYNTHETIC_PRIVATE_VALUE" not in receipt.read_text(encoding="utf-8")
        try:
            archive_tool.archive_environment(
                source=source,
                history_root=history,
                governing_commit="8" * 40,
            )
        except archive_tool.ArchiveError as exc:
            assert str(exc) == "HISTORY_ROOT_COLLISION"
        else:
            raise AssertionError("history root was overwritten")


def test_phase1ef_old_environment_archive_accepts_traversable_nonwritable_parent() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        private = root / "private"
        private.mkdir(mode=0o700)
        source = private / "phase1ef_attempt004_authority.env"
        source.write_text("SYNTHETIC_PRIVATE_VALUE=not-exported\n", encoding="utf-8")
        source.chmod(0o600)
        private.chmod(0o755)
        history = private / "history_attempt_001"
        archive, receipt = archive_tool.archive_environment(
            source=source,
            history_root=history,
            governing_commit="8" * 40,
        )
        assert archive.read_bytes() == source.read_bytes()
        assert history.is_dir() and not history.is_symlink()
        assert history.stat().st_uid == os.geteuid()
        assert (history.stat().st_mode & 0o7777) == 0o700
        assert (archive.stat().st_mode & 0o7777) == 0o600
        assert (receipt.stat().st_mode & 0o7777) == 0o600


def test_phase1ef_history_parent_policy_accepts_scc_setgid_traversal_mode() -> None:
    metadata = mock.Mock(st_uid=os.geteuid(), st_mode=stat.S_IFDIR | 0o2755)
    assert archive_tool._history_parent_policy(metadata)
    for unsafe_mode in (0o2775, 0o2757):
        metadata = mock.Mock(st_uid=os.geteuid(), st_mode=stat.S_IFDIR | unsafe_mode)
        assert not archive_tool._history_parent_policy(metadata)


def test_phase1ef_old_environment_archive_rejects_writable_parent() -> None:
    for unsafe_mode in (0o770, 0o702, 0o775, 0o757):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            private = root / "private"
            private.mkdir(mode=0o700)
            source = private / "phase1ef_attempt004_authority.env"
            source.write_text("SYNTHETIC_PRIVATE_VALUE=not-exported\n", encoding="utf-8")
            source.chmod(0o600)
            private.chmod(unsafe_mode)
            try:
                archive_tool.archive_environment(
                    source=source,
                    history_root=private / "history_attempt_001",
                    governing_commit="8" * 40,
                )
            except archive_tool.ArchiveError as exc:
                assert str(exc) == "HISTORY_PARENT_POLICY_FAILED"
            else:
                raise AssertionError("writable archive parent was accepted")
            assert not (private / "history_attempt_001").exists()


def test_phase1ef_old_environment_archive_rejects_symlink_and_sanitizes_cli() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        private = root / "private"
        private.mkdir(mode=0o700)
        real = private / "real.env"
        real.write_text("SYNTHETIC=1\n", encoding="utf-8")
        real.chmod(0o600)
        link = private / "phase1ef_attempt004_authority.env"
        link.symlink_to(real)
        result = _run(
            [
                sys.executable,
                str(SCRIPTS / "archive_lvef_c3_phase1ef_environment.py"),
                "--source",
                str(link),
                "--history-root",
                str(private / "history"),
                "--governing-commit",
                "8" * 40,
            ]
        )
        assert result.returncode != 0
        assert str(private) not in result.stdout + result.stderr
        assert "SYNTHETIC=1" not in result.stdout + result.stderr
        assert not (private / "history").exists()
