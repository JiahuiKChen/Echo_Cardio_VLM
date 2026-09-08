from __future__ import annotations

"""Synthetic-only ADC readiness tests: no login, tokens, or network calls."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "scripts", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import lvef_c3_r7h_adc as adc
import test_lvef_c3_orchestration_core as core_tests

ACCOUNT = "synthetic-approved@example.invalid"
PROJECT = "synthetic-private-project"
TOKEN = "synthetic-token-never-printed"


def _raises(code, operation):
    try:
        operation()
    except adc.ADCReadinessError as error:
        assert error.code == code
        assert str(error) == code
        for secret in (ACCOUNT, PROJECT, TOKEN, "private-detail"):
            assert secret not in str(error)
    else:
        raise AssertionError(f"Expected {code}")


@contextlib.contextmanager
def _fixture():
    plan, _ = core_tests._plan()
    object_row = plan["batches"][0]["objects"][0]
    authority = SimpleNamespace(
        governing_commit=adc.SCIENTIFIC_COMMIT, billing_project=PROJECT,
        gcloud=Path("/synthetic/pinned/gcloud"),
        cloudsdk_config=Path("/synthetic/private/config"),
        gcloud_receipt=Path("/synthetic/private/resolver.json"),
        gcloud_receipt_sha256="a" * 64,
    )
    runtime = {"gcloud_resolution_receipt_sha256": "a" * 64, "gcloud_executable_sha256": "b" * 64}
    run = SimpleNamespace(
        attempt_id=adc.ATTEMPT_ID, plan_sha256=adc.PLAN_SHA256,
        authority=authority, runtime_authority=runtime,
        plan={"batches": [{"batch_id": f"c3_batch_{index:03d}", "objects": [object_row]} for index in range(19)]},
    )
    metadata = {
        "name": object_row["source_relative_path"], "size": str(object_row["size_bytes"]),
        "generation": object_row["generation"], "md5Hash": object_row["md5_base64"],
        "crc32c": object_row["crc32c_base64"],
    }
    provider = mock.Mock(return_value=TOKEN)
    provider.validate_authority.return_value = runtime
    with (
        mock.patch.object(adc, "_approved_bindings", return_value=(ACCOUNT, PROJECT)) as bindings,
        mock.patch.object(adc, "_adc_quota_project", return_value=PROJECT) as quota,
        mock.patch.object(adc.core, "GcloudADCTokenProvider", return_value=provider) as constructor,
        mock.patch.object(adc.audit, "_userinfo_account", return_value=ACCOUNT) as userinfo,
        mock.patch.object(adc.audit, "_api_json", return_value=metadata) as metadata_api,
        mock.patch.object(adc.core.GCSExactObjectBodyTransport, "fetch") as body,
        mock.patch("builtins.print") as printer,
    ):
        yield SimpleNamespace(
            run=run, object_row=object_row, metadata=metadata,
            provider=provider, constructor=constructor, bindings=bindings,
            quota=quota, userinfo=userinfo, metadata_api=metadata_api,
            body=body, printer=printer,
        )
        body.assert_not_called()
        printer.assert_not_called()


def test_adc_readiness_uses_pinned_downloader_token_for_identity_and_exact_metadata() -> None:
    with _fixture() as fixture:
        value = adc.validate_adc_access(fixture.run, role="array", job_id="8123400", batch_id="c3_batch_016")
        fixture.constructor.assert_called_once_with(
            fixture.run.authority.gcloud,
            cloudsdk_config=fixture.run.authority.cloudsdk_config,
            authority_receipt=fixture.run.authority.gcloud_receipt,
            authority_receipt_sha256=fixture.run.authority.gcloud_receipt_sha256,
        )
        fixture.provider.validate_authority.assert_called_once_with()
        fixture.provider.assert_called_once_with()
        fixture.userinfo.assert_called_once_with(TOKEN, timeout_seconds=30)
        fixture.metadata_api.assert_called_once()
        kwargs = fixture.metadata_api.call_args.kwargs
        parsed = urlsplit(kwargs["url"])
        assert parsed.scheme == "https" and parsed.hostname == "storage.googleapis.com"
        assert parsed.path.endswith("/o/" + quote(fixture.object_row["source_relative_path"], safe=""))
        assert "/download/" not in parsed.path
        assert parse_qs(parsed.query) == {
            "fields": ["name,size,generation,md5Hash,crc32c"],
            "generation": [fixture.object_row["generation"]], "userProject": [PROJECT],
        }
        assert kwargs["token"] == TOKEN and kwargs["purpose"] == "R7H_ADC_SOURCE_METADATA"
        assert kwargs["timeout_seconds"] == 30
        assert value["status"] == adc.STATUS
        assert value["token_command_invocations"] == 1
        assert value["userinfo_requests"] == value["source_metadata_requests"] == 1
        assert value["oauth_refresh_network_requests"] is None
        assert value["dicom_body_requests"] == value["gpu_executions"] == 0
        assert value["credential_material_persisted"] is False
        encoded = json.dumps(value)
        for secret in (TOKEN, ACCOUNT, PROJECT, fixture.object_row["source_relative_path"], str(fixture.run.authority.cloudsdk_config)):
            assert secret not in encoded


def test_adc_identity_quota_and_token_failures_stop_before_source_request() -> None:
    cases = {
        "quota": "ADC_QUOTA_PROJECT_MISMATCH", "project": "ADC_APPROVED_PROJECT_MISMATCH",
        "provider": "ADC_PINNED_PROVIDER_AUTHORITY_INVALID", "token": "ADC_TOKEN_ACQUISITION_FAILED",
        "identity": "ADC_IDENTITY_MISMATCH", "reauth": "ADC_REAUTH_REQUIRED",
    }
    for mutation, code in cases.items():
        with _fixture() as fixture:
            if mutation == "quota": fixture.quota.return_value = "synthetic-foreign-project"
            elif mutation == "project": fixture.run.authority.billing_project = "synthetic-foreign-project"
            elif mutation == "provider": fixture.provider.validate_authority.return_value = {"gcloud_executable_sha256": "c" * 64}
            elif mutation == "token": fixture.provider.side_effect = RuntimeError(TOKEN + " private-detail")
            elif mutation == "identity": fixture.userinfo.return_value = "synthetic-other@example.invalid"
            else: fixture.userinfo.side_effect = adc.audit.ApiHttpError("SYNTHETIC_USERINFO", 401)
            _raises(code, lambda: adc.validate_adc_access(fixture.run, role="probe", job_id="8123400"))
            fixture.metadata_api.assert_not_called()


def test_adc_metadata_denials_and_integrity_changes_fail_without_body_transfer() -> None:
    for mutation in (401, 403, 500, "name", "size", "generation", "md5Hash", "crc32c", "extra"):
        with _fixture() as fixture:
            if isinstance(mutation, int):
                fixture.metadata_api.side_effect = adc.audit.ApiHttpError("SYNTHETIC_METADATA", mutation)
                expected = {401: "ADC_REAUTH_REQUIRED", 403: "ADC_SOURCE_ACCESS_DENIED", 500: "ADC_SOURCE_METADATA_REQUEST_FAILED"}[mutation]
            else:
                fixture.metadata_api.return_value = {**fixture.metadata, mutation: "changed"}
                expected = "ADC_SOURCE_METADATA_INTEGRITY_MISMATCH"
            _raises(expected, lambda: adc.validate_adc_access(fixture.run, role="probe", job_id="8123400"))
            fixture.provider.assert_called_once_with()


def test_adc_receipt_is_closed_bound_and_fresh_without_refreshing_credentials() -> None:
    with _fixture() as fixture:
        value = adc.validate_adc_access(fixture.run, role="array", job_id="8123400", batch_id="c3_batch_016")
        sampled = datetime.fromisoformat(value["sampled_at_utc"])
        arguments = dict(run=fixture.run, role="array", job_id="8123400", batch_id="c3_batch_016", now=sampled)
        assert adc.validate_adc_receipt(value, **arguments) == value
        for change in (
            {"status": "FAIL"}, {"attempt_id": "foreign"}, {"batch_plan_sha256": "c" * 64},
            {"batch_id": "c3_batch_017"}, {"job_id": "8123499"}, {"role": "login"},
            {"approved_identity_sha256": "c" * 64}, {"approved_project_sha256": "c" * 64},
            {"source_metadata_matches_plan": 1}, {"dicom_body_requests": False},
            {"credential_material_persisted": True}, {"unexpected": TOKEN},
        ):
            _raises("ADC_READINESS_RECEIPT_INVALID", lambda: adc.validate_adc_receipt({**value, **change}, **arguments))
        missing = dict(value)
        del missing["status"]
        _raises("ADC_READINESS_RECEIPT_INVALID", lambda: adc.validate_adc_receipt(missing, **arguments))
        for delta in (-1, 301):
            _raises("ADC_READINESS_STALE", lambda: adc.validate_adc_receipt(value, **{**arguments, "now": sampled + timedelta(seconds=delta)}))
        fixture.provider.assert_called_once_with()
        fixture.userinfo.assert_called_once()
        fixture.metadata_api.assert_called_once()


def test_adc_private_quota_reader_rejects_untrusted_file_shapes() -> None:
    for mutation in ("none", "public", "symlink", "hardlink", "duplicate", "wrong_type"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "application_default_credentials.json"
            value = {"type": "authorized_user", "quota_project_id": PROJECT, "refresh_token": "synthetic-private-detail"}
            path.write_text(json.dumps(value))
            path.chmod(0o600)
            provider = SimpleNamespace(cloudsdk_config=root)
            if mutation == "none":
                assert adc._adc_quota_project(provider) == PROJECT
                continue
            if mutation == "public": path.chmod(0o644)
            elif mutation == "symlink":
                target = root / "synthetic-credential.json"
                path.rename(target)
                path.symlink_to(target)
            elif mutation == "hardlink": os.link(path, root / "duplicate-file")
            elif mutation == "duplicate": path.write_text('{"type":"authorized_user","quota_project_id":"foreign-project","quota_project_id":"' + PROJECT + '"}')
            else: path.write_text(json.dumps({**value, "type": "service_account"}))
            expected = "ADC_CREDENTIAL_TYPE_INVALID" if mutation == "wrong_type" else "ADC_CREDENTIAL_FILE_INVALID"
            _raises(expected, lambda: adc._adc_quota_project(provider))
