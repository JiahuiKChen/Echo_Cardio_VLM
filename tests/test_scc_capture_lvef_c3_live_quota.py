from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: all paths, principals, quota values, and identities
# in this module are temporary fixtures.

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "scc_capture_lvef_c3_live_quota.sh"
VALIDATOR = ROOT / "scripts" / "capture_lvef_c3_live_quota.py"
BRANCH = "codex/lvef-multitask-revalidation"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _private_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _classification() -> dict[str, object]:
    inventory = 10_954_752_000
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_disaster_tier_path_classification",
        "status": "PASS_COMPLETE_PLANNING_CLASSIFICATION_NOT_EXECUTED",
        "planning_mode": "FULL_MIGRATION_AFTER_BACKUP",
        "source_storage_detail_sha256": "b" * 64,
        "disaster_root": "/synthetic/disaster",
        "disaster_tier_inventory_bytes": inventory,
        "direct_child_count": 1,
        "direct_child_bytes": inventory,
        "root_files_or_overhead_bytes": 0,
        "nested_inventory_row_count": 1,
        "nested_mount_count": 0,
        "symlink_count": 0,
        "symlink_scope_count": 0,
        "blocking_symlink_count": 0,
        "retained_symlink_scope_count": 0,
        "all_symlinks_internal_existing_same_scope": True,
        "symlink_target_content_followed_or_counted": False,
        "complete_classified_direct_child_coverage": True,
        "classified_migration_bytes": inventory,
        "classified_retained_bytes": 0,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "entries": [
            {
                "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
                "migration_status": "NOT_EXECUTED",
            }
        ],
        "root_files_or_overhead": {
            "backup_status": "NOT_VERIFIED_BY_THIS_WITNESS",
            "migration_status": "NOT_EXECUTED",
        },
    }


