from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: this module contains no restricted values.

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock
from urllib.error import URLError

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import adjudicate_lvef_c3_autoclass as adjudicator
import audit_lvef_c3_gcp_authority as gcp_authority
from lvef_c3_autoclass_states import (
    AutoclassStateError,
    adjudicate_autoclass,
    observe_raw_autoclass,
    strict_json_loads,
)
from lvef_multitask_analysis_modes import (
    SafetyPolicyError,
    load_policy,
    validate_candidate_bytes,
)


SAFE_POLICY_PATH = ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
RESOURCE_POLICY_PATH = ROOT / "configs" / "lvef_c3_resource_policy.yaml"


class _Headers:
    def __init__(self, content_type: str = "application/json") -> None:
        self.content_type = content_type

    def get_content_type(self) -> str:
        return self.content_type


class _Response:
    def __init__(
        self,
        payload: bytes,
        *,
        url: str | None = None,
        content_type: str = "application/json",
        status: int = 200,
    ) -> None:
        self.payload = payload
        self.url = url
        self.headers = _Headers(content_type)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self) -> str:
        assert self.url is not None
        return self.url

    def getcode(self) -> int:
        return self.status

    def read(self, _: int) -> bytes:
        return self.payload


def _valid_bucket_payload(autoclass_marker: object = ... ) -> bytes:
    payload = {
        "location": "US",
        "locationType": "multi-region",
        "storageClass": "STANDARD",
        "billing": {"requesterPays": True},
        "metageneration": "1",
        "updated": "2026-08-09T00:00:00Z",
    }
    if autoclass_marker is not ...:
        payload["autoclass"] = autoclass_marker
    return json.dumps(payload).encode()


def _adjudicate(payload: dict, **overrides):
    evidence = {
        "bucket_get_succeeded": True,
        "fields_selector_proven": True,
        "content_type_json": True,
        "redirect_occurred": False,
        "raw_response_receipt_verified": True,
        "official_default_disabled_semantics_verified": True,
    }
    evidence.update(overrides)
    return adjudicate_autoclass(payload, **evidence)


def _source_summary() -> dict:
    return {
        "status": "PASS_METADATA_ONLY",
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "raw_source_request_rows": 336016,
        "requested_objects": 335984,
        "verified_objects": 335984,
        "exact_source_bytes": 1216569133322,
        "missing_objects": 0,
        "unexpected_selected_objects": 0,
        "ownership_conflicts": 0,
        "repeated_locator_groups_in_frozen_manifest": 0,
        "historical_identical_rows_collapsed_before_frozen_manifest": 32,
        "zero_record_studies": 0,
        "production_batches": 19,
        "metadata_listing_pages": 526,
        "media_requests": 0,
        "object_body_bytes_read": 0,
        "dicom_bodies_downloaded": False,
        "selected_manifest_sha256": "a" * 64,
        "split_manifest_sha256": "b" * 64,
        "selected_source_manifest_sha256": "c" * 64,
        "historical_metadata_comparable_counts": {
            "size": 0,
            "md5": 0,
            "crc32c": 0,
            "generation": 0,
        },
        "historical_metadata_mismatch_counts": {
            "size": 0,
            "md5": 0,
            "crc32c": 0,
            "generation": 0,
        },
        "storage_class_counts": {"STANDARD": 335984},
        "storage_class_bytes": {"STANDARD": 1216569133322},
        "bucket_metadata_operations": 1,
        "bucket_location_rate_match": True,
        "bucket_requester_pays_enabled": True,
    }


def _source_authority(summary: dict | None = None, safety: dict | None = None) -> dict:
    return adjudicator._build_source_authority(
        source_summary=summary or _source_summary(),
        source_safety=safety
        or {
            "status": "PASS",
            "safety_gate_passed": True,
            "media_requests": 0,
            "object_body_bytes_read": 0,
        },
        batch_totals={
            "studies": 4530,
            "subjects": 4530,
            "requested": 335984,
            "verified": 335984,
            "unexpected": 0,
            "bytes": 1216569133322,
        },
        original_payloads={
            "c3_full_source_preflight.summary.json": b"a",
            "c3_full_source_preflight_by_batch.csv": b"b",
            "c3_full_source_preflight_safety_gate.json": b"c",
        },
    )


def _current_bucket_metadata() -> dict:
    return {
        "location_rate_match": True,
        "location_type_rate_match": True,
        "default_storage_class_standard": True,
        "requester_pays_enabled": True,
        "consistent_with_source_summary": True,
    }


