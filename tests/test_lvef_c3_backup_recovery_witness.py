from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no test contacts a network or uses SCC data.

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_backup_recovery_witness as recovery
import lvef_multitask_analysis_modes as analysis_modes


BRANCH = "codex/lvef-multitask-revalidation"
ATTEMPT = "lvef_multitask_phase1ef_synthetic_attempt_001"


def _run(*arguments: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        list(arguments), cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"Synthetic command failed: {arguments!r}")
    return completed.stdout.strip()


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True)
    path.chmod(0o700)
    return path


def _private_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _synthetic_checkout(root: Path) -> tuple[Path, Path, str]:
    origin = root / "origin.git"
    checkout = root / "checkout"
    _run("git", "init", "--bare", str(origin))
    _run("git", "init", str(checkout))
    _run("git", "config", "user.email", "synthetic@example.invalid", cwd=checkout)
    _run("git", "config", "user.name", "Synthetic Test", cwd=checkout)
    _run("git", "checkout", "-b", BRANCH, cwd=checkout)
    (checkout / "README.md").write_text("synthetic recovery authority\n", encoding="utf-8")
    (checkout / "tool.py").write_text("print('synthetic')\n", encoding="utf-8")
    _run("git", "add", "README.md", "tool.py", cwd=checkout)
    _run("git", "commit", "-m", "synthetic authority", cwd=checkout)
    _run("git", "remote", "add", "origin", str(origin), cwd=checkout)
    _run("git", "push", "-u", "origin", BRANCH, cwd=checkout)
    commit = _run("git", "rev-parse", "HEAD", cwd=checkout)
    (checkout / ".DS_Store").write_bytes(b"untracked synthetic fixture")
    return checkout, origin, commit


