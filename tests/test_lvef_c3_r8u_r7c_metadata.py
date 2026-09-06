#!/usr/bin/env python3
"""Dependency-light tests for metadata-only R8U-R7C finalization."""
from __future__ import annotations

import copy
import inspect
import os
from pathlib import Path
import sys
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8u_r7c_metadata as metadata


ATTEMPT = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
SCIENTIFIC = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
RUNTIME = "1be99c6436293a7cad576e9855ba4cd58a71e156"
ADJUDICATION = "2" * 40
CREATED = "2026-09-06T18:00:00Z"
BRANCH = "codex/lvef-multitask-revalidation"


def _hash(label: str) -> str:
    return metadata.canonical_json_sha256({"label": label})


def _fixture() -> tuple[dict, str, list[dict]]:
    batches: list[dict] = []
    projections: list[dict] = []
    next_identifier = 10_000_000
    total_objects = 0
    total_bytes = 0
    for ordinal, batch_id in enumerate(metadata.EXPECTED_BATCH_IDS):
        n_studies = 30 if ordinal == 18 else 250
        studies = []
        for offset in range(n_studies):
            identifier = str(next_identifier + offset)
            studies.append(
                {"subject_id": identifier, "study_id": identifier, "split": "train"}
            )
        next_identifier += n_studies
        n_objects = n_studies * 2
        source_bytes = n_studies * 101
        no_cine = 5 if ordinal == 0 else 0
        no_cine_keys = [
            {"subject_id": row["subject_id"], "study_id": row["study_id"]}
            for row in studies[:no_cine]
        ]
        study_membership_sha = metadata.canonical_json_sha256(studies)
        no_cine_sha = metadata.canonical_json_sha256(no_cine_keys)
        batches.append(
            {
                "batch_id": batch_id,
                "ordinal": ordinal,
                "n_studies": n_studies,
                "n_subjects": n_studies,
                "n_objects": n_objects,
                "source_bytes": source_bytes,
                "study_membership_sha256": study_membership_sha,
                "expected_no_cine_studies": no_cine,
                "prespecified_no_cine_study_set_sha256": no_cine_sha,
                "studies": studies,
            }
        )
        total_objects += n_objects
        total_bytes += source_bytes
    plan = {
        "schema_version": 3,
        "artifact_type": "lvef_c3_restricted_immutable_batch_plan_v3",
        "cohort": {
            "selected_studies": 4530,
            "selected_subjects": 4530,
            "normalized_source_objects": total_objects,
            "selected_source_bytes": total_bytes,
            "expected_no_cine_studies": 5,
        },
        "batches": batches,
    }
    plan_sha = metadata.canonical_json_sha256(plan)
    for ordinal, batch in enumerate(batches):
        successful = 100 if ordinal < 18 else 12
        projection = {
            "batch_id": batch["batch_id"],
            "ordinal": ordinal,
            "attempt_id": ATTEMPT,
            "batch_plan_sha256": plan_sha,
            "scientific_commit": SCIENTIFIC,
            "batch_finalization_receipt_sha256": _hash(f"final-{ordinal}"),
            "preservation_receipt_sha256": _hash(f"preservation-{ordinal}"),
            "preservation_manifest_sha256": _hash(f"manifest-{ordinal}"),
            "extraction_stage_completion_receipt_sha256": _hash(f"stage-{ordinal}"),
            "extraction_summary_sha256": _hash(f"summary-{ordinal}"),
            "cache_retirement_transition_sha256": _hash(f"transition-{ordinal}"),
            "final_ledger_sha256": _hash(f"ledger-{ordinal}"),
            "retired_cache_tree_sha256": _hash(f"tree-{ordinal}"),
            "study_membership_sha256": batch["study_membership_sha256"],
            "prespecified_no_cine_study_set_sha256": batch[
                "prespecified_no_cine_study_set_sha256"
            ],
            "n_selected_studies": batch["n_studies"],
            "n_selected_subjects": batch["n_subjects"],
            "n_source_objects": batch["n_objects"],
            "source_bytes": batch["source_bytes"],
            "n_downloaded_objects": batch["n_objects"],
            "downloaded_bytes": batch["source_bytes"],
            "n_readable_objects": batch["n_objects"],
            "readable_bytes": batch["source_bytes"],
            "n_multiframe_candidates": successful,
            "n_successful_extractions": successful,
            "n_clip_embeddings": successful,
            "n_technical_dispositions": 0,
            "n_ordinary_preprocessing_path": successful,
            "n_spatial_fallback_preprocessing_path": 0,
            "n_temporal_fallback_preprocessing_path": 0,
            "n_spatial_temporal_fallback_preprocessing_path": 0,
            "n_study_embeddings": batch["n_studies"]
            - batch["expected_no_cine_studies"],
            "n_prespecified_no_cine_studies": batch["expected_no_cine_studies"],
            "n_new_no_cine_studies": 0,
            "retired_extracted_cache_bytes": successful * 4096,
            "n_missing_selected_studies": 0,
            "n_duplicate_selected_studies": 0,
            "n_source_substitutions": 0,
            "n_unaccounted_multiframe_candidates": 0,
            "n_outcome_informed_decisions": 0,
            "final_ledger_status": "FINALIZED",
            "preservation_status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE",
            "cache_retirement_status": "PASS_RETIRED",
            "batch_finalization_status": "PASS_BATCH_FINALIZED",
            "raw_source_authority_retained": True,
            "extracted_cache_absent": True,
        }
        projections.append(projection)
    return plan, plan_sha, projections


