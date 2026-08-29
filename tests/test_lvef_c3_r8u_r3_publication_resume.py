#!/usr/bin/env python3
"""Focused dependency-light R8U-R3 controller and runner tests."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout
import csv
import errno
import hashlib
import io
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as controller


SHA = "a" * 64
COMMIT = "b" * 40


def _expect_code(callable_value, expected: str) -> None:
    try:
        callable_value()
    except controller.R8RControllerError as exc:
        assert exc.code == expected
    else:
        raise AssertionError(f"expected {expected}")


def _successful_extraction_row(seed: str = "a") -> dict[str, object]:
    """Return one row accepted by the canonical extraction producer validator."""

    source_key = hashlib.sha256(f"source-{seed}".encode()).hexdigest()
    source_relative = f"{source_key}.dcm"
    clip_key = hashlib.sha256(
        f"{controller.stages.CLIP_KEY_NAMESPACE}\0{source_relative}".encode()
    ).hexdigest()
    return {
        "subject_id": "1",
        "study_id": "1",
        "smoke_role": "production_selected",
        "source_relative_path": source_relative,
        "source_sha256": hashlib.sha256(f"body-{seed}".encode()).hexdigest(),
        "clip_key": clip_key,
        "output_relative_path": f"clips/{clip_key[:2]}/{clip_key}.npz",
        "physical_source_key": source_key,
        "write_ok": True,
        "frames_shape": "32x224x224x3",
        "frames_dtype": "uint8",
        "frames_sha256": hashlib.sha256(f"frames-{seed}".encode()).hexdigest(),
        "sampled_indices_sha256": hashlib.sha256(
            f"indices-{seed}".encode()
        ).hexdigest(),
        "source_num_frames": 40,
        "source_num_frames_sha256": hashlib.sha256(
            f"frame-count-{seed}".encode()
        ).hexdigest(),
        "mask_status": "APPLIED",
        "photometric_interpretation": "RGB",
        "transfer_syntax_uid": "1.2.840.10008.1.2.1",
        "decoder_backend": "pydicom_pixels_raw:native",
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": "NONE_RGB",
        "canonical_color_space": "RGB",
        "temporal_sampling_policy": (
            "historical_compatible_linspace_or_tail_repeat_v1"
        ),
        "pixel_decode_ok": True,
        "decode_color_status": "PASS",
        "source_sector_pixel_count": 100,
        "source_sector_nonempty_gate_passed": True,
        "source_nonzero_retained_pixel_count": 50,
        "source_nonzero_retained_pixel_gate_passed": True,
        "source_temporal_variation_pixel_count": 10,
        "source_temporal_variation_gate_passed": True,
        "ordinary_post_crop_nonzero_retained_pixel_count": 50,
        "ordinary_post_crop_nonzero_retained_pixel_gate_passed": True,
        "ordinary_post_crop_temporal_variation_pixel_count": 10,
        "ordinary_post_crop_temporal_variation_gate_passed": True,
        "post_crop_nonzero_retained_pixel_count": 50,
        "post_crop_nonzero_retained_pixel_gate_passed": True,
        "post_crop_temporal_variation_pixel_count": 10,
        "post_crop_temporal_variation_gate_passed": True,
        "ordinary_sampled_nonzero_retained_pixel_count": 50,
        "ordinary_sampled_nonzero_retained_pixel_gate_passed": True,
        "ordinary_sampled_temporal_variation_pixel_count": 10,
        "ordinary_sampled_temporal_variation_gate_passed": True,
        "sampled_nonzero_retained_pixel_count": 50,
        "sampled_nonzero_retained_pixel_gate_passed": True,
        "sampled_temporal_variation_pixel_count": 10,
        "sampled_temporal_variation_gate_passed": True,
        "encoder_visible_nonzero_retained_pixel_count": 25,
        "encoder_visible_nonzero_retained_pixel_gate_passed": True,
        "encoder_visible_temporal_variation_pixel_count": 5,
        "encoder_visible_temporal_variation_gate_passed": True,
        "selected_preprocessing_path": (
            controller.stages.ORDINARY_PREPROCESSING_PATH
        ),
        "fallback_status": controller.stages.FALLBACK_NOT_ATTEMPTED,
        "failure_substage": "NONE",
        "npz_sha256": hashlib.sha256(f"npz-{seed}".encode()).hexdigest(),
        "error_code": None,
    }


def _write_extraction_manifest(path: Path, row: dict[str, object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=controller.preservation.EXTRACTION_MANIFEST_HEADER,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(row)
    path.chmod(0o600)


def _metadata_candidate_fixture(
    root: Path,
) -> tuple[PurePosixPath, Path]:
    root.mkdir(mode=0o700)
    for name in controller.R8U_R3_CANDIDATE_CONTROL_FILES:
        path = root / name
        path.write_bytes(b"control\n")
        path.chmod(0o600)
    row = _successful_extraction_row()
    relative = PurePosixPath("clips") / PurePosixPath(
        str(row["output_relative_path"])
    )
    npz = root / relative
    npz.parent.mkdir(parents=True, mode=0o700)
    for parent in (npz.parent, *npz.parent.parents):
        if parent == root.parent:
            break
        parent.chmod(0o700)
    npz.write_bytes(b"opaque-scientific-body")
    npz.chmod(0o600)
    return relative, npz


@contextmanager
def _forbid_npz_body_opens():
    real_os_open = os.open
    real_path_open = Path.open

    def guarded_os_open(path, *args, **kwargs):
        if str(path).endswith(".npz"):
            raise AssertionError("candidate preflight opened an NPZ body")
        return real_os_open(path, *args, **kwargs)

    def guarded_path_open(path: Path, *args, **kwargs):
        if path.suffix == ".npz":
            raise AssertionError("candidate preflight opened an NPZ body")
        return real_path_open(path, *args, **kwargs)

    with (
        mock.patch.object(controller.os, "open", side_effect=guarded_os_open),
        mock.patch.object(Path, "open", guarded_path_open),
    ):
        yield


def test_errno_classifier_is_closed_and_fallback_is_narrow() -> None:
    cases = {
        0: "RENAME_NOREPLACE_SUPPORTED",
        errno.EINVAL: "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        errno.ENOSYS: "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        errno.EOPNOTSUPP: "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
        errno.EXDEV: "RENAME_CROSS_MOUNT_EXDEV",
        errno.EACCES: "RENAME_PERMISSION_FAILURE",
        errno.EPERM: "RENAME_PERMISSION_FAILURE",
        errno.EEXIST: "OTHER_EXACT_ERRNO_CLASS",
        errno.ENOTEMPTY: "OTHER_EXACT_ERRNO_CLASS",
        errno.EIO: "OTHER_EXACT_ERRNO_CLASS",
    }
    for number, expected in cases.items():
        result = controller._R8UR3RenameResult(
            number == 0, number, "NONE" if number == 0 else errno.errorcode[number]
        )
        assert controller._r8u_r3_primary_classification(result) == expected
    assert controller.R8U_R3_PROCEEDABLE_PROBE_RESULTS == {
        "RENAME_NOREPLACE_SUPPORTED",
        "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    }


def test_raw_primary_invoker_is_called_exactly_once_and_preserves_errno() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source"
        target = root / "target"
        source.mkdir()
        calls: list[tuple[Path, Path]] = []

        def unsupported(left: Path, right: Path) -> int:
            calls.append((left, right))
            return errno.ENOSYS

        result = controller._r8u_r3_raw_rename_noreplace(
            source, target, invoker=unsupported
        )
        assert calls == [(source, target)]
        assert result == controller._R8UR3RenameResult(False, errno.ENOSYS, "ENOSYS")


def test_identity_and_mount_seals_are_portable_but_same_call_guards_remain() -> None:
    path = Path("/synthetic/mount/source")

    def directory_identity(*, device: int, inode: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_dev=device,
            st_ino=inode,
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=os.geteuid(),
            st_gid=os.getegid(),
        )

    login = directory_identity(device=11, inode=101)
    worker = directory_identity(device=29, inode=707)
    identity_shas: list[str] = []
    for observed in (login, worker):
        with (
            mock.patch.object(
                controller.sequential, "_require_nonsymlink_components"
            ),
            mock.patch.object(
                controller.os, "lstat", side_effect=[observed, observed]
            ),
        ):
            identity_shas.append(
                controller._r8u_r3_identity_sha256(path, directory=True)
            )
    assert identity_shas[0] == identity_shas[1]

    replaced = directory_identity(device=11, inode=102)
    with (
        mock.patch.object(
            controller.sequential, "_require_nonsymlink_components"
        ),
        mock.patch.object(
            controller.os, "lstat", side_effect=[login, replaced]
        ),
    ):
        _expect_code(
            lambda: controller._r8u_r3_identity_sha256(path, directory=True),
            "R8U_R3_PATH_AUTHORITY_INVALID",
        )

    def mountinfo(mount_id: int, major_minor: str) -> bytes:
        return (
            f"{mount_id} 1 {major_minor} / /synthetic/mount rw "
            "- nfs4 server:/fixed-export rw\n"
        ).encode("utf-8")

    node_results: list[tuple[tuple[object, ...], str]] = []
    for payload in (mountinfo(101, "8:1"), mountinfo(909, "0:42")):
        with (
            mock.patch.object(controller.sys, "platform", "linux"),
            mock.patch.object(
                controller, "_r8u_r3_stable_identity",
                return_value={"mode": 0o700, "uid": os.geteuid(), "gid": os.getegid()},
            ),
            mock.patch.object(Path, "read_bytes", return_value=payload),
        ):
            source_key, source_sha = controller._r8u_r3_mount_authority(
                Path("/synthetic/mount/source")
            )
            target_key, target_sha = controller._r8u_r3_mount_authority(
                Path("/synthetic/mount/target")
            )
        assert source_key == target_key
        assert source_sha == target_sha
        node_results.append((source_key, source_sha))
    assert node_results[0][0] != node_results[1][0]
    assert node_results[0][1] == node_results[1][1]


def test_metadata_candidate_projection_is_exact_and_opens_no_npz_body() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, _npz = _metadata_candidate_fixture(root)
        expected = frozenset({relative})
        with (
            mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1),
            _forbid_npz_body_opens(),
        ):
            projection = controller._r8u_r3_metadata_projection(
                root, expected_npz_paths=expected
            )
            assert projection.value["candidate_npz_files"] == 1
            assert projection.expected_npz_paths == expected


def test_metadata_projection_rejects_missing_extra_substitution_and_rename() -> None:
    def missing(root: Path, expected: PurePosixPath, npz: Path) -> None:
        npz.unlink()

    def extra(root: Path, expected: PurePosixPath, npz: Path) -> None:
        other = root / "clips" / "clips" / "ff" / f"{'f' * 64}.npz"
        other.parent.mkdir(mode=0o700)
        other.write_bytes(b"extra")
        other.chmod(0o600)

    def substitution(root: Path, expected: PurePosixPath, npz: Path) -> None:
        npz.unlink()
        other = root / "clips" / "clips" / "ee" / f"{'e' * 64}.npz"
        other.parent.mkdir(mode=0o700)
        other.write_bytes(b"substitute")
        other.chmod(0o600)

    def rename(root: Path, expected: PurePosixPath, npz: Path) -> None:
        npz.rename(npz.with_name(f"{'d' * 64}.npz"))

    for mutate in (missing, extra, substitution, rename):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, npz = _metadata_candidate_fixture(root)
            mutate(root, relative, npz)
            with (
                mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1),
                _forbid_npz_body_opens(),
            ):
                _expect_code(
                    lambda: controller._r8u_r3_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "CANDIDATE_NPZ_PATH_SET_MISMATCH",
                )


def test_metadata_projection_detects_control_and_npz_substitution() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, npz = _metadata_candidate_fixture(root)
        with mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1):
            baseline = controller._r8u_r3_metadata_projection(
                root, expected_npz_paths=frozenset({relative})
            )
            control = root / sorted(controller.R8U_R3_CANDIDATE_CONTROL_FILES)[0]
            control.write_bytes(b"changed\n")
            control_projection = controller._r8u_r3_metadata_projection(
                root, expected_npz_paths=frozenset({relative})
            )
            assert (
                control_projection.value[
                    "candidate_relative_file_projection_sha256"
                ]
                != baseline.value["candidate_relative_file_projection_sha256"]
            )
            npz.unlink()
            npz.write_bytes(b"replacement")
            npz.chmod(0o600)
            substituted = controller._r8u_r3_metadata_projection(
                root, expected_npz_paths=frozenset({relative})
            )
            assert (
                substituted.value["candidate_relative_file_projection_sha256"]
                != control_projection.value[
                    "candidate_relative_file_projection_sha256"
                ]
            )
            renamed = npz.with_name(f"{'b' * 64}.npz")
            npz.rename(renamed)
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "CANDIDATE_NPZ_PATH_SET_MISMATCH",
            )
            renamed.unlink()
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "CANDIDATE_NPZ_PATH_SET_MISMATCH",
            )


def test_canonical_dicom_audit_accepts_producer_booleans_and_rejects_unknown() -> None:
    fields = (
        "subject_id", "study_id", "source_relative_path", "read_ok",
        "is_multiframe", "pixel_decode_ok",
    )
    rows = (
        {
            "subject_id": "1", "study_id": "10",
            "source_relative_path": "a.dcm", "read_ok": "True",
            "is_multiframe": "True", "pixel_decode_ok": "True",
        },
        {
            "subject_id": "2", "study_id": "20",
            "source_relative_path": "b.dcm", "read_ok": "False",
            "is_multiframe": "False", "pixel_decode_ok": "False",
        },
    )
    canonical_validator = controller.stages.validate_production_dicom_rows

    def validate(records, *, expected_objects: int, expected_studies: int):
        assert expected_objects == 2
        assert expected_studies == 250
        assert [record["read_ok"] for record in records] == [True, False]
        assert [record["is_multiframe"] for record in records] == [True, False]
        assert [record["pixel_decode_ok"] for record in records] == [True, False]
        return canonical_validator(
            records, expected_objects=2, expected_studies=2
        )

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "dicom_audit.restricted.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        path.chmod(0o600)
        with (
            mock.patch.object(controller, "R8U_BATCH16_RAW_FILES", 2),
            mock.patch.object(
                controller.stages,
                "validate_production_dicom_rows",
                side_effect=validate,
            ) as production_validator,
        ):
            summary = controller._r8u_r3_normalize_dicom_audit(path)
        assert summary["n_objects"] == 2
        assert summary["n_readable"] == 1
        assert summary["n_multiframe_candidates"] == 1
        production_validator.assert_called_once()

        unknown = dict(rows[0], read_ok="MAYBE")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerow(unknown)
        _expect_code(
            lambda: controller._r8u_r3_normalize_dicom_audit(path),
            "CANDIDATE_DICOM_AUDIT_BOOLEAN_DIALECT_MISMATCH",
        )


def test_candidate_projection_maps_changed_stage_artifact_hash_exactly() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source_parent = root / "fresh" / controller.R8U_FIXED_BATCH_ID
        target = root / "canonical" / "dicom_extraction"
        run = SimpleNamespace(
            attempt_id=controller.ORIGINAL_ATTEMPT_ID,
            runtime_authority={},
            plan={"batches": [{} for _ in range(16)]},
        )
        tree = controller._R8UR3CandidateProjection(
            value={}, expected_npz_paths=frozenset()
        )
        with (
            mock.patch.object(
                controller, "R8U_FRESH_EXTRACTION_BATCH_ROOT", source_parent
            ),
            mock.patch.object(
                controller, "R8U_FRESH_PUBLICATION_PATH", root / "absent.json"
            ),
            mock.patch.object(
                controller.sequential,
                "_batch_paths",
                return_value={
                    "extraction": target,
                    "raw_batch": root / "raw" / controller.R8U_FIXED_BATCH_ID,
                },
            ),
            mock.patch.object(
                controller, "_r8u_r3_metadata_projection", return_value=tree
            ),
            mock.patch.object(
                controller.stages,
                "validate_completed_stage_for_recovery",
                side_effect=controller.stages.ProductionStageError(
                    "STAGE_COMPLETION_RECOVERY_AUTHORITY_MISMATCH"
                ),
            ) as completed_stage,
            mock.patch.object(
                controller,
                "_r8u_r3_normalize_dicom_audit",
                side_effect=AssertionError("audit reached after changed stage hash"),
            ) as audit,
        ):
            _expect_code(
                lambda: controller._r8u_r3_candidate_projection(run),
                "CANDIDATE_STAGE_COMPLETION_RECEIPT_INVALID",
            )
        completed_stage.assert_called_once()
        audit.assert_not_called()


def test_candidate_projection_maps_canonical_plan_ownership_failure_exactly() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source_parent = root / "fresh" / controller.R8U_FIXED_BATCH_ID
        target = root / "canonical" / "dicom_extraction"
        planned = {"objects": []}
        run = SimpleNamespace(
            attempt_id=controller.ORIGINAL_ATTEMPT_ID,
            runtime_authority={},
            plan={"batches": [*({} for _ in range(15)), planned]},
        )
        tree = controller._R8UR3CandidateProjection(
            value={}, expected_npz_paths=frozenset()
        )
        manifest = controller._R8UR3CanonicalExtractionManifest(
            rows=(), summary={}, expected_npz_paths=frozenset(),
            manifest_projection=(),
        )
        with (
            mock.patch.object(
                controller, "R8U_FRESH_EXTRACTION_BATCH_ROOT", source_parent
            ),
            mock.patch.object(
                controller, "R8U_FRESH_PUBLICATION_PATH", root / "absent.json"
            ),
            mock.patch.object(
                controller.sequential,
                "_batch_paths",
                return_value={
                    "extraction": target,
                    "raw_batch": root / "raw" / controller.R8U_FIXED_BATCH_ID,
                },
            ),
            mock.patch.object(
                controller, "_r8u_r3_metadata_projection", return_value=tree
            ),
            mock.patch.object(
                controller.stages,
                "validate_completed_stage_for_recovery",
                return_value={},
            ),
            mock.patch.object(
                controller, "_r8u_r3_normalize_dicom_audit", return_value={}
            ),
            mock.patch.object(
                controller,
                "_r8u_r3_normalize_extraction_manifest",
                return_value=manifest,
            ),
            mock.patch.object(
                controller.stages,
                "validate_extraction_manifest_plan_membership",
                side_effect=controller.stages.ProductionStageError(
                    "EXTRACTION_MANIFEST_PLAN_MEMBERSHIP_MISMATCH"
                ),
            ) as plan_validator,
            mock.patch.object(
                controller.stages,
                "read_technical_disposition_manifest",
                side_effect=AssertionError("disposition reached after ownership failure"),
            ) as disposition,
        ):
            _expect_code(
                lambda: controller._r8u_r3_candidate_projection(run),
                "CANDIDATE_EXTRACTION_MANIFEST_PLAN_MISMATCH",
            )
        plan_validator.assert_called_once()
        assert plan_validator.call_args.args[1] is planned
        disposition.assert_not_called()


def test_canonical_manifest_accepts_exact_true_none_producer_dialect() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "extraction_manifest.restricted.csv"
        raw = _successful_extraction_row()
        _write_extraction_manifest(path, raw)
        normalized = controller._r8u_r3_normalize_extraction_manifest(path)
        assert len(normalized.rows) == 1
        assert normalized.rows[0]["write_ok"] is True
        assert normalized.rows[0]["failure_substage"] == "NONE"
        assert normalized.summary == (
            controller.stages.validate_production_extraction_rows(
                normalized.rows, expected_cines=1
            )
        )
        output_relative = PurePosixPath(str(raw["output_relative_path"]))
        expected = PurePosixPath("clips") / output_relative
        assert normalized.expected_npz_paths == frozenset({expected})
        assert normalized.manifest_projection == (
            (expected.as_posix(), str(raw["npz_sha256"])),
        )


def test_canonical_manifest_rejects_unrecognized_closed_tokens() -> None:
    for field, token in (
        ("write_ok", "YES"),
        ("failure_substage", "None"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "extraction_manifest.restricted.csv"
            row = _successful_extraction_row()
            row[field] = token
            _write_extraction_manifest(path, row)
            _expect_code(
                lambda: controller._r8u_r3_normalize_extraction_manifest(path),
                "CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH",
            )


def test_canonical_manifest_rejects_nonproducer_schema() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "extraction_manifest.restricted.csv"
        row = _successful_extraction_row()
        fields = list(controller.preservation.EXTRACTION_MANIFEST_HEADER[:-1])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerow({key: row[key] for key in fields})
        path.chmod(0o600)
        _expect_code(
            lambda: controller._r8u_r3_normalize_extraction_manifest(path),
            "CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID",
        )


def test_candidate_control_closure_and_modes_are_role_specific() -> None:
    for missing in sorted(controller.R8U_R3_CANDIDATE_CONTROL_FILES):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, _npz = _metadata_candidate_fixture(root)
            (root / missing).unlink()
            with mock.patch.object(
                controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1
            ):
                _expect_code(
                    lambda: controller._r8u_r3_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "CANDIDATE_CONTROL_SET_INVALID",
                )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, _npz = _metadata_candidate_fixture(root)
        unknown = root / "unknown_producer_file.restricted.json"
        unknown.write_bytes(b"{}\n")
        unknown.chmod(0o600)
        with mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1):
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "CANDIDATE_CONTROL_SET_INVALID",
            )

    for transient_name in (".nfs0000000000000001", ".candidate.tmp"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, _npz = _metadata_candidate_fixture(root)
            transient = root / transient_name
            transient.write_bytes(b"transient")
            transient.chmod(0o600)
            with mock.patch.object(
                controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1
            ):
                _expect_code(
                    lambda: controller._r8u_r3_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "CANDIDATE_UNEXPECTED_TRANSIENT_FILE",
                )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, _npz = _metadata_candidate_fixture(root)
        control = root / sorted(controller.R8U_R3_CANDIDATE_CONTROL_FILES)[0]
        control.chmod(0o644)
        with mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1):
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "CANDIDATE_CONTROL_FILE_MODE_INVALID",
            )


def test_candidate_npz_metadata_authority_is_distinct_from_path_closure() -> None:
    def wrong_mode(root: Path, npz: Path) -> None:
        npz.chmod(0o644)

    def zero_body(root: Path, npz: Path) -> None:
        npz.write_bytes(b"")

    def extra_directory(root: Path, npz: Path) -> None:
        extra = root / "clips" / "clips" / "unused"
        extra.mkdir(mode=0o700)

    def wrong_directory_mode(root: Path, npz: Path) -> None:
        npz.parent.chmod(0o755)

    def extra_hardlink(root: Path, npz: Path) -> None:
        os.link(npz, root.parent / "second-link.npz")

    def symlinked_npz(root: Path, npz: Path) -> None:
        npz.unlink()
        npz.symlink_to(root / "dicom_audit.restricted.csv")

    def nonregular_npz(root: Path, npz: Path) -> None:
        npz.unlink()
        os.mkfifo(npz, mode=0o600)

    for mutate in (
        wrong_mode,
        zero_body,
        extra_directory,
        wrong_directory_mode,
        extra_hardlink,
        symlinked_npz,
        nonregular_npz,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, npz = _metadata_candidate_fixture(root)
            mutate(root, npz)
            with (
                mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1),
                _forbid_npz_body_opens(),
            ):
                _expect_code(
                    lambda: controller._r8u_r3_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID",
                )


def test_candidate_root_owner_and_cross_device_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        missing = Path(temporary).resolve() / "missing"
        _expect_code(
            lambda: controller._r8u_r3_metadata_projection(missing),
            "CANDIDATE_ROOT_AUTHORITY_INVALID",
        )

    for metadata_field in ("st_dev", "st_uid"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, npz = _metadata_candidate_fixture(root)
            real_lstat = os.lstat

            def changed_lstat(path, *, dir_fd=None):
                observed = real_lstat(path, dir_fd=dir_fd)
                if Path(path) != npz:
                    return observed
                fields = (
                    "st_mode", "st_uid", "st_gid", "st_dev", "st_ino",
                    "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
                )
                values = {field: getattr(observed, field) for field in fields}
                values[metadata_field] = values[metadata_field] + 1
                return SimpleNamespace(**values)

            with (
                mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1),
                mock.patch.object(controller.os, "lstat", side_effect=changed_lstat),
                _forbid_npz_body_opens(),
            ):
                _expect_code(
                    lambda: controller._r8u_r3_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID",
                )


def test_closed_candidate_failure_registry_is_complete_and_generic_code_is_gone() -> None:
    expected = frozenset(
        {
            "CANDIDATE_ROOT_AUTHORITY_INVALID",
            "CANDIDATE_TARGET_STATE_INVALID",
            "CANDIDATE_STAGE_COMPLETION_RECEIPT_INVALID",
            "CANDIDATE_STAGE_EVENT_COMMIT_BINDING_INVALID",
            "CANDIDATE_CONTROL_SET_INVALID",
            "CANDIDATE_CONTROL_FILE_MODE_INVALID",
            "CANDIDATE_UNEXPECTED_TRANSIENT_FILE",
            "CANDIDATE_DICOM_AUDIT_SCHEMA_INVALID",
            "CANDIDATE_DICOM_AUDIT_BOOLEAN_DIALECT_MISMATCH",
            "CANDIDATE_DICOM_AUDIT_SEMANTIC_MISMATCH",
            "CANDIDATE_EXTRACTION_MANIFEST_SCHEMA_INVALID",
            "CANDIDATE_EXTRACTION_MANIFEST_SERIALIZATION_DIALECT_MISMATCH",
            "CANDIDATE_EXTRACTION_MANIFEST_PLAN_MISMATCH",
            "CANDIDATE_TECHNICAL_DISPOSITION_MANIFEST_INVALID",
            "CANDIDATE_NPZ_PATH_SET_MISMATCH",
            "CANDIDATE_NPZ_METADATA_AUTHORITY_INVALID",
            "CANDIDATE_EXTRACTION_SUMMARY_MISMATCH",
            "CANDIDATE_MOUNT_AUTHORITY_INVALID",
            "GENUINE_COMPLETED_EXTRACTION_INCONSISTENCY",
            "CANDIDATE_FAILURE_UNRESOLVED",
        }
    )
    assert controller.R8U_R3_CANDIDATE_FAILURE_CODES == expected
    candidate_functions = (
        controller._r8u_r3_metadata_projection,
        controller._r8u_r3_normalize_dicom_audit,
        controller._r8u_r3_normalize_extraction_manifest,
        controller._r8u_r3_candidate_projection,
        controller._r8u_r3_validate_candidate_seal_static,
        controller.validate_r8u_r3_extraction_candidate_seal,
    )
    for function in candidate_functions:
        source = inspect.getsource(function)
        assert "R8U_PUBLICATION_SOURCE_AUTHORITY_INVALID" not in source
    controller_source = (
        ROOT / "scripts" / "lvef_c3_r8r_recovery_continuation.py"
    ).read_text(encoding="utf-8")
    assert "R8U_PUBLICATION_SOURCE_AUTHORITY_INVALID" not in controller_source


def test_historical_extraction_event_and_candidate_seal_fail_role_specifically() -> None:
    historical = controller._r8u_r2_historical_implementation_authority_epochs()
    values = {
        controller.R8U_FAILED_PARTIAL_SEAL_PATH: ({}, b"{}"),
        controller.R8U_RECOVERY_CAPACITY_PATH: ({}, b"{}"),
        controller.R8U_RECOVERY_AUTHORITY_PATH: (
            {
                "implementation_commit": "f" * 40,
                "implementation_authority_epochs": historical,
            },
            b"{}",
        ),
        controller.R8U_RECOVERY_SUBMISSION_PATH: (
            {
                "implementation_commit": (
                    controller.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT
                ),
                "implementation_authority_epochs": historical,
                "qsub_environment_sha256": SHA,
            },
            b"{}",
        ),
    }

    with (
        mock.patch.object(
            controller, "_load_private_json", side_effect=lambda path: values[path]
        ),
        mock.patch.object(controller, "_r8u_failed_partial_seal", return_value={}),
        mock.patch.object(
            controller, "_r8u_recovery_submission_receipt", return_value={}
        ),
        mock.patch.object(
            controller.capacity, "validate_fixed_r8u_batch16_recovery_capacity"
        ),
    ):
        _expect_code(
            lambda: controller._r8u_r3_validate_r2_history(
                SimpleNamespace(plan={})
            ),
            "CANDIDATE_STAGE_EVENT_COMMIT_BINDING_INVALID",
        )

    history = {
        "r2_recovery_capacity_receipt_sha256": SHA,
        "r2_recovery_authority_sha256": SHA,
        "r2_recovery_submission_receipt_sha256": SHA,
        "failed_partial_seal_sha256": SHA,
    }
    _expect_code(
        lambda: controller._r8u_r3_validate_candidate_seal_static(
            {}, implementation_commit=COMMIT, history=history
        ),
        "CANDIDATE_FAILURE_UNRESOLVED",
    )


def test_candidate_repair_is_one_direct_child_and_binds_seven_epochs() -> None:
    publication = "ce3326a23f149dd864c5aa534225b959d7b5abbe"
    assert (
        controller.R8U_PUBLICATION_RESUME_REPAIR_IMPLEMENTATION_COMMIT
        == publication
    )
    assert controller.R8U_R2_COMPLETED_EXTRACTION_JOB_ID == "7354951"
    historical = controller._r8u_r2_historical_implementation_authority_epochs()
    assert historical["r8u_scheduler_log_repair_commit"] == (
        "4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff"
    )
    epochs = controller._r8u_r3_implementation_authority_epochs(COMMIT)
    assert len(epochs) == 7
    assert set(epochs) == controller.R8U_R3_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    assert epochs["r8u_publication_resume_repair_commit"] == publication
    assert epochs["r8u_candidate_authority_repair_commit"] == COMMIT

    fixed = (
        controller.ORIGINAL_SCIENTIFIC_COMMIT,
        controller.R8U_STARTING_IMPLEMENTATION_COMMIT,
        controller.R8U_BASE_IMPLEMENTATION_COMMIT,
        controller.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        controller.R8U_SCHEDULER_LOG_REPAIR_IMPLEMENTATION_COMMIT,
        publication,
    )
    parent_by_commit = {
        child: parent for child, parent in zip(fixed[1:], fixed[:-1])
    }
    parent_by_commit[COMMIT] = publication

    def git(*arguments: str) -> str:
        if arguments[:3] == ("rev-list", "--parents", "-n"):
            child = arguments[4]
            return f"{child} {parent_by_commit[child]}"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            return "1" if arguments[2] == f"{publication}..{COMMIT}" else "6"
        raise AssertionError(f"unexpected git query: {arguments}")

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r3_implementation_commit() == COMMIT

    def nonchild_git(*arguments: str) -> str:
        value = git(*arguments)
        if arguments[:2] == ("rev-list", "--count") and arguments[2] == (
            f"{publication}..{COMMIT}"
        ):
            return "2"
        return value

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(controller.sequential, "_git", side_effect=nonchild_git),
    ):
        _expect_code(
            controller._current_r8u_r3_implementation_commit,
            "R8U_R3_IMPLEMENTATION_ANCESTRY_INVALID",
        )


def _publication_fixture(root: Path):
    source_parent = root / "source_parent"
    target_parent = root / "target_parent"
    source = source_parent / "dicom_extraction"
    target = target_parent / "dicom_extraction"
    source.mkdir(parents=True)
    target_parent.mkdir()
    seal_path = root / "candidate.json"
    probe_path = root / "probe.json"
    publication_path = root / "publication.json"
    for path in (seal_path, probe_path):
        path.write_text("{}\n", encoding="utf-8")
        path.chmod(0o600)
    run = SimpleNamespace()
    candidate = {
        "candidate_relative_file_projection_sha256": SHA,
        "candidate_total_bytes": 99,
        "source_parent_identity_sha256": SHA,
        "target_parent_identity_sha256": SHA,
        "source_mount_identity_sha256": SHA,
        "target_mount_identity_sha256": SHA,
    }
    probe = {
        "primary_result": "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "primary_errno": "EINVAL",
    }
    return source_parent, target_parent, source, target, seal_path, probe_path, publication_path, run, candidate, probe


@contextmanager
def _patched_publication(values, *, mount_side_effect=None):
    (source_parent, _target_parent, _source, target, seal_path, probe_path,
     publication_path, _run, candidate, _probe) = values
    mounts = mount_side_effect or [((1,), SHA), ((1,), SHA)]
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(
            controller, "R8U_FRESH_EXTRACTION_BATCH_ROOT", source_parent
        ))
        stack.enter_context(mock.patch.object(
            controller, "R8U_R3_CANDIDATE_SEAL_PATH", seal_path
        ))
        stack.enter_context(mock.patch.object(
            controller, "R8U_R3_PROBE_PATH", probe_path
        ))
        stack.enter_context(mock.patch.object(
            controller, "R8U_R3_PUBLICATION_PATH", publication_path
        ))
        stack.enter_context(mock.patch.object(
            controller.sequential, "_batch_paths",
            return_value={"extraction": target},
        ))
        stack.enter_context(mock.patch.object(
            controller, "_r8u_r3_mount_authority", side_effect=mounts
        ))
        stack.enter_context(mock.patch.object(
            controller, "_r8u_r3_identity_sha256", return_value=SHA
        ))
        stack.enter_context(mock.patch.object(
            controller, "_r8u_r3_validate_r2_history", return_value={}
        ))
        stack.enter_context(mock.patch.object(
            controller, "validate_r8u_r3_extraction_candidate_seal",
            return_value=candidate,
        ))
        stack.enter_context(mock.patch.object(
            controller, "_r8u_r3_validate_publication_claim", return_value={}
        ))
        stack.enter_context(mock.patch.object(
            controller, "_r8u_r3_target_projection",
            return_value={
                "candidate_relative_file_projection_sha256": SHA
            },
        ))
        yield


def test_claim_protected_fallback_renames_once_and_accepts_nfs_error_success() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        (source_parent, target_parent, source, target, seal_path, probe_path,
         publication_path, run, candidate, probe) = values
        calls = 0

        def ambiguous_success(left: Path, right: Path) -> None:
            nonlocal calls
            calls += 1
            os.rename(left, right)
            raise OSError(errno.EIO, "synthetic NFS return")

        with _patched_publication(values):
            receipt = controller._r8u_r3_publish_candidate(
                run=run,
                implementation_commit=COMMIT,
                candidate_seal=candidate,
                probe=probe,
                publication_claim_sha256=SHA,
                fallback_invoker=ambiguous_success,
            )
        assert calls == 1
        assert receipt["fallback_used"] is True
        assert receipt["publication_ruling"] == (
            "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
        )
        assert receipt["real_rename_errno"] == "EIO"
        assert receipt["real_rename_errno_number"] == errno.EIO
        assert receipt["real_rename_errno_classification"] == "RENAME_ERROR_IO"
        assert not source.exists() and target.is_dir()


def test_fallback_blocks_both_present_and_never_calls_replace() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        (source_parent, _target_parent, _source, target, seal_path, probe_path,
         publication_path, run, candidate, probe) = values

        def both_present(_left: Path, right: Path) -> None:
            right.mkdir()
            raise OSError(errno.EIO, "synthetic")

        with (
            _patched_publication(values),
            mock.patch.object(
                os, "replace", side_effect=AssertionError("replace reached")
            ),
        ):
            _expect_code(
                lambda: controller._r8u_r3_publish_candidate(
                    run=run, implementation_commit=COMMIT,
                    candidate_seal=candidate, probe=probe,
                    publication_claim_sha256=SHA,
                    fallback_invoker=both_present,
                ),
                "R8U_PUBLICATION_AMBIGUOUS_STATE",
            )


def test_publication_errno_and_poststate_matrix_fails_closed() -> None:
    for collision_errno in (errno.EEXIST, errno.ENOTEMPTY):
        with tempfile.TemporaryDirectory() as temporary:
            values = _publication_fixture(Path(temporary).resolve())
            run, candidate, probe = values[-3:]

            def collision(_left: Path, _right: Path) -> None:
                raise OSError(collision_errno, "synthetic collision")

            with _patched_publication(values):
                _expect_code(
                    lambda: controller._r8u_r3_publish_candidate(
                        run=run, implementation_commit=COMMIT,
                        candidate_seal=candidate, probe=probe,
                        publication_claim_sha256=SHA,
                        fallback_invoker=collision,
                    ),
                    "R8U_PUBLICATION_TARGET_ALREADY_EXISTS",
                )

    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        source, run, candidate, probe = values[2], values[-3], values[-2], values[-1]

        def clean_failure(_left: Path, _right: Path) -> None:
            raise OSError(errno.EIO, "synthetic clean failure")

        with _patched_publication(values):
            _expect_code(
                lambda: controller._r8u_r3_publish_candidate(
                    run=run, implementation_commit=COMMIT,
                    candidate_seal=candidate, probe=probe,
                    publication_claim_sha256=SHA,
                    fallback_invoker=clean_failure,
                ),
                "R8U_PUBLICATION_RENAME_FAILED",
            )
        assert source.is_dir()

    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        source, run, candidate, probe = values[2], values[-3], values[-2], values[-1]

        def both_absent(left: Path, _right: Path) -> None:
            left.rmdir()
            raise OSError(errno.EIO, "synthetic missing poststate")

        with _patched_publication(values):
            _expect_code(
                lambda: controller._r8u_r3_publish_candidate(
                    run=run, implementation_commit=COMMIT,
                    candidate_seal=candidate, probe=probe,
                    publication_claim_sha256=SHA,
                    fallback_invoker=both_absent,
                ),
                "R8U_PUBLICATION_AMBIGUOUS_STATE",
            )
        assert not source.exists()


def test_publication_blocks_preexisting_target_and_cross_mount() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        target, run, candidate, probe = values[3], values[-3], values[-2], values[-1]
        target.mkdir()
        with _patched_publication(values):
            _expect_code(
                lambda: controller._r8u_r3_publish_candidate(
                    run=run, implementation_commit=COMMIT,
                    candidate_seal=candidate, probe=probe,
                    publication_claim_sha256=SHA,
                ),
                "R8U_PUBLICATION_TARGET_ALREADY_EXISTS",
            )

    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        run, candidate, probe = values[-3:]
        rename_calls = 0

        def forbidden(_left: Path, _right: Path) -> None:
            nonlocal rename_calls
            rename_calls += 1

        with _patched_publication(
            values, mount_side_effect=[((1,), SHA), ((2,), SHA)]
        ):
            _expect_code(
                lambda: controller._r8u_r3_publish_candidate(
                    run=run, implementation_commit=COMMIT,
                    candidate_seal=candidate, probe=probe,
                    publication_claim_sha256=SHA,
                    fallback_invoker=forbidden,
                ),
                "R8U_PUBLICATION_CROSS_MOUNT",
            )
        assert rename_calls == 0


def _run_blocked_probe_case(
    invoker, expected_code: str, *, poststate_scandir_error: bool = False
) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source_parent = root / "source_parent"
        target_parent = root / "target_parent"
        r3_root = root / "r3"
        source_parent.mkdir(mode=0o700)
        target_parent.mkdir(mode=0o700)
        r3_root.mkdir(mode=0o700)
        probe_path = r3_root / "probe.json"
        probe_work = r3_root / "primitive_probe"
        target = target_parent / "dicom_extraction"
        run = SimpleNamespace()
        with (
            mock.patch.object(
                controller, "R8U_FRESH_EXTRACTION_BATCH_ROOT", source_parent
            ),
            mock.patch.object(controller, "R8U_R3_ROOT", r3_root),
            mock.patch.object(controller, "R8U_R3_PROBE_PATH", probe_path),
            mock.patch.object(controller, "R8U_R3_PROBE_WORK_ROOT", probe_work),
            mock.patch.object(
                controller.sequential, "_batch_paths",
                return_value={"extraction": target},
            ),
            mock.patch.object(
                controller, "_r8u_r3_mount_authority",
                side_effect=[((1,), SHA), ((1,), SHA), ((1,), SHA)],
            ),
            mock.patch.object(
                controller, "_r8u_r3_identity_sha256", return_value=SHA
            ),
        ):
            def invoke_probe() -> None:
                _expect_code(
                    lambda: controller._r8u_r3_primitive_probe(
                        run=run, implementation_commit=COMMIT,
                        candidate_seal={"target_absent": True}, invoker=invoker,
                    ),
                    expected_code,
                )

            if poststate_scandir_error:
                real_scandir = os.scandir

                def inspect(path):
                    if Path(path) == probe_work / "target":
                        raise OSError(errno.EIO, "synthetic NFS metadata RPC")
                    return real_scandir(path)

                with mock.patch.object(controller.os, "scandir", side_effect=inspect):
                    invoke_probe()
            else:
                invoke_probe()
        value = json.loads(probe_path.read_text(encoding="utf-8"))
        assert value["status"] == "BLOCKED_PUBLICATION_PRIMITIVE_PROBE"
        return value


def test_blocked_probe_always_persists_exact_diagnostic() -> None:
    cases = (
        (lambda _left, _right: errno.EXDEV,
         "R8U_PUBLICATION_CROSS_MOUNT", "RENAME_CROSS_MOUNT_EXDEV", "EXDEV"),
        (lambda _left, _right: errno.EACCES,
         "R8U_PUBLICATION_PERMISSION_DENIED", "RENAME_PERMISSION_FAILURE", "EACCES"),
        (lambda _left, _right: errno.EIO,
         "R8U_PUBLICATION_RENAME_FAILED", "OTHER_EXACT_ERRNO_CLASS", "EIO"),
        (lambda _left, _right: errno.EEXIST,
         "R8U_PUBLICATION_TARGET_ALREADY_EXISTS", "OTHER_EXACT_ERRNO_CLASS", "EEXIST"),
        (lambda _left, _right: errno.ENOTEMPTY,
         "R8U_PUBLICATION_TARGET_ALREADY_EXISTS", "OTHER_EXACT_ERRNO_CLASS", "ENOTEMPTY"),
    )
    for invoker, code, classification, errno_name in cases:
        value = _run_blocked_probe_case(invoker, code)
        assert value["primary_result"] == classification
        assert value["primary_errno"] == errno_name
        assert value["primary_errno_number"] >= 1
        assert value["probe_cleanup_passed"] is True
        assert value["probe_directories_created"] == 2
        assert value["probe_directories_removed"] == 2

    def ambiguous(left: Path, right: Path) -> int:
        os.rename(left, right)
        return errno.EIO

    value = _run_blocked_probe_case(
        ambiguous, "R8U_PUBLICATION_AMBIGUOUS_STATE"
    )
    assert value["primary_result"] == "RENAME_AMBIGUOUS_SERVER_RESULT"

    def cleanup_blocker(left: Path, right: Path) -> None:
        os.rename(left, right)
        blocker = right / "blocker"
        blocker.write_bytes(b"x")

    value = _run_blocked_probe_case(
        cleanup_blocker, "R8U_R3_PROBE_CLEANUP_FAILED"
    )
    assert value["probe_cleanup_passed"] is False

    unknown_number = 4095
    value = _run_blocked_probe_case(
        lambda _left, _right: unknown_number,
        "R8U_PUBLICATION_RENAME_FAILED",
    )
    assert value["primary_result"] == "OTHER_EXACT_ERRNO_CLASS"
    assert value["primary_errno"] == "UNKNOWN"
    assert value["primary_errno_number"] == unknown_number


def test_probe_poststate_rpc_error_still_persists_ambiguous_receipt() -> None:
    def successful_rename(left: Path, right: Path) -> None:
        os.rename(left, right)

    value = _run_blocked_probe_case(
        successful_rename,
        "R8U_PUBLICATION_AMBIGUOUS_STATE",
        poststate_scandir_error=True,
    )
    assert value["primary_result"] == "RENAME_AMBIGUOUS_SERVER_RESULT"
    assert value["primary_returned_success"] is True
    assert value["primary_errno_number"] == 0
    assert value["primary_errno"] == "NONE"
    assert value["probe_cleanup_passed"] is True


def test_cross_mount_precondition_persists_probe_before_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source_parent = root / "source_parent"
        target_parent = root / "target_parent"
        r3_root = root / "r3"
        for path in (source_parent, target_parent, r3_root):
            path.mkdir(mode=0o700)
        probe_path = r3_root / "probe.json"
        with (
            mock.patch.object(
                controller, "R8U_FRESH_EXTRACTION_BATCH_ROOT", source_parent
            ),
            mock.patch.object(controller, "R8U_R3_ROOT", r3_root),
            mock.patch.object(controller, "R8U_R3_PROBE_PATH", probe_path),
            mock.patch.object(
                controller, "R8U_R3_PROBE_WORK_ROOT", r3_root / "work"
            ),
            mock.patch.object(
                controller.sequential, "_batch_paths",
                return_value={"extraction": target_parent / "dicom_extraction"},
            ),
            mock.patch.object(
                controller, "_r8u_r3_mount_authority",
                side_effect=[
                    ((1,), "1" * 64), ((2,), "2" * 64),
                    ((1,), "1" * 64),
                ],
            ),
            mock.patch.object(
                controller, "_r8u_r3_identity_sha256", return_value=SHA
            ),
        ):
            _expect_code(
                lambda: controller._r8u_r3_primitive_probe(
                    run=SimpleNamespace(), implementation_commit=COMMIT,
                    candidate_seal={"target_absent": True},
                ),
                "R8U_PUBLICATION_CROSS_MOUNT",
            )
        value = json.loads(probe_path.read_text(encoding="utf-8"))
        assert value["status"] == "BLOCKED_PUBLICATION_PRIMITIVE_PROBE"
        assert value["primary_result"] == "RENAME_CROSS_MOUNT_EXDEV"
        assert value["real_parents_same_mounted_filesystem"] is False
        assert value["probe_mount_matches_real_parents"] is False
        assert value["probe_directories_created"] == 0
        assert value["probe_directories_removed"] == 0


def test_claim_creation_is_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "batch"
        parent.mkdir(mode=0o700)
        claim_root = parent / ".claim"
        claim_path = claim_root / "claim.json"
        with (
            mock.patch.object(controller, "R8U_R3_PUBLICATION_CLAIM_ROOT", claim_root),
            mock.patch.object(controller, "R8U_R3_PUBLICATION_CLAIM_PATH", claim_path),
        ):
            controller._r8u_r3_create_publication_claim({"status": "PASS"})
            _expect_code(
                lambda: controller._r8u_r3_create_publication_claim(
                    {"status": "PASS"}
                ),
                "R8U_PUBLICATION_CLAIM_COLLISION",
            )


def test_parser_dispatch_and_runner_are_fixed_to_r3_resume_and_continuation() -> None:
    options = {
        option
        for action in controller._r8u_r3_parser()._actions
        for option in action.option_strings
    }
    assert {
        "--submit-r8u-r3-batch16-publication-resume",
        "--run-r8u-r3-batch16-publication-resume",
        "--submit-r8u-r3-continuation-17-19",
        "--run-r8u-r3-continuation-17-19-array-task",
        "--run-r8u-r3-continuation-finalizer",
    } == options - {"-h", "--help"}
    with mock.patch.object(
        controller,
        "submit_r8u_r3_batch16_publication_resume",
        return_value={
            "resume_job_id": "8123456",
            "capacity_status": controller.R8U_R3_CAPACITY_STATUS,
            "initial_state": "qw",
        },
    ):
        output = io.StringIO()
        with redirect_stdout(output):
            assert controller.guarded_main(
                ["--submit-r8u-r3-batch16-publication-resume"]
            ) == 0
        assert "R8U_R3_RESUME_JOB_ID=8123456" in output.getvalue()
    runner = (
        ROOT / "scripts" / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
    )
    source = runner.read_text(encoding="utf-8")
    assert "lvef_c3_r8u_r3_res_*)" in source
    assert "MODE=--run-r8u-r3-batch16-publication-resume" in source
    assert "lvef_c3_r8u_r3_seq_*)" in source
    assert "MODE=--run-r8u-r3-continuation-17-19-array-task" in source
    assert "lvef_c3_r8u_r3_fin_*)" in source
    assert "MODE=--run-r8u-r3-continuation-finalizer" in source
    subprocess.run(["/bin/bash", "-n", str(runner)], check=True)


def test_submit_runs_one_qsub_then_one_qstat_then_writes_submission() -> None:
    events: list[str] = []
    candidate = {"candidate_total_bytes": 99}
    candidate_sha = controller._sha256_bytes(
        controller._canonical_bytes(candidate)
    )
    process_projection = {
        "status": "PASS_ZERO_COMPETING_R8U_R3_PROCESSES",
        "matching_processes": 0,
        "process_snapshot_count": 1,
        "ps_argv_sha256": SHA,
        "ps_stdout_sha256": SHA,
    }
    qstat_projection = {
        "status": "PASS_EXACT_ONE_R8U_R3_RESUME_JOB_ZERO_COMPETITORS",
        "resume_job_id": "8123456",
        "resume_job_name": controller._r8u_r3_resume_job_name(COMMIT),
        "state": "qw",
        "category": "pending",
        "target_matches": 1,
        "competing_matching_jobs": 0,
        "qstat_snapshot_count": 1,
    }
    qstat_projection["qstat_projection_sha256"] = (
        controller.core.canonical_json_sha256(qstat_projection)
    )

    def write(path: Path, _value) -> str:
        if path == controller.R8U_R3_CANDIDATE_SEAL_PATH:
            events.append("candidate_seal")
        elif path == controller.R8U_R3_SUBMISSION_PATH:
            events.append("write_submission")
        else:
            events.append("write_control")
        return candidate_sha if path == controller.R8U_R3_CANDIDATE_SEAL_PATH else SHA

    def qsub(*_args, **_kwargs) -> str:
        events.append("qsub")
        return "8123456"

    def qstat(**_kwargs):
        events.append("qstat")
        return qstat_projection

    capacity_value = {"status": controller.R8U_R3_CAPACITY_STATUS}

    def capacity_probe(*_args, **_kwargs):
        events.append("capacity_probe")
        return capacity_value

    with ExitStack() as stack:
        patches = (
            mock.patch.object(controller.scheduler, "validate_scheduler_tools"),
            mock.patch.object(
                controller.scheduler, "build_qsub_environment",
                return_value=({"USER": "tester"}, {}),
            ),
            mock.patch.object(
                controller.scheduler, "qsub_environment_sha256", return_value=SHA
            ),
            mock.patch.object(
                controller, "_current_r8u_r3_implementation_commit",
                return_value=COMMIT,
            ),
            mock.patch.object(
                controller, "_r8u_r3_process_projection",
                return_value=process_projection,
            ),
            mock.patch.object(
                controller, "_load_fixed_original_run",
                return_value=SimpleNamespace(plan={}),
            ),
            mock.patch.object(controller, "_validate_original_controls"),
            mock.patch.object(
                controller, "_r8u_validate_frozen_prefix", return_value=()
            ),
            mock.patch.object(
                controller, "_r8u_historical_r8r_chain_authority",
                return_value={},
            ),
            mock.patch.object(
                controller, "_r8u_r3_validate_r2_history", return_value={}
            ),
            mock.patch.object(
                controller, "_r8u_r3_require_submit_outputs_absent"
            ),
            mock.patch.object(
                controller, "_r8u_r3_candidate_projection", return_value=object()
            ),
            mock.patch.object(
                controller, "_r8u_r3_candidate_seal", return_value=candidate
            ),
            mock.patch.object(
                controller.capacity,
                "probe_fixed_r8u_r3_batch16_publication_resume_capacity",
                side_effect=capacity_probe,
            ),
            mock.patch.object(
                controller.capacity,
                "validate_fixed_r8u_r3_batch16_publication_resume_capacity",
            ),
            mock.patch.object(
                controller, "_create_private_directory_no_clobber"
            ),
            mock.patch.object(
                controller, "_write_private_json", side_effect=write
            ),
            mock.patch.object(
                controller, "_r8u_r3_resume_authority",
                return_value={"authority": 1},
            ),
            mock.patch.object(
                controller, "_r8u_r3_resume_submission_receipt",
                return_value={"submission": 1},
            ),
            mock.patch.object(
                controller.scheduler, "_capture_qsub", side_effect=qsub
            ),
            mock.patch.object(
                controller, "_validate_r8u_r3_initial_qstat", side_effect=qstat
            ),
        )
        for patcher in patches:
            stack.enter_context(patcher)
        sleep = stack.enter_context(mock.patch.object(controller.time, "sleep"))
        value = controller.submit_r8u_r3_batch16_publication_resume()
    assert value["resume_job_id"] == "8123456"
    assert events.count("candidate_seal") == 1
    assert events.count("capacity_probe") == 1
    assert events.count("qsub") == 1
    assert events.count("qstat") == 1
    assert events.index("candidate_seal") < events.index("capacity_probe")
    assert events.index("capacity_probe") < events.index("qsub")
    assert events.index("qsub") < events.index("qstat")
    assert events.index("qstat") < events.index("write_submission")
    sleep.assert_not_called()


def test_sole_qstat_rejects_competing_production_stage_job() -> None:
    xml = f"""<job_info><queue_info>
      <job_list state="pending"><JB_job_number>8123456</JB_job_number>
        <JB_name>{controller._r8u_r3_resume_job_name(COMMIT)}</JB_name>
        <state>qw</state></job_list>
      <job_list state="pending"><JB_job_number>8123457</JB_job_number>
        <JB_name>c3_ext_aaaaaaaaaaaa</JB_name><state>qw</state></job_list>
      </queue_info><job_info /></job_info>""".encode()
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0, xml, b"")

    _expect_code(
        lambda: controller._validate_r8u_r3_initial_qstat(
            environment={"USER": "tester"}, resume_job_id="8123456",
            implementation_commit=COMMIT, runner=runner,
        ),
        "R8U_R3_INITIAL_QSTAT_TOPOLOGY_INVALID",
    )
    assert calls == 1


def test_worker_has_no_extraction_path_and_rechecks_before_retirement() -> None:
    source = inspect.getsource(controller.run_r8u_r3_batch16_publication_resume)
    assert "dependency.dicom(" not in source
    assert source.count("_r8u_r3_target_projection(run, candidate)") == 2
    assert "receipt_root=R8U_R3_EXTRACTION_TRANSITION_ROOT" in source
    assert 'paths["extraction"] / "transition_receipts"' not in source
    assert "preservation.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY" in source
    assert "retirement.R8U_R3_FIXED_BATCH16_NO_SCIENTIFIC_BODY" in source
    assert source.index("validate_r8u_r3_publication_receipt(run)") < source.index(
        "dependency.echoprime("
    )


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
    print(f"PASS: {len(tests)} R8U-R3 controller tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
