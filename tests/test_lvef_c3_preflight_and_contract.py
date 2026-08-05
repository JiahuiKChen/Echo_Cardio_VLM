from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: all identifier-shaped values in this file are fixtures.

import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_lvef_c3_full_source as preflight
import plan_lvef_c3_resources as planner
import retire_lvef_c3_extracted_cache as retirement
import validate_lvef_c3_execution_contract as contract_validator
from lvef_multitask_analysis_modes import load_policy, validate_candidate_bytes


RESOURCE_POLICY = ROOT / "configs" / "lvef_c3_resource_policy.yaml"
CONTRACT = ROOT / "configs" / "lvef_c3_execution_contract.yaml"


def _migration_witness(
    *,
    migrated: int = 10_000_000_000,
    retained: int = 954_752_000,
    state: str = "PLANNED_NOT_EXECUTED",
) -> dict:
    return {
        "schema_version": 1,
        "status": "PASS_CLASSIFIED_MIGRATION_WITNESS",
        "classification_complete": True,
        "migration_state": state,
        "disaster_tier_inventory_bytes": migrated + retained,
        "classified_migration_bytes": migrated,
        "classified_retained_bytes": retained,
        "inventory_sha256": "a" * 64,
        "classification_sha256": "b" * 64,
    }


def _digest(byte: bytes, length: int) -> str:
    return base64.b64encode(byte * length).decode("ascii")


def _fixture(root: Path):
    selected = root / "selected.csv"
    selected_rows = [
        {"subject_id": "10000001", "study_id": "20000001", "n_dicoms": "2"},
        {"subject_id": "10000002", "study_id": "20000002", "n_dicoms": "1"},
    ]
    with selected.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected_rows[0]))
        writer.writeheader()
        writer.writerows(selected_rows)
    selected_sha = preflight.sha256_file(selected)

    source = root / "source.csv"
    source_rows = []
    for subject, study, suffix in (
        ("10000001", "20000001", "a"),
        ("10000001", "20000001", "b"),
        ("10000002", "20000002", "c"),
    ):
        relative = f"files/p10/p{subject}/s{study}/{suffix}.dcm"
        source_rows.append(
            {
                "release_id": preflight.RELEASE,
                "source_bucket": preflight.BUCKET,
                "component": "batch_000",
                "subject_id": subject,
                "study_id": study,
                "split": "train",
                "source_relative_path": relative,
                "gcs_uri": f"gs://{preflight.BUCKET}/{relative}",
                "expected_size_bytes": "",
                "expected_sha256": "",
                "source_object_key": hashlib.sha256(
                    f"{preflight.RELEASE}\0{relative}".encode()
                ).hexdigest(),
            }
        )
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]))
        writer.writeheader()
        writer.writerows(source_rows)
    source_sha = preflight.sha256_file(source)

    metadata = root / "metadata.jsonl"
    with metadata.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(source_rows, start=1):
            handle.write(
                json.dumps(
                    {
                        "name": row["source_relative_path"],
                        "size": str(index * 100),
                        "md5Hash": _digest(bytes([index]), 16),
                        "crc32c": _digest(bytes([index]), 4),
                        "generation": str(1000 + index),
                        "storageClass": "STANDARD",
                        "updated": "2026-08-01T00:00:00Z",
                    }
                )
                + "\n"
            )
    return selected, selected_sha, source, source_sha, source_rows, metadata


def test_offline_full_source_reconciliation_is_metadata_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path, selected_sha, source_path, source_sha, _, metadata_path = _fixture(root)
        selected = preflight.load_selected_studies(
            selected_path,
            batch_size=1,
            expected_sha256=selected_sha,
            expected_studies=2,
        )
        requests, source_stats = preflight.load_source_requests(
            source_path,
            selected,
            split_by_subject={"10000001": "train", "10000002": "train"},
            expected_sha256=source_sha,
            expected_requests=3,
            expected_raw_rows=3,
            expected_collapsed_rows=0,
        )
        metadata, provider = preflight.load_offline_listing(
            metadata_path, {row.relative_path for row in requests}
        )
        summary, batches, details, discrepancies = preflight.reconcile(
            selected, requests, metadata, provider, source_stats
        )
        assert summary["status"] == "PASS_METADATA_ONLY"
        assert summary["exact_source_bytes"] == 600
        assert summary["object_body_bytes_read"] == 0
        assert summary["media_requests"] == 0
        assert len(batches) == 2
        assert len(details) == 3
        assert discrepancies == []


