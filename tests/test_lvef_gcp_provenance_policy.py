from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_lvef_git_export_safety import scan_staged_git_safety
from lvef_multitask_analysis_modes import SafetyPolicyError, load_policy
from validate_lvef_c3_execution_contract import ContractError, validate_structure


CONTRACT = ROOT / "configs" / "lvef_c3_execution_contract.yaml"
SAFE_EXPORT_POLICY = ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"


def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _assert_contract_mutation_blocked(path: tuple[str, ...], value: object) -> None:
    payload = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    cursor = payload
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = value
    try:
        validate_structure(payload)
    except ContractError:
        return
    raise AssertionError(f"Unsafe Google Cloud contract mutation passed: {path}")


def test_contract_separates_cloud_authorities_and_fails_closed() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    validate_structure(contract)
    cloud = contract["google_cloud_provenance"]
    assert cloud["historical_authority"]["classification"] == "HISTORICAL_AUTHORITY"
    assert (
        cloud["active_prospective_authority"]["classification"]
        == "ACTIVE_PROSPECTIVE_AUTHORITY"
    )
    assert cloud["credential_state"]["classification"] == "CREDENTIAL_STATE"
    assert cloud["active_prospective_authority"]["exact_identifiers_may_be_written_to_git"] is False
    assert (
        cloud["free_trial"]["api_verification_status"]
        == "NOT_API_VERIFIABLE_REQUIRES_OWNER_CONSOLE_OR_BILLING_RECORD"
    )
    assert all(
        contract["preauthorization_gates"][name] is False
        for name in (
            "prospective_gcp_tooling_resolved",
            "prospective_gcp_identity_verified",
            "prospective_gcp_project_verified",
            "prospective_gcp_billing_link_verified",
            "prospective_requester_pays_metadata_access_verified",
            "prospective_bigquery_billing_access_verified",
            "free_trial_status_owner_verified_or_separately_budgeted",
        )
    )


def test_contract_blocks_historical_rewrite_credential_export_and_false_trial_claims() -> None:
    mutations = (
        (
            ("google_cloud_provenance", "historical_authority", "prospective_project_backfill_permitted"),
            True,
        ),
        (
            ("google_cloud_provenance", "active_prospective_authority", "exact_identifiers_may_be_written_to_git"),
            True,
        ),
        (
            ("google_cloud_provenance", "credential_state", "oauth_tokens_may_be_printed"),
            True,
        ),
        (
            ("google_cloud_provenance", "free_trial", "api_verification_status"),
            "PASS_FROM_PROJECT_EXISTENCE",
        ),
        (("requester_pays", "authentication_and_billing_gate_required"), False),
    )
    for path, value in mutations:
        _assert_contract_mutation_blocked(path, value)


def test_gitignore_covers_common_cloud_credential_and_session_files() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        _git(repo, "init", "-q")
        (repo / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        candidates = (
            "application_default_credentials.json",
            ".config/gcloud/credentials.db",
            "private/service-account-key.json",
            "private/requester-pays-session.env",
            "private/lvef_multitask_phase1ebc_preflight.env",
            "private/lvef_c3_preflight.env",
            "private/gcloud_config/credentials.db",
        )
        for relative in candidates:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic\n", encoding="utf-8")
            result = _git(repo, "check-ignore", "-q", "--", relative, check=False)
            assert result.returncode == 0, relative


def test_git_gate_blocks_forced_cloud_session_file_and_high_confidence_token() -> None:
    policy, policy_sha = load_policy(SAFE_EXPORT_POLICY)
    cases = (
        ("requester-pays-session.env", "synthetic nonsecret session state\n"),
        ("lvef_c3_preflight.env", "synthetic nonsecret session state\n"),
        (".config/gcloud/configurations/config_default", "synthetic nonsecret SDK state\n"),
        ("gcloud_config/configurations/config_default", "synthetic nonsecret SDK state\n"),
        ("notes.txt", "token=" + "ya" + "29." + "A" * 32 + "\n"),
        ("notes.txt", "key=" + "AIza" + "B" * 32 + "\n"),
        ("notes.txt", "secret=" + "GOCSPX-" + "C" * 24 + "\n"),
        ("notes.txt", "-----BEGIN " + "PRIVATE KEY-----\nsynthetic\n"),
        ("random.json", '{"refresh_' + 'token":"synthetic-sensitive-state"}\n'),
        ("notes.txt", "billing=" + "ABC123-" + "4D5E6F-" + "7890AB" + "\n"),
    )
    for filename, content in cases:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init", "-q")
            path = repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            _git(repo, "add", "-f", "--", filename)
            try:
                scan_staged_git_safety(
                    repo=repo,
                    policy=policy,
                    policy_sha256=policy_sha,
                )
            except SafetyPolicyError:
                continue
            raise AssertionError(f"Cloud credential artifact passed Git safety gate: {filename}")


def test_git_gate_does_not_treat_digit_free_kebab_option_as_billing_identifier() -> None:
    policy, policy_sha = load_policy(SAFE_EXPORT_POLICY)
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        _git(repo, "init", "-q")
        path = repo / "runbook.md"
        path.write_text(
            "Use --resume-ledger-schema for the frozen schema.\n",
            encoding="utf-8",
        )
        _git(repo, "add", "--", path.name)
        result = scan_staged_git_safety(
            repo=repo,
            policy=policy,
            policy_sha256=policy_sha,
        )
        assert result["status"] == "PASS"
