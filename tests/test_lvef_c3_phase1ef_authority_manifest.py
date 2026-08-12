from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no SCC, cloud, scheduler, or scientific data.
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_phase1ef_authority_manifest as authority


COMMIT = "8" * 40
CREATED_UTC = "2026-08-11T20:00:00Z"


def _make_worktree(root: Path) -> Path:
    worktree = root.resolve() / "worktree"
    worktree.mkdir(parents=True)
    for index, (role, (relative, mode)) in enumerate(
        authority.ROLE_SPECS.items(), start=1
    ):
        path = worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"synthetic {index} {role}\n", encoding="utf-8")
        path.chmod(mode)
    return worktree.resolve()


def _private_root(root: Path) -> Path:
    private = root.resolve() / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    return private.resolve()


def _create(root: Path) -> tuple[Path, Path, str]:
    worktree = _make_worktree(root)
    manifest = _private_root(root) / "authority.restricted.json"
    digest = authority.write_manifest_atomic(
        output_path=manifest,
        worktree=worktree,
        attempt_id=authority.AUTHORIZED_ATTEMPT_ID,
        git_branch=authority.AUTHORIZED_BRANCH,
        git_commit=COMMIT,
        historical_base_commit=authority.HISTORICAL_BASE_COMMIT,
        created_utc=CREATED_UTC,
    )
    return worktree, manifest, digest


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_manifest(path: Path, value: object) -> str:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.unlink()
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def _validate(worktree: Path, manifest: Path, digest: str) -> object:
    return authority.load_and_validate_manifest(
        manifest_path=manifest,
        manifest_sha256=digest,
        worktree=worktree,
        expected_attempt_id=authority.AUTHORIZED_ATTEMPT_ID,
        expected_git_branch=authority.AUTHORIZED_BRANCH,
        expected_git_commit=COMMIT,
        expected_historical_base_commit=authority.HISTORICAL_BASE_COMMIT,
    )


def _entry(value: dict[str, Any], role: str) -> dict[str, Any]:
    entries = value["authorities"]
    assert isinstance(entries, list)
    return next(item for item in entries if item["logical_role"] == role)