def test_unexpected_object_in_a_selected_study_prefix_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path, selected_sha, source_path, source_sha, _, metadata_path = _fixture(root)
        selected = preflight.load_selected_studies(
            selected_path,
            batch_size=1,
            expected_sha256=selected_sha,
            expected_studies=2,
        )
        requests, source_stats = preflight.load_source_requests(
            source_path,
            selected,
            split_by_subject={"10000001": "train", "10000002": "train"},
            expected_sha256=source_sha,
            expected_requests=3,
            expected_raw_rows=3,
            expected_collapsed_rows=0,
        )
        with metadata_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "name": (
                            "files/" + "p10/" + "p" + "10000001/"
                            + "s" + "20000001/unexpected.dcm"
                        ),
                        "size": "444",
                        "md5Hash": _digest(b"u", 16),
                        "crc32c": _digest(b"v", 4),
                        "generation": "4444",
                        "storageClass": "STANDARD",
                        "updated": "2026-08-01T00:00:00Z",
                    }
                )
                + "\n"
            )
        metadata, provider = preflight.load_offline_listing(
            metadata_path, {row.relative_path for row in requests}
        )
        summary, batches, _, discrepancies = preflight.reconcile(
            selected, requests, metadata, provider, source_stats
        )
        assert summary["status"] == "FAIL_SOURCE_PREFLIGHT"
        assert summary["unexpected_selected_objects"] == 1
        assert summary["unexpected_selected_source_bytes"] == 444
        assert sum(int(row["n_unexpected_selected_objects"]) for row in batches) == 1
        assert any("UNEXPECTED_SELECTED_PREFIX_OBJECT" in row["reasons"] for row in discrepancies)


def test_json_api_provider_is_paginated_project_billed_and_never_media() -> None:
    relative = "files/" + "p10/" + "p" + "10000001/" + "s" + "20000001/a.dcm"
    payload = {
        "items": [
            {
                "name": relative,
                "size": "123",
                "md5Hash": _digest(b"a", 16),
                "crc32c": _digest(b"b", 4),
                "generation": "1234",
                "storageClass": "STANDARD",
                "updated": "2026-08-01T00:00:00Z",
            }
        ]
    }
    urls: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def fake_urlopen(request, timeout):
        del timeout
        urls.append(request.full_url)
        return Response(json.dumps(payload).encode())

    with tempfile.TemporaryDirectory() as directory, mock.patch.object(
        preflight, "urlopen", side_effect=fake_urlopen
    ), mock.patch.dict("os.environ", {"TEST_GCS_TOKEN": "secret-token"}):
        metadata, stats = preflight.list_json_api_metadata(
            {relative},
            billing_project="approved-billing-project",
            token_env="TEST_GCS_TOKEN",
            gcloud_bin="gcloud",
            page_size=1000,
            restricted_work_dir=Path(directory),
            resume=False,
            timeout_seconds=10,
        )
    assert set(metadata) == {relative}
    assert stats.pages == 1
    assert stats.media_requests == 0
    assert stats.body_bytes_read == 0
    assert len(urls) == 1
    assert "/storage/v1/b/" in urls[0]
    assert "userProject=approved-billing-project" in urls[0]
    assert "fields=" in urls[0]
    assert "alt=media" not in urls[0]
    assert "/download/storage/" not in urls[0]


