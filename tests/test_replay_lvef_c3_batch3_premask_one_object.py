from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

import numpy as np

try:
    import pytest
except ModuleNotFoundError:
    class _ManualSkip(RuntimeError):
        pass

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

    class _Mark:
        @staticmethod
        def parametrize(names: object, values: object) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            normalized_names = (
                tuple(part.strip() for part in names.split(","))
                if isinstance(names, str)
                else tuple(str(part) for part in names)  # type: ignore[arg-type]
            )
            normalized_values = tuple(values)  # type: ignore[arg-type]

            def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
                cases = list(getattr(function, "__manual_parametrize__", ()))
                cases.append((normalized_names, normalized_values))
                setattr(function, "__manual_parametrize__", tuple(cases))
                return function

            return decorate

    class _DependencyLightPytest:
        mark = _Mark()
        raises = staticmethod(lambda expected: _Raises(expected))

        @staticmethod
        def importorskip(name: str) -> object:
            try:
                return importlib.import_module(name)
            except ModuleNotFoundError as exc:
                raise _ManualSkip(name) from exc

    pytest = _DependencyLightPytest()  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import replay_lvef_c3_batch3_premask_one_object as replay


def _identity(path: Path) -> replay.r3e.SourceFileIdentity:
    item = os.lstat(path)
    return replay.r3e.SourceFileIdentity(
        path=path,
        device=int(item.st_dev),
        inode=int(item.st_ino),
        size=int(item.st_size),
        mtime_ns=int(item.st_mtime_ns),
        ctime_ns=int(item.st_ctime_ns),
    )


def _root_identity(path: Path, *, mode: int = 0o2700) -> replay.r3e.LegacyRootIdentity:
    item = os.lstat(path)
    return replay.r3e.LegacyRootIdentity(
        path=path,
        device=int(item.st_dev),
        inode=int(item.st_ino),
        group=int(item.st_gid),
        mode=mode,
        nlink=int(item.st_nlink),
        size=int(item.st_size),
        mtime_ns=int(item.st_mtime_ns),
        ctime_ns=int(item.st_ctime_ns),
    )


def _frozen_inventory(path: Path) -> replay.R4Inventory:
    root = _root_identity(path)
    return replay.R4Inventory(
        file_count=replay.FROZEN_FILE_COUNT,
        directory_count=replay.FROZEN_DIRECTORY_COUNT,
        total_bytes=replay.FROZEN_TOTAL_BYTES,
        symlink_count=0,
        nonregular_count=0,
        owner_mismatch_count=0,
        cross_device_count=0,
        identity_instability_count=0,
        group_other_write_count=0,
        special_bit_anomaly_count=0,
        regular_nlink_anomaly_count=0,
        duplicate_inode_count=0,
        sensitive_exception_count=0,
        kind_mode_histogram=replay.FROZEN_KIND_MODE_HISTOGRAM,
        exceptional_role_histogram=replay.FROZEN_EXCEPTION_HISTOGRAM,
        metadata_stat_sha256=replay.FROZEN_METADATA_STAT_SHA256,
        root_identity=replace(root, mode=0o2700),
    )


