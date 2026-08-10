from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: all identifiers, objects, hashes, and paths are fixtures.

import base64
import ast
import copy
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_orchestration_core as core
import lvef_multitask_analysis_modes as analysis_modes


CONTRACT_PATH = ROOT / "configs" / "lvef_c3_orchestration_v2.yaml"


def _requirements() -> core.PlanRequirements:
    return core.PlanRequirements(
        release="mimic-iv-echo/1.0",
        selected_studies=5,
        selected_subjects=5,
        normalized_source_objects=7,
        selected_source_bytes=sum(len(f"synthetic-{index}".encode()) for index in range(7)),
        batch_count=3,
        studies_per_full_batch=2,
        final_batch_studies=1,
        contract_id="synthetic_c3_v2",
    )


def _authority() -> dict[str, str]:
    return {
        "git_commit": "1" * 40,
        "orchestration_contract_sha256": "2" * 64,
        "selected_manifest_sha256": "3" * 64,
        "selected_source_manifest_sha256": "4" * 64,
        "source_metadata_sha256": "5" * 64,
        "split_map_sha256": "6" * 64,
        "checkpoint_sha256": "7" * 64,
        "environment_receipt_sha256": "8" * 64,
        "state_machine_schema_sha256": "9" * 64,
        "resume_ledger_schema_sha256": "a" * 64,
        "gcloud_resolution_receipt_sha256": "b" * 64,
        "gcloud_executable_sha256": "c" * 64,
        "crc32c_python_executable_sha256": "d" * 64,
        "crc32c_worker_sha256": "e" * 64,
        "crc32c_distribution_sha256": "f" * 64,
    }


def _fixture_rows():
    selected = []
    splits = []
    source = []
    metadata = []
    object_content: dict[str, bytes] = {}
    object_index = 0
    for index in range(5):
        subject = str(10_000_001 + index)
        study = str(20_000_001 + index)
        split = "train" if index < 3 else ("val" if index == 3 else "test")
        selected.append({"subject_id": subject, "study_id": study})
        splits.append({"subject_id": subject, "split": split})
        n_objects = 2 if index < 2 else 1
        for within in range(n_objects):
            relative = f"files/p10/p{subject}/s{study}/synthetic_{within}.dcm"
            key = hashlib.sha256(
                f"mimic-iv-echo/1.0\0{relative}".encode()
            ).hexdigest()
            content = f"synthetic-{object_index}".encode()
            source.append(
                {
                    "release_id": "mimic-iv-echo/1.0",
                    "subject_id": subject,
                    "study_id": study,
                    "split": split,
                    "source_relative_path": relative,
                    "source_object_key": key,
                }
            )
            metadata.append(
                {
                    "release_id": "mimic-iv-echo/1.0",
                    "subject_id": subject,
                    "study_id": study,
                    "split": split,
                    "source_relative_path": relative,
                    "source_object_key": key,
                    "production_batch": f"c3_batch_{index // 2:03d}",
                    "remote_size_bytes": len(content),
                    "remote_md5_base64": base64.b64encode(
                        hashlib.md5(content).digest()
                    ).decode(),
                    "remote_crc32c_base64": core._crc32c_base64(content),
                    "remote_generation": str(1000 + object_index),
                    "preflight_status": "PASS",
                    "discrepancy_reasons": [],
                }
            )
            object_content[key] = content
            object_index += 1
    return selected, splits, source, metadata, object_content


def _plan():
    selected, splits, source, metadata, content = _fixture_rows()
    enriched = core.reconcile_selected_source_metadata(
        source, metadata, release="mimic-iv-echo/1.0"
    )
    plan = core.build_immutable_batch_plan(
        list(reversed(selected)),
        list(reversed(enriched)),
        list(reversed(splits)),
        requirements=_requirements(),
        authority=_authority(),
    )
    return plan, content


def _runtime_authority(plan) -> dict[str, str]:
    return {**_authority(), "batch_plan_sha256": core.canonical_json_sha256(plan)}


def _ledger_in_download_state(plan):
    ledger = core.initialize_resume_ledger(
        plan,
        requirements=_requirements(),
        attempt_id="lvef_c3_synthetic_attempt",
        authority=_runtime_authority(plan),
    )
    receipt = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": ledger["attempt_id"],
        "batch_id": "c3_batch_000",
        "from_state": "PLANNED",
        "to_state": "DOWNLOAD_IN_PROGRESS",
        "status": "PASS",
        "authority": ledger["authority"],
        "input_receipt_sha256": [ledger["authority"]["batch_plan_sha256"]],
        "output_manifest_sha256": "9" * 64,
    }
    return core.apply_transition(ledger, receipt)


def _batch_planned_ledger(plan):
    return core.initialize_resume_ledger(
        plan,
        requirements=_requirements(),
        attempt_id="lvef_c3_synthetic_attempt",
        authority=_runtime_authority(plan),
        batch_ids=["c3_batch_000"],
    )


def _body_authorization(plan, ledger, *, now):
    n_objects = plan["batches"][0]["n_objects"]
    return {
        "schema_version": 2,
        "receipt_type": "lvef_c3_body_transfer_authorization_v2",
        "status": "AUTHORIZED_C3_DICOM_BODY_TRANSFER",
        "attempt_id": ledger["attempt_id"],
        "scope": "FIRST_BATCH_ONLY",
        "batch_ids": ["c3_batch_000"],
        "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
        "batch_plan_sha256": ledger["authority"]["batch_plan_sha256"],
        "launch_authority_sha256": "f" * 64,
        "maximum_requests": n_objects * 5,
        "issued_at_utc": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "owner_authorization_recorded": True,
        "body_download_only": True,
        "scientific_actions_authorized": False,
    }


def test_production_orchestration_contract_is_exact_and_unauthorized() -> None:
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    assert contract["cohort"]["selected_studies"] == 4530
    assert contract["cohort"]["normalized_source_objects"] == 335984
    assert contract["cohort"]["selected_source_bytes"] == 1216569133322
    assert contract["cohort"]["selected_source_manifest_sha256"] == (
        "35071385477515e40e6cc1503561b7c7aa434127435ce91aa3ae8b0a009188ff"
    )
    assert contract["batching"]["batch_count"] == 19
    assert contract["downloader"]["live_transport_present"] is True
    assert contract["downloader"]["live_requests_authorized"] is False
    assert set(contract["state_machine"]["states"]) == set(core.STATES)
    assert all(value is False for value in contract["authorization"].values())
    assert contract["cache_retirement"]["raw_dicom_deletion_owner_authorizable"] is False
    assert (
        contract["cache_retirement"]["extracted_cache_retirement_default_authorized"]
        is False
    )
    assert (
        contract["cache_retirement"]["extracted_cache_retirement_owner_authorizable"]
        is True
    )


def test_selected_source_and_metadata_reconcile_one_to_one() -> None:
    selected, splits, source, metadata, _ = _fixture_rows()
    enriched = core.reconcile_selected_source_metadata(
        source, metadata, release="mimic-iv-echo/1.0"
    )
    assert len(enriched) == 7
    plan = core.build_immutable_batch_plan(
        selected,
        enriched,
        splits,
        requirements=_requirements(),
        authority=_authority(),
    )
    assert core.validate_batch_plan(plan, requirements=_requirements()) == (
        core.canonical_json_sha256(plan)
    )