def _policy(root: Path, source_root: Path, origin: Path) -> tuple[Path, dict]:
    value = yaml.safe_load(
        (ROOT / "configs/lvef_c3_backup_recovery_policy_v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    value["roots"] = {
        "approved_source_roots": [str(source_root)],
        "approved_backup_root_prefix": str(root / "backed"),
        "approved_restore_root_prefix": str(root / "research"),
        "backup_root_must_be_on_backed_tier": True,
        "restore_root_must_be_isolated_from_live_checkout": True,
    }
    value["git"]["approved_origin_url_regex"] = f"^{re.escape(str(origin))}$"
    value["git"]["recovery_documentation_relative_path"] = "README.md"
    for role_policy in value["artifact_roles"].values():
        role_policy["fixed_expected_sha256"] = None
        role_policy["fixed_expected_size_bytes"] = None
        suffixes = set(role_policy["permitted_suffixes"])
        if role_policy["binary_payload_permitted"]:
            role_policy["schema_kind"] = "echoprime_checkpoint_v1"
        elif ".jsonl" in suffixes:
            role_policy["schema_kind"] = "strict_jsonl_mapping_v1"
        elif suffixes == {".json"}:
            role_policy["schema_kind"] = "strict_json_mapping_v1"
        else:
            role_policy["schema_kind"] = "strict_json_or_csv_v1"
        role_policy["schema_parameters"] = {}
    policy_path = root / "policy.yaml"
    policy_path.write_text(yaml.safe_dump(value, sort_keys=True), encoding="utf-8")
    return policy_path, value


def _artifacts(source_root: Path, policy: dict) -> tuple[list[str], dict[str, Path]]:
    paths: dict[str, Path] = {}
    arguments: list[str] = []
    for role in policy["required_artifact_roles"]:
        role_policy = policy["artifact_roles"][role]
        suffix = str(role_policy["permitted_suffixes"][0])
        path = source_root / f"{role}{suffix}"
        if role == "checkpoint":
            payload = b"synthetic checkpoint bytes\x00\x01"
        elif suffix == ".csv":
            payload = b"synthetic_field\nsynthetic_value\n"
        elif suffix == ".jsonl":
            payload = b'{"synthetic":true}\n'
        else:
            payload = b'{"synthetic":true}\n'
        _private_file(path, payload)
        classification = str(role_policy["classification"])
        paths[role] = path
        digest = recovery.sha256_file(path)
        arguments.append(
            f"{role}={classification}={digest}={path.stat().st_size}="
            f"{role_policy['schema_kind']}={path}"
        )
    return arguments, paths


def _declarations(policy: dict) -> list[str]:
    return [
        f"{role}={classification}"
        for role, classification in policy["required_declarations"].items()
    ]


def _fixture(root: Path) -> dict:
    checkout, origin, commit = _synthetic_checkout(root)
    source = _private_directory(root / "sources")
    _private_directory(root / "backed")
    _private_directory(root / "research")
    aggregate_parent = _private_directory(root / "aggregate")
    policy_path, policy = _policy(root, source, origin)
    artifacts, paths = _artifacts(source, policy)
    return {
        "checkout": checkout,
        "commit": commit,
        "policy_path": policy_path,
        "policy": policy,
        "declarations": _declarations(policy),
        "artifacts": artifacts,
        "paths": paths,
        "backup_root": root / "backed" / "backup_attempt",
        "restore_root": root / "research" / "restore_attempt",
        "aggregate_output": aggregate_parent / "lvef_c3_backup_recovery.summary.json",
    }


def _execute(fixture: dict) -> dict:
    return dict(
        recovery.execute(
            policy_path=fixture["policy_path"],
            attempt_id=ATTEMPT,
            governing_commit=fixture["commit"],
            checkout=fixture["checkout"],
            declarations_raw=fixture["declarations"],
            artifacts_raw=fixture["artifacts"],
            backup_root=fixture["backup_root"],
            restore_root=fixture["restore_root"],
            aggregate_output=fixture["aggregate_output"],
        )
    )


def _refresh_artifact_binding(fixture: dict, role: str) -> None:
    path = fixture["paths"][role]
    refreshed: list[str] = []
    for item in fixture["artifacts"]:
        fields = item.split("=", 5)
        if fields[0] == role:
            fields[2] = recovery.sha256_file(path)
            fields[3] = str(path.stat().st_size)
            fields[5] = str(path)
            item = "=".join(fields)
        refreshed.append(item)
    fixture["artifacts"] = refreshed


def _expect(code: str, function) -> None:
    try:
        function()
    except recovery.BackupRecoveryError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected BackupRecoveryError({code})")


def test_offline_backup_bundle_and_isolated_restore_pass_exactly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        before_head = _run("git", "rev-parse", "HEAD", cwd=fixture["checkout"])
        before_status = _run(
            "git", "status", "--porcelain", "--untracked-files=no",
            cwd=fixture["checkout"],
        )
        aggregate = _execute(fixture)
        recovery.validate_aggregate_output(aggregate)
        policy, _ = analysis_modes.load_policy(
            ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
        )
        safe = analysis_modes.validate_candidate_bytes(
            (json.dumps(aggregate, sort_keys=True) + "\n").encode(),
            filename="lvef_c3_backup_recovery.summary.json",
            profile_name="phase1ef_backup_recovery_json",
            policy=policy,
        )
        assert safe["status"] == "PASS"
        assert aggregate["status"] == recovery.AGGREGATE_STATUS
        assert aggregate["backup_verified"] is True
        assert aggregate["restore_test_passed"] is True
        assert aggregate["linked_worktree_recreated"] is True
        assert aggregate["credential_material_absent"] is True
        assert aggregate["approved_restricted_control_authorities_only"] is True
        assert aggregate["unapproved_bulk_scientific_payload_absent"] is True
        assert aggregate["git_bundle_full_reachable_scan_passed"] is True
        assert aggregate["post_normalization_git_fsck_passed"] is True
        assert aggregate["unresolved_item_count"] == 0
        assert aggregate["classification_counts"] == {
            "CHECKSUM_ONLY_NO_COPY_REQUIRED": 2,
            "COMMITTED_RECONSTRUCTABLE": 1,
            "EXCLUDED_CREDENTIAL_MATERIAL": 2,
            "GIT_ORIGIN_PROTECTED": 1,
            "IRREPLACEABLE_BACKUP_REQUIRED": 23,
            "OWNER_RECREATABLE": 1,
            "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE": 1,
            "UNRESOLVED": 0,
        }
        assert _run("git", "rev-parse", "HEAD", cwd=fixture["checkout"]) == before_head
        assert _run(
            "git", "status", "--porcelain", "--untracked-files=no",
            cwd=fixture["checkout"],
        ) == before_status
        assert (fixture["checkout"] / ".DS_Store").exists()
        manifest = json.loads(
            (fixture["backup_root"] / "backup_manifest.restricted.json").read_text()
        )
        restore = json.loads(
            (fixture["backup_root"] / "restore_receipt.restricted.json").read_text()
        )
        recovery.validate_backup_manifest(manifest)
        recovery.validate_restore_receipt(restore)
        assert fixture["aggregate_output"].stat().st_mode & 0o777 == 0o600


def test_credentials_private_project_values_and_logs_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        path = fixture["paths"]["production_environment_receipt"]
        path.write_text('{"project_id":"private-project"}\n', encoding="utf-8")
        path.chmod(0o600)
        _refresh_artifact_binding(fixture, "production_environment_receipt")
        _expect(
            "CREDENTIAL_OR_PRIVATE_CLOUD_VALUE_DETECTED",
            lambda: _execute(fixture),
        )


def test_reviewed_preflight_aggregate_basename_is_role_scoped() -> None:
    policy, _ = recovery._load_policy(
        ROOT / "configs/lvef_c3_backup_recovery_policy_v1.yaml"
    )
    name_patterns, content_patterns, _ = recovery._compiled_forbidden(policy)
    with tempfile.TemporaryDirectory() as directory:
        path = _private_file(
            Path(directory) / "c3_full_source_preflight.summary.json",
            b'{"synthetic":true}\n',
        )
        recovery._artifact_safety(
            role="prior_safe_aggregate_02",
            source=path,
            role_policy=policy["artifact_roles"]["prior_safe_aggregate_02"],
            policy=policy,
            name_patterns=name_patterns,
            content_patterns=content_patterns,
        )
        _expect(
            "FORBIDDEN_AUTHORITY_BASENAME",
            lambda: recovery._artifact_safety(
                role="production_environment_receipt",
                source=path,
                role_policy=policy["artifact_roles"]["production_environment_receipt"],
                policy=policy,
                name_patterns=name_patterns,
                content_patterns=content_patterns,
            ),
        )

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        path = fixture["paths"]["prior_safe_aggregate_01"]
        renamed = path.with_suffix(".log")
        path.rename(renamed)
        fixture["artifacts"] = [
            item.rsplit("=", 1)[0] + f"={renamed}" if item.startswith("prior_safe_aggregate_01=") else item
            for item in fixture["artifacts"]
        ]
        _expect("AUTHORITY_SUFFIX_NOT_ALLOWLISTED_FOR_ROLE", lambda: _execute(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        path = fixture["paths"]["production_environment_receipt"]
        path.write_text(
            "LVEF_C3_GCP_BILLING_PROJECT=private-live-project\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        _refresh_artifact_binding(fixture, "production_environment_receipt")
        _expect(
            "CREDENTIAL_OR_PRIVATE_CLOUD_VALUE_DETECTED",
            lambda: _execute(fixture),
        )


def test_symlink_special_file_and_source_mode_policy_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        source = fixture["paths"]["checkpoint"]
        target = source.with_name("checkpoint_target.pt")
        source.rename(target)
        source.symlink_to(target)
        _expect("SOURCE_AUTHORITY_NOT_REGULAR_NOFOLLOW", lambda: _execute(fixture))

    if hasattr(os, "mkfifo"):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory).resolve())
            source = fixture["paths"]["checkpoint"]
            source.unlink()
            os.mkfifo(source, 0o600)
            _expect("SOURCE_AUTHORITY_NOT_REGULAR_NOFOLLOW", lambda: _execute(fixture))

    for safe_mode in (0o400, 0o600, 0o640, 0o644):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory).resolve())
            fixture["paths"]["checkpoint"].chmod(safe_mode)
            aggregate = _execute(fixture)
            assert aggregate["backup_verified"] is True

    for unsafe_mode in (0o620, 0o602, 0o666):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory).resolve())
            fixture["paths"]["checkpoint"].chmod(unsafe_mode)
            _expect(
                "SOURCE_AUTHORITY_NOT_OWNER_CONTROLLED_READABLE",
                lambda: _execute(fixture),
            )


