from __future__ import annotations

import copy
from dataclasses import fields, replace
import errno
import hashlib
import importlib.util
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
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
    "finalize_lvef_c3_production_r8u_r3_test",
    "finalize_lvef_c3_production.py",
)
controller = _load(
    "lvef_c3_r8r_recovery_continuation_r8u_r3_finalizer_test",
    "lvef_c3_r8r_recovery_continuation.py",
)


def _expect_code(code: str, function: Any) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", None) == code, repr(exc)
    else:
        raise AssertionError(f"Expected {code}")


def _authority() -> Any:
    names = [field.name for field in fields(finalizer.R8UR3ImplementationAuthority)]
    values = {
        name: hashlib.sha256(f"r8u-r3:{name}".encode("ascii")).hexdigest()
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
    return finalizer.R8UR3ImplementationAuthority(
        implementation_commit="c" * 40,
        **values,
    )


def _receipt_paths() -> dict[str, Path]:
    attempt_root = Path("/synthetic/attempts") / finalizer.R8R_ATTEMPT_ID
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
        epoch = (
            original_epoch
            if index < 2
            else finalizer.R8U_FE3_IMPLEMENTATION_EPOCH
            if index < 15
            else current_epoch
        )
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


def _common_r3(authority: Any, artifact_type: str, status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "status": status,
        "original_scientific_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "implementation_commit": authority.implementation_commit,
        "implementation_authority_epochs": (
            finalizer._r8u_r3_expected_implementation_authority_epochs(
                authority.implementation_commit
            )
        ),
        "attempt_id": finalizer.R8R_ATTEMPT_ID,
        "batch_plan_sha256": finalizer.R8R_BATCH_PLAN_SHA256,
        "batch_id": "c3_batch_015",
    }


def test_r3_api_schemas_paths_and_terminal_status_are_closed() -> None:
    expected_fields = [
        "implementation_commit",
        "historical_r8r_recovery_authority_sha256",
        "historical_r8r_recovery_terminal_receipt_sha256",
        "historical_r8r_continuation_capacity_receipt_sha256",
        "historical_r8r_continuation_claim_sha256",
        "historical_r8r_continuation_submission_receipt_sha256",
        "failed_partial_seal_sha256",
        "r2_recovery_capacity_receipt_sha256",
        "r2_recovery_authority_sha256",
        "r2_recovery_submission_receipt_sha256",
        "extraction_candidate_seal_sha256",
        "publication_primitive_probe_sha256",
        "publication_claim_sha256",
        "publication_receipt_sha256",
        "resume_capacity_receipt_sha256",
        "resume_authority_sha256",
        "resume_submission_receipt_sha256",
        "resume_accounting_sha256",
        "resume_terminal_receipt_sha256",
        "continuation_claim_sha256",
        "continuation_submission_receipt_sha256",
    ]
    assert [field.name for field in fields(finalizer.R8UR3ImplementationAuthority)] == expected_fields
    assert "r8u_r3_implementation_authority" in inspect.signature(
        finalizer.finalize_receipts
    ).parameters
    specifications = {
        field: (path, artifact_type, status, epoch_kind)
        for field, path, artifact_type, status, epoch_kind in (
            finalizer.R8U_R3_CHAIN_ARTIFACT_SPECS
        )
    }
    assert set(specifications) == set(finalizer.R8U_R3_CHAIN_ARTIFACT_KEYS)
    assert specifications["publication_claim_sha256"][0] == (
        "extracted_cache/c3_batch_015/.r8u_r3_publication_claim/claim.restricted.json"
    )
    assert specifications["resume_terminal_receipt_sha256"][1:3] == (
        "lvef_c3_r8u_r3_batch16_publication_resume_terminal_v1",
        "PASS_BATCH16_PUBLICATION_RESUME_FINALIZED",
    )
    assert specifications["continuation_claim_sha256"][0].startswith(
        "r8u_r3_continuation_17_19/"
    )
    assert specifications["continuation_submission_receipt_sha256"][1] == (
        "lvef_c3_r8u_r3_fixed_continuation_submission_v1"
    )
    assert finalizer.R8U_R3_CHAIN_ARTIFACT_KEYS[
        "resume_capacity_receipt_sha256"
    ] == finalizer.r8r_capacity.R8U_R3_CAPACITY_KEYS


def test_r3_controller_and_finalizer_schemas_are_identical() -> None:
    pairs = (
        ("R8U_R3_COMMON_CHAIN_KEYS", "R8U_R3_COMMON_KEYS"),
        ("R8U_R3_CANDIDATE_SEAL_KEYS", "R8U_R3_CANDIDATE_SEAL_KEYS"),
        ("R8U_R3_PROBE_KEYS", "R8U_R3_PROBE_KEYS"),
        (
            "R8U_R3_PROCEEDABLE_PROBE_RESULTS",
            "R8U_R3_PROCEEDABLE_PROBE_RESULTS",
        ),
        (
            "R8U_R3_PUBLICATION_CLAIM_KEYS",
            "R8U_R3_PUBLICATION_CLAIM_KEYS",
        ),
        ("R8U_R3_PUBLICATION_KEYS", "R8U_R3_PUBLICATION_KEYS"),
        ("R8U_R3_AUTHORITY_KEYS", "R8U_R3_AUTHORITY_KEYS"),
        ("R8U_R3_SUBMISSION_KEYS", "R8U_R3_SUBMISSION_KEYS"),
        (
            "R8U_R3_PROCESS_PROJECTION_KEYS",
            "R8U_R3_PROCESS_PROJECTION_KEYS",
        ),
        (
            "R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS",
            "R8U_R3_INITIAL_QSTAT_PROJECTION_KEYS",
        ),
        ("R8U_R3_TERMINAL_KEYS", "R8U_R3_TERMINAL_KEYS"),
        (
            "R8U_R3_CONTINUATION_LINK_KEYS",
            "R8U_R3_CONTINUATION_LINK_KEYS",
        ),
        (
            "R8U_R3_CONTINUATION_CLAIM_KEYS",
            "R8U_R3_CONTINUATION_CLAIM_KEYS",
        ),
        (
            "R8U_R3_CONTINUATION_SUBMISSION_KEYS",
            "R8U_R3_CONTINUATION_SUBMISSION_KEYS",
        ),
    )
    for finalizer_name, controller_name in pairs:
        assert getattr(finalizer, finalizer_name) == getattr(
            controller, controller_name
        )

    implementation_commit = "c" * 40
    assert finalizer._r8u_r3_expected_resume_qsub_command(
        attempt_root=controller.ATTEMPT_ROOT,
        implementation_commit=implementation_commit,
    ) == controller._r8u_r3_resume_qsub_command(implementation_commit)


def test_r3_requires_the_exact_seven_commit_chain_and_rejects_r2_mix() -> None:
    authority = _authority()
    expected = {
        "scientific_commit": finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
        "r8r_implementation_commit": finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": finalizer.R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        "r8u_scheduler_log_repair_commit": finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        "r8u_publication_resume_repair_commit": (
            finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_candidate_authority_repair_commit": (
            authority.implementation_commit
        ),
    }
    assert finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT == (
        "ce3326a23f149dd864c5aa534225b959d7b5abbe"
    )
    assert finalizer._r8u_r3_expected_implementation_authority_epochs(
        authority.implementation_commit
    ) == expected
    finalizer._r8u_r3_validate_implementation_authority_epochs(
        expected, implementation_commit=authority.implementation_commit
    )
    r2_only = finalizer._r8u_expected_implementation_authority_epochs(
        finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
    )
    for drifted in (
        r2_only,
        {**expected, "r8u_publication_resume_repair_commit": "d" * 40},
        {**expected, "r8u_candidate_authority_repair_commit": "d" * 40},
        {**expected, "unexpected": "d" * 40},
    ):
        _expect_code(
            "R8U_R3_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID",
            lambda drifted=drifted: finalizer._r8u_r3_validate_implementation_authority_epochs(
                drifted, implementation_commit=authority.implementation_commit
            ),
        )


