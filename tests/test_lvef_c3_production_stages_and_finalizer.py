from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
from argparse import Namespace
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stages = _load("lvef_c3_production_stages_test", "lvef_c3_production_stages.py")
finalizer = _load("finalize_lvef_c3_production_test", "finalize_lvef_c3_production.py")
dispatch_auth = _load(
    "validate_lvef_c3_dispatch_authorization_test",
    "validate_lvef_c3_dispatch_authorization.py",
)
retirement = _load(
    "retire_lvef_c3_extracted_cache_v2_test",
    "retire_lvef_c3_extracted_cache_v2.py",
)
owner_authorization = _load(
    "build_lvef_c3_owner_authorization_receipt_test",
    "build_lvef_c3_owner_authorization_receipt.py",
)
prior_batch_gate = _load(
    "validate_lvef_c3_prior_batch_finalization_test",
    "validate_lvef_c3_prior_batch_finalization.py",
)
analysis_modes = _load(
    "lvef_multitask_analysis_modes_test", "lvef_multitask_analysis_modes.py"
)


def _synthetic_dispatch_launch(root: Path) -> tuple[Path, dict[str, object]]:
    path = root / "launch_authority.restricted.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o600)
    value: dict[str, object] = {
        "post_expansion_capacity_summary": {"sha256": "d" * 64},
        "production_authority_packet": {"sha256": "e" * 64},
    }
    return path, value


def _dispatch_payload(
    *, root: Path, contract: Path, plan: Path, environment: Path,
    stage: str, task_scope: str,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    launch_path, launch_value = _synthetic_dispatch_launch(root)
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_dispatch_authorization_v1",
        "status": "AUTHORIZED",
        "stage": stage,
        "authorized_array_range": task_scope,
        "governing_commit": "a" * 40,
        "attempt_id": "lvef_c3_phase1ee_synthetic_001",
        "orchestration_contract_sha256": dispatch_auth.sha256_file(contract),
        "batch_plan_sha256": dispatch_auth.sha256_file(plan),
        "execution_environment_sha256": dispatch_auth.sha256_file(environment),
        "launch_authority_sha256": dispatch_auth.sha256_file(launch_path),
        "post_expansion_capacity_summary_sha256": "d" * 64,
        "production_authority_packet_sha256": "e" * 64,
        "owner_authorized": True,
        "owner_authorization_date": "2026-08-10",
    }
    return launch_path, launch_value, payload


def _validate_dispatch_with_stub(
    receipt: Path, *, launch_path: Path, launch_value: dict[str, object], **kwargs
) -> None:
    with mock.patch.object(
        dispatch_auth.launch, "_load_json", return_value=launch_value
    ), mock.patch.object(dispatch_auth.launch, "validate", return_value=launch_value):
        dispatch_auth.validate(
            receipt,
            launch_authority=launch_path,
            attempt_id="lvef_c3_phase1ee_synthetic_001",
            **kwargs,
        )


def expect_code(code: str, function) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", None) == code
    else:
        raise AssertionError(f"Expected {code}")


def test_production_dicom_validator_separates_single_multiframe_and_decode_failure() -> None:
    rows = [
        {
            "subject_id": 1,
            "study_id": 10,
            "source_relative_path": "files/a.dcm",
            "read_ok": True,
            "is_multiframe": True,
            "pixel_decode_ok": True,
        },
        {
            "subject_id": 1,
            "study_id": 10,
            "source_relative_path": "files/b.dcm",
            "read_ok": True,
            "is_multiframe": False,
            "pixel_decode_ok": False,
        },
        {
            "subject_id": 2,
            "study_id": 20,
            "source_relative_path": "files/c.dcm",
            "read_ok": True,
            "is_multiframe": True,
            "pixel_decode_ok": False,
        },
    ]
    value = stages.validate_production_dicom_rows(
        rows, expected_objects=3, expected_studies=2
    )
    assert value["n_multiframe_candidates"] == 2
    assert value["n_single_frame"] == 1
    assert value["n_pixel_decode_failures"] == 1


def test_production_dicom_validator_rejects_contradiction_and_duplicate_source() -> None:
    row = {
        "subject_id": 1,
        "study_id": 10,
        "source_relative_path": "files/a.dcm",
        "read_ok": False,
        "is_multiframe": True,
        "pixel_decode_ok": False,
    }
    expect_code(
        "DICOM_AUDIT_STATE_CONTRADICTION",
        lambda: stages.validate_production_dicom_rows(
            [row], expected_objects=1, expected_studies=1
        ),
    )
    row["read_ok"] = True
    row["pixel_decode_ok"] = True
    expect_code(
        "DUPLICATE_PHYSICAL_SOURCE",
        lambda: stages.validate_production_dicom_rows(
            [row, dict(row)], expected_objects=2, expected_studies=1
        ),
    )


def _extraction_row(seed: str = "a") -> dict[str, object]:
    return {
        "study_id": 1,
        "clip_key": hashlib.sha256(f"clip-{seed}".encode()).hexdigest(),
        "physical_source_key": hashlib.sha256(f"source-{seed}".encode()).hexdigest(),
        "write_ok": True,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "mask_status": "APPLIED",
        "temporal_sampling_policy": "historical_compatible_linspace_or_tail_repeat_v1",
        "pixel_decode_ok": True,
        "npz_sha256": hashlib.sha256(f"npz-{seed}".encode()).hexdigest(),
    }