def _synthetic_targeted_evidence() -> tuple[bytes, bytes, bytes, dict]:
    governing = "a" * 40
    evidence_sha = "e" * 64
    raw = _valid_bucket_payload()
    classification = _adjudicate({}).to_dict()
    request = {
        "schema_version": 1,
        "status": "REQUEST_AUTHORITY_FROZEN_BEFORE_NETWORK",
        "attempt_id": adjudicator.ATTEMPT_ID,
        "source_scheduler_job_id": adjudicator.SOURCE_SCHEDULER_JOB_ID,
        "governing_commit": governing,
        "method": "GET",
        "endpoint_class": adjudicator.TARGETED_BUCKET_ENDPOINT_CLASS,
        "sanitized_fields_projection": adjudicator.TARGETED_BUCKET_FIELDS,
        "fields_explicitly_requested": True,
        "requester_pays_project_supplied": True,
        "maximum_response_bytes": adjudicator.MAX_TARGETED_RESPONSE_BYTES,
        "automatic_retry": False,
        "planned_request_count": 1,
        "media_endpoint_allowed": False,
        "object_list_allowed": False,
        "object_get_allowed": False,
        "bigquery_allowed": False,
        "official_evidence_registry_sha256": evidence_sha,
        "created_at_utc": "2026-08-09T00:00:00Z",
    }
    request_bytes = adjudicator._json_bytes(request)
    response = {
        "schema_version": 1,
        "status": "PASS_TARGETED_BUCKET_METADATA_RESPONSE_CAPTURED",
        "attempt_id": adjudicator.ATTEMPT_ID,
        "source_scheduler_job_id": adjudicator.SOURCE_SCHEDULER_JOB_ID,
        "governing_commit": governing,
        "request_receipt_sha256": adjudicator.sha256_bytes(request_bytes),
        "method": "GET",
        "endpoint_class": adjudicator.TARGETED_BUCKET_ENDPOINT_CLASS,
        "http_success": True,
        "http_status": 200,
        "content_type_json": True,
        "redirect_occurred": False,
        "raw_response_size_bytes": len(raw),
        "raw_response_sha256": adjudicator.sha256_bytes(raw),
        **classification,
        "request_count": 1,
        "requester_pays_project_supplied": True,
        "media_requests": 0,
        "object_list_requests": 0,
        "object_get_requests": 0,
        "object_body_bytes_read": 0,
        "bigquery_requests": 0,
        "credential_content_exported": False,
        "captured_at_utc": "2026-08-09T00:00:01Z",
    }
    response_bytes = adjudicator._json_bytes(response)
    summary = adjudicator._safe_capture_summary(
        governing_commit=governing,
        request_receipt_sha256=adjudicator.sha256_bytes(request_bytes),
        response_receipt_sha256=adjudicator.sha256_bytes(response_bytes),
        raw_response_sha256=adjudicator.sha256_bytes(raw),
        raw_response_size_bytes=len(raw),
        classification=classification,
        official_evidence_registry_sha256=evidence_sha,
        bucket_location_rate_match=True,
        bucket_location_type_rate_match=True,
        bucket_default_storage_class_standard=True,
        requester_pays_enabled=True,
    )
    return request_bytes, response_bytes, raw, summary


def test_raw_key_absent() -> None:
    assert observe_raw_autoclass({}) == "KEY_ABSENT"


def test_raw_key_null() -> None:
    assert observe_raw_autoclass({"autoclass": None}) == "KEY_PRESENT_NULL"


def test_raw_explicit_enabled() -> None:
    assert observe_raw_autoclass({"autoclass": {"enabled": True}}) == "KEY_PRESENT_MAPPING_ENABLED_TRUE"


def test_raw_explicit_disabled() -> None:
    assert observe_raw_autoclass({"autoclass": {"enabled": False}}) == "KEY_PRESENT_MAPPING_ENABLED_FALSE"


def test_raw_empty_mapping() -> None:
    assert observe_raw_autoclass({"autoclass": {}}) == "KEY_PRESENT_EMPTY_MAPPING"


def test_raw_mapping_without_enabled() -> None:
    assert observe_raw_autoclass({"autoclass": {"toggleTime": "x"}}) == "KEY_PRESENT_MAPPING_ENABLED_MISSING"


def test_raw_nonboolean_enabled_is_malformed() -> None:
    assert observe_raw_autoclass({"autoclass": {"enabled": 1}}) == "KEY_PRESENT_MALFORMED"


def test_raw_string_list_number_are_malformed() -> None:
    for value in ("false", [], 0):
        assert observe_raw_autoclass({"autoclass": value}) == "KEY_PRESENT_MALFORMED"