def test_r3_loader_binds_candidate_and_capacity_to_the_same_seven_epochs() -> None:
    authority = _authority()
    candidate_type = "lvef_c3_r8u_r3_batch16_extraction_candidate_seal_v1"
    candidate_status = "PASS_COMPLETED_BATCH16_EXTRACTION_CANDIDATE_SEALED"
    candidate = {key: 0 for key in finalizer.R8U_R3_CANDIDATE_SEAL_KEYS}
    candidate.update(_common_r3(authority, candidate_type, candidate_status))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "candidate.json"
        payload = finalizer.core.canonical_json_bytes(candidate)
        path.write_bytes(payload)
        path.chmod(0o600)
        bound = replace(
            authority,
            extraction_candidate_seal_sha256=hashlib.sha256(payload).hexdigest(),
        )
        loaded, _digest = finalizer._load_r8u_r3_chain_artifact(
            path=path,
            field="extraction_candidate_seal_sha256",
            artifact_type=candidate_type,
            status=candidate_status,
            epoch_kind="r3",
            authority=bound,
            plan={},
        )
        assert loaded["implementation_authority_epochs"] == (
            finalizer._r8u_r3_expected_implementation_authority_epochs(
                authority.implementation_commit
            )
        )

        mixed = copy.deepcopy(candidate)
        mixed["implementation_authority_epochs"] = (
            finalizer._r8u_expected_implementation_authority_epochs(
                finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
            )
        )
        payload = finalizer.core.canonical_json_bytes(mixed)
        path.write_bytes(payload)
        mixed_bound = replace(
            authority,
            extraction_candidate_seal_sha256=hashlib.sha256(payload).hexdigest(),
        )
        _expect_code(
            "R8U_R3_FINALIZER_IMPLEMENTATION_AUTHORITY_EPOCHS_INVALID",
            lambda: finalizer._load_r8u_r3_chain_artifact(
                path=path,
                field="extraction_candidate_seal_sha256",
                artifact_type=candidate_type,
                status=candidate_status,
                epoch_kind="r3",
                authority=mixed_bound,
                plan={},
            ),
        )

        capacity = {
            key: 0 for key in finalizer.r8r_capacity.R8U_R3_CAPACITY_KEYS
        }
        capacity.update(
            {
                "schema_version": 1,
                "artifact_type": "lvef_c3_r8u_r3_batch16_publication_resume_capacity_v1",
                "status": "PASS_BATCH16_PUBLICATION_RESUME_AND_17_19_WITH_200GB_RESERVE",
                "implementation_authority_epochs": (
                    finalizer._r8u_r3_expected_implementation_authority_epochs(
                        authority.implementation_commit
                    )
                ),
            }
        )
        capacity_payload = finalizer.core.canonical_json_bytes(capacity)
        path.write_bytes(capacity_payload)
        capacity_bound = replace(
            authority,
            resume_capacity_receipt_sha256=hashlib.sha256(
                capacity_payload
            ).hexdigest(),
        )
        with mock.patch.object(
            finalizer.r8r_capacity,
            "validate_fixed_r8u_r3_batch16_publication_resume_capacity",
            return_value=capacity,
        ) as validator:
            finalizer._load_r8u_r3_chain_artifact(
                path=path,
                field="resume_capacity_receipt_sha256",
                artifact_type=capacity["artifact_type"],
                status=capacity["status"],
                epoch_kind="r3_capacity",
                authority=capacity_bound,
                plan={"fixed": "plan"},
                candidate_total_bytes=123_456,
            )
        validator.assert_called_once_with(
            {"fixed": "plan"},
            capacity,
            completed_extraction_candidate_seal_sha256=(
                capacity_bound.extraction_candidate_seal_sha256
            ),
            completed_extraction_candidate_bytes=123_456,
            r8u_candidate_authority_repair_commit=(
                authority.implementation_commit
            ),
        )