def test_production_extraction_validator_rejects_shape_and_duplicate_clip_key() -> None:
    good = _extraction_row()
    assert stages.validate_production_extraction_rows(
        [good], expected_cines=1
    )["all_shapes_and_dtypes_valid"]
    malformed = dict(good, frames_shape="31x224x224x3")
    expect_code(
        "EXTRACTION_SHAPE_OR_DTYPE_MISMATCH",
        lambda: stages.validate_production_extraction_rows(
            [malformed], expected_cines=1
        ),
    )
    duplicate = _extraction_row("b")
    duplicate["clip_key"] = good["clip_key"]
    expect_code(
        "DUPLICATE_CLIP_KEY",
        lambda: stages.validate_production_extraction_rows(
            [good, duplicate], expected_cines=2
        ),
    )


def test_embedding_validator_rejects_wrong_dimension_nonfinite_and_boolean() -> None:
    good = [[0.25] * 512]
    assert stages.validate_embedding_values(good, expected_rows=1)["all_finite"]
    expect_code(
        "EMBEDDING_DIMENSION_MISMATCH",
        lambda: stages.validate_embedding_values([[0.25] * 511], expected_rows=1),
    )
    nonfinite = [[0.25] * 512]
    nonfinite[0][8] = float("nan")
    expect_code(
        "EMBEDDING_VALUE_NONFINITE",
        lambda: stages.validate_embedding_values(nonfinite, expected_rows=1),
    )
    boolean = [[0.25] * 512]
    boolean[0][8] = True
    expect_code(
        "EMBEDDING_VALUE_NOT_NUMERIC",
        lambda: stages.validate_embedding_values(boolean, expected_rows=1),
    )


def test_stage_authorization_is_closed_and_stage_specific() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "authorization.json"
        payload = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
            "status": "AUTHORIZED",
            "authorization_scope": "EXTRACTION_AUTHORIZATION",
            "stage": "DICOM_EXTRACTION",
            "batch_id": "c3_batch_000",
            "attempt_id": "lvef_c3_attempt_001",
            "governing_commit": "a" * 40,
            "orchestration_contract_sha256": "b" * 64,
            "batch_plan_sha256": "c" * 64,
            "launch_authority_sha256": "d" * 64,
            "owner_authorized": True,
            "owner_authorization_date": "2026-08-10",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert stages.validate_stage_authorization(
            path,
            stage="DICOM_EXTRACTION",
            batch_id="c3_batch_000",
            attempt_id="lvef_c3_attempt_001",
            governing_commit="a" * 40,
            orchestration_contract_sha256="b" * 64,
            batch_plan_sha256="c" * 64,
            launch_authority_sha256="d" * 64,
        )["owner_authorized"]
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "STAGE_AUTHORIZATION_SCHEMA_MISMATCH",
            lambda: stages.validate_stage_authorization(
                path,
                stage="DICOM_EXTRACTION",
                batch_id="c3_batch_000",
                attempt_id="lvef_c3_attempt_001",
                governing_commit="a" * 40,
                orchestration_contract_sha256="b" * 64,
                batch_plan_sha256="c" * 64,
                launch_authority_sha256="d" * 64,
            ),
        )


def _batch_receipt(index: int) -> dict[str, object]:
    studies = 250 if index < 18 else 30
    objects_base, remainder = divmod(finalizer.EXPECTED_SOURCE_OBJECTS, 19)
    source_base, source_remainder = divmod(finalizer.EXPECTED_SOURCE_BYTES, 19)
    objects = objects_base + int(index < remainder)
    source_bytes = source_base + int(index < source_remainder)
    no_cine = 5 if index == 0 else 0
    multiframe = objects - 1
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_batch_finalization_receipt_v2",
        "status": "PASS_BATCH_FINALIZED",
        "batch_id": f"c3_batch_{index:03d}",
        "attempt_id": "lvef_c3_attempt_shared",
        "governing_commit": "a" * 40,
        "source_commit": "a" * 40,
        "run_timestamp_utc": "2026-08-10T12:00:00+00:00",
        "cohort_version": "mimic-iv-echo/1.0",
        "split_version": f"split_map_sha256:{'d' * 64}",
        "execution_contract_version": 2,
        "orchestration_contract_sha256": "b" * 64,
        "batch_plan_sha256": "c" * 64,
        "checkpoint_sha256": "6" * 64,
        "checkpoint_checksum": "6" * 64,
        "environment_receipt_sha256": "7" * 64,
        "python_version": "3.10.12",
        "pytorch_version": "2.11.0+cu130",
        "torchvision_version": "0.26.0+cu130",
        "cuda_version": "13.0",
        "cudnn_version": "91002",
        "package_inventory_sha256": "e" * 64,
        "production_stage_wrapper_sha256": "8" * 64,
        "batch_preservation_script_sha256": "8" * 64,
        "scheduler_runner_sha256": "9" * 64,
        "command_checksum": "0" * 64,
        "config_checksum": "1" * 64,
        "scheduler_job_identity": f"7100000.{index + 1}",
        "state_input_ledger_sha256": "0" * 64,
        "n_selected_studies": studies,
        "n_selected_subjects": studies,
        "n_expected_objects": objects,
        "expected_source_bytes": source_bytes,
        "n_download_verified": objects,
        "n_dicom_readable": objects,
        "n_dicom_unreadable": 0,
        "n_multiframe_cines": multiframe,
        "n_single_frame_objects": 1,
        "n_extracted_clips": multiframe,
        "n_unique_clip_keys": multiframe,
        "n_clip_embeddings": multiframe,
        "n_pooled_studies": studies - no_cine,
        "n_no_cine_studies": no_cine,
        "no_cine_disposition": (
            "IMAGING_INELIGIBLE_NO_MULTIFRAME_CINE" if no_cine else "NONE"
        ),
        "n_outside_selected_studies": 0,
        "n_missing_selected_studies": 0,
        "n_duplicate_physical_sources": 0,
        "n_duplicate_clip_keys": 0,
        "n_nonfinite_embeddings": 0,
        "n_wrong_dimension_embeddings": 0,
        "source_receipt_sha256": "d" * 64,
        "dicom_audit_sha256": "e" * 64,
        "extraction_manifest_sha256": "f" * 64,
        "clip_manifest_sha256": "1" * 64,
        "clip_embeddings_sha256": "2" * 64,
        "study_manifest_sha256": "3" * 64,
        "study_embeddings_sha256": "4" * 64,
        "preservation_manifest_sha256": "5" * 64,
        "cache_retirement_authorization_sha256": "a" * 64,
        "cache_tree_sha256": "b" * 64,
        "cache_atomically_staged_receipt_sha256": "c" * 64,
        "cache_retirement_script_sha256": "d" * 64,
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


