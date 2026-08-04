from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lvef_reconstruction_smoke.py"
SPEC = importlib.util.spec_from_file_location("lvef_reconstruction_smoke", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_heavy_optional_dependencies_are_not_imported_at_module_import() -> None:
    source = SCRIPT.read_text()
    top_level = source.split("def _dicom_header_row", 1)[0]
    assert "import torch" not in top_level
    assert "import torchvision" not in top_level
    assert "import pydicom" not in top_level
    assert "import cv2" not in top_level


def expect_raises(exception_type: type[BaseException], function, match: str | None = None) -> None:
    try:
        function()
    except exception_type as exc:
        if match is not None:
            assert match in str(exc)
    else:
        raise AssertionError(f"Expected {exception_type.__name__}")


def test_safe_relative_path_rejects_noncanonical_or_escaping_values() -> None:
    values = [
        "/absolute/file.dcm", "../escape.dcm", "a/../b.dcm", "a\\b.dcm",
        "./a.dcm", "a//b.dcm", " a.dcm",
    ]
    for value in values:
        expect_raises(ValueError, lambda value=value: smoke.safe_relative_path(value))


def test_clip_key_is_stable_and_namespaced() -> None:
    path = "files/p10/p10000001/s20000001/a.dcm"
    expected = hashlib.sha256(f"{smoke.CLIP_KEY_NAMESPACE}\0{path}".encode()).hexdigest()
    assert smoke.stable_clip_key(path) == expected
    assert smoke.stable_clip_key(path) == smoke.stable_clip_key(path)


def test_restricted_row_and_array_outputs_cannot_land_in_git_tree() -> None:
    expect_raises(
        ValueError,
        lambda: smoke.require_outside_repository_output(ROOT / "restricted_rows.csv"),
        "repository",
    )
    with tempfile.TemporaryDirectory() as directory:
        smoke.require_outside_repository_output(Path(directory) / "restricted_rows.csv")


def test_array_content_hash_encodes_shape_dtype_and_content() -> None:
    value = np.arange(12, dtype=np.float32).reshape(3, 4)
    assert smoke.array_content_sha256(value) == smoke.array_content_sha256(value.copy())
    assert smoke.array_content_sha256(value) != smoke.array_content_sha256(value.reshape(4, 3))
    assert smoke.array_content_sha256(value) != smoke.array_content_sha256(value.astype(np.float64))


def _source_frame(paths: list[str]) -> pd.DataFrame:
    assert len(paths) == 4
    return pd.DataFrame(
        {
            "subject_id": [10 + i for i in range(len(paths))],
            "study_id": [20 + i for i in range(len(paths))],
            "split": ["train"] * len(paths),
            "smoke_role": list(smoke.EXPECTED_SMOKE_ROLES),
            "source_relative_path": paths,
        }
    )


def test_download_audit_requires_exact_set_and_release_hashes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        paths = [
            "files/p00/p10/s20/a.dcm",
            "files/p00/p11/s21/b.dcm",
            "files/p00/p12/s22/c.dcm",
            "files/p00/p13/s23/d.dcm",
        ]
        checksums = {}
        for index, relative in enumerate(paths):
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"dicom-{index}".encode())
            checksums[relative] = smoke.sha256_file(path)

        restricted, summary = smoke.audit_downloaded_objects(_source_frame(paths[::-1]), checksums, tmp_path)
        assert summary["status"] == "PASS"
        assert summary["n_verified_objects"] == 4
        assert summary["smoke_role_set_exact"] is True
        assert restricted["source_relative_path"].tolist() == sorted(paths)
        assert restricted["download_ok"].all()

        unexpected = tmp_path / "files/p00/p12/s22/unexpected.dcm"
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_bytes(b"extra")
        _, failed = smoke.audit_downloaded_objects(_source_frame(paths), checksums, tmp_path)
        assert failed["status"] == "FAIL"
        assert failed["n_unexpected_objects"] == 1