def _independent_r4t_digest(root: Path) -> str:
    digest = hashlib.sha256()

    def observe(path: Path, relative: str) -> None:
        item = os.lstat(path)
        if stat.S_ISDIR(item.st_mode):
            kind = "directory"
        elif stat.S_ISREG(item.st_mode):
            kind = "file"
        elif stat.S_ISLNK(item.st_mode):
            kind = "symlink"
        else:
            kind = "nonregular"
        record = (
            relative,
            kind,
            stat.S_IMODE(item.st_mode),
            item.st_uid,
            item.st_gid,
            item.st_dev,
            item.st_ino,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        digest.update(
            json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode()
            + b"\n"
        )

    observe(root, ".")
    for current_text, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        current = Path(current_text)
        for name in directories + files:
            child = current / name
            observe(child, child.relative_to(root).as_posix())
    return digest.hexdigest()


def _failed_row() -> tuple[dict[str, str], dict[str, str]]:
    physical = "b" * 64
    relative = f"{physical}.dcm"
    clip = replay.reconstruction.stable_clip_key(relative)
    row = {field: "" for field in replay.preservation.EXTRACTION_MANIFEST_HEADER}
    row.update(
        {
            "subject_id": "private-subject",
            "study_id": "private-study",
            "smoke_role": "production_selected",
            "source_relative_path": relative,
            "source_sha256": "a" * 64,
            "clip_key": clip,
            "output_relative_path": f"clips/{clip[:2]}/{clip}.npz",
            "write_ok": "False",
            "mask_status": "FAILED",
            "photometric_interpretation": "YBR_FULL_422",
            "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
            "decoder_backend": "pydicom_pixels_raw:pillow",
            "decoder_color_behavior": "STORED_COLOR_RAW",
            "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
            "canonical_color_space": "RGB",
            "source_sector_pixel_count": "0",
            "source_sector_nonempty_gate_passed": "False",
            "source_nonzero_retained_pixel_count": "0",
            "source_nonzero_retained_pixel_gate_passed": "False",
            "source_temporal_variation_pixel_count": "0",
            "source_temporal_variation_gate_passed": "False",
            "selected_preprocessing_path": replay.reconstruction.PREPROCESSING_PATH_NOT_SELECTED,
            "fallback_status": replay.reconstruction.FALLBACK_NOT_ATTEMPTED,
            "failure_substage": "SOURCE_SIGNAL_QUALITY_FAILURE",
            "decode_color_status": "PASS",
            "temporal_sampling_policy": replay.reconstruction.TEMPORAL_SAMPLING_POLICY,
            "source_num_frames": str(replay.EXPECTED_SOURCE_FRAMES),
            "error_code": "ValueError",
            "physical_source_key": physical,
            "pixel_decode_ok": "True",
        }
    )
    for _, gate in replay.DOWNSTREAM_GATE_FIELDS:
        row[gate] = "False"
    audit = {field: "" for field in replay.preservation.DICOM_AUDIT_HEADER}
    audit.update(
        {
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "smoke_role": "production_selected",
            "source_relative_path": relative,
            "download_sha256": row["source_sha256"],
            "read_ok": "True",
            "is_multiframe": "True",
            "number_of_frames": str(replay.EXPECTED_SOURCE_FRAMES),
            "samples_per_pixel": "3",
            "bits_allocated": "8",
            "bits_stored": "8",
            "photometric_interpretation": "YBR_FULL_422",
            "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
            "pixel_decode_ok": "True",
        }
    )
    return row, audit


def _collapse_value(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "pre_mask_nonzero_pixel_count": 10,
        "pre_mask_adjacent_temporal_variation_pixel_count": 4,
        "first_last_difference_pixel_count": 3,
        "persistent_occupancy_pixel_count": 5,
        "overlap_before_floodfill_pixel_count": 2,
        "generated_sector_pixel_count": 0,
        "post_mask_nonzero_pixel_count": 0,
        "post_mask_adjacent_temporal_variation_pixel_count": 0,
        "source_sector_nonempty_gate_passed": False,
        "source_nonzero_retained_pixel_gate_passed": False,
        "source_temporal_variation_gate_passed": False,
        "mask_mirror_equals_frozen_helper": True,
        "production_mask_exception_class": None,
        "contradiction_kind": "NONE",
    }
    value.update(changes)
    return value


def _decode_metadata() -> dict[str, str]:
    return {
        "photometric_interpretation": "YBR_FULL_422",
        "transfer_syntax_uid": "1.2.840.10008.1.2.4.50",
        "decoder_backend": "pydicom_pixels_raw:pillow",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "EXPLICIT_YBR_FULL_422_TO_RGB",
        "canonical_color_space": "RGB",
    }


def _computed_dynamic() -> dict[str, object]:
    return {
        **_collapse_value(),
        "premask_replay_class": "DYNAMIC_DECODE_MASK_COLLAPSE",
        "mask_collapse_mechanism": "FLOODFILL_OR_CONTOUR_TOPOLOGY_EMPTY",
        "selected_next_action": "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION",
    }


def _valid_observation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r4d2_premask_replay_observation_v1",
        "status": "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED",
        "execution_commit": "f" * 40,
        "source_frame_count": replay.EXPECTED_SOURCE_FRAMES,
        "decode_color_status": "PASS",
        **_decode_metadata(),
        **_computed_dynamic(),
        "identifiers_emitted": False,
        "locators_emitted": False,
        "paths_emitted": False,
    }


def _valid_aggregate() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r4d2_premask_replay_aggregate_safe_v1",
        "status": "PASS_ONE_OBJECT_PREMASK_DIAGNOSTIC_CLASSIFIED",
        "starting_commit": replay.STARTING_COMMIT,
        "execution_commit": "f" * 40,
        "r4_attempt_inventory_before_sha256": replay.FROZEN_METADATA_STAT_SHA256,
        "r4_attempt_inventory_after_sha256": replay.FROZEN_METADATA_STAT_SHA256,
        "r4_attempt_preserved_immutable": True,
        "batch_1_terminal_receipt_unchanged": True,
        "batch_2_terminal_receipt_unchanged": True,
        "batch_3_authorities_unchanged": True,
        "replay_input_authority": "PASS",
        "historical_file_device_authority": "PASS_EXACT_CURRENT_DEVICE",
        "current_mount_authority": "PASS",
        "unique_dicom_objects_accessed": 1,
        "local_dicom_read_passes": 1,
        "pydicom_decode_invocations": 1,
        "source_frame_count": replay.EXPECTED_SOURCE_FRAMES,
        "decode_color_status": "PASS",
        **_decode_metadata(),
        **_computed_dynamic(),
        "affected_study_valid_cines": replay.EXPECTED_AFFECTED_STUDY_ORDINARY_CINES,
        "affected_study_imaging_coverage": "RETAINED",
        "source_object_substitution_required": False,
        "whole_batch_zero_failure_policy": "UNCHANGED_FAIL_CLOSED",
        "observation_receipt_basename": "premask_replay_observation.restricted.json",
        "observation_receipt_bytes": 123,
        "observation_receipt_sha256": "a" * 64,
        "diagnostic_npz_created": False,
        "cloud_requests": 0,
        "object_downloads": 0,
        "qsub_submissions": 0,
        "gpu_executions": 0,
        "echoprime_executions": 0,
        "embedding_generations": 0,
        "model_fitting": 0,
        "prediction_generation": 0,
        "confirmatory_performance_accessed": False,
        "identifiers_emitted": False,
        "locators_emitted": False,
        "paths_emitted": False,
    }


