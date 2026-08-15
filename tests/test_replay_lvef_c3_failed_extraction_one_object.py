from __future__ import annotations

"""No-body tests for the frozen one-object failed-extraction replay route."""

import ast
import base64
from contextlib import redirect_stdout
import csv
from dataclasses import replace
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock

import numpy as np

try:
    import pytest
except ModuleNotFoundError:
    class _Raises:
        def __init__(self, expected: type[BaseException]):
            self.expected = expected
            self.value: BaseException | None = None

        def __enter__(self) -> _Raises:
            return self

        def __exit__(self, kind: Any, value: Any, _traceback: Any) -> bool:
            if kind is None:
                raise AssertionError(f"expected {self.expected.__name__}")
            if not issubclass(kind, self.expected):
                return False
            self.value = value
            return True

    class _DependencyLightPytest:
        raises = staticmethod(lambda expected: _Raises(expected))

    pytest = _DependencyLightPytest()


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import replay_lvef_c3_failed_extraction_one_object as replay
import lvef_c3_full_sequential as sequential


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(path, 0o600)


def _write_private_csv(
    path: Path, header: tuple[str, ...], rows: list[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)


def _write_private_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(
        b"\n".join(replay.core.canonical_json_bytes(row) for row in rows) + b"\n"
    )
    os.chmod(path, 0o600)


def _legacy_failed_row(*, physical_key: str, source_sha256: str) -> dict[str, Any]:
    row = {field: "" for field in replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER}
    row.update(
        {
            "subject_id": "SENSITIVE_SUBJECT_VALUE",
            "study_id": "SENSITIVE_STUDY_VALUE",
            "smoke_role": "production_selected",
            "source_relative_path": f"{physical_key}.dcm",
            "source_sha256": source_sha256,
            "clip_key": "c" * 64,
            "output_relative_path": "clips/cc/legacy-sensitive-output.npz",
            "write_ok": False,
            "mask_status": "FAILED",
            "photometric_interpretation": "YBR_FULL_422",
            "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
            "decoder_backend": "pydicom_pixels_raw:pillow",
            "decoder_color_behavior": "STORED_COLOR_RAW",
            "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
            "canonical_color_space": "RGB",
            "source_sector_pixel_count": 100,
            "source_sector_nonempty_gate_passed": True,
            "source_nonzero_retained_pixel_count": 50,
            "source_nonzero_retained_pixel_gate_passed": True,
            "source_temporal_variation_pixel_count": 25,
            "source_temporal_variation_gate_passed": True,
            "sampled_nonzero_retained_pixel_count": 0,
            "sampled_nonzero_retained_pixel_gate_passed": False,
            "sampled_temporal_variation_pixel_count": 0,
            "sampled_temporal_variation_gate_passed": False,
            "temporal_sampling_policy": (
                "historical_compatible_linspace_or_tail_repeat_v1"
            ),
            "source_num_frames": 64,
            "error_code": "ValueError",
            "physical_source_key": physical_key,
            "pixel_decode_ok": False,
        }
    )
    return row


def _legacy_failure_summary() -> dict[str, Any]:
    return {
        "status": "FAIL_DICOM_OR_PIXEL_DECODE_GATE",
        "n_objects": 18_196,
        "n_studies": 250,
        "n_readable": 18_196,
        "n_unreadable": 0,
        "n_multiframe_candidates": 9_937,
        "n_single_frame": 8_259,
        "n_pixel_decode_failures": 1,
        "physical_source_keys_unique": True,
        "identifiers_emitted": False,
        "paths_emitted": False,
    }


def _make_private_tree(root: Path) -> None:
    for current, directories, filenames in os.walk(root):
        os.chmod(current, 0o700)
        for name in directories:
            os.chmod(Path(current) / name, 0o700)
        for name in filenames:
            os.chmod(Path(current) / name, 0o600)


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, int, str]]:
    observed: dict[str, tuple[int, int, int, str]] = {}
    for current, directories, filenames in os.walk(root):
        directories.sort()
        filenames.sort()
        for name in filenames:
            path = Path(current) / name
            item = path.stat(follow_symlinks=False)
            observed[path.relative_to(root).as_posix()] = (
                stat.S_IMODE(item.st_mode),
                item.st_size,
                item.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return observed


def _synthetic_original_attempt(
    root: Path,
) -> dict[str, Any]:
    production = root / "production"
    execution_commit = "a" * 40
    release = "mimic-iv-echo/1.0"
    selected: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    selected_ids: list[dict[str, str]] = []
    splits: list[dict[str, str]] = []
    payloads: dict[str, bytes] = {}
    for ordinal in range(4):
        subject = str(100_001 + ordinal)
        study = str(200_001 + ordinal)
        relative = "/".join(
            (
                "files",
                "p00",
                f"p{subject}",
                f"s{study}",
                f"synthetic_cine_{ordinal + 1:03d}.dcm",
            )
        )
        payload = f"synthetic-retained-dicom-{ordinal}".encode("ascii")
        key = hashlib.sha256(f"{release}\0{relative}".encode()).hexdigest()
        batch_id = f"c3_batch_{ordinal // 2:03d}"
        md5 = base64.b64encode(
            hashlib.md5(payload, usedforsecurity=False).digest()
        ).decode("ascii")
        crc32c = replay.core._crc32c_base64(payload)
        selected_ids.append({"subject_id": subject, "study_id": study})
        splits.append({"subject_id": subject, "split": "train"})
        selected.append(
            {
                "release_id": release,
                "source_bucket": "mimic-iv-echo",
                "component": "files",
                "subject_id": subject,
                "study_id": study,
                "split": "train",
                "source_relative_path": relative,
                "gcs_uri": f"gs://mimic-iv-echo/{relative}",
                "expected_size_bytes": str(len(payload)),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "source_object_key": key,
            }
        )
        metadata.append(
            {
                "release_id": release,
                "component": "files",
                "subject_id": subject,
                "study_id": study,
                "split": "train",
                "source_relative_path": relative,
                "gcs_uri": f"gs://mimic-iv-echo/{relative}",
                "source_object_key": key,
                "production_batch": batch_id,
                "remote_size_bytes": len(payload),
                "remote_md5_base64": md5,
                "remote_crc32c_base64": crc32c,
                "remote_generation": str(1_000 + ordinal),
                "remote_storage_class": "STANDARD",
                "remote_updated": "2026-08-01T00:00:00Z",
                "preflight_status": "PASS",
                "discrepancy_reasons": [],
            }
        )
        payloads[key] = payload

    authority_root = root / "authorities"
    selected_source = authority_root / "selected_source.restricted.csv"
    source_metadata = authority_root / "source_metadata.restricted.jsonl"
    _write_private_csv(selected_source, tuple(selected[0]), selected)
    _write_private_jsonl(source_metadata, metadata)
    selected_source_sha256 = hashlib.sha256(selected_source.read_bytes()).hexdigest()
    source_metadata_sha256 = hashlib.sha256(source_metadata.read_bytes()).hexdigest()
    normalized = replay.core.reconcile_selected_source_metadata(
        selected, metadata, release=release
    )
    requirements = replay.core.PlanRequirements(
        release=release,
        selected_studies=4,
        selected_subjects=4,
        normalized_source_objects=4,
        selected_source_bytes=sum(len(payload) for payload in payloads.values()),
        batch_count=2,
        studies_per_full_batch=2,
        final_batch_studies=2,
        contract_id=replay.core.TEST_ONLY_FULL_CONTRACT_ID,
    )
    plan_authority = {
        key: (
            execution_commit
            if key == "git_commit"
            else selected_source_sha256
            if key == "selected_source_manifest_sha256"
            else source_metadata_sha256
            if key == "source_metadata_sha256"
            else hashlib.sha256(f"synthetic-{key}".encode()).hexdigest()
        )
        for key in replay.core.PLAN_AUTHORITY_KEYS
    }
    plan = replay.core.build_immutable_batch_plan(
        selected_ids,
        normalized,
        splits,
        requirements=requirements,
        authority=plan_authority,
    )
    plan_sha256 = replay.core.canonical_json_sha256(plan)
    attempt_id = f"lvef_c3_full_{plan_sha256[:16]}_{execution_commit[:8]}"
    batch_id = "c3_batch_000"
    attempt = production / "attempts" / attempt_id
    partial = attempt / "extracted_cache" / batch_id / "dicom_extraction.partial"
    download = attempt / "raw" / batch_id
    planned_batch = plan["batches"][0]
    target = planned_batch["objects"][0]
    physical_key = str(target["source_object_key"])
    source_sha256 = hashlib.sha256(payloads[physical_key]).hexdigest()

    receipts: dict[str, str] = {}
    manifest_rows: list[dict[str, Any]] = []
    for planned in planned_batch["objects"]:
        key = str(planned["source_object_key"])
        source = download / "objects" / f"{key}.dcm"
        source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source.write_bytes(payloads[key])
        os.chmod(source, 0o600)
        item = os.stat(source, follow_symlinks=False)
        local_sha = hashlib.sha256(payloads[key]).hexdigest()
        receipt_path = download / "receipts" / f"{key}.verification.json"
        _write_private_json(
            receipt_path,
            {
                "schema_version": 2,
                "status": "PASS_DOWNLOAD_VERIFICATION",
                "source_object_key": key,
                "size_bytes": int(planned["size_bytes"]),
                "generation": str(planned["generation"]),
                "md5_base64": str(planned["md5_base64"]),
                "crc32c_base64": str(planned["crc32c_base64"]),
                "local_sha256": local_sha,
                "file_device": int(item.st_dev),
                "file_inode": int(item.st_ino),
                "file_mtime_ns": int(item.st_mtime_ns),
                "digest_backend": "google_crc32c_c_external_worker_v1",
                "digest_chunk_size_bytes": 8_388_608,
            },
        )
        receipts[key] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        manifest_rows.append(
            {
                "subject_id": str(planned["subject_id"]),
                "study_id": str(planned["study_id"]),
                "source_relative_path": str(planned["source_relative_path"]),
                "download_ok": "true",
                "observed_sha256": local_sha,
                "physical_source_key": key,
            }
        )
    download_manifest = download / "verified_download_manifest.restricted.csv"
    _write_private_csv(
        download_manifest,
        replay.VERIFIED_DOWNLOAD_MANIFEST_HEADER,
        manifest_rows,
    )
    manifest_sha256 = hashlib.sha256(download_manifest.read_bytes()).hexdigest()

    runtime_authority = replay.core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha256}
    )
    ledger = replay.core.initialize_resume_ledger(
        plan,
        requirements=requirements,
        attempt_id=attempt_id,
        authority=runtime_authority,
        batch_ids=[batch_id],
    )
    transition = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": attempt_id,
        "batch_id": batch_id,
        "from_state": "PLANNED",
        "to_state": "DOWNLOAD_IN_PROGRESS",
        "status": "PASS",
        "authority": runtime_authority,
        "input_receipt_sha256": [plan_sha256],
        "output_manifest_sha256": plan_sha256,
    }
    ledger = replay.core.apply_transition(ledger, transition)
    for key in receipts:
        ledger = replay.core.register_download_attempt(
            ledger,
            batch_id=batch_id,
            source_object_key=key,
            maximum_attempts=5,
        )
        ledger = replay.core.mark_download_verified(
            ledger,
            batch_id=batch_id,
            source_object_key=key,
            verification_receipt_sha256=receipts[key],
        )
    ledger = replay.core.mark_download_manifest(
        ledger, batch_id=batch_id, manifest_sha256=manifest_sha256
    )
    transition = {
        **transition,
        "from_state": "DOWNLOAD_IN_PROGRESS",
        "to_state": "DOWNLOAD_VERIFIED",
        "input_receipt_sha256": [
            ledger["batches"][batch_id]["events"][-1]["receipt_sha256"]
        ],
        "output_manifest_sha256": manifest_sha256,
    }
    ledger = replay.core.apply_transition(ledger, transition)
    _write_private_json(
        attempt
        / "batches"
        / batch_id
        / "download_resume_ledger.restricted.json",
        ledger,
    )

    _write_private_json(partial / "failure.summary.json", _legacy_failure_summary())
    failed_row = _legacy_failed_row(
        physical_key=physical_key, source_sha256=source_sha256
    )
    failed_row["subject_id"] = str(target["subject_id"])
    failed_row["study_id"] = str(target["study_id"])
    _write_private_csv(
        partial / "extraction_manifest.restricted.csv",
        replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER,
        [failed_row],
    )

    launch = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_selected_cohort_launch_authority_v1",
        "status": "AUTHORIZED_FULL_SELECTED_COHORT_RECONSTRUCTION",
        "governing_commit": execution_commit,
        "batch_plan_sha256": plan_sha256,
        "selected_manifest_sha256": plan["authority"]["selected_manifest_sha256"],
        "selected_source_manifest_sha256": selected_source_sha256,
        "split_map_sha256": plan["authority"]["split_map_sha256"],
        "checkpoint_sha256": plan["authority"]["checkpoint_sha256"],
        "selected_studies": 4,
        "selected_subjects": 4,
        "normalized_source_objects": 4,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_count": 2,
        "expected_no_cine_studies": 1,
        "maximum_scheduler_submissions": 2,
        "array_task_range": "1-2",
        "array_max_concurrency": 1,
        "raw_dicom_deletion_authorized": False,
        "extracted_cache_retirement_authorized_after_preservation": True,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }
    claim = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_full_submission_claim_v1",
        "status": "PREPARED_TWO_SUBMISSION_FULL_RECONSTRUCTION",
        "governing_commit": execution_commit,
        "attempt_id": attempt_id,
        "batch_plan_sha256": plan_sha256,
        "plan_authority_sha256": replay.core.canonical_json_sha256(plan["authority"]),
        "runtime_authority_sha256": replay.core.canonical_json_sha256(runtime_authority),
        "launch_authority_sha256": replay.core.canonical_json_sha256(launch),
        "capacity_receipt_sha256": "c" * 64,
        "qsub_environment_sha256": "d" * 64,
        "maximum_qsub_submissions": 2,
        "array_tasks": 2,
        "array_max_concurrency": 1,
        "automatic_resubmission": False,
        "whole_batch_retry_authorized": False,
        "third_scheduler_submission_reachable": False,
        "raw_dicom_deletion_authorized": False,
        "bucket_listing_requests_before_claim": 0,
        "cloud_requests_before_claim": 0,
        "qsub_submissions_before_claim": 0,
        "dicom_body_reads_before_claim": 0,
        "gpu_executions_before_claim": 0,
        "model_fitting_before_claim": 0,
        "prediction_generation_before_claim": 0,
        "confirmatory_performance_access_before_claim": 0,
    }
    _write_private_json(attempt / "full_batch_plan.restricted.json", plan)
    _write_private_json(attempt / "full_launch_authority.restricted.json", launch)
    _write_private_json(attempt / "full_submission_claim.restricted.json", claim)
    _make_private_tree(attempt)
    inventory = replay._attempt_metadata_inventory(attempt)
    authority = replace(
        replay.ORIGINAL_AUTHORITY,
        attempt_id=attempt_id,
        execution_commit=execution_commit,
        batch_id=batch_id,
        file_count=inventory["file_count"],
        total_bytes=inventory["total_bytes"],
        opaque_d4_metadata_tree_sha256="f" * 64,
        extraction_rows=1,
        successful_extraction_rows=0,
        download_rows=2,
        batch_plan_sha256_prefix=plan_sha256[:16],
    )
    return {
        "production": production,
        "attempt": attempt,
        "authority": authority,
        "inventory": inventory,
        "requirements": requirements,
        "row_authority": replay.ReplayRowAuthority(
            selected_source=selected_source,
            selected_source_sha256=selected_source_sha256,
            source_metadata=source_metadata,
        ),
        "selected_source_sha256": selected_source_sha256,
        "source_sha256": source_sha256,
        "physical_key": physical_key,
    }


