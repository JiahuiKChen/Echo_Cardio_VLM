from __future__ import annotations

import io
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_lvef_c3_gcp_authority as authority  # noqa: E402
import preflight_lvef_c3_full_source as preflight  # noqa: E402
import write_lvef_gcp_quota_project_stage as quota_stage_writer  # noqa: E402
from lvef_multitask_analysis_modes import (  # noqa: E402
    SafetyPolicyError,
    load_policy,
    validate_candidate_bytes,
)


EXPECTED_ACCOUNT = "synthetic.user@example.edu"
EXPECTED_PROJECT = "synthetic-project-123"
EXPECTED_DISPLAY = "Synthetic Project"
SYNTHETIC_SUBJECT_TOKEN = "p" + "10000001"
SYNTHETIC_STUDY_TOKEN = "s" + "20000001"
SYNTHETIC_BILLING_ACCOUNT = "ABCDEF-" + "123456-" + "ABCDEF"
SYNTHETIC_RELATIVE = (
    "files/" + "p10/" + SYNTHETIC_SUBJECT_TOKEN + "/" + SYNTHETIC_STUDY_TOKEN + "/a.dcm"
)
ENVIRONMENT = {
    "LVEF_C3_EXPECTED_GCP_ACCOUNT": EXPECTED_ACCOUNT,
    "LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME": EXPECTED_DISPLAY,
    "LVEF_C3_GCP_BILLING_PROJECT": EXPECTED_PROJECT,
}


@contextmanager
def _raises(error_type: type[BaseException], match: str):
    try:
        yield
    except error_type as exc:
        assert match in str(exc)
    else:
        raise AssertionError(f"Expected {error_type.__name__} containing {match!r}")


def _source_manifest(path: Path) -> None:
    path.write_text(
        "source_bucket,source_relative_path\n"
        f"{authority.BUCKET},{SYNTHETIC_RELATIVE}\n",
        encoding="utf-8",
    )


def _credentials(*, adc: bool = False) -> authority.CredentialEvidence:
    return authority.CredentialEvidence(
        access_token=(
            "synthetic-adc-token-never-output" if adc else "synthetic-token-never-output"
        ),
        source_kind=("PINNED_AUTHORIZED_USER_ADC" if adc else "PINNED_GCLOUD_EXECUTABLE"),
        observed_account=EXPECTED_ACCOUNT,
        configured_project=EXPECTED_PROJECT,
        source_sha256=(None if adc else "a" * 64),
        gcloud_executable_pinned=not adc,
        authorized_user_adc_pinned=adc,
    )


def _run_authority(source: Path) -> authority.RunAuthority:
    return authority.RunAuthority(
        expected_commit="1" * 40,
        run_root_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        wrapper_script_sha256="4" * 64,
        execution_contract_sha256="5" * 64,
        source_manifest_sha256=authority.sha256_file(source),
        gcloud_resolver_script_sha256="7" * 64,
        gcloud_resolution_record_sha256="6" * 64,
        gcloud_resolution_record_bound=True,
        quota_project_stage_sha256="8" * 64,
        quota_project_setup_passed=True,
        run_context_bound=True,
    )


def _api_response(*, purpose: str, **_: object) -> dict[str, object]:
    if purpose == "RESOURCE_MANAGER_PROJECT":
        return {
            "name": "projects/123456789",
            "projectId": EXPECTED_PROJECT,
            "displayName": EXPECTED_DISPLAY,
            "state": "ACTIVE",
        }
    if purpose == "CLOUD_BILLING_PROJECT":
        return {
            "projectId": EXPECTED_PROJECT,
            "billingAccountName": "billingAccounts/" + SYNTHETIC_BILLING_ACCOUNT,
            "billingEnabled": True,
        }
    if purpose == "CLOUD_BILLING_ACCOUNT":
        return {"displayName": "Synthetic Billing", "open": True}
    if purpose == "GCS_BUCKET_METADATA":
        return {"name": authority.BUCKET, "billing": {"requesterPays": True}}
    if purpose == "GCS_OBJECT_METADATA_PROBE":
        return {
            "name": SYNTHETIC_RELATIVE,
            "size": "123",
            "generation": "7",
            "storageClass": "STANDARD",
        }
    if purpose.startswith("BIGQUERY_"):
        return {
            "jobReference": {"projectId": EXPECTED_PROJECT},
            "totalBytesProcessed": "0",
            "jobComplete": True,
        }
    raise AssertionError(purpose)