def test_bucket_metadata_is_project_billed_and_location_bound() -> None:
    payload = {
        "name": preflight.BUCKET,
        "location": "US",
        "locationType": "multi-region",
        "storageClass": "STANDARD",
        "autoclass": {"enabled": False},
        "billing": {"requesterPays": True},
        "metageneration": "9",
        "updated": "2026-08-01T00:00:00Z",
    }
    urls: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def fake_urlopen(request, timeout):
        del timeout
        urls.append(request.full_url)
        return Response(json.dumps(payload).encode())

    with mock.patch.object(preflight, "urlopen", side_effect=fake_urlopen):
        metadata = preflight.get_bucket_metadata(
            billing_project="approved-billing-project",
            token_env="UNUSED",
            gcloud_bin="gcloud",
            timeout_seconds=10,
            access_token="synthetic-token",
        )
    assert metadata.location == "US"
    assert metadata.requester_pays is True
    assert metadata.autoclass_metadata_present is True
    assert metadata.autoclass_enabled is False
    assert metadata.body_bytes_read == 0
    assert len(urls) == 1
    assert "userProject=approved-billing-project" in urls[0]
    assert "alt=media" not in urls[0]


def test_live_listing_recovers_after_page_journal_before_state_crash() -> None:
    relative = "files/" + "p10/" + "p" + "10000001/" + "s" + "20000001/a.dcm"
    payload = {
        "items": [{
            "name": relative,
            "size": "123",
            "md5Hash": _digest(b"a", 16),
            "crc32c": _digest(b"b", 4),
            "generation": "1234",
            "storageClass": "STANDARD",
            "updated": "2026-08-01T00:00:00Z",
        }]
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def fake_urlopen(request, timeout):
        del request, timeout
        return Response(json.dumps(payload).encode())

    with tempfile.TemporaryDirectory() as directory, mock.patch.object(
        preflight, "urlopen", side_effect=fake_urlopen
    ):
        work = Path(directory)
        with mock.patch.object(
            preflight, "_atomic_json", side_effect=RuntimeError("synthetic crash")
        ):
            try:
                preflight.list_json_api_metadata(
                    {relative},
                    billing_project="approved-billing-project",
                    token_env="UNUSED",
                    gcloud_bin="gcloud",
                    page_size=1000,
                    restricted_work_dir=work,
                    resume=True,
                    timeout_seconds=10,
                    access_token="synthetic-token",
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("Synthetic crash was not injected")
        assert not (work / "c3_gcs_listing_state.restricted.json").exists()
        page_files = list(
            (work / "c3_gcs_selected_metadata_pages.restricted").glob("*.jsonl")
        )
        assert len(page_files) == 1
        metadata, stats = preflight.list_json_api_metadata(
            {relative},
            billing_project="approved-billing-project",
            token_env="UNUSED",
            gcloud_bin="gcloud",
            page_size=1000,
            restricted_work_dir=work,
            resume=True,
            timeout_seconds=10,
            access_token="synthetic-token",
        )
        assert set(metadata) == {relative}
        assert stats.pages == 1


def test_cost_estimate_uses_exact_bytes_and_explicit_rates() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    cost = preflight.calculate_cost(
        {
            "exact_source_bytes": 1024**3,
            "metadata_listing_pages": 1000,
            "requested_objects": 10000,
            "storage_class_bytes": {"STANDARD": 1024**3},
            "storage_class_counts": {"STANDARD": 10000},
            "authoritative_for_full_c3": True,
            "bucket_location_rate_match": True,
            "bucket_autoclass_metadata_present": True,
            "bucket_autoclass_enabled": False,
        },
        policy,
    )
    assert cost["one_pass_source_egress_cost_usd"] == "0.120000"
    assert cost["metadata_preflight_operation_cost_usd"] == "0.010000"
    assert cost["one_pass_body_get_operation_cost_usd"] == "0.004000"
    assert cost["one_pass_retrieval_cost_usd"] == "0.000000"
    assert cost["operation_pricing_authoritative"] is True
    assert cost["status"] == "PASS_RATE_EXPLICIT_PLANNING_ESTIMATE"
    assert cost["invoice_guarantee"] is False


def test_rolling_resource_plan_enforces_headroom() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    summary = {
        "status": "PASS_METADATA_ONLY",
        "metadata_provider": "GCS_JSON_API_OBJECTS_LIST",
        "authoritative_for_full_c3": True,
        "exact_source_bytes": 1_000_000_000_000,
        "requested_objects": 335984,
        "selected_studies": 4530,
    }
    batches = [
        {
            "production_batch": f"c3_batch_{index:03d}",
            "n_requested_objects": 335984 // 4 + (1 if index < 335984 % 4 else 0),
            "total_source_bytes": 250_000_000_000,
        }
        for index in range(4)
    ]
    plan = planner.build_plan(
        summary,
        batches,
        policy,
        current_usage_override=139_170_000_000,
        migration_witness=_migration_witness(),
    )
    assert plan["required_headroom_bytes"] == 200_000_000_000
    assert plan["strategies"][1]["components_bytes"]["selected_raw_dicoms"] == 1_000_000_000_000
    expected_largest_batch_objects = max(int(row["n_requested_objects"]) for row in batches)
    assert plan["strategies"][1]["components_bytes"]["active_extracted_cache"] == (
        expected_largest_batch_objects * 32 * 224 * 224 * 3
    )
    assert plan["current_usage_bytes"] == 139_170_000_000 + 10_000_000_000
    assert plan["shared_partial_retry_buffer_bytes"] == 250_000_000_000
    assert "partial_transfers" not in plan["strategies"][1]["components_bytes"]
    assert "retry_reserve" not in plan["strategies"][1]["components_bytes"]
    assert plan["provisional_50gb_retention_is_authority"] is False
    assert plan["raw_dicom_retention_assumed"] is True
    assert plan["scc_quota_cost_authority"] == policy["scc_quota_cost"]


def test_scc_quota_cost_policy_uses_published_annual_rate() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    quota_cost = planner.validate_scc_quota_cost_policy(policy)
    assert quota_cost["status"] == "APPROVED_OR_IMMINENT_PENDING_PQUOTA_ACTIVATION"
    assert quota_cost["storage_as_a_service_usd_per_tb_year"] == "22.00"
    assert quota_cost["minimum_term_months"] == 6
    assert quota_cost["estimated_one_tb_six_month_cost_usd"] == "11.00"
    assert quota_cost["estimated_one_tb_twelve_month_cost_usd"] == "22.00"
    assert quota_cost["billing_basis"] == "fiscal-year-prorated"
    assert quota_cost["administrative_exact_invoice"] == "pending-start-date-confirmation"


def test_scc_quota_cost_policy_rejects_invalid_six_month_arithmetic() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    policy["scc_quota_cost"]["estimated_one_tb_six_month_cost_usd"] = "10.00"
    try:
        planner.validate_scc_quota_cost_policy(policy)
    except planner.ResourcePlanError as exc:
        assert str(exc) == "SCC_QUOTA_COST_SIX_MONTH_ESTIMATE_INVALID"
    else:
        raise AssertionError("An invalid six-month storage estimate was accepted")


def test_resource_plan_requires_a_contemporaneous_usage_witness() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    summary = {
        "status": "PASS_METADATA_ONLY",
        "metadata_provider": "GCS_JSON_API_OBJECTS_LIST",
        "authoritative_for_full_c3": True,
        "exact_source_bytes": 1,
        "requested_objects": 1,
        "selected_studies": 1,
    }
    batches = [{
        "production_batch": "c3_batch_000",
        "n_requested_objects": 1,
        "total_source_bytes": 1,
    }]
    try:
        planner.build_plan(summary, batches, policy)
    except planner.ResourcePlanError as exc:
        assert str(exc) == "CURRENT_RESEARCH_USAGE_RUNTIME_WITNESS_REQUIRED"
    else:
        raise AssertionError("A stale policy usage estimate was accepted as runtime authority")


def test_resource_plan_requires_a_classified_migration_witness() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    summary = {
        "status": "PASS_METADATA_ONLY",
        "metadata_provider": "GCS_JSON_API_OBJECTS_LIST",
        "authoritative_for_full_c3": True,
        "exact_source_bytes": 1,
        "requested_objects": 1,
        "selected_studies": 1,
    }
    batches = [{
        "production_batch": "c3_batch_000",
        "n_requested_objects": 1,
        "total_source_bytes": 1,
    }]
    try:
        planner.build_plan(
            summary,
            batches,
            policy,
            current_usage_override=1,
        )
    except planner.ResourcePlanError as exc:
        assert str(exc) == "CLASSIFIED_MIGRATION_RUNTIME_WITNESS_REQUIRED"
    else:
        raise AssertionError("A hard-coded migration amount was accepted")


def test_nonstandard_or_autoclass_source_fails_operation_cost_authority() -> None:
    policy = yaml.safe_load(RESOURCE_POLICY.read_text())
    base = {
        "exact_source_bytes": 1024**3,
        "metadata_listing_pages": 1,
        "requested_objects": 1,
        "authoritative_for_full_c3": True,
        "bucket_location_rate_match": True,
        "bucket_autoclass_metadata_present": True,
        "bucket_autoclass_enabled": False,
        "storage_class_bytes": {"NEARLINE": 1024**3},
        "storage_class_counts": {"NEARLINE": 1},
    }
    nonstandard = preflight.calculate_cost(base, policy)
    assert nonstandard["operation_pricing_authoritative"] is False
    assert nonstandard["status"] == "NONAUTHORITATIVE_OR_INCOMPLETE_PLANNING_ESTIMATE"
    autoclass = preflight.calculate_cost(
        {
            **base,
            "storage_class_bytes": {"STANDARD": 1024**3},
            "storage_class_counts": {"STANDARD": 1},
            "bucket_autoclass_enabled": True,
        },
        policy,
    )
    assert autoclass["operation_pricing_authoritative"] is False


def test_committed_execution_contract_is_valid_but_no_go() -> None:
    result = contract_validator.validate_contract(CONTRACT)
    assert result["contract_valid"] is True
    assert result["execution_authorized"] is False
    assert result["status"] == "PASS_CONTRACT_NO_GO"
    assert "SEPARATE_OWNER_AUTHORIZATION_RECORD_ABSENT" in result["authorization_blockers"]
    assert result["raw_dicom_deletion_prohibited"] is True


def test_future_authorization_accepts_the_repositorys_40_hex_git_commit() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = yaml.safe_load(CONTRACT.read_text())
        payload["status"] = "AUTHORIZED_FULL_C3"
        payload["source"]["source_body_download_authorized"] = True
        payload["source"]["exact_source_bytes"] = 123456789
        payload["authority"]["frozen_selected_source_manifest_sha256"] = "a" * 64
        payload["authority"]["source_preflight_summary_sha256"] = "b" * 64
        payload["authority"]["resource_plan_sha256"] = "c" * 64
        payload["authority"]["command_config_manifest_sha256"] = "d" * 64
        payload["authority"]["command_commit"] = "e" * 40
        for key in contract_validator.AUTHORIZATION_ACTIONS:
            payload["authorization"][key] = True
        for key in payload["preauthorization_gates"]:
            payload["preauthorization_gates"][key] = True
        contract_path = root / "contract.yaml"
        contract_path.write_text(yaml.safe_dump(payload, sort_keys=False))
        contract_sha = contract_validator.sha256_file(contract_path)
        owner = {
            "status": "AUTHORIZED_FULL_C3",
            "contract_sha256": contract_sha,
            "selected_source_manifest_sha256": "a" * 64,
            "authorized_commit": "e" * 40,
            "owner_name_or_initials": "SYNTHETIC",
            "authorization_date": "2026-08-05",
        }
        owner_path = root / "owner.json"
        owner_path.write_text(json.dumps(owner))
        result = contract_validator.validate_contract(contract_path, owner_path)
        assert result["status"] == "PASS_AUTHORIZED"
        assert result["execution_authorized"] is True


def test_raw_dicom_retirement_is_unconditionally_prohibited() -> None:
    try:
        retirement.evaluate_retirement(
            contract_path=CONTRACT,
            owner_authorization_path=None,
            batch_id="c3_batch_000",
            cache_path=Path("/tmp/unused"),
            gate_report_path=Path("/tmp/unused.json"),
            target_kind="raw_dicom",
        )
    except retirement.RetirementError as exc:
        assert str(exc) == "RAW_DICOM_DELETION_PROHIBITED"
    else:
        raise AssertionError("Raw DICOM retirement was not prohibited")


def _write_cache_manifest(path: Path, cache: Path, batch_id: str, marker_name: str) -> dict:
    records = retirement._cache_tree_records(cache, marker_name)
    payload = {
        "schema_version": 1,
        "manifest_type": "c3_extracted_cache_manifest_v1",
        "batch_id": batch_id,
        "n_files": len(records),
        "total_bytes": sum(int(row["size_bytes"]) for row in records),
        "cache_inventory_sha256": retirement._records_inventory_sha256(records),
        "files": records,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _write_preservation_manifest(
    path: Path,
    *,
    batch_id: str,
    batch_manifest: Path,
    inside_cache_role: tuple[str, Path] | None = None,
) -> None:
    root = path.parent
    rows = []
    for role in sorted(retirement.REQUIRED_PRESERVED_ROLES):
        if role == "extracted_cache_manifest":
            artifact = batch_manifest
        elif inside_cache_role is not None and role == inside_cache_role[0]:
            artifact = inside_cache_role[1]
        else:
            artifact = root / "retained" / f"{role}.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(f"synthetic:{role}".encode())
        rows.append(
            {
                "role": role,
                "relative_path": artifact.relative_to(root).as_posix(),
                "size_bytes": artifact.stat().st_size,
                "sha256": retirement._sha256_file(artifact),
            }
        )
    payload = {
        "schema_version": 1,
        "manifest_type": "c3_batch_preservation_manifest_v1",
        "batch_id": batch_id,
        "status": "PASS",
        "files": rows,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def test_cache_manifest_exact_set_and_preservation_scope_are_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        batch_id = "c3_batch_000"
        cache = root / "cache" / batch_id
        cache.mkdir(parents=True)
        (cache / "one.npz").write_bytes(b"one")
        manifest = root / "cache_manifest.json"
        payload = _write_cache_manifest(manifest, cache, batch_id, ".marker.json")
        assert retirement._validate_cache_manifest(
            manifest,
            batch_id=batch_id,
            cache_path=cache,
            marker_name=".marker.json",
        ) == payload["cache_inventory_sha256"]

        (cache / "unlisted.npz").write_bytes(b"unlisted")
        try:
            retirement._validate_cache_manifest(
                manifest,
                batch_id=batch_id,
                cache_path=cache,
                marker_name=".marker.json",
            )
        except retirement.RetirementError:
            pass
        else:
            raise AssertionError("Unlisted cache file passed exact-manifest validation")

        (cache / "unlisted.npz").unlink()
        preserved = root / "preservation.json"
        _write_preservation_manifest(
            preserved,
            batch_id=batch_id,
            batch_manifest=manifest,
            inside_cache_role=("download_manifest", cache / "one.npz"),
        )
        try:
            retirement._validate_preservation_manifest(
                preserved,
                batch_id=batch_id,
                deletion_scope=cache,
            )
        except retirement.RetirementError as exc:
            assert str(exc) == "PRESERVATION_FILE_INSIDE_DELETION_SCOPE"
        else:
            raise AssertionError("Preserved authority inside deletion scope was accepted")


def test_authorized_cache_retirement_dry_run_reconciles_every_binding() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        batch_id = "c3_batch_000"
        cache_root = root / "cache"
        cache = cache_root / batch_id
        raw_root = root / "raw"
        cache.mkdir(parents=True)
        raw_root.mkdir()
        (cache / "one.npz").write_bytes(b"one")
        marker_name = ".c3_extracted_cache_marker.json"
        batch_manifest = root / "cache_manifest.json"
        cache_payload = _write_cache_manifest(batch_manifest, cache, batch_id, marker_name)
        preservation = root / "preservation.json"
        _write_preservation_manifest(
            preservation,
            batch_id=batch_id,
            batch_manifest=batch_manifest,
        )
        required_gates = [
            "source", "download", "dicom_header", "extraction", "embedding",
            "pooling", "checksum", "preservation", "safety",
        ]
        contract_payload = {
            "storage": {
                "extracted_cache_root": str(cache_root),
                "raw_dicom_root": str(raw_root),
            },
            "cache_retirement": {
                "required_batch_gates": required_gates,
                "require_marker_file": marker_name,
                "execute_confirmation_environment_variable": "SYNTHETIC_CONFIRMATION",
            },
            "authority": {"frozen_selected_source_manifest_sha256": "a" * 64},
            "authorization": {"cache_retirement": True},
        }
        contract = root / "contract.yaml"
        contract.write_text(yaml.safe_dump(contract_payload, sort_keys=False))
        contract_sha = retirement._sha256_file(contract)
        bindings = {
            "contract_sha256": contract_sha,
            "selected_source_manifest_sha256": "a" * 64,
            "batch_manifest_sha256": retirement._sha256_file(batch_manifest),
            "preservation_manifest_sha256": retirement._sha256_file(preservation),
            "cache_inventory_sha256": cache_payload["cache_inventory_sha256"],
        }
        (cache / marker_name).write_text(json.dumps({"batch_id": batch_id, **bindings}))
        gate = root / "gate.json"
        gate.write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "gates": {name: "PASS" for name in required_gates},
                    "raw_dicoms_retained": True,
                    **bindings,
                }
            )
        )
        with mock.patch.object(
            retirement,
            "validate_contract",
            return_value={"contract_sha256": contract_sha, "execution_authorized": True},
        ):
            result = retirement.evaluate_retirement(
                contract_path=contract,
                owner_authorization_path=None,
                batch_id=batch_id,
                cache_path=cache,
                gate_report_path=gate,
                target_kind="extracted_cache",
                batch_manifest_path=batch_manifest,
                preservation_manifest_path=preservation,
            )
        assert result["status"] == "PASS_RETIREMENT_GATES"
        assert cache.is_dir()


def test_full_runner_and_submit_gate_are_fail_closed_and_bash_valid() -> None:
    runner = ROOT / "scripts" / "scc_run_lvef_c3_full.sh"
    batch_runner = ROOT / "scripts" / "scc_run_lvef_c3_batch.sh"
    finalizer = ROOT / "scripts" / "scc_finalize_lvef_c3_full.sh"
    submit = ROOT / "scripts" / "scc_submit_lvef_c3_full.sh"
    resource = ROOT / "scripts" / "scc_run_lvef_c3_resource_preflight.sh"
    for path in (runner, batch_runner, finalizer, submit, resource):
        subprocess.run(["bash", "-n", str(path)], check=True)
    completed = subprocess.run(["bash", str(runner)], capture_output=True, text=True)
    assert completed.returncode == 78
    assert "EXECUTION_REFUSED" in completed.stderr
    preflight_source = (ROOT / "scripts" / "preflight_lvef_c3_full_source.py").read_text()
    assert "objects.list" in preflight_source
    assert 'method="GET"' in preflight_source
    assert "gsutil cp" not in preflight_source
    assert "gcloud storage cp" not in preflight_source
    planned = subprocess.run(
        [
            "bash", str(submit), str(CONTRACT), "-",
            "/restricted/projectnb/synthetic.env", "--print-only",
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 0
    assert "NO_GO_PRODUCTION_BATCH_RUNNER_AND_FINALIZER_NOT_IMPLEMENTED" in planned.stdout
    assert "-t 1-19 -tc 1" in planned.stdout
    assert "-hold_jid" in planned.stdout
    assert " -V " not in planned.stdout


def test_contract_validator_rejects_execution_field_mutations() -> None:
    mutations = (
        (("download", "retries"), -1),
        (("storage", "partial_files", "atomic_rename_after_verified_size_and_md5"), False),
        (("dicom_and_extraction", "target_frames"), 16),
        (("embedding", "random_seed"), 1),
        (("pooling", "stable_clip_key_order_required"), False),
        (("environment", "python_executable_sha256"), "f" * 64),
        (("scheduler", "array_tasks"), 18),
        (("scheduler", "ambient_environment_export_permitted"), True),
        (("scheduler", "production_concurrency_batches"), 2),
    )
    for path, value in mutations:
        with tempfile.TemporaryDirectory() as directory:
            payload = yaml.safe_load(CONTRACT.read_text())
            cursor = payload
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = value
            candidate = Path(directory) / "contract.yaml"
            candidate.write_text(yaml.safe_dump(payload, sort_keys=False))
            try:
                contract_validator.validate_contract(candidate)
            except contract_validator.ContractError:
                continue
            raise AssertionError(f"Execution-field mutation was accepted: {path}")


def test_resource_policy_and_contract_yaml_are_parseable() -> None:
    for path in (RESOURCE_POLICY, CONTRACT):
        payload = yaml.safe_load(path.read_text())
        assert payload["schema_version"] == 1


def test_generated_aggregate_c3_artifacts_match_the_safe_export_profiles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected, selected_sha, source, source_sha, _, metadata = _fixture(root)
        split_map = root / "split.csv"
        split_map.write_text(
            "subject_id,split\n10000001,train\n10000002,train\n",
            encoding="utf-8",
        )
        split_sha = preflight.sha256_file(split_map)
        restricted = root / "restricted"
        aggregate = root / "aggregate"
        args = SimpleNamespace(
            source_manifest=source,
            selected_studies=selected,
            split_map=split_map,
            expected_source_manifest_sha256=source_sha,
            expected_selected_manifest_sha256=selected_sha,
            expected_split_manifest_sha256=split_sha,
            resource_policy=RESOURCE_POLICY,
            restricted_output_dir=restricted,
            aggregate_output_dir=aggregate,
            metadata_json=metadata,
            billing_project_env="UNUSED",
            access_token_env="UNUSED",
            gcloud_bin="gcloud",
            page_size=1000,
            timeout_seconds=10,
            resume=False,
            expected_selected_studies=2,
            expected_split_counts={"train": 2, "val": 0, "test": 0},
            expected_source_requests=3,
            expected_raw_request_rows=3,
            expected_collapsed_rows=0,
            safe_export_policy=ROOT
            / "configs"
            / "lvef_multitask_safe_export_policy.yaml",
        )
        with mock.patch.object(
            preflight,
            "bind_approved_restricted_path",
            side_effect=lambda path, **_: Path(path).resolve(),
        ):
            summary = preflight.execute(args)
        assert summary["status"] == "PASS_OFFLINE_REPLAY_NONAUTHORITATIVE"
        assert summary["authoritative_for_full_c3"] is False
        policy, _ = load_policy(ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml")
        profiles = {
            "c3_full_source_preflight.summary.json": "c3_full_source_preflight_summary_json",
            "c3_full_source_preflight_by_batch.csv": "c3_full_source_preflight_by_batch_csv",
            "c3_full_source_cost_estimate.json": "c3_full_source_cost_estimate_json",
            "c3_full_source_preflight_safety_gate.json": "aggregate_safety_gate_json",
        }
        for filename, profile in profiles.items():
            candidate = aggregate / filename
            result = validate_candidate_bytes(
                candidate.read_bytes(),
                filename=filename,
                profile_name=profile,
                policy=policy,
            )
            assert result["status"] == "PASS"

        batches = planner._load_batches(aggregate / "c3_full_source_preflight_by_batch.csv")
        planning_summary = dict(summary)
        planning_summary["status"] = "PASS_METADATA_ONLY"
        planning_summary["metadata_provider"] = "GCS_JSON_API_OBJECTS_LIST"
        planning_summary["authoritative_for_full_c3"] = True
        resource_plan = planner.build_plan(
            planning_summary,
            batches,
            yaml.safe_load(RESOURCE_POLICY.read_text()),
            current_usage_override=139_170_000_000,
            migration_witness=_migration_witness(),
        )
        resource_path = aggregate / "c3_full_resource_plan.json"
        resource_path.write_text(json.dumps(resource_plan, sort_keys=True) + "\n")
        result = validate_candidate_bytes(
            resource_path.read_bytes(),
            filename=resource_path.name,
            profile_name="c3_storage_projection_json",
            policy=policy,
        )
        assert result["status"] == "PASS"