def test_download_audit_rejects_duplicate_source_paths() -> None:
    with tempfile.TemporaryDirectory() as directory:
        frame = pd.DataFrame(
            {
                "subject_id": [1, 1],
                "study_id": [2, 2],
                "split": ["train", "train"],
                "smoke_role": [smoke.EXPECTED_SMOKE_ROLES[0]] * 2,
                "source_relative_path": ["a.dcm", "a.dcm"],
            }
        )
        expect_raises(
            ValueError,
            lambda: smoke.audit_downloaded_objects(frame, {"a.dcm": "0" * 64}, Path(directory)),
            "duplicate",
        )


def test_source_manifest_rejects_non_dicom_and_download_audit_blocks_symlink() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        non_dicom = pd.DataFrame(
            {
                "subject_id": [1],
                "study_id": [2],
                "split": ["train"],
                "smoke_role": [smoke.EXPECTED_SMOKE_ROLES[0]],
                "source_relative_path": ["files/a.txt"],
            }
        )
        expect_raises(
            ValueError,
            lambda: smoke.audit_downloaded_objects(non_dicom, {"files/a.txt": "0" * 64}, tmp_path),
            "DICOM",
        )

        paths = ["linked.dcm", "second.dcm", "third.dcm", "fourth.dcm"]
        checksums = {}
        real = tmp_path / "real.bin"
        real.write_bytes(b"dicom-0")
        linked = tmp_path / paths[0]
        linked.symlink_to(real)
        checksums[paths[0]] = smoke.sha256_file(real)
        for index, relative in enumerate(paths[1:], start=1):
            path = tmp_path / relative
            path.write_bytes(f"dicom-{index}".encode())
            checksums[relative] = smoke.sha256_file(path)
        frame = _source_frame(paths)
        _, summary = smoke.audit_downloaded_objects(
            frame, checksums, tmp_path
        )
        assert summary["status"] == "FAIL"
        assert summary["n_unsafe_symlink_objects"] == 1


def test_release_checksum_parser_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        good = tmp_path / "SHA256SUMS.txt"
        good.write_text(f"{'a' * 64}  ./files/a.dcm\n")
        assert smoke.read_release_checksums(good) == {"files/a.dcm": "a" * 64}
        bad = tmp_path / "bad.txt"
        bad.write_text("not-a-checksum files/a.dcm\n")
        expect_raises(ValueError, lambda: smoke.read_release_checksums(bad), "Malformed")


def test_dicom_summary_emits_counts_only_and_requires_cine() -> None:
    frame = pd.DataFrame(
        {
            "subject_id": [1, 2, 3, 4],
            "study_id": [10, 20, 30, 40],
            "smoke_role": list(smoke.EXPECTED_SMOKE_ROLES),
            "source_relative_path": ["a.dcm", "b.dcm", "c.dcm", "d.dcm"],
            "read_ok": [True, True, True, True],
            "is_multiframe": [True, True, True, False],
            "photometric_interpretation": ["YBR_FULL_422", "RGB", "MONOCHROME2", "MONOCHROME2"],
            "transfer_syntax_uid": [
                "1.2.840.10008.1.2.4.50",
                "1.2.840.10008.1.2.4.50",
                "1.2.840.10008.1.2.1",
                "1.2.840.10008.1.2.1",
            ],
        }
    )
    summary = smoke.summarize_dicom_audit(frame)
    assert summary["status"] == "PASS"
    assert summary["n_cine_candidates"] == 3
    assert summary["positive_control_role_cine_gate_passed"] is True
    assert summary["negative_control_zero_cine_gate_passed"] is True
    assert summary["photometric_interpretation_counts"] == {
        "MONOCHROME2": 2,
        "RGB": 1,
        "YBR_FULL_422": 1,
    }
    assert summary["transfer_syntax_uid_counts"] == {
        "1.2.840.10008.1.2.1": 2,
        "1.2.840.10008.1.2.4.50": 2,
    }
    assert "study_id" not in summary and "source_relative_path" not in summary
    frame["is_multiframe"] = False
    assert smoke.summarize_dicom_audit(frame)["status"] == "FAIL"