def test_unproven_failed_request_is_closed() -> None:
    result = _adjudicate({}, bucket_get_succeeded=False)
    assert result.raw_autoclass_observation_state == "REQUEST_OR_RECEIPT_UNPROVEN"


def test_unproven_selector_is_closed() -> None:
    assert _adjudicate({}, fields_selector_proven=False).effective_autoclass_semantic_state == "MALFORMED_OR_UNPROVEN"


def test_missing_receipt_is_closed() -> None:
    assert _adjudicate({}, raw_response_receipt_verified=False).effective_autoclass_semantic_state == "MALFORMED_OR_UNPROVEN"


def test_receipt_hash_mismatch_is_closed() -> None:
    result = _adjudicate({"autoclass": {"enabled": False}}, raw_response_receipt_verified=False)
    assert result.autoclass_authoritatively_disabled is False


def test_offline_recomputation_rejects_false_aggregate_raw_state() -> None:
    request, response, raw, summary = _synthetic_targeted_evidence()
    summary["raw_autoclass_observation_state"] = "KEY_PRESENT_NULL"
    try:
        adjudicator._recompute_and_verify_targeted_evidence(
            request_bytes=request,
            response_bytes=response,
            raw_bytes=raw,
            autoclass_summary=summary,
            evidence_sha256="e" * 64,
            governing_commit="a" * 40,
        )
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "TARGETED_AUTOCLASS_STATE_RECOMPUTATION_MISMATCH"
    else:
        raise AssertionError("Aggregate state contradictory to raw receipt was accepted")


def test_offline_receipt_bundle_tampering_fails_closed() -> None:
    request, response, raw, summary = _synthetic_targeted_evidence()
    cases = []
    request_extra = json.loads(request)
    request_extra["unexpected"] = True
    cases.append((adjudicator._json_bytes(request_extra), response, raw, summary))
    response_extra = json.loads(response)
    response_extra["unexpected"] = True
    cases.append((request, adjudicator._json_bytes(response_extra), raw, summary))
    request_selector = json.loads(request)
    request_selector["sanitized_fields_projection"] = "autoclass"
    cases.append((adjudicator._json_bytes(request_selector), response, raw, summary))
    response_status = json.loads(response)
    response_status["http_status"] = 204
    cases.append((request, adjudicator._json_bytes(response_status), raw, summary))
    response_content = json.loads(response)
    response_content["content_type_json"] = False
    cases.append((request, adjudicator._json_bytes(response_content), raw, summary))
    response_redirect = json.loads(response)
    response_redirect["redirect_occurred"] = True
    cases.append((request, adjudicator._json_bytes(response_redirect), raw, summary))
    response_count = json.loads(response)
    response_count["request_count"] = 2
    cases.append((request, adjudicator._json_bytes(response_count), raw, summary))
    response_counter = json.loads(response)
    response_counter["object_list_requests"] = 1
    cases.append((request, adjudicator._json_bytes(response_counter), raw, summary))
    cases.append((request, response, raw + b" ", summary))
    request_commit = json.loads(request)
    request_commit["governing_commit"] = "b" * 40
    cases.append((adjudicator._json_bytes(request_commit), response, raw, summary))

    for request_case, response_case, raw_case, summary_case in cases:
        try:
            adjudicator._recompute_and_verify_targeted_evidence(
                request_bytes=request_case,
                response_bytes=response_case,
                raw_bytes=raw_case,
                autoclass_summary=summary_case,
                evidence_sha256="e" * 64,
                governing_commit="a" * 40,
            )
        except adjudicator.AdjudicationError:
            pass
        else:
            raise AssertionError("Tampered targeted evidence bundle was accepted")


