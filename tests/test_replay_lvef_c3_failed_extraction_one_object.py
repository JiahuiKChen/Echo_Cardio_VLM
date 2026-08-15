from __future__ import annotations

"""No-body tests for the frozen one-object failed-extraction replay route."""

import ast
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
) -> tuple[Path, Path, replay.OriginalAttemptAuthority, dict[str, Any]]:
    production = root / "production"
    attempt_id = "lvef_c3_full_1111111111111111_aaaaaaaa"
    batch_id = "c3_batch_001"
    attempt = production / "attempts" / attempt_id
    partial = (
        attempt
        / "extracted_cache"
        / batch_id
        / "dicom_extraction.partial"
    )
    download = attempt / "raw" / batch_id
    physical_key = "b" * 64
    source_payload = b"synthetic retained DICOM body -- never decoded by tests"
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    source = download / "objects" / f"{physical_key}.dcm"
    source.parent.mkdir(mode=0o700, parents=True)
    source.write_bytes(source_payload)
    os.chmod(source, 0o600)
    _write_private_json(partial / "failure.summary.json", _legacy_failure_summary())
    _write_private_csv(
        partial / "extraction_manifest.restricted.csv",
        replay.LEGACY_B805_EXTRACTION_MANIFEST_HEADER,
        [_legacy_failed_row(physical_key=physical_key, source_sha256=source_sha256)],
    )
    _write_private_csv(
        download / "verified_download_manifest.restricted.csv",
        replay.VERIFIED_DOWNLOAD_MANIFEST_HEADER,
        [
            {
                "subject_id": "SENSITIVE_SUBJECT_VALUE",
                "study_id": "SENSITIVE_STUDY_VALUE",
                "source_relative_path": (
                    "files/p00/pSENSITIVE/sSENSITIVE/original-cine.dcm"
                ),
                "download_ok": True,
                "observed_sha256": source_sha256,
                "physical_source_key": physical_key,
            }
        ],
    )
    _make_private_tree(attempt)
    inventory = replay._attempt_metadata_inventory(attempt)
    authority = replace(
        replay.ORIGINAL_AUTHORITY,
        attempt_id=attempt_id,
        execution_commit="a" * 40,
        batch_id=batch_id,
        file_count=inventory["file_count"],
        total_bytes=inventory["total_bytes"],
        opaque_d4_metadata_tree_sha256="f" * 64,
        extraction_rows=1,
        successful_extraction_rows=0,
        download_rows=1,
    )
    return production, attempt, authority, inventory


def _repaired_row(output_root: str) -> dict[str, Any]:
    source_relative = f"{'b' * 64}.dcm"
    clip_key = replay.reconstruction.stable_clip_key(source_relative)
    output_relative = f"clips/{clip_key[:2]}/{clip_key}.npz"
    output = Path(output_root) / output_relative
    output.parent.mkdir(mode=0o700, parents=True)
    payload = b"synthetic repaired NPZ"
    output.write_bytes(payload)
    os.chmod(output, 0o600)
    digest = "e" * 64
    return {
        "subject_id": "SENSITIVE_SUBJECT_VALUE",
        "study_id": "SENSITIVE_STUDY_VALUE",
        "source_relative_path": "SENSITIVE_SOURCE_LOCATOR",
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
        "sampled_nonzero_retained_pixel_count": 50,
        "sampled_nonzero_retained_pixel_gate_passed": True,
        "sampled_temporal_variation_pixel_count": 25,
        "sampled_temporal_variation_gate_passed": True,
        "encoder_visible_nonzero_retained_pixel_count": 50,
        "encoder_visible_nonzero_retained_pixel_gate_passed": True,
        "encoder_visible_temporal_variation_pixel_count": 25,
        "encoder_visible_temporal_variation_gate_passed": True,
        "selected_preprocessing_path": (
            replay.reconstruction.TEMPORAL_FALLBACK_PREPROCESSING_PATH
        ),
        "fallback_status": "FALLBACK_PATH_PASS",
        "failure_substage": "NONE",
        "temporal_sampling_policy": replay.reconstruction.TEMPORAL_FALLBACK_POLICY,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "frames_sha256": digest,
        "sampled_indices_sha256": digest,
        "source_num_frames_sha256": digest,
        "npz_sha256": hashlib.sha256(payload).hexdigest(),
        "source_num_frames": 64,
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
        _, attempt, authority, baseline = _synthetic_original_attempt(root)
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
        production, attempt, authority, before_inventory = (
            _synthetic_original_attempt(root)
        )
        before_tree = _tree_snapshot(attempt)
        calls: list[tuple[Mapping[str, Any], str, str]] = []

        def fake_extractor(
            record: Mapping[str, Any], download_root: str, output_root: str
        ) -> Mapping[str, Any]:
            calls.append((dict(record), download_root, output_root))
            assert record["subject_id"] == "SENSITIVE_SUBJECT_VALUE"
            assert record["study_id"] == "SENSITIVE_STUDY_VALUE"
            assert Path(download_root).is_dir()
            return _repaired_row(output_root)

        def fake_git(
            repository: Path, governing_commit: str, *, original_commit: str
        ) -> None:
            assert repository == ROOT
            assert governing_commit == "e" * 40
            assert original_commit == authority.execution_commit

        diagnostic = root / "lvef_c3_r3a_one_object_replay_synthetic"
        summary = replay.run_replay(
            governing_commit="e" * 40,
            diagnostic_root=diagnostic,
            production_root=production,
            allowed_diagnostic_prefix=root,
            repository=ROOT,
            authority=authority,
            extractor=fake_extractor,
            git_validator=fake_git,
        )

        assert len(calls) == 1
        assert _tree_snapshot(attempt) == before_tree
        assert replay._attempt_metadata_inventory(attempt) == before_inventory
        assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o700
        assert summary["source_dicom_objects_read"] == 1
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
        production, _, authority, _ = _synthetic_original_attempt(root)
        diagnostic = root / "lvef_c3_r3a_one_object_replay_collision"
        diagnostic.mkdir(mode=0o700)
        extractor = mock.Mock()
        with (
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
                extractor=extractor,
                git_validator=lambda *_args, **_kwargs: None,
            )
        assert caught.value.code == "ONE_OBJECT_REPLAY_DIAGNOSTIC_ROOT_INVALID"
        assert caught.value.dicom_body_reads == 0
        body_reader.assert_not_called()
        extractor.assert_not_called()


def test_repaired_npz_hash_is_bound_to_the_fresh_private_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        production, _, authority, _ = _synthetic_original_attempt(root)

        def mismatched_extractor(
            _record: Mapping[str, Any], _download_root: str, output_root: str
        ) -> Mapping[str, Any]:
            value = _repaired_row(output_root)
            value["npz_sha256"] = "0" * 64
            return value

        with pytest.raises(replay.ReplayError) as caught:
            replay.run_replay(
                governing_commit="e" * 40,
                diagnostic_root=(
                    root / "lvef_c3_r3a_one_object_replay_npz_mismatch"
                ),
                production_root=production,
                allowed_diagnostic_prefix=root,
                repository=ROOT,
                authority=authority,
                extractor=mismatched_extractor,
                git_validator=lambda *_args, **_kwargs: None,
            )
        assert caught.value.code == "ONE_OBJECT_REPLAY_REPAIRED_NPZ_HASH_MISMATCH"
        assert caught.value.dicom_body_reads == 1


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

        def publish_competing(
            _source: os.PathLike[str] | str,
            destination: os.PathLike[str] | str,
            *,
            follow_symlinks: bool,
        ) -> None:
            assert follow_symlinks is False
            Path(destination).write_bytes(competing)
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