def test_dicom_role_gate_fails_positive_without_cine_or_negative_with_cine() -> None:
    frame = pd.DataFrame(
        {
            "subject_id": [1, 2, 3, 4],
            "study_id": [10, 20, 30, 40],
            "smoke_role": list(smoke.EXPECTED_SMOKE_ROLES),
            "source_relative_path": ["a.dcm", "b.dcm", "c.dcm", "d.dcm"],
            "read_ok": [True] * 4,
            "is_multiframe": [False, True, True, False],
        }
    )
    summary = smoke.summarize_dicom_audit(frame)
    assert summary["status"] == "FAIL"
    assert summary["positive_control_role_cine_gate_passed"] is False
    frame["is_multiframe"] = [True, True, True, True]
    summary = smoke.summarize_dicom_audit(frame)
    assert summary["status"] == "FAIL"
    assert summary["negative_control_zero_cine_gate_passed"] is False


def test_temporal_sampling_is_exact_and_repeatable() -> None:
    frames = np.arange(40 * 2 * 2 * 3, dtype=np.uint8).reshape(40, 2, 2, 3)
    first, first_indices = smoke.temporal_sample(frames, 32)
    second, second_indices = smoke.temporal_sample(frames, 32)
    assert np.array_equal(first, second)
    assert np.array_equal(first_indices, second_indices)
    assert first_indices.tolist() == np.linspace(0, 39, 32, dtype=np.int64).tolist()

    short = frames[:3]
    sampled, indices = smoke.temporal_sample(short, 5)
    assert indices.tolist() == [0, 1, 2, 2, 2]
    assert np.array_equal(sampled[-1], short[-1])
    assert smoke.TEMPORAL_SAMPLING_POLICY == "historical_compatible_linspace_or_tail_repeat_v1"


class _FakePixelsAPI:
    def __init__(self, converted: np.ndarray | None = None) -> None:
        self.converted = converted
        self.decode_calls: list[dict[str, object]] = []
        self.convert_calls: list[tuple[str, str]] = []

    def get_decoder(self, _transfer_syntax: str):
        return SimpleNamespace(available_plugins=("gdcm", "pylibjpeg"))

    def pixel_array(self, dataset, **kwargs):
        self.decode_calls.append(dict(kwargs))
        return np.asarray(dataset._stored_pixels).copy()

    def convert_color_space(self, value: np.ndarray, source: str, destination: str):
        self.convert_calls.append((source, destination))
        assert np.array_equal(value, self._expected_stored)
        assert self.converted is not None
        return self.converted.copy()


def _fake_dataset(pixels: np.ndarray, photometric: str, samples: int) -> SimpleNamespace:
    return SimpleNamespace(
        _stored_pixels=pixels,
        pixel_array=pixels,
        NumberOfFrames=pixels.shape[0],
        SamplesPerPixel=samples,
        BitsAllocated=8,
        BitsStored=8,
        PhotometricInterpretation=photometric,
        file_meta=SimpleNamespace(TransferSyntaxUID="1.2.840.10008.1.2.4.50"),
    )


def test_color_decode_oracles_convert_raw_ybr_once_and_preserve_raw_rgb() -> None:
    stored_ybr = np.asarray(
        [[[[76, 85, 255], [149, 43, 21]], [[29, 255, 107], [128, 128, 128]]]] * 2,
        dtype=np.uint8,
    )
    expected_rgb = np.asarray(
        [[[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [128, 128, 128]]]] * 2,
        dtype=np.uint8,
    )
    pixels_api = _FakePixelsAPI(expected_rgb)
    pixels_api._expected_stored = stored_ybr
    module = SimpleNamespace(pixels=pixels_api)
    decoded, metadata = smoke._normalize_dicom_pixels(
        _fake_dataset(stored_ybr, "YBR_FULL", 3), module
    )
    assert np.array_equal(decoded, expected_rgb)
    assert pixels_api.decode_calls == [{"raw": True, "decoding_plugin": "pylibjpeg"}]
    assert pixels_api.convert_calls == [("YBR_FULL", "RGB")]
    assert metadata["color_transform"] == "EXPLICIT_YBR_FULL_TO_RGB"
    assert metadata["decoder_color_behavior"] == "STORED_COLOR_RAW"
    assert metadata["canonical_color_space"] == "RGB"

    raw_rgb = np.asarray(
        [[[[1, 2, 3], [11, 12, 13]], [[21, 22, 23], [31, 32, 33]]]] * 2,
        dtype=np.uint8,
    )
    rgb_api = _FakePixelsAPI()
    rgb_api._expected_stored = raw_rgb
    decoded_rgb, rgb_metadata = smoke._normalize_dicom_pixels(
        _fake_dataset(raw_rgb, "RGB", 3), SimpleNamespace(pixels=rgb_api)
    )
    assert np.array_equal(decoded_rgb, raw_rgb)
    assert rgb_api.convert_calls == []
    assert rgb_metadata["color_transform"] == "NONE_RGB"