def _assert_error(code: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except authority.AuthorityManifestError as exc:
        assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError(f"expected AuthorityManifestError {code}")


def test_phase1ef_authority_manifest_closed_zero_scope_contract() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        value = _validate(worktree, manifest, digest)
        assert set(value) == authority.TOP_LEVEL_KEYS
        assert value["schema_name"] == authority.SCHEMA_NAME
        assert value["schema_version"] == authority.SCHEMA_VERSION
        assert value["execution_scopes_granted"] == 0
        assert value["execution_scope_flags"] == authority.EXECUTION_SCOPE_FLAGS
        entries = value["authorities"]
        assert isinstance(entries, list)
        assert len(entries) == len(authority.AUTHORITY_ROLES) == 7
        assert {item["logical_role"] for item in entries} == authority.AUTHORITY_ROLES
        assert all(set(item) == authority.AUTHORITY_KEYS for item in entries)
        assert all(item["symlink_permitted"] is False for item in entries)
        assert (manifest.stat().st_mode & 0o7777) == 0o600
        assert not list(manifest.parent.glob(f".{manifest.name}.tmp.*"))


def test_phase1ef_authority_manifest_excludes_sensitive_fields() -> None:
    with tempfile.TemporaryDirectory() as raw:
        _, manifest, _ = _create(Path(raw))
        text = manifest.read_text(encoding="utf-8").lower()
        for forbidden in (
            "credential",
            "oauth",
            "token",
            "requester",
            "billing_project",
            "patient",
            "subject_id",
            "study_id",
            "dicom_locator",
        ):
            assert forbidden not in text


def test_phase1ef_authority_manifest_rejects_schema_role_and_scope_mutations() -> None:
    cases: list[tuple[Callable[[dict[str, Any]], None], str]] = [
        (lambda value: value.pop("created_utc"), "MANIFEST_SCHEMA_NOT_CLOSED"),
        (lambda value: value.update({"unknown": 1}), "MANIFEST_SCHEMA_NOT_CLOSED"),
        (
            lambda value: value["authorities"].pop(),
            "AUTHORITY_ROLE_COUNT_INVALID",
        ),
        (
            lambda value: value["authorities"].append(
                copy.deepcopy(value["authorities"][0])
            ),
            "AUTHORITY_ROLE_COUNT_INVALID",
        ),
        (
            lambda value: value["authorities"].__setitem__(
                1, copy.deepcopy(value["authorities"][0])
            ),
            "AUTHORITY_ROLE_DUPLICATE",
        ),
        (
            lambda value: value["authorities"][0].update({"unknown": 1}),
            "AUTHORITY_ENTRY_SCHEMA_NOT_CLOSED",
        ),
        (
            lambda value: value.update({"execution_scopes_granted": 1}),
            "EXECUTION_SCOPE_COUNT_NONZERO",
        ),
        (
            lambda value: value.update({"execution_scopes_granted": False}),
            "EXECUTION_SCOPE_COUNT_INVALID",
        ),
        (
            lambda value: value["execution_scope_flags"].update(
                {"cloud_access": True}
            ),
            "EXECUTION_SCOPE_FLAG_ENABLED",
        ),
        (
            lambda value: value["execution_scope_flags"].update(
                {"unknown_scope": False}
            ),
            "EXECUTION_SCOPE_FLAGS_SCHEMA_NOT_CLOSED",
        ),
    ]
    for mutation, expected_code in cases:
        with tempfile.TemporaryDirectory() as raw:
            worktree, manifest, _ = _create(Path(raw))
            value = _load(manifest)
            mutation(value)
            digest = _replace_manifest(manifest, value)
            _assert_error(
                expected_code,
                lambda: _validate(worktree, manifest, digest),
            )


def test_phase1ef_authority_manifest_rejects_duplicate_json_keys() -> None:
    payload = b'{"schema_name":"first","schema_name":"second"}\n'
    _assert_error("JSON_DUPLICATE_KEY", lambda: authority.parse_manifest_bytes(payload))


def test_phase1ef_authority_manifest_rejects_entry_tampering() -> None:
    cases = [
        ("sha256", "0" * 64, "AUTHORITY_SHA256_MISMATCH"),
        ("size_bytes", 1, "AUTHORITY_SIZE_MISMATCH"),
        ("canonical_absolute_path", "relative/file", "AUTHORITY_PATH_MISMATCH"),
        ("canonical_absolute_path", "/absolute/wrong", "AUTHORITY_PATH_MISMATCH"),
        ("required_file_type", "DIRECTORY", "AUTHORITY_FILE_TYPE_POLICY_INVALID"),
        ("required_owner_policy", "ANY_OWNER", "AUTHORITY_OWNER_POLICY_INVALID"),
        ("required_mode_policy", "EXACT_0600", "AUTHORITY_MODE_POLICY_INVALID"),
        ("symlink_permitted", True, "AUTHORITY_SYMLINK_POLICY_INVALID"),
    ]
    for field, replacement, expected_code in cases:
        with tempfile.TemporaryDirectory() as raw:
            worktree, manifest, _ = _create(Path(raw))
            value = _load(manifest)
            _entry(value, "capacity_parser")[field] = replacement
            digest = _replace_manifest(manifest, value)
            _assert_error(
                expected_code,
                lambda: _validate(worktree, manifest, digest),
            )


def test_phase1ef_authority_manifest_rejects_live_content_and_mode_changes() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
        original = target.read_bytes()
        target.write_bytes(b"x" * len(original))
        target.chmod(0o755)
        _assert_error(
            "AUTHORITY_SHA256_MISMATCH",
            lambda: _validate(worktree, manifest, digest),
        )
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
        target.chmod(0o644)
        _assert_error(
            "AUTHORITY_MODE_POLICY_FAILED",
            lambda: _validate(worktree, manifest, digest),
        )
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
        target.chmod(0o775)
        _assert_error(
            "AUTHORITY_MODE_POLICY_FAILED",
            lambda: _validate(worktree, manifest, digest),
        )


def test_phase1ef_authority_manifest_accepts_restrictive_umask_checkout_modes() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        worktree = _make_worktree(root)
        for role, (relative, expected_mode) in authority.ROLE_SPECS.items():
            (worktree / relative).chmod(0o700 if expected_mode & 0o111 else 0o600)
        manifest = _private_root(root) / "authority.restricted.json"
        digest = authority.write_manifest_atomic(
            output_path=manifest,
            worktree=worktree,
            attempt_id=authority.AUTHORIZED_ATTEMPT_ID,
            git_branch=authority.AUTHORIZED_BRANCH,
            git_commit=COMMIT,
            historical_base_commit=authority.HISTORICAL_BASE_COMMIT,
            created_utc=CREATED_UTC,
        )
        _validate(worktree, manifest, digest)


def test_phase1ef_authority_mode_policies_reject_unsafe_boundaries() -> None:
    for mode in (0o500, 0o700, 0o750, 0o755, 0o705):
        assert authority._mode_satisfies_policy(
            mode, authority.MODE_POLICY_EXECUTABLE
        )
    for mode in (0o300, 0o600, 0o775, 0o757, 0o4755, 0o2755, 0o1755):
        assert not authority._mode_satisfies_policy(
            mode, authority.MODE_POLICY_EXECUTABLE
        )
    for mode in (0o400, 0o600, 0o640, 0o644, 0o604):
        assert authority._mode_satisfies_policy(
            mode, authority.MODE_POLICY_NONEXECUTABLE
        )
    for mode in (0o200, 0o755, 0o664, 0o646, 0o4644, 0o2644, 0o1644):
        assert not authority._mode_satisfies_policy(
            mode, authority.MODE_POLICY_NONEXECUTABLE
        )
    assert authority._mode_satisfies_policy(0o600, authority.MODE_POLICY_PRIVATE)
    assert not authority._mode_satisfies_policy(
        0o400, authority.MODE_POLICY_PRIVATE
    )


def test_phase1ef_authority_manifest_rejects_owner_policy_violation() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        with mock.patch.object(authority.os, "geteuid", return_value=os.geteuid() + 1):
            _assert_error(
                "AUTHORITY_OWNER_POLICY_FAILED",
                lambda: _validate(worktree, manifest, digest),
            )


def test_phase1ef_authority_manifest_rejects_symlink_nonregular_and_ancestor_alias() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
        alternate = target.parent / "alternate"
        alternate.write_bytes(target.read_bytes())
        alternate.chmod(0o755)
        target.unlink()
        target.symlink_to(alternate)
        try:
            _validate(worktree, manifest, digest)
        except authority.AuthorityManifestError as exc:
            assert str(exc) in {"PATH_NOT_CANONICAL", "PATH_SYMLINK_FORBIDDEN"}
        else:
            raise AssertionError("symlinked authority accepted")
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        target = worktree / authority.ROLE_SPECS["capacity_parser"][0]
        target.unlink()
        target.mkdir()
        _assert_error(
            "AUTHORITY_NOT_REGULAR_FILE",
            lambda: _validate(worktree, manifest, digest),
        )
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        scripts = worktree / "scripts"
        real_scripts = worktree / "scripts.real"
        scripts.rename(real_scripts)
        scripts.symlink_to(real_scripts, target_is_directory=True)
        _assert_error(
            "PATH_NOT_CANONICAL",
            lambda: _validate(worktree, manifest, digest),
        )


def test_phase1ef_authority_manifest_rejects_worktree_path_alias() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        worktree = _make_worktree(root)
        alias = root / "worktree_alias"
        alias.symlink_to(worktree, target_is_directory=True)
        _assert_error(
            "PATH_NOT_CANONICAL",
            lambda: authority.build_manifest_payload(
                worktree=alias,
                attempt_id=authority.AUTHORIZED_ATTEMPT_ID,
                git_branch=authority.AUTHORIZED_BRANCH,
                git_commit=COMMIT,
                historical_base_commit=authority.HISTORICAL_BASE_COMMIT,
                created_utc=CREATED_UTC,
            ),
        )


def test_phase1ef_authority_manifest_rejects_manifest_hash_and_identity_mismatch() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, _ = _create(Path(raw))
        _assert_error(
            "MANIFEST_SHA256_MISMATCH",
            lambda: _validate(worktree, manifest, "0" * 64),
        )

    cases = [
        ("expected_attempt_id", "attempt_001", "EXPECTED_ATTEMPT_ID_MISMATCH"),
        ("expected_git_branch", "codex/wrong", "EXPECTED_GIT_BRANCH_MISMATCH"),
        ("expected_git_commit", "9" * 40, "EXPECTED_GIT_COMMIT_MISMATCH"),
        (
            "expected_historical_base_commit",
            "7" * 40,
            "EXPECTED_HISTORICAL_BASE_MISMATCH",
        ),
    ]
    for keyword, replacement, expected_code in cases:
        with tempfile.TemporaryDirectory() as raw:
            worktree, manifest, digest = _create(Path(raw))
            arguments: dict[str, Any] = {
                "manifest_path": manifest,
                "manifest_sha256": digest,
                "worktree": worktree,
                "expected_attempt_id": authority.AUTHORIZED_ATTEMPT_ID,
                "expected_git_branch": authority.AUTHORIZED_BRANCH,
                "expected_git_commit": COMMIT,
                "expected_historical_base_commit": authority.HISTORICAL_BASE_COMMIT,
            }
            arguments[keyword] = replacement
            _assert_error(
                expected_code,
                lambda: authority.load_and_validate_manifest(**arguments),
            )

    payload_cases = [
        ("attempt_id", "attempt_001", "ATTEMPT_ID_NOT_AUTHORIZED"),
        ("git_branch", "codex/wrong", "GIT_BRANCH_NOT_AUTHORIZED"),
        ("git_commit", "not-a-commit", "GIT_COMMIT_INVALID"),
        (
            "historical_base_commit",
            "7" * 40,
            "HISTORICAL_BASE_COMMIT_INVALID",
        ),
    ]
    for field, replacement, expected_code in payload_cases:
        with tempfile.TemporaryDirectory() as raw:
            worktree, manifest, _ = _create(Path(raw))
            value = _load(manifest)
            value[field] = replacement
            digest = _replace_manifest(manifest, value)
            _assert_error(
                expected_code,
                lambda: _validate(worktree, manifest, digest),
            )


def test_phase1ef_authority_manifest_requires_owner_private_manifest_mode() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        manifest.chmod(0o644)
        _assert_error(
            "AUTHORITY_MODE_POLICY_FAILED",
            lambda: _validate(worktree, manifest, digest),
        )


def test_phase1ef_authority_manifest_atomic_writer_refuses_clobber() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        original = manifest.read_bytes()
        _assert_error(
            "OUTPUT_ALREADY_EXISTS",
            lambda: authority.write_manifest_atomic(
                output_path=manifest,
                worktree=worktree,
                attempt_id=authority.AUTHORIZED_ATTEMPT_ID,
                git_branch=authority.AUTHORIZED_BRANCH,
                git_commit=COMMIT,
                historical_base_commit=authority.HISTORICAL_BASE_COMMIT,
                created_utc=CREATED_UTC,
            ),
        )
        assert manifest.read_bytes() == original
        assert hashlib.sha256(original).hexdigest() == digest


def test_phase1ef_authority_manifest_cli_output_is_aggregate_safe() -> None:
    with tempfile.TemporaryDirectory() as raw:
        worktree, manifest, digest = _create(Path(raw))
        base_args = [
            "validate",
            "--manifest",
            str(manifest),
            "--worktree",
            str(worktree),
            "--attempt-id",
            authority.AUTHORIZED_ATTEMPT_ID,
            "--git-branch",
            authority.AUTHORIZED_BRANCH,
            "--git-commit",
            COMMIT,
            "--historical-base-commit",
            authority.HISTORICAL_BASE_COMMIT,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            failed = authority.main(
                [*base_args, "--manifest-sha256", "0" * 64]
            )
        assert failed == 65
        assert "PHASE1EF_AUTHORITY_MANIFEST=FAILED" in stderr.getvalue()
        assert "MANIFEST_SHA256_MISMATCH" in stderr.getvalue()
        assert str(raw) not in stdout.getvalue() + stderr.getvalue()

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            passed = authority.main([*base_args, "--manifest-sha256", digest])
        assert passed == 0
        assert stderr.getvalue() == ""
        assert stdout.getvalue() == (
            "PHASE1EF_AUTHORITY_MANIFEST_VALIDATION=PASS\n"
        )
        assert str(raw) not in stdout.getvalue()


def test_phase1ef_authority_manifest_cli_sanitizes_unexpected_oserror() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    private_marker = "/private/do-not-export/control-authority"
    with mock.patch.object(
        authority,
        "load_and_validate_manifest",
        side_effect=OSError(private_marker),
    ):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = authority.main(
                [
                    "validate",
                    "--manifest",
                    "/synthetic/manifest",
                    "--manifest-sha256",
                    "0" * 64,
                    "--worktree",
                    "/synthetic/worktree",
                    "--attempt-id",
                    authority.AUTHORIZED_ATTEMPT_ID,
                    "--git-branch",
                    authority.AUTHORIZED_BRANCH,
                    "--git-commit",
                    COMMIT,
                    "--historical-base-commit",
                    authority.HISTORICAL_BASE_COMMIT,
                ]
            )
    assert status == 70
    assert private_marker not in stdout.getvalue() + stderr.getvalue()
    assert "FILESYSTEM_OR_RUNTIME_FAILURE" in stderr.getvalue()


def test_phase1ef_authority_manifest_argparse_never_reflects_private_argument() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    private_marker = "/private/do-not-export/unknown-argument"
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            authority.main(["--unknown", private_marker])
    except SystemExit as exc:
        assert exc.code == 64
    else:
        raise AssertionError("malformed CLI arguments did not fail")
    assert private_marker not in stdout.getvalue() + stderr.getvalue()
    assert stderr.getvalue() == "PHASE1EF_AUTHORITY_MANIFEST_ARGUMENTS=FAILED\n"