def test_supplemental_output_dag_binds_all_predecessors() -> None:
    autoclass_bytes, source_bytes, cost_bytes = b"a", b"b", b"c"
    manifest = {
        "derived_artifacts": [
            {
                "role": role,
                "filename": adjudicator.AGGREGATE_FILENAMES[role],
                "size_bytes": len(payload),
                "sha256": adjudicator.sha256_bytes(payload),
                "export_profile": adjudicator.AGGREGATE_PROFILES[role],
                "closed_schema_status": "PASS",
            }
            for role, payload in (
                ("autoclass", autoclass_bytes),
                ("source", source_bytes),
                ("cost", cost_bytes),
            )
        ]
    }
    manifest_bytes = adjudicator._json_bytes(manifest)
    safety = {
        "aggregate_files_checked": 4,
        "provenance_manifest_sha256": adjudicator.sha256_bytes(manifest_bytes),
    }
    safety_bytes = adjudicator._json_bytes(safety)
    combined = {
        "autoclass_summary_sha256": adjudicator.sha256_bytes(autoclass_bytes),
        "source_authority_sha256": adjudicator.sha256_bytes(source_bytes),
        "cost_authority_sha256": adjudicator.sha256_bytes(cost_bytes),
        "provenance_manifest_sha256": adjudicator.sha256_bytes(manifest_bytes),
        "safety_gate_sha256": adjudicator.sha256_bytes(safety_bytes),
    }
    kwargs = {
        "manifest_payload": manifest,
        "safety_payload": safety,
        "combined_payload": combined,
        "autoclass_bytes": autoclass_bytes,
        "source_bytes": source_bytes,
        "cost_bytes": cost_bytes,
        "manifest_bytes": manifest_bytes,
        "safety_bytes": safety_bytes,
    }
    adjudicator.validate_supplemental_artifact_dag(**kwargs)
    for collection, key in (
        (manifest["derived_artifacts"][0], "sha256"),
        (safety, "provenance_manifest_sha256"),
        (combined, "safety_gate_sha256"),
    ):
        original = collection[key]
        collection[key] = "0" * 64
        try:
            adjudicator.validate_supplemental_artifact_dag(**kwargs)
        except adjudicator.AdjudicationError:
            pass
        else:
            raise AssertionError("Tampered supplemental artifact DAG was accepted")
        collection[key] = original


def test_redirect_is_closed() -> None:
    assert _adjudicate({}, redirect_occurred=True).raw_autoclass_observation_state == "REQUEST_OR_RECEIPT_UNPROVEN"


def test_non_json_response_is_closed() -> None:
    assert _adjudicate({}, content_type_json=False).effective_autoclass_semantic_state == "MALFORMED_OR_UNPROVEN"


def test_media_endpoint_is_rejected() -> None:
    url = "https://storage.googleapis.com/download/storage/v1/b/x?alt=media&userProject=p"
    try:
        adjudicator.validate_targeted_request_url(url, billing_project="p")
    except adjudicator.AdjudicationError:
        pass
    else:
        raise AssertionError("Media endpoint was accepted")


def test_oversized_response_is_rejected() -> None:
    payload = b"x" * (adjudicator.MAX_TARGETED_RESPONSE_BYTES + 1)

    def opener(request, timeout):
        return _Response(payload, url=request.full_url)

    try:
        adjudicator.perform_one_targeted_bucket_get(
            access_token="synthetic", billing_project="synthetic-project", timeout_seconds=1, opener=opener
        )
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "TARGETED_BUCKET_RESPONSE_TOO_LARGE"
    else:
        raise AssertionError("Oversized response was accepted")


def test_exactly_one_request_attempted_and_no_retry() -> None:
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        calls += 1
        return _Response(_valid_bucket_payload(), url=request.full_url)

    response = adjudicator.perform_one_targeted_bucket_get(
        access_token="synthetic", billing_project="synthetic-project", timeout_seconds=1, opener=opener
    )
    assert calls == response.request_count == 1


def test_request_failure_has_no_retry() -> None:
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        calls += 1
        raise URLError("synthetic")

    try:
        adjudicator.perform_one_targeted_bucket_get(
            access_token="synthetic", billing_project="synthetic-project", timeout_seconds=1, opener=opener
        )
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "TARGETED_BUCKET_NETWORK_FAILURE"
        assert calls == 1
    else:
        raise AssertionError("Network failure was accepted")


def test_object_listing_endpoint_is_rejected() -> None:
    url = "https://storage.googleapis.com/storage/v1/b/x/o?fields=items&userProject=p"
    try:
        adjudicator.validate_targeted_request_url(url, billing_project="p")
    except adjudicator.AdjudicationError:
        pass
    else:
        raise AssertionError("Object listing endpoint was accepted")


def test_object_get_endpoint_is_rejected() -> None:
    url = "https://storage.googleapis.com/storage/v1/b/x/o/object?fields=name&userProject=p"
    try:
        adjudicator.validate_targeted_request_url(url, billing_project="p")
    except adjudicator.AdjudicationError:
        pass
    else:
        raise AssertionError("Object GET endpoint was accepted")