def _repaired_row(
    output_root: str,
    *,
    physical_key: str = "b" * 64,
    source_sha256: str = "a" * 64,
) -> dict[str, Any]:
    source_relative = f"{physical_key}.dcm"
    clip_key = replay.reconstruction.stable_clip_key(source_relative)
    output_relative = f"clips/{clip_key[:2]}/{clip_key}.npz"
    output = Path(output_root) / output_relative
    output.parent.mkdir(mode=0o700, parents=True)
    anchors = np.zeros((16, 224, 224, 3), dtype=np.uint8)
    for index in range(16):
        anchors[index, :, :, :] = np.uint8(32 + index)
        anchors[index, 20 + index : 80 + index, 40:160, :] = np.uint8(180)
    frames = np.repeat(anchors, 2, axis=0)
    sampled_indices = np.repeat(
        np.linspace(0, 63, 16, dtype=np.int64), 2
    )
    source_num_frames = np.asarray([64], dtype=np.int32)
    replay.reconstruction.write_npz_atomic(
        output,
        frames=frames,
        sampled_indices=sampled_indices,
        source_num_frames=source_num_frames,
    )
    sampled_quality = replay.reconstruction._signal_quality_metrics(frames)
    encoder_quality = replay.reconstruction._signal_quality_metrics(frames[0:32:2])
    return {
        "subject_id": "SENSITIVE_SUBJECT_VALUE",
        "study_id": "SENSITIVE_STUDY_VALUE",
        "source_relative_path": source_relative,
        "source_sha256": source_sha256,
        "clip_key": clip_key,
        "output_relative_path": output_relative,
        "write_ok": True,
        "mask_status": "APPLIED",
        "decode_color_status": "PASS",
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
        "decoder_backend": "pydicom_pixels_raw:pillow",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
        "canonical_color_space": "RGB",
        "source_sector_pixel_count": 100,
        "source_sector_nonempty_gate_passed": True,
        "source_nonzero_retained_pixel_count": 50,
        "source_nonzero_retained_pixel_gate_passed": True,
        "source_temporal_variation_pixel_count": 25,
        "source_temporal_variation_gate_passed": True,
        "ordinary_post_crop_nonzero_retained_pixel_count": 50,
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed": True,
        "ordinary_post_crop_temporal_variation_pixel_count": 25,
        "ordinary_post_crop_temporal_variation_gate_passed": True,
        "ordinary_sampled_nonzero_retained_pixel_count": 0,
        "ordinary_sampled_nonzero_retained_pixel_gate_passed": False,
        "ordinary_sampled_temporal_variation_pixel_count": 0,
        "ordinary_sampled_temporal_variation_gate_passed": False,
        "post_crop_nonzero_retained_pixel_count": 50,
        "post_crop_nonzero_retained_pixel_gate_passed": True,
        "post_crop_temporal_variation_pixel_count": 25,
        "post_crop_temporal_variation_gate_passed": True,
        "sampled_nonzero_retained_pixel_count": sampled_quality[
            "nonzero_retained_pixel_count"
        ],
        "sampled_nonzero_retained_pixel_gate_passed": True,
        "sampled_temporal_variation_pixel_count": sampled_quality[
            "temporal_variation_pixel_count"
        ],
        "sampled_temporal_variation_gate_passed": True,
        "encoder_visible_nonzero_retained_pixel_count": encoder_quality[
            "nonzero_retained_pixel_count"
        ],
        "encoder_visible_nonzero_retained_pixel_gate_passed": True,
        "encoder_visible_temporal_variation_pixel_count": encoder_quality[
            "temporal_variation_pixel_count"
        ],
        "encoder_visible_temporal_variation_gate_passed": True,
        "selected_preprocessing_path": (
            replay.reconstruction.TEMPORAL_FALLBACK_PREPROCESSING_PATH
        ),
        "fallback_status": "FALLBACK_PATH_PASS",
        "failure_substage": "NONE",
        "temporal_sampling_policy": replay.reconstruction.TEMPORAL_FALLBACK_POLICY,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "frames_sha256": replay.reconstruction.array_content_sha256(frames),
        "sampled_indices_sha256": replay.reconstruction.array_content_sha256(
            sampled_indices
        ),
        "source_num_frames_sha256": replay.reconstruction.array_content_sha256(
            source_num_frames
        ),
        "npz_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_num_frames": 64,
        "pixel_decode_ok": True,
        "error_code": None,
    }