def _write_receipts(root: Path) -> list[Path]:
    paths = []
    for index in range(19):
        path = root / f"receipt_{index:03d}.json"
        path.write_text(json.dumps(_batch_receipt(index)), encoding="utf-8")
        paths.append(path)
    return paths


def test_production_finalizer_reconciles_exact_cohort_and_five_no_cine() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary = finalizer.finalize_receipts(
            _write_receipts(Path(directory)), expected_governing_commit="a" * 40
        )
        assert set(summary) == finalizer.FINAL_KEYS
        assert summary["selected_studies"] == 4530
        assert summary["verified_source_objects"] == 335984
        assert summary["selected_source_bytes"] == 1216569133322
        assert summary["pooled_imaging_eligible_studies"] == 4525
        assert summary["no_cine_studies"] == 5
        assert summary["raw_dicoms_retained"] is True
        assert summary["extracted_cache_retired"] is True
        policy, _ = analysis_modes.load_policy(
            ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
        )
        result = analysis_modes.validate_candidate_bytes(
            (json.dumps(summary, sort_keys=True) + "\n").encode("utf-8"),
            filename="lvef_c3_production_finalization.summary.json",
            profile_name="lvef_c3_production_finalization_summary_json",
            policy=policy,
        )
        assert result["status"] == "PASS"


def test_production_finalizer_fails_on_missing_batch_or_scientific_inconsistency() -> None:
    with tempfile.TemporaryDirectory() as directory:
        paths = _write_receipts(Path(directory))
        expect_code(
            "FINAL_BATCH_SET_INCOMPLETE",
            lambda: finalizer.finalize_receipts(
                paths[:-1], expected_governing_commit="a" * 40
            ),
        )
        payload = json.loads(paths[0].read_text())
        payload["n_duplicate_clip_keys"] = 1
        paths[0].write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "SCIENTIFIC_INCONSISTENCY",
            lambda: finalizer.finalize_receipts(
                paths, expected_governing_commit="a" * 40
            ),
        )


def test_finalizer_rejects_cross_batch_clip_key_collision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        global_clips: set[str] = set()
        global_sources: set[str] = set()

        def write_manifest(name: str, clip: str, source: str, subject: str, study: str) -> Path:
            path = root / name
            path.write_text(
                ",".join(finalizer.CLIP_MANIFEST_HEADER)
                + "\n"
                + f"0,{subject},{study},{clip},{source},1.0,{'e' * 64},True\n",
                encoding="utf-8",
            )
            return path

        first_source = "1" * 64
        second_source = "2" * 64
        clip = "a" * 64
        finalizer.accumulate_global_clip_authority(
            write_manifest("first.csv", clip, first_source, "1", "10"),
            planned_batch={
                "objects": [{"source_object_key": first_source, "subject_id": "1", "study_id": "10"}]
            },
            expected_rows=1,
            global_clip_keys=global_clips,
            global_physical_source_keys=global_sources,
        )
        expect_code(
            "GLOBAL_CLIP_KEY_COLLISION",
            lambda: finalizer.accumulate_global_clip_authority(
                write_manifest("second.csv", clip, second_source, "2", "20"),
                planned_batch={
                    "objects": [{"source_object_key": second_source, "subject_id": "2", "study_id": "20"}]
                },
                expected_rows=1,
                global_clip_keys=global_clips,
                global_physical_source_keys=global_sources,
            ),
        )