def test_color_decode_oracles_never_yuv_convert_grayscale_and_legacy_decode_fails_closed() -> None:
    grayscale = np.asarray(
        [[[0, 1], [2, 3]], [[4, 5], [6, 7]]], dtype=np.uint8
    )
    mono_api = _FakePixelsAPI()
    mono_api._expected_stored = grayscale
    decoded, metadata = smoke._normalize_dicom_pixels(
        _fake_dataset(grayscale, "MONOCHROME2", 1), SimpleNamespace(pixels=mono_api)
    )
    assert np.array_equal(decoded, np.repeat(grayscale[..., None], 3, axis=3))
    assert mono_api.convert_calls == []
    assert metadata["color_transform"] == "MONOCHROME2_REPLICATE_TO_RGB"

    mono1_api = _FakePixelsAPI()
    mono1_api._expected_stored = grayscale
    inverted, inverted_metadata = smoke._normalize_dicom_pixels(
        _fake_dataset(grayscale, "MONOCHROME1", 1), SimpleNamespace(pixels=mono1_api)
    )
    expected_inverted = np.repeat((np.uint8(255) - grayscale)[..., None], 3, axis=3)
    assert np.array_equal(inverted, expected_inverted)
    assert inverted_metadata["color_transform"] == "MONOCHROME1_INVERT_REPLICATE_TO_RGB"

    already_rgb = np.asarray(
        [[[[250, 10, 20], [30, 240, 40]], [[50, 60, 230], [90, 100, 110]]]] * 2,
        dtype=np.uint8,
    )
    legacy_dataset = _fake_dataset(already_rgb, "YBR_FULL_422", 3)
    legacy_module = SimpleNamespace(config=SimpleNamespace(pixel_data_handlers=()))
    expect_raises(
        ValueError,
        lambda: smoke._normalize_dicom_pixels(legacy_dataset, legacy_module),
        "pydicom 3 raw pixel API",
    )