def test_frozen_authority_and_legacy_header_are_explicit() -> None:
    assert replay.ORIGINAL_AUTHORITY.attempt_id == (
        "lvef_c3_full_d574f21c760a5679_b805fd1a"
    )
    assert replay.ORIGINAL_AUTHORITY.execution_commit == (
        "b805fd1a403b3ff0503d09bb79d35b01805dd765"
    )
    assert replay.ORIGINAL_AUTHORITY.file_count == 158_288
    assert replay.ORIGINAL_AUTHORITY.total_bytes == 151_954_116_217
    assert replay.ORIGINAL_AUTHORITY.opaque_d4_metadata_tree_sha256 == (
        "800ff8fa66949a919d00bf3f66b6aec16c3c244ff48e68dc47d866c924684bb3"
    )
    assert len(replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER) == 36
    assert replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER[-2:] == (
        "physical_source_key",
        "pixel_decode_ok",
    )
    assert "selected_preprocessing_path" not in (
        replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER
    )


def test_runtime_stat_snapshot_binds_inode_and_ctime_separately_from_d4() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        attempt = fixture["attempt"]
        authority = fixture["authority"]
        baseline = fixture["inventory"]
        target = attempt / "raw" / authority.batch_id / (
            "verified_download_manifest.restricted.csv"
        )
        real_lstat = replay.os.lstat

        def changed_inode_and_ctime(path: os.PathLike[str] | str) -> Any:
            item = real_lstat(path)
            if Path(path) != target:
                return item
            return SimpleNamespace(
                st_mode=item.st_mode,
                st_uid=item.st_uid,
                st_gid=item.st_gid,
                st_dev=item.st_dev,
                st_ino=item.st_ino + 1,
                st_size=item.st_size,
                st_mtime_ns=item.st_mtime_ns,
                st_ctime_ns=item.st_ctime_ns + 1,
            )

        with mock.patch.object(replay.os, "lstat", side_effect=changed_inode_and_ctime):
            changed = replay._attempt_metadata_inventory(attempt)
        assert changed["file_count"] == baseline["file_count"]
        assert changed["total_bytes"] == baseline["total_bytes"]
        assert changed["runtime_metadata_stat_snapshot_sha256"] != baseline[
            "runtime_metadata_stat_snapshot_sha256"
        ]
        assert authority.opaque_d4_metadata_tree_sha256 not in {
            baseline["runtime_metadata_stat_snapshot_sha256"],
            changed["runtime_metadata_stat_snapshot_sha256"],
        }