def test_deterministic_batch_plan_is_order_invariant_and_aggregate_safe() -> None:
    first, _ = _plan()
    selected, splits, source, metadata, _ = _fixture_rows()
    enriched = core.reconcile_selected_source_metadata(
        list(reversed(source)), list(reversed(metadata)), release="mimic-iv-echo/1.0"
    )
    second = core.build_immutable_batch_plan(
        selected,
        enriched,
        splits,
        requirements=_requirements(),
        authority=_authority(),
    )
    assert first == second
    assert [row["n_studies"] for row in first["batches"]] == [2, 2, 1]
    aggregate = core.aggregate_batch_plan(first, requirements=_requirements())
    assert aggregate["status"] == "PASS_OFFLINE_IMMUTABLE_BATCH_PLAN"
    assert len(aggregate["batches"]) == 3
    assert aggregate["contains_identifiers"] is False
    assert aggregate["contains_source_locators"] is False
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(aggregate, sort_keys=True) + "\n").encode("utf-8"),
        filename="lvef_c3_batch_plan.summary.json",
        profile_name="phase1ee_batch_plan_summary_json",
        policy=policy,
    )
    assert result["status"] == "PASS"


def test_plan_rejects_missing_new_duplicate_or_changed_batch_objects() -> None:
    selected, splits, source, metadata, _ = _fixture_rows()
    for mutation, code in (
        ("missing", "SOURCE_OBJECT_COUNT_MISMATCH"),
        ("duplicate", "DUPLICATE_PHYSICAL_SOURCE_KEY"),
    ):
        enriched = core.reconcile_selected_source_metadata(
            source, metadata, release="mimic-iv-echo/1.0"
        )
        changed = enriched[:-1] if mutation == "missing" else enriched + [dict(enriched[0])]
        try:
            core.build_immutable_batch_plan(
                selected,
                changed,
                splits,
                requirements=_requirements(),
                authority=_authority(),
            )
        except core.OrchestrationError as exc:
            assert str(exc) == code
        else:
            raise AssertionError(f"{mutation} source object did not fail")
    plan, _ = _plan()
    changed_plan = copy.deepcopy(plan)
    changed_plan["batches"][0]["objects"].append(
        copy.deepcopy(changed_plan["batches"][1]["objects"][0])
    )
    try:
        core.validate_batch_plan(changed_plan, requirements=_requirements())
    except core.OrchestrationError as exc:
        assert str(exc) in {
            "BATCH_SOURCE_MEMBERSHIP_HASH_MISMATCH",
            "BATCH_OBJECT_OWNERSHIP_MISMATCH",
        }
    else:
        raise AssertionError("Altered plan did not fail")


def test_metadata_missing_conflicting_or_nonpass_fails_closed() -> None:
    _, _, source, metadata, _ = _fixture_rows()
    for changed, expected in (
        (metadata[:-1], "SOURCE_METADATA_NOT_ONE_TO_ONE"),
        ([*metadata, dict(metadata[0])], "DUPLICATE_SOURCE_METADATA_AUTHORITY"),
    ):
        try:
            core.reconcile_selected_source_metadata(
                source, changed, release="mimic-iv-echo/1.0"
            )
        except core.OrchestrationError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("Bad metadata authority did not fail")
    failed = copy.deepcopy(metadata)
    failed[0]["preflight_status"] = "FAIL"
    try:
        core.reconcile_selected_source_metadata(source, failed, release="mimic-iv-echo/1.0")
    except core.OrchestrationError as exc:
        assert str(exc) == "SOURCE_METADATA_PREFLIGHT_NOT_PASS"
    else:
        raise AssertionError("Nonpass metadata did not fail")


def test_state_machine_requires_exact_sequence_receipt_chain_and_authority() -> None:
    plan, _ = _plan()
    ledger = _ledger_in_download_state(plan)
    last = ledger["batches"]["c3_batch_000"]["events"][-1]["receipt_sha256"]
    receipt = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": ledger["attempt_id"],
        "batch_id": "c3_batch_000",
        "from_state": "DOWNLOAD_IN_PROGRESS",
        "to_state": "DOWNLOAD_VERIFIED",
        "status": "PASS",
        "authority": ledger["authority"],
        "input_receipt_sha256": [last],
        "output_manifest_sha256": "a" * 64,
    }
    advanced = core.apply_transition(ledger, receipt)
    assert advanced["batches"]["c3_batch_000"]["state"] == "DOWNLOAD_VERIFIED"
    skipped = copy.deepcopy(receipt)
    skipped["to_state"] = "EXTRACTION_COMPLETE"
    try:
        core.apply_transition(ledger, skipped)
    except core.OrchestrationError as exc:
        assert str(exc) == "STATE_SKIP_OR_REGRESSION_PROHIBITED"
    else:
        raise AssertionError("State skip did not fail")
    broken = copy.deepcopy(receipt)
    broken["input_receipt_sha256"] = ["b" * 64]
    try:
        core.apply_transition(ledger, broken)
    except core.OrchestrationError as exc:
        assert str(exc) == "TRANSITION_RECEIPT_CHAIN_BROKEN"
    else:
        raise AssertionError("Broken receipt chain did not fail")
    changed_authority = dict(ledger["authority"])
    changed_authority["environment_receipt_sha256"] = "f" * 64
    try:
        core.validate_resume_authority(ledger, expected_authority=changed_authority)
    except core.OrchestrationError as exc:
        assert str(exc) == "RESUME_AUTHORITY_MISMATCH_NEW_ATTEMPT_REQUIRED"
    else:
        raise AssertionError("Changed resume authority did not fail")


def test_ledger_must_match_independently_derived_current_runtime_authority() -> None:
    plan, _ = _plan()
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    plan = copy.deepcopy(plan)
    plan["authority"] = {
        "git_commit": "1" * 40,
        "orchestration_contract_sha256": core.sha256_file(CONTRACT_PATH),
        "selected_manifest_sha256": contract["cohort"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": contract["cohort"][
            "selected_source_manifest_sha256"
        ],
        "source_metadata_sha256": "5" * 64,
        "split_map_sha256": contract["cohort"]["split_map_sha256"],
        "checkpoint_sha256": contract["embedding"]["checkpoint_sha256"],
        "environment_receipt_sha256": "8" * 64,
        "state_machine_schema_sha256": contract["authority"][
            "state_machine_schema_sha256"
        ],
        "resume_ledger_schema_sha256": contract["authority"][
            "resume_ledger_schema_sha256"
        ],
        "gcloud_resolution_receipt_sha256": "b" * 64,
        "gcloud_executable_sha256": "c" * 64,
        "crc32c_python_executable_sha256": "d" * 64,
        "crc32c_worker_sha256": "e" * 64,
        "crc32c_distribution_sha256": "f" * 64,
    }
    runtime = {**plan["authority"], "batch_plan_sha256": core.canonical_json_sha256(plan)}
    ledger = core.initialize_resume_ledger(
        plan,
        requirements=_requirements(),
        attempt_id="lvef_c3_synthetic_attempt",
        authority=runtime,
        batch_ids=["c3_batch_000"],
    )
    expected = core.validate_ledger_against_current_runtime(
        ledger,
        plan=plan,
        requirements=_requirements(),
        contract=contract,
        contract_path=CONTRACT_PATH,
        governing_commit="1" * 40,
        environment_receipt_sha256="8" * 64,
        batch_id="c3_batch_000",
    )
    assert expected == ledger["authority"]
    stale = copy.deepcopy(ledger)
    stale["authority"]["environment_receipt_sha256"] = "f" * 64
    try:
        core.validate_ledger_against_current_runtime(
            stale,
            plan=plan,
            requirements=_requirements(),
            contract=contract,
            contract_path=CONTRACT_PATH,
            governing_commit="1" * 40,
            environment_receipt_sha256="8" * 64,
            batch_id="c3_batch_000",
        )
    except core.OrchestrationError as exc:
        assert str(exc) == "RESUME_AUTHORITY_MISMATCH_NEW_ATTEMPT_REQUIRED"
    else:
        raise AssertionError("Stale self-attested ledger authority was accepted")

    bool_attempt = copy.deepcopy(ledger)
    first_key = next(
        iter(bool_attempt["batches"]["c3_batch_000"]["download_attempts"])
    )
    bool_attempt["batches"]["c3_batch_000"]["download_attempts"][first_key] = False
    try:
        core.validate_resume_authority(
            bool_attempt, expected_authority=bool_attempt["authority"]
        )
    except core.OrchestrationError as exc:
        assert str(exc) == "LEDGER_BATCH_CONTENT_INVALID"
    else:
        raise AssertionError("Boolean download-attempt counter was accepted")