def _witness(classification_sha256: str) -> dict[str, object]:
    inventory = 10_954_752_000
    return {
        "schema_version": 1,
        "witness_type": "lvef_c3_migration_witness_v1",
        "status": "PASS_CLASSIFIED_MIGRATION_WITNESS",
        "planning_mode": "FULL_MIGRATION_AFTER_BACKUP",
        "classification_complete": True,
        "migration_state": "PLANNED_NOT_EXECUTED",
        "disaster_tier_inventory_bytes": inventory,
        "classified_migration_bytes": inventory,
        "classified_retained_bytes": 0,
        "symlink_count": 0,
        "symlink_scope_count": 0,
        "blocking_symlink_count": 0,
        "retained_symlink_scope_count": 0,
        "nested_mount_count": 0,
        "all_symlinks_internal_existing_same_scope": True,
        "full_migration_path_classification_supported": True,
        "symlink_target_content_followed_or_counted": False,
        "inventory_sha256": "b" * 64,
        "classification_sha256": classification_sha256,
        "backup_verified": False,
        "migration_executed": False,
        "owner_authorization_present": False,
        "full_c3_authorized": False,
    }


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _fixture(
    root: Path,
    *,
    malformed_pquota: bool = False,
    attempt_mode: int = 0o700,
) -> tuple[Path, Path, str]:
    worktree = root / "worktree"
    scripts = worktree / "scripts"
    scripts.mkdir(parents=True)
    (scripts / WRAPPER.name).write_bytes(WRAPPER.read_bytes())
    (scripts / WRAPPER.name).chmod(0o755)
    (scripts / VALIDATOR.name).write_bytes(VALIDATOR.read_bytes())
    (scripts / VALIDATOR.name).chmod(0o755)
    _git("init", "-b", BRANCH, cwd=worktree)
    _git("config", "user.email", "synthetic@example.invalid", cwd=worktree)
    _git("config", "user.name", "Synthetic Test", cwd=worktree)
    _git("add", "scripts", cwd=worktree)
    _git("commit", "-m", "synthetic capture authority", cwd=worktree)
    commit = _git("rev-parse", "HEAD", cwd=worktree)

    evidence = root / "evidence"
    attempt = root / "attempt"
    research = root / "research"
    tools = root / "bin"
    for directory in (evidence, attempt, research, tools):
        directory.mkdir()
        directory.chmod(0o700)
    attempt.chmod(attempt_mode)

    classification_path = evidence / "classification.json"
    witness_path = evidence / "witness.json"
    _private_json(classification_path, _classification())
    _private_json(witness_path, _witness(_sha256(classification_path)))

    pquota_body = "printf '%s\\n' 'malformed quota output'\n"
    if not malformed_pquota:
        pquota_body = (
            "principal=\"$2\"\n"
            "printf '%s\\n' 'Filesystem quota(GB) quota(files) usage(GB) usage(files)'\n"
            "printf '/rproject/%s 11 25000 10.19 20123\\n' \"$principal\"\n"
            "printf '/rprojectnb/%s 989 500000 140.04 335984\\n' \"$principal\"\n"
        )
    _write_executable(tools / "pquota", pquota_body)
    _write_executable(
        tools / "findmnt",
        "target=\"$3\"\n"
        "printf '{\"filesystems\":[{\"source\":\"syntheticfs\",\"target\":\"%s\",\"fstype\":\"gpfs\",\"options\":\"rw,synthetic\"}]}\\n' \"$target\"\n",
    )
    _write_executable(
        tools / "df",
        "target=\"$3\"\n"
        "printf '%s\\n' 'Filesystem 1B-blocks Used Avail Mounted on'\n"
        "printf 'syntheticfs 3000000000000 900000000000 2000000000000 %s\\n' \"$target\"\n",
    )
    _write_executable(
        tools / "du",
        "target=\"$4\"\n"
        "printf '140000000123\\t%s\\n' \"$target\"\n",
    )

    python_path = Path(sys.executable)
    capture_env = evidence / "capture.env"
    assignments = {
        "WORKTREE": str(worktree),
        "EXPECTED_COMMIT": commit,
        "PYTHON": str(python_path),
        "EXPECTED_PYTHON_SHA256": _sha256(python_path),
        "PHASE1ED_ATTEMPT_ROOT": str(attempt),
        "LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT": str(research),
        "LVEF_C3_LIVE_QUOTA_PRINCIPAL": "synthetic_project",
        "MIGRATION_WITNESS": str(witness_path),
        "EXPECTED_MIGRATION_WITNESS_SHA256": _sha256(witness_path),
        "MIGRATION_CLASSIFICATION": str(classification_path),
        "EXPECTED_MIGRATION_CLASSIFICATION_SHA256": _sha256(classification_path),
    }
    capture_env.write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in assignments.items()),
        encoding="utf-8",
    )
    capture_env.chmod(0o600)
    return worktree, capture_env, commit