def test_source_authority_can_pass_independently_of_cost() -> None:
    source = _source_authority()
    safe_policy, _ = load_policy(SAFE_POLICY_PATH)
    validate_candidate_bytes(
        adjudicator._json_bytes(source),
        filename=adjudicator.AGGREGATE_FILENAMES["source"],
        profile_name=adjudicator.AGGREGATE_PROFILES["source"],
        policy=safe_policy,
    )
    policy = yaml.safe_load(RESOURCE_POLICY_PATH.read_text())
    cost = adjudicator._calculate_revised_cost(
        source_summary=_source_summary(),
        original_cost={"projected_requester_pays_total_usd": "171.488027"},
        autoclass_summary={
            "effective_autoclass_semantic_state": "PRESENT_NULL_UNRESOLVED",
            "autoclass_authoritatively_disabled": False,
        },
        resource_policy=policy,
        official_evidence_sha256="d" * 64,
        source_authority_status=source["status"],
        current_bucket_metadata=_current_bucket_metadata(),
    )
    assert source["source_inventory_authority"] is True
    assert cost["cost_authority"] is False


def test_source_blocks_on_object_count_mismatch() -> None:
    summary = _source_summary()
    summary["verified_objects"] -= 1
    assert _source_authority(summary)["source_inventory_authority"] is False


def test_source_blocks_on_selected_byte_mismatch() -> None:
    summary = _source_summary()
    summary["exact_source_bytes"] -= 1
    assert _source_authority(summary)["source_inventory_authority"] is False


def test_source_blocks_on_body_byte() -> None:
    summary = _source_summary()
    summary["object_body_bytes_read"] = 1
    assert _source_authority(summary)["source_inventory_authority"] is False


def test_source_blocks_on_media_request() -> None:
    summary = _source_summary()
    summary["media_requests"] = 1
    assert _source_authority(summary)["source_inventory_authority"] is False


def test_valid_key_absence_is_default_disabled() -> None:
    result = _adjudicate({})
    assert result.effective_autoclass_semantic_state == "ABSENT_CONFIGURATION_DEFAULT_DISABLED"
    assert result.autoclass_authoritatively_disabled is True


def test_json_null_remains_unresolved() -> None:
    result = _adjudicate({"autoclass": None})
    assert result.effective_autoclass_semantic_state == "PRESENT_NULL_UNRESOLVED"


def test_incomplete_mapping_remains_unresolved() -> None:
    result = _adjudicate({"autoclass": {}})
    assert result.effective_autoclass_semantic_state == "EMPTY_OR_INCOMPLETE_MAPPING_UNRESOLVED"


def test_original_output_cannot_be_overwritten() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        target = root / "evidence.json"
        adjudicator.write_exclusive_no_follow(target, b"{}\n")
        try:
            adjudicator.write_exclusive_no_follow(target, b"{}\n")
        except FileExistsError:
            pass
        else:
            raise AssertionError("No-clobber writer overwrote an artifact")


def test_setgid_private_attempt_directory_is_portable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "private"
        root.mkdir(mode=0o700)
        root.chmod(0o2700)
        assert adjudicator.private_directory_mode_ok(root.stat().st_mode)
        target = root / "receipt.json"
        adjudicator.write_exclusive_no_follow(target, b"{}\n")
        assert target.stat().st_mode & 0o7777 == 0o600


def test_duplicate_json_keys_are_rejected() -> None:
    try:
        strict_json_loads(b'{"autoclass":null,"AUTOCLASS":{}}')
    except AutoclassStateError as exc:
        assert str(exc) == "RAW_RESPONSE_DUPLICATE_JSON_KEY"
    else:
        raise AssertionError("Duplicate JSON keys were accepted")


def test_duplicate_csv_columns_are_rejected() -> None:
    header = ",".join((*adjudicator.EXPECTED_BATCH_HEADER[:-1], "production_batch"))
    try:
        adjudicator._validate_batch_table((header + "\n").encode())
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "SOURCE_BATCH_TABLE_HEADER_INVALID_OR_DUPLICATED"
    else:
        raise AssertionError("Duplicate CSV header was accepted")


def test_new_safe_export_profiles_are_closed_without_wildcards() -> None:
    policy, _ = load_policy(SAFE_POLICY_PATH)
    for name in adjudicator.AGGREGATE_PROFILES.values():
        profile = policy["export_profiles"][name]
        assert profile["required_top_level_keys"] == profile["allowed_top_level_keys"]
        assert "*" not in profile["allowed_top_level_keys"]


def test_symlink_is_rejected_by_nofollow_reader() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "target"
        target.write_bytes(b"x")
        link = root / "link"
        link.symlink_to(target)
        try:
            adjudicator.read_regular_bytes_no_follow(link)
        except OSError:
            pass
        else:
            raise AssertionError("Symlink was followed")


