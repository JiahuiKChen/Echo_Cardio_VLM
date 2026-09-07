#!/usr/bin/env python3
"""Dependency-light tests for fixed R8U-R7G metadata and lock receipts."""
from __future__ import annotations

import copy
import inspect
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8u_r7g_metadata as metadata


ADJUDICATION = "9" * 40
CREATED = "2026-09-07T12:00:00Z"
BRANCH = "codex/lvef-multitask-revalidation"


def _hash(label: str) -> str:
    return metadata.canonical_json_sha256({"label": label})


def _fixture() -> tuple[dict, str, list[dict]]:
    object_counts = [18_500] * 15 + [18_877, 18_606, 18_658, 2_343]
    source_bytes = (
        [66_000_000_000] * 15
        + [81_366_146_696, 66_807_894_336, 68_754_613_138, 9_640_479_152]
    )
    assert sum(object_counts) == 335_984
    assert sum(source_bytes) == 1_216_569_133_322
    batches: list[dict] = []
    projections: list[dict] = []
    next_identifier = 20_000_000
    for ordinal, batch_id in enumerate(metadata.EXPECTED_BATCH_IDS):
        studies = 30 if ordinal == 18 else 250
        members = [
            {
                "subject_id": str(next_identifier + offset),
                "study_id": str(next_identifier + offset),
                "split": "train",
            }
            for offset in range(studies)
        ]
        next_identifier += studies
        no_cine = 5 if ordinal == 0 else 0
        no_cine_members = [
            {"subject_id": row["subject_id"], "study_id": row["study_id"]}
            for row in members[:no_cine]
        ]
        membership_sha = metadata.canonical_json_sha256(members)
        no_cine_sha = metadata.canonical_json_sha256(no_cine_members)
        batches.append(
            {
                "batch_id": batch_id,
                "ordinal": ordinal,
                "n_studies": studies,
                "n_subjects": studies,
                "n_objects": object_counts[ordinal],
                "source_bytes": source_bytes[ordinal],
                "study_membership_sha256": membership_sha,
                "expected_no_cine_studies": no_cine,
                "prespecified_no_cine_study_set_sha256": no_cine_sha,
                "studies": members,
            }
        )
    plan = {
        "schema_version": 3,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v3",
        "cohort": {
            "selected_studies": 4_530,
            "selected_subjects": 4_530,
            "normalized_source_objects": 335_984,
            "selected_source_bytes": 1_216_569_133_322,
            "expected_no_cine_studies": 5,
        },
        "batches": batches,
    }
    plan_sha = metadata.canonical_json_sha256(plan)
    for ordinal, batch in enumerate(batches):
        multiframe = 100 if ordinal == 18 else 1_000
        technical = 1 if ordinal == 2 else 0
        successful = multiframe - technical
        hashes = {
            key: _hash(f"{key}-{ordinal}")
            for key in metadata.BATCH_HASH_KEYS
        }
        hashes["study_membership_sha256"] = batch[
            "study_membership_sha256"
        ]
        hashes["prespecified_no_cine_study_set_sha256"] = batch[
            "prespecified_no_cine_study_set_sha256"
        ]
        if ordinal == 15:
            hashes["batch_finalization_receipt_sha256"] = (
                metadata.BATCH16_FINAL_RECEIPT_SHA256
            )
        projections.append(
            {
                "batch_id": batch["batch_id"],
                "ordinal": ordinal,
                "attempt_id": metadata.ATTEMPT_ID,
                "batch_plan_sha256": plan_sha,
                "scientific_commit": metadata.SCIENTIFIC_COMMIT,
                **hashes,
                "n_selected_studies": batch["n_studies"],
                "n_selected_subjects": batch["n_subjects"],
                "n_source_objects": batch["n_objects"],
                "source_bytes": batch["source_bytes"],
                "n_downloaded_objects": batch["n_objects"],
                "downloaded_bytes": batch["source_bytes"],
                "n_readable_objects": batch["n_objects"],
                "n_unreadable_objects": 0,
                "readable_bytes": batch["source_bytes"],
                "n_multiframe_candidates": multiframe,
                "n_single_frame_objects": batch["n_objects"] - multiframe,
                "n_successful_extractions": successful,
                "n_clip_embeddings": successful,
                "n_technical_dispositions": technical,
                "n_blocking_failures": 0,
                "n_ordinary_preprocessing_path": successful,
                "n_spatial_fallback_preprocessing_path": 0,
                "n_temporal_fallback_preprocessing_path": 0,
                "n_spatial_temporal_fallback_preprocessing_path": 0,
                "n_study_embeddings": batch["n_studies"]
                - batch["expected_no_cine_studies"],
                "n_prespecified_no_cine_studies": batch[
                    "expected_no_cine_studies"
                ],
                "n_new_no_cine_studies": 0,
                "retired_extracted_cache_bytes": successful * 4_096,
                "n_missing_selected_studies": 0,
                "n_duplicate_selected_studies": 0,
                "n_source_substitutions": 0,
                "n_unaccounted_multiframe_candidates": 0,
                "n_outcome_informed_decisions": 0,
                "final_ledger_status": "FINALIZED",
                "preservation_status": (
                    "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
                ),
                "cache_retirement_status": "PASS_RETIRED",
                "batch_finalization_status": "PASS_BATCH_FINALIZED",
                "raw_source_authority_retained": True,
                "extracted_cache_absent": True,
            }
        )
    return plan, plan_sha, projections