def test_collect_observation_is_identifier_free_and_checks_all_authorities() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "source.csv"
        _source_manifest(manifest)
        calls: list[dict[str, object]] = []

        def api_probe(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return _api_response(**kwargs)

        with mock.patch.dict(os.environ, ENVIRONMENT, clear=True), mock.patch.object(
            authority, "_load_run_authority", return_value=_run_authority(manifest)
        ), mock.patch.object(
            authority,
            "acquire_credentials",
            side_effect=(_credentials(), _credentials(adc=True)),
        ), mock.patch.object(authority, "_api_json", side_effect=api_probe):
            receipt = authority.collect_observation(
                gcloud_bin="/synthetic/pinned/gcloud",
                source_manifest=manifest,
                wrapper_script=manifest,
                execution_contract=manifest,
                timeout_seconds=10,
            )

    assert receipt["status"] == "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY"
    assert receipt["billing_link_active"] is True
    assert receipt["gcs_requester_pays_metadata_access_passed"] is True
    assert receipt["bigquery_job_project_dry_run_passed"] is True
    assert receipt["bigquery_echo_entitlement_dry_run_passed"] is True
    assert receipt["bigquery_mimiciv_entitlement_dry_run_passed"] is True
    assert receipt["free_trial_status"] == "NOT_MACHINE_VERIFIABLE"
    assert receipt["gcs_object_body_requests"] == 0
    assert receipt["bigquery_rows_returned"] == 0
    assert receipt["bigquery_rows_returned_zero"] is True
    assert receipt["cli_identity_matches_expected"] is True
    assert receipt["adc_identity_matches_expected"] is True
    assert receipt["adc_quota_project_matches_expected"] is True
    assert receipt["cli_and_adc_identity_match"] is True
    assert receipt["authorized_user_adc_pinned"] is True
    serialized = json.dumps(receipt, sort_keys=True)
    for forbidden in (
        EXPECTED_ACCOUNT,
        EXPECTED_PROJECT,
        EXPECTED_DISPLAY,
        SYNTHETIC_BILLING_ACCOUNT,
        SYNTHETIC_SUBJECT_TOKEN,
        SYNTHETIC_STUDY_TOKEN,
        "synthetic-token-never-output",
        "synthetic-adc-token-never-output",
    ):
        assert forbidden not in serialized
    purposes = [str(call["purpose"]) for call in calls]
    assert purposes == [
        "RESOURCE_MANAGER_PROJECT",
        "CLOUD_BILLING_PROJECT",
        "CLOUD_BILLING_ACCOUNT",
        "GCS_BUCKET_METADATA",
        "GCS_OBJECT_METADATA_PROBE",
        "BIGQUERY_JOB_PROJECT_DRY_RUN",
        "BIGQUERY_PHYSIONET_ECHO_DRY_RUN",
        "BIGQUERY_PHYSIONET_MIMICIV_DRY_RUN",
    ]
    bigquery_calls = [call for call in calls if str(call["purpose"]).startswith("BIGQUERY_")]
    assert len(bigquery_calls) == 3
    assert all(call["method"] == "POST" for call in bigquery_calls)
    assert all(call["payload"]["dryRun"] is True for call in bigquery_calls)


def test_ambient_credential_overrides_are_rejected() -> None:
    for override in authority.AMBIENT_CREDENTIAL_OVERRIDES:
        environment = dict(ENVIRONMENT)
        environment[override] = "synthetic-ambient-secret"
        with mock.patch.dict(os.environ, environment, clear=True):
            with _raises(authority.AuthorityError, "AMBIENT_CREDENTIAL_OVERRIDE_PROHIBITED"):
                authority.acquire_credentials(
                    gcloud_bin="/nonexistent/gcloud",
                    expected=authority._expected_from_environment(),
                    timeout_seconds=10,
                )


def test_shell_gate_rejects_every_python_ambient_override_and_requires_adc() -> None:
    wrapper = (ROOT / "scripts" / "verify_lvef_scc_gcp_authority.sh").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "scripts" / "scc_run_lvef_c3_resource_preflight.sh").read_text(
        encoding="utf-8"
    )
    for name in authority.AMBIENT_CREDENTIAL_OVERRIDES:
        expected = f'test -z "${{{name}:-}}"'
        assert wrapper.count(expected) >= 2
        assert runner.count(expected) >= 2
        assert wrapper.index(expected, wrapper.index('source "$PREFLIGHT_ENV"')) < wrapper.index(
            ': "${WORKTREE:?}"'
        )
        runner_source = runner.index('source "$BOOTSTRAP_PREFLIGHT_ENV"')
        assert runner.index(expected, runner_source) < runner.index(
            "lvef_c3_quarantine_gcp_authority_environment", runner_source
        )
    assert 'test -n "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"' in wrapper
    assert 'test ! -L "$PREFLIGHT_ENV"' in wrapper
    assert 'test ! -L "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"' in wrapper
    assert 'test ! -L "$BOOTSTRAP_PREFLIGHT_ENV"' in runner
    assert "GCP_QUOTA_PROJECT_STAGE" in wrapper
    assert "EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256" in wrapper


