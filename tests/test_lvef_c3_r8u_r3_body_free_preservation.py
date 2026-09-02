#!/usr/bin/env python3
"""Focused R8U-R3 preservation/retirement body-access boundaries."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement
import lvef_c3_full_sequential as sequential


def _write_extraction_manifest(path: Path, clip_keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=preservation.EXTRACTION_MANIFEST_HEADER
        )
        writer.writeheader()
        for index, clip_key in enumerate(clip_keys):
            row = {key: "" for key in preservation.EXTRACTION_MANIFEST_HEADER}
            row.update(
                {
                    "subject_id": f"subject-{index}",
                    "study_id": f"study-{index}",
                    "clip_key": clip_key,
                    "output_relative_path": (
                        f"clips/{clip_key[:2]}/{clip_key}.npz"
                    ),
                    "write_ok": "True",
                    "npz_sha256": chr(ord("c") + index) * 64,
                }
            )
            writer.writerow(row)


def test_r8u_r3_scope_is_exact_batch16_only() -> None:
    kwargs = {
        "artifact_validation_context": (
            preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
        ),
        "production_root": preservation.R8R_FIXED_PRODUCTION_ROOT,
        "attempt_id": preservation.R8R_FIXED_ATTEMPT_ID,
        "batch_id": preservation.R8U_R3_FIXED_BATCH_ID,
        "plan_sha256": preservation.R8R_FIXED_PLAN_SHA256,
        "governing_commit": preservation.R8R_FIXED_SCIENTIFIC_COMMIT,
        "scheduler_runner_path": (
            preservation.R8R_FIXED_SCHEDULER_RUNNER_PATH
        ),
    }
    preservation.validate_artifact_validation_scope(**kwargs)
    for field, bad in (
        ("batch_id", "c3_batch_002"),
        ("attempt_id", "lvef_c3_wrong_attempt"),
        ("scheduler_runner_path", Path("/tmp/wrong-runner")),
    ):
        changed = dict(kwargs)
        changed[field] = bad
        try:
            preservation.validate_artifact_validation_scope(**changed)
        except preservation.BatchPreservationError as exc:
            assert exc.code.startswith("R8U_R3_RECOVERY_")
        else:
            raise AssertionError(f"scope mutation accepted: {field}")


def test_candidate_npz_seal_is_metadata_only_and_exact() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        extraction = Path(temporary) / "dicom_extraction"
        clip_keys = ["a" * 64, "b" * 64]
        manifest = extraction / "extraction_manifest.restricted.csv"
        _write_extraction_manifest(manifest, clip_keys)
        for key in clip_keys:
            leaf = extraction / "clips" / "clips" / key[:2] / f"{key}.npz"
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_bytes(b"opaque-npz-body")
            leaf.chmod(0o600)
            leaf.parent.chmod(0o700)
        (extraction / "clips" / "clips").chmod(0o700)
        (extraction / "clips").chmod(0o700)
        with (
            mock.patch.object(
                preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
            ),
            mock.patch.object(
                preservation,
                "sha256_file",
                side_effect=AssertionError("NPZ body hash was reached"),
            ),
        ):
            sealed = preservation.seal_r8u_r3_extracted_npz_authority(
                extraction_manifest=manifest,
                extraction_root=extraction,
            )
            assert len(sealed) == 2
            for authority in sealed.values():
                preservation.validate_sealed_extracted_npz_metadata(authority)
        first_leaf = (
            extraction / "clips" / "clips" / "aa" / f"{'a' * 64}.npz"
        )
        first_leaf.chmod(0o644)
        with mock.patch.object(
            preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
        ):
            try:
                preservation.seal_r8u_r3_extracted_npz_authority(
                    extraction_manifest=manifest,
                    extraction_root=extraction,
                )
            except preservation.BatchPreservationError as exc:
                assert exc.code == "R8U_NPZ_MODE_INVALID"
            else:
                raise AssertionError("world-readable NPZ was accepted")
        first_leaf.chmod(0o600)
        first_leaf.parent.chmod(0o755)
        with mock.patch.object(
            preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
        ):
            try:
                preservation.seal_r8u_r3_extracted_npz_authority(
                    extraction_manifest=manifest,
                    extraction_root=extraction,
                )
            except preservation.BatchPreservationError as exc:
                assert exc.code == "R8U_R3_EXTRACTION_NPZ_TOPOLOGY_INVALID"
            else:
                raise AssertionError("world-readable NPZ directory was accepted")
        first_leaf.parent.chmod(0o700)
        extra = (
            extraction / "clips" / "clips" / "ff" / f"{'f' * 64}.npz"
        )
        extra.parent.mkdir(parents=True)
        extra.write_bytes(b"extra")
        extra.chmod(0o600)
        extra.parent.chmod(0o700)
        with mock.patch.object(
            preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
        ):
            try:
                preservation.seal_r8u_r3_extracted_npz_authority(
                    extraction_manifest=manifest,
                    extraction_root=extraction,
                )
            except preservation.BatchPreservationError as exc:
                assert exc.code == "R8U_NPZ_PATH_SET_MISMATCH"
            else:
                raise AssertionError("additional NPZ was accepted")


def test_retirement_tree_authority_uses_preservation_hashes_not_npz_bodies() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        production = Path(temporary) / "production"
        logical = production / "attempts" / "a" / "extracted_cache" / "b" / "dicom_extraction" / "clips"
        actual = Path(temporary) / "staged"
        keys = ["1" * 64, "2" * 64]
        rows: list[tuple[str, int, str]] = []
        for index, key in enumerate(keys):
            leaf = actual / "clips" / key[:2] / f"{key}.npz"
            leaf.parent.mkdir(parents=True, exist_ok=True)
            body = f"body-{index}".encode()
            leaf.write_bytes(body)
            leaf.chmod(0o600)
            leaf.parent.chmod(0o700)
            relative = (logical / leaf.relative_to(actual)).relative_to(
                production
            ).as_posix()
            rows.append((relative, len(body), chr(ord("d") + index) * 64))
        (actual / "clips").chmod(0o700)
        actual.chmod(0o700)
        manifest = Path(temporary) / "preservation.tsv"
        with manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(["relative_path", "size_bytes", "sha256", "role"])
            for relative, size, digest in rows:
                writer.writerow(
                    [relative, size, digest, "extracted_npz_cache_owner_retirable"]
                )
        original_path_open = Path.open
        original_os_open = os.open

        def guarded_path_open(path: Path, *args: object, **kwargs: object):
            if path.suffix.lower() == ".npz":
                raise AssertionError("NPZ Path.open was reached")
            return original_path_open(path, *args, **kwargs)

        def guarded_os_open(path: object, *args: object, **kwargs: object):
            if isinstance(path, (str, os.PathLike)) and str(path).lower().endswith(
                ".npz"
            ):
                raise AssertionError("NPZ os.open was reached")
            return original_os_open(path, *args, **kwargs)

        with (
            mock.patch.object(
                preservation, "R8U_R3_FIXED_EXTRACTION_NPZ_FILES", 2
            ),
            mock.patch.object(
                retirement,
                "sha256_file",
                side_effect=AssertionError("NPZ body hash was reached"),
            ),
            mock.patch("pathlib.Path.open", new=guarded_path_open),
            mock.patch("os.open", new=guarded_os_open),
        ):
            digest = retirement._r8u_r3_cache_tree_sha256(
                actual,
                logical_root=logical,
                production_root=production,
                preservation_manifest=manifest,
            )
            retirement.validate_preservation_coverage(
                manifest,
                production_root=production,
                required_roots=[(actual, logical)],
                artifact_validation_context=(
                    preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
                ),
                sealed_raw_dicom_authority={},
            )
        assert retirement.SHA_RE.fullmatch(digest)


def test_retirement_authorization_can_reuse_body_free_tree_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        production = root / "production"
        attempt = production / "attempts" / "a"
        preservation_root = attempt / "batches" / "c3_batch_015" / "preservation"
        extraction = (
            attempt / "extracted_cache" / "c3_batch_015" / "dicom_extraction"
        )
        paths = {
            "preservation": preservation_root,
            "eligibility_ledger": attempt / "eligible.json",
            "extraction": extraction,
        }
        run = SimpleNamespace(
            attempt_root=attempt,
            production_root=production,
            attempt_id="a",
            launch_authority_sha256="a" * 64,
        )
        tree_authority = mock.Mock(return_value="b" * 64)
        strict_tree = mock.Mock(
            side_effect=AssertionError("strict NPZ body hash was reached")
        )
        with (
            mock.patch.object(
                sequential.preservation,
                "load_json",
                return_value={"status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"},
            ),
            mock.patch.object(
                sequential.core,
                "load_strict_json",
                return_value={"authority": {}},
            ),
            mock.patch.object(
                sequential.core,
                "sha256_file",
                return_value="c" * 64,
            ),
            mock.patch.object(
                sequential.retirement,
                "_cache_tree_authority",
                tree_authority,
            ),
            mock.patch.object(
                sequential.retirement, "cache_tree_sha256", strict_tree
            ),
            mock.patch.object(sequential, "_ensure_private_directory"),
            mock.patch.object(
                sequential, "_write_private_json", return_value="d" * 64
            ),
        ):
            sequential._cache_retirement_authorization(
                run=run,
                batch_id="c3_batch_015",
                paths=paths,
                artifact_validation_context=(
                    retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
                ),
            )
        strict_tree.assert_not_called()
        tree_authority.assert_called_once_with(
            extraction / "clips",
            logical_root=extraction / "clips",
            production_root=production,
            preservation_manifest=(
                preservation_root
                / "batch_preservation_manifest.restricted.tsv"
            ),
            artifact_validation_context=(
                retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY
            ),
        )


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
    print(f"PASS: {len(tests)} R8U-R3 body-free preservation tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