def test_r4t_inventory_uses_exact_tuple_json_preimage_and_sorted_walk(tmp_path: Path) -> None:
    root = (tmp_path / "attempt").resolve()
    root.mkdir()
    (root / "z-dir").mkdir()
    (root / "a-dir").mkdir()
    (root / "z-file").write_bytes(b"z")
    (root / "a-file").write_bytes(b"alpha")
    os.chmod(root, 0o2700)
    os.chmod(root / "z-dir", 0o2700)
    os.chmod(root / "a-dir", 0o2700)
    os.chmod(root / "z-file", 0o600)
    os.chmod(root / "a-file", 0o600)

    observed = replay.r4_metadata_inventory(root)

    assert observed.file_count == 2
    assert observed.directory_count == 3  # Includes the root's "." record.
    assert observed.total_bytes == 6
    assert observed.metadata_stat_sha256 == _independent_r4t_digest(root)
    directory_mode = stat.S_IMODE(os.lstat(root).st_mode)
    assert directory_mode in {0o700, 0o2700}
    assert observed.kind_mode_histogram == (
        ("directory", directory_mode, 3),
        ("file", 0o600, 2),
    )
    assert observed.identity_instability_count == 0


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"file_count": replay.FROZEN_FILE_COUNT + 1}, "R4D2_FROZEN_ATTEMPT_INVENTORY_MISMATCH"),
        ({"metadata_stat_sha256": "0" * 64}, "R4D2_FROZEN_ATTEMPT_INVENTORY_MISMATCH"),
        ({"symlink_count": 1}, "R4D2_FROZEN_ATTEMPT_TOPOLOGY_INVALID"),
        ({"identity_instability_count": 1}, "R4D2_FROZEN_ATTEMPT_TOPOLOGY_INVALID"),
        ({"regular_nlink_anomaly_count": 1}, "R4D2_FROZEN_ATTEMPT_TOPOLOGY_INVALID"),
    ],
)
def test_frozen_inventory_accepts_only_exact_authority(
    tmp_path: Path, mutation: dict[str, object], code: str
) -> None:
    root = tmp_path.resolve()
    os.chmod(root, 0o2700)
    frozen = _frozen_inventory(root)
    replay.validate_r4_inventory(frozen)
    with pytest.raises(replay.PremaskReplayError) as captured:
        replay.validate_r4_inventory(replace(frozen, **mutation))
    assert captured.value.code == code


def test_failed_csv_row_is_unique_privately_joined_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, audit = _failed_row()
    successful = {
        "write_ok": "True",
        "study_id": failed["study_id"],
        "selected_preprocessing_path": replay.reconstruction.ORDINARY_PREPROCESSING_PATH,
    }
    monkeypatch.setattr(replay, "EXPECTED_EXTRACTION_ROWS", 2)
    monkeypatch.setattr(replay, "EXPECTED_SUCCESSFUL_EXTRACTIONS", 1)
    monkeypatch.setattr(replay, "EXPECTED_AFFECTED_STUDY_ORDINARY_CINES", 1)

    assert replay._validate_failed_row(
        [successful, failed], [audit], partial=tmp_path
    ) == failed

    with pytest.raises(replay.PremaskReplayError) as duplicate:
        replay._validate_failed_row([failed, dict(failed)], [audit], partial=tmp_path)
    assert duplicate.value.code == "R4D2_FAILED_ROW_NOT_UNIQUE"


@pytest.mark.parametrize(
    ("row_field", "value", "code"),
    [
        ("decoder_backend", "pydicom_pixels_raw:other", "R4D2_FAILED_ROW_AUTHORITY_INVALID"),
        ("source_sector_pixel_count", "1", "R4D2_FROZEN_SOURCE_METRICS_INVALID"),
        ("ordinary_sampled_nonzero_retained_pixel_count", "0", "R4D2_DOWNSTREAM_STAGE_AUTHORITY_INVALID"),
        ("npz_sha256", "a" * 64, "R4D2_DIAGNOSTIC_NPZ_AUTHORITY_INVALID"),
    ],
)
def test_failed_csv_row_mutations_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_field: str,
    value: str,
    code: str,
) -> None:
    failed, audit = _failed_row()
    failed[row_field] = value
    monkeypatch.setattr(replay, "EXPECTED_EXTRACTION_ROWS", 1)
    monkeypatch.setattr(replay, "EXPECTED_SUCCESSFUL_EXTRACTIONS", 0)
    monkeypatch.setattr(replay, "EXPECTED_AFFECTED_STUDY_ORDINARY_CINES", 0)
    with pytest.raises(replay.PremaskReplayError) as captured:
        replay._validate_failed_row([failed], [audit], partial=tmp_path)
    assert captured.value.code == code


