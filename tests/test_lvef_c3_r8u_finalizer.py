from __future__ import annotations

import copy
import csv
from dataclasses import fields, replace
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / filename
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


finalizer = _load(
    "finalize_lvef_c3_production_r8u_test",
    "finalize_lvef_c3_production.py",
)


def _expect_code(code: str, function: Any) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", None) == code
    else:
        raise AssertionError(f"Expected {code}")


def _authority() -> Any:
    names = [field.name for field in fields(finalizer.R8UImplementationAuthority)]
    values = {
        name: hashlib.sha256(f"r8u:{name}".encode("ascii")).hexdigest()
        for name in names
        if name != "implementation_commit"
    }
    values.update(
        {
            "historical_r8r_recovery_authority_sha256": (
                finalizer.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                    "recovery_authority_sha256"
                ]
            ),
            "historical_r8r_recovery_terminal_receipt_sha256": (
                finalizer.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                    "recovery_terminal_receipt_sha256"
                ]
            ),
            "historical_r8r_continuation_capacity_receipt_sha256": (
                finalizer.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                    "continuation_capacity_receipt_sha256"
                ]
            ),
            "historical_r8r_continuation_claim_sha256": (
                finalizer.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                    "continuation_claim_sha256"
                ]
            ),
            "historical_r8r_continuation_submission_receipt_sha256": (
                finalizer.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES[
                    "continuation_submission_receipt_sha256"
                ]
            ),
        }
    )
    return finalizer.R8UImplementationAuthority(
        implementation_commit="c" * 40,
        **values,
    )


def _receipt_paths() -> dict[str, Path]:
    attempt_root = (
        Path("/synthetic/attempts") / finalizer.R8R_ATTEMPT_ID
    )
    return {
        batch_id: (
            attempt_root
            / "batches"
            / batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        for batch_id in finalizer.EXPECTED_BATCH_IDS
    }


def _epoch_receipts() -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    dict[str, int],
    dict[str, Any],
    tuple[str, ...],
]:
    original_epoch = tuple(f"original-{index}" for index in range(5))
    current_epoch = tuple(f"current-{index}" for index in range(5))
    scientific = {
        key: f"fixed-{key}"
        for key in finalizer.R8R_SCIENTIFIC_AUTHORITY_KEYS
    }
    scientific.update(
        {
            "governing_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
            "orchestration_contract_sha256": "1" * 64,
            "checkpoint_sha256": "2" * 64,
            "environment_receipt_sha256": "3" * 64,
        }
    )
    receipts: list[dict[str, Any]] = []
    for index, batch_id in enumerate(finalizer.EXPECTED_BATCH_IDS):
        if index < 2:
            epoch = original_epoch
        elif index < 15:
            epoch = finalizer.R8U_FE3_IMPLEMENTATION_EPOCH
        else:
            epoch = current_epoch
        receipts.append(
            {
                **scientific,
                "batch_id": batch_id,
                "attempt_id": finalizer.R8R_ATTEMPT_ID,
                **dict(
                    zip(
                        finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS,
                        epoch,
                        strict=True,
                    )
                ),
            }
        )
    hashes = {
        batch_id: hashlib.sha256(batch_id.encode("ascii")).hexdigest()
        for batch_id in finalizer.EXPECTED_BATCH_IDS
    }
    sizes = {batch_id: 1 for batch_id in finalizer.EXPECTED_BATCH_IDS}
    for batch_id, (size, digest) in (
        finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        sizes[batch_id] = size
        hashes[batch_id] = digest
    runtime = {
        "git_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
        "orchestration_contract_sha256": "1" * 64,
        "checkpoint_sha256": "2" * 64,
        "environment_receipt_sha256": "3" * 64,
    }
    return receipts, hashes, sizes, runtime, current_epoch


def _qsub_evidence(payload: bytes) -> dict[str, Any]:
    return {
        "stdout_bytes": len(payload),
        "stdout_sha256": hashlib.sha256(payload).hexdigest(),
        "stderr_bytes": 0,
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "exit_status": 0,
    }


def _valid_r8u_summary() -> dict[str, Any]:
    value = {key: 0 for key in finalizer.FINAL_KEYS}
    value.update(
        {
            "schema_version": 2,
            "artifact_type": "lvef_c3_production_finalization_summary_v2",
            "status": "PASS_PRODUCTION_C3_BATCH_RECEIPTS_RECONCILED",
            "production_batches": 19,
            "selected_studies": 2,
            "selected_subjects": 2,
            "verified_source_objects": 3,
            "selected_source_bytes": 3,
            "dicom_readable_objects": 3,
            "dicom_unreadable_objects": 0,
            "multiframe_cines": 2,
            "single_frame_objects": 1,
            "extracted_clips": 2,
            "successfully_extracted_cines": 2,
            "object_technical_dispositions": 0,
            "blocking_failures": 0,
            "studies_affected_by_technical_disposition": 0,
            "new_no_cine_studies": 0,
            "technical_disposition_counts_by_class": {
                "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": 0
            },
            "technical_disposition_policy_version": (
                finalizer.production_stages.
                OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
            ),
            "technical_disposition_manifest_set_sha256": "4" * 64,
            "unique_clip_keys": 2,
            "clip_embeddings": 2,
            "pooled_imaging_eligible_studies": 2,
            "no_cine_studies": 0,
            "no_cine_disposition": "NONE",
            "batch_receipt_set_sha256": "5" * 64,
            "all_authority_bindings_identical": False,
            "canonical_clip_index_sha256": None,
            "canonical_study_embeddings_sha256": None,
            "canonical_study_manifest_sha256": None,
            "canonical_study_store_receipt_sha256": None,
            "cohort_preservation_receipt_sha256": None,
            "cohort_preservation_second_pass_replay_passed": False,
            "cohort_preservation_passed": False,
        }
    )
    for key in (
        "all_batches_finalized",
        "all_source_receipts_passed",
        "all_dicom_audits_passed",
        "all_extraction_rows_resolved",
        "all_successful_extractions_embedded",
        "all_technical_dispositions_retained",
        "all_no_cine_studies_prespecified",
        "all_embeddings_passed",
        "all_pooling_passed",
        "all_preservation_manifests_passed",
        "all_aggregate_safety_gates_passed",
        "raw_dicoms_retained",
        "extracted_cache_retired",
    ):
        value[key] = True
    for key in (
        "outside_selected_studies_permitted",
        "scientific_inconsistency_repair_performed",
        "identifiers_emitted",
        "restricted_paths_emitted",
    ):
        value[key] = False
    value.update(
        {
            "all_scientific_authority_bindings_identical": True,
            "implementation_authority_epoch_count": 3,
            "r8u_implementation_commit": "c" * 40,
            "r8u_recovery_continuation_authority_sha256": "6" * 64,
        }
    )
    return value


def test_r8u_api_and_closed_schema_exports_are_exact() -> None:
    expected_fields = [
        "implementation_commit",
        "historical_r8r_recovery_authority_sha256",
        "historical_r8r_recovery_terminal_receipt_sha256",
        "historical_r8r_continuation_capacity_receipt_sha256",
        "historical_r8r_continuation_claim_sha256",
        "historical_r8r_continuation_submission_receipt_sha256",
        "failed_partial_seal_sha256",
        "recovery_capacity_receipt_sha256",
        "recovery_authority_sha256",
        "recovery_submission_receipt_sha256",
        "recovery_accounting_sha256",
        "recovery_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "continuation_submission_receipt_sha256",
    ]
    assert [field.name for field in fields(finalizer.R8UImplementationAuthority)] == (
        expected_fields
    )
    assert "r8u_implementation_authority" in inspect.signature(
        finalizer.finalize_receipts
    ).parameters
    assert set(finalizer.R8U_CHAIN_ARTIFACT_KEYS) == {
        specification[0]
        for specification in finalizer.R8U_CHAIN_ARTIFACT_SPECS
    }
    assert finalizer.R8U_CHAIN_ARTIFACT_KEYS[
        "recovery_capacity_receipt_sha256"
    ] == finalizer.r8r_capacity.R8U_CAPACITY_KEYS
    assert finalizer.R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS == {
        "scientific_commit",
        "r8r_implementation_commit",
        "r8u_base_implementation_commit",
        "r8u_projection_repair_commit",
    }
    assert "implementation_authority_epochs" in (
        finalizer.R8U_COMMON_CHAIN_KEYS
    )


def test_r8u_receipts_require_exact_four_commit_authority_chain() -> None:
    implementation_commit = "c" * 40
    expected = {
        "scientific_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "r8r_implementation_commit": (
            finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
        ),
        "r8u_base_implementation_commit": (
            finalizer.R8U_BASE_IMPLEMENTATION_COMMIT
        ),
        "r8u_projection_repair_commit": implementation_commit,
    }
    assert finalizer._r8u_expected_implementation_authority_epochs(
        implementation_commit
    ) == expected
    finalizer._r8u_validate_implementation_authority_epochs(
        expected,
        implementation_commit=implementation_commit,
    )
    for drifted in (
        {key: value for key, value in expected.items() if key != "scientific_commit"},
        {**expected, "unexpected": implementation_commit},
        {**expected, "r8u_projection_repair_commit": "d" * 40},
        {**expected, "r8u_base_implementation_commit": implementation_commit},
    ):
        _expect_code(
            "R8U_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID",
            lambda drifted=drifted: (
                finalizer._r8u_validate_implementation_authority_epochs(
                    drifted,
                    implementation_commit=implementation_commit,
                )
            ),
        )