def test_replay_uses_one_retained_body_and_preserves_original_attempt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        production = fixture["production"]
        attempt = fixture["attempt"]
        authority = fixture["authority"]
        before_inventory = fixture["inventory"]
        before_tree = _tree_snapshot(attempt)
        calls: list[tuple[Mapping[str, Any], str, str]] = []

        def fake_extractor(
            record: Mapping[str, Any], download_root: str, output_root: str
        ) -> Mapping[str, Any]:
            calls.append((dict(record), download_root, output_root))
            assert record[replay.reconstruction.REPLAY_ORDINARY_TOPOLOGY_RECORD_KEY] == (
                replay.reconstruction.REPLAY_REQUIRED_ORDINARY_FAILURE_TOPOLOGY
            )
            assert Path(download_root).is_dir()
            return _repaired_row(
                output_root,
                physical_key=fixture["physical_key"],
                source_sha256=fixture["source_sha256"],
            )

        def fake_git(
            repository: Path, governing_commit: str, *, original_commit: str
        ) -> None:
            assert repository == ROOT
            assert governing_commit == "e" * 40
            assert original_commit == authority.execution_commit

        diagnostic = root / "lvef_c3_r3c_one_object_replay_synthetic"
        with mock.patch.object(
            replay.core,
            "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
            fixture["selected_source_sha256"],
        ):
            summary = replay.run_replay(
                governing_commit="e" * 40,
                diagnostic_root=diagnostic,
                production_root=production,
                allowed_diagnostic_prefix=root,
                repository=ROOT,
                authority=authority,
                requirements=fixture["requirements"],
                row_authority=fixture["row_authority"],
                extractor=fake_extractor,
                git_validator=fake_git,
            )

        assert len(calls) == 1
        assert _tree_snapshot(attempt) == before_tree
        assert replay._attempt_metadata_inventory(attempt) == before_inventory
        assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o700
        assert summary["source_dicom_objects_read"] == 1
        assert summary["dicom_body_read_calls"] == 2
        assert summary["cloud_requests"] == 0
        assert summary["qsub_submissions"] == 0
        assert summary["gpu_executions"] == 0
        assert summary["echoprime_executions"] == 0
        assert summary["model_fitting"] == 0
        assert summary["prediction_generation"] == 0
        assert summary["confirmatory_performance_accessed"] is False
        assert summary["opaque_d4_hash_recomputed"] is False
        assert "original_attempt_id" not in summary
        assert summary["opaque_d4_metadata_tree_authority_sha256"] == "f" * 64
        assert summary["runtime_metadata_stat_snapshot_before_sha256"] == (
            summary["runtime_metadata_stat_snapshot_after_sha256"]
        )
        assert summary["runtime_metadata_stat_snapshot_before_sha256"] != (
            summary["opaque_d4_metadata_tree_authority_sha256"]
        )

        comparison = json.loads(
            (diagnostic / "technical_comparison.restricted.json").read_text(
                encoding="utf-8"
            )
        )
        assert comparison["legacy_b805"]["pixel_decode_ok"] == "False"
        assert comparison["repaired"]["decode_color_status"] == "PASS"
        serialized = json.dumps(comparison, sort_keys=True)
        for secret in (
            "SENSITIVE_SUBJECT_VALUE",
            "SENSITIVE_STUDY_VALUE",
            "SENSITIVE_SOURCE_LOCATOR",
            "SENSITIVE_OUTPUT_LOCATOR",
            "legacy-sensitive-output",
        ):
            assert secret not in serialized
        for projection in (comparison["legacy_b805"], comparison["repaired"]):
            assert "subject_id" not in projection
            assert "study_id" not in projection
            assert "source_relative_path" not in projection
            assert "output_relative_path" not in projection