def test_current_device_receipt_rejects_historical_device_namespace() -> None:
    expectation = replay.core.DownloadExpectation(
        source_object_key="a" * 64,
        source_relative_path="private",
        size_bytes=12,
        generation="7",
        md5_base64="AAAAAAAAAAAAAAAAAAAAAA==",
        crc32c_base64="AAAAAA==",
    )
    source = replay.r3e.SourceFileIdentity(
        path=Path("/private/object"),
        device=41,
        inode=8,
        size=12,
        mtime_ns=9,
        ctime_ns=10,
    )
    root = replay.r3e.LegacyRootIdentity(
        path=Path("/private"), device=41, inode=2, group=3, mode=0o2700,
        nlink=1, size=0, mtime_ns=1, ctime_ns=1,
    )
    mount = replay.r3e.CurrentMountAuthority(
        status="PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT",
        identity_sha256="b" * 64,
    )
    receipt = {
        "schema_version": 2,
        "status": "PASS_DOWNLOAD_VERIFICATION",
        "source_object_key": expectation.source_object_key,
        "size_bytes": expectation.size_bytes,
        "generation": expectation.generation,
        "md5_base64": expectation.md5_base64,
        "crc32c_base64": expectation.crc32c_base64,
        "local_sha256": "c" * 64,
        "file_device": 41,
        "file_inode": 8,
        "file_mtime_ns": 9,
        "digest_backend": "google_crc32c_c_external_worker_v1",
        "digest_chunk_size_bytes": 8_388_608,
    }
    assert replay._strict_current_device_receipt(
        receipt,
        expectation=expectation,
        source_identity=source,
        root_identity=root,
        mount_authority=mount,
    ) == "c" * 64
    receipt["file_device"] = 40
    with pytest.raises(replay.PremaskReplayError) as captured:
        replay._strict_current_device_receipt(
            receipt,
            expectation=expectation,
            source_identity=source,
            root_identity=root,
            mount_authority=mount,
        )
    assert captured.value.code == "R4D2_HISTORICAL_FILE_DEVICE_NAMESPACE_UNRESOLVED"
    assert replay.SAFE_CODE_RE.fullmatch(captured.value.code)


def test_preflight_is_structurally_zero_body_zero_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_text = inspect.getsource(replay.run_preflight)
    tree = ast.parse(source_text)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called.isdisjoint(
        {"_read_source_once", "_decode_once", "_create_root", "run_execute"}
    )
    assert "dcmread" not in source_text

    production = (tmp_path / "production").resolve()
    attempt = production / "attempts" / replay.ATTEMPT_ID
    attempt.mkdir(parents=True)
    parent = production / "owner_private"
    parent.mkdir()
    os.chmod(attempt, 0o2700)
    os.chmod(parent, 0o2700)
    inventory = _frozen_inventory(attempt)
    source = attempt / "object.dcm"
    source.write_bytes(b"not-read")
    os.chmod(source, 0o600)
    source_identity = _identity(source)
    mount = replay.r3e.CurrentMountAuthority(
        "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT", "d" * 64
    )
    failed, _ = _failed_row()
    parent_identity = replay.r3e._private_directory_identity(parent)

    monkeypatch.setattr(replay, "_validate_batch_terminal_receipt", lambda *_: (1, "a" * 64))
    monkeypatch.setattr(replay, "_load_batch3_authority", lambda *_: (failed, {}))
    monkeypatch.setattr(
        replay,
        "_load_plan_and_source",
        lambda **_: ({"size_bytes": source_identity.size}, source, source_identity, mount, "e" * 64),
    )
    monkeypatch.setattr(replay, "_validate_diagnostic_candidate", lambda *_args, **_kwargs: parent_identity)
    monkeypatch.setattr(replay.r3e, "_source_file_identity", lambda *_args, **_kwargs: source_identity)
    body_reader = mock.Mock(side_effect=AssertionError("body read in preflight"))
    decoder = mock.Mock(side_effect=AssertionError("decode in preflight"))
    creator = mock.Mock(side_effect=AssertionError("root creation in preflight"))
    monkeypatch.setattr(replay, "_read_source_once", body_reader)
    monkeypatch.setattr(replay, "_decode_once", decoder)
    monkeypatch.setattr(replay, "_create_root", creator)

    result = replay.run_preflight(
        production_root=production,
        allowed_diagnostic_prefix=parent,
        repository=ROOT,
        git_validator=lambda _repository: "f" * 40,
        quiescence_validator=lambda: None,
        inventory_loader=lambda _root: inventory,
        mount_validator=lambda *_: mount,
    )
    assert result.source_path == source
    assert not result.diagnostic_root.exists()
    body_reader.assert_not_called()
    decoder.assert_not_called()
    creator.assert_not_called()