def _authorities(projections: list[dict]) -> dict:
    return {
        "attempt_id": ATTEMPT,
        "plan_sha256": projections[0]["batch_plan_sha256"],
        "scientific_commit": SCIENTIFIC,
        "runtime_implementation_commit": RUNTIME,
        "adjudication_implementation_commit": ADJUDICATION,
        "batch16_final_receipt_sha256": projections[15][
            "batch_finalization_receipt_sha256"
        ],
        "continuation_receipt_sha256": _hash("continuation"),
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


def _cohort_fixture() -> tuple[dict, str, list[dict], dict, dict]:
    plan, plan_sha, projections = _fixture()
    authority = _authorities(projections)
    receipt = metadata.build_cohort_finalization_receipt(
        plan, projections, **authority
    )
    return plan, plan_sha, projections, authority, receipt


def test_valid_19_batch_partition_and_batch19_is_30() -> None:
    plan, plan_sha, projections = _fixture()
    observed = metadata.validate_batch_metadata_partition(
        plan,
        projections,
        expected_plan_sha256=plan_sha,
        expected_attempt_id=ATTEMPT,
        expected_scientific_commit=SCIENTIFIC,
    )
    assert observed["aggregate_totals"]["finalized_batches"] == 19
    assert observed["aggregate_totals"]["n_selected_studies"] == 4530
    assert projections[18]["n_selected_studies"] == 30
    assert observed["study_partition"]["exact_plan_equality"] is True


def test_missing_batch_is_rejected() -> None:
    plan, plan_sha, projections = _fixture()
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections[:-1],
            expected_plan_sha256=plan_sha,
            expected_attempt_id=ATTEMPT,
            expected_scientific_commit=SCIENTIFIC,
        )
    except metadata.R7CMetadataError as exc:
        assert exc.code == "MISSING_OR_ADDITIONAL_BATCH_METADATA"
    else:
        raise AssertionError("missing Batch 19 accepted")


def test_duplicate_study_across_batches_is_rejected() -> None:
    plan, _plan_sha, projections = _fixture()
    plan = copy.deepcopy(plan)
    plan["batches"][18]["studies"][0]["study_id"] = plan["batches"][0]["studies"][0]["study_id"]
    plan["batches"][18]["study_membership_sha256"] = metadata.canonical_json_sha256(
        plan["batches"][18]["studies"]
    )
    plan_sha = metadata.canonical_json_sha256(plan)
    for projection in projections:
        projection["batch_plan_sha256"] = plan_sha
    projections[18]["study_membership_sha256"] = plan["batches"][18][
        "study_membership_sha256"
    ]
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections,
            expected_plan_sha256=plan_sha,
            expected_attempt_id=ATTEMPT,
            expected_scientific_commit=SCIENTIFIC,
        )
    except metadata.R7CMetadataError as exc:
        assert exc.code == "DUPLICATE_STUDY_ACROSS_BATCHES"
    else:
        raise AssertionError("duplicate study accepted")