def test_restricted_path_value_is_rejected_from_aggregate() -> None:
    policy, _ = load_policy(SAFE_POLICY_PATH)
    payload = adjudicator._safe_capture_summary(
        governing_commit="a" * 40,
        request_receipt_sha256="b" * 64,
        response_receipt_sha256="c" * 64,
        raw_response_sha256="d" * 64,
        raw_response_size_bytes=1,
        classification=_adjudicate({}).to_dict(),
        official_evidence_registry_sha256="e" * 64,
        bucket_location_rate_match=True,
        bucket_location_type_rate_match=True,
        bucket_default_storage_class_standard=True,
        requester_pays_enabled=True,
    )
    payload["status"] = "/restricted/project/private"
    candidate = (json.dumps(payload) + "\n").encode()
    try:
        validate_candidate_bytes(
            candidate,
            filename=adjudicator.AGGREGATE_FILENAMES["autoclass"],
            profile_name=adjudicator.AGGREGATE_PROFILES["autoclass"],
            policy=policy,
        )
    except SafetyPolicyError:
        pass
    else:
        raise AssertionError("Restricted path was exportable")


def test_cloud_locator_is_rejected_from_aggregate() -> None:
    policy, _ = load_policy(SAFE_POLICY_PATH)
    payload = adjudicator._safe_capture_summary(
        governing_commit="a" * 40,
        request_receipt_sha256="b" * 64,
        response_receipt_sha256="c" * 64,
        raw_response_sha256="d" * 64,
        raw_response_size_bytes=1,
        classification=_adjudicate({}).to_dict(),
        official_evidence_registry_sha256="e" * 64,
        bucket_location_rate_match=True,
        bucket_location_type_rate_match=True,
        bucket_default_storage_class_standard=True,
        requester_pays_enabled=True,
    )
    payload["status"] = "gs://private"
    try:
        validate_candidate_bytes(
            (json.dumps(payload) + "\n").encode(),
            filename=adjudicator.AGGREGATE_FILENAMES["autoclass"],
            profile_name=adjudicator.AGGREGATE_PROFILES["autoclass"],
            policy=policy,
        )
    except SafetyPolicyError:
        pass
    else:
        raise AssertionError("Cloud locator was exportable")


def test_default_disabled_requires_primary_semantic_authority() -> None:
    result = _adjudicate({}, official_default_disabled_semantics_verified=False)
    assert result.effective_autoclass_semantic_state == "MALFORMED_OR_UNPROVEN"
    assert result.autoclass_authoritatively_disabled is False


def test_explicit_disabled_is_authoritative() -> None:
    result = _adjudicate({"autoclass": {"enabled": False}})
    assert result.effective_autoclass_semantic_state == "EXPLICIT_DISABLED"
    assert result.autoclass_authoritatively_disabled is True


def test_explicit_enabled_is_not_disabled() -> None:
    result = _adjudicate({"autoclass": {"enabled": True}})
    assert result.effective_autoclass_semantic_state == "EXPLICIT_ENABLED"
    assert result.autoclass_authoritatively_disabled is False


def test_response_content_type_is_enforced() -> None:
    def opener(request, timeout):
        return _Response(_valid_bucket_payload(), url=request.full_url, content_type="text/plain")

    try:
        adjudicator.perform_one_targeted_bucket_get(
            access_token="synthetic", billing_project="synthetic-project", timeout_seconds=1, opener=opener
        )
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "TARGETED_BUCKET_CONTENT_TYPE_NOT_JSON"
    else:
        raise AssertionError("Non-JSON content type was accepted")


def test_response_redirect_is_enforced() -> None:
    def opener(request, timeout):
        return _Response(_valid_bucket_payload(), url="https://example.invalid/redirect")

    try:
        adjudicator.perform_one_targeted_bucket_get(
            access_token="synthetic", billing_project="synthetic-project", timeout_seconds=1, opener=opener
        )
    except adjudicator.AdjudicationError as exc:
        assert str(exc) == "TARGETED_BUCKET_REDIRECT_PROHIBITED"
    else:
        raise AssertionError("Redirect was accepted")