def test_mirror_is_bit_exact_with_frozen_helper_when_cv2_is_available() -> None:
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(481)
    frames = rng.integers(0, 256, size=(4, 64, 72, 3), dtype=np.uint8)
    mirrored, mirror_mask, intermediates = replay._mirror_strict_mask(frames, cv2)
    governed, governed_mask = replay.reconstruction._mask_ultrasound_strict(frames, cv2)
    assert np.array_equal(mirror_mask, governed_mask)
    assert np.array_equal(mirrored, governed)
    assert set(intermediates) == {
        "first_last_difference_pixel_count",
        "persistent_occupancy_pixel_count",
        "overlap_before_floodfill_pixel_count",
        "generated_sector_pixel_count",
    }
    assert all(type(value) is int and value >= 0 for value in intermediates.values())

    luma = replay.reconstruction._rgb_luma_uint8(frames)
    occupancy = np.where(luma[0] > 0, 1, 0)
    for frame in luma:
        occupancy = np.add(occupancy, np.where(frame > 0, 1, 0))
    kernel = np.ones((3, 3), np.uint8)
    eroded = cv2.erode(
        np.where(occupancy > 0, 1, 0).astype(np.uint8),
        kernel,
        iterations=10,
    )
    eroded = np.where(eroded > 0, 1, 0)
    first_last = np.where(
        np.abs(luma[0].astype(np.int16) - luma[-1].astype(np.int16)) > 0,
        1,
        0,
    )
    first_last[0:20, 0:20] = 0
    overlap = np.where(np.add(eroded, first_last) > 1, 1, 0)
    dilated_overlap = cv2.dilate(
        np.uint8(overlap), kernel, iterations=10
    ).astype(np.uint8)
    assert intermediates["persistent_occupancy_pixel_count"] == int(
        np.count_nonzero(eroded)
    )
    assert intermediates["first_last_difference_pixel_count"] == int(
        np.count_nonzero(first_last)
    )
    assert intermediates["overlap_before_floodfill_pixel_count"] == int(
        np.count_nonzero(dilated_overlap)
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"pre_mask_nonzero_pixel_count": 0, "pre_mask_adjacent_temporal_variation_pixel_count": 0},
         ("PREMASK_BLANK", "NOT_APPLICABLE", "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION")),
        ({"pre_mask_nonzero_pixel_count": 12, "pre_mask_adjacent_temporal_variation_pixel_count": 0},
         ("PREMASK_NONBLANK_STATIC", "NOT_APPLICABLE", "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION")),
        ({},
         ("DYNAMIC_DECODE_MASK_COLLAPSE", "FLOODFILL_OR_CONTOUR_TOPOLOGY_EMPTY", "DESIGN_EXPLICIT_OBJECT_LEVEL_TECHNICAL_DISPOSITION")),
        ({"generated_sector_pixel_count": 7, "post_mask_nonzero_pixel_count": 9,
          "post_mask_adjacent_temporal_variation_pixel_count": 2,
          "source_sector_nonempty_gate_passed": True,
          "source_nonzero_retained_pixel_gate_passed": True,
          "source_temporal_variation_gate_passed": True},
         ("PERSISTED_SOURCE_FAILURE_CONTRADICTED", "NOT_APPLICABLE", "READ_ONLY_AUTHORITY_RECONCILIATION")),
        ({"contradiction_kind": "MIRROR_MASK_MISMATCH", "mask_mirror_equals_frozen_helper": False},
         ("REPLAY_EVIDENCE_CONTRADICTORY", "NOT_APPLICABLE", "MASK_IMPLEMENTATION_REPAIR_REVIEW")),
        ({"contradiction_kind": "SOURCE_METRIC_RECOMPUTATION_EXCEPTION"},
         ("REPLAY_EVIDENCE_CONTRADICTORY", "NOT_APPLICABLE", "MASK_IMPLEMENTATION_REPAIR_REVIEW")),
        ({"production_mask_exception_class": "ValueError"},
         ("MASK_CONSTRUCTION_EXCEPTION", "NOT_APPLICABLE", "MASK_IMPLEMENTATION_REPAIR_REVIEW")),
    ],
)
def test_closed_classification_matrix(
    changes: dict[str, object], expected: tuple[str, str, str]
) -> None:
    assert replay.classify_premask_observation(_collapse_value(**changes)) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"persistent_occupancy_pixel_count": 0, "first_last_difference_pixel_count": 0},
         "MULTIPLE_MASK_COLLAPSE_MECHANISMS"),
        ({"persistent_occupancy_pixel_count": 0}, "PERSISTENT_OCCUPANCY_ERODED_TO_ZERO"),
        ({"first_last_difference_pixel_count": 0}, "FIRST_LAST_DIFFERENCE_ZERO"),
        ({"overlap_before_floodfill_pixel_count": 0}, "OVERLAP_EMPTY"),
        ({}, "FLOODFILL_OR_CONTOUR_TOPOLOGY_EMPTY"),
        ({"generated_sector_pixel_count": 3, "post_mask_nonzero_pixel_count": 0,
          "post_mask_adjacent_temporal_variation_pixel_count": 0}, "MULTIPLE_MASK_COLLAPSE_MECHANISMS"),
        ({"generated_sector_pixel_count": 3, "post_mask_nonzero_pixel_count": 5,
          "post_mask_adjacent_temporal_variation_pixel_count": 0}, "POSTMASK_ZERO_VARIATION"),
    ],
)
def test_mask_mechanism_uses_earliest_independent_collapse(
    changes: dict[str, object], expected: str
) -> None:
    assert replay.classify_mask_collapse(_collapse_value(**changes)) == expected