def test_r3_resume_accounting_requires_later_qacct_zero_zero() -> None:
    projection = {
        "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
        "job_id": "8123456",
        "task_id": "NONE",
        "failed": 0,
        "exit_status": 0,
        "start_time": "Sat Aug 29 01:00:00 2026",
        "end_time": "Sat Aug 29 01:01:00 2026",
        "ru_wallclock_seconds": "60.0",
    }
    accounting = {
        "original_task_id": 16,
        "resume_job_id": "8123456",
        "failed": 0,
        "exit_status": 0,
        "accounting_projection": projection,
    }
    finalizer._validate_r8u_r3_resume_accounting(
        accounting, resume_job_id="8123456"
    )
    for drifted in (
        {**accounting, "exit_status": 78},
        {**accounting, "failed": 1},
        {
            **accounting,
            "accounting_projection": {**projection, "exit_status": 78},
        },
        {**accounting, "resume_job_id": "8123457"},
    ):
        _expect_code(
            "R8U_R3_FINALIZER_RESUME_ACCOUNTING_INVALID",
            lambda drifted=drifted: finalizer._validate_r8u_r3_resume_accounting(
                drifted, resume_job_id="8123456"
            ),
        )


def test_r3_candidate_static_projection_is_replayed_exactly() -> None:
    candidate = {
        key: ("1" * 64 if key.endswith("_sha256") else 0)
        for key in finalizer.R8U_R3_CANDIDATE_SEAL_KEYS
    }
    candidate.update(
        {
            "candidate_regular_files": 10_192,
            "candidate_directories": 1,
            "candidate_total_bytes": 100,
            "candidate_npz_files": 10_187,
            "candidate_npz_bytes": 100,
            "source_target_same_mounted_filesystem": True,
            "target_absent": True,
            "symlink_count": 0,
            "nonregular_count": 0,
            "n_selected_studies": 250,
            "n_source_objects": finalizer.R8U_BATCH16_RAW_FILES,
            "source_bytes": finalizer.R8U_BATCH16_RAW_BYTES,
            "n_readable": finalizer.R8U_BATCH16_RAW_FILES,
            "n_unreadable": 0,
            "n_multiframe_candidates": 10_187,
            "n_single_frame": 8_490,
            "n_pixel_decode_failures": 0,
            "n_successfully_extracted_cines": 10_187,
            "n_object_technical_dispositions": 0,
            "n_blocking_failures": 0,
            "n_ordinary_preprocessing_path": 10_187,
            "n_spatial_fallback_preprocessing_path": 0,
            "n_temporal_fallback_preprocessing_path": 0,
            "n_spatial_temporal_fallback_preprocessing_path": 0,
            "object_substitution_count": 0,
            "extraction_status": (
                "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
            ),
            "npz_body_reads": 0,
            "dicom_body_reads": 0,
            "dicom_extraction_executions": 0,
            "cloud_requests": 0,
            "downloads": 0,
        }
    )
    finalizer._validate_r8u_r3_candidate_static(candidate)
    for drifted in (
        {"candidate_regular_files": 10_191},
        {"candidate_npz_bytes": 101},
        {"source_mount_identity_sha256": "2" * 64},
        {"downloads": False},
        {"candidate_root_identity_sha256": "invalid"},
    ):
        _expect_code(
            "R8U_R3_FINALIZER_EXTRACTION_CANDIDATE_INVALID",
            lambda drifted=drifted: finalizer._validate_r8u_r3_candidate_static(
                {**candidate, **drifted}
            ),
        )


