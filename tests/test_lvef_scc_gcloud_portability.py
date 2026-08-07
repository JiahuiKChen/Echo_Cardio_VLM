from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve_lvef_scc_gcloud.sh"
BOOTSTRAP_PREPARER = (
    ROOT / "scripts" / "prepare_lvef_scc_gcloud_cli_bootstrap.sh"
)
RUNBOOK = ROOT / "docs" / "lvef_multitask" / "scc_phase1ebc_commands.md"


def _fake_gcloud(path: Path, version: str = "579.0.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = version ]; then\n"
        f"  printf '%s\\n' '{{\"Google Cloud SDK\": \"{version}\"}}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 91\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _resolver(
    fake: Path,
    record: Path,
    *,
    expected_version: str = "579.0.0",
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment or {})
    env["LVEF_SCC_GCLOUD"] = str(fake)
    env["LVEF_SCC_PYTHON"] = sys.executable
    return subprocess.run(
        [
            str(RESOLVER),
            "--record-json",
            str(record),
            "--expected-version",
            expected_version,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_explicit_gcloud_path_is_validated_and_record_is_credential_free() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake = root / "sdk" / "bin" / "gcloud"
        record = root / "record.json"
        _fake_gcloud(fake)
        completed = _resolver(
            fake,
            record,
            environment={
                "GOOGLE_OAUTH_ACCESS_TOKEN": "synthetic-secret-never-read",
                "GOOGLE_APPLICATION_CREDENTIALS": "/synthetic/credential.json",
            },
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(fake.resolve())
        payload = json.loads(record.read_text(encoding="utf-8"))
        assert payload["status"] == "PASS"
        assert payload["resolution_source"] == "EXPLICIT_ABSOLUTE_PATH"
        assert payload["version"] == "579.0.0"
        assert payload["expected_version"] == "579.0.0"
        assert payload["credential_material_accessed"] is False
        assert len(payload["executable_sha256"]) == 64
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
        serialized = record.read_text(encoding="utf-8")
        for forbidden in (
            "synthetic-secret-never-read",
            "credential.json",
            "account",
            "billingAccount",
            "access_token",
        ):
            assert forbidden not in serialized


def test_symlinked_gcloud_is_canonicalized_before_recording_and_return() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "sdk" / "bin" / "gcloud-real"
        link = root / "bin" / "gcloud"
        record = root / "record.json"
        _fake_gcloud(real)
        link.parent.mkdir(parents=True)
        link.symlink_to(real)
        completed = _resolver(link, record)
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(real.resolve())
        payload = json.loads(record.read_text(encoding="utf-8"))
        assert payload["selected_executable"] == str(real.resolve())


def test_invalid_explicit_path_fails_closed_without_path_fallback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path_gcloud = root / "path-bin" / "gcloud"
        _fake_gcloud(path_gcloud)
        record = root / "record.json"
        env = os.environ.copy()
        env["PATH"] = f"{path_gcloud.parent}:{env.get('PATH', '')}"
        env["LVEF_SCC_GCLOUD"] = str(root / "missing" / "gcloud")
        env["LVEF_SCC_PYTHON"] = sys.executable
        completed = subprocess.run(
            [str(RESOLVER), "--record-json", str(record)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 69
        assert completed.stdout == ""
        assert "explicit_gcloud_path_is_not_executable_no_fallback" in completed.stderr
        assert not record.exists()


def test_version_mismatch_fails_without_creating_authority_record() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake = root / "gcloud"
        record = root / "record.json"
        _fake_gcloud(fake, version="455.0.0")
        completed = _resolver(fake, record)
        assert completed.returncode == 65
        assert completed.stdout == ""
        assert "selected_gcloud_version_mismatch" in completed.stderr
        assert not record.exists()


def test_path_precedes_common_user_install() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path_gcloud = root / "path-bin" / "gcloud"
        home_gcloud = root / "home" / "google-cloud-sdk" / "bin" / "gcloud"
        _fake_gcloud(path_gcloud, version="578.0.0")
        _fake_gcloud(home_gcloud, version="579.0.0")
        record = root / "record.json"
        env = os.environ.copy()
        env.pop("LVEF_SCC_GCLOUD", None)
        env.pop("LVEF_SCC_GCLOUD_EXPECTED_VERSION", None)
        env["HOME"] = str(root / "home")
        env["LVEF_SCC_PYTHON"] = sys.executable
        env["PATH"] = f"{path_gcloud.parent}:/usr/bin:/bin:/usr/sbin:/sbin"
        completed = subprocess.run(
            [str(RESOLVER), "--record-json", str(record)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(path_gcloud.resolve())
        assert json.loads(record.read_text(encoding="utf-8"))[
            "resolution_source"
        ] == "PATH"


def test_common_user_install_is_used_after_empty_path_probe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        home_gcloud = root / "home" / "google-cloud-sdk" / "bin" / "gcloud"
        _fake_gcloud(home_gcloud)
        record = root / "record.json"
        env = os.environ.copy()
        env.pop("LVEF_SCC_GCLOUD", None)
        env["HOME"] = str(root / "home")
        env["LVEF_SCC_PYTHON"] = sys.executable
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        completed = subprocess.run(
            [str(RESOLVER), "--record-json", str(record)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(home_gcloud.resolve())
        assert json.loads(record.read_text(encoding="utf-8"))[
            "resolution_source"
        ] == "COMMON_SELF_CONTAINED_INSTALL"


def test_bootstrap_helper_only_prepares_reviewable_pinned_script() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        output = root / "prepared-bootstrap.sh"
        completed = subprocess.run(
            [str(BOOTSTRAP_PREPARER), "--output-script", str(output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert "PASS_PREPARED_NOT_EXECUTED" in completed.stdout
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        text = output.read_text(encoding="utf-8")
        assert "google-cloud-cli-579.0.0-linux-x86_64.tar.gz" in text
        assert (
            "a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
            in text
        )
        assert "96066973" in text
        assert "f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd" in text
        assert "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/" in text
        assert "sha256sum --check --strict" in text
        assert '"Google Cloud SDK"' in text
        assert "CLOUDSDK_CONFIG" in text
        assert "--proto '=https'" in text
        assert "archive_members.txt" in text
        assert "path-update" not in text
        assert "/restricted/projectnb/mimicecho/tools" in text
        assert "INSTALL_ROOT='/restricted/project/" not in text
        assert "curl" not in completed.stdout
        assert "gcloud auth" not in text


def test_portability_scripts_and_phase1ebc_runbook_parse_as_bash() -> None:
    subprocess.run(["bash", "-n", str(RESOLVER)], check=True)
    subprocess.run(["bash", "-n", str(BOOTSTRAP_PREPARER)], check=True)
    # The prepared script is syntax-checked without executing it.
    with tempfile.TemporaryDirectory() as directory:
        prepared = Path(directory) / "bootstrap.sh"
        subprocess.run(
            [str(BOOTSTRAP_PREPARER), "--output-script", str(prepared)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["bash", "-n", str(prepared)], check=True)

    markdown = RUNBOOK.read_text(encoding="utf-8")
    blocks = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if current is None and line.strip() == "```bash":
            current = []
        elif current is not None and line.strip() == "```":
            blocks.append("\n".join(current) + "\n")
            current = None
        elif current is not None:
            current.append(line)
    assert current is None
    for block in blocks:
        subprocess.run(["bash", "-n"], input=block, text=True, check=True)


def test_existing_run_repair_is_fast_forward_checksum_bound_and_storage_free() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.index("## 3A.")
    end = text.index("## 3B.")
    repair = text[start:end]
    assert "177aac1ce498390f62d43fb76ca216d06dc6b25f" in repair
    assert 'git merge-base --is-ancestor "$PRIOR_EXPECTED_COMMIT"' in repair
    assert (
        'test "$NEW_EXPECTED_COMMIT" = "$(git rev-parse '
        'origin/codex/lvef-multitask-revalidation)"'
    ) in repair
    for checksum in (
        "EXPECTED_RESOURCE_POLICY_SHA256",
        "EXPECTED_SAFE_EXPORT_POLICY_SHA256",
        "EXPECTED_MIGRATION_CLASSIFICATION_SHA256",
        "EXPECTED_MIGRATION_WITNESS_SHA256",
        "EXPECTED_SELECTED_SOURCE_SHA256",
        "EXPECTED_SELECTED_STUDIES_SHA256",
        "EXPECTED_SPLIT_MAP_SHA256",
    ):
        assert checksum in repair
    assert 'repair_expected_commit "$SESSION_ENV"' in repair
    assert 'repair_expected_commit "$PREFLIGHT_ENV"' in repair
    assert "audit_lvef_c3_storage.py" not in repair
    assert "build_lvef_c3_migration_witness.py" not in repair
    assert "RUN_ID=" not in repair
    assert "rm " not in repair and "rm -" not in repair


def test_runbook_keeps_expected_authority_values_restricted_and_gates_qsub() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.index("## 3B.")
    submit = text.index("## 4.")
    portability = text[start:submit]
    for key in (
        "LVEF_C3_EXPECTED_GCP_ACCOUNT",
        "LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME",
        "LVEF_C3_GCP_AUTHORIZED_USER_FILE",
        "LVEF_C3_GCP_BILLING_PROJECT",
    ):
        assert key in text
    assert "read -r -s -p 'Approved active Google identity: '" in portability
    assert "read -r -s -p 'Approved Google Cloud project display name: '" in portability
    assert "gcloud auth list" not in portability
    assert "print-access-token" not in portability
    assert "cat \"$PREFLIGHT_ENV\"" not in text
    assert "gcp_authority_receipt.restricted.json" in text
    assert "gcp_authority_receipt.summary.json" in text
    assert "--expected-version 579.0.0" in portability
    assert "CLOUDSDK_CONFIG" in portability
    assert "auth application-default set-quota-project" in portability
    qsub_position = text.index("qsub \\")
    final_gate_position = text.rfind(
        "scripts/verify_lvef_scc_gcp_authority.sh", 0, qsub_position
    )
    assert final_gate_position != -1
    assert final_gate_position < qsub_position


def test_bootstrap_is_prepared_but_never_executed_by_runbook() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "prepare_lvef_scc_gcloud_cli_bootstrap.sh" in text
    assert "BOOTSTRAP_REVIEW_SCRIPT" in text
    assert 'bash "$BOOTSTRAP_REVIEW_SCRIPT"' not in text
    assert '"$BOOTSTRAP_REVIEW_SCRIPT"' in text
    resolver = RESOLVER.read_text(encoding="utf-8")
    assert "version --format=json" in resolver
    assert "value(core.version)" not in resolver
    assert "CLOUDSDK_CONFIG" in resolver


def test_interactive_auth_login_is_isolated_and_last_in_its_block() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("## 3C.") : text.index("## 3D.")]
    blocks = []
    current: list[str] | None = None
    for line in section.splitlines():
        if current is None and line.strip() == "```bash":
            current = []
        elif current is not None and line.strip() == "```":
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    assert len(blocks) == 1
    executable_lines = [line.strip() for line in blocks[0] if line.strip()]
    assert executable_lines[-1].endswith(
        'auth login "$LVEF_C3_EXPECTED_GCP_ACCOUNT" '
        "--no-launch-browser --update-adc"
    )
    assert "CLOUDSDK_CONFIG=\"$CLOUDSDK_CONFIG\"" in executable_lines[-1]
    assert "qsub" not in section
    assert "verify_lvef_scc_gcp_authority.sh" not in section