def test_compute_exception_is_closed_and_exports_no_exception_text() -> None:
    frames = np.ones((2, 4, 4, 3), dtype=np.uint8)

    def mirror(value: np.ndarray, _cv2: object):
        mask = np.zeros(value.shape[1:3], dtype=bool)
        return np.zeros_like(value), mask, {
            "first_last_difference_pixel_count": 0,
            "persistent_occupancy_pixel_count": 0,
            "overlap_before_floodfill_pixel_count": 0,
            "generated_sector_pixel_count": 0,
        }

    def raising_helper(_value: np.ndarray, _cv2: object):
        raise ValueError("private locator must never escape")

    observed = replay.compute_premask_observation(
        frames, cv2_module=object(), mask_helper=raising_helper, mirror=mirror
    )
    assert observed["premask_replay_class"] == "MASK_CONSTRUCTION_EXCEPTION"
    assert observed["production_mask_exception_class"] == "ValueError"
    assert "private" not in json.dumps(observed)


def test_access_counters_and_single_nofollow_read_are_hard_bounded(tmp_path: Path) -> None:
    source = (tmp_path / "source.bin").resolve()
    payload = b"one retained object"
    source.write_bytes(payload)
    os.chmod(source, 0o600)
    counters = replay.AccessCounters(allowed_source=source)
    observed, digest = replay._read_source_once(
        source, expected_identity=_identity(source), counters=counters
    )
    assert observed == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert counters.local_read_passes == 1
    assert counters.unique_objects == 1
    counters.register_decode(source)
    assert counters.decode_invocations == 1
    assert counters.unique_objects == 1

    with pytest.raises(replay.PremaskReplayError) as second_read:
        counters.register_read(source)
    assert second_read.value.code == "R4D2_ONE_OBJECT_READ_SCOPE_EXCEEDED"
    with pytest.raises(replay.PremaskReplayError) as second_decode:
        counters.register_decode(source)
    assert second_decode.value.code == "R4D2_ONE_OBJECT_DECODE_SCOPE_EXCEEDED"
    with pytest.raises(replay.PremaskReplayError):
        replay.AccessCounters(source).register_read(tmp_path / "other")


def test_single_read_rejects_an_actual_leaf_symlink(tmp_path: Path) -> None:
    target = (tmp_path / "target").resolve()
    target.write_bytes(b"retained object")
    os.chmod(target, 0o600)
    leaf = (tmp_path / "leaf").resolve()
    leaf.symlink_to(target)
    counters = replay.AccessCounters(allowed_source=leaf)

    with pytest.raises(replay.PremaskReplayError) as captured:
        replay._read_source_once(
            leaf,
            expected_identity=_identity(target),
            counters=counters,
        )
    assert captured.value.code == "R4D2_SOURCE_READ_AUTHORITY_INVALID"
    assert counters.local_read_passes == 1
    assert counters.decode_invocations == 0


def test_execute_writes_exactly_two_safe_no_clobber_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = (tmp_path / "attempt").resolve()
    parent = (tmp_path / "owner_private").resolve()
    attempt.mkdir()
    parent.mkdir()
    os.chmod(attempt, 0o2700)
    os.chmod(parent, 0o2700)
    source = attempt / "source"
    source.write_bytes(b"dicom")
    os.chmod(source, 0o600)
    source_identity = _identity(source)
    inventory = _frozen_inventory(attempt)
    failed, _ = _failed_row()
    diagnostic = parent / ("lvef_c3_r4d2_premask_" + "f" * 16)
    mount = replay.r3e.CurrentMountAuthority(
        "PASS_APPROVED_RESTRICTED_RESEARCH_MOUNT", "d" * 64
    )
    preflight = replay.PremaskReplayPreflight(
        execution_commit="f" * 40,
        attempt_root=attempt,
        inventory=inventory,
        failed_row=failed,
        planned_object={"size_bytes": 5},
        source_path=source,
        source_identity=source_identity,
        root_identity=inventory.root_identity,
        mount_authority=mount,
        local_sha256=hashlib.sha256(b"dicom").hexdigest(),
        diagnostic_root=diagnostic,
        diagnostic_parent=replay.r3e._private_directory_identity(parent),
        artifact_digests={},
    )

    def reader(path: Path, *, expected_identity: object, counters: replay.AccessCounters):
        assert expected_identity == source_identity
        counters.register_read(path)
        return b"dicom", hashlib.sha256(b"dicom").hexdigest()

    frames = np.ones((replay.EXPECTED_SOURCE_FRAMES, 2, 2, 3), dtype=np.uint8)

    def decoder(payload: bytes, *, source_path: Path, counters: replay.AccessCounters):
        assert payload == b"dicom"
        counters.register_decode(source_path)
        return frames, _decode_metadata()

    monkeypatch.setattr(replay, "_revalidate_source", lambda _preflight: None)
    monkeypatch.setattr(
        replay,
        "_post_validate_original_authority",
        lambda _preflight, **_kwargs: inventory,
    )
    result = replay.run_execute(
        preflight=preflight,
        source_reader=reader,
        decoder=decoder,
        observation_computer=lambda *_args, **_kwargs: _computed_dynamic(),
        cv2_module=object(),
    )

    assert result["unique_dicom_objects_accessed"] == 1
    assert result["local_dicom_read_passes"] == 1
    assert result["pydicom_decode_invocations"] == 1
    assert {path.name for path in diagnostic.iterdir()} == {
        "premask_replay_observation.restricted.json",
        "premask_replay.aggregate_safe.json",
    }
    for path in diagnostic.iterdir():
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
        document = json.loads(path.read_text())
        serialized = json.dumps(document)
        assert all(
            token not in serialized
            for token in ("private-subject", "private-study", "/restricted/", ".dcm", ".npz")
        )
    before = {path.name: path.read_bytes() for path in diagnostic.iterdir()}
    with pytest.raises(replay.PremaskReplayError):
        replay.run_execute(
            preflight=preflight,
            source_reader=reader,
            decoder=decoder,
            observation_computer=lambda *_args, **_kwargs: _computed_dynamic(),
            cv2_module=object(),
        )
    assert {path.name: path.read_bytes() for path in diagnostic.iterdir()} == before