def _run(worktree: Path, capture_env: Path, root: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = str(root / "bin") + os.pathsep + environment["PATH"]
    return subprocess.run(
        [
            "bash",
            str(worktree / "scripts" / WRAPPER.name),
            "--capture-env",
            str(capture_env),
        ],
        cwd=worktree,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_wrapper_is_strict_and_contains_only_bounded_read_only_commands() -> None:
    source = WRAPPER.read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "lvef_multitask" / "scc_phase1ed_pretransfer_commands.md").read_text(
        encoding="utf-8"
    )
    assert "set -euo pipefail" in source
    assert "umask 077" in source
    assert "700|2700" in source
    assert "700|2700" in runbook
    assert 'stat_mode "$PHASE1ED_ATTEMPT_ROOT")" = \'700\'' not in source
    assert 'stat -c \'%a\' "$PHASE1ED_ATTEMPT_ROOT")" = 700' not in runbook
    for required in (
        '"$PQUOTA_BIN" -u',
        '"$FINDMNT_BIN" --json',
        '"$DF_BIN" -B1',
        '"$DU_BIN" -x -s -B1',
        "--untracked-files=no",
        'stat_mode "$CAPTURE_ENV"',
        "CAPTURE_OUTPUT_COLLISION",
    ):
        assert required in source
    for forbidden in (
        "qsub",
        "qstat",
        "gcloud",
        "gsutil",
        "objects.list",
        "alt=media",
        "rm -",
        "git reset",
        "git checkout --",
    ):
        assert forbidden not in source


def test_valid_989gb_capture_is_operational_pass_but_quota_no_go() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "LIVE_QUOTA_CAPTURE_VALIDATION=PASS" in result.stdout
        assert "MINIMUM_EFFECTIVE_QUOTA_GATE=FAIL" in result.stdout
        assert "PASS_CAPTURED_NO_GO" in result.stdout
        assert "synthetic_project" not in result.stdout
        assert str(root) not in result.stdout
        aggregate = root / "attempt" / "aggregate" / "lvef_c3_live_quota.summary.json"
        payload = json.loads(aggregate.read_text(encoding="utf-8"))
        assert payload["status"] == "FAIL_LIVE_QUOTA_GATE"
        assert payload["quota_bytes"] == 989_000_000_000
        assert payload["exact_project_usage_bytes"] == 140_000_000_123
        assert payload["dicom_body_transfer_authorized"] is False
        receipt = root / "attempt" / "restricted" / "live_quota_capture" / "live_quota_raw_receipt.json"
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
        assert stat.S_IMODE(aggregate.stat().st_mode) == 0o600
        for path in receipt.parent.glob("*.txt"):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_setgid_owner_private_attempt_root_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root, attempt_mode=0o2700)
        for output_directory in (
            root / "attempt" / "restricted",
            root / "attempt" / "aggregate",
        ):
            output_directory.mkdir()
            output_directory.chmod(0o2700)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "LIVE_QUOTA_CAPTURE_VALIDATION=PASS" in result.stdout
        assert "PASS_CAPTURED_NO_GO" in result.stdout


def test_group_readable_attempt_root_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root, attempt_mode=0o750)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 2
        assert "FAILED_ATTEMPT_ROOT_MODE_INVALID" in result.stdout
        assert not (root / "attempt" / "restricted").exists()


def test_group_readable_existing_output_directory_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root)
        restricted = root / "attempt" / "restricted"
        restricted.mkdir()
        restricted.chmod(0o750)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 2
        assert "FAILED_OUTPUT_DIRECTORY_MODE_INVALID" in result.stdout
        assert not (root / "attempt" / "aggregate").exists()


def test_invalid_raw_evidence_fails_and_never_creates_aggregate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root, malformed_pquota=True)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 2
        assert "PHASE1ED_LIVE_QUOTA_CAPTURE=FAILED_RESTRICTED_RECEIPT_BUILD_FAILED" in result.stdout
        assert "synthetic_project" not in result.stdout
        assert not (
            root / "attempt" / "aggregate" / "lvef_c3_live_quota.summary.json"
        ).exists()


def test_no_clobber_and_mode_600_capture_environment_are_enforced() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root)
        first = _run(worktree, capture_env, root)
        assert first.returncode == 0
        receipt = root / "attempt" / "restricted" / "live_quota_capture" / "live_quota_raw_receipt.json"
        before = _sha256(receipt)
        second = _run(worktree, capture_env, root)
        assert second.returncode == 2
        assert "FAILED_CAPTURE_OUTPUT_COLLISION" in second.stdout
        assert _sha256(receipt) == before

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree, capture_env, _ = _fixture(root)
        capture_env.chmod(0o644)
        result = _run(worktree, capture_env, root)
        assert result.returncode == 2
        assert "FAILED_CAPTURE_ENV_MODE_INVALID" in result.stdout
        assert not (root / "attempt" / "restricted").exists()