def test_cost_correction_is_exact() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY_PATH.read_text())
    cost = adjudicator._calculate_revised_cost(
        source_summary=_source_summary(),
        original_cost={"projected_requester_pays_total_usd": "171.488027"},
        autoclass_summary={
            "effective_autoclass_semantic_state": "ABSENT_CONFIGURATION_DEFAULT_DISABLED",
            "autoclass_authoritatively_disabled": True,
        },
        resource_policy=policy,
        official_evidence_sha256="f" * 64,
        source_authority_status=adjudicator.SOURCE_AUTHORITY_PASS,
        current_bucket_metadata=_current_bucket_metadata(),
    )
    assert (cost["original_low_usd"], cost["original_base_usd"], cost["original_high_usd"]) == (
        "136.101859", "142.906689", "171.488027"
    )
    assert (cost["revised_low_usd"], cost["revised_base_usd"], cost["revised_high_usd"]) == (
        "136.101850", "142.906680", "171.488015"
    )
    assert cost["currency"] == "USD"
    safe_policy, _ = load_policy(SAFE_POLICY_PATH)
    validate_candidate_bytes(
        adjudicator._json_bytes(cost),
        filename=adjudicator.AGGREGATE_FILENAMES["cost"],
        profile_name=adjudicator.AGGREGATE_PROFILES["cost"],
        policy=safe_policy,
    )


def test_cost_authority_requires_every_current_bucket_metadata_gate() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY_PATH.read_text())
    for key in _current_bucket_metadata():
        current = _current_bucket_metadata()
        current[key] = False
        cost = adjudicator._calculate_revised_cost(
            source_summary=_source_summary(),
            original_cost={"projected_requester_pays_total_usd": "171.488027"},
            autoclass_summary={
                "effective_autoclass_semantic_state":
                    "ABSENT_CONFIGURATION_DEFAULT_DISABLED",
                "autoclass_authoritatively_disabled": True,
            },
            resource_policy=policy,
            official_evidence_sha256="f" * 64,
            source_authority_status=adjudicator.SOURCE_AUTHORITY_PASS,
            current_bucket_metadata=current,
        )
        assert cost["cost_authority"] is False, key


def test_targeted_wrapper_has_no_scheduler_or_listing_command() -> None:
    text = (ROOT / "scripts" / "scc_run_lvef_c3_autoclass_adjudication.sh").read_text()
    for prohibited in ("qsub", "objects.list", "gsutil", "gcloud storage cp", "alt=media"):
        assert prohibited not in text


def test_targeted_wrapper_uses_billing_value_only_as_environment() -> None:
    text = (ROOT / "scripts" / "scc_run_lvef_c3_autoclass_adjudication.sh").read_text()
    assert "lvef_c3_billing_environment.sh" in text
    assert "lvef_c3_run_with_gcp_authority_environment" in text
    assert "lvef_c3_quarantine_gcp_authority_environment" in text
    assert "--billing-project" not in text
    assert "echo $LVEF_C3_GCP_BILLING_PROJECT" not in text


def test_authority_and_token_checks_precede_billing_environment_consumption() -> None:
    environment_name = "LVEF_C3_GCP_BILLING_PROJECT"
    synthetic_project = "synthetic-project"
    observed = []

    def validator(path, *, expected_sha256, billing_project):
        assert path == Path("synthetic-receipt.json")
        assert expected_sha256 == "a" * 64
        assert billing_project == synthetic_project
        assert os.environ[environment_name] == synthetic_project
        observed.append(True)

    credential = gcp_authority.CredentialEvidence(
        access_token="synthetic-token",
        source_kind="PINNED_GCLOUD_EXECUTABLE",
        observed_account="synthetic@example.edu",
        configured_project=synthetic_project,
        source_sha256="b" * 64,
        gcloud_executable_pinned=True,
        authorized_user_adc_pinned=False,
    )
    environment = {
        environment_name: synthetic_project,
        "LVEF_C3_EXPECTED_GCP_ACCOUNT": "synthetic@example.edu",
        "LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME": "Synthetic Project",
    }
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
        adjudicator, "validate_restricted_receipt", side_effect=validator
    ), mock.patch.object(
        gcp_authority, "acquire_credentials", return_value=credential
    ):
        returned_project, returned_token = (
            adjudicator._validate_authority_and_acquire_token(
                authority_receipt=Path("synthetic-receipt.json"),
                expected_receipt_sha256="a" * 64,
                billing_project_env=environment_name,
                gcloud_bin="/synthetic/gcloud",
                timeout_seconds=7,
            )
        )
        assert environment_name not in os.environ

        assert returned_project == synthetic_project
        assert returned_token == "synthetic-token"
        assert observed == [True]