def test_r3_process_qstat_and_real_rename_projections_are_closed() -> None:
    expected_probe_outcomes = {
        "RENAME_NOREPLACE_SUPPORTED": (0, "NONE", True),
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL": (
            errno.EINVAL,
            errno.errorcode[errno.EINVAL],
            False,
        ),
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS": (
            errno.ENOSYS,
            errno.errorcode[errno.ENOSYS],
            False,
        ),
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP": (
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            errno.errorcode.get(
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
                "UNKNOWN",
            ),
            False,
        ),
    }
    assert {
        result: finalizer._r8u_r3_expected_proceedable_probe_outcome(result)
        for result in expected_probe_outcomes
    } == expected_probe_outcomes
    for blocked in (
        "RENAME_CROSS_MOUNT_EXDEV",
        "RENAME_PERMISSION_FAILURE",
        "RENAME_AMBIGUOUS_SERVER_RESULT",
        "OTHER_EXACT_ERRNO_CLASS",
    ):
        assert finalizer._r8u_r3_expected_proceedable_probe_outcome(
            blocked
        ) is None

    process_projection = {
        "status": "PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
        "matching_processes": 0,
        "process_snapshot_count": 1,
        "ps_argv_sha256": "1" * 64,
        "ps_stdout_sha256": "2" * 64,
    }
    assert finalizer._validate_r8u_r3_process_projection(
        process_projection,
        expected_status="PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
    ) == finalizer.core.canonical_json_sha256(process_projection)
    _expect_code(
        "R8U_R3_FINALIZER_PROCESS_PROJECTION_INVALID",
        lambda: finalizer._validate_r8u_r3_process_projection(
            {**process_projection, "matching_processes": 1},
            expected_status="PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
        ),
    )

    qstat_body = {
        "status": "PASS_EXACT_ONE_R8U_R3_RESUME_JOB_ZERO_COMPETITORS",
        "resume_job_id": "8123456",
        "resume_job_name": "lvef_c3_r8u_r3_res_cccccccc",
        "state": "qw",
        "category": "pending",
        "target_matches": 1,
        "competing_matching_jobs": 0,
        "qstat_snapshot_count": 1,
    }
    qstat_projection = {
        **qstat_body,
        "qstat_projection_sha256": finalizer.core.canonical_json_sha256(
            qstat_body
        ),
    }
    assert finalizer._validate_r8u_r3_initial_qstat_projection(
        qstat_projection,
        resume_job_id="8123456",
        resume_job_name="lvef_c3_r8u_r3_res_cccccccc",
    ) == finalizer.core.canonical_json_sha256(qstat_projection)
    _expect_code(
        "R8U_R3_FINALIZER_QSTAT_PROJECTION_INVALID",
        lambda: finalizer._validate_r8u_r3_initial_qstat_projection(
            {**qstat_projection, "competing_matching_jobs": 1},
            resume_job_id="8123456",
            resume_job_name="lvef_c3_r8u_r3_res_cccccccc",
        ),
    )

    expected = {
        0: "RENAME_RETURNED_SUCCESS",
        errno.EINVAL: "RENAME_ERROR_UNSUPPORTED",
        errno.ENOSYS: "RENAME_ERROR_UNSUPPORTED",
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP): (
            "RENAME_ERROR_UNSUPPORTED"
        ),
        errno.EXDEV: "RENAME_ERROR_CROSS_MOUNT",
        errno.EACCES: "RENAME_ERROR_PERMISSION",
        errno.EPERM: "RENAME_ERROR_PERMISSION",
        errno.EEXIST: "RENAME_ERROR_COLLISION",
        errno.ENOTEMPTY: "RENAME_ERROR_COLLISION",
        errno.ENOENT: "RENAME_ERROR_SOURCE_MISSING",
        errno.EIO: "RENAME_ERROR_IO",
        123_456: "RENAME_ERROR_OTHER",
    }
    assert {
        errno_number: finalizer._r8u_r3_real_rename_classification(
            errno_number
        )
        for errno_number in expected
    } == expected

    finalizer._validate_r8u_r3_real_rename_outcome(
        returned_success=True,
        errno_number=0,
        errno_name="NONE",
        errno_classification="RENAME_RETURNED_SUCCESS",
        publication_ruling="PUBLICATION_PASS",
    )
    finalizer._validate_r8u_r3_real_rename_outcome(
        returned_success=False,
        errno_number=errno.EIO,
        errno_name="EIO",
        errno_classification="RENAME_ERROR_IO",
        publication_ruling="PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN",
    )
    finalizer._validate_r8u_r3_real_rename_outcome(
        returned_success=False,
        errno_number=123_456,
        errno_name="UNKNOWN",
        errno_classification="RENAME_ERROR_OTHER",
        publication_ruling="PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN",
    )
    for drifted in (
        {"errno_name": "EIO"},
        {"errno_classification": "RENAME_ERROR_IO"},
        {"returned_success": True},
        {"publication_ruling": "PUBLICATION_PASS"},
    ):
        values = {
            "returned_success": False,
            "errno_number": 123_456,
            "errno_name": "UNKNOWN",
            "errno_classification": "RENAME_ERROR_OTHER",
            "publication_ruling": (
                "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
            ),
            **drifted,
        }
        _expect_code(
            "R8U_R3_FINALIZER_PUBLICATION_RECEIPT_INVALID",
            lambda values=values: (
                finalizer._validate_r8u_r3_real_rename_outcome(**values)
            ),
        )