def test_batch_and_cohort_aggregate_mismatch_is_rejected() -> None:
    plan, plan_sha, projections = _fixture()
    projections[17]["n_clip_embeddings"] -= 1
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections,
            expected_plan_sha256=plan_sha,
            expected_attempt_id=ATTEMPT,
            expected_scientific_commit=SCIENTIFIC,
        )
    except metadata.R7CMetadataError as exc:
        assert exc.code == "BATCH_CLIP_ACCOUNTING_MISMATCH"
    else:
        raise AssertionError("within-batch aggregate mismatch accepted")

    plan, _plan_sha, projections = _fixture()
    plan["batches"][18]["n_objects"] += 1
    plan_sha = metadata.canonical_json_sha256(plan)
    for projection in projections:
        projection["batch_plan_sha256"] = plan_sha
    for key in (
        "n_source_objects",
        "n_downloaded_objects",
        "n_readable_objects",
    ):
        projections[18][key] += 1
    try:
        metadata.validate_batch_metadata_partition(
            plan,
            projections,
            expected_plan_sha256=plan_sha,
            expected_attempt_id=ATTEMPT,
            expected_scientific_commit=SCIENTIFIC,
        )
    except metadata.R7CMetadataError as exc:
        assert exc.code == "FULL_COHORT_AGGREGATE_MISMATCH"
    else:
        raise AssertionError("full-cohort aggregate mismatch accepted")


def test_cohort_receipt_is_deterministic_and_strict() -> None:
    plan, _plan_sha, projections, authority, receipt = _cohort_fixture()
    second = metadata.build_cohort_finalization_receipt(plan, projections, **authority)
    assert receipt == second
    assert metadata.canonical_json_sha256(receipt) == metadata.canonical_json_sha256(second)
    metadata.validate_cohort_finalization_receipt(
        receipt, plan, projections, **authority
    )
    tampered = copy.deepcopy(receipt)
    tampered["aggregate_totals"]["n_clip_embeddings"] += 1
    try:
        metadata.validate_cohort_finalization_receipt(
            tampered, plan, projections, **authority
        )
    except metadata.R7CMetadataError as exc:
        assert exc.code == "COHORT_RECEIPT_MISMATCH"
    else:
        raise AssertionError("tampered cohort aggregate accepted")


def test_cohort_publication_is_0600_reopened_and_valid_reuse_only() -> None:
    plan, _plan_sha, projections, authority, receipt = _cohort_fixture()
    kwargs = {"plan": plan, "batch_metadata": projections, **authority}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        path = root / "cohort_finalization_receipt.restricted.json"
        digest, created = metadata.publish_cohort_finalization_receipt(
            path, receipt, validation_kwargs=kwargs
        )
        assert created is True
        assert digest == metadata.canonical_json_sha256(receipt)
        assert stat_mode(path) == 0o600
        observed, payload = metadata.read_private_json(path)
        assert observed == receipt
        assert payload == metadata.canonical_json_bytes(receipt)
        assert metadata.publish_cohort_finalization_receipt(
            path, receipt, validation_kwargs=kwargs
        ) == (digest, False)
        contradictory = copy.deepcopy(receipt)
        contradictory["created_at_utc"] = "2026-09-06T18:00:01Z"
        contradictory_kwargs = dict(kwargs)
        contradictory_kwargs["created_at_utc"] = contradictory["created_at_utc"]
        try:
            metadata.publish_cohort_finalization_receipt(
                path, contradictory, validation_kwargs=contradictory_kwargs
            )
        except metadata.R7CMetadataError as exc:
            assert exc.code == "EXISTING_RECEIPT_CONTRADICTS_EXPECTED"
        else:
            raise AssertionError("contradictory receipt clobbered existing output")


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o7777


def test_private_publication_forces_0600_under_restrictive_umask() -> None:
    plan, _plan_sha, projections, authority, receipt = _cohort_fixture()
    kwargs = {"plan": plan, "batch_metadata": projections, **authority}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        path = root / "cohort_finalization_receipt.restricted.json"
        prior_umask = os.umask(0o777)
        try:
            _digest, created = metadata.publish_cohort_finalization_receipt(
                path, receipt, validation_kwargs=kwargs
            )
        finally:
            os.umask(prior_umask)
        assert created is True
        assert stat_mode(path) == 0o600
        observed, payload = metadata.read_private_json(path)
        assert observed == receipt
        assert payload == metadata.canonical_json_bytes(receipt)