def test_failed_authority_validation_still_consumes_billing_environment() -> None:
    environment_name = "SYNTHETIC_REQUESTER_PAYS_PROJECT"
    os.environ[environment_name] = "synthetic-project"
    with mock.patch.object(
        adjudicator,
        "validate_restricted_receipt",
        side_effect=RuntimeError("synthetic failure"),
    ):
        try:
            adjudicator._validate_authority_and_acquire_token(
                authority_receipt=Path("synthetic-receipt.json"),
                expected_receipt_sha256="a" * 64,
                billing_project_env=environment_name,
                gcloud_bin="/synthetic/gcloud",
                timeout_seconds=7,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Synthetic authority failure was accepted")
    assert environment_name not in os.environ


def test_failed_token_acquisition_still_consumes_billing_environment() -> None:
    environment_name = "SYNTHETIC_REQUESTER_PAYS_PROJECT"
    os.environ[environment_name] = "synthetic-project"
    with mock.patch.object(adjudicator, "validate_restricted_receipt"), mock.patch.object(
        adjudicator,
        "acquire_access_token_for_preflight",
        side_effect=RuntimeError("synthetic failure"),
    ):
        try:
            adjudicator._validate_authority_and_acquire_token(
                authority_receipt=Path("synthetic-receipt.json"),
                expected_receipt_sha256="a" * 64,
                billing_project_env=environment_name,
                gcloud_bin="/synthetic/gcloud",
                timeout_seconds=7,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Synthetic token failure was accepted")
    assert environment_name not in os.environ


def test_adjudication_policy_preserves_no_go_boundary() -> None:
    policy = yaml.safe_load(
        (ROOT / "configs" / "lvef_c3_autoclass_adjudication.yaml").read_text()
    )
    assert policy["status"] == "SUPPLEMENTAL_METADATA_ONLY_NOT_C3_EXECUTION_AUTHORITY"
    assert policy["attempt"]["targeted_bucket_get_requests"] == 1
    assert policy["attempt"]["attempt_id"] == adjudicator.ATTEMPT_ID
    assert policy["attempt"]["prior_failed_attempt_targeted_bucket_get_requests"] == 0
    assert policy["attempt"]["prior_failed_attempt_preserved"] is True
    assert policy["attempt"]["object_list_requests"] == 0
    assert policy["source_inventory"]["autoclass_is_source_inventory_gate"] is False
    assert policy["provenance"]["supplemental_output_count"] == 6
    assert policy["provenance"]["manifest_bound_predecessor_output_count"] == 3
    assert policy["provenance"]["terminal_validation_predecessor_output_count"] == 5


def test_git_commit_and_sha256_authorities_are_not_interchangeable() -> None:
    assert adjudicator._require_git_commit("a" * 40, "BAD") == "a" * 40
    assert adjudicator._require_sha256("b" * 64, "BAD") == "b" * 64
    for value, validator in (
        ("a" * 64, adjudicator._require_git_commit),
        ("b" * 40, adjudicator._require_sha256),
    ):
        try:
            validator(value, "AUTHORITY_WIDTH_INVALID")
        except adjudicator.AdjudicationError as exc:
            assert str(exc) == "AUTHORITY_WIDTH_INVALID"
        else:
            raise AssertionError("Authority width confusion was accepted")


def test_resource_policy_migration_is_exact_once_and_fail_closed() -> None:
    runbook = (ROOT / "docs" / "lvef_multitask" / "scc_phase1ebc_commands.md").read_text()
    start = runbook.index("migrate_resource_policy_checksum() {")
    end = runbook.index('\nmigrate_resource_policy_checksum "$SESSION_ENV"', start)
    function_text = runbook[start:end]
    if sys.platform == "darwin":
        function_text = function_text.replace("stat -c '%a'", "stat -f '%Lp'")
    old = "a" * 64
    new = "b" * 64
    with tempfile.TemporaryDirectory() as directory:
        authority = Path(directory) / "authority.env"
        authority.write_text(f"EXPECTED_RESOURCE_POLICY_SHA256={old}\n")
        authority.chmod(0o600)
        command = (
            "set -euo pipefail\n"
            f"PRIOR_RESOURCE_POLICY_SHA256={old}\n"
            f"NEW_RESOURCE_POLICY_SHA256={new}\n"
            f"{function_text}\n"
            f"migrate_resource_policy_checksum {str(authority)!r}\n"
        )
        subprocess.run(["bash"], input=command, text=True, check=True)
        assert authority.read_text() == f"EXPECTED_RESOURCE_POLICY_SHA256={new}\n"
        authority.write_text(
            f"EXPECTED_RESOURCE_POLICY_SHA256={old}\nEXPECTED_RESOURCE_POLICY_SHA256={old}\n"
        )
        authority.chmod(0o600)
        failed = subprocess.run(["bash"], input=command, text=True)
        assert failed.returncode != 0