def test_r3_accepts_only_the_exact_2_plus_13_plus_4_partition() -> None:
    receipts, hashes, sizes, runtime, current_epoch = _epoch_receipts()
    authority = _authority()
    with (
        mock.patch.object(
            finalizer.core, "validate_runtime_authority", return_value=runtime
        ),
        mock.patch.object(
            finalizer,
            "_current_r8r_implementation_epoch",
            return_value=current_epoch,
        ),
        mock.patch.object(finalizer, "_validate_r8u_r3_repository_authority") as repository,
        mock.patch.object(
            finalizer,
            "_validate_r8u_r3_chain_artifacts",
            return_value="d" * 64,
        ) as chain,
    ):
        observed = finalizer._validate_r8u_r3_mixed_implementation_epochs(
            receipts,
            receipt_hashes_by_batch=hashes,
            receipt_sizes_by_batch=sizes,
            receipt_paths_by_batch=_receipt_paths(),
            expected_governing_commit=finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
            expected_runtime_authority=runtime,
            authority=authority,
            plan={},
        )
        assert observed == "d" * 64
        repository.assert_called_once_with(authority.implementation_commit)
        assert chain.call_count == 1

        wrong_split = copy.deepcopy(receipts)
        wrong_split[14][finalizer.R8R_IMPLEMENTATION_EPOCH_KEYS[0]] = (
            current_epoch[0]
        )
        _expect_code(
            "R8U_R3_FINALIZER_IMPLEMENTATION_EPOCH_MISMATCH",
            lambda: finalizer._validate_r8u_r3_mixed_implementation_epochs(
                wrong_split,
                receipt_hashes_by_batch=hashes,
                receipt_sizes_by_batch=sizes,
                receipt_paths_by_batch=_receipt_paths(),
                expected_governing_commit=finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
                expected_attempt_id=finalizer.R8R_ATTEMPT_ID,
                expected_runtime_authority=runtime,
                authority=authority,
                plan={},
            ),
        )