def test_safe_schema_rejects_locator_and_topology_rejects_extra_file(
    tmp_path: Path,
) -> None:
    value = {key: False for key in replay.OBSERVATION_KEYS}
    value.update(
        {
            "identifiers_emitted": False,
            "locators_emitted": False,
            "paths_emitted": False,
            "decoder_backend": "/restricted/private/source.dcm",
        }
    )
    with pytest.raises(replay.PremaskReplayError) as unsafe:
        replay._safe_document(value, replay.OBSERVATION_KEYS)
    assert unsafe.value.code == "R4D2_DIAGNOSTIC_SAFE_EXPORT_INVALID"

    root = (tmp_path / "diagnostic").resolve()
    root.mkdir()
    os.chmod(root, 0o700)
    for name in (
        "premask_replay_observation.restricted.json",
        "premask_replay.aggregate_safe.json",
        "extra.json",
    ):
        path = root / name
        path.write_text("{}")
        os.chmod(path, 0o600)
    with pytest.raises(replay.PremaskReplayError) as topology:
        replay._validate_output_topology(root)
    assert topology.value.code == "R4D2_DIAGNOSTIC_OUTPUT_TOPOLOGY_INVALID"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("source_frame_count", 8, "R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID"),
        ("source_sector_nonempty_gate_passed", True, "R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID"),
        ("contradiction_kind", "MIRROR_IMPLEMENTATION_EXCEPTION", "R4D2_DIAGNOSTIC_TECHNICAL_SCHEMA_INVALID"),
        ("premask_replay_class", "PREMASK_BLANK", "R4D2_DIAGNOSTIC_CLASSIFICATION_SCHEMA_INVALID"),
        ("mask_collapse_mechanism", "NOT_APPLICABLE", "R4D2_DIAGNOSTIC_CLASSIFICATION_SCHEMA_INVALID"),
        ("execution_commit", "not-a-commit", "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("paths_emitted", True, "R4D2_DIAGNOSTIC_SAFE_EXPORT_INVALID"),
    ],
)
def test_reopened_observation_rejects_closed_schema_mutations(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    document = _valid_observation()
    replay.validate_observation_document(document)
    document[field] = value
    payload = (json.dumps(document, sort_keys=True) + "\n").encode()
    path = (tmp_path / "observation.json").resolve()
    path.write_bytes(payload)
    os.chmod(path, 0o600)

    with pytest.raises(replay.PremaskReplayError) as captured:
        replay._reopen_json_receipt(
            path,
            expected_keys=replay.OBSERVATION_KEYS,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    assert captured.value.code == code


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("local_dicom_read_passes", 0, "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("pydicom_decode_invocations", True, "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("diagnostic_npz_created", True, "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("observation_receipt_bytes", 0, "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("observation_receipt_sha256", "bad", "R4D2_DIAGNOSTIC_JSON_SCHEMA_INVALID"),
        ("selected_next_action", "READ_ONLY_AUTHORITY_RECONCILIATION", "R4D2_DIAGNOSTIC_CLASSIFICATION_SCHEMA_INVALID"),
        ("observation_receipt_basename", "/restricted/private.json", "R4D2_DIAGNOSTIC_SAFE_EXPORT_INVALID"),
    ],
)
def test_reopened_aggregate_rejects_effect_and_schema_mutations(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    document = _valid_aggregate()
    replay.validate_aggregate_document(document)
    document[field] = value
    payload = (json.dumps(document, sort_keys=True) + "\n").encode()
    path = (tmp_path / "aggregate.json").resolve()
    path.write_bytes(payload)
    os.chmod(path, 0o600)

    with pytest.raises(replay.PremaskReplayError) as captured:
        replay._reopen_json_receipt(
            path,
            expected_keys=replay.AGGREGATE_KEYS,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    assert captured.value.code == code


def test_cli_has_only_two_exclusive_modes_and_preflight_effects_are_zero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = replay._parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option not in {"-h", "--help"}
    }
    assert options == {"--preflight-only", "--execute"}
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--preflight-only", "--execute"])

    monkeypatch.setattr(
        replay,
        "run_preflight",
        lambda: SimpleNamespace(execution_commit="f" * 40),
    )
    assert replay.main(["--preflight-only"]) == 0
    output = capsys.readouterr().out
    assert "R4D2_PREFLIGHT=PASS_ZERO_BODY_NO_ROOT" in output
    assert "UNIQUE_DICOM_OBJECTS_ACCESSED=0" in output
    assert "LOCAL_DICOM_READ_PASSES=0" in output
    assert "PYDICOM_DECODE_INVOCATIONS=0" in output
    assert "DIAGNOSTIC_ROOT_CREATED=NO" in output
    assert "NEW_CLOUD_REQUESTS=0" in output
    assert "NEW_QSUB_SUBMISSIONS=0" in output


def test_cli_blocked_failure_is_sanitized_and_zero_effect() -> None:
    output = io.StringIO()
    with (
        mock.patch.object(
            replay,
            "run_preflight",
            side_effect=replay.PremaskReplayError("R4D2_TEST_BLOCKED"),
        ),
        redirect_stdout(output),
    ):
        assert replay.main(["--preflight-only"]) == 78
    text = output.getvalue()
    assert "R4D2_PREFLIGHT=BLOCKED_R4D2_TEST_BLOCKED" in text
    assert "UNIQUE_DICOM_OBJECTS_ACCESSED=0" in text
    assert "LOCAL_DICOM_READ_PASSES=0" in text
    assert "PYDICOM_DECODE_INVOCATIONS=0" in text
    assert "DIAGNOSTIC_ROOT_CREATED=NO" in text


def test_direct_imports_and_safe_receipt_keys_expose_no_prohibited_interface() -> None:
    source = (ROOT / "scripts/replay_lvef_c3_batch3_premask_one_object.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(
        {"google", "torch", "tensorflow", "sklearn", "boto3", "requests"}
    )
    forbidden_keys = {
        "subject_id",
        "study_id",
        "clip_key",
        "physical_source_key",
        "source_relative_path",
        "output_relative_path",
        "gcs_uri",
    }
    assert replay.OBSERVATION_KEYS.isdisjoint(forbidden_keys)
    assert replay.AGGREGATE_KEYS.isdisjoint(forbidden_keys)
    assert "BytesIO(payload)" in source
    assert "O_NOFOLLOW" in source
    assert "np.save" not in source
    assert "to_csv(" not in source


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(replay.PremaskReplayError):
        replay._strict_json(b'{"status":"PASS","status":"FAIL"}')


class _ManualMonkeyPatch:
    def __init__(self) -> None:
        self._changes: list[tuple[object, str, object]] = []

    def setattr(self, target: object, name: str, value: object) -> None:
        self._changes.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, previous in reversed(self._changes):
            setattr(target, name, previous)
        self._changes.clear()


class _ManualCapture:
    def __init__(self) -> None:
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def readouterr(self) -> SimpleNamespace:
        result = SimpleNamespace(
            out=self.stdout.getvalue(), err=self.stderr.getvalue()
        )
        self.stdout.seek(0)
        self.stdout.truncate(0)
        self.stderr.seek(0)
        self.stderr.truncate(0)
        return result


def _manual_parameter_cases(
    function: Callable[..., Any],
) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = [{}]
    for names, values in getattr(function, "__manual_parametrize__", ()):
        expanded: list[dict[str, object]] = []
        for prior in cases:
            for raw in values:
                items = raw if len(names) > 1 else (raw,)
                if len(items) != len(names):
                    raise AssertionError("manual parameter width mismatch")
                expanded.append({**prior, **dict(zip(names, items, strict=True))})
        cases = expanded
    return cases


def _run_dependency_light_matrix() -> int:
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    passed = skipped = failed = 0
    for name, function in tests:
        cases = _manual_parameter_cases(function)
        for case_index, parameters in enumerate(cases, start=1):
            label = name if len(cases) == 1 else f"{name}[{case_index}]"
            patcher = _ManualMonkeyPatch()
            capture: _ManualCapture | None = None
            temporary: tempfile.TemporaryDirectory[str] | None = None
            try:
                arguments = dict(parameters)
                signature = inspect.signature(function)
                if "tmp_path" in signature.parameters:
                    temporary = tempfile.TemporaryDirectory(prefix="r4d2-test-")
                    arguments["tmp_path"] = Path(temporary.name).resolve()
                if "monkeypatch" in signature.parameters:
                    arguments["monkeypatch"] = patcher
                if "capsys" in signature.parameters:
                    capture = _ManualCapture()
                    arguments["capsys"] = capture
                if capture is None:
                    function(**arguments)
                else:
                    with redirect_stdout(capture.stdout), redirect_stderr(
                        capture.stderr
                    ):
                        function(**arguments)
            except _ManualSkip as exc:
                skipped += 1
                print(f"SKIP {label}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {label}: {type(exc).__name__}: {exc}")
            else:
                passed += 1
                print(f"PASS {label}")
            finally:
                patcher.undo()
                if temporary is not None:
                    temporary.cleanup()
    print(
        "R4D2_FOCUSED_MATRIX="
        f"passed:{passed},skipped:{skipped},failed:{failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light_matrix())