def test_exported_override_in_preflight_env_blocks_wrapper_before_outputs() -> None:
    wrapper = ROOT / "scripts" / "verify_lvef_scc_gcp_authority.sh"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        preflight = root / "preflight.env"
        restricted = root / "restricted.json"
        aggregate = root / "aggregate.json"
        preflight.write_text(
            "export CLOUDSDK_AUTH_ACCESS_TOKEN=synthetic-override-never-output\n",
            encoding="utf-8",
        )
        preflight.chmod(0o600)
        environment = os.environ.copy()
        for name in authority.AMBIENT_CREDENTIAL_OVERRIDES:
            environment.pop(name, None)
        completed = subprocess.run(
            [
                str(wrapper),
                "--preflight-env",
                str(preflight),
                "--restricted-output",
                str(restricted),
                "--aggregate-output",
                str(aggregate),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert not restricted.exists()
        assert not aggregate.exists()
        assert "synthetic-override-never-output" not in completed.stdout
        assert "synthetic-override-never-output" not in completed.stderr


def test_symlinked_preflight_env_blocks_wrapper_before_source_or_outputs() -> None:
    wrapper = ROOT / "scripts" / "verify_lvef_scc_gcp_authority.sh"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real_preflight = root / "real-preflight.env"
        linked_preflight = root / "linked-preflight.env"
        restricted = root / "restricted.json"
        aggregate = root / "aggregate.json"
        real_preflight.write_text(
            "export CLOUDSDK_AUTH_ACCESS_TOKEN=symlink-secret-never-sourced\n",
            encoding="utf-8",
        )
        real_preflight.chmod(0o600)
        linked_preflight.symlink_to(real_preflight)
        environment = os.environ.copy()
        for name in authority.AMBIENT_CREDENTIAL_OVERRIDES:
            environment.pop(name, None)
        completed = subprocess.run(
            [
                str(wrapper),
                "--preflight-env",
                str(linked_preflight),
                "--restricted-output",
                str(restricted),
                "--aggregate-output",
                str(aggregate),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert not restricted.exists()
        assert not aggregate.exists()
        assert "symlink-secret-never-sourced" not in completed.stdout
        assert "symlink-secret-never-sourced" not in completed.stderr


def test_collect_blocks_adc_quota_project_or_identity_drift_before_api_probe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "source.csv"
        _source_manifest(manifest)
        drifted_adc = authority.CredentialEvidence(
            access_token="synthetic-adc-token",
            source_kind="PINNED_AUTHORIZED_USER_ADC",
            observed_account=EXPECTED_ACCOUNT,
            configured_project="wrong-project-123",
            source_sha256=None,
            gcloud_executable_pinned=False,
            authorized_user_adc_pinned=True,
        )
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=True), mock.patch.object(
            authority, "_load_run_authority", return_value=_run_authority(manifest)
        ), mock.patch.object(
            authority, "acquire_credentials", side_effect=(_credentials(), drifted_adc)
        ), mock.patch.object(authority, "_api_json") as api_probe:
            with _raises(
                authority.AuthorityError,
                "CLI_OR_ADC_AUTHORITY_DOES_NOT_MATCH_EXPECTED",
            ):
                authority.collect_observation(
                    gcloud_bin="/synthetic/pinned/gcloud",
                    source_manifest=manifest,
                    wrapper_script=manifest,
                    execution_contract=manifest,
                    timeout_seconds=10,
                )
        api_probe.assert_not_called()


def test_authorized_user_adc_is_explicit_private_and_never_emitted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        credential = Path(directory) / "authorized_user.json"
        credential.write_text(
            json.dumps(
                {
                    "type": "authorized_user",
                    "account": EXPECTED_ACCOUNT,
                    "quota_project_id": EXPECTED_PROJECT,
                    "client_id": "synthetic-client",
                    "client_secret": "synthetic-secret",
                    "refresh_token": "synthetic-refresh",
                }
            ),
            encoding="utf-8",
        )
        credential.chmod(0o600)
        environment = dict(ENVIRONMENT)
        environment[authority.AUTHORIZED_USER_FILE_ENV] = str(credential)
        def oauth_response(*_: object, purpose: str, **__: object) -> dict[str, object]:
            if purpose == "OAUTH_TOKEN_REFRESH":
                return {"access_token": "synthetic-access", "token_type": "Bearer"}
            if purpose == "OAUTH_USERINFO":
                return {"email": EXPECTED_ACCOUNT, "verified_email": True}
            raise AssertionError(purpose)

        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            authority, "_read_json_response", side_effect=oauth_response
        ), mock.patch.object(
            authority,
            "sha256_file",
            side_effect=AssertionError("ADC credential content must not be hashed"),
        ):
            evidence = authority.acquire_credentials(
                gcloud_bin="",
                expected=authority._expected_from_environment(),
                timeout_seconds=10,
            )
        assert evidence.source_kind == "PINNED_AUTHORIZED_USER_ADC"
        assert evidence.observed_account == EXPECTED_ACCOUNT
        assert evidence.configured_project == EXPECTED_PROJECT
        assert evidence.access_token == "synthetic-access"
        assert evidence.source_sha256 is None

        def mismatched_oauth_response(
            *_: object, purpose: str, **__: object
        ) -> dict[str, object]:
            if purpose == "OAUTH_TOKEN_REFRESH":
                return {"access_token": "synthetic-access", "token_type": "Bearer"}
            if purpose == "OAUTH_USERINFO":
                return {"email": "different.user@example.edu", "verified_email": True}
            raise AssertionError(purpose)

        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            authority, "_read_json_response", side_effect=mismatched_oauth_response
        ):
            with _raises(authority.AuthorityError, "ADC_ACCOUNT_DOES_NOT_MATCH_TOKEN"):
                authority.acquire_credentials(
                    gcloud_bin="",
                    expected=authority._expected_from_environment(),
                    timeout_seconds=10,
                )

        credential.chmod(0o644)
        with mock.patch.dict(os.environ, environment, clear=True):
            with _raises(authority.AuthorityError, "PERMISSIONS_NOT_PRIVATE"):
                authority.acquire_credentials(
                    gcloud_bin="",
                    expected=authority._expected_from_environment(),
                    timeout_seconds=10,
                )


def test_pinned_gcloud_does_not_use_path_and_requires_exact_active_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "gcloud"
        executable.write_text(
            "#!/bin/bash\n"
            "case \"$*\" in\n"
            "  *'auth list'*) printf '%s\\n' \"$TEST_EXPECTED_ACCOUNT\" ;;\n"
            "  *'config get-value account'*) printf '%s\\n' \"$TEST_EXPECTED_ACCOUNT\" ;;\n"
            "  *'config get-value project'*) printf '%s\\n' \"$TEST_EXPECTED_PROJECT\" ;;\n"
            "  *'config get-value auth/impersonate_service_account'*) printf '%s\\n' '(unset)' ;;\n"
            "  *'auth print-access-token'*) printf '%s\\n' 'synthetic-token' ;;\n"
            "  *) exit 44 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        environment = dict(ENVIRONMENT)
        environment.update(
            {
                "TEST_EXPECTED_ACCOUNT": EXPECTED_ACCOUNT,
                "TEST_EXPECTED_PROJECT": EXPECTED_PROJECT,
                "PATH": "/definitely/not/a/gcloud/path",
            }
        )
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            authority, "_userinfo_account", return_value=EXPECTED_ACCOUNT
        ):
            evidence = authority.acquire_credentials(
                gcloud_bin=str(executable),
                expected=authority._expected_from_environment(),
                timeout_seconds=10,
            )
    assert evidence.source_kind == "PINNED_GCLOUD_EXECUTABLE"
    assert evidence.observed_account == EXPECTED_ACCOUNT
    assert evidence.configured_project == EXPECTED_PROJECT
    assert evidence.gcloud_executable_pinned is True


def test_receipts_are_private_immutable_and_live_validated_without_identifiers() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.csv"
        gcloud = root / "gcloud"
        resolution = root / "gcloud_resolution.json"
        adc = root / "application_default_credentials.json"
        quota_stage = root / "gcp_adc_quota_project_setup.summary.json"
        restricted = root / "gcp_authority_receipt.restricted.json"
        aggregate = root / "gcp_authority_receipt.summary.json"
        _source_manifest(source)
        gcloud.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gcloud.chmod(0o700)
        resolution.write_text(
            json.dumps(
                {
                    "audit": "lvef_scc_gcloud_resolution",
                    "credential_material_accessed": False,
                    "expected_version": "579.0.0",
                    "executable_sha256": authority.sha256_file(gcloud),
                    "selected_executable": str(gcloud.resolve()),
                    "status": "PASS",
                    "version": "579.0.0",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        resolution.chmod(0o600)
        adc.write_text(
            json.dumps(
                {"type": "authorized_user", "quota_project_id": EXPECTED_PROJECT}
            )
            + "\n",
            encoding="utf-8",
        )
        adc.chmod(0o600)
        quota_stage.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS",
                    "quota_project_setup_command_succeeded": True,
                    "authority_audit_started": False,
                    "identifiers_emitted": False,
                    "tokens_emitted": False,
                    "credential_paths_emitted": False,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        quota_stage.chmod(0o600)
        source_sha = authority.sha256_file(source)
        gcloud_sha = authority.sha256_file(gcloud)
        resolution_sha = authority.sha256_file(resolution)
        quota_stage_sha = authority.sha256_file(quota_stage)
        observation = {
            "schema_version": 1,
            "status": "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY",
            "authority_scope": "ACTIVE_PROSPECTIVE_AUTHORITY_ONLY",
            "observed_at_utc": "2026-08-07T00:00:00+00:00",
            "expected_commit": "1" * 40,
            "run_root_sha256": authority.sha256_text(str(root.resolve())),
            "audit_script_sha256": authority.sha256_file(Path(authority.__file__).resolve()),
            "wrapper_script_sha256": source_sha,
            "execution_contract_sha256": source_sha,
            "source_manifest_sha256": source_sha,
            "gcloud_resolver_script_sha256": source_sha,
            "gcloud_resolution_record_sha256": resolution_sha,
            "gcloud_resolution_record_bound": True,
            "quota_project_stage_sha256": quota_stage_sha,
            "quota_project_setup_passed": True,
            "run_context_bound": True,
            "command_authority_bound": True,
            "expected_account_sha256": authority.sha256_text(EXPECTED_ACCOUNT),
            "observed_account_sha256": authority.sha256_text(EXPECTED_ACCOUNT),
            "expected_project_id_sha256": authority.sha256_text(EXPECTED_PROJECT),
            "observed_project_id_sha256": authority.sha256_text(EXPECTED_PROJECT),
            "project_display_name_sha256": authority.sha256_text(EXPECTED_DISPLAY),
            "observed_project_display_name_sha256": authority.sha256_text(EXPECTED_DISPLAY),
            "requester_pays_project_sha256": authority.sha256_text(EXPECTED_PROJECT),
            "active_identity_matches_expected": True,
            "cli_identity_matches_expected": True,
            "cli_configured_project_matches_expected": True,
            "adc_identity_matches_expected": True,
            "adc_quota_project_matches_expected": True,
            "cli_and_adc_identity_match": True,
            "configured_project_matches_expected": True,
            "project_display_name_matches_expected": True,
            "requester_pays_project_matches_expected": True,
            "project_resource_name_valid": True,
            "project_lifecycle_active": True,
            "billing_project_matches_expected": True,
            "billing_link_active": True,
            "billing_account_link_present": True,
            "billing_account_detail_query": "PASS",
            "billing_account_open": True,
            "free_trial_status": "NOT_MACHINE_VERIFIABLE",
            "free_trial_machine_verifiable": False,
            "credential_source_kind": "PINNED_GCLOUD_EXECUTABLE",
            "credential_source_sha256": gcloud_sha,
            "gcloud_executable_pinned": True,
            "authorized_user_adc_pinned": True,
            "gcs_bucket_matches_expected": True,
            "gcs_requester_pays_metadata_access_passed": True,
            "gcs_single_object_metadata_probe_passed": True,
            "gcs_object_body_requests": 0,
            "gcs_object_body_bytes_read": 0,
            "gcs_object_body_requests_zero": True,
            "gcs_object_body_bytes_read_zero": True,
            "bigquery_job_project_dry_run_passed": True,
            "bigquery_echo_entitlement_dry_run_passed": True,
            "bigquery_mimiciv_entitlement_dry_run_passed": True,
            "bigquery_job_project_bytes_processed": 0,
            "bigquery_echo_bytes_processed": 0,
            "bigquery_mimiciv_bytes_processed": 0,
            "bigquery_rows_returned": 0,
            "bigquery_rows_returned_zero": True,
            "access_token_emitted": False,
            "credential_bytes_emitted": False,
            "account_or_project_identifier_emitted": False,
            "billing_account_identifier_emitted": False,
        }
        args = SimpleNamespace(
            gcloud_bin=str(gcloud),
            source_manifest=source,
            wrapper_script=source,
            execution_contract=source,
            restricted_output=restricted,
            aggregate_output=aggregate,
            timeout_seconds=10,
        )
        receipt_environment = dict(ENVIRONMENT)
        receipt_environment.update(
            {
                "EXPECTED_COMMIT": "1" * 40,
                "RUN_ROOT": str(root),
                "GCP_AUTHORITY_WRAPPER": str(source),
                "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256": source_sha,
                "C3_EXECUTION_CONTRACT": str(source),
                "EXPECTED_C3_EXECUTION_CONTRACT_SHA256": source_sha,
                "EXPECTED_SELECTED_SOURCE_SHA256": source_sha,
                "GCLOUD_RESOLVER": str(source),
                "EXPECTED_GCLOUD_RESOLVER_SHA256": source_sha,
                "GCLOUD": str(gcloud),
                "GCLOUD_RESOLUTION_RECORD": str(resolution),
                "EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256": resolution_sha,
                authority.AUTHORIZED_USER_FILE_ENV: str(adc),
                "GCP_QUOTA_PROJECT_STAGE": str(quota_stage),
                "EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256": quota_stage_sha,
            }
        )
        real_sha256_file = authority.sha256_file

        def hash_noncredential_authority(path: Path) -> str:
            assert Path(path) != adc, "ADC credential content must not be hashed"
            return real_sha256_file(Path(path))

        with mock.patch.dict(os.environ, receipt_environment, clear=True), mock.patch.object(
            authority, "collect_observation", return_value=dict(observation)
        ), mock.patch.object(
            authority, "sha256_file", side_effect=hash_noncredential_authority
        ):
            created = authority.create_receipts(args)
            assert stat.S_IMODE(restricted.stat().st_mode) == 0o600
            assert stat.S_IMODE(aggregate.stat().st_mode) == 0o600
            with _raises(authority.AuthorityError, "OUTPUT_ALREADY_EXISTS"):
                authority.create_receipts(args)
            validated = authority.validate_receipts(args)
            source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with _raises(authority.AuthorityError, "BINDING_MISMATCH"):
                authority.validate_restricted_receipt(
                    restricted,
                    expected_sha256=authority.sha256_file(restricted),
                    billing_project=EXPECTED_PROJECT,
                )
        assert created == validated
        serialized = restricted.read_text() + aggregate.read_text()
        for forbidden in (EXPECTED_ACCOUNT, EXPECTED_PROJECT, EXPECTED_DISPLAY):
            assert forbidden not in serialized
        assert created["schema_version"] == 1
        assert created["status"] == "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY"
        assert all(
            isinstance(value, bool)
            for key, value in created.items()
            if key not in {"schema_version", "status"}
        )
        assert "restricted_receipt_sha256" not in created
        assert "expected_commit" not in created
        assert "authority_scope" not in created
        assert "adc_credential_source_sha256" not in observation
        assert "adc_credential_source_sha256" not in restricted.read_text(encoding="utf-8")


def test_resume_state_is_bound_to_gcp_authority_receipt_checksum() -> None:
    relative = SYNTHETIC_RELATIVE
    payload = {
        "items": [
            {
                "name": relative,
                "size": "123",
                "md5Hash": "YWFhYWFhYWFhYWFhYWFhYQ==",
                "crc32c": "YmJiYg==",
                "generation": "1",
                "storageClass": "STANDARD",
                "updated": "2026-08-07T00:00:00Z",
            }
        ]
    }

    def fake_json_response(
        request: object, *, timeout_seconds: int, purpose: str
    ) -> dict[str, object]:
        del request, timeout_seconds, purpose
        return payload

    with tempfile.TemporaryDirectory() as directory, mock.patch.object(
        preflight, "_read_gcs_json_response", side_effect=fake_json_response
    ):
        root = Path(directory)
        preflight.list_json_api_metadata(
            {relative},
            billing_project=EXPECTED_PROJECT,
            token_env="UNUSED",
            gcloud_bin="",
            page_size=1000,
            restricted_work_dir=root,
            resume=True,
            timeout_seconds=10,
            access_token="synthetic-token",
            authority_hashes={"gcp_authority_receipt_sha256": "a" * 64},
        )
        state = json.loads((root / "c3_gcs_listing_state.restricted.json").read_text())
        assert state["schema_version"] == 5
        with _raises(preflight.PreflightError, "RESUME_STATE_AUTHORITY_MISMATCH"):
            preflight.list_json_api_metadata(
                {relative},
                billing_project=EXPECTED_PROJECT,
                token_env="UNUSED",
                gcloud_bin="",
                page_size=1000,
                restricted_work_dir=root,
                resume=True,
                timeout_seconds=10,
                access_token="synthetic-token",
                authority_hashes={"gcp_authority_receipt_sha256": "b" * 64},
            )


def test_quota_project_stage_writer_classifies_failure_without_identifiers() -> None:
    for command_status, expected_return, expected_status in ((0, 0, "PASS"), (17, 3, "FAIL")):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quota_stage.summary.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = quota_stage_writer.main(
                    [
                        "--command-exit-code",
                        str(command_status),
                        "--output",
                        str(output),
                    ]
                )
            assert result == expected_return
            payload = json.loads(output.read_text(encoding="utf-8"))
            assert payload["status"] == expected_status
            assert payload["quota_project_setup_command_succeeded"] is (
                command_status == 0
            )
            assert payload["authority_audit_started"] is False
            assert stat.S_IMODE(output.stat().st_mode) == 0o600
            emitted = stdout.getvalue() + output.read_text(encoding="utf-8")
            assert EXPECTED_ACCOUNT not in emitted
            assert EXPECTED_PROJECT not in emitted


def test_quota_project_stage_writer_rejects_symlink_and_uses_nofollow() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "existing-target.json"
        target.write_text("preserve-me\n", encoding="utf-8")
        output = root / "quota-stage-link.json"
        output.symlink_to(target)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = quota_stage_writer.main(
                ["--command-exit-code", "0", "--output", str(output)]
            )
        assert result == 2
        assert output.is_symlink()
        assert target.read_text(encoding="utf-8") == "preserve-me\n"
        assert str(output) not in stdout.getvalue()
        assert str(target) not in stdout.getvalue()

        safe_output = root / "quota-stage-safe.json"
        real_open = os.open
        with mock.patch.object(
            quota_stage_writer.os, "open", wraps=real_open
        ) as guarded_open:
            assert quota_stage_writer.main(
                ["--command-exit-code", "0", "--output", str(safe_output)]
            ) == 0
        if hasattr(os, "O_NOFOLLOW"):
            assert guarded_open.call_args.args[1] & os.O_NOFOLLOW


def test_gcp_authority_and_quota_stage_export_profiles_are_exact_and_identifier_free() -> None:
    policy, _ = load_policy(ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml")
    authority_profile = policy["export_profiles"]["gcp_authority_receipt_summary_json"]
    restricted: dict[str, object] = {}
    for key, expected_type in authority_profile["field_types"].items():
        if key == "schema_version":
            continue
        if expected_type == "boolean":
            restricted[key] = key not in {
                "free_trial_machine_verifiable",
                "access_token_emitted",
                "credential_bytes_emitted",
                "account_or_project_identifier_emitted",
                "billing_account_identifier_emitted",
            }
        elif expected_type == "integer":
            restricted[key] = 0
        else:
            restricted[key] = "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY"
    aggregate = authority.aggregate_receipt(restricted)
    assert set(aggregate) == set(authority_profile["required_top_level_keys"])
    aggregate_bytes = (json.dumps(aggregate, sort_keys=True) + "\n").encode("utf-8")
    assert validate_candidate_bytes(
        aggregate_bytes,
        filename="gcp_authority_receipt.summary.json",
        profile_name="gcp_authority_receipt_summary_json",
        policy=policy,
    )["status"] == "PASS"

    stage = {
        "schema_version": 1,
        "status": "PASS",
        "quota_project_setup_command_succeeded": True,
        "authority_audit_started": False,
        "identifiers_emitted": False,
        "tokens_emitted": False,
        "credential_paths_emitted": False,
    }
    stage_bytes = (json.dumps(stage, sort_keys=True) + "\n").encode("utf-8")
    assert validate_candidate_bytes(
        stage_bytes,
        filename="gcp_adc_quota_project_setup.summary.json",
        profile_name="gcp_adc_quota_project_stage_summary_json",
        policy=policy,
    )["status"] == "PASS"

    for payload, profile_name, filename in (
        (
            {**aggregate, "project_id": EXPECTED_PROJECT},
            "gcp_authority_receipt_summary_json",
            "gcp_authority_receipt.summary.json",
        ),
        (
            {**stage, "credential_path": "/synthetic/credential.json"},
            "gcp_adc_quota_project_stage_summary_json",
            "gcp_adc_quota_project_setup.summary.json",
        ),
    ):
        with _raises(SafetyPolicyError, "unapproved top-level keys"):
            validate_candidate_bytes(
                (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
                filename=filename,
                profile_name=profile_name,
                policy=policy,
            )