def test_download_request_budget_retry_classes_and_private_billing() -> None:
    plan, _ = _plan()
    ledger = _ledger_in_download_state(plan)
    key = plan["batches"][0]["objects"][0]["source_object_key"]
    ledger = core.register_download_attempt(
        ledger, batch_id="c3_batch_000", source_object_key=key, maximum_attempts=2
    )
    ledger = core.register_download_attempt(
        ledger, batch_id="c3_batch_000", source_object_key=key, maximum_attempts=2
    )
    try:
        core.register_download_attempt(
            ledger, batch_id="c3_batch_000", source_object_key=key, maximum_attempts=2
        )
    except core.OrchestrationError as exc:
        assert str(exc) == "DOWNLOAD_REQUEST_BUDGET_EXHAUSTED"
    else:
        raise AssertionError("Request budget overrun did not fail")
    assert core.classify_download_failure(
        "TIMEOUT", attempts_used=1, maximum_attempts=2
    ) == "FAILED_RETRYABLE"
    assert core.classify_download_failure(
        "TIMEOUT", attempts_used=2, maximum_attempts=2
    ) == "FAILED_NONRETRYABLE"
    assert core.classify_download_failure(
        "CHECKSUM_MISMATCH", attempts_used=1, maximum_attempts=2
    ) == "FAILED_NONRETRYABLE"
    assert [
        core.retry_backoff_seconds(
            attempt, initial_seconds=2, maximum_seconds=30
        )
        for attempt in range(1, 6)
    ] == [2, 4, 8, 16, 30]
    safe = core.validate_private_billing_environment(
        "PRIVATE_PROJECT", argv=("download-batch",), environ={"PRIVATE_PROJECT": "secret-project"}
    )
    assert safe["value_returned"] is False
    try:
        core.validate_private_billing_environment(
            "PRIVATE_PROJECT",
            argv=("--billing-project=secret-project",),
            environ={"PRIVATE_PROJECT": "secret-project"},
        )
    except core.OrchestrationError as exc:
        assert str(exc) == "PRIVATE_BILLING_PROJECT_EXPOSED_IN_ARGV"
    else:
        raise AssertionError("Private billing value in argv did not fail")