def _r7f_hashes() -> dict[str, str]:
    return {
        "r7f_capacity_receipt_sha256": metadata.R7F_CAPACITY_RECEIPT_SHA256,
        "r7f_probe_terminal_receipt_sha256": _hash("probe-terminal"),
        "r7f_continuation_claim_sha256": (
            metadata.R7F_CONTINUATION_CLAIM_SHA256
        ),
        "r7f_array_submission_receipt_sha256": (
            metadata.R7F_ARRAY_SUBMISSION_RECEIPT_SHA256
        ),
        "r7f_finalizer_submission_receipt_sha256": (
            metadata.R7F_FINALIZER_SUBMISSION_RECEIPT_SHA256
        ),
        "r7f_combined_submission_receipt_sha256": (
            metadata.R7F_COMBINED_SUBMISSION_RECEIPT_SHA256
        ),
    }


def _authorities(plan_sha: str) -> dict:
    return {
        "attempt_id": metadata.ATTEMPT_ID,
        "plan_sha256": plan_sha,
        "scientific_commit": metadata.SCIENTIFIC_COMMIT,
        "runtime_implementation_commit": (
            metadata.R7F_RUNTIME_IMPLEMENTATION_COMMIT
        ),
        "adjudication_implementation_commit": ADJUDICATION,
        "terminal_authority_sha256": _hash("terminal-authority"),
        "r7f_authority_receipt_sha256": _r7f_hashes(),
        "accounting_receipt_sha256": {
            "task_17": _hash("accounting-17"),
            "task_18": _hash("accounting-18"),
            "task_19": _hash("accounting-19"),
            "finalizer": _hash("accounting-finalizer"),
        },
        "cache_topology": {
            "active_finalized_extraction_caches": 0,
            "batch16_failed_partial_cache_retained": True,
            "batch16_failed_partial_cache_outside_active_topology": True,
            "batch16_failed_partial_cache_adopted": False,
            "batch16_failed_partial_cache_deleted": False,
            "batch16_failed_partial_cache_overwritten": False,
            "batch16_failed_partial_seal_sha256": _hash("partial-seal"),
            "batch16_failed_partial_metadata_projection_sha256": _hash(
                "partial-metadata"
            ),
        },
        "prohibited_actions": metadata.zero_scientific_actions(),
        "created_at_utc": CREATED,
    }


def _cohort_fixture() -> tuple[dict, list[dict], dict, dict]:
    plan, plan_sha, projections = _fixture()
    authority = _authorities(plan_sha)
    with mock.patch.object(metadata, "PLAN_SHA256", plan_sha):
        cohort = metadata.build_cohort_finalization_receipt(
            plan, projections, **authority
        )
    return plan, projections, authority, cohort