def test_finalizer_replays_retained_raw_and_allows_only_retired_npz_cache() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        attempt = "lvef_c3_synthetic_001"
        batch = "c3_batch_000"
        prefix = Path("attempts") / attempt
        raw = root / prefix / "raw" / batch / "objects" / "source.dcm"
        metadata = (
            root / prefix / "extracted_cache" / batch / "dicom_extraction"
            / "dicom_audit.restricted.csv"
        )
        embedding = root / prefix / "batches" / batch / "echoprime" / "clip_manifest.restricted.csv"
        ledger = root / prefix / "batches" / batch / "download_resume_ledger.restricted.json"
        for path, body in (
            (raw, b"raw"), (metadata, b"metadata"),
            (embedding, b"embedding"), (ledger, b"ledger"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        retired_relative = (
            prefix / "extracted_cache" / batch / "dicom_extraction" / "clips" / "clip.npz"
        ).as_posix()
        retired_body = b"retired-npz"
        records = [
            (raw, "raw_dicom_and_download_authority"),
            (metadata, "dicom_extraction_metadata_retained"),
            (embedding, "embedding_and_pooling_retained"),
            (ledger, "download_ledger"),
        ]
        rows = ["\t".join(finalizer.PRESERVATION_MANIFEST_HEADER)]
        for path, role in records:
            rows.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t"
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}\t{role}"
            )
        retired_sha = hashlib.sha256(retired_body).hexdigest()
        rows.append(
            f"{retired_relative}\t{len(retired_body)}\t{retired_sha}\t"
            "extracted_npz_cache_owner_retirable"
        )
        manifest = root / "preservation.tsv"
        manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
        cache_tree_sha = hashlib.sha256(
            f"clip.npz\t{len(retired_body)}\t{retired_sha}\n".encode("utf-8")
        ).hexdigest()
        finalizer.replay_batch_preservation_manifest(
            manifest,
            production_root=root,
            attempt_id=attempt,
            batch_id=batch,
            expected_retired_cache_tree_sha256=cache_tree_sha,
        )
        unlisted = raw.parent / "unlisted.dcm"
        unlisted.write_bytes(b"unlisted")
        expect_code(
            "UNLISTED_OR_MISSING_RETAINED_ARTIFACT",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )
        unlisted.unlink()
        raw.unlink()
        expect_code(
            "RETAINED_ARTIFACT_MISSING",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )
        raw.write_bytes(b"bad")
        expect_code(
            "RETAINED_ARTIFACT_HASH_MISMATCH",
            lambda: finalizer.replay_batch_preservation_manifest(
                manifest, production_root=root, attempt_id=attempt,
                batch_id=batch, expected_retired_cache_tree_sha256=cache_tree_sha,
            ),
        )


def test_finalizer_rejects_duplicate_json_keys_symlink_and_output_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        duplicate = root / "duplicate.json"
        duplicate.write_text('{"status":"PASS","status":"FAIL"}\n', encoding="utf-8")
        expect_code(
            "DUPLICATE_JSON_KEY",
            lambda: finalizer.load_json(duplicate, "BATCH_RECEIPT"),
        )
        target = root / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        link = root / "link.json"
        link.symlink_to(target)
        expect_code(
            "BATCH_RECEIPT_NOT_REGULAR",
            lambda: finalizer.load_json(link, "BATCH_RECEIPT"),
        )
        expect_code(
            "FINAL_OUTPUT_ALREADY_EXISTS",
            lambda: finalizer.write_json_atomic(target, {"status": "PASS"}),
        )


def test_dispatch_authorization_is_stage_specific_hash_bound_and_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=not-exported\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="FIRST_BATCH_DOWNLOAD",
            task_scope="1",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="FIRST_BATCH_DOWNLOAD",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
        )
        expect_code(
            "DISPATCH_RECEIPT_AUTHORITY_MISMATCH",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="REMAINING_BATCH_DOWNLOAD",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
                authorized_array_range="2",
            ),
        )


def test_dispatch_authorization_requires_exact_single_rolling_batch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=literal\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="DICOM_EXTRACTION",
            task_scope="7",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="DICOM_EXTRACTION",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
            authorized_array_range="7",
        )
        expect_code(
            "DISPATCH_ARRAY_SCOPE_INVALID",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="DICOM_EXTRACTION",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
                authorized_array_range="1-19",
            ),
        )


