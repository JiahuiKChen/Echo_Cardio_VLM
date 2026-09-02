#!/usr/bin/env python3
"""Focused dependency-light tests for the R8U-R7 body-free NPZ seal."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preserve_lvef_c3_production_batch as preservation


_BASE_STAT = {
    "mode": stat.S_IFREG | 0o600,
    "inode": 40_001,
    "device": 30_001,
    "nlink": 1,
    "uid": os.geteuid(),
    "gid": os.getegid(),
    "size": 17,
    "atime_ns": 1_700_000_001_000_000_000,
    "mtime_ns": 1_700_000_002_000_000_000,
    "ctime_ns": 1_700_000_003_000_000_000,
}


def _stat_result(**changes: int) -> os.stat_result:
    """Construct a stat_result with explicit nanosecond fields."""

    values = dict(_BASE_STAT)
    values.update(changes)
    # The final six values populate the three unnamed platform slots followed
    # by st_atime_ns, st_mtime_ns, and st_ctime_ns on supported POSIX builds.
    return os.stat_result(
        (
            values["mode"],
            values["inode"],
            values["device"],
            values["nlink"],
            values["uid"],
            values["gid"],
            values["size"],
            values["atime_ns"] / 1_000_000_000,
            values["mtime_ns"] / 1_000_000_000,
            values["ctime_ns"] / 1_000_000_000,
            0,
            0,
            0,
            values["atime_ns"],
            values["mtime_ns"],
            values["ctime_ns"],
        )
    )


def _error_code(operation: Callable[[], object]) -> str:
    try:
        operation()
    except preservation.BatchPreservationError as exc:
        return exc.code
    raise AssertionError("expected BatchPreservationError")


def _write_extraction_manifest(path: Path, clip_keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=preservation.EXTRACTION_MANIFEST_HEADER
        )
        writer.writeheader()
        for index, clip_key in enumerate(clip_keys):
            row = {field: "" for field in preservation.EXTRACTION_MANIFEST_HEADER}
            row.update(
                {
                    "clip_key": clip_key,
                    "output_relative_path": (
                        f"clips/{clip_key[:2]}/{clip_key}.npz"
                    ),
                    "write_ok": "True",
                    "npz_sha256": f"{index + 1:064x}",
                }
            )
            writer.writerow(row)


def _create_cache(root: Path, clip_keys: list[str]) -> tuple[Path, dict[str, Path]]:
    manifest = root / "extraction_manifest.restricted.csv"
    _write_extraction_manifest(manifest, clip_keys)
    leaves: dict[str, Path] = {}
    for index, clip_key in enumerate(clip_keys):
        leaf = root / "clips" / "clips" / clip_key[:2] / f"{clip_key}.npz"
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(f"opaque-{index}".encode("ascii"))
        leaf.chmod(0o600)
        leaf.parent.chmod(0o700)
        leaves[clip_key] = leaf
    (root / "clips" / "clips").chmod(0o700)
    (root / "clips").chmod(0o700)
    return manifest, leaves


def _guard_npz_body_opens():
    original_path_open = Path.open
    original_os_open = os.open

    def guarded_path_open(path: Path, *args: object, **kwargs: object):
        if path.suffix.lower() == ".npz":
            raise AssertionError("extracted NPZ Path.open was reached")
        return original_path_open(path, *args, **kwargs)

    def guarded_os_open(path: object, *args: object, **kwargs: object):
        if isinstance(path, (str, os.PathLike)) and str(path).lower().endswith(
            ".npz"
        ):
            raise AssertionError("extracted NPZ os.open was reached")
        return original_os_open(path, *args, **kwargs)

    return (
        mock.patch("pathlib.Path.open", new=guarded_path_open),
        mock.patch("os.open", new=guarded_os_open),
        mock.patch.object(
            preservation,
            "sha256_file",
            side_effect=AssertionError("extracted NPZ body hash was reached"),
        ),
    )


def _sealed_authority(info: os.stat_result | None = None):
    observed = info if info is not None else _stat_result()
    return preservation.SealedExtractedNpzAuthority(
        path=Path("/private/tmp/r8u-r7-sealed-authority.npz"),
        output_relative_path=(
            "clips/aa/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.npz"
        ),
        size_bytes=int(observed.st_size),
        observed_sha256="b" * 64,
        metadata_projection=preservation._npz_stable_metadata_projection(observed),
        diagnostic_atime_ns=int(observed.st_atime_ns),
    )


def test_raw_stat_atime_inequality_has_equal_explicit_projection() -> None:
    before = _stat_result()
    after = _stat_result(atime_ns=_BASE_STAT["atime_ns"] + 1_000_000_000)
    assert before != after
    assert preservation._npz_stable_metadata_projection(
        before
    ) == preservation._npz_stable_metadata_projection(after)
    assert preservation.NPZ_STABLE_METADATA_FIELDS == (
        "device",
        "inode",
        "mode_including_type",
        "uid",
        "gid",
        "nlink",
        "size",
        "mtime_ns",
        "ctime_ns",
    )

    diagnostics: dict[str, int | str] = {}
    with mock.patch.object(
        preservation.os, "lstat", side_effect=[before, after]
    ):
        observation = preservation._extracted_npz_metadata_observation(
            Path("/private/tmp/r8u-r7-atime-only.npz"),
            approved_device=int(before.st_dev),
            diagnostics=diagnostics,
            count_evaluation=True,
        )
    assert observation.stable_projection == (
        preservation._npz_stable_metadata_projection(before)
    )
    assert observation.atime_only_difference is True
    assert diagnostics == {
        "files_evaluated": 1,
        "files_passing": 1,
        "first_failed_predicate": "NONE",
        "atime_only_differences": 1,
        "stable_metadata_differences": 0,
        "missing_paths": 0,
        "additional_paths": 0,
    }


def test_later_atime_change_is_diagnostic_only() -> None:
    sealed_info = _stat_result()
    authority = _sealed_authority(sealed_info)
    later = _stat_result(atime_ns=_BASE_STAT["atime_ns"] + 2_000_000_000)
    diagnostics: dict[str, int | str] = {}
    with mock.patch.object(preservation.os, "lstat", return_value=later):
        preservation.validate_sealed_extracted_npz_metadata(
            authority, diagnostics=diagnostics
        )
    assert diagnostics["first_failed_predicate"] == "NONE"
    assert diagnostics["atime_only_differences"] == 1
    assert diagnostics["stable_metadata_differences"] == 0


def test_initial_metadata_failures_are_field_specific() -> None:
    valid = _stat_result()
    path = Path("/private/tmp/r8u-r7-field-errors.npz")

    diagnostics: dict[str, int | str] = {}
    with mock.patch.object(preservation.os, "lstat", side_effect=OSError("closed")):
        code = _error_code(
            lambda: preservation._extracted_npz_metadata_observation(
                path, diagnostics=diagnostics
            )
        )
    assert code == "R8U_NPZ_LSTAT_FAILED"
    assert diagnostics["first_failed_predicate"] == code

    cases = (
        (
            "symlink",
            path,
            _stat_result(mode=stat.S_IFLNK | 0o600),
            None,
            "R8U_NPZ_SYMLINK_INVALID",
        ),
        (
            "owner",
            path,
            _stat_result(uid=os.geteuid() + 1),
            None,
            "R8U_NPZ_OWNER_MISMATCH",
        ),
        (
            "mode",
            path,
            _stat_result(mode=stat.S_IFREG | 0o640),
            None,
            "R8U_NPZ_MODE_INVALID",
        ),
        (
            "nlink",
            path,
            _stat_result(nlink=2),
            None,
            "R8U_NPZ_LINK_COUNT_INVALID",
        ),
        (
            "size",
            path,
            _stat_result(size=0),
            None,
            "R8U_NPZ_SIZE_INVALID",
        ),
        (
            "suffix",
            path.with_suffix(".bin"),
            valid,
            None,
            "R8U_NPZ_SUFFIX_INVALID",
        ),
        (
            "device",
            path,
            valid,
            int(valid.st_dev) + 1,
            "R8U_NPZ_DEVICE_TOPOLOGY_INVALID",
        ),
    )
    for label, candidate_path, info, approved_device, expected in cases:
        diagnostics = {}
        with mock.patch.object(preservation.os, "lstat", return_value=info):
            code = _error_code(
                lambda: preservation._extracted_npz_metadata_observation(
                    candidate_path,
                    approved_device=approved_device,
                    diagnostics=diagnostics,
                )
            )
        assert code == expected, label
        assert diagnostics["first_failed_predicate"] == expected, label
        assert diagnostics["stable_metadata_differences"] == 0, label

    diagnostics = {}
    changed_gid = _stat_result(gid=int(valid.st_gid) + 1)
    with mock.patch.object(
        preservation.os, "lstat", side_effect=[valid, changed_gid]
    ):
        code = _error_code(
            lambda: preservation._extracted_npz_metadata_observation(
                path, diagnostics=diagnostics
            )
        )
    assert code == "R8U_NPZ_STABLE_METADATA_CHANGED"
    assert diagnostics["first_failed_predicate"] == code
    assert diagnostics["stable_metadata_differences"] == 1


def test_symlink_fifo_socket_and_devices_are_rejected() -> None:
    path = Path("/private/tmp/r8u-r7-special-file.npz")
    modes = (
        stat.S_IFIFO,
        stat.S_IFSOCK,
        stat.S_IFCHR,
        stat.S_IFBLK,
    )
    for file_type in modes:
        info = _stat_result(mode=file_type | 0o600)
        with mock.patch.object(preservation.os, "lstat", return_value=info):
            assert _error_code(
                lambda: preservation._extracted_npz_metadata_observation(path)
            ) == "R8U_NPZ_NOT_REGULAR"


def test_every_stable_field_drift_blocks_and_preserves_specific_error() -> None:
    baseline = _stat_result()
    authority = _sealed_authority(baseline)
    cases = (
        (
            "device",
            _stat_result(device=int(baseline.st_dev) + 1),
            "R8U_NPZ_DEVICE_TOPOLOGY_INVALID",
        ),
        (
            "inode",
            _stat_result(inode=int(baseline.st_ino) + 1),
            "R8U_NPZ_SEALED_METADATA_CHANGED",
        ),
        (
            "type",
            _stat_result(mode=stat.S_IFIFO | 0o600),
            "R8U_NPZ_NOT_REGULAR",
        ),
        (
            "mode",
            _stat_result(mode=stat.S_IFREG | 0o640),
            "R8U_NPZ_MODE_INVALID",
        ),
        (
            "uid",
            _stat_result(uid=os.geteuid() + 1),
            "R8U_NPZ_OWNER_MISMATCH",
        ),
        (
            "gid",
            _stat_result(gid=int(baseline.st_gid) + 1),
            "R8U_NPZ_SEALED_METADATA_CHANGED",
        ),
        (
            "nlink",
            _stat_result(nlink=2),
            "R8U_NPZ_LINK_COUNT_INVALID",
        ),
        (
            "size",
            _stat_result(size=int(baseline.st_size) + 1),
            "R8U_NPZ_SEALED_METADATA_CHANGED",
        ),
        (
            "mtime_ns",
            _stat_result(mtime_ns=int(baseline.st_mtime_ns) + 1),
            "R8U_NPZ_SEALED_METADATA_CHANGED",
        ),
        (
            "ctime_ns",
            _stat_result(ctime_ns=int(baseline.st_ctime_ns) + 1),
            "R8U_NPZ_SEALED_METADATA_CHANGED",
        ),
    )
    for field, changed, expected in cases:
        diagnostics: dict[str, int | str] = {}
        with mock.patch.object(preservation.os, "lstat", return_value=changed):
            code = _error_code(
                lambda: preservation.validate_sealed_extracted_npz_metadata(
                    authority, diagnostics=diagnostics
                )
            )
        assert code == expected, field
        assert diagnostics["first_failed_predicate"] == expected, field
        expected_stable_count = int(expected == "R8U_NPZ_SEALED_METADATA_CHANGED")
        assert diagnostics["stable_metadata_differences"] == expected_stable_count

    diagnostics = {}
    with mock.patch.object(preservation.os, "lstat", side_effect=OSError("gone")):
        code = _error_code(
            lambda: preservation.validate_sealed_extracted_npz_metadata(
                authority, diagnostics=diagnostics
            )
        )
    assert code == "R8U_NPZ_LSTAT_FAILED"
    assert diagnostics["first_failed_predicate"] == code


def test_missing_additional_and_renamed_paths_fail_exact_closure() -> None:
    clip_keys = ["a" * 64, "b" * 64]
    scenarios = (
        ("missing", 1, 0),
        ("additional", 0, 1),
        ("renamed", 1, 1),
    )
    for scenario, expected_missing, expected_additional in scenarios:
        with tempfile.TemporaryDirectory() as temporary:
            extraction = Path(temporary) / "dicom_extraction"
            manifest, leaves = _create_cache(extraction, clip_keys)
            if scenario == "missing":
                leaves[clip_keys[0]].unlink()
            elif scenario == "additional":
                extra_key = "f" * 64
                extra = (
                    extraction
                    / "clips"
                    / "clips"
                    / "ff"
                    / f"{extra_key}.npz"
                )
                extra.parent.mkdir(parents=True)
                extra.write_bytes(b"additional")
                extra.chmod(0o600)
                extra.parent.chmod(0o700)
            else:
                renamed_key = "aa" + "f" * 62
                leaves[clip_keys[0]].rename(
                    leaves[clip_keys[0]].with_name(f"{renamed_key}.npz")
                )
            diagnostics: dict[str, int | str] = {}
            guards = _guard_npz_body_opens()
            with (
                mock.patch.object(
                    preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
                ),
                guards[0],
                guards[1],
                guards[2],
            ):
                code = _error_code(
                    lambda: preservation.seal_r8u_r3_extracted_npz_authority(
                        extraction_manifest=manifest,
                        extraction_root=extraction,
                        diagnostics=diagnostics,
                    )
                )
            assert code == "R8U_NPZ_PATH_SET_MISMATCH", scenario
            assert diagnostics["first_failed_predicate"] == code, scenario
            assert diagnostics["missing_paths"] == expected_missing, scenario
            assert diagnostics["additional_paths"] == expected_additional, scenario


def test_same_size_post_seal_substitution_fails_without_body_read() -> None:
    clip_key = "a" * 64
    with tempfile.TemporaryDirectory() as temporary:
        extraction = Path(temporary) / "dicom_extraction"
        manifest, leaves = _create_cache(extraction, [clip_key])
        with mock.patch.object(
            preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 1
        ):
            sealed = preservation.seal_r8u_r3_extracted_npz_authority(
                extraction_manifest=manifest, extraction_root=extraction
            )
        target = leaves[clip_key]
        authority = sealed[target]
        replacement = extraction / "same-size-replacement.npz"
        replacement.write_bytes(b"changed!"[: target.stat().st_size])
        assert replacement.stat().st_size == target.stat().st_size
        replacement.chmod(0o600)
        os.replace(replacement, target)
        assert target.stat().st_ino != authority.metadata_projection[1]

        diagnostics: dict[str, int | str] = {}
        guards = _guard_npz_body_opens()
        with guards[0], guards[1], guards[2]:
            code = _error_code(
                lambda: preservation.validate_sealed_extracted_npz_metadata(
                    authority, diagnostics=diagnostics
                )
            )
        assert code == "R8U_NPZ_SEALED_METADATA_CHANGED"
        assert diagnostics["first_failed_predicate"] == code
        assert diagnostics["stable_metadata_differences"] == 1


def test_diagnostic_schema_is_closed_and_aggregate_safe() -> None:
    diagnostics = preservation.new_npz_metadata_diagnostics()
    assert set(diagnostics) == preservation.NPZ_METADATA_DIAGNOSTIC_KEYS
    assert set(diagnostics) == {
        "files_evaluated",
        "files_passing",
        "first_failed_predicate",
        "atime_only_differences",
        "stable_metadata_differences",
        "missing_paths",
        "additional_paths",
    }
    assert diagnostics["first_failed_predicate"] == "NONE"
    assert all(
        isinstance(value, int) and not isinstance(value, bool) and value == 0
        for key, value in diagnostics.items()
        if key != "first_failed_predicate"
    )
    serialized_values = " ".join(str(value) for value in diagnostics.values())
    assert "/" not in serialized_values
    assert ".npz" not in serialized_values
    assert "clip" not in serialized_values.lower()
    assert "study" not in serialized_values.lower()


def test_exact_10187_synthetic_authority_passes_without_opening_bodies() -> None:
    count = 10_187
    keys = [f"{index:064x}" for index in range(count)]
    rows = [
        {
            "clip_key": clip_key,
            "output_relative_path": f"clips/00/{clip_key}.npz",
            "write_ok": "True",
            "npz_sha256": f"{index + 1:064x}",
        }
        for index, clip_key in enumerate(keys)
    ]
    extraction = Path("/private/tmp/r8u-r7-synthetic-authority")
    clips = extraction / "clips"
    filenames = [f"{clip_key}.npz" for clip_key in keys]
    walk_projection = [
        (str(clips), ["clips"], []),
        (str(clips / "clips"), ["00"], []),
        (str(clips / "clips" / "00"), [], filenames),
    ]

    def synthetic_lstat(path: os.PathLike[str] | str) -> os.stat_result:
        candidate = Path(path)
        if candidate.suffix.lower() == ".npz":
            return _stat_result(inode=50_000 + int(candidate.stem, 16))
        return _stat_result(
            mode=stat.S_IFDIR | 0o700,
            inode=49_999 if candidate == clips else 49_998,
            nlink=2,
            size=0,
        )

    diagnostics: dict[str, int | str] = {}
    guards = _guard_npz_body_opens()
    with (
        mock.patch.object(preservation, "read_csv_exact", return_value=rows),
        mock.patch.object(preservation.os, "walk", return_value=walk_projection),
        mock.patch.object(preservation.os, "lstat", side_effect=synthetic_lstat),
        guards[0],
        guards[1],
        guards[2],
    ):
        sealed = preservation.seal_r8u_r3_extracted_npz_authority(
            extraction_manifest=extraction / "extraction_manifest.restricted.csv",
            extraction_root=extraction,
            diagnostics=diagnostics,
        )
    assert len(sealed) == count
    assert diagnostics == {
        "files_evaluated": count,
        "files_passing": count,
        "first_failed_predicate": "NONE",
        "atime_only_differences": 0,
        "stable_metadata_differences": 0,
        "missing_paths": 0,
        "additional_paths": 0,
    }


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
    print(f"PASS: {len(tests)} R8U-R7 NPZ metadata tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
