#!/usr/bin/env python3
"""Bound ADC identity and exact-object metadata readiness, without body access.

The token comes only from the production downloader's pinned provider.  Neither
tokens, ADC JSON, account/project text nor source paths enter the result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode

import audit_lvef_c3_gcp_authority as audit
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core

ATTEMPT_ID = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
PLAN_SHA256 = "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
SCIENTIFIC_COMMIT = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
STATUS = "PASS_R7H_ADC_IDENTITY_AND_SOURCE_ACCESS"
ROLES = frozenset({"login", "probe", "array", "finalizer"})
SHA_RE = re.compile(r"[0-9a-f]{64}")


class ADCReadinessError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ADCReadinessError(code)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _approved_bindings() -> tuple[str, str]:
    try:
        session = minimal._project_legacy_session_environment(
            required_names=minimal.LEGACY_SESSION_REQUIRED_NAMES
        ).values
        names = frozenset(audit.EXPECTED_ENV.values())
        value = minimal._parse_literal_environment(
            Path(session["PREFLIGHT_ENV"]), required_names=names
        )
        account = value[audit.EXPECTED_ENV["account"]]
        project = value[audit.EXPECTED_ENV["billing_project"]]
        if not audit.ACCOUNT_RE.fullmatch(account) or not audit.PROJECT_ID_RE.fullmatch(project):
            _fail("ADC_APPROVED_AUTHORITY_INVALID")
        return account, project
    except ADCReadinessError:
        raise
    except Exception:
        raise ADCReadinessError("ADC_APPROVED_AUTHORITY_INVALID") from None


def _adc_quota_project(provider: core.GcloudADCTokenProvider) -> str:
    """Read the renewed credential only in memory under stable private metadata."""
    path = provider.cloudsdk_config / "application_default_credentials.json"
    descriptor = None
    try:
        descriptor, before = core._open_regular_nofollow(path)
        if (before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1 or not 0 < before.st_size <= 65536):
            _fail("ADC_CREDENTIAL_FILE_INVALID")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(65537)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        def identity(item):
            return (item.st_dev, item.st_ino, item.st_mode, item.st_uid,
                    item.st_gid, item.st_nlink, item.st_size,
                    item.st_mtime_ns, item.st_ctime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current)
                or len(payload) != before.st_size):
            _fail("ADC_CREDENTIAL_FILE_CHANGED")
        value = json.loads(payload, object_pairs_hook=core._strict_pairs)
        del payload
        if not isinstance(value, dict) or value.get("type") != "authorized_user":
            _fail("ADC_CREDENTIAL_TYPE_INVALID")
        project = value.get("quota_project_id")
        del value
        if not isinstance(project, str) or not audit.PROJECT_ID_RE.fullmatch(project):
            _fail("ADC_QUOTA_PROJECT_MISMATCH")
        return project
    except ADCReadinessError:
        raise
    except Exception:
        raise ADCReadinessError("ADC_CREDENTIAL_FILE_INVALID") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _scope(run: Any, role: str, job_id: str | None, batch_id: str | None):
    batch_id = batch_id or "c3_batch_016"
    if (role not in ROLES or batch_id not in {"c3_batch_016", "c3_batch_017", "c3_batch_018"}
            or (job_id is not None and re.fullmatch(r"[1-9][0-9]{5,9}", job_id) is None)
            or (role != "login" and job_id is None)
            or run.attempt_id != ATTEMPT_ID or run.plan_sha256 != PLAN_SHA256
            or run.authority.governing_commit != SCIENTIFIC_COMMIT):
        _fail("ADC_SCOPE_INVALID")
    try:
        planned = run.plan["batches"][int(batch_id[-3:])]
        if planned["batch_id"] != batch_id:
            _fail("ADC_SCOPE_INVALID")
        expectation = core.expectation_from_plan_object(planned["objects"][0])
    except ADCReadinessError:
        raise
    except Exception:
        raise ADCReadinessError("ADC_SCOPE_INVALID") from None
    return batch_id, expectation


def validate_adc_access(
    run: Any, *, job_id: str | None = None, role: str = "login",
    batch_id: str | None = None,
) -> dict[str, Any]:
    """One token command, one identity request and one pinned metadata request.

    The gcloud subprocess may internally refresh/cache OAuth state; its network
    call count is not observable here and is deliberately reported as unknown.
    """
    batch_id, expectation = _scope(run, role, job_id, batch_id)
    account, project = _approved_bindings()
    if project != run.authority.billing_project:
        _fail("ADC_APPROVED_PROJECT_MISMATCH")
    provider = core.GcloudADCTokenProvider(
        run.authority.gcloud, cloudsdk_config=run.authority.cloudsdk_config,
        authority_receipt=run.authority.gcloud_receipt,
        authority_receipt_sha256=run.authority.gcloud_receipt_sha256,
    )
    try:
        observed = provider.validate_authority()
        core.validate_gcloud_runtime_authority(observed, expected_runtime_authority=run.runtime_authority)
    except Exception:
        raise ADCReadinessError("ADC_PINNED_PROVIDER_AUTHORITY_INVALID") from None
    if _adc_quota_project(provider) != project:
        _fail("ADC_QUOTA_PROJECT_MISMATCH")
    try:
        token = provider()
    except Exception:
        raise ADCReadinessError("ADC_TOKEN_ACQUISITION_FAILED") from None
    try:
        try:
            observed_account = audit._userinfo_account(token, timeout_seconds=30)
        except audit.ApiHttpError as exc:
            raise ADCReadinessError("ADC_REAUTH_REQUIRED" if exc.status_code == 401 else "ADC_IDENTITY_VALIDATION_FAILED") from None
        except Exception:
            raise ADCReadinessError("ADC_IDENTITY_VALIDATION_FAILED") from None
        if observed_account != account:
            _fail("ADC_IDENTITY_MISMATCH")
        del observed_account
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            + quote(core.GCSExactObjectBodyTransport.bucket, safe="") + "/o/"
            + quote(expectation.source_relative_path, safe="") + "?"
            + urlencode({"fields": "name,size,generation,md5Hash,crc32c",
                         "generation": expectation.generation, "userProject": project})
        )
        try:
            metadata = audit._api_json(token=token, url=url, purpose="R7H_ADC_SOURCE_METADATA", timeout_seconds=30)
        except audit.ApiHttpError as exc:
            code = "ADC_REAUTH_REQUIRED" if exc.status_code == 401 else "ADC_SOURCE_ACCESS_DENIED" if exc.status_code == 403 else "ADC_SOURCE_METADATA_REQUEST_FAILED"
            raise ADCReadinessError(code) from None
        except Exception:
            raise ADCReadinessError("ADC_SOURCE_METADATA_REQUEST_FAILED") from None
        if (set(metadata) != {"name", "size", "generation", "md5Hash", "crc32c"}
                or metadata["name"] != expectation.source_relative_path
                or str(metadata["size"]) != str(expectation.size_bytes)
                or str(metadata["generation"]) != expectation.generation
                or metadata["md5Hash"] != expectation.md5_base64
                or metadata["crc32c"] != expectation.crc32c_base64):
            _fail("ADC_SOURCE_METADATA_INTEGRITY_MISMATCH")
    finally:
        del token
    return {
        "schema_version": 1, "artifact_type": "lvef_c3_r7h_adc_readiness_v1",
        "status": STATUS, "attempt_id": ATTEMPT_ID, "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT, "batch_id": batch_id,
        "role": role, "job_id": job_id,
        "sampled_at_utc": datetime.now(timezone.utc).isoformat(),
        "approved_identity_sha256": _sha(account), "approved_project_sha256": _sha(project),
        "metadata_object_sha256": expectation.source_object_key,
        **observed,
        "adc_identity_matches_expected": True, "adc_quota_project_matches_expected": True,
        "source_metadata_matches_plan": True,
        "token_command_invocations": 1, "oauth_refresh_network_requests": None,
        "userinfo_requests": 1, "source_metadata_requests": 1,
        "dicom_body_requests": 0, "gpu_executions": 0,
        "credential_material_persisted": False,
    }


def validate_adc_receipt(
    value: Mapping[str, Any], *, run: Any, role: str, job_id: str | None,
    batch_id: str | None = None, max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay a closed readiness result; never substitute it for worker refresh."""
    batch_id, expectation = _scope(run, role, job_id, batch_id)
    account, project = _approved_bindings()
    expected = {
        "schema_version": 1, "artifact_type": "lvef_c3_r7h_adc_readiness_v1",
        "status": STATUS, "attempt_id": ATTEMPT_ID, "batch_plan_sha256": PLAN_SHA256,
        "scientific_commit": SCIENTIFIC_COMMIT, "batch_id": batch_id,
        "role": role, "job_id": job_id,
        "approved_identity_sha256": _sha(account), "approved_project_sha256": _sha(project),
        "metadata_object_sha256": expectation.source_object_key,
        "gcloud_resolution_receipt_sha256": run.runtime_authority["gcloud_resolution_receipt_sha256"],
        "gcloud_executable_sha256": run.runtime_authority["gcloud_executable_sha256"],
        "adc_identity_matches_expected": True, "adc_quota_project_matches_expected": True,
        "source_metadata_matches_plan": True, "token_command_invocations": 1,
        "oauth_refresh_network_requests": None, "userinfo_requests": 1,
        "source_metadata_requests": 1, "dicom_body_requests": 0, "gpu_executions": 0,
        "credential_material_persisted": False,
    }
    if (not isinstance(value, Mapping) or set(value) != set(expected) | {"sampled_at_utc"}
            or any(type(value.get(k)) is not type(v) or value.get(k) != v for k, v in expected.items())
            or project != run.authority.billing_project):
        _fail("ADC_READINESS_RECEIPT_INVALID")
    try:
        sampled = datetime.fromisoformat(value["sampled_at_utc"])
        if sampled.utcoffset() != timezone.utc.utcoffset(sampled):
            _fail("ADC_READINESS_RECEIPT_INVALID")
        age = ((now or datetime.now(timezone.utc)) - sampled).total_seconds()
        if isinstance(max_age_seconds, bool) or not 0 <= max_age_seconds <= 86400 or not 0 <= age <= max_age_seconds:
            _fail("ADC_READINESS_STALE")
    except ADCReadinessError:
        raise
    except Exception:
        raise ADCReadinessError("ADC_READINESS_RECEIPT_INVALID") from None
    return dict(value)