def test_partial_verification_stale_short_zero_and_atomic_no_clobber() -> None:
    plan, content = _plan()
    expectation = core.expectation_from_plan_object(plan["batches"][0]["objects"][0])
    payload = content[expectation.source_object_key]
    receipt = {
        "schema_version": 2,
        "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
        "source_object_key": expectation.source_object_key,
        "size_bytes": expectation.size_bytes,
        "generation": expectation.generation,
        "md5_base64": expectation.md5_base64,
        "crc32c_base64": expectation.crc32c_base64,
        "media_request_count": 1,
        "object_body_bytes_read": len(payload),
        "resume_offset_bytes": 0,
        "response_body_bytes_read": len(payload),
        "final_partial_size_bytes": len(payload),
        "content_range_validated": False,
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        partial = root / core.planned_partial_name(expectation, "lvef_c3_synthetic_attempt")
        partial.write_bytes(payload)
        verified = core.verify_downloaded_partial(
            expectation,
            partial_path=partial,
            transfer_receipt=receipt,
            attempt_id="lvef_c3_synthetic_attempt",
        )
        final = root / core.planned_final_name(expectation)
        core.atomic_finalize_verified_file(
            partial_path=partial, final_path=final, verification=verified
        )
        assert final.read_bytes() == payload
        replacement = root / core.planned_partial_name(expectation, "lvef_c3_synthetic_attempt")
        replacement.write_bytes(payload)
        try:
            core.atomic_finalize_verified_file(
                partial_path=replacement, final_path=final, verification=verified
            )
        except core.OrchestrationError as exc:
            assert str(exc) == "FINAL_DOWNLOAD_ALREADY_EXISTS_NO_CLOBBER"
        else:
            raise AssertionError("Atomic finalization clobbered existing output")
    for bad_payload, bad_name, expected in (
        (b"", core.planned_partial_name(expectation, "lvef_c3_synthetic_attempt"), "ZERO_BYTE_DOWNLOAD"),
        (payload[:-1], core.planned_partial_name(expectation, "lvef_c3_synthetic_attempt"), "DOWNLOAD_SIZE_MISMATCH"),
        (payload, f"{expectation.source_object_key}.lvef_c3_old_attempt.partial", "STALE_OR_FOREIGN_DOWNLOAD_PARTIAL"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / bad_name
            path.write_bytes(bad_payload)
            bad_receipt = dict(receipt)
            bad_receipt["object_body_bytes_read"] = len(bad_payload)
            bad_receipt["response_body_bytes_read"] = len(bad_payload)
            bad_receipt["final_partial_size_bytes"] = len(bad_payload)
            try:
                core.verify_downloaded_partial(
                    expectation,
                    partial_path=path,
                    transfer_receipt=bad_receipt,
                    attempt_id="lvef_c3_synthetic_attempt",
                )
            except core.OrchestrationError as exc:
                assert str(exc) == expected
            else:
                raise AssertionError(f"{expected} was not detected")


def test_streaming_digest_is_multiblock_equivalent_and_bounded() -> None:
    payload = (b"0123456789abcdef" * 2_000) + b"tail"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "multiblock.bin"
        path.write_bytes(payload)
        observed = core.stream_regular_file_digests(path, chunk_size=1_024)
    assert observed["size_bytes"] == len(payload)
    assert observed["sha256"] == hashlib.sha256(payload).hexdigest()
    assert observed["md5_base64"] == base64.b64encode(
        hashlib.md5(payload).digest()
    ).decode()
    assert observed["crc32c_base64"] == core._crc32c_base64(payload)
    assert observed["chunk_size_bytes"] == 1_024
    assert observed["chunk_size_bytes"] < len(payload)
    assert observed["backend"] == "google_crc32c_c"


def test_resume_content_range_requires_exact_start_end_and_total() -> None:
    core.validate_resume_content_range("bytes 10-99/100", offset=10, total_size=100)
    for value in (
        "bytes 9-99/100",
        "bytes 10-98/100",
        "bytes 10-99/101",
        "bytes */100",
        None,
    ):
        try:
            core.validate_resume_content_range(value, offset=10, total_size=100)
        except core.OrchestrationError as exc:
            assert str(exc) in {
                "CONTENT_RANGE_HEADER_INVALID",
                "CONTENT_RANGE_IDENTITY_MISMATCH",
            }
        else:
            raise AssertionError("Wrong resume Content-Range was accepted")


def test_gcloud_adc_provider_is_private_receipt_and_binary_bound() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cloudsdk = root / "private-cloudsdk"
        cloudsdk.mkdir(mode=0o700)
        adc = cloudsdk / "application_default_credentials.json"
        adc.write_text("synthetic credential placeholder\n", encoding="utf-8")
        adc.chmod(0o600)
        gcloud = root / "google-cloud-sdk" / "bin" / "gcloud"
        gcloud.parent.mkdir(parents=True)
        gcloud.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gcloud.chmod(0o700)
        executable_sha = core.sha256_file(gcloud)
        receipt = root / "resolution.json"
        receipt.write_text(
            json.dumps(
                {
                    "audit": "lvef_scc_gcloud_resolution",
                    "credential_material_accessed": False,
                    "expected_version": "579.0.0",
                    "executable_sha256": executable_sha,
                    "module_name": None,
                    "resolution_source": "COMMON_SELF_CONTAINED_INSTALL",
                    "retained_archive_sha256": (
                        "a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
                    ),
                    "retained_tar_payload_sha256": (
                        "f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
                    ),
                    "selected_executable": str(gcloud.resolve()),
                    "status": "PASS",
                    "version": "579.0.0",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        receipt.chmod(0o600)
        provider = core.GcloudADCTokenProvider(
            gcloud,
            cloudsdk_config=cloudsdk,
            authority_receipt=receipt,
            authority_receipt_sha256=core.sha256_file(receipt),
        )
        expected = {
            "gcloud_resolution_receipt_sha256": core.sha256_file(receipt),
            "gcloud_executable_sha256": executable_sha,
        }
        assert provider.validate_authority() == expected
        cloudsdk.chmod(0o2700)
        assert provider.validate_authority() == expected
        cloudsdk.chmod(0o770)
        try:
            provider.validate_authority()
        except core.DownloadTransportError as exc:
            assert str(exc) == "CLOUDSDK_CONFIG_NOT_PRIVATE"
        else:
            raise AssertionError("group-writable Cloud SDK config was accepted")
        cloudsdk.chmod(0o2700)
        with mock.patch.object(
            core.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout="synthetic-token\n"),
        ) as run:
            assert provider() == "synthetic-token"
        assert run.call_args.kwargs["stderr"] is core.subprocess.DEVNULL
        assert run.call_args.kwargs["env"] == {
            "CLOUDSDK_CONFIG": str(cloudsdk),
            "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
            "PATH": "/usr/bin:/bin",
        }
        receipt.write_text("{}", encoding="utf-8")
        try:
            provider.validate_authority()
        except core.DownloadTransportError as exc:
            assert str(exc) == "CLOUDSDK_AUTHORITY_RECEIPT_INVALID"
            assert exc.failure_class == "AUTHENTICATION"
        else:
            raise AssertionError("Changed Cloud SDK resolution receipt was accepted")


def test_gcloud_runtime_authority_must_match_plan_and_is_closed() -> None:
    authority = _authority()
    observed = {
        "gcloud_resolution_receipt_sha256": authority[
            "gcloud_resolution_receipt_sha256"
        ],
        "gcloud_executable_sha256": authority["gcloud_executable_sha256"],
    }
    assert core.validate_gcloud_runtime_authority(
        observed, expected_runtime_authority=authority
    ) == dict(sorted(observed.items()))
    for changed in (
        {**observed, "gcloud_executable_sha256": "f" * 64},
        {**observed, "unexpected": "f" * 64},
    ):
        try:
            core.validate_gcloud_runtime_authority(
                changed, expected_runtime_authority=authority
            )
        except core.OrchestrationError as exc:
            assert str(exc) in {
                "CURRENT_GCLOUD_AUTHORITY_MISMATCH",
                "GCLOUD_RUNTIME_AUTHORITY_SCHEMA_INVALID",
            }
        else:
            raise AssertionError("Foreign Cloud SDK runtime authority was accepted")


def test_authorization_gated_downloader_executes_only_injected_synthetic_transport() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)

    class SyntheticTransport:
        def __init__(self):
            self.calls = 0

        def fetch(self, expectation, *, partial_path, billing_project, access_token):
            assert billing_project == "synthetic-private-project"
            assert access_token == "synthetic-token"
            self.calls += 1
            partial_path.write_bytes(content[expectation.source_object_key])
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": expectation.size_bytes,
                "resume_offset_bytes": 0,
                "response_body_bytes_read": expectation.size_bytes,
                "final_partial_size_bytes": expectation.size_bytes,
                "content_range_validated": False,
            }

    transport = SyntheticTransport()
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        with mock.patch.dict(
            "os.environ", {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"}
        ):
            updated = core.execute_exact_batch_download(
                plan=plan,
                requirements=_requirements(),
                ledger=ledger,
                contract=contract,
                batch_id="c3_batch_000",
                expected_runtime_authority=ledger["authority"],
                authorization_receipt=authorization,
                output_root=output,
                launch_authority_sha256="f" * 64,
                argv=("download-batch",),
                token_provider=lambda: "synthetic-token",
                transport=transport,
                now=now,
            )
        assert transport.calls == plan["batches"][0]["n_objects"]
        receipts = updated["batches"]["c3_batch_000"]["download_verification_receipts"]
        assert len(receipts) == plan["batches"][0]["n_objects"]
        assert updated["batches"]["c3_batch_000"]["state"] == "DOWNLOAD_VERIFIED"
        assert not list(output.rglob("*.partial"))
        assert len(list(output.rglob("*.dcm"))) == plan["batches"][0]["n_objects"]
        manifest = output / "c3_batch_000" / "verified_download_manifest.restricted.csv"
        assert manifest.is_file()
        assert (output / "c3_batch_000" / "receipts" / "download_start_transition.restricted.json").is_file()
        assert (output / "c3_batch_000" / "receipts" / "download_verified_transition.restricted.json").is_file()


def test_synthetic_transport_resumes_same_attempt_partial_with_bounded_retry() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)

    class ResumeTransport:
        def __init__(self):
            self.calls: dict[str, int] = {}

        def fetch(self, expectation, *, partial_path, billing_project, access_token):
            del billing_project, access_token
            calls = self.calls.get(expectation.source_object_key, 0) + 1
            self.calls[expectation.source_object_key] = calls
            payload = content[expectation.source_object_key]
            if calls == 1 and len(self.calls) == 1:
                partial_path.write_bytes(payload[: len(payload) // 2])
                raise core.DownloadTransportError(
                    "SYNTHETIC_TRANSIENT", "TRANSIENT_NETWORK"
                )
            offset = partial_path.stat().st_size if partial_path.exists() else 0
            with partial_path.open("ab" if offset else "wb") as handle:
                if offset:
                    handle.write(payload[offset:])
                else:
                    handle.write(payload)
            response_bytes = expectation.size_bytes - offset
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": response_bytes,
                "resume_offset_bytes": offset,
                "response_body_bytes_read": response_bytes,
                "final_partial_size_bytes": expectation.size_bytes,
                "content_range_validated": offset > 0,
            }

    transport = ResumeTransport()
    retry_delays: list[int] = []
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        with mock.patch.dict(
            "os.environ", {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"}
        ):
            updated = core.execute_exact_batch_download(
                plan=plan,
                requirements=_requirements(),
                ledger=ledger,
                contract=contract,
                batch_id="c3_batch_000",
                expected_runtime_authority=ledger["authority"],
                authorization_receipt=authorization,
                output_root=output,
                launch_authority_sha256="f" * 64,
                argv=("download-batch",),
                token_provider=lambda: "synthetic-token",
                transport=transport,
                now=now,
                sleeper=retry_delays.append,
            )
        attempts = updated["batches"]["c3_batch_000"]["download_attempts"]
        assert sorted(attempts.values(), reverse=True)[0] == 2
        assert retry_delays == [2]
        assert len(updated["batches"]["c3_batch_000"]["download_verification_receipts"]) == 4


def _synthetic_transport(content):
    class SyntheticTransport:
        def __init__(self):
            self.calls: dict[str, int] = {}

        def fetch(self, expectation, *, partial_path, billing_project, access_token):
            assert billing_project == "synthetic-private-project"
            assert access_token == "synthetic-token"
            self.calls[expectation.source_object_key] = (
                self.calls.get(expectation.source_object_key, 0) + 1
            )
            payload = content[expectation.source_object_key]
            partial_path.write_bytes(payload)
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": expectation.size_bytes,
                "resume_offset_bytes": 0,
                "response_body_bytes_read": expectation.size_bytes,
                "final_partial_size_bytes": expectation.size_bytes,
                "content_range_validated": False,
            }

    return SyntheticTransport()


def _run_download_for_crash_test(
    *, plan, ledger, authorization, contract, output, transport, now,
    token_provider=None, monotonic_clock=None
):
    kwargs = {}
    if monotonic_clock is not None:
        kwargs["monotonic_clock"] = monotonic_clock
    return core.execute_exact_batch_download(
        plan=plan,
        requirements=_requirements(),
        ledger=ledger,
        contract=contract,
        batch_id="c3_batch_000",
        expected_runtime_authority=ledger["authority"],
        authorization_receipt=authorization,
        output_root=output,
        launch_authority_sha256="f" * 64,
        argv=("download-batch",),
        token_provider=token_provider or (lambda: "synthetic-token"),
        transport=transport,
        now=now,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_crash_after_final_before_receipt_recovers_with_bound_recovery_receipt() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    transport = _synthetic_transport(content)
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        original_writer = core.atomic_write_json_no_clobber
        crash = {"raised": False}

        def crash_before_verification_receipt(path, payload, *, attempt_id):
            if path.name.endswith(".verification.json") and not crash["raised"]:
                crash["raised"] = True
                raise KeyboardInterrupt("synthetic crash after final")
            return original_writer(path, payload, attempt_id=attempt_id)

        with (
            mock.patch.dict(
                "os.environ",
                {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
            ),
            mock.patch.object(
                core,
                "atomic_write_json_no_clobber",
                side_effect=crash_before_verification_receipt,
            ),
        ):
            try:
                _run_download_for_crash_test(
                    plan=plan,
                    ledger=ledger,
                    authorization=authorization,
                    contract=contract,
                    output=output,
                    transport=transport,
                    now=now,
                )
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("Synthetic crash was not reached")
        first_key = plan["batches"][0]["objects"][0]["source_object_key"]
        assert transport.calls[first_key] == 1
        with mock.patch.dict(
            "os.environ",
            {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
        ):
            updated = _run_download_for_crash_test(
                plan=plan,
                ledger=ledger,
                authorization=authorization,
                contract=contract,
                output=output,
                transport=transport,
                now=now,
            )
        assert transport.calls[first_key] == 1
        batch = updated["batches"]["c3_batch_000"]
        assert batch["state"] == "DOWNLOAD_VERIFIED"
        assert first_key in batch["download_recovery_receipts"]
        recovery_path = (
            output / "c3_batch_000" / "receipts" / f"{first_key}.recovery.json"
        )
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        assert recovery["recovery_class"] == "FINAL_WITHOUT_VERIFICATION_RECEIPT"
        assert core.sha256_file(recovery_path) == batch["download_recovery_receipts"][first_key]


def test_adc_token_is_refreshed_proactively_without_retrying_auth_failure() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    transport = _synthetic_transport(content)
    provider_calls = {"count": 0}
    clock_values = iter((0.0, 1_000.0, 2_500.0, 2_600.0))

    def provider():
        provider_calls["count"] += 1
        return f"synthetic-token-{provider_calls['count']}"

    class TokenAgnosticTransport:
        def fetch(self, expectation, *, partial_path, billing_project, access_token):
            assert access_token.startswith("synthetic-token-")
            return transport.fetch(
                expectation,
                partial_path=partial_path,
                billing_project=billing_project,
                access_token="synthetic-token",
            )

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        with mock.patch.dict(
            "os.environ",
            {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
        ):
            updated = _run_download_for_crash_test(
                plan=plan,
                ledger=ledger,
                authorization=authorization,
                contract=contract,
                output=output,
                transport=TokenAgnosticTransport(),
                token_provider=provider,
                monotonic_clock=lambda: next(clock_values),
                now=now,
            )
    assert provider_calls["count"] == 2
    assert updated["batches"]["c3_batch_000"]["state"] == "DOWNLOAD_VERIFIED"


def test_crash_after_receipt_before_ledger_recovers_without_redownload() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    transport = _synthetic_transport(content)
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        original_append = core.append_ledger_delta_no_clobber
        crash = {"raised": False}

        def crash_before_ledger(*args, **kwargs):
            if kwargs.get("operation") == "DOWNLOAD_VERIFIED" and not crash["raised"]:
                crash["raised"] = True
                raise KeyboardInterrupt("synthetic crash after receipt")
            return original_append(*args, **kwargs)

        with (
            mock.patch.dict(
                "os.environ",
                {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
            ),
            mock.patch.object(
                core, "append_ledger_delta_no_clobber", side_effect=crash_before_ledger
            ),
        ):
            try:
                _run_download_for_crash_test(
                    plan=plan,
                    ledger=ledger,
                    authorization=authorization,
                    contract=contract,
                    output=output,
                    transport=transport,
                    now=now,
                )
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("Synthetic crash was not reached")
        first_key = plan["batches"][0]["objects"][0]["source_object_key"]
        with mock.patch.dict(
            "os.environ",
            {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
        ):
            updated = _run_download_for_crash_test(
                plan=plan,
                ledger=ledger,
                authorization=authorization,
                contract=contract,
                output=output,
                transport=transport,
                now=now,
            )
        assert transport.calls[first_key] == 1
        recovery = json.loads(
            (
                output
                / "c3_batch_000"
                / "receipts"
                / f"{first_key}.recovery.json"
            ).read_text(encoding="utf-8")
        )
        assert recovery["recovery_class"] == "VERIFICATION_RECEIPT_WITHOUT_LEDGER_BINDING"
        assert first_key in updated["batches"]["c3_batch_000"]["download_recovery_receipts"]


def test_inconsistent_unbound_final_fails_without_adoption() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    transport = _synthetic_transport(content)
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        original_writer = core.atomic_write_json_no_clobber

        def crash(path, payload, *, attempt_id):
            if path.name.endswith(".verification.json"):
                raise KeyboardInterrupt("synthetic crash")
            return original_writer(path, payload, attempt_id=attempt_id)

        with (
            mock.patch.dict(
                "os.environ",
                {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
            ),
            mock.patch.object(core, "atomic_write_json_no_clobber", side_effect=crash),
        ):
            try:
                _run_download_for_crash_test(
                    plan=plan, ledger=ledger, authorization=authorization,
                    contract=contract, output=output, transport=transport, now=now
                )
            except KeyboardInterrupt:
                pass
        first_key = plan["batches"][0]["objects"][0]["source_object_key"]
        final = output / "c3_batch_000" / "objects" / f"{first_key}.dcm"
        final.write_bytes(b"changed")
        with mock.patch.dict(
            "os.environ",
            {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
        ):
            try:
                _run_download_for_crash_test(
                    plan=plan, ledger=ledger, authorization=authorization,
                    contract=contract, output=output, transport=transport, now=now
                )
            except core.OrchestrationError as exc:
                assert str(exc) == "RECOVERY_FINAL_SIZE_MISMATCH"
            else:
                raise AssertionError("Changed unbound final was adopted")


def test_transfer_identity_mismatch_is_not_accepted_or_retried() -> None:
    plan, content = _plan()
    expectation = core.expectation_from_plan_object(plan["batches"][0]["objects"][0])
    payload = content[expectation.source_object_key]
    with tempfile.TemporaryDirectory() as directory:
        partial = Path(directory) / core.planned_partial_name(
            expectation, "lvef_c3_synthetic_attempt"
        )
        partial.write_bytes(payload)
        receipt = {
            "schema_version": 2,
            "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
            "source_object_key": expectation.source_object_key,
            "size_bytes": expectation.size_bytes,
            "generation": str(int(expectation.generation) + 1),
            "md5_base64": expectation.md5_base64,
            "crc32c_base64": expectation.crc32c_base64,
            "media_request_count": 1,
            "object_body_bytes_read": expectation.size_bytes,
            "resume_offset_bytes": 0,
            "response_body_bytes_read": expectation.size_bytes,
            "final_partial_size_bytes": expectation.size_bytes,
            "content_range_validated": False,
        }
        try:
            core.verify_downloaded_partial(
                expectation,
                partial_path=partial,
                transfer_receipt=receipt,
                attempt_id="lvef_c3_synthetic_attempt",
            )
        except core.OrchestrationError as exc:
            assert str(exc) == "TRANSFER_REMOTE_IDENTITY_CHANGED"
        else:
            raise AssertionError("Changed remote generation was accepted")


def test_exact_download_batch_cli_contract_runs_with_synthetic_transport_only() -> None:
    plan, content = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime.now(timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)

    class SyntheticTransport:
        def fetch(self, expectation, *, partial_path, billing_project, access_token):
            assert billing_project == "synthetic-private-project"
            assert access_token == "synthetic-token"
            partial_path.write_bytes(content[expectation.source_object_key])
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": expectation.size_bytes,
                "resume_offset_bytes": 0,
                "response_body_bytes_read": expectation.size_bytes,
                "final_partial_size_bytes": expectation.size_bytes,
                "content_range_validated": False,
            }

    class SyntheticTokenProvider:
        def validate_authority(self):
            return {
                "gcloud_resolution_receipt_sha256": ledger["authority"][
                    "gcloud_resolution_receipt_sha256"
                ],
                "gcloud_executable_sha256": ledger["authority"][
                    "gcloud_executable_sha256"
                ],
            }

        def __call__(self):
            return "synthetic-token"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw_root = root / "raw"
        raw_root.mkdir()
        contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
        contract["storage"]["raw_root"] = str(raw_root)
        plan_path = root / "plan.json"
        ledger_path = root / "ledger.json"
        authorization_path = root / "authorization.json"
        environment_receipt = root / "environment.json"
        output_ledger = root / "updated-ledger.json"
        gcloud = root / "gcloud"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        environment_receipt.write_text("synthetic environment receipt\n", encoding="utf-8")
        gcloud.write_text("synthetic executable placeholder\n", encoding="utf-8")
        exact_scheduler_args = [
            "download-batch",
            "--contract", str(CONTRACT_PATH),
            "--plan", str(plan_path),
            "--ledger", str(ledger_path),
            "--authorization-receipt", str(authorization_path),
            "--launch-authority-sha256", "f" * 64,
            "--batch-id", "c3_batch_000",
            "--governing-commit", ledger["authority"]["git_commit"],
            "--environment-receipt", str(environment_receipt),
            "--output-root", str(raw_root),
            "--gcloud-binary", str(gcloud),
            "--crc32c-python", str(Path(sys.executable).resolve()),
            "--crc32c-worker", str(ROOT / "scripts/lvef_c3_crc32c_worker.py"),
            "--ledger-output", str(output_ledger),
        ]
        synthetic_digest_worker = mock.MagicMock()
        synthetic_digest_worker.__enter__.return_value.digest = (
            core._inprocess_digest_provider
        )
        synthetic_digest_worker.__exit__.return_value = False
        with (
            mock.patch.object(core, "load_orchestration_contract", return_value=contract),
            mock.patch.object(core, "validate_plan_authority_against_contract"),
            mock.patch.object(
                core,
                "validate_ledger_against_current_runtime",
                return_value=ledger["authority"],
            ),
            mock.patch.object(core, "production_requirements", return_value=_requirements()),
            mock.patch.object(
                core, "GcloudADCTokenProvider", return_value=SyntheticTokenProvider()
            ),
            mock.patch.object(
                core, "GCSExactObjectBodyTransport", return_value=SyntheticTransport()
            ),
            mock.patch.object(
                core,
                "ExternalCRC32CDigestWorker",
                return_value=synthetic_digest_worker,
            ),
            mock.patch.dict(
                "os.environ",
                {
                    "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project",
                    "LVEF_C3_CLOUDSDK_CONFIG": str(root / "private-cloudsdk"),
                    "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT": str(
                        root / "cloudsdk-resolution-receipt.json"
                    ),
                    "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256": "d" * 64,
                },
            ),
        ):
            assert core.main(exact_scheduler_args) == 0
        updated = json.loads(output_ledger.read_text(encoding="utf-8"))
        assert updated["batches"]["c3_batch_000"]["state"] == "DOWNLOAD_VERIFIED"
        assert (
            raw_root / "c3_batch_000" / "verified_download_manifest.restricted.csv"
        ).is_file()


def test_downloader_authorization_expiry_scope_and_request_budget_fail_before_transport() -> None:
    plan, _ = _plan()
    ledger = _ledger_in_download_state(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    for field, value, expected in (
        ("maximum_requests", 1, "BODY_TRANSFER_REQUEST_BUDGET_INVALID"),
        ("batch_ids", ["c3_batch_001"], "BODY_TRANSFER_AUTHORIZATION_BATCH_SCOPE_INVALID"),
        ("expires_at_utc", "2026-08-10T11:00:00Z", "BODY_TRANSFER_AUTHORIZATION_NOT_CURRENT"),
    ):
        changed = copy.deepcopy(authorization)
        changed[field] = value
        try:
            core.validate_body_transfer_authorization(
                changed,
                ledger=ledger,
                plan=plan,
                batch_id="c3_batch_000",
                maximum_attempts_per_object=5,
                expected_launch_authority_sha256="f" * 64,
                now=now,
            )
        except core.OrchestrationError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"Bad authorization {field} did not fail")


def test_cache_capacity_and_retirement_are_fail_closed() -> None:
    one = core.evaluate_cache_capacity(
        [50], filesystem_available_bytes=300, required_reserve_bytes=200,
        maximum_active_batches=2
    )
    two = core.evaluate_cache_capacity(
        [50, 60], filesystem_available_bytes=300, required_reserve_bytes=200,
        maximum_active_batches=2
    )
    assert one["capacity_gate_passed"] is True
    assert two["capacity_gate_passed"] is False
    plan, _ = _plan()
    ledger = _ledger_in_download_state(plan)
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    raw = core.evaluate_cache_retirement(
        ledger,
        batch_id="c3_batch_000",
        target_kind="raw_dicom",
        contract=contract,
        owner_authorization=None,
        expected_launch_authority_sha256="f" * 64,
    )
    extracted = core.evaluate_cache_retirement(
        ledger,
        batch_id="c3_batch_000",
        target_kind="extracted_cache",
        contract=contract,
        owner_authorization=None,
        expected_launch_authority_sha256="f" * 64,
    )
    assert raw == {"authorized": False, "reason": "RAW_DICOM_DELETION_PROHIBITED"}
    assert extracted["authorized"] is False
    assert "OWNER_CACHE_RETIREMENT_AUTHORIZATION_ABSENT" in extracted["all_reasons"]


def test_contract_cache_overlap_enforces_both_reserves_and_explicit_two_batch_scope() -> None:
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    one = core.evaluate_contract_cache_overlap(
        contract,
        active_batches=1,
        quota_available_bytes=400_000_000_000,
        filesystem_available_bytes=400_000_000_000,
    )
    unauthorized_two = core.evaluate_contract_cache_overlap(
        contract,
        active_batches=2,
        quota_available_bytes=500_000_000_000,
        filesystem_available_bytes=500_000_000_000,
    )
    insufficient_two = core.evaluate_contract_cache_overlap(
        contract,
        active_batches=2,
        quota_available_bytes=500_000_000_000,
        filesystem_available_bytes=300_000_000_000,
        two_batch_authorized=True,
    )
    assert one["authorized"] is True
    assert unauthorized_two["reason"] == "TWO_BATCH_OVERLAP_NOT_AUTHORIZED"
    assert insufficient_two["physical_reserve_gate_passed"] is False


def test_scoped_owner_receipt_can_authorize_decision_but_never_raw_deletion() -> None:
    plan, _ = _plan()
    ledger = _ledger_in_download_state(plan)
    batch_id = "c3_batch_000"
    for to_state in (
        "DOWNLOAD_VERIFIED",
        "DICOM_AUDIT_COMPLETE",
        "EXTRACTION_COMPLETE",
        "EMBEDDING_COMPLETE",
        "STUDY_POOLING_COMPLETE",
        "PRESERVATION_COMPLETE",
        "CACHE_RETIREMENT_ELIGIBLE",
    ):
        batch = ledger["batches"][batch_id]
        receipt = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": ledger["attempt_id"],
            "batch_id": batch_id,
            "from_state": batch["state"],
            "to_state": to_state,
            "status": "PASS",
            "authority": ledger["authority"],
            "input_receipt_sha256": [batch["events"][-1]["receipt_sha256"]],
            "output_manifest_sha256": hashlib.sha256(to_state.encode()).hexdigest(),
        }
        ledger = core.apply_transition(ledger, receipt)
    owner_receipt = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_cache_retirement_owner_authorization_v2",
        "status": "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT",
        "authorization_scope": "EXTRACTED_CACHE_RETIREMENT",
        "owner_authorized": True,
        "owner_authorization_date_utc": "2026-08-10T12:00:00Z",
        "attempt_id": ledger["attempt_id"],
        "batch_id": batch_id,
        "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
        "preservation_receipt_sha256": "b" * 64,
        "cache_inventory_sha256": "c" * 64,
        "launch_authority_sha256": "f" * 64,
    }
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    decision = core.evaluate_cache_retirement(
        ledger,
        batch_id=batch_id,
        target_kind="extracted_cache",
        contract=contract,
        owner_authorization=owner_receipt,
        expected_launch_authority_sha256="f" * 64,
    )
    raw = core.evaluate_cache_retirement(
        ledger,
        batch_id=batch_id,
        target_kind="raw_dicom",
        contract=contract,
        owner_authorization=owner_receipt,
        expected_launch_authority_sha256="f" * 64,
    )
    assert decision["authorized"] is True
    assert decision["raw_dicom_deletion_permitted"] is False
    assert raw == {"authorized": False, "reason": "RAW_DICOM_DELETION_PROHIBITED"}


def test_control_schemas_are_hash_bound_and_semantically_validated() -> None:
    contract = core.load_orchestration_contract(CONTRACT_PATH)
    state_path = CONTRACT_PATH.parent / contract["authority"]["state_machine_schema_filename"]
    resume_path = CONTRACT_PATH.parent / contract["authority"]["resume_ledger_schema_filename"]
    assert core.sha256_file(state_path) == contract["authority"]["state_machine_schema_sha256"]
    assert core.sha256_file(resume_path) == contract["authority"]["resume_ledger_schema_sha256"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for source in (CONTRACT_PATH, state_path, resume_path):
            (root / source.name).write_bytes(source.read_bytes())
        changed_state = json.loads((root / state_path.name).read_text(encoding="utf-8"))
        changed_state["transitions"]["PLANNED"] = ["FINALIZED"]
        changed_body = json.dumps(changed_state, sort_keys=True).encode()
        (root / state_path.name).write_bytes(changed_body)
        changed_contract = core.yaml.safe_load((root / CONTRACT_PATH.name).read_text())
        changed_contract["authority"]["state_machine_schema_sha256"] = hashlib.sha256(
            changed_body
        ).hexdigest()
        (root / CONTRACT_PATH.name).write_text(
            core.yaml.safe_dump(changed_contract, sort_keys=False), encoding="utf-8"
        )
        try:
            core.load_orchestration_contract(root / CONTRACT_PATH.name)
        except core.OrchestrationError as exc:
            assert str(exc) == "STATE_MACHINE_SCHEMA_SEMANTICS_INVALID"
        else:
            raise AssertionError("Semantically altered state schema was accepted")


def test_orchestration_contract_rejects_unknown_nested_key() -> None:
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))
    contract["downloader"]["unexpected_nested_authority"] = False
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for filename in ("lvef_c3_state_machine_v2.json", "lvef_c3_resume_ledger_v2.json"):
            (root / filename).write_bytes((CONTRACT_PATH.parent / filename).read_bytes())
        path = root / CONTRACT_PATH.name
        path.write_text(core.yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
        try:
            core.load_orchestration_contract(path)
        except core.OrchestrationError as exc:
            assert str(exc) == "ORCHESTRATION_CONTRACT_DOWNLOADER_SCHEMA_INVALID"
        else:
            raise AssertionError("Unknown nested orchestration-contract key was accepted")


def test_scheduler_pins_resolver_authoritative_nested_gcloud_path() -> None:
    source = (
        ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    ).read_text(encoding="utf-8")
    expected = (
        "/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/"
        "google-cloud-sdk/bin/gcloud"
    )
    assert expected in source
    assert "google-cloud-cli-579.0.0/bin/gcloud" not in source


def test_nonretryable_download_failure_persists_terminal_receipt_and_ledger() -> None:
    plan, _ = _plan()
    ledger = _batch_planned_ledger(plan)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    authorization = _body_authorization(plan, ledger, now=now)
    contract = copy.deepcopy(core.load_orchestration_contract(CONTRACT_PATH))

    class FailedTransport:
        def fetch(self, *args, **kwargs):
            raise core.DownloadTransportError("SYNTHETIC_DENIED", "AUTHORIZATION")

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "raw"
        output.mkdir()
        contract["storage"]["raw_root"] = str(output)
        with mock.patch.dict(
            "os.environ",
            {"LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project"},
        ):
            try:
                _run_download_for_crash_test(
                    plan=plan,
                    ledger=ledger,
                    authorization=authorization,
                    contract=contract,
                    output=output,
                    transport=FailedTransport(),
                    now=now,
                )
            except core.OrchestrationError as exc:
                assert str(exc) == "DOWNLOAD_FAILED_NONRETRYABLE_OR_EXHAUSTED"
            else:
                raise AssertionError("Nonretryable transport failure did not stop")
        latest = core.load_latest_ledger_snapshot(
            output / "c3_batch_000" / "ledger", initial_ledger=ledger
        )
        assert latest["status"] == "FAILED"
        assert latest["batches"]["c3_batch_000"]["state"] == "FAILED_NONRETRYABLE"
        assert list((output / "c3_batch_000" / "receipts").glob("*.failure.restricted.json"))


def test_resume_journal_is_constant_size_per_event_and_replays_exactly() -> None:
    plan, _ = _plan()
    initial = _ledger_in_download_state(plan)
    ledger = copy.deepcopy(initial)
    key = plan["batches"][0]["objects"][0]["source_object_key"]
    event_count = 400
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for count in range(1, event_count + 1):
            core.append_ledger_delta_no_clobber(
                root,
                ledger,
                operation="DOWNLOAD_ATTEMPT",
                payload={
                    "batch_id": "c3_batch_000",
                    "source_object_key": key,
                    "attempt_count": count,
                },
            )
        files = list(root.iterdir())
        assert len(files) == event_count
        assert all(path.name.startswith("journal_") for path in files)
        assert max(path.stat().st_size for path in files) < 1_024
        assert sum(path.stat().st_size for path in files) < event_count * 1_024
        replayed = core.load_latest_ledger_snapshot(root, initial_ledger=initial)
        assert replayed == ledger
        assert replayed["journal_sequence"] == event_count
        assert replayed["batches"]["c3_batch_000"]["download_attempts"][key] == event_count


def test_strict_json_csv_symlink_and_no_clobber_guards() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        duplicate_json = root / "duplicate.json"
        duplicate_json.write_text('{"a":1,"a":2}', encoding="utf-8")
        try:
            core.load_strict_json(duplicate_json)
        except core.OrchestrationError as exc:
            assert str(exc) == "JSON_DUPLICATE_KEY"
        else:
            raise AssertionError("Duplicate JSON key was accepted")
        duplicate_csv = root / "duplicate.csv"
        duplicate_csv.write_text("a,a\n1,2\n", encoding="utf-8")
        try:
            core._read_csv_rows(duplicate_csv)
        except core.OrchestrationError as exc:
            assert str(exc) == "CSV_HEADER_INVALID_OR_DUPLICATE"
        else:
            raise AssertionError("Duplicate CSV header was accepted")
        target = root / "target.json"
        core.atomic_write_json_no_clobber(
            target, {"safe": True}, attempt_id="lvef_c3_synthetic_attempt"
        )
        try:
            core.atomic_write_json_no_clobber(
                target, {"safe": False}, attempt_id="lvef_c3_synthetic_attempt"
            )
        except core.OrchestrationError as exc:
            assert str(exc) == "OUTPUT_ALREADY_EXISTS_NO_CLOBBER"
        else:
            raise AssertionError("No-clobber JSON write overwrote output")
        symlink = root / "link.json"
        symlink.symlink_to(target)
        try:
            core.read_regular_bytes(symlink)
        except core.OrchestrationError as exc:
            assert str(exc) == "INPUT_NOT_REGULAR_NOFOLLOW_FILE"
        else:
            raise AssertionError("Symlink input was followed")


def test_core_has_no_object_listing_or_scheduler_execution_path() -> None:
    source = (ROOT / "scripts" / "lvef_c3_orchestration_core.py").read_text(
        encoding="utf-8"
    )
    assert "objects.list" not in source
    assert "qsub" not in source
    assert "BigQuery" not in source
    assert "pixel_array" not in source
    assert "torch" not in source


def test_scheduler_never_probes_gcloud_with_ambient_configuration() -> None:
    scheduler = (
        ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    ).read_text(encoding="utf-8")
    assert "version --format=" not in scheduler
    assert 'export CLOUDSDK_CONFIG="$LVEF_C3_CLOUDSDK_CONFIG"' in scheduler
    assert "GCLOUD_RESOLUTION_RECEIPT_HASH_MISMATCH" in scheduler
    assert "CLOUDSDK_AND_GCLOUD_RECEIPT_PATH_MISMATCH" in scheduler
    assert "CLOUDSDK_AND_GCLOUD_RECEIPT_HASH_MISMATCH" in scheduler
    core_source = (ROOT / "scripts" / "lvef_c3_orchestration_core.py").read_text(
        encoding="utf-8"
    )
    assert "CLOUDSDK_RESOLUTION_RECEIPT_NOT_AUTHORITATIVE" in core_source
    assert '"expected_version") != "579.0.0"' in core_source


def test_core_source_has_no_duplicate_literal_dict_keys() -> None:
    source = (ROOT / "scripts" / "lvef_c3_orchestration_core.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    duplicates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        if len(keys) != len(set(keys)):
            duplicates.append(node.lineno)
    assert duplicates == []


def test_main_sanitizes_unexpected_runtime_exception() -> None:
    stderr = io.StringIO()
    secret = "restricted/secret/object-locator"
    with (
        mock.patch.object(core, "_main_download_batch", side_effect=RuntimeError(secret)),
        mock.patch.object(
            core,
            "parse_args",
            return_value=type("Args", (), {"command": "download-batch"})(),
        ),
        contextlib.redirect_stderr(stderr),
    ):
        status = core.main(("download-batch",))
    assert status == 70
    assert stderr.getvalue().strip() == (
        "C3_ORCHESTRATION_REFUSED=UNEXPECTED_RUNTIME_FAILURE"
    )
    assert secret not in stderr.getvalue()