def test_r8u_loader_crosschecks_epochs_in_every_common_receipt() -> None:
    authority = _authority()
    receipt_hashes = {
        batch_id: hashlib.sha256(batch_id.encode("ascii")).hexdigest()
        for batch_id in finalizer.EXPECTED_BATCH_IDS
    }
    values, _observed, _fresh, _batch16_receipt = _chain_values(
        authority,
        receipt_hashes,
        paths=_receipt_paths(),
        runtime={"runtime": "fixed"},
        script_authority={"script": "fixed"},
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "receipt.json"
        for field, _relative, artifact_type, status in (
            finalizer.R8U_CHAIN_ARTIFACT_SPECS
        ):
            if field == "recovery_capacity_receipt_sha256":
                continue
            value = values[field]
            payload = finalizer.core.canonical_json_bytes(value)
            path.write_bytes(payload)
            path.chmod(0o600)
            bound = replace(
                authority,
                **{field: hashlib.sha256(payload).hexdigest()},
            )
            loaded, _digest = finalizer._load_r8u_chain_artifact(
                path=path,
                field=field,
                artifact_type=artifact_type,
                status=status,
                authority=bound,
                plan={},
            )
            assert loaded["implementation_authority_epochs"] == (
                finalizer._r8u_expected_implementation_authority_epochs(
                    authority.implementation_commit
                )
            )

            drifted = copy.deepcopy(value)
            drifted["implementation_authority_epochs"][
                "r8u_projection_repair_commit"
            ] = "d" * 40
            payload = finalizer.core.canonical_json_bytes(drifted)
            path.write_bytes(payload)
            bound = replace(
                authority,
                **{field: hashlib.sha256(payload).hexdigest()},
            )
            _expect_code(
                "R8U_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID",
                lambda: finalizer._load_r8u_chain_artifact(
                    path=path,
                    field=field,
                    artifact_type=artifact_type,
                    status=status,
                    authority=bound,
                    plan={},
                ),
            )


def test_r8u_capacity_receipt_crosschecks_projection_repair_commit() -> None:
    authority = _authority()
    value: dict[str, Any] = {
        key: 0 for key in finalizer.r8r_capacity.R8U_CAPACITY_KEYS
    }
    value.update(
        {
            "schema_version": 1,
            "artifact_type": "lvef_c3_r8u_batch16_recovery_capacity_v1",
            "status": "PASS_BATCH16_RECOVERY_AND_17_19_WITH_200GB_RESERVE",
            "blocking_reason_codes": [],
            "implementation_authority_epochs": (
                finalizer._r8u_expected_implementation_authority_epochs(
                    authority.implementation_commit
                )
            ),
        }
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "capacity.json"
        payload = finalizer.core.canonical_json_bytes(value)
        path.write_bytes(payload)
        path.chmod(0o600)
        bound = replace(
            authority,
            recovery_capacity_receipt_sha256=(
                hashlib.sha256(payload).hexdigest()
            ),
        )
        with mock.patch.object(
            finalizer.r8r_capacity,
            "validate_fixed_r8u_batch16_recovery_capacity",
            return_value=value,
        ) as validator:
            finalizer._load_r8u_chain_artifact(
                path=path,
                field="recovery_capacity_receipt_sha256",
                artifact_type=value["artifact_type"],
                status=value["status"],
                authority=bound,
                plan={},
            )
        validator.assert_called_once_with(
            {},
            value,
            r8u_projection_repair_commit=authority.implementation_commit,
        )

        value["implementation_authority_epochs"] = {
            **value["implementation_authority_epochs"],
            "r8u_projection_repair_commit": "d" * 40,
        }
        payload = finalizer.core.canonical_json_bytes(value)
        path.write_bytes(payload)
        bound = replace(
            authority,
            recovery_capacity_receipt_sha256=(
                hashlib.sha256(payload).hexdigest()
            ),
        )
        with mock.patch.object(
            finalizer.r8r_capacity,
            "validate_fixed_r8u_batch16_recovery_capacity",
        ) as validator:
            _expect_code(
                "R8U_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID",
                lambda: finalizer._load_r8u_chain_artifact(
                    path=path,
                    field="recovery_capacity_receipt_sha256",
                    artifact_type=value["artifact_type"],
                    status=value["status"],
                    authority=bound,
                    plan={},
                ),
            )
        validator.assert_not_called()


def test_r8u_summary_requires_exact_three_epoch_closure() -> None:
    summary = _valid_r8u_summary()
    finalizer.validate_closed_final_summary(summary)
    wrong_epoch = dict(summary, implementation_authority_epoch_count=2)
    _expect_code(
        "FINAL_SUMMARY_BINDING_INVALID",
        lambda: finalizer.validate_closed_final_summary(wrong_epoch),
    )
    mixed_schema = dict(summary, r8r_implementation_commit="d" * 40)
    _expect_code(
        "FINAL_SUMMARY_SCHEMA_MISMATCH",
        lambda: finalizer.validate_closed_final_summary(mixed_schema),
    )


def test_r8u_accepts_only_exact_2_plus_13_plus_4_partition() -> None:
    receipts, hashes, sizes, runtime, current_epoch = _epoch_receipts()
    authority = _authority()
    with (
        mock.patch.object(
            finalizer.core,
            "validate_runtime_authority",
            return_value=runtime,
        ),
        mock.patch.object(
            finalizer,
            "_current_r8r_implementation_epoch",
            return_value=current_epoch,
        ),
        mock.patch.object(
            finalizer, "_validate_r8u_repository_authority"
        ),
        mock.patch.object(
            finalizer,
            "_validate_r8u_chain_artifacts",
            return_value="d" * 64,
        ),
    ):
        observed = finalizer._validate_r8u_mixed_implementation_epochs(
            receipts,
            receipt_hashes_by_batch=hashes,
            receipt_sizes_by_batch=sizes,
            receipt_paths_by_batch=_receipt_paths(),
            expected_governing_commit=(
                finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
            ),
            expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
            expected_runtime_authority=runtime,
            authority=authority,
            plan={},
        )
        assert observed == "d" * 64

        wrong_split = copy.deepcopy(receipts)
        wrong_split[14][finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS[0]] = (
            current_epoch[0]
        )
        _expect_code(
            "R8U_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH",
            lambda: finalizer._validate_r8u_mixed_implementation_epochs(
                wrong_split,
                receipt_hashes_by_batch=hashes,
                receipt_sizes_by_batch=sizes,
                receipt_paths_by_batch=_receipt_paths(),
                expected_governing_commit=(
                    finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
                ),
                expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
                expected_runtime_authority=runtime,
                authority=authority,
                plan={},
            ),
        )


def test_r8u_stage_authority_audit_uses_only_current_four_receipts() -> None:
    receipts = [{"index": index} for index in range(19)]
    assert finalizer._stage_authority_receipts(
        receipts, r8r_mode=False, r8u_mode=True
    ) == receipts[15:]
    assert finalizer._stage_authority_receipts(
        receipts, r8r_mode=True, r8u_mode=False
    ) == receipts[2:]
    assert finalizer._stage_authority_receipts(
        receipts, r8r_mode=False, r8u_mode=False
    ) == receipts


def test_r8u_repository_authority_requires_direct_child_of_fixed_r8u_base() -> None:
    implementation_commit = "c" * 40
    historical_payloads = {
        key: f"historical:{key}".encode("ascii")
        for key in finalizer.R8U_FE3_GIT_TREE_PATHS
    }
    historical_hashes = {
        key: hashlib.sha256(payload).hexdigest()
        for key, payload in historical_payloads.items()
    }

    def result(arguments: list[str], *, wrong_parent: bool = False, **_kwargs: Any):
        tail = arguments[3:]
        stdout = b""
        if tuple(tail) in {
            ("rev-parse", "HEAD"),
            (
                "rev-parse",
                "refs/remotes/origin/codex/lvef-multitask-revalidation",
            ),
        }:
            stdout = f"{implementation_commit}\n".encode("ascii")
        elif tail == ["branch", "--show-current"]:
            stdout = b"codex/lvef-multitask-revalidation\n"
        elif tail[:4] == ["rev-list", "--parents", "-n", "1"]:
            commit = tail[4]
            if commit == implementation_commit:
                parent = (
                    "d" * 40
                    if wrong_parent
                    else finalizer.R8U_BASE_IMPLEMENTATION_COMMIT
                )
            elif commit == finalizer.R8U_BASE_IMPLEMENTATION_COMMIT:
                parent = finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
            else:
                assert commit == finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
                parent = finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
            stdout = f"{commit} {parent}\n".encode("ascii")
        elif tail[:2] == ["rev-list", "--count"]:
            revision_range = tail[2]
            distances = {
                (
                    f"{finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT}.."
                    f"{finalizer.R8U_BASE_IMPLEMENTATION_COMMIT}"
                ): b"2\n",
                (
                    f"{finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT}.."
                    f"{implementation_commit}"
                ): b"3\n",
            }
            stdout = distances.get(revision_range, b"1\n")
        elif tail and tail[0] == "show":
            path = tail[1].split(":", 1)[1]
            key = next(
                key
                for key, expected_path in finalizer.R8U_FE3_GIT_TREE_PATHS.items()
                if path == expected_path
            )
            stdout = historical_payloads[key]
        return subprocess.CompletedProcess(arguments, 0, stdout, b"")

    with (
        mock.patch.object(
            finalizer, "R8U_FE3_GIT_TREE_SHA256", historical_hashes
        ),
        mock.patch.object(
            finalizer.subprocess,
            "run",
            side_effect=lambda arguments, **kwargs: result(
                arguments, **kwargs
            ),
        ),
    ):
        finalizer._validate_r8u_repository_authority(implementation_commit)
    with (
        mock.patch.object(
            finalizer, "R8U_FE3_GIT_TREE_SHA256", historical_hashes
        ),
        mock.patch.object(
            finalizer.subprocess,
            "run",
            side_effect=lambda arguments, **kwargs: result(
                arguments, wrong_parent=True, **kwargs
            ),
        ),
    ):
        _expect_code(
            "R8U_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH",
            lambda: finalizer._validate_r8u_repository_authority(
                implementation_commit
            ),
        )


def _chain_values(
    authority: Any,
    receipt_hashes: dict[str, str],
    *,
    paths: Mapping[str, Path],
    runtime: Mapping[str, Any],
    script_authority: Mapping[str, str],
):
    observed = {
        field: getattr(authority, field)
        for field, *_rest in finalizer.R8U_CHAIN_ARTIFACT_SPECS
    }
    prefix15 = [
        finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES[
            f"c3_batch_{index:03d}"
        ][1]
        for index in range(15)
    ]
    historical = {
        "recovery_authority_sha256": (
            authority.historical_r8r_recovery_authority_sha256
        ),
        "recovery_terminal_receipt_sha256": (
            authority.historical_r8r_recovery_terminal_receipt_sha256
        ),
        "continuation_capacity_receipt_sha256": (
            authority.historical_r8r_continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": (
            authority.historical_r8r_continuation_claim_sha256
        ),
        "continuation_submission_receipt_sha256": (
            authority.historical_r8r_continuation_submission_receipt_sha256
        ),
    }
    recovery_job_id = "801"
    array_job_id = "802"
    finalizer_job_id = "803"
    qsub_environment_sha256 = "4" * 64
    retained_raw = {
        "raw_dicom_files": finalizer.R8U_BATCH16_RAW_FILES,
        "raw_dicom_bytes": finalizer.R8U_BATCH16_RAW_BYTES,
        "raw_metadata_projection_sha256": "1" * 64,
        "download_control_projection_sha256": "2" * 64,
        "download_ledger_sha256": (
            finalizer.R8U_BATCH16_DOWNLOAD_LEDGER_SHA256
        ),
        "verified_download_manifest_sha256": (
            finalizer.R8U_BATCH16_VERIFIED_MANIFEST_SHA256
        ),
        "selected_batch_manifest_sha256": (
            finalizer.R8U_BATCH16_SELECTED_MANIFEST_SHA256
        ),
        "historical_st_dev_required": False,
        "raw_dicom_body_reads": 0,
        "cloud_requests": 0,
    }
    common = {
        "schema_version": 1,
        "original_scientific_commit": (
            finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
        ),
        "implementation_commit": authority.implementation_commit,
        "implementation_authority_epochs": (
            finalizer._r8u_expected_implementation_authority_epochs(
                authority.implementation_commit
            )
        ),
        "attempt_id": finalizer.R8R_ATTEMPT_ID,
        "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
    }
    raw_content = {
        **retained_raw,
        "raw_dicom_body_reads": finalizer.R8U_BATCH16_RAW_FILES,
        "status": "PASS_BATCH16_RETAINED_RAW_CONTENT_AUTHORITY",
    }
    terminal_support_sha256 = hashlib.sha256(b"terminal-support").hexdigest()
    fresh = {
        **common,
        "artifact_type": (
            "lvef_c3_r8u_fresh_batch16_extraction_publication_v1"
        ),
        "status": "PASS_FRESH_BATCH16_EXTRACTION_PUBLISHED_NO_CLOBBER",
        "batch_id": "c3_batch_015",
        "failed_partial_seal_sha256": observed[
            "failed_partial_seal_sha256"
        ],
        "raw_content_authority": raw_content,
        "stage_completion_receipt_sha256": terminal_support_sha256,
        "fresh_stage_promoted_to_canonical": True,
        "canonical_target_absent_before_promotion": True,
        "failed_partial_modified": False,
        "partial_npz_adopted": 0,
        "cloud_requests": 0,
        "download_reruns": 0,
        "dicom_extraction_reruns": 1,
    }
    fresh_sha256 = hashlib.sha256(
        finalizer.core.canonical_json_bytes(fresh)
    ).hexdigest()
    batch16_receipt = {
        "batch_id": "c3_batch_015",
        "n_selected_studies": 250,
        "n_expected_objects": finalizer.R8U_BATCH16_RAW_FILES,
        "expected_source_bytes": finalizer.R8U_BATCH16_RAW_BYTES,
        "n_successfully_extracted_cines": 100,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_clip_embeddings": 100,
        "n_pooled_studies": 250,
        "n_no_cine_studies": 0,
        "n_new_no_cine_studies": 0,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
        "cache_retirement_authorization_sha256": terminal_support_sha256,
    }
    receipt_hashes["c3_batch_015"] = hashlib.sha256(
        finalizer.core.canonical_json_bytes(batch16_receipt)
    ).hexdigest()
    zero_science = {
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }
    values = {
        "failed_partial_seal_sha256": {
            "batch_id": "c3_batch_015",
            "original_task_id": 16,
            "failed_array_job_id": "7292691",
            "failure_class": "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS",
            "observation": {
                "file_count": 4_757,
                "directory_count": 259,
                "total_bytes": 8_583_119_701,
                "symlink_count": 0,
                "nonregular_count": 0,
                "metadata_projection_sha256": (
                    finalizer.R8U_FAILED_PARTIAL_METADATA_SHA256
                ),
                "npz_body_reads": 0,
            },
            "partial_outputs_adopted": False,
            "partial_outputs_modified": False,
            "partial_outputs_deleted": False,
            "partial_outputs_renamed": False,
            "npz_body_reads": 0,
        },
        "recovery_capacity_receipt_sha256": {},
        "recovery_authority_sha256": {
            "prior_implementation_commit": (
                finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
            ),
            "batch_id": "c3_batch_015",
            "original_task_id": 16,
            "continuation_task_range": "17-19",
            "prefix_final_receipt_sha256": prefix15,
            "historical_r8r_chain_authority": historical,
            "failed_partial_seal_sha256": observed[
                "failed_partial_seal_sha256"
            ],
            "recovery_capacity_sha256": observed[
                "recovery_capacity_receipt_sha256"
            ],
            "retained_raw_authority": retained_raw,
            "runtime_authority_sha256": (
                finalizer.core.canonical_json_sha256(runtime)
            ),
            "qsub_environment_sha256": qsub_environment_sha256,
            "script_authority": dict(script_authority),
            "runtime_validation_context": "SEALED_SCHEDULER_RUNTIME_REPLAY",
            "fresh_extraction_relative_root": (
                "r8u_batch16_recovery/fresh_extracted_cache/c3_batch_015"
            ),
            "cloud_requests_authorized": 0,
            "download_reruns_authorized": 0,
            "dicom_extraction_reruns_authorized": 1,
            "echoprime_reruns_authorized": 1,
            "gpu_executions_authorized": 1,
            "failed_partial_adoption_authorized": False,
            "failed_partial_mutation_authorized": False,
            "raw_dicom_deletion_authorized": False,
            "model_fitting_authorized": False,
            "prediction_authorized": False,
            "confirmatory_performance_access_authorized": False,
            "maximum_new_qsub_submissions": 3,
        },
        "recovery_submission_receipt_sha256": {
            "batch_id": "c3_batch_015",
            "original_task_id": 16,
            "recovery_job_name": (
                f"lvef_c3_r8u_rec_{authority.implementation_commit[:8]}"
            ),
            "recovery_job_id": recovery_job_id,
            "recovery_qsub_argv_sha256": "6" * 64,
            "qsub_environment_sha256": qsub_environment_sha256,
            "recovery_authority_sha256": observed[
                "recovery_authority_sha256"
            ],
            "failed_partial_seal_sha256": observed[
                "failed_partial_seal_sha256"
            ],
            "recovery_capacity_sha256": observed[
                "recovery_capacity_receipt_sha256"
            ],
            "recovery_qsub_evidence": _qsub_evidence(b"801\n"),
            "scheduler_submission_count": 1,
            "recovery_is_array": False,
            "gpu_requested": True,
            "automatic_retry_authorized": False,
            "cloud_requests": 0,
            "download_reruns": 0,
            **zero_science,
        },
        "recovery_accounting_sha256": {
            "batch_id": "c3_batch_015",
            "original_task_id": 16,
            "recovery_job_id": recovery_job_id,
            "failed": 0,
            "exit_status": 0,
            "accounting_projection": {
                "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
                "job_id": recovery_job_id,
                "task_id": "undefined",
                "failed": 0,
                "exit_status": 0,
                "start_time": "Thu Aug 20 14:51:18 2026",
                "end_time": "Thu Aug 20 16:42:24 2026",
                "ru_wallclock_seconds": "6666.0",
            },
        },
        "recovery_terminal_receipt_sha256": {
            "batch_id": "c3_batch_015",
            "original_task_id": 16,
            "failed_partial_seal_sha256": observed[
                "failed_partial_seal_sha256"
            ],
            "recovery_capacity_sha256": observed[
                "recovery_capacity_receipt_sha256"
            ],
            "recovery_authority_sha256": observed[
                "recovery_authority_sha256"
            ],
            "recovery_submission_receipt_sha256": observed[
                "recovery_submission_receipt_sha256"
            ],
            "fresh_extraction_publication_sha256": fresh_sha256,
            "preservation_receipt_sha256": terminal_support_sha256,
            "cache_retirement_authorization_sha256": (
                terminal_support_sha256
            ),
            "cache_retirement_transition_sha256": terminal_support_sha256,
            "final_ledger_sha256": terminal_support_sha256,
            "batch_finalization_receipt_sha256": receipt_hashes[
                "c3_batch_015"
            ],
            **{
                key: batch16_receipt[key]
                for key in (
                    "n_selected_studies",
                    "n_expected_objects",
                    "expected_source_bytes",
                    "n_successfully_extracted_cines",
                    "n_object_technical_dispositions",
                    "n_blocking_failures",
                    "n_clip_embeddings",
                    "n_pooled_studies",
                    "n_no_cine_studies",
                    "n_new_no_cine_studies",
                    "object_substitution_count",
                    "unaccounted_multiframe_objects",
                )
            },
            "raw_dicoms_retained": True,
            "fresh_recovery_cache_retired": True,
            "failed_partial_cache_retained": True,
            "batch16_raw_reused": True,
            "failed_partial_files": 4_757,
            "failed_partial_bytes": 8_583_119_701,
            "cloud_requests": 0,
            "download_reruns": 0,
            "dicom_extraction_reruns": 1,
            "echoprime_reruns": 1,
            "embedding_generations": 1,
            "gpu_executions": 1,
            **zero_science,
        },
        "continuation_claim_sha256": {
            "prior_implementation_commit": (
                finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
            ),
            "prefix_final_receipt_sha256": [
                *prefix15,
                receipt_hashes["c3_batch_015"],
            ],
            "failed_partial_seal_sha256": observed[
                "failed_partial_seal_sha256"
            ],
            "recovery_capacity_sha256": observed[
                "recovery_capacity_receipt_sha256"
            ],
            "recovery_authority_sha256": observed[
                "recovery_authority_sha256"
            ],
            "recovery_submission_receipt_sha256": observed[
                "recovery_submission_receipt_sha256"
            ],
            "recovery_accounting_sha256": observed[
                "recovery_accounting_sha256"
            ],
            "recovery_terminal_receipt_sha256": observed[
                "recovery_terminal_receipt_sha256"
            ],
            "runtime_authority_sha256": (
                finalizer.core.canonical_json_sha256(runtime)
            ),
            "qsub_environment_sha256": qsub_environment_sha256,
            "script_authority": dict(script_authority),
            "continuation_task_range": "17-19",
            "continuation_task_count": 3,
            "continuation_max_concurrency": 1,
            "held_finalizer_count": 1,
            "total_new_qsub_maximum": 3,
            "automatic_retry_authorized": False,
            "whole_stage_retry_authorized": False,
            "fourth_submission_reachable": False,
            "cloud_requests_by_submitter": 0,
            "dicom_body_reads_by_submitter": 0,
            "npz_body_reads_by_submitter": 0,
            "gpu_executions_by_submitter": 0,
            "embedding_generations_by_submitter": 0,
            "model_fitting_authorized": False,
            "prediction_authorized": False,
            "confirmatory_performance_access_authorized": False,
        },
        "continuation_submission_receipt_sha256": {
            "recovery_job_id": recovery_job_id,
            "array_job_name": (
                f"lvef_c3_r8u_seq_{authority.implementation_commit[:8]}"
            ),
            "finalizer_job_name": (
                f"lvef_c3_r8u_fin_{authority.implementation_commit[:8]}"
            ),
            "array_job_id": array_job_id,
            "finalizer_job_id": finalizer_job_id,
            "array_qsub_argv_sha256": "6" * 64,
            "finalizer_qsub_argv_sha256": "6" * 64,
            "qsub_environment_sha256": qsub_environment_sha256,
            "failed_partial_seal_sha256": observed[
                "failed_partial_seal_sha256"
            ],
            "recovery_capacity_sha256": observed[
                "recovery_capacity_receipt_sha256"
            ],
            "recovery_authority_sha256": observed[
                "recovery_authority_sha256"
            ],
            "recovery_accounting_sha256": observed[
                "recovery_accounting_sha256"
            ],
            "recovery_terminal_receipt_sha256": observed[
                "recovery_terminal_receipt_sha256"
            ],
            "continuation_claim_sha256": observed[
                "continuation_claim_sha256"
            ],
            "array_qsub_evidence": _qsub_evidence(b"802.17-19:1\n"),
            "finalizer_qsub_evidence": _qsub_evidence(b"803\n"),
            "scheduler_submission_count": 2,
            "total_new_qsub_submissions": 3,
            "scheduler_submission_maximum": 3,
            "array_task_range": "17-19",
            "array_task_count": 3,
            "array_max_concurrency": 1,
            "finalizer_held_on_array": True,
            "whole_stage_retry_authorized": False,
            "fourth_submission_reachable": False,
            "cloud_requests": 0,
            "dicom_body_reads_by_submitter": 0,
            "npz_body_reads_by_submitter": 0,
            "gpu_executions_by_submitter": 0,
            **zero_science,
        },
    }
    for field, _path, artifact_type, status in (
        finalizer.R8U_CHAIN_ARTIFACT_SPECS
    ):
        if field == "recovery_capacity_receipt_sha256":
            values[field]["implementation_authority_epochs"] = common[
                "implementation_authority_epochs"
            ]
        else:
            values[field] = {
                **common,
                "artifact_type": artifact_type,
                "status": status,
                **values[field],
            }
    return values, observed, fresh, batch16_receipt


def test_r8u_chain_binds_historical_chain_and_failed_partial_is_never_receipt() -> None:
    authority = _authority()
    paths = _receipt_paths()
    receipt_hashes = {
        batch_id: hashlib.sha256(batch_id.encode("ascii")).hexdigest()
        for batch_id in finalizer.EXPECTED_BATCH_IDS
    }
    for batch_id, (_size, digest) in (
        finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES.items()
    ):
        receipt_hashes[batch_id] = digest
    receipt_hashes["c3_batch_015"] = "f" * 64
    runtime = {"runtime": "fixed"}
    script_authority = {"script": "fixed"}
    values, observed, fresh, batch16_receipt = _chain_values(
        authority,
        receipt_hashes,
        paths=paths,
        runtime=runtime,
        script_authority=script_authority,
    )

    def load_artifact(**kwargs: Any):
        field = kwargs["field"]
        return values[field], observed[field]

    def stable(path: Path, **_kwargs: Any) -> bytes:
        if path.name == "fresh_extraction_publication.restricted.json":
            return finalizer.core.canonical_json_bytes(fresh)
        if path.name == "batch_finalization_receipt.restricted.json":
            return finalizer.core.canonical_json_bytes(batch16_receipt)
        return b"terminal-support"

    with (
        mock.patch.object(
            finalizer,
            "_validate_r8r_chain_artifacts",
            return_value="e" * 64,
        ) as historical,
        mock.patch.object(
            finalizer,
            "_load_r8u_chain_artifact",
            side_effect=load_artifact,
        ),
        mock.patch.object(
            finalizer,
            "_stable_nofollow_bytes",
            side_effect=stable,
        ),
        mock.patch.object(
            finalizer,
            "_r8r_current_script_authority",
            return_value=script_authority,
        ),
        mock.patch.object(
            finalizer,
            "_r8r_controller_json_sha256",
            return_value="6" * 64,
        ),
    ):
        digest = finalizer._validate_r8u_chain_artifacts(
            receipt_paths_by_batch=paths,
            receipt_hashes_by_batch=receipt_hashes,
            authority=authority,
            expected_runtime_authority=runtime,
            plan={},
        )
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    historical_authority = historical.call_args.kwargs["authority"]
    assert historical_authority.implementation_commit == (
        finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT
    )
    assert historical.call_args.kwargs["historical_script_authority"] == (
        finalizer.R8U_FE3_GIT_TREE_SHA256
    )

    drifted_historical = replace(
        authority,
        historical_r8r_recovery_authority_sha256="a" * 64,
    )
    with (
        mock.patch.object(
            finalizer,
            "_validate_r8r_chain_artifacts",
            return_value="e" * 64,
        ),
        mock.patch.object(
            finalizer,
            "_load_r8u_chain_artifact",
            side_effect=load_artifact,
        ),
    ):
        _expect_code(
            "R8U_FINALIZER_HISTORICAL_CHAIN_AUTHORITY_MISMATCH",
            lambda: finalizer._validate_r8u_chain_artifacts(
                receipt_paths_by_batch=paths,
                receipt_hashes_by_batch=receipt_hashes,
                authority=drifted_historical,
                expected_runtime_authority=runtime,
                plan={},
            ),
        )

    receipt_hashes_with_seal = dict(receipt_hashes)
    receipt_hashes_with_seal["c3_batch_015"] = (
        authority.failed_partial_seal_sha256
    )
    with mock.patch.object(
        finalizer, "_validate_r8r_chain_artifacts"
    ) as historical:
        _expect_code(
            "R8U_FINALIZER_CHAIN_ARTIFACT_INVALID",
            lambda: finalizer._validate_r8u_chain_artifacts(
                receipt_paths_by_batch=paths,
                receipt_hashes_by_batch=receipt_hashes_with_seal,
                authority=authority,
                expected_runtime_authority={},
                plan={},
            ),
        )
        historical.assert_not_called()


def test_r8u_end_to_end_all_19_recovered_batches_3_and_16_finalize() -> None:
    import lvef_reconstruction_smoke as smoke
    import numpy as np

    def write_csv(
        path: Path, header: Any, rows: list[Mapping[str, Any]]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(header), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        path.chmod(0o600)

    real_sha256_file = finalizer.sha256_file
    authority = _authority()
    current_epoch = finalizer._current_r8r_implementation_epoch()
    original_epoch = tuple(
        hashlib.sha256(f"original-epoch-{index}".encode("ascii")).hexdigest()
        for index in range(5)
    )
    assert len({original_epoch, finalizer.R8U_FE3_IMPLEMENTATION_EPOCH, current_epoch}) == 3

    runtime = {
        key: hashlib.sha256(f"runtime:{key}".encode("ascii")).hexdigest()
        for key in finalizer.core.RUNTIME_AUTHORITY_KEYS
        if key != "git_commit"
    }
    runtime.update(
        {
            "git_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
            "orchestration_contract_sha256": "1" * 64,
            "checkpoint_sha256": "2" * 64,
            "environment_receipt_sha256": "3" * 64,
        }
    )
    plan_no_cine_sha256 = finalizer.core.canonical_json_sha256([])

    with tempfile.TemporaryDirectory() as directory:
        production_root = Path(directory).resolve() / "production"
        production_root.mkdir(mode=0o700)
        attempt_root = (
            production_root / "attempts" / finalizer.R8R_ATTEMPT_ID
        )
        attempt_root.mkdir(parents=True, mode=0o700)
        cache_authority_root = attempt_root / "cache_authority"
        cache_authority_root.mkdir(mode=0o700)
        canonical_output_root = attempt_root / "cohort_finalization"
        canonical_output_root.mkdir(mode=0o700)

        plan_batches: list[dict[str, Any]] = []
        receipt_paths: list[Path] = []
        transitions: dict[str, dict[str, Any]] = {}
        source_bytes_total = 0

        for index, batch_id in enumerate(finalizer.EXPECTED_BATCH_IDS):
            subject_id = str(10_001 + index)
            study_id = str(20_001 + index)
            source_key = hashlib.sha256(
                f"source:{batch_id}".encode("ascii")
            ).hexdigest()
            clip_key = hashlib.sha256(
                f"clip:{batch_id}".encode("ascii")
            ).hexdigest()
            source_bytes = index + 1
            source_bytes_total += source_bytes
            plan_batches.append(
                {
                    "batch_id": batch_id,
                    "n_studies": 1,
                    "n_subjects": 1,
                    "n_objects": 1,
                    "source_bytes": source_bytes,
                    "expected_no_cine_studies": 0,
                    "prespecified_no_cine_study_keys": [],
                    "prespecified_no_cine_study_set_sha256": (
                        plan_no_cine_sha256
                    ),
                    "studies": [
                        {"subject_id": subject_id, "study_id": study_id}
                    ],
                    "objects": [
                        {
                            "subject_id": subject_id,
                            "study_id": study_id,
                            "source_object_key": source_key,
                        }
                    ],
                }
            )

            batch_root = attempt_root / "batches" / batch_id
            echoprime_root = batch_root / "echoprime"
            preservation_root = batch_root / "preservation"
            extraction_root = (
                attempt_root
                / "extracted_cache"
                / batch_id
                / "dicom_extraction"
            )
            echoprime_root.mkdir(parents=True)
            preservation_root.mkdir(parents=True)
            extraction_root.mkdir(parents=True)

            vector = np.zeros((1, 512), dtype=np.float32)
            vector[0, index] = np.float32(index + 1)
            embedding_sha256 = smoke.array_content_sha256(vector[0])
            clip_embeddings_path = (
                echoprime_root / "clip_embeddings.restricted.npz"
            )
            study_embeddings_path = (
                echoprime_root / "study_embeddings.restricted.npz"
            )
            np.savez(clip_embeddings_path, embeddings=vector)
            np.savez(study_embeddings_path, embeddings=vector)

            clip_manifest_path = (
                echoprime_root / "clip_manifest.restricted.csv"
            )
            write_csv(
                clip_manifest_path,
                finalizer.CLIP_MANIFEST_HEADER,
                [
                    {
                        "embedding_idx": 0,
                        "subject_id": subject_id,
                        "study_id": study_id,
                        "clip_key": clip_key,
                        "physical_source_key": source_key,
                        "embedding_l2_norm": str(float(index + 1)),
                        "embedding_sha256": embedding_sha256,
                        "write_ok": "True",
                    }
                ],
            )
            study_manifest_path = (
                echoprime_root / "study_manifest.restricted.csv"
            )
            write_csv(
                study_manifest_path,
                finalizer.STUDY_MANIFEST_HEADER,
                [
                    {
                        "study_idx": 0,
                        "subject_id": subject_id,
                        "study_id": study_id,
                        "n_clips": 1,
                        "embedding_sha256": embedding_sha256,
                    }
                ],
            )
            write_csv(
                echoprime_root / "study_disposition.restricted.csv",
                finalizer.DISPOSITION_HEADER,
                [
                    {
                        "subject_id": subject_id,
                        "study_id": study_id,
                        "disposition": "IMAGING_ELIGIBLE",
                    }
                ],
            )

            extraction_manifest_path = (
                extraction_root / "extraction_manifest.restricted.csv"
            )
            extraction_row = {
                key: "synthetic"
                for key in finalizer.preservation.EXTRACTION_MANIFEST_HEADER
            }
            extraction_row.update(
                {
                    "subject_id": subject_id,
                    "study_id": study_id,
                    "clip_key": clip_key,
                    "physical_source_key": source_key,
                    "write_ok": "True",
                    "npz_sha256": embedding_sha256,
                    "failure_substage": "",
                }
            )
            write_csv(
                extraction_manifest_path,
                finalizer.preservation.EXTRACTION_MANIFEST_HEADER,
                [extraction_row],
            )
            technical_path = (
                extraction_root
                / "technical_disposition_manifest.restricted.csv"
            )
            write_csv(
                technical_path,
                finalizer.production_stages.
                TECHNICAL_DISPOSITION_MANIFEST_HEADER,
                [],
            )
            dicom_audit_path = extraction_root / "dicom_audit.restricted.csv"
            dicom_audit_path.write_text("synthetic-dicom-audit\n", encoding="utf-8")

            source_ledger_path = (
                batch_root / "download_resume_ledger.restricted.json"
            )
            pooling_ledger_path = (
                batch_root / "pooling_resume_ledger.restricted.json"
            )
            preservation_manifest_path = (
                preservation_root / "batch_preservation_manifest.restricted.tsv"
            )
            staged_receipt_path = (
                preservation_root / "cache_atomically_staged.restricted.json"
            )
            for path, payload in (
                (source_ledger_path, b"{}\n"),
                (pooling_ledger_path, b"{}\n"),
                (preservation_manifest_path, b"synthetic-preservation\n"),
                (staged_receipt_path, b"{}\n"),
            ):
                path.write_bytes(payload)

            cache_authority_path = (
                cache_authority_root / f"{batch_id}.authorization.json"
            )
            cache_authority_path.write_text("{}\n", encoding="utf-8")
            cache_authority_path.chmod(0o600)

            if index < 2:
                epoch = original_epoch
            elif index < 15:
                epoch = finalizer.R8U_FE3_IMPLEMENTATION_EPOCH
            else:
                epoch = current_epoch
            scheduler_identity = f"synthetic_{index + 1}"
            if index == 2:
                scheduler_identity = "recovered_batch3"
            elif index == 15:
                scheduler_identity = "recovered_batch16"
            receipt: dict[str, Any] = {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_finalization_receipt_v3",
                "status": "PASS_BATCH_FINALIZED",
                "batch_id": batch_id,
                "attempt_id": finalizer.R8R_ATTEMPT_ID,
                "governing_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
                "source_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
                "run_timestamp_utc": "2026-08-28T12:00:00+00:00",
                "cohort_version": "mimic-iv-echo/1.0",
                "split_version": f"split_map_sha256:{'4' * 64}",
                "execution_contract_version": 2,
                "orchestration_contract_sha256": "1" * 64,
                "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
                "checkpoint_sha256": "2" * 64,
                "checkpoint_checksum": "2" * 64,
                "environment_receipt_sha256": "3" * 64,
                "python_version": "3.10",
                "pytorch_version": "2.0",
                "torchvision_version": "0.15",
                "cuda_version": "12.0",
                "cudnn_version": "9",
                "package_inventory_sha256": "5" * 64,
                **dict(
                    zip(
                        finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS,
                        epoch,
                        strict=True,
                    )
                ),
                "config_checksum": "6" * 64,
                "scheduler_job_identity": scheduler_identity,
                "state_input_ledger_sha256": real_sha256_file(
                    pooling_ledger_path
                ),
                "n_selected_studies": 1,
                "n_selected_subjects": 1,
                "n_expected_objects": 1,
                "expected_source_bytes": source_bytes,
                "n_download_verified": 1,
                "n_dicom_readable": 1,
                "n_dicom_unreadable": 0,
                "n_multiframe_cines": 1,
                "n_single_frame_objects": 0,
                "n_extracted_clips": 1,
                "n_successfully_extracted_cines": 1,
                "n_object_technical_dispositions": 0,
                "n_blocking_failures": 0,
                "n_studies_affected_by_technical_disposition": 0,
                "n_new_no_cine_studies": 0,
                "technical_disposition_counts_by_class": {
                    "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": 0
                },
                "technical_disposition_policy_version": (
                    finalizer.production_stages.
                    OBJECT_TECHNICAL_DISPOSITION_POLICY_VERSION
                ),
                "technical_disposition_manifest_sha256": (
                    finalizer.production_stages.
                    technical_disposition_manifest_sha256(technical_path)
                ),
                "prespecified_no_cine_study_set_sha256": (
                    plan_no_cine_sha256
                ),
                "all_no_cine_studies_prespecified": True,
                "all_extraction_rows_resolved": True,
                "all_successful_extractions_embedded": True,
                "all_technical_dispositions_retained": True,
                "object_substitution_count": 0,
                "unaccounted_multiframe_objects": 0,
                "n_unique_clip_keys": 1,
                "n_clip_embeddings": 1,
                "n_pooled_studies": 1,
                "n_no_cine_studies": 0,
                "no_cine_disposition": "NONE",
                "n_outside_selected_studies": 0,
                "n_missing_selected_studies": 0,
                "n_duplicate_physical_sources": 0,
                "n_duplicate_clip_keys": 0,
                "n_nonfinite_embeddings": 0,
                "n_wrong_dimension_embeddings": 0,
                "source_receipt_sha256": real_sha256_file(
                    source_ledger_path
                ),
                "dicom_audit_sha256": real_sha256_file(dicom_audit_path),
                "extraction_manifest_sha256": real_sha256_file(
                    extraction_manifest_path
                ),
                "clip_manifest_sha256": real_sha256_file(
                    clip_manifest_path
                ),
                "clip_embeddings_sha256": real_sha256_file(
                    clip_embeddings_path
                ),
                "study_manifest_sha256": real_sha256_file(
                    study_manifest_path
                ),
                "study_embeddings_sha256": real_sha256_file(
                    study_embeddings_path
                ),
                "preservation_manifest_sha256": real_sha256_file(
                    preservation_manifest_path
                ),
                "cache_retirement_authorization_sha256": real_sha256_file(
                    cache_authority_path
                ),
                "cache_tree_sha256": hashlib.sha256(
                    f"retired:{batch_id}".encode("ascii")
                ).hexdigest(),
                "cache_atomically_staged_receipt_sha256": real_sha256_file(
                    staged_receipt_path
                ),
                "source_gate_passed": True,
                "download_gate_passed": True,
                "dicom_audit_gate_passed": True,
                "extraction_gate_passed": True,
                "embedding_gate_passed": True,
                "pooling_gate_passed": True,
                "study_pooling_semantics_gate_passed": True,
                "preservation_gate_passed": True,
                "aggregate_safety_gate_passed": True,
                "aggregate_safety_gate_result": "PASS",
                "raw_dicoms_retained": True,
                "extracted_cache_retired": True,
            }
            receipt_path = (
                preservation_root
                / "batch_finalization_receipt.restricted.json"
            )
            body = json.dumps(
                receipt, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if batch_id in finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES:
                target_size = finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES[
                    batch_id
                ][0]
                assert len(body) + 1 <= target_size
                body += b"\n" + b" " * (target_size - len(body) - 1)
            else:
                body += b"\n"
            receipt_path.write_bytes(body)
            receipt_paths.append(receipt_path)

        plan = {
            "contract_id": "synthetic-r8u-finalizer",
            "cohort": {
                "release": "mimic-iv-echo/1.0",
                "selected_studies": 19,
                "selected_subjects": 19,
                "normalized_source_objects": 19,
                "selected_source_bytes": source_bytes_total,
                "expected_no_cine_studies": 0,
                "prespecified_no_cine_study_set_sha256": (
                    plan_no_cine_sha256
                ),
            },
            "batches": plan_batches,
        }
        plan_path = attempt_root / "full_batch_plan.restricted.json"
        plan_path.write_text(
            json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8"
        )

        prefix_paths = {
            receipt_paths[index]: finalizer.R8U_PREFIX_RECEIPT_AUTHORITIES[
                f"c3_batch_{index:03d}"
            ][1]
            for index in range(15)
        }

        def synthetic_sha256_file(path: Path) -> str:
            path = Path(path)
            if path == plan_path:
                return finalizer.R8R_BATCH_PLAN_SHA256
            if path in prefix_paths:
                return prefix_paths[path]
            return real_sha256_file(path)

        for index, receipt_path in enumerate(receipt_paths):
            batch_id = f"c3_batch_{index:03d}"
            receipt_sha256 = synthetic_sha256_file(receipt_path)
            transition = {
                "from_state": "CACHE_RETIREMENT_ELIGIBLE",
                "to_state": "FINALIZED",
                "output_manifest_sha256": receipt_sha256,
            }
            transition_path = (
                receipt_path.parent
                / "cache_retirement_finalized.restricted.json"
            )
            transition_path.write_text(
                json.dumps(transition, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            transitions[batch_id] = transition

        def synthetic_final_ledger(path: Path) -> dict[str, Any]:
            batch_id = next(
                part
                for part in Path(path).parts
                if re.fullmatch(r"c3_batch_[0-9]{3}", part)
            )
            return {
                "status": "COMPLETE",
                "batches": {
                    batch_id: {
                        "state": "FINALIZED",
                        "events": [
                            {
                                "receipt_sha256": (
                                    finalizer.core.canonical_json_sha256(
                                        transitions[batch_id]
                                    )
                                )
                            }
                        ],
                    }
                },
            }

        requirements = SimpleNamespace(
            batch_count=19,
            selected_studies=19,
            selected_subjects=19,
            normalized_source_objects=19,
            selected_source_bytes=source_bytes_total,
        )
        repository_boundary = mock.Mock()
        chain_boundary = mock.Mock(return_value="a" * 64)
        with (
            mock.patch.object(
                finalizer, "sha256_file", side_effect=synthetic_sha256_file
            ),
            mock.patch.object(
                finalizer.core,
                "validate_current_batch_plan_v3",
                return_value=finalizer.R8R_BATCH_PLAN_SHA256,
            ),
            mock.patch.object(
                finalizer, "_validate_r8u_repository_authority",
                repository_boundary,
            ),
            mock.patch.object(
                finalizer, "_validate_r8u_chain_artifacts", chain_boundary
            ),
            mock.patch.object(
                finalizer,
                "replay_batch_preservation_manifest",
                return_value={
                    "retained_artifacts_reverified": 1,
                    "retired_cache_artifacts": 1,
                },
            ) as preservation_replay,
            mock.patch.object(
                finalizer.core,
                "load_strict_json",
                side_effect=synthetic_final_ledger,
            ),
            mock.patch.object(finalizer.core, "validate_resume_authority"),
        ):
            summary = finalizer.finalize_receipts(
                receipt_paths,
                expected_governing_commit=(
                    finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
                ),
                expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
                plan=plan,
                requirements=requirements,
                production_root=production_root,
                contract={},
                contract_path=attempt_root / "contract.json",
                environment_receipt=attempt_root / "environment.json",
                cache_retirement_authorization_root=cache_authority_root,
                canonical_output_root=canonical_output_root,
                expected_runtime_authority=runtime,
                expected_no_cine_studies=0,
                r8u_implementation_authority=authority,
            )

        assert summary["status"] == "PASS_PRODUCTION_C3_FINALIZED"
        assert summary["production_batches"] == 19
        assert summary["selected_studies"] == 19
        assert summary["verified_source_objects"] == 19
        assert summary["pooled_imaging_eligible_studies"] == 19
        assert summary["implementation_authority_epoch_count"] == 3
        assert summary["all_scientific_authority_bindings_identical"] is True
        assert summary["r8u_implementation_commit"] == (
            authority.implementation_commit
        )
        assert summary["r8u_recovery_continuation_authority_sha256"] == (
            "a" * 64
        )
        assert json.loads(receipt_paths[2].read_text(encoding="utf-8"))[
            "scheduler_job_identity"
        ] == "recovered_batch3"
        assert json.loads(receipt_paths[15].read_text(encoding="utf-8"))[
            "scheduler_job_identity"
        ] == "recovered_batch16"
        assert preservation_replay.call_count == 19
        repository_boundary.assert_called_once_with(
            authority.implementation_commit
        )
        assert chain_boundary.call_count == 1


def test_finalize_rejects_simultaneous_r8r_and_r8u_authority() -> None:
    r8r = finalizer.R8RImplementationAuthority(
        implementation_commit="b" * 40,
        recovery_authority_sha256="1" * 64,
        recovery_terminal_receipt_sha256="2" * 64,
        continuation_capacity_receipt_sha256="3" * 64,
        continuation_claim_sha256="4" * 64,
        continuation_submission_receipt_sha256="5" * 64,
    )
    _expect_code(
        "FINALIZER_IMPLEMENTATION_AUTHORITY_AMBIGUOUS",
        lambda: finalizer.finalize_receipts(
            [],
            expected_governing_commit=(
                finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT
            ),
            r8r_implementation_authority=r8r,
            r8u_implementation_authority=_authority(),
        ),
    )


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"R8U_FINALIZER_DEPENDENCY_LIGHT_TESTS=PASS ({len(tests)})")