def test_full_fixed_partition_and_derived_totals_pass() -> None:
    plan, plan_sha, projections = _fixture()
    observed = metadata.validate_batch_metadata_partition(
        plan,
        projections,
        expected_plan_sha256=plan_sha,
        expected_attempt_id=metadata.ATTEMPT_ID,
        expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
    )
    totals = observed["aggregate_totals"]
    assert totals["finalized_batches"] == 19
    assert totals["n_selected_studies"] == 4_530
    assert totals["n_source_objects"] == 335_984
    assert totals["source_bytes"] == 1_216_569_133_322
    assert totals["tail_selected_studies"] == 530
    assert totals["n_unreadable_objects"] == 0
    assert totals["n_single_frame_objects"] == (
        totals["n_readable_objects"] - totals["n_multiframe_candidates"]
    )
    assert totals["n_blocking_failures"] == 0


def test_missing_duplicate_and_literal_aggregate_mismatch_fail() -> None:
    plan, plan_sha, projections = _fixture()
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections[:-1],
            expected_plan_sha256=plan_sha,
            expected_attempt_id=metadata.ATTEMPT_ID,
            expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
        )
    except metadata.R7GMetadataError as exc:
        assert exc.code == "MISSING_OR_ADDITIONAL_BATCH_METADATA"
    else:
        raise AssertionError("missing batch accepted")

    duplicate = copy.deepcopy(projections)
    duplicate[18]["batch_finalization_receipt_sha256"] = duplicate[17][
        "batch_finalization_receipt_sha256"
    ]
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            duplicate,
            expected_plan_sha256=plan_sha,
            expected_attempt_id=metadata.ATTEMPT_ID,
            expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
        )
    except metadata.R7GMetadataError as exc:
        assert exc.code == "DUPLICATE_BATCH_FINALIZATION_RECEIPT"
    else:
        raise AssertionError("duplicate receipt accepted")

    changed_plan = copy.deepcopy(plan)
    changed_rows = copy.deepcopy(projections)
    changed_plan["batches"][18]["source_bytes"] += 1
    changed_plan["cohort"]["selected_source_bytes"] += 1
    changed_rows[18]["source_bytes"] += 1
    changed_rows[18]["downloaded_bytes"] += 1
    changed_rows[18]["readable_bytes"] += 1
    changed_sha = metadata.canonical_json_sha256(changed_plan)
    for row in changed_rows:
        row["batch_plan_sha256"] = changed_sha
    try:
        metadata.validate_batch_metadata_partition(
            changed_plan,
            changed_rows,
            expected_plan_sha256=changed_sha,
            expected_attempt_id=metadata.ATTEMPT_ID,
            expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
        )
    except metadata.R7GMetadataError as exc:
        assert exc.code == "FULL_COHORT_FIXED_AGGREGATE_MISMATCH"
    else:
        raise AssertionError("nonfixed source-byte total accepted")


def test_cohort_binds_all_r7f_and_accounting_authorities() -> None:
    plan, projections, authority, cohort = _cohort_fixture()
    assert cohort["continuation_array_job_id"] == "7480830"
    assert cohort["cohort_finalizer_job_id"] == "7480831"
    assert {
        key: cohort[key] for key in metadata.R7F_AUTHORITY_RECEIPT_KEYS
    } == authority["r7f_authority_receipt_sha256"]
    assert cohort["terminal_authority_sha256"] == authority[
        "terminal_authority_sha256"
    ]
    assert set(cohort["accounting_receipt_sha256"]) == set(
        metadata.ACCOUNTING_ROLES
    )
    assert len(cohort["ordered_batch_finalization_receipts"]) == 19
    with mock.patch.object(metadata, "PLAN_SHA256", authority["plan_sha256"]):
        metadata.validate_cohort_finalization_receipt(
            cohort, plan, projections, **authority
        )


def test_r7f_hash_substitution_and_blocking_failure_fail() -> None:
    plan, plan_sha, projections = _fixture()
    authority = _authorities(plan_sha)
    authority["r7f_authority_receipt_sha256"] = _r7f_hashes()
    authority["r7f_authority_receipt_sha256"][
        "r7f_capacity_receipt_sha256"
    ] = _hash("substitution")
    with mock.patch.object(metadata, "PLAN_SHA256", plan_sha):
        try:
            metadata.build_cohort_finalization_receipt(
                plan, projections, **authority
            )
        except metadata.R7GMetadataError as exc:
            assert exc.code == "R7F_AUTHORITY_HASH_MISMATCH"
        else:
            raise AssertionError("R7F hash substitution accepted")

    projections[2]["n_blocking_failures"] = 1
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections,
            expected_plan_sha256=plan_sha,
            expected_attempt_id=metadata.ATTEMPT_ID,
            expected_scientific_commit=metadata.SCIENTIFIC_COMMIT,
        )
    except metadata.R7GMetadataError as exc:
        assert exc.code == "BATCH_DERIVED_COUNT_CONTRADICTION"
    else:
        raise AssertionError("blocking failure accepted")