def test_remaining_download_authorization_is_exactly_one_batch_2_to_19() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        contract = root / "contract.yaml"
        plan = root / "plan.json"
        environment = root / "environment"
        receipt = root / "authorization.json"
        contract.write_text("schema_version: 2\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("PRIVATE=literal\n", encoding="utf-8")
        launch_path, launch_value, payload = _dispatch_payload(
            root=root,
            contract=contract,
            plan=plan,
            environment=environment,
            stage="REMAINING_BATCH_DOWNLOAD",
            task_scope="2",
        )
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        _validate_dispatch_with_stub(
            receipt,
            launch_path=launch_path,
            launch_value=launch_value,
            stage="REMAINING_BATCH_DOWNLOAD",
            governing_commit="a" * 40,
            orchestration_contract=contract,
            batch_plan=plan,
            execution_environment=environment,
            authorized_array_range="2",
        )
        for invalid in ("1", "2-19", "20"):
            expect_code(
                "DISPATCH_ARRAY_SCOPE_INVALID",
                lambda invalid=invalid: _validate_dispatch_with_stub(
                    receipt,
                    launch_path=launch_path,
                    launch_value=launch_value,
                    stage="REMAINING_BATCH_DOWNLOAD",
                    governing_commit="a" * 40,
                    orchestration_contract=contract,
                    batch_plan=plan,
                    execution_environment=environment,
                    authorized_array_range=invalid,
                ),
            )
        payload["unexpected"] = True
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        expect_code(
            "DISPATCH_RECEIPT_SCHEMA_MISMATCH",
            lambda: _validate_dispatch_with_stub(
                receipt,
                launch_path=launch_path,
                launch_value=launch_value,
                stage="FIRST_BATCH_DOWNLOAD",
                governing_commit="a" * 40,
                orchestration_contract=contract,
                batch_plan=plan,
                execution_environment=environment,
            ),
        )


def test_scheduler_scripts_resolve_helpers_from_authority_worktree_and_bind_projectnb() -> None:
    common = (ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh").read_text()
    runner = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    final = (ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh").read_text()
    for source in (runner, final):
        assert "AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'" in source
        assert 'source "$COMMON"' in source
        assert "dirname" not in source
        assert "BASH_SOURCE" not in source
    for name in (
        "TMPDIR",
        "XDG_CACHE_HOME",
        "TORCH_HOME",
        "MPLCONFIGDIR",
        "NUMBA_CACHE_DIR",
        "PIP_CACHE_DIR",
        "JOBLIB_TEMP_FOLDER",
    ):
        assert f"export {name}=" in common
    assert "/restricted/projectnb/" in common
    assert "CONCURRENT_BATCH_OWNERSHIP" in common
    assert "mkdir \"$LVEF_C3_ACTIVE_LOCK\"" in common
    for path in (
        ROOT / "scripts" / "scc_dispatch_lvef_c3_production_v2.sh",
        ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh",
        ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh",
    ):
        assert stat.S_IMODE(path.stat().st_mode) & 0o111


def test_future_dispatcher_is_stage_separated_no_ambient_export_and_print_only_capable() -> None:
    source = (ROOT / "scripts" / "scc_dispatch_lvef_c3_production_v2.sh").read_text()
    assert "--print-only|--submit" in source
    assert "C3_FUTURE_DISPATCHER=UNEXECUTED" in source
    assert "QSUB_EXECUTED=NO" in source
    assert "FIRST_BATCH_DOWNLOAD" in source
    assert "REMAINING_BATCH_DOWNLOAD" in source
    assert "DICOM_EXTRACTION" in source
    assert "ECHOPRIME_EMBEDDING" in source
    assert "BATCH_PRESERVATION" in source
    assert "CACHE_RETIREMENT" in source
    assert "PRESERVATION_FINALIZATION" in source
    assert "qsub -terse" in source
    assert "qsub -V" not in source
    assert "-cwd" not in source
    assert 'QSUB_COMMAND+=(-t "$TASK_SCOPE" -tc 1)' in source
    assert "ROLLING_BATCH_SCOPE_INVALID" in source
    assert '[[ "$TASK_SCOPE" =~ ^([2-9]|1[0-9])$ ]]' in source
    commands = (ROOT / "docs" / "lvef_multitask" / "scc_phase1ee_production_commands.md").read_text()
    assert "PRIOR_BATCH_RETIREMENT_JOB" not in commands
    assert '"$CACHE_RETIREMENT_DISPATCH_AUTH" - "$N"' in commands
    assert '"$REMAINING_DOWNLOAD_DISPATCH_AUTH" \\\n  - "$N"' in commands
    assert "2-19)" not in commands
    assert "validate_lvef_c3_dispatch_authorization.py" in source


def test_future_cache_retirement_is_separate_from_preservation_submission() -> None:
    commands = (
        ROOT / "docs" / "lvef_multitask" / "scc_phase1ee_production_commands.md"
    ).read_text()
    bash_blocks = [
        chunk.split("```", 1)[0]
        for chunk in commands.split("```bash\n")[1:]
    ]
    preservation_blocks = [
        block for block in bash_blocks
        if "dispatch_one --submit BATCH_PRESERVATION" in block
    ]
    retirement_blocks = [
        block for block in bash_blocks
        if '"$AUTH_BUILDER" cache-retirement' in block
    ]
    submission_blocks = [
        block for block in bash_blocks if "dispatch_one --submit" in block
    ]
    assert len(submission_blocks) == 7
    assert all(block.count("dispatch_one --submit") == 1 for block in submission_blocks)
    assert len(preservation_blocks) == 1
    assert len(retirement_blocks) == 1
    assert all('"$AUTH_BUILDER" cache-retirement' not in block for block in preservation_blocks)
    assert all("dispatch_one --submit BATCH_PRESERVATION" not in block for block in retirement_blocks)
    assert "preservation job completes, its evidence independently passes" in commands
    assert "owner grants a new cache-retirement authorization" in commands
    assert "ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19" in commands
    for stage in (
        "DICOM_EXTRACTION",
        "ECHOPRIME_EMBEDDING",
        "BATCH_PRESERVATION",
        "CACHE_RETIREMENT",
    ):
        matching = [
            block for block in submission_blocks
            if f"dispatch_one --submit {stage}" in block
        ]
        assert len(matching) == 1
        assert ' - "$N")' in matching[0]
    finalization = [
        block for block in submission_blocks
        if "dispatch_one --submit PRESERVATION_FINALIZATION" in block
    ]
    assert len(finalization) == 1
    assert " - none)" in finalization[0]


def test_owner_authorization_builder_separates_dispatch_and_operation_receipts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        dispatch_root = root / "dispatch"
        extraction_root = root / "extraction"
        dispatch_root.mkdir(mode=0o700)
        extraction_root.mkdir(mode=0o700)
        contract = root / "contract.json"
        plan = root / "plan.json"
        environment = root / "environment"
        launch_authority_path = root / "launch"
        contract.write_text("{}\n", encoding="utf-8")
        plan.write_text("{}\n", encoding="utf-8")
        environment.write_text("synthetic environment\n", encoding="utf-8")
        launch_authority_path.write_text("{}\n", encoding="utf-8")
        values = {
            "LVEF_C3_DISPATCH_AUTHORIZATION_ROOT": str(dispatch_root),
            "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT": str(extraction_root),
            "LVEF_C3_GOVERNING_COMMIT": "a" * 40,
            "LVEF_C3_ATTEMPT_ID": "lvef_c3_phase1ee_synthetic_001",
            "LVEF_C3_ORCHESTRATION_CONTRACT": str(contract),
            "LVEF_C3_BATCH_PLAN": str(plan),
        }
        launch_value = {
            "post_expansion_capacity_summary": {"sha256": "b" * 64},
            "production_authority_packet": {"sha256": "c" * 64},
        }
        runtime = (values, launch_value, "d" * 64)
        common = {
            "execution_environment": environment,
            "launch_authority": launch_authority_path,
            "owner_authorization_affirmed": "YES",
            "owner_authorization_date": "2026-08-10",
        }
        dispatch_output = dispatch_root / "DICOM_EXTRACTION.1.dispatch_authorization.json"
        dispatch_args = Namespace(
            **common,
            stage="DICOM_EXTRACTION",
            task_scope="1",
            output=dispatch_output,
        )
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime), mock.patch.object(
            owner_authorization.dispatch, "validate"
        ):
            dispatch_value = owner_authorization.build_dispatch(dispatch_args)

        operation_output = extraction_root / "c3_batch_000.authorization.json"
        operation_args = Namespace(
            **common,
            stage="DICOM_EXTRACTION",
            batch_id="c3_batch_000",
            output=operation_output,
        )
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime), mock.patch.object(
            owner_authorization.stages, "validate_stage_authorization_value"
        ):
            operation_value = owner_authorization.build_stage(operation_args)

        assert dispatch_output.parent == dispatch_root
        assert operation_output.parent == extraction_root
        assert dispatch_value["artifact_type"] == "lvef_c3_restricted_dispatch_authorization_v1"
        assert operation_value["artifact_type"] == "lvef_c3_restricted_stage_authorization_v1"
        assert dispatch_value["launch_authority_sha256"] == operation_value["launch_authority_sha256"]
        assert dispatch_value["stage"] == operation_value["stage"]

        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime):
            try:
                owner_authorization.build_dispatch(dispatch_args)
            except owner_authorization.OwnerAuthorizationError as exc:
                assert str(exc) == "LAUNCH_OUTPUT_COLLISION"
            else:
                raise AssertionError("owner authorization receipt was overwritten")

        wrong_output = extraction_root / dispatch_output.name
        wrong_args = Namespace(**{**vars(dispatch_args), "output": wrong_output})
        with mock.patch.object(owner_authorization, "_runtime", return_value=runtime):
            try:
                owner_authorization.build_dispatch(wrong_args)
            except owner_authorization.OwnerAuthorizationError as exc:
                assert str(exc) == "AUTHORIZATION_OUTPUT_PATH_MISMATCH"
            else:
                raise AssertionError("dispatch receipt was accepted in an operation root")