def test_existing_diagnostic_root_fails_before_dicom_body_read() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        production = fixture["production"]
        authority = fixture["authority"]
        diagnostic = root / "lvef_c3_r3c_one_object_replay_collision"
        diagnostic.mkdir(mode=0o700)
        extractor = mock.Mock()
        with (
            mock.patch.object(
                replay.core,
                "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
                fixture["selected_source_sha256"],
            ),
            mock.patch.object(replay, "_sha256_file") as body_reader,
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay.run_replay(
                governing_commit="e" * 40,
                diagnostic_root=diagnostic,
                production_root=production,
                allowed_diagnostic_prefix=root,
                repository=ROOT,
                authority=authority,
                requirements=fixture["requirements"],
                row_authority=fixture["row_authority"],
                extractor=extractor,
                git_validator=lambda *_args, **_kwargs: None,
            )
        assert caught.value.code == "REPLAY_DIAGNOSTIC_ROOT_INVALID"
        assert caught.value.dicom_body_reads == 0
        body_reader.assert_not_called()
        extractor.assert_not_called()


def test_repaired_npz_hash_is_bound_to_the_fresh_private_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        production = fixture["production"]
        authority = fixture["authority"]

        def mismatched_extractor(
            _record: Mapping[str, Any], _download_root: str, output_root: str
        ) -> Mapping[str, Any]:
            value = _repaired_row(
                output_root,
                physical_key=fixture["physical_key"],
                source_sha256=fixture["source_sha256"],
            )
            value["npz_sha256"] = "0" * 64
            return value

        with (
            mock.patch.object(
                replay.core,
                "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
                fixture["selected_source_sha256"],
            ),
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay.run_replay(
                governing_commit="e" * 40,
                diagnostic_root=(
                    root / "lvef_c3_r3c_one_object_replay_npz_mismatch"
                ),
                production_root=production,
                allowed_diagnostic_prefix=root,
                repository=ROOT,
                authority=authority,
                requirements=fixture["requirements"],
                row_authority=fixture["row_authority"],
                extractor=mismatched_extractor,
                git_validator=lambda *_args, **_kwargs: None,
            )
        assert caught.value.code == "REPLAY_DIAGNOSTIC_NPZ_VALIDATION_FAILED"
        assert caught.value.dicom_body_reads == 2


def test_repaired_decoder_and_color_provenance_must_equal_legacy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repaired = _repaired_row(str(Path(directory).resolve()))
        legacy = _legacy_failed_row(
            physical_key="b" * 64, source_sha256="a" * 64
        )
        replay._validate_repaired_row(repaired, legacy_failed_row=legacy)
        for field in replay.DECODER_COLOR_AUTHORITY_FIELDS:
            mutated = dict(repaired)
            mutated[field] = f"MUTATED_{field}"
            with pytest.raises(replay.ReplayError) as caught:
                replay._validate_repaired_row(
                    mutated, legacy_failed_row=legacy
                )
            assert caught.value.code == (
                "ONE_OBJECT_REPLAY_REPAIRED_DECODER_COLOR_AUTHORITY_MISMATCH"
            )
            assert caught.value.dicom_body_reads == 1


def test_json_publication_race_never_clobbers_the_competing_destination() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        target = root / "aggregate_safe.json"
        competing = b"competing publication\n"

        def publish_competing(*_args: Any, **kwargs: Any) -> None:
            assert kwargs["follow_symlinks"] is False
            target.write_bytes(competing)
            raise FileExistsError("synthetic publication race")

        with (
            mock.patch.object(replay.os, "link", side_effect=publish_competing),
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay._write_json_no_clobber(target, {"status": "PASS"})
        assert caught.value.code == "ONE_OBJECT_REPLAY_OUTPUT_COLLISION"
        assert target.read_bytes() == competing
        assert not tuple(root.glob(".aggregate_safe.json.partial.*"))


def test_producer_v2_failure_summary_is_accepted_by_successor_classifier() -> None:
    current = "lvef_c3_full_1111111111111111_aaaaaaaa"
    prior = "lvef_c3_full_2222222222222222_bbbbbbbb"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        row = _repaired_row(str(root / "producer_scratch"))
        row.update(
            {
                "write_ok": False,
                "mask_status": "FAILED",
                "selected_preprocessing_path": (
                    replay.reconstruction.PREPROCESSING_PATH_NOT_SELECTED
                ),
                "fallback_status": "FALLBACK_PATH_FAILED",
                "failure_substage": "SAMPLED_NONZERO_SIGNAL_FAILURE",
                "sampled_nonzero_retained_pixel_count": 0,
                "sampled_nonzero_retained_pixel_gate_passed": False,
                "sampled_temporal_variation_pixel_count": 0,
                "sampled_temporal_variation_gate_passed": False,
                "encoder_visible_nonzero_retained_pixel_count": 0,
                "encoder_visible_nonzero_retained_pixel_gate_passed": False,
                "encoder_visible_temporal_variation_pixel_count": 0,
                "encoder_visible_temporal_variation_gate_passed": False,
                "error_code": "ValueError",
            }
        )
        provenance = replay.reconstruction.summarize_extraction(
            replay.reconstruction.pd.DataFrame([row])
        )
        partial = (
            root
            / "production"
            / "attempts"
            / prior
            / "extracted_cache"
            / "c3_batch_001"
            / "dicom_extraction.partial"
        )
        _write_private_json(
            partial / "failure.summary.json",
            {
                "schema_version": 2,
                "artifact_type": "lvef_c3_batch_extraction_failure_summary_v2",
                "status": "FAIL_EXTRACTION_GATE",
                "error_code": "EXTRACTION_SAMPLED_NONZERO_SIGNAL_FAILURE",
                "extraction_provenance": provenance,
                "identifiers_emitted": False,
                "paths_emitted": False,
            },
        )
        assert sequential._extraction_cache_inventory(
            root / "production", current_attempt_id=current
        ) == sequential.ExtractionCacheInventory(
            active=0, preserved_terminal_failed=1
        )


def test_multiple_legacy_failures_are_rejected_before_replay() -> None:
    source_sha256 = "a" * 64
    row = _legacy_failed_row(physical_key="b" * 64, source_sha256=source_sha256)
    authority = replace(
        replay.ORIGINAL_AUTHORITY,
        extraction_rows=2,
        successful_extraction_rows=0,
    )
    with pytest.raises(replay.ReplayError) as caught:
        replay._validate_legacy_failed_row([row, dict(row)], authority)
    assert caught.value.code == "ONE_OBJECT_REPLAY_LEGACY_FAILED_ROW_NOT_UNIQUE"
    assert caught.value.dicom_body_reads == 0


def test_execute_flag_is_required_and_default_extractor_is_production() -> None:
    extractor_default = inspect.signature(replay.run_replay).parameters[
        "extractor"
    ].default
    assert extractor_default is replay.reconstruction._extract_one
    output_buffer = io.StringIO()
    with (
        mock.patch.object(replay, "run_replay") as runner,
        redirect_stdout(output_buffer),
    ):
        status = replay.main(
            [
                "--governing-commit",
                "e" * 40,
                "--diagnostic-root",
                "/restricted/projectnb/lvef_c3_r3a_one_object_replay_blocked",
            ]
        )
    assert status == 78
    runner.assert_not_called()
    output = output_buffer.getvalue()
    assert "LVEF_C3_ONE_OBJECT_REPLAY=BLOCKED_EXECUTE_FLAG_REQUIRED" in output
    assert "DICOM_BODY_READS=0" in output
    assert "CLOUD_REQUESTS=0" in output
    assert "QSUB_SUBMISSIONS=0" in output


def test_replay_imports_no_cloud_scheduler_gpu_or_model_interface() -> None:
    tree = ast.parse(Path(replay.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"google", "boto3", "torch", "tensorflow", "echoprime", "sklearn"}
    )
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "run_production_echoprime" not in called_attributes
    assert "qsub" not in called_attributes


def _run_fixture_preflight(
    fixture: Mapping[str, Any], diagnostic: Path
) -> replay.ReplayPreflight:
    with mock.patch.object(
        replay.core,
        "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
        fixture["selected_source_sha256"],
    ):
        return replay.run_preflight(
            governing_commit="e" * 40,
            diagnostic_root=diagnostic,
            production_root=fixture["production"],
            allowed_diagnostic_prefix=diagnostic.parent,
            repository=ROOT,
            authority=fixture["authority"],
            requirements=fixture["requirements"],
            row_authority=fixture["row_authority"],
            git_validator=lambda *_args, **_kwargs: None,
        )


def _refresh_fixture_inventory(fixture: dict[str, Any]) -> None:
    inventory = replay._attempt_metadata_inventory(fixture["attempt"])
    fixture["inventory"] = inventory
    fixture["authority"] = replace(
        fixture["authority"],
        file_count=inventory["file_count"],
        total_bytes=inventory["total_bytes"],
    )


def test_preflight_canonical_authority_is_zero_body_zero_root_and_no_model() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        diagnostic = root / "lvef_c3_r3c_one_object_replay_preflight"
        with (
            mock.patch.object(replay, "_sha256_file") as body_reader,
            mock.patch.object(
                replay.minimal,
                "discover_live_authority",
                side_effect=AssertionError("checkpoint authority is prohibited"),
            ),
            mock.patch.object(
                replay.production_stages,
                "validate_checkpoint_and_environment",
                side_effect=AssertionError("checkpoint access is prohibited"),
            ),
        ):
            result = _run_fixture_preflight(fixture, diagnostic)
        assert result.source_path.is_file()
        assert not os.path.lexists(diagnostic)
        body_reader.assert_not_called()

        output = io.StringIO()
        with (
            mock.patch.object(replay, "run_preflight", return_value=result),
            redirect_stdout(output),
        ):
            status = replay.main(
                [
                    "--governing-commit",
                    "e" * 40,
                    "--diagnostic-root",
                    str(diagnostic),
                    "--preflight-only",
                ]
            )
        assert status == 0
        text = output.getvalue()
        assert "R3C_REPLAY_PREFLIGHT=PASS_ZERO_BODY_NO_ROOT" in text
        assert "UNIQUE_DICOM_OBJECTS_ACCESSED=0" in text
        assert "DICOM_BODY_READS=0" in text
        assert "PYDICOM_DECODE_INVOCATIONS=0" in text
        assert "DIAGNOSTIC_ROOT_CREATED=NO" in text
        assert not os.path.lexists(diagnostic)


def test_receipt_tuple_and_local_sha_mutations_fail_role_specifically() -> None:
    cases = {
        "size_bytes": (1, "REPLAY_OBJECT_SIZE_MISMATCH"),
        "generation": ("999999", "REPLAY_OBJECT_GENERATION_MISMATCH"),
        "md5_base64": (base64.b64encode(b"x" * 16).decode(), "REPLAY_OBJECT_MD5_MISMATCH"),
        "crc32c_base64": (base64.b64encode(b"x" * 4).decode(), "REPLAY_OBJECT_CRC32C_MISMATCH"),
        "local_sha256": ("0" * 64, "REPLAY_LOCAL_SHA256_MISMATCH"),
    }
    for ordinal, (field, (mutated, expected_code)) in enumerate(cases.items()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixture = _synthetic_original_attempt(root)
            authority = fixture["authority"]
            receipt_path = (
                fixture["attempt"]
                / "raw"
                / authority.batch_id
                / "receipts"
                / f"{fixture['physical_key']}.verification.json"
            )
            receipt = replay._read_json(receipt_path)
            receipt[field] = mutated
            _write_private_json(receipt_path, receipt)
            receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            ledger_path = (
                fixture["attempt"]
                / "batches"
                / authority.batch_id
                / "download_resume_ledger.restricted.json"
            )
            ledger = replay._read_json(
                ledger_path, maximum_bytes=replay.MAXIMUM_MANIFEST_BYTES
            )
            ledger["batches"][authority.batch_id][
                "download_verification_receipts"
            ][fixture["physical_key"]] = receipt_sha
            _write_private_json(ledger_path, ledger)
            _refresh_fixture_inventory(fixture)
            diagnostic = root / (
                f"lvef_c3_r3c_one_object_replay_receipt_{ordinal:02d}"
            )
            with pytest.raises(replay.ReplayError) as caught:
                _run_fixture_preflight(fixture, diagnostic)
            assert caught.value.code == expected_code
            assert caught.value.dicom_body_reads == 0
            assert not os.path.lexists(diagnostic)


def test_plan_and_missing_receipt_fail_before_body_access() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        plan_path = fixture["attempt"] / "full_batch_plan.restricted.json"
        plan = replay._read_json(
            plan_path, maximum_bytes=replay.MAXIMUM_MANIFEST_BYTES
        )
        plan["cohort"]["selected_source_bytes"] += 1
        _write_private_json(plan_path, plan)
        _refresh_fixture_inventory(fixture)
        with pytest.raises(replay.ReplayError) as caught:
            _run_fixture_preflight(
                fixture, root / "lvef_c3_r3c_one_object_replay_plan_mismatch"
            )
        assert caught.value.code == "REPLAY_BATCH_MEMBERSHIP_INVALID"
        assert caught.value.dicom_body_reads == 0

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        receipt = (
            fixture["attempt"]
            / "raw"
            / fixture["authority"].batch_id
            / "receipts"
            / f"{fixture['physical_key']}.verification.json"
        )
        receipt.unlink()
        _refresh_fixture_inventory(fixture)
        with pytest.raises(replay.ReplayError) as caught:
            _run_fixture_preflight(
                fixture, root / "lvef_c3_r3c_one_object_replay_receipt_missing"
            )
        assert caught.value.code == "REPLAY_DOWNLOAD_RECEIPT_INVALID"
        assert caught.value.dicom_body_reads == 0


def test_duplicate_and_contradictory_verification_receipts_fail_closed() -> None:
    for mutation in ("duplicate_json_key", "contradictory_object_key"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixture = _synthetic_original_attempt(root)
            authority = fixture["authority"]
            receipt_path = (
                fixture["attempt"]
                / "raw"
                / authority.batch_id
                / "receipts"
                / f"{fixture['physical_key']}.verification.json"
            )
            if mutation == "duplicate_json_key":
                payload = receipt_path.read_text(encoding="utf-8").rstrip()
                receipt_path.write_text(
                    payload[:-1] + ', "schema_version": 2}\n',
                    encoding="utf-8",
                )
                os.chmod(receipt_path, 0o600)
            else:
                receipt = replay._read_json(receipt_path)
                ledger_path = (
                    fixture["attempt"]
                    / "batches"
                    / authority.batch_id
                    / "download_resume_ledger.restricted.json"
                )
                ledger = replay._read_json(
                    ledger_path, maximum_bytes=replay.MAXIMUM_MANIFEST_BYTES
                )
                receipts = ledger["batches"][authority.batch_id][
                    "download_verification_receipts"
                ]
                other_key = next(key for key in receipts if key != fixture["physical_key"])
                receipt["source_object_key"] = other_key
                _write_private_json(receipt_path, receipt)
                receipts[fixture["physical_key"]] = hashlib.sha256(
                    receipt_path.read_bytes()
                ).hexdigest()
                _write_private_json(ledger_path, ledger)
            _refresh_fixture_inventory(fixture)
            with pytest.raises(replay.ReplayError) as caught:
                _run_fixture_preflight(
                    fixture,
                    root / f"lvef_c3_r3c_one_object_replay_receipt_{mutation}",
                )
            assert caught.value.code == "REPLAY_DOWNLOAD_RECEIPT_INVALID"
            assert caught.value.dicom_body_reads == 0


def test_exact_selected_source_batch_assignment_mismatch_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        plan = replay._read_json(
            fixture["attempt"] / "full_batch_plan.restricted.json",
            maximum_bytes=replay.MAXIMUM_MANIFEST_BYTES,
        )
        planned_object = plan["batches"][0]["objects"][0]
        mismatched = replace(fixture["authority"], batch_id="c3_batch_001")
        with (
            mock.patch.object(
                replay.core,
                "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
                fixture["selected_source_sha256"],
            ),
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay._load_selected_source_object(
                fixture["row_authority"],
                plan=plan,
                planned_object=planned_object,
                authority=mismatched,
            )
        assert caught.value.code == "REPLAY_BATCH_MEMBERSHIP_INVALID"


def test_exact_both_fail_topology_and_unique_object_counter() -> None:
    base = {
        "decode_color_status": "PASS",
        "mask_status": "APPLIED",
        "source_sector_nonempty_gate_passed": True,
        "source_nonzero_retained_pixel_gate_passed": True,
        "source_temporal_variation_gate_passed": True,
        "ordinary_sampled_nonzero_retained_pixel_gate_passed": False,
        "ordinary_sampled_temporal_variation_gate_passed": False,
        "failure_substage": "NONE",
    }
    replay._validate_ordinary_reproduction(base)
    for nonzero, temporal in ((True, False), (False, True), (True, True)):
        changed = {
            **base,
            "ordinary_sampled_nonzero_retained_pixel_gate_passed": nonzero,
            "ordinary_sampled_temporal_variation_gate_passed": temporal,
        }
        with pytest.raises(replay.ReplayError) as caught:
            replay._validate_ordinary_reproduction(changed)
        assert caught.value.code == "REPLAY_ORDINARY_FAILURE_TOPOLOGY_MISMATCH"
    earlier = {**base, "decode_color_status": "FAILED"}
    with pytest.raises(replay.ReplayError):
        replay._validate_ordinary_reproduction(earlier)

    allowed = Path("/tmp/r3c-one-object").resolve()
    counters = replay.ReplayAccessCounters(allowed_source=allowed)
    counters.register_hash(allowed)
    counters.register_decode(allowed)
    assert counters.unique_dicom_objects_accessed == 1
    assert counters.dicom_body_read_calls == 2
    with pytest.raises(replay.ReplayError) as caught:
        counters.register_decode(Path("/tmp/r3c-second-object").resolve())
    assert caught.value.code == "REPLAY_UNIQUE_OBJECT_SCOPE_EXCEEDED"
    assert counters.unique_dicom_objects_accessed == 1

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory).resolve() / "fixed-source.dcm"
        source.write_bytes(b"synthetic-body")
        os.chmod(source, 0o600)
        identity = replay._source_file_identity(
            source, expected_size=source.stat().st_size
        )
        changed = replace(identity, inode=identity.inode + 1)
        with pytest.raises(replay.ReplayError) as caught:
            replay._sha256_file(source, expected_identity=changed)
        assert caught.value.code == "REPLAY_LOCAL_FILE_AUTHORITY_INVALID"


def test_private_parent_effective_modes_and_symlink_are_closed() -> None:
    assert replay.core.owner_private_directory_mode_ok(stat.S_IFDIR | 0o2700)
    for mode in (0o700,):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, mode)
            candidate = parent / "lvef_c3_r3c_one_object_replay_private_ok"
            identity = replay._validate_diagnostic_candidate(
                candidate,
                allowed_prefix=parent,
                original_attempt_root=parent / "original-attempt",
            )
            assert identity.mode == mode
            assert not os.path.lexists(candidate)
    for mode in (0o750, 0o770, 0o777, 0o1700, 0o3700):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, mode)
            candidate = parent / "lvef_c3_r3c_one_object_replay_private_bad"
            with pytest.raises(replay.ReplayError) as caught:
                replay._validate_diagnostic_candidate(
                    candidate,
                    allowed_prefix=parent,
                    original_attempt_root=parent / "original-attempt",
                )
            assert caught.value.code == "REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        private = root / "private"
        private.mkdir(mode=0o700)
        linked = root / "linked"
        linked.symlink_to(private, target_is_directory=True)
        with pytest.raises(replay.ReplayError) as caught:
            replay._validate_diagnostic_candidate(
                linked / "lvef_c3_r3c_one_object_replay_symlinked",
                allowed_prefix=linked,
                original_attempt_root=root / "original-attempt",
            )
        assert caught.value.code == "REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID"


def test_effective_2700_wrong_owner_and_parent_identity_change() -> None:
    with tempfile.TemporaryDirectory() as directory:
        parent = Path(directory).resolve()
        candidate = parent / "lvef_c3_r3c_one_object_replay_setgid_ok"
        original_lstat = replay.os.lstat
        parent_stat = original_lstat(parent)

        os.chmod(parent, 0o2700)
        if stat.S_IMODE(parent.stat().st_mode) == 0o2700:
            identity = replay._validate_diagnostic_candidate(
                candidate,
                allowed_prefix=parent,
                original_attempt_root=parent / "original-attempt",
            )
            created, created_identity = replay._create_diagnostic_root(
                SimpleNamespace(
                    diagnostic_root=candidate,
                    diagnostic_parent=identity,
                )
            )
            assert created == candidate
            assert created_identity.mode in {0o700, 0o2700}
            assert replay.core.owner_private_directory_mode_ok(
                created.stat(follow_symlinks=False).st_mode
            )
            continue_with_mock = False
        else:
            # Darwin clears setgid on this temporary directory.  Exercise the
            # exact candidate gate with a faithful stat projection; Linux/SCC
            # takes the real branch above.
            os.chmod(parent, 0o700)
            continue_with_mock = True

        def setgid_parent(path: os.PathLike[str] | str) -> Any:
            observed = original_lstat(path)
            if Path(path) != parent:
                return observed
            return SimpleNamespace(
                st_mode=observed.st_mode | stat.S_ISGID,
                st_uid=observed.st_uid,
                st_gid=observed.st_gid,
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
            )

        if continue_with_mock:
            with mock.patch.object(replay.os, "lstat", side_effect=setgid_parent):
                identity = replay._validate_diagnostic_candidate(
                    candidate,
                    allowed_prefix=parent,
                    original_attempt_root=parent / "original-attempt",
                )
            assert identity.mode == 0o2700
            assert identity.inode == parent_stat.st_ino

        def wrong_owner(path: os.PathLike[str] | str) -> Any:
            observed = original_lstat(path)
            if Path(path) != parent:
                return observed
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_uid=observed.st_uid + 1,
                st_gid=observed.st_gid,
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
            )

        with (
            mock.patch.object(replay.os, "lstat", side_effect=wrong_owner),
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay._validate_diagnostic_candidate(
                parent / "lvef_c3_r3c_one_object_replay_wrong_owner",
                allowed_prefix=parent,
                original_attempt_root=parent / "original-attempt",
            )
        assert caught.value.code == "REPLAY_DIAGNOSTIC_PARENT_AUTHORITY_INVALID"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        fixture = _synthetic_original_attempt(root)
        candidate = root / "lvef_c3_r3c_one_object_replay_parent_change"
        preflight = _run_fixture_preflight(fixture, candidate)
        changed = replace(
            preflight,
            diagnostic_parent=replace(
                preflight.diagnostic_parent,
                inode=preflight.diagnostic_parent.inode + 1,
            ),
        )
        with pytest.raises(replay.ReplayError) as caught:
            replay._create_diagnostic_root(changed)
        assert caught.value.code == "REPLAY_DIAGNOSTIC_PARENT_IDENTITY_CHANGED"
        assert caught.value.diagnostic_root_created is False
        assert not os.path.lexists(candidate)

    with tempfile.TemporaryDirectory() as directory:
        parent = Path(directory).resolve()
        candidate = parent / "lvef_c3_r3c_one_object_replay_postcheck"
        identity = replay._validate_diagnostic_candidate(
            candidate,
            allowed_prefix=parent,
            original_attempt_root=parent / "original-attempt",
        )
        with (
            mock.patch.object(
                replay,
                "_directory_stat_matches_identity",
                side_effect=(True, False),
            ),
            pytest.raises(replay.ReplayError) as caught,
        ):
            replay._create_diagnostic_root(
                SimpleNamespace(
                    diagnostic_root=candidate,
                    diagnostic_parent=identity,
                )
            )
        assert caught.value.diagnostic_root_created is True
        assert os.path.lexists(candidate)
