#!/usr/bin/env python3
"""Verify prospective GCP identity, billing, GCS, and BigQuery authority.

The gate is intentionally independent of ``bq`` and can authenticate through
either one explicitly resolved Cloud SDK executable or one explicitly named,
owner-only authorized-user ADC file.  Expected account/project values are read
only from the SCC mode-600 environment; none are accepted in argv or emitted.

All network probes are control-plane or metadata-only.  The GCS probe uses
``buckets.get`` and one JSON ``objects.get`` projection (never ``alt=media``),
and the BigQuery probes are dry-run jobs that cannot return rows.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


BUCKET = "mimic-iv-echo-1.0.physionet.org"
EXPECTED_ENV = {
    "account": "LVEF_C3_EXPECTED_GCP_ACCOUNT",
    "project_display_name": "LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME",
    "billing_project": "LVEF_C3_GCP_BILLING_PROJECT",
}
AUTHORIZED_USER_FILE_ENV = "LVEF_C3_GCP_AUTHORIZED_USER_FILE"
AMBIENT_CREDENTIAL_OVERRIDES = (
    "GOOGLE_OAUTH_ACCESS_TOKEN",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_CORE_ACCOUNT",
    "CLOUDSDK_CORE_PROJECT",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
    "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
    "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
    "CLOUDSDK_AUTH_DELEGATES",
)
PINNED_GCLOUD_VERSION = "579.0.0"
PINNED_GCLOUD_ROOT = "/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0"
PINNED_GCLOUD_ARCHIVE_SHA256 = (
    "a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
)
PINNED_GCLOUD_TAR_PAYLOAD_SHA256 = (
    "f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
)
PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
ACCOUNT_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
BILLING_ACCOUNT_RE = re.compile(r"^billingAccounts/[0-9A-Z-]+$")
PROJECT_RESOURCE_RE = re.compile(r"^projects/[0-9]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON_BYTES = 2 * 1024 * 1024
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo?fields=email,verified_email"
ALLOWED_API_HOSTS = {
    "cloudresourcemanager.googleapis.com",
    "cloudbilling.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
}
BIGQUERY_DRY_RUNS = {
    "job_project": "SELECT 1",
    "physionet_echo": (
        "SELECT 1 FROM `physionet-data.mimiciv_echo.echo_study_list` LIMIT 0"
    ),
    "physionet_mimiciv": (
        "SELECT 1 FROM `physionet-data.mimiciv_3_1_hosp.patients` LIMIT 0"
    ),
}


class AuthorityError(RuntimeError):
    """Fail-closed error carrying only a nonsecret static code."""


class ApiHttpError(AuthorityError):
    def __init__(self, purpose: str, status_code: int):
        self.purpose = purpose
        self.status_code = status_code
        super().__init__(f"{purpose}_HTTP_{status_code}")


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def _urlopen_no_redirect(request: Request, *, timeout: int):
    return build_opener(_RejectRedirectHandler()).open(request, timeout=timeout)


@dataclass(frozen=True)
class ExpectedAuthority:
    account: str
    project_id: str
    project_display_name: str
    billing_project: str


@dataclass(frozen=True)
class CredentialEvidence:
    access_token: str
    source_kind: str
    observed_account: str
    configured_project: str
    source_sha256: str
    gcloud_executable_pinned: bool
    authorized_user_adc_pinned: bool


@dataclass(frozen=True)
class RunAuthority:
    expected_commit: str
    run_root_sha256: str
    audit_script_sha256: str
    wrapper_script_sha256: str
    execution_contract_sha256: str
    source_manifest_sha256: str
    gcloud_resolver_script_sha256: str
    gcloud_resolution_record_sha256: str | None
    gcloud_resolution_record_bound: bool
    run_context_bound: bool


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_regular_file(path: Path, *, private: bool) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise AuthorityError("AUTHORITY_FILE_NOT_REGULAR_ABSOLUTE")
    details = path.stat()
    if details.st_uid != os.getuid():
        raise AuthorityError("AUTHORITY_FILE_NOT_OWNED_BY_CURRENT_USER")
    if private and stat.S_IMODE(details.st_mode) & 0o077:
        raise AuthorityError("AUTHORITY_FILE_PERMISSIONS_NOT_PRIVATE")
    return path


def _require_private_output_parent(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise AuthorityError("AUTHORITY_OUTPUT_PATH_INVALID")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise AuthorityError("AUTHORITY_OUTPUT_PARENT_INVALID")
    if parent.stat().st_uid != os.getuid():
        raise AuthorityError("AUTHORITY_OUTPUT_PARENT_NOT_OWNED")


def _expected_from_environment() -> ExpectedAuthority:
    values: dict[str, str] = {}
    for name, environment_name in EXPECTED_ENV.items():
        value = os.environ.get(environment_name, "")
        if not value or value != value.strip() or "\x00" in value:
            raise AuthorityError(f"EXPECTED_{name.upper()}_MISSING_OR_INVALID")
        values[name] = value
    if not ACCOUNT_RE.fullmatch(values["account"]):
        raise AuthorityError("EXPECTED_ACCOUNT_FORMAT_INVALID")
    if not PROJECT_ID_RE.fullmatch(values["billing_project"]):
        raise AuthorityError("EXPECTED_PROJECT_ID_FORMAT_INVALID")
    if values["project_display_name"].strip() != values["project_display_name"]:
        raise AuthorityError("EXPECTED_PROJECT_DISPLAY_NAME_FORMAT_INVALID")
    # The one owner-entered requester-pays value is also the prospective
    # Resource Manager and BigQuery job-project authority.  Do not create a
    # second environment value that could drift from it.
    values["project_id"] = values["billing_project"]
    return ExpectedAuthority(**values)


def _reject_ambient_credential_overrides() -> None:
    if any(os.environ.get(name, "").strip() for name in AMBIENT_CREDENTIAL_OVERRIDES):
        raise AuthorityError("AMBIENT_CREDENTIAL_OVERRIDE_PROHIBITED")


def _clean_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in AMBIENT_CREDENTIAL_OVERRIDES:
        environment.pop(name, None)
    return environment


def _run_gcloud(
    executable: Path,
    arguments: Sequence[str],
    error_code: str,
    *,
    allow_empty: bool = False,
) -> str:
    completed = subprocess.run(
        [str(executable), "--quiet", *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_subprocess_environment(),
        timeout=120,
    )
    if completed.returncode != 0 or (not allow_empty and not completed.stdout.strip()):
        raise AuthorityError(error_code)
    return completed.stdout.strip()


def _resolve_gcloud(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute() or not path.exists() or not os.access(path, os.X_OK):
        raise AuthorityError("PINNED_GCLOUD_EXECUTABLE_INVALID")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise AuthorityError("PINNED_GCLOUD_EXECUTABLE_INVALID")
    return resolved


def _load_run_authority(
    *,
    gcloud_bin: str,
    source_manifest: Path,
    wrapper_script: Path,
    execution_contract: Path,
) -> RunAuthority:
    expected_commit = os.environ.get("EXPECTED_COMMIT", "").strip()
    run_root_text = os.environ.get("RUN_ROOT", "").strip()
    if not COMMIT_RE.fullmatch(expected_commit):
        raise AuthorityError("EXPECTED_COMMIT_MISSING_OR_INVALID")
    run_root = Path(run_root_text)
    if not run_root_text or not run_root.is_absolute() or run_root.is_symlink() or not run_root.is_dir():
        raise AuthorityError("RUN_ROOT_AUTHORITY_INVALID")
    if run_root.stat().st_uid != os.getuid():
        raise AuthorityError("RUN_ROOT_NOT_OWNED_BY_CURRENT_USER")

    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or completed.stdout.strip() != expected_commit:
        raise AuthorityError("GIT_HEAD_DOES_NOT_MATCH_EXPECTED_COMMIT")

    audit_script = _require_regular_file(Path(__file__).resolve(), private=False)
    wrapper_script = _require_regular_file(wrapper_script, private=False)
    execution_contract = _require_regular_file(execution_contract, private=False)
    source_manifest = _require_regular_file(source_manifest, private=False)
    expected_wrapper_path = os.environ.get("GCP_AUTHORITY_WRAPPER", "").strip()
    expected_wrapper_sha = os.environ.get(
        "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256", ""
    ).strip()
    expected_contract_path = os.environ.get("C3_EXECUTION_CONTRACT", "").strip()
    expected_contract_sha = os.environ.get(
        "EXPECTED_C3_EXECUTION_CONTRACT_SHA256", ""
    ).strip()
    expected_source_sha = os.environ.get("EXPECTED_SELECTED_SOURCE_SHA256", "").strip()
    resolver_script_path = os.environ.get("GCLOUD_RESOLVER", "").strip()
    expected_resolver_sha = os.environ.get(
        "EXPECTED_GCLOUD_RESOLVER_SHA256", ""
    ).strip()
    if (
        expected_wrapper_path != str(wrapper_script)
        or not SHA256_RE.fullmatch(expected_wrapper_sha)
        or sha256_file(wrapper_script) != expected_wrapper_sha
    ):
        raise AuthorityError("GCP_AUTHORITY_WRAPPER_BINDING_MISMATCH")
    if (
        expected_contract_path != str(execution_contract)
        or not SHA256_RE.fullmatch(expected_contract_sha)
        or sha256_file(execution_contract) != expected_contract_sha
    ):
        raise AuthorityError("C3_EXECUTION_CONTRACT_BINDING_MISMATCH")
    if (
        not SHA256_RE.fullmatch(expected_source_sha)
        or sha256_file(source_manifest) != expected_source_sha
    ):
        raise AuthorityError("SOURCE_MANIFEST_BINDING_MISMATCH")
    resolver_script = _require_regular_file(Path(resolver_script_path), private=False)
    if (
        not SHA256_RE.fullmatch(expected_resolver_sha)
        or sha256_file(resolver_script) != expected_resolver_sha
    ):
        raise AuthorityError("GCLOUD_RESOLVER_SCRIPT_BINDING_MISMATCH")

    resolution_sha256: str | None = None
    resolution_bound = False
    if gcloud_bin:
        record_text = os.environ.get("GCLOUD_RESOLUTION_RECORD", "").strip()
        expected_record_sha = os.environ.get(
            "EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256", ""
        ).strip()
        if not record_text or not SHA256_RE.fullmatch(expected_record_sha):
            raise AuthorityError("GCLOUD_RESOLUTION_RECORD_AUTHORITY_MISSING")
        record_path = _require_regular_file(Path(record_text), private=True)
        resolution_sha256 = sha256_file(record_path)
        if resolution_sha256 != expected_record_sha:
            raise AuthorityError("GCLOUD_RESOLUTION_RECORD_SHA256_MISMATCH")
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AuthorityError("GCLOUD_RESOLUTION_RECORD_JSON_INVALID") from None
        resolved_gcloud = _resolve_gcloud(gcloud_bin)
        is_pinned_common_install = str(resolved_gcloud).startswith(
            PINNED_GCLOUD_ROOT + "/"
        )
        if (
            not isinstance(record, Mapping)
            or record.get("audit") != "lvef_scc_gcloud_resolution"
            or record.get("status") != "PASS"
            or record.get("credential_material_accessed") is not False
            or record.get("expected_version") != PINNED_GCLOUD_VERSION
            or record.get("version") != PINNED_GCLOUD_VERSION
            or record.get("selected_executable") != str(resolved_gcloud)
            or record.get("executable_sha256") != sha256_file(resolved_gcloud)
            or (
                is_pinned_common_install
                and record.get("retained_archive_sha256")
                != PINNED_GCLOUD_ARCHIVE_SHA256
            )
            or (
                is_pinned_common_install
                and record.get("retained_tar_payload_sha256")
                != PINNED_GCLOUD_TAR_PAYLOAD_SHA256
            )
        ):
            raise AuthorityError("GCLOUD_RESOLUTION_RECORD_NOT_AUTHORITATIVE")
        resolution_bound = True

    return RunAuthority(
        expected_commit=expected_commit,
        run_root_sha256=sha256_text(str(run_root.resolve(strict=True))),
        audit_script_sha256=sha256_file(audit_script),
        wrapper_script_sha256=sha256_file(wrapper_script),
        execution_contract_sha256=sha256_file(execution_contract),
        source_manifest_sha256=sha256_file(source_manifest),
        gcloud_resolver_script_sha256=sha256_file(resolver_script),
        gcloud_resolution_record_sha256=resolution_sha256,
        gcloud_resolution_record_bound=resolution_bound,
        run_context_bound=True,
    )


def _refresh_authorized_user_token(path: Path) -> tuple[str, str, str, str]:
    path = _require_regular_file(path, private=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AuthorityError("AUTHORIZED_USER_ADC_JSON_INVALID") from None
    if not isinstance(payload, Mapping) or payload.get("type") != "authorized_user":
        raise AuthorityError("AUTHORIZED_USER_ADC_TYPE_INVALID")
    token_uri = str(payload.get("token_uri") or TOKEN_ENDPOINT)
    if token_uri != TOKEN_ENDPOINT:
        raise AuthorityError("AUTHORIZED_USER_TOKEN_ENDPOINT_NOT_PINNED")
    required = {
        name: str(payload.get(name, "")).strip()
        for name in ("client_id", "client_secret", "refresh_token")
    }
    if not all(required.values()):
        raise AuthorityError("AUTHORIZED_USER_ADC_FIELDS_MISSING")
    body = urlencode(
        {
            "client_id": required["client_id"],
            "client_secret": required["client_secret"],
            "refresh_token": required["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token_payload = _read_json_response(request, purpose="OAUTH_TOKEN_REFRESH", bearer=False)
    access_token = str(token_payload.get("access_token", "")).strip()
    token_type = str(token_payload.get("token_type", "")).strip().lower()
    if not access_token or token_type != "bearer":
        raise AuthorityError("AUTHORIZED_USER_TOKEN_REFRESH_INVALID")
    account = str(payload.get("account", "")).strip()
    quota_project = str(payload.get("quota_project_id", "")).strip()
    return access_token, account, quota_project, sha256_file(path)


def _read_json_response(
    request: Request,
    *,
    purpose: str,
    bearer: bool = True,
    timeout_seconds: int = 120,
) -> Mapping[str, Any]:
    parsed = urlparse(request.full_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_API_HOSTS:
        raise AuthorityError(f"{purpose}_URL_NOT_PINNED")
    if "alt=media" in request.full_url or "/download/storage/" in request.full_url:
        raise AuthorityError("MEDIA_ENDPOINT_PROHIBITED")
    if bearer and not str(request.headers.get("Authorization", "")).startswith("Bearer "):
        raise AuthorityError(f"{purpose}_BEARER_HEADER_MISSING")
    try:
        with _urlopen_no_redirect(request, timeout=timeout_seconds) as response:
            final = urlparse(response.geturl())
            if (
                final.scheme != "https"
                or final.hostname not in ALLOWED_API_HOSTS
                or response.geturl() != request.full_url
            ):
                raise AuthorityError(f"{purpose}_REDIRECT_NOT_PINNED")
            headers = getattr(response, "headers", None)
            content_type = (
                headers.get_content_type()
                if headers is not None and hasattr(headers, "get_content_type")
                else ""
            )
            if content_type != "application/json":
                raise AuthorityError(f"{purpose}_CONTENT_TYPE_INVALID")
            body = response.read(MAX_JSON_BYTES + 1)
    except HTTPError as exc:
        raise ApiHttpError(purpose, exc.code) from None
    except (URLError, TimeoutError):
        raise AuthorityError(f"{purpose}_NETWORK_FAILURE") from None
    if len(body) > MAX_JSON_BYTES:
        raise AuthorityError(f"{purpose}_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise AuthorityError(f"{purpose}_JSON_INVALID") from None
    if not isinstance(payload, Mapping):
        raise AuthorityError(f"{purpose}_JSON_NOT_OBJECT")
    return payload


def _api_json(
    *,
    token: str,
    url: str,
    purpose: str,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    if not token:
        raise AuthorityError("ACCESS_TOKEN_MISSING")
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return _read_json_response(
        Request(url, data=data, headers=headers, method=method),
        purpose=purpose,
        timeout_seconds=timeout_seconds,
    )


def _userinfo_account(token: str, *, timeout_seconds: int) -> str:
    payload = _api_json(
        token=token,
        url=USERINFO_ENDPOINT,
        purpose="OAUTH_USERINFO",
        timeout_seconds=timeout_seconds,
    )
    account = str(payload.get("email", "")).strip()
    if not account or payload.get("verified_email") is not True:
        raise AuthorityError("AUTHORIZED_USER_ACCOUNT_NOT_VERIFIED")
    return account


def acquire_credentials(
    *,
    gcloud_bin: str,
    expected: ExpectedAuthority,
    timeout_seconds: int,
) -> CredentialEvidence:
    """Acquire one token from an explicit source; never consult PATH or ADC discovery."""
    _reject_ambient_credential_overrides()
    authorized_user_text = os.environ.get(AUTHORIZED_USER_FILE_ENV, "").strip()
    if bool(gcloud_bin) == bool(authorized_user_text):
        raise AuthorityError("EXACTLY_ONE_EXPLICIT_CREDENTIAL_SOURCE_REQUIRED")
    if gcloud_bin:
        executable = _resolve_gcloud(gcloud_bin)
        active_rows = [
            row.strip()
            for row in _run_gcloud(
                executable,
                ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
                "GCLOUD_ACTIVE_ACCOUNT_QUERY_FAILED",
            ).splitlines()
            if row.strip()
        ]
        if len(active_rows) != 1:
            raise AuthorityError("GCLOUD_ACTIVE_ACCOUNT_NOT_UNIQUE")
        configured_account = _run_gcloud(
            executable,
            ["config", "get-value", "account"],
            "GCLOUD_CONFIG_ACCOUNT_QUERY_FAILED",
        )
        configured_project = _run_gcloud(
            executable,
            ["config", "get-value", "project"],
            "GCLOUD_CONFIG_PROJECT_QUERY_FAILED",
        )
        if active_rows[0] != configured_account:
            raise AuthorityError("GCLOUD_ACTIVE_AND_CONFIGURED_ACCOUNT_DIFFER")
        impersonated_account = _run_gcloud(
            executable,
            ["config", "get-value", "auth/impersonate_service_account"],
            "GCLOUD_IMPERSONATION_QUERY_FAILED",
            allow_empty=True,
        )
        if impersonated_account not in {"", "(unset)", "None"}:
            raise AuthorityError("GCLOUD_SERVICE_ACCOUNT_IMPERSONATION_PROHIBITED")
        token = _run_gcloud(
            executable,
            ["auth", "print-access-token"],
            "GCLOUD_ACCESS_TOKEN_REFRESH_FAILED",
        )
        token_account = _userinfo_account(token, timeout_seconds=timeout_seconds)
        if token_account != active_rows[0]:
            raise AuthorityError("GCLOUD_TOKEN_IDENTITY_DOES_NOT_MATCH_ACTIVE_ACCOUNT")
        return CredentialEvidence(
            access_token=token,
            source_kind="PINNED_GCLOUD_EXECUTABLE",
            observed_account=active_rows[0],
            configured_project=configured_project,
            source_sha256=sha256_file(executable),
            gcloud_executable_pinned=True,
            authorized_user_adc_pinned=False,
        )
    access_token, asserted_account, quota_project, source_sha256 = _refresh_authorized_user_token(
        Path(authorized_user_text)
    )
    token_account = _userinfo_account(access_token, timeout_seconds=timeout_seconds)
    if asserted_account and asserted_account != token_account:
        raise AuthorityError("AUTHORIZED_USER_ADC_ACCOUNT_DOES_NOT_MATCH_TOKEN")
    return CredentialEvidence(
        access_token=access_token,
        source_kind="PINNED_AUTHORIZED_USER_ADC",
        observed_account=token_account,
        configured_project=quota_project,
        source_sha256=source_sha256,
        gcloud_executable_pinned=False,
        authorized_user_adc_pinned=True,
    )


def acquire_access_token_for_preflight(
    *, gcloud_bin: str, timeout_seconds: int = 120
) -> str:
    """Acquire a token using the same fail-closed authority environment."""
    expected = _expected_from_environment()
    evidence = acquire_credentials(
        gcloud_bin=gcloud_bin,
        expected=expected,
        timeout_seconds=timeout_seconds,
    )
    if evidence.observed_account != expected.account:
        raise AuthorityError("ACTIVE_IDENTITY_DOES_NOT_MATCH_EXPECTED")
    if evidence.configured_project != expected.project_id:
        raise AuthorityError("CONFIGURED_PROJECT_DOES_NOT_MATCH_EXPECTED")
    return evidence.access_token


def _probe_object_from_manifest(path: Path) -> tuple[str, str]:
    path = _require_regular_file(path, private=False).resolve(strict=True)
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise AuthorityError("SOURCE_MANIFEST_HEADER_MISSING")
            row = next(reader, None)
    except (OSError, csv.Error):
        raise AuthorityError("SOURCE_MANIFEST_PROBE_READ_FAILED") from None
    if not isinstance(row, Mapping):
        raise AuthorityError("SOURCE_MANIFEST_PROBE_ROW_MISSING")
    bucket = str(row.get("source_bucket") or BUCKET).strip()
    raw_path = str(row.get("source_relative_path") or row.get("gcs_uri") or "").strip()
    prefix = f"gs://{BUCKET}/"
    if raw_path.startswith("gs://"):
        if not raw_path.startswith(prefix):
            raise AuthorityError("SOURCE_MANIFEST_PROBE_BUCKET_MISMATCH")
        raw_path = raw_path[len(prefix) :]
    pure = PurePosixPath(raw_path)
    if (
        bucket != BUCKET
        or not raw_path.startswith("files/")
        or pure.as_posix() != raw_path
        or any(part in {"", ".", ".."} for part in pure.parts)
        or not raw_path.endswith(".dcm")
    ):
        raise AuthorityError("SOURCE_MANIFEST_PROBE_OBJECT_INVALID")
    return raw_path, sha256_file(path)


def _bigquery_dry_run(
    *, token: str, project_id: str, query: str, purpose: str, timeout_seconds: int
) -> int:
    fields = "jobReference(projectId),totalBytesProcessed,jobComplete,errors,rows,totalRows"
    url = (
        f"https://bigquery.googleapis.com/bigquery/v2/projects/"
        f"{quote(project_id, safe='')}/queries?{urlencode({'fields': fields})}"
    )
    response = _api_json(
        token=token,
        url=url,
        purpose=purpose,
        method="POST",
        payload={
            "query": query,
            "useLegacySql": False,
            "dryRun": True,
            "location": "US",
        },
        timeout_seconds=timeout_seconds,
    )
    reference = response.get("jobReference")
    if (
        (reference is not None and (
            not isinstance(reference, Mapping)
            or reference.get("projectId") != project_id
        ))
        or response.get("jobComplete") is not True
        or response.get("errors") not in (None, [])
        or response.get("rows") not in (None, [])
        or response.get("totalRows") not in (None, "0")
    ):
        raise AuthorityError(f"{purpose}_RESPONSE_INVALID")
    raw_bytes = response.get("totalBytesProcessed")
    text = str(raw_bytes if raw_bytes is not None else "")
    if not text.isdigit():
        raise AuthorityError(f"{purpose}_BYTES_PROCESSED_INVALID")
    return int(text)


def collect_observation(
    *,
    gcloud_bin: str,
    source_manifest: Path,
    wrapper_script: Path,
    execution_contract: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    expected = _expected_from_environment()
    run_authority = _load_run_authority(
        gcloud_bin=gcloud_bin,
        source_manifest=source_manifest,
        wrapper_script=wrapper_script,
        execution_contract=execution_contract,
    )
    credentials = acquire_credentials(
        gcloud_bin=gcloud_bin,
        expected=expected,
        timeout_seconds=timeout_seconds,
    )
    if credentials.observed_account != expected.account:
        raise AuthorityError("ACTIVE_IDENTITY_DOES_NOT_MATCH_EXPECTED")
    if credentials.configured_project != expected.project_id:
        raise AuthorityError("CONFIGURED_PROJECT_DOES_NOT_MATCH_EXPECTED")

    project_fields = "name,projectId,displayName,state"
    project = _api_json(
        token=credentials.access_token,
        url=(
            "https://cloudresourcemanager.googleapis.com/v3/projects/"
            f"{quote(expected.project_id, safe='')}?{urlencode({'$fields': project_fields})}"
        ),
        purpose="RESOURCE_MANAGER_PROJECT",
        timeout_seconds=timeout_seconds,
    )
    project_resource_valid = bool(PROJECT_RESOURCE_RE.fullmatch(str(project.get("name", ""))))
    project_id_match = project.get("projectId") == expected.project_id
    project_display_name_match = project.get("displayName") == expected.project_display_name
    project_active = project.get("state") == "ACTIVE"
    if not all((project_resource_valid, project_id_match, project_display_name_match, project_active)):
        raise AuthorityError("RESOURCE_MANAGER_PROJECT_AUTHORITY_MISMATCH")

    billing = _api_json(
        token=credentials.access_token,
        url=(
            "https://cloudbilling.googleapis.com/v1/projects/"
            f"{quote(expected.project_id, safe='')}/billingInfo?"
            f"{urlencode({'fields': 'projectId,billingAccountName,billingEnabled,name'})}"
        ),
        purpose="CLOUD_BILLING_PROJECT",
        timeout_seconds=timeout_seconds,
    )
    billing_account_name = str(billing.get("billingAccountName", ""))
    billing_project_match = billing.get("projectId") == expected.billing_project
    billing_enabled = billing.get("billingEnabled") is True
    billing_account_link_present = bool(BILLING_ACCOUNT_RE.fullmatch(billing_account_name))
    if not all((billing_project_match, billing_enabled, billing_account_link_present)):
        raise AuthorityError("CLOUD_BILLING_PROJECT_AUTHORITY_MISMATCH")

    billing_account_detail_query = "PASS"
    billing_account_open: bool | None = None
    billing_account_name_has_free_trial_signal = False
    try:
        account_payload = _api_json(
            token=credentials.access_token,
            url=(
                f"https://cloudbilling.googleapis.com/v1/{billing_account_name}?"
                f"{urlencode({'fields': 'displayName,open,masterBillingAccount'})}"
            ),
            purpose="CLOUD_BILLING_ACCOUNT",
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(account_payload.get("open"), bool):
            raise AuthorityError("CLOUD_BILLING_ACCOUNT_RESPONSE_INVALID")
        billing_account_open = bool(account_payload["open"])
        if not billing_account_open:
            raise AuthorityError("CLOUD_BILLING_ACCOUNT_NOT_OPEN")
        billing_account_name_has_free_trial_signal = (
            "free trial" in str(account_payload.get("displayName", "")).casefold()
        )
    except ApiHttpError as exc:
        if exc.status_code not in {403, 404}:
            raise
        billing_account_detail_query = "NOT_PERMITTED_OR_NOT_EXPOSED"

    bucket = _api_json(
        token=credentials.access_token,
        url=(
            f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}?"
            + urlencode(
                {
                    "fields": "name,billing(requesterPays)",
                    "userProject": expected.billing_project,
                }
            )
        ),
        purpose="GCS_BUCKET_METADATA",
        timeout_seconds=timeout_seconds,
    )
    requester_pays_metadata_access = (
        bucket.get("name") == BUCKET
        and isinstance(bucket.get("billing"), Mapping)
        and bucket["billing"].get("requesterPays") is True
    )
    if not requester_pays_metadata_access:
        raise AuthorityError("GCS_REQUESTER_PAYS_BUCKET_GATE_FAILED")

    probe_object, source_manifest_sha256 = _probe_object_from_manifest(source_manifest)
    if source_manifest_sha256 != run_authority.source_manifest_sha256:
        raise AuthorityError("SOURCE_MANIFEST_CHANGED_DURING_AUTHORITY_GATE")
    object_payload = _api_json(
        token=credentials.access_token,
        url=(
            f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}/o/"
            f"{quote(probe_object, safe='')}?"
            + urlencode(
                {
                    "fields": "name,size,generation,storageClass",
                    "userProject": expected.billing_project,
                }
            )
        ),
        purpose="GCS_OBJECT_METADATA_PROBE",
        timeout_seconds=timeout_seconds,
    )
    object_probe_passed = (
        object_payload.get("name") == probe_object
        and str(object_payload.get("size", "")).isdigit()
        and str(object_payload.get("generation", "")).isdigit()
        and bool(str(object_payload.get("storageClass", "")).strip())
    )
    if not object_probe_passed:
        raise AuthorityError("GCS_OBJECT_METADATA_PROBE_FAILED")

    bq_bytes: dict[str, int] = {}
    for name, query in BIGQUERY_DRY_RUNS.items():
        bq_bytes[name] = _bigquery_dry_run(
            token=credentials.access_token,
            project_id=expected.project_id,
            query=query,
            purpose=f"BIGQUERY_{name.upper()}_DRY_RUN",
            timeout_seconds=timeout_seconds,
        )

    return {
        "schema_version": 1,
        "status": "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY",
        "authority_scope": "ACTIVE_PROSPECTIVE_AUTHORITY_ONLY",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_commit": run_authority.expected_commit,
        "run_root_sha256": run_authority.run_root_sha256,
        "audit_script_sha256": run_authority.audit_script_sha256,
        "wrapper_script_sha256": run_authority.wrapper_script_sha256,
        "execution_contract_sha256": run_authority.execution_contract_sha256,
        "gcloud_resolver_script_sha256": run_authority.gcloud_resolver_script_sha256,
        "gcloud_resolution_record_sha256": (
            run_authority.gcloud_resolution_record_sha256
        ),
        "gcloud_resolution_record_bound": (
            run_authority.gcloud_resolution_record_bound
        ),
        "run_context_bound": run_authority.run_context_bound,
        "command_authority_bound": True,
        "expected_account_sha256": sha256_text(expected.account),
        "observed_account_sha256": sha256_text(credentials.observed_account),
        "active_identity_matches_expected": True,
        "expected_project_id_sha256": sha256_text(expected.project_id),
        "observed_project_id_sha256": sha256_text(str(project.get("projectId"))),
        "configured_project_matches_expected": True,
        "project_display_name_sha256": sha256_text(expected.project_display_name),
        "observed_project_display_name_sha256": sha256_text(str(project.get("displayName"))),
        "project_display_name_matches_expected": True,
        "requester_pays_project_sha256": sha256_text(expected.billing_project),
        "requester_pays_project_matches_expected": True,
        "project_resource_name_valid": project_resource_valid,
        "project_lifecycle_active": project_active,
        "billing_project_matches_expected": billing_project_match,
        "billing_link_active": billing_enabled,
        "billing_account_link_present": billing_account_link_present,
        "billing_account_detail_query": billing_account_detail_query,
        "billing_account_open": billing_account_open,
        "billing_account_display_name_has_free_trial_signal": (
            billing_account_name_has_free_trial_signal
        ),
        "free_trial_status": "NOT_MACHINE_VERIFIABLE",
        "free_trial_machine_verifiable": False,
        "credential_source_kind": credentials.source_kind,
        "credential_source_sha256": credentials.source_sha256,
        "gcloud_executable_pinned": credentials.gcloud_executable_pinned,
        "authorized_user_adc_pinned": credentials.authorized_user_adc_pinned,
        "source_manifest_sha256": source_manifest_sha256,
        "gcs_bucket_matches_expected": True,
        "gcs_requester_pays_metadata_access_passed": True,
        "gcs_single_object_metadata_probe_passed": True,
        "gcs_object_body_requests": 0,
        "gcs_object_body_bytes_read": 0,
        "bigquery_job_project_dry_run_passed": True,
        "bigquery_echo_entitlement_dry_run_passed": True,
        "bigquery_mimiciv_entitlement_dry_run_passed": True,
        "bigquery_job_project_bytes_processed": bq_bytes["job_project"],
        "bigquery_echo_bytes_processed": bq_bytes["physionet_echo"],
        "bigquery_mimiciv_bytes_processed": bq_bytes["physionet_mimiciv"],
        "bigquery_rows_returned": 0,
        "access_token_emitted": False,
        "credential_bytes_emitted": False,
        "account_or_project_identifier_emitted": False,
        "billing_account_identifier_emitted": False,
    }


def aggregate_receipt(restricted: Mapping[str, Any], restricted_sha256: str) -> dict[str, Any]:
    keys = (
        "status",
        "authority_scope",
        "expected_commit",
        "run_context_bound",
        "command_authority_bound",
        "gcloud_resolution_record_bound",
        "active_identity_matches_expected",
        "configured_project_matches_expected",
        "project_display_name_matches_expected",
        "requester_pays_project_matches_expected",
        "project_resource_name_valid",
        "project_lifecycle_active",
        "billing_project_matches_expected",
        "billing_link_active",
        "billing_account_link_present",
        "free_trial_machine_verifiable",
        "gcloud_executable_pinned",
        "authorized_user_adc_pinned",
        "gcs_bucket_matches_expected",
        "gcs_requester_pays_metadata_access_passed",
        "gcs_single_object_metadata_probe_passed",
        "gcs_object_body_requests",
        "gcs_object_body_bytes_read",
        "bigquery_job_project_dry_run_passed",
        "bigquery_echo_entitlement_dry_run_passed",
        "bigquery_mimiciv_entitlement_dry_run_passed",
        "access_token_emitted",
        "credential_bytes_emitted",
        "account_or_project_identifier_emitted",
        "billing_account_identifier_emitted",
    )
    output = {"schema_version": 1, "restricted_receipt_sha256": restricted_sha256}
    output.update({key: restricted.get(key) for key in keys})
    return output


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    _require_private_output_parent(path)
    serialized = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _load_receipt(path: Path) -> Mapping[str, Any]:
    _require_regular_file(path, private=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AuthorityError("AUTHORITY_RECEIPT_JSON_INVALID") from None
    if not isinstance(payload, Mapping):
        raise AuthorityError("AUTHORITY_RECEIPT_NOT_OBJECT")
    return payload


def validate_restricted_receipt(
    path: Path, *, expected_sha256: str, billing_project: str
) -> Mapping[str, Any]:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise AuthorityError("AUTHORITY_RECEIPT_EXPECTED_SHA256_INVALID")
    if sha256_file(path) != expected_sha256:
        raise AuthorityError("AUTHORITY_RECEIPT_SHA256_MISMATCH")
    payload = _load_receipt(path)
    expected = _expected_from_environment()
    if expected.billing_project != billing_project:
        raise AuthorityError("AUTHORITY_RECEIPT_BILLING_ARGUMENT_MISMATCH")

    wrapper_path_text = os.environ.get("GCP_AUTHORITY_WRAPPER", "").strip()
    wrapper_expected_sha = os.environ.get(
        "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256", ""
    ).strip()
    contract_path_text = os.environ.get("C3_EXECUTION_CONTRACT", "").strip()
    contract_expected_sha = os.environ.get(
        "EXPECTED_C3_EXECUTION_CONTRACT_SHA256", ""
    ).strip()
    source_expected_sha = os.environ.get("EXPECTED_SELECTED_SOURCE_SHA256", "").strip()
    resolver_path_text = os.environ.get("GCLOUD_RESOLVER", "").strip()
    resolver_expected_sha = os.environ.get(
        "EXPECTED_GCLOUD_RESOLVER_SHA256", ""
    ).strip()
    if not all(
        SHA256_RE.fullmatch(value)
        for value in (
            wrapper_expected_sha,
            contract_expected_sha,
            source_expected_sha,
            resolver_expected_sha,
        )
    ):
        raise AuthorityError("AUTHORITY_RECEIPT_COMMAND_BINDING_ENVIRONMENT_INVALID")
    wrapper_path = _require_regular_file(Path(wrapper_path_text), private=False)
    contract_path = _require_regular_file(Path(contract_path_text), private=False)
    resolver_path = _require_regular_file(Path(resolver_path_text), private=False)
    if (
        sha256_file(wrapper_path) != wrapper_expected_sha
        or payload.get("wrapper_script_sha256") != wrapper_expected_sha
        or sha256_file(contract_path) != contract_expected_sha
        or payload.get("execution_contract_sha256") != contract_expected_sha
        or payload.get("source_manifest_sha256") != source_expected_sha
        or sha256_file(resolver_path) != resolver_expected_sha
        or payload.get("gcloud_resolver_script_sha256") != resolver_expected_sha
        or payload.get("expected_account_sha256") != sha256_text(expected.account)
        or payload.get("observed_account_sha256") != sha256_text(expected.account)
        or payload.get("expected_project_id_sha256") != sha256_text(expected.project_id)
        or payload.get("observed_project_id_sha256") != sha256_text(expected.project_id)
        or payload.get("project_display_name_sha256")
        != sha256_text(expected.project_display_name)
        or payload.get("observed_project_display_name_sha256")
        != sha256_text(expected.project_display_name)
    ):
        raise AuthorityError("AUTHORITY_RECEIPT_COMMAND_OR_IDENTIFIER_BINDING_MISMATCH")

    credential_kind = payload.get("credential_source_kind")
    if credential_kind == "PINNED_GCLOUD_EXECUTABLE":
        record_text = os.environ.get("GCLOUD_RESOLUTION_RECORD", "").strip()
        record_expected_sha = os.environ.get(
            "EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256", ""
        ).strip()
        gcloud_text = os.environ.get("GCLOUD", "").strip()
        if not record_text or not SHA256_RE.fullmatch(record_expected_sha) or not gcloud_text:
            raise AuthorityError("AUTHORITY_RECEIPT_GCLOUD_BINDING_ENVIRONMENT_INVALID")
        record_path = _require_regular_file(Path(record_text), private=True)
        gcloud_path = _resolve_gcloud(gcloud_text)
        try:
            resolution = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AuthorityError("AUTHORITY_RECEIPT_GCLOUD_RESOLUTION_INVALID") from None
        if (
            sha256_file(record_path) != record_expected_sha
            or payload.get("gcloud_resolution_record_sha256") != record_expected_sha
            or not isinstance(resolution, Mapping)
            or resolution.get("status") != "PASS"
            or resolution.get("expected_version") != PINNED_GCLOUD_VERSION
            or resolution.get("version") != PINNED_GCLOUD_VERSION
            or resolution.get("selected_executable") != str(gcloud_path)
            or resolution.get("executable_sha256") != sha256_file(gcloud_path)
            or (
                str(gcloud_path).startswith(PINNED_GCLOUD_ROOT + "/")
                and resolution.get("retained_archive_sha256")
                != PINNED_GCLOUD_ARCHIVE_SHA256
            )
            or (
                str(gcloud_path).startswith(PINNED_GCLOUD_ROOT + "/")
                and resolution.get("retained_tar_payload_sha256")
                != PINNED_GCLOUD_TAR_PAYLOAD_SHA256
            )
            or payload.get("credential_source_sha256") != sha256_file(gcloud_path)
        ):
            raise AuthorityError("AUTHORITY_RECEIPT_GCLOUD_BINDING_MISMATCH")
    elif credential_kind == "PINNED_AUTHORIZED_USER_ADC":
        adc_text = os.environ.get(AUTHORIZED_USER_FILE_ENV, "").strip()
        adc_path = _require_regular_file(Path(adc_text), private=True)
        if payload.get("credential_source_sha256") != sha256_file(adc_path):
            raise AuthorityError("AUTHORITY_RECEIPT_ADC_BINDING_MISMATCH")
    else:
        raise AuthorityError("AUTHORITY_RECEIPT_CREDENTIAL_SOURCE_INVALID")

    required_true = (
        "run_context_bound",
        "command_authority_bound",
        "active_identity_matches_expected",
        "configured_project_matches_expected",
        "project_display_name_matches_expected",
        "requester_pays_project_matches_expected",
        "project_resource_name_valid",
        "project_lifecycle_active",
        "billing_project_matches_expected",
        "billing_link_active",
        "billing_account_link_present",
        "gcs_bucket_matches_expected",
        "gcs_requester_pays_metadata_access_passed",
        "gcs_single_object_metadata_probe_passed",
        "bigquery_job_project_dry_run_passed",
        "bigquery_echo_entitlement_dry_run_passed",
        "bigquery_mimiciv_entitlement_dry_run_passed",
    )
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "PASS_ACTIVE_PROSPECTIVE_GCP_AUTHORITY"
        or payload.get("authority_scope") != "ACTIVE_PROSPECTIVE_AUTHORITY_ONLY"
        or any(payload.get(key) is not True for key in required_true)
        or payload.get("requester_pays_project_sha256") != sha256_text(billing_project)
        or payload.get("expected_commit") != os.environ.get("EXPECTED_COMMIT", "").strip()
        or payload.get("run_root_sha256")
        != sha256_text(str(Path(os.environ.get("RUN_ROOT", "")).resolve(strict=True)))
        or payload.get("audit_script_sha256") != sha256_file(Path(__file__).resolve())
        or (
            payload.get("credential_source_kind") == "PINNED_GCLOUD_EXECUTABLE"
            and payload.get("gcloud_resolution_record_bound") is not True
        )
        or payload.get("gcs_object_body_requests") != 0
        or payload.get("gcs_object_body_bytes_read") != 0
        or payload.get("bigquery_rows_returned") != 0
        or payload.get("free_trial_machine_verifiable") is not False
        or payload.get("access_token_emitted") is not False
        or payload.get("credential_bytes_emitted") is not False
        or payload.get("account_or_project_identifier_emitted") is not False
        or payload.get("billing_account_identifier_emitted") is not False
    ):
        raise AuthorityError("AUTHORITY_RECEIPT_NOT_AUTHORITATIVE_PASS")
    return payload


def create_receipts(args: argparse.Namespace) -> dict[str, Any]:
    if args.restricted_output.exists() or args.aggregate_output.exists():
        raise AuthorityError("AUTHORITY_RECEIPT_OUTPUT_ALREADY_EXISTS")
    restricted = collect_observation(
        gcloud_bin=args.gcloud_bin,
        source_manifest=args.source_manifest,
        wrapper_script=args.wrapper_script,
        execution_contract=args.execution_contract,
        timeout_seconds=args.timeout_seconds,
    )
    _write_json_exclusive(args.restricted_output, restricted)
    restricted_sha256 = sha256_file(args.restricted_output)
    aggregate = aggregate_receipt(restricted, restricted_sha256)
    _write_json_exclusive(args.aggregate_output, aggregate)
    return aggregate


def validate_receipts(args: argparse.Namespace) -> dict[str, Any]:
    expected = _expected_from_environment()
    restricted_sha256 = sha256_file(_require_regular_file(args.restricted_output, private=True))
    prior = validate_restricted_receipt(
        args.restricted_output,
        expected_sha256=restricted_sha256,
        billing_project=expected.billing_project,
    )
    aggregate = _load_receipt(args.aggregate_output)
    if aggregate != aggregate_receipt(prior, restricted_sha256):
        raise AuthorityError("AGGREGATE_AUTHORITY_RECEIPT_MISMATCH")
    run_root = Path(os.environ.get("RUN_ROOT", ""))
    completed_source_summary = run_root / "aggregate" / "c3_full_source_preflight.summary.json"
    if completed_source_summary.exists():
        _load_receipt(completed_source_summary)
        listing_state_path = (
            run_root
            / "restricted"
            / "source_preflight"
            / "c3_gcs_listing_state.restricted.json"
        )
        listing_state = _load_receipt(listing_state_path)
        selected_studies = _require_regular_file(
            Path(os.environ.get("SELECTED_STUDIES", "")), private=False
        )
        split_map = _require_regular_file(
            Path(os.environ.get("SPLIT_MAP", "")), private=False
        )
        authority_hashes = {
            "gcp_authority_receipt_sha256": restricted_sha256,
            "selected_source_manifest_sha256": sha256_file(args.source_manifest),
            "selected_studies_sha256": sha256_file(selected_studies),
            "split_map_sha256": sha256_file(split_map),
        }
        expected_authority_hash_set_sha256 = sha256_bytes(
            json.dumps(
                authority_hashes, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        if (
            listing_state.get("schema_version") != 5
            or listing_state.get("complete") is not True
            or listing_state.get("authority_hash_set_sha256")
            != expected_authority_hash_set_sha256
            or listing_state.get("media_requests") != 0
            or listing_state.get("body_bytes_read") != 0
        ):
            raise AuthorityError("COMPLETED_SOURCE_OUTPUT_AUTHORITY_MISMATCH")
    current = collect_observation(
        gcloud_bin=args.gcloud_bin,
        source_manifest=args.source_manifest,
        wrapper_script=args.wrapper_script,
        execution_contract=args.execution_contract,
        timeout_seconds=args.timeout_seconds,
    )
    stable_keys = set(prior) - {"observed_at_utc"}
    if any(current.get(key) != prior.get(key) for key in stable_keys):
        raise AuthorityError("LIVE_AUTHORITY_NO_LONGER_MATCHES_RECEIPT")
    return dict(aggregate)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "validate"))
    parser.add_argument("--gcloud-bin", default="")
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--wrapper-script", type=Path, required=True)
    parser.add_argument(
        "--execution-contract",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_c3_execution_contract.yaml",
    )
    parser.add_argument("--restricted-output", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if args.timeout_seconds < 1 or args.timeout_seconds > 600:
        parser.error("--timeout-seconds must be between 1 and 600")
    for name in (
        "source_manifest",
        "wrapper_script",
        "execution_contract",
        "restricted_output",
        "aggregate_output",
    ):
        setattr(args, name, getattr(args, name).resolve())
    return args


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = parse_args(argv)
    try:
        result = create_receipts(args) if args.mode == "create" else validate_receipts(args)
    except (AuthorityError, OSError, subprocess.SubprocessError) as exc:
        code = str(exc) if isinstance(exc, AuthorityError) else "GCP_AUTHORITY_GATE_RUNTIME_FAILURE"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "authority_verified": True,
                "billing_link_active": result["billing_link_active"],
                "gcs_metadata_access_passed": result[
                    "gcs_requester_pays_metadata_access_passed"
                ],
                "bigquery_access_passed": all(
                    result[key]
                    for key in (
                        "bigquery_job_project_dry_run_passed",
                        "bigquery_echo_entitlement_dry_run_passed",
                        "bigquery_mimiciv_entitlement_dry_run_passed",
                    )
                ),
                "free_trial_status": "NOT_MACHINE_VERIFIABLE",
                "identifiers_emitted": False,
                "tokens_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