def test_dispatcher_stdout_contract_with_synthetic_qsub_stub() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worktree = root / "authority_worktree"
        scripts = worktree / "scripts"
        scripts.mkdir(parents=True)
        dispatcher = scripts / "scc_dispatch_lvef_c3_production_v2.sh"
        dispatcher.write_text(
            (ROOT / "scripts" / dispatcher.name).read_text().replace(
                "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask",
                str(worktree),
            ),
            encoding="utf-8",
        )
        dispatcher.chmod(0o700)
        production_root = root / "production"
        dispatch_root = production_root / "authorizations" / "dispatch"
        dispatch_root.mkdir(parents=True)
        common = scripts / "lvef_c3_production_scheduler_common.sh"
        common.write_text(
            "lvef_c3_load_runtime(){\n"
            "  LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n"
            f"  LVEF_C3_PRODUCTION_ROOT={production_root}\n"
            f"  LVEF_C3_PYTHON={scripts / 'fake_python'}\n"
            f"  LVEF_C3_GOVERNING_COMMIT={'a' * 40}\n"
            f"  LVEF_C3_ORCHESTRATION_CONTRACT={root / 'contract'}\n"
            f"  LVEF_C3_BATCH_PLAN={root / 'plan'}\n"
            f"  LVEF_C3_DISPATCH_AUTHORIZATION_ROOT={dispatch_root}\n"
            "}\n"
            "lvef_c3_require_projectnb_path(){ :; }\n"
            "lvef_c3_require_private_regular_file(){ :; }\n"
            "lvef_c3_require_private_projectnb_directory(){ :; }\n"
            "lvef_c3_validate_launch_authority(){ :; }\n"
            "lvef_c3_die(){ printf 'C3_DISPATCH_REFUSED=%s\\n' \"$1\" >&2; exit 78; }\n",
            encoding="utf-8",
        )
        fake_python = scripts / "fake_python"
        fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python.chmod(0o700)
        runner = scripts / "scc_run_lvef_c3_production_batch_v2.sh"
        runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        runner.chmod(0o700)
        env_file = root / "environment"
        launch_file = root / "launch_authority"
        auth_file = dispatch_root / "FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json"
        env_file.write_text("synthetic\n", encoding="utf-8")
        launch_file.write_text("synthetic\n", encoding="utf-8")
        auth_file.write_text("synthetic\n", encoding="utf-8")
        tool_bin = root / "bin"
        tool_bin.mkdir()
        qsub = tool_bin / "qsub"
        qsub.write_text("#!/usr/bin/env bash\nprintf '8123456\\n'\n", encoding="utf-8")
        qsub.chmod(0o700)
        environment = dict(os.environ)
        environment["PATH"] = f"{tool_bin}:{environment.get('PATH', '')}"
        result = subprocess.run(
            [
                str(dispatcher), "--submit", "FIRST_BATCH_DOWNLOAD",
                str(env_file), str(launch_file), str(auth_file), "-", "1",
            ],
            capture_output=True, text=True, check=False, env=environment,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert result.stdout == "8123456\n"
        assert "C3_DISPATCH_SUBMISSION=PASS" in result.stderr


def test_production_wrappers_are_authorization_gated_and_raw_deletion_absent() -> None:
    stages_source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    runner_source = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    final_source = (ROOT / "scripts" / "scc_finalize_lvef_c3_production_v2.sh").read_text()
    assert "validate_stage_authorization(" in stages_source
    assert "run-dicom-extraction" in stages_source
    assert "run-echoprime" in stages_source
    assert "view_classifier_used\": False" in stages_source
    assert "weights_only=True" in stages_source
    assert "raw_dicom" not in final_source.lower() or "RAW_DICOM_DELETION=NOT_PERFORMED" in final_source
    for source in (stages_source, runner_source, final_source):
        assert "rm -rf" not in source
        assert "gsutil" not in source


def test_scheduler_private_environment_is_literal_allowlisted_and_not_sourced() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh").read_text()
    assert "lvef_c3_parse_private_environment" in source
    assert "ENVIRONMENT_KEY_DUPLICATE" in source
    assert "ENVIRONMENT_ASSIGNMENT_NOT_LITERAL" in source
    assert 'source "$environment_file"' not in source
    assert "local -A" not in source
    assert "LVEF_C3_CLOUDSDK_CONFIG" in source
    assert "application_default_credentials.json" in source


def test_python_stage_output_gate_rejects_symlinked_projectnb_ancestor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        linked = root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        original = stages.PROJECTNB_PREFIX
        try:
            stages.PROJECTNB_PREFIX = root
            expect_code(
                "OUTPUT_ROOT_SYMLINK_ANCESTOR",
                lambda: stages.require_projectnb_path(linked / "batch"),
            )
        finally:
            stages.PROJECTNB_PREFIX = original


def test_scheduler_literal_environment_parser_rejects_duplicate_and_shell_syntax() -> None:
    common = ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "runtime.env"
        command = [
            "bash", "-c",
            'source "$1"; lvef_c3_parse_private_environment "$2"',
            "bash", str(common), str(env_file),
        ]
        env_file.write_text(
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n"
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001\n",
            encoding="utf-8",
        )
        duplicate = subprocess.run(command, capture_output=True, text=True, check=False)
        assert duplicate.returncode == 78
        assert "ENVIRONMENT_KEY_DUPLICATE" in duplicate.stderr
        env_file.write_text(
            "LVEF_C3_ATTEMPT_ID=$(touch_should_never_execute)\n",
            encoding="utf-8",
        )
        syntax = subprocess.run(command, capture_output=True, text=True, check=False)
        assert syntax.returncode == 78
        assert "ENVIRONMENT_ASSIGNMENT_NOT_LITERAL" in syntax.stderr


def test_single_active_cache_gate_rejects_symlink_and_nonregular_entries() -> None:
    common = ROOT / "scripts" / "lvef_c3_production_scheduler_common.sh"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache_root = root / "attempts" / "lvef_c3_synthetic_001" / "extracted_cache"
        cache_root.mkdir(parents=True)
        outside = root / "outside"
        outside.mkdir()
        (cache_root / "bad_link").symlink_to(outside)
        shell = (
            "set -euo pipefail; source \"$1\"; "
            "LVEF_C3_PRODUCTION_ROOT=\"$2\"; "
            "LVEF_C3_ATTEMPT_ID=lvef_c3_synthetic_001; "
            "lvef_c3_require_projectnb_path(){ :; }; "
            "lvef_c3_require_single_active_extraction_cache c3_batch_000"
        )
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(common), str(root)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 78
        assert "EXTRACTION_CACHE_ENTRY_INVALID" in result.stderr
        (cache_root / "bad_link").unlink()
        (cache_root / "regular_file").write_text("x", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(common), str(root)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 78
        assert "EXTRACTION_CACHE_ENTRY_INVALID" in result.stderr


def test_runner_binds_exact_downloader_authority_and_per_batch_stage_receipts() -> None:
    source = (ROOT / "scripts" / "scc_run_lvef_c3_production_batch_v2.sh").read_text()
    assert 'download-batch \\' in source
    assert '--governing-commit "$LVEF_C3_GOVERNING_COMMIT"' in source
    assert '--environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT"' in source
    assert '$LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert '$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json' in source
    assert "lvef_c3_require_single_active_extraction_cache" in source
    assert "/extracted_cache/$BATCH_ID" in source
    assert "validate_lvef_c3_prior_batch_finalization.py" in source
    assert "cache_retirement_finalized.restricted.json" in source


def test_remaining_download_requires_immediately_prior_finalized_batch() -> None:
    assert prior_batch_gate.prior_batch_id("c3_batch_001") == "c3_batch_000"
    assert prior_batch_gate.prior_batch_id("c3_batch_018") == "c3_batch_017"
    for invalid in ("c3_batch_000", "c3_batch_019", "batch_001"):
        expect_code(
            "CURRENT_BATCH_NOT_REMAINING_BATCH",
            lambda invalid=invalid: prior_batch_gate.prior_batch_id(invalid),
        )


def test_echoprime_loader_is_streamed_with_bounded_residency() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    assert "mini_records = ordered_records[start : start + batch_size]" in source
    assert "frames = [" not in source
    assert "del batch, tensors, result" in source
    assert "ProcessPoolExecutor(max_workers=workers)" in source
    assert "packages != list(live_packages)" in source
    assert 'receipt.get("package_count") != len(packages)' in source


def test_stage_failures_are_new_attempt_only_not_false_retryable() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text()
    function = source[source.index("def record_stage_failure(") : source.index("def _build_parser(")]
    assert '"to_state": "FAILED_NONRETRYABLE"' in function
    assert '"FAILED_RETRYABLE" if retryable' not in function
    assert "retryable=" not in source
    assert "requires a new attempt" in function


def test_preservation_stops_at_eligibility_and_retirement_is_explicit() -> None:
    preservation = (ROOT / "scripts" / "preserve_lvef_c3_production_batch.py").read_text()
    retirement = (ROOT / "scripts" / "retire_lvef_c3_extracted_cache_v2.py").read_text()
    assert '"CACHE_RETIREMENT_ELIGIBLE", receipt_body_sha' in preservation
    assert '("FINALIZED", receipt_body_sha)' not in preservation
    assert "--execute" in retirement
    assert "EXTRACTED_CACHE_RETIREMENT" in retirement
    assert "CACHE_ATOMICALLY_STAGED" in retirement
    assert "os.rename(cache_root, staging)" in retirement
    assert 'batch_id / "dicom_extraction" / "clips"' in retirement
    assert "extracted_npz_cache_owner_retirable" in preservation
    assert "dicom_extraction_metadata_retained" in preservation
    assert "embedding_and_pooling_retained" in preservation
    assert "RAW_TARGET_PROHIBITED" in retirement
    assert '"to_state": "FINALIZED"' in retirement
    policy = (ROOT / "configs" / "lvef_c3_preservation_policy_v2.yaml").read_text()
    assert "extracted_cache_retirement_implemented: true" in policy
    assert "extracted_cache_retirement_authorized_by_default: false" in policy
    assert "extracted_cache_retirement_scope: EXTRACTED_NPZ_CLIP_SUBTREE_ONLY" in policy
    assert "dicom_extraction_metadata_retained: true" in policy
    assert "extracted_cache_owner_authorization_required: true" in policy
    assert "extracted_cache_deletion_enabled" not in policy


def test_cache_retirement_uses_core_v2_owner_authorization_schema() -> None:
    assert retirement.AUTH_KEYS == {
        "schema_version", "artifact_type", "status", "authorization_scope",
        "owner_authorized", "owner_authorization_date_utc", "batch_id",
        "attempt_id", "authority_sha256", "preservation_receipt_sha256",
        "cache_inventory_sha256", "launch_authority_sha256",
    }
    source = (ROOT / "scripts" / "retire_lvef_c3_extracted_cache_v2.py").read_text()
    assert '"lvef_c3_cache_retirement_owner_authorization_v2"' in source
    assert "core.evaluate_cache_retirement(" in source
    assert "CACHE_RETIREMENT_POLICY_GATE_BLOCKED" in source


def test_cache_retirement_crash_states_are_closed_and_resumable() -> None:
    assert retirement.classify_retirement_state(
        cache_exists=True, staging_exists=False, intent_exists=False
    ) == "READY_TO_RECORD_INTENT"
    assert retirement.classify_retirement_state(
        cache_exists=True, staging_exists=False, intent_exists=True
    ) == "INTENT_RECORDED_READY_TO_RENAME"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=True, intent_exists=True
    ) == "FULL_STAGING_REQUIRES_HASH_AND_STAGED_RECEIPT"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=True, intent_exists=True,
        staged_receipt_exists=True,
    ) == "RENAMED_OR_PARTIALLY_CLEANED_RESUME"
    assert retirement.classify_retirement_state(
        cache_exists=False, staging_exists=False, intent_exists=True,
        staged_receipt_exists=True,
    ) == "PHYSICAL_RETIREMENT_COMPLETE_READY_TO_FINALIZE"
    expect_code(
        "CACHE_AND_RETIREMENT_STAGING_BOTH_EXIST",
        lambda: retirement.classify_retirement_state(
            cache_exists=True, staging_exists=True, intent_exists=True
        ),
    )
    expect_code(
        "ATOMIC_STAGING_EVIDENCE_MISSING",
        lambda: retirement.classify_retirement_state(
            cache_exists=False, staging_exists=False, intent_exists=True
        ),
    )
    expect_code(
        "CACHE_MISSING_BEFORE_RETIREMENT_INTENT",
        lambda: retirement.classify_retirement_state(
            cache_exists=False, staging_exists=False, intent_exists=False
        ),
    )


def test_cache_retirement_delete_is_exact_no_follow_and_resumable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "staging"
        nested = root / "nested"
        nested.mkdir(parents=True)
        (nested / "clip.npz").write_bytes(b"synthetic")
        retirement._delete_cache_tree(root)
        assert not root.exists()

        outside = Path(directory) / "outside"
        outside.mkdir()
        (outside / "keep").write_text("keep", encoding="utf-8")
        root.mkdir()
        (root / "bad").symlink_to(outside)
        expect_code("CACHE_TREE_SYMLINK", lambda: retirement._delete_cache_tree(root))
        assert (outside / "keep").read_text(encoding="utf-8") == "keep"