def test_role_classification_and_unresolved_items_are_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        fixture["declarations"] = [
            item.replace(
                "tracked_code_and_configuration=COMMITTED_RECONSTRUCTABLE",
                "tracked_code_and_configuration=UNRESOLVED",
            )
            for item in fixture["declarations"]
        ]
        _expect(
            "DECLARATION_SET_OR_CLASSIFICATION_MISMATCH",
            lambda: _execute(fixture),
        )

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        fixture["artifacts"] = fixture["artifacts"][:-1]
        _expect("ARTIFACT_ROLE_SET_NOT_EXACT", lambda: _execute(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        fixture["artifacts"][0] = fixture["artifacts"][0].replace(
            "IRREPLACEABLE_BACKUP_REQUIRED", "CHECKSUM_ONLY_NO_COPY_REQUIRED"
        )
        _expect("ARTIFACT_CLASSIFICATION_MISMATCH", lambda: _execute(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        fixture["artifacts"][0] = fixture["artifacts"][0].replace(
            recovery.sha256_file(fixture["paths"]["checkpoint"]), "0" * 64
        )
        _expect("SOURCE_EXPECTED_AUTHORITY_MISMATCH", lambda: _execute(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        role = "production_environment_receipt"
        path = fixture["paths"][role]
        path.write_text('{"duplicate":1,"duplicate":2}\n', encoding="utf-8")
        path.chmod(0o600)
        _refresh_artifact_binding(fixture, role)
        _expect("JSON_DUPLICATE_KEY", lambda: _execute(fixture))


def test_full_bundle_history_and_historical_paths_are_scanned() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        checkout = fixture["checkout"]
        historical = checkout / "removed_secret.txt"
        historical.write_text(
            "ya" + "29." + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890\n"
        )
        _run("git", "add", historical.name, cwd=checkout)
        _run("git", "commit", "-m", "synthetic historical fixture", cwd=checkout)
        historical.unlink()
        _run("git", "add", "-u", cwd=checkout)
        _run("git", "commit", "-m", "remove historical fixture", cwd=checkout)
        _run("git", "push", "origin", BRANCH, cwd=checkout)
        fixture["commit"] = _run("git", "rev-parse", "HEAD", cwd=checkout)
        _expect(
            "GIT_BUNDLE_CREDENTIAL_OR_PRIVATE_VALUE_DETECTED",
            lambda: _execute(fixture),
        )

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        checkout = fixture["checkout"]
        historical = checkout / "owner_session.env"
        historical.write_text("SYNTHETIC_ONLY=1\n")
        _run("git", "add", historical.name, cwd=checkout)
        _run("git", "commit", "-m", "synthetic historical path fixture", cwd=checkout)
        historical.unlink()
        _run("git", "add", "-u", cwd=checkout)
        _run("git", "commit", "-m", "remove historical path fixture", cwd=checkout)
        _run("git", "push", "origin", BRANCH, cwd=checkout)
        fixture["commit"] = _run("git", "rev-parse", "HEAD", cwd=checkout)
        _expect("GIT_BUNDLE_HISTORICAL_PATH_FORBIDDEN", lambda: _execute(fixture))

    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        checkout = fixture["checkout"]
        historical = checkout / "deleted_patient_fixture.dcm"
        historical.write_bytes(b"SYNTHETIC_DICOM_FIXTURE_ONLY\n")
        _run("git", "add", historical.name, cwd=checkout)
        _run("git", "commit", "-m", "synthetic scientific suffix fixture", cwd=checkout)
        historical.unlink()
        _run("git", "add", "-u", cwd=checkout)
        _run("git", "commit", "-m", "remove scientific suffix fixture", cwd=checkout)
        _run("git", "push", "origin", BRANCH, cwd=checkout)
        fixture["commit"] = _run("git", "rev-parse", "HEAD", cwd=checkout)
        _expect("GIT_BUNDLE_HISTORICAL_PATH_FORBIDDEN", lambda: _execute(fixture))


def test_setgid_private_parents_are_supported() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        fixture["backup_root"].parent.chmod(0o2700)
        fixture["restore_root"].parent.chmod(0o2700)
        aggregate = _execute(fixture)
        assert aggregate["owner_private_permissions_passed"] is True
        assert fixture["backup_root"].stat().st_mode & 0o7777 in {0o700, 0o2700}
        assert fixture["restore_root"].stat().st_mode & 0o7777 in {0o700, 0o2700}


def test_restore_git_commands_ignore_ambient_global_hooks_and_fsmonitor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        checkout, _, _ = _synthetic_checkout(root)
        marker = root / "ambient_git_code_executed"
        probe = root / "probe.sh"
        probe.write_text(
            "#!/usr/bin/env bash\nprintf invoked >\"$1\"\n",
            encoding="utf-8",
        )
        probe.chmod(0o700)
        global_config = root / "hostile-global.gitconfig"
        global_config.write_text(
            "[core]\n"
            f"\tfsmonitor = {probe} {marker}\n"
            "\thooksPath = /definitely/not/approved\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}, clear=False
        ):
            recovery._git(checkout, "status", "--porcelain")
        assert not marker.exists()
        environment = recovery._git_environment()
        assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_VALUE_0"] == "/dev/null"
        assert environment["GIT_CONFIG_VALUE_1"] == "false"


def test_no_clobber_and_restored_manifest_tamper_detection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = _fixture(Path(directory).resolve())
        aggregate = _execute(fixture)
        recovery.validate_aggregate_output(aggregate)
        manifest_path = fixture["backup_root"] / "backup_manifest.restricted.json"
        restore_receipt_path = fixture["backup_root"] / "restore_receipt.restricted.json"
        live = recovery.validate_live_backup_root(
            fixture["backup_root"], attempt_id=ATTEMPT,
            governing_commit=fixture["commit"],
            expected_manifest_binding={
                "size_bytes": manifest_path.stat().st_size,
                "sha256": recovery.sha256_file(manifest_path),
            },
            expected_restore_binding={
                "size_bytes": restore_receipt_path.stat().st_size,
                "sha256": recovery.sha256_file(restore_receipt_path),
            },
        )
        assert live["exact_file_set_verified"] is True
        _expect("BACKUP_ROOT_COLLISION", lambda: _execute(fixture))

        manifest = json.loads(
            (fixture["backup_root"] / "backup_manifest.restricted.json").read_text()
        )
        first = next(row for row in manifest["artifacts"] if row["copied"])
        backed_copy = fixture["backup_root"] / first["backup_relative_path"]
        backed_copy.write_bytes(b"tampered backed primary authority")
        backed_copy.chmod(0o600)
        _expect(
            "LIVE_BACKUP_PRIMARY_ARTIFACT_MISMATCH",
            lambda: recovery.validate_live_backup_root(
                fixture["backup_root"], attempt_id=ATTEMPT,
                governing_commit=fixture["commit"],
                expected_manifest_binding={
                    "size_bytes": manifest_path.stat().st_size,
                    "sha256": recovery.sha256_file(manifest_path),
                },
                expected_restore_binding={
                    "size_bytes": restore_receipt_path.stat().st_size,
                    "sha256": recovery.sha256_file(restore_receipt_path),
                },
            ),
        )
        restored = fixture["restore_root"] / first["backup_relative_path"]
        restored.write_bytes(b"tampered")
        restored.chmod(0o600)
        _, policy = recovery._load_policy(fixture["policy_path"])
        del policy
        policy_value = yaml.safe_load(fixture["policy_path"].read_text())
        _, content_patterns, _ = recovery._compiled_forbidden(policy_value)
        _expect(
            "RESTORE_FILE_MANIFEST_MISMATCH",
            lambda: recovery._verify_manifest_file_set(
                fixture["restore_root"], manifest["artifacts"],
                scan_patterns=content_patterns,
            ),
        )


def test_aggregate_and_restricted_receipt_schemas_reject_unknown_fields() -> None:
    counts = {
        "GIT_ORIGIN_PROTECTED": 1,
        "COMMITTED_RECONSTRUCTABLE": 1,
        "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE": 1,
        "OWNER_RECREATABLE": 1,
        "CHECKSUM_ONLY_NO_COPY_REQUIRED": 2,
        "IRREPLACEABLE_BACKUP_REQUIRED": 23,
        "EXCLUDED_CREDENTIAL_MATERIAL": 2,
        "UNRESOLVED": 0,
    }
    value = {key: False for key in recovery.AGGREGATE_KEYS}
    value.update(
        {
            "schema_version": 1,
            "artifact_type": recovery.AGGREGATE_TYPE,
            "status": recovery.AGGREGATE_STATUS,
            "attempt_id": ATTEMPT,
            "governing_commit": "a" * 40,
            "classification_counts": counts,
            "declared_item_count": sum(counts.values()),
        }
    )
    for key in (
        "policy_sha256", "selection_sha256", "backup_manifest_sha256",
        "restore_receipt_sha256", "recovery_documentation_sha256",
        "git_bundle_sha256", "tracked_file_set_sha256",
        "post_normalization_tracked_file_set_sha256",
    ):
        value[key] = "a" * 64
    for key in (
        "exact_git_commit_restored", "git_fsck_passed", "linked_worktree_recreated",
        "manifest_restore_checksum_equality", "owner_private_permissions_passed",
        "no_symlinks", "no_special_files", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "approved_restricted_control_authorities_only",
        "unapproved_bulk_scientific_payload_absent",
        "git_bundle_full_reachable_scan_passed", "historical_path_scan_passed",
        "post_normalization_git_fsck_passed",
        "live_git_unchanged", "backup_verified", "restore_test_passed",
    ):
        value[key] = True
    for key in (
        "cloud_requests", "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "unresolved_item_count",
    ):
        value[key] = 0
    value["git_bundle_reachable_commit_count"] = 1
    value["git_bundle_historical_path_record_count"] = 1
    value["git_bundle_reachable_unique_blob_count"] = 1
    value["git_bundle_reachable_blob_bytes"] = 1
    value["git_bundle_allowed_synthetic_fixture_match_count"] = 0
    value["object_listing_repeated"] = False
    value["real_dicom_extraction"] = False
    value["echoprime_inference"] = False
    value["model_fitting"] = False
    value["confirmatory_performance_accessed"] = False
    value["full_c3_authorized"] = False
    recovery.validate_aggregate_output(value)
    broken = copy.deepcopy(value)
    broken["unexpected"] = False
    _expect(
        "BACKUP_AGGREGATE_SCHEMA_NOT_CLOSED",
        lambda: recovery.validate_aggregate_output(broken),
    )