def test_pixel_decode_fails_closed_without_compressed_plugin_or_exact_8_bit_range() -> None:
    rgb = np.asarray(
        [[[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]] * 2,
        dtype=np.uint8,
    )

    class _NoPluginPixels(_FakePixelsAPI):
        def get_decoder(self, _transfer_syntax: str):
            return SimpleNamespace(available_plugins=())

    no_plugin = _NoPluginPixels()
    no_plugin._expected_stored = rgb
    expect_raises(
        ValueError,
        lambda: smoke._normalize_dicom_pixels(
            _fake_dataset(rgb, "RGB", 3), SimpleNamespace(pixels=no_plugin)
        ),
        "no deterministic available decoder plugin",
    )

    native = _fake_dataset(rgb, "RGB", 3)
    native.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    native.BitsStored = 7
    native_api = _NoPluginPixels()
    native_api._expected_stored = rgb
    expect_raises(
        ValueError,
        lambda: smoke._normalize_dicom_pixels(native, SimpleNamespace(pixels=native_api)),
        "BitsAllocated=BitsStored=8",
    )


def test_rgb_luma_tensor_oracle_has_explicit_channel_order() -> None:
    colors = np.asarray([[[[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]]]], dtype=np.uint8)
    assert smoke._rgb_luma_uint8(colors).tolist() == [[[77, 149, 29, 255]]]
    source = SCRIPT.read_text()
    assert "COLOR_YUV2" not in source
    assert "COLOR_BGR2" not in source


def test_signal_gates_fail_closed_for_empty_blank_and_static_cines() -> None:
    dynamic = np.zeros((3, 4, 4, 3), dtype=np.uint8)
    dynamic[:, 1:3, 1:3] = np.asarray([10, 20, 40], dtype=np.uint8)[:, None, None, None]
    sector = np.zeros((4, 4), dtype=bool)
    sector[1:3, 1:3] = True
    passed = smoke._signal_quality_metrics(dynamic, sector)
    smoke._require_signal_quality(passed)
    assert passed["sector_pixel_count"] == 4
    assert passed["nonzero_retained_pixel_count"] == 12
    assert passed["temporal_variation_pixel_count"] == 8

    empty_sector = smoke._signal_quality_metrics(dynamic, np.zeros((4, 4), dtype=bool))
    expect_raises(ValueError, lambda: smoke._require_signal_quality(empty_sector), "gate failed")
    blank = smoke._signal_quality_metrics(np.zeros_like(dynamic), sector)
    assert blank["nonzero_retained_pixel_gate_passed"] is False
    expect_raises(ValueError, lambda: smoke._require_signal_quality(blank), "gate failed")
    static = np.full((3, 4, 4, 3), 17, dtype=np.uint8)
    static_metrics = smoke._signal_quality_metrics(static, sector)
    assert static_metrics["nonzero_retained_pixel_gate_passed"] is True
    assert static_metrics["temporal_variation_gate_passed"] is False
    expect_raises(ValueError, lambda: smoke._require_signal_quality(static_metrics), "gate failed")


def _extraction_rows(order: list[int]) -> pd.DataFrame:
    base = pd.DataFrame(
        {
            "subject_id": [1, 1],
            "study_id": [10, 10],
            "smoke_role": [smoke.POSITIVE_CONTROL_ROLES[0], smoke.POSITIVE_CONTROL_ROLES[0]],
            "source_relative_path": ["b.dcm", "a.dcm"],
            "clip_key": [smoke.stable_clip_key("b.dcm"), smoke.stable_clip_key("a.dcm")],
            "write_ok": [True, True],
            "mask_status": ["APPLIED", "APPLIED"],
            "source_sector_nonempty_gate_passed": [True, True],
            "source_nonzero_retained_pixel_gate_passed": [True, True],
            "source_temporal_variation_gate_passed": [True, True],
            "sampled_nonzero_retained_pixel_gate_passed": [True, True],
            "sampled_temporal_variation_gate_passed": [True, True],
            "temporal_sampling_policy": [smoke.TEMPORAL_SAMPLING_POLICY] * 2,
            "photometric_interpretation": ["YBR_FULL_422", "MONOCHROME2"],
            "transfer_syntax_uid": ["1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.1"],
            "decoder_backend": ["pydicom_pixels_raw:pylibjpeg", "pydicom_pixels_raw:native"],
            "decoder_color_behavior": ["STORED_COLOR_RAW", "STORED_COLOR_RAW"],
            "color_transform": ["EXPLICIT_YBR_FULL_422_TO_RGB", "MONOCHROME2_REPLICATE_TO_RGB"],
            "canonical_color_space": ["RGB", "RGB"],
            "frames_shape": ["32x224x224x3", "32x224x224x3"],
            "frames_dtype": ["uint8", "uint8"],
        }
    )
    return base.iloc[order].reset_index(drop=True)


def test_extraction_summary_requires_unique_keys_shape_and_mask() -> None:
    frame = _extraction_rows([0, 1])
    summary = smoke.summarize_extraction(frame)
    assert summary["status"] == "PASS"
    assert summary["all_preprocessing_signal_gates_passed"] is True
    assert summary["all_decoders_stored_color_raw_to_canonical_rgb"] is True
    assert summary["temporal_sampling_policy"] == smoke.TEMPORAL_SAMPLING_POLICY
    assert summary["photometric_interpretation_counts"] == {
        "MONOCHROME2": 1,
        "YBR_FULL_422": 1,
    }
    assert summary["decoder_backend_counts"] == {
        "pydicom_pixels_raw:native": 1,
        "pydicom_pixels_raw:pylibjpeg": 1,
    }
    assert "study_id" not in summary and "source_relative_path" not in summary
    frame.loc[1, "clip_key"] = frame.loc[0, "clip_key"]
    assert smoke.summarize_extraction(frame)["status"] == "FAIL"
    frame = _extraction_rows([0, 1])
    frame.loc[1, "mask_status"] = "FAILED"
    assert smoke.summarize_extraction(frame)["status"] == "FAIL"
    frame = _extraction_rows([0, 1])
    frame.loc[1, "sampled_temporal_variation_gate_passed"] = False
    assert smoke.summarize_extraction(frame)["status"] == "FAIL"


def test_checkpoint_gate_uses_name_size_and_sha() -> None:
    with tempfile.TemporaryDirectory() as directory:
        checkpoint = Path(directory) / smoke.CHECKPOINT_FILENAME
        checkpoint.write_bytes(b"synthetic-checkpoint")
        digest = smoke.sha256_file(checkpoint)
        result = smoke.validate_checkpoint(checkpoint, digest, checkpoint.stat().st_size)
        assert result["identity_gate_passed"] is True
        expect_raises(
            ValueError,
            lambda: smoke.validate_checkpoint(checkpoint, "0" * 64, checkpoint.stat().st_size),
            "identity",
        )


def _clip_embedding_fixture() -> tuple[np.ndarray, pd.DataFrame]:
    embeddings = np.stack(
        [
            np.linspace(0.1, 1.1, smoke.EMBEDDING_WIDTH, dtype=np.float32),
            np.linspace(1.1, 2.1, smoke.EMBEDDING_WIDTH, dtype=np.float32),
            np.linspace(2.1, 3.1, smoke.EMBEDDING_WIDTH, dtype=np.float32),
            np.linspace(3.1, 4.1, smoke.EMBEDDING_WIDTH, dtype=np.float32),
        ]
    )
    manifest = pd.DataFrame(
        {
            "embedding_idx": [0, 1, 2, 3],
            "subject_id": [1, 1, 2, 3],
            "study_id": [10, 10, 20, 30],
            "smoke_role": [
                smoke.POSITIVE_CONTROL_ROLES[0],
                smoke.POSITIVE_CONTROL_ROLES[0],
                smoke.POSITIVE_CONTROL_ROLES[1],
                smoke.POSITIVE_CONTROL_ROLES[2],
            ],
            "clip_key": ["b", "a", "c", "d"],
            "write_ok": [True, True, True, True],
            "embedding_l2_norm": np.linalg.norm(embeddings.astype(np.float64), axis=1),
            "embedding_sha256": [
                smoke.array_content_sha256(vector) for vector in embeddings
            ],
        }
    )
    return embeddings, manifest


def test_embedding_authority_rejects_index_misalignment_duplicates_and_nonfinite() -> None:
    embeddings, manifest = _clip_embedding_fixture()
    assert smoke.validate_embedding_authority(embeddings, manifest)["embedding_idx_authoritative"]
    bad = manifest.copy()
    bad.loc[1, "embedding_idx"] = 0
    expect_raises(ValueError, lambda: smoke.validate_embedding_authority(embeddings, bad), "embedding_idx")
    bad = manifest.copy()
    bad.loc[1, "clip_key"] = "b"
    expect_raises(ValueError, lambda: smoke.validate_embedding_authority(embeddings, bad), "Duplicate")
    nonfinite = embeddings.copy()
    nonfinite[0, 0] = np.nan
    expect_raises(ValueError, lambda: smoke.validate_embedding_authority(nonfinite, manifest), "nonfinite")


def test_mean_pool_is_float64_accumulated_and_stable_under_row_permutation() -> None:
    embeddings, manifest = _clip_embedding_fixture()
    first_array, first_manifest, first_summary = smoke.mean_pool_studies(embeddings, manifest)
    permutation = np.asarray([3, 2, 0, 1])
    permuted_embeddings = embeddings[permutation]
    permuted_manifest = manifest.iloc[permutation].reset_index(drop=True).copy()
    permuted_manifest["embedding_idx"] = np.arange(4)
    permuted_manifest["embedding_l2_norm"] = np.linalg.norm(permuted_embeddings.astype(np.float64), axis=1)
    permuted_manifest["embedding_sha256"] = [
        smoke.array_content_sha256(vector) for vector in permuted_embeddings
    ]
    second_array, second_manifest, _ = smoke.mean_pool_studies(permuted_embeddings, permuted_manifest)
    assert np.array_equal(first_array, second_array)
    assert smoke._normalized_manifest_hash(first_manifest) == smoke._normalized_manifest_hash(second_manifest)
    expected = embeddings[:2].mean(axis=0, dtype=np.float64).astype(np.float32)
    assert np.array_equal(first_array[0], expected)
    assert first_summary["accumulation_dtype"] == "float64"


def test_reproducibility_comparator_normalizes_manifest_order_and_hashes_arrays() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        run_a = tmp_path / "run-a"
        run_b = tmp_path / "run-b"
        extraction_a = tmp_path / "extraction-a.csv"
        extraction_b = tmp_path / "extraction-b.csv"
        _write_synthetic_extraction_run(run_a, extraction_a)
        _write_synthetic_extraction_run(run_b, extraction_b, reverse=True)

        clip_array, clip_manifest = _clip_embedding_fixture()
        study_array, study_manifest, _ = smoke.mean_pool_studies(
            clip_array, clip_manifest
        )
        clip_manifest_a = tmp_path / "clip-manifest-a.csv"
        clip_manifest_b = tmp_path / "clip-manifest-b.csv"
        study_manifest_a = tmp_path / "study-manifest-a.csv"
        study_manifest_b = tmp_path / "study-manifest-b.csv"
        clip_manifest.to_csv(clip_manifest_a, index=False)
        clip_manifest.iloc[::-1].to_csv(clip_manifest_b, index=False)
        study_manifest.to_csv(study_manifest_a, index=False)
        study_manifest.iloc[::-1].to_csv(study_manifest_b, index=False)
        clip_a = tmp_path / "clip-a.npz"
        clip_b = tmp_path / "clip-b.npz"
        study_a = tmp_path / "study-a.npz"
        study_b = tmp_path / "study-b.npz"
        np.savez_compressed(clip_a, embeddings=clip_array)
        np.savez_compressed(clip_b, embeddings=clip_array.copy())
        np.savez_compressed(study_a, embeddings=study_array)
        np.savez_compressed(study_b, embeddings=study_array.copy())

        manifest_pairs = [
            ("clip_manifest", clip_manifest_a, clip_manifest_b),
            ("study_manifest", study_manifest_a, study_manifest_b),
        ]
        array_pairs = [
            ("clip_embeddings", clip_a, clip_b),
            ("study_embeddings", study_a, study_b),
        ]
        extraction_pairs = [
            ("extraction", extraction_a, run_a, extraction_b, run_b)
        ]

        restricted, aggregate = smoke.compare_reproducibility(
            manifest_pairs, array_pairs, extraction_pairs
        )
        assert aggregate["status"] == "PASS"
        assert aggregate["n_exact_equal"] == 5
        assert all(row["exact_equal"] for row in restricted["pairs"])

        np.savez_compressed(clip_b, embeddings=clip_array + np.float32(1e-7))
        _, failed = smoke.compare_reproducibility(
            manifest_pairs, array_pairs, extraction_pairs
        )
        assert failed["status"] == "FAIL"
        assert failed["n_not_exact_equal"] == 1


def _write_synthetic_extraction_run(
    run_root: Path, manifest_path: Path, *, mutate_second_clip: bool = False, reverse: bool = False
) -> pd.DataFrame:
    rows = []
    for index, source in enumerate(("source-a.dcm", "source-b.dcm")):
        clip_key = smoke.stable_clip_key(source)
        output_relative = f"clips/{clip_key[:2]}/{clip_key}.npz"
        output = run_root / output_relative
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = np.arange(3 * 2 * 2 * 3, dtype=np.uint8).reshape(3, 2, 2, 3) + index
        if mutate_second_clip and index == 1:
            frames = frames.copy()
            frames[0, 0, 0, 0] += np.uint8(1)
        sampled_indices = np.asarray([0, 1, 2], dtype=np.int64)
        source_num_frames = np.asarray([3], dtype=np.int32)
        np.savez_compressed(
            output,
            frames=frames,
            sampled_indices=sampled_indices,
            source_num_frames=source_num_frames,
        )
        rows.append(
            {
                "subject_id": 1,
                "study_id": 10,
                "smoke_role": smoke.POSITIVE_CONTROL_ROLES[0],
                "source_relative_path": source,
                "clip_key": clip_key,
                "output_relative_path": output_relative,
                "write_ok": True,
                "frames_sha256": smoke.array_content_sha256(frames),
                "sampled_indices_sha256": smoke.array_content_sha256(sampled_indices),
                "source_num_frames_sha256": smoke.array_content_sha256(source_num_frames),
                # Deliberately run-specific: extraction exactness must not use this field.
                "npz_sha256": ("a" if run_root.name == "run-a" else "b") * 64,
            }
        )
    frame = pd.DataFrame(rows)
    if reverse:
        frame = frame.iloc[::-1].reset_index(drop=True)
    frame.to_csv(manifest_path, index=False)
    return frame


def test_extraction_comparator_uses_internal_arrays_not_npz_container_bytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run_a = root / "run-a"
        run_b = root / "run-b"
        manifest_a = root / "manifest-a.csv"
        manifest_b = root / "manifest-b.csv"
        _write_synthetic_extraction_run(run_a, manifest_a)
        _write_synthetic_extraction_run(run_b, manifest_b, reverse=True)
        result = smoke._compare_extraction_pair(
            "extraction", manifest_a, run_a, manifest_b, run_b
        )
        assert result["exact_equal"] is True
        assert result["n_internal_arrays_compared"] == 6
        assert result["npz_container_bytes_compared"] is False

        _write_synthetic_extraction_run(
            root / "run-c", root / "manifest-c.csv", mutate_second_clip=True
        )
        failed = smoke._compare_extraction_pair(
            "extraction",
            manifest_a,
            run_a,
            root / "manifest-c.csv",
            root / "run-c",
        )
        assert failed["exact_equal"] is False


def test_extraction_pair_cli_syntax_is_explicit() -> None:
    parsed = smoke.parse_extraction_pair("x=a.csv,run-a,b.csv,run-b")
    assert parsed == ("x", Path("a.csv"), Path("run-a"), Path("b.csv"), Path("run-b"))
    expect_raises(ValueError, lambda: smoke.parse_extraction_pair("x=a,b,c"), "Expected")


def test_cli_contract_contains_no_label_model_or_view_classifier_arguments() -> None:
    parser = smoke.build_parser()
    help_text = parser.format_help()
    source = SCRIPT.read_text()
    for prohibited in ("--label", "--prediction", "fit(", "view_classifier.pt", "convnext"):
        assert prohibited not in help_text
        assert prohibited not in source


def test_guarded_main_suppresses_exception_message_and_path() -> None:
    # Capture without pytest so this also runs in the SCC dependency-light runner.
    import contextlib
    import io

    secret_path = "/restricted/secret/source.csv"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        status = smoke.guarded_main(
            [
                "audit-downloads",
                "--source-manifest", secret_path,
                "--release-checksums", "/restricted/secret/SHA256SUMS.txt",
                "--download-root", "/restricted/secret/download",
                "--restricted-output", "/restricted/secret/out.csv",
                "--aggregate-output", "/restricted/secret/out.json",
            ]
        )
    output = buffer.getvalue()
    assert status == 2
    assert secret_path not in output
    assert "BLOCKED_SMOKE_EXCEPTION" in output
    assert '"exception_message_emitted": false' in output