def test_symlink_receipt_path_is_rejected_without_clobber() -> None:
    plan, _plan_sha, projections, authority, receipt = _cohort_fixture()
    kwargs = {"plan": plan, "batch_metadata": projections, **authority}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        real = root / "real"
        real.write_text("{}", encoding="ascii")
        os.chmod(real, 0o600)
        link = root / "cohort_finalization_receipt.restricted.json"
        link.symlink_to(real.name)
        try:
            metadata.publish_cohort_finalization_receipt(
                link, receipt, validation_kwargs=kwargs
            )
        except metadata.R7CMetadataError as exc:
            assert exc.code == "PRIVATE_RECEIPT_NOT_OWNER_REGULAR_0600"
        else:
            raise AssertionError("symlink receipt accepted")
        assert real.read_text(encoding="ascii") == "{}"


def test_post_reconstruction_lock_build_validate_and_publish() -> None:
    _plan, _plan_sha, _projections, _authority, cohort = _cohort_fixture()
    cohort_sha = metadata.canonical_json_sha256(cohort)
    repository_state = {
        "branch": BRANCH,
        "local_head": ADJUDICATION,
        "origin_head": ADJUDICATION,
        "scc_head": ADJUDICATION,
        "local_tracked_clean": True,
        "scc_tracked_clean": True,
    }
    scheduler_state = {
        "matching_active_jobs": 0,
        "matching_active_processes": 0,
        "qstat_projection_sha256": _hash("qstat"),
        "process_projection_sha256": _hash("process"),
    }
    lock = metadata.build_post_reconstruction_lock_receipt(
        cohort,
        cohort_receipt_sha256=cohort_sha,
        repository_state=repository_state,
        scheduler_state=scheduler_state,
        expected_branch=BRANCH,
        created_at_utc=CREATED,
    )
    kwargs = {
        "cohort_receipt": cohort,
        "cohort_receipt_sha256": cohort_sha,
        "repository_state": repository_state,
        "scheduler_state": scheduler_state,
        "expected_branch": BRANCH,
        "created_at_utc": CREATED,
    }
    metadata.validate_post_reconstruction_lock_receipt(lock, **kwargs)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        path = root / "post_reconstruction_lock_receipt.restricted.json"
        digest, created = metadata.publish_post_reconstruction_lock_receipt(
            path, lock, validation_kwargs=kwargs
        )
        assert created is True
        assert digest == metadata.canonical_json_sha256(lock)
        assert stat_mode(path) == 0o600


def test_lock_can_bind_separately_validated_original_cohort_receipt() -> None:
    _plan, _plan_sha, _projections, _authority, cohort = _cohort_fixture()
    original_receipt_sha = _hash("original-finalizer-receipt")
    repository_state = {
        "branch": BRANCH,
        "local_head": ADJUDICATION,
        "origin_head": ADJUDICATION,
        "scc_head": ADJUDICATION,
        "local_tracked_clean": True,
        "scc_tracked_clean": True,
    }
    scheduler_state = {
        "matching_active_jobs": 0,
        "matching_active_processes": 0,
        "qstat_projection_sha256": _hash("qstat-original"),
        "process_projection_sha256": _hash("process-original"),
    }
    lock = metadata.build_post_reconstruction_lock_receipt(
        cohort,
        cohort_receipt_sha256=original_receipt_sha,
        cohort_finalization_mode="REUSED_EXISTING",
        repository_state=repository_state,
        scheduler_state=scheduler_state,
        expected_branch=BRANCH,
        created_at_utc=CREATED,
    )
    assert lock["cohort_finalization_mode"] == "REUSED_EXISTING"
    assert lock["cohort_finalization_receipt_sha256"] == original_receipt_sha
    metadata.validate_post_reconstruction_lock_receipt(
        lock,
        cohort,
        cohort_receipt_sha256=original_receipt_sha,
        cohort_finalization_mode="REUSED_EXISTING",
        repository_state=repository_state,
        scheduler_state=scheduler_state,
        expected_branch=BRANCH,
        created_at_utc=CREATED,
    )


def test_zero_scientific_execution_invariant_and_source_are_body_free() -> None:
    actions = metadata.zero_scientific_actions()
    actions["npz_body_reads"] = 1
    try:
        metadata.validate_zero_scientific_actions(actions)
    except metadata.R7CMetadataError as exc:
        assert exc.code == "PROHIBITED_SCIENTIFIC_ACTION_OBSERVED"
    else:
        raise AssertionError("NPZ body read accepted")
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