def test_r3_repository_authority_requires_one_direct_child_of_ce3326a() -> None:
    implementation_commit = "c" * 40
    exact_outputs = {
        ("rev-parse", "HEAD"): f"{implementation_commit}\n".encode("ascii"),
        (
            "rev-parse",
            "refs/remotes/origin/codex/lvef-multitask-revalidation",
        ): f"{implementation_commit}\n".encode("ascii"),
        ("branch", "--show-current"): b"codex/lvef-multitask-revalidation\n",
        (
            "rev-list", "--parents", "-n", "1", implementation_commit,
        ): (
            f"{implementation_commit} "
            f"{finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        ): (
            f"{finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
            f"{finalizer.R8U_BASE_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            finalizer.R8U_BASE_IMPLEMENTATION_COMMIT,
        ): (
            f"{finalizer.R8U_BASE_IMPLEMENTATION_COMMIT} "
            f"{finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT}\n"
        ).encode("ascii"),
        (
            "rev-list", "--parents", "-n", "1",
            finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT,
        ): (
            f"{finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT} "
            f"{finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT}\n"
        ).encode("ascii"),
    }
    count_pairs = (
        (
            finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            implementation_commit,
            b"1\n",
        ),
        (
            finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            b"1\n",
        ),
        (
            finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            finalizer.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
            b"1\n",
        ),
        (
            finalizer.R8U_BASE_IMPLEMENTATION_COMMIT,
            finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
            b"1\n",
        ),
        (
            finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT,
            finalizer.R8U_BASE_IMPLEMENTATION_COMMIT,
            b"1\n",
        ),
        (
            finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            finalizer.R8U_PRIOR_IMPLEMENTATION_COMMIT,
            b"1\n",
        ),
        (
            finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            finalizer.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT,
            b"5\n",
        ),
        (
            finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            implementation_commit,
            b"6\n",
        ),
    )
    for ancestor, descendant, output in count_pairs:
        exact_outputs[("rev-list", "--count", f"{ancestor}..{descendant}")] = output

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        arguments = tuple(argv[3:])
        if arguments in exact_outputs:
            return subprocess.CompletedProcess(argv, 0, exact_outputs[arguments], b"")
        if arguments == ("status", "--porcelain", "--untracked-files=no"):
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if arguments[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError(arguments)

    with (
        mock.patch.object(finalizer.subprocess, "run", side_effect=run),
        mock.patch.object(finalizer, "R8U_FE3_GIT_TREE_PATHS", {}),
    ):
        finalizer._validate_r8u_r3_repository_authority(implementation_commit)

    drifted = dict(exact_outputs)
    drifted[("rev-list", "--parents", "-n", "1", implementation_commit)] = (
        f"{implementation_commit} {finalizer.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}\n"
    ).encode("ascii")

    def run_drifted(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        arguments = tuple(argv[3:])
        if arguments in drifted:
            return subprocess.CompletedProcess(argv, 0, drifted[arguments], b"")
        return run(argv, **kwargs)

    with (
        mock.patch.object(finalizer.subprocess, "run", side_effect=run_drifted),
        mock.patch.object(finalizer, "R8U_FE3_GIT_TREE_PATHS", {}),
    ):
        _expect_code(
            "R8U_R3_FINALIZER_REPOSITORY_AUTHORITY_MISMATCH",
            lambda: finalizer._validate_r8u_r3_repository_authority(
                implementation_commit
            ),
        )


def test_r3_stage_selection_and_mixed_mode_rejection_are_exact() -> None:
    receipts = [{"index": index} for index in range(19)]
    assert finalizer._stage_authority_receipts(
        receipts, r8r_mode=False, r8u_mode=False, r8u_r3_mode=True
    ) == receipts[15:]
    _expect_code(
        "FINALIZER_IMPLEMENTATION_AUTHORITY_AMBIGUOUS",
        lambda: finalizer._stage_authority_receipts(
            receipts, r8r_mode=False, r8u_mode=True, r8u_r3_mode=True
        ),
    )
    _expect_code(
        "FINALIZER_IMPLEMENTATION_AUTHORITY_AMBIGUOUS",
        lambda: finalizer.finalize_receipts(
            [],
            expected_governing_commit=finalizer.R8R_SCIENTIFIC_GOVERNING_COMMIT,
            r8u_implementation_authority=mock.sentinel.r2,
            r8u_r3_implementation_authority=_authority(),
        ),
    )


def test_future_tasks_17_19_use_only_the_closed_r3_terminal_chain() -> None:
    claim_keys = finalizer.R8U_R3_CONTINUATION_CLAIM_KEYS
    submission_keys = finalizer.R8U_R3_CONTINUATION_SUBMISSION_KEYS
    assert finalizer.R8U_R3_CONTINUATION_LINK_KEYS <= claim_keys
    assert finalizer.R8U_R3_CONTINUATION_LINK_KEYS <= submission_keys
    assert {
        "resume_accounting_sha256",
        "resume_terminal_receipt_sha256",
        "publication_claim_sha256",
        "publication_receipt_sha256",
    } <= claim_keys & submission_keys
    assert {
        "recovery_capacity_sha256",
        "recovery_authority_sha256",
        "recovery_accounting_sha256",
        "recovery_terminal_receipt_sha256",
    }.isdisjoint(claim_keys | submission_keys)
    assert "resume_job_id" in submission_keys
    assert "recovery_job_id" not in submission_keys
    assert {
        "continuation_task_range",
        "continuation_task_count",
        "continuation_max_concurrency",
        "held_finalizer_count",
        "total_new_qsub_maximum",
    } <= claim_keys
    assert {
        "array_task_range",
        "array_task_count",
        "array_max_concurrency",
        "finalizer_held_on_array",
        "total_new_qsub_submissions",
        "scheduler_submission_maximum",
    } <= submission_keys


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"R8U_R3_FINALIZER_FOCUSED_TESTS=PASS ({len(tests)})")