def _lock_inputs() -> tuple[dict, dict, dict, str]:
    _plan, _projections, authority, cohort = _cohort_fixture()
    repository = {
        "branch": BRANCH,
        "local_head": ADJUDICATION,
        "origin_head": ADJUDICATION,
        "scc_head": ADJUDICATION,
        "local_tracked_clean": True,
        "scc_tracked_clean": True,
    }
    scheduler = {
        "matching_active_jobs": 0,
        "matching_active_processes": 0,
        "qstat_projection_sha256": _hash("qstat"),
        "process_projection_sha256": _hash("process"),
    }
    return cohort, repository, scheduler, authority["plan_sha256"]


def test_lock_publication_reopen_and_existing_reuse() -> None:
    cohort, repository, scheduler, plan_sha = _lock_inputs()
    cohort_sha = metadata.canonical_json_sha256(cohort)
    kwargs = {
        "cohort_receipt_sha256": cohort_sha,
        "repository_state": repository,
        "scheduler_state": scheduler,
        "expected_branch": BRANCH,
        "created_at_utc": CREATED,
    }
    with mock.patch.object(metadata, "PLAN_SHA256", plan_sha):
        lock = metadata.build_post_reconstruction_lock_receipt(cohort, **kwargs)
        assert lock["exact_335984_source_objects"] is True
        assert lock["exact_1216569133322_source_bytes"] is True
        assert lock["no_blocking_failure"] is True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            path = root / "post_reconstruction_lock_receipt.restricted.json"
            digest, created = metadata.publish_post_reconstruction_lock_receipt(
                path,
                lock,
                validation_kwargs={"cohort_receipt": cohort, **kwargs},
            )
            assert created is True
            assert (
                os.stat(path, follow_symlinks=False).st_mode & 0o7777
            ) == 0o600
            assert metadata.publish_post_reconstruction_lock_receipt(
                path,
                lock,
                validation_kwargs={"cohort_receipt": cohort, **kwargs},
            ) == (digest, False)


def test_lock_reuse_requires_the_exact_canonical_r7g_cohort_hash() -> None:
    cohort, repository, scheduler, plan_sha = _lock_inputs()
    cohort_sha = metadata.canonical_json_sha256(cohort)
    with mock.patch.object(metadata, "PLAN_SHA256", plan_sha):
        lock = metadata.build_post_reconstruction_lock_receipt(
            cohort,
            cohort_receipt_sha256=cohort_sha,
            repository_state=repository,
            scheduler_state=scheduler,
            expected_branch=BRANCH,
            created_at_utc=CREATED,
            cohort_finalization_mode="REUSED_EXISTING",
        )
    assert lock["cohort_finalization_receipt_sha256"] == cohort_sha
    assert lock["cohort_finalization_mode"] == "REUSED_EXISTING"

    with mock.patch.object(metadata, "PLAN_SHA256", plan_sha):
        try:
            metadata.build_post_reconstruction_lock_receipt(
                cohort,
                cohort_receipt_sha256=_hash("historical-non-r7g-cohort"),
                repository_state=repository,
                scheduler_state=scheduler,
                expected_branch=BRANCH,
                created_at_utc=CREATED,
                cohort_finalization_mode="REUSED_EXISTING",
            )
        except metadata.R7GMetadataError as exc:
            assert exc.code == "LOCK_COHORT_RECEIPT_INVALID"
        else:
            raise AssertionError("unrelated historical cohort hash was accepted")


def test_metadata_surface_has_no_scientific_execution_path() -> None:
    source = inspect.getsource(metadata)
    assert "numpy" not in source
    assert "np.load" not in source
    assert "finalize_receipts" not in source
    assert "subprocess" not in source
    assert "os.system" not in source


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
